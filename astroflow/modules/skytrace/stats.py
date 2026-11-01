"""Statistik über alle SkyTrace-Läufe: je Beobachtung zählt nur die neueste (möglichst vollständige) Analyse.

Geliefert werden Beobachtungen und schlanke Kandidaten (nur die Felder, die `evidence.js classification` braucht) –
die Einordnung und Zählung macht die Oberfläche, damit sie mit Kandidatenliste und Filter übereinstimmt."""
from __future__ import annotations

import datetime as dt
import re
import threading
from pathlib import Path

_cache: dict[str, tuple[tuple, dict]] = {}
_lock = threading.Lock()
MATCH_KEYS = ("name", "object_type", "norad_id", "mean_offset_arcsec", "separation_arcsec", "time_offset_s")


def _read(path: Path, default):
    from json_store import read_json
    try:
        return read_json(path, default)
    except (OSError, ValueError):
        return default


def _candidate(c: dict, run: str) -> dict:
    """Nur das Nötige für Einordnung, Zeit und Geschwindigkeit (Kandidatendateien enthalten Ausschnitte, Messprofile …)."""
    track, blink = c.get("linked_track") or {}, c.get("blink") or None
    speed = (c.get("plausibility") or {}).get("minimum_angular_speed_deg_s")
    return dict(id=c.get("id"), run=run, session=c.get("session"), kind=c.get("kind"), timestamp=c.get("timestamp"),
                confidence=c.get("confidence"), model_scope=c.get("model_scope"),
                model_hints=[{"kind": h.get("kind"), "confidence": h.get("confidence")} for h in c.get("model_hints") or []],
                linked_track={"count": track.get("count"), "reason": track.get("reason")} if track else None,
                blink={k: blink.get(k) for k in ("flashes", "period_pixels", "form")} if isinstance(blink, dict) else None,
                speed_arcsec_s=speed * 3600 if isinstance(speed, (int, float)) else None,
                catalogs=[{"provider": r.get("provider"), "status": r.get("status"),
                           "matches": [{k: m.get(k) for k in MATCH_KEYS} for m in r.get("matches") or []][:3]}
                          for r in c.get("catalogs") or []])


def _night(session: str, stamps: list[float]) -> str | None:
    """Beobachtungsnacht: Datum im Stellina-Ordnernamen, sonst früheste Kandidatenzeit − 12 h (wie beim Stacking)."""
    m = re.match(r"^(\d{4}-\d\d-\d\d)_observation_", session or "") or re.search(r"_observation_(\d{4}-\d\d-\d\d)-p\d+-\d+$", session or "")
    if m:
        return m.group(1)
    if stamps:
        return (dt.datetime.fromtimestamp(min(stamps), dt.timezone.utc) - dt.timedelta(hours=12)).date().isoformat()
    return None


def _run(path: Path) -> dict:
    """Beobachtungen eines Laufs samt Kandidaten (gecacht nach Dateizeiten)."""
    summaries = _read(path / "videos.json", []) or []
    files = [path / s["candidates_file"] for s in summaries if isinstance(s, dict) and s.get("candidates_file")]
    stamp = tuple(f.stat().st_mtime_ns if f.exists() else 0 for f in [path / "videos.json", *files])
    with _lock:
        hit = _cache.get(str(path))
        if hit and hit[0] == stamp:
            return hit[1]
    sessions = []
    for s in summaries:
        if not isinstance(s, dict) or not s.get("session"):
            continue
        items = _read(path / s["candidates_file"], []) if s.get("candidates_file") else []
        cands = [_candidate(c, path.name) for c in items or [] if isinstance(c, dict)]
        sessions.append(dict(session=s["session"], run=path.name, frames=s.get("frames") or 0, excluded=s.get("excluded") or 0,
                             hits=s.get("hits") or 0, complete=s.get("state") == "Fertig" and not s.get("limited", False),
                             night=_night(s["session"], [c["timestamp"] for c in cands if isinstance(c["timestamp"], (int, float))]),
                             candidates=cands))
    data = dict(sessions=sessions)
    with _lock:
        _cache[str(path)] = (stamp, data)
    return data


def collect(results: Path, skip: str | None = None) -> dict:
    """Alle Läufe lesen; mehrfach analysierte Beobachtungen (gleicher Ordnername) nur einmal zählen.
    `skip` = gerade laufende Analyse (Dateien noch unvollständig)."""
    runs = sorted((p for p in results.iterdir() if p.is_dir() and (p / "videos.json").exists() and p.name != skip),
                  key=lambda p: p.name, reverse=True) if results.exists() else []
    best: dict[str, dict] = {}
    total = 0
    for path in runs:                                   # Lauf-IDs sind Startzeiten → neueste zuerst
        for s in _run(path)["sessions"]:
            total += 1
            old = best.get(s["session"])
            if old is None or (s["complete"] and not old["complete"]):
                best[s["session"]] = s
    sessions = sorted(best.values(), key=lambda s: (s["night"] or "", s["session"]), reverse=True)
    candidates = [c for s in sessions for c in s["candidates"]]
    return dict(runs=len({s["run"] for s in sessions}), all_runs=len(runs), repeated=total - len(sessions),
                sessions=[{k: v for k, v in s.items() if k != "candidates"} | {"candidate_count": len(s["candidates"])} for s in sessions],
                candidates=candidates)
