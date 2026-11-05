"""Master-Dark/-Bias/-Flat erzeugen und auf Light-Frames anwenden."""
from __future__ import annotations

import hashlib
import os
import threading
import warnings
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .fits_io import read_data


class SourceOffline(ValueError):
    """Am Ort eingebundene Dateien gerade nicht erreichbar (z. B. USB-Platte abgesteckt). dirs: fehlende Ordner – der
    Auftrag wartet, bis sie wieder da sind (`Engine.pending_projects`), statt als endgültiger Fehler liegen zu bleiben."""

    def __init__(self, msg: str, dirs):
        super().__init__(msg)
        self.dirs = sorted({str(d) for d in dirs})


def offline_error(what: str, paths) -> SourceOffline:
    dirs = sorted({str(Path(p).parent) for p in paths})
    where = dirs[0] + (f" (und {len(dirs) - 1} weitere Ordner)" if len(dirs) > 1 else "") if dirs else ""
    return SourceOffline(f"{what} liegen in einem Ordner, der nicht erreichbar ist ({where}) – z. B. USB-Platte "
                         "abgesteckt. Bitte anschließen; die Verarbeitung läuft dann automatisch weiter.", dirs)


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
        self.lock = threading.Lock()

    def _cached(self, paths: list[str], tag: str, build):
        ps = sorted(paths)
        # Verweis Pfadliste -> zuletzt gebauter Master: so bleibt der Master nutzbar, wenn die Kalibrierbilder auf einer
        # abgesteckten Platte liegen (der eigentliche Schlüssel braucht ihre Änderungszeit)
        ref = self.cache / f"{tag}_{hashlib.sha1((tag + '|'.join(ps)).encode()).hexdigest()[:16]}.ref"
        try:
            h = hashlib.sha1((tag + "|".join(f"{p}:{os.path.getmtime(p)}" for p in ps)).encode()).hexdigest()[:16]
        except OSError:
            last = self.cache / ref.read_text().strip() if ref.exists() else None
            if last and last.exists():
                return np.load(last)
            raise offline_error("Kalibrierbilder", [p for p in ps if not os.path.exists(p)]) from None
        f = self.cache / f"{tag}_{h}.npy"
        with self.lock:                     # parallele Analyse-Threads: einmal bauen, nie halb geschriebene Dateien lesen
            if f.exists():
                arr = np.load(f)
            else:
                arr = build()
                np.save(f, arr)
            if not ref.exists() or ref.read_text().strip() != f.name:
                ref.write_text(f.name)
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


# ----------------------------------------------------- ortsfeste Sensorfehler #
# cosmetic() schützt alles mit hellem Nachbarn als „Stern“ – auch Hotpixel-Paare und -Grüppchen. Beleg NGC 7293 (Stellina,
# 293 Frames): zwei übereinanderliegende R-Pixel mit je ~42 000 ADU blieben stehen. Die Stellina verharrt zwischen ihren
# Versätzen einige Aufnahmen auf derselben Stelle; so landete das Paar 2–6-mal auf derselben Himmelsstelle, das Clipping im
# Stapel verwarf es dort nicht → Gruppe aus ~12 roten Punkten (je Zeigegruppe einer). Hier: Fehlerkarte je Kamera und Nacht
# aus einer Stichprobe von Lights. Kandidat je Aufnahme = deutlich über/unter dem Nachbar-Median der Farbebene mit höchstens
# zwei hellen Nachbarn (Paar/Dreier); Sterne haben mehr helle Nachbarn und bei CFA auch helle Pixel der anderen Farben.
# Ortsfest = in mindestens DEFECT_FRAC der Stichprobe Kandidat (Sterne wandern zwischen den Aufnahmen weiter).
DEFECT_K = 5.0
DEFECT_FRAC = 0.5
DEFECT_MIN_FRAMES = 12
DEFECT_MAX_SHARE = 0.01   # mehr als 1 % der Pixel „ortsfest“: Messung unplausibel (z. B. Feld steht still) → keine Karte


def _neighbours(p: np.ndarray) -> np.ndarray:
    pp = np.pad(p, 1, mode="reflect")
    H, W = p.shape
    return np.stack([pp[1 + dy : 1 + dy + H, 1 + dx : 1 + dx + W] for dy in (-1, 0, 1) for dx in (-1, 0, 1) if dy or dx])


