"""Heute Nacht: Dämmerung, Mond, Höhenverlauf der eigenen Objekte, Ablaufvorschlag und noch nicht fotografierte Objekte.

Alles offline mit astropy (IERS-Download aus, wie im Bild-Tutor). Standort aus Einstellungen → Bild-Tutor (tutor.lat/lon);
ohne Standort liefert plan() nur {"site": None}. Wolkenvorhersage (Open-Meteo, Ort auf 0,1° gerundet) nur, wenn im Tutor
„Wetter“ eingeschaltet ist – Cache 1 h in work/tutor/. Zeiten als Unix-Sekunden (UTC); die Oberfläche zeigt Ortszeit.
"""
from __future__ import annotations

import datetime as dt
import json
import time
import warnings
from pathlib import Path

import numpy as np

from . import objects, tutor

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
STEP_MIN = 10            # Abtastung der Nacht
MIN_ALT = 30.0           # „gut sichtbar“ ab dieser Höhe
MAX_CURVES = 6           # maximale Anzahl Empfehlungen in der Grafik
_cache: dict = {}


def library(db) -> tuple[list[dict], dict]:
    """Nur gespeicherte Messwerte lesen, keine Bilder öffnen oder Tutor-/KI-Aufträge starten."""
    rows = db.q("SELECT p.id, p.name, p.kind, p.ra, p.dec, p.obj_id, r.exposure, "
                "json_extract(r.stats,'$.quality') quality, json_extract(r.stats,'$.tutor.findings') findings "
                "FROM projects p LEFT JOIN results r ON r.id="
                "(SELECT MAX(id) FROM results WHERE project_id=p.id) "
                "WHERE json_extract(p.options,'$.gallery_parent') IS NULL")
    for p in rows:
        p["stats"] = {"quality": json.loads(p.pop("quality") or "{}"),
                      "tutor": {"findings": json.loads(p.pop("findings") or "[]")}}
        cat = objects.resolve_name(p.get("obj_id") or p["name"])
        if cat:
            p["obj_id"] = cat.id
            if p["ra"] is None or p["dec"] is None:
                p["ra"], p["dec"] = cat.ra, cat.dec
    # Das zuletzt verwendete Gerät; ein Frame genügt, auch wenn seine Rohdatei schon gelöscht ist.
    f = db.one("SELECT info FROM frames WHERE kind='light' AND project_id IS NOT NULL "
               "ORDER BY night DESC, rowid DESC LIMIT 1")
    info = json.loads(f["info"] or "{}") if f else {}
    device = tutor.detect_device([info.get("instrument")], info.get("shape"))
    sol = info.get("solve") or {}
    field = [sol.get("fov_w"), sol.get("fov_h")]
    source = "Plate-Solving der letzten Aufnahme"
    if not all(isinstance(v, (int, float)) and np.isfinite(v) and v > 0 for v in field):
        # Gesicherter Rückfall aus tutor.DEVICES, keine pauschalen Werte für Gerätefamilien.
        field = [1.0, 0.7] if device["id"] == "stellina" else None
        source = "ungefähres Stellina-Bildfeld" if field else None
    return rows, {"name": device["name"], "fov": field, "source": source}


def _need(p: dict) -> list[str]:
    """Mehr Zeit hilft bei Rauschen/Signal, nicht automatisch bei jedem schlechten Qualitätswert."""
    stats = p.get("stats") or {}
    quality = stats.get("quality") or {}
    codes = {x.get("code") for x in quality.get("issues", [])}
    findings = (stats.get("tutor") or {}).get("findings") or []
    codes.update(x.get("code") for x in findings if x.get("level") in ("warn", "bad"))
    reasons = []
    exp = p.get("exposure")
    if exp is not None and 0 < exp < 3600:
        reasons.append("Bisher weniger als eine Stunde belichtet.")
    if codes & {"noisy", "chroma_noise", "faint"}:
        reasons.append("Die Bildprüfung meldet Rauschen oder schwaches Signal.")
    snr = (quality.get("measure") or {}).get("snr")
    if snr is not None and 0 < snr < 10 and not reasons:
        reasons.append("Das gemessene Objekt-Signal ist noch schwach.")
    return reasons


