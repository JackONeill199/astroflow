"""Versteckte Dateien und Ordner erkennen – werden nirgends angezeigt oder eingelesen (Ordnerdialog, „Hauptordner
durchsuchen“, Scanner, Upload). Anlass 08.11.2026: Auf einer Stellina-Platte tauchten `.thumbnail`, `.webaxs_*` und
`.webview` (Vorschaubilder der App) als „Fertige Bilder · neu“ auf und waren sogar vorgewählt.

Versteckt ist, was mit „.“ beginnt (auf jedem System) und unter Windows, was das Attribut „versteckt“ trägt – wie der
Explorer. Das Attribut „System“ zählt nur bei Dateien: Ordner mit eigenem Symbol (desktop.ini) tragen es manchmal,
ohne versteckt zu sein."""
from __future__ import annotations

import os
import stat

_HIDDEN = getattr(stat, "FILE_ATTRIBUTE_HIDDEN", 2)
_SYSTEM = getattr(stat, "FILE_ATTRIBUTE_SYSTEM", 4)


def hidden_name(name: str) -> bool:
    return name.startswith(".")


def _attrs(st) -> int:
    return getattr(st, "st_file_attributes", 0) or 0


def hidden_entry(e: os.DirEntry) -> bool:
    """Eintrag aus os.scandir. Unter Windows kommen die Attribute aus dem Verzeichniseintrag selbst (kein Plattenzugriff)."""
    if hidden_name(e.name):
        return True
    if os.name != "nt":
        return False
    try:
        a = _attrs(e.stat(follow_symlinks=False))
        return bool(a & _HIDDEN) or (bool(a & _SYSTEM) and not e.is_dir(follow_symlinks=False))
    except OSError:
        return False

