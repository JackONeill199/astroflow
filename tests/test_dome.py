"""Observatorium, Bahnen im Atlas „Von hier“: Bahnen, freie Fenster, Plan-Blöcke und Mond einer Nacht (offline, erfundener Ort)."""
import datetime as dt

import pytest
from fastapi.testclient import TestClient

from astroflow.core.config import load_settings
from astroflow.core.store import SettingsStore
from astroflow.modules.stacking import deepsky, dome
from astroflow.modules.stacking.db import DB
from astroflow.server import create_app

SITE = {"id": "s1", "name": "Erfundener Garten", "lat": 50.0, "lon": 10.0}
EVENING = dt.datetime(2026, 11, 2, 17, 0, tzinfo=dt.timezone.utc).timestamp()
M31 = {"id": 7, "name": "M31 Andromedagalaxie", "kind": "galaxy", "ra": 10.68, "dec": 41.27}
M42 = {"id": 8, "name": "M42 Orionnebel", "kind": "nebula", "ra": 83.82, "dec": -5.39}
SOUTH = {"id": 9, "name": "Omega Centauri", "kind": "cluster", "ra": 201.7, "dec": -47.5}
COMET = {"id": 10, "name": "C/2023 A3 (Tsuchinshan-ATLAS)", "kind": "generic", "ra": 210.0, "dec": 5.0}


@pytest.fixture(autouse=True)
def _no_astap(monkeypatch):
    monkeypatch.setenv("ASTROFLOW_DEEPSKY", "0")
    deepsky.reset()
    yield
    deepsky.reset()


def _by(out):
    return {t["name"]: t for t in out["targets"]}


def test_tracks_free_windows_and_reasons():
    out = dome.dome([M31, M42, SOUTH, COMET], lambda night: SITE, None, now=EVENING)
    assert out["night"] == "2026-11-02" and out["k"] == 0 and out["dark"] and out["min_alt"] == 30
    assert "lat" not in out["site"] and "lon" not in out["site"]                     # Standort bleibt auf dem Rechner
    t = _by(out)
    assert set(t) == {"M31 Andromedagalaxie", "M42 Orionnebel", "Omega Centauri"}       # Komet wandert: nicht dabei
    m31, south = t["M31 Andromedagalaxie"], t["Omega Centauri"]
    assert len(m31["alt"]) == len(m31["az"]) == out["n"] == len(out["sun"]) == len(out["moon"]["alt"])
    assert m31["free"] and m31["free_hours"] > 5 and m31["max_alt"] > 80 and m31["why"] is None
    # frei nur in der Dunkelheit und über 30°
    i0 = round((m31["free"][0][0] - out["t0"]) / out["step"])
    assert m31["alt"][i0] >= 30 and out["sun"][i0] <= -18
    assert not south["free"] and south["why"] == "bleibt unter dem Horizont"
    assert 0 <= out["moon"]["illum"] <= 100


def test_horizon_mask_blocks_and_explains():
    north_wall = {**SITE, "horizon": [90.0] * 9 + [0.0] * 18 + [90.0] * 9}        # Haus im Norden: nur der Süden frei
    out = dome.dome([M31, M42], lambda night: north_wall, None, now=EVENING)
    t = _by(out)
    free = dome.dome([M31, M42], lambda night: SITE, None, now=EVENING)
    assert t["M31 Andromedagalaxie"]["free_hours"] < _by(free)["M31 Andromedagalaxie"]["free_hours"]
    assert out["site"]["horizon"] == north_wall["horizon"]
    boxed = dome.dome([M31], lambda night: {**SITE, "horizon": [90.0] * 36}, None, now=EVENING)
    assert _by(boxed)["M31 Andromedagalaxie"]["why"] == "hinter der Horizontmaske"


def test_plan_targets_first_with_blocks_of_that_night():
    blk = {"target": "ngc7000", "device": "stellina", "start": EVENING + 3 * 3600, "end": EVENING + 5 * 3600}
    plan = {"targets": [{"id": "ngc7000", "name": "NGC 7000 Nordamerikanebel", "ra": 314.7, "dec": 44.5, "href": None}],
            "nights": [{"night": "2026-11-02", "blocks": [blk]}, {"night": "2026-11-03", "blocks": []}],
            "devices": [{"id": "stellina", "name": "Vaonis Stellina", "fov": [1.0, 0.7]}]}
    out = dome.dome([M31], lambda night: SITE, plan, now=EVENING)
    assert [t["source"] for t in out["targets"]] == ["plan", "own"]
    assert out["targets"][0]["blocks"] == [{"start": blk["start"], "end": blk["end"], "device": "stellina"}]
    assert out["devices"] == [{"id": "stellina", "name": "Vaonis Stellina"}]                # Ablauf: welches Teleskop wann
    nxt = dome.dome([M31], lambda night: SITE, plan, now=EVENING, date="2026-11-03")
    assert nxt["night"] == "2026-11-03" and nxt["k"] == 1 and nxt["targets"][0]["blocks"] == []