def defect_candidates(raw: np.ndarray, bayer: bool, k: float = DEFECT_K, fixed: np.ndarray | None = None) -> np.ndarray:
    """Flache Indizes (in raw) der Pixel, die in dieser Aufnahme wie ein Hot-/Coldpixel bzw. Teil eines kleinen
    Fehlergrüppchens aussehen. Großzügig (5σ) – erst die Häufigkeit über viele Aufnahmen entscheidet.
    fixed: von cosmetic() in dieser Aufnahme schon ersetzte Pixel – zählen mit (warme Pixel um die Kosmetik-Schwelle
    werden sonst mal dort, mal hier erfasst und erreichen in keiner der beiden Listen die nötige Häufigkeit)."""
    raw = np.asarray(raw, np.float32)
    cfa = bayer and raw.ndim == 2
    H, W = raw.shape[-2:]
    fx = np.zeros(raw.shape, bool)
    if fixed is not None and len(fixed):
        fx.flat[np.asarray(fixed, np.int64)] = True
    exc = np.zeros(raw.shape, np.float32) if cfa else None
    found = []
    for idx in _planes(raw, cfa):
        p = _plane(raw, idx)
        part = np.partition(_neighbours(p), (2, 4, 5), axis=0)
        med = part[4]                                   # oberer Median der 8 Nachbarn (wie _fix_plane)
        d = p - med
        s = d[::3, ::3]
        sig = float(1.4826 * np.median(np.abs(s - np.median(s)))) + 1e-6
        # höchstens zwei helle (bzw. dunkle) Nachbarn: Index 5 = drittgrößter, 2 = drittkleinster
        hot = (d > k * sig) & (part[5] - med < 0.3 * d)
        cold = (d < -k * sig) & (med - part[2] < -0.3 * d)
        # von cosmetic() ersetzt (Wert schon geglättet): nur ohne drei helle Nachbarn – sonst war es eine Sternspitze
        f = _plane(fx, idx) & ~hot & ~cold & (part[5] - med < 3 * sig)
        if cfa:
            exc[idx[1], idx[2]] = d
        found.append((idx, hot, cold, f, sig))
    out = []
    for (c, ys, xs), hot, cold, f, sig in found:
        cy, cx = np.nonzero(hot | cold | f)
        if len(cy) == 0:
            continue
        y = cy * (ys.step or 1) + (ys.start or 0)
        x = cx * (xs.step or 1) + (xs.start or 0)
        if cfa:
            # Stern: auch die direkten Nachbarn anderer Farbe liegen über ihrer Umgebung; ein Hotpixel sitzt nur in einer Farbe
            n4 = np.mean([exc[np.clip(y + dy, 0, H - 1), np.clip(x + dx, 0, W - 1)]
                          for dy, dx in ((-1, 0), (1, 0), (0, -1), (0, 1))], axis=0)   # Mittel aus 4: Rauschen ≈ σ/2
            star = (hot[cy, cx] & (n4 > np.maximum(0.15 * exc[y, x], sig))) | (f[cy, cx] & (n4 > 2 * sig))
            y, x = y[~star], x[~star]
        flat = np.ravel_multi_index((y, x), (H, W))
        if c is not None:
            flat = flat + c * H * W
        out.append(flat)
    return np.concatenate(out).astype(np.int64) if out else np.zeros(0, np.int64)


def defect_map(cands: list[np.ndarray], size: int) -> np.ndarray | None:
    """Ortsfeste Fehler aus den Kandidaten vieler Aufnahmen (≥ DEFECT_FRAC). None = unplausibel viele."""
    if len(cands) < DEFECT_MIN_FRAMES:
        return None
    u, n = np.unique(np.concatenate(cands) if cands else np.zeros(0, np.int64), return_counts=True)
    bad = u[n >= int(np.ceil(DEFECT_FRAC * len(cands)))]
    if bad.size > DEFECT_MAX_SHARE * size:
        return None
    return bad.astype(np.int64)


