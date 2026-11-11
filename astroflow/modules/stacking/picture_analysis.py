"""Grobe Analyse fertig importierter (bearbeiteter, gestreckter) Bilder: Bildfeld und Maßstab aus der Plate-Solving-Lösung,
Sternschärfe, Sternform, Hintergrundrauschen und Farbstich am Anzeigebild. Es ist eine Einordnung, keine Messung: gestreckte
Sterne sind aufgebläht, das Bild kann beschnitten oder verkleinert sein. Grenzgröße, HR-Diagramm, Farbkalibrierung und
Asteroiden bleiben bewusst aus (sie brauchen lineare Daten bzw. Einzelaufnahmen). Messfunktionen kommen aus `quality`
(Sternsuche) und `develop.assess` (Hintergrund, Rauschen, Farbstich)."""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np

from . import objects, picture_solve

PICTURE_ANALYSIS_VERSION = 1       # erhöhen, wenn sich die Auswertung ändert → alte Ergebnisse werden neu berechnet
MAX_SIDE = 2400                    # längere Bilder werden für die Sternsuche ganzzahlig verkleinert
MIN_STARS = 12


def cache_path(work_dir: Path, pid: int) -> Path:
    return Path(work_dir) / f"p{pid}" / "picture_analysis.json"


def north_angle(rotation: float) -> float:
    """Winkel, um den Norden im Anzeigebild im Uhrzeigersinn von „oben“ abweicht (-180…180), aus der Drehung der
    Lösung (`services.describe`: atan2(cd10, cd00) im FITS-Raster mit Ost links)."""
    a = (180.0 - float(rotation)) % 360.0
    return round(a - 360.0 if a > 180 else a, 1)


def field_info(sol: dict) -> dict:
    """Bildfeld, Maßstab, Ausrichtung und vorsichtige Geräteeinordnung."""
    fw, fh, scale = float(sol["fov_w"]), float(sol["fov_h"]), float(sol["scale"])
    long_side, short_side = max(fw, fh), min(fw, fh)
    if long_side >= 4.0:
        cls = "wide"
        hint = "Sehr großes Bildfeld – typisch für Objektiv/Teleobjektiv oder Weitfeldkamera, nicht für ein Teleskop mit langer Brennweite."
    elif long_side >= 0.9 and scale >= 1.8:
        cls = "smart"
        hint = "Bildfeld und Maßstab passen zu einem Smart-Teleskop (Seestar, Vespera, Stellina und ähnliche)."
    elif scale >= 1.8:
        cls = "smart_crop"
        hint = "Maßstab wie bei einem Smart-Teleskop, das Feld ist aber kleiner – vermutlich ein Ausschnitt."
    else:
        cls = "classic"
        hint = "Feiner Maßstab bei kleinem Feld – eher ein klassisches Teleskop mit längerer Brennweite oder ein stark vergrößerter Ausschnitt."
    return {"fov_w": round(fw, 3), "fov_h": round(fh, 3), "fov_long": round(long_side, 3), "fov_short": round(short_side, 3),
            "scale": round(scale, 2), "north": north_angle(sol.get("rotation") or 0.0), "device_class": cls, "device_hint": hint,
            "caveat": "Nur eine Einordnung: ist das Bild beschnitten oder verkleinert, weichen Feld und Maßstab vom Gerät ab.",
            "width": int(sol.get("width") or 0), "height": int(sol.get("height") or 0),
            "center": {"ra": sol.get("ra"), "dec": sol.get("dec")}}


def _tone(rgb: np.ndarray, L: np.ndarray) -> str | None:
    """Farbe des Hintergrunds („rötlich“, „bläulich“ …) aus dem Median der dunkelsten, sternfreien Pixel je Kanal."""
    from . import processing
    sky = processing.dark_sky_mask(L, 12.0, smooth=4.0)
    if sky.sum() < 500:
        return None
    med = np.array([np.median(rgb[c][sky]) for c in range(3)])
    d = med - med.mean()
    if np.abs(d).max() < 0.004:
        return None
    hi = tuple(bool(v > 0.5 * d.max()) for v in d) if d.max() > 0.004 else None
    names = {(True, False, False): "rötlich", (False, True, False): "grünlich", (False, False, True): "bläulich",
             (True, False, True): "magenta", (True, True, False): "gelblich", (False, True, True): "cyan"}
    return names.get(hi)


