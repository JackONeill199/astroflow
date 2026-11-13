"""Observatorium, Planer: Beobachtungsplan über mehrere Nächte für ein oder zwei Geräte.

Grundlage ist dieselbe Rechnung wie „Heute Nacht“ (`tonight`): 10-min-Schritte, astronomische Dunkelheit, Höhe über
max(30°, Horizontmaske des Ortes), Mondgewicht (Phase × Abstand), Bildfelddrehung je Gerät (`devices.weights`; EQ-Modus
je Gerät wählbar), Wolken aus der
vorhandenen Open-Meteo-Schicht (`tonight._forecast`, nur soweit die Vorhersage reicht – spätere Nächte „ohne Wetter“).

Ablauf: Jede Nacht wird je Gerät in Halbstunden-Abschnitte (SLOT_H) geteilt. Nacht für Nacht werden
die Abschnitte in der Reihenfolge des größten Nutzens vergeben (Priorität × min(Gewinn, Restbedarf)); ein Ziel läuft nie auf
zwei Geräten gleichzeitig, und wer seinen Bedarf erreicht hat, scheidet aus. Ein gewähltes Ziel wächst über benachbarte
Abschnitte desselben Geräts, bis sein Restbedarf gedeckt ist – lange Ziele bekommen so einen zusammenhängenden Block, kurze
(Sternhaufen) nur eine halbe Stunde, und zwei Haufen passen in zwei Stunden. Abschnitte, die wegen Wolken oder fehlender
Ziele kaum Nutzen bringen, bleiben frei – das Ziel wandert so von selbst in eine spätere Nacht.
Bedarf je Ziel und Gerät: `need.need_hours` (Objektart/Flächenhelligkeit, Optik, Himmelsqualität, eigene Stacks) × Wunsch
kurz/normal/gründlich; schon gesammelte Belichtung eigener Projekte zählt mit. Alte Wünsche mit `min_hours` gelten weiter
(→ `depth`).
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
from . import deepsky, need, objects, tonight
from . import devices as devtab

STEP_MIN = tonight.STEP_MIN
MAX_NIGHTS = tonight.NIGHTS
MIN_USE = 0.25          # Abschnitt nur belegen, wenn mindestens 25 % seiner Dauer nutzbar sind
SLOT_H = 0.5            # Abschnittslänge (h); lange Ziele wachsen über benachbarte Abschnitte
REST_MIN = 0.15         # so viel Restbedarf (h) lohnt noch einen weiteren Abschnitt
MAX_CANDIDATES = 500
WISHES = ("new", "mix", "deep")
COMET = re.compile(r"^\s*(komet\b|comet\b|[CPDXI]/\d{4}|\d{1,4}[PDI](/|\b))", re.I)       # Kometen-Projekte (Name)
# Geräte zur Auswahl (Bildfeld lange × kurze Seite in Grad, wie beim Lösen importierter Bilder); „library“ = aus den Aufnahmen
DEVICES = devtab.planner_devices()          # aus devices.json (Geräte mit bekanntem Bildfeld)


def device_options(library_device: dict | None) -> list[dict]:
    out = [{"id": k, **v} for k, v in DEVICES.items()]
    if library_device and library_device.get("id") not in DEVICES:           # eigene Kamera: Bildfeld aus dem letzten Plate-Solving
        out.insert(0, {"id": "library", "name": library_device.get("name") or "Gerät aus deinen Aufnahmen",
                       "fov": library_device.get("fov"), "alt_az": bool(library_device.get("alt_az")),
                       "mag_limit": library_device.get("mag_limit") or deepsky.MAG_DEFAULT, "lib_id": library_device.get("id"),
                       **{k: library_device[k] for k in ("model", "exptime") if library_device.get(k)},
                       "eq_mode": devtab.profile({**library_device, "id": "library", "lib_id": library_device.get("id")})["eq_mode"]})
    return out


def clean_wishes(w: dict, options: list[dict]) -> dict:
    """Eingaben des Assistenten prüfen (feste deutsche Meldungen)."""
    ids = {d["id"] for d in options}
    try:
        nights = int(w.get("nights") or 3)
        hours = float(w.get("min_hours") or 2)
    except (TypeError, ValueError):
        raise ValueError("Nächte und Belichtung als Zahl angeben.") from None
    depth = str(w.get("depth") or "")
    if depth not in need.DEPTH:                 # alte Wünsche (feste Mindestbelichtung) → Faktor
        depth = "short" if hours <= 1 else ("thorough" if hours >= 3 else "normal")
    devices = [str(d) for d in (w.get("devices") or [])][:2]
    if not devices or any(d not in ids for d in devices) or len(set(devices)) != len(devices):
        raise ValueError("Ein oder zwei Geräte aus der Liste wählen.")
    mode = str(w.get("mode") or "mix")
    if mode not in WISHES:
        raise ValueError("Wunsch: neue Objekte, Mischung oder vorhandene vertiefen.")
    watch = [str(x)[:40] for x in (w.get("watch") or [])][:20]
    eq_ok = {d["id"] for d in options if d.get("eq_mode")}
    eq = [d for d in devices if d in {str(x) for x in (w.get("eq") or [])} and d in eq_ok]     # EQ-Modus nur, wo das Gerät ihn kann
    return {"nights": max(1, min(MAX_NIGHTS, nights)), "depth": depth, "devices": devices, "mode": mode, "watch": watch, "eq": eq}


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
        cat = next((t for t in deepsky.catalog() if t.keys & keys), None)
        own.append((p["ra"], p["dec"], (cat.size if cat else 0) / 120))
        hit = bool(keys & watch) or str(p["id"]) in watch
        kind = p.get("kind") if p.get("kind") not in (None, "generic") else (cat.kind if cat else "generic")
        out.append({"id": str(p["id"]), "name": p["name"], "kind": kind, "ra": p["ra"], "dec": p["dec"],
                    "category": "more", "href": f"#/p/{p['id']}", "prio": w_own * (2.0 if hit else 1.0), "watch": hit,
                    "fits": [True] * len(devices), "have": max(0.0, float(p.get("exposure") or 0)) / 3600,
                    "info": {"id": cat.id if cat else None, "kind": kind, "mag": cat.mag if cat else None, "size": cat.size if cat else None}})
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
                    "prio": w_new * appeal * (1 + bright) * (2.0 if hit else 1.0), "watch": hit, "fits": fits if any(fits) else [True] * len(devices),
                    "have": 0.0, "info": c})
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
    profs = [devtab.profile(d) for d in devices]
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
        az = tonight._az(ras, decs, lst, lat)
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
    per_dev = [base * devtab.weights(alt, az, lat, p) for p in profs]         # Bildfelddrehung/Zenit-Grenze je Gerät
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


def needs(tg: list[dict], devices: list[dict], wishes: dict, sky: tuple | None, calib_rows: list[dict] | None) -> tuple[np.ndarray, list, list]:
    """Bedarf (h) je Ziel × Gerät, Begründung je Ziel × Gerät und Eichung je Gerät."""
    sqm, src = sky or (None, None)
    cal = [need.calibration(calib_rows or [], d, sqm) for d in devices]
    out = np.zeros((len(tg), len(devices)))
    why = [[None] * len(devices) for _ in tg]
    for i, t in enumerate(tg):
        for di, d in enumerate(devices):
            r = need.need_hours(t["info"], d, sqm, calib=cal[di]["factor"], depth=wishes.get("depth", "normal"), sky_source=src)
            out[i, di], why[i][di] = r["hours"], r
    return out, why, cal


def plan(projects: list[dict], site_for, wishes: dict, devices: list[dict], cache: Path, now: float | None = None,
         weather: bool = False, sky=None, calib_rows: list[dict] | None = None) -> dict:
    """site_for(night) → Ort (mit `horizon`) oder None. sky(site) → (SQM, Herkunft) oder None (Himmelsqualität des Ortes).
    calib_rows: eigene Stacks mit Grenzgröße (`need.calibration_rows`). Rückgabe: Plan je Nacht und Gerät, Fortschritt je Ziel."""
    now = time.time() if now is None else now
    devices = [{**d, "eq": d["id"] in wishes.get("eq", [])} for d in devices]          # EQ-Modus je Gerät (Assistent)
    wishes = {**wishes, "depth": wishes.get("depth") or "normal"}
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
    sky_val = None
    if sky:
        try:
            sky_val = sky(first)
        except Exception:                                  # Himmelsqualität ist optional
            sky_val = None
    need_h, why, cal = needs(tg, devices, wishes, sky_val, calib_rows)          # Ziele × Geräte
    # Fortschritt als Anteil am Bedarf (zwei Geräte mit unterschiedlichem Bedarf zählen anteilig); eigene Belichtung zählt mit
    frac = np.array([min(1.0, t["have"] / need_h[i, 0]) for i, t in enumerate(tg)])
    got = np.zeros(len(tg))                     # im Plan gesammelte (gewichtete) Stunden
    per_dev_h = np.zeros((len(tg), len(devices)))
    prio = np.array([t["prio"] for t in tg])
    fits = np.array([t["fits"] for t in tg], dtype=bool)               # Ziele × Geräte
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
        slots = _slots(ts, dark & future, SLOT_H)
        free = [(di, a, b) for di in range(len(devices)) for a, b in slots]
        gains = {(di, a, b): per_dev[di][:, a:b].sum(axis=1) * STEP_MIN / 60 for di, a, b in free}
        busy: list[tuple[int, int, int]] = []          # (Ziel, a, b) bereits vergeben (für das zweite Gerät)
        blocks = []
        tonight_t = np.zeros(len(tg), dtype=bool)

        def clash(ki, a, b):                           # dasselbe Ziel nie gleichzeitig auf zwei Geräten
            return any(k2 == ki and ba < b and a < bb for k2, ba, bb in busy)

        while free:
            best = None
            rem_all = np.clip(1 - frac, 0, None)
            open_at = {(sl[0], sl[1]): sl for sl in free}
            open_to = {(sl[0], sl[2]): sl for sl in free}
            for si, slot in enumerate(free):
                di, a, b = slot
                gain = gains[slot]
                rem = rem_all * need_h[:, di]
                # Platz zum Wachsen: freie Nachbarabschnitte zählen halb – lange Ziele beginnen dort, wo sie weiterlaufen können
                nb = [gains[x] for x in (open_to.get((di, a)), open_at.get((di, b))) if x]
                room = gain + 0.5 * sum(nb) if nb else gain
                # angefangene Ziele zuerst fertig machen, statt immer neue zu beginnen
                score = prio * np.minimum(room, rem) * fits[:, di] * np.where((got > 0) & (rem > REST_MIN), 1.6, 1.0)
                score[gain < MIN_USE * (b - a) * STEP_MIN / 60] = 0     # nur Ziele, die den Abschnitt wirklich nutzen, kommen in Frage
                score[rem < REST_MIN * 0.5] = 0
                score[tonight_t] *= 0.4                  # zweiter, getrennter Block desselben Ziels in einer Nacht nur notfalls
                for ki, ba, bb in busy:
                    if ba < b and a < bb:
                        score[ki] = 0
                ki = int(np.argmax(score))
                if score[ki] > 0 and (best is None or score[ki] > best[0]):
                    best = (score[ki], si, ki)
            if best is None:
                break
            _, si, ki = best
            di, a, b = free.pop(si)
            gain = float(gains[(di, a, b)][ki])
            taken = [(a, b)]
            busy.append((ki, a, b))
            # Block wachsen lassen: benachbarte freie Abschnitte desselben Geräts, solange Restbedarf bleibt
            left = need_h[ki, di] * max(0.0, 1 - frac[ki]) - gain
            while left > REST_MIN:
                lo, hi = min(x for x, _ in taken), max(y for _, y in taken)
                cand = [(float(gains[sl][ki]), sl) for sl in free if sl[0] == di and (sl[2] == lo or sl[1] == hi)]
                cand = [(g, sl) for g, sl in cand if g >= MIN_USE * (sl[2] - sl[1]) * STEP_MIN / 60 and not clash(ki, sl[1], sl[2])]
                if not cand:
                    break
                g, sl = max(cand)
                free.remove(sl)
                taken.append((sl[1], sl[2]))
                busy.append((ki, sl[1], sl[2]))
                gain += g
                left -= g
            frac[ki] += gain / need_h[ki, di]
            tonight_t[ki] = True
            got[ki] += gain
            per_dev_h[ki, di] += gain
            for a, b in taken:
                g = float(gains[(di, a, b)][ki])
                used = np.where(per_dev[di][ki, a:b] > 0)[0] + a
                s, e = (int(used[0]), int(used[-1]) + 1) if len(used) else (a, b)
                blocks.append({"device": devices[di]["id"], "target": tg[ki]["id"], "start": float(ts[s]), "end": float(ts[min(e, len(ts) - 1)]),
                               "hours": round(g, 2), "max_alt": round(float(alt[ki, s:e].max()), 1) if e > s else None})
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
    targets = []
    for i in sorted(used, key=lambda i: -got[i]):
        di = int(np.argmax(per_dev_h[i]))                  # Bedarf des Geräts, das das Ziel hauptsächlich aufnimmt
        n_nights = sum(1 for n in nights if any(b["target"] == tg[i]["id"] for b in n["blocks"]))
        rest = max(0.0, (1 - frac[i]) * need_h[i, di])
        done = rest <= max(REST_MIN, 0.05 * need_h[i, di])           # gewichtete Stunden: kleiner Rest gilt als erreicht
        r = why[i][di]
        targets.append({**{k: tg[i][k] for k in ("id", "name", "kind", "ra", "dec", "category", "href", "watch")},
                        "hours": round(float(got[i]), 2), "goal": float(need_h[i, di]), "have": round(float(tg[i]["have"]), 2),
                        "done": bool(done), "nights": n_nights, "rest": 0.0 if done else round(rest, 2),
                        "more_nights": 0 if done else need.nights_for(rest, max(1.0, got[i] / max(1, n_nights))),
                        "why": r["why"], "assumed": r["assumed"]})
    missing = [tg[i]["name"] for i in range(len(tg)) if tg[i]["watch"] and got[i] == 0]
    return {"created": now, "wishes": wishes,
            "devices": [{**{k: d.get(k) for k in ("id", "name", "fov", "alt_az", "eq_mode", "eq")}, "tracking": devtab.profile(d)["label"],
                         **({"calibration": c["text"]} if c.get("text") else {})} for d, c in zip(devices, cal)],
            "site": {"id": first.get("id"), "name": first.get("name"), "horizon": bool(first.get("horizon"))},
            "sky": {"sqm": sky_val[0], "source": sky_val[1]} if sky_val and sky_val[0] is not None else None,
            "weather_on": bool(weather), "nights": nights, "targets": targets, "watch_missing": missing}
