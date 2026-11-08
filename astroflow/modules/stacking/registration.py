"""Bildregistrierung per Dreiecks-Matching (Translation + Rotation + leichter Maßstab), optional mit
Verzerrungskorrektur (Polynom 2. Ordnung, `fit_distortion`).

Verzerrung: Smart-Teleskope zentrieren zwischendurch neu (Stellina alle 1–3 min), das Feld wandert dabei über die Optik.
Die Verzeichnung sitzt fest in Optik/Sensor, derselbe Stern bildet sich in verschobenen Aufnahmen also leicht
unterschiedlich ab – eine Ähnlichkeitstransformation lässt am Rand Restfehler (Sterne im Stack breiter/länglicher).
Der Unterschied zweier gegeneinander verschobener Verzeichnungen ist in erster Näherung quadratisch → Polynom
2. Ordnung Referenz → Aufnahme über alle zugeordneten Sterne. Übernommen nur, wenn die Sterne das Überlappfeld
abdecken (jedes Viertel, jede Ecke) und der Restfehler in der Kreuzvalidierung deutlich sinkt; sonst bleibt es bei der
Ähnlichkeit (mit Grund).
"""
from __future__ import annotations

import os
from itertools import combinations

import numpy as np
from scipy import ndimage as ndi
from scipy.spatial import cKDTree


def _triangles(pts: np.ndarray, k: int = 7):
    n = len(pts)
    k = min(k, n - 1)
    _, idx = cKDTree(pts).query(pts, k=k + 1)
    tris = set()
    for i in range(n):
        for a, b in combinations(idx[i, 1:], 2):
            tris.add(tuple(sorted((i, int(a), int(b)))))
    tris = np.array(sorted(tris))
    P = pts[tris]
    d = np.stack([
        np.linalg.norm(P[:, 1] - P[:, 2], axis=1),
        np.linalg.norm(P[:, 0] - P[:, 2], axis=1),
        np.linalg.norm(P[:, 0] - P[:, 1], axis=1),
    ], axis=1)
    order = np.argsort(d, axis=1)
    ds = np.take_along_axis(d, order, axis=1)
    tr = np.take_along_axis(tris, order, axis=1)
    keep = (ds[:, 2] > 1e-6) & ((ds[:, 1] - ds[:, 0]) > 0.03 * ds[:, 2]) & ((ds[:, 2] - ds[:, 1]) > 0.03 * ds[:, 2])
    inv = np.stack([ds[:, 0] / ds[:, 2], ds[:, 1] / ds[:, 2]], axis=1)
    return tr[keep], inv[keep]


def _similarity(src: np.ndarray, dst: np.ndarray) -> np.ndarray:
    mu_s, mu_d = src.mean(0), dst.mean(0)
    s0, d0 = src - mu_s, dst - mu_d
    U, S, Vt = np.linalg.svd(d0.T @ s0 / len(src))
    D = np.diag([1.0, np.sign(np.linalg.det(U) * np.linalg.det(Vt)) or 1.0])
    R = U @ D @ Vt
    var_s = (s0**2).sum() / len(src)
    sc = float(np.trace(np.diag(S) @ D) / var_s) if var_s > 0 else 1.0
    M = np.eye(3)
    M[:2, :2] = sc * R
    M[:2, 2] = mu_d - sc * R @ mu_s
    return M


def _apply(M: np.ndarray, p: np.ndarray) -> np.ndarray:
    return p @ M[:2, :2].T + M[:2, 2]


def find_transform(ref_xy: np.ndarray, tgt_xy: np.ndarray, tol: float = 2.5, max_stars: int = 80):
    """Gibt (M tgt->ref, Index-Paare (tgt, ref), rms) oder None zurück."""
    ref, tgt = ref_xy[:max_stars], tgt_xy[:max_stars]
    if len(ref) < 5 or len(tgt) < 5:
        return None
    tr_r, inv_r = _triangles(ref)
    tr_t, inv_t = _triangles(tgt)
    if len(tr_r) == 0 or len(tr_t) == 0:
        return None
    dist, idx = cKDTree(inv_r).query(inv_t, k=3, distance_upper_bound=0.01)
    cands = [(dist[i, j], i, idx[i, j]) for i in range(len(inv_t)) for j in range(3) if np.isfinite(dist[i, j])]
    if not cands:
        return None
    cands.sort()
    rtree = cKDTree(ref)
    best, best_n = None, 0
    for _, ti, ri in cands[:400]:
        M = _similarity(tgt[tr_t[ti]], ref[tr_r[ri]])
        sc = np.hypot(M[0, 0], M[1, 0])
        if not 0.9 < sc < 1.1:
            continue
        d, _ = rtree.query(_apply(M, tgt), distance_upper_bound=tol)
        n = int(np.isfinite(d).sum())
        if n > best_n:
            best, best_n = M, n
            if n >= 0.6 * min(len(ref), len(tgt)):
                break
    if best is None or best_n < max(5, 0.15 * min(len(ref), len(tgt))):
        return None
    M = best
    for t in (tol, 1.5, 1.0):
        d, j = rtree.query(_apply(M, tgt), distance_upper_bound=t)
        sel = np.isfinite(d)
        if sel.sum() < 4:
            return None
        M = _similarity(tgt[sel], ref[j[sel]])
    d, j = rtree.query(_apply(M, tgt), distance_upper_bound=1.5)
    sel = np.isfinite(d)
    pairs = np.stack([np.nonzero(sel)[0], j[sel]], axis=1)
    return M, pairs, float(np.sqrt(np.mean(d[sel] ** 2)))


