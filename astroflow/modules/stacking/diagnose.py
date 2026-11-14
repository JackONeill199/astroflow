"""Gerätediagnose aus vorhandenen Daten (Wunsch 17, seit 0.43.0) – ohne Rohbilder.

Drei Teile, alle aus gespeicherten Messwerten:
- **Nachführung:** Lage und Drehung jeder Aufnahme relativ zum Referenzbild aus den gespeicherten Sternlisten (dieselbe
  Dreieckszuordnung wie beim Stacken, `skyquality.frame_links`). Smart-Teleskope zentrieren zwischendurch neu (Stellina alle
  1–3 min, Sprünge von 30–100 px) – die Drift wird daher je **Abschnitt** zwischen zwei Sprüngen gemessen (Gerade durch die
  Lagen): Geschwindigkeit, Verschmieren je Belichtung (gegen die Sterngröße), Unruhe um die Gerade, bei langen Abschnitten ein
  periodischer Anteil (Lomb-Scargle). Dazu Ausreißergruppen (Stöße, Böen): mehrere Aufnahmen hintereinander unscharf/verzogen,
  ohne dass Sterne verschwinden (das wären Wolken).
- **Fokus:** `metrics.fwhm` über die Nacht. Der Seeing-Anteil wächst mit der Luftmasse (FWHM ∝ X^0,6) und wird herausgerechnet,
  wenn ein Standort bekannt ist; danach robuste Gerade (Theil-Sen) gegen die Zeit und – falls im FITS-Kopf – gegen die
  Sensortemperatur. Stellina legt nach jedem automatischen Nachfokussieren einen neuen Ordner an (`02-images-adjust-focus`,
  `03-…`): Schärfe vor/nach diesen Marken zeigt, ob der Fokus gewandert war.
- **Bildfeld:** Sterngröße über das Feld im Master-Stack (bleibt nach dem Löschen der Rohbilder): Ebene = Verkippung des Sensors,
  r²-Anteil = Bildfeldwölbung; Unsicherheit per Bootstrap. Neuere Aufnahmen tragen zusätzlich eine 3×3-Karte je Bild
  (`metrics.tiles`, `quality.shape_metrics`) – deren Mittel bestätigt die Stack-Messung unabhängig von der Registrierung.

- **Himmelslage (seit DIAG_VERSION 2):** Höhe, Azimut und Bildfelddrehung je Aufnahme lokal aus Zeit, Ort der Nacht und
  Objektposition (`sky_track`, nichts in die DB). Daraus **Montierung je Nacht** (`mount`): dreht sich das Bild (Registrierung)
  mit dem parallaktischen Winkel, ist es Alt-Az ohne Ausgleich; steht es still, obwohl Alt-Az es deutlich gedreht hätte, ist es
  EQ-Modus – oder ein Derotator (Stellina), deshalb nur zusammen mit der Geräte-Tabelle (`devices.json`). Und **verworfene
  Aufnahmen nach Höhe** (`sky`): Anteil der wegen Sternform verworfenen Aufnahmen in der Zone starker Drehung/über der
  Zenit-Grenze gegen den Rest, gegengeprüft mit Driftrichtung/Länglichkeit (`elong_vs_drift`, `smear_ratio`).
Ergebnis in `results.stats.diagnose` (`v` = DIAG_VERSION), Gerätetrend über alle Nächte in `device_summary`.
Befunde für den Tutor: `findings`.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import re
import time
import traceback
from pathlib import Path

import numpy as np

DIAG_VERSION = 3          # 3 (14.11.2026): erste Vespera nicht mehr als Vespera II (Seriennummer mit „2“); 2 (12.11.2026): Himmelslage je Aufnahme, Montierung je Nacht, verworfene Aufnahmen nach Höhe
Q_MIN = 3.0               # Grad: so weit muss sich der parallaktische Winkel drehen, damit die Montierung erkennbar ist
ROT_ALTAZ = 0.6           # |Bilddrehung / parallaktischer Winkel| ab hier: Bildfeld dreht wie bei Alt-Az ohne Ausgleich
ROT_STEADY = 0.2          # darunter: Bildfeld steht (EQ oder Derotator)
SHAPE_REASONS = ("verzogen", "unscharf")      # Ablehnungsgründe, die Nachführung/Drehung betreffen können (grade.py)
SEEING_EXP = 0.6          # FWHM des Seeings ∝ Luftmasse^0,6
MIN_SEG = 3               # Aufnahmen je Abschnitt für eine Driftmessung
JUMP_PX = 12.0            # Abweichung von der Fortsetzung, ab der ein neuer Abschnitt beginnt (Nachzentrieren, Dithern)
GAP_S = 150.0             # längere Pause = neuer Abschnitt
TILT_MIN = 10.0           # % Unterschied Ecke zu Ecke, ab dem eine Verkippung gemeldet wird
CURV_MIN = 12.0           # % Rand gegen Mitte (Bildfeldwölbung)
FOCUS_MIN = 4.0           # %/h – darunter gilt der Fokus als stabil
SMEAR_WARN = 0.5          # Verschmieren je Belichtung ab diesem Anteil der Sterngröße
STACK_GRID = (4, 6)       # Kacheln der Schärfekarte (Zeilen, Spalten)
FOLDER_RE = re.compile(r"(?:^|[\\/])(\d{2})-images[^\\/]*[\\/]")


# ------------------------------------------------------------------ Helfer ---
def _r(v, nd=2):
    return None if v is None or not np.isfinite(v) else round(float(v), nd)


def _mad(a) -> float:
    a = np.asarray(a, float)
    return float(1.4826 * np.median(np.abs(a - np.median(a)))) if len(a) else 0.0


def _time(s) -> dt.datetime | None:
    try:
        t = dt.datetime.fromisoformat(str(s).replace("Z", ""))
    except (TypeError, ValueError):
        return None
    return t.replace(tzinfo=dt.timezone.utc) if t.tzinfo is None else t.astimezone(dt.timezone.utc)


def folder_mark(path: str | None) -> str | None:
    """Stellina-Aufnahmeordner („01“ Beginn, „02“, „03“ … nach automatischem Nachfokussieren)."""
    m = FOLDER_RE.search(str(path or ""))
    return m.group(1) if m else None


def theil_sen(x, y) -> dict | None:
    """Robuste Steigung mit 95-%-Bereich."""
    from scipy.stats import theilslopes
    x, y = np.asarray(x, float), np.asarray(y, float)
    ok = np.isfinite(x) & np.isfinite(y)
    if ok.sum() < 8 or np.ptp(x[ok]) <= 0:
        return None
    s, a, lo, hi = theilslopes(y[ok], x[ok], 0.95)
    return {"slope": float(s), "icpt": float(a), "lo": float(lo), "hi": float(hi)}


def _axial_diff(a: float, b: float) -> float:
    d = abs((a - b) % 180)
    return min(d, 180 - d)


# ------------------------------------------------------------------ Aufnahmen ---
def load_frames(db, pid: int, nights: set | None = None) -> list[dict]:
    out = []
    for x in db.q("SELECT id, night, path, status, reason, info, metrics FROM frames WHERE project_id=? AND kind='light' "
                  "AND metrics IS NOT NULL", (pid,)):
        if nights and x["night"] not in nights:
            continue
        i, m = json.loads(x["info"] or "{}"), json.loads(x["metrics"] or "{}")
        t = _time(i.get("date_obs"))
        if not m or m.get("n_stars") is None or t is None:
            continue
        out.append({"id": x["id"], "night": x["night"], "t": t, "status": x["status"], "reason": x["reason"],
                    "exptime": i.get("exptime"), "temp": i.get("temp"), "instrument": i.get("instrument"), "shape": i.get("shape"),
                    "site": i.get("site"), "path": x["path"] or i.get("path"), "scale": (i.get("solve") or {}).get("scale"),
                    "folder": folder_mark(x["path"] or i.get("path")), "metrics": m})
    out.sort(key=lambda f: f["t"])
    return out


def header_temps(frames: list[dict], limit: int = 400) -> int:
    """Sensortemperatur älterer Aufnahmen aus dem FITS-Kopf nachlesen (nur Kopf, nur erreichbare Dateien; Stellina: TEMP).
    Ändert nur die Liste im Speicher. Rückgabe: Zahl der gelesenen Köpfe."""
    from astropy.io import fits
    todo = [f for f in frames if f["temp"] is None and f.get("path")]
    todo = todo[:: max(1, len(todo) // limit)][:limit]
    n = 0
    for f in todo:
        p = Path(f["path"])
        if not p.exists():
            continue
        try:
            h = fits.getheader(p)
            v = h.get("CCD-TEMP", h.get("TEMPERAT", h.get("TEMP")))
            f["temp"] = float(v) if v is not None and np.isfinite(float(v)) else None
            n += 1
        except Exception:
            continue
    return n


# ------------------------------------------------------------------ Nachführung ---
def segments(ts: np.ndarray, pos: np.ndarray) -> list[np.ndarray]:
    """Zeitlich sortierte Lagen in Abschnitte teilen: neu bei Pause > GAP_S oder Sprung weg von der Fortsetzung."""
    if not len(ts):
        return []
    segs, cur = [], [0]
    steps = np.linalg.norm(np.diff(pos, axis=0), axis=1)
    med = float(np.median(steps)) if len(steps) else 0.0
    for k in range(1, len(ts)):
        dt_ = ts[k] - ts[k - 1]
        if len(cur) >= 2:
            idx = np.array(cur[-5:])
            tt = ts[idx] - ts[idx].mean()
            v = (tt[:, None] * (pos[idx] - pos[idx].mean(0))).sum(0) / max((tt**2).sum(), 1e-9)
            pred = pos[cur[-1]] + v * dt_
            lim = JUMP_PX + 0.3 * float(np.linalg.norm(v)) * dt_
        else:
            pred, lim = pos[cur[-1]], max(2 * JUMP_PX, 4 * med)
        if dt_ > GAP_S or np.linalg.norm(pos[k] - pred) > lim:
            segs.append(np.array(cur))
            cur = [k]
        else:
            cur.append(k)
    segs.append(np.array(cur))
    return segs


def _periodic(t: np.ndarray, res: np.ndarray) -> dict | None:
    """Periodischer Anteil in den Restabweichungen langer Abschnitte (Lomb-Scargle, 1–20 min)."""
    from astropy.timeseries import LombScargle
    if len(t) < 24 or np.ptp(t) < 600:
        return None
    best = None
    for ax in range(2):
        y = res[:, ax]
        fmin, fmax = 1 / min(1200.0, np.ptp(t) / 2), 1 / 60.0
        if fmin >= fmax:
            return None
        ls = LombScargle(t, y)
        f, p = ls.autopower(minimum_frequency=fmin, maximum_frequency=fmax, samples_per_peak=8)
        i = int(np.argmax(p))
        fap = float(ls.false_alarm_probability(p[i], minimum_frequency=fmin, maximum_frequency=fmax))
        amp = float(np.sqrt(2) * np.std(ls.model(t, f[i])))
        if best is None or fap < best["fap"]:
            best = {"period_s": round(float(1 / f[i]), 1), "fap": fap, "amp_px": round(amp, 2), "axis": "xy"[ax]}
    return best if best and best["fap"] < 0.01 else None


# ------------------------------------------------------------------ Himmelslage ---
def sky_track(times: list[dt.datetime], ra: float, dec: float, site: list) -> dict:
    """Höhe, Azimut (ab Nord), parallaktischer Winkel (Grad) und Bildfelddrehung einer Alt-Az-Montierung (°/min) je Zeitpunkt.
    Offline (Ortssternzeit über astropy ohne IERS-Download), Standort bleibt auf dem Rechner."""
    import warnings
    from astropy import units as u
    from astropy.time import Time
    from astropy.utils import iers
    from . import devices
    with warnings.catch_warnings(), iers.conf.set_temp("auto_download", False), iers.conf.set_temp("auto_max_age", None):
        warnings.simplefilter("ignore")
        lst = Time(times).sidereal_time("mean", longitude=site[1] * u.deg).rad
    p, d = math.radians(site[0]), math.radians(dec)
    h = np.asarray(lst, float) - math.radians(ra)
    alt = np.degrees(np.arcsin(np.clip(math.sin(p) * math.sin(d) + math.cos(p) * math.cos(d) * np.cos(h), -1, 1)))
    az = np.degrees(np.arctan2(-math.cos(d) * np.sin(h), math.sin(d) * math.cos(p) - math.cos(d) * np.cos(h) * math.sin(p))) % 360
    q = np.degrees(np.unwrap(np.arctan2(np.sin(h), math.tan(p) * math.cos(d) - math.sin(d) * np.cos(h))))
    return {"alt": alt, "az": az, "q": q, "rate": devices.rotation_rate(alt, az, site[0])}


def mount_mode(fit: dict | None, device: dict) -> dict:
    """Montierung einer Nacht aus dem Vergleich Bilddrehung ↔ parallaktischer Winkel (fit aus `tracking`) und der Geräte-Tabelle.
    state: rotating (dreht wie Alt-Az) | steady (steht still) | unknown; mode: altaz | eq | derot | None (unklar)."""
    from . import devices
    if not fit or fit.get("q_range") is None or fit["q_range"] < Q_MIN or fit.get("slope") is None:
        return {"state": "unknown", "mode": None, **(fit or {})}
    s = abs(fit["slope"])
    d = devices.get(device.get("model") or device.get("id")) or {}
    if s >= ROT_ALTAZ:
        return {"state": "rotating", "mode": "altaz", **fit}
    if s > ROT_STEADY:
        return {"state": "unknown", "mode": None, **fit}
    if d.get("derotator") == "optical":
        mode = "derot"
    elif d.get("eq_mode") or not device.get("smart"):          # EQ-fähiges Gerät bzw. Astrokamera (meist EQ-Montierung)
        mode = "eq"
    else:
        mode = None                                             # Gerät ohne EQ-Modus und ohne Derotator: unerwartet, nichts behaupten
    return {"state": "steady", "mode": mode, **fit}


ALT_BINS = (0, 30, 45, 60, 70, 80, 90)


def _flag(f: dict) -> int:
    """0 = verwendet, 1 = wegen Sternform verworfen (verzogen/unscharf), 2 = aus anderem Grund verworfen."""
    if f.get("status") != "rejected":
        return 0
    r = str(f.get("reason") or "").lower()
    return 1 if any(k in r for k in SHAPE_REASONS) else 2


def sky_rejects(rows: list[dict], trk: dict, prof: dict, mount: dict, exptime: float | None, shape, tr: dict | None) -> dict:
    """Verworfene Aufnahmen nach Höhe/Bildfelddrehung. trk: sky_track der Aufnahmen; prof: devices.profile des Geräts;
    mount: mount_mode der Nacht (gemessen schlägt Tabelle). Ergebnis: Höhenklassen, Zone starker Drehung gegen den Rest, Urteil."""
    from . import devices
    flags = np.array([_flag(f) for f in rows])
    alt, rate = trk["alt"], trk["rate"]
    H, W = (shape or [0, 0])[-2:]
    radius = math.hypot(W, H) / 2 if W and H else prof["radius_px"]
    mode = {"altaz": "software", "eq": "eq", "derot": "derot"}.get(mount.get("mode")) or prof["mode"]
    p = {**prof, "mode": mode, "exp": float(exptime or prof["exp"]), "radius_px": radius}
    smear = devices.edge_smear(rate, p) if mode in ("software", "derot") else np.zeros(len(rows))
    zl = prof.get("zenith_limit") if mode in ("software", "derot", "legacy") else None
    hi = (smear >= 1.0) | ((alt > zl) if zl is not None else False)
    if mode == "legacy":
        hi = alt > devices.DEFAULT_ZENITH
    bins = []
    for a, b in zip(ALT_BINS[:-1], ALT_BINS[1:]):
        m = (alt >= a) & (alt < b if b < 90 else alt <= b)
        if m.any():
            bins.append({"from": a, "to": b, "n": int(m.sum()), "shape": int((flags[m] == 1).sum()), "other": int((flags[m] == 2).sum())})
    def part(m):
        n = int(m.sum())
        return {"n": n, "shape": int((flags[m] == 1).sum()), "share": _r(float((flags[m] == 1).mean()), 3) if n else None}
    zone, rest = part(hi), part(~hi)
    aligned = bool(tr and tr.get("elong_vs_drift") is not None and (tr.get("elong_coherence") or 0) >= 0.4 and tr["elong_vs_drift"] <= 25)
    drift = bool(tr and (tr.get("smear_ratio") or 0) >= SMEAR_WARN)
    verdict = None
    if zone["n"] >= 5 and rest["n"] >= 5 and zone["shape"] >= 3:
        sz, sr = zone["share"], rest["share"] or 0.0
        if sz >= max(2 * sr, sr + 0.15):
            verdict = "mixed" if aligned else "rotation"
    total_shape = int((flags == 1).sum())
    if verdict is None and total_shape >= max(5, 0.1 * len(rows)):
        verdict = "drift" if (aligned or drift) else "unclear"
    out = {"mode": mode, "zenith_limit": zl, "rate_max": _r(float(rate.max()), 3) if len(rate) else None,
           "smear_max": _r(float(smear.max()), 2) if len(smear) else None, "alt_range": [_r(float(alt.min()), 1), _r(float(alt.max()), 1)],
           "bins": bins, "zone": zone, "rest": rest, "rejected_shape": total_shape, "rejected_other": int((flags == 2).sum()),
           "aligned": aligned, "verdict": verdict}
    t0 = rows[0]["t"]
    out["series"] = _thin([[round((f["t"] - t0).total_seconds() / 3600, 4), _r(float(alt[i]), 1), _r(float(trk["az"][i]), 1),
                            _r(float(rate[i]), 3), int(flags[i])] for i, f in enumerate(rows)], 400)
    return out


def tracking(fr: list[dict], links: dict, exptime: float | None, scale: float | None, shape: tuple | None,
             para: dict | None = None) -> dict | None:
    """Drift, Unruhe, Drehung und Verschmieren einer Nacht aus den Registrierungsmatrizen (Frame → Referenz).
    para: parallaktischer Winkel je Aufnahme (id → Grad) – Vergleich mit der Bilddrehung ergibt die Montierung (`rot_vs_q`)."""
    rows = [f for f in fr if f["id"] in links]
    if len(rows) < MIN_SEG + 1:
        return None
    H, W = (shape or [0, 0])[-2:]
    c = np.array([(W - 1) / 2, (H - 1) / 2, 1.0])
    t0 = rows[0]["t"]
    ts = np.array([(f["t"] - t0).total_seconds() for f in rows])
    Ms = np.array([links[f["id"]][1] for f in rows])
    pos = (Ms @ c)[:, :2]
    rot = np.degrees(np.unwrap(np.arctan2(Ms[:, 1, 0], Ms[:, 0, 0])))
    segs = segments(ts, pos)
    out_segs, vel, wts, jit, rots, per = [], [], [], [], [], None
    res_all = np.full(len(rows), np.nan)
    for s in segs:
        dur = ts[s[-1]] - ts[s[0]]
        if len(s) < MIN_SEG or dur < 15:
            continue
        tt = ts[s] - ts[s].mean()
        A = np.c_[np.ones(len(s)), tt]
        coef = np.linalg.lstsq(A, pos[s], rcond=None)[0]            # (2, 2): Achsabschnitt, Geschwindigkeit
        v = coef[1]
        res = pos[s] - A @ coef
        res_all[s] = np.linalg.norm(res, axis=1)
        dof = max(1, len(s) - 2)
        jit.append((float(np.sqrt((res**2).sum() / dof / 2)), len(s)))
        rr = np.polyfit(tt, rot[s], 1)[0] if np.ptp(tt) > 0 else 0.0
        rots.append((rr, len(s)))
        vel.append(v)
        wts.append(len(s))
        if dur >= 600 and len(s) >= 24:                            # periodischer Fehler nur in langen Abschnitten messbar
            q = np.polyfit(tt, pos[s], 2)
            r2 = pos[s] - np.c_[tt**2, tt, np.ones(len(s))] @ q
            p = _periodic(ts[s], r2)
            if p and (per is None or p["amp_px"] > per["amp_px"]):
                per = p
        out_segs.append({"t0": round(float(ts[s[0]]), 1), "t1": round(float(ts[s[-1]]), 1), "n": int(len(s)),
                         "vx": _r(v[0], 4), "vy": _r(v[1], 4)})
    if not vel:
        return {"segments": len(segs), "measured": 0, "reason": "keine Abschnitte mit mindestens 3 Aufnahmen"}
    vel, wts = np.array(vel), np.array(wts, float)
    speed = np.linalg.norm(vel, axis=1)
    order = np.argsort(speed)
    cw = np.cumsum(wts[order]) / wts.sum()
    v_med = float(speed[order][np.searchsorted(cw, 0.5)])                # gewichteter Median
    mean_v = (vel * wts[:, None]).sum(0) / wts.sum()
    coherence = float(np.linalg.norm(mean_v) / max((speed * wts).sum() / wts.sum(), 1e-9))
    direction = float(np.degrees(np.arctan2(mean_v[1], mean_v[0])) % 360)
    jitter = float(np.average([j for j, _ in jit], weights=[n for _, n in jit]))
    rate = float(np.average([abs(r) for r, _ in rots], weights=[n for _, n in rots])) * 60   # Grad je Minute
    exp = float(exptime or 0)
    fw = np.array([f["metrics"].get("fwhm") or np.nan for f in rows], float)
    fwhm = float(np.nanmedian(fw)) if np.isfinite(fw).any() else None
    smear = v_med * exp
    half_diag = math.hypot(W, H) / 2
    rot_smear = math.radians(rate / 60 * exp) * half_diag
    seg_dur = [s["t1"] - s["t0"] for s in out_segs]
    out = {"segments": len(segs), "measured": len(out_segs), "frames": int(wts.sum()), "linked": len(rows), "total": len(fr),
           "speed_px_s": _r(v_med, 4), "direction": _r(direction, 1), "coherence": _r(coherence, 2),
           "jitter_px": _r(jitter, 2), "rot_deg_min": _r(rate, 4), "rot_spread": _r(float(np.ptp(rot)), 3),
           "smear_px": _r(smear, 2), "rot_smear_px": _r(rot_smear, 2), "fwhm_px": _r(fwhm, 2),
           "smear_ratio": _r(smear / fwhm, 2) if fwhm and exp else None, "seg_median_s": _r(float(np.median(seg_dur)), 0),
           "recenter": len(segs) - 1, "periodic": per}
    if scale:
        out.update(speed_as_min=_r(v_med * scale * 60, 2), jitter_as=_r(jitter * scale, 2), smear_as=_r(smear * scale, 2))
    # Vorzugsrichtung länglicher Sterne (neuere Analysen) gegen die Driftrichtung
    pas = [f["metrics"]["ecc_pa"] for f in rows if f["metrics"].get("ecc_pa")]
    if len(pas) >= 10:
        a = np.radians([p[0] for p in pas]) * 2
        w = np.array([p[1] for p in pas])
        cc, ss = float((np.cos(a) * w).sum() / w.sum()), float((np.sin(a) * w).sum() / w.sum())
        pa = float(np.degrees(np.arctan2(ss, cc)) / 2 % 180)
        out["elong_pa"] = _r(pa, 1)
        out["elong_coherence"] = _r(float(np.hypot(cc, ss)), 2)
        out["elong_vs_drift"] = _r(_axial_diff(pa, direction % 180), 1)
    # Länglichkeit gegen Driftgeschwindigkeit des Abschnitts (Rangkorrelation)
    ecc = np.array([f["metrics"].get("ecc") or np.nan for f in rows], float)
    loc = np.full(len(rows), np.nan)
    for s, sg in zip([s for s in segs if len(s) >= MIN_SEG and ts[s[-1]] - ts[s[0]] >= 15], out_segs):
        loc[s] = math.hypot(sg["vx"] or 0, sg["vy"] or 0)
    ok = np.isfinite(ecc) & np.isfinite(loc)
    if ok.sum() >= 15 and np.ptp(loc[ok]) > 0 and np.ptp(ecc[ok]) > 0:
        from scipy.stats import spearmanr
        out["ecc_vs_speed"] = _r(float(spearmanr(loc[ok], ecc[ok])[0]), 2)
    if para:
        qq = np.array([para.get(f["id"], np.nan) for f in rows], float)
        ok = np.isfinite(qq)
        if ok.sum() >= 8:
            fit = theil_sen(qq[ok], rot[ok])
            out["rot_vs_q"] = {"q_range": _r(float(np.ptp(qq[ok])), 2), "rot_range": _r(float(np.ptp(rot[ok])), 2),
                               "slope": _r(fit["slope"], 3) if fit else None}
    out["series"] = _thin([[round(float(ts[i] / 3600), 4), round(float(pos[i, 0] - pos[0, 0]), 1), round(float(pos[i, 1] - pos[0, 1]), 1),
                            round(float(rot[i] - rot[0]), 3), _r(res_all[i], 2)] for i in range(len(rows))], 400)
    return out


def _thin(pts: list, n: int) -> list:
    if len(pts) <= n:
        return pts
    idx = np.unique(np.linspace(0, len(pts) - 1, n).round().astype(int))
    return [pts[i] for i in idx]


# ------------------------------------------------------------------ Ausreißer ---
def events(fr: list[dict]) -> dict:
    """Gruppen kurz hintereinander unscharfer/verzogener Aufnahmen bei normaler Sternzahl (Stöße, Böen, Berührung)."""
    if len(fr) < 10:
        return {"frames": 0, "events": []}
    get = lambda k: np.array([f["metrics"].get(k) if f["metrics"].get(k) is not None else np.nan for f in fr], float)
    fw, ec, ns = get("fwhm"), get("ecc"), get("n_stars")
    ok = np.isfinite(fw) & np.isfinite(ec)
    if ok.sum() < 10:
        return {"frames": 0, "events": []}
    f_med, e_med, n_med = np.median(fw[ok]), np.median(ec[ok]), np.nanmedian(ns)
    f_lim = f_med + max(4 * _mad(fw[ok]), 0.35 * f_med)
    e_lim = max(0.8, e_med + 4 * _mad(ec[ok]))
    bad = ok & ((fw > f_lim) | (ec > e_lim)) & (ns >= 0.5 * n_med)
    evs, cur = [], []
    for i in np.nonzero(bad)[0]:
        if cur and (i - cur[-1] > 2 or (fr[i]["t"] - fr[cur[-1]]["t"]).total_seconds() > 120):
            evs.append(cur)
            cur = []
        cur.append(int(i))
    if cur:
        evs.append(cur)
    out = []
    for e in evs:
        if len(e) < 3:                                      # Einzelne Ausreißer/Paare: Seeing-Spitze, Satellit – kein Ereignis
            continue
        out.append({"start": fr[e[0]]["t"].isoformat(), "end": fr[e[-1]]["t"].isoformat(), "n": len(e),
                    "fwhm": _r(float(np.median(fw[e]) / f_med), 2), "ecc": _r(float(np.median(ec[e])), 2),
                    "kind": "verzogen" if np.median(ec[e]) > e_lim else "unscharf"})
    return {"frames": int(bad.sum()), "share": _r(float(bad.mean()), 3), "events": out, "bad_idx": [int(i) for i in np.nonzero(bad)[0]]}


# ------------------------------------------------------------------ Fokus ---
def focus(fr: list[dict], alt: np.ndarray | None, scale: float | None, skip: set) -> dict | None:
    """Fokusdrift einer Nacht: FWHM (Seeing-Anteil über die Luftmasse herausgerechnet) gegen Zeit und Temperatur."""
    from .skyquality import airmass
    idx = [i for i, f in enumerate(fr) if i not in skip and f["metrics"].get("fwhm") and f.get("status") != "failed"]
    if len(idx) < 12:
        return None
    t0 = fr[0]["t"]
    th = np.array([(fr[i]["t"] - t0).total_seconds() / 3600 for i in idx])
    fw = np.array([fr[i]["metrics"]["fwhm"] for i in idx], float)
    k = np.ones(len(idx))
    if alt is not None:
        a = np.asarray(alt, float)[idx]
        good = a > 10
        if good.sum() >= 12:
            k = np.where(good, airmass(np.maximum(a, 10)) ** -SEEING_EXP, np.nan)
    corr = fw * k
    unit = scale or 1.0
    med = float(np.nanmedian(corr))
    out = {"n": len(idx), "hours": _r(float(np.ptp(th)), 2), "airmass": alt is not None, "unit": "as" if scale else "px",
           "fwhm": _r(float(np.median(fw)) * unit, 2), "fwhm_corr": _r(med * unit, 2)}
    # Zeit und Luftmasse laufen bei auf- oder untergehenden Objekten fast gleich – wie viel der Unschärfe Seeing ist, bleibt
    # dann offen. Gemeldet wird eine Drift nur, wenn sie ohne und mit voller Seeing-Korrektur gleich gerichtet und sicher ist;
    # angegeben wird der kleinere Wert.
    if np.ptp(th) >= 0.75:
        fits_ = [(theil_sen(th, fw), float(np.median(fw)))] + ([(theil_sen(th, corr), med)] if out["airmass"] else [])
        fits_ = [(t_, m_) for t_, m_ in fits_ if t_]
        if fits_:
            pcts = [(t_["slope"] / m_ * 100, t_["lo"] / m_ * 100, t_["hi"] / m_ * 100) for t_, m_ in fits_]
            pct, lo, hi = min(pcts, key=lambda p: abs(p[0]))
            same = all(p[1] > 0 for p in pcts) or all(p[2] < 0 for p in pcts)
            out["time"] = {"pct_h": _r(pct, 1), "lo": _r(lo, 1), "hi": _r(hi, 1), "raw": _r(pcts[0][0], 1),
                           "corr": _r(pcts[-1][0], 1) if len(pcts) > 1 else None, "sig": bool(same and abs(pct) >= FOCUS_MIN),
                           # stabil nur, wenn keine der Varianten eine merkliche Drift zulässt
                           "stable": all(p[1] > -FOCUS_MIN and p[2] < FOCUS_MIN for p in pcts)}
    temps = np.array([fr[i]["temp"] if fr[i].get("temp") is not None else np.nan for i in idx], float)
    if np.isfinite(temps).sum() >= 12 and np.nanmax(temps) - np.nanmin(temps) >= 1.5:
        tt = theil_sen(temps, corr)
        out["temp_range"] = [_r(float(np.nanmin(temps)), 1), _r(float(np.nanmax(temps)), 1)]
        if tt:
            pc = tt["slope"] / med * 100
            out["temp"] = {"pct_c": _r(pc, 1), "lo": _r(tt["lo"] / med * 100, 1), "hi": _r(tt["hi"] / med * 100, 1),
                           "sig": bool(tt["lo"] > 0 or tt["hi"] < 0)}
    # Marken: Stellina legt nach dem automatischen Nachfokussieren einen neuen Ordner an
    marks = []
    folders = [fr[i].get("folder") for i in idx]
    for j in range(1, len(idx)):
        if folders[j] and folders[j - 1] and folders[j] != folders[j - 1]:
            # gleiche Beobachtung, nächster Ordner = automatisch nachfokussiert; Ordner „01“ = neue Beobachtung (beginnt ebenfalls mit Fokus)
            b, a = corr[max(0, j - 10):j], corr[j:j + 10]
            b, a = b[np.isfinite(b)], a[np.isfinite(a)]
            m = {"t": fr[idx[j]]["t"].isoformat(), "h": _r(float(th[j]), 3), "folder": folders[j],
                 "kind": "restart" if folders[j] < folders[j - 1] or folders[j] == "01" else "refocus"}
            if len(b) >= 4 and len(a) >= 4:
                m["change"] = _r((float(np.median(a)) / float(np.median(b)) - 1) * 100, 1)
            marks.append(m)
    out["marks"] = marks
    out["series"] = _thin([[_r(float(th[j]), 4), _r(float(fw[j]) * unit, 2), _r(float(corr[j]) * unit, 2),
                            _r(float(temps[j]), 1)] for j in range(len(idx))], 400)
    return out


# ------------------------------------------------------------------ Bildfeld ---
def field_fit(x: np.ndarray, y: np.ndarray, f: np.ndarray, W: int, H: int, boot: int = 40, seed: int = 3) -> dict | None:
    """Sterngröße über das Feld: f = a + b·u + c·v + d·r² (u, v relativ zur halben Diagonale). Verkippung = Ebene
    (Ecke zu Ecke in %), Wölbung = r²-Anteil (Ecke gegen Mitte in %); Unsicherheit per Bootstrap über die Sterne."""
    from .skyquality import _clip_fit
    R = math.hypot(W, H) / 2
    u, v = (x - (W - 1) / 2) / R, (y - (H - 1) / 2) / R
    A = np.c_[np.ones(len(x)), u, v, u**2 + v**2]
    if len(x) < 30:
        return None
    coef, use = _clip_fit(A, f, hi=3.0, lo=3.0)
    if use.sum() < 30:
        return None

    def derive(c):
        a = c[0] + c[3] * 0.5                                  # mittlere Größe (r² ≈ 0,5 im Mittel über das Feld)
        return 2 * math.hypot(c[1], c[2]) / a * 100, c[3] / a * 100, math.degrees(math.atan2(c[2], c[1])) % 360
    tilt, curv, ang = derive(coef)
    rng = np.random.default_rng(seed)
    ii = np.nonzero(use)[0]
    bt, bc = [], []
    for _ in range(boot):
        s = rng.choice(ii, len(ii))
        c = np.linalg.lstsq(A[s], f[s], rcond=None)[0]
        t_, c_, _ = derive(c)
        bt.append(t_)
        bc.append(c_)
    st, sc = float(np.std(bt)), float(np.std(bc))
    return {"stars": int(use.sum()), "tilt_pct": _r(tilt, 1), "tilt_err": _r(st, 1), "tilt_dir": _r(ang, 0),
            "curv_pct": _r(curv, 1), "curv_err": _r(sc, 1),
            "tilt_sig": bool(tilt >= TILT_MIN and tilt >= 3 * st), "curv_sig": bool(abs(curv) >= CURV_MIN and abs(curv) >= 3 * sc)}


def _grid(x, y, val, W, H, rows, cols, k=4, flip=True) -> list:
    iy = np.clip((y / H * rows).astype(int), 0, rows - 1)
    ix = np.clip((x / W * cols).astype(int), 0, cols - 1)
    out = []
    for r in range(rows):
        out.append([_r(float(np.median(val[(iy == r) & (ix == c)])), 3) if ((iy == r) & (ix == c)).sum() >= k else None
                    for c in range(cols)])
    return out[::-1] if flip else out


def side_text(deg: float) -> str:
    """Richtung im fertigen Bild (Norden oben = Stack vertikal gespiegelt): von wo nach wo die Sterne größer werden."""
    a = deg % 360                                              # Stack-y wächst mit der Zeile; gespiegelt zeigt sie im Bild nach oben
    names = ["rechts", "rechts oben", "oben", "links oben", "links", "links unten", "unten", "rechts unten"]
    return names[int(((a + 22.5) % 360) // 45)]


def field_stack(mean: np.ndarray, cov: np.ndarray, sc: float = 1.0) -> dict | None:
    """Sterngröße und -form über das Feld im Master-Stack (Stack-Raster → native Pixel)."""
    from .quality import detect_stars
    lum = mean.mean(0) if mean.ndim == 3 else mean
    valid = cov >= 0.9
    k = int(round(sc)) if sc and sc > 1 else 1
    if k > 1:
        h, w = lum.shape[0] // k * k, lum.shape[1] // k * k
        lum = lum[:h, :w].reshape(h // k, k, w // k, k).mean((1, 3))
        valid = valid[:h, :w].reshape(h // k, k, w // k, k).all((1, 3))
    from scipy import ndimage as ndi
    valid = ndi.binary_erosion(valid, iterations=12)
    if valid.sum() < 0.2 * valid.size:
        return None
    rows, cols = np.nonzero(valid)
    y0, y1, x0, x1 = rows.min(), rows.max() + 1, cols.min(), cols.max() + 1
    img = np.where(valid, lum, np.median(lum[valid])).astype(np.float32)[y0:y1, x0:x1]
    st = detect_stars(img, max_stars=1200, thresh=8.0)
    if len(st) < 40:
        return None
    H, W = img.shape
    keep = valid[y0:y1, x0:x1][st.y.astype(int), st.x.astype(int)]
    lim = np.quantile(st.flux, 0.97)
    keep &= (st.flux < lim) & (st.fwhm < 3 * np.median(st.fwhm))
    x, y, f, e = st.x[keep], st.y[keep], st.fwhm[keep], st.ecc[keep]
    pa = st.pa[keep] if st.pa is not None else None
    fit = field_fit(x, y, f, W, H)
    if not fit:
        return None
    rows_, cols_ = STACK_GRID
    out = {**fit, "source": "stack", "fwhm_px": _r(float(np.median(f)), 2), "ecc": _r(float(np.median(e)), 2),
           "map": _grid(x, y, f, W, H, rows_, cols_), "ecc_map": _grid(x, y, e, W, H, rows_, cols_), "size": [W, H],
           "soft_side": side_text(fit["tilt_dir"])}
    if pa is not None:                                          # radial (+1) oder tangential (−1) ausgerichtete Sterne am Rand
        R = math.hypot(W, H) / 2
        u, v = (x - (W - 1) / 2) / R, (y - (H - 1) / 2) / R
        sel = (np.hypot(u, v) > 0.5) & (e > 0.35)
        if sel.sum() >= 15:
            out["radial"] = _r(float(np.median(np.cos(2 * (np.radians(pa[sel]) - np.arctan2(v[sel], u[sel]))))), 2)
    return out


def field_frames(fr: list[dict]) -> dict | None:
    """Mittlere 3×3-Schärfekarte der Einzelaufnahmen (neuere Analysen, `metrics.tiles`), je Aufnahme auf ihren Median bezogen."""
    maps = []
    for f in fr:
        t = f["metrics"].get("tiles")
        if not t:
            continue
        a = np.array([[np.nan if v is None else v for v in row] for row in t], float)
        if np.isfinite(a).sum() >= 7:
            maps.append(a / np.nanmedian(a))
    if len(maps) < 10:
        return None
    m = np.nanmedian(np.array(maps), axis=0)
    g = m.shape[0]
    yy, xx = np.mgrid[0:g, 0:g]
    u, v = (xx - (g - 1) / 2) / ((g - 1) / 2) / math.sqrt(2), (yy - (g - 1) / 2) / ((g - 1) / 2) / math.sqrt(2)
    ok = np.isfinite(m)
    A = np.c_[np.ones(ok.sum()), u[ok], v[ok], (u**2 + v**2)[ok]]
    c = np.linalg.lstsq(A, m[ok], rcond=None)[0]
    a = c[0] + c[3] * 0.5
    sd = np.nanstd(np.array(maps), axis=0)
    err = float(np.nanmedian(sd)) / math.sqrt(len(maps)) / a * 100 * 2
    tilt = 2 * math.hypot(c[1], c[2]) / a * 100
    ang = math.degrees(math.atan2(c[2], c[1])) % 360
    return {"source": "frames", "frames": len(maps), "tilt_pct": _r(tilt, 1), "tilt_err": _r(err, 1), "tilt_dir": _r(ang, 0),
            "curv_pct": _r(c[3] / a * 100, 1), "soft_side": side_text(ang),
            "tilt_sig": bool(tilt >= TILT_MIN and tilt >= 3 * err), "map": [[_r(v, 3) for v in row] for row in m[::-1]]}


# ------------------------------------------------------------------ Gesamt ---
def compute(pl, pid: int, nights: set | None = None, site_for=None, cfg: dict | None = None, log=lambda m: None) -> dict:
    """Gerätediagnose eines Objekts (Nächte einer Version) – nur aus gespeicherten Daten."""
    from . import devices, photometry, tutor
    from .skyquality import frame_links
    db = pl.db
    proj = db.one("SELECT * FROM projects WHERE id=?", (pid,))
    if not proj:
        raise KeyError(pid)
    fr = load_frames(db, pid, nights)
    if len(fr) < 8:
        return {"v": DIAG_VERSION, "ok": False, "error": "Zu wenige analysierte Aufnahmen für eine Gerätediagnose.", "erstellt": time.time()}
    device = tutor.detect_device([f["instrument"] for f in fr], tuple(fr[0]["shape"]) if fr[0].get("shape") else None)
    prof = devices.profile(device)
    if any(f["temp"] is None for f in fr):
        header_temps(fr)
    scales = [f["scale"] for f in fr if f.get("scale")]
    scale = float(np.median(scales)) if scales else None
    states = pl._master(pid)
    st = states.get(None) or (next(iter(states.values())) if states else None)
    links, field, sc = {}, None, 1.0
    if st is not None:
        sc = float(st.meta.get("scale") or 1)
        log("Aufnahmen dem Referenzbild zuordnen")
        links = frame_links(st.meta, [{"id": f["id"], "metrics": f["metrics"]} for f in fr])
        if scale is None:
            try:
                wf = pl.s.work_dir / f"p{pid}" / "wcs.json"
                if wf.exists():
                    w = photometry.wcs_from(json.loads(wf.read_text())["wcs"])
                    scale = float(np.sqrt(abs(np.linalg.det(w.pixel_scale_matrix)))) * 3600 * sc
            except Exception:
                pass
        log("Sterngröße über das Bildfeld messen")
        try:
            mean, cov = st.result()
            field = field_stack(mean, cov, sc)
        except Exception as e:
            field = {"error": str(e)[:200]}
    by_night: dict[str, list] = {}
    for f in fr:
        by_night.setdefault(f["night"], []).append(f)
    out_n, sources = [], []
    log("Nachführung und Fokus je Nacht")
    for night, rows in sorted(by_night.items()):
        exps = [f["exptime"] for f in rows if f.get("exptime")]
        exptime = float(np.median(exps)) if exps else None
        ev = events(rows)
        # Standort je Nacht (FITS-Ort bzw. Zeitraum der Ortsliste) – ein Objekt kann an mehreren Orten aufgenommen sein
        site, site_src, _ = tutor.pick_site(rows, cfg or {}, site_for)
        if site:
            sources.append(site_src)
        alt, trk = None, None
        if site and proj["ra"] is not None and proj["dec"] is not None:
            try:
                trk = sky_track([f["t"] for f in rows], proj["ra"], proj["dec"], site)
                alt = trk["alt"]
            except Exception:
                traceback.print_exc()
                alt, trk = None, None
        q = {f["id"]: float(trk["q"][i]) for i, f in enumerate(rows)} if trk else None
        tr = tracking(rows, links, exptime, scale, rows[0].get("shape"), q) if links else None
        mount = mount_mode((tr or {}).get("rot_vs_q"), device) if trk else {"state": "unknown", "mode": None}
        n = {"night": night, "frames": len(rows), "exptime": exptime, "tracking": tr, "mount": mount,
             "sky": sky_rejects(rows, trk, prof, mount, exptime, rows[0].get("shape"), tr) if trk else None,
             "events": {k: v for k, v in ev.items() if k != "bad_idx"},
             "focus": focus(rows, alt, scale, set(ev.get("bad_idx", []))), "site_source": site_src, "site": site,
             "field_frames": field_frames(rows),                 # Kippung dieser Nacht (neuere Analysen) – für den Gerätetrend
             "start": rows[0]["t"].isoformat(), "end": rows[-1]["t"].isoformat()}
        out_n.append(n)
    rot_spread = max([(n["tracking"] or {}).get("rot_spread") or 0 for n in out_n] or [0])
    if field and not field.get("error") and rot_spread > 3:
        field["smeared"] = True                                  # Feldrotation zwischen den Aufnahmen verwischt die Karte
        field["tilt_sig"] = False
    return {"v": DIAG_VERSION, "ok": True, "erstellt": time.time(), "device": {"id": device["id"], "name": device["name"], **({"model": device["model"]} if device.get("model") else {})},
            "device_key": f"{device['id']}|{fr[0]['instrument'] or ''}", "scale": _r(scale, 3), "site": bool(sources),
            "site_source": max(set(sources), key=sources.count) if sources else None, "nights": out_n, "field": field, "field_frames": field_frames(fr),
            "linked": len(links), "total": len(fr), "temps": sum(1 for f in fr if f["temp"] is not None)}


# ------------------------------------------------------------------ Gerätetrend ---
def _night_tilt(n: dict, fld: dict, single: bool):
    """Kippung einer Nacht für den Trend: eigene Karten der Aufnahmen dieser Nacht; die Stack-Messung nur, wenn der Stack
    aus genau dieser einen Nacht besteht (sonst wäre sie ein Mittel über alle Nächte und ergäbe einen falschen Verlauf)."""
    ff = n.get("field_frames") or {}
    if ff.get("tilt_pct") is not None:
        return ff["tilt_pct"]
    return fld.get("tilt_pct") if single and not fld.get("smeared") else None


def device_summary(db) -> list[dict]:
    """Je Gerät alle Nächte (neueste Version je Objekt) mit Kennzahlen und Trend über die Zeit."""
    rows = db.q("SELECT * FROM (SELECT r.id rid, r.project_id, p.name, r.stats, ROW_NUMBER() OVER (PARTITION BY r.project_id "
                "ORDER BY r.id DESC) rn FROM results r JOIN projects p ON p.id=r.project_id "
                "WHERE json_extract(r.stats,'$.diagnose.ok')=1) WHERE rn=1")
    dev: dict[str, dict] = {}
    for r in rows:
        d = json.loads(r["stats"] or "{}").get("diagnose") or {}
        if d.get("v") != DIAG_VERSION:
            continue
        x = dev.setdefault(d.get("device_key") or "?", {"key": d.get("device_key"), "name": (d.get("device") or {}).get("name"),
                                                         "id": (d.get("device") or {}).get("id"), "nights": []})
        fld = d.get("field") or {}
        single = len(d.get("nights") or []) == 1
        for n in d.get("nights") or []:
            tr, fo = n.get("tracking") or {}, n.get("focus") or {}
            x["nights"].append({"night": n["night"], "pid": r["project_id"], "rid": r["rid"], "name": r["name"],
                                "speed": tr.get("speed_as_min"), "smear": tr.get("smear_ratio"), "jitter": tr.get("jitter_as"),
                                "fwhm": fo.get("fwhm_corr") if fo.get("unit") == "as" else None,
                                "focus": (fo.get("time") or {}).get("pct_h"),
                                "events": (n.get("events") or {}).get("share"),
                                "tilt": _night_tilt(n, fld, single), "curv": fld.get("curv_pct") if single else None})
    out = []
    for x in dev.values():
        x["nights"].sort(key=lambda n: n["night"])
        trends = {}
        days = []
        for n in x["nights"]:
            try:
                days.append(dt.date.fromisoformat(n["night"]).toordinal() / 365.25)
            except ValueError:
                days.append(np.nan)
        for k, rel in (("fwhm", True), ("tilt", False), ("smear", False), ("speed", True)):
            y = np.array([n[k] if n[k] is not None else np.nan for n in x["nights"]], float)
            ok = np.isfinite(y) & np.isfinite(days)
            if ok.sum() < 8 or np.ptp(np.array(days)[ok]) < 0.25:
                continue
            ts = theil_sen(np.array(days)[ok], y[ok])
            if not ts:
                continue
            med = float(np.median(y[ok]))
            per_year = ts["slope"] / med * 100 if rel and med else ts["slope"]
            trends[k] = {"per_year": _r(per_year, 2), "rel": rel, "worse": bool(ts["lo"] > 0), "better": bool(ts["hi"] < 0),
                         "n": int(ok.sum())}
        x["trends"] = trends
        out.append(x)
    return sorted(out, key=lambda x: -len(x["nights"]))


def eq_detected(db) -> dict:
    """Je Gerätemodell (bei Geräten ohne Modell: Erkennungs-ID) die neueste Nacht mit erkannter Montierung:
    {schlüssel: {"eq": bool, "night": …}}. Seestar S50 und S30 sind getrennt; ältere Diagnosen ohne Modell stehen unter der
    Familien-ID (Abfrage über `eq_lookup`). Grundlage für den EQ-Modus in Heute Nacht und im Planer (nur Diagnosen der aktuellen Version)."""
    out: dict[str, dict] = {}
    for r in db.q("SELECT json_extract(stats,'$.diagnose') d FROM results WHERE json_extract(stats,'$.diagnose.v')=? "
                  "AND json_extract(stats,'$.diagnose.ok')=1", (DIAG_VERSION,)):
        d = json.loads(r["d"] or "{}")
        dev = d.get("device") or {}
        key = dev.get("model") or dev.get("id")
        for n in d.get("nights") or []:
            mode = (n.get("mount") or {}).get("mode")
            if key and mode in ("eq", "altaz") and n["night"] >= (out.get(key) or {}).get("night", ""):
                out[key] = {"eq": mode == "eq", "night": n["night"]}
    return out


def eq_lookup(det: dict, model: str | None, dev_id: str | None) -> dict | None:
    """Erkannter EQ-Zustand für ein Gerät: erst das Modell; die Familien-ID (ältere Werte ohne Modell, beim Seestar zugleich das S50)
    zählt nur für Geräte ohne eigenes Modell bzw. mit Modell = ID – ein S30 liest nie Werte des S50."""
    if model and (hit := det.get(model)):
        return hit
    return det.get(dev_id) if dev_id and model in (None, dev_id) else None


# ------------------------------------------------------------------ Tutor ---
def _de(v, nd=1) -> str:
    return f"{v:.{nd}f}".replace(".", ",")


def _d(night: str) -> str:
    try:
        return dt.date.fromisoformat(night).strftime("%d.%m.%Y")
    except ValueError:
        return night


def devices_get(device: dict) -> dict | None:
    from . import devices
    return devices.get(device.get("model") or device.get("id"))


def findings(d: dict | None, device: dict) -> list[dict]:
    """Befunde der Gerätediagnose mit vorsichtigen, gerätebezogenen Tipps (nur Gesichertes)."""
    from .tutor import _f
    if not d or not d.get("ok"):
        return []
    out, smart, altaz = [], bool(device.get("smart")), bool(device.get("alt_az"))
    nights = d.get("nights") or []
    # --- Nachführung -------------------------------------------------------------
    tr_n = [n for n in nights if (n.get("tracking") or {}).get("smear_ratio") is not None]
    if tr_n:
        n = max(tr_n, key=lambda n: n["tracking"]["smear_ratio"])
        t = n["tracking"]
        if t["smear_ratio"] >= SMEAR_WARN:
            as_ = f" ({_de(t['smear_as'])}″)" if t.get("smear_as") is not None else ""
            text = (f"Am {_d(n['night'])} wanderte das Bild während jeder Belichtung um etwa {_de(t['smear_px'])} Pixel{as_} – "
                    f"die Sterne sind nur ~{_de(t['fwhm_px'])} Pixel groß. Gemessen aus der Lage der Aufnahmen zueinander "
                    f"({t['measured']} Abschnitte zwischen {t['recenter']} Nachzentrierungen).")
            if t.get("elong_vs_drift") is not None and t.get("elong_coherence", 0) >= 0.4 and t["elong_vs_drift"] <= 25:
                text += " Die Sterne sind genau in Driftrichtung länglich – die Ursache liegt also bei der Nachführung, nicht bei der Optik."
            tips = (["Die Drift einer azimutalen Montierung wächst nahe am Zenit und tief am Horizont – Objekte zwischen etwa 30° und 70° Höhe aufnehmen."]
                    if altaz else ["Polausrichtung prüfen – eine gleichmäßige Drift in einer Richtung deutet auf eine ungenaue Einnordung.",
                                   "Autoguiding gleicht Drift aus; ohne Guiding kürzer belichten."])
            if smart:
                tips.append("Gerät auf festem, ebenem Untergrund aufstellen; weicher Boden (Rasen) gibt im Lauf der Nacht nach.")
            out.append(_f("tracking_drift", "warn" if t["smear_ratio"] >= 1 else "info", "Nachführung: Bild driftet während der Belichtung",
                          text, tips, {"verschmieren_px": t["smear_px"], "sterngroesse_px": t["fwhm_px"]}))
        per = next((n["tracking"].get("periodic") for n in tr_n if n["tracking"].get("periodic")), None)
        if per and not smart:
            out.append(_f("periodic_error", "info", "Periodischer Nachführfehler",
                          f"Die Abweichung schwingt mit einer Periode von etwa {_de(per['period_s'] / 60)} min und ±{_de(per['amp_px'])} Pixeln.",
                          ["Periodischen Fehler mit PEC (Periodic Error Correction) der Montierung trainieren oder Autoguiding nutzen."]))
    # --- Montierung / Bildfelddrehung -----------------------------------------------
    eq_n = [n for n in nights if (n.get("mount") or {}).get("mode") == "eq"]
    if eq_n and smart:
        n = eq_n[-1]
        out.append(_f("mount_eq", "ok", "EQ-Modus erkannt",
                      f"Am {_d(n['night'])} stand das Bildfeld still, obwohl eine azimutale Montierung es in dieser Zeit um etwa "
                      f"{_de(n['mount']['q_range'], 0)}° gedreht hätte – das Gerät lief offenbar im EQ-Modus. Heute Nacht und der Planer "
                      "rechnen für dieses Gerät dann ohne Bildfelddrehung.", []))
    sky_n = [n for n in nights if (n.get("sky") or {}).get("verdict") in ("rotation", "mixed")]
    if sky_n:
        n = max(sky_n, key=lambda n: n["sky"]["zone"]["shape"])
        s = n["sky"]
        z, r = s["zone"], s["rest"]
        where = (f"über {_de(s['zenith_limit'], 0)}° Höhe bzw. bei schneller Bildfelddrehung" if s.get("zenith_limit") is not None
                 else "bei schneller Bildfelddrehung (nahe am Meridian, hoch am Himmel)")
        text = (f"Am {_d(n['night'])} wurden {where} {z['shape']} von {z['n']} Aufnahmen wegen verzogener oder unscharfer Sterne verworfen "
                f"({_de(z['share'] * 100, 0)} %), sonst nur {r['shape']} von {r['n']} ({_de((r['share'] or 0) * 100, 0)} %). "
                "Höhe und Drehung sind aus Aufnahmezeit, Ort und Objektlage berechnet.")
        if s["verdict"] == "rotation":
            text += " Das passt zur Zenit-Totzone bzw. Bildfelddrehung einer azimutalen Montierung."
        else:
            text += (" Die Sterne sind allerdings auch in Driftrichtung länglich – vermutlich wirken Drehung und Drift bzw. Wind zusammen.")
        tips = ["Hohe Ziele vor oder nach dem Meridiandurchgang aufnehmen; „Heute Nacht“ und der Planer berücksichtigen das je Gerät."]
        if (devices_get(device) or {}).get("eq_mode"):
            tips.append("Im EQ-Modus (Polhöhenwiege) dreht sich das Bildfeld nicht – für lange Sitzungen nahe am Zenit die bessere Wahl.")
        out.append(_f("zenith_rejects", "info" if z["shape"] < 10 else "warn", "Verworfene Aufnahmen hoch am Himmel", text, tips,
                      {"verworfen_zone": z["shape"], "aufnahmen_zone": z["n"], "verworfen_rest": r["shape"], "aufnahmen_rest": r["n"]}))
    # --- Stöße / Böen -------------------------------------------------------------
    ev = [(n, e) for n in nights for e in (n.get("events") or {}).get("events") or []]
    if ev:
        total = sum(e["n"] for _, e in ev)
        n0, e0 = max(ev, key=lambda x: x[1]["n"])
        t = dt.datetime.fromisoformat(e0["start"]).strftime("%H:%M")
        text = (f"{len(ev)} kurze Störung{'en' if len(ev) > 1 else ''} mit zusammen {total} Aufnahmen, die schlagartig "
                f"{'verzogen' if e0['kind'] == 'verzogen' else 'unscharf'} waren, obwohl die Sterne sichtbar blieben – die längste am "
                f"{_d(n0['night'])} um {t} UTC ({e0['n']} Aufnahmen). Typisch sind Windböen, Erschütterungen (Schritte, Tisch) oder eine Berührung.")
        out.append(_f("gusts", "info" if total < 10 else "warn", "Stöße oder Böen während der Aufnahme", text,
                      [device.get("wind"), "AstroFlow sortiert solche Aufnahmen beim Stacken automatisch aus."]))
    # --- Fokus --------------------------------------------------------------------
    fo_n = [n for n in nights if (n.get("focus") or {}).get("time")]
    drift = [n for n in fo_n if n["focus"]["time"]["sig"] and n["focus"]["time"]["pct_h"] > 0]
    if drift:
        n = max(drift, key=lambda n: n["focus"]["time"]["pct_h"])
        f = n["focus"]
        text = (f"Am {_d(n['night'])} wurden die Sterne um etwa {_de(f['time']['pct_h'])} % je Stunde größer"
                + (" – der Einfluss der Luftmasse (tiefer stehendes Objekt) ist dabei schon herausgerechnet." if f.get("airmass")
                   else " (ohne Standort ließ sich der Einfluss der Objekthöhe nicht herausrechnen).")
                + (f" Mit der Sensortemperatur ({_de(f['temp_range'][1])} → {_de(f['temp_range'][0])} °C) hängt es deutlich zusammen."
                   if (f.get("temp") or {}).get("sig") and f["temp"]["pct_c"] < 0 else ""))
        helped = [m for m in f.get("marks") or [] if m.get("change") is not None and m["change"] <= -8]
        if helped:
            text += f" Nach dem automatischen Nachfokussieren waren die Sterne bis zu {_de(-min(m['change'] for m in helped), 0)} % kleiner."
        tips = (["Eine neue Beobachtung beginnt mit einer Fokussierung – fällt die Temperatur stark, kann ein Neustart der Beobachtung helfen."
                 if smart else "Nach Temperaturänderungen nachfokussieren (Autofokus etwa je 1–2 °C)."])
        if not device.get("dew_heater") and device.get("dew"):
            tips.append(device["dew"])
        out.append(_f("focus_drift", "warn" if f["time"]["pct_h"] >= 2 * FOCUS_MIN else "info", "Fokus wanderte im Lauf der Nacht", text, tips,
                      {"prozent_je_stunde": f["time"]["pct_h"]}))
    elif fo_n and all(n["focus"]["time"].get("stable") for n in fo_n):
        hrs = sum(n["focus"].get("hours") or 0 for n in fo_n)
        if hrs >= 1.5:
            out.append(_f("focus_stable", "ok", "Fokus blieb stabil",
                          f"Über {_de(hrs)} Stunden Aufnahmezeit änderte sich die Sterngröße nicht messbar"
                          + (" (Luftmasse herausgerechnet)." if any(n["focus"].get("airmass") for n in fo_n) else "."), []))
    # --- Bildfeld -----------------------------------------------------------------
    fld, ff = d.get("field") or {}, d.get("field_frames") or {}
    if fld.get("tilt_sig") or ff.get("tilt_sig"):
        src = fld if fld.get("tilt_sig") else ff
        both = fld.get("tilt_sig") and ff.get("tilt_sig") and _axial_diff(fld["tilt_dir"] / 2, ff["tilt_dir"] / 2) < 30
        text = (f"Die Sterne sind {side_text(src['tilt_dir'])} im Bild etwa {_de(src['tilt_pct'], 0)} % größer als auf der gegenüberliegenden Seite "
                f"(± {_de(src['tilt_err'], 0)} %, gemessen an {src.get('stars') or src.get('frames')} {'Sternen im Stack' if src['source'] == 'stack' else 'Aufnahmen'}). "
                "Ein einseitiger Schärfeabfall entsteht meist, wenn der Sensor nicht genau senkrecht zur optischen Achse steht (Verkippung).")
        if both:
            text += " Stack und Einzelaufnahmen zeigen dieselbe Richtung."
        tips = (["Bei Smart-Teleskopen lässt sich die Sensorlage nicht selbst justieren. Zeigt der Gerätetrend die Verkippung über viele Nächte gleich, "
                 "lohnt eine Anfrage beim Hersteller-Support."] if smart else
                ["Kameraanschluss und Adapter auf festen, planen Sitz prüfen; bei bleibender Verkippung hilft ein Tilt-Adapter."])
        out.append(_f("tilt", "warn" if src["tilt_pct"] >= 2 * TILT_MIN else "info", "Schärfe fällt zu einer Seite ab", text, tips,
                      {"prozent": src["tilt_pct"], "seite": side_text(src["tilt_dir"])}))
    if fld.get("curv_sig") and fld["curv_pct"] > 0:
        rad = fld.get("radial")
        text = (f"Zum Bildrand hin sind die Sterne etwa {_de(fld['curv_pct'], 0)} % größer als in der Mitte"
                + (", am Rand radial in die Länge gezogen" if rad is not None and rad > 0.3 else "") + ". Das ist eine Eigenschaft der Optik "
                "(Bildfeldwölbung bzw. Randfehler).")
        tips = ([] if smart else ["Bei Refraktoren mit Bildfeldebner den Abstand zum Sensor prüfen (Herstellerangabe, oft 55 mm)."])
        tips.append("AstroFlow kann das nicht korrigieren; für Ränder hilft nur, das Objekt mittig zu halten.")
        out.append(_f("curvature", "info", "Sterne am Bildrand größer", text, tips, {"prozent": fld["curv_pct"]}))
    return out
