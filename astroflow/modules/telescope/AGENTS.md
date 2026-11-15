# Teleskop-Modul (`modules/telescope`) – Kurzüberblick für Agenten

Stand 14.11.2026. Steuern echter Geräte nur über Treiber-Plugins; im Repo keine Anmelde-/Befehlslogik für Vaonis.
Konzept, Protokoll, Lizenzfrage und offene Proben: `docs/entwicklung/teleskopsteuerung.md`.

## Feste Regeln
- **Keine Anmelde- oder Befehlslogik für Vaonis im Repo** (Signaturschlüssel aus der App). Steuern nur über Treiber-Plugins
  (`plugins.py`, Vertrag `driver.CONTRACT`); das Vaonis-Steuer-Plugin ist privat und liegt außerhalb des Repos. Im Repo erlaubt:
  Status lesen ohne Anmeldung (`vaonis_read.py`) und anonymer FTP (`frames.py`, `captures.py`). Bibliothek/Schlüssel nie ins Repo.
- Plugins sind fremder Code: Aktivieren, Plugin-Ordner, Optionen, Geräteadresse und das **Wählen** einer echten Steuerung nur mit
  `require_local`. Bedienen (Verbinden, Start mit Bestätigung, Pause, Weiter, Sofort einklappen) ist für gekoppelte Geräte gewollt
  (Nutzerentscheidung 14.11.2026: „Steuer-Endpunkte nur am Rechner selbst oder von gekoppelten Geräten“, Sicherheit 2/3).
  Ein defektes Plugin darf AstroFlow nicht lahmlegen (feste Meldung, Simulator). Vertrag unverträglich geändert → `CONTRACT` erhöhen.
- **Am echten Gerät nur mit ausdrücklicher Freigabe des Nutzers**, jede Probe einzeln. Tests nur gegen `sim.SimDriver`.
- Ort nur an das Teleskop (`Driver.init`), nie in Protokoll, Status, Browser oder Dateien. Gerätekennungen bleiben im Adapter.
- **Bestätigung je Nacht** (echtes Gerät): durchgesetzt an genau einer Stelle, `Autopilot._cmd` vor jedem Bewegungsbefehl (`MOVES`: Öffnen,
  Ausrichten, GoTo/Beobachtung, Geräte-Plan, Fokus) gegen `current_night`. /start und /resume fragen sie über `night_and_confirm` ab (409 →
  Dialog in der Oberfläche). Einklappen/Stoppen sind nie gesperrt. Neue Bewegungswege nur über `_cmd`.
- **Abgelehnte Befehle** (`Autopilot._rejected`): kein roter Fehler je Takt, sondern ein Protokolleintrag und Backoff (10 s … 2 min);
  `DriverBusy` (Gerät öffnet/richtet sich selbst ein) ist kein Fehler. Erst nach `GIVE_UP` echter Fehler → einklappen, anhalten.
  Plugins werfen `DriverBusy` nur, wenn das Gerät laut Status wirklich beschäftigt ist, sonst `DriverError`. Einklappen nie verzögern.
- „Sofort einklappen“ (`Autopilot.emergency`) geht ohne Warteschlange und ohne Takt-Sperre; `estop` sperrt danach jede Bewegung.
- Sonnenmodus, WLAN-Band, Herunterfahren, Löschen, Firmware: im Adapter absichtlich nicht erreichbar.
- FTP am Teleskop nur lesen (`frames.FtpFrames`: LIST/RETR), lokale Pfade nur aus geprüften Namen (`safe_name`, `img-NNNN.fits`).

## Aufbau
`driver.py` Vertrag + `TelescopeState` · `sim.py` Simulator (Uhren, Szenarien) · `machine.py` Phasen/erlaubte Befehle ·
`autopilot.py` Takt, Wächter, Hindernis/Wolke, Plan aus dem Planer · `plugins.py` Treiber-Plugins · `vaonis_read.py` Status ohne
Anmeldung · `frames.py` Live-Rohbilder per FTP · `captures.py` Import vom Teleskop ·
`api.py` `/api/telescope/*` · `static/telescope.js|css` Ansicht `#/teleskop` + Einstellungen. Tests: `tests/test_telescope.py`.
Dienste: `sites`, `weather_forecast`, `scan_input` (Bilder-Modul, neu), `frame_quick_metrics` (Bilder-Modul: Messwerte je Live-Rohbild
für die Beschlag-Erkennung `fog.py`), `catalog_resolve`, `catalog_objects` – alle optional.
