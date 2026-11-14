"""Live-Bilder vom Teleskop holen: anonymer FTP im WLAN des Teleskops (Vaonis, Hinweis des Nutzers 14.11.2026).

Rohbilder liegen als `img-NNNN.fits` (16 bit, ~4 MB) unter `user/<plan>/<beobachtung>/01-images-initial/`; während einer
Beobachtung kommen laufend neue dazu – regelmäßig nachsehen = Live-Mitschnitt. Nur lesen (LIST/RETR), nie schreiben oder
löschen. Abgelegt wird unter `input/Teleskop/<Gerät>/<Nacht>/<Ziel>/` – den Eingangsordner überwacht die Bilder-Pipeline.
Ordner- und Dateinamen vom Gerät werden nicht für lokale Pfade benutzt (nur geprüfte `img-NNNN.fits`).

Stand: am Gerät noch nicht durch AstroFlow geprüft (Probe „Bilder abholen“ steht aus).
"""
from __future__ import annotations

import ftplib
import re
import threading
import time
import traceback
from pathlib import Path

FRAME = re.compile(r"^img-\d{1,6}\.fits?$", re.I)
IMAGES_DIR = re.compile(r"^\d{2}-images", re.I)
SAFE = re.compile(r"[^A-Za-z0-9 _.+-]+")
MAX_ENTRIES = 400          # je Ordnerliste (Schutz gegen riesige Bäume)


def safe_name(s: str) -> str:
    return (SAFE.sub("_", str(s or "")).strip(" ._") or "Ziel")[:60]


class FtpFrames:
    """Ein Teleskop: neuesten Bilderordner finden, neue Einzelbilder vollständig holen."""

    def __init__(self, host: str, root: str = "user", timeout: float = 15.0, ftp_factory=None):
        self.host, self.root, self.timeout = host, root, timeout
        self.ftp_factory = ftp_factory or (lambda: ftplib.FTP(host, timeout=timeout))
        self.sizes: dict[str, int] = {}          # Größe beim letzten Blick (Datei wird evtl. noch geschrieben)
        self.done: set[str] = set()
        self.lock = threading.Lock()

    def _ftp(self):
        f = self.ftp_factory()
        f.login()                                   # anonym
        return f

    @staticmethod
    def _list(f, path: str, limit: int | None = MAX_ENTRIES) -> list[tuple[str, dict]]:
        """Ordnerliste. `limit` nur für Ordner (Schutz gegen riesige Bäume); Bilderlisten immer vollständig (`limit=None`)."""
        try:
            out = [(n, facts) for n, facts in f.mlsd(path, facts=["type", "size", "modify"]) if n not in (".", "..")]
        except ftplib.error_perm:                   # ohne MLSD: nur Namen
            out = [(n.rsplit("/", 1)[-1], {}) for n in f.nlst(path)]
        return out if limit is None else out[:limit]

    def latest_folder(self, f, since: float | None = None) -> str | None:
        """Jüngster `<plan>/<beobachtung>/0x-images-*`-Ordner unter `user/` (nach Änderungszeit). `since`: nur Beobachtungen,
        die nach dem Start des Ziels geändert wurden (sonst wäre es noch der Ordner des vorigen Ziels)."""
        lim = time.strftime("%Y%m%d%H%M%S", time.gmtime(since - 120)) if since else ""
        best = None
        for plan, pf in self._list(f, self.root):
            if pf.get("type", "dir") != "dir":
                continue
            for obs, of in self._list(f, f"{self.root}/{plan}"):
                if of.get("type", "dir") != "dir":
                    continue
                key = of.get("modify", "") or pf.get("modify", "")
                if lim and key and key < lim:
                    continue
                if best is None or key > best[0]:
                    best = (key, f"{self.root}/{plan}/{obs}")
        if not best:
            return None
        subs = [(n, x) for n, x in self._list(f, best[1]) if IMAGES_DIR.match(n) and x.get("type", "dir") == "dir"]
        if not subs:
            return None
        return f"{best[1]}/{sorted(subs, key=lambda s: (s[1].get('modify', ''), s[0]))[-1][0]}"

    def fetch(self, dest, folder: str | None = None, final: bool = False, since: float | None = None) -> tuple[str | None, list[Path]]:
        """Neue Einzelbilder nach `dest` holen. Eine Datei gilt erst als fertig, wenn ihre Größe beim nächsten Blick gleich
        blieb (oder `final`, wenn die Beobachtung beendet ist). Rückgabe: (Ordner am Gerät, neue Dateien)."""
        got: list[Path] = []
        with self.lock:
            f = self._ftp()
            try:
                folder = folder or self.latest_folder(f, since)
                if not folder:
                    return None, got
                files = [(n, int(x.get("size") or -1)) for n, x in self._list(f, folder, None) if FRAME.match(n)]
                dest = dest(folder) if callable(dest) else dest            # Zielordner kann vom Ordner am Gerät abhängen
                dest.mkdir(parents=True, exist_ok=True)
                for name, size in sorted(files):
                    key = f"{folder}/{name}"
                    if key in self.done:
                        continue
                    stable = final or (size > 0 and self.sizes.get(key) == size)
                    self.sizes[key] = size
                    if not stable:
                        continue
                    tmp = dest / (name + ".part")
                    with open(tmp, "wb") as out:
                        f.retrbinary(f"RETR {key}", out.write)
                    if size > 0 and tmp.stat().st_size != size:
                        tmp.unlink(missing_ok=True)
                        continue
                    target = dest / name
                    tmp.replace(target)
                    self.done.add(key)
                    got.append(target)
            finally:
                try:
                    f.quit()
                except Exception:
                    pass
        return folder, got


