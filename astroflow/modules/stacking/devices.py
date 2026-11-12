"""Geräte-Tabelle (seit 0.71): eine Quelle für Tutor, Heute Nacht, Planer und Gerätediagnose.

Die Daten stehen in `devices.json` – je Gerät Montierung, Derotator, EQ-Modus, Zenit-Grenze, typische Belichtung, Sensor,
Bildfeld und Tipps. Werte mit Herkunft schreiben sich `{"v": Wert, "status": "gesichert"|"vorschlag", "quelle": "…"}`;
„vorschlag“ heißt Forum/Test/Händler und wird später mit eigenen Aufnahmen kalibriert. Recherche: docs/entwicklung/nachfuehrung.md.

Nachführung (`profile`, `rotation_rate`, `weights`): statt eines festen Zenit-Abschlags zählt die **Bildfelddrehung** einer
azimutalen Montierung, dq/dt = ω·cos φ·|cos A| / cos h (ω = Erddrehung, φ Breite, A Azimut ab Nord, h Höhe). Daraus das
Verschmieren am Bildrand je Belichtung (Drehung × halbe Sensordiagonale in Pixeln); bis SMEAR_OK Pixel zählt eine Stunde voll.
- Software-Derotation (Vespera, Seestar …): Drehung zählt voll, dazu der Abschlag über der Zenit-Grenze des Geräts.
- Mechanischer/optischer Derotator (Stellina): nur ein kleiner Rest (DEROT_RESIDUAL), Zenit-Grenze laut Tabelle.
- EQ-Modus/äquatoriale Montierung: keine Drehung.
- Unbekanntes Gerät: wie bisher (Alt-Az: Abschlag ZENITH_WEIGHT über 80°, sonst keiner).
"""
from __future__ import annotations

import json
import math
from functools import lru_cache
from pathlib import Path

import numpy as np

PATH = Path(__file__).with_name("devices.json")
OMEGA = 360.0 / 86164.1 * 60       # Erddrehung in Grad je Minute (siderisch, ≈ 0,2507)
DEFAULT_ZENITH = 80.0               # bisherige Grenze für Alt-Az-Geräte ohne eigenen Wert
ZENITH_WEIGHT = 0.4                 # Gewicht der Stunden über der Zenit-Grenze
SMEAR_OK = 1.5                      # Pixel Randverschmieren je Belichtung, bis zu dem eine Stunde voll zählt
ROT_FLOOR = 0.15                    # kleinstes Gewicht durch Bildfelddrehung
DEROT_RESIDUAL = 0.1                # Restdrehung bei mechanischem/optischem Derotator (Anteil)
DEFAULT_RADIUS = 1100.0             # halbe Sensordiagonale (px), wenn das Gerät keinen Sensor nennt (≈ 1920 × 1080)
DEFAULT_EXP = 10.0                  # s, wenn weder Gerät noch Aufnahmen eine Belichtung nennen
VALUE_FIELDS = ("mount", "derotator", "eq_mode", "zenith_limit", "exp_altaz", "exp_eq", "sensor", "fov")
MODE_LABEL = {"none": "keine Bildfelddrehung berücksichtigt", "legacy": f"Alt-Az, Abschlag über {DEFAULT_ZENITH:.0f}°",
              "software": "Alt-Az mit Software-Derotation", "derot": "Alt-Az mit Feld-Derotator", "eq": "äquatorial (EQ)"}


@lru_cache(maxsize=1)
def table() -> dict:
    """Rohdaten aus devices.json (ohne Hinweis-Eintrag)."""
    raw = json.loads(PATH.read_text(encoding="utf-8"))
    return {k: v for k, v in raw.items() if not k.startswith("_")}


def _val(x):
    return x.get("v") if isinstance(x, dict) and "v" in x else x


@lru_cache(maxsize=32)
def _get(key: str) -> dict | None:
    raw = table().get(key)
    if raw is None:
        return None
    fam = table().get(raw.get("family") or "") or {}
    out = {k: _val(v) for k, v in raw.items() if k != "texts"}
    out["texts"] = {**(fam.get("texts") or {}), **(raw.get("texts") or {})}
    out["sources"] = {k: {"status": v.get("status"), "quelle": v.get("quelle")} for k, v in raw.items()
                      if isinstance(v, dict) and "v" in v}
    return out


def get(key: str | None) -> dict | None:
    """Ein Gerät mit einfachen Werten, `texts` (mit denen der Gerätefamilie) und `sources` {Feld: {status, quelle}}."""
    d = _get(str(key)) if key else None
    return json.loads(json.dumps(d)) if d else None


def ids() -> list[str]:
    return list(table())


def tutor_devices() -> dict:
    """Ansicht für den Bild-Tutor (früher tutor.DEVICES): Erkennungs-IDs mit Tipps."""
    out = {}
    for k in ("stellina", "vespera", "seestar", "dwarf", "evscope", "camera"):
        d = _get(k)
        t = d["texts"]
        out[k] = {"name": d["name"] if k != "seestar" else "ZWO Seestar", "smart": bool(d.get("smart")),
                  "alt_az": d.get("mount") == "altaz", "gust": d.get("gust"),
                  **{x: d[x] for x in ("aperture", "focal") if d.get(x) is not None},
                  **({"dew_heater": True} if d.get("dew_heater") else {}),
                  "filter": t.get("filter", ""), "wind": t.get("wind", ""), "dew": t.get("dew", ""), "fov": t.get("fov", "")}
    return out


