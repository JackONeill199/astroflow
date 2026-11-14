"""Develop Engine: Neu entwickeln aus dem Master-Stack, Qualitätskontrolle, Vorher-Bild."""
import json
from pathlib import Path

import numpy as np
import pytest

from astroflow.modules.stacking import demo, develop
from astroflow.core.config import load_settings
from astroflow.modules.stacking.db import DB
from astroflow.modules.stacking.pipeline import Pipeline
from astroflow.modules.stacking.reviewer import ReviewerConfig


def test_develop_only_uses_master_stack(tmp_path):
    s = load_settings(tmp_path / "data")
    s.delete_uploads = True
    db = DB(s.db_path)
    pl = Pipeline(s, db, ReviewerConfig())
    with pytest.raises(ValueError, match="Master-Stack"):
        db.x("INSERT INTO projects(name, created) VALUES('leer', 0)")
        pl.develop(db.one("SELECT id FROM projects")["id"])
    demo.generate(s.uploads_dir / "n", n_lights=14, n_darks=0, n_flats=0)
    pl.scan()
    pid = db.one("SELECT id FROM projects WHERE name!='leer'")["id"]
    pl.process(pid)
    assert not list((s.uploads_dir / "n" / "lights").glob("*.fit"))      # Rohdaten weg

    stages = []
    rid = pl.process(pid, {"mode": "develop", "params": {"saturation": 1.1}}, lambda p, st, m="": stages.append(st))
    assert "Stacking" not in stages and "Analyse" not in stages
    r = db.one("SELECT * FROM results WHERE id=?", (rid,))
    st, files = json.loads(r["stats"]), json.loads(r["files"])
    q = st["quality"]
    assert 0 <= q["score"] <= 10 and "metrics" in q and isinstance(q["hints"], list)
    assert st["params"]["saturation"] == 1.1                     # Nutzerwert bleibt, Automatik fasst ihn nicht an
    assert Path(files["before"]).exists()
    from PIL import Image
    assert Image.open(files["before"]).size == Image.open(files["jpg"]).size   # gleicher Ausschnitt für den Vergleich


def test_assess_flags_measurable_problems():
    rng = np.random.default_rng(0)
    good = np.full((3, 300, 300), 0.04, np.float32) + rng.normal(0, 0.002, (3, 300, 300)).astype(np.float32)
    yy, xx = np.mgrid[0:300, 0:300]
    good += (0.45 * np.exp(-((xx - 150) ** 2 + (yy - 150) ** 2) / 2000) * np.array([1.0, 0.6, 0.5])[:, None, None]).astype(np.float32)
    assert develop.assess(good)["score"] == 10, develop.assess(good)["issues"]
    dark = good - 0.035
    assert "bg_dark" in {i["code"] for i in develop.assess(np.clip(dark, 0, 1))["issues"]}
    blotchy = good.copy()
    blotchy[0] += np.kron(rng.normal(0, 0.03, (30, 30)), np.ones((10, 10))).astype(np.float32)
    assert "chroma_noise" in {i["code"] for i in develop.assess(np.clip(blotchy, 0, 1))["issues"]}


def test_burnt_core_detected_and_white_point_raised():
    rng = np.random.default_rng(1)
    L = np.full((400, 600), 0.04, np.float32)
    for _ in range(30):                                          # Sternkerne dürfen weiß sein
        y, x = rng.integers(10, 390), rng.integers(10, 590)
        L[y - 2 : y + 3, x - 2 : x + 3] = 1.0
    assert develop.burnt_core(L) == 0.0
    yy, xx = np.mgrid[0:400, 0:600]
    L[((yy - 200) / 9) ** 2 + ((xx - 300) / 14) ** 2 < 1] = 1.0  # flächiger weißer Galaxienkern
    assert develop.burnt_core(L) > 0
    qc = develop.assess(np.repeat(L[None], 3, axis=0))
    assert "burnt_core" in {i["code"] for i in qc["issues"]}
    adj = develop.corrections(qc, {"asinh": 30, "denoise": 0.5, "chroma_denoise": 2, "saturation": 1.3, "target_bg": 0.07})
    assert adj["white_pct"] > 99.9 and adj["asinh"] > 30
    last = develop.corrections(qc, {"asinh": 30, "white_pct": develop.WHITE_STEPS[-1], "denoise": 0.5, "chroma_denoise": 2,
                                    "saturation": 1.3, "target_bg": 0.07})
    assert "white_pct" not in last                               # Ende der Stufen: nicht endlos nachregeln


