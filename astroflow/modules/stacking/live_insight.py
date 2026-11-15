"""Einblicke in die laufende Bearbeitung: kleine Auszüge echter Zwischenergebnisse für die Live-Ansicht.

Jede Funktion bekommt den Live-Lauf (`live.LiveRun`) und die Daten, die der Bearbeitungsschritt ohnehin berechnet hat, und
legt daraus einen Einblick ab (`run.insight`). Aufruf nur über `live.offer(run, fn, …)`: ohne offene Live-Ansicht wird gar
nichts gerechnet, Fehler bleiben folgenlos. Grundsatz: nichts erfinden – alle Punkte, Kurven und Karten stammen aus dem
Schritt selbst (verkleinert bzw. als Stichprobe), Erwartungswerte (z. B. 1/√N) zeichnet erst der Browser und beschriftet sie.
Koordinaten siehe `live.uv` (Bruchteile in Anzeigeausrichtung)."""
from __future__ import annotations

import numpy as np

from . import live

rnd = lambda v, n=4: round(float(v), n)


def _lum(a: np.ndarray) -> np.ndarray:
    a = np.asarray(a, np.float32)
    return a.mean(0) if a.ndim == 3 else a


# ------------------------------------------------------------- Sensorfehler #
def defects(run, idx: np.ndarray, shape: tuple, glow=None, night: str = '', source: str = '') -> str | None:
    """Ortsfeste Sensorfehler einer Nacht (Indizes ins Rohbild der Form `shape`) und – falls gemessen – das Glühmodell.
    Kein Vorschaubild: der Browser zeichnet das Sensorfeld (Seitenverhältnis aus `frame`)."""
    h, w = int(shape[-2]), int(shape[-1])
    idx = np.asarray(idx, np.int64) % (h * w)                     # Farbrohbild (C,H,W): Position auf dem Sensor
    y, x = np.divmod(idx, w)
    k = live.thin(len(idx))
    cols = []
    if len(idx):                                                  # echte Spaltenfehler: ein Viertel der Spalte oder mehr
        cnt = np.bincount(x, minlength=w)
        cols = [rnd((c + 0.5) / w) for c in np.nonzero(cnt >= max(20, h // 4))[0][:40]]
    out = {'frame': {'w': w, 'h': h}, 'n': int(len(idx)), 'points': live.uv(x[k], y[k], w, h), 'columns': cols,
           'night': night, 'source': source}
    if glow:
        g = glow_map(glow)
        if g is not None:
            out['glow'] = g
    return run.insight('defects', out)


def glow_map(model) -> dict | None:
    """Glühmodell (je Farbebene Zeilen-/Spaltenprofil + Restkarte) als eine kleine Karte in ADU (Mittel der Ebenen)."""
    from .calibration import _full_map
    maps = []
    for pl in model:
        r, c = np.asarray(pl[0], np.float32), np.asarray(pl[1], np.float32)
        m = r[:, None] + c[None, :]
        if len(pl) > 2:
            m = m + _full_map(pl[2], m.shape)
        maps.append(m)
    if not maps or len({m.shape for m in maps}) != 1:
        return None
    rows, lo, hi = live.grid_of(np.mean(maps, axis=0))
    return {'grid': rows, 'min': rnd(lo, 2), 'max': rnd(hi, 2), 'unit': 'ADU'}


def hotfix(run, found: list, mean: np.ndarray) -> str | None:
    """Stack-Reparatur (`stackfix.repair`): gefundene Hotpixel-Punkte an ihren Stellen im linearen Stack."""
    if not found:
        return None
    H, W = mean.shape[-2:]
    pic = run.snap(mean)
    k = live.thin(len(found))
    f = np.asarray([found[i][:3] for i in k], np.int64)
    pts = live.uv(f[:, 2], f[:, 1], W, H)
    return run.insight('hotfix', {'n': len(found), 'points': [p + [int(c)] for p, c in zip(pts, f[:, 0])],
                                  'channels': np.bincount([c for c, *_ in found], minlength=3).tolist()},
                       {'base': pic})


# ---------------------------------------------------------------- Hintergrund #
def background(run, before: np.ndarray, after: np.ndarray, method: str, support: dict | None = None) -> str | None:
    """Abgezogener Hintergrund = vorher − nachher (verkleinert) als Karte; dazu, falls vorhanden, die echten Stützpunkte.
    method: 'graxpert' | 'polynom' | 'himmel'."""
    b, a = live.sample(before), live.sample(after)
    if b.shape != a.shape:
        return None
    model = _lum(b) - _lum(a)
    rows, lo, hi = live.grid_of(model)
    sky = float(np.median(_lum(b)))
    out = {'method': method, 'grid': rows, 'min': rnd(lo, 6), 'max': rnd(hi, 6),
           # Spanne des Verlaufs im Verhältnis zum Himmelshintergrund vorher (nur wenn der Himmel deutlich über 0 liegt)
           'span_pct': rnd(100 * (hi - lo) / sky, 1) if sky > 1e-6 and hi > lo else None}
    if support:
        out['support'] = support
    pics = {'before': run.snap(before), 'after': run.snap(after)}
    return run.insight('background', out, pics)


def support_points(pts: np.ndarray, use: np.ndarray, degree: int) -> dict:
    """Stützkacheln des Polynom-Fits (`remove_gradient`): pts (y, x) in [−1, 1], use = in den Fit eingegangen."""
    pts = np.asarray(pts, np.float64)
    k = live.thin(len(pts))
    u = np.round((pts[k, 1] + 1) / 2, 4)
    v = np.round(1 - (pts[k, 0] + 1) / 2, 4)                      # Anzeige senkrecht gespiegelt
    return {'degree': int(degree), 'points': [[float(a), float(b), int(bool(c))] for a, b, c in zip(u, v, np.asarray(use)[k])],
            'used': int(np.asarray(use).sum()), 'total': int(len(pts))}


# ------------------------------------------------------------ Farbkalibrierung #
def color(run, mean: np.ndarray, sample: dict, gains: list) -> str | None:
    """Gaia-Farbkalibrierung (`photometry.pcc_gains`): zugeordnete Sterne mit BP−RP und gemessener Farbe, Ausgleichsgeraden,
    Faktoren. sample: x, y (Stackpixel), bprp, r = ln(R/G), b = ln(B/G), use_r/use_b (in der Geraden), fit {R/B: a, b}."""
    H, W = mean.shape[-2:]
    n = len(sample['x'])
    k = live.thin(n, 300)
    pts = live.uv(np.asarray(sample['x'])[k], np.asarray(sample['y'])[k], W, H)
    stars = [p + [rnd(sample['bprp'][i], 3), rnd(sample['r'][i], 3), rnd(sample['b'][i], 3),
                  int(sample['use_r'][i]) | 2 * int(sample['use_b'][i])] for p, i in zip(pts, k)]
    fit = {c: {'a': rnd(v['a'], 4), 'b': rnd(v['b'], 4)} for c, v in sample['fit'].items()}
    return run.insight('color', {'n': n, 'stars': stars, 'fit': fit, 'g2v': sample['g2v'],
                                 'gains': [rnd(g, 3) for g in gains]}, {'base': run.snap(mean)})


# -------------------------------------------------------------------- Stretch #
def stretch(run, before: np.ndarray, after: np.ndarray, curve, black: float, white: float, method: str,
            params: dict | None = None) -> str | None:
    """Strecken: echtes Histogramm vorher (zwischen Schwarz- und Weißpunkt) und nachher, die angewandte Übertragungskurve an
    256 Stellen, dazu zwei Bilder: der Stack in linearer Darstellung (ohne Kurve) und das gestreckte Ergebnis.
    curve: genau die Funktion, die das Bild gestreckt hat (Eingabe in Stack-Einheiten, (C,h,w) → (C,h,w))."""
    if not white > black:
        return None
    x = np.linspace(0, 1, live.CURVE_N, dtype=np.float32)
    ramp = np.repeat((black + x * (white - black))[None, None, :], 3, axis=0)
    y = np.clip(_lum(curve(ramp))[0], 0, 1)
    sb = _lum(live.sample(before))
    sa = _lum(live.sample(after))
    out = {'method': method, 'curve': np.round(y, 4).tolist(),
           'hist_in': live.hist((sb - black) / (white - black), 0, 1), 'hist_out': live.hist(sa, 0, 1),
           # Anteil der Pixel, die im linearen Bild unter 5 % der Spanne liegen – „fast alles ist dunkel“
           'dark_pct': rnd(100 * float(((sb - black) / (white - black) < 0.05).mean()), 1),
           'median_in': rnd(float(np.median((sb - black) / (white - black))), 4), 'median_out': rnd(float(np.median(sa)), 4),
           'params': params or {}}
    pics = {'before': run.snap(live.linear_view(before, black, white), linear=False), 'after': run.snap(after, linear=False)}
    return run.insight('stretch', out, pics)


def stars(run, base: np.ndarray, layer: np.ndarray, how: str, count: int | None = None) -> str | None:
    """Sternentrennung: gestrecktes sternloses Bild und die Sternebene (beide echt, so wie sie später zusammengesetzt werden)."""
    return run.insight('stars', {'how': how, 'count': count},
                       {'base': run.snap(base, linear=False), 'stars': run.snap(layer, linear=False)})
