"""Fertig importierten Bildern ihr Objekt geben: Plate-Solving im Hintergrund (nur lokale Solver wie ASTAP – der
Online-Dienst nova.astrometry.net läuft hier nie). Das Original bleibt unverändert; gelöst wird die Anzeigeversion.

Ablauf je Bild: erst mit den Bildfeldern der erkannten Smart-Teleskope (EXIF/Dateiname), dann blind über den ganzen
Himmel (ASTAP `-fov 0`), jeweils zeitlich begrenzt. Sternreiche Webbilder (Schmalband, verkleinerte Sterne) brauchen
mehr Sterne als die Rohbild-Lösung (`-s 1000`). Scheitert alles, gilt das Bild still als „nicht bestimmt“."""
from __future__ import annotations

import json
import os
import queue
import re
import shutil
import tempfile
import threading
import time
import traceback
from dataclasses import replace
from pathlib import Path

from . import objects, platesolve

# Bildfeld (lange, kurze Seite in Grad) der Smart-Teleskope – passt nur, solange das Bild nicht beschnitten ist
DEVICE_FOV = (("seestar s30", (2.17, 1.21)), ("seestar", (1.29, 0.73)), ("vespera ii", (2.5, 1.4)),
              ("vespera pro", (1.6, 1.6)), ("vespera", (1.6, 0.9)), ("stellina", (1.05, 0.7)))
ATTEMPT_TIMEOUT = 120     # s je ASTAP-Aufruf
BUDGET = 300              # s für alle Versuche eines Bildes zusammen
# dasselbe Ergebnis, das `project_picture` (Sage) zeigt: bei Teleskop-Serien die höchste Sequenz, nicht der letzte Import
LATEST = "COALESCE(json_extract(stats,'$.imported.series.sequence'),0) DESC, id DESC"
MSG_NONE = "Objekt nicht bestimmt – das Sternmuster wurde nicht erkannt (bei kleinen oder stark bearbeiteten Bildern häufig)."
MSG_NO_SOLVER = "Kein Plate-Solver eingerichtet – ASTAP unter Einstellungen → Bilder einrichten."


def enabled() -> bool:
    """Automatisches Lösen nach dem Import (Tests schalten es über die Umgebung ab, wie GraXpert/Gaia)."""
    return os.environ.get("ASTROFLOW_PICTURE_SOLVE", "1") != "0"


def device_of(info: dict) -> str:
    """Gerätename aus Teleskop-Serie, EXIF oder Dateiname (Seestar: „Stacked_<n>_<Objekt>_…“)."""
    text = " ".join(str(x or "") for x in ((info.get("series") or {}).get("instrument"), info.get("make"), info.get("model"),
                                           info.get("filename"))).lower()
    if re.match(r"stacked_\d+_", str(info.get("filename") or ""), re.I) and "seestar" not in text:
        text += " seestar"
    return text


def attempts(info: dict, width: int, height: int) -> list[tuple[float, int]]:
    """(Bildfeldhöhe in Grad | 0 = ASTAP schätzt selbst, Höchstzahl Sterne) in der Reihenfolge der Versuche."""
    dev = device_of(info)
    out = []
    for key, (long, short) in DEVICE_FOV:
        if key in dev:
            out.append((long if height >= width else short, 500))
            break
    return out + [(0.0, 1000), (0.0, 500)]


def main_object(ra: float, dec: float, fov_w: float, fov_h: float) -> objects.CatObject | None:
    """Hauptobjekt im Bild wie beim Scan gelöster Rohbilder: eingebauter Katalog, sonst großer ASTAP-Katalog."""
    return objects.nearest(ra, dec) or objects.nearest_extended(ra, dec, 0.35 * min(fov_w, fov_h))


def solution_path(work_dir: Path, pid: int) -> Path:
    return Path(work_dir) / f"p{int(pid)}" / "picture_solution.json"


