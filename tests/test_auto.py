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
        app.state.modules["sage"].backfill_thread.join(30)     # Start-Verknüpfung fertig, sonst vergibt ihr refresh() mitten im Test neue Erfolge
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
        assert len(q) == 1 and q[0]["frames"] == 3 and q[0]["night"] == "2026-10-28" and q[0]["folder"] == "2026-10-28_observation_M74"
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


def test_aufnahmen_fuer_den_bericht_bleiben(tmp_path, monkeypatch):
    """Nach einem automatischen Lauf: erstes Bild und Treffer ±2 wandern in den Ergebnisordner, Pfade zeigen dorthin."""
    import json
    raw = tmp_path / "Österreich"                                 # Nicht-ASCII im Pfad (JSON mit ensure_ascii=False)
    raw.mkdir()
    frames = [{"id": f"f{i}", "path": str(raw / f"Bild-ä{i:02d}.fits"), "night": "n1", "status": "ok"} for i in range(12)]
    for f in frames:
        open(f["path"], "wb").write(b"FITS")
    app = _app(tmp_path, monkeypatch, frames)
    with TestClient(app) as c:
        c.post("/api/sage/config", json={"auto_discover": False})
        app.state.ctx.emit("stacking.stacked", project_id=5, result_id=1, name="Plejaden Süd")
        auto = app.state.ctx.listeners["stacking.stacked"][0].__self__
        item = auto.state["queue"][0]
        staged = sorted((auto.dir / item["folder"]).iterdir())
        session = auto.mod.ctl.results / "lauf1" / "M45-x"
        session.mkdir(parents=True)
        rows = [{"index": i, "file": str(p), "detections": [{"kind": "trail"}] if i == 7 else []} for i, p in enumerate(staged)]
        dump = lambda v: json.dumps(v, ensure_ascii=False)                # wie sequence.py
        # Bild 0 und 6 unscharf ausgeschlossen: Referenz ist die erste akzeptierte Aufnahme (Index 1)
        (session / "frames.jsonl").write_text("\n".join(dump(r) for r in rows if r["index"] not in (0, 6)), encoding="utf-8")
        (session / "ausgeschlossen.jsonl").write_text("\n".join(dump({"index": i, "file": str(staged[i])}) for i in (0, 6)), encoding="utf-8")
        (session / "kandidaten.json").write_text(dump([{"source_file": str(staged[7]), "frames": [{"original": staged[7].as_uri()}]}]), encoding="utf-8")
        item["run"] = "lauf1"
        auto._finish(item, "done", "fertig")
        kept = sorted(p.name for p in (session / "rohbilder").iterdir())
        assert kept == [staged[i].name for i in (1, 5, 6, 7, 8, 9)] and not (auto.dir / item["folder"]).exists()
        first = json.loads((session / "frames.jsonl").read_text(encoding="utf-8").splitlines()[0])
        assert first["file"] == str(session / "rohbilder" / staged[1].name)
        cand = json.loads((session / "kandidaten.json").read_text(encoding="utf-8"))[0]
        assert cand["source_file"] == str(session / "rohbilder" / staged[7].name)
        assert cand["frames"][0]["original"] == (session / "rohbilder" / staged[7].name).as_uri()
        excluded = [json.loads(x) for x in (session / "ausgeschlossen.jsonl").read_text(encoding="utf-8").splitlines()]
        assert excluded[1]["file"] == str(session / "rohbilder" / staged[6].name) and excluded[0]["file"] == str(staged[0])
