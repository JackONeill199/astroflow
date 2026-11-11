"""Observatorium, Teil 1: Himmelsatlas. Liefert die eigenen Bilder mit ihrer Lage am Himmel (vier Ecken des angezeigten
Bildes in RA/Dec, aus der gespeicherten Plate-Solving-Lösung) und den Deep-Sky-Katalog von „Heute Nacht“.

Stacks: `work/p<id>/wcs.json` (Lösung des linearen Master-Stacks). Das angezeigte JPG ist meist ein leicht beschnittener
Ausschnitt davon; hier wird ohne Stack-Lesen ein mittiger Ausschnitt angenommen (Abweichung wenige Prozent des Feldes –
für den Atlas genug; die genaue Lage per Korrelation kostet je Bild ~1 s). Importierte Bilder: `picture_solution.json`
(neuere Lösungen enthalten die WCS, ältere nur Mitte/Maßstab/Drehung – dann wird die übliche Bildlage angenommen,
Norden oben, Osten links, nicht gespiegelt). Ergebnis je Projekt zwischengespeichert (`work/p<id>/atlas.json`)."""
from __future__ import annotations

import json
import math
import traceback
from pathlib import Path

from . import deepsky
from .objects import KIND_LABEL

ATLAS_VERSION = 1
# neuestes Ergebnis wie `project_picture` (Teleskop-Serien: höchste Sequenz)
LATEST = "COALESCE(json_extract(stats,'$.imported.series.sequence'),0) DESC, id DESC"
WCS_KEYS = ("CTYPE", "CRVAL", "CRPIX", "CD1_", "CD2_", "CDELT", "CROTA", "PC1_", "PC2_", "CUNIT")


def wcs_subset(hdr: dict) -> dict:
    """Nur die Schlüssel der linearen TAN-Lösung (für `picture_solution.json`)."""
    return {k: v for k, v in hdr.items() if isinstance(k, str) and k.startswith(WCS_KEYS) and isinstance(v, (int, float, str))}


def _wcs(hdr: dict, w: int, h: int):
    from astropy.wcs import WCS
    return WCS({**hdr, "NAXIS": 2, "NAXIS1": w, "NAXIS2": h}, naxis=2)


def _corners(wcs, x0: float, y0: float, vw: float, vh: float) -> list[list[float]]:
    """Ecken des angezeigten Bildes (oben links, oben rechts, unten rechts, unten links) in RA/Dec. (x0, y0) und
    (vw, vh) = Ausschnitt in FITS-Pixeln (Zeile 0 = unten); Pixelränder liegen bei −0,5."""
    xs = [x0 - 0.5, x0 + vw - 0.5, x0 + vw - 0.5, x0 - 0.5]
    ys = [y0 + vh - 0.5, y0 + vh - 0.5, y0 - 0.5, y0 - 0.5]
    world = wcs.pixel_to_world_values(xs, ys)
    return [[round(float(ra) % 360, 5), round(float(dec), 5)] for ra, dec in zip(*world)]


def _image_size(path) -> tuple[int, int] | None:
    from PIL import Image
    try:
        with Image.open(path) as im:
            return im.size
    except (OSError, ValueError):
        return None


