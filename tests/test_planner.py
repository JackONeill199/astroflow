"""Observatorium, Planer: Mehrnächte-Plan für ein oder zwei Geräte (offline, erfundener Ort in Mitteleuropa)."""
import datetime as dt

import pytest
from fastapi.testclient import TestClient

from astroflow.core.config import load_settings
from astroflow.core.store import SettingsStore
from astroflow.modules.stacking import deepsky, planner, tonight
from astroflow.modules.stacking.db import DB
from astroflow.server import create_app

SITE = {"id": "s1", "name": "Erfundener Garten", "lat": 50.0, "lon": 10.0}
EVENING = dt.datetime(2026, 11, 2, 17, 0, tzinfo=dt.timezone.utc).timestamp()
M31 = {"id": 7, "name": "M31 Andromedagalaxie", "kind": "galaxy", "ra": 10.68, "dec": 41.27, "obj_id": "M31", "exposure": 3600}


@pytest.fixture(autouse=True)
def _no_astap(monkeypatch):
    monkeypatch.setenv("ASTROFLOW_DEEPSKY", "0")
    deepsky.reset()
    yield
    deepsky.reset()


def _plan(tmp_path, site=SITE, weather=False, **w):
    opts = planner.device_options(None)
    wishes = planner.clean_wishes({"nights": 3, "devices": ["stellina"], "mode": "mix", "min_hours": 3, **w}, opts)
    devs = [next(o for o in opts if o["id"] == d) for d in wishes["devices"]]
    return planner.plan([M31], lambda night: site, wishes, devs, tmp_path, now=EVENING, weather=weather)


def test_wishes_are_checked():
    opts = planner.device_options(None)
    ok = planner.clean_wishes({"nights": 99, "devices": ["stellina"], "min_hours": 0.1, "watch": ["M33"] * 30}, opts)
    assert ok["nights"] == planner.MAX_NIGHTS and ok["min_hours"] == 0.5 and ok["mode"] == "mix" and len(ok["watch"]) == 20
    for bad in ({"devices": []}, {"devices": ["unbekannt"]}, {"devices": ["stellina", "stellina"]},
                {"devices": ["stellina"], "mode": "alles"}, {"devices": ["stellina"], "nights": "x"}):
        with pytest.raises(ValueError):
            planner.clean_wishes(bad, opts)
    lib = planner.device_options({"id": "camera", "name": "Eigene Kamera", "fov": [2, 1.5], "alt_az": False, "mag_limit": 12})
    assert lib[0]["id"] == "library" and lib[0]["name"] == "Eigene Kamera"


def test_plan_reaches_goals_in_the_dark(tmp_path):
    p = _plan(tmp_path, watch=["M33"])
    assert len(p["nights"]) == 3 and [n["night"] for n in p["nights"]] == ["2026-11-02", "2026-11-03", "2026-11-04"]
    for n in p["nights"]:
        assert n["blocks"] and n["weather"] == "none"
        dark = n["dark"]
        for b in n["blocks"]:
            assert any(a - 1 <= b["start"] < b["end"] <= e + 601 for a, e in dark)          # nur in astronomischer Dunkelheit
        starts = sorted((b["start"], b["end"]) for b in n["blocks"])
        assert all(s2 >= e1 for (_, e1), (s2, _) in zip(starts, starts[1:]))             # ein Gerät: nichts überlappt
    names = [t["name"] for t in p["targets"]]
    assert any(n.startswith("M33") for n in names)                                       # Merkliste kommt in den Plan
    assert p["nights"][0]["blocks"][0]["target"] == "M33" or "M33" in {b["target"] for b in p["nights"][0]["blocks"]}
    assert all(t["done"] for t in p["targets"])                                          # Abschnitte ≥ Mindestbelichtung
    assert not any(n in ("M32", "M110") for n in (t["id"] for t in p["targets"]))        # Begleiter von M31 sind nicht „neu“


def test_two_devices_never_share_a_target_at_once(tmp_path):
    p = _plan(tmp_path, devices=["stellina", "vespera"], nights=2)
    for n in p["nights"]:
        by = {}
        for b in n["blocks"]:
            for o in by.get(b["target"], []):
                assert o["device"] == b["device"] or o["end"] <= b["start"] or b["end"] <= o["start"]
            by.setdefault(b["target"], []).append(b)
        assert {b["device"] for b in n["blocks"]} == {"stellina", "vespera"}


