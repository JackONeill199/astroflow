"""Stacking: FITS-Rohbilder → fertiges Astrofoto (Kernmodul von AstroFlow)."""
from __future__ import annotations

import json
import threading
from pathlib import Path

from ...core.modules import BaseModule


class Module(BaseModule):
    id = "stacking"
    name = "Bilder"
    description = "Rohbilder automatisch kalibrieren, stacken und zum fertigen Astrofoto entwickeln."
    icon = "grid"
    scripts = ("stacking.js",)
    styles = ("statistics.css", "housekeeping.css")

    def __init__(self, ctx):
        super().__init__(ctx)
        from .api import Engine
        from .services import PictureSolver
        self.eng = Engine(ctx)
        ctx.services["plate_solver"] = PictureSolver(self.eng.pl.solver_cfg, ctx.settings.work_dir)
        ctx.services["project_picture"] = self.project_picture
        ctx.services["project_solution"] = self.project_solution
        ctx.services["project_frames"] = self.project_frames
        ctx.services["observation_log"] = self.observation_log
        ctx.services["catalog_resolve"] = catalog_resolve
        ctx.services["catalog_objects"] = catalog_objects
        self.eng.pl.on_stacked = self._stacked

    def _stacked(self, pid: int, rid: int):
        p = self.eng.db.one("SELECT name FROM projects WHERE id=?", (pid,))
        self.ctx.emit("stacking.stacked", project_id=pid, result_id=rid, name=p["name"] if p else "")

    def router(self):
        from .api import build_router
        return build_router(self.eng)

    def start(self):
        threading.Thread(target=self.eng.worker, daemon=True).start()
        threading.Thread(target=self.eng.watcher, daemon=True).start()

    def stop(self):
        self.eng.stop.set()

    def status(self) -> dict:
        return self.eng.status()

    def activity(self, limit: int = 30) -> list[dict]:
        return self.eng.activity(limit)

    def project_picture(self, pid: int) -> dict | None:
        """Neuestes Ergebnis eines Objekts für andere Module (z. B. Sage erzählt dazu eine Geschichte)."""
        db = self.eng.db
        p = db.one("SELECT * FROM projects WHERE id=?", (pid,))
        r = p and db.one("SELECT * FROM results WHERE project_id=? ORDER BY COALESCE(json_extract(stats,'$.imported.series.sequence'),0) DESC,id DESC LIMIT 1", (pid,))
        if not r:
            return None
        files = json.loads(r["files"] or "{}")
        img = files.get("jpg") or files.get("png")
        if not img or not Path(img).exists():
            return None
        from .objects import KIND_LABEL
        return {"name": p["name"], "kind": p["kind"], "kind_label": KIND_LABEL.get(p["kind"] or "generic"),
                "image": img, "result_id": r["id"], "exposure": r["exposure"], "n_frames": r["n_frames"],
                "imported": json.loads(p["options"] or "{}").get("input_type") == "picture",
                "ra": p["ra"], "dec": p["dec"], "files": files}

    def project_frames(self, pid: int) -> list[dict]:
        """Vorhandene Light-Aufnahmen eines Objekts (auch verworfene) für andere Module, z. B. SkyTrace."""
        rows = self.eng.db.q("SELECT id, path, night, status FROM frames WHERE project_id=? AND kind='light' AND deleted=0 "
                             "ORDER BY id", (pid,))
        return [dict(r) for r in rows if Path(r["path"]).exists()]

    def observation_log(self) -> list[dict]:
        """Aufnahmeprotokoll aller Objekte für Sage (persönliches Gedächtnis): Nächte mit Frames, Belichtung, Instrument,
        Filter, Bildqualität; dazu die entwickelten Versionen mit gemessenem Objekt-SNR. Gilt auch für gelöschte Rohbilder."""
        db = self.eng.db
        out = []
        for p in db.q("SELECT id, name, obj_id, kind, ra, dec, created FROM projects WHERE json_extract(options,'$.gallery_parent') IS NULL ORDER BY id"):
            nights: dict[str, dict] = {}
            for f in db.q("SELECT night, info, metrics, status, stacked FROM frames WHERE project_id=? AND kind='light'", (p["id"],)):
                info, met = json.loads(f["info"] or "{}"), json.loads(f["metrics"] or "{}")
                n = nights.setdefault(f["night"] or "?", {"night": f["night"], "frames": 0, "used": 0, "exposure_s": 0.0, "used_s": 0.0,
                                                          "instruments": set(), "filters": set(), "fwhm": [], "first": None, "last": None})
                exp = float(info.get("exptime") or 0)
                n["frames"] += 1
                n["exposure_s"] += exp
                if f["stacked"] or f["status"] == "accepted":
                    n["used"] += 1
                    n["used_s"] += exp
                if info.get("instrument"):
                    n["instruments"].add(info["instrument"])
                if info.get("filter"):
                    n["filters"].add(info["filter"])
                if met.get("fwhm"):
                    n["fwhm"].append(float(met["fwhm"]))
                d = info.get("date_obs")
                if d:
                    n["first"] = min(n["first"] or d, d)
                    n["last"] = max(n["last"] or d, d)
            sessions = []
            for n in sorted(nights.values(), key=lambda n: n["night"] or ""):
                fw = sorted(n.pop("fwhm"))
                sessions.append({**n, "instruments": sorted(n["instruments"]), "filters": sorted(n["filters"]),
                                 "fwhm_px": round(fw[len(fw) // 2], 2) if fw else None})
            results = []
            for r in db.q("SELECT id, created, n_frames, exposure, stats FROM results WHERE project_id=? ORDER BY id", (p["id"],)):
                st = json.loads(r["stats"] or "{}")
                q = st.get("quality") or {}
                results.append({"id": r["id"], "created": r["created"], "n_frames": r["n_frames"], "exposure_s": r["exposure"],
                                "score": q.get("score"), "snr": (q.get("measure") or {}).get("snr"), "fwhm_px": st.get("fwhm_px"),
                                "profile": st.get("profile"), "palette": st.get("palette")})
            out.append({**dict(p), "sessions": sessions, "results": results})
        return out

    def project_solution(self, pid: int) -> dict | None:
        """Objekte im neuesten Bild eines Objekts aus der gespeicherten Stack-Lösung (None = keine Lösung gespeichert)."""
        from .services import project_solution
        pic = self.project_picture(pid)
        return pic and project_solution(self.ctx.settings.work_dir, pid, pic["files"])


def catalog_resolve(name: str) -> dict | None:
    """Objekt im eingebauten bzw. ASTAP-Katalog (für Sage: sofortiger Eintrag ohne Netz)."""
    from . import objects
    c = objects.resolve_name(name)
    return c and {"id": c.id, "display": c.display, "ra": c.ra, "dec": c.dec, "kind": c.kind, "size": c.size,
                  "aliases": list(c.aliases), "kind_label": objects.KIND_LABEL.get(c.kind, "")}


def catalog_objects() -> list[dict]:
    """Eingebauter Katalog (helle Deep-Sky-Objekte) – für Beobachtungsvorschläge."""
    from . import objects
    return [{"id": c.id, "display": c.display, "ra": c.ra, "dec": c.dec, "kind": c.kind, "size": c.size,
             "kind_label": objects.KIND_LABEL.get(c.kind, "")} for c in objects.CATALOG]
