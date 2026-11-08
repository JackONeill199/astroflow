"""Kleine echte Zwischenbilder und kompakter Ablauf; keine zusätzliche Vollbild-Entwicklung."""
from __future__ import annotations

import json
import math
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
from PIL import Image

SIDE = 800
INTERVAL = 4.0
# „Das Bild entsteht aus dem Rauschen“: echte Zwischenstände des Stacks nach diesen Aufnahmenzahlen (anfangs fast jeder
# Schritt, dann je ×1,5 seltener) – mit fester Streckung über den ganzen Lauf, damit man das Rauschen wirklich sinken sieht.
FRAMES_KEEP = 12              # so viele Zwischenstände (JPG) bleiben je Lauf liegen
EARLY = 80                    # bis hierher auch Zwischenstände mitten in einem Stapel (Mini-Stack auf jedem n-ten Pixel)


def schedule(limit=10 ** 6):
    """Aufnahmenzahlen für Zwischenstände: 1, 2, 3, 4, 5, 6, 8, 10, 13, 16, 20, 25, 30 … (je ≥ ×1,25, später ×1,5)."""
    out, n = [], 1
    while n <= limit:
        out.append(n)
        n = max(n + 1, round(n * (1.25 if n < 30 else 1.5)))
    return out


SCHEDULE = schedule()


def fixed_stretch(mean):
    """Streckung aus dem ersten Zwischenstand, danach für den ganzen Lauf fest: Schwarzpunkt je Kanal knapp unter dem
    Himmel, gemeinsamer Weißpunkt an den hellen Sternen, asinh so, dass der Himmel bei ~0,13 liegt. Weil sie fest bleibt,
    wird das Rauschen sichtbar weniger (eine Auto-Streckung je Bild würde es immer gleich hell machen)."""
    a = np.asarray(mean, np.float32)
    ok = np.isfinite(a) & (a != 0)
    bg = np.array([float(np.median(c[k])) if k.any() else 0.0 for c, k in zip(a, ok)])
    sig = np.array([1.4826 * float(np.median(np.abs(c[k] - b))) if k.any() else 1.0 for c, k, b in zip(a, ok, bg)])
    sig = np.maximum(sig, 1e-3 * np.maximum(np.abs(bg), 1.0))   # völlig flaches Bild: Himmel nicht auf Schwarz legen
    black = bg - 1.0 * sig
    hi = float(np.percentile((a - bg[:, None, None])[ok], 99.95)) if ok.any() else 1.0
    span = max(hi + float(sig.mean()), 1e-6)
    r = float(np.clip(sig.mean() / span, 1e-5, 0.5))           # Himmel relativ zur Spanne
    lo_k, hi_k = 0.1, 1e5                                       # asinh-Faktor per Bisektion: f(r) = 0,13
    for _ in range(60):
        k = (lo_k * hi_k) ** 0.5
        if np.arcsinh(k * r) / np.arcsinh(k) < 0.13:
            lo_k = k
        else:
            hi_k = k
    return {'black': black.tolist(), 'span': span, 'k': float(k)}


LUT_N = 4096


def apply_stretch(mean, p):
    """Feste Kurve als Tabelle (4096 Stufen, schneller als asinh je Pixel) → uint8 (C,h,w)."""
    a = np.asarray(mean, np.float32)
    x = (a - np.asarray(p['black'], np.float32)[:, None, None]) * np.float32((LUT_N - 1) / p['span'])
    lut = p.get('_lut')
    if lut is None:
        t = np.linspace(0, 1, LUT_N)
        lut = p['_lut'] = (np.arcsinh(p['k'] * t) / np.arcsinh(p['k']) * 255 + .5).astype(np.uint8)
    y = lut[np.clip(np.nan_to_num(x, nan=0.0), 0, LUT_N - 1).astype(np.int32)]
    y[a == 0] = 0                                               # nicht bedeckte Stellen schwarz
    return y


def noise_of(mean):
    """Rauschen eines Zwischenstands: robuste Streuung der Differenz benachbarter Pixel der Luminanz (unempfindlich gegen
    Verläufe und große Strukturen), nur bedeckte Stellen."""
    lum = np.asarray(mean, np.float32)[:, ::3].mean(0)          # jede 3. Zeile genügt
    d = lum[:, 1:] - lum[:, :-1]
    ok = (lum[:, 1:] != 0) & (lum[:, :-1] != 0) & np.isfinite(d)
    if ok.sum() < 100:
        return None
    v = d[ok]
    return float(1.4826 * np.median(np.abs(v - np.median(v))) / np.sqrt(2))


def sample(img):
    """Vor jeder Vorschau-Rechnung verkleinern; das Original bleibt unangetastet."""
    step = max(1, math.ceil(max(img.shape[-2:]) / SIDE))
    return img[..., ::step, ::step].astype(np.float32, copy=True)


def stack_sample(state):
    # Niemals state.result() auf dem ganzen Bild nur für eine Vorschau aufrufen.
    step = max(1, math.ceil(max(state.shape[-2:]) / SIDE))
    return (state.swx[:, ::step, ::step] / np.maximum(state.sw[:, ::step, ::step], 1e-9)).astype(np.float32)


