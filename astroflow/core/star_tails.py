# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0 OR AGPL-3.0-or-later
"""Schwache, gleichgerichtete Sternschweife neben einem runden Kern (Ruck/Sprung der Montierung während der Belichtung).

Gemeinsam für Bilder (`stacking/quality.py`) und die SkyTrace-Engine (`engine/frame_quality.py`, lädt die Datei über den
Pfad – daher doppelt lizenziert und nur numpy/scipy). Verfahren: viele Sternausschnitte subpixelgenau zentrieren, auf den
Kernfluss normieren und per Median zu einem mittleren Sternprofil stapeln. Ein Ruck hinterlässt bei allen Sternen denselben
einseitigen Schweif; er bleibt im Median erhalten, Rauschen, Hotpixel und einzelne echte Spuren verschwinden. Gemessen wird
der Fluss in einem schmalen Streifen je Richtung **minus** dem Streifen in Gegenrichtung (Halos, Beugung, gleichmäßiges
Verziehen sind symmetrisch und heben sich auf), dazu wie viele Einzelsterne in dieselbe Richtung zeigen.

Messung Stellina-Serie (931 Bilder à 10 s): gute Aufnahmen Schweifanteil 0,5–1,5 %, Aufnahmen mit Schweif 4–34 %.
"""
from __future__ import annotations

import numpy as np
from scipy import ndimage as ndi

# Grenzen für `has_tail` (an der Serie oben bestimmt, Längen in Analysepixeln)
MIN_STARS = 12          # Sterne im mittleren Profil
MIN_FRACTION = 0.035    # einseitiger Schweiffluss / Kernfluss
MIN_SIGMA = 10.0        # Signifikanz des Schweifs im gestapelten Profil
MIN_SUPPORT = 0.5       # Anteil der Einzelsterne mit Schweif in dieselbe Richtung
MIN_LENGTH_FWHM = 3.0   # Schweif reicht über mindestens 3 FWHM (kurze Asymmetrien am Kern sind Optik/Seeing)
MIN_QUADRANTS = 3       # Belege in mindestens drei Bildvierteln (nicht ein Nachbarobjekt, keine lokale Optik)


def _residual(img: np.ndarray) -> np.ndarray:
    """Bild minus grober Hintergrund (Median auf 1/8 verkleinert)."""
    step = 8 if min(img.shape) >= 160 else 1
    small = ndi.median_filter(img[::step, ::step], 7)
    bg = ndi.zoom(small, (img.shape[0] / small.shape[0], img.shape[1] / small.shape[1]), order=1)
    bg = np.pad(bg, ((0, max(0, img.shape[0] - bg.shape[0])), (0, max(0, img.shape[1] - bg.shape[1]))), mode="edge")
    return img - bg[: img.shape[0], : img.shape[1]]


def _sigma(a: np.ndarray) -> float:
    return float(1.4826 * np.median(np.abs(a - np.median(a)))) + 1e-12


