"""Kometenmodus: Erkennung, Bahn (Bildsuche, Ephemeride als Attrappe), zwei Stacks, Zusammensetzen, Tutor-Hinweis."""
import json

import numpy as np
import pytest

from astroflow.core.config import load_settings
from astroflow.modules.stacking import comet, demo, registration, scanner, tutor
from astroflow.modules.stacking.db import DB
from astroflow.modules.stacking.fits_io import FrameInfo
from astroflow.modules.stacking.pipeline import Pipeline, frame_time
from astroflow.modules.stacking.platesolve import SolverConfig
from astroflow.modules.stacking.reviewer import ReviewerConfig
from astroflow.modules.stacking.stacking import StackState

N = 24
RATE = (1.1, 0.45)                       # Himmelspixel je Aufnahme (demo.generate_comet)


@pytest.mark.parametrize("name,desig", [
    ("C 2022 E3", "C/2022 E3"), ("C_2022_E3", "C/2022 E3"), ("C/2022 E3 (ZTF)", "C/2022 E3"),
    ("Komet C/2023 A3 Tsuchinshan-ATLAS", "C/2023 A3"), ("P/2019 LD2", "P/2019 LD2"), ("12P", "12P"),
    ("12P/Pons-Brooks", "12P"), ("0029P", "29P"), ("Komet", None), ("Comet Leonard", None),
])
def test_designation(name, desig):
    assert comet.is_comet(name)
    assert comet.designation(name) == desig


@pytest.mark.parametrize("name", ["M 31", "IC 1396 Elefantenrüssel", "NGC 2024 Flammennebel", "NGC7000", "Sh2-155", "M101"])
def test_no_false_comets(name):
    assert not comet.is_comet(name)


def test_comet_project_per_night():
    i = FrameInfo(path="x.fits", shape=(360, 480), object="C 2022 E3", instrument="Stellina")
    a = scanner.assign_project(i, {}, "2023-01-29")
    b = scanner.assign_project(i, {}, "2023-01-30")
    assert a.kind == "comet" and a.name == "C/2022 E3" and a.key != b.key
    # Kometenprojekte ziehen keine Aufnahmen über Koordinaten an sich
    j = FrameInfo(path="y.fits", shape=(360, 480), ra=10.0, dec=20.0, instrument="Stellina")
    c = scanner.assign_project(j, {a.key: (10.0, 20.0, 1.0)}, "2023-01-29")
    assert c.key != a.key


def test_mode_option():
    assert comet.active({"name": "C/2022 E3", "kind": "comet"}, {})
    assert not comet.active({"name": "C/2022 E3", "kind": "comet"}, {"comet_mode": "off"})
    assert comet.active({"name": "M 31", "kind": "galaxy"}, {"comet_mode": "on"})
    assert not comet.active({"name": "M 31", "kind": "galaxy"}, {"comet_mode": "auto"})


def test_track_fit_and_helpers():
    t = np.arange(20) * 15.0
    xy = np.stack([100 + 0.1 * t, 50 - 0.03 * t], 1)
    xy[5] += 30                                                  # Ausreißer
    tr, keep = comet.robust_line(t, xy)
    assert not keep[5] and keep.sum() == 19
    assert np.allclose(tr.at(t[[0, -1]]), xy[[0, -1]], atol=1e-6)
    tr2 = comet.Track.from_dict(json.loads(json.dumps(tr.to_dict())))
    assert np.allclose(tr2.at(t), tr.at(t))
    # gebinnt <-> voll
    assert np.allclose(comet.scale_track(comet.unscale_track(tr, 4), 4).at(t), tr.at(t))
    m = np.zeros((10, 12), bool)
    m[2, 3] = True
    assert comet.shift_mask(m, 2, 1)[3, 5] and comet.shift_mask(m, -20, 0).sum() == 0
    lane = comet.corridor((60, 80), np.array([[10.0, 10.0], [70.0, 50.0]]), 3.0)
    assert lane[30, 40] and not lane[50, 10]


HZ = """*******
 Date__(UT)__HR:MN, , , R.A.___(ICRF), DEC____(ICRF),             delta,     deldot,
*******
$$SOE
 2023-Jan-29 21:30, , , 170.012362430,  80.023080075,  0.29925853839886,-18.0959645,
 2023-Jan-29 22:00, , , 169.307988019,  80.052044074,  0.29904149930527,-17.9775447,
 2023-Jan-29 22:30, , , 168.598587771,  80.079558412,  0.29882588684676,-17.8588539,
 2023-Jan-29 23:00, , , 167.884329228,  80.105603846,  0.29861170427616,-17.7398934,
$$EOE
"""


