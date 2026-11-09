"""Gespeichertes Nachtdatum der Aufnahmen (`frames.night`) an die Ortszeit-Regel anpassen (core/sites.night_of, Issue #39).

Bis 0.53 galt überall „UTC − 12 h“. Seitdem gilt Mittag bis Mittag Ortszeit; in Europa/Afrika (Ortszeit ≤ 3 h neben UTC)
bleibt die Grenze bei 12 Uhr UTC – dort ändert sich nichts. Beim Start werden die Nächte aus `DATE-OBS` (+ Ort im FITS-Kopf)
neu bestimmt, aber nur, wenn sich die Grundlage geändert hat (Regel, Ortsliste, Rechnerzeitzone; Signatur in
settings.json → `nights`). Idempotent; Rohdaten und Ergebnisse bleiben unberührt. Aufnahmen ohne DATE-OBS (Nacht aus der
Dateizeit) behalten ihr Datum. Kometenprojekte (Nacht im Schlüssel) bekommen den Schlüssel der neuen Nacht. Für Projekte
mit geänderten Nächten wird die Himmelsqualität je Nacht verworfen (sie trug das alte Datum; „Fehlende berechnen“ holt sie nach).
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json

from ...core import sites
from .fits_io import parse_date

RULE = 1                 # erhöhen, wenn sich die Regel in core/sites.night_of ändert
SECTION = "nights"


def signature(book) -> str:
    """Alles, wovon das Nachtdatum außer der Aufnahme selbst abhängt."""
    data = book.data() if book is not None else {}
    year = dt.datetime.now(dt.timezone.utc).year
    zone = [str(dt.datetime(year, m, 15, 12, tzinfo=dt.timezone.utc).astimezone().utcoffset()) for m in (1, 7)]
    raw = {"rule": RULE, "compat": sites.COMPAT_H, "zone": zone, "default": data.get("default"),
           "sites": sorted((str(s.get("id")), s.get("lon")) for s in data.get("list") or [] if isinstance(s, dict)),
           "periods": sorted((str(p.get("site_id")), p.get("from") or "", p.get("to") or "") for p in data.get("periods") or []
                             if isinstance(p, dict))}
    return hashlib.sha256(json.dumps(raw, sort_keys=True, default=str).encode()).hexdigest()[:16]


def _lon(site) -> float | None:
    try:
        s = json.loads(site) if isinstance(site, str) else site
        return float(s[1]) if isinstance(s, (list, tuple)) and len(s) >= 2 and s[1] is not None else None
    except (TypeError, ValueError):
        return None


def renight(db, book=None) -> dict:
    """Nacht aller Aufnahmen mit DATE-OBS neu bestimmen; nur abweichende Zeilen werden geschrieben."""
    rows = db.q("SELECT id, project_id, night, json_extract(info,'$.date_obs') d, json_extract(info,'$.site') s FROM frames "
                "WHERE json_extract(info,'$.date_obs') IS NOT NULL")
    changes, projects = [], set()
    for r in rows:
        when = parse_date(r["d"])
        if when is None:
            continue
        night = sites.night_of(when, lon=_lon(r["s"]), book=book)
        if night and night != r["night"]:
            changes.append((night, r["id"]))
            if r["project_id"] is not None:
                projects.add(int(r["project_id"]))
    if changes:
        db.many("UPDATE frames SET night=? WHERE id=?", changes)
        _comet_keys(db, projects)
    if projects:
        try:                                     # Tabelle entsteht erst mit der Himmelsqualität
            db.many("DELETE FROM sky_nights WHERE project_id=?", [(p,) for p in sorted(projects)])
        except Exception:
            pass
    return {"frames": len(changes), "projects": sorted(projects)}


def _comet_keys(db, projects: set[int]) -> None:
    """Kometenprojekte tragen die Nacht im Schlüssel (`scanner.assign_project`: KOMET:<name>|<nacht>|<kamera>), neue
    Aufnahmen werden nur über ihn zugeordnet. Liegen alle Lights eines Projekts jetzt in einer anderen Nacht, Schlüssel
    nachziehen – außer ein anderes Projekt hat den neuen Schlüssel schon (dann bleibt alles, wie es ist; nichts wird
    zusammengelegt)."""
    from .scanner import COMET_KEY
    for pid in sorted(projects):
        p = db.one("SELECT key FROM projects WHERE id=?", (pid,))
        parts = (p["key"] or "").split("|") if p else []
        if len(parts) != 3 or not parts[0].startswith(COMET_KEY):
            continue
        nights = {r["night"] for r in db.q("SELECT DISTINCT night FROM frames WHERE project_id=? AND kind='light'", (pid,))}
        if len(nights) != 1:
            continue
        night = next(iter(nights))
        key = f"{parts[0]}|{night}|{parts[2]}"
        if night and key != p["key"] and not db.one("SELECT id FROM projects WHERE key=?", (key,)):
            db.x("UPDATE projects SET key=? WHERE id=?", (key, pid))


def migrate(db, store, book) -> dict | None:
    """Beim Start: nur bei geänderter Signatur neu bestimmen. Rückgabe None = nichts zu tun."""
    sig = signature(book)
    if (store.get(SECTION) or {}).get("sig") == sig:
        return None
    out = renight(db, book)
    store.update(SECTION, {"sig": sig, "rule": RULE, "changed": out["frames"]})
    return out
