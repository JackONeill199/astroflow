import numpy as np

from astroflow.modules.stacking import quality


def _metrics(eccs):
    return {f"f{i}": {"n_stars": 150, "fwhm": 6.0, "ecc": float(e), "bg": 900.0} for i, e in enumerate(eccs)}


def test_mostly_elongated_frames_rejected_absolutely():
    # Wie M57: die meisten Frames verzogen – die relative Grenze allein ließe fast alle durch
    eccs = np.r_[np.linspace(0.5, 0.7, 40), np.linspace(0.82, 0.95, 60)]
    sel = quality.select_frames(_metrics(eccs))
    bad = [k for k, (ok, reason, _) in sel.items() if not ok and "verzogen" in reason]
    assert len(bad) >= 25
    assert len(bad) <= np.ceil(len(eccs) * quality.ECC_MAX_REJECT)          # höchstens ein Drittel
    assert all(sel[f"f{i}"][0] for i in range(40))                          # runde Frames bleiben


def test_round_frames_untouched_by_absolute_limit():
    eccs = np.linspace(0.45, 0.65, 50)
    sel = quality.select_frames(_metrics(eccs))
    assert all(ok for ok, _, _ in sel.values())
