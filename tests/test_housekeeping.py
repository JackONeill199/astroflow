"""Speicherbelegung und Aufräumen: nur Ungenutztes verschwindet, Rohdaten/Stacks/neueste Ergebnisse bleiben."""
import json
import os
import time
from pathlib import Path

from astroflow.core.config import load_settings
from astroflow.modules.stacking import demo, housekeeping
from astroflow.modules.stacking.db import DB
from astroflow.modules.stacking.pipeline import Pipeline
from astroflow.modules.stacking.reviewer import ReviewerConfig


def _old(p: Path):
    t = time.time() - 2 * housekeeping.TEMP_AGE_S
    os.utime(p, (t, t))


def test_scan_and_cleanup(tmp_path):
    s = load_settings(tmp_path / "data")
    s.delete_uploads = False
    db = DB(s.db_path)
    pl = Pipeline(s, db, ReviewerConfig())
    demo.generate(s.uploads_dir / "nacht1", n_lights=10, n_darks=0, n_flats=0)
    pl.scan()
    pid = db.one("SELECT id FROM projects")["id"]
    for _ in range(3):                                            # drei Versionen
        pl.process(pid, {"mode": "develop"}) if db.one("SELECT id FROM results") else pl.process(pid)
    results = db.q("SELECT id, files FROM results ORDER BY id")
    assert len(results) == 3
    # Ungenutztes anlegen: verwaiste Ausgabedatei, Stack eines gelöschten Objekts, alter Temp-Ordner, leerer Ordner
    stray = s.output_dir / "Alt" / "rest.jpg"
    stray.parent.mkdir(parents=True)
    stray.write_bytes(b"x" * 1000)
    ghost = s.work_dir / "p999"
    ghost.mkdir()
    (ghost / "sw.npy").write_bytes(b"x" * 500)
    tmp = s.work_dir / "bildimport-abc"
    tmp.mkdir()
    (tmp / "bild").write_bytes(b"x" * 200)
    _old(tmp)
    empty = s.uploads_dir / "leer" / "unten"
    empty.mkdir(parents=True)
    _old(empty), _old(empty.parent)

    u = housekeeping.usage(s, db)
    assert u["total"] > 0 and u["projects"][0]["id"] == pid and u["projects"][0]["versions"] == 3
    items = {i["key"]: i for i in housekeeping.scan(s, db, keep=2)}
    assert items["orphan_output"]["count"] == 1 and items["orphan_output"]["bytes"] == 1000
    assert items["orphan_stacks"]["count"] == 1 and items["temp"]["count"] == 1
    assert items["empty_dirs"]["count"] == 2
    assert items["old_versions"]["count"] == 1 and not items["old_versions"]["default"]

    r = housekeeping.cleanup(s, db, ["orphan_output", "orphan_stacks", "temp", "empty_dirs", "old_versions", "db_compact"], keep=2)
    assert r["freed"] > 1700
    assert not stray.exists() and not ghost.exists() and not tmp.exists() and not (s.uploads_dir / "leer").exists()
    left = db.q("SELECT id, files FROM results ORDER BY id")
    assert [x["id"] for x in left] == [results[1]["id"], results[2]["id"]]          # neueste zwei bleiben
    assert not any(Path(f).exists() for f in json.loads(results[0]["files"]).values())
    assert all(Path(f).exists() for x in left for f in json.loads(x["files"]).values())
    assert (s.work_dir / f"p{pid}").exists()                                         # Master-Stack bleibt
    assert len(list((s.uploads_dir / "nacht1" / "lights").glob("*.fit"))) == 10      # Rohdaten bleiben
    assert {i["key"] for i in housekeeping.scan(s, db, keep=2)} <= {"db_compact", "empty_dirs"}


def test_cleanup_api_refuses_while_job_runs(tmp_path):
    from fastapi.testclient import TestClient
    from astroflow.server import create_app
    s = load_settings(tmp_path / "data")
    s.auto = False
    with TestClient(create_app(s)) as c:
        d = c.get("/api/stacking/housekeeping").json()
        assert "usage" in d and "unused" in d
        db = DB(Path(tmp_path / "data" / "astroflow.db"))
        db.x("INSERT INTO jobs(project_id, state, created) VALUES(NULL, 'running', 0)")
        assert c.post("/api/stacking/housekeeping/cleanup", json={"keys": ["temp"]}).status_code == 409
