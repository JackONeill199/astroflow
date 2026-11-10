"""Mosaike: Stackfläche = Hülle aller Aufnahmen statt nur das Referenzbild.

Smart-Teleskope fahren für große Objekte ein Raster ab (Stellina-Mosaikmodus: feste Positionen im Abstand von
~400 px, die sich wiederholen). Gestackt auf das Referenzbild bliebe nur dessen Fläche – und nach dem Zuschnitt auf
volle Abdeckung oft nur ein Drittel davon. Ablauf:

1. **Erkennung** (`plan`): aus den Registrierungen (Aufnahme → Referenz) die Lage der Bildmitten. Streuen sie robust
   (5.–95. Perzentil) über mehr als `DETECT` der Bildbreite bzw. -höhe, ist es ein Mosaik. Drift, Dithering und
   Nachzentrieren bleiben darunter; Bildfeldrotation dreht um die Mitte und verschiebt sie nicht.
2. **Leinwand**: Rechteck um alle Zellen eines groben Rasters, die mindestens `MIN_SHARE` der Aufnahmen
   abdecken (Ausreißer am Rand zählen nicht), begrenzt auf `MAX_AREA` × Referenzfläche bzw. `MAX_PIXELS`.
   Der Ursprung (`origin`, Referenzpixel) wandert als Verschiebung in die Warp-Transformation; das Referenzbild
   selbst rückt in die Mitte der Aufnahmen (`center_ref`), damit auch die äußeren Kacheln genug Sterne mit ihm teilen.
3. **Nähte**: jede Aufnahme bekommt an ihren Rändern weich abfallende Gewichte (`feather`), und ihr Hintergrund wird
   nicht über den eigenen Median (der hinge vom Nebelanteil der Kachel ab), sondern über den Überlapp mit dem
   bisherigen Stack angeglichen (`match_background`: Ebene bzw. Versatz). Die Aufnahmen gehen von der Mitte nach
   außen ein, damit jede neue Kachel schon Überlapp hat.
4. **Zuschnitt** (`effective_cov`): Fläche gegen Mindesttiefe abwägen; die Abdeckung wird so umgerechnet, dass der
   gewählte Tiefenwert bei 0,5 liegt – alle Folgeschritte (Zuschnitt, GraXpert-Lückenfüllung, Stack-Reparatur …)
   arbeiten mit ihrer gewohnten Schwelle weiter.

Stand im Stack-Zustand (state.json): `mosaic` {spread, area, frames}, `origin` [x, y] (Referenzpixel der Leinwandecke),
`canvas` [H, W] (native Pixel, ohne resolution_scale). Ohne diese Schlüssel: gewöhnlicher Stack (Ursprung 0, Fläche
des Referenzbilds).
"""
from __future__ import annotations

import math

import numpy as np

MODES = ("auto", "on", "off")          # Projektoption `mosaic`
CROPS = ("wide", "compact")            # Projektoption `mosaic_crop`: Fläche bevorzugen | volle Tiefe (wie ohne Mosaik)
DETECT = 0.25           # Bildmitten streuen über mehr als 25 % der Bildbreite/-höhe -> Mosaik
MIN_GAIN = 1.15         # Leinwand muss mindestens 15 % größer sein als das Referenzbild (sonst lohnt es nicht)
MIN_SHARE = 0.02        # Leinwandzelle zählt, wenn mindestens 2 % der Aufnahmen (mind. 2) sie abdecken
MAX_AREA = 6.0          # höchstens 6× Fläche des Referenzbilds …
MAX_PIXELS = 48e6       # … und höchstens 48 Mio. Ausgabepixel (3 Summen × 3 Kanäle × 4 Byte ≈ 1,7 GB)
FEATHER = 0.06          # Randrampe der Gewichte: 6 % der kürzeren Bildseite
FEATHER_MIN = 0.05      # Gewicht ganz am Rand (nicht 0: sonst zählt der Rand gar nicht zur Abdeckung)
MIN_SAMPLES = 400       # Stichproben im Überlapp für den Hintergrundangleich
PLANE_SPAN = 0.4        # Ebene nur, wenn der Überlapp mindestens 40 % der Bildbreite und -höhe überspannt
CROP_LEVELS = (0.5, 0.4, 0.3, 0.22, 0.15)   # Kandidaten für die Mindesttiefe beim Zuschnitt (Anteil der größten Tiefe)


