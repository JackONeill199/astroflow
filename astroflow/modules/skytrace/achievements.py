"""Erfolge für SkyTrace – ernste Meilensteine und der eine oder andere Seufzer über den vollen Himmel.

Datenbasis ist ausschließlich die Statistik (`stats.collect`: je Beobachtung die neueste vollständige Analyse) und
darin die gespeicherten Katalogtreffer der Kandidaten. Nichts wird erfunden: ein Satellit zählt nur, wenn eine Bahn
in Lage, Richtung und Zeit zur Spur passte (Kataloghinweis, keine bestätigte Identität).

Rückwirkend datiert wird nach dem Lauf, der das Ziel erstmals erreicht hat (Läufe in Reihenfolge ihrer Startzeit).
Einmal freigeschaltete Erfolge bleiben (Datei `erfolge.json` im SkyTrace-Datenordner), auch wenn ein Lauf später gelöscht wird.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import threading
from pathlib import Path

# (id, Titel, Beschreibung, Symbol, Gruppe, Kennzahl, Ziel, Spruch nach der Freischaltung) – IDs bestehender Erfolge nie ändern
ACHIEVEMENTS = [
    # --- Satelliten
    ("sat_1", "Erster Treffer", "Eine Spur einer bekannten Satellitenbahn zugeordnet", "orbit", "Satelliten", "sats", 1, ""),
    ("sat_10", "Satellitenspotter", "10 Spuren Satelliten zugeordnet", "orbit", "Satelliten", "sats", 10, ""),
    ("sat_50", "Orbit-Beobachter", "50 Spuren Satelliten zugeordnet", "search", "Satelliten", "sats", 50, ""),
    ("sat_100", "Hundert am Himmel", "100 Spuren Satelliten zugeordnet", "medal", "Satelliten", "sats", 100,
     "Der Himmel war früher mal dunkler."),
    ("sat_500", "Halbes Tausend", "500 Spuren Satelliten zugeordnet", "medal", "Satelliten", "sats", 500,
     "Irgendwo dazwischen waren auch Sterne."),
    ("sat_1000", "Tausendsassa", "1 000 Spuren Satelliten zugeordnet", "sparkle", "Satelliten", "sats", 1000,
     "Die Frage ist nicht mehr, ob ein Satellit im Bild ist, sondern welcher."),
    ("objects_10", "Zehn Gesichter", "10 verschiedene Satelliten und Objekte erwischt", "grid", "Satelliten", "objects", 10, ""),
    ("objects_50", "Katalog-Kenner", "50 verschiedene Bahnen im Bild", "grid", "Satelliten", "objects", 50, ""),
    ("objects_100", "Orbit-Atlas", "100 verschiedene Bahnen im Bild", "globe", "Satelliten", "objects", 100, ""),
    ("families_3", "Vielfalt im Orbit", "Spuren von 3 verschiedenen Satellitenfamilien (z. B. STARLINK, IRIDIUM)", "star", "Satelliten", "families", 3, ""),
    ("families_6", "Konstellations-Kenner", "Spuren von 6 verschiedenen Satellitenfamilien", "star", "Satelliten", "families", 6, ""),
    ("families_10", "Wer fliegt da eigentlich alles?", "Spuren von 10 verschiedenen Satellitenfamilien", "globe", "Satelliten", "families", 10,
     "Zehn Familien, und alle wollten aufs Foto."),
    ("repeat_2", "Wiedersehen im Orbit", "Dasselbe Objekt in 2 verschiedenen Nächten erwischt", "refresh", "Satelliten", "repeat", 2,
     "Man trifft sich immer zweimal."),
    ("repeat_4", "Stammgast", "Dasselbe Objekt in 4 verschiedenen Nächten erwischt", "refresh", "Satelliten", "repeat", 4, ""),
    ("iss_1", "Besuch von der ISS", "Die Internationale Raumstation im Bild", "target", "Satelliten", "iss", 1, "Winken nicht vergessen."),
    ("css_1", "Tiangong im Bild", "Die chinesische Raumstation im Bild", "target", "Satelliten", "css", 1, ""),
    ("hst_1", "Hubble über Hubble", "Das Weltraumteleskop Hubble im Bild", "target", "Satelliten", "hst", 1,
     "Ein Teleskop fotografiert ein Teleskop. Das Universum schmunzelt."),
    # --- Starlink-Ära
    ("starlink_1", "Hallo, Starlink", "Eine Spur dem Namen STARLINK zugeordnet", "sparkle", "Starlink-Ära", "starlink", 1,
     "Willkommen im Club. Austreten ist nicht vorgesehen."),
    ("starlink_10", "Zehn Mal Starlink", "10 Spuren STARLINK-Satelliten zugeordnet", "sparkle", "Starlink-Ära", "starlink", 10,
     "Noch kein Grund zur Sorge."),
    ("starlink_100", "Die Konstellation grüßt", "100 Spuren STARLINK-Satelliten zugeordnet", "sparkle", "Starlink-Ära", "starlink", 100,
     "Sie grüßt freundlich – am liebsten mitten ins Bild."),
    ("starlink_500", "Dauergast", "500 Spuren STARLINK-Satelliten zugeordnet", "medal", "Starlink-Ära", "starlink", 500,
     "Bald brauchen wir eine Sternkarte mit Starlink als Sternbild."),
    ("starlink_run_3", "Dreierpack", "3 Starlink-Treffer in einer Beobachtung", "grid", "Starlink-Ära", "starlink_run", 3, "Das war erst das Vorspiel."),
    ("starlink_run_6", "Fast mehr Starlink als Sterne", "6 Starlink-Treffer in einer Beobachtung", "grid", "Starlink-Ära", "starlink_run", 6,
     "Gefühlt jedenfalls."),
    ("starlink_run_10", "Mehr Starlink als Sterne", "10 Starlink-Treffer in einer Beobachtung", "grid", "Starlink-Ära", "starlink_run", 10,
     "Sternbild Starlink – im Atlas leider noch nicht eingetragen."),
    ("burst_3", "Zug verpasst? Nein, mittendrin", "3 Starlinks innerhalb von 5 Minuten im selben Bild", "pulse", "Starlink-Ära", "burst", 3,
     "Perlenkette im Anflug."),
    ("burst_5", "Perlenkette", "5 Starlinks innerhalb von 5 Minuten im selben Bild", "pulse", "Starlink-Ära", "burst", 5,
     "Der nächste kommt bestimmt – wie die S-Bahn."),
    # --- Schrott im Orbit
    ("rb_1", "Raketenstufe erwischt", "Die Spur einer ausgebrannten Raketenstufe (R/B) zugeordnet", "target", "Weltraumschrott", "rb", 1,
     "Auch Müll fliegt im Kreis."),
    ("deb_1", "Trümmerteil im Visier", "Die Spur eines Trümmerteils (DEB) zugeordnet", "search", "Weltraumschrott", "deb", 1, "Ein Splitter mit Aussicht."),
    ("scrap_10", "Weltraumschrott-Sammler", "10 Raketenstufen und Trümmerteile zugeordnet", "grid", "Weltraumschrott", "scrap", 10,
     "Sammeln ohne Aufbewahrungsproblem."),
    ("scrap_50", "Schrottplatz mit Aussicht", "50 Raketenstufen und Trümmerteile zugeordnet", "medal", "Weltraumschrott", "scrap", 50,
     "Die Müllabfuhr kommt hier leider nicht vorbei."),
    # --- Meteore & Flugzeuge
    ("plane_1", "Erstes Flugzeug", "Eine Spur als Flugzeug eingeordnet", "pin", "Meteore & Flugzeuge", "planes", 1, ""),
    ("plane_5", "Fluglotse", "5 Flugzeugspuren", "pin", "Meteore & Flugzeuge", "planes", 5, "Landeanflug auf den Sternenhimmel."),
    ("meteor_1", "Endlich eine Sternschnuppe?", "Eine Spur als Meteorkandidat eingeordnet", "star", "Meteore & Flugzeuge", "meteors", 1,
     "Wünsch dir was – aber erst nach der Prüfung."),
    ("meteor_cat_1", "Meteor mit Stempel", "Ein Meteorkandidat passt zu einer gemessenen Meteorbahn im Katalog", "sparkle", "Meteore & Flugzeuge",
     "meteor_cat", 1, "Selten genug, um ihn einzurahmen."),
    ("meteor_plane_1", "Sternschnuppe? Nein, Flugzeug", "Ein Meteorvorschlag stellte sich als Flugzeug heraus", "pin", "Meteore & Flugzeuge",
     "meteor_plane", 1, "Wünsch dir trotzdem was."),
    ("meteor_sat_1", "Sternschnuppe? Nein, Satellit", "Ein Meteorvorschlag stellte sich als Satellit heraus", "orbit", "Meteore & Flugzeuge",
     "meteor_sat", 1, "Die Wunschliste bleibt im Orbit."),
    ("meteor_sat_5", "Wunschkonzert", "5 Meteorvorschläge stellten sich als Flugzeug oder Satellit heraus", "orbit", "Meteore & Flugzeuge",
     "meteor_other", 5, "Fünf Wünsche, null Erfüllung."),
    # --- Beobachtung
    ("runs_1", "Erste Beobachtung", "Eine Bildfolge analysiert", "camera", "Beobachtung", "runs", 1, ""),
    ("runs_10", "Fleißiger Beobachter", "10 Beobachtungen analysiert", "camera", "Beobachtung", "runs", 10, ""),
    ("runs_50", "Veteran", "50 Beobachtungen analysiert", "medal", "Beobachtung", "runs", 50, ""),
    ("runs_100", "Hundert Beobachtungen", "100 Beobachtungen analysiert", "medal", "Beobachtung", "runs", 100,
     "Der Rechner kennt deine Nächte besser als dein Kalender."),
    ("nights_10", "Zehn Nächte", "In 10 verschiedenen Nächten fotografiert", "camera", "Beobachtung", "nights", 10, ""),
    ("nights_30", "Dauerbeobachter", "In 30 verschiedenen Nächten fotografiert", "camera", "Beobachtung", "nights", 30, ""),
    ("nights_100", "Nachtmensch", "In 100 verschiedenen Nächten fotografiert", "star", "Beobachtung", "nights", 100, "Schlaf wird überbewertet."),
    ("frames_1k", "Tausend Aufnahmen", "1 000 Aufnahmen analysiert", "grid", "Beobachtung", "frames", 1000, ""),
    ("frames_10k", "Zehntausend Aufnahmen", "10 000 Aufnahmen analysiert", "medal", "Beobachtung", "frames", 10000,
     "Zehntausend Mal geschaut, ob da was fliegt."),
    ("frames_50k", "Fünfzigtausend Aufnahmen", "50 000 Aufnahmen analysiert", "sparkle", "Beobachtung", "frames", 50000, ""),
    ("cand_100", "Spurensucher", "100 Spuren in den Analysen gefunden", "search", "Beobachtung", "cands", 100, ""),
    ("cand_500", "Spurenleser", "500 Spuren in den Analysen gefunden", "search", "Beobachtung", "cands", 500, ""),
    ("excl_100", "Wackelpudding-Filter", "100 unscharfe Aufnahmen aussortiert", "pulse", "Beobachtung", "excluded", 100,
     "Wind, Wolken, Wackler – SkyTrace sieht sie alle."),
    ("excl_1000", "Qualitätskontrolle", "1 000 unscharfe Aufnahmen aussortiert", "pulse", "Beobachtung", "excluded", 1000,
     "Tapfer weggeklickt, ohne dass du es merken musstest."),
    # --- Kurioses
    ("traffic_5", "Reger Betrieb", "5 Spuren in einer Beobachtung", "grid", "Kurioses", "traffic", 5, ""),
    ("traffic_10", "Kosmischer Verkehrsstau", "10 Spuren in einer Beobachtung", "grid", "Kurioses", "traffic", 10, "Bitte Reißverschlussverfahren im Orbit."),
    ("traffic_20", "Rushhour im Orbit", "20 Spuren in einer Beobachtung", "sparkle", "Kurioses", "traffic", 20, "Blitzer fehlen noch."),
    ("clean_1", "Saubere Nacht", "Eine Beobachtung mit mindestens 100 Aufnahmen ohne eine einzige Spur", "star", "Kurioses", "clean", 1,
     "Selten und kostbar. Oder die Wolken haben geholfen."),
    ("clean_5", "Reinheitsgebot", "5 saubere Nächte mit mindestens 100 Aufnahmen", "star", "Kurioses", "clean", 5, ""),
    ("clean_25", "Dark-Sky-Glück", "25 saubere Nächte mit mindestens 100 Aufnahmen", "sparkle", "Kurioses", "clean", 25,
     "Fünfundzwanzig Mal Himmel pur. Genieße es, solange es dauert."),
    ("late_1", "Nachtschicht", "Eine Spur nach 3 Uhr morgens (Ortszeit dieses Rechners)", "star", "Kurioses", "late", 1,
     "Um diese Zeit schlafen nur Menschen und Hobbyastronomen nicht."),
    ("late_10", "Nachteule", "10 Spuren zwischen 3 und 6 Uhr morgens (Ortszeit dieses Rechners)", "star", "Kurioses", "late", 10,
     "Der Kaffee ist alle, die Satelliten nicht."),
    ("variety_1", "Vollprogramm", "Satellit, Flugzeug und Meteorkandidat in derselben Beobachtung", "medal", "Kurioses", "variety", 1,
     "Fehlt nur noch ein UFO – das bleibt aber ein Kandidat ohne Bestätigung."),
]
BY_ID = {a[0]: a for a in ACHIEVEMENTS}
GROUPS = ["Satelliten", "Starlink-Ära", "Weltraumschrott", "Meteore & Flugzeuge", "Beobachtung", "Kurioses"]
KEYS = sorted({a[5] for a in ACHIEVEMENTS})
# Raumstationen: NORAD-Nummern (Namen im SATCAT: ISS (ZARYA), CSS (TIANHE) …)
ISS_IDS, CSS_IDS, HST_IDS = {25544}, {48274, 53239, 54216}, {20580}
BURST_S = 300            # Starlink-„Zug“: Treffer innerhalb von 5 Minuten
LATE_HOURS = (3, 4, 5)   # Ortszeit des Rechners
CLEAN_MIN_FRAMES = 100


def classify(c: dict) -> tuple[str, dict | None, str]:
    """Einordnung wie `evidence.js classification` (Kataloghinweis → Verkettung → Blinken → Modell).
    Rückgabe (Klasse, Satellitentreffer oder None, ursprünglicher Meteorvorschlag 'meteor'/'')."""
    cats = c.get("catalogs") or []
    hints = sorted((h for h in c.get("model_hints") or [] if h.get("kind") in ("meteor", "plane", "satellite", "moving", "trail")
                    and isinstance(h.get("confidence"), (int, float))), key=lambda h: -h["confidence"])
    proposal = "meteor" if c.get("kind") == "meteor" else ""             # Erkennungsvorschlag des Detektors (kein Modellhinweis)
    sat = next((r for r in cats if r.get("provider") == "satellites" and r.get("status") == "compatible" and r.get("matches")), None)
    if sat:
        return "satellite", sat["matches"][0], proposal
    kinds = {{"satellites": "satellite", "aircraft": "plane", "meteors": "meteor", "fireballs": "meteor"}.get(r.get("provider"))
             for r in cats if r.get("status") == "compatible"} - {None}
    if len(kinds) == 1:
        return kinds.pop(), None, proposal
    if ((c.get("linked_track") or {}).get("count") or 0) > 1 or c.get("blink"):
        return "plane", None, proposal
    if hints and c.get("model_scope") != "narrow_field":
        return hints[0]["kind"], None, proposal
    return c.get("kind") or "trail", None, proposal


def _family(name: str) -> str:
    return re.split(r"[\s\-_]", name.strip().upper(), maxsplit=1)[0] if name else ""


def _object_kind(m: dict) -> str:
    """'sat' | 'rb' | 'deb' – aus der SATCAT-Objektart, sonst aus dem Namen (… R/B, … DEB)."""
    t, n = (m.get("object_type") or "").lower(), (m.get("name") or "").upper()
    if t.startswith("raketen") or n.endswith("R/B"):
        return "rb"
    if (t.startswith("tr") and "mmer" in t) or n.endswith(" DEB") or " DEB " in n + " ":
        return "deb"
    return "sat"


def _burst(times: list[float]) -> int:
    times = sorted(times)
    best, lo = 0, 0
    for hi, t in enumerate(times):
        while t - times[lo] > BURST_S:
            lo += 1
        best = max(best, hi - lo + 1)
    return best


def _run_date(run: str) -> str | None:
    m = re.match(r"^(\d{4}-\d\d-\d\d)_(\d\d)-(\d\d)-(\d\d)", run or "")
    return f"{m[1]}T{m[2]}:{m[3]}:{m[4]}" if m else None


def compute(data: dict) -> dict:
    """Kennzahlen und Freischaltzeitpunkte aus `stats.collect`. Rückgabe {metrics, when: {id: ISO-Datum}}.
    Beobachtungen werden in Reihenfolge ihrer Analyse (Lauf-ID = Startzeit) durchlaufen; der Erfolg bekommt das Datum des Laufs,
    in dem seine Kennzahl das Ziel erstmals erreicht."""
    by_session: dict[tuple, list] = {}
    for c in data.get("candidates") or []:
        by_session.setdefault((c.get("run"), c.get("session")), []).append(c)
    sessions = sorted(data.get("sessions") or [], key=lambda s: (s.get("run") or "", s.get("session") or ""))
    cur = dict.fromkeys(KEYS, 0)
    objects: set = set()
    families: set = set()
    nights: set = set()
    object_nights: dict = {}
    pending = list(ACHIEVEMENTS)
    when: dict[str, str] = {}
    for s in sessions:
        cands = by_session.get((s.get("run"), s.get("session")), [])
        cur["runs"] += 1
        cur["frames"] += s.get("frames") or 0
        cur["excluded"] += s.get("excluded") or 0
        cur["cands"] += len(cands)
        if s.get("night"):
            nights.add(s["night"])
        cur["nights"] = len(nights)
        starlinks, stars, classes = [], 0, set()
        for c in cands:
            kind, m, proposal = classify(c)
            classes.add(kind)
            if kind == "satellite" and m:
                name, norad = m.get("name") or "", m.get("norad_id")
                what = _object_kind(m)
                cur["sats"] += 1
                key = norad if norad is not None else name
                if key:
                    objects.add(key)
                    if s.get("night"):
                        object_nights.setdefault(key, set()).add(s["night"])
                cur["objects"] = len(objects)
                cur["repeat"] = max([cur["repeat"], *(len(v) for v in object_nights.values())])
                if what == "sat" and _family(name):
                    families.add(_family(name))
                cur["families"] = len(families)
                if what != "sat":
                    cur["rb" if what == "rb" else "deb"] += 1
                    cur["scrap"] += 1
                if name.upper().startswith("STARLINK"):
                    cur["starlink"] += 1
                    stars += 1
                    if isinstance(c.get("timestamp"), (int, float)):
                        starlinks.append(c["timestamp"])
                if norad in ISS_IDS or name.upper().startswith("ISS"):
                    cur["iss"] = 1
                if norad in CSS_IDS or name.upper().startswith("CSS ("):
                    cur["css"] = 1
                if norad in HST_IDS or name.upper() == "HST":
                    cur["hst"] = 1
                if proposal:
                    cur["meteor_sat"] += 1
                    cur["meteor_other"] += 1
            elif kind == "plane":
                cur["planes"] += 1
                if proposal:
                    cur["meteor_plane"] += 1
                    cur["meteor_other"] += 1
            elif kind == "meteor":
                cur["meteors"] += 1
                if any(r.get("provider") in ("meteors", "fireballs") and r.get("status") == "compatible" for r in c.get("catalogs") or []):
                    cur["meteor_cat"] += 1
            ts = c.get("timestamp")
            if isinstance(ts, (int, float)):
                try:
                    if dt.datetime.fromtimestamp(ts).hour in LATE_HOURS:
                        cur["late"] += 1
                except (OverflowError, OSError, ValueError):
                    pass
        cur["starlink_run"] = max(cur["starlink_run"], stars)
        cur["burst"] = max(cur["burst"], _burst(starlinks))
        cur["traffic"] = max(cur["traffic"], len(cands))
        if not cands and (s.get("frames") or 0) >= CLEAN_MIN_FRAMES:
            cur["clean"] += 1
        if {"satellite", "plane", "meteor"} <= classes:
            cur["variety"] += 1
        stamp = _run_date(s.get("run"))
        for a in [a for a in pending if cur[a[5]] >= a[6]]:
            when[a[0]] = stamp or ""
            pending.remove(a)
    return {"metrics": cur, "when": when}


def now_iso() -> str:
    return dt.datetime.now().replace(microsecond=0).isoformat()


class Achievements:
    """Gespeicherter Zustand (`erfolge.json`) plus Auswertung. Der Server ruft `refresh` beim Öffnen der Seite und wenn ein Lauf endet."""

    def __init__(self, path: Path, collect):
        self.path, self.collect = Path(path), collect          # collect() → Statistik-Daten
        self.lock = threading.RLock()
        self._new: list[dict] = []                             # ungesehene Erfolge für Hinweis und Animation

    def _load(self) -> dict | None:
        try:
            d = json.loads(self.path.read_text(encoding="utf-8"))
            return d if isinstance(d, dict) and isinstance(d.get("unlocked"), dict) else None
        except (OSError, ValueError):
            return None

    def _save(self, d: dict) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, self.path)

    def refresh(self) -> dict:
        """Bestand prüfen, neue Erfolge speichern. Erster Aufruf (noch keine Datei): alles rückwirkend und schon „gesehen“."""
        with self.lock:
            saved = self._load()
            res = compute(self.collect())
            first = saved is None
            d = saved or {"v": 1, "unlocked": {}}
            changed = first
            for aid, stamp in res["when"].items():
                if aid not in d["unlocked"]:
                    d["unlocked"][aid] = {"unlocked": stamp or now_iso(), "seen": first}
                    changed = True
            if changed:
                try:
                    self._save(d)
                except OSError:
                    pass
            self._new = [{"id": i, "title": BY_ID[i][1], "icon": BY_ID[i][3]} for i, v in d["unlocked"].items()
                         if i in BY_ID and not v.get("seen")]
            return {"state": d, "metrics": res["metrics"]}

    def new(self) -> list[dict]:
        return list(self._new)

    def mark_seen(self) -> None:
        with self.lock:
            d = self._load()
            if d and any(not v.get("seen") for v in d["unlocked"].values()):
                for v in d["unlocked"].values():
                    v["seen"] = True
                try:
                    self._save(d)
                except OSError:
                    pass
            self._new = []

    def overview(self) -> dict:
        r = self.refresh()
        have, m = r["state"]["unlocked"], r["metrics"]
        items = []
        for aid, title, desc, icon, group, key, goal, joke in ACHIEVEMENTS:
            h = have.get(aid)
            items.append({"id": aid, "title": title, "description": desc, "icon": icon, "group": group, "joke": joke if h else "",
                          "unlocked": h["unlocked"] if h else None, "new": bool(h and not h.get("seen")),
                          "progress": {"cur": goal if h else min(int(m.get(key) or 0), goal), "goal": goal}})
        got = sum(1 for i in items if i["unlocked"])
        return {"achievements": items, "groups": GROUPS, "unlocked": got, "total": len(items), "metrics": m,
                "new": [i["id"] for i in items if i["new"]]}
