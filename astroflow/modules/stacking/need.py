"""Belichtungsbedarf je Ziel und Gerät (Planer, Heute Nacht) – Herleitung und Annahmen: docs/entwicklung/belichtungsbedarf.md.

Ein offener Haufen ist nach 20–40 min fertig, ein lichtschwacher Nebel braucht mit einem kleinen Gerät viele Stunden. Die
Schätzung ist bewusst grob und endet in Stufen (STAGES); sie ist eine Planungshilfe, kein Messwert.

    Stunden = Grundbedarf(Objekt) × Geräte-Faktor^e × Himmels-Faktor^e × Faktor aus eigenen Stacks × Wunsch (kurz/normal/gründlich)

- **Grundbedarf** für das Referenzgerät (50 mm f/4) unter einem Vorortshimmel (SQM_REF): bei Galaxien, Planetarischen Nebeln
  und Supernova-Überresten mit Helligkeit aus der mittleren Flächenhelligkeit S (mag + 2,5·log10(Fläche in ″²)):
  H_REF × 10^(SLOPE·(S − S_REF)). Physikalisch wäre die Zeit für gleiches Signal/Rauschen ∝ 10^(0,8·S); Smart-Teleskop-Bilder
  schwacher Objekte werden aber mit weniger S/N „fertig“ – deshalb gedämpft (Erfahrungswerte, siehe Doku). Ohne Helligkeit
  (Emissionsnebel, ASTAP-Objekte) zählt die Objektart (KIND_HOURS, helle Emissionsnebel BRIGHT_EMISSION).
- **Gerät:** Kompromiss aus Flächenhelligkeit je Pixel (∝ f-Zahl²) und Licht je Bogensekunde (∝ 1/Öffnung²):
  geometrisches Mittel → Zeit ∝ f-Zahl / Öffnung = Brennweite / Öffnung².
- **Himmel:** Zeit ∝ Himmelshelligkeit = 10^(−0,4·(SQM − SQM_REF)) (hintergrundbegrenzt).
- Sternhaufen (Punktquellen, Ziel „Sterne sauber“) reagieren schwächer auf Gerät und Himmel (Exponent 0,5),
  Emissionsnebel mit Schmalband-/Dual-Band-Filter etwas schwächer auf den Himmel (0,7).
- **Eichung aus eigenen Daten** (`calibration`): Gaia-Grenzgröße m5 eigener Stacks (Tiefe-Auswertung) gegen die Erwartung
  M5_REF + 2,5·log10(Öffnung/80 mm) + 0,5·(SQM − SQM_REF) nach 1 h; Abweichung → vorsichtiger Faktor (immer „Vorschlag“).
"""
from __future__ import annotations

import json
import math
import statistics

STAGES = (0.5, 1.0, 2.0, 3.0, 5.0, 8.0, 12.0)
DEPTH = {"short": 0.5, "normal": 1.0, "thorough": 1.6}          # Assistent: kurz / normal / gründlich
DEPTH_CAP = {"short": 3.0, "normal": 8.0, "thorough": 12.0}      # Obergrenze je Wunsch
DEPTH_LABEL = {"short": "kurz", "normal": "normal", "thorough": "gründlich"}
REF_RATIO = 0.08            # Brennweite/Öffnung² (1/mm) des Referenzgeräts: 200 mm / (50 mm)² – Vespera, f/4
DEV_CLAMP = (0.3, 3.0)
SQM_REF = 20.0              # Vorortshimmel (Bortle 5) – auch die Annahme, wenn der Ort keinen Messwert hat
SKY_CLAMP = (0.3, 4.0)
S_REF, H_REF, SLOPE = 22.0, 2.0, 0.3     # Flächenhelligkeit 22 mag/″² → 2 h; je mag schwächer × 10^0,3 ≈ 2
KIND_HOURS = {"open_cluster": 0.4, "globular": 1.0, "planetary": 1.0, "emission": 3.0, "reflection": 3.0, "snr": 4.0,
              "galaxy": 3.0, "dark": 8.0, "generic": 2.0}
KIND_FLOOR = {"open_cluster": 0.25, "globular": 0.5, "planetary": 0.5, "galaxy": 1.0, "emission": 0.75, "reflection": 1.0,
              "snr": 1.0, "dark": 3.0}
BRIGHT_EMISSION = {"M42": 0.75, "M43": 0.75, "M8": 1.0, "M17": 1.0, "M16": 1.5, "M20": 1.5, "C92": 1.0, "C103": 1.0}
POINT_KINDS = {"open_cluster", "globular"}
M5_REF = 17.35              # Gaia-Grenzgröße (5σ) eigener Stellina-Stacks nach 1 h, Median aus 82 Objekten (Vorortshimmel)
M5_REF_APERTURE = 80.0
CALIB_MIN = 3               # so viele eigene Stacks braucht ein Gerät für einen Faktor
CALIB_CLAMP = (0.6, 1.6)
KIND_WORD = {"open_cluster": "Offener Haufen", "globular": "Kugelhaufen", "planetary": "Planetarischer Nebel",
             "emission": "Emissionsnebel", "reflection": "Reflexionsnebel", "snr": "Supernova-Überrest", "galaxy": "Galaxie",
             "dark": "Dunkelnebel", "generic": "Objekt"}