# ------------------------------------------------------------ Verzerrung ---
MODES = ("off", "auto")  # Projektoption `distortion`: off = nur Ähnlichkeit (Standard), auto = Polynom, wenn es trägt
POLY_TOL = 2.5          # px: Zuordnung aller Sterne über die Ähnlichkeit (Ränder weichen wegen der Verzeichnung ab)
POLY_MIN_PAIRS = 30     # mindestens so viele zugeordnete Sterne (nach Ausreißern)
POLY_MIN_QUAD = 5       # … davon je Viertel des Überlappfelds
POLY_MIN_CORNER = 2     # … und je Eckfeld (3×3-Raster) – das Polynom soll in den Ecken nicht frei extrapolieren
POLY_MIN_GAIN = 0.10    # Restfehler (Kreuzvalidierung) muss um mindestens 10 % sinken …
POLY_MIN_GAIN_PX = 0.02 # … und um mindestens 0,02 px
POLY_MAX_CORR = 4.0     # px: größte Abweichung Polynom ↔ Ähnlichkeit im Überlappfeld (mehr = Fehlanpassung)
POLY_FOLDS = 5
STRIP_ROWS = 256        # Zeilen je Streifen beim Polynom-Warp auf der CPU (Koordinaten in float64 bleiben klein)


def distortion_mode(value=None) -> str:
    """Projektoption `distortion` (None = Standard aus ASTROFLOW_DISTORTION, sonst aus) -> "auto" | "off"."""
    v = str(value or os.environ.get("ASTROFLOW_DISTORTION", "") or "off").strip().lower()
    v = {"1": "auto", "on": "auto", "true": "auto", "ein": "auto", "0": "off", "false": "off", "aus": "off"}.get(v, v)
    return v if v in MODES else "off"


def summary(meta: dict) -> dict | None:
    """Zusammenfassung der Verzerrungskorrektur eines Stacks (state.json → meta["reg"], nur gestackte Aufnahmen):
    frames geprüft, poly übernommen, pairs Median Sternpaare, rms_before = Median Restfehler mit Ähnlichkeit,
    rms_after = Median des wirksamen Restfehlers (Polynom, wo übernommen, sonst Ähnlichkeit) – beides aus der
    Kreuzvalidierung, über dieselben Aufnahmen; max_corr größte Korrektur (px); why = Gründe ohne Polynom."""
    reg = meta.get("reg") or {}
    rows = [reg[f] for f in meta.get("frames", {}) if f in reg]
    if not rows:
        return None
    med = lambda v: round(float(np.median(v)), 3) if len(v) else None
    poly = [r for r in rows if r.get("poly")]
    fit = [r for r in rows if "cv0" in r]
    why: dict = {}
    for r in rows:
        if not r.get("poly"):
            why[r.get("why") or "?"] = why.get(r.get("why") or "?", 0) + 1
    return {"frames": len(rows), "poly": len(poly), "pairs": med([r["n"] for r in rows]),
            "rms_before": med([r["cv0"] for r in fit]),
            "rms_after": med([r["cv1"] if r.get("poly") else r["cv0"] for r in fit]),
            "max_corr": round(float(max(r.get("k", 0) for r in poly)), 2) if poly else None, "why": why}


def _terms(u: np.ndarray, v: np.ndarray) -> np.ndarray:
    return np.stack([np.ones_like(u), u, v, u * u, u * v, v * v], axis=-1)


