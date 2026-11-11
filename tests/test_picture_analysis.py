"""Analyse-Reiter für fertig importierte Bilder: Bildfeld/Maßstab, grobe Bildqualität, Objekte, eigene Aufnahmen (ASTAP-Attrappe)."""
import json
import time

from fastapi.testclient import TestClient

from astroflow.modules.stacking import picture_analysis, picture_solve
from test_picture_solve import env, sky_png, wait_solve  # noqa: F401  (Fixture `env`)


def test_field_info_classes_and_north():
    base = {"fov_w": 1.29, "fov_h": 0.73, "scale": 2.4, "rotation": 180.0, "width": 1080, "height": 1920}
    f = picture_analysis.field_info(base)
    assert f["device_class"] == "smart" and f["north"] == 0.0 and "beschnitten" in f["caveat"]
    assert picture_analysis.field_info({**base, "fov_w": 8.0, "fov_h": 5.0, "scale": 20.0})["device_class"] == "wide"
    assert picture_analysis.field_info({**base, "fov_w": 0.4, "fov_h": 0.3, "scale": 0.8})["device_class"] == "classic"
    assert picture_analysis.field_info({**base, "fov_w": 0.5, "fov_h": 0.3, "scale": 2.4})["device_class"] == "smart_crop"
    assert picture_analysis.north_angle(90.0) == 90.0 and picture_analysis.north_angle(-170.0) == -10.0


def test_solved_picture_gets_analysis_objects_and_cache(env):
    app, eng, fake, events = env
    with TestClient(app) as c:
        d = c.post("/api/stacking/upload", files={"files": ("Bild_0815.png", sky_png())}).json()["pictures"][0]
        pid, rid = d["id"], d["result_id"]
        wait_solve(c, pid)
        a = c.get(f"/api/stacking/results/{rid}/picture-analysis").json()
        assert a["solved"] and a["v"] == picture_analysis.PICTURE_ANALYSIS_VERSION
        assert abs(a["field"]["scale"] - 4.0) < 0.01 and a["field"]["north"] == 0.0 and a["field"]["device_class"] in ("smart_crop", "smart", "classic")
        assert any(("31" in o["name"] or "Andromeda" in o["name"]) and o["x"] is not None for o in a["objects"])    # Schreibweise je nach Katalogstand
        q = a["quality"]
        assert q["background"]["mono"] is True and "noise" in q["background"] and "cast" in q["background"]
        assert q["n_stars"] > 5
        # Ergebnis ist gecacht und steht nicht in der gepollten Projektansicht
        assert picture_analysis.cache_path(eng.s.work_dir, pid).exists()
        assert "picture-analysis" not in json.dumps(c.get(f"/api/stacking/projects/{pid}").json())
        assert c.get(f"/api/stacking/results/{rid}/picture-analysis").json()["field"] == a["field"]
        # Objektpositionen fürs Bild (Überlagerung) kommen aus derselben Lösung
        o = c.get(f"/api/stacking/results/{rid}/objects").json()
        assert o["width"] == 400 and o["height"] == 300 and any(x["x"] is not None for x in o["objects"])
        # zweites Bild desselben Objekts → „Eigene Aufnahmen“
        d2 = c.post("/api/stacking/upload", files={"files": ("Zweites.png", sky_png(seed=5))}).json()["pictures"][0]
        wait_solve(c, d2["id"])
        own = c.get(f"/api/stacking/results/{rid}/picture-analysis").json()["own"]
        assert [x["project"] for x in own] == [d2["id"]] and own[0]["imported"] is True


def test_unsolved_picture_still_measures_but_has_no_field(env):
    app, eng, fake, events = env
    (fake / "mode.txt").write_text("fail")
    with TestClient(app) as c:
        d = c.post("/api/stacking/upload", files={"files": ("Urlaub.png", sky_png())}).json()["pictures"][0]
        wait_solve(c, d["id"])
        a = c.get(f"/api/stacking/results/{d['result_id']}/picture-analysis").json()
        assert a["solved"] is False and "field" not in a and "objects" not in a
        assert a["quality"]["stars"]["fwhm_arcsec"] is None and a["quality"]["stars"]["fwhm_px"] > 0
        assert c.get(f"/api/stacking/results/{d['result_id']}/objects").json()["objects"] == []


def test_solve_button_works_when_object_was_named_by_hand(env):
    app, eng, fake, events = env
    monkey_off = picture_solve.enabled
    with TestClient(app) as c:
        picture_solve.enabled = lambda: False
        try:
            pid = c.post("/api/stacking/upload", files={"files": ("x.png", sky_png())}).json()["pictures"][0]["id"]
        finally:
            picture_solve.enabled = monkey_off
        c.patch(f"/api/stacking/projects/{pid}", json={"name": "IC 1805"})          # Objekt bekannt, aber keine Lösung
        assert c.post(f"/api/stacking/projects/{pid}/solve").status_code == 409     # ohne Zusatz bleibt es dabei
        assert c.post(f"/api/stacking/projects/{pid}/solve", json={"force": True}).json() == {"picture_solve": True}
        wait_solve(c, pid, ("done",))
        assert c.post(f"/api/stacking/projects/{pid}/solve", json={"force": True}).status_code == 409     # jetzt gibt es eine Lösung


def test_analysis_refused_for_raw_projects(env):
    app, eng, fake, events = env
    with TestClient(app) as c:
        assert c.get("/api/stacking/results/9999/picture-analysis").status_code == 404
