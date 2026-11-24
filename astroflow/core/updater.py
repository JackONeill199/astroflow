"""„Jetzt aktualisieren“ (Wunsch 23): das neue Paket aus dem GitHub-Release laden, prüfen und starten – nur auf Knopfdruck, nur am Rechner selbst.

* **Windows** (installiert per Inno-Installer): `AstroFlow-Setup-<Version>.exe` laden, SHA-256 gegen `<Datei>.sha256` aus demselben Release
  prüfen, Installer starten – er beendet den Server selbst (`PrepareToInstall` → `astroflow stop`), Daten und Einstellungen bleiben.
* **Linux** (installiert per `install.sh`): `AstroFlow-<Version>-Linux-x86_64.tar.gz` laden, prüfen, sicher entpacken (`filter="data"`) und
  `install.sh` losgelöst starten – es beendet den Server, ersetzt das Programm über `.neu`/`.alt` und startet die neue Fassung.
* Portable Nutzung, Quellcode, Docker oder ein Release ohne `.sha256` (ältere Stände): **kein** Automatismus, nur Download-Link/Anleitung.

Die Download-Adresse kommt nie vom Client, sondern nur aus der Release-Abfrage des Servers. Jede Anfrage der Kette (auch jede Weiterleitung)
muss https auf einen erlaubten GitHub-Host sein. Die Prüfsumme stammt aus derselben Quelle wie das Paket und schützt nur vor kaputten bzw.
abgeschnittenen Downloads, nicht vor einem manipulierten Release – dafür bräuchte es eine Signatur (offen, `docs/FEATURE-WUENSCHE.md` Nr. 23).
Bei Fehler oder Abbruch **vor dem Start des Installers** bleibt die Installation unverändert; heruntergeladene Teile werden gelöscht.
Was danach schiefgeht, liegt beim Installer bzw. `install.sh` (dort `.neu`/`.alt`).
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from pathlib import Path
from urllib.parse import urljoin, urlsplit

# Release-Dateien liefert GitHub über eine Weiterleitung aus: früher objects.githubusercontent.com, seit 2025 release-assets.githubusercontent.com
ALLOWED_HOSTS = ("github.com", "objects.githubusercontent.com", "release-assets.githubusercontent.com")
MAX_REDIRECTS = 5
MAX_BYTES = 2 * 1024 ** 3            # Obergrenze je Paket (heute ~150–300 MB)
MAX_SHA_BYTES = 4096
TRANSPORT = None                     # Tests: httpx.MockTransport (kein echtes Netz)
CHUNK = 1 << 16


class UpdateError(Exception):
    """Feste deutsche Meldung für den Browser (keine Ausnahmetexte, Sicherheit 7)."""


class Cancelled(Exception):
    pass


def work_dir() -> Path:
    """Basisordner für Downloads (nie im Datenordner) – **nur für den eigenen Nutzer**, nie ein gemeinsamer Temp-Ordner (CWE-377):
    Windows `%LOCALAPPDATA%\\AstroFlow\\Update`, sonst `$XDG_CACHE_HOME/astroflow/update` bzw. `~/.cache/astroflow/update`.
    Jeder Lauf bekommt darin einen eigenen, atomar und privat angelegten Unterordner (`tempfile.mkdtemp`)."""
    if os.environ.get("ASTROFLOW_UPDATE_DIR"):
        return Path(os.environ["ASTROFLOW_UPDATE_DIR"])
    if os.name == "nt" and os.environ.get("LOCALAPPDATA"):
        return Path(os.environ["LOCALAPPDATA"]) / "AstroFlow" / "Update"
    return Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "astroflow" / "update"


KEEP_SECONDS = 3600                      # jüngere Laufordner beim Start nicht löschen – ein Installer könnte sie noch brauchen


def allowed(url: str) -> bool:
    try:
        u = urlsplit(url)
    except ValueError:
        return False
    return u.scheme == "https" and (u.hostname or "").lower() in ALLOWED_HOSTS and not u.username and not u.password \
        and u.port in (None, 443)


def parse_sha256(text: str, name: str) -> str | None:
    """`<64 Hex>  <Dateiname>` (sha256sum-Format) oder nur die Prüfsumme; steht ein Dateiname dabei, muss er passen."""
    for line in str(text or "").splitlines()[:20]:
        m = re.match(r"^\s*([0-9a-fA-F]{64})(?:\s+\*?(\S.{0,200}?))?\s*$", line)
        if m and (not m.group(2) or m.group(2).strip() == name):
            return m.group(1).lower()
    return None


def _client():
    import httpx
    return httpx.Client(follow_redirects=False, timeout=httpx.Timeout(30, read=120), transport=TRANSPORT,
                        headers={"User-Agent": "AstroFlow", "Accept-Encoding": "identity"})


def fetch(url: str, to: Path | None, prog=None, cancel: threading.Event | None = None, limit: int = MAX_BYTES) -> bytes | None:
    """Datei laden (nach `to` oder in den Speicher). Weiterleitungen werden einzeln gegen `ALLOWED_HOSTS` geprüft."""
    with _client() as cl:
        for _ in range(MAX_REDIRECTS + 1):
            if not allowed(url):
                raise UpdateError("Unerwartete Download-Adresse – Update abgebrochen (nur https von GitHub ist erlaubt).")
            with cl.stream("GET", url) as r:
                if r.status_code in (301, 302, 303, 307, 308):
                    url = urljoin(url, r.headers.get("location") or "")
                    continue
                if r.status_code != 200:
                    raise UpdateError(f"Download fehlgeschlagen (HTTP {int(r.status_code)}) – bitte später erneut versuchen.")
                size = r.headers.get("content-length") or ""
                total = int(size) if size.isdigit() else 0
                if total > limit:
                    raise UpdateError("Die Datei ist unerwartet groß – Update abgebrochen.")
                got, buf = 0, bytearray()
                out = open(to, "wb") if to else None
                try:
                    for chunk in r.iter_bytes(CHUNK):      # Accept-Encoding identity → Bytes wie auf dem Server
                        if cancel is not None and cancel.is_set():
                            raise Cancelled()
                        got += len(chunk)
                        if got > limit:
                            raise UpdateError("Die Datei ist unerwartet groß – Update abgebrochen.")
                        (out.write(chunk) if out else buf.extend(chunk))
                        if prog and total:
                            prog(got / total, got, total)
                finally:
                    if out:
                        out.close()
                if total and got != total:
                    raise UpdateError("Download unvollständig – bitte erneut versuchen.")
                return None if to else bytes(buf)
    raise UpdateError("Zu viele Weiterleitungen – Update abgebrochen.")


def sha256_file(p: Path, cancel: threading.Event | None = None) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            if cancel is not None and cancel.is_set():
                raise Cancelled()
            h.update(chunk)
    return h.hexdigest()


def safe_name(name: str) -> str:
    """Dateiname aus der Release-Liste als einfacher Name (kein Pfad)."""
    base = Path(str(name or "")).name
    if not base or base != name or not re.match(r"^[A-Za-z0-9._-]{1,120}$", base):
        raise UpdateError("Unerwarteter Dateiname im Release – Update abgebrochen.")
    return base


def extract_linux(tar: Path, dest: Path) -> Path:
    """Paket sicher entpacken (keine absoluten Pfade, kein `..`, keine Geräte) → Ordner mit `install.sh`."""
    import tarfile
    with tarfile.open(tar, "r:gz") as t:
        t.extractall(dest, filter="data")
    root = dest / "AstroFlow"
    if not (root / "install.sh").is_file() or not (root / "python" / "bin" / "python3").is_file():
        raise UpdateError("Das Paket hat nicht den erwarteten Aufbau – Update abgebrochen.")
    return root


# Start des neuen Pakets – in Tests per monkeypatch ersetzt (nie einen echten Installer starten)
def launch_windows(exe: Path) -> int:
    """Gibt die Prozessnummer zurück (der Inno-Installer wartet auf seinen Setup-Teil, lebt also bis zum Ende)."""
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    return subprocess.Popen([str(exe)], cwd=str(exe.parent), creationflags=flags, close_fds=True,
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).pid


def launch_linux(root: Path, prefix: Path, bindir: Path) -> int:
    """`install.sh` mit Programm- und Befehlsordner der laufenden Installation (auch eigene `--prefix`/`--bin`): nur so beendet es
    genau diesen Server über seinen Befehl und ersetzt genau diesen Programmordner."""
    log = open(root.parent / "install.log", "ab")
    # install.sh ersetzt sich per `exec` durch linux_install.py – dieselbe Prozessnummer bis zum Ende der Installation
    return subprocess.Popen(["/bin/sh", str(root / "install.sh"), "--prefix", str(prefix), "--bin", str(bindir)], cwd=str(root),
                            start_new_session=True, close_fds=True, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT).pid


PID_FILE = "installer.pid"               # im Laufordner: Prozess des gestarteten Installers – schützt den Ordner über Neustarts hinweg


def installer_alive(run: Path) -> bool:
    """Läuft der aus diesem Laufordner gestartete Installer noch? (prozessübergreifend, auch nach einem Serverneustart)"""
    try:
        pid = int((run / PID_FILE).read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return False
    from .launcher import _alive
    try:
        return pid > 0 and _alive(pid)
    except Exception:
        traceback.print_exc()
        return True                      # im Zweifel schützen


LINUX_MARKER = ".astroflow-install"      # wie `installer/linux_install.MARKER`: Programmordner aus install.sh


def linux_target() -> tuple[Path, Path] | None:
    """Programmordner und Befehlsordner der laufenden Linux-Installation – oder None, wenn sie sich nicht sicher bestimmen lassen
    (dann kein Automatismus). Programmordner = `<prefix>/python/bin/python3` des laufenden Pythons mit Markierungsdatei; der Befehl
    `astroflow` muss auf genau diesen Ordner zeigen (Standard `~/.local/bin`, sonst der im PATH)."""
    try:
        prefix = Path(sys.executable).resolve().parents[2]
        if not (prefix / LINUX_MARKER).is_file():
            return None
        found = shutil.which("astroflow")
        for w in (Path.home() / ".local" / "bin" / "astroflow", Path(found) if found else None):
            if w and w.is_file() and str(prefix / "python" / "bin" / "python3") in w.read_text(encoding="utf-8", errors="replace")[:4000]:
                return prefix, w.parent
    except (OSError, IndexError, ValueError):
        traceback.print_exc()
    return None


def windows_installed() -> bool:
    """Läuft das Python der Inno-Installation (`<App>\\python\\python(w).exe` mit `<App>\\unins000.exe`)? pip/pipx/uv-Installationen
    starten über `astroflow` ebenfalls mit ASTROFLOW_LAUNCHER=1 – dort würde der Installer nur eine zweite Installation daneben legen."""
    try:
        return (Path(sys.executable).resolve().parents[1] / "unins000.exe").is_file()
    except (OSError, IndexError):
        return False


def availability(offer: dict | None, kind: str, portable: bool) -> tuple[bool, str]:
    """Ob „Jetzt aktualisieren“ möglich ist – sonst der Grund (für den Hinweis im Panel)."""
    if kind not in ("windows", "linux"):
        return False, "source"
    if portable:
        return False, "portable"
    if kind == "linux" and linux_target() is None:
        return False, "unknown"
    if kind == "windows" and not windows_installed():
        return False, "notinstaller"
    dl = (offer or {}).get("download") or {}
    if not dl.get("url"):
        return False, "nodownload"
    if not dl.get("sha256_url"):
        return False, "nosha"
    return True, ""


class Updater:
    """Ein Update-Auftrag gleichzeitig; Fortschritt für `activity()` und das Update-Panel."""

    def __init__(self):
        self.task: dict | None = None
        self._cancel = threading.Event()
        self._lock = threading.Lock()
        self.cleanup()

    def cleanup(self) -> None:
        """Reste früherer Läufe entfernen – nur Laufordner, die älter als `KEEP_SECONDS` sind (ein eben gestarteter Installer bzw.
        `install.sh` liest sein Paket noch, auch wenn hier schon ein neuer Server startet)."""
        try:
            base = work_dir()
            if self.running() or not base.is_dir():
                return
            for d in base.iterdir():
                if not d.is_dir() or d.is_symlink() or installer_alive(d):
                    continue                                         # Installer liest sein Paket noch
                if (d / PID_FILE).exists() or time.time() - d.stat().st_mtime > KEEP_SECONDS:
                    shutil.rmtree(d, ignore_errors=True)
        except Exception:
            traceback.print_exc()

    def running(self) -> bool:
        return bool(self.task and self.task.get("state") == "running")

    def busy(self) -> bool:
        """Läuft gerade – oder ein Installer wurde schon gestartet (auch von einem früheren Serverprozess) und braucht sein Paket
        noch: dann kein zweiter Start."""
        return self.running() or bool(self.task and self.task.get("launched")) or self.installer_running()

    @staticmethod
    def installer_running() -> bool:
        try:
            base = work_dir()
            return base.is_dir() and any(d.is_dir() and installer_alive(d) for d in base.iterdir())
        except OSError:
            return False

    def state(self) -> dict | None:
        return dict(self.task) if self.task else None

    def start(self, info: dict, kind: str) -> dict:
        """`info` = Download-Eintrag des Servers (`name`, `url`, `sha256_url`, `latest`) – nie Werte vom Client."""
        with self._lock:
            if self.busy():
                return self.state()
            self._cancel = threading.Event()
            self.task = {"state": "running", "progress": 0.0, "detail": "Startet …", "started": time.time(),
                         "latest": str(info.get("latest") or ""), "kind": kind, "cancelable": True}
        threading.Thread(target=self._go, args=(dict(info), kind, self._cancel), daemon=True, name="update").start()
        return self.state()

    def cancel(self) -> dict | None:
        with self._lock:                 # gleiche Sperre wie `_commit`: entweder wirkt der Abbruch, oder der Start ist schon fest
            if self.running() and self.task.get("cancelable"):
                self._cancel.set()
                self.task.update(detail="Wird abgebrochen …")
        return self.state()

    def _commit(self, cancel: threading.Event, detail: str) -> None:
        """Letzter Punkt für einen Abbruch – danach wird der Installer gestartet."""
        with self._lock:
            if cancel.is_set():
                raise Cancelled()
            self._set(progress=0.99, detail=detail, cancelable=False)

    def _set(self, **kw):
        if self.task is not None:
            self.task.update(**kw)

    def _go(self, info: dict, kind: str, cancel: threading.Event):
        wd = None
        try:
            name = safe_name(info.get("name") or "")
            base = work_dir()
            base.mkdir(parents=True, exist_ok=True, mode=0o700)
            wd = Path(tempfile.mkdtemp(prefix="lauf-", dir=base))                 # privat (0700) und atomar angelegt
            self._set(progress=0.01, detail="Prüfsumme laden …")
            raw = fetch(str(info.get("sha256_url") or ""), None, cancel=cancel, limit=MAX_SHA_BYTES)
            want = parse_sha256(raw.decode("utf-8", "replace"), name)
            if not want:
                raise UpdateError("Die Prüfsumme im Release ist nicht lesbar – Update abgebrochen.")
            target = wd / name
            mb = lambda n: f"{n / 1e6:.0f}"                                        # noqa: E731

            def prog(f, got, total):
                self._set(progress=round(0.02 + 0.86 * f, 3), detail=f"Lade {name} … {mb(got)} von {mb(total)} MB")
            self._set(progress=0.02, detail=f"Lade {name} …")
            fetch(str(info.get("url") or ""), target, prog, cancel)
            self._set(progress=0.9, detail="Prüfsumme vergleichen …")
            if sha256_file(target, cancel) != want:
                raise UpdateError("Prüfsumme stimmt nicht – die Datei ist beschädigt. Nichts wurde geändert, bitte erneut versuchen.")
            if kind == "linux":
                self._set(progress=0.94, detail="Paket entpacken …")
                where = linux_target()
                if where is None:
                    raise UpdateError("Die laufende Linux-Installation ließ sich nicht bestimmen – bitte das Paket von Hand installieren.")
                root = extract_linux(target, wd / "paket")
                target.unlink(missing_ok=True)
                self._commit(cancel, "Installation starten …")
                self._pid(wd, launch_linux(root, *where))
                msg = "Installation läuft – AstroFlow wird beendet und startet danach in der neuen Fassung."
            else:
                self._commit(cancel, "Installer starten …")
                self._pid(wd, launch_windows(target))
                msg = "Installer gestartet – er beendet AstroFlow kurz. Danach AstroFlow wieder öffnen; Daten und Einstellungen bleiben."
            self._set(state="done", progress=1.0, detail=msg, finished=time.time(), cancelable=False, launched=True)
        except Cancelled:
            self._drop(wd)
            self._set(state="cancelled", detail="Abgebrochen – nichts wurde geändert.", finished=time.time(), cancelable=False)
        except UpdateError as e:
            self._drop(wd)
            self._set(state="error", detail=str(e), finished=time.time(), cancelable=False)
        except Exception:
            traceback.print_exc()
            self._drop(wd)
            self._set(state="error", detail="Update fehlgeschlagen (Netz oder Speicher) – nichts wurde geändert. Details im Protokoll.",
                      finished=time.time(), cancelable=False)

    @staticmethod
    def _pid(wd: Path, pid) -> None:
        try:
            (wd / PID_FILE).write_text(str(int(pid)), encoding="utf-8")
        except (OSError, TypeError, ValueError):
            traceback.print_exc()          # ohne Nummer schützt nur noch die Altersgrenze

    @staticmethod
    def _drop(wd: Path | None) -> None:
        if wd is not None:
            shutil.rmtree(wd, ignore_errors=True)

    def activity(self) -> list[dict]:
        t = self.task
        if not t:
            return []
        state = {"cancelled": "done"}.get(t["state"], t["state"])     # Abbruch ist kein Fehler – die Meldung sagt „Abgebrochen“
        return [{"key": "setup:update", "title": f"Update auf {t.get('latest') or 'neue Version'}", "kind": "Update",
                 "detail": t.get("detail", ""), "state": state, "progress": t.get("progress"), "started": t.get("started"),
                 "finished": t.get("finished"), "href": None}]
