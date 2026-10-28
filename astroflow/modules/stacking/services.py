"""Dienste des Stacking-Moduls für andere Module (über `ctx.services`, nicht per Import).

Koordinaten: intern wird in FITS-Orientierung gerechnet (Zeile 0 = unterer Bildrand) – nur so löst ASTAP;
ein unten/oben gespiegeltes Bild findet es nicht. Ausgegebene Objektpositionen (x, y) gelten für das
angezeigte Bild (Zeile 0 = oben)."""
from __future__ import annotations

import json
import math
import re
from pathlib import Path

import numpy as np

from . import objects, platesolve
from .fits_io import FITS_EXT, read_data

PICTURE_EXT = (".jpg", ".jpeg", ".png", ".tif", ".tiff", ".gif", *FITS_EXT)
# Bildfeldhöhen (Grad), die ohne Maßstabsangabe nacheinander probiert werden: Smart-Teleskope zuerst
FOV_GUESSES = (0.7, 1.0, 1.3, 0.5, 2.0, 3.0, 5.0)


def load_fits_oriented(path: Path) -> tuple[np.ndarray, object]:
    """Helligkeitsbild in FITS-Orientierung + FITS-Header (oder None)."""
    if path.suffix.lower() in FITS_EXT:
        from astropy.io import fits
        with fits.open(path, memmap=False) as hl:
            header = next((h.header for h in hl if h.data is not None and h.data.ndim >= 2), None)
        a = read_data(path)
        return (a.mean(0) if a.ndim == 3 else a), header
    from PIL import Image
    with Image.open(path) as im:
        im.seek(0)
        return np.flipud(np.asarray(im.convert("L"), dtype=np.float32)), None


def describe(wcs, w: int, h: int, solver: str, max_objects: int = 30, offset=(0.0, 0.0), view=None) -> dict:
    """Bildmitte, Maßstab und Katalogobjekte im Feld. `wcs` gilt für ein Raster, aus dem das angezeigte Bild
    (Größe `view` = (B, H), Versatz `offset` = (x0, y0) in FITS-Pixeln) ausgeschnitten wurde."""
    vw, vh = view or (w, h)
    x0, y0 = offset
    (ra, dec), = wcs.pixel_to_world_values([[x0 + vw / 2 - 0.5, y0 + vh / 2 - 0.5]])
    cd = wcs.pixel_scale_matrix
    scale = math.sqrt(abs(np.linalg.det(cd))) * 3600
    rot = math.degrees(math.atan2(cd[1, 0], cd[0, 0]))
    fov_w, fov_h = vw * scale / 3600, vh * scale / 3600
    radius = math.hypot(fov_w, fov_h) / 2
    found = {}
    for c in [*objects.CATALOG, *objects.extended_catalog()]:
        if abs(c.dec - dec) > radius + 0.1 or objects.ang_sep(ra, dec, c.ra, c.dec) > radius:
            continue
        (x, y), = wcs.world_to_pixel_values([[c.ra, c.dec]])
        x, y = x - x0, y - y0
        if not (0 <= x < vw and 0 <= y < vh):
            continue
        m = re.match(r"\s*([A-Za-z]+\d?-?\s?\d+)", c.display)       # „M1 Krebsnebel“ und „M 1 Crab“ sind dasselbe
        key = objects.normalize_name(m.group(1) if m else c.display)
        if key not in found or found[key]["size"] < c.size:
            found[key] = {"name": c.display, "id": c.id, "kind": c.kind, "kind_label": objects.KIND_LABEL.get(c.kind, ""),
                          "size": c.size, "x": round(float(x), 1), "y": round(float(vh - 1 - y), 1),
                          "radius": round(max(6.0, c.size * 60 / scale / 2), 1)}
    objs = sorted(found.values(), key=lambda o: -o["size"])[:max_objects]
    return {"ra": round(float(ra), 5), "dec": round(float(dec), 5), "scale": round(scale, 3), "rotation": round(rot, 2),
            "fov_w": round(fov_w, 4), "fov_h": round(fov_h, 4), "width": vw, "height": vh, "solver": solver, "objects": objs}


def name_hint(name: str) -> objects.CatObject | None:
    """Objektname aus dem Dateinamen („M42_2024.jpg“, „NGC 7000 Nordamerika.png“)."""
    stem = re.sub(r"[_\-.]+", " ", Path(name).stem)
    for m in re.finditer(r"\b(M|NGC|IC|Messier|Sh2|C)\s?0*(\d{1,4})\b", stem, re.I):
        c = objects.resolve_name(m.group(0))
        if c:
            return c
    return objects.resolve_name(stem)


