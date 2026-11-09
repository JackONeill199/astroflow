"""Heute Nacht: Dämmerung, Zielkurven, Ablauf und Vorschläge (offline, erfundener Ort in Mitteleuropa)."""
import datetime as dt
import json

import numpy as np
import pytest

from astroflow.modules.stacking import deepsky, tonight

STELLINA = {"id": "stellina", "name": "Testgerät", "alt_az": True, "fov": [1, .7], "source": "Test", "mag_limit": 11.5}
CAMERA = {**STELLINA, "id": "camera", "alt_az": False}
SITE = {"lat": 50.0, "lon": 10.0, "weather": False}
EVENING = dt.datetime(2026, 11, 2, 19, 0, tzinfo=dt.timezone.utc).timestamp()
M31 = {"id": 7, "name": "M31 Andromedagalaxie", "kind": "galaxy", "ra": 10.68, "dec": 41.27, "obj_id": "M31", "exposure": 3600}


@pytest.fixture(autouse=True)
def _no_astap(monkeypatch):
    """Ohne ASTAP-Datei des Rechners: nur die fest eingebauten Kataloge (Tests mit Attrappe schalten sie gezielt ein)."""
    monkeypatch.setenv("ASTROFLOW_DEEPSKY", "0")
    deepsky.reset()
    tonight._cache.clear()
    yield
    deepsky.reset()
    tonight._cache.clear()


def test_without_site_only_says_so(tmp_path):
    assert tonight.plan([M31], {"lat": None, "lon": None}, tmp_path) == {"site": None}


def test_night_plan(tmp_path):
    r = tonight.plan([M31], SITE, tmp_path, EVENING)
    tw = r["twilight"]
    assert tw["sunset"] < tw["civil"] < tw["nautical"] < tw["dark_start"] < tw["dark_end"] < tw["nautical_end"] < tw["sunrise"]
    assert 9 < r["dark_hours"] < 13                                   # Anfang November, 50° Nord
    assert len(r["times"]) == len(r["moon"]["alt"]) == len(r["targets"][0]["alt"])
    m31 = r["targets"][0]
    assert m31["id"] == 7 and m31["max_alt"] > 75 and m31["good_hours"] > 6
    assert all(tw["dark_start"] - 1 <= b["start"] < b["end"] <= tw["dark_end"] + 700 for b in r["plan"])
    assert r["plan"]
    names = [s["name"] for s in r["suggestions"]]
    assert names and not any(n.startswith("M31") for n in names)    # schon fotografiert → kein Vorschlag
    assert all(s["good_hours"] >= 2 for s in r["suggestions"])
    assert r["weather"] is None and not r["weather_on"]


def test_after_midnight_still_same_night(tmp_path):
    late = EVENING + 7 * 3600                                         # 02:00 UTC am nächsten Morgen
    assert tonight.plan([M31], SITE, tmp_path, late)["start"] == tonight.plan([M31], SITE, tmp_path, EVENING)["start"]


def test_recommendations_use_library_quality_and_field(tmp_path):
    poor = {**M31, 'exposure': 1800}
    d = tonight.plan([poor], SITE, tmp_path, EVENING, {**STELLINA, 'name': 'Testgerät'})
    own = next(x for x in d['recommendations'] if x['category'] == 'more')
    assert own['id'] == 7 and own['href'] == '#/p/7' and own['reasons'] and own['gain']
    new = [x for x in d['recommendations'] if x['category'] == 'new']
    assert new and all(x['size'] <= .7 * 60 * .9 for x in new)
    assert len(d['recommendations']) <= 6
    assert all(len(x['alt']) == len(d['times']) for x in d['recommendations'])
    # Schlechte Farbe/ausgebrannte Kerne sind kein Grund für mehr Belichtungszeit.
    assert not tonight._need({'exposure': 14400, 'stats': {'quality': {'score': 2, 'issues': [{'code': 'cast'}]}}})
    assert tonight._need({'stats': {'quality': {'issues': [{'code': 'noisy'}]}}})
    assert tonight._need({'stats': {'tutor': {'findings': [{'code': 'faint', 'level': 'warn'}]}}})