def _recommend(targets: list[dict], suggestions: list[dict], projects: list[dict], device: dict) -> list[dict]:
    own = {p["id"]: p for p in projects}
    out = []
    for t in targets:
        reasons = _need(own[t["id"]])
        if not reasons or t["good_hours"] < 1:
            continue
        out.append({**t, "category": "more", "reasons": reasons,
                    "benefit": "Doppelte Gesamtbelichtung senkt zufälliges Rauschen theoretisch um etwa 29 %; schwache Details werden klarer.",
                    "href": f"#/p/{t['id']}", "rank": 3 + min(t["good_hours"], 6) / 6})
    for s in suggestions:
        field = device.get("fov")
        if field and s["size"] > min(field) * 60 * 0.9:
            continue
        reasons = ["Fehlt noch in deiner Bibliothek (Auswahl bekannter heller Katalogziele)."]
        if field:
            reasons.append("Passt nach seiner Kataloggröße mit Rand ins zuletzt verwendete Bildfeld.")
        out.append({**s, "category": "new", "reasons": reasons,
                    "benefit": "Eine neue Aufnahme ergänzt deine Sammlung.",
                    "href": f"#/nacht/{s['id']}", "rank": 2 + min(s["good_hours"], 6) / 6 + s["max_alt"] / 180})
    for x in out:
        x["moon_warning"] = x["moon_sep"] < 40
        x["rank"] -= 1 if x["moon_warning"] else 0
    # Beide Gründe sollen bei geeigneten Zielen auf der Übersicht vorkommen.
    ranked = sorted(out, key=lambda x: -x["rank"])
    selected = [next(x for x in ranked if x["category"] == c) for c in ("more", "new")
                if any(x["category"] == c for x in ranked)]
    selected += [x for x in ranked if x not in selected][:MAX_CURVES - len(selected)]
    return selected


