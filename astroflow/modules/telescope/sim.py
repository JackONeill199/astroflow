"""Simulator mit demselben Vertrag wie ein echtes Teleskop – für Tests, die Oberfläche und Proben ohne Gerät.

Virtuelle Uhr (`SimClock`: Zeitraffer, `ManualClock`: Tests springen beliebig), Motoren fahren mit endlicher Geschwindigkeit,
Ausrichten/Öffnen/Einklappen dauern. Szenarien legen Wolken, Hindernisse (Azimut-/Höhenbereich), Tau, Akku, Verbindungs-
abbrüche und eine Übernahme durch die Handy-App fest. Alles deterministisch (keine Zufallszahlen) – Tests sind wiederholbar.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

from . import sky
from .driver import Driver, DriverBusy, DriverError, PlanItem, Target, TelescopeState

SLEW_DEG_S = 3.0          # Motoren (Grad/s)
OPEN_S = 8.0              # Arm öffnen / einklappen
INIT_S = 75.0             # Auto-Init inkl. Fokus
ASTROMETRY_S = 15.0
PARK_AZ = 0.0
SIM_FWHM = 3.8            # px, gutes Bild
DEW_RAMP_S = 20 * 60      # so lange dauert es, bis die Optik ganz beschlagen ist
OPEN_ALT = 10.0           # nach dem Öffnen steht der Tubus knapp über dem Horizont


def park_alt(model: str) -> float:
    """Vaonis (einarmig) hängt eingeklappt senkrecht nach unten (Probe 1: ALT −90°), andere liegen waagerecht."""
    return -90.0 if str(model or "").startswith(("vespera", "stellina")) else 0.0


class RealClock:
    def now(self) -> float:
        return time.time()


class SimClock:
    """Zeitraffer: virtuelle Zeit = Start + (echte Zeit seit Start) × Faktor."""

    def __init__(self, start: float | None = None, speed: float = 1.0):
        self.t0, self.r0, self.speed = (time.time() if start is None else start), time.time(), max(0.1, float(speed))

    def now(self) -> float:
        return self.t0 + (time.time() - self.r0) * self.speed


class ManualClock:
    """Für Tests: Zeit steht, bis `advance()` sie weiterschiebt."""

    def __init__(self, start: float):
        self.t = float(start)

    def now(self) -> float:
        return self.t

    def advance(self, s: float) -> float:
        self.t += s
        return self.t


@dataclass
class Scenario:
    """Zeiten absolut (Unix-Sekunden). Hindernis: {az0, az1, alt} – verdeckt alles unterhalb `alt` im Azimutbereich."""
    clouds: list[tuple[float, float]] = field(default_factory=list)
    obstacles: list[dict] = field(default_factory=list)
    dew_from: float | None = None      # Optik beschlägt ab hier, schleichend über DEW_RAMP_S (Sterne weniger, FWHM, Schleier)
    battery: float = 100.0
    battery_per_h: float = 6.0
    offline: list[tuple[float, float]] = field(default_factory=list)     # Verbindung weg
    app_takes_control_at: float | None = None
    stars: int = 180                                                     # Sterne in einem guten Bild
    setup_busy_s: float = 0.0          # nach dem Öffnen so lange „beschäftigt“ (Gerät richtet sich selbst ein) → DriverBusy
    stop_silently_at: float | None = None   # Gerät beendet die Beobachtung einmal ohne Fehler und ohne Bilder (Stillstand)
    init_rejected: bool = False        # Ausrichten wird immer abgelehnt (z. B. falsche Geräte-Uhr) → DriverError

    @classmethod
    def demo(cls, start: float) -> "Scenario":
        """Vorführung: Hauswand im Osten, Wolkenband nach ~3 h, Tau gegen Morgen."""
        return cls(clouds=[(start + 3.0 * 3600, start + 3.6 * 3600)],
                   obstacles=[{"az0": 60.0, "az1": 115.0, "alt": 38.0}], dew_from=start + 7.5 * 3600)


def _between(t: float, spans) -> bool:
    return any(a <= t < b for a, b in spans)


def _az_in(az: float, a0: float, a1: float) -> bool:
    az, a0, a1 = az % 360, a0 % 360, a1 % 360
    return a0 <= az <= a1 if a0 <= a1 else az >= a0 or az <= a1


class SimDriver(Driver):
    kind = "sim"
    label = "Simulator"
    capabilities = frozenset({"device_plan", "focus", "preview"})

    def __init__(self, clock=None, scenario: Scenario | None = None, model: str = "vespera"):
        self.clock = clock or RealClock()
        self.sc = scenario or Scenario()
        self.model = model
        self._lock = threading.RLock()
        self._t = self.clock.now()
        self.connected = False
        self.master = "none"
        self.parked, self.initialized = True, False
        self.az, self.alt = PARK_AZ, park_alt(model)
        self.goal: tuple[float, float] | None = None
        self.step, self.step_until = "idle", 0.0
        self.busy_until = 0.0
        self.target: Target | None = None
        self.exposure = 10.0
        self.frames = self.stacked = self.rejected = 0
        self.frame_t = 0.0
        self.measured: list = []               # (Zeit, Messwerte) je Bild, für `frame_metrics`
        self.stars: int | None = None
        self.astrometry_failed = False
        self.error: str | None = None
        self.battery = float(self.sc.battery)
        self.lat = self.lon = None
        self.plan: list[PlanItem] = []
        self.commands: list[str] = []          # Protokoll der Befehle (Tests)

    # ------------------------------------------------------------ Umgebung --- #
    def blocked(self, t: float, az: float, alt: float) -> str | None:
        """Grund, warum hier gerade keine Sterne zu sehen sind (None = frei)."""
        if alt < 0:
            return "horizon"
        if _between(t, self.sc.clouds):
            return "clouds"
        for o in self.sc.obstacles:
            if _az_in(az, o["az0"], o["az1"]) and alt < o["alt"]:
                return "obstacle"
        return None

    def _online(self, t: float) -> bool:
        return not _between(t, self.sc.offline)

    # ------------------------------------------------------------- Ablauf --- #
    def _target_altaz(self, t: float) -> tuple[float, float]:
        if self.target is None or self.lat is None:
            return self.az, self.alt
        return sky.altaz(self.target.ra, self.target.dec, self.lat, self.lon, t)

    def _advance(self):
        now = self.clock.now()
        while self._t < now:
            dt = min(now - self._t, max(1.0, (now - self._t) / 4000))
            self._t += dt
            self._step(self._t, dt)

    def _move(self, dt: float) -> bool:
        if self.goal is None:
            return True
        gaz, galt = self.goal
        d_az = (gaz - self.az + 540) % 360 - 180
        d_alt = galt - self.alt
        m = SLEW_DEG_S * dt
        self.az = (self.az + max(-m, min(m, d_az))) % 360
        self.alt += max(-m, min(m, d_alt))
        return abs(d_az) <= m and abs(d_alt) <= m

    def _step(self, t: float, dt: float):
        self.battery = max(0.0, self.battery - self.sc.battery_per_h * dt / 3600)
        if self.sc.app_takes_control_at and t >= self.sc.app_takes_control_at and self.master == "us":
            self.master = "other"
            self.sc.app_takes_control_at = None
        s = self.step
        arrived = self._move(dt) if s in ("opening", "parking") else True     # erst fertig, wenn der Arm angekommen ist
        if s in ("opening", "parking", "init") and t >= self.step_until and arrived:
            if s == "parking":
                self.parked, self.az, self.alt = True, PARK_AZ, park_alt(self.model)
            elif s == "opening":
                self.parked = False
                self.busy_until = t + self.sc.setup_busy_s
            else:
                self.initialized = True
            self.step, self.goal = "idle", None
        if s in ("opening", "parking", "init"):
            pass
        elif s in ("goto", "astrometry", "capture") and self.sc.stop_silently_at and t >= self.sc.stop_silently_at:
            self.sc.stop_silently_at = None
            self.step, self.target, self.goal = "idle", None, None
        elif s == "goto":
            self.goal = self._target_altaz(t)
            if self._move(dt):
                self.step, self.step_until = "astrometry", t + ASTROMETRY_S
        elif s == "astrometry" and t >= self.step_until:
            why = self.blocked(t, self.az, self.alt)
            if why:
                self.astrometry_failed, self.error, self.step = True, "ASTROMETRY.FAILED", "idle"
                self.stars = 0 if why != "clouds" else 3
            else:
                self.step, self.frame_t = "capture", t + self.exposure
        elif s == "capture":
            self.goal = self._target_altaz(t)
            self._move(dt)
            while self.frame_t <= t:
                self.frames += 1
                why = self.blocked(self.frame_t, self.az, self.alt)
                fog = self.fogged(self.frame_t)
                if why or (fog > 0.5 and self.frames % 4 != 0):
                    self.rejected += 1
                    self.stars = 0 if why == "obstacle" else 4 if why else max(8, round(self.sc.stars * (1 - 0.8 * fog)))
                else:
                    self.stacked += 1
                    self.stars = round(self.sc.stars * (1 - 0.8 * fog))
                if not why:                                   # Messwerte je Bild wie vom Dienst `frame_quick_metrics`
                    self.measured.append((self.frame_t, {"n_stars": self.stars, "fwhm": round(SIM_FWHM * (1 + fog), 2),
                                                         "veil": round(0.5 + 6 * fog, 2)}))
                    del self.measured[:-200]
                self.frame_t += self.exposure
            if self.plan and t >= self.plan[0].end:
                self._plan_next(t)
        if self.plan and self.step == "idle" and not self.parked and self.initialized:
            self._plan_next(t)

    def fogged(self, t: float) -> float:
        """0 = klare Optik … 1 = voll beschlagen."""
        if self.sc.dew_from is None or t < self.sc.dew_from:
            return 0.0
        return min(1.0, (t - self.sc.dew_from) / DEW_RAMP_S)

    def frame_metrics(self) -> list[tuple[float, dict]]:
        """Messwerte der seit dem letzten Abruf aufgenommenen Bilder (holt der Autopilot je Takt ab)."""
        with self._lock:
            out, self.measured = self.measured, []
            return out

    def _plan_next(self, t: float):
        """Geräte-Plan: arbeitet weiter, auch wenn AstroFlow nicht verbunden ist (Rückfall)."""
        while self.plan and t >= self.plan[0].end:
            self.plan.pop(0)
        if not self.plan:
            self.step, self.step_until, self.goal = "parking", t + OPEN_S, (PARK_AZ, park_alt(self.model))
            self.target = None
            return
        it = self.plan[0]
        if self.target is None or self.target.id != it.target.id:
            self._start(it.target, t)

    def _start(self, target: Target, t: float):
        self.target = target
        self.frames = self.stacked = self.rejected = 0
        self.astrometry_failed, self.error, self.stars = False, None, None
        self.step = "goto"
        self.goal = self._target_altaz(t)

    # ------------------------------------------------------------ Vertrag --- #
    def _need(self, control=True):
        self._advance()
        if not self.connected or not self._online(self._t):
            raise DriverError("Teleskop nicht erreichbar.")
        if control and self.master != "us":
            raise DriverError("AstroFlow hat gerade nicht die Kontrolle über das Teleskop.")

    def connect(self):
        with self._lock:
            self._advance()
            if not self._online(self._t):
                raise DriverError("Teleskop nicht erreichbar.")
            self.connected = True

    def disconnect(self):
        with self._lock:
            self.connected = False
            if self.master == "us":
                self.master = "none"

    def preview(self):
        """Attrappe: kleines synthetisches Sternfeld, das sich mit der Bildzahl ändert (nur wenn schon Bilder da sind)."""
        with self._lock:
            self._advance()
            n, t = self.frames, self._t
        if not n or not self.connected:
            return None
        import io, random
        from PIL import Image, ImageDraw
        rnd = random.Random(7)
        im = Image.new("L", (480, 320), 8)
        d = ImageDraw.Draw(im)
        for _ in range(140):
            x, y, b = rnd.randrange(480), rnd.randrange(320), rnd.randrange(90, 255)
            d.ellipse((x - 1, y - 1, x + 1, y + 1), fill=b)
        d.text((8, 8), f"Simulation · {n} Bilder", fill=200)
        buf = io.BytesIO()
        im.convert("RGB").save(buf, "JPEG", quality=80)
        return buf.getvalue(), t

    def take_control(self):
        with self._lock:
            self._need(control=False)
            self.master = "us"
            self.commands.append("takeControl")

    def release_control(self):
        with self._lock:
            if self.master == "us":
                self.master = "none"

    def state(self) -> TelescopeState:
        with self._lock:
            self._advance()
            online = self.connected and self._online(self._t)
            tg = self.target.as_dict() if self.target else None
            return TelescopeState(model=self.model, simulated=True, connected=online, master=self.master if online else "none",
                                  parked=self.parked, initialized=self.initialized, az=round(self.az, 2), alt=round(self.alt, 2),
                                  step=self.step, target=tg, frames=self.frames, stacked=self.stacked, rejected=self.rejected,
                                  stars=self.stars, astrometry_failed=self.astrometry_failed, battery=round(self.battery),
                                  storage_free=0.6, raw_enabled=True, error=self.error, device_plan=bool(self.plan),
                                  age_s=0.0 if online else 999.0)

    def open(self):
        with self._lock:
            self._need()
            self.commands.append("open")
            if self.parked and self.step != "opening":
                self.step, self.step_until, self.goal = "opening", self._t + OPEN_S, (self.az, OPEN_ALT)

    def park(self):
        with self._lock:
            self._need()
            self.commands.append("park")
            self.plan, self.target = [], None
            if not self.parked or self.step == "opening":
                self.step, self.step_until, self.goal = "parking", self._t + OPEN_S + abs(self.alt) / SLEW_DEG_S, (PARK_AZ, park_alt(self.model))

    def init(self, lat: float, lon: float):
        with self._lock:
            self._need()
            if self.parked:
                raise DriverError("Erst den Arm öffnen.")
            if self._t < self.busy_until:
                self.commands.append("init-busy")
                raise DriverBusy("Ausrichten: Teleskop ist noch beschäftigt.")
            if self.sc.init_rejected:
                self.commands.append("init-rejected")
                raise DriverError("Ausrichten: vom Teleskop abgelehnt.")
            self.commands.append("init")
            self.lat, self.lon = float(lat), float(lon)
            self.step, self.step_until = "init", self._t + INIT_S

    def observe(self, target: Target, exposure_s: float, gain: int | None = None):
        with self._lock:
            self._need()
            if self.parked or not self.initialized:
                raise DriverError("Teleskop ist nicht geöffnet und ausgerichtet.")
            self.commands.append(f"observe:{target.id}")
            self.plan = []
            self.exposure = max(1.0, float(exposure_s))
            self._start(target, self._t)

    def stop(self):
        with self._lock:
            self._need()
            self.commands.append("stop")
            self.plan = []
            if self.step in ("goto", "astrometry", "capture"):
                self.step, self.goal = "idle", None

    def focus(self):
        with self._lock:
            self._need()
            self.commands.append("focus")

    def start_device_plan(self, items: list[PlanItem], exposure_s: float):
        with self._lock:
            self._need()
            if self.parked or not self.initialized:
                raise DriverError("Teleskop ist nicht geöffnet und ausgerichtet.")
            self.commands.append("plan:" + ",".join(i.target.id for i in items))
            self.exposure = max(1.0, float(exposure_s))
            self.plan = sorted(items, key=lambda i: i.start)
            self.target = None
            self._plan_next(self._t)


class SimFtp:
    """Attrappe des Teleskop-FTP für „Vom Teleskop importieren“ im Simulator: zwei Pläne mit kleinen, synthetischen
    16-bit-FITS (Rauschen + ein paar Sterne). Nur LIST/RETR wie am Gerät."""
    _tree: dict | None = None

    @classmethod
    def shared(cls):
        if cls._tree is None:
            cls._tree = cls._build()
        return cls(cls._tree)

    def __init__(self, tree: dict):
        self.tree = tree

    @staticmethod
    def _fits(obj: str, k: int, when: str) -> bytes:
        import io

        import numpy as np
        from astropy.io import fits
        rng = np.random.default_rng(k * 7919 + len(obj))
        a = rng.normal(800, 25, (96, 128))
        yy, xx = np.mgrid[0:96, 0:128]
        for sx, sy, amp in [(20, 30, 9000), (70, 50, 4000), (100, 20, 6000), (40, 75, 3000), (90, 80, 2500)]:
            a += amp * np.exp(-((xx - sx - k * 0.3) ** 2 + (yy - sy) ** 2) / 4.0)
        h = fits.Header({"OBJECT": obj, "EXPTIME": 10.0, "IMAGETYP": "LIGHT", "DATE-OBS": when, "INSTRUME": "Simulator", "GAIN": 200})
        buf = io.BytesIO()
        fits.PrimaryHDU(np.clip(a, 0, 65535).astype(np.uint16), header=h).writeto(buf)
        return buf.getvalue()

    @classmethod
    def _build(cls) -> dict:
        # Ordnerschema wie am Gerät (Probe 14.11.2026): <datum>_<zeit>_plan_<name> / NN-observation-<objekt>; Einzelbeobachtung
        # (Echtbetrieb 15.11.2026, so startet der Autopilot jedes Ziel) ohne Plan-Ebene: <datum>_<zeit>_observation_<objekt>
        obs = {"2026-11-12_19-58-10_plan_Demo": [("01-observation-m31", "M31", "2026-11-12T20:%02d:00"), ("02-observation-m45", "M45", "2026-11-12T22:%02d:00")],
               "2026-11-13_22-41-05_plan_Demo": [("01-observation-m81", "M81", "2026-11-13T23:%02d:00")]}
        single = [("2026-11-14_23-59-44_observation_M1_Krebsnebel", "M1", "2026-11-15T00:%02d:00")]

        def imgs_of(obj, when):
            imgs = {"_m": when[:10].replace("-", "") + "235900"}
            for k in range(4):
                imgs[f"img-{k:04d}.fits"] = cls._fits(obj, k, when % k)
            return imgs
        tree = {"user": {}}
        for p, lst in obs.items():
            tree["user"][p] = {"_m": p[:10].replace("-", "") + "235900"}
            for name, obj, when in lst:
                imgs = imgs_of(obj, when)
                tree["user"][p][name] = {"_m": imgs["_m"], "01-images-initial": imgs}
        for name, obj, when in single:
            imgs = imgs_of(obj, when)
            tree["user"][name] = {"_m": imgs["_m"], "01-images-initial": imgs}
        return tree

    def login(self):
        pass

    def quit(self):
        pass

    def _node(self, path: str):
        node = self.tree
        for part in path.split("/"):
            node = node[part]
        return node

    def mlsd(self, path, facts=None):
        for name, v in self._node(path).items():
            if name == "_m":
                continue
            if isinstance(v, dict):
                yield name, {"type": "dir", "modify": v.get("_m", "")}
            else:
                yield name, {"type": "file", "size": str(len(v))}

    def retrbinary(self, cmd: str, cb):
        cb(self._node(cmd[5:]))
