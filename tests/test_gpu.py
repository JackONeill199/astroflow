"""Grafikkarte (OpenCL) gegen CPU: Ausrichten und Clipping müssen innerhalb des Rauschens übereinstimmen.
Ohne pyopencl oder GPU übersprungen."""
import numpy as np
import pytest

from astroflow.modules.stacking import gpu, registration
from astroflow.modules.stacking.stacking import StackState


@pytest.fixture
def eng(monkeypatch):
    monkeypatch.setenv("ASTROFLOW_GPU", "auto")
    pytest.importorskip("pyopencl")
    e = gpu.engine()
    if e is None:
        pytest.skip("keine geeignete Grafikkarte")
    return e


def _sky(rng, H=300, W=420):
    img = rng.normal(100, 5, (3, H, W)).astype(np.float32)
    yy, xx = np.mgrid[:H, :W]
    for y, x, f in zip(rng.uniform(10, H - 10, 60), rng.uniform(10, W - 10, 60), rng.uniform(200, 3000, 60)):
        img += (f * np.exp(-((yy - y) ** 2 + (xx - x) ** 2) / (2 * 1.8**2))).astype(np.float32)
    return img


def test_warp_matches_cpu(eng):
    img = _sky(np.random.default_rng(1))
    th = np.deg2rad(1.3)
    M = np.array([[np.cos(th), -np.sin(th), 7.3], [np.sin(th), np.cos(th), -4.6], [0, 0, 1]])
    for scale in (1, 2):
        shape = (img.shape[1] * scale, img.shape[2] * scale)
        a, va = registration.warp(img, M, shape, scale)
        b, vb = registration.warp(img, M, shape, scale, gpu=eng)
        assert np.array_equal(va, vb)
        d = np.abs(a - b)[:, va]
        assert d.max() < 0.05 * 5 and d.mean() < 1e-3, (scale, d.max(), d.mean())


def test_warp_poly_matches_cpu(eng):
    """Verzerrungskorrektur: Polynom 2. Ordnung als Abbildung, gleiche Grenzen wie beim affinen Warp."""
    img = _sky(np.random.default_rng(4))
    H, W = img.shape[1:]
    s = 0.5 * float(np.hypot(W, H))
    P = {"c": [[(W - 1) / 2 + 7.3, s * 0.9998, -s * 0.02, 1.5, -0.8, 0.6],
               [(H - 1) / 2 - 4.6, s * 0.02, s * 0.9998, -0.7, 1.1, 0.9]], "ctr": [(W - 1) / 2, (H - 1) / 2], "s": s}
    for scale in (1, 2):
        shape = (H * scale, W * scale)
        a, va = registration.warp(img, None, shape, scale, poly=P)
        b, vb = registration.warp(img, None, shape, scale, gpu=eng, poly=P)
        assert np.array_equal(va, vb) and not eng.broken
        d = np.abs(a - b)[:, va]
        assert d.max() < 0.05 * 5 and d.mean() < 1e-3, (scale, d.max(), d.mean())


def test_accumulate_matches_cpu(eng):
    rng = np.random.default_rng(2)
    base = _sky(rng)
    C, H, W = base.shape
    cpu, dev = StackState((C, H, W)), StackState((C, H, W))
    for rnd in range(5):
        B = (12, 8, 3, 2, 16)[rnd]
        X = np.stack([base + rng.normal(0, 5, base.shape).astype(np.float32) for _ in range(B)])
        X[0, :, 100 + rnd : 102 + rnd] += 2000                 # Satellitenspur
        V = np.ones((B, H, W), bool)
        V[B - 1, :, :40] = False
        w = rng.uniform(0.5, 1.5, B).astype(np.float32)
        r1 = cpu.add_batch(X, V, w, workers=4)                 # Streifen = eigener Rauschboden, wie auf der GPU je Stapel
        r2 = dev.add_batch(X, V, w, gpu=eng)
        assert abs(r1 - r2) <= max(10, 0.001 * r1), (rnd, r1, r2)
    m1, m2 = cpu.result()[0], dev.result()[0]
    d = np.abs(m1 - m2)
    assert d.mean() < 0.01 and np.mean(d > 0.5) < 1e-3, (d.mean(), np.mean(d > 0.5))
    assert m2[:, 100:107].max() < base[:, 100:107].max() + 30   # Spur entfernt


def test_failure_falls_back_to_cpu(eng, monkeypatch):
    img = _sky(np.random.default_rng(3))
    M = np.eye(3)
    monkeypatch.setattr(eng, "warp", lambda *a: (_ for _ in ()).throw(RuntimeError("Treiber weg")))
    monkeypatch.setattr(eng, "broken", None)
    out, _ = registration.warp(img, M, img.shape[1:], gpu=eng)
    assert eng.broken and np.allclose(out[:, 5:-5, 5:-5], img[:, 5:-5, 5:-5], atol=1e-3)
    assert gpu.engine() is None