def stack_footprint(work_dir: Path, pid: int, files: dict) -> dict | None:
    """Lage des fertigen Stack-Bildes aus `wcs.json` (ohne den Stack zu lesen: Größe aus FITS-Kopf bzw. Lösungsschlüssel)."""
    try:
        saved = json.loads((Path(work_dir) / f"p{pid}" / "wcs.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    W = H = None
    key = str(saved.get("key") or "")
    if "|" in key and "x" in key.split("|")[-1]:
        try:
            H, W = (int(v) for v in key.split("|")[-1].split("x"))
        except ValueError:
            H = W = None
    if W is None and files.get("fits") and Path(files["fits"]).exists():
        from astropy.io import fits
        try:
            hd = fits.getheader(files["fits"])
            W, H = int(hd["NAXIS1"]), int(hd["NAXIS2"])
        except (OSError, KeyError, ValueError):
            return None
    if not W:
        return None
    wcs = _wcs(saved["wcs"], W, H)
    vw, vh = W, H
    size = _image_size(files.get("jpg") or files.get("png") or "")
    if size and 0.75 * W <= size[0] <= W and 0.75 * H <= size[1] <= H:      # beschnittener Ausschnitt: mittig angenommen
        vw, vh = size
    return _describe(wcs, (W - vw) / 2, (H - vh) / 2, vw, vh)


def picture_footprint(sol: dict) -> dict | None:
    """Lage eines importierten Bildes aus `picture_solution.json` (`services.describe` + optional `wcs`)."""
    w, h = sol.get("width"), sol.get("height")
    if not w or not h or sol.get("ra") is None:
        return None
    hdr = sol.get("wcs")
    if not hdr:                                    # ältere Lösung: TAN aus Mitte, Maßstab, Drehung (Spiegelung wie üblich: det < 0)
        s, r = (sol.get("scale") or 1) / 3600, math.radians(sol.get("rotation") or 0)
        hdr = {"CTYPE1": "RA---TAN", "CTYPE2": "DEC--TAN", "CRVAL1": sol["ra"], "CRVAL2": sol["dec"],
               "CRPIX1": w / 2 + 0.5, "CRPIX2": h / 2 + 0.5,
               "CD1_1": s * math.cos(r), "CD2_1": s * math.sin(r), "CD1_2": s * math.sin(r), "CD2_2": -s * math.cos(r)}
    return _describe(_wcs(hdr, w, h), 0, 0, w, h)


def _describe(wcs, x0, y0, vw, vh) -> dict:
    (ra, dec), = wcs.pixel_to_world_values([[x0 + vw / 2 - 0.5, y0 + vh / 2 - 0.5]])
    corners = _corners(wcs, x0, y0, vw, vh)
    from .objects import ang_sep
    fw = ang_sep(*corners[0], *corners[1])
    fh = ang_sep(*corners[1], *corners[2])
    return {"ra": round(float(ra) % 360, 5), "dec": round(float(dec), 5), "corners": corners,
            "fov": [round(fw, 4), round(fh, 4)]}


def _footprint(work_dir: Path, pid: int, rid: int, files: dict, imported: bool) -> dict | None:
    """Lage mit Zwischenspeicher je Projekt (neu bei anderer Version, anderem Ergebnis oder jüngerer Lösung)."""
    wd = Path(work_dir) / f"p{pid}"
    src = wd / ("picture_solution.json" if imported else "wcs.json")
    if not src.exists():
        return None
    cache = wd / "atlas.json"
    stamp = [ATLAS_VERSION, rid, round(src.stat().st_mtime, 3)]
    try:
        c = json.loads(cache.read_text(encoding="utf-8"))
        if c.get("stamp") == stamp:
            return c.get("fp")
    except (OSError, ValueError):
        pass
    try:
        if imported:
            fp = picture_footprint(json.loads(src.read_text(encoding="utf-8")))
        else:
            fp = stack_footprint(work_dir, pid, files)
    except Exception:                              # beschädigte Lösung: Bild fehlt im Atlas, Rest läuft
        traceback.print_exc()
        fp = None
    try:
        cache.write_text(json.dumps({"stamp": stamp, "fp": fp}), encoding="utf-8")
    except OSError:
        pass
    return fp


def photos(db, work_dir: Path) -> list[dict]:
    """Alle Objekte mit gelöstem neuesten Ergebnis: Lage, Vorschaubild, Grunddaten."""
    out = []
    for p in db.q("SELECT id, name, kind, options FROM projects ORDER BY id"):
        r = db.one(f"SELECT id, files, exposure, n_frames, created FROM results WHERE project_id=? ORDER BY {LATEST} LIMIT 1", (p["id"],))
        if not r:
            continue
        files = json.loads(r["files"] or "{}")
        if not (files.get("jpg") or files.get("png")):
            continue
        imported = json.loads(p["options"] or "{}").get("input_type") == "picture"
        fp = _footprint(work_dir, p["id"], r["id"], files, imported)
        if not fp:
            continue
        img = "jpg" if files.get("jpg") else "png"
        out.append({"pid": p["id"], "rid": r["id"], "name": p["name"], "kind": p["kind"] or "generic",
                    "kind_label": KIND_LABEL.get(p["kind"] or "generic", ""), "exposure": r["exposure"], "frames": r["n_frames"],
                    "created": r["created"], "imported": imported, "href": f"#/p/{p['id']}",
                    "thumb": f"/api/stacking/results/{r['id']}/thumb" if files.get("thumb") else f"/api/stacking/results/{r['id']}/{img}",
                    "image": f"/api/stacking/results/{r['id']}/{img}", **fp})
    return out


def catalog() -> list[dict]:
    """Deep-Sky-Katalog wie bei „Heute Nacht“ (Messier, Caldwell, auffällige ASTAP-Objekte)."""
    return [{"id": t.id, "name": t.display, "ra": round(t.ra, 4), "dec": round(t.dec, 4), "kind": t.kind,
             "kind_label": KIND_LABEL.get(t.kind, ""), "size": t.size, "mag": t.mag, "src": t.source[0]}
            for t in deepsky.catalog()]