def test_horizons_parse_cache_and_parallax(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setenv("ASTROFLOW_HORIZONS", "1")
    monkeypatch.setattr(comet, "_http_get", lambda params, timeout: calls.append(params) or {"result": HZ})
    t0 = comet.parse_horizons(HZ)[0, 0]
    rows = comet.horizons("C/2022 E3", t0 + 600, t0 + 3000, tmp_path)
    rows2 = comet.horizons("C/2022 E3", t0 + 600, t0 + 3000, tmp_path)
    assert len(calls) == 1 and np.allclose(rows, rows2) and rows.shape == (4, 4)
    assert "DES=C/2022 E3" in calls[0]["COMMAND"] and "SITE_COORD" not in calls[0]   # Standort verlässt den Rechner nie
    t = np.linspace(t0 + 600, t0 + 3000, 5)
    ra_g, dec_g, topo_g = comet.topocentric(rows, t, None)
    ra_t, dec_t, topo_t = comet.topocentric(rows, t, [40.0, -3.0])
    assert not topo_g and topo_t
    sep = np.hypot((ra_t - ra_g) * np.cos(np.radians(dec_g)), dec_t - dec_g) * 3600
    assert 5 < sep.min() and sep.max() < 60                     # Parallaxe bei 0,3 AU: einige 10″
    with pytest.raises(ValueError):
        comet.parse_horizons("No matches found.")


def test_horizons_off(tmp_path, monkeypatch):
    monkeypatch.setenv("ASTROFLOW_HORIZONS", "0")
    with pytest.raises(RuntimeError):
        comet.horizons("C/2022 E3", 0, 100, tmp_path)


# --------------------------------------------------------------------------- Pipeline #
def _truth(db, A):
    """Wahre Kometenposition im Referenzraster je Aufnahme (Erzeugung von demo.generate_comet nachgerechnet)."""
    rows = db.q("SELECT * FROM frames WHERE kind='light' ORDER BY path")
    rng = np.random.default_rng(3)
    demo._sky(rng)
    sh, sw = 620, 800
    c0 = np.array([sw / 2 - 0.5 * RATE[0] * N, sh / 2 - 0.5 * RATE[1] * N])
    ref_xy = np.array([[s[0], s[1]] for s in A.meta["ref_stars"]])
    out = {}
    for i, r in enumerate(rows):
        shift = rng.normal(0, 3, 2)
        rng.normal(3.6, 0.15)
        rng.normal(0, 12, (demo.H, demo.W))
        th = np.deg2rad(i * 0.25)
        R = np.array([[np.cos(th), -np.sin(th)], [np.sin(th), np.cos(th)]])
        p = R.T @ (c0 + np.array(RATE) * i - (np.array([sw / 2, sh / 2]) + shift)) + np.array([demo.W / 2, demo.H / 2])
        m = json.loads(r["metrics"])
        M = registration.find_transform(ref_xy, np.array([[s[0], s[1]] for s in m["stars"]]))[0]
        out[r["id"]] = (frame_time(r), M[:2, :2] @ p + M[:2, 2])
    return out


@pytest.fixture(scope="module")
def comet_env(tmp_path_factory):
    root = tmp_path_factory.mktemp("komet")
    s = load_settings(root / "data")
    s.delete_uploads = False
    demo.generate_comet(s.input_dir / "komet", n_lights=N)
    db = DB(s.db_path)
    pl = Pipeline(s, db, ReviewerConfig(), SolverConfig(backend="off"))
    pl.scan()
    p = db.one("SELECT * FROM projects")
    rid = pl.process(p["id"])
    return s, db, pl, p, rid


def test_comet_stacks_and_composite(comet_env):
    s, db, pl, p, rid = comet_env
    assert p["kind"] == "comet" and p["name"] == "C/2026 A1"
    info = pl.comet_info(p["id"])
    assert info["source"] == "bild" and info["frames"] == N and not info.get("error")
    wd = s.work_dir / f"p{p['id']}"
    A, B = StackState.load(wd / "stack"), StackState.load(wd / "stack_comet")
    assert B.meta["ref_fid"] == A.meta["ref_fid"] and len(B.meta["frames"]) == N
    # Bahn stimmt mit der Simulation überein (Fehler unter einem Pixel über die ganze Serie)
    truth = _truth(db, A)
    tr = comet.Track.from_dict(B.meta["comet"])
    err = [np.hypot(*(tr.at(t) - xy)) for t, xy in truth.values()]
    assert max(err) < 1.2, err
    # Kern im Kometen-Stack scharf und hell, im Sterne-Stack verschmiert
    x, y = (int(round(v)) for v in info["nucleus"])
    a, b = A.result()[0].mean(0), B.result()[0].mean(0)
    win = (slice(y - 3, y + 4), slice(x - 3, x + 4))
    assert b[win].max() > 1.8 * a[win].max()
    # Sterne fehlen im Kometen-Stack: an hellen Sternen des Sterne-Stacks ist er dunkel
    ys, xs = np.unravel_index(np.argsort(a.ravel())[-400:], a.shape)
    far = np.hypot(xs - x, ys - y) > 40
    assert np.median(b[ys[far], xs[far]]) < 0.15 * np.median(a[ys[far], xs[far]])
    r = db.one("SELECT files, stats FROM results WHERE id=?", (rid,))
    files, stats = json.loads(r["files"]), json.loads(r["stats"])
    assert {"jpg", "stars", "comet", "comet_fits"} <= set(files)
    assert stats["profile"] == "comet" and stats["comet"]["composite"]
    assert stats["params"]["scnr"] == 0                          # Grün der Koma bleibt


def test_comet_rerun_is_incremental(comet_env):
    s, db, pl, p, rid = comet_env
    wd = s.work_dir / f"p{p['id']}"
    before = (wd / "stack_comet" / "sw.npy").stat().st_mtime_ns
    pl.stack(p["id"])
    assert (wd / "stack_comet" / "sw.npy").stat().st_mtime_ns == before


def test_ephemeris_prior_is_refined(comet_env, monkeypatch, tmp_path):
    """Ephemeride (Attrappe) mit 3 px Versatz: der Bildabgleich korrigiert ihn."""
    s, db, pl, p, rid = comet_env
    wd = s.work_dir / f"p{p['id']}"
    good = comet.Track.from_dict(StackState.load(wd / "stack_comet").meta["comet"])
    monkeypatch.setenv("ASTROFLOW_HORIZONS", "1")
    monkeypatch.setattr(Pipeline, "_stack_wcs", lambda self, proj, A, mean, log: {"fake": True})
    monkeypatch.setattr(comet, "ephemeris_pixels", lambda desig, t, hdr, scale, site, cache: (good.at(t) + (3.0, -2.0), False))
    import shutil
    shutil.rmtree(wd / "stack_comet")
    pl.stack(p["id"])
    info = pl.comet_info(p["id"])
    assert info["source"] == "horizons+bild" and info["designation"] == "C/2026 A1"
    assert 2.5 < info["offset_px"] < 4.5
    tr = comet.Track.from_dict(StackState.load(wd / "stack_comet").meta["comet"])
    t = np.array([v[0] for v in _truth(db, StackState.load(wd / "stack")).values()])
    assert np.max(np.hypot(*(tr.at(t) - good.at(t)).T)) < 1.0


def test_comet_mode_off(tmp_path):
    s = load_settings(tmp_path / "data")
    demo.generate_comet(s.input_dir / "komet", n_lights=12, seed=5)
    db = DB(s.db_path)
    pl = Pipeline(s, db, ReviewerConfig(), SolverConfig(backend="off"))
    pl.scan()
    p = db.one("SELECT * FROM projects")
    db.x("UPDATE projects SET options=? WHERE id=?", (json.dumps({"comet_mode": "off"}), p["id"]))
    rid = pl.process(p["id"])
    files = json.loads(db.one("SELECT files FROM results WHERE id=?", (rid,))["files"])
    assert "comet" not in files and not (s.work_dir / f"p{p['id']}" / "stack_comet").exists()
    assert pl.comet_info(p["id"]) is None


def test_tutor_warns_about_motion():
    cm = {"active": True, "per_exposure_px": 6.0, "fwhm_px": 3.5, "exptime_s": 10.0}
    f = tutor.findings({"issues": []}, [], {"smart": True}, {"comet": cm})
    hit = [x for x in f if x["code"] == "comet_motion"]
    assert hit and hit[0]["level"] == "warn" and "2 s" in hit[0]["tips"][0]
    calm = tutor.findings({"issues": []}, [], {}, {"comet": {**cm, "per_exposure_px": 0.8}})
    assert not [x for x in calm if x["code"] == "comet_motion"]
