"""Einrichtung beim ersten Start (Assistent `#/willkommen`), Werkzeuge auf Knopfdruck, Update-Hinweis, Beenden/Neustart.

* ASTAP-Kommandozeile + Sterndatenbank + `deep_sky.csv` werden **nur auf Wunsch** und nur von der Originalquelle (SourceForge-Projekt
  „astap-program“ von Han Kleijn) geladen – nie mitgeliefert: `deep_sky.csv` ist nur für nicht-kommerzielle Nutzung frei, die Lizenz
  der Sterndatenbanken ist nicht eindeutig angegeben. Ziel ist der Ordner, den AstroFlow ohnehin durchsucht
  (Windows `%LOCALAPPDATA%\\astap`, sonst `~/astap`). Vorhandene Dateien werden nicht überschrieben.
* Update-Prüfung: höchstens einmal am Tag eine anonyme GET-Anfrage an die GitHub-Releases-API, abschaltbar
  (`settings.json` → `system.update_check`); jeder Fehler (auch 404 bei privatem Repo) bleibt still.
"""
from __future__ import annotations

import os
import platform
import shutil
import signal
import sys
import threading
import time
from pathlib import Path

from fastapi import APIRouter, Body, HTTPException, Request

from . import launcher
from . import security
from .. import __version__ as VERSION

REPO = "JackONeill199/astroflow"
RELEASES_API = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPO}/releases"
UPDATE_EVERY = 24 * 3600

from .components import ASTAP_CLI, ASTAP_DBS, astap_dir, system as _system   # noqa: F401 (Rückwärtskompatibilität)
from . import components as comp

TELESCOPES = {   # Auswahl im Assistenten → empfohlene Sterndatenbank (Bildfeld ≥ 0,6° → D05)
    "stellina": "d05", "vespera": "d05", "seestar": "d05", "dwarf": "d05", "andere": "d50",
}
TASK_NAMES = {"astap": "ASTAP + Sterndatenbank", "demo": "Demo-Aufnahmen", "gpu": "GPU-Beschleunigung",
              "graxpert": "GraXpert", "skytrace": "SkyTrace-Analyse-Engine"}


def _version_tuple(v: str) -> tuple:
    import re
    return tuple(int(x) for x in re.findall(r"\d+", v or "")[:3]) or (0,)


def drives() -> list[dict]:
    """Laufwerke mit freiem Speicher (Windows: A–Z, sonst Home + Wurzel)."""
    out, seen = [], set()
    roots = [f"{c}:\\" for c in "CDEFGHIJKLMNOPQRSTUVWXYZ"] if os.name == "nt" else [str(Path.home()), "/"]
    for r in roots:
        try:
            if os.name == "nt" and not os.path.exists(r):
                continue
            du = shutil.disk_usage(r)
        except OSError:
            continue
        if (du.total, du.free) in seen and os.name != "nt":
            continue
        seen.add((du.total, du.free))
        out.append({"path": r, "free": du.free, "total": du.total})
    return out


def folder_info(path: Path) -> dict:
    p = Path(path)
    probe = p
    while not probe.exists() and probe.parent != probe:
        probe = probe.parent
    try:
        du = shutil.disk_usage(probe)
        free, total = du.free, du.total
    except OSError:
        free = total = None
    return {"path": str(p), "exists": p.exists(), "free": free, "total": total, "cloud": launcher.cloud_folder(p)}


