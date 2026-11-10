"""Ortsliste geändert (Ereignis `sites.changed`, z. B. Nacht in SkyTrace einem Ort zugewiesen) → Nächte mit anderem Ort neu rechnen.

- Himmelsqualität: `sky_nights.data.site_key` (Ort beim Rechnen) gegen den aktuellen Ort der Nacht; ältere Zeilen ohne
  Fingerabdruck vergleichen die Orts-ID. Neu gerechnet wird das ganze Objekt im Hintergrund (`SkyRunner.queue`).
- Gerätediagnose: `nights[].site` (Ort beim Rechnen, wie `tutor.pick_site`) gegen den aktuellen Ort; ältere Diagnosen
  ohne Ort nur, wenn die Nacht jetzt eine automatisch gepflegte Einzelnacht hat (`by`, z. B. SkyTrace).
- Tutor: Der Ort steckt im Cacheschlüssel (`build_report`), der Bericht rechnet beim nächsten Öffnen selbst neu.
FITS-Orte hängen nicht an der Ortsliste und bleiben unberührt."""
from __future__ import annotations

import json
import os
import threading
import traceback

DELAY_S = 2.0                      # mehrere Änderungen kurz hintereinander (Nachtsuche übernimmt viele Nächte) = ein Durchgang


def _cached(site_for):
    seen: dict = {}

    def get(night: str):
        if night not in seen:
            try:
                seen[night] = site_for(night=night)
            except Exception:
                seen[night] = None
        return seen[night]
    return get


def stale_sky(db, site_for) -> list[int]:
    """Objekte mit mindestens einer Nacht, deren Ort sich seit der Berechnung geändert hat."""
    from .skyquality import SKY_VERSION, ensure_table, site_key
    ensure_table(db)
    get, out = _cached(site_for), set()
    for r in db.q("SELECT project_id, night, site_id, data FROM sky_nights WHERE version=?", (SKY_VERSION,)):
        if r["project_id"] in out or str(r["site_id"] or "").startswith("fits:"):
            continue
        cur = get(r["night"])
        try:
            old = json.loads(r["data"] or "{}").get("site_key")
        except ValueError:
            old = None
        if old is not None:
            stale = old != site_key(cur)
        else:
            stale = (r["site_id"] or None) != ((cur or {}).get("id") or None)
        if stale:
            out.add(r["project_id"])
    return sorted(out)


def stale_diagnose(db, site_for) -> list[int]:
    """Ergebnisse, deren Gerätediagnose mit einem anderen Ort gerechnet wurde (Höhe des Objekts → Fokusbewertung)."""
    from .diagnose import DIAG_VERSION
    get, out = _cached(site_for), []
    for r in db.q("SELECT id, stats FROM results WHERE json_extract(stats,'$.diagnose.v')=?", (DIAG_VERSION,)):
        try:
            d = json.loads(r["stats"] or "{}").get("diagnose") or {}
        except ValueError:
            continue
        if not d.get("ok"):
            continue
        for n in d.get("nights") or []:
            if n.get("site_source") == "header" or not n.get("night"):
                continue
            cur = get(n["night"])
            if "site" in n:
                now = [round(float(cur["lat"]), 3), round(float(cur["lon"]), 3)] if cur else None
                stale = n["site"] != now if n.get("site_source") in ("sites", None) else False
            else:
                stale = bool((cur or {}).get("by"))
            if stale:
                out.append(r["id"])
                break
    return out


class SiteWatch:
    """Empfänger von `sites.changed`: kehrt sofort zurück, rechnet nach kurzer Pause in einem eigenen Thread."""

    def __init__(self, eng):
        self.eng = eng
        self._timer: threading.Timer | None = None
        self._lock = threading.Lock()
        self.last: dict = {}

    def changed(self, **_):
        with self._lock:
            if self._timer:
                self._timer.cancel()
            self._timer = threading.Timer(DELAY_S, self.run)
            self._timer.daemon = True
            self._timer.start()

    def run(self) -> dict:
        from . import skyquality
        site_for = self.eng.ctx.services.get("site_for")
        if not site_for:
            return {}
        out = {"sky": [], "diagnose": []}
        try:
            if skyquality.auto_enabled():
                out["sky"] = stale_sky(self.eng.db, site_for)
                for pid in out["sky"]:
                    self.eng.sky.queue(pid)
            if os.environ.get("ASTROFLOW_DIAG", "1") != "0":
                out["diagnose"] = stale_diagnose(self.eng.db, site_for)
                for rid in out["diagnose"]:
                    self.eng.insights.submit("diagnose", rid)
        except Exception:
            traceback.print_exc()
        self.last = out
        return out
