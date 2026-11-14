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
