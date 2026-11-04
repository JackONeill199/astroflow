"""Gemeinsame Ortsliste (core/sites.py): Auflösung, Zeiträume, Migration, API und Anbindung von Tutor/Heute Nacht/SkyTrace.
Nur erfundene Orte (Testhausen, Beispielberg …)."""
import json
import math
import sys
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from astroflow.core import sites
from astroflow.core.config import load_settings
from astroflow.core.store import SettingsStore
from astroflow.server import create_app

HOME = {"id": "home", "name": "Testhausen", "lat": 47.5, "lon": 10.25, "alt_m": 400, "accuracy_m": 50}
TRIP = {"id": "trip", "name": "Beispielberg", "lat": 45.0, "lon": 6.0, "alt_m": 1800, "accuracy_m": 30}
CAMP = {"id": "camp", "name": "Zeltplatz", "lat": 46.0, "lon": 7.0, "accuracy_m": 2000}


def book(tmp_path, **data):
    b = sites.SiteBook(SettingsStore(tmp_path / "settings.json"))
    b.save({"list": [HOME, TRIP, CAMP], "default": "home", "periods": [], **data})
    return b


def test_night_of():
    assert sites.night_of("2024-03-10T03:00:00") == "2024-03-09"          # nach Mitternacht → Vornacht
    assert sites.night_of("2024-03-10T20:00:00Z") == "2024-03-10"
    assert sites.night_of("2024-03-10") == "2024-03-10"
    assert sites.night_of(None) is None and sites.night_of("kaputt") is None


def test_resolution_order(tmp_path):
    b = book(tmp_path, periods=[{"site_id": "trip", "from": "2024-07-01", "to": "2024-07-14"}])
    assert b.site_for(night="2024-06-30")["id"] == "home"
    assert b.site_for(night="2024-06-30")["source"] == "default"
    s = b.site_for(when="2024-07-05T22:00:00")
    assert (s["id"], s["source"], s["accuracy_m"]) == ("trip", "period", 30)
    # FITS-Kopf schlägt Zeitraum, Override schlägt alles
    hdr = {"SITELAT": "48 30 00", "SITELONG": "-3 15 00", "SITEELEV": 120, "DATE-OBS": "2024-07-05T22:00:00"}
    s = b.site_for(header=hdr)
    assert s["source"] == "fits" and s["lat"] == pytest.approx(48.5) and s["lon"] == pytest.approx(-3.25)
    assert s["accuracy_m"] == sites.HEADER_ACCURACY_M
    assert b.site_for(header=hdr, override={"lat": 1, "lon": 2, "name": "x"})["source"] == "override"
    # Kopf ohne Ort → Zeit aus DATE-OBS entscheidet
    assert b.site_for(header={"DATE-OBS": "2024-07-06T01:00:00"})["id"] == "trip"
    # Kamera ohne GPS-Fix (0/0) zählt nicht
    assert b.site_for(header={"SITELAT": 0, "SITELONG": 0, "DATE-OBS": "2024-01-01T22:00:00"})["id"] == "home"


def test_obsgeo_xyz():
    # Testhausen als ECEF → zurück
    lat, lon, h = math.radians(47.5), math.radians(10.25), 400
    a, f = 6378137.0, 1 / 298.257223563
    e2 = f * (2 - f)
    n = a / math.sqrt(1 - e2 * math.sin(lat) ** 2)
    xyz = ((n + h) * math.cos(lat) * math.cos(lon), (n + h) * math.cos(lat) * math.sin(lon), (n * (1 - e2) + h) * math.sin(lat))
    s = sites.from_header({"OBSGEO-X": xyz[0], "OBSGEO-Y": xyz[1], "OBSGEO-Z": xyz[2]})
    assert s["lat"] == pytest.approx(47.5, abs=1e-7) and s["lon"] == pytest.approx(10.25, abs=1e-7) and s["alt_m"] == pytest.approx(400, abs=0.01)


def test_periods_overlap_and_open(tmp_path):
    b = book(tmp_path, periods=[{"site_id": "camp", "from": "2023-01-01", "to": ""},          # offen: Umzug
                                {"site_id": "trip", "from": "2024-07-01", "to": "2024-07-14"}])
    assert b.site_for(night="2022-12-31")["id"] == "home"
    assert b.site_for(night="2025-05-05")["id"] == "camp"
    assert b.site_for(night="2024-07-03")["id"] == "trip"                   # kürzerer Zeitraum gewinnt
    b.set_night("2024-07-03", "home")                                          # Einzelnacht schlägt beide
    assert b.site_for(night="2024-07-03")["id"] == "home"
    assert b.site_for(night="2024-07-03")["detail"] == "Zeitraum diese Nacht"
    b.set_night("2024-07-03", None)
    assert b.site_for(night="2024-07-03")["id"] == "trip"


