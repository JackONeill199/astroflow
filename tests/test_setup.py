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
from astroflow.core import launcher, setup as setup_mod
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

    def fake(url, prog, to=None):
        urls.append(url)
        data = served[url.split("/")[-2]]
        prog(1.0)
        if to:
            Path(to).write_bytes(data)
            return None
        return data
    monkeypatch.setattr(setup_mod, "_download", fake)
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
    assert v("v0.40.0") > v(__version__)


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
