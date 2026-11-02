"""Hauptordner-Import von einem anderen Laufwerk: Rohdaten werden am Ort eingebunden statt kopiert – nie gelöscht,
und eine abgesteckte Platte macht nichts kaputt."""
import time

import pytest
from fastapi.testclient import TestClient

from astroflow.core.config import load_settings
from astroflow.modules.stacking import demo, diskspace
from astroflow.server import create_app


def _wait(c, t=60):
    for _ in range(int(t / 0.1)):
        st = c.get("/api/stacking/library").json()
        if st["state"] in ("done", "error"):
            return st
        time.sleep(0.1)
    raise AssertionError(st)


def _import_all(c, root):
    c.post("/api/stacking/library/scan", json={"root": str(root)})
    st = _wait(c)
    ids = [g["id"] for g in st["groups"] if g["n_new"]]
    assert c.post("/api/stacking/library/import", json={"ids": ids}).status_code == 200
    return _wait(c)


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(diskspace, "same_drive", lambda a, b: False)     # Quelle liegt „auf einer anderen Platte“
    root = tmp_path / "Platte"
    demo.generate(root / "2026-08-12_21-00-00_observation_M31", n_lights=12, n_darks=0, n_flats=0)
    s = load_settings(tmp_path / "data")
    s.auto = False
    s.delete_uploads = True
    app = create_app(s)
    return s, app, app.state.modules["stacking"].eng, root


def test_import_from_other_drive_does_not_copy_and_never_deletes(setup):
    s, app, eng, root = setup
    src = sorted(root.rglob("*.fit"))
    with TestClient(app) as c:
        st = _import_all(c, root)
        assert st["imported"]["raw"] == 12 and st["imported"]["in_place"] == 12, st
        assert "am Ort" in st["message"]
        assert not list(s.input_dir.rglob("*.fit")), "nichts kopiert oder verlinkt"
        frames = eng.db.q("SELECT * FROM frames WHERE kind='light'")
        assert sorted(f["path"] for f in frames) == sorted(str(p) for p in src)
        assert all(f["size"] for f in frames)

        # Watcher-Scan liest nur input/: findet nichts Neues und legt nichts doppelt an
        assert eng.scan_and_queue()["new_frames"] == 0
        assert eng.db.one("SELECT COUNT(*) n FROM frames")["n"] == 12

        pid = eng.db.one("SELECT id FROM projects")["id"]
        eng.pl.process(pid)
        # wichtigstes Kriterium: Originale des Nutzers werden nach dem Stacken nie gelöscht
        assert all(p.exists() for p in src)
        assert eng.db.one("SELECT COUNT(*) n FROM frames WHERE deleted=1")["n"] == 0
        assert eng.pl.delete_stacked_uploads(pid) == (0, 0)
        assert all(p.exists() for p in src)

        # Hauptordner erneut durchsuchen: alles bekannt und fertig, ohne neuen Import
        c.post("/api/stacking/library/scan", json={"root": str(root)})
        st = _wait(c)
        assert [g["state"] for g in st["groups"]] == ["done"] and st["read"] == 0

        # Objekt löschen: Originale bleiben, Frames werden nur ignoriert
        assert c.delete(f"/api/stacking/projects/{pid}").status_code == 200
        assert all(p.exists() for p in src)


def test_missing_source_keeps_stack_and_reports_clearly(setup):
    s, app, eng, root = setup
    with TestClient(app) as c:
        _import_all(c, root)
        pid = eng.db.one("SELECT id FROM projects")["id"]
        eng.pl.process(pid)
        n1 = eng.db.one("SELECT n_frames FROM results ORDER BY id DESC LIMIT 1")["n_frames"]

        # zweite Nacht eingebunden, dann wird die Platte abgesteckt, bevor sie verarbeitet ist
        demo.generate(root / "2026-08-13_21-00-00_observation_M31", n_lights=6, n_darks=0, n_flats=0, seed=1)
        _import_all(c, root)
        assert eng.pending_projects() == {pid}
        root.rename(root.with_name("abgesteckt"))
        before = {f["id"]: (f["status"], f["deleted"]) for f in eng.db.q("SELECT * FROM frames")}

        assert eng.pending_projects() == set(), "kein endloser Auftrag für unerreichbare Aufnahmen"
        eng.scan_and_queue()
        eng.pl.process(pid)                                   # Nachstacken/Entwickeln aus dem Stack geht weiter
        assert eng.db.one("SELECT n_frames FROM results ORDER BY id DESC LIMIT 1")["n_frames"] == n1
        after = {f["id"]: (f["status"], f["deleted"]) for f in eng.db.q("SELECT * FROM frames")}
        assert after == before, "nicht als gelöscht, fehlerhaft oder neu markiert"
        assert any(f["offline"] for f in c.get(f"/api/stacking/projects/{pid}/frames").json())

        # Neu-Stacken: klare Meldung statt Absturz, Stack bleibt
        r = c.post(f"/api/stacking/projects/{pid}/process", json={"restack": True})
        assert r.status_code == 409 and "nicht erreichbar" in r.json()["detail"]
        eng.db.x("UPDATE projects SET restack=1 WHERE id=?", (pid,))
        with pytest.raises(ValueError, match="nicht erreichbar"):
            eng.pl.stack(pid)
        eng.db.x("UPDATE projects SET restack=0 WHERE id=?", (pid,))
        assert eng.db.one("SELECT COUNT(*) n FROM frames WHERE stacked=1")["n"] >= 3

        # Platte wieder da: die zweite Nacht kommt dran
        root.with_name("abgesteckt").rename(root)
        assert eng.pending_projects() == {pid}
        eng.pl.process(pid)
        assert eng.db.one("SELECT n_frames FROM results ORDER BY id DESC LIMIT 1")["n_frames"] > n1


def test_import_needs_no_space_for_raw_data(tmp_path, monkeypatch):
    s = load_settings(tmp_path / "data")
    s.auto = False
    lib = create_app(s).state.modules["stacking"].eng.library
    lib.groups = [{"id": "g0", "kind": "lights", "dir": tmp_path / "quelle", "to_import": [tmp_path / "x.fits"],
                   "import_bytes": 500e9}]
    monkeypatch.setattr(diskspace, "same_drive", lambda a, b: False)
    monkeypatch.setattr(lib, "_import", lambda chosen: None)
    monkeypatch.setattr(diskspace, "free", lambda p: 8e9)            # 500 GB Rohdaten, 8 GB frei: passt (nichts kopiert)
    assert lib.start_import(["g0"]) == 1
    lib.state["state"] = "done"
    monkeypatch.setattr(diskspace, "free", lambda p: 1e9)            # Datenlaufwerk fast voll: trotzdem ablehnen
    with pytest.raises(ValueError, match="Zu wenig Speicher"):
        lib.start_import(["g0"])
