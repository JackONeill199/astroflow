"""Hintergrundauswertungen fertiger Bilder: „Wie tief schaust du?“ (`depth.py`), Vergleich mit einer Referenz
(`compare.py`) und HR-Diagramm von Sternhaufen (`hrd.py`). Eigener Thread mit Warteschlange – Netzabfragen (Gaia, SIMBAD, DSS, JPL) halten so weder Stacking
noch Entwicklung auf. Ergebnisse landen in `results.stats.depth`, `.compare` bzw. `.hrd` (mit Versionsnummer).

Einstellungen (`data/settings.json` → `insights`): `auto_depth` (nach jeder Entwicklung messen), `simbad`
(Rotverschiebungen/Veränderliche), `survey` (Himmelsdurchmusterung DSS2 als Referenz), `small_bodies` (JPL-Kleinkörper).
Gaia hängt an der bestehenden Einstellung „Fotometrische Farbkalibrierung“ (`storage.gaia`)."""
from __future__ import annotations

import json
import queue
import threading
import time
import traceback

DEFAULTS = {"auto_depth": True, "simbad": True, "survey": True, "small_bodies": True}


class Insights:
    def __init__(self, eng):
        self.eng = eng
        self.q: queue.Queue = queue.Queue()
        self.state: dict[tuple[str, int], dict] = {}    # (art, rid) → {state: queued|running|error, message, t}
        self.lock = threading.Lock()

    # ---- Einstellungen -------------------------------------------------------
    def settings(self) -> dict:
        saved = self.eng.ctx.store.get("insights")
        return {**DEFAULTS, **{k: bool(v) for k, v in saved.items() if k in DEFAULTS}, "gaia": bool(self.eng.s.gaia)}

    def save(self, body: dict) -> dict:
        self.eng.ctx.store.update("insights", {k: bool(body[k]) for k in DEFAULTS if k in body})
        return self.settings()

    # ---- Aufträge ------------------------------------------------------------
    def submit(self, kind: str, rid: int, **opts) -> dict:
        with self.lock:
            cur = self.state.get((kind, rid))
            if cur and cur["state"] in ("queued", "running"):
                return cur
            st = {"state": "queued", "message": "wartet", "t": time.time()}
            self.state[(kind, rid)] = st
        self.q.put((kind, rid, opts))
        return st

    def status(self, kind: str, rid: int) -> dict | None:
        return self.state.get((kind, rid))

    def after_develop(self, rid: int | None):
        """Nach einer Entwicklung automatisch messen (nur gelöste Bilder, Einstellung `auto_depth`, Gaia an);
        bei Sternhaufen danach auch das HR-Diagramm."""
        from .hrd import is_cluster
        cfg = self.settings()
        if rid and cfg["auto_depth"] and cfg["gaia"]:
            row = self.eng.db.one("SELECT r.project_id, p.kind, p.name FROM results r JOIN projects p ON p.id=r.project_id WHERE r.id=?", (rid,))
            if row and (self.eng.s.work_dir / f"p{row['project_id']}" / "wcs.json").exists():
                self.submit("depth", rid)
                if is_cluster(row["kind"], row["name"]):
                    self.submit("hrd", rid)

    def worker(self):
        while not self.eng.stop.is_set():
            try:
                kind, rid, opts = self.q.get(timeout=1)
            except queue.Empty:
                continue
            st = self.state.setdefault((kind, rid), {})
            st.update(state="running", message="läuft", t=time.time())
            try:
                fn = {"depth": self.depth, "compare": self.compare, "hrd": self.hrd}[kind]
                fn(rid, lambda m: st.update(message=m), **opts)
                self.state.pop((kind, rid), None)
            except Exception as e:
                from .compare import CompareError
                from .depth import DepthError
                from .hrd import HrdError
                if not isinstance(e, (DepthError, CompareError, HrdError)):
                    traceback.print_exc()
                try:                                     # erst speichern, dann melden (Abfragen dazwischen sähen sonst nichts)
                    self._store(rid, kind, {"v": 0, "ok": False, "error": str(e)[:300], "erstellt": time.time()})
                finally:
                    st.update(state="error", message=str(e)[:300], t=time.time())

    def _store(self, rid: int, key: str, value: dict):
        self.eng.db.x(f"UPDATE results SET stats=json_set(COALESCE(stats,'{{}}'),'$.{key}',json(?)) WHERE id=?",
                      (json.dumps(value), rid))

    # ---- Auswertungen --------------------------------------------------------
    def depth(self, rid: int, log, **_):
        from . import depth
        r = self.eng.db.one("SELECT r.*, p.name FROM results r JOIN projects p ON p.id=r.project_id WHERE r.id=?", (rid,))
        if not r:
            return
        cfg = self.settings()
        out = depth.compute(self.eng.s.work_dir, r["project_id"], json.loads(r["files"] or "{}"), r["name"],
                            facts=self.eng.ctx.services.get("object_facts"), gaia=cfg["gaia"], simbad=cfg["simbad"],
                            stats=json.loads(r["stats"] or "{}"), log=log)
        out["erstellt"] = time.time()
        self._store(rid, "depth", out)

    def hrd(self, rid: int, log, **_):
        from . import hrd
        r = self.eng.db.one("SELECT r.*, p.name, p.kind FROM results r JOIN projects p ON p.id=r.project_id WHERE r.id=?", (rid,))
        if not r:
            return
        out = hrd.compute(self.eng.s.work_dir, r["project_id"], json.loads(r["files"] or "{}"), r["name"], kind=r["kind"],
                          gaia=self.settings()["gaia"], log=log)
        out["erstellt"] = time.time()
        self._store(rid, "hrd", out)

    def compare(self, rid: int, log, ref="auto", **_):
        from . import compare
        cfg = self.settings()
        out = compare.compute(self.eng.db, self.eng.s.work_dir, rid, ref, survey=cfg["survey"],
                              catalogs=cfg["simbad"] or cfg["small_bodies"], log=log,
                              simbad=cfg["simbad"], small_bodies=cfg["small_bodies"])
        self._store(rid, "compare", out)
