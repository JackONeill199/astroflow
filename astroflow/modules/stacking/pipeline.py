"""Gesamtablauf: scannen -> kalibrieren -> analysieren -> auswählen -> stacken -> bearbeiten -> prüfen."""
from __future__ import annotations

import json
import math
import re
import shutil
import threading
import time
import warnings
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import numpy as np
from PIL import Image

from . import calibration, channels, comet, develop, stackfix, gpu, graxpert, live, objects, photometry, platesolve, processing, quality, registration, reviewer, scanner, stars, trails, tutor
from ...core.config import Settings, free_ram_mb
from ...core.errors import user_message
from . import diskspace
from .db import DB
from .fits_io import FrameInfo, parse_date, read_data, to_channels, write_fits
from .stacking import StackState, batch_size_for

DEFAULT_OPTIONS = {
    "profile": "auto",          # auto | generic | galaxy | emission | ...
    "keep_percent": None,       # None = nur Ausreißer verwerfen
    "resolution_scale": 1,      # 2 = 2x überabgetastet (viel RAM)
    "params": {},               # Überschreibungen der Profilparameter
    "narrowband": None,         # None = automatisch aus Filtername
    "palette": "auto",          # Mono-Mehrfilter: auto | RGB | LRGB | SHO | HOO | mono
    "review": True,             # KI-Prüfung, falls konfiguriert
    "sensor_glow": True,        # ortsfestes Randglühen vor der Registrierung abziehen (nur ohne Darks)
    "star_separation": None,    # None = Profil (aus) | off | auto | intern | starnet
    "graxpert": None,           # None = Einstellung | off | bge | denoise | all (KI-Hintergrund/-Entrauschen, falls installiert)
    "comet_mode": "auto",       # Kometenmodus: auto (nach Name/Typ) | on | off – nicht „mode“, das heißt bei Aufträgen „develop“
    "comet_name": None,         # Bezeichnung für JPL Horizons, falls der Name keine enthält (z. B. „C/2022 E3“)
    "distortion": "global",     # Verzerrungskorrektur: global = Einstellung (Standard an) | auto | off (registration.distortion_mode)
}
GLOW_SAMPLE = 30                # Frames je Nacht für die Glühmessung
CHECKPOINT_S = 120              # Stack-Zustand spätestens alle 2 min sichern (Wiederaufnahme nach Abbruch)
STAGES = (1, 3, 10, 30, 100, 300, 1000, 3000)   # Stacking-Stufen (Aufnahmen) für den Verlaufsregler im Bild-Tab
STAGE_SCALE = 8.0               # nur zum Lesen älterer float16-Stufen; neue Stufen sind kleine Live-JPGs


def band_of_row(r: dict) -> str | None:
    i = json.loads(r["info"])
    return channels.band_of(i.get("filter"), channels.is_color(i))


def stack_dir(wd: Path, band: str | None) -> Path:
    return wd / ("stack" if band is None else f"stack_{band}")


def comet_dir(wd: Path, band: str | None) -> Path:
    return wd / ("stack_comet" if band is None else f"stack_comet_{band}")


def frame_time(r: dict) -> float | None:
    """Mitte der Belichtung als Unixzeit (DATE-OBS ist UTC)."""
    i = json.loads(r["info"])
    d = parse_date(i.get("date_obs"))
    if d is None:
        return None
    from datetime import timezone
    d = d.replace(tzinfo=timezone.utc) if d.tzinfo is None else d
    return d.timestamp() + 0.5 * float(i.get("exptime") or 0)


def stage_dir(wd: Path, band: str | None) -> Path:
    return wd / ("stufen" if band is None else f"stufen_{band}")


def load_stages(d: Path) -> list[dict]:
    """Gespeicherte Stacking-Stufen, aufsteigend: [{n, exposure, path}]."""
    out = []
    for f in sorted(d.glob("n*.json")):
        try:
            path = f.with_suffix('.jpg') if f.with_suffix('.jpg').exists() else f.with_suffix('.npz')
            out.append({**json.loads(f.read_text()), "path": path})
        except Exception:
            pass
    return sorted((x for x in out if x["path"].exists()), key=lambda x: x["n"])


def info_from_row(r: dict) -> FrameInfo:
    d = json.loads(r["info"])
    d["shape"] = tuple(d["shape"])
    return FrameInfo(**{k: v for k, v in d.items() if k in FrameInfo.__dataclass_fields__})


def needs_solve(info: FrameInfo) -> bool:
    return info.ra is None and objects.resolve_name(info.object) is None


def solve_group(path: str, info: FrameInfo, night: str) -> str:
    return f"{Path(path).parent}|{info.camera}|{info.hw[1]}x{info.hw[0]}|{night}"


