"""Belichtungsbedarf je Ziel und Gerät (need.py) und seine Nutzung in Planer und Heute Nacht (offline, erfundener Ort)."""
import datetime as dt

import pytest

from astroflow.modules.stacking import deepsky, need, planner, tonight

SITE = {"id": "s1", "name": "Erfundener Garten", "lat": 50.0, "lon": 10.0}
EVENING = dt.datetime(2026, 11, 2, 17, 0, tzinfo=dt.timezone.utc).timestamp()
CLUSTER = {"id": "X1", "kind": "open_cluster", "mag": 6.0, "size": 20}
FAINT = {"id": "X2", "kind": "galaxy", "mag": 10.0, "size": 12}            # Flächenhelligkeit ~23,6 mag/″²
NEBULA = {"id": "X3", "kind": "emission", "mag": None, "size": 30}


@pytest.fixture(autouse=True)
def _no_astap(monkeypatch):
    monkeypatch.setenv("ASTROFLOW_DEEPSKY", "0")
    deepsky.reset()
    tonight._cache.clear()
    yield
    deepsky.reset()
    tonight._cache.clear()


def test_model_is_monotonic():
    raw = lambda t, dev="vespera", sqm=20.0, **k: need.need_hours(t, {"id": dev}, sqm, **k)["raw"]
    for t in (CLUSTER, FAINT, NEBULA):
        assert raw(t, sqm=21.5) < raw(t, sqm=20.0) < raw(t, sqm=18.5)                  # dunklerer Himmel → weniger
        assert raw(t, "stellina") < raw(t, "vespera") < raw(t, "seestar30")          # größere Öffnung → weniger
        assert raw(t, depth="short") < raw(t) < raw(t, depth="thorough")
    # gleiche Optik, nur größere Öffnung (gleiche f-Zahl) → weniger
    small, big = {"aperture": 50, "focal": 250}, {"aperture": 80, "focal": 400}
    for t in (CLUSTER, FAINT):
        assert need.need_hours(t, big, 20.0)["raw"] < need.need_hours(t, small, 20.0)["raw"]
    # Haufen weniger als schwacher Nebel bzw. schwache Galaxie, auf jedem Gerät und unter jedem Himmel
    for dev in ("stellina", "vespera", "seestar", "seestar30"):
        for sqm in (18.0, 20.0, 21.8, None):
            c, f, n = (need.need_hours(t, {"id": dev}, sqm)["hours"] for t in (CLUSTER, FAINT, NEBULA))
            assert c < n and c < f
    assert need.need_hours(deepsky.fixed()[44 - 1], {"id": "stellina"}, 20.0)["hours"] <= 1     # M44 (Krippe)


def test_stages_caps_and_reasons():
    r = need.need_hours(FAINT, {"id": "seestar30"}, 17.0, depth="thorough")
    assert r["hours"] in need.STAGES and r["hours"] <= need.DEPTH_CAP["thorough"]
    assert need.need_hours(FAINT, {"id": "seestar30"}, 17.0, depth="short")["hours"] <= need.DEPTH_CAP["short"]
    assert all(need.need_hours(t, {"id": "stellina"}, 20.0)["hours"] in need.STAGES for t in deepsky.fixed())
    r = need.need_hours(NEBULA, {"id": "library", "lib_id": "camera"}, None)
    assert "Optik" in " ".join(r["assumed"]) and "Himmel" in " ".join(r["assumed"])
    assert "80 mm f/5" in need.need_hours(FAINT, {"id": "stellina"}, 20.5)["why"]
    assert need.text(0.5, 0.0, 6.0).endswith("reicht heute") and "3 Nächte" in need.text(5.0, 0.0, 2.0)
    assert "erreicht" in need.text(2.0, 3.0, 4.0) and need.nights_for(3, 0) is None


