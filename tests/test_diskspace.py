"""Volles Datenlaufwerk: Aufträge halten an statt endlos zu scheitern, Import kopiert nicht über den Rand hinaus."""
import errno
import threading

import pytest

from astroflow.core.config import load_settings
from astroflow.modules.stacking import diskspace
from astroflow.server import create_app


def engine(tmp_path):
    s = load_settings(tmp_path / "data")
    s.auto = False
    eng = create_app(s).state.modules["stacking"].eng
    eng.pl.finish = lambda *a, **k: 1                # Endbearbeitung (eigener Thread) hier ohne Wirkung
    pid = eng.db.x("INSERT INTO projects(name) VALUES('M31')")
    return eng, pid


def run_once(eng):
    """Worker so lange laufen lassen, bis die Warteschlange leer ist oder er anhält."""
    def stop_soon():
        eng.stop.wait(1.5)
        eng.stop.set()
    threading.Thread(target=stop_soon, daemon=True).start()
    eng.worker()


def test_disk_full_pauses_queue_and_requeues_job(tmp_path):
    eng, pid = engine(tmp_path)
    jid = eng.enqueue(pid)

    def process(pid, opts, progress):
        raise OSError(errno.ENOSPC, "No space left on device")

    eng.pl.stack = process
    run_once(eng)
    job = eng.db.one("SELECT state, message FROM jobs WHERE id=?", (jid,))
    assert job["state"] == "queued" and "Fortsetzen" in job["message"]
    assert eng.paused and "voll" in eng.hold()
    assert eng.status()["paused"] is True

    calls = []
    eng.pl.stack = lambda pid, opts, progress: calls.append(pid)
    eng.set_paused(False)
    eng.stop.clear()
    run_once(eng)
    assert calls == [pid] and eng.db.one("SELECT state FROM jobs WHERE id=?", (jid,))["state"] == "done"


def test_pause_cancels_running_job_at_next_progress(tmp_path):
    eng, pid = engine(tmp_path)
    jid = eng.enqueue(pid)

    def process(pid, opts, progress):
        eng.set_paused(True)                       # Nutzer klickt „Aufträge anhalten“
        progress(0.5, "Stacking")
        raise AssertionError("hätte abbrechen müssen")

    eng.pl.stack = process
    run_once(eng)
    assert eng.db.one("SELECT state FROM jobs WHERE id=?", (jid,))["state"] == "queued"
    assert eng.hold() == "Warteschlange angehalten."


def test_low_space_holds_new_jobs(tmp_path, monkeypatch):
    eng, pid = engine(tmp_path)
    monkeypatch.setattr(diskspace, "free", lambda p: 1e9)
    eng.enqueue(pid)
    eng.pl.stack = lambda *a: pytest.fail("darf bei 1 GB frei nicht starten")
    run_once(eng)
    assert "1,0 GB frei" in eng.hold()


def test_import_refuses_copies_that_do_not_fit(tmp_path, monkeypatch):
    eng, _ = engine(tmp_path)
    lib = eng.library
    lib.groups = [{"id": "g0", "kind": "lights", "dir": tmp_path / "quelle", "to_import": [tmp_path / "x.fits"],
                   "import_bytes": 50e9}]
    monkeypatch.setattr(diskspace, "same_drive", lambda a, b: False)
    monkeypatch.setattr(diskspace, "free", lambda p: 60e9)
    with pytest.raises(ValueError, match="Zu wenig Speicher"):
        lib.start_import(["g0"])
    monkeypatch.setattr(diskspace, "same_drive", lambda a, b: True)      # Hardlinks kosten nichts
    monkeypatch.setattr(lib, "_import", lambda chosen: None)
    assert lib.start_import(["g0"]) == 1
