"""Speicherbelegung des Datenordners anzeigen und ungenutzte Daten aufräumen.

`usage()` misst, was Platz belegt (je Bereich und je Objekt), `scan()` sucht Aufräumbares, `cleanup()` löscht die gewählten
Bereiche. Gelöscht wird nur, was `scan()` selbst wieder findet – der Client schickt nur Schlüssel, nie Pfade.
Nie angefasst: Rohdaten (auch nicht hochgeladene), Master-Stacks bestehender Objekte, neueste Ergebnisse, Einstellungen.
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import time
from pathlib import Path

TEMP_AGE_S = 3600            # temporäre Dateien erst nach einer Stunde aufräumen (könnten noch in Arbeit sein)
KEEP_VERSIONS = 2            # ältere Versionen: so viele neueste je Objekt bleiben


def _size(p: Path) -> int:
    try:
        if p.is_file():
            return p.stat().st_size
        return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
    except OSError:
        return 0


def _norm(p) -> str:
    return os.path.normcase(os.path.abspath(str(p)))


def _inside(p: Path, root: Path) -> bool:
    try:
        p.resolve().relative_to(root.resolve())
        return True
    except (ValueError, OSError):
        return False


def _results(db) -> list[dict]:
    rows = db.q("SELECT r.id, r.project_id, r.created, r.files, p.name, p.options FROM results r JOIN projects p ON p.id=r.project_id ORDER BY r.id DESC")
    for r in rows:
        r["files"] = json.loads(r["files"] or "{}")
        r["picture"] = json.loads(r["options"] or "{}").get("input_type") == "picture"
    return rows


def usage(s, db) -> dict:
    """Belegung je Bereich (Bytes) und die größten Objekte (Ausgabe + Master-Stack)."""
    work_named = {"calibration", "glow", "gaia"}
    cats = []

    def add(key, label, paths, detail=""):
        cats.append({"key": key, "label": label, "bytes": sum(_size(Path(p)) for p in paths), "detail": detail})

    out = [p for p in s.output_dir.iterdir()] if s.output_dir.exists() else []
    add("output", "Fertige Bilder", out, "TIFF, PNG, JPG und linearer FITS je Version")
    stacks = [p for p in s.work_dir.glob("p*") if p.name[1:].isdigit()]
    add("stacks", "Master-Stacks", stacks, "nötig zum Nachstacken und Neu-Entwickeln")
    add("uploads", "Hochgeladene Rohdaten", [s.uploads_dir], "noch nicht gestackt oder Löschen aus")
    add("caches", "Kalibrierung & Kataloge", [s.work_dir / n for n in work_named], "Master-Darks/Flats, Sensorglühen, Gaia")
    add("temp", "Temporäre Dateien", [p for p in s.work_dir.iterdir() if p.is_dir() and p not in stacks and p.name not in work_named]
        if s.work_dir.exists() else [], "Plate-Solving, Bildimport")
    add("db", "Datenbank", [s.db_path, Path(str(s.db_path) + "-wal"), Path(str(s.db_path) + "-shm")])
    add("sage", "Sage", [s.data_dir / "sage"], "Enzyklopädie, Audio, Bildvorschauen")
    add("skytrace", "SkyTrace", [s.data_dir / "skytrace"])
    add("backups", "Sicherungen", [s.data_dir / "backups", *s.data_dir.glob("*.vor-*")])
    known = {_norm(p) for p in (s.output_dir, s.work_dir, s.input_dir, s.data_dir / "sage", s.data_dir / "skytrace", s.data_dir / "backups")}
    known |= {_norm(p) for p in (s.db_path, str(s.db_path) + "-wal", str(s.db_path) + "-shm", *s.data_dir.glob("*.vor-*"))}
    add("other", "Sonstiges", [p for p in s.data_dir.iterdir() if _norm(p) not in known])
    cats = [c for c in cats if c["bytes"] > 0]

    per: dict[int, dict] = {}
    for r in _results(db):
        e = per.setdefault(r["project_id"], {"id": r["project_id"], "name": r["name"], "output": 0, "stack": 0, "versions": 0})
        e["versions"] += 1
        e["output"] += sum(_size(Path(f)) for f in set(r["files"].values()))
    for p in db.q("SELECT id, name FROM projects"):
        st = _size(s.work_dir / f"p{p['id']}")
        if st:
            per.setdefault(p["id"], {"id": p["id"], "name": p["name"], "output": 0, "stack": 0, "versions": 0})["stack"] = st
    projects = sorted(per.values(), key=lambda e: -(e["output"] + e["stack"]))
    total = sum(c["bytes"] for c in cats)
    try:
        du = shutil.disk_usage(s.data_dir)
        disk = {"free": du.free, "total": du.total}
    except OSError:
        disk = None
    return {"total": total, "categories": sorted(cats, key=lambda c: -c["bytes"]), "projects": projects[:12], "disk": disk}


# ------------------------------------------------------------------ Aufräumbares #
def _orphan_output(s, db) -> list[Path]:
    """Dateien in der Ausgabe, auf die kein Ergebnis mehr verweist (z. B. abgebrochene Läufe)."""
    ref = {_norm(f) for r in _results(db) for f in r["files"].values()}
    if not s.output_dir.exists():
        return []
    return [p for p in s.output_dir.rglob("*") if p.is_file() and _norm(p) not in ref]


def _orphan_stacks(s, db) -> list[Path]:
    """Arbeitsordner gelöschter Objekte."""
    ids = {p["id"] for p in db.q("SELECT id FROM projects")}
    return [p for p in s.work_dir.glob("p*") if p.is_dir() and p.name[1:].isdigit() and int(p.name[1:]) not in ids]


def _temp(s) -> list[Path]:
    old = time.time() - TEMP_AGE_S
    found = []
    for d in ("solve", "picture_solve"):
        found += [p for p in (s.work_dir / d).glob("*") if p.stat().st_mtime < old] if (s.work_dir / d).exists() else []
    found += [p for p in s.work_dir.glob("bildimport-*") if p.stat().st_mtime < old]
    return found


def _old_versions(db, keep: int) -> list[dict]:
    """Ergebnisse jenseits der `keep` neuesten je Objekt (nicht bei importierten Bildern: dort sind Versionen Teleskop-Verläufe)."""
    seen: dict[int, int] = {}
    old = []
    for r in _results(db):
        if r["picture"]:
            continue
        seen[r["project_id"]] = seen.get(r["project_id"], 0) + 1
        if seen[r["project_id"]] > keep:
            old.append(r)
    return old


def _empty_dirs(s) -> list[Path]:
    """Ordner ohne Dateien (auch verschachtelt) – tiefste zuerst, damit das Löschen der Reihe nach klappt."""
    found, empty, old = [], set(), time.time() - TEMP_AGE_S
    for root in (s.uploads_dir, s.output_dir):
        if root.exists():
            for d in sorted((p for p in root.rglob("*") if p.is_dir()), key=lambda p: -len(p.parts)):
                if d.stat().st_mtime < old and all(_norm(c) in empty for c in d.iterdir()):   # frische Ordner: Upload läuft evtl.
                    empty.add(_norm(d))
                    found.append(d)
    return found


def _db_free(s) -> int:
    with sqlite3.connect(s.db_path, timeout=30) as c:
        free = c.execute("PRAGMA freelist_count").fetchone()[0] * c.execute("PRAGMA page_size").fetchone()[0]
    return free + _size(Path(str(s.db_path) + "-wal"))


def scan(s, db, keep: int = KEEP_VERSIONS) -> list[dict]:
    """Aufräumbare Bereiche mit Größe. `default`: unbedenklich, in der Oberfläche vorausgewählt."""
    items = []
    f = _orphan_output(s, db)
    items.append({"key": "orphan_output", "label": "Verwaiste Ausgabedateien", "count": len(f), "bytes": sum(_size(p) for p in f),
                  "detail": "Bilddateien ohne zugehörige Version (abgebrochene Läufe, gelöschte Objekte)", "default": True})
    f = _orphan_stacks(s, db)
    items.append({"key": "orphan_stacks", "label": "Stacks gelöschter Objekte", "count": len(f), "bytes": sum(_size(p) for p in f),
                  "detail": "Arbeitsordner von Objekten, die es nicht mehr gibt", "default": True})
    f = _temp(s)
    items.append({"key": "temp", "label": "Temporäre Dateien", "count": len(f), "bytes": sum(_size(p) for p in f),
                  "detail": "Reste von Plate-Solving und Bildimport (älter als 1 Stunde)", "default": True})
    f = _empty_dirs(s)
    items.append({"key": "empty_dirs", "label": "Leere Ordner", "count": len(f), "bytes": 0,
                  "detail": "in Uploads und Ausgabe", "default": True})
    items.append({"key": "db_compact", "label": "Datenbank verdichten", "count": 1, "bytes": _db_free(s),
                  "detail": "Freien Platz in der Datenbank zurückgeben (VACUUM)", "default": True})
    old = _old_versions(db, keep)
    items.append({"key": "old_versions", "label": f"Ältere Versionen (neueste {keep} je Objekt bleiben)", "count": len(old),
                  "bytes": sum(_size(Path(p)) for r in old for p in set(r["files"].values())),
                  "detail": "Frühere Entwicklungen samt Dateien – nicht wiederherstellbar", "default": False, "keep": keep})
    b = [s.data_dir / "backups", *s.data_dir.glob("*.vor-*")]
    items.append({"key": "backups", "label": "Sicherungen", "count": sum(1 for p in b if p.exists()), "bytes": sum(_size(p) for p in b),
                  "detail": "Datenbank-Sicherungen von Umzügen und Umbauten", "default": False})
    return [i for i in items if i["count"] and (i["bytes"] or i["key"] == "empty_dirs")]


def _rm(p: Path, root: Path) -> int:
    """Löscht p (Datei oder Ordner) – nur innerhalb von root. Gibt freigegebene Bytes zurück."""
    if not _inside(p, root) or not p.exists():
        return 0
    n = _size(p)
    if p.is_dir():
        shutil.rmtree(p, ignore_errors=True)
    else:
        p.unlink(missing_ok=True)
    return n


def _delete_results(s, db, rows: list[dict]) -> int:
    """Ergebnisse samt Dateien löschen, die keine verbleibende Version mehr nutzt. Gibt freigegebene Bytes zurück."""
    ids = {r["id"] for r in rows}
    kept = {_norm(f) for r in _results(db) if r["id"] not in ids for f in r["files"].values()}
    freed = 0
    for r in rows:
        for f in set(r["files"].values()):
            if _norm(f) not in kept:
                freed += _rm(Path(f), s.output_dir)
        db.x("DELETE FROM results WHERE id=?", (r["id"],))
    return freed


def delete_version(s, db, rid: int) -> dict:
    """Eine einzelne Version löschen. Die letzte Version eines Objekts bleibt (dafür das Objekt löschen)."""
    r = next((x for x in _results(db) if x["id"] == rid), None)
    if not r:
        raise KeyError(rid)
    if db.one("SELECT COUNT(*) n FROM results WHERE project_id=?", (r["project_id"],))["n"] < 2:
        raise ValueError("Die einzige Version kann nicht gelöscht werden – dafür das ganze Objekt löschen.")
    return {"freed": _delete_results(s, db, [r]), "project_id": r["project_id"]}


def cleanup(s, db, keys: list[str], keep: int = KEEP_VERSIONS) -> dict:
    """Gewählte Bereiche aufräumen. Gibt {freed, removed: {key: Anzahl}} zurück."""
    keep = max(1, int(keep))
    freed, removed = 0, {}
    if "old_versions" in keys:                        # zuerst: deren Dateien sollen nicht als „verwaist“ doppelt zählen
        old = _old_versions(db, keep)
        freed += _delete_results(s, db, old)
        removed["old_versions"] = len(old)
    if "orphan_output" in keys:
        f = _orphan_output(s, db)
        freed += sum(_rm(p, s.output_dir) for p in f)
        removed["orphan_output"] = len(f)
    if "orphan_stacks" in keys:
        f = _orphan_stacks(s, db)
        freed += sum(_rm(p, s.work_dir) for p in f)
        removed["orphan_stacks"] = len(f)
    if "temp" in keys:
        f = _temp(s)
        freed += sum(_rm(p, s.work_dir) for p in f)
        removed["temp"] = len(f)
    if "backups" in keys:
        f = [s.data_dir / "backups", *s.data_dir.glob("*.vor-*")]
        freed += sum(_rm(p, s.data_dir) for p in f)
        removed["backups"] = sum(1 for p in f if not p.exists())
    if "empty_dirs" in keys:                          # nach den Dateien, damit frisch geleerte Ordner mitgehen
        f = _empty_dirs(s)
        for d in f:
            if _inside(d, s.data_dir) and d.exists() and not any(d.iterdir()):
                d.rmdir()
        removed["empty_dirs"] = len(f)
    if "db_compact" in keys:
        before = _size(s.db_path) + _size(Path(str(s.db_path) + "-wal"))
        c = sqlite3.connect(s.db_path, timeout=30, isolation_level=None)
        try:
            c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            c.execute("VACUUM")
        finally:
            c.close()
        freed += max(0, before - _size(s.db_path) - _size(Path(str(s.db_path) + "-wal")))
        removed["db_compact"] = 1
    return {"freed": freed, "removed": removed}
