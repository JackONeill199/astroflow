"""Mehrfilter-Aufnahmen (Mono-Kamera): Filter erkennen und Kanal-Stacks zu Farbe kombinieren.

Jeder Filter (L, R, G, B, Ha, OIII, SII) wird getrennt gestackt – alle auf dieselbe Referenz
registriert – und anschließend automatisch kombiniert:
  R+G+B -> RGB, +L -> LRGB, +Ha -> Hα in Rot, Ha+OIII -> HOO, Ha+OIII+SII -> SHO, ein Filter -> Mono.
Farbkameras (Bayer/Farbwürfel) haben kein Band (None) und laufen wie bisher als ein Stack.
"""
from __future__ import annotations

import re

import numpy as np

BANDS = ("L", "R", "G", "B", "Ha", "OIII", "SII")
NARROW = {"Ha", "OIII", "SII"}
PALETTES = ("auto", "RGB", "LRGB", "SHO", "HOO", "mono")

_PATTERNS = [
    ("SII", r"^s\s*-?ii|^s2$|sulf|^sii"),
    ("OIII", r"^o\s*-?iii|^o3$|oxy|^oiii"),
    ("Ha", r"^h\s*-?a(lpha)?\b|^ha|h-?alpha|^hα"),
    ("L", r"^l(um(inance)?)?$|^clear$|^cls?$|^uv[/ -]?ir|^lum"),
    ("R", r"^r(ed)?$"),
    ("G", r"^g(reen)?$"),
    ("B", r"^b(lue)?$"),
]


def band_of(filter_name: str | None, color: bool) -> str | None:
    """Kanonisches Band eines Mono-Frames; None bei Farbkameras (dort bleibt alles ein Stack)."""
    if color:
        return None
    f = (filter_name or "").strip().lower()
    for band, pat in _PATTERNS:
        if re.search(pat, f):
            return band
    return "L"                                   # Mono ohne/unbekannter Filter = Luminanz


def is_color(info: dict) -> bool:
    shape = info.get("shape") or []
    return bool(info.get("bayerpat")) or (len(shape) == 3 and shape[0] == 3)


def auto_palette(bands: set[str]) -> str:
    if {"R", "G", "B"} <= bands:
        return "LRGB" if "L" in bands else "RGB"
    if {"Ha", "OIII", "SII"} <= bands:
        return "SHO"
    if {"Ha", "OIII"} <= bands:
        return "HOO"
    return "mono"


def _norm(a: np.ndarray, cov: np.ndarray) -> np.ndarray:
    """Hintergrund auf 0, helle Strukturen (99,5 %-Perzentil) auf 1 – macht Schmalband-Kanäle vergleichbar."""
    v = a[cov > 0.5] if (cov > 0.5).any() else a.ravel()
    v = v[::max(1, v.size // 400_000)]
    bg = float(np.median(v))
    hi = float(np.percentile(v, 99.5)) - bg
    return (a - bg) / max(hi, 1e-9)


def _noise(a: np.ndarray) -> float:
    d = np.diff(a[:: max(1, a.shape[0] // 512)], axis=1)
    return float(1.4826 * np.median(np.abs(d - np.median(d))) / np.sqrt(2)) + 1e-12


def combine(stacks: dict[str, tuple[np.ndarray, np.ndarray]], palette: str = "auto"):
    """stacks: Band -> (Mittelwert (1,H,W), Abdeckung (H,W)). Gibt (Bild (C,H,W), Abdeckung, Palette, schmalbandig)."""
    bands = set(stacks)
    if palette == "auto" or not _possible(palette, bands):
        palette = auto_palette(bands)
    cov = np.minimum.reduce([c for _, c in stacks.values()])
    ch = {b: _norm(m[0], cov) for b, (m, _) in stacks.items()}

    if palette in ("RGB", "LRGB"):
        img = np.stack([ch["R"], ch["G"], ch["B"]])
        if "Ha" in ch:                           # Hα verstärkt Rot (nur wo Hα heller ist)
            img[0] = np.maximum(img[0], ch["Ha"])
        if palette == "LRGB":
            # Luminanz ersetzen (rauschgewichtet mit der RGB-Luminanz), Farbdifferenzen behalten
            lum = img.mean(0)
            wl, wc = 1 / _noise(ch["L"]) ** 2, 1 / _noise(lum) ** 2
            new = (wl * ch["L"] + wc * lum) / (wl + wc)
            img = img + (new - lum)[None]
        return img.astype(np.float32), cov, palette, False
    if palette == "SHO":
        img = np.stack([ch["SII"], ch["Ha"], ch["OIII"]])
        return img.astype(np.float32), cov, palette, True
    if palette == "HOO":
        img = np.stack([ch["Ha"], ch["OIII"], ch["OIII"]])
        return img.astype(np.float32), cov, palette, True
    # mono: bester verfügbarer Kanal (L > Ha > Rest), bei mehreren breitbandigen gemittelt
    for b in ("L", "Ha", "OIII", "SII"):
        if b in ch:
            return ch[b][None].astype(np.float32), cov, "mono", False
    return np.mean([ch[b] for b in ch], 0)[None].astype(np.float32), cov, "mono", False


def _possible(palette: str, bands: set[str]) -> bool:
    need = {"RGB": {"R", "G", "B"}, "LRGB": {"L", "R", "G", "B"}, "SHO": {"SII", "Ha", "OIII"},
            "HOO": {"Ha", "OIII"}, "mono": set()}.get(palette)
    return need is not None and need <= bands