def planner_devices() -> dict:
    """Ansicht für den Planer (früher planner.DEVICES): Geräte mit bekanntem Bildfeld."""
    out = {}
    for k, raw in table().items():
        d = _get(k)
        if not d.get("planner") or not d.get("fov"):
            continue
        fam = _get(d["family"]) if d.get("family") else None
        out[k] = {"name": d["name"], "fov": list(d["fov"]), "alt_az": d.get("mount") == "altaz",
                  "mag_limit": d.get("mag_limit") or (fam or {}).get("mag_limit"),
                  **({"mag_id": d["family"]} if fam else {}), "eq_mode": bool(d.get("eq_mode"))}
    return out


def mag_limits() -> dict:
    """Grenzgröße je Erkennungs-ID (deepsky.MAG_LIMITS)."""
    return {k: _get(k)["mag_limit"] for k in table() if _get(k).get("mag_limit") and not _get(k).get("family")}


# ------------------------------------------------------------------ Nachführung ---
def profile(device: dict | None) -> dict:
    """Nachführungsprofil eines Geräts. device: Gerät wie in Heute Nacht/Planer ({id, alt_az, …}; optional `eq` = EQ-Modus
    laut Nutzer oder Erkennung, `exptime` = Belichtung der eigenen Aufnahmen, `lib_id` = erkanntes Gerät hinter „library“)."""
    device = device or {}
    key = device.get("lib_id") if device.get("id") == "library" else device.get("id")
    d = _get(str(key)) if key else None
    src = (d or {}).get("sources", {})
    if device.get("eq"):
        mode = "eq"
    elif d is None or not d.get("mount"):
        mode = "legacy" if device.get("alt_az") else "none"
    elif d["mount"] == "eq":
        mode = "eq"
    elif d.get("derotator") == "optical":
        mode = "derot"
    else:
        mode = "software"
    zl, zl_status = None, None
    if mode in ("software", "derot"):
        zl = d.get("zenith_limit")
        zl_status = (src.get("zenith_limit") or {}).get("status")
        if zl is None and zl_status != "gesichert":
            zl, zl_status = DEFAULT_ZENITH, "vorschlag"
    elif mode == "legacy":
        zl = DEFAULT_ZENITH
    sensor = (d or {}).get("sensor")
    exp = device.get("exptime") or (d or {}).get("exp_altaz") or DEFAULT_EXP
    return {"mode": mode, "label": MODE_LABEL[mode], "zenith_limit": zl, "zenith_status": zl_status,
            "zenith_source": (src.get("zenith_limit") or {}).get("quelle") if mode in ("software", "derot") else None,
            "exp": float(exp), "radius_px": math.hypot(*sensor) / 2 if sensor else DEFAULT_RADIUS,
            "eq_mode": bool((d or {}).get("eq_mode")) if d else not device.get("alt_az")}


def rotation_rate(alt, az, lat: float):
    """Bildfelddrehung einer Alt-Az-Montierung in Grad je Minute (Betrag). alt/az in Grad (Azimut ab Nord), lat in Grad.
    Nahe am Zenit auf cos(89,5°) begrenzt (dort ist die Rate ohnehin weit jenseits jeder Belichtung)."""
    alt, az = np.asarray(alt, float), np.asarray(az, float)
    c = np.maximum(np.cos(np.radians(alt)), math.cos(math.radians(89.5)))
    return OMEGA * math.cos(math.radians(lat)) * np.abs(np.cos(np.radians(az))) / c


def edge_smear(rate, prof: dict):
    """Verschmieren am Bildrand je Belichtung in Pixeln (Drehung × halbe Sensordiagonale)."""
    k = DEROT_RESIDUAL if prof["mode"] == "derot" else (0.0 if prof["mode"] in ("eq", "none") else 1.0)
    return np.radians(np.asarray(rate, float) * k * prof["exp"] / 60) * prof["radius_px"]


def weights(alt, az, lat: float, prof: dict):
    """Gewicht (0–1) je Höhe/Azimut für die Rangfolge: Bildfelddrehung und Zenit-Grenze nach Geräteprofil."""
    alt = np.asarray(alt, float)
    if prof["mode"] in ("none", "eq"):
        return np.ones_like(alt)
    if prof["mode"] == "legacy":
        return np.where(alt > DEFAULT_ZENITH, ZENITH_WEIGHT, 1.0)
    smear = edge_smear(rotation_rate(alt, az, lat), prof)
    w = np.clip(SMEAR_OK / np.maximum(smear, 1e-9), ROT_FLOOR, 1.0)
    if prof["zenith_limit"] is not None:
        w = w * np.where(alt > prof["zenith_limit"], ZENITH_WEIGHT, 1.0)
    return w


def max_exposure(alt, az, lat: float, prof: dict, px: float = 1.0):
    """Längste Belichtung (s), bei der der Bildrand höchstens `px` Pixel verschmiert (Software-Derotation/Derotator-Rest)."""
    k = DEROT_RESIDUAL if prof["mode"] == "derot" else 1.0
    rate = rotation_rate(alt, az, lat) * k / 60                      # Grad je Sekunde
    return px / (np.radians(np.maximum(rate, 1e-12)) * prof["radius_px"])
