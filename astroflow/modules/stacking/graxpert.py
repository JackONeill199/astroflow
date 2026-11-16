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
        from ...core.components import graxpert_exe
        own = graxpert_exe()
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
    bad = ~np.isfinite(y)
    if bad.any():
        raise NonFinite(y, bad)
    return y


CROP_MARGIN = 32                     # Rand (px) um die abgedeckte Fläche beim Zuschneiden
TINY_PCT = 0.1                       # bis zu diesem Anteil ungültiger Pixel (%) werden sie aus der Eingabe ersetzt


class NonFinite(RuntimeError):
    """GraXpert lieferte NaN/Inf. Trägt das (teilweise ungültige) Ergebnis und die Messung (Anteil, Kanäle)."""

    def __init__(self, y: np.ndarray, bad: np.ndarray):
        super().__init__("GraXpert lieferte ungültige Werte")
        self.y, self.bad = y, bad
        self.pct = 100.0 * float(bad.any(axis=0).mean())                     # Pixel mit mindestens einem ungültigen Kanal
        self.channels = [int(c) for c in range(bad.shape[0]) if bad[c].any()]


def _run_safe(exe: str, x: np.ndarray, cmd: str, args: list[str], gpu: bool, info: dict, log) -> np.ndarray:
    """`_run` mit Absicherung gegen ungültige Werte (sporadischer GPU-Aussetzer, IC 443 13.11.2026): Anteil und Kanäle
    messen und protokollieren, mit Grafikkarte einmal ohne wiederholen; bleibt nur ein winziger Anteil (< TINY_PCT) übrig,
    werden genau diese Pixel aus der Eingabe übernommen (kein Bildfehler, aber auch kein Verwerfen des ganzen Ergebnisses),
    sonst Fehler (das Bild wird mit eigenen Verfahren entwickelt)."""
    gpu_arg = ["-gpu", "true" if gpu else "false"]
    try:
        return _run(exe, x, cmd, [*args, *gpu_arg])
    except NonFinite as e:
        first = e
    log_tail = LAST_LOG.strip()[-600:]
    print(f"[graxpert] {cmd}: {first.pct:.4f} % ungültige Pixel (Kanäle {first.channels}, GPU={gpu}); Log-Ende: {log_tail}")
    info["nonfinite_pct"] = round(first.pct, 4)
    if gpu:
        log("GraXpert lieferte ungültige Werte – wiederhole ohne Grafikkarte")
        info["retried_cpu"] = True
        try:
            return _run(exe, x, cmd, [*args, "-gpu", "false"])
        except NonFinite as e:
            first = e
            print(f"[graxpert] {cmd}: auch ohne GPU {e.pct:.4f} % ungültige Pixel (Kanäle {e.channels}); Log-Ende: {LAST_LOG.strip()[-600:]}")
            info["nonfinite_pct"] = round(e.pct, 4)
    if first.pct < TINY_PCT:
        y = first.y.copy()
        np.copyto(y, x, where=first.bad)
        info["nonfinite_repaired"] = True
        return y
    raise first




def _cache_key(mean: np.ndarray, mode: str, strength: float, gpu: bool, tag: str = "", smoothing: float = DEFAULT_SMOOTHING) -> str:
    h = hashlib.sha1(np.ascontiguousarray(mean[:, ::7, ::7]).tobytes())
    h.update(json.dumps([list(mean.shape), mode, round(strength, 3), gpu, GRAXPERT_VERSION]).encode())
    if round(smoothing, 3) != DEFAULT_SMOOTHING:     # alter Schlüssel bleibt für den Standard gültig (Cache bleibt)
        h.update(f"smoothing:{smoothing:.3f}".encode())
    if tag:                                          # Vorbearbeitung, die das ausgedünnte Raster verfehlen kann (stackfix)
        h.update(tag.encode())
    return h.hexdigest()[:16]


GRAXPERT_VERSION = 3                 # erhöhen, wenn sich die Vor-/Nachbereitung ändert (Cache ungültig); 3 = Staubwächter


