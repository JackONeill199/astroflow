"""Grafikkarte fürs Stacking (OpenCL über pyopencl, optional) – herstellerunabhängig: AMD, NVIDIA, Intel.

Übernimmt die beiden teuersten Schritte (Profil 219 Stellina-Frames: ~80 % der Stacking-Rechenzeit):
  warp        kubische B-Spline-Interpolation wie scipy.ndimage.affine_transform(order=3) – Vorfilter (rekursiv
              je Zeile/Spalte) und Auswertung auf der GPU; Gültigkeitsmaske weiter analytisch auf der CPU.
              warp_poly: dasselbe mit Polynom 2. Ordnung als Abbildung (Verzerrungskorrektur, registration.fit_distortion).
  accumulate  Sigma-Clipping des Stapels (Median der gültigen Werte, 2 Runden), Abgleich mit dem bestehenden Stack
              und Aufsummieren in sw/swx/swx2 – je Pixel ein Arbeitselement, Stapel ≤ 32 Frames sortiert im Register.

Nicht bitgleich zur CPU (float32 statt float64 in ndimage, Rauschboden des Clippings je Stapel statt je Streifen), die
Abweichungen liegen weit unter dem Stack-Rauschen (Test `test_gpu.py`). Fehlt pyopencl oder eine GPU oder geht etwas
schief, rechnet die CPU wie bisher – ein GPU-Fehler bricht das Stacking nie ab.
"""
from __future__ import annotations

import os
import threading
import traceback
from dataclasses import dataclass

import numpy as np

MODES = ("auto", "off")
MAX_B = 32                           # größter Stapel (stacking.batch_size_for begrenzt auf 32)
STRIP_BYTES = 1 << 30                # Stapel streifenweise hochladen: höchstens ~1 GB je Puffer


@dataclass
class GpuConfig:
    mode: str = "auto"               # auto = Grafikkarte nutzen, falls vorhanden; off = immer CPU


GPU = GpuConfig()

