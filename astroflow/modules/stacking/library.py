"""Hauptordner durchsuchen (z. B. alle Stellina-Beobachtungen auf einer Platte): Aufnahmegruppen finden,
mit der Datenbank abgleichen und ausgewählte Gruppen importieren. Die Quelldateien bleiben unverändert."""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import scanner
from .fits_io import FITS_EXT
from .picture_import import PICTURE_EXT, SOURCE_LIMIT, import_picture

CALIB = re.compile(r"dark|flat|bias|offset", re.I)
MAX_GROUPS = 2000


def _walk(root: Path):
    """(Ordner, FITS-Dateien, Bilddateien) je Ordner; Ordner anderer Software (process, stacked …) fallen weg."""
    for dp, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if not scanner.EXCLUDE_DIR.search(d))
        fits = sorted(Path(dp) / f for f in files if f.lower().endswith(FITS_EXT))
        pics = sorted(Path(dp) / f for f in files if f.lower().endswith(PICTURE_EXT))
        if fits or pics:
            yield Path(dp), fits, pics


def _group_dir(d: Path, root: Path) -> Path:
    """Stellina & Co.: alle Unterordner einer „…_observation_X“ gehören zu einer Gruppe."""
    for parent in [d, *d.parents]:
        if scanner.OBSERVATION.search(parent.name):
            return parent
        if parent == root:
            break
    return d


def find_groups(root: Path) -> list[dict]:
    """Rohdaten- und Bildgruppen unterhalb von root (ohne Datenbankabgleich)."""
    raw: dict[Path, list[Path]] = {}
    pic_dirs: dict[Path, list[Path]] = {}
    for d, fits, pics in _walk(root):
        if fits:
            raw.setdefault(_group_dir(d, root), []).extend(fits)
        elif pics:
            pic_dirs[d] = pics
    groups = []
    for g, files in raw.items():
        calib = not scanner.OBSERVATION.search(g.name) and bool(CALIB.search(g.name))
        groups.append({"dir": g, "kind": "calibration" if calib else "lights", "files": files})
    for d, pics in pic_dirs.items():
        # unterhalb einer Aufnahmegruppe (Vorschauen, Zwischenstände) sind es keine eigenständigen Fotos
        if any(g in d.parents for g in raw) or len(pics) > 4 * SOURCE_LIMIT:
            continue
        groups.append({"dir": d, "kind": "pictures", "files": pics})
    return sorted(groups, key=lambda g: str(g["dir"]).lower())[:MAX_GROUPS]


