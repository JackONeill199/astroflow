"""3D-Lauf-Ansicht: Teleskopweg über die ganze Beobachtung, Zielobjekt und alle zugeordneten Treffer in einem System.

Baut auf `orbit3d` auf (Bezugssystem D, SGP4). Je Beobachtung eines Laufs:
- Blickrichtung je Aufnahme aus der Plate-Solving-WCS der Referenz und `reference_to_current` (Feldrotation sichtbar);
  ohne WCS aus dem Katalog (Zielname) mit nordorientiertem Bildfeld aus FOCAL/PIXSZ – als Schätzung gekennzeichnet.
- Az/Alt je Stichprobe, Lücken zwischen Aufnahmen, ausgelassene (unscharfe) Aufnahmen.
- Treffer aller Kandidaten (bester Satellit je Kandidat, beim Fokus-Kandidaten alle) im gemeinsamen Bezugssystem.
Ergebnis je Lauf/Beobachtung/Fokus im Speicher gecacht (Schlüssel enthält die Dateizeiten).
"""
from __future__ import annotations

import json
import math
from functools import lru_cache
from pathlib import Path

import numpy as np

from . import orbit3d as o

MAX_SAMPLES = 360                    # Stichproben der Blickrichtung (lange Nächte: 300–500 Aufnahmen)
HIT_WINDOW_S = 300                   # Bahnstück je Treffer ± 5 min um den Durchgang
HIT_RING = 180                       # Umlaufpunkte je Treffer in der Übersicht
GAP_MIN_S = 120


def _rows(path: Path, keys=("index", "timestamp", "reference_to_current", "meta", "file")) -> list[dict]:
    if not path.is_file():
        return []
    out = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if isinstance(r.get("timestamp"), (int, float)):
                out.append({k: r.get(k) for k in keys})
    return out


def _first_time(path: Path):
    """Zeitstempel der ersten Aufnahme (nur bis zur ersten gültigen Zeile lesen)."""
    if not path.is_file():
        return None
    with path.open(encoding="utf-8") as f:
        for line in f:
            try:
                t = json.loads(line).get("timestamp")
            except (ValueError, AttributeError):
                continue
            if isinstance(t, (int, float)):
                return t
    return None


def _wcs(folder: Path):
    info_path, wcs_path = folder / "platesolve" / "ergebnis.json", folder / "platesolve" / "loesung.wcs"
    if not (info_path.is_file() and wcs_path.is_file()):
        return None, None
    from astropy.io import fits
    from astropy.wcs import WCS
    info = json.loads(info_path.read_text(encoding="utf-8"))
    header = fits.Header.fromfile(wcs_path)
    header["NAXIS"], header["NAXIS1"], header["NAXIS2"] = 2, info["width"], info["height"]
    return WCS(header, naxis=2), info