class Setup:
    def __init__(self, ctx, app_state):
        self.ctx, self.app_state = ctx, app_state
        self.tasks: dict[str, dict] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()

    # ---- Zustand ------------------------------------------------------------
    @property
    def launched(self) -> bool:
        """Über den Starter (`astroflow start`, Installer, portables Paket) gestartet – nur dann ist der Datenordner wählbar."""
        return os.environ.get("ASTROFLOW_LAUNCHER") == "1"

    def state(self) -> dict:
        sec = self.ctx.store.get("setup")
        sysc = self.ctx.store.get("system")
        st = self.ctx.settings
        stacking = self.app_state.modules.get("stacking")
        solver = getattr(getattr(stacking, "eng", None), "pl", None)
        cfg_s = getattr(solver, "solver_cfg", None)
        astap = {"path": cfg_s.astap_path() if cfg_s else None, "databases": cfg_s.astap_databases() if cfg_s else [],
                 "target": str(astap_dir()), "supported": _system() in ASTAP_CLI}
        demo = st.input_dir / "demo"
        return {
            "pending": bool(self.launched and not sec.get("done")),
            "done": bool(sec.get("done")), "launched": self.launched, "telescope": sec.get("telescope") or "",
            "data": folder_info(st.data_dir), "default_data": str(launcher.default_data_dir()),
            "config": str(launcher.config_path()) if self.launched else None,
            "drives": drives(), **self.lan_state(), "port": launcher.port(),
            "platform": platform.system(), "astap": astap, "dbs": {k: v[1] for k, v in ASTAP_DBS.items()},
            "demo": {"exists": demo.exists()}, "gpu": self._gpu_state(), "components": self.components(),
            "update_check": sysc.get("update_check", True), "update": sysc.get("update") or {},
            "tray_notify": sysc.get("tray_notify", True),
            "version": VERSION, "tasks": dict(self.tasks),
        }

    def lan_state(self) -> dict:
        """Heimnetz: eingestellt (`lan`), gerade wirksam (`lan_active`), beim Update übernommen (`lan_migrated`).
        Starter: `config.json` → `lan` (Neustart automatisch); `serve`/Start.bat: `settings.json` → `lan.enabled` (Neustart von Hand)."""
        sec = self.ctx.store.get("lan")
        lan = bool(launcher.read_config().get("lan")) if self.launched else bool(sec.get("enabled"))
        return {"lan": lan, "lan_active": security.bind_is_lan(), "lan_migrated": bool(sec.get("migrated")) and not self.launched}

    def _gpu_state(self) -> dict:
        try:
            import importlib.util
            return {"installed": importlib.util.find_spec("pyopencl") is not None}
        except Exception:
            return {"installed": False}

    def save(self, body: dict) -> dict:
        vals = {}
        if "telescope" in body:
            vals["telescope"] = str(body["telescope"] or "")[:40]
        if "done" in body:
            vals["done"] = bool(body["done"])
        if vals:
            self.ctx.store.update("setup", vals)
        if "update_check" in body:
            self.ctx.store.update("system", {"update_check": bool(body["update_check"])})
        if "tray_notify" in body:
            self.ctx.store.update("system", {"tray_notify": bool(body["tray_notify"])})
        restart, target = False, None
        if body.get("data_dir"):
            if not self.launched:
                raise HTTPException(400, "Der Datenordner ist bei dieser Instanz fest vorgegeben (--data bzw. ASTROFLOW_DATA).")
            new = Path(str(body["data_dir"])).expanduser()
            if not new.is_absolute():
                raise HTTPException(400, "Bitte einen vollständigen Pfad wählen.")
            if new.resolve() != self.ctx.settings.data_dir.resolve():
                try:
                    new.mkdir(parents=True, exist_ok=True)
                except OSError as e:
                    raise HTTPException(400, f"Ordner lässt sich nicht anlegen: {e}")
                # Einrichtung im neuen Ordner fortsetzen (Teleskopwahl mitnehmen)
                from .store import SettingsStore
                SettingsStore(new / "settings.json").update("setup", {"telescope": self.ctx.store.get("setup").get("telescope"),
                                                                       "done": False})
                launcher.write_config({"data_dir": str(new.resolve())})
                restart, target = True, new.resolve()
        if "lan" in body and self.launched and bool(body["lan"]) != bool(launcher.read_config().get("lan")):
            launcher.write_config({"lan": bool(body["lan"])})       # Server lauscht danach im Heimnetz bzw. nur lokal
            restart = True
        elif "lan" in body and not self.launched:                   # serve/Start.bat: gilt nach dem nächsten Start
            self.ctx.store.update("lan", {"enabled": bool(body["lan"]), "migrated": None})
        if restart:
            self.restart(target)
        return {**self.state(), "restart": restart}

    # ---- Hintergrundaufgaben -------------------------------------------------
    def _run(self, key: str, fn, *args):
        with self._lock:
            if (self.tasks.get(key) or {}).get("state") == "running":
                raise HTTPException(409, "Läuft bereits.")
            self.tasks[key] = {"state": "running", "progress": 0.0, "detail": "Startet …", "started": time.time()}

        def go():
            try:
                detail = fn(lambda f, d: self.tasks[key].update(progress=round(f, 3), detail=d), *args)
                self.tasks[key].update(state="done", progress=1.0, detail=detail or "Fertig.", finished=time.time())
            except Exception as e:
                self.tasks[key].update(state="error", detail=str(e)[:300], finished=time.time())
        threading.Thread(target=go, daemon=True, name=f"setup-{key}").start()
        return self.tasks[key]

    def activity(self) -> list[dict]:
        return [{"key": f"setup:{k}", "title": TASK_NAMES.get(k, k), "kind": "Einrichtung", "detail": t.get("detail", ""),
                 "state": t["state"], "progress": t.get("progress"), "started": t.get("started"), "finished": t.get("finished"),
                 "href": "#/willkommen"} for k, t in self.tasks.items()]

    # Komponenten -----------------------------------------------------------------
    def _solver_cfg(self):
        stacking = self.app_state.modules.get("stacking")
        return getattr(getattr(getattr(stacking, "eng", None), "pl", None), "solver_cfg", None)

    def _skytrace_health(self):
        return getattr(self.app_state.modules.get("skytrace"), "health", None)

    def components(self) -> dict:
        items = comp.status(self._solver_cfg(), self._skytrace_health())
        pend = comp.take_order() if self.launched else []
        for c in items:
            c["task"] = self.tasks.get(c["id"])
            c["pending"] = any(x.split(":")[0] == c["id"] for x in pend)
        return {"items": items, "pending": pend, "tools_dir": str(comp.tools_dir()),
                "telescope_db": TELESCOPES.get(self.ctx.store.get("setup").get("telescope") or "", "d05")}

    def install_component(self, name: str, option: str | None = None, force: bool = False):
        if name == "demo":
            return self.make_demo()
        if name not in ("astap", "graxpert", "gpu", "skytrace"):
            raise HTTPException(400, comp.INFO.get(name, {}).get("license") if name == "starnet" else "Unbekannte Komponente.")
        if name == "astap":
            option = (option or "d05").lower()
            if option not in ASTAP_DBS:
                raise HTTPException(400, "Unbekannte Sterndatenbank.")
            if _system() not in ASTAP_CLI:
                raise HTTPException(400, f"Für {_system()[0]} {_system()[1]} gibt es keine ASTAP-Kommandozeile – bitte ASTAP selbst installieren.")
        if name == "graxpert" and os.name != "nt":
            raise HTTPException(400, "GraXpert installiert AstroFlow nur unter Windows selbst.")
        return self._run(name, self._component_job, name, option, force)

    def _component_job(self, prog, name, option, force) -> str:
        msg = comp.install(name, prog, option, force)
        if name == "skytrace" and self.app_state.modules.get("skytrace"):
            try:
                self.app_state.modules["skytrace"].check_engine()      # Engine-Zustand sofort neu prüfen
            except Exception:
                pass
        return msg

    def remove_component(self, name: str) -> dict:
        if (self.tasks.get(name) or {}).get("state") == "running":
            raise HTTPException(409, "Läuft gerade.")
        msg = comp.remove(name)
        comp._refresh_astap()
        return {"detail": msg}

    def run_order(self):
        """Vom Installer gewählte Komponenten nacheinander installieren (Hintergrund, Fortschritt in der Aktivität).
        Fehlschläge bleiben in `components_pending` und werden im Assistenten/den Einstellungen erneut angeboten;
        die Demo kommt zuletzt, damit sie GraXpert und die SkyTrace-Engine schon nutzt."""
        if not self.launched or os.environ.get("ASTROFLOW_COMPONENTS_AUTO") == "0":
            return
        pend = comp.take_order()
        if not pend:
            return
        rank = {"astap": 0, "graxpert": 1, "gpu": 2, "skytrace": 3, "demo": 9}
        for item in sorted(pend, key=lambda x: rank.get(x.split(":")[0], 5)):
            if self._stop.is_set():
                return
            name, _, opt = item.partition(":")
            if name == "demo" and (self.ctx.settings.input_dir / "demo").exists():
                comp.done_order(item)
                continue
            try:
                self.install_component(name, opt or None)
            except HTTPException as e:
                self.tasks[name] = {"state": "error", "detail": str(e.detail), "started": time.time(), "finished": time.time()}
                continue
            while (self.tasks.get(name) or {}).get("state") == "running" and not self._stop.wait(1):
                pass
            if (self.tasks.get(name) or {}).get("state") == "done":
                comp.done_order(item)

    # Rückwärtskompatibel (Assistent 0.33)
    def install_astap(self, db: str):
        return self.install_component("astap", db)

    def install_gpu(self):
        return self.install_component("gpu")

    # Demo ----------------------------------------------------------------------
    def make_demo(self):
        return self._run("demo", self._demo_job)

    def _demo_job(self, prog) -> str:
        from ..modules.stacking.demo import generate
        out = self.ctx.settings.input_dir / "demo"
        prog(0.1, "Erzeuge synthetische Aufnahmen …")
        generate(out, n_lights=30)
        prog(0.8, "Lese Aufnahmen ein …")
        eng = getattr(self.app_state.modules.get("stacking"), "eng", None)
        if eng is not None:
            eng.scan_and_queue()
        return "Demo-Aufnahmen liegen bereit und werden verarbeitet."

    # Heimnetz ----------------------------------------------------------------------
    def firewall(self) -> dict:
        if os.name != "nt":
            return {"ok": True, "detail": "Keine Windows-Firewall – ggf. die Firewall des Systems selbst freigeben."}
        import subprocess
        p = launcher.port()
        cmd = (f"Remove-NetFirewallRule -DisplayName 'AstroFlow LAN' -ErrorAction SilentlyContinue; "
               f"New-NetFirewallRule -DisplayName 'AstroFlow LAN' -Description 'AstroFlow-Weboberfläche im Heimnetz (nur privates Profil, nur lokales Subnetz)' "
               f"-Direction Inbound -Action Allow -Protocol TCP -LocalPort {p} -Profile Private -RemoteAddress LocalSubnet | Out-Null")
        subprocess.Popen(["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command",
                          f"Start-Process powershell -Verb RunAs -WindowStyle Hidden -ArgumentList '-NoProfile','-Command',\"{cmd}\""],
                         creationflags=0x08000000)
        return {"ok": True, "detail": "Windows fragt nach Administratorrechten. Danach ist AstroFlow im Heimnetz erreichbar."}

    # Updates -----------------------------------------------------------------------
    def check_update(self, force: bool = False) -> dict:
        sysc = self.ctx.store.get("system")
        if not sysc.get("update_check", True) and not force:
            return sysc.get("update") or {}
        last = (sysc.get("update") or {}).get("checked") or 0
        if not force and time.time() - last < UPDATE_EVERY:
            return sysc.get("update") or {}
        info = {"checked": time.time()}
        try:
            import httpx
            r = httpx.get(RELEASES_API, timeout=10, headers={"Accept": "application/vnd.github+json", "User-Agent": "AstroFlow"})
            if r.status_code == 200:
                d = r.json()
                latest = str(d.get("tag_name") or "").lstrip("v")
                info.update(latest=latest, url=d.get("html_url") or RELEASES_PAGE,
                            newer=_version_tuple(latest) > _version_tuple(VERSION))
        except Exception:
            pass                                       # still: kein Netz, privates Repo (404), Ratenlimit …
        self.ctx.store.update("system", {"update": info})
        return info

    def update_loop(self):
        if os.environ.get("ASTROFLOW_UPDATE_CHECK") == "0":
            return
        while not self._stop.wait(20):
            try:
                self.check_update()
            except Exception:
                pass
            if self._stop.wait(3600):
                return

    # Beenden / Neustart --------------------------------------------------------------
    def shutdown(self, restart: bool = False, data: Path | None = None):
        def go():
            time.sleep(0.5)                            # Antwort noch ausliefern
            if restart:
                launcher.restart_detached(launcher.port(), data or self.ctx.settings.data_dir)
            else:
                from . import tray
                tray.request_stop()                    # Symbol im Infobereich mit beenden (beim Neustart bleibt es)
            try:
                signal.raise_signal(signal.SIGINT)    # uvicorn fährt sauber herunter (Module stoppen, Checkpoints)
            except Exception:
                pass
            time.sleep(20)
            os._exit(0)
        threading.Thread(target=go, daemon=True).start()

    def restart(self, data: Path | None = None):
        """Neustart über den Starter – im laufenden Datenordner, außer `data` (Ordnerwechsel im Assistenten) gibt einen neuen vor."""
        if not self.launched:
            return
        self.shutdown(restart=True, data=data)


