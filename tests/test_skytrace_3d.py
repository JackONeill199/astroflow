"""SkyTrace 3D-Ansicht: Bahn (SGP4) + Blickfeld physikalisch prüfen, ältere Läufe ohne Bahnelemente → Hinweis.
Erfundener Ort und erfundene Bahn (ISS-ähnlich); Vergleich gegen astropy (TEME → ITRS → AltAz)."""
import datetime as dt
import json
import math

import numpy as np
import pytest
from fastapi.testclient import TestClient

pytest.importorskip("sgp4")
from astropy.utils import iers  # noqa: E402

iers.conf.auto_download = False

from astroflow.core.config import load_settings  # noqa: E402
from astroflow.modules.skytrace import orbit3d  # noqa: E402
from astroflow.server import create_app  # noqa: E402

L1 = "1 99001U 24001A   24061.50000000  .00016717  00000-0  30000-3 0  9998"
L2 = "2 99001  51.6400 120.0000 0005000  80.0000 280.0000 15.50000000 10007"
T0 = dt.datetime(2024, 3, 1, 21, 0, tzinfo=dt.timezone.utc).timestamp()


def _astropy(t, site):
    """Satellit von `site` aus zur Zeit t: (RA, Dec) geozentrisch-äquatorial (GCRS ≈ ICRS-Achsen), Höhe über dem Ellipsoid (km), ECEF (km)."""
    import astropy.units as u
    from astropy.coordinates import GCRS, CartesianRepresentation, EarthLocation, ITRS, TEME
    from astropy.time import Time
    sat = orbit3d.satrec_from_tle(L1, L2)
    _, r, _ = sat.sgp4_array(*orbit3d._jd(np.array([t])))
    T = Time(t, format="unix")
    itrs = TEME(CartesianRepresentation(r[0] * u.km), obstime=T).transform_to(ITRS(obstime=T))
    loc = EarthLocation.from_geodetic(site["lon"] * u.deg, site["lat"] * u.deg, 0)
    # topozentrische Richtung: Differenzvektor im ITRS, dann Richtung über eine weit entfernte Hilfsposition
    obs = loc.get_itrs(obstime=T).cartesian.xyz.to(u.km).value
    d = itrs.cartesian.xyz.to(u.km).value - obs
    far = ITRS(CartesianRepresentation((obs + d / np.linalg.norm(d) * 1e9) * u.km), obstime=T)
    sky = far.transform_to(GCRS(obstime=T))                 # geozentrisch (ICRS wäre baryzentrisch → Parallaxe)
    return sky.ra.deg, sky.dec.deg, itrs.earth_location.height.to(u.km).value, itrs.cartesian.xyz.to(u.km).value


def _site():
    """Erfundener Ort knapp neben der Bahnspur 2 min nach T0 (Satellit steht zur Spurzeit hoch am Himmel)."""
    import astropy.units as u
    from astropy.coordinates import CartesianRepresentation, ITRS, TEME
    from astropy.time import Time
    sat = orbit3d.satrec_from_tle(L1, L2)
    t = T0 + 120
    _, r, _ = sat.sgp4_array(*orbit3d._jd(np.array([t])))
    loc = TEME(CartesianRepresentation(r[0] * u.km), obstime=Time(t, format="unix")).transform_to(ITRS(obstime=Time(t, format="unix"))).earth_location
    return {"lat": round(float(loc.lat.deg) + .7, 1), "lon": round(float(loc.lon.deg), 1), "alt_m": 0}


def _wcs(folder, ra, dec, w=1000, h=800, scale=4.5 / 3600 * 1.0):
    from astropy.wcs import WCS
    wcs = WCS(naxis=2)
    wcs.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    wcs.wcs.crval = [ra, dec]
    wcs.wcs.crpix = [w / 2 + .5, h / 2 + .5]
    wcs.wcs.cdelt = [-scale, scale]
    (folder / "platesolve").mkdir(parents=True)
    wcs.to_header().tofile(folder / "platesolve" / "loesung.wcs")
    (folder / "platesolve" / "ergebnis.json").write_text(json.dumps({"width": w, "height": h, "ra": ra, "dec": dec}), encoding="utf-8")


