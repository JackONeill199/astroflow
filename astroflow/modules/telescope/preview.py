"""Vorschaubild für die Teleskop-Übersicht.

Quelle 1: der Treiber (`Driver.preview()`, Fähigkeit „preview“) – das fertige Geräte-Bild als JPEG.
Quelle 2 (Rückfall): die zuletzt per FTP geholte Rohbild-Datei (`Collector.last`), lokal gestreckt und als JPEG gerendert.
Ausgeliefert wird nur, was hier erzeugt wurde – nie ein vom Client genannter Pfad. Kurzer Zwischenspeicher: die Ansicht fragt
nur bei geänderter Signatur nach dem Bild.
"""
from __future__ import annotations

import io
import threading
import time
import traceback

TTL = 3.0           # Sekunden, in denen das Ergebnis ohne neue Anfrage an Gerät/Platte wiederverwendet wird
MAX_W = 960
_lock = threading.Lock()


def _render_fits(path) -> bytes:
    import numpy as np
    from astropy.io import fits
    from PIL import Image
    a = np.asarray(fits.getdata(path), dtype=np.float32)
    while a.ndim > 2:
        a = a[0]
    k = max(1, a.shape[1] // MAX_W) * 2                 # Blockmittel: nebenbei Bayer-Muster mitteln, Bild verkleinern
    h, w = (a.shape[0] // k) * k, (a.shape[1] // k) * k
    a = a[:h, :w].reshape(h // k, k, w // k, k).mean(axis=(1, 3))
    lo, hi = np.percentile(a, (1.0, 99.7))
    a = np.arcsinh((a - lo) / max(hi - lo, 1e-6) * 12.0) / np.arcsinh(12.0)
    im = Image.fromarray((np.clip(a, 0, 1) * 255).astype(np.uint8), "L").convert("RGB")
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=82)
    return buf.getvalue()


def _build(unit):
    drv = unit.driver
    if "preview" in getattr(drv, "capabilities", ()):
        got = drv.preview()
        if got:
            data, ts = got
            return {"jpeg": data, "ts": float(ts), "source": "device", "n": unit.pilot.state.frames or 0}
        return None
    col = unit.collector
    path = col.last if col else None
    if not path or not path.is_file():
        return None
    st = path.stat()
    cache = getattr(unit, "_pv_fits", None)
    if cache and cache[0] == (str(path), st.st_mtime_ns):
        data = cache[1]
    else:
        data = _render_fits(path)
        unit._pv_fits = ((str(path), st.st_mtime_ns), data)
    return {"jpeg": data, "ts": st.st_mtime, "source": "fits", "n": col.count}


def get(unit) -> dict | None:
    """Aktuelle Vorschau {jpeg, ts, source, n, sig} oder None. Fehler → None (ruhiger Platzhalter)."""
    with _lock:
        c = getattr(unit, "_pv", None)
        now = time.monotonic()
        if c and now - c[0] < TTL:
            return c[1]
        try:
            res = _build(unit)
        except Exception:
            traceback.print_exc()
            res = None
        if res:
            res["sig"] = f"{int(res['ts'])}-{res['n']}-{len(res['jpeg'])}"
        unit._pv = (now, res)
        return res
