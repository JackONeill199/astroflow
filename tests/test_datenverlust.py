"""Datenverlust-Fixes: defekte settings.json (F1), gleichnamige Uploads (F2), Sage-Verlauf unter Last (F3)."""
import io
import json
import threading

import numpy as np
import pytest
from fastapi.testclient import TestClient

from astroflow.core.config import load_settings
from astroflow.core.store import SettingsError, SettingsStore
from astroflow.server import create_app


# ---- F1 ---------------------------------------------------------------------------------------------
def test_settings_missing_file_is_empty(tmp_path):
    st = SettingsStore(tmp_path / "settings.json")
    assert st.get("x") == {}
    assert st.update("x", {"a": 1}) == {"a": 1}


def test_settings_invalid_json_is_never_overwritten(tmp_path):
    p = tmp_path / "settings.json"
    kaputt = '{"sites": {"list": [1]}, "gaia": {"on": true},'          # vergessenes Komma / abgeschnitten
    p.write_text(kaputt, encoding="utf-8")
    st = SettingsStore(p)
    assert st.get("sites") == {}                                       # Lesen stoppt die App nicht
    with pytest.raises(SettingsError):
        st.update("modules", {"stacking": True})
    assert p.read_text(encoding="utf-8") == kaputt                     # unverändert
    kopien = list(tmp_path.glob("settings.json.defekt-*"))
    assert kopien and kopien[0].read_text(encoding="utf-8") == kaputt
    assert st.problem
    # nach der Reparatur läuft alles weiter, nichts ging verloren
    p.write_text('{"sites": {"list": [1]}}', encoding="utf-8")
    st.update("modules", {"stacking": True})
    d = json.loads(p.read_text(encoding="utf-8"))
    assert d["sites"] == {"list": [1]} and d["modules"] == {"stacking": True} and st.problem is None
    assert (tmp_path / "settings.json.bak").exists()


def test_settings_not_a_dict_and_api_message(tmp_path):
    s = load_settings(tmp_path / "d")
    s.auto = False
    (s.data_dir).mkdir(parents=True, exist_ok=True)
    (s.data_dir / "settings.json").write_text("[1, 2]", encoding="utf-8")
    with TestClient(create_app(s)) as c:
        assert c.get("/api/app").json()["settings_problem"]
    assert (s.data_dir / "settings.json").read_text(encoding="utf-8") == "[1, 2]"


def test_settings_locked_read_is_retried(tmp_path, monkeypatch):
    p = tmp_path / "settings.json"
    p.write_text('{"a": {"b": 1}}', encoding="utf-8")
    st = SettingsStore(p)
    real, n = type(p).read_bytes, {"n": 0}

    def flaky(self):
        n["n"] += 1
        if n["n"] < 3:
            raise PermissionError("gesperrt")
        return real(self)
    monkeypatch.setattr(type(p), "read_bytes", flaky)
    assert st.get("a") == {"b": 1}


# ---- F2 ---------------------------------------------------------------------------------------------
def _fits(v):
    from astropy.io import fits
    buf = io.BytesIO()
    fits.PrimaryHDU(np.full((8, 8), v, np.uint16)).writeto(buf)
    return buf.getvalue()


def test_same_name_uploads_do_not_overwrite(tmp_path):
    s = load_settings(tmp_path / "d")
    s.auto = False
    a, b = _fits(1), _fits(2)
    with TestClient(create_app(s)) as c:
        r1 = c.post("/api/stacking/upload", files=[("files", ("img-0001r.fits", a))], data={"upload_id": "abcdef123456"})
        r2 = c.post("/api/stacking/upload", files=[("files", ("img-0001r.fits", b))], data={"upload_id": "zyxwvu654321"})
        r3 = c.post("/api/stacking/upload", files=[("files", ("img-0001r.fits", b))])           # ohne Kennung
        # Teilanfrage desselben Vorgangs: gleiche Kennung, gleicher Name, anderer Inhalt → nummeriert, nie überschrieben
        r4 = c.post("/api/stacking/upload", files=[("files", ("img-0001r.fits", a))], data={"upload_id": "zyxwvu654321"})
        bad = c.post("/api/stacking/upload", files=[("files", ("x.fits", a))], data={"upload_id": "../../evil"})
        assert all(r.json()["saved"] == 1 for r in (r1, r2, r3, r4, bad))
    files = sorted((s.input_dir / "uploads").rglob("*.fits"))
    assert len(files) == 5
    assert sorted(f.read_bytes() for f in files).count(a) == 3 and sorted(f.read_bytes() for f in files).count(b) == 2
    assert not list(s.data_dir.rglob("evil*"))


def test_upload_folder_name_for_object_detection_kept(tmp_path):
    s = load_settings(tmp_path / "d")
    s.auto = False
    with TestClient(create_app(s)) as c:
        c.post("/api/stacking/upload", files=[("files", ("2022-08-06_observation_M57/img-0001r.fits", _fits(3)))],
               data={"upload_id": "abcdef123456"})
    from astroflow.modules.stacking.scanner import object_from_path
    f = next((s.input_dir / "uploads").rglob("img-0001r.fits"))
    assert object_from_path(f) == "M57"


# ---- F3 ---------------------------------------------------------------------------------------------
def test_sage_history_concurrent_read_write(tmp_path):
    from astroflow.modules.sage.store import SageStore as Store
    st = Store(tmp_path / "sage")
    errors, stop = [], threading.Event()

    def reader():
        while not stop.is_set():
            try:
                st.entries()
            except Exception as e:
                errors.append(e)

    ts = [threading.Thread(target=reader) for _ in range(4)]
    for t in ts:
        t.start()
    try:
        for i in range(40):
            st.add({"title": f"Analyse {i}", "objects": []})
    finally:
        stop.set()
        for t in ts:
            t.join()
    assert not errors
    assert len(st.entries()) == 40


def test_sage_load_error_is_not_empty_history(tmp_path, monkeypatch):
    from astroflow.modules.sage import store as sstore
    st = sstore.SageStore(tmp_path / "sage")
    st.add({"title": "x", "objects": []})
    monkeypatch.setattr(sstore, "RETRIES", 2)
    monkeypatch.setattr(sstore, "RETRY_PAUSE", 0)
    monkeypatch.setattr(type(st.file), "read_text", lambda self, *a, **k: (_ for _ in ()).throw(PermissionError("gesperrt")))
    with pytest.raises(PermissionError):
        st.entries()


def test_cleanup_counts_only_existing_backups(tmp_path):
    from astroflow.modules.stacking import housekeeping
    from astroflow.modules.stacking.db import DB
    s = load_settings(tmp_path / "data")
    db = DB(s.db_path)
    assert housekeeping.cleanup(s, db, ["backups"])["removed"]["backups"] == 0       # kein Ordner „backups“ vorhanden
    (s.data_dir / "backups").mkdir()
    (s.data_dir / "backups" / "a.db").write_bytes(b"x")
    (s.data_dir / "astroflow.db.vor-test").write_bytes(b"x")
    assert housekeeping.cleanup(s, db, ["backups"])["removed"]["backups"] == 2