_SRC = r"""
#define POLE (-0.2679491924311228f)          // sqrt(3) - 2, kubischer B-Spline
#define HORIZON 16                           // |POLE|^16 < 1e-9

// Vorfilter (Interpolationskoeffizienten) entlang einer Linie mit Spiegelrand, in-place.
inline void spline_line(__global float *p, int n, int step) {
    if (n < 2) return;
    const float z = POLE, lam = (1.0f - z) * (1.0f - 1.0f / z);
    for (int k = 0; k < n; k++) p[k * step] *= lam;
    float s = p[0], zk = z;
    for (int k = 1; k < min(n, HORIZON); k++) { s += zk * p[k * step]; zk *= z; }
    p[0] = s;
    for (int k = 1; k < n; k++) p[k * step] += z * p[(k - 1) * step];
    p[(n - 1) * step] = (z / (z * z - 1.0f)) * (p[(n - 1) * step] + z * p[(n - 2) * step]);
    for (int k = n - 2; k >= 0; k--) p[k * step] = z * (p[(k + 1) * step] - p[k * step]);
}

__kernel void prefilter_rows(__global float *img, int H, int W) {
    int y = get_global_id(0);
    if (y < H) spline_line(img + (size_t)y * W, W, 1);
}

__kernel void prefilter_cols(__global float *img, int H, int W) {
    int x = get_global_id(0);
    if (x < W) spline_line(img + x, H, W);
}

inline int mirror(int i, int n) {
    if (n == 1) return 0;
    int p = 2 * (n - 1);
    i = abs(i) % p;
    return i < n ? i : p - i;
}

inline void bspline_w(float t, float *w) {
    float t2 = t * t, t3 = t2 * t, u = 1.0f - t;
    w[0] = u * u * u / 6.0f;
    w[1] = (3.0f * t3 - 6.0f * t2 + 4.0f) / 6.0f;
    w[2] = (-3.0f * t3 + 3.0f * t2 + 3.0f * t + 1.0f) / 6.0f;
    w[3] = t3 / 6.0f;
}

// kubischer B-Spline an (sy,sx); außerhalb des Quellbildes 0 (mode="constant")
inline float sample(__global const float *coef, int H, int W, float sy, float sx) {
    float v = 0.0f;
    if (sy >= 0.0f && sy <= H - 1 && sx >= 0.0f && sx <= W - 1) {
        int iy = (int)floor(sy), ix = (int)floor(sx);
        float wy[4], wx[4];
        bspline_w(sy - iy, wy);
        bspline_w(sx - ix, wx);
        for (int j = 0; j < 4; j++) {
            __global const float *row = coef + (size_t)mirror(iy - 1 + j, H) * W;
            float r = 0.0f;
            for (int i = 0; i < 4; i++) r += wx[i] * row[mirror(ix - 1 + i, W)];
            v += wy[j] * r;
        }
    }
    return v;
}

// Ausgabe (y,x) <- Quelle (sy,sx) = mat·(y,x) + off
__kernel void warp(__global const float *coef, __global float *out, int H, int W, int Ho, int Wo,
                   float m00, float m01, float m10, float m11, float o0, float o1) {
    int x = get_global_id(0), y = get_global_id(1);
    if (x >= Wo || y >= Ho) return;
    out[(size_t)y * Wo + x] = sample(coef, H, W, m00 * y + m01 * x + o0, m10 * y + m11 * x + o1);
}

// Verzerrungskorrektur: Quelle = Polynom 2. Ordnung in u = (x − cx)/s, v = (y − cy)/s
// (Terme 1, u, v, u², uv, v²; p[0..5] für sx, p[6..11] für sy)
__kernel void warp_poly(__global const float *coef, __global float *out, int H, int W, int Ho, int Wo,
                        float cx, float cy, float s, __constant float *p) {
    int x = get_global_id(0), y = get_global_id(1);
    if (x >= Wo || y >= Ho) return;
    float u = (x - cx) / s, v = (y - cy) / s;
    float t[6] = {1.0f, u, v, u * u, u * v, v * v};
    float sx = 0.0f, sy = 0.0f;
    for (int i = 0; i < 6; i++) { sx += p[i] * t[i]; sy += p[6 + i] * t[i]; }
    out[(size_t)y * Wo + x] = sample(coef, H, W, sy, sx);
}

// Runde 0: Median der gültigen Werte und Streuung um ihn (wie stacking._clip, erste Runde)
__kernel void clip_stats(__global const float *X, __global const uchar *V, __global float *ref0, __global float *sd,
                         int B, int C, int HW) {
    size_t g = get_global_id(0);
    if (g >= (size_t)C * HW) return;
    int c = g / HW, p = g % HW;
    float v[32];
    int n = 0;
    for (int b = 0; b < B; b++)
        if (V[(size_t)b * HW + p]) v[n++] = X[((size_t)b * C + c) * HW + p];
    if (n == 0) { ref0[g] = 0.0f; sd[g] = -1.0f; return; }
    for (int i = 1; i < n; i++) {                      // Einfügesortierung, n <= 32
        float a = v[i]; int j = i - 1;
        while (j >= 0 && v[j] > a) { v[j + 1] = v[j]; j--; }
        v[j + 1] = a;
    }
    float m = (n & 1) ? v[n / 2] : 0.5f * (v[n / 2 - 1] + v[n / 2]);
    float s = 0.0f;
    for (int i = 0; i < n; i++) s += (v[i] - m) * (v[i] - m);
    ref0[g] = m;
    sd[g] = sqrt(s / n);
}

// Runden 1–2 des Clippings, Abgleich mit dem bestehenden Stack, Aufsummieren
__kernel void clip_add(__global const float *X, __global const uchar *V, __global const float *w,
                       __global const float *ref0, __global const float *sd0,
                       __global float *sw, __global float *swx, __global float *swx2, __global int *rejected,
                       int B, int C, int HW, float floor_sd, float klo, float khi, int check, float sd_floor) {
    size_t g = get_global_id(0);
    if (g >= (size_t)C * HW) return;
    int p = g % HW, c = g / HW;
    float s = fmax(sd0[g], floor_sd), ref = ref0[g];
    unsigned int keep = 0, valid = 0;
    for (int b = 0; b < B; b++)
        if (V[(size_t)b * HW + p]) valid |= 1u << b;
    if (B < 3 || sd0[g] < 0.0f) {
        keep = valid;
    } else {
        for (int it = 0; it < 2; it++) {
            keep = 0;
            float sum = 0.0f; int cnt = 0;
            for (int b = 0; b < B; b++) {
                if (!((valid >> b) & 1u)) continue;
                float x = X[((size_t)b * C + c) * HW + p], d = x - ref;
                if (d <= khi * s && d >= -klo * s) { keep |= 1u << b; sum += x; cnt++; }
            }
            ref = cnt > 0 ? sum / cnt : ref0[g];
        }
    }
    float a = sw[g];
    float mean = 0.0f, sdev = 0.0f;
    if (check && a > 0.0f) {
        mean = swx[g] / fmax(a, 1e-9f);
        sdev = fmax(sqrt(fmax(swx2[g] / fmax(a, 1e-9f) - mean * mean, 0.0f)), sd_floor);
    }
    float aw = 0.0f, awx = 0.0f, awx2 = 0.0f;
    int nrej = 0;
    for (int b = 0; b < B; b++) {
        if (!((valid >> b) & 1u)) continue;
        float x = X[((size_t)b * C + c) * HW + p];
        if (((keep >> b) & 1u) && !(check && a > 0.0f && fabs(x - mean) > 5.0f * sdev)) {
            float wx = w[b] * x;
            aw += w[b]; awx += wx; awx2 += wx * x;
        } else {
            nrej++;
        }
    }
    sw[g] = a + aw;
    swx[g] += awx;
    swx2[g] += awx2;
    if (nrej) atomic_add(rejected, nrej);
}
"""


