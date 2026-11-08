"""Bildregistrierung per Dreiecks-Matching (Translation + Rotation + leichter Maßstab)."""
from __future__ import annotations

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


def find_transform(ref_xy: np.ndarray, tgt_xy: np.ndarray, tol: float = 2.5, max_stars: int = 80,
                   info: dict | None = None):
    """Gibt (M tgt->ref, Index-Paare (tgt, ref), rms) oder None zurück.
    info (optional, nur für die Sternmuster-Ansicht): erhält Startdreieck und Zählwerte – ändert das Ergebnis nicht."""
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
    best, best_n, seed = None, 0, None
    for _, ti, ri in cands[:400]:
        M = _similarity(tgt[tr_t[ti]], ref[tr_r[ri]])
        sc = np.hypot(M[0, 0], M[1, 0])
        if not 0.9 < sc < 1.1:
            continue
        d, _ = rtree.query(_apply(M, tgt), distance_upper_bound=tol)
        n = int(np.isfinite(d).sum())
        if n > best_n:
            best, best_n, seed = M, n, (ti, ri)
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
    if info is not None:
        info.update(seed_tgt=[int(v) for v in tr_t[seed[0]]], seed_ref=[int(v) for v in tr_r[seed[1]]],
                    tris_ref=len(tr_r), tris_tgt=len(tr_t), cands=len(cands))
    return M, pairs, float(np.sqrt(np.mean(d[sel] ** 2)))


PATTERN_STARS = 36       # Sterne je Seite in der Sternmuster-Ansicht (hellste zuerst, dazu das Startdreieck)


def _mesh(pts: np.ndarray) -> list[tuple[int, int]]:
    """Feines Netz für die Anzeige: Delaunay-Kanten ohne lange Randkanten."""
    if len(pts) < 3:
        return []
    from scipy.spatial import Delaunay
    try:
        tri = Delaunay(pts)
    except Exception:
        return []
    edges = set()
    for a, b, c in tri.simplices:
        for i, j in ((a, b), (b, c), (a, c)):
            edges.add((int(min(i, j)), int(max(i, j))))
    edges = sorted(edges)
    ln = np.array([np.hypot(*(pts[i] - pts[j])) for i, j in edges])
    lim = 2.5 * float(np.median(ln))
    return [e for e, l in zip(edges, ln) if l <= lim]


def pattern(ref_xy: np.ndarray, tgt_xy: np.ndarray, M: np.ndarray, pairs: np.ndarray, info: dict,
            shape: tuple[int, int], rms: float | None = None) -> dict:
    """Kompakte Daten für die Sternmuster-Ansicht (wenige KB): Sterne beider Aufnahmen in Rohbild-Pixeln
    (ganzzahlig), Netzkanten je Seite, welche Kanten der Aufnahme nach der Transformation auf einer Kante der
    Referenz liegen, das tatsächlich gefundene Startdreieck und die Transformation."""
    def pick(n, must):
        keep = list(range(min(n, PATTERN_STARS)))
        return keep + [i for i in must if i not in keep and i < n]
    ri = pick(len(ref_xy), info.get('seed_ref', []))
    ti = pick(len(tgt_xy), info.get('seed_tgt', []))
    rpos, tpos = {o: k for k, o in enumerate(ri)}, {o: k for k, o in enumerate(ti)}
    rp, tp = ref_xy[ri], tgt_xy[ti]
    redges, tedges = _mesh(rp), _mesh(tp)
    match = {}                                       # Index Aufnahme (gezeigt) -> Index Referenz (gezeigt)
    for t, r in pairs:
        if int(t) in tpos and int(r) in rpos:
            match[tpos[int(t)]] = rpos[int(r)]
    rset = set(redges)
    tl = []
    for a, b in tedges:
        ra, rb = match.get(a), match.get(b)
        ok = ra is not None and rb is not None and (min(ra, rb), max(ra, rb)) in rset
        tl.append([a, b, 1 if ok else 0])
    sc = float(np.hypot(M[0, 0], M[1, 0]))
    H, W = shape
    return {
        'v': 1, 'w': int(W), 'h': int(H),
        'ref': [[int(round(x)), int(round(y))] for x, y in rp],
        'tgt': [[int(round(x)), int(round(y))] for x, y in tp],
        'ref_edges': [list(e) for e in redges], 'tgt_edges': tl,
        'pairs': [[t, r] for t, r in sorted(match.items())],
        'seed_ref': [rpos[i] for i in info.get('seed_ref', []) if i in rpos],
        'seed_tgt': [tpos[i] for i in info.get('seed_tgt', []) if i in tpos],
        'm': [round(float(v), 5) for v in (M[0, 0], M[0, 1], M[0, 2], M[1, 0], M[1, 1], M[1, 2])],
        'rot': round(float(np.degrees(np.arctan2(M[1, 0], M[0, 0]))), 3), 'scale': round(sc, 5),
        'shift': [round(float(M[0, 2]), 1), round(float(M[1, 2]), 1)],
        'rms': None if rms is None else round(float(rms), 3), 'n_pairs': int(len(pairs)),
        'n_ref': int(len(ref_xy)), 'n_tgt': int(len(tgt_xy)),
        'tris_ref': info.get('tris_ref'), 'tris_tgt': info.get('tris_tgt'), 'cands': info.get('cands'),
    }


def warp(img: np.ndarray, M: np.ndarray, out_shape: tuple[int, int], scale: int = 1, order: int = 3, gpu=None):
    """img (C,H,W) in Zielframe -> Referenzraster (optional x`scale` überabgetastet).
    Gibt (warped, valid_mask) zurück. `gpu` (gpu.Engine): Interpolation auf der Grafikkarte, bei Fehlern CPU."""
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
