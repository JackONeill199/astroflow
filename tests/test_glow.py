"""Sensorglühen: Messung aus Lights, Abzug in Sensorkoordinaten, Glüh-Bibliothek je Kamera."""
import json

import numpy as np

from astroflow.modules.stacking import calibration, demo
from astroflow.core.config import load_settings
from astroflow.modules.stacking.db import DB
from astroflow.modules.stacking.pipeline import Pipeline
from astroflow.modules.stacking.reviewer import ReviewerConfig

H, W = 200, 300


def _glow():
    y = np.arange(H, dtype=np.float32)
    return (80 * np.exp(-y / 12) + 60 * np.exp(-(H - 1 - y) / 15))[:, None] * np.ones((1, W), np.float32)


def _frames(n, rng):
    """Bayer-Frames: Himmel mit wandernden Sternen + ortsfestes Randglühen + Rauschen."""
    out = []
    for i in range(n):
        sky = np.full((H, W), 500, np.float32)
        for _ in range(60):
            x, y = rng.integers(3, W - 3), rng.integers(3, H - 3)
            sky[y - 1 : y + 2, x - 1 : x + 2] += 3000
        out.append(sky + _glow() + rng.normal(0, 20, (H, W)).astype(np.float32))
    return out


def test_glow_measured_and_removed_only_at_edges():
    rng = np.random.default_rng(0)
    frames = _frames(20, rng)
    model = calibration.glow_model([calibration.glow_profiles(f, True) for f in frames])
    assert calibration.glow_amplitude(model) > 50
    clean = calibration.remove_glow(frames[0], True, model)
    prof = np.percentile(clean, 30, axis=1)
    mid = np.median(prof[H // 3 : 2 * H // 3])
    assert abs(prof[:5].mean() - mid) < 15 and abs(prof[-5:].mean() - mid) < 15          # Ränder vorher +80/+60
    c = (slice(H // 3, 2 * H // 3), slice(W // 3, 2 * W // 3))
    assert np.allclose(clean[c], frames[0][c])                                             # Bildmitte unberührt
    assert calibration.glow_model([calibration.glow_profiles(f, True) for f in frames[:5]]) is None   # zu wenige Frames


def test_library_grows_per_night_and_falls_back(tmp_path):
    rng = np.random.default_rng(1)
    lib = calibration.GlowLibrary(tmp_path)
    info = {"instrument": "Stellina", "shape": [H, W], "gain": 200, "exptime": 10.0, "filter": None}
    key = lib.key(info)
    for night in ("2022-07-18", "2022-08-06"):
        lib.add(key, night, calibration.median_profiles([calibration.glow_profiles(f, True) for f in _frames(15, rng)]))
    assert lib.nights(key) == ["2022-07-18", "2022-08-06"]
    own, src = lib.medians(key, "2022-07-18")
    assert src == "nacht"
    other, src = lib.medians(key, "2022-09-23")                  # unbekannte Nacht -> Mittel der Kamera
    assert src.startswith("bibliothek") and calibration.glow_amplitude(calibration.model_from(other)) > 50
    assert lib.key({**info, "instrument": "Seestar"}) != key      # jedes Teleskop hat seinen eigenen Datensatz


def test_pipeline_builds_library_and_skips_with_darks(tmp_path):
    s = load_settings(tmp_path / "data")
    s.delete_uploads = True
    db = DB(s.db_path)
    pl = Pipeline(s, db, ReviewerConfig())
    demo.generate(s.uploads_dir / "n", n_lights=36, n_darks=0, n_flats=0)     # 2 Nächte, je >= 12 Frames
    pl.scan()
    pid = db.one("SELECT id FROM projects")["id"]
    pl.process(pid)
    info = json.loads(db.one("SELECT options FROM projects WHERE id=?", (pid,))["options"])["glow_info"]
    assert len(info) == 2 and all(v["quelle"] == "nacht" for v in info.values())
    assert list((s.work_dir / "glow").glob("*.npz")), "Glüh-Bibliothek fehlt"
    # Rohbilder sind gelöscht – die Bibliothek bleibt und liefert das Modell weiter
    pl.process(pid)
    info = json.loads(db.one("SELECT options FROM projects WHERE id=?", (pid,))["options"])["glow_info"]
    assert list(info.values())[0]["quelle"] == "nacht"

    s2 = load_settings(tmp_path / "data2")                       # mit Darks: kein Glühmodell
    db2 = DB(s2.db_path)
    pl2 = Pipeline(s2, db2, ReviewerConfig())
    demo.generate(s2.input_dir / "n", n_lights=36, n_darks=4, n_flats=0)
    pl2.scan()
    pid2 = db2.one("SELECT id FROM projects")["id"]
    pl2.process(pid2)
    assert "glow_info" not in json.loads(db2.one("SELECT options FROM projects WHERE id=?", (pid2,))["options"])
