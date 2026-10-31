import json
from pathlib import Path

import numpy as np
import pytest

from astroflow.modules.stacking import demo, registration, quality
from astroflow.core.config import load_settings
from astroflow.modules.stacking.db import DB
from astroflow.modules.stacking.pipeline import Pipeline
from astroflow.modules.stacking.reviewer import ReviewerConfig, parse_reply


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    root = tmp_path_factory.mktemp("af")
    s = load_settings(root / "data")
    demo.generate(s.input_dir / "night", n_lights=30, n_darks=6, n_flats=6)
    db = DB(s.db_path)
    return s, db, Pipeline(s, db, ReviewerConfig())


def test_scan_groups_and_classifies(env):
    s, db, pl = env
    r = pl.scan()
    assert r["new_frames"] == 30 + 6 + 6
    projects = db.q("SELECT * FROM projects")
    assert len(projects) == 1 and projects[0]["obj_id"] == "M31" and projects[0]["kind"] == "galaxy"
    kinds = {k["kind"]: k["n"] for k in db.q("SELECT kind, COUNT(*) n FROM frames GROUP BY kind")}
    assert kinds == {"light": 30, "dark": 6, "flat": 6}
    assert pl.scan()["new_frames"] == 0           # idempotent


def test_full_pipeline(env):
    s, db, pl = env
    pl.scan()
    pid = db.q("SELECT id FROM projects")[0]["id"]
    rid = pl.process(pid)
    res = db.one("SELECT * FROM results WHERE id=?", (rid,))
    files = json.loads(res["files"])
    for k in ("png", "jpg", "fits"):
        assert Path(files[k]).exists()
    frames = db.q("SELECT status, reason FROM frames WHERE kind='light'")
    rejected = [f for f in frames if f["status"] == "rejected"]
    assert 2 <= len(rejected) <= 12, rejected
    assert res["n_frames"] >= 18
    stats = json.loads(res["stats"])
    assert 0.02 < stats["median"] < 0.35 and stats["clipped_pct"] < 2     # dunkler Hintergrund (bg_dark 0,03)
    assert "Flat" in db.q("SELECT metrics FROM frames WHERE status!='new' AND kind='light' LIMIT 1")[0]["metrics"]


def test_incremental_restack_adds_frames(env):
    s, db, pl = env
    pid = db.q("SELECT id FROM projects")[0]["id"]
    before = db.one("SELECT n_frames FROM results ORDER BY id DESC LIMIT 1")["n_frames"]
    # ein bisher verworfenes Bild manuell einschließen -> nur dieses wird nachgestackt
    r = db.q("SELECT id FROM frames WHERE status='rejected' AND kind='light'")[0]["id"]
    db.set_frame(r, manual=1)
    pl.process(pid)
    after = db.one("SELECT n_frames FROM results ORDER BY id DESC LIMIT 1")["n_frames"]
    assert after == before + 1


def test_registration_recovers_rotation():
    rng = np.random.default_rng(0)
    ref = rng.uniform(0, 400, (60, 2))
    th = np.deg2rad(2.0)
    R = np.array([[np.cos(th), -np.sin(th)], [np.sin(th), np.cos(th)]])
    tgt = (ref - 200) @ R.T + 200 + [5.5, -3.2]
    tgt = tgt[rng.permutation(len(tgt))][:50]
    M, pairs, rms = registration.find_transform(ref, tgt)
    assert rms < 0.05
    back = tgt @ M[:2, :2].T + M[:2, 2]
    assert np.abs(back[pairs[:, 0]] - ref[pairs[:, 1]]).max() < 0.1


def test_reviewer_reply_parsing_clamps_values():
    r = parse_reply('Hier: {"ok": false, "score": 6, "issues": ["zu hell"], "adjust": {"target_bg": 0.9, "foo": 1, "denoise": "0.4"}}')
    assert r["adjust"] == {"target_bg": 0.30, "denoise": 0.4} and not r["ok"]


def test_duplicate_upload_is_not_stacked_twice(tmp_path):
    import shutil
    s = load_settings(tmp_path / "data")
    demo.generate(s.input_dir / "nacht1", n_lights=6, n_darks=0, n_flats=0)
    db = DB(s.db_path)
    pl = Pipeline(s, db, ReviewerConfig())
    assert pl.scan()["new_frames"] == 6
    shutil.copytree(s.input_dir / "nacht1", s.input_dir / "uploads" / "nochmal")   # gleicher Ordner erneut hochgeladen
    r = pl.scan()
    assert r["duplicates"] == 6 and r["new_frames"] == 0
    pid = db.one("SELECT id FROM projects")["id"]
    assert r["duplicates_by_project"] == {pid: 6}                                  # Import zeigt „bereits in Objekt X“
    assert db.one("SELECT COUNT(*) n FROM frames WHERE kind='light'")["n"] == 6
    assert pl.scan()["new_frames"] == 0                                             # bleibt erkannt


def test_existing_duplicates_marked_retroactively(tmp_path):
    import shutil
    s = load_settings(tmp_path / "data")
    demo.generate(s.input_dir / "a", n_lights=4, n_darks=0, n_flats=0)
    shutil.copytree(s.input_dir / "a", s.input_dir / "b")
    db = DB(s.db_path)
    pl = Pipeline(s, db, ReviewerConfig())
    pl.scan()
    # Altbestand simulieren: alles als Light ohne Fingerabdruck (wie vor Einführung der Erkennung)
    for row in db.q("SELECT id, info FROM frames"):
        i = json.loads(row["info"]); i.pop("fp", None)
        db.x("UPDATE frames SET kind='light', status='new', info=? WHERE id=?", (json.dumps(i), row["id"]))
    assert db.one("SELECT COUNT(*) n FROM frames WHERE kind='light'")["n"] == 8
    pl.scan()
    assert db.one("SELECT COUNT(*) n FROM frames WHERE kind='light'")["n"] == 4
    assert db.one("SELECT COUNT(*) n FROM frames WHERE kind='duplicate'")["n"] == 4