def fix_defects(raw: np.ndarray, bayer: bool, idx: np.ndarray) -> np.ndarray:
    """Ersetzt die Pixel `idx` (flach) durch den Median ihrer 8 Nachbarn derselben Farbebene, ohne andere Fehlerpixel."""
    if idx is None or len(idx) == 0:
        return raw
    out = np.array(raw, np.float32, copy=True)
    idx = idx[idx < out.size]
    H, W = out.shape[-2:]
    step = 2 if bayer and out.ndim == 2 else 1
    c, rest = np.divmod(idx, H * W)
    y, x = np.divmod(rest, W)
    bad = np.zeros(out.size, bool)
    bad[idx] = True
    flat = out.reshape(-1)
    vals = []
    for dy in (-step, 0, step):
        for dx in (-step, 0, step):
            if not (dy or dx):
                continue
            yy, xx = y + dy, x + dx
            ok = (yy >= 0) & (yy < H) & (xx >= 0) & (xx < W)
            j = c * H * W + np.clip(yy, 0, H - 1) * W + np.clip(xx, 0, W - 1)
            v = flat[j].copy()
            v[~ok | bad[j]] = np.nan
            vals.append(v)
    with warnings.catch_warnings():                  # alle Nachbarn ebenfalls Fehler: Pixel bleibt
        warnings.simplefilter("ignore", RuntimeWarning)
        med = np.nanmedian(np.stack(vals), axis=0)
    keep = np.isfinite(med)
    flat[idx[keep]] = med[keep]
    return out


class DefectLibrary:
    """Ortsfeste Sensorfehler je Kamera (Schlüssel wie GlowLibrary) und Nacht, als flache Pixelindizes (wenige KB).
    Bleibt nach dem Löschen der Rohbilder; Nächte ohne eigene Messung nutzen die Pixel, die in mindestens der Hälfte
    der gemessenen Nächte der Kamera auftreten."""

    def __init__(self, root: Path):
        self.root = Path(root)

    def _file(self, key: str) -> Path:
        return self.root / f"{key}.npz"

    def _read(self, key: str) -> dict[str, np.ndarray]:
        f = self._file(key)
        if not f.exists():
            return {}
        try:
            with np.load(f, allow_pickle=False) as z:
                return {name[1:]: z[name] for name in z.files}
        except (OSError, ValueError):
            return {}

    def nights(self, key: str) -> list[str]:
        return sorted(self._read(key))

    def add(self, key: str, night: str, idx: np.ndarray) -> None:
        data = self._read(key)
        data[night] = np.asarray(idx, np.int64)
        self.root.mkdir(parents=True, exist_ok=True)
        tmp = self._file(key).with_suffix(".tmp.npz")
        np.savez_compressed(tmp, **{f"n{n}": a for n, a in data.items()})
        os.replace(tmp, self._file(key))

    def get(self, key: str, night: str) -> tuple[np.ndarray | None, str]:
        data = self._read(key)
        if night in data:
            return data[night], "nacht"
        if not data:
            return None, ""
        u, n = np.unique(np.concatenate(list(data.values())), return_counts=True)
        return u[n >= max(1, int(np.ceil(len(data) / 2)))], f"bibliothek ({len(data)} Nächte)"


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
# Was Zeilen-/Spaltenprofile nicht erfassen (Eckglühen, Flecken an einer Seite – NGC 4490: Ecke +18 ADU, Seite −7 ADU
# nach dem Profilabzug, so groß wie das Stack-Rauschen), misst eine grobe 2D-Karte des Rests: Kacheln GLOW_BIN px je
# Farbebene. Sie wirkt außerhalb des mittleren Rechtecks (voll bis GLOW_MAP_FULL, ausgeblendet bis GLOW_MAP_BAND) –
# dort steht das Objekt, das sich bei der Feldrotation kaum bewegt und sonst als „Glühen“ abgezogen würde.
GLOW_BIN = 32
GLOW_MAP_FULL, GLOW_MAP_BAND = 0.18, 0.35


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


def _bin_percentile(a: np.ndarray, b: int, q: float = 30) -> np.ndarray:
    eh, ew = a.shape[0] // b, a.shape[1] // b
    t = a[: eh * b, : ew * b].reshape(eh, b, ew, b).transpose(0, 2, 1, 3).reshape(eh, ew, -1)
    return np.percentile(t, q, axis=2).astype(np.float32)


