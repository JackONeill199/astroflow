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
    assert np.ptp(out[0, 10, 10:]) < 0.8 * np.ptp(mean[0, 10, 10:])   # Verlauf kleiner (Attrappe zieht fest 0,3 der Skala ab; Skala nur über die abgedeckte Fläche)
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


def test_cpu_denoise_setting_persists():
    """`graxpert.cpu_denoise: true` aus settings.json kommt bei der GraXpert-Konfiguration an (Engine._apply_saved)."""
    from types import SimpleNamespace
    from astroflow.modules.stacking import api
    cfg = graxpert.GraxpertConfig()
    stub = SimpleNamespace(_targets=lambda: {"graxpert": cfg},
                           ctx=SimpleNamespace(store=SimpleNamespace(get=lambda sec: {"cpu_denoise": True, "unbekannt": 1})))
    api.Engine._apply_saved(stub)
    assert cfg.cpu_denoise is True and not hasattr(cfg, "unbekannt")


# Attrappe mit Aussetzer: liefert mit Grafikkarte (-gpu true) NaN – Anteil über GX_NAN_FRAC, auch ohne GPU bei GX_NAN_CPU=1
FAKE_NAN = FAKE.replace("fits.PrimaryHDU(d.astype", '''gpu = a[a.index("-gpu") + 1] == "true"
if gpu or os.environ.get("GX_NAN_CPU"):
    n = max(1, int(d.shape[-1] * d.shape[-2] * float(os.environ.get("GX_NAN_FRAC", "0.5"))))
    d = np.ascontiguousarray(d); d.reshape(-1)[:n] = np.nan
print("Used inference providers : ['DmlExecutionProvider']" if gpu else "Used inference providers : ['CPUExecutionProvider']")
fits.PrimaryHDU(d.astype''')


def _nan_fake(tmp_path) -> str:
    p = tmp_path / "graxpert_nan.py"
    p.write_text(FAKE_NAN, encoding="utf-8")
    return str(p)


def _flat():
    mean = np.full((3, 40, 50), 1000, np.float32) + np.random.default_rng(5).normal(0, 5, (3, 40, 50)).astype(np.float32)
    return mean, np.ones((40, 50), np.float32)


def test_nonfinite_retries_without_gpu(tmp_path, monkeypatch, capsys):
    """Ungültige Werte mit Grafikkarte: Messung ins Protokoll, einmal ohne GPU wiederholen – Ergebnis ist dann sauber."""
    monkeypatch.setattr(graxpert, "GRAXPERT", graxpert.GraxpertConfig(gpu=True))
    mean, cov = _flat()
    out, info = graxpert.apply(mean, cov, "bge", exe=_nan_fake(tmp_path))
    assert np.isfinite(out).all() and info["bge"] and "error" not in info
    assert info["retried_cpu"] is True and info["nonfinite_pct"] > 1
    assert "ungültige Pixel" in capsys.readouterr().out


def test_nonfinite_cpu_also_fails_gives_fixed_message(tmp_path, monkeypatch):
    monkeypatch.setattr(graxpert, "GRAXPERT", graxpert.GraxpertConfig(gpu=True))
    monkeypatch.setenv("GX_NAN_CPU", "1")
    mean, cov = _flat()
    out, info = graxpert.apply(mean, cov, "bge", exe=_nan_fake(tmp_path))
    assert out is mean and info["error"] == "GraXpert lieferte auch ohne Grafikkarte ungültige Werte"
    assert info["retried_cpu"] and info["nonfinite_pct"] > 1


def test_nonfinite_tiny_share_is_repaired_from_input(tmp_path, monkeypatch):
    """Nur ein winziger Anteil (< 0,1 %) bleibt ungültig → diese Pixel kommen aus der Eingabe, kein Verwerfen."""
    monkeypatch.setattr(graxpert, "GRAXPERT", graxpert.GraxpertConfig(gpu=False))
    monkeypatch.setenv("GX_NAN_FRAC", "0.0005")        # 1 Pixel von 2000
    monkeypatch.setenv("GX_NAN_CPU", "1")
    mean, cov = _flat()
    out, info = graxpert.apply(mean, cov, "bge", exe=_nan_fake(tmp_path))
    assert np.isfinite(out).all() and info["bge"] and "error" not in info
    assert info["nonfinite_repaired"] is True and 0 < info["nonfinite_pct"] < graxpert.TINY_PCT
    assert "retried_cpu" not in info


# Attrappe wie GraXpert: Normierung über MAD – ist sie in einem Kanal 0, kommt NaN heraus (IC 443 Mosaik, 72 % Lücke)
FAKE_MAD = FAKE.replace("d =fits.getdata(src).astype(np.float32)", '''d =fits.getdata(src).astype(np.float32)
for ch in (d if d.ndim == 3 else d[None]):
    if np.median(np.abs(ch - np.median(ch))) == 0:
        d = d * np.nan
        break
H0 = d.shape''')


def test_large_gaps_cropped_and_noise_filled_no_nan(tmp_path):
    """Mosaik mit > 50 % Lücke: Zuschnitt auf die abgedeckte Fläche, Lücken mit Himmel + Rauschen statt konstant → MAD > 0."""
    p = tmp_path / "gx_mad.py"
    p.write_text(FAKE_MAD, encoding="utf-8")
    H, W = 200, 300
    rng = np.random.default_rng(7)
    mean = (1000 + rng.normal(0, 5, (3, H, W))).astype(np.float32)
    cov = np.zeros((H, W), np.float32)
    cov[20:80, 30:110] = 1                                   # 8 % abgedeckt, Rest Lücke
    mean[:, cov == 0] = 0
    assert (cov <= 0.5).mean() > 0.5
    out, info = graxpert.apply(mean, cov, "bge", exe=str(p))
    assert "error" not in info and info["bge"] and np.isfinite(out).all()
    assert out.shape == mean.shape and np.allclose(out[:, cov == 0], 0)      # Lücken bleiben unverändert
    assert graxpert.GRAXPERT_VERSION >= 2
