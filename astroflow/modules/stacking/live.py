"""Kleine echte Zwischenbilder und kompakter Ablauf; keine zusätzliche Vollbild-Entwicklung."""
from __future__ import annotations

import json
import math
import threading
import time
from pathlib import Path

import numpy as np
from PIL import Image

SIDE = 800
INTERVAL = 4.0
PATTERN_INTERVAL = 3.0       # Sternmuster der Registrierung: höchstens alle 3 s eine Aufnahme


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
        self.data = {'run': str(time.time_ns()), 'job': job, 'steps': [], 'preview': None, 'pattern': None}
        self._write_lock = threading.Lock()      # Sternmuster kommen aus den Registrier-Threads
        self.seq = 0
        self.last_picture = 0.0
        self.last_pattern = 0.0
        self.pattern_seq = 0
        self._pattern_lock = threading.Lock()
        self.finished = False
        # Alte Live-Bilder sind in Ergebnissen separat archiviert.
        for p in [*self.path.glob('*.jpg'), self.path / 'pattern.json']:
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


    def want_pattern(self):
        """Genau ein Registrier-Thread darf alle PATTERN_INTERVAL Sekunden Sternmuster liefern (sonst nichts zu tun)."""
        if self.finished:
            return False
        with self._pattern_lock:
            now = time.monotonic()
            if now - self.last_pattern < PATTERN_INTERVAL:
                return False
            self.last_pattern = now
            return True

    def pattern(self, data, n=None, total=None):
        """Sternmuster in eigener Datei (nicht im gepollten state.json, dort nur die Kennung)."""
        with self._pattern_lock:
            self.pattern_seq += 1
            key = f"{self.data['run']}-{self.pattern_seq}"
        data = {**data, 'key': key, 'n': n, 'total': total}
        with self._write_lock:
            tmp = self.path / 'pattern.tmp'
            tmp.write_text(json.dumps(data, separators=(',', ':')), encoding='utf-8')
            tmp.replace(self.path / 'pattern.json')
        self.data['pattern'] = key
        self._write()


def read_pattern(work, pid):
    try:
        return json.loads((work / f'p{pid}' / 'live' / 'pattern.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None


def optional(fn, *args, **kwargs):
    """Vorschaufehler dürfen weder Stacking noch Verarbeitung gefährden."""
    try:
        return fn(*args, **kwargs)
    except Exception:
        return None
