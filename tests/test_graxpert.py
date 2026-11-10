"""GraXpert-Anbindung mit einer Attrappe (Python-Skript mit der echten Kommandozeile, schreibt 32-bit-FITS)."""
import json
import sys
from pathlib import Path

import numpy as np

from astroflow.core.config import load_settings
from astroflow.modules.stacking import demo, graxpert
from astroflow.modules.stacking.db import DB
from astroflow.modules.stacking.pipeline import Pipeline
from astroflow.modules.stacking.reviewer import ReviewerConfig

FAKE = r'''
import sys, numpy as np
from pathlib import Path
from astropy.io import fits
a = sys.argv[1:]
if a == ["-v"]:
    print("GraXpert version: 3.0.2 release: Fake"); sys.exit(0)
src = Path(a[0])
cmd = a[a.index("-cmd") + 1]; out = a[a.index("-output") + 1]
with open(src.with_name("calls.txt"), "a") as f: f.write(cmd + "\n")
import os
if "-smoothing" in a and os.environ.get("GX_FAKE_LOG"):
    with open(os.environ["GX_FAKE_LOG"], "a") as f: f.write(a[a.index("-smoothing") + 1] + "\n")
d =fits.getdata(src).astype(np.float32)
if cmd == "background-extraction":
    hw = d.shape[-2:]
    yy, xx = np.mgrid[0:hw[0], 0:hw[1]]
    d = d - 0.3 * xx / hw[1] + 0.1                  # Attrappe: zieht den bekannten Verlauf ab
    d = np.moveaxis(d, 0, -1) if d.ndim == 3 else d   # GraXpert schreibt (H,W,C) – muss zurückgedreht werden
else:
    d = d * 0 + np.median(d)
fits.PrimaryHDU(d.astype(np.float32)).writeto(src.with_name(out + ".fits"))
'''


def _fake(tmp_path) -> str:
    p = tmp_path / "graxpert_fake.py"
    p.write_text(FAKE, encoding="utf-8")
    return str(p)


def test_apply_removes_gradient_keeps_holes_and_caches(tmp_path):
    exe = _fake(tmp_path)
    H, W = 60, 80
    xx = np.mgrid[0:H, 0:W][1]
    base = (1000 + 300 * xx / W).astype(np.float32)          # Verlauf: 300 ADU über die Breite (Skala lo..hi ≈ 1000)
    mean = np.stack([base, base, base])
    cov = np.ones((H, W), np.float32)
    cov[:, :5] = 0                                           # nicht abgedeckter Rand
    mean[:, :, :5] = 0
    out, info = graxpert.apply(mean, cov, "bge", cache_dir=tmp_path / "c", exe=exe)
    assert info == {"mode": "bge", "bge": True, "smoothing": 0.1}
    assert out.shape == mean.shape
    assert np.allclose(out[:, :, :5], 0)                     # Rand unverändert zurückgesetzt
    assert np.ptp(out[0, 10, 10:]) < 0.5 * np.ptp(mean[0, 10, 10:])   # Verlauf großteils weg
    out2, info2 = graxpert.apply(mean, cov, "bge", cache_dir=tmp_path / "c", exe=exe)
    assert info2.get("cache") and np.array_equal(out, out2)


def test_smoothing_reaches_graxpert_and_cache_key(tmp_path, monkeypatch):
    """Glättung je Profil (Staubnebel 1,0) wird an GraXpert übergeben und trennt den Cache; der Standard 0,1 behält den alten Schlüssel."""
    exe = _fake(tmp_path)
    monkeypatch.setenv("GX_FAKE_LOG", str(tmp_path / "smoothing.txt"))
    mean = np.full((3, 40, 50), 1000, np.float32) + np.random.default_rng(1).normal(0, 5, (3, 40, 50)).astype(np.float32)
    cov = np.ones((40, 50), np.float32)
    _, i1 = graxpert.apply(mean, cov, "bge", cache_dir=tmp_path / "c", exe=exe, smoothing=1.0)
    _, i2 = graxpert.apply(mean, cov, "bge", cache_dir=tmp_path / "c", exe=exe)
    assert i1["smoothing"] == 1.0 and i2["smoothing"] == 0.1 and not i2.get("cache")
    assert (tmp_path / "smoothing.txt").read_text().split() == ["1", "0.1"]
    assert graxpert._cache_key(mean, "bge", 0.5, True) == graxpert._cache_key(mean, "bge", 0.5, True, smoothing=0.1)
    assert graxpert._cache_key(mean, "bge", 0.5, True) != graxpert._cache_key(mean, "bge", 0.5, True, smoothing=1.0)


