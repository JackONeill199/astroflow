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

    def __init__(self, ctx):
        super().__init__(ctx)
        from .api import Engine
        from .services import PictureSolver
        self.eng = Engine(ctx)
        ctx.services["plate_solver"] = PictureSolver(self.eng.pl.solver_cfg, ctx.settings.work_dir)
        ctx.services["project_picture"] = self.project_picture
        ctx.services["project_solution"] = self.project_solution

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
        r = p and db.one("SELECT * FROM results WHERE project_id=? ORDER BY id DESC LIMIT 1", (pid,))
        if not r:
            return None
        files = json.loads(r["files"] or "{}")
        img = files.get("jpg") or files.get("png")
        if not img or not Path(img).exists():
            return None
        from .objects import KIND_LABEL
        return {"name": p["name"], "kind": p["kind"], "kind_label": KIND_LABEL.get(p["kind"] or "generic"),
                "image": img, "result_id": r["id"], "exposure": r["exposure"], "n_frames": r["n_frames"],
                "ra": p["ra"], "dec": p["dec"], "files": files}

    def project_solution(self, pid: int) -> dict | None:
        """Objekte im neuesten Bild eines Objekts aus der gespeicherten Stack-Lösung (None = keine Lösung gespeichert)."""
        from .services import project_solution
        pic = self.project_picture(pid)
        return pic and project_solution(self.ctx.settings.work_dir, pid, pic["files"])
