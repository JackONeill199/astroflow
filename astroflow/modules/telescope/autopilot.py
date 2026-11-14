"""Autopilot: arbeitet den Plan der Nacht ab und passt auf das Teleskop auf.

`tick(now)` ist ein Takt (alle paar Sekunden aus dem Thread der Einheit, in Tests mit virtueller Uhr). Reihenfolge je Takt:
Zustand lesen → Verbindung/Kontrolle → Einklappen bestätigt? → Wächter (Dämmerung, Wetter, Tau, Akku, Wolken im Bild) →
öffnen/ausrichten → aktuelles Ziel prüfen (Hindernis? Ende?) → nächstes freies Ziel starten.

Hindernis oder Wolke: Scheitert ein Ziel (Astrometrie oder fast nur verworfene Bilder), während ein Ziel in deutlich anderer
Richtung kurz vorher gut lief, ist es ein Hindernis → Richtung für diese Nacht sperren, als Vorschlag für die Horizontmaske
merken (übernommen wird nur nach Rückfrage). Scheitern zwei Richtungen ohne Erfolg dazwischen, sind es Wolken → einklappen.

Der Ort geht nur an den Treiber (`init`), nie ins Protokoll oder an den Browser.
"""
from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field

from ...core import sites
from . import sky
from .driver import DriverError, PlanItem, Target, TelescopeState
from .machine import Machine

DEFAULTS = {
    "cloud_max": 70,         # % Vorhersage
    "wind_max": 30,          # km/h
    "dew_min": 2.0,          # °C Taupunktabstand
    "dew_min_stellina": 3.0,
    "sun_limit": -12.0,      # Beginn/Ende der Nacht (nautisch); -18 = nur astronomisch dunkel
    "reopen_min": 20,        # so lange müssen die Bedingungen stabil gut sein
    "min_alt": 30.0,         # wie Planer/Heute Nacht
    "exposure": 10.0,        # s (Gerätestandard)
    "battery_min": 15,       # %
    "fallback": True,        # Geräte-Plan als Rückfall
}
WEATHER_EVERY = 900          # s zwischen zwei Wetterabfragen
RECONNECT_EVERY = 30
PARK_CONFIRM_S = 150         # so lange darf Einklappen dauern (Probe 3: ~47 s, plus evtl. laufendes Öffnen ~49 s)
PARK_TRIES = 3
WINDOW_FRAMES = 6            # Fenster für „fast nur verworfene Bilder“
BAD_RATIO = 0.8
OK_MEMORY = 20 * 60          # Erfolg in anderer Richtung gilt so lange als Beleg „kein Wolkenhimmel“
OTHER_DIR = 20.0             # Grad Abstand = andere Richtung
BLOCK_AZ = 10.0              # gesperrte Richtung: ±Azimut …
BLOCK_ALT = 5.0              # … bis über die gescheiterte Höhe
MAX_EVENTS = 300
SUB_MAX = 1800              # Ersatzziel höchstens so lange am Stück
OPEN = ("geplant", "verdeckt", "uebersprungen")     # noch nicht erledigt (verdeckt/übersprungen: neuer Versuch, sobald frei)


@dataclass
class Item:
    id: str
    name: str
    ra: float
    dec: float
    start: float
    end: float
    hours: float = 0.0
    status: str = "geplant"          # geplant | aktiv | fertig | uebersprungen | verdeckt | abgebrochen
    why: str = ""
    real_start: float | None = None
    real_end: float | None = None
    frames: int = 0
    stacked: int = 0
    rejected: int = 0
    az: float | None = None
    alt: float | None = None
    substitute_for: str | None = None
    manual: bool = False

    @property
    def target(self) -> Target:
        return Target(self.id, self.name, self.ra, self.dec)

    def view(self) -> dict:
        return {k: getattr(self, k) for k in ("id", "name", "start", "end", "hours", "status", "why", "real_start", "real_end",
                                              "frames", "stacked", "rejected", "substitute_for", "manual")}


@dataclass
class Hold:
    kind: str        # twilight | weather | dew | battery | clouds | control
    text: str


@dataclass
class Env:
    """Was der Autopilot von außen braucht (Dienste von AstroFlow; in Tests Attrappen)."""
    site: object = None                 # (night) -> {lat, lon, horizon, name} | None
    forecast: object = None             # (site, t0, t1) -> {"hours": [...]} | None; Ausnahme = kein Internet
    plan: object = None                 # (night, model) -> [Item]
    device: object = None               # (model) -> {gust, dew_heater, name}


def _fmt(ts: float) -> str:
    return time.strftime("%H:%M", time.localtime(ts))


