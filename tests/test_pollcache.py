"""Geteilte Poll-Ergebnisse (core/pollcache.py) und schnelle Objektliste unter vielen Projekten (Stau unter Last, 14.11.2026)."""
import json
import sqlite3
import threading
import time

from fastapi.testclient import TestClient

from astroflow.core import pollcache
from astroflow.core.config import load_settings
from astroflow.server import create_app


def test_shared_rechnet_einmal_fuer_alle_wartenden():
    calls = []
    gate = threading.Event()

    def slow():
        calls.append(1)
        gate.wait(5)
        return len(calls)

    sh = pollcache.Shared(slow, min_age=5)
    out = []
    ths = [threading.Thread(target=lambda: out.append(sh.get())) for _ in range(8)]
    for t in ths:
        t.start()
    time.sleep(0.2)
    gate.set()
    for t in ths:
        t.join(5)
    assert calls == [1] and out == [1] * 8


def test_shared_signatur_alter_und_schreibende_anfrage():
    sig = [0]
    n = [0]

    def fn():
        n[0] += 1
        return n[0]

    sh = pollcache.Shared(fn, min_age=0.0, signature=lambda: sig[0])
    assert sh.get() == 1 and sh.get() == 1            # gleiche Signatur: wiederverwenden
    sig[0] += 1
    assert sh.get() == 2                              # Signatur geändert, Mindestalter 0 → neu
    pollcache.touch()
    assert sh.get() == 3                              # schreibende Anfrage → sofort neu
    held = pollcache.Shared(fn, min_age=10.0, signature=lambda: sig[0])
    v = held.get()
    sig[0] += 1
    assert held.get() == v                            # ständig neue Signatur: innerhalb des Mindestalters wiederverwenden
    pollcache.touch()
    assert held.get() == v + 1


def test_shared_hintergrund_liefert_letzten_stand_sofort():
    sig = [0]
    gate = threading.Event()
    n = [0]

    def fn():
        n[0] += 1
        if n[0] > 1:
            gate.wait(5)                              # Neuberechnung dauert
        return n[0]

    sh = pollcache.Shared(fn, min_age=0.0, signature=lambda: sig[0], background=True)
    assert sh.get() == 1
    sig[0] += 1                                       # Pipeline schreibt: nur die Signatur ändert sich
    t = time.perf_counter()
    assert sh.get() == 1 and sh.get() == 1            # sofort der letzte Stand, nur ein Hintergrund-Rechner
    assert time.perf_counter() - t < 1.0
    gate.set()
    for _ in range(100):
        if sh.get() == 2:
            break
        time.sleep(0.02)
    assert sh.get() == 2 and n[0] == 2
    pollcache.touch()                                 # Nutzeraktion: synchron neu
    assert sh.get() == 3


def _many_projects(db, n_proj=300, frames_per=100):
    """Synthetischer Bestand: viele Objekte, ~30 000 Frames, je zwei Ergebnisse mit großen Statistik-Blöcken."""
    big = {"tutor": {"text": "x" * 4000, "items": list(range(400))}, "qc": [{"round": 0, "adapt": ["y" * 50] * 40}],
           "params": {f"p{i}": i * 0.5 for i in range(300)}, "quality": {f"q{i}": i for i in range(200)},
           "depth": {"curve": list(range(2000))}, "palette": "OSC", "profile": "galaxy"}
    now = time.time()
    with db.conn() as c:
        c.executemany("INSERT INTO projects(id, key, name, kind, ra, dec, options, created) VALUES (?,?,?,?,?,?,?,?)",
                      [(i, f"Seestar|M {i}", f"M {i}", "galaxy", 10.0 + i % 300, 20.0, "{}", now) for i in range(1, n_proj + 1)])
        c.executemany("INSERT INTO frames(id, project_id, path, kind, night, info, metrics, status, size) VALUES (?,?,?,?,?,?,?,?,?)",
                      [(f"f{p}_{k}", p, f"/x/{p}/{k}.fit", "light", f"2026-0{1 + k % 9}-01",
                        json.dumps({"exptime": 10, **({"solve": {"ra": 1.0, "dec": 2.0}} if k == frames_per - 1 else {})}),
                        "{}", "accepted" if k % 5 else "rejected", 1000)
                       for p in range(1, n_proj + 1) for k in range(frames_per)])
        c.executemany("INSERT INTO results(project_id, created, n_frames, exposure, files, stats, review) VALUES (?,?,?,?,?,?,?)",
                      [(p, now + v, frames_per, 1000.0, "{}", json.dumps(big), "[]") for p in range(1, n_proj + 1) for v in (0, 1)])


