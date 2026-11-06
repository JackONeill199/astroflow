"""Einrichtung beim ersten Start (Assistent `#/willkommen`), Werkzeuge auf Knopfdruck, Update-Hinweis, Beenden/Neustart.

* ASTAP-Kommandozeile + Sterndatenbank + `deep_sky.csv` werden **nur auf Wunsch** und nur von der Originalquelle (SourceForge-Projekt
  „astap-program“ von Han Kleijn) geladen – nie mitgeliefert: `deep_sky.csv` ist nur für nicht-kommerzielle Nutzung frei, die Lizenz
  der Sterndatenbanken ist nicht eindeutig angegeben. Ziel ist der Ordner, den AstroFlow ohnehin durchsucht
  (Windows `%LOCALAPPDATA%\\astap`, sonst `~/astap`). Vorhandene Dateien werden nicht überschrieben.
* Update-Prüfung: höchstens einmal am Tag eine anonyme GET-Anfrage an die GitHub-Releases-API, abschaltbar
  (`settings.json` → `system.update_check`); jeder Fehler (auch 404 bei privatem Repo) bleibt still.
"""
from __future__ import annotations

import io
import os
import platform
import shutil
import signal
import sys
import threading
import time
import zipfile
from pathlib import Path

from fastapi import APIRouter, Body, HTTPException

from . import launcher
from .. import __version__ as VERSION

REPO = "JackONeill199/astroflow"
RELEASES_API = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPO}/releases"
UPDATE_EVERY = 24 * 3600

SF = "https://sourceforge.net/projects/astap-program/files/{}/download"
ASTAP_CLI = {   # (Betriebssystem, Architektur) → Datei auf SourceForge
    ("Windows", "AMD64"): "windows_installer/astap_command-line_version_win64.zip",
    ("Linux", "x86_64"): "linux_installer/astap_command-line_version_Linux_amd64.zip",
    ("Linux", "aarch64"): "linux_installer/astap_command-line_version_Linux_aarch64.zip",
    ("Darwin", "arm64"): "macOS installer/astap_command-line_version_macOS_M1.zip",
    ("Darwin", "x86_64"): "macOS installer/astap_command-line_version_macOS_x86_64.zip",
}
ASTAP_CATALOG = "windows_installer/astapwin32.zip"          # enthält deep_sky.csv (plattformunabhängig, ~1 MB)
ASTAP_DBS = {"d05": ("star_databases/d05_star_database.zip", 102), "d50": ("star_databases/d50_star_database.zip", 900)}
TELESCOPES = {   # Auswahl im Assistenten → empfohlene Sterndatenbank (Bildfeld ≥ 0,6° → D05)
    "stellina": "d05", "vespera": "d05", "seestar": "d05", "dwarf": "d05", "andere": "d50",
}


