"""Teleskop: steuert Smart-Teleskope selbst – Plan der Nacht abarbeiten, Hindernisse erkennen, bei Wetter/Dämmerung einklappen.

Treiber: Simulator (immer da), „Vaonis (nur Status)“ ohne Schlüssel (`vaonis_read`) oder ein Treiber-Plugin (`plugins.py`,
fremder Code, nur am Rechner selbst aktivierbar). Im Repo steht keine Anmelde- oder Befehlslogik für Vaonis. Eine „Einheit“ = Treiber + Autopilot + Takt-Thread; die Architektur erlaubt mehrere
Einheiten (zwei Teleskope, je Adapter eine Verbindung), eingerichtet ist vorerst eine.
Dienste: `sites` (Ort/Horizont der Nacht), `weather_forecast` (Bilder-Modul), `frame_quick_metrics` (Bilder-Modul: Messwerte je
Live-Rohbild für die Beschlag-Erkennung), `catalog_resolve`/`catalog_objects` – fehlt
einer, läuft das Modul trotzdem (ohne Wetter bzw. ohne Zielsuche).
"""
from __future__ import annotations

import threading
import time
import traceback

from ...core.modules import BaseModule
from . import autopilot as ap
from .driver import DriverError

DEFAULTS = {"driver": "sim", "ip": "", "model": "vespera", "sim_speed": 30, "sim_start": "night",
            "sim_scenario": "demo", "weather": True, "live_frames": True,
            "plugin_paths": [], "plugins_enabled": [], "plugin_options": {}, **ap.DEFAULTS}
MODELS = {"vespera": "Vaonis Vespera", "vespera2": "Vaonis Vespera II", "stellina": "Vaonis Stellina"}
TICK_S = 1.0


def settings(store) -> dict:
    raw = store.get("telescope")
    return {**DEFAULTS, **{k: v for k, v in raw.items() if k in DEFAULTS}}


