"""Volles Datenlaufwerk: Aufträge halten an statt endlos zu scheitern, Import hält bei knappem Speicher an."""
import errno
import threading
import time

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
        # Nicht nach fester Zeit abbrechen (auf langsamen CI-Rechnern reichten 1,5 s für die Wiederholungen nicht):
        # sobald kein Auftrag mehr wartet/läuft oder die Warteschlange angehalten ist; harte Obergrenze nur gegen Hänger
        end = time.monotonic() + 60
        while time.monotonic() < end and not eng.stop.is_set():
            if eng.paused or eng.hold() or not eng.db.one("SELECT 1 AS n FROM jobs WHERE state IN ('queued','running') LIMIT 1"):
                break
            time.sleep(0.02)
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


def test_cleanup_allowed_while_queue_is_held(tmp_path):
    """Volle Platte: Aufräumen ist der Ausweg – wartende Aufträge dürfen es nicht blockieren."""
    from fastapi.testclient import TestClient
    s = load_settings(tmp_path / "data")
    s.auto = False
    app = create_app(s)
    eng = app.state.modules["stacking"].eng
    with TestClient(app) as c:
        eng.set_paused(True)                        # Worker steht, bevor ein Auftrag dazukommt
        eng.db.x("INSERT INTO jobs(project_id, state, created) VALUES(NULL, 'queued', 0)")
        assert c.post("/api/stacking/housekeeping/cleanup", json={"keys": ["temp"]}).status_code == 200
        assert eng.paused == "Warteschlange angehalten."              # bleibt angehalten
        eng.set_paused(False)
        assert c.post("/api/stacking/housekeeping/cleanup", json={"keys": ["temp"]}).status_code == 409
        assert eng.paused is None


def test_import_stopped_by_low_space_keeps_rest(tmp_path, monkeypatch):
    eng, _ = engine(tmp_path)
    lib = eng.library
    src = tmp_path / "quelle"
    src.mkdir()
    files = []
    for i in range(4):
        f = src / f"img-{i}.fits"
        f.write_bytes(b"SIMPLE  =".ljust(2880))        # minimal gültig: ein Block mit FITS-Kopfbeginn
        files.append(f)
    g = {"id": "g0", "kind": "lights", "dir": src, "files": files, "to_import": list(files), "n_files": 4,
         "n_known": 0, "n_new": 4, "stat": {f: (2880, 0) for f in files}, "import_bytes": 4 * 2880, "state": "new"}
    lib.groups = [g]
    left = iter([10e9, 10e9, 1e9])                   # nach zwei Dateien wird es knapp
    monkeypatch.setattr(diskspace, "free", lambda p: next(left, 1e9))
    monkeypatch.setattr(eng, "scan_and_queue", lambda extra=None: {"new_by_project": {}, "duplicates": 0})
    lib._import([g])
    assert lib.state["imported"]["raw"] == 2 and "angehalten" in lib.state["imported"]["errors"][0]
    assert g["to_import"] == files[2:] and g["n_new"] == 2 and g["n_known"] == 2 and g["state"] == "partial"


def test_memory_error_waits_and_retries_then_gives_up(tmp_path, monkeypatch):
    """Zu wenig RAM beim Stacken: Auftrag wartet und startet erneut statt „Unerwarteter Fehler“; nach den Versuchen klare Meldung."""
    eng, pid = engine(tmp_path)
    monkeypatch.setattr(type(eng), "MEM_RETRY_S", 0.05)
    jid = eng.enqueue(pid)
    calls = []

    def process(pid, opts, progress):
        calls.append(pid)
        if len(calls) == 1:
            raise MemoryError("Unable to allocate 48.9 MiB")

    eng.pl.stack = process
    run_once(eng)
    assert calls == [pid, pid] and not eng.paused
    assert eng.db.one("SELECT state FROM jobs WHERE id=?", (jid,))["state"] == "done"

    jid = eng.enqueue(pid)
    eng.pl.stack = lambda *a: (_ for _ in ()).throw(MemoryError())
    eng.stop.clear()
    run_once(eng)
    job = eng.db.one("SELECT state, message FROM jobs WHERE id=?", (jid,))
    assert job["state"] == "error" and "Arbeitsspeicher" in job["message"]
