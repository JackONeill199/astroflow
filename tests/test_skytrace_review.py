"""SkyTrace: Befunde aus dem Code-Review (Auto-Analyse, Prozess-Wächter, Adresssuche, Start-Fehler)."""
import subprocess
import sys
import time

import pytest
from fastapi.testclient import TestClient

from astroflow.core.config import load_settings
from astroflow.server import create_app
from test_auto import _app


def _frames(tmp_path, n=3):
    raw = tmp_path / "roh"
    raw.mkdir()
    out = []
    for i in range(n):
        p = raw / f"img-{i}.fits"
        p.write_bytes(b"FITS")
        out.append({"id": f"f{i}", "path": str(p), "night": "2026-10-28", "status": "ok"})
    return out


def test_fehlschlag_behaelt_aufnahmen_und_holt_nach(tmp_path, monkeypatch):
    """#34: Lauf scheitert/Engine fehlt → Aufnahmen bleiben, nach Einrichten der Engine erneut eingereiht."""
    app = _app(tmp_path, monkeypatch, _frames(tmp_path))
    with TestClient(app) as c:
        c.post("/api/sage/config", json={"auto_discover": False})
        app.state.ctx.emit("stacking.stacked", project_id=1, result_id=1, name="M74")
        auto = app.state.modules["skytrace"].auto
        item = auto.state["queue"][0]
        auto._finish(item, "skipped", "Engine fehlt")
        assert (auto.dir / item["folder"]).is_dir() and not auto.state["queue"]
        assert auto.retry_waiting() == 1 and auto.state["queue"][0]["state"] == "queued"
        auto._finish(auto.state["queue"][0], "stopped", "angehalten")
        assert not (auto.dir / item["folder"]).exists()


def test_kopierfehler_raeumt_auf(tmp_path, monkeypatch):
    """#35: Kopie (anderer Datenträger) läuft im Thread; scheitert sie, bleibt nichts Halbes zurück."""
    app = _app(tmp_path, monkeypatch, _frames(tmp_path))
    import astroflow.modules.skytrace.auto as auto_mod

    def no_link(*a):
        raise OSError("anderes Laufwerk")

    def no_copy(*a, **k):
        raise OSError("Platte voll")
    monkeypatch.setattr(auto_mod.os, "link", no_link)
    monkeypatch.setattr(auto_mod.shutil, "copy2", no_copy)
    with TestClient(app) as c:
        c.post("/api/sage/config", json={"auto_discover": False})
        app.state.ctx.emit("stacking.stacked", project_id=1, result_id=1, name="M74")
        auto = app.state.modules["skytrace"].auto
        end = time.monotonic() + 10
        while auto.state["queue"] and time.monotonic() < end:
            time.sleep(0.02)
        assert not auto.state["queue"] and not auto.state["sent"] and not any(auto.dir.iterdir())


def test_auto_uebernimmt_nur_eigenen_laufstatus(tmp_path, monkeypatch):
    """#36: Ausgang je Lauf statt des aktuellen Status."""
    app = _app(tmp_path, monkeypatch, [])
    with TestClient(app):
        ctl = app.state.modules["skytrace"].ctl
        ctl.outcomes["a"] = dict(failed=True, stopping=False, status="kaputt")
        ctl.failed, ctl.current = False, "b"
        assert ctl.outcome("a")["failed"] and ctl.outcome("b") is None


def test_prozesswaechter_beendet_haengenden_prozess():
    from astroflow.modules.skytrace.procguard import end_process
    p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    t = time.monotonic()
    end_process(p, grace=0.2)
    assert p.poll() is not None and time.monotonic() - t < 15


