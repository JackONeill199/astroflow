"""Einblicke in die laufende Bearbeitung (live_insight.py): nur echte Zwischenergebnisse, klein, nur mit offener Live-Ansicht."""
import json
import time

import numpy as np
from fastapi.testclient import TestClient

from astroflow.core.config import load_settings
from astroflow.modules.stacking import live, live_insight, photometry, processing
from astroflow.modules.stacking.db import DB
from astroflow.server import create_app

from test_photometry import HDR, H, W, _field


def _run(tmp_path, pid=1, watch=True, monkeypatch=None):
    run = live.LiveRun(tmp_path, pid, job=7)
    if watch:
        monkeypatch.setitem(live._watch, pid, time.monotonic())
    return run


def _read(run, name):
    return json.loads((run.path / name).read_text(encoding="utf-8"))


def test_without_viewer_nothing_is_computed(tmp_path, monkeypatch):
    monkeypatch.setattr(live, "_watch", {})
    run = live.LiveRun(tmp_path, 3)
    called = []
    assert live.offer(run, lambda r: called.append(1)) is None and not called
    assert live.offer(None, lambda r: called.append(1)) is None
    live.touch(3)
    live.offer(run, lambda r: called.append(1))
    assert called == [1]
    run.event("Fertig")                                         # nach dem Ende keine Einblicke mehr
    assert not run.wants()


def test_uv_flips_vertically_like_the_preview():
    assert live.uv([0], [0], 10, 20) == [[0.05, 0.975]]        # Zeile 0 (FITS unten) steht in der Anzeige unten
    rows, lo, hi = live.grid_of(np.arange(12, dtype=np.float32).reshape(3, 4), side=4)
    assert rows[-1] == [0, 23, 46, 70] and rows[0][-1] == 255      # oberste Anzeigezeile = letzte Bildzeile and (lo, hi) == (0.0, 11.0)


def test_defects_real_positions_and_columns(tmp_path, monkeypatch):
    run = _run(tmp_path, monkeypatch=monkeypatch)
    h, w = 120, 200
    idx = [5 * w + 7, 50 * w + 150] + [y * w + 33 for y in range(40)]   # zwei Hotpixel und eine halbe Spalte
    name = live.offer(run, live_insight.defects, np.array(idx), (h, w), None, "2026-11-14", "nacht")
    d = _read(run, name)
    assert d["kind"] == "defects" and d["n"] == 42 and d["frame"] == {"w": w, "h": h}
    assert [round(7.5 / w, 4), round(1 - 5.5 / h, 4)] in d["points"]
    assert d["columns"] == [round(33.5 / w, 4)] and "glow" not in d
    assert live.read_state(tmp_path, 1)["insights"][0]["name"] == name


def test_color_insight_from_real_gaia_fit(tmp_path, monkeypatch):
    run = _run(tmp_path, monkeypatch=monkeypatch)
    img, cat = _field(gain_r=1.6, gain_b=0.7)
    sample = {}
    gains, info = photometry.pcc_gains(img, np.ones((H, W), np.float32), HDR, cat, fwhm=3.8, sample=sample)
    assert len(sample["x"]) == info["sterne"] and set(sample["fit"]) == {"R", "B"}
    name = live.offer(run, live_insight.color, img, sample, gains)
    d = _read(run, name)
    assert d["n"] == info["sterne"] and len(d["stars"]) == min(300, info["sterne"])
    assert d["gains"] == [round(g, 3) for g in gains] and d["g2v"] == photometry.G2V_BP_RP
    # die gezeigte Gerade ist genau die der Kalibrierung: bei G2V ergibt sie den Kehrwert der Verstärkung
    assert abs(np.exp(-(d["fit"]["R"]["a"] * d["g2v"] + d["fit"]["R"]["b"])) - gains[0]) < 0.01
    assert all(0 <= u <= 1 and 0 <= v <= 1 for u, v, *_ in d["stars"])
    assert (run.path / d["pictures"]["base"]).is_file()
    assert len(json.dumps(d)) < 40_000                          # klein


