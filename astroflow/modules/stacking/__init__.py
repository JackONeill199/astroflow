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
    scripts = ("chartdeco.js", "live.js", "stacking.js", "tonight.js", "depth.js", "hrd.js", "asteroids.js", "skyquality.js", "diagnose.js", "analysis.js", "picanalysis.js", "dashboard.js")
    styles = ("chartdeco.css", "depth.css", "hrd.css", "asteroids.css", "analysis.css", "dashboard.css", "statistics.css", "housekeeping.css", "library.css", "stages.css", "tutor.css", "versions.css", "live.css", "tonight.css", "skyquality.css", "diagnose.css", "frames.css", "picanalysis.css")

    def __init__(self, ctx):
        super().__init__(ctx)
        from .api import Engine
        from .services import PictureSolver
        self.eng = Engine(ctx)
        try:                                                      # Nachtdatum nach Ortszeit (Issue #39), nur bei Bedarf
            from .nights import migrate
            moved = migrate(self.eng.db, ctx.store, ctx.services.get("sites"))
            if moved and moved["frames"]:
                print(f"Nachtdatum nach Ortszeit: {moved['frames']} Aufnahmen in {len(moved['projects'])} Projekten angepasst.")
        except Exception:
            import traceback
            traceback.print_exc()
        ctx.services["plate_solver"] = PictureSolver(self.eng.pl.solver_cfg, ctx.settings.work_dir)
        ctx.services["project_picture"] = self.project_picture
        ctx.services["project_solution"] = self.project_solution
        ctx.services["project_frames"] = self.project_frames
        ctx.services["observation_log"] = self.observation_log
        ctx.services["catalog_resolve"] = catalog_resolve
        ctx.services["catalog_objects"] = catalog_objects
        self.eng.pl.on_stacked = self._stacked
        from .site_watch import SiteWatch                        # Ortsliste geändert → betroffene Nächte neu rechnen
        self.site_watch = SiteWatch(self.eng)
        ctx.on("sites.changed", self.site_watch.changed)
        ctx.services["asteroid_tally"] = self.asteroid_tally       # Sage-Erfolge: nachgewiesene Asteroiden
        ctx.services["site_sky"] = self.site_sky                  # Ortsliste: Himmelsqualität je Standort
        ctx.services["night_zero_points"] = self.night_zero_points  # Nullpunkte je Nacht/Gerät (SkyTrace: Satellitenhelligkeit)
        if isinstance(ctx.services.get("night_sources"), list):   # Einstellungen → Standorte: Nachtliste
            ctx.services["night_sources"].append(self.nights)

    def _stacked(self, pid: int, rid: int):
        p = self.eng.db.one("SELECT name FROM projects WHERE id=?", (pid,))
        self.ctx.emit("stacking.stacked", project_id=pid, result_id=rid, name=p["name"] if p else "")

    def router(self):
        from .api import build_router
        return build_router(self.eng)

    def start(self):
        threading.Thread(target=self.eng.worker, daemon=True).start()
        threading.Thread(target=self.eng.watcher, daemon=True).start()
        threading.Thread(target=self.eng.insights.worker, daemon=True, name="stacking-insights").start()
        threading.Thread(target=self.eng.picture_solve.backfill, daemon=True, name="stacking-picture-backfill").start()

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

    def asteroid_tally(self) -> dict:
        """Verschiedene nachgewiesene Asteroiden über alle Bilder und der schwächste (mag)."""
        from .asteroids import tally
        return tally(self.eng.db)

    def site_sky(self) -> dict:
        """Himmelsqualität je Standort-ID (Mittel über die Nächte) für die Ortsliste – ohne Lichtrichtung (schnell)."""
        from .skyquality import summary
        return {s["id"]: {k: s[k] for k in ("sqm", "bortle", "seeing", "n_nights", "n_site_nights")}
                for s in summary(self.eng.db, with_dirs=False)["sites"] if s["id"]}

    def night_zero_points(self) -> dict:
        """Photometrische Nullpunkte (mag für 1 ADU/s, Luminanz, über der Atmosphäre) aus den Gaia-geeichten Stacks:
        `nights` = {"<Nacht>|<Gerät>": zp} (Median über die Objekte der Nacht), `devices` = {Gerät: zp} (ab 2 Nächten).
        Gerät = "<INSTRUME>|<Gain>" wie in `sky_nights.device`. Nur Nächte mit eigener Gaia-Eichung (`zp_source == stack`)."""
        import json as _json
        import statistics

        from .skyquality import ensure_table
        ensure_table(self.eng.db)
        by_night: dict[str, list] = {}
        by_dev: dict[str, list] = {}
        for r in self.eng.db.q("SELECT s.night, s.device, s.data FROM sky_nights s JOIN projects p ON p.id=s.project_id"):
            d = _json.loads(r["data"] or "{}")
            if d.get("zp_source") == "stack" and isinstance(d.get("zp"), (int, float)):
                by_night.setdefault(f"{r['night']}|{r['device']}", []).append(d["zp"])
        for k, v in by_night.items():
            by_dev.setdefault(k.split("|", 1)[1], []).append(statistics.median(v))
        return {"nights": {k: round(statistics.median(v), 3) for k, v in by_night.items()},
                "devices": {k: round(statistics.median(v), 3) for k, v in by_dev.items() if len(v) >= 2}}

    def nights(self) -> list[dict]:
        """Aufnahmenächte mit Objektnamen (für die Nachtliste unter Einstellungen → Standorte)."""
        rows = self.eng.db.q("SELECT f.night, p.name, COUNT(*) n FROM frames f JOIN projects p ON p.id=f.project_id "
                             "WHERE f.kind='light' AND f.night IS NOT NULL GROUP BY f.night, p.id ORDER BY f.night")
        return [{"night": r["night"], "label": f"{r['name']} ({r['n']} Aufnahmen)", "module": "Bilder"} for r in rows]

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
                                                          "instruments": set(), "filters": set(), "fwhm": [], "first": None, "last": None, "owl": False})
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
                    n["owl"] = n["owl"] or _after_two(d)
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
        if pic and pic["imported"]:                               # fertiges Bild: Lösung aus dem Import-Plate-Solving
            from .picture_solve import stored_solution
            return stored_solution(self.ctx.settings.work_dir, pid)
        return pic and project_solution(self.ctx.settings.work_dir, pid, pic["files"])


def _after_two(date_obs: str) -> bool:
    """Aufnahme zwischen 2 und 6 Uhr Ortszeit des Rechners (DATE-OBS ohne Zeitzone gilt als UTC)."""
    from datetime import datetime, timezone
    try:
        t = datetime.fromisoformat(str(date_obs).replace("Z", "+00:00"))
    except ValueError:
        return False
    return 2 <= (t if t.tzinfo else t.replace(tzinfo=timezone.utc)).astimezone().hour < 6


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
