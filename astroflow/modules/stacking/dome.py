"""Observatorium, Himmelskuppel: Bahnen der Ziele durch eine Nacht in Azimut/Höhe für die Ansicht von oben
(Zenit in der Mitte, Horizont am Rand). Je Ziel die Zeiten im freien Fenster – dunkel und über max(30°, Horizontmaske),
dieselbe Regel wie „Heute Nacht“ und der Planer –, dazu Mond und die Blöcke des gespeicherten Plans in dieser Nacht.

Alles lokal gerechnet (astropy ohne IERS-Download, wie der Planer); der Standort verlässt den Rechner nicht – an den Browser
gehen nur Name und Horizontmaske des Ortes, keine Koordinaten."""
from __future__ import annotations

import datetime as dt
import math
import time
import warnings

import numpy as np

from ...core import sites
from . import planner, tonight

STEP_MIN = tonight.STEP_MIN
MAX_TARGETS = 80
CIVIL = -6.0           # gezeigte Nacht: Sonne unter −6° (bürgerliche Dämmerung); frei = astronomisch dunkel (−18°)


def _spans(ts: np.ndarray, ok: np.ndarray) -> list[list[float]]:
    return [[round(a), round(b)] for a, b in tonight._spans(ts, ok)]


def _candidates(projects: list[dict], plan: dict | None, night: str) -> list[dict]:
    """Ziele: zuerst die des gespeicherten Plans (mit ihren Blöcken in dieser Nacht), dann eigene Objekte."""
    out, seen = [], set()
    pn = next((n for n in (plan or {}).get("nights", []) if n.get("night") == night), None)
    for t in (plan or {}).get("targets", []):
        if t.get("ra") is None or t.get("dec") is None:
            continue
        blocks = [{"start": b["start"], "end": b["end"], "device": b.get("device")} for b in (pn or {}).get("blocks", []) if b.get("target") == t["id"]]
        out.append({"id": str(t["id"]), "name": t["name"], "ra": float(t["ra"]), "dec": float(t["dec"]), "href": t.get("href"),
                    "source": "plan", "blocks": blocks})
        seen |= {str(t["id"]), str(t["name"]).lower()}
    for p in projects:
        if p.get("ra") is None or p.get("dec") is None or str(p["id"]) in seen or str(p.get("name") or "").lower() in seen:
            continue
        if p.get("kind") == "comet" or planner.COMET.match(str(p.get("name") or "")):     # Kometen wandern: feste Koordinaten taugen nicht
            continue
        out.append({"id": str(p["id"]), "name": p["name"], "ra": float(p["ra"]), "dec": float(p["dec"]), "href": f"#/p/{p['id']}",
                    "source": "own", "blocks": []})
        seen.add(str(p["id"]))
    return out[:MAX_TARGETS]


