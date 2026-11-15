"""„Plane meine Nacht“ (Teleskop-Modul): Zeitplan einer Nacht für ein Gerät aus dem gemessenen freien Himmel.

Rechnet mit dem Planer (`planner.plan`: eine Nacht, ein Gerät, Wunsch „Mischung“) – dieselben Regeln wie Planer und Heute
Nacht: astronomisch dunkel, Höhe über max(Mindesthöhe, Grenze), Mondgewicht, Wolken je Stunde, Bildfelddrehung je Gerät,
Belichtungsbedarf (`need`) abzüglich schon gesammelter Belichtung, angefangene Ziele zuerst, wenige lange Blöcke.
Dazu: höchstens `max_targets` Ziele, ein aktiver Komet (Position dieser Nacht über Horizons, nur wenn erreichbar),
noch fehlende Messier-Objekte der Sammlung leicht bevorzugt, und eine kurze Begründung je Ziel.

Bereitgestellt als Dienst `night_autoplan` (Bilder-Modul). Der Standort bleibt im Rechner: Wetter wie Heute Nacht (gerundet),
Komet geozentrisch."""
from __future__ import annotations

import datetime as dt
import time
import traceback
import warnings
from pathlib import Path

import numpy as np

from . import comet, need, objects, planner, tonight

MIN_BLOCK_S = 20 * 60        # kürzere Blöcke lohnen den Schwenk nicht
SHORT_S = 45 * 60            # … unter dieser Dauer nur, wenn der Block mindestens ein Drittel des Bedarfs deckt
COMET_DAYS = 30              # Komet gilt als aktiv, wenn er in den letzten … Tagen aufgenommen wurde
COMET_PRIO = 1.6
MOON_NEAR = 45               # Grad: darunter erwähnt die Begründung den Mond


def _hm(t: float) -> str:
    return time.strftime("%H:%M", time.localtime(t))


def _comets(db, projects: list[dict], now: float, cache: Path, t_mid: float) -> tuple[list[dict], list[str]]:
    """Aktive Kometen (Kometenmodus an bzw. automatisch erkannt, Aufnahme in den letzten 30 Tagen) mit Position dieser Nacht.
    Ohne Horizons (abgeschaltet, kein Netz) entfallen sie mit Hinweis – geplant wird trotzdem."""
    import json
    since = dt.date.fromtimestamp(now - COMET_DAYS * 86400).isoformat()
    out, notes = [], []
    try:
        rows = db.q("SELECT p.id, p.name, p.kind, p.options, MAX(f.night) last FROM projects p JOIN frames f ON f.project_id=p.id "
                    "WHERE f.kind='light' GROUP BY p.id")
    except Exception:
        traceback.print_exc()
        return [], []
    have = {p["id"]: max(0.0, float(p.get("exposure") or 0)) / 3600 for p in projects}
    for r in rows:
        opt = json.loads(r.get("options") or "{}") if isinstance(r.get("options"), str) else (r.get("options") or {})
        if not comet.active(r, opt) or not r.get("last") or str(r["last"]) < since:
            continue
        desig = comet.designation(r["name"])
        if not desig:
            continue
        try:
            eph = comet.horizons(desig, t_mid - 3600, t_mid + 3600, cache)
            ra, dec = float(eph[:, 1].mean()), float(eph[:, 2].mean())
        except Exception:
            notes.append(f"Komet {desig}: Position für heute Nacht nicht abrufbar – nicht eingeplant.")
            continue
        out.append({"id": str(r["id"]), "name": comet.display_name(r["name"]), "kind": "comet", "ra": ra, "dec": dec,
                    "category": "comet", "href": f"#/p/{r['id']}", "prio": COMET_PRIO, "watch": False, "fits": [True],
                    "have": have.get(r["id"], 0.0), "info": {"id": None, "kind": "generic", "mag": None, "size": None}})
    return out, notes


def _moon(ts: list[float], lat: float, lon: float):
    """Mondposition (RA/Dec in Grad) und Beleuchtung an den Zeitpunkten (astropy, ohne Netz)."""
    from astropy import units as u
    from astropy.coordinates import EarthLocation, get_body
    from astropy.time import Time
    from astropy.utils import iers
    with warnings.catch_warnings(), iers.conf.set_temp("auto_download", False), iers.conf.set_temp("auto_max_age", None):
        warnings.simplefilter("ignore")
        t = Time(ts, format="unix")
        loc = EarthLocation(lat=lat * u.deg, lon=lon * u.deg)
        m, s, mc = get_body("moon", t, loc), get_body("sun", t), get_body("moon", t)
        illum = (1 - np.cos(s.separation(mc).rad)) / 2
        return [(float(a), float(b), float(c)) for a, b, c in zip(m.ra.deg, m.dec.deg, illum)]


def _sep(ra1, dec1, ra2, dec2) -> float:
    return objects.ang_sep(ra1, dec1, ra2, dec2)


def _altaz(ra, dec, lat, lon, t):
    from .observatory import _altaz as f
    return f(ra, dec, lat, lon, t)


def _peak(ra: float, dec: float, lat: float, lon: float, t0: float, t1: float) -> tuple[float, float]:
    """Höchster Stand zwischen t0 und t1 (10-min-Schritte): (Zeit, Höhe)."""
    from .observatory import _altaz
    best = (t0, -90.0)
    t = t0
    while t <= t1:
        alt = _altaz(ra, dec, lat, lon, t)[1]
        if alt > best[1]:
            best = (t, alt)
        t += 600
    return best