class Unit:
    """Ein Teleskop: Treiber, Autopilot und Takt."""

    def __init__(self, mod, cfg: dict):
        self.mod, self.cfg = mod, cfg
        self.error: str | None = None
        self.clock = None
        self.driver = self._driver(cfg)
        self.pilot = ap.Autopilot(self.driver, self._env(), {k: cfg[k] for k in ap.DEFAULTS}, clock=self.clock)
        self.pilot.requires_confirm = not self.simulated
        self._stop = threading.Event()
        self.thread: threading.Thread | None = None
        self.collector = None
        if not self.simulated and cfg["live_frames"] and getattr(self.driver, "ip", None) and "capture" in getattr(self.driver, "features", {"capture"}):
            from .frames import Collector                     # Live-Rohbilder per FTP (nur lesen) → Eingangsordner
            self.collector = Collector(self, mod.ctx.settings.input_dir, self.driver.ip)

    def _driver(self, cfg):
        from . import sim
        drv = cfg["driver"]
        if drv == "vaonis-read":
            from .vaonis_read import VaonisReadOnly
            return VaonisReadOnly(cfg["ip"] or None)
        if drv.startswith("plugin:"):
            from . import plugins
            pid = drv.split(":", 1)[1]
            info = next((i for i in self.mod.plugins() if i.id == pid), None)
            if info is None:
                self.error = "Das gewählte Treiber-Plugin wurde nicht gefunden."
            elif pid not in cfg["plugins_enabled"]:
                self.error = "Das gewählte Treiber-Plugin ist nicht aktiviert."
            else:
                try:
                    return plugins.load(info, (cfg["plugin_options"] or {}).get(pid) or {})
                except DriverError as e:
                    self.error = str(e)
        site = self.mod.site(None)
        start = None
        if cfg["sim_start"] == "night" and site:
            start = _dusk_after(time.time(), site, float(cfg["sun_limit"]))
        self.clock = sim.SimClock(start, float(cfg["sim_speed"] or 1))
        sc = sim.Scenario.demo(self.clock.now()) if cfg["sim_scenario"] == "demo" else sim.Scenario()
        return sim.SimDriver(self.clock, sc, model=cfg["model"])

    def _env(self) -> ap.Env:
        mod = self.mod

        def plan(night, model):
            items = ap.plan_items(mod.ctx.store.get("observatory").get("plan"), night, self.cfg["model"])
            if not items and not getattr(items, "note", None) and self.simulated and mod.site(night):
                objs = mod.ctx.service("catalog_objects")
                if objs:
                    items = ap.demo_items(self.now(), mod.site(night), [o for o in objs() if o.get("kind") in ("galaxy", "globular", "planetary", "nebula", "open")])
            return items

        def forecast(site, t0, t1):
            fn = mod.ctx.service("weather_forecast")
            if not fn or not self.cfg["weather"]:
                return None
            if self.simulated and self.cfg["sim_speed"] and float(self.cfg["sim_speed"]) > 1:
                return None                       # Zeitraffer: echte Vorhersage passt nicht zur virtuellen Zeit
            return fn(site, t0, t1)

        def rain(site, minutes):
            fn = mod.ctx.service("weather_rain")               # Bilder-Modul; fehlt es → kein Regenwächter
            if not fn or not self.cfg["weather"]:
                return None
            if self.simulated and self.cfg["sim_speed"] and float(self.cfg["sim_speed"]) > 1:
                return None
            return fn(minutes=minutes, site={"lat": site["lat"], "lon": site["lon"]})
        return ap.Env(site=mod.site, forecast=forecast, plan=plan, rain=rain)

    def measure_frames(self, paths, item=None, start: float | None = None, most: int = 12):
        """Frisch geholte Rohbilder messen (Dienst `frame_quick_metrics`) und dem Autopiloten melden; fehlt der Dienst
        (Bilder-Modul aus), gibt es keine Beschlag-Erkennung. Nach einer Lücke nur die neuesten `most` Bilder.
        `item`/`start` = Versuch, zu dem die Bilder gehören: ist er nicht mehr der laufende (Zielwechsel während des Holens
        oder Messens), werden die Messwerte verworfen – sonst würden sie zum Bezug des neuen Ziels (Review PR #140)."""
        fn = self.mod.ctx.service("frame_quick_metrics")
        if not fn:
            return
        for p in sorted(paths, key=lambda p: p.name)[-most:]:
            try:
                m = fn(str(p))
                if not self.pilot.frame_seen(self.now(), m, item, start):
                    return
            except Exception:
                traceback.print_exc()

    @property
    def simulated(self) -> bool:
        return self.driver.kind == "sim"

    def now(self) -> float:
        return self.clock.now() if self.clock else time.time()

    def start(self):
        self.thread = threading.Thread(target=self._loop, daemon=True, name="teleskop-takt")
        self.thread.start()
        if self.collector:
            self.collector.start()

    def _loop(self):
        while not self._stop.wait(TICK_S if self.simulated else 2.0):
            try:
                self.pilot.tick(self.now())
            except Exception:
                traceback.print_exc()

    def stop(self):
        """Server endet: läuft der Autopilot und hat er das Teleskop bewegt, wird eingeklappt."""
        self._stop.set()
        if self.collector:
            self.collector.stop()
        try:
            if self.pilot.mode in ("laeuft", "wartet", "pausiert") and not self.pilot.state.parked:
                self.pilot.emergency(self.now(), "AstroFlow wird beendet")
            self.driver.disconnect()
        except Exception:
            pass

    def info(self) -> dict:
        return {"driver": self.driver.kind, "label": self.driver.label if not self.simulated else "Simulator",
                "model": self.cfg["model"], "model_name": MODELS.get(self.cfg["model"], self.cfg["model"]),
                "simulated": self.simulated, "speed": float(self.cfg["sim_speed"]) if self.simulated else 1.0, "error": self.error,
                "device_plan": "device_plan" in getattr(self.driver, "capabilities", ()),
                "live_frames": bool(self.collector), "frames_fetched": self.collector.count if self.collector else 0}


def _dusk_after(now: float, site: dict, limit: float) -> float | None:
    """Beginn der nächsten Dunkelheit (oder jetzt, wenn es schon dunkel ist) – Startzeit der Vorführ-Nacht."""
    from . import sky
    for k in range(0, 24 * 12):
        t = now + k * 300
        if sky.sun_alt(t, site["lat"], site["lon"]) <= limit:
            return t
    return None


