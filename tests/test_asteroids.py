"""Asteroiden im Bild (Wunsch 10): Vorhersage über eine JPL-Attrappe, Nachweis eines synthetischen wandernden Punkts in den
Einzelbildern (Shift-and-Add entlang der Bahn), Helligkeit über einen festen Nullpunkt, Kennzeichnung ohne Rohbilder,
API, Dashboard und Sage-Erfolg. Kein Netz."""
import json
import math
import shutil

import numpy as np
import pytest
from astropy.wcs import WCS

from astroflow.core.config import load_settings
from astroflow.modules.stacking import asteroids, demo
from astroflow.modules.stacking.db import DB
from astroflow.modules.stacking.pipeline import Pipeline, frame_time, stack_dir
from astroflow.modules.stacking.platesolve import SolverConfig
from astroflow.modules.stacking.reviewer import ReviewerConfig
from astroflow.modules.stacking.stacking import StackState

N, FLUX, SCALE_AS = 36, 900.0, 1.2
RA0, DEC0 = 150.0, 20.0
ZP = 24.0


def _hdr(H, W):
    d = SCALE_AS / 3600
    return {"CTYPE1": "RA---TAN", "CTYPE2": "DEC--TAN", "CRVAL1": RA0, "CRVAL2": DEC0, "CRPIX1": (W + 1) / 2, "CRPIX2": (H + 1) / 2,
            "CD1_1": -d, "CD1_2": 0.0, "CD2_1": 0.0, "CD2_2": d}


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    root = tmp_path_factory.mktemp("asteroid")
    s = load_settings(root / "data")
    s.delete_uploads = False
    _, truth = demo.generate_asteroid(s.input_dir / "feld", n_lights=N, flux=FLUX)
    db = DB(s.db_path)
    pl = Pipeline(s, db, ReviewerConfig(), SolverConfig(backend="off"))
    pl.scan()
    p = db.one("SELECT * FROM projects")
    rid = pl.process(p["id"])
    st = StackState.load(stack_dir(s.work_dir / f"p{p['id']}", None))
    H, W = st.sw.shape[1:]
    (s.work_dir / f"p{p['id']}" / "wcs.json").write_text(json.dumps({"key": f"{st.meta['ref_fid']}|{H}x{W}", "wcs": _hdr(H, W)}))
    return s, db, pl, p, rid, st, truth


def _truth_at(db, st, truth, t):
    """Wahre Lage im Referenzraster (= Stack, Maßstab 1) zur Zeit t – über die Lage der Referenzaufnahme (demo._frame)."""
    rows = db.q("SELECT id, path, info FROM frames WHERE kind='light' ORDER BY path")
    i_ref = [r["id"] for r in rows].index(st.meta["ref_fid"])
    ts = np.array([frame_time(r) for r in rows])
    sky = np.array(truth["sky"])
    k = (t - ts[0]) / (ts[1] - ts[0])
    pos = sky[0] + (sky[1] - sky[0]) * k
    sh, sw = truth["shape"]
    fh, fw = truth["frame"]
    th = np.deg2rad(truth["angle"][i_ref])
    c, s_ = np.cos(th), np.sin(th)
    dx, dy = pos - (np.array([sw / 2, sh / 2]) + np.array(truth["shift"][i_ref]))
    return np.array([c * dx + s_ * dy + fw / 2, -s_ * dx + c * dy + fh / 2])


def _fake_jpl(db, st, truth, offset_as=(3.0, -2.0), extra=()):
    """JPL-Attrappe: der echte Punkt (geozentrisch leicht versetzt), dazu weitere Körper ohne Signal."""
    H, W = st.sw.shape[1:]
    w = WCS({**_hdr(H, W), "NAXIS": 2})
    calls = []

    def fn(when, ra, dec, hw_ra, hw_dec, cache_dir, timeout=60):
        from datetime import datetime, timezone
        t = datetime.fromisoformat(when).replace(tzinfo=timezone.utc).timestamp()
        calls.append(when)
        p0, p1 = _truth_at(db, st, truth, t), _truth_at(db, st, truth, t + 3600)
        (r0, d0), (r1, d1) = (tuple(float(v) for v in w.pixel_to_world_values(*p)) for p in (p0, p1))
        cosd = math.cos(math.radians(d0))
        body = {"name": "(4711) Testoid", "ra": r0 + offset_as[0] / 3600 / cosd, "dec": d0 + offset_as[1] / 3600, "vmag": "17.1", "kind": "a",
                "rate_ra": (r1 - r0) * cosd * 3600, "rate_dec": (d1 - d0) * 3600}
        out = [body]
        for name, (x, y), vmag in extra:
            rr, dd = (float(v) for v in w.pixel_to_world_values(x, y))
            out.append({"name": name, "ra": rr, "dec": dd, "vmag": vmag, "kind": "a", "rate_ra": 120.0, "rate_dec": -40.0})
        return out
    return fn, calls