def tail_profile(img: np.ndarray, max_stars: int = 40, radius: int = 32) -> dict:
    """Kennzahlen des mittleren Sternprofils einer Aufnahme (Luminanz, Analysepixel). Schlüssel `tail_profile_*`:
    stars, fraction (Schweif-/Kernfluss), sigma, length (Pixel), fwhm, angle (Grad, 0 = +x, 90 = +y), support (0–1),
    quadrants. Bei zu wenigen Sternen nur `tail_profile_stars`."""
    img = np.asarray(img, dtype=np.float32)
    h, w = img.shape
    radius = int(min(radius, (min(h, w) - 6) // 4))
    if radius < 12:
        return {"tail_profile_stars": 0}
    res = _residual(img)
    clean = ndi.median_filter(res, 3)                      # Hotpixel/Kosmik dürfen keine Sterne werden
    sm = ndi.gaussian_filter(clean, 1.0)
    noise = _sigma(sm[::3, ::3])
    ys, xs = np.nonzero((sm == ndi.maximum_filter(sm, 9)) & (sm > 15 * noise))
    m = radius + 3
    ok = (xs >= m) & (ys >= m) & (xs < w - m) & (ys < h - m)
    xs, ys = xs[ok], ys[ok]
    order = np.argsort(sm[ys, xs])[::-1]
    xs, ys = xs[order], ys[order]
    sat = 0.9 * float(img.max())
    g = np.arange(-radius, radius + 1, dtype=float)
    yy, xx = np.meshgrid(g, g, indexing="ij")
    dist = np.hypot(xx, yy)
    outer = dist > radius - 4
    k = np.arange(-2, 3)
    patches, points = [], []
    for x, y in zip(xs, ys):
        if img[y - 2 : y + 3, x - 2 : x + 3].max() >= sat:          # gesättigt: Kern abgeschnitten
            continue
        near = sm[y - radius : y + radius + 1, x - radius : x + radius + 1]
        if near[dist > 4].max() > 0.3 * sm[y, x]:                     # hellerer Nachbar im Ausschnitt
            continue
        c = np.clip(clean[y - 2 : y + 3, x - 2 : x + 3] - np.median(near), 0, None)
        if c.sum() <= 0:
            continue
        cy, cx = float((c.sum(1) * k).sum() / c.sum()), float((c.sum(0) * k).sum() / c.sum())
        p = ndi.map_coordinates(res, [yy + y + cy, xx + x + cx], order=1)
        patches.append(p - np.median(p[outer]))
        points.append((int(x), int(y)))
        if len(patches) >= max_stars:
            break
    out: dict = {"tail_profile_stars": len(patches)}
    if len(patches) < 8:
        return out
    # FWHM des mittleren Profils (Fläche über halber Höhe) – Kernradius und Mindestlänge skalieren damit
    raw = np.median([p / max(float(p[radius, radius]), 1e-9) for p in patches], axis=0)
    fwhm = float(2 * np.sqrt(max(np.count_nonzero((raw >= 0.5 * raw[radius, radius]) & (dist <= 10)), 1) / np.pi))
    r_core = max(2.5, 1.3 * fwhm)
    r_in = max(4.0, 1.9 * fwhm)
    if r_in > radius - 8:
        return out
    stack_list = [p / f for p in patches if (f := float(p[dist <= r_core].sum())) > 0]
    if len(stack_list) < 8:
        return out
    stack = np.median(stack_list, axis=0)
    stack -= np.median(stack[outer])
    noise_stack = _sigma(stack[outer])
    ang = np.radians(np.arange(0, 360, 5))
    dirs = np.column_stack([np.cos(ang), np.sin(ang)])
    nrm = np.column_stack([-dirs[:, 1], dirs[:, 0]])
    rr = np.arange(r_in, radius - 3, 1.0)
    pos = dirs[:, None, :] * rr[None, :, None] + radius
    n = len(ang)

    def strip(im, rows=slice(None)):
        """Fluss in einem 3 px breiten Streifen je Richtung und Abstand."""
        p, q = pos[rows], nrm[rows]
        return sum(ndi.map_coordinates(im, [(p + q[:, None, :] * o)[..., 1], (p + q[:, None, :] * o)[..., 0]], order=1)
                   for o in (-1, 0, 1))

    s = strip(stack)
    asym = s - np.roll(s, n // 2, axis=0)                  # einseitiger Anteil
    total = asym.sum(axis=1)
    best = int(np.argmax(total))
    prof = np.clip(asym[best], 0, None)
    cum = np.cumsum(prof)
    length = float(rr[min(len(rr) - 1, int(np.searchsorted(cum, 0.9 * cum[-1])))]) if cum[-1] > 0 else 0.0
    norm = np.sqrt(6 * len(rr))
    # Belege je Stern: zeigt der Einzelstern in dieselbe Richtung mehr Fluss als in die Gegenrichtung?
    opp = (best + n // 2) % n
    support = []
    for p, pt in zip(stack_list, points):
        both = strip(p, [best, opp])
        if (both[0] - both[1]).sum() / (_sigma(p[outer]) * norm) > 3:
            support.append(pt)
    out.update(
        tail_profile_fraction=round(float(total[best]), 4),
        tail_profile_sigma=round(float(total[best] / (noise_stack * norm)), 1),
        tail_profile_length=round(length, 1),
        tail_profile_fwhm=round(fwhm, 2),
        tail_profile_angle=int(round(float(np.degrees(ang[best])))),
        tail_profile_support=round(len(support) / len(stack_list), 2),
        tail_profile_quadrants=len({(int(x >= w / 2), int(y >= h / 2)) for x, y in support}),
    )
    return out


def has_tail(m: dict, min_fraction: float = MIN_FRACTION) -> bool:
    """True, wenn das mittlere Sternprofil einen gemeinsamen einseitigen Schweif zeigt (Ruck der Montierung)."""
    if m.get("tail_profile_stars", 0) < MIN_STARS or "tail_profile_fraction" not in m:
        return False
    return (m["tail_profile_fraction"] >= min_fraction and m["tail_profile_sigma"] >= MIN_SIGMA
            and m["tail_profile_support"] >= MIN_SUPPORT and m["tail_profile_quadrants"] >= MIN_QUADRANTS
            and m["tail_profile_length"] >= MIN_LENGTH_FWHM * m["tail_profile_fwhm"])