def test_objektliste_viele_projekte_schnell_und_geteilt(tmp_path, monkeypatch):
    monkeypatch.setenv("ASTROFLOW_AUTO", "0")
    s = load_settings(tmp_path / "data")
    app = create_app(s, only=("stacking",))
    c = TestClient(app)                                # ohne `with`: kein Worker, nur die API
    db = app.state.modules["stacking"].eng.db
    _many_projects(db)

    t = time.perf_counter()
    r = c.get("/api/stacking/projects")
    first = time.perf_counter() - t
    assert r.status_code == 200 and len(r.json()) == 300
    p1 = next(p for p in r.json() if p["id"] == 1)
    st = p1["result"]["stats"]
    assert st["palette"] == "OSC" and not {"tutor", "qc", "params", "quality", "depth"} & set(st)   # Liste ohne große Blöcke
    assert p1["total"] == 100 and p1["coords"]["source"] == "solve" and p1["n_results"] == 2
    assert first < 5.0, first                          # vorher ~1 s für 96 Objekte ohne Last; großzügig für langsame CI

    # unverändert: geteilt statt neu gerechnet – eine Änderung an der DB an der Schicht vorbei bleibt daher unsichtbar
    with sqlite3.connect(s.db_path) as raw:
        raw.execute("UPDATE projects SET name='Heimlich' WHERE id=1")
    t = time.perf_counter()
    r = c.get("/api/stacking/projects")
    assert time.perf_counter() - t < 1.0
    assert "Heimlich" not in {p["name"] for p in r.json()}

    # Auftragsstand kommt bei jeder Anfrage frisch dazu (ändert sich ständig, löst keine Neuberechnung aus)
    gen = db.gen
    jid = db.x("INSERT INTO jobs(project_id, state, progress, stage, created) VALUES (1,'running',0.4,'Analyse',?)", (time.time(),))
    db.x("UPDATE jobs SET progress=0.5 WHERE id=?", (jid,))
    assert db.gen == gen
    job = next(p for p in c.get("/api/stacking/projects").json() if p["id"] == 1)["job"]
    assert job["id"] == jid and job["progress"] == 0.5 and job["state"] == "running"

    # Aktion des Nutzers (schreibende Anfrage): sofort sichtbar
    assert c.patch("/api/stacking/projects/2", json={"name": "Umbenannt"}).status_code == 200
    names = {p["name"] for p in c.get("/api/stacking/projects").json()}
    assert "Umbenannt" in names and "Heimlich" in names

    # Objektseite behält die vollen Kennzahlen
    one = c.get("/api/stacking/projects/1").json()
    assert "tutor" in one["result"]["stats"] and "depth" not in one["result"]["stats"]


def test_status_und_aktivitaet_geteilt(tmp_path, monkeypatch):
    monkeypatch.setenv("ASTROFLOW_AUTO", "0")
    app = create_app(load_settings(tmp_path / "data"), only=("stacking",))
    mod = app.state.modules["stacking"]
    calls = []
    real = mod.status
    monkeypatch.setattr(mod, "status", lambda: calls.append(1) or real())
    c = TestClient(app)
    for _ in range(5):
        assert c.get("/api/status").json()["modules"]["stacking"]["projects"] == 0
    assert len(calls) == 1                             # innerhalb einer Sekunde ein Ergebnis für alle Tabs
    assert c.post("/api/stacking/scan").status_code == 200
    c.get("/api/status")
    assert len(calls) == 2                             # nach einer Aktion sofort neu
    assert isinstance(c.get("/api/activity?limit=5").json(), list)