class Autopilot:
    def __init__(self, driver, env: Env, cfg: dict | None = None, clock=None):
        self.driver = driver
        self.env = env
        self.cfg = {**DEFAULTS, **(cfg or {})}
        self.clock = clock
        self.machine = Machine()
        self.mode = "aus"                 # aus | laeuft | pausiert | wartet | fertig
        self.hold: Hold | None = None
        self.items: list[Item] = []
        self.night: str | None = None
        self.site: dict | None = None
        self.state = TelescopeState()
        self.events: deque = deque(maxlen=MAX_EVENTS)
        self.seq = 0
        self.blocked: list[dict] = []     # gesperrte Richtungen dieser Nacht {az, alt, t, target, sure}
        self.proposals: list[dict] = []   # Vorschläge für die Horizontmaske (bis zur Rückfrage)
        self.ok_marks: deque = deque(maxlen=200)     # (t, az, alt) gute Bilder
        self.fail_marks: deque = deque(maxlen=50)    # (t, az, alt)
        self.samples: deque = deque(maxlen=60)       # (frames, rejected) des aktiven Ziels
        self.weather: dict | None = None
        self.weather_t = 0.0
        self.weather_offline = False
        self.good_since: float | None = None
        self.clouds_at: float | None = None
        self.park_pending: dict | None = None
        self.alarm: str | None = None
        self.estop = threading.Event()
        self.confirmed_night: str | None = None
        self.last_connect = 0.0
        self.lost_at: float | None = None
        self.warned: set = set()
        self.dark: dict | None = None
        self.frames_fetched = 0
        self.plan_note: str | None = None
        self.lock = threading.RLock()

    # ------------------------------------------------------------ Protokoll --- #
    def log(self, text: str, kind: str = "info", now: float | None = None):
        self.seq += 1
        self.events.append({"id": self.seq, "t": now if now is not None else self._now(), "kind": kind, "text": text})

    def _now(self) -> float:
        return self.clock.now() if self.clock else time.time()

    def _once(self, key: str, text: str, kind: str = "warn", now: float | None = None):
        if key not in self.warned:
            self.warned.add(key)
            self.log(text, kind, now)

    # ------------------------------------------------------------- Befehle --- #
    def _cmd(self, what: str, fn, *a, now=None) -> bool:
        if self.estop.is_set() and what != "park":
            return False
        if not self.machine.can(what):
            return False
        try:
            fn(*a)
            return True
        except DriverError as e:
            self.log(f"{_label(what)} fehlgeschlagen: {e}", "bad", now)
            return False

    def current_night(self, now: float) -> str:
        """Nachtdatum für `now` (gleiche Regel wie `load_night`, über core/sites)."""
        site0 = self.env.site(None) if self.env.site else None
        lon = site0["lon"] if site0 else None
        return sites.night_of(now, lon=lon) or sites.planning_night(now, lon or 0.0)

    def load_night(self, now: float):
        """Plan dieser Nacht laden (Ort und Nacht über core/sites – Nachtdatum nie selbst rechnen)."""
        site0 = self.env.site(None) if self.env.site else None
        self.night = self.current_night(now)
        self.site = (self.env.site(self.night) if self.env.site else None) or site0
        got = self.env.plan(self.night, self.state.model or "") if self.env.plan else None
        got = [] if got is None else got                 # leere PlanList behalten (trägt den Hinweis)
        self.plan_note = getattr(got, "note", None)
        items = list(got)
        manual = [i for i in self.items if i.manual and i.status == "geplant"]
        self.items = sorted(items + manual, key=lambda i: i.start)
        self.blocked, self.warned = [], set()
        self.dark = self._dark_window(now) if self.site else None

    def _dark_window(self, now: float) -> dict | None:
        """Dunkle Zeit (Sonne unter der Grenze) um `now` – für Zeitleiste und Wartezeit."""
        lat, lon, lim = self.site["lat"], self.site["lon"], float(self.cfg["sun_limit"])
        ts = [now - 14 * 3600 + k * 300 for k in range(int(28 * 12) + 1)]
        dark = [sky.sun_alt(t, lat, lon) <= lim for t in ts]
        spans, a = [], None
        for t, d in zip(ts, dark):
            if d and a is None:
                a = t
            if not d and a is not None:
                spans.append((a, t))
                a = None
        if a is not None:
            spans.append((a, ts[-1]))
        if not spans:
            return None
        cur = next((s for s in spans if s[0] <= now < s[1]), None) or next((s for s in spans if s[0] > now), spans[-1])
        return {"start": cur[0], "end": cur[1]}

    # --------------------------------------------------------------- Start --- #
    def start(self, now: float):
        with self.lock:
            self.estop.clear()
            self.alarm = None
            if not self.items or self.night is None:
                self.load_night(now)
            self.mode = "laeuft"
            self.hold = None
            self.log("Autopilot gestartet." + (f" {len(self.items)} Ziele für diese Nacht." if self.items else " Noch keine Ziele – im Planer planen oder Ziele hinzufügen."), "info", now)

    def pause(self, now: float):
        with self.lock:
            if self.mode in ("laeuft", "wartet"):
                self.mode = "pausiert"
                cur = self._current()
                if cur:
                    self._end_item(cur, "geplant", "pausiert", now)
                self._cmd("stop", self.driver.stop, now=now)
                self.log("Pausiert – das Teleskop bleibt offen und steht still.", "info", now)

    def resume(self, now: float):
        with self.lock:
            if self.mode == "pausiert":
                self.mode = "laeuft"
                self.log("Weiter geht's.", "info", now)

    def emergency(self, now: float, why: str = "Sofort einklappen"):
        """Not-Aus: ohne Warteschlange, ohne auf den Takt zu warten. Stoppt alles und klappt ein."""
        self.estop.set()
        self.mode = "aus"
        self.log(f"{why}: stoppe und klappe ein.", "bad", now)
        if self.machine.phase in ("oeffnet", "richtet_aus"):        # Probe 3: laufende Operation lässt sich nicht abbrechen
            self.log("Das Teleskop beendet zuerst den laufenden Schritt (höchstens etwa eine Minute) und klappt dann ein.", "warn", now)
        try:
            self.driver.stop()
        except Exception:
            pass
        self._park_now(now, why, force=True)

    def _park_now(self, now: float, why: str, force: bool = False):
        cur = self._current()
        if cur:
            self._end_item(cur, "abgebrochen" if force else "geplant", why, now)
        try:
            if force and self.state.master != "us":
                self.driver.take_control()
            self.driver.park()
            if not self.park_pending:
                self.park_pending = {"since": now, "tries": 1, "why": why}
        except DriverError as e:
            self.park_pending = self.park_pending or {"since": now, "tries": 1, "why": why}
            self.log(f"Einklappen fehlgeschlagen: {e}", "bad", now)
        except Exception:
            self.park_pending = self.park_pending or {"since": now, "tries": 1, "why": why}

    # ---------------------------------------------------------------- Takt --- #
    def tick(self, now: float | None = None):
        now = self._now() if now is None else now
        with self.lock:
            try:
                self._tick(now)
            finally:
                if self.estop.is_set():           # Not-Aus während des Takts: der Takt darf den Modus nicht zurücksetzen
                    self.mode = "aus"

    def _tick(self, now: float):
        try:
            s = self.driver.state()
        except DriverError:
            s = TelescopeState(connected=False)
        prev, self.state = self.state, s
        ch = self.machine.update(s)
        if ch and not self.machine.plausible(*ch) and ch[1] != "getrennt":
            self.log(f"Unerwarteter Wechsel: {ch[0]} → {ch[1]}", "warn", now)
        if ch and ch[1] == "eingeklappt" and ch[0] not in ("getrennt", "fremd") and not self.park_pending:
            self.log("Teleskop hat selbst eingeklappt" + (" (Plan im Gerät beendet)." if prev.device_plan else "."), "ok", now)
        self._park_watch(s, now)
        if not s.connected:
            return self._offline(prev, now)
        if self.lost_at is not None:
            self.log("Verbindung wieder da." + (" Der Geräte-Plan lief in der Zwischenzeit weiter." if s.device_plan else ""), "ok", now)
            self.lost_at = None
        if s.master == "other":
            if self.mode in ("laeuft", "wartet"):
                self.mode = "pausiert"
                self.log("Die Handy-App hat die Steuerung übernommen – Autopilot pausiert.", "warn", now)
            return
        if s.master != "us" and self.mode in ("laeuft", "wartet"):
            self._take(now)
            return
        if s.raw_enabled is False:
            self._once("raw", "Rohbilder werden am Teleskop nicht gespeichert – für die AstroFlow-Pipeline in der App einschalten.", "warn", now)
        if self.mode == "pausiert" and not self.estop.is_set():
            # Pause = keine neuen Ziele, aber die Schutz-Wächter bleiben aktiv: offen stehendes Teleskop bei Wetter, Tau,
            # Akku oder Morgendämmerung trotzdem einklappen (der Modus bleibt „pausiert“, nichts startet von selbst).
            if self.night is None:
                self.load_night(now)
            self._refresh_weather(now)
            holds = self.conditions(now, s)
            if holds and not s.parked and not self.park_pending and s.step != "parking":
                self.log(f"{holds[0].text} – klappe trotz Pause ein.", "bad", now)
                self._park_now(now, holds[0].text)
            return
        if self.mode not in ("laeuft", "wartet") or self.estop.is_set():
            return
        if self.night is None or (self.dark and now > self.dark["end"] + 3600):
            self.load_night(now)
        self._refresh_weather(now)
        self._track(s, now)
        holds = self.conditions(now, s)
        if holds:
            return self._hold(holds[0], now)
        if self.mode == "wartet":
            if not self._may_reopen(now):
                return
            self.mode, self.hold = "laeuft", None
            self.log("Bedingungen wieder gut – es geht weiter.", "ok", now)
        self._run(s, now)

    def _take(self, now):
        try:
            self.driver.take_control()
        except DriverError as e:
            self.log(f"Kontrolle übernehmen fehlgeschlagen: {e}", "bad", now)

    def _offline(self, prev: TelescopeState, now: float):
        if self.lost_at is None and self.mode in ("laeuft", "wartet", "pausiert"):
            self.lost_at = now
            fb = prev.device_plan or (self.cfg["fallback"] and any(i.status == "aktiv" for i in self.items))
            self.log("Verbindung zum Teleskop verloren." + (" Der Plan im Gerät arbeitet weiter und klappt am Ende ein." if fb else ""), "bad", now)
        if self.mode != "aus" and now - self.last_connect >= RECONNECT_EVERY:
            self.last_connect = now
            try:
                self.driver.connect()
                self.driver.take_control()
            except DriverError:
                pass

    def _park_watch(self, s: TelescopeState, now: float):
        p = self.park_pending
        if not p:
            return
        if s.connected and s.parked and s.step != "parking":
            self.park_pending, self.alarm = None, None
            self.log("Teleskop ist eingeklappt.", "ok", now)
            return
        if now - p["since"] < PARK_CONFIRM_S * p["tries"]:
            return
        if p["tries"] >= PARK_TRIES:
            if not self.alarm:
                self.alarm = "Einklappen nicht bestätigt – bitte das Teleskop sofort von Hand prüfen und einklappen!"
                self.log(self.alarm, "bad", now)
            return
        p["tries"] += 1
        self.log(f"Einklappen noch nicht bestätigt – Versuch {p['tries']} von {PARK_TRIES}.", "warn", now)
        try:
            if s.connected and s.master != "us":
                self.driver.take_control()
            self.driver.park()
        except DriverError:
            pass

    # ------------------------------------------------------------- Wächter --- #
    def _refresh_weather(self, now: float):
        if not self.env.forecast or not self.site or now - self.weather_t < WEATHER_EVERY:
            return
        self.weather_t = now
        try:
            w = self.env.forecast(self.site, now - 3600, now + 12 * 3600)
        except Exception:
            w = None
            if not self.weather_offline:
                self.weather_offline = True
                since = f" (Vorhersage von {_fmt(self.weather['fetched'])})" if self.weather else ""
                self.log(f"Kein Internet für die Wettervorhersage{since} – zählt jetzt vor allem, was das Teleskop im Bild sieht.", "warn", now)
            return
        if w and w.get("hours"):
            self.weather = {**w, "fetched": now}
            if self.weather_offline:
                self.log("Wettervorhersage wieder aktuell.", "ok", now)
            self.weather_offline = False

    def weather_now(self, now: float) -> dict | None:
        """Vorhersage für jetzt, linear zwischen den Stundenwerten (höchstens 1 h über die Vorhersage hinaus)."""
        hh = sorted((self.weather or {}).get("hours") or [], key=lambda h: h["t"])
        if not hh or now < hh[0]["t"] - 3600 or now > hh[-1]["t"] + 3600:
            return None
        a = max((h for h in hh if h["t"] <= now), key=lambda h: h["t"], default=hh[0])
        b = min((h for h in hh if h["t"] > now), key=lambda h: h["t"], default=hh[-1])
        f = 0.0 if b["t"] == a["t"] else (now - a["t"]) / (b["t"] - a["t"])
        out = {"t": now}
        for k in ("cloud", "wind", "dew_spread"):
            va, vb = a.get(k), b.get(k)
            out[k] = None if va is None and vb is None else (va if vb is None else vb if va is None else round(va + (vb - va) * f, 1))
        return out

    def conditions(self, now: float, s: TelescopeState | None = None) -> list[Hold]:
        s = s or self.state
        out = []
        if self.site:
            sun = sky.sun_alt(now, self.site["lat"], self.site["lon"])
            if sun > float(self.cfg["sun_limit"]):
                rising = sky.sun_alt(now + 600, self.site["lat"], self.site["lon"]) > sun
                out.append(Hold("dawn" if rising else "twilight",
                                "Morgendämmerung – die Nacht ist vorbei." if rising else "Noch zu hell – warte auf die Dunkelheit."))
        h = self.weather_now(now)
        if h:
            if h.get("cloud") is not None and h["cloud"] > self.cfg["cloud_max"]:
                out.append(Hold("weather", f"Wolken {round(h['cloud'])} % laut Vorhersage (Grenze {self.cfg['cloud_max']} %)."))
            if h.get("wind") is not None and h["wind"] > self.cfg["wind_max"]:
                out.append(Hold("weather", f"Wind {round(h['wind'])} km/h laut Vorhersage (Grenze {self.cfg['wind_max']} km/h)."))
            lim = self.dew_limit(s.model)
            if h.get("dew_spread") is not None and h["dew_spread"] < lim:
                out.append(Hold("dew", f"Taugefahr: Taupunktabstand {h['dew_spread']:.1f} °C (Grenze {lim:.1f} °C)."))
        if self.clouds_at is not None and now - self.clouds_at < self.cfg["reopen_min"] * 60:
            out.append(Hold("clouds", "Wolken im Bild gemessen (mehrere Richtungen ohne Sterne)."))
        if s.battery is not None and not s.charging and s.battery < self.cfg["battery_min"]:
            out.append(Hold("battery", f"Akku nur noch {s.battery} % – Teleskop wird eingeklappt."))
        return out

    def dew_limit(self, model: str) -> float:
        return float(self.cfg["dew_min_stellina"] if (model or "").startswith("stellina") else self.cfg["dew_min"])

    def _hold(self, h: Hold, now: float):
        final = h.kind in ("dawn", "battery")
        if self.hold is None or self.hold.kind != h.kind or self.mode != "wartet":
            self.log(h.text, "warn" if h.kind in ("twilight",) else "bad", now)
        self.hold, self.good_since = h, None
        self.mode = "fertig" if final and self._started_tonight() else "wartet"
        if final and self.mode == "fertig":
            for i in self.items:
                if i.status == "geplant":
                    i.status, i.why = "uebersprungen", "Nacht vorbei" if h.kind == "dawn" else "Akku"
            self.log("Nacht beendet." if h.kind == "dawn" else "Abgebrochen wegen Akku.", "info", now)
        if not self.state.parked and not self.park_pending:
            self._cmd("stop", self.driver.stop, now=now)
            self._park_now(now, h.text)

    def _started_tonight(self) -> bool:
        return any(i.real_start for i in self.items)

    def _may_reopen(self, now: float) -> bool:
        if self.hold and self.hold.kind in ("twilight",):
            return True
        if self.good_since is None:
            self.good_since = now
            self.log(f"Bedingungen besser – warte {self.cfg['reopen_min']} min, ob es so bleibt.", "info", now)
            return False
        return now - self.good_since >= self.cfg["reopen_min"] * 60

    # ------------------------------------------------------------- Ablauf --- #
    def _current(self) -> Item | None:
        return next((i for i in self.items if i.status == "aktiv"), None)

    def _run(self, s: TelescopeState, now: float):
        ph = self.machine.phase
        cur = self._current()
        if cur and ph in ("eingeklappt", "klappt_ein", "geoeffnet"):      # Gerät hat selbst eingeklappt (Geräte-Plan zu Ende, App)
            self._end_item(cur, "fertig" if now >= cur.end - 120 else "abgebrochen", "" if now >= cur.end - 120 else "Teleskop eingeklappt", now)
        if ph in ("oeffnet", "klappt_ein", "richtet_aus"):
            return
        if ph == "eingeklappt":
            if not self._anything_free(now) and not self._current():
                return self._idle_or_done(now)
            if self._cmd("open", self.driver.open, now=now):
                self.log("Öffne das Teleskop.", "move", now)
            return
        if ph == "geoeffnet":
            if self.site and self._cmd("init", self.driver.init, self.site["lat"], self.site["lon"], now=now):
                self.log("Richte das Teleskop aus (Sterne, Fokus).", "move", now)
            return
        cur = self._current()
        if cur:
            if s.target and s.target.get("id") != cur.id and not s.device_plan:
                self._end_item(cur, "abgebrochen", "anderes Ziel am Gerät", now)
            elif self._failed(cur, s):
                return self._failure(cur, s, now)
            elif not (vis := self.free(cur.ra, cur.dec, now))[0]:
                # Ziel verlässt den freien Bereich (Mindesthöhe, Horizontmaske, gesperrte Richtung): sauber beenden, weiter
                self._cmd("stop", self.driver.stop, now=now)
                self._end_item(cur, "fertig" if cur.stacked else "uebersprungen", vis[1], now)
                self.log(f"{cur.name} verlässt den freien Bereich ({vis[1]}) – beende und wechsle zum nächsten Ziel.", "warn", now)
            elif now >= cur.end:
                self._end_item(cur, "fertig", "", now)
            else:
                return
        nxt = self._next_free(now)
        if nxt is None:
            return self._idle_or_done(now)
        self._start_item(nxt, now)

    def _idle_or_done(self, now: float):
        if any(i.status in OPEN and i.end > now for i in self.items):
            if self.machine.phase in ("schwenkt", "astrometrie", "nimmt_auf"):
                self._cmd("stop", self.driver.stop, now=now)
            self._once(f"wait:{int(now // 1800)}", "Gerade ist kein geplantes Ziel frei – warte auf das nächste.", "info", now)
            return
        if not self.items:
            return
        if not self.state.parked and not self.park_pending:
            self.log("Alle Ziele dieser Nacht erledigt – klappe ein.", "ok", now)
            self._park_now(now, "Plan erledigt")
        self.mode = "fertig"

    def free(self, ra: float, dec: float, now: float) -> tuple[bool, str, float, float]:
        """(frei, Grund, Azimut, Höhe) – Regel wie Planer/Heute Nacht (max(Mindesthöhe, Horizontmaske)) + gesperrte Richtungen."""
        if not self.site:
            return False, "kein Standort", 0.0, 0.0
        az, alt = sky.altaz(ra, dec, self.site["lat"], self.site["lon"], now)
        lim = sky.limit(az, self.site.get("horizon"), float(self.cfg["min_alt"]))
        if alt < lim:
            return False, f"zu tief ({alt:.0f}° unter {lim:.0f}°)", az, alt
        for b in self.blocked:
            if not b["sure"] and now - b["t"] > OK_MEMORY:      # unsichere Sperre: nach 20 min neu versuchen (war evtl. eine Wolke)
                continue
            if abs((az - b["az"] + 540) % 360 - 180) <= BLOCK_AZ and alt <= b["alt"] + BLOCK_ALT:
                return False, "Richtung heute verdeckt", az, alt
        return True, "", az, alt

    def _anything_free(self, now: float) -> bool:
        """Lohnt sich Öffnen? (ohne Seiteneffekte: ein offenes Ziel der Nacht ist jetzt frei)"""
        return any(i.status in OPEN and i.end > now and self.free(i.ra, i.dec, now)[0] for i in self.items)

    def _next_free(self, now: float) -> Item | None:
        """1. Ziel, dessen Abschnitt gerade läuft (auch ein vorhin verdecktes, sobald es aus dem Hindernis heraus ist).
        2. Ist keines frei: ein späteres Ziel des Plans, das jetzt frei ist, als Ersatz für den Rest des Abschnitts.
        3. Lücke im Plan: das nächste Ziel früher beginnen, wenn es schon frei ist."""
        due = sorted([i for i in self.items if i.status in OPEN and i.start <= now < i.end], key=lambda i: i.status != "geplant")
        for i in due:
            ok, why, _, _ = self.free(i.ra, i.dec, now)
            if ok:
                if i.status != "geplant":
                    self.log(f"{i.name} ist jetzt frei – neuer Versuch.", "info", now)
                return i
            if i.status == "geplant":
                i.status, i.why = ("verdeckt" if "verdeckt" in why else "uebersprungen"), why
                self.log(f"{i.name} übersprungen: {why}.", "warn", now)
        later = sorted([i for i in self.items if i.status == "geplant" and i.start > now], key=lambda i: i.start)
        for i in later:
            if not self.free(i.ra, i.dec, now)[0]:
                continue
            if due:                                  # Ersatz höchstens 30 min, dann prüfen, ob das geplante Ziel frei ist
                end = min(due[0].end, i.end, now + SUB_MAX)
                if end - now < 600:
                    continue
                sub = Item(i.id, i.name, i.ra, i.dec, now, end, hours=round((end - now) / 3600, 2), substitute_for=due[0].name)
                self.items.append(sub)
                self.items.sort(key=lambda x: x.start)
                self.log(f"Ersatzziel: {i.name} statt {due[0].name}.", "info", now)
                return sub
            self.log(f"Beginne früher mit {i.name}.", "info", now)
            i.start = now
            return i
        return None

    def _start_item(self, it: Item, now: float):
        it.status, it.real_start, it.why = "aktiv", now, ""
        it.frames = it.stacked = it.rejected = 0
        self.samples.clear()
        _, _, it.az, it.alt = self.free(it.ra, it.dec, now)
        ok = False
        caps = getattr(self.driver, "capabilities", frozenset())
        if self.cfg["fallback"] and "device_plan" in caps:
            rest = [PlanItem(it.target, now, it.end)] + [PlanItem(j.target, j.start, j.end) for j in self.items
                                                        if j.status == "geplant" and j.start >= it.end]
            ok = self._cmd("device_plan", self.driver.start_device_plan, rest, float(self.cfg["exposure"]), now=now)
        if not ok:
            ok = self._cmd("observe", self.driver.observe, it.target, float(self.cfg["exposure"]), now=now)
        if ok:
            self.log(f"Schwenke zu {it.name} – bis {_fmt(it.end)}.", "move", now)
        else:
            it.status, it.real_start = "geplant", None

    def _end_item(self, it: Item, status: str, why: str, now: float):
        s = self.state
        if s.target and s.target.get("id") == it.id:
            it.frames, it.stacked, it.rejected = s.frames, s.stacked, s.rejected
        it.status, it.why, it.real_end = status, why, now
        if status == "fertig":
            self.log(f"{it.name} fertig: {it.stacked} Bilder gestapelt" + (f", {it.rejected} verworfen" if it.rejected else "") + ".", "ok", now)
        if status == "geplant":
            it.real_start = it.real_end = None

    def _track(self, s: TelescopeState, now: float):
        cur = self._current()
        if not cur or not s.target or s.target.get("id") != cur.id:
            return
        if s.stacked > cur.stacked and s.az is not None:
            self.ok_marks.append((now, s.az, s.alt))
        cur.frames, cur.stacked, cur.rejected = s.frames, s.stacked, s.rejected
        if not self.samples or self.samples[-1][0] != s.frames:
            self.samples.append((s.frames, s.rejected))

    def _failed(self, it: Item, s: TelescopeState) -> bool:
        if s.target and s.target.get("id") != it.id:
            return False
        if s.astrometry_failed:
            return True
        if s.frames < WINDOW_FRAMES or not self.samples:
            return False
        base = next((x for x in reversed(self.samples) if x[0] <= s.frames - WINDOW_FRAMES), None)
        if base is None:
            return False
        return (s.rejected - base[1]) / max(1, s.frames - base[0]) >= BAD_RATIO

    def _failure(self, it: Item, s: TelescopeState, now: float):
        az, alt = (s.az if s.az is not None else it.az or 0.0), (s.alt if s.alt is not None else it.alt or 0.0)
        why = "Lagebestimmung gescheitert" if s.astrometry_failed else "fast nur verworfene Bilder"
        # gutes Bild in anderer Richtung, das nicht durch ein späteres Scheitern dort überholt ist
        elsewhere_ok = any(now - t <= OK_MEMORY and sky.ang_dist(a, h, az, alt) > OTHER_DIR
                           and not any(f[0] > t and sky.ang_dist(f[1], f[2], a, h) <= OTHER_DIR for f in self.fail_marks)
                           for t, a, h in self.ok_marks)
        other_fail = [m for m in self.fail_marks if now - m[0] <= OK_MEMORY and sky.ang_dist(m[1], m[2], az, alt) > OTHER_DIR
                      and not any(m[0] < t <= now for t, _, _ in self.ok_marks)]
        self.fail_marks.append((now, az, alt))
        h = self.weather_now(now)
        clear_forecast = h is not None and h.get("cloud") is not None and h["cloud"] < 30
        self._cmd("stop", self.driver.stop, now=now)
        if other_fail and not elsewhere_ok and not clear_forecast:
            self._end_item(it, "geplant", "", now)
            # Die vorige „verdeckte“ Richtung war wohl auch Wolke: Sperre und Vorschlag zurücknehmen
            self.blocked = [b for b in self.blocked if b["sure"] or now - b["t"] > OK_MEMORY]
            self.proposals = [p for p in self.proposals if now - p["t"] > OK_MEMORY]
            for i in self.items:
                if i.status == "verdeckt" and i.real_end and now - i.real_end <= OK_MEMORY:
                    i.status, i.why = "geplant", ""
            self.clouds_at = now
            self.log(f"Auch {it.name} ohne Sterne ({why}) – das sind Wolken, kein Hindernis.", "bad", now)
            return self._hold(Hold("clouds", "Wolken im Bild gemessen – Teleskop wird eingeklappt."), now)
        again = any(sky.ang_dist(b["az"], b["alt"], az, alt) <= BLOCK_AZ and now - b["t"] > OK_MEMORY for b in self.blocked)
        sure = elsewhere_ok or clear_forecast or again
        self._end_item(it, "verdeckt", why, now)
        mark = {"az": round(az, 1), "alt": round(alt, 1), "t": now, "target": it.name, "sure": sure}
        self.blocked.append(mark)
        near = next((q for q in self.proposals if abs((q["az"] - mark["az"] + 540) % 360 - 180) <= BLOCK_AZ), None)
        if near:                                     # dieselbe Richtung: einen Vorschlag mit der höchsten Höhe behalten
            near.update({k: mark[k] for k in ("az", "alt", "t", "target")} if mark["alt"] >= near["alt"] else {"t": mark["t"]})
            near["sure"] = near["sure"] or mark["sure"]
        else:
            self.proposals.append(dict(mark))
        self.log(f"{it.name} verdeckt ({why}) bei Azimut {az:.0f}°, Höhe {alt:.0f}° – "
                 + ("andere Richtungen sind klar, also ein Hindernis." if sure else "vermutlich ein Hindernis.")
                 + " Weiter mit dem nächsten freien Ziel.", "warn", now)
        nxt = self._next_free(now)
        if nxt:
            self._start_item(nxt, now)

    # ------------------------------------------------------------ Ansicht --- #
    def add_target(self, t: Target, now: float, hours: float = 1.0) -> Item:
        with self.lock:
            last = max([i.end for i in self.items if i.status in ("geplant", "aktiv")] + [now])
            it = Item(t.id, t.name, t.ra, t.dec, last, last + hours * 3600, hours=hours, manual=True)
            self.items.append(it)
            self.items.sort(key=lambda x: x.start)
            self.log(f"{t.name} zur Nacht hinzugefügt ({_fmt(it.start)}–{_fmt(it.end)}).", "info", now)
            return it

    def view(self, now: float) -> dict:
        s = self.state
        h = self.weather_now(now)
        sun = sky.sun_alt(now, self.site["lat"], self.site["lon"]) if self.site else None
        cur = self._current()
        pointing = None
        if cur and self.site:
            az, alt = sky.altaz(cur.ra, cur.dec, self.site["lat"], self.site["lon"], now)
            pointing = {"az": round(az, 1), "alt": round(alt, 1)}
        return {
            "now": now, "mode": self.mode, "phase": self.machine.phase, "phase_label": self.machine.label,
            "hold": {"kind": self.hold.kind, "text": self.hold.text} if self.hold and self.mode in ("wartet", "fertig") else None,
            "state": {k: v for k, v in s.as_dict().items()},
            "current": cur.view() if cur else None, "target_pointing": pointing,
            "items": [{**i.view(), "track": self._track_of(i)} for i in self.items],
            "min_alt": self.cfg["min_alt"], "plan_note": self.plan_note,
            "night": self.night, "site": {"name": self.site.get("name"), "horizon": self.site.get("horizon")} if self.site else None,
            "dark": self.dark,
            "conditions": {
                "sun": None if sun is None else round(sun, 1), "sun_limit": self.cfg["sun_limit"],
                "cloud": None if not h else h.get("cloud"), "wind": None if not h else h.get("wind"),
                "dew_spread": None if not h else h.get("dew_spread"), "dew_limit": self.dew_limit(s.model),
                "cloud_max": self.cfg["cloud_max"], "wind_max": self.cfg["wind_max"],
                "weather_at": self.weather["fetched"] if self.weather else None, "weather_offline": self.weather_offline,
                "battery": s.battery, "battery_min": self.cfg["battery_min"],
            },
            "weather": [{"t": x["t"], "cloud": x.get("cloud")} for x in (self.weather or {}).get("hours") or []],
            "blocked": [{k: b[k] for k in ("az", "alt", "target", "sure")} for b in self.blocked],
            "proposals": [{k: p[k] for k in ("az", "alt", "target", "sure", "t")} for p in self.proposals],
            "alarm": self.alarm, "parking": bool(self.park_pending),
            "confirmed": self.confirmed_night is not None and self.confirmed_night == self.night,
        }

    def _track_of(self, it: Item) -> list[list[float]]:
        """Bahn des Ziels in seinem Abschnitt (Azimut/Höhe alle 10 min) – für die Kuppel; Koordinaten des Ortes bleiben hier."""
        if not self.site:
            return []
        n = max(2, min(40, int((it.end - it.start) / 600) + 1))
        out = []
        for k in range(n):
            az, alt = sky.altaz(it.ra, it.dec, self.site["lat"], self.site["lon"], it.start + (it.end - it.start) * k / (n - 1))
            out.append([round(az, 1), round(alt, 1)])
        return out

    def events_since(self, since: int = 0) -> list[dict]:
        return [e for e in self.events if e["id"] > since]