def test_calibration_from_own_stacks():
    expect = need.M5_REF                                   # Stellina, 80 mm, Vorortshimmel → Erwartung nach 1 h
    deep = [{"device": "stellina", "family": "stellina", "exposure": 4 * 3600, "m5": expect + 1.25 * 0.602 + 0.5} for _ in range(4)]
    c = need.calibration(deep, {"id": "stellina"}, None)
    assert c["factor"] < 1 and c["status"] == "vorschlag" and "Vorschlag" in c["text"] and c["n"] == 4
    shallow = [{**r, "m5": r["m5"] - 1.0} for r in deep]
    assert need.calibration(shallow, {"id": "stellina"}, None)["factor"] > 1
    assert need.calibration(deep[:2], {"id": "stellina"}, None)["factor"] == 1.0          # zu wenige Stacks
    assert need.calibration(deep, {"id": "vespera"}, None)["factor"] == 1.0               # anderes Gerät
    assert need.calibration([{**r, "m5": 99} for r in deep], {"id": "stellina"}, None)["factor"] == need.CALIB_CLAMP[0]


def _plan(projects, tmp_path, **w):
    opts = planner.device_options(None)
    wishes = planner.clean_wishes({"nights": 1, "devices": ["stellina"], "mode": "mix", **w}, opts)
    devs = [next(o for o in opts if o["id"] == d) for d in wishes["devices"]]
    return planner.plan(projects, lambda night: SITE, wishes, devs, tmp_path, now=EVENING)


def test_short_targets_get_short_blocks(tmp_path):
    """Haufen bekommen kurze Blöcke (zwei passen in zwei Stunden), schwache Galaxien lange zusammenhängende."""
    p = _plan([], tmp_path, watch=["M36", "M37", "M38", "C23"])
    blocks = p["nights"][0]["blocks"]
    by = {}
    for b in blocks:
        by[b["target"]] = by.get(b["target"], 0) + (b["end"] - b["start"]) / 3600
    clusters = [t for t in ("M36", "M37", "M38") if t in by]
    assert len(clusters) >= 2 and all(by[t] <= 1.05 for t in clusters)
    assert by.get("C23", 0) >= 2.5                                            # NGC 891: lange am Stück
    kinds = {t["id"]: t for t in p["targets"]}
    assert kinds["M37"]["goal"] < kinds["C23"]["goal"]
    # zwei Haufen in einem 2-h-Fenster: zwei verschiedene Haufenblöcke liegen innerhalb von 2 h hintereinander
    cb = sorted((b for b in blocks if b["target"] in clusters), key=lambda b: b["start"])
    assert any(b2["end"] - b1["start"] <= 2 * 3600 + 60 and b1["target"] != b2["target"] for b1, b2 in zip(cb, cb[1:]))


def test_old_wishes_still_load(tmp_path):
    opts = planner.device_options(None)
    assert planner.clean_wishes({"devices": ["stellina"], "min_hours": 1}, opts)["depth"] == "short"
    assert planner.clean_wishes({"devices": ["stellina"], "min_hours": 2}, opts)["depth"] == "normal"
    assert planner.clean_wishes({"devices": ["stellina"], "min_hours": 4}, opts)["depth"] == "thorough"
    assert planner.clean_wishes({"devices": ["stellina"], "min_hours": 4, "depth": "short"}, opts)["depth"] == "short"
    old = {"nights": 1, "min_hours": 3, "devices": ["stellina"], "mode": "mix", "watch": [], "eq": []}    # gespeichert mit fester Mindestbelichtung
    p = planner.plan([], lambda night: SITE, old, [opts[0]], tmp_path, now=EVENING)
    assert p["targets"] and p["wishes"]["depth"] == "normal"


def test_existing_exposure_counts_against_need(tmp_path):
    m33 = {"id": 5, "name": "M33 Dreiecksgalaxie", "kind": "galaxy", "ra": 23.46, "dec": 30.66, "obj_id": "M33", "exposure": 0}
    fresh = _plan([m33], tmp_path, mode="deep")
    t = next(t for t in fresh["targets"] if t["id"] == "5")
    assert t["have"] == 0 and t["goal"] >= 2
    half = _plan([{**m33, "exposure": t["goal"] * 3600 / 2}], tmp_path, mode="deep")
    h = next(x for x in half["targets"] if x["id"] == "5")
    assert h["have"] > 0 and h["hours"] < t["hours"] and h["done"]
    full = _plan([{**m33, "exposure": 30 * 3600}], tmp_path, mode="deep")
    assert "5" not in {x["id"] for x in full["targets"]}                     # Bedarf schon gedeckt


