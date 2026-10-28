"""GHS-Stretch und Sternentrennung."""
import numpy as np

from astroflow import objects, processing
from astroflow import stars as S


def test_ghs_monotonic_and_contrast_at_symmetry_point():
    x = np.linspace(0, 1, 2001, dtype=np.float32)
    y = S.ghs(x, D=5, b=3, SP=0.1, LP=0.0, HP=0.9)
    assert y[0] == 0 and abs(y[-1] - 1) < 1e-6 and np.all(np.diff(y) >= 0)
    slope = np.diff(y)
    assert abs(np.argmax(slope) / 2000 - 0.1) < 0.01          # größter Kontrastgewinn am Symmetriepunkt


def _sky(rng, n_stars=300, H=300, W=400):
    """Linearer Himmel: Rauschen + langgestrecktes Nebelfilament + runde Sterne (bekannte Positionen)."""
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    neb = 30 * np.exp(-((yy - 0.5 * xx - 60) ** 2) / (2 * 6 ** 2))          # diagonales Filament, ~14 px breit
    img = np.repeat((neb + rng.normal(0, 2, (H, W)))[None], 3, 0).astype(np.float32)
    stars = np.zeros((H, W), np.float32)
    for _ in range(n_stars):
        x, y, f = rng.uniform(10, W - 10), rng.uniform(10, H - 10), rng.uniform(50, 3000)
        stars += f * np.exp(-((xx - x) ** 2 + (yy - y) ** 2) / (2 * 1.3 ** 2)) / (2 * np.pi * 1.3 ** 2)
    return img + stars[None], neb, stars


def test_internal_separation_removes_stars_and_keeps_nebula():
    rng = np.random.default_rng(0)
    img, neb, stars = _sky(rng)
    starless, st, how = S.separate(img, fwhm=3.1, method="intern")
    assert how == "intern"
    assert np.allclose(starless + st, img, atol=1e-3)                   # nichts erfunden: Summe = Original
    on_star = stars > 20
    assert np.median((starless[0] - neb)[on_star]) < 0.25 * np.median(stars[on_star])   # Sterne weitgehend weg
    from scipy import ndimage as ndi
    off = (ndi.distance_transform_edt(stars < 0.5) > 8) & (neb > 15)    # Filament abseits der Sterne
    assert abs(np.median(starless[0][off] - img[0][off])) < 1.0          # bleibt unverändert erhalten


def test_stretch_reaches_target_and_process_with_separation():
    rng = np.random.default_rng(1)
    img, _, _ = _sky(rng)
    img = img + 100
    T = S.Stretch(img, 0.12)
    assert abs(float(np.median(T(img).mean(0))) - 0.12) < 0.02
    cov = np.ones(img.shape[1:], np.float32)
    p = objects.profile_for("emission")
    out, st = processing.process(img, cov, {**p, "star_separation": "intern"})
    assert st["star_separation"] == "intern" and st["ghs"] and out.shape[0] == 3 and np.isfinite(out).all()
    out2, st2 = processing.process(img, cov, {**p, "star_separation": "off"})
    assert "star_separation" not in st2
