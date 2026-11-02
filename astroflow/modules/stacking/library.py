"""Hauptordner durchsuchen (z. B. alle Stellina-Beobachtungen auf einer Platte): Aufnahmegruppen finden,
mit der Datenbank abgleichen und ausgewählte Gruppen importieren. Die Quelldateien bleiben unverändert.

Rohdaten werden nie kopiert (seit 0.12.0): auf demselben Laufwerk als Hardlink unter input/uploads/bibliothek
(kostet keinen Platz, der Link wird nach dem Stacken wieder entfernt), sonst am Ort eingebunden – der Frame zeigt
dann direkt auf die Originaldatei (z. B. auf der Stellina-Platte E:) und wird nie gelöscht. Anlass: der Import der
ganzen Stellina-Platte kopierte 95 GB nach C:."""
from __future__ import annotations

import hashlib
import os
import re
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import diskspace, scanner
from .fits_io import FITS_EXT
from .picture_import import PICTURE_EXT, SOURCE_LIMIT, import_picture

CALIB = re.compile(r"dark|flat|bias|offset", re.I)
MAX_GROUPS = 2000
SAMPLE = 4                      # Stichprobe je unbekanntem Beobachtungsordner (je 2 vom Anfang und Ende)


def _walk(root: Path):
    """(Ordner, FITS-Dateien, Bilddateien, {Datei: (Größe, mtime_ns)}) je Ordner; Ordner anderer Software (process,
    stacked …) fallen weg. Größe/Zeit kommen aus dem Verzeichniseintrag – unter Windows ohne weiteren Plattenzugriff
    (einzelnes stat() kostete auf der Stellina-Platte 3,5 ms je Datei)."""
    todo = [root]
    while todo:
        d = todo.pop()
        fits, pics, dirs, st = [], [], [], {}
        try:
            entries = list(os.scandir(d))
        except OSError:
            continue
        for e in entries:
            try:
                if e.is_dir():
                    # verknüpfte Ordner (Symlink, Windows-Junction) nicht betreten – wie os.walk; ein Link auf einen
                    # Elternordner liefe sonst endlos im Kreis
                    linked = e.is_symlink() or (hasattr(e, "is_junction") and e.is_junction())
                    if not linked and not scanner.EXCLUDE_DIR.search(e.name):
                        dirs.append(e.name)
                    continue
                low = e.name.lower()
                is_fits, is_pic = low.endswith(FITS_EXT), low.endswith(PICTURE_EXT)
                if is_fits or is_pic:
                    s = e.stat()
                    f = Path(e.path)
                    st[f] = (s.st_size, s.st_mtime_ns)
                    (fits if is_fits else pics).append(f)
            except OSError:
                pass
        if fits or pics:
            yield Path(d), sorted(fits), sorted(pics), st
        todo.extend(Path(d) / n for n in sorted(dirs, reverse=True))


