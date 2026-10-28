# AGENTS.md – AstroFlow

Kompakter Einstieg für KI-Agenten und neue Sessions. Nutzerdoku: `README.md`.

## Was ist das
AstroFlow: lokale, vollautomatische Astrofotografie-Pipeline (FITS-Rohbilder → fertiges Astrofoto), Web-App im Browser.
Zielgruppe: Smart-Teleskope (Seestar, Vespera, Stellina) und klassische Astrokameras (Farbe/Bayer und Mono mit Filtern).
Stack: Python 3.12+ · numpy/scipy/astropy · FastAPI + uvicorn · SQLite · Vanilla-JS-SPA ohne Build-Schritt. Lizenz MIT. Version 0.3.0.

**Sprache:** UI, README, Kommentare und Fehlermeldungen auf **Deutsch**. Der Nutzer schreibt Deutsch.

## Befehle
```bash
.venv/Scripts/python -m pytest -q tests          # Windows-venv liegt im Repo (.venv); ~30 s, muss grün sein
python -m astroflow serve --port 8080 --data ./data   # Web-UI
python -m astroflow process --data ./data             # alles per CLI verarbeiten
python -m astroflow demo ./data/input/demo            # synthetische Testdaten (Bayer, 2 Nächte, Darks/Flats)
docker compose up -d --build                          # Image enthält ASTAP + D05 (ARG ASTAP_DB=d05|d50)
```
Git-Repo: github.com/JackONeill199/astroflow (privat, Branch main; `data/`, `data_real/`, `.venv` per .gitignore ausgeschlossen). Kein Linter/Formatter konfiguriert – Stil des umliegenden Codes übernehmen (knapp, deutsche Kommentare).

## Ablauf (`pipeline.py`)
Zwei getrennte Engines: `Pipeline.stack()` (Schritte 1–6, Ergebnis = linearer Master-Stack in `work/p<id>/stack*`) und
`Pipeline.develop()` (Schritte 7–9, nur aus dem Master-Stack, braucht keine Rohdaten). `process()` = beides + Upload-Aufräumen;
Job-Option `{"mode": "develop"}` = nur neu entwickeln (UI: „Mehr → Neu entwickeln“, Optionen speichern ohne Stack-relevante Änderung).
Ausbauplan, Lücken und Risiken: `docs/PLAN.md`.
1. **Scan** (`scanner.py`): rekursiv FITS im Eingangsordner; Frame-ID = sha1(relPfad|Größe|mtime). Typ über `IMAGETYP` oder Ordner-/Dateinamen.
   Stellina-Ordner: `01-images-initial` (Beginn) **und** `02-images-adjust-focus`, `03-…` (normale Aufnahmen nach automatischem
   Nachfokussieren – bei NGC 6888 waren das 300 von 306 Frames) gehören beide dazu; früher fälschlich ausgeschlossen.
   Nacht = Datum (DATE-OBS − 12 h). Objekt: `OBJECT`-Header → Katalog (`objects.py`); sonst Ordnername (Stellina `…_observation_M57`);
   sonst RA/DEC → nächstes Katalogobjekt; sonst **Plate-Solving** (s. u.); sonst „Unbekanntes Objekt“.
   Projekt-Key = `objekt|kamera|BxH` → alle Filter einer Mono-Kamera landen im selben Projekt.
   **Duplikate:** Inhalts-Fingerabdruck (`scanner.fingerprint`: Größe + erste/letzte 64 KB) in `info.fp`; inhaltsgleiche Dateien werden
   `kind='duplicate'` (nie gestackt). Altbestand ohne Fingerabdruck wird beim nächsten Scan nachgetragen und nachträglich bereinigt
   (behalten: gestackter bzw. zuerst eingelesener Frame; war das Duplikat gestackt → `restack=1`).
2. **Analyse** (`quality.py`, parallel): Sterne, FWHM, Exzentrizität, Hintergrund, Rauschen, `snr`, `grad` (Hintergrundgradient in
   Rauscheinheiten), `trails` → `frames.metrics`. `snr`/`grad` fließen in den Score, wenn alle Frames sie haben (ältere Analysen nicht).
   `detect_trails` erkennt die Demo-Satellitenspur noch NICHT zuverlässig (offener Punkt) – entfernt werden Spuren ohnehin per Clipping. Nur neue Frames – oder solche
   mit `metrics.mv < quality.METRICS_VERSION` (dann auch automatisch Neu-Stacken). **Bei jeder Änderung an Analyse/Kosmetik `METRICS_VERSION` erhöhen.**
   Sternerkennung verwirft Einzelpixel-Spitzen (Nachbarn < 12 % des Peaks).