def test_mode_new_or_deep(tmp_path):
    deep = _plan(tmp_path, mode="deep", nights=1)
    own = [t for t in deep["targets"] if t["category"] == "more"]
    assert own and own[0]["href"] == "#/p/7" and "7" in {b["target"] for b in deep["nights"][0]["blocks"]}
    new = _plan(tmp_path, mode="new", nights=1)
    assert all(t["category"] == "new" for t in new["targets"][:2])


def test_clouds_move_targets_to_later_nights(tmp_path, monkeypatch):
    """Erste Nacht laut Vorhersage dicht: dort bleibt alles frei, die Ziele wandern in die nächsten Nächte."""
    first_end = EVENING + 20 * 3600
    hours = [{"t": EVENING - 3600 + h * 3600, "cloud": 100 if EVENING - 3600 + h * 3600 < first_end else 0} for h in range(5 * 24)]
    monkeypatch.setattr(tonight, "_forecast", lambda *a, **k: {"hours": hours, "later": []})
    p = _plan(tmp_path, weather=True, watch=["M33"])
    assert p["nights"][0]["blocks"] == [] and p["nights"][0]["cloud"] == 100 and p["nights"][0]["weather"] == "ok"
    assert p["nights"][1]["blocks"] and p["nights"][1]["cloud"] == 0
    assert "M33" in {b["target"] for b in p["nights"][1]["blocks"]}


def test_horizon_and_missing_site(tmp_path):
    boxed = _plan(tmp_path, site={**SITE, "horizon": [90.0] * 36})
    assert all(not n["blocks"] for n in boxed["nights"]) and boxed["targets"] == []
    assert "Standort" in planner.plan([M31], lambda night: None, planner.clean_wishes({"devices": ["stellina"]}, planner.device_options(None)),
                                      [planner.device_options(None)[0]], tmp_path, now=EVENING)["error"]


def test_plan_api_store_and_delete(tmp_path):
    s = load_settings(tmp_path / "d")
    s.auto = False
    SettingsStore(s.data_dir / "settings.json").update("sites", {"list": [{**SITE, "alt_m": 0, "accuracy_m": 2000}], "default": "s1", "periods": []})
    with TestClient(create_app(s)) as c:
        DB(s.db_path)
        d = c.get("/api/stacking/plan").json()
        assert d["plan"] is None and d["has_site"] and any(o["id"] == "vespera" for o in d["devices"])
        assert d["eq_detected"] == d["eq_default"] == [] and next(o for o in d["devices"] if o["id"] == "seestar")["eq_mode"]
        assert c.post("/api/stacking/plan", json={"devices": ["gibtsnicht"]}).status_code == 400
        r = c.post("/api/stacking/plan", json={"nights": 2, "devices": ["stellina"], "mode": "new", "min_hours": 2})
        assert r.status_code == 200 and len(r.json()["plan"]["nights"]) == 2
        assert c.get("/api/stacking/plan").json()["plan"]["wishes"]["min_hours"] == 2
        assert c.delete("/api/stacking/plan").json() == {"plan": None}
        assert c.get("/api/stacking/plan").json()["plan"] is None


def test_comets_are_not_planned(tmp_path):
    """Kometen-Projekte haben nur die Koordinaten einer Nacht – sie wandern und werden nicht eingeplant."""
    opts = planner.device_options(None)
    wishes = planner.clean_wishes({"nights": 1, "devices": ["stellina"], "mode": "deep", "min_hours": 2}, opts)
    comets = [{**M31, "id": 8, "name": "C/2022 E3 (ZTF)", "kind": "galaxy", "obj_id": None},
              {**M31, "id": 9, "name": "Komet", "kind": "comet", "obj_id": None}]
    p = planner.plan(comets, lambda night: SITE, wishes, [opts[0]], tmp_path, now=EVENING)
    assert not {"8", "9"} & {t["id"] for t in p["targets"]}


