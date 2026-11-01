"""SQLite-Persistenz (Projekte, Frames, Jobs, Ergebnisse)."""
from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects(
  id INTEGER PRIMARY KEY AUTOINCREMENT, key TEXT UNIQUE, name TEXT, obj_id TEXT, kind TEXT,
  ra REAL, dec REAL, radius REAL, options TEXT DEFAULT '{}', created REAL, restack INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS frames(
  id TEXT PRIMARY KEY, project_id INTEGER, path TEXT, kind TEXT, night TEXT, info TEXT,
  metrics TEXT, status TEXT DEFAULT 'new', reason TEXT DEFAULT '', score REAL DEFAULT 0,
  manual INTEGER DEFAULT 0, stacked INTEGER DEFAULT 0);
CREATE INDEX IF NOT EXISTS ix_frames_p ON frames(project_id);
CREATE TABLE IF NOT EXISTS jobs(
  id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER, state TEXT, progress REAL DEFAULT 0,
  stage TEXT DEFAULT '', message TEXT DEFAULT '', created REAL, started REAL, finished REAL, options TEXT DEFAULT '{}');
CREATE TABLE IF NOT EXISTS solves(
  key TEXT PRIMARY KEY, status TEXT, result TEXT, message TEXT, created REAL);
CREATE TABLE IF NOT EXISTS results(
  id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER, created REAL, n_frames INTEGER,
  exposure REAL, files TEXT, stats TEXT, review TEXT);
-- Hauptordner-Suche: einmal berechnete Fingerabdrücke je Quelldatei (gültig, solange Größe und Änderungszeit gleich sind)
CREATE TABLE IF NOT EXISTS library_index(
  path TEXT PRIMARY KEY, size INTEGER, mtime INTEGER, fp TEXT);
"""

# Spalten, die nach der ersten Version dazukamen: (Tabelle, Spalte, Definition) – werden bei Bedarf ergänzt
MIGRATIONS = [
    ("frames", "deleted", "INTEGER DEFAULT 0"),      # 1 = Rohdatei nach dem Stacking gelöscht (Speicher sparen)
    ("frames", "size", "INTEGER DEFAULT 0"),         # Dateigröße in Bytes (für die Speicheranzeige)
    ("frames", "stack_notified", "INTEGER DEFAULT 0"),  # Beobachtung nach gespeichertem Stack erfolgreich gemeldet
]


class DB:
    def __init__(self, path: Path):
        self.path = str(path)
        with self.conn() as c:
            c.executescript(SCHEMA)
            for table, col, decl in MIGRATIONS:
                cols = {r[1] for r in c.execute(f"PRAGMA table_info({table})")}
                if col not in cols:
                    c.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
                    if table == "frames" and col == "stack_notified":
                        # Altbestand: gelöschte Uploads und erfolgreich abgeschlossene Jobs waren bereits gemeldet.
                        # Unterbrochene/fehlgeschlagene Läufe bleiben vorsichtshalber offen.
                        c.execute("UPDATE frames SET stack_notified=1 WHERE stacked=1 AND (deleted=1 OR EXISTS "
                                  "(SELECT 1 FROM jobs j WHERE j.project_id=frames.project_id AND j.state='done' "
                                  "AND COALESCE(json_extract(j.options,'$.mode'),'')!='develop' "
                                  "AND COALESCE(json_extract(j.options,'$.action'),'')!='solve' "
                                  "AND j.id=(SELECT MAX(j2.id) FROM jobs j2 WHERE j2.project_id=frames.project_id)))")
            # abdeckender Index für Objektliste/Status: Zählungen ohne die großen info/metrics-Zeilen zu lesen
            # (nach den Migrationen, weil deleted/size erst dort entstehen; 20 000 Frames: ~90 ms -> ~1 ms je Abfrage)
            c.execute("CREATE INDEX IF NOT EXISTS ix_frames_pk ON frames(project_id, kind, status, night, deleted, stacked, size)")

    @contextmanager
    def conn(self):
        c = sqlite3.connect(self.path, timeout=30)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        try:
            yield c
            c.commit()
        finally:
            c.close()

    def q(self, sql: str, args=()) -> list[dict]:
        with self.conn() as c:
            return [dict(r) for r in c.execute(sql, args).fetchall()]

    def one(self, sql: str, args=()) -> dict | None:
        r = self.q(sql, args)
        return r[0] if r else None

    def x(self, sql: str, args=()) -> int:
        with self.conn() as c:
            return c.execute(sql, args).lastrowid

    def many(self, sql: str, rows) -> None:
        with self.conn() as c:
            c.executemany(sql, rows)

    # ---- Komfort ----------------------------------------------------------- #
    def set_frame(self, fid: str, **kw) -> None:
        cols = ", ".join(f"{k}=?" for k in kw)
        vals = [json.dumps(v) if isinstance(v, (dict, list)) else v for v in kw.values()]
        self.x(f"UPDATE frames SET {cols} WHERE id=?", (*vals, fid))

    def now(self) -> float:
        return time.time()
