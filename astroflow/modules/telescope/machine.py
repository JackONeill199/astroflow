"""Zustandsautomat: welche Phase hat das Teleskop, welche Befehle sind darin erlaubt, welche Übergänge sind plausibel.

Die Phase wird aus dem gemeldeten Gerätezustand abgeleitet (Ist), nicht aus dem zuletzt gesendeten Befehl (Soll) – so
bleibt die Anzeige richtig, auch wenn die Handy-App oder der Geräte-Plan etwas bewegt. Einklappen ist aus jeder Phase
erlaubt (Not-Aus).
"""
from __future__ import annotations

from .driver import TelescopeState

LABELS = {
    "getrennt": "Nicht verbunden",
    "fremd": "Gesteuert von der App",
    "eingeklappt": "Eingeklappt",
    "oeffnet": "Öffnet",
    "geoeffnet": "Geöffnet",
    "richtet_aus": "Richtet sich ein",
    "bereit": "Bereit",
    "schwenkt": "Schwenkt",
    "astrometrie": "Bestimmt die Lage",
    "nimmt_auf": "Nimmt auf",
    "klappt_ein": "Klappt ein",
}

# Unplausible Übergänge (Ist → Ist) werden ins Protokoll geschrieben, nicht verhindert – das Gerät hat das letzte Wort.
# Zwischenschritte darf ein Takt überspringen (Öffnen dauert Sekunden); auffällig ist nur Bewegung ohne geöffneten Arm.
IMPLAUSIBLE = {("eingeklappt", "schwenkt"), ("eingeklappt", "astrometrie"), ("eingeklappt", "nimmt_auf"),
               ("klappt_ein", "schwenkt"), ("klappt_ein", "nimmt_auf"), ("geoeffnet", "nimmt_auf")}

# Befehle je Phase (park fehlt absichtlich: immer erlaubt)
COMMANDS = {
    "open": {"eingeklappt"},
    "init": {"geoeffnet", "bereit"},
    "observe": {"bereit", "nimmt_auf", "schwenkt", "astrometrie"},
    "device_plan": {"bereit", "nimmt_auf", "schwenkt", "astrometrie"},
    "stop": {"schwenkt", "astrometrie", "nimmt_auf"},
    "focus": {"nimmt_auf", "bereit"},
}


def phase_of(s: TelescopeState) -> str:
    if not s.connected:
        return "getrennt"
    if s.master == "other":
        return "fremd"
    if s.step == "parking":
        return "klappt_ein"
    if s.step == "opening":
        return "oeffnet"
    if s.parked:
        return "eingeklappt"
    if s.step == "init":
        return "richtet_aus"
    if not s.initialized:
        return "geoeffnet"
    return {"goto": "schwenkt", "astrometry": "astrometrie", "capture": "nimmt_auf"}.get(s.step, "bereit")


class Machine:
    def __init__(self):
        self.phase = "getrennt"

    def update(self, s: TelescopeState) -> tuple[str, str] | None:
        """Neue Phase übernehmen. Rückgabe (alt, neu) bei Wechsel, sonst None."""
        new = phase_of(s)
        if new == self.phase:
            return None
        old, self.phase = self.phase, new
        return old, new

    def plausible(self, old: str, new: str) -> bool:
        return (old, new) not in IMPLAUSIBLE

    def can(self, cmd: str) -> bool:
        return cmd == "park" or self.phase in COMMANDS.get(cmd, set())

    @property
    def label(self) -> str:
        return LABELS.get(self.phase, self.phase)
