"""Wissensbasis von Sage (SQLite + FTS5): Objekte, Kennungen, Quellen, Fakten, Texte, Beziehungen, Notizen, Entdeckungen,
Läufe und Kosten. Ein Nutzer, ein Prozess – eine Verbindung mit Sperre reicht (WAL für lesende Zugriffe nebenher)."""
from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS entities(id TEXT PRIMARY KEY, kind TEXT, name TEXT, main_id TEXT DEFAULT '', otype TEXT DEFAULT '',
  otype_label TEXT DEFAULT '', ra REAL, dec REAL, size REAL, summary TEXT DEFAULT '', status TEXT DEFAULT 'stub',
  data TEXT DEFAULT '{}', created REAL, updated REAL, refreshed REAL);
CREATE TABLE IF NOT EXISTS aliases(norm TEXT PRIMARY KEY, entity_id TEXT, alias TEXT);
CREATE INDEX IF NOT EXISTS ix_alias_e ON aliases(entity_id);
CREATE TABLE IF NOT EXISTS sources(id INTEGER PRIMARY KEY AUTOINCREMENT, key TEXT UNIQUE, kind TEXT, title TEXT, url TEXT DEFAULT '',
  authors TEXT DEFAULT '', year INTEGER, publisher TEXT DEFAULT '', abstract TEXT DEFAULT '', retrieved REAL, data TEXT DEFAULT '{}');
CREATE TABLE IF NOT EXISTS facts(id INTEGER PRIMARY KEY AUTOINCREMENT, entity_id TEXT, key TEXT, value TEXT, num REAL, unit TEXT DEFAULT '',
  err REAL, source_id INTEGER, origin TEXT, status TEXT, note TEXT DEFAULT '', created REAL);
CREATE INDEX IF NOT EXISTS ix_facts_e ON facts(entity_id);
CREATE TABLE IF NOT EXISTS sections(id INTEGER PRIMARY KEY AUTOINCREMENT, entity_id TEXT, key TEXT, title TEXT, body TEXT,
  sources TEXT DEFAULT '[]', flags TEXT DEFAULT '[]', origin TEXT, status TEXT, model TEXT DEFAULT '', run_id INTEGER,
  version INTEGER DEFAULT 1, created REAL, updated REAL);
CREATE INDEX IF NOT EXISTS ix_sections_e ON sections(entity_id);
CREATE TABLE IF NOT EXISTS edges(id INTEGER PRIMARY KEY AUTOINCREMENT, src TEXT, dst TEXT, rel TEXT, origin TEXT, status TEXT,
  source_id INTEGER, note TEXT DEFAULT '', created REAL, UNIQUE(src, dst, rel));
CREATE TABLE IF NOT EXISTS notes(id INTEGER PRIMARY KEY AUTOINCREMENT, entity_id TEXT, kind TEXT, title TEXT DEFAULT '', body TEXT DEFAULT '',
  date TEXT DEFAULT '', data TEXT DEFAULT '{}', created REAL, updated REAL);