def glow_profiles(raw: np.ndarray, bayer: bool) -> list[tuple[np.ndarray, ...]]:
    """Je Farbebene (Zeilenprofil, Spaltenprofil, 2D-Restkarte) des Hintergrunds (30-%-Perzentil: robust gegen Sterne und Nebel)."""
    out = []
    for p in (_plane(raw, idx) for idx in _planes(raw, bayer)):
        r, c = np.percentile(p, 30, axis=1).astype(np.float32), np.percentile(p, 30, axis=0).astype(np.float32)
        if min(p.shape) >= 5 * GLOW_BIN:
            out.append((r, c, _bin_percentile(p - r[:, None] - c[None, :], GLOW_BIN)))
        else:                                            # zu klein für eine Kachelkarte: nur Profile
            out.append((r, c))
    return out


def _taper(n: int) -> np.ndarray:
    b = max(4, int(GLOW_BAND * n))
    d = np.minimum(np.arange(n), n - 1 - np.arange(n))
    return np.where(d < b, 0.5 * (1 + np.cos(np.pi * d / b)), 0.0).astype(np.float32)


def median_profiles(profiles: list[list[tuple[np.ndarray, ...]]]) -> list[tuple[np.ndarray, ...]] | None:
    """Median der Profile vieler Frames je Ebene – das ortsfeste Muster einer Nacht. None bei zu wenigen Frames."""
    if len(profiles) < GLOW_MIN_FRAMES:
        return None
    return [tuple(np.median([p[k][axis] for p in profiles], axis=0).astype(np.float32) for axis in range(len(profiles[0][k])))
            for k in range(len(profiles[0]))]


def _bin_mean(v: np.ndarray, n: int) -> np.ndarray:
    return v[: n * GLOW_BIN].reshape(n, GLOW_BIN).mean(1)


