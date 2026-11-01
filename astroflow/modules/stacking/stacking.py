"""Inkrementelles, gewichtetes Sigma-Clipping-Stacking mit persistentem Zustand.

Der Zustand (Summen der Gewichte, gewichteten Werte und Quadrate) wird gespeichert.
Neue Aufnahmen – auch Nächte später – lassen sich dadurch ohne Neuberechnung ergänzen.
"""
from __future__ import annotations

import json
import shutil
import warnings
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np


def clip_batch(X: np.ndarray, V: np.ndarray, kappa_lo: float = 3.0, kappa_hi: float = 3.0) -> np.ndarray:
    """X (B,C,H,W), V (B,H,W) gültig -> Keep-Maske (B,C,H,W)."""
    B = X.shape[0]
    valid = np.broadcast_to(V[:, None], X.shape)
    if B < 3:
        return valid.copy()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        return _clip(X, valid, kappa_lo, kappa_hi)


def _masked_median(X: np.ndarray, valid: np.ndarray) -> np.ndarray:
    """Median über Achse 0 nur gültiger Werte. Schnell: np.median für voll gültige Pixel,
    nanmedian nur für die (wenigen) Randpixel mit teilweiser Abdeckung."""
    med = np.median(X, axis=0)
    part = ~valid.all(0)
    if part.any():
        idx = np.nonzero(part)
        sub = np.where(valid[(slice(None), *idx)], X[(slice(None), *idx)], np.nan)
        med[idx] = np.nanmedian(sub, axis=0)
    return med


def _clip(X, valid, kappa_lo, kappa_hi):
    ref0 = _masked_median(X, valid)
    ref = ref0
    keep = valid
    sd = None
    for _ in range(2):
        dev = X - ref
        if sd is None:
            # Streuung nur einmal aus allen gültigen Werten schätzen: aus bereits beschnittenen Werten neu
            # geschätzt wird sie zu klein, und bei großen Stapeln werden dann zu viele normale Pixel verworfen
            cnt = keep.sum(0)
            sd = np.sqrt(np.where(keep, dev * dev, 0).sum(0) / np.maximum(cnt, 1))
            has = cnt > 0
            floor = 0.5 * float(np.median(sd[has])) if has.any() else 0.0
            sd = np.maximum(sd, floor)
        keep = valid & (dev <= kappa_hi * sd) & (dev >= -kappa_lo * sd)
        cnt = keep.sum(0)
        ref = np.where(cnt > 0, np.where(keep, X, 0).sum(0) / np.maximum(cnt, 1), ref0)
    return keep