def test_process_writes_background_and_stretch(tmp_path, monkeypatch):
    run = _run(tmp_path, monkeypatch=monkeypatch)
    rng = np.random.default_rng(1)
    yy, xx = np.mgrid[0:300, 0:400] / 400
    img = (0.1 + 0.05 * xx + 0.02 * yy + rng.normal(0, 0.002, (3, 300, 400))).astype(np.float32)
    for y, x in rng.integers(20, 280, (40, 2)):
        img[:, y - 2:y + 3, x - 2:x + 3] += 0.3
    params = {"gradient_degree": 2, "deconv": 0, "denoise": 0.2, "chroma_denoise": 0.5, "scnr": 0, "star_reduction": 0,
              "contrast": 0, "sharpen": 0, "saturation": 1, "target_bg": 0.12, "asinh": 30, "star_separation": "off",
              "cpu_denoise": 0}
    processing.process(img, np.ones((300, 400), np.float32), params, live_run=run)
    kinds = {i["kind"]: _read(run, i["name"]) for i in live.read_state(tmp_path, 1)["insights"]}
    bg, st = kinds["background"], kinds["stretch"]
    assert bg["method"] == "polynom" and bg["support"]["used"] <= bg["support"]["total"]
    # das Modell ist der tatsächliche Verlauf: links dunkler als rechts (Anzeige nicht gespiegelt in x)
    assert np.mean([r[0] for r in bg["grid"]]) < np.mean([r[-1] for r in bg["grid"]])
    assert len(st["curve"]) == 256 and np.all(np.diff(st["curve"]) >= -1e-6) and st["curve"][-1] > 0.9
    assert len(st["hist_in"]) == 256 and st["median_out"] > st["median_in"]
    for d in (bg, st):
        assert all((run.path / p).is_file() for p in d["pictures"].values())
    # ohne Live-Ansicht: gleiches Ergebnis, keine Einblicke
    run2 = live.LiveRun(tmp_path / "b", 2)
    a = processing.process(img, np.ones((300, 400), np.float32), params, live_run=run2)[0]
    b = processing.process(img, np.ones((300, 400), np.float32), params)[0]
    assert np.array_equal(a, b) and not live.read_state(tmp_path / "b", 2).get("insights")


def test_hotfix_points_and_cap(tmp_path, monkeypatch):
    run = _run(tmp_path, monkeypatch=monkeypatch)
    img = np.full((3, 100, 120), 10.0, np.float32) + np.random.default_rng(0).normal(0, 0.5, (3, 100, 120)).astype(np.float32)
    found = [(0, 30, 40, 12.0), (2, 60, 90, 9.0)]
    name = live.offer(run, live_insight.hotfix, found, img)
    d = _read(run, name)
    assert d["n"] == 2 and d["channels"] == [1, 0, 1] and d["points"][0][2] == 0
    pics = [_read(run, live.offer(run, live_insight.hotfix, found, img))["pictures"]["base"] for _ in range(live.INSIGHTS_KEEP + 2)]
    assert len(live.read_state(tmp_path, 1)["insights"]) == live.INSIGHTS_KEEP
    assert not (run.path / name).exists() and not (run.path / pics[0]).exists() and (run.path / pics[-1]).exists()


def test_api_serves_insights_only_with_pattern(tmp_path, monkeypatch):
    s = load_settings(tmp_path / "data")
    s.auto = False
    c = TestClient(create_app(s))
    monkeypatch.setitem(live._watch, 1, time.monotonic())
    run = live.LiveRun(s.work_dir, 1, job=7)
    name = live.offer(run, live_insight.defects, np.array([3, 4]), (10, 10), None)
    r = c.get(f"/api/stacking/projects/1/live/{name}")
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/json") and r.json()["n"] == 2
    for bad in ("state.json", "x-i1.json", "1-i1.json.tmp", "..%2Fstate.json"):
        assert c.get(f"/api/stacking/projects/1/live/{bad}").status_code == 404
    live._watch.pop(1, None)
    pid = DB(s.db_path).x("INSERT INTO projects(name) VALUES('Test')")
    assert c.get(f"/api/stacking/projects/{pid}").status_code == 200   # Objektseite abgefragt = Live-Ansicht offen
    assert live.watched(pid)
    c.close()


