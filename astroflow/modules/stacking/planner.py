"""Observatorium, Planer: Beobachtungsplan über mehrere Nächte für ein oder zwei Geräte.

Grundlage ist dieselbe Rechnung wie „Heute Nacht“ (`tonight`): 10-min-Schritte, astronomische Dunkelheit, Höhe über
max(30°, Horizontmaske des Ortes), Mondgewicht (Phase × Abstand), Zenit-Abschlag bei Alt-Az-Montierung, Wolken aus der
vorhandenen Open-Meteo-Schicht (`tonight._forecast`, nur soweit die Vorhersage reicht – spätere Nächte „ohne Wetter“).

Ablauf: Jede Nacht wird je Gerät in gleich lange Abschnitte (≈ Mindestbelichtung, 1–3 h) geteilt. Nacht für Nacht werden
die Abschnitte in der Reihenfolge des größten Nutzens vergeben (Priorität × min(Gewinn, Restbedarf)); ein Ziel läuft nie auf
zwei Geräten gleichzeitig, und wer seine Mindestbelichtung erreicht hat, scheidet aus. Abschnitte, die wegen Wolken oder
fehlender Ziele kaum Nutzen bringen, bleiben frei – das Ziel wandert so von selbst in eine spätere Nacht.
Der Standort verlässt den Rechner nicht (Wetter: gleiche Abfrage und Rundung wie Heute Nacht)."""
from __future__ import annotations

import datetime as dt
import math
import re
import time
import warnings
from pathlib import Path

import numpy as np

from ...core import sites
from . import deepsky, objects, tonight

STEP_MIN = tonight.STEP_MIN
MAX_NIGHTS = tonight.NIGHTS
MIN_USE = 0.25          # Abschnitt nur belegen, wenn mindestens 25 % seiner Dauer nutzbar sind
MAX_CANDIDATES = 500
WISHES = ("new", "mix", "deep")
COMET = re.compile(r"^\s*(komet\b|comet\b|[CPDXI]/\d{4}|\d{1,4}[PDI](/|\b))", re.I)       # Kometen-Projekte (Name)
# Geräte zur Auswahl (Bildfeld lange × kurze Seite in Grad, wie beim Lösen importierter Bilder); „library“ = aus den Aufnahmen
DEVICES = {
    "stellina": {"name": "Vaonis Stellina", "fov": [1.0, 0.7], "alt_az": True, "mag_limit": deepsky.MAG_LIMITS["stellina"]},
    "vespera": {"name": "Vaonis Vespera", "fov": [1.6, 0.9], "alt_az": True, "mag_limit": deepsky.MAG_LIMITS["vespera"]},
    "vespera2": {"name": "Vaonis Vespera II", "fov": [2.5, 1.4], "alt_az": True, "mag_limit": deepsky.MAG_LIMITS["vespera"], "mag_id": "vespera"},
    "seestar": {"name": "ZWO Seestar S50", "fov": [1.29, 0.73], "alt_az": True, "mag_limit": deepsky.MAG_LIMITS["seestar"]},
    "seestar30": {"name": "ZWO Seestar S30", "fov": [2.17, 1.21], "alt_az": True, "mag_limit": deepsky.MAG_LIMITS["seestar"], "mag_id": "seestar"},
}


def device_options(library_device: dict | None) -> list[dict]:
    out = [{"id": k, **v} for k, v in DEVICES.items()]
    if library_device and library_device.get("id") not in DEVICES:           # eigene Kamera: Bildfeld aus dem letzten Plate-Solving
        out.insert(0, {"id": "library", "name": library_device.get("name") or "Gerät aus deinen Aufnahmen",
                       "fov": library_device.get("fov"), "alt_az": bool(library_device.get("alt_az")),
                       "mag_limit": library_device.get("mag_limit") or deepsky.MAG_DEFAULT, "lib_id": library_device.get("id")})
    return out


def clean_wishes(w: dict, options: list[dict]) -> dict:
    """Eingaben des Assistenten prüfen (feste deutsche Meldungen)."""
    ids = {d["id"] for d in options}
    try:
        nights = int(w.get("nights") or 3)
        hours = float(w.get("min_hours") or 2)
    except (TypeError, ValueError):
        raise ValueError("Nächte und Mindestbelichtung als Zahl angeben.") from None
    devices = [str(d) for d in (w.get("devices") or [])][:2]
    if not devices or any(d not in ids for d in devices) or len(set(devices)) != len(devices):
        raise ValueError("Ein oder zwei Geräte aus der Liste wählen.")
    mode = str(w.get("mode") or "mix")
    if mode not in WISHES:
        raise ValueError("Wunsch: neue Objekte, Mischung oder vorhandene vertiefen.")
    watch = [str(x)[:40] for x in (w.get("watch") or [])][:20]
    return {"nights": max(1, min(MAX_NIGHTS, nights)), "min_hours": max(0.5, min(12.0, round(hours * 2) / 2)),
            "devices": devices, "mode": mode, "watch": watch}