def test_only_future_visibility_counts_and_cache_refreshes_quality(tmp_path):
    early = tonight.plan([{**M31, 'exposure': 1800}], SITE, tmp_path, EVENING)
    late = tonight.plan([{**M31, 'exposure': 1800}], SITE, tmp_path, EVENING + 8 * 3600)
    assert all(start >= EVENING + 8 * 3600 for r in late['recommendations'] for start, end in r['good'])
    assert late['targets'][0]['good_hours'] < early['targets'][0]['good_hours']
    good = tonight.plan([M31], SITE, tmp_path, EVENING)
    assert not any(x['category'] == 'more' for x in good['recommendations'])
    noisy = {**M31, 'stats': {'quality': {'issues': [{'code': 'noisy'}]}}}
    refreshed = tonight.plan([noisy], SITE, tmp_path, EVENING)
    assert any(x['category'] == 'more' for x in refreshed['recommendations'])


def test_known_alias_without_coordinates_is_not_recommended_as_new(tmp_path):
    d = tonight.plan([{**M31, 'name': 'NGC 224', 'obj_id': None, 'ra': None, 'dec': None}], SITE, tmp_path, EVENING)
    assert all(s['id'] != 'M31' for s in d['suggestions'] + d['recommendations'])


@pytest.mark.parametrize('lat,month', [(80, 6), (80, 12), (-80, 6), (-80, 12)])
def test_polar_nights_and_days(tmp_path, lat, month):
    now = dt.datetime(2026, month, 21, 19, tzinfo=dt.timezone.utc).timestamp()
    r = tonight.plan([], {**SITE, 'lat': lat}, tmp_path, now)
    assert r['times'] and len(r['times']) == len(r['sun']) == len(r['moon']['alt'])
    if (lat > 0) == (month == 6):
        assert r['dark_hours'] == 0 and not r['recommendations']


def test_weather_failure_leaves_offline_recommendations(tmp_path, monkeypatch):
    def fail(*args):
        raise OSError('offline')
    monkeypatch.setattr(tonight, '_forecast', fail)
    r = tonight.plan([], {**SITE, 'weather': True}, tmp_path, EVENING)
    assert r['weather'] is None and r['weather_error'] and r['recommendations']


def test_api_uses_latest_result_not_sum_and_detects_device(tmp_path):
    from fastapi.testclient import TestClient
    from astroflow.core.config import load_settings
    from astroflow.core.store import SettingsStore
    from astroflow.modules.stacking.db import DB
    from astroflow.server import create_app
    s = load_settings(tmp_path)
    db = DB(s.db_path)
    pid = db.x("INSERT INTO projects(name,obj_id,options) VALUES('NGC 224','M31','{}')")
    for exp in (1800, 7200):
        db.x('INSERT INTO results(project_id,exposure,stats) VALUES(?,?,?)', (pid, exp, '{}'))
    info = {'instrument': 'Stellina', 'shape': [2080, 3072]}
    db.x("INSERT INTO frames(id,project_id,kind,info,night) VALUES('f',?,'light',?,'2026-11-01')", (pid, json.dumps(info)))
    rows, device = tonight.library(db)
    assert rows[0]['exposure'] == 7200 and abs(rows[0]['ra'] - M31['ra']) < .01
    assert device['fov'] == [1, .7] and device['id'] == 'stellina' and device['alt_az']
    info['solve'] = {'fov_w': 2, 'fov_h': 1.5}
    db.set_frame('f', info=info)
    assert tonight.library(db)[1]['fov'] == [2, 1.5]
    with TestClient(create_app(s)) as client:
        assert client.get('/api/stacking/tonight').json()['site'] is None
        SettingsStore(s.data_dir / 'settings.json').update('tutor', SITE)
        r = client.get('/api/stacking/tonight')
        assert r.status_code == 200 and r.json()['device']['fov'] == [2, 1.5]
        gone = r.json()['suggestions'][0]['id']                                   # ausgeblendete Ziele kommen nicht mehr
        again = client.get('/api/stacking/tonight', params={'hide': f'{gone},'}).json()
        assert gone not in {x['id'] for x in again['suggestions']} and again['hidden'] == [gone]


