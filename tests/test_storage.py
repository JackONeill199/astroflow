"""Hochgeladene Einzelbilder nach dem Stacking löschen – ohne Datenverlust für den Stack."""
import json
import shutil
from pathlib import Path

import pytest

from astroflow.modules.stacking import demo
from astroflow.core.config import load_settings
from astroflow.modules.stacking.db import DB
from astroflow.modules.stacking.pipeline import Pipeline
from astroflow.modules.stacking.reviewer import ReviewerConfig


def _env(tmp_path, delete=True):
    s = load_settings(tmp_path / "data")
    s.delete_uploads = delete
    db = DB(s.db_path)
    return s, db, Pipeline(s, db, ReviewerConfig())


def test_uploaded_lights_deleted_after_stacking_and_stack_keeps_growing(tmp_path):
    s, db, pl = _env(tmp_path)
    demo.generate(s.uploads_dir / "nacht1", n_lights=16, n_darks=3, n_flats=3)
    pl.scan()
    pid = db.one("SELECT id FROM projects")["id"]
    seen = []                                         # andere Module (SkyTrace) bekommen die Rohbilder vor dem Löschen
    pl.on_stacked = lambda p, rid: seen.append((p, rid, len(list((s.uploads_dir / "nacht1" / "lights").glob("*.fit")))))
    pl.process(pid)
    assert seen and seen[0][0] == pid and seen[0][1] and seen[0][2] == 16
    lights = db.q("SELECT * FROM frames WHERE kind='light'")
    assert all(f["deleted"] for f in lights if f["stacked"] or f["status"] == "rejected")
    assert not list((s.uploads_dir / "nacht1" / "lights").glob("*.fit")), "Lights sollten gelöscht sein"
    assert len(list((s.uploads_dir / "nacht1" / "darks").glob("*.fit"))) == 3, "Kalibrierbilder bleiben"
    n1 = db.one("SELECT n_frames FROM results ORDER BY id DESC LIMIT 1")["n_frames"]

    # erneut verarbeiten ohne neue Aufnahmen: nur Bearbeitung aus dem gespeicherten Stack
    pl.process(pid)
    assert db.one("SELECT n_frames FROM results ORDER BY id DESC LIMIT 1")["n_frames"] == n1
    assert len(seen) == 1, "Ohne neue Aufnahmen keine erneuten Entdeckungen/Erfolge"
    pl.process(pid, {"mode": "develop"})
    assert len(seen) == 1, "Auch explizites Neu-Entwickeln meldet keine neue Beobachtung"

    # neue Nacht wird auf den bestehenden Stack nachgestackt (gleiches Sternfeld wie Nacht 1)
    demo.generate(s.uploads_dir / "nacht2", n_lights=8, n_darks=0, n_flats=0, seed=1)
    for f in (s.uploads_dir / "nacht2" / "lights").glob("*.fit"):
        f.rename(f.with_name("N2_" + f.name))     # neue Dateien (anderer Name/Pfad)
    pl.scan()
    pl.process(pid)
    n2 = db.one("SELECT n_frames FROM results ORDER BY id DESC LIMIT 1")["n_frames"]
    assert n2 > n1
    assert len(seen) == 2, "Neue gestackte Aufnahmen melden wieder eine Beobachtung"
    assert not list((s.uploads_dir / "nacht2" / "lights").glob("*.fit"))

    # komplett neu stacken geht nicht mehr – klare Meldung statt kaputtem Stack
    db.x("UPDATE projects SET restack=1 WHERE id=?", (pid,))
    with pytest.raises(ValueError, match="nicht möglich"):
        pl.process(pid)
    assert db.one("SELECT restack FROM projects WHERE id=?", (pid,))["restack"] == 0
    pl.process(pid)                                   # danach läuft alles normal weiter


def test_files_outside_uploads_are_never_deleted(tmp_path):
    s, db, pl = _env(tmp_path)
    demo.generate(s.input_dir / "nas", n_lights=10, n_darks=0, n_flats=0)
    pl.scan()
    pl.process(db.one("SELECT id FROM projects")["id"])
    assert len(list((s.input_dir / "nas" / "lights").glob("*.fit"))) == 10
    assert db.one("SELECT COUNT(*) n FROM frames WHERE deleted=1")["n"] == 0


def test_option_off_keeps_uploads(tmp_path):
    s, db, pl = _env(tmp_path, delete=False)
    demo.generate(s.uploads_dir / "n", n_lights=10, n_darks=0, n_flats=0)
    pl.scan()
    pl.process(db.one("SELECT id FROM projects")["id"])
    assert len(list((s.uploads_dir / "n" / "lights").glob("*.fit"))) == 10


def test_restack_same_frames_does_not_report_new_observation(tmp_path):
    s, db, pl = _env(tmp_path, delete=False)
    demo.generate(s.uploads_dir / "n", n_lights=10, n_darks=0, n_flats=0)
    pl.scan()
    pid = db.one("SELECT id FROM projects")["id"]
    seen = []
    pl.on_stacked = lambda p, rid: seen.append((p, rid))
    pl.process(pid)
    assert len(seen) == 1
    db.x("UPDATE projects SET restack=1 WHERE id=?", (pid,))
    pl.process(pid)
    assert len(seen) == 1, "Neu-Stacken derselben Aufnahmen ist keine neue Beobachtung"