def _run(home, site, with_tle=True, with_site=True):
    run = home / "Ergebnisse" / "2026-11-03_20-00-00-000001"
    obs = run / "Testfeld"
    (obs / "kandidaten").mkdir(parents=True)
    (run / "bericht.html").write_text("<html></html>", encoding="utf-8")
    start = T0 - 5                                          # Belichtung 10 s, Durchgang in der Mitte
    a = _astropy(T0 - .4, site)
    b = _astropy(T0 + .4, site)
    mid = _astropy(T0, site)
    _wcs(obs, mid[0], mid[1])
    match = {"name": "TESTSAT", "norad_id": 99001, "tle_age_days": .4, "separation_arcsec": 12.0, "time_offset_s": 0,
             "passage_seconds": [4.6, 5.4], "compatible": True}
    if with_tle:
        match["tle"] = [L1, L2]
    cand = {"id": "K0001", "session": "Testfeld", "kind": "trail", "label": "Einzelbild-Spur", "timestamp": start + 10,
            "time_reference": "end", "exposure_seconds": 10, "world_path": [[a[0], a[1]], [b[0], b[1]]],
            "site": site if with_site else None, "frames": [], "initial_frame": 0,
            "context_image": {"image": "Testfeld/vergleich.jpg", "width": 500, "height": 400, "reference_to_current": [[1, 0, 0], [0, 1, 0]]},
            "catalogs": [{"provider": "satellites", "status": "compatible", "exposure_start": start, "matches": [match]}]}
    plain = dict(cand, id="K0002", catalogs=[{"provider": "satellites", "status": "checked_no_match", "matches": []}])
    (obs / "kandidaten.json").write_text(json.dumps([cand, plain]), encoding="utf-8")
    (obs / "ereignisse.mp4").write_bytes(b"x")
    (run / "videos.json").write_text(json.dumps([{"session": "Testfeld", "video": "Testfeld/ereignisse.mp4", "frames": 3,
                                                  "candidates_file": "Testfeld/kandidaten.json", "state": "Fertig"}]), encoding="utf-8")
    return run.name, mid


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("ASTROFLOW_MODULES", "stacking,skytrace")
    s = load_settings(tmp_path / "d")
    from astroflow.core.store import SettingsStore
    SettingsStore(s.data_dir / "settings.json").update("skytrace", {"home": str(tmp_path / "skytrace")})
    with TestClient(create_app(s)) as c:
        yield c, tmp_path / "skytrace"


def test_bahn_und_blickfeld_stimmen(client):
    c, home = client
    site = _site()
    rid, mid = _run(home, site)
    data = c.get("/api/skytrace/run", params={"id": rid}).json()
    assert [x["view3d"] for x in data["candidates"]] == [True, False]
    r = c.get("/api/skytrace/orbit3d", params={"run": rid, "key": "Testfeld/K0001"}).json()
    assert r["ok"] and r["t0"] == pytest.approx(T0) and not r["missing"]
    s = r["satellites"][0]
    assert s["name"] == "TESTSAT" and s["tle_source"] == "im Lauf gespeichert" and s["in_fov"]
    assert 92 < s["period_min"] < 94 and s["inclination_deg"] == pytest.approx(51.64)
    # Position zur Spurzeit gegen astropy: D-System = erdfest zu t0
    k = len(s["track"]) // 2
    assert np.linalg.norm(np.array(s["track"][k]) - mid[3]) < .5
    assert s["at_t0"]["height_km"] == pytest.approx(mid[2], abs=.5) and 7.3 < s["at_t0"]["speed_kms"] < 7.9
    # Blickfeld zeigt vom Standort auf den Satelliten (Bildmitte = Satellit zur Spurzeit)
    obs = np.array(r["observer"])
    look = np.array(s["track"][k]) - obs
    angle = math.degrees(math.acos(look @ np.array(r["fov"]["boresight"]) / np.linalg.norm(look)))
    assert angle < .02
    assert r["fov"]["size_deg"] == pytest.approx([1.25, 1.0], abs=.01)    # 1000×800 px à 4,5″
    # Durchstoßpunkt: kurzes Stück um t0, Länge ≈ Bildfeld / Winkelgeschwindigkeit
    seg = s["fov_segments"][0]
    assert seg["t"][0] < 0 < seg["t"][-1] and 1 < seg["t"][-1] - seg["t"][0] < 30
    # ganzer Umlauf geschlossen, Sonne als Einheitsvektor
    assert np.linalg.norm(np.array(s["ring"][0]) - np.array(s["ring"][-1])) < 60
    assert np.linalg.norm(r["sun"]) == pytest.approx(1, abs=1e-4)