def test_soft_gain_never_pushes_channels_to_white():
    from astroflow.modules.stacking.processing import soft_gain
    rng = np.random.default_rng(2)
    img = rng.uniform(0.0, 0.95, (3, 50, 50)).astype(np.float32)
    out = soft_gain(img, np.full((50, 50), 1.8, np.float32))
    assert out.max() < 0.999                                     # kein Kanal läuft ins Weiß (vorher: img·1,8 → 1,0)
    assert np.all(out >= img - 1e-6)                             # hellt weiterhin auf
    dim = soft_gain(img, np.full((50, 50), 0.7, np.float32))
    assert np.allclose(dim, img * 0.7, atol=1e-6)                # Abdunkeln unverändert
    low = np.full((3, 4, 4), 0.05, np.float32)
    assert np.allclose(soft_gain(low, np.full((4, 4), 1.5, np.float32)), 0.075, atol=0.002)  # dunkle Bereiche wie bisher


def test_object_burn_counts_flat_white_object_not_stars():
    rng = np.random.default_rng(3)
    L = np.full((400, 600), 0.04, np.float32) + rng.normal(0, 0.003, (400, 600)).astype(np.float32)
    for _ in range(30):                                          # Sternkerne dürfen weiß sein
        y, x = rng.integers(10, 390), rng.integers(10, 590)
        L[y - 2: y + 3, x - 2: x + 3] = 1.0
    rgb = np.repeat(L[None], 3, axis=0)
    assert develop.assess(rgb)["metrics"]["object_burn"] == 0.0
    yy, xx = np.mgrid[0:400, 0:600]
    r = np.sqrt(((yy - 200) / 40) ** 2 + ((xx - 300) / 55) ** 2)
    neb = np.where(r < 1, 0.55 + 0.38 * np.clip(1.6 - 1.6 * r, 0, 1), 0)   # kompakter Nebel, Kern flach bei 0,93
    rgb = np.clip(rgb + neb[None].astype(np.float32), 0, 0.93)
    qc = develop.assess(rgb)
    assert qc["metrics"]["object_burn"] > develop.OBJECT_BURN_MAX
    assert "object_burn" in {i["code"] for i in qc["issues"]}
    assert qc["v"] == develop.QC_VERSION >= 4


def test_object_burn_corrections_use_hdr_with_star_separation():
    qc = {"issues": [{"code": "object_burn", "text": ""}], "metrics": {"bg": 0.03}}
    base = {"asinh": 40, "denoise": 0.3, "chroma_denoise": 1.5, "saturation": 1.5, "target_bg": 0.08, "star_separation": "starnet"}
    adj = develop.corrections(qc, base)
    # GHS-Zweig: white_pct/asinh wirken dort nicht auf den Nebel (M1 lief drei Runden ohne Änderung)
    assert adj == {"hdr": develop.HDR_STEPS[0]}
    assert develop.corrections(qc, {**base, "hdr": develop.HDR_STEPS[0]}) == {"hdr": develop.HDR_STEPS[1]}
    assert develop.corrections(qc, {**base, "hdr": develop.HDR_STEPS[-1]}) == {}   # Ende der Stufen
    off = develop.corrections(qc, {**base, "star_separation": "off"})
    assert off["white_pct"] > 99.9 and "hdr" not in off          # ohne Trennung zuerst der Weißpunkt (wie bisher)
    second = develop.corrections(qc, {**base, "star_separation": "off", "white_pct": develop.WHITE_STEPS[1]})
    # letzte Weißpunkt-Stufe zusammen mit hdr: sonst reichen die QC-Runden bei Start 99,9 nie bis zum Lichterschutz
    assert second["white_pct"] == develop.WHITE_STEPS[-1] and second["hdr"] == develop.HDR_STEPS[0]
    done = develop.corrections(qc, {**base, "star_separation": "off", "white_pct": develop.WHITE_STEPS[-1]})
    assert done["hdr"] == develop.HDR_STEPS[0]                   # Weißpunkt ausgereizt: Lichterschutz


