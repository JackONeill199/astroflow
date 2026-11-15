"""Beschlagene Optik während der Aufnahme erkennen (Autopilot).

Grundlage sind Messwerte je Rohbild des laufenden Ziels: Sternzahl, FWHM und `veil` (heller Schleier, den keine Ebene
erklärt – Dienst `frame_quick_metrics` des Bilder-Moduls bzw. der Simulator). Beschlag kommt schleichend und bleibt:
anders als ein Wolkenzug sind *alle* Bilder der letzten Minuten schlechter als zu Beginn des Ziels. Ob die Vorhersage
klar ist, prüft der Autopilot (bei Wolken laut Vorhersage gilt es nicht als Beschlag).

Echtbetrieb 14.11.2026 (Vespera ohne Tauheizung, Taupunktabstand ~1 °C): M 35 mit hellem, ortsfestem Schleier,
`veil` 4,5–5,1 (klare Nächte 0,1–1,3). Nur Standardbibliothek – Zahlen kommen fertig vom Dienst.
"""
from __future__ import annotations

from collections import deque
from statistics import median

FOG_SPAN_S = 5 * 60          # so lange muss es anhalten („über mehrere Minuten“)
FOG_MIN_RECENT = 6           # … mit mindestens so vielen Bildern darin
BASE_N = 6                   # Bezug: die ersten Bilder des Ziels
FOG_SHARE = 0.8              # Anteil der Bilder im Fenster, die schlechter sein müssen (Wolkenzüge lassen Lücken)
STAR_DROP = 0.35             # Sternzahl im Fenster (Median) mindestens um so viel gefallen …
FWHM_RISE = 0.30             # … oder FWHM um so viel gestiegen
STAR_BAD, FWHM_BAD = 0.8, 1.15   # ein einzelnes Bild zählt als „schlechter“ ab diesen Faktoren
VEIL_MIN = 3.0               # wie `stacking.quality.VEIL_MIN` (Dienst liefert zusätzlich `veiled`)


class FogWatch:
    """Messwerte der zuletzt aufgenommenen Bilder; `check` sagt, ob die Optik vermutlich beschlagen ist."""

    def __init__(self, maxlen: int = 400):
        self.frames: deque = deque(maxlen=maxlen)     # (t, n_stars, fwhm, veil)

    def add(self, t: float, m: dict):
        n, f, v = m.get("n_stars"), m.get("fwhm"), m.get("veil")
        if n is None and v is None:
            return
        self.frames.append((float(t), n, f, v))

    def clear(self):
        self.frames.clear()

    def check(self, now: float, since: float) -> dict | None:
        """None oder {why, stars: (vorher, jetzt), fwhm: (vorher, jetzt), veil, minutes} für die Bilder ab `since`."""
        fr = [x for x in self.frames if x[0] >= since]
        if len(fr) < BASE_N + FOG_MIN_RECENT or fr[-1][0] - fr[0][0] < FOG_SPAN_S:
            return None
        recent = [x for x in fr if x[0] >= now - FOG_SPAN_S]
        if len(recent) < FOG_MIN_RECENT or recent[-1][0] - recent[0][0] < 0.6 * FOG_SPAN_S:
            return None
        base = fr[:BASE_N]
        out = {"minutes": round((recent[-1][0] - recent[0][0]) / 60)}
        veils = [x[3] for x in recent if x[3] is not None]
        if veils and sum(v >= VEIL_MIN for v in veils) >= FOG_SHARE * len(recent):
            out.update(why="veil", veil=round(median(veils), 1))
        n0, n1 = _med(base, 1), _med(recent, 1)
        f0, f1 = _med(base, 2), _med(recent, 2)
        if n0 and f0 and n1 is not None and f1 is not None:
            out.update(stars=(round(n0), round(n1)), fwhm=(round(f0, 1), round(f1, 1)))
            worse = sum(1 for x in recent if (x[1] is not None and x[1] <= STAR_BAD * n0) or (x[2] is not None and x[2] >= FWHM_BAD * f0))
            trend = n1 <= (1 - STAR_DROP) * n0 or f1 >= (1 + FWHM_RISE) * f0
            if "why" not in out and trend and worse >= FOG_SHARE * len(recent):
                out["why"] = "trend"
        return out if "why" in out else None


def _med(rows, i):
    v = [x[i] for x in rows if x[i] is not None]
    return median(v) if v else None


def describe(name: str, r: dict) -> str:
    """Protokolltext zum Befund."""
    parts = []
    if r.get("stars") and r["stars"][1] < r["stars"][0]:
        parts.append(f"Sterne {r['stars'][0]} → {r['stars'][1]}")
    if r.get("fwhm") and r["fwhm"][1] > r["fwhm"][0]:
        parts.append(f"FWHM {r['fwhm'][0]:.1f} → {r['fwhm'][1]:.1f} px".replace(".", ","))
    if r.get("veil") is not None:
        parts.append(f"heller Schleier im Bild ({r['veil']:.1f})".replace(".", ","))
    return f"{name}: Optik vermutlich beschlagen – " + ", ".join(parts) + f" seit über {max(1, r['minutes'])} min"