3. **Auswahl**: Ausreißer je **Filter-Band** verwerfen (Wolken, Unschärfe, Nachführfehler); manuelle Ein-/Ausschlüsse (`frames.manual` ±1) gewinnen.
4. **Kalibrierung** (`calibration.py`): Master-Dark/Flat/Bias passend nach Größe/Gain/Belichtung/Filter/Nacht, gecacht in `work/calibration`.
   Danach immer `cosmetic()`: Hot-/Cold-Pixel je CFA-Farbebene durch Nachbar-Median ersetzen (Sterne geschützt: hellster Nachbar muss dunkel sein).
   Grund: Ohne Darks richtete die Registrierung Stellina-Aufnahmen an den ortsfesten Hotpixeln aus → Sterne mehrfach im Stack (behoben, Test `test_hotpixels.py`).
   `cosmetic` rechnet den Nachbar-Median nur an Kandidaten (exakter Vorfilter, „oberer Median“ wie ndimage) – bitgleich zur alten Version, 5× schneller.
5. **Stacking** (`registration.py` Dreiecks-Matching + `stacking.py` gewichtetes Sigma-Clipping): **inkrementell** – Zustand
   (`sw/swx/swx2.npy` + `state.json`) in `work/p<id>/stack` (Farbe) bzw. `stack_<Band>` (Mono-Filter). Neue Frames werden nur nachgestackt;
   `projects.restack=1` erzwingt Neuaufbau (wird nach dem Zurücksetzen sofort auf 0 gesetzt).
   **Checkpoints:** `_stack(save_dir=…)` speichert den Zustand spätestens alle `CHECKPOINT_S` (120 s) und am Ende; Frames werden erst
   danach `stacked=1`. `StackState.save` ist atomar (`.tmp` → Tausch, `.old` als Rückfall beim Laden). Alle Bänder registrieren auf **eine** gemeinsame Referenz; Flussnormierung je Band relativ (`flux_k0`).
   **Performance** (gemessen, 80 Stellina-Frames 3072×2080, 16 Kerne: gesamt 267 s → 44 s, Stacking 245 s → 29 s):
   Warp-Threads = `settings.threads` (ndimage gibt den GIL frei), nächster Stapel wird vorbereitet während `add_batch` läuft (doppelte Pufferung),
   `add_batch` arbeitet in Zeilenstreifen parallel, Stapelgröße aus RAM-Budget (`ASTROFLOW_BATCH_MB`, Standard RAM/8, 700–4000 MB; Docker-Limit wird beachtet).
   Clipping: Streuung nur einmal aus allen gültigen Werten schätzen, κ = 3/3 – die alte Variante (Streuung neu aus beschnittenen Werten, κhi 2,5)
   hob bei großen Stapeln das Rauschen um 4,5 %. Messskript-Idee: gleiche Frames vorher/nachher stacken, Hintergrundrauschen + Sternfluss vergleichen.
