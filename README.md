# AstroFlow

**Vollautomatische Astrofotografie für alle.** Vom FITS-Rohbild zum fertigen Astrofoto – lokal, im Browser, ohne manuelle Nachbearbeitung.

AstroFlow läuft vollständig lokal (Docker auf NAS/PC oder direkt mit Python). Du legst FITS-Aufnahmen
(Seestar, Stellina, Vespera oder klassische Astrokameras) in den Eingangsordner – den Rest erledigt die Software.

## Was automatisch passiert

| Schritt | Umsetzung |
|---|---|
| Erkennung & Gruppierung | Objekt aus Header (`OBJECT`, `RA/DEC`) oder Ordnername (Stellina `…_observation_M57`) → eingebauter Katalog (Messier, NGC/IC, …); Gruppierung nach Objekt + Kamera, Sessions nach Nacht; doppelt vorhandene Dateien (z. B. erneut hochgeladen) werden erkannt und nur einmal gestackt |
| Plate-Solving | Fehlen Objektname und Koordinaten, wird die Himmelsposition aus dem Sternmuster bestimmt (ASTAP, lokales astrometry.net oder optional nova.astrometry.net) – eine Aufnahme je Ordner/Nacht, Ergebnis gecacht |
| Mehrfilter (Mono-Kamera) | Filter aus `FILTER`-Header (L, R, G, B, Hα, OIII, SII); je Filter eigene Auswahl und eigener Stack auf gemeinsamer Referenz; automatische Kombination zu **RGB, LRGB, RGB+Hα, HOO, SHO** oder Mono |
| Kalibrierung | Master-Dark/Bias/Flat aus Kalibrierframes (auch Dark-Skalierung), passend nach Größe/Gain/Belichtung/Filter/Nacht; automatische Hotpixel-Korrektur (auch ohne Darks) |
| Sensorglühen | Ohne Darks misst AstroFlow das ortsfeste Randglühen des Sensors aus den Aufnahmen selbst und zieht es vor dem Ausrichten ab – sonst verschmiert es bei Alt-Az-Geräten (Stellina, Seestar) durch die Feldrotation zu Schlieren. Je Teleskop entsteht ein Glüh-Datensatz, der mit jeder Nacht genauer wird und auch nach dem Löschen der Einzelbilder erhalten bleibt |
| Qualitätsbewertung | Sternzahl, FWHM, Exzentrizität, Hintergrund → Score; Wolken, Unschärfe, Schlieren werden verworfen |
| Registrierung | Dreiecks-Matching: Verschiebung, Feldrotation (Alt/Az), Meridian-Flip; über mehrere Nächte |
| Stacking | Gewichtetes Sigma-Clipping (Satelliten/Flugzeuge/Hot-Pixel fliegen raus), Tausende Frames in RAM-sparenden Batches |
| Kontinuierliche Verbesserung | Stack-Zustand wird gespeichert; neue Aufnahmen werden **nachgestackt**, ohne alles neu zu rechnen |
| Speicher | Hochgeladene Einzelbilder werden nach erfolgreichem Stacking gelöscht (abschaltbar, `ASTROFLOW_DELETE_UPLOADS=0`); Nachstacken neuer Nächte geht weiter, nur komplettes Neu-Stacken nicht mehr. Eingebundene Ordner werden nie angetastet |
| Farbe | Fotometrische Farbkalibrierung wie PCC/SPCC: der Stack wird plate-gesolvt, der Weißabgleich richtet sich nach den Gaia-Farben der Sterne im Feld (sonnenähnliche Sterne = weiß). Nur die Feldkoordinaten gehen an das ESA-Gaia-Archiv; abschaltbar (`ASTROFLOW_GAIA=0`) |
| Sterne | Optional je Objekt (Optionen → „Sterne getrennt bearbeiten“): wie bei Siril/PixInsight mit StarNet werden Sterne vom Nebel getrennt (StarNet2, falls installiert, sonst eigene Trennung), der Nebel wird ohne Sterne gestreckt (GHS), die Sterne kommen klein zurück. Standard: aus – ohne Trennung wirkt das Bild knackiger |
| Bildbearbeitung | Gradientenentfernung, Farbkalibrierung über Sternfarben, Entfaltung, farberhaltender asinh/MTF-Stretch, Wavelet-Entrauschen, Sternreduktion, lokaler Kontrast, Sättigung – **profilabhängig** (Galaxie, Emissions-/Reflexionsnebel, Planetarischer Nebel, Haufen, …) |
| KI-Bildprüfung (optional) | Vision-LLM prüft das Ergebnis und korrigiert Parameter, bis es passt |
| Ausgabe | TIFF 16-bit, PNG, JPEG, linearer FITS-Stack; Verlauf aller Versionen |

