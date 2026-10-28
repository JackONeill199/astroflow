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
    scripts = ("evidence.js", "skytrace.js")
    styles = ("evidence.css", "skytrace.css")

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
        self.ctl = Controller(self)

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
        return {**os.environ, "SKYTRACE_HOME": str(self.home), "PYTHONIOENCODING": "utf-8"}

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

    def stop(self):
        self.ctl.stop()

    def status(self) -> dict:
        s = self.ctl.snapshot(logs=False)
        return {"running": s["running"], "stopping": s["stopping"], "run": s["run"], "text": s["status"],
                "stats": s["stats"], "operation": s["operation"], "busy_jobs": int(s["running"]),
                "engine": self.health, "home": str(self.home)}

    def activity(self, limit: int = 30) -> list[dict]:
        from .api import list_runs, short_name
        s = self.ctl.snapshot(logs=False)
        out = []
        if s["running"] or (s["finished"] and s["run"]):
            st = s["stats"]
            out.append({"key": f"skytrace:{s['run']}", "title": "Bildfolge analysieren" if s["operation"] == "analysis" else "Kandidatenbericht aktualisieren",
                        "kind": "Spurenanalyse", "detail": s["status"],
                        "state": "running" if s["running"] else "stopped" if s["stopping"] else "error" if s.get("failed") else "done",
                        "progress": (st.get("current", 0) / st["total"]) if s["running"] and st.get("total") else None,
                        "started": s["started"], "finished": s["finished"], "href": f"#/skytrace/{s['run']}"})
        for r in list_runs(self.ctl.results)[:limit]:
            if r["id"] == s["run"] and out:
                continue
            out.append({"key": f"skytrace:{r['id']}", "title": short_name(r["title"]), "kind": "Spurenanalyse",
                        "detail": f"{r['frames']} Aufnahmen · {r['candidates']} Kandidaten", "state": "done",
                        "progress": 1, "started": r["modified"], "finished": r["modified"], "href": f"#/skytrace/{r['id']}"})
        return out
