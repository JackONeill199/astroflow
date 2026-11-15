"""Kleine Himmelsrechnung (ohne astropy, schnell genug für jeden Takt): Sternzeit, Azimut/Höhe, Sonne."""
from __future__ import annotations

import math

from ...core import sites


def gmst_deg(ts: float) -> float:
    """Mittlere Greenwich-Sternzeit in Grad (Genauigkeit ~0,1 s, reicht für Planung und Anzeige)."""
    d = ts / 86400.0 + 2440587.5 - 2451545.0
    return (280.46061837 + 360.98564736629 * d) % 360.0


def altaz(ra: float, dec: float, lat: float, lon: float, ts: float) -> tuple[float, float]:
    """(Azimut, Höhe) in Grad; Azimut von Norden über Osten."""
    ha = math.radians((gmst_deg(ts) + lon - ra) % 360.0)
    la, de = math.radians(lat), math.radians(dec)
    sin_alt = math.sin(la) * math.sin(de) + math.cos(la) * math.cos(de) * math.cos(ha)
    alt = math.asin(max(-1.0, min(1.0, sin_alt)))
    az = math.atan2(-math.sin(ha) * math.cos(de), math.cos(la) * math.sin(de) - math.sin(la) * math.cos(de) * math.cos(ha))
    return math.degrees(az) % 360.0, math.degrees(alt)


def sun_alt(ts: float, lat: float, lon: float) -> float:
    """Sonnenhöhe über `core/sites.sun_alt` (eine Quelle für alle Module)."""
    v = sites.sun_alt(ts, lat, lon)
    return -90.0 if v is None else float(v)


def limit(az: float, horizon: list | None, min_alt: float) -> float:
    """Freie Höhe in dieser Richtung: max(Mindesthöhe, Horizontmaske)."""
    return max(min_alt, sites.horizon_at(horizon, az) if horizon else 0.0)


def ang_dist(az1: float, alt1: float, az2: float, alt2: float) -> float:
    a1, a2, z1, z2 = map(math.radians, (alt1, alt2, az1, az2))
    c = math.sin(a1) * math.sin(a2) + math.cos(a1) * math.cos(a2) * math.cos(z1 - z2)
    return math.degrees(math.acos(max(-1.0, min(1.0, c))))


def radec(az: float, alt: float, lat: float, lon: float, ts: float) -> tuple[float, float]:
    """Umkehrung von `altaz`: (RA, Dec) in Grad für eine Richtung am Ort zur Zeit `ts` (Peilpunkte des Himmelsscans)."""
    A, h, la = math.radians(az), math.radians(alt), math.radians(lat)
    sin_dec = math.sin(la) * math.sin(h) + math.cos(la) * math.cos(h) * math.cos(A)
    dec = math.asin(max(-1.0, min(1.0, sin_dec)))
    ha = math.atan2(-math.sin(A) * math.cos(h), math.cos(la) * math.sin(h) - math.sin(la) * math.cos(h) * math.cos(A))
    ra = (gmst_deg(ts) + lon - math.degrees(ha)) % 360.0
    return ra, math.degrees(dec)


def moon_radec(ts: float) -> tuple[float, float]:
    """Mond grob (≈ 1°, genügt, um ihm beim Scan auszuweichen): mittlere Bahn mit den größten Störungen."""
    d = ts / 86400.0 + 2440587.5 - 2451545.0
    L = math.radians((218.316 + 13.176396 * d) % 360)
    M = math.radians((134.963 + 13.064993 * d) % 360)
    F = math.radians((93.272 + 13.229350 * d) % 360)
    lam = L + math.radians(6.289) * math.sin(M)
    beta = math.radians(5.128) * math.sin(F)
    eps = math.radians(23.439)
    ra = math.atan2(math.sin(lam) * math.cos(eps) - math.tan(beta) * math.sin(eps), math.cos(lam))
    dec = math.asin(math.sin(beta) * math.cos(eps) + math.cos(beta) * math.sin(eps) * math.sin(lam))
    return math.degrees(ra) % 360.0, math.degrees(dec)


def moon_altaz(ts: float, lat: float, lon: float) -> tuple[float, float]:
    ra, dec = moon_radec(ts)
    return altaz(ra, dec, lat, lon, ts)