CREATE TABLE IF NOT EXISTS insights(id INTEGER PRIMARY KEY AUTOINCREMENT, key TEXT UNIQUE, kind TEXT, title TEXT, body TEXT,
  reason TEXT DEFAULT '', entities TEXT DEFAULT '[]', sources TEXT DEFAULT '[]', data TEXT DEFAULT '{}', origin TEXT,
  created REAL, seen INTEGER DEFAULT 0, dismissed INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS runs(id INTEGER PRIMARY KEY AUTOINCREMENT, entity_id TEXT, kind TEXT, state TEXT, started REAL, finished REAL,
  steps TEXT DEFAULT '[]', cost REAL DEFAULT 0, tokens INTEGER DEFAULT 0, model TEXT DEFAULT '', error TEXT);
CREATE TABLE IF NOT EXISTS usage(id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, model TEXT, tokens_in INTEGER, tokens_out INTEGER,
  cost REAL, estimated INTEGER, run_id INTEGER, purpose TEXT);
CREATE TABLE IF NOT EXISTS stories(id INTEGER PRIMARY KEY AUTOINCREMENT, entity_id TEXT, format TEXT, title TEXT, body TEXT,
  segments TEXT DEFAULT '[]', image TEXT DEFAULT '{}', model TEXT DEFAULT '', audio TEXT DEFAULT '{}', created REAL);
CREATE TABLE IF NOT EXISTS chats(id INTEGER PRIMARY KEY AUTOINCREMENT, scope TEXT, role TEXT, content TEXT, data TEXT DEFAULT '{}', created REAL);
CREATE TABLE IF NOT EXISTS http_cache(key TEXT PRIMARY KEY, url TEXT, body TEXT, fetched REAL);
CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);
CREATE VIRTUAL TABLE IF NOT EXISTS search USING fts5(entity_id UNINDEXED, name, aliases, body, tokenize='unicode61 remove_diacritics 2');
"""
JSON_COLS = {"data", "sources", "flags", "entities", "steps", "segments", "image", "audio"}

# Status-Bedeutungen (Oberfläche zeigt sie als Abzeichen)
ENTITY_STATUS = {"stub": "Grundeintrag", "catalog": "Katalogdaten", "researched": "KI-recherchiert", "reviewed": "geprüft"}


def norm(name: str) -> str:
    """Kennung vergleichbar machen: „M 45“ = „M45“ = „Messier 45“, „NGC  1976“ = „ngc1976“, „NAME Pleiades“ = „pleiades“."""
    n = re.sub(r"\s+", " ", (name or "").strip().lower())
    n = re.sub(r"^name ", "", n)
    n = re.sub(r"^messier ?", "m ", n)
    # führende Nullen erst im Ersatz entfernen – „0*(\d+)“ wäre mehrdeutig und bei vielen Nullen quadratisch langsam
    n = re.sub(r"^(m|ngc|ic|sh ?2|ugc|pgc|mel|cr|b|vdb|ldn|lbn|abell|arp|hcg|pk|c|caldwell) ?-?(\d+)\b",
               lambda m: m.group(1).replace(" ", "") + " " + (m.group(2).lstrip("0") or "0"), n)
    return n.replace("caldwell", "c")


def slug(name: str) -> str:
    s = norm(name).replace(" ", "")
    s = re.sub(r"[^a-z0-9+\-]+", "-", s.replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss")).strip("-")
    return s[:60] or "objekt"


def month_start(t: float | None = None) -> float:
    lt = time.localtime(t or time.time())
    return time.mktime((lt.tm_year, lt.tm_mon, 1, 0, 0, 0, 0, 0, -1))


class KB:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.RLock()
        self.c = sqlite3.connect(str(self.path), check_same_thread=False, isolation_level=None)
        self.c.row_factory = sqlite3.Row
        self.c.execute("PRAGMA journal_mode=WAL")
        self.c.executescript(SCHEMA)

    # ---- Grundfunktionen ---------------------------------------------------------
    @staticmethod
    def _row(r) -> dict | None:
        if r is None:
            return None
        d = dict(r)
        for k in JSON_COLS & d.keys():
            try:
                d[k] = json.loads(d[k]) if d[k] else ({} if k in ("data", "image", "audio") else [])
            except ValueError:
                pass
        return d

    def q(self, sql: str, args=()) -> list[dict]:
        with self._lock:
            return [self._row(r) for r in self.c.execute(sql, args).fetchall()]

    def one(self, sql: str, args=()) -> dict | None:
        with self._lock:
            return self._row(self.c.execute(sql, args).fetchone())

    def x(self, sql: str, args=()) -> int:
        with self._lock:
            return self.c.execute(sql, args).lastrowid

    @staticmethod
    def _j(v):
        return json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v

    def meta(self, k: str, default=None):
        r = self.one("SELECT v FROM meta WHERE k=?", (k,))
        return json.loads(r["v"]) if r else default

    def set_meta(self, k: str, v):
        self.x("INSERT OR REPLACE INTO meta(k, v) VALUES(?, ?)", (k, json.dumps(v)))

    # ---- Objekte & Kennungen -------------------------------------------------------
    def resolve(self, name: str) -> dict | None:
        """Objekt über eine Kennung oder seine ID finden."""
        if not name:
            return None
        e = self.one("SELECT * FROM entities WHERE id=?", (name,))
        if e:
            return e
        a = self.one("SELECT entity_id FROM aliases WHERE norm=?", (norm(name),))
        if not a:                                    # „M45 Plejaden“: erst die Katalognummer, dann der Name
            m = re.match(r"\s*((?:m|ngc|ic|sh2|ugc|messier)\s?-?\d+)", name, re.I)
            a = m and self.one("SELECT entity_id FROM aliases WHERE norm=?", (norm(m.group(1)),))
        return self.one("SELECT * FROM entities WHERE id=?", (a["entity_id"],)) if a else None

    def entity(self, eid: str) -> dict | None:
        return self.one("SELECT * FROM entities WHERE id=?", (eid,))

    def upsert_entity(self, eid: str, **vals) -> dict:
        now = time.time()
        with self._lock:
            cur = self.entity(eid)
            if cur is None:
                self.x("INSERT INTO entities(id, kind, name, created, updated) VALUES(?,?,?,?,?)",
                       (eid, vals.get("kind", "object"), vals.get("name") or eid, now, now))
                cur = self.entity(eid)
            if vals:
                if "data" in vals:
                    vals["data"] = {**(cur.get("data") or {}), **vals["data"]}
                cols = ", ".join(f"{k}=?" for k in vals)
                self.x(f"UPDATE entities SET {cols}, updated=? WHERE id=?", (*[self._j(v) for v in vals.values()], now, eid))
            self.index(eid)
            return self.entity(eid)

    def add_alias(self, eid: str, alias: str, force: bool = False):
        n = norm(alias)
        if not n:
            return
        if force:
            self.x("INSERT OR REPLACE INTO aliases(norm, entity_id, alias) VALUES(?,?,?)", (n, eid, alias.strip()))
        else:
            self.x("INSERT OR IGNORE INTO aliases(norm, entity_id, alias) VALUES(?,?,?)", (n, eid, alias.strip()))

    def aliases(self, eid: str) -> list[str]:
        return [r["alias"] for r in self.q("SELECT alias FROM aliases WHERE entity_id=? ORDER BY alias", (eid,))]

    def delete_entity(self, eid: str):
        with self._lock:
            for t, col in (("aliases", "entity_id"), ("facts", "entity_id"), ("sections", "entity_id"), ("stories", "entity_id"),
                           ("search", "entity_id")):
                self.x(f"DELETE FROM {t} WHERE {col}=?", (eid,))
            self.x("DELETE FROM edges WHERE src=? OR dst=?", (eid, eid))
            self.x("UPDATE notes SET entity_id=NULL WHERE entity_id=?", (eid,))
            self.x("DELETE FROM entities WHERE id=?", (eid,))

    # ---- Suche ---------------------------------------------------------------------
    def index(self, eid: str):
        e = self.entity(eid)
        if not e:
            return
        body = " ".join([e.get("summary") or "", e.get("otype_label") or "",
                         *[s["title"] + " " + s["body"] for s in self.sections(eid)]])
        with self._lock:
            self.x("DELETE FROM search WHERE entity_id=?", (eid,))
            self.x("INSERT INTO search(entity_id, name, aliases, body) VALUES(?,?,?,?)",
                   (eid, e["name"], " ".join(self.aliases(eid)), body))

    def search(self, text: str, limit: int = 20) -> list[dict]:
        text = (text or "").strip()
        if not text:
            return []
        out, seen = [], set()
        hit = self.resolve(text)                          # exakte Kennung zuerst
        if hit:
            out.append(hit)
            seen.add(hit["id"])
        terms = [t for t in re.findall(r"\w+", text) if t]
        if terms:
            fts = " ".join(f'"{t}"*' for t in terms)
            try:
                rows = self.q("SELECT entity_id, bm25(search, 0, 10, 6, 1) AS r FROM search WHERE search MATCH ? ORDER BY r LIMIT ?",
                              (fts, limit))
            except sqlite3.OperationalError:
                rows = []
            for r in rows:
                if r["entity_id"] not in seen:
                    e = self.entity(r["entity_id"])
                    if e:
                        out.append(e)
                        seen.add(e["id"])
        return out[:limit]

    # ---- Quellen & Fakten ------------------------------------------------------------
    def add_source(self, key: str, kind: str, title: str, **vals) -> int:
        """Quelle anlegen oder auffrischen (gleicher Schlüssel = gleiche Quelle); liefert die ID."""
        with self._lock:
            cur = self.one("SELECT id FROM sources WHERE key=?", (key,))
            cols = {"kind": kind, "title": title[:500], "retrieved": vals.pop("retrieved", time.time()),
                    **{k: self._j(v) for k, v in vals.items() if v is not None}}
            if cur:
                self.x(f"UPDATE sources SET {', '.join(f'{k}=?' for k in cols)} WHERE id=?", (*cols.values(), cur["id"]))
                return cur["id"]
            return self.x(f"INSERT INTO sources(key, {', '.join(cols)}) VALUES(?{', ?' * len(cols)})", (key, *cols.values()))

    def source(self, sid: int) -> dict | None:
        return self.one("SELECT * FROM sources WHERE id=?", (sid,))

    def sources(self, ids) -> list[dict]:
        ids = [int(i) for i in ids if i]
        if not ids:
            return []
        return self.q(f"SELECT * FROM sources WHERE id IN ({','.join('?' * len(ids))})", ids)

    def set_fact(self, eid: str, key: str, value, num: float | None = None, unit: str = "", err: float | None = None,
                 source_id: int | None = None, origin: str = "catalog", status: str = "current", note: str = "") -> int:
        """Ein Wert je Schlüssel und Quelle; ein neuer aktueller Katalogwert verdrängt ältere Katalogwerte (→ alternative)."""
        with self._lock:
            self.x("DELETE FROM facts WHERE entity_id=? AND key=? AND origin=? AND IFNULL(source_id, 0)=IFNULL(?, 0)",
                   (eid, key, origin, source_id))
            if status == "current" and origin in ("catalog", "derived"):
                self.x("UPDATE facts SET status='alternative' WHERE entity_id=? AND key=? AND status='current' AND origin IN ('catalog','derived')",
                       (eid, key))
            return self.x("INSERT INTO facts(entity_id, key, value, num, unit, err, source_id, origin, status, note, created) "
                          "VALUES(?,?,?,?,?,?,?,?,?,?,?)", (eid, key, str(value), num, unit, err, source_id, origin, status, note, time.time()))

    def facts(self, eid: str) -> list[dict]:
        return self.q("SELECT f.*, s.title AS source_title, s.kind AS source_kind, s.url AS source_url FROM facts f "
                      "LEFT JOIN sources s ON s.id=f.source_id WHERE f.entity_id=? ORDER BY f.key, f.status='current' DESC, f.id", (eid,))

    def fact(self, eid: str, key: str) -> dict | None:
        return self.one("SELECT * FROM facts WHERE entity_id=? AND key=? AND status IN ('current','conflict') "
                        "ORDER BY status='current' DESC, origin='user' DESC, id DESC LIMIT 1", (eid, key))

    # ---- Texte (versioniert) ------------------------------------------------------------
    def put_section(self, eid: str, key: str, title: str, body: str, sources=(), flags=(), origin: str = "ai",
                    status: str = "draft", model: str = "", run_id: int | None = None) -> int:
        """Neue Version eines Abschnitts; die bisherige bleibt als `superseded` erhalten (Herkunft nachvollziehbar)."""
        with self._lock:
            old = self.one("SELECT id, version FROM sections WHERE entity_id=? AND key=? AND status!='superseded' ORDER BY id DESC LIMIT 1",
                           (eid, key))
            if old:
                self.x("UPDATE sections SET status='superseded', updated=? WHERE id=?", (time.time(), old["id"]))
            sid = self.x("INSERT INTO sections(entity_id, key, title, body, sources, flags, origin, status, model, run_id, version, created, updated) "
                         "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                         (eid, key, title, body, self._j(list(sources)), self._j(list(flags)), origin, status, model, run_id,
                          (old["version"] + 1) if old else 1, time.time(), time.time()))
        self.index(eid)
        return sid

    def sections(self, eid: str, history: bool = False) -> list[dict]:
        return self.q("SELECT * FROM sections WHERE entity_id=?" + ("" if history else " AND status!='superseded'") + " ORDER BY id",
                      (eid,))

    # ---- Beziehungen --------------------------------------------------------------------
    def add_edge(self, src: str, dst: str, rel: str, origin: str = "catalog", status: str = "verified",
                 source_id: int | None = None, note: str = "") -> int | None:
        if not src or not dst or src == dst:
            return None
        with self._lock:
            cur = self.one("SELECT id, status FROM edges WHERE src=? AND dst=? AND rel=?", (src, dst, rel))
            if cur:
                if cur["status"] != "verified" and status == "verified":        # Vorschlag durch Beleg bestätigt
                    self.x("UPDATE edges SET status='verified', origin=?, source_id=? WHERE id=?", (origin, source_id, cur["id"]))
                return cur["id"]
            return self.x("INSERT INTO edges(src, dst, rel, origin, status, source_id, note, created) VALUES(?,?,?,?,?,?,?,?)",
                          (src, dst, rel, origin, status, source_id, note, time.time()))

    def edges(self, eid: str) -> list[dict]:
        return self.q("SELECT * FROM edges WHERE (src=? OR dst=?) AND status!='rejected' ORDER BY status='verified' DESC, id", (eid, eid))

    # ---- Persönliches ---------------------------------------------------------------------
    def notes(self, eid: str | None = None, kind: str | None = None) -> list[dict]:
        sql, args = "SELECT * FROM notes WHERE 1=1", []
        if eid:
            sql += " AND entity_id=?"
            args.append(eid)
        if kind:
            sql += " AND kind=?"
            args.append(kind)
        return self.q(sql + " ORDER BY COALESCE(NULLIF(date, ''), created) DESC, id DESC", args)

    # ---- Entdeckungen -----------------------------------------------------------------------
    def add_insight(self, key: str, kind: str, title: str, body: str, reason: str = "", entities=(), sources=(),
                    data: dict | None = None, origin: str = "rule") -> int | None:
        """Neue Entdeckung (gleicher Schlüssel = schon gemeldet → None)."""
        with self._lock:
            if self.one("SELECT id FROM insights WHERE key=?", (key,)):
                return None
            return self.x("INSERT INTO insights(key, kind, title, body, reason, entities, sources, data, origin, created) "
                          "VALUES(?,?,?,?,?,?,?,?,?,?)", (key, kind, title, body, reason, self._j(list(entities)),
                                                         self._j(list(sources)), self._j(data or {}), origin, time.time()))

    # ---- Kosten -------------------------------------------------------------------------------
    def log_usage(self, model: str, tokens_in: int, tokens_out: int, cost: float, estimated: bool, run_id=None, purpose=""):
        self.x("INSERT INTO usage(ts, model, tokens_in, tokens_out, cost, estimated, run_id, purpose) VALUES(?,?,?,?,?,?,?,?)",
               (time.time(), model, tokens_in, tokens_out, cost, int(estimated), run_id, purpose))

    def month_cost(self) -> float:
        r = self.one("SELECT COALESCE(SUM(cost), 0) AS c FROM usage WHERE ts>=?", (month_start(),))
        return float(r["c"])

    def usage_summary(self) -> dict:
        m = month_start()
        r = self.one("SELECT COUNT(*) AS n, COALESCE(SUM(cost),0) AS c, COALESCE(SUM(tokens_in+tokens_out),0) AS t, "
                     "COALESCE(MAX(estimated),0) AS est FROM usage WHERE ts>=?", (m,))
        by = self.q("SELECT purpose, COUNT(*) AS n, SUM(cost) AS cost FROM usage WHERE ts>=? GROUP BY purpose ORDER BY cost DESC", (m,))
        return {"calls": r["n"], "cost": round(r["c"], 4), "tokens": r["t"], "estimated": bool(r["est"]), "by_purpose": by}

    # ---- Läufe ---------------------------------------------------------------------------------
    def start_run(self, kind: str, eid: str | None = None, model: str = "") -> int:
        return self.x("INSERT INTO runs(entity_id, kind, state, started, model) VALUES(?,?,?,?,?)", (eid, kind, "running", time.time(), model))

    def finish_run(self, rid: int, state: str, steps: list, cost: float = 0, tokens: int = 0, error: str | None = None):
        self.x("UPDATE runs SET state=?, finished=?, steps=?, cost=?, tokens=?, error=? WHERE id=?",
               (state, time.time(), self._j(steps), cost, tokens, error, rid))
