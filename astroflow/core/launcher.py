"""Starter für die installierte Version: `astroflow` (= `astroflow start`) startet den Server im Hintergrund und öffnet den Browser,
`astroflow stop` beendet ihn. Datenordner und Port stehen in einer kleinen Konfigurationsdatei außerhalb des Datenordners
(Windows `%APPDATA%\\AstroFlow\\config.json`, sonst `~/.config/astroflow/config.json`; portabel: `%ASTROFLOW_HOME%\\config.json`).

Bestehende Instanzen (`astroflow serve --data …`, `ASTROFLOW_DATA`, `Start.bat` mit `./data`) nutzen diesen Weg nicht."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

DEFAULT_PORT = 8766
CLOUD_MARKERS = ("onedrive", "dropbox", "icloud", "google drive", "googledrive", "nextcloud", "pcloud", "megasync")


def home() -> Path | None:
    """Portable Installation: Ordner aus ASTROFLOW_HOME (setzt `AstroFlow.cmd`)."""
    h = os.environ.get("ASTROFLOW_HOME")
    return Path(h) if h else None


def config_path() -> Path:
    if home():
        return home() / "config.json"
    if os.name == "nt" and os.environ.get("APPDATA"):
        return Path(os.environ["APPDATA"]) / "AstroFlow" / "config.json"
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "astroflow" / "config.json"


def default_data_dir() -> Path:
    """Standard-Datenordner: `%USERPROFILE%\\AstroFlow` bzw. `~/AstroFlow` – bewusst nicht unter „Dokumente“
    (dort oft per OneDrive synchronisiert, Stacks sind viele GB). Portabel: `<Programmordner>\\Daten`."""
    return home() / "Daten" if home() else Path.home() / "AstroFlow"


def read_config() -> dict:
    try:
        d = json.loads(config_path().read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def write_config(values: dict) -> dict:
    d = read_config()
    d.update({k: v for k, v in values.items() if v is not None})
    p = config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(p)
    return d


def data_dir(explicit: str | None = None) -> Path:
    return Path(explicit or os.environ.get("ASTROFLOW_DATA") or read_config().get("data_dir") or default_data_dir()).resolve()


def port(explicit: int | None = None) -> int:
    return int(explicit or os.environ.get("ASTROFLOW_PORT") or read_config().get("port") or DEFAULT_PORT)


def host() -> str:
    """Nur dieser PC, bis die Heimnetz-Freigabe eingeschaltet wird (sonst fragt die Windows-Firewall beim ersten Start)."""
    return "0.0.0.0" if read_config().get("lan") else "127.0.0.1"


def cloud_folder(path: Path) -> str | None:
    """Name des Sync-Dienstes, falls der Pfad in OneDrive/Dropbox/… liegt."""
    p = str(path).lower()
    for env in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial"):
        v = os.environ.get(env)
        if v and p.startswith(v.lower()):
            return "OneDrive"
    for m in CLOUD_MARKERS:
        if m in p:
            return {"googledrive": "Google Drive", "icloud": "iCloud", "megasync": "MEGA"}.get(m, m.title().replace("Onedrive", "OneDrive"))
    return None


# --------------------------------------------------------------------------- #
# Server finden, starten, beenden
# --------------------------------------------------------------------------- #
def probe(p: int, timeout: float = 1.5) -> dict | None:
    """Antwortet auf dem Port ein AstroFlow? → /api/status, sonst None."""
    import urllib.request
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{p}/api/status", timeout=timeout) as r:
            d = json.loads(r.read().decode("utf-8"))
            return d if isinstance(d, dict) and "version" in d else None
    except Exception:
        return None


def port_busy(p: int) -> bool:
    import socket
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", p)) == 0


def _gui_python() -> str:
    """pythonw.exe neben python.exe (kein Konsolenfenster), sonst der laufende Interpreter."""
    exe = Path(sys.executable)
    if os.name == "nt":
        w = exe.with_name("pythonw.exe")
        if w.is_file():
            return str(w)
    return str(exe)


def message(text: str, error: bool = False):
    """Meldung auch ohne Konsole (pythonw) sichtbar machen."""
    if os.name == "nt" and (sys.stdout is None or not sys.stdout.isatty()):
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, text, "AstroFlow", 0x10 if error else 0x40)
            return
        except Exception:
            pass
    print(text, file=sys.stderr if error else sys.stdout)


def spawn_server(data: Path, p: int, h: str) -> subprocess.Popen:
    data.mkdir(parents=True, exist_ok=True)
    log = open(data / "server.log", "ab")
    err = open(data / "server.log.err", "ab")
    env = {**os.environ, "ASTROFLOW_LAUNCHER": "1", "ASTROFLOW_PORT": str(p), "PYTHONIOENCODING": "utf-8"}
    env.pop("ASTROFLOW_DATA", None)                                  # --data hat ohnehin Vorrang
    kw = {}
    if os.name == "nt":
        kw["creationflags"] = 0x00000008 | 0x00000200 | 0x08000000   # DETACHED_PROCESS | NEW_PROCESS_GROUP | NO_WINDOW
    else:
        kw["start_new_session"] = True
    iso = ["-I"] if sys.flags.isolated else []                       # portables/installiertes Paket: ohne fremde PYTHON*-Variablen
    iso += ["-X", "utf8"]                                            # Protokolle in UTF-8 (pythonw, Umlaute)
    return subprocess.Popen([_gui_python(), *iso, "-m", "astroflow", "serve", "--host", h, "--port", str(p), "--data", str(data)],
                            stdin=subprocess.DEVNULL, stdout=log, stderr=err, cwd=str(data), env=env, **kw)


def start(data: str | None = None, p: int | None = None, browser: bool = True, wait_pid: int | None = None,
          timeout: float = 90) -> int:
    p = port(p)
    browser = browser and os.environ.get("ASTROFLOW_NO_BROWSER") != "1"
    if wait_pid:                                   # Neustart: warten, bis der alte Server weg ist
        t0 = time.time()
        while time.time() - t0 < 30 and (port_busy(p) or _alive(wait_pid)):
            time.sleep(0.3)
    url = f"http://localhost:{p}/"
    if probe(p):
        if browser:
            webbrowser.open(url)
        return 0
    if port_busy(p):
        message(f"Port {p} ist von einem anderen Programm belegt. AstroFlow kann nicht starten.\n"
                f"Anderen Port wählen: Umgebungsvariable ASTROFLOW_PORT oder „port“ in {config_path()}.", error=True)
        return 1
    d = data_dir(data)
    proc = spawn_server(d, p, host())
    t0 = time.time()
    while time.time() - t0 < timeout:
        if probe(p, 1.0):
            if browser:
                webbrowser.open(url)
            return 0
        if proc.poll() is not None:
            break
        time.sleep(0.4)
    message(f"AstroFlow startet nicht. Details im Protokoll:\n{d / 'server.log.err'}", error=True)
    return 1


def stop(p: int | None = None, timeout: float = 30) -> int:
    import urllib.request
    p = port(p)
    if not probe(p):
        print(f"AstroFlow läuft nicht (Port {p}).")
        return 0
    try:
        urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{p}/api/system/shutdown", data=b"{}", method="POST",
                                                      headers={"Content-Type": "application/json"}), timeout=10).read()
    except Exception:
        pass
    t0 = time.time()
    while time.time() - t0 < timeout and port_busy(p):
        time.sleep(0.3)
    print("AstroFlow wurde beendet." if not port_busy(p) else "AstroFlow reagiert nicht auf das Beenden.")
    return 0 if not port_busy(p) else 1


def _alive(pid: int) -> bool:
    if os.name == "nt":
        import ctypes
        h = ctypes.windll.kernel32.OpenProcess(0x1000 | 0x00100000, False, pid)   # QUERY_LIMITED_INFORMATION | SYNCHRONIZE
        if not h:
            return False
        try:
            return ctypes.windll.kernel32.WaitForSingleObject(h, 0) == 0x102            # WAIT_TIMEOUT = läuft noch
        finally:
            ctypes.windll.kernel32.CloseHandle(h)
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def restart_detached(p: int):
    """Neuen Starter anstoßen, der auf das Ende dieses Prozesses wartet und den Server neu startet (ohne Browser)."""
    kw = {"creationflags": 0x00000008 | 0x00000200 | 0x08000000} if os.name == "nt" else {"start_new_session": True}
    env = {k: v for k, v in os.environ.items() if k not in ("ASTROFLOW_DATA", "ASTROFLOW_LAUNCHER")}
    subprocess.Popen([_gui_python(), *(["-I"] if sys.flags.isolated else []), "-m", "astroflow", "start", "--no-browser", "--port", str(p), "--wait-pid", str(os.getpid())],
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env, **kw)
