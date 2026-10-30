"""Ordner scannen, Frames klassifizieren, nach Objekt/Kamera/Nacht gruppieren."""
from __future__ import annotations

import datetime as dt
import hashlib
import os
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from . import objects
from .fits_io import FITS_EXT, FrameInfo, parse_date, read_info


@dataclass
class ScannedFrame:
    id: str
    path: str
    kind: str          # light|dark|flat|bias
    info: FrameInfo
    night: str


def frame_id(path: Path, root: Path) -> str:
    st = path.stat()
    try:
        rel = str(path.relative_to(root))
    except ValueError:
        rel = str(path)
    return hashlib.sha1(f"{rel}|{st.st_size}|{st.st_mtime_ns}".encode()).hexdigest()[:16]


def fingerprint(path: str | Path, block: int = 65536) -> str:
    """Inhalts-Fingerabdruck: Größe + Anfang (Header mit Aufnahmezeit) + Ende der Datei. Erkennt dieselbe
    Aufnahme in einem anderen Ordner (z. B. erneut hochgeladen), ohne die ganze Datei zu lesen."""
    p = Path(path)
    size = p.stat().st_size
    h = hashlib.sha1(str(size).encode())
    with open(p, "rb") as f:
        h.update(f.read(block))
        if size > 2 * block:
            f.seek(-block, os.SEEK_END)
            h.update(f.read(block))
    return h.hexdigest()[:20]


def classify(info: FrameInfo, path: Path) -> str:
    t = (info.imagetyp or "").lower()
    if "dark" in t:
        return "dark"
    if "flat" in t:
        return "flat"
    if "bias" in t or "offset" in t:
        return "bias"
    if "light" in t or "object" in t:
        return "light"
    low = "/".join(p.lower() for p in path.parts[-3:])
    for key, kind in (("dark", "dark"), ("flat", "flat"), ("bias", "bias"), ("offset", "bias")):
        if key in low:
            return kind
    return "light"


def night_of(info: FrameInfo, path: Path) -> str:
    d = parse_date(info.date_obs)
    if d is None:
        d = dt.datetime.fromtimestamp(path.stat().st_mtime)
    return (d - dt.timedelta(hours=12)).date().isoformat()


# Ordner, die keine Rohaufnahmen enthalten (Ergebnisse anderer Software).
# NICHT ausschließen: Stellina „02-images-adjust-focus“ – dort liegen die normalen Aufnahmen nach einem automatischen
# Nachfokussieren während der Beobachtung (oft der Großteil der Session; unscharfe sortiert die Qualitätsprüfung aus).
EXCLUDE_DIR = re.compile(r"^process$|^AS_P\d+$|\bAPP\b|^output$|^stacked$|^registered$|^pp_", re.I)
OBSERVATION = re.compile(r"observation_(.+)$", re.I)


def list_fits(root: Path) -> list[Path]:
    out = []
    for dp, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if not EXCLUDE_DIR.search(d)]
        for f in files:
            if f.lower().endswith(FITS_EXT):
                out.append(Path(dp) / f)
    return sorted(out)


def object_from_path(path: Path) -> str | None:
    """Stellina & Co.: Objektname steckt im Ordner '..._observation_M57'."""
    for part in reversed(path.parts[:-1]):
        m = OBSERVATION.search(part)
        if m:
            return m.group(1).replace("_", " ").strip() or None
    return None


def scan_dir(root: Path, known_ids: set[str], threads: int = 8) -> tuple[list[ScannedFrame], list[str]]:
    """Gibt nur neue Frames zurück (id nicht in known_ids) und eine Liste von Fehlern."""
    paths = [p for p in list_fits(root) if frame_id(p, root) not in known_ids]
    errors: list[str] = []

    def one(p: Path):
        try:
            info = read_info(p)
            info.fp = fingerprint(p)
            if not info.object:
                info.object = object_from_path(p)
            return ScannedFrame(frame_id(p, root), str(p), classify(info, p), info, night_of(info, p))
        except Exception as e:  # defekte Datei
            errors.append(f"{p.name}: {e}")
            return None

    with ThreadPoolExecutor(threads) as ex:
        frames = [f for f in ex.map(one, paths) if f]
    return frames, errors


@dataclass
class Assignment:
    key: str
    name: str
    obj_id: str | None
    kind: str | None
    ra: float | None
    dec: float | None


def assign_project(info: FrameInfo, centers: dict[str, tuple[float, float, float]]) -> Assignment:
    """Ordnet einen Light-Frame einem Projekt zu. centers: key -> (ra, dec, radius_deg)."""
    cam = f"{info.camera}|{info.hw[1]}x{info.hw[0]}"
    entry = objects.resolve_name(info.object)
    if entry:
        return Assignment(f"{entry.id}|{cam}", entry.display, entry.id, entry.kind, entry.ra, entry.dec)
    if info.object and info.object.strip():
        nm = info.object.strip()
        ext = objects.resolve_extended(nm)                     # Objekttyp (Profil) aus dem großen Katalog, Name bleibt
        return Assignment(f"{objects.normalize_name(nm)}|{cam}", nm, None, ext.kind if ext else None, info.ra, info.dec)
    if info.ra is not None and info.dec is not None:
        for key, (ra, dec, rad) in centers.items():
            if key.endswith("|" + cam) and objects.ang_sep(info.ra, info.dec, ra, dec) < rad:
                return Assignment(key, "", None, None, ra, dec)
        near = objects.nearest(info.ra, info.dec)
        if near is None and info.solve:                        # gelöstes Feld: großen Katalog (ASTAP) befragen
            near = objects.nearest_extended(info.ra, info.dec, 0.35 * min(info.solve["fov_w"], info.solve["fov_h"]))
        if near:
            return Assignment(f"{near.id}|{cam}", near.display, near.id, near.kind, near.ra, near.dec)
        nm = f"Feld RA {info.ra / 15:.2f}h Dec {info.dec:+.1f}°"
        return Assignment(f"FELD{info.ra:.1f}{info.dec:+.1f}|{cam}", nm, None, None, info.ra, info.dec)
    return Assignment(f"UNBEKANNT|{cam}", "Unbekanntes Objekt", None, None, None, None)