# ---------------------------------------------------------------- Katalog (seit 0.20.0) #
def test_builtin_catalog_has_messier_and_caldwell_without_astap():
    t = deepsky.fixed()
    assert {x.number for x in t if x.source == "messier"} >= set(range(1, 111)) - {24, 40, 73}      # Sternwolke/Doppelstern/Asterismus fehlen bewusst
    assert {x.number for x in t if x.source == "caldwell"} >= {1, 14, 20, 27, 31, 49, 63, 65, 77, 106}
    keys = [k for x in t for k in x.keys]
    assert len(keys) == len(set(keys))                                  # keine doppelte Kennung zwischen Katalogeinträgen
    assert deepsky.keys_for("NGC 224") >= {"M31", "NGC224"} and deepsky.keys_for("Messier 31 Andromeda") >= {"M31"}
    assert "C20" in deepsky.keys_for("NGC 7000") and "C49" in deepsky.keys_for("NGC2244")


def test_filters_follow_device_and_field():
    t = {x.id: x for x in deepsky.fixed()}
    assert deepsky.usable(t["M57"], STELLINA, [1, .7])                  # 3,8′ von 42′: passt (OpenNGC allein gäbe nur die Kernfigur, 1,27′)
    assert not deepsky.usable(t["M31"], STELLINA, [1, .7])              # 178′ passt nicht ins Bild
    assert not deepsky.usable(t["C6"], STELLINA, [1, .7])               # Katzenauge 0,4′ = Pünktchen
    assert not deepsky.usable(t["C24"], STELLINA, [1, .7])              # 11,9 mag: über der Grenze
    assert deepsky.usable(t["M76"], {"id": "stellina"}, None) and not deepsky.usable(t["M76"], {"id": "dwarf"}, None)   # 10,1 mag
    assert deepsky.mag_limit({"id": "vespera"}) < deepsky.mag_limit({"id": "stellina"})


def test_recommendations_come_from_big_catalog_and_never_repeat_own_objects(tmp_path):
    d = tonight.plan([M31], SITE, tmp_path, EVENING, STELLINA)
    new = [x for x in d["recommendations"] if x["category"] == "new"]
    assert len(new) >= 3 and d["catalog"]["messier"] > 30
    assert not {s["id"] for s in d["suggestions"]} & {"M31", "M32", "M110"}     # M31 gehört mir, Begleiter im selben Feld auch nicht
    assert all(("Caldwell" in x["reasons"][0] or "Messier" in x["reasons"][0]) for x in new)
    d2 = tonight.plan([{**M31, "name": "NGC 224", "obj_id": None, "ra": None, "dec": None}], SITE, tmp_path, EVENING, STELLINA)
    assert "M31" not in {s["id"] for s in d2["suggestions"]}            # Alias ohne Koordinaten


def test_astap_csv_adds_named_and_large_objects_but_not_duplicates(tmp_path, monkeypatch):
    csv = tmp_path / "deep_sky.csv"
    csv.write_text("x\nRA,DEC,name,length\n"
                   "25643,148568,M31/NGC224/Andromeda_Galaxy,1891,617,35\n"           # Messier: nicht doppelt
                   "26000,100000,NGC999/Sh2-1/Test_Nebula,300\n"                           # benannt, 30′ → Kandidat
                   "27000,90000,NGC998/PGC222,60\n"                                  # unbenannte Galaxie 6′ → nein
                   "28000,95000,NGC997/PGC111,100\n", encoding="utf-8")              # unbenannte Galaxie 10′ → ja
    monkeypatch.setenv("ASTROFLOW_DEEPSKY", str(csv))
    deepsky.reset()
    ex = {x.id: x for x in deepsky.astap_extras()}
    assert "NGC999" in ex and ex["NGC999"].mag is None and ex["NGC999"].source == "astap"
    assert "NGC997" in ex and "NGC224" not in ex and "NGC998" not in ex
    tonight._cache.clear()
    d = tonight.plan([], SITE, tmp_path, EVENING, STELLINA)
    assert d["catalog"]["astap_found"] and d["catalog"]["astap"] >= 1


# ---------------------------------------------------------------- Rangfolge #
def test_mix_of_kinds_and_categories(tmp_path):
    poor = {**M31, "exposure": 1800}
    recs = tonight.plan([poor], SITE, tmp_path, EVENING, CAMERA)["recommendations"]
    kinds = [x["kind"] for x in recs]
    assert all(kinds.count(k) <= 2 for k in set(kinds)) and len(set(kinds)) >= 3
    assert {x["category"] for x in recs} == {"more", "new"}
    assert [x["rank"] for x in recs] == sorted((x["rank"] for x in recs), reverse=True)