def _de(v: float, n: int = 1) -> str:
    return f"{v:.{n}f}".replace(".", ",")


def fmt(h: float) -> str:
    """„30 min“, „2 h“, „1,5 h“."""
    if h < 1:
        return f"{round(h * 60)} min"
    return f"{_de(h)} h" if h % 1 else f"{int(h)} h"


def stage(raw: float, cap: float = STAGES[-1]) -> float:
    """Nächste sinnvolle Stufe (geometrisch gerundet), höchstens cap."""
    best = min(STAGES, key=lambda s: abs(math.log(max(raw, 0.05) / s)))
    return min(best, cap)


def surface_brightness(mag: float | None, size: float | None) -> float | None:
    """Mittlere Flächenhelligkeit (mag/″²) aus Gesamthelligkeit und größter Ausdehnung (′), Fläche als Kreis mit 0,8 × Größe
    (die meisten Objekte sind länglich – mit der vollen großen Achse wäre die Fläche zu groß)."""
    if mag is None or not size or size <= 0:
        return None
    area = math.pi / 4 * (0.8 * size * 60) ** 2
    return mag + 2.5 * math.log10(area)


def device_ratio(device: dict | None) -> tuple[float | None, str | None]:
    """Brennweite/Öffnung² und Text („80 mm f/5“); None, wenn das Gerät keine Optik nennt."""
    from . import devices
    d = device or {}
    ap, fo = d.get("aperture"), d.get("focal")
    if not ap or not fo:
        key = d.get("model") or (d.get("lib_id") if d.get("id") == "library" else d.get("id"))
        row = devices.get(key) or {}
        if row.get("family") and not row.get("aperture"):
            row = devices.get(row["family"]) or {}
        ap, fo = row.get("aperture"), row.get("focal")
    if not ap or not fo:
        return None, None
    return fo / ap ** 2, f"{ap:.0f} mm f/{_de(fo / ap, 1).rstrip('0').rstrip(',')}"


def need_hours(target, device: dict | None = None, sqm: float | None = None, *, calib: float = 1.0,
               depth: str = "normal", sky_source: str | None = None) -> dict:
    """Geschätzter Belichtungsbedarf. target: deepsky.Target oder dict mit kind, mag, size, id.
    Rückgabe {hours (Stufe), raw, why (kurze Begründung), assumed [Annahmen]}."""
    g = (lambda k: getattr(target, k, None)) if not isinstance(target, dict) else target.get
    kind = g("kind") or "generic"
    mag, size, tid = g("mag"), g("size"), str(g("id") or "")
    assumed, parts = [], []
    S = surface_brightness(mag, size) if kind not in POINT_KINDS else None
    if tid in BRIGHT_EMISSION:
        base = BRIGHT_EMISSION[tid]
        parts.append(f"{KIND_WORD.get(kind, 'Objekt')}, sehr hell")
    # Reflexionsnebel: Helligkeit gehört zum beleuchtenden Stern; große Supernova-Reste (Cirrus) sind fädig, nicht flächig
    elif S is not None and (kind in ("galaxy", "planetary", "generic") or (kind == "snr" and (size or 0) <= 20)):
        base = H_REF * 10 ** (SLOPE * (S - S_REF))
        parts.append(f"{KIND_WORD.get(kind, 'Objekt')}, Flächenhelligkeit {_de(S)} mag/″²")
    else:
        base = KIND_HOURS.get(kind, KIND_HOURS["generic"])
        if kind in POINT_KINDS and mag is not None:                   # schwache Haufen etwas länger, helle kürzer
            base *= min(1.6, max(0.6, 10 ** (0.1 * (mag - 6))))
        parts.append(KIND_WORD.get(kind, "Objekt"))
        if kind not in POINT_KINDS:
            assumed.append("Helligkeit unbekannt – Wert nach Objektart")
    base = max(base, KIND_FLOOR.get(kind, 0.5))
    e_dev = 0.5 if kind in POINT_KINDS else 1.0
    e_sky = 0.5 if kind in POINT_KINDS else (0.7 if kind in ("emission", "planetary", "snr") else 1.0)
    ratio, optics = device_ratio(device)
    if ratio is None:
        f_dev = 1.0
        assumed.append("Optik des Geräts unbekannt – wie 50 mm f/4 gerechnet")
    else:
        f_dev = min(DEV_CLAMP[1], max(DEV_CLAMP[0], ratio / REF_RATIO))
        parts.append(optics)
    if sqm is None:
        f_sky = 1.0
        assumed.append(f"Himmel ohne Messwert – SQM {_de(SQM_REF)} angenommen")
    else:
        f_sky = min(SKY_CLAMP[1], max(SKY_CLAMP[0], 10 ** (-0.4 * (sqm - SQM_REF))))
        parts.append(f"Himmel SQM {_de(sqm)}" + (f" ({sky_source})" if sky_source else ""))
    if abs(calib - 1) >= 0.05:
        parts.append(f"eigene Stacks ×{_de(calib, 2)}")
    k = DEPTH.get(depth, 1.0)
    raw = base * f_dev ** e_dev * f_sky ** e_sky * calib * k
    cap = DEPTH_CAP.get(depth, STAGES[-1])
    h = stage(raw, cap)
    return {"hours": h, "raw": round(raw, 2), "why": " · ".join(parts), "assumed": assumed}


