"""GraXpert (github.com/Steffenhir/GraXpert, GPL-3) als optionales externes Werkzeug – wie StarNet:

  Hintergrund (KI): entfernt Lichtverschmutzungs-Verläufe, ohne großflächige Nebel abzutragen – das Polynom
                    kann das nicht (höherer Grad trägt Nebel ab).
  Entrauschen (KI): auf dem linearen Stack, vor dem Stretch.

Beides läuft genau einmal je Entwicklung auf dem linearen Master-Stack (`apply`), nicht in jeder QC-Runde;
das Ergebnis wird je Projekt zwischengespeichert. GraXpert lädt seine KI-Modelle beim ersten Aufruf selbst
herunter (in seinen eigenen Benutzerordner). Fehler brechen die Bearbeitung nie ab – dann Polynom/Wavelets.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

import numpy as np

PAGE = "https://github.com/Steffenhir/GraXpert/releases/latest"
EXE_NAMES = ("GraXpert.exe", "GraXpert-win64.exe", "graxpert.exe", "GraXpert", "GraXpert-linux", "graxpert")
TIMEOUT = 1800                       # Entrauschen ohne GPU dauert bei 6 MP einige Minuten
MODES = ("off", "bge", "denoise", "all")
DEFAULT_STRENGTH = 0.5               # Entrauschstärke, falls das Profil keinen Wert `ai_denoise` setzt
# Glättung des KI-Hintergrunds, falls das Profil keinen Wert `bge_smoothing` setzt. 0,1 folgt auch 100–500 px großen
# Strukturen – bei Staub-/Reflexionsnebeln trug das die Hälfte des echten Staubs ab (NGC 7023, Messung 10.11.2026).
DEFAULT_SMOOTHING = 0.1


@dataclass
class GraxpertConfig:
    path: str = ""                   # vom Nutzer gesetzt (Einstellungen)
    mode: str = "all"                # Standard für alle Objekte, sofern GraXpert gefunden wird
    gpu: bool = True                 # GraXpert nutzt sonst die CPU (DirectML/CUDA, falls vorhanden)
    # KI-Entrauschen auch ohne Grafikkarte? Auf der CPU dauert es ~5 min je 6 MP (gemessen 10.11.2026: 306 s gegen 21 s mit GPU)
    # – Nutzerentscheidung: zu lang. Standard: ohne GPU nur der KI-Hintergrund (~4 s), entrauscht wird dann auf der CPU
    # mit processing.denoise_multiscale (~6 s).
    cpu_denoise: bool = False


GRAXPERT = GraxpertConfig()
LAST_LOG = ""                        # Ausgabe des letzten GraXpert-Aufrufs (zeigt die genutzten Rechenwerke)


def uses_gpu(log: str) -> bool | None:
    """Hat GraXpert eine Grafikkarte benutzt? Aus der Zeile „Used inference providers : [...]“ (onnxruntime: Dml/CUDA/ROCm/
    CoreML = GPU). None, wenn die Zeile fehlt (ältere Fassungen, Attrappen)."""
    import re
    m = re.search(r"Used inference providers\s*:\s*\[([^\]]*)\]", log or "")
    if not m:
        return None
    return any(p.strip(" '\"") != "CPUExecutionProvider" for p in m.group(1).split(",") if p.strip())


def _search_dirs() -> list[Path]:
    env = os.environ
    dirs = [Path(env.get("ProgramFiles", "C:/Program Files")) / "GraXpert",
            Path(env.get("LOCALAPPDATA", "")) / "Programs" / "GraXpert",
            Path(env.get("LOCALAPPDATA", "")) / "GraXpert",
            Path("/opt/graxpert"), Path("/Applications/GraXpert.app/Contents/MacOS")]
    return [d for d in dirs if str(d) not in ("", ".") and d.is_dir()]


def graxpert_path() -> str | None:
    """GraXpert suchen: Einstellung, ASTROFLOW_GRAXPERT, PATH, übliche Installationsordner (MSI-Installer)."""
    if os.environ.get("ASTROFLOW_GRAXPERT", "").lower() in ("0", "off"):      # Tests: nie ein echtes GraXpert
        return None
    for p in (GRAXPERT.path, os.environ.get("ASTROFLOW_GRAXPERT")):
        if p and Path(p).is_file():
            return p
    return system_graxpert() or own_graxpert()


def system_graxpert() -> str | None:
    """Normal installiertes GraXpert (PATH, Programmordner) – hat Vorrang vor dem von AstroFlow ausgepackten."""
    for name in ("GraXpert", "graxpert", "GraXpert-win64", "GraXpert-linux"):
        p = shutil.which(name)
        if p:
            return p
    for d in _search_dirs():
        for name in EXE_NAMES:
            hits = sorted(d.rglob(name), key=lambda p: len(p.parts))
            if hits:
                return str(hits[0])
    return None


def own_graxpert() -> str | None:
    """Vom Installer/Assistenten ausgepacktes GraXpert (core/components.py)."""
    try:
        from ...core.components import graxpert_dir
        own = graxpert_dir() / "GraXpert.exe"
        return str(own) if own.is_file() else None
    except Exception:
        return None


def mode_for(option: str | None) -> str:
    """Projektoption (None = Einstellung) → wirksamer Modus; ohne GraXpert immer „off“."""
    m = option or GRAXPERT.mode
    return m if m in MODES else "off"


def _run(exe: str, img: np.ndarray, cmd: str, args: list[str], timeout: int = TIMEOUT) -> np.ndarray:
    """img (C,H,W) float 0..1 als 32-bit-FITS an GraXpert geben, Ergebnis (C,H,W) zurück."""
    from astropy.io import fits

    with tempfile.TemporaryDirectory(prefix="astroflow_gx_") as td:
        src = Path(td) / "in.fits"
        data = img[0] if img.shape[0] == 1 else img
        fits.PrimaryHDU(np.ascontiguousarray(data, np.float32)).writeto(src)
        prefix = [sys.executable, exe] if exe.endswith(".py") else [exe]          # .py: Attrappe in den Tests
        r = subprocess.run([*prefix, str(src), "-cli", "-cmd", cmd, "-output", "out", *args],
                           cwd=td, timeout=timeout, capture_output=True, text=True, errors="replace")
        global LAST_LOG
        LAST_LOG = (r.stdout or "") + (r.stderr or "")
        out = Path(td) / "out.fits"
        if not out.exists():
            tail = (r.stderr or r.stdout or "").strip().splitlines()[-3:]
            raise RuntimeError(f"GraXpert ({cmd}) ohne Ergebnis, Code {r.returncode}: {' | '.join(tail)}")
        y = np.asarray(fits.getdata(out), np.float32)
    if y.ndim == 2:
        y = y[None]
    elif y.shape[-1] in (1, 3) and y.shape[0] not in (1, 3):          # (H,W,C) → (C,H,W)
        y = np.moveaxis(y, -1, 0)
    if y.shape[1:] != img.shape[1:]:
        raise RuntimeError(f"GraXpert lieferte falsches Format {y.shape} statt {img.shape}")
    if y.shape[0] != img.shape[0]:
        y = np.repeat(y[:1], img.shape[0], 0) if img.shape[0] == 3 else y[:1]
    if not np.isfinite(y).all():
        raise RuntimeError("GraXpert lieferte ungültige Werte")
    return y


def _cache_key(mean: np.ndarray, mode: str, strength: float, gpu: bool, tag: str = "", smoothing: float = DEFAULT_SMOOTHING) -> str:
    h = hashlib.sha1(np.ascontiguousarray(mean[:, ::7, ::7]).tobytes())
    h.update(json.dumps([list(mean.shape), mode, round(strength, 3), gpu, GRAXPERT_VERSION]).encode())
    if round(smoothing, 3) != DEFAULT_SMOOTHING:     # alter Schlüssel bleibt für den Standard gültig (Cache bleibt)
        h.update(f"smoothing:{smoothing:.3f}".encode())
    if tag:                                          # Vorbearbeitung, die das ausgedünnte Raster verfehlen kann (stackfix)
        h.update(tag.encode())
    return h.hexdigest()[:16]


GRAXPERT_VERSION = 1                 # erhöhen, wenn sich die Vor-/Nachbereitung ändert (Cache ungültig)


def apply(mean: np.ndarray, cov: np.ndarray, mode: str, strength: float = 0.5, cache_dir: Path | None = None,
          exe: str | None = None, log=lambda m: None, preview=None, tag: str = "",
          smoothing: float = DEFAULT_SMOOTHING) -> tuple[np.ndarray, dict]:
    """Linearen Stack (C,H,W) mit GraXpert bearbeiten. Gibt (Stack, Info). Info leer = nichts gemacht.
    Nicht abgedeckte Ränder (cov ≤ 0,5) werden vorher mit dem Himmelswert gefüllt, damit die KI keine
    harten Kanten als Hintergrund deutet, und nachher wieder eingesetzt."""
    exe = exe or graxpert_path()
    if mode == "off" or not exe:
        return mean, {}
    gpu = bool(GRAXPERT.gpu)
    smoothing = float(np.clip(smoothing, 0.0, 1.0))
    key = _cache_key(mean, mode, strength, gpu, tag + ("|cpu_denoise" if GRAXPERT.cpu_denoise else ""), smoothing)
    if cache_dir is not None:
        f = cache_dir / f"{key}.npy"
        meta = cache_dir / f"{key}.json"
        if f.exists() and meta.exists():
            try:
                log('GraXpert-Ergebnis aus dem Cache')
                return np.load(f), {**json.loads(meta.read_text(encoding="utf-8")), "cache": True}
            except Exception:
                pass
    lo = float(np.percentile(mean, 0.01))
    hi = float(np.percentile(mean, 99.999))
    scale = max(hi - lo, 1e-12)
    x = np.clip((mean - lo) / scale, 0, 1).astype(np.float32)
    hole = cov <= 0.5
    if hole.any() and not hole.all():
        x[:, hole] = np.median(x[:, ~hole], axis=1)[:, None]
    info: dict = {"mode": mode}
    gpu_arg = ["-gpu", "true" if gpu else "false"]
    try:
        if mode in ("bge", "all"):
            log("Hintergrund entfernen (GraXpert-KI)")
            x = _run(exe, x, "background-extraction", ["-correction", "Subtraction", "-smoothing", f"{smoothing:g}", *gpu_arg])
            info["bge"] = True
            info["smoothing"] = round(smoothing, 2)
            if preview:
                from .live import optional, sample
                optional(preview, sample(x) * scale + lo)
        if mode == "all" and strength > 0 and not GRAXPERT.cpu_denoise and (not gpu or uses_gpu(LAST_LOG) is False):
            # ohne Grafikkarte: KI-Entrauschen überspringen (Minuten statt Sekunden), processing entrauscht auf der CPU
            log("GraXpert ohne Grafikkarte – Entrauschen auf der CPU")
            info["denoise_skipped"] = "ohne GPU"
        elif mode in ("denoise", "all") and strength > 0:
            log("Entrauschen (GraXpert-KI)")
            x = _run(exe, x, "denoising", ["-strength", f"{strength:.2f}", "-batch_size", "4", *gpu_arg])
            info["denoise"] = round(strength, 2)
            if preview:
                from .live import optional, sample
                optional(preview, sample(x) * scale + lo)
    except Exception as e:                       # GraXpert-Fehler brechen die Bearbeitung nie ab
        log(f"GraXpert fehlgeschlagen ({e}) – eigene Verfahren")
        if not info.get("bge"):
            return mean, {"error": str(e)[:300]}
        info["error"] = str(e)[:300]
    out = (x * scale + lo).astype(np.float32)
    if hole.any():
        out[:, hole] = mean[:, hole]
    if cache_dir is not None and "error" not in info:
        shutil.rmtree(cache_dir, ignore_errors=True)            # nur das jüngste Ergebnis behalten
        cache_dir.mkdir(parents=True, exist_ok=True)
        np.save(cache_dir / f"{key}.npy", out)
        (cache_dir / f"{key}.json").write_text(json.dumps(info), encoding="utf-8")
    return out, info


def selftest(exe: str) -> str:
    """Version abfragen; wirft bei Fehlern. (Ein echter Lauf lädt beim ersten Mal die KI-Modelle – das passiert
    erst bei der ersten Entwicklung.)"""
    r = subprocess.run([sys.executable, exe, "-v"] if exe.endswith(".py") else [exe, "-v"], capture_output=True, text=True, timeout=120, errors="replace")
    txt = (r.stdout + r.stderr).strip()
    for line in txt.splitlines():
        if "version" in line.lower():
            return line.split("GraXpert", 1)[-1].strip(" :")
    if r.returncode != 0:
        raise ValueError(f"GraXpert startet nicht (Code {r.returncode})")
    return ""
