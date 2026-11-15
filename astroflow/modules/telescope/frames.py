"""Live-Bilder vom Teleskop holen: anonymer FTP im WLAN des Teleskops (Vaonis, Hinweis des Nutzers 14.11.2026).

Rohbilder liegen als `img-NNNN.fits` (16 bit, ~4 MB) unter `user/<plan>/<beobachtung>/01-images-initial/` bzw. – bei einer
Einzelbeobachtung (so startet der Autopilot jedes Ziel) – ohne Plan-Ebene unter `user/<beobachtung>/01-images-initial/`; während einer
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
# Einzelbeobachtung ohne Plan (Echtbetrieb 15.11.2026): „2026-11-14_23-59-44_observation_M1_Krebsnebel“
SINGLE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})_(\d{2})-(\d{2})-\d{2}_observation_(.{0,80})$", re.I)
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

    def observations(self, f):
        """Alle Beobachtungen unter `user/`: (Pfad relativ zu `user/`, Plan oder "", Beobachtung, Änderungszeit, Bilderordner
        oder None = noch nicht gelistet). Zwei Formen: `<plan>/<beobachtung>/0x-images-*` und – Einzelbeobachtung –
        `<beobachtung>/0x-images-*` (erkannt am Namen oder daran, dass die Bilderordner direkt darin liegen)."""
        for top, tf in self._list(f, self.root):
            if tf.get("type", "dir") != "dir":
                continue
            kids = self._list(f, f"{self.root}/{top}")
            imgs = [(n, x) for n, x in kids if IMAGES_DIR.match(n) and x.get("type", "dir") == "dir"]
            if imgs or SINGLE_RE.match(top):
                yield top, "", top, tf.get("modify", ""), imgs
                continue
            for obs, of in kids:
                if of.get("type", "dir") == "dir":
                    yield f"{top}/{obs}", top, obs, of.get("modify", "") or tf.get("modify", ""), None

    def images_dirs(self, f, rel: str, known=None) -> list[tuple[str, dict]]:
        return known if known is not None else \
            [(n, x) for n, x in self._list(f, f"{self.root}/{rel}") if IMAGES_DIR.match(n) and x.get("type", "dir") == "dir"]

    def latest_folder(self, f, since: float | None = None) -> str | None:
        """Jüngster Bilderordner einer Beobachtung (beide Formen, s. `observations`) nach Änderungszeit. `since`: nur
        Beobachtungen, die nach dem Start des Ziels geändert wurden (sonst wäre es noch der Ordner des vorigen Ziels)."""
        lim = time.strftime("%Y%m%d%H%M%S", time.gmtime(since - 120)) if since else ""
        best = None
        for rel, _, _, key, imgs in self.observations(f):
            if lim and key and key < lim:
                continue
            if best is None or key > best[0]:
                best = (key, rel, imgs)
        if not best:
            return None
        subs = self.images_dirs(f, best[1], best[2])
        if not subs:
            return None
        return f"{self.root}/{best[1]}/{sorted(subs, key=lambda s: (s[1].get('modify', ''), s[0]))[-1][0]}"

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
        self.folders: dict[tuple, str] = {}        # (id(Item), Start dieses Versuchs) → Ordner am Gerät
        self.attempt: tuple | None = None          # laufender Versuch (Item, Startzeit)
        self.pending: list[tuple] = []             # beendete Versuche, deren Abschlussabruf noch aussteht
        self.count = 0
        self.last: Path | None = None              # zuletzt geholte Rohbild-Datei (Quelle der Vorschau)
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
        while not self._stop.wait(self.EVERY):
            self.step()

    def step(self):
        """Ein Durchgang (auch für Tests). Ein *Versuch* = (Ziel, Startzeit). Endet er – fertig, verdeckt, Pause, Wetterpause,
        neuer Versuch –, wird sein Ordner einmal vollständig abgeholt, auch die zuletzt geschriebenen Bilder."""
        p = self.unit.pilot
        cur = p._current()
        done = self.attempt
        if done and not (cur is done[0] and cur.real_start == done[1]):
            self.attempt = None
            self.pending.append(done)                                   # Rest nach dem Ende dieses Versuchs
        ok = True
        for att in list(self.pending):              # bleibt vorgemerkt, bis der Abschlussabruf geklappt hat (FTP kurz weg)
            try:
                self._take(att[0], final=True, start=att[1])
                self.pending.remove(att)
            except Exception:
                self._failed(p)
                ok = False
        try:
            if cur and p.state.step == "capture":
                if self.attempt is None:
                    self.attempt = (cur, cur.real_start)
                self._take(cur, final=False, start=cur.real_start)
        except Exception:
            self._failed(p)
            ok = False
        if ok:
            self.error = None

    def _failed(self, p):
        if self.error is None:                      # nur beim ersten Fehler protokollieren, nicht bei jeder Wiederholung
            traceback.print_exc()
            self.error = "Bilder konnten nicht vom Teleskop geholt werden (FTP)."
            p.log(self.error, "warn")

    def _take(self, item, final: bool, start: float | None = None):
        start = item.real_start if start is None else start
        key = (id(item), start)                     # neuer Versuch desselben Ziels (Weiter, Wiederöffnen) → neuer Ordner am Gerät
        if final and key not in self.folders:
            return                                  # von diesem Versuch nie einen Ordner gesehen – nichts Fremdes holen
        folder, got = self.ftp.fetch(lambda fo: self.dest(item, fo), self.folders.get(key), final=final, since=start)
        if folder:
            self.folders[key] = folder
        if got:
            self.last = max(got, key=lambda p: p.name)
            self.count += len(got)
            self.unit.pilot.frames_fetched = self.count
            if not final and hasattr(self.unit, "measure_frames"):
                self.unit.measure_frames(got)          # Beschlag-Erkennung des Autopiloten (Dienst des Bilder-Moduls)
            if self.count == len(got) or final:
                self.unit.pilot.log(f"{len(got)} Rohbilder von {item.name} geholt ({self.count} in dieser Nacht).", "ok")
