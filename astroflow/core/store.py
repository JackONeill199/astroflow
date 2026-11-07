"""Gemeinsame, dauerhafte Einstellungen aller Module in `data/settings.json` (ein Abschnitt je Thema)."""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path

log = logging.getLogger("astroflow.store")
RETRIES, RETRY_PAUSE = 6, 0.02


class SettingsError(RuntimeError):
    """`settings.json` ist beschädigt oder unlesbar – es wird nichts geschrieben, damit keine Einstellungen verloren gehen."""


class SettingsStore:
    """Thread-sichere JSON-Datei mit Abschnitten. In der App geänderte Werte haben Vorrang vor Umgebungsvariablen.
    Achtung: API-Schlüssel stehen hier im Klartext – die Datei liegt im (per .gitignore ausgeschlossenen) Datenordner."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.RLock()
        self.problem: str | None = None      # letzte Meldung zu einer defekten Datei (für die Oberfläche)

    def _read(self) -> dict:
        """Fehlende Datei = leer. Unlesbare oder ungültige Datei: nie still als leer behandeln (das nächste Speichern
        würde alle anderen Abschnitte löschen) – Kopie als `settings.json.defekt-<zeit>`, dann `SettingsError`."""
        last = None
        for attempt in range(RETRIES):
            try:
                raw = self.path.read_bytes()
                break
            except FileNotFoundError:
                return {}
            except OSError as e:                      # Windows: kurz gesperrt (Virenscanner, Sicherung)
                last = e
                time.sleep(RETRY_PAUSE * (attempt + 1))
        else:
            self._warn(f"settings.json nicht lesbar ({last}).")
            raise SettingsError(f"{self.path.name} ist nicht lesbar: {last}. Die Datei wurde nicht verändert.")
        try:
            data = json.loads(raw.decode("utf-8-sig"))
            if not isinstance(data, dict):
                raise ValueError("oberste Ebene ist kein Objekt")
            return data
        except ValueError as e:                       # auch UnicodeDecodeError
            if not raw.strip():                       # leer (Absturz mitten im Schreiben): nichts zu retten
                return {}
            keep = self.path.with_name(f"{self.path.name}.defekt-{time.strftime('%Y%m%d-%H%M%S')}")
            try:
                if not keep.exists():
                    keep.write_bytes(raw)
            except OSError:
                pass
            self._warn(f"{self.path.name} ist ungültig ({e}); Kopie: {keep.name}.")
            raise SettingsError(f"{self.path.name} ist beschädigt ({e}). Eine Kopie liegt als {keep.name} daneben; "
                                "die Datei wird nicht überschrieben. Bitte reparieren oder löschen.") from e

    def _warn(self, msg: str):
        self.problem = msg
        log.error("%s", msg)

    def get(self, section: str) -> dict:
        with self._lock:
            try:
                v = self._read().get(section)
            except SettingsError:                 # Lesen darf die App nicht stoppen: Standardwerte, Meldung steht im Protokoll
                return {}
            return dict(v) if isinstance(v, dict) else {}

    def update(self, section: str, values: dict) -> dict:
        """Werte eines Abschnitts ergänzen/überschreiben (`None` entfernt einen Schlüssel)."""
        with self._lock:
            data = self._read()
            sec = data.get(section) if isinstance(data.get(section), dict) else {}
            for k, v in values.items():
                if v is None:
                    sec.pop(k, None)
                else:
                    sec[k] = v
            data[section] = sec
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
            if self.path.exists():                # letzten guten Stand behalten
                try:
                    self.path.with_name(self.path.name + ".bak").write_bytes(self.path.read_bytes())
                except OSError:
                    pass
            for attempt in range(RETRIES):
                try:
                    os.replace(tmp, self.path)
                    break
                except PermissionError:
                    if attempt == RETRIES - 1:
                        raise
                    time.sleep(RETRY_PAUSE * (attempt + 1))
            self.problem = None
            return dict(sec)