def test_zenith_penalty_only_for_alt_az(tmp_path):
    zen = {**M31, "dec": 50.0, "exposure": 600}                          # steht bei 50° Breite im Zenit
    alt_az = tonight.plan([zen], SITE, tmp_path, EVENING, STELLINA)["targets"][0]
    eq = tonight.plan([zen], SITE, tmp_path, EVENING, CAMERA)["targets"][0]
    assert alt_az["zenith_hours"] > 0 and alt_az["eff_hours"] < eq["eff_hours"]
    assert eq["zenith_hours"] == 0


def test_full_moon_lowers_effective_hours(tmp_path):
    full = dt.datetime(2026, 11, 24, 19, tzinfo=dt.timezone.utc).timestamp()      # Vollmond-nah
    new = dt.datetime(2026, 11, 9, 19, tzinfo=dt.timezone.utc).timestamp()        # Neumond-nah
    a, b = (tonight.plan([M31], SITE, tmp_path, t, CAMERA) for t in (full, new))
    assert a["moon"]["illum"] > 80 > 20 > b["moon"]["illum"]
    ra, rb = a["targets"][0], b["targets"][0]
    assert ra["eff_hours"] / ra["good_hours"] < rb["eff_hours"] / rb["good_hours"] <= 1.0


# ---------------------------------------------------------------- Mehr Zeit #
def test_gain_uses_sqrt_rule():
    g = tonight._gain(37 * 60, 5)
    assert g["noise_pct"] == 67 and g["add"] == 5 * 3600 and "37 min" in g["text"] and "5 h" in g["text"] and "−67 %" in g["text"]
    assert tonight._gain(4 * 3600, 4)["noise_pct"] == 29                # doppelte Zeit = −29 %
    assert tonight._gain(None, 5) is None and tonight._gain(600, 0) is None
    assert tonight._dur(5400) == "1,5 h" and tonight._dur(1800) == "30 min"


def test_more_time_skips_when_gain_is_tiny(tmp_path):
    big = {**M31, "exposure": 40 * 3600, "stats": {"quality": {"issues": [{"code": "noisy"}]}}}
    d = tonight.plan([big], SITE, tmp_path, EVENING, STELLINA)
    assert not any(x["category"] == "more" for x in d["recommendations"])      # +8 h bei 40 h ≈ −9 %
    d = tonight.plan([{**big, "exposure": 4 * 3600}], SITE, tmp_path, EVENING, STELLINA)
    own = next(x for x in d["recommendations"] if x["category"] == "more")
    assert own["gain"]["noise_pct"] >= 25 and own["benefit"] == own["gain"]["text"]


# ---------------------------------------------------------------- Saison #
def test_season_last_chance_and_soon():
    t0 = dt.datetime(2026, 11, 2, 12, tzinfo=dt.timezone.utc).timestamp()
    last = tonight._season(np.array([5, 4, 3, 2, 1, 0, 0, 0, 1, 2, 3, 4, 5, 5, 5]), t0, 50.0)
    assert last["kind"] == "last" and "Letzte Gelegenheit bis zum" in last["text"] and last["from"]
    soon = tonight._season(np.array([0, 1, 3, 4, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5]), t0, 50.0)
    assert soon["kind"] == "soon" and soon["text"].startswith("Bald gut sichtbar")
    assert tonight._season(np.array([6] * 15), t0, 50.0) is None
    assert tonight._season_word(3, 50.0) == "Frühjahr" and tonight._season_word(3, -34.0) == "Herbst"


def test_plan_marks_last_chance_and_soon_objects(tmp_path):
    d = tonight.plan([], SITE, tmp_path, EVENING, CAMERA)
    assert d["soon"] and all(s["season"]["kind"] == "soon" for s in d["soon"])
    may = dt.datetime(2026, 5, 10, 19, tzinfo=dt.timezone.utc).timestamp()          # Winterobjekte gehen aus dem Dunkel
    d = tonight.plan([], SITE, tmp_path, may, CAMERA)
    assert any(x.get("season", {}) and x["season"]["kind"] == "last" for x in d["suggestions"])