def test_validation(tmp_path):
    b = book(tmp_path)
    with pytest.raises(ValueError):
        b.save({"list": [{**HOME, "lat": 91}]})
    with pytest.raises(ValueError):
        b.save({"list": [HOME], "periods": [{"site_id": "nope", "from": "2024-01-01"}]})
    with pytest.raises(ValueError):
        b.save({"list": [HOME], "periods": [{"site_id": "home", "from": "2024-02-01", "to": "2024-01-01"}]})
    with pytest.raises(ValueError):
        b.save({"list": [HOME, HOME]})
    d = b.save({"list": [HOME], "default": "weg"})
    assert d["default"] == "home"                                              # unbekannter Standard → erster Ort


def test_migration_merges_and_keeps_old(tmp_path):
    store = SettingsStore(tmp_path / "settings.json")
    store.update("tutor", {"lat": 47.5, "lon": 10.25, "mode": "einsteiger"})
    b = sites.SiteBook(store)
    assert sites.migrate_tutor(b, store) == 1
    assert sites.migrate_tutor(b, store) == 0                                  # nur einmal
    cfg = {"site": {"lat": 47.5009, "lon": 10.2505, "alt_m": 412, "name": "Garten"},       # ~110 m daneben → derselbe Ort
           "sites": {"2024-07-05_observation_M31": {"lat": 45.0, "lon": 6.0, "name": "Urlaub"},
                     "ohne-datum": {"lat": 46.0, "lon": 7.0}}}
    assert sites.migrate_skytrace(b, cfg, key="skytrace:x") == 2
    d = b.data()
    assert len(d["list"]) == 3 and d["default"] == d["list"][0]["id"]
    assert d["list"][0]["alt_m"] == 412                                        # Höhe aus SkyTrace ergänzt
    assert b.site_for(night="2024-07-05")["name"] == "Urlaub"
    assert store.get("tutor")["lat"] == 47.5                                   # alte Felder bleiben
    assert sites.migrate_skytrace(b, cfg, key="skytrace:x") == 0


def test_api_and_tonight(tmp_path, monkeypatch):
    data = tmp_path / "d"
    data.mkdir()
    (data / "settings.json").write_text(json.dumps({"tutor": {"lat": 47.5, "lon": 10.25}}), encoding="utf-8")
    with TestClient(create_app(load_settings(data), only=("stacking",))) as c:
        d = c.get("/api/sites").json()
        assert len(d["list"]) == 1 and d["today_site"]["source"] == "default"       # Tutor-Ort übernommen
        sid = d["list"][0]["id"]
        r = c.post("/api/sites", json={"list": [*d["list"], {"name": "Beispielberg", "lat": 45, "lon": 6, "accuracy_m": 30}]})
        assert r.status_code == 200
        other = [s for s in r.json()["list"] if s["id"] != sid][0]["id"]
        assert c.post("/api/sites", json={"list": [{"lat": 99, "lon": 0}]}).status_code == 400
        today = d["today"]
        r = c.post("/api/sites/night", json={"night": today, "site_id": other}).json()
        assert r["today_site"]["id"] == other
        t = c.get("/api/stacking/tonight").json()
        assert t["site_info"]["id"] == other and t["site"][0] == pytest.approx(45)
        assert c.post("/api/sites/night", json={"night": "gestern"}).status_code == 400
        assert c.get("/api/sites/resolve", params={"night": "2001-01-01"}).json()["site"]["id"] == sid


def test_tutor_pick_site():
    from astroflow.modules.stacking import tutor
    calls = []
    fake = lambda **kw: calls.append(kw) or {"lat": 45.123456, "lon": 6.5, "name": "Beispielberg"}
    frames = [{"night": "2024-07-05"}, {"night": "2024-07-05"}, {"night": "2024-07-06"}]
    assert tutor.pick_site(frames, {"lat": 1, "lon": 2}, fake) == ([45.123, 6.5], "sites", "Beispielberg")
    assert calls == [{"night": "2024-07-05"}]
    assert tutor.pick_site([{"site": [1, 2]}], {}, fake)[1] == "header"
    assert tutor.pick_site(frames, {"lat": 1, "lon": 2}, None)[1] == "settings"
    assert tutor.pick_site(frames, {}, lambda **kw: None) == (None, None, None)


