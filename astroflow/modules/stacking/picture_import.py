"""Fertige Bilder unverändert archivieren und mit Anzeigevorschauen in die Galerie übernehmen."""
from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
import time
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

from . import objects

PICTURE_EXT = (".jpg", ".jpeg", ".png", ".tif", ".tiff")


def import_picture(eng, stream, filename: str) -> dict:
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
            old = eng.db.one("SELECT id,name FROM projects WHERE key=?", (key,))
            if old:
                return {"id": old["id"], "name": old["name"], "duplicate": True}
            with Image.open(src) as im:
                fmt = im.format
                ext = {"JPEG": ".jpg", "PNG": ".png", "TIFF": ".tif"}.get(fmt)
                if not ext:
                    raise ValueError("Nur JPG, PNG und TIFF sind als fertige Bilder unterstützt.")
                if getattr(im, "n_frames", 1) != 1:
                    raise ValueError("Bitte ein einzelnes Bild statt einer mehrseitigen oder animierten Datei importieren.")
                exif = im.getexif()
                observed = exif.get(36867) or exif.get(306)
                observed = observed if isinstance(observed, str) else None
                bit_depth = 8
                if fmt == "TIFF":
                    import tifffile
                    a = tifffile.imread(src)
                    if a.ndim == 3 and a.shape[0] in (3, 4) and im.tag_v2.get(284) == 2:
                        a = np.moveaxis(a, 0, -1)
                    if a.ndim not in (2, 3) or (a.ndim == 3 and a.shape[-1] not in (3, 4)):
                        raise ValueError("Das TIFF muss ein Graustufen- oder RGB-Bild sein.")
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
            stem = Path(filename).stem[:80] or "Importiertes Bild"
            obj = objects.resolve_name(stem)
            name = obj.display if obj else stem
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
        eng.ctx.emit("stacking.imported", project_id=pid, result_id=rid, name=name)
        return {"id": pid, "name": name, "result_id": rid, "duplicate": False}
