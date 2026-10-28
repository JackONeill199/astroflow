"""Hotpixel ohne Darks: Die Registrierung darf sich nicht an ortsfesten Sensorfehlern ausrichten."""
import numpy as np

from astroflow.modules.stacking import calibration, demo, quality, registration


def _scene(seed=3, n_hot=400):
    rng = np.random.default_rng(seed)
    stars, ext = demo._sky(rng)
    hot_idx = rng.choice(demo.H * demo.W, n_hot, replace=False)
    hot_val = rng.uniform(3000, 9000, n_hot)        # heller als die meisten Sterne

    def frame(shift, hot=True):
        r = np.random.default_rng(int(abs(shift[0]) * 100 + abs(shift[1])))
        sky = demo._frame(r, stars, ext * 0, 0.0, np.array(shift, float), 3.4)
        raw = demo._mosaic(sky) + 300 + r.normal(0, 10, (demo.H, demo.W))
        if hot:
            raw.ravel()[hot_idx] += hot_val         # Hotpixel bleiben am selben Sensorpixel
        return raw.astype(np.float32)

    return frame, hot_idx


def test_cosmetic_removes_hot_pixels_but_keeps_stars():
    frame, hot_idx = _scene()
    fixed = calibration.cosmetic(frame((0, 0)), bayer=True)
    left = (fixed.ravel()[hot_idx] - 300) > 1000
    assert left.mean() < 0.05, f"{left.sum()} Hotpixel übrig"
    # Sterne: gleiche Szene ohne Hotpixel -> Kosmetik verändert die hellsten Sterne kaum
    clean = frame((0, 0), hot=False)
    st = quality.detect_stars(quality.analysis_image(clean, True)[0], max_stars=30)
    for x, y in zip(st.x * 2, st.y * 2):
        xi, yi = int(x), int(y)
        a = clean[yi - 5 : yi + 6, xi - 5 : xi + 6].sum() - 300 * 121
        b = calibration.cosmetic(clean, True)[yi - 5 : yi + 6, xi - 5 : xi + 6].sum() - 300 * 121
        assert abs(a - b) < 0.05 * a + 300, (x, y, a, b)


def test_detection_ignores_single_pixels():
    frame, hot_idx = _scene()
    raw = frame((0, 0))
    L, s = quality.analysis_image(raw, True)       # ohne Kosmetik: nur die Schärfeprüfung
    st = quality.detect_stars(L)
    hy, hx = np.unravel_index(hot_idx, raw.shape)
    hot_b = np.column_stack([hx / s, hy / s])
    d = np.hypot(*(np.column_stack([st.x, st.y])[:, None] - hot_b[None]).transpose(2, 0, 1)).min(1)
    assert (d < 1.0).mean() < 0.1, f"{(d < 1.0).sum()} von {len(st)} Detektionen sind Hotpixel"


def test_registration_follows_stars_not_hot_pixels():
    frame, _ = _scene()
    shift = (17.0, -11.0)                           # deutliche Drift zwischen den Aufnahmen
    ma = quality.analyze_frame(calibration.cosmetic(frame((0, 0)), True), True)
    mb = quality.analyze_frame(calibration.cosmetic(frame(shift), True), True)
    ref = np.array([s[:2] for s in ma["stars"]])
    tgt = np.array([s[:2] for s in mb["stars"]])
    M, pairs, rms = registration.find_transform(ref, tgt)
    # Szene um +shift verschoben -> M (tgt -> ref) verschiebt um +shift zurück
    assert np.allclose(M[:2, 2], shift, atol=1.0), M
    assert abs(M[0, 1]) < 0.01 and rms < 1.0
