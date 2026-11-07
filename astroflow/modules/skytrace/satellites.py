"""Satellitenhelligkeit (mag) und Satelliten je Belichtungsstunde – „wie füllt sich der Himmel“ (Wunsch 18, 0.39.0).

Alles wird aus den gespeicherten Läufen gerechnet, ohne Neuanalyse und ohne Engine-Interpreter (nur numpy):
- **Fluss** aus `evidence.profile` des Kandidaten (Querprofil der Spur im Trefferbild, ADU nach 2×2-Mittelung, Seitenhintergrund
  abgezogen, Median entlang der Spur) × Spurlänge × Binning² = Summe der Rohpixel-ADU über die Spur. Geteilt durch die Zeit, die
  der Satellit für genau dieses Spurstück brauchte (`passage_seconds` des Katalogtreffers: Durchgang an Spuranfang und -ende) ergibt
  das den Fluss in ADU/s – wie bei einem Stern (Nullpunkt: 1 ADU/s). Länge und Zeit kürzen sich weitgehend (grob gerasterte Enden).
- **Nullpunkt** je Nacht aus den Gaia-geeichten Stacks des Bilder-Moduls (Dienst `night_zero_points`, über der Atmosphäre);
  ohne eigene Nacht der Median des Geräts, sonst für Stellina ein Richtwert (`NOMINAL_ZP`) – als Schätzung gekennzeichnet.
  Extinktion: `K_EXT` × Luftmasse der Bildfeldhöhe. Ergebnis = scheinbare Helligkeit im Luminanzband, keine Normhelligkeit
  (keine Entfernungs-/Phasenreduktion), Unsicherheit grob 0,3–0,6 mag.
- **Rate**: Spuren je Stunde nutzbarer Belichtung (angenommene Aufnahmen × Belichtungszeit), nur bei Sonne unter −12°."""
from __future__ import annotations

import datetime as dt
import math
import re

import numpy as np

SUN_NIGHT = -12.0            # Sonnenhöhe, unter der eine Aufnahme als Nachtzeit zählt (nautische Dämmerung)
K_EXT = 0.20                 # Extinktion mag je Luftmasse (wie stacking.skyquality)
PROFILE_HALF = 7             # Querprofil: Summe über ±7 Pixel (Spur-FWHM ~2,4 px, Seitenhintergrund ab ±8)
MIN_PASSAGE_S = 0.02
NOMINAL_ZP = {"stellina": 22.0}          # Richtwert (Median der Gaia-Nullpunkte aller Nächte), nur als Schätzung
ZP_ERR = {"night": 0.3, "device": 0.4, "estimate": 0.6}


# ---- Astronomie (numpy, ~0,05° genau – reicht für Nacht/Luftmasse) ---------------------------------------------------
def _jd(ts) -> np.ndarray:
    return np.asarray(ts, float) / 86400.0 + 2440587.5


def altitude(ts, ra, dec, lat, lon) -> np.ndarray:
    """Höhe (°) eines Himmelsorts (ra, dec in °) zur Unix-Zeit ts am Ort (lat, lon in °)."""
    d = _jd(ts) - 2451545.0
    lst = np.radians((280.46061837 + 360.98564736629 * d + lon) % 360.0)
    ra, dec, lat = np.radians(ra), np.radians(dec), math.radians(lat)
    return np.degrees(np.arcsin(np.sin(lat) * np.sin(dec) + np.cos(lat) * np.cos(dec) * np.cos(lst - ra)))


def sun_altitude(ts, lat, lon) -> np.ndarray:
    d = _jd(ts) - 2451545.0
    L = np.radians((280.460 + 0.9856474 * d) % 360.0)
    g = np.radians((357.528 + 0.9856003 * d) % 360.0)
    lam = L + np.radians(1.915) * np.sin(g) + np.radians(0.020) * np.sin(2 * g)
    eps = math.radians(23.439)
    ra = np.degrees(np.arctan2(np.cos(eps) * np.sin(lam), np.cos(lam)))
    dec = np.degrees(np.arcsin(math.sin(eps) * np.sin(lam)))
    return altitude(ts, ra, dec, lat, lon)