def nights_for(rem_hours: float, per_night: float) -> int | None:
    """So viele Nächte braucht der Rest bei `per_night` nutzbaren Stunden (None, wenn das Ziel heute gar nicht geht)."""
    if rem_hours <= 0:
        return 0
    if per_night <= 0.1:
        return None
    return max(1, math.ceil(rem_hours / per_night - 0.05))


def text(need: float, have: float = 0.0, per_night: float | None = None) -> str:
    """„reicht heute“ / „braucht ~3 Nächte“ / „fertig“ – für Heute Nacht."""
    rem = need - have
    if rem <= 0.05:
        return f"Bedarf ~{fmt(need)} erreicht"
    n, head = nights_for(rem, per_night or 0), f"{'noch ' if have > 0.05 else ''}~{fmt(rem)}"
    if n is None:
        return f"braucht {head}"
    return f"{head} – reicht heute" if n <= 1 else f"{head} – braucht ~{n} Nächte"


# ------------------------------------------------------------------ Eichung ---
def calibration_rows(db) -> list[dict]:
    """Eigene Stacks mit Tiefe-Ergebnis: {device, exposure (s), m5}. Gerät aus dem Kopf eines Frames des Projekts."""
    from . import tutor
    rows = db.q("SELECT r.project_id, r.exposure, json_extract(r.stats,'$.depth.grenze.m5') m5 FROM results r "
                "WHERE r.id IN (SELECT MAX(id) FROM results GROUP BY project_id) AND r.exposure > 600 "
                "AND json_extract(r.stats,'$.depth.grenze.m5') IS NOT NULL")
    out = []
    for r in rows:
        f = db.one("SELECT info FROM frames WHERE project_id=? AND kind='light' LIMIT 1", (r["project_id"],))
        info = json.loads(f["info"] or "{}") if f else {}
        dev = tutor.detect_device([info.get("instrument")], info.get("shape"))
        if dev.get("guessed") and dev["id"] != "stellina":
            continue
        out.append({"device": dev.get("model") or dev["id"], "family": dev["id"], "exposure": float(r["exposure"]), "m5": float(r["m5"])})
    return out


def calibration(rows: list[dict], device: dict | None, sqm: float | None) -> dict:
    """Faktor aus eigenen Stacks für ein Gerät: {factor, n, offset, status, text}. Ohne genug Stacks factor 1."""
    from . import devices
    d = device or {}
    keys = {d.get("model"), d.get("id"), d.get("lib_id"), d.get("mag_id")} - {None, "library"}
    mine = [r for r in rows if r["device"] in keys or r.get("family") in keys]
    ratio, _ = device_ratio(device)
    key = d.get("model") or (d.get("lib_id") if d.get("id") == "library" else d.get("id"))
    row = devices.get(key) or {}
    ap = d.get("aperture") or row.get("aperture") or (devices.get(row.get("family")) or {}).get("aperture")
    if len(mine) < CALIB_MIN or not ap or ratio is None:
        return {"factor": 1.0, "n": len(mine), "status": None, "text": None}
    m1h = statistics.median(r["m5"] - 1.25 * math.log10(r["exposure"] / 3600) for r in mine)
    expect = M5_REF + 2.5 * math.log10(ap / M5_REF_APERTURE) + 0.5 * ((sqm if sqm is not None else SQM_REF) - SQM_REF)
    off = m1h - expect
    # tiefer als erwartet → weniger Zeit. Physikalisch Zeit ∝ 10^(−0,8·Δm); wie beim Grundbedarf halb gedämpft
    f = min(CALIB_CLAMP[1], max(CALIB_CLAMP[0], 10 ** (-0.4 * off)))
    return {"factor": round(f, 2), "n": len(mine), "offset": round(off, 2), "status": "vorschlag",
            "text": f"Aus {len(mine)} eigenen Stacks: Grenzgröße nach 1 h {_de(m1h, 2)} mag (erwartet {_de(expect, 2)}) → Faktor {_de(f, 2)} (Vorschlag)"}
