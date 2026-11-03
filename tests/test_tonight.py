"""Heute Nacht: Dämmerung, Zielkurven, Ablauf und Vorschläge (offline, erfundener Ort in Mitteleuropa)."""
import datetime as dt
import json

import pytest

from astroflow.modules.stacking import tonight

SITE = {"lat": 50.0, "lon": 10.0, "weather": False}
EVENING = dt.datetime(2026, 11, 2, 19, 0, tzinfo=dt.timezone.utc).timestamp()
M31 = {"id": 7, "name": "M31 Andromedagalaxie", "kind": "galaxy", "ra": 10.68, "dec": 41.27, "obj_id": "M31", "exposure": 3600}


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
    assert r["plan"] and r["plan"][0]["name"] == "M31 Andromedagalaxie"
    names = [s["name"] for s in r["suggestions"]]
    assert names and not any(n.startswith("M31") for n in names)    # schon fotografiert → kein Vorschlag
    assert all(s["good_hours"] >= 2 for s in r["suggestions"])
    assert r["weather"] is None and not r["weather_on"]


def test_after_midnight_still_same_night(tmp_path):
    late = EVENING + 7 * 3600                                         # 02:00 UTC am nächsten Morgen
    assert tonight.plan([M31], SITE, tmp_path, late)["start"] == tonight.plan([M31], SITE, tmp_path, EVENING)["start"]

def test_recommendations_use_library_quality_and_field(tmp_path):
    poor = {**M31, 'exposure': 1800}
    d = tonight.plan([poor], SITE, tmp_path, EVENING, {'name': 'Testgerät', 'fov': [1, .7]})
    own = next(x for x in d['recommendations'] if x['category'] == 'more')
    assert own['id'] == 7 and own['href'] == '#/p/7' and own['reasons']
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
    assert device['fov'] == [1, .7]
    info['solve'] = {'fov_w': 2, 'fov_h': 1.5}
    db.set_frame('f', info=info)
    assert tonight.library(db)[1]['fov'] == [2, 1.5]
    with TestClient(create_app(s)) as client:
        assert client.get('/api/stacking/tonight').json() == {'site': None}
        SettingsStore(s.data_dir / 'settings.json').update('tutor', SITE)
        r = client.get('/api/stacking/tonight')
        assert r.status_code == 200 and r.json()['device']['fov'] == [2, 1.5]