def poly_eval(P: dict, x, y) -> tuple[np.ndarray, np.ndarray]:
    """Polynom Referenz-xy -> Aufnahme-xy. P = {"c": (2,6) Koeffizienten für x und y, "ctr": (cx, cy), "s": Maßstab}
    über [1, u, v, u², uv, v²] mit u = (x − cx)/s, v = (y − cy)/s."""
    c = np.asarray(P["c"], float)
    T = _terms((np.asarray(x, float) - P["ctr"][0]) / P["s"], (np.asarray(y, float) - P["ctr"][1]) / P["s"])
    return T @ c[0], T @ c[1]


def _fit_poly(ref: np.ndarray, tgt: np.ndarray, ctr, s) -> np.ndarray:
    T = _terms((ref[:, 0] - ctr[0]) / s, (ref[:, 1] - ctr[1]) / s)
    return np.linalg.lstsq(T, tgt, rcond=None)[0].T           # (2, 6)


def _pred_poly(c: np.ndarray, ref: np.ndarray, ctr, s) -> np.ndarray:
    return _terms((ref[:, 0] - ctr[0]) / s, (ref[:, 1] - ctr[1]) / s) @ c.T


def _rms(d: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.sum(d * d, axis=1)))) if len(d) else float("nan")


def _cv(ref: np.ndarray, tgt: np.ndarray, fit, pred, folds: int = POLY_FOLDS) -> float:
    """Restfehler aus k-facher Kreuzvalidierung: jeder Stern wird von einer Anpassung ohne ihn vorhergesagt –
    so gewinnt das Polynom (12 statt 4 Parameter) nicht schon durch Anpassung an das Messrauschen."""
    idx = np.arange(len(ref)) % folds
    d = np.empty_like(tgt)
    for k in range(folds):
        te = idx == k
        d[te] = pred(fit(ref[~te], tgt[~te]), ref[te]) - tgt[te]
    return _rms(d)


def match_all(ref_xy: np.ndarray, tgt_xy: np.ndarray, M: np.ndarray, tol: float = POLY_TOL) -> np.ndarray:
    """Alle Sterne der Aufnahme über M (Aufnahme→Referenz) zuordnen, eindeutig (je Referenzstern der nächste).
    Gibt Index-Paare (tgt, ref) zurück."""
    if len(ref_xy) == 0 or len(tgt_xy) == 0:
        return np.zeros((0, 2), int)
    d, j = cKDTree(ref_xy).query(_apply(M, tgt_xy), distance_upper_bound=tol)
    ok = np.isfinite(d)
    ti, rj, dd = np.nonzero(ok)[0], j[ok], d[ok]
    o = np.argsort(dd, kind="stable")
    _, first = np.unique(rj[o], return_index=True)
    keep = np.sort(o[first])
    return np.stack([ti[keep], rj[keep]], axis=1)


def overlap_box(M: np.ndarray, shape: tuple[int, int]) -> tuple[float, float, float, float]:
    """Begrenzungsrechteck (x0, y0, x1, y1) des Überlapps Aufnahme ∩ Referenz in Referenzpixeln."""
    H, W = shape
    c = _apply(M, np.array([[0, 0], [W - 1, 0], [0, H - 1], [W - 1, H - 1]], float))
    return (max(0.0, float(c[:, 0].min())), max(0.0, float(c[:, 1].min())),
            min(W - 1.0, float(c[:, 0].max())), min(H - 1.0, float(c[:, 1].max())))