def _download(url: str, prog, to: Path | None = None) -> bytes | None:
    return comp.download(url, prog, to)


def build_router(setup: Setup) -> APIRouter:
    r = APIRouter()

    @r.get("/setup")
    def get_setup():
        return setup.state()

    @r.post("/setup")
    def post_setup(request: Request, body: dict = Body(...)):
        if "data_dir" in body or "lan" in body:
            security.require_local(request, "Datenordner und Heimnetz")
        return setup.save(body)

    @r.get("/setup/folder")
    def get_folder(path: str):
        return folder_info(Path(path))

    @r.post("/setup/astap")
    def post_astap(request: Request, body: dict = Body({})):
        security.require_local(request, "Komponenten installieren")
        return setup.install_astap(str(body.get("db") or "d05").lower())

    @r.post("/setup/demo")
    def post_demo():
        return setup.make_demo()

    @r.post("/setup/gpu")
    def post_gpu(request: Request):
        security.require_local(request, "Komponenten installieren")
        return setup.install_gpu()

    @r.get("/components")
    def get_components():
        return setup.components()

    @r.post("/components/install")
    def post_component(request: Request, body: dict = Body(...)):
        security.require_local(request, "Komponenten installieren")
        return setup.install_component(str(body.get("id") or ""), body.get("option"), bool(body.get("force")))

    @r.post("/components/remove")
    def post_component_remove(request: Request, body: dict = Body(...)):
        security.require_local(request, "Komponenten entfernen")
        return setup.remove_component(str(body.get("id") or ""))

    @r.post("/setup/firewall")
    def post_firewall(request: Request):
        security.require_local(request, "Firewall freigeben")
        return setup.firewall()

    @r.get("/system/update")
    def get_update(force: bool = False):
        return setup.check_update(force)

    @r.post("/system/shutdown")
    def post_shutdown(request: Request):
        security.require_local(request, "AstroFlow beenden")
        setup.shutdown()
        return {"ok": True}

    @r.post("/system/restart")
    def post_restart(request: Request):
        security.require_local(request, "Neu starten")
        if not setup.launched:
            raise HTTPException(400, "Neustart nur bei Start über AstroFlow (Startmenü/Starter) möglich.")
        setup.restart()
        return {"ok": True}

    return r
