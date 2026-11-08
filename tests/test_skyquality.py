"""Himmelsqualität: Himmelshelligkeit (mag/″²), Bortle, Lichtrichtung, Seeing – offline mit synthetischen Daten und erfundenen Orten."""
import datetime as dt
import json
import math

import numpy as np
import pytest
from fastapi.testclient import TestClient

from astroflow.core.config import load_settings
from astroflow.modules.stacking import photometry, quality, skyquality as sq
from astroflow.server import create_app

SITE = {"id": "x", "name": "Testwiese", "lat": 47.0, "lon": 11.0, "alt_m": 0}   # erfundener Ort
H, W = 500, 700
SCALE = 1.5                                                                     # ″/px
HDR = {"CTYPE1": "RA---TAN", "CTYPE2": "DEC--TAN", "CRVAL1": 250.0, "CRVAL2": 60.0, "CRPIX1": W / 2 + 0.5,
       "CRPIX2": H / 2 + 0.5, "CD1_1": -SCALE / 3600, "CD1_2": 0.0, "CD2_1": 0.0, "CD2_2": SCALE / 3600}


def test_basics():
    assert abs(float(sq.airmass(90)) - 1) < 1e-3 and 1.99 < float(sq.airmass(30)) < 2.0
    assert abs(float(sq.zenith_offset(90))) < 1e-3 and 0.4 < float(sq.zenith_offset(30)) < 0.6
    assert abs(float(sq.gaia_v(np.array([10.0]), np.array([0.0]))[0]) - 10.027) < 1e-3
    b = sq.bortle(21.1)
    assert b["class"] == 4 and b["range"] == [3, 4] and b["text"] == "Bortle 3–4"
    assert sq.bortle(22.2, 0.1)["text"] == "Bortle 1" and sq.bortle(17.0)["class"] == 9 and sq.bortle(None) is None
    assert sq.compass(0) == "N" and sq.compass(95) == "O" and sq.compass(200) == "SSW"
    assert sq.noise_model("stellina", 200)["k"] == 12.9 and sq.noise_model("camera", 100) is None
    assert sq.calibrated("Dark×20, Flat×10") and not sq.calibrated("keine Kalibrierung")


def test_fit_gain_from_noise():
    """Photonen-Transfer: Rauschen² = Ausleserauschen² + k · Himmel; der Schwarzpunkt darf springen."""
    rng = np.random.default_rng(2)
    fr = []
    for i in range(120):
        sky = 50 + 10 * i                                   # Dämmerung: Himmel wird heller
        pedestal = 700 + (60 if i % 10 == 0 else 0)          # gelegentlicher Sprung des Schwarzpunkts
        fr.append({"night": "n", "metrics": {"bg": pedestal + sky, "noise": math.sqrt(42 ** 2 + 12.9 * sky) + rng.normal(0, 0.2)}})
    assert abs(sq.fit_gain(fr) - 12.9) < 0.6
    assert sq.fit_gain(fr[:20]) is None                      # zu wenige Aufnahmen


def test_bg_plane_ignores_object():
    rng = np.random.default_rng(3)
    yy, xx = np.mgrid[0:400, 0:600].astype(float)
    img = 500 + 0.05 * (xx - 300) - 0.02 * (yy - 200) + rng.normal(0, 3, (400, 600))
    img += 400 * np.exp(-((xx - 300) ** 2 + (yy - 200) ** 2) / (2 * 40 ** 2))      # Galaxie in der Mitte
    r = sq.bg_plane(img, s=2)
    assert abs(r["sky"] - 500) < 3                                                 # Median läge deutlich höher
    assert abs(r["bgplane"][0] - 0.025) < 0.004 and abs(r["bgplane"][1] + 0.01) < 0.004   # je Vollauflösungs-Pixel
    m = quality.analyze_frame(img.astype(np.float32), bayer=False)
    assert "sky" in m and len(m["bgplane"]) == 2


