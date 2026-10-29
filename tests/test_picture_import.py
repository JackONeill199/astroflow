"""Gemeinsamer Import: Roh-FITS und fertige Bilder bleiben sauber getrennt."""
import io

import numpy as np
import pytest
from astropy.io import fits
from fastapi.testclient import TestClient
from PIL import Image

from astroflow.core.config import load_settings
from astroflow.server import create_app


def picture(fmt="PNG"):
    buf = io.BytesIO()
    Image.new("RGB", (40, 30), (60, 100, 140)).save(buf, fmt)
    return buf.getvalue()


def app_at(tmp_path):
    s = load_settings(tmp_path / "data")
    s.auto = False
    return create_app(s)


@pytest.mark.parametrize("ext,fmt", [("jpg", "JPEG"), ("png", "PNG"), ("tiff", "TIFF")])
def test_finished_picture_preserves_original_and_is_not_a_raw_frame(tmp_path, ext, fmt):
    app = app_at(tmp_path)
    data = picture(fmt)
    events, stacks = [], []
    app.state.ctx.on("stacking.imported", lambda **kw: events.append(kw))
    app.state.ctx.on("stacking.stacked", lambda **kw: stacks.append(kw))
    with TestClient(app) as c:
        uploaded = c.post("/api/stacking/upload", files={"files": (f"M31.{ext}", data)}).json()
        assert uploaded["saved"] == 1 and uploaded["raw"] == 0 and not uploaded["errors"]
        pid = uploaded["pictures"][0]["id"]
        p = c.get(f"/api/stacking/projects/{pid}").json()
        assert p["imported"] and p["total"] == 0 and not p["deleted"]["can_restack"]
        assert p["nights"] == [] and not p["can_solve"]
        assert p["result"]["stats"]["shape"] == [30, 40]
        assert "quality" not in p["result"]["stats"]
        rid = p["result"]["id"]
        assert c.get(f"/api/stacking/results/{rid}/original").content == data
        assert Image.open(io.BytesIO(c.get(f"/api/stacking/results/{rid}/jpg").content)).size == (40, 30)
        assert c.get(f"/api/stacking/results/{rid}/fits").status_code == 404
        for options in ({}, {"mode": "develop"}, {"restack": True}):
            assert c.post(f"/api/stacking/projects/{pid}/process", json=options).status_code == 409
        assert c.post(f"/api/stacking/projects/{pid}/solve").status_code == 409
        assert c.get("/api/stacking/jobs").json() == []
        assert c.get("/api/stacking/statistics").json()["frames"] == 0
        assert len(events) == 1 and events[0]["result_id"] == rid and not stacks
        again = c.post("/api/stacking/upload", files={"files": (f"anders.{ext}", data)}).json()
        assert again["pictures"][0]["duplicate"] and again["pictures"][0]["id"] == pid
        assert len(c.get("/api/stacking/projects").json()) == 1 and len(events) == 1
        assert c.delete(f"/api/stacking/projects/{pid}").status_code == 200
        assert c.get(f"/api/stacking/results/{rid}/original").status_code == 404
        assert c.post("/api/stacking/upload", files={"files": (f"neu.{ext}", data)}).json()["pictures"][0]["duplicate"] is False


def test_mixed_import_and_bad_picture(tmp_path):
    app = app_at(tmp_path)
    raw = io.BytesIO()
    fits.PrimaryHDU(np.ones((24, 32), np.uint16), header=fits.Header({"OBJECT": "M31", "DATE-OBS": "2022-09-22T22:00:00"})).writeto(raw)
    with TestClient(app) as c:
        d = c.post("/api/stacking/upload", files=[("files", ("nacht/M31.fits", raw.getvalue())),
                   ("files", ("M31.png", picture())), ("files", ("kaputt.jpg", b"kein Bild"))]).json()
        assert d["saved"] == 2 and d["raw"] == 1 and len(d["pictures"]) == 1 and len(d["errors"]) == 1
        c.post("/api/stacking/scan")
        projects = c.get("/api/stacking/projects").json()
        assert len(projects) == 2
        assert sum(p["total"] for p in projects) == 1
        assert sum(p["imported"] for p in projects) == 1


def test_16bit_tiff_preserves_tones_and_orientation(tmp_path):
    import tifffile
    a = np.zeros((20, 30, 3), np.uint16)
    a[:10] = 32768
    a[10:] = 16384
    buf = io.BytesIO()
    tifffile.imwrite(buf, a, photometric="rgb")
    with TestClient(app_at(tmp_path)) as c:
        d = c.post("/api/stacking/upload", files={"files": ("M31.tif", buf.getvalue())}).json()
        p = c.get(f"/api/stacking/projects/{d['pictures'][0]['id']}").json()
        rid = p["result"]["id"]
        assert p["result"]["stats"]["imported"]["bit_depth"] == 16
        assert c.get(f"/api/stacking/results/{rid}/original").content == buf.getvalue()
        preview = np.asarray(Image.open(io.BytesIO(c.get(f"/api/stacking/results/{rid}/png").content)))
        assert abs(int(preview[4, 4, 0]) - 128) <= 1
        assert abs(int(preview[15, 4, 0]) - 64) <= 1


def test_finished_picture_automatically_reaches_sage(tmp_path, monkeypatch):
    from test_sage import _wait
    monkeypatch.setenv("ASTROFLOW_MODULES", "stacking,sage")
    app = app_at(tmp_path)
    app.state.ctx.services["plate_solver"] = None      # hier den Import→Sage-Weg testen, kein echtes ASTAP
    with TestClient(app) as c:
        c.post("/api/sage/config", json={"auto_story": False})
        d = c.post("/api/stacking/upload", files={"files": ("M31.png", picture())}).json()
        tasks = c.get("/api/sage/tasks").json()
        assert len(tasks) == 1
        t = _wait(c, tasks[0]["id"])
        assert t["state"] == "done", t
        entry = c.get(f"/api/sage/history/{t['entry']}").json()
        assert entry["source"]["project"] == d["pictures"][0]["id"]
        assert entry["source"]["auto"] and entry["source"]["n_frames"] == 0


def test_unknown_filename_is_not_invented_as_a_discovered_object(tmp_path, monkeypatch):
    from test_sage import _wait
    monkeypatch.setenv("ASTROFLOW_MODULES", "stacking,sage")
    app = app_at(tmp_path)
    app.state.ctx.services["plate_solver"] = None
    with TestClient(app) as c:
        d = c.post("/api/stacking/upload", files={"files": ("Urlaubsfoto.png", picture())}).json()
        t = _wait(c, c.get("/api/sage/tasks").json()[0]["id"])
        assert t["state"] == "error" and "Himmelsposition" in t["error"]
        assert c.get("/api/sage/history").json() == []
        assert c.get(f"/api/stacking/projects/{d['pictures'][0]['id']}").json()["imported"]