class Collector:
    """Holt während der Aufnahme regelmäßig neue Bilder (eigener Thread, der Takt des Autopiloten wartet nie auf FTP)."""
    EVERY = 20.0

    def __init__(self, unit, input_dir: Path, host: str):
        self.unit, self.input_dir = unit, Path(input_dir)
        self.ftp = FtpFrames(host)
        self.folders: dict[int, str] = {}          # id(Item) → Ordner am Gerät
        self.count = 0
        self.error: str | None = None
        self._stop = threading.Event()
        self.thread = threading.Thread(target=self._loop, daemon=True, name="teleskop-bilder")

    def start(self):
        self.thread.start()

    def stop(self):
        self._stop.set()

    def dest(self, item, folder: str | None = None) -> Path:
        """Je Beobachtung am Gerät ein eigener Ordner – dasselbe Ziel zweimal in einer Nacht (Ersatz + geplanter Block) hat am
        Gerät zwei Beobachtungen mit jeweils img-0001 …, die sich sonst überschreiben würden."""
        p = self.unit.pilot
        base = self.input_dir / "Teleskop" / safe_name(self.unit.cfg["model"]) / safe_name(p.night or "Nacht") / safe_name(item.name)
        parts = (folder or "").split("/")
        return base / safe_name(parts[-2]) if len(parts) >= 2 else base

    def _loop(self):
        last = None
        while not self._stop.wait(self.EVERY):
            p = self.unit.pilot
            cur = p._current()
            try:
                if cur and p.state.step == "capture":
                    self._take(cur, final=False)
                if last is not None and last is not cur and last.status in ("fertig", "verdeckt", "abgebrochen"):
                    self._take(last, final=True)          # Rest nach dem Ende des Ziels
                last = cur or last
                self.error = None
            except Exception:
                traceback.print_exc()
                if self.error is None:
                    self.error = "Bilder konnten nicht vom Teleskop geholt werden (FTP)."
                    p.log(self.error, "warn")

    def _take(self, item, final: bool):
        folder, got = self.ftp.fetch(lambda fo: self.dest(item, fo), self.folders.get(id(item)), final=final, since=item.real_start)
        if folder:
            self.folders[id(item)] = folder
        if got:
            self.count += len(got)
            self.unit.pilot.frames_fetched = self.count
            if self.count == len(got) or final:
                self.unit.pilot.log(f"{len(got)} Rohbilder von {item.name} geholt ({self.count} in dieser Nacht).", "ok")
