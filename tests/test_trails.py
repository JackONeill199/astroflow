"""Satellitenspuren: Erkennung je Aufnahme (trails.find) und gezieltes Ausblenden beim Stacken statt nur Clipping."""
import json
import os

import numpy as np
import pytest
from scipy import ndimage as ndi

from astroflow.core.config import load_settings
from astroflow.modules.stacking import demo, quality, trails
from astroflow.modules.stacking.db import DB
from astroflow.modules.stacking.pipeline import Pipeline, stack_dir
from astroflow.modules.stacking.registration import find_transform
from astroflow.modules.stacking.reviewer import ReviewerConfig
from astroflow.modules.stacking.stacking import StackState


def _field(h, w, n_stars, seed, noise=14.0, filament=0.0):
    r = np.random.default_rng(seed)
    img = np.full((h, w), 300.0, np.float32)
    if filament:                                                     # gerader schmaler Nebelfaden
        yy, xx = np.mgrid[0:h, 0:w]
        d = np.abs((xx - w * 0.2) * 0.5 - (yy - h * 0.3) * 0.866)
        img += (filament * np.exp(-d**2 / 8)).astype(np.float32)
    pts = np.zeros((h, w), np.float32)
    np.add.at(pts, (r.integers(0, h, n_stars), r.integers(0, w, n_stars)), 10 ** r.uniform(2.3, 5.0, n_stars))
    img += ndi.gaussian_filter(pts, 3.0 / 2.355)
    return img + r.normal(0, noise, (h, w)).astype(np.float32)


def _trail(img, amp, width=1.0, p=(0.0, 0.1), q=(1.0, 0.8)):
    h, w = img.shape
    yy, xx = np.mgrid[0:h, 0:w]
    a, b = np.array([p[0] * w, p[1] * h]), np.array([q[0] * w, q[1] * h])
    L = np.linalg.norm(b - a)
    d = (b - a) / L
    u = (xx - a[0]) * d[0] + (yy - a[1]) * d[1]
    v = -(xx - a[0]) * d[1] + (yy - a[1]) * d[0]
    return img + (amp * np.exp(-v**2 / (2 * width**2)) * (u >= 0) * (u <= L)).astype(np.float32), (a, b)


def _on_line(line, a, b, tol=4.0):
    """Beide Enden der gefundenen Strecke liegen auf der wahren Geraden, und sie deckt sie zu ≥ 90 % ab."""
    d = (b - a) / np.linalg.norm(b - a)
    n = np.array([-d[1], d[0]])
    e = np.array(line[:4]).reshape(2, 2)
    off = np.abs((e - a) @ n)
    u = np.sort((e - a) @ d)
    return off.max() < tol and (min(u[1], np.linalg.norm(b - a)) - max(u[0], 0)) > 0.9 * np.linalg.norm(b - a) - 30


@pytest.mark.parametrize("n_stars", [300, 3000, 30000])
def test_no_false_alarm_in_star_fields(n_stars):
    assert trails.find(_field(540, 720, n_stars, seed=n_stars)) == []


@pytest.mark.parametrize("amp,width", [(30, 1.0), (100, 1.0), (3000, 1.0), (2000, 4.0)])
def test_finds_faint_to_bright_trails_in_dense_field(amp, width):
    img, (a, b) = _trail(_field(540, 720, 3000, seed=9), amp, width)
    lines = trails.find(img)
    assert lines
    # die ganze Spur ist abgedeckt (eine sehr helle, breite darf in zwei Stücken kommen)
    m = trails.band(img.shape, lines)
    t = np.linspace(0.02, 0.98, 200)
    xy = a + t[:, None] * (b - a)
    assert m[xy[:, 1].astype(int), xy[:, 0].astype(int)].mean() > 0.95
    if amp <= 100:
        assert len(lines) == 1 and _on_line(lines[0], a, b)


def test_two_crossing_trails():
    img, (a1, b1) = _trail(_field(540, 720, 3000, seed=9), 60)
    img, (a2, b2) = _trail(img, 60, p=(0.1, 0.9), q=(0.9, 0.05))
    lines = trails.find(img)
    assert len(lines) == 2
    assert any(_on_line(l, a1, b1) for l in lines) and any(_on_line(l, a2, b2) for l in lines)