def dome(projects: list[dict], site_for, plan: dict | None, now: float | None = None, k: int = 0, date: str = "") -> dict:
    """site_for(night) → Ort (mit `horizon`) oder None. k: 0 = die gerade geplante Nacht, 1 … = so viele Nächte später;
    `date` (JJJJ-MM-TT) hat Vorrang. Rückgabe siehe Modulkopf; Zeiten in Unix-Sekunden, Bahnen in 10-min-Schritten."""
    now = time.time() if now is None else now
    first = site_for(None)
    if not first:
        return {"error": "Für die Himmelskuppel fehlt ein Standort (Einstellungen → Standorte)."}
    base0, _ = tonight._window(now, first["lon"])
    night0 = sites.planning_night(now, first["lon"])
    if date:
        try:
            k = (dt.date.fromisoformat(date) - dt.date.fromisoformat(night0)).days
        except ValueError:
            k = 0
    k = max(0, min(tonight.NIGHTS - 1, int(k)))
    night = (dt.date.fromisoformat(night0) + dt.timedelta(days=k)).isoformat()
    site = site_for(night) or first
    lat, lon, horizon = site["lat"], site["lon"], site.get("horizon")

    from astropy import units as u
    from astropy.coordinates import EarthLocation, get_body
    from astropy.time import Time
    from astropy.utils import iers

    t0 = base0 + k * 86400
    ts = np.arange(t0, t0 + 86400 + 1, STEP_MIN * 60, dtype=float)
    with warnings.catch_warnings(), iers.conf.set_temp("auto_download", False), iers.conf.set_temp("auto_max_age", None), \
            iers.conf.set_temp("iers_degraded_accuracy", "warn"):
        warnings.simplefilter("ignore")
        t = Time(ts, format="unix")
        lst = t.sidereal_time("mean", longitude=lon * u.deg).rad
        sun_g, moon_g, moon_c = get_body("sun", t), get_body("moon", t, EarthLocation(lat=lat * u.deg, lon=lon * u.deg)), get_body("moon", t)
        illum = (1 - np.cos(sun_g.separation(moon_c).rad)) / 2
        sun = _body_alt(sun_g.ra.rad, sun_g.dec.rad, lst, lat)
        mra, mdec = moon_g.ra.rad, moon_g.dec.rad
    # gezeigte Nacht: Sonne unter −6° (sonst unter 0°, in hellen Sommernächten); ein Schritt Rand
    show = sun <= CIVIL
    if not show.any():
        show = sun <= 0
    if not show.any():
        return {"error": "In dieser Nacht geht die Sonne nicht unter.", "night": night, "k": k}
    idx = np.where(show)[0]
    lo, hi = max(0, int(idx[0]) - 1), min(len(ts), int(idx[-1]) + 2)
    ts, lst, sun = ts[lo:hi], lst[lo:hi], sun[lo:hi]
    mra, mdec, illum = mra[lo:hi], mdec[lo:hi], illum[lo:hi]
    dark = sun <= -18
    moon_alt, moon_az = _body_alt(mra, mdec, lst, lat), _body_az(mra, mdec, lst, lat)

    tg = _candidates(projects, plan, night)
    targets = []
    if tg:
        ras, decs = np.radians([x["ra"] for x in tg]), np.radians([x["dec"] for x in tg])
        alt, az = tonight._alt(ras, decs, lst, lat), tonight._az(ras, decs, lst, lat)
        lim = np.broadcast_to(tonight._limit(ras, decs, lst, lat, horizon), alt.shape)
        free = (alt >= lim) & dark[None, :]
        for i, x in enumerate(tg):
            above = alt[i] > 0
            targets.append({**x, "alt": [round(float(v), 1) for v in alt[i]], "az": [round(float(v), 1) for v in az[i]],
                            "free": _spans(ts, free[i]), "free_hours": round(float(free[i].sum()) * STEP_MIN / 60, 2),
                            "max_alt": round(float(alt[i][dark].max() if dark.any() else alt[i].max()), 1),
                            "why": None if free[i].any() else _why(alt[i], lim[i], above, dark)})
    mid = int(np.argmax(dark)) + int(dark.sum()) // 2 if dark.any() else len(ts) // 2
    return {"night": night, "k": k, "nights": tonight.NIGHTS, "now": now,
            "site": {"name": site.get("name") or "Standort", "horizon": horizon},
            "t0": float(ts[0]), "step": STEP_MIN * 60, "n": len(ts),
            "sun": [round(float(v), 1) for v in sun], "dark": _spans(ts, dark), "min_alt": tonight.MIN_ALT,
            "moon": {"alt": [round(float(v), 1) for v in moon_alt], "az": [round(float(v), 1) for v in moon_az],
                     "illum": round(float(illum[mid]) * 100)},
            "targets": targets}


def _why(alt: np.ndarray, lim: np.ndarray, above: np.ndarray, dark: np.ndarray) -> str:
    """Kurzer Grund, warum ein Ziel in dieser Nacht nie frei ist."""
    if not dark.any():
        return "keine astronomische Dunkelheit"
    if not (above & dark).any():
        return "bleibt unter dem Horizont"
    if float(alt[dark].max()) < tonight.MIN_ALT:
        return f"steigt nur auf {round(float(alt[dark].max()))}°"
    return "hinter der Horizontmaske"


def _body_alt(ra: np.ndarray, dec: np.ndarray, lst: np.ndarray, lat: float) -> np.ndarray:
    """Höhe eines Körpers mit zeitabhängigem ra/dec (rad) je Zeit."""
    p = math.radians(lat)
    return np.degrees(np.arcsin(np.clip(math.sin(p) * np.sin(dec) + math.cos(p) * np.cos(dec) * np.cos(lst - ra), -1, 1)))


def _body_az(ra: np.ndarray, dec: np.ndarray, lst: np.ndarray, lat: float) -> np.ndarray:
    p = math.radians(lat)
    h = lst - ra
    return np.degrees(np.arctan2(-np.cos(dec) * np.sin(h), np.sin(dec) * math.cos(p) - np.cos(dec) * np.cos(h) * math.sin(p))) % 360