def _rest_map(pl: tuple, applied: tuple) -> np.ndarray:
    """2D-Karte dessen, was nach dem (ausgeblendeten) Profilabzug noch übrig ist – gemessen als Gesamtmuster aus Profilen +
    Restkarte. Kompakte helle Objekte (Galaxie, Sternhaufen, die sich kaum mitdrehen) werden durch die Umgebung ersetzt,
    die Karte leicht geglättet und auf die unberührte Bildmitte bezogen."""
    from scipy import ndimage as ndi

    r, c, e = pl
    eh, ew = e.shape
    rest = (_bin_mean(r, eh)[:, None] + _bin_mean(c, ew)[None, :] + e
            - _bin_mean(applied[0], eh)[:, None] - _bin_mean(applied[1], ew)[None, :])
    bg = ndi.median_filter(rest, size=5, mode="nearest")
    d = rest - bg
    s = 1.4826 * float(np.median(np.abs(d - np.median(d)))) + 1e-6
    inner = np.zeros(rest.shape, bool)
    inner[2:-2, 2:-2] = True                             # äußerste Kacheln: dort sitzt das Glühen selbst (Ecken)
    rest = ndi.gaussian_filter(np.where((d > 3 * s) & inner, bg, rest), 1.0, mode="nearest")
    return (rest - np.median(rest[eh // 3 : eh - eh // 3, ew // 3 : ew - ew // 3])).astype(np.float32)


def model_from(medians: list[tuple[np.ndarray, ...]] | None) -> list[tuple[np.ndarray, ...]] | None:
    """Abzugsmodell: Profile auf das Niveau der Bildmitte bezogen, geglättet, nur in den Randbändern; dazu die bereinigte
    2D-Restkarte (falls gemessen – ältere Bibliothekseinträge haben nur Profile)."""
    from scipy.ndimage import gaussian_filter1d

    if not medians:
        return None
    model = []
    for pl in medians:
        out = []
        for v in pl[:2]:
            n = len(v)
            v = gaussian_filter1d(v - np.median(v[n // 3 : 2 * n // 3]), 3, mode="nearest")
            out.append((v * _taper(n)).astype(np.float32))
        if len(pl) > 2 and min(pl[2].shape) >= 5:
            out.append(_rest_map(pl, out))
        model.append(tuple(out))
    return model


def _map_weight(h: int, w: int) -> np.ndarray:
    """1 außen, 0 im mittleren Rechteck, weicher Übergang dazwischen."""
    def t(n):
        d = np.minimum(np.arange(n), n - 1 - np.arange(n)) / n
        x = np.clip((d - GLOW_MAP_FULL) / (GLOW_MAP_BAND - GLOW_MAP_FULL), 0, 1)
        return (0.5 * (1 + np.cos(np.pi * x))).astype(np.float32)
    return np.maximum(t(h)[:, None], t(w)[None, :])


def _upsample_map(e: np.ndarray, h: int, w: int) -> np.ndarray:
    """Kachelkarte (Kachelmitten) bilinear auf (h, w); außerhalb der Kachelfläche wird der Rand gehalten."""
    from scipy.ndimage import map_coordinates

    ys = np.clip((np.arange(h) + 0.5) / GLOW_BIN - 0.5, 0, e.shape[0] - 1)
    xs = np.clip((np.arange(w) + 0.5) / GLOW_BIN - 0.5, 0, e.shape[1] - 1)
    yy, xx = np.meshgrid(ys, xs, indexing="ij")
    return map_coordinates(e, [yy, xx], order=1, mode="nearest").astype(np.float32)


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
                out.setdefault(night, {}).setdefault(int(k), [None, None, None])[int(ax)] = z[name]
        # Achse 2 = 2D-Restkarte; Nächte von vor der Karte haben nur Profile
        return {n: [tuple(a for a in v[k] if a is not None) for k in sorted(v)] for n, v in out.items()}

    def nights(self, key: str, with_map: bool = False) -> list[str]:
        """Gemessene Nächte; with_map: nur solche mit 2D-Restkarte (ältere werden bei vorhandenen Rohbildern neu gemessen)."""
        return sorted(n for n, planes in self._read(key).items() if not with_map or len(planes[0]) > 2)

    def add(self, key: str, night: str, medians: list[tuple[np.ndarray, ...]]) -> None:
        data = self._read(key)
        data[night] = medians
        self.root.mkdir(parents=True, exist_ok=True)
        arrays = {f"n{n}_{k}_{ax}": a for n, planes in data.items() for k, pl in enumerate(planes) for ax, a in enumerate(pl)}
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
            prof = [[np.median([p[k][ax] for p in planes], axis=0) for ax in (0, 1)] for k in range(len(planes[0]))]
            maps = [p for p in planes if len(p[0]) > 2]
            if maps:
                for k, pl in enumerate(prof):
                    pl.append(np.median([p[k][2] for p in maps], axis=0))
            return [tuple(pl) for pl in prof], f"bibliothek ({len(planes)} Nächte)"
        except ValueError:                                # uneinheitliche Formate
            return None, ""


def glow_amplitude(model) -> float:
    """Stärkstes Randglühen des Modells (ADU) – für Protokoll und Tests."""
    return float(max(max(np.abs(a).max() for a in pl) for pl in model)) if model else 0.0


def remove_glow(raw: np.ndarray, bayer: bool, model) -> np.ndarray:
    if not model:
        return raw
    out = np.array(raw, dtype=np.float32, copy=True)
    for (c, ys, xs), pl in zip(_planes(out, bayer), model):
        r, col = pl[0], pl[1]
        tgt = out[c] if c is not None else out
        p = tgt[ys, xs]
        if p.shape != (len(r), len(col)):
            return raw                                   # anderes Bildformat -> Modell passt nicht
        p = p - r[:, None] - col[None, :]
        if len(pl) > 2:
            p -= _full_map(pl[2], p.shape)
        tgt[ys, xs] = p
    return out


_MAP_CACHE: dict = {}


def _full_map(e: np.ndarray, shape: tuple) -> np.ndarray:
    """Gewichtete Restkarte in Ebenengröße – einmal je Modell gerechnet, gilt für alle Frames der Nacht."""
    key = (id(e), shape)
    hit = _MAP_CACHE.get(key)
    if hit is None or hit[0] is not e:
        if len(_MAP_CACHE) > 32:
            _MAP_CACHE.clear()
        hit = _MAP_CACHE[key] = (e, _upsample_map(e, *shape) * _map_weight(*shape))
    return hit[1]
