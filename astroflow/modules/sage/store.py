"""Verlauf und Entdeckungen (portiert aus starlight-sage lib/history.js + lib/discoveries.js, Einzelnutzer)."""
from __future__ import annotations

import json
import re
import secrets
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

ACHIEVEMENTS = [
    ("first_star", "Erste Entdeckung", "Dein erstes Himmelsobjekt identifiziert", "star", lambda s: s["total_objects"] >= 1),
    ("explorer_5", "Himmels-Forscher", "5 verschiedene Objekte entdeckt", "search", lambda s: s["total_objects"] >= 5),
    ("explorer_10", "Sternenkundler", "10 verschiedene Objekte entdeckt", "orbit", lambda s: s["total_objects"] >= 10),
    ("explorer_25", "Meister-Beobachter", "25 verschiedene Objekte entdeckt", "medal", lambda s: s["total_objects"] >= 25),
    ("messier_first", "Messier-Jäger", "Erstes Messier-Objekt entdeckt", "target", lambda s: s["messier"] >= 1),
    ("messier_5", "Messier-Sammler", "5 Messier-Objekte entdeckt", "grid", lambda s: s["messier"] >= 5),
    ("ngc_first", "NGC-Entdecker", "Erstes NGC-Objekt identifiziert", "globe", lambda s: s["ngc"] >= 1),
    ("return_visitor", "Wiederkehrer", "Ein Objekt in mehreren Analysen gefunden", "refresh", lambda s: s["repeat_objects"] >= 1),
    ("analyses_5", "Fleißiger Beobachter", "5 Analysen durchgeführt", "camera", lambda s: s["analyses"] >= 5),
    ("analyses_10", "Veteran", "10 Analysen durchgeführt", "medal", lambda s: s["analyses"] >= 10),
    ("busy_field", "Reichhaltiges Feld", "Mindestens 5 Objekte in einer Analyse", "sparkle", lambda s: s["max_in_one"] >= 5),
]
MILESTONES = (5, 10, 25, 50, 100)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def normalize(name: str) -> str:
    n = re.sub(r"\s+", " ", (name or "").strip().lower())
    n = re.sub(r"^messier\s+", "m ", n)
    return re.sub(r"^(m|ngc|ic)\s?(\d+).*$", r"\1 \2", n)            # „M 42 Orionnebel“ = „M42“ = „Messier 42“


def category(name: str) -> str:
    m = re.match(r"^(m|ngc|ic) \d+", normalize(name))
    return {"m": "messier", "ngc": "ngc", "ic": "ic"}[m.group(1)] if m else "other"


def story_title(story: str | None) -> str:
    m = re.search(r"^#{1,3}\s+(.+)$", story or "", re.M)
    return m.group(1).replace("**", "").strip() if m else "Astronomische Analyse"


def plain(text: str) -> str:
    """Markdown entfernen (für Vorschau und Vorlesen)."""
    t = re.sub(r"^#{1,6}\s+", "", text or "", flags=re.M)
    t = re.sub(r"\*\*(.+?)\*\*|__(.+?)__", lambda m: m.group(1) or m.group(2), t)
    t = re.sub(r"\*(.+?)\*|_(.+?)_", lambda m: m.group(1) or m.group(2), t)
    t = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", t)
    return re.sub(r"\n{3,}", "\n\n", t).strip()


