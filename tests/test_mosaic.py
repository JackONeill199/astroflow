"""Mosaike: Erkennung, Leinwand, Hintergrundangleich, Zuschnitt und Ende zu Ende mit einem synthetischen 2×2-Raster."""
import json
import shutil

import numpy as np
import pytest

from astroflow.core.config import load_settings
from astroflow.modules.stacking import demo, mosaic, skyquality
from astroflow.modules.stacking.db import DB
from astroflow.modules.stacking.pipeline import Pipeline
from astroflow.modules.stacking.reviewer import ReviewerConfig

H, W = 2080, 3072


def _T(dx, dy, deg=0.0, cx=(W - 1) / 2, cy=(H - 1) / 2):
    """Aufnahme → Referenz: Drehung um die Bildmitte, dann Verschiebung."""
    a = np.radians(deg)
    R = np.array([[np.cos(a), -np.sin(a), 0], [np.sin(a), np.cos(a), 0], [0, 0, 1]])
    C = np.array([[1, 0, cx], [0, 1, cy], [0, 0, 1]], float)
    return np.array([[1, 0, dx], [0, 1, dy], [0, 0, 1]], float) @ C @ R @ np.linalg.inv(C)


def test_plan_detects_grid_like_stellina():
    rng = np.random.default_rng(1)
    pos = [(x, y) for x in (-830, -415, 0, 415, 830) for y in (-415, 0, 415)]
    Ms = {f"f{i}": _T(*pos[i % len(pos)] + rng.normal(0, 5, 2)) for i in range(60)}
    p = mosaic.plan(Ms, (H, W))
    assert p is not None
    Hc, Wc = p["canvas"]
    assert Wc > W + 1500 and Hc > H + 700
    assert p["origin"][0] < -800 and p["origin"][1] < -350
    assert p["mosaic"]["area"] > 1.6 and p["mosaic"]["frames"] == 60


@pytest.mark.parametrize("Ms", [
    {f"f{i}": _T(*np.random.default_rng(i).normal(0, 25, 2)) for i in range(40)},                 # Dithering
    {f"f{i}": _T(4.0 * i, -2.0 * i) for i in range(40)},                                             # Drift (160 px)
    {f"f{i}": _T(0, 0, deg=1.2 * i) for i in range(40)},                                             # Bildfeldrotation (47°)
])
def test_plan_keeps_ordinary_stacks(Ms):
    assert mosaic.plan(Ms, (H, W)) is None
    assert mosaic.plan(Ms, (H, W), "off") is None


def test_plan_on_forces_canvas_and_caps_memory():
    Ms = {f"f{i}": _T(*np.random.default_rng(i).normal(0, 25, 2)) for i in range(40)}
    p = mosaic.plan(Ms, (H, W), "on")
    assert p is not None and p["canvas"][0] >= H - 16 and p["canvas"][1] >= W - 16
    # riesiges Raster: Leinwand bleibt unter der Grenze, die gut abgedeckte Mitte bleibt drin
    pos = [(x, y) for x in range(-6000, 6001, 1500) for y in range(-4000, 4001, 1000)]
    Ms = {f"g{i}": _T(*pos[i % len(pos)]) for i in range(3 * len(pos))}
    Ms.update({f"c{i}": _T(0, 0) for i in range(40)})                       # Mitte viel öfter belichtet
    p = mosaic.plan(Ms, (H, W))
    Hc, Wc = p["canvas"]
    assert Hc * Wc <= mosaic.MAX_AREA * H * W * 1.01
    ox, oy = p["origin"]
    assert ox <= 0 <= ox + Wc - W and oy <= 0 <= oy + Hc - H


def test_center_ref_and_order():
    pos = {"a": (-800, -400), "b": (0, 0), "c": (800, 400), "d": (400, 0)}
    Ms = {k: _T(*v) for k, v in pos.items()}
    assert mosaic.center_ref(Ms, (H, W), ["a", "c", "b", "d"]) == "b"
    Mb = mosaic.rebase(Ms, "b")
    assert np.allclose(Mb["b"], np.eye(3))
    assert mosaic.order(Mb, (H, W), ["c", "a", "d", "b"]) == ["b", "d", "c", "a"] or \
        mosaic.order(Mb, (H, W), ["c", "a", "d", "b"]) == ["b", "d", "a", "c"]