def _field(zp=21.5, t=10.0, n=180, seed=1):
    """Stack mit Sternen bekannter V-Helligkeit (Luminanz = alle drei Kanäle gleich) und Gaia-Katalog (RA, Dec, BP−RP, G)."""
    rng = np.random.default_rng(seed)
    w = photometry.wcs_from(HDR)
    x, y = rng.uniform(40, W - 40, n), rng.uniform(40, H - 40, n)
    ra, dec = w.pixel_to_world_values(x, y)
    bprp, g = rng.uniform(0.3, 1.6, n), rng.uniform(9.5, 13.5, n)
    v = sq.gaia_v(g, bprp)
    img = rng.normal(0, 0.5, (H, W))
    yy, xx = np.mgrid[0:H, 0:W]
    for xi, yi, vi in zip(x, y, v):
        f = 10 ** (-0.4 * (vi - zp)) * t
        img += f * np.exp(-((xx - xi) ** 2 + (yy - yi) ** 2) / (2 * 1.7 ** 2)) / (2 * np.pi * 1.7 ** 2)
    return np.repeat(img[None].astype(np.float32), 3, 0), np.c_[ra, dec, bprp, g]


def test_zero_point_from_gaia():
    mean, cat = _field()
    zp = sq.zero_point(mean, np.ones((H, W), np.float32), HDR, cat, exptime=10.0)
    assert abs(zp["zp"] - 21.5) < 0.05 and zp["stars"] >= 40 and abs(zp["color"]) < 0.05


def _night_frames(n=40, sky_adu=150.0, pedestal=720.0):
    """Stellina-artige Aufnahmen einer mondlosen Winternacht (Objekt hoch am Himmel); Himmel steckt im Rauschen."""
    noise = math.sqrt(42.0 ** 2 + 12.9 * sky_adu)
    t0 = dt.datetime(2026, 1, 20, 22, 0, tzinfo=dt.timezone.utc)
    out = []
    for i in range(n):
        out.append({"id": f"f{i}", "night": "2026-01-20", "date": (t0 + dt.timedelta(minutes=3 * i)).isoformat(),
                    "exptime": 10.0, "gain": 200.0, "status": "accepted",
                    "metrics": {"bg": pedestal + sky_adu + 40 * (i % 3), "sky": pedestal + sky_adu, "noise": noise, "fwhm": 4.0, "n_stars": 120,
                                "calib": "keine Kalibrierung", "bgplane": [0.0, 0.0]}})
    return out


def test_evaluate_sky_brightness():
    frames = _night_frames()
    links = {f["id"]: (1.0, np.eye(3)) for f in frames}
    zp_stack = 21.6
    nights = sq.evaluate(frames, zp_stack=zp_stack, zp_device=None, flux_k0=1.0, links=links, excess=0.0, scale=SCALE,
                         site=SITE, ra=100.0, dec=40.0, device_id="stellina")
    n = nights["2026-01-20"]
    # Erwartung: ZP_Nacht = ZP_Stack + K·X (klarste Aufnahmen), dann Fläche je Pixel und Zenitkorrektur
    pts = [p for p in n["curve"] if not p["flags"]]
    assert n["good"] >= 30 and n["site_value"] and n["bortle"]
    alt = np.array([p["alt"] for p in pts])
    X = sq.airmass(alt)
    zp_night = np.percentile(zp_stack + sq.K_EXT * X, 85)
    expect = zp_night - 2.5 * math.log10(150.0 / 10 / SCALE ** 2) + sq.zenith_offset(alt)
    assert abs(n["sqm"] - float(np.median(expect))) < 0.05
    assert abs(n["seeing"] - 4.0 * SCALE) < 1e-6
    assert n["transparency"] is not None and n["transparency"] < 0.1
    assert n["method"] == "noise" and 0.2 < n["err"] < 0.35         # Ausleserauschen-Unsicherheit + Eichung
    # Mit Darks kalibriert: Pegel (ohne Schwarzpunkt) ist der Himmel
    for f in frames:
        f["metrics"].update(calib="Dark×20", sky=150.0)
    n2 = sq.evaluate(frames, zp_stack=zp_stack, zp_device=None, flux_k0=1.0, links=links, excess=0.0, scale=SCALE,
                     site=SITE, ra=100.0, dec=40.0, device_id="camera")["2026-01-20"]
    assert n2["method"] == "level" and abs(n2["sqm"] - n["sqm"]) < 0.02