class PictureSolver:
    """Bestimmt Himmelsposition und Objekte eines einzelnen Bildes mit dem lokal eingerichteten Plate-Solver
    (ASTAP/astrometry.net – der Online-Dienst wird hier nie benutzt)."""

    def __init__(self, cfg: platesolve.SolverConfig, work: Path):
        self.cfg = cfg
        self.work = work

    @property
    def available(self) -> bool:
        return self.cfg.enabled

    def solve(self, path: Path, filename: str | None = None, hint: dict | None = None) -> dict:
        from astropy.wcs import WCS
        path = Path(path)
        L, header = load_fits_oriented(path)
        h, w = L.shape
        if header is not None:                                    # bereits gelöstes FITS (z. B. aus Siril/PixInsight)
            try:
                wcs = WCS(header, naxis=2)
                if wcs.has_celestial:
                    return describe(wcs, w, h, "header")
            except Exception:
                pass
        hint = dict(hint or {})
        if "ra" not in hint:
            c = name_hint(filename or path.name)
            if c:
                hint.update(ra=c.ra, dec=c.dec, name=c.display)
        if "ra" not in hint:
            raise platesolve.SolveError("ohne Positionshinweis nicht lokal lösbar – Objektnamen in den Dateinamen schreiben "
                                        "(z. B. „M42.jpg“) oder das Online-Lösen erlauben")
        errors = []
        for fov in ([hint["fov_h"]] if hint.get("fov_h") else FOV_GUESSES):
            try:
                hdr = platesolve.solve_image(self.cfg, L, self.work / "picture_solve", hint["ra"], hint["dec"], fov)
                return describe(WCS({**hdr, "NAXIS": 2, "NAXIS1": w, "NAXIS2": h}, naxis=2), w, h, self.cfg.chain()[0])
            except platesolve.SolveError as e:
                errors.append(str(e))
        raise platesolve.SolveError(f"keine Lösung in der Nähe von {hint.get('name') or 'der vermuteten Position'} ({errors[-1]})")


# ---------------------------------------------------------------- Objekte -- #
def _stretch(a: np.ndarray, f: int) -> np.ndarray:
    h, w = a.shape[0] // f * f, a.shape[1] // f * f
    a = a[:h, :w].reshape(h // f, f, w // f, f).mean((1, 3))
    lo, hi = np.percentile(a, [5, 99.5])
    a = np.arcsinh(np.clip((a - lo) / max(hi - lo, 1e-9), 0, 1) * 30)
    return (a - a.mean()) / (a.std() + 1e-9)


def locate(small: np.ndarray, big: np.ndarray) -> tuple[int, int, float]:
    """Versatz (y, x) eines Ausschnitts im größeren Bild per Phasenkorrelation; dazu die Güte (Spitze/Rauschen)."""
    pad = np.zeros_like(big)
    pad[:small.shape[0], :small.shape[1]] = small
    r = np.fft.fft2(big) * np.conj(np.fft.fft2(pad))
    r = np.fft.ifft2(r / (np.abs(r) + 1e-9)).real
    dy, dx = np.unravel_index(int(r.argmax()), r.shape)
    return int(dy), int(dx), float(r.max() / (r.std() + 1e-12))


def project_solution(work_dir: Path, pid: int, files: dict) -> dict | None:
    """Objekte im fertigen Bild eines Objekts – aus der gespeicherten Plate-Solving-Lösung des Master-Stacks
    (`work/p<id>/wcs.json`, entsteht bei der Gaia-Farbkalibrierung), ohne neu zu lösen. Der Bildausschnitt
    des JPGs wird per Korrelation im Stack gefunden."""
    from astropy.wcs import WCS
    from PIL import Image
    try:
        saved = json.loads((work_dir / f"p{pid}" / "wcs.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not files.get("fits") or not Path(files["fits"]).exists():
        return None
    stack = read_data(files["fits"])
    stack = stack.mean(0) if stack.ndim == 3 else stack
    H, W = stack.shape
    wcs = WCS({**saved["wcs"], "NAXIS": 2, "NAXIS1": W, "NAXIS2": H}, naxis=2)
    with Image.open(files.get("before") or files["jpg"]) as im:
        pic = np.flipud(np.asarray(im.convert("L"), dtype=np.float32))
    vh, vw = pic.shape
    if not (0.75 * W <= vw <= W and 0.75 * H <= vh <= H):     # anderer Maßstab (z. B. 2×-Auflösung): ohne Bildpositionen
        sol = describe(wcs, W, H, "stack")
        for o in sol["objects"]:
            o["x"] = o["y"] = None
        return sol
    f = 4
    dy, dx, q = locate(_stretch(pic, f), _stretch(stack, f))
    if q < 8:                                                  # keine eindeutige Lage gefunden: Mitte annehmen
        dy, dx = (H - vh) // (2 * f), (W - vw) // (2 * f)
    return describe(wcs, W, H, "stack", offset=(dx * f, dy * f), view=(vw, vh))