class SageStore:
    def __init__(self, root: Path):
        self.root = root
        self.uploads = root / "uploads"
        self.audio = root / "audio"
        for p in (root, self.uploads, self.audio):
            p.mkdir(parents=True, exist_ok=True)
        self.file = root / "history.json"
        self.disc_file = root / "discoveries.json"
        self.cel_file = root / "celebrations.json"
        self._lock = threading.RLock()
        cel = self._load(self.cel_file, [])
        self._cel = cel if isinstance(cel, list) else []
        # Entdeckungen sofort anlegen: würden sie erst bei der nächsten Analyse aus dem Verlauf aufgebaut,
        # zählte die gerade gespeicherte Analyse schon mit und ihre Objekte gälten als „bekannt“
        if not isinstance(self._load(self.disc_file, None), dict):
            self.rebuild()

    # ---- Dateien ------------------------------------------------------------
    def _load(self, path: Path, default):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return default

    def _save(self, path: Path, data):
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)

    # ---- Verlauf --------------------------------------------------------------
    def entries(self) -> list[dict]:
        data = self._load(self.file, [])
        return data if isinstance(data, list) else []

    def summary(self, e: dict) -> dict:
        p = plain(e.get("story") or "").replace("\n", " ")
        return {"id": e["id"], "created": e["created"], "title": e.get("title") or story_title(e.get("story")),
                "image": e.get("image"), "source": e.get("source"), "objects": len(e.get("objects") or []),
                "names": [o["name"] for o in (e.get("objects") or [])][:6],
                "preview": p[:160] + ("…" if len(p) > 160 else ""), "has_story": bool(e.get("story"))}

    def get(self, hid: str) -> dict | None:
        return next((e for e in self.entries() if e["id"] == hid), None)

    def add(self, entry: dict) -> dict:
        with self._lock:
            rec = {"id": f"{int(time.time() * 1000)}-{secrets.token_hex(3)}", "created": now_iso(), **entry}
            rec.setdefault("title", story_title(rec.get("story")) if rec.get("story") else (rec.get("source", {}) or {}).get("name") or "Analyse")
            items = self.entries()
            items.insert(0, rec)
            self._save(self.file, items)
            return rec

    def update(self, hid: str, **values) -> dict | None:
        with self._lock:
            items = self.entries()
            for e in items:
                if e["id"] == hid:
                    e.update(values)
                    self._save(self.file, items)
                    return e
        return None

    def delete(self, hid: str) -> bool:
        with self._lock:
            items = self.entries()
            hit = next((e for e in items if e["id"] == hid), None)
            if not hit:
                return False
            self._save(self.file, [e for e in items if e["id"] != hid])
            for f in [hit.get("file"), hit.get("preview_file"), *[a.get("file") for a in (hit.get("audio") or {}).values()]]:
                if f and (self.root / f).is_file():
                    (self.root / f).unlink()
            self.rebuild()
            return True

    # ---- Entdeckungen ---------------------------------------------------------
    def _disc(self) -> dict:
        d = self._load(self.disc_file, None)
        return d if isinstance(d, dict) and "objects" in d else self.rebuild()

    @staticmethod
    def _stats(objects: dict, analyses: int, max_in_one: int) -> dict:
        v = list(objects.values())
        return {"total_objects": len(v), "analyses": analyses, "max_in_one": max_in_one,
                "messier": sum(o["category"] == "messier" for o in v), "ngc": sum(o["category"] == "ngc" for o in v),
                "ic": sum(o["category"] == "ic" for o in v), "repeat_objects": sum(o["seen"] > 1 for o in v)}

    @staticmethod
    def _unlock(stats: dict, have: dict) -> list[dict]:
        new = []
        for aid, title, desc, icon, check in ACHIEVEMENTS:
            if aid not in have and check(stats):
                have[aid] = {"id": aid, "title": title, "description": desc, "icon": icon, "unlocked": now_iso()}
                new.append(have[aid])
        return new

    def rebuild(self) -> dict:
        """Entdeckungen vollständig aus dem Verlauf berechnen (nach dem Löschen eines Eintrags)."""
        with self._lock:
            old = self._load(self.disc_file, {}) or {}
            objs, analyses, max_in_one = {}, 0, 0
            for e in reversed(self.entries()):
                names = list(dict.fromkeys(o["name"] for o in (e.get("objects") or []) if o.get("name")))
                analyses += 1
                max_in_one = max(max_in_one, len(names))
                for n in names:
                    k = normalize(n)
                    if k not in objs:
                        objs[k] = {"name": n, "category": category(n), "kind_label": "", "first_seen": e["created"],
                                   "first_entry": e["id"], "entries": [], "seen": 0, "image": e.get("image")}
                    o = objs[k]
                    o["entries"].append(e["id"])
                    o["seen"] = len(o["entries"])
                    kl = next((x.get("kind_label") for x in e["objects"] if x.get("name") == n and x.get("kind_label")), "")
                    o["kind_label"] = o["kind_label"] or kl
            ach = {k: v for k, v in (old.get("achievements") or {}).items()}
            stats = self._stats(objs, analyses, max_in_one)
            self._unlock(stats, ach)
            d = {"objects": objs, "achievements": ach, "stats": stats}
            self._save(self.disc_file, d)
            return d

    def record(self, entry: dict) -> dict:
        """Neue Analyse eintragen; liefert neue Objekte und frisch freigeschaltete Erfolge."""
        with self._lock:
            d = self._disc()
            names = list(dict.fromkeys(o["name"] for o in (entry.get("objects") or []) if o.get("name")))
            new, known = [], []
            for n in names:
                k = normalize(n)
                o = d["objects"].get(k)
                if not o:
                    kl = next((x.get("kind_label", "") for x in entry["objects"] if x.get("name") == n), "")
                    d["objects"][k] = {"name": n, "category": category(n), "kind_label": kl, "first_seen": entry["created"],
                                       "first_entry": entry["id"], "entries": [entry["id"]], "seen": 1, "image": entry.get("image")}
                    new.append(n)
                else:
                    if entry["id"] not in o["entries"]:
                        o["entries"].append(entry["id"])
                    o["seen"] = len(o["entries"])
                    known.append(n)
            st = d["stats"]
            d["stats"] = self._stats(d["objects"], st.get("analyses", 0) + 1, max(st.get("max_in_one", 0), len(names)))
            unlocked = self._unlock(d["stats"], d["achievements"])
            self._save(self.disc_file, d)
            return {"new": new, "known": known, "achievements": unlocked}

    # ---- Feiern: neue Objekte/Erfolge, bis der Nutzer sie gesehen hat ------------
    def celebrate(self, entry: dict, disc: dict) -> dict | None:
        if not disc.get("new") and not disc.get("achievements"):
            return None
        c = {"id": entry["id"], "entry": entry["id"], "title": entry.get("title") or "Analyse", "image": entry.get("preview_file"),
             "new": disc.get("new") or [], "achievements": disc.get("achievements") or [], "auto": bool((entry.get("source") or {}).get("auto")),
             "created": now_iso(), "seen": False}
        with self._lock:
            self._cel = [x for x in self._cel if x["id"] != c["id"]][-29:] + [c]
            self._save(self.cel_file, self._cel)
        return c

    def celebrations(self) -> list[dict]:
        """Noch nicht gesehene Feiern (klein – geht mit jedem Status-Polling raus)."""
        return [c for c in self._cel if not c["seen"]][-5:]

    def ack(self, ids: list[str]):
        with self._lock:
            for c in self._cel:
                if c["id"] in ids:
                    c["seen"] = True
            self._save(self.cel_file, self._cel)

    def overview(self) -> dict:
        d = self._disc()
        s = d["stats"]
        nxt = next((m for m in MILESTONES if m > s["total_objects"]), None)
        prev = max([m for m in MILESTONES if m <= s["total_objects"]] or [0])
        progress = round(100 * (s["total_objects"] - prev) / max(1, (nxt or prev) - prev)) if nxt else 100
        objs = sorted(d["objects"].values(), key=lambda o: o["first_seen"], reverse=True)
        ach = [{"id": a, "title": t, "description": de, "icon": i, "unlocked": (d["achievements"].get(a) or {}).get("unlocked")}
               for a, t, de, i, _ in ACHIEVEMENTS]
        return {"stats": s, "objects": objs, "achievements": ach, "next_milestone": nxt, "milestone_progress": progress}
