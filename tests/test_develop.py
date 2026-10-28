"""Develop Engine: Neu entwickeln aus dem Master-Stack, Qualitätskontrolle, Vorher-Bild."""
import json
from pathlib import Path

import numpy as np
import pytest

from astroflow.modules.stacking import demo, develop
from astroflow.core.config import load_settings
from astroflow.modules.stacking.db import DB
from astroflow.modules.stacking.pipeline import Pipeline
from astroflow.modules.stacking.reviewer import ReviewerConfig


def test_develop_only_uses_master_stack(tmp_path):
    s = load_settings(tmp_path / "data")
    s.delete_uploads = True
    db = DB(s.db_path)
    pl = Pipeline(s, db, ReviewerConfig())
    with pytest.raises(ValueError, match="Master-Stack"):
        db.x("INSERT INTO projects(name, created) VALUES('leer', 0)")
        pl.develop(db.one("SELECT id FROM projects")["id"])
    demo.generate(s.uploads_dir / "n", n_lights=14, n_darks=0, n_flats=0)
    pl.scan()
    pid = db.one("SELECT id FROM projects WHERE name!='leer'")["id"]
    pl.process(pid)
    assert not list((s.uploads_dir / "n" / "lights").glob("*.fit"))      # Rohdaten weg

    stages = []
    rid = pl.process(pid, {"mode": "develop", "params": {"saturation": 1.1}}, lambda p, st, m="": stages.append(st))
    assert "Stacking" not in stages and "Analyse" not in stages
    r = db.one("SELECT * FROM results WHERE id=?", (rid,))
    st, files = json.loads(r["stats"]), json.loads(r["files"])
    q = st["quality"]
    assert 0 <= q["score"] <= 10 and "metrics" in q and isinstance(q["hints"], list)
    assert st["params"]["saturation"] == 1.1                     # Nutzerwert bleibt, Automatik fasst ihn nicht an
    assert Path(files["before"]).exists()
    from PIL import Image
    assert Image.open(files["before"]).size == Image.open(files["jpg"]).size   # gleicher Ausschnitt für den Vergleich


def test_assess_flags_measurable_problems():
    rng = np.random.default_rng(0)
    good = np.full((3, 300, 300), 0.04, np.float32) + rng.normal(0, 0.002, (3, 300, 300)).astype(np.float32)
    yy, xx = np.mgrid[0:300, 0:300]
    good += (0.45 * np.exp(-((xx - 150) ** 2 + (yy - 150) ** 2) / 2000) * np.array([1.0, 0.6, 0.5])[:, None, None]).astype(np.float32)
    assert develop.assess(good)["score"] == 10, develop.assess(good)["issues"]
    dark = good - 0.035
    assert "bg_dark" in {i["code"] for i in develop.assess(np.clip(dark, 0, 1))["issues"]}
    blotchy = good.copy()
    blotchy[0] += np.kron(rng.normal(0, 0.03, (30, 30)), np.ones((10, 10))).astype(np.float32)
    assert "chroma_noise" in {i["code"] for i in develop.assess(np.clip(blotchy, 0, 1))["issues"]}