6. **Kanäle** (`channels.py`): L/R/G/B/Ha/OIII/SII → RGB, LRGB, RGB+Hα, HOO, SHO oder Mono (Option `palette`).
7. **Develop Engine** (`develop.py`): `measure()` (Objekt-SNR, Signalfläche, Dynamik auf gebinnter Vorschau) → `adapt()` passt
   Profilwerte an (Entrauschen, `linear_denoise` = (10/SNR)^1,5, Sättigung, asinh, keine Entfaltung bei wenig Signal) → bis zu 3 Renderings
   in voller Auflösung mit `assess()` (Hintergrund, Farbstich, Farbrauschen [Bandpass 2–12 px], Restgradient, Clipping, Sättigung, Signal)
   und `corrections()`; Restgradient wird nur gemeldet (höherer Polynomgrad trägt Nebel ab). Vom Nutzer gesetzte `params` sind tabu
   (`fixed`). Ergebnis in `stats.quality` (Score, Probleme, `hints`) und `stats.qc` (Verlauf). Schwellen kalibriert an NGC 1579, M3, M74,
   IC 405, M57 (gute Bilder: Farbrauschen 0,0035–0,006; fleckig ab ~0,009).
   **Bearbeitung** (`processing.py`, Reihenfolge in `process()`): Sättigungsmaske → Farbkanäle auf Grün ausrichten (`align_channels`,
   atmosphärische Dispersion bei Alt-Az-Geräten) → Polynom-Gradient → `remove_edge_glow` (steiles Randglühen, nur in 15-%-Randbändern,
   plus 2D-Eckenkarte) → Polynom Grad 2 → `trim_bad_edges` (max. 5 % je Seite) → Farbkalibrierung über Sterne → gesättigte Kerne neutral →
   Entfaltung, `denoise_linear` (vor dem Stretch, holt schwaches Signal), asinh/MTF-Stretch, Wavelet-Entrauschen, Sternreduktion, Kontrast, Sättigung → `tame_star_color` (Farbsäume in Halos heller
   Sterne; Nebelkerne über Formfilter geschützt) → `neutral_display_background`. `want_before=True` liefert `stats["before"]`
   (gleicher Ausschnitt, nur linear gestreckt) → `<base>_before.jpg` für den Vorher-Nachher-Vergleich. Parameter aus Profil je Objekttyp (`objects.PROFILES`).
   Achtung Reihenfolge: Randglühen **vor** dem ersten Polynom zu korrigieren wurde getestet und war deutlich schlechter.
   Polynom-Fit ohne die äußeren 12 % (`EDGE_EXCLUDE`) – sonst schwingt es beim Stellina-Randglühen über → dunkle Bänder im Bild.
   **Stellina-Vergleich (NGC 6888, Stellina-Fertigbild vom Nutzer):** Unser linearer Stack ist gleich gut (FWHM 5,5 vs. 5,45 px). Der Unterschied
   lag in der Entwicklung: (1) Hintergrund 0,12 statt ~0,03 → grau, Sterne wirkten zahlreicher/größer; (2) der alte **farberhaltende**
   Stretch ließ helle Sterne als große gesättigte rote/blaue Scheiben mit Ringen erscheinen. Jetzt Standard: Kurve **je Kanal**
   (`_stretch_channels`, Sternkerne → Weiß; alt per Parameter `color_preserve`), Profil-`target_bg` 0,06–0,08 (= Helligkeit schwachen Signals),
   danach `darken_background` auf `bg_dark` = 0,03 (Schwarzpunkt anheben; Nutzer wünscht dunklen Himmel – natürlicher, Artefakte
   weniger sichtbar). Ein niedrigeres `target_bg` statt Schwarzpunkt machte Nebel und Sterne mit dunkel – getestet, schlechter. QC-`BG_RANGE` 0,02–0,10.
   **Sternentrennung + GHS** (`stars.py`, Profil `star_separation` auto|starnet|intern|off, `star_brightness`): linear trennen
   (StarNet2-CLI, falls gefunden: `ASTROFLOW_STARNET`/PATH/`%LOCALAPPDATA%\starnet`; sonst eigene Trennung: Top-Hat runder kompakter
   Quellen, Füllen per Öffnung + Versatzausgleich, nie heller als das Original, sternlos + Sterne = Original). Sternlos: `Stretch` =
   automatische GHS-Folge (3 Durchgänge, SP am Himmelshintergrund, D ≤ 25, Rest per MTF; Weißpunkt vom Bild **mit** Sternen).
   Sterne: `channel_curve(img) − channel_curve(sternlos)` (knackig, Kerne weiß), sättigen, × `star_brightness`, Screen-Mischung.
   **Standard seit 2026-10-28: aus** (alle Profile `off`) – der Nutzer verglich NGC 7000 und fand die Trennung (eigenes Verfahren)
   matschiger und kontrastärmer; gewählt: ohne Trennung + Gaia-Farbe. Je Objekt per Option `star_separation` (auto/intern) zuschaltbar.
   Bei Sternhaufen brannte die Trennung den Kern aus (M3). Ohne Trennung: `channel_curve`.
   `darken_background` mit weichem Fuß (hartes Abschneiden ließ dunkle Nebelpartien schwarz werden).
   **Fotometrische Farbkalibrierung** (`photometry.py`, `Pipeline._photometric`, Einstellung `gaia` / `ASTROFLOW_GAIA`): Master-Stack mit
   `platesolve.solve_image` lösen (WCS auf vollem Pixelraster, Cache `work/p<id>/wcs.json` je Referenzbild), Gaia-DR3-Farben BP−RP
   über das ESA-Gaia-Archiv (TAP/ADQL; VizieR als Ausweich – VizieR war beim Nutzer nicht erreichbar), Cache `work/gaia/`.
   Zuordnung ≤ max(3 px, FWHM), Aperturphotometrie, robuste Gerade log(R/G), log(B/G) gegen BP−RP, Weiß = G2V (0,82).
   Ergebnis als `params.wb_gains` + `_pcc`; ohne Solver/Netz/Sterne → Sternmittel mit Grund in `stats.color_cal`.
   Stellina-Werte (5 Objekte): R ≈ 0,97, B ≈ 0,88, Streuung ~3 %. Tests offline (`tests/conftest.py` setzt ASTROFLOW_GAIA=0).
   **StarNet** optional: Einstellungen → „ZIP installieren“ (`stars.install_zip` → `data/tools/starnet`, Pfadprüfung, Selbsttest)
   oder Pfad (`settings.json` → `starnet.path`). Kein automatischer Download: Lizenz „all rights reserved“, Download-Links nur per JS.
   **Sensorglühen** (Schritt 0 in `stack()`, `Pipeline._glow`, `calibration.GlowLibrary`): ortsfestes Randglühen (Stellina oben/unten
   80–170 ADU) verschmiert sonst durch die Feldrotation zu Schlieren. Je Kamera (Gerät+Format+Gain+Belichtung+Filter) und Nacht:
   Zeilen-/Spaltenprofile (30-%-Perzentil je CFA-Ebene) aus ≤ 30 Lights, Median → `work/glow/<key>.npz` (wächst mit jeder Nacht,
   bleibt nach dem Löschen der Rohbilder). Abzug in `_load` nur in den Randbändern (`GLOW_BAND` 25 %, Mitte unberührt), nur ohne
   echte Darks. Nacht mit < 12 Frames → Median aller Nächte der Kamera. Info je Nacht in `projects.options.glow_info`;
   abschaltbar per Projektoption `sensor_glow`. Gemessen (135 IC-405-Frames): Randglühen im Stack 5,9σ/3,6σ → 0,1σ/0,6σ.
   Schnelles Iterieren: `StackState.load(work/p<id>/stack).result()` → `processing.process(...)` (~10 s), kein Neu-Stacken nötig.