@pytest.mark.parametrize("failure", ["develop", "notify"])
def test_pending_observation_survives_restart_before_upload_cleanup(tmp_path, monkeypatch, failure):
    s, db, pl = _env(tmp_path)
    demo.generate(s.uploads_dir / "n", n_lights=10, n_darks=0, n_flats=0)
    pl.scan()
    pid = db.one("SELECT id FROM projects")["id"]

    def interrupted(*args, **kwargs):
        raise RuntimeError("Unterbrochen")

    if failure == "develop":
        monkeypatch.setattr(pl, "develop", interrupted)
    else:
        pl.on_stacked = interrupted
    with pytest.raises(RuntimeError, match="Unterbrochen"):
        pl.process(pid)
    pending = db.q("SELECT id FROM frames WHERE stacked=1 AND stack_notified=0")
    assert pending, "Der Stack-Checkpoint muss bereits gespeichert sein"
    assert db.one("SELECT COUNT(*) n FROM frames WHERE deleted=1")["n"] == 0

    # Neue Pipeline + DB-Verbindung: kein neuer Frame kommt hinzu, die Meldung ist aber noch offen.
    resumed_db = DB(s.db_path)
    resumed = Pipeline(s, resumed_db, ReviewerConfig())
    seen = []

    def notify(p, rid):
        rows = resumed_db.q("SELECT path FROM frames WHERE stacked=1 AND stack_notified=0")
        assert len(rows) == len(pending) and all(Path(r["path"]).exists() for r in rows)
        seen.append((p, rid))

    resumed.on_stacked = notify
    resumed.process(pid)
    assert len(seen) == 1
    assert resumed_db.one("SELECT COUNT(*) n FROM frames WHERE stacked=1 AND stack_notified=0")["n"] == 0
    assert not list((s.uploads_dir / "n" / "lights").glob("*.fit"))
    again = Pipeline(s, DB(s.db_path), ReviewerConfig())
    again.on_stacked = lambda p, rid: seen.append((p, rid))
    again.process(pid)
    assert len(seen) == 1, "Die Bestätigung muss auch einen weiteren Neustart überleben"


def test_notification_migration_keeps_interrupted_legacy_jobs_pending(tmp_path):
    import sqlite3
    from astroflow.modules.stacking.db import SCHEMA
    path = tmp_path / "alt.db"
    with sqlite3.connect(path) as c:
        c.executescript(SCHEMA)
        c.execute("ALTER TABLE frames ADD COLUMN deleted INTEGER DEFAULT 0")
        c.executemany("INSERT INTO jobs(project_id,state) VALUES(?,?)", [(1, "done"), (2, "done"), (2, "running"), (3, "error")])
        c.execute("INSERT INTO jobs(project_id,state,options) VALUES(5,'done',?)", (json.dumps({"mode": "develop"}),))
        c.execute("INSERT INTO jobs(project_id,state,options) VALUES(6,'done',?)", (json.dumps({"action": "solve"}),))
        c.executemany("INSERT INTO frames(id,project_id,stacked,deleted) VALUES(?,?,?,?)",
                      [("fertig", 1, 1, 0), ("abbruch", 2, 1, 0), ("fehler", 3, 1, 0), ("geloescht", 4, 1, 1), ("neu", 1, 0, 0),
                       ("entwickelt", 5, 1, 0), ("geloest", 6, 1, 0)])
    db = DB(path)
    assert {r["id"]: r["stack_notified"] for r in db.q("SELECT id,stack_notified FROM frames")} == {
        "fertig": 1, "abbruch": 0, "fehler": 0, "geloescht": 1, "neu": 0, "entwickelt": 0, "geloest": 0}


def test_duplicate_upload_removed_immediately(tmp_path):
    s, db, pl = _env(tmp_path, delete=False)                 # Original behalten, nur die Kopie betrachten
    demo.generate(s.input_dir / "nas", n_lights=5, n_darks=0, n_flats=0)
    pl.scan()
    s.delete_uploads = True
    shutil.copytree(s.input_dir / "nas", s.uploads_dir / "kopie")
    r = pl.scan()
    assert r["duplicates"] == 5
    assert not list((s.uploads_dir / "kopie" / "lights").glob("*.fit"))
    assert len(list((s.input_dir / "nas" / "lights").glob("*.fit"))) == 5


def test_delete_project_removes_everything_but_never_mounted_files(tmp_path):
    s, db, pl = _env(tmp_path, delete=False)
    demo.generate(s.uploads_dir / "up", n_lights=12, n_darks=0, n_flats=0)
    pl.scan()
    pid = db.one("SELECT id FROM projects")["id"]
    pl.process(pid)
    out = json.loads(db.one("SELECT files FROM results")["files"])
    r = pl.delete_project(pid)
    assert r["files"] == 12 and r["bytes"] > 0
    assert not list((s.uploads_dir / "up" / "lights").glob("*.fit"))
    assert not any(Path(f).exists() for f in out.values())
    assert not (s.work_dir / f"p{pid}").exists()
    assert db.one("SELECT COUNT(*) n FROM projects")["n"] == 0 and db.one("SELECT COUNT(*) n FROM results")["n"] == 0
    # erneut hochgeladen -> wird wieder ganz normal verarbeitet (kein „Duplikat“ eines gelöschten Objekts)
    demo.generate(s.uploads_dir / "up2", n_lights=12, n_darks=0, n_flats=0)
    assert pl.scan()["new_frames"] == 12 and db.one("SELECT COUNT(*) n FROM projects")["n"] == 1

    # eingebundener Ordner: Dateien bleiben, Objekt kommt beim nächsten Scan nicht wieder
    s2, db2, pl2 = _env(tmp_path / "b", delete=False)
    demo.generate(s2.input_dir / "nas", n_lights=10, n_darks=0, n_flats=0, seed=3)
    pl2.scan()
    pid2 = db2.one("SELECT id FROM projects")["id"]
    assert pl2.delete_project(pid2)["ignored"] == 10
    assert len(list((s2.input_dir / "nas" / "lights").glob("*.fit"))) == 10
    pl2.scan()
    assert db2.one("SELECT COUNT(*) n FROM projects")["n"] == 0