def test_analysis_blanks_trail_before_star_detection(tmp_path):
    """Die Spur verfälschte die Frame-Metriken (Spurpunkte als Sterne → FWHM hoch → Aufnahme „unscharf“ verworfen)."""
    img = _field(540, 720, 400, seed=3)
    clean = quality.analyze_frame(img, False)
    dirty = quality.analyze_frame(_trail(img, 400)[0], False)
    assert clean["trails"] == 0 and clean["trail_lines"] == []
    assert dirty["trails"] == 1 and len(dirty["trail_lines"][0]) == 6
    assert abs(dirty["fwhm"] - clean["fwhm"]) < 0.15 * clean["fwhm"]
    assert abs(dirty["n_stars"] - clean["n_stars"]) <= 5


def test_band_and_transform():
    M = np.array([[0.0, -1.0, 100.0], [1.0, 0.0, 10.0], [0, 0, 1]])           # 90° gedreht + verschoben
    (l,) = trails.transform([[10, 20, 60, 20, 2.0]], M, scale=2)
    assert np.allclose(l[:4], [(80 + 0.5) * 2 - 0.5, (20 + 0.5) * 2 - 0.5, (80 + 0.5) * 2 - 0.5, (70 + 0.5) * 2 - 0.5])
    assert l[4] == pytest.approx(4.0)
    m = trails.band((300, 300), [l], pad=1.0)
    assert m[int(l[1]) + 20, int(l[0])] and not m[int(l[1]) + 20, int(l[0]) + 8]


def test_confirm_new_vs_static():
    rng = np.random.default_rng(1)
    ref = rng.normal(0, 5, (200, 300)).astype(np.float32)
    line = [10, 50, 290, 150, 2.0]
    core = trails.band(ref.shape, [line])
    valid = np.ones(ref.shape, bool)
    new = ref + rng.normal(0, 5, ref.shape).astype(np.float32) + 40 * core
    assert trails.confirm(new, ref, valid, line, 7.0)
    static = ref + 40 * core                                              # steht auch im Vergleich
    assert not trails.confirm(static + rng.normal(0, 5, ref.shape).astype(np.float32) + 6 * core, static, valid, line, 7.0)
    assert trails.confirm(new, None, valid, line, 7.0)                  # nichts zum Vergleichen → ausblenden


def test_stackstate_masked_weight_keeps_coverage(tmp_path):
    st = StackState((1, 20, 30))
    X = np.ones((3, 1, 20, 30), np.float32)
    V = np.ones((3, 20, 30), bool)
    V[1, 5:8] = False                                                     # Spurband in Aufnahme 1
    st.add_batch(X, V, np.ones(3, np.float32))
    st.swm[5:8] += 1.0
    st.save(tmp_path / "s")
    st2 = StackState.load(tmp_path / "s")
    mean, cov = st2.result()
    assert np.allclose(cov, 1.0) and np.allclose(mean, 1.0)


def _demo_stack(tmp_path, name, trail=True, n=8, monkeypatch=None, filament=0.0):
    """Demo stacken (kleine Stapel à 3). trail=False: dieselben Aufnahmen ohne Satellitenspur (gleiches Rauschen)."""
    if monkeypatch is not None:
        f0 = demo._frame
        if not trail:
            monkeypatch.setattr(demo, "_frame", lambda *a, **k: f0(*a[:8], **k) if len(a) > 8 else f0(*a, **{**k, "sat_trail": False}))
        if filament:
            s0 = demo._sky
            def sky(rng, size=(620, 800)):
                stars, ext = s0(rng, size)
                yy, xx = np.mgrid[0:size[0], 0:size[1]]
                d = np.abs((xx - 100) * 0.42 - (yy - 80) * 0.91)
                return stars, ext + (filament * np.exp(-d**2 / (2 * 1.2**2))).astype(np.float32)[None]
            monkeypatch.setattr(demo, "_sky", sky)
    data = tmp_path / name
    demo.generate(data / "input" / "demo", n_lights=n)
    if monkeypatch is not None:
        monkeypatch.undo()
    for p in (data / "input").rglob("*.fit"):
        os.utime(p, (1.7e9, 1.7e9))                       # gleiche Frame-IDs in beiden Läufen
    s = load_settings(data)
    s.delete_uploads = False
    s.batch_mb = 15                                       # Stapel à 3: Clipping allein kann eine Spur nicht verwerfen
    db = DB(s.db_path)
    pl = Pipeline(s, db, ReviewerConfig())
    pl.scan()
    pid = db.one("SELECT id FROM projects")["id"]
    db.x("UPDATE frames SET manual=1 WHERE path LIKE ?", ("%_003.fit",))
    pl.stack(pid)
    return pl, db, StackState.load(stack_dir(s.work_dir / f"p{pid}", None))