def measure(rgb: np.ndarray, scale: float | None) -> dict:
    """Qualitätseinordnung eines Anzeigebildes (3,H,W) 0..1. `scale` = Bogensekunden je Pixel des Bildes (None ohne Lösung)."""
    from . import develop, quality
    from .stars import luminance
    H, W = rgb.shape[1:]
    out: dict = {"n_stars": 0}
    mono = bool(np.allclose(rgb[0], rgb[1], atol=2e-3) and np.allclose(rgb[1], rgb[2], atol=2e-3))
    qc = develop.assess(rgb, mono=mono, neutral_sky=False)
    m = qc["metrics"]
    L = luminance(rgb)
    out["background"] = {"level": m["bg"], "noise": m["noise"], "chroma_noise": m["chroma_noise"], "gradient": m["gradient"],
                         "cast": m["cast"], "tone": None if mono else _tone(rgb, L), "mono": mono,
                         "notes": [i["text"] for i in qc["issues"] if i["code"] in ("chroma_noise", "gradient", "color_gradient")]}
    f = max(1, int(math.ceil(max(H, W) / MAX_SIDE)))
    Ls = L[: H // f * f, : W // f * f].reshape(H // f, f, W // f, f).mean((1, 3)) if f > 1 else L
    st = quality.detect_stars(Ls.astype(np.float32), max_stars=200)
    n = len(st)
    out["n_stars"] = n
    if n >= MIN_STARS:
        # nur die helleren Sterne: schwache sind vom Rauschen verfälscht; FWHM in Pixeln des Originalbildes
        top = slice(0, max(MIN_STARS, n * 2 // 3))
        fwhm = float(np.median(st.fwhm[top])) * f
        ecc = float(np.median(st.ecc[top]))
        out["stars"] = {"fwhm_px": round(fwhm, 2), "fwhm_arcsec": round(fwhm * scale, 2) if scale else None,
                        "ecc": round(ecc, 2), "round": ecc < 0.35,
                        "pa": quality.shape_metrics(st, Ls.shape, f).get("ecc_pa")}
    return out


def _rgb(path: Path) -> np.ndarray:
    from PIL import Image
    with Image.open(path) as im:
        im.seek(0)
        return (np.asarray(im.convert("RGB"), dtype=np.float32) / 255.0).transpose(2, 0, 1)


def own_images(eng, pid: int, obj_id: str | None, rid: int) -> list[dict]:
    """Eigene Projekte (Rohbild-Stacks und andere Importe) mit demselben Objekt – für das kleine Nebeneinander."""
    if not obj_id:
        return []
    out = []
    for p in eng.db.q("SELECT id, name, options FROM projects WHERE obj_id=? AND id<>?", (obj_id, pid)):
        opt = json.loads(p["options"] or "{}")
        if opt.get("gallery_parent"):
            continue
        r = eng.db.one(f"SELECT id, n_frames, exposure, created FROM results WHERE project_id=? ORDER BY {picture_solve.LATEST} LIMIT 1", (p["id"],))
        if r:
            out.append({"project": p["id"], "name": p["name"], "result": r["id"], "imported": opt.get("input_type") == "picture",
                        "n_frames": r["n_frames"], "exposure": r["exposure"], "created": r["created"]})
    out.sort(key=lambda x: (x["imported"], -(x["exposure"] or 0)))
    return out[:6]


def analyse(eng, rid: int) -> dict | None:
    """Ergebnis für `GET /results/<id>/picture-analysis` (None = Ergebnis unbekannt). Gerechnet wird beim ersten Abruf und
    nach Versions-/Lösungsänderung neu, sonst aus `work/p<id>/picture_analysis.json`."""
    x = eng.db.one("SELECT r.id, r.project_id, r.files, p.obj_id, p.name FROM results r JOIN projects p ON p.id=r.project_id WHERE r.id=?", (rid,))
    if not x:
        return None
    pid = x["project_id"]
    work = eng.s.work_dir
    sol = picture_solve.stored_solution(work, pid)
    sp = picture_solve.solution_path(work, pid)
    cache = cache_path(work, pid)
    base = {"solved": bool(sol), "v": PICTURE_ANALYSIS_VERSION}
    if cache.exists() and (not sol or cache.stat().st_mtime >= sp.stat().st_mtime):
        try:
            c = json.loads(cache.read_text(encoding="utf-8"))
            if c.get("v") == PICTURE_ANALYSIS_VERSION and c.get("result_id") == rid and c.get("solved") == bool(sol):
                c["own"] = own_images(eng, pid, x["obj_id"], rid)
                return c
        except (OSError, ValueError):
            pass
    files = json.loads(x["files"] or "{}")
    image = files.get("jpg") or files.get("png")
    out: dict = {**base, "result_id": rid, "object": x["name"]}
    if sol:
        out["field"] = field_info(sol)
        out["objects"] = [{k: o[k] for k in ("name", "kind_label", "size", "x", "y", "radius") if k in o} for o in sol.get("objects", [])]
    if image and Path(image).exists():
        try:
            out["quality"] = measure(_rgb(Path(image)), (float(sol["scale"]) if sol else None))
        except Exception:
            import traceback
            traceback.print_exc()
            out["quality_error"] = "Die Bildqualität konnte nicht gemessen werden (Details im Server-Protokoll)."
    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(out), encoding="utf-8")
    except OSError:
        pass
    out["own"] = own_images(eng, pid, x["obj_id"], rid)
    return out
