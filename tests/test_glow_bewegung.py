"""Glühmessung nur bei bewegtem Himmel: steht das Feld still (EQ-Montierung ohne Dithering), lernen Median und
Perzentil den Himmel selbst als „Glühen“ (Sh2 101, Mono-RGB: senkrechte Streifen und Flecken am Rand)."""
import json

import numpy as np

from astroflow.core.config import load_settings
from astroflow.modules.stacking import calibration, demo, pipeline
from astroflow.modules.stacking.db import DB
from astroflow.modules.stacking.pipeline import Pipeline
from astroflow.modules.stacking.reviewer import ReviewerConfig

SHAPE = (2080, 3072)


def _stars(rng, n=80):
    return np.c_[rng.uniform(50, SHAPE[1] - 50, n), rng.uniform(50, SHAPE[0] - 50, n), rng.uniform(1e3, 1e5, n)]


def _moved(st, dx, dy, ang_deg=0.0):
    a = np.radians(ang_deg)
    c = np.array([SHAPE[1] / 2, SHAPE[0] / 2])
    R = np.array([[np.cos(a), -np.sin(a)], [np.sin(a), np.cos(a)]])
    xy = (st[:, :2] - c) @ R.T + c + [dx, dy]
    out = np.c_[xy, st[:, 2]]
    return out[np.argsort(-out[:, 2])]                    # hellste zuerst wie die Sternliste der Analyse


def test_glow_motion_static_drift_and_rotation():
    rng = np.random.default_rng(1)
    st = _stars(rng)
    still = [_moved(st, *rng.normal(0, 1.0, 2)) for _ in range(20)]                       # Nachführung, kein Dithering
    assert pipeline.glow_motion(still, SHAPE) < 3
    dith = [_moved(st, *rng.uniform(-60, 60, 2)) for _ in range(20)]                       # Dithering ±60 px
    assert pipeline.glow_motion(dith, SHAPE) > calibration.GLOW_MIN_MOTION_PX
    rot = [_moved(st, 0, 0, a) for a in np.linspace(-8, 8, 20)]                            # Feldrotation (Alt-Az)
    assert pipeline.glow_motion(rot, SHAPE) > 3 * calibration.GLOW_MIN_MOTION_PX
    assert pipeline.glow_motion(still[:4], SHAPE) is None                                   # zu wenige Aufnahmen


def _opts(db, pid):
    return json.loads(db.one("SELECT options FROM projects WHERE id=?", (pid,))["options"])


def test_static_field_learns_no_glow(tmp_path, monkeypatch):
    """Demo-Feld wandert kaum: keine eigene Glühmessung, Grund in glow_info, Bewegung vermerkt; mit kleiner Schwelle schon."""
    monkeypatch.setattr(calibration, "GLOW_MIN_MOTION_PX", 40.0)
    s = load_settings(tmp_path / "data")
    db = DB(s.db_path)
    pl = Pipeline(s, db, ReviewerConfig())
    demo.generate(s.input_dir / "n", n_lights=36, n_darks=0, n_flats=0)
    pl.scan()
    pid = db.one("SELECT id FROM projects")["id"]
    pl.stack(pid)
    info = _opts(db, pid)["glow_info"]
    assert len(info) == 2 and all(v["quelle"].startswith("keine") and v["adu"] == 0.0 for v in info.values())
    assert all(0 < v["bewegung_px"] < 40 for v in info.values())
    lib = calibration.GlowLibrary(s.work_dir / "glow")
    key = next(iter(s.work_dir.glob("glow/*.json"))).stem
    assert lib.nights(key) == [] and all(lib.motion(key, n) == v["bewegung_px"] for n, v in info.items())

    # gleiche Daten, Feld gilt als bewegt -> Messung je Nacht; ein späterer Lauf misst nicht erneut (Vermerk vorhanden)
    monkeypatch.setattr(calibration, "GLOW_MIN_MOTION_PX", 0.0)
    s2 = load_settings(tmp_path / "data2")
    db2 = DB(s2.db_path)
    pl2 = Pipeline(s2, db2, ReviewerConfig())
    demo.generate(s2.input_dir / "n", n_lights=36, n_darks=0, n_flats=0)
    pl2.scan()
    pid2 = db2.one("SELECT id FROM projects")["id"]
    pl2.stack(pid2)
    assert all(v["quelle"] == "nacht" and v["adu"] > 0 for v in _opts(db2, pid2)["glow_info"].values())


def test_mono_night_from_before_motion_check_is_remeasured(tmp_path):
    """Mono-Kamera, Nacht schon (ohne Bewegungsvermerk) in der Bibliothek – vor der Prüfung gelernt: wird neu gemessen,
    bei stehendem Feld aus der Bibliothek genommen. Farbkameras bleiben unberührt (eigener Fall oben)."""
    s = load_settings(tmp_path / "data")
    db = DB(s.db_path)
    pl = Pipeline(s, db, ReviewerConfig())
    demo.generate_mono(s.input_dir / "m", bands=("L",), per_band=14)          # 14 Aufnahmen über 65 min, Feld fast fest
    pl.scan()
    pid = db.one("SELECT id FROM projects")["id"]
    r = db.one("SELECT info, night FROM frames WHERE project_id=? LIMIT 1", (pid,))
    info, night = json.loads(r["info"]), r["night"]
    lib = calibration.GlowLibrary(s.work_dir / "glow")
    key = lib.key(info)
    h, w = info["shape"][-2:]
    fake = [(np.full(h, 5, np.float32), np.linspace(0, 80, w).astype(np.float32),
             np.zeros((h // calibration.GLOW_BIN, w // calibration.GLOW_BIN), np.float32))]
    lib.add(key, night, fake)                                                  # „alte“ Messung ohne Vermerk
    assert lib.motion(key, night) is None
    pl.stack(pid)
    assert lib.nights(key) == [] and lib.motion(key, night) is not None
    assert lib.motion(key, night) < calibration.GLOW_MIN_MOTION_PX
    assert _opts(db, pid)["glow_info"][night]["quelle"].startswith("keine")
