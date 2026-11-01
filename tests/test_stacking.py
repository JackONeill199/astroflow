import numpy as np

from astroflow.modules.stacking.stacking import StackState, _median0, batch_size_for


def _frames(rng, B, shape=(3, 256, 384)):
    X = rng.normal(100, 10, (B, *shape)).astype(np.float32)
    V = np.ones((B, *shape[1:]), bool)
    return X, V


def test_satellite_trail_rejected():
    rng = np.random.default_rng(0)
    X, V = _frames(rng, 12)
    X[4, :, 100, :] += 3000                         # Satellitenspur in einer Aufnahme
    st = StackState(X.shape[1:])
    st.add_batch(X, V, np.ones(12, np.float32), workers=4)
    mean, _ = st.result()
    assert abs(float(mean[:, 100].mean()) - 100) < 3, "Spur nicht verworfen"
    # normale Pixel: kaum verworfen (sonst steigt das Rauschen)
    kept = st.sw[:, 50:90].mean() / 12
    assert kept > 0.99, kept


def test_parallel_strips_match_serial():
    rng = np.random.default_rng(1)
    a, b = StackState((3, 256, 384)), StackState((3, 256, 384))
    for _ in range(6):
        X, V = _frames(rng, 5)
        X[rng.random(X.shape) < 0.001] += 800
        w = rng.uniform(0.5, 1.5, 5).astype(np.float32)
        a.add_batch(X, V, w, workers=1)
        b.add_batch(X, V, w, workers=4)
    ma, mb = a.result()[0], b.result()[0]
    assert np.abs(ma - mb).mean() < 1e-3 and np.abs(ma - mb).max() < 2


def test_batch_size_scales_with_budget():
    shape = (3, 2080, 3072)
    assert batch_size_for(shape, 700) == 3
    assert batch_size_for(shape, 4000) >= 15


def test_fast_median_matches_numpy():
    rng = np.random.default_rng(2)
    for B in (1, 2, 3, 4, 7, 8, 9, 16):
        X = rng.normal(0, 1, (B, 3, 20, 30)).astype(np.float32)
        assert np.array_equal(_median0(X), np.median(X, axis=0)), B