def test_calendar_export(server, browser):
    """Plan als .ics (im Browser erzeugt): ein Termin je Block, Zeiten in UTC, Sonderzeichen maskiert."""
    base, _ = server
    page = browser.new_page()
    page.goto(f"{base}/#/observatorium/plan")
    page.wait_for_function("() => !!(window.AFplan && window.AFplan.ics)")
    out = page.evaluate("""() => window.AFplan.ics({created: 1793660000, devices: [{id: 'stellina', name: 'Vaonis Stellina'}],
        targets: [{id: 'M33', name: 'M33, Dreiecksgalaxie'}],
        nights: [{night: '2026-11-02', blocks: [{device: 'stellina', target: 'M33', start: 1793660400, end: 1793671200, hours: 3, max_alt: 61}]}]})""")
    page.close()
    lines = out.split("\r\n")
    assert out.count("BEGIN:VEVENT") == 1 and "BEGIN:VEVENT" in lines and "END:VEVENT" in lines       # je Feld eine eigene Zeile
    assert all("," not in ln.split(":", 1)[0] for ln in lines if ln)                                    # nie mehrere Felder in einer Zeile
    assert "DTSTART:20261102T" in out and r"SUMMARY:M33\, Dreiecksgalaxie · Vaonis Stellina" in out


def test_targets_from_later_sites_and_usable_second_choice(tmp_path):
    """Ortswechsel im Zeitraum: ein Ziel, das nur am späteren (südlicheren) Ort hoch genug kommt, bleibt Kandidat.
    Mindestnutzen wird vor der Auswahl geprüft: ein Abschnitt bleibt nicht leer, nur weil das bestbewertete Ziel ihn kaum nutzt."""
    south = {**SITE, "id": "s2", "name": "Erfundener Süden", "lat": 30.0}
    sites_by_night = lambda night: south if night and night >= "2026-11-03" else SITE
    opts = planner.device_options(None)
    low = {**M31, "id": 11, "name": "Tiefes Ziel", "ra": 30.0, "dec": -28.0, "obj_id": None}
    wishes = planner.clean_wishes({"nights": 2, "devices": ["stellina"], "mode": "deep", "min_hours": 2}, opts)
    p = planner.plan([low], sites_by_night, wishes, [opts[0]], tmp_path, now=EVENING)
    assert "11" in {b["target"] for b in p["nights"][1]["blocks"]}                 # am südlichen Ort eingeplant
    assert "11" not in {b["target"] for b in p["nights"][0]["blocks"]}             # am nördlichen nie über 30°
    assert p["nights"][1]["site"]["id"] == "s2"
    # Vor der Korrektur verwarf ein knapp unbrauchbares Spitzenziel den ganzen Abschnitt; jetzt bleibt keine dunkle Nacht leer
    q = planner.plan([M31], lambda night: SITE, planner.clean_wishes({"nights": 1, "devices": ["stellina"], "watch": ["M33"]}, opts),
                     [opts[0]], tmp_path, now=EVENING)
    used = sum(b["end"] - b["start"] for b in q["nights"][0]["blocks"]) / 3600
    assert used > 0.8 * q["nights"][0]["dark_hours"]


def test_eq_mode_per_device_removes_rotation_penalty(tmp_path):
    """EQ-Modus nur für Geräte, die ihn können (Seestar ja, Stellina nein); im EQ-Modus zählen hohe Ziele voll."""
    opts = planner.device_options(None)
    w = planner.clean_wishes({"nights": 1, "devices": ["seestar", "stellina"], "eq": ["seestar", "stellina"], "min_hours": 3}, opts)
    assert w["eq"] == ["seestar"]
    zen = {**M31, "dec": 50.0, "exposure": 600}                          # steht am erfundenen Ort (50° N) im Zenit

    def hours(eq):
        wi = planner.clean_wishes({"nights": 1, "devices": ["seestar"], "eq": eq, "min_hours": 8, "watch": ["7"]}, opts)
        devs = [next(o for o in opts if o["id"] == d) for d in wi["devices"]]
        p = planner.plan([zen], lambda night: SITE, wi, devs, tmp_path, now=EVENING)
        return p, next((t["hours"] for t in p["targets"] if t["id"] == "7"), 0)
    (alt, h_alt), (eq, h_eq) = hours([]), hours(["seestar"])
    assert h_eq > h_alt and eq["devices"][0]["eq"] and not alt["devices"][0]["eq"]
    assert "EQ" in eq["devices"][0]["tracking"] and "Software" in alt["devices"][0]["tracking"]
