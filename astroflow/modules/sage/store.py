"""Verlauf und Entdeckungen (portiert aus starlight-sage lib/history.js + lib/discoveries.js, Einzelnutzer)."""
from __future__ import annotations

import json
import re
import secrets
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from . import collection

# (id, Titel, Beschreibung, Symbol, Gruppe, Kennzahl in den Statistiken, Ziel) – IDs bestehender Erfolge nie ändern
ACHIEVEMENTS = [
    ("first_star", "Erste Entdeckung", "Dein erstes Himmelsobjekt identifiziert", "star", "Sammlung", "total_objects", 1),
    ("explorer_5", "Himmels-Forscher", "5 verschiedene Objekte entdeckt", "search", "Sammlung", "total_objects", 5),
    ("explorer_10", "Sternenkundler", "10 verschiedene Objekte entdeckt", "orbit", "Sammlung", "total_objects", 10),
    ("explorer_25", "Meister-Beobachter", "25 verschiedene Objekte entdeckt", "medal", "Sammlung", "total_objects", 25),
    ("explorer_50", "Himmelsatlas", "50 verschiedene Objekte entdeckt", "grid", "Sammlung", "total_objects", 50),
    ("explorer_100", "Kartograf des Himmels", "100 verschiedene Objekte entdeckt", "globe", "Sammlung", "total_objects", 100),
    ("messier_first", "Messier-Jäger", "Erstes Messier-Objekt entdeckt", "target", "Kataloge", "messier", 1),
    ("messier_5", "Messier-Sammler", "5 Messier-Objekte entdeckt", "grid", "Kataloge", "messier", 5),
    ("messier_10", "Messier-Kenner", "10 Messier-Objekte entdeckt", "target", "Kataloge", "messier", 10),
    ("messier_50", "Messier-Meister", "50 Messier-Objekte entdeckt", "medal", "Kataloge", "messier", 50),
    ("messier_110", "Messier-Marathon", "Alle 110 Messier-Objekte entdeckt", "sparkle", "Kataloge", "messier", 110),
    ("ngc_first", "NGC-Entdecker", "Erstes NGC-Objekt identifiziert", "globe", "Kataloge", "ngc", 1),
    ("caldwell_first", "Caldwell-Entdecker", "Erstes Caldwell-Objekt entdeckt", "search", "Kataloge", "caldwell", 1),
    ("first_galaxy", "Galaxienjäger", "Erste Galaxie in der Sammlung", "orbit", "Objektarten", "galaxy", 1),
    ("galaxies_25", "Galaxienfreund", "25 Galaxien in der Sammlung", "orbit", "Objektarten", "galaxy", 25),
    ("first_planetary", "Gashülle", "Ersten Planetarischen Nebel entdeckt", "sparkle", "Objektarten", "planetary", 1),
    ("first_globular", "Sternenball", "Ersten Kugelsternhaufen entdeckt", "star", "Objektarten", "globular", 1),
    ("first_cluster", "Sternenhaufen", "Ersten offenen Sternhaufen entdeckt", "star", "Objektarten", "open_cluster", 1),
    ("first_snr", "Sternenreste", "Ersten Supernova-Überrest entdeckt", "pulse", "Objektarten", "snr", 1),
    ("kinds_5", "Allrounder", "Objekte aus 5 verschiedenen Objektarten", "grid", "Objektarten", "kinds", 5),
    ("constellations_5", "Sternbild-Wanderer", "Objekte in 5 verschiedenen Sternbildern", "pin", "Himmel", "constellations", 5),
    ("constellations_10", "Sternbild-Kenner", "Objekte in 10 verschiedenen Sternbildern", "pin", "Himmel", "constellations", 10),
    ("constellations_20", "Himmelskartograf", "Objekte in 20 verschiedenen Sternbildern", "pin", "Himmel", "constellations", 20),
    ("dist_10m", "Ferne Welten", "Ein Objekt über 10 Millionen Lichtjahre entfernt", "globe", "Himmel", "max_mly", 10),
    ("dist_100m", "Blick in die Tiefe", "Ein Objekt über 100 Millionen Lichtjahre entfernt", "globe", "Himmel", "max_mly", 100),
    ("return_visitor", "Wiederkehrer", "Ein Objekt in mehreren Analysen gefunden", "refresh", "Aktivität", "repeat_objects", 1),
    ("repeat_5", "Stammgast", "Dasselbe Objekt in 5 Analysen wiedergesehen", "refresh", "Aktivität", "max_seen", 5),
    ("analyses_5", "Fleißiger Beobachter", "5 Analysen durchgeführt", "camera", "Aktivität", "analyses", 5),
    ("analyses_10", "Veteran", "10 Analysen durchgeführt", "medal", "Aktivität", "analyses", 10),
    ("busy_field", "Reichhaltiges Feld", "Mindestens 5 Objekte in einer Analyse", "sparkle", "Aktivität", "max_in_one", 5),
    ("nights_5", "Nachtschicht", "In 5 verschiedenen Nächten fotografiert", "camera", "Beobachtung", "nights", 5),
    ("nights_10", "Dauerbeobachter", "In 10 verschiedenen Nächten fotografiert", "camera", "Beobachtung", "nights", 10),
    ("night_owl", "Nachteule", "Eine Aufnahme nach 2 Uhr nachts", "star", "Beobachtung", "night_owl", 1),
    ("seasons_4", "Rund ums Jahr", "In allen vier Jahreszeiten fotografiert", "refresh", "Beobachtung", "seasons", 4),
    ("exposure_1h", "Geduldsprobe", "1 Stunde Belichtung an einem Objekt", "pulse", "Beobachtung", "max_obj_hours", 1),
    ("exposure_10h", "Langzeitbelichter", "10 Stunden Belichtung an einem Objekt", "pulse", "Beobachtung", "max_obj_hours", 10),
    ("total_100h", "Hundert Stunden", "100 Stunden Belichtung insgesamt", "medal", "Beobachtung", "total_hours", 100),
    ("asteroid_first", "Kleinplanetenjäger", "Ersten Asteroiden in einem eigenen Bild nachgewiesen", "orbit", "Himmel", "asteroids", 1),
    ("asteroids_10", "Asteroidengürtel", "10 verschiedene Asteroiden in eigenen Bildern nachgewiesen", "orbit", "Himmel", "asteroids", 10),
]
# Kennzahlen, die sich aus den Objekten allein ergeben und deshalb rückwirkend datiert werden können
OBJECT_KEYS = {"total_objects", "messier", "ngc", "caldwell", "galaxy", "planetary", "globular", "open_cluster", "snr",
               "kinds", "constellations", "max_mly"}