def test_detects_moving_point(env, monkeypatch):
    s, db, pl, p, rid, st, truth = env
    H, W = st.sw.shape[1:]
    monkeypatch.setattr(asteroids, "_zero_point", lambda *a, **k: {"zp": ZP, "aperture_px": 5.0})
    fn, calls = _fake_jpl(db, st, truth, extra=[("(9999) Leer", (W * 0.7, H * 0.3), "16.0"), ("(12345) Zuschwach", (W * 0.3, H * 0.7), "22.4")])
    out = asteroids.compute(pl, rid, small_bodies=fn, gaia=True)
    assert out["ok"] and out["v"] == asteroids.ASTEROID_VERSION and len(calls) == 1          # eine Nacht → eine Abfrage
    by = {b["name"]: b for b in out["kleinkoerper"]}
    a = by["(4711) Testoid"]
    assert a["status"] == "nachgewiesen", a
    assert a["snr"] >= 8 and a["abweichung"] < 6                     # Versatz 3,6″ der „geozentrischen“ Vorhersage gefunden
    # Helligkeit: Fluss je Aufnahme in Stack-Einheiten (Luminanz, Farbe neutral) → ZP − 2,5 log(F/t)
    expect = ZP - 2.5 * math.log10(FLUX / 10.0)
    assert abs(a["mag"] - expect) < 0.35, (a["mag"], expect)
    assert by["(9999) Leer"]["status"] == "nicht_gefunden"
    assert by["(12345) Zuschwach"]["status"] == "zu_schwach"
    assert out["nachgewiesen"] == 1 and out["im_feld"] == 3
    assert out["schwaechster"]["name"] == "(4711) Testoid"
    assert a.get("bild") and (s.work_dir / f"p{p['id']}" / "asteroiden" / f"r{rid}" / a["bild"]).exists()


def test_static_spot_is_not_an_asteroid(env, monkeypatch):
    """Vorhersage an einer Stelle ohne Bewegung entlang der echten Spur: falsche Bahnrichtung → nicht nachweisbar."""
    s, db, pl, p, rid, st, truth = env
    monkeypatch.setattr(asteroids, "_zero_point", lambda *a, **k: {"zp": ZP, "aperture_px": 5.0})
    fn, _ = _fake_jpl(db, st, truth)

    def wrong(*a, **k):
        b = fn(*a, **k)
        b[0]["rate_ra"], b[0]["rate_dec"] = -b[0]["rate_dec"] * 1.3, b[0]["rate_ra"] * 1.3      # quer zur echten Bahn
        return b
    out = asteroids.compute(pl, rid, small_bodies=wrong, gaia=False)
    a = out["kleinkoerper"][0]
    assert a["status"] != "nachgewiesen" and out["nachgewiesen"] == 0
    assert any("Gaia" in h for h in out["hinweise"])


def test_without_raw_frames_only_prediction(env, tmp_path, monkeypatch):
    s, db, pl, p, rid, st, truth = env
    fn, _ = _fake_jpl(db, st, truth)
    db.x("UPDATE frames SET deleted=1 WHERE project_id=?", (p["id"],))
    try:
        out = asteroids.compute(pl, rid, small_bodies=fn, gaia=False)
    finally:
        db.x("UPDATE frames SET deleted=0 WHERE project_id=?", (p["id"],))
    assert out["nachgewiesen"] == 0 and out["im_feld"] == 1
    assert out["kleinkoerper"][0]["status"] == "keine_rohbilder"
    assert any("nicht mehr erreichbar" in h for h in out["hinweise"])


