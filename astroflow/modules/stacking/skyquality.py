"""Himmelsqualität aus den eigenen Aufnahmen: Himmelshelligkeit (mag/″², „Sky-Quality-Meter ohne Gerät“), Bortle-Schätzung,
Richtung der Lichtverschmutzung und Seeing (effektive Bildschärfe) – je Nacht und je Standort.

Messprinzip (alles aus gespeicherten Daten, Rohbilder werden nicht gebraucht):
  1. Nullpunkt: Master-Stack + WCS (wcs.json, wie die Farbkalibrierung) + Gaia-DR3-Sterne mit G und BP−RP
     (photometry.gaia_stars(with_g=True)). Aperturphotometrie auf der Luminanz (R+2G+B)/4 – genau die Gewichtung der
     Einzelbild-Analyse (quality.analysis_image, 2×2-Mittel). Gaia G → Johnson V (Riello et al. 2021), Farbterm frei,
     ausgewertet bei sonnenähnlicher Farbe → Werte vergleichbar mit einem Sky Quality Meter (≈ V-Band, ±0,2–0,3 mag).
  2. Einzelbilder: Der Stack ist auf das Referenzbild normiert (Pipeline._stack: X · kr, kr = Sternfluss Referenz/Frame).
     kr je Frame wird aus den gespeicherten Sternlisten (frames.metrics.stars) über dieselbe Dreieckszuordnung neu bestimmt
     → Nullpunkt je Aufnahme. Plus Extinktion K_EXT·Luftmasse = Gerätenullpunkt; der klarste Teil der Nacht gilt
     (oberes Perzentil), was darunter liegt, ist Dunst/Wolken.
  3. Himmel je Aufnahme: metrics.sky (Hintergrundebene, seit 0.26) bzw. metrics.bg − Objektanteil (aus dem Stack geschätzt),
     minus Bias-Pegel (Darks: 0; Stellina ohne Darks: gemessener Pegel), je Pixelfläche aus dem WCS-Maßstab.
     Zenitwert: Näherung „Helligkeit ∝ Luftmasse, gedämpft durch Extinktion“ (van Rhijn); Ortswert nur aus Aufnahmen
     ≥ 30° Höhe, Sonne < −18°, Mond unter dem Horizont, ohne Wolken.
  4. Lichtrichtung: Hintergrundebene je Aufnahme (metrics.bgplane) → mit WCS, Feldrotation (Registrierungsmatrix) und
     Az/Alt in Himmelsrichtungen; über viele Blickrichtungen eines Standorts wird die Richtung der Lichtglocke angepasst.
  5. Seeing: metrics.fwhm (Vollauflösungs-Pixel) × Maßstab. Bei Smart-Teleskopen stecken Optik, Fokus und Nachführung mit
     drin – darum „effektive Bildschärfe“.
Standorte sind privat: gespeichert werden nur Ort-ID/Name aus der Ortsliste, keine Koordinaten.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import threading
import time
import warnings
from pathlib import Path

import numpy as np

SKY_VERSION = 1
K_EXT = 0.20            # Extinktion (mag je Luftmasse), breitbandig, typischer Wert für Mitteleuropa
MIN_ALT = 30.0          # Ortswert nur aus Aufnahmen über 30° Höhe
CLOUD_LOSS = 0.35       # Sternlicht so viel schwächer als in den klarsten Aufnahmen der Nacht -> Dunst/Wolken
SYS_ERR = 0.2           # systematische Unsicherheit (Bandpass Farbsensor ↔ SQM, Eichung, Zenitnäherung)
MIN_GOOD = 8            # so viele ungestörte Aufnahmen braucht eine Nacht für einen Ortswert
GMAG_MAX = 14.5
# Himmel aus dem Photonenrauschen (Geräte ohne Darks): Rauschen² = Ausleserauschen² + k · Himmel (alles in ADU der
# Analyse-Luminanz). Grund: Die Stellina verschiebt ihren Schwarzpunkt zwischen Aufnahmen (gleiche Nacht, gleiches
# Rauschen, Pegel 680 ↔ 866 ADU; Darks 772) – der Pegel taugt nicht, das Rauschen schon.
# k: Photonen-Transfer, gemessen an ~30 000 Stellina-Aufnahmen aus 30 Nächten: Steigung Rauschen² über Pegel je Nacht
# 12,9 (Median, r ≈ 0,99) → 0,019 e⁻/ADU. Wird je Objekt aus den eigenen Aufnahmen nachgemessen (fit_gain).
# Ausleserauschen: ~1,6 e⁻ (IMX178 bei hoher Verstärkung) → 42 ± 3 ADU; Grenzen: Darks bei 29 °C 49,3 (mit
# Dunkelstrom), dunkelste Aufnahme 47,1 (Himmel kann nicht dunkler als ~22 mag/″² sein → höchstens 45).
NOISE_MODEL = {"stellina": {200.0: {"k": 12.9, "read": 42.0, "read_err": 3.0}}}
# Bortle-Klassen nach Himmelshelligkeit (Untergrenze mag/″², gebräuchliche Zuordnung; Übergänge sind fließend)
BORTLE = ((21.7, 1), (21.5, 2), (21.3, 3), (20.4, 4), (19.1, 5), (18.5, 6), (18.0, 7), (17.5, 8))
BORTLE_LABEL = {1: "exzellent dunkel", 2: "typisch dunkel", 3: "ländlich", 4: "Übergang ländlich/Vorort", 5: "Vorort",
                6: "heller Vorort", 7: "Übergang Vorort/Stadt", 8: "Stadt", 9: "Innenstadt"}
FLAG_TEXT = {"twilight": "Dämmerung", "moon": "Mond über dem Horizont", "low": "Objekt tiefer als 30°",
             "cloud": "Dunst/Wolken", "nosky": "Hintergrund nicht messbar", "nosite": "Standort unbekannt"}


# --------------------------------------------------------------- Grundlagen ---
def gaia_v(g: np.ndarray, bprp: np.ndarray) -> np.ndarray:
    """Johnson V aus Gaia G und BP−RP (Riello et al. 2021, gültig −0,5 < BP−RP < 2,75)."""
    x = np.clip(bprp, -0.5, 2.75)
    return g - (-0.02704 + 0.01424 * x - 0.2156 * x ** 2 + 0.01426 * x ** 3)


def airmass(alt) -> np.ndarray:
    """Luftmasse nach Kasten & Young (1989)."""
    h = np.clip(np.asarray(alt, float), 1.0, 90.0)
    return 1.0 / (np.sin(np.radians(h)) + 0.50572 * (h + 6.07995) ** -1.6364)


def zenith_offset(alt) -> np.ndarray:
    """mag, um die der Himmel im Zenit dunkler ist als in Höhe alt (Leuchten ∝ Luftmasse, durch Extinktion gedämpft)."""
    X = airmass(alt)
    return 2.5 * np.log10(X) - K_EXT * (X - 1)


def bortle(sqm: float | None, err: float = SYS_ERR) -> dict | None:
    """Bortle-Klasse mit Spanne aus der Unsicherheit – keine Scheingenauigkeit."""
    if sqm is None or not np.isfinite(sqm):
        return None

    def cls(v):
        return next((c for lim, c in BORTLE if v >= lim), 9)
    c, lo, hi = cls(sqm), cls(sqm + err), cls(sqm - err)
    return {"class": c, "range": [lo, hi], "label": BORTLE_LABEL[c],
            "text": f"Bortle {c}" if lo == hi else f"Bortle {lo}–{hi}"}


def luma(img: np.ndarray) -> np.ndarray:
    """Luminanz wie die Einzelbild-Analyse (2×2-Mittel einer RGGB-Matrix = (R+2G+B)/4)."""
    if img.ndim == 3 and img.shape[0] == 3:
        return 0.25 * img[0] + 0.5 * img[1] + 0.25 * img[2]
    return img[0] if img.ndim == 3 else img


def _clip_fit(A: np.ndarray, z: np.ndarray, hi: float = 2.5, lo: float = 3.0, it: int = 6):
    """Robuste lineare Anpassung: helle Ausreißer (Objekt, Sterne, Glühen) stärker verwerfen als dunkle."""
    use = np.isfinite(z)
    coef = np.zeros(A.shape[1])
    for _ in range(it):
        if use.sum() < A.shape[1] + 2:
            break
        coef = np.linalg.lstsq(A[use], z[use], rcond=None)[0]
        res = z - A @ coef
        s = 1.4826 * np.median(np.abs(res[use])) + 1e-9
        new = np.isfinite(z) & (res < hi * s) & (res > -lo * s)
        if (new == use).all():
            break
        use = new
    return coef, use


def bg_plane(img: np.ndarray, s: int = 1, box: int = 48, edge: float = 0.06) -> dict:
    """Hintergrundebene eines Analysebilds: Himmel in der Bildmitte (ADU je Pixel, ohne Objekt/Sterne) und Gefälle
    [x, y] in ADU je Vollauflösungs-Pixel. Kacheln am äußersten Rand (Glühen) zählen nicht."""
    h, w = img.shape
    bh, bw = max(3, h // box), max(3, w // box)
    ch, cw = h // bh, w // bw
    med = np.median(img[: bh * ch, : bw * cw].reshape(bh, ch, bw, cw).swapaxes(1, 2).reshape(bh, bw, -1), axis=2)
    yc, xc = (np.arange(bh) + 0.5) * ch - h / 2, (np.arange(bw) + 0.5) * cw - w / 2
    Y, X = np.meshgrid(yc, xc, indexing="ij")
    keep = (np.abs(X) < w * (0.5 - edge)) & (np.abs(Y) < h * (0.5 - edge))
    A = np.c_[np.ones(keep.sum()), X[keep], Y[keep]]
    coef, use = _clip_fit(A, med[keep].astype(float))
    return {"sky": round(float(coef[0]), 2), "bgplane": [round(float(coef[1]) / s, 5), round(float(coef[2]) / s, 5)]}


def stack_excess(lum: np.ndarray, cov: np.ndarray, box: int = 64) -> float:
    """Wie weit der Median eines (hintergrundbereinigten) Stacks über dem Himmel liegt – Objektlicht im Bildmedian.
    Ältere Aufnahmen kennen nur metrics.bg (Median) – davon wird dieser Anteil abgezogen."""
    good = cov > 0.9
    if good.sum() < 1000:
        return 0.0
    tiles = bg_plane(np.where(good, lum, np.nan), 1, box)          # NaN-Kacheln fallen aus der Anpassung
    sky = tiles["sky"]
    if not np.isfinite(sky):
        return 0.0
    return max(0.0, float(np.nanmedian(lum[good])) - sky)


def calibrated(calib: str | None) -> bool:
    """Mit Darks/Bias kalibriert: der Hintergrundpegel ist dann direkt der Himmel."""
    return bool(calib and ("Dark" in calib or "Bias" in calib))


def noise_model(device_id: str | None, gain) -> dict | None:
    """Rauschmodell des Geräts (k, Ausleserauschen) für Aufnahmen ohne Darks; unbekannt -> None."""
    tab = NOISE_MODEL.get(device_id or "")
    try:
        g = float(gain)
    except (TypeError, ValueError):
        return None
    return next((dict(v) for k, v in (tab or {}).items() if abs(k - g) < 0.5), None)


def fit_gain(frames: list[dict]) -> float | None:
    """Photonen-Transfer aus den eigenen Aufnahmen: Steigung Rauschen² über Hintergrundpegel je Nacht (Median der Nächte
    mit genug Helligkeitsumfang – Dämmerung, Mond). Der Schwarzpunkt darf dabei springen, solange er meist gleich bleibt."""
    by: dict[str, list] = {}
    for f in frames:
        m = f["metrics"]
        if m.get("bg") is not None and m.get("noise"):
            by.setdefault(f["night"], []).append((float(m["bg"]), float(m["noise"]) ** 2))
    ks = []
    for v in by.values():
        a = np.array(v)
        if len(a) < 40 or np.ptp(a[:, 0]) < 200:
            continue
        if np.corrcoef(a[:, 0], a[:, 1])[0, 1] < 0.95:
            continue
        ks.append(np.polyfit(a[:, 0], a[:, 1], 1)[0])
    return float(np.median(ks)) if ks else None


# --------------------------------------------------------------- Nullpunkt ---
def zero_point(mean: np.ndarray, cov: np.ndarray, hdr: dict, cat: np.ndarray, exptime: float) -> dict:
    """Photometrischer Nullpunkt des Stacks (V-Band-nah) für 1 ADU/s auf der Luminanz. cat: (N,4) RA, Dec, BP−RP, G."""
    from scipy.spatial import cKDTree

    from .photometry import _fluxes, wcs_from
    from .quality import detect_stars

    lum = luma(mean)
    H, W = lum.shape
    if len(cat) < 15:
        raise ValueError("zu wenige Gaia-Sterne im Feld")
    w = wcs_from(hdr)
    px, py = w.world_to_pixel_values(cat[:, 0], cat[:, 1])
    st = detect_stars(lum, max_stars=1500, thresh=6)
    if len(st) < 15:
        raise ValueError("zu wenige Sterne im Stack")
    fwhm = float(np.median(st.fwhm[: min(len(st), 80)]))
    r = max(3.0, 2.0 * fwhm)
    m = 3 * r
    inside = (px > m) & (py > m) & (px < W - m) & (py < H - m)
    # isolierte Sterne: kein anderer Gaia-Stern mit mehr als 1/10 des Lichts innerhalb des Rings
    tree = cKDTree(np.c_[px, py])
    iso = np.ones(len(cat), bool)
    for i, nb in enumerate(tree.query_ball_point(np.c_[px, py], 2.4 * r)):
        if any(j != i and cat[j, 3] < cat[i, 3] + 2.5 for j in nb):
            iso[i] = False
    sel = np.flatnonzero(inside & iso & (cat[:, 3] > 7.5))
    if len(sel) < 15:
        raise ValueError("zu wenige isolierte Gaia-Sterne")
    d, j = cKDTree(np.c_[st.x, st.y]).query(np.c_[px[sel], py[sel]], distance_upper_bound=max(3.0, fwhm))
    ok = np.isfinite(d)
    sel, j = sel[ok], j[ok]
    fl, peak = _fluxes(lum[None], st.x[j], st.y[j], r)
    fl, peak = fl[:, 0], peak
    cv = cov[np.clip(np.round(st.y[j]).astype(int), 0, H - 1), np.clip(np.round(st.x[j]).astype(int), 0, W - 1)]
    top = float(np.nanpercentile(lum, 99.999))
    good = np.isfinite(fl) & (fl > 0) & (cv > 0.9) & (peak < 0.6 * top) & (cat[sel, 2] > -0.3) & (cat[sel, 2] < 2.5)
    if good.sum() < 15:
        raise ValueError(f"nur {int(good.sum())} brauchbare Sterne für den Nullpunkt")
    x = cat[sel[good], 2]
    v = gaia_v(cat[sel[good], 3], x)
    z = v + 2.5 * np.log10(fl[good] / max(exptime, 1e-3))
    A = np.c_[np.ones(len(z)), x - 0.82]
    coef, use = _clip_fit(A, z, hi=3.0, lo=3.0)
    res = z - A @ coef
    s = 1.4826 * float(np.median(np.abs(res[use])))
    return {"zp": float(coef[0]), "color": round(float(coef[1]), 3), "stars": int(use.sum()), "scatter": round(s, 3),
            "aperture_px": round(r, 1), "err": round(s / math.sqrt(max(1, use.sum())), 3)}


def frame_links(meta: dict, frames: list[dict]) -> dict:
    """Je Aufnahme (Flussverhältnis kr zum Referenzbild, Matrix Frame→Referenz) aus den gespeicherten Sternlisten –
    dieselbe Zuordnung wie beim Stacken (Pipeline._stack)."""
    from .registration import find_transform

    ref_xy = np.array([[s[0], s[1]] for s in meta.get("ref_stars") or []], float)
    ref_flux = np.array([s[2] for s in meta.get("ref_stars") or []], float)
    out = {}
    if len(ref_xy) < 5:
        return out
    for f in frames:
        stars = f["metrics"].get("stars") or []
        if f["id"] == meta.get("ref_fid"):
            out[f["id"]] = (1.0, np.eye(3))
            continue
        if len(stars) < 8:
            continue
        tg = np.array([[s[0], s[1]] for s in stars], float)
        tf = np.array([s[2] for s in stars], float)
        try:
            res = find_transform(ref_xy, tg)
        except Exception:
            res = None
        if res is None:
            continue
        M, pairs, _ = res
        ratio = ref_flux[pairs[:, 1]] / np.maximum(tf[pairs[:, 0]], 1e-6)
        if len(ratio) >= 5:
            out[f["id"]] = (float(np.median(ratio)), M)
    return out


# ------------------------------------------------------------- Ephemeriden ---
def _iers():
    from astropy.utils import iers
    return iers.conf.set_temp("auto_download", False), iers.conf.set_temp("auto_max_age", None)


def ephemeris(times: list[dt.datetime], ra: float, dec: float, site: dict) -> dict:
    """Sonne, Mond, Höhe/Azimut der Bildmitte zu allen Aufnahmezeiten (offline, vektoriell)."""
    from astropy import units as u
    from astropy.coordinates import AltAz, EarthLocation, SkyCoord, get_body
    from astropy.time import Time
    t = Time(times)
    a, b = _iers()
    with warnings.catch_warnings(), a, b:
        warnings.simplefilter("ignore")
        loc = EarthLocation(lat=site["lat"] * u.deg, lon=site["lon"] * u.deg, height=float(site.get("alt_m") or 0) * u.m)
        frame = AltAz(obstime=t, location=loc)
        sun, moon = get_body("sun", t, loc), get_body("moon", t, loc)
        aa = SkyCoord(np.broadcast_to(ra, len(t)), np.broadcast_to(dec, len(t)), unit="deg").transform_to(frame)
        return {"sun_alt": sun.transform_to(frame).alt.deg, "moon_alt": moon.transform_to(frame).alt.deg,
                "moon_illum": (1 - np.cos(sun.separation(moon).rad)) / 2, "alt": aa.alt.deg, "az": aa.az.deg}


def orientation(w, links: list, shape: tuple, times: list, site: dict, d: float = 0.2, sc: float = 1.0):
    """Richtung „nach oben“ (zum Zenit) und „nach rechts“ (wachsendes Azimut) je Aufnahme in Frame-Pixeln je Grad,
    dazu Az/Alt der Bildmitte. links: Matrizen Frame→Referenz (Feldrotation), w: WCS des Stacks (Referenzraster)."""
    from astropy import units as u
    from astropy.coordinates import AltAz, EarthLocation, SkyCoord
    from astropy.time import Time
    H, W = shape
    c = np.array([(W - 1) / 2, (H - 1) / 2, 1.0])
    Ms = np.array(links)
    cref = Ms @ c
    to_stack = lambda v: (v + 0.5) * sc - 0.5               # Referenzraster (nativ) -> Stack-Raster (resolution_scale)
    ra0, dec0 = w.pixel_to_world_values(to_stack(cref[:, 0]), to_stack(cref[:, 1]))
    a, b = _iers()
    with warnings.catch_warnings(), a, b:
        warnings.simplefilter("ignore")
        loc = EarthLocation(lat=site["lat"] * u.deg, lon=site["lon"] * u.deg, height=float(site.get("alt_m") or 0) * u.m)
        frame = AltAz(obstime=Time(times), location=loc)
        aa = SkyCoord(ra0, dec0, unit="deg").transform_to(frame)
        alt, az = aa.alt.deg, aa.az.deg
        cosa = np.maximum(np.cos(np.radians(alt)), 0.05)
        up = SkyCoord(az=az * u.deg, alt=np.minimum(alt + d, 89.99) * u.deg, frame=frame).icrs
        rt = SkyCoord(az=(az + d / cosa) * u.deg, alt=alt * u.deg, frame=frame).icrs
    inv = np.linalg.inv(Ms)

    def to_frame(sky):
        x, y = w.world_to_pixel_values(sky.ra.deg, sky.dec.deg)
        x, y = (x + 0.5) / sc - 0.5, (y + 0.5) / sc - 0.5
        p = np.einsum("nij,nj->ni", inv, np.c_[x, y, np.ones(len(x))])
        return (p[:, :2] - c[:2]) / d
    return to_frame(up), to_frame(rt), alt, az


# ------------------------------------------------------------- Lichtrichtung ---
def _bearing_to_horizon(alt, az, phi):
    """Richtungswinkel (von „oben“ zu „rechts“) vom Blickpunkt (alt, az) zum Horizontpunkt mit Azimut phi (Grad)."""
    a, dl = np.radians(alt), np.radians(phi - az)
    return np.arctan2(np.sin(dl), -np.sin(a) * np.cos(dl))


def _horizon_az(alt, az, beta):
    """Azimut, an dem ein Großkreis vom Blickpunkt in Richtung beta den Horizont trifft."""
    a = np.radians(alt)
    dist = np.arctan2(np.sin(a), -np.cos(a) * np.cos(beta))
    return (az + np.degrees(np.arctan2(np.sin(beta) * np.sin(dist) * np.cos(a), np.cos(dist)))) % 360


def _nat_slope(alt):
    """Natürliches relatives Gefälle nach oben (Anteil je Grad, negativ): Helligkeit ∝ X · 10^(−0,4·K·(X−1))."""
    def ln_b(a):
        X = airmass(a)
        return np.log(X) - 0.4 * np.log(10) * K_EXT * (X - 1)
    a = np.asarray(alt, float)
    return ln_b(a + 0.5) - ln_b(a - 0.5)


def light_direction(samples: list, n_boot: int = 60, seed: int = 1) -> dict | None:
    """Richtung der Lichtglocke aus vielen Blickrichtungen. samples: [az, alt, g_oben, g_rechts, gruppe] mit relativem
    Hintergrundgefälle (Anteil je Grad). Modell je Aufnahme: konstanter Gerätesockel (oben/rechts, z. B. Restglühen)
    + natürlicher Anstieg zum Horizont + b · Richtung zum Horizontpunkt φ. φ mit der kleinsten Restabweichung gewinnt."""
    if len(samples) < 20:
        return None
    S = np.array([s[:4] for s in samples], float)
    groups = np.array([str(s[4]) for s in samples])
    az, alt, gu, gr = S.T
    ok = np.isfinite(gu) & np.isfinite(gr) & (np.hypot(gu, gr) < 0.2)
    az, alt, gu, gr, groups = az[ok], alt[ok], gu[ok], gr[ok], groups[ok]
    if len(az) < 20 or len(set(groups)) < 2:
        return None
    spread = _az_spread(az)
    phis = np.arange(0, 360, 3.0)
    nat = _nat_slope(alt)

    def fit(idx):
        n = len(idx)
        z = np.r_[gu[idx], gr[idx]]
        base = np.zeros((2 * n, 3))
        base[:n, 0], base[n:, 1], base[:n, 2] = 1, 1, nat[idx]
        c0 = np.linalg.lstsq(base, z, rcond=None)[0]
        ss0 = float(((z - base @ c0) ** 2).sum())
        best = (np.inf, None, 0.0, c0)
        for phi in phis:
            beta = _bearing_to_horizon(alt[idx], az[idx], phi)
            A = np.c_[base, np.r_[np.cos(beta), np.sin(beta)]]
            c = np.linalg.lstsq(A, z, rcond=None)[0]
            if c[3] <= 0:
                continue
            ss = float(((z - A @ c) ** 2).sum())
            if ss < best[0]:
                best = (ss, phi, float(c[3]), c)
        return best, ss0

    (ss, phi, b, coef), ss0 = fit(np.arange(len(az)))
    if phi is None:
        return None
    gain = 1 - ss / max(ss0, 1e-12)
    rng = np.random.default_rng(seed)
    ug = sorted(set(groups))
    boot = []
    for _ in range(n_boot):
        pick = rng.choice(ug, len(ug))
        idx = np.concatenate([np.flatnonzero(groups == g) for g in pick])
        (s2, p2, _, _), _ = fit(idx)
        if p2 is not None:
            boot.append(p2)
    circ = _circ_std(np.array(boot)) if len(boot) >= 10 else 180.0
    # Windrose: Restgefälle (ohne Sockel und natürlichen Anstieg) als Horizontrichtung, nach Stärke gewichtet
    ru, rr = gu - coef[0] - coef[2] * nat, gr - coef[1]
    hit = _horizon_az(alt, az, np.arctan2(rr, ru))
    rose = np.bincount(((hit + 11.25) % 360 // 22.5).astype(int), weights=np.hypot(ru, rr), minlength=16)[:16]
    rose = rose / rose.max() if rose.max() > 0 else rose
    reliable = len(ug) >= 3 and spread >= 60 and circ <= 45 and gain >= 0.05
    return {"az": round(float(phi)), "spread_deg": round(float(circ)), "strength": round(b * 10 * 100, 2),
            "explained": round(float(gain), 3), "samples": int(len(az)), "groups": len(ug), "az_coverage": round(float(spread)),
            "reliable": bool(reliable), "rose": [round(float(v), 3) for v in rose], "compass": compass(phi)}


def _az_spread(az: np.ndarray) -> float:
    """Wie viel vom Horizont die Blickrichtungen abdecken (Grad, größte Lücke abgezogen)."""
    a = np.sort(np.asarray(az) % 360)
    if len(a) < 2:
        return 0.0
    gaps = np.diff(np.r_[a, a[0] + 360])
    return float(360 - gaps.max())


def _circ_std(deg: np.ndarray) -> float:
    r = np.hypot(np.cos(np.radians(deg)).mean(), np.sin(np.radians(deg)).mean())
    return float(np.degrees(np.sqrt(max(0.0, -2 * np.log(max(r, 1e-9))))))


def compass(az: float) -> str:
    names = ("N", "NNO", "NO", "ONO", "O", "OSO", "SO", "SSO", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW")
    return names[int((az % 360 + 11.25) // 22.5) % 16]


# ------------------------------------------------------------ Auswertung ---
def _parse(t) -> dt.datetime | None:
    try:
        x = dt.datetime.fromisoformat(str(t).replace("Z", ""))
        return x.replace(tzinfo=dt.timezone.utc) if x.tzinfo is None else x.astimezone(dt.timezone.utc)
    except (TypeError, ValueError):
        return None


def evaluate(frames: list[dict], *, zp_stack: float | None, zp_device: float | None, flux_k0: float, links: dict,
             excess: float, scale: float, site: dict | None, ra: float, dec: float, wcs=None, shape=None,
             neutral: bool = True, device_id: str | None = None, gain_k: float | None = None, stack_scale: float = 1.0) -> dict:
    """Kennzahlen je Nacht aus den Einzelbildern eines Objekts. frames: {id, night, date, exptime, gain, metrics, status}.
    zp_stack: Nullpunkt des Stacks (1 ADU/s), sonst zp_device (Gerätenullpunkt aus anderen Nächten); gain_k: gemessener
    Photonen-Transfer (fit_gain), sonst Gerätewert."""
    rows = [f for f in frames if _parse(f.get("date")) and f["metrics"].get("bg") is not None]
    rows.sort(key=lambda f: _parse(f["date"]))
    out: dict[str, dict] = {}
    if not rows:
        return out
    times = [_parse(f["date"]) for f in rows]
    eph = ephemeris(times, ra, dec, site) if site else None
    area = scale * scale
    zp_f = np.full(len(rows), np.nan)
    for i, f in enumerate(rows):
        if zp_stack is not None and f["id"] in links:
            zp_f[i] = zp_stack + 2.5 * math.log10(flux_k0 / links[f["id"]][0])
    alt = eph["alt"] if eph else np.full(len(rows), np.nan)
    X = airmass(alt) if eph else np.ones(len(rows))
    zp_true = zp_f + K_EXT * X
    # Richtungsdaten (Hintergrundebene) für Aufnahmen mit Ebene, Lage und Standort
    dir_idx = [i for i, f in enumerate(rows) if f["metrics"].get("bgplane") and f["id"] in links]
    ori = {}
    if eph is not None and wcs is not None and shape and dir_idx:
        try:
            up, rt, a2, z2 = orientation(wcs, [links[rows[i]["id"]][1] for i in dir_idx], shape, [times[i] for i in dir_idx], site,
                                         sc=stack_scale)
            ori = {i: (up[k], rt[k], a2[k], z2[k]) for k, i in enumerate(dir_idx)}
        except Exception:
            ori = {}
    nights: dict[str, list[int]] = {}
    for i, f in enumerate(rows):
        nights.setdefault(f["night"] or "?", []).append(i)
    for night, idx in nights.items():
        idx = np.array(idx)
        zt = zp_true[idx]
        if np.isfinite(zt).sum() >= 5:
            zp_night = float(np.nanpercentile(zt[np.isfinite(zt)], 85))
            zsrc = "stack"
        elif zp_device is not None:
            zp_night, zsrc = float(zp_device), "device"
        else:
            zp_night, zsrc = None, None
        # ganze Nacht dunstig: selbst die klarsten Aufnahmen bleiben deutlich unter dem Gerätenullpunkt anderer Nächte
        hazy = bool(zsrc == "stack" and zp_device is not None and zp_device - zp_night > CLOUD_LOSS)
        if hazy:
            zp_night = float(zp_device)
        pts, good, good_err, seeing, dirs, methods = [], [], [], [], [], set()
        for i in idx:
            f, m = rows[i], rows[i]["metrics"]
            t_exp = float(f.get("exptime") or 0) or None
            sky = s_err = None
            if calibrated(m.get("calib")):                # Darks abgezogen: Pegel = Himmel (+ Objekt im Median)
                if m.get("sky") is not None:
                    sky = float(m["sky"])
                else:
                    k = links.get(f["id"], (flux_k0, None))[0]
                    sky = float(m["bg"]) - excess * flux_k0 / max(k, 1e-6)
                methods.add("level")
            elif (nm := noise_model(device_id, f.get("gain"))) and m.get("noise"):
                k = gain_k or nm["k"]                       # ohne Darks: Himmel aus dem Photonenrauschen
                sky = (float(m["noise"]) ** 2 - nm["read"] ** 2) / k
                s_err = 2 * nm["read"] * nm["read_err"] / k
                methods.add("noise")
            flags = [] if eph is not None else ["nosite"]   # ohne Ort keine Dämmerungs-/Mond-/Höhenprüfung
            if eph is not None:
                if eph["sun_alt"][i] > -18:
                    flags.append("twilight")
                if eph["moon_alt"][i] > 0 and eph["moon_illum"][i] >= 0.05:
                    flags.append("moon")
                if alt[i] < MIN_ALT:
                    flags.append("low")
            loss = None
            if np.isfinite(zp_true[i]) and zsrc == "stack":
                loss = zp_night - float(zp_true[i])
                if loss > CLOUD_LOSS or hazy:
                    flags.append("cloud")
            sb = sbz = None
            if zp_night is not None and sky is not None and sky > 1 and t_exp and area > 0:
                sb = zp_night - 2.5 * math.log10(sky / t_exp / area)
                sbz = sb + float(zenith_offset(alt[i])) if eph is not None else sb
            else:
                flags.append("nosky")
            fw = m.get("fwhm")
            fw_as = float(fw) * scale if fw and (m.get("n_stars") or 0) >= 8 else None
            if fw_as:
                seeing.append(fw_as)
            if sbz is not None and not flags:
                good.append(sbz)
                good_err.append(1.0857 * s_err / sky if s_err else 0.0)
            pts.append({"t": times[i].strftime("%H:%M"), "ts": round(times[i].timestamp()), "sqm": _r(sbz, 2), "sqm_alt": _r(sb, 2),
                        "seeing": _r(fw_as, 2), "alt": _r(alt[i], 1) if eph is not None else None, "flags": flags,
                        "loss": _r(loss, 2)})
            o = ori.get(i)
            if o is not None and neutral and sky and sky > 1 and not ({"twilight", "moon", "cloud"} & set(flags)) and alt[i] >= 20:
                g = np.array(m["bgplane"], float)
                dirs.append([round(float(o[3]), 2), round(float(o[2]), 2), round(float(g @ o[0]) / sky, 6),
                             round(float(g @ o[1]) / sky, 6)])
        allv = [p["sqm"] for p in pts if p["sqm"] is not None]
        sqm = float(np.median(good)) if len(good) >= MIN_GOOD else None
        reasons: dict[str, int] = {}
        for p in pts:
            for fl in p["flags"]:
                reasons[fl] = reasons.get(fl, 0) + 1
        # Unsicherheit: Eichung/Bandpass + (Rauschmethode) Ausleserauschen, das bei dunklem Himmel stärker durchschlägt
        err = math.hypot(SYS_ERR if zsrc == "stack" else SYS_ERR + 0.1, float(np.median(good_err)) if good_err else 0.0)
        n = {"night": night, "frames": len(idx), "good": len(good), "zp": _r(zp_night, 3), "zp_source": zsrc, "hazy": hazy,
             "sqm": _r(sqm, 2), "sqm_best": _r(float(np.percentile(good, 90)), 2) if len(good) >= MIN_GOOD else None,
             "sqm_all": _r(float(np.median(allv)), 2) if allv else None,
             "err": round(err, 2), "bortle": bortle(sqm, err) if sqm is not None else None,
             "method": "+".join(sorted(methods)) or None, "gain_k": _r(gain_k, 2),
             "site_value": sqm is not None,
             "reasons": {k: v for k, v in sorted(reasons.items(), key=lambda kv: -kv[1])},
             "seeing": _r(float(np.median(seeing)), 2) if seeing else None,
             "seeing_best": _r(float(np.percentile(seeing, 10)), 2) if len(seeing) >= 5 else None,
             "transparency": _r(float(np.nanmedian([p["loss"] for p in pts if p["loss"] is not None])), 2)
             if any(p["loss"] is not None for p in pts) else None,
             "start": times[idx[0]].isoformat(), "end": times[idx[-1]].isoformat(),
             "curve": _thin(pts, 160), "dirs": dirs[:: max(1, len(dirs) // 200)]}
        if not n["site_value"] and allv:
            n["note"] = _note(reasons, len(idx))
        out[night] = n
    return out


def _note(reasons: dict, n: int) -> str:
    top = [FLAG_TEXT[k] for k, v in reasons.items() if v >= 0.3 * n and k in FLAG_TEXT]
    return ("Kein Ortswert: " + ", ".join(top[:3])) if top else "Kein Ortswert: zu wenige ungestörte Aufnahmen"


def _r(v, nd):
    return None if v is None or not np.isfinite(v) else round(float(v), nd)


def _thin(pts: list, n: int) -> list:
    if len(pts) <= n:
        return pts
    idx = np.unique(np.linspace(0, len(pts) - 1, n).round().astype(int))
    return [pts[i] for i in idx]


# ---------------------------------------------------------------- Ablage ---
SCHEMA = """CREATE TABLE IF NOT EXISTS sky_nights(
  project_id INTEGER, night TEXT, site_id TEXT, site_name TEXT, device TEXT, updated REAL, version INTEGER, data TEXT,
  PRIMARY KEY(project_id, night))"""


def ensure_table(db) -> None:
    with db.conn() as c:
        c.execute(SCHEMA)


def project_rows(db, pid: int) -> list[dict]:
    out = []
    for x in db.q("SELECT id, night, status, info, metrics FROM frames WHERE project_id=? AND kind='light' AND metrics IS NOT NULL", (pid,)):
        i, m = json.loads(x["info"] or "{}"), json.loads(x["metrics"] or "{}")
        if not m or m.get("n_stars") is None:
            continue
        out.append({"id": x["id"], "night": x["night"], "status": x["status"], "date": i.get("date_obs"),
                    "exptime": i.get("exptime"), "gain": i.get("gain"), "instrument": i.get("instrument"),
                    "shape": i.get("shape"), "site": i.get("site"), "metrics": m})
    return out


def device_key(instrument, gain) -> str:
    return f"{instrument or '?'}|{gain}"


def device_zp(db) -> dict:
    """Gerätenullpunkte (Median über Nächte mit eigenem Gaia-Nullpunkt) – Rückfall für Nächte ohne Plate-Solving."""
    vals: dict[str, list] = {}
    for r in db.q("SELECT s.device, s.data FROM sky_nights s JOIN projects p ON p.id=s.project_id"):
        d = json.loads(r["data"] or "{}")
        if d.get("zp_source") == "stack" and d.get("zp") is not None:
            vals.setdefault(r["device"], []).append(d["zp"])
    return {k: float(np.median(v)) for k, v in vals.items() if len(v) >= 2}


def _site_of(rows: list[dict], night: str, site_for) -> dict | None:
    hdr = [tuple(r["site"]) for r in rows if r["night"] == night and r.get("site")]
    if hdr:
        lat, lon = max(set(hdr), key=hdr.count)
        return {"id": f"fits:{lat:.2f},{lon:.2f}", "name": "Aufnahmestandort (FITS)", "lat": lat, "lon": lon, "alt_m": 0}
    if site_for:
        try:
            return site_for(night=night)
        except Exception:
            return None
    return None


def compute_project(pl, pid: int, site_for=None, log=lambda m: None, backfill: bool = True) -> dict:
    """Himmelsqualität eines Objekts berechnen und je Nacht speichern. pl: Pipeline (Master-Stack, WCS, Gaia-Einstellung)."""
    from . import photometry, tutor
    db = pl.db
    ensure_table(db)
    proj = db.one("SELECT * FROM projects WHERE id=?", (pid,))
    if not proj:
        raise KeyError(pid)
    rows = project_rows(db, pid)
    if not rows:
        return {"project": pid, "nights": 0, "reason": "keine analysierten Aufnahmen"}
    device = tutor.detect_device([r["instrument"] for r in rows], tuple(rows[0]["shape"]) if rows[0].get("shape") else None)
    states = pl._master(pid)
    st = states.get(None) or (next(iter(states.values())) if states else None)
    info: dict = {"device": device["id"]}
    zp_stack, wcs, hdr, excess, scale, shape = None, None, None, 0.0, None, None
    flux_k0 = 1.0
    links: dict = {}
    sc = 1.0                                            # resolution_scale: Stack-Pixel = native Pixel / sc
    if st is not None:
        mean, cov = st.result()
        flux_k0 = float(st.meta.get("flux_k0") or 1.0)
        sc = float(st.meta.get("scale") or 1)
        shape = tuple(st.meta["shape"][1:])
        excess = stack_excess(luma(mean), cov)
        try:
            f = pl.s.work_dir / f"p{pid}" / "wcs.json"
            key = f"{st.meta.get('ref_fid')}|{mean.shape[1]}x{mean.shape[2]}"
            cached = json.loads(f.read_text()) if f.exists() else {}
            hdr = cached["wcs"] if cached.get("key") == key else pl._stack_wcs(proj, st, mean, log)
            wcs = photometry.wcs_from(hdr)
            scale = float(np.sqrt(abs(np.linalg.det(wcs.pixel_scale_matrix)))) * 3600 * sc   # ″ je nativem Pixel (FWHM, Fläche)
        except Exception as e:
            info["wcs_error"] = str(e)[:160]
        if wcs is not None and pl.s.gaia:
            try:
                H, W = mean.shape[1:]
                ra, dec = (float(v) for v in wcs.pixel_to_world_values((W - 1) / 2, (H - 1) / 2))
                log("Gaia-Helligkeiten abfragen")
                cat = photometry.gaia_stars(ra, dec, 0.5 * scale / sc / 3600 * math.hypot(W, H), pl.s.work_dir / "gaia",
                                            gmag_max=GMAG_MAX, with_g=True)
                exps = [float(r["exptime"]) for r in rows if r.get("exptime")]
                zp = zero_point(mean, cov, hdr, cat, float(np.median(exps)) if exps else 1.0)
                # hochgerechneter Stack: Sternfluss verteilt sich auf sc² Pixel gleicher Höhe -> Fluss ×sc²
                zp_stack = zp["zp"] - 5 * math.log10(sc)
                info["zp_fit"] = zp
            except Exception as e:
                info["zp_error"] = str(e)[:160]
        if zp_stack is not None or wcs is not None:
            log("Aufnahmen dem Stack zuordnen")
            links = frame_links(st.meta, rows)
    if scale is None:                                   # ohne WCS: Maßstab aus dem Plate-Solving der Aufnahmegruppe
        solve = next((json.loads(x["info"]).get("solve") for x in db.q(
            "SELECT info FROM frames WHERE project_id=? AND json_extract(info,'$.solve.scale') IS NOT NULL LIMIT 1", (pid,))), None)
        scale = float(solve["scale"]) if solve and solve.get("scale") else None
    if scale is None:
        return {"project": pid, "nights": 0, "reason": "Bildmaßstab unbekannt (kein Plate-Solving)"}
    if backfill:
        _backfill(pl, rows, log)
    dzp = device_zp(db).get(device_key(rows[0]["instrument"], rows[0]["gain"]))
    neutral = tutor.neutral_sky(proj["ra"], proj["dec"], proj["kind"])
    ra, dec = (float(v) for v in wcs.pixel_to_world_values((shape[1] * sc - 1) / 2, (shape[0] * sc - 1) / 2)) if wcs is not None and shape \
        else (proj["ra"], proj["dec"])
    if ra is None or dec is None:
        return {"project": pid, "nights": 0, "reason": "Himmelsposition unbekannt"}
    nm = noise_model(device["id"], rows[0].get("gain"))
    k_fit = fit_gain(rows)
    gain_k = k_fit if nm and k_fit and abs(k_fit / nm["k"] - 1) < 0.3 else None   # Ausreißer: Gerätewert
    by_night: dict[str, list] = {}
    for r in rows:
        by_night.setdefault(r["night"], []).append(r)
    saved = 0
    now = time.time()
    for night, fr in by_night.items():
        site = _site_of(rows, night, site_for)
        res = evaluate(fr, zp_stack=zp_stack, zp_device=dzp, flux_k0=flux_k0, links=links, excess=excess, scale=scale,
                       site=site, ra=ra, dec=dec, wcs=wcs, shape=shape, neutral=neutral, device_id=device["id"],
                       gain_k=gain_k, stack_scale=sc)
        for nk, n in res.items():
            n.update(scale=round(scale, 3), neutral=neutral, device=device["name"], object=proj["name"],
                     zp_fit=info.get("zp_fit"), zp_error=info.get("zp_error"), wcs_error=info.get("wcs_error"),
                     site_name=(site or {}).get("name"), site_source=(site or {}).get("source"))
            db.x("INSERT OR REPLACE INTO sky_nights(project_id, night, site_id, site_name, device, updated, version, data) "
                 "VALUES(?,?,?,?,?,?,?,?)", (pid, nk, (site or {}).get("id"), (site or {}).get("name"),
                                              device_key(rows[0]["instrument"], rows[0]["gain"]), now, SKY_VERSION, json.dumps(n)))
            saved += 1
    return {"project": pid, "nights": saved, **{k: v for k, v in info.items() if k != "device"}}


def _backfill(pl, rows: list[dict], log, per_night: int = 40) -> int:
    """Hintergrundebene für ältere Aufnahmen nachtragen, deren Rohbild noch erreichbar ist (≤ per_night je Nacht)."""
    from concurrent.futures import ThreadPoolExecutor

    from . import calibration
    from .quality import analysis_image
    todo = []
    by_night: dict[str, list] = {}
    for r in rows:
        if "bgplane" not in r["metrics"]:
            by_night.setdefault(r["night"], []).append(r)
    paths = {x["id"]: (x["path"], x["info"], x["deleted"]) for x in pl.db.q(
        f"SELECT id, path, info, deleted FROM frames WHERE id IN ({','.join('?' * len(rows))})", tuple(r["id"] for r in rows))} if rows else {}
    for night, fr in by_night.items():
        have = [r for r in fr if r["id"] in paths and not paths[r["id"]][2] and paths[r["id"]][0] and Path(paths[r["id"]][0]).exists()]
        todo += have[:: max(1, len(have) // per_night)][:per_night]
    if not todo:
        return 0
    log(f"Hintergrundebene für {len(todo)} ältere Aufnahmen nachmessen")
    lib, cache = pl._library(), {}
    glib = calibration.GlowLibrary(pl.s.work_dir / "glow")
    for r in todo:                                     # Glühmodell wie beim Stacken (nur gespeicherte Bibliothek)
        info = json.loads(paths[r["id"]][1])
        k = (("glow", glib.key(info), r["night"]))
        if k not in cache:
            try:
                med, _ = glib.medians(glib.key(info), r["night"])
                cache[k] = calibration.model_from(med)
            except Exception:
                cache[k] = None

    def one(r):
        try:
            info = json.loads(paths[r["id"]][1])
            raw, _ = pl._load(paths[r["id"]][0], info, lib, r["night"], cache)
            img, s = analysis_image(raw, bool(info.get("bayerpat")) and raw.ndim == 2)
            return r, bg_plane(img, s)
        except Exception:
            return r, None
    n = 0
    with ThreadPoolExecutor(max(1, pl.s.threads)) as ex:
        for r, bp in ex.map(one, todo):
            if bp:
                r["metrics"].update(bp)
                fresh = pl.db.one("SELECT metrics FROM frames WHERE id=?", (r["id"],))
                m = json.loads(fresh["metrics"] or "{}") if fresh else {}
                m.update(bp)
                pl.db.set_frame(r["id"], metrics=m)
                n += 1
    return n


# ------------------------------------------------------------- Zusammenfassung ---
def nights_of(db, pid: int | None = None) -> list[dict]:
    ensure_table(db)
    sql = "SELECT s.*, p.name FROM sky_nights s JOIN projects p ON p.id=s.project_id"
    rows = db.q(sql + (" WHERE s.project_id=?" if pid is not None else "") + " ORDER BY s.night", (pid,) if pid is not None else ())
    out = []
    for r in rows:
        d = json.loads(r["data"] or "{}")
        out.append({**d, "project_id": r["project_id"], "object": r["name"] or d.get("object"), "site_id": r["site_id"],
                    "site_name": r["site_name"], "updated": r["updated"]})
    return out


def summary(db, with_dirs: bool = True) -> dict:
    """Je Standort: Mittelwerte über Nächte (eine Nacht = Mittel ihrer Objekte), Verlauf, Lichtrichtung."""
    rows = nights_of(db)
    sites: dict[str, dict] = {}
    for n in rows:
        key = n.get("site_id") or "?"
        s = sites.setdefault(key, {"id": n.get("site_id"), "name": n.get("site_name") or "Ohne Standort", "nights": {}, "dirs": []})
        nn = s["nights"].setdefault(n["night"], {"night": n["night"], "objects": [], "sqm": [], "seeing": [], "sqm_all": [], "err": []})
        nn["objects"].append({"id": n["project_id"], "name": n.get("object"), "sqm": n.get("sqm"), "note": n.get("note")})
        if n.get("sqm") is not None:
            nn["sqm"].append(n["sqm"])
            nn["err"].append(n.get("err") or SYS_ERR)
        if n.get("sqm_all") is not None:
            nn["sqm_all"].append(n["sqm_all"])
        if n.get("seeing") is not None:
            nn["seeing"].append(n["seeing"])
        for d in n.get("dirs") or []:
            s["dirs"].append([*d, f"{n['project_id']}|{n['night']}"])
    out = []
    for s in sites.values():
        nights = []
        for nn in sorted(s["nights"].values(), key=lambda x: x["night"]):
            nights.append({"night": nn["night"], "objects": nn["objects"],
                           "sqm": _r(float(np.median(nn["sqm"])), 2) if nn["sqm"] else None,
                           "sqm_all": _r(float(np.median(nn["sqm_all"])), 2) if nn["sqm_all"] else None,
                           "seeing": _r(float(np.median(nn["seeing"])), 2) if nn["seeing"] else None,
                           "err": _r(float(np.median(nn["err"])), 2) if nn["err"] else None})
        vals = [n["sqm"] for n in nights if n["sqm"] is not None]
        see = [n["seeing"] for n in nights if n["seeing"] is not None]
        sqm = float(np.median(vals)) if vals else None
        errs = [n["err"] for n in nights if n["err"] is not None]
        err = (float(np.median(errs)) if errs else SYS_ERR) + (0 if len(vals) >= 3 else 0.1)
        item = {"id": s["id"], "name": s["name"], "n_nights": len(nights), "n_site_nights": len(vals),
                "sqm": _r(sqm, 2), "sqm_best": _r(max(vals), 2) if vals else None, "sqm_spread": _r(float(np.std(vals)), 2) if len(vals) >= 3 else None,
                "err": _r(err, 2), "bortle": bortle(sqm, err), "seeing": _r(float(np.median(see)), 2) if see else None,
                "seeing_best": _r(min(see), 2) if see else None, "nights": nights}
        if with_dirs:
            item["light"] = light_direction(s["dirs"])
        out.append(item)
    out.sort(key=lambda s: -s["n_nights"])
    return {"sites": out, "version": SKY_VERSION}


def _best_window(nights: list[dict], k: int = 5) -> dict | None:
    """Dunkelster zusammenhängender Abschnitt einer Nacht: Median über k aufeinanderfolgende ungestörte Kurvenpunkte.
    Die Kurve eines Objekts wird an gestörten Punkten (Flags, ohne Wert) in ungestörte Läufe geteilt – ein Fenster überspannt nie eine Störung."""
    runs: list[list[tuple]] = []
    for n in nights:
        run: list[tuple] = []
        for p in sorted(n.get("curve") or [], key=lambda p: p["ts"]):
            if p.get("sqm") is not None and not p.get("flags"):
                run.append((p["ts"], p["sqm"]))
            elif run:
                runs.append(run)
                run = []
        if run:
            runs.append(run)
    if not runs:
        return None
    long = [r for r in runs if len(r) >= 3] or runs          # Mini-Läufe nur, wenn es nichts Besseres gibt
    cand = []
    for r in long:
        kk = min(k, len(r))
        for i in range(len(r) - kk + 1):
            w = r[i:i + kk]
            cand.append((float(np.median([v for _, v in w])), w))
    med, w = max(cand, key=lambda c: c[0])
    return {"from": w[0][0], "to": w[-1][0], "sqm": _r(med, 2)}


def _pct(vals: list[float], v: float, higher_better: bool) -> float:
    """Anteil der Nächte, die schlechter sind als v (1 = beste Nacht)."""
    if len(vals) < 2:
        return 1.0
    worse = sum(1 for x in vals if (x < v if higher_better else x > v))
    return worse / (len(vals) - 1)


W_DARK, W_SEEING, W_CLEAR = 0.6, 0.2, 0.2     # Gesamtwertung: Dunkelheit zählt am meisten
SURE_ERR = 0.35                                # darüber gilt der Ortswert einer Nacht als unsicher


def clean_nights(db, top: int = 10) -> dict:
    """Die saubersten Nächte je Himmel, aus den gespeicherten Messungen (nichts wird neu gemessen).
    Ortswert = Nacht mit mindestens MIN_GOOD ungestörten Aufnahmen (sqm gesetzt). Rangliste Dunkelheit nur mit solchen Nächten,
    unsichere (err > SURE_ERR, z. B. Eichung aus anderen Nächten) getrennt. Seeing und Durchsicht auch aus gestörten Nächten.
    Gesamtwertung 0–100 = 60 % Dunkelheit, 20 % Seeing, 20 % Durchsicht (jeweils Rang unter allen Nächten mit Ortswert)."""
    rows = nights_of(db)
    groups: dict[tuple, list[dict]] = {}
    for n in rows:
        groups.setdefault((n.get("site_id") or "?", n["night"]), []).append(n)
    items = []
    for (sid, night), ns in groups.items():
        def med(key, nn=ns):
            v = [x[key] for x in nn if x.get(key) is not None]
            return float(np.median(v)) if v else None
        own = [x for x in ns if x.get("sqm") is not None]
        sqm = float(np.median([x["sqm"] for x in own])) if own else None
        err = float(np.median([x.get("err") or SYS_ERR for x in own])) if own else None
        bt = bortle(sqm, err) if sqm is not None else None
        try:
            d0 = dt.date.fromisoformat(night)
        except ValueError:
            continue
        items.append({"site_id": None if sid == "?" else sid, "site_name": ns[0].get("site_name") or "Ohne Standort", "night": night,
                      "weekday": d0.weekday(), "month": d0.month, "sqm": _r(sqm, 2), "sqm_all": _r(med("sqm_all"), 2),
                      "err": _r(err, 2), "sure": bool(own) and err <= SURE_ERR, "bortle": bt, "seeing": _r(med("seeing"), 2),
                      "loss": _r(med("transparency"), 2), "good": sum(x.get("good") or 0 for x in ns),
                      "window": _best_window(own),
                      "objects": [{"id": x["project_id"], "name": x.get("object"), "sqm": x.get("sqm")} for x in ns]})
    ref = [i for i in items if i["sqm"] is not None]
    for i in ref:
        parts = [(W_DARK, _pct([x["sqm"] for x in ref], i["sqm"], True))]
        if i["seeing"] is not None:
            parts.append((W_SEEING, _pct([x["seeing"] for x in ref if x["seeing"] is not None], i["seeing"], False)))
        if i["loss"] is not None:
            parts.append((W_CLEAR, _pct([x["loss"] for x in ref if x["loss"] is not None], i["loss"], False)))
        i["score"] = round(100 * sum(w * p for w, p in parts) / sum(w for w, _ in parts))
    sure = [i for i in ref if i["sure"]]

    def best(lst, key, rev):
        return sorted(lst, key=lambda i: (-i[key] if rev else i[key], i["night"]))[:top]

    sites: dict[str, dict] = {}
    for i in items:
        s = sites.setdefault(i["site_id"] or "?", {"id": i["site_id"], "name": i["site_name"], "months": {}, "n": 0})
        if i["sqm"] is not None:
            s["n"] += 1
            s["months"].setdefault(i["month"], []).append(i["sqm"])
    site_out = []
    for s in sites.values():
        ms = [{"month": m, "n": len(s["months"].get(m, [])),
               "sqm": _r(float(np.median(s["months"][m])), 2) if m in s["months"] else None} for m in range(1, 13)]
        vals = [x for x in ms if x["sqm"] is not None]
        cand = [x for x in vals if x["n"] >= 2] or vals
        bm = max(cand, key=lambda x: x["sqm"]) if cand else None
        allv = [v for vs in s["months"].values() for v in vs]
        site_out.append({"id": s["id"], "name": s["name"], "n": s["n"], "months": ms, "best_month": bm["month"] if bm else None,
                         "sqm": _r(float(np.median(allv)), 2) if allv else None})
    site_out.sort(key=lambda s: (s["sqm"] is None, -(s["sqm"] or 0)))
    ranked = [s for s in site_out if s["sqm"] is not None]
    best_site = max(ranked, key=lambda s: (s["n"] >= 3, s["sqm"])) if ranked else None
    return {"dark": best(sure, "sqm", True), "score": best(sure, "score", True),
            "seeing": best([i for i in items if i["seeing"] is not None], "seeing", False),
            "clear": best([i for i in items if i["loss"] is not None], "loss", False),
            "uncertain": best([i for i in ref if not i["sure"]], "sqm", True),
            "sites": site_out, "best_site": {"id": best_site["id"], "name": best_site["name"], "sqm": best_site["sqm"], "n": best_site["n"]} if best_site else None,
            "n_nights": len(items), "n_site_nights": len(ref), "n_sure": len(sure), "version": SKY_VERSION}


def for_site(db, site_id: str | None) -> dict | None:
    """Kurzfassung für einen Standort (Heute Nacht, Ortsliste)."""
    if not site_id:
        return None
    for s in summary(db, with_dirs=False)["sites"]:
        if s["id"] == site_id and s["sqm"] is not None:
            return {k: s[k] for k in ("sqm", "bortle", "seeing", "n_site_nights", "sqm_spread")}
    return None


# ------------------------------------------------------------------ Auftrag ---
class SkyRunner:
    """„Für alle Nächte berechnen“: eigener Hintergrund-Thread, Fortschritt in der Aktivitätsanzeige."""

    def __init__(self, pl, site_for=None):
        self.pl, self.site_for = pl, site_for
        self.state: dict = {"state": "idle"}
        self._lock = threading.Lock()
        self.pending: list[int] = []                   # nach dem Entwickeln automatisch (ohne Nachmessen der Rohbilder)
        self.quiet: dict = {"state": "idle"}

    def busy(self) -> bool:
        return self.state.get("state") == "running" or self.quiet.get("state") == "running"

    def queue(self, pid: int) -> None:
        """Ein frisch entwickeltes Objekt im Hintergrund nachrechnen (eigener Thread, blockiert die Pipeline nicht)."""
        with self._lock:
            if pid not in self.pending:
                self.pending.append(pid)
            if self.busy():
                return
            self.quiet = {"state": "running"}
        threading.Thread(target=self._drain, daemon=True, name="sky-quality-auto").start()

    def _drain(self):
        while True:
            with self._lock:
                if not self.pending:
                    self.quiet = {"state": "idle"}
                    return
                pid = self.pending.pop(0)
            try:
                compute_project(self.pl, pid, self.site_for, backfill=False)
            except Exception:
                pass

    def start(self, pids: list[int] | None = None, only_missing: bool = False) -> dict:
        with self._lock:
            if self.busy():
                return self.state
            db = self.pl.db
            ensure_table(db)
            if pids is None:
                pids = [r["project_id"] for r in db.q(
                    "SELECT DISTINCT f.project_id FROM frames f JOIN projects p ON p.id=f.project_id WHERE f.kind='light' "
                    "AND f.metrics IS NOT NULL AND COALESCE(json_extract(p.options,'$.input_type'),'')!='picture'")]
                if only_missing:
                    have = {r["project_id"] for r in db.q("SELECT DISTINCT project_id FROM sky_nights WHERE version=?", (SKY_VERSION,))}
                    pids = [p for p in pids if p not in have]
            # Objekte mit gelöstem Stack zuerst – ihre Nullpunkte helfen den übrigen (Gerätenullpunkt)
            wd = self.pl.s.work_dir
            pids = sorted(pids, key=lambda p: not (wd / f"p{p}" / "wcs.json").exists())
            self.state = {"state": "running", "total": len(pids), "done": 0, "started": time.time(), "finished": None,
                          "current": "", "errors": [], "nights": 0, "key": f"sky:{int(time.time())}"}
            threading.Thread(target=self._run, args=(pids,), daemon=True, name="sky-quality").start()
            return self.state

    def _run(self, pids):
        for pid in pids:
            p = self.pl.db.one("SELECT name FROM projects WHERE id=?", (pid,))
            self.state["current"] = p["name"] if p else str(pid)
            try:
                r = compute_project(self.pl, pid, self.site_for, log=lambda m: self.state.update(detail=m))
                self.state["nights"] += r.get("nights", 0)
            except Exception as e:
                self.state["errors"].append(f"{self.state['current']}: {str(e)[:120]}")
            self.state["done"] += 1
        self.state.update(state="done", finished=time.time(), current="", detail="")

    def activity(self) -> list[dict]:
        s = self.state
        if s.get("state") == "idle":
            return []
        tot = max(1, s.get("total", 0))
        det = (f"{s['done']}/{s['total']} Objekte" + (f" · {s['current']}" if s.get("current") else "")) if s["state"] == "running" \
            else f"{s['nights']} Nächte ausgewertet" + (f", {len(s['errors'])} Fehler" if s.get("errors") else "")
        return [{"key": s["key"], "title": "Himmelsqualität", "kind": "Alle Nächte berechnen", "detail": det,
                 "state": "running" if s["state"] == "running" else "done", "progress": s.get("done", 0) / tot,
                 "started": s.get("started"), "finished": s.get("finished"), "href": "#/himmel"}]


def auto_enabled() -> bool:
    """Nach dem Entwickeln automatisch rechnen (ASTROFLOW_SKY=0 schaltet ab – Tests)."""
    import os
    return os.environ.get("ASTROFLOW_SKY", "1") != "0"
