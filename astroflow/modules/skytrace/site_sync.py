"""SkyTrace-Ausnahmen je Beobachtung → Einzelnächte der gemeinsamen Ortsliste (Nutzerwunsch 10.11.2026: der in SkyTrace
zugewiesene Ort soll auch für die Nacht in Bilder gelten – Himmelsqualität, Gerätediagnose, Tutor).

Quelle: `catalog_settings.json` → `sites[<Beobachtung>]` (Einstellungen → SkyTrace → „Standort einer Beobachtung“).
Nacht je Beobachtung wie in der Statistik (`stats._night`: Datum im Stellina-Ordnernamen, sonst Nacht der frühesten
Kandidatenzeit nach `core/sites.night_of`); ohne Lauf das Datum im Namen. Die Ortsliste erreicht nur der Dienst `sites`
(`SiteBook.sync_nights`, Marke `src="skytrace"`) – Einzelnächte des Nutzers bleiben unberührt.

Mehrere Beobachtungen einer Nacht: Beobachtungen ohne Ausnahme zählen nicht (sie folgen ohnehin der Liste). Liegen die
Ausnahmen einer Nacht weiter als `MERGE_M` auseinander, bekommt die Nacht keinen Ort (Konflikt, Meldung beim Speichern)."""
from __future__ import annotations

import threading

from ...core import sites as sites_mod

SRC = "skytrace"
CLAIM = "skytrace:nachtabgleich"         # einmalig: frühere Übernahmen (migrate_skytrace) als SkyTrace-Nächte kennzeichnen
_lock = threading.Lock()


def _short(session: str) -> str:
    return str(session).split("_observation_")[-1][:60] or str(session)[:60]


def session_nights(mod) -> dict[str, set[str]]:
    """Nächte je Beobachtungsname aus allen Läufen (laufende Analyse ausgenommen)."""
    from .stats import collect
    out: dict[str, set[str]] = {}
    try:
        s = mod.ctl.snapshot(logs=False)
        data = collect(mod.ctl.results, skip=s["run"] if s.get("running") else None)
    except Exception:
        import traceback
        traceback.print_exc()
        return out
    for row in data.get("sessions") or []:
        if row.get("session") and row.get("night"):
            out.setdefault(row["session"], set()).add(row["night"])
    return out


def plan(overrides: dict, nights: dict[str, set[str]]) -> tuple[dict[str, dict], dict[str, list[str]]]:
    """Gewünschte Einzelnächte {Nacht: Ort} und Konflikte {Nacht: [Beobachtungen]} aus den Ausnahmen."""
    by_night: dict[str, list[tuple[str, dict]]] = {}
    for session, site in (overrides or {}).items():
        try:
            s = sites_mod.valid(site) if isinstance(site, dict) else None
        except (TypeError, ValueError, KeyError):
            s = None
        if not s:
            continue
        found = nights.get(session) or ({n} if (n := sites_mod.night_from_name(str(session))) else set())
        for night in found:
            by_night.setdefault(night, []).append((session, s))
    wanted, conflicts = {}, {}
    for night, rows in by_night.items():
        first = rows[0][1]
        if any(sites_mod.distance_m(first, s) > sites_mod.MERGE_M for _, s in rows[1:]):
            conflicts[night] = sorted(_short(x) for x, _ in rows)
            continue
        names = sorted({_short(x) for x, _ in rows})
        wanted[night] = {"name": first.get("name") or f"SkyTrace: {names[0]}", "lat": first["lat"], "lon": first["lon"],
                         "alt_m": first.get("alt_m") or 0, "note": "aus SkyTrace (Standort einer Beobachtung)",
                         "why": "SkyTrace: " + ", ".join(names[:3]) + (f" +{len(names) - 3}" if len(names) > 3 else "")}
    return wanted, conflicts


def sync(mod, settings: dict | None = None) -> dict:
    """Ausnahmen in die Ortsliste übernehmen. Ohne Dienst `sites` (Kern fehlt) passiert nichts."""
    book = mod.ctx.services.get("sites")
    if book is None or not hasattr(book, "sync_nights"):
        return {}
    import observation_settings
    # Alles unter der Sperre und Stand frisch von der Platte: zwei gleichzeitige Abgleiche (Speichern und Start-/Lauf-Thread)
    # dürfen sich nicht mit einem veralteten Plan überholen und so einen eben geänderten Ort zurücksetzen.
    with _lock:
        try:
            cfg = observation_settings.load_settings()
        except Exception:
            cfg = settings if settings is not None else {}
        overrides = cfg.get("sites") or {}
        nights = session_nights(mod) if overrides else {}
        wanted, conflicts = plan(overrides, nights)
        res = book.sync_nights(SRC, wanted, claim=CLAIM)
    return {**res, "conflicts": conflicts, "nights": sorted(wanted)}


def sync_quiet(mod, settings: dict | None = None) -> None:
    try:
        sync(mod, settings)
    except Exception:
        import traceback
        traceback.print_exc()
