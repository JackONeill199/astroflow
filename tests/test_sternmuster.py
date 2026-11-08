"""Sternmuster der Registrierung (Sternbild-Stil): Daten je Aufnahme aus dem echten Dreiecks-Matching, klein, im Speicher."""
import json
import time

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


def _pulse(ref, tgt):
    M, pairs, rms = registration.find_transform(ref, tgt)
    return registration.pulse(tgt, M, pairs, rms), M, pairs


def test_pulse_is_small_constellation_and_lands_on_reference():
    ref, tgt = _field()
    d, M, pairs = _pulse(ref, tgt)
    assert len(json.dumps(d, separators=(",", ":"))) < 400              # ~200 Bytes je Aufnahme
    assert 5 <= len(d["p"]) <= 7 and len(d["p"]) == registration.PATTERN_STARS
    # hellste zugeordnete Sterne (Sternlisten sind nach Helligkeit sortiert)
    want = sorted(set(int(t) for t in pairs[:, 0]))[:registration.PATTERN_STARS]
    assert d["p"] == [[int(round(x)), int(round(y))] for x, y in tgt[want]]
    # Spannbaum: n−1 Linien, alle Sterne verbunden, keine Schleifen
    n = len(d["p"])
    assert len(d["e"]) == n - 1 and all(0 <= a < n and 0 <= b < n and a != b for a, b in d["e"])
    reach, todo = {0}, [0]
    while todo:
        k = todo.pop()
        for a, b in d["e"]:
            for u, v in ((a, b), (b, a)):
                if u == k and v not in reach:
                    reach.add(v)
                    todo.append(v)
    assert reach == set(range(n))
    assert abs(d["r"] - 2.5) < 0.1 and d["q"] > 20
    # jeder gezeigte Stern landet mit der Transformation auf einem Referenzstern
    a, b, tx, c, e, ty = d["m"]
    p = np.array(d["p"], float)
    q = np.column_stack([a * p[:, 0] + b * p[:, 1] + tx, c * p[:, 0] + e * p[:, 1] + ty])
    dist = np.min(np.hypot(*(q[:, None] - ref[None]).transpose(2, 0, 1)), axis=1)
    assert dist.max() < 2.5


def test_spanning_tree_is_minimal():
    rng = np.random.default_rng(9)
    for _ in range(20):
        pts = rng.uniform(0, 100, (6, 2))
        tree = registration._spanning_tree(pts)
        length = sum(np.hypot(*(pts[a] - pts[b])) for a, b in tree)
        # Vergleich mit Kruskal: gleiche Gesamtlänge = minimal
        edges = sorted((np.hypot(*(pts[a] - pts[b])), a, b) for a in range(6) for b in range(a + 1, 6))
        parent = list(range(6))

        def find(x):
            while parent[x] != x:
                x = parent[x]
            return x
        best = 0.0
        for w, a, b in edges:
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[ra] = rb
                best += w
        assert abs(length - best) < 1e-9


def test_pulse_buffer_and_endpoint(tmp_path):
    s = load_settings(tmp_path)
    s.auto = False
    db = DB(s.db_path)
    pid = db.x("INSERT INTO projects(name, created) VALUES('Testnebel',0)")
    app = create_app(s)
    c = TestClient(app)
    assert c.get(f"/api/stacking/projects/{pid}/pattern").json()["items"] == []
    pl = app.state.modules["stacking"].eng.pl
    run = pl.begin_live(pid, job=5)
    ref, tgt = _field()
    d, _, _ = _pulse(ref, tgt)
    for k in range(live.PULSES + 6):
        run.add_pulse(d, n=k + 1, total=100, shape=(620, 800))
    r = c.get(f"/api/stacking/projects/{pid}/pattern")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    j = r.json()
    assert j["wh"] == [800, 620] and j["seq"] == live.PULSES + 6
    assert len(j["items"]) == live.PULSES and j["items"][-1]["n"] == live.PULSES + 6   # Ringpuffer
    j2 = c.get(f"/api/stacking/projects/{pid}/pattern?seit={j['seq'] - 2}").json()
    assert [x["i"] for x in j2["items"]] == [j["seq"] - 1, j["seq"]]                   # nur Neues
    pl.begin_live(pid, job=6)                                                            # neuer Lauf: alte Nummer gilt nicht
    run2 = pl.live_runs[pid]
    run2.add_pulse(d, n=1, total=3, shape=(620, 800))
    j3 = c.get(f"/api/stacking/projects/{pid}/pattern?seit={j['seq']}").json()
    assert j3["run"] != j["run"] and len(j3["items"]) == 1
    # im gepollten Projekt kommt nichts davon mit
    assert "items" not in json.dumps(c.get(f"/api/stacking/projects/{pid}").json().get("live", {}))
    c.close()


def test_pipeline_pulses_every_registered_frame(tmp_path):
    s = load_settings(tmp_path / "data")
    db = DB(s.db_path)
    pl = Pipeline(s, db, ReviewerConfig())
    demo.generate(s.input_dir / "demo", n_lights=8, n_darks=2, n_flats=2)
    pl.scan()
    pid = db.one("SELECT project_id FROM frames WHERE kind='light' GROUP BY project_id ORDER BY COUNT(*) DESC")["project_id"]
    run = pl.begin_live(pid, job=1)
    seen = []
    pl.process(pid, progress=lambda p, stage, msg="": seen.append((stage, msg)))
    # „Stacking“ steht schon vor der ersten Registrierung an (die Ansicht hängt an diesem Schritt)
    assert any(stage == "Stacking" and msg.startswith("0/") for stage, msg in seen)
    stacked = db.one("SELECT COUNT(*) n FROM frames WHERE project_id=? AND stacked=1", (pid,))["n"]
    items = run.pulses_since(0)["items"]
    assert len(items) == stacked - 1                         # jede registrierte Aufnahme außer der Referenz
    assert all(x["q"] >= 5 and x["t"] >= len(items) for x in items)


def test_pulse_cost_is_small():
    """Zusatzaufwand je Aufnahme klein gegenüber Laden + Ausrichten (Messung, großzügige Grenze)."""
    ref, tgt = _field()
    M, pairs, rms = registration.find_transform(ref, tgt)
    t0 = time.perf_counter()
    for _ in range(50):
        registration.pulse(tgt, M, pairs, rms)
    assert (time.perf_counter() - t0) / 50 < 0.02
