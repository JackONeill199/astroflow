"""Vollautomatische Nachbearbeitung eines linearen Stacks zu einem Astrofoto.

Alle Schritte laufen mit Parametern aus einem Profil (siehe objects.PROFILES), die vom
Bildprüfer (reviewer.py) iterativ angepasst werden können.
"""
from __future__ import annotations

import warnings

import numpy as np
from scipy import ndimage as ndi
from scipy.signal import fftconvolve

from . import stars as starsep
from .quality import detect_stars, noise_sigma


def luminance(img: np.ndarray) -> np.ndarray:
    return img.mean(0)


def smoothstep(x, a, b):
    t = np.clip((x - a) / (b - a + 1e-12), 0, 1)
    return t * t * (3 - 2 * t)


# ---------------------------------------------------------------------------- #
def crop_to_coverage(img: np.ndarray, cov: np.ndarray, thr: float = 0.5) -> np.ndarray:
    y0, y1, x0, x1 = coverage_box(cov, thr)
    return img[:, y0:y1, x0:x1]


def coverage_box(cov: np.ndarray, thr: float = 0.5) -> tuple[int, int, int, int]:
    """Größtes Rechteck (y0, y1, x0, x1) mit (fast) voller Abdeckung."""
    mask = cov > thr
    if not mask.any():
        return 0, cov.shape[0], 0, cov.shape[1]
    rows, cols = np.nonzero(mask.any(1))[0], np.nonzero(mask.any(0))[0]
    y0, y1, x0, x1 = rows[0], rows[-1] + 1, cols[0], cols[-1] + 1
    while y1 - y0 > 16 and x1 - x0 > 16:
        sub = mask[y0:y1, x0:x1]
        top, bot, left, right = sub[0].mean(), sub[-1].mean(), sub[:, 0].mean(), sub[:, -1].mean()
        worst = min(top, bot, left, right)
        if worst >= 0.98:
            break
        if worst == top:
            y0 += 1
        elif worst == bot:
            y1 -= 1
        elif worst == left:
            x0 += 1
        else:
            x1 -= 1
    return int(y0), int(y1), int(x0), int(x1)


def _star_mask(L: np.ndarray, grow: int = 3, sigma_k: float = 3.0) -> np.ndarray:
    med = np.median(L)
    sig = 1.4826 * np.median(np.abs(L - med)) + 1e-9
    m = L > med + sigma_k * sig * 3
    return ndi.binary_dilation(m, iterations=grow)


EDGE_EXCLUDE = 0.12