def test_blickfeld_richtung_fuer_bekannte_koordinaten():
    """Zenit eines erfundenen Orts (RA/Dec aus astropy) → Blickrichtung = Ortsnormale, D = erdfest zu t0."""
    import astropy.units as u
    from astropy.coordinates import AltAz, EarthLocation, SkyCoord
    from astropy.time import Time
    lat, lon = -31.0, 116.0
    z = SkyCoord(alt=90 * u.deg, az=0 * u.deg,
                 frame=AltAz(obstime=Time(T0, format="unix"), location=EarthLocation.from_geodetic(lon * u.deg, lat * u.deg, 0))).icrs
    d = orbit3d.radec_to_d(z.ra.deg, z.dec.deg, T0)
    up = np.array([math.cos(math.radians(lat)) * math.cos(math.radians(lon)), math.cos(math.radians(lat)) * math.sin(math.radians(lon)), math.sin(math.radians(lat))])
    assert math.degrees(math.acos(min(1, d @ up))) * 3600 < 40            # Aberration ~20″
    # Polarstern-Richtung zeigt fast zum Himmelsnordpol (Erdachse = z)
    assert orbit3d.radec_to_d(37.95, 89.26, T0)[2] > .9998


def test_alter_lauf_ohne_bahnelemente(client):
    c, home = client
    site = _site()
    rid, _ = _run(home, site, with_tle=False)
    r = c.get("/api/skytrace/orbit3d", params={"run": rid, "key": "Testfeld/K0001"}).json()
    assert r["ok"] and not r["satellites"] and "nicht gespeichert" in r["missing"][0]["reason"]
    # lokale Archivkopie (wie von der Engine angelegt) liefert die Bahn nach
    sat = orbit3d.satrec_from_tle(L1, L2)
    epoch = dt.datetime(2024, 1, 1, tzinfo=dt.timezone.utc) + dt.timedelta(days=60.5)
    rec = dict(norad_id=99001, epoch=epoch.isoformat(), bstar=sat.bstar, mean_motion_dot=.00016717, eccentricity=sat.ecco,
               arg_perigee=math.degrees(sat.argpo), inclination=math.degrees(sat.inclo), mean_anomaly=math.degrees(sat.mo),
               mean_motion=sat.no_kozai * 1440 / (2 * math.pi), raan=math.degrees(sat.nodeo))
    cache = home / "catalog_cache" / "archives"
    cache.mkdir(parents=True)
    (cache / f"orbits-{dt.datetime.fromtimestamp(T0, dt.timezone.utc):%Y%m%d}.json").write_text(json.dumps({"records": [rec]}), encoding="utf-8")
    r = c.get("/api/skytrace/orbit3d", params={"run": rid, "key": "Testfeld/K0001"}).json()
    assert r["satellites"][0]["tle_source"].startswith("lokales Bahnarchiv") and r["satellites"][0]["in_fov"]


def test_hinweise_statt_fehler(client):
    c, home = client
    rid, _ = _run(home, _site(), with_site=False)
    r = c.get("/api/skytrace/orbit3d", params={"run": rid, "key": "Testfeld/K0001"}).json()
    assert r == {"ok": False, "reason": r["reason"]} and "standort fehlt" in r["reason"]
    r = c.get("/api/skytrace/orbit3d", params={"run": rid, "key": "Testfeld/K0002"}).json()
    assert not r["ok"] and "kein zugeordnetes Objekt" in r["reason"]
    assert c.get("/api/skytrace/orbit3d", params={"run": rid, "key": "Testfeld/K0099"}).status_code == 400
    assert c.get("/api/skytrace/orbit3d", params={"run": "../x", "key": "a/b"}).status_code == 400


def test_eth_archiv_mit_zeitpunkt_des_abgleichs(tmp_path):
    """ETH-Cache ist nach der UTC-Minute der Belichtungsmitte benannt; der Durchgang darf in der nächsten Minute liegen."""
    import hashlib
    minute = dt.datetime.fromtimestamp(T0 - 30, dt.timezone.utc).replace(second=0)
    params = {"start-datetime": minute.strftime("%Y%m%dT%H%M"), "end-datetime": (minute + dt.timedelta(minutes=1)).strftime("%Y%m%dT%H%M"),
              "before": 3, "after": 3, "without-frequency-data": "True"}
    name = "eth-" + hashlib.sha256(json.dumps(params, sort_keys=True).encode()).hexdigest() + ".json"
    (tmp_path / name).write_text(json.dumps({"tles": ["TESTSAT\n" + L1 + "\n" + L2]}), encoding="utf-8")
    base = dict(site={"lat": 10.0, "lon": 20.0, "alt_m": 0}, t0=T0, fov=dict(center=[10.0, 10.0], radius_deg=.5),
                satellites=[dict(name="TESTSAT", norad_id=99001)], cache=str(tmp_path))
    assert orbit3d.build(base)["missing"]                                 # Durchgangsminute: kein Treffer
    r = orbit3d.build(dict(base, archive_time=T0 - 30))
    assert not r["missing"] and r["satellites"][0]["tle_source"] == "lokales Bahnarchiv (ETH Zürich)"
