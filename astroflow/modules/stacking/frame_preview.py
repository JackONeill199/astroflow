"""Kleine Vorschaubilder der einzelnen Aufnahmen (Reiter „Aufnahmen“).

Entstehen bei der Frame-Analyse, solange das Rohbild noch da ist (hochgeladene Rohbilder werden nach dem Stacken gelöscht),
als JPEG mit höchstens `MAX_EDGE` px Kante und einfacher Autostreckung unter `work/frame_previews/<frame-id>.jpg`.
Fehlende Vorschauen älterer Aufnahmen holt der Endpunkt beim ersten Abruf nach, falls die Rohdatei noch existiert –
ohne Neu-Analyse. Löschbar unter Einstellungen → Speicherbelegung (`housekeeping`).
"""
from __future__ import annotations

import io
import os
import sys
import tempfile
import threading
from pathlib import Path

import numpy as np

DIR_NAME = "frame_previews"
MAX_EDGE = 480          # längste Kante in px – reicht für das Popup, hält die Datei bei ~15–40 KB
QUALITY = 65


def enabled() -> bool:
    return os.environ.get("ASTROFLOW_FRAME_PREVIEWS", "1") != "0"


def folder(work_dir: Path) -> Path:
    return Path(work_dir) / DIR_NAME


def path_for(work_dir: Path, fid: str) -> Path:
    if not fid or not all(c.isalnum() or c in "-_" for c in fid):   # Frame-IDs sind Hashes – nie Pfadteile
        raise ValueError("ungültige Aufnahme")
    return folder(work_dir) / f"{fid}.jpg"


def existing(work_dir: Path) -> set[str]:
    """Frame-IDs mit vorhandener Vorschau (ein Verzeichnislauf statt je Aufnahme ein stat)."""
    try:
        return {n[:-4] for n in os.listdir(folder(work_dir)) if n.endswith(".jpg")}
    except OSError:
        return set()


def _channels(raw: np.ndarray, bayerpat: str | None) -> np.ndarray:
    """(C,h,w) in halber Auflösung bei Bayer-Rohbildern (Superpixel R, mittleres G, B) – kein teures Debayering."""
    if raw.ndim == 3:
        return raw
    if not bayerpat or len(bayerpat) != 4:
        return raw[None]
    h, w = raw.shape[0] // 2 * 2, raw.shape[1] // 2 * 2
    sub = {}
    for i, c in enumerate(bayerpat.upper()):
        sub.setdefault(c, []).append(raw[i // 2 : h : 2, i % 2 : w : 2])
    if set(sub) != {"R", "G", "B"}:
        return raw[None]
    return np.stack([sub["R"][0], np.mean(sub["G"], axis=0), sub["B"][0]]).astype(np.float32)


def render(raw: np.ndarray, bayerpat: str | None = None) -> bytes:
    """Rohbild → JPEG-Bytes (verkleinert, Hintergrund neutral, verknüpft gestreckt)."""
    from PIL import Image

    from .processing import auto_stretch
    img = _channels(np.asarray(raw, np.float32), bayerpat)
    c, h, w = img.shape
    f = max(1, int(np.ceil(max(h, w) / MAX_EDGE)))
    hh, ww = h // f, w // f
    img = img[:, : hh * f, : ww * f].reshape(c, hh, f, ww, f).mean((2, 4))     # Blockmittel: rauscharm, schnell
    img = np.nan_to_num(img)
    if c == 3:                                        # Himmelshintergrund grau statt grünstichig (Bayer: doppelt so viel Grün)
        med = np.median(img.reshape(3, -1), axis=1)
        img = img - med[:, None, None] + med.mean()
    out = auto_stretch(img if c == 3 else img[0][None].repeat(3, 0))
    a = (np.clip(out, 0, 1) * 255 + 0.5).astype(np.uint8)
    pic = Image.fromarray(np.moveaxis(a, 0, -1)) if c == 3 else Image.fromarray(a[0], "L")
    buf = io.BytesIO()
    pic.save(buf, "JPEG", quality=QUALITY, optimize=True, progressive=True)
    return buf.getvalue()


def save(work_dir: Path, fid: str, raw: np.ndarray, bayerpat: str | None = None) -> int:
    """Vorschau schreiben (atomar). Gibt die Dateigröße zurück."""
    p = path_for(work_dir, fid)
    data = render(raw, bayerpat)
    p.parent.mkdir(parents=True, exist_ok=True)
    # eigene Temp-Datei je Schreibvorgang: zwei gleichzeitige Nachholungen derselben Aufnahme (zwei Geräte, Analyse + Abruf)
    # teilten sich sonst „<id>.tmp“ – der zweite os.replace fand sie nicht mehr
    fd, tmp = tempfile.mkstemp(prefix=f"{fid}.", suffix=".tmp", dir=p.parent)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        with _lock(fid):                      # Windows: os.replace auf eine gerade ersetzte Datei → PermissionError
            os.replace(tmp, p)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        if isinstance(sys.exc_info()[1], PermissionError) and p.exists():   # anderer Prozess schrieb dieselbe Vorschau
            return len(data)
        raise
    return len(data)


_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def _lock(fid: str) -> threading.Lock:
    with _LOCKS_GUARD:
        if len(_LOCKS) > 4096:
            _LOCKS.clear()
        return _LOCKS.setdefault(fid, threading.Lock())


def ensure(work_dir: Path, fid: str, path: str | None, bayerpat: str | None) -> Path | None:
    """Vorhandene Vorschau oder – solange die Rohdatei existiert – jetzt aus ihr erzeugen (ohne Kalibrierung)."""
    p = path_for(work_dir, fid)
    if p.exists():
        return p
    if not path or not enabled() or not Path(path).is_file():
        return None
    from .fits_io import read_data
    save(work_dir, fid, read_data(path), bayerpat)
    return p
