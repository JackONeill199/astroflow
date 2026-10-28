"""FastAPI-Server: REST-API, Job-Worker, Ordner-Watcher und Web-UI."""
from __future__ import annotations

import asyncio
import json
import queue
import re
import shutil
import threading
import time
import traceback
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Body, FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import channels, objects, platesolve, reviewer, stars
from .config import Settings, load_settings
from .db import DB
from .pipeline import Pipeline

STATIC = Path(__file__).parent / "static"
# In der Web-App geänderte Einstellungen überleben einen Neustart (Vorrang vor Umgebungsvariablen)
PERSIST = {"reviewer": ("provider", "base_url", "model", "api_key", "max_rounds"),
           "solver": ("backend", "astap", "astap_db", "nova_key", "timeout"),
           "storage": ("delete_uploads", "gaia"),
           "starnet": ("path",)}


class App:
    def __init__(self, settings: Settings):
        self.s = settings
        self.db = DB(settings.db_path)
        self.review_cfg = reviewer.ReviewerConfig.from_env()
        self.pl = Pipeline(settings, self.db, self.review_cfg)
        self.settings_file = settings.data_dir / "settings.json"
        stars.STARNET.tools_dir = settings.data_dir / "tools" / "starnet"
        self._apply_saved()
        self.q: queue.Queue[int] = queue.Queue()
        self.stop = threading.Event()
        self.last_scan = 0.0
        # unterbrochene Jobs fortsetzen: das Stacking macht am letzten Checkpoint weiter
        self.db.x("UPDATE jobs SET state='queued', message='wird fortgesetzt (Neustart)' WHERE state='running'")
        for j in self.db.q("SELECT id FROM jobs WHERE state='queued' ORDER BY id"):
            self.q.put(j["id"])

    # ---- gespeicherte Einstellungen -------------------------------------------
    def _targets(self) -> dict:
        return {"reviewer": self.review_cfg, "solver": self.pl.solver_cfg, "storage": self.s, "starnet": stars.STARNET}

    def _apply_saved(self):
        try:
            saved = json.loads(self.settings_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        for sec, obj in self._targets().items():
            for k, v in (saved.get(sec) or {}).items():
                if k in PERSIST[sec]:
                    setattr(obj, k, v)

    def save_settings(self):
        data = {sec: {k: getattr(obj, k) for k in PERSIST[sec]} for sec, obj in self._targets().items()}
        tmp = self.settings_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.settings_file)

    # ---- Jobs ----------------------------------------------------------------
    def enqueue(self, project_id: int, options: dict | None = None) -> int:
        busy = self.db.one("SELECT id FROM jobs WHERE project_id=? AND state IN ('queued','running')", (project_id,))
        if busy:
            return busy["id"]
        jid = self.db.x("INSERT INTO jobs(project_id,state,created,options) VALUES(?,?,?,?)",
                        (project_id, "queued", time.time(), json.dumps(options or {})))
        self.q.put(jid)
        return jid

    def worker(self):
        while not self.stop.is_set():
            try:
                jid = self.q.get(timeout=0.5)
            except queue.Empty:
                continue
            job = self.db.one("SELECT * FROM jobs WHERE id=?", (jid,))
            self.db.x("UPDATE jobs SET state='running', started=? WHERE id=?", (time.time(), jid))

            def prog(p, stage, msg=""):
                self.db.x("UPDATE jobs SET progress=?, stage=?, message=? WHERE id=?", (round(p, 3), stage, msg, jid))

            try:
                opts = json.loads(job["options"] or "{}")
                if opts.get("action") == "solve":
                    r = self.pl.solve_project(job["project_id"], prog)
                    n = sum(r["moved"].values())
                    self.db.x("UPDATE jobs SET state='done', progress=1, finished=?, message=? WHERE id=?",
                              (time.time(), f"{n} Aufnahmen zugeordnet", jid))
                    for pid in r["moved"]:
                        self.enqueue(pid)
                    continue
                self.pl.process(job["project_id"], opts, prog)
                self.db.x("UPDATE jobs SET state='done', progress=1, finished=? WHERE id=?", (time.time(), jid))
            except Exception as e:
                traceback.print_exc()
                self.db.x("UPDATE jobs SET state='error', message=?, finished=? WHERE id=?", (str(e), time.time(), jid))

    def watcher(self):
        while not self.stop.is_set():
            try:
                self.scan_and_queue()
            except Exception:
                traceback.print_exc()
            self.stop.wait(self.s.scan_interval)

    def scan_and_queue(self) -> dict:
        r = self.pl.scan()
        self.last_scan = time.time()
        if self.s.auto:
            for pid in r["new_by_project"]:
                self.enqueue(pid)
        return r