MILESTONES = (5, 10, 25, 50, 100, 150, 250, 500, 1000)


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
        self.enrich = None        # (Namen) -> {Name: Katalog-/Art-/Sternbild-Angaben}, setzt das Modul (Wissensbasis)
        self.log_stats = None     # () -> Kennzahlen des Aufnahmeprotokolls (Nächte, Belichtung)
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

    def _ext(self, objs: list[dict], attrs: dict, extra: dict | None = None) -> dict:
        """Kennzahlen für Erfolge aus den Objekten (Kataloge, Arten, Sternbilder, Entfernung) plus Protokollwerte."""
        a = [attrs.get(o["name"]) or {} for o in objs]
        kinds = [x.get("kind") for x in a if x.get("kind")]
        st = {"total_objects": len(objs), "messier_list": sorted({x["m"] for x in a if x.get("m")}),
              "caldwell": len({x["c"] for x in a if x.get("c")}), "kinds": len(set(kinds)),
              "constellations": len({str(x["con"]).lower() for x in a if x.get("con")}),
              "max_mly": max([x.get("mly") or 0 for x in a], default=0), "max_seen": max([o["seen"] for o in objs], default=0),
              "ngc": sum(o["category"] == "ngc" for o in objs)}
        st["messier"] = len(st["messier_list"])
        for k in collection.KINDS:
            st[k] = kinds.count(k)
        return {**st, **{k: v for k, v in (extra or {}).items() if k != "dates"}}

    def _attrs(self, objs: list[dict]) -> dict:
        names = [o["name"] for o in objs]
        out = {n: {"m": collection.messier_number(n)} for n in names}          # Namen allein reichen für „M 31“ und „NGC 224“
        try:
            for n, v in (self.enrich(names) if self.enrich else {}).items():
                out[n] = {**v, "m": v.get("m") or out[n]["m"]}
        except Exception:
            pass
        return out

    def _full_stats(self, d: dict) -> tuple[dict, dict, dict]:
        objs = list(d["objects"].values())
        try:
            log = (self.log_stats() if self.log_stats else None) or {}
        except Exception:
            log = {}
        attrs = self._attrs(objs)
        entries = self.entries()
        extra = {**log, "stories": sum(bool(e.get("story")) for e in entries)}
        return {**d["stats"], **self._ext(objs, attrs, extra)}, attrs, log

    def _unlock(self, stats: dict, have: dict, when: dict | None = None) -> list[dict]:
        new = []
        for aid, title, desc, icon, _grp, key, goal in ACHIEVEMENTS:
            if aid not in have and (stats.get(key) or 0) >= goal:
                have[aid] = {"id": aid, "title": title, "description": desc, "icon": icon, "unlocked": (when or {}).get(aid) or now_iso()}
                new.append(have[aid])
        return new

    def _dates(self, d: dict, attrs: dict, log: dict) -> dict:
        """Plausible Zeitpunkte für rückwirkende Erfolge: wann das Ziel in der Reihenfolge der Entdeckungen erstmals erreicht war."""
        when = dict(log.get("dates") or {})
        objs = sorted(d["objects"].values(), key=lambda o: o["first_seen"])
        todo = [a for a in ACHIEVEMENTS if a[0] not in d["achievements"] and a[0] not in when and a[5] in OBJECT_KEYS]
        for i in range(1, len(objs) + 1):
            if not todo:
                break
            st = self._ext(objs[:i], attrs)
            for a in [a for a in todo if (st.get(a[5]) or 0) >= a[6]]:
                when[a[0]] = objs[i - 1]["first_seen"]
                todo.remove(a)
        return when

    def refresh(self, celebrate: bool = True) -> list[dict]:
        """Erfolge für den Bestand prüfen (neue Erfolgsarten werden rückwirkend vergeben)."""
        with self._lock:
            d = self._disc()
            stats, attrs, log = self._full_stats(d)
            new = self._unlock(stats, d["achievements"], self._dates(d, attrs, log))
            if new:
                self._save(self.disc_file, d)
                if celebrate:
                    self.celebrate({"id": f"erfolge-{int(time.time())}", "title": "Neue Erfolge"}, {"achievements": new})
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
            d = {"objects": objs, "achievements": ach, "stats": self._stats(objs, analyses, max_in_one)}
            self._unlock(self._full_stats(d)[0], ach)
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
            unlocked = self._unlock(self._full_stats(d)[0], d["achievements"])
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

    def stats(self) -> dict:
        return self._disc()["stats"]

    def overview(self) -> dict:
        with self._lock:
            self.refresh()
            d = self._disc()
            s, attrs, _ = self._full_stats(d)
        total = s["total_objects"]
        nxt = next((m for m in MILESTONES if m > total), None)
        prev = max([m for m in MILESTONES if m <= total] or [0])
        progress = round(100 * (total - prev) / max(1, (nxt or prev) - prev)) if nxt else 100
        objs = sorted(d["objects"].values(), key=lambda o: o["first_seen"], reverse=True)
        ach = [{"id": a, "title": t, "description": de, "icon": i, "group": g, "unlocked": (d["achievements"].get(a) or {}).get("unlocked"),
                "progress": {"cur": min(int(s.get(k) or 0), goal), "goal": goal}} for a, t, de, i, g, k, goal in ACHIEVEMENTS]
        first = {}
        for o in sorted(d["objects"].values(), key=lambda o: o["first_seen"]):
            m = (attrs.get(o["name"]) or {}).get("m")
            if m and m not in first:
                first[m] = o
        messier = [{"n": n, "have": n in first, "name": first[n]["name"] if n in first else f"M{n}",
                    "entry": first[n]["first_entry"] if n in first else None} for n in range(1, 111)]
        return {"stats": s, "objects": objs, "achievements": ach, "next_milestone": nxt, "milestone_progress": progress,
                "milestones": list(MILESTONES), "messier": messier}
