"""Treiber-Vertrag der Teleskopsteuerung: ein normierter Zustand und wenige Befehle, gleich für Simulator und echte Geräte.

Kennungen des Geräts (Seriennummer, Namen verbundener Handys) und der Ort bleiben im Treiber – `TelescopeState` enthält
nur, was Automatik und Oberfläche brauchen. Fehler melden Treiber als `DriverError` mit fester deutscher Meldung.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field


# Version des Treiber-Vertrags für Plugins (plugins.py). Erhöhen, wenn sich Methoden, `TelescopeState` oder `Target` so ändern,
# dass bestehende Plugins nicht mehr passen; Plugins geben in plugin.json die Version an, für die sie gebaut sind.
CONTRACT = 1


class DriverError(RuntimeError):
    """Befehl abgelehnt oder Gerät nicht erreichbar (Meldung ist für Menschen, ohne Ausnahmetext)."""


@dataclass
class Target:
    id: str
    name: str
    ra: float          # Grad
    dec: float         # Grad

    def as_dict(self) -> dict:
        return asdict(self)


# Schritte einer Beobachtung (normiert; Vaonis: GO_TARGET, ASTROMETRY, CAPTURE …)
STEPS = ("idle", "opening", "parking", "init", "goto", "astrometry", "capture")


@dataclass
class TelescopeState:
    model: str = ""                    # „vespera“, „stellina“, „sim“
    simulated: bool = False
    connected: bool = False
    master: str = "none"               # "us" | "other" | "none" (wer steuert)
    parked: bool = True
    initialized: bool = False
    az: float | None = None            # Grad, Norden 0, Osten 90
    alt: float | None = None
    step: str = "idle"                 # siehe STEPS
    target: dict | None = None         # aktuelles Ziel {id, name, ra, dec}
    frames: int = 0                    # Einzelbilder des aktuellen Ziels
    stacked: int = 0
    rejected: int = 0
    stars: int | None = None           # Sterne im letzten Bild (falls bekannt)
    astrometry_failed: bool = False    # letzte Ausrichtung gescheitert
    battery: int | None = None         # %, None = unbekannt
    charging: bool = False             # hängt am Ladegerät
    storage_free: float | None = None  # Anteil 0..1
    raw_enabled: bool | None = None    # Rohbilder am Gerät speichern
    humidity: float | None = None
    temperature: float | None = None
    error: str | None = None           # letzter Fehlername des Geräts (unverändert)
    device_plan: bool = False          # Geräte-Plan läuft (Rückfall)
    age_s: float = 0.0                 # Alter der letzten Statusmeldung

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class PlanItem:
    """Ein Abschnitt der Nacht für das Gerät (für den Geräte-Plan als Rückfall)."""
    target: Target
    start: float
    end: float
    extra: dict = field(default_factory=dict)


class Driver:
    """Basisklasse (Vertrag `CONTRACT`). Plugins: Konstruktor `__init__(self, options: dict)`; `capabilities` sagt der
    Automatik, was das Gerät kann; ein Attribut `ip` (Adresse im Teleskop-WLAN) schaltet FTP-Import und Live-Bilder frei."""
    kind = "base"
    label = "Teleskop"
    capabilities: frozenset = frozenset()       # {"device_plan", "focus"}

    def connect(self) -> None: raise NotImplementedError
    def disconnect(self) -> None: raise NotImplementedError
    def take_control(self) -> None: raise NotImplementedError
    def release_control(self) -> None: raise NotImplementedError
    def state(self) -> TelescopeState: raise NotImplementedError
    def open(self) -> None: raise NotImplementedError
    def park(self) -> None:
        """Muss aus jedem Zustand gehen (laufende Beobachtung/Init vorher beenden)."""
        raise NotImplementedError
    def init(self, lat: float, lon: float) -> None:
        """Ausrichten (Auto-Init). Der Ort geht nur an das Gerät, nie ins Protokoll."""
        raise NotImplementedError
    def observe(self, target: Target, exposure_s: float, gain: int | None = None) -> None: raise NotImplementedError
    def stop(self) -> None: raise NotImplementedError
    def focus(self) -> None: raise NotImplementedError
    def preview(self) -> tuple[bytes, float] | None:
        """Optional (Fähigkeit \"preview\"): das fertige Vorschaubild des Geräts als JPEG-Bytes plus Zeitstempel (Unix-Sekunden),
        oder None, wenn es noch keins gibt. Nur lesen; ein Fehler (DriverError) heißt „gerade nicht verfügbar“. Ohne diese Methode
        rendert AstroFlow die Vorschau aus der zuletzt geholten Rohbild-Datei (`preview.py`)."""
        return None
    def start_device_plan(self, items: list[PlanItem], exposure_s: float) -> None:
        raise DriverError("Dieses Gerät kann keinen eigenen Plan ausführen.")