def test_apply_failure_falls_back(tmp_path):
    bad = tmp_path / "bad.py"
    bad.write_text("import sys; print('Modell-Download fehlgeschlagen'); sys.exit(1)", encoding="utf-8")
    mean = np.ones((3, 20, 20), np.float32)
    out, info = graxpert.apply(mean, np.ones((20, 20)), "all", exe=str(bad))
    assert out is mean and "Modell-Download" in info["error"]


def test_pipeline_uses_graxpert_once_and_skips_polynomial(tmp_path, monkeypatch):
    exe = _fake(tmp_path)
    monkeypatch.setenv("ASTROFLOW_GRAXPERT", exe)
    monkeypatch.setattr(graxpert, "GRAXPERT", graxpert.GraxpertConfig(mode="all", gpu=False))
    calls = []
    real_run = graxpert._run
    monkeypatch.setattr(graxpert, "_run", lambda e, img, cmd, args, **k: calls.append((cmd, args)) or real_run(e, img, cmd, args))
    s = load_settings(tmp_path / "data")
    db = DB(s.db_path)
    pl = Pipeline(s, db, ReviewerConfig())
    demo.generate(s.uploads_dir / "n", n_lights=10, n_darks=0, n_flats=0)
    pl.scan()
    pid = db.one("SELECT id FROM projects")["id"]
    db.x("UPDATE projects SET options=? WHERE id=?", (json.dumps({"graxpert": "bge"}), pid))
    rid = pl.process(pid)
    st = json.loads(db.one("SELECT stats FROM results WHERE id=?", (rid,))["stats"])
    assert st["graxpert"]["bge"] and "denoise" not in st["graxpert"]
    assert [c for c, _ in calls] == ["background-extraction"]        # einmal, nicht je QC-Runde
    assert ["-gpu", "false"] == calls[0][1][-2:]
    # lineare FITS = unbearbeiteter Stack (für Siril/PixInsight), GraXpert-Ergebnis zusätzlich
    from astropy.io import fits
    files = json.loads(db.one("SELECT files FROM results WHERE id=?", (rid,))["files"])
    raw, gx = fits.getdata(files["fits"]).astype(np.float32), fits.getdata(files["fits_graxpert"]).astype(np.float32)
    assert raw.shape == gx.shape and not np.allclose(raw, gx)
    rid = pl.process(pid, {"mode": "develop", "graxpert": "off"})
    st = json.loads(db.one("SELECT stats FROM results WHERE id=?", (rid,))["stats"])
    assert "graxpert" not in st and len(calls) == 1
    assert "fits_graxpert" not in json.loads(db.one("SELECT files FROM results WHERE id=?", (rid,))["files"])


def test_no_gpu_skips_ai_denoise(tmp_path, monkeypatch):
    """Ohne Grafikkarte dauert das KI-Entrauschen Minuten (Nutzerentscheidung: zu lang) – nur Hintergrund, Entrauschen dann
    auf der CPU (processing). Mit `cpu_denoise` bleibt es beim KI-Entrauschen."""
    exe = _fake(tmp_path)
    mean = np.full((3, 40, 50), 1000, np.float32) + np.random.default_rng(3).normal(0, 5, (3, 40, 50)).astype(np.float32)
    cov = np.ones((40, 50), np.float32)
    monkeypatch.setattr(graxpert, "GRAXPERT", graxpert.GraxpertConfig(gpu=False))
    _, info = graxpert.apply(mean, cov, "all", exe=exe)
    assert info["bge"] and "denoise" not in info and info["denoise_skipped"] == "ohne GPU"
    monkeypatch.setattr(graxpert, "GRAXPERT", graxpert.GraxpertConfig(gpu=False, cpu_denoise=True))
    _, info = graxpert.apply(mean, cov, "all", exe=exe)
    assert info["denoise"] == 0.5
    # GPU eingeschaltet, GraXpert meldet aber nur die CPU
    assert graxpert.uses_gpu("… Used inference providers : ['CPUExecutionProvider']") is False
    assert graxpert.uses_gpu("Used inference providers : ['DmlExecutionProvider', 'CPUExecutionProvider']") is True
    assert graxpert.uses_gpu("keine Angabe") is None
