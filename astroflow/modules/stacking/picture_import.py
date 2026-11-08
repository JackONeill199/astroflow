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
    return {"object": name, "instrument": model, "night": (stamp - dt.timedelta(hours=12)).date().isoformat(),
            "sequence": int(match[1])}


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
                bit_depth = 8
                if fmt == "TIFF":
                    import tifffile
                    a = tifffile.imread(src)
                    if a.ndim == 3 and a.shape[0] in (3, 4) and im.tag_v2.get(284) == 2:
                        a = np.moveaxis(a, 0, -1)
                    if a.ndim not in (2, 3) or (a.ndim == 3 and a.shape[-1] not in (3, 4)):
                        raise ValueError(MSG_TIFF)
                    bit_depth = a.dtype.itemsize * 8
                    if np.issubdtype(a.dtype, np.integer):
                        a = a.astype(np.float32) / np.iinfo(a.dtype).max
                    else:                         # Float-TIFF: Anzeigewerte 0..1, kein automatischer Stretch
                        a = np.nan_to_num(a, nan=0, posinf=1, neginf=0)
                    preview = Image.fromarray((np.clip(a, 0, 1) * 255 + .5).astype(np.uint8))
                    orientation = exif.get(274)
                    if orientation:
                        preview.getexif()[274] = orientation
                    preview = ImageOps.exif_transpose(preview).convert("RGB")
                else:
                    if im.mode.startswith("I;16") or im.mode == "I":
                        a = np.asarray(ImageOps.exif_transpose(im), dtype=np.float32)
                        bit_depth = 16
                        preview = Image.fromarray((np.clip(a / 65535, 0, 1) * 255 + .5).astype(np.uint8)).convert("RGB")
                    else:
                        preview = ImageOps.exif_transpose(im).convert("RGB")
                preview.load()
            info = {"filename": filename, "format": fmt, "bit_depth": bit_depth,
                    "width": preview.width, "height": preview.height, "date_obs": observed,
                    "sha256": digest.hexdigest()}
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
                eng.ctx.emit("stacking.imported", project_id=pid, result_id=rid, name=name)
        return {"id": pid, "name": name, "result_id": rid, "duplicate": False}
