"""Standort und Uhrversatz einer Nacht aus sicher zugeordneten Satellitenspuren zurückrechnen (nur Vorschlag).

Idee: Erdnahe Satelliten zeigen eine Parallaxe von ≈ 7′ je km Standortfehler (500 km Entfernung). Liegen mehrere
zugeordnete Bahnen einer Nacht konstant quer neben ihren Spuren, passt ein anderer Standort besser. Unbekannte:
Verschiebung Nord/Ost (km) und Uhrversatz (s, wahre Zeit = Kamerazeit + dt). Messgrößen je Spur:
  quer  – Abstand von 5 Punkten der gemessenen Spur zur vorhergesagten Bahn (σ = SIGMA_CROSS)
  längs – nur bei Spuren ganz im Bild (Anfang/Ende nicht am Bildrand): Zeitpunkt, zu dem die Bahn Anfang und Ende
          passiert, gegen Belichtungsbeginn/-ende (σ = SIGMA_TIME). Spuren über den Bildrand liefern keine Zeit.
Schwache Vorinformation (±50 km, ±30 s) hält die Rechnung bei wenigen Spuren stabil. Gauß-Newton mit numerischer
Jacobi-Matrix, Ausreißer (> 4σ) werden einmal entfernt. Fehlerellipse aus der Kovarianz (× reduziertes χ², falls > 1).

Rechnung wie orbit3d.py (numpy + erfa + sgp4), UT1 ≈ UTC; Aberration wie beim Abgleich (astrometrische Richtungen).
Schreibt nichts – die Oberfläche bietet „übernehmen“ an (neuer Ort + Zuordnung der Nacht in der Ortsliste).
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np

from .orbit3d import _basis, _exposure_start, _fov, archive_satrec, geodetic_to_ecef, gmst, icrs_to_teme, rot_z, satrec_from_tle, unit_radec

SIGMA_CROSS = 30.0          # ″ – Lage der Spur aus WCS + Linienanpassung
SIGMA_TIME = 1.0            # s – Belichtungszeitpunkte (Stellina: Ende der Belichtung, ganzzahlige Sekunden)
PRIOR_KM, PRIOR_S = 50.0, 30.0
WINDOW_S = 30.0             # Bahn so weit vor/nach der Belichtung rechnen (Uhrversatz bis ±20 s)
STEP_S = 0.1
EDGE_FRACTION = 0.03        # Spurende näher als 3 % der Bildgröße am Rand → Spur läuft aus dem Bild
MIN_TRAILS = 3
ARCSEC = 206264.806


# ---------------------------------------------------------------- Geometrie ---
def topo_directions(sat, t, lat, lon, alt_m, matrix):
    """Topozentrische ICRS-Einheitsvektoren (N, 3) eines Satelliten zu Zeiten t (wahre UTC, Unix-Sekunden)."""
    t = np.asarray(t, float)
    day = np.floor(t / 86400)
    err, r, _ = sat.sgp4_array(2440587.5 + day, (t - day * 86400) / 86400)
    obs = rot_z(gmst(t), geodetic_to_ecef(lat, lon, alt_m))
    topo = (np.asarray(r) - obs) @ matrix                        # TEME → ICRS (Zeilenvektoren: teme = M · icrs)
    topo[np.asarray(err) != 0] = np.nan
    return topo / np.linalg.norm(topo, axis=1, keepdims=True)


def _plane(u, center):
    e1, e2 = _basis(center)
    z = u @ center
    return np.stack([(u @ e1) / z, (u @ e2) / z], axis=-1) * ARCSEC


def _resample(points, n=5):
    seg = np.linalg.norm(np.diff(points, axis=0), axis=1)
    s = np.r_[0, np.cumsum(seg)]
    if s[-1] <= 0:
        return points[:1].repeat(n, 0)
    q = np.linspace(0, s[-1], n)
    out = np.stack([np.interp(q, s, points[:, i]) for i in range(points.shape[1])], axis=-1)
    return out / np.linalg.norm(out, axis=1, keepdims=True) if points.shape[1] == 3 else out


def _nearest(points, line):
    """Je Punkt: vorzeichenbehafteter Abstand zur Polylinie und Index+Anteil des nächsten Abschnitts."""
    a, b = line[:-1], line[1:]
    v = b - a
    vv = np.maximum((v * v).sum(1), 1e-12)
    out_d, out_k, out_f = [], [], []
    for p in points:
        f = np.clip(((p - a) * v).sum(1) / vv, 0, 1)
        q = a + f[:, None] * v
        d = np.linalg.norm(p - q, axis=1)
        d = np.where(np.isfinite(d), d, np.inf)
        k = int(np.argmin(d))
        side = np.sign(v[k, 0] * (p[1] - a[k, 1]) - v[k, 1] * (p[0] - a[k, 0])) or 1.0
        out_d.append(side * d[k]); out_k.append(k); out_f.append(f[k])
    return np.array(out_d), np.array(out_k), np.array(out_f)


# ---------------------------------------------------------------- Ausgleich ---
def residuals(obs: list[dict], site: dict, x) -> tuple[np.ndarray, list[np.ndarray]]:
    """x = [Nord km, Ost km, dt s] → gewichtete Residuen (ohne Vorinformation) und Residuen je Spur."""
    lat = site["lat"] + x[0] / 111.195
    lon = site["lon"] + x[1] / (111.195 * math.cos(math.radians(site["lat"])))
    per = []
    for o in obs:
        t_cam = o["_times"]
        u = topo_directions(o["sat"], t_cam + x[2], lat, lon, site.get("alt_m") or 0, o["_matrix"])
        ok = np.isfinite(u).all(1) & (u @ o["_center"] > 0)
        if ok.sum() < 3:
            per.append(np.full(len(o["_trail"]) + (2 if o["inside"] else 0), 50.0))
            continue
        line, times = _plane(u[ok], o["_center"]), t_cam[ok]
        d, k, f = _nearest(o["_trail"], line)
        r = list(d / SIGMA_CROSS)
        if o["inside"]:
            tau = times[k] + f * (times[np.minimum(k + 1, len(times) - 1)] - times[k])
            ends = sorted([tau[0], tau[-1]])
            r += [(ends[0] - o["start"]) / SIGMA_TIME, (ends[1] - (o["start"] + o["duration"])) / SIGMA_TIME]
        per.append(np.array(r))
    return np.concatenate(per) if per else np.zeros(0), per


def _prepare(o: dict) -> dict:
    trail = np.asarray(o["trail"], float)
    tu = unit_radec(trail[:, 0], trail[:, 1])
    center = tu.mean(0)
    center /= np.linalg.norm(center)
    n = int((o["duration"] + 2 * WINDOW_S) / STEP_S) + 1
    return {**o, "_center": center, "_trail": _plane(_resample(tu), center) if len(tu) > 1 else _plane(tu, center),
            "_times": np.linspace(o["start"] - WINDOW_S, o["start"] + o["duration"] + WINDOW_S, n),
            "_matrix": icrs_to_teme(o["start"] + o["duration"] / 2)}


def _solve(obs, site, iters=10):
    x = np.zeros(3)
    prior = np.array([PRIOR_KM, PRIOR_KM, PRIOR_S])
    h = np.array([0.05, 0.05, 0.05])
    for _ in range(iters):
        r0, _ = residuals(obs, site, x)
        r0 = np.r_[r0, x / prior]
        J = np.empty((len(r0), 3))
        for j in range(3):
            dx = x.copy(); dx[j] += h[j]
            r1, _ = residuals(obs, site, dx)
            J[:, j] = (np.r_[r1, dx / prior] - r0) / h[j]
        step = np.linalg.lstsq(J, -r0, rcond=None)[0]
        x = x + step
        if np.all(np.abs(step) < [0.005, 0.005, 0.005]):
            break
    r, per = residuals(obs, site, x)
    full = np.r_[r, x / prior]
    cov = np.linalg.pinv(J.T @ J)
    dof = max(1, len(r) - 3)
    chi2 = float(r @ r) / dof
    return x, cov * max(1.0, chi2), per, chi2


def fit(observations: list[dict], site: dict) -> dict:
    """observations: {sat (Satrec), trail [[ra, dec] …], start, duration, inside, label}. site: {lat, lon, alt_m}."""
    obs = [_prepare(o) for o in observations if o.get("trail") and len(o["trail"]) >= 2]
    if len(obs) < MIN_TRAILS:
        return {"ok": False, "reason": f"Zu wenige sicher zugeordnete Satellitenspuren ({len(obs)}, nötig {MIN_TRAILS}).", "trails": len(obs)}
    _, per0 = residuals(obs, site, np.zeros(3))
    x, cov, per, chi2 = _solve(obs, site)
    worst = [float(np.sqrt(np.mean(p ** 2))) for p in per]
    dropped = [o["label"] for o, w in zip(obs, worst) if w > 4]
    if dropped and len(obs) - len(dropped) >= MIN_TRAILS:
        keep = [i for i, w in enumerate(worst) if w <= 4]
        obs, per0 = [obs[i] for i in keep], [per0[i] for i in keep]
        x, cov, per, chi2 = _solve(obs, site)
    else:
        dropped = []
    cross = lambda ps, os_: np.concatenate([p[:len(o["_trail"])] for p, o in zip(ps, os_)]) * SIGMA_CROSS
    before, after = cross(per0, obs), cross(per, obs)
    ne = cov[:2, :2]
    w, v = np.linalg.eigh(ne)
    w = np.sqrt(np.maximum(w, 0))
    shift = float(math.hypot(x[0], x[1]))
    lat = site["lat"] + x[0] / 111.195
    lon = site["lon"] + x[1] / (111.195 * math.cos(math.radians(site["lat"])))
    sig_shift = float(math.sqrt(max(np.array([x[0], x[1]]) @ ne @ np.array([x[0], x[1]]) / max(shift ** 2, 1e-12), 0))) if shift > 0 else float(w[-1])
    rms0, rms1 = float(np.sqrt(np.mean(before ** 2))), float(np.sqrt(np.mean(after ** 2)))
    worth = bool(shift > 0.3 and shift > 3 * sig_shift and rms1 < 0.7 * rms0)
    bearing = (math.degrees(math.atan2(x[1], x[0])) + 360) % 360
    return {"ok": True, "trails": len(obs), "timed": sum(1 for o in obs if o["inside"]), "dropped": dropped,
            "shift_km": round(shift, 3), "shift_sigma_km": round(sig_shift, 3), "bearing_deg": round(bearing, 1),
            "north_km": round(float(x[0]), 3), "east_km": round(float(x[1]), 3),
            "ellipse_km": {"major": round(float(w[-1]), 3), "minor": round(float(w[0]), 3),
                           "angle_deg": round((math.degrees(math.atan2(v[1, -1], v[0, -1])) + 360) % 180, 1)},
            "clock_s": round(float(x[2]), 2), "clock_sigma_s": round(float(math.sqrt(max(cov[2, 2], 0))), 2),
            "rms_before_arcsec": round(rms0, 1), "rms_after_arcsec": round(rms1, 1), "chi2": round(chi2, 2),
            "lat": round(lat, 6), "lon": round(lon, 6), "alt_m": site.get("alt_m") or 0,
            "accuracy_m": round(max(50.0, 1000 * float(w[-1])), 0), "worth": worth}


# ---------------------------------------------------------------- Laufdaten ---
def _inside(folder: Path, c: dict) -> bool:
    """Liegen Anfang und Ende der Spur im Bild (nicht am Rand)? Nur dann tragen sie Zeitinformation."""
    path = c.get("path") or ((c.get("detection") or {}).get("segment"))
    ctx = c.get("context_image") or {}
    w, h = ctx.get("width"), ctx.get("height")
    if path and w and h:
        p = np.asarray(path, float)[[0, -1]]
        m = EDGE_FRACTION * max(w, h)
        return bool(np.all((p[:, 0] > m) & (p[:, 0] < w - m) & (p[:, 1] > m) & (p[:, 1] < h - m)))
    fov = _fov(folder, c) if folder else None
    if fov and fov.get("corners"):
        from .orbit3d import inside_fov
        trail = np.asarray(c["world_path"], float)[[0, -1]]
        corners = unit_radec(*np.asarray(fov["corners"], float).T)
        center = unit_radec(*fov["center"])
        # Ecken um 3 % zur Mitte ziehen: Spurende darf nicht im Randstreifen liegen
        shrunk = corners + (center - corners) * EDGE_FRACTION * 2
        return bool(inside_fov(unit_radec(trail[:, 0], trail[:, 1]), center, shrunk / np.linalg.norm(shrunk, axis=1, keepdims=True)).all())
    return False


def _sure(sat: dict) -> dict | None:
    """Eindeutige Zuordnung: bester Treffer ohne nahezu gleich guten Zweiten (Starlink-Züge sonst mehrdeutig)."""
    ms = [m for m in sat.get("matches") or [] if m.get("norad_id") is not None]
    if sat.get("status") != "compatible" or not ms:
        return None
    rank = lambda m: (m.get("mean_offset_arcsec") or m.get("separation_arcsec") or 0) + 20 * (m.get("time_offset_s") or 0)
    if len(ms) > 1 and rank(ms[1]) < 1.5 * rank(ms[0]) + 30:
        return None
    return ms[0]


def collect(folder: Path, cache: Path | None) -> tuple[list[dict], dict | None, list[str]]:
    """Beobachtungen einer Session (Ordner mit kandidaten.json) → Eingaben für `fit`, Standort des Laufs, Hinweise."""
    try:
        cands = json.loads((folder / "kandidaten.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return [], None, ["Keine Kandidaten in diesem Lauf."]
    out, site, notes = [], None, []
    for c in cands:
        site = site or (c.get("site") if (c.get("site") or {}).get("lat") is not None else None)
        sat = {r.get("provider"): r for r in c.get("catalogs") or []}.get("satellites") or {}
        m = _sure(sat)
        if not m or not c.get("world_path") or c.get("timestamp") is None or c.get("exposure_seconds") is None:
            continue
        start, dur = _exposure_start(c, sat), float(c["exposure_seconds"])
        rec = None
        if m.get("tle"):
            try:
                rec = satrec_from_tle(*m["tle"][:2])
            except Exception:
                rec = None
        if rec is None:
            rec, _ = archive_satrec(cache, int(m["norad_id"]), start + dur / 2)
        if rec is None:
            notes.append(f"{c.get('id')}: Bahnelemente für NORAD {m['norad_id']} nicht mehr vorhanden")
            continue
        out.append({"sat": rec, "trail": c["world_path"], "start": start, "duration": dur, "inside": _inside(folder, c),
                    "label": f"{c.get('id')} {m.get('name') or 'NORAD ' + str(m['norad_id'])}"})
    return out, site, notes


def fit_session(folder: Path, cache: Path | None) -> dict:
    obs, site, notes = collect(folder, cache)
    if not site:
        return {"ok": False, "reason": "Für diesen Lauf ist kein Standort gespeichert.", "notes": notes}
    res = fit(obs, {"lat": float(site["lat"]), "lon": float(site["lon"]), "alt_m": float(site.get("alt_m") or 0)})
    return {**res, "notes": notes, "site": {k: site.get(k) for k in ("name", "site_id", "source", "origin", "accuracy_m")},
            "night": site.get("night")}
