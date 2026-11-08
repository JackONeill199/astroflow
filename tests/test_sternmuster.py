"""Sternmuster der Registrierung: Daten aus dem echten Dreiecks-Matching, klein, gedrosselt, ohne Einfluss aufs Ergebnis."""
import json

import numpy as np
from fastapi.testclient import TestClient

from astroflow.core.config import load_settings
from astroflow.modules.stacking import demo, live, registration
from astroflow.modules.stacking.db import DB
from astroflow.modules.stacking.pipeline import Pipeline
from astroflow.modules.stacking.reviewer import ReviewerConfig
from astroflow.server import create_app


def _field(seed=4, n=120, ang=2.5, shift=(17.0, -9.0)):
    rng = np.random.default_rng(seed)
    ref = rng.uniform([10, 10], [790, 610], (n, 2))
    th = np.deg2rad(ang)
    Rm = np.array([[np.cos(th), -np.sin(th)], [np.sin(th), np.cos(th)]])
    tgt = (ref - shift) @ Rm + rng.normal(0, 0.2, (n, 2))       # Aufnahme: gedreht und verschoben
    keep = (tgt[:, 0] > 5) & (tgt[:, 0] < 795) & (tgt[:, 1] > 5) & (tgt[:, 1] < 615)
    return ref, tgt[keep]


def test_info_does_not_change_result():
    ref, tgt = _field()
    plain = registration.find_transform(ref, tgt)
    info = {}
    seen = registration.find_transform(ref, tgt, info=info)
    np.testing.assert_array_equal(plain[0], seen[0])
    np.testing.assert_array_equal(plain[1], seen[1])
    assert plain[2] == seen[2]
    assert len(info["seed_ref"]) == 3 and len(info["seed_tgt"]) == 3
    assert info["tris_ref"] > 0 and info["cands"] > 0
    # Startdreieck der Aufnahme liegt nach der Transformation auf dem der Referenz
    p = registration._apply(seen[0], tgt[info["seed_tgt"]])
    assert np.abs(p - ref[info["seed_ref"]]).max() < 1.5


def test_pattern_is_small_and_marks_matching_edges():
    ref, tgt = _field()
    info = {}
    M, pairs, rms = registration.find_transform(ref, tgt, info=info)
    d = registration.pattern(ref, tgt, M, pairs, info, (620, 800), rms)
    raw = json.dumps(d, separators=(",", ":"))
    assert len(raw) < 6000                                       # „wenige KB“
    assert len(d["ref"]) <= registration.PATTERN_STARS + 3 and len(d["tgt"]) <= registration.PATTERN_STARS + 3
    assert d["w"] == 800 and d["h"] == 620 and d["n_pairs"] == len(pairs)
    matched = [e for e in d["tgt_edges"] if e[2]]
    assert len(matched) > 0.5 * len(d["tgt_edges"])            # gleiches Feld: meiste Kanten deckungsgleich
    assert abs(d["rot"] - 2.5) < 0.1                             # Aufnahme -> Referenz: zurückdrehen
    assert len(d["seed_ref"]) == 3 and len(d["seed_tgt"]) == 3
    # deckungsgleiche Kanten verbinden zugeordnete Sterne
    pairs_d = dict(map(tuple, d["pairs"]))
    for a, b, _ in matched:
        assert a in pairs_d and b in pairs_d
    # Kennzahlen stimmen mit der Transformation überein
    a, b, tx, c, e, ty = d["m"]
    t = np.array(d["tgt"][d["pairs"][0][0]], float)
    r = np.array(d["ref"][d["pairs"][0][1]], float)
    assert np.hypot(a * t[0] + b * t[1] + tx - r[0], c * t[0] + e * t[1] + ty - r[1]) < 2.5


def test_live_pattern_throttled_and_endpoint(tmp_path, monkeypatch):
    s = load_settings(tmp_path)
    s.auto = False
    db = DB(s.db_path)
    pid = db.x("INSERT INTO projects(name, created) VALUES('Testnebel',0)")
    jid = db.x("INSERT INTO jobs(project_id,state,created) VALUES(?,'running',0)", (pid,))
    run = live.LiveRun(s.work_dir, pid, job=jid)
    assert run.want_pattern() and not run.want_pattern()      # zweiter Aufruf sofort: gedrosselt
    c = TestClient(create_app(s))
    assert c.get(f"/api/stacking/projects/{pid}/pattern").status_code == 404
    ref, tgt = _field()
    info = {}
    M, pairs, rms = registration.find_transform(ref, tgt, info=info)
    run.pattern(registration.pattern(ref, tgt, M, pairs, info, (620, 800), rms), n=3, total=10)
    state = c.get(f"/api/stacking/projects/{pid}").json()["live"]
    assert state["pattern"] and "tgt" not in state                # im Poll nur die Kennung
    r = c.get(f"/api/stacking/projects/{pid}/pattern")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    assert r.json()["key"] == state["pattern"] and r.json()["n"] == 3
    assert c.get(f"/api/stacking/projects/{pid + 1}/pattern").status_code == 404
    live.LiveRun(s.work_dir, pid, job=jid)                         # neuer Lauf: altes Muster weg
    assert c.get(f"/api/stacking/projects/{pid}/pattern").status_code == 404
    run2 = live.LiveRun(s.work_dir, pid, job=jid)
    run2.event("Fertig")
    assert not run2.want_pattern()                                 # nach dem Ende nichts mehr
    c.close()


def test_pipeline_writes_pattern_during_stacking(tmp_path, monkeypatch):
    s = load_settings(tmp_path / "data")
    db = DB(s.db_path)
    pl = Pipeline(s, db, ReviewerConfig())
    demo.generate(s.input_dir / "demo", n_lights=8, n_darks=2, n_flats=2)
    pl.scan()
    pid = db.one("SELECT project_id FROM frames WHERE kind='light' GROUP BY project_id ORDER BY COUNT(*) DESC")["project_id"]
    monkeypatch.setattr(live, "PATTERN_INTERVAL", 0.0)
    pl.begin_live(pid, job=1)
    pl.process(pid)
    d = live.read_pattern(s.work_dir, pid)
    assert d is not None and d["n_pairs"] >= 5 and d["total"] >= 2
    assert live.read_state(s.work_dir, pid)["pattern"] == d["key"]
    assert len(json.dumps(d)) < 8000
