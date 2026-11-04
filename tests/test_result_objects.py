"""Objektnamen im Bild (Hover in „Bilder“): /results/<id>/objects liefert Positionen aus der Plate-Solving-Lösung des
Master-Stacks, ohne Lösung eine leere Liste, und speichert das Ergebnis je Version zwischen."""
import json

from fastapi.testclient import TestClient

from astroflow.core.config import load_settings
from astroflow.modules.stacking import services
from astroflow.modules.stacking.db import DB
from astroflow.server import create_app


def _result(s):
    db = DB(s.db_path)
    pid = db.x("INSERT INTO projects(key, name, created) VALUES('t','Testobjekt',0)")
    rid = db.x("INSERT INTO results(project_id, created, n_frames, exposure, files, stats) VALUES(?,?,?,?,?,?)",
               (pid, 0, 1, 1, json.dumps({"fits": "fehlt.fits", "jpg": "fehlt.jpg"}), "{}"))
    return pid, rid


def test_objects_without_and_with_solution(tmp_path, monkeypatch):
    s = load_settings(tmp_path / "d")
    s.auto = False
    with TestClient(create_app(s)) as c:
        pid, rid = _result(s)
        assert c.get("/api/stacking/results/999999/objects").status_code == 404
        # ohne Plate-Solving: leer, mit Grund
        d = c.get(f"/api/stacking/results/{rid}/objects").json()
        assert d["objects"] == [] and "Plate-Solving" in d["reason"]
        # mit Lösung: Positionen aus project_solution, nur Objekte mit Bildposition, danach aus dem Zwischenspeicher
        (s.work_dir / f"p{pid}").mkdir(parents=True, exist_ok=True)
        (s.work_dir / f"p{pid}" / "wcs.json").write_text("{}", encoding="utf-8")
        calls = []
        sol = {"width": 400, "height": 300, "objects": [{"name": "M 1", "x": 10.0, "y": 20.0, "radius": 6}, {"name": "ohne", "x": None, "y": None}]}
        monkeypatch.setattr(services, "project_solution", lambda *a: calls.append(a) or sol)
        d = c.get(f"/api/stacking/results/{rid}/objects").json()
        assert d == {"width": 400, "height": 300, "objects": [{"name": "M 1", "x": 10.0, "y": 20.0, "radius": 6}]}
        assert c.get(f"/api/stacking/results/{rid}/objects").json() == d and len(calls) == 1
