"""Kleine echte Zwischenbilder und kompakter Ablauf; keine zusätzliche Vollbild-Entwicklung."""
from __future__ import annotations

import io
import json
from collections import deque
import math
import threading
import time
from pathlib import Path

import numpy as np
from PIL import Image

SIDE = 800
INTERVAL = 4.0
PULSES = 64                  # Aufnahmen-Ebenen der Registrierung: Ringpuffer der letzten Aufnahmen (nur im Speicher)
THUMB = 160                  # Mini-Vorschau je Aufnahme (längste Seite, px)


def sample(img):
    """Vor jeder Vorschau-Rechnung verkleinern; das Original bleibt unangetastet."""
    step = max(1, math.ceil(max(img.shape[-2:]) / SIDE))
    return img[..., ::step, ::step].astype(np.float32, copy=True)


def stack_sample(state):
    # Niemals state.result() auf dem ganzen Bild nur für eine Vorschau aufrufen.
    step = max(1, math.ceil(max(state.shape[-2:]) / SIDE))
    return (state.swx[:, ::step, ::step] / np.maximum(state.sw[:, ::step, ::step], 1e-9)).astype(np.float32)


def thumb(img, side=THUMB):
    """Mini-Vorschau einer Aufnahme als JPEG-Bytes (~3 KB): nur jedes n-te Pixel, schnelle Streckung, Norden oben wie
    die übrigen Vorschauen. Rechnet auf ≤ side² Pixeln – das Original bleibt unangetastet."""
    step = max(1, math.ceil(max(img.shape[-2:]) / side))
    a = np.asarray(img[..., ::step, ::step], np.float32)
    lum = a.mean(0)
    lo, hi = np.percentile(lum, (30, 99.8))
    a = np.clip((a - lo) / max(float(hi - lo), 1e-6), 0, 1)
    a = np.arcsinh(a * 12) / np.arcsinh(12)
    if a.shape[0] == 1:
        a = np.repeat(a, 3, axis=0)
    pic = Image.fromarray((np.moveaxis(a, 0, -1)[::-1] * 255 + .5).astype(np.uint8))
    buf = io.BytesIO()
    pic.save(buf, format='JPEG', quality=72)
    return buf.getvalue()


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
        self._write_lock = threading.Lock()      # Sternmuster kommen aus den Registrier-Threads
        self.seq = 0
        self.last_picture = 0.0
        self.pulses = deque(maxlen=PULSES)
        self.pulse_seq = 0
        self.pulse_shape = None
        self._pulse_lock = threading.Lock()
        self.finished = False
        # Alte Live-Bilder sind in Ergebnissen separat archiviert.
        for p in self.path.glob('*.jpg'):
            p.unlink(missing_ok=True)
        self._write()

    def _write(self):
        with self._write_lock:
            tmp = self.path / 'state.tmp'
            tmp.write_text(json.dumps(self.data, ensure_ascii=False), encoding='utf-8')
            tmp.replace(self.path / 'state.json')

    def event(self, stage, message=''):
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

    def picture(self, img, *, linear=True, n=None, exposure=None, band=None, force=False):
        if not force and time.monotonic() - self.last_picture < INTERVAL:
            return
        self.seq += 1
        name = f"{self.data['run']}-{self.seq}.jpg"
        save_picture(self.path / name, img, linear)
        self.data.update(preview=name)
        if n is not None:
            self.data.update(n=n, exposure=exposure, band=band)
        self.last_picture = time.monotonic()
        self._write()
        # Zwei Bildstände reichen für den weichen Wechsel im Browser.
        for p in self.path.glob(f"{self.data['run']}-*.jpg"):
            if p.name not in (name, f"{self.data['run']}-{self.seq - 1}.jpg"):
                p.unlink(missing_ok=True)


    def add_pulse(self, data, n=None, total=None, shape=None, thumb=None):
        """Ausrichtung einer registrierten Aufnahme (aus den Registrier-Threads, ~3 KB mit Mini-Vorschau, keine Datei)."""
        with self._pulse_lock:
            self.pulse_seq += 1
            self.pulses.append({**data, 'i': self.pulse_seq, 'n': n, 't': total, 'img': bool(thumb), '_thumb': thumb})
            if shape is not None:
                self.pulse_shape = [int(shape[1]), int(shape[0])]

    def pulses_since(self, seit=0):
        with self._pulse_lock:
            if seit > self.pulse_seq:            # Nummer aus einem früheren Lauf
                seit = 0
            return {'run': self.data['run'], 'job': self.data.get('job'), 'seq': self.pulse_seq,
                    'wh': self.pulse_shape,
                    'items': [{k: v for k, v in d.items() if k != '_thumb'} for d in self.pulses if d['i'] > seit]}

    def pulse_thumb(self, i):
        with self._pulse_lock:
            return next((d['_thumb'] for d in self.pulses if d['i'] == i), None)


def stage_list(work, pid):
    """Gespeicherte Stacking-Stufen dieses Objekts (work/p<id>/stufen[_<Band>]/n<t>.jpg + .json) für die Zeitachse."""
    out = []
    for d in sorted((work / f'p{pid}').glob('stufen*')):
        band = d.name.removeprefix('stufen').removeprefix('_')
        for j in sorted(d.glob('n*.json')):
            if not j.with_suffix('.jpg').is_file():
                continue
            try:
                info = json.loads(j.read_text(encoding='utf-8'))
            except (OSError, ValueError):
                continue
            out.append({'n': int(info.get('n') or 0), 'exposure': info.get('exposure'), 'band': band, 'file': j.with_suffix('.jpg').name})
    return sorted(out, key=lambda x: (x['band'], x['n']))


def optional(fn, *args, **kwargs):
    """Vorschaufehler dürfen weder Stacking noch Verarbeitung gefährden."""
    try:
        return fn(*args, **kwargs)
    except Exception:
        return None
