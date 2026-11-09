"""Bildqualität einer Aufnahme (0–10) aus gespeicherten Messwerten – Nutzerbefund „Bildqualität immer 10 von 10?“ (09.11.2026).

Die Bewertung aus `develop.assess` misst, ob die *Bearbeitung* Restfehler hat (Farbstich, Gradient, Clipping …), und wird von
der Korrekturschleife fast immer auf 10 geregelt. Diese Note bewertet dagegen die *Aufnahme* selbst – aus Werten, die ohnehin
gespeichert sind (keine neue Analyse):

| Teil       | Messwert                                              | 0 Punkte  | 10 Punkte | Gewicht |
|------------|-------------------------------------------------------|-----------|-----------|---------|
| Tiefe      | Grenzgröße 5σ (`depth.grenze.m5`, Gaia)               | 14 mag    | 19 mag    | 35 %    |
| Rauschen   | Objekt-SNR des linearen Stacks (`quality.measure.snr`)| 3         | 60 (log.) | 25 %    |
| Schärfe    | Stern-FWHM im Stack (`fwhm_px`, Pixel)                | 8 px      | 3 px      | 25 %    |
| Sternform  | mittlere Exzentrizität der verwendeten Einzelbilder   | 0,85      | 0,35      | 15 %    |

Feste Ankerpunkte (nicht relativ zur eigenen Sammlung): eine Note bleibt gleich, wenn neue Bilder dazukommen. Fehlende Teile
(z. B. Tiefe ohne Plate-Solving) fallen weg, die Gewichte der übrigen werden hochgerechnet; unter zwei Teilen gibt es keine Note.
Schärfe in Pixeln, weil sie so aussieht, wie man das Bild in voller Größe sieht (und ohne Plate-Solving auskommt); die
Bogensekunden stehen nur in der Erklärung.
"""
from __future__ import annotations

import math

GRADE_VERSION = 1
MIN_PARTS = 2

# (Schlüssel, Bezeichnung, Gewicht, Wert bei 0 Punkten, Wert bei 10 Punkten, logarithmisch)
PARTS = (("depth", "Tiefe", 0.35, 14.0, 19.0, False),
         ("noise", "Rauschen", 0.25, 3.0, 60.0, True),
         ("sharp", "Schärfe", 0.25, 8.0, 3.0, False),
         ("shape", "Sternform", 0.15, 0.85, 0.35, False))


def _de(v: float, nd: int = 1) -> str:
    return f"{v:.{nd}f}".replace(".", ",")


def _points(v: float, lo: float, hi: float, log: bool) -> float:
    if log:
        v, lo, hi = math.log(max(v, 1e-6)), math.log(lo), math.log(hi)
    return max(0.0, min(10.0, 10.0 * (v - lo) / (hi - lo)))


def _ok(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def grade(m5: float | None = None, snr: float | None = None, fwhm_px: float | None = None, ecc: float | None = None,
          scale: float | None = None) -> dict | None:
    """Note 0–10 mit Herleitung. scale: Bogensekunden je Pixel (nur für den Erklärungstext). None, wenn zu wenig Messwerte."""
    val = {"depth": m5, "noise": snr, "sharp": fwhm_px, "shape": ecc}
    text = {"depth": lambda v: f"Grenzgröße {_de(v)} mag",
            "noise": lambda v: f"Objekt-SNR {v:.0f}",
            "sharp": lambda v: f"Sterne {_de(v)} px breit" + (f" ({_de(v * scale)}″)" if _ok(scale) and scale > 0 else ""),
            "shape": lambda v: f"Exzentrizität {_de(v, 2)}" + (" (rund)" if v < 0.5 else " (leicht oval)" if v < 0.7 else " (verzogen)")}
    parts = []
    for key, label, w, lo, hi, log in PARTS:
        v = val[key]
        if not _ok(v) or (log and v <= 0):
            continue
        parts.append({"key": key, "label": label, "value": round(float(v), 3), "points": round(_points(float(v), lo, hi, log), 1),
                      "weight": w, "text": text[key](float(v))})
    if len(parts) < MIN_PARTS:
        return None
    total = sum(p["weight"] for p in parts)
    for p in parts:
        p["share"] = round(p["weight"] / total, 3)            # Anteil an der Note nach dem Hochrechnen
    score = sum(p["points"] * p["weight"] for p in parts) / total
    return {"score": round(score, 1), "parts": parts, "missing": [k for k, *_ in PARTS if k not in {p["key"] for p in parts}],
            "v": GRADE_VERSION}


def of_result(stats: dict, ecc: float | None = None, depth_version: int | None = None) -> dict | None:
    """Note aus den gespeicherten `results.stats` (+ mittlere Exzentrizität der Einzelbilder aus `frames.metrics`).
    Tiefe zählt nur in der aktuellen Fassung (depth_version), damit alte, fehlerhafte Grenzgrößen keine Note bestimmen."""
    d = stats.get("depth") or {}
    depth_ok = d.get("ok") and (depth_version is None or d.get("v") == depth_version)
    m5 = (d.get("grenze") or {}).get("m5") if depth_ok else None
    scale = (d.get("feld") or {}).get("skala") if depth_ok else None
    snr = ((stats.get("quality") or {}).get("measure") or {}).get("snr")
    return grade(m5=m5, snr=snr, fwhm_px=stats.get("fwhm_px"), ecc=ecc, scale=scale)


def project_ecc(db, project_ids: list[int] | None = None) -> dict[int, float]:
    """Mittlere Exzentrizität der nicht aussortierten Einzelbilder je Projekt (bleibt nach dem Löschen der Rohbilder)."""
    sql = ("SELECT project_id, AVG(json_extract(metrics,'$.ecc')) e FROM frames WHERE kind='light' AND status!='rejected' "
           "AND metrics IS NOT NULL")
    args: tuple = ()
    if project_ids is not None:
        if not project_ids:
            return {}
        sql += f" AND project_id IN ({','.join('?' * len(project_ids))})"
        args = tuple(project_ids)
    return {x["project_id"]: x["e"] for x in db.q(sql + " GROUP BY project_id", args) if x["e"] is not None}
