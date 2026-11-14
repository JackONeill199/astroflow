"""Akku-Restzeit eines Teleskops: aus dem gemessenen Verlauf (gleitende Entladerate) bzw. vor der ersten Messung aus
Erfahrungswerten je Modell und Betriebsart. Beim Laden gibt es keine Restzeit („lädt“); ohne Akkuwert nichts.

Der Akkustand kommt in ganzen Prozent – eine Rate wird erst geschätzt, wenn der Verlauf lang genug ist und mindestens zwei
Prozentpunkte gefallen ist (sonst wäre sie reines Rauschen).
"""
from __future__ import annotations

from collections import deque

# Entladung in %/h – grobe Erfahrungswerte, ersetzt durch die gemessene Rate, sobald es eine gibt.
# aktiv = geöffnet (Motoren, Kamera, Heizung); ruhe = eingeklappt, nur verbunden.
DRAIN_PCT_H = {
    "vespera": {"aktiv": 10.0, "ruhe": 3.0},
    "stellina": {"aktiv": 18.0, "ruhe": 5.0},
}
DEFAULT_DRAIN = {"aktiv": 12.0, "ruhe": 4.0}
WINDOW_S = 45 * 60          # gleitendes Fenster für die gemessene Rate
MIN_SPAN_S = 10 * 60        # so lang muss der Verlauf mindestens sein …
MIN_DROP = 2                # … und so viele Prozentpunkte gefallen


def _family(model: str) -> str | None:
    m = (model or "").lower()
    return next((k for k in DRAIN_PCT_H if m.startswith(k)), None)


class BatteryEta:
    def __init__(self):
        self.samples: deque = deque()          # (t, Prozent) seit dem letzten Laden
        self.charging = False
        self.measured: float | None = None     # gemessene Entladung in %/h

    def update(self, t: float, level: int | None, charging: bool):
        if charging or level is None:
            if charging:
                self.samples.clear()
                self.measured = None
            self.charging = bool(charging)
            return
        if self.charging or (self.samples and level > self.samples[-1][1] + 1):   # gerade geladen → neuer Verlauf
            self.samples.clear()
            self.measured = None
        self.charging = False
        if not self.samples or self.samples[-1][1] != level or t - self.samples[-1][0] >= 60:
            self.samples.append((t, level))
        while self.samples and t - self.samples[0][0] > WINDOW_S:
            self.samples.popleft()
        self.measured = self._fit()

    def _fit(self) -> float | None:
        if len(self.samples) < 2:
            return None
        t0, l0 = self.samples[0]
        t1, l1 = self.samples[-1]
        if t1 - t0 < MIN_SPAN_S or l0 - l1 < MIN_DROP:
            return None
        n = len(self.samples)
        mt = sum(t for t, _ in self.samples) / n
        ml = sum(v for _, v in self.samples) / n
        var = sum((t - mt) ** 2 for t, _ in self.samples)
        if var <= 0:
            return None
        slope = sum((t - mt) * (v - ml) for t, v in self.samples) / var      # %/s, negativ beim Entladen
        return -slope * 3600 if slope < 0 else None

    def rate(self, model: str, active: bool) -> float:
        if self.measured:
            return self.measured
        d = DRAIN_PCT_H.get(_family(model) or "", DEFAULT_DRAIN)
        return d["aktiv" if active else "ruhe"]

    def seconds_left(self, level: int | None, model: str, active: bool, floor: float = 0.0) -> float | None:
        """Sekunden, bis der Akku auf `floor` % fällt; None ohne Akkuwert oder beim Laden."""
        if level is None or self.charging:
            return None
        return max(0.0, (level - floor) / self.rate(model, active) * 3600)

    def view(self, level: int | None, charging: bool, model: str, active: bool, floor: float) -> dict | None:
        if level is None and not charging:
            return None
        if charging:
            return {"charging": True}
        return {"charging": False, "empty_s": round(self.seconds_left(level, model, active)),
                "limit_s": round(self.seconds_left(level, model, active, floor)), "measured": self.measured is not None,
                "rate": round(self.rate(model, active), 1)}


def fmt_left(s: float) -> str:
    """„2 h 40 min“ / „35 min“ – für Protokoll und Anzeige."""
    m = int(round(s / 60))
    if m < 60:
        return f"{m} min"
    h, m = divmod(m, 60)
    m = int(round(m / 10) * 10)
    if m == 60:
        h, m = h + 1, 0
    return f"{h} h {m} min" if m else f"{h} h"
