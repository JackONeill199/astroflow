"""Himmelsscan: Peilpunkte abfahren und entscheiden, welche Richtung frei ist (Ablauf; Bewegen macht der Autopilot über `_cmd`).

Voller Scan: Spalten alle `COL_STEP`° Azimut, je Spalte zuerst `START_ALT`; frei → eine Stufe tiefer (nur wenn das über der
Mindesthöhe der Ziele etwas bringt), verdeckt → gleich die oberste Stufe (Hauswand? sonst halbieren); jede zweite freie Spalte
einmal auf `TOP_ALT` (Dachüberstand). Danach halbieren – in der Höhe, bis frei und verdeckt höchstens `BISECT_MIN` auseinanderliegen, im Azimut einmal
zwischen Nachbarspalten mit deutlich verschiedener Grenze. Kurzscan (bekannte Aufstellungen): erst wenige Erkennungspunkte,
dann nur die Grenzen der erkannten Aufstellung; passt keine, wird daraus ein voller Scan.

Wolke oder Hindernis: Der erste freie Punkt wird Gegenprobe. Sie wird alle `REF_EVERY` Punkte und nach `STREAK` verdeckten in
Folge erneut angefahren. Ist sie frei, gelten die verdeckten Punkte seitdem als sicher; scheitert sie, werden sie zu Zweifeln
(später wiederholt), und nach `REF_FAILS` Fehlschlägen in Folge endet der Scan als „Wolken“. Zweifel (ein paar Sterne, aber
keine Lagebestimmung, oder keine Antwort) werden am Ende einmal wiederholt.
"""
from __future__ import annotations

from . import skymap

COL_STEP = 30
LEVELS = (15.0, 25.0, 40.0, 55.0)
START_ALT = 25.0
TOP_ALT = 70.0             # Überhang (Dach, Balkon darüber): jede zweite freie Spalte einmal steil prüfen
STEEP = ((180.0, 60.0), (90.0, 50.0))   # keine Sterne in mehreren Spalten: so (Azimut-Versatz, Höhe) gegenprüfen
NOFREE_COLS = 3
SCAN_FLOOR = 15.0          # feste Mindesthöhe der Peilpunkte (darunter ist am Balkon/Gerät ohnehin nichts sinnvoll)
BISECT_MIN = 8.0           # Grad: so eng wird die Grenze in der Höhe eingegrenzt
AZ_SPLIT = 8.0             # Grenzen zweier Nachbarspalten so verschieden → Spalte dazwischen prüfen
MOON_AVOID = 25.0          # Grad Abstand zum Mond
REF_EVERY = 8
STREAK = 3                 # verdeckte Punkte in so vielen Spalten ohne freien dazwischen → Gegenprobe
REF_FAILS = 2
CLOUD_WAIT_S = 180         # Gegenprobe gescheitert: so lange warten, dann noch einmal
MAX_PROBES = 48
SCAN_MAX_S = 25 * 60       # danach Karte aus den bisherigen Punkten (Simulator: voller Balkon-Scan ≈ 20 min)
SHORT_MAX_S = 8 * 60
PROBE_TIMEOUT_S = 150      # Schwenk + Lagebestimmung; danach gilt der Punkt als Zweifel
STALE_S = 20               # „Lage nicht bestimmt“ ohne gesehenen Schwenk erst nach so langer Zeit glauben (alter Fehler?)
EXPOSURE_S = 5.0
STARS_SOME = 3             # so viele Sterne ohne Lagebestimmung → Zweifel (eher dünne Wolke), darunter verdeckt
LABELS = {"raster": "Raster", "halbieren": "Grenze eingrenzen", "gegenprobe": "Gegenprobe", "wiederholung": "Wiederholung",
          "erkennen": "Aufstellung erkennen", "grenze": "Grenze prüfen"}


def _key(p: dict) -> tuple[float, float]:
    return round(p["az"] % 360, 1), round(p["alt"], 1)