def test_startfehler_hinterlaesst_keinen_ordner(tmp_path, monkeypatch):
    """#37: Popen scheitert → kein leerer Ergebnisordner."""
    monkeypatch.setenv("ASTROFLOW_MODULES", "stacking,skytrace")
    monkeypatch.setenv("SKYTRACE_HOME", str(tmp_path / "home"))
    src = tmp_path / "quelle" / "2026-10-28_observation_M74"
    src.mkdir(parents=True)
    for i in range(3):
        (src / f"img-{i}.fits").write_bytes(b"FITS")
    import astroflow.modules.skytrace.api as api

    calls = []

    def kaputt(*a, **k):
        calls.append(a)
        raise OSError("Interpreter fehlt")
    monkeypatch.setattr(api.subprocess, "Popen", kaputt)
    with TestClient(create_app(load_settings(tmp_path / "d"))) as c:
        r = c.post("/api/skytrace/start", json={"source": str(src.parent), "observation": src.name})
        assert r.status_code == 400 and calls
        res = tmp_path / "home" / "Ergebnisse"
        assert not res.exists() or not any(res.iterdir())


@pytest.mark.parametrize("antwort", [[], {"features": "x"}, {"features": [{"properties": {}}, 5, {"geometry": {"coordinates": [1]}}]}])
def test_adresssuche_unerwartete_antwort(tmp_path, monkeypatch, antwort):
    """#38: unbrauchbare Photon-Antwort → 502 bzw. leere Liste statt 500."""
    import httpx
    monkeypatch.setenv("ASTROFLOW_MODULES", "stacking,skytrace")
    monkeypatch.setenv("SKYTRACE_HOME", str(tmp_path / "home"))

    class R:
        def json(self):
            return antwort
    monkeypatch.setattr(httpx, "get", lambda *a, **k: R())
    with TestClient(create_app(load_settings(tmp_path / "d"))) as c:
        r = c.get("/api/skytrace/geocode", params={"q": "Irgendwo"})
        assert (r.status_code, r.json()) == (200, []) if isinstance(antwort, dict) and isinstance(antwort["features"], list) else r.status_code == 502


def test_halbe_kopie_nach_neustart_verworfen(tmp_path, monkeypatch):
    """Server-Ende während einer Kopie: Eintrag „staging“ wird beim Start verworfen, Aufnahmen wieder frei."""
    import json
    app = _app(tmp_path, monkeypatch, [])
    auto = app.state.modules["skytrace"].auto
    (auto.dir / "n_observation_X").mkdir(parents=True)
    auto.file.write_text(json.dumps({"sent": {"f1": "n_observation_X"}, "done": [],
                                     "queue": [{"folder": "n_observation_X", "state": "staging"}]}), encoding="utf-8")
    from astroflow.modules.skytrace.auto import AutoAnalysis, _is_upload
    neu = AutoAnalysis(auto.mod)
    assert not neu.state["queue"] and not neu.state["sent"] and not (auto.dir / "n_observation_X").exists()
    # mit gemerkter Kopierliste: gesicherte Dateien bleiben, die Kopie wird fortgesetzt
    ext = tmp_path / "nas"
    ext.mkdir()
    (ext / "b.fits").write_bytes(b"FITS")
    d = auto.dir / "n_observation_Y"
    d.mkdir()
    (d / "00000_a.fits").write_bytes(b"FITS")                       # schon gesicherter Upload
    auto.file.write_text(json.dumps({"sent": {"f1": d.name, "f2": d.name}, "done": [], "queue": [
        {"folder": d.name, "state": "staging", "copies": [[str(ext / "b.fits"), str(d / "00001_b.fits")]], "ids": ["f1", "f2"]}]}),
        encoding="utf-8")
    neu = AutoAnalysis(auto.mod)
    neu.start()
    end = time.monotonic() + 10
    while neu.state["queue"][0]["state"] == "staging" and time.monotonic() < end:
        time.sleep(0.02)
    neu.stop()
    assert neu.state["queue"][0]["state"] == "queued" and sorted(x.name for x in d.iterdir()) == ["00000_a.fits", "00001_b.fits"]
    assert _is_upload(tmp_path / "data" / "input" / "uploads" / "a.fits") and not _is_upload(tmp_path / "nas" / "a.fits")