class StackState:
    def __init__(self, shape: tuple[int, int, int]):
        C, H, W = shape
        self.shape = shape
        self.sw = np.zeros((C, H, W), np.float32)
        self.swx = np.zeros((C, H, W), np.float32)
        self.swx2 = np.zeros((C, H, W), np.float32)
        self.meta: dict = {"frames": {}, "ref_fid": None, "ref_stars": None, "ref_bg": None, "scale": 1}
        self.snaps: dict = {}

    # ---- Speichern/Laden -------------------------------------------------- #
    def save(self, d: Path) -> None:
        """Atomar: erst in <d>.tmp schreiben, dann austauschen – ein Abbruch hinterlässt nie einen halben Zustand."""
        tmp, old = d.with_name(d.name + ".tmp"), d.with_name(d.name + ".old")
        shutil.rmtree(tmp, ignore_errors=True)
        tmp.mkdir(parents=True)
        np.save(tmp / "sw.npy", self.sw)
        np.save(tmp / "swx.npy", self.swx)
        np.save(tmp / "swx2.npy", self.swx2)
        (tmp / "state.json").write_text(json.dumps(self.meta))
        shutil.rmtree(old, ignore_errors=True)
        if d.exists():
            d.rename(old)
        tmp.rename(d)
        shutil.rmtree(old, ignore_errors=True)

    @classmethod
    def load(cls, d: Path) -> "StackState | None":
        if not d.exists() and d.with_name(d.name + ".old").exists():      # Abbruch genau beim Austauschen
            d = d.with_name(d.name + ".old")
        try:
            sw = np.load(d / "sw.npy")
            st = cls(sw.shape)
            st.sw = sw
            st.swx = np.load(d / "swx.npy")
            st.swx2 = np.load(d / "swx2.npy")
            st.meta = json.loads((d / "state.json").read_text())
            return st
        except Exception:
            return None

    # ---- Akkumulieren ------------------------------------------------------ #
    def add_batch(self, X: np.ndarray, V: np.ndarray, w: np.ndarray, workers: int = 1, prefixes=()) -> int:
        """X (B,C,H,W) normierte Frames, V (B,H,W) gültig, w (B,) Gewichte. Gibt Zahl abgelehnter Pixel zurück.
        Pixel sind unabhängig -> Verarbeitung in Zeilenstreifen, parallel auf `workers` Threads (numpy gibt den GIL frei).
        prefixes: Stapelanteile k, für die der Zwischenstand „bisher + erste k Frames“ in self.snaps[k] = (Mittel, Gewichtsminimum)
        landet (Stacking-Stufen; die Clipping-Maske stammt aus dem ganzen Stapel)."""
        self.snaps = {k: (np.empty(self.shape, np.float32), np.empty(self.shape[1:], np.float32)) for k in prefixes}
        H = X.shape[2]
        w = w.astype(np.float32)
        # globale Größen einmal vorab (Streifen sollen sich nicht gegenseitig beeinflussen)
        check = float(self.sw.max()) >= 8 * float(np.median(w))
        sd_floor = 1e-6
        if check:
            s = slice(None, None, 8)                  # Stichprobe jeder 8. Zeile genügt für den Median
            sw = self.sw[:, s]
            m = self.swx[:, s] / np.maximum(sw, 1e-9)
            sd = np.sqrt(np.clip(self.swx2[:, s] / np.maximum(sw, 1e-9) - m**2, 0, None))
            if (sw > 0).any():
                sd_floor = 0.5 * float(np.median(sd[sw > 0]))
        n = max(1, min(workers, H // 64))
        bounds = np.linspace(0, H, n + 1).astype(int)

        def strip(i):
            ys = slice(bounds[i], bounds[i + 1])
            return self._add_rows(X[:, :, ys], V[:, ys], w, ys, check, sd_floor)

        if n == 1:
            return strip(0)
        with ThreadPoolExecutor(n) as ex:
            return sum(ex.map(strip, range(n)))

    def _add_rows(self, X, V, w, ys, check, sd_floor) -> int:
        keep = clip_batch(X, V)
        if check:
            # zusätzlich gegen bestehenden Stack prüfen (Satelliten/Cosmics in kleinen Batches)
            sw = self.sw[:, ys]
            mean = self.swx[:, ys] / np.maximum(sw, 1e-9)
            sd = np.maximum(np.sqrt(np.clip(self.swx2[:, ys] / np.maximum(sw, 1e-9) - mean**2, 0, None)), sd_floor)
            keep &= ~((sw > 0)[None] & (np.abs(X - mean[None]) > 5 * sd[None]))
        wk = keep * w[:, None, None, None]
        Xk = np.where(keep, X, 0).astype(np.float32)
        for k, (m, c) in self.snaps.items():
            sw = self.sw[:, ys] + wk[:k].sum(0)
            m[:, ys] = (self.swx[:, ys] + (wk[:k] * Xk[:k]).sum(0)) / np.maximum(sw, 1e-9)
            c[ys] = sw.min(0)
        self.sw[:, ys] += wk.sum(0)
        self.swx[:, ys] += (wk * Xk).sum(0)
        self.swx2[:, ys] += (wk * Xk * Xk).sum(0)
        return int(V.sum() * X.shape[1] - keep.sum())

    # ---- Ergebnis ---------------------------------------------------------- #
    def result(self) -> tuple[np.ndarray, np.ndarray]:
        """(Mittelwert (C,H,W), Abdeckung (H,W) in 0..1)."""
        mean = self.swx / np.maximum(self.sw, 1e-9)
        cov = self.sw.min(0)
        mx = float(cov.max())
        return mean, (cov / mx if mx > 0 else cov)


def batch_size_for(shape: tuple[int, int, int], budget_mb: int) -> int:
    C, H, W = shape
    # je Aufnahme: Stapel X + vorbereiteter nächster Stapel (doppelte Pufferung); Hilfsarrays entstehen nur je Streifen
    per = C * H * W * 4 * 2.5
    return int(np.clip(budget_mb * 1e6 // per, 3, 32))
