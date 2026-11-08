"""SkyTrace 3D-Lauf-Ansicht: Teleskopweg über die Nacht, Lücken, Zielobjekt, alle Treffer; Hinweise statt Fehler.
Erfundener Ort, erfundene Bahn (siehe test_skytrace_3d); Az/Alt gegen astropy."""
import json
import math
import shutil

import numpy as np
import pytest

pytest.importorskip("sgp4")
from test_skytrace_3d import T0, _run, _site, client  # noqa: E402,F401

META = {"INSTRUME": "stellina-test", "EXPOSURE": "10000", "exposure_seconds": 10.0, "NAXIS1": "1000", "NAXIS2": "800",
        "FOCAL": "400", "PIXSZ": "2.4"}


def _frames(obs, n=30, step=20, gap_after=20, gap=600, rot_deg=.05):
    """Aufnahmen alle `step` s (Zeitstempel = Belichtungsende wie Stellina), eine Pause, Bilddrehung je Aufnahme."""
    rows, t = [], T0 - 300
    for i in range(n):
        a = math.radians(rot_deg * i)
        c, s = math.cos(a), math.sin(a)
        cx, cy = 250, 200                                 # Drehung um die Mitte des Analysebilds (500×400)
        m = [[c, -s, cx - c * cx + s * cy], [s, c, cy - s * cx - c * cy]]
        rows.append(dict(index=i, timestamp=t, file=f"x/{i}.fits", meta=META, reference_to_current=m))
        t += step + (gap if i == gap_after else 0)
    (obs / "frames.jsonl").write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    excl = [dict(index=n + k, timestamp=T0 - 300 + 5 + k * step, file="x") for k in range(3)]
    (obs / "ausgeschlossen.jsonl").write_text("\n".join(json.dumps(r) for r in excl), encoding="utf-8")
    return rows


def test_teleskopweg_und_treffer(client):
    c, home = client
    site = _site()
    rid, mid = _run(home, site)
    obs = home / "Ergebnisse" / rid / "Testfeld"
    rows = _frames(obs)
    r = c.get("/api/skytrace/run3d", params={"run": rid}).json()
    assert r["ok"] and r["sessions"] == ["Testfeld"] and r["target"]["name"] == "Testfeld"
    p = r["pointing"]
    assert p["source"] == "wcs" and len(p["t"]) == len(rows) and r["frames"] == dict(accepted=30, excluded=3, excluded_times=r["frames"]["excluded_times"])
    assert r["anchor"] == pytest.approx(T0 - 310) and len(r["gaps"]) == 1 and r["gaps"][0][1] - r["gaps"][0][0] > 600
    # Az/Alt der Bildmitte gegen astropy (Belichtungsmitte der ersten Aufnahme)
    import astropy.units as u
    from astropy.coordinates import AltAz, EarthLocation, SkyCoord
    from astropy.time import Time
    aa = SkyCoord(mid[0] * u.deg, mid[1] * u.deg).transform_to(
        AltAz(obstime=Time(T0 - 305, format="unix"), location=EarthLocation.from_geodetic(site["lon"] * u.deg, site["lat"] * u.deg, 0)))
    assert p["alt"][0] == pytest.approx(aa.alt.deg, abs=.05) and abs(((p["az"][0] - aa.az.deg) + 180) % 360 - 180) < .1
    # Bilddrehung 29 × 0,05° ist im Positionswinkel sichtbar
    assert abs(((p["pa"][-1] - p["pa"][0]) + 180) % 360 - 180) == pytest.approx(1.45, abs=.05)
    assert all(math.isfinite(x) for x in p["parallactic"])
    hit = r["hits"][0]
    assert len(r["hits"]) == 1 and hit["name"] == "TESTSAT" and hit["key"] == "Testfeld/K0001" and hit["in_fov"]
    assert hit["t_pass"] == pytest.approx(T0) and len(hit["ring"]) == 181 and r["window"][0] <= T0 - 310
    # gemeinsames System D: Satellit zur Spurzeit liegt in Blickrichtung der passenden Aufnahme
    k = int(np.argmin(abs(np.array(p["t"]) - T0)))
    j = len(hit["track"]) // 2
    obs_d = np.array(r["observer"])
    from astroflow.modules.skytrace import orbit3d
    obs_d = orbit3d.rot_z(orbit3d.gmst(T0) - orbit3d.gmst(r["anchor"]), obs_d)
    look = np.array(hit["track"][j]) - obs_d
    assert math.degrees(math.acos(look @ np.array(p["center"][k]) / np.linalg.norm(look))) < 1.2
    # Fokus markiert den Kandidaten
    f = c.get("/api/skytrace/run3d", params={"run": rid, "focus": "Testfeld/K0001"}).json()
    assert f["hits"][0]["focus"]