def test_errors_are_clear(env, monkeypatch):
    s, db, pl, p, rid, st, truth = env
    f = s.work_dir / f"p{p['id']}" / "wcs.json"
    keep = f.read_text()
    f.unlink()
    try:
        with pytest.raises(asteroids.AsteroidError, match="Plate-Solving"):
            asteroids.compute(pl, rid, small_bodies=lambda *a, **k: [], gaia=False)
    finally:
        f.write_text(keep)

    def down(*a, **k):
        raise TimeoutError("timed out")
    with pytest.raises(asteroids.AsteroidError, match="sehr langsam"):
        asteroids.compute(pl, rid, small_bodies=down, gaia=False)


def test_detect_and_predict_units():
    """Shift-and-Add: Punkt in jedem Ausschnitt leicht verrauscht → Fund; reines Rauschen → kein Fund."""
    rng = np.random.default_rng(1)
    half = 30
    gx, gy = asteroids.stamp_grid(half)
    psf = 60 * np.exp(-((gx - 4) ** 2 + (gy + 3) ** 2) / (2 * 1.5 ** 2))
    stamps = [psf + rng.normal(0, 10, psf.shape) for _ in range(30)]
    d = asteroids.detect(stamps, 3.5, 12, 5)
    assert d["found"] and abs(d["dx"] - 4) < 0.6 and abs(d["dy"] + 3) < 0.6
    noise = [rng.normal(0, 10, psf.shape) for _ in range(30)]
    assert not asteroids.detect(noise, 3.5, 12, 5)["found"]
    # Vorhersage: Körper außerhalb des Bildes fallen weg, Rate → px/s
    w = WCS({**_hdr(200, 300), "NAXIS": 2})
    bodies = [{"name": "a", "ra": RA0, "dec": DEC0, "vmag": "15", "rate_ra": 36.0, "rate_dec": 0.0},
              {"name": "b", "ra": RA0 + 1, "dec": DEC0, "vmag": "15", "rate_ra": 1.0, "rate_dec": 0.0}]
    pr = asteroids.predict(bodies, w, (200, 300), None, 1000.0, 0.0, 2000.0)
    assert [b["name"] for b in pr] == ["a"]
    assert abs(abs(pr[0]["_vx"]) * 3600 - 30.0) < 0.5 and abs(pr[0]["_vy"]) < 0.05       # 36″/h bei 1,2″/px


def test_api_dashboard_and_sage(env, tmp_path, monkeypatch):
    """Auftrag über die Insights-Warteschlange, Endpunkte, Dashboard-Rekord, Sage-Erfolg."""
    from fastapi.testclient import TestClient

    from astroflow.server import create_app
    s, db, pl, p, rid, st, truth = env
    data = tmp_path / "data"
    shutil.copytree(s.data_dir, data)
    # absolute Pfade in der Kopie auf die Kopie umbiegen (Rohbilder, Ausgaben)
    db2 = DB(data / "astroflow.db")
    for r in db2.q("SELECT id, path FROM frames"):
        db2.x("UPDATE frames SET path=? WHERE id=?", (r["path"].replace(str(s.data_dir), str(data)), r["id"]))
    for r in db2.q("SELECT id, files FROM results"):
        db2.x("UPDATE results SET files=? WHERE id=?", (r["files"].replace(json.dumps(str(s.data_dir))[1:-1], json.dumps(str(data))[1:-1]), r["id"]))
    fn, _ = _fake_jpl(db2, st, truth)
    from astroflow.modules.stacking import compare
    monkeypatch.setattr(compare, "small_bodies", fn)
    monkeypatch.setattr(asteroids, "_zero_point", lambda *a, **k: {"zp": ZP, "aperture_px": 5.0})
    monkeypatch.setenv("ASTROFLOW_AUTO", "0")
    monkeypatch.setenv("ASTROFLOW_MODULES", "stacking,sage")
    s2 = load_settings(data)
    with TestClient(create_app(s2, only=("stacking", "sage"))) as c:
        g = c.get(f"/api/stacking/results/{rid}/asteroids").json()
        assert g["state"] == "none" and g["solved"] and g["enabled"]
        c.post(f"/api/stacking/storage", json={"gaia": True})
        assert c.post(f"/api/stacking/results/{rid}/asteroids").json()["state"] in ("queued", "running")
        import time
        for _ in range(600):
            g = c.get(f"/api/stacking/results/{rid}/asteroids").json()
            if g["state"] not in ("queued", "running"):
                break
            time.sleep(0.1)
        assert g["state"] == "done", g
        assert g["nachgewiesen"] == 1
        name = next(b["bild"] for b in g["kleinkoerper"] if b.get("bild"))
        assert c.get(f"/api/stacking/results/{rid}/asteroids/{name}").status_code == 200
        for bad in ("..%2Fwcs.json", "x.png", "a1.jpg"):
            assert c.get(f"/api/stacking/results/{rid}/asteroids/{bad}").status_code == 404
        # Projektansicht bleibt schlank
        assert "asteroids" not in json.dumps(c.get(f"/api/stacking/projects/{p['id']}").json().get("results", [{}])[0].get("stats", {}))
        d = c.get("/api/stacking/analysis").json()
        assert d["totals"]["asteroids"] == 1 and d["records"]["faint_asteroid"]["body"] == "(4711) Testoid"
        assert d["coverage"]["asteroids"].get("done") == 1 and d["asteroids"][0]["name"] == "(4711) Testoid"
        # Sage: Erfolg „Kleinplanetenjäger“ (Ereignis stacking.asteroids → Erfolge prüfen, eigener Thread)
        for _ in range(100):
            resp = c.get("/api/sage/discoveries")
            ach = {a["id"]: a for a in resp.json().get("achievements") or []}
            if (ach.get("asteroid_first") or {}).get("unlocked"):
                break
            time.sleep(0.1)
        assert ach, (resp.status_code, resp.text[:300])
        assert ach["asteroid_first"]["unlocked"] and not ach["asteroids_10"]["unlocked"]
        # ausgeschaltet → klare Meldung
        c.post("/api/stacking/insights", json={"small_bodies": False})
        assert c.post(f"/api/stacking/results/{rid}/asteroids").status_code == 400


