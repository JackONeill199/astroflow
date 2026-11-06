"""Hotpixel-Punkte in bestehenden linearen Stacks entfernen (ohne Neu-Stacken).

Stacks von vor der Fehlerkarte (0.31.2, `calibration.DefectLibrary`) enthalten Gruppen scharfer Einzelpunkte: ortsfeste
Hotpixel-Paare, die `cosmetic()` als Stern schützte und die wegen der Zeigegruppen der Stellina das Clipping überstanden
(NGC 7293, M16 …). Oft sind die Rohbilder schon gelöscht – daher hier direkt im Stack beim Entwickeln. Ein Punkt gilt nur dann
als Fehler, wenn er
- in einem Kanal deutlich über dem lokalen Median liegt (`K` σ), in den beiden anderen ringsum nichts zeigt (Sterne und
  Nebel haben Licht in mehreren Kanälen; Hα-Knoten sind nicht punktförmig),
- schärfer als die Stack-PSF ist (im Ring bei 3 px fast nichts – ein Stern mit FWHM ~5 px hat dort ~40 % der Spitze),
- und in einer Gruppe mit weiteren solchen Punkten liegt (eine je Zeigegruppe) bzw. sehr deutlich ist,
- nicht in heller Umgebung (Halo heller Sterne, Nebelkerne: dort sitzen Strukturen, ein Punkt fiele nicht auf).
Ersetzt wird nur der Fleck selbst (zusammenhängend über dem 7×7-Median, 3σ bzw. 2 % der Spitze, 1 px Saum, höchstens
`MAX_AREA` px) – gefüllt mit einer glatten Fläche, angepasst an einen Ring um den Fleck."""
from __future__ import annotations

import hashlib
import json

import numpy as np
from scipy import ndimage as ndi
from scipy.spatial import cKDTree

K = 10.0                 # Spitze über dem 7×7-Median in σ des Kanals
QUIET = 4.0              # andere Kanäle im 3×3-Kern (dort hätte ein Stern seine Spitze) höchstens so viele σ …
OTHER = 0.20             # … bzw. dieser Anteil der Spitze (Steilhang eines Nachbarsterns; rote Sterne ≥ ~0,3)
SHARP = 0.15             # Ring bei 3 px höchstens dieser Anteil der Spitze
GROUP_R, GROUP_N = 80, 3  # Gruppe: so viele Punkte im Umkreis (px)
STRONG = 40.0            # einzelne Punkte nur, wenn so deutlich
BRIGHT = 300.0           # Umgebung (Median auf ~20 px) höchstens so viele σ über dem Bildhintergrund
MAX_AREA = 150           # größere Flecken sind keine Punkte (Demo: zusammengewachsene Paare bis ~100 px)
CORE = 0.02              # Fleck: über diesem Anteil der Spitze (Sternhänge trennt die Kanalprüfung, nicht die Schwelle)
RING = [(-3, 0), (3, 0), (0, -3), (0, 3), (-2, -2), (-2, 2), (2, -2), (2, 2)]
VERSION = 1


def find(img: np.ndarray, cov: np.ndarray | None = None) -> tuple[list[tuple[int, int, int, float]], np.ndarray, np.ndarray]:
    """Fehlerpunkte (Kanal, y, x, Spitze in σ) sowie Median-Bild und σ je Kanal."""
    C, H, W = img.shape
    med = np.stack([ndi.median_filter(c, size=7) for c in img])
    hp = img - med
    inner = (ndi.minimum_filter(cov, 5) > 0.5) if cov is not None else np.ones((H, W), bool)
    sig = np.array([1.4826 * np.median(np.abs(h[inner][::7])) + 1e-9 if inner.any() else 1.0 for h in hp])
    hs = hp / sig[:, None, None]
    # helle Umgebung (Halo/Kern heller Sterne, helle Nebelkerne): dort Strukturen statt Punkte – nichts anfassen
    # (M45: Halo-Struktur im B-Kanal galt sonst als Punkt, die Füllung hinterließ eine Kerbe)
    # Umgebung großräumig (Median auf ~20 px über das 4×4-gebinnte Bild): ein Fleck hebt sie nicht an, ein Sternhalo schon
    level = np.array([np.median(m[inner][::7]) if inner.any() else 0.0 for m in med])
    hb, wb = H // 4, W // 4
    calm = np.ones((H, W), bool)
    for c in range(C):
        b = ndi.median_filter(np.median(img[c, : hb * 4, : wb * 4].reshape(hb, 4, wb, 4), axis=(1, 3)), size=5)
        bright = np.repeat(np.repeat((b - level[c]) / sig[c] >= BRIGHT, 4, 0), 4, 1)
        calm[: hb * 4, : wb * 4] &= ~ndi.binary_dilation(bright, iterations=4)
    found = []
    for c in range(C):
        h = hs[c]
        y, x = np.nonzero((h > K) & (h == ndi.maximum_filter(h, 3)) & inner & calm)
        ok = (y >= 4) & (y < H - 4) & (x >= 4) & (x < W - 4)
        y, x = y[ok], x[ok]
        if not len(y):
            continue
        pk = h[y, x]
        # Median der 8 Ringpunkte: ein länglicher Fleck (Paar, Plateau) trifft höchstens 2–3, ein Stern alle
        good = np.median([h[y + dy, x + dx] for dy, dx in RING], axis=0) < SHARP * pk
        for o in range(C):
            if o != c:
                # andere Kanäle: höchstens Rauschen bzw. ein kleiner Bruchteil der Spitze (Halo eines Nachbarsterns) –
                # Sterne, auch rote, haben dort in Grün ≥ ~30 % von Rot
                # Anteil in Bildintensität (σ der Kanäle verschieden), die Rauschschwelle je Kanal
                good &= np.max([hs[o][y + dy, x + dx] for dy in (-1, 0, 1) for dx in (-1, 0, 1)], axis=0) < np.maximum(QUIET, OTHER * pk * sig[c] / sig[o])
        found += [(c, int(a), int(b), float(p)) for a, b, p in zip(y[good], x[good], pk[good])]
    if found:
        t = cKDTree(np.array([(f[2], f[1]) for f in found]))
        n = [len(t.query_ball_point((f[2], f[1]), GROUP_R)) for f in found]
        found = [f for f, m in zip(found, n) if m >= GROUP_N or f[3] >= STRONG]
    return found, med, sig


