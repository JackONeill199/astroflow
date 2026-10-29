"""Aufnahmen, die während der Verarbeitung eintreffen, dürfen nicht in status='new' liegen bleiben."""
from astroflow.core.config import load_settings
from astroflow.server import create_app


def engine(tmp_path, auto=True):
    s = load_settings(tmp_path / "data")
    s.auto = auto
    eng = create_app(s).state.modules["stacking"].eng
    pid = eng.db.x("INSERT INTO projects(name) VALUES('M31')")
    return eng, pid


def add(eng, pid, *ids):
    eng.db.many("INSERT INTO frames(id,project_id,kind,status) VALUES(?,?,'light','new')", [(i, pid) for i in ids])


def test_late_uploads_get_a_followup_after_running_job(tmp_path):
    eng, pid = engine(tmp_path)
    add(eng, pid, "a", "b")
    first = eng.enqueue(pid)
    calls = []

    def process(pid, opts, progress):
        rows = eng.db.q("SELECT id FROM frames WHERE project_id=? AND status='new'", (pid,))
        calls.append(len(rows))
        eng.db.many("UPDATE frames SET status='selected',stacked=1 WHERE id=?", [(r["id"],) for r in rows])
        if len(calls) == 1:
            add(eng, pid, "c", "d", "e")
            assert eng.enqueue(pid) == first       # Scan findet Uploads, aber der bisherige Job läuft noch
        else:
            eng.stop.set()

    eng.pl.process = process
    eng.worker()
    assert calls == [2, 3]
    assert [r["state"] for r in eng.db.q("SELECT state FROM jobs ORDER BY id")] == ["done", "done"]
    assert eng.pending_projects() == set()


def test_watcher_recovers_already_scanned_uploads_and_respects_auto(tmp_path):
    eng, pid = engine(tmp_path)
    add(eng, pid, "a")
    eng.pl.scan = lambda: {"new_by_project": {}, "new_frames": 0}
    eng.s.auto = False
    eng.scan_and_queue()
    assert eng.db.q("SELECT id FROM jobs") == []
    eng.s.auto = True
    eng.scan_and_queue()
    eng.scan_and_queue()
    assert len(eng.db.q("SELECT id FROM jobs")) == 1


def test_failed_job_is_not_retried_without_another_upload(tmp_path):
    eng, pid = engine(tmp_path)
    add(eng, pid, "a")
    jid = eng.enqueue(pid)
    eng.db.x("UPDATE jobs SET state='error' WHERE id=?", (jid,))
    eng.pl.scan = lambda: {"new_by_project": {}, "new_frames": 0}
    eng.scan_and_queue()
    assert len(eng.db.q("SELECT id FROM jobs")) == 1
    add(eng, pid, "b")
    eng.pl.scan = lambda: {"new_by_project": {pid: 1}, "new_frames": 1}
    eng.scan_and_queue()
    assert len(eng.db.q("SELECT id FROM jobs")) == 2