def test_sensor_maps_reach_detection_frames(env, tmp_path):
    """Review PR #67: Einzelbilder bekommen dieselbe Fehlerkarte wie der Master-Stack (Pipeline._load wendet sie nur aus dem Cache an)."""
    from astroflow.modules.stacking import calibration
    s, db, pl, p, rid, st, truth = env
    rows = asteroids._frames(db, p["id"], set())
    key = calibration.GlowLibrary.key(rows[0]["infod"])
    dlib = calibration.DefectLibrary(s.work_dir / "defects")
    had = dlib.get(key, rows[0]["night"])[0]
    dlib.add(key, rows[0]["night"], np.array([5, 77, 1234], np.int64))
    try:
        cache = {}
        asteroids._sensor_cache(pl, p["id"], rows[:3], rows[0]["night"], cache)
        assert list(cache[("defects", key, rows[0]["night"])]) == [5, 77, 1234]
    finally:
        dlib.add(key, rows[0]["night"], had if had is not None else np.zeros(0, np.int64))


def test_dashboard_counts_all_detections():
    """Review PR #67: Zählung und Rekord aus allen Funden, nicht nur aus den ersten 30."""
    from astroflow.modules.stacking import dashboard
    found = [{"name": f"({i}) Fund", "status": "nachgewiesen", "mag": 14.0 + i * 0.1} for i in range(60)]
    info = dashboard._asteroid_info({"nachgewiesen": 60, "im_feld": 60, "kleinkoerper": found})
    assert len(info["items"]) == 60 and max(b["mag"] for b in info["items"]) == pytest.approx(19.9)


# ------------------------------------------- JPL schnell abfragen (Fehlerbericht 07.11.2026, 0.43.2) #
class _Resp:
    def __init__(self, j):
        self.j = j

    def raise_for_status(self):
        pass

    def json(self):
        return self.j


def _sb_answer(second: bool, rows):
    f = ["Object name", "Astrometric RA (hh:mm:ss)", "Astrometric Dec (dd mm'ss\")", "Visual magnitude (V)", "RA rate (\"/h)", "Dec rate (\"/h)",
         "Est. error RA (\")", "Est. error Dec (\")"]
    return {"fields_second" if second else "fields_first": f, "data_second_pass" if second else "data_first_pass": rows}