def test_match_background_removes_planes_and_offsets():
    rng = np.random.default_rng(1)
    h, w = 400, 600
    yy, xx = np.mgrid[0:h, 0:w]
    sky = 60 * np.exp(-((xx - 420) / 120) ** 2 - ((yy - 180) / 120) ** 2)          # Nebel ungleich verteilt
    boxes = [(0, 260, 0, 380), (0, 260, 220, 600), (140, 400, 0, 380), (140, 400, 220, 600)]
    X = np.zeros((4, 1, h, w), np.float32)
    V = np.zeros((4, h, w), bool)
    for b, (y0, y1, x0, x1) in enumerate(boxes):
        V[b, y0:y1, x0:x1] = True
        plane = 25 * b + 8 * b * (xx - x0) / 380 - 5 * b * (yy - y0) / 260
        X[b, 0] = np.where(V[b], sky + plane + rng.normal(0, 2, (h, w)), 0)
        X[b, 0] -= np.median(X[b, 0][V[b]])                        # wie der Warp: eigener Median (hängt am Nebel)
    how = mosaic.match_background(X, V, None, None, 1)
    assert how[0] is None and all(h_ == "plane" for h_ in how[1:])
    for b in range(1, 4):
        d = (X[b, 0] - sky)[V[b]] - np.median((X[0, 0] - sky)[V[0]])
        assert abs(np.median(d)) < 0.3 and np.percentile(np.abs(d), 95) < 5, b


def test_effective_cov_maps_depth_threshold():
    cov = np.zeros((300, 400), np.float32)
    cov[:, :] = 0.3                                             # Mosaik-Ränder: 30 % der größten Tiefe
    cov[100:200, 130:270] = 1.0                                 # tiefe Mitte
    out, info = mosaic.effective_cov(cov, "wide")
    assert info["min_depth"] <= 0.3 and info["crop_area"] > 0.9
    from astroflow.modules.stacking.processing import coverage_box
    y0, y1, x0, x1 = coverage_box(out)
    assert (y1 - y0) * (x1 - x0) > 0.9 * cov.size
    out, info = mosaic.effective_cov(cov, "compact")
    y0, y1, x0, x1 = coverage_box(out)
    assert info["min_depth"] == 0.5 and (y1 - y0) * (x1 - x0) < 0.15 * cov.size
    assert mosaic.mode("an") == "on" and mosaic.mode(None) == "auto" and mosaic.crop_mode("kompakt") == "compact"


# --------------------------------------------------------------------- Ende zu Ende #
@pytest.fixture(scope="module")
def grid(tmp_path_factory):
    return demo.generate_mosaic(tmp_path_factory.mktemp("mosaik") / "serie")


def _pipeline(tmp_path, src, skip=()):
    s = load_settings(tmp_path / "data")
    shutil.copytree(src, s.input_dir / "serie", ignore=lambda d, names: [n for n in names if n in skip])
    db = DB(s.db_path)
    pl = Pipeline(s, db, ReviewerConfig())
    pl.scan()
    return s, db, pl, db.one("SELECT id FROM projects")["id"]