def airmass(alt) -> np.ndarray:
    h = np.clip(np.asarray(alt, float), 5.0, 90.0)
    return 1.0 / (np.sin(np.radians(h)) + 0.50572 * (h + 6.07995) ** -1.6364)


# ---- Fluss einer Spur ------------------------------------------------------------------------------------------------
def _num(v) -> float | None:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) else None


def phot_of(c: dict, match: dict) -> dict | None:
    """Messwerte einer Satellitenspur für die Helligkeit (klein, wird mit der Statistik gecacht). None, wenn etwas fehlt."""
    ev = c.get("evidence") or {}
    pr = ev.get("profile") or {}
    try:
        off, val = np.asarray(pr["offset_pixels"], float), np.asarray(pr["contrast_adu"], float)
    except (KeyError, TypeError, ValueError):
        return None
    if off.shape != val.shape or not off.size:
        return None
    s = float(val[np.abs(off) <= PROFILE_HALF].sum())
    ps = match.get("passage_seconds")
    t = abs(ps[1] - ps[0]) if isinstance(ps, (list, tuple)) and len(ps) == 2 and all(_num(x) is not None for x in ps) else None
    length = _num(ev.get("length_pixels"))
    exposure = _num(c.get("exposure_seconds"))
    pl = c.get("plausibility") or {}
    try:
        b = float(pl["original_size"][0]) / float(pl["analysis_size"][0])
    except (KeyError, TypeError, ValueError, IndexError, ZeroDivisionError):
        b = 1.0
    if not (s > 0 and length and t and t >= MIN_PASSAGE_S and exposure):
        return None
    if t > 1.3 * exposure:                                   # Durchgangszeit länger als die Belichtung: Zuordnung unplausibel
        return None
    wp = c.get("world_path") or []
    site = c.get("site") or {}
    return dict(s=round(s, 2), L=round(length, 1), b=round(b, 3), t=round(t, 3), exp=exposure,
                ra=_num(wp[0][0]) if wp else None, dec=_num(wp[0][1]) if wp else None,
                lat=_num(site.get("lat")), lon=_num(site.get("lon")))


def flux_rate(ph: dict) -> float:
    """Fluss der Spur in ADU/s (Summe der Rohpixel, Luminanz-Näherung)."""
    return ph["s"] * ph["b"] ** 2 * ph["L"] / ph["t"]


def magnitude(ph: dict, ts: float | None, zp: float) -> float:
    """Scheinbare Helligkeit: ZP (über der Atmosphäre) − Extinktion − 2,5·lg(Fluss)."""
    x = 1.4                                                  # ohne Ort/Zeit: Höhe ~45°
    if None not in (ph.get("ra"), ph.get("dec"), ph.get("lat"), ph.get("lon")) and ts is not None:
        x = float(airmass(altitude(ts, ph["ra"], ph["dec"], ph["lat"], ph["lon"])))
    return zp - K_EXT * x - 2.5 * math.log10(flux_rate(ph))


def family(name: str | None, norad=None) -> str:
    n = (name or "").upper()
    return "starlink" if n.startswith("STARLINK") else "oneweb" if n.startswith("ONEWEB") else "other"


def pick_zp(zps: dict | None, night: str | None, device: str | None) -> tuple[float | None, str | None]:
    """(Nullpunkt, Quelle night|device|estimate)."""
    zps = zps or {}
    names = [device] if device else []
    if device and "|" in device:
        i, g = device.split("|", 1)
        try:
            names.append(f"{i}|{float(g)}")
        except ValueError:
            pass
    for n in names:
        v = (zps.get("nights") or {}).get(f"{night}|{n}")
        if v is not None:
            return float(v), "night"
    for n in names:
        v = (zps.get("devices") or {}).get(n)
        if v is not None:
            return float(v), "device"
    for key, v in NOMINAL_ZP.items():
        if device and key in device.lower():
            return v, "estimate"
    return None, None