def create_app(settings: Settings | None = None) -> FastAPI:
    core = App(settings or load_settings())

    @asynccontextmanager
    async def lifespan(app):
        threading.Thread(target=core.worker, daemon=True).start()
        threading.Thread(target=core.watcher, daemon=True).start()
        yield
        core.stop.set()

    app = FastAPI(title="AstroFlow", version="0.4.0", lifespan=lifespan)
    db = core.db

    def project_view(p: dict) -> dict:
        fr = db.q("SELECT status, COUNT(*) n FROM frames WHERE project_id=? AND kind='light' GROUP BY status", (p["id"],))
        counts = {r["status"]: r["n"] for r in fr}
        last = db.one("SELECT * FROM results WHERE project_id=? ORDER BY id DESC LIMIT 1", (p["id"],))
        job = db.one("SELECT * FROM jobs WHERE project_id=? ORDER BY id DESC LIMIT 1", (p["id"],))
        nights = db.q("SELECT DISTINCT night FROM frames WHERE project_id=? AND kind='light' ORDER BY night", (p["id"],))
        n_results = db.one("SELECT COUNT(*) n FROM results WHERE project_id=?", (p["id"],))["n"]
        sv = db.one("SELECT json_extract(info,'$.solve') s FROM frames WHERE project_id=? AND json_extract(info,'$.solve') IS NOT NULL LIMIT 1", (p["id"],))
        dl = db.one("SELECT COUNT(*) n, COALESCE(SUM(size),0) b, COALESCE(SUM(stacked),0) s FROM frames "
                    "WHERE project_id=? AND kind='light' AND deleted=1", (p["id"],))
        coords = None
        if p["ra"] is not None:
            coords = {"ra": p["ra"], "dec": p["dec"], "source": "solve" if sv else ("catalog" if p["obj_id"] else "header")}
            if sv:
                coords["solve"] = json.loads(sv["s"])
        return {
            "id": p["id"], "name": p["name"], "kind": p["kind"], "kind_label": objects.KIND_LABEL.get(p["kind"] or "generic"),
            "options": json.loads(p["options"] or "{}"), "frames": counts, "total": sum(counts.values()),
            "nights": [n["night"] for n in nights], "n_results": n_results, "created": p["created"],
            "coords": coords, "can_solve": not p["obj_id"],
            # Einzelbilder nach dem Stacking gelöscht -> kein komplettes Neu-Stacken mehr möglich
            "deleted": {"frames": dl["n"], "bytes": dl["b"], "can_restack": dl["s"] == 0},
            "result": {"id": last["id"], "created": last["created"], "n_frames": last["n_frames"], "exposure": last["exposure"],
                       "stats": json.loads(last["stats"])} if last else None,
            "job": {k: job[k] for k in ("id", "state", "progress", "stage", "message", "finished")} if job else None,
        }

    @app.get("/api/status")
    def status():
        frames = {r["kind"]: r["n"] for r in db.q("SELECT kind, COUNT(*) n FROM frames GROUP BY kind")}
        busy = db.one("SELECT COUNT(*) n FROM jobs WHERE state IN ('queued','running')")["n"]
        try:
            du = shutil.disk_usage(core.s.data_dir)
            disk = {"free": du.free, "total": du.total}
        except OSError:
            disk = None
        return {"version": "0.4.0", "input_dir": str(core.s.input_dir), "output_dir": str(core.s.output_dir),
                "auto": core.s.auto, "scan_interval": core.s.scan_interval, "threads": core.s.threads,
                "last_scan": core.last_scan, "frames": frames, "busy_jobs": busy, "disk": disk,
                "delete_uploads": core.s.delete_uploads, "gaia": core.s.gaia,
                "reviewer": {"enabled": core.review_cfg.enabled, "provider": core.review_cfg.provider, "model": core.review_cfg.model},
                "solver": {"enabled": core.pl.solver_cfg.enabled, "active": core.pl.solver_cfg.chain()}}

    @app.post("/api/storage")
    def set_storage(body: dict = Body(...)):
        for k in ("delete_uploads", "gaia"):
            if k in body:
                setattr(core.s, k, bool(body[k]))
        core.save_settings()
        return {"delete_uploads": core.s.delete_uploads, "gaia": core.s.gaia}

    @app.get("/api/solver")
    def get_solver():
        return core.pl.solver_cfg.status()

    @app.post("/api/solver")
    def set_solver(body: dict = Body(...)):
        c = core.pl.solver_cfg
        if body.get("backend") in platesolve.BACKENDS:
            c.backend = body["backend"]
        for k in ("astap", "astap_db"):
            if k in body:
                setattr(c, k, str(body[k]).strip())
        if body.get("nova_key"):
            c.nova_key = str(body["nova_key"]).strip()
        if "timeout" in body:
            c.timeout = max(20, min(900, int(body["timeout"])))
        # fehlgeschlagene Lösungsversuche verwerfen, damit der nächste Scan es erneut versucht
        db.x("DELETE FROM solves WHERE status='failed'")
        core.save_settings()
        return get_solver()

    @app.post("/api/projects/{pid}/solve")
    def solve(pid: int):
        if not db.one("SELECT id FROM projects WHERE id=?", (pid,)):
            raise HTTPException(404)
        if not core.pl.solver_cfg.enabled:
            raise HTTPException(400, "Kein Plate-Solver eingerichtet – siehe Einstellungen.")
        return {"job": core.enqueue(pid, {"action": "solve"})}

    @app.post("/api/scan")
    def scan():
        return core.scan_and_queue()

    @app.get("/api/projects")
    def projects():
        return [project_view(p) for p in db.q("SELECT * FROM projects ORDER BY name")]

    @app.get("/api/projects/{pid}")
    def project(pid: int):
        p = db.one("SELECT * FROM projects WHERE id=?", (pid,))
        if not p:
            raise HTTPException(404)
        v = project_view(p)
        v["results"] = [{"id": r["id"], "created": r["created"], "n_frames": r["n_frames"], "exposure": r["exposure"],
                         "stats": json.loads(r["stats"]), "review": json.loads(r["review"] or "[]"),
                         "has_before": "before" in json.loads(r["files"] or "{}")}
                        for r in db.q("SELECT * FROM results WHERE project_id=? ORDER BY id DESC", (pid,))]
        v["profiles"] = list(objects.PROFILES)
        v["palettes"] = list(channels.PALETTES)
        v["kind_labels"] = objects.KIND_LABEL
        return v

    @app.patch("/api/projects/{pid}")
    def patch_project(pid: int, body: dict = Body(...)):
        p = db.one("SELECT * FROM projects WHERE id=?", (pid,))
        if not p:
            raise HTTPException(404)
        opts = json.loads(p["options"] or "{}")
        if "name" in body:
            db.x("UPDATE projects SET name=? WHERE id=?", (str(body["name"])[:80], pid))
        if "options" in body:
            opts.update(body["options"])
            db.x("UPDATE projects SET options=? WHERE id=?", (json.dumps(opts), pid))
        return project_view(db.one("SELECT * FROM projects WHERE id=?", (pid,)))

    @app.delete("/api/projects/{pid}")
    def delete_project(pid: int):
        if not db.one("SELECT id FROM projects WHERE id=?", (pid,)):
            raise HTTPException(404)
        if db.one("SELECT id FROM jobs WHERE project_id=? AND state IN ('queued','running')", (pid,)):
            raise HTTPException(409, "Das Objekt wird gerade verarbeitet – bitte warten, bis der Durchlauf fertig ist.")
        with core.pl._scan_lock:                         # kein gleichzeitiger Scan, der Frames neu zuordnet
            return core.pl.delete_project(pid)

    @app.post("/api/projects/{pid}/process")
    def process(pid: int, body: dict = Body(default={})):
        if not db.one("SELECT id FROM projects WHERE id=?", (pid,)):
            raise HTTPException(404)
        if body.get("restack"):
            if db.one("SELECT id FROM frames WHERE project_id=? AND deleted=1 AND stacked=1 LIMIT 1", (pid,)):
                raise HTTPException(409, "Komplett neu stacken ist nicht möglich: die hochgeladenen Einzelbilder wurden "
                                         "nach dem Stacking gelöscht. Neue Aufnahmen werden weiterhin ergänzt.")
            db.x("UPDATE projects SET restack=1 WHERE id=?", (pid,))
        return {"job": core.enqueue(pid, {k: v for k, v in body.items() if k != "restack"})}

    @app.get("/api/projects/{pid}/frames")
    def frames(pid: int):
        out = []
        for r in db.q("SELECT * FROM frames WHERE project_id=? AND kind='light' ORDER BY json_extract(info,'$.date_obs'), path", (pid,)):
            m = json.loads(r["metrics"]) if r["metrics"] else {}
            i = json.loads(r["info"])
            out.append({"id": r["id"], "name": Path(r["path"]).name, "night": r["night"], "status": r["status"], "reason": r["reason"],
                        "band": channels.band_of(i.get("filter"), channels.is_color(i)), "filter": i.get("filter"),
                        "exptime": i.get("exptime"), "date": i.get("date_obs"),
                        "score": round(r["score"] or 0, 3), "manual": r["manual"], "stacked": r["stacked"], "deleted": r["deleted"],
                        "n_stars": m.get("n_stars"), "fwhm": m.get("fwhm") and round(m["fwhm"], 2),
                        "ecc": m.get("ecc") and round(m["ecc"], 2), "bg": m.get("bg") and round(m["bg"], 1)})
        return out

    @app.patch("/api/frames/{fid}")
    def patch_frame(fid: str, body: dict = Body(...)):
        f = db.one("SELECT * FROM frames WHERE id=?", (fid,))
        if not f:
            raise HTTPException(404)
        if f["deleted"]:
            raise HTTPException(409, "Die Rohdatei wurde nach dem Stacking gelöscht – Ein-/Ausschließen ist nicht mehr möglich.")
        manual = int(body.get("manual", 0))
        db.set_frame(fid, manual=manual)
        if manual == -1 and f["stacked"]:
            db.x("UPDATE projects SET restack=1 WHERE id=?", (f["project_id"],))
        return {"ok": True, "restack_needed": manual == -1 and bool(f["stacked"])}

    @app.get("/api/jobs")
    def jobs():
        return db.q("SELECT j.*, p.name project FROM jobs j LEFT JOIN projects p ON p.id=j.project_id ORDER BY j.id DESC LIMIT 50")

    @app.post("/api/upload")
    async def upload(files: list[UploadFile] = File(...)):
        dest = core.s.input_dir / "uploads" / time.strftime("%Y%m%d")
        dest.mkdir(parents=True, exist_ok=True)
        n = 0
        for f in files:
            # relative Ordnerpfade behalten (z. B. „…_observation_M57/img-0001r.fits“ – der Ordner verrät das Objekt),
            # aber nur harmlose Pfadteile zulassen
            parts = [p for p in re.split(r"[\\/]+", f.filename or "") if p not in ("", ".", "..") and ":" not in p]
            if not parts or not parts[-1].lower().endswith((".fit", ".fits", ".fts")):
                continue
            target = dest.joinpath(*parts[-4:])
            target.parent.mkdir(parents=True, exist_ok=True)
            with open(target, "wb") as out:
                shutil.copyfileobj(f.file, out)
            n += 1
        return {"saved": n}

    @app.get("/api/results/{rid}/{kind}")
    def result_file(rid: int, kind: str):
        r = db.one("SELECT files FROM results WHERE id=?", (rid,))
        files = json.loads(r["files"]) if r else {}
        if kind not in files or not Path(files[kind]).exists():
            raise HTTPException(404)
        return FileResponse(files[kind], filename=Path(files[kind]).name if kind in ("tif", "fits", "png") else None)

    @app.get("/api/starnet")
    def get_starnet():
        exe = stars.starnet_path()
        return {"path": exe, "found": bool(exe), "custom": stars.STARNET.path,
                "installed_here": bool(exe and stars.STARNET.tools_dir and str(stars.STARNET.tools_dir) in exe),
                "page": stars.STARNET_PAGE}

    @app.post("/api/starnet")
    def set_starnet(body: dict = Body(...)):
        p = str(body.get("path") or "").strip()
        if p and not Path(p).is_file():
            raise HTTPException(400, f"Datei nicht gefunden: {p}")
        stars.STARNET.path = p
        core.save_settings()
        return get_starnet()

    @app.post("/api/starnet/install")
    async def install_starnet(file: UploadFile = File(...)):
        if not (file.filename or "").lower().endswith(".zip"):
            raise HTTPException(400, "Bitte die heruntergeladene ZIP-Datei der StarNet-Kommandozeilenversion wählen.")
        tmp = core.s.data_dir / "tools" / "starnet_upload.zip"
        tmp.parent.mkdir(parents=True, exist_ok=True)
        with open(tmp, "wb") as f:
            while chunk := await file.read(1 << 20):
                f.write(chunk)
        try:
            exe = await asyncio.to_thread(stars.install_zip, tmp, stars.STARNET.tools_dir)
        except Exception as e:
            raise HTTPException(400, f"Installation fehlgeschlagen: {e}")
        finally:
            tmp.unlink(missing_ok=True)
        return {**get_starnet(), "installed": exe}

    @app.delete("/api/starnet")
    def remove_starnet():
        shutil.rmtree(stars.STARNET.tools_dir, ignore_errors=True)
        stars.STARNET.path = ""
        core.save_settings()
        return get_starnet()

    @app.get("/api/reviewer")
    def get_reviewer():
        c = core.review_cfg
        return {"provider": c.provider, "base_url": c.base_url, "model": c.model, "max_rounds": c.max_rounds, "has_key": bool(c.api_key)}

    @app.post("/api/reviewer")
    def set_reviewer(body: dict = Body(...)):
        c = core.review_cfg
        for k in ("provider", "base_url", "model", "api_key"):
            if k in body and (k != "api_key" or body[k]):
                setattr(c, k, str(body[k]))
        if "max_rounds" in body:
            c.max_rounds = max(1, min(6, int(body["max_rounds"])))
        core.save_settings()
        return get_reviewer()

    @app.middleware("http")
    async def no_cache_index(request, call_next):
        r = await call_next(request)
        # Startseite immer neu prüfen – sonst hängt der Browser an alten ?v=-Verweisen auf app.js/style.css
        if request.url.path in ("/", "/index.html"):
            r.headers["Cache-Control"] = "no-cache"
        return r

    app.mount("/", StaticFiles(directory=STATIC, html=True), name="ui")
    return app