_LABELS = {"open": "Öffnen", "init": "Ausrichten", "observe": "Beobachtung starten", "device_plan": "Geräte-Plan starten",
           "stop": "Stoppen", "park": "Einklappen", "focus": "Fokus"}


def _label(cmd: str) -> str:
    return _LABELS.get(cmd, cmd)


def demo_items(now: float, site: dict, catalog: list[dict], hours: float = 8.0, n: int = 6) -> list[Item]:
    """Vorführ-Nacht ohne Plan (nur Simulator): helle Katalogobjekte, die in den nächsten Stunden hoch stehen."""
    out, t = [], now
    used = set()
    for k in range(n):
        tm = t + 0.5 * 3600
        best = None
        for c in catalog:
            if c["id"] in used:
                continue
            az, alt = sky.altaz(c["ra"], c["dec"], site["lat"], site["lon"], tm)
            if alt > 35 and (best is None or alt > best[0]):
                best = (alt, c)
        if not best:
            break
        c = best[1]
        used.add(c["id"])
        dur = hours / n * 3600
        out.append(Item(str(c["id"]), c.get("display") or c["id"], c["ra"], c["dec"], t, t + dur, hours=round(dur / 3600, 2)))
        t += dur
    return out


class PlanList(list):
    """Liste der Plan-Abschnitte mit optionalem Hinweis (`note`), warum sie leer ist."""
    note: str | None = None


