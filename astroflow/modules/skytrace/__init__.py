"""SkyTrace: bewegte Objekte in Bildfolgen finden – Spuren, Kandidaten, Katalogabgleich (Meteore, Satelliten, Flugzeuge).

Die Analyse-Engine (`engine/`, flache Python-Module aus dem SkyTrace-Projekt) läuft als eigener Prozess mit dem
konfigurierten Python-Interpreter (braucht OpenCV, PyTorch/YOLO, PyAV …). Der Server selbst importiert nur leichte
Engine-Teile (Einstellungen, Overlays, Dateizugriff), damit AstroFlow auch ohne diese Pakete startet.
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
from pathlib import Path

from ...core.modules import BaseModule

ENGINE = Path(__file__).parent / "engine"
# Pakete, die die Analyse-Prozesse brauchen (Prüfung beim Start, Ergebnis in den Einstellungen sichtbar)
ENGINE_PACKAGES = ("numpy", "cv2", "astropy", "PIL", "av", "torch", "ultralytics", "skyfield", "sgp4", "requests", "filelock")


class Module(BaseModule):
    id = "skytrace"
    name = "SkyTrace"
    description = "Lichtspuren und bewegte Objekte in Bildfolgen erkennen und mit Katalogen abgleichen."
    icon = "orbit"
    scripts = ("evidence.js", "skytrace.js", "skytrace-3d.js", "skytrace-erfolge.js")
    styles = ("evidence.css", "skytrace.css", "skytrace-3d.css", "skytrace-erfolge.css")

    def __init__(self, ctx):
        super().__init__(ctx)
        cfg = ctx.store.get("skytrace")
        self.home = Path(cfg.get("home") or os.environ.get("SKYTRACE_HOME") or ctx.data_dir / "skytrace").resolve()
        self.home.mkdir(parents=True, exist_ok=True)
        # Engine-Pfade werden beim Import festgelegt -> Datenordner vor dem ersten Import setzen (Änderung = Neustart)
        os.environ["SKYTRACE_HOME"] = str(self.home)
        if str(ENGINE) not in sys.path:
            sys.path.insert(0, str(ENGINE))
        self.health = {"checked": False}
        from .api import Controller
        from .auto import AutoAnalysis
        self.ctl = Controller(self)
        self.auto = AutoAnalysis(self)
        ctx.on("stacking.stacked", self.auto.stacked)
        from .achievements import Achievements
        self.ach = Achievements(self.home / "erfolge.json", self._stats)
        self._was_running = False
        self._stop = threading.Event()
        ctx.services["skytrace_achievements"] = self.ach.overview      # optional: andere Module dürfen die Erfolge anzeigen
        self._sites_setup()

    def _sites_setup(self):
        """Gemeinsame Ortsliste: Engine-Auflösung im Server auf data/settings.json zeigen, alte SkyTrace-Standorte einmalig
        übernehmen (bleiben in catalog_settings.json erhalten) und die Nächte der Läufe für die Nachtliste melden."""
        import observation_settings
        observation_settings.SITES_FILE = str(self.ctx.store.path)
        book = self.ctx.services.get("sites")
        if book is None:
            return
        try:
            from ...core.sites import migrate_skytrace
            migrate_skytrace(book, observation_settings.load_settings(), key=f"skytrace:{self.home}")
        except Exception:
            import traceback
            traceback.print_exc()
        if isinstance(self.ctx.services.get("night_sources"), list):
            self.ctx.services["night_sources"].append(self.nights)

    def nights(self) -> list[dict]:
        """Nächte der SkyTrace-Läufe aus dem Beobachtungsnamen (Stellina: „JJJJ-MM-TT_observation_…“)."""
        from ...core.sites import night_from_name
        from .api import _read_json
        out, root = [], self.ctl.results
        for path in (root.iterdir() if root.exists() else []):
            if path.is_dir() and (path / "videos.json").is_file():
                for s in _read_json(path / "videos.json", []) or []:
                    n = night_from_name(str(s.get("session") or ""))
                    if n:
                        out.append({"night": n, "label": f"SkyTrace: {s['session'].split('_observation_')[-1]}", "module": "SkyTrace"})
        return out

    def _stats(self) -> dict:
        from .stats import collect
        s = self.ctl.snapshot(logs=False)
        return collect(self.ctl.results, skip=s["run"] if s["running"] else None)

    def _achievements_check(self, running: bool) -> None:
        """Endet eine Analyse, werden die Erfolge im Hintergrund neu geprüft (Hinweis erscheint über `status`)."""
        if self._was_running and not running:
            threading.Thread(target=self._refresh_achievements, daemon=True).start()
        self._was_running = running

    def _achievements_loop(self) -> None:
        """Ausgangsstand beim Start herstellen (Altbestand = gesehen, ungesehene Erfolge nach Neustart wieder melden), danach Läufe
        beobachten – unabhängig davon, ob ein Browser offen ist (automatische Analysen)."""
        self._refresh_achievements()
        while not self._stop.wait(3):
            try:
                self._achievements_check(bool(self.ctl.snapshot(logs=False)["running"]))
            except Exception:
                pass

    def _refresh_achievements(self) -> None:
        try:
            self.ach.refresh()
        except Exception:
            pass

    @property
    def python(self) -> str:
        """Interpreter für die Analyse-Prozesse: Einstellung > SkyTrace-venv im Datenordner > dieser Interpreter."""
        p = self.ctx.store.get("skytrace").get("python") or ""
        if p and Path(p).is_file():
            return p
        for cand in (self.home / ".venv" / "Scripts" / "python.exe", self.home / ".venv" / "bin" / "python"):
            if cand.is_file():
                return str(cand)
        return sys.executable

    def engine_env(self) -> dict:
        # ASTROFLOW_SITES: gemeinsame Ortsliste (data/settings.json) – die Engine lädt core/sites.py über den Pfad
        return {**os.environ, "SKYTRACE_HOME": str(self.home), "PYTHONIOENCODING": "utf-8", "ASTROFLOW_SITES": str(self.ctx.store.path)}

    def check_engine(self):
        """Prüft im Hintergrund, ob der Engine-Interpreter alle Analysepakete hat."""
        code = ("import importlib.util,json;"
                f"print(json.dumps([m for m in {list(ENGINE_PACKAGES)!r} if importlib.util.find_spec(m) is None]))")
        try:
            out = subprocess.run([self.python, "-c", code], capture_output=True, text=True, timeout=60,
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            import json
            missing = json.loads(out.stdout.strip() or "null")
            self.health = {"checked": True, "python": self.python, "ok": missing == [], "missing": missing or [],
                           "error": None if missing is not None else (out.stderr.strip()[-300:] or "Interpreter antwortet nicht")}
        except Exception as e:
            self.health = {"checked": True, "python": self.python, "ok": False, "missing": [], "error": str(e)}

    def router(self):
        from .api import build_router
        return build_router(self)

    def start(self):
        threading.Thread(target=self.check_engine, daemon=True).start()
        self.auto.start()
        threading.Thread(target=self._achievements_loop, daemon=True).start()

    def stop(self):
        self._stop.set()
        self.auto.stop()
        self.ctl.stop()

    def status(self) -> dict:
        s = self.ctl.snapshot(logs=False)
        return {"running": s["running"], "stopping": s["stopping"], "run": s["run"], "text": s["status"],
                "stats": s["stats"], "operation": s["operation"], "busy_jobs": int(s["running"]),
                "engine": self.health, "home": str(self.home), "auto_waiting": self.auto.waiting(),
                "achievements_new": self.ach.new()}

    def activity(self, limit: int = 30) -> list[dict]:
        from .api import list_runs, short_name
        s = self.ctl.snapshot(logs=False)
        out = self.auto.activity()
        current = bool(s["running"] or (s["finished"] and s["run"]))
        if current:
            st = s["stats"]
            out.append({"key": f"skytrace:{s['run']}", "title": "Bildfolge analysieren" if s["operation"] == "analysis" else "Kandidatenbericht aktualisieren",
                        "kind": "Spurenanalyse", "detail": s["status"],
                        "state": "running" if s["running"] else "stopped" if s["stopping"] else "error" if s.get("failed") else "done",
                        "progress": (st.get("current", 0) / st["total"]) if s["running"] and st.get("total") else None,
                        "started": s["started"], "finished": s["finished"], "href": f"#/skytrace/{s['run']}"})
        for r in list_runs(self.ctl.results)[:limit]:
            if r["id"] == s["run"] and current:
                continue
            out.append({"key": f"skytrace:{r['id']}", "title": short_name(r["title"]), "kind": "Spurenanalyse",
                        "detail": f"{r['frames']} Aufnahmen · {r['candidates']} Kandidaten", "state": "done",
                        "progress": 1, "started": r["modified"], "finished": r["modified"], "href": f"#/skytrace/{r['id']}"})
        return out
