"""Aufnahmen als Ebenen in der Live-Ansicht: Ausrichtung + Mini-Vorschau je Aufnahme aus dem echten Dreiecks-Matching,
klein, im Speicher; Zeitachse aus den Stacking-Stufen."""
import io
import json
import time

import numpy as np
from fastapi.testclient import TestClient
from PIL import Image

from astroflow.core.config import load_settings
from astroflow.modules.stacking import demo, live, registration
from astroflow.modules.stacking.db import DB
from astroflow.modules.stacking.pipeline import STAGES, Pipeline
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


def test_pulse_pairs_are_small_and_land_on_reference():
    ref, tgt = _field()
    d, M, pairs = _pulse(ref, tgt)
    assert set(d) == {"s", "m", "r", "q", "x"}                            # Punktpaare + Transformation, keine Linien
    assert len(json.dumps(d, separators=(",", ":"))) < 500              # einige hundert Bytes je Aufnahme
    np.testing.assert_allclose(np.array(d["m"]).reshape(2, 3), M[:2], atol=0.06)
    assert 8 <= len(d["s"]) <= 15 and len(d["s"]) == registration.PATTERN_STARS
    # hellste zugeordnete Sterne (Sternlisten sind nach Helligkeit sortiert), Lage in der Aufnahme gerundet
    want = sorted(set(int(t) for t in pairs[:, 0]))[:registration.PATTERN_STARS]
    assert [s[:2] for s in d["s"]] == [[int(round(x)), int(round(y))] for x, y in tgt[want]]
    # Ziel = Lage im Stack: mit der Transformation gerechnet und auf einem Referenzstern
    dst = np.array([s[2:] for s in d["s"]], float)
    np.testing.assert_allclose(dst, registration._apply(M, tgt[want]), atol=0.51)
    dist = np.min(np.hypot(*(dst[:, None] - ref[None]).transpose(2, 0, 1)), axis=1)
    assert dist.max() < 2.5
    # die Aufnahme ist gegenüber dem Stack verschoben: Start und Ziel unterscheiden sich
    src = np.array([s[:2] for s in d["s"]], float)
    assert np.median(np.hypot(*(dst - src).T)) > 5
    assert abs(d["r"] - 2.5) < 0.1 and d["q"] > 20


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
    assert all(x["q"] >= 5 and x["t"] >= len(items) and x["img"] for x in items)
    assert all("_thumb" not in x for x in items)              # Bilder nur über den eigenen Endpunkt
    pic = Image.open(io.BytesIO(run.pulse_thumb(items[0]["i"])))
    assert max(pic.size) <= live.THUMB and pic.format == "JPEG"
    # Zeitachse: Stufen dieses Laufs (1, 3 …) mit Bild
    st = live.stage_list(s.work_dir, pid)
    assert [x["n"] for x in st][:2] == [1, 3] and all((s.work_dir / f"p{pid}" / "stufen" / x["file"]).is_file() for x in st)


def test_thumb_small_and_input_untouched():
    rng = np.random.default_rng(1)
    img = rng.normal(1000, 20, (3, 900, 1400)).astype(np.float32)
    img[:, 400:420, 600:620] += 3000
    before = img.copy()
    data = live.thumb(img)
    pic = Image.open(io.BytesIO(data))
    assert max(pic.size) <= live.THUMB and len(data) < 20000
    np.testing.assert_array_equal(img, before)
    assert 50 in STAGES                                       # Zeitachse: 10, 30, 50, 100, 300 …


def test_thumb_and_stage_endpoints(tmp_path):
    s = load_settings(tmp_path)
    s.auto = False
    db = DB(s.db_path)
    pid = db.x("INSERT INTO projects(name, created) VALUES('Testnebel',0)")
    app = create_app(s)
    c = TestClient(app)
    run = app.state.modules["stacking"].eng.pl.begin_live(pid, job=5)
    ref, tgt = _field()
    d, _, _ = _pulse(ref, tgt)
    run.add_pulse(d, n=2, total=9, shape=(620, 800), thumb=live.thumb(np.ones((3, 620, 800), np.float32)))
    i = run.pulses_since(0)["items"][0]["i"]
    r = c.get(f"/api/stacking/projects/{pid}/pattern/{i}.jpg")
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
    assert c.get(f"/api/stacking/projects/{pid}/pattern/{i + 1}.jpg").status_code == 404
    assert c.get(f"/api/stacking/projects/{pid + 1}/pattern/{i}.jpg").status_code == 404
    assert c.get(f"/api/stacking/projects/{pid}/stages").json() == {"stages": []}
    d1 = s.work_dir / f"p{pid}" / "stufen"
    d2 = s.work_dir / f"p{pid}" / "stufen_Ha"
    for d_, n in ((d1, 10), (d1, 3), (d2, 1)):
        d_.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (8, 6)).save(d_ / f"n{n:05d}.jpg")
        (d_ / f"n{n:05d}.json").write_text(json.dumps({"n": n, "exposure": n * 10}), encoding="utf-8")
    st = c.get(f"/api/stacking/projects/{pid}/stages").json()["stages"]
    assert [(x["band"], x["n"]) for x in st] == [("", 3), ("", 10), ("Ha", 1)]
    assert c.get(f"/api/stacking/projects/{pid}/stages/n00010.jpg").status_code == 200
    assert c.get(f"/api/stacking/projects/{pid}/stages/n00001.jpg?band=Ha").status_code == 200
    for bad in ("n00010.json", "..%2Fstate.json", "n0001.jpg"):
        assert c.get(f"/api/stacking/projects/{pid}/stages/{bad}").status_code == 404
    assert c.get(f"/api/stacking/projects/{pid}/stages/n00001.jpg?band=..").status_code == 404
    c.close()


def test_pulse_cost_is_small():
    """Zusatzaufwand je Aufnahme klein gegenüber Laden + Ausrichten (Messung, großzügige Grenze)."""
    ref, tgt = _field()
    M, pairs, rms = registration.find_transform(ref, tgt)
    t0 = time.perf_counter()
    for _ in range(50):
        registration.pulse(tgt, M, pairs, rms)
    assert (time.perf_counter() - t0) / 50 < 0.02
