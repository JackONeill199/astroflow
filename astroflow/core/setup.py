"""Einrichtung beim ersten Start (Assistent `#/willkommen`), Werkzeuge auf Knopfdruck, Update-Hinweis, Beenden/Neustart.

* ASTAP-Kommandozeile + Sterndatenbank + `deep_sky.csv` werden **nur auf Wunsch** und nur von der Originalquelle (SourceForge-Projekt
  „astap-program“ von Han Kleijn) geladen – nie mitgeliefert: `deep_sky.csv` ist nur für nicht-kommerzielle Nutzung frei, die Lizenz
  der Sterndatenbanken ist nicht eindeutig angegeben. Ziel ist der Ordner, den AstroFlow ohnehin durchsucht
  (Windows `%LOCALAPPDATA%\\astap`, sonst `~/astap`). Vorhandene Dateien werden nicht überschrieben.
* Update-Prüfung: höchstens einmal am Tag eine anonyme GET-Anfrage an die GitHub-Releases-API, abschaltbar
  (`settings.json` → `system.update_check`); jeder Fehler (auch 404 bei privatem Repo) bleibt still. Beim selben Abruf werden
  „Was ist neu“ (Release-Text als reiner, gekürzter Text) und die Download-Adressen der Pakete mitgespeichert – heruntergeladen wird nichts.
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
RANK = {"astap": 0, "graxpert": 1, "gpu": 2, "skytrace": 3, "demo": 9}   # Reihenfolge der Installer-Aufträge


def ranked(pend: list[str]) -> list[str]:
    """Aufträge in der Reihenfolge, in der `run_order` sie abarbeitet."""
    return sorted(pend, key=lambda x: RANK.get(x.split(":")[0], 5))


def recommended_db(telescopes: list[str]) -> str:
    """Gesamtempfehlung der Sterndatenbank: sobald ein gewähltes Gerät D50 braucht (kleines Bildfeld), D50; sonst D05."""
    return "d50" if any(TELESCOPES.get(t) == "d50" for t in telescopes) else "d05"


TASK_NAMES = {"astap": "ASTAP + Sterndatenbank", "demo": "Demo-Aufnahmen", "gpu": "GPU-Beschleunigung",
              "graxpert": "GraXpert", "skytrace": "SkyTrace-Analyse-Engine", "firewall": "Firewall-Freigabe"}


def _version_tuple(v: str) -> tuple:
    import re
    return tuple(int(x) for x in re.findall(r"\d+", v or "")[:3]) or (0,)


NOTES_MAX = 2400                                       # Zeichen „Was ist neu“, danach gekürzt
_ASSETS = {"windows": ("AstroFlow-Setup-", ".exe"), "windows_zip": ("AstroFlow-", "-Windows-portabel.zip"),
           "linux": ("AstroFlow-", "-Linux-x86_64.tar.gz")}


def release_notes(body: str, limit: int = NOTES_MAX) -> list[dict]:
    """Release-Text (Markdown) → Absätze als reiner Text `[{t, h}]` (h = Überschrift). Bilder, Links, Hervorhebungen und HTML fallen weg.
    Steht vor der ersten Überschrift nur der Download-Hinweis, beginnt „Was ist neu“ mit der ersten Überschrift."""
    import re
    text = str(body or "")[:20000].replace("\r\n", "\n")
    first = re.search(r"^#{1,6} ", text, re.M)
    if first:
        text = text[first.start():]
    out, used, para = [], 0, []

    def clean(line: str) -> str:
        line = re.sub(r"!\[[^\]\n]{0,300}\]\([^)\s]{0,500}\)", "", line)          # Bilder
        line = re.sub(r"\[([^\]\n]{0,300})\]\([^)\s]{0,500}\)", r"\1", line)     # Links → Text
        line = re.sub(r"<[^<>\n]{0,300}>", "", line)                             # HTML-Schnipsel
        line = re.sub(r"(\*\*|__|`)", "", line)
        return re.sub(r"\s+", " ", line).strip()

    def flush():
        nonlocal used
        if para and used < limit:
            t = " ".join(para)
            if used + len(t) > limit and limit - used < 120:       # kein Mini-Schnipsel am Ende
                used, t = limit, ""
            elif used + len(t) > limit:
                t = t[: max(0, limit - used)].rsplit(" ", 1)[0] + " …"
            if t.strip():
                out.append({"t": t, "h": False})
                used += len(t)
        para.clear()

    for raw in text.split("\n"):
        if used >= limit:
            break
        m = re.match(r"^(#{1,6}) (.{1,200})$", raw.strip())
        if m:
            flush()
            t = clean(m.group(2))
            if t:
                out.append({"t": t, "h": True})
                used += len(t)
            continue
        line = clean(raw)
        if not line:
            flush()
            continue
        if re.match(r"^[-*] ", raw.strip()):                                      # Aufzählung → eigener Absatz
            flush()
            line = "• " + line[2:].strip() if line[:2] in ("- ", "* ") else line
        para.append(line)
    flush()
    while out and out[-1]["h"]:                                                   # keine Überschrift ohne Text am Ende
        out.pop()
    if used >= limit and out and not out[-1]["t"].endswith("…"):
        out[-1]["t"] += " …"
    return out


def release_assets(assets) -> dict:
    """Download-Adressen der Pakete aus `assets` – nur https auf github.com, nur bekannte Dateinamen."""
    out = {}
    for a in assets or []:
        if not isinstance(a, dict):
            continue
        name, url = str(a.get("name") or ""), str(a.get("browser_download_url") or "")
        if not url.startswith("https://github.com/"):
            continue
        for key, (pre, suf) in _ASSETS.items():
            if name.startswith(pre) and name.endswith(suf) and key not in out:
                out[key] = {"name": name[:120], "url": url[:500], "size": int(a.get("size") or 0)}
    return out


def install_kind() -> str:
    """windows | linux (installiertes Paket) | docker | source – bestimmt den passenden Download."""
    if Path("/.dockerenv").exists():
        return "docker"
    if os.environ.get("ASTROFLOW_LAUNCHER") == "1" or getattr(sys, "frozen", False):
        return {"Windows": "windows", "Linux": "linux"}.get(platform.system(), "source")
    return "source"


def update_offer(info: dict) -> dict | None:
    """Für `/api/app` und „Jetzt prüfen“: nur wenn die gemeldete Version neuer als die laufende ist (nach einem Update also nicht mehr)."""
    latest = str((info or {}).get("latest") or "")
    if not latest or _version_tuple(latest) <= _version_tuple(VERSION):
        return None
    kind = install_kind()
    dl = (info.get("downloads") or {}).get(kind) if kind in ("windows", "linux") else None
    return {"latest": latest, "current": VERSION, "url": info.get("url") or RELEASES_PAGE, "newer": True,
            "checked": info.get("checked"), "published": info.get("published"), "notes": info.get("notes") or [],
            "kind": kind, "download": dl}


def drives() -> list[dict]:
    """Laufwerke mit freiem Speicher (Windows: A–Z, sonst Home, Wurzel und eingehängte Datenträger unter /media, /run/media, /mnt)."""
    out, seen = [], set()
    roots = [f"{c}:\\" for c in "CDEFGHIJKLMNOPQRSTUVWXYZ"] if os.name == "nt" else [str(Path.home()), "/", *_mounts()]
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


MOUNT_BASES = ("/media", "/run/media", "/mnt")       # USB-Platten, zweite Festplatten (udisks hängt unter /media/<Nutzer> ein)


def _mounts(bases=MOUNT_BASES) -> list[str]:
    """Eingehängte Datenträger: Unterordner von /media/<Nutzer>, /run/media/<Nutzer> und /mnt, die ein eigener Einhängepunkt sind."""
    out = []
    for base in bases:
        for d in _subdirs(Path(base)):
            if _is_mount(d):
                out.append(str(d))
            else:                                    # /media/<Nutzer>/<Datenträger>
                out += [str(e) for e in _subdirs(d) if _is_mount(e)]
    return out


def _subdirs(p: Path) -> list[Path]:
    try:
        return sorted(d for d in p.iterdir() if d.is_dir())
    except OSError:                                  # fehlt oder gehört einem anderen Nutzer
        return []


def _is_mount(p: Path) -> bool:
    try:
        return os.path.ismount(p)
    except OSError:
        return False


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
        self.order_active = False     # `run_order` arbeitet den Installer-Auftrag gerade ab

    def telescopes(self) -> list[str]:
        """Gewählte Teleskope; ältere Stände speicherten nur einen Einzelwert (`telescope`)."""
        sec = self.ctx.store.get("setup")
        lst = sec.get("telescopes")
        if not isinstance(lst, list):
            lst = [sec["telescope"]] if sec.get("telescope") else []
        return [t for t in lst if isinstance(t, str) and t in TELESCOPES]

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
            "done": bool(sec.get("done")), "launched": self.launched, "telescope": (self.telescopes() or [""])[0],
            "telescopes": self.telescopes(),
            "data": folder_info(st.data_dir), "default_data": str(launcher.default_data_dir()),
            "config": str(launcher.config_path()) if self.launched else None,
            "drives": drives(), **self.lan_state(), "port": launcher.port(),
            "platform": platform.system(), "astap": astap, "dbs": {k: v[1] for k, v in ASTAP_DBS.items()},
            "demo": {"exists": demo.exists()}, "gpu": self._gpu_state(), "components": self.components(),
            "update_check": sysc.get("update_check", True), "update": sysc.get("update") or {},
            "update_offer": update_offer(sysc.get("update") or {}),   # frisch je Aufruf: findet die Hintergrundprüfung später etwas, steht es hier
            "tray_notify": sysc.get("tray_notify", True), "report": sysc.get("report", True),
            "report_env_off": os.environ.get("ASTROFLOW_REPORT") == "0",
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
        if "telescopes" in body or "telescope" in body:
            raw = body["telescopes"] if "telescopes" in body else [body["telescope"]]
            if isinstance(raw, str):
                raw = [raw]
            lst = list(dict.fromkeys(str(t)[:40] for t in (raw or []) if str(t) in TELESCOPES))
            vals["telescopes"] = lst
            vals["telescope"] = lst[0] if lst else ""        # Einzelwert für ältere Stände
        if "done" in body:
            vals["done"] = bool(body["done"])
        if vals:
            self.ctx.store.update("setup", vals)
        if "update_check" in body:
            self.ctx.store.update("system", {"update_check": bool(body["update_check"])})
        if "report" in body:
            self.ctx.store.update("system", {"report": bool(body["report"])})
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
                                                                       "telescopes": self.telescopes(), "done": False})
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
        pend = ranked(comp.take_order()) if self.launched else []
        names = [x.split(":")[0] for x in pend]
        installed = {c["id"] for c in items if c["state"] == "installed"}
        waiting = [n for n in names if (self.tasks.get(n) or {}).get("state") not in ("running", "error") and n not in installed]
        for c in items:
            c["task"] = self.tasks.get(c["id"])
            c["pending"] = c["id"] in names
            # Platz in der Warteschlange (1 = als Nächstes); 0 = nicht in der Schlange oder läuft schon
            c["queue_pos"] = waiting.index(c["id"]) + 1 if self.order_active and c["id"] in waiting else 0
        running = next((n for n in names if (self.tasks.get(n) or {}).get("state") == "running"), None)
        return {"items": items, "pending": pend, "queue_active": self.order_active, "queue_running": running,
                "tools_dir": str(comp.tools_dir()), "telescope_db": recommended_db(self.telescopes())}

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
        if name == "graxpert" and not comp.graxpert_supported():
            raise HTTPException(400, "GraXpert installiert AstroFlow nur unter Windows und Linux (x86_64) selbst.")
        return self._run(name, self._component_job, name, option, force)

    def _component_job(self, prog, name, option, force) -> str:
        msg = comp.install(name, prog, option, force)
        for item in list(comp.take_order()) if self.launched else []:
            n, _, o = item.partition(":")
            if n == name and (not o or o == (option or "")):        # nur die tatsächlich installierte Datenbank-Option
                comp.done_order(item)                       # manuell installiert → nicht noch einmal aus der Schlange
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
        if not comp.take_order():
            return
        self.order_active = True
        try:
            for item in ranked(comp.take_order()):
                if self._stop.is_set():
                    return
                if item not in comp.take_order():            # inzwischen von Hand erledigt
                    continue
                name, _, opt = item.partition(":")
                if name == "demo" and (self.ctx.settings.input_dir / "demo").exists():
                    comp.done_order(item)
                    continue
                if (self.tasks.get(name) or {}).get("state") != "running":       # läuft sie schon (Klick), nur abwarten
                    try:
                        self.install_component(name, opt or None)
                    except HTTPException as e:
                        if (self.tasks.get(name) or {}).get("state") != "running":
                            self.tasks[name] = {"state": "error", "detail": str(e.detail), "started": time.time(), "finished": time.time()}
                            continue
                while (self.tasks.get(name) or {}).get("state") == "running" and not self._stop.wait(1):
                    pass
                if (self.tasks.get(name) or {}).get("state") == "done":
                    comp.done_order(item)
        finally:
            self.order_active = False

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
    def firewall(self, remove: bool = False) -> dict:
        if sys.platform.startswith("linux"):
            return self._firewall_linux(remove)
        if os.name != "nt":
            return {"ok": True, "detail": "Keine Windows-Firewall – ggf. die Firewall des Systems selbst freigeben."}
        if remove:
            return {"ok": False, "detail": "Entfernen unter Windows: scripts/windows/Netzwerk-freigeben.bat entfernen."}
        import subprocess
        p = launcher.port()
        cmd = (f"Remove-NetFirewallRule -DisplayName 'AstroFlow LAN' -ErrorAction SilentlyContinue; "
               f"New-NetFirewallRule -DisplayName 'AstroFlow LAN' -Description 'AstroFlow-Weboberfläche im Heimnetz (nur privates Profil, nur lokales Subnetz)' "
               f"-Direction Inbound -Action Allow -Protocol TCP -LocalPort {p} -Profile Private -RemoteAddress LocalSubnet | Out-Null")
        subprocess.Popen(["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command",
                          f"Start-Process powershell -Verb RunAs -WindowStyle Hidden -ArgumentList '-NoProfile','-Command',\"{cmd}\""],
                         creationflags=0x08000000)
        return {"ok": True, "detail": "Windows fragt nach Administratorrechten. Danach ist AstroFlow im Heimnetz erreichbar."}

    def _firewall_linux(self, remove: bool) -> dict:
        """ufw/firewalld per pkexec (feste Befehle, s. core/firewall.py); sonst den Befehl zum Selbst-Ausführen anzeigen."""
        from . import firewall as fw
        p = launcher.port()
        try:
            plan = fw.plan(p, remove)
        except Exception:
            import traceback
            traceback.print_exc()
            return {"ok": False, "detail": "Die Firewall ließ sich nicht prüfen."}
        if not plan.get("kind"):
            return {"ok": True, "detail": f"Keine aktive Firewall (ufw/firewalld) gefunden – nichts zu tun. Läuft eine andere, dort "
                                          f"TCP-Port {p} für das Heimnetz freigeben."}
        if not plan.get("net"):
            return {"ok": False, "detail": "Kein privates Heimnetz (IPv4) gefunden – Freigabe nicht möglich."}
        what = "Freigabe entfernen" if remove else "Freigabe"
        if not plan.get("argv"):
            return {"ok": True, "manual": plan["manual"], "detail": f"{what} bitte im Terminal ausführen: {plan['manual']}"}

        def job(prog):
            prog(0.1, "Das System fragt nach dem Passwort …")
            ok, msg = fw.execute(plan["argv"])
            if not ok:
                raise RuntimeError(msg)
            return (f"Freigabe entfernt ({plan['kind']})." if remove else
                    f"AstroFlow ist im Heimnetz {plan['net']} erreichbar ({plan['kind']}, TCP-Port {p}).")
        self._run("firewall", job)
        return {"ok": True, "pending": True, "manual": plan["manual"],
                "detail": f"Das System fragt nach dem Passwort ({plan['kind']}, Heimnetz {plan['net']}, TCP-Port {p})."}

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
                url = str(d.get("html_url") or "")
                info.update(latest=latest, url=url if url.startswith("https://github.com/") else RELEASES_PAGE,
                            newer=_version_tuple(latest) > _version_tuple(VERSION),
                            published=str(d.get("published_at") or "")[:10] or None,
                            notes=release_notes(d.get("body") or ""), downloads=release_assets(d.get("assets")))
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
    def post_firewall(request: Request, body: dict = Body({})):
        security.require_local(request, "Firewall freigeben")
        return setup.firewall(bool(body.get("remove")))

    @r.get("/system/update")
    def get_update():
        info = setup.ctx.store.get("system").get("update") or {}
        return {"checked": info.get("checked"), "latest": info.get("latest"), "update": update_offer(info)}

    @r.post("/system/update/check")
    def post_update_check():
        """„Jetzt prüfen“: fragt sofort an (auch wenn die tägliche Prüfung abgeschaltet ist)."""
        info = setup.check_update(force=True)
        return {"checked": info.get("checked"), "latest": info.get("latest"), "update": update_offer(info)}

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