def night_plan(db, ctx, work_dir: Path, *, model: str, site: dict, horizon: list | None, now: float, weather: bool = True,
               max_targets: int = 5, have_add: dict | None = None, top: list | None = None, exclude: list | None = None) -> dict:
    """Plan dieser Nacht ab `now`: {blocks: [{id, name, ra, dec, start, end, hours, category, reason, …}], night, moon, cloud,
    device, notes} oder {error}. `site` = {id, name, lat, lon}; `horizon` = 36 Höhen (gemessene Grenze), None = Ortsmaske;
    `top` = 36 Höhen eines gemessenen Überhangs (None je Richtung = frei bis zum Zenit)."""
    devs = planner.device_options(None)
    dev = next((d for d in devs if d["id"] == model), None) or next((d for d in devs if model and d["id"].startswith(model)), None)
    if dev is None:
        return {"error": "Für dieses Gerät fehlen Bildfeld und Grenzgröße in der Gerätetabelle."}
    projects, _ = tonight.library(db)
    try:
        shot = need.project_devices(db)
    except Exception:
        traceback.print_exc()
        shot = {}
    projects = [{**p, "device": shot.get(p["id"])} for p in projects]
    hz = [float(v) for v in horizon] if horizon and len(horizon) == 36 else site.get("horizon")
    here = {**site, "horizon": hz, "top": list(top) if top and len(top) == 36 else None}
    sky = None
    try:
        from .skyquality import for_site
        s = for_site(db, site.get("id"))
        sky = (float(s["sqm"]), "aus deinen Aufnahmen") if s and s.get("sqm") is not None else None
    except Exception:
        traceback.print_exc()
    try:
        calib_rows = need.calibration_rows(db)
    except Exception:
        traceback.print_exc()
        calib_rows = []
    coll = None
    svc = ctx.services.get("collection_progress") if ctx else None
    if svc:
        try:
            coll = svc()
        except Exception:
            coll = None
    cache = Path(work_dir) / "tutor"
    base0, base1 = tonight._window(now, site["lon"])
    extra, notes = _comets(db, projects, now, cache, (base0 + base1) / 2)
    wishes = {"nights": 1, "depth": "normal", "devices": [dev["id"]], "mode": "mix", "watch": [], "eq": []}
    out = planner.plan(projects, lambda night: here, wishes, [dev], cache, now=now, weather=weather, sky=lambda s: sky,
                       calib_rows=calib_rows, max_targets=max(1, int(max_targets)), extra=extra, have_add=have_add or {},
                       collection=coll, exclude=exclude)
    if out.get("error"):
        return {"error": out["error"], "notes": notes}
    night = out["nights"][0]
    tg = {t["id"]: t for t in out["targets"]}
    # Mindestdauer: kurze Blöcke lohnen den Schwenk nicht; ein Stummel unter 45 min für ein Ziel, das viel mehr braucht, auch nicht
    blocks = [b for b in night["blocks"] if b["target"] in tg and b["end"] - b["start"] >= MIN_BLOCK_S
              and not (b["end"] - b["start"] < SHORT_S and (b["end"] - b["start"]) / 3600 < tg[b["target"]]["goal"] / 3)]
    dark = night.get("dark") or []
    d0, d1 = (dark[0][0], dark[-1][1]) if dark else (base0, base1)
    moon = _moon([(b["start"] + b["end"]) / 2 for b in blocks], site["lat"], site["lon"]) if blocks else []
    res = []
    for b, m in zip(blocks, moon):
        t = tg[b["target"]]
        pt, palt = _peak(t["ra"], t["dec"], site["lat"], site["lon"], max(now, d0), d1)
        alts = [_altaz(t["ra"], t["dec"], site["lat"], site["lon"], b["start"] + k * (b["end"] - b["start"]) / 4)[1] for k in range(5)]
        lo, hi = round(min(alts)), round(max(alts))
        when = f"{_hm(b['start'])}–{_hm(b['end'])}"
        why = [f"steht {when} am höchsten ({round(palt)}° um {_hm(pt)})" if b["start"] - 1200 <= pt <= b["end"] + 1200
               else f"steht {when} {lo}–{hi}° hoch (am höchsten {round(palt)}° um {_hm(pt)})"]
        h = (b["end"] - b["start"]) / 3600
        if t["category"] == "comet":
            why.insert(0, "Aktiver Komet – Position von heute Nacht")
        elif t["category"] == "more":
            why.insert(0, f"Dein Projekt fortsetzen: hat {need.fmt(t['have'])}, braucht ca. {need.fmt(t['goal'])}" if t["have"] >= 0.05
                       else f"Dein Projekt, braucht ca. {need.fmt(t['goal'])}")
        else:
            kl = objects.KIND_LABEL.get(t.get("kind") or "", "") or "Objekt"
            why.insert(0, f"Neu für deine Sammlung ({kl}), braucht ca. {need.fmt(t['goal'])}")
        why.append(f"heute {need.fmt(h)}" + (" – dann erreicht" if t.get("done") else f", danach fehlen noch ca. {need.fmt(t['rest'])}" if t.get("rest") else ""))
        sep = _sep(t["ra"], t["dec"], m[0], m[1])
        if m[2] > 0.3 and sep < MOON_NEAR:
            why.append(f"Mond {round(sep)}° entfernt ({round(m[2] * 100)} % beleuchtet)")
        res.append({"id": t["id"], "name": t["name"], "ra": t["ra"], "dec": t["dec"], "start": b["start"], "end": b["end"],
                    "hours": round(h, 2), "category": t["category"], "kind": t.get("kind"), "max_alt": b.get("max_alt"),
                    "peak_t": pt, "peak_alt": round(palt, 1), "goal": t["goal"], "have": t["have"], "rest": t.get("rest"),
                    "reason": " · ".join(why)})
    return {"blocks": res, "night": night["night"], "moon": night.get("moon"), "cloud": night.get("cloud"),
            "weather": night.get("weather"), "dark": dark, "device": dev.get("name"), "notes": notes}