def test_evaluate_flags_moon_and_clouds():
    frames = _night_frames()
    links = {f["id"]: (1.0 if i % 4 else 2.0, np.eye(3)) for i, f in enumerate(frames)}   # jede 4. Aufnahme halb so viel Sternlicht
    # Vollmondnacht (Mond hoch über dem Horizont)
    t0 = dt.datetime(2026, 1, 3, 22, 0, tzinfo=dt.timezone.utc)
    for i, f in enumerate(frames):
        f["night"], f["date"] = "2026-01-03", (t0 + dt.timedelta(minutes=3 * i)).isoformat()
    n = sq.evaluate(frames, zp_stack=21.6, zp_device=None, flux_k0=1.0, links=links, excess=0.0, scale=SCALE,
                    site=SITE, ra=100.0, dec=40.0, device_id="stellina")["2026-01-03"]
    assert n["sqm"] is None and n["bortle"] is None and n["sqm_all"] is not None
    assert n["reasons"].get("moon") == 40 and n["reasons"].get("cloud") == 10
    assert "Mond" in n["note"]


def test_unknown_pedestal_gives_no_value():
    frames = _night_frames()
    n = sq.evaluate(frames, zp_stack=21.6, zp_device=None, flux_k0=1.0, links={}, excess=0.0, scale=SCALE,
                    site=SITE, ra=100.0, dec=40.0, device_id="camera")["2026-01-20"]
    assert n["sqm"] is None and n["sqm_all"] is None and n["reasons"]["nosky"] == 40


def test_light_direction_recovers_azimuth():
    """Lichtglocke im Osten-Südosten (110°): Gefälle je Aufnahme = Sockel + natürlicher Anstieg + b · Richtung dorthin."""
    rng = np.random.default_rng(5)
    samples = []
    for g, (az0, alt0) in enumerate([(30, 50), (80, 40), (150, 60), (200, 45), (260, 55), (320, 35), (10, 70)]):
        for k in range(15):
            az, alt = (az0 + 2 * k) % 360, alt0 + rng.normal(0, 2)
            beta = sq._bearing_to_horizon(np.array([alt]), np.array([az]), 110.0)[0]
            gu = 0.002 + 0.8 * float(sq._nat_slope(np.array([alt]))[0]) + 0.004 * math.cos(beta) + rng.normal(0, 4e-4)
            gr = -0.001 + 0.004 * math.sin(beta) + rng.normal(0, 4e-4)
            samples.append([az, alt, gu, gr, f"n{g}"])
    r = sq.light_direction(samples)
    assert r and abs((r["az"] - 110 + 180) % 360 - 180) <= 15 and r["reliable"] and r["compass"] in ("O", "OSO")
    assert len(r["rose"]) == 16 and max(r["rose"]) == 1
    assert sq.light_direction(samples[:10]) is None                          # zu wenig Aufnahmen


def test_api_and_storage(tmp_path, monkeypatch):
    monkeypatch.setenv("ASTROFLOW_GAIA", "0")
    s = load_settings(tmp_path / "data")
    with TestClient(create_app(s, only=("stacking",))) as c:
        d = c.get("/api/stacking/sky").json()
        assert d["sites"] == [] and d["computed"] == 0 and d["job"]["state"] == "idle"
        eng = c.app.state.modules["stacking"].eng
        night = sq.evaluate(_night_frames(), zp_stack=21.6, zp_device=None, flux_k0=1.0,
                            links={f["id"]: (1.0, np.eye(3)) for f in _night_frames()}, excess=0.0, scale=SCALE,
                            site=SITE, ra=100.0, dec=40.0, device_id="stellina")["2026-01-20"]
        pid = eng.db.x("INSERT INTO projects(key, name, created) VALUES('k', 'Testobjekt', 0)")
        eng.db.x("INSERT INTO sky_nights VALUES(?,?,?,?,?,?,?,?)", (pid, "2026-01-20", "x", "Testwiese", "stellina|200.0", 1.0, 1, json.dumps(night)))
        d = c.get("/api/stacking/sky").json()
        assert d["sites"][0]["name"] == "Testwiese" and d["sites"][0]["bortle"]["class"] in range(1, 10)
        assert d["sites"][0]["n_site_nights"] == 1 and d["sites"][0]["light"] is None
        p = c.get(f"/api/stacking/projects/{pid}/sky").json()
        assert p["nights"][0]["sqm"] == night["sqm"] and "dirs" not in p["nights"][0]
        assert c.post("/api/stacking/sky/compute", json={"project_id": 99999}).status_code == 404
        r = c.post("/api/stacking/sky/compute", json={"project_id": pid}).json()
        assert r["state"] in ("running", "done") and r["total"] == 1     # Hintergrundauftrag kann schon fertig sein
        for _ in range(100):
            if not eng.sky.busy():
                break
            import time
            time.sleep(0.05)
        assert not eng.sky.busy()
        assert eng.sky.activity()[0]["title"] == "Himmelsqualität"
        # Ortsliste bekommt den Wert über den Dienst site_sky
        assert c.app.state.modules["stacking"].site_sky()["x"]["sqm"] == night["sqm"]