# ---------------------------------------------------------------- SkyTrace ---
ENGINE = Path(__file__).resolve().parents[1] / "astroflow" / "modules" / "skytrace" / "engine"


@pytest.fixture
def obs(tmp_path, monkeypatch):
    """Engine-Modul mit eigenem Datenordner; danach wieder entladen (project_paths legt BASE beim Import fest –
    sonst sähen spätere SkyTrace-Tests im selben Worker diesen Ordner)."""
    before = set(sys.modules)
    already = "observation_settings" in sys.modules
    if not already:
        monkeypatch.setenv("SKYTRACE_HOME", str(tmp_path / "st"))
    monkeypatch.syspath_prepend(str(ENGINE))
    import observation_settings
    monkeypatch.setattr(observation_settings, "SITES_FILE", None)
    monkeypatch.delenv("ASTROFLOW_SITES", raising=False)
    yield observation_settings
    for name in set(sys.modules) - before:
        if not name.startswith("astroflow"):
            sys.modules.pop(name, None)


def test_skytrace_resolve_uses_shared_list(tmp_path, obs):
    from astropy.io import fits
    b = book(tmp_path, periods=[{"site_id": "trip", "from": "2024-07-01", "to": "2024-07-14"}])
    path = tmp_path / "a.fits"
    fits.PrimaryHDU(header=fits.Header({"DATE-OBS": "2024-07-05T22:00:00"})).writeto(path)
    legacy = {"site": {"lat": 1.0, "lon": 2.0, "alt_m": 3}, "sites": {}}
    assert obs.resolve_site(path, "x", legacy)["lat"] == 1.0                  # ohne Ortsliste: alter Standard
    shared = {**legacy, "_sites_file": str(b.store.path)}
    s = obs.resolve_site(path, "x", shared)
    assert (s["site_id"], s["origin"], s["accuracy_m"]) == ("trip", "period", 30)
    assert obs.resolve_site(tmp_path / "fehlt.fits", "x", shared, when=1e9)["site_id"] == "home"   # Datei weg → Zeitangabe
    over = {**shared, "sites": {"x": {"lat": 5, "lon": 6}}}
    assert obs.resolve_site(path, "x", over)["origin"] == "override"
    # Engine ohne AstroFlow-Paket: Datei direkt laden
    obs._SITES_MODULE = None
    mod = sys.modules.pop("astroflow.core.sites")
    try:
        import builtins
        real = builtins.__import__

        def no_pkg(name, *a, **k):
            if name.startswith("astroflow"):
                raise ImportError(name)
            return real(name, *a, **k)
        builtins.__import__ = no_pkg
        try:
            assert obs.resolve_site(path, "x", shared)["site_id"] == "trip"
        finally:
            builtins.__import__ = real
    finally:
        sys.modules["astroflow.core.sites"] = mod
        obs._SITES_MODULE = None


def test_skytrace_tolerance(obs):
    pytest.importorskip("skyfield")
    import catalog_matching as cm
    assert cm.site_tolerance(100, 500) == cm.SATELLITE_TOLERANCE_ARCSEC            # genauer Ort: unverändert
    assert cm.site_tolerance(None, 500) == cm.SATELLITE_TOLERANCE_ARCSEC
    assert cm.site_tolerance(1000, 500) == pytest.approx(2 * 1000 / 500e3 * 206264.8, rel=1e-6)   # ≈ 825″
    assert cm.site_tolerance(20000, 500) == cm.SATELLITE_SITE_TOLERANCE_CAP_ARCSEC
    assert not cm.site_uncertain(300) and cm.site_uncertain(2000)


