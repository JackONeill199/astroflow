"""Entwicklung ohne GraXpert/GPU: Hintergrundmodell, Wavelet-Entrauschen, Himmel = dunkelste Bildteile."""
import numpy as np
from scipy import ndimage as ndi

from astroflow.modules.stacking import develop, processing


def _field(seed=0, H=300, W=450, noise=2.0):
    """Synthetisches Feld: Lichtverschmutzung (Ebene + Krümmung), großflächiger roter „Staub“ links, Sterne, Rauschen."""
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:H, 0:W] / np.array([H, W])[:, None, None]
    grad = 100 + 40 * xx + 20 * yy + 15 * (xx - 0.5) ** 2
    dust = 12 * np.exp(-(((xx - 0.3) / 0.12) ** 2 + ((yy - 0.5) / 0.2) ** 2))      # ~100 px groß
    img = np.stack([grad + dust * 1.0, grad + dust * 0.6, grad + dust * 0.4]).astype(np.float32)
    for _ in range(120):
        y, x = rng.integers(5, H - 5), rng.integers(5, W - 5)
        img[:, y, x] += rng.uniform(50, 400)
    img = np.stack([ndi.gaussian_filter(c, 1.2) for c in img])
    return img + rng.normal(0, noise, img.shape).astype(np.float32), dust, grad


def test_sky_background_keeps_dust_removes_gradient():
    img, dust, grad = _field()
    model, info = processing.sky_background(img)
    out = img - model
    L = out.mean(0)
    dmask = dust > 6
    far = dust < 0.2
    # Verlauf weg: ferne Himmelsbereiche links/rechts gleich hell (vorher 40 ADU Unterschied)
    left, right = np.median(L[:, :40][far[:, :40]]), np.median(L[:, -40:][far[:, -40:]])
    assert abs(left - right) < 3
    # Staub bleibt großteils stehen (Polynom kann 100-px-Strukturen nicht nachbilden)
    kept = np.median(L[dmask]) - np.median(L[far])
    assert kept > 0.6 * np.median(dust.mean() * 0 + dust[dmask] * 0.667)
    assert info["degree"] >= 1 and info["samples"] >= 10


def test_sky_background_coverage_step():
    """Stufe an der Abdeckungsgrenze (Bereich mit weniger Aufnahmen hat anderen Himmelswert) wird mit modelliert."""
    img, _, _ = _field(seed=1)
    cov = np.ones(img.shape[1:], np.float32)
    cov[:70] = 0.8
    img[:, :70] += 6.0                                         # Rahmen: hellerer Himmel, wo nur 80 % der Bilder liegen
    out = img - processing.sky_background(img, cov=cov)[0]
    L = ndi.median_filter(out.mean(0), 5)
    step = np.median(L[40:68, 300:]) - np.median(L[72:100, 300:])
    assert abs(step) < 1.5


def test_dark_sky_mask_ignores_dust():
    img, dust, _ = _field()
    img = img - processing.sky_background(img)[0]
    m = processing.dark_sky_mask(img.mean(0), 12)
    assert m.mean() > 0.05
    assert (dust[m] > 4).mean() < 0.05                         # kaum Staubpixel als „Himmel“


def test_neutralize_background_keeps_dust_color():
    img, dust, _ = _field()
    img = img - processing.sky_background(img)[0]
    out = processing.neutralize_background(img)
    far, dm = dust < 0.2, dust > 8
    # Himmel neutral, Staub bleibt rot (R deutlich über B)
    assert abs(np.median(out[0][far]) - np.median(out[2][far])) < 1.0
    assert np.median(out[0][dm]) - np.median(out[2][dm]) > 4


def test_denoise_multiscale_lowers_noise_keeps_stars():
    rng = np.random.default_rng(3)
    H, W = 200, 260
    clean = np.full((3, H, W), 50, np.float32)
    star = np.zeros((H, W), np.float32)
    star[100, 130] = 2000
    star = ndi.gaussian_filter(star, 1.5)
    clean += star[None] * np.array([1.0, 0.8, 0.6], np.float32)[:, None, None]
    noisy = clean + rng.normal(0, 3, clean.shape).astype(np.float32)
    out = processing.denoise_multiscale(noisy, 0.5)
    sky = (slice(None), slice(10, 60), slice(10, 60))
    assert out[sky].std() < 0.6 * noisy[sky].std()
    # Stern: Fluss und Farbe bleiben (keine weiche Schwelle, die Kerne absenkt)
    ap = (slice(None), slice(95, 106), slice(125, 136))
    f0 = (noisy[ap] - 50).sum((1, 2))
    f1 = (out[ap] - 50).sum((1, 2))
    assert np.allclose(f1, f0, rtol=0.05)
    assert abs(out.mean() - noisy.mean()) < 0.05