# ---------------------------------------------------------------- Ablauf, Wetter, Ausblenden #
def test_night_plan_is_sequential_with_several_targets(tmp_path):
    d = tonight.plan([], SITE, tmp_path, EVENING, STELLINA)
    plan = d["plan"]
    assert len(plan) >= 2
    assert all(a["end"] <= b["start"] for a, b in zip(plan, plan[1:]))        # nie zwei Ziele gleichzeitig
    assert all(b["end"] - b["start"] >= tonight.MIN_BLOCK for b in plan)
    assert len({b["id"] for b in plan}) == len(plan)                          # jedes Ziel höchstens einmal
    assert all(d["twilight"]["dark_start"] - 1 <= b["start"] and b["end"] <= d["twilight"]["dark_end"] + 700 for b in plan)


def test_weather_adds_cloud_curve_and_prefers_clear_hours(tmp_path, monkeypatch):
    base = tonight.plan([], SITE, tmp_path, EVENING, STELLINA)
    t0 = base["start"]
    hours = [{"t": t0 + 3600 * i, "cloud": 100 if i < 6 else 0, "humidity": 50, "dew_spread": 5} for i in range(26)]    # die ersten 6 Stunden zu
    monkeypatch.setattr(tonight, "_forecast", lambda *a, **k: {"hours": hours, "source": "Test"})
    tonight._cache.clear()
    d = tonight.plan([], {**SITE, "weather": True}, tmp_path, EVENING, STELLINA)
    assert d["cloud"] and len(d["cloud"]) == len(d["times"]) and max(d["cloud"]) == 100
    assert sum(x["eff_hours"] for x in d["suggestions"]) < sum(x["eff_hours"] for x in base["suggestions"])
    assert d["plan"] and all(b["cloud"] is not None for b in d["plan"])
    first_clear = d["plan"][0]
    assert first_clear["start"] >= t0 + 5 * 3600                              # zugezogene Stunden bekommen kein Ziel


def test_hidden_targets_are_replaced(tmp_path):
    d = tonight.plan([], SITE, tmp_path, EVENING, STELLINA)
    gone = d["recommendations"][0]["id"]
    d2 = tonight.plan([], SITE, tmp_path, EVENING, STELLINA, hidden={gone})
    assert gone not in {x["id"] for x in d2["recommendations"] + d2["suggestions"]}
    assert len(d2["recommendations"]) == len(d["recommendations"]) and d2["hidden"] == [gone]


def test_collection_service_adds_messier_count(tmp_path):
    d = tonight.plan([], SITE, tmp_path, EVENING, STELLINA, collection={"messier": [1, 2, 3, 4, 5]})
    m = next(x for x in d["suggestions"] if x["source"] == "messier")
    assert any("6. Messier-Objekt (bisher 5 von 110)" in r for r in m["reasons"])
    assert not {1, 2, 3, 4, 5} & {x.number for x in deepsky.fixed() if x.id in {s["id"] for s in d["suggestions"]} and x.source == "messier"}
    d2 = tonight.plan([], SITE, tmp_path, EVENING, STELLINA)
    assert not any("Messier-Objekt (bisher" in r for x in d2["suggestions"] for r in x["reasons"])        # ohne Dienst kein Satz


def test_plan_blocks_never_span_cloudy_gaps(tmp_path, monkeypatch):
    t0 = tonight.plan([], SITE, tmp_path, EVENING, STELLINA)["start"]
    hours = [{"t": t0 + 3600 * i, "cloud": 100 if 5 <= i <= 8 else 0, "humidity": 50, "dew_spread": 5} for i in range(26)]
    monkeypatch.setattr(tonight, "_forecast", lambda *a, **k: {"hours": hours, "source": "Test"})
    tonight._cache.clear()
    d = tonight.plan([], {**SITE, "weather": True}, tmp_path, EVENING, STELLINA)
    times, cloud = d["times"], d["cloud"]
    for b in d["plan"]:
        inside = [c for t, c in zip(times, cloud) if b["start"] <= t < b["end"] and c is not None]
        assert inside and max(inside) < 80


