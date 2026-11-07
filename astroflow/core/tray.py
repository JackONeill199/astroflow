"""Symbol im Infobereich (Windows-Taskleiste unten rechts; macOS/Linux, soweit pystray dort ein Backend findet).

Eigener leichter Prozess (`pythonw -m astroflow tray --port P`), vom Starter nach dem Serverstart angestoßen, je Benutzer nur einmal
(PID-Datei neben config.json). Spricht nur über HTTP mit dem Server – kein Import der Module, also wenig Speicher:

* Menü: AstroFlow öffnen (auch Doppelklick) · Aufträge · Aufträge anhalten/fortsetzen · Benachrichtigungen · Beim Anmelden starten · Beenden
* Symbol mit orangem Punkt, solange gerechnet wird (`/api/status` → `busy`, alle 5 s); Tooltip nennt den laufenden Auftrag.
* Benachrichtigung „Bild fertig“, wenn ein Bilder-Auftrag fertig wird (abschaltbar, `settings.json` → `system.tray_notify`).
* „Beenden“ fragt bei laufendem Auftrag nach und nutzt `/api/system/shutdown`. Der Prozess endet auch von selbst, wenn der
  Server dauerhaft weg ist, und `astroflow stop` bzw. das Beenden aus der Oberfläche beenden ihn ausdrücklich.
* Abschalten: `ASTROFLOW_NO_TRAY=1`; fehlt pystray oder ein Infobereich, läuft alles wie bisher ohne Symbol.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

from . import launcher

POLL_S = 5
GONE_S = 40                 # so lange darf der Server fehlen (Neustart nach Ordnerwechsel), dann endet das Symbol
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_NAME = "AstroFlow"


def pid_path() -> Path:
    return launcher.config_path().parent / "tray.pid"


def running_pid() -> int | None:
    try:
        pid = int(pid_path().read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None
    return pid if pid != os.getpid() and launcher._alive(pid) else None


def stop_path() -> Path:
    return launcher.config_path().parent / "tray.stop"


def request_stop():
    """Symbol bittet sich selbst zu beenden (prüft die Marke bei jeder Abfrage) – sauber, ohne Geistersymbol."""
    if running_pid():
        try:
            stop_path().parent.mkdir(parents=True, exist_ok=True)
            stop_path().write_text("1", encoding="utf-8")
        except OSError:
            pass


def disabled() -> bool:
    return os.environ.get("ASTROFLOW_NO_TRAY") == "1"


def ensure(port: int):
    """Vom Starter: Symbol starten, falls nicht abgeschaltet und noch keins läuft."""
    if disabled() or running_pid():
        return
    import subprocess
    kw = {"creationflags": 0x00000008 | 0x00000200 | 0x08000000} if os.name == "nt" else {"start_new_session": True}
    try:
        subprocess.Popen([launcher._gui_python(), *(["-I"] if sys.flags.isolated else []), "-m", "astroflow", "tray", "--port", str(port)],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **kw)
    except OSError:
        pass


def stop_running(timeout: float = 10):
    """Laufendes Symbol beenden (astroflow stop, Deinstallation – sonst bliebe python gesperrt): erst bitten, dann hart."""
    pid = running_pid()
    if not pid:
        return
    request_stop()
    t0 = time.time()
    while time.time() - t0 < POLL_S + 3 and launcher._alive(pid):
        time.sleep(0.25)
    if not launcher._alive(pid):
        return
    try:
        if os.name == "nt":
            import ctypes
            h = ctypes.windll.kernel32.OpenProcess(0x0001 | 0x00100000, False, pid)        # TERMINATE | SYNCHRONIZE
            if h:
                ctypes.windll.kernel32.TerminateProcess(h, 0)
                ctypes.windll.kernel32.WaitForSingleObject(h, int(timeout * 1000))
                ctypes.windll.kernel32.CloseHandle(h)
        else:
            import signal
            os.kill(pid, signal.SIGTERM)
    except Exception:
        pass
    try:
        pid_path().unlink()
    except OSError:
        pass


# ---- Autostart (Registry „Run“ des Benutzers; die Installer-Aufgabe legt dagegen eine Verknüpfung im Autostart-Ordner an) ----
def _startup_lnk() -> Path | None:
    a = os.environ.get("APPDATA")
    return Path(a) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / "AstroFlow.lnk" if a else None


def autostart_enabled() -> bool:
    if os.name != "nt":
        return False
    lnk = _startup_lnk()
    if lnk and lnk.exists():
        return True
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.QueryValueEx(k, RUN_NAME)
            return True
    except OSError:
        return False


def set_autostart(on: bool):
    if os.name != "nt":
        return
    import winreg
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
        if on:
            cmd = f'"{launcher._gui_python()}" {"-I " if sys.flags.isolated else ""}-m astroflow start --no-browser'
            if launcher.home():                       # portabel: Konfiguration, Daten und Pakete liegen im Programmordner
                cmd += f' --home "{launcher.home()}"'
            winreg.SetValueEx(k, RUN_NAME, 0, winreg.REG_SZ, cmd)
        else:
            try:
                winreg.DeleteValue(k, RUN_NAME)
            except OSError:
                pass
    lnk = _startup_lnk()
    if not on and lnk and lnk.exists():
        try:
            lnk.unlink()
        except OSError:
            pass


# ---- Symbol ----------------------------------------------------------------------------------------------------------
def draw_icon(busy: bool = False, size: int = 64):
    """App-Symbol (wie static/icon.svg) – mit orangem Punkt, solange ein Auftrag läuft."""
    from PIL import Image, ImageDraw, ImageFilter
    n = 256
    img = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((0, 0, n - 1, n - 1), radius=56, fill=(10, 13, 22, 255))
    glow = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    g = ImageDraw.Draw(glow)
    for i in range(40, 0, -1):
        t = i / 40
        col = (int(199 * (1 - t) + 109 * t), int(214 * (1 - t) + 141 * t), 255, int(255 * (1 - t) ** 0.7))
        g.ellipse((128 - 100 * t, 128 - 40 * t, 128 + 100 * t, 128 + 40 * t), fill=col)
    img.alpha_composite(glow.rotate(30, resample=Image.BICUBIC).filter(ImageFilter.GaussianBlur(3)))
    d = ImageDraw.Draw(img)
    for cx, cy, r, c in ((128, 128, 18, (255, 255, 255)), (56, 60, 6, (255, 255, 255)), (200, 192, 5, (255, 255, 255)), (196, 56, 7, (255, 217, 168))):
        d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=c + (255,))
    if busy:
        d.ellipse((150, 150, 250, 250), fill=(255, 159, 28, 255), outline=(10, 13, 22, 255), width=14)
    return img.resize((size, size), Image.LANCZOS)


def tooltip(status: dict | None, activity: list[dict]) -> str:
    if status is None:
        return "AstroFlow – Server nicht erreichbar"
    run = next((a for a in activity if a.get("state") == "running"), None)
    if run:
        pct = f" {round(run['progress'] * 100)} %" if isinstance(run.get("progress"), (int, float)) else ""
        text = f"AstroFlow – {run.get('title') or 'Auftrag'}{pct}"
    elif status.get("busy"):
        text = f"AstroFlow – {status['busy']} Auftrag/Aufträge"
    else:
        text = "AstroFlow – bereit"
    return text[:120]                          # Windows erlaubt 127 Zeichen


def finished_pictures(prev: dict, activity: list[dict]) -> tuple[dict, list[str]]:
    """Bilder-Aufträge, die seit der letzten Abfrage fertig wurden → Titel (für die Benachrichtigung)."""
    now = {a["key"]: a.get("state") for a in activity if a.get("module") == "stacking" and a.get("key")}
    done = [a.get("title") or "Bild" for a in activity if a.get("module") == "stacking" and a.get("state") == "done"
            and prev.get(a.get("key")) in ("running", "queued")]
    return now, done


class Tray:
    def __init__(self, port: int):
        self.port = port
        self.base = f"http://127.0.0.1:{port}"
        self.status: dict | None = None
        self.activity: list[dict] = []
        self.notify = True
        self.seen: dict = {}
        self.icon = None
        self.stop = threading.Event()
        self.busy = None

    # HTTP
    def get(self, path: str, timeout: float = 4):
        with urllib.request.urlopen(self.base + path, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))

    def post(self, path: str, body: dict, timeout: float = 10):
        req = urllib.request.Request(self.base + path, data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json", "Origin": self.base})   # Herkunftsprüfung
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))

    # Menüaktionen
    def open(self, *_):
        webbrowser.open(f"http://localhost:{self.port}/")

    def jobs(self, *_):
        webbrowser.open(f"http://localhost:{self.port}/#/activity")

    def paused(self, *_) -> bool:
        return bool(((self.status or {}).get("modules") or {}).get("stacking", {}).get("paused"))

    def toggle_pause(self, *_):
        try:
            self.post("/api/stacking/queue", {"paused": not self.paused()})
            self.refresh()
        except Exception:
            pass

    def toggle_notify(self, *_):
        self.notify = not self.notify
        try:
            self.post("/api/setup", {"tray_notify": self.notify})
        except Exception:
            pass

    def toggle_autostart(self, *_):
        try:
            set_autostart(not autostart_enabled())
        except OSError:
            pass

    def quit(self, *_):
        if (self.status or {}).get("busy") and not ask("Auftrag läuft – trotzdem beenden? Er wird später fortgesetzt."):
            return
        try:
            self.post("/api/system/shutdown", {})
        except Exception:
            pass
        self.close()

    def close(self):
        self.stop.set()
        if self.icon:
            self.icon.stop()

    # Abfrage
    def refresh(self):
        try:
            self.status = self.get("/api/status")
            self.activity = self.get("/api/activity?limit=15")
        except Exception:
            self.status = None
            return False
        busy = bool(self.status.get("busy"))
        if self.icon:
            if busy != self.busy:
                self.icon.icon = draw_icon(busy)
                self.busy = busy
            self.icon.title = tooltip(self.status, self.activity)
        self.seen, done = finished_pictures(self.seen, self.activity)
        if done and self.notify and self.icon and getattr(self.icon, "HAS_NOTIFICATION", True):
            try:
                self.icon.notify(f"Fertig: {', '.join(done)[:200]}", "AstroFlow – Bild fertig")
            except Exception:
                pass
        return True

    def loop(self):
        last_ok = time.time()
        try:
            self.notify = bool(self.get("/api/setup").get("tray_notify", True))
        except Exception:
            pass
        while not self.stop.wait(POLL_S):
            if stop_path().exists():
                try:
                    stop_path().unlink()
                except OSError:
                    pass
                self.close()
                return
            if self.refresh():
                last_ok = time.time()
            elif time.time() - last_ok > GONE_S:
                self.close()
                return

    def run(self) -> int:
        import pystray
        M = pystray.MenuItem
        menu = pystray.Menu(
            M("AstroFlow öffnen", self.open, default=True),
            M("Aufträge", self.jobs),
            M(lambda _: "Aufträge fortsetzen" if self.paused() else "Aufträge anhalten", self.toggle_pause),
            pystray.Menu.SEPARATOR,
            M("Benachrichtigung, wenn ein Bild fertig ist", self.toggle_notify, checked=lambda _: self.notify),
            M("Beim Anmelden starten", self.toggle_autostart, checked=lambda _: autostart_enabled(), visible=os.name == "nt"),
            pystray.Menu.SEPARATOR,
            M("Beenden", self.quit),
        )
        self.refresh()
        self.icon = pystray.Icon("AstroFlow", draw_icon(False), tooltip(self.status, self.activity), menu)
        self.busy = None
        threading.Thread(target=self.loop, daemon=True).start()
        self.icon.run(setup=lambda ic: (setattr(ic, "visible", True), self.refresh()))
        return 0


def ask(text: str) -> bool:
    if os.name == "nt":
        try:
            import ctypes
            return ctypes.windll.user32.MessageBoxW(None, text, "AstroFlow", 0x04 | 0x20 | 0x100 | 0x40000) == 6   # Ja/Nein, ?, Nein vorgewählt, oben
        except Exception:
            return True
    return True


def main(port: int | None = None) -> int:
    if disabled():
        return 0
    try:
        import pystray  # noqa: F401
    except Exception:
        return 0                               # kein Infobereich verfügbar – AstroFlow läuft ohne Symbol
    if running_pid():
        return 0
    p = pid_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(str(os.getpid()), encoding="utf-8")
    try:
        stop_path().unlink()                    # alte Marke eines früheren Laufs
    except OSError:
        pass
    try:
        return Tray(launcher.port(port)).run()
    except Exception:
        return 1
    finally:
        try:
            if p.read_text(encoding="utf-8").strip() == str(os.getpid()):
                p.unlink()
        except OSError:
            pass
