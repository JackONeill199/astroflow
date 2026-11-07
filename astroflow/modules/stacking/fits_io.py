"""FITS einlesen, Header interpretieren, Debayering."""
from __future__ import annotations

import datetime as dt
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
from astropy.io import fits
from scipy import ndimage as ndi

FITS_EXT = (".fit", ".fits", ".fts")
BAYER_PATTERNS = ("RGGB", "GRBG", "GBRG", "BGGR")


@dataclass
class FrameInfo:
    path: str
    shape: tuple
    object: str | None = None
    ra: float | None = None       # Grad
    dec: float | None = None      # Grad
    date_obs: str | None = None
    exptime: float | None = None
    gain: float | None = None
    filter: str | None = None
    imagetyp: str | None = None
    instrument: str | None = None
    bayerpat: str | None = None
    temp: float | None = None
    solve: dict | None = None     # Plate-Solving-Ergebnis, falls die Koordinaten daraus stammen
    fp: str | None = None         # Inhalts-Fingerabdruck (Duplikaterkennung, siehe scanner.fingerprint)
    processed: str | None = None  # Grund, falls schon fertig verarbeitet (Stack/Master anderer Software) – kein Rohbild
    site: list | None = None      # Beobachtungsort [Breite, Länge] in Grad, falls im Header (Seestar, ASIAIR, NINA …)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["shape"] = list(self.shape)
        return d

    @property
    def hw(self) -> tuple[int, int]:
        return tuple(self.shape[-2:])

    @property
    def camera(self) -> str:
        return (self.instrument or "unbekannt").strip().lower()


def _float(v):
    try:
        f = float(v)
        return f if np.isfinite(f) else None
    except (TypeError, ValueError):
        return None


def _sexagesimal(s: str) -> float | None:
    parts = s.replace(":", " ").split()
    try:
        vals = [float(p) for p in parts]
    except ValueError:
        return None
    if not vals:
        return None
    sign = -1 if s.strip().startswith("-") else 1
    v = abs(vals[0]) + (vals[1] / 60 if len(vals) > 1 else 0) + (vals[2] / 3600 if len(vals) > 2 else 0)
    return sign * v


def _coord(h, keys_num, key_sexa, hours_sexa: bool):
    for k in keys_num:
        if k in h:
            v = h[k]
            f = _float(v)
            if f is not None:
                return f
            if isinstance(v, str):
                s = _sexagesimal(v)
                if s is not None:
                    return s * (15.0 if hours_sexa else 1.0)
    if key_sexa and key_sexa in h and isinstance(h[key_sexa], str):
        s = _sexagesimal(h[key_sexa])
        if s is not None:
            return s * (15.0 if hours_sexa else 1.0)
    return None


def _clean(v):
    if v is None:
        return None
    s = str(v).strip().strip("'").strip()
    return s or None


def _exposure_seconds(h) -> float | None:
    """EXPTIME in s; EXPOSURE kann (z. B. Stellina) in Millisekunden angegeben sein ('[ms]' im Kommentar)."""
    if "EXPTIME" in h:
        return _float(h["EXPTIME"])
    v = _float(h.get("EXPOSURE"))
    if v is not None and "ms" in str(h.comments["EXPOSURE"]).lower().replace(" ", ""):
        return v / 1000.0
    return v


def _first_image_header(hdul):
    for h in hdul:
        if h.header.get("NAXIS", 0) >= 2:
            return h
    return None


def read_info(path: str | Path) -> FrameInfo:
    """Liest nur den Header (schnell)."""
    with fits.open(path, memmap=True) as hdul:
        hdu = _first_image_header(hdul)
        if hdu is None:
            raise ValueError("kein Bild in der FITS-Datei")
        h = hdu.header
        n = h["NAXIS"]
        dims = tuple(int(h[f"NAXIS{i}"]) for i in range(n, 0, -1))
        if len(dims) == 3 and dims[0] not in (1, 3) and dims[2] in (1, 3):
            dims = (dims[2], dims[0], dims[1])
        if len(dims) == 3 and dims[0] == 1:
            dims = dims[1:]
        bp = _clean(h.get("BAYERPAT") or h.get("COLORTYP"))
        bp = bp.upper() if bp and bp.upper() in BAYER_PATTERNS else None
        ra = _coord(h, ("RA", "CRVAL1", "OBJCTRA"), "OBJCTRA", True)
        dec = _coord(h, ("DEC", "CRVAL2", "OBJCTDEC"), "OBJCTDEC", False)
        if ra is not None and "RA" not in h and "CRVAL1" not in h:
            pass
        info = FrameInfo(
            path=str(path),
            shape=dims,
            object=_clean(h.get("OBJECT")),
            ra=ra,
            dec=dec,
            date_obs=_clean(h.get("DATE-OBS") or h.get("DATE")),
            exptime=_exposure_seconds(h),
            gain=_float(h.get("GAIN")),
            filter=_clean(h.get("FILTER")),
            imagetyp=_clean(h.get("IMAGETYP") or h.get("FRAME")),
            instrument=_clean(h.get("INSTRUME") or h.get("TELESCOP")),
            bayerpat=bp,
            temp=_float(h.get("CCD-TEMP", h.get("TEMPERAT", h.get("TEMP")))),   # Stellina: TEMP (Sensor, °C) – Gerätediagnose
            processed=_processed(h, dims),
            site=_site(h),
        )
        return info


