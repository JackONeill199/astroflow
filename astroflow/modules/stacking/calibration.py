"""Master-Dark/-Bias/-Flat erzeugen und auf Light-Frames anwenden."""
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .fits_io import read_data


@dataclass
class CalFrame:
    path: str
    kind: str
    shape: tuple
    exptime: float | None
    gain: float | None
    filter: str | None
    night: str
    bayerpat: str | None = None


@dataclass
class Masters:
    dark: np.ndarray | None = None
    bias: np.ndarray | None = None
    flat: np.ndarray | None = None
    desc: str = "keine Kalibrierung"


def _combine(paths: list[str], max_n: int = 40) -> np.ndarray:
    paths = paths[:max_n]
    stack = np.stack([read_data(p) for p in paths])
    return np.median(stack, axis=0).astype(np.float32)


def _same(a, b, tol=0.05):
    if a is None or b is None:
        return a is None and b is None
    return abs(a - b) <= tol * max(abs(a), abs(b), 1e-9)


class CalibrationLibrary:
    def __init__(self, frames: list[CalFrame], cache_dir: Path):
        self.frames = frames
        self.cache = Path(cache_dir)
        self.cache.mkdir(parents=True, exist_ok=True)

    def _cached(self, paths: list[str], tag: str, build):
        h = hashlib.sha1((tag + "|".join(f"{p}:{os.path.getmtime(p)}" for p in sorted(paths))).encode()).hexdigest()[:16]
        f = self.cache / f"{tag}_{h}.npy"
        if f.exists():
            return np.load(f)
        arr = build()
        np.save(f, arr)
        return arr

    def _select(self, kind: str, shape, pred):
        return [f for f in self.frames if f.kind == kind and tuple(f.shape) == tuple(shape) and pred(f)]

    def masters_for(self, shape, exptime, gain, filt, night, bayerpat) -> Masters:
        shape = tuple(shape)
        bias_f = self._select("bias", shape, lambda f: _same(f.gain, gain, 0.01) or gain is None)
        dark_f = self._select("dark", shape, lambda f: _same(f.exptime, exptime) and (_same(f.gain, gain, 0.01) or gain is None))
        parts = []
        m = Masters()
        if bias_f:
            m.bias = self._cached([f.path for f in bias_f], "bias", lambda: _combine([f.path for f in bias_f]))
            parts.append(f"Bias×{len(bias_f)}")
        if dark_f:
            m.dark = self._cached([f.path for f in dark_f], "dark", lambda: _combine([f.path for f in dark_f]))
            parts.append(f"Dark×{len(dark_f)}")
        else:
            # Darks mit anderer Belichtungszeit skalieren (benötigt Bias)
            alt = self._select("dark", shape, lambda f: f.exptime and (_same(f.gain, gain, 0.01) or gain is None))
            if alt and m.bias is not None and exptime:
                best_t = min({f.exptime for f in alt}, key=lambda t: abs(t - exptime))
                sel = [f for f in alt if f.exptime == best_t]
                d = self._cached([f.path for f in sel], "dark", lambda: _combine([f.path for f in sel]))
                m.dark = m.bias + (d - m.bias) * (exptime / best_t)
                parts.append(f"Dark×{len(sel)} (skaliert)")
        flat_f = self._select("flat", shape, lambda f: (f.filter or "") == (filt or ""))
        if flat_f:
            nights = sorted({f.night for f in flat_f}, key=lambda n: abs(_ord(n) - _ord(night)))
            sel = [f for f in flat_f if f.night == nights[0]]
            m.flat = self._cached([f.path for f in sel], "flat", lambda: self._build_flat(sel, m, bayerpat))
            parts.append(f"Flat×{len(sel)}")
        m.desc = ", ".join(parts) or "keine Kalibrierung"
        return m

    def _build_flat(self, sel: list[CalFrame], m: Masters, bayerpat: str | None) -> np.ndarray:
        frames = []
        for f in sel[:40]:
            a = read_data(f.path)
            if m.bias is not None and a.shape == m.bias.shape:
                a = a - m.bias
            frames.append(a)
        flat = np.median(np.stack(frames), axis=0).astype(np.float32)
        return normalize_flat(flat, bayerpat)