class Library:
    def __init__(self, eng):
        self.eng = eng
        self.db = eng.db
        self.lock = threading.Lock()
        self.state: dict = {"state": "idle", "root": eng.ctx.store.get("library").get("root", "")}
        self.groups: list[dict] = []

    # ---------------------------------------------------------------- Zustand #
    def view(self) -> dict:
        st = dict(self.state)
        st["groups"] = [{k: v for k, v in g.items() if k not in ("files", "dir", "to_import")} for g in self.groups]
        return st

    def _set(self, **kw):
        self.state.update(kw)

    def busy(self) -> bool:
        return self.state["state"] in ("scanning", "importing")

    # ------------------------------------------------------------------ Scan #
    def start_scan(self, root: str) -> None:
        p = Path(root.strip().strip('"')).expanduser()
        if not p.is_dir():
            raise ValueError(f"Ordner nicht gefunden: {root}")
        with self.lock:
            if self.busy():
                raise RuntimeError("Es läuft bereits ein Suchlauf oder Import.")
            self.groups = []
            self.state = {"state": "scanning", "root": str(p), "progress": 0.0, "message": "Ordner werden durchsucht …",
                          "started": time.time()}
        self.eng.ctx.store.update("library", {"root": str(p)})
        threading.Thread(target=self._scan, args=(p,), daemon=True, name="library-scan").start()

    def _known(self):
        fps: dict[str, dict] = {}
        for r in self.db.q("SELECT project_id, kind, status, stacked, deleted, json_extract(info,'$.fp') fp "
                           "FROM frames WHERE kind!='ignored' AND json_extract(info,'$.fp') IS NOT NULL"):
            fps[r["fp"]] = r
        shas = {r["h"] for r in self.db.q("SELECT json_extract(stats,'$.imported.sha256') h FROM results") if r["h"]}
        names = {r["id"]: r["name"] for r in self.db.q("SELECT id, name FROM projects")}
        return fps, shas, names

    def _scan(self, root: Path):
        try:
            groups = find_groups(root)
            fps, shas, names = self._known()
            total = sum(len(g["files"]) for g in groups) or 1
            done = [0]

            def fp_of(g, f):
                try:
                    if g["kind"] == "pictures":
                        h = hashlib.sha256()
                        with open(f, "rb") as fh:
                            while b := fh.read(1 << 20):
                                h.update(b)
                        return h.hexdigest()
                    return scanner.fingerprint(f)
                except OSError:
                    return None
                finally:
                    done[0] += 1
                    if done[0] % 20 == 0:
                        self._set(progress=round(done[0] / total, 3), message=f"{done[0]} von {total} Dateien geprüft …")

            out = []
            with ThreadPoolExecutor(max(2, self.eng.s.threads)) as ex:
                for n, g in enumerate(groups):
                    files = g["files"]
                    prints = list(ex.map(lambda f: fp_of(g, f), files))
                    out.append(self._describe(g, files, prints, fps, shas, names, root, n))
            self.groups = out
            fresh = sum(1 for g in out if g["state"] != "done")
            self._set(state="done", progress=1.0, finished=time.time(),
                      message=f"{len(out)} Gruppen gefunden, {fresh} davon noch nicht vollständig in AstroFlow." if out
                      else "Keine FITS-Dateien oder Bilder gefunden.")
        except Exception as e:
            traceback.print_exc()
            self._set(state="error", message=f"Suchlauf fehlgeschlagen: {e}")

    def _describe(self, g, files, prints, fps, shas, names, root, n) -> dict:
        d: Path = g["dir"]
        size = sum(f.stat().st_size for f in files if f.exists())
        todo, known, stacked, owners = [], 0, 0, {}
        for f, fp in zip(files, prints):
            if g["kind"] == "pictures":
                hit = fp in shas
                finished = hit
            else:
                r = fps.get(fp) if fp else None
                hit = r is not None
                finished = bool(r) and (r["kind"] != "light" or r["stacked"] or r["deleted"] or r["status"] in ("rejected", "duplicate"))
                if r and r["project_id"]:
                    owners[r["project_id"]] = owners.get(r["project_id"], 0) + 1
            known += hit
            stacked += finished
            if not hit:
                todo.append(f)
        total = len(files)
        state = "done" if stacked == total else "new" if known == 0 else "partial" if known < total else "unstacked"
        rel = str(d.relative_to(root)) if d != root else d.name
        obj, camera, night = None, None, None
        if g["kind"] == "lights":
            try:
                info = scanner.read_info(files[0])
                obj = info.object or scanner.object_from_path(files[0])
                camera = (info.instrument or "").strip() or None
                night = scanner.night_of(info, files[0])
            except Exception:
                pass
        project = max(owners, key=owners.get) if owners else None
        label = (obj or d.name) if g["kind"] == "lights" else d.name
        m = scanner.OBSERVATION.search(d.name)
        if g["kind"] == "lights" and not obj and m:
            label = m.group(1).replace("_", " ")
        return {"id": f"g{n}", "kind": g["kind"], "name": label, "rel": rel, "dir": d, "files": files,
                "n_files": total, "n_known": known, "n_done": stacked, "n_new": len(todo), "bytes": size,
                "camera": camera, "night": night, "state": state,
                "project": {"id": project, "name": names.get(project)} if project else None,
                "to_import": todo}

    # ---------------------------------------------------------------- Import #
    def start_import(self, ids: list[str]) -> int:
        with self.lock:
            if self.busy():
                raise RuntimeError("Es läuft bereits ein Suchlauf oder Import.")
            chosen = [g for g in self.groups if g["id"] in set(ids) and g["to_import"]]
            if not chosen:
                raise ValueError("Nichts zu importieren – die Auswahl ist leer oder schon vollständig vorhanden.")
            self.state.update(state="importing", progress=0.0, message="Import startet …", imported={}, started=time.time())
        threading.Thread(target=self._import, args=(chosen,), daemon=True, name="library-import").start()
        return len(chosen)

    def _put(self, src: Path, dst: Path):
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.exists() and dst.stat().st_size == src.stat().st_size:
            return
        try:
            os.link(src, dst)                       # gleiche Platte: kostet keinen Speicher
        except OSError:
            shutil.copy2(src, dst)

    def _import(self, chosen: list[dict]):
        eng = self.eng
        total = sum(len(g["to_import"]) for g in chosen)
        done, raw, pics, errors = 0, 0, 0, []
        base = eng.s.uploads_dir / "bibliothek"
        try:
            for g in chosen:
                tag = hashlib.sha1(str(g["dir"]).encode()).hexdigest()[:6]
                for f in g["to_import"]:
                    try:
                        if g["kind"] == "pictures":
                            with open(f, "rb") as fh:
                                import_picture(eng, fh, f.name, True)
                            pics += 1
                        else:
                            self._put(f, base / tag / g["dir"].name / f.relative_to(g["dir"]))
                            raw += 1
                    except Exception as e:
                        errors.append(f"{f.name}: {e}")
                    done += 1
                    if done % 5 == 0 or done == total:
                        self._set(progress=round(done / total * 0.9, 3), message=f"{done} von {total} Dateien übernommen …")
            self._set(message="Aufnahmen werden einsortiert …", progress=0.92)
            scan = eng.scan_and_queue() if raw else {"new_by_project": {}, "duplicates": 0}
            for g in chosen:                                     # übernommene Gruppen sind nun bekannt
                g["n_known"], g["n_new"], g["to_import"] = g["n_files"], 0, []
                if g["kind"] == "lights":
                    g["state"] = "unstacked"
                else:
                    g["state"], g["n_done"] = "done", g["n_files"]
            self._set(state="done", progress=1.0, finished=time.time(),
                      imported={"raw": raw, "pictures": pics, "projects": scan["new_by_project"],
                                "duplicates": scan.get("duplicates", 0), "errors": errors[:20]},
                      message=f"{raw} Aufnahmen und {pics} Bilder übernommen" + (f" · {len(errors)} Fehler" if errors else "")
                      + (" – die Verarbeitung startet automatisch." if raw and eng.s.auto else "."))
        except Exception as e:
            traceback.print_exc()
            self._set(state="error", message=f"Import fehlgeschlagen: {e}")