8. **KI-Prüfer** (`reviewer.py`, optional, Standard aus): Vision-LLM (LM Studio/OpenRouter) bewertet Vorschau, darf nur Whitelist-Parameter in festen Grenzen ändern.
   Läuft nach der messbaren Kontrolle (`first=` = bereits gerendertes Bild). Denkmodelle (Bonsai, Qwen3) brauchen ~2 500 Tokens vor der
   Antwort → `max_tokens` 8000, Timeout 300 s; `<think>`-Blöcke und `reasoning_content` werden behandelt. Beim Nutzer: LM Studio, `prism-ml/bonsai-27b` (~55 s/Runde).
9. **Ausgabe**: TIFF16/PNG/JPG/Thumb + linearer FITS in `output/<Objekt>/`, Zeile in `results` (Versionen). Bild wird beim Speichern vertikal gespiegelt (Norden oben).
10. **Speicher** (`Settings.delete_uploads`, Env `ASTROFLOW_DELETE_UPLOADS`, Standard an, UI: Einstellungen → Speicher): nach erfolgreichem
   Durchlauf löscht `Pipeline.delete_stacked_uploads` hochgeladene Lights (nur unter `input/uploads`, gestackt oder verworfen) → `frames.deleted=1`,
   `frames.size`. Hochgeladene Duplikate werden beim Scan sofort gelöscht. Folgen: kein komplettes Neu-Stacken mehr (Pipeline wirft klare
   `ValueError`, API 409, UI sperrt Menüpunkt/Auswahl/Auflösung/Ein-/Ausschließen); gelöschte Frames behalten Status und Analyse und werden nie
   reanalysiert; Nachstacken neuer Nächte und Bearbeitung laufen normal (nur Stack + `state.json`-Referenzsterne nötig). Eingebundene Ordner
   und Kalibrierbilder werden nie gelöscht. DB-Spalten kommen über `db.MIGRATIONS` (einfaches ALTER TABLE beim Start).

## Plate-Solving (`platesolve.py`)
- Backends: `astap` (empfohlen) · `astrometry` (lokales `solve-field`) · `nova` (Online, **nie** automatisch – Datenschutz). `auto` = verfügbare lokale.
- Gelöst wird **eine Aufnahme je Gruppe** (Ordner + Kamera + Bildgröße + Nacht), Ergebnis in Tabelle `solves` gecacht; Fehlschläge werden nicht
  bei jedem Scan wiederholt (erst nach Speichern der Solver-Einstellungen oder per „Jetzt bestimmen“ → `Pipeline.solve_project`, verschiebt Frames ins richtige Projekt).
