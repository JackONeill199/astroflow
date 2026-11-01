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


def test_burnt_core_detected_and_white_point_raised():
    rng = np.random.default_rng(1)
    L = np.full((400, 600), 0.04, np.float32)
    for _ in range(30):                                          # Sternkerne dürfen weiß sein
        y, x = rng.integers(10, 390), rng.integers(10, 590)
        L[y - 2 : y + 3, x - 2 : x + 3] = 1.0
    assert develop.burnt_core(L) == 0.0
    yy, xx = np.mgrid[0:400, 0:600]
    L[((yy - 200) / 9) ** 2 + ((xx - 300) / 14) ** 2 < 1] = 1.0  # flächiger weißer Galaxienkern
    assert develop.burnt_core(L) > 0
    qc = develop.assess(np.repeat(L[None], 3, axis=0))
    assert "burnt_core" in {i["code"] for i in qc["issues"]}
    adj = develop.corrections(qc, {"asinh": 30, "denoise": 0.5, "chroma_denoise": 2, "saturation": 1.3, "target_bg": 0.07})
    assert adj["white_pct"] > 99.9 and adj["asinh"] > 30
    last = develop.corrections(qc, {"asinh": 30, "white_pct": develop.WHITE_STEPS[-1], "denoise": 0.5, "chroma_denoise": 2,
                                    "saturation": 1.3, "target_bg": 0.07})
    assert "white_pct" not in last                               # Ende der Stufen: nicht endlos nachregeln


def test_develop_all_queues_only_projects_with_stack(tmp_path):
    from fastapi.testclient import TestClient
    from astroflow.server import create_app
    s = load_settings(tmp_path / "data")
    s.auto = False
    db = DB(s.db_path)
    pl = Pipeline(s, db, ReviewerConfig())
    demo.generate(s.uploads_dir / "n", n_lights=10, n_darks=0, n_flats=0)
    pl.scan()
    pid = db.one("SELECT id FROM projects")["id"]
    pl.process(pid)
    db.x("INSERT INTO projects(name, created) VALUES('ohne Stack', 0)")
    db.x("INSERT INTO projects(name, created, options) VALUES('Foto', 0, ?)", (json.dumps({"input_type": "picture"}),))
    with TestClient(create_app(s)) as c:
        assert c.post("/api/stacking/develop-all", json={}).json() == {"queued": 1}
        job = db.one("SELECT * FROM jobs ORDER BY id DESC LIMIT 1")
        assert job["project_id"] == pid and json.loads(job["options"]) == {"mode": "develop"}
