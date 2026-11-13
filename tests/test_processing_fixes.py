"""Farbsäume (Kanalversatz, gesättigte Kerne) und Randglühen – Fälle aus der echten Stellina-Session NGC 1579."""
import numpy as np
from scipy import ndimage as ndi

from astroflow.modules.stacking import processing


def _starfield(rng, H=400, W=600, n=120, fwhm=4.0):
    img = np.zeros((H, W), np.float32)
    ys, xs = rng.uniform(10, H - 10, n), rng.uniform(10, W - 10, n)
    img[ys.astype(int), xs.astype(int)] = rng.uniform(2000, 20000, n)
    return ndi.gaussian_filter(img, fwhm / 2.355) * (fwhm / 2.355) ** 2 * 2 * np.pi, xs, ys


def test_align_channels_recovers_dispersion_shift():
    rng = np.random.default_rng(1)
    g, _, _ = _starfield(rng)
    shift_r, shift_b = (0.4, -0.35), (-0.3, 0.45)          # (dy, dx) wie bei atmosphärischer Dispersion
    img = np.stack([ndi.shift(g, shift_r, order=3), g, ndi.shift(g, shift_b, order=3)]) + 100
    img += rng.normal(0, 2, img.shape).astype(np.float32)
    out, shifts = processing.align_channels(img)
    assert np.allclose(shifts[0], (shift_r[1], shift_r[0]), atol=0.1), shifts      # gemeldet als (dx, dy)
    assert np.allclose(shifts[1], (shift_b[1], shift_b[0]), atol=0.1), shifts
    # nach der Ausrichtung liegen Rot und Blau auf Grün
    for c in (0, 2):
        assert np.abs(out[c] - img[1]).mean() < 0.35 * np.abs(img[c] - img[1]).mean()


def test_edge_glow_removed_and_central_nebula_kept():
    rng = np.random.default_rng(2)
    H, W = 400, 600
    stars, _, _ = _starfield(rng, H, W)
    yy, xx = np.mgrid[0:H, 0:W]
    nebula = 60 * np.exp(-((yy - H / 2) ** 2 + (xx - W / 2) ** 2) / (2 * 40**2))
    d = np.minimum(yy, H - 1 - yy)
    glow = 80 * np.exp(-d / 25.0)                           # steil zum oberen/unteren Rand
    base = 100 + stars + nebula + rng.normal(0, 2, (H, W))
    img = np.stack([base + glow * k for k in (0.8, 0.65, 1.0)]).astype(np.float32)
    out = processing.remove_edge_glow(img)
    for c in range(3):
        edge = np.median(out[c, :6, 200:400]) - np.median(out[c, 150:170, 50:150])
        assert abs(edge) < 6, (c, edge)                     # vorher bis +80
        neb = out[c, H // 2 - 5 : H // 2 + 5, W // 2 - 5 : W // 2 + 5].mean() - np.median(out[c, 150:170, 50:150])
        assert neb > 50, (c, neb)                           # Nebel (60) bleibt erhalten


def test_saturated_cores_become_neutral():
    img = np.full((3, 60, 60), 100.0, np.float32)
    yy, xx = np.mgrid[0:60, 0:60]
    star = 70000 * np.exp(-((yy - 30) ** 2 + (xx - 30) ** 2) / (2 * 3.0**2))
    img += np.minimum(star, 65000)[None]                    # alle Kanäle am Anschlag
    sat = processing.saturation_mask(img)
    assert sat[30, 30] and not sat[5, 5]
    wb = img * np.array([0.57, 1.0, 0.94], np.float32)[:, None, None]   # Weißabgleich wie bei der Stellina
    out = processing.neutral_saturated_cores(wb, sat, fwhm=7.0)
    core = out[:, 30, 30]
    assert core.max() - core.min() < 0.02 * core.max()      # Kern neutral statt cyan
    assert np.allclose(out[:, 5, 5], wb[:, 5, 5])           # Hintergrund unverändert


def test_grade_lifts_nebula_structure_but_not_sky():
    """Endbearbeitung: Strukturen im Nebel kräftiger, Himmel (Rauschen) unverändert – auch wenn der Nebel das Bild füllt."""
    import numpy as np
    from astroflow.modules.stacking import processing
    rng = np.random.default_rng(1)
    H, W = 256, 384
    yy, xx = np.mgrid[0:H, 0:W]
    sky = 0.05 + rng.normal(0, 0.004, (H, W))
    neb = (xx > 96) * (0.12 + 0.04 * np.sin(xx / 6.0) * np.sin(yy / 7.0))       # Nebel mit Filamenten, 3/4 des Bildes
    img = np.stack([sky + neb, sky + 0.5 * neb, sky + 0.4 * neb]).astype(np.float32)
    out = processing.grade(img, 1.0, 0.5)
    L0, L1 = processing.luminance(img), processing.luminance(out)
    core = (slice(40, 220), slice(140, 360))
    assert L1[core].std() > 1.15 * L0[core].std()                                  # mehr Struktur im Nebel
    assert abs(float(L1[:, :80].std() - L0[:, :80].std())) < 0.1 * L0[:, :80].std()  # Himmelsrauschen nicht verstärkt


def test_continuum_color_removes_red_glow_keeps_dust_and_blue_nebula():
    """Staubprofil (NGC 7023): rotes Sensor-/IR-Glühen (Grün fehlt) wird zu Kontinuumsfarbe, brauner Staub und blauer
    Reflexionsnebel bleiben, Sterne werden nicht angefasst."""
    rng = np.random.default_rng(3)
    H, W = 240, 360
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    blob = lambda cy, cx, r: np.exp(-((yy - cy) ** 2 + (xx - cx) ** 2) / (2 * r**2))
    glow = 1.0 * blob(0, 0, 70)                                   # Glühen in der Ecke: R:G:B 1,8 : 0,3 : 0,15
    dust = 1.0 * blob(170, 260, 45)                               # brauner Staub 1,3 : 1 : 0,75
    neb = 1.0 * blob(120, 150, 25)                                # blauer Nebel 0,7 : 1 : 1,4
    img = np.stack([1.8 * glow + 1.3 * dust + 0.7 * neb,
                    0.3 * glow + 1.0 * dust + 1.0 * neb,
                    0.15 * glow + 0.75 * dust + 1.4 * neb]).astype(np.float32)
    stars, _, _ = _starfield(rng, H, W, n=40)
    img += (stars / stars.max() * 5)[None] * np.array([1.6, 1.0, 0.6], np.float32)[:, None, None]  # rötliche Sterne
    img += rng.normal(0, 0.02, img.shape).astype(np.float32)
    out, amount = processing.continuum_color(img, 1.0)
    assert amount > 0
    g0 = (slice(0, 25), slice(0, 25))                             # Glühen: Rot höchstens 1,8 × Grün, vorher 6 ×
    assert img[0][g0].mean() > 4 * img[1][g0].mean()
    assert out[0][g0].mean() < 1.9 * out[1][g0].mean()
    for sl in ((slice(160, 180), slice(250, 270)), (slice(110, 130), slice(140, 160))):   # Staub und Nebel unverändert
        assert np.allclose(out[:, sl[0], sl[1]].mean((1, 2)), img[:, sl[0], sl[1]].mean((1, 2)), rtol=0.03)
    assert np.allclose(out[1], img[1])                            # Grün bleibt, nur Rot/Blau werden gesenkt
    # ohne Profilwert: process lässt die Farbe in Ruhe (Emissionsnebel – Hα ist echtes Rot)
    assert processing.continuum_color(img, 0.0)[0] is img
