"""Runder Sternkern + schwacher gleichgerichteter Schweif (Ruck der Montierung) – Bilder und SkyTrace verwerfen ihn,
symmetrische Optik, zufällige Formen, lokale Effekte und echte Spuren bleiben erhalten."""
from pathlib import Path
import sys

import numpy as np
from scipy.ndimage import gaussian_filter

from astroflow.core import star_tails
from astroflow.modules.stacking import quality

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "astroflow/modules/skytrace/engine"))
from frame_quality import assess_quality, measure_quality  # noqa: E402


def field(tail=0.0, length=22, angle=235, symmetric=False, random_angles=False, local=False, seed=5):
    """Sternfeld 700×900: Gauß-Kerne (FWHM ≈ 2,8 px) und optional ein vom Kern ausgehender Schweif mit Anteil `tail`
    am Sternfluss (wie Bild 25 der Rosette-Serie: Kern hell und rund, Schweif ~25 Analysepixel)."""
    rng = np.random.default_rng(seed)
    shape = (700, 900)
    cores = np.zeros(shape, np.float32)
    tails = np.zeros(shape, np.float32)
    for y in range(70, 650, 75):
        for x in range(70, 850, 75):
            x0, y0 = x + rng.uniform(-0.5, 0.5), y + rng.uniform(-0.5, 0.5)
            flux = rng.uniform(2e4, 8e4)
            cores[int(round(y0)), int(round(x0))] += flux
            if not tail or (local and not (x > 450 and y < 350)):
                continue
            a = np.radians(rng.uniform(0, 360) if random_angles else angle)
            for sign in ((1, -1) if symmetric else (1,)):
                for d in np.linspace(1, length, 4 * length):
                    tails[int(round(y0 + sign * d * np.sin(a))), int(round(x0 + sign * d * np.cos(a)))] += tail * flux / (4 * length)
    img = 200 + gaussian_filter(cores + tails, 1.2)
    return (img + rng.normal(0, 3, shape)).astype(np.float32)


def test_round_core_with_faint_tail_detected():
    m = star_tails.tail_profile(field(tail=0.12))
    assert star_tails.has_tail(m), m
    assert abs(((m["tail_profile_angle"] - 235) + 180) % 360 - 180) <= 10
    assert m["tail_profile_quadrants"] >= 3 and m["tail_profile_support"] >= 0.8


def test_sharp_symmetric_random_and_local_shapes_kept():
    for kw in ({}, dict(tail=0.12, symmetric=True), dict(tail=0.12, random_angles=True), dict(tail=0.12, local=True)):
        m = star_tails.tail_profile(field(**kw))
        assert not star_tails.has_tail(m), (kw, m)
    sharp = star_tails.tail_profile(field())
    assert sharp["tail_profile_fraction"] < 0.015, sharp


def test_short_asymmetry_at_core_is_not_a_tail():
    # Kurze einseitige Ausbuchtung am Kern (Koma, Seeing) – kürzer als 3 FWHM
    m = star_tails.tail_profile(field(tail=0.12, length=4))
    assert not star_tails.has_tail(m), m


def test_independent_trails_do_not_count():
    img = field()
    lines = np.zeros_like(img)
    for y in (80, 300, 550):
        for t in np.linspace(0, 1, 1600):
            lines[int(y + 80 * t), int(40 + 800 * t)] = 400
    m = star_tails.tail_profile(img + gaussian_filter(lines, 0.8))
    assert not star_tails.has_tail(m), m


def test_too_few_stars_gives_no_verdict():
    img = np.random.default_rng(1).normal(200, 3, (300, 300)).astype(np.float32)
    m = star_tails.tail_profile(img)
    assert m["tail_profile_stars"] < 8 and not star_tails.has_tail(m)


# ---- Bilder ---------------------------------------------------------------------------------------------------------

def test_analyze_frame_stores_tail_profile():
    m = quality.analyze_frame(field(tail=0.12), bayer=False)
    assert star_tails.has_tail(m), {k: v for k, v in m.items() if k.startswith("tail_profile")}
    assert not star_tails.has_tail(quality.analyze_frame(field(), bayer=False))


def _metrics(n, tailed, fraction=0.1):
    base = {"n_stars": 150, "fwhm": 6.0, "ecc": 0.5, "bg": 900.0, "tail_profile_stars": 40, "tail_profile_sigma": 80.0,
            "tail_profile_length": 20.0, "tail_profile_fwhm": 3.0, "tail_profile_angle": 235, "tail_profile_support": 0.9,
            "tail_profile_quadrants": 4}
    return {f"f{i}": dict(base, tail_profile_fraction=fraction if i in tailed else 0.006) for i in range(n)}


def test_select_frames_rejects_tailed_frames():
    sel = quality.select_frames(_metrics(30, {3, 17}))
    assert sel["f3"] == (False, "Sternschweif (Ruck der Montierung)", sel["f3"][2])
    assert not sel["f17"][0]
    assert sum(ok for ok, _, _ in sel.values()) == 28


def test_select_frames_tail_rejection_capped():
    # Hat fast die ganze Serie einen Schweif, fliegt höchstens ein Drittel heraus (die stärksten)
    metrics = _metrics(30, set(range(25)))
    for i in range(25):
        metrics[f"f{i}"]["tail_profile_fraction"] = 0.05 + 0.01 * i
    sel = quality.select_frames(metrics)
    bad = [k for k, (ok, reason, _) in sel.items() if not ok and "schweif" in reason]
    assert 0 < len(bad) <= np.ceil(30 * quality.ECC_MAX_REJECT)
    assert "f24" in bad and "f0" not in bad


def test_select_frames_ignores_old_analyses_without_tail_profile():
    metrics = _metrics(20, {2})
    del metrics["f5"]["tail_profile_stars"]                      # ältere Analyse ohne Zusatzmetrik
    assert all(ok for ok, _, _ in quality.select_frames(metrics).values())


# ---- SkyTrace -------------------------------------------------------------------------------------------------------

def test_skytrace_rejects_round_core_with_faint_tail():
    q = assess_quality(measure_quality(field(tail=0.12)))
    assert q["excluded"] and q["field_motion_evidence"] == "mean_star_profile", q
    assert not q["ensemble_motion"]          # die bisherige Prüfung allein hätte diese Aufnahme behalten


def test_skytrace_keeps_sharp_and_symmetric_fields():
    for kw in ({}, dict(tail=0.12, symmetric=True)):
        q = assess_quality(measure_quality(field(**kw)))
        assert not q["excluded"], (kw, q)
