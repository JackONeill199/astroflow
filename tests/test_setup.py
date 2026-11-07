"""Einfache Installation: Starter (core/launcher.py), Einrichtungsassistent und Update-Hinweis (core/setup.py), Paketbeschreibung."""
import io
import json
import re
import shutil
import sys
import time
import tomllib
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from astroflow import __version__
from astroflow.core import components as comp, launcher, setup as setup_mod
from astroflow.core.config import load_settings
from astroflow.server import create_app

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def home(tmp_path, monkeypatch):
    """Portable Installation im Testordner (Konfiguration + Daten dort), ohne fremde Umgebungsvariablen."""
    for k in ("ASTROFLOW_DATA", "ASTROFLOW_PORT", "ASTROFLOW_LAUNCHER"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("ASTROFLOW_HOME", str(tmp_path / "app"))
    return tmp_path / "app"


def client(data: Path, launched: bool, monkeypatch) -> TestClient:
    if launched:
        monkeypatch.setenv("ASTROFLOW_LAUNCHER", "1")
    return TestClient(create_app(load_settings(data), only=("stacking",)))


def test_launcher_pfade(home, monkeypatch, tmp_path):
    assert launcher.config_path() == home / "config.json"
    assert launcher.default_data_dir() == home / "Daten"
    assert launcher.data_dir() == (home / "Daten").resolve()
    launcher.write_config({"data_dir": str(tmp_path / "woanders"), "port": 8799})
    assert launcher.data_dir() == (tmp_path / "woanders").resolve()
    assert launcher.port() == 8799
    monkeypatch.setenv("ASTROFLOW_DATA", str(tmp_path / "env"))          # bestehende Instanzen: Umgebung gewinnt
    assert launcher.data_dir() == (tmp_path / "env").resolve()
    assert launcher.data_dir(str(tmp_path / "arg")) == (tmp_path / "arg").resolve()
    assert launcher.host() == "127.0.0.1"
    launcher.write_config({"lan": True})
    assert launcher.host() == "0.0.0.0"


def test_standard_ohne_portabel(monkeypatch, tmp_path):
    monkeypatch.delenv("ASTROFLOW_HOME", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    assert launcher.default_data_dir() == tmp_path / "AstroFlow"          # nicht unter „Dokumente“ (OneDrive)


def test_cloud_ordner(monkeypatch, tmp_path):
    monkeypatch.setenv("OneDrive", str(tmp_path / "OneDrive - Firma"))
    assert launcher.cloud_folder(tmp_path / "OneDrive - Firma" / "Dokumente" / "AstroFlow") == "OneDrive"
    assert launcher.cloud_folder(tmp_path / "Dropbox" / "x") == "Dropbox"
    assert launcher.cloud_folder(tmp_path / "AstroFlow") is None


def test_cli_ohne_befehl_startet(monkeypatch):
    from astroflow import __main__ as cli
    seen = {}
    monkeypatch.setattr(launcher, "start", lambda *a: seen.setdefault("start", a) and 0)
    cli.main([])
    assert seen["start"] == (None, None, True, None)
    cli.main(["start", "--no-browser", "--port", "8799"])


def test_assistent_nur_bei_starter(home, tmp_path, monkeypatch):
    c = client(tmp_path / "data", False, monkeypatch)
    assert c.get("/api/app").json()["setup"] == {"pending": False, "launched": False}
    s = c.get("/api/setup").json()
    assert not s["pending"] and s["config"] is None
    assert c.post("/api/setup", json={"data_dir": str(tmp_path / "x")}).status_code == 400   # --data/ASTROFLOW_DATA bleibt fest


def test_assistent_ablauf(home, tmp_path, monkeypatch):
    c = client(home / "Daten", True, monkeypatch)
    assert c.get("/api/app").json()["setup"]["pending"] is True
    s = c.post("/api/setup", json={"telescope": "stellina"}).json()
    assert s["telescope"] == "stellina" and s["pending"]
    assert s["data"]["path"] == str((home / "Daten").resolve()) and s["data"]["free"] > 0
    assert any(d["free"] > 0 for d in s["drives"])
    s = c.post("/api/setup", json={"done": True}).json()
    assert not s["pending"] and not c.get("/api/app").json()["setup"]["pending"]


def test_datenordner_wechsel_startet_neu(home, tmp_path, monkeypatch):
    c = client(home / "Daten", True, monkeypatch)
    calls = []
    monkeypatch.setattr(setup_mod.Setup, "restart", lambda self, data=None: calls.append(data))
    neu = tmp_path / "Neu"
    c.post("/api/setup", json={"telescope": "vespera"})
    r = c.post("/api/setup", json={"data_dir": str(neu)}).json()
    assert r["restart"] and calls == [neu.resolve()]
    assert launcher.read_config()["data_dir"] == str(neu.resolve())
    # Einrichtung läuft im neuen Ordner weiter, Teleskopwahl bleibt
    assert json.loads((neu / "settings.json").read_text(encoding="utf-8"))["setup"] == {"telescope": "vespera", "done": False}
    assert c.post("/api/setup", json={"data_dir": "relativ/pfad"}).status_code == 400


def _wait_task(c, key, timeout=30):
    t0 = time.time()
    while time.time() - t0 < timeout:
        t = c.get("/api/setup").json()["tasks"].get(key) or {}
        if t.get("state") in ("done", "error"):
            return t
        time.sleep(0.1)
    raise TimeoutError(key)


def test_astap_herunterladen(home, tmp_path, monkeypatch):
    """Download-Attrappe statt SourceForge: Kommandozeile (= Kopie des Python-Interpreters, beantwortet -h), Katalog, D05."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "lad"))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "lad"))
    monkeypatch.setattr(setup_mod.platform, "system", lambda: "Windows")
    monkeypatch.setattr(setup_mod.platform, "machine", lambda: "AMD64")

    def zbytes(files):
        b = io.BytesIO()
        with zipfile.ZipFile(b, "w") as z:
            for n, d in files.items():
                z.writestr(n, d)
        return b.getvalue()
    exe = Path(sys.executable).read_bytes()
    served = {"astap_command-line_version_win64.zip": zbytes({"astap_cli.exe": exe}),
              "astapwin32.zip": zbytes({"astap.exe": b"x", "deep_sky.csv": b"kopf\n1,2,M_31\n", "acknowledgement of databases.txt": b"ok"}),
              "d05_star_database.zip": zbytes({"d05_0101.1476": b"sterne", "d05_0102.1476": b"sterne"})}
    urls = []

    def fake(url, prog, to=None, resume=False):
        urls.append(url)
        data = served[url.split("/")[-2]]
        prog(1.0)
        if to:
            Path(to).write_bytes(data)
            return None
        return data
    monkeypatch.setattr(comp, "download", fake)
    (tmp_path / "lad" / "astap").mkdir(parents=True)
    (tmp_path / "lad" / "astap" / "fremd.txt").write_text("vom Nutzer")          # gehört nicht AstroFlow
    c = client(home / "Daten", True, monkeypatch)
    assert c.post("/api/setup/astap", json={"db": "d07"}).status_code == 400
    assert c.post("/api/setup/astap", json={"db": "d05"}).status_code == 200
    t = _wait_task(c, "astap")
    assert t["state"] == "done", t
    dest = tmp_path / "lad" / "astap"
    assert (dest / "astap_cli.exe").is_file() and (dest / "deep_sky.csv").is_file() and (dest / "d05_0101.1476").is_file()
    assert not list(dest.glob(".*part"))
    assert all("sourceforge.net/projects/astap-program/" in u for u in urls)
    # zweiter Lauf: nichts mehr laden, vorhandene Dateien bleiben
    urls.clear()
    c.post("/api/setup/astap", json={"db": "d05"})
    assert "Bereits vorhanden" in _wait_task(c, "astap")["detail"] and not urls
    assert any(a["key"] == "setup:astap" for a in c.get("/api/activity").json())
    # Entfernen löscht nur, was AstroFlow geladen hat
    rec = comp.records()["astap"]
    assert {"astap_cli.exe", "deep_sky.csv", "d05_0101.1476"} <= set(rec["files"])
    assert c.post("/api/components/remove", json={"id": "astap"}).status_code == 200
    assert not (dest / "d05_0101.1476").exists() and (dest / "fremd.txt").is_file() and "astap" not in comp.records()


def test_komponenten_liste(home, tmp_path, monkeypatch):
    c = client(home / "Daten", True, monkeypatch)
    d = c.get("/api/components").json()
    ids = [i["id"] for i in d["items"]]
    assert ids == ["astap", "graxpert", "gpu", "skytrace", "starnet"]
    star = d["items"][-1]
    assert star["can_install"] is False and "all rights reserved" in star["license"]
    assert c.post("/api/components/install", json={"id": "starnet"}).status_code == 400
    assert c.post("/api/components/install", json={"id": "unsinn"}).status_code == 400
    gx = d["items"][1]
    assert gx["license"].startswith("GPL-3.0") and gx["source"] == comp.GRAXPERT_SOURCE
    assert all(i["state"] in ("installed", "missing", "partial", "unsupported", "error") for i in d["items"])


def test_installer_auftrag(home, tmp_path, monkeypatch):
    """Installer schreibt die Auswahl; AstroFlow arbeitet sie beim Start ab – Demo zuletzt, Fehlschläge bleiben vorgemerkt."""
    home.mkdir(parents=True, exist_ok=True)
    comp.order_path().write_text("\ufeffdemo\nskytrace\nastap:d50\ngraxpert\n", encoding="utf-8")
    c = client(home / "Daten", True, monkeypatch)
    setup = c.app.state.setup
    seen = []

    def fake_install(self, name, option=None, force=False):
        seen.append((name, option))
        self.tasks[name] = {"state": "error" if name == "graxpert" else "done", "detail": "x"}
    monkeypatch.setattr(setup_mod.Setup, "install_component", fake_install)
    setup.run_order()
    assert seen == [("astap", "d50"), ("graxpert", None), ("skytrace", None), ("demo", None)]
    assert not comp.order_path().exists()
    assert launcher.read_config()["components_pending"] == ["graxpert"]          # wird später erneut angeboten
    assert c.get("/api/components").json()["pending"] == ["graxpert"]


@pytest.mark.skipif(sys.platform != "win32", reason="GraXpert-Installation nur unter Windows")
def test_graxpert_auspacken(home, tmp_path, monkeypatch):
    """MSI-Attrappe: Prüfsumme wird geprüft, msiexec /a packt ins eigene Werkzeugverzeichnis, AstroFlow findet GraXpert dort."""
    import hashlib
    blob = b"msi-attrappe" * 1000
    monkeypatch.setattr(comp, "GRAXPERT_SHA256", hashlib.sha256(blob).hexdigest())

    def fake_dl(url, prog, to=None, resume=False):
        assert url == comp.GRAXPERT_MSI and resume
        Path(to).write_bytes(blob)
    calls = []

    def fake_run(args, **kw):
        calls.append(args)
        target = Path(next(a for a in args if str(a).startswith("TARGETDIR=")).split("=", 1)[1])
        (target / "lib").mkdir(parents=True)
        (target / "GraXpert.exe").write_bytes(b"exe")
        (target / "graxpert.msi").write_bytes(b"kopie")
        return type("R", (), {"returncode": 0})()
    monkeypatch.setattr(comp, "download", fake_dl)
    monkeypatch.setattr(comp.subprocess, "run", fake_run)
    msg = comp.install("graxpert")
    exe = comp.graxpert_dir() / "GraXpert.exe"
    assert exe.is_file() and not list(comp.graxpert_dir().glob("*.msi")) and "GPL-3.0" in msg
    assert calls[0][:2] == ["msiexec", "/a"] and "/qn" in calls[0]
    monkeypatch.delenv("ASTROFLOW_GRAXPERT")
    from astroflow.modules.stacking import graxpert
    assert graxpert.graxpert_path() == str(exe)
    # falsche Prüfsumme → verworfen
    comp.remove("graxpert")
    monkeypatch.setattr(comp, "GRAXPERT_SHA256", "0" * 64)
    with pytest.raises(RuntimeError, match="Prüfsumme"):
        comp.install("graxpert")
    assert not comp.graxpert_dir().exists()


def test_pip_haelt_serverpakete_fest(home):
    cons = comp._constraints().read_text(encoding="utf-8").splitlines()
    import numpy
    assert f"numpy=={numpy.__version__}" in cons
    reqs = comp.ENGINE_REQS.read_text(encoding="utf-8")
    assert "torch" in reqs and "ultralytics" in reqs and "pywin32" not in reqs          # plattformneutral


def test_update_hinweis(home, tmp_path, monkeypatch):
    import httpx
    c = client(tmp_path / "data", False, monkeypatch)
    setup = c.app.state.setup
    calls = []

    class R:
        status_code = 200
        def json(self):
            return {"tag_name": "v99.0.0", "html_url": "https://github.com/x/y/releases/tag/v99.0.0"}
    monkeypatch.setattr(httpx, "get", lambda url, **kw: calls.append(url) or R())
    info = setup.check_update(force=True)
    assert info["newer"] and info["latest"] == "99.0.0" and calls == [setup_mod.RELEASES_API]
    assert c.get("/api/app").json()["update"]["latest"] == "99.0.0"
    setup.check_update()                                 # innerhalb eines Tages keine zweite Anfrage
    assert len(calls) == 1
    c.post("/api/setup", json={"update_check": False})
    assert c.get("/api/app").json()["update"] is None
    setup.ctx.store.update("system", {"update": {"checked": 0}})
    setup.check_update()
    assert len(calls) == 1                               # abgeschaltet → keine Anfrage

    class E:
        status_code = 404                                # privates Repo
    c.post("/api/setup", json={"update_check": True})
    monkeypatch.setattr(httpx, "get", lambda url, **kw: E())
    assert setup.check_update(force=True).get("newer") is None


def test_version_vergleich():
    v = setup_mod._version_tuple
    assert v("0.33.0") < v("0.33.1") < v("0.34.0") < v("1.0.0")
    major, minor, _ = v(__version__)
    assert v(f"v{major}.{minor + 1}.0") > v(__version__) and not v(f"v{__version__}") > v(__version__)


def test_pyproject_passt_zu_requirements():
    proj = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    req = [re.split(r"\s+#", l)[0].strip() for l in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()]
    assert sorted(proj["dependencies"]) == sorted(r for r in req if r)
    assert proj["scripts"]["astroflow"] == "astroflow.__main__:main"
    assert proj["requires-python"] == ">=3.12"


def test_wheel_enthaelt_oberflaeche(tmp_path):
    """Paketdaten: Oberfläche, Modul-Skripte, Engine und Modell müssen im Wheel landen (sonst leere Seite nach pip install)."""
    import subprocess
    r = subprocess.run([sys.executable, "-m", "pip", "wheel", "--no-deps", "-q", "-w", str(tmp_path), str(ROOT)],
                       capture_output=True, text=True, timeout=300)
    if r.returncode:
        pytest.skip("Wheel-Bau nicht möglich: " + r.stderr[-200:])
    shutil.rmtree(ROOT / "build" / "lib", ignore_errors=True)
    shutil.rmtree(ROOT / "astroflow.egg-info", ignore_errors=True)
    names = zipfile.ZipFile(next(tmp_path.glob("astroflow-*.whl"))).namelist()
    for need in ("astroflow/static/index.html", "astroflow/static/setup.js", "astroflow/static/vendor/LICENSE-three.txt",
                 "astroflow/modules/stacking/static/stacking.js", "astroflow/modules/skytrace/engine/solving.py",
                 "astroflow/modules/skytrace/models/night-detector/LICENSE", "astroflow/modules/sage/static/sage.js"):
        assert need in names, need
    assert not any("__pycache__" in n for n in names)


def test_neustart_behaelt_datenordner(home, tmp_path, monkeypatch):
    """Gewöhnlicher Neustart (Heimnetz, „Neu starten“) bleibt im laufenden Ordner, auch wenn der per --data/ASTROFLOW_DATA kam."""
    started = []
    monkeypatch.setattr(launcher.subprocess, "Popen", lambda args, **kw: started.append(args))
    monkeypatch.setattr(setup_mod.signal, "raise_signal", lambda sig: None)
    monkeypatch.setattr(setup_mod.os, "_exit", lambda code: None)
    monkeypatch.setattr(setup_mod.time, "sleep", lambda s: None)
    eigen = tmp_path / "eigener-ordner"
    c = client(eigen, True, monkeypatch)
    setup = c.app.state.setup
    seen = []
    monkeypatch.setattr(setup_mod.threading, "Thread", lambda target, daemon=True, **kw: type("T", (), {"start": lambda self: (target(), seen.append(1))})())
    setup.restart()
    args = started[-1]
    assert args[args.index("--data") + 1] == str(eigen.resolve()) and seen
    neu = tmp_path / "neu"
    setup.restart(neu)
    args = started[-1]
    assert args[args.index("--data") + 1] == str(neu)


def test_infobereich_tooltip_und_meldung():
    from astroflow.core import tray
    assert tray.tooltip(None, []) == "AstroFlow – Server nicht erreichbar"
    assert tray.tooltip({"busy": 0}, []) == "AstroFlow – bereit"
    act = [{"key": "stacking:7", "module": "stacking", "title": "M31", "state": "running", "progress": 0.42}]
    assert tray.tooltip({"busy": 1}, act) == "AstroFlow – M31 42 %"
    assert len(tray.tooltip({"busy": 1}, [{**act[0], "title": "X" * 300}])) <= 120     # Windows: höchstens 127 Zeichen
    seen, done = tray.finished_pictures({}, act)
    assert done == [] and seen == {"stacking:7": "running"}
    seen, done = tray.finished_pictures(seen, [{**act[0], "state": "done"}])
    assert done == ["M31"]
    _, done = tray.finished_pictures(seen, [{**act[0], "state": "done"}])                # nur einmal melden
    assert done == []
    img = tray.draw_icon(True, 32)
    assert img.size == (32, 32) and img.getpixel((26, 26))[0] > 200                    # oranger Punkt unten rechts


def test_infobereich_abschaltbar_und_einmalig(home, monkeypatch):
    from astroflow.core import tray
    started = []
    monkeypatch.setattr(tray, "running_pid", lambda: None)
    monkeypatch.setattr("subprocess.Popen", lambda args, **kw: started.append(args))
    monkeypatch.setenv("ASTROFLOW_NO_TRAY", "1")
    tray.ensure(8766)
    assert started == [] and tray.main(8766) == 0
    monkeypatch.delenv("ASTROFLOW_NO_TRAY")
    tray.ensure(8766)
    assert started and started[0][-3:] == ["tray", "--port", "8766"]
    monkeypatch.setattr(tray, "running_pid", lambda: 4242)                              # läuft schon → kein zweites Symbol
    started.clear()
    tray.ensure(8766)
    assert started == []


def test_infobereich_endet_mit_stoppmarke(home, monkeypatch):
    from astroflow.core import tray
    t = tray.Tray(8799)
    closed = []
    monkeypatch.setattr(t, "close", lambda: closed.append(1) or t.stop.set())
    monkeypatch.setattr(t, "get", lambda path, timeout=4: {"tray_notify": False} if path == "/api/setup" else {})
    monkeypatch.setattr(tray, "POLL_S", 0.01)
    tray.stop_path().parent.mkdir(parents=True, exist_ok=True)
    tray.stop_path().write_text("1")
    t.loop()
    assert closed and not tray.stop_path().exists() and t.notify is False