def mode(value) -> str:
    v = str(value or "auto").strip().lower()
    v = {"an": "on", "ein": "on", "1": "on", "true": "on", "aus": "off", "0": "off", "false": "off"}.get(v, v)
    return v if v in MODES else "auto"


def crop_mode(value) -> str:
    v = str(value or "wide").strip().lower()
    v = {"gross": "wide", "groß": "wide", "kompakt": "compact"}.get(v, v)
    return v if v in CROPS else "wide"


def origin(meta: dict) -> tuple[float, float]:
    """Leinwandecke in Referenzpixeln (nativ); 0, 0 ohne Mosaik."""
    o = meta.get("origin") or (0, 0)
    return float(o[0]), float(o[1])


def canvas(meta: dict) -> tuple[int, int]:
    """Leinwand (H, W) in nativen Pixeln (ohne resolution_scale) – ohne Mosaik das Referenzbild."""
    c = meta.get("canvas") or meta["shape"][1:]
    return int(c[0]), int(c[1])


def shift(ox: float, oy: float) -> np.ndarray:
    return np.array([[1, 0, -ox], [0, 1, -oy], [0, 0, 1]], float)


def _centers(Ms: np.ndarray, H: int, W: int) -> np.ndarray:
    c = np.array([(W - 1) / 2, (H - 1) / 2, 1.0])
    return (Ms @ c)[:, :2]


def spread(Ms: np.ndarray, shape: tuple[int, int]) -> tuple[float, float]:
    """Robuste Streuung der Bildmitten (5.–95. Perzentil) als Anteil von Bildbreite/-höhe."""
    H, W = shape
    if len(Ms) < 3:
        return 0.0, 0.0
    p = _centers(Ms, H, W)
    lo, hi = np.percentile(p, 5, axis=0), np.percentile(p, 95, axis=0)
    return float((hi[0] - lo[0]) / W), float((hi[1] - lo[1]) / H)