# ---------------------------------------------------------------- Bewölkung: Meldungen und Ziele „bei klarem Himmel“ #
def _weather(monkeypatch, cloud_of, later_clear=True):
    """Vorhersage mit Bewölkung je Stunde ab Fensterbeginn; spätere Stunden (nächste Nacht) wahlweise klar."""
    t0 = tonight._window(EVENING, SITE["lon"])[0]
    hours = [{"t": t0 + 3600 * i, "cloud": cloud_of(i), "humidity": 50, "dew_spread": 5} for i in range(25)]
    later = [{"t": t0 + 3600 * i, "cloud": 0 if later_clear else 100} for i in range(26, 48)]
    monkeypatch.setattr(tonight, "_forecast", lambda *a, **k: {"hours": hours, "later": later, "source": "Test"})
    tonight._cache.clear()


def test_overcast_night_still_shows_targets_marked_for_clear_sky(tmp_path, monkeypatch):
    _weather(monkeypatch, lambda i: 100)
    d = tonight.plan([{**M31, "exposure": 1800}], {**SITE, "weather": True}, tmp_path, EVENING, STELLINA)
    assert d["sky"]["state"] == "overcast" and d["sky"]["mean"] == 100 and d["status"]["code"] == "overcast"
    assert d["recommendations"] and all(r["weather_blocked"] for r in d["recommendations"])
    assert all(any("klarem Himmel" in x for x in r["reasons"]) for r in d["recommendations"])
    assert {r["category"] for r in d["recommendations"]} == {"more", "new"}
    assert d["plan"] == []                                              # nichts einplanen, was nicht aufgenommen werden kann
    nc = d["sky"]["next_clear"]
    assert nc and not nc["tonight"] and nc["end"] - nc["start"] >= 7200      # nächste Nacht klar


def test_overcast_without_any_clear_window_says_so(tmp_path, monkeypatch):
    _weather(monkeypatch, lambda i: 100, later_clear=False)
    d = tonight.plan([], {**SITE, "weather": True}, tmp_path, EVENING, STELLINA)
    assert d["status"]["code"] == "overcast" and d["sky"]["next_clear"] is None


def test_partly_cloudy_prefers_clear_hours_and_flags_the_rest(tmp_path, monkeypatch):
    t0 = tonight._window(EVENING, SITE["lon"])[0]
    _weather(monkeypatch, lambda i: 100 if i < 14 else 0)                  # Abend und halbe Nacht bedeckt, gegen Morgen klar
    d = tonight.plan([], {**SITE, "weather": True}, tmp_path, EVENING, STELLINA)
    assert d["sky"]["state"] == "partly" and d["status"]["code"] == "partly"
    recs = d["recommendations"]
    assert recs and any(not r["weather_blocked"] for r in recs)
    assert all(b["start"] >= t0 + 13 * 3600 for b in d["plan"])
    assert all(r["rank"] < min(x["rank"] for x in recs if not x["weather_blocked"]) + 1.5 for r in recs if r["weather_blocked"])


def test_without_weather_there_is_no_sky_or_blocking(tmp_path):
    d = tonight.plan([], SITE, tmp_path, EVENING, STELLINA)
    assert d["sky"] is None and d["status"]["code"] == "ok"
    assert not any(r["weather_blocked"] for r in d["recommendations"] + d["suggestions"])


def test_status_separates_height_from_night_over(tmp_path, monkeypatch):
    monkeypatch.setattr(deepsky, "catalog", lambda: [])
    assert tonight.plan([], SITE, tmp_path, EVENING, STELLINA)["status"]["code"] == "low_alt"
    dawn = dt.datetime(2026, 11, 3, 4, 50, tzinfo=dt.timezone.utc).timestamp()             # nach Ende der Dunkelheit, noch laufende Nacht
    d = tonight.plan([], SITE, tmp_path, dawn, STELLINA)
    assert d["twilight"]["dark_end"] < dawn and d["status"]["code"] == "over"
    summer = dt.datetime(2026, 6, 21, 19, tzinfo=dt.timezone.utc).timestamp()
    assert tonight.plan([], {**SITE, "lat": 80}, tmp_path, summer, STELLINA)["status"]["code"] == "no_dark"


def test_clear_forecast_with_short_remaining_dark_is_not_overcast(tmp_path, monkeypatch):
    _weather(monkeypatch, lambda i: 0)
    dawn = tonight.plan([], SITE, tmp_path, EVENING, STELLINA)["twilight"]["dark_end"] - 1800        # 30 min Dunkelheit übrig
    d = tonight.plan([], {**SITE, "weather": True}, tmp_path, dawn, STELLINA)
    assert d["sky"]["state"] == "clear" and d["status"]["code"] != "overcast"


