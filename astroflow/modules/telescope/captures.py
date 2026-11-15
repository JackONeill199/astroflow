"""Aufnahmen, die schon auf dem Teleskop liegen, in AstroFlow importieren (Importansicht: „Vom Teleskop“).

Liest per anonymem FTP (nur LIST/RETR) die Beobachtungen unter `user/<plan>/<beobachtung>/0x-images-*/img-NNNN.fits` bzw.
(Einzelbeobachtung) `user/<beobachtung>/0x-images-*/…` und kopiert gewählte Beobachtungen nach
`input/Teleskop/<Gerät>/<plan>/<beobachtung>/` bzw. `input/Teleskop/<Gerät>/<beobachtung>/`. Danach stößt der Dienst `scan_input`
(Bilder-Modul) das Einlesen an. Gewählt werden nur Beobachtungen aus der zuletzt gelesenen Liste – der Browser schickt keine
Pfade, nur deren Schlüssel. Bereits vorhandene Dateien gleicher Größe werden übersprungen (Import fortsetzbar).
"""
from __future__ import annotations

import calendar
import re
import json
import threading
import time
import traceback
from pathlib import Path

from .frames import FRAME, SINGLE_RE, FtpFrames, safe_name

CACHE_S = 30
MAX_OBS = 300


def _ts(modify: str | None) -> float | None:
    try:
        return float(calendar.timegm(time.strptime(modify[:14], "%Y%m%d%H%M%S")))
    except (TypeError, ValueError):
        return None


# Ordnernamen am Gerät (Probe 14.11.2026, Vespera 3.5.3): „2026-11-13_17-21-02_plan_<Planname>“ / „03-observation-m77“
PLAN_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})_(\d{2})-(\d{2})-\d{2}_plan_(.{0,80})$")
OBS_RE = re.compile(r"^\d{1,3}-observation-(.{1,80})$", re.I)
CATALOG_RE = re.compile(r"^(m|ngc|ic|sh2|c|b|abell|arp)[ -]?(\d+)$", re.I)


CAT_PREFIX = re.compile(r"^(m|ngc|ic|sh2|c|b|abell|arp)$", re.I)


def _cat(prefix: str, num: str) -> str:
    p = prefix.upper() if len(prefix) <= 3 else prefix.capitalize()
    return f"M{num}" if p == "M" else f"{p} {num}"


def nice(raw: str) -> str:
    """„m77“ → „M77“, „M1_Krebsnebel“ → „M1 Krebsnebel“, „C_51_IC_1613_“ → „C 51 IC 1613“ (Unterstriche weg, Katalognummern)."""
    words = [w for w in re.split(r"[_ ]+", raw) if w]
    out, k = [], 0
    while k < len(words):
        w = words[k]
        c = CATALOG_RE.match(w)
        if c:
            out.append(_cat(c.group(1), c.group(2)))
        elif CAT_PREFIX.match(w) and k + 1 < len(words) and words[k + 1].isdigit():
            out.append(_cat(w, words[k + 1]))
            k += 1
        else:
            out.append(w)
        k += 1
    return " ".join(out) or raw


def pretty(plan: str, obs: str) -> tuple[str, str]:
    """Lesbare Namen: („M77“, „Plan Astroflow · 13.11.2026 17:21“) bzw. Einzelbeobachtung („M1 Krebsnebel“,
    „Einzelbeobachtung · 14.11.2026 23:59“). Unbekanntes Schema → Ordnernamen unverändert."""
    s = SINGLE_RE.match(obs) if not plan else None
    if s:
        return nice(s.group(6)) or obs, f"Einzelbeobachtung · {s.group(3)}.{s.group(2)}.{s.group(1)} {s.group(4)}:{s.group(5)}"
    m = OBS_RE.match(obs)
    name = nice(m.group(1)) if m else obs
    p = PLAN_RE.match(plan)
    sub = f"Plan {p.group(6).replace('_', ' ') or 'ohne Namen'} · {p.group(3)}.{p.group(2)}.{p.group(1)} {p.group(4)}:{p.group(5)}" if p         else plan or "Einzelbeobachtung"
    return name, sub


def list_captures(ftp: FtpFrames) -> list[dict]:
    """Beobachtungen mit Rohbildern (neueste zuerst). Schlüssel = „<plan>/<beobachtung>“ bzw. „<beobachtung>“
    (Einzelbeobachtung) – Pfad relativ zu `user/` aus dieser Liste, nie vom Browser."""
    out = []
    f = ftp._ftp()
    try:
        for rel, plan, obs, modify, imgs in ftp.observations(f):
            if len(out) >= MAX_OBS:
                break
            frames, size = 0, 0
            for sub, _ in ftp.images_dirs(f, rel, imgs):
                for name, x in ftp._list(f, f"{ftp.root}/{rel}/{sub}", None):
                    if FRAME.match(name):
                        frames += 1
                        size += int(x.get("size") or 0)
            if frames:
                name, sub = pretty(plan, obs)
                out.append({"key": rel, "plan": plan, "observation": obs, "name": name, "sub": sub, "frames": frames, "bytes": size,
                            "modified": _ts(modify)})
    finally:
        try:
            f.quit()
        except Exception:
            pass
    return sorted(out, key=lambda e: -(e["modified"] or 0))