def _targets(projects: list[dict], devices: list[dict], wishes: dict, lats: list[float]) -> list[dict]:
    """Kandidaten: eigene Objekte (vertiefen) + Katalog (neu) + Merkliste; je Gerät, ob es passt. `lats`: Breiten aller Orte des
    Zeitraums – ein Ziel bleibt, wenn es an mindestens einem davon über die Mindesthöhe kommt."""
    rises = lambda dec: any(90 - abs(lat - dec) >= tonight.MIN_ALT for lat in lats)
    mode, watch = wishes["mode"], set(wishes["watch"])
    w_own, w_new = {"new": 0.35, "mix": 1.0, "deep": 1.3}[mode], {"new": 1.3, "mix": 1.0, "deep": 0.3}[mode]
    out, taken, own = [], set(), []
    for p in projects:
        if p.get("ra") is None or p.get("dec") is None or not rises(p["dec"]):
            continue
        if p.get("kind") == "comet" or COMET.match(str(p.get("name") or "")):      # Kometen wandern: feste Koordinaten taugen nicht zum Planen
            continue
        keys = deepsky.keys_for(p.get("obj_id")) | deepsky.keys_for(p.get("name"))
        taken |= keys
        own.append((p["ra"], p["dec"], max([t.size for t in deepsky.catalog() if t.keys & keys] or [0]) / 120))
        hit = bool(keys & watch) or str(p["id"]) in watch
        out.append({"id": str(p["id"]), "name": p["name"], "kind": p.get("kind") or "generic", "ra": p["ra"], "dec": p["dec"],
                    "category": "more", "href": f"#/p/{p['id']}", "prio": w_own * (2.0 if hit else 1.0), "watch": hit,
                    "fits": [True] * len(devices)})
    for c in deepsky.catalog():
        if c.keys & taken or not rises(c.dec):
            continue
        # Begleiter im selben Feld eines eigenen Objekts (M32/M110 bei M31) sind kein neues Ziel – wie in Heute Nacht
        if any(objects.ang_sep(c.ra, c.dec, ra, dec) <= max(0.3, r, c.size / 120) for ra, dec, r in own):
            continue
        hit = c.id in watch or bool(c.keys & watch)
        fits = [deepsky.usable(c, {"id": d.get("mag_id") or d.get("lib_id") or d["id"]}, d.get("fov")) for d in devices]
        if not any(fits) and not hit:
            continue
        appeal = tonight.KIND_APPEAL.get(c.kind, 1.0) * (1.0 if c.source != "astap" else 0.7)
        bright = 0.15 * max(0.0, min(1.0, ((devices[0].get("mag_limit") or deepsky.MAG_DEFAULT) - c.mag) / 6)) if c.mag is not None else 0.0
        out.append({"id": c.id, "name": c.display, "kind": c.kind, "ra": c.ra, "dec": c.dec, "category": "new", "href": None,
                    "prio": w_new * appeal * (1 + bright) * (2.0 if hit else 1.0), "watch": hit, "fits": fits if any(fits) else [True] * len(devices)})
    out.sort(key=lambda t: -t["prio"])
    return out[:MAX_CANDIDATES]


