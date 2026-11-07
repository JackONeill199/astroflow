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
    with pytest.raises(asteroids.AsteroidError, match="zu langsam"):
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