def save_picture(path, img, linear=True):
    a = sample(img)
    if linear:
        from .processing import auto_stretch
        a = auto_stretch(a, target=0.12)
    if a.shape[0] == 1:
        a = np.repeat(a, 3, axis=0)
    a = np.nan_to_num(a, nan=0.0, posinf=1.0, neginf=0.0)
    pic = Image.fromarray((np.clip(np.moveaxis(a, 0, -1)[::-1], 0, 1) * 255 + .5).astype(np.uint8))
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.tmp')
    pic.save(tmp, format='JPEG', quality=88)
    tmp.replace(path)


def read_state(work, pid):
    try:
        return json.loads((work / f'p{pid}' / 'live' / 'state.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}


class LiveRun:
    def __init__(self, work: Path, pid: int, job=None):
        self.path = work / f'p{pid}' / 'live'
        self.path.mkdir(parents=True, exist_ok=True)
        self.data = {'run': str(time.time_ns()), 'job': job, 'steps': [], 'preview': None}
        self.seq = 0
        self.last_picture = 0.0
        self.finished = False
        self.stretch, self.noise0, self.fseq = {}, {}, 0         # feste Streckung und Anfangsrauschen je Kanal
        self._lock = threading.RLock()                            # Zwischenstände entstehen in einem eigenen Thread
        self._bg, self._stopped = None, []
        self.closed = False                                       # abgelöst (neuer Lauf, Objekt gelöscht): nichts mehr schreiben
        # Alte Live-Bilder sind in Ergebnissen separat archiviert.
        for p in [*self.path.glob('*.jpg'), *self.path.glob('*.jpg.tmp')]:
            p.unlink(missing_ok=True)
        self._write()

    def _write(self):
        with self._lock:
            if self.closed:
                return
            tmp = self.path / 'state.tmp'
            tmp.write_text(json.dumps(self.data, ensure_ascii=False), encoding='utf-8')
            tmp.replace(self.path / 'state.json')

    def later(self, fn):
        """Zwischenstand im Hintergrund rechnen (ein Thread, Reihenfolge bleibt) – nicht auf dem Weg des Stackens."""
        with self._lock:
            if self.closed or self.finished:
                return None
            if self._bg is None:
                self._bg = ThreadPoolExecutor(1, thread_name_prefix='live-zwischenstand')
            return self._bg.submit(optional, fn)

    def _stop_bg(self, cancel=False):
        """Hintergrund-Thread beenden: nach „Fertig“ dürfen wartende Stände noch fertig werden (der Thread endet danach),
        beim Ablösen werden sie verworfen."""
        bg, self._bg = self._bg, None
        if bg is not None:
            bg.shutdown(wait=False, cancel_futures=cancel)
            self._stopped = [b for b in self._stopped if any(t.is_alive() for t in b._threads)] + [bg]
        return bg

    def wait(self):
        """Bis alle Zwischenstände geschrieben sind (Tests)."""
        with self._lock:
            self._stop_bg()
            pending = list(self._stopped)
        for bg in pending:
            bg.shutdown(wait=True)

    def close(self):
        """Lauf abgelöst (neuer Auftrag, Fortsetzen, Objekt gelöscht): wartende Stände verwerfen, ein noch laufender schreibt
        weder Bilder noch state.json mehr."""
        with self._lock:
            self.closed = True
            self._stop_bg(cancel=True)

    def event(self, stage, message=''):
        with self._lock:
            self._event(stage, message)

    def _event(self, stage, message=''):
        """Nur tatsächlich besuchte Schritte; erneute QC-Runden bleiben nachvollziehbar."""
        label = message or stage
        steps = self.data['steps']
        if steps and steps[-1]['stage'] == stage and steps[-1]['label'] == label:
            return
        if steps:
            steps[-1]['done'] = True
        steps.append({'stage': stage, 'label': label, 'done': stage == 'Fertig'})
        self.data['steps'] = steps[-60:]
        self.finished = stage == 'Fertig'
        self._write()
        if self.finished:
            self._stop_bg()

    def picture(self, img, *, linear=True, n=None, exposure=None, band=None, force=False):
        if not force and time.monotonic() - self.last_picture < INTERVAL:
            return
        self.seq += 1
        name = f"{self.data['run']}-{self.seq}.jpg"
        save_picture(self.path / name, img, linear)
        with self._lock:
            self.data.update(preview=name)
            if n is not None:
                self.data.update(n=n, exposure=exposure, band=band)
            self.last_picture = time.monotonic()
            self._write()
        # Zwei Bildstände reichen für den weichen Wechsel im Browser.
        for p in self.path.glob(f"{self.data['run']}-[0-9]*.jpg"):      # Zwischenstände (…-f<n>.jpg) bleiben
            if p.name not in (name, f"{self.data['run']}-{self.seq - 1}.jpg"):
                p.unlink(missing_ok=True)


    def stack_frame(self, mean, *, n, exposure=None, band=None, cov=None):
        """Echter Zwischenstand des Stacks (verkleinert, (C,h,w) Mittel) mit fester Streckung als JPG in die Folge
        `frames` (die letzten FRAMES_KEEP; der Browser blendet sie nacheinander über). cov: Gewichtsminimum, 0 = unbedeckt."""
        if self.closed:
            return
        a = np.asarray(mean, np.float32)
        if cov is not None:
            a = fill_holes(a, np.asarray(cov) > 0)
        key = band or ''
        if key not in self.stretch:
            self.stretch[key] = fixed_stretch(a)
        y = apply_stretch(a, self.stretch[key])
        if y.shape[0] == 1:
            y = np.repeat(y, 3, axis=0)
        noise = noise_of(a)
        if noise and key not in self.noise0:
            self.noise0[key] = noise
        self.fseq += 1
        name = f"{self.data['run']}-f{self.fseq}.jpg"
        pic = Image.fromarray(np.ascontiguousarray(np.moveaxis(y, 0, -1)[::-1]))
        with self._lock:
            if self.closed:                           # inzwischen abgelöst: nichts mehr ablegen
                return
            tmp = self.path / (name + '.tmp')
            pic.save(tmp, format='JPEG', quality=84)
            tmp.replace(self.path / name)
        rel = round(noise / self.noise0[key], 3) if noise and self.noise0.get(key) else None
        with self._lock:
            if self.closed:
                (self.path / name).unlink(missing_ok=True)
                return
            frames = list(self.data.get('frames') or [])
            frames.append({'name': name, 'n': int(n), 'exposure': exposure, 'band': band, 'noise': rel})
            for old in frames[:-FRAMES_KEEP]:
                (self.path / old['name']).unlink(missing_ok=True)
            self.data['frames'] = frames[-FRAMES_KEEP:]
            # n/Belichtung/Kanal stehen je Stand in `frames`; die Gesamtangaben gehören der Endbearbeitung (develop setzt sie,
            # ein verspätet fertiger Zwischenstand darf sie nicht überschreiben)
            self._write()


def fill_holes(a, covered):
    """Nur für die Anzeige: Ein Zwischenstand hat an einzelnen Pixeln kein Gewicht, wenn das Clipping (über den ganzen
    Stapel) genau die ersten Aufnahmen dort verworfen hat – etwa nach 1 Aufnahme ein Ausreißer. Solche einzelnen Lücken
    mitten im bedeckten Feld bekommen das Mittel ihrer bedeckten Nachbarn (sonst schwarze Punkte); unbedeckte Flächen
    (Bildrand nach dem Ausrichten) bleiben schwarz."""
    from scipy import ndimage as ndi
    hole = ~covered
    if not hole.any():
        return a
    m = covered.astype(np.float32)
    near = ndi.uniform_filter(m, 3, mode='constant') * 9          # bedeckte Pixel im 3×3-Fenster
    lone = hole & (near >= 5)
    out = np.where(covered[None], a, 0).astype(np.float32)
    if lone.any():
        for c in range(out.shape[0]):
            s = ndi.uniform_filter(out[c], 3, mode='constant') * 9
            out[c][lone] = s[lone] / near[lone]
    return out


def stack_targets(n0, n1):
    """Aufnahmenzahlen aus SCHEDULE im Bereich (n0, n1]."""
    return [t for t in SCHEDULE if n0 < t <= n1]


def mini_inputs(state, X, V, w):
    """Verkleinerte Kopien (jedes n-te Pixel) der Stack-Summen vor dem Stapel und der Aufnahmen des Stapels – schnell,
    damit der Rest im Hintergrund laufen kann, während der echte Stack weiterrechnet."""
    step = max(1, math.ceil(max(state.shape[-2:]) / SIDE))
    s = (slice(None), slice(None, None, step), slice(None, None, step))
    return (state.sw[s].copy(), state.swx[s].copy(), state.swx2[s].copy(),
            np.ascontiguousarray(X[:, :, ::step, ::step]), np.ascontiguousarray(V[:, ::step, ::step]), np.array(w, np.float32))


def mini_snapshots(inputs, ks, workers=1):
    """Zwischenstände „bisher + erste k Aufnahmen des Stapels“ für jedes k in ks – mit demselben Stacking-Code
    (StackState.add_batch, gleiches Clipping je Pixel), aber nur auf jedem n-ten Pixel (Vorschaugröße). Der eigentliche
    Stack bleibt unberührt (Eingaben aus mini_inputs). Gibt {k: (Mittel, Gewichtsminimum)} zurück."""
    from .stacking import StackState
    sw, swx, swx2, Xs, Vs, w = inputs
    mini = StackState(sw.shape)
    mini.sw, mini.swx, mini.swx2 = sw, swx, swx2
    mini.add_batch(Xs, Vs, w, workers=workers, prefixes=ks)
    return mini.snaps


def stack_cov(state):
    step = max(1, math.ceil(max(state.shape[-2:]) / SIDE))
    return state.sw[:, ::step, ::step].min(0)


def optional(fn, *args, **kwargs):
    """Vorschaufehler dürfen weder Stacking noch Verarbeitung gefährden."""
    try:
        return fn(*args, **kwargs)
    except Exception:
        return None