def astap_dir() -> Path:
    if os.name == "nt" and os.environ.get("LOCALAPPDATA"):
        return Path(os.environ["LOCALAPPDATA"]) / "astap"
    return Path.home() / "astap"


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
        cfg = launcher.read_config() if self.launched else {}
        st = self.ctx.settings
        stacking = self.app_state.modules.get("stacking")
        solver = getattr(getattr(stacking, "eng", None), "pl", None)
        cfg_s = getattr(solver, "solver_cfg", None)
        astap = {"path": cfg_s.astap_path() if cfg_s else None, "databases": cfg_s.astap_databases() if cfg_s else [],
                 "target": str(astap_dir()), "supported": (platform.system(), platform.machine()) in ASTAP_CLI}
        demo = st.input_dir / "demo"
        return {
            "pending": bool(self.launched and not sec.get("done")),
            "done": bool(sec.get("done")), "launched": self.launched, "telescope": sec.get("telescope") or "",
            "data": folder_info(st.data_dir), "default_data": str(launcher.default_data_dir()),
            "config": str(launcher.config_path()) if self.launched else None,
            "drives": drives(), "lan": bool(cfg.get("lan")), "port": launcher.port(),
            "platform": platform.system(), "astap": astap, "dbs": {k: v[1] for k, v in ASTAP_DBS.items()},
            "demo": {"exists": demo.exists()}, "gpu": self._gpu_state(),
            "update_check": sysc.get("update_check", True), "update": sysc.get("update") or {},
            "version": VERSION, "tasks": dict(self.tasks),
        }

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
        restart = False
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
                restart = True
        if "lan" in body and self.launched and bool(body["lan"]) != bool(launcher.read_config().get("lan")):
            launcher.write_config({"lan": bool(body["lan"])})       # Server lauscht danach im Heimnetz bzw. nur lokal
            restart = True
        if restart:
            self.restart()
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
        names = {"astap": "ASTAP + Sterndatenbank", "demo": "Demo-Aufnahmen", "gpu": "GPU-Beschleunigung"}
        return [{"key": f"setup:{k}", "title": names.get(k, k), "kind": "Einrichtung", "detail": t.get("detail", ""),
                 "state": t["state"], "progress": t.get("progress"), "started": t.get("started"), "finished": t.get("finished"),
                 "href": "#/willkommen"} for k, t in self.tasks.items()]

    # ASTAP ---------------------------------------------------------------------
    def install_astap(self, db: str):
        if db not in ASTAP_DBS:
            raise HTTPException(400, "Unbekannte Sterndatenbank.")
        key = (platform.system(), platform.machine())
        if key not in ASTAP_CLI:
            raise HTTPException(400, f"Für {key[0]} {key[1]} gibt es keine ASTAP-Kommandozeile – bitte ASTAP selbst installieren.")
        return self._run("astap", self._astap_job, db)

    def _astap_job(self, prog, db: str) -> str:
        import httpx
        dest = astap_dir()
        dest.mkdir(parents=True, exist_ok=True)
        steps = []
        cli = dest / ("astap_cli.exe" if os.name == "nt" else "astap_cli")
        if not cli.exists():
            prog(0.01, "Lade ASTAP-Kommandozeile …")
            data = _download(SF.format(ASTAP_CLI[(platform.system(), platform.machine())]), lambda f: prog(0.01 + 0.04 * f, "Lade ASTAP-Kommandozeile …"))
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                name = next((n for n in z.namelist() if Path(n).name.startswith("astap_cli")), None)
                if not name:
                    raise RuntimeError("ASTAP-Paket enthält keine astap_cli.")
                cli.write_bytes(z.read(name))
            if os.name != "nt":
                cli.chmod(0o755)
            steps.append("Kommandozeile")
        if not (dest / "deep_sky.csv").exists():
            prog(0.06, "Lade Objektkatalog deep_sky.csv …")
            data = _download(SF.format(ASTAP_CATALOG), lambda f: prog(0.06 + 0.04 * f, "Lade Objektkatalog deep_sky.csv …"))
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                for n in z.namelist():
                    if Path(n).name in ("deep_sky.csv", "acknowledgement of databases.txt"):
                        (dest / Path(n).name).write_bytes(z.read(n))
            steps.append("Objektkatalog")
        if not list(dest.glob(f"{db}_0101.*")):
            mb = ASTAP_DBS[db][1]
            prog(0.10, f"Lade Sterndatenbank {db.upper()} (~{mb} MB) …")
            tmp = dest / f".{db}.zip.part"
            _download(SF.format(ASTAP_DBS[db][0]), lambda f: prog(0.10 + 0.8 * f, f"Lade Sterndatenbank {db.upper()} (~{mb} MB) … {f*100:.0f} %"),
                      to=tmp)
            prog(0.92, "Entpacke Sterndatenbank …")
            with zipfile.ZipFile(tmp) as z:
                for n in z.namelist():
                    base = Path(n).name
                    if base.lower().startswith(db) and not n.endswith("/"):
                        with z.open(n) as src, open(dest / base, "wb") as out:
                            shutil.copyfileobj(src, out, 1 << 20)
            tmp.unlink(missing_ok=True)
            if not list(dest.glob(f"{db}_0101.*")):
                raise RuntimeError("Die Sterndatenbank wurde nicht gefunden – Paketaufbau hat sich geändert?")
            steps.append(f"Sterndatenbank {db.upper()}")
        prog(0.97, "Prüfe ASTAP …")
        import subprocess
        try:
            subprocess.run([str(cli), "-h"], capture_output=True, timeout=30,
                           creationflags=0x08000000 if os.name == "nt" else 0)
        except OSError as e:
            raise RuntimeError(f"ASTAP lässt sich nicht starten: {e}")
        from ..modules.stacking import platesolve
        platesolve._AVAILABLE.clear()
        try:
            from ..modules.stacking import objects
            objects._EXT = None
        except Exception:
            pass
        return ("Installiert: " + ", ".join(steps) if steps else "Bereits vorhanden.") + f" Ordner: {dest}"

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

    # GPU -----------------------------------------------------------------------
    def install_gpu(self):
        return self._run("gpu", self._gpu_job)

    def _gpu_job(self, prog) -> str:
        import subprocess
        prog(0.1, "Installiere pyopencl …")
        r = subprocess.run([sys.executable.replace("pythonw.exe", "python.exe"), "-m", "pip", "install", "--disable-pip-version-check", "pyopencl"],
                           capture_output=True, text=True, timeout=900, creationflags=0x08000000 if os.name == "nt" else 0)
        if r.returncode:
            raise RuntimeError("pip: " + (r.stderr or r.stdout).strip()[-250:])
        return "pyopencl installiert – wirkt nach einem Neustart (Einstellungen → Bilder → System)."

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
    def shutdown(self, restart: bool = False):
        def go():
            time.sleep(0.5)                            # Antwort noch ausliefern
            if restart:
                launcher.restart_detached(launcher.port())
            try:
                signal.raise_signal(signal.SIGINT)    # uvicorn fährt sauber herunter (Module stoppen, Checkpoints)
            except Exception:
                pass
            time.sleep(20)
            os._exit(0)
        threading.Thread(target=go, daemon=True).start()

    def restart(self):
        if not self.launched:
            return
        self.shutdown(restart=True)