# ---------------------------------------------------------------- Rückrechnung ---
def _sats(t0, site, n=6):
    """Erfundene LEO-Satelliten (550 km, 53°), die zur Zeit t0 über dem Standort stehen."""
    pytest.importorskip("sgp4")
    from sgp4.api import Satrec, WGS72
    from astroflow.modules.skytrace import orbit3d, sitefit
    up = orbit3d.geodetic_to_ecef(site["lat"], site["lon"], 0)
    up = up / np.linalg.norm(up)
    jd = t0 / 86400 + 2440587.5
    out = []
    for raan in np.arange(0, 360, 7.0):
        for ma in np.arange(0, 360, 1.5):
            s = Satrec()
            s.sgp4init(WGS72, "i", 90000 + len(out), jd - 2433281.5, 1e-5, 0, 0, 1e-4, 0.0, math.radians(53),
                       math.radians(ma), 2 * math.pi / (95.6), math.radians(raan))
            _, r, _ = s.sgp4(jd, 0.0)
            ecef = orbit3d.rot_z(-orbit3d.gmst(t0), np.asarray(r))
            topo = ecef - orbit3d.geodetic_to_ecef(site["lat"], site["lon"], site["alt_m"])
            if topo @ up / np.linalg.norm(topo) > math.sin(math.radians(35)):
                out.append(s)
                break
        if len(out) >= n:
            break
    return out


def test_sitefit_recovers_site_and_clock():
    from astroflow.modules.skytrace import orbit3d, sitefit
    true = {"lat": 47.5, "lon": 10.25, "alt_m": 400}
    t0 = 1.72e9
    sats = _sats(t0, true)
    assert len(sats) >= 4
    clock = 4.0                                              # Kamera geht 4 s nach: wahre Zeit = Kamerazeit + 4 s
    obs = []
    for i, s in enumerate(sats):
        start = t0 + 40 * i
        inside = i % 2 == 0
        a, b = (0, 10) if inside else (3, 7)                 # durchs Bild laufende Spur: nur ein Mittelstück sichtbar
        tt = np.linspace(start + a, start + b, 11) + clock
        u = sitefit.topo_directions(s, tt, true["lat"], true["lon"], true["alt_m"], orbit3d.icrs_to_teme(start + 5))
        ra = np.degrees(np.arctan2(u[:, 1], u[:, 0])) % 360
        dec = np.degrees(np.arcsin(u[:, 2]))
        obs.append({"sat": s, "trail": np.column_stack([ra, dec]).tolist(), "start": start, "duration": 10.0, "inside": inside, "label": f"S{i}"})
    wrong = {"lat": true["lat"] + 1.8 / 111.195, "lon": true["lon"] - 1.2 / (111.195 * math.cos(math.radians(47.5))), "alt_m": 400}
    res = sitefit.fit(obs, wrong)
    assert res["ok"] and res["worth"], res
    assert res["north_km"] == pytest.approx(-1.8, abs=0.05) and res["east_km"] == pytest.approx(1.2, abs=0.05)
    assert res["clock_s"] == pytest.approx(clock, abs=0.1)
    assert res["rms_after_arcsec"] < 5 < res["rms_before_arcsec"]
    # Richtiger Ort: kein Vorschlag
    same = sitefit.fit([{**o, "start": o["start"]} for o in obs], true)
    assert same["shift_km"] < 0.05 and not same["worth"]
    assert sitefit.fit(obs[:2], wrong)["ok"] is False


def test_impossible_dates_rejected(tmp_path):
    b = book(tmp_path)
    with pytest.raises(ValueError):
        b.save({"list": [HOME], "periods": [{"site_id": "home", "from": "2026-02-30", "to": "2026-03-05"}]})
    with pytest.raises(ValueError):
        b.set_night("2026-02-30", "home")
    # von Hand verdorbene Datei: Auflösung läuft trotzdem
    b.store.update("sites", {"periods": [{"id": "x", "site_id": "trip", "from": "2026-02-30", "to": ""}]})
    assert b.site_for(night="2026-06-01")["id"] == "home"


def test_planning_night_matches_tonight_window():
    import datetime as dt
    from astroflow.modules.stacking import tonight
    for hour in (2, 5, 7, 9, 13, 20, 23):
        now = dt.datetime(2026, 11, 4, hour, tzinfo=dt.timezone.utc).timestamp()
        for lon in (10.25, -70.0, 150.0):
            start, _ = tonight._window(now, lon)
            local_noon = dt.datetime.fromtimestamp(start + lon / 15 * 3600, dt.timezone.utc)
            assert sites.planning_night(now, lon) == local_noon.strftime("%Y-%m-%d")
    # Vormittag in Mitteleuropa: schon die kommende Nacht (night_of läge noch beim Vortag)
    now = dt.datetime(2026, 11, 4, 9, tzinfo=dt.timezone.utc).timestamp()
    assert sites.planning_night(now, 10.25) == "2026-11-04" and sites.night_of(now) == "2026-11-03"