def remove_gradient(img: np.ndarray, degree: int = 3, grid: int = 16, edge_exclude: float = 0.0) -> np.ndarray:
    """Additive Hintergrundextraktion per Polynom-Fit auf Hintergrundkacheln.
    edge_exclude: Randanteil, dessen Kacheln nicht in den Fit eingehen – steiles Randglühen (Stellina ohne Darks)
    zieht das Polynom sonst nach außen und erzeugt Über-/Unterschwinger als helle/dunkle Bänder im Bild.
    Die Ränder korrigiert danach remove_edge_glow."""
    C, H, W = img.shape
    L = luminance(img)
    mask = _star_mask(L)
    gh, gw = max(4, min(grid, H // 24)), max(4, min(grid, W // 24))
    ys = np.linspace(0, H, gh + 1).astype(int)
    xs = np.linspace(0, W, gw + 1).astype(int)
    pts, vals = [], []
    for i in range(gh):
        for j in range(gw):
            ok = ~mask[ys[i] : ys[i + 1], xs[j] : xs[j + 1]]
            if ok.mean() < 0.3:
                continue
            cy, cx = (ys[i] + ys[i + 1]) / 2 / H, (xs[j] + xs[j + 1]) / 2 / W
            if edge_exclude and min(cy, 1 - cy, cx, 1 - cx) < edge_exclude:
                continue
            pts.append(((ys[i] + ys[i + 1]) / 2 / H * 2 - 1, (xs[j] + xs[j + 1]) / 2 / W * 2 - 1))
            vals.append([np.median(img[c, ys[i] : ys[i + 1], xs[j] : xs[j + 1]][ok]) for c in range(C)])
    pts, vals = np.array(pts), np.array(vals)
    nterm = (degree + 1) * (degree + 2) // 2
    if len(pts) < nterm + 4:
        return img - np.median(img.reshape(C, -1), axis=1)[:, None, None]

    def basis(y, x):
        return np.stack([y**a * x**b for a in range(degree + 1) for b in range(degree + 1 - a)], axis=-1)

    A = basis(pts[:, 0], pts[:, 1])
    use = np.ones(len(pts), bool)
    coef = None
    for _ in range(4):                           # nebelige Kacheln iterativ verwerfen
        coef = np.linalg.lstsq(A[use], vals[use], rcond=None)[0]
        res = vals - A @ coef
        r = np.abs(res).max(1)
        s = 1.4826 * np.median(np.abs(r[use] - np.median(r[use]))) + 1e-9
        new = r < np.median(r[use]) + 2.0 * s
        if new.sum() < nterm + 2 or (new == use).all():
            break
        use = new
    h, w = max(8, H // 8), max(8, W // 8)
    yy, xx = np.meshgrid(np.linspace(-1, 1, h), np.linspace(-1, 1, w), indexing="ij")
    low = basis(yy, xx) @ coef                   # (h,w,C)
    out = np.empty_like(img)
    for c in range(C):
        bgm = ndi.zoom(low[..., c], (H / h, W / w), order=1, mode="nearest")
        bgm = np.pad(bgm, ((0, max(0, H - bgm.shape[0])), (0, max(0, W - bgm.shape[1]))), mode="edge")[:H, :W]
        out[c] = img[c] - bgm
    return out


def _poly_basis(y, x, degree):
    return np.stack([y**a * x**b for a in range(degree + 1) for b in range(degree + 1 - a)], axis=-1)


def sky_background(img: np.ndarray, max_degree: int = 3, grid: int = 24, tol: float = 1.0,
                   edge_exclude: float = 0.0, cov: np.ndarray | None = None) -> tuple[np.ndarray, dict]:
    """Hintergrundmodell ohne KI, nach dem Vorbild von DBE (PixInsight): Stützpunkte nur auf echtem Himmel, glattes Modell.
    - Kacheln mit robustem Median (Sterne vorher ausgeblendet), je Kanal.
    - Auswahl asymmetrisch: Nebel, Staub und Galaxien *addieren* Licht – Kacheln deutlich **über** dem Modell sind Objekt
      und fallen heraus, Kacheln darunter bleiben (das alte Verfahren verwarf nach |Abweichung| und ließ schwachen,
      großflächigen Nebel als „Himmel“ stehen, der dann abgezogen wurde).
    - Polynomgrad wächst nur, solange der Fit dadurch deutlich besser wird (1 → 2 → 3) – ein steifes Modell kann
      100–500 px große Staubwolken nicht nachbilden und also nicht abziehen.
    - Abdeckung (`cov`, optional): Wo nur ein Teil der Aufnahmen beiträgt (Feldrotation, Nächte mit anderem Ausschnitt),
      hat der Stack einen anderen Himmelswert – eine Stufe entlang der Abdeckungsgrenze („Rahmen“ bei Stellina). Dafür ein
      zusätzlicher Term (1 − Abdeckung) im Fit.
    Gibt (Modell (C,H,W), Info) zurück; Info: Grad, Zahl der Stützpunkte, Anteil verworfener Kacheln."""
    C, H, W = img.shape
    f = max(1, int(max(H, W) // 600))
    sm = img[:, : H // f * f, : W // f * f].reshape(C, H // f, f, W // f, f).mean((2, 4)) if f > 1 else img
    h, w = sm.shape[1:]
    L = sm.mean(0)
    opened = ndi.grey_opening(L, size=5)
    resid = L - opened
    s0 = 1.4826 * float(np.median(np.abs(resid - np.median(resid)))) + 1e-12
    stars = ndi.binary_dilation(resid > 4 * s0, iterations=2)
    cv = None
    if cov is not None and cov.shape == (H, W) and float(np.percentile(cov, 10)) < 0.97:
        cv = cov[: H // f * f, : W // f * f].reshape(h, f, w, f).mean((1, 3)) if f > 1 else cov
        cv = 1.0 - np.clip(cv, 0, 1)
    gw = grid
    gh = max(6, int(round(grid * h / w)))
    ys, xs = np.linspace(0, h, gh + 1).astype(int), np.linspace(0, w, gw + 1).astype(int)
    pts, vals, rough, cvt = [], [], [], []
    for i in range(gh):
        for j in range(gw):
            ok = ~stars[ys[i]: ys[i + 1], xs[j]: xs[j + 1]]
            if ok.mean() < 0.4:
                continue
            cy, cx = (ys[i] + ys[i + 1]) / 2 / h, (xs[j] + xs[j + 1]) / 2 / w
            if edge_exclude and min(cy, 1 - cy, cx, 1 - cx) < edge_exclude:
                continue
            blk = sm[:, ys[i]: ys[i + 1], xs[j]: xs[j + 1]][:, ok]
            pts.append((cy * 2 - 1, cx * 2 - 1))
            vals.append(np.median(blk, axis=1))
            cvt.append(float(np.median(cv[ys[i]: ys[i + 1], xs[j]: xs[j + 1]])) if cv is not None else 0.0)
            lb = blk.mean(0)
            rough.append(1.4826 * float(np.median(np.abs(lb - np.median(lb)))))
    pts, vals, rough, cvt = np.array(pts), np.array(vals), np.array(rough), np.array(cvt)
    use_cov = cv is not None and (cvt > 0.03).sum() >= 4
    if len(pts) < 16:
        return np.broadcast_to(np.median(img.reshape(C, -1), axis=1)[:, None, None], img.shape).astype(np.float32), {"degree": 0}
    t = vals.mean(1)
    # unruhige Kacheln (Nebelkanten, Sternhaufen) sind schlechte Stützpunkte
    use = rough <= np.median(rough) * 2.5
    # Start: untere Hälfte der Kacheln – Objektkacheln gar nicht erst mitnehmen
    use &= t <= np.percentile(t[use], 60)
    deg, coef = 0, None
    best = None
    for d in range(1, max_degree + 1):
        nterm = (d + 1) * (d + 2) // 2
        A = _poly_basis(pts[:, 0], pts[:, 1], d)
        if use_cov:
            A = np.c_[A, cvt]
            nterm += 1
        u = use.copy()
        for _ in range(8):
            if u.sum() < nterm + 6:
                break
            cf = np.linalg.lstsq(A[u], vals[u], rcond=None)[0]
            r = t - (A @ cf).mean(1)
            s = 1.4826 * float(np.median(np.abs(r[u] - np.median(r[u])))) + 1e-12
            new = use & (r < np.median(r[u]) + tol * s) & (r > np.median(r[u]) - 4 * s)
            if (new == u).all():
                break
            u = new
        if u.sum() < nterm + 6:
            break
        cf = np.linalg.lstsq(A[u], vals[u], rcond=None)[0]
        r = t[u] - (A[u] @ cf).mean(1)
        rss = float(np.mean(r ** 2)) + 1e-30
        bic = u.sum() * np.log(rss) + nterm * np.log(u.sum()) * 2.0     # strenger als BIC: lieber steif
        if best is None or bic < best[0]:
            best = (bic, d, cf, u)
    if best is None:
        return np.broadcast_to(np.median(img.reshape(C, -1), axis=1)[:, None, None], img.shape).astype(np.float32), {"degree": 0}
    _, deg, coef, u = best
    gy, gx = max(8, H // 8), max(8, W // 8)
    yy, xx = np.meshgrid(np.linspace(-1, 1, gy), np.linspace(-1, 1, gx), indexing="ij")
    nb = (deg + 1) * (deg + 2) // 2
    low = _poly_basis(yy, xx, deg) @ coef[:nb]
    model = np.empty_like(img, dtype=np.float32)
    for c in range(C):
        bgm = ndi.zoom(low[..., c], (H / gy, W / gx), order=1, mode="nearest")
        model[c] = np.pad(bgm, ((0, max(0, H - bgm.shape[0])), (0, max(0, W - bgm.shape[1]))), mode="edge")[:H, :W]
        if use_cov:                                   # Stufe an der Abdeckungsgrenze in voller Auflösung (geglättet)
            model[c] += float(coef[nb, c]) * ndi.gaussian_filter(1.0 - np.clip(cov, 0, 1), 2.0).astype(np.float32)
    return model, {"degree": int(deg), "samples": int(u.sum()), "tiles": int(len(pts)),
                   "rejected": round(1 - float(u.sum()) / len(pts), 3), "coverage_term": bool(use_cov)}


def remove_edge_glow(img: np.ndarray, band: float = 0.15) -> np.ndarray:
    """Steile Aufhellungen an den Bildrändern (Sensor-/Verstärkerglühen ohne Darks), die ein globales Polynom
    nicht erfasst: je Kanal Zeilen-/Spaltenprofil des sternfreien Hintergrunds (30-%-Perzentil, robust gegen
    Nebel) messen und nur in den äußeren Randbändern auf das Niveau der Bildmitte bringen."""
    C, H, W = img.shape
    bgmask = ~_star_mask(luminance(img), grow=4)
    out = img.copy()
    for axis, n in ((1, H), (2, W)):                 # axis 1: Zeilenprofil (oben/unten), 2: Spaltenprofil (links/rechts)
        b = max(8, int(band * n))
        pos = np.arange(n)
        d = np.minimum(pos, n - 1 - pos)
        # voll korrigieren bis 60 % der Bandbreite, dann weich ausblenden (eine reine Kosinusrampe ließ dort ~50 % Glühen stehen)
        f = np.clip((d - 0.6 * b) / (0.4 * b), 0, 1)
        taper = np.where(d < b, 0.5 * (1 + np.cos(np.pi * f)), 0.0)
        for c in range(C):
            v = np.where(bgmask, img[c], np.nan)
            with warnings.catch_warnings():               # komplett maskierte Zeilen -> NaN, wird unten ersetzt
                warnings.simplefilter("ignore", RuntimeWarning)
                prof = np.nanpercentile(v, 30, axis=1 if axis == 1 else 0)
            prof = np.where(np.isfinite(prof), prof, np.nanmedian(prof))
            prof = ndi.gaussian_filter1d(prof, max(2.0, n / 400), mode="nearest")   # fein: Randanstieg ist steil
            ref = np.median(prof[int(0.3 * n) : int(0.7 * n)])
            corr = (prof - ref) * taper
            out[c] -= corr[:, None] if axis == 1 else corr[None, :]
    # Ecken (nicht zeilen-/spaltenförmig): grobe 2D-Hintergrundkarte, nur im Randbereich angewendet
    ty, tx = max(16, H // 20), max(16, W // 30)
    gh, gw = H // ty, W // tx
    if gh >= 6 and gw >= 6:
        dy = np.minimum(np.arange(H), H - 1 - np.arange(H)) / (band * H)
        dx = np.minimum(np.arange(W), W - 1 - np.arange(W)) / (band * W)
        w2 = np.clip(1 - np.minimum(dy[:, None], dx[None, :]), 0, 1) ** 2
        for c in range(C):
            v = np.where(bgmask, out[c], np.nan)[: gh * ty, : gw * tx].reshape(gh, ty, gw, tx).swapaxes(1, 2).reshape(gh, gw, -1)
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)
                grid = np.nanpercentile(v, 30, axis=2)
            grid = np.where(np.isfinite(grid), grid, np.nanmedian(grid))
            grid = ndi.median_filter(grid, size=(1, 3), mode="nearest") * 0.5 + ndi.median_filter(grid, size=(3, 1), mode="nearest") * 0.5
            ref = np.median(grid[gh // 4 : 3 * gh // 4, gw // 4 : 3 * gw // 4])
            bgm = ndi.zoom(grid - ref, (H / gh, W / gw), order=3, mode="nearest")
            bgm = np.pad(bgm, ((0, max(0, H - bgm.shape[0])), (0, max(0, W - bgm.shape[1]))), mode="edge")[:H, :W]
            out[c] -= bgm * w2
    return out


def trim_bad_edges(img: np.ndarray, max_frac: float = 0.05) -> tuple[np.ndarray, list[int]]:
    """Randzeilen/-spalten abschneiden, deren Hintergrund nach allen Korrekturen noch deutlich vom
    Bildinneren abweicht (extrem steiles Randglühen). Höchstens max_frac je Seite."""
    C, H, W = img.shape
    L = luminance(img)
    bg = L[~_star_mask(L, grow=3)]
    sig = 1.4826 * float(np.median(np.abs(bg - np.median(bg)))) + 1e-9
    ref = float(np.median(bg))
    cut = []
    for axis, n in ((1, H), (0, W)):                 # axis 1: Zeilen (Median über x), 0: Spalten
        prof = np.median(L, axis=axis)
        lim = 0.6 * sig                               # Zeilen-/Spaltenmedian rauscht kaum -> strenge Schwelle
        for side in (0, 1):
            k = 0
            idx = (lambda i: i) if side == 0 else (lambda i: n - 1 - i)
            while k < int(max_frac * n) and abs(prof[idx(k)] - ref) > lim:
                k += 1
            # dort, wo es kippt, noch etwas Sicherheitsabstand
            cut.append(k + (max(2, k // 4) if k else 0))
    y0, y1, x0, x1 = cut[0], H - cut[1], cut[2], W - cut[3]
    return img[:, y0:y1, x0:x1], cut


def neutral_saturated_cores(img: np.ndarray, sat: np.ndarray, fwhm: float) -> np.ndarray:
    """Gesättigte Sternkerne enthalten keine Farbinformation (alle Kanäle am Anschlag); nach dem Weißabgleich
    würden sie sonst cyan/grün. Sie werden weich auf neutral (hellster Kanal) gesetzt."""
    if not sat.any():
        return img
    w = ndi.binary_dilation(sat, iterations=max(1, int(round(fwhm * 0.6))))
    w = np.clip(ndi.gaussian_filter(w.astype(np.float32), max(1.0, fwhm / 3)) * 1.5, 0, 1)
    core = img.max(0)
    return img * (1 - w) + core[None] * w


def saturation_mask(img: np.ndarray) -> np.ndarray:
    """Pixel, in denen mindestens ein Kanal nahe am höchsten Wert des Stacks liegt (Sensor-Sättigung –
    vor dem Weißabgleich sättigen alle Kanäle bei demselben ADU-Wert)."""
    mx = img.max(0)
    top = float(mx.max())
    if top <= 0:
        return np.zeros(mx.shape, bool)
    m = mx > 0.8 * top
    return m if m.mean() < 0.01 else np.zeros(mx.shape, bool)   # mehr als 1 %: kein Sättigungsfall


def align_channels(img: np.ndarray, max_shift: float = 2.0) -> tuple[np.ndarray, list]:
    """Farbkanäle subpixelgenau auf Grün ausrichten (atmosphärische Dispersion, Farbquerfehler).
    Versatz = Median der Sternschwerpunkt-Differenzen; ohne genug Sterne bleibt alles unverändert."""
    if img.shape[0] != 3:
        return img, []
    ref = detect_stars(img[1], max_stars=150, thresh=10)
    if len(ref) < 15:
        return img, []
    rp = np.column_stack([ref.x, ref.y])
    out, shifts = img.copy(), []
    for c in (0, 2):
        st = detect_stars(img[c], max_stars=150, thresh=10)
        if len(st) < 15:
            shifts.append([0.0, 0.0])
            continue
        p = np.column_stack([st.x, st.y])
        d = np.hypot(p[:, None, 0] - rp[None, :, 0], p[:, None, 1] - rp[None, :, 1])
        j = d.argmin(1)
        ok = d[np.arange(len(p)), j] < max_shift
        if ok.sum() < 10:
            shifts.append([0.0, 0.0])
            continue
        dx, dy = np.median(p[ok] - rp[j[ok]], axis=0)
        if np.hypot(dx, dy) > 0.08:
            out[c] = ndi.shift(img[c], (-dy, -dx), order=3, mode="nearest")
        shifts.append([round(float(dx), 2), round(float(dy), 2)])
    return out, shifts


def aperture_fluxes(img: np.ndarray, xs, ys, r: float):
    C, H, W = img.shape
    ri = int(np.ceil(r * 2.2))
    yy, xx = np.mgrid[-ri : ri + 1, -ri : ri + 1]
    rr = np.hypot(yy, xx)
    ap, ann = rr <= r, (rr > r * 1.4) & (rr <= r * 2.2)
    out = []
    for x, y in zip(xs, ys):
        x, y = int(round(x)), int(round(y))
        if x < ri or y < ri or x >= W - ri or y >= H - ri:
            continue
        f = []
        for c in range(C):
            cut = img[c, y - ri : y + ri + 1, x - ri : x + ri + 1]
            f.append(cut[ap].sum() - np.median(cut[ann]) * ap.sum())
        out.append(f)
    return np.array(out)


def color_calibrate(img: np.ndarray, fwhm: float) -> tuple[np.ndarray, list[float]]:
    """Weißabgleich über mittlere Sternfarbe (Sterne im Feld mitteln sich zu ~neutral)."""
    L = luminance(img)
    st = detect_stars(L, max_stars=300, thresh=8)
    if len(st) < 10:
        return img, [1, 1, 1]
    fl = aperture_fluxes(img, st.x, st.y, max(2.0, fwhm * 1.2))
    fl = fl[(fl > 0).all(1)]
    if len(fl) < 10:
        return img, [1, 1, 1]
    lo, hi = np.percentile(fl.sum(1), [20, 98])
    fl = fl[(fl.sum(1) >= lo) & (fl.sum(1) <= hi)]
    gains = np.clip(np.median(fl[:, 1:2] / fl, axis=0), 0.5, 2.0)   # auf Grün normiert
    gains = gains / gains[1]
    return img * gains[:, None, None].astype(np.float32), [float(g) for g in gains]


NEUTRAL_PCT = 10          # Hintergrund-Neutralisierung: dunkelste 10 % (Staubfarbe NGC 7023 R−B ×1,8 gegenüber „dunkle Hälfte“)


def neutralize_background(img: np.ndarray) -> np.ndarray:
    """Farbstich des Himmels ausgleichen – gemessen am echten Himmel (dunkelste Teile), nicht an allem unter
    Median + σ: in Staub-/Nebelfeldern wäre das die braune bzw. rote Objektfarbe, die dabei grau würde."""
    L = luminance(img)
    low = dark_sky_mask(L, NEUTRAL_PCT)
    if low.sum() < 500:
        sig = 1.4826 * np.median(np.abs(L - np.median(L)))
        low = L < np.median(L) + sig
    bg = np.array([np.median(img[c][low]) if low.any() else np.median(img[c]) for c in range(img.shape[0])])
    return img - (bg - bg.mean())[:, None, None]


def remove_color_gradient(img: np.ndarray, degree: int = 1, grid: int = 12) -> np.ndarray:
    """Farbverlauf des Himmels (z. B. rötlich zu einer Seite: Dämmerung, Mond, Horizontlicht) entfernen, ohne die
    Helligkeit zu ändern: Polynom über die Farbabweichung (Kanal − Luminanz) der Himmelskacheln, ohne Mittelwert
    abgezogen (den globalen Stich gleicht neutralize_background aus). Nur auf Anforderung der Qualitätskontrolle –
    bei Nebelfeldern wäre ein Farbverlauf echt."""
    C, H, W = img.shape
    if C != 3:
        return img
    L = luminance(img)
    d = img - L[None]
    mask = _star_mask(L)
    sky = L <= np.median(L) + 1.4826 * np.median(np.abs(L - np.median(L)))
    ys, xs = np.linspace(0, H, grid + 1).astype(int), np.linspace(0, W, grid + 1).astype(int)
    pts, vals = [], []
    for i in range(grid):
        for j in range(grid):
            ok = sky[ys[i] : ys[i + 1], xs[j] : xs[j + 1]] & ~mask[ys[i] : ys[i + 1], xs[j] : xs[j + 1]]
            if ok.mean() < 0.3:
                continue
            pts.append(((ys[i] + ys[i + 1]) / H - 1, (xs[j] + xs[j + 1]) / W - 1))
            vals.append([np.median(d[c, ys[i] : ys[i + 1], xs[j] : xs[j + 1]][ok]) for c in range(C)])
    nterm = (degree + 1) * (degree + 2) // 2
    if len(pts) < nterm + 6:
        return img
    pts, vals = np.array(pts), np.array(vals)

    def basis(y, x):
        return np.stack([y**a * x**b for a in range(degree + 1) for b in range(degree + 1 - a)], axis=-1)

    A = basis(pts[:, 0], pts[:, 1])
    use = np.ones(len(pts), bool)
    for _ in range(3):                           # Kacheln mit Nebel/Galaxie (echte Farbe) verwerfen
        coef = np.linalg.lstsq(A[use], vals[use], rcond=None)[0]
        r = np.abs(vals - A @ coef).max(1)
        s = 1.4826 * np.median(np.abs(r[use] - np.median(r[use]))) + 1e-9
        new = r < np.median(r[use]) + 2.5 * s
        if new.sum() < nterm + 4 or (new == use).all():
            break
        use = new
    yy, xx = np.meshgrid(np.linspace(-1, 1, H, dtype=np.float32), np.linspace(-1, 1, W, dtype=np.float32), indexing="ij")
    model = basis(yy, xx) @ coef.astype(np.float32)            # (H,W,C)
    model -= model.reshape(-1, C).mean(0)
    return (img - np.moveaxis(model, -1, 0)).astype(np.float32)


def scnr(img: np.ndarray, amount: float) -> np.ndarray:
    if amount <= 0 or img.shape[0] != 3:
        return img
    r, g, b = img
    g2 = np.minimum(g, (r + b) / 2)
    out = img.copy()
    out[1] = g * (1 - amount) + g2 * amount
    return out


def deconvolve(L: np.ndarray, fwhm: float, iters: int) -> np.ndarray:
    sig = max(0.8, fwhm / 2.3548)
    r = int(np.ceil(sig * 3))
    yy, xx = np.mgrid[-r : r + 1, -r : r + 1]
    psf = np.exp(-(xx**2 + yy**2) / (2 * sig**2))
    psf /= psf.sum()
    flip = psf[::-1, ::-1]
    off = 8 * noise_sigma(L)
    Lp = np.clip(L - L.min() + off, 1e-6, None)
    est = Lp.copy()
    for _ in range(iters):
        conv = fftconvolve(est, psf, mode="same")
        est *= fftconvolve(Lp / np.maximum(conv, 1e-9), flip, mode="same")
    return est + (L.min() - off)


def mtf(x, m):
    return ((m - 1) * x) / ((2 * m - 1) * x - m + 1e-12)


def stretch(img: np.ndarray, target_bg: float, asinh_k: float, color_preserve: bool = False) -> np.ndarray:
    """asinh (Highlight-Kompression) + automatische Mitteltonanpassung mit verknüpften Schwarz-/Weißpunkten.

    Standard: Kurve je Farbkanal – helle Sternkerne laufen wie bei Stellina nach Weiß und bleiben klein; Farbe zeigen
    nur die Sternränder und flächiges Signal. color_preserve=True: alte farberhaltende Variante (Verhältnisse bleiben
    auch in Kernen) – ließ helle Sterne als große gesättigte rote/blaue Scheiben mit Ringen erscheinen (NGC 6888)."""
    if not color_preserve:
        return _stretch_channels(img, target_bg, asinh_k)
    L = luminance(img)
    med = np.median(L)
    sig = 1.4826 * np.median(np.abs(L - med)) + 1e-12
    black = med - 2.8 * sig
    white = np.percentile(L, 99.9)
    white = max(white, med + 10 * sig)
    x = (img - black) / (white - black)          # bewusst ohne Clipping: Farbe bleibt bis nach der Chroma-Glättung erhalten
    Lx = np.maximum(luminance(x), 1e-6)
    f = np.arcsinh(asinh_k * Lx) / np.arcsinh(asinh_k) / Lx
    y = x * f
    Ly = luminance(y)
    m0 = float(np.clip(np.median(Ly), 1e-5, 0.9))
    t = target_bg
    m = m0 * (t - 1) / (2 * t * m0 - t - m0)
    m = float(np.clip(m, 1e-4, 0.999))
    Ly = np.maximum(luminance(y), 1e-6)
    f2 = mtf(np.clip(Ly, 0, 1), m) / Ly
    f2 = np.where(Ly > 1, 1 / Ly, f2)
    out = y * f2
    mx = np.maximum(out.max(0), 1.0)
    return (out / mx).astype(np.float32)


def channel_curve(ref: np.ndarray, target_bg: float, asinh_k: float, white_pct: float = 99.9):
    """Kanalweise Kurve (asinh + Mittelton) mit verknüpften Schwarz-/Weißpunkten aus `ref`; als Funktion, damit
    Original und sternloses Bild exakt dieselbe Kurve bekommen. Sternkerne laufen nach Weiß und bleiben knackig.
    white_pct: Weißpunkt als Perzentil – alles darüber wird Weiß; die Qualitätskontrolle hebt ihn bei ausgebrannten Kernen."""
    L = luminance(ref)
    med = float(np.median(L))
    sig = 1.4826 * float(np.median(np.abs(L - med))) + 1e-12
    black = med - 2.8 * sig
    white = max(float(np.percentile(L, white_pct)), med + 10 * sig)
    y0 = np.arcsinh(asinh_k * np.clip((L[::3, ::3] - black) / (white - black), 0, None)) / np.arcsinh(asinh_k)
    m0 = float(np.clip(np.median(y0), 1e-5, 0.9))
    t = target_bg
    m = float(np.clip(m0 * (t - 1) / (2 * t * m0 - t - m0), 1e-4, 0.999))

    def curve(img):
        x = np.clip((img - black) / (white - black), 0, None)
        return mtf(np.clip(np.arcsinh(asinh_k * x) / np.arcsinh(asinh_k), 0, 1), m).astype(np.float32)
    return curve


def _stretch_channels(img: np.ndarray, target_bg: float, asinh_k: float, white_pct: float = 99.9) -> np.ndarray:
    return channel_curve(img, target_bg, asinh_k, white_pct)(img)


def _atrous(a: np.ndarray, scales: int):
    h = np.array([1, 4, 6, 4, 1], np.float32) / 16
    cur, details = a, []
    for j in range(scales):
        k = np.zeros(4 * 2**j + 1, np.float32)
        k[:: 2**j] = h
        sm = ndi.convolve1d(ndi.convolve1d(cur, k, axis=0, mode="mirror"), k, axis=1, mode="mirror")
        details.append(cur - sm)
        cur = sm
    return details, cur


def denoise(img: np.ndarray, strength: float, chroma_sigma: float, protect: np.ndarray) -> np.ndarray:
    """Wavelet-Entrauschen der Luminanz + Glättung der Chrominanz. protect: 0..1 (1 = unverändert)."""
    if strength <= 0 and chroma_sigma <= 0:
        return img
    L = luminance(img)
    chroma = img - L
    if chroma_sigma > 0:
        # Faint-Star-/Rauschfarbe: zusätzlich stark glätten, wo es dunkel ist (Sterne bleiben via protect erhalten)
        cs0 = chroma_sigma * max(1.0, img.shape[2] / 1500) * 1.8
        chroma = chroma * protect + np.stack([ndi.gaussian_filter(c, cs0) for c in chroma]) * (1 - protect)
        cs = chroma_sigma * max(1.0, img.shape[2] / 1500)
        cb = np.stack([ndi.gaussian_filter(c, cs) for c in chroma])
        chroma = chroma * protect + cb * (1 - protect)
    if strength > 0:
        details, resid = _atrous(L, 4)
        out = resid.copy()
        ks = (3.0, 2.2, 1.4, 0.8)
        for d, k in zip(details, ks):
            s = 1.4826 * np.median(np.abs(d - np.median(d))) + 1e-9
            thr = k * s * strength
            shr = np.sign(d) * np.maximum(np.abs(d) - thr, 0)
            out += d * protect + shr * (1 - protect)
        L = out
    return np.clip(L + chroma, 0, 1).astype(np.float32)


def denoise_linear(img: np.ndarray, strength: float) -> np.ndarray:
    """Rauschreduzierung im linearen Bild (vor dem Stretch) für signalschwache Stacks.
    Wavelet-Schwelle auf der Luminanz (feine Skalen) + leichte Chroma-Glättung; Sterne und
    helle Strukturen liegen weit über der Schwelle und bleiben erhalten. Additiv -> farbneutral."""
    if strength <= 0:
        return img
    L = luminance(img)
    details, resid = _atrous(L, 3)
    out = resid.copy()
    for d, k in zip(details, (3.0, 2.0, 1.2)):
        s = 1.4826 * np.median(np.abs(d - np.median(d))) + 1e-12
        thr = k * s * strength
        out += np.sign(d) * np.maximum(np.abs(d) - thr, 0)
    chroma = img - L
    cs = 0.8 + 1.2 * strength
    chroma = np.stack([ndi.gaussian_filter(c, cs) for c in chroma]) if img.shape[0] > 1 else chroma
    return (out + chroma).astype(np.float32)


def dark_sky_mask(L: np.ndarray, pct: float = 12.0, smooth: float = 8.0) -> np.ndarray:
    """Echter Himmel: dunkelste `pct` % der geglätteten, sternfreien Luminanz. Die dunkle Bildhälfte taugt dafür nicht –
    in Nebel- und Staubfeldern ist sie Staub/Nebel (NGC 7023: brauner Staub wurde als „Himmel“ grau gezogen)."""
    f = max(1, int(max(L.shape) // 700))                       # verkleinert rechnen (schnell), Ergebnis in voller Größe
    Ls = L[: L.shape[0] // f * f, : L.shape[1] // f * f].reshape(L.shape[0] // f, f, L.shape[1] // f, f).mean((1, 3)) if f > 1 else L
    Ls = ndi.gaussian_filter(ndi.grey_opening(Ls, size=5), smooth / f)
    m = Ls <= np.percentile(Ls, pct)
    if f > 1:
        m = np.repeat(np.repeat(m, f, 0), f, 1)
        m = np.pad(m, ((0, L.shape[0] - m.shape[0]), (0, L.shape[1] - m.shape[1])), mode="edge")
    return m & ~_star_mask(L, grow=2)


def denoise_multiscale(img: np.ndarray, strength: float, chroma: float = 1.0, cov: np.ndarray | None = None,
                       scales: int = 5) -> np.ndarray:
    """Lineares Entrauschen auf der CPU (Ersatz für KI-Entrauschen): à-trous-Wavelets auf Luminanz und Farbe getrennt.
    - Rauschen je Skala am echten Himmel gemessen (dunkelste Bildteile ohne Sterne) – nicht über das ganze Bild, wo
      Nebel/Galaxie die Schwelle hochtreiben, und nicht aus Nachbardifferenzen (Interpolation beim Registrieren macht das
      Rauschen korreliert, die feinste Skala wird sonst überschätzt).
    - Rauschen örtlich über die Abdeckung (σ ∝ 1/√Abdeckung, Stackränder) und über die Helligkeit (Photonenrauschen)
      skaliert.
    - Garrote-Schwelle statt weicher Schwelle: starke Koeffizienten (Sterne, Nebelkanten) bleiben fast unverändert,
      Sterne werden nicht kleiner/dunkler.
    - Farbe (Kanal − Luminanz) mit höheren Schwellen und bis zu 32 px: Farbrauschen erscheint als Flecken mittlerer Größe.
    Additiv, Mittelwert jeder Region bleibt erhalten (Residuum unverändert)."""
    if strength <= 0:
        return img
    C = img.shape[0]
    L = luminance(img)
    sky = dark_sky_mask(L, 25.0)
    if sky.sum() < 1000:
        sky = L <= np.percentile(L, 25)
    bg = float(np.median(L[sky]))
    sig_sky = 1.4826 * float(np.median(np.abs(L[sky] - bg))) + 1e-12
    # örtlicher Rauschfaktor: Abdeckung (weniger Bilder am Rand) und Photonenrauschen heller Flächen (σ² ∝ Himmel + Signal;
    # Himmel ≈ Rauschgrenze, daher grob: Faktor sqrt(1 + Signal/(Himmel-Abstand)) begrenzt)
    loc = np.ones(L.shape, np.float32)
    if cov is not None and cov.shape == L.shape:
        c = np.clip(cov / max(float(np.median(cov[sky])) if sky.any() else 1.0, 1e-3), 0.05, 1.0)
        loc *= (1.0 / np.sqrt(c)).astype(np.float32)
    sm = ndi.gaussian_filter(L, 4.0)
    loc *= np.sqrt(1.0 + np.clip((sm - bg) / (50 * sig_sky), 0, 8)).astype(np.float32)

    def shrink(d, thr, j):
        a2 = d * d
        out = np.where(a2 > thr * thr, d - thr * thr / np.where(d == 0, 1, d), 0.0).astype(np.float32)
        # Mehrskalen-Träger: um signifikante Koeffizienten (Sterne) bleibt die Ebene unverändert – sonst fielen die
        # negativen Ränder grober Ebenen weg und Sterne bekämen Fluss/Halo dazu
        sup = ndi.binary_dilation(np.abs(d) > 2.5 * thr, iterations=2 ** (j + 1) + 1)
        return np.where(sup, d, out)

    ks = (3.0, 2.4, 1.8, 1.3, 1.0, 0.8)
    details, resid = _atrous(L, scales)
    out = resid.copy()
    for j, d in enumerate(details):
        s = 1.4826 * float(np.median(np.abs(d[sky] - np.median(d[sky])))) + 1e-12
        out += shrink(d, ks[j] * strength * s * loc, j)
    if C == 1 or chroma <= 0:
        return np.repeat(out[None], C, 0).astype(np.float32) if C > 1 else out[None].astype(np.float32)
    res = []
    kc = (3.5, 3.0, 2.6, 2.2, 1.8, 1.5)
    for c in range(C):
        ch = img[c] - L
        dets, r = _atrous(ch, min(scales + 1, 6))
        o = r.copy()
        for j, d in enumerate(dets):
            s = 1.4826 * float(np.median(np.abs(d[sky] - np.median(d[sky])))) + 1e-12
            o += shrink(d, kc[j] * chroma * strength * s * loc, j)
        res.append(o)
    chroma_out = np.stack(res)
    chroma_out -= chroma_out.mean(0, keepdims=True)        # Summe der Farbanteile bleibt 0 (Luminanz unverändert)
    return (out[None] + chroma_out).astype(np.float32)


def shrink_stars(stars: np.ndarray, amount: float) -> np.ndarray:
    """Sternebene verkleinern: Grauwert-Erosion (Ränder nach innen) und etwas dunkler – wirkt nur auf Sterne."""
    er = np.stack([ndi.grey_erosion(c, size=(3, 3)) for c in stars])
    return (stars * (1 - amount) + er * amount).astype(np.float32)


def reduce_stars(img: np.ndarray, amount: float, fwhm: float) -> np.ndarray:
    if amount <= 0:
        return img
    L = luminance(img)
    st = detect_stars(L, max_stars=600, thresh=6)
    if len(st) == 0:
        return img
    mask = np.zeros(L.shape, np.float32)
    for x, y, f, fw in zip(st.x, st.y, st.flux, st.fwhm):
        mask[int(round(y)), int(round(x))] = 1
    rad = max(2, int(fwhm * 1.5))
    mask = ndi.gaussian_filter(ndi.maximum_filter(mask, size=2 * rad + 1), rad / 2)
    mask = np.clip(mask * 3, 0, 1) * amount
    eroded = np.stack([ndi.grey_erosion(c, size=(3, 3)) for c in img])
    eroded = np.stack([ndi.gaussian_filter(c, 0.8) for c in eroded])
    return img * (1 - mask) + eroded * mask


def local_contrast(img: np.ndarray, amount: float, sharpen: float) -> np.ndarray:
    L = luminance(img)
    out = L.copy()
    if amount > 0:
        big = ndi.gaussian_filter(L, max(img.shape[1:]) / 40)
        out = out + amount * 0.8 * (L - big) * smoothstep(L, 0.05, 0.4)
    if sharpen > 0:
        small = ndi.gaussian_filter(L, 1.6 * max(1.0, img.shape[2] / 2000))
        out = out + sharpen * 0.9 * (L - small) * smoothstep(L, 0.1, 0.4)
    ratio = np.clip(np.maximum(out, 1e-4) / np.maximum(L, 1e-4), 0.5, 2.0)
    return np.clip(img * ratio, 0, 1)


def tame_star_color(img: np.ndarray, fwhm: float, amount: float = 0.75, sat: np.ndarray | None = None) -> np.ndarray:
    """Farbsäume in den Halos heller Sterne (Dispersion, Farbquerfehler, je Kanal verschieden verschmiert)
    abschwächen: Chrominanz um helle Sterne herum Richtung Luminanz ziehen. Nebel bleiben unberührt,
    weil nur kompakte, sehr helle Kerne die Maske auslösen."""
    if amount <= 0 or img.shape[0] != 3:
        return img
    L = luminance(img)
    st = detect_stars(L, max_stars=300, thresh=8, max_fwhm=60)
    if len(st) < 5:
        return img
    med = float(np.median(st.fwhm))
    fmed = float(np.median(st.flux))
    yy, xx = np.indices(L.shape)
    halo = np.zeros(L.shape, np.float32)
    for x, y, fw, fl in zip(st.x, st.y, st.fwhm, st.flux):
        xi, yi = int(round(x)), int(round(y))
        # nur sternförmige, helle Objekte: Nebelkerne sind breiter als die typische Sternabbildung
        if fw > 1.6 * med or L[yi, xi] < 0.55:
            continue
        r = fw * float(np.clip(1.6 + 0.9 * np.log10(max(fl / fmed, 1.0)), 1.6, 4.0))   # helle Sterne: großer Halo
        ri = int(np.ceil(r)) + 1
        sl = (slice(max(0, yi - ri), yi + ri + 1), slice(max(0, xi - ri), xi + ri + 1))
        d = np.hypot(yy[sl] - y, xx[sl] - x)
        halo[sl] = np.maximum(halo[sl], np.clip((r - d) / (0.4 * r), 0, 1))
    if sat is not None and sat.any():
        # gesättigte Sterne: breite Kerne fallen durch den Formfilter, haben aber die stärksten Säume
        lab, n = ndi.label(sat)
        for i, (sy, sx) in enumerate(ndi.find_objects(lab), start=1):
            area = float((lab[sy, sx] == i).sum())
            y, x = (sy.start + sy.stop - 1) / 2, (sx.start + sx.stop - 1) / 2
            r = 3 * np.sqrt(area / np.pi) + 2.5 * fwhm
            ri = int(np.ceil(r)) + 1
            sl = (slice(max(0, int(y) - ri), int(y) + ri + 1), slice(max(0, int(x) - ri), int(x) + ri + 1))
            d = np.hypot(yy[sl] - y, xx[sl] - x)
            halo[sl] = np.maximum(halo[sl], np.clip((r - d) / (0.4 * r), 0, 1))
    w = halo * amount
    return L + (img - L) * (1 - w)


def neutral_display_background(img: np.ndarray) -> np.ndarray:
    """Restfarbstich des Himmels nach dem Stretch angleichen (Kanal-Mediane der dunkelsten Bildteile, siehe
    neutralize_background)."""
    L = luminance(img)
    sky = dark_sky_mask(L, NEUTRAL_PCT)
    if sky.sum() < 500:
        sky = L <= np.median(L)
    bg = np.array([np.median(c[sky]) for c in img])
    return np.clip(img - (bg - bg.mean())[:, None, None], 0, 1).astype(np.float32)


def darken_background(img: np.ndarray, level: float) -> np.ndarray:
    """Schwarzpunkt anheben, bis der Himmel bei `level` liegt (Stellina ~0,03). Anders als ein niedrigeres target_bg
    bleiben Nebel und Sterne hell – nur der Hintergrund (und mit ihm Restgradienten, Rauschen, Artefakte) wird dunkel."""
    L = luminance(img)
    bg = float(np.median(L[L <= np.median(L)]))
    if bg <= level:
        return img
    b = (bg - level) / (1 - level)
    # weicher Fuß statt hartem Abschneiden: Werte knapp über/unter dem Schwarzpunkt laufen sanft gegen 0, dunkle
    # Nebelbereiche behalten Struktur (Stellina: ~0,1 % Schwarz; hart abgeschnitten wären es mehrere Prozent)
    w = 0.8 * min(b, level)
    t = img - b
    y = np.where(t > w, t, np.where(t > -w, (t + w) ** 2 / (4 * w), 0.0))
    return np.clip(y / (1 - b), 0, 1).astype(np.float32)


def grade(img: np.ndarray, amount: float, vibrance: float = 0.0, large: float = 0.0) -> np.ndarray:
    """Endbearbeitung wie in Affinity/Photoshop nach GraXpert+StarNet: Strukturen von 4–64 px (Staubbänder,
    Spiralarme, Nebelfilamente) über Wavelet-Ebenen anheben – nur im Objekt, nicht im Himmel (Rauschen) und nicht in
    Sternen –, dazu Vibrance: blasse Farben stärker sättigen als bereits kräftige.
    large: zusätzlich Ebenen 64/128 px (Profis: LocalHistogramEqualization mit Skala ~150 px) für flächigen Staub und
    Dunkelwolken (NGC 7023: Staubkontrast ×1,5); im ganzen Bild außer hellen Sternen, weil Staub auch „Himmel“ ist."""
    L = luminance(img)
    # Objektmaske gegen den dunkelsten Himmel (10-%-Perzentil der geglätteten Luminanz): Median/Streuung taugen nicht,
    # wenn der Nebel das ganze Bild füllt (NGC 7000: Maske sonst 0,4 % des Bildes)
    Ls = ndi.gaussian_filter(L, 3.0)
    sky = float(np.percentile(Ls, 10))
    obj = smoothstep(Ls, sky + 0.012, sky + 0.10)
    stars = ndi.grey_dilation(L, size=5) > 0.75                       # helle Sternkerne + Rand
    obj = obj * (1 - ndi.gaussian_filter(stars.astype(np.float32), 2.0).clip(0, 1) * 0.8)
    details, _ = _atrous(L, 8 if large > 0 else 6)
    gains = (0.0, 0.0, 0.35, 0.6, 0.6, 0.4)                          # Skalen 1,2,4,8,16,32 px
    Ln = L + amount * sum(g * d for g, d in zip(gains, details) if g) * obj
    if large > 0:                                                    # 64 und 128 px
        nostar = 1 - ndi.gaussian_filter(stars.astype(np.float32), 2.0).clip(0, 1) * 0.8
        Ln = Ln + large * (0.35 * details[6] + 0.25 * details[7]) * nostar
    ratio = np.clip(np.maximum(Ln, 1e-4) / np.maximum(L, 1e-4), 0.6, 1.8)
    out = np.clip(img * ratio, 0, 1)
    if vibrance > 0:
        L2 = luminance(out)
        chroma = out - L2
        s = np.sqrt((chroma ** 2).sum(0)) / np.maximum(L2, 0.02)        # relative Sättigung
        k = 1 + vibrance * np.clip(1 - s / 0.6, 0, 1) * obj
        out = np.clip(L2 + chroma * k, 0, 1)
    return out.astype(np.float32)


def saturate(img: np.ndarray, sat: float) -> np.ndarray:
    L = luminance(img)
    # schwache Pixel (Rauschen, Sternchen am Limit) nicht überfärben, Sternkerne auch nicht
    w = smoothstep(L, 0.04, 0.22) * (1 - 0.5 * smoothstep(L, 0.6, 1.0))
    s = 1 + (sat - 1) * w
    return np.clip(L + (img - L) * s, 0, 1)


# ---------------------------------------------------------------------------- #
def auto_stretch(img: np.ndarray, target: float = 0.2) -> np.ndarray:
    """Einfacher verknüpfter Auto-Stretch ohne weitere Bearbeitung (Vorher-Bild)."""
    L = luminance(img)
    med = float(np.median(L))
    sig = 1.4826 * float(np.median(np.abs(L - med))) + 1e-12
    lo, hi = med - 2.8 * sig, max(float(np.percentile(L, 99.95)), med + 10 * sig)
    x = np.clip((img - lo) / (hi - lo), 0, 1)
    m0 = float(np.clip(np.median(luminance(x)), 1e-5, 0.9))
    m = float(np.clip(m0 * (target - 1) / (2 * target * m0 - target - m0), 1e-4, 0.999))
    return np.clip(mtf(x, m), 0, 1).astype(np.float32)


def process(stack: np.ndarray, cov: np.ndarray, params: dict, narrowband: bool = False, palette: str | None = None,
            log=lambda m: None, want_before: bool = False, crop: list | None = None, preview=None):
    """stack (C,H,W) linear, cov (H,H) 0..1. Gibt (rgb float32 (3,H,W) 0..1, Statistik).
    want_before: stats["before"] = derselbe Ausschnitt nur linear gestreckt (Vorher-Nachher-Vergleich).
    crop: fester Ausschnitt (y0, y1, x0, x1) statt aus der Abdeckung – stats["crop"] eines früheren Laufs
    (Stacking-Stufen haben so exakt den Ausschnitt des fertigen Bildes)."""
    p = dict(params)
    def show(a, linear=True):
        if preview:
            from .live import optional
            optional(preview, a, linear=linear)
    if crop:
        y0, y1, x0, x1 = crop
    else:
        y0, y1, x0, x1 = coverage_box(cov)
        m = max(8, int(0.015 * min(y1 - y0, x1 - x0)))   # Randzone (Vignettierung/Interpolationsränder) abschneiden
        if min(y1 - y0, x1 - x0) > 8 * m:
            y0, y1, x0, x1 = y0 + m, y1 - m, x0 + m, x1 - m
    img = stack[:, y0:y1, x0:x1].astype(np.float32)
    if img.shape[0] == 1:
        img = np.repeat(img, 3, axis=0)
        mono = True
    else:
        mono = False
    raw0 = img.copy() if want_before else None
    stats: dict = {"shape": list(img.shape[1:])}
    L0 = luminance(img)
    st = detect_stars(L0, max_stars=100, thresh=8)
    fwhm = float(np.median(st.fwhm[:50])) if len(st) else 3.0
    stats["fwhm_px"] = round(fwhm, 2)
    stats["stars"] = int(len(st))

    sat = saturation_mask(img) if not mono else None
    if not mono:
        log("Farbkanäle ausrichten")
        img, shifts = align_channels(img)
        if shifts:
            stats["channel_shift"] = shifts
    ai = p.get("_ai") or {}                          # GraXpert lief schon auf dem linearen Stack (pipeline.develop)
    if not ai.get("bge"):
        log("Hintergrundgradient entfernen")
        if p.get("background", "sky") == "poly":     # altes Verfahren (bis 0.55)
            img = remove_gradient(img, int(p["gradient_degree"]), edge_exclude=EDGE_EXCLUDE)
            img = remove_edge_glow(img)              # steiles Randglühen, das das Polynom nicht erfasst
            img = remove_gradient(img, 2)            # Restneigung nach der Randkorrektur
        else:                                        # Himmels-Stützpunkte + Abdeckungsstufe, schützt Nebel/Staub
            model, binfo = sky_background(img, min(3, int(p["gradient_degree"])), cov=cov[y0:y1, x0:x1])
            img = remove_edge_glow(img - model)
            stats["background"] = binfo
            del model
    elif float(ai.get("smoothing", 0.1)) >= 0.5:
        # GraXpert mit hoher Glättung (Staubprofile) lässt steiles Randglühen (Stellina ohne Darks) stehen
        log("Randglühen entfernen")
        img = remove_edge_glow(img)
    img, trimmed = (img, [0, 0, 0, 0]) if crop else trim_bad_edges(img)
    stats["crop"] = [y0 + trimmed[0], y1 - trimmed[1], x0 + trimmed[2], x1 - trimmed[3]]
    if sat is not None:
        sat = sat[trimmed[0] : sat.shape[0] - trimmed[1], trimmed[2] : sat.shape[1] - trimmed[3]]
    if raw0 is not None:
        stats["before"] = auto_stretch(raw0[:, trimmed[0] : raw0.shape[1] - trimmed[1], trimmed[2] : raw0.shape[2] - trimmed[3]])
        del raw0
    if any(trimmed):
        stats["edge_trim"] = trimmed
        stats["shape"] = list(img.shape[1:])
    pcc = p.get("_pcc") or {}
    if not mono and not narrowband and p.get("color_calibration", True):
        log("Farbkalibrierung")
        if p.get("wb_gains") and "fallback" not in pcc:           # fotometrisch (Gaia) bzw. vom Nutzer vorgegeben
            gains = [float(g) for g in p["wb_gains"]]
            img = img * np.array(gains, np.float32)[:, None, None]
            stats["color_cal"] = {"verfahren": "gaia" if pcc else "manuell", **{k: v for k, v in pcc.items() if k != "gains"}}
        else:
            img, gains = color_calibrate(img, fwhm)
            stats["color_cal"] = {"verfahren": "sternmittel", **({"grund": pcc["fallback"]} if pcc.get("fallback") else {})}
        stats["wb_gains"] = [round(g, 3) for g in gains]
    if sat is not None and sat.any():
        img = neutral_saturated_cores(img, sat, fwhm)
        stats["saturated_px"] = int(sat.sum())
    if not mono and p.get("color_gradient"):
        log("Farbverlauf des Himmels ausgleichen")
        img = remove_color_gradient(img, int(p["color_gradient"]))
        stats["color_gradient"] = int(p["color_gradient"])
    img = neutralize_background(img)
    show(img)

    if p["deconv"] > 0 and fwhm > 1.5:
        log("Entfaltung")
        L = luminance(img)
        Ld = deconvolve(L, fwhm, int(p["deconv"]))
        s = 1.4826 * np.median(np.abs(L - np.median(L)))
        blend = 0.6 * smoothstep(L - np.median(L), 6 * s, 40 * s)
        Lnew = L + (Ld - L) * blend
        img = img + (Lnew - L)[None]          # additiv, farbneutral

    if not ai.get("denoise"):
        # Ersatz für das KI-Entrauschen (Mehrskalen-Wavelets), gleiche Stärke wie dort (Profil ai_denoise, sonst 0,5)
        cpu = float(p.get("cpu_denoise", p.get("ai_denoise", 0.5)))
        if cpu > 0:
            log("Rauschreduzierung (linear, Wavelets)")
            cv = cov[y0:y1, x0:x1][trimmed[0]: y1 - y0 - trimmed[1], trimmed[2]: x1 - x0 - trimmed[3]]
            img = denoise_multiscale(img, max(cpu, float(p.get("linear_denoise", 0))), cov=cv)
            stats["denoise_cpu"] = round(max(cpu, float(p.get("linear_denoise", 0))), 2)
        elif p.get("linear_denoise", 0) > 0:
            log("Rauschreduzierung (linear)")
            img = denoise_linear(img, float(p["linear_denoise"]))
    # Sterne trennen (wie StarNet in Siril/PixInsight): Nebel/Galaxie werden ohne Sterne gestreckt und bearbeitet,
    # die Sterne kommen am Ende mit eigener Helligkeit zurück. Ohne Trennung: GHS auf das ganze Bild.
    sep = p.get("star_separation", "auto") not in ("off", None, False)
    stars_s = None
    if p.get("color_preserve"):
        log("Stretch")
        img = stretch(img, float(p["target_bg"]), float(p["asinh"]), True)
    elif sep:
        starless, _, how = starsep.separate(img, fwhm, str(p.get("star_separation", "auto")), log)
        stats["star_separation"] = how
        show(starless)
        log("Stretch (GHS)")
        T = starsep.Stretch(starless, float(p["target_bg"]), full=img)
        # Sterne mit der kanalweisen Kurve (knackig, Kerne weiß – wie Stellina), Nebel mit GHS
        Ts = channel_curve(img, float(p["target_bg"]), float(p["asinh"]), float(p.get("white_pct", 99.9)))
        stars_s = np.clip(Ts(img) - Ts(starless), 0, 1)
        img = T(starless)
        stats["ghs"] = T.steps
        del starless
    else:                                                  # ohne Trennung (Sternhaufen): knackige Kanalkurve
        log("Stretch")
        img = channel_curve(img, float(p["target_bg"]), float(p["asinh"]), float(p.get("white_pct", 99.9)))(img)
    show(img, linear=False)
    L = luminance(img)
    star = np.clip(ndi.gaussian_filter(ndi.binary_dilation(L > 0.7, iterations=2).astype(np.float32), 1.5) * 2, 0, 1)
    protect = np.maximum(smoothstep(L, 0.25, 0.7) * 0.6, star)
    log("Rauschreduzierung")
    img = denoise(img, float(p["denoise"]), float(p["chroma_denoise"]), protect)
    img = scnr(img, 0.0 if narrowband and palette != "SHO" else float(p["scnr"]))   # SHO: Grünstich entfernen (Hubble-Look)
    if p["star_reduction"] > 0 and stars_s is None:
        log("Sterne reduzieren")
        img = reduce_stars(img, float(p["star_reduction"]), fwhm)
    log("Kontrast & Farbe")
    img = local_contrast(img, float(p["contrast"]), float(p["sharpen"]))
    img = saturate(img, float(p["saturation"]) if not mono else 1.0)
    if p.get("grade", 0) > 0:
        log("Endbearbeitung")
        img = grade(img, float(p["grade"]), float(p.get("vibrance", 0.0)) if not mono else 0.0, float(p.get("grade_large", 0.0)))
    show(img, linear=False)
    if stars_s is not None:
        log("Sterne einsetzen")
        if p["star_reduction"] > 0:                          # Sterne kleiner: nur die Sternebene, Nebel unberührt
            stars_s = shrink_stars(stars_s, float(p["star_reduction"]))
        if not mono:
            stars_s = saturate(stars_s, float(p.get("star_saturation", 1.2)))
        img = starsep.screen(img, np.clip(stars_s * float(p.get("star_brightness", 0.85)), 0, 1))
        del stars_s
    elif not mono:
        img = tame_star_color(img, fwhm, float(p.get("star_color_tame", 0.75)), sat)
    if not mono:
        img = neutral_display_background(img)
    if p.get("bg_dark") is not None:
        img = darken_background(img, float(p["bg_dark"]))
    show(img, linear=False)
    L = luminance(img)
    stats["median"] = round(float(np.median(L)), 4)
    stats["p99"] = round(float(np.percentile(L, 99)), 4)
    stats["clipped_pct"] = round(float((img.max(0) >= 0.999).mean() * 100), 3)
    return np.clip(img, 0, 1).astype(np.float32), stats
