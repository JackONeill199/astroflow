"""Ordner auf dem Server-Rechner auflisten – für den Ordnerdialog der Oberfläche (der Browser darf keine echten Pfade
herausgeben). Nur Ordnernamen und eine Zählung der Bilddateien im aktuellen Ordner, nie Dateiinhalte."""
from __future__ import annotations

import os
import string
from pathlib import Path

from .hidden import hidden_entry

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
                    if hidden_entry(e):
                        continue
                    if e.is_dir(follow_symlinks=True):
                        if not name.startswith("$") and name not in HIDDEN:
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


# ---- Suche in Unterordnern ------------------------------------------------------------------------------------
SEARCH_DEPTH = 4                 # Ebenen unter dem Startordner
SEARCH_LIMIT = 200               # Treffer höchstens
SEARCH_VISIT = 300000            # Verzeichniseinträge (Ordner + Dateien), die höchstens angesehen werden
SEARCH_SECONDS = 5.0             # Zeitgrenze (langsame Netz-/USB-Laufwerke)


def norm(text: str) -> str:
    """Suchschlüssel: Groß-/Kleinschreibung, Leerzeichen, „_“ und „-“ egal („NGC 4395“ = „ngc_4395“ = „ngc4395“)."""
    return "".join(c for c in text.casefold() if not c.isspace() and c not in "_-")


def search_folders(path: str, q: str, depth: int = SEARCH_DEPTH, limit: int = SEARCH_LIMIT) -> dict:
    """Ordner unter path (Breitensuche, höchstens depth Ebenen), deren Name q enthält. Treffer mit relativem Pfad;
    `truncated` = Grenze (Treffer, Einträge oder Zeit) erreicht, es kann weitere geben."""
    import time
    key = norm(q or "")
    if not path or not path.strip():
        raise ValueError("Suche braucht einen Startordner.")
    p = Path(path.strip().strip('"')).expanduser()
    if not p.is_dir():
        raise ValueError(f"Ordner nicht gefunden: {path}")
    p = p.resolve()
    depth = max(1, min(int(depth), 8))
    limit = max(1, min(int(limit), 1000))
    out = {"path": str(p), "q": q, "depth": depth, "hits": [], "truncated": False, "visited": 0}
    if not key:
        return out
    end = time.monotonic() + SEARCH_SECONDS
    level, hits, visited = [(p, "")], out["hits"], 0
    # Budget gilt je Verzeichniseintrag (auch Dateien), auch mitten in einem großen Ordner auf langsamen Laufwerken
    over = lambda: visited >= SEARCH_VISIT or time.monotonic() > end
    for d in range(1, depth + 1):
        nxt = []
        for folder, rel in level:
            if over():
                out["truncated"] = True
                break
            names = []
            try:
                with os.scandir(folder) as it:
                    for e in it:
                        visited += 1
                        if (visited & 63) == 0 and over():
                            out["truncated"] = True          # Teilergebnis dieses Ordners noch auswerten
                            break
                        try:
                            if (e.is_dir(follow_symlinks=False) and not e.name.startswith("$") and e.name not in HIDDEN
                                    and not hidden_entry(e)):
                                names.append(e.name)
                        except OSError:
                            pass
            except OSError:
                pass
            for name in sorted(names, key=str.lower):
                r = f"{rel}{os.sep}{name}" if rel else name
                if key in norm(name):
                    if len(hits) >= limit:
                        out["truncated"] = True
                        break
                    hits.append({"name": name, "path": str(folder / name), "rel": r, "depth": d})
                nxt.append((folder / name, r))
            if out["truncated"]:
                break
        if out["truncated"]:
            break
        level = nxt
    out["visited"] = visited
    return out
