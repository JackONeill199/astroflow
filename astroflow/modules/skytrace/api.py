"""SkyTrace-API (unter /api/skytrace): Läufe, Kandidaten, Analyseprozess, Standort- und Katalogeinstellungen.
Portiert aus SkyTrace `web_server.py`; die fachliche Logik bleibt in der Engine."""
from __future__ import annotations

import datetime as dt
import json
import math
import os
import re
import shutil
import subprocess
import threading
import time
import traceback
from pathlib import Path
from functools import lru_cache
from urllib.parse import quote

from fastapi import APIRouter, Body, HTTPException, Request
from fastapi.responses import FileResponse, Response

from ...core import security
from ...core.errors import user_message

DEFAULT_SOURCE = str(Path.home())
MODES = ("Bildfolge → Ereignisvideo", "Fotos", "Videos")
DEFAULT_PREFS = dict(source=DEFAULT_SOURCE, observation="", samples=0, fps=8, mode=MODES[0], solve=True)


def short_name(name: str | None) -> str:
    old = re.match(r"^(.+)_observation_\d{4}-\d\d-\d\d-p\d+-\d+$", name or "")    # ältere automatische Läufe
    return (old.group(1) if old else (name or "Beobachtung").split("_observation_")[-1]).replace("_", " ")


def within(root: Path, relative: str) -> Path:
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError("Ungültiger Dateipfad")
    return path


def _read_json(path: Path, default):
    from json_store import read_json
    return read_json(path, default)


@lru_cache(maxsize=32)
def _frame_times(path: str, stamp: int, size: int) -> dict:
    """Original-Bildnummer auf gespeicherte Videozeit abbilden; ausgeschlossene Frames stehen hier nicht."""
    from json_store import open_read
    out = {}
    with open_read(Path(path)) as stream:
        for line in stream:
            try:
                row = json.loads(line)
                seconds = row.get('video_seconds')
                if isinstance(seconds, (int, float)) and math.isfinite(seconds) and seconds >= 0:
                    out[int(row['index']) + 1] = seconds
            except (ValueError, TypeError, KeyError):
                continue
    return out


def view3d_available(c: dict) -> bool:
    from .orbit3d import available
    return available(c)


