"""Stacking-Modul: Job-Worker, Ordner-Watcher und REST-API (unter /api/stacking)."""
from __future__ import annotations

import asyncio
import json
import queue
import re
import shutil
import threading
import time
import traceback
from pathlib import Path

from fastapi import APIRouter, Body, File, HTTPException, UploadFile
from fastapi.responses import FileResponse

from . import calibration, channels, diskspace, gpu, graxpert, objects, platesolve, reviewer, stars, tonight, tutor
from .db import DB
from .pipeline import Pipeline

# In der Web-App geänderte Einstellungen überleben einen Neustart (Vorrang vor Umgebungsvariablen)
PERSIST = {"reviewer": ("provider", "base_url", "model", "api_key", "max_rounds"),
           "solver": ("backend", "astap", "astap_db", "nova_key", "timeout"),
           "storage": ("delete_uploads", "gaia"),
           "starnet": ("path",),
           "graxpert": ("path", "mode", "gpu"),
           "gpu": ("mode",)}


class Engine:
    def __init__(self, ctx):
        self.ctx = ctx
        self.s = ctx.settings
        self.db = DB(self.s.db_path)
        self.review_cfg = reviewer.ReviewerConfig.from_env()
        self.pl = Pipeline(self.s, self.db, self.review_cfg)
        from .library import Library
        self.library = Library(self)
        stars.STARNET.tools_dir = self.s.data_dir / "tools" / "starnet"
        self._apply_saved()
        self.q: queue.Queue[int] = queue.Queue()
        self.dq: queue.Queue[int] = queue.Queue()      # gestackt, wartet auf die Endbearbeitung
        self._queue_lock = threading.Lock()
        self.stop = threading.Event()
        self.cancel = threading.Event()               # laufenden Auftrag beim nächsten Fortschritt abbrechen
        self.paused: str | None = None                # Grund, falls die Warteschlange angehalten ist
        self.last_scan = 0.0
        # unterbrochene Jobs fortsetzen: das Stacking macht am letzten Checkpoint weiter
        self.db.x("UPDATE jobs SET state='queued', message='wird fortgesetzt (Neustart)' WHERE state='running'")
        for j in self.db.q("SELECT id FROM jobs WHERE state='queued' ORDER BY id"):
            self.q.put(j["id"])

    # ---- gespeicherte Einstellungen -------------------------------------------
    def _targets(self) -> dict:
        return {"reviewer": self.review_cfg, "solver": self.pl.solver_cfg, "storage": self.s, "starnet": stars.STARNET,
                "graxpert": graxpert.GRAXPERT, "gpu": gpu.GPU}

    def _apply_saved(self):
        for sec, obj in self._targets().items():
            for k, v in self.ctx.store.get(sec).items():
                if k in PERSIST[sec]:
                    setattr(obj, k, v)

    def save_settings(self):
        for sec, obj in self._targets().items():
            self.ctx.store.update(sec, {k: getattr(obj, k) for k in PERSIST[sec]})

    # ---- Jobs ----------------------------------------------------------------
    def enqueue(self, project_id: int, options: dict | None = None) -> int:
        with self._queue_lock:
            busy = self.db.one("SELECT id FROM jobs WHERE project_id=? AND state IN ('queued','running')", (project_id,))
            if busy:
                return busy["id"]
            jid = self.db.x("INSERT INTO jobs(project_id,state,created,options) VALUES(?,?,?,?)",
                            (project_id, "queued", time.time(), json.dumps(options or {})))
            self.q.put(jid)
            return jid

    def pending_projects(self) -> set[int]:
        """Während laufender Jobs eingelesene Aufnahmen nachholen; Fehler nicht endlos neu versuchen. Nicht erreichbare
        Quelldateien (abgesteckte Platte) zählen nicht – sonst liefe jede Scan-Runde ein leerer Auftrag; sobald die
        Platte wieder da ist, holt der Watcher sie nach. Ein Auftrag, der an einer nicht erreichbaren Quelle scheiterte
        (`options.offline` = fehlende Ordner), gilt erst dann wieder als offen, wenn diese Ordner zurück sind."""
        out = set()
        for r in self.db.q(
                "SELECT f.project_id, f.path, j.state, j.options FROM frames f LEFT JOIN jobs j ON j.id=(SELECT MAX(j2.id) "
                "FROM jobs j2 WHERE j2.project_id=f.project_id) WHERE f.kind='light' AND f.status='new' AND f.deleted=0 "
                "AND f.project_id IS NOT NULL"):
            if r["project_id"] in out or (r["path"] and not Path(r["path"]).exists()):
                continue
            if r["state"] == "error":
                dirs = json.loads(r["options"] or "{}").get("offline")
                if not dirs or not all(Path(d).exists() for d in dirs):
                    continue
            out.add(r["project_id"])
        return out

    def hold(self) -> str | None:
        """Warum gerade keine Aufträge starten (angehalten oder zu wenig Speicher) – sonst None."""
        if self.paused:
            return self.paused
        f = diskspace.free(self.s.data_dir)
        if f is not None and f < diskspace.MIN_FREE:
            return (f"Nur noch {diskspace.gb(f)} frei auf dem Datenlaufwerk – Aufträge warten, bis wieder "
                    f"mindestens {diskspace.gb(diskspace.MIN_FREE)} frei sind.")
        return None

    def set_paused(self, paused: bool, reason: str = "Warteschlange angehalten.") -> None:
        self.paused = reason if paused else None
        if paused:
            self.cancel.set()
        else:
            self.cancel.clear()

    def worker(self):
        """Stack-Thread. Ist ein Objekt gestackt, übernimmt der Entwicklungs-Thread (`developer`) die Endbearbeitung,
        und hier beginnt sofort das Stacken des nächsten Objekts. Höchstens ein fertiger Stack wartet auf die Entwicklung."""
        stacked_all = threading.Event()               # Entwicklungs-Thread endet erst, wenn hier nichts mehr nachkommt
        dev = threading.Thread(target=self.developer, args=(stacked_all,), daemon=True, name="stacking-develop")
        dev.start()
        while not self.stop.is_set():
            if self.hold() or self.dq.qsize() >= 1:
                self.stop.wait(1 if self.dq.qsize() else 3)
                continue
            try:
                jid = self.q.get(timeout=0.5)
            except queue.Empty:
                continue
            self._guarded(jid, self._stack_job)
        stacked_all.set()
        dev.join()

    def developer(self, stacked_all: threading.Event):
        while not (stacked_all.is_set() and self.dq.empty()):
            try:
                jid = self.dq.get(timeout=0.5)
            except queue.Empty:
                continue
            self._guarded(jid, self._develop_job)

    def _guarded(self, jid: int, fn):
        try:
            fn(jid)
        except Exception as e:                        # z. B. Datenbank bei voller Platte – der Thread muss weiterleben
            traceback.print_exc()
            if diskspace.is_full(e):
                self._full()
                self.q.put(jid)
            self.stop.wait(5)

    def _full(self):
        f = diskspace.free(self.s.data_dir) or 0
        self.set_paused(True, f"Datenlaufwerk voll ({diskspace.gb(f)} frei) – Warteschlange angehalten. "
                              "Platz schaffen (Einstellungen → Bilder → Speicherbelegung) und dann fortsetzen.")

    def _prog(self, jid: int):
        checked = [0.0]

        def prog(p, stage, msg=""):
            if self.cancel.is_set():
                raise InterruptedError("angehalten")
            if time.time() - checked[0] > 5:          # Platte während des Auftrags im Blick behalten
                checked[0] = time.time()
                f = diskspace.free(self.s.data_dir)
                if f is not None and f < diskspace.ABORT_FREE:
                    raise diskspace.DiskFull(f"Nur noch {diskspace.gb(f)} frei")
            self.db.x("UPDATE jobs SET progress=?, stage=?, message=? WHERE id=?", (round(p, 3), stage, msg, jid))
        return prog

    def _stack_job(self, jid: int):
        job = self.db.one("SELECT * FROM jobs WHERE id=?", (jid,))
        if not job or job["state"] not in ("queued", "running"):
            return
        self.db.x("UPDATE jobs SET state='running', started=? WHERE id=?", (time.time(), jid))
        prog = self._prog(jid)
        try:
            opts = json.loads(job["options"] or "{}")
            if opts.get('action') != 'solve':
                self.pl.begin_live(job['project_id'], jid)
            if opts.get("action") == "solve":
                r = self.pl.solve_project(job["project_id"], prog)
                n = sum(r["moved"].values())
                self.db.x("UPDATE jobs SET state='done', progress=1, finished=?, message=? WHERE id=?",
                          (time.time(), f"{n} Aufnahmen zugeordnet", jid))
                for pid in r["moved"]:
                    self.enqueue(pid)
                return
            if opts.get("mode") != "develop":
                self.pl.stack(job["project_id"], opts, prog)
                prog(0.86, "Bearbeitung", "wartet auf die Endbearbeitung")
            self.dq.put(jid)
        except Exception as e:
            self._failed(jid, e)

    def _develop_job(self, jid: int):
        job = self.db.one("SELECT * FROM jobs WHERE id=?", (jid,))
        if not job or job["state"] != "running":
            return
        prog = self._prog(jid)
        try:
            opts = json.loads(job["options"] or "{}")
            if opts.get("mode") == "develop":
                self.pl.develop(job["project_id"], opts, prog, start=0.05)
            else:
                self.pl.finish(job["project_id"], opts, prog)
            self.db.x("UPDATE jobs SET state='done', progress=1, finished=? WHERE id=?", (time.time(), jid))
            if self.s.auto and job["project_id"] in self.pending_projects():
                self.enqueue(job["project_id"])
        except Exception as e:
            self._failed(jid, e)

    def _failed(self, jid: int, e: Exception):
        if isinstance(e, InterruptedError) or isinstance(e, diskspace.DiskFull) or diskspace.is_full(e):
            # nichts verloren: Stacking setzt am letzten Checkpoint fort – Auftrag wartet wieder
            if not isinstance(e, InterruptedError):
                self._full()
            self.db.x("UPDATE jobs SET state='queued', progress=0, message=? WHERE id=?",
                      ("angehalten – geht beim Fortsetzen weiter", jid))
            self.q.put(jid)
            return
        if isinstance(e, calibration.SourceOffline):    # wartet auf die Platte – kein endgültiger Fehler
            self.db.x("UPDATE jobs SET options=json_set(COALESCE(options,'{}'),'$.offline',json(?)) WHERE id=?",
                      (json.dumps(e.dirs), jid))
        else:
            traceback.print_exc()
        self.db.x("UPDATE jobs SET state='error', message=?, finished=? WHERE id=?", (str(e), time.time(), jid))

    def watcher(self):
        while not self.stop.is_set():
            f = diskspace.free(self.s.data_dir)
            if f is not None and f < diskspace.ABORT_FREE:       # volle Platte: nicht jede Runde Fehler produzieren
                self.stop.wait(self.s.scan_interval)
                continue
            try:
                self.scan_and_queue()
            except Exception:
                traceback.print_exc()
            self.stop.wait(self.s.scan_interval)

    def scan_and_queue(self, extra: list[Path] | None = None) -> dict:
        r = self.pl.scan(extra)
        from .picture_import import attach_previews
        attach_previews(self)
        self.last_scan = time.time()
        if self.s.auto:
            for pid in set(r["new_by_project"]) | self.pending_projects():
                self.enqueue(pid)
        return r

    # ---- Beiträge zur gemeinsamen Oberfläche ---------------------------------
    def status(self) -> dict:
        frames = {r["kind"]: r["n"] for r in self.db.q("SELECT kind, COUNT(*) n FROM frames GROUP BY kind")}
        busy = self.db.one("SELECT COUNT(*) n FROM jobs WHERE state IN ('queued','running')")["n"]
        n_proj = self.db.one("SELECT COUNT(*) n FROM projects WHERE json_extract(options,'$.gallery_parent') IS NULL")["n"]
        n_res = self.db.one("SELECT COUNT(DISTINCT r.project_id) n FROM results r JOIN projects p ON p.id=r.project_id "
                            "WHERE json_extract(p.options,'$.gallery_parent') IS NULL")["n"]
        try:
            du = shutil.disk_usage(self.s.data_dir)
            disk = {"free": du.free, "total": du.total}
        except OSError:
            disk = None
        return {"input_dir": str(self.s.input_dir), "output_dir": str(self.s.output_dir),
                "auto": self.s.auto, "scan_interval": self.s.scan_interval, "threads": self.s.threads,
                "last_scan": self.last_scan, "frames": frames, "busy_jobs": busy, "disk": disk,
                "hold": self.hold(), "paused": bool(self.paused),
                "projects": n_proj, "finished": n_res,
                "delete_uploads": self.s.delete_uploads, "gaia": self.s.gaia,
                "reviewer": {"enabled": self.review_cfg.enabled, "provider": self.review_cfg.provider, "model": self.review_cfg.model},
                "solver": {"enabled": self.pl.solver_cfg.enabled, "active": self.pl.solver_cfg.chain()}}

    def activity(self, limit: int = 30) -> list[dict]:
        out = []
        for j in self.db.q("SELECT j.*, p.name project FROM jobs j LEFT JOIN projects p ON p.id=j.project_id "
                           "ORDER BY j.id DESC LIMIT ?", (limit,)):
            opts = json.loads(j["options"] or "{}")
            what = "Plate-Solving" if opts.get("action") == "solve" else "Neu entwickeln" if opts.get("mode") == "develop" else "Verarbeitung"
            out.append({"key": f"stacking:{j['id']}", "id": j["id"], "title": j["project"] or "Objekt", "kind": what,
                        "detail": j["message"] or j["stage"] or "", "stage": j["stage"], "state": j["state"],
                        "progress": j["progress"], "started": j["started"] or j["created"], "finished": j["finished"],
                        "href": f"#/p/{j['project_id']}"})
        return out