def _night(site: dict, t0: float, t1: float, tg: list[dict], devices: list[dict], hours: list[dict] | None):
    """Gewichte (Ziele × Schritte, 0–1) einer Nacht wie in Heute Nacht; dazu Dunkelheit, Mond und Wolken."""
    from astropy import units as u
    from astropy.coordinates import EarthLocation, get_body
    from astropy.time import Time
    from astropy.utils import iers

    ts = np.arange(t0, t1 + 1, STEP_MIN * 60, dtype=float)
    ras, decs = np.radians([t["ra"] for t in tg]), np.radians([t["dec"] for t in tg])
    lat, lon = site["lat"], site["lon"]
    with warnings.catch_warnings(), iers.conf.set_temp("auto_download", False), iers.conf.set_temp("auto_max_age", None):
        warnings.simplefilter("ignore")
        t = Time(ts, format="unix")
        loc = EarthLocation(lat=lat * u.deg, lon=lon * u.deg)
        lst = t.sidereal_time("mean", longitude=lon * u.deg).rad
        sun_g, moon_g, moon_c = get_body("sun", t), get_body("moon", t, loc), get_body("moon", t)
        illum = (1 - np.cos(sun_g.separation(moon_c).rad)) / 2
        p = math.radians(lat)
        body = lambda ra, dec: np.degrees(np.arcsin(np.clip(math.sin(p) * np.sin(dec) + math.cos(p) * np.cos(dec) * np.cos(lst - ra), -1, 1)))
        sun, moon = body(sun_g.ra.rad, sun_g.dec.rad), body(moon_g.ra.rad, moon_g.dec.rad)
        alt = tonight._alt(ras, decs, lst, lat)
        lim = tonight._limit(ras, decs, lst, lat, site.get("horizon"))
        msep = tonight._sep(ras, decs, moon_g.ra.rad, moon_g.dec.rad)
    dark = sun <= -18
    good = dark[None, :] & (alt >= lim)
    loss = illum[None, :] * np.clip(1 - msep / 80, 0, 1)
    w_moon = np.where((moon > 0)[None, :], (1 - 0.85 * loss) * (1 - 0.3 * illum[None, :]), 1.0)
    cloud = tonight._series(ts, hours, "cloud") if hours else None
    clear = np.ones(len(ts)) if cloud is None else np.clip(1 - np.nan_to_num(cloud, nan=0.0) / 80, 0, 1)
    known = None if cloud is None else np.isfinite(cloud) & dark
    base = good * w_moon * clear[None, :]
    per_dev = []
    for d in devices:
        w_zen = np.where((alt > tonight.ZENITH_ALT) & bool(d.get("alt_az", True)), tonight.ZENITH_WEIGHT, 1.0)
        per_dev.append(base * w_zen)
    mid = int(np.argmax(dark)) + int(dark.sum()) // 2 if dark.any() else len(ts) // 2
    info = {"dark": tonight._spans(ts, dark), "dark_hours": round(float(dark.sum()) * STEP_MIN / 60, 2),
            "moon": round(float(illum[mid]) * 100),
            "weather": "none" if known is None or not known.any() else ("partial" if known.sum() < dark.sum() * 0.8 else "ok"),
            "cloud": None if known is None or not known.any() else round(float(np.nanmean(cloud[known])))}
    return ts, per_dev, alt, dark, info


def _slots(ts: np.ndarray, dark: np.ndarray, hours_per_slot: float) -> list[tuple[int, int]]:
    """Dunkle Zeit in gleich lange Abschnitte teilen (Indexbereiche), jeder mindestens `hours_per_slot` lang."""
    idx = np.where(dark)[0]
    if not len(idx):
        return []
    lo, hi = int(idx[0]), int(idx[-1]) + 1
    n = max(1, math.floor((hi - lo) * STEP_MIN / 60 / hours_per_slot))
    edges = np.linspace(lo, hi, n + 1).round().astype(int)
    return [(int(a), int(b)) for a, b in zip(edges[:-1], edges[1:]) if b > a]