def test_browser_plays_real_insights_through_server(tmp_path, monkeypatch, browser):
    """Ende-zu-Ende ohne Attrappen: echter Server, echte Einblick-Dateien aus processing.process, echte live.js –
    die Einblick-JSONs laden ohne 404, die Zeichenfläche erscheint, neue Einblicke werden danach abgespielt."""
    import threading
    import uvicorn
    from astroflow.modules.stacking import demo, objects
    from astroflow.modules.stacking.pipeline import Pipeline
    from astroflow.modules.stacking.reviewer import ReviewerConfig
    from astroflow.modules.stacking.stacking import StackState
    from test_ui_layout import _free_port

    for k, v in {"ASTROFLOW_MODULES": "stacking", "ASTROFLOW_AUTO": "0", "ASTROFLOW_GAIA": "0", "ASTROFLOW_GRAXPERT": "0"}.items():
        monkeypatch.setenv(k, v)
    s = load_settings(tmp_path / "data")
    s.auto = False
    db = DB(s.db_path)
    demo.generate(s.input_dir / "demo", n_lights=10, n_darks=0, n_flats=0)
    pl = Pipeline(s, db, ReviewerConfig())
    pl.scan()
    pid = db.one("SELECT project_id FROM frames WHERE kind='light' GROUP BY project_id ORDER BY COUNT(*) DESC")["project_id"]
    pl.process(pid)
    port = _free_port()
    srv = uvicorn.Server(uvicorn.Config(create_app(s), host="127.0.0.1", port=port, log_level="warning"))
    th = threading.Thread(target=srv.run, daemon=True)
    th.start()
    for _ in range(200):
        if srv.started:
            break
        time.sleep(0.05)
    try:
        jid = db.x("INSERT INTO jobs(project_id,state,created,stage,message,progress) VALUES(?,'running',?,'Bearbeitung','Stretch',0.9)",
                   (pid, time.time()))
        run = live.LiveRun(s.work_dir, pid, job=jid)
        monkeypatch.setitem(live._watch, pid, time.monotonic() + 3600)
        mean, cov = StackState.load(s.work_dir / f"p{pid}" / "stack").result()
        processing.process(mean, cov, {**objects.profile_for("galaxy"), "star_separation": "off"}, live_run=run)
        run.event("Bearbeitung", "Stretch")
        kinds = [i["kind"] for i in run.data["insights"]]
        assert kinds == ["background", "stretch"]
        ctx = browser.new_context(viewport={"width": 1280, "height": 900})
        page = ctx.new_page()
        bad, errors = [], []
        page.on("response", lambda r: bad.append(r.url) if "/live/" in r.url and r.status >= 400 else None)
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"http://127.0.0.1:{port}/#/p/{pid}")
        page.wait_for_selector(".sl-ins.on", timeout=20000)          # jüngste Einblicke: Hintergrund, dann Stretch
        page.wait_for_function("document.querySelector('.sl-ins-cap b')?.textContent.startsWith('Hintergrund')", timeout=10000)
        page.wait_for_function("document.querySelector('.sl-ins-cap b')?.textContent.startsWith('Strecken')", timeout=15000)
        assert page.evaluate("getComputedStyle(document.querySelector('.sl-ins')).display") != "none"
        # ein neuer Einblick im laufenden Lauf wird beim nächsten Abruf geladen und abgespielt
        found = [(0, 30, 40, 9.0), (2, 50, 60, 8.0)]
        live.offer(run, live_insight.hotfix, found, mean)
        page.wait_for_function("document.querySelector('.sl-ins-cap b')?.textContent.startsWith('Hotpixel')", timeout=25000)
        assert not bad, bad
        assert not errors, errors
        ctx.close()
    finally:
        srv.should_exit = True
        th.join(10)
