"""Fertig importierte Bilder bekommen ihr Objekt per Plate-Solving im Hintergrund (ASTAP-Attrappe, kein echtes ASTAP)."""
import hashlib
import io
import json
import sys
import time

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from astroflow.core.config import load_settings
from astroflow.modules.stacking import picture_solve
from astroflow.server import create_app
from test_platesolve import FAKE


def sky_png(seed=1, size=(400, 300)):
    """Sternfeld ohne Objektbezug im Dateinamen (die Attrappe „löst“ jedes Bild auf M31)."""
    rng = np.random.default_rng(seed)
    a = rng.normal(20, 3, size[::-1]).clip(0, 255)
    for _ in range(60):
        y, x = rng.integers(5, size[1] - 5), rng.integers(5, size[0] - 5)
        a[y - 1:y + 2, x - 1:x + 2] = 250
    buf = io.BytesIO()
    Image.fromarray(a.astype(np.uint8)).convert("RGB").save(buf, "PNG")
    return buf.getvalue()


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("ASTROFLOW_PICTURE_SOLVE", "1")
    monkeypatch.setenv("ASTROFLOW_DEEPSKY", "0")             # nur der eingebaute Katalog (gleich auf allen Rechnern)
    fake = tmp_path / "fake"
    fake.mkdir()
    (fake / "astap.py").write_text(FAKE, encoding="utf-8")
    (fake / "mode.txt").write_text("ok")
    s = load_settings(tmp_path / "data")
    s.auto = False
    app = create_app(s)
    eng = app.state.modules["stacking"].eng
    cfg = eng.pl.solver_cfg
    cfg.backend, cfg.cmd_prefix, cfg.timeout = "astap", [sys.executable, str(fake / "astap.py")], 60
    events = []
    app.state.ctx.on("stacking.imported", lambda **kw: events.append(kw))
    return app, eng, fake, events


def wait_solve(c, pid, states=("done", "failed", "manual")):
    for _ in range(600):
        p = c.get(f"/api/stacking/projects/{pid}").json()
        if (p["options"].get("picture_solve") or {}).get("state") in states:
            return p
        time.sleep(0.05)
    raise AssertionError("Plate-Solving hängt")


def calls(fake):
    p = fake / "calls.txt"
    return p.read_text().splitlines() if p.exists() else []


def test_import_solves_unknown_picture_and_then_tells_sage(env):
    app, eng, fake, events = env
    data = sky_png()
    with TestClient(app) as c:
        d = c.post("/api/stacking/upload", files={"files": ("Bild_0815.png", data)}).json()
        pid = d["pictures"][0]["id"]
        p = wait_solve(c, pid)
        ps = p["options"]["picture_solve"]
        assert ps["state"] == "done" and ps["object"] == "M31 Andromedagalaxie"
        assert p["name"] == "M31 Andromedagalaxie" and p["kind"] == "galaxy" and not p["can_solve"]
        assert p["coords"]["source"] == "solve" and abs(ps["solution"]["fov_h"] - 300 * 4 / 3600) < 1e-3
        for _ in range(100):                                  # Meldung an Sage erst nach dem Lösen, mit dem neuen Namen
            if events:
                break
            time.sleep(0.05)
        assert events == [{"project_id": pid, "result_id": d["pictures"][0]["result_id"], "name": "M31 Andromedagalaxie"}]
        # erster Versuch: blind mit vielen Sternen (kein Gerät bekannt), nie online
        assert "-fov 0" in calls(fake)[0] and "-s 1000" in calls(fake)[0] and "-r 180" in calls(fake)[0]
        # Lösung für andere Module (Sage) mit Objektpositionen im angezeigten Bild
        sol = app.state.ctx.service("project_solution")(pid)
        assert sol["objects"][0]["id"] == "M31" and sol["width"] == 400 and sol["height"] == 300
        # Original unverändert, kein Stacking/Entwickeln
        res = c.get(f"/api/stacking/projects/{pid}").json()["results"]
        assert len(res) == 1
        orig = c.get(f"/api/stacking/results/{res[0]['id']}/original").content
        assert hashlib.sha256(orig).hexdigest() == hashlib.sha256(data).hexdigest()
        assert not eng.db.q("SELECT id FROM jobs")
        assert any(a["kind"] == "Objekt bestimmen" and a["state"] == "done" for a in c.get("/api/activity").json()["items"]) \
            if "items" in c.get("/api/activity").json() else True