def scene_3d(mod, payload: dict) -> dict:
    """Bahnrechnung im Server; ohne sgp4 hier im Engine-Interpreter (hat sgp4 über skyfield)."""
    import importlib.util
    import sys
    if importlib.util.find_spec("sgp4") is not None:
        from .orbit3d import build
        return build(payload)
    if mod.python == sys.executable:
        return dict(ok=False, reason="Für die Bahnrechnung fehlt das Python-Paket sgp4 (pip install sgp4).")
    out = subprocess.run([mod.python, str(Path(__file__).parent / "orbit3d.py")], input=json.dumps(payload), capture_output=True,
                         text=True, encoding="utf-8", timeout=90, env=mod.engine_env(),
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if out.returncode != 0:
        return dict(ok=False, reason="Bahnrechnung im Engine-Interpreter fehlgeschlagen: " + (out.stderr.strip().splitlines() or ["?"])[-1][:200])
    return json.loads(out.stdout)


def list_runs(results: Path, limit: int = 80) -> list[dict]:
    paths = sorted(results.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True) if results.exists() else []
    runs = []
    for path in paths:
        if not path.is_dir() or not (path / "bericht.html").exists():
            continue
        summaries = _read_json(path / "videos.json", []) or []
        runs.append(dict(id=path.name, modified=path.stat().st_mtime, sessions=len(summaries),
                         frames=sum(s.get("frames", 0) for s in summaries),
                         excluded=sum(s.get("excluded", 0) or 0 for s in summaries),
                         candidates=sum(s.get("candidate_count", s.get("persistent_trails", 0)) for s in summaries),
                         title=summaries[0]["session"] if len(summaries) == 1 else path.name,
                         preview=next((f"{path.name}/{Path(s['video']).parent.as_posix()}/live.jpg" for s in summaries
                                       if s.get("video") and (path / Path(s["video"]).parent / "live.jpg").exists()), None)))
        if len(runs) >= limit:
            break
    return runs


class Controller:
    """Startet und überwacht genau einen Analyseprozess (JSON-Zeilen über stdout, Stopp über STOP-Datei)."""

    def __init__(self, mod):
        self.mod = mod
        self.results = mod.home / "Ergebnisse"
        self.prefs = mod.home / "web_preferences.json"
        self.latest = mod.home / "letzter-lauf.txt"
        self.lock = threading.RLock()
        self.process = None
        self.cancel = None
        self.sequence = 0
        self.video_seen = {}
        self.mode = None
        self.current = self.latest_run()
        self.status = "Bereit für deine nächste Entdeckung."
        self.logs = []
        self.row = None
        self.stats = dict(processed=0, total=0, hits=0, excluded=0, candidates=0)
        self.stopping = False
        self.failed = False
        self.started = self.finished = self.operation = None

    def latest_run(self):
        try:
            path = Path(self.latest.read_text(encoding="utf-8").strip()).resolve()
            if path.is_dir() and path.parent == self.results.resolve():
                return path.name
        except OSError:
            pass
        return None

    def media_url(self, path) -> str | None:
        try:
            return "/api/skytrace/media/" + quote(Path(path).resolve().relative_to(self.results.resolve()).as_posix(), safe="/")
        except ValueError:
            return None

    def snapshot(self, logs: bool = True) -> dict:
        with self.lock:
            return dict(mode=self.mode, revision=self.sequence, running=self.process is not None, stopping=self.stopping,
                        failed=self.failed, run=self.current, status=self.status, logs=self.logs[-100:] if logs else [],
                        row=self.row, stats=dict(self.stats), started=self.started, finished=self.finished, operation=self.operation)

    def receive(self, row):
        with self.lock:
            self.sequence += 1
            kind = row.get("type", "log")
            if row.get("text"):
                self.status = row["text"]
                self.logs.append(dict(id=self.sequence, time=time.time(), type=kind, text=row["text"]))
            if len(self.logs) > 600:
                self.logs = self.logs[-400:]
            if kind in ("sequence", "video", "result", "rejected"):
                safe = {k: v for k, v in row.items() if k != "meta"}
                safe["preview_url"] = self.media_url(row["preview_absolute"]) if row.get("preview_absolute") else None
                safe["clean_preview_url"] = self.media_url(row["preview_clean_absolute"]) if row.get("preview_clean_absolute") else None
                safe["filename"] = Path(row.get("file", "")).name
                self.row = safe
                if kind == "video":
                    key = row.get("file", "")
                    current = int(row.get("frame", 0)) + 1
                    self.stats["processed"] += max(0, current - self.video_seen.get(key, 0))
                    self.video_seen[key] = current
                    self.stats["total"] = row.get("frame_total", 0)
                    self.stats["current"] = current
                else:
                    self.stats["processed"] += 1
                    self.stats["total"] = row.get("total", self.stats["total"])
                    self.stats["current"] = row.get("current", self.stats["processed"])
                self.stats["excluded"] += int(kind == "rejected")
                detections = row.get("detections", [])
                self.stats["hits"] += int(bool(detections))
                self.stats["candidates"] += len(detections)

    def run_path(self, name: str) -> Path:
        if not name or Path(name).name != name:
            raise ValueError("Ungültiger Lauf")
        path = within(self.results, name)
        if not path.is_dir():
            raise ValueError("Lauf nicht gefunden")
        return path

    def launch(self, options: dict, refresh: bool = False, replay: dict | None = None) -> dict:
        from json_store import write_json
        with self.lock:
            if self.process is not None:
                raise ValueError("Eine Analyse läuft bereits.")
            py, code = self.mod.python, Path(__file__).parent / "engine"
            if refresh:
                out = self.run_path(options.get("run") or self.current)
                if self.mod.sitejobs.busy_with(out.name):
                    raise ValueError("Dieser Lauf wird gerade neu abgeglichen – bitte warten.")
                if not (out / "videos.json").exists():
                    raise ValueError("Dieser Lauf hat keine Bildfolgen.")
                cancel = out / ("STOP-update-" + str(time.time_ns()))
                cmd = [py, "-u", str(code / "candidate_report.py"), str(out), "--stop-file", str(cancel)]
                mode = None
            else:
                source = Path(options.get("source") or DEFAULT_SOURCE).expanduser().resolve()
                if not source.is_dir():
                    raise ValueError("Der Beobachtungsordner existiert nicht.")
                parent = source
                observation = options.get("observation", "")
                if observation:
                    source = within(parent, observation)
                    if source.parent != parent or not source.is_dir():
                        raise ValueError("Beobachtung nicht gefunden.")
                try:
                    count = float(options.get("samples", 0))
                    samples = int(count)
                    fps = float(options.get("fps", 8))
                except (TypeError, ValueError, OverflowError):
                    raise ValueError("Ungültige Bilderzahl oder Bildrate.") from None
                if count != samples:
                    raise ValueError("Die Bilderzahl muss eine ganze Zahl sein.")
                mode = options.get("mode", MODES[0])
                if mode not in MODES or not 0 <= samples <= 100000 or not 1 <= fps <= 60:
                    raise ValueError("Ungültige Analyse-Einstellungen.")
                self.results.mkdir(parents=True, exist_ok=True)
                if not replay and (source.is_relative_to(self.results.resolve()) or self.results.resolve().is_relative_to(source)):
                    raise ValueError("Originalordner und Ergebnisordner müssen getrennt sein.")
                out = self.results / dt.datetime.now().strftime("%Y-%m-%d_%H-%M-%S-%f")
                out.mkdir()
                cancel = out / "STOP"
                cmd = [py, "-u", str(code / "analyse.py"), "--source", str(source), "--output", str(out), "--mode", mode,
                       "--samples", str(samples), "--fps", str(fps)]
                if replay:
                    write_json(out / 'eingang.json', replay)
                    cmd.extend(['--input-manifest', str(out / 'eingang.json')])
                if mode == MODES[0] and samples:
                    cmd.extend(["--limit", str(samples)])
                if options.get("solve", True):
                    cmd.append("--solve")
                if options.get("remember", True):        # automatische Läufe ändern die Formularvorgaben nicht
                    write_json(self.prefs, dict(source=str(parent), observation=observation, samples=samples, fps=fps, mode=mode,
                                                solve=bool(options.get("solve", True))))
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                                    errors="replace", creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                                    cwd=self.mod.home, env=self.mod.engine_env())
            self.process, self.cancel, self.current = proc, cancel, out.name
            self.operation = "refresh" if refresh else "analysis"
            self.stopping = self.failed = False
            self.started, self.finished = time.time(), None
            self.logs, self.row = [], None
            self.stats = dict(processed=0, total=0, hits=0, excluded=0, candidates=0)
            self.video_seen, self.mode = {}, mode
            self.status = "Kandidatenbericht wird aktualisiert …" if refresh else "Analyse wird vorbereitet …"
            self.sequence += 1
            self.latest.write_text(str(out), encoding="utf-8")
            threading.Thread(target=self.read_process, args=(proc,), daemon=True).start()
            return self.snapshot()

    def read_process(self, proc):
        try:
            for line in proc.stdout:
                try:
                    row = json.loads(line)
                except ValueError:
                    row = dict(type="log", text=line.strip())
                if isinstance(row, dict):
                    self.receive(row)
        finally:
            code = proc.wait()
            with self.lock:
                self.process = None
                self.finished = time.time()
                self.sequence += 1
                self.failed = bool(code) and not self.stopping
                if self.stopping:
                    self.status = "Angehalten. Deine Teilergebnisse sind gespeichert."
                elif code:
                    self.status = "Analyse unterbrochen. Details stehen im Protokoll."
                elif self.operation == "refresh":
                    self.status = "Zeitreihen und Katalogstatus sind aktualisiert."
                else:
                    self.status = "Analyse abgeschlossen. Deine Ergebnisse sind bereit."

    def delete_run(self, name: str) -> dict:
        """Analyse-Lauf samt Videos, Ausschnitten und Bericht löschen. Originalaufnahmen liegen nie im Ergebnisordner
        und bleiben unberührt; ein laufender oder gerade aktualisierter Lauf wird nicht gelöscht."""
        path = self.run_path(name)
        if not ((path / "videos.json").exists() or (path / "bericht.html").exists() or (path / "messwerte.jsonl").exists()):
            raise ValueError("Kein SkyTrace-Lauf")
        with self.lock:
            if self.process is not None and self.current == name:
                raise ValueError("Diese Analyse läuft gerade – erst anhalten.")
            if self.mod.sitejobs.busy_with(name):
                raise ValueError("Dieser Lauf wird gerade neu abgeglichen – bitte warten.")
            size = sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
            def retry(func, p, _exc):                    # Windows: Videos können kurz von einer Wiedergabe gesperrt sein
                time.sleep(0.3)
                os.chmod(p, 0o666)
                func(p)
            shutil.rmtree(path, onexc=retry)
            if self.current == name:
                self.current = None
                self.latest.unlink(missing_ok=True)
                self.status = "Bereit für deine nächste Entdeckung."
                self.sequence += 1
        return {"deleted": name, "bytes": size}

    def stop(self) -> dict:
        with self.lock:
            if self.process is not None:
                self.cancel.touch()
                self.stopping = True
                self.sequence += 1
                self.status = "Wird angehalten · aktuelles Bild und Video werden gespeichert …"
            return self.snapshot()

    # ---- Lauf lesen ---------------------------------------------------------
    def run_data(self, name: str) -> dict:
        path = self.run_path(name)
        summaries = _read_json(path / "videos.json", []) or []
        sessions, candidates = [], []
        url = self.media_url
        for summary in summaries:
            item = dict(summary)
            folder = within(path, str(Path(summary["video"]).parent))
            item["video_url"] = url(within(path, summary["video"]))
            item["clean_url"] = url(within(path, summary["clean_video"])) if summary.get("clean_video") else None
            preview = folder / "live.jpg"
            item["preview_url"] = url(preview) if preview.exists() else None
            clean_preview = folder / "live-clean.jpg"
            if not clean_preview.exists() and summary.get("clean_video"):
                try:                                     # letztes Videobild als saubere Vorschau (nur mit OpenCV)
                    import cv2
                    capture = cv2.VideoCapture(str(within(path, summary["clean_video"])))
                    try:
                        count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
                        if count:
                            capture.set(cv2.CAP_PROP_POS_FRAMES, count - 1)
                        ok, frame = capture.read()
                        if ok:
                            cv2.imwrite(str(clean_preview), frame)
                    finally:
                        capture.release()
                except ImportError:
                    pass
            item["clean_preview_url"] = url(clean_preview) if clean_preview.exists() else None
            items = _read_json(within(path, summary["candidates_file"]), []) if summary.get("candidates_file") else []
            item["folder"] = folder.name
            item["sat_matches"] = sum(1 for c in items or [] if any(r.get("provider") == "satellites" and r.get("status") == "compatible"
                                                                      for r in c.get("catalogs") or []))
            item["trails"] = sum(1 for c in items or [] if c.get("world_path"))
            found = _read_json(folder / "standortsuche.json", None)
            if isinstance(found, dict):        # letzte Standortsuche (rematch_sites.py search) – kurz für die Liste
                item["site_search"] = {k: found.get(k) for k in ("ok", "worth", "before", "after", "tracks", "created", "shift_km")}
            try:
                frame_path = folder / 'frames.jsonl'
                stat = frame_path.stat()
                frame_times = _frame_times(str(frame_path), stat.st_mtime_ns, stat.st_size)
            except OSError:
                frame_times = {}
            try:
                from video_overlays import timeline
                item["overlay_timeline"] = timeline(folder, items or [])
            except ImportError:
                item["overlay_timeline"] = None
            for c in items or []:
                c["key"] = folder.name + "/" + c["id"]
                c["view3d"] = view3d_available(c)
                if c.get("context_image"):
                    c["context_image"]["url"] = url(within(path, c["context_image"]["image"]))
                for frame in c.get("frames", []):
                    frame["url"] = url(within(path, frame["image"]))
                    # Zeit aus akzeptierten Videoframes, nicht aus Originalnummer/fps ableiten (Lücken!).
                    frame.pop('video_seconds', None)
                    if not frame.get('excluded') and frame.get('index') in frame_times:
                        frame['video_seconds'] = frame_times[frame['index']]
                    full = frame.get('full_image')
                    context = c.get('context_image')
                    if not full and context and context.get('frame') == frame.get('index'):
                        full = {k: context[k] for k in ('image', 'width', 'height') if k in context}
                    if full and full.get('image') and within(path, full['image']).is_file():
                        frame['full_image'] = {**full, 'url': url(within(path, full['image']))}
                    else:
                        frame.pop('full_image', None)
                candidates.append(c)
            sessions.append(item)
        if not summaries and (path / "messwerte.jsonl").exists():
            grouped = {}
            for line in (path / "messwerte.jsonl").read_text(encoding="utf-8").splitlines():
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                session = row.get("session") or "Videoanalyse"
                item = grouped.setdefault(session, dict(session=session, frames=0, hits=0, excluded=0, state="Foto-/Videoanalyse",
                                                        video_url=None, clean_url=None, preview_url=None, clean_preview_url=None))
                item["frames"] += 1
                item["hits"] += int(bool(row.get("detections") or row.get("meta", {}).get("detections")))
                if row.get("preview"):
                    preview = within(path, row["preview"])
                    item["preview_url"] = url(preview)
                    clean = preview.with_name(preview.stem + "-clean" + preview.suffix)
                    item["clean_preview_url"] = url(clean) if clean.exists() else None
            sessions.extend(grouped.values())
        return dict(id=name, sessions=sessions, candidates=candidates, report_url=url(path / "bericht.html"),
                    modified=(path / "videos.json").stat().st_mtime if (path / "videos.json").exists() else 0)

    def orbit3d(self, name: str, key: str) -> dict:
        """3D-Szene eines Kandidaten (Bahnen, Blickfeld, Standort). Fehlt etwas, kommt `ok: False` mit Grund statt eines Fehlers."""
        from .orbit3d import payload_for
        path = self.run_path(name)
        folder_name, _, cid = (key or "").partition("/")
        for summary in _read_json(path / "videos.json", []) or []:
            if not summary.get("candidates_file"):
                continue
            folder = within(path, str(Path(summary["video"]).parent))
            if folder.name != folder_name:
                continue
            c = next((x for x in _read_json(within(path, summary["candidates_file"]), []) or [] if x.get("id") == cid), None)
            if c is None:
                break
            payload = payload_for(folder, c, self.mod.home / "catalog_cache" / "archives")
            if payload.get("reason"):
                return dict(ok=False, reason=payload["reason"])
            return dict(scene_3d(self.mod, payload), candidate=dict(id=c["id"], name=c.get("label"), key=key))
        raise ValueError("Kandidat nicht gefunden")

    def sitefit(self, name: str, folder_name: str) -> dict:
        """Standort/Uhrversatz der Nacht aus den zugeordneten Satellitenspuren schätzen (sitefit.py) – nur Vorschlag."""
        path = self.run_path(name)
        folder = within(path, folder_name)
        if not (folder / "kandidaten.json").is_file():
            raise ValueError("Beobachtung nicht gefunden")
        try:
            from .sitefit import fit_session
        except ImportError as e:                              # sgp4/erfa fehlen im Server
            return dict(ok=False, reason=f"Rückrechnung braucht sgp4 und erfa im AstroFlow-Python ({e.name}).")
        return fit_session(folder, self.mod.home / "catalog_cache" / "archives")

    def observation_history(self, source: Path, names: list[str]) -> dict:
        """Gespeicherte Bildfolgen ihrem Originalordner zuordnen, auch aus älteren Läufen."""
        from json_store import open_read
        history = {}
        for manifest in self.results.glob("*/videos.json"):
            summaries = _read_json(manifest, [])
            if not isinstance(summaries, list):
                continue
            for summary in summaries:
                if not isinstance(summary, dict) or summary.get("session") not in names:
                    continue
                name = summary["session"]
                try:
                    folder = within(manifest.parent, str(Path(summary["video"]).parent))
                    original = summary.get("source_folder")
                    if original:
                        matches = Path(original).resolve() == (source / name).resolve()
                    else:
                        matches = False
                        for filename in ("frames.jsonl", "ausgeschlossen.jsonl"):
                            try:
                                with open_read(folder / filename) as rows:
                                    first = json.loads(next(rows))
                                matches = (source / name).resolve() in Path(first["file"]).resolve().parents
                            except (OSError, ValueError, KeyError, StopIteration, TypeError):
                                continue
                            if matches:
                                break
                    if not matches:
                        continue
                    complete = summary.get("state") == "Fertig" and not summary.get("limited", False)
                    if history.get(name, {}).get("status") == "completed":
                        continue
                    history[name] = dict(status="completed" if complete else "partial", run=manifest.parent.name)
                except (ValueError, KeyError, TypeError):
                    continue
        return history


