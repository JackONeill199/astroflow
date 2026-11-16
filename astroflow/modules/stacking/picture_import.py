"""Fertige Bilder unverändert archivieren und mit Anzeigevorschauen in die Galerie übernehmen."""
from __future__ import annotations

import hashlib
import datetime as dt
import json
import re
import shutil
import tempfile
import time
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

from ...core import sites
from . import objects

PICTURE_EXT = (".jpg", ".jpeg", ".png", ".tif", ".tiff")
PREVIEW_EXT = (".jpg", ".jpeg", ".png")
FITS_EXT = (".fit", ".fits", ".fts")
SOURCE_EXT = (".tif", ".tiff") + FITS_EXT
SOURCE_LIMIT = 10        # ab mehr als so vielen Dateien gilt die Quelle als Aufnahmeordner


def skipped_pictures(names: list[str]) -> tuple[str, ...]:
    """Endungen, die bei diesem Upload nicht als fertige Bilder importiert werden. Aufnahmeordner (z. B. Stellina) enthalten
    neben den Einzelbildern Vorschauen und Zwischenstände – die gehören nicht in die Galerie:
    > 10 FITS → nur die FITS (auch kein TIFF), sonst > 10 TIFF/FITS → keine JPG/PNG."""
    low = [n.lower() for n in names]
    if sum(n.endswith(FITS_EXT) for n in low) > SOURCE_LIMIT:
        return PICTURE_EXT
    return PREVIEW_EXT if sum(n.endswith(SOURCE_EXT) for n in low) > SOURCE_LIMIT else ()


def telescope_series(filename: str, exif) -> dict | None:
    """Stellina-Verlaufsbilder nur bei eindeutiger Geräte-, Objekt- und Zeitangabe gruppieren."""
    match = re.fullmatch(r"img-(\d+)-output\.(?:jpe?g|png|tiff?)", Path(filename).name, re.I)
    model = str(exif.get(272) or "")
    name = re.sub(r"\s*\(\d+\s+exp\).*$", "", str(exif.get(270) or ""), flags=re.I).strip()
    if not match or str(exif.get(271) or "").upper() != "VAONIS" or not model.lower().startswith("stellina") or not name:
        return None
    try:
        stamp = dt.datetime.fromisoformat(str(exif.get(36867) or exif.get(306) or "").replace("/", "-").replace(" ", "T"))
    except ValueError:
        return None
    return {"object": name, "instrument": model, "night": sites.night_of(stamp),
            "sequence": int(match[1])}


# ------------------------------------------------------------------ lineare Bilder (TIFF/PNG mit 16/32 bit) #
# Stacks aus DeepSkyStacker/Siril/Smart-Teleskop-Apps und Einzelbilder kommen oft ungestreckt: der Himmel liegt bei
# wenigen Prozent des Wertebereichs, als fertiges Bild angezeigt ist alles fast schwarz. Solche Bilder strecken wir für
# die Vorschau und bieten an, sie wie einen Master-Stack zu entwickeln (Pipeline.develop).
LINEAR_MEDIAN = 0.1        # Median der Helligkeit unter 10 % des Wertebereichs …
LINEAR_TOP = 0.25          # … und 99,5-%-Wert unter 25 % (keine Gamma-Kurve, Sterne/Nebel nicht hochgezogen)
LINEAR_SPREAD = 8.0        # oder: 99,5-%-Wert höchstens 8× Median (heller Nebel füllt das Feld)


def normalized(a: np.ndarray) -> np.ndarray:
    """Ganzzahl → 0..1 nach Bit-Tiefe; Float: 0..1, Werte über 1,5 (DSS/Siril speichern ADU) auf 65535 bzw. den Höchstwert."""
    if np.issubdtype(a.dtype, np.integer):
        return a.astype(np.float32) / np.float32(np.iinfo(a.dtype).max)
    a = np.nan_to_num(a.astype(np.float32), nan=0, posinf=0, neginf=0)
    mx = float(a.max()) if a.size else 0.0
    return a / np.float32(65535.0 if mx <= 65535 else mx) if mx > 1.5 else a


def _sample(a: np.ndarray, n: int = 1_000_000) -> np.ndarray:
    step = max(1, int(np.sqrt(a.shape[0] * a.shape[1] / n)))
    return a[::step, ::step]