class Module(BaseModule):
    id = "telescope"
    name = "Teleskop"
    description = "Smart-Teleskop selbst steuern: Plan der Nacht abarbeiten, Hindernisse erkennen, bei Wetter und Dämmerung einklappen."
    icon = "target"
    scripts = ("telescope.js",)
    styles = ("telescope.css",)

    def __init__(self, ctx):
        super().__init__(ctx)
        self.lock = threading.RLock()
        self.units: list[Unit] = [Unit(self, settings(ctx.store))]
        # Fehler melden: Plugin-Ordner nie in Berichte/Protokollauszüge (Dienst des Kerns, Liste von Funktionen)
        if isinstance(ctx.services.get("report_private_dirs"), list):
            ctx.services["report_private_dirs"].append(self.private_dirs)
        from .captures import CaptureImport
        self.captures = CaptureImport(self)                 # Importansicht: vorhandene Aufnahmen vom Teleskop holen
        ctx.services["telescope_sky"] = self.sky             # „Heute Nacht“: klar/bewölkt laut Teleskop

    def private_dirs(self) -> list[str]:
        from . import plugins
        cfg = settings(self.ctx.store)
        return plugins.private_dirs(self.ctx.data_dir, cfg["plugin_paths"], cfg["plugin_options"])

    def plugins(self):
        from . import plugins
        return plugins.discover(self.ctx.data_dir, settings(self.ctx.store)["plugin_paths"])

    def site(self, night):
        book = self.ctx.service("sites")
        try:
            return book.site_for(night=night) if book else None
        except Exception:
            return None

    def sky(self) -> dict | None:
        """Himmel laut Teleskop: {clear, ratio, t, why, source} oder None (nichts Frisches; im Simulator nie – erfundener Himmel)."""
        try:
            u = self.unit
            if u.simulated:
                return None
            v = u.pilot.sky_now(u.now())
            return {**v, "source": MODELS.get(u.cfg["model"], u.cfg["model"])} if v else None
        except Exception:
            traceback.print_exc()
            return None

    @property
    def unit(self) -> Unit:
        return self.units[0]

    def rebuild(self):
        """Neue Einstellungen (Treiber, Zeitraffer …) wirken nur, solange der Autopilot nicht läuft."""
        with self.lock:
            old = self.unit
            if old.pilot.mode in ("laeuft", "wartet", "pausiert"):
                raise DriverError("Erst den Autopilot beenden (Einklappen), dann den Treiber wechseln.")
            old.stop()
            self.units[0] = Unit(self, settings(self.ctx.store))
            self.units[0].pilot.adopt(old.pilot)          # von Hand hinzugefügte Ziele u. a. nicht verlieren
            self.units[0].start()

    def router(self):
        from .api import build_router
        return build_router(self)

    def start(self):
        for u in self.units:
            u.start()

    def stop(self):
        for u in self.units:
            u.stop()

    def status(self) -> dict:
        u = self.unit
        p = u.pilot
        cur = p._current()
        return {"mode": p.mode, "phase": p.machine.phase, "phase_label": p.machine.label, "simulated": u.simulated,
                "target": cur.name if cur else None, "alarm": p.alarm,
                "busy_jobs": int(p.mode in ("laeuft", "wartet")) + int(bool(self.captures.job and self.captures.job["state"] == "running"))}

    def activity(self, limit: int = 30) -> list[dict]:
        out = []
        j = self.captures.job
        if j:
            out.append({"key": f"telescope:import:{int(j['started'])}", "title": "Aufnahmen vom Teleskop importieren", "kind": "Import",
                        "detail": j["text"] or (f"{j['current']} · {j['done']}/{j['total']} Rohbilder" if j["current"] else ""),
                        "state": {"running": "running", "done": "done"}.get(j["state"], "error"),
                        "progress": j["done"] / j["total"] if j["total"] else None, "started": j["started"], "finished": j["finished"],
                        "href": "#/import"})
        return out + self._night_activity()

    def _night_activity(self) -> list[dict]:
        u = self.unit
        p = u.pilot
        if p.mode == "aus" and not any(i.real_start for i in p.items):
            return []
        cur = p._current()
        done = sum(1 for i in p.items if i.status == "fertig")
        total = len([i for i in p.items if not i.substitute_for]) or 1
        state = {"laeuft": "running", "wartet": "queued", "pausiert": "stopped", "fertig": "done"}.get(p.mode, "stopped")
        return [{"key": f"telescope:{p.night}", "title": f"Teleskop – Nacht {p.night or ''}".strip(), "kind": "Teleskop",
                 "detail": (f"{cur.name} · " if cur else "") + p.machine.label + (" (Simulation)" if u.simulated else ""),
                 "state": state, "progress": min(1.0, done / total), "started": min([i.real_start for i in p.items if i.real_start] or [time.time()]),
                 "finished": None if state in ("running", "queued") else time.time(), "href": "#/teleskop"}]
