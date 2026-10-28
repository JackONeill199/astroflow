import json

import numpy as np
import pytest

from astroflow import channels, demo
from astroflow.config import load_settings
from astroflow.db import DB
from astroflow.pipeline import Pipeline
from astroflow.reviewer import ReviewerConfig


def test_band_detection():
    assert channels.band_of("Ha 7nm", False) == "Ha"
    assert channels.band_of("H-alpha", False) == "Ha"
    assert channels.band_of("OIII", False) == "OIII"
    assert channels.band_of("S2", False) == "SII"
    assert channels.band_of("Red", False) == "R"
    assert channels.band_of("Lum", False) == "L"
    assert channels.band_of(None, False) == "L"
    assert channels.band_of("Ha", True) is None          # Farbkamera mit Dualband-Filter: ein Stack
    assert channels.auto_palette({"L", "R", "G", "B"}) == "LRGB"
    assert channels.auto_palette({"Ha", "OIII"}) == "HOO"
    assert channels.auto_palette({"Ha", "OIII", "SII"}) == "SHO"


def test_combine_lrgb_keeps_color_and_uses_luminance():
    rng = np.random.default_rng(0)
    base = np.zeros((1, 64, 64), np.float32)
    base[0, 20:40, 20:40] = 10
    cov = np.ones((64, 64), np.float32)
    st = {"R": (base * 1.5 + rng.normal(0, 1, base.shape), cov), "G": (base + rng.normal(0, 1, base.shape), cov),
          "B": (base * 0.5 + rng.normal(0, 1, base.shape), cov), "L": (base + rng.normal(0, 0.1, base.shape), cov)}
    img, c, pal, nb = channels.combine(st)
    assert pal == "LRGB" and img.shape == (3, 64, 64) and not nb
    # Luminanz-Rauschen im Hintergrund sinkt deutlich durch L
    assert img.mean(0)[:15, :15].std() < 0.5 * channels._norm(st["G"][0][0], cov)[:15, :15].std()


@pytest.mark.parametrize("bands,palette", [(("L", "R", "G", "B"), "LRGB"), (("Ha", "OIII", "SII"), "SHO")])
def test_mono_multifilter_pipeline(tmp_path, bands, palette):
    s = load_settings(tmp_path / "data")
    demo.generate_mono(s.input_dir / "mono", bands=bands, per_band=5)
    db = DB(s.db_path)
    pl = Pipeline(s, db, ReviewerConfig())
    pl.scan()
    projects = db.q("SELECT * FROM projects")
    assert len(projects) == 1                         # alle Filter landen im selben Projekt
    rid = pl.process(projects[0]["id"])
    st = json.loads(db.one("SELECT stats FROM results WHERE id=?", (rid,))["stats"])
    assert st["palette"] == palette
    assert set(st["bands"]) == set(bands)
    assert 0.03 < st["median"] < 0.4 and st["clipped_pct"] < 3
    for b in bands:
        assert (s.work_dir / f"p{projects[0]['id']}" / f"stack_{b}" / "sw.npy").exists()