# ---- Sammeln ---------------------------------------------------------------------------------------------------------
def _ym(night: str | None, times) -> tuple[int, int] | None:
    m = re.match(r"^(\d{4})-(\d\d)", night or "")
    if m:
        return int(m[1]), int(m[2])
    if len(times):
        d = dt.datetime.fromtimestamp(float(min(times)), dt.timezone.utc) - dt.timedelta(hours=12)
        return d.year, d.month
    return None


def collect(data: dict, zps: dict | None, resolve_site, top: int = 5) -> dict:
    """`data` = stats.collect (mit `_full`), `resolve_site(session)` -> Ort oder None. Liefert kleine Zellen je Ort/Jahr/Monat."""
    cells: dict[tuple, dict] = {}
    sites: dict[str, dict] = {}
    best: list[dict] = []
    src_count = {"night": 0, "device": 0, "estimate": 0, "none": 0}
    skipped = 0
    for s in data.get("_full") or []:
        times = np.asarray(s.get("_times") or [], float)
        exp = s.get("_exp")
        if not len(times) or not exp:
            skipped += 1
            continue
        ym = _ym(s.get("night"), times)
        if ym is None:
            skipped += 1
            continue
        exps = np.asarray(s.get("_exps") or [], float)
        exps = np.where(np.isfinite(exps), exps, exp) if len(exps) == len(times) else np.full(len(times), float(exp))
        dev = (s.get("_dev") or "").split("|")[0] or "Gerät unbekannt"      # verschiedene Bildfelder nie in einer Rate mischen
        site = resolve_site(s)
        if site and site.get("lat") is not None:
            key = str(site.get("site_id") or f"{site['lat']:.3f},{site['lon']:.3f}")
            name = site.get("name") or "Standort"
            night = sun_altitude(times, site["lat"], site["lon"]) < SUN_NIGHT
        else:
            key, name, night = "?", "Standort unbekannt", np.ones(len(times), bool)
        hours = float(exps[night].sum()) / 3600.0
        key = f"{key}|{dev}"
        sites.setdefault(key, {"key": key, "name": name, "device": dev, "hours": 0.0, "n": 0})["hours"] += hours
        cell = cells.setdefault((key, *ym), {"site": key, "y": ym[0], "m": ym[1], "h": 0.0, "n": 0, "sl": 0, "ow": 0, "mags": []})
        cell["h"] += hours
        for c in s.get("_sat") or []:
            ts = c.get("ts")
            if site and site.get("lat") is not None and isinstance(ts, (int, float)) and sun_altitude(ts, site["lat"], site["lon"]) >= SUN_NIGHT:
                continue                                      # Dämmerung zählt nicht (Zähler und Nenner gleich behandelt)
            fam = family(c.get("name"), c.get("norad"))
            cell["n"] += 1
            sites[key]["n"] += 1
            cell["sl"] += fam == "starlink"
            cell["ow"] += fam == "oneweb"
            ph = c.get("ph")
            zp, src = pick_zp(zps, s.get("night"), s.get("_dev")) if ph else (None, None)
            if zp is None:
                src_count["none"] += 1
                continue
            mag = round(magnitude(ph, ts, zp), 1)
            src_count[src] += 1
            cell["mags"].append(mag)
            best.append(dict(mag=mag, src=src, name=c.get("name"), norad=c.get("norad"), family=fam, night=s.get("night"),
                             run=c.get("run"), id=c.get("id"), site=name, group=key, err=ZP_ERR[src]))
    best.sort(key=lambda b: b["mag"])
    out_cells = [dict(c, h=round(c["h"], 2)) for c in sorted(cells.values(), key=lambda c: (c["site"], c["y"], c["m"]))
                 if c["h"] > 0 or c["n"]]
    return dict(v=1, sites=[dict(s, hours=round(s["hours"], 1)) for s in sorted(sites.values(), key=lambda s: -s["hours"])],
                cells=out_cells, brightest=best[:top], zp=src_count, skipped=skipped,
                method=dict(night_deg=SUN_NIGHT, k_ext=K_EXT, errors=ZP_ERR))
