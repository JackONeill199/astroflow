"""Stacking-Modul: Job-Worker, Ordner-Watcher und REST-API (unter /api/stacking)."""
from __future__ import annotations

import asyncio
import json
import queue
import re
import secrets
import shutil
import threading
import time
import traceback
from pathlib import Path

from fastapi import APIRouter, Body, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse

from . import calibration, channels, comet, diskspace, gpu, graxpert, objects, platesolve, reviewer, skyquality, stars, tonight, tutor
from ...core import security
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
        self.pl.site_provider = self._site                # Kometenmodus: Parallaxe lokal am Standort rechnen
        from .library import Library
        self.library = Library(self)
        from .insights import Insights
        self.insights = Insights(self)                    # Tiefe und Vergleich fertiger Bilder (eigener Thread)
        from .dashboard import Dashboard
        self.dashboard = Dashboard(self)                  # Analyse-Dashboard über alle Objekte + Sammelauftrag
        skyquality.ensure_table(self.db)
        self.sky = skyquality.SkyRunner(self.pl, ctx.services.get("site_for"))
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

    def _site(self, night: str | None = None):
        """Standort für die Kometen-Parallaxe: gemeinsame Ortsliste (Ort der Nacht), sonst alte Tutor-Einstellung."""
        book = self.ctx.services.get("sites")
        if book:
            try:
                if site := book.site_for(night=night):
                    return [float(site["lat"]), float(site["lon"])]
            except Exception:
                pass
        t = tutor.settings(self.ctx.store)
        if t.get("lat") in (None, "") or t.get("lon") in (None, ""):
            return None
        return [float(t["lat"]), float(t["lon"])]

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
                rid = self.pl.develop(job["project_id"], opts, prog, start=0.05)
            else:
                rid = self.pl.finish(job["project_id"], opts, prog)
            self.db.x("UPDATE jobs SET state='done', progress=1, finished=? WHERE id=?", (time.time(), jid))
            self.insights.after_develop(rid)
            if skyquality.auto_enabled():                 # Himmelsqualität der Nächte im Hintergrund nachrechnen
                self.sky.queue(job["project_id"])
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
                "last_scan": self.last_scan, "frames": frames, "busy_jobs": busy + (self.sky.state.get("state") == "running")
                + self.dashboard.batch.busy(), "disk": disk,
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
        return self.dashboard.batch.activity() + self.sky.activity() + out


_coll_cache: dict = {}          # Dienst collection_progress (Sage), 2 min zwischengespeichert


