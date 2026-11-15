"""Freier Himmel am Teleskop: aus Peilpunkten (frei/verdeckt) eine Karte je Azimut, aus vielen Nächten Aufstellungen eines Ortes.

Peilpunkt: {az, alt, res: "frei"|"verdeckt"|"zweifel", sure, stars, src: "scan"|"ziel"|"bild", t} – nur Azimut/Höhe, nie
Koordinaten des Ortes. Je Stützstelle der Horizontmaske (36 × 10°):
  lo  = höchste verdeckte Höhe unterhalb des ersten freien Punkts (Geländer, Hauswand – verdeckt von unten),
  hi  = niedrigste freie Höhe (dort waren nachweislich Sterne),
  top = verdeckt *über* einem freien Punkt (Dachüberstand – verdeckt von oben).
Grenze = Ortsmaske, angehoben auf lo + MARGIN, abgesenkt höchstens bis hi; liegen beide vor, gilt die Mitte (das Halbieren
im Scan macht die Spanne klein). Verdeckte Punkte wirken ±BLOCK_SPREAD breit (Rasterabstand des Scans), gedeckelt durch
freie Punkte; freie Punkte senken nur in ihrer eigenen Stützstelle.

Aufstellungen: Nächte, die sich nirgends widersprechen, gehören zusammen (das Teleskop stand gleich). Verdeckt aus Zielen und
Aufnahmen gilt erst als sicher, wenn es sich in zwei Nächten wiederholt – Wolken wandern, Hindernisse bleiben.
Nur Standardbibliothek (schnell, für jeden Takt).
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

STEP = 10
N = 36
MARGIN = 2.0            # Grenze knapp über der höchsten verdeckten Höhe
BLOCK_SPREAD = 15.0     # verdeckter Punkt: so weit im Azimut (halber Rasterabstand des Scans)
DIRECT = 5.0            # Abstand, bis zu dem ein Scan-Punkt gelernte Punkte derselben Richtung ersetzt
CONFLICT_TOL = 4.0      # frei so viel unter verdeckt (gleiche Stützstelle) = Widerspruch zwischen zwei Nächten
MIN_COMMON = 1          # so viele gemeinsame Stützstellen mit Auskunft braucht der Vergleich zweier Nächte (ohne Widerspruch)
FAR_TOL = 4.0           # so nah müssen die Grenzen aller Aufstellungen liegen, damit sie für den ganzen Ort gilt
MAX_NIGHTS = 120        # je Ort gespeicherte Nächte
MAX_POINTS = 400        # je Nacht


def _d(a: float, b: float) -> float:
    return abs((a - b + 540) % 360 - 180)


def _bin_az(i: int) -> float:
    return float(i * STEP)


def bin_of(az: float) -> int:
    """Stützstelle eines Punkts (die nächste; genau auf der Mitte zwischen zweien: die kleinere)."""
    x = (az % 360) / STEP
    return int(x + 0.5 - 1e-9) % N


def columns(points: list[dict]) -> list[dict]:
    """Je Stützstelle {lo, hi, top, nb, nf, sure}. `sure` = es gibt einen sicheren verdeckten Punkt bzw. nur freie Punkte."""
    out = []
    for i in range(N):
        az = _bin_az(i)
        own = [p for p in points if p.get("res") in ("frei", "verdeckt") and bin_of(p["az"]) == i]
        free = sorted(p["alt"] for p in own if p["res"] == "frei")
        hi = free[0] if free else None
        lo = top = None
        sure = False
        for p in own:
            if p["res"] != "verdeckt":
                continue
            if hi is not None and p["alt"] > hi:
                if not any(f > p["alt"] for f in free):          # über allen freien Punkten: Überhang
                    top = p["alt"] if top is None else min(top, p["alt"])
                continue
            lo = p["alt"] if lo is None else max(lo, p["alt"])
            sure = sure or bool(p.get("sure"))
        # Nachbarn (halber Rasterabstand): heben an, aber nie über einen hier nachweislich freien Punkt
        for p in points:
            if p.get("res") != "verdeckt" or bin_of(p["az"]) == i or _d(p["az"], az) > BLOCK_SPREAD:
                continue
            if hi is not None and p["alt"] >= hi:
                continue
            if any(bin_of(q["az"]) == bin_of(p["az"]) and q.get("res") == "frei" and q["alt"] < p["alt"] for q in points):
                continue                                         # beim Nachbarn selbst ein Überhang
            lo = p["alt"] if lo is None else max(lo, p["alt"])
        if top is not None:                                      # Überhang: Mitte zwischen höchstem freien Punkt darunter und ihm
            below = max(f for f in free if f < top)
            top = round(below + (top - below) / 2, 1)
        nb = sum(1 for p in own if p["res"] == "verdeckt")
        out.append({"az": az, "lo": lo, "hi": hi, "top": top, "nb": nb, "nf": len(free),
                    "sure": sure or (lo is None and bool(free))})
    return out


def limit_of(c: dict, base: float = 0.0) -> float:
    """Grenze einer Stützstelle: Ortsmaske `base`, angehoben/abgesenkt durch die Messung."""
    lo, hi = c.get("lo"), c.get("hi")
    if lo is not None and hi is not None and hi > lo:
        return round(min(hi, max(lo + MARGIN, (lo + hi) / 2)), 1)
    v = base
    if lo is not None:
        v = max(v, lo + MARGIN)
    if hi is not None and lo is None:
        v = min(v, hi)
    return round(min(90.0, max(0.0, v)), 1)


def horizon(points: list[dict], base: list | None = None) -> dict:
    """Wirksame Maske dieser Punkte über der Ortsmaske `base`: {horizon: [36], top: [36|None], known: [bool]}."""
    cols = columns(points)
    b = list(base or [0.0] * N)
    hz = [limit_of(c, float(b[i] or 0.0)) for i, c in enumerate(cols)]
    return {"horizon": hz, "top": [c["top"] for c in cols], "known": [c["lo"] is not None or c["hi"] is not None for c in cols],
            "cols": cols}


def state_at(cols: list[dict], az: float, alt: float) -> str:
    """Erwartung an einem Punkt: "frei" | "verdeckt" | "?" (nach den Stützstellen in der Nähe)."""
    c = cols[int(round((az % 360) / STEP)) % N]
    if c.get("top") is not None and alt >= c["top"]:
        return "verdeckt"
    if c.get("lo") is not None and alt <= c["lo"]:
        return "verdeckt"
    if c.get("hi") is not None and alt >= c["hi"]:
        return "frei"
    return "?"


# ------------------------------------------------------------------ Aufstellungen --- #
def _conflicts(a: list[dict], b: list[dict]) -> tuple[int, int]:
    """(Widersprüche, gemeinsame Stützstellen mit Auskunft) zwischen zwei Karten."""
    bad = common = 0
    for x, y in zip(a, b):
        if (x["lo"] is None and x["hi"] is None) or (y["lo"] is None and y["hi"] is None):
            continue
        common += 1
        if (x["lo"] is not None and y["hi"] is not None and y["hi"] < x["lo"] - CONFLICT_TOL) or \
           (y["lo"] is not None and x["hi"] is not None and x["hi"] < y["lo"] - CONFLICT_TOL):
            bad += 1
    return bad, common


def _usable(points: list[dict]) -> list[dict]:
    """Für das Lernen: Zweifel weg; verdeckt aus Zielen/Aufnahmen bleibt (wird über Wiederholung sicher)."""
    return [p for p in points if p.get("res") in ("frei", "verdeckt")]


def setups(nights: dict[str, list[dict]]) -> list[dict]:
    """Nächte zu Aufstellungen zusammenfassen. Rückgabe [{id, nights, points, cols, sure_bins}] – älteste Aufstellung zuerst.
    Verdeckt zählt in der Aufstellung nur, wenn es aus einem sicheren Scan stammt oder in ≥ 2 Nächten vorkommt."""
    groups: list[dict] = []
    for night in sorted(nights):
        pts = _usable(nights[night])
        cols = columns(pts)
        info = sum(1 for c in cols if c["lo"] is not None or c["hi"] is not None)
        if info == 0:
            continue
        best = None
        for g in groups:
            bad, common = _conflicts(cols, g["cols"])
            if bad == 0 and common >= MIN_COMMON and (best is None or common > best[1]):
                best = (g, common)
        if best is None:
            g = {"nights": [], "points": []}
            groups.append(g)
        else:
            g = best[0]
        g["nights"].append(night)
        g["points"] += [{**p, "night": night} for p in pts]
        g["cols"] = columns(g["points"])
    out = []
    for k, g in enumerate(groups):
        pts = _confirmed(g["points"])
        cols = columns(pts)
        out.append({"id": f"a{k + 1}", "nights": g["nights"], "points": pts, "cols": cols,
                    "last": max(g["nights"]), "first": min(g["nights"])})
    return out


def _confirmed(points: list[dict]) -> list[dict]:
    """Verdeckt aus Zielen/Aufnahmen (unsicher) nur behalten, wenn eine andere Nacht in derselben Richtung auch verdeckt war."""
    out = []
    for p in points:
        if p["res"] == "verdeckt" and not p.get("sure"):
            again = any(q is not p and q["res"] == "verdeckt" and q.get("night") != p.get("night")
                        and _d(q["az"], p["az"]) <= BLOCK_SPREAD and abs(q["alt"] - p["alt"]) <= 10 for q in points)
            if not again:
                continue
            p = {**p, "sure": True}
        out.append(p)
    return out


def site_wide(groups: list[dict]) -> list[float | None]:
    """Je Stützstelle die Grenze, die in allen Aufstellungen (mindestens zwei) gleich ist – ferne Hindernisse; sonst None."""
    if len(groups) < 2:
        return [None] * N
    out = []
    for i in range(N):
        vals = [limit_of(g["cols"][i]) for g in groups if g["cols"][i]["lo"] is not None]
        out.append(round(min(vals), 1) if len(vals) == len(groups) and max(vals) - min(vals) <= FAR_TOL else None)
    return out


def probes_to_tell(groups: list[dict], floor: float, most: int = 3) -> list[dict]:
    """Wenige Peilpunkte, die die Aufstellungen am besten unterscheiden (gierig: je Punkt möglichst viele Paare, deren
    Erwartung dort verschieden ist). Bei nur einer Aufstellung: zwei Punkte knapp über ihren höchsten Grenzen (Bestätigung)."""
    cands = []
    for i in range(N):
        az = _bin_az(i)
        for alt in range(int(max(floor, 10)), 61, 5):
            exp = [state_at(g["cols"], az, alt) for g in groups]
            cands.append({"az": az, "alt": float(alt), "exp": exp})
    if len(groups) == 1:
        cols = groups[0]["cols"]
        ranked = sorted((c for c in cols if c["lo"] is not None and c["hi"] is not None), key=lambda c: -c["lo"])
        out = []
        for c in ranked:
            if all(_d(c["az"], o["az"]) > 40 for o in out) and c["hi"] >= floor:
                out.append({"az": c["az"], "alt": max(floor, c["hi"]), "why": "erkennen"})
            if len(out) >= 2:
                break
        return out
    pairs = {(a, b) for a in range(len(groups)) for b in range(a + 1, len(groups))}
    out = []
    while pairs and len(out) < most:
        def gain(c):
            return sum(1 for a, b in pairs if "?" not in (c["exp"][a], c["exp"][b]) and c["exp"][a] != c["exp"][b])
        best = max(cands, key=gain, default=None)
        if best is None or gain(best) == 0:
            break
        out.append({"az": best["az"], "alt": best["alt"], "why": "erkennen"})
        pairs = {(a, b) for a, b in pairs if not ("?" not in (best["exp"][a], best["exp"][b]) and best["exp"][a] != best["exp"][b])}
        cands = [c for c in cands if _d(c["az"], best["az"]) > 20]
    return out


def match(groups: list[dict], probes: list[dict]) -> dict | None:
    """Aufstellung, die zu den gemessenen Erkennungspunkten passt (keine falsche Erwartung, die meisten Treffer)."""
    best = None
    for g in groups:
        hits = 0
        ok = True
        for p in probes:
            if p.get("res") not in ("frei", "verdeckt"):
                continue
            e = state_at(g["cols"], p["az"], p["alt"])
            if e == "?":
                continue
            if e != p["res"]:
                ok = False
                break
            hits += 1
        if ok and hits and (best is None or hits > best[1] or (hits == best[1] and g["last"] > best[0]["last"])):
            best = (g, hits)
    return best[0] if best else None


def edges(cols: list[dict], floor: float, most: int = 8) -> list[dict]:
    """Kurzscan einer bekannten Aufstellung: je Hindernis (alle 30°) ein Punkt knapp über der Grenze (soll frei sein) und
    einer knapp darunter (soll verdeckt sein)."""
    out = []
    for c in cols:
        if c["az"] % 30 or c["lo"] is None:
            continue
        lim = limit_of(c)
        up, down = lim + 3, c["lo"] - 2
        if up <= 70:
            out.append({"az": c["az"], "alt": max(floor, up), "why": "grenze", "expect": "frei"})
        if down >= floor:
            out.append({"az": c["az"], "alt": down, "why": "grenze", "expect": "verdeckt"})
    return out[:most]


# ------------------------------------------------------------------------ Ablage --- #
class SkyStore:
    """`<Daten>/telescope/himmel.json`: je Standort-ID die Peilpunkte je Nacht (nur Azimut/Höhe – keine Koordinaten)."""

    def __init__(self, path: Path | str | None):
        self.path = Path(path) if path else None
        self.lock = threading.RLock()
        self._data: dict | None = None

    def _load(self) -> dict:
        if self._data is None:
            try:
                self._data = json.loads(self.path.read_text(encoding="utf-8")) if self.path and self.path.exists() else {}
            except (OSError, ValueError):
                self._data = {}
            if not isinstance(self._data.get("sites"), dict):
                self._data = {"sites": {}}
        return self._data

    def _save(self):
        if not self.path:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._data, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self.path)
        except OSError:
            import traceback
            traceback.print_exc()

    def nights(self, site_id: str | None) -> dict[str, list[dict]]:
        with self.lock:
            return {k: list(v) for k, v in ((self._load()["sites"].get(site_id or "-") or {}).get("nights") or {}).items()}

    def put_night(self, site_id: str | None, night: str, points: list[dict]):
        """Punkte einer Nacht ersetzen (Scan + Ziele dieser Nacht)."""
        clean = [{k: p[k] for k in ("az", "alt", "res", "sure", "stars", "src", "t") if k in p} for p in points][-MAX_POINTS:]
        with self.lock:
            s = self._load()["sites"].setdefault(site_id or "-", {"nights": {}})
            s["nights"][night] = clean
            for old in sorted(s["nights"])[:-MAX_NIGHTS]:
                del s["nights"][old]
            s["t"] = time.time()
            self._save()

    def forget(self, site_id: str | None, night: str | None = None):
        with self.lock:
            s = self._load()["sites"].get(site_id or "-")
            if not s:
                return
            if night:
                s["nights"].pop(night, None)
            else:
                self._data["sites"].pop(site_id or "-", None)
            self._save()
