"""Aufnahmen, die während der Verarbeitung eintreffen, dürfen nicht in status='new' liegen bleiben."""
import threading
import time

from astroflow.core.config import load_settings
from astroflow.server import create_app


def engine(tmp_path, auto=True):
    s = load_settings(tmp_path / "data")
    s.auto = auto
    eng = create_app(s).state.modules["stacking"].eng
    eng.pl.finish = lambda *a, **k: 1                # Endbearbeitung (eigener Thread) hier ohne Wirkung
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

    eng.pl.stack = process
    eng.worker()
    assert calls == [2, 3]
    assert [r["state"] for r in eng.db.q("SELECT state FROM jobs ORDER BY id")] == ["done", "done"]
    assert eng.pending_projects() == set()


def test_watcher_recovers_already_scanned_uploads_and_respects_auto(tmp_path):
    eng, pid = engine(tmp_path)
    add(eng, pid, "a")
    eng.pl.scan = lambda extra=None: {"new_by_project": {}, "new_frames": 0}
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
    eng.pl.scan = lambda extra=None: {"new_by_project": {}, "new_frames": 0}
    eng.scan_and_queue()
    assert len(eng.db.q("SELECT id FROM jobs")) == 1
    add(eng, pid, "b")
    eng.pl.scan = lambda extra=None: {"new_by_project": {pid: 1}, "new_frames": 1}
    eng.scan_and_queue()
    assert len(eng.db.q("SELECT id FROM jobs")) == 2


def test_next_object_stacks_while_previous_develops(tmp_path):
    """Nach dem Stacken übernimmt der Entwicklungs-Thread – das nächste Objekt wird schon gestackt."""
    eng, p1 = engine(tmp_path, auto=False)
    p2 = eng.db.x("INSERT INTO projects(name) VALUES('M42')")
    eng.enqueue(p1)
    eng.enqueue(p2)
    developing, overlap = threading.Event(), []

    def stack(pid, opts, progress):
        if pid == p2:
            # Entwicklungs-Thread kann das Objekt schon übernommen, finish() aber noch nicht betreten haben –
            # kurz warten statt Momentaufnahme (lief sonst gelegentlich mit [False] durch); seriell bliebe es False
            overlap.append(developing.wait(2))

    def finish(pid, opts, progress):
        if pid == p1:
            developing.set()
            for _ in range(100):                  # Endbearbeitung dauert, bis M42 gestackt ist
                if overlap:
                    break
                time.sleep(0.05)
            developing.clear()
        else:
            eng.stop.set()

    eng.pl.stack, eng.pl.finish = stack, finish
    eng.worker()
    assert overlap == [True]
    assert [r["state"] for r in eng.db.q("SELECT state FROM jobs ORDER BY id")] == ["done", "done"]


def test_enqueue_skips_deleted_project(tmp_path):
    """Scan reiht ein, während das Objekt gelöscht wird: kein verwaister Auftrag (#24)."""
    eng, pid = engine(tmp_path, auto=False)
    eng.db.x("DELETE FROM projects WHERE id=?", (pid,))
    assert eng.enqueue(pid) == 0
    assert not eng.db.q("SELECT id FROM jobs")


def test_memory_error_in_analysis_is_no_read_error(tmp_path):
    """Knapper RAM bei der Analyse: Auftrag bricht ab, die Aufnahme gilt nicht als „Lesefehler“ (#23)."""
    import pytest
    eng, pid = engine(tmp_path, auto=False)
    f = tmp_path / "a.fits"
    f.write_bytes(b"x")
    eng.db.x("INSERT INTO frames(id,project_id,kind,status,path,info) VALUES('a',?,'light','new',?,'{}')", (pid, str(f)))
    eng.pl._sensor_maps = lambda *a, **k: (None, None)
    eng.pl.offline = lambda rows: set()

    def boom(*a, **k):
        raise MemoryError()
    eng.pl._load = boom
    with pytest.raises(MemoryError):
        eng.pl.stack(pid, {}, lambda *a, **k: None)
    assert eng.db.one("SELECT status FROM frames WHERE id='a'")["status"] == "new"


def test_stackstate_rename_retries(tmp_path, monkeypatch):
    """Windows: kurz gesperrter Ordner beim Austauschen des Stack-Zustands (#26)."""
    from pathlib import Path
    import numpy as np
    from astroflow.modules.stacking import stacking
    st = stacking.StackState((1, 4, 4))
    st.sw += 1
    st.save(tmp_path / "s")
    real, calls = Path.rename, []

    def flaky(self, dst):
        calls.append(self.name)
        if len(calls) <= 2:
            raise PermissionError("gesperrt")
        return real(self, dst)
    monkeypatch.setattr(Path, "rename", flaky)
    st.sw += 1
    st.save(tmp_path / "s")
    monkeypatch.undo()
    assert len(calls) >= 4 and float(stacking.StackState.load(tmp_path / "s").sw.max()) == 2