def normalize_flat(flat: np.ndarray, bayerpat: str | None) -> np.ndarray:
    flat = flat.copy()
    if flat.ndim == 2 and bayerpat:
        for i in range(2):
            for j in range(2):
                sub = flat[i::2, j::2]
                sub /= max(float(np.mean(sub)), 1e-6)
    elif flat.ndim == 3:
        for c in range(flat.shape[0]):
            flat[c] /= max(float(np.mean(flat[c])), 1e-6)
    else:
        flat /= max(float(np.mean(flat)), 1e-6)
    return np.clip(flat, 0.2, None)


def _ord(night: str) -> int:
    import datetime as dt
    try:
        return dt.date.fromisoformat(night).toordinal()
    except ValueError:
        return 0


def _upper_median(a: np.ndarray) -> np.ndarray:
    """Median über Achse 0 wie ndimage.median_filter: bei gerader Anzahl der obere der beiden Mittelwerte."""
    return np.partition(a, a.shape[0] // 2, axis=0)[a.shape[0] // 2]


def _fix_plane(p: np.ndarray, k: float) -> int:
    """Ersetzt isolierte heiße/kalte Pixel einer Ebene durch den Median der 8 Nachbarn (in-place).
    Der teure Median wird nur an Kandidaten berechnet: Aus den Kriterien unten folgt, dass ein Hotpixel seinen
    hellsten Nachbarn um mehr als (0,8·k − 3)·σ überragt (Coldpixel analog) – dieser Vorfilter ist exakt."""
    pp = np.pad(p, 1, mode="reflect")                 # wie mode="mirror" der ndimage-Filter
    H, W = p.shape
    nbs = [pp[1 + dy : 1 + dy + H, 1 + dx : 1 + dx + W] for dy in (-1, 0, 1) for dx in (-1, 0, 1) if dy or dx]
    nmax, nmin = nbs[0].copy(), nbs[0].copy()
    for a in nbs[1:]:
        np.maximum(nmax, a, out=nmax)
        np.minimum(nmin, a, out=nmin)
    # Rauschen aus einer Stichprobe der Abweichung vom Nachbar-Median
    sub = (slice(None, None, 3), slice(None, None, 3))
    s = p[sub] - _upper_median(np.stack([a[sub] for a in nbs]))
    sig = float(1.4826 * np.median(np.abs(s - np.median(s)))) + 1e-6
    lim = (0.8 * k - 3) * sig
    cy, cx = np.nonzero((p - nmax > lim) | (nmin - p > lim))
    if len(cy) == 0:
        return 0
    med = _upper_median(np.stack([a[cy, cx] for a in nbs]))
    v = p[cy, cx]
    diff = v - med
    nb_hi, nb_lo = nmax[cy, cx] - med, med - nmin[cy, cx]
    # Sterne haben helle Nachbarn, Hotpixel nicht: hellster Nachbar muss deutlich unter dem Pixel liegen
    bad = ((diff > k * sig) & (nb_hi < 0.2 * diff + 3 * sig)) | ((diff < -k * sig) & (nb_lo < -0.2 * diff + 3 * sig))
    p[cy[bad], cx[bad]] = med[bad]
    return int(bad.sum())


def cosmetic(raw: np.ndarray, bayer: bool, k: float = 8.0) -> np.ndarray:
    """Kosmetische Korrektur (Hot-/Cold-Pixel) – wichtig ohne Darks, sonst richtet die Registrierung
    die Bilder an den ortsfesten Sensorfehlern statt an den Sternen aus. Bei CFA je Farbebene."""
    out = np.array(raw, dtype=np.float32, copy=True)
    if out.ndim == 3:
        for c in range(out.shape[0]):
            _fix_plane(out[c], k)
    elif bayer:
        for dy in (0, 1):
            for dx in (0, 1):
                plane = out[dy::2, dx::2].copy()
                _fix_plane(plane, k)
                out[dy::2, dx::2] = plane
    else:
        _fix_plane(out, k)
    return out


def apply(raw: np.ndarray, m: Masters) -> np.ndarray:
    out = raw
    if m.dark is not None and m.dark.shape == raw.shape:
        out = out - m.dark
    elif m.bias is not None and m.bias.shape == raw.shape:
        out = out - m.bias
    if m.flat is not None and m.flat.shape == raw.shape:
        out = out / m.flat
    return out.astype(np.float32, copy=False)


# ------------------------------------------------------------- Sensorglühen #
# Ohne Darks bleibt das ortsfeste Randglühen des Sensors (Stellina: oben/unten bis ~80 ADU) in den Lights. Bei Alt-Az-Geräten
# dreht sich das Feld zwischen den Aufnahmen – nach dem Ausrichten auf die Sterne verschmiert das Glühen zu Schlieren und
# Bändern, die sich nach dem Stacken nicht mehr sauber entfernen lassen. Deshalb vorher, in Sensorkoordinaten:
# Zeilen-/Spaltenprofil des Hintergrunds je Farbebene aus vielen Lights einer Nacht (Sterne wandern und fallen über
# Perzentil + Median heraus) messen und nur in den Randbändern abziehen – die Bildmitte mit dem Objekt bleibt unberührt.
GLOW_BAND = 0.25          # Randanteil, in dem korrigiert wird (zur Mitte hin weich ausgeblendet)
GLOW_MIN_FRAMES = 12


def _planes(raw: np.ndarray, bayer: bool) -> list[tuple]:
    """Index-Ausschnitte der Farbebenen: CFA 2x2, Farbbild je Kanal, Mono eine Ebene."""
    if raw.ndim == 3:
        return [(c, slice(None), slice(None)) for c in range(raw.shape[0])]
    if bayer:
        return [(None, slice(dy, None, 2), slice(dx, None, 2)) for dy in (0, 1) for dx in (0, 1)]
    return [(None, slice(None), slice(None))]


def _plane(raw, idx):
    c, ys, xs = idx
    return raw[c][ys, xs] if c is not None else raw[ys, xs]


def glow_profiles(raw: np.ndarray, bayer: bool) -> list[tuple[np.ndarray, np.ndarray]]:
    """Je Farbebene (Zeilenprofil, Spaltenprofil) des Hintergrunds (30-%-Perzentil: robust gegen Sterne und Nebel)."""
    return [(np.percentile(p, 30, axis=1).astype(np.float32), np.percentile(p, 30, axis=0).astype(np.float32))
            for p in (_plane(raw, idx) for idx in _planes(raw, bayer))]


def _taper(n: int) -> np.ndarray:
    b = max(4, int(GLOW_BAND * n))
    d = np.minimum(np.arange(n), n - 1 - np.arange(n))
    return np.where(d < b, 0.5 * (1 + np.cos(np.pi * d / b)), 0.0).astype(np.float32)


def median_profiles(profiles: list[list[tuple[np.ndarray, np.ndarray]]]) -> list[tuple[np.ndarray, np.ndarray]] | None:
    """Median der Profile vieler Frames je Ebene – das ortsfeste Muster einer Nacht. None bei zu wenigen Frames."""
    if len(profiles) < GLOW_MIN_FRAMES:
        return None
    return [tuple(np.median([p[k][axis] for p in profiles], axis=0).astype(np.float32) for axis in (0, 1))
            for k in range(len(profiles[0]))]


def model_from(medians: list[tuple[np.ndarray, np.ndarray]] | None) -> list[tuple[np.ndarray, np.ndarray]] | None:
    """Abzugsmodell: auf das Niveau der Bildmitte bezogen, geglättet, nur in den Randbändern."""
    from scipy.ndimage import gaussian_filter1d

    if not medians:
        return None
    model = []
    for pl in medians:
        out = []
        for v in pl:
            n = len(v)
            v = gaussian_filter1d(v - np.median(v[n // 3 : 2 * n // 3]), 3, mode="nearest")
            out.append((v * _taper(n)).astype(np.float32))
        model.append(tuple(out))
    return model


def glow_model(profiles):
    return model_from(median_profiles(profiles))


class GlowLibrary:
    """Glüh-Datensatz je Kamera (Gerät + Bildformat + Gain + Belichtung + Filter), über alle Nächte gesammelt.

    Jede verarbeitete Nacht mit genug Frames legt ihr Medianprofil ab (wenige KB, bleibt nach dem Löschen der Rohbilder).
    Für eine Nacht wird ihr eigenes Profil genutzt; fehlt es (zu wenige Frames), der Median aller Nächte dieser Kamera.
    So lernt AstroFlow das Glühen jedes Teleskops aus dessen eigenen Aufnahmen – nichts ist fest eingebaut oder erfunden."""

    def __init__(self, root: Path):
        self.root = Path(root)

    @staticmethod
    def key(info: dict) -> str:
        shape = "x".join(str(v) for v in info.get("shape") or [])
        raw = f"{info.get('instrument') or 'kamera'}|{shape}|g{info.get('gain')}|e{info.get('exptime')}|{info.get('filter') or ''}"
        return hashlib.sha1(raw.encode()).hexdigest()[:12]

    def _file(self, key: str) -> Path:
        return self.root / f"{key}.npz"

    def _read(self, key: str) -> dict:
        f = self._file(key)
        if not f.exists():
            return {}
        with np.load(f, allow_pickle=False) as z:
            out: dict = {}
            for name in z.files:                        # n<night>_<ebene>_<achse>
                night, k, ax = name[1:].rsplit("_", 2)
                out.setdefault(night, {}).setdefault(int(k), [None, None])[int(ax)] = z[name]
        return {n: [tuple(v[k]) for k in sorted(v)] for n, v in out.items()}

    def nights(self, key: str) -> list[str]:
        return sorted(self._read(key))

    def add(self, key: str, night: str, medians: list[tuple[np.ndarray, np.ndarray]]) -> None:
        data = self._read(key)
        data[night] = medians
        self.root.mkdir(parents=True, exist_ok=True)
        arrays = {f"n{n}_{k}_{ax}": pl[ax] for n, planes in data.items() for k, pl in enumerate(planes) for ax in (0, 1)}
        tmp = self._file(key).with_suffix(".tmp.npz")
        np.savez_compressed(tmp, **arrays)
        os.replace(tmp, self._file(key))

    def medians(self, key: str, night: str) -> tuple[list | None, str]:
        """(Medianprofile, Quelle) – eigene Nacht, sonst Median aller Nächte der Kamera."""
        data = self._read(key)
        if night in data:
            return data[night], "nacht"
        if not data:
            return None, ""
        planes = list(data.values())
        try:
            return [tuple(np.median([p[k][ax] for p in planes], axis=0) for ax in (0, 1)) for k in range(len(planes[0]))], f"bibliothek ({len(planes)} Nächte)"
        except ValueError:                                # uneinheitliche Formate
            return None, ""


def glow_amplitude(model) -> float:
    """Stärkstes Randglühen des Modells (ADU) – für Protokoll und Tests."""
    return float(max(max(np.abs(r).max(), np.abs(c).max()) for r, c in model)) if model else 0.0


def remove_glow(raw: np.ndarray, bayer: bool, model) -> np.ndarray:
    if not model:
        return raw
    out = np.array(raw, dtype=np.float32, copy=True)
    for (c, ys, xs), (r, col) in zip(_planes(out, bayer), model):
        tgt = out[c] if c is not None else out
        p = tgt[ys, xs]
        if p.shape != (len(r), len(col)):
            return raw                                   # anderes Bildformat -> Modell passt nicht
        tgt[ys, xs] = p - r[:, None] - col[None, :]
    return out