def name_key(path: str | Path) -> str | None:
    """Pfad ab dem nächsten „…_observation_X“-Ordner (klein geschrieben), z. B.
    „2022-11-24_18-13-37_observation_ngc1579/01-images-initial/img-0001r.fits“. Stellina-Ordnernamen enthalten Datum,
    Uhrzeit und Objekt – zusammen mit Dateiname und Größe erkennt das eine bekannte Aufnahme, ohne sie zu lesen."""
    parts = Path(path).parts
    for i in range(len(parts) - 2, -1, -1):
        if scanner.OBSERVATION.search(parts[i]):
            return "/".join(parts[i:]).lower()
    return None


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
    stat: dict[Path, tuple[int, int]] = {}
    for d, fits, pics, st in _walk(root):
        stat.update(st)
        if fits:
            raw.setdefault(_group_dir(d, root), []).extend(fits)
        elif pics:
            pic_dirs[d] = pics
    groups = []
    for g, files in raw.items():
        calib = not scanner.OBSERVATION.search(g.name) and bool(CALIB.search(g.name))
        files.sort()
        groups.append({"dir": g, "kind": "calibration" if calib else "lights", "files": files,
                       "stat": {f: stat[f] for f in files}})
    for d, pics in pic_dirs.items():
        # unterhalb einer Aufnahmegruppe (Vorschauen, Zwischenstände) sind es keine eigenständigen Fotos
        if any(g in d.parents for g in raw) or len(pics) > 4 * SOURCE_LIMIT:
            continue
        groups.append({"dir": d, "kind": "pictures", "files": pics, "stat": {f: stat[f] for f in pics}})
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
        by_name: dict[str, dict] = {}
        for r in self.db.q("SELECT project_id, kind, status, stacked, deleted, path, size, json_extract(info,'$.fp') fp "
                           "FROM frames WHERE kind!='ignored'"):
            if r["fp"]:
                fps[r["fp"]] = r
            k = name_key(r["path"] or "")
            if k:
                by_name[k] = r
        shas = {r["h"] for r in self.db.q("SELECT json_extract(stats,'$.imported.sha256') h FROM results") if r["h"]}
        names = {r["id"]: r["name"] for r in self.db.q("SELECT id, name FROM projects")}
        return fps, by_name, shas, names

    def _scan(self, root: Path):
        """Schnellpfade, damit nicht jede Datei gelesen werden muss (Stellina-Platte: 31 718 FITS, 35 ms je Fingerabdruck
        ≈ 19 min): (1) bekannte Aufnahmen über Ordnername + Dateiname + Größe, (2) gemerkter Fingerabdruck bei gleicher
        Größe und Änderungszeit, (3) Beobachtungsordner ohne Namenstreffer nur stichprobenartig lesen – erkennt keine
        Stichprobe AstroFlow, ist der Rest neu. Doppelte Aufnahmen fängt der Scan beim Import ohnehin ab."""
        try:
            groups = find_groups(root)
            fps, by_name, shas, names = self._known()
            self._set(message="Abgleich mit AstroFlow …")
            cache = {r["path"]: r for r in self.db.q("SELECT path, size, mtime, fp FROM library_index")}
            rows: dict[Path, dict | None] = {}          # Rohdaten: Datenbankzeile der bekannten Aufnahme
            prints: dict[Path, str | None] = {}         # Fingerabdruck bzw. SHA256 (Bilder)
            unknown: list[tuple[dict, list[Path]]] = []
            for g in groups:
                unk = []
                for f in g["files"]:
                    size, mtime = g["stat"][f]
                    # Größe muss passen, falls bekannt (ältere Einträge haben sie erst nach dem Löschen der Rohdatei)
                    if g["kind"] != "pictures" and (k := name_key(f)) and (r := by_name.get(k)) and r["size"] in (0, None, size):
                        rows[f] = r
                        continue
                    c = cache.get(str(f))
                    if c and c["size"] == size and c["mtime"] == mtime:
                        prints[f] = c["fp"]
                    else:
                        unk.append(f)
                if unk:
                    unknown.append((g, unk))
            n_read = [0]

            def read(items: list[tuple[dict, Path]], label: str):
                total, done = len(items), [0]

                def fp_of(item):
                    g, f = item
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
                            self._set(progress=round(done[0] / total, 3), message=f"{label}: {done[0]} von {total} Dateien …")

                if not items:
                    return
                self._set(progress=0.0, message=f"{label}: {total} Dateien …")
                with ThreadPoolExecutor(max(2, self.eng.s.threads)) as ex:
                    new = list(ex.map(fp_of, items))
                remember = []
                for (g, f), fp in zip(items, new):
                    prints[f] = fp
                    if fp:
                        remember.append((str(f), *g["stat"][f], fp))
                self.db.many("INSERT OR REPLACE INTO library_index(path, size, mtime, fp) VALUES(?,?,?,?)", remember)
                n_read[0] += total

            # Stichproben je Beobachtungsordner (Anfang und Ende), übrige Gruppen vollständig
            # (gemerkte Fingerabdrücke derselben Gruppe zählen als Stichprobe – sonst läse jeder Lauf 4 neue Dateien)
            sampled = [(g, unk) for g, unk in unknown if g["kind"] != "pictures" and name_key(unk[0]) and len(g["files"]) > SAMPLE]
            probe: dict[int, list[Path]] = {}
            new_probe: list[tuple[dict, Path]] = []
            for g, unk in sampled:
                have = [f for f in g["files"] if f in prints][:SAMPLE]
                need = SAMPLE - len(have)
                pick = list(dict.fromkeys(unk[:(need + 1) // 2] + unk[len(unk) - need // 2:])) if need > 0 else []
                probe[id(g)] = have + pick
                new_probe += [(g, f) for f in pick]
            read(new_probe, "Stichproben")
            full: list[tuple[dict, Path]] = []
            skipped = 0
            for g, unk in unknown:
                if id(g) in probe and not any(fps.get(prints.get(f)) for f in probe[id(g)]):
                    skipped += sum(1 for f in unk if f not in prints)     # neu – Rest muss nicht gelesen werden
                    continue
                full += [(g, f) for f in unk if f not in prints]
            read(full, "Unbekannte Dateien prüfen")
            for f, fp in prints.items():
                if f not in rows and fp:
                    rows[f] = fps.get(fp)
            out = [self._describe(g, rows, prints, shas, names, root, n) for n, g in enumerate(groups)]
            self.groups = out
            open_groups = sum(1 for g in out if g["state"] != "done")
            self._set(state="done", progress=1.0, finished=time.time(), read=n_read[0], skipped=skipped,
                      message=(f"{len(out)} Gruppen gefunden, {open_groups} davon noch nicht vollständig in AstroFlow"
                               + (f" ({n_read[0]} Dateien gelesen)." if n_read[0] else ".")) if out
                      else "Keine FITS-Dateien oder Bilder gefunden.")
        except Exception as e:
            traceback.print_exc()
            self._set(state="error", message=f"Suchlauf fehlgeschlagen: {e}")

    def _describe(self, g, rows, prints, shas, names, root, n) -> dict:
        d: Path = g["dir"]
        files = g["files"]
        size = sum(g["stat"][f][0] for f in files)
        todo, known, stacked, owners = [], 0, 0, {}
        for f in files:
            if g["kind"] == "pictures":
                hit = prints.get(f) in shas
                finished = hit
            else:
                r = rows.get(f)
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
                "to_import": todo, "import_bytes": sum(g["stat"][f][0] for f in todo)}

    # ---------------------------------------------------------------- Import #
    def start_import(self, ids: list[str]) -> int:
        with self.lock:
            if self.busy():
                raise RuntimeError("Es läuft bereits ein Suchlauf oder Import.")
            chosen = [g for g in self.groups if g["id"] in set(ids) and g["to_import"]]
            if not chosen:
                raise ValueError("Nichts zu importieren – die Auswahl ist leer oder schon vollständig vorhanden.")
            # Rohdaten brauchen keinen Platz (Hardlink oder am Ort eingebunden) – nur fertige Bilder werden übernommen
            base = self.eng.s.uploads_dir
            base.mkdir(parents=True, exist_ok=True)
            free = diskspace.free(base)
            if free is not None and free < diskspace.MIN_FREE:
                raise ValueError(f"Zu wenig Speicher: frei sind nur {diskspace.gb(free)} auf dem Datenlaufwerk – für Stacks, "
                                 f"Ergebnisse und die Datenbank sollten mindestens {diskspace.gb(diskspace.MIN_FREE)} frei sein.")
            self.state.update(state="importing", progress=0.0, message="Import startet …", imported={}, started=time.time())
        threading.Thread(target=self._import, args=(chosen,), daemon=True, name="library-import").start()
        return len(chosen)

    @staticmethod
    def _put(src: Path, dst: Path, base: Path) -> bool:
        """Hardlink unter input/uploads anlegen (gleiche Platte, kostet keinen Speicher). False = nicht möglich (anderes
        Laufwerk, exFAT-USB-Platte, Netzlaufwerk) – dann wird die Datei am Ort eingebunden, nie kopiert."""
        if not diskspace.same_drive(src, base):
            return False
        if dst.exists() and dst.stat().st_size == src.stat().st_size:
            return True
        dst.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.link(src, dst)
            return True
        except OSError:
            return False

    def _import(self, chosen: list[dict]):
        eng = self.eng
        total = sum(len(g["to_import"]) for g in chosen)
        done, raw, pics, errors = 0, 0, 0, []
        in_place: list[Path] = []                                   # am Ort eingebunden (kein Hardlink möglich)
        base = eng.s.uploads_dir / "bibliothek"
        taken: dict[str, set] = {g["id"]: set() for g in chosen}   # tatsächlich übernommene Dateien je Gruppe
        try:
            full = False
            for g in chosen:
                if full:
                    break
                tag = hashlib.sha1(str(g["dir"]).encode()).hexdigest()[:6]
                for f in g["to_import"]:
                    left = diskspace.free(base.parent)
                    if left is not None and left < diskspace.MIN_FREE:
                        errors.append(f"Import nach {done} von {total} Dateien angehalten: nur noch {diskspace.gb(left)} frei. "
                                      "Nach dem Freigeben von Speicher erneut importieren – der Rest bleibt ausgewählt.")
                        full = True
                        break
                    try:
                        if g["kind"] == "pictures":
                            with open(f, "rb") as fh:
                                import_picture(eng, fh, f.name, True)
                            pics += 1
                        else:
                            if not self._put(f, base / tag / g["dir"].name / f.relative_to(g["dir"]), base.parent):
                                in_place.append(f)
                            raw += 1
                        taken[g["id"]].add(f)
                    except Exception as e:
                        errors.append(f"{f.name}: {e}")
                    done += 1
                    if done % 5 == 0 or done == total:
                        self._set(progress=round(done / total * 0.9, 3), message=f"{done} von {total} Dateien übernommen …")
            self._set(message="Aufnahmen werden einsortiert …", progress=0.92)
            scan = eng.scan_and_queue(in_place) if raw else {"new_by_project": {}, "duplicates": 0}
            for g in chosen:                                     # übernommene Dateien sind nun bekannt, der Rest bleibt offen
                got = taken[g["id"]]
                g["to_import"] = [f for f in g["to_import"] if f not in got]
                g["n_known"] += len(got)
                g["n_new"] = len(g["to_import"])
                g["import_bytes"] = sum(g["stat"][f][0] for f in g["to_import"]) if "stat" in g else 0
                if g["to_import"]:
                    g["state"] = "partial" if g["n_known"] else "new"
                elif g["kind"] == "lights":
                    g["state"] = "unstacked"
                else:
                    g["state"], g["n_done"] = "done", g["n_files"]
            self._set(state="done", progress=1.0, finished=time.time(),
                      imported={"raw": raw, "pictures": pics, "in_place": len(in_place), "projects": scan["new_by_project"],
                                "duplicates": scan.get("duplicates", 0), "errors": errors[:20]},
                      message=f"{raw} Aufnahmen und {pics} Bilder übernommen"
                      + (f" ({len(in_place)} Aufnahmen bleiben am Ort und werden von dort gelesen)" if in_place else "")
                      + (f" · {len(errors)} Fehler" if errors else "")
                      + (" – die Verarbeitung startet automatisch." if raw and eng.s.auto else "."))
        except Exception as e:
            traceback.print_exc()
            self._set(state="error", message=f"Import fehlgeschlagen: {e}")