def test_assess_dust_does_not_count_as_sky():
    """Sichtbarer Staub darf die Himmelswerte der Qualitätskontrolle nicht verfälschen (früher: dunkle Bildhälfte)."""
    rng = np.random.default_rng(5)
    H, W = 400, 600
    yy, xx = np.mgrid[0:H, 0:W] / np.array([H, W])[:, None, None]
    base = 0.03 + rng.normal(0, 0.004, (3, H, W))
    dust = 0.05 * np.exp(-(((xx - 0.5) / 0.35) ** 2 + ((yy - 0.5) / 0.35) ** 2))     # füllt einen großen Teil des Feldes
    img = base + dust[None] * np.array([1.0, 0.7, 0.5])[:, None, None]
    m = develop.assess(img.astype(np.float32))["metrics"]
    assert m["bg"] < 0.036                                      # Himmel, nicht Staub
    assert m["cast"] < 0.006                                    # Staubfarbe ist kein Farbstich
    assert develop.QC_VERSION >= 3


def test_black_clip_widens_toe_instead_of_lifting_sky():
    """Abgeschnittene Schwarzwerte bei sonst dunklem Himmel: Fuß verbreitern (bg_toe), nicht den ganzen Himmel anheben."""
    qc = {"issues": [{"code": "black_clip", "text": ""}], "metrics": {"bg": 0.03}}
    adj = develop.corrections(qc, {"bg_dark": 0.03, "target_bg": 0.07, "denoise": 0.3, "chroma_denoise": 1.5, "saturation": 1.3, "asinh": 30})
    assert adj.get("bg_toe") == 1.75 and "bg_dark" not in adj
    rng = np.random.default_rng(2)
    img = np.clip(0.06 + rng.normal(0, 0.01, (3, 100, 100)), 0, 1).astype(np.float32)
    hard = processing.darken_background(img, 0.03)
    soft = processing.darken_background(img, 0.03, toe=2.5)
    assert (soft < 0.004).mean() < (hard < 0.004).mean()
    assert abs(np.median(soft) - np.median(hard)) < 0.01


def test_graxpert_preferred_cpu_denoise_only_as_fallback():
    """Reihenfolge (Nutzerentscheidung 10.11.2026): Lief das KI-Entrauschen von GraXpert, kein Wavelet-Entrauschen dazu;
    ohne GraXpert (oder wenn nur der Hintergrund lief bzw. das Entrauschen scheiterte) greift der CPU-Weg."""
    from astroflow.modules.stacking import objects
    img, _, _ = _field(seed=4, H=240, W=360)
    cov = np.ones(img.shape[1:], np.float32)
    p = {**objects.profile_for("galaxy"), "star_separation": "off"}
    _, st_gx = processing.process(img, cov, {**p, "_ai": {"bge": True, "denoise": 0.5}})
    _, st_bge = processing.process(img, cov, {**p, "_ai": {"bge": True}})
    _, st_cpu = processing.process(img, cov, p)
    assert "denoise_cpu" not in st_gx
    assert st_bge.get("denoise_cpu") == 0.5 and st_cpu.get("denoise_cpu") == 0.5


def test_continuum_color_only_in_dust_profile():
    """Glühen-Korrektur der Staubfarbe (13.11.2026) läuft im Profil reflection, nicht bei Emissionsnebeln (Hα ist echtes Rot)."""
    from astroflow.modules.stacking import objects
    img, _, _ = _field(seed=5, H=240, W=360)
    cov = np.ones(img.shape[1:], np.float32)
    for kind, on in (("reflection", True), ("emission", False), ("galaxy", False)):
        p = {**objects.profile_for(kind), "star_separation": "off", "_ai": {"bge": True, "denoise": 0.5, "smoothing": 1.0}}
        _, st = processing.process(img, cov, p)
        assert ("continuum_color" in st) is on, kind