class Scan:
    def __init__(self, kind: str, now: float, floor: float = SCAN_FLOOR, groups: list[dict] | None = None, need: float = 0.0):
        self.kind = kind                      # "voll" | "kurz"
        self.floor = max(SCAN_FLOOR, float(floor))
        self.need = float(need)               # Mindesthöhe der Ziele: Grenzen darunter genauer zu kennen, bringt nichts
        self.started = now
        self.state = "laeuft"                 # laeuft | fertig | abgebrochen | wolken
        self.why = ""
        self.groups = groups or []
        self.setup: dict | None = None        # erkannte Aufstellung (Kurzscan)
        self.queue: list[dict] = []
        self.done: list[dict] = []
        self.cur: dict | None = None
        self.ref: dict | None = None
        self.since_ref = 0
        self.streak: set = set()              # Spalten mit verdeckten Punkten seit dem letzten freien
        self.ref_fails = 0
        self.unconfirmed: list[dict] = []     # verdeckt seit der letzten guten Gegenprobe
        self.wait_until = 0.0
        self.az_split_done: set = set()
        self.steep = 0
        self.retried = False
        self.n = 0
        if kind == "kurz" and self.groups:
            self.queue = [dict(p, why="erkennen") for p in skymap.probes_to_tell(self.groups, self.floor)]
        if not self.queue:
            self.kind = "voll"
            self._raster()

    # ---------------------------------------------------------------- Planung --- #
    def _raster(self):
        start = next((a for a in LEVELS if a >= START_ALT and a >= self.floor), max(LEVELS))
        self.queue += [{"az": float(az), "alt": start, "why": "raster"} for az in range(0, 360, COL_STEP)]

    def _measured(self) -> list[dict]:
        return [p for p in self.done if p.get("res") in ("frei", "verdeckt", "zweifel")]

    def _have(self, az: float, alt: float) -> bool:
        return any(abs(((p["az"] - az + 540) % 360) - 180) < 1.0 and abs(p["alt"] - alt) < 1.0
                   for p in self.done + self.queue + ([self.cur] if self.cur else []))

    def _follow(self, p: dict):
        """Raster: nächste Stufe derselben Spalte gleich als Nächstes (kurzer Schwenk)."""
        if p["why"] != "raster" or p["res"] == "zweifel":
            return
        lv = [a for a in LEVELS if a >= self.floor]
        if p["res"] == "frei":
            lower = [a for a in lv if a < p["alt"] and p["alt"] > self.need]
            nxt = lower[-1] if lower and not any(q["az"] == p["az"] and q["res"] == "verdeckt" and q["alt"] < p["alt"] for q in self.done) else None
        else:
            higher = [a for a in lv if a > p["alt"]]
            nxt = higher[-1] if higher and not any(q["az"] == p["az"] and q["res"] == "frei" and q["alt"] > p["alt"]
                                                  and q["why"] != "gegenprobe" for q in self.done) else None
        if nxt is not None and not self._have(p["az"], nxt):
            self.queue.insert(0, {"az": p["az"], "alt": nxt, "why": "raster"})
        elif p["res"] == "frei" and nxt is None and p["az"] % (2 * COL_STEP) == 0 and not self._have(p["az"], TOP_ALT):
            self.queue.insert(0, {"az": p["az"], "alt": TOP_ALT, "why": "raster"})      # Dachüberstand?

    def _refine(self) -> list[dict]:
        """Halbieren: in der Höhe je Spalte, im Azimut einmal zwischen Spalten mit deutlich verschiedener Grenze."""
        out = []
        cols: dict[float, list[dict]] = {}
        for p in self.done:
            if p.get("res") in ("frei", "verdeckt") and p["why"] != "gegenprobe":
                cols.setdefault(round(p["az"] % 360, 1), []).append(p)
        lims = {}
        for az, ps in cols.items():
            free = [p["alt"] for p in ps if p["res"] == "frei"]
            hi = min(free) if free else None
            lo = max([p["alt"] for p in ps if p["res"] == "verdeckt" and (hi is None or p["alt"] < hi)], default=None)
            if lo is not None and hi is not None and hi - lo > BISECT_MIN and hi > self.need:
                mid = round((lo + hi) / 2, 1)
                if not self._have(az, mid):
                    out.append({"az": az, "alt": mid, "why": "halbieren"})
            top = min([p["alt"] for p in ps if p["res"] == "verdeckt" and hi is not None and p["alt"] > hi], default=None)
            if top is not None:                        # Überhang: zwischen höchstem freien Punkt darunter und ihm halbieren
                below = max(p["alt"] for p in ps if p["res"] == "frei" and p["alt"] < top)
                if top - below > BISECT_MIN and not self._have(az, round((below + top) / 2, 1)):
                    out.append({"az": az, "alt": round((below + top) / 2, 1), "why": "halbieren"})
            lims[az] = (lo, hi)
        if self.kind == "voll":
            for az in sorted(lims):
                if az % COL_STEP:
                    continue
                nb = round((az + COL_STEP) % 360, 1)
                if nb not in lims or (az, nb) in self.az_split_done:
                    continue
                a, b = lims[az], lims[nb]
                la = a[0] if a[0] is not None else (self.floor - 1 if a[1] is not None else None)
                lb = b[0] if b[0] is not None else (self.floor - 1 if b[1] is not None else None)
                if la is None or lb is None or abs(la - lb) <= AZ_SPLIT:
                    continue
                self.az_split_done.add((az, nb))
                # Stützstelle neben der Spalte mit dem höheren Hindernis: reicht es bis hierher?
                mid_az = round((az + skymap.STEP) % 360 if la > lb else (nb - skymap.STEP) % 360, 1)
                alt = round(max(self.floor, (max(la, lb) + min(la, lb)) / 2 + skymap.MARGIN), 1)
                if not self._have(mid_az, alt):
                    out.append({"az": mid_az, "alt": alt, "why": "halbieren"})
        return out

    # ------------------------------------------------------------------ Ablauf --- #
    def next_probe(self, now: float, avoid=None) -> dict | None:
        """Nächster Peilpunkt (oder None = fertig bzw. warten). `avoid(az, alt)` → Grund oder None (Mond)."""
        if self.state != "laeuft" or self.cur is not None or now < self.wait_until:
            return None
        if now - self.started > (SHORT_MAX_S if self.kind == "kurz" else SCAN_MAX_S) or self.n >= MAX_PROBES:
            return self._close("Zeit- bzw. Punktgrenze erreicht – Karte aus den bisherigen Punkten.")
        if self.ref and (self.since_ref >= REF_EVERY or len(self.streak) >= STREAK or self.wait_until):
            self.wait_until = 0.0
            return self._take({**self._ref_near(), "why": "gegenprobe"})
        cols = {round(p["az"] % 360) for p in self._measured() if p["why"] == "raster"}
        if self.ref is None and len(cols) >= NOFREE_COLS * (self.steep + 1) and self.steep < len(STEEP)                 and not any(p["res"] == "frei" for p in self.done):
            # keine Sterne in mehreren Richtungen: weit weg davon steil prüfen – Hauswand oder Wolken?
            off, alt = STEEP[self.steep]
            self.steep += 1
            return self._take({"az": round((self.done[-1]["az"] + off) % 360, 1), "alt": alt, "why": "gegenprobe"})
        while True:
            if not self.queue:
                self.queue = self._next_batch()
                if not self.queue:
                    if self.unconfirmed and self.ref:      # Schluss-Gegenprobe für die letzten verdeckten Punkte
                        return self._take({**self._ref_near(), "why": "gegenprobe"})
                    return self._close("")
            p = self.queue.pop(0)
            why = avoid(p["az"], p["alt"]) if avoid else None
            if why:
                self.done.append({**p, "res": "ausgelassen", "note": why, "t": now})
                if p["why"] == "raster":               # Spalte nicht aufgeben: nächste Stufe, die nicht am Mond liegt
                    alt = next((a for a in LEVELS if a > p["alt"] and not avoid(p["az"], a) and not self._have(p["az"], a)), None)
                    if alt is not None:
                        self.queue.insert(0, {"az": p["az"], "alt": alt, "why": "raster"})
                continue
            return self._take(p)

    def _ref_near(self) -> dict:
        """Gegenprobe: der nächstgelegene schon freie Punkt (kurzer Schwenk), mindestens 20° hoch."""
        last = self.done[-1] if self.done else self.ref
        free = [p for p in self.done if p.get("res") == "frei" and p["alt"] >= 20] or [self.ref]
        best = min(free, key=lambda p: (abs(((p["az"] - last["az"] + 540) % 360) - 180) + abs(p["alt"] - last["alt"])))
        return {"az": best["az"], "alt": best["alt"]}

    def _next_batch(self) -> list[dict]:
        if self.kind == "kurz" and self.setup is None and all(p["why"] != "grenze" for p in self.done):
            got = skymap.match(self.groups, [p for p in self.done if p["why"] == "erkennen"])
            if got is None:
                self.kind, self.why = "voll", "Keine bekannte Aufstellung passt – vermesse den ganzen Himmel."
                self._raster()
                return self.queue
            self.setup = got
            return [dict(p) for p in skymap.edges(got["cols"], self.floor) if not self._have(p["az"], p["alt"])]
        ref = self._refine()
        if ref:                                        # eine Runde im Kreis ab der aktuellen Richtung (kurze Schwenks)
            here = self.done[-1]["az"] if self.done else 0.0
            return sorted(ref, key=lambda p: ((p["az"] - here) % 360, p["alt"]))
        if not self.retried:
            self.retried = True
            doubt = [p for p in self.done if p.get("res") == "zweifel" and not p.get("retry")]
            for p in doubt:
                p["retry"] = True
            return [{"az": p["az"], "alt": p["alt"], "why": "wiederholung"} for p in doubt]
        return []

    def _take(self, p: dict) -> dict:
        self.n += 1
        self.cur = {**p, "n": self.n}
        return self.cur

    def _close(self, why: str) -> None:
        self.state = "fertig"
        if why:
            self.why = why
        self.cur = None
        return None

    def result(self, res: str, now: float, stars: int | None = None, solved: bool = False):
        """Ergebnis des laufenden Punkts: "frei" | "verdeckt" | "zweifel"."""
        p = self.cur
        if p is None:
            return
        self.cur = None
        p.update(res=res, stars=stars, solved=solved, t=now, sure=solved and res == "frei")
        self.done.append(p)
        if p["why"] == "gegenprobe":
            return self._ref_result(p, now)
        if p["why"] == "wiederholung" and res == "zweifel":
            p["res"] = "verdeckt"                     # zweimal unklar: als (unsicher) verdeckt werten
        if p["res"] == "frei":
            self.streak = set()
            if self.ref is None and p["alt"] >= 20:
                self._first_ref(p)
        elif p["res"] == "verdeckt":
            self.streak.add(round(p["az"] % 360))
            self.unconfirmed.append(p)
        self.since_ref += 1
        self._follow(p)

    def _first_ref(self, p: dict):
        """Erster freier Punkt: wird Gegenprobe. Verdeckte Punkte davor hatten keine Gegenprobe vorher (vielleicht Wolke, die
        inzwischen weitergezogen ist) – nicht bestätigen, sondern noch einmal messen (Review PR #143)."""
        self.ref = {"az": p["az"], "alt": p["alt"]}
        for q in self.unconfirmed:
            if not q.get("retry"):
                q["retry"] = True
                self.queue.append({"az": q["az"], "alt": q["alt"], "why": "wiederholung"})
        self.unconfirmed, self.since_ref, self.streak, self.ref_fails = [], 0, set(), 0

    def ref_ok(self, now: float):
        for q in self.unconfirmed:
            q["sure"] = True
        self.unconfirmed, self.since_ref, self.streak, self.ref_fails = [], 0, set(), 0

    def _ref_result(self, p: dict, now: float):
        if p["res"] == "frei":
            if self.ref is None:
                return self._first_ref(p)
            return self.ref_ok(now)
        if self.ref is None:                           # steile Gegenprobe ohne Sterne
            if self.steep >= len(STEEP):
                self.state, self.why = "wolken", "Auch steil in anderen Richtungen keine Sterne – eher Wolken als Hindernisse."
            return
        self.ref_fails += 1
        for q in self.unconfirmed:                     # seit der letzten guten Gegenprobe: vielleicht Wolke
            if not q.get("retry"):
                q["res"], q["retry"] = "zweifel", True
                self.queue.append({"az": q["az"], "alt": q["alt"], "why": "wiederholung"})
            else:
                q["res"] = "zweifel"
        self.unconfirmed, self.streak, self.since_ref = [], set(), 0
        if self.ref_fails >= REF_FAILS:
            self.state, self.why = "wolken", "Die Gegenprobe ist zweimal gescheitert – Wolken ziehen durch."
            return
        self.wait_until = now + CLOUD_WAIT_S

    def abort(self, why: str):
        if self.state == "laeuft":
            self.state, self.why, self.cur = "abgebrochen", why, None

    # ----------------------------------------------------------------- Ergebnis --- #
    def points(self) -> list[dict]:
        """Punkte für die Karte (Gegenproben zählen als freie Punkte, Ausgelassene nicht)."""
        return [{"az": p["az"], "alt": p["alt"], "res": p["res"], "sure": bool(p.get("sure")), "stars": p.get("stars"),
                 "src": "scan", "t": p.get("t")} for p in self.done if p.get("res") in ("frei", "verdeckt", "zweifel")]

    def view(self, now: float) -> dict:
        cols = {round(p["az"] % 360) for p in self.done if p["why"] == "raster"}
        prog = (min(1.0, len(cols) / (360 / COL_STEP)) * 0.8 + (0.2 if self.state != "laeuft" else 0.0)) if self.kind == "voll" \
            else min(1.0, len(self.done) / max(1, len(self.done) + len(self.queue)))
        return {"kind": self.kind, "state": self.state, "why": self.why, "n": len(self.done), "queued": len(self.queue),
                "started": self.started, "elapsed": round(now - self.started), "progress": round(prog, 2),
                "setup": self.setup["id"] if self.setup else None, "waiting": max(0, round(self.wait_until - now)) if self.wait_until else 0,
                "cur": {k: self.cur[k] for k in ("az", "alt", "why")} if self.cur else None,
                "points": [{k: p.get(k) for k in ("az", "alt", "res", "sure", "why")} for p in self.done]}