## Schnellstart

### Docker

1. In `docker-compose.yml` den Pfad zu deinen FITS-Dateien eintragen.
2. `docker compose up -d --build`
3. Browser: <http://localhost:8080>

### Ohne Docker

```bash
python -m venv .venv && . .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python -m astroflow demo            # synthetische Testdaten nach ./data/input/demo
python -m astroflow serve           # Web-UI auf :8080
python -m astroflow process         # oder: alles direkt per CLI verarbeiten
```

Ordner: `data/input` (Rohdaten, rekursiv gescannt; `ASTROFLOW_INPUT` ändert das), `data/output` (Ergebnisse), `data/work` (Zwischenstände).
Dark-/Flat-/Bias-Frames werden am Header (`IMAGETYP`) oder an Ordner-/Dateinamen (`darks/`, `Flat_…`) erkannt.

## KI-Bildprüfer (LM Studio / OpenRouter)

Ein Vision-Modell bekommt eine Vorschau des fertigen Bildes und die aktuellen Parameter und beantwortet:
*„Sieht das so aus, wie es aussehen sollte?“* (Hintergrund, Farbstich, Sterne, Rauschen/Plastik-Look, Artefakte).
Bei Beanstandung liefert es konkrete Parameterkorrekturen (z. B. `target_bg`, `denoise`, `saturation`); AstroFlow
rendert neu und prüft erneut (bis `ASTROFLOW_REVIEW_ROUNDS`, Standard 3). Fällt der Prüfer aus, bleibt das letzte Bild gültig.
Das Modell darf nur eine feste Liste von Parametern in begrenzten Bereichen ändern – es führt keinen Code aus.

```yaml
ASTROFLOW_REVIEW_PROVIDER: lmstudio            # lmstudio | openrouter | off (Standard)
ASTROFLOW_REVIEW_MODEL: qwen2.5-vl-7b-instruct # ein Modell mit Bildeingabe
ASTROFLOW_REVIEW_URL: http://host.docker.internal:1234/v1   # optional, Standard je Anbieter
ASTROFLOW_REVIEW_KEY: sk-or-...                # nur OpenRouter
```

Oder in der Web-UI unter **KI-Prüfer** – dort gesetzte Werte (wie auch Plate-Solving und Speicher) werden in
`data/settings.json` gespeichert und überleben Neustarts; sie haben Vorrang vor den Umgebungsvariablen.
Denkmodelle (z. B. Qwen3, Bonsai) überlegen oft 2 000–3 000 Tokens vor der Antwort: Standardlimit daher
`ASTROFLOW_REVIEW_MAX_TOKENS=8000`, Zeitlimit `ASTROFLOW_REVIEW_TIMEOUT=300` s. **Datenschutz:** LM Studio bleibt komplett lokal. Bei OpenRouter wird die
verkleinerte Vorschau (max. 1280 px) an einen externen Dienst gesendet – deshalb ist der Prüfer standardmäßig aus.

## Plate-Solving

Viele Aufnahmen (Stellina, Astrokameras ohne Montierungsanbindung) enthalten weder Objektnamen noch Koordinaten.
AstroFlow löst dann je Ordner, Kamera und Nacht **eine** Aufnahme und ordnet alle Bilder der Gruppe dem erkannten Objekt zu.
Bildmaßstab-Hinweise aus dem Header (`FOCALLEN`/`XPIXSZ`, Stellina `FOCAL`/`PIXSZ`) beschleunigen das; ohne Hinweise wird blind gelöst.

