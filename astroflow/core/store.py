"""Gemeinsame, dauerhafte Einstellungen aller Module in `data/settings.json` (ein Abschnitt je Thema)."""
from __future__ import annotations

import json
import threading
from pathlib import Path


class SettingsStore:
    """Thread-sichere JSON-Datei mit Abschnitten. In der App geänderte Werte haben Vorrang vor Umgebungsvariablen.
    Achtung: API-Schlüssel stehen hier im Klartext – die Datei liegt im (per .gitignore ausgeschlossenen) Datenordner."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.RLock()

    def _read(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def get(self, section: str) -> dict:
        with self._lock:
            v = self._read().get(section)
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
            tmp.replace(self.path)
            return dict(sec)