def plan(projects: list[dict], site_for, wishes: dict, devices: list[dict], cache: Path, now: float | None = None,
         weather: bool = False) -> dict:
    """site_for(night) → Ort (mit `horizon`) oder None. Rückgabe: Plan je Nacht und Gerät, Fortschritt je Ziel."""
    now = time.time() if now is None else now
    first = site_for(None)
    if not first:
        return {"error": "Für die Planung fehlt ein Standort (Einstellungen → Standorte)."}
    base0, _ = tonight._window(now, first["lon"])
    night0 = sites.planning_night(now, first["lon"])
    night_dates = [(dt.date.fromisoformat(night0) + dt.timedelta(days=k)).isoformat() for k in range(wishes["nights"])]
    night_sites = [site_for(n) or first for n in night_dates]
    tg = _targets(projects, devices, wishes, sorted({s["lat"] for s in night_sites}))
    if not tg:
        return {"error": "Keine passenden Ziele für diese Geräte gefunden."}
    need = np.full(len(tg), wishes["min_hours"], dtype=float)
    got = np.zeros(len(tg))
    prio = np.array([t["prio"] for t in tg])
    fits = np.array([t["fits"] for t in tg], dtype=bool)               # Ziele × Geräte
    slot_h = max(1.0, min(3.0, wishes["min_hours"]))       # ein Abschnitt reicht bis 3 h Ziel; mehr wird über Abschnitte/Nächte verteilt
    forecasts: dict = {}
    nights = []
    for k in range(wishes["nights"]):
        night, site = night_dates[k], night_sites[k]
        t0, t1 = base0 + k * 86400, base0 + (k + 1) * 86400
        hours = None
        if weather:
            key = (round(site["lat"], 1), round(site["lon"], 1))
            if key not in forecasts:
                try:
                    fc = tonight._forecast([site["lat"], site["lon"]], cache, base0, base0 + MAX_NIGHTS * 86400)
                    forecasts[key] = (fc or {}).get("hours")
                except Exception:                          # Netz weg: ohne Wetter planen
                    forecasts[key] = None
            hours = forecasts[key]
        ts, per_dev, alt, dark, info = _night(site, t0, t1, tg, devices, hours)
        future = ts >= now
        slots = _slots(ts, dark & future, slot_h)
        free = [(di, a, b) for di in range(len(devices)) for a, b in slots]
        busy: list[tuple[int, int, int]] = []          # (Ziel, a, b) bereits vergeben (für das zweite Gerät)
        blocks = []
        while free:
            best = None
            for si, (di, a, b) in enumerate(free):
                gain = per_dev[di][:, a:b].sum(axis=1) * STEP_MIN / 60
                rem = np.clip(need - got, 0, None)
                # angefangene Ziele zuerst fertig machen, statt immer neue zu beginnen
                score = prio * np.minimum(gain, rem) * fits[:, di] * np.where((got > 0) & (rem > 0), 1.6, 1.0)
                score[gain < MIN_USE * (b - a) * STEP_MIN / 60] = 0     # nur Ziele, die den Abschnitt wirklich nutzen, kommen in Frage
                for ki, ba, bb in busy:                  # dasselbe Ziel nie gleichzeitig auf zwei Geräten
                    if ba < b and a < bb:
                        score[ki] = 0
                ki = int(np.argmax(score))
                if score[ki] > 0 and (best is None or score[ki] > best[0]):
                    best = (score[ki], si, ki, float(gain[ki]))
            if best is None:
                break
            _, si, ki, gain = best
            di, a, b = free.pop(si)
            got[ki] += gain
            busy.append((ki, a, b))
            used = np.where(per_dev[di][ki, a:b] > 0)[0] + a
            s, e = (int(used[0]), int(used[-1]) + 1) if len(used) else (a, b)
            blocks.append({"device": devices[di]["id"], "target": tg[ki]["id"], "start": float(ts[s]), "end": float(ts[min(e, len(ts) - 1)]),
                           "hours": round(gain, 2), "max_alt": round(float(alt[ki, s:e].max()), 1) if e > s else None})
        merged = []
        for bl in sorted(blocks, key=lambda x: (x["device"], x["start"])):
            m = merged[-1] if merged else None
            if m and m["device"] == bl["device"] and m["target"] == bl["target"] and bl["start"] - m["end"] <= STEP_MIN * 60:
                m["end"], m["hours"] = bl["end"], round(m["hours"] + bl["hours"], 2)
                m["max_alt"] = max(m["max_alt"] or 0, bl["max_alt"] or 0)
            else:
                merged.append(dict(bl))
        nights.append({"night": night, "site": {"id": site.get("id"), "name": site.get("name")}, **info,
                       "blocks": sorted(merged, key=lambda x: x["start"])})
    used = [i for i in range(len(tg)) if got[i] > 0]
    targets = [{**{k: tg[i][k] for k in ("id", "name", "kind", "ra", "dec", "category", "href", "watch")},
                "hours": round(float(got[i]), 2), "goal": wishes["min_hours"], "done": bool(got[i] >= wishes["min_hours"] - 0.05)}
               for i in sorted(used, key=lambda i: -got[i])]
    missing = [tg[i]["name"] for i in range(len(tg)) if tg[i]["watch"] and got[i] == 0]
    return {"created": now, "wishes": wishes, "devices": [{k: d.get(k) for k in ("id", "name", "fov", "alt_az")} for d in devices],
            "site": {"id": first.get("id"), "name": first.get("name"), "horizon": bool(first.get("horizon"))},
            "weather_on": bool(weather), "nights": nights, "targets": targets, "watch_missing": missing}