def test_missing_site_and_api(tmp_path):
    assert "Standort" in dome.dome([M31], lambda night: None, None, now=EVENING)["error"]
    s = load_settings(tmp_path / "d")
    s.auto = False
    with TestClient(create_app(s)) as c:
        DB(s.db_path)
        assert "Standort" in c.get("/api/stacking/dome").json()["error"]
    s2 = load_settings(tmp_path / "e")
    s2.auto = False
    SettingsStore(s2.data_dir / "settings.json").update("sites", {"list": [{**SITE, "alt_m": 0, "accuracy_m": 2000}], "default": "s1", "periods": []})
    with TestClient(create_app(s2)) as c:
        db = DB(s2.db_path)
        db.x("INSERT INTO projects(key, name, ra, dec, created) VALUES('m31','M31',10.68,41.27,0)")
        d = c.get("/api/stacking/dome?night=2").json()
        assert d["k"] == 2 and d["site"]["name"] == SITE["name"] and [t["name"] for t in d["targets"]] == ["M31"]
        assert c.get("/api/stacking/dome?night=99").json()["k"] == 13


def test_tracks_layer_in_atlas(server, browser):
    """Atlas „Von hier“ mit ?blick=hier: Ebene „Bahnen“ sichtbar, Listen unter der Karte, Klick → Kontextpanel; Himmel ↔ zurück."""
    base, _ = server
    page = browser.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(f"{base}/#/observatorium?blick=hier")
    page.wait_for_selector("#obsFree:not([hidden]) .obs-fi", timeout=30000)
    assert page.locator('[data-layer="tracks"]:not([hidden])').count() == 1
    assert page.locator('[data-mode="here"].on').count() == 1
    page.locator("#obsFree .obs-fi").first.click()
    page.wait_for_selector("#obsFree .obs-fi.on")
    page.click('[data-layer="tracks"]')                          # Ebene aus → Listen weg
    page.wait_for_selector("#obsFree", state="hidden")
    page.click('[data-mode="sky"]')
    page.wait_for_timeout(300)
    assert page.locator('[data-layer="tracks"]').is_hidden()
    assert not errors
    page.close()


def test_tracks_now_button_and_date_change(server, browser):
    """„Jetzt“ stellt auch den Regler auf jetzt (Codex); anderes Datum bei offenem Atlas lädt die Bahnen dieser Nacht neu."""
    import re
    base, _ = server
    page = browser.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(f"{base}/#/observatorium?blick=hier")
    page.wait_for_selector("#obsFree:not([hidden]) .obs-fi", timeout=30000)
    page.click("#obsNow")
    page.wait_for_timeout(500)
    minutes = lambda: (lambda h, m: int(h) * 60 + int(m))(*re.search(r"(\d\d):(\d\d)", page.text_content("#obsTL")).groups())
    a = minutes()
    page.eval_on_selector("#obsT", "e => e.dispatchEvent(new Event('input', {bubbles: true}))")   # Regler unverändert „anfassen“
    page.wait_for_timeout(500)
    # Regler rastet in 10-min-Schritten ab t0 (vorher: Stunden daneben): ≤ 5 min Abstand, dazu bis 1 min, weil die Anzeige
    # bei „jetzt“ und beim Rasterpunkt die Sekunden abschneidet (CI scheiterte je nach Uhrzeit mit 6)
    assert abs(minutes() - a) <= 6
    nxt = page.evaluate("async () => (await (await fetch('/api/stacking/dome?night=1')).json()).night")
    page.evaluate(f"location.hash = '#/observatorium?blick=hier&date={nxt}'")
    page.wait_for_function("() => /in der Nacht/.test(document.querySelector('#obsFree h2')?.textContent || '')", timeout=30000)
    page.evaluate("location.hash = '#/observatorium?blick=hier'")
    page.wait_for_function("() => /heute Nacht/.test(document.querySelector('#obsFree h2')?.textContent || '')", timeout=30000)
    assert not errors
    page.close()
