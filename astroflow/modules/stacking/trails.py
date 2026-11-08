"""Satelliten-, Flugzeug- und Meteorspuren je Aufnahme finden und beim Stacken gezielt ausblenden.

Erkennung (`find`) auf einem Einzelbild, nur numpy/scipy (OpenCV gibt es nur in der optionalen SkyTrace-Engine):
Hintergrund ab, kompakte und flächige Quellen (Sterne, Galaxie, Nebel) ausblenden, Hough-Abstimmung über die übrigen
hellen Pixel, jede Linie dann wie in SkyTraces `line_detection` über ihren Kontrast in 24-px-Abschnitten prüfen
(schmaler Grat gegen beide Seiten, Ausreißer gekappt – eine zufällige Sternkette trägt keine ganze Linie).
Breite = Querprofil, solange der Überschuss über dem Einzelpixel-Rauschen liegt.

Beim Stacken (`Pipeline._stack`) werden die Linien ins Referenzraster umgerechnet, mit Saum gerastert (`band`) und
aus der Gültigkeitsmaske genommen – CPU und GPU beachten sie gleich. Vorher `confirm`: das Band muss in dieser Aufnahme
heller sein als im bisherigen Stack bzw. im Median der übrigen Aufnahmen des Stapels. Ein gerader Nebelfaden, der in
jeder Aufnahme steht, wird so nie ausgeblendet.
"""
from __future__ import annotations

import numpy as np
from scipy import ndimage as ndi

VERSION = 1
WORK_WIDTH = 800          # Erkennung auf höchstens so breitem Bild (wie SkyTrace 768 px)
BIN = 24                  # Abschnittslänge der Linienprüfung (Arbeitsbild-Pixel)
SCORE = 10.5              # integrierter Kontrast für lange Abschnitte (SkyTrace LINE_SCORE_THRESHOLD)
MIN_BINS = 4              # ≥ 96 px im Arbeitsbild
MAX_LINES = 8
MAX_AREA = 0.15           # mehr ausgeblendete Fläche je Aufnahme → eher Wolken/Schlieren, nichts ausblenden
CONFIRM_SIGMA = 3.0
NEW_FRACTION = 0.5        # Anteil des Bandsignals, der in der Vergleichsaufnahme fehlen muss


def _required(bins: int) -> float:
    return min(SCORE, 5.2 + 0.9 * (bins - 2))


def _sigma(a: np.ndarray) -> float:
    s = a[::2, ::2]
    return float(1.4826 * np.median(np.abs(s - np.median(s)))) + 1e-9


def _background(img: np.ndarray, box: int = 64) -> np.ndarray:
    from .quality import background_map
    return background_map(img, box)


def _elongated(lab: np.ndarray, n: int) -> np.ndarray:
    """Je Komponente: lang und schmal (Länge > 30 px, Achsenverhältnis² > 25)? Index 0 = Hintergrund."""
    out = np.zeros(n + 1, bool)
    if n == 0:
        return out
    idx = np.arange(1, n + 1)
    one = np.ones(lab.shape, np.float32)
    yy, xx = np.indices(lab.shape, dtype=np.float32)
    c = np.maximum(ndi.sum_labels(one, lab, idx), 1)
    mx, my = ndi.sum_labels(xx, lab, idx) / c, ndi.sum_labels(yy, lab, idx) / c
    sxx = ndi.sum_labels(xx * xx, lab, idx) / c - mx**2
    syy = ndi.sum_labels(yy * yy, lab, idx) / c - my**2
    sxy = ndi.sum_labels(xx * yy, lab, idx) / c - mx * my
    tr, det = sxx + syy, sxx * syy - sxy**2
    disc = np.sqrt(np.maximum(tr * tr / 4 - det, 0))
    l1, l2 = tr / 2 + disc, np.maximum(tr / 2 - disc, 0.05)
    out[1:] = (l1 / l2 > 25) & (np.sqrt(12 * l1) > 30)
    return out


RING = 16