def test_lauf_ohne_treffer(client):
    c, home = client
    rid, _ = _run(home, _site())
    obs = home / "Ergebnisse" / rid / "Testfeld"
    _frames(obs)
    items = json.loads((obs / "kandidaten.json").read_text(encoding="utf-8"))
    (obs / "kandidaten.json").write_text(json.dumps([dict(x, catalogs=[]) for x in items]), encoding="utf-8")
    r = c.get("/api/skytrace/run3d", params={"run": rid}).json()
    assert r["ok"] and r["hits"] == [] and len(r["pointing"]["t"]) == 30


def test_lauf_ohne_standort(client):
    c, home = client
    rid, _ = _run(home, _site(), with_site=False)
    _frames(home / "Ergebnisse" / rid / "Testfeld")
    r = c.get("/api/skytrace/run3d", params={"run": rid}).json()
    assert not r["ok"] and r["need_site"] and "Standort" in r["reason"]


def test_ohne_plate_solving_aus_dem_katalog(client):
    """Ohne WCS: Blickrichtung aus OBJECT/Katalog (M31), Bildfeld aus FOCAL/PIXSZ, als Schätzung gekennzeichnet."""
    c, home = client
    rid, _ = _run(home, _site())
    run = home / "Ergebnisse" / rid
    obs = run / "Testfeld"
    _frames(obs)
    shutil.rmtree(obs / "platesolve")
    v = json.loads((run / "videos.json").read_text(encoding="utf-8"))
    v[0]["session"] = "2022-08-01_observation_M31"
    (run / "videos.json").write_text(json.dumps(v), encoding="utf-8")
    r = c.get("/api/skytrace/run3d", params={"run": rid}).json()
    assert r["ok"] and r["pointing"]["source"] == "katalog" and r["target"]["name"].startswith("M31")
    assert r["target"]["kind"] and r["pointing"]["ra"] == pytest.approx(10.68, abs=.1)
    assert r["pointing"]["size_deg"] == pytest.approx([.344, .275], abs=.01)    # 1000×800 px à 2,4 µm, 400 mm
    assert c.get("/api/skytrace/run3d", params={"run": "../x"}).status_code == 400


def test_analysegroesse_ohne_kandidaten(tmp_path):
    """WCS in voller Auflösung (3000×2000), Registrierung in Analysepixeln (1536×1024): Verschiebung muss skaliert werden."""
    from astroflow.modules.skytrace import run3d
    from test_skytrace_3d import _wcs
    assert run3d._analysis_size({"width": 3000, "height": 2000}) == (1536, 1024)
    assert run3d._analysis_size({"width": 1000, "height": 801}) == (1000, 800)
    obs = tmp_path / "obs"
    _wcs(obs, 100.0, 20.0, w=3000, h=2000, scale=1.5 / 3600)
    shifted = [[1, 0, -768], [0, 1, 0]]                   # Aufnahme um halbe Analysebreite verschoben
    rows = [dict(index=0, timestamp=T0, reference_to_current=[[1, 0, 0], [0, 1, 0]]), dict(index=1, timestamp=T0 + 20, reference_to_current=shifted)]
    p = run3d._pointing(obs, rows, [], {})
    a, b = (o for o in (p["samples"][0]["center"], p["samples"][1]["center"]))
    # Bildmitte der zweiten Aufnahme = rechter Rand der Referenz: halbe Bildbreite (3000 px × 1,5″ / 2 = 0,625°)
    assert math.hypot((b[0] - a[0]) * math.cos(math.radians(20)), b[1] - a[1]) == pytest.approx(.625, abs=.005)


def test_standort_aus_den_einstellungen_wirkt_sofort(client):
    """Zwischengespeicherter Hinweis „Standort fehlt“ verfällt, sobald der Standort in den Einstellungen gespeichert wird."""
    c, home = client
    rid, _ = _run(home, _site(), with_site=False)
    _frames(home / "Ergebnisse" / rid / "Testfeld")
    assert c.get("/api/skytrace/run3d", params={"run": rid}).json()["need_site"]
    assert c.post("/api/skytrace/settings", json={"site": {"lat": 12.0, "lon": 34.0, "alt_m": 0}}).status_code == 200
    assert c.get("/api/skytrace/run3d", params={"run": rid}).json()["ok"]