def stored_solution(work_dir: Path, pid: int) -> dict | None:
    """Gespeicherte Lösung samt Objekten im Bild (Positionen im angezeigten Bild) – für Sage (`project_solution`)."""
    try:
        return json.loads(solution_path(work_dir, pid).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def solve_picture(cfg: platesolve.SolverConfig, image: Path, info: dict, work: Path, budget: float = BUDGET,
                  log=lambda m: None) -> dict | None:
    """Lösung (`services.describe`) des angezeigten Bildes oder None. Wirft nur bei fehlendem Solver."""
    from astropy.wcs import WCS

    from .services import describe, load_fits_oriented
    if not cfg.enabled:
        raise platesolve.SolveError(MSG_NO_SOLVER)
    L, _ = load_fits_oriented(Path(image))
    h, w = L.shape
    work.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(dir=work))
    t0 = time.time()
    try:
        tries = attempts(info, w, h)
        for n, (fov, stars) in enumerate(tries):
            left = budget - (time.time() - t0)
            if left < 10:
                break
            log(f"Versuch {n + 1}/{len(tries)}: " + (f"Bildfeld {fov:.2f}°" if fov else "blind über den ganzen Himmel"))
            c = replace(cfg, timeout=int(min(cfg.timeout or ATTEMPT_TIMEOUT, ATTEMPT_TIMEOUT, left)))
            try:
                hdr = platesolve.solve_image(c, L, tmp, None, None, fov or None, stars=stars)
            except platesolve.SolveError as e:
                log(str(e))
                continue
            sol = describe(WCS({**hdr, "NAXIS": 2, "NAXIS1": w, "NAXIS2": h}, naxis=2), w, h, cfg.chain()[0])
            sol["seconds"] = round(time.time() - t0, 1)
            return sol
        return None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