# Staubwächter: Glättung 0,1 folgt 100–500 px großen Strukturen – bei Emissionsnebeln mit Dunkelwolken und Staubfeldern
# rechnet GraXpert dann Nebel und Dunkelstrukturen als Hintergrund weg. Gemessen wird kachelweise, welchen Anteil der
# großflächigen Bildstruktur (Kachelmediane nach Abzug einer Fläche 2. Grades) das abgezogene Modell mitnimmt (Steigung
# Modell gegen Bild), insgesamt und in sternarmen Kacheln (Dunkelwolke/Staub verdecken Hintergrundsterne).
# Eichung 16.11.2026 an 14 echten Stacks (Glättung 0,1): NGC 7000 0,57 / sternarm 0,61, NGC 7023 0,40 / 0,47 (2 statt 8 Sterne
# je Kachel), IC 1396 0,43 / 0,22, IC 1805 0,21 / 0,32, Galaxien/Haufen (M31, M51, M101, M3, M17) ≤ 0,04. NGC 6888 0,44 kommt nur
# vom Randglühen, das höhere Glättung schlechter trifft (0,5: 0,56) → die höhere Glättung gilt nur, wenn sie messbar weniger mitnimmt.
DUST_TILES = 12                      # Kacheln über die kürzere Seite (mindestens 32 px)
DUST_ALL = 0.35                      # Anteil gesamt, ab dem der Wächter eingreift
DUST_DARK = 0.30                     # … bzw. in sternarmen Kacheln
DUST_STRONG = 0.45                   # ab hier gleich 1,0 statt 0,5
DUST_POOR = 0.55                     # sternarm = weniger als 55 % der mittleren Sternzahl je Kachel
DUST_MIN_STARS = 4                   # darunter (Galaxienfelder, kurze Belichtung) sagt die Sterndichte nichts
DUST_GAIN = 0.03                     # so viel weniger muss die höhere Glättung mitnehmen
# Mindeststruktur: Streuung der Kachelmediane (nach Fläche 2. Grades) in Einheiten des Kleinskalenrauschens – darunter ist der
# Anteil Zufall (Sternfeld ohne Nebel: 0,06; echte Stacks 0,23 (IC 1396) bis 24 (M31))
DUST_MIN_STRUCT = 0.15


def _dust_stars(lum: np.ndarray, T: int, ty: int, tx: int) -> tuple[np.ndarray, float]:
    """Sterne je Kachel: lokale Maxima > 5σ über dem lokalen Mittel (25 px) der leicht geglätteten Helligkeit; dazu σ."""
    from scipy import ndimage as ndi
    sm = ndi.gaussian_filter(lum, 1.2)
    res = sm - ndi.uniform_filter(sm, 25)
    sig = 1.4826 * float(np.median(np.abs(res - np.median(res)))) + 1e-12
    pk = (sm == ndi.maximum_filter(sm, 7)) & (res > 5 * sig)
    return pk[: ty * T, : tx * T].reshape(ty, T, tx, T).sum((1, 3)).astype(np.float64), sig


def _tile_median(img: np.ndarray, T: int, ty: int, tx: int) -> np.ndarray:
    return np.median(img[: ty * T, : tx * T].reshape(ty, T, tx, T).swapaxes(1, 2).reshape(ty, tx, -1), axis=2)


def _minus_plane2(R: np.ndarray, ok: np.ndarray) -> np.ndarray:
    ty, tx = R.shape
    yy, xx = np.mgrid[0:ty, 0:tx].astype(np.float64)
    yy = yy / max(ty - 1, 1) * 2 - 1
    xx = xx / max(tx - 1, 1) * 2 - 1
    A = np.stack([np.ones_like(xx), xx, yy, xx * xx, xx * yy, yy * yy], -1)
    c, *_ = np.linalg.lstsq(A[ok], R[ok], rcond=None)
    return R - A @ c