def test_compact_bright_nebula_keeps_core():
    """Kleiner heller Nebel (wie M1) mit Sternentrennung: früher Kern weiß (white_pct/asinh wirkungslos), jetzt Zeichnung."""
    from astroflow.modules.stacking import objects
    mean, cov = demo.compact_nebula()
    p = {**objects.profile_for("snr"), "star_separation": "intern"}
    rgb, stats, params, hist = develop.develop(mean, cov, p, False, "OSC")
    L = rgb.mean(0)
    h, w = L.shape
    core = L[h // 2 - 20: h // 2 + 20, w // 2 - 25: w // 2 + 25]
    assert any("hdr" in (r.get("adjust") or {}) for r in hist[1:]), hist
    assert (core >= 0.9).mean() < 0.05 and float(np.median(core)) < 0.85
    assert float(np.median(core)) > 0.4                          # Objekt bleibt hell
    m = stats["quality"]["metrics"]
    assert m["object_burn"] <= develop.OBJECT_BURN_MAX and m["burnt_core"] == 0
    assert develop.BG_RANGE[0] <= m["bg"] <= develop.BG_RANGE[1]  # Himmel wird nicht abgedunkelt
    assert stats["hdr"] == params["hdr"] > 0


def test_develop_all_queues_only_projects_with_stack(tmp_path):
    from fastapi.testclient import TestClient
    from astroflow.server import create_app
    s = load_settings(tmp_path / "data")
    s.auto = False
    db = DB(s.db_path)
    pl = Pipeline(s, db, ReviewerConfig())
    demo.generate(s.uploads_dir / "n", n_lights=10, n_darks=0, n_flats=0)
    pl.scan()
    pid = db.one("SELECT id FROM projects")["id"]
    pl.process(pid)
    db.x("INSERT INTO projects(name, created) VALUES('ohne Stack', 0)")
    db.x("INSERT INTO projects(name, created, options) VALUES('Foto', 0, ?)", (json.dumps({"input_type": "picture"}),))
    with TestClient(create_app(s)) as c:
        assert c.post("/api/stacking/develop-all", json={}).json() == {"queued": 1}
        job = db.one("SELECT * FROM jobs ORDER BY id DESC LIMIT 1")
        assert job["project_id"] == pid and json.loads(job["options"]) == {"mode": "develop"}


def test_develop_fixes_generic_kind_of_messier_object(tmp_path, monkeypatch):
    """Projekt mit „generic“ (M77: kleiner Katalog kennt es nicht, ASTAP ohne Art) bekommt beim Entwickeln das Galaxienprofil."""
    monkeypatch.setenv("ASTROFLOW_DEEPSKY", "0")
    s = load_settings(tmp_path / "data")
    db = DB(s.db_path)
    pl = Pipeline(s, db, ReviewerConfig())
    demo.generate(s.input_dir / "n", n_lights=8, n_darks=0, n_flats=0)
    pl.scan()
    pid = db.one("SELECT id FROM projects")["id"]
    pl.stack(pid)
    db.x("UPDATE projects SET name='M77', obj_id=NULL, kind='generic' WHERE id=?", (pid,))
    rid = pl.develop(pid)
    assert db.one("SELECT kind FROM projects WHERE id=?", (pid,))["kind"] == "galaxy"
    assert json.loads(db.one("SELECT stats FROM results WHERE id=?", (rid,))["stats"])["profile"] == "galaxy"