def test_demo_project_without_solver(tmp_path, monkeypatch):
    """Demodaten ohne Plate-Solving: keine Ausnahme, klarer Grund."""
    monkeypatch.setenv("ASTROFLOW_GAIA", "0")
    from astroflow.modules.stacking import demo
    from astroflow.modules.stacking.db import DB
    from astroflow.modules.stacking.pipeline import Pipeline
    from astroflow.modules.stacking.reviewer import ReviewerConfig
    s = load_settings(tmp_path / "data")
    demo.generate(s.input_dir / "n", n_lights=8, n_darks=0, n_flats=0)
    db = DB(s.db_path)
    pl = Pipeline(s, db, ReviewerConfig())
    pl.scan()
    pid = db.q("SELECT id FROM projects")[0]["id"]
    pl.stack(pid)
    m = json.loads(db.q("SELECT metrics FROM frames WHERE project_id=? AND kind='light'", (pid,))[0]["metrics"])
    assert "bgplane" in m and "sky" in m
    r = sq.compute_project(pl, pid, lambda **k: SITE)
    assert r["nights"] == 0 and "maßstab" in r["reason"].lower()


def test_gaia_with_g_column(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(photometry, "_gavo", lambda *a, with_g=False: seen.append(with_g) or [[1.0, 2.0, 0.5, 11.2]])
    cat = photometry.gaia_stars(10, 20, 0.3, tmp_path, with_g=True)
    assert cat.shape == (1, 4) and seen == [True]
    assert photometry.gaia_stars(10, 20, 0.3, tmp_path, with_g=True).shape == (1, 4) and len(seen) == 1   # eigener Cache


def test_hazy_night_has_no_site_value():
    """Ganze Nacht dunstig: Nullpunkt 0,8 mag unter dem Gerätewert anderer Nächte -> kein Ortswert, Durchsicht gemeldet."""
    frames = _night_frames()
    links = {f["id"]: (1.0, np.eye(3)) for f in frames}
    n = sq.evaluate(frames, zp_stack=20.8, zp_device=21.6 + 0.2, flux_k0=1.0, links=links, excess=0.0, scale=SCALE,
                    site=SITE, ra=100.0, dec=40.0, device_id="stellina")["2026-01-20"]
    assert n["hazy"] and n["sqm"] is None and n["transparency"] > 0.5 and n["reasons"]["cloud"] == 40


def test_without_site_no_site_value():
    """Ohne Standort keine Dämmerungs-/Mond-/Höhenprüfung -> Messwerte ja, Ortswert/Bortle nein."""
    frames = _night_frames()
    n = sq.evaluate(frames, zp_stack=21.6, zp_device=None, flux_k0=1.0, links={f["id"]: (1.0, np.eye(3)) for f in frames},
                    excess=0.0, scale=SCALE, site=None, ra=100.0, dec=40.0, device_id="stellina")["2026-01-20"]
    assert n["sqm"] is None and n["bortle"] is None and n["sqm_all"] is not None and n["reasons"]["nosite"] == 40


def test_deleted_project_leaves_no_measurements(tmp_path, monkeypatch):
    monkeypatch.setenv("ASTROFLOW_GAIA", "0")
    from astroflow.modules.stacking.db import DB
    from astroflow.modules.stacking.pipeline import Pipeline
    from astroflow.modules.stacking.reviewer import ReviewerConfig
    s = load_settings(tmp_path / "data")
    db = DB(s.db_path)
    pl = Pipeline(s, db, ReviewerConfig())
    sq.ensure_table(db)
    pid = db.x("INSERT INTO projects(key, name, created) VALUES('k', 'Weg', 0)")
    db.x("INSERT INTO sky_nights VALUES(?,?,?,?,?,?,?,?)", (pid, "2026-01-20", "x", "Testwiese", "d", 1.0, 1, json.dumps({"sqm": 20.0, "zp": 21.9, "zp_source": "stack"})))
    db.x("INSERT INTO sky_nights VALUES(?,?,?,?,?,?,?,?)", (999, "2026-01-21", "x", "Testwiese", "d", 1.0, 1, json.dumps({"sqm": 18.0})))
    assert len(sq.nights_of(db)) == 1                       # verwaiste Zeile (Objekt 999 gibt es nicht) zählt nicht
    pl.delete_project(pid)
    assert sq.nights_of(db) == [] and db.q("SELECT * FROM sky_nights WHERE project_id=?", (pid,)) == []


def test_dienst_nullpunkte_je_nacht(tmp_path, monkeypatch):
    """`night_zero_points` (für die Satellitenhelligkeit in SkyTrace): Median je Nacht und Gerät, nur Gaia-geeichte Nächte."""
    monkeypatch.setenv("ASTROFLOW_GAIA", "0")
    with TestClient(create_app(load_settings(tmp_path / "data"), only=("stacking",))) as c:
        assert c.app.state.modules["stacking"].night_zero_points() == {"nights": {}, "devices": {}}
        eng = c.app.state.modules["stacking"].eng
        pa = eng.db.x("INSERT INTO projects(key, name, created) VALUES('a', 'A', 0)")
        pb = eng.db.x("INSERT INTO projects(key, name, created) VALUES('b', 'B', 0)")
        for pid, night, zp, src in ((pa, "2026-01-20", 22.0, "stack"), (pb, "2026-01-20", 22.2, "stack"), (pa, "2026-01-21", 21.8, "stack"),
                                    (pa, "2026-01-22", 25.0, "device")):
            eng.db.x("INSERT INTO sky_nights VALUES(?,?,?,?,?,?,?,?)", (pid, night, "x", "Testwiese", "stellina|200.0", 1.0, 1,
                                                                        json.dumps({"zp": zp, "zp_source": src})))
        z = c.app.state.modules["stacking"].night_zero_points()
    assert z["nights"] == {"2026-01-20|stellina|200.0": 22.1, "2026-01-21|stellina|200.0": 21.8}      # 25,0 (Gerätewert) bleibt draußen
    assert z["devices"] == {"stellina|200.0": 21.95}


def _clean_seed(db):
    """Erfundene Nächte an zwei erfundenen Orten für die Auswertung „Saubere Nächte“."""
    sq.ensure_table(db)
    p1 = db.x("INSERT INTO projects(key, name, created) VALUES('k1', 'Objekt A', 0)")
    p2 = db.x("INSERT INTO projects(key, name, created) VALUES('k2', 'Objekt B', 0)")

    def curve(day, vals):
        t0 = dt.datetime.fromisoformat(day + "T20:00:00+00:00")
        return [{"t": "x", "ts": int((t0 + dt.timedelta(minutes=20 * i)).timestamp()), "sqm": v, "flags": []} for i, v in enumerate(vals)]

    def put(pid, night, site, sqm, seeing, loss, err=0.2, curve_v=None, good=30):
        d = {"night": night, "sqm": sqm, "sqm_all": sqm, "err": err, "seeing": seeing, "transparency": loss, "good": good,
             "bortle": sq.bortle(sqm, err) if sqm else None, "curve": curve(night, curve_v) if curve_v else []}
        db.x("INSERT OR REPLACE INTO sky_nights VALUES(?,?,?,?,?,?,?,?)",
             (pid, night, site, {"w": "Testwiese", "g": "Gipfelblick"}[site], "stellina|200.0", 1.0, sq.SKY_VERSION, json.dumps(d)))
    put(p1, "2026-01-10", "w", 20.4, 3.2, 0.1)
    put(p1, "2026-02-14", "w", 20.8, 2.9, 0.05)
    put(p2, "2026-02-14", "w", 21.0, 3.0, 0.07, curve_v=[20.9, 21.0, 21.2, 21.4, 21.3, 21.1, 20.8])       # Mittel der Objekte 20,9
    put(p1, "2026-08-02", "g", 21.9, 2.5, 0.02, curve_v=[21.8, 21.9, 22.0, 22.1, 22.0, 21.9])
    put(p2, "2026-08-20", "g", 21.6, 2.2, 0.3)
    put(p2, "2026-09-05", "g", 22.0, 2.4, 0.04, err=0.5)                                                    # unsicher
    put(p2, "2026-09-06", "g", None, 2.0, 0.6)                                                              # Mond/Dunst: kein Ortswert
    return p1, p2


def test_clean_nights(tmp_path, monkeypatch):
    monkeypatch.setenv("ASTROFLOW_GAIA", "0")
    s = load_settings(tmp_path / "data")
    with TestClient(create_app(s, only=("stacking",))) as c:
        eng = c.app.state.modules["stacking"].eng
        empty = c.get("/api/stacking/sky/clean").json()
        assert empty["dark"] == [] and empty["best_site"] is None and empty["n_nights"] == 0
        _clean_seed(eng.db)
        d = c.get("/api/stacking/sky/clean").json()
    assert [(x["site_name"], x["night"]) for x in d["dark"]][:2] == [("Gipfelblick", "2026-08-02"), ("Gipfelblick", "2026-08-20")]
    assert all(x["sure"] for x in d["dark"]) and "2026-09-05" not in {x["night"] for x in d["dark"]}
    assert [x["night"] for x in d["uncertain"]] == ["2026-09-05"]                       # unsicher: getrennt ausgewiesen
    assert "2026-09-06" not in {x["night"] for x in d["dark"] + d["score"] + d["uncertain"]}     # ohne Ortswert nie in der Dunkelheits-Liste
    n = next(x for x in d["dark"] if x["night"] == "2026-08-02")
    assert n["weekday"] == dt.date(2026, 8, 2).weekday() == 6 and n["month"] == 8 and {o["name"] for o in n["objects"]} == {"Objekt A"}
    w = n["window"]
    assert w["sqm"] == 22.0 and w["from"] < w["to"]                                    # bester Abschnitt statt Einzelspitze
    assert d["seeing"][0]["night"] == "2026-09-06" and d["seeing"][0]["seeing"] == 2.0   # Seeing zählt auch gestörte Nächte
    assert d["clear"][0]["night"] == "2026-08-02" or d["clear"][0]["loss"] == 0.02
    # zwei Objekte in einer Nacht: Median der Objekte, beide verlinkt
    m = next(x for x in d["score"] if x["night"] == "2026-02-14")
    assert m["sqm"] == 20.9 and len(m["objects"]) == 2
    assert all(0 <= x["score"] <= 100 for x in d["score"]) and d["score"][0]["score"] >= d["score"][-1]["score"]
    # bester Ort / Monat
    assert d["best_site"]["name"] == "Gipfelblick"
    g = next(x for x in d["sites"] if x["name"] == "Gipfelblick")
    assert g["best_month"] == 8                      # September hat nur eine Nacht, August zwei
    assert g["months"][7]["sqm"] == 21.75 and g["months"][0]["sqm"] is None and len(g["months"]) == 12
    wz = next(x for x in d["sites"] if x["name"] == "Testwiese")
    assert wz["best_month"] == 2 and d["n_nights"] == 6 and d["n_sure"] == 4


def test_best_window_never_spans_a_disturbance():
    """Gestörte Punkte teilen die Kurve: das dunkelste Fenster liegt in einem ungestörten Lauf, nicht über der Wolke."""
    pts = [{"ts": 60 * i, "sqm": 21.9, "flags": ["cloud"] if i == 3 else []} for i in range(8)]
    w = sq._best_window([{"curve": pts}])
    assert w is not None and not (w["from"] <= 180 <= w["to"])          # Punkt 3 (ts 180) liegt nie im Fenster
    assert (w["from"], w["to"]) in ((240, 420), (0, 120))               # Lauf 4–7 (5 Punkte nicht möglich -> 4 Punkte) bzw. 0–2
    assert sq._best_window([{"curve": [{"ts": 0, "sqm": 21.0, "flags": ["moon"]}]}]) is None
