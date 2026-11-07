import os

# Threads je Prozess begrenzen: unter pytest-xdist laufen viele Worker gleichzeitig, jeder mit eigener Pipeline.
# Ohne Grenze rechnet jeder Worker mit allen Kernen (BLAS, Warp-Threads) und alles wird eher langsamer.
# Muss vor dem ersten `import numpy` stehen (conftest wird zuerst geladen).
if os.environ.get("PYTEST_XDIST_WORKER"):
    for _k in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
        os.environ.setdefault(_k, "2")
    os.environ.setdefault("ASTROFLOW_THREADS", "2")

import subprocess
import tempfile
import threading
import time
from pathlib import Path

import pytest

# Nie ein echtes GraXpert/StarNet/GPU/Netz in Tests: schon beim Import setzen (vor allen Modul-/Sitzungs-Fixtures,
# in jedem xdist-Worker, und von Unterprozessen geerbt). Einzelne Tests überschreiben per monkeypatch.
os.environ.update({"ASTROFLOW_GAIA": "0", "ASTROFLOW_GRAXPERT": "0", "ASTROFLOW_GPU": "0", "ASTROFLOW_SAGE_OFFLINE": "1",
                   "ASTROFLOW_HORIZONS": "0", "ASTROFLOW_SKY": "0", "ASTROFLOW_UPDATE_CHECK": "0",
                   "ASTROFLOW_DIAG": "0"})
os.environ.pop("ASTROFLOW_LAUNCHER", None)           # Einrichtungsassistent nur in eigenen Tests (monkeypatch)


def pytest_configure(config):
    """Schutz: Programme namens graxpert*/starnet* dürfen nur aus dem Temp-Ordner (Attrappen der Tests) gestartet werden.
    Ein echtes GraXpert (GB an RAM je Prozess) hat am 04.11.2026 bei paralleler Ausführung den Rechner lahmgelegt."""
    real_init = subprocess.Popen.__init__
    tmp = Path(tempfile.gettempdir()).resolve()

    def guarded(self, args, *a, **kw):
        first = args if isinstance(args, (str, bytes, os.PathLike)) else (args[0] if args else "")
        exe = Path(os.fsdecode(first))
        if any(w in exe.name.lower() for w in ("graxpert", "starnet")):
            try:
                inside = tmp in exe.resolve().parents
            except OSError:
                inside = False
            if not inside:
                raise RuntimeError(f"Test startet ein echtes Programm: {exe} (Attrappe im tmp_path verwenden)")
        real_init(self, args, *a, **kw)

    subprocess.Popen.__init__ = guarded
    _block_network()


LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1", "[::1]", "testserver"}


def _block_network():
    """Schutz: Tests gehen nie ins echte Netz (httpx). Nur localhost/Testserver; alles andere scheitert sofort wie ein
    Verbindungsfehler. Anlass 07.11.2026: ein Sage-Hintergrundthread aus einem früheren Test fragte arXiv ab und
    landete in der Zählung von test_update_hinweis. Tests mit Attrappen (monkeypatch auf httpx.get o. ä.) sind nicht betroffen."""
    import httpx

    def check(request):
        host = request.url.host or ""
        if host not in LOCAL_HOSTS:
            raise httpx.ConnectError(f"Netz in Tests gesperrt: {request.url.scheme}://{host}", request=request)

    real, real_async = httpx.HTTPTransport.handle_request, httpx.AsyncHTTPTransport.handle_async_request

    def handle(self, request):
        check(request)
        return real(self, request)

    async def handle_async(self, request):
        check(request)
        return await real_async(self, request)

    httpx.HTTPTransport.handle_request = handle
    httpx.AsyncHTTPTransport.handle_async_request = handle_async

UI_FILES = ("test_ui_layout.py", "test_skytrace_3d_ui.py", "test_skytrace_video_ui.py", "test_skytrace_erde_ui.py")


def pytest_collection_modifyitems(items):
    """Marker automatisch: `ui` = Tests mit echtem Browser (Playwright) – schneller Teilsatz: `-m "not ui"`."""
    for it in items:
        if it.path.name in UI_FILES:
            it.add_marker(pytest.mark.ui)


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    """Tests laufen ohne Netz: keine Gaia-Abfragen (die Farbkalibrierung fällt dann auf das Sternmittel zurück)."""
    monkeypatch.setenv("ASTROFLOW_GAIA", "0")
    monkeypatch.setenv("ASTROFLOW_GRAXPERT", "0")             # nie ein installiertes GraXpert aufrufen
    monkeypatch.setenv("ASTROFLOW_GPU", "0")                  # Stacking auf der CPU (GPU-Vergleich: test_gpu.py)
    monkeypatch.setenv("ASTROFLOW_SAGE_OFFLINE", "1")          # Sage: keine SIMBAD-/arXiv-Abfragen
    # Standard: nur das Stacking-Modul laden; Modultests schalten weitere gezielt dazu
    monkeypatch.setenv("ASTROFLOW_MODULES", "stacking")