def _slim(stats: str | None) -> dict:
    """Ergebnis-Kennzahlen für Listen/Objektseite – ohne die großen Auswertungen (Tiefe, Vergleich, Asteroiden: eigene Endpunkte)."""
    st = json.loads(stats or "{}")
    st.pop("depth", None)
    st.pop("compare", None)
    st.pop("asteroids", None)
    st.pop("hrd", None)
    st.pop("diagnose", None)                       # Gerätediagnose: eigener Endpunkt, Verläufe bis 400 Punkte je Nacht
    return st


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
            is_comet = not imported and comet.active(p, json.loads(p["options"] or "{}"))
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
                # Kometen sind am Namen erkannt: „Objekt bestimmen“ würde nichts zuordnen (der Stack wird für die Bahn ohnehin gelöst)
                "coords": coords, "can_solve": not imported and not p["obj_id"] and not is_comet, "imported": imported,
                "comet": is_comet,
                # Einzelbilder nach dem Stacking gelöscht -> kein komplettes Neu-Stacken mehr möglich
                "deleted": {"frames": d["n"], "bytes": d["b"], "can_restack": not imported and d["s"] == 0},
                "result": {"id": lr["id"], "created": lr["created"], "n_frames": lr["n_frames"], "exposure": lr["exposure"],
                           "stats": _slim(lr["stats"]), "formats": list(json.loads(lr["files"] or "{}"))} if lr else None,
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
    def set_solver(request: Request, body: dict = Body(...)):
        c = eng.pl.solver_cfg
        # Formular schickt die Felder mit – unverändert heißt: wie gespeichert oder wie gerade gefunden (GET liefert astap_path())
        same = {"astap": {c.astap or "", c.astap_path() or ""}, "astap_db": {c.astap_db or ""}}
        if any(k in body and str(body[k] or "").strip() not in same[k] for k in same):
            security.require_local(request, "Programmpfade setzen")
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
                         "stats": _slim(x["stats"]), "review": json.loads(x["review"] or "[]"),
                         "has_before": "before" in json.loads(x["files"] or "{}"),
                         "views": [k for k in ("stars", "comet") if k in json.loads(x["files"] or "{}")]}
                        for x in db.q("SELECT * FROM results WHERE project_id=? ORDER BY COALESCE(json_extract(stats,'$.imported.series.sequence'),0) DESC,id DESC", (pid,))]
        v["hotpixel_hint"] = not v.get("imported") and eng.pl.hotpixel_hint(pid)
        v["profiles"] = list(objects.PROFILES)
        v["palettes"] = list(channels.PALETTES)
        v["kind_labels"] = objects.KIND_LABEL
        return v

    @r.get('/projects/{pid}/live/{name}')
    def live_picture(pid: int, name: str):
        if not re.fullmatch(r'\d+-f?\d+\.jpg', name):            # Vorschau bzw. Zwischenstand (…-f<n>.jpg)
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
    async def upload(files: list[UploadFile] = File(...), upload_id: str = Form("")):
        from .picture_import import PICTURE_EXT, attach_previews, import_picture, skipped_pictures
        # Je Upload-Vorgang ein eigener Ordner: gleichnamige Dateien (Stellina img-0001r.fits in jeder Beobachtung)
        # überschreiben sich so nie. Die Kennung kommt vom Client (alle Teilanfragen eines Vorgangs teilen sie).
        token = upload_id if re.fullmatch(r"[A-Za-z0-9]{6,32}", upload_id or "") else time.strftime("%H%M%S") + secrets.token_hex(3)
        dest = eng.s.input_dir / "uploads" / time.strftime("%Y%m%d") / token
        dest.mkdir(parents=True, exist_ok=True)
        n, raw, pictures, errors = 0, 0, [], []
        # versteckte Dateien/Ordner (Name mit „.“, z. B. Stellina „.thumbnail/…“) nie übernehmen – die Oberfläche lässt
        # sie schon weg, ältere Clients oder Skripte nicht
        visible = lambda f: not any(p.startswith(".") and p not in (".", "..")      # „..“ wird unten nur entfernt
                                    for p in re.split(r"[\\/]+", f.filename or ""))
        skipped = sum(not visible(f) for f in files)
        files = [f for f in files if visible(f)]
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
            n_try = 0
            while True:                                   # nie überschreiben: bei Namensgleichheit eindeutig nummerieren
                try:
                    out = open(target, "xb")
                    break
                except FileExistsError:
                    n_try += 1
                    target = target.with_name(f"{Path(parts[-1]).stem}-{n_try}{Path(parts[-1]).suffix}")
            with out:
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
    def get_tonight(hide: str = ""):
        """hide: Kennungen, die der Nutzer ausgeblendet hat (kommagetrennt, lokal im Browser gemerkt)."""
        projects, device = tonight.library(db)
        hidden = {h.strip() for h in hide.split(",") if h.strip()}
        coll = None
        svc = eng.ctx.services.get("collection_progress")            # optional (Sage): schon entdeckte Messier-Objekte
        if svc:
            now = time.time()
            if now - _coll_cache.get("t", 0) > 120:
                try:
                    _coll_cache.update(t=now, v=svc())
                except Exception:
                    _coll_cache.update(t=now, v=None)
            coll = _coll_cache.get("v")
        cfg, here, night = tutor.settings(eng.ctx.store), None, None
        site_for = eng.ctx.services.get("site_for")                  # gemeinsame Ortsliste: Ort der geplanten Nacht
        if site_for:
            from ...core.sites import planning_night
            now = time.time()
            try:
                # Nacht wie tonight._window (vormittags schon die kommende); Länge erst vom Standard-, dann vom Nachtort
                night = planning_night(now, (site_for(night="1900-01-01") or {}).get("lon") or 0)
                here = site_for(night=night)
                if here and planning_night(now, here["lon"]) != night:
                    night = planning_night(now, here["lon"])
                    here = site_for(night=night)
            except Exception:
                here = None
            if here:
                cfg = {**cfg, "lat": here["lat"], "lon": here["lon"]}
        out = tonight.plan(projects, cfg, eng.s.work_dir / "tutor", device=device, hidden=hidden, collection=coll)
        sky = None
        if here:
            try:
                sky = skyquality.for_site(db, here.get("id"))
            except Exception:
                sky = None
        return {**out, "site_info": {k: here.get(k) for k in ("id", "name", "source", "detail")} if here else None,
                "sites_on": bool(site_for), "night": night, "sky_quality": sky}

    # ---- Himmelsqualität (Himmelshelligkeit, Bortle, Lichtrichtung, Seeing) ------------------------
    @r.get("/sky")
    def get_sky():
        out = skyquality.summary(db)
        have = {x["project_id"] for x in db.q("SELECT DISTINCT project_id FROM sky_nights")}
        todo = db.one("SELECT COUNT(DISTINCT f.project_id) n FROM frames f JOIN projects p ON p.id=f.project_id WHERE f.kind='light' "
                      "AND f.metrics IS NOT NULL AND COALESCE(json_extract(p.options,'$.input_type'),'')!='picture'")["n"]
        st = eng.sky.state
        return {**out, "computed": len(have), "projects": todo, "gaia": eng.s.gaia, "solver": eng.pl.solver_cfg.enabled,
                "job": {k: st.get(k) for k in ("state", "total", "done", "current", "detail", "nights", "errors", "finished")}}

    @r.get("/sky/clean")
    def get_sky_clean():
        return skyquality.clean_nights(db)

    @r.get("/projects/{pid}/sky")
    def get_project_sky(pid: int):
        nights = skyquality.nights_of(db, pid)
        for n in nights:
            n.pop("dirs", None)
        return {"nights": nights, "busy": eng.sky.busy()}

    @r.post("/sky/compute")
    def compute_sky(body: dict = Body(default={})):
        if eng.sky.busy():
            raise HTTPException(409, "Die Himmelsqualität wird gerade berechnet – bitte kurz warten.")
        pid = body.get("project_id")
        if pid is not None and not db.one("SELECT id FROM projects WHERE id=?", (int(pid),)):
            raise HTTPException(404, "Objekt nicht gefunden")
        st = eng.sky.start([int(pid)] if pid is not None else None, only_missing=bool(body.get("missing")))
        return {k: st.get(k) for k in ("state", "total", "done")}

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
            return tutor.build_report(db, rid, tutor.settings(eng.ctx.store), eng.s.work_dir, refresh,
                                      site_for=eng.ctx.services.get("site_for"))
        except KeyError:
            raise HTTPException(404)

    @r.post("/results/{rid}/tutor/ki")
    def result_tutor_ki(rid: int):
        try:
            return tutor.ask_report(db, rid, tutor.settings(eng.ctx.store), eng.review_cfg, eng.s.work_dir,
                                    site_for=eng.ctx.services.get("site_for"))
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

    @r.get("/results/{rid}/objects")
    def result_objects(rid: int):
        """Katalogobjekte im fertigen Bild (Pixel des JPGs) aus der Plate-Solving-Lösung des Master-Stacks – für die
        Objektnamen beim Überfahren des Bildes. Ohne Lösung leer. Je Version zwischengespeichert (neu, wenn wcs.json jünger ist)."""
        x = db.one("SELECT project_id, files FROM results WHERE id=?", (rid,))
        if not x:
            raise HTTPException(404)
        pid, files = x["project_id"], json.loads(x["files"] or "{}")
        wcs = eng.s.work_dir / f"p{pid}" / "wcs.json"
        if not wcs.exists():
            return {"objects": [], "reason": "nicht per Plate-Solving gelöst"}
        cache = eng.s.work_dir / f"p{pid}" / f"objekte_r{rid}.json"
        if cache.exists() and cache.stat().st_mtime >= wcs.stat().st_mtime:
            try:
                return json.loads(cache.read_text(encoding="utf-8"))
            except ValueError:
                pass
        from .services import project_solution
        try:
            sol = project_solution(eng.s.work_dir, pid, files)
        except Exception as e:                           # beschädigte Lösung/Datei: einfach ohne Namen
            return {"objects": [], "reason": f"Lösung nicht lesbar: {e}"}
        out = ({"width": sol["width"], "height": sol["height"], "objects": [o for o in sol["objects"] if o.get("x") is not None]}
               if sol else {"objects": [], "reason": "kein Master-Stack"})
        try:
            cache.write_text(json.dumps(out), encoding="utf-8")
        except OSError:
            pass
        return out

    # ---- Wie tief schaust du? / Vergleich mit Referenz (insights.py) ----------------------------------
    def _insight(rid: int, key: str, version: int):
        x = db.one("SELECT project_id, stats FROM results WHERE id=?", (rid,))
        if not x:
            raise HTTPException(404)
        st = eng.insights.status(key, rid)
        data = json.loads(x["stats"] or "{}").get(key)
        solved = (eng.s.work_dir / f"p{x['project_id']}" / "wcs.json").exists()
        if st and st["state"] in ("queued", "running", "error"):
            return {"state": st["state"], "message": st["message"], "solved": solved}
        if data and data.get("ok") and data.get("v") == version:
            return {"state": "done", "solved": solved, **data}
        if data and not data.get("ok"):
            return {"state": "error", "message": data.get("error"), "solved": solved}
        return {"state": "old" if data else "none", "solved": solved}

    @r.get("/results/{rid}/depth")
    def get_depth(rid: int):
        from .depth import DEPTH_VERSION
        return _insight(rid, "depth", DEPTH_VERSION)

    @r.post("/results/{rid}/depth")
    def run_depth(rid: int):
        if not db.one("SELECT id FROM results WHERE id=?", (rid,)):
            raise HTTPException(404)
        return eng.insights.submit("depth", rid)

    @r.get("/results/{rid}/hrd")
    def get_hrd(rid: int):
        from .hrd import HRD_VERSION, is_cluster
        out = _insight(rid, "hrd", HRD_VERSION)
        p = db.one("SELECT p.kind, p.name FROM results r JOIN projects p ON p.id=r.project_id WHERE r.id=?", (rid,))
        out["haufen_art"] = bool(p and is_cluster(p["kind"], p["name"]))
        return out

    @r.post("/results/{rid}/hrd")
    def run_hrd(rid: int):
        if not db.one("SELECT id FROM results WHERE id=?", (rid,)):
            raise HTTPException(404)
        return eng.insights.submit("hrd", rid)

    @r.get("/results/{rid}/compare")
    def get_compare(rid: int):
        from .compare import COMPARE_VERSION, references
        out = _insight(rid, "compare", COMPARE_VERSION)
        out["refs"] = [{k: v for k, v in x.items() if k != "nights"} for x in references(db, eng.s.work_dir, rid)]
        out["survey"] = eng.insights.settings()["survey"]
        return out

    @r.post("/results/{rid}/compare")
    def run_compare(rid: int, body: dict = Body(default={})):
        if not db.one("SELECT id FROM results WHERE id=?", (rid,)):
            raise HTTPException(404)
        ref = body.get("ref", "auto")
        if ref not in ("auto", "survey") and not (isinstance(ref, int) or str(ref).isdigit()):
            raise HTTPException(400, "Unbekannte Referenz")
        return eng.insights.submit("compare", rid, ref=ref if ref in ("auto", "survey") else int(ref))

    # ---- Gerätediagnose (diagnose.py): Nachführung, Fokus, Bildfeld – lokal, ohne Netz ----------------
    @r.get("/results/{rid}/diagnose")
    def get_diagnose(rid: int):
        from .diagnose import DIAG_VERSION
        out = _insight(rid, "diagnose", DIAG_VERSION)
        if out["state"] in ("none", "old"):              # noch nie bzw. mit älterer Version gerechnet: im Hintergrund nachholen
            st = eng.insights.submit("diagnose", rid)
            out = {**out, "state": st["state"], "message": st["message"]}
        return out

    @r.post("/results/{rid}/diagnose")
    def run_diagnose(rid: int):
        if not db.one("SELECT id FROM results WHERE id=?", (rid,)):
            raise HTTPException(404)
        return eng.insights.submit("diagnose", rid)

    @r.get("/diagnose/devices")
    def diagnose_devices():
        """Gerätetrend über alle Nächte (neueste Version je Objekt mit Gerätediagnose)."""
        from .diagnose import device_summary
        return {"devices": device_summary(db)}

    @r.get("/results/{rid}/compare/{name}")
    def compare_file(rid: int, name: str):
        import re as _re
        if not _re.fullmatch(r"(aktuell|referenz)\.jpg|k\d{1,3}\.png", name):
            raise HTTPException(404)
        x = db.one("SELECT project_id FROM results WHERE id=?", (rid,))
        f = x and eng.s.work_dir / f"p{x['project_id']}" / "vergleich" / f"r{rid}" / name
        if not f or not f.exists():
            raise HTTPException(404)
        return FileResponse(f)

    # ---- Asteroiden im Bild (asteroids.py) ---------------------------------------------------------
    @r.get("/results/{rid}/asteroids")
    def get_asteroids(rid: int):
        from .asteroids import ASTEROID_VERSION
        out = _insight(rid, "asteroids", ASTEROID_VERSION)
        out["enabled"] = eng.insights.settings()["small_bodies"]
        return out

    @r.post("/results/{rid}/asteroids")
    def run_asteroids(rid: int):
        if not db.one("SELECT id FROM results WHERE id=?", (rid,)):
            raise HTTPException(404)
        if not eng.insights.settings()["small_bodies"]:
            raise HTTPException(400, "Der Abgleich mit Kleinkörpern (JPL) ist ausgeschaltet.")
        return eng.insights.submit("asteroids", rid)

    @r.get("/results/{rid}/asteroids/{name}")
    def asteroid_file(rid: int, name: str):
        import re as _re
        if not _re.fullmatch(r"a\d{1,3}\.png", name):
            raise HTTPException(404)
        x = db.one("SELECT project_id FROM results WHERE id=?", (rid,))
        f = x and eng.s.work_dir / f"p{x['project_id']}" / "asteroiden" / f"r{rid}" / name
        if not f or not f.exists():
            raise HTTPException(404)
        return FileResponse(f)

    # ---- Analyse-Dashboard über alle Objekte (dashboard.py) ----------------------------------------
    def _filters(device: str = "", night: str = "", year: str = "", q: str = "") -> dict:
        return {"device": device, "night": night, "year": year, "q": q}

    @r.get("/analysis")
    def analysis(device: str = "", night: str = "", year: str = "", q: str = ""):
        return eng.dashboard.summary(_filters(device, night, year, q))

    @r.get("/analysis/universe")
    def analysis_universe(device: str = "", night: str = "", year: str = "", q: str = ""):
        return eng.dashboard.universe(_filters(device, night, year, q))

    def _kinds(body: dict) -> list[str]:
        """Fehlt das Feld, alle Arten; eine leere Auswahl bleibt leer (sonst startete „nichts gewählt“ alles)."""
        from .dashboard import KINDS
        raw = body.get("kinds")
        return list(KINDS) if raw is None else [k for k in raw if k in KINDS]

    @r.post("/analysis/run")
    def analysis_run(body: dict = Body(default={})):
        kinds = _kinds(body)
        scope = body.get("scope", "missing")
        if not kinds or scope not in ("missing", "errors", "all"):
            raise HTTPException(400, "Bitte mindestens eine Auswertung wählen.")
        ids = body.get("ids")
        try:
            return eng.dashboard.batch.start(kinds, scope, [int(i) for i in ids] if ids is not None else None)
        except RuntimeError as e:
            raise HTTPException(409, str(e))

    @r.post("/analysis/plan")
    def analysis_plan(body: dict = Body(default={})):
        kinds = _kinds(body)
        ids = body.get("ids")
        items, skipped = eng.dashboard.batch.plan(kinds, body.get("scope", "missing") if body.get("scope") in ("missing", "errors", "all") else "missing",
                                                  [int(i) for i in ids] if ids is not None else None)
        return {"total": len(items), "per_kind": {k: sum(1 for i in items if i[0] == k) for k in kinds}, "skipped": skipped}

    @r.post("/analysis/pause")
    def analysis_pause(body: dict = Body(default={})):
        return eng.dashboard.batch.pause(bool(body.get("paused", True)))

    @r.post("/analysis/stop")
    def analysis_stop():
        return eng.dashboard.batch.stop()

    @r.post("/analysis/develop")
    def analysis_develop(body: dict = Body(default={})):
        """Fehlende Plate-Solving-Lösungen nachholen: Objekte mit Master-Stack, aber ohne wcs.json neu entwickeln
        (die Lösung entsteht beim Entwickeln; danach misst `auto_depth` die Tiefe von selbst)."""
        if not eng.pl.solver_cfg.enabled:
            raise HTTPException(400, "Kein Plate-Solver eingerichtet – siehe Einstellungen → Bilder.")
        ids = {int(i) for i in body.get("ids") or []}
        jobs = []
        for row in eng.dashboard.base()["rows"]:
            if row.get("imported") or row["solved"] or not row["can_develop"] or (ids and row["id"] not in ids):
                continue
            jobs.append(eng.enqueue(row["id"], {"mode": "develop"}))
        return {"queued": len(jobs)}

    @r.get("/insights")
    def get_insights():
        return eng.insights.settings()

    @r.post("/insights")
    def set_insights(body: dict = Body(...)):
        return eng.insights.save(body)

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
    def set_starnet(request: Request, body: dict = Body(...)):
        security.require_local(request, "Programmpfade setzen")
        p = str(body.get("path") or "").strip()
        if p and not Path(p).is_file():
            raise HTTPException(400, f"Datei nicht gefunden: {p}")
        stars.STARNET.path = p
        eng.save_settings()
        return get_starnet()

    @r.post("/starnet/install")
    async def install_starnet(request: Request, file: UploadFile = File(...)):
        security.require_local(request, "StarNet installieren")
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
    def remove_starnet(request: Request):
        security.require_local(request, "StarNet entfernen")
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
    async def set_graxpert(request: Request, body: dict = Body(...)):
        g = graxpert.GRAXPERT
        if "path" in body and str(body.get("path") or "").strip().strip('"') != (g.path or ""):
            security.require_local(request, "Programmpfade setzen")
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

    @r.post("/reviewer/models")
    def reviewer_models(body: dict = Body({})):
        """Modelle mit Bildeingabe für den KI-Prüfer (auch für eine noch nicht gespeicherte Anbieterwahl). Der gespeicherte
        Schlüssel geht nur an die gespeicherte bzw. die feste OpenRouter-Adresse, sonst wird ohne Schlüssel gefragt."""
        from dataclasses import replace
        from ...core.models import ModelListError, key_for, list_models
        c = eng.review_cfg
        prov = body.get("provider") or c.provider
        base_url = body.get("base_url")
        if prov not in ("lmstudio", "openrouter"):
            raise HTTPException(400, "Erst einen Anbieter wählen.")
        cfg = replace(c, provider=prov, base_url=c.base_url if base_url is None else str(base_url)).resolved()
        saved = c.resolved()
        key = key_for(cfg.base_url, c.api_key, saved.base_url if c.provider == "openrouter" else None,
                      "https://openrouter.ai/api/v1") if prov == "openrouter" else ""
        try:
            return list_models(cfg.base_url, key, "vision")
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
