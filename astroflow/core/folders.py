"""Ordner auf dem Server-Rechner auflisten – für den Ordnerdialog der Oberfläche (der Browser darf keine echten Pfade
herausgeben). Nur Ordnernamen und eine Zählung der Bilddateien im aktuellen Ordner, nie Dateiinhalte."""
from __future__ import annotations

import os
import string
from pathlib import Path

HIDDEN = {"System Volume Information", "$RECYCLE.BIN", "$Recycle.Bin", "Recovery", "Config.Msi"}
FITS = (".fit", ".fits", ".fts")
PICTURES = (".jpg", ".jpeg", ".png", ".tif", ".tiff")
MAX_DIRS = 1000
MAX_SCAN = 20000                 # Einträge, die für die Zählung höchstens angesehen werden


def drives() -> list[str]:
    """Windows: vorhandene Laufwerke („C:\\“, „E:\\“ …); sonst „/“ und das Benutzerverzeichnis."""
    if os.name == "nt":
        if hasattr(os, "listdrives"):
            try:
                return [d for d in os.listdrives() if os.path.isdir(d)]
            except OSError:
                pass
        return [f"{c}:\\" for c in string.ascii_uppercase if os.path.isdir(f"{c}:\\")]
    return ["/", str(Path.home())]


def list_folders(path: str | None) -> dict:
    """path leer → Laufwerksliste. Sonst Unterordner, übergeordneter Ordner (None am Laufwerk) und Bildzählung."""
    roots = drives()
    if not path or not path.strip():
        return {"path": "", "parent": None, "folders": [{"name": d, "path": d} for d in roots], "drives": roots,
                "fits": 0, "pictures": 0}
    p = Path(path.strip().strip('"')).expanduser()
    if not p.is_dir():
        raise ValueError(f"Ordner nicht gefunden: {path}")
    p = p.resolve()
    dirs, fits, pics, seen = [], 0, 0, 0
    try:
        with os.scandir(p) as it:
            for e in it:
                seen += 1
                if seen > MAX_SCAN:
                    break
                name = e.name
                try:
                    if e.is_dir(follow_symlinks=True):
                        if not name.startswith((".", "$")) and name not in HIDDEN:
                            dirs.append(name)
                    else:
                        low = name.lower()
                        fits += low.endswith(FITS)
                        pics += low.endswith(PICTURES)
                except OSError:
                    pass
    except PermissionError:
        raise ValueError(f"Kein Zugriff auf {p}")
    dirs.sort(key=str.lower)
    parent = None if p.parent == p else str(p.parent)
    return {"path": str(p), "parent": parent, "folders": [{"name": n, "path": str(p / n)} for n in dirs[:MAX_DIRS]],
            "more": max(0, len(dirs) - MAX_DIRS), "drives": roots, "fits": fits, "pictures": pics}