def test_failed_solve_is_quiet_and_can_be_retried(env):
    app, eng, fake, events = env
    (fake / "mode.txt").write_text("fail")
    with TestClient(app) as c:
        pid = c.post("/api/stacking/upload", files={"files": ("Urlaub.png", sky_png())}).json()["pictures"][0]["id"]
        p = wait_solve(c, pid)
        assert p["options"]["picture_solve"]["state"] == "failed"
        assert p["options"]["picture_solve"]["message"] == picture_solve.MSG_NONE
        assert p["name"] == "Urlaub" and p["kind"] is None and p["can_solve"] and p["job"] is None
        assert len(calls(fake)) == 2                          # blind mit 1000 und 500 Sternen
        for _ in range(100):
            if events:
                break
            time.sleep(0.05)
        assert len(events) == 1                               # Sage wird trotzdem informiert (wie bisher)
        (fake / "mode.txt").write_text("ok")
        assert c.post(f"/api/stacking/projects/{pid}/solve").json() == {"picture_solve": True}
        p = wait_solve(c, pid, ("done",))
        assert p["name"] == "M31 Andromedagalaxie"
        for _ in range(100):                                  # erneuter Versuch per Knopf meldet den Erfolg an Sage
            if len(events) == 2:
                break
            time.sleep(0.05)
        assert events[-1]["name"] == "M31 Andromedagalaxie"


def test_user_name_wins_and_catalog_name_sets_object(env):
    app, eng, fake, events = env
    monkey_off = picture_solve.enabled
    with TestClient(app) as c:
        picture_solve.enabled = lambda: False                 # Import ohne automatisches Lösen
        try:
            pid = c.post("/api/stacking/upload", files={"files": ("x.png", sky_png())}).json()["pictures"][0]["id"]
        finally:
            picture_solve.enabled = monkey_off
        # freier Name: bleibt, Objekt noch unbekannt
        p = c.patch(f"/api/stacking/projects/{pid}", json={"name": "Mein Lieblingsbild"}).json()
        assert p["name"] == "Mein Lieblingsbild" and p["can_solve"] and p["options"]["name_custom"]
        c.post(f"/api/stacking/projects/{pid}/solve")
        p = wait_solve(c, pid)
        assert p["name"] == "Mein Lieblingsbild" and p["kind"] == "galaxy"     # Objekt gesetzt, Name bleibt
        # Katalogname von Hand: setzt Objekt und Koordinaten (überstimmt die Lösung)
        p = c.patch(f"/api/stacking/projects/{pid}", json={"name": "IC 1805"}).json()
        assert p["kind"] == "emission" and p["options"]["picture_solve"]["state"] == "manual"
        assert abs(p["coords"]["ra"] - 38.295) < 1e-3


def test_backfill_solves_old_imports_once(env, monkeypatch):
    app, eng, fake, events = env
    monkeypatch.setenv("ASTROFLOW_PICTURE_SOLVE", "0")
    with TestClient(app) as c:
        pid = c.post("/api/stacking/upload", files={"files": ("alt.png", sky_png())}).json()["pictures"][0]["id"]
        assert "picture_solve" not in c.get(f"/api/stacking/projects/{pid}").json()["options"]
        monkeypatch.setenv("ASTROFLOW_PICTURE_SOLVE", "1")
        eng.picture_solve.backfill()
        assert wait_solve(c, pid)["name"] == "M31 Andromedagalaxie"
        n = len(calls(fake))
        eng.picture_solve.backfill()                          # schon gelöst: kein zweiter Versuch
        time.sleep(0.3)
        assert len(calls(fake)) == n