- Gelöst wird eine gebinnte 16-bit-Kopie in `work/solve` – Originale werden nie verändert. Maßstab-Hinweise: `FOCALLEN`/`XPIXSZ`, Stellina `FOCAL`/`PIXSZ`, bekannte Smart-Teleskope.
- ASTAP-Suche: Settings → PATH → `%LOCALAPPDATA%\astap\astap_cli.exe` → `C:\Program Files\astap` → `/opt/astap`. Datenbank (`*_0101.*`) ebenso; `-d` wird automatisch gesetzt.
- D-Datenbanken (D05/D20/D50) brauchen ASTAP ≥ 2023. Objektnamen für gelöste Felder: ASTAPs `deep_sky.csv` (~28 000 Objekte) via `objects.nearest_extended`.

## Daten & Konfiguration
- Datenordner (`--data` / `ASTROFLOW_DATA`, Standard `./data`): `input/` (Rohdaten, rekursiv), `work/` (Stacks, Master, Solve-Temp), `output/`, `astroflow.db`.
- SQLite-Tabellen: `projects`, `frames` (`info`/`metrics` als JSON), `jobs`, `results`, `solves`. Neue Spalten in `db.MIGRATIONS` eintragen
  (werden beim Start per `ALTER TABLE` ergänzt). Neue Felder sonst lieber ins JSON (`FrameInfo` ist ein Dataclass, `to_dict()` → `frames.info`).
- Projektoptionen (`projects.options` JSON, Defaults `pipeline.DEFAULT_OPTIONS`): `profile`, `palette`, `keep_percent`, `resolution_scale`, `narrowband`, `review`, `params`.
- Env: `ASTROFLOW_DATA/INPUT/OUTPUT`, `ASTROFLOW_AUTO`, `ASTROFLOW_SCAN_INTERVAL`, `ASTROFLOW_THREADS`, `ASTROFLOW_BATCH_MB`,
  `ASTROFLOW_REVIEW_PROVIDER/MODEL/URL/KEY/ROUNDS`, `ASTROFLOW_SOLVER`, `ASTROFLOW_ASTAP`, `ASTROFLOW_ASTAP_DB`, `ASTROFLOW_NOVA_KEY`,
  `ASTROFLOW_SOLVE_TIMEOUT`, `ASTROFLOW_DEEPSKY`, `ASTROFLOW_REVIEW_MAX_TOKENS/TIMEOUT`. In der UI geänderte Reviewer-/Solver-/Speicher-
  Einstellungen landen in `data/settings.json` (`server.PERSIST`) und haben Vorrang vor Env (API-Schlüssel dort im Klartext).

## Server (`server.py`)
Ein Worker-Thread (Job-Queue, `jobs`-Tabelle; Option `{"action":"solve"}` = Plate-Solving-Job) + Watcher-Thread (Scan alle N s, Auto-Verarbeitung).
`Pipeline._scan_lock` serialisiert Scan und Nach-Solving (vorher gab es Doppel-Inserts). Beim Start werden unterbrochene Jobs wieder eingereiht (Stacking setzt am letzten Checkpoint an).
Objekt löschen (`DELETE /api/projects/{id}` → `Pipeline.delete_project`, 409 während eines Jobs): Ergebnisse, Stack, Jobs, hochgeladene
Rohbilder weg; Frames aus eingebundenen Ordnern bleiben als `kind='ignored'` in der DB (sonst legt der nächste Scan das Objekt neu an),
`_fingerprints` überspringt sie, damit ein erneuter Upload normal verarbeitet wird. UI: Papierkorb auf der Galerie-Karte + „Mehr → Objekt löschen“.
REST unter `/api`: `status`, `scan`, `projects[/{id}]` (GET/PATCH/DELETE), `projects/{id}/process|solve|frames`, `frames/{id}` (PATCH manual),
`jobs`, `upload` (behält bis zu 4 Ordnerebenen, blockt `..`), `results/{id}/{tif|png|jpg|thumb|fits|before}`, `reviewer`, `solver`, `storage`.