# Über /media ausgelieferte Dateien: Bilder, Videos und die eigenständigen HTML-Berichte (bericht.html, bewegungspruefung.html).
# Lauf-Interna (eingang.json, frames.jsonl, Protokolle …) enthalten Originalpfade und bleiben auf dem Rechner.
MEDIA_TYPES = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".mp4", ".webm", ".m4v", ".html"}


def build_router(mod) -> APIRouter:
    r = APIRouter()
    ctl = mod.ctl

    def bad(e: Exception):
        raise HTTPException(400, user_message(e, "Aktion nicht möglich – Details im Server-Protokoll.")[:250])

    def settings():
        from observation_settings import load_settings
        return load_settings()

    @r.get("/bootstrap")
    def bootstrap():
        try:
            return dict(preferences=_read_json(ctl.prefs, DEFAULT_PREFS) or DEFAULT_PREFS, settings=settings(),
                        state=ctl.snapshot(), runs=list_runs(ctl.results), engine=mod.health, home=str(mod.home))
        except (OSError, ValueError) as e:
            bad(e)

    @r.get("/state")
    def state():
        return ctl.snapshot()

    @r.get("/runs")
    def runs():
        return list_runs(ctl.results)

    @r.get("/statistics")
    def statistics():
        from .stats import collect
        s = ctl.snapshot(logs=False)
        return {k: v for k, v in collect(ctl.results, skip=s["run"] if s["running"] else None).items() if not k.startswith("_")}

    @r.get("/statistics/satellites")
    def statistics_satellites():
        return mod.satellite_stats()

    @r.get("/achievements")
    def achievements():
        return mod.ach.overview()

    @r.post("/achievements/seen")
    def achievements_seen():
        mod.ach.mark_seen()
        return {"ok": True}

    @r.get("/run")
    def run(id: str):
        try:
            return ctl.run_data(id)
        except (OSError, ValueError, KeyError) as e:
            bad(e)

    @r.get("/run3d")
    def run3d(run: str, session: str | None = None, focus: str | None = None):
        """3D-Lauf-Ansicht: Teleskopweg, Zielobjekt, alle Treffer (`focus` = Kandidat im Fokus)."""
        from .run3d import build_run
        try:
            return build_run(ctl.run_path(run), session, focus, mod.home, mod.ctx.services, scene=lambda p: scene_3d(mod, p))
        except (OSError, ValueError, KeyError, TypeError) as e:
            bad(e)

    @r.get("/orbit3d")
    def orbit3d(run: str, key: str):
        try:
            return ctl.orbit3d(run, key)
        except (OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired) as e:
            bad(e)

    @r.get("/satellite")
    def satellite(run: str, key: str, norad: int):
        """Infokarte eines zugeordneten Satelliten (3D-Erde): Katalog, Bahn, Zuordnung, Helligkeit, Ausschnitt – nur Gespeichertes."""
        from .satdetail import details
        zps = None
        try:
            if "night_zero_points" in mod.ctx.services:
                zps = mod.ctx.services["night_zero_points"]()
        except Exception:
            zps = None
        try:
            return details(ctl.run_path(run), key, norad, mod.home, zps)
        except (OSError, ValueError, KeyError, TypeError) as e:
            bad(e)

    @r.get("/satellite/crop")
    def satellite_crop(run: str, key: str):
        """Bildausschnitt der Spur (JPEG, Zwischenspeicher im Kandidatenordner unter festem Namen)."""
        from .satdetail import crop_jpeg, find
        try:
            path = ctl.run_path(run)
            folder, c, _ = find(path, key)
            data = crop_jpeg(path, folder, c)
        except (OSError, ValueError, KeyError, TypeError) as e:
            bad(e)
        if not data:
            raise HTTPException(404, "Für diese Spur ist kein Bildausschnitt gespeichert")
        return Response(data, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=3600", "X-Content-Type-Options": "nosniff"})

    @r.get("/sitefit")
    def sitefit(run: str, folder: str):
        try:
            return ctl.sitefit(run, folder)
        except (OSError, ValueError, KeyError, TypeError, ArithmeticError) as e:
            bad(e)

    # ---- Neu abgleichen nach Standortänderung / Standortsuche (rematch.py) ----
    @r.get("/rematch")
    def rematch_state():
        return {"job": mod.sitejobs.snapshot(), "sites_changed": mod.sitejobs.sites_changed(), "signature": mod.sitejobs.signature()}

    @r.post("/rematch")
    def rematch(body: dict = Body(...)):
        """{run} = ein Lauf (alle Spuren), {runs: [...]} = diese Läufe, {all: true} = alle Läufe (nur Spuren mit geändertem Ort; `force` = alle)."""
        try:
            if body.get("all"):
                return mod.sitejobs.start_rematch(None, body.get("force"))
            if isinstance(body.get("runs"), list) and body["runs"] and all(isinstance(x, str) for x in body["runs"]):
                return mod.sitejobs.start_rematch(list(dict.fromkeys(body["runs"])), body.get("force"))     # z. B. nach „übernehmen“
            if not isinstance(body.get("run"), str):
                raise ValueError("Lauf fehlt")
            return mod.sitejobs.start_rematch([body["run"]], body.get("force"))
        except (OSError, ValueError) as e:
            bad(e)

    @r.post("/rematch/stop")
    def rematch_stop():
        return {"job": mod.sitejobs.stop()}

    @r.post("/sitesearch/nights")
    def nights_start(body: dict = Body(default={})):
        """Standorte für alle Nächte suchen (Hintergrundauftrag; Ergebnis bleibt in standortsuche-naechte.json)."""
        try:
            g = body.get("global_km")
            return mod.sitejobs.start_nights(float(body.get("radius_km") or 10), body.get("clock") is not False,
                                             float(1500 if g is None else g or 0))
        except (OSError, ValueError, TypeError) as e:
            bad(e)

    @r.get("/sitesearch/nights")
    def nights_state():
        job = mod.sitejobs.snapshot()
        from .rematch import with_alternatives
        sites = (mod.ctx.store.get("sites") or {}).get("list") or []
        return {"job": job if job and job.get("kind") == "nights" else None, "result": with_alternatives(mod.sitejobs.nights_result(), sites),
                "maps": mod.sitejobs.heat_nights()}

    @r.post("/sitesearch/nights/heatmap")
    def nights_heatmap_start(body: dict = Body(...)):
        """Heatmap der Standortsuche für eine Nacht berechnen (Hintergrundauftrag, Ergebnis in standortsuche-karten/<Nacht>.json)."""
        try:
            return mod.sitejobs.start_heatmap(str(body.get("night") or ""), float(body.get("global_km") or 1500), body.get("clock") is not False)
        except (OSError, ValueError, TypeError) as e:
            bad(e)

    @r.get("/sitesearch/nights/heatmap")
    def nights_heatmap(night: str):
        try:
            job = mod.sitejobs.snapshot()
            return {"heat": mod.sitejobs.heat(night), "job": job if job and job.get("kind") == "heatmap" and job.get("night") == night else None}
        except ValueError as e:
            bad(e)

    @r.get("/sitesearch/nights/heatmaps")
    def nights_heatmaps(nights: str = ""):
        """Mehrere Heatmaps auf einmal (Erd-Ansicht, „Alle Nächte“); leer = alle gespeicherten, höchstens 120."""
        try:
            want = [n for n in nights.split(",") if n] or mod.sitejobs.heat_nights()
            if len(want) > 120:
                raise ValueError("Höchstens 120 Nächte auf einmal.")
            return {"heat": {n: mod.sitejobs.heat(n) for n in want}}
        except ValueError as e:
            bad(e)

    @r.post("/sitesearch/nights/applied")
    def nights_applied(body: dict = Body(...)):
        try:
            nights = body.get("nights")
            if not isinstance(nights, list) or not all(isinstance(n, str) for n in nights):
                raise ValueError("Nächte fehlen")
            return {"result": mod.sitejobs.mark_applied(nights)}
        except (OSError, ValueError) as e:
            bad(e)

    @r.post("/sitesearch")
    def sitesearch_start(body: dict = Body(...)):
        try:
            return mod.sitejobs.start_search(str(body.get("run") or ""), str(body.get("folder") or ""))
        except (OSError, ValueError) as e:
            bad(e)

    @r.get("/sitesearch")
    def sitesearch(run: str, folder: str):
        try:
            job = mod.sitejobs.snapshot()
            mine = job if job and job.get("kind") == "search" and job["runs"] == [run] and job.get("folder") == folder else None
            return {"job": mine, "result": mod.sitejobs.search_result(run, folder)}
        except (OSError, ValueError) as e:
            bad(e)

    @r.delete("/run")
    def delete_run(id: str):
        try:
            return ctl.delete_run(id)
        except (OSError, ValueError) as e:
            bad(e)

    @r.get("/observations")
    def observations(source: str = DEFAULT_SOURCE):
        path = Path(source).resolve()
        if not path.is_dir():
            raise HTTPException(400, "Ordner nicht gefunden.")
        names = sorted(p.name for p in path.iterdir() if p.is_dir() and not p.name.startswith("."))
        return dict(source=str(path), observations=names, history=ctl.observation_history(path, names))

    @r.get("/settings")
    def get_settings():
        return settings()

    @r.post("/settings")
    def set_settings(body: dict = Body(...)):
        from observation_settings import update_settings
        try:
            return update_settings(body)
        except (ValueError, TypeError, KeyError) as e:
            bad(e)

    @r.get("/engine")
    def engine():
        return {**mod.health, "home": str(mod.home), "python": mod.python, "configured": mod.ctx.store.get("skytrace")}

    @r.post("/engine")
    def set_engine(request: Request, body: dict = Body(...)):
        """Datenordner/Interpreter der Engine. Der Datenordner wirkt nach einem Neustart."""
        cur = mod.ctx.store.get("skytrace")
        if any(k in body and str(body[k] or "").strip() != (cur.get(k) or "") for k in ("home", "python")):
            security.require_local(request, "Programmpfade setzen")
        vals = {}
        for k in ("home", "python"):
            if k in body:
                v = str(body[k] or "").strip()
                if v and k == "home" and not Path(v).is_dir():
                    raise HTTPException(400, f"Ordner nicht gefunden: {v}")
                if v and k == "python" and not Path(v).is_file():
                    raise HTTPException(400, f"Python nicht gefunden: {v}")
                vals[k] = v or None
        if "auto" in body:                                      # gestackte Aufnahmen automatisch analysieren
            vals["auto"] = bool(body["auto"])
        mod.ctx.store.update("skytrace", vals)
        if "home" in vals or "python" in vals:
            threading.Thread(target=mod.check_engine, daemon=True).start()
        return {"saved": True, "restart": "home" in vals}

    @r.get("/catalog/status")
    def catalog_status():
        cache = mod.home / "catalog_cache" / "archives"
        files = list(cache.glob("*")) if cache.exists() else []
        # Namensabfrage CelesTrak-SATCAT: angehalten (`stopped`, z. B. HTTP 403) bzw. kurz pausiert – engine/archive_sources.py
        satcat = _read_json(cache / "satcat-pause.json", {}) or {}
        if not satcat.get("stopped") and float(satcat.get("until") or 0) <= time.time():
            satcat = {}
        csv = cache / "satcat.csv"                  # lokale Kopie des Namenskatalogs (täglich bei Bedarf erneuert)
        satcat_file = dict(updated=csv.stat().st_mtime, bytes=csv.stat().st_size) if csv.is_file() else None
        return dict(satcat=satcat, satcat_file=satcat_file, satellite_time_queries=sum(p.name.startswith("eth-") and p.suffix == ".json" for p in files),
                    satellite_years=[p.stem.removeprefix("tle_") for p in files if p.suffix == ".parquet"],
                    meteor_months=[p.stem.removeprefix("traj_summary_monthly_") for p in files
                                   if p.suffix == ".sqlite" and ".building." not in p.name],
                    bytes=sum(p.stat().st_size for p in files if p.is_file()))

    @r.post("/catalog/satcat/release")
    def satcat_release():
        """Angehaltene Satelliten-Namensabfrage wieder freigeben (CelesTrak verlangt bei Fehlern eine Prüfung durch einen Menschen)."""
        (mod.home / "catalog_cache" / "archives" / "satcat-pause.json").unlink(missing_ok=True)
        return catalog_status()["satcat"]

    @r.get("/geocode")
    def geocode(q: str = ""):
        import httpx
        q = q.strip()
        if len(q) < 2 or len(q) > 250:
            raise HTTPException(400, "Bitte einen Ort oder eine Adresse eingeben.")
        try:
            data = httpx.get("https://photon.komoot.io/api/", params={"q": q, "limit": 5, "lang": "de"}, timeout=15,
                             headers={"User-Agent": "AstroFlow-SkyTrace"}).json()
        except Exception:
            traceback.print_exc()
            raise HTTPException(502, "Adresssuche nicht erreichbar.") from None
        rows = []
        for feature in data.get("features", []):
            p = feature["properties"]
            lon, lat = feature["geometry"]["coordinates"]
            label = ", ".join(str(p[k]) for k in ("name", "street", "housenumber", "postcode", "city", "country") if p.get(k))
            rows.append(dict(name=label, lat=lat, lon=lon, alt_m=0, source="Adresse via Photon/OpenStreetMap"))
        return rows

    @r.post("/start")
    def start(body: dict = Body(...)):
        try:
            return ctl.launch(body)
        except (ValueError, OSError) as e:
            bad(e)

    @r.post("/refresh")
    def refresh(body: dict = Body(...)):
        try:
            return ctl.launch(body, refresh=True)
        except (ValueError, OSError) as e:
            bad(e)

    @r.post('/reanalyse')
    def reanalyse(body: dict = Body(...)):
        from .reanalysis import prepare
        try:
            with ctl.lock:
                if ctl.process is not None:
                    raise ValueError('Eine Analyse läuft bereits.')
                plan = prepare(ctl.run_path(body.get('run')), body.get('source'))
                if body.get('check'):
                    return dict(missing=plan['missing'], total=plan['total'], observations=len(plan['groups']))
                if plan['missing']:
                    raise ValueError(f"{plan['missing']} von {plan['total']} Originalaufnahmen fehlen. Bitte ihren Originalordner auswählen; die Analyse wurde nicht gestartet.")
                return ctl.launch(dict(source=str(Path(plan['groups'][0]['files'][0]).parent), samples=0,
                                       mode=MODES[0], fps=plan['groups'][0]['fps'], solve=True, remember=False), replay=plan)
        except (ValueError, OSError, KeyError, TypeError) as e:
            bad(e)

    @r.post("/stop")
    def stop():
        return ctl.stop()

    @r.get("/media/{path:path}")
    def media(path: str, request: Request):
        try:
            p = within(ctl.results, path)
        except ValueError as e:
            bad(e)
        if p.suffix.lower() not in MEDIA_TYPES:            # nur Vorschauen, Videos und Berichte – keine internen Lauf-Dateien
            raise HTTPException(404, "Datei nicht verfügbar")
        if not p.is_file():
            raise HTTPException(404, "Datei noch nicht verfügbar")
        headers = {"Cache-Control": "no-cache", "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer"}
        if p.suffix == ".html":
            headers["Content-Security-Policy"] = ("default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
                                                  "img-src 'self' data:; media-src 'self'; connect-src 'self'; frame-ancestors 'self'")
        if p.stat().st_size <= 8 * 1024 * 1024 and not request.headers.get("range"):
            # kleine Vorschauen sofort lesen: Windows-Dateihandle nicht offen halten, während die Engine schreibt
            from json_store import open_read
            import mimetypes
            with open_read(p) as f:
                data = f.read()
            ctype = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
            return Response(data, media_type=ctype, headers=headers)
        return FileResponse(p, headers=headers)                # mit Range-Unterstützung für Videos

    return r
