"""Nach dem Stacken: Sage trägt das Bild in die Entdeckungen ein (mit Feier), SkyTrace übernimmt die Aufnahmen."""
import io

import numpy as np
from fastapi.testclient import TestClient
from PIL import Image

from astroflow.core.config import load_settings
from astroflow.server import create_app
from test_sage import _wait


def _app(tmp_path, monkeypatch, frames):
    monkeypatch.setattr("astroflow.modules.skytrace.Module.check_engine", lambda self: None)   # Engine nicht starten
    monkeypatch.setenv("ASTROFLOW_MODULES", "stacking,skytrace,sage")
    monkeypatch.setenv("SKYTRACE_HOME", str(tmp_path / "skytrace-home"))
    img = tmp_path / "ergebnis.jpg"
    Image.fromarray((np.random.default_rng(4).random((48, 64)) * 255).astype(np.uint8)).save(img)
    app = create_app(load_settings(tmp_path / "data"))
    sv = app.state.ctx.services
    sv["project_picture"] = lambda pid: {"name": "M74", "kind": "galaxy", "kind_label": "Galaxie", "image": str(img), "result_id": 7,
                                         "exposure": 600, "n_frames": 3, "ra": 24.2, "dec": 15.78, "files": {}}
    sv["project_solution"] = lambda pid: {"ra": 24.2, "dec": 15.78, "solver": "stack", "fov_w": 1, "fov_h": .7, "width": 64, "height": 48,
                                          "objects": [{"name": "M74", "kind_label": "Galaxie", "x": 30, "y": 20, "radius": 8}]}
    sv["project_frames"] = lambda pid: frames
    return app


def test_nach_dem_stacken(tmp_path, monkeypatch):
    raw = tmp_path / "data" / "input" / "uploads" / "nacht"
    raw.mkdir(parents=True)
    frames = []
    for i in range(3):
        p = raw / f"img-{i}.fits"
        p.write_bytes(b"FITS" * 10)
        frames.append({"id": f"f{i}", "path": str(p), "night": "2026-10-28", "status": "ok"})
    frames.append({"id": "einzeln", "path": str(p), "night": "2026-10-29", "status": "ok"})   # eine Aufnahme: keine Bildfolge
    app = _app(tmp_path, monkeypatch, frames)
    with TestClient(app) as c:
        c.post("/api/sage/config", json={"story_provider": "lmstudio", "auto_story": False})
        app.state.ctx.emit("stacking.stacked", project_id=3, result_id=7, name="M74")
        t = _wait(c, c.get("/api/sage/tasks").json()[0]["id"])
        assert t["state"] == "done" and t["discovery"]["new"] == ["M74"]
        e = c.get(f"/api/sage/history/{t['entry']}").json()
        assert e["source"]["auto"] and e["story"] is None and "Neu erzählen" in e["story_error"]
        # Feier kommt mit dem Status-Polling, bis sie bestätigt ist
        cel = c.get("/api/status").json()["modules"]["sage"]["celebrate"]
        assert cel[0]["new"] == ["M74"] and cel[0]["auto"] and {a["id"] for a in cel[0]["achievements"]} >= {"first_star", "messier_first"}
        assert cel[0]["image"].startswith("/api/sage/files/uploads/projekt-3-7-")
        c.post("/api/sage/celebrations/ack", json={"ids": [cel[0]["id"]]})
        assert c.get("/api/status").json()["modules"]["sage"]["celebrate"] == []
        # dasselbe Ergebnis nicht doppelt eintragen
        app.state.ctx.emit("stacking.stacked", project_id=3, result_id=7, name="M74")
        assert len(c.get("/api/sage/history").json()) == 1
        # SkyTrace: Nacht mit 3 Aufnahmen als Beobachtungsordner aus Hardlinks, wartet auf die Engine
        auto = app.state.ctx.listeners["stacking.stacked"][0].__self__
        q = auto.state["queue"]
        assert len(q) == 1 and q[0]["frames"] == 3 and q[0]["night"] == "2026-10-28" and "_observation_2026-10-28" in q[0]["folder"]
        folder = auto.dir / q[0]["folder"]
        assert sorted(x.name for x in folder.iterdir()) == ["00000_img-0.fits", "00001_img-1.fits", "00002_img-2.fits"]
        (raw / "img-0.fits").unlink()                              # Upload gelöscht → Analyse-Kopie bleibt
        assert (folder / "00000_img-0.fits").read_bytes() == b"FITS" * 10
        assert any(a["state"] == "queued" and "M74" in a["title"] for a in c.get("/api/activity").json())
        assert c.get("/api/status").json()["modules"]["skytrace"]["auto_waiting"] == 1
        # schon übergebene Aufnahmen werden nicht noch einmal analysiert
        app.state.ctx.emit("stacking.stacked", project_id=3, result_id=8, name="M74")
        assert len(auto.state["queue"]) == 1


def test_automatik_abschaltbar(tmp_path, monkeypatch):
    raw = tmp_path / "r"
    raw.mkdir()
    frames = [{"id": f"f{i}", "path": str(raw / f"{i}.fits"), "night": "n", "status": "ok"} for i in range(2)]
    for f in frames:
        open(f["path"], "wb").write(b"x")
    app = _app(tmp_path, monkeypatch, frames)
    with TestClient(app) as c:
        c.post("/api/sage/config", json={"auto_discover": False})
        c.post("/api/skytrace/engine", json={"auto": False})
        app.state.ctx.emit("stacking.stacked", project_id=1, result_id=1, name="X")
        assert c.get("/api/sage/tasks").json() == [] and c.get("/api/status").json()["modules"]["skytrace"]["auto_waiting"] == 0