def dust_measure(before: np.ndarray, after: np.ndarray, hole: np.ndarray | None = None,
                 stars: tuple | None = None) -> dict | None:
    """Wie viel großflächige Struktur nimmt das Hintergrundmodell (before − after, beide (C,H,W) normiert) mit?
    Gibt {share, dark, structure, stars, poor, tiles, _stars} oder None (Bild zu klein). `stars` = `_stars` eines früheren Aufrufs."""
    lum = before.mean(0)
    H, W = lum.shape
    T = max(32, int(round(min(H, W) / DUST_TILES)))
    ty, tx = H // T, W // T
    if ty < 4 or tx < 4:
        return None
    ok = np.ones((ty, tx), bool)
    ok[[0, -1], :] = False                      # äußerer Kachelring: Randglühen, Sensorränder
    ok[:, [0, -1]] = False
    if hole is not None and hole.any():
        ok &= _tile_median(hole.astype(np.float32), T, ty, tx) < 0.5
    if ok.sum() < 6:
        return None
    stars = _dust_stars(lum, T, ty, tx) if stars is None else stars
    n, sig = stars
    rI = _minus_plane2(_tile_median(lum, T, ty, tx), ok)
    rM = _minus_plane2(_tile_median(lum - after.mean(0), T, ty, tx), ok)

    def share(m):
        den = float((rI[m] ** 2).sum())
        return float((rM[m] * rI[m]).sum() / den) if den > 0 else 0.0

    med = float(np.median(n[ok]))
    poor = ok & (n < DUST_POOR * med)
    dark = share(poor) if med >= DUST_MIN_STARS and poor.sum() >= 3 else None
    return {"share": round(share(ok), 3), "dark": None if dark is None else round(dark, 3),
            "structure": round(float(rI[ok].std()) / sig, 3),
            "stars": [round(float(np.median(n[poor])), 1) if poor.any() else None, round(med, 1)],
            "poor": int(poor.sum()), "tiles": int(ok.sum()), "_stars": stars}


def dust_target(m: dict | None, smoothing: float) -> float | None:
    """Höhere Glättung (0,5 bzw. 1,0), wenn das Modell zu viel Struktur mitnimmt – sonst None. Nie nach unten."""
    if not m or m.get("structure", 1.0) < DUST_MIN_STRUCT:
        return None
    hit = max(m["share"] if m["share"] >= DUST_ALL else 0.0,
              m["dark"] if m["dark"] is not None and m["dark"] >= DUST_DARK else 0.0)
    if hit <= 0 or smoothing >= 1.0 - 1e-6:
        return None
    t = 1.0 if hit >= DUST_STRONG else 0.5
    return t if t > smoothing + 1e-6 else 1.0


def dust_reason(m: dict) -> str:
    """Begründung für den Nutzer (feste deutsche Sätze mit Messwerten)."""
    de = lambda v: f"{v:g}".replace(".", ",")
    s = f"Das Hintergrundmodell nahm {100 * m['share']:.0f} % der großflächigen Struktur mit"
    if m.get("dark") is not None and m["dark"] >= DUST_DARK:
        p, md = m["stars"]
        s += (f", in sternarmen Zonen (Dunkelwolke/Staub, {de(p)} statt {de(md)} Sterne je Kachel) "
              f"{100 * m['dark']:.0f} %")
    return s


def dust_better(m0: dict, m1: dict) -> bool:
    """Gegenprobe: die höhere Glättung muss den auslösenden Anteil (gesamt und/oder sternarm) um DUST_GAIN senken und darf
    keinen der beiden um mehr als DUST_GAIN erhöhen (ein sinkender Gesamtanteil darf keine schlechteren Dunkelzonen verdecken)."""
    d0, d1 = m0.get("dark"), m1.get("dark")
    better = ((m0["share"] >= DUST_ALL and m1["share"] <= m0["share"] - DUST_GAIN)
              or (d0 is not None and d0 >= DUST_DARK and d1 is not None and d1 <= d0 - DUST_GAIN))
    worse = m1["share"] > m0["share"] + DUST_GAIN or (d0 is not None and d1 is not None and d1 > d0 + DUST_GAIN)
    return better and not worse


