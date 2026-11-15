"""Autopilot: arbeitet den Plan der Nacht ab und passt auf das Teleskop auf.

`tick(now)` ist ein Takt (alle paar Sekunden aus dem Thread der Einheit, in Tests mit virtueller Uhr). Reihenfolge je Takt:
Zustand lesen → Verbindung/Kontrolle → Einklappen bestätigt? → Wächter (Dämmerung, Wetter, Tau, Akku, Wolken im Bild,
beschlagene Optik) →
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
from . import fog, sky, skymap, skyscan
from .battery import BatteryEta, fmt_left
from .driver import DriverBusy, DriverError, PlanItem, Target, TelescopeState
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
    "scan_auto": True,       # zu Beginn der Nacht Aufstellung erkennen + Kurzscan (nur mit gelernten Aufstellungen)
    "night_autostart": False,  # „Plane meine Nacht“: Entwurf nach AUTO_GRACE_S von selbst starten (sonst „Nacht starten“)
    "night_max_targets": 5,  # „Plane meine Nacht“: höchstens so viele Ziele je Nacht
}
WEATHER_EVERY = 900          # s zwischen zwei Wetterabfragen
RAIN_EVERY = 600             # s zwischen zwei Regenabfragen (Dienst `weather_rain`)
RAIN_PARK_MIN = 30           # Regen in so vielen Minuten erwartet → laufende Beobachtung beenden, einklappen
RAIN_START_MIN = 60          # … in so vielen Minuten → kein neues Ziel, nicht öffnen
RAIN_PLAN_GUARD_S = 60       # Geräte-Plan: so viele s vor Blockende einklappen, bevor das Gerät selbst zum nächsten Ziel schwenkt
RECONNECT_EVERY = 30
PARK_CONFIRM_S = 150         # so lange darf Einklappen dauern (Probe 3: ~47 s, plus evtl. laufendes Öffnen ~49 s)
PARK_TRIES = 3
# „Zu viele verworfene Bilder“ – zwei Fenster: kurz (≥ 80 % der letzten 10) für ein plötzliches Hindernis, lang (≥ 50 % der
# letzten 30) für eines, das zunehmend verdeckt und zwischendurch noch gute Bilder lässt (Echtbetrieb 14.11.2026: M1 hinter
# dem Balkon, Verwurf langsam steigend, Endstand 32 %). Verdeckt, wenn eines davon zutrifft UND insgesamt ≥ 80 % verworfen
# sind (nie Sterne gesehen) bzw. es schon BAD_GRACE_S anhält – so lange nimmt das Gerät weiter auf (kurzer Wolkenzug).
# Ob Hindernis oder Wolke, entscheidet `_failure` (erstes Scheitern sperrt nur vorläufig).
WINDOW_FRAMES = 10
BAD_RATIO = 0.8
LONG_FRAMES = 30
LONG_BAD = 0.5
LONG_OK = 0.3                # erst darunter gilt es wieder als gut (Hysterese)
TOTAL_BAD = 0.8
BAD_GRACE_S = 5 * 60
CLOUDY = 60                  # % Bewölkung laut Wetterdaten: dann nie „sicher“ ein Hindernis
SKY_FRESH_S = 15 * 60        # Dienst `telescope_sky`: so alt darf die Messung höchstens sein
SKY_MIN_FRAMES = 5           # … und so viele Bilder umfassen
SKY_CLEAR = 0.5              # Anteil guter Bilder, ab dem der Himmel „klar“ heißt
FOG_PAUSE_S = 60 * 60        # beschlagene Optik: so lange eingeklappt warten (oder bis „Weiter“/„Start“)
OK_MEMORY = 20 * 60          # Erfolg in anderer Richtung gilt so lange als Beleg „kein Wolkenhimmel“
OTHER_DIR = 20.0             # Grad Abstand = andere Richtung
BLOCK_AZ = 10.0              # gesperrte Richtung: ±Azimut …
BLOCK_ALT = 5.0              # … bis über die gescheiterte Höhe
MAX_EVENTS = 300
MOVES = ("open", "init", "observe", "device_plan", "focus")   # Befehle, die das Teleskop bewegen (Einklappen/Stoppen nie gesperrt)
BATTERY_MIN_ITEM = 10 * 60    # reicht der Akku nicht einmal so lang, startet kein neues Ziel mehr
RETRY_FIRST = 10            # abgelehnter Befehl: erster neuer Versuch nach … s, dann doppelt so lang …
RETRY_MAX = 120             # … höchstens so lang
# dann echter Fehler (Geräte-Plan fehlt absichtlich: er ist nur ein Rückfall, abgelehnt → normale Beobachtung)
GIVE_UP = {"open": 6 * 60, "init": 15 * 60, "observe": 10 * 60, "focus": 10 * 60}
# Erfahrungswerte je Phase in s (Vespera, Probe 3: Öffnen ~49 s, Einklappen ~47 s; Auto-Init mit Plate-Solving und Fokus
# dauert ein paar Minuten). Gemessene Dauern ersetzen sie im laufenden Betrieb (`took`).
EXPECT_S = {"oeffnet": 50.0, "richtet_aus": 240.0, "klappt_ein": 50.0}
STALL_S = 90                # Ziel aktiv, Gerät tut nichts (kein GoTo/Astrometrie/Aufnahme) → nach … s handeln
SUB_MAX = 1800              # Ersatzziel höchstens so lange am Stück
OPEN = ("geplant", "verdeckt", "uebersprungen")     # noch nicht erledigt (verdeckt/übersprungen: neuer Versuch, sobald frei)
FREE_EVERY = 10 * 60        # gutes Bild eines Ziels: höchstens so oft ein freier Punkt für die Himmelskarte
AUTO_GRACE_S = 60           # „Plane meine Nacht“ mit „gleich starten“: so viele echte Sekunden Frist zum Abbrechen
REPLAN_EVERY = 10 * 60      # Rest der Nacht höchstens so oft neu planen (Ausfall, Wiederöffnen)
REFILL_MIN_S = 60 * 60      # Plan früh erledigt: neu planen, wenn noch so viel Dunkelheit bleibt
AUTO_LABEL = {"entwurf": "Entwurf – wartet auf den Start", "vermessen": "Himmel vermessen", "laeuft": "Beobachtet nach Plan",
              "fertig": "Nacht beendet"}


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
    seen: bool = False               # Gerät hat an diesem Versuch gearbeitet (GoTo/Astrometrie/Aufnahme gemeldet)
    restarts: int = 0
    auto: bool = False               # von „Plane meine Nacht“ gewählt
    reason: str = ""                 # Begründung der automatischen Wahl
    category: str = ""               # more | new | comet (automatische Wahl)

    @property
    def target(self) -> Target:
        return Target(self.id, self.name, self.ra, self.dec)

    def view(self) -> dict:
        return {k: getattr(self, k) for k in ("id", "name", "start", "end", "hours", "status", "why", "real_start", "real_end",
                                              "frames", "stacked", "rejected", "substitute_for", "manual", "auto", "reason", "category")}


@dataclass
class Hold:
    kind: str        # twilight | weather | dew | battery | clouds | fog | control
    text: str


@dataclass
class Env:
    """Was der Autopilot von außen braucht (Dienste von AstroFlow; in Tests Attrappen)."""
    site: object = None                 # (night) -> {lat, lon, horizon, name} | None
    forecast: object = None             # (site, t0, t1) -> {"hours": [...]} | None; Ausnahme = kein Internet
    plan: object = None                 # (night, model) -> [Item]
    device: object = None               # (model) -> {gust, dew_heater, name}
    rain: object = None                 # (site, minutes) -> {rain: bool|None, in_minutes, probability, precip} | None
    sky_store: object = None            # skymap.SkyStore (Peilpunkte je Ort und Nacht) | None
    frame_points: object = None         # (site_id) -> {night: [Punkte]} aus früheren Aufnahmen (Bilder-Modul) | None
    autoplan: object = None             # (night, horizon[36], now, {max_targets, have_add}) -> {blocks, …} | None (Dienst night_autoplan)


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
        self.samples: deque = deque(maxlen=60)       # (frames, rejected, t) des aktiven Ziels
        self.bad_since: float | None = None          # seit wann das Fenster fast nur verworfene Bilder zeigt
        self.weather: dict | None = None
        self.weather_t = 0.0
        self.weather_offline = False
        self.rain: dict | None = None             # letzte Regenvorhersage {rain, in_minutes, probability, t}
        self.rain_t = 0.0
        self.good_since: float | None = None
        self.clouds_at: float | None = None
        self.fog = fog.FogWatch()                    # Messwerte je Rohbild (Beschlag-Erkennung, `fog.py`)
        self.fog_at: float | None = None
        self.park_pending: dict | None = None
        self.alarm: str | None = None
        self.estop = threading.Event()
        self.confirmed_night: str | None = None
        self.requires_confirm = False            # echtes Gerät: jede Nacht neu bestätigen, dass sich der Arm frei bewegen kann
        self.last_connect = 0.0
        self.lost_at: float | None = None
        self.warned: set = set()
        self.dark: dict | None = None
        self.frames_fetched = 0
        self.plan_note: str | None = None
        self.known: TelescopeState | None = None   # letzter Zustand MIT Verbindung (für Not-Aus ohne Verbindung)
        self.battery = BatteryEta()               # Restzeit aus dem gemessenen Akkuverlauf
        self.idle_since: float | None = None      # seit wann das Gerät beim aktiven Ziel nichts tut
        self.retry: dict[str, dict] = {}          # abgelehnte Befehle: {first, next, n, msg, busy} – Backoff statt Schleife
        self.phase_since: float | None = None     # seit wann die aktuelle Phase gilt (Restdauer)
        self.took: dict[str, float] = {}          # gemessene Dauer je Phase (ersetzt den Erfahrungswert)
        self.scan: skyscan.Scan | None = None     # laufender Himmelsscan
        self.last_scan: dict | None = None        # Ansicht des letzten Scans dieser Nacht
        self.scan_only = False                    # Scan ohne Plan gestartet → danach einklappen
        self.scan_auto_night: str | None = None   # Nacht, in der der automatische Kurzscan schon versucht wurde
        self.night_points: list[dict] = []        # Peilpunkte dieser Nacht (Scan, gute/verdeckte Ziele)
        self.free_t: dict[str, float] = {}        # Ziel → Zeit des letzten freien Punkts
        self.groups: list[dict] = []              # gelernte Aufstellungen des Ortes (ohne diese Nacht)
        self.site_wide: list = [None] * skymap.N
        self.setup: dict | None = None            # Aufstellung, zu der diese Nacht passt
        self._hz_cache: tuple | None = None
        self.auto: dict | None = None             # „Plane meine Nacht“: {night, state, deadline, replans, summary …}
        self.lock = threading.RLock()

    def adopt(self, old: "Autopilot"):
        """Neuer Autopilot nach geänderten Einstellungen/Treiber (`Module.rebuild`): von Hand hinzugefügte Ziele, gesperrte
        Richtungen, offene Horizont-Vorschläge und das Protokoll übernehmen – sonst gingen sie beim Speichern verloren."""
        with old.lock:
            keep = old.auto is not None and old.auto.get("state") == "entwurf"      # Entwurf von „Plane meine Nacht“ behalten
            self.items = [Item(**vars(i)) for i in old.items if (i.manual or (keep and i.auto)) and i.status == "geplant"]
            self.auto = dict(old.auto) if keep else None
            self.night = old.night if keep else None
            self.blocked = [dict(b) for b in old.blocked]
            self.night_points = [dict(p) for p in old.night_points]
            self.last_scan, self.scan_auto_night = old.last_scan, old.scan_auto_night
            self.proposals = [dict(q) for q in old.proposals]
            self.events = deque(old.events, maxlen=MAX_EVENTS)
            self.seq = old.seq

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
    def confirmed(self, now: float) -> bool:
        """Bewegung für die *aktuelle* Nacht freigegeben? (Simulator: immer)"""
        return not self.requires_confirm or self.confirmed_night == self.current_night(now)

    def confirm(self, now: float):
        self.confirmed_night = self.current_night(now)

    def _cmd(self, what: str, fn, *a, now=None) -> bool:
        if self.estop.is_set() and what != "park":
            return False
        if what in MOVES and not self.confirmed(self._now() if now is None else now):
            # Die eine Stelle, an der die Bestätigung je Nacht durchgesetzt wird: kein Öffnen/Ausrichten/GoTo ohne Freigabe für
            # diese Nacht – egal ob über Start, Weiter, Takt nach Nachtwechsel oder Wiederöffnen nach Wetter.
            if self.mode in ("laeuft", "wartet"):
                self.mode = "pausiert"
                self.log("Für diese Nacht fehlt die Bestätigung, dass sich der Arm frei bewegen kann – angehalten. Bitte „Weiter“ und bestätigen.", "warn", now)
            return False
        if what in MOVES and (self.park_pending or self.mode not in ("laeuft", "wartet")):
            return False                          # angehalten oder Einklappen läuft (z. B. Zeitgrenze im selben Takt): keine Bewegung
        if not self.machine.can(what):
            return False
        if what in ("park", "stop"):             # Einklappen/Stoppen nie verzögern (Einklappen wiederholt _park_watch)
            try:
                fn(*a)
                return True
            except DriverError as e:
                self.log(f"{_label(what)} fehlgeschlagen: {e}", "bad", now)
                return False
        t = self._now() if now is None else now
        r = self.retry.get(what)
        if r and t < r["next"]:
            return False                          # abgelehnt – nächster Versuch erst nach der Wartezeit
        try:
            fn(*a)
        except DriverError as e:
            return self._rejected(what, e, t)
        if r:
            self.log(f"{_label(what)}: jetzt angenommen.", "ok", now)
        self.retry.pop(what, None)
        return True

    def _rejected(self, what: str, e: DriverError, now: float) -> bool:
        """Abgelehnter Befehl: kein roter Fehler je Takt, sondern ein Eintrag und Backoff (10 s, 20 s … höchstens 2 min).
        „Beschäftigt“ (das Gerät öffnet oder richtet sich selbst ein) ist kein Fehler. Erst nach `GIVE_UP` ein echter Fehler:
        dann sicher reagieren – einklappen und anhalten, der Nutzer entscheidet."""
        busy = isinstance(e, DriverBusy)
        r = self.retry.setdefault(what, {"first": now, "n": 0, "msg": None})
        r["n"] += 1
        wait = min(RETRY_MAX, RETRY_FIRST * 2 ** (r["n"] - 1))
        r["next"], r["busy"] = now + wait, busy
        limit = GIVE_UP.get(what)
        if limit and now - r["first"] >= limit:
            self.retry.pop(what, None)
            self.log(f"{_label(what)} klappt seit {round((now - r['first']) / 60)} min nicht ({e}) – klappe ein und halte an.", "bad", now)
            if self.mode in ("laeuft", "wartet"):
                self.mode = "pausiert"
            if not self.state.parked:
                self._park_now(now, f"{_label(what)} klappt nicht")
            return False
        if str(e) != r["msg"]:                    # nur bei neuer Meldung ins Protokoll, nicht bei jeder Wiederholung
            r["msg"] = str(e)
            if busy:
                self.log(f"Teleskop ist noch beschäftigt – {_label(what)} folgt, sobald es fertig ist.", "info", now)
            else:
                self.log(f"{_label(what)} vom Teleskop abgelehnt: {e} Neuer Versuch gleich, höchstens {round(limit / 60) if limit else '–'} min lang.", "warn", now)
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
        if self.auto and self.auto.get("night") == self.night:
            got = PlanList(i for i in self.items if not i.manual)     # „Plane meine Nacht“ läuft: eigener Plan bleibt
        else:
            self.auto = None                                          # neue Nacht: automatischer Plan gilt nicht mehr
            got = self.env.plan(self.night, self.state.model or "") if self.env.plan else None
        got = [] if got is None else got                 # leere PlanList behalten (trägt den Hinweis)
        self.plan_note = getattr(got, "note", None)
        items = list(got)
        manual = [i for i in self.items if i.manual and i.status == "geplant"]
        self.items = sorted(items + manual, key=lambda i: i.start)
        self.blocked, self.warned = [], set()
        self.dark = self._dark_window(now) if self.site else None
        store = self.env.sky_store
        self.night_points = (store.nights(self._site_id()).get(self.night) or []) if store else []
        self.last_scan, self.setup = None, None
        self.learn(now)

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
            self.retry = {}
            self.scan_only = False               # ab jetzt mit Plan (ein laufender Scan wird danach fortgesetzt)
            self.fog_at = None                   # Nutzer hat die Optik geprüft
            self.log("Autopilot gestartet." + (f" {len(self.items)} Ziele für diese Nacht." if self.items else " Noch keine Ziele – im Planer planen oder Ziele hinzufügen."), "info", now)

    def pause(self, now: float):
        with self.lock:
            if self.mode in ("laeuft", "wartet"):
                self.mode = "pausiert"
                cur = self._current()
                if cur:
                    self._end_item(cur, "geplant", "pausiert", now)
                self._scan_abort("pausiert", now)
                self.scan_only = False
                self._cmd("stop", self.driver.stop, now=now)
                self.log("Pausiert – das Teleskop bleibt offen und steht still.", "info", now)

    def resume(self, now: float):
        with self.lock:
            if self.mode == "pausiert":
                self.mode = "laeuft"
                self.fog_at = None
                self.log("Weiter geht's.", "info", now)

    def emergency(self, now: float, why: str = "Sofort einklappen"):
        """Not-Aus: ohne Warteschlange, ohne auf den Takt zu warten. Stoppt alles und klappt ein."""
        self.estop.set()
        self.mode = "aus"
        if self.auto and self.auto.get("deadline"):          # „gleich starten“: Frist verfällt, Start nur noch per Klick
            self.auto["deadline"] = None
        self._scan_abort(why, now)
        self.scan_only = False
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
        if not self.state.connected:
            # Ohne Verbindung lässt sich Einklappen weder senden noch bestätigen. War das Teleskop zuletzt eingeklappt (oder nie
            # verbunden), gibt es nichts zu tun; stand es zuletzt offen, sofort deutlich warnen statt Minuten später.
            if self.known is None or self.known.parked:
                self.log("Keine Verbindung – laut letztem Stand ist das Teleskop eingeklappt." if self.known else
                         "Keine Verbindung zum Teleskop – es wurde von AstroFlow nicht bewegt.", "info", now)
                return
            self.alarm = "Keine Verbindung zum Teleskop – es stand zuletzt offen. Bitte sofort von Hand prüfen und einklappen!"
            self.log(self.alarm, "bad", now)
            return
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
            a = self.auto
            if a and a["state"] == "entwurf" and a.get("deadline") and time.time() >= a["deadline"] and not self.estop.is_set():
                self.night_go(now)                # „gleich starten“: Frist abgelaufen, niemand hat abgebrochen
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
        if s.connected:
            self.known = s
            self.battery.update(now, s.battery, s.charging, not s.parked)
        ch = self.machine.update(s)
        if ch:
            if ch[0] in EXPECT_S and self.phase_since is not None and ch[1] not in ("getrennt", "fremd"):
                self.took[ch[0]] = now - self.phase_since          # Erfahrungswert für die nächste Restdauer
            self.phase_since = now
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
            self._refresh_rain(now)
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
        self._refresh_rain(now)
        self._track(s, now)
        holds = self.conditions(now, s)
        if holds:
            return self._hold(holds[0], now)
        if self.mode == "wartet":
            if not self._may_reopen(now):
                return
            was = self.hold
            self.mode, self.hold = "laeuft", None
            self.log("Bedingungen wieder gut – es geht weiter.", "ok", now)
            if was and was.kind not in ("twilight", "dawn"):
                self._replan(now, "nach der Pause", force=True)
        self._run(s, now)

    def _take(self, now):
        try:
            self.driver.take_control()
        except DriverError as e:
            self.log(f"Kontrolle übernehmen fehlgeschlagen: {e}", "bad", now)

    def _offline(self, prev: TelescopeState, now: float):
        if self.lost_at is None and self.mode in ("laeuft", "wartet", "pausiert"):
            self.lost_at = now
            # nur melden, wenn wirklich ein Plan im Gerät läuft (Treiber ohne `device_plan`: nur die eine Beobachtung)
            fb = prev.device_plan or (self.cfg["fallback"] and "device_plan" in getattr(self.driver, "capabilities", frozenset())
                                      and any(i.status == "aktiv" for i in self.items))
            self.log("Verbindung zum Teleskop verloren." + (" Der Plan im Gerät arbeitet weiter und klappt am Ende ein." if fb else ""), "bad", now)
        if self.mode in ("laeuft", "wartet") and now - self.last_connect >= RECONNECT_EVERY:
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
            if s.connected and s.master == "none":            # nur freie Kontrolle nehmen, nie der App wegnehmen
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

    def _refresh_rain(self, now: float):
        """Regen in der nächsten Stunde? Dienst `weather_rain` (Bilder-Modul); fehlt er oder ist nichts bekannt → wie bisher."""
        if not self.env.rain or not self.site or now - self.rain_t < RAIN_EVERY:
            return
        self.rain_t = now
        try:
            r = self.env.rain(self.site, RAIN_START_MIN)
        except Exception:
            r = None
        self.rain = {**r, "t": now} if r and r.get("rain") is not None else None

    def rain_in(self, now: float) -> float | None:
        """Minuten bis zum erwarteten Regen (0 = jetzt) oder None (kein Regen erwartet/unbekannt/Vorhersage zu alt)."""
        r = self.rain
        if not r or not r.get("rain") or r.get("in_minutes") is None or now - r["t"] > 3 * RAIN_EVERY:
            return None
        return max(0.0, float(r["in_minutes"]) - (now - r["t"]) / 60)

    def _rain_text(self, m: float) -> str:
        p = (self.rain or {}).get("probability")
        when = "jetzt" if m < 5 else f"in ca. {round(m)} min"
        return f"Regen {when} laut Vorhersage" + (f" ({round(p)} %)" if p is not None else "")

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
                until = f" (ab {_fmt(self.dark['start'])})" if not rising and self.dark and self.dark["start"] > now else ""
                out.append(Hold("dawn" if rising else "twilight",
                                "Morgendämmerung – die Nacht ist vorbei." if rising else f"Noch zu hell – warte auf die Dunkelheit{until}."))
        h = self.weather_now(now)
        if h:
            if h.get("cloud") is not None and h["cloud"] > self.cfg["cloud_max"]:
                out.append(Hold("weather", f"Wolken {round(h['cloud'])} % laut Vorhersage (Grenze {self.cfg['cloud_max']} %)."))
            if h.get("wind") is not None and h["wind"] > self.cfg["wind_max"]:
                out.append(Hold("weather", f"Wind {round(h['wind'])} km/h laut Vorhersage (Grenze {self.cfg['wind_max']} km/h)."))
            lim = self.dew_limit(s.model)
            if h.get("dew_spread") is not None and h["dew_spread"] < lim:
                out.append(Hold("dew", f"Taugefahr: Taupunktabstand {h['dew_spread']:.1f} °C (Grenze {lim:.1f} °C)."))
        rain = self.rain_in(now)
        if rain is not None:
            # läuft eine Beobachtung, bleibt sie bis kurz vor dem Regen; eingeklappt wird erst gar nicht geöffnet
            observing = self._current() is not None and not s.parked
            if rain <= (RAIN_PARK_MIN if observing else RAIN_START_MIN):
                out.append(Hold("rain", f"{self._rain_text(rain)} – Teleskop wird eingeklappt."))
        if self.fog_at is not None and now - self.fog_at < FOG_PAUSE_S:
            out.append(Hold("fog", "Optik vermutlich beschlagen – Teleskop bleibt eingeklappt. Optik prüfen und mit „Weiter“ "
                                   f"fortsetzen, sonst neuer Versuch nach {FOG_PAUSE_S // 60} min."))
        if self.clouds_at is not None and now - self.clouds_at < self.cfg["reopen_min"] * 60:
            out.append(Hold("clouds", "Wolken im Bild gemessen (mehrere Richtungen ohne Sterne)."))
        if s.battery is not None and not s.charging and s.battery < self.cfg["battery_min"]:
            out.append(Hold("battery", f"Akku nur noch {s.battery} % – Teleskop wird eingeklappt."))
        return out

    def dew_limit(self, model: str) -> float:
        return float(self.cfg["dew_min_stellina"] if (model or "").startswith("stellina") else self.cfg["dew_min"])

    def _hold(self, h: Hold, now: float):
        final = h.kind in ("dawn", "battery")
        only = self.scan_only
        self._scan_abort(h.text, now)                 # Regen, Beschlag, Akku, Wetter … haben Vorrang vor dem Himmelsscan
        if self.hold is None or self.hold.kind != h.kind or self.mode != "wartet":
            self.log(h.text, "warn" if h.kind in ("twilight",) else "bad", now)
        self.hold, self.good_since = h, None
        auto_on = bool(self.auto and self.auto["state"] in ("vermessen", "laeuft"))
        self.mode = "fertig" if final and (self._started_tonight() or auto_on) else "wartet"
        if final and self.mode == "fertig":
            cur = self._current()
            if cur:                                   # laufendes Ziel endet mit der Nacht (nicht wieder „geplant“)
                self._end_item(cur, "fertig" if cur.stacked or self.state.stacked else "abgebrochen", "", now)
            for i in self.items:
                if i.status == "geplant":
                    i.status, i.why = "uebersprungen", "Nacht vorbei" if h.kind == "dawn" else "Akku"
            self.log("Nacht beendet." if h.kind == "dawn" else "Abgebrochen wegen Akku.", "info", now)
            self._finish_auto(now)
        if not self.state.parked and not self.park_pending:
            self._cmd("stop", self.driver.stop, now=now)
            self._park_now(now, h.text)
        if only:                                      # Scan ohne Plan: nicht auf besseres Wetter warten
            self.scan_only = False
            self.mode = "aus"

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
            if not self.scan and not self._anything_free(now) and not self._current() and not self._auto_measuring():
                return self._idle_or_done(now)
            if self._cmd("open", self.driver.open, now=now):
                self.log("Öffne das Teleskop.", "move", now)
            return
        if ph == "geoeffnet":
            if self.site and self._cmd("init", self.driver.init, self.site["lat"], self.site["lon"], now=now):
                self.log("Richte das Teleskop aus (Sterne, Fokus).", "move", now)
            return
        if self.scan is None and not self._current() and self._auto_measuring() and self._auto_measure(now):
            return
        if self.scan is None and not self._current() and self._auto_scan_due(now):
            self.start_scan(now, "kurz", auto=True)
        if self.scan is not None:
            return self._scan_tick(s, now)
        cur = self._current()
        if cur:
            working = s.step in ("goto", "astrometry", "capture") or s.device_plan
            if working and (not s.target or s.target.get("id") == cur.id):
                cur.seen = True
            self.idle_since = None if working else (self.idle_since or now)
            if s.target and s.target.get("id") != cur.id and not s.device_plan:
                self._end_item(cur, "abgebrochen", "anderes Ziel am Gerät", now)
            elif (fg := self._fogged(cur, now)) is not None:
                return self._fog_end(cur, fg, now)
            elif self._failed(cur, s, now):
                return self._failure(cur, s, now)
            elif self.idle_since is not None and now - self.idle_since >= (STALL_S if cur.seen else 2 * STALL_S):
                return self._stalled(cur, s, now)
            elif not (vis := self.free(cur.ra, cur.dec, now))[0]:
                # Ziel verlässt den freien Bereich (Mindesthöhe, Horizontmaske, gesperrte Richtung): sauber beenden, weiter
                self._cmd("stop", self.driver.stop, now=now)
                self._end_item(cur, "fertig" if cur.stacked else "uebersprungen", vis[1], now)
                self.log(f"{cur.name} verlässt den freien Bereich ({vis[1]}) – beende und wechsle zum nächsten Ziel.", "warn", now)
                self._replan(now, f"{cur.name} verlässt den freien Bereich")
            elif (s.device_plan and now >= cur.end - RAIN_PLAN_GUARD_S and (rain := self.rain_in(now)) is not None
                  and rain <= RAIN_START_MIN):
                # der Geräte-Plan schwenkt am Blockende selbst zum nächsten Ziel – vorher einklappen (wie `_start_item`)
                self._end_item(cur, "fertig", "", now)
                return self._hold(Hold("rain", f"{self._rain_text(rain)} – kein Zielwechsel mehr, Teleskop wird eingeklappt."), now)
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
        a = self.auto
        if a and a["state"] == "laeuft" and self.dark and self.dark["end"] - now >= REFILL_MIN_S \
                and now - a.get("refill", 0.0) >= REFILL_MIN_S:
            # Plan früh erledigt (Bedarf gedeckt, Ziele ausgefallen): Rest der Nacht neu füllen statt einzuklappen
            a["refill"] = now
            if self._replan(now, "Plan erledigt, die Nacht ist noch lang", force=True) and self._anything_free(now):
                return
        if not self.items and not a:
            return
        if not self.state.parked and not self.park_pending:
            self.log("Alle Ziele dieser Nacht erledigt – klappe ein." if self.items else "Keine passenden Ziele für diese Nacht – klappe ein.",
                     "ok", now)
            self._park_now(now, "Plan erledigt")
        self.mode = "fertig"
        self._finish_auto(now)

    def free(self, ra: float, dec: float, now: float) -> tuple[bool, str, float, float]:
        """(frei, Grund, Azimut, Höhe) – Regel wie Planer/Heute Nacht (max(Mindesthöhe, Horizontmaske)) + gesperrte Richtungen."""
        if not self.site:
            return False, "kein Standort", 0.0, 0.0
        az, alt = sky.altaz(ra, dec, self.site["lat"], self.site["lon"], now)
        hz = self.horizon()
        lim = sky.limit(az, hz["horizon"], float(self.cfg["min_alt"]))
        if alt < lim:
            return False, f"zu tief ({alt:.0f}° unter {lim:.0f}°)", az, alt
        top = hz["top"][int(round((az % 360) / skymap.STEP)) % skymap.N]
        if top is not None and alt >= top:
            return False, f"Richtung heute verdeckt (Überhang ab {top:.0f}°)", az, alt
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

    def battery_left(self, active: bool = True) -> float | None:
        """Sekunden bis zur Akkugrenze `battery_min` (None: kein Akkuwert oder lädt)."""
        s = self.state
        return self.battery.seconds_left(s.battery, s.model, active, float(self.cfg["battery_min"]))

    def _battery_fits(self, it: Item, now: float) -> bool:
        """Reicht der Akku für das Ziel? Zu knapp (auch nach Erfahrungswert) → nicht starten; reicht er laut Messung nicht bis
        zum Ende → Abschnitt kürzen und sagen."""
        left = self.battery_left(True)
        if left is None:
            return True
        if left < BATTERY_MIN_ITEM:
            self._once(f"akku-leer:{it.id}", f"Akku reicht nur noch ca. {fmt_left(left)} – starte {it.name} nicht mehr.", "warn", now)
            if self.machine.phase in ("schwenkt", "astrometrie", "nimmt_auf"):
                self._cmd("stop", self.driver.stop, now=now)     # vorige Beobachtung nicht unbeobachtet weiterlaufen lassen
            return False
        if now + left < it.end and self.battery.measured and self.battery.active:
            # kürzen nur mit gemessener Rate im offenen Betrieb – der Erfahrungswert ist dafür zu grob
            it.end = now + left
            self.log(f"Akku reicht nur noch ca. {fmt_left(left)} – {it.name} wird bis {_fmt(it.end)} gekürzt.", "warn", now)
        return True

    def _start_item(self, it: Item, now: float):
        rain = self.rain_in(now)
        if rain is not None and rain <= RAIN_START_MIN:      # kein neues Ziel kurz vor dem Regen – lieber gleich einklappen
            self._hold(Hold("rain", f"{self._rain_text(rain)} – starte {it.name} nicht mehr, Teleskop wird eingeklappt."), now)
            return
        if not self._battery_fits(it, now):
            return
        self.idle_since = None
        it.seen, it.restarts = False, 0
        it.status, it.real_start, it.why = "aktiv", now, ""
        it.frames = it.stacked = it.rejected = 0
        self.samples.clear()
        self.bad_since = None
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
            if now - self.free_t.get(cur.id, 0.0) >= FREE_EVERY:      # Himmelskarte: hier waren Sterne
                self.free_t[cur.id] = now
                self._add_point({"az": round(s.az, 1), "alt": round(s.alt, 1), "res": "frei", "sure": True, "src": "ziel", "t": now})
        cur.frames, cur.stacked, cur.rejected = s.frames, s.stacked, s.rejected
        if not self.samples or self.samples[-1][0] != s.frames:
            self.samples.append((s.frames, s.rejected, now))
        pull = getattr(self.driver, "frame_metrics", None)       # Simulator liefert Messwerte je Bild selbst
        if callable(pull):
            for t, m in pull():
                self.fog.add(t, m)

    def frame_seen(self, t: float, m: dict, item: Item | None = None, start: float | None = None) -> bool:
        """Messwerte eines gerade geholten Rohbilds (Live-Bilder, Dienst `frame_quick_metrics`). Mit `item`/`start` nur, wenn
        dieser Versuch noch läuft; False = verworfen (Ziel inzwischen gewechselt)."""
        with self.lock:
            if item is not None and (self._current() is not item or item.real_start != start):
                return False
            self.fog.add(t, m)
            return True

    def _fogged(self, it: Item, now: float) -> dict | None:
        """Beschlag am laufenden Ziel? Nur bei bekannter klarer Vorhersage (Wolkenwert vorhanden und unter CLOUDY) – sonst
        sind es eher Wolken bzw. es lässt sich nicht unterscheiden; dann kein Abbruch (übrige Wächter)."""
        r = self.fog.check(now, it.real_start or 0.0)
        if r is None:
            return None
        h = self.weather_now(now)
        if h is None or h.get("cloud") is None:
            self._once(f"fog-unknown-{it.id}", f"{it.name}: Bilder werden stetig schlechter – ohne aktuelle Wettervorhersage "
                       "lässt sich Beschlag nicht von Wolken unterscheiden, das Ziel läuft weiter.", "info", now)
            return None
        if h["cloud"] >= CLOUDY:
            self._once(f"fog-cloudy-{it.id}", f"{it.name}: Bilder werden stetig schlechter, laut Vorhersage aber bewölkt – "
                       "eher Wolken als beschlagene Optik.", "info", now)
            return None
        r["dew_spread"] = h.get("dew_spread")
        return r

    def _fog_end(self, it: Item, r: dict, now: float):
        """Optik beschlagen: Protokoll und Warnung, Ziel beenden (statt weiter unbrauchbare Bilder zu sammeln), einklappen."""
        hint = (f" Taupunktabstand laut Vorhersage {r['dew_spread']:.1f} °C.".replace(".", ",", 1)
                if r.get("dew_spread") is not None else "")
        self.log(fog.describe(it.name, r) + " bei klarer Vorhersage." + hint + " Ziel beendet – weitere Bilder wären unbrauchbar.",
                 "bad", now)
        self._cmd("stop", self.driver.stop, now=now)
        self._end_item(it, "abgebrochen", "Optik beschlagen?", now)
        self.fog_at = now
        self.fog.clear()
        self.samples.clear()
        self.bad_since = None
        h = self.conditions(now)
        return self._hold(next(x for x in h if x.kind == "fog"), now)

    def _failed(self, it: Item, s: TelescopeState, now: float) -> bool:
        if s.target and s.target.get("id") != it.id:
            return False
        if s.astrometry_failed:
            return it.seen                # nur, wenn das Gerät an *diesem* Versuch gearbeitet hat (kein alter Fehler)
        def share(n):                     # Anteil verworfener Bilder unter den letzten n (None: noch zu wenige)
            base = next((x for x in reversed(self.samples) if x[0] <= s.frames - n), None) if s.frames >= n else None
            return None if base is None else (s.rejected - base[1]) / max(1, s.frames - base[0])
        short, long_ = share(WINDOW_FRAMES), share(LONG_FRAMES)
        if not ((short or 0) >= BAD_RATIO or (long_ or 0) >= LONG_BAD):
            # Hysterese: die Wartezeit beginnt erst neu, wenn es wirklich besser ist – sonst rutscht ein Hindernis, das um
            # die Schwelle pendelt (zwischendurch gute Bilder), immer wieder durch
            if self.bad_since is not None and (short or 0) < LONG_BAD and (long_ is None or long_ < LONG_OK):
                self.log(f"{it.name}: wieder brauchbare Bilder.", "info", now)
                self.bad_since = None
            return False
        if s.rejected / max(1, s.frames) >= TOTAL_BAD:
            return True
        if self.bad_since is None:
            self.bad_since = now
            self.log(f"{it.name}: viele verworfene Bilder – Wolke oder Hindernis, nehme noch {BAD_GRACE_S // 60} min weiter auf.",
                     "info", now)
        return now - self.bad_since >= BAD_GRACE_S

    def _stalled(self, it: Item, s: TelescopeState, now: float):
        """Ziel aktiv, aber das Gerät tut seit `STALL_S` nichts (Beobachtung selbst beendet oder nie begonnen): nicht endlos
        warten. Astrometrie gescheitert → wie verdeckt; sonst einmal neu starten (bleibt genug Zeit), danach fertig (mit
        gestapelten Bildern) bzw. abbrechen und weiter."""
        self.idle_since = None
        reason = f" ({s.error})" if s.error else ""
        if s.astrometry_failed:
            return self._failure(it, s, now)
        if it.restarts < 1 and it.end - now > 10 * 60:
            it.restarts += 1
            it.seen = False
            self.log(f"{it.name}: Die Beobachtung läuft am Teleskop nicht{reason} – starte sie einmal neu.", "warn", now)
            self._cmd("observe", self.driver.observe, it.target, float(self.cfg["exposure"]), now=now)
            return
        elif it.stacked:
            self._end_item(it, "fertig", "Gerät hat die Beobachtung beendet", now)
            self.log(f"Das Teleskop hat {it.name} selbst beendet{reason} – weiter mit dem nächsten Ziel.", "info", now)
        else:
            self._end_item(it, "abgebrochen", "Beobachtung lief am Gerät nicht", now)
            self.log(f"{it.name}: Die Beobachtung startet am Teleskop nicht{reason} – überspringe das Ziel.", "warn", now)
        nxt = self._next_free(now)
        if nxt:
            self._start_item(nxt, now)

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
        cloudy = h is not None and h.get("cloud") is not None and h["cloud"] >= CLOUDY
        self.bad_since = None
        self.samples.clear()                         # die Messung galt dieser Richtung, nicht dem Himmel (→ `sky_now`)
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
        # „Sicher“ ein Hindernis erst, wenn dieselbe Richtung in einem früheren, getrennten Versuch schon gescheitert ist
        # (unsichere Sperren laufen nach OK_MEMORY ab → neuer Versuch) und andere Richtungen klar sind; nie bei Wolken laut
        # Wetterdaten. Nur dann ein Vorschlag für die Horizontmaske – ein einzelnes Scheitern sperrt nur vorläufig.
        again = any(sky.ang_dist(b["az"], b["alt"], az, alt) <= BLOCK_AZ and now - b["t"] > OK_MEMORY for b in self.blocked) \
            or any(abs((q["az"] - az + 540) % 360 - 180) <= BLOCK_AZ and now - q["t"] > OK_MEMORY for q in self.proposals)
        sure = again and (elsewhere_ok or clear_forecast) and not cloudy
        self._end_item(it, "verdeckt", why, now)
        mark = {"az": round(az, 1), "alt": round(alt, 1), "t": now, "target": it.name, "sure": sure}
        if not cloudy:                                # fürs Lernen: Hindernis erst, wenn es sich in einer anderen Nacht wiederholt
            self._add_point({"az": mark["az"], "alt": mark["alt"], "res": "verdeckt", "sure": False, "src": "ziel", "t": now})
        self.blocked.append(mark)
        if sure:
            near = next((q for q in self.proposals if abs((q["az"] - mark["az"] + 540) % 360 - 180) <= BLOCK_AZ), None)
            if near:                                 # dieselbe Richtung: einen Vorschlag mit der höchsten Höhe behalten
                near.update({k: mark[k] for k in ("az", "alt", "t", "target")} if mark["alt"] >= near["alt"] else {"t": mark["t"]})
                near["sure"] = True
            else:
                self.proposals.append(dict(mark))
        if sure:
            tail = "dieselbe Richtung scheitert erneut, andere sind klar – also ein Hindernis."
        elif cloudy:
            tail = "laut Wetterdaten bewölkt, also eher Wolken – versuche es später noch einmal."
        else:
            tail = f"vielleicht ein Hindernis, vielleicht eine Wolke – neuer Versuch in {OK_MEMORY // 60} min."
        self.log(f"{it.name} verdeckt ({why}) bei Azimut {az:.0f}°, Höhe {alt:.0f}° – {tail}"
                 + " Weiter mit dem nächsten freien Ziel.", "warn", now)
        self._replan(now, f"{it.name} verdeckt", force=True)
        nxt = self._next_free(now)
        if nxt:
            self._start_item(nxt, now)

    def sky_now(self, now: float) -> dict | None:
        """Was das Teleskop gerade über den Himmel misst (Dienst `telescope_sky`): Anteil guter Bilder in den letzten
        `WINDOW_FRAMES` Bildern des laufenden Ziels, bzw. „bewölkt“, wenn der Autopilot eben Wolken erkannt hat.
        None ohne frische Messung (älter als `SKY_FRESH_S`, zu wenig Bilder, getrennt/eingeklappt) oder nach einem
        Hindernis (die verworfenen Bilder gehören dann zur Richtung, nicht zum Himmel)."""
        with self.lock:
            if self.clouds_at is not None and now - self.clouds_at <= SKY_FRESH_S:
                return {"clear": False, "ratio": 0.0, "t": self.clouds_at, "why": "Wolken erkannt"}
            s = self.state
            if not s.connected or s.parked or not self.samples or self._current() is None:
                return None
            f1, r1, t1 = self.samples[-1]
            if now - t1 > SKY_FRESH_S:
                return None
            f0, r0, _ = next((x for x in reversed(self.samples) if x[0] <= f1 - WINDOW_FRAMES), self.samples[0])
            if f1 - f0 < SKY_MIN_FRAMES:
                return None
            ratio = round(1.0 - (r1 - r0) / (f1 - f0), 2)
            return {"clear": ratio >= SKY_CLEAR, "ratio": ratio, "t": t1, "why": "Anteil guter Bilder"}

    # --------------------------------------------------------- Freier Himmel --- #
    def _site_id(self) -> str | None:
        return (self.site or {}).get("id")

    def _add_point(self, p: dict):
        self.night_points.append(p)
        del self.night_points[:-skymap.MAX_POINTS]
        self._hz_cache = None
        self._persist()

    def _persist(self):
        store = self.env.sky_store
        if store and self.night and self.site:
            try:
                store.put_night(self._site_id(), self.night, self.night_points)
            except Exception:
                import traceback
                traceback.print_exc()

    def learn(self, now: float):
        """Aufstellungen des Ortes aus gespeicherten Nächten (Scans, Ziele) und früheren Aufnahmen (Dienst `frame_sky_points`)
        neu bilden – ohne diese Nacht, die wird an ihnen gemessen."""
        sid = self._site_id()
        nights: dict[str, list] = {}
        if self.env.frame_points and self.site:
            try:
                for k, v in (self.env.frame_points(sid) or {}).items():
                    nights.setdefault(k, []).extend(v)
            except Exception:
                import traceback
                traceback.print_exc()
        if self.env.sky_store:
            for k, v in self.env.sky_store.nights(sid).items():
                nights.setdefault(k, []).extend(v)
        nights.pop(self.night, None)
        self.groups = skymap.setups(nights)
        self.site_wide = skymap.site_wide(self.groups)
        self.setup = self._which_setup()
        self._hz_cache = None

    def _which_setup(self) -> dict | None:
        # nur Bestätigtes: frei bzw. sicher verdeckt – ein unsicherer Punkt (Wolke?) darf keine alten Hindernisse zurückholen
        pts = self._tonight_points()
        return skymap.match(self.groups, pts) if pts and self.groups else None

    def _tonight_points(self) -> list[dict]:
        """Punkte, die heute die Grenze bestimmen: freie Punkte und sicher verdeckte Scan-Punkte (Gegenprobe davor und danach frei).
        Unsicher verdeckte (Wolke?) und verdeckte Ziele (sperren über `blocked`) zählen erst beim Lernen über mehrere Nächte."""
        return [p for p in self.night_points if p.get("res") == "frei" or (p.get("src") == "scan" and p.get("res") == "verdeckt" and p.get("sure"))]

    def horizon(self) -> dict:
        """Grenze dieser Nacht: Ortsmaske + standortweit gelernte Grenzen, angepasst durch die erkannte Aufstellung und die
        heutigen Messungen ({horizon, top, known, cols}). Ohne Messung = Ortsmaske."""
        base = list((self.site or {}).get("horizon") or [0.0] * skymap.N)
        sig = (tuple(base), len(self.night_points), self.setup["id"] if self.setup else None, tuple(self.site_wide))
        if self._hz_cache and self._hz_cache[0] == sig:
            return self._hz_cache[1]
        pts = self._tonight_points()
        base = [max(float(b or 0), w) if w is not None else float(b or 0) for b, w in zip(base, self.site_wide)]
        prior = []
        if self.setup:                                # gelernte Punkte, wo heute nicht gemessen wurde
            prior = [p for p in self.setup["points"]
                     if not any(skymap._d(p["az"], q["az"]) <= skymap.DIRECT for q in pts if q.get("src") == "scan")]
        if prior or pts:
            out = skymap.horizon(prior + pts, base)
        else:
            out = {"horizon": base, "top": [None] * skymap.N, "known": [False] * skymap.N, "cols": skymap.columns([])}
        self._hz_cache = (sig, out)
        return out

    def measured(self) -> bool:
        return bool(self._tonight_points() or self.setup)

    def _avoid(self, now: float):
        lat, lon = self.site["lat"], self.site["lon"]
        maz, malt = sky.moon_altaz(now, lat, lon)

        def check(az, alt):
            if malt > -5 and sky.ang_dist(az, alt, maz, malt) < skyscan.MOON_AVOID:
                return "zu nah am Mond"
            return None
        return check

    def _auto_scan_due(self, now: float) -> bool:
        """Automatischer Kurzscan einmal je Nacht – nur mit gelernten Aufstellungen und ohne Messung dieser Nacht."""
        if not self.cfg.get("scan_auto") or self.scan_auto_night == self.night or not self.groups or self.scan_only:
            return False
        if any(p.get("src") == "scan" for p in self.night_points):
            return False
        self.scan_auto_night = self.night
        return True

    def start_scan(self, now: float, kind: str = "voll", auto: bool = False) -> str | None:
        """Himmel vermessen (Knopf oder automatisch). Rückgabe: Grund, warum nicht (sonst None)."""
        with self.lock:
            if self.scan is not None:
                return "Der Himmel wird schon vermessen."
            if not self.site:
                if self.night is None:
                    self.load_night(now)
                if not self.site:
                    return "Für diese Nacht ist kein Standort bekannt."
            holds = [x for x in self.conditions(now) if x.kind != "clouds"]
            if holds:
                return holds[0].text
            h = self.weather_now(now)
            if h and h.get("cloud") is not None and h["cloud"] >= CLOUDY:
                msg = f"Laut Vorhersage {round(h['cloud'])} % Wolken – vermesse den Himmel lieber in einer klaren Nacht."
                if auto:
                    self.log(msg, "info", now)
                return msg
            if kind == "kurz" and not self.groups:
                kind = "voll"
            self.scan = skyscan.Scan(kind, now, groups=self.groups, need=float(self.cfg["min_alt"]))
            cur = self._current()
            if cur:
                self._end_item(cur, "geplant", "Himmel vermessen", now)
                self._cmd("stop", self.driver.stop, now=now)
            if self.mode not in ("laeuft", "wartet"):
                self.scan_only = True
                if self.night is None or self.night != self.current_night(now):
                    self.load_night(now)
                self.mode, self.hold = "laeuft", None
                self.estop.clear()
                self.alarm = None
                self.retry = {}
            if self.scan.kind == "kurz":
                self.log("Erkenne die Aufstellung an wenigen Peilpunkten, dann Kurzscan an den Grenzen.", "info", now)
            else:
                self.log(f"Vermesse den freien Himmel: Peilpunkte alle {skyscan.COL_STEP}° Azimut ab {skyscan.SCAN_FLOOR:.0f}° Höhe "
                         "(etwa 10–20 min).", "info", now)
            return None

    def stop_scan(self, now: float):
        with self.lock:
            if self.scan:
                self._scan_abort("vom Nutzer beendet", now)
                self._cmd("stop", self.driver.stop, now=now)
                if self.scan_only:
                    self._scan_finish_only(now)

    def _scan_abort(self, why: str, now: float):
        sc = self.scan
        if sc is None:
            return
        sc.abort(why)
        self._scan_store(now)
        self.log(f"Himmelsscan abgebrochen ({why}) – {len(sc.points())} Punkte bleiben erhalten.", "warn", now)

    def _scan_store(self, now: float):
        """Ergebnis übernehmen: Punkte in die Nacht, Aufstellung bestimmen, speichern."""
        sc = self.scan
        self.scan = None
        if sc is None:
            return
        pts = sc.points()
        self.night_points = [p for p in self.night_points if not (p.get("src") == "scan" and any(
            skymap._d(p["az"], q["az"]) < 1 and abs(p["alt"] - q["alt"]) < 1 for q in pts))] + pts
        self._hz_cache = None
        self._persist()
        self.setup = self._which_setup()           # auch nach dem Kurzscan nur mit bestätigten Punkten (Review PR #143)
        self.last_scan = sc.view(now)

    def _scan_tick(self, s: TelescopeState, now: float):
        sc = self.scan
        p = sc.cur
        if p is not None and p.get("tid") is None:            # Befehl abgelehnt – erneut (Backoff in _cmd)
            return self._scan_goto(p, now)
        if p is None:
            p = sc.next_probe(now, self._avoid(now))
            if p is None:
                if sc.state == "laeuft":
                    return                                     # wartet (Gegenprobe nach Wolkenverdacht)
                return self._scan_done(sc, now)
            return self._scan_goto(p, now)
        same = bool(s.target) and s.target.get("id") == p["tid"]
        if same and s.step in ("goto", "astrometry"):
            p["seen"] = True
        if same and (s.step == "capture" or s.frames > 0):
            res, solved = "frei", True
        elif same and s.astrometry_failed and (p.get("seen") or now - p["since"] >= skyscan.STALE_S):
            res, solved = ("zweifel" if (s.stars or 0) >= skyscan.STARS_SOME else "verdeckt"), False
        elif now - p["since"] > skyscan.PROBE_TIMEOUT_S:
            res, solved = "zweifel", False
        else:
            return
        sc.result(res, now, s.stars, solved)
        if p["why"] == "gegenprobe" and res != "frei" and sc.state == "laeuft":
            self.log("Gegenprobe ohne Sterne – vielleicht eine Wolke. Prüfe die letzten Punkte später noch einmal.", "warn", now)

    def _scan_goto(self, p: dict, now: float):
        ra, dec = sky.radec(p["az"], p["alt"], self.site["lat"], self.site["lon"], now)
        tid = f"scan-{p['n']}"
        tgt = Target(tid, f"Peilpunkt {p['az']:.0f}° / {p['alt']:.0f}°", ra, dec)
        if self._cmd("observe", self.driver.observe, tgt, skyscan.EXPOSURE_S, now=now):
            p["tid"], p["since"] = tid, now

    def _scan_done(self, sc: skyscan.Scan, now: float):
        self._cmd("stop", self.driver.stop, now=now)
        self._scan_store(now)
        pts = sc.points()
        free = sum(1 for p in pts if p["res"] == "frei")
        if sc.state == "wolken":
            self.clouds_at = now
            self.log(f"Himmelsscan beendet: {sc.why} {len(pts)} Punkte gespeichert; als Hindernis zählt davon nur Sicheres.", "bad", now)
        else:
            known = sum(self.horizon()["known"])
            if self.setup:
                n = len(self.setup["nights"])
                setup = f" Aufstellung wie in {n} früheren Nächten." if n != 1 else " Aufstellung wie in einer früheren Nacht."
            else:
                setup = " Neue Aufstellung." if self.groups else ""
            self.log(f"Himmel vermessen: {len(pts)} Punkte, {free} frei, Grenze in {known} von {skymap.N} Richtungen bekannt."
                     + setup + (f" {sc.why}" if sc.why else ""), "ok", now)
        if self.scan_only:
            self._scan_finish_only(now)

    def _scan_finish_only(self, now: float):
        """Scan ohne Plan: einklappen und den Autopiloten beenden."""
        self.scan_only = False
        if not self.state.parked and not self.park_pending:
            self._park_now(now, "Himmelsscan fertig")
        self.mode = "aus"

    def sky_view(self, now: float) -> dict:
        hz = self.horizon()
        sc = self.scan.view(now) if self.scan else self.last_scan
        return {"horizon": hz["horizon"], "top": hz["top"], "known": hz["known"],
                "cols": [{k: c[k] for k in ("lo", "hi", "top", "sure")} for c in hz["cols"]],
                "site_horizon": (self.site or {}).get("horizon"),
                "points": [{k: p.get(k) for k in ("az", "alt", "res", "sure", "src")} for p in self.night_points[-200:]],
                "scan": sc, "scanning": self.scan is not None,
                "setup": {"id": self.setup["id"], "nights": len(self.setup["nights"]), "last": self.setup["last"]} if self.setup else None,
                "setups": len(self.groups), "site_wide": sum(1 for v in self.site_wide if v is not None),
                "can_accept": bool(self._site_id()) and any(hz["known"]) and getattr(self.driver, "kind", "") != "sim",
                "measured": self.measured(),
                "moon": self._moon_view(now)}

    def _moon_view(self, now: float) -> dict | None:
        """Mond als Azimut/Höhe (der Scan weicht ihm aus) – nur über dem Horizont."""
        if not self.site:
            return None
        az, alt = sky.moon_altaz(now, self.site["lat"], self.site["lon"])
        return {"az": round(az, 1), "alt": round(alt, 1)} if alt > -2 else None

    # --------------------------------------------------- Plane meine Nacht --- #
    # Ablauf: Entwurf (sofort, mit der bekannten Grenze) → Start (Knopf oder Frist) → warten auf Dunkelheit → öffnen,
    # ausrichten, Himmel vermessen → neu planen mit der gemessenen Grenze → beobachten (alle Wächter wie sonst; nach einem
    # Ausfall oder Wiederöffnen wird der Rest neu geplant) → Morgendämmerung: einklappen, Nachtbilanz. Ziele und Zeitplan
    # kommen aus dem Dienst `night_autoplan` (Bilder-Modul, Planer-Regeln); fehlt er, gibt es diese Funktion nicht.
    def plan_night(self, now: float, autostart: bool = False) -> str | None:
        """Entwurf für diese Nacht rechnen und zeigen. Rückgabe: Grund, warum nicht (sonst None)."""
        with self.lock:
            if self.mode in ("laeuft", "wartet"):
                return "Der Autopilot läuft schon – erst pausieren oder einklappen."
            if not self.env.autoplan:
                return "Die automatische Zielwahl braucht das Bilder-Modul."
            if self.night is None or self.night != self.current_night(now):
                self.auto = None
                self.load_night(now)
            if not self.site:
                return "Für diese Nacht ist kein Standort bekannt (Einstellungen → Standorte)."
            self.auto = {"night": self.night, "state": "entwurf", "created": now, "replans": 0, "last_plan": None,
                         "deadline": time.time() + AUTO_GRACE_S if autostart else None, "scan_started": False,
                         "summary": None, "note": None, "moon": None, "cloud": None}
            if not self._autoplan(now):
                return None                       # Hinweis steht in `auto.note` (Ansicht), der Entwurf ist leer
            n = len([i for i in self.items if i.auto])
            self.log(f"Plane meine Nacht – Entwurf: {n} Ziel{'e' if n != 1 else ''} "
                     + (f"({', '.join(dict.fromkeys(i.name for i in self.items if i.auto))}). " if n else ". ")
                     + "Nach dem Start wird zuerst der Himmel vermessen, dann der Plan angepasst.", "info", now)
            return None

    def night_go(self, now: float) -> str | None:
        """Entwurf starten: ab jetzt alles automatisch (Dämmerung abwarten, vermessen, neu planen, beobachten, einklappen)."""
        with self.lock:
            a = self.auto
            if not a or a["state"] != "entwurf":
                return "Kein Entwurf für diese Nacht – zuerst „Plane meine Nacht“."
            a["deadline"] = None
            a["state"] = "vermessen"
            self.scan_auto_night = self.night             # kein zweiter automatischer Kurzscan
            self.start(now)
            self.log("Plane meine Nacht läuft: erst den Himmel vermessen, dann die Ziele – ab hier alles automatisch.", "info", now)
            return None

    def night_cancel(self, now: float) -> str | None:
        """Automatischen Plan verwerfen (nicht während der Autopilot läuft) – zurück zum Plan aus dem Planer."""
        with self.lock:
            if self.mode in ("laeuft", "wartet"):
                return "Erst pausieren oder einklappen, dann den automatischen Plan verwerfen."
            if not self.auto:
                return None
            self.auto = None
            self.items = [i for i in self.items if i.manual and i.status == "geplant"]
            self.load_night(now)
            self.log("Automatischer Plan verworfen – es gilt wieder der Plan aus dem Planer.", "info", now)
            return None

    def _auto_measuring(self) -> bool:
        return bool(self.auto and self.auto["state"] == "vermessen")

    def _auto_measure(self, now: float) -> bool:
        """Schritt „vermessen“ (Teleskop offen und ausgerichtet): Scan starten bzw. nach dem Scan neu planen.
        True = Takt endet hier (Scan läuft)."""
        a = self.auto
        if not a["scan_started"]:
            a["scan_started"] = True
            if any(p.get("src") == "scan" for p in self.night_points):
                self.log("Der Himmel ist für diese Nacht schon vermessen – plane mit dieser Messung.", "info", now)
            else:
                why = self.start_scan(now, "kurz" if self.groups else "voll")
                if why is None:
                    return True
                self.log(f"Vermessung übersprungen: {why} Plane mit der bekannten Grenze.", "warn", now)
        a["state"] = "laeuft"
        self._replan(now, "nach der Vermessung", force=True)
        return False

    def _plan_horizon(self) -> list[float]:
        """Grenze für die Planung: gemessene Grenze dieser Nacht, angehoben an heute gesperrten Richtungen."""
        h = [float(v or 0) for v in self.horizon()["horizon"]]
        for b in self.blocked:
            i = skymap.bin_of(b["az"])
            for j in (i - 1, i, i + 1):
                h[j % skymap.N] = max(h[j % skymap.N], min(90.0, b["alt"] + BLOCK_ALT))
        return h

    def _autoplan(self, now: float) -> bool:
        """Ziele ab `now` neu wählen (Dienst `night_autoplan`). Bleibt: Erledigtes/Begonnenes (Verlauf) und von Hand
        Hinzugefügtes; nie gestartete automatische Ziele werden ersetzt. False = kein Plan (Hinweis in `auto.note`)."""
        a = self.auto
        exp = float(self.cfg["exposure"])
        have: dict[str, float] = {}
        for i in self.items:
            if i.stacked:
                have[i.id] = have.get(i.id, 0.0) + i.stacked * exp / 3600
        used = {i.id for i in self.items if i.real_start and i.stacked and not i.manual}
        left = int(self.cfg["night_max_targets"]) - len(used)        # so viele neue Ziele dürfen noch dazukommen
        opts = {"max_targets": left if left > 0 else max(1, len(used)), "have_add": have,
                "top": list(self.horizon()["top"]),
                "exclude": sorted({i.id for i in self.items if i.status == "verdeckt"})}     # heute verdeckt: nicht wieder wählen
        try:
            res = self.env.autoplan(self.night, self._plan_horizon(), now, opts)
        except Exception:
            import traceback
            traceback.print_exc()
            res = {"error": "Die Ziele konnten nicht berechnet werden – Details im Protokoll."}
        if not res or res.get("error"):
            a["note"] = (res or {}).get("error") or "Die automatische Zielwahl ist gerade nicht verfügbar."
            self.log(a["note"], "warn", now)
            if a["state"] == "entwurf":
                self.items = [i for i in self.items if i.manual and i.status == "geplant"]
            return False
        a["note"] = None
        a["moon"], a["cloud"], a["notes"] = res.get("moon"), res.get("cloud"), res.get("notes") or []
        end = self.dark["end"] if self.dark else None
        new = [Item(str(b["id"]), b["name"], float(b["ra"]), float(b["dec"]), max(now, float(b["start"])), float(b["end"]),
                    hours=float(b.get("hours") or 0), auto=True, reason=b.get("reason") or "", category=b.get("category") or "")
               for b in res.get("blocks") or [] if float(b["end"]) > now + 600 and (end is None or float(b["start"]) < end)]
        fresh = list(dict.fromkeys(i.id for i in new if i.id not in used))
        allowed = used | set(fresh[:max(0, left)])                  # Höchstzahl je Nacht gilt auch nach Neuplanungen
        new = [i for i in new if i.id in allowed]
        keep = []
        for i in self.items:
            if i.status == "aktiv" or (i.manual and i.status == "geplant"):
                keep.append(i)
            elif i.real_start or (i.status in ("verdeckt", "uebersprungen") and a["state"] != "entwurf"):
                if i.status in OPEN:                  # Verlauf bleibt sichtbar, wird aber nicht mehr angefahren
                    i.end = max(i.start, min(i.end, now))
                keep.append(i)
        self.items = sorted(keep + new, key=lambda i: i.start)
        a["last_plan"] = now
        return True

    def _replan(self, now: float, why: str, force: bool = False) -> bool:
        """Rest der Nacht neu planen (nach Vermessung, Ausfall, Wiederöffnen). True = neuer Plan gilt."""
        a = self.auto
        if not a or a["state"] != "laeuft" or self._current() is not None:
            return False
        if not force and a.get("last_plan") is not None and now - a["last_plan"] < REPLAN_EVERY:
            return False
        before = [i.name for i in self.items if i.auto and i.end > now]       # auch das eben ausgefallene Ziel
        if not self._autoplan(now):
            return False
        a["replans"] += 1
        after = [i.name for i in self.items if i.auto and i.status == "geplant"]
        gone = [n for n in dict.fromkeys(before) if n not in after]
        added = [n for n in dict.fromkeys(after) if n not in before]
        plan = ", ".join(f"{i.name} {_fmt(i.start)}–{_fmt(i.end)}" for i in self.items if i.auto and i.status == "geplant") or "keine passenden Ziele"
        diff = (f" Neu: {', '.join(added)}." if added else "") + (f" Entfällt: {', '.join(gone)}." if gone else "")
        self.log(f"Plan neu berechnet ({why}): {plan}.{diff}", "info", now)
        return True

    def _finish_auto(self, now: float):
        """Nachtbilanz (einmal): Protokoll und Ansicht."""
        a = self.auto
        if not a or a["state"] == "fertig":
            return
        a["state"] = "fertig"
        exp = float(self.cfg["exposure"])
        seen: dict[str, dict] = {}
        for i in self.items:
            if not i.real_start:
                continue
            x = seen.setdefault(i.name, {"name": i.name, "stacked": 0, "status": i.status, "minutes": 0})
            x["stacked"] += i.stacked
            x["minutes"] += round(((i.real_end or now) - i.real_start) / 60)
            if i.status == "fertig" or x["status"] != "fertig":
                x["status"] = i.status
        rows = list(seen.values())
        frames = sum(r["stacked"] for r in rows)
        hidden = [r["name"] for r in rows if r["status"] == "verdeckt" and not r["stacked"]]
        seen_ok = [r for r in rows if r["stacked"]]
        scanned = any(p.get("src") == "scan" for p in self.night_points)
        a["summary"] = {"targets": seen_ok, "frames": frames, "hours": round(frames * exp / 3600, 2), "blocked": hidden,
                        "measured": scanned, "replans": a["replans"], "known": sum(self.horizon()["known"])}
        hours = frames * exp / 3600
        tail = (f" Verdeckt: {', '.join(hidden)}." if hidden else "") + (f" {a['replans']}× neu geplant." if a["replans"] else "")
        self.log(f"Nachtbilanz: {len(seen_ok)} Ziel{'e' if len(seen_ok) != 1 else ''} beobachtet"
                 + (f" ({', '.join(r['name'] for r in seen_ok)})" if seen_ok else "")
                 + f", {frames} Bilder gestapelt (≈ {_dur_h(hours)} Belichtung)."
                 + (" Himmel vermessen." if scanned else "") + tail + " Die Bilder gehen wie gewohnt in die Pipeline.", "ok", now)

    def auto_view(self, now: float) -> dict | None:
        a = self.auto
        if not a:
            return None
        label = AUTO_LABEL.get(a["state"], a["state"])
        if a["state"] == "vermessen":
            if self.hold and self.mode == "wartet":
                label = self.hold.text
            elif self.scan is not None:
                label = "Vermesse den freien Himmel"
            elif self.machine.phase in ("eingeklappt", "oeffnet", "geoeffnet", "richtet_aus"):
                label = "Öffne und richte aus – danach wird der Himmel vermessen"
        elif a["state"] == "laeuft" and self.hold and self.mode == "wartet":
            label = self.hold.text
        return {"state": a["state"], "label": label, "night": a["night"], "replans": a["replans"], "note": a.get("note"),
                "notes": a.get("notes") or [], "moon": a.get("moon"), "cloud": a.get("cloud"), "summary": a.get("summary"),
                "deadline_in": max(0, round(a["deadline"] - time.time())) if a.get("deadline") else None,
                "autostart": bool(self.cfg.get("night_autostart")), "max_targets": int(self.cfg["night_max_targets"])}

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
            "now": now, "mode": self.mode, "phase": self.machine.phase, "phase_label": self.phase_text(now),
            "eta": self.eta(now), "retry": {k: {"busy": bool(r.get("busy")), "in_s": max(0, round(r["next"] - now))} for k, r in self.retry.items()},
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
                "battery_left": self.battery.view(s.battery, s.charging, s.model, not s.parked, float(self.cfg["battery_min"])),
                "rain_in": None if (rm := self.rain_in(now)) is None else round(rm),
            },
            "weather": [{"t": x["t"], "cloud": x.get("cloud")} for x in (self.weather or {}).get("hours") or []],
            "blocked": [{k: b[k] for k in ("az", "alt", "target", "sure")} for b in self.blocked],
            "proposals": [{k: p[k] for k in ("az", "alt", "target", "sure", "t")} for p in self.proposals],
            "sky": self.sky_view(now),
            "alarm": self.alarm, "parking": bool(self.park_pending),
            "confirmed": self.confirmed(now), "requires_confirm": self.requires_confirm,
            "auto": self.auto_view(now),
        }

    def eta(self, now: float) -> float | None:
        """Voraussichtliche Restdauer der aktuellen Phase in s (Öffnen, Einrichten, Einklappen), sonst None."""
        ph = self.machine.phase
        if ph not in EXPECT_S or self.phase_since is None:
            return None
        return max(0.0, self.took.get(ph, EXPECT_S[ph]) - (now - self.phase_since))

    def phase_text(self, now: float) -> str:
        label, rest = self.machine.label, self.eta(now)
        if rest is not None:
            return f"{label} … noch ca. {_dur(rest)}" if rest >= 5 else f"{label} … gleich fertig"
        waiting = [k for k in ("open", "init") if k in self.retry]
        if waiting:
            r = self.retry[waiting[0]]
            return f"{label} – {'Teleskop noch beschäftigt' if r.get('busy') else _label(waiting[0]) + ' abgelehnt'}, neuer Versuch in {_dur(max(0, r['next'] - now))}"
        return label

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


def _dur(s: float) -> str:
    return f"{round(s)} s" if s < 90 else f"{round(s / 60)} min"


def _dur_h(h: float) -> str:
    """„45 min“, „2 h 10 min“."""
    m = round(h * 60)
    return f"{m} min" if m < 60 else (f"{m // 60} h {m % 60} min" if m % 60 else f"{m // 60} h")


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