## Web-UI (`astroflow/static/`)
`index.html` + `app.js` (Hash-Routing: `#/`, `#/p/<id>/<bild|aufnahmen|versionen|optionen>`, `#/activity`, `#/import`, `#/settings`) + `style.css` (Tokens, Dark/Light) + PWA-Manifest.
Polling alle 2,5 s, Ansichten zeichnen nur bei geänderter Signatur neu (Formulare/Zoom bleiben erhalten). Bildbetrachter mit Zoom/Pan/Pinch,
Versionsvergleich und Vorher/Nachher (`createViewer(host, src, cmpSrc, labels)`); Panel „Bildqualität“ (`qualityPanel`) aus `stats.quality`.
Achtung: in `createViewer` keine lokale Variable `esc` – überschattet die globale Escape-Funktion (TDZ-Fehler, Viewer bleibt leer).
Seit v0.5 (UI): Ansichten dürfen `navigate(path)` (Tabwechsel ohne Neuladen) und `actions()` (Einträge für die Befehlspalette Strg+K) liefern.
Globale Job-Anzeige oben (`showJobPill`, pollt `/jobs` nur bei laufenden Jobs) + Toast mit Aktion beim Fertigwerden (`toast(msg, kind, {label, href|fn})`).
Galerie aktualisiert Karten einzeln (Hash je Karte) statt neu zu zeichnen – sonst flackern Bilder bei jedem Poll. `createViewer(…, ph)` = Thumb als Blur-Vorschau.
Tastenkürzel: `/` Galerie-Suche, `1–4` Tabs, `F` Vollbild, `B` Vorher/Nachher. Server schickt `Cache-Control: no-cache` für `/` (sonst alte `?v=`).
**Nach Änderungen an statischen Dateien `?v=` in `index.html` hochzählen** (Browser-Cache).

## Tests (`tests/`)
Synthetische Daten aus `demo.py` (`generate`, `generate(anonymous=True)` ohne Koordinaten, `generate_mono` mit Filtern).
Plate-Solving-Tests nutzen eine ASTAP-Attrappe (Python-Skript, schreibt echtes `.ini`-Format) über `SolverConfig(cmd_prefix=[...])` – kein echtes ASTAP nötig.

## Stolperfallen (Windows-Umgebung des Nutzers)
- Shell ist Git-Bash: In Heredocs/`python -c` werden Backslashes umgedeutet (`\a` wurde zu BEL). Windows-Pfade in Skripten → Skript per Datei schreiben, raw strings.
- Port 8765 belegt ein anderes Programm des Nutzers; Vorschau-Server landen auf 8766/8767.
- Python-Dateien explizit mit `encoding="utf-8"` schreiben (Standard ist cp1252).
- ASTAP beim Nutzer: alte CLI (2022) in `C:\Program Files\astap` (nicht beschreibbar, hat `deep_sky.csv`), aktuelle `astap_cli.exe` + D05 in `%LOCALAPPDATA%\astap`.

## Echte Daten des Nutzers / laufende Instanz
- **Arbeitsinstanz:** Datenordner `astroflow/data/` (seit 2026-10-27; vorher Temp – umgezogen, DB-Pfade umgeschrieben, Sicherung `data/astroflow.db.vor-umzug`).
  Start über `.claude/launch.json` im Elternordner (`Claude Code/.claude`), Konfiguration „astroflow“, Port 8766, `--data …/astroflow/data`.
  **Nie Testdaten in diese Instanz schreiben** – Experimente in eigenem Datenordner (z. B. Scratchpad) mit kopierten Frames.
- Projekte: NGC 1579 (422 Stellina-Frames, 404 gestackt, per ASTAP gelöst), M3, M74, IC 405, M39 – Rohdaten nach dem Stacking gelöscht,
  also nur noch Nachstacken neuer Nächte und Neu-Entwickeln möglich.
- `data_real/` (im Projekt): ältere Stellina-M57-Session (Stack + Ergebnisse); `reproc.py` = Ad-hoc-Skript zum Nachbearbeiten.
- Datenordner beim Verschieben: Pfade stehen absolut in `frames.path`, `frames.info.path`, `results.files`, `solves.key` → alle umschreiben.
  Frame-IDs hängen am Pfad relativ zum Eingangsordner – bleibt die Struktur unter `input/` gleich, werden Dateien nicht neu eingelesen.

## Grenzen / mögliche nächste Schritte
Keine Mosaike (Stackfläche = Referenzbild), kein echtes Drizzle (2× interpoliert), keine Komet-/Planeten-/Mond-Modi, kleiner eingebauter Katalog
(großer nur mit ASTAP-`deep_sky.csv`), Dockerfile (ASTAP-Download) noch nicht real gebaut/getestet, Satellitenspur-Erkennung unzuverlässig,
keine fotometrische Farbkalibrierung (Gaia), keine Verzerrungskorrektur, keine GPU, waagerechte Hintergrundstreifen bei NGC 1579 (Stack).