# Ebenheits-Gegenprobe (16.11.2026, unabhängige Prüfung im Hauptchat: höhere Glättung ließ bei NGC 7000 vor der Bearbeitung
# mehr Verlauf stehen, bei M3 30×): mit einem anderen Maß als dem Entscheidungsmaß wird der Himmel so verglichen, wie ihn die
# Bearbeitung danach sieht – ab Glättung 0,5 räumt processing.remove_edge_glow die Randbänder, sonst nichts. Gemessen auf 2×
# gebinnten Kacheln (25-%-Perzentil, robust gegen Sterne) in Einheiten des Pixelrauschens: Streuung der nebelfreien Kacheln
# (normale Sterndichte – keine Dunkelwolke – und unter den dunkelsten 30 % des Stacks nach Abzug einer Fläche 2. Grades – kein
# heller Nebel), Abstand des Randrings dazu und Streuung der Farbe (R−G, B−G) dort.
# Echte Läufe 16.11.2026 (Ebenheit 0,1 → gewählte Stufe): NGC 7000 2,16 → 1,26 (1,0 bleibt), IC 1396 0,17 → 0,21 und
# IC 1805 0,23 → 0,24 (0,5), NGC 7023 bei 1,0 0,20 → 0,47 verworfen → 0,5 (0,29). Ohne den Randglüh-Schritt wäre NGC 7000 bei 1,0
# unebener – ihn macht die Bearbeitung ab 0,5 aber immer. Eine große Dunkelwolke in der Bildmitte verschiebt den Bezug von
# remove_edge_glow (mittleres Drittel); auch das fängt die Gegenprobe ab. Die absolute Himmelsfarbe gleicht die Farbkalibrierung an.
SKY_FACTOR = 1.5                     # unebener als 1,5 × (0,1-Ergebnis) + SKY_SLACK → höhere Glättung verworfen
SKY_SLACK = 0.1