def fit_distortion(ref_xy: np.ndarray, tgt_xy: np.ndarray, M: np.ndarray, shape: tuple[int, int]):
    """Polynom 2. Ordnung Referenz → Aufnahme aus allen zugeordneten Sternen (nicht nur den Dreiecks-Treffern).
    shape = (H, W) des Referenzbilds (native Pixel). Gibt (P oder None, info) zurück; info = {n Sternpaare,
    r0/r1 Restfehler Ähnlichkeit/Polynom (px, angepasst), cv0/cv1 dieselben aus der Kreuzvalidierung, quad/corner
    kleinste Sternzahl je Viertel/Ecke, k größte Korrektur (px), why Grund, wenn nicht übernommen}. P: `poly_eval`."""
    info: dict = {"n": 0}
    ref_xy, tgt_xy = np.asarray(ref_xy, float).reshape(-1, 2), np.asarray(tgt_xy, float).reshape(-1, 2)
    pairs = match_all(ref_xy, tgt_xy, M)
    if len(pairs) < POLY_MIN_PAIRS:
        info.update(n=int(len(pairs)), why="zu wenige Sterne")
        return None, info
    H, W = shape
    ctr, s = ((W - 1) / 2, (H - 1) / 2), 0.5 * float(np.hypot(W, H))
    r, t = ref_xy[pairs[:, 1]], tgt_xy[pairs[:, 0]]
    sim_fit, sim_pred = _similarity, _apply
    pol_fit = lambda a, b: _fit_poly(a, b, ctr, s)
    pol_pred = lambda c, a: _pred_poly(c, a, ctr, s)
    # Ausreißer (Doppelsterne, Fehlzuordnungen) robust entfernen – am Polynom, damit Randsterne nicht wegen der
    # Verzeichnung selbst herausfallen
    use = np.ones(len(r), bool)
    for _ in range(3):
        c = pol_fit(r[use], t[use])
        e = np.linalg.norm(pol_pred(c, r) - t, axis=1)
        new = e <= max(3.0 * 1.4826 * float(np.median(e[use])), 0.3)
        if np.array_equal(new, use) or new.sum() < POLY_MIN_PAIRS:
            break
        use = new
    r, t = r[use], t[use]
    info["n"] = int(len(r))
    S0, c1 = sim_fit(r, t), pol_fit(r, t)
    info.update(r0=round(_rms(sim_pred(S0, r) - t), 3), r1=round(_rms(pol_pred(c1, r) - t), 3),
                cv0=round(_cv(r, t, sim_fit, sim_pred), 3), cv1=round(_cv(r, t, pol_fit, pol_pred), 3))
    # Abdeckung des Überlappfelds: jedes Viertel und jede Ecke (3×3-Raster) braucht Sterne
    x0, y0, x1, y1 = overlap_box(M, shape)
    if x1 - x0 < 0.3 * W or y1 - y0 < 0.3 * H:
        info["why"] = "Überlapp zu klein"
        return None, info
    fx, fy = (r[:, 0] - x0) / (x1 - x0), (r[:, 1] - y0) / (y1 - y0)
    quads = [int(np.sum(((fx < .5) == a) & ((fy < .5) == b))) for a in (True, False) for b in (True, False)]
    corners = [int(np.sum(((fx < 1 / 3) if a else (fx > 2 / 3)) & ((fy < 1 / 3) if b else (fy > 2 / 3))))
               for a in (True, False) for b in (True, False)]
    info.update(quad=min(quads), corner=min(corners))
    if min(quads) < POLY_MIN_QUAD:
        info["why"] = "Bildviertel zu dünn besetzt"
        return None, info
    if min(corners) < POLY_MIN_CORNER:
        info["why"] = "Ecken nicht abgedeckt"
        return None, info
    # größte Korrektur gegenüber der Ähnlichkeit im Überlappfeld (auch dort, wo keine Sterne liegen)
    gx, gy = np.meshgrid(np.linspace(x0, x1, 9), np.linspace(y0, y1, 9))
    g = np.c_[gx.ravel(), gy.ravel()]
    info["k"] = round(float(np.linalg.norm(pol_pred(c1, g) - sim_pred(S0, g), axis=1).max()), 2)
    if info["k"] > POLY_MAX_CORR:
        info["why"] = "Korrektur unplausibel groß"
        return None, info
    if not (info["cv1"] <= (1 - POLY_MIN_GAIN) * info["cv0"] and info["cv0"] - info["cv1"] >= POLY_MIN_GAIN_PX):
        info["why"] = "Restfehler sinkt nicht deutlich"
        return None, info
    return {"c": c1.tolist(), "ctr": [float(ctr[0]), float(ctr[1])], "s": s}, info


def shift_poly(P: dict, d) -> dict:
    """Polynom für eine zusätzliche Verschiebung d (Referenzpixel) nach der Registrierung (Kometen-Stack):
    Referenzpunkt x gehört dann zu x − d."""
    return {**P, "ctr": [P["ctr"][0] + float(d[0]), P["ctr"][1] + float(d[1])]}


def poly_out(P: dict, scale: int) -> tuple[np.ndarray, tuple[float, float], float]:
    """Polynom auf Ausgabepixel (Referenzraster ×scale) umgerechnet: (Koeffizienten, Mitte, Maßstab) – x_ref =
    (x_out + 0.5)/scale − 0.5 ist affin, also bleibt es dasselbe Polynom mit verschobener Mitte und Maßstab."""
    return (np.asarray(P["c"], float), ((P["ctr"][0] + 0.5) * scale - 0.5, (P["ctr"][1] + 0.5) * scale - 0.5),
            float(P["s"]) * scale)


