"""Gesamtablauf: scannen -> kalibrieren -> analysieren -> auswählen -> stacken -> bearbeiten -> prüfen."""
from __future__ import annotations

import json
import math
import re
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import numpy as np
from PIL import Image

from . import calibration, channels, develop, gpu, graxpert, objects, photometry, platesolve, processing, quality, registration, reviewer, scanner, stars
from ...core.config import Settings, free_ram_mb
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
}
GLOW_SAMPLE = 30                # Frames je Nacht für die Glühmessung
CHECKPOINT_S = 120              # Stack-Zustand spätestens alle 2 min sichern (Wiederaufnahme nach Abbruch)
STAGES = (1, 3, 10, 30, 100, 300, 1000, 3000)   # Stacking-Stufen (Aufnahmen) für den Verlaufsregler im Bild-Tab
STAGE_SCALE = 8.0               # Stufen als float16 (Speicher); ADU / 8 bleibt sicher unter dem float16-Maximum


def band_of_row(r: dict) -> str | None:
    i = json.loads(r["info"])
    return channels.band_of(i.get("filter"), channels.is_color(i))


def stack_dir(wd: Path, band: str | None) -> Path:
    return wd / ("stack" if band is None else f"stack_{band}")


def stage_dir(wd: Path, band: str | None) -> Path:
    return wd / ("stufen" if band is None else f"stufen_{band}")


def load_stages(d: Path) -> list[dict]:
    """Gespeicherte Stacking-Stufen, aufsteigend: [{n, exposure, path}]."""
    out = []
    for f in sorted(d.glob("n*.json")):
        try:
            out.append({**json.loads(f.read_text()), "path": f.with_suffix(".npz")})
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