class Engine:
    """Eine Grafikkarte mit übersetztem Programm. Aufrufe werden serialisiert (eine Befehlswarteschlange); die
    Arbeit auf der GPU dauert je Frame nur Millisekunden, die CPU-Threads lesen/kalibrieren derweil weiter."""

    def __init__(self, cl, ctx, dev):
        self.cl = cl
        self.ctx = ctx
        self.dev = dev
        self.name = (getattr(dev, "board_name_amd", "") or dev.name).strip()
        self.queue = cl.CommandQueue(ctx)
        prg = cl.Program(ctx, _SRC).build()
        self.k = {n: cl.Kernel(prg, n) for n in ("prefilter_rows", "prefilter_cols", "warp", "warp_poly", "clip_stats", "clip_add")}
        self.lock = threading.RLock()                  # wiedereintrittsfähig: warp_poly hält sie über _warp hinweg
        self._bufs: dict = {}
        self.broken: str | None = None

    def _buf(self, key: str, nbytes: int):
        b = self._bufs.get(key)
        if b is None or b.size < nbytes:
            self._bufs[key] = b = self.cl.Buffer(self.ctx, self.cl.mem_flags.READ_WRITE, max(nbytes, 4))
        return b

    def fail(self, e: Exception) -> None:
        """Fehler: GPU für den Rest der Laufzeit abschalten (Aufrufer rechnet auf der CPU weiter)."""
        if not self.broken:
            traceback.print_exc()
            self.broken = f"{type(e).__name__}: {e}"
            print(f"GPU abgeschaltet, weiter auf der CPU: {self.broken}", flush=True)

    def free(self) -> None:
        """Puffer freigeben (nach jedem Stacking-Lauf; der Grafikspeicher gehört nicht nur uns)."""
        with self.lock:
            self._bufs.clear()

    # ---- Registrierung --------------------------------------------------- #
    def _warp(self, img: np.ndarray, out_shape: tuple[int, int], kernel: str, args: list) -> np.ndarray:
        cl, q = self.cl, self.queue
        C, H, W = img.shape
        Ho, Wo = out_shape
        out = np.empty((C, Ho, Wo), np.float32)
        with self.lock:
            src = self._buf("w_src", H * W * 4)
            dst = self._buf("w_dst", Ho * Wo * 4)
            for c in range(C):
                cl.enqueue_copy(q, src, np.ascontiguousarray(img[c], np.float32))
                self.k["prefilter_rows"](q, (H,), None, src, np.int32(H), np.int32(W))
                self.k["prefilter_cols"](q, (W,), None, src, np.int32(H), np.int32(W))
                self.k[kernel](q, (Wo, Ho), None, src, dst, np.int32(H), np.int32(W), np.int32(Ho), np.int32(Wo), *args)
                cl.enqueue_copy(q, out[c], dst)
            q.finish()
        return out

    def warp(self, img: np.ndarray, mat: np.ndarray, off, out_shape: tuple[int, int]) -> np.ndarray:
        return self._warp(img, out_shape, "warp",
                          [np.float32(v) for v in (mat[0, 0], mat[0, 1], mat[1, 0], mat[1, 1], off[0], off[1])])

    def warp_poly(self, img: np.ndarray, c: np.ndarray, ctr, s: float, out_shape: tuple[int, int]) -> np.ndarray:
        """Verzerrungskorrektur (registration.poly_out): c (2,6) Koeffizienten für sx, sy in Ausgabepixeln."""
        cl = self.cl
        with self.lock:                                # Koeffizienten-Puffer bis zum Ende des Warps für uns
            pb = self._bufs.get("w_poly")
            if pb is None:
                self._bufs["w_poly"] = pb = cl.Buffer(self.ctx, cl.mem_flags.READ_ONLY, 12 * 4)
            cl.enqueue_copy(self.queue, pb, np.ascontiguousarray(np.asarray(c, np.float32).ravel()))
            return self._warp(img, out_shape, "warp_poly", [np.float32(ctr[0]), np.float32(ctr[1]), np.float32(s), pb])

    # ---- Stapeln ------------------------------------------------------------ #
    def accumulate(self, state, X: np.ndarray, V: np.ndarray, w: np.ndarray, check: bool, sd_floor: float,
                   kappa_lo: float = 3.0, kappa_hi: float = 3.0) -> int:
        """Wie StackState._add_rows über das ganze Bild, in Zeilenstreifen von höchstens STRIP_BYTES je Puffer."""
        cl, q = self.cl, self.queue
        B, C, H, W = X.shape
        rows = max(1, min(H, STRIP_BYTES // max(1, B * C * W * 4)))
        wv = np.ascontiguousarray(w, np.float32)
        rejected, done = 0, []
        with self.lock:
            for y0 in range(0, H, rows):
                ys = slice(y0, min(H, y0 + rows))
                h = ys.stop - ys.start
                n, HW = C * h * W, h * W
                bx = self._buf("x", B * n * 4)
                bv = self._buf("v", B * HW)
                bw = self._buf("w", MAX_B * 4)
                br, bs = self._buf("ref0", n * 4), self._buf("sd", n * 4)
                b1, b2, b3 = (self._buf(k, n * 4) for k in ("sw", "swx", "swx2"))
                brej = self._buf("rej", 4)
                cl.enqueue_copy(q, bx, np.ascontiguousarray(X[:, :, ys]))
                cl.enqueue_copy(q, bv, np.ascontiguousarray(V[:, ys], np.uint8))
                cl.enqueue_copy(q, bw, wv)
                cl.enqueue_copy(q, brej, np.zeros(1, np.int32))
                sws = [np.array(a[:, ys]) for a in (state.sw, state.swx, state.swx2)]        # Kopien (Zustand erst am Ende)
                for buf, a in zip((b1, b2, b3), sws):
                    cl.enqueue_copy(q, buf, a)
                self.k["clip_stats"](q, (n,), None, bx, bv, br, bs, np.int32(B), np.int32(C), np.int32(HW))
                floor = 0.0
                if B >= 3:
                    sd = np.empty(n, np.float32)
                    cl.enqueue_copy(q, sd, bs)
                    sub = sd[::3]
                    sub = sub[sub >= 0]
                    floor = 0.5 * float(np.median(sub)) if sub.size else 0.0
                self.k["clip_add"](q, (n,), None, bx, bv, bw, br, bs, b1, b2, b3, brej, np.int32(B), np.int32(C),
                                  np.int32(HW), np.float32(floor), np.float32(kappa_lo), np.float32(kappa_hi),
                                  np.int32(bool(check)), np.float32(sd_floor))
                for buf, a in zip((b1, b2, b3), sws):
                    cl.enqueue_copy(q, a, buf)
                r = np.empty(1, np.int32)
                cl.enqueue_copy(q, r, brej)
                q.finish()
                rejected += int(r[0])
                done.append((ys, sws))
        # erst übernehmen, wenn alle Streifen geklappt haben – sonst rechnet die CPU den ganzen Stapel noch einmal
        for ys, sws in done:
            for dst, a in zip((state.sw, state.swx, state.swx2), sws):
                dst[:, ys] = a
        return rejected


_ENGINE: Engine | None = None
_TRIED = False
_INIT_LOCK = threading.Lock()


def _pick(cl):
    """Leistungsstärkste GPU: dedizierter Speicher × Recheneinheiten (die iGPU des Ryzen fällt so heraus)."""
    best, score = None, 0
    for p in cl.get_platforms():
        try:
            devs = p.get_devices(device_type=cl.device_type.GPU)
        except Exception:
            continue
        for d in devs:
            s = d.global_mem_size * d.max_compute_units
            if s > score:
                best, score = d, s
    return best


def _off() -> bool:
    return GPU.mode == "off" or os.environ.get("ASTROFLOW_GPU", "").lower() in ("0", "off", "false", "no")


def engine() -> Engine | None:
    """GPU-Engine oder None (Einstellung aus, pyopencl fehlt, keine GPU, Übersetzen fehlgeschlagen, früherer Fehler)."""
    global _ENGINE, _TRIED
    if _off():
        return None
    with _INIT_LOCK:
        if not _TRIED:
            _TRIED = True
            try:
                import pyopencl as cl
                dev = _pick(cl)
                if dev is not None and dev.global_mem_size >= 2 << 30:
                    _ENGINE = Engine(cl, cl.Context([dev]), dev)
            except Exception:
                traceback.print_exc()
                _ENGINE = None
    if _ENGINE is not None and _ENGINE.broken:
        return None
    return _ENGINE


def status() -> dict:
    """Für die Einstellungen: gefundene Karte und ob sie genutzt wird."""
    try:
        import pyopencl  # noqa: F401
        have = True
    except Exception:
        have = False
    e = engine() if have else None
    return {"mode": "off" if _off() else GPU.mode, "pyopencl": have, "device": e.name if e else None,
            "active": e is not None, "error": _ENGINE.broken if _ENGINE else None}