def build_router(eng: Engine) -> APIRouter:
    r = APIRouter()
    db = eng.db

    def project_views(rows: list[dict]) -> list[dict]:
        """Listenansicht der Objekte. Alle Kennzahlen in wenigen gruppierten Abfragen über eine Verbindung –
        je Objekt einzeln abzufragen kostete bei 58 Objekten ~1,8 s (≈ 460 Verbindungen) und bremste jeden Seitenwechsel."""
        if not rows:
            return []
        ids = [p["id"] for p in rows]
        ph = ",".join("?" * len(ids))
        counts, nights, dl, last, job, n_results, solve, comp = {}, {}, {}, {}, {}, {}, {}, {}
        with db.conn() as c:
            for x in c.execute(f"SELECT project_id, status, COUNT(*) n FROM frames WHERE kind='light' AND project_id IN ({ph}) "
                               "GROUP BY project_id, status", ids):
                counts.setdefault(x["project_id"], {})[x["status"]] = x["n"]
            for x in c.execute(f"SELECT DISTINCT project_id, night FROM frames WHERE kind='light' AND project_id IN ({ph}) "
                               "ORDER BY night", ids):
                nights.setdefault(x["project_id"], []).append(x["night"])
            for x in c.execute(f"SELECT project_id, COUNT(*) n, COALESCE(SUM(size),0) b, COALESCE(SUM(stacked),0) s FROM frames "
                               f"WHERE kind='light' AND deleted=1 AND project_id IN ({ph}) GROUP BY project_id", ids):
                dl[x["project_id"]] = dict(x)
            for x in c.execute(f"SELECT * FROM (SELECT *, ROW_NUMBER() OVER (PARTITION BY project_id ORDER BY "
                               "COALESCE(json_extract(stats,'$.imported.series.sequence'),0) DESC, id DESC) rn "
                               f"FROM results WHERE project_id IN ({ph})) WHERE rn=1", ids):
                last[x["project_id"]] = dict(x)
            for x in c.execute(f"SELECT project_id, COUNT(*) n FROM results WHERE project_id IN ({ph}) GROUP BY project_id", ids):
                n_results[x["project_id"]] = x["n"]
            for x in c.execute(f"SELECT * FROM jobs WHERE id IN (SELECT MAX(id) FROM jobs WHERE project_id IN ({ph}) "
                               "GROUP BY project_id)", ids):
                job[x["project_id"]] = dict(x)
            for x in c.execute("SELECT json_extract(options,'$.gallery_parent') gp, COUNT(*) n FROM projects "
                               "WHERE json_extract(options,'$.gallery_parent') IS NOT NULL GROUP BY gp"):
                comp[x["gp"]] = x["n"]
            # Lösung nur für Objekte mit Koordinaten nötig (liest JSON je Frame – der teuerste Teil)
            with_coords = [p["id"] for p in rows if p["ra"] is not None]
            if with_coords:
                for x in c.execute("SELECT p.id, (SELECT json_extract(f.info,'$.solve') FROM frames f WHERE f.project_id=p.id "
                                   "AND json_extract(f.info,'$.solve') IS NOT NULL LIMIT 1) s FROM projects p "
                                   f"WHERE p.id IN ({','.join('?' * len(with_coords))})", with_coords):
                    if x["s"] is not None:
                        solve[x["id"]] = x["s"]
        out = []
        for p in rows:
            pid = p["id"]
            imported = json.loads(p["options"] or "{}").get("input_type") == "picture"
            fr = counts.get(pid, {})
            sv = solve.get(pid)
            d = dl.get(pid, {"n": 0, "b": 0, "s": 0})
            lr, jb = last.get(pid), job.get(pid)
            coords = None
            if p["ra"] is not None:
                coords = {"ra": p["ra"], "dec": p["dec"], "source": "solve" if sv else ("catalog" if p["obj_id"] else "header")}
                if sv:
                    coords["solve"] = json.loads(sv)
            out.append({
                "id": pid, "name": p["name"], "kind": p["kind"], "kind_label": objects.KIND_LABEL.get(p["kind"] or "generic"),
                "options": json.loads(p["options"] or "{}"), "frames": fr, "total": sum(fr.values()),
                "companions": comp.get(pid, 0),
                "nights": nights.get(pid, []), "n_results": n_results.get(pid, 0), "created": p["created"],
                "coords": coords, "can_solve": not imported and not p["obj_id"], "imported": imported,
                # Einzelbilder nach dem Stacking gelöscht -> kein komplettes Neu-Stacken mehr möglich
                "deleted": {"frames": d["n"], "bytes": d["b"], "can_restack": not imported and d["s"] == 0},
                "result": {"id": lr["id"], "created": lr["created"], "n_frames": lr["n_frames"], "exposure": lr["exposure"],
                           "stats": json.loads(lr["stats"]), "formats": list(json.loads(lr["files"] or "{}"))} if lr else None,
                "job": {k: jb[k] for k in ("id", "state", "progress", "stage", "message", "finished")} if jb else None,
            })
        return out

    def project_view(p: dict) -> dict:
        return project_views([p])[0]

    @r.get("/status")
    def status():
        return eng.status()

    @r.get("/statistics")
    def statistics(year: int | None = None):
        from .activity_stats import summarize
        return summarize(db.q("SELECT kind,info,project_id FROM frames WHERE kind='light'"), year)

    @r.post("/storage")
    def set_storage(body: dict = Body(...)):
        for k in ("delete_uploads", "gaia"):
            if k in body:
                setattr(eng.s, k, bool(body[k]))
        eng.save_settings()
        return {"delete_uploads": eng.s.delete_uploads, "gaia": eng.s.gaia}

    @r.get("/housekeeping")
    def housekeeping_scan(keep: int = 2):
        """Speicherbelegung + aufräumbare Bereiche (nur lesen)."""
        from . import housekeeping
        return {"usage": housekeeping.usage(eng.s, db), "unused": housekeeping.scan(eng.s, db, max(1, keep))}

    @r.post("/housekeeping/cleanup")
    def housekeeping_cleanup(body: dict = Body(...)):
        from . import housekeeping
        # wartende Aufträge stören nicht, solange die Warteschlange steht (angehalten oder Platte voll) –
        # genau dann muss Aufräumen möglich sein; währenddessen bleibt sie angehalten
        if db.one("SELECT id FROM jobs WHERE state='running' LIMIT 1") or (
                not eng.hold() and db.one("SELECT id FROM jobs WHERE state='queued' LIMIT 1")):
            raise HTTPException(409, "Gerade läuft eine Verarbeitung – bitte danach aufräumen oder die Aufträge anhalten.")
        keys = [k for k in body.get("keys") or [] if isinstance(k, str)]
        before = eng.paused
        eng.set_paused(True, before or "Aufräumen läuft – Aufträge warten.")
        try:
            with eng.pl._scan_lock:                      # kein Scan/Import gleichzeitig
                return housekeeping.cleanup(eng.s, db, keys, int(body.get("keep") or housekeeping.KEEP_VERSIONS))
        finally:
            if before:
                eng.set_paused(True, before)
            else:
                eng.set_paused(False)

    @r.get("/solver")
    def get_solver():
        return eng.pl.solver_cfg.status()

    @r.post("/solver")
    def set_solver(body: dict = Body(...)):
        c = eng.pl.solver_cfg
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
        platesolve._AVAILABLE.clear()                    # z. B. ASTAP gerade installiert -> sofort neu suchen
        eng.save_settings()
        return get_solver()

    @r.post("/projects/{pid}/solve")
    def solve(pid: int):
        p = db.one("SELECT options FROM projects WHERE id=?", (pid,))
        if not p:
            raise HTTPException(404)
        if json.loads(p["options"] or "{}").get("input_type") == "picture":
            raise HTTPException(409, "Fertige Bilder bitte mit Sage bestimmen lassen.")
        if not eng.pl.solver_cfg.enabled:
            raise HTTPException(400, "Kein Plate-Solver eingerichtet – siehe Einstellungen.")
        return {"job": eng.enqueue(pid, {"action": "solve"})}

    @r.post("/scan")
    def scan():
        return eng.scan_and_queue()

    @r.get("/projects")
    def projects(parent_id: int | None = None):
        rows = db.q("SELECT * FROM projects WHERE json_extract(options,'$.gallery_parent') IS NULL ORDER BY name") if parent_id is None else db.q(
            "SELECT * FROM projects WHERE json_extract(options,'$.gallery_parent')=? ORDER BY id DESC", (parent_id,))
        return project_views(rows)

    @r.get("/projects/{pid}")
    def project(pid: int):
        p = db.one("SELECT * FROM projects WHERE id=?", (pid,))
        if not p:
            raise HTTPException(404)
        v = project_view(p)
        from .live import read_state
        v['live'] = read_state(eng.s.work_dir, pid)
        if v['live'].get('job') != (v.get('job') or {}).get('id'):
            v['live'] = {}
        v["results"] = [{"id": x["id"], "created": x["created"], "n_frames": x["n_frames"], "exposure": x["exposure"],
                         "stats": json.loads(x["stats"]), "review": json.loads(x["review"] or "[]"),
                         "has_before": "before" in json.loads(x["files"] or "{}")}
                        for x in db.q("SELECT * FROM results WHERE project_id=? ORDER BY COALESCE(json_extract(stats,'$.imported.series.sequence'),0) DESC,id DESC", (pid,))]
        v["profiles"] = list(objects.PROFILES)
        v["palettes"] = list(channels.PALETTES)
        v["kind_labels"] = objects.KIND_LABEL
        return v

    @r.get('/projects/{pid}/live/{name}')
    def live_picture(pid: int, name: str):
        if not re.fullmatch(r'\d+-\d+\.jpg', name):
            raise HTTPException(404)
        path = eng.s.work_dir / f'p{pid}' / 'live' / name
        if not path.is_file():
            raise HTTPException(404)
        return FileResponse(path, media_type='image/jpeg', headers={'Cache-Control': 'no-store'})

    @r.patch("/projects/{pid}")
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

    @r.delete("/projects/{pid}")
    def delete_project(pid: int):
        if not db.one("SELECT id FROM projects WHERE id=?", (pid,)):
            raise HTTPException(404)
        if db.one("SELECT id FROM jobs WHERE project_id=? AND state IN ('queued','running')", (pid,)):
            raise HTTPException(409, "Das Objekt wird gerade verarbeitet – bitte warten, bis der Durchlauf fertig ist.")
        with eng.pl._scan_lock:                          # kein gleichzeitiger Scan, der Frames neu zuordnet
            return eng.pl.delete_project(pid)

    @r.post("/develop-all")
    def develop_all():
        """Alle Objekte mit Master-Stack aus dem Stack neu entwickeln (z. B. nach neuen Bearbeitungsfunktionen).
        Fertig importierte Bilder und Objekte ohne Stack werden übersprungen."""
        jobs = []
        for p in db.q("SELECT id, options FROM projects ORDER BY id"):
            if json.loads(p["options"] or "{}").get("input_type") == "picture":
                continue
            if any((eng.s.work_dir / f"p{p['id']}").glob("stack*/state.json")):
                jobs.append(eng.enqueue(p["id"], {"mode": "develop"}))
        return {"queued": len(jobs)}

    @r.post("/projects/{pid}/process")
    def process(pid: int, body: dict = Body(default={})):
        p = db.one("SELECT options FROM projects WHERE id=?", (pid,))
        if not p:
            raise HTTPException(404)
        if json.loads(p["options"] or "{}").get("input_type") == "picture":
            raise HTTPException(409, "Dieses Bild wurde fertig importiert. Zum Stacken oder Neu-Entwickeln sind Rohaufnahmen erforderlich.")
        if body.get("restack"):
            if db.one("SELECT id FROM frames WHERE project_id=? AND deleted=1 AND stacked=1 LIMIT 1", (pid,)):
                raise HTTPException(409, "Komplett neu stacken ist nicht möglich: die hochgeladenen Einzelbilder wurden "
                                         "nach dem Stacking gelöscht. Neue Aufnahmen werden weiterhin ergänzt.")
            rows = db.q("SELECT id, path, deleted FROM frames WHERE project_id=? AND kind='light' AND stacked=1", (pid,))
            if off := eng.pl.offline(rows):
                raise HTTPException(409, f"Komplett neu stacken ist gerade nicht möglich: {len(off)} Aufnahmen liegen in einem "
                                         f"Ordner, der nicht erreichbar ist ({eng.pl.offline_hint(rows, off)}) – z. B. "
                                         "USB-Platte abgesteckt. Bitte anschließen und erneut versuchen.")
            db.x("UPDATE projects SET restack=1 WHERE id=?", (pid,))
        return {"job": eng.enqueue(pid, {k: v for k, v in body.items() if k != "restack"})}

    @r.get("/projects/{pid}/frames")
    def frames(pid: int):
        out = []
        for x in db.q("SELECT * FROM frames WHERE project_id=? AND kind='light' ORDER BY json_extract(info,'$.date_obs'), path", (pid,)):
            m = json.loads(x["metrics"]) if x["metrics"] else {}
            i = json.loads(x["info"])
            out.append({"id": x["id"], "name": Path(x["path"]).name, "night": x["night"], "status": x["status"], "reason": x["reason"],
                        "band": channels.band_of(i.get("filter"), channels.is_color(i)), "filter": i.get("filter"),
                        "exptime": i.get("exptime"), "date": i.get("date_obs"),
                        "score": round(x["score"] or 0, 3), "manual": x["manual"], "stacked": x["stacked"], "deleted": x["deleted"],
                        "offline": bool(not x["deleted"] and x["path"] and not Path(x["path"]).exists()),
                        "n_stars": m.get("n_stars"), "fwhm": m.get("fwhm") and round(m["fwhm"], 2),
                        "ecc": m.get("ecc") and round(m["ecc"], 2), "bg": m.get("bg") and round(m["bg"], 1)})
        return out

    @r.patch("/frames/{fid}")
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

    @r.get("/jobs")
    def jobs():
        return db.q("SELECT j.*, p.name project FROM jobs j LEFT JOIN projects p ON p.id=j.project_id ORDER BY j.id DESC LIMIT 50")

    @r.post("/upload")
    async def upload(files: list[UploadFile] = File(...)):
        from .picture_import import PICTURE_EXT, attach_previews, import_picture, skipped_pictures
        dest = eng.s.input_dir / "uploads" / time.strftime("%Y%m%d")
        dest.mkdir(parents=True, exist_ok=True)
        n, raw, skipped, pictures, errors = 0, 0, 0, [], []
        skip_ext = skipped_pictures([f.filename or "" for f in files])
        for f in files:
            # relative Ordnerpfade behalten (z. B. „…_observation_M57/img-0001r.fits“ – der Ordner verrät das Objekt),
            # aber nur harmlose Pfadteile zulassen
            parts = [p for p in re.split(r"[\\/]+", f.filename or "") if p not in ("", ".", "..") and ":" not in p]
            if not parts:
                continue
            if skip_ext and parts[-1].lower().endswith(skip_ext):
                skipped += 1
                continue
            if parts[-1].lower().endswith(PICTURE_EXT):
                try:
                    pictures.append(await asyncio.to_thread(import_picture, eng, f.file, parts[-1], False))
                    n += 1
                except Exception as e:
                    errors.append(f"{parts[-1]}: Bild nicht importiert: {e}")
                continue
            if not parts[-1].lower().endswith((".fit", ".fits", ".fts")):
                continue
            target = dest.joinpath(*parts[-4:])
            target.parent.mkdir(parents=True, exist_ok=True)
            with open(target, "wb") as out:
                shutil.copyfileobj(f.file, out)
            n += 1
            raw += 1
        await asyncio.to_thread(attach_previews, eng)
        for item in pictures:
            opt = json.loads(db.one("SELECT options FROM projects WHERE id=?", (item["id"],))["options"])
            item["series"] = bool((opt.get("imported") or {}).get("series"))
            item["parent_id"] = opt.get("gallery_parent")
        for pid in {p["id"] for p in pictures if not p.get("duplicate")}:
            p = db.one("SELECT * FROM projects WHERE id=?", (pid,))
            opt = json.loads(p["options"] or "{}")
            if not opt.get("gallery_parent") and not (raw and (opt.get("imported") or {}).get("series")):
                last = db.one("SELECT id FROM results WHERE project_id=? ORDER BY COALESCE(json_extract(stats,'$.imported.series.sequence'),0) DESC,id DESC LIMIT 1", (pid,))
                eng.ctx.emit("stacking.imported", project_id=pid, result_id=last["id"], name=p["name"])
        return {"saved": n, "raw": raw, "skipped": skipped, "pictures": pictures, "errors": errors}

    @r.post("/queue")
    def queue_state(body: dict = Body(...)):
        """Warteschlange anhalten (bricht den laufenden Auftrag ab, er setzt später am Checkpoint fort) oder fortsetzen."""
        eng.set_paused(bool(body.get("paused")))
        return {"paused": bool(eng.paused), "hold": eng.hold()}

    @r.get("/library")
    def library_state():
        return eng.library.view()

    @r.post("/library/scan")
    def library_scan(body: dict = Body(...)):
        try:
            eng.library.start_scan(str(body.get("root") or ""))
        except ValueError as e:
            raise HTTPException(400, str(e))
        except RuntimeError as e:
            raise HTTPException(409, str(e))
        return eng.library.view()

    @r.post("/library/import")
    def library_import(body: dict = Body(...)):
        ids = [i for i in body.get("ids") or [] if isinstance(i, str)]
        try:
            n = eng.library.start_import(ids)
        except ValueError as e:
            raise HTTPException(400, str(e))
        except RuntimeError as e:
            raise HTTPException(409, str(e))
        return {"groups": n, **eng.library.view()}

    # ---- Nachtplanung: Standort und optionale Wetterfreigabe aus dem Bild-Tutor ------------
    @r.get("/tonight")
    def get_tonight():
        projects, device = tonight.library(db)
        return tonight.plan(projects, tutor.settings(eng.ctx.store), eng.s.work_dir / "tutor", device=device)

    # ---- Bild-Tutor (vor /results/{rid}/{kind}, sonst fängt die Datei-Route „tutor“ ab) --------
    @r.get("/tutor")
    def get_tutor():
        t = tutor.settings(eng.ctx.store)
        return {**t, "llm_ready": tutor.llm_config(t, eng.review_cfg) is not None, "providers": ["off", "lmstudio", "openrouter"]}

    @r.post("/tutor")
    def set_tutor(body: dict = Body(...)):
        upd = {}
        if "mode" in body:
            if body["mode"] not in tutor.MODES:
                raise HTTPException(400, "Unbekannter Modus")
            upd["mode"] = body["mode"]
        for k in ("lat", "lon"):
            if k in body:
                v = str(body[k] if body[k] is not None else "").strip().replace(",", ".")
                try:
                    f = float(v) if v else None
                except ValueError:
                    raise HTTPException(400, "Breite/Länge als Zahl in Grad angeben, z. B. 49,1 und 8,5.")
                if f is not None and not (-90 <= f <= 90 if k == "lat" else -180 <= f <= 180):
                    raise HTTPException(400, "Breite −90…90°, Länge −180…180°.")
                upd[k] = round(f, 3) if f is not None else None
        if "weather" in body:
            upd["weather"] = bool(body["weather"])
        if "llm_provider" in body:
            if body["llm_provider"] not in ("off", "lmstudio", "openrouter"):
                raise HTTPException(400, "Unbekannter Anbieter")
            upd["llm_provider"] = body["llm_provider"]
        if "llm_model" in body:
            upd["llm_model"] = str(body["llm_model"] or "")
        eng.ctx.store.update("tutor", upd)              # None entfernt den Standort wieder
        return get_tutor()

    @r.get("/results/{rid}/tutor")
    def result_tutor(rid: int, refresh: bool = False):
        try:
            return tutor.build_report(db, rid, tutor.settings(eng.ctx.store), eng.s.work_dir, refresh)
        except KeyError:
            raise HTTPException(404)

    @r.post("/results/{rid}/tutor/ki")
    def result_tutor_ki(rid: int):
        try:
            return tutor.ask_report(db, rid, tutor.settings(eng.ctx.store), eng.review_cfg, eng.s.work_dir)
        except KeyError:
            raise HTTPException(404)
        except ValueError as e:
            raise HTTPException(400, str(e))
        except Exception as e:                       # Netz, Modell, Zeitlimit
            raise HTTPException(502, f"KI nicht erreichbar: {str(e)[:200]}")

    @r.delete("/results/{rid}")
    def delete_result(rid: int):
        x = db.one("SELECT project_id FROM results WHERE id=?", (rid,))
        if not x:
            raise HTTPException(404)
        if db.one("SELECT id FROM jobs WHERE project_id=? AND state IN ('queued','running')", (x["project_id"],)):
            raise HTTPException(409, "Das Objekt wird gerade verarbeitet – bitte warten, bis der Durchlauf fertig ist.")
        from . import housekeeping
        try:
            return housekeeping.delete_version(eng.s, db, rid)
        except KeyError:                                 # inzwischen von einer anderen Anfrage gelöscht
            raise HTTPException(404)
        except ValueError as e:
            raise HTTPException(409, str(e))

    @r.get("/results/{rid}/{kind}")
    def result_file(rid: int, kind: str):
        x = db.one("SELECT files FROM results WHERE id=?", (rid,))
        files = json.loads(x["files"]) if x else {}
        if kind not in files or not Path(files[kind]).exists():
            raise HTTPException(404)
        return FileResponse(files[kind], filename=Path(files[kind]).name if kind in ("tif", "fits", "png", "original") else None)

    @r.get("/starnet")
    def get_starnet():
        exe = stars.starnet_path()
        return {"path": exe, "found": bool(exe), "custom": stars.STARNET.path,
                "installed_here": bool(exe and stars.STARNET.tools_dir and str(stars.STARNET.tools_dir) in exe),
                "page": stars.STARNET_PAGE}

    @r.post("/starnet")
    def set_starnet(body: dict = Body(...)):
        p = str(body.get("path") or "").strip()
        if p and not Path(p).is_file():
            raise HTTPException(400, f"Datei nicht gefunden: {p}")
        stars.STARNET.path = p
        eng.save_settings()
        return get_starnet()

    @r.post("/starnet/install")
    async def install_starnet(file: UploadFile = File(...)):
        if not (file.filename or "").lower().endswith(".zip"):
            raise HTTPException(400, "Bitte die heruntergeladene ZIP-Datei der StarNet-Kommandozeilenversion wählen.")
        tmp = eng.s.data_dir / "tools" / "starnet_upload.zip"
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

    @r.delete("/starnet")
    def remove_starnet():
        shutil.rmtree(stars.STARNET.tools_dir, ignore_errors=True)
        stars.STARNET.path = ""
        eng.save_settings()
        return get_starnet()

    @r.get("/graxpert")
    def get_graxpert():
        exe = graxpert.graxpert_path()
        g = graxpert.GRAXPERT
        return {"path": exe, "found": bool(exe), "custom": g.path, "mode": g.mode, "gpu": g.gpu, "page": graxpert.PAGE}

    @r.post("/graxpert")
    async def set_graxpert(body: dict = Body(...)):
        g = graxpert.GRAXPERT
        if "path" in body:
            p = str(body.get("path") or "").strip().strip('"')
            if p:
                if not Path(p).is_file():
                    raise HTTPException(400, f"Datei nicht gefunden: {p}")
                try:
                    await asyncio.to_thread(graxpert.selftest, p)
                except Exception as e:
                    raise HTTPException(400, f"GraXpert lässt sich nicht starten: {e}")
            g.path = p
        if "mode" in body:
            if body["mode"] not in graxpert.MODES:
                raise HTTPException(400, "Unbekannter Modus")
            g.mode = body["mode"]
        if "gpu" in body:
            g.gpu = bool(body["gpu"])
        eng.save_settings()
        return get_graxpert()

    @r.get("/gpu")
    def get_gpu():
        return gpu.status()

    @r.post("/gpu")
    def set_gpu(body: dict = Body(...)):
        if body.get("mode") not in gpu.MODES:
            raise HTTPException(400, "Unbekannter Modus")
        gpu.GPU.mode = body["mode"]
        eng.save_settings()
        return gpu.status()

    @r.get("/reviewer")
    def get_reviewer():
        c = eng.review_cfg
        return {"provider": c.provider, "base_url": c.base_url, "model": c.model, "max_rounds": c.max_rounds, "has_key": bool(c.api_key)}

    @r.get("/reviewer/models")
    def reviewer_models(provider: str | None = None, base_url: str | None = None):
        """Modelle mit Bildeingabe für den KI-Prüfer (auch für eine noch nicht gespeicherte Anbieterwahl)."""
        from dataclasses import replace
        from ...core.models import ModelListError, list_models
        c = eng.review_cfg
        prov = provider or c.provider
        if prov not in ("lmstudio", "openrouter"):
            raise HTTPException(400, "Erst einen Anbieter wählen.")
        cfg = replace(c, provider=prov, base_url=c.base_url if base_url is None else base_url).resolved()
        try:
            return list_models(cfg.base_url, c.api_key if prov == "openrouter" else "", "vision")
        except ModelListError as e:
            raise HTTPException(502, str(e))

    @r.post("/reviewer")
    def set_reviewer(body: dict = Body(...)):
        c = eng.review_cfg
        for k in ("provider", "base_url", "model", "api_key"):
            if k in body and (k != "api_key" or body[k]):
                setattr(c, k, str(body[k]))
        if "max_rounds" in body:
            c.max_rounds = max(1, min(6, int(body["max_rounds"])))
        eng.save_settings()
        return get_reviewer()

    return r