def test_small_bodies_fast_first_pass_and_failure_pause(tmp_path, monkeypatch):
    """Asteroiden nur im ersten Durchgang (der zweite brauchte 127 s), Kometen genau; nach Zeitüberschreitung Pause mit
    ehrlicher Meldung statt erneuter Abfrage."""
    import httpx

    from astroflow.modules.stacking import compare
    seen = []

    def get(url, params=None, timeout=None):
        seen.append(dict(params))
        if params["sb-kind"] == "c":
            return _Resp(_sb_answer(True, [["C/2026 A1", "10:00:00.00", "+20 00 00.0", "14.0", "30.0", "-5.0", "1", "1"]]))
        return _Resp(_sb_answer(False, [["433 Eros (A898 PA)", "10:00:10.00", "+20 01 00.0", "12.1", "40.0", "2.0", "3", "4"]]))
    monkeypatch.setattr(httpx, "get", get)
    out = compare.small_bodies("2025-04-10T22:00:00", 150.0, 20.0, 0.5, 0.35, tmp_path)
    by = {p["sb-kind"]: p for p in seen}
    assert by["c"].get("two-pass") == "true" and "two-pass" not in by["a"]
    eros = next(b for b in out if b["name"].startswith("433"))
    assert eros["genau"] is False and eros["rate_ra"] == 40.0 and abs(eros["ra"] - 150.0417) < 1e-3
    assert eros["fehler"] == pytest.approx(5.0)
    assert next(b for b in out if b["kind"] == "c")["genau"] is True
    n = len(seen)
    assert compare.small_bodies("2025-04-10T22:00:00", 150.0, 20.0, 0.5, 0.35, tmp_path) == out and len(seen) == n   # Zwischenspeicher

    def slow(url, params=None, timeout=None):
        seen.append(dict(params))
        raise httpx.ReadTimeout("timed out")
    monkeypatch.setattr(httpx, "get", slow)
    with pytest.raises(compare.SmallBodyError, match="sehr langsam"):
        compare.small_bodies("2025-04-11T22:00:00", 150.0, 20.0, 0.5, 0.35, tmp_path)
    n = len(seen)
    with pytest.raises(compare.SmallBodyError, match="Nächster Versuch"):           # Pause: keine neue Abfrage
        compare.small_bodies("2025-04-12T22:00:00", 151.0, 20.0, 0.5, 0.35, tmp_path)
    assert len(seen) == n
    assert "sehr langsam" in compare.jpl_message(TimeoutError("timed out"))


def test_horizons_command_and_refine(monkeypatch, tmp_path):
    from astroflow.modules.stacking import comet
    assert asteroids.horizons_command("433 Eros (A898 PA)") == "433;"
    assert asteroids.horizons_command("(4711) Testoid") == "4711;"
    assert asteroids.horizons_command("2004 MN4") == "DES=2004 MN4;"
    # SB-Ident schreibt vorläufige Bezeichnungen in Klammern – nicht als Nummer lesen (sonst falscher Körper)
    assert asteroids.horizons_command("(1927 LA)") == "DES=1927 LA;"
    assert asteroids.horizons_command("(1988 RH9)") == "DES=1988 RH9;"
    assert asteroids.horizons_command("(1991 BA)") == "DES=1991 BA;"
    assert asteroids.horizons_command("(4711)") == "4711;"
    assert asteroids.horizons_command("Eros") == "Eros;"
    # Suchkreis nach JPLs Fehlerschätzung: 15″ + 3 × Fehler, höchstens 60″
    assert asteroids.rough_search(None, 10.0, 1.2) == pytest.approx(50.0)
    assert asteroids.rough_search(5.0, 10.0, 1.2) == pytest.approx(25.0)
    assert asteroids.rough_search(40.0, 10.0, 1.2) == pytest.approx(50.0)
    w = WCS({**_hdr(200, 300), "NAXIS": 2})
    calls = []

    def hz(desig, t0, t1, cache_dir, timeout=25, command=None):
        calls.append(command)
        t = np.arange(t0 - 600, t1 + 601, 120.0)
        return np.c_[t, RA0 + (t - 1000) * 36 / 3600 / 3600 / math.cos(math.radians(DEC0)), np.full_like(t, DEC0), np.full_like(t, 1.5)]
    monkeypatch.setattr(comet, "horizons", hz)
    monkeypatch.setattr(comet, "horizons_enabled", lambda: True)
    b = {"name": "433 Eros (A898 PA)", "genau": False, "_x": 0.0, "_y": 0.0, "_vx": 0.0, "_vy": 0.0}
    left = asteroids.refine_all([b], w, 1000.0, 0.0, 2000.0, tmp_path, 60.0)
    assert calls == ["433;"] and b["genau"] and left > 0
    x0, y0 = (float(v) for v in w.world_to_pixel_values(RA0, DEC0))
    assert abs(b["_x"] - x0) < 0.05 and abs(b["_y"] - y0) < 0.05
    assert abs(abs(b["_vx"]) * 3600 - 30.0) < 0.3 and abs(b["_vy"]) < 0.01            # 36″/h bei 1,2″/px