def test_no_solver_keeps_old_behaviour(tmp_path, monkeypatch):
    monkeypatch.setenv("ASTROFLOW_PICTURE_SOLVE", "1")
    s = load_settings(tmp_path / "data")
    app = create_app(s)
    app.state.modules["stacking"].eng.pl.solver_cfg.backend = "off"
    events = []
    app.state.ctx.on("stacking.imported", lambda **kw: events.append(kw))
    with TestClient(app) as c:
        pid = c.post("/api/stacking/upload", files={"files": ("y.png", sky_png())}).json()["pictures"][0]["id"]
        assert len(events) == 1                                # sofort gemeldet, kein Lösungsversuch
        assert "picture_solve" not in c.get(f"/api/stacking/projects/{pid}").json()["options"]
        r = c.post(f"/api/stacking/projects/{pid}/solve")
        assert r.status_code == 400 and "Plate-Solver" in r.json()["detail"]


def test_attempt_order_uses_device_field_first():
    assert picture_solve.attempts({"filename": "Stacked_30_NGC 7000_10.0s_LP_20231105.jpg"}, 1080, 1920)[0] == (1.29, 500)
    assert picture_solve.attempts({"series": {"instrument": "Stellina"}}, 3000, 2000)[0] == (0.7, 500)
    assert picture_solve.attempts({"model": "Vespera II"}, 3840, 2160)[0] == (1.4, 500)
    assert picture_solve.attempts({}, 970, 1724) == [(0.0, 1000), (0.0, 500)]


def test_unexpected_error_does_not_stay_busy(env, monkeypatch):
    app, eng, fake, events = env

    def boom(*a, **k):
        raise PermissionError("kein Zugriff")
    monkeypatch.setattr(picture_solve, "solve_picture", boom)
    with TestClient(app) as c:
        pid = c.post("/api/stacking/upload", files={"files": ("z.png", sky_png())}).json()["pictures"][0]["id"]
        p = wait_solve(c, pid)
        assert p["options"]["picture_solve"]["state"] == "failed" and "kein Zugriff" not in p["options"]["picture_solve"]["message"]
        for _ in range(100):
            if eng.picture_solve.busy() == 0:
                break
            time.sleep(0.05)
        assert eng.picture_solve.busy() == 0
        assert not [a for a in eng.activity() if a["kind"] == "Objekt bestimmen" and a["state"] == "running"]


def test_series_solves_the_shown_version(env, monkeypatch):
    """Teleskop-Serie ohne bekanntes Objekt, Versionen in falscher Reihenfolge importiert: gelöst wird die Version,
    die auch Sage zeigt (höchste Sequenz), nicht der zuletzt importierte Upload."""
    from test_picture_import import telescope_jpg
    app, eng, fake, events = env
    seen = []
    real = picture_solve.solve_picture
    monkeypatch.setattr(picture_solve, "solve_picture", lambda cfg, image, *a, **k: seen.append(image) or real(cfg, image, *a, **k))
    with TestClient(app) as c:
        picture_solve_enabled = picture_solve.enabled
        picture_solve.enabled = lambda: False
        try:
            for seq in (5, 2):
                d = c.post("/api/stacking/upload", files={"files": (f"img-{seq:04d}-output.jpg", telescope_jpg(seq, name="Unbekanntes Feld 7"))}).json()
        finally:
            picture_solve.enabled = picture_solve_enabled
        pid = d["pictures"][0]["id"]
        shown = app.state.ctx.service("project_picture")(pid)
        c.post(f"/api/stacking/projects/{pid}/solve")
        wait_solve(c, pid)
        assert str(seen[0]) == shown["image"]