def _mismatch(X, V, mean, b=16):
    """Wie gut passt jede Aufnahme (nach dem Angleich) zum fertigen Stack? Je Aufnahme das 95-%-Perzentil der Abweichung
    der Blockmediane (b×b, Sterne und Rauschen fallen heraus) – Kachelstufen entstehen genau aus solchen Abweichungen."""
    import warnings
    from scipy import ndimage as ndi
    out = []
    for k in range(len(X)):
        d = np.where(ndi.binary_erosion(V[k], iterations=6), (X[k] - mean).mean(0), np.nan)
        h, w = (d.shape[0] // b) * b, (d.shape[1] // b) * b
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            blk = np.nanmedian(d[:h, :w].reshape(h // b, b, w // b, b).swapaxes(1, 2).reshape(h // b, w // b, -1), 2)
        blk = blk[np.isfinite(blk)]
        out.append(float(np.percentile(np.abs(blk - np.median(blk)), 95)))
    return np.array(out)


def _stack_capture(tmp_path, src, monkeypatch, match=True):
    """Stacken und die Aufnahmen nach dem Hintergrundangleich mitschneiden (match=False: Angleich abgeschaltet)."""
    cap = []
    orig = mosaic.match_background

    def spy(X, V, ref, ok, step):
        how = orig(X, V, ref, ok, step) if match else [None] * len(X)
        cap.append((X.copy(), V.copy()))
        return how
    monkeypatch.setattr(mosaic, "match_background", spy)
    s, db, pl, pid = _pipeline(tmp_path, src)
    st = pl.stack(pid)[None]
    monkeypatch.setattr(mosaic, "match_background", orig)
    mean = st.result()[0]
    return s, db, pl, pid, st, np.concatenate([_mismatch(X, V, mean) for X, V in cap])


def test_mosaic_end_to_end(tmp_path, grid, monkeypatch):
    s, db, pl, pid, st, mis = _stack_capture(tmp_path / "a", grid, monkeypatch)     # Standard: auto
    meta = st.meta
    assert meta.get("mosaic") and meta["mosaic"]["mode"] == "auto"
    Hc, Wc = meta["canvas"]
    assert st.sw.shape[1:] == (Hc, Wc) and Hc * Wc > 1.8 * demo.H * demo.W
    assert len(meta["frames"]) == 20 and sum(meta["bg_match"].values()) == 20
    assert meta["bg_match"]["plane"] >= 15
    mean, cov = st.result()
    # alle vier Kacheln eingesammelt: der größte Teil der Leinwand ist belichtet
    assert (cov > 0.15).mean() > 0.8
    # keine Kachelstufen: jede Aufnahme passt nach dem Angleich zum Stack (Synthetik: Himmel je Aufnahme anders mit
    # Verlauf im Bildfeld, Nebel ungleich über die Kacheln verteilt) – deutlich besser als nur mit dem eigenen Median
    *_, raw = _stack_capture(tmp_path / "b", grid, monkeypatch, match=False)
    assert mis.max() < 8.0 and mis.max() < 0.75 * raw.max() and np.median(mis) < 0.8 * np.median(raw), (mis, raw)
    assert pl.mosaic_state(pid)["canvas"] == [Hc, Wc]
    # Himmelsqualität/Asteroiden: Matrix Aufnahme → Stack-Raster enthält den Ursprung
    frames = [{"id": r["id"], "metrics": json.loads(r["metrics"])} for r in db.q("SELECT id, metrics FROM frames WHERE stacked=1")]
    links = skyquality.frame_links(meta, frames)
    assert np.allclose(links[meta["ref_fid"]][1], mosaic.shift(*mosaic.origin(meta)))
    rid = pl.develop(pid)
    stats = json.loads(db.one("SELECT stats FROM results WHERE id=?", (rid,))["stats"])
    sh = stats["shape"]
    assert sh[0] * sh[1] > 1.5 * demo.H * demo.W, sh
    assert stats["mosaic"]["crop"] == "wide" and stats["params"]["_mosaic"] is True
    # kompakt: kleinerer Ausschnitt, ohne neu zu stacken
    rid2 = pl.develop(pid, extra={"mosaic_crop": "compact"})
    sh2 = json.loads(db.one("SELECT stats FROM results WHERE id=?", (rid2,))["stats"])["shape"]
    assert sh2[0] * sh2[1] < sh[0] * sh[1]


def test_mosaic_off_keeps_reference_frame(tmp_path, grid):
    s, db, pl, pid = _pipeline(tmp_path, grid)
    st = pl.stack(pid, extra={"mosaic": "off"})[None]
    assert "mosaic" not in st.meta and "origin" not in st.meta and st.sw.shape[1:] == (demo.H, demo.W)
    assert pl.mosaic_state(pid) is False


def test_mosaic_adds_frames_later(tmp_path, grid):
    """Neue Aufnahmen später (Checkpoint/Fortsetzen): Leinwand und Ursprung bleiben, die Aufnahmen kommen dazu."""
    late = {f"Light_NGC2244_{i:03d}.fit" for i in (16, 17, 18, 19)}
    s, db, pl, pid = _pipeline(tmp_path, grid, skip=late)
    st = pl.stack(pid)[None]
    canvas, origin, n = st.meta["canvas"], st.meta["origin"], len(st.meta["frames"])
    for f in late:
        shutil.copy(grid / "lights" / f, s.input_dir / "serie" / "lights" / f)
    pl.scan()
    st2 = pl.stack(pid)[None]
    assert st2.meta["canvas"] == canvas and st2.meta["origin"] == origin
    assert len(st2.meta["frames"]) == n + 4 and st2.sw.shape == st.sw.shape


def test_ordinary_stack_unchanged_by_auto(tmp_path_factory):
    """Gewöhnliche Serie (Dithering, Feldrotation): „auto“ rechnet bitgleich wie „aus“."""
    src = demo.generate(tmp_path_factory.mktemp("normal") / "serie", n_lights=12, n_darks=3, n_flats=3)
    out = {}
    for m in ("auto", "off"):
        s, db, pl, pid = _pipeline(tmp_path_factory.mktemp(m), src)
        st = pl.stack(pid, extra={"mosaic": m})[None]
        assert "mosaic" not in st.meta
        out[m] = st.result()[0]
    assert np.array_equal(out["auto"], out["off"])


def test_graxpert_cache_follows_mosaic_crop(tmp_path, grid, monkeypatch):
    """GraXpert füllt Abdeckung ≤ 0,5 vorher mit Himmel – der Cache muss den Mosaik-Ausschnitt kennen (Review PR #64)."""
    from astroflow.modules.stacking import graxpert
    s, db, pl, pid = _pipeline(tmp_path, grid)
    pl.stack(pid)
    tags = []

    def fake(mean, cov, mode, strength, cache_dir, exe, log=None, preview=None, tag="", **kw):
        tags.append(tag)
        return mean, {"bge": True}
    monkeypatch.setattr(graxpert, "graxpert_path", lambda: "graxpert-attrappe")
    monkeypatch.setattr(graxpert, "mode_for", lambda v: "bge")
    monkeypatch.setattr(graxpert, "apply", fake)
    pl.develop(pid)
    pl.develop(pid, extra={"mosaic_crop": "compact"})
    assert len(tags) == 2 and tags[0] != tags[1] and all("mosaic:" in t for t in tags)