def center_ref(Ms: dict, shape: tuple[int, int], candidates: list) -> str | None:
    """Aufnahme (aus `candidates`, beste zuerst), deren Mitte der Mitte aller Aufnahmen am nächsten liegt – unter den
    besten 50 % der Kandidaten (mindestens 5), damit die Referenz trotzdem ein gutes Bild ist."""
    ids = [f for f in candidates if f in Ms]
    if not ids:
        return None
    H, W = shape
    allc = _centers(np.array(list(Ms.values())), H, W)
    mid = np.median(allc, axis=0)
    top = ids[:max(5, len(ids) // 2)]
    cc = _centers(np.array([Ms[f] for f in top]), H, W)
    return top[int(np.argmin(np.hypot(*(cc - mid).T)))]


def rebase(Ms: dict, new_ref: str) -> dict:
    """Transformationen Aufnahme → alte Referenz auf eine neue Referenz (eine der Aufnahmen) umrechnen."""
    inv = np.linalg.inv(Ms[new_ref])
    return {f: inv @ M for f, M in Ms.items()}


def plan(Ms: dict, shape: tuple[int, int], how: str = "auto", scale: int = 1) -> dict | None:
    """Leinwand für die Transformationen `Ms` (Aufnahme → Referenz, 3×3). shape = (H, W) der Aufnahmen.
    Gibt None (gewöhnlicher Stack) oder {origin [x, y], canvas [H, W], mosaic {...}} zurück."""
    if how == "off" or len(Ms) < 3:
        return None
    H, W = shape
    A = np.array(list(Ms.values()))
    sx, sy = spread(A, shape)
    if how == "auto" and max(sx, sy) < DETECT:
        return None
    # grobes Raster über die Hülle aller Aufnahmen; je Zelle zählen, wie viele Aufnahmen sie abdecken
    corners = np.array([[0, 0, 1], [W - 1, 0, 1], [0, H - 1, 1], [W - 1, H - 1, 1]], float).T
    pts = np.concatenate([(M @ corners)[:2].T for M in A])
    x0, y0 = np.floor(pts.min(0))
    x1, y1 = np.ceil(pts.max(0)) + 1
    b = max(8, int(math.ceil(math.sqrt((x1 - x0) * (y1 - y0) / 250_000))))
    gx = x0 + (np.arange(int(math.ceil((x1 - x0) / b))) + 0.5) * b
    gy = y0 + (np.arange(int(math.ceil((y1 - y0) / b))) + 0.5) * b
    XX, YY = np.meshgrid(gx, gy)
    P = np.stack([XX.ravel(), YY.ravel(), np.ones(XX.size)])
    cnt = np.zeros(XX.size, np.int32)
    for M in A:
        q = np.linalg.inv(M) @ P
        cnt += (q[0] >= 0) & (q[0] <= W - 1) & (q[1] >= 0) & (q[1] <= H - 1)
    cnt = cnt.reshape(XX.shape)
    need = max(2, int(math.ceil(MIN_SHARE * len(A))))
    ok = cnt >= need
    if not ok.any():
        return None
    rows, cols = np.nonzero(ok.any(1))[0], np.nonzero(ok.any(0))[0]
    r0, r1, c0, c1 = rows[0], rows[-1] + 1, cols[0], cols[-1] + 1
    # Speicher begrenzen: Rand mit der geringsten Abdeckung abschneiden, bis die Leinwand passt
    limit = min(MAX_AREA * H * W, MAX_PIXELS / scale ** 2)
    sub = np.where(ok, cnt, 0)
    while (r1 - r0) * (c1 - c0) * b * b > limit and r1 - r0 > 2 and c1 - c0 > 2:
        edges = {"t": sub[r0, c0:c1].sum(), "b": sub[r1 - 1, c0:c1].sum(), "l": sub[r0:r1, c0].sum(), "r": sub[r0:r1, c1 - 1].sum()}
        e = min(edges, key=lambda k: edges[k] / ((c1 - c0) if k in "tb" else (r1 - r0)))
        if e == "t":
            r0 += 1
        elif e == "b":
            r1 -= 1
        elif e == "l":
            c0 += 1
        else:
            c1 -= 1
    ox, oy = float(x0 + c0 * b), float(y0 + r0 * b)
    Wc, Hc = int(min((c1 - c0) * b, x1 - ox)), int(min((r1 - r0) * b, y1 - oy))
    area = Wc * Hc / (W * H)
    if how == "auto" and area < MIN_GAIN:
        return None
    return {"origin": [ox, oy], "canvas": [Hc, Wc],
            "mosaic": {"spread": [round(sx, 3), round(sy, 3)], "area": round(area, 2), "frames": len(A), "mode": how}}


def order(Ms: dict, shape: tuple[int, int], ids: list) -> list:
    """Reihenfolge fürs Stacken: Referenz zuerst, dann nach Abstand der Bildmitte von ihr (die Fläche wächst von der
    Mitte nach außen, jede neue Kachel überlappt schon gestackte). Unbekannte Transformation: ans Ende."""
    H, W = shape
    def dist(f):
        M = Ms.get(f)
        if M is None:
            return math.inf
        c = _centers(M[None], H, W)[0]
        return math.hypot(c[0] - (W - 1) / 2, c[1] - (H - 1) / 2)
    return sorted(ids, key=dist)


def footprint(M: np.ndarray, shape: tuple[int, int], out: tuple[int, int], scale: int, pad: int = 8):
    """Begrenzungsrechteck (y0, y1, x0, x1) der Aufnahme (shape = (H, W) nativ) im Ausgaberaster `out` (Leinwand × scale).
    M: Aufnahme → Leinwand (native Pixel). None, wenn die Aufnahme die Leinwand nicht trifft."""
    H, W = shape
    c = M @ np.array([[0, W - 1, 0, W - 1], [0, 0, H - 1, H - 1], [1, 1, 1, 1]], float)
    xs, ys = (c[0] + 0.5) * scale - 0.5, (c[1] + 0.5) * scale - 0.5
    x0, x1 = max(0, int(math.floor(xs.min())) - pad * scale), min(out[1], int(math.ceil(xs.max())) + 1 + pad * scale)
    y0, y1 = max(0, int(math.floor(ys.min())) - pad * scale), min(out[0], int(math.ceil(ys.max())) + 1 + pad * scale)
    if x1 <= x0 or y1 <= y0:
        return None
    return y0, y1, x0, x1


def feather(M: np.ndarray, shape: tuple[int, int], box, scale: int) -> np.ndarray:
    """Randgewicht der Aufnahme im Ausschnitt `box` (y0, y1, x0, x1) des Ausgaberasters: 1 im Inneren, zum Bildrand hin
    weich (Smoothstep) auf FEATHER_MIN – Kachelkanten verschwimmen statt als Stufe im Stack zu stehen."""
    H, W = shape
    y0, y1, x0, x1 = box
    Ai = np.linalg.inv(M)
    xo = (np.arange(x0, x1, dtype=np.float64) + 0.5) / scale - 0.5
    yo = (np.arange(y0, y1, dtype=np.float64) + 0.5) / scale - 0.5
    sx = Ai[0, 0] * xo[None, :] + Ai[0, 1] * yo[:, None] + Ai[0, 2]
    sy = Ai[1, 0] * xo[None, :] + Ai[1, 1] * yo[:, None] + Ai[1, 2]
    d = np.minimum(np.minimum(sx, W - 1 - sx), np.minimum(sy, H - 1 - sy))
    t = np.clip(d / max(8.0, FEATHER * min(H, W)), 0, 1)
    return (FEATHER_MIN + (1 - FEATHER_MIN) * t * t * (3 - 2 * t)).astype(np.float32)


def reference(state, step: int):
    """Verkleinertes Mittel des bisherigen Stacks (C, h, w) und Maske „genug Tiefe“ – Ziel des Hintergrundangleichs.
    None, solange nichts gestackt ist."""
    sw = state.sw[:, ::step, ::step]
    smin = sw.min(0)
    if not (smin > 0).any():
        return None, None
    ok = smin >= 1.5 * float(np.median(list(state.meta.get("frames", {}).values()) or [1.0]))
    if ok.sum() < MIN_SAMPLES:
        ok = smin > 0
    return state.swx[:, ::step, ::step] / np.maximum(sw, 1e-9), ok


def match_background(X: np.ndarray, V: np.ndarray, ref, ref_ok, step: int) -> list:
    """Hintergrund der Aufnahmen X (B, C, H, W, schon flussnormiert) angleichen – je Kanal eine robuste Ebene (bzw. nur
    ein Versatz bei schmalem Überlapp) aus der Differenz zum Ziel im Überlapp, abgezogen auf dem ganzen Raster.
    Ziel: der bisherige Stack (`ref`, `ref_ok` aus `reference`) plus die schon angeglichenen Aufnahmen dieses Stapels –
    so findet auch eine Kachel Anschluss, die nur mit Vorgängern im selben Stapel überlappt. Ohne Stack bleibt die erste
    Aufnahme (Referenz bzw. mittigste) wie sie ist. Aufnahmen ohne genug Überlapp behalten den Median-Abzug aus dem
    Warp. Ändert X; gibt je Aufnahme "plane" | "offset" | None zurück."""
    B, C, H, W = X.shape
    ys = np.arange(0, H, step)
    xs = np.arange(0, W, step)
    yn, xn = (ys - H / 2) / H, (xs - W / 2) / W
    gx, gy = ((np.arange(W) - W / 2) / W).astype(np.float32), ((np.arange(H) - H / 2) / H).astype(np.float32)
    h, w = len(ys), len(xs)
    if ref is not None:                                   # Stack zählt wie drei Aufnahmen (er ist tiefer als jede einzelne)
        acc = np.where(ref_ok, ref, 0).astype(np.float32) * 3
        num = ref_ok.astype(np.float32) * 3
    else:
        acc, num = np.zeros((C, h, w), np.float32), np.zeros((h, w), np.float32)
    out: list = []
    for b in range(B):
        vb = V[b][::step, ::step]
        tok = num > 0
        v = vb & tok
        n = int(v.sum())
        how = None
        if n >= MIN_SAMPLES:
            tgt = acc / np.maximum(num, 1e-9)
            iy, ix = np.nonzero(v)
            if n > 200_000:                               # genügt für drei Zahlen je Kanal
                k = np.linspace(0, n - 1, 200_000).astype(int)
                iy, ix = iy[k], ix[k]
            u, ww = xn[ix], yn[iy]
            # Ebene nur, wenn der Überlapp einen guten Teil der Aufnahme überspannt (sonst kippt sie beim Extrapolieren)
            fy, fx = np.nonzero(vb.any(1))[0], np.nonzero(vb.any(0))[0]
            plane = bool(np.ptp(ix) >= PLANE_SPAN * (fx[-1] - fx[0]) and np.ptp(iy) >= PLANE_SPAN * (fy[-1] - fy[0]))
            A = np.c_[np.ones(len(u)), u, ww] if plane else np.ones((len(u), 1))
            for c in range(C):
                d = (X[b, c][::step, ::step][iy, ix] - tgt[c][iy, ix]).astype(np.float64)
                use = np.ones(len(d), bool)
                coef = np.zeros(A.shape[1])
                for _ in range(5):                        # robust: Sterne (Seeing), Satelliten, Ausreißer
                    if use.sum() < A.shape[1] * 20:
                        break
                    coef = np.linalg.lstsq(A[use], d[use], rcond=None)[0] if plane else np.array([np.median(d[use])])
                    r = d - A @ coef
                    s = 1.4826 * float(np.median(np.abs(r[use]))) + 1e-12
                    new = np.abs(r) < 3 * s
                    if np.array_equal(new, use):
                        break
                    use = new
                X[b, c] -= np.float32(coef[0])
                if plane:
                    X[b, c] -= np.float32(coef[1]) * gx[None, :]
                    X[b, c] -= np.float32(coef[2]) * gy[:, None]
            how = "plane" if plane else "offset"
        elif ref is not None or b > 0:
            out.append(None)                              # ohne Anschluss: nicht ins Ziel (sein Median-Niveau ist unsicher)
            continue
        out.append(how)
        acc += np.where(vb, X[b][:, ::step, ::step], 0)
        num += vb
    return out


def effective_cov(cov: np.ndarray, how: str = "wide") -> tuple[np.ndarray, dict]:
    """Abdeckung eines Mosaiks für Zuschnitt und Folgeschritte umrechnen: Mindesttiefe t wählen (Fläche × √t maximal,
    das Rauschen wächst mit 1/√Tiefe; `compact` = 0,5 wie ohne Mosaik) und so abbilden, dass t bei 0,5 liegt."""
    from .processing import coverage_box
    f = max(1, int(math.ceil(max(cov.shape) / 1000)))
    small = cov[::f, ::f]
    best, best_s, areas = 0.5, -1.0, {}
    for t in (CROP_LEVELS if how == "wide" else (0.5,)):
        y0, y1, x0, x1 = coverage_box(small, t)
        a = (y1 - y0) * (x1 - x0) / small.size
        areas[t] = round(a, 3)
        s = a * math.sqrt(t)
        if s > best_s * 1.02:                             # tiefere Schwelle nur bei echtem Flächengewinn
            best, best_s = t, s
    t = best
    out = np.where(cov < t, 0.5 * cov / t, 0.5 + 0.5 * (cov - t) / (1 - t)).astype(np.float32)
    return out, {"crop": how, "min_depth": t, "crop_area": areas.get(t)}