class CaptureImport:
    """Liste (zwischengespeichert) und ein Import-Auftrag zur Zeit."""

    def __init__(self, mod):
        self.mod = mod
        self.cache: tuple[float, str, list[dict]] | None = None
        self.job: dict | None = None
        self.lock = threading.Lock()
        self.done_file = mod.ctx.module_dir("telescope") / "importiert.json"

    def _ftp(self) -> tuple[FtpFrames | None, str]:
        """(FTP, Gerätebezeichnung) – Simulator: Attrappe; Vaonis: Adresse des Teleskops."""
        u = self.mod.unit
        if u.simulated and u.cfg["driver"] == "sim":
            from .sim import SimFtp
            return FtpFrames("sim", ftp_factory=SimFtp.shared), u.cfg["model"]
        host = getattr(u.driver, "ip", None) or u.cfg.get("ip") or (None if u.cfg["driver"] == "sim" else "10.0.0.1")
        return (FtpFrames(host, timeout=5) if host else None), u.cfg["model"]

    def imported(self) -> dict:
        try:
            return json.loads(self.done_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def listing(self, fresh: bool = False) -> dict:
        ftp, model = self._ftp()
        u = self.mod.unit
        from . import MODELS
        base = {"simulated": u.simulated, "model": model, "model_name": MODELS.get(model, model), "job": self.job_view()}
        if ftp is None:
            return {**base, "available": False, "items": [], "reason": "Kein Teleskop eingerichtet."}
        c = self.cache
        if c and not fresh and c[1] == ftp.host and time.time() - c[0] < CACHE_S:
            items = c[2]
        else:
            try:
                items = list_captures(ftp)
            except Exception:
                traceback.print_exc()
                return {**base, "available": False, "items": [], "reason": "Teleskop nicht erreichbar – ist der Rechner in seinem WLAN?"}
            self.cache = (time.time(), ftp.host, items)
        done = self.imported()
        return {**base, "available": True, "items": [{**e, "imported": done.get(f"{ftp.host}|{e['key']}")} for e in items]}

    def job_view(self) -> dict | None:
        j = self.job
        return None if j is None else {k: j[k] for k in ("state", "done", "total", "current", "files", "started", "finished", "text")}

    def start(self, keys: list[str]) -> dict:
        with self.lock:
            if self.job and self.job["state"] == "running":
                raise RuntimeError("Es läuft schon ein Import vom Teleskop.")
            ftp, model = self._ftp()
            if ftp is None or not self.cache or self.cache[1] != ftp.host:
                raise RuntimeError("Bitte die Liste der Aufnahmen neu laden.")
            known = {e["key"]: e for e in self.cache[2]}
            pick = [known[k] for k in dict.fromkeys(keys) if k in known]          # nur Schlüssel aus der Liste, keine Pfade
            if not pick:
                raise RuntimeError("Keine Aufnahmen gewählt.")
            self.job = {"state": "running", "done": 0, "total": sum(e["frames"] for e in pick), "current": "", "files": 0,
                        "started": time.time(), "finished": None, "text": "", "items": pick}
            threading.Thread(target=self._run, args=(ftp, model, pick), daemon=True, name="teleskop-import").start()
            return self.job_view()

    def _run(self, ftp: FtpFrames, model: str, pick: list[dict]):
        j = self.job
        input_dir = Path(self.mod.ctx.settings.input_dir)
        try:
            for e in pick:
                j["current"] = e["observation"]
                dest = input_dir / "Teleskop" / safe_name(model)
                dest = (dest / safe_name(e["plan"]) if e["plan"] else dest) / safe_name(e["observation"])    # Einzelbeobachtung: ohne Plan-Ebene
                got = self._copy(ftp, e, dest, j)
                done = self.imported()
                done[f"{ftp.host}|{e['key']}"] = {"frames": e["frames"], "at": time.time(), "new": got}
                self.done_file.write_text(json.dumps(done), encoding="utf-8")
            j["state"], j["text"] = "done", f"{j['files']} neue Rohbilder aus {len(pick)} Beobachtung{'en' if len(pick) != 1 else ''} importiert."
        except Exception:
            traceback.print_exc()
            j["state"], j["text"] = "error", "Import vom Teleskop abgebrochen – Verbindung prüfen und erneut starten (fertige Dateien bleiben)."
        j["finished"] = time.time()
        scan = self.mod.ctx.service("scan_input")
        if scan and j["files"]:
            try:
                scan()
            except Exception:
                traceback.print_exc()

    @staticmethod
    def _copy(ftp: FtpFrames, e: dict, dest: Path, j: dict) -> int:
        new = 0
        f = ftp._ftp()
        try:
            base = f"{ftp.root}/{e['key']}"
            for sub, _ in ftp.images_dirs(f, e["key"]):
                folder = dest if sub.lower().startswith("01-") else dest / safe_name(sub)
                folder.mkdir(parents=True, exist_ok=True)
                for name, x in sorted(ftp._list(f, f"{base}/{sub}", None)):
                    if not FRAME.match(name):
                        continue
                    size, target = int(x.get("size") or -1), folder / name
                    if not (target.exists() and target.stat().st_size == size):
                        tmp = folder / (name + ".part")
                        with open(tmp, "wb") as out:
                            f.retrbinary(f"RETR {base}/{sub}/{name}", out.write)
                        if size > 0 and tmp.stat().st_size != size:
                            tmp.unlink(missing_ok=True)
                            raise OSError("Datei unvollständig")
                        tmp.replace(target)
                        new += 1
                        j["files"] += 1
                    j["done"] += 1
        finally:
            try:
                f.quit()
            except Exception:
                pass
        return new