@pytest.fixture(scope="session")
def server(tmp_path_factory):
    """Testserver für die Oberflächentests (alle Module, Demodaten) – je Prozess/Worker einmal, auf freiem Port."""
    pytest.importorskip("playwright.sync_api")
    import uvicorn
    from astroflow.core.config import load_settings
    from astroflow.core.modules import AVAILABLE
    from astroflow.core.store import SettingsStore
    from astroflow.modules.stacking import demo
    from astroflow.modules.stacking.db import DB
    from astroflow.modules.stacking.pipeline import Pipeline
    from astroflow.modules.stacking.reviewer import ReviewerConfig
    from astroflow.server import create_app
    from test_ui_layout import LONG, _free_port, _sky_seed, _skytrace_run

    root = tmp_path_factory.mktemp("ui")
    mp = pytest.MonkeyPatch()
    try:
        for k, v in {"ASTROFLOW_MODULES": ",".join(AVAILABLE), "ASTROFLOW_AUTO": "0", "ASTROFLOW_GAIA": "0",
                     "ASTROFLOW_GPU": "0", "ASTROFLOW_GRAXPERT": "0", "ASTROFLOW_SAGE_OFFLINE": "1"}.items():
            mp.setenv(k, v)
        mp.delenv("SKYTRACE_HOME", raising=False)          # SkyTrace setzt sie beim Laden prozessweit – danach wieder entfernen
        s = load_settings(root / "data")
        SettingsStore(s.data_dir / "settings.json").update("skytrace", {"home": str(root / "skytrace")})
        SettingsStore(s.data_dir / "settings.json").update("sites", {       # erfundene Orte, langer Name für die Layoutprüfung
            "list": [{"id": "a", "name": LONG, "lat": 47.5, "lon": 10.25, "alt_m": 400, "accuracy_m": 30, "note": LONG},
                     {"id": "b", "name": "Beispielberg", "lat": 45.0, "lon": 6.0, "alt_m": 0, "accuracy_m": 2000, "note": ""},
                     {"id": "c", "name": "Zeltwiese", "lat": 46.0, "lon": 7.0, "alt_m": 0, "accuracy_m": 500, "note": ""}],     # c gilt nirgends
            "default": "a", "periods": [{"id": "p1", "site_id": "b", "from": "2022-06-01", "to": "2022-06-20"},
                                        {"id": "p2", "site_id": "b", "from": "2026-01-01", "to": "2026-01-01"}]})
        _skytrace_run(root / "skytrace" / "Ergebnisse" / "2026-10-28_10-00-00-000001")
        demo.generate(s.input_dir / "night", n_lights=16, n_darks=0, n_flats=0)
        db = DB(s.db_path)
        pl = Pipeline(s, db, ReviewerConfig())
        pl.scan()
        pid = db.q("SELECT id FROM projects")[0]["id"]
        pl.process(pid)
        for _ in range(2):                                   # 3 Versionen -> Speicherbelegung bietet „Ältere Versionen“ an
            pl.process(pid, {"mode": "develop"})
        db.x("UPDATE projects SET name=? WHERE id=?", (LONG, pid))
        _sky_seed(db, pid)
        port = _free_port()
        srv = uvicorn.Server(uvicorn.Config(create_app(s), host="127.0.0.1", port=port, log_level="warning"))
        th = threading.Thread(target=srv.run, daemon=True)
        th.start()
        for _ in range(200):
            if srv.started:
                break
            time.sleep(0.05)
        yield f"http://127.0.0.1:{port}", pid
        srv.should_exit = True
        th.join(10)
    finally:
        mp.undo()


@pytest.fixture(scope="session")
def browser():
    """Ein Chromium je Prozess/Worker."""
    sync_api = pytest.importorskip("playwright.sync_api")
    with sync_api.sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception as e:                               # Paket da, Browser nicht installiert
            try:
                b = p.chromium.launch(channel='msedge')      # vorhandenes Windows-Edge geht ebenfalls
            except Exception:
                pytest.skip(f"Chromium/Edge fehlt: {e}")
        yield b
        b.close()