def bayer_pattern(a: np.ndarray) -> str | None:
    """Undebayertes Einzelbild (2D)? Die vier 2×2-Teilgitter einer Farbmaske haben deutlich verschiedene Mediane,
    die beiden Grün-Pixel (eine Diagonale) aber fast gleiche. Rot und Blau lassen sich nicht sicher unterscheiden –
    angenommen wird Rot in der ersten Zeile (RGGB bzw. GRBG, die häufigsten Sensoren)."""
    if a.ndim != 2 or min(a.shape) < 16:
        return None
    m = {(i, j): float(np.median(a[i::2, j::2])) for i in (0, 1) for j in (0, 1)}
    ref = float(np.median(list(m.values()))) + 1e-9
    spread = (max(m.values()) - min(m.values())) / ref
    main, anti = abs(m[0, 0] - m[1, 1]) / ref, abs(m[0, 1] - m[1, 0]) / ref
    if spread < 0.05 or min(main, anti) > 0.25 * spread:
        return None                          # Graustufen/Mono oder keine klare Grün-Diagonale
    return "RGGB" if main > anti else "GRBG"     # Grün auf der Nebendiagonale (0,1)/(1,0) → RGGB


def linear_info(a: np.ndarray, bit_depth: int) -> dict | None:
    """a: Werte 0..1 (H,W) oder (H,W,C). Kennzahlen, wenn das Bild linear (ungestreckt) ist, sonst None.
    8-bit-Bilder gelten immer als fertig."""
    if bit_depth < 16:
        return None
    bayer = bayer_pattern(a)
    if bayer:                                # Farbmaske: Mittel je 2×2-Block statt einzelner Farbpixel
        H, W = a.shape[0] // 2 * 2, a.shape[1] // 2 * 2
        L = _sample(a[:H, :W].reshape(H // 2, 2, W // 2, 2).mean((1, 3)))
    else:
        L = _sample(a if a.ndim == 2 else a[..., :3].mean(-1))
    med, top = (float(v) for v in np.percentile(L, (50, 99.5)))
    if med >= LINEAR_MEDIAN or not (top < LINEAR_TOP or (med > 0.002 and top < LINEAR_SPREAD * med)):
        return None
    out = {"median": round(med, 4), "p995": round(top, 4), "channels": 1 if a.ndim == 2 else 3}
    if bayer:
        out.update(bayer=bayer, single=True, channels=3)
    return out


def to_channels(a: np.ndarray, bayer: str | None) -> np.ndarray:
    """(H,W[,C]) 0..1 in Bildorientierung → (C,H,W) float32; Farbmaske debayert, Alphakanal verworfen."""
    if a.ndim == 3:
        return np.ascontiguousarray(np.moveaxis(a[..., :3], -1, 0), dtype=np.float32)
    if bayer:
        from .fits_io import debayer
        H, W = a.shape[0] // 2 * 2, a.shape[1] // 2 * 2
        return debayer(np.ascontiguousarray(a[:H, :W], dtype=np.float32), bayer)
    return a[None].astype(np.float32)


def stretched_preview(a: np.ndarray, bayer: str | None = None) -> np.ndarray:
    """Automatisch gestreckte Anzeige (H,W,3) uint8 eines linearen Bildes. Je Kanal eigener Schwarzpunkt und
    Mittelton (wie die Autostretch-Ansicht in Siril/PixInsight): ungestreckte Stacks haben oft einen kräftigen
    Farbstich im Himmel, verknüpft gestreckt wäre die Vorschau einfarbig. Die echte Farbe macht erst die Entwicklung."""
    from .processing import mtf
    img = to_channels(a, bayer)
    out = np.empty((3,) + img.shape[1:], np.uint8)
    for c in range(3):
        x = img[min(c, img.shape[0] - 1)]
        s = _sample(x)
        med = float(np.median(s))
        sig = 1.4826 * float(np.median(np.abs(s - med))) + 1e-9
        lo, hi = med - 2.8 * sig, max(float(np.percentile(s, 99.95)), med + 10 * sig)
        y = np.clip((x - lo) / (hi - lo), 0, 1)
        m0 = float(np.clip((med - lo) / (hi - lo), 1e-5, 0.9))
        t = 0.15
        m = float(np.clip(m0 * (t - 1) / (2 * t * m0 - t - m0), 1e-4, 0.999))
        out[c] = (np.clip(mtf(y, m), 0, 1) * 255 + .5).astype(np.uint8)
    return np.ascontiguousarray(np.moveaxis(out, 0, -1))


def read_linear(path: str | Path, info: dict) -> np.ndarray:
    """Original eines linearen Imports als Master-Stack (C,H,W) float32 in ADU (0..65535), FITS-Orientierung
    (Zeile 0 unten, wie Pipeline._save es zurückdreht) – Pipeline.develop rechnet damit wie mit einem eigenen Stack."""
    path = Path(path)
    if path.suffix.lower() in (".tif", ".tiff"):
        import tifffile
        with tifffile.TiffFile(path) as t:
            page = t.pages[0]
            a = page.asarray()
            if a.ndim == 3 and a.shape[0] in (3, 4) and a.shape[-1] not in (3, 4):
                a = np.moveaxis(a, 0, -1)         # planar gespeichert (C,H,W)
        with Image.open(path) as im:
            orientation = im.getexif().get(274)
    else:
        with Image.open(path) as im:
            a = np.asarray(im)
            orientation = im.getexif().get(274)
    img = oriented(to_channels(normalized(a), (info.get("linear") or {}).get("bayer")), orientation)
    img *= np.float32(65535)
    return np.ascontiguousarray(img[:, ::-1])


def oriented(img: np.ndarray, orientation) -> np.ndarray:
    """EXIF-Ausrichtung auf (C,H,W) anwenden – wie ImageOps.exif_transpose für die Vorschau (nach dem Debayern)."""
    hw = lambda a: a.swapaxes(1, 2)                          # Transponieren (Zeilen ↔ Spalten)
    ops = {2: lambda a: a[:, :, ::-1], 3: lambda a: a[:, ::-1, ::-1], 4: lambda a: a[:, ::-1],
           5: hw, 6: lambda a: np.rot90(a, -1, axes=(1, 2)), 7: lambda a: hw(a[:, ::-1, ::-1]),
           8: lambda a: np.rot90(a, 1, axes=(1, 2))}
    return ops[orientation](img) if orientation in ops else img


class PictureMaster:
    """Ersatz für StackState bei importierten linearen Bildern: ein Bild, volle Abdeckung, keine Rohframes."""

    def __init__(self, mean: np.ndarray, key: str):
        self.mean = mean
        self.meta = {"frames": {}, "ref_fid": f"picture:{key}", "scale": 1, "picture": True}

    def result(self) -> tuple[np.ndarray, np.ndarray]:
        return self.mean, edge_coverage(self.mean)


def edge_coverage(mean: np.ndarray, limit: float = 0.15) -> np.ndarray:
    """Abdeckung (H,W) 0/1 eines fremden Stacks schätzen: Randzeilen/-spalten, die nur von wenigen Aufnahmen gedeckt
    sind (Versatz beim Registrieren in DSS/Siril), rauschen viel stärker oder sind leer. Höchstens `limit` je Seite."""
    L = mean.mean(0)
    H, W = L.shape
    cov = np.ones((H, W), np.float32)

    def bad(lines: np.ndarray) -> np.ndarray:            # je Zeile: Pixelrauschen (Nachbardifferenz) bzw. leer
        noise = np.median(np.abs(np.diff(lines, axis=1)), axis=1)
        ref = float(np.median(noise)) + 1e-9
        return (noise > 3 * ref) | (np.median(lines, axis=1) <= 0)

    for axis, n in ((0, H), (1, W)):
        b = bad(L if axis == 0 else L.T)
        k = int(limit * n)
        top = next((i for i in range(k) if not b[i]), k)
        bottom = next((i for i in range(k) if not b[n - 1 - i]), k)
        if axis == 0:
            cov[:top], cov[n - bottom:] = 0, 0
        else:
            cov[:, :top], cov[:, n - bottom:] = 0, 0
    return cov


def linear_source(db, project_id: int) -> dict | None:
    """Neuester importierter Stand eines Projekts, der linear ist und dessen Original noch existiert:
    {"rid", "path", "info"} – sonst None."""
    for r in db.q("SELECT id, files, stats FROM results WHERE project_id=? AND json_extract(stats,'$.imported.linear') IS NOT NULL "
                  "AND json_extract(stats,'$.developed') IS NULL "
                  "ORDER BY COALESCE(json_extract(stats,'$.imported.series.sequence'),0) DESC, id DESC", (project_id,)):
        f = json.loads(r["files"] or "{}").get("original")
        if f and Path(f).exists():
            return {"rid": r["id"], "path": f, "info": json.loads(r["stats"])["imported"]}
    return None


def attach_previews(eng):
    """Teleskop-Verläufe neben den Rohdaten einordnen, auch wenn der Scan erst nach dem Upload fertig wird."""
    def object_key(name):
        obj = objects.resolve_name(name)
        return objects.normalize_name(obj.id if obj else name)

    parents = {}
    for r in eng.db.q("SELECT DISTINCT f.project_id,f.night,json_extract(f.info,'$.instrument') instrument,p.name "
                      "FROM frames f JOIN projects p ON p.id=f.project_id WHERE f.kind='light'"):
        key = (object_key(r["name"]), r["instrument"], r["night"])
        parents.setdefault(key, set()).add(r["project_id"])
    children = {}
    for p in eng.db.q("SELECT id,options FROM projects WHERE json_extract(options,'$.input_type')='picture'"):
        opt = json.loads(p["options"] or "{}")
        series = (opt.get("imported") or {}).get("series")
        if not series:
            continue
        candidates = parents.get((object_key(series["object"]), series["instrument"], series["night"]), set())
        if len(candidates) != 1:
            continue
        parent = next(iter(candidates))
        eng.db.x("UPDATE projects SET options=json_set(options,'$.gallery_parent',?) WHERE id=? "
                 "AND EXISTS (SELECT 1 FROM projects parent WHERE parent.id=?)", (parent, p["id"], parent))
        children.setdefault(parent, []).append(p["id"])
    for parent, ids in children.items():
        eng.db.x("UPDATE projects SET options=json_set(COALESCE(options,'{}'),'$.companion_ids',json(?)) WHERE id=?",
                 (json.dumps(sorted(ids)), parent))


# Meldungen, die der Upload unverändert an den Browser gibt – alles andere nur ins Protokoll (keine Ausnahmetexte nach außen)
MSG_FORMAT = "Nur JPG, PNG und TIFF sind als fertige Bilder unterstützt."
MSG_PAGES = "Bitte ein einzelnes Bild statt einer mehrseitigen oder animierten Datei importieren."
MSG_TIFF = "Das TIFF muss ein Graustufen- oder RGB-Bild sein."
MESSAGES = (MSG_FORMAT, MSG_PAGES, MSG_TIFF)
MSG_UNREADABLE = "Datei ist kein lesbares Bild."


def user_message(e: Exception) -> str:
    """Bekannte Meldung aus MESSAGES, sonst eine allgemeine – der Ausnahmetext selbst geht nie an den Browser."""
    text = str(e)
    return next((m for m in MESSAGES if m == text), MSG_UNREADABLE)


def import_picture(eng, stream, filename: str, notify: bool = True) -> dict:
    """Nur Vorschauen konvertieren; Original und Herkunft bleiben erhalten. Identische Uploads sind idempotent."""
    with tempfile.TemporaryDirectory(prefix="bildimport-", dir=eng.s.work_dir) as tmp:
        src = Path(tmp) / "bild"
        digest = hashlib.sha256()
        with src.open("wb") as out:
            while block := stream.read(1024 * 1024):
                digest.update(block)
                out.write(block)
        key = "picture|" + digest.hexdigest()
        with eng.pl._scan_lock:
            old = eng.db.one("SELECT p.id,p.name FROM projects p JOIN results r ON r.project_id=p.id "
                             "WHERE p.key=? OR json_extract(r.stats,'$.imported.sha256')=? LIMIT 1", (key, digest.hexdigest()))
            if old:
                return {"id": old["id"], "name": old["name"], "duplicate": True}
            with Image.open(src) as im:
                fmt = im.format
                ext = {"JPEG": ".jpg", "PNG": ".png", "TIFF": ".tif"}.get(fmt)
                if not ext:
                    raise ValueError(MSG_FORMAT)
                if getattr(im, "n_frames", 1) != 1:
                    raise ValueError(MSG_PAGES)
                exif = im.getexif()
                series = telescope_series(filename, exif)
                observed = exif.get(36867) or exif.get(306)
                observed = observed if isinstance(observed, str) else None
                bit_depth, raw = 8, None
                if fmt == "TIFF":
                    import tifffile
                    a = tifffile.imread(src)
                    if a.ndim == 3 and a.shape[0] in (3, 4) and im.tag_v2.get(284) == 2:
                        a = np.moveaxis(a, 0, -1)
                    if a.ndim not in (2, 3) or (a.ndim == 3 and a.shape[-1] not in (3, 4)):
                        raise ValueError(MSG_TIFF)
                    bit_depth = a.dtype.itemsize * 8
                    raw = a
                elif im.mode.startswith("I;16") or im.mode == "I":
                    raw, bit_depth = np.clip(np.asarray(im), 0, 65535).astype(np.uint16), 16   # PNG 16 bit (Modus I;16 bzw. I)
                linear = None
                if raw is not None:
                    a = normalized(raw)
                    linear = linear_info(a, bit_depth)
                    if linear:                    # lineares Bild (Stack/Einzelbild): Vorschau strecken, sonst fast schwarz
                        linear["software"] = str(exif.get(305) or "").strip()[:60] or None
                        preview = Image.fromarray(stretched_preview(a, linear.get("bayer")))
                    else:                         # schon gestreckt: Anzeigewerte 0..1 unverändert
                        preview = Image.fromarray((np.clip(a[..., :3] if a.ndim == 3 else a, 0, 1) * 255 + .5).astype(np.uint8))
                    del raw, a
                    orientation = exif.get(274)
                    if orientation:
                        preview.getexif()[274] = orientation
                    preview = ImageOps.exif_transpose(preview).convert("RGB")
                else:
                    preview = ImageOps.exif_transpose(im).convert("RGB")
                preview.load()
            info = {"filename": filename, "format": fmt, "bit_depth": bit_depth,
                    "width": preview.width, "height": preview.height, "date_obs": observed,
                    "sha256": digest.hexdigest()}
            if linear:
                info["linear"] = linear
            for tag, k in ((271, "make"), (272, "model")):   # Gerät hilft beim Plate-Solving (Bildfeld des Smart-Teleskops)
                if isinstance(exif.get(tag), str) and exif[tag].strip():
                    info[k] = exif[tag].strip()[:60]
            if series:
                info["series"] = series
                key = f"picture-series|{objects.normalize_name(series['object'])}|{series['instrument']}|{series['night']}"
            group = eng.db.one("SELECT id,options FROM projects WHERE key=?", (key,))
            stem = Path(filename).stem[:80] or "Importiertes Bild"
            title = series["object"] if series else stem
            obj = objects.resolve_name(title)
            name = obj.display if obj else title
            dest = eng.s.output_dir / "importiert" / digest.hexdigest()
            dest.mkdir(parents=True, exist_ok=True)
            files = {"original": str(dest / ("original" + ext)), "jpg": str(dest / "ansicht.jpg"),
                     "png": str(dest / "ansicht.png"), "thumb": str(dest / "vorschau.jpg")}
            if fmt == "TIFF":
                files["tif"] = files["original"]
            try:
                shutil.copyfile(src, files["original"])
                preview.save(files["jpg"], "JPEG", quality=95)
                preview.save(files["png"], "PNG")
                preview.thumbnail((640, 640))
                preview.save(files["thumb"], "JPEG", quality=85)
                now = time.time()
                with eng.db.conn() as c:
                    if group:
                        pid = group["id"]
                        opt = json.loads(group["options"] or "{}")
                        if series["sequence"] >= opt.get("imported", {}).get("series", {}).get("sequence", -1):
                            opt["imported"] = info
                            c.execute("UPDATE projects SET options=? WHERE id=?", (json.dumps(opt), pid))
                    else:
                        pid = c.execute("INSERT INTO projects(key,name,obj_id,kind,ra,dec,options,created) VALUES(?,?,?,?,?,?,?,?)",
                                    (key, name, obj.id if obj else None, obj.kind if obj else None,
                                     obj.ra if obj else None, obj.dec if obj else None,
                                     json.dumps({"input_type": "picture", "imported": info}), now)).lastrowid
                    rid = c.execute("INSERT INTO results(project_id,created,n_frames,exposure,files,stats,review) VALUES(?,?,?,?,?,?,?)",
                                    (pid, now, 0, 0, json.dumps(files),
                                     json.dumps({"imported": info, "shape": [info["height"], info["width"]]}), "[]")).lastrowid
            except Exception:
                shutil.rmtree(dest, ignore_errors=True)
                raise
        if notify:
            attach_previews(eng)
            opt = json.loads(eng.db.one("SELECT options FROM projects WHERE id=?", (pid,))["options"])
            if not opt.get("gallery_parent"):
                eng.picture_solve.after_import(pid, rid, name)
        return {"id": pid, "name": name, "result_id": rid, "duplicate": False}