class PictureSolveRunner:
    """Eigener Hintergrund-Thread: blockiert weder den Upload noch die Stacking-Warteschlange. Zustand je Projekt in
    `projects.options.picture_solve` = {state: queued|running|done|failed, message, at, solution?, object?}."""

    def __init__(self, eng):
        self.eng = eng
        self.q: queue.Queue = queue.Queue()
        self.lock = threading.Lock()
        self.thread: threading.Thread | None = None
        self.current: dict | None = None
        self.recent: list[dict] = []

    # ---- Zustand ----------------------------------------------------------
    def _set(self, pid: int, state: dict):
        self.eng.db.x("UPDATE projects SET options=json_set(COALESCE(options,'{}'),'$.picture_solve',json(?)) WHERE id=?",
                      (json.dumps({**state, "at": time.time()}), pid))

    def _project(self, pid: int) -> tuple[dict | None, dict]:
        p = self.eng.db.one("SELECT * FROM projects WHERE id=?", (pid,))
        return p, json.loads((p or {}).get("options") or "{}")

    def needs_solve(self, pid: int) -> bool:
        p, opt = self._project(pid)
        return bool(p and opt.get("input_type") == "picture" and not p["obj_id"] and not opt.get("gallery_parent"))

    # ---- Aufträge ---------------------------------------------------------
    def after_import(self, pid: int, rid: int, name: str):
        """Nach dem Import: unbekanntes Objekt erst lösen, dann `stacking.imported` melden (Sage bekommt so schon das
        Objekt). Ohne Solver bzw. mit bekanntem Objekt sofort melden wie bisher."""
        if enabled() and self.eng.pl.solver_cfg.enabled and self.needs_solve(pid):
            self.queue(pid, notify=True)
        else:
            self.eng.ctx.emit("stacking.imported", project_id=pid, result_id=rid, name=name)

    def queue(self, pid: int, notify: bool = False) -> bool:
        with self.lock:
            _, opt = self._project(pid)
            if (opt.get("picture_solve") or {}).get("state") in ("queued", "running"):
                return False
            self._set(pid, {"state": "queued", "message": "wartet auf das Plate-Solving"})
            self.q.put((pid, notify))
            if self.thread is None:
                self.thread = threading.Thread(target=self.worker, daemon=True, name="stacking-picture-solve")
                self.thread.start()
        return True

    def backfill(self):
        """Einmalig beim Start: früher importierte Bilder ohne Objekt, die noch nie gelöst wurden. Abgebrochene
        Versuche (Neustart während „running“) werden ebenfalls nachgeholt; gescheiterte nur per Knopf."""
        if not (enabled() and self.eng.pl.solver_cfg.enabled):
            return
        for p in self.eng.db.q("SELECT id, options FROM projects WHERE obj_id IS NULL "
                               "AND json_extract(options,'$.input_type')='picture' "
                               "AND json_extract(options,'$.gallery_parent') IS NULL ORDER BY id"):
            st = (json.loads(p["options"] or "{}").get("picture_solve") or {}).get("state")
            if st in (None, "queued", "running"):
                if st:                                            # verwaisten Zustand freigeben
                    self._set(p["id"], {"state": "failed", "message": "unterbrochen"})
                self.queue(p["id"], notify=True)

    def worker(self):
        while True:
            try:
                pid, notify = self.q.get(timeout=1)
            except queue.Empty:
                with self.lock:                                   # Thread endet; `queue` startet bei Bedarf neu
                    if self.q.empty():
                        self.thread = None
                        return
                continue
            try:
                self.run(pid, notify)
            except Exception:
                traceback.print_exc()                             # Aktivität nicht als „läuft“ stehen lassen
                self._finish(pid, {"state": "failed", "message": "Plate-Solving fehlgeschlagen (Details im Server-Protokoll)."})

    def run(self, pid: int, notify: bool = False) -> dict | None:
        db = self.eng.db
        p, opt = self._project(pid)
        r = p and db.one(f"SELECT * FROM results WHERE project_id=? ORDER BY {LATEST} LIMIT 1", (pid,))
        if not r:
            return None
        files = json.loads(r["files"] or "{}")
        image = files.get("jpg") or files.get("png")
        info = json.loads(r["stats"] or "{}").get("imported") or opt.get("imported") or {}
        self.current = {"pid": pid, "name": p["name"], "started": time.time(), "message": "Sternmuster wird gesucht …"}
        self._set(pid, {"state": "running", "message": "Sternmuster wird gesucht …"})

        def log(m):
            self.current["message"] = m

        sol = None
        try:
            if image and Path(image).exists():
                sol = solve_picture(self.eng.pl.solver_cfg, Path(image), info, self.eng.s.work_dir / "picture_solve", log=log)
        except platesolve.SolveError:
            self._finish(pid, {"state": "failed", "message": MSG_NO_SOLVER})
            return None
        if not sol:
            self._finish(pid, {"state": "failed", "message": MSG_NONE})
        else:
            self._apply(pid, sol)
        if notify:
            p = db.one("SELECT name FROM projects WHERE id=?", (pid,))
            if p:
                self.eng.ctx.emit("stacking.imported", project_id=pid, result_id=r["id"], name=p["name"])
        return sol

    def _finish(self, pid: int, state: dict):
        self._set(pid, state)
        cur = self.current or {"started": time.time()}
        self.recent = ([{**cur, "pid": pid, "state": "done" if state["state"] == "done" else "error",
                         "message": state["message"], "finished": time.time()}] + self.recent)[:10]
        self.current = None

    def _apply(self, pid: int, sol: dict):
        """Lösung speichern; Objekt, Koordinaten und Name setzen (vom Nutzer vergebene Namen bleiben)."""
        path = solution_path(self.eng.s.work_dir, pid)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(sol), encoding="utf-8")
        brief = {k: sol[k] for k in ("ra", "dec", "fov_w", "fov_h", "scale", "rotation", "solver", "seconds") if k in sol}
        obj = main_object(sol["ra"], sol["dec"], sol["fov_w"], sol["fov_h"])
        p, opt = self._project(pid)
        if not p:
            return
        with self.eng.db.conn() as c:
            if obj and not p["obj_id"]:
                name = p["name"] if opt.get("name_custom") else obj.display
                c.execute("UPDATE projects SET obj_id=?, kind=?, ra=?, dec=?, name=? WHERE id=?",
                          (obj.id, obj.kind, obj.ra, obj.dec, name, pid))
            elif p["ra"] is None:
                c.execute("UPDATE projects SET ra=?, dec=? WHERE id=?", (sol["ra"], sol["dec"], pid))
        self._finish(pid, {"state": "done", "solution": brief, "object": obj.display if obj else None,
                           "message": f"Bestimmt: {obj.display}" if obj else "Himmelsposition bestimmt, kein Katalogobjekt in der Bildmitte"})

    # ---- Aktivität (gemeinsames Format) -------------------------------------
    def activity(self) -> list[dict]:
        out = []
        cur = self.current
        if cur:
            out.append({"key": f"stacking:solve:{cur['pid']}", "title": cur["name"], "kind": "Objekt bestimmen",
                        "detail": cur["message"], "state": "running", "progress": None, "started": cur["started"],
                        "finished": None, "href": f"#/p/{cur['pid']}"})
        for x in self.recent:
            out.append({"key": f"stacking:solve:{x['pid']}:{int(x['finished'])}", "title": x.get("name") or "Bild",
                        "kind": "Objekt bestimmen", "detail": x["message"], "state": x["state"], "progress": 1,
                        "started": x.get("started") or x["finished"], "finished": x["finished"], "href": f"#/p/{x['pid']}"})
        return out

    def busy(self) -> int:
        return int(self.current is not None) + self.q.qsize()