def _sources(z: np.ndarray) -> np.ndarray:
    """Maske der Sterne und flächigen Objekte (Galaxie, Nebel) – Spurpixel sollen frei bleiben.
    Flächig: Öffnung von z > 3 mit einer Scheibe von 17 px, langgestreckte Reste davon (sehr helle, breite Spur) zählen nicht.
    Sterne: Maxima > 7σ, deren Ring bei 4 px nicht in zwei gegenüberliegenden Richtungen hell ist (eine Linie ist es,
    auch dort, wo sie einen Stern kreuzt). Ausgeblendet wird die Umgebung > 2,5σ des Sterns bis 12 px Abstand.
    Schwache Spurstücke (ohne Maximum > 7σ) bleiben frei – als eigene Komponenten wären sie sonst „rund“ und verschwunden."""
    hi = z > 3.0
    yy, xx = np.mgrid[-8:9, -8:9]
    big = ndi.binary_opening(hi, structure=(xx * xx + yy * yy) <= 64)
    lab, n = ndi.label(big, structure=np.ones((3, 3)))
    if n:
        big &= ~_elongated(lab, n)[lab]
        big = ndi.binary_dilation(big, iterations=2)
    peaks = (z == ndi.maximum_filter(z, size=7)) & (z > 7) & ~big
    py, px = np.nonzero(peaks)
    if len(py) == 0:
        return big
    ang = np.arange(RING) * 2 * np.pi / RING
    zs = ndi.gaussian_filter(z, 1.0)            # dünne Spur (1 px): Ringproben träfen sie sonst nur zufällig
    zpk = zs[py, px]
    line = np.zeros(len(py), bool)
    todo = np.ones(len(py), bool)
    for rr in (4, 8, 14):                   # kleinster Ring, der nicht rundum hell ist (breite helle Spur: erst bei 14 px)
        if not todo.any():
            break
        i = np.flatnonzero(todo)
        ry = py[i, None] + rr * np.sin(ang)[None]
        rx = px[i, None] + rr * np.cos(ang)[None]
        ring = ndi.map_coordinates(zs, [ry.ravel(), rx.ravel()], order=1, mode="nearest").reshape(len(i), RING)
        bright = ring > np.maximum(2.0, 0.25 * zpk[i])[:, None]
        full = bright.sum(1) > 12
        line[i] = (bright & np.roll(bright, RING // 2, axis=1)).any(1) & ~full
        todo[i] = full
    line |= todo                            # auch bei 14 px rundum hell: flächig, das deckt die Öffnung ab
    sy, sx, zp = py[~line], px[~line], z[py, px][~line]
    if len(sy) == 0:
        return big
    # Radius je Stern aus seiner Helligkeit: Gauß-Profil (σ ≈ 1,5 px nach Glättung) bis 2,5σ Rauschen, +1,5 px, ≤ 12 px.
    # Nicht „ganze Komponente > 2,5σ“: eine Spur verbindet in dichten Feldern viele Sterne zu einer Komponente.
    rad = np.minimum(12.0, 1.5 * np.sqrt(2 * np.log(np.maximum(zp, 2.6) / 2.5)) + 1.5)
    star = np.zeros(z.shape, bool)
    for r in np.unique(np.ceil(rad)):
        mark = np.ones(z.shape, bool)
        sel = np.ceil(rad) == r
        mark[sy[sel], sx[sel]] = False
        star |= ndi.distance_transform_edt(mark) <= r
    return star | big


def _hough(votes: np.ndarray, n_theta: int = 720):
    ys, xs = np.nonzero(votes)
    h, w = votes.shape
    diag = int(np.ceil(np.hypot(h, w)))
    th = np.linspace(0, np.pi, n_theta, endpoint=False)
    cos, sin = np.cos(th), np.sin(th)
    acc = np.zeros((n_theta, 2 * diag + 1), np.int32)
    for i in range(0, n_theta, 60):                             # in Blöcken (Speicher)
        r = np.rint(xs[:, None] * cos[None, i:i + 60] + ys[:, None] * sin[None, i:i + 60]).astype(np.int64) + diag
        k = (np.arange(i, min(i + 60, n_theta))[None, :] * (2 * diag + 1) + r).ravel()
        acc.ravel()[:] += np.bincount(k, minlength=acc.size)[: acc.size].astype(np.int32)
    return acc, th, diag


def _peaks(acc: np.ndarray, n: int, floor: int):
    mx = ndi.maximum_filter(acc, size=(9, 15), mode="wrap")
    t, r = np.nonzero((acc == mx) & (acc >= floor))
    o = np.argsort(acc[t, r])[::-1][:n]
    return t[o], r[o]


def _sample(field: np.ndarray, pts: np.ndarray, normal: np.ndarray, off: float) -> np.ndarray:
    p = pts + normal * off
    return ndi.map_coordinates(field, [p[:, 1], p[:, 0]], order=1, mode="nearest")


def _sides(zc, valid_map, pts, normal, offs):
    v = np.array([_sample(zc, pts, normal, o) for o in offs])
    v[np.array([_sample(valid_map, pts, normal, o) for o in offs]) < 0.5] = np.nan     # Seitenprobe auf einem Stern
    return v


def _nanmean0(v: np.ndarray) -> np.ndarray:
    n = np.isfinite(v).sum(0)
    return np.where(n > 0, np.nansum(v, 0) / np.maximum(n, 1), np.nan)


def _nanmedian0(v: np.ndarray) -> np.ndarray:
    """Median über Achse 0 ohne NaN, ohne Warnung bei leeren Spalten (warnings.catch_warnings ist nicht threadsicher)."""
    s = np.sort(v, 0)                              # NaN ans Ende
    n = np.isfinite(v).sum(0)
    i = np.arange(v.shape[1])
    lo = s[np.maximum((n - 1) // 2, 0), i]
    hi = s[np.maximum(n // 2, 0), i]
    return np.where(n > 0, (lo + hi) / 2, np.nan)


def _contrast(zc, valid_map, pts, normal) -> np.ndarray:
    """Linienkontrast je Abtastpunkt (z-Einheiten, gekappt): Grat gegen den Median der Seitenbänder und gegen die dunklere
    der beiden nahen Seiten (schmal). Seitenproben auf Sternen zählen nicht – in dichten Feldern zogen sie sonst jeden
    Abschnitt ins Negative. Zweite Skala für sehr helle, breite Spuren (nur bei Grat > 8σ, sonst passten Galaxienarme)."""
    out = None
    for core, far, near, need in (((-1, 0, 1), (-11, -9, -7, 7, 9, 11), (4, 5), 0.0),
                                  ((-3, 0, 3), (-24, -20, -16, 16, 20, 24), (10, 11), 8.0)):
        sig = np.mean([_sample(zc, pts, normal, o) for o in core], 0)
        sides = _nanmedian0(_sides(zc, valid_map, pts, normal, far))
        nr = _sides(zc, valid_map, pts, normal, (-near[1], -near[0], near[0], near[1]))
        narrow = sig - np.fmin(_nanmean0(nr[:2]), _nanmean0(nr[2:]))
        with np.errstate(invalid="ignore"):
            c = np.clip(np.minimum(sig - sides, narrow * 1.4), -4, 4)
            c = np.where(np.isfinite(c) & (sig >= need), c, -4.0 if need else np.nan)
        out = c if out is None else np.fmax(out, c)
    return out


def _section(bins: np.ndarray):
    """Bester zusammenhängender Abschnitt; unbekannte Abschnitte (verdeckt von Stern/Galaxie, NaN) dürfen innen liegen."""
    good = bins > 0.32
    known = ~np.isnan(bins)
    best = None
    n = len(bins)
    for a in np.flatnonzero(good):
        for b in range(a + MIN_BINS, n + 1):
            if not good[b - 1]:
                continue
            k = known[a:b]
            if k.sum() < MIN_BINS or good[a:b][k].mean() < 0.65:
                continue
            s = float(np.mean(bins[a:b][k]) * np.sqrt(k.sum() * BIN / 4))
            if s < _required(int(k.sum())):
                continue
            if best is None or s > best[0]:
                best = (s, a, b)
    return best


def find(img: np.ndarray, fwhm: float | None = None) -> list[list[float]]:
    """Spuren in einem Luminanzbild → [[x1, y1, x2, y2, Halbbreite, Kontrast], …] in Pixeln von `img`."""
    img = np.asarray(img, np.float32)
    h0, w0 = img.shape
    f = max(1, int(np.ceil(w0 / WORK_WIDTH)))
    h, w = h0 // f, w0 // f
    small = img[: h * f, : w * f].reshape(h, f, w, f).mean((1, 3)) if f > 1 else img
    res = small - _background(small)
    sig_px = _sigma(res - ndi.uniform_filter(res, 3))  * 1.13       # Einzelpixel-Rauschen (Arbeitsbild)
    sm = ndi.gaussian_filter(res, 0.8)
    s_sm = _sigma(sm)
    z = sm / s_sm
    src = _sources(z)
    votes = (z > 1.5) & ~src
    b = 12
    votes[:b] = votes[-b:] = False
    votes[:, :b] = votes[:, -b:] = False
    if votes.sum() < 30:
        return []
    acc, th, diag = _hough(votes)
    vy, vx = np.nonzero(votes)
    floor = max(25, int(min(h, w) * 0.042))
    ts, rs = _peaks(acc, 40, floor)
    zc = np.clip(z, -50, 50)
    valid_map = (~src).astype(np.float32)
    out: list = []
    tt = np.arange(-np.hypot(h, w), np.hypot(h, w), 1.0)
    for ti, ri in zip(ts, rs):
        theta, rho = th[ti], ri - diag
        normal = np.array([np.cos(theta), np.sin(theta)])
        # verfeinern: Gerade durch die Stimmen nahe der Hough-Lösung (Raster 0,25° / 1 px lässt lange Spuren sonst abdriften)
        dist = np.abs(vx * normal[0] + vy * normal[1] - rho)
        sel = dist < 3
        if sel.sum() >= 20:
            q = np.column_stack([vx[sel], vy[sel]]).astype(float)
            c0 = q.mean(0)
            _, vec = np.linalg.eigh(np.cov((q - c0).T))
            normal = vec[:, 0]
            rho = float(c0 @ normal)
        direction = np.array([-normal[1], normal[0]])
        pts = normal * rho + tt[:, None] * direction
        inside = (pts[:, 0] > 14) & (pts[:, 0] < w - 15) & (pts[:, 1] > 14) & (pts[:, 1] < h - 15)
        pts = pts[inside]
        if len(pts) < MIN_BINS * BIN:
            continue
        con = _contrast(zc, valid_map, pts, normal)
        ok = _sample(valid_map, pts, normal, 0) > 0.5
        nb = len(pts) // BIN
        bins = np.full(nb, np.nan)
        for i in range(nb):
            k = ok[i * BIN:(i + 1) * BIN]
            k &= np.isfinite(con[i * BIN:(i + 1) * BIN])
            if k.sum() >= 12:
                bins[i] = float(con[i * BIN:(i + 1) * BIN][k].mean())
        best = _section(bins)
        if best is None:
            continue
        score, a, e = best
        # Enden: über verdeckte (Stern, Galaxie) und noch schwach helle Abschnitte weiter nach außen, dann ein Abschnitt
        # Zugabe (die Spur kann im letzten, nur teilweise hellen Abschnitt enden); erreicht sie den Rand, bis zum Bildrand
        level = 0.25 * float(np.mean(bins[a:e][np.isfinite(bins[a:e])]))
        while a > 0 and (np.isnan(bins[a - 1]) or bins[a - 1] > level):
            a -= 1
        while e < nb and (np.isnan(bins[e]) or bins[e] > level):
            e += 1
        p1 = pts[0] - direction * 14 if a == 0 else pts[a * BIN] - direction * BIN
        p2 = pts[-1] + direction * 14 if e * BIN >= len(pts) - BIN else pts[e * BIN - 1] + direction * BIN
        mid = (p1 + p2) / 2
        if any(abs(np.dot(mid - np.array(o[6]), o[7])) < o[4] + 6 and abs(np.dot(direction, o[8])) > 0.995 for o in out):
            continue                                                 # dieselbe Spur, Nachbarlösung der Abstimmung
        # Querprofil (nur unverdeckte Stellen des Abschnitts): Halbbreite, solange der Überschuss > 1σ je Pixel
        seg = pts[a * BIN: e * BIN][ok[a * BIN: e * BIN]]
        offs = np.arange(-20, 21)
        prof = np.array([np.median(_sample(res, seg, normal, o)) for o in offs])
        prof -= np.median(prof[np.abs(offs) >= 16])
        hw = 1
        while hw < 20 and min(prof[20 + hw], prof[20 - hw]) > max(sig_px, 0.01 * prof[19:22].max()):
            hw += 1
        out.append([*p1, *p2, float(hw), score, mid.tolist(), normal, direction])
        # angenommene Spur ausblenden: Nachbarlösungen der Abstimmung (Halo heller Spuren) finden dort keine Abschnitte mehr
        valid_map[band((h, w), [[*p1, *p2, hw]], pad=4)] = 0
    if len(out) > MAX_LINES:
        out = sorted(out, key=lambda o: -o[5])[:MAX_LINES]
    lines = [[round(float((v + 0.5) * f - 0.5), 1) for v in o[:4]] + [round(float(o[4] * f + 0.5 * f), 1), round(float(o[5]), 1)]
             for o in out]
    area = sum(np.hypot(l[2] - l[0], l[3] - l[1]) * 2 * l[4] for l in lines)
    return [] if area > MAX_AREA * h0 * w0 else lines


def to_full(lines: list, s: int) -> list:
    """Linien aus dem (2×2 gebinnten) Analysebild in Vollauflösungs-Pixel."""
    if s == 1:
        return [list(l) for l in lines]
    return [[round((l[0] + 0.5) * s - 0.5, 1), round((l[1] + 0.5) * s - 0.5, 1), round((l[2] + 0.5) * s - 0.5, 1),
             round((l[3] + 0.5) * s - 0.5, 1), round(l[4] * s, 1), *l[5:]] for l in lines]


def blank(img: np.ndarray, lines: list, value: np.ndarray | float, pad: float = 2.0) -> np.ndarray:
    """Spurpixel (mit `pad` Saum) im Bild durch `value` ersetzen – vor der Sternerkennung, damit die Spur keine Sterne liefert."""
    if not lines:
        return img
    m = band(img.shape, lines, pad=pad)
    out = img.copy()
    out[m] = value[m] if isinstance(value, np.ndarray) else value
    return out


def transform(lines: list, M: np.ndarray, scale: int = 1) -> list:
    """Vollauflösungs-Linien eines Frames → Pixel des (`scale`-fach überabgetasteten) Referenzrasters."""
    out = []
    det = abs(np.linalg.det(M[:2, :2])) ** 0.5
    for l in lines:
        p = M @ np.array([[l[0], l[2]], [l[1], l[3]], [1, 1]], float)
        q = (p[:2] + 0.5) * scale - 0.5
        out.append([float(q[0, 0]), float(q[1, 0]), float(q[0, 1]), float(q[1, 1]), float(l[4] * det * scale)])
    return out


def band(shape: tuple, lines: list, pad: float = 0.0, ends: float | None = None) -> np.ndarray:
    """Pixel mit Abstand ≤ Halbbreite + `pad` zur Strecke (Enden um `ends`, Standard Halbbreite + pad, verlängert)."""
    H, W = shape[-2:]
    m = np.zeros((H, W), bool)
    for l in lines:
        x1, y1, x2, y2, hw = l[:5]
        r = hw + pad
        e = r if ends is None else ends
        d = np.array([x2 - x1, y2 - y1], float)
        L = float(np.hypot(*d))
        if L < 1e-6:
            continue
        d /= L
        x1, y1, x2, y2 = x1 - d[0] * e, y1 - d[1] * e, x2 + d[0] * e, y2 + d[1] * e
        L += 2 * e
        xa, xb = int(max(0, np.floor(min(x1, x2) - r))), int(min(W, np.ceil(max(x1, x2) + r) + 1))
        ya, yb = int(max(0, np.floor(min(y1, y2) - r))), int(min(H, np.ceil(max(y1, y2) + r) + 1))
        if xa >= xb or ya >= yb:
            continue
        yy, xx = np.mgrid[ya:yb, xa:xb].astype(np.float32)
        u = (xx - x1) * d[0] + (yy - y1) * d[1]
        v = np.abs(-(xx - x1) * d[1] + (yy - y1) * d[0])
        m[ya:yb, xa:xb] |= (v <= r) & (u >= 0) & (u <= L)
    return m


def confirm(lum: np.ndarray, ref: np.ndarray | None, valid: np.ndarray, line: list, noise: float) -> bool:
    """Ist das Band in dieser Aufnahme neu? Überschuss gegen die Vergleichsaufnahme (bisheriger Stack bzw. Median der
    übrigen Aufnahmen) muss signifikant sein **und** mindestens `NEW_FRACTION` des eigenen Bandsignals (gegen die Umgebung)
    ausmachen: eine Spur fehlt im Vergleich ganz, ein statischer Nebelfaden ist dort fast gleich hell – nur in schärferen
    Aufnahmen etwas spitzer, das allein war signifikant (Demo: 9 von 24 Aufnahmen). ref None = nichts zum Vergleichen → ja."""
    if ref is None:
        return True
    x1, y1, x2, y2, hw = line[:5]
    fin = valid & np.isfinite(ref)
    core = band(lum.shape, [[x1, y1, x2, y2, max(1.0, hw * 0.6)]], ends=0.0) & fin
    n = int(core.sum())
    if n < 20:
        return True
    side = band(lum.shape, [[x1, y1, x2, y2, hw + 8]], ends=0.0) & ~band(lum.shape, [[x1, y1, x2, y2, hw + 3]], ends=0.0) & fin
    excess = float(np.mean((lum - ref)[core]))
    own = float(np.mean(lum[core]) - np.median(lum[side])) if side.sum() >= 20 else excess
    # Nachbarpixel sind nach Debayer/Interpolation korreliert: Rauschen des Mittels konservativ über √(n/9)
    return excess > CONFIRM_SIGMA * noise / np.sqrt(n / 9) and excess >= NEW_FRACTION * max(own, 0.0)