def _poly_coords(c, ctr, s, ys: slice, Wo: int) -> tuple[np.ndarray, np.ndarray]:
    """Quellkoordinaten (sy, sx) der Ausgabezeilen ys."""
    v = (np.arange(ys.start, ys.stop, dtype=float)[:, None] - ctr[1]) / s
    u = (np.arange(Wo, dtype=float)[None, :] - ctr[0]) / s
    u, v = np.broadcast_arrays(u, v)
    T = _terms(u, v)
    return T @ c[1], T @ c[0]


def warp(img: np.ndarray, M: np.ndarray, out_shape: tuple[int, int], scale: int = 1, order: int = 3, gpu=None,
         poly: dict | None = None):
    """img (C,H,W) in Zielframe -> Referenzraster (optional x`scale` überabgetastet).
    Gibt (warped, valid_mask) zurück. `gpu` (gpu.Engine): Interpolation auf der Grafikkarte, bei Fehlern CPU.
    `poly` (aus `fit_distortion`): Abbildung Referenz → Aufnahme als Polynom statt über M (Verzerrungskorrektur)."""
    if poly is not None:
        return _warp_poly(img, poly, out_shape, scale, order, gpu)
    Ho, Wo = out_shape
    S = np.array([[1 / scale, 0, 0.5 / scale - 0.5], [0, 1 / scale, 0.5 / scale - 0.5], [0, 0, 1]])
    A = np.linalg.inv(M) @ S                       # Ausgabe-xy -> Quell-xy
    mat = np.array([[A[1, 1], A[1, 0]], [A[0, 1], A[0, 0]]])
    off = [A[1, 2], A[0, 2]]
    out = None
    if gpu is not None and not gpu.broken and order == 3:
        try:
            out = gpu.warp(img, mat, off, (Ho, Wo))
        except Exception as e:
            gpu.fail(e)
    if out is None:
        out = np.empty((img.shape[0], Ho, Wo), np.float32)
        for c in range(img.shape[0]):
            out[c] = ndi.affine_transform(img[c], mat, offset=off, output_shape=(Ho, Wo), order=order,
                                          mode="constant", cval=0.0, prefilter=order > 1)
    # gültig = Quellkoordinate liegt mit Sicherheitsabstand im Quellbild (analytisch statt zweitem Warp + Erosion)
    H, W = img.shape[1:]
    m = 3.0 + (1.0 if order > 1 else 0.0)            # 2 px Rand wie zuvor die Erosion, +1 für den kubischen Kern
    yo = np.arange(Ho, dtype=np.float32)[:, None]
    xo = np.arange(Wo, dtype=np.float32)[None, :]
    sy = mat[0, 0] * yo + mat[0, 1] * xo + off[0]
    sx = mat[1, 0] * yo + mat[1, 1] * xo + off[1]
    valid = (sy >= m - 1) & (sy <= H - m) & (sx >= m - 1) & (sx <= W - m)
    return out, valid


def _warp_poly(img: np.ndarray, P: dict, out_shape: tuple[int, int], scale: int, order: int, gpu):
    """Wie `warp`, Quellkoordinate je Ausgabepixel aus dem Polynom. CPU: Vorfilter einmal je Kanal (wie
    affine_transform mit mode="constant": Spiegelrand, außerhalb des Bildes 0), dann Auswertung in Zeilenstreifen."""
    Ho, Wo = out_shape
    C, H, W = img.shape
    c, ctr, s = poly_out(P, scale)
    m = 3.0 + (1.0 if order > 1 else 0.0)
    out = None
    if gpu is not None and not gpu.broken and order == 3:
        try:
            out = gpu.warp_poly(img, c, ctr, s, (Ho, Wo))
        except Exception as e:
            gpu.fail(e)
    coef = None
    if out is None:
        out = np.empty((C, Ho, Wo), np.float32)
        coef = [ndi.spline_filter(img[k], order=order, mode="mirror") if order > 1 else img[k] for k in range(C)]
    valid = np.empty((Ho, Wo), bool)
    for y in range(0, Ho, STRIP_ROWS):
        ys = slice(y, min(Ho, y + STRIP_ROWS))
        sy, sx = _poly_coords(c, ctr, s, ys, Wo)
        valid[ys] = (sy >= m - 1) & (sy <= H - m) & (sx >= m - 1) & (sx <= W - m)
        if coef is not None:
            xy = np.stack([sy, sx])
            for k in range(C):
                out[k, ys] = ndi.map_coordinates(coef[k], xy, order=order, mode="constant", cval=0.0, prefilter=False)
    return out, valid
