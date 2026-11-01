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
