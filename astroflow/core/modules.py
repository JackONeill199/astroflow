"""Modul-System: Jedes Fachmodul bringt API, Oberfläche, Einstellungen und Aktivitäten selbst mit.

Ein Modul ist ein Unterpaket von `astroflow.modules` mit einer Klasse `Module` (Unterklasse von `BaseModule`).
Der Kern (`astroflow.server`) lädt die Module, hängt ihre Router unter `/api/<id>` ein, liefert ihre statischen
Dateien unter `/m/<id>/` aus und fasst Status und Aktivitäten aller Module zusammen. Module sprechen nicht direkt
miteinander, sondern über benannte Dienste (`ctx.services`), z. B. stellt Stacking den Plate-Solver bereit,
und über Ereignisse (`ctx.on` / `ctx.emit`):
  stacking.stacked(project_id, result_id, name) – neue Aufnahmen gestackt und entwickelt; vor dem Löschen der Rohbilder
                                                  (hochgeladene werden direkt danach gelöscht).
  stacking.imported(project_id, result_id, name) – fertiges JPG/PNG/TIFF in die Galerie importiert (Sage, kein SkyTrace).
  stacking.asteroids(project_id, result_id, count) – Asteroiden in einem Bild nachgewiesen (Sage: Erfolge prüfen).
"""
from __future__ import annotations

import importlib
import os
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .config import Settings
from .store import SettingsStore

# Reihenfolge = Reihenfolge in Navigation, Übersicht und Einstellungen
AVAILABLE = ("stacking", "skytrace", "sage")


@dataclass
class Context:
    """Was der Kern jedem Modul mitgibt."""
    settings: Settings
    store: SettingsStore
    services: dict[str, Any] = field(default_factory=dict)
    listeners: dict[str, list[Callable]] = field(default_factory=dict)

    def on(self, event: str, fn: Callable):
        """Auf ein Ereignis eines anderen Moduls reagieren (z. B. `stacking.stacked`)."""
        self.listeners.setdefault(event, []).append(fn)

    def emit(self, event: str, **data):
        """Ereignis melden. Fehler eines Empfängers stören weder den Absender noch andere Empfänger.
        Empfänger kehren schnell zurück und lagern längere Arbeit in eigene Threads aus."""
        for fn in self.listeners.get(event, []):
            try:
                fn(**data)
            except Exception:
                traceback.print_exc()

    @property
    def data_dir(self) -> Path:
        return self.settings.data_dir

    def module_dir(self, mid: str) -> Path:
        p = self.settings.data_dir / mid
        p.mkdir(parents=True, exist_ok=True)
        return p

    def service(self, name: str):
        return self.services.get(name)


class BaseModule:
    id: str = ""
    name: str = ""
    description: str = ""
    icon: str = "star"               # Symbol-ID aus index.html (#i-…)
    scripts: tuple[str, ...] = ()    # relativ zu <modul>/static, ausgeliefert unter /m/<id>/
    styles: tuple[str, ...] = ()

    def __init__(self, ctx: Context):
        self.ctx = ctx

    # ---- Lebenszyklus -------------------------------------------------------
    def router(self):
        """FastAPI-`APIRouter` (wird unter /api/<id> eingehängt) oder None."""
        return None

    def start(self):
        """Hintergrund-Threads starten (nach dem Laden aller Module)."""

    def stop(self):
        """Hintergrund-Threads beenden."""

    # ---- Beiträge zur gemeinsamen Oberfläche ---------------------------------
    def status(self) -> dict:
        """Kurzstatus fürs Polling (klein halten – wird alle paar Sekunden abgefragt)."""
        return {}

    def activity(self, limit: int = 30) -> list[dict]:
        """Aufträge im gemeinsamen Format (neueste zuerst):
        {key, title, detail, state: queued|running|done|error|stopped, progress 0..1|None, started, finished, href}"""
        return []

    @property
    def static_dir(self) -> Path | None:
        p = Path(importlib.import_module(type(self).__module__).__file__).parent / "static"
        return p if p.is_dir() else None


@dataclass
class Loaded:
    id: str
    module: BaseModule | None
    enabled: bool
    error: str | None = None

    def info(self) -> dict:
        m = self.module
        cls = m.__class__ if m else None
        return {"id": self.id, "enabled": self.enabled, "available": m is not None, "error": self.error,
                "name": getattr(cls, "name", self.id) or self.id, "description": getattr(cls, "description", ""),
                "icon": getattr(cls, "icon", "star"),
                "scripts": [f"/m/{self.id}/{s}" for s in getattr(cls, "scripts", ())] if m else [],
                "styles": [f"/m/{self.id}/{s}" for s in getattr(cls, "styles", ())] if m else []}


def enabled_ids(store: SettingsStore) -> dict[str, bool]:
    """Aktivierte Module: Einstellungen (Abschnitt `modules`) > Umgebung `ASTROFLOW_MODULES` > alle."""
    env = os.environ.get("ASTROFLOW_MODULES")
    base = {m: (m in [x.strip() for x in env.split(",")]) if env else True for m in AVAILABLE}
    saved = store.get("modules")
    for m in AVAILABLE:
        if isinstance(saved.get(m), bool):
            base[m] = saved[m]
    base["stacking"] = True                                  # Kernmodul, nicht abschaltbar
    return base


def load_modules(ctx: Context, factory: Callable[[str], type] | None = None) -> list[Loaded]:
    """Importiert und instanziiert die aktivierten Module. Ein defektes Modul legt die App nicht lahm,
    es erscheint mit Fehlermeldung in den Einstellungen."""
    out = []
    for mid, on in enabled_ids(ctx.store).items():
        if not on:
            out.append(Loaded(mid, None, False))
            continue
        try:
            cls = factory(mid) if factory else importlib.import_module(f"astroflow.modules.{mid}").Module
            out.append(Loaded(mid, cls(ctx), True))
        except Exception as e:
            traceback.print_exc()
            out.append(Loaded(mid, None, True, f"{type(e).__name__}: {e}"))
    return out