def _window(now: float, lon: float) -> tuple[float, float]:
    """Mittag (mittlere Ortszeit) vor der kommenden Nacht bis Mittag danach. Zwischen Mitternacht und 6 Uhr Ortszeit
    gilt noch die laufende Nacht."""
    local = now + lon / 15 * 3600
    day = int(local // 86400) * 86400
    if local - day < 6 * 3600:
        day -= 86400
    noon = day + 12 * 3600 - lon / 15 * 3600
    return noon, noon + 86400


def _cross(t: np.ndarray, alt: np.ndarray, level: float, down: bool) -> float | None:
    """Erster Zeitpunkt, an dem alt `level` abwärts (down) bzw. aufwärts kreuzt – linear interpoliert."""
    a, b = alt[:-1] - level, alt[1:] - level
    idx = np.where((a > 0) & (b <= 0) if down else (a < 0) & (b >= 0))[0]
    if not len(idx):
        return None
    i = idx[0]
    return float(t[i] + (t[i + 1] - t[i]) * a[i] / (a[i] - b[i]))


def _spans(t: np.ndarray, ok: np.ndarray) -> list[list[float]]:
    out, start = [], None
    for ti, o in zip(t, ok):
        if o and start is None:
            start = ti
        elif not o and start is not None:
            out.append([float(start), float(ti)]); start = None
    if start is not None:
        out.append([float(start), float(t[-1])])
    return out


def _forecast(site, cache: Path, t0: float, t1: float, timeout: float = 10.0) -> dict | None:
    import httpx
    lat, lon = round(site[0], 1), round(site[1], 1)
    cache.mkdir(parents=True, exist_ok=True)
    f = cache / f"vorhersage_{lat}_{lon}.json"
    data = None
    if f.exists() and time.time() - f.stat().st_mtime < 3600:
        data = json.loads(f.read_text(encoding="utf-8"))
    else:
        r = httpx.get(FORECAST_URL, params={"latitude": lat, "longitude": lon, "hourly": "cloud_cover,relative_humidity_2m,temperature_2m,dew_point_2m",
                                            "forecast_days": 3, "timezone": "UTC"}, timeout=timeout)
        r.raise_for_status()
        data = r.json().get("hourly") or {}
        if data.get("time"):
            f.write_text(json.dumps(data), encoding="utf-8")
    hours = []
    for i, s in enumerate(data.get("time") or []):
        ts = dt.datetime.fromisoformat(s).replace(tzinfo=dt.timezone.utc).timestamp()
        if t0 - 1800 <= ts <= t1 + 1800:
            g = lambda k: (data.get(k) or [None] * (i + 1))[i]
            hours.append({"t": ts, "cloud": g("cloud_cover"), "humidity": g("relative_humidity_2m"),
                          "dew_spread": None if g("temperature_2m") is None or g("dew_point_2m") is None else round(g("temperature_2m") - g("dew_point_2m"), 1)})
    return {"hours": hours, "source": "Open-Meteo-Vorhersage (Raster ~10 km)"} if hours else None


def plan(projects: list[dict], cfg: dict, cache: Path, now: float | None = None, device: dict | None = None) -> dict:
    """projects: Objektzeilen mit exposure (Sekunden des neuesten Ergebnisses) und optional gespeicherten Befunden."""
    if cfg.get("lat") is None or cfg.get("lon") is None:
        return {"site": None}
    now = time.time() if now is None else now
    device = device or {"name": "Gerät unbekannt", "fov": None, "source": None}
    site = [float(cfg["lat"]), float(cfg["lon"])]
    t0, t1 = _window(now, site[1])
    key = (str(cache.resolve()), tuple(site), int(now // 600),
           json.dumps(projects, sort_keys=True), json.dumps(device, sort_keys=True), bool(cfg.get("weather")))
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < 600:
        return {**hit[1], "now": now}

    from astropy import units as u
    from astropy.coordinates import AltAz, EarthLocation, SkyCoord, get_body
    from astropy.time import Time
    from astropy.utils import iers

    ts = np.arange(t0, t1 + 1, STEP_MIN * 60, dtype=float)
    with warnings.catch_warnings(), iers.conf.set_temp("auto_download", False), iers.conf.set_temp("auto_max_age", None):
        warnings.simplefilter("ignore")
        t = Time(ts, format="unix")
        loc = EarthLocation(lat=site[0] * u.deg, lon=site[1] * u.deg)
        frame = AltAz(obstime=t, location=loc)
        sun_g, moon_g = get_body("sun", t), get_body("moon", t, loc)
        sun = sun_g.transform_to(frame).alt.deg
        moon = moon_g.transform_to(frame).alt.deg
        illum = (1 - np.cos(sun_g.separation(get_body("moon", t)).rad)) / 2
        own = [p for p in projects if p.get("ra") is not None and p.get("dec") is not None]
        own_ids = {c.id for p in projects if (c := objects.resolve_name(p.get("obj_id") or p["name"]))}
        cands = [c for c in objects.CATALOG if c.id not in own_ids
                 and all(objects.ang_sep(c.ra, c.dec, p["ra"], p["dec"]) > max(0.3, c.size / 120) for p in own)]
        ras = np.array([p["ra"] for p in own] + [c.ra for c in cands], float)
        decs = np.array([p["dec"] for p in own] + [c.dec for c in cands], float)
        if len(ras):
            sc = SkyCoord(ras[:, None] * u.deg, decs[:, None] * u.deg)
            sky = sc.transform_to(AltAz(obstime=t[None, :], location=loc))
            alt = sky.alt.deg
            msep = sky.separation(moon_g.transform_to(frame)[None, :]).deg
        else:
            alt, msep = np.zeros((0, len(ts))), np.zeros((0, len(ts)))

    tw = {"sunset": _cross(ts, sun, -0.833, True), "civil": _cross(ts, sun, -6, True), "nautical": _cross(ts, sun, -12, True),
          "dark_start": _cross(ts, sun, -18, True), "dark_end": _cross(ts, sun, -18, False), "nautical_end": _cross(ts, sun, -12, False),
          "sunrise": _cross(ts, sun, -0.833, False)}
    dark = sun <= -18
    moonless = dark & (moon <= 0)
    view = [max(t0, tw["sunset"] - 1800) if tw["sunset"] else t0,
            min(t1, tw["sunrise"] + 1800) if tw["sunrise"] else t1]
    sel = (ts >= view[0]) & (ts <= view[1])
    vt, vm = ts[sel], moon[sel]                       # Mondauf-/-untergang nur innerhalb der gezeigten Nacht
    mid_dark = int(np.argmax(dark)) + int(dark.sum()) // 2 if dark.any() else len(ts) // 2
    waxing = bool(illum[min(mid_dark + 6, len(illum) - 1)] > illum[mid_dark])

    def entry(i: int, info: dict) -> dict:
        a = alt[i]
        remaining = dark & (ts >= now)
        good = remaining & (a >= MIN_ALT)
        j = int(np.argmax(np.where(remaining, a, -90)))
        moon_visible = good & (moon > 0)
        sep = float(msep[i][moon_visible].min()) if moon_visible.any() else 180.0
        spans = _spans(ts, good)
        return {**info, "max_alt": round(float(a[j]), 1), "max_t": float(ts[j]),
                "good_hours": round(sum(b - a for a, b in spans) / 3600, 2), "good": spans,
                "moon_sep": round(sep, 1), "alt": [round(float(v), 1) for v in a[sel]]}

    targets = [entry(i, {"id": p["id"], "name": p["name"], "kind": p.get("kind"), "exposure": p.get("exposure") or 0})
               for i, p in enumerate(own)]
    targets = sorted([x for x in targets if x["good_hours"] > 0], key=lambda x: -x["good_hours"])
    sugg = [entry(len(own) + k, {"id": c.id, "name": c.display, "kind": c.kind, "kind_label": objects.KIND_LABEL.get(c.kind, ""), "size": c.size})
            for k, c in enumerate(cands)]
    sugg = sorted([x for x in sugg if x["good_hours"] >= 2], key=lambda x: (-x["good_hours"], -x["max_alt"]))
    recommendations = _recommend(targets, sugg, projects, device)

    # Ablaufvorschlag aus dem UI-Umbau: nur zusammenhängende Blöcke ab 40 min.
    blocks = []
    if targets:
        own_alt = {x["id"]: alt[next(i for i, p in enumerate(own) if p["id"] == x["id"])] for x in targets[:MAX_CURVES]}
        for k in np.where(dark & (ts >= now))[0]:
            best = max(own_alt, key=lambda pid: own_alt[pid][k])
            best = best if own_alt[best][k] >= MIN_ALT else None
            if blocks and blocks[-1]["id"] == best:
                blocks[-1]["end"] = float(ts[k] + STEP_MIN * 60)
            else:
                blocks.append({"id": best, "start": float(ts[k]), "end": float(ts[k] + STEP_MIN * 60)})
        names = {x["id"]: x["name"] for x in targets}
        blocks = [{**b, "name": names.get(b["id"])} for b in blocks if b["id"] is not None and b["end"] - b["start"] >= 2400]

    weather, weather_error = None, None
    if cfg.get("weather"):
        try:
            weather = _forecast(site, cache, view[0], view[1])
        except Exception as e:                          # Netz weg: Planung geht trotzdem
            weather_error = f"Wettervorhersage nicht erreichbar ({type(e).__name__})"

    out = {"site": site, "start": view[0], "end": view[1], "step": STEP_MIN * 60, "times": [float(v) for v in ts[sel]],
           "twilight": tw, "dark_hours": round(sum(b - a for a, b in _spans(ts, dark)) / 3600, 2),
           "moon": {"illum": round(float(illum[mid_dark]) * 100), "waxing": waxing, "alt": [round(float(v), 1) for v in moon[sel]],
                    "rise": _cross(vt, vm, 0, False), "set": _cross(vt, vm, 0, True), "moonless": _spans(ts, moonless)},
           "targets": targets[:MAX_CURVES], "more_targets": max(0, len(targets) - MAX_CURVES), "plan": blocks,
           "suggestions": sugg[:6], "recommendations": recommendations, "device": device,
           "sun": [round(float(v), 1) for v in sun[sel]],
           "weather": weather, "weather_error": weather_error, "weather_on": bool(cfg.get("weather")), "min_alt": MIN_ALT}
    _cache.clear()
    _cache[key] = (time.time(), out)
    return {**out, "now": now}
