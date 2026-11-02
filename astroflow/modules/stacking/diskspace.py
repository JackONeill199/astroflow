"""Freier Speicher auf dem Datenlaufwerk: Aufträge und Import halten an, bevor die Platte voll ist.

Anlass (01.11.2026): Der Import der ganzen Stellina-Platte (E:) kopierte 95 GB nach C: – Hardlinks gehen nicht über
Laufwerksgrenzen. C: lief voll, danach scheiterte jeder Auftrag (auch die Datenbank konnte nicht mehr schreiben). Seit 0.12.0 bindet der
Import solche Aufnahmen am Ort ein, statt sie zu kopieren (`library.py`)."""
from __future__ import annotations

import os
import shutil
import sqlite3
from pathlib import Path

MIN_FREE = 5e9          # darunter starten keine Aufträge (Stacks, Ausgaben und Datenbank brauchen Platz)
ABORT_FREE = 2e9        # darunter bricht ein laufender Auftrag ab und die Warteschlange hält an


def free(path: Path) -> float | None:
    try:
        return shutil.disk_usage(path).free
    except OSError:
        return None


def gb(b: float) -> str:
    return f"{b / 1e9:.1f} GB".replace(".", ",")


def is_full(e: BaseException) -> bool:
    """Fehler, die von einem vollen Datenträger kommen (Python, Windows, SQLite)."""
    if isinstance(e, OSError) and (e.errno == 28 or getattr(e, "winerror", None) in (39, 112)):
        return True
    if isinstance(e, sqlite3.OperationalError) and "full" in str(e):
        return True
    return "No space left" in str(e) or "disk is full" in str(e)


def same_drive(a: Path, b: Path) -> bool:
    """Hardlink möglich? (gleiches Laufwerk/Dateisystem)"""
    try:
        return os.stat(a).st_dev == os.stat(b).st_dev
    except OSError:
        return False


class DiskFull(RuntimeError):
    pass