def _residual(db, st, mean_with, mean_without):
    row = db.one("SELECT id, metrics FROM frames WHERE path LIKE ?", ("%_003.fit",))
    assert row["id"] in st.meta["frames"]
    ref = np.array([[s[0], s[1]] for s in st.meta["ref_stars"]])
    tg = np.array([[s[0], s[1]] for s in json.loads(row["metrics"])["stars"]])
    M = np.eye(3) if row["id"] == st.meta["ref_fid"] else find_transform(ref, tg)[0]
    core = trails.band(mean_with.shape[1:], trails.transform([[0, 40, 479, 239.6, 1.0]], M, 1), ends=0)
    return float((mean_with.mean(0) - mean_without.mean(0))[core].mean())


def test_demo_trail_masked_in_small_batches(tmp_path, monkeypatch):
    pl, db, with_trail = _demo_stack(tmp_path, "mit")
    _, db0, without = _demo_stack(tmp_path, "ohne", trail=False, monkeypatch=monkeypatch)
    assert with_trail.meta["ref_fid"] == without.meta["ref_fid"]
    info = with_trail.meta["trails"]
    assert info["frames"] == 1 and info["lines"] == 1 and info["pixels"] > 1000
    lines = json.loads(db.one("SELECT metrics FROM frames WHERE path LIKE ?", ("%_003.fit",))["metrics"])["trail_lines"]
    assert len(lines) == 1
    others = [json.loads(r["metrics"]).get("trail_lines") for r in db.q("SELECT metrics FROM frames WHERE path NOT LIKE ? AND kind='light'", ("%_003.fit",))]
    assert all(o == [] for o in others if o is not None)
    m1, cov1 = with_trail.result()
    m0, cov0 = without.result()
    assert abs(_residual(db, with_trail, m1, m0)) < 1.0
    band = with_trail.swm > 0                                            # Abdeckung bleibt geometrisch (swm): kein „Loch“,
    assert band.any() and np.median(np.abs(cov1 - cov0)[band]) < 0.01      # das GraXpert mit Himmel füllen würde (≤ 0,5)
    assert not ((cov0 > 0.6) & (cov1 <= 0.5) & band).any()

    # Gegenprobe: ohne Ausblenden bleibt die Spur in kleinen Stapeln stehen (gemessen +42 ADU)
    monkeypatch.setattr(trails, "confirm", lambda *a, **k: False)
    _, db2, plain = _demo_stack(tmp_path, "nur_clipping")
    m2, _ = plain.result()
    assert _residual(db2, plain, m2, m0) > 10


def test_demo_static_filament_not_masked(tmp_path, monkeypatch):
    _, db, st = _demo_stack(tmp_path, "faden", trail=False, monkeypatch=monkeypatch, filament=150.0)
    seen = sum(len(json.loads(r["metrics"]).get("trail_lines") or []) for r in db.q("SELECT metrics FROM frames WHERE kind='light'"))
    assert seen >= 4                                                    # die Analyse hält den Faden für eine Linie …
    assert st.meta["trails"]["frames"] == 0 and st.meta["trails"]["kept"] >= 4   # … beim Stacken bleibt er erhalten
    assert not st.swm.any()


def test_old_metrics_without_trail_lines_detected_while_stacking(tmp_path, monkeypatch):
    """Vor 0.47 analysierte Aufnahmen haben kein `trail_lines`: beim Stacken im geladenen Bild nachsuchen."""
    real = quality.analyze_frame

    def old(raw, bayer):
        m = real(raw, bayer)
        m.pop("trail_lines")
        return m
    monkeypatch.setattr(quality, "analyze_frame", old)
    _, db, st = _demo_stack(tmp_path, "alt")
    assert st.meta["trails"]["frames"] == 1