def test_rough_first_pass_found_with_wider_search(env, monkeypatch):
    """Ohne Horizons (Tests) bleibt die Bahn genähert: 30″ daneben wird mit dem größeren Suchkreis trotzdem gefunden."""
    s, db, pl, p, rid, st, truth = env
    monkeypatch.setattr(asteroids, "_zero_point", lambda *a, **k: {"zp": ZP, "aperture_px": 5.0})
    fn, _ = _fake_jpl(db, st, truth, offset_as=(24.0, -18.0))

    def rough(*a, **k):
        return [dict(b, genau=False) for b in fn(*a, **k)]
    out = asteroids.compute(pl, rid, small_bodies=rough, gaia=True)
    a = out["kleinkoerper"][0]
    assert a["status"] == "nachgewiesen" and 20 < a["abweichung"] < 40, a
    assert not a["genau"] and any("genäherter Bahn" in h for h in out["hinweise"])


def test_rough_edge_candidate_survives(env, monkeypatch):
    """Review PR #73: liegt die genäherte Lage knapp außerhalb des Bildes, darf der Körper nicht vor der Suche wegfallen;
    ein genäherter Körper weit außerhalb und ein Randkandidat ohne Fund erscheinen nicht in der Liste."""
    s, db, pl, p, rid, st, truth = env
    H, W = st.sw.shape[1:]
    w = WCS({**_hdr(H, W), "NAXIS": 2})
    pr = asteroids.predict([{"name": "rand", "ra": float(w.pixel_to_world_values(W + 10, H / 2)[0]), "dec": float(w.pixel_to_world_values(W + 10, H / 2)[1]),
                             "vmag": "15", "rate_ra": 0.0, "rate_dec": 0.0, "genau": False, "fehler": 20.0},
                            {"name": "weit", "ra": float(w.pixel_to_world_values(W + 400, H / 2)[0]), "dec": float(w.pixel_to_world_values(W + 400, H / 2)[1]),
                             "vmag": "15", "rate_ra": 0.0, "rate_dec": 0.0, "genau": False, "fehler": 20.0},
                            {"name": "rand_genau", "ra": float(w.pixel_to_world_values(W + 10, H / 2)[0]), "dec": float(w.pixel_to_world_values(W + 10, H / 2)[1]),
                             "vmag": "15", "rate_ra": 0.0, "rate_dec": 0.0}],
                           w, (H, W), None, 1000.0, 0.0, 2000.0)
    assert [b["name"] for b in pr] == ["rand"] and pr[0]["_edge"]
    # Ende zu Ende: echter Punkt 30″ außerhalb vorhergesagt (genähert) → im erweiterten Kreis gesucht und gefunden;
    # ein zusätzlicher Randkandidat ohne Signal fällt am Ende weg
    monkeypatch.setattr(asteroids, "_zero_point", lambda *a, **k: {"zp": ZP, "aperture_px": 5.0})
    fn, _ = _fake_jpl(db, st, truth, offset_as=(24.0, -18.0))

    def rough(*a, **k):
        out = [dict(b, genau=False) for b in fn(*a, **k)]
        rr, dd = (float(v) for v in w.pixel_to_world_values(W + 8, H * 0.2))
        out.append({"name": "(1927 LA)", "ra": rr, "dec": dd, "vmag": "16.0", "kind": "a", "rate_ra": 120.0, "rate_dec": -40.0,
                    "genau": False, "fehler": 15.0})
        return out
    out = asteroids.compute(pl, rid, small_bodies=rough, gaia=True)
    names = [b["name"] for b in out["kleinkoerper"]]
    assert "(4711) Testoid" in names and "(1927 LA)" not in names
    assert out["naechte"][0]["im_feld"] == len(out["kleinkoerper"])
