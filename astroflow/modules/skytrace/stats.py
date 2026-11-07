"""Statistik über alle SkyTrace-Läufe: je Beobachtung zählt nur die neueste (möglichst vollständige) Analyse.

Geliefert werden Beobachtungen und schlanke Kandidaten (nur die Felder, die `evidence.js classification` braucht) –
die Einordnung und Zählung macht die Oberfläche, damit sie mit Kandidatenliste und Filter übereinstimmt."""
from __future__ import annotations

import datetime as dt
import json
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


def _first_file(folder: Path) -> str | None:
    """Name der ersten Aufnahme einer Bildfolge (erste Zeile von frames.jsonl bzw. ausgeschlossen.jsonl).
    Gleicher Ordnername + gleiche erste Aufnahme = erneute Analyse derselben Daten; automatische Läufe einer Nacht
    bekommen nur noch nicht übergebene Aufnahmen und unterscheiden sich darin."""
    from json_store import open_read
    names = []
    for filename in ("frames.jsonl", "ausgeschlossen.jsonl"):
        try:
            with open_read(folder / filename) as rows:
                names.append(Path(json.loads(next(rows))["file"]).name)
        except (OSError, ValueError, KeyError, StopIteration, TypeError):
            continue
    return min(names, default=None)


def _frames_info(folder: Path) -> dict:
    """Aufnahmezeiten (Unix-s), Belichtung, Gerät und Pfad der ersten Aufnahme aus frames.jsonl – Grundlage der Satellitenrate."""
    from json_store import open_read
    times, exps, dev, first = [], [], None, None          # exps parallel zu times (nan = unbekannt)
    try:
        with open_read(folder / "frames.jsonl") as rows:
            for line in rows:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(r, dict) or not isinstance(r.get("timestamp"), (int, float)):
                    continue
                times.append(float(r["timestamp"]))
                m = r.get("meta") if isinstance(r.get("meta"), dict) else {}
                exps.append(float(m["exposure_seconds"]) if isinstance(m.get("exposure_seconds"), (int, float)) else float("nan"))
                if dev is None and m.get("INSTRUME"):
                    dev = f"{m['INSTRUME']}|{m.get('GAIN')}"
                    try:
                        dev = f"{m['INSTRUME']}|{float(m['GAIN'])}"
                    except (KeyError, TypeError, ValueError):
                        pass
                if first is None and isinstance(r.get("file"), str):
                    first = r["file"]
    except OSError:
        pass
    known = sorted(e for e in exps if e == e)
    return dict(_times=times, _exps=exps, _exp=known[len(known) // 2] if known else None, _dev=dev, _first_path=first)


def _satellites(items: list, run: str) -> list[dict]:
    """Zugeordnete Satelliten eines Laufs mit Helligkeits-Messwerten (satellites.phot_of) – wie `achievements.classify` eingeordnet."""
    from .achievements import classify
    from .satellites import phot_of
    out = []
    for c in items or []:
        if not isinstance(c, dict):
            continue
        slim = _candidate(c, run)
        kind, hit, _ = classify(slim)
        if kind != "satellite" or not hit:
            continue
        full = next((r for r in c.get("catalogs") or [] if r.get("provider") == "satellites" and r.get("status") == "compatible" and r.get("matches")), None)
        ph = phot_of(c, full["matches"][0]) if full else None
        out.append(dict(id=c.get("id"), run=run, ts=c.get("timestamp"), name=hit.get("name"), norad=hit.get("norad_id"), ph=ph))
    return out


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
        first = s.get('source_first_file') or (_first_file(path / Path(s["video"]).parent) if s.get("video") else None)
        extra = _frames_info(path / Path(s["video"]).parent) if s.get("video") else {}
        sessions.append(dict(extra, _sat=_satellites(items, path.name), session=s["session"], run=path.name, folder=Path(s["video"]).parent.name if s.get("video") else None, first=first, frames=s.get("frames") or 0, excluded=s.get("excluded") or 0,
                             hits=s.get("hits") or 0, complete=s.get("state") == "Fertig" and not s.get("limited", False),
                             night=_night(s["session"], [c["timestamp"] for c in cands if isinstance(c["timestamp"], (int, float))]),
                             candidates=cands))
    data = dict(sessions=sessions)
    with _lock:
        _cache[str(path)] = (stamp, data)
    return data


def collect(results: Path, skip: str | None = None) -> dict:
    """Alle Läufe lesen; mehrfach analysierte Beobachtungen (gleicher Ordnername und gleiche erste Aufnahme) nur einmal
    zählen. `skip` = gerade laufende Analyse (Dateien noch unvollständig)."""
    runs = sorted((p for p in results.iterdir() if p.is_dir() and (p / "videos.json").exists() and p.name != skip),
                  key=lambda p: p.name, reverse=True) if results.exists() else []
    best: dict[tuple, dict] = {}
    total = 0
    for path in runs:                                   # Lauf-IDs sind Startzeiten → neueste zuerst
        for s in _run(path)["sessions"]:
            total += 1
            key = (s["session"], s["first"])
            old = best.get(key)
            if old is None or (s["complete"] and not old["complete"]):
                best[key] = s
    sessions = sorted(best.values(), key=lambda s: (s["night"] or "", s["session"]), reverse=True)
    candidates = [c for s in sessions for c in s["candidates"]]
    return dict(runs=len({s["run"] for s in sessions}), all_runs=len(runs), repeated=total - len(sessions),
                sessions=[{k: v for k, v in s.items() if k != "candidates" and not k.startswith("_")} | {"candidate_count": len(s["candidates"])} for s in sessions],
                candidates=candidates, _full=sessions)         # `_full`: interne Daten (Satellitenstatistik), nie an den Browser