class Pipeline:
    def __init__(self, settings: Settings, db: DB, review_cfg: reviewer.ReviewerConfig | None = None,
                 solver_cfg: platesolve.SolverConfig | None = None):
        self.s = settings
        self.db = db
        self.review_cfg = review_cfg or reviewer.ReviewerConfig.from_env()
        self.solver_cfg = solver_cfg or platesolve.SolverConfig.from_env()
        self._scan_lock = threading.Lock()     # Watcher, API-Scan und Nach-Solving nie gleichzeitig
        self.on_stacked = None                 # fn(project_id, result_id): neue Aufnahmen gestackt, Rohbilder noch vorhanden

    # ------------------------------------------------------------------ scan #
    def scan(self) -> dict:
        with self._scan_lock:
            return self._scan()

    def _scan(self) -> dict:
        known = {r["id"] for r in self.db.q("SELECT id FROM frames")}
        frames, errors = scanner.scan_dir(self.s.input_dir, known, self.s.threads)
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
                pid = self._project_for(f.info, centers)
                new_by_proj[pid] = new_by_proj.get(pid, 0) + 1
            else:
                pid = None
            try:
                size = Path(f.path).stat().st_size                 # auch für den Namensabgleich der Hauptordner-Suche
            except OSError:
                size = 0
            self.db.x("INSERT OR IGNORE INTO frames(id,project_id,path,kind,night,info,size) VALUES(?,?,?,?,?,?,?)",
                      (f.id, pid, f.path, f.kind, f.night, json.dumps(f.info.to_dict()), size))
        return {"new_frames": len(frames), "new_by_project": new_by_proj, "errors": errors, "solved": solved,
                "duplicates": len(dups), "duplicates_by_project": dup_by_proj}

    def _is_upload(self, path: str) -> bool:
        try:
            Path(path).resolve().relative_to(self.s.uploads_dir.resolve())
            return True
        except ValueError:
            return False

    def delete_stacked_uploads(self, project_id: int) -> tuple[int, int]:
        """Hochgeladene Light-Aufnahmen löschen, deren Information im gespeicherten Stack steckt (gestackt)
        oder die verworfen wurden. Nur Dateien unter input/uploads – eingebundene Ordner (NAS) bleiben unberührt,
        Kalibrierbilder ebenso. Gibt (Anzahl, Bytes) zurück."""
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

    def _project_for(self, info: FrameInfo, centers: dict) -> int:
        a = scanner.assign_project(info, centers)
        row = self.db.one("SELECT id FROM projects WHERE key=?", (a.key,))
        if row:
            return row["id"]
        cat = objects.resolve_name(a.obj_id) if a.obj_id else None
        radius = max(0.5, (cat.size / 60 if cat else 0.5))
        self.db.x(
            "INSERT OR IGNORE INTO projects(key,name,obj_id,kind,ra,dec,radius,options,created) VALUES(?,?,?,?,?,?,?,?,?)",
            (a.key, a.name or a.key.split("|")[0], a.obj_id, a.kind, a.ra, a.dec, radius, "{}", time.time()))
        pid = self.db.one("SELECT id FROM projects WHERE key=?", (a.key,))["id"]
        if a.ra is not None:
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
            pid = self._project_for(info, centers)
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
        glow = cache.get(("glow", calibration.GlowLibrary.key(info), night))
        if glow and m.dark is None:                    # echte Darks enthalten das Glühen bereits
            raw = calibration.remove_glow(raw, bool(info["bayerpat"]) and raw.ndim == 2, glow)
        return raw, m

    def _glow(self, rows: list[dict], lib, cache: dict, progress) -> dict:
        """Glühmodelle je Kamera und Nacht in den Cache legen. Nächte mit genug vorhandenen Rohbildern werden gemessen und
        in die Glüh-Bibliothek der Kamera aufgenommen; sonst gilt der Mittelwert der bisher gemessenen Nächte."""
        glib = calibration.GlowLibrary(self.s.work_dir / "glow")
        groups: dict = {}
        for r in rows:
            info = json.loads(r["info"])
            groups.setdefault((glib.key(info), r["night"]), []).append((r, info))
        used = {}
        for (key, night), items in groups.items():
            have = [(r, i) for r, i in items if not r["deleted"] and Path(r["path"]).exists()]
            if night not in glib.nights(key, with_map=True) and len(have) >= calibration.GLOW_MIN_FRAMES:
                sample = have[:: max(1, len(have) // GLOW_SAMPLE)][:GLOW_SAMPLE]
                progress(0.02, "Analyse", f"Sensorglühen messen ({night}, {len(sample)} Aufnahmen)")

                def prof(ri):
                    r, i = ri
                    raw, m = self._load(r["path"], i, lib, night, cache)
                    return None if m.dark is not None else calibration.glow_profiles(raw, bool(i["bayerpat"]) and raw.ndim == 2)

                with ThreadPoolExecutor(self.s.threads) as ex:
                    profs = [p for p in ex.map(prof, sample) if p is not None]
                med = calibration.median_profiles(profs)
                if med:
                    glib.add(key, night, med)
            med, src = glib.medians(key, night)
            model = calibration.model_from(med)
            if model:
                cache[("glow", key, night)] = model
                used[night] = {"quelle": src, "adu": round(calibration.glow_amplitude(model), 1)}
        return used

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
        if opt.get("sensor_glow", True):
            glow = self._glow(rows, lib, cache, progress)
            if glow:
                self.db.x("UPDATE projects SET options=json_set(COALESCE(options,'{}'),'$.glow_info',json(?)) WHERE id=?",
                          (json.dumps(glow), project_id))

        # --- 1. Analyse (nur neue Frames) ------------------------------------
        # neu analysieren: neue Frames und solche mit veralteter Analyse (z. B. vor der Hotpixel-Korrektur)
        # (gelöschte Rohdateien behalten ihre Analyse – sie lässt sich nicht wiederholen)
        todo = [r for r in rows if not r["deleted"] and
                (not r["metrics"] or json.loads(r["metrics"]).get("mv", 1) < quality.METRICS_VERSION)]
        if any(r["metrics"] and r["stacked"] for r in todo) and not any(r["deleted"] and r["stacked"] for r in rows):
            self.db.x("UPDATE projects SET restack=1 WHERE id=?", (project_id,))
            proj["restack"] = 1
        progress(0.02, "Analyse", f"{len(todo)} neue Aufnahmen analysieren")

        def analyze(r):
            try:
                info = json.loads(r["info"])
                raw, m = self._load(r["path"], info, lib, r["night"], cache)
                met = quality.analyze_frame(raw, bool(info["bayerpat"]) and raw.ndim == 2)
                met["calib"] = m.desc
                met["mv"] = quality.METRICS_VERSION
                return r["id"], met, None
            except Exception as e:
                return r["id"], None, str(e)

        with ThreadPoolExecutor(self.s.threads) as ex:
            for n, (fid, met, err) in enumerate(ex.map(analyze, todo)):
                if err:
                    self.db.set_frame(fid, status="failed", reason=f"Lesefehler: {err}", metrics={"n_stars": 0})
                else:
                    self.db.set_frame(fid, metrics=met, status="analyzed")
                progress(0.02 + 0.30 * (n + 1) / max(1, len(todo)), "Analyse")

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
            raise ValueError(f"zu wenige brauchbare Aufnahmen ({sum(len(w) for w in wanted.values())})")
        states = {} if proj["restack"] else {b: StackState.load(stack_dir(wd, b)) for b in by_band}
        states = {b: st for b, st in states.items() if st is not None}
        if any(st.meta.get("scale") != scale for st in states.values()) or len({st.meta.get("ref_fid") for st in states.values()}) > 1:
            states = {}
        for b in by_band:
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
        # Neuaufbau vorbereitet: nach einem Abbruch geht es an den Checkpoints weiter statt von vorn
        self.db.x("UPDATE projects SET restack=0 WHERE id=?", (project_id,))
        # Breitband zuerst (bestes Referenzbild), dann Schmalband; je Band nach Menge
        order = sorted(by_band, key=lambda b: (b in channels.NARROW, -len(wanted[b])))
        n_all = sum(len(w) for w in wanted.values())
        done_before = 0
        for b in order:
            st = states.get(b)
            if st is None and len(wanted[b]) < 3:
                continue                               # zu wenig für einen eigenen Kanal
            todo = [r for r in wanted[b] if not r["stacked"] and not r["deleted"]]
            if todo or st is None:
                ref = next(iter(states.values())).meta if states else None
                lo, span = done_before / n_all, len(wanted[b]) / n_all
                sub = lambda f, stage, msg="", lo=lo, span=span, b=b: progress(
                    0.36 + 0.48 * (lo + span * (f - 0.36) / 0.48), stage, (f"[{b}] " if b else "") + msg)
                st = self._stack(project_id, todo or wanted[b], st, lib, cache, scale, sub, ref, save_dir=stack_dir(wd, b),
                                 stages=stage_dir(wd, b))
                states[b] = st
            done_before += len(wanted[b])
        if not states:
            raise ValueError("kein Kanal mit genug brauchbaren Aufnahmen")
        return states

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
        wd = self.s.work_dir / f"p{proj['id']}"
        ref = next(iter(states.values())).meta.get("ref_fid")
        key = f"{ref}|{mean.shape[1]}x{mean.shape[2]}"
        f = wd / "wcs.json"
        try:
            cached = json.loads(f.read_text()) if f.exists() else {}
            if cached.get("key") == key:
                hdr = cached["wcs"]
            else:
                log("Stack plate-solven")
                hdr = platesolve.solve_image(self.solver_cfg, mean.mean(0), wd / "solve", proj["ra"], proj["dec"])
                f.write_text(json.dumps({"key": key, "wcs": hdr}))
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
        proj = self.db.one("SELECT * FROM projects WHERE id=?", (project_id,))
        if not proj:
            raise ValueError("Projekt nicht gefunden")
        opt = self._options(proj, extra)
        states = self._master(project_id)
        if not states:
            raise ValueError("Noch kein Master-Stack vorhanden – bitte zuerst verarbeiten.")
        prog = lambda f, stage, msg="": progress(start + (1 - start) * f, stage, msg)
        prog(0.0, "Bearbeitung")
        stacked = self.db.q("SELECT id,info FROM frames WHERE project_id=? AND stacked=1", (project_id,))
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
        kind = proj["kind"]
        if not kind and (ext := objects.resolve_extended(proj["name"])) and ext.kind != "generic":
            kind = ext.kind                                 # ältere Projekte: Typ nachträglich aus dem großen Katalog
            self.db.x("UPDATE projects SET kind=? WHERE id=?", (kind, project_id))
        pname = opt["profile"] if opt["profile"] != "auto" else (kind or "generic")
        user = opt.get("params") or {}
        params = {**objects.profile_for(pname), **user}
        if opt.get("star_separation"):
            params["star_separation"] = opt["star_separation"]
        if params.get("star_separation") == "starnet" and not stars.starnet_path():
            params["star_separation"] = "off"           # Profil „nur StarNet“: ohne StarNet keine eigene Trennung
        gx: dict = {}
        if (gmode := graxpert.mode_for(opt.get("graxpert"))) != "off" and (exe := graxpert.graxpert_path()):
            # einmal je Entwicklung auf dem linearen Stack (gecacht) – vor der Farbkalibrierung, damit die
            # Sternfotometrie auf verlaufsfreiem Himmel misst
            mean, gx = graxpert.apply(mean, cov, gmode, float(params.get("ai_denoise", graxpert.DEFAULT_STRENGTH)),
                                      self.s.work_dir / f"p{project_id}" / "graxpert", exe,
                                      log=lambda m: prog(0.05, "Bearbeitung", m))
            params["_ai"] = {k: v for k, v in gx.items() if k in ("bge", "denoise")}
        if mean.shape[0] == 3 and not narrow and params.get("color_calibration", True) and "wb_gains" not in user:
            pcc = self._photometric(proj, states, mean, cov, lambda m: prog(0.1, "Farbkalibrierung", m))
            if pcc:
                params["_pcc"] = pcc
                if "gains" in pcc:
                    params["wb_gains"] = pcc["gains"]
        cnt = self.db.one("SELECT COUNT(*) n, SUM(status='rejected') r FROM frames WHERE project_id=? AND kind='light'", (project_id,))
        ctx = {"exposure_s": exposure, "rejected_pct": 100 * (cnt["r"] or 0) / max(1, cnt["n"]),
               "has_flats": bool(self.db.one("SELECT id FROM frames WHERE kind='flat' LIMIT 1"))}
        rgb, stats, params, _ = develop.develop(
            mean, cov, params, narrow, palette, ctx, fixed=user,
            log=lambda m: prog(0.3, "Bearbeitung", m), render_log=lambda m: prog(0.5, "Bearbeitung", m))

        review_hist = []
        if opt.get("review", True) and self.review_cfg.enabled:
            render = lambda p: processing.process(mean, cov, p, narrowband=narrow, palette=palette)
            quality, before, qc_hist = stats["quality"], stats.get("before"), stats.get("qc")
            rgb, stats, params, review_hist = reviewer.review_loop(
                self.review_cfg, render, params, proj["name"], kind, log=lambda m: prog(0.7, "KI-Prüfung", m),
                first=(rgb, stats))
            if "quality" not in stats:                     # KI hat nachgebessert -> neu bewerten
                stats["quality"] = {**develop.assess(rgb, mono=mean.shape[0] == 1),
                                    "hints": quality["hints"], "measure": quality["measure"]}
                stats["before"], stats["qc"] = before, qc_hist
        if gx:
            stats["graxpert"] = gx
        stats.update({"profile": pname, "narrowband": narrow, "frames": len(stacked), "exposure_s": exposure,
                      "palette": palette, "bands": band_stats})

        # --- Ausgabe ----------------------------------------------------------
        prog(0.85, "Speichern")
        rid = self._save(proj, rgb, mean, cov, stats, review_hist, len(stacked), exposure, params)
        # erst nach dem gespeicherten Ergebnis (ist schon sichtbar): Zwischenstufen für den Verlaufsregler
        try:
            self._render_stages(project_id, rid, states, params, narrow, palette, stats.get("crop"), len(stacked),
                                lambda f, m: prog(0.88 + 0.12 * f, "Stacking-Stufen", m))
        except Exception as e:                         # Stufen sind Beiwerk – das fertige Bild bleibt
            print(f"Stacking-Stufen fehlgeschlagen: {e}")
        if start < 0.5:
            progress(1.0, "Fertig")
        return rid

    def _render_stages(self, project_id, rid, states, params, narrow, palette, crop, n_total, log) -> None:
        """Gespeicherte Stacking-Stufen (weniger Aufnahmen) mit den Parametern des fertigen Bildes und exakt dessen
        Ausschnitt entwickeln -> <base>_s<n>.jpg, Liste in stats.stages. Nur bei einem Kanal (Farbkamera / ein Filter)."""
        if len(states) != 1 or not crop:
            return
        (band, _), = states.items()
        found = [x for x in load_stages(stage_dir(self.s.work_dir / f"p{project_id}", band)) if x["n"] < n_total]
        if not found:
            return
        # externe KI-Werkzeuge kosten je Stufe Minuten -> eigene Verfahren (Hinweis in der Oberfläche)
        p = {k: v for k, v in params.items() if k != "_ai"}
        simplified = [n for n, on in (("GraXpert", "_ai" in params), ("StarNet", p.get("star_separation") == "starnet")) if on]
        if p.get("star_separation") == "starnet":
            p["star_separation"] = "off"
        row = self.db.one("SELECT files, stats FROM results WHERE id=?", (rid,))
        files, st = json.loads(row["files"]), json.loads(row["stats"])
        base = files["jpg"][: -len(".jpg")]
        done = []
        for i, x in enumerate(found):
            log(i / len(found), f"{x['n']} von {n_total} Aufnahmen entwickeln")
            with np.load(x["path"]) as z:
                mean = z["mean"].astype(np.float32) * STAGE_SCALE
                cov = z["cov"].astype(np.float32)
            cov /= max(float(cov.max()), 1e-9)
            if band is not None:
                mean, cov, _, _ = channels.combine({band: (mean, cov)}, palette)
            rgb, _ = processing.process(mean, cov, p, narrowband=narrow, palette=palette, crop=crop)
            del mean, cov
            f = f"{base}_s{x['n']}.jpg"
            Image.fromarray((np.moveaxis(rgb, 0, -1)[::-1] * 255 + 0.5).astype(np.uint8)).save(f, quality=90)
            files[f"s{x['n']}"] = f
            done.append({"n": x["n"], "exposure": x["exposure"]})
        st["stages"] = done
        if simplified:
            st["stages_simplified"] = simplified
        self.db.x("UPDATE results SET files=?, stats=? WHERE id=?", (json.dumps(files), json.dumps(st), rid))

    # -------------------------------------------------------------- stacking #
    def _stack(self, pid, todo, state, lib, cache, scale, progress, ref_meta: dict | None = None,
               save_dir: Path | None = None, stages: Path | None = None) -> StackState:
        if state is None and ref_meta is not None:
            # weiterer Filter: auf die Referenz des ersten Kanals registrieren, Fluss relativ zum eigenen ersten Batch
            i0 = json.loads(todo[0]["info"])
            C = 3 if channels.is_color(i0) else 1
            _, H, W = ref_meta["shape"]
            state = StackState((C, H * scale, W * scale))
            state.meta.update({k: ref_meta[k] for k in ("ref_fid", "ref_stars", "scale", "ref_fwhm")},
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
        meta = state.meta
        if "exposure" not in meta:                      # ältere Zustände: Belichtung der schon gestackten Frames nachtragen
            ids = list(meta["frames"])
            meta["exposure"] = sum((json.loads(r["info"]).get("exptime") or 0) for i in range(0, len(ids), 500)
                                   for r in self.db.q(f"SELECT info FROM frames WHERE id IN ({','.join('?' * len(ids[i:i + 500]))})",
                                                      tuple(ids[i:i + 500])))
        exptime = {r["id"]: json.loads(r["info"]).get("exptime") or 0 for r in todo}
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
                raw, _ = self._load(r["path"], info, lib, r["night"], cache)
                img = to_channels(raw, info["bayerpat"])
                warped, valid = registration.warp(img, M, (H * scale, W * scale), scale, gpu=g)
                sub = valid.ravel()[::7]
                bg = np.array([np.median(warped[c].ravel()[::7][sub]) for c in range(C)])
                ratio = ref_flux[pairs[:, 1]] / np.maximum(tf[pairs[:, 0]], 1e-6)
                fw = max(m["fwhm"] or meta["ref_fwhm"], 1.0)
                return r["id"], ((warped - bg[:, None, None]).astype(np.float32), valid, float(np.median(ratio)), m["noise"], fw), None
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
                if d is None:
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
                n0 = len(meta["frames"])
                cut = {t: t - n0 for t in STAGES if n0 < t <= n0 + len(good)} if stages is not None else {}
                state.add_batch(X, V, ws, workers=self.s.threads, prefixes=cut.values(), gpu=g)
                del X, V
                exp0 = meta["exposure"]
                for (fid, _), w in zip(good, ws):
                    meta["frames"][fid] = float(w)
                    meta["exposure"] += exptime.get(fid, 0)
                    unsaved.append(fid)
                for t, k in cut.items():                 # Zwischenstand nach t Aufnahmen für den Verlaufsregler
                    m, c = state.snaps.pop(k)
                    stages.mkdir(parents=True, exist_ok=True)
                    np.savez(stages / f"n{t:05d}.npz", mean=(m / STAGE_SCALE).astype(np.float16), cov=c.astype(np.float16))
                    (stages / f"n{t:05d}.json").write_text(json.dumps(
                        {"n": t, "exposure": exp0 + sum(exptime.get(fid, 0) for fid, _ in good[:k])}))
                    del m, c
            done += len(chunk)
            # Checkpoint: Frames gelten erst als gestackt, wenn der Zustand mit ihnen gespeichert ist
            if save_dir is not None and unsaved and (ci == len(chunks) - 1 or time.time() - last_save > CHECKPOINT_S):
                state.save(save_dir)
                self.db.many("UPDATE frames SET stacked=1 WHERE id=?", [(f,) for f in unsaved])
                unsaved, last_save = [], time.time()
            progress(0.36 + 0.48 * done / max(1, total), "Stacking",
                     f"{done}/{total} Aufnahmen" + (f" · Grafikkarte {g.name}" if g and not g.broken else ""))
        ex.shutdown()
        if g:
            g.free()
        return state

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