def test_next_clear_window_is_clipped_to_darkness(tmp_path, monkeypatch):
    base = tonight.plan([], SITE, tmp_path, EVENING, STELLINA)
    dark_end = base["twilight"]["dark_end"] + 86400                          # Ende der Dunkelheit der folgenden Nacht (±Minuten)
    t0 = tonight._window(EVENING, SITE["lon"])[0]
    _weather(monkeypatch, lambda i: 100, later_clear=False)
    later = [{"t": float(int(dark_end // 3600) * 3600 - 3600 * k), "cloud": 0} for k in range(0, 2)]       # nur die beiden Stunden vor Morgengrauen
    monkeypatch.setattr(tonight, "_forecast", lambda *a, **k: {"hours": [{"t": t0 + 3600 * i, "cloud": 100} for i in range(25)], "later": later, "source": "T"})
    tonight._cache.clear()
    d = tonight.plan([], {**SITE, "weather": True}, tmp_path, EVENING, STELLINA)
    nc = d["sky"]["next_clear"]
    assert nc is None or nc["end"] <= dark_end + 700          # nie über das Ende der Dunkelheit hinaus; < 2 h dunkel → kein Fenster


def test_katalogdaten_vollstaendig_und_plausibel():
    """katalogdaten/messier_caldwell.csv (OpenNGC + Wikipedia, CC-BY-SA 4.0): 110 Messier + 109 Caldwell, Werte plausibel, Lizenz daneben."""
    d = deepsky.DATA_FILE.parent
    kopf = deepsky.DATA_FILE.read_text(encoding="utf-8")[:1500]
    assert (d / "LICENSE-OpenNGC").is_file() and "OpenNGC" in kopf and "Wikipedia" in kopf and "CC-BY-SA" in kopf
    data = deepsky._data()
    assert sorted(n for c, n in data if c == "M") == list(range(1, 111))
    assert sorted(n for c, n in data if c == "C") == list(range(1, 110))
    for (c, n), v in data.items():
        assert 0 <= v["ra"] < 360 and -90 <= v["dec"] <= 90, (c, n)
        assert v["mag"] is None or -2 < v["mag"] < 14, (c, n)
        assert v["size"] is None or 0 < v["size"] < 500, (c, n)
    # bekannte Werte (Literatur): Galaxien nicht schwächer als B−0,85-Regel zulässt, Haufen mit sichtbarer Ausdehnung, PN nicht nur die Kernfigur
    assert abs(data[("M", 31)]["ra"] - 10.68) < 0.1 and abs(data[("M", 31)]["dec"] - 41.27) < 0.1
    assert data[("M", 31)]["mag"] < 4.5 and data[("C", 65)]["mag"] < 8.5 and data[("C", 83)]["mag"] < 10
    assert data[("M", 7)]["size"] >= 60 and data[("M", 41)]["size"] >= 30 and data[("M", 45)]["size"] >= 100
    assert data[("M", 57)]["size"] >= 3 and data[("M", 76)]["size"] >= 2
    assert data[("C", 34)]["size"] < 100 and data[("C", 80)]["size"] >= 30 and data[("C", 103)]["size"] >= 30 and data[("C", 13)]["size"] >= 13   # Haufen/Nebel: sichtbare Ausdehnung
    assert 8 < data[("C", 46)]["mag"] < 10.5                              # veränderlich, nicht der OpenNGC-Wert 11,85
    assert data[("C", 37)]["mag"] < 8                                      # nicht der OpenNGC-Wert 14,1 (falsche Messung)
    for prefix, rows in (("M", deepsky.MESSIER), ("C", deepsky.CALDWELL)):
        assert all((prefix, n) in data for n, *_ in rows)
    t = {x.id: x for x in deepsky.fixed()}
    assert t["C34"].fallback and not t["M31"].fallback                     # C34: Größe von Hand (Quellen meinen den ganzen Komplex)
    assert t["M8"].mag is None                                             # Emissionsnebel: Helligkeit bewusst unbekannt
    assert "M57" in {x.id for x in deepsky.catalog() if deepsky.usable(x, STELLINA, [1, .7])}    # bleibt in den „Heute Nacht“-Kandidaten