def _download(url: str, prog, to: Path | None = None) -> bytes | None:
    import httpx
    with httpx.stream("GET", url, follow_redirects=True, timeout=httpx.Timeout(60, read=120),
                      headers={"User-Agent": "AstroFlow"}) as r:
        r.raise_for_status()
        total = int(r.headers.get("content-length") or 0)
        if "text/html" in (r.headers.get("content-type") or ""):
            raise RuntimeError("Download-Seite statt Datei erhalten – SourceForge nicht erreichbar?")
        got, buf = 0, (open(to, "wb") if to else io.BytesIO())
        try:
            for chunk in r.iter_bytes(1 << 20):
                buf.write(chunk)
                got += len(chunk)
                if total:
                    prog(min(1.0, got / total))
        finally:
            if to:
                buf.close()
        return None if to else buf.getvalue()


def build_router(setup: Setup) -> APIRouter:
    r = APIRouter()

    @r.get("/setup")
    def get_setup():
        return setup.state()

    @r.post("/setup")
    def post_setup(body: dict = Body(...)):
        return setup.save(body)

    @r.get("/setup/folder")
    def get_folder(path: str):
        return folder_info(Path(path))

    @r.post("/setup/astap")
    def post_astap(body: dict = Body({})):
        return setup.install_astap(str(body.get("db") or "d05").lower())

    @r.post("/setup/demo")
    def post_demo():
        return setup.make_demo()

    @r.post("/setup/gpu")
    def post_gpu():
        return setup.install_gpu()

    @r.post("/setup/firewall")
    def post_firewall():
        return setup.firewall()

    @r.get("/system/update")
    def get_update(force: bool = False):
        return setup.check_update(force)

    @r.post("/system/shutdown")
    def post_shutdown():
        setup.shutdown()
        return {"ok": True}

    @r.post("/system/restart")
    def post_restart():
        if not setup.launched:
            raise HTTPException(400, "Neustart nur bei Start über AstroFlow (Startmenü/Starter) möglich.")
        setup.restart()
        return {"ok": True}

    return r