def _site(h) -> list | None:
    """Beobachtungsort aus SITELAT/SITELONG, LAT-OBS/LONG-OBS oder OBSGEO-B/L (Zahl oder „+50 12 30“). Stellina schreibt keinen."""
    for kb, kl in (("SITELAT", "SITELONG"), ("LAT-OBS", "LONG-OBS"), ("OBSGEO-B", "OBSGEO-L")):
        if kb in h and kl in h:
            b, l = (_float(h[k]) if _float(h[k]) is not None else _sexagesimal(str(h[k])) for k in (kb, kl))
            if b is not None and l is not None and -90 <= b <= 90 and (b, l) != (0, 0):
                return [round(b, 4), round((l + 180) % 360 - 180, 4)]
    return None


def _processed(h, dims: tuple) -> str | None:
    """Fertige Ergebnisse anderer Software erkennen (z. B. Astro-Pixel-Processor-Integration, die im
    Stellina-Beobachtungsordner neben den Rohbildern lag und sonst als eigenes Objekt „gestackt“ wurde)."""
    for key in ("NUMFRAME", "STACKCNT", "NCOMBINE"):
        n = _float(h.get(key))
        if n and n > 1:
            return f"bereits gestackt ({key} = {int(n)})"
    if "INTEGRAT" in h:
        return "bereits gestackt (Integration)"
    if len(dims) == 3 and dims[0] == 3:
        return "fertiges Farbbild (3 Farbebenen statt Rohbild)"
    return None


def parse_date(s: str | None) -> dt.datetime | None:
    if not s:
        return None
    s = s.strip().rstrip("Z")
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return dt.datetime.strptime(s, fmt)
        except ValueError:
            continue
    try:
        return dt.datetime.fromisoformat(s)
    except ValueError:
        return None


def read_data(path: str | Path) -> np.ndarray:
    """Pixeldaten als float32: (H,W) für Mono/CFA oder (C,H,W) für Farbwürfel."""
    with fits.open(path, memmap=False) as hdul:
        data = None
        for h in hdul:
            if h.data is not None and h.data.ndim >= 2:
                data = h.data
                break
        if data is None:
            raise ValueError("keine Bilddaten")
        arr = np.asarray(data, dtype=np.float32)
    if arr.ndim == 3:
        if arr.shape[0] not in (1, 3) and arr.shape[2] in (1, 3):
            arr = np.moveaxis(arr, 2, 0)
        if arr.shape[0] == 1:
            arr = arr[0]
    return np.ascontiguousarray(arr)


def debayer(raw: np.ndarray, pattern: str) -> np.ndarray:
    """Bilineares Debayering (H,W) -> (3,H,W) in der Reihenfolge R,G,B."""
    H, W = raw.shape
    pattern = pattern.upper()
    masks = {c: np.zeros((H, W), np.float32) for c in "RGB"}
    for i, c in enumerate(pattern):
        masks[c][i // 2 :: 2, i % 2 :: 2] = 1.0
    k_g = np.array([[0, 1, 0], [1, 4, 1], [0, 1, 0]], np.float32) / 4
    k_rb = np.array([[1, 2, 1], [2, 4, 2], [1, 2, 1]], np.float32) / 4
    out = np.empty((3, H, W), np.float32)
    for ci, c in enumerate("RGB"):
        k = k_g if c == "G" else k_rb
        out[ci] = ndi.convolve(raw * masks[c], k, mode="mirror")
    return out


def to_channels(raw: np.ndarray, bayerpat: str | None) -> np.ndarray:
    """Immer (C,H,W): debayert, Farbwürfel oder Mono (C=1)."""
    if raw.ndim == 3:
        return raw
    if bayerpat:
        return debayer(raw, bayerpat)
    return raw[None]


def write_fits(path: str | Path, data: np.ndarray, header: dict | None = None) -> None:
    hdu = fits.PrimaryHDU(np.asarray(data, np.float32))
    for k, v in (header or {}).items():
        try:
            hdu.header[k] = v
        except Exception:
            pass
    hdu.writeto(path, overwrite=True)