def test_sky_and_calibration_reach_the_plan(tmp_path):
    opts = planner.device_options(None)
    w = planner.clean_wishes({"nights": 1, "devices": ["stellina"], "watch": ["C23"]}, opts)
    dark = planner.plan([], lambda n: SITE, w, [opts[0]], tmp_path, now=EVENING, sky=lambda s: (21.8, "Test"))
    bright = planner.plan([], lambda n: SITE, w, [opts[0]], tmp_path, now=EVENING, sky=lambda s: (18.0, "Test"))
    goal = lambda p: next(t["goal"] for t in p["targets"] if t["id"] == "C23")
    assert goal(dark) < goal(bright) and dark["sky"]["sqm"] == 21.8
    rows = [{"device": "stellina", "family": "stellina", "exposure": 7200, "m5": 15.0}] * 3
    cal = planner.plan([], lambda n: SITE, w, [opts[0]], tmp_path, now=EVENING, calib_rows=rows)
    assert "Vorschlag" in cal["devices"][0]["calibration"]


def test_tonight_shows_need(tmp_path):
    m31 = {"id": 7, "name": "M31 Andromedagalaxie", "kind": "galaxy", "ra": 10.68, "dec": 41.27, "obj_id": "M31", "exposure": 1800}
    dev = {"id": "stellina", "name": "Testgerät", "alt_az": True, "fov": [1, .7], "source": "Test", "mag_limit": 11.5}
    d = tonight.plan([m31], {**SITE, "weather": False}, tmp_path, EVENING + 2 * 3600, dev, site_sky=(20.5, "Test"))
    assert d["recommendations"] and all(x["need"]["text"] and x["need"]["hours"] in need.STAGES for x in d["recommendations"])
    own = next(x for x in d["recommendations"] if x["category"] == "more")
    assert own["need"]["have"] == 0.5 and "SQM 20,5" in own["need"]["why"]
    clusters = [x for x in d["recommendations"] if x["kind"] in ("open_cluster", "globular")]
    for x in clusters:
        assert x["need"]["nights"] == 1 and "reicht heute" in x["need"]["text"]


def test_calibration_only_from_the_same_model():
    """Seestar S50-Stacks eichen keine S30 (gleiche Familie, andere Optik)."""
    s50 = [{"device": "seestar", "family": "seestar", "exposure": 7200, "m5": 14.0}] * 4
    assert need.calibration(s50, {"id": "seestar30", "mag_id": "seestar"}, None)["factor"] == 1.0
    assert need.calibration(s50, {"id": "seestar"}, None)["n"] == 4


def test_existing_exposure_uses_the_capturing_device(tmp_path):
    """Vorhandene Belichtung zählt gegen den Bedarf des Aufnahmegeräts – die Reihenfolge der Geräte ändert nichts."""
    m33 = {"id": 5, "name": "M33 Dreiecksgalaxie", "kind": "galaxy", "ra": 23.46, "dec": 30.66, "obj_id": "M33",
           "exposure": 3 * 3600, "device": "stellina"}
    opts = planner.device_options(None)

    def run(devs):
        w = planner.clean_wishes({"nights": 1, "devices": devs, "mode": "deep", "watch": ["5"]}, opts)
        d = [next(o for o in opts if o["id"] == x) for x in w["devices"]]
        return planner.plan([m33], lambda n: SITE, w, d, tmp_path, now=EVENING)
    a, b = run(["stellina", "seestar30"]), run(["seestar30", "stellina"])
    ids = lambda p: {t["id"] for t in p["targets"]}
    assert ("5" in ids(a)) == ("5" in ids(b))