def _analysis_size(info: dict) -> tuple[float, float]:
    """Analysegröße wie engine/sequence.stretch: Plate-Solving-Bild auf höchstens 1536 px Breite, gerade Pixelzahl."""
    w, h = float(info["width"]), float(info["height"])
    scale = min(1., 1536 / w)
    return max(2, int(w * scale) // 2 * 2), max(2, int(h * scale) // 2 * 2)


def _exposure(meta: dict | None) -> tuple[float, str]:
    """Belichtung (s) und Zeitbezug; Stellina schreibt das Belichtungsende (engine/motion_plausibility.exposure_info)."""
    meta = meta or {}
    try:
        from motion_plausibility import exposure_info                  # Engine-Pfad liegt beim Laden des Moduls in sys.path
        e = exposure_info(meta)
        return float(e["seconds"] or 0), e.get("reference", "start")
    except Exception:
        sec = meta.get("exposure_seconds")
        return (float(sec) if isinstance(sec, (int, float)) else 0.), "start"


def _target(session: str, services: dict, cands: list[dict]) -> dict:
    """Zielobjekt aus dem Beobachtungsnamen: Katalog (Bilder-Modul) für Typ/Koordinaten, Sage-Wissensbasis für die Entfernung."""
    from .api import short_name
    name = short_name(session)
    words = name.replace("_", " ").split()
    tries = [" ".join(words[:k]) for k in range(len(words), 0, -1)]
    resolve, facts = services.get("catalog_resolve"), services.get("object_facts")
    out = dict(name=name)
    for t in tries:
        c = None
        try:
            c = resolve and resolve(t)
        except Exception:
            c = None
        if c:
            out.update(name=c.get("display") or t, id=c.get("id"), kind=c.get("kind_label") or None, ra=c.get("ra"), dec=c.get("dec"))
            break
    if facts:
        for t in [out.get("id")] + tries:
            try:
                f = t and facts(t)
            except Exception:
                f = None
            if f:
                out["kind"] = out.get("kind") or f.get("otype")
                out["distance"] = f.get("distance")
                break
    return out


def _site(cands, rows, session):
    for c in cands:
        if c.get("site") and c["site"].get("lat") is not None:
            return c["site"]
    try:
        from observation_settings import resolve_site
        row = next((r for r in rows if r.get("file")), {})
        when = next((r.get("timestamp") for r in rows if isinstance(r.get("timestamp"), (int, float))), None)
        try:
            return resolve_site(row.get("file", ""), session, when=when)
        except OSError:                                 # Rohbild schon gelöscht → Standort aus Ortsliste/Einstellungen
            return resolve_site("", session, when=when)
    except Exception:
        return None


def _pointing(folder: Path, accepted: list[dict], cands: list[dict], target: dict):
    """Je Stichprobe: Zeit, Bildmitte und Ecken (RA/Dec). Quelle 'wcs' bzw. 'katalog'."""
    wcs, info = _wcs(folder)
    sample = accepted
    if len(sample) > MAX_SAMPLES:
        idx = np.unique(np.linspace(0, len(sample) - 1, MAX_SAMPLES).round().astype(int))
        sample = [sample[i] for i in idx]
    if wcs is not None:
        ctx = next((c.get("context_image") for c in cands if (c.get("context_image") or {}).get("width")), None) or {}
        # reference_to_current gilt in Analysepixeln; erst danach in WCS-Pixel skalieren
        w, h = (float(ctx["width"]), float(ctx["height"])) if ctx else _analysis_size(info)
        base = np.array([[0, 0], [w, 0], [w, h], [0, h], [w / 2, h / 2], [w / 2, 0]])
        pix, keep = [], []
        for r in sample:
            m = r.get("reference_to_current")
            if m is None:
                continue
            m = np.asarray(m, float)
            try:
                pix.append((base - m[:, 2]) @ np.linalg.inv(m[:, :2]).T)
            except np.linalg.LinAlgError:
                continue
            keep.append(r)
        if pix:
            world = wcs.all_pix2world(np.concatenate(pix) * [info["width"] / w, info["height"] / h], 0).reshape(len(pix), 6, 2)
            ok = np.isfinite(world).all(axis=(1, 2))
            u = o.unit_radec(world[0, :, 0], world[0, :, 1])
            ang = lambda a, b: round(math.degrees(math.acos(min(1., float(u[a] @ u[b])))), 3)
            return dict(source="wcs", size_deg=[ang(0, 1), ang(1, 2)],
                        samples=[dict(t=r["timestamp"], index=r.get("index"), center=wd[4].tolist(), corners=wd[:4].tolist(), top=wd[5].tolist())
                                 for r, wd, good in zip(keep, world, ok) if good])
    if target.get("ra") is None:
        return None
    meta = next((r.get("meta") for r in accepted if r.get("meta")), {}) or {}
    try:
        fw = math.degrees(float(meta["NAXIS1"]) * float(meta["PIXSZ"]) / 1000 / float(meta["FOCAL"]))
        fh = math.degrees(float(meta["NAXIS2"]) * float(meta["PIXSZ"]) / 1000 / float(meta["FOCAL"]))
    except (KeyError, ValueError, TypeError, ZeroDivisionError):
        fw = fh = 1.
    ra, dec = float(target["ra"]), float(target["dec"])
    cd = max(math.cos(math.radians(dec)), 1e-3)
    corners = [[ra + dx * fw / 2 / cd, dec + dy * fh / 2] for dx, dy in ((-1, -1), (1, -1), (1, 1), (-1, 1))]
    return dict(source="katalog", size_deg=[round(fw, 3), round(fh, 3)],
                samples=[dict(t=r["timestamp"], index=r.get("index"), center=[ra, dec], corners=corners, top=[ra, dec + fh / 2]) for r in sample])


def _gaps(times: list[float], exposure: float) -> list[list[float]]:
    t = np.sort(np.asarray(times, float))
    if len(t) < 3:
        return []
    d = np.diff(t)
    limit = max(GAP_MIN_S, 5 * float(np.median(d)), 3 * exposure)
    return [[float(t[i]), float(t[i + 1])] for i in np.flatnonzero(d > limit)]


def _altaz(site, t, dirs_d, anchor):
    """Az/Alt (Grad) je Richtung in D zur Zeit t (geodätische Ortsnormale, ohne Refraktion)."""
    lat, lon = math.radians(site["lat"]), math.radians(site["lon"])
    up = np.array([math.cos(lat) * math.cos(lon), math.cos(lat) * math.sin(lon), math.sin(lat)])
    east = np.array([-math.sin(lon), math.cos(lon), 0.])
    north = np.cross(up, east)
    ecef = o.rot_z(-(o.gmst(np.asarray(t, float)) - o.gmst(anchor)), dirs_d)
    alt = np.degrees(np.arcsin(np.clip(ecef @ up, -1, 1)))
    az = np.degrees(np.arctan2(ecef @ east, ecef @ north)) % 360
    return alt, az, dict(up=up.tolist(), east=east.tolist(), north=north.tolist())


def _position_angle(center, top):
    """Positionswinkel der Bild-Oberkante (Grad, Nord über Ost)."""
    ra0, d0 = np.radians(center[:, 0]), np.radians(center[:, 1])
    ra1, d1 = np.radians(top[:, 0]), np.radians(top[:, 1])
    y = np.sin(ra1 - ra0) * np.cos(d1)
    x = np.cos(d0) * np.sin(d1) - np.sin(d0) * np.cos(d1) * np.cos(ra1 - ra0)
    return np.degrees(np.arctan2(y, x)) % 360


def _settings_file(home: Path) -> Path:
    """Einstellungsdatei, aus der die Engine den Standort liest (`observation_settings.CONFIG`)."""
    try:
        from observation_settings import CONFIG
        return Path(CONFIG)
    except Exception:
        return home / "catalog_settings.json"


def build_run(run_path: Path, session: str | None, focus: str | None, home: Path, services: dict, scene=None) -> dict:
    """`scene(payload)` rechnet die Treffer (Standard `orbit3d.build`; die API übergibt `api.scene_3d` mit Engine-Rückfall)."""
    from .api import _read_json, within
    summaries = [s for s in _read_json(run_path / "videos.json", []) or [] if isinstance(s, dict) and s.get("video")]
    if not summaries:
        return dict(ok=False, reason="Dieser Lauf enthält keine Bildfolge mit Aufnahmezeiten.")
    names = [s["session"] for s in summaries]
    if focus:
        summary = next((s for s in summaries if within(run_path, str(Path(s["video"]).parent)).name == focus.partition("/")[0]), summaries[0])
    else:
        summary = next((s for s in summaries if s["session"] == session), summaries[0])
    folder = within(run_path, str(Path(summary["video"]).parent))
    stamp = tuple((p.stat().st_mtime_ns if p.is_file() else 0) for p in
                  (folder / "frames.jsonl", folder / "ausgeschlossen.jsonl", folder / "kandidaten.json", folder / "platesolve" / "loesung.wcs",
                   _settings_file(home)))                           # Standort aus den Einstellungen geändert → neu rechnen
    first = _first_time(folder / "frames.jsonl")
    offset = o.clock_offset(first) if first is not None else 0.        # übernommener Uhrversatz der Nacht (Teil des Cache-Schlüssels)
    return _cached(str(run_path), str(folder), summary["session"], focus or "", str(home), stamp + (offset,), tuple(names),
                   _Services(dict(services, _scene=scene or o.build)))


class _Services:
    """Dienste als Cache-Schlüssel neutral (gleiche Dienste → gleicher Schlüssel)."""
    def __init__(self, d): self.d = d
    def __hash__(self): return 0
    def __eq__(self, other): return isinstance(other, _Services)


@lru_cache(maxsize=12)
def _cached(run_path, folder, session, focus, home, stamp, names, services):
    return _build(Path(run_path), Path(folder), session, focus, Path(home), list(names), services.d)


def _build(run_path, folder, session, focus, home, names, services):
    from .api import _read_json
    accepted = _rows(folder / "frames.jsonl")
    excluded = _rows(folder / "ausgeschlossen.jsonl", keys=("index", "timestamp", "file"))
    cands = _read_json(folder / "kandidaten.json", []) or []
    base = dict(run=run_path.name, sessions=names, session=session)
    if not accepted:
        return dict(base, ok=False, reason="Für diese Beobachtung sind keine Aufnahmezeiten gespeichert.")
    site = _site(cands, accepted, session)
    if not site or site.get("lat") is None:
        return dict(base, ok=False, need_site=True,
                    reason="Der Beobachtungsstandort fehlt. Einstellungen → SkyTrace → Standort eintragen, dann zeigt die 3D-Ansicht Erde, Blickfeld und Teleskopweg.")
    target = _target(session, services, cands)
    pointing = _pointing(folder, accepted, cands, target)
    if not pointing or not pointing["samples"]:
        return dict(base, ok=False, reason="Die Blickrichtung ist unbekannt: kein Plate-Solving und kein Zielobjekt im Katalog.")
    exposure, ref = _exposure(accepted[0].get("meta"))
    # Zeitstempel (Kamerazeit) → Belichtungsbeginn in wahrer UTC: Stellina-Zeit = Ende, dazu der übernommene Uhrversatz der Nacht
    offset = o.clock_offset(accepted[0]["timestamp"])
    shift = (exposure if ref == "end" else 0.) - offset
    times = sorted([r["timestamp"] - shift for r in accepted] + [r["timestamp"] - shift for r in excluded])
    anchor = times[0]
    samples = pointing["samples"]
    st = np.array([s["t"] - shift for s in samples], float)
    center = o.radec_to_d(*np.array([s["center"] for s in samples], float).T, anchor)
    corners = o.radec_to_d(*np.array([s["corners"] for s in samples], float).reshape(-1, 2).T, anchor).reshape(len(samples), 4, 3)
    alt, az, enu = _altaz(site, st + exposure / 2, center, anchor)
    # Parallaktischer Winkel = Drehung des Himmels gegenüber dem Horizont (bei Alt-Az-Montierung sichtbare Feldrotation)
    zen = o.rot_z(o.gmst(st + exposure / 2) - o.gmst(anchor), np.array(enu["up"]))
    flat = lambda v: v - (v * center).sum(-1)[:, None] * center
    zp, pp = flat(zen), flat(np.broadcast_to([0., 0., 1.], center.shape))
    parallactic = np.degrees(np.arctan2((np.cross(pp, zp) * center).sum(-1), (pp * zp).sum(-1)))
    pa = _position_angle(np.array([s["center"] for s in samples], float), np.array([s["top"] for s in samples], float))
    observer = o.geodetic_to_ecef(site["lat"], site["lon"], site.get("alt_m", 0) or 0)
    from .stats import _night                   # Nacht wie in Statistik/Standortsuche (Ortszeit, core/sites.night_of)
    night = _night(session or "", [r["timestamp"] for r in accepted + excluded if isinstance(r.get("timestamp"), (int, float))])
    out = dict(base, ok=True, night=night, clock_offset_s=offset or None, clock_pending=False, anchor=anchor, earth_rate=o.EARTH_RATE, sun=o._r(o.sun_d(anchor), 5), observer=o._r(observer, 3), enu=enu,
               window=[times[0] - 60, times[-1] + exposure + 60], exposure=exposure, target=target,
               frames=dict(accepted=len(accepted), excluded=len(excluded),
                           excluded_times=[round(r["timestamp"] - shift, 1) for r in excluded][:1000]),
               gaps=_gaps(times, exposure),
               pointing=dict(source=pointing["source"], size_deg=pointing["size_deg"], t=o._r(st, 1), index=[s["index"] for s in samples],
                             center=o._r(center, 6), corners=o._r(corners, 6), alt=o._r(alt, 2), az=o._r(az, 2), pa=o._r(pa, 2),
                             parallactic=o._r(parallactic, 2),
                             ra=samples[0]["center"][0], dec=samples[0]["center"][1]),
               hits=[], missing=[])
    # Treffer aller Kandidaten im gemeinsamen System D
    cache = home / "catalog_cache" / "archives"
    focus_id = focus.partition("/")[2] if focus else ""
    for c in cands:
        if not o.available(c):
            continue
        payload = o.payload_for(folder, c, cache)
        if payload.get("reason"):
            continue
        mine = c.get("id") == focus_id
        if not mine:
            payload["satellites"] = payload["satellites"][:1]
        out["clock_pending"] = out["clock_pending"] or bool(payload.get("clock_pending"))
        payload.update(anchor=anchor, window_s=HIT_WINDOW_S, ring_points=HIT_RING)
        scene = services["_scene"](payload)
        if not scene.get("ok", True):                          # z. B. sgp4 fehlt auch im Engine-Interpreter
            out["missing"].append(dict(candidate=c["id"], name=c.get("label"), reason=scene.get("reason")))
            continue
        key = folder.name + "/" + c["id"]
        common = dict(key=key, candidate=c["id"], label=c.get("label"), t_pass=scene["t0"], focus=mine,
                      fov_corners=scene["fov"]["corners"], window=scene["window"])
        for i, s in enumerate(scene["satellites"]):
            out["hits"].append(dict(common, kind="sat", alternative=i > 0, **{k: v for k, v in s.items() if k != "color"}))
        for m in scene["meteors"]:
            out["hits"].append(dict(common, kind="meteor", **m))
        for a in scene["aircraft"]:
            out["hits"].append(dict(common, kind="plane", **a))
        out["missing"] += [dict(m, candidate=c["id"]) for m in scene["missing"]]
    out["hits"].sort(key=lambda h: (h["t_pass"], h.get("alternative", False)))
    if out["hits"]:
        out["window"] = [min(out["window"][0], min(h["window"][0] for h in out["hits"])),
                         max(out["window"][1], max(h["window"][1] for h in out["hits"]))]
    return out