def _bin2(a: np.ndarray) -> np.ndarray:
    C, H, W = a.shape
    return a[:, : H // 2 * 2, : W // 2 * 2].reshape(C, H // 2, 2, W // 2, 2).mean((2, 4))


SKY_STARS = 0.8                      # nebelfrei nur mit mindestens 80 % der mittleren Sternzahl je Kachel


def sky_measure(x0: np.ndarray, y: np.ndarray, hole: np.ndarray | None, glow: bool, sel: dict | None = None) -> dict | None:
    """Himmel des Ergebnisses `y` (normiert, (C,H,W)) nach dem, was die Bearbeitung noch tut (`glow`: Randglühen ab 0,5).
    Gibt {flat, edge, color} in Einheiten des Pixelrauschens oder None (zu klein). `sel` merkt sich die Kachelauswahl
    (hängt nur am Stack x0) zwischen den Aufrufen."""
    from .processing import remove_edge_glow
    xb, yb = _bin2(x0), _bin2(y)
    if glow:
        yb = remove_edge_glow(yb)
    C, H, W = yb.shape
    T = max(16, min(H, W) // 16)
    ty, tx = H // T, W // T
    if ty < 4 or tx < 4:
        return None

    def tiles(a):
        t = a[:, : ty * T, : tx * T].reshape(a.shape[0], ty, T, tx, T).swapaxes(2, 3).reshape(a.shape[0], ty, tx, -1)
        return np.percentile(t, 25, axis=3)

    sel = {} if sel is None else sel
    if "sky" not in sel:
        ok = np.ones((ty, tx), bool)
        if hole is not None and hole.any():
            hb = hole[: H * 2 : 2, : W * 2 : 2][:H, :W].astype(np.float32)
            ok &= tiles(hb[None])[0] < 0.5
        ring = np.zeros_like(ok)
        ring[[0, -1], :] = True
        ring[:, [0, -1]] = True
        inner = ok & ~ring
        if inner.sum() < 6:
            return None
        from scipy import ndimage as ndi
        n, _ = _dust_stars(x0.mean(0), 2 * T, ty, tx)            # Sterne je Kachel in voller Auflösung,
        n = ndi.uniform_filter(n, 3, mode="nearest")                # über 3×3 gemittelt (Dunkelwolken sind zusammenhängend)
        cand = inner & (n >= SKY_STARS * float(np.median(n[inner])))
        if cand.sum() < 6:
            cand = inner
        r0 = _minus_plane2(tiles(xb).mean(0), ok)
        sel.update(sky=cand & (r0 <= np.percentile(r0[cand], 30)), ring=ring & ok)
    sky, ring = sel["sky"], sel["ring"]
    t = tiles(yb)
    L = t.mean(0)
    lum = yb.mean(0)
    sig = 1.4826 * float(np.median(np.abs(np.diff(lum, axis=1)))) / float(np.sqrt(2)) + 1e-12
    edge = float(np.abs(L[ring] - np.median(L[sky])).mean()) / sig if ring.any() else 0.0
    color = float(np.std(t[0][sky] - t[1][sky]) + np.std(t[2][sky] - t[1][sky])) / sig if C == 3 else 0.0
    return {"flat": round(float(L[sky].std()) / sig, 3), "edge": round(float(edge), 3), "color": round(float(color), 3)}


def sky_worse(s0: dict | None, s1: dict | None) -> list[str]:
    """Kennzahlen, in denen der Himmel mit höherer Glättung messbar schlechter ist als mit dem ersten Ergebnis."""
    if not s0 or not s1:
        return []
    return [k for k in ("flat", "edge", "color") if s1[k] > SKY_FACTOR * s0[k] + SKY_SLACK]


SKY_WORDS = {"flat": "unebener Himmel", "edge": "hellere/dunklere Ränder", "color": "Farbverlauf im Himmel"}


def _dust_guard(exe: str, x0: np.ndarray, x: np.ndarray, hole: np.ndarray, smoothing: float, gpu: bool, info: dict, log,
                edge_glow: bool = True) -> np.ndarray:
    """Staubwächter nach dem ersten Hintergrundlauf: nimmt das Modell zu viel großflächige Struktur mit, mit höherer Glättung
    neu rechnen (GraXpert-GPU ~4 s) und das nur übernehmen, wenn es messbar weniger mitnimmt (1,0 zu viel → 0,5 versuchen).
    Fehler beim Messen/Nachrechnen lassen das erste Ergebnis stehen; Abbruch und Speichermangel werden weitergeworfen.
    Verworfene Versuche hinterlassen keinen Laufzustand (LAST_LOG entscheidet über das Entrauschen auf der GPU)."""
    global LAST_LOG
    log_first = LAST_LOG
    run_keys = ("retried_cpu", "nonfinite_pct", "nonfinite_repaired")
    run_first = {k: info[k] for k in run_keys if k in info}

    def discard():
        global LAST_LOG
        LAST_LOG = log_first
        for k in run_keys:
            info.pop(k, None)
        info.update(run_first)

    try:
        m0 = dust_measure(x0, x, hole)
        target = dust_target(m0, smoothing)
        if target is None:
            return x
        stars = m0.pop("_stars")
        dust = {"from": round(smoothing, 2), "reason": dust_reason(m0), "before": m0}
        sky_sel: dict = {}
        info["dust"] = dust
        for t in dict.fromkeys((target, 0.5)):
            if t <= smoothing + 1e-6:
                continue
            discard()                           # jeder Versuch startet mit dem Laufzustand des ersten Laufs
            log(f"Staubwächter: Nebel/Dunkelwolken im Hintergrundmodell – Glättung {smoothing:.1f} → {t:.1f}".replace(".", ","))
            y = _run_safe(exe, x0, "background-extraction", ["-correction", "Subtraction", "-smoothing", f"{t:g}"], gpu, info, log)
            m1 = dust_measure(x0, y, hole, stars)
            m1.pop("_stars", None)
            tried = {"smoothing": t, "share": m1["share"], "dark": m1["dark"]}
            dust.setdefault("tried", []).append(tried)
            if not dust_better(m0, m1):
                continue
            # Ebenheits-Gegenprobe: Himmel nach der Bearbeitung nicht messbar unebener/farbstichiger als mit dem ersten Ergebnis
            if "sky_before" not in dust:
                dust["sky_before"] = sky_measure(x0, x, hole, edge_glow and smoothing >= 0.5, sky_sel)
            tried["sky"] = s1 = sky_measure(x0, y, hole, edge_glow, sky_sel)
            if bad := sky_worse(dust["sky_before"], s1):
                tried["sky_worse"] = bad
                log(f"Staubwächter: Glättung {t:.1f} ließ den Himmel schlechter ({', '.join(SKY_WORDS[k] for k in bad)})".replace(".", ","))
                continue
            dust["to"] = t
            info["smoothing"] = round(t, 2)
            return y
        dust["kept"] = True                     # höhere Glättung nahm nicht weniger mit (z. B. Randglühen statt Nebel) …
        bad = sorted({k for tr in dust.get("tried", []) for k in tr.get("sky_worse", [])})
        if bad:                                 # … oder ließ mehr Verlauf/Farbstich im Himmel stehen
            dust["why_kept"] = "höhere Glättung ließ " + " und ".join(SKY_WORDS[k] for k in bad) + " zurück"
        discard()
        log("Staubwächter: höhere Glättung brachte nichts – bleibt bei " + f"{smoothing:.1f}".replace(".", ","))
    except (InterruptedError, MemoryError):
        raise
    except Exception as e:
        print(f"[graxpert] Staubwächter übersprungen: {e}")
        discard()
        if "dust" in info and "to" not in info["dust"]:
            info["dust"]["kept"] = True
    return x


def apply(mean: np.ndarray, cov: np.ndarray, mode: str, strength: float = 0.5, cache_dir: Path | None = None,
          exe: str | None = None, log=lambda m: None, preview=None, tag: str = "",
          smoothing: float = DEFAULT_SMOOTHING, dust_guard: bool = True,
          edge_glow: bool = True) -> tuple[np.ndarray, dict]:
    """Linearen Stack (C,H,W) mit GraXpert bearbeiten. Gibt (Stack, Info). Info leer = nichts gemacht.
    Nicht abgedeckte Ränder (cov ≤ 0,5) werden vorher mit dem Himmelswert gefüllt, damit die KI keine
    harten Kanten als Hintergrund deutet, und nachher wieder eingesetzt. `dust_guard`: Staubwächter (hebt die Glättung,
    wenn das Modell Nebel/Dunkelwolken mitnimmt – Begründung in info["dust"]); `edge_glow`: entfernt die Bearbeitung danach das Randglühen (nicht bei Mosaiken) – für die
    Ebenheits-Gegenprobe)."""
    exe = exe or graxpert_path()
    if mode == "off" or not exe:
        return mean, {}
    gpu = bool(GRAXPERT.gpu)
    smoothing = float(np.clip(smoothing, 0.0, 1.0))
    key = _cache_key(mean, mode, strength, gpu, tag + ("|cpu_denoise" if GRAXPERT.cpu_denoise else "")
                     + ("" if dust_guard else "|ohne_staubwaechter") + ("" if edge_glow else "|ohne_randgluehen"), smoothing)
    if cache_dir is not None:
        f = cache_dir / f"{key}.npy"
        meta = cache_dir / f"{key}.json"
        if f.exists() and meta.exists():
            try:
                log('GraXpert-Ergebnis aus dem Cache')
                return np.load(f), {**json.loads(meta.read_text(encoding="utf-8")), "cache": True}
            except Exception:
                pass
    hole = cov <= 0.5
    # Auf das Begrenzungsrechteck der abgedeckten Fläche zuschneiden (+ Rand): spart Rechenzeit und verhindert, dass große
    # Lücken GraXperts Normierung (MAD) auf 0 setzen → NaN überall (IC 443 Mosaik, 72 % Lücke, 13.11.2026).
    box = (slice(0, mean.shape[1]), slice(0, mean.shape[2]))
    if hole.any() and not hole.all():
        ys, xs = np.where(~hole)
        m = CROP_MARGIN
        box = (slice(max(0, ys.min() - m), min(mean.shape[1], ys.max() + 1 + m)),
               slice(max(0, xs.min() - m), min(mean.shape[2], xs.max() + 1 + m)))
    sub, hsub = mean[:, box[0], box[1]], hole[box]
    cov_px = sub[:, ~hsub] if hsub.any() and not hsub.all() else sub.reshape(sub.shape[0], -1)
    lo = float(np.percentile(cov_px, 0.01))
    hi = float(np.percentile(cov_px, 99.999))
    scale = max(hi - lo, 1e-12)
    x = np.clip((sub - lo) / scale, 0, 1).astype(np.float32)
    if hsub.any() and not hsub.all():
        # Lücken mit Himmelswert + Rauschen in Höhe des gemessenen Himmelsrauschens füllen (fester Seed → stabiler Cache):
        # konstante Flächen machen die MAD null, die KI deutet harte Kanten als Hintergrund
        rng = np.random.default_rng(12345)
        for c in range(x.shape[0]):
            v = x[c][~hsub]
            med = float(np.median(v))
            sig = max(1.4826 * float(np.median(np.abs(v - med))), 1e-4)
            x[c][hsub] = np.clip(med + rng.normal(0.0, sig, int(hsub.sum())), 0, 1)
    info: dict = {"mode": mode}
    try:
        if mode in ("bge", "all"):
            log("Hintergrund entfernen (GraXpert-KI)")
            x0 = x
            x = _run_safe(exe, x0, "background-extraction", ["-correction", "Subtraction", "-smoothing", f"{smoothing:g}"], gpu, info, log)
            info["bge"] = True
            info["smoothing"] = round(smoothing, 2)
            if dust_guard:
                x = _dust_guard(exe, x0, x, hsub, smoothing, gpu, info, log, edge_glow)
            del x0
            if preview:
                from .live import optional, sample
                optional(preview, sample(x) * scale + lo)
        if mode == "all" and strength > 0 and not GRAXPERT.cpu_denoise and (not gpu or uses_gpu(LAST_LOG) is False):
            # ohne Grafikkarte: KI-Entrauschen überspringen (Minuten statt Sekunden), processing entrauscht auf der CPU
            log("GraXpert ohne Grafikkarte – Entrauschen auf der CPU")
            info["denoise_skipped"] = "ohne GPU"
        elif mode in ("denoise", "all") and strength > 0:
            log("Entrauschen (GraXpert-KI)")
            x = _run_safe(exe, x, "denoising", ["-strength", f"{strength:.2f}", "-batch_size", "4"], gpu, info, log)
            info["denoise"] = round(strength, 2)
            if preview:
                from .live import optional, sample
                optional(preview, sample(x) * scale + lo)
    except (InterruptedError, MemoryError):      # Abbruch/Speichermangel: Auftrag hält an (pipeline.resource_error)
        raise
    except Exception as e:                       # GraXpert-Fehler brechen die Bearbeitung nie ab
        log(f"GraXpert fehlgeschlagen ({e}) – eigene Verfahren")
        # ungültige Werte: feste Meldung (Regel 7), Einzelheiten stehen im Server-Protokoll
        msg = ("GraXpert lieferte auch ohne Grafikkarte ungültige Werte" if isinstance(e, NonFinite) and info.get("retried_cpu")
               else "GraXpert lieferte ungültige Werte" if isinstance(e, NonFinite) else str(e)[:300])
        if not isinstance(e, NonFinite):
            print(f"[graxpert] fehlgeschlagen: {e}")
        if not info.get("bge"):
            return mean, {"error": msg, **{k: v for k, v in info.items() if k in ("retried_cpu", "nonfinite_pct")}}
        info["error"] = msg
    out = mean.astype(np.float32, copy=True)
    out[:, box[0], box[1]] = (x * scale + lo).astype(np.float32)
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