def _fill(a: np.ndarray, hp: np.ndarray, m: np.ndarray, sig: float) -> bool:
    """Fleck `m` in `a` (Sicht) durch eine Ebene ersetzen, angepasst an einen Ring um den Fleck ohne auffällige Pixel und
    auf dessen Wertebereich begrenzt – folgt dem Hang eines Nachbarsterns oder Nebels, schießt aber nie über (eine
    quadratische Fläche erzeugte neue Punkte)."""
    ring = ndi.binary_dilation(m, iterations=3) & ~ndi.binary_dilation(m) & (np.abs(hp) < 3 * sig)
    yy, xx = np.nonzero(ring)
    if len(yy) < 8:
        return False
    v = a[ring].astype(np.float64)
    A = np.column_stack([np.ones(len(yy)), yy, xx]).astype(np.float64)
    coef, *_ = np.linalg.lstsq(A, v, rcond=None)
    by, bx = np.nonzero(m)
    fill = np.column_stack([np.ones(len(by)), by, bx]).astype(np.float64) @ coef
    a[m] = np.clip(fill, v.min(), v.max()).astype(a.dtype)
    return True


def repair(img: np.ndarray, cov: np.ndarray | None = None) -> tuple[np.ndarray, dict]:
    """Gibt (korrigierter Stack, Info) zurück; ohne Treffer unverändert (gleiches Objekt)."""
    if img.ndim != 3 or img.shape[0] != 3:
        return img, {"v": VERSION, "n": 0}
    found, med, sig = find(img, cov)
    if not found:
        return img, {"v": VERSION, "n": 0}
    out = img.copy()
    C, H, W = img.shape
    hs = (img - med) / sig[:, None, None]
    for c in range(C):
        # Maske aller Flecken des Kanals: um jede Spitze (≤ 3 px, Saum ≤ 4 px) die Pixel über dem 7×7-Median, in denen
        # die anderen Kanäle ruhig sind – so wächst ein Fleck nicht in den Hang eines Nachbarsterns
        mask = np.zeros((H, W), bool)
        for cc, y, x, pk in found:
            if cc != c:
                continue
            y0, y1, x0, x1 = max(0, y - 4), min(H, y + 5), max(0, x - 4), min(W, x + 5)
            # Fleckgrenze gegen den Hintergrund im Ring 6–10 px (der 7×7-Median ist bei großen Flecken selbst angehoben)
            Y0, Y1, X0, X1 = max(0, y - 10), min(H, y + 11), max(0, x - 10), min(W, x + 11)
            gy, gx = np.mgrid[Y0:Y1, X0:X1]
            far = np.maximum(np.abs(gy - y), np.abs(gx - x)) >= 6
            bgv = float(np.median(img[c, Y0:Y1, X0:X1][far]))
            h = np.maximum((img[c, y0:y1, x0:x1] - bgv) / sig[c], hs[c, y0:y1, x0:x1])   # neben Sternen ist der Ring angehoben
            quiet = np.all([hs[o, y0:y1, x0:x1] < np.maximum(QUIET, OTHER * hs[c, y0:y1, x0:x1] * sig[c] / sig[o])
                            for o in range(C) if o != c], axis=0)
            yy, xx = np.mgrid[y0:y1, x0:x1]
            d = np.maximum(np.abs(yy - y), np.abs(xx - x))
            lab, _ = ndi.label((h > max(3, CORE * pk)) & quiet & (d <= 3))
            k = lab[y - y0, x - x0]
            if k:
                mask[y0:y1, x0:x1] |= ndi.binary_dilation(lab == k, iterations=2) & (h > 2) & quiet   # Saum (kubische Interpolation)
        lab, n = ndi.label(mask)
        for i, sl in enumerate(ndi.find_objects(lab), 1):
            y0, y1 = max(0, sl[0].start - 4), min(H, sl[0].stop + 4)
            x0, x1 = max(0, sl[1].start - 4), min(W, sl[1].stop + 4)
            m = lab[y0:y1, x0:x1] == i
            if m.sum() <= MAX_AREA:
                _fill(out[c, y0:y1, x0:x1], hs[c, y0:y1, x0:x1] * sig[c] + 1e9 * mask[y0:y1, x0:x1], m, sig[c])
    groups = len({(f[1] // GROUP_R, f[2] // GROUP_R) for f in found})
    # Fingerabdruck der Reparatur – für Zwischenspeicher, die das Bild nur ausgedünnt hashen (GraXpert: jeder 7. Pixel)
    key = hashlib.sha1(json.dumps([VERSION, sorted((c, y, x) for c, y, x, _ in found)]).encode()).hexdigest()[:12]
    return out, {"v": VERSION, "n": len(found), "channels": np.bincount([f[0] for f in found], minlength=3).tolist(),
                 "areas": groups, "key": key}
