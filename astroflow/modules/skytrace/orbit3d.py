"""3D-Ansicht: Satellitenbahnen (SGP4), Teleskop-Blickfeld und Standort für einen Kandidaten berechnen.

Bezugssystem „D“ = erdfestes System (ECEF) zur Bezugszeit t0, danach raumfest: Darin dreht sich die Erde mit
der Sternzeit (Winkel `earth_rate`·(t − t0) um die Nordachse), Bahnen sind geschlossene Ellipsen und das nachgeführte
Blickfeld zeigt fest in eine Richtung, während sein Ursprung mit dem Standort wandert. Erdfeste Dinge (Standort,
Meteore, Flugzeuge) kommen als ECEF, die Oberfläche dreht sie mit der Erde. Einheiten: km, Unix-Sekunden.

Nur numpy, erfa (kommt mit astropy) und sgp4. Läuft im Server; fehlt dort sgp4, ruft die API dieses Skript im
Engine-Interpreter auf (`python orbit3d.py` mit JSON auf stdin, Ergebnis auf stdout).
Genauigkeit: UT1 ≈ UTC (≤ 0,9 s ≈ 14″ Erddrehung), keine Polbewegung – für die Darstellung weit genug.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import sys
import warnings
from functools import lru_cache
from pathlib import Path

import erfa
import numpy as np

EARTH_RATE = 7.292115855e-5                 # rad/s, Änderung der mittleren Sternzeit
WINDOW_S = 600                              # Zeitregler ± 10 min um den Durchgang
STEP_S = 2
RING_POINTS = 360
COLORS = ("#7c9cff", "#5eead4", "#f2d385", "#b18cff", "#f87171")


def _jd(t):
    """Unix-Sekunden → zweiteiliges Julianisches Datum (UTC)."""
    t = np.asarray(t, dtype=float)
    day = np.floor(t / 86400)
    return 2440587.5 + day, (t - day * 86400) / 86400


def _tt(t):
    jd1, jd2 = _jd(t)
    with warnings.catch_warnings():                       # „dubious year“ für Zeiten nach der Schaltsekundentabelle
        warnings.simplefilter("ignore")
        tai = erfa.utctai(jd1, jd2)
        return erfa.taitt(*tai)


def gmst(t):
    jd1, jd2 = _jd(t)
    return erfa.gmst82(jd1, jd2)                         # TEME → erdfest (Vallado), UT1 ≈ UTC


def rot_z(angle, v):
    """Aktive Drehung um die z-Achse (positiv = nach Osten); v (..., 3)."""
    v = np.asarray(v, dtype=float)
    c, s = np.cos(angle), np.sin(angle)
    return np.stack(np.broadcast_arrays(c * v[..., 0] - s * v[..., 1], s * v[..., 0] + c * v[..., 1], v[..., 2]), axis=-1)


def icrs_to_teme(t):
    """Matrix ICRS/GCRS-Richtung → TEME: Präzession/Nutation (IAU 2006/2000A) + Gleichung der Äquinoktien."""
    tt1, tt2 = _tt(t)
    return erfa.rz(erfa.eqeq94(tt1, tt2), erfa.pnm06a(tt1, tt2))


def unit_radec(ra, dec):
    ra, dec = np.radians(np.asarray(ra, float)), np.radians(np.asarray(dec, float))
    return np.stack([np.cos(dec) * np.cos(ra), np.cos(dec) * np.sin(ra), np.sin(dec)], axis=-1)


def radec_to_d(ra, dec, t0):
    """Himmelsrichtungen (ICRS, wie die Bild-WCS) → Bezugssystem D: TEME, um GMST(t0) gedreht."""
    teme = unit_radec(ra, dec) @ icrs_to_teme(t0).T
    return rot_z(-gmst(t0), teme)


def geodetic_to_ecef(lat, lon, alt_m):
    xyz = erfa.gd2gc(1, np.radians(np.asarray(lon, float)), np.radians(np.asarray(lat, float)), np.asarray(alt_m, float))
    return np.asarray(xyz) / 1000


def ecef_height_km(ecef_km):
    _, _, h = erfa.gc2gd(1, np.asarray(ecef_km, float) * 1000)
    return np.asarray(h) / 1000


def sun_d(t0):
    """Richtung zur Sonne in D (für die Tag-Nacht-Grenze)."""
    tt1, tt2 = _tt(t0)
    pvh, _ = erfa.epv00(tt1, tt2)
    v = -np.asarray(pvh["p"] if pvh.dtype.names else pvh[0])
    v = rot_z(-gmst(t0), v @ icrs_to_teme(t0).T)
    return v / np.linalg.norm(v)


# ---------------------------------------------------------------- Bahnelemente ---
def satrec_from_tle(l1, l2):
    from sgp4.api import Satrec, WGS72
    return Satrec.twoline2rv(l1, l2, WGS72)


def satrec_from_record(row):
    """Wie `engine/satellite_archive.model_from_record` (Space-Track-Spiegel) – gleiche Bahn wie beim Abgleich."""
    from sgp4.api import Satrec, WGS72, jday
    epoch = dt.datetime.fromisoformat(str(row["epoch"]).replace("Z", "+00:00"))
    jd, fr = jday(epoch.year, epoch.month, epoch.day, epoch.hour, epoch.minute, epoch.second + epoch.microsecond / 1e6)
    model = Satrec()
    factor = 2 * math.pi / 1440
    model.sgp4init(WGS72, "i", int(row["norad_id"]), jd + fr - 2433281.5, float(row["bstar"]),
                   float(row.get("mean_motion_dot", 0)) * factor / 1440, 0., float(row["eccentricity"]),
                   math.radians(row["arg_perigee"]), math.radians(row["inclination"]), math.radians(row["mean_anomaly"]),
                   row["mean_motion"] * factor, math.radians(row["raan"]))
    return model


@lru_cache(maxsize=4)
def _orbit_records(path: str, stamp: int):
    rows = json.loads(Path(path).read_text(encoding="utf-8")).get("records") or []
    return {int(r["norad_id"]): r for r in rows if r.get("norad_id") is not None}


@lru_cache(maxsize=8)
def _eth_tles(path: str, stamp: int):
    out = {}
    for text in json.loads(Path(path).read_text(encoding="utf-8")).get("tles") or []:
        lines = [x.rstrip() for x in str(text).splitlines() if x.strip()]
        if len(lines) >= 2 and lines[-2].startswith("1 ") and lines[-1].startswith("2 "):
            try:
                out.setdefault(int(lines[-2][2:7]), []).append((lines[-2], lines[-1]))
            except ValueError:
                continue
    return out


def archive_satrec(cache: Path | None, norad: int, when: float):
    """Bahnelemente älterer Läufe (ohne gespeicherte TLE) aus den lokalen Archivkopien der Engine – ohne Netz.
    Reihenfolge wie beim Abgleich: Tagesauswahl `orbits-<Datum>.json`, sonst ETH-Minutenabfrage `eth-<hash>.json`."""
    if not cache or not cache.is_dir():
        return None, None
    day = dt.datetime.fromtimestamp(when, dt.timezone.utc)
    path = cache / f"orbits-{day:%Y%m%d}.json"
    if path.is_file():
        row = _orbit_records(str(path), path.stat().st_mtime_ns).get(int(norad))
        if row:
            return satrec_from_record(row), "lokales Bahnarchiv (Space-Track-Spiegel)"
    minute = day.replace(second=0, microsecond=0)
    for age in (3, 2, 1):                                  # Schlüssel wie engine/archive_sources.eth_tles
        params = {"start-datetime": minute.strftime("%Y%m%dT%H%M"),
                  "end-datetime": (minute + dt.timedelta(minutes=1)).strftime("%Y%m%dT%H%M"),
                  "before": age, "after": age, "without-frequency-data": "True"}
        path = cache / ("eth-" + hashlib.sha256(json.dumps(params, sort_keys=True).encode()).hexdigest() + ".json")
        if path.is_file():
            tles = _eth_tles(str(path), path.stat().st_mtime_ns).get(int(norad))
            if tles:
                best = min((satrec_from_tle(*x) for x in tles), key=lambda s: abs(s.jdsatepoch + s.jdsatepochF - (when / 86400 + 2440587.5)))
                return best, "lokales Bahnarchiv (ETH Zürich)"
    return None, None


# ---------------------------------------------------------------- Blickfeld ---
def _basis(b):
    helper = np.array([0., 0., 1.]) if abs(b[2]) < .9 else np.array([1., 0., 0.])
    e1 = np.cross(helper, b)
    e1 /= np.linalg.norm(e1)
    return e1, np.cross(b, e1)


def inside_fov(directions, boresight, corners):
    """Gnomonische Projektion auf die Bildebene, Punkt-im-Viereck (Ecken in Umlaufreihenfolge, beide Drehsinne)."""
    e1, e2 = _basis(boresight)

    def project(u):
        z = u @ boresight
        safe = np.where(z > 0, z, np.nan)
        return np.stack([(u @ e1) / safe, (u @ e2) / safe], axis=-1)

    p, q = project(directions), project(corners)
    turn = np.sign(sum(q[i - 1, 0] * q[i, 1] - q[i, 0] * q[i - 1, 1] for i in range(len(q))))
    ok = (directions @ boresight) > 0
    for i in range(len(q)):
        a, b = q[i - 1], q[i]
        cross = (b[0] - a[0]) * (p[:, 1] - a[1]) - (b[1] - a[1]) * (p[:, 0] - a[0])
        ok &= np.nan_to_num(cross * turn, nan=-1) >= 0
    return ok


def _runs(mask):
    """Zusammenhängende True-Abschnitte als (Anfang, Ende) einschließlich."""
    idx = np.flatnonzero(mask)
    if not len(idx):
        return []
    cuts = np.flatnonzero(np.diff(idx) > 1)
    starts = np.r_[idx[0], idx[cuts + 1]]
    ends = np.r_[idx[cuts], idx[-1]]
    return list(zip(starts.tolist(), ends.tolist()))


def _r(a, n=1):
    return np.round(np.asarray(a, float), n).tolist()


# ---------------------------------------------------------------- Szene ---
def build(payload: dict) -> dict:
    """payload: site{lat,lon,alt_m}, t0, exposure{start,seconds}, fov{center:[ra,dec], corners:[[ra,dec]×4], source},
    trail:[[ra,dec]…], satellites:[{name,norad_id,tle?,…Abgleichswerte}], meteors:[…], aircraft:[…], cache (Ordner)."""
    site, t0 = payload["site"], float(payload["t0"])
    window = float(payload.get("window_s", WINDOW_S))
    observer = geodetic_to_ecef(site["lat"], site["lon"], site.get("alt_m", 0) or 0)
    fov = payload.get("fov") or {}
    corners = radec_to_d(*np.asarray(fov["corners"], float).T, t0) if fov.get("corners") else None
    boresight = radec_to_d(*fov["center"], t0)
    if corners is None:                                    # ohne Bildecken: Kreis aus der angegebenen Öffnung
        radius = math.radians(float(fov.get("radius_deg", .5)))
        e1, e2 = _basis(boresight)
        corners = np.array([boresight + math.tan(radius) * (math.cos(a) * e1 + math.sin(a) * e2) for a in np.radians([45, 135, 225, 315])])
        corners /= np.linalg.norm(corners, axis=1)[:, None]
    cache = Path(payload["cache"]) if payload.get("cache") else None
    g0 = gmst(t0)
    out = dict(ok=True, t0=t0, exposure=payload.get("exposure"), earth_rate=EARTH_RATE, window=[t0 - window, t0 + window],
               step=STEP_S, observer=_r(observer, 3), observer_height_km=round(float(site.get("alt_m", 0) or 0) / 1000, 3),
               sun=_r(sun_d(t0), 5),
               fov=dict(boresight=_r(boresight, 7), corners=_r(corners, 7), source=fov.get("source", "wcs"),
                        size_deg=fov.get("size_deg"), ra=fov["center"][0], dec=fov["center"][1]),
               trail=_r(radec_to_d(*np.asarray(payload["trail"], float).T, t0), 7) if payload.get("trail") else None,
               satellites=[], missing=[], meteors=[], aircraft=[])
    dense = t0 + np.arange(-window, window + STEP_S / 2, STEP_S)
    fine = t0 + np.arange(-120, 120.001, .05)
    for i, s in enumerate(payload.get("satellites") or []):
        sat, origin = None, None
        try:
            if s.get("tle"):
                sat, origin = satrec_from_tle(*s["tle"]), "im Lauf gespeichert"
            else:
                # gleicher Zeitpunkt wie beim Abgleich (Belichtungsmitte) → gleicher Archiv-/Cache-Schlüssel wie in historical_models
                sat, origin = archive_satrec(cache, s["norad_id"], float(payload.get("archive_time") or t0))
        except (ValueError, KeyError, TypeError, OSError) as e:
            out["missing"].append(dict(name=s.get("name"), norad_id=s.get("norad_id"), reason=f"Bahnelemente unlesbar ({type(e).__name__})."))
            continue
        if sat is None:
            out["missing"].append(dict(name=s.get("name"), norad_id=s.get("norad_id"),
                                       reason="Bahnelemente wurden in diesem älteren Lauf nicht gespeichert und liegen nicht mehr im lokalen "
                                              "Archiv. „Zeitreihen aktualisieren“ rechnet den Abgleich neu und speichert sie mit."))
            continue
        period = 2 * math.pi / sat.no_kozai * 60          # Sekunden
        ring_t = t0 + np.linspace(-period / 2, period / 2, RING_POINTS + 1)
        all_t = np.concatenate([ring_t, dense, fine])
        jd1, jd2 = _jd(all_t)
        err, r, v = sat.sgp4_array(jd1, jd2)
        if np.any(err[RING_POINTS + 1:RING_POINTS + 1 + len(dense)]):
            out["missing"].append(dict(name=s.get("name"), norad_id=s.get("norad_id"), reason="SGP4 liefert für diese Zeit keine gültige Bahn."))
            continue
        pos = rot_z(-g0, r)                                 # TEME → D
        ring, track, near = pos[:RING_POINTS + 1], pos[RING_POINTS + 1:RING_POINTS + 1 + len(dense)], pos[RING_POINTS + 1 + len(dense):]
        obs_d = rot_z(gmst(dense) - g0, observer)
        ecef = rot_z(-(gmst(dense) - g0), track)           # D → erdfest zur jeweiligen Zeit (für die Höhe)
        rng = np.linalg.norm(track - obs_d, axis=1)
        speed = np.linalg.norm(v[RING_POINTS + 1:RING_POINTS + 1 + len(dense)], axis=1)
        obs_f = rot_z(gmst(fine) - g0, observer)
        look = near - obs_f
        look /= np.linalg.norm(look, axis=1)[:, None]
        up = obs_f / np.linalg.norm(obs_f, axis=1)[:, None]
        visible = (look * up).sum(1) > 0
        inside = inside_fov(look, boresight, corners) & visible & (err[RING_POINTS + 1 + len(dense):] == 0)
        segments = [dict(t=_r(fine[a:b + 1] - t0, 2), pos=_r(near[a:b + 1], 2)) for a, b in _runs(inside)]
        k = int(np.argmin(abs(dense - t0)))
        out["satellites"].append(dict(
            name=s.get("name") or f"NORAD {s.get('norad_id')}", norad_id=s.get("norad_id"), object_type=s.get("object_type"),
            tle_age_days=s.get("tle_age_days"), tle_source=origin, color=COLORS[i % len(COLORS)],
            separation_arcsec=s.get("separation_arcsec"), time_offset_s=s.get("time_offset_s"),
            period_min=round(period / 60, 2), inclination_deg=round(math.degrees(sat.inclo), 2),
            ring=_r(ring), track=_r(track), height_km=_r(ecef_height_km(ecef)), speed_kms=_r(speed, 3), range_km=_r(rng),
            fov_segments=segments, in_fov=bool(segments),
            at_t0=dict(height_km=round(float(ecef_height_km(ecef[k])), 1), speed_kms=round(float(speed[k]), 3),
                       range_km=round(float(rng[k]), 1))))
    for m in payload.get("meteors") or []:
        tr = m["trajectory"]
        pts = geodetic_to_ecef([tr["begin"][0], tr["end"][0]], [tr["begin"][1], tr["end"][1]],
                               [tr["begin"][2] * 1000, tr["end"][2] * 1000])
        out["meteors"].append(dict(name=m.get("name"), shower=m.get("shower"), ecef=_r(pts, 2),
                                   times=[float(tr["start"]), float(tr["start"]) + float(tr.get("duration") or 0)],
                                   height_km=[tr["begin"][2], tr["end"][2]]))
    for a in payload.get("aircraft") or []:
        rows = np.asarray(a["track"], float)
        out["aircraft"].append(dict(name=a.get("name"), ecef=_r(geodetic_to_ecef(rows[:, 1], rows[:, 2], rows[:, 3]), 3),
                                    times=rows[:, 0].tolist(), height_km=_r(rows[:, 3] / 1000, 2)))
    return out


# ---------------------------------------------------------------- Laufdaten ---
def _exposure_start(c: dict, sat: dict | None) -> float:
    if sat and isinstance(sat.get("exposure_start"), (int, float)):
        return float(sat["exposure_start"])
    # wie engine/motion_plausibility.exposure_start (Stellina: Zeitstempel = Belichtungsende)
    ref = c.get("time_reference") or ("end" if (c.get("plausibility") or {}).get("exposure_source") == "Stellina EXPOSURE [ms]" else "start")
    return float(c["timestamp"]) - (float(c["exposure_seconds"]) if ref == "end" else 0.)


def _fov(folder: Path, c: dict):
    """Bildecken der Trefferaufnahme am Himmel: Analysepixel → Referenzbild (Feldrotation) → WCS des Plate-Solvings."""
    info_path, wcs_path = folder / "platesolve" / "ergebnis.json", folder / "platesolve" / "loesung.wcs"
    ctx = c.get("context_image") or {}
    if info_path.is_file() and wcs_path.is_file() and ctx.get("width") and ctx.get("height"):
        from astropy.io import fits
        from astropy.wcs import WCS
        info = json.loads(info_path.read_text(encoding="utf-8"))
        header = fits.Header.fromfile(wcs_path)
        header["NAXIS"], header["NAXIS1"], header["NAXIS2"] = 2, info["width"], info["height"]
        wcs = WCS(header, naxis=2)
        w, h = float(ctx["width"]), float(ctx["height"])
        pix = np.array([[0, 0], [w, 0], [w, h], [0, h], [w / 2, h / 2], [w / 2, 0], [w / 2, h], [0, h / 2], [w, h / 2]])
        m = ctx.get("reference_to_current")
        if m is not None:                                      # Trefferbild → Referenzbild
            m = np.asarray(m, float)
            pix = (pix - m[:, 2]) @ np.linalg.inv(m[:, :2]).T
        world = wcs.all_pix2world(pix * [info["width"] / w, info["height"] / h], 0)
        if np.isfinite(world).all():
            u = unit_radec(world[:, 0], world[:, 1])
            ang = lambda a, b: round(math.degrees(math.acos(min(1., float(u[a] @ u[b])))), 3)
            return dict(center=world[4].tolist(), corners=world[:4].tolist(), source="wcs", size_deg=[ang(7, 8), ang(5, 6)])
    path = np.asarray(c.get("world_path") or [], float)
    if not len(path):
        return None
    center = unit_radec(path[:, 0], path[:, 1]).mean(0)
    ra, dec = math.degrees(math.atan2(center[1], center[0])) % 360, math.degrees(math.asin(center[2] / np.linalg.norm(center)))
    return dict(center=[ra, dec], radius_deg=.5, source="trail", size_deg=None)


def payload_for(folder: Path, c: dict, cache: Path | None) -> dict:
    """Eingaben für `build` aus einem gespeicherten Kandidaten; `reason` statt Fehler, wenn etwas fehlt."""
    cats = {r.get("provider"): r for r in c.get("catalogs") or []}
    sat = cats.get("satellites") or {}
    sats = [m for m in sat.get("matches") or [] if m.get("norad_id") is not None][:5] if sat.get("status") == "compatible" else []
    meteors = [m for m in (cats.get("meteors") or {}).get("matches") or [] if m.get("trajectory")]
    aircraft = [m for m in (cats.get("aircraft") or {}).get("matches") or [] if len(m.get("track") or []) >= 2]
    site = c.get("site")
    if not (sats or meteors or aircraft):
        return dict(reason="Für diesen Kandidaten gibt es kein zugeordnetes Objekt mit Bahn – die 3D-Ansicht zeigt Satelliten, "
                           "Meteorbahnen und Flugzeugspuren aus dem Katalogabgleich.")
    if not site or site.get("lat") is None:
        return dict(reason="Der Aufnahmestandort fehlt – ohne ihn lassen sich Blickfeld und Bahn nicht im Raum darstellen.")
    if c.get("timestamp") is None or c.get("exposure_seconds") is None:
        return dict(reason="Aufnahmezeit oder Belichtung fehlen.")
    fov = _fov(folder, c)
    if not fov:
        return dict(reason="Ohne Plate-Solving ist die Blickrichtung des Teleskops unbekannt.")
    start, seconds = _exposure_start(c, sat), float(c["exposure_seconds"])
    passage = (sats[0].get("passage_seconds") if sats else None) or [seconds / 2]
    t0 = start + float(np.mean(passage))
    if not sats and meteors:
        t0 = float(meteors[0]["trajectory"]["start"])
    keys = ("name", "norad_id", "object_type", "tle_age_days", "tle", "separation_arcsec", "time_offset_s")
    return dict(site={k: site.get(k) for k in ("lat", "lon", "alt_m")}, t0=t0, exposure=dict(start=start, seconds=seconds), fov=fov,
                archive_time=start + seconds / 2, trail=c.get("world_path"), satellites=[{k: m.get(k) for k in keys} for m in sats],
                meteors=[{k: m.get(k) for k in ("name", "shower", "trajectory")} for m in meteors],
                aircraft=[{k: m.get(k) for k in ("name", "icao24", "track")} for m in aircraft],
                cache=str(cache) if cache else None)


def available(c: dict) -> bool:
    """Gibt es etwas zu zeigen? (Satellitentreffer oder Meteor-/Flugzeugtreffer mit gespeicherter Bahn)"""
    cats = {r.get("provider"): r for r in c.get("catalogs") or []}
    sat = cats.get("satellites") or {}
    return bool((sat.get("status") == "compatible" and any(m.get("norad_id") is not None for m in sat.get("matches") or []))
                or any(m.get("trajectory") for m in (cats.get("meteors") or {}).get("matches") or [])
                or any(len(m.get("track") or []) >= 2 for m in (cats.get("aircraft") or {}).get("matches") or []))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(build(json.loads(sys.stdin.read()))))