def test_uhrversatz_der_nacht_wirkt(client):
    """Kamera-Uhr ging 10 min vor (übernommener Uhrversatz −600 s): Teleskopzeiten in wahrer UTC, Treffer passt zu den Aufnahmen."""
    import datetime as dt
    c, home = client
    site = _site()
    rid, mid = _run(home, site)
    obs = home / "Ergebnisse" / rid / "Testfeld"
    rows = _frames(obs)
    clock = -600.0                                           # wahre UTC = Kamerazeit + clock
    for name in ("frames.jsonl", "ausgeschlossen.jsonl"):
        lines = [json.loads(x) for x in (obs / name).read_text(encoding="utf-8").splitlines()]
        (obs / name).write_text(chr(10).join(json.dumps(dict(r, timestamp=r["timestamp"] - clock)) for r in lines), encoding="utf-8")
    cands = json.loads((obs / "kandidaten.json").read_text(encoding="utf-8"))
    for x in cands:
        x["timestamp"] -= clock                               # Kamerazeit; Abgleich (exposure_start) war schon korrigiert
        x["catalogs"][0]["clock_offset_s"] = clock if x["id"] == "K0001" else None
    (obs / "kandidaten.json").write_text(json.dumps(cands), encoding="utf-8")
    night = dt.datetime.fromtimestamp(T0 - 43200, dt.timezone.utc).date().isoformat()
    if True:                                                 # Einstellungen liegen dank client-Fixture in tmp_path
        assert c.post("/api/skytrace/settings", json={"clock_offsets": {night: {"offset_s": clock}}}).status_code == 200
        r = c.get("/api/skytrace/run3d", params={"run": rid}).json()
        assert r["ok"] and r["clock_offset_s"] == clock and not r["clock_pending"]
        p = r["pointing"]
        assert r["anchor"] == pytest.approx(T0 - 310) and p["t"][0] == pytest.approx(rows[0]["timestamp"] - 10)
        assert r["frames"]["excluded_times"][0] == pytest.approx(T0 - 300 + 5 - 10)
        hit = r["hits"][0]
        assert hit["t_pass"] == pytest.approx(T0)
        k = int(np.argmin(abs(np.array(p["t"]) - T0)))
        assert abs(p["t"][k] - T0) < 20                       # Treffer fällt zeitlich in die Aufnahmen
        from astroflow.modules.skytrace import orbit3d
        obs_d = orbit3d.rot_z(orbit3d.gmst(T0) - orbit3d.gmst(r["anchor"]), np.array(r["observer"]))
        look = np.array(hit["track"][len(hit["track"]) // 2]) - obs_d
        assert math.degrees(math.acos(look @ np.array(p["center"][k]) / np.linalg.norm(look))) < 1.2
        # Rückfall ohne Satellitenergebnis: Kamerazeit + Versatz
        assert orbit3d._exposure_start(cands[0], None, clock) == pytest.approx(T0 - 5)
        # Korrektur wieder entfernt → Kamerazeit, Hinweis „neu abgleichen“ (Cache-Schlüssel enthält den Versatz)
        assert c.post("/api/skytrace/settings", json={"clock_offsets": {night: None}}).status_code == 200
        r = c.get("/api/skytrace/run3d", params={"run": rid}).json()
        assert r["clock_offset_s"] is None and r["anchor"] == pytest.approx(T0 - 310 - clock) and r["clock_pending"]


def test_bahnrechnung_nicht_moeglich(tmp_path):
    """Liefert die Bahnrechnung (z. B. ohne sgp4 im Engine-Interpreter) einen Hinweis, bleibt die Lauf-Ansicht nutzbar."""
    from astroflow.modules.skytrace import run3d
    home = tmp_path / "home"
    rid, _ = _run(home, _site())
    _frames(home / "Ergebnisse" / rid / "Testfeld")
    run3d._cached.cache_clear()
    r = run3d.build_run(home / "Ergebnisse" / rid, None, None, home, {}, scene=lambda p: dict(ok=False, reason="sgp4 fehlt"))
    run3d._cached.cache_clear()
    assert r["ok"] and r["hits"] == [] and r["missing"][0]["reason"] == "sgp4 fehlt"