def slug(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", s).strip("_")[:60] or "projekt"



def resource_error(e: BaseException) -> bool:
    """Fehler, die nichts über die Aufnahme sagen (Abbruch, volle Platte, RAM, abgesteckte Quelle): weiterwerfen –
    der Auftrag hält an bzw. wartet, statt gesunde Frames als „Lesefehler“ oder einen Teilschritt als gescheitert zu markieren."""
    return (isinstance(e, (InterruptedError, diskspace.DiskFull, calibration.SourceOffline, MemoryError))
            or diskspace.is_full(e))

class Pipeline:
    def __init__(self, settings: Settings, db: DB, review_cfg: reviewer.ReviewerConfig | None = None,
                 solver_cfg: platesolve.SolverConfig | None = None):
        self.s = settings
        self.db = db
        self.review_cfg = review_cfg or reviewer.ReviewerConfig.from_env()
        self.solver_cfg = solver_cfg or platesolve.SolverConfig.from_env()
        self._scan_lock = threading.Lock()     # Watcher, API-Scan und Nach-Solving nie gleichzeitig
        self.on_stacked = None                 # fn(project_id, result_id): neue Aufnahmen gestackt, Rohbilder noch vorhanden
        self.live_runs = {}                     # getrennt je Objekt: Stacking und Entwicklung laufen parallel
        self.site_provider = None               # fn(Nacht) -> [Breite, Länge] | None (Ortsliste bzw. Tutor-Einstellung)

    def begin_live(self, pid, job=None):
        old = self.live_runs.get(pid)
        if old is not None:                           # alter Lauf (auch Fortsetzen): seine Hintergrund-Stände verwerfen
            live.optional(old.close)
        run = live.optional(live.LiveRun, self.s.work_dir, pid, job)
        self.live_runs[pid] = run
        return run

    def _track_live(self, pid, progress):
        run = self.live_runs.get(pid)
        if run is None or run.finished:
            run = self.begin_live(pid)

        def prog(p, stage, msg=''):
            if run:
                p = max(p, run.data.get('progress', 0))
                run.data['progress'] = p
            progress(p, stage, msg)              # Abbruch/Plattenprüfung niemals als Vorschaufehler verschlucken
            if run:
                live.optional(run.event, stage, msg)
        return run, prog

    # ------------------------------------------------------------------ scan #
    def scan(self, extra: list[Path] | None = None) -> dict:
        """extra: am Ort eingebundene Dateien außerhalb von input/ (Hauptordner-Import, nur einmal beim Import gelesen –
        der Watcher sieht nur input/, eine abgesteckte Platte stört ihn also nicht)."""
        with self._scan_lock:
            return self._scan(extra)

    def _scan(self, extra: list[Path] | None = None) -> dict:
        known = {r["id"] for r in self.db.q("SELECT id FROM frames")}
        frames, errors = scanner.scan_dir(self.s.input_dir, known, self.s.threads, extra)
        # inhaltsgleiche Dateien (z. B. derselbe Ordner erneut hochgeladen) nie doppelt stacken
        seen = self._fingerprints()
        unique, dups = [], []
        for f in sorted(frames, key=lambda f: f.path):
            if f.info.fp and f.info.fp in seen:
                dups.append((f, seen[f.info.fp]))
            else:
                if f.info.fp:
                    seen[f.info.fp] = f.path
                unique.append(f)
        dup_by_proj: dict[int, int] = {}                     # Rückmeldung an den Import: „bereits in Objekt X“
        owner = {r["path"]: r["project_id"] for r in self.db.q("SELECT path, project_id FROM frames WHERE project_id IS NOT NULL")} if dups else {}
        for f, orig in dups:
            if owner.get(orig):
                dup_by_proj[owner[orig]] = dup_by_proj.get(owner[orig], 0) + 1
            if self.s.delete_uploads and self._is_upload(f.path):
                Path(f.path).unlink(missing_ok=True)       # hochgeladene Kopie: sofort Speicher freigeben
                continue
            self.db.x("INSERT OR IGNORE INTO frames(id,project_id,path,kind,night,info,status,reason) VALUES(?,?,?,?,?,?,?,?)",
                      (f.id, None, f.path, "duplicate", f.night, json.dumps(f.info.to_dict()), "duplicate",
                       f"identisch mit {Path(orig).name} ({Path(orig).parent.name})"))
        frames = unique
        centers = {p["key"]: (p["ra"], p["dec"], p["radius"]) for p in self.db.q("SELECT * FROM projects") if p["ra"] is not None}
        # Lights ohne Koordinaten und ohne bekannten Objektnamen: Plate-Solving je Aufnahmegruppe
        unsolved = [(f.path, f.info, f.night) for f in frames if f.kind == "light" and needs_solve(f.info)]
        solved = 0
        if unsolved:
            sols, msgs = self._solve_groups(unsolved)
            errors += msgs
            for p, i, n in unsolved:
                solved += self._apply_solution(p, i, n, sols)
        new_by_proj: dict[int, int] = {}
        for f in sorted(frames, key=lambda f: f.info.date_obs or ""):
            if f.kind == "light":
                pid = self._project_for(f.info, centers, f.night)
                new_by_proj[pid] = new_by_proj.get(pid, 0) + 1
            else:
                pid = None
            try:
                size = Path(f.path).stat().st_size                 # auch für den Namensabgleich der Hauptordner-Suche
            except OSError:
                size = 0
            self.db.x("INSERT OR IGNORE INTO frames(id,project_id,path,kind,night,info,size,status,reason) VALUES(?,?,?,?,?,?,?,?,?)",
                      (f.id, pid, f.path, f.kind, f.night, json.dumps(f.info.to_dict()), size,
                       "ignored" if f.kind == "processed" else "new", f.info.processed or ""))
        return {"new_frames": len(frames), "new_by_project": new_by_proj, "errors": errors, "solved": solved,
                "duplicates": len(dups), "duplicates_by_project": dup_by_proj}

    def _is_upload(self, path: str) -> bool:
        try:
            Path(path).resolve().relative_to(self.s.uploads_dir.resolve())
            return True
        except ValueError:
            return False

    @staticmethod
    def offline(rows: list[dict]) -> set[str]:
        """Frames, deren Rohdatei nicht gelöscht, aber gerade nicht erreichbar ist (z. B. USB-Platte mit am Ort
        eingebundenen Aufnahmen abgesteckt). Sie bleiben in der Datenbank unverändert und kommen später dran."""
        return {r["id"] for r in rows if not r["deleted"] and r["path"] and not Path(r["path"]).exists()}

    @staticmethod
    def offline_hint(rows: list[dict], ids: set[str]) -> str:
        dirs = sorted({str(Path(r["path"]).parent) for r in rows if r["id"] in ids})
        return dirs[0] + (f" (und {len(dirs) - 1} weitere Ordner)" if len(dirs) > 1 else "") if dirs else ""

    @staticmethod
    def offline_error(rows: list[dict], ids: set[str]) -> calibration.SourceOffline:
        return calibration.offline_error("Aufnahmen", [r["path"] for r in rows if r["id"] in ids])

    def delete_stacked_uploads(self, project_id: int) -> tuple[int, int]:
        """Hochgeladene Light-Aufnahmen löschen, deren Information im gespeicherten Stack steckt (gestackt)
        oder die verworfen wurden. Nur Dateien unter input/uploads – eingebundene Ordner (NAS) und am Ort eingebundene
        Originale aus „Hauptordner durchsuchen“ bleiben unberührt, Kalibrierbilder ebenso. Gibt (Anzahl, Bytes) zurück."""
        n = freed = 0
        for r in self.db.q("SELECT id, path, status, stacked FROM frames WHERE project_id=? AND kind='light' AND deleted=0 "
                           "AND (stacked=1 OR status='rejected')", (project_id,)):
            if not self._is_upload(r["path"]):
                continue
            p = Path(r["path"])
            size = p.stat().st_size if p.exists() else 0
            p.unlink(missing_ok=True)
            self.db.set_frame(r["id"], deleted=1, size=size)
            n += 1
            freed += size
        return n, freed

    def delete_project(self, project_id: int) -> dict:
        """Objekt vollständig entfernen: Ergebnisse (alle Versionen), Master-Stack, Jobs und hochgeladene Rohbilder.
        Dateien aus eingebundenen Ordnern (NAS) werden nie gelöscht, sondern als „ignoriert“ markiert – sonst legte der
        nächste Scan das Objekt sofort wieder an. Die Glüh-Bibliothek der Kamera bleibt erhalten."""
        proj = self.db.one("SELECT * FROM projects WHERE id=?", (project_id,))
        if not proj:
            raise ValueError("Projekt nicht gefunden")
        old = self.live_runs.pop(project_id, None)
        if old is not None:                           # Live-Ansicht: Hintergrund-Thread beenden, nichts mehr schreiben
            live.optional(old.close)
        files = freed = ignored = 0
        for r in self.db.q("SELECT id, path, deleted FROM frames WHERE project_id=?", (project_id,)):
            if self._is_upload(r["path"]):
                p = Path(r["path"])
                if not r["deleted"] and p.exists():
                    freed += p.stat().st_size
                    p.unlink()
                    files += 1
                self.db.x("DELETE FROM frames WHERE id=?", (r["id"],))
            else:
                self.db.set_frame(r["id"], project_id=None, kind="ignored", status="ignored", stacked=0,
                                  reason=f"Objekt „{proj['name']}“ gelöscht")
                ignored += 1
        out_dirs = set()
        for r in self.db.q("SELECT files FROM results WHERE project_id=?", (project_id,)):
            for f in json.loads(r["files"] or "{}").values():
                p = Path(f)
                if p.exists():
                    freed += p.stat().st_size
                    p.unlink()
                out_dirs.add(p.parent)
        for d in out_dirs:                                   # Ausgabeordner nur entfernen, wenn leer
            try:
                d.rmdir()
            except OSError:
                pass
        wd = self.s.work_dir / f"p{project_id}"
        if wd.exists():
            freed += sum(f.stat().st_size for f in wd.rglob("*") if f.is_file())
            shutil.rmtree(wd, ignore_errors=True)
        self.db.x("DELETE FROM results WHERE project_id=?", (project_id,))
        self.db.x("DELETE FROM jobs WHERE project_id=?", (project_id,))
        try:                                                 # Himmelsqualität (Tabelle entsteht erst mit skyquality)
            self.db.x("DELETE FROM sky_nights WHERE project_id=?", (project_id,))
        except Exception:
            pass
        self.db.x("UPDATE projects SET options=json_remove(options,'$.gallery_parent') WHERE json_extract(options,'$.gallery_parent')=?", (project_id,))
        self.db.x("DELETE FROM projects WHERE id=?", (project_id,))
        return {"name": proj["name"], "files": files, "ignored": ignored, "bytes": freed}

    def _fingerprints(self) -> dict[str, str]:
        """Fingerabdruck -> Pfad aller bekannten (Nicht-Duplikat-)Frames. Fehlende Fingerabdrücke werden einmalig
        nachgetragen; dabei entdeckte Duplikate bereits eingelesener Frames werden nachträglich markiert
        (behalten wird der gestackte bzw. zuerst eingelesene Frame)."""
        out = {}
        for r in self.db.q("SELECT id, project_id, path, info, stacked FROM frames WHERE kind NOT IN ('duplicate','ignored') "
                           "ORDER BY stacked DESC, rowid"):
            i = json.loads(r["info"])
            fp = i.get("fp")
            if not fp and Path(r["path"]).exists():
                fp = i["fp"] = scanner.fingerprint(r["path"])
                self.db.set_frame(r["id"], info=i)
            if not fp:
                continue
            if fp in out:
                orig = out[fp]
                self.db.set_frame(r["id"], kind="duplicate", project_id=None, status="duplicate", stacked=0,
                                  reason=f"identisch mit {Path(orig).name} ({Path(orig).parent.name})")
                if r["stacked"] and r["project_id"]:
                    self.db.x("UPDATE projects SET restack=1 WHERE id=?", (r["project_id"],))
            else:
                out[fp] = r["path"]
        return out

    def _project_for(self, info: FrameInfo, centers: dict, night: str | None = None) -> int:
        a = scanner.assign_project(info, centers, night)
        row = self.db.one("SELECT id FROM projects WHERE key=?", (a.key,))
        if row:
            return row["id"]
        cat = objects.resolve_name(a.obj_id) if a.obj_id else None
        radius = max(0.5, (cat.size / 60 if cat else 0.5))
        self.db.x(
            "INSERT OR IGNORE INTO projects(key,name,obj_id,kind,ra,dec,radius,options,created) VALUES(?,?,?,?,?,?,?,?,?)",
            (a.key, a.name or a.key.split("|")[0], a.obj_id, a.kind, a.ra, a.dec, radius, "{}", time.time()))
        pid = self.db.one("SELECT id FROM projects WHERE key=?", (a.key,))["id"]
        if a.ra is not None and a.kind != "comet":
            centers[a.key] = (a.ra, a.dec, radius)
        return pid

    # ----------------------------------------------------------- plate solve #
    def _solve_groups(self, items: list[tuple[str, FrameInfo, str]], force=False, log=lambda m: None) -> tuple[dict, list[str]]:
        """items: (Pfad, Info, Nacht). Je Gruppe (Ordner + Kamera + Nacht) wird eine Aufnahme gelöst,
        das Ergebnis gilt für die ganze Gruppe und wird gecacht. Gibt (Gruppe -> Lösung|None, Meldungen)."""
        groups: dict[str, list] = {}
        for it in items:
            groups.setdefault(solve_group(*it), []).append(it)
        out, msgs = {}, []
        for n, (key, its) in enumerate(groups.items()):
            cached = self.db.one("SELECT * FROM solves WHERE key=?", (key,))
            if cached and (cached["status"] == "ok" or not force):
                out[key] = json.loads(cached["result"]) if cached["status"] == "ok" else None
                continue
            if not self.solver_cfg.enabled:
                out[key] = None
                msgs.append("Aufnahmen ohne Koordinaten gefunden, aber kein Plate-Solver eingerichtet (ASTAP empfohlen)")
                continue
            its = sorted(its, key=lambda it: it[1].date_obs or it[0])
            picks = list(dict.fromkeys([its[len(its) // 2][0], its[0][0], its[-1][0]]))[:2]
            sol, err = None, ""
            for path in picks:
                info = next(i for p, i, _ in its if p == path)
                log(f"Gruppe {n + 1}/{len(groups)}: {Path(path).name}")
                try:
                    sol = platesolve.solve_frame(self.solver_cfg, path, bool(info.bayerpat), info.instrument,
                                                 self.s.work_dir / "solve").to_dict()
                    break
                except platesolve.SolveError as e:
                    err = str(e)
                except Exception as e:
                    err = f"Plate-Solving: {e}"
            self.db.x("INSERT OR REPLACE INTO solves(key,status,result,message,created) VALUES(?,?,?,?,?)",
                      (key, "ok" if sol else "failed", json.dumps(sol), err, time.time()))
            out[key] = sol
            if not sol:
                msgs.append(f"Plate-Solving fehlgeschlagen ({Path(its[0][0]).parent.name}): {err}")
        return out, list(dict.fromkeys(msgs))

    @staticmethod
    def _apply_solution(path: str, info: FrameInfo, night: str, sols: dict) -> bool:
        sol = sols.get(solve_group(path, info, night))
        if sol:
            info.ra, info.dec, info.solve = sol["ra"], sol["dec"], sol
        return bool(sol)

    def solve_project(self, project_id: int, progress=lambda p, stage, msg="": None) -> dict:
        """Nachträglich lösen (z. B. „Unbekanntes Objekt“) und Aufnahmen ins passende Projekt verschieben."""
        with self._scan_lock:
            return self._solve_project(project_id, progress)

    def _solve_project(self, project_id: int, progress) -> dict:
        rows = self.db.q("SELECT * FROM frames WHERE project_id=? AND kind='light'", (project_id,))
        items = [(r, info_from_row(r)) for r in rows]
        todo = [(r["path"], i, r["night"]) for r, i in items if i.solve is None]
        progress(0.05, "Plate-Solving", f"{len(todo)} Aufnahmen ohne gelöste Koordinaten")
        sols, msgs = self._solve_groups(todo, force=True, log=lambda m: progress(0.4, "Plate-Solving", m))
        centers = {p["key"]: (p["ra"], p["dec"], p["radius"]) for p in self.db.q("SELECT * FROM projects WHERE id!=?", (project_id,)) if p["ra"] is not None}
        moved: dict[int, int] = {}
        for r, info in items:
            if info.solve is None and not self._apply_solution(r["path"], info, r["night"], sols):
                continue
            pid = self._project_for(info, centers, r["night"])
            self.db.set_frame(r["id"], info=info.to_dict())
            if pid != project_id:
                self.db.set_frame(r["id"], project_id=pid, stacked=0)
                moved[pid] = moved.get(pid, 0) + 1
        left = self.db.one("SELECT COUNT(*) n FROM frames WHERE project_id=?", (project_id,))["n"]
        if left == 0:                     # leeres Projekt entfernen (ausgegebene Bilddateien bleiben erhalten)
            for t in ("results", "jobs"):
                self.db.x(f"DELETE FROM {t} WHERE project_id=?", (project_id,))
            self.db.x("DELETE FROM projects WHERE id=?", (project_id,))
            shutil.rmtree(self.s.work_dir / f"p{project_id}", ignore_errors=True)
        elif moved:
            self.db.x("UPDATE projects SET restack=1 WHERE id=?", (project_id,))
        progress(1.0, "Fertig")
        if not moved and msgs:
            raise ValueError(msgs[0])
        return {"moved": moved, "remaining": left, "messages": msgs}

    # --------------------------------------------------------------- helpers #
    def _options(self, project: dict, extra: dict | None) -> dict:
        o = {**DEFAULT_OPTIONS, **json.loads(project["options"] or "{}"), **(extra or {})}
        return o

    def _library(self) -> calibration.CalibrationLibrary:
        rows = self.db.q("SELECT * FROM frames WHERE kind IN ('dark','flat','bias')")
        cal = []
        for r in rows:
            i = json.loads(r["info"])
            cal.append(calibration.CalFrame(r["path"], r["kind"], tuple(i["shape"]), i["exptime"], i["gain"], i["filter"], r["night"], i["bayerpat"]))
        return calibration.CalibrationLibrary(cal, self.s.work_dir / "calibration")

    @staticmethod
    def _load(path: str, info: dict, lib: calibration.CalibrationLibrary, night: str, cache: dict):
        key = (tuple(info["shape"]), info["exptime"], info["gain"], info["filter"], night)
        if key not in cache:
            cache[key] = lib.masters_for(info["shape"], info["exptime"], info["gain"], info["filter"], night, info["bayerpat"])
        m = cache[key]
        cal = calibration.apply(read_data(path), m)
        # Hotpixel-Korrektur je Datei nur einmal je Lauf rechnen (Glühen, Analyse und Stacking laden dieselben Frames):
        # gemerkt werden nur die ersetzten Pixel – Ergebnis bitgleich, spart ~10 % der Stacking-Rechenzeit
        fix = cache.get(("cosmetic", path))
        if fix is not None and fix[0] == cal.shape:
            raw = cal.copy()
            raw.flat[fix[1]] = fix[2]
        else:
            raw = calibration.cosmetic(cal, bool(info["bayerpat"]))
            idx = np.flatnonzero(raw != cal)
            if idx.size < raw.size // 100:
                cache[("cosmetic", path)] = (cal.shape, idx, raw.flat[idx])
        # ortsfeste Sensorfehler der Nacht (Hotpixel-Paare/-Grüppchen, die cosmetic() als Stern schützt)
        bad = cache.get(("defects", calibration.GlowLibrary.key(info), night))
        if bad is not None and len(bad):
            raw = calibration.fix_defects(raw, bool(info["bayerpat"]) and raw.ndim == 2, bad)
        glow = cache.get(("glow", calibration.GlowLibrary.key(info), night))
        if glow and m.dark is None:                    # echte Darks enthalten das Glühen bereits
            raw = calibration.remove_glow(raw, bool(info["bayerpat"]) and raw.ndim == 2, glow)
        return raw, m

    def _sensor_maps(self, rows: list[dict], lib, cache: dict, progress, glow: bool = True) -> tuple[dict, dict]:
        """Glühmodelle und Karten ortsfester Sensorfehler je Kamera und Nacht in den Cache legen. Nächte mit genug
        vorhandenen Rohbildern werden an einer gemeinsamen Stichprobe gemessen (jede Datei nur einmal gelesen) und in
        die Bibliotheken der Kamera aufgenommen; sonst gelten die bisher gemessenen Nächte."""
        glib = calibration.GlowLibrary(self.s.work_dir / "glow")
        dlib = calibration.DefectLibrary(self.s.work_dir / "defects")
        groups: dict = {}
        for r in rows:
            info = json.loads(r["info"])
            groups.setdefault((glib.key(info), r["night"]), []).append((r, info))
        used, bad_used = {}, {}
        for (key, night), items in groups.items():
            have = [(r, i) for r, i in items if not r["deleted"] and Path(r["path"]).exists()]
            need_glow = glow and night not in glib.nights(key, with_map=True) and len(have) >= calibration.GLOW_MIN_FRAMES
            need_bad = night not in dlib.nights(key) and len(have) >= calibration.DEFECT_MIN_FRAMES
            if need_glow or need_bad:
                sample = have[:: max(1, len(have) // GLOW_SAMPLE)][:GLOW_SAMPLE]
                what = " und ".join(t for t, n in (("Sensorglühen", need_glow), ("Hotpixel", need_bad)) if n)
                progress(0.02, "Analyse", f"{what} messen ({night}, {len(sample)} Aufnahmen)")

                def prof(ri):
                    r, i = ri
                    raw, m = self._load(r["path"], i, lib, night, cache)
                    cfa = bool(i["bayerpat"]) and raw.ndim == 2
                    g = calibration.glow_profiles(raw, cfa) if need_glow and m.dark is None else None
                    fix = cache.get(("cosmetic", r["path"]))
                    c = calibration.defect_candidates(raw, cfa, fixed=fix[1] if fix else None) if need_bad else None
                    return g, c, raw.size

                with ThreadPoolExecutor(self.s.threads) as ex:
                    res = list(ex.map(prof, sample))
                if need_glow:
                    med = calibration.median_profiles([g for g, _, _ in res if g is not None])
                    if med:
                        glib.add(key, night, med)
                if need_bad:
                    bad = calibration.defect_map([c for _, c, _ in res], res[0][2])
                    dlib.add(key, night, bad if bad is not None else np.zeros(0, np.int64))
            if glow:
                med, src = glib.medians(key, night)
                model = calibration.model_from(med)
                if model:
                    cache[("glow", key, night)] = model
                    used[night] = {"quelle": src, "adu": round(calibration.glow_amplitude(model), 1)}
            bad, src = dlib.get(key, night)
            if bad is not None and len(bad):
                cache[("defects", key, night)] = bad
                bad_used[night] = {"quelle": src, "pixel": int(len(bad))}
        return used, bad_used

    _hint_cache: dict = {}

    def hotpixel_hint(self, pid: int) -> bool:
        """Stellina-Stack von vor der Fehlerkarte (0.31.2), neueste Version noch ohne Stack-Bereinigung (`stackfix`, 0.37.1)
        → „Neu entwickeln“ entfernt die Hotpixel-Punkte (auch ohne Rohbilder). Der Stack-Teil ist je Stand von state.json
        gecacht (die Objektansicht fragt alle 2,5 s)."""
        f = self.s.work_dir / f"p{pid}" / "stack" / "state.json"
        try:
            mt = f.stat().st_mtime
        except OSError:
            return False
        hit = self._hint_cache.get(str(f))
        if hit and hit[0] == mt:
            old = hit[1]
        else:
            old = False
            try:
                if "defects" not in json.loads(f.read_text(encoding="utf-8")):
                    r = self.db.one("SELECT info FROM frames WHERE project_id=? AND kind='light' AND stacked=1 LIMIT 1", (pid,))
                    old = bool(r) and "stellina" in (json.loads(r["info"]).get("instrument") or "").lower()
            except (OSError, ValueError):
                old = False
            self._hint_cache[str(f)] = (mt, old)
        if not old:
            return False
        r = self.db.one("SELECT json_extract(stats,'$.hotfix.v') v FROM results WHERE project_id=? ORDER BY id DESC LIMIT 1", (pid,))
        return bool(r) and (r["v"] or 0) < stackfix.VERSION

    # --------------------------------------------------------------- process #
    def process(self, project_id: int, extra: dict | None = None, progress=lambda p, stage, msg="": None) -> int:
        """Stack Engine + Develop Engine. extra["mode"] == "develop": nur neu entwickeln (kein Stacking)."""
        if (extra or {}).get("mode") == "develop":
            return self.develop(project_id, extra, progress, start=0.05)
        self.stack(project_id, extra, progress)
        return self.finish(project_id, extra, progress)

    def finish(self, project_id: int, extra: dict | None = None, progress=lambda p, stage, msg="": None) -> int:
        """Nach stack(): entwickeln, an Sage/SkyTrace melden, hochgeladene Einzelbilder löschen. Der Server führt das
        in einem eigenen Thread aus, während schon das nächste Objekt gestackt wird."""
        pending = self.db.q("SELECT id FROM frames WHERE project_id=? AND stacked=1 AND stack_notified=0", (project_id,))
        rid = self.develop(project_id, extra, progress, start=0.86)
        # Dauerhaft pro Aufnahme bestätigen: ein Checkpoint/Abbruch ist noch keine zugestellte Beobachtung.
        # Bei Fehlern bleiben Meldung und Rohbilder für den nächsten Versuch erhalten.
        if pending:
            if self.on_stacked:
                self.on_stacked(project_id, rid)
            self.db.many("UPDATE frames SET stack_notified=1 WHERE id=?", [(r["id"],) for r in pending])
        # erst nach gespeichertem Stack und Ergebnis: hochgeladene Einzelbilder entfernen (Speicher)
        if self.s.delete_uploads:
            n, freed = self.delete_stacked_uploads(project_id)
            if n:
                progress(1.0, "Fertig", f"{n} hochgeladene Einzelbilder gelöscht ({freed / 1e9:.1f} GB frei)")
                return rid
        progress(1.0, "Fertig")
        return rid

    # ========================================================= Stack Engine #
    def stack(self, project_id: int, extra: dict | None = None, progress=lambda p, stage, msg="": None) -> dict:
        """Analyse -> Auswahl -> Kalibrierung/Registrierung -> gewichtetes Sigma-Clip-Stacking.
        Ergebnis ist der lineare Master-Stack je Filter in work/p<id>/ (inkrementell, mit Checkpoints)."""
        _, progress = self._track_live(project_id, progress)
        proj = self.db.one("SELECT * FROM projects WHERE id=?", (project_id,))
        if not proj:
            raise ValueError("Projekt nicht gefunden")
        opt = self._options(proj, extra)
        wd = self.s.work_dir / f"p{project_id}"
        scale = int(opt["resolution_scale"])
        lib = self._library()
        cache: dict = {}
        rows = self.db.q("SELECT * FROM frames WHERE project_id=? AND kind='light' ORDER BY id", (project_id,))
        if not rows:
            raise ValueError("keine Light-Frames")

        # --- 0. Sensorglühen (vor Analyse und Registrierung, in Sensorkoordinaten) --
        # (dazu ortsfeste Sensorfehler: Hotpixel-Paare/-Grüppchen, die die Einzelbild-Kosmetik als Stern schützt)
        glow, bad = self._sensor_maps(rows, lib, cache, progress, glow=opt.get("sensor_glow", True))
        for k, v in (("glow_info", glow), ("defect_info", bad)):
            if v:
                self.db.x(f"UPDATE projects SET options=json_set(COALESCE(options,'{{}}'),'$.{k}',json(?)) WHERE id=?",
                          (json.dumps(v), project_id))

        # --- 1. Analyse (nur neue Frames) ------------------------------------
        # neu analysieren: neue Frames und solche mit veralteter Analyse (z. B. vor der Hotpixel-Korrektur)
        # (gelöschte Rohdateien behalten ihre Analyse – sie lässt sich nicht wiederholen)
        # (nicht erreichbare Quellen ebenso: kein „Lesefehler“, sie kommen dran, sobald die Platte wieder da ist)
        offline = self.offline(rows)
        lights = rows
        todo = [r for r in rows if not r["deleted"] and r["id"] not in offline and
                (not r["metrics"] or json.loads(r["metrics"]).get("mv", 1) < quality.METRICS_VERSION)]
        if any(r["metrics"] and r["stacked"] for r in todo) and not any((r["deleted"] or r["id"] in offline) and r["stacked"] for r in rows):
            self.db.x("UPDATE projects SET restack=1 WHERE id=?", (project_id,))
            proj["restack"] = 1
        progress(0.02, "Analyse", f"{len(todo)} neue Aufnahmen analysieren"
                 + (f" ({len(offline)} nicht erreichbar: {self.offline_hint(rows, offline)})" if offline else ""))

        def analyze(r):
            try:
                info = json.loads(r["info"])
                raw, m = self._load(r["path"], info, lib, r["night"], cache)
                met = quality.analyze_frame(raw, bool(info["bayerpat"]) and raw.ndim == 2)
                met["calib"] = m.desc
                met["mv"] = quality.METRICS_VERSION
                return r["id"], met, None
            except calibration.SourceOffline as e:     # Kalibrierbilder auf abgesteckter Platte: kein Lesefehler
                return r["id"], None, e
            except Exception as e:
                if resource_error(e):                  # RAM/Platte knapp: Auftrag hält an, die Aufnahme bleibt heil
                    raise
                msg = user_message(e, "Datei nicht lesbar – Details im Server-Protokoll.", log=False)
                if msg != str(e).strip():                  # Ausnahmetext (Pfad …) nur ins Protokoll
                    print(f"Lesefehler {r['path']}: {e!r}", flush=True)
                return r["id"], None, msg

        waiting = None
        with ThreadPoolExecutor(self.s.threads) as ex:
            for n, (fid, met, err) in enumerate(ex.map(analyze, todo)):
                if isinstance(err, calibration.SourceOffline):
                    waiting = err
                elif err:
                    self.db.set_frame(fid, status="failed", reason=f"Lesefehler: {err}", metrics={"n_stars": 0})
                else:
                    self.db.set_frame(fid, metrics=met, status="analyzed")
                progress(0.02 + 0.30 * (n + 1) / max(1, len(todo)), "Analyse")
        if waiting:
            raise waiting

        # --- 2. Auswahl (je Filter getrennt) ---------------------------------
        rows = self.db.q("SELECT * FROM frames WHERE project_id=? AND kind='light'", (project_id,))
        band = {r["id"]: band_of_row(r) for r in rows}
        metrics = {r["id"]: json.loads(r["metrics"]) for r in rows if r["metrics"] and r["status"] != "failed"}
        sel: dict = {}
        for b in set(band.values()):
            sel.update(quality.select_frames({k: m for k, m in metrics.items() if band[k] == b}, opt.get("keep_percent")))
        for r in rows:
            if r["id"] not in sel or r["deleted"]:          # gelöschte Rohdateien: Status bleibt, wie er war
                continue
            ok, reason, sc = sel[r["id"]]
            if r["manual"] == 1:
                ok, reason = True, "manuell eingeschlossen"
            elif r["manual"] == -1:
                ok, reason = False, "manuell ausgeschlossen"
            if r["stacked"] and not ok and r["manual"] != -1:
                ok, reason = True, "bereits gestackt"
            self.db.set_frame(r["id"], status="selected" if ok else "rejected", reason=reason, score=sc)
        progress(0.34, "Auswahl")

        # --- 3. Stacking (ein Stack je Filter, gemeinsame Referenz) -----------
        rows = self.db.q("SELECT * FROM frames WHERE project_id=? AND kind='light' AND status IN ('selected','rejected') ORDER BY score DESC", (project_id,))
        by_band: dict = {}
        for r in rows:
            by_band.setdefault(band_of_row(r), []).append(r)
        wanted = {b: [r for r in rs if r["status"] == "selected"] for b, rs in by_band.items()}
        if sum(len(w) for w in wanted.values()) < 3:
            if offline:                                # erste Verarbeitung, Platte abgesteckt: warten statt Fehler
                raise self.offline_error(lights, offline)
            raise ValueError(f"zu wenige brauchbare Aufnahmen ({sum(len(w) for w in wanted.values())})")
        states = {} if proj["restack"] else {b: StackState.load(stack_dir(wd, b)) for b in by_band}
        states = {b: st for b, st in states.items() if st is not None}
        if any(st.meta.get("scale") != scale for st in states.values()) or len({st.meta.get("ref_fid") for st in states.values()}) > 1:
            states = {}
        for b in by_band:
            if b not in states and (gone := [r for r in by_band[b] if r["id"] in offline and r["status"] == "selected"]):
                raise self.offline_error(gone, {r["id"] for r in gone})
            if b not in states and any(r["deleted"] and r["stacked"] for r in by_band[b]):
                self.db.x("UPDATE projects SET restack=0 WHERE id=?", (project_id,))
                raise ValueError("Komplett neu stacken ist nicht möglich: die hochgeladenen Einzelbilder wurden nach dem "
                                 "Stacking gelöscht. Neue Aufnahmen werden weiterhin ergänzt, die Bearbeitung funktioniert wie gewohnt.")
        for b in by_band:
            if b not in states:
                ids = [r["id"] for r in by_band[b]]
                self.db.many("UPDATE frames SET stacked=0 WHERE id=?", [(i,) for i in ids])
                for r in by_band[b]:
                    r["stacked"] = 0
                shutil.rmtree(stack_dir(wd, b), ignore_errors=True)
                shutil.rmtree(stage_dir(wd, b), ignore_errors=True)
                shutil.rmtree(comet_dir(wd, b), ignore_errors=True)
        # Neuaufbau vorbereitet: nach einem Abbruch geht es an den Checkpoints weiter statt von vorn
        self.db.x("UPDATE projects SET restack=0 WHERE id=?", (project_id,))
        # Breitband zuerst (bestes Referenzbild), dann Schmalband; je Band nach Menge
        order = sorted(by_band, key=lambda b: (b in channels.NARROW, -len(wanted[b])))
        n_all = sum(len(w) for w in wanted.values())
        with_comet = comet.active(proj, opt)
        dist = registration.distortion_mode(opt.get("distortion")) == "auto"
        full = progress
        if with_comet:                                   # Sterne-Stack 36–60 %, Kometen-Stack 60–84 %
            progress = lambda f, stage, msg="", _p=full: _p(0.36 + (f - 0.36) * 0.5 if f > 0.36 else f, stage, msg)
        done_before = 0
        for b in order:
            st = states.get(b)
            if st is None and len(wanted[b]) < 3:
                continue                               # zu wenig für einen eigenen Kanal
            todo = [r for r in wanted[b] if not r["stacked"] and not r["deleted"] and r["id"] not in offline]
            if todo or st is None:
                ref = next(iter(states.values())).meta if states else None
                lo, span = done_before / n_all, len(wanted[b]) / n_all
                sub = lambda f, stage, msg="", lo=lo, span=span, b=b: progress(
                    0.36 + 0.48 * (lo + span * (f - 0.36) / 0.48), stage, (f"[{b}] " if b else "") + msg)
                st = self._stack(project_id, todo or wanted[b], st, lib, cache, scale, sub, ref, save_dir=stack_dir(wd, b),
                                 stages=stage_dir(wd, b), distortion=dist)
                states[b] = st
            done_before += len(wanted[b])
        if not states:
            raise ValueError("kein Kanal mit genug brauchbaren Aufnahmen")
        if with_comet:
            self._comet(proj, opt, states, by_band, lib, cache, scale, offline,
                        lambda f, stage, msg="": full(0.60 + 0.24 * f, stage, msg))
        else:
            (wd / "comet.json").unlink(missing_ok=True)
        return states

    # ============================================================ Kometen #
    def _comet(self, proj, opt, states, by_band, lib, cache, scale, offline, progress) -> None:
        """Zweiter Stack auf den Kometenkern (work/p<id>/stack_comet). Fehler hier brechen das Stacking nie ab –
        dann gibt es nur den Sterne-Stack und den Grund in work/p<id>/comet.json."""
        wd = self.s.work_dir / f"p{proj['id']}"
        info_f = wd / "comet.json"
        info: dict = {"active": True}
        try:
            if len(states) != 1:
                raise ValueError("Kometenmodus nur für Farbkameras bzw. einen Filter – mehrere Filter werden auf die Sterne gestackt")
            (band, A), = states.items()
            info.update(self._comet_stack(proj, opt, band, A, by_band[band], lib, cache, scale, offline, progress))
        except Exception as e:
            if resource_error(e):
                raise
            info["error"] = user_message(e, "Kometenstack fehlgeschlagen – Details im Server-Protokoll.")[:300]
            for b in by_band:
                shutil.rmtree(comet_dir(wd, b), ignore_errors=True)
        wd.mkdir(parents=True, exist_ok=True)
        info_f.write_text(json.dumps(info), encoding="utf-8")

    def _site(self, rows) -> list | None:
        sites = [tuple(json.loads(r["info"]).get("site") or ()) for r in rows]
        sites = [x for x in sites if len(x) == 2]
        if sites:
            return list(max(set(sites), key=sites.count))
        nights = [r["night"] for r in rows if r["night"]]
        try:
            return self.site_provider(max(set(nights), key=nights.count) if nights else None) if self.site_provider else None
        except Exception:
            return None

    def _comet_stack(self, proj, opt, band, A, rows, lib, cache, scale, offline, progress) -> dict:
        wd = self.s.work_dir / f"p{proj['id']}"
        cdir = comet_dir(wd, band)
        in_a = [r for r in rows if r["id"] in A.meta["frames"]]
        times = {r["id"]: frame_time(r) for r in in_a}
        if len(in_a) < 3 or any(t is None for t in times.values()):
            raise ValueError("zu wenige Aufnahmen mit Aufnahmezeit für den Kometen-Stack")
        old = StackState.load(cdir)
        if old and (old.meta.get("ref_fid") != A.meta.get("ref_fid") or old.meta.get("scale") != scale):
            old = None
        otrack = comet.Track.from_dict(old.meta.get("comet")) if old else None
        prev = self.comet_info(proj["id"]) or {}
        desig = comet.designation(opt.get("comet_name") or "") or comet.designation(proj["name"])
        same_desig = (old.meta.get("comet") or {}).get("designation", prev.get("designation")) == desig if old else False
        if old and otrack and same_desig and all(f in old.meta["frames"] for f in times):
            return {**prev, "active": True, "frames": len(old.meta["frames"])}
        progress(0.0, "Kometenbahn", "Kometenkern suchen")
        try:
            track, info = self._comet_track(proj, opt, A, in_a, times, lib, cache, scale, offline, progress)
        except Exception as e:
            if resource_error(e) or not (old and otrack):
                raise
            # z. B. nur 1–3 neue Aufnahmen, ältere Einzelbilder gelöscht: bisherige Bahn weiterverwenden, Stack behalten
            track = otrack
            info = {k: v for k, v in prev.items() if k not in ("frames", "error", "note", "active")}
            info["track_note"] = f"Bahn nicht neu bestimmbar ({user_message(e, 'Details im Server-Protokoll')[:120]}) – bisherige Bahn weiter genutzt"
        track.info["designation"] = info.get("designation")
        fwhm = float(A.meta.get("ref_fwhm") or 3.0)
        tt = np.array(sorted(times.values()))
        pos = track.at(tt)
        motion = float(np.hypot(*(pos[-1] - pos[0])))
        info.update(motion_px=round(motion, 1), span_s=round(float(tt[-1] - tt[0]), 1))
        if motion < comet.MIN_MOTION_FWHM * fwhm:
            shutil.rmtree(cdir, ignore_errors=True)
            info["note"] = (f"Der Komet bewegte sich während der Aufnahmen nur um {motion:.1f} px (Sterne ~{fwhm:.1f} px) – "
                            "ein Stack auf die Sterne zeigt ihn bereits scharf.")
            return info
        if old and otrack:
            dev = float(np.max(np.hypot(*(track.at(tt) - otrack.at(tt)).T)))
            gone = any(r["deleted"] or r["id"] in offline for r in in_a if r["id"] in old.meta["frames"])
            if dev > comet.REBUILD_PX and not gone:
                old = None                              # Bahn deutlich anders (neue Aufnahmen, Ephemeride) -> neu aufbauen
            else:
                if dev > comet.REBUILD_PX:
                    info["track_note"] = (f"Neue Bahn weicht um {dev:.1f} px ab, die gestackten Einzelbilder sind aber nicht "
                                          "mehr verfügbar – der bisherige Kometen-Stack bleibt")
                otrack.info["designation"] = track.info.get("designation")
                track = otrack                          # bisherige Bahn beibehalten: der Stack bleibt in sich stimmig
            if old is not None:
                old.meta["comet"] = track.to_dict()
        if old is None:
            shutil.rmtree(cdir, ignore_errors=True)
            state = StackState(A.sw.shape)
            state.meta.update({k: v for k, v in A.meta.items() if k not in ("frames", "exposure", "reg", "trails")}, frames={}, exposure=0.0)
            state.meta.setdefault("flux_k0", 1.0)
            state.meta["comet"] = track.to_dict()
        else:
            state = old
        todo = [r for r in in_a if r["id"] not in state.meta["frames"] and not r["deleted"] and r["id"] not in offline]
        if not todo and not state.meta["frames"]:
            raise ValueError("Die Einzelbilder wurden nach dem Stacking gelöscht – ein Kometen-Stack ist nicht mehr möglich.")
        # Verschiebung je Aufnahme (Referenzpixel): Kern zur Zeit t0 bleibt stehen
        c0 = track.at(track.t0)
        shifts = {fid: tuple((c0 - track.at(t)).tolist()) for fid, t in times.items()}
        # Sterne des Sterne-Stacks ausblenden, Bahn ausgenommen (Raster ggf. überabgetastet)
        meanA, _ = A.result()
        # Bahn nur ausnehmen, wenn der Komet so langsam ist, dass er im Sterne-Stack kompakt bleibt (sonst wäre sein Kern
        # maskiert); bei schnelleren bleiben dort sonst Sternspuren im Kometen-Stack stehen
        prot = comet.corridor(meanA.shape[1:], (pos + 0.5) * scale - 0.5, (0.5 * motion + 2 * fwhm) * scale)             if motion < comet.COMPACT_FWHM * fwhm else None
        lane = comet.corridor(meanA.shape[1:], (pos + 0.5) * scale - 0.5, 1.5 * fwhm * scale)
        mask = comet.star_mask_for(meanA.mean(0), fwhm * scale, prot, lane)
        info["masked_pct"] = round(100 * float(mask.mean()), 2)
        # Kern im Kometen-Stack nie ausblenden (Absicherung, falls doch ein Teil der Kometenspur als Stern galt)
        nuc = (c0 + 0.5) * scale - 0.5
        yy, xx = np.ogrid[: mask.shape[0], : mask.shape[1]]
        head = (xx - nuc[0]) ** 2 + (yy - nuc[1]) ** 2 <= (3 * fwhm * scale) ** 2
        if todo:
            progress(0.15, "Kometen-Stack", f"{len(todo)} Aufnahmen auf den Kometenkern ausrichten")
            sub = lambda f, stage, msg="": progress(0.15 + 0.85 * min(1.0, max(0.0, (f - 0.36) / 0.48)), "Kometen-Stack", msg)
            state = self._stack(proj["id"], todo, state, lib, cache, scale, sub, save_dir=cdir, stages=None,
                                shifts=shifts, mask=mask, mark=False, unmasked=head,
                                distortion=registration.distortion_mode(opt.get("distortion")) == "auto")
        info.update(frames=len(state.meta["frames"]), t0=track.t0, nucleus=[float(v) for v in (c0 + 0.5) * scale - 0.5],
                    track=[[round(float(x), 2), round(float(y), 2)] for x, y in
                           ((pos[np.unique(np.linspace(0, len(pos) - 1, 9).round().astype(int))] + 0.5) * scale - 0.5)])
        return info

    def _comet_track(self, proj, opt, A, rows, times, lib, cache, scale, offline, progress):
        """Kometenbahn im Referenzraster: Horizons (falls möglich) und/oder Kernsuche in Stichproben."""
        desig = comet.designation(opt.get("comet_name") or "") or comet.designation(proj["name"])
        info: dict = {"designation": desig}
        meanA, _ = A.result()
        LA = comet.bin_image(meanA.mean(0), scale) if scale > 1 else meanA.mean(0)
        fwhm = float(A.meta.get("ref_fwhm") or 3.0)
        tt = np.array(sorted(times.values()))
        prior = None
        if desig and comet.horizons_enabled():
            try:
                progress(0.02, "Kometenbahn", f"Bahn von {desig} bei JPL Horizons abfragen")
                hdr = self._stack_wcs(proj, A, meanA, lambda m: progress(0.02, "Kometenbahn", m))
                xy, topo = comet.ephemeris_pixels(desig, tt, hdr, scale, self._site(rows), self.s.work_dir / "komet")
                prior = comet.Track.fit(tt, xy, "horizons", deg=2 if tt[-1] - tt[0] > 1800 else 1)
                info.update(topocentric=topo)
            except (InterruptedError, diskspace.DiskFull):
                raise
            except Exception as e:
                info["ephemeris_error"] = user_message(e, "Bahnabfrage fehlgeschlagen – Details im Server-Protokoll.")[:200]
        elif desig:
            info["ephemeris_error"] = "JPL Horizons abgeschaltet"
        # Stichproben über die Zeit verteilt
        avail = sorted((r for r in rows if not r["deleted"] and r["id"] not in offline), key=lambda r: times[r["id"]])
        pick = [avail[int(i)] for i in np.unique(np.linspace(0, len(avail) - 1, min(comet.SAMPLE, len(avail))).round())] if avail else []
        H, W = LA.shape
        b = max(1, int(round(max(H, W) / 800)))
        LAb = comet.bin_image(LA, b)
        ref_xy = np.array([[s[0], s[1]] for s in A.meta["ref_stars"]])
        ref_flux = np.array([s[2] for s in A.meta["ref_stars"]])
        k0 = A.meta.get("flux_k0") or 1.0
        # Verzerrungskorrektur: Stichproben genauso ausrichten wie später der Kometen-Stack (`_stack`), sonst enthielte die
        # Bahn Registrierungsreste, die im korrigierten Stack schon fehlen (Kerne gegeneinander versetzt)
        dense_ref = self._dense_ref(A, lib, cache) if registration.distortion_mode(opt.get("distortion")) == "auto" else None
        C0, H0, W0 = A.meta["shape"]

        def load(r):
            m = json.loads(r["metrics"])
            i = json.loads(r["info"])
            tg = np.array([[s[0], s[1]] for s in m["stars"]])
            tf = np.array([s[2] for s in m["stars"]])
            if r["id"] == A.meta["ref_fid"]:
                M, pairs = np.eye(3), np.stack([np.arange(len(tg))] * 2, 1)
            else:
                res = registration.find_transform(ref_xy, tg)
                if res is None:
                    return None
                M, pairs, _ = res
            raw, _ = self._load(r["path"], i, lib, r["night"], cache)
            P = None
            if dense_ref is not None and r["id"] != A.meta["ref_fid"]:
                try:
                    tgd = np.array([[s[0], s[1]] for s in quality.dense_stars(raw, bool(i["bayerpat"]) and raw.ndim == 2)])
                    P, _ = registration.fit_distortion(dense_ref, tgd, M, (H0, W0))
                except Exception:
                    P = None
            Lb = comet.bin_image(to_channels(raw, i["bayerpat"]).mean(0), b)
            w, valid = registration.warp(Lb[None], comet.binned_transform(M, b), LAb.shape, 1, order=1,
                                         poly=registration.binned_poly(P, b) if P is not None else None)
            k = float(np.clip(np.median(ref_flux[pairs[:, 1]] / np.maximum(tf[pairs[:, 0]], 1e-6)) / k0, 0.4, 2.5))
            a = w[0] * k
            a -= float(np.median(a[valid]))
            a[~valid] = np.nan
            return times[r["id"]], a

        samples = []
        if pick:
            with ThreadPoolExecutor(max(1, self.s.threads)) as ex:
                for n, s_ in enumerate(ex.map(load, pick)):
                    if s_ is not None:
                        samples.append(s_)
                    progress(0.02 + 0.10 * (n + 1) / len(pick), "Kometenbahn", f"Kometenkern suchen ({n + 1}/{len(pick)})")
        lab = LAb - float(np.median(LAb))
        if len(samples) >= 4:
            res = comet.locate(samples, lab, b, fwhm, prior=comet.unscale_track(prior, b) if prior else None)
        else:
            res = {"track": comet.unscale_track(prior, b) if prior else None, "refined": False,
                   "error": "zu wenige Einzelbilder für die Kernsuche"}
        if res.get("track") is None:
            raise ValueError(res.get("error") or "Kometenbahn nicht bestimmbar")
        track = comet.scale_track(res["track"], b)
        src = ("horizons+bild" if res.get("refined") else "horizons") if prior else "bild"
        track.source = src
        info.update(source=src, samples=len(samples), points=len(res.get("points") or []),
                    rms_px=round(res["rms"] * b, 2) if res.get("rms") is not None else None,
                    offset_px=round(res["offset_px"] * b, 1) if res.get("offset_px") is not None else None,
                    rate_px_s=round(track.rate(), 4), fwhm_px=round(fwhm, 2), sharpness=res.get("sharpness"),
                    coma_px=round(res["coma_px"] * b, 1) if res.get("coma_px") else None)
        ex_t = [float(json.loads(r["info"]).get("exptime") or 0) for r in rows]
        info["exptime_s"] = float(np.median(ex_t)) if ex_t else None
        info["per_exposure_px"] = round(track.rate() * (info["exptime_s"] or 0), 2)
        return track, info

    def _stack_wcs(self, proj, A, mean, log) -> dict:
        """WCS des Sterne-Stacks (gecacht je Referenzbild in work/p<id>/wcs.json, wie die Farbkalibrierung)."""
        if not self.solver_cfg.enabled:
            raise ValueError("kein Plate-Solver eingerichtet")
        wd = self.s.work_dir / f"p{proj['id']}"
        key = f"{A.meta.get('ref_fid')}|{mean.shape[1]}x{mean.shape[2]}"
        f = wd / "wcs.json"
        cached = json.loads(f.read_text()) if f.exists() else {}
        if cached.get("key") == key:
            return cached["wcs"]
        log("Stack plate-solven")
        hdr = platesolve.solve_image(self.solver_cfg, mean.mean(0), wd / "solve", proj["ra"], proj["dec"])
        f.write_text(json.dumps({"key": key, "wcs": hdr}))
        return hdr

    def comet_info(self, project_id: int) -> dict | None:
        f = self.s.work_dir / f"p{project_id}" / "comet.json"
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            return None

    def distortion_state(self, pid: int) -> bool | None:
        """Wurde der (Sterne-)Stack mit Verzerrungskorrektur aufgebaut? None = noch kein Stack. Ältere Stacks ohne Marke:
        mit Einträgen je Aufnahme (`reg`) gemischt/an, sonst aus. Je Stand von state.json gecacht (Ansicht wird gepollt)."""
        wd = self.s.work_dir / f"p{pid}"
        f = wd / "stack" / "state.json"
        if not f.exists():
            f = next((x / "state.json" for x in sorted(wd.glob("stack_*")) if x.name != "stack_comet"
                      and not x.name.startswith("stack_comet_") and (x / "state.json").exists()), f)
        try:
            mt = f.stat().st_mtime
        except OSError:
            return None
        hit = self._hint_cache.get(("dist", str(f)))
        if hit and hit[0] == mt:
            return hit[1]
        try:
            meta = json.loads(f.read_text(encoding="utf-8"))
            val = bool(meta.get("distortion", bool(meta.get("reg"))))
        except (OSError, ValueError):
            val = None
        self._hint_cache[("dist", str(f))] = (mt, val)
        return val

    def _master(self, project_id: int) -> dict:
        """Gespeicherte Master-Stacks je Filter (None = Farbkamera)."""
        wd = self.s.work_dir / f"p{project_id}"
        bands = {band_of_row(r) for r in self.db.q("SELECT * FROM frames WHERE project_id=? AND stacked=1", (project_id,))}
        states = {b: StackState.load(stack_dir(wd, b)) for b in bands}
        return {b: st for b, st in states.items() if st is not None}

    def _photometric(self, proj: dict, states: dict, mean: np.ndarray, cov: np.ndarray, log) -> dict | None:
        """Weißabgleich über Gaia-Sternfarben (PCC). Stack-WCS wird je Referenzbild gecacht. None -> Sternfarben-Mittel."""
        if not self.s.gaia:
            return None
        if not self.solver_cfg.enabled:
            return {"fallback": "kein Plate-Solver eingerichtet"}
        try:
            hdr = self._stack_wcs(proj, next(iter(states.values())), mean, log)
            w = photometry.wcs_from(hdr)
            H, W = mean.shape[1:]
            ra, dec = (float(v) for v in w.pixel_to_world_values((W - 1) / 2, (H - 1) / 2))
            scale = float(np.sqrt(abs(np.linalg.det(w.pixel_scale_matrix))))          # Grad/Pixel
            log("Gaia-Sternfarben abfragen")
            cat = photometry.gaia_stars(ra, dec, 0.5 * scale * math.hypot(W, H), self.s.work_dir / "gaia")
            st = next(iter(states.values())).meta
            gains, info = photometry.pcc_gains(mean, cov, hdr, cat, float(st.get("ref_fwhm") or 3.0))
            return {"gains": gains, **info}
        except Exception as e:                    # Netz weg, Solver scheitert, zu wenige Sterne -> bisheriges Verfahren
            return {"fallback": str(e)[:200]}

    # ======================================================== Develop Engine #
    def develop(self, project_id: int, extra: dict | None = None, progress=lambda p, stage, msg="": None,
                start: float = 0.86) -> int:
        """Master-Stack -> fertiges Astrofoto. Braucht keine Rohdaten und kein Stacking."""
        run, progress = self._track_live(project_id, progress)
        view = (lambda img, linear=True: live.optional(run.picture, img, linear=linear)) if run else None
        proj = self.db.one("SELECT * FROM projects WHERE id=?", (project_id,))
        if not proj:
            raise ValueError("Projekt nicht gefunden")
        opt = self._options(proj, extra)
        states = self._master(project_id)
        if not states:
            raise ValueError("Noch kein Master-Stack vorhanden – bitte zuerst verarbeiten.")
        prog = lambda f, stage, msg="": progress(start + (1 - start) * f, stage, msg)
        prog(0.0, "Bearbeitung")
        stacked = self.db.q("SELECT id,info,night FROM frames WHERE project_id=? AND stacked=1", (project_id,))
        exposure = sum((json.loads(r["info"]).get("exptime") or 0) for r in stacked)
        band_stats: dict = {}
        for r in stacked:
            i = json.loads(r["info"])
            bs = band_stats.setdefault(channels.band_of(i.get("filter"), channels.is_color(i)) or "RGB", {"frames": 0, "exposure": 0.0})
            bs["frames"] += 1
            bs["exposure"] += i.get("exptime") or 0
        mono_bands = {b: st for b, st in states.items() if b is not None}
        if mono_bands and (None not in states or len(mono_bands) > 1):
            mean, cov, palette, nb = channels.combine({b: st.result() for b, st in mono_bands.items()}, opt.get("palette") or "auto")
            narrow = opt["narrowband"] if opt["narrowband"] is not None else nb
        else:
            mean, cov = states[None].result()
            palette = "OSC"
            filt = (json.loads(stacked[0]["info"]).get("filter") or "") if stacked else ""
            narrow = opt["narrowband"] if opt["narrowband"] is not None else bool(re.search(r"ha|oiii|duo|dual|nb|lp|lenhance|l-extreme|uv/ir", filt, re.I))
        hotfix = None
        if palette == "OSC" and mean.shape[0] == 3 and None in states and not states[None].meta.get("defects"):
            # Stack von vor der Fehlerkarte: Hotpixel-Punkte direkt im linearen Stack entfernen (Rohbilder oft gelöscht)
            prog(0.01, "Bearbeitung", "Hotpixel-Punkte im Stack suchen")
            mean, hotfix = stackfix.repair(mean, cov)
        kind = proj["kind"]
        if not kind and (ext := objects.resolve_extended(proj["name"])) and ext.kind != "generic":
            kind = ext.kind                                 # ältere Projekte: Typ nachträglich aus dem großen Katalog
            self.db.x("UPDATE projects SET kind=? WHERE id=?", (kind, project_id))
        is_comet = comet.active(proj, opt)
        pname = opt["profile"] if opt["profile"] != "auto" else ("comet" if is_comet else (kind or "generic"))
        user = opt.get("params") or {}
        params = {**objects.profile_for(pname), **user}
        if opt.get("star_separation"):
            params["star_separation"] = opt["star_separation"]
        if params.get("star_separation") == "starnet" and not stars.starnet_path():
            params["star_separation"] = "off"           # Profil „nur StarNet“: ohne StarNet keine eigene Trennung
        gx: dict = {}
        if run:
            run.data.update(n=len(stacked), exposure=exposure, band=None)
        cinfo = self.comet_info(project_id) if is_comet else None
        singles: dict = {}
        if cinfo is not None:
            mean, cov, singles = self._comet_compose(project_id, states, mean, cov, cinfo, opt,
                                                     lambda m: prog(0.02, "Kometenmodus", m))
        if view:
            view(mean)
        gmode = graxpert.mode_for(opt.get("graxpert"))
        if is_comet:                                    # KI-Hintergrund könnte den schwachen Schweif abtragen
            gmode = {"bge": "off", "all": "denoise"}.get(gmode, gmode)
        if gmode != "off" and (exe := graxpert.graxpert_path()):
            # einmal je Entwicklung auf dem linearen Stack (gecacht) – vor der Farbkalibrierung, damit die
            # Sternfotometrie auf verlaufsfreiem Himmel misst
            mean, gx = graxpert.apply(mean, cov, gmode, float(params.get("ai_denoise", graxpert.DEFAULT_STRENGTH)),
                                      self.s.work_dir / f"p{project_id}" / "graxpert", exe,
                                      log=lambda m: prog(0.05, "Bearbeitung", m), preview=view,
                                      tag=f"hotfix:{hotfix['key']}" if hotfix and hotfix.get("n") else "")
            params["_ai"] = {k: v for k, v in gx.items() if k in ("bge", "denoise")}
        if mean.shape[0] == 3 and not narrow and params.get("color_calibration", True) and "wb_gains" not in user:
            pcc = self._photometric(proj, states, mean, cov, lambda m: prog(0.1, "Farbkalibrierung", m))
            if pcc:
                params["_pcc"] = pcc
                if "gains" in pcc:
                    params["wb_gains"] = pcc["gains"]
                elif pcc.get('fallback'):
                    prog(0.15, 'Farbkalibrierung', 'Sternfarben-Mittel – ' + pcc['fallback'])
        cnt = self.db.one("SELECT COUNT(*) n, SUM(status='rejected') r FROM frames WHERE project_id=? AND kind='light'", (project_id,))
        ctx = {"exposure_s": exposure, "rejected_pct": 100 * (cnt["r"] or 0) / max(1, cnt["n"]),
               "has_flats": bool(self.db.one("SELECT id FROM frames WHERE kind='flat' LIMIT 1")),
               "neutral_sky": tutor.neutral_sky(proj["ra"], proj["dec"], kind)}
        rgb, stats, params, _ = develop.develop(
            mean, cov, params, narrow, palette, ctx, fixed=user,
            log=lambda m: prog(0.3, "Bearbeitung", m), render_log=lambda m: prog(0.5, "Bearbeitung", m), on_preview=view)

        review_hist = []
        if opt.get("review", True) and self.review_cfg.enabled:
            render = lambda p: processing.process(mean, cov, p, narrowband=narrow, palette=palette)
            quality, before, qc_hist = stats["quality"], stats.get("before"), stats.get("qc")
            rgb, stats, params, review_hist = reviewer.review_loop(
                self.review_cfg, render, params, proj["name"], kind, log=lambda m: prog(0.7, "KI-Prüfung", m),
                first=(rgb, stats))
            if "quality" not in stats:                     # KI hat nachgebessert -> neu bewerten
                stats["quality"] = {**develop.assess(rgb, mono=mean.shape[0] == 1, neutral_sky=ctx["neutral_sky"]),
                                    "hints": quality["hints"], "measure": quality["measure"]}
                stats["before"], stats["qc"] = before, qc_hist
        if gx:
            stats["graxpert"] = gx
        if hotfix is not None:
            stats["hotfix"] = hotfix
        tr = [st.meta.get("trails") for st in states.values() if st.meta.get("trails")]
        if tr:                                          # ausgeblendete Satellitenspuren (trails.py), alle Bänder zusammen
            stats["trails"] = {k: sum(t.get(k, 0) for t in tr) for k in ("frames", "lines", "pixels", "kept")}
        # Verzerrungskorrektur (nur Stacks, die mit ihr gerechnet wurden): alle Bänder zusammen
        dsum = registration.summary({"reg": {k: v for st in states.values() for k, v in (st.meta.get("reg") or {}).items()},
                                     "frames": {k: 1 for st in states.values() for k in st.meta.get("frames", {})}})
        if dsum:
            stats["distortion"] = dsum
        if cinfo is not None:
            stats["comet"] = cinfo
        stats.update({"profile": pname, "narrowband": narrow, "frames": len(stacked), "exposure_s": exposure,
                      "palette": palette, "bands": band_stats,
                      "nights": sorted({r["night"] for r in stacked if r["night"]})})   # Bild-Tutor: nur Nächte dieses Ergebnisses

        # --- Ausgabe ----------------------------------------------------------
        prog(0.85, "Speichern")
        if run:
            live.optional(run.picture, rgb, linear=False, force=True)
        rid = self._save(proj, rgb, mean, cov, stats, review_hist, len(stacked), exposure, params)
        if singles:
            try:
                self._save_singles(rid, singles, params, stats, narrow, palette, lambda m: prog(0.86, "Speichern", m))
            except Exception as e:                     # Einzelstacks sind Beiwerk – das fertige Bild bleibt
                print(f"Kometen-Einzelstacks fehlgeschlagen: {e}")
        # Live-JPGs nur übernehmen – keine erneute Entwicklung der Stacking-Stufen.
        try:
            self._render_stages(project_id, rid, states, params, narrow, palette, stats.get("crop"), len(stacked),
                                lambda f, m: prog(0.88 + 0.12 * f, "Stacking-Stufen", m))
        except Exception as e:                         # Stufen sind Beiwerk – das fertige Bild bleibt
            print(f"Stacking-Stufen fehlgeschlagen: {e}")
        if start < 0.5:
            progress(1.0, "Fertig")
        if run:
            live.optional(run.event, 'Fertig')
        return rid

    def _comet_compose(self, project_id, states, mean, cov, cinfo, opt, log):
        """Sterne-Stack (mean) + Kometen-Stack -> linear zusammengesetztes Bild. Gibt (Bild, Abdeckung, Einzelstacks)."""
        if len(states) != 1 or not cinfo.get("nucleus"):
            return mean, cov, {}
        (band, A), = states.items()
        cst = StackState.load(comet_dir(self.s.work_dir / f"p{project_id}", band))
        if cst is None or cst.meta.get("ref_fid") != A.meta.get("ref_fid") or not cst.meta.get("frames"):
            return mean, cov, {}
        log("Sterne- und Kometen-Stack zusammensetzen")
        B, covB = cst.result()
        if band is not None:
            # Mono mit einem Filter: `mean` ist schon über channels.combine normiert, der Kometen-Stack noch in ADU –
            # deshalb roh zusammensetzen und danach jedes Bild gleich normieren
            mean, cov = A.result()
        sc = int(A.meta.get("scale") or 1)
        fw = float(A.meta.get("ref_fwhm") or 3.0) * sc
        pts = np.array(cinfo.get("track") or [cinfo["nucleus"]], float)
        motion = float(cinfo.get("motion_px") or 0) * sc
        method = opt.get("star_separation") if opt.get("star_separation") in ("starnet", "intern", "auto") else "auto"
        out, ccov, meta = comet.compose(mean, cov, B, covB, fw, tuple(cinfo["nucleus"]), pts,
                                        lane_px=0.5 * motion + 2 * fw if motion < comet.COMPACT_FWHM * fw else 0.0,
                                        core_px=max(6 * fw, 20.0 * sc), method=method, log=log)
        cinfo.update(composite=True, frames_comet=len(cst.meta["frames"]), **meta)
        singles = {"stars": (mean, cov), "comet": (B, covB)}
        if band is not None:
            norm = lambda m, c: channels.combine({band: (m, c)}, "mono")[:2]
            out, ccov = norm(out, ccov)
            singles = {k: norm(*v) for k, v in singles.items()}
        return out, ccov, singles

    def _save_singles(self, rid, singles, params, stats, narrow, palette, log) -> None:
        """Sterne- und Kometen-Stack mit denselben Parametern und Ausschnitt wie das Ergebnis entwickeln (Umschalter)."""
        row = self.db.one("SELECT files FROM results WHERE id=?", (rid,))
        files = json.loads(row["files"])
        base = files["jpg"][: -len(".jpg")]
        sp = {k: v for k, v in params.items() if k != "_ai"}           # GraXpert lief nur auf dem Ergebnis
        if stats.get("wb_gains") and "wb_gains" not in sp:
            sp["wb_gains"] = stats["wb_gains"]                          # Kometen-Stack hat keine Sterne zum Kalibrieren
        names = {"stars": "sterne", "comet": "komet"}
        for k, (m, c) in singles.items():
            log(f"{'Sterne' if k == 'stars' else 'Kometen'}-Stack entwickeln")
            img, _ = processing.process(m, c, sp, narrowband=narrow, palette=palette, crop=stats.get("crop"))
            disp = np.moveaxis(img, 0, -1)[::-1]                        # Norden oben wie das Ergebnis
            f = f"{base}_{names[k]}.jpg"
            Image.fromarray((np.clip(disp, 0, 1) * 255 + 0.5).astype(np.uint8)).save(f, quality=90)
            files[k] = f
        m, _ = singles["comet"]
        write_fits(Path(f"{base}_komet_linear.fits"), m, {"NFRAMES": 0, "COMMENT": "AstroFlow Kometen-Stack (auf den Kern ausgerichtet)"})
        files["comet_fits"] = f"{base}_komet_linear.fits"
        self.db.x("UPDATE results SET files=? WHERE id=?", (json.dumps(files), rid))

    def _render_stages(self, project_id, rid, states, params, narrow, palette, crop, n_total, log) -> None:
        """Live-Vorschauen archivieren; Altbestand nur verkleinert und einfach strecken."""
        if len(states) != 1:
            return
        (band, _), = states.items()
        found = [x for x in load_stages(stage_dir(self.s.work_dir / f"p{project_id}", band)) if x["n"] < n_total]
        if not found:
            return
        row = self.db.one("SELECT files, stats FROM results WHERE id=?", (rid,))
        files, st = json.loads(row["files"]), json.loads(row["stats"])
        base = files["jpg"][: -len(".jpg")]
        done = []
        for i, x in enumerate(found):
            log(i / len(found), f"Live-Verlauf sichern: {x['n']} Aufnahmen")
            f = f"{base}_s{x['n']}.jpg"
            if x['path'].suffix == '.jpg':
                shutil.copyfile(x['path'], f)
            else:
                with np.load(x['path']) as z:
                    mean = live.sample(z['mean']) * STAGE_SCALE
                live.save_picture(Path(f), mean)
            files[f"s{x['n']}"] = f
            done.append({"n": x["n"], "exposure": x["exposure"]})
        st["stages"] = done
        st['stages_live'] = True
        st.pop('stages_simplified', None)
        self.db.x("UPDATE results SET files=?, stats=? WHERE id=?", (json.dumps(files), json.dumps(st), rid))

    # -------------------------------------------------------------- stacking #
    def _dense_ref(self, state: StackState, lib, cache) -> np.ndarray:
        """Dichte Sternliste des Referenzbilds für die Verzerrungskorrektur (state.json → ref_dense). Ältere Stacks
        haben keine: dann aus dem Referenzbild nachmessen, ohne Rohbild aus dem Stack selbst (gleiches Raster)."""
        meta = state.meta
        if not meta.get("ref_dense"):
            r = self.db.one("SELECT path, info, night, deleted FROM frames WHERE id=?", (meta["ref_fid"],))
            dense = None
            if r and not r["deleted"] and Path(r["path"]).exists():
                try:
                    info = json.loads(r["info"])
                    raw, _ = self._load(r["path"], info, lib, r["night"], cache)
                    dense = quality.dense_stars(raw, bool(info["bayerpat"]) and raw.ndim == 2)
                except Exception:
                    dense = None
            if not dense and meta.get("frames") and "comet" not in meta:     # Kometen-Stack ist auf den Kern verschoben
                mean, cov = state.result()
                sc = int(meta.get("scale") or 1)
                lum = mean.mean(0)
                ok = cov > 0.5
                lum = np.where(ok, lum, np.median(lum[ok]) if ok.any() else 0.0).astype(np.float32)
                st = quality.detect_stars(lum, max_stars=quality.DENSE_STARS)
                dense = [[round(float((x + 0.5) / sc - 0.5), 2), round(float((y + 0.5) / sc - 0.5), 2), round(float(f), 1)]
                         for x, y, f in zip(st.x, st.y, st.flux)]
            meta["ref_dense"] = dense or meta["ref_stars"]
        return np.array([[s[0], s[1]] for s in meta["ref_dense"]], float).reshape(-1, 2)

    def _stack(self, pid, todo, state, lib, cache, scale, progress, ref_meta: dict | None = None,
               save_dir: Path | None = None, stages: Path | None = None, shifts: dict | None = None,
               mask: np.ndarray | None = None, mark: bool = True, unmasked: np.ndarray | None = None,
               distortion: bool = False) -> StackState:
        """shifts: je Frame zusätzliche Verschiebung (Referenzpixel) nach der Sternregistrierung – Kometen-Stack;
        mask: Pixel im Sterne-Raster, die nicht eingehen (Sterne), wird mitverschoben; mark=False: frames.stacked nicht setzen
        (der Kometen-Stack führt seine Aufnahmen nur in state.json).
        distortion: je Aufnahme zusätzlich ein Polynom 2. Ordnung anpassen (registration.fit_distortion) und, wenn es
        trägt, damit statt mit der Ähnlichkeit warpen. Ergebnis je Aufnahme in meta["reg"] (Restfehler, Paare, Grund)."""
        if state is None and ref_meta is not None:
            # weiterer Filter: auf die Referenz des ersten Kanals registrieren, Fluss relativ zum eigenen ersten Batch
            i0 = json.loads(todo[0]["info"])
            C = 3 if channels.is_color(i0) else 1
            _, H, W = ref_meta["shape"]
            state = StackState((C, H * scale, W * scale))
            state.meta.update({k: ref_meta[k] for k in ("ref_fid", "ref_stars", "scale", "ref_fwhm", "ref_dense") if k in ref_meta},
                              shape=[C, H, W], flux_k0=None)
        if state is None:
            ref = next((r for r in todo if len(json.loads(r["metrics"]).get("stars", [])) >= 15), None)
            if ref is None:
                raise ValueError("kein Referenzbild mit genug Sternen")
            ri = json.loads(ref["info"])
            raw, _ = self._load(ref["path"], ri, lib, ref["night"], cache)
            rgb0 = to_channels(raw, ri["bayerpat"])
            C, H, W = rgb0.shape
            state = StackState((C, H * scale, W * scale))
            rm = json.loads(ref["metrics"])
            state.meta.update(ref_fid=ref["id"], ref_stars=rm["stars"], scale=scale, shape=[C, H, W],
                              ref_bg=[float(np.median(rgb0[c])) for c in range(C)], ref_noise=rm["noise"],
                              ref_fwhm=rm["fwhm"])
            if distortion:
                state.meta["ref_dense"] = quality.dense_stars(raw, bool(ri["bayerpat"]) and raw.ndim == 2)
        meta = state.meta
        if not meta.get("frames"):
            meta["distortion"] = bool(distortion)       # mit welchem Modus der Stack aufgebaut wurde (Hinweis „Neu stacken“)
        if not meta.get("frames") and "defects" not in meta:
            # neu aufgebauter Stack: mit Fehlerkarte gerechnet? (ältere Stacks haben die Marke nicht → Hinweis „Neu stacken“)
            meta["defects"] = any(isinstance(k, tuple) and k[0] == "defects" for k in cache)
        if "exposure" not in meta:                      # ältere Zustände: Belichtung der schon gestackten Frames nachtragen
            ids = list(meta["frames"])
            meta["exposure"] = sum((json.loads(r["info"]).get("exptime") or 0) for i in range(0, len(ids), 500)
                                   for r in self.db.q(f"SELECT info FROM frames WHERE id IN ({','.join('?' * len(ids[i:i + 500]))})",
                                                      tuple(ids[i:i + 500])))
        exptime = {r["id"]: json.loads(r["info"]).get("exptime") or 0 for r in todo}
        dense_ref = self._dense_ref(state, lib, cache) if distortion else None
        reg = meta.setdefault("reg", {}) if distortion else None
        ref_xy = np.array([[s[0], s[1]] for s in meta["ref_stars"]])
        ref_flux = np.array([s[2] for s in meta["ref_stars"]])
        C, H, W = meta["shape"]
        # RAM-Budget je Stapel: Einstellung, aber höchstens 1/5 des gerade freien Speichers (doppelte Pufferung +
        # Clipping-Zwischenwerte; andere Programme wie ein geladenes LLM verkleinern den Spielraum)
        free = free_ram_mb()
        budget = min(self.s.batch_mb, max(300, free // 5)) if free else self.s.batch_mb
        bsz = batch_size_for((C, H * scale, W * scale), budget)
        g = gpu.engine()                               # Grafikkarte für Ausrichten + Clipping (sonst None = CPU)

        def work(r):
            try:
                m = json.loads(r["metrics"])
                info = json.loads(r["info"])
                tg = np.array([[s[0], s[1]] for s in m["stars"]])
                tf = np.array([s[2] for s in m["stars"]])
                if r["id"] == meta["ref_fid"]:
                    M, pairs = np.eye(3), np.stack([np.arange(len(tg)), np.arange(len(tg))], 1)
                else:
                    res = registration.find_transform(ref_xy, tg)
                    if res is None:
                        return r["id"], None, "Registrierung fehlgeschlagen"
                    M, pairs, _ = res
                d = shifts.get(r["id"]) if shifts else None
                M0 = M                                   # Sternregistrierung ohne Kometenverschiebung
                if d is not None:
                    M = np.array([[1, 0, d[0]], [0, 1, d[1]], [0, 0, 1]], float) @ M
                raw, _ = self._load(r["path"], info, lib, r["night"], cache)
                P, rinfo = None, None
                if dense_ref is not None and r["id"] != meta["ref_fid"]:
                    # Verzerrung: Polynom aus allen zugeordneten Sternen (dichte Liste dieser Aufnahme), sonst Ähnlichkeit
                    try:
                        tgd = np.array([[s[0], s[1]] for s in quality.dense_stars(raw, bool(info["bayerpat"]) and raw.ndim == 2)])
                        P, rinfo = registration.fit_distortion(dense_ref, tgd, M0, (H, W))
                    except Exception as e:               # nie die Aufnahme verlieren – dann eben ohne Polynom
                        P, rinfo = None, {"n": 0, "why": f"Fehler: {e}"[:120]}
                    rinfo["poly"] = P is not None
                    if P is not None and d is not None:
                        P = registration.shift_poly(P, d)
                img = to_channels(raw, info["bayerpat"])
                warped, valid = registration.warp(img, M, (H * scale, W * scale), scale, gpu=g, poly=P)
                if mask is not None and d is not None:
                    sm = comet.shift_mask(mask, d[0] * scale, d[1] * scale)
                    valid &= ~(sm & ~unmasked) if unmasked is not None else ~sm
                sub = valid.ravel()[::7]
                bg = np.array([np.median(warped[c].ravel()[::7][sub]) for c in range(C)])
                ratio = ref_flux[pairs[:, 1]] / np.maximum(tf[pairs[:, 0]], 1e-6)
                fw = max(m["fwhm"] or meta["ref_fwhm"], 1.0)
                lines = m.get("trail_lines")
                if lines is None:                       # vor 0.49 analysiert: Spuren im ohnehin geladenen Bild suchen
                    luma, s_an = quality.analysis_image(raw, bool(info.get("bayerpat")) and raw.ndim == 2)
                    lines = trails.to_full(trails.find(luma), s_an)
                # Spuren: Lage im Ausgaberaster über M; mit Polynom zusätzlich Aufnahme-Linien + Polynom (Maske wie der Warp)
                tl = {"ref": trails.transform(lines, M, scale), "frame": lines, "P": P} if lines else None
                return r["id"], ((warped - bg[:, None, None]).astype(np.float32), valid, float(np.median(ratio)), m["noise"], fw,
                                 rinfo, tl), None
            except Exception as e:
                return r["id"], None, f"Fehler: {e}"

        # Laden/Registrieren/Warpen parallel (scipy.ndimage gibt den GIL frei); der nächste Stapel wird schon
        # vorbereitet, während der aktuelle verrechnet wird (doppelte Pufferung, daher 2 Stapel im RAM)
        ex = ThreadPoolExecutor(max(1, self.s.threads))
        total = len(todo)
        done = 0
        chunks = [todo[i : i + bsz] for i in range(0, total, bsz)]
        unsaved: list = []
        last_save = time.time()
        pending = [ex.submit(work, r) for r in chunks[0]] if chunks else []
        for ci, chunk in enumerate(chunks):
            results = [f.result() for f in pending]
            pending = [ex.submit(work, r) for r in chunks[ci + 1]] if ci + 1 < len(chunks) else []
            good = [(fid, d) for fid, d, e in results if d is not None]
            for fid, d, e in results:
                if d is None and mark:                   # Kometen-Stack: Auswahl der Aufnahmen nicht verändern
                    self.db.set_frame(fid, status="rejected", reason=e)
            if good:
                X = np.stack([d[0] for _, d in good])
                V = np.stack([d[1] for _, d in good])
                kr = np.array([d[2] for _, d in good])
                if meta.get("flux_k0", 1.0) is None:
                    meta["flux_k0"] = float(np.median(kr))
                k = np.clip(kr / meta.get("flux_k0", 1.0), 0.4, 2.5)
                X *= k[:, None, None, None].astype(np.float32)
                noise = np.maximum(np.array([d[3] for _, d in good]) * k, 1e-6)
                fw = np.array([d[4] for _, d in good])
                ws = (1 / noise**2 * (meta["ref_fwhm"] / fw) ** 2).astype(np.float32)
                ref_w = ws[0] if "ref_w" not in meta else meta["ref_w"]
                meta.setdefault("ref_w", float(ref_w))
                ws = np.clip(ws / meta["ref_w"], 0.1, 3.0)
                masked = self._trail_masks(state, X, V, [d[6] for _, d in good], fw, scale)
                n0 = len(meta["frames"])
                cut = {t: t - n0 for t in STAGES if n0 < t <= n0 + len(good)} if stages is not None else {}
                run = self.live_runs.get(pid) if mark else None    # „Bild entsteht aus dem Rauschen“ (nicht im Kometen-Stack)
                band = stages.name.removeprefix('stufen_') if stages and stages.name != 'stufen' else None
                inner = [t for t in live.stack_targets(n0, n0 + len(good) - 1) if t <= live.EARLY] if run else []
                inp = live.optional(live.mini_inputs, state, X, V, ws) if inner else None
                if inp:                                  # frühe Zwischenstände mitten im Stapel: Mini-Stack im Hintergrund
                    exps = [meta["exposure"] + sum(exptime.get(fid, 0) for fid, _ in good[:t - n0]) for t in inner]
                    def early_frames(inp=inp, inner=inner, n0=n0, exps=exps, band=band):
                        snaps = live.mini_snapshots(inp, [t - n0 for t in inner], workers=2)
                        for t, e in zip(inner, exps):
                            m, c = snaps[t - n0]
                            run.stack_frame(m, n=t, exposure=e, band=band, cov=c)
                    run.later(early_frames)
                state.add_batch(X, V, ws, workers=self.s.threads, prefixes=cut.values(), gpu=g)
                for b, mb in masked.items():             # ausgeblendete Spur zählt für die Abdeckung weiter
                    state.swm += ws[b] * mb
                del X, V, masked
                exp0 = meta["exposure"]
                for (fid, d), w in zip(good, ws):
                    if reg is not None and d[5] is not None:
                        reg[fid] = d[5]
                    meta["frames"][fid] = float(w)
                    meta["exposure"] += exptime.get(fid, 0)
                    unsaved.append(fid)
                for t, k in cut.items():                 # Zwischenstand nach t Aufnahmen für den Verlaufsregler
                    m, c = state.snaps.pop(k)
                    def save_stage():
                        stages.mkdir(parents=True, exist_ok=True)
                        live.save_picture(stages / f'n{t:05d}.jpg', m)
                        (stages / f'n{t:05d}.json').write_text(json.dumps(
                            {'n': t, 'exposure': exp0 + sum(exptime.get(fid, 0) for fid, _ in good[:k])}), encoding='utf-8')
                    live.optional(save_stage)
                    del m, c
                n1 = len(meta['frames'])
                if run and (live.stack_targets(n0 + len(good) - 1, n1) or time.monotonic() - run.last_picture >= live.INTERVAL
                            or ci == len(chunks) - 1):
                    def show_stack():                    # Stand am Stapelende: direkt aus dem Stack, Rendern im Hintergrund
                        mean, cov, exp = live.stack_sample(state), live.stack_cov(state), meta['exposure']
                        # Werte jetzt binden – bis der Hintergrund-Thread dran ist, kann schon der nächste Stapel fertig sein
                        run.later(lambda mean=mean, cov=cov, exp=exp, n1=n1, band=band:
                                  run.stack_frame(mean, n=n1, exposure=exp, band=band, cov=cov))
                        run.last_picture = time.monotonic()
                    live.optional(show_stack)
            done += len(chunk)
            # Checkpoint: Frames gelten erst als gestackt, wenn der Zustand mit ihnen gespeichert ist
            if save_dir is not None and unsaved and (ci == len(chunks) - 1 or time.time() - last_save > CHECKPOINT_S):
                state.save(save_dir)
                if mark:
                    self.db.many("UPDATE frames SET stacked=1 WHERE id=?", [(f,) for f in unsaved])
                unsaved, last_save = [], time.time()
            progress(0.36 + 0.48 * done / max(1, total), "Stacking",
                     f"{done}/{total} Aufnahmen" + (f" · Grafikkarte {g.name}" if g and not g.broken else ""))
        ex.shutdown()
        if g:
            g.free()
        return state

    @staticmethod
    def _trail_masks(state: StackState, X: np.ndarray, V: np.ndarray, lines: list, fw: np.ndarray, scale: int) -> dict:
        """Satellitenspuren aus V nehmen, wenn das Band in der Aufnahme heller ist als im bisherigen Stack bzw. im Median
        der übrigen Aufnahmen des Stapels (trails.confirm). `lines` je Aufnahme None oder {ref: Linien im Ausgaberaster,
        frame: Linien in der Aufnahme, P: Polynom der Verzerrungskorrektur oder None}. Ändert V; gibt {b: Maske}."""
        out: dict = {}
        if not any(lines):
            return out
        meta = state.meta
        info = meta.setdefault("trails", {"frames": 0, "lines": 0, "pixels": 0, "kept": 0})
        lum = X.mean(1)
        sw = state.sw.min(0)
        ref_stack = np.where(sw > 0, (state.swx / np.maximum(state.sw, 1e-9)).mean(0), np.nan) if (sw > 0).any() else None
        for b, tl in enumerate(lines):
            if not tl:
                continue
            ref = ref_stack
            if ref is None or not np.isfinite(ref).any():
                ref = None
                if len(X) >= 3:                     # Median der übrigen Aufnahmen (nur für Aufnahmen mit Spur)
                    others = np.delete(np.arange(len(X)), b)
                    with np.errstate(all="ignore"), warnings.catch_warnings():
                        warnings.simplefilter("ignore", RuntimeWarning)
                        ref = np.nanmedian(np.where(V[others], lum[others], np.nan), 0)
            noise = None
            if ref is not None:
                dv = (lum[b] - ref)[V[b] & np.isfinite(ref)][::11]
                noise = float(1.4826 * np.median(np.abs(dv - np.median(dv)))) if dv.size > 100 else None
                if noise is None:
                    ref = None
            P = tl["P"]
            if P is None:                           # Ähnlichkeit: Linien im Ausgaberaster, Saum in Ausgabepixeln
                use, kw, pad = tl["ref"], {}, (float(fw[b]) + 1.0) * scale
            else:                                   # Polynom: in Aufnahme-Pixeln, über dasselbe Polynom gerastert
                use, kw, pad = tl["frame"], {"poly": P, "scale": scale}, float(fw[b]) + 1.0
            ok = [i for i, l in enumerate(use)
                  if trails.confirm(lum[b], ref, V[b], l, noise or 1.0, ref_line=tl["ref"][i], **kw)]
            info["kept"] += len(use) - len(ok)
            if not ok:
                continue
            m = trails.band(V.shape[1:], [use[i] for i in ok], pad=pad, ref_lines=[tl["ref"][i] for i in ok], **kw) & V[b]
            V[b] &= ~m
            out[b] = m
            info["frames"] += 1
            info["lines"] += len(ok)
            info["pixels"] += int(m.sum())
        return out

    # ----------------------------------------------------------------- save #
    def _save(self, proj, rgb, mean, cov, stats, review, n, exposure, params) -> int:
        out = self.s.output_dir / slug(proj["name"])
        out.mkdir(parents=True, exist_ok=True)
        base = f"{datetime.now():%Y%m%d-%H%M%S}_{n}f"
        disp = np.moveaxis(rgb, 0, -1)[::-1]            # Astro-Orientierung (Norden oben)
        u16 = (np.clip(disp, 0, 1) * 65535 + 0.5).astype(np.uint16)
        files = {}
        try:
            import tifffile
            tifffile.imwrite(out / f"{base}.tif", u16, photometric="rgb")
            files["tif"] = str(out / f"{base}.tif")
        except Exception:
            pass
        Image.fromarray((disp * 255 + 0.5).astype(np.uint8)).save(out / f"{base}.png")
        files["png"] = str(out / f"{base}.png")
        prev = Image.fromarray((disp * 255 + 0.5).astype(np.uint8))
        prev.save(out / f"{base}.jpg", quality=92)
        files["jpg"] = str(out / f"{base}.jpg")
        before = stats.pop("before", None)
        if before is not None:
            Image.fromarray((np.moveaxis(before, 0, -1)[::-1] * 255 + 0.5).astype(np.uint8)).save(out / f"{base}_before.jpg", quality=88)
            files["before"] = str(out / f"{base}_before.jpg")
        prev.thumbnail((480, 480))
        prev.save(out / f"{base}_thumb.jpg", quality=85)
        files["thumb"] = str(out / f"{base}_thumb.jpg")
        write_fits(out / f"{base}_linear.fits", mean, {"OBJECT": proj["name"][:60], "NFRAMES": n, "EXPTOTAL": exposure})
        files["fits"] = str(out / f"{base}_linear.fits")
        stats["params"] = {k: (round(v, 3) if isinstance(v, float) else v) for k, v in params.items()}
        return self.db.x("INSERT INTO results(project_id,created,n_frames,exposure,files,stats,review) VALUES(?,?,?,?,?,?,?)",
                         (proj["id"], time.time(), n, exposure, json.dumps(files), json.dumps(stats), json.dumps(review)))