- **Docker:** ASTAP + Sterndatenbank D05 sind im Image enthalten (für kleine Bildfelder < 0,6°: `docker compose build --build-arg ASTAP_DB=d50`).
- **Ohne Docker:** [ASTAP](https://www.hnsky.org/astap.htm) (ab 2023 – ältere Versionen kennen die D-Datenbanken nicht) und eine Sterndatenbank (D05, D20 oder D50) installieren.
  AstroFlow sucht ASTAP und Datenbank automatisch im ASTAP-Programmordner und – unter Windows ohne Admin-Rechte – in `%LOCALAPPDATA%\astap`
  (dort einfach `astap_cli.exe` aus dem Kommandozeilen-ZIP und die entpackte Datenbank ablegen).
- **Objektnamen:** Liegt ASTAPs `deep_sky.csv` (~30 000 Objekte, beim Windows-Installer dabei) vor, bekommen gelöste Felder echte Namen
  (z. B. „NGC 1579“) statt „Feld RA … Dec …“.
- Bereits als „Unbekanntes Objekt“ eingeordnete Aufnahmen: auf der Objektseite **„Jetzt bestimmen“** – sie werden danach ins richtige Projekt verschoben und nachgestackt.

```yaml
ASTROFLOW_SOLVER: auto        # auto (lokale Solver) | astap | astrometry | nova | off
ASTROFLOW_ASTAP: /opt/astap/astap_cli      # optional, sonst automatische Suche
ASTROFLOW_ASTAP_DB: /opt/astap             # optional, Ordner der Sterndatenbank
ASTROFLOW_NOVA_KEY: ...                    # nur für nova.astrometry.net
ASTROFLOW_SOLVE_TIMEOUT: 180
```

**Datenschutz:** ASTAP und astrometry.net laufen lokal. nova.astrometry.net wird nur genutzt, wenn er ausdrücklich gewählt ist;
dabei wird ein verkleinertes Graustufenbild (nicht öffentlich markiert) hochgeladen.

## Web-App

Die Oberfläche (<http://localhost:8080>) ist eine installierbare Web-App (PWA) für Desktop und Smartphone:

- **Galerie** aller Objekte mit Suche, Filter nach Objekttyp und Sortierung; Live-Fortschritt laufender Verarbeitungen
- **Objektseite** mit Zoom-/Pan-Betrachter (Mausrad, Ziehen, Pinch, Doppelklick = 100 %, Vollbild), Kanalübersicht (Belichtung je Filter), Details und KI-Prüfprotokoll
- **Aufnahmen**: Qualitätsdiagramm über alle Nächte, filter- und sortierbare Tabelle, Einzelbilder ein-/ausschließen
- **Versionen**: jede Verarbeitung bleibt erhalten; zwei Versionen per Schieberegler vergleichen
- **Optionen** je Objekt (Profil, Farbpalette, Bildauswahl, Auflösung, Schmalband, KI-Prüfung) mit „Speichern & verarbeiten“
- **Import** von Dateien oder ganzen Ordnern per Drag & Drop (überall auf der Seite) mit Upload-Fortschritt – Ordnernamen bleiben erhalten, **Aktivität** (Auftragsliste), **Einstellungen**

## Weitere Einstellungen

`ASTROFLOW_AUTO=0` (nicht automatisch verarbeiten), `ASTROFLOW_SCAN_INTERVAL`, `ASTROFLOW_THREADS` (Standard: Kerne − 1, max. 12), `ASTROFLOW_BATCH_MB` (RAM pro Stacking-Stapel, Standard 1/8 des Arbeitsspeichers bzw. des Docker-Limits, 700–4000 MB – auf kleinen NAS ggf. senken).
Pro Objekt in der UI: Profil, Farbpalette (`auto | RGB | LRGB | SHO | HOO | mono`), „beste X %“, 2×-Auflösung; einzelne Aufnahmen lassen sich ein-/ausschließen.

## Architektur

```
astroflow/
  fits_io.py      FITS lesen, Header, Debayering        scanner.py       Scan, Klassifikation, Gruppierung
  channels.py     Filter erkennen, Kanäle kombinieren   platesolve.py    ASTAP / astrometry.net / nova
  objects.py      Katalog + Profile                     calibration.py   Master-Frames
  quality.py      Sterne, Metriken, Auswahl             registration.py  Triangle-Matching + Warp
  stacking.py     inkrementelles Sigma-Clip-Stacking    processing.py    automatische Bildbearbeitung
  reviewer.py     KI-Prüfer (LM Studio/OpenRouter)      pipeline.py      Orchestrierung
  server.py       FastAPI + Worker + Watcher            static/          Web-UI
```

## Tests

```bash
pip install pytest && python -m pytest tests
```
Die Tests erzeugen synthetische Aufnahmen (Galaxie, Bayer-Sensor, Mono-Kamera mit LRGB- und SHO-Filtern, Feldrotation über zwei Nächte, Wolken-/Schlieren-/Satellitenframes, Darks/Flats) und prüfen die ganze Kette inkl. inkrementellem Nachstacken.

## Grenzen (v0.3)

- Objektkatalog ist klein (erweiterbar in `objects.py`); gelöste Felder ohne Katalogobjekt heißen „Feld RA … Dec …“.
- Die Stack-Fläche entspricht dem Referenzbild; Mosaike und stark versetzte Sessions werden nicht erweitert.
- Kein echtes Drizzle (2×-Option interpoliert); keine Komet-/Planeten-/Mond-Modi.
- Ohne Flats bleibt multiplikative Vignettierung teilweise sichtbar (additive Gradienten werden entfernt).

Lizenz: MIT