def plan_items(plan: dict | None, night: str | None, model: str) -> list[Item]:
    """Blöcke des gespeicherten Planer-Plans (`settings.json` → `observatory.plan`) für diese Nacht und dieses Gerät.
    Stehen mehrere Geräte im Plan und passt keines zum eingestellten Modell: leer mit Hinweis – nie fremde Blöcke abarbeiten."""
    out = PlanList()
    if not plan or not night:
        return out
    targets = {t["id"]: t for t in plan.get("targets") or []}
    devs = [d["id"] for d in plan.get("devices") or []]
    want = next((d for d in devs if d == model), None) or next((d for d in devs if model and d.startswith(model)), None) \
        or (devs[0] if len(devs) == 1 else None)
    if len(devs) > 1 and want is None:
        out.note = "Der gespeicherte Plan gehört zu einem anderen Gerät – im Planer für dieses Teleskop planen oder das Gerät in den Einstellungen wählen."
        return out
    for n in plan.get("nights") or []:
        if n.get("night") != night:
            continue
        for b in n.get("blocks") or []:
            t = targets.get(b.get("target"))
            if not t or (want and b.get("device") != want) or t.get("ra") is None:
                continue
            out.append(Item(str(t["id"]), t.get("name") or str(t["id"]), float(t["ra"]), float(t["dec"]), float(b["start"]), float(b["end"]),
                            hours=float(b.get("hours") or (b["end"] - b["start"]) / 3600)))
    out.sort(key=lambda i: i.start)
    return out


def horizon_with(horizon: list | None, points: list[dict], step: int = 10) -> list[float]:
    """Horizontmaske an verdeckten Punkten anheben (2° über die gescheiterte Höhe, Nachbarstützstellen mit)."""
    h = list(horizon or [0.0] * (360 // step))
    for p in points:
        i = int(round(p["az"] / step)) % len(h)
        top = min(90.0, float(p["alt"]) + 2.0)
        for j in (i - 1, i, i + 1):
            h[j % len(h)] = round(max(h[j % len(h)], top), 1)
    return h

