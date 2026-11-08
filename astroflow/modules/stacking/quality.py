"""Sternerkennung, Frame-Qualitätsmetriken und automatische Frame-Auswahl."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy import ndimage as ndi

METRICS_VERSION = 2      # erhöhen, wenn sich die Analyse ändert -> bestehende Frames werden neu analysiert
# Feste Grenze für verzogene Sterne: ecc 0,8 ≈ Achsverhältnis 1,7:1 (an M57 gemessen: 1,5:1 ≈ 0,71, 2:1 ≈ 0,85).
# Höchstens dieser Anteil der Frames fällt deswegen heraus – sonst verlöre z. B. NGC 7000 zwei Drittel seiner Daten
ECC_MAX = 0.8
ECC_MAX_REJECT = 1 / 3


@dataclass
class Stars:
    x: np.ndarray
    y: np.ndarray
    flux: np.ndarray
    fwhm: np.ndarray
    ecc: np.ndarray
    pa: np.ndarray | None = None      # Richtung der Längsachse (Grad, 0 = +x, 90 = +y, achsial 0–180)

    def __len__(self):
        return len(self.x)


def analysis_image(raw: np.ndarray, bayer: bool) -> tuple[np.ndarray, int]:
    """Luminanzbild zur Analyse. Bei CFA 2x2-Binning (Skalierung 2)."""
    if raw.ndim == 3:
        return raw.mean(0), 1
    if bayer:
        h, w = raw.shape[0] // 2 * 2, raw.shape[1] // 2 * 2
        return raw[:h, :w].reshape(h // 2, 2, w // 2, 2).mean((1, 3)), 2
    return raw, 1


def noise_sigma(img: np.ndarray) -> float:
    """Robuste Rauschschätzung über Nachbardifferenzen (unempfindlich gegen Struktur)."""
    d = (img[:, 1:] - img[:, :-1])[::2, ::2]
    return float(1.4826 * np.median(np.abs(d - np.median(d))) / np.sqrt(2)) + 1e-9


def background_map(img: np.ndarray, box: int = 48) -> np.ndarray:
    h, w = img.shape
    bh, bw = max(1, h // box), max(1, w // box)
    ch, cw = h // bh, w // bw
    tiles = img[: bh * ch, : bw * cw].reshape(bh, ch, bw, cw).swapaxes(1, 2).reshape(bh, bw, -1)
    med = np.median(tiles, axis=2)
    bg = ndi.zoom(med, (h / bh, w / bw), order=1, mode="nearest")
    return bg[:h, :w] if bg.shape == (h, w) else np.pad(bg, ((0, h - bg.shape[0]), (0, w - bg.shape[1])), mode="edge")[:h, :w]


def detect_stars(img: np.ndarray, max_stars: int = 150, thresh: float = 5.0, min_fwhm: float = 0.8, max_fwhm: float = 20.0) -> Stars:
    res = img - background_map(img)
    sm = ndi.gaussian_filter(res, 1.2)
    sig = 1.4826 * np.median(np.abs(sm - np.median(sm))) + 1e-9
    mx = ndi.maximum_filter(sm, size=7)
    ys, xs = np.nonzero((sm == mx) & (sm > thresh * sig))
    h, w = img.shape
    r = 6
    ok = (xs >= r) & (xs < w - r) & (ys >= r) & (ys < h - r)
    xs, ys = xs[ok], ys[ok]
    order = np.argsort(sm[ys, xs])[::-1][: max_stars * 3]
    xs, ys = xs[order], ys[order]
    out = []
    yy, xx = np.mgrid[-r : r + 1, -r : r + 1]
    ring = (np.maximum(abs(yy), abs(xx)) == r)
    for x0, y0 in zip(xs, ys):
        cut = res[y0 - r : y0 + r + 1, x0 - r : x0 + r + 1]
        wgt = np.clip(cut - np.median(cut[ring]), 0, None)
        pk = wgt.max()
        if pk <= 0:
            continue
        # Einzelpixel (Hotpixel, Cosmics): direkte Nachbarn des hellsten Pixels fast auf Hintergrundniveau
        py, px = np.unravel_index(np.argmax(wgt[r - 1 : r + 2, r - 1 : r + 2]), (3, 3))
        py, px = py + r - 1, px + r - 1
        nb = (wgt[py - 1, px] + wgt[py + 1, px] + wgt[py, px - 1] + wgt[py, px + 1]) / 4
        if nb < 0.12 * wgt[py, px]:
            continue
        wgt = np.where(wgt > 0.15 * pk, wgt, 0)
        f = wgt.sum()
        cx, cy = (wgt * xx).sum() / f, (wgt * yy).sum() / f
        sxx = (wgt * (xx - cx) ** 2).sum() / f
        syy = (wgt * (yy - cy) ** 2).sum() / f
        sxy = (wgt * (xx - cx) * (yy - cy)).sum() / f
        tr, det = sxx + syy, sxx * syy - sxy**2
        disc = np.sqrt(max(tr * tr / 4 - det, 0))
        l1, l2 = tr / 2 + disc, max(tr / 2 - disc, 1e-6)
        fwhm = 2.3548 * np.sqrt(max(tr / 2, 0)) + 0.5
        if not (min_fwhm <= fwhm <= max_fwhm) or abs(cx) > 2.5 or abs(cy) > 2.5:
            continue
        out.append((x0 + cx, y0 + cy, f, fwhm, float(np.sqrt(1 - l2 / l1)), float(np.degrees(0.5 * np.arctan2(2 * sxy, sxx - syy)) % 180)))
        if len(out) >= max_stars:
            break
    if not out:
        z = np.zeros(0)
        return Stars(z, z, z, z, z, z)
    a = np.array(out)
    o = np.argsort(a[:, 2])[::-1]
    a = a[o]
    return Stars(a[:, 0], a[:, 1], a[:, 2], a[:, 3], a[:, 4], a[:, 5])


DENSE_STARS = 400       # Sterne je Aufnahme für die Verzerrungskorrektur (gespeichert werden nur die hellsten 100)


def dense_stars(raw: np.ndarray, bayer: bool, max_stars: int = DENSE_STARS) -> list:
    """Dichte Sternliste [x, y, Fluss] in Vollauflösungs-Pixeln – gleiche Erkennung und Koordinaten wie
    `analyze_frame`, nur mehr Sterne (Polynom-Anpassung in registration.fit_distortion braucht alle Bildecken)."""
    luma, s = analysis_image(raw, bayer)
    st = detect_stars(luma, max_stars=max_stars)
    return [[round(float((x + 0.5) * s - 0.5), 2), round(float((y + 0.5) * s - 0.5), 2), round(float(f), 1)]
            for x, y, f in zip(st.x, st.y, st.flux)]


def analyze_frame(raw: np.ndarray, bayer: bool) -> dict:
    """Metriken + Sternliste (in Vollauflösungs-Pixeln) für einen kalibrierten Frame."""
    luma, s = analysis_image(raw, bayer)
    # Satelliten-/Flugzeugspuren zuerst: ihre Punkte zählten sonst als Sterne (Demo: 88 statt ~60, FWHM hoch → ganze
    # Aufnahme als „unscharf“ verworfen). Spur durch den Hintergrund ersetzen; Lage für die Maske beim Stacken (trails.py).
    from . import trails as _trails
    lines = _trails.find(luma)
    if lines:
        luma = _trails.blank(luma, lines, background_map(luma), pad=2.0)
    stars = detect_stars(luma)
    n = len(stars)
    top = slice(0, min(n, 60))
    m = {
        "n_stars": n,
        "fwhm": float(np.median(stars.fwhm[top]) * s) if n else None,
        "ecc": float(np.median(stars.ecc[top])) if n else None,
        "bg": float(np.median(luma)),
        "noise": noise_sigma(luma) * s**0 ,
        "scale": s,
        # Signal-Rausch-Verhältnis der hellsten Sterne (relativ zwischen Frames vergleichbar)
        "snr": round(float(np.median(stars.flux[:20]) / (noise_sigma(luma) * max(float(np.median(stars.fwhm[:20])), 1.0))), 1) if n else 0.0,
        # Hintergrundgradient in Rauscheinheiten (Wolken, Mond, Streulicht)
        "grad": round(float(np.ptp(np.percentile(background_map(luma), [2, 98])) / noise_sigma(luma)), 1),
        "trails": len(lines),
        "trail_lines": _trails.to_full(lines, s),       # [x1, y1, x2, y2, Halbbreite, Kontrast] in Vollauflösungs-Pixeln
        "stars": [[round(float((x + 0.5) * s - 0.5), 2), round(float((y + 0.5) * s - 0.5), 2), round(float(f), 1)]
                  for x, y, f in zip(stars.x[:100], stars.y[:100], stars.flux[:100])],
    }
    # Himmel ohne Objekt + Hintergrundebene (Himmelshelligkeit, Richtung der Lichtverschmutzung – skyquality.py).
    # Zusätzliche Felder ohne Erhöhung von METRICS_VERSION: ältere Frames bekommen sie im Himmelsqualitäts-Auftrag nach.
    from .skyquality import bg_plane
    m.update(bg_plane(luma, s))
    m.update(shape_metrics(stars, luma.shape, s))      # Gerätediagnose (diagnose.py), ebenfalls ohne Versionserhöhung
    return m


def shape_metrics(stars: Stars, shape: tuple, s: int = 1, grid: int = 3) -> dict:
    """Für die Gerätediagnose: Vorzugsrichtung länglicher Sterne (`ecc_pa` = [Winkel°, Einheitlichkeit 0–1]; 1 = alle gleich
    ausgerichtet, typisch für Nachführung/Wind) und Schärfe je Bildbereich (`tiles` = grid×grid Median-FWHM in Vollauflösungs-
    Pixeln, Zeile 0 oben; None bei < 3 Sternen) – Grundlage für Verkippung je Aufnahme."""
    out: dict = {}
    if not len(stars):
        return out
    top = slice(0, min(len(stars), 60))
    if stars.pa is not None:
        e, a = stars.ecc[top], np.radians(stars.pa[top]) * 2
        sel = e > 0.3
        if sel.sum() >= 5:
            c, d = float(np.mean(np.cos(a[sel]))), float(np.mean(np.sin(a[sel])))
            out["ecc_pa"] = [round(float(np.degrees(np.arctan2(d, c)) / 2 % 180), 1), round(float(np.hypot(c, d)), 3)]
    h, w = shape
    iy = np.clip((stars.y / h * grid).astype(int), 0, grid - 1)
    ix = np.clip((stars.x / w * grid).astype(int), 0, grid - 1)
    tiles = []
    for r in range(grid):
        row = []
        for c in range(grid):
            v = stars.fwhm[(iy == r) & (ix == c)]
            row.append(round(float(np.median(v)) * s, 2) if len(v) >= 3 else None)
        tiles.append(row)
    out["tiles"] = tiles
    return out


def _mad_sigma(a: np.ndarray) -> float:
    return float(1.4826 * np.median(np.abs(a - np.median(a))))


def select_frames(metrics: dict[str, dict], keep_percent: float | None = None) -> dict[str, tuple[bool, str, float]]:
    """fid -> (ausgewählt, Grund, Score). Entfernt Ausreißer (Wolken, Schlieren, Unschärfe)."""
    ok = {k: m for k, m in metrics.items() if m.get("n_stars", 0) >= 8 and m.get("fwhm")}
    res: dict[str, tuple[bool, str, float]] = {}
    for k, m in metrics.items():
        if k not in ok:
            res[k] = (False, "zu wenige Sterne (Wolken/Fehlbelichtung)", 0.0)
    if not ok:
        return res
    ns = np.array([m["n_stars"] for m in ok.values()], float)
    fw = np.array([m["fwhm"] for m in ok.values()], float)
    ec = np.array([m["ecc"] for m in ok.values()], float)
    bg = np.array([m["bg"] for m in ok.values()], float)
    n_med, f_med, e_med, b_med = np.median(ns), np.median(fw), np.median(ec), np.median(bg)
    has_snr = all("snr" in m for m in ok.values())
    snr_med = float(np.median([m["snr"] for m in ok.values()])) if has_snr else 0.0
    g_med = max(float(np.median([m["grad"] for m in ok.values()])), 1.0) if has_snr else 1.0
    f_lim = f_med + max(3 * _mad_sigma(fw), 0.25 * f_med)
    e_lim = max(0.6, e_med + 3 * _mad_sigma(ec))
    # Rein relativ ließ die Grenze durch, was die meisten Frames haben: M57 (Wind/Nachführung) – 89 von 133 gestackten
    # Frames mit Sternen ≥ 1,5:1, Stack 1,5:1. Daher zusätzlich absolut, aber höchstens ECC_MAX_REJECT der Frames
    if e_lim > ECC_MAX:
        e_lim = max(ECC_MAX, float(np.quantile(ec, 1 - ECC_MAX_REJECT)))
    b_lim = b_med + max(4 * _mad_sigma(bg), 0.3 * abs(b_med)) if len(ok) >= 8 else np.inf
    scores = {}
    for k, m in ok.items():
        sc = (m["n_stars"] / n_med) ** 0.5 / ((m["fwhm"] / f_med) ** 1.5 * (1 + 2 * (m["ecc"] - e_med)))
        if snr_med:                                  # neuere Analysen: SNR und Gradient fließen in den Score ein
            sc *= (max(m["snr"], 0.1) / snr_med) ** 0.3 / (1 + 0.3 * max(0.0, m["grad"] / g_med - 1.5))
        scores[k] = float(sc)
        reason = ""
        if m["n_stars"] < 0.5 * n_med:
            reason = "wenige Sterne (Wolken/Dunst)"
        elif m["fwhm"] > f_lim:
            reason = "unscharf (Seeing/Fokus)"
        elif m["ecc"] > e_lim:
            reason = "Sterne verzogen (Nachführfehler/Wind)"
        elif m["bg"] > b_lim:
            reason = "Himmelshintergrund zu hell"
        res[k] = (reason == "", reason, sc)
    if keep_percent and keep_percent < 100:
        good = sorted((k for k in ok if res[k][0]), key=lambda k: scores[k], reverse=True)
        for k in good[int(np.ceil(len(good) * keep_percent / 100)) :]:
            res[k] = (False, f"nicht unter den besten {keep_percent:.0f}%", scores[k])
    return res
