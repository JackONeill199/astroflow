# SkyTrace-Modul in AstroFlow

Herkunft: Repo `JackONeill199/SkyTrace` (bleibt unverändert bestehen). Die Analyse-Engine liegt hier in `engine/`
(flache Python-Module, unverändert bis auf Pfade), die Oberfläche ist in die AstroFlow-Shell portiert (`static/skytrace.js`),
Messgrafiken/Zoomdialoge (`static/evidence.js/css`) sind unverändert und werden auch in den eigenständigen Bericht eingebettet.

## Abweichungen gegenüber dem Original-Repo

- **Pfade** (`engine/project_paths.py`): `CODE` = `engine/`, `ASSETS` = Modulordner (Modell unter `models/`, `static/evidence.*`),
  `BASE` = Datenordner aus `SKYTRACE_HOME` (Standard `<AstroFlow-Daten>/skytrace`, einstellbar unter Einstellungen → SkyTrace;
  kann auf einen bestehenden SkyTrace-Ordner zeigen, damit alte Läufe sichtbar bleiben). Wirkt nach Neustart.
- **Server:** `web_server.py` ist durch `api.py` (FastAPI-Router unter `/api/skytrace`) ersetzt; Medien unter `/api/skytrace/media/<Lauf>/…`
  (Range-Anfragen für Videos über `FileResponse`). Token-/Host-Prüfung entfällt – der AstroFlow-Kern blockt schreibende Anfragen fremder Herkunft.
  LAN-Freigabe regelt AstroFlow (`serve --host`).
- **Interpreter:** Analysen laufen als Unterprozess mit dem Interpreter aus Einstellungen → SkyTrace, sonst `<SKYTRACE_HOME>/.venv`, sonst
  AstroFlows Python. Der AstroFlow-Server importiert nur leichte Engine-Teile (`json_store`, `observation_settings`, `video_overlays`) –
  ohne OpenCV/PyTorch startet die App trotzdem; die Engine-Prüfung (`Module.check_engine`) zeigt fehlende Pakete an.
  OpenSky-Anmeldung läuft im Engine-Interpreter (braucht `trino`).
- Nicht übernommen: Tk-Desktop-Oberfläche (`desktop_app.py`, `catalog_dialog.py`, `location_picker.py`), Starter (`*.cmd/*.vbs`),
  Web-Prüfungen gegen den alten Server (`check_web.py`, `check_lan.py`, `check_preview_race.py`). Die übrigen `check_*.py` liegen in `engine/`
  und laufen mit gesetztem `SKYTRACE_HOME` (manche brauchen echte Stellina-Daten: Ordner per `SKYTRACE_SAMPLES`).
- Abhängigkeiten der Engine: `requirements.txt` (Lock aus SkyTrace). Docker: `--build-arg SKYTRACE=1`.

## Analysen löschen (29.10.2026, Nutzerwunsch)
`DELETE /api/skytrace/run?id=<Lauf>` → `Controller.delete_run`: nur Ordner unter `Ergebnisse/` mit Laufdateien (`videos.json`,
`bericht.html` oder `messwerte.jsonl`), nicht der gerade laufende; Windows-Sperren (Videowiedergabe) mit einem Wiederholversuch.
Originalaufnahmen liegen nie im Ergebnisordner. UI: Papierkorb auf den Karten (bei Hover), in der Lauf-Ansicht und in der Befehlspalette,
jeweils mit Bestätigungsdialog.

## Statistik (`stats.py`, 01.11.2026, Nutzerwunsch)
`GET /api/skytrace/statistics` → `stats.collect`: alle Läufe mit `videos.json` (laufende Analyse ausgenommen), je Beobachtung
(`session`) nur die neueste vollständige Analyse (sonst die neueste; `repeated` = nicht gezählte ältere). Kandidaten kommen schlank
zurück (nur Felder für `evidence.js classification`, Zeit, Geschwindigkeit, Katalogstatus) – **Einordnung und Zählung macht die
Oberfläche** (`statsView` in `skytrace.js`), damit Zahlen und Kandidatenfilter übereinstimmen. Nacht = Datum im Ordnernamen,
sonst früheste Kandidatenzeit − 12 h. Cache je Lauf nach Dateizeiten (Arbeitsinstanz: 119 Läufe, 0,4 s erstmals, danach 0,07 s).
Route `#/skytrace/statistik` steht vor der Lauf-Route. Test: `test_skytrace.py`, Ansicht in `test_ui_layout.py`.

## Automatisch nach dem Stacken (`auto.py`, 28.10.2026)
- Ereignis `stacking.stacked` → `AutoAnalysis.stacked`: noch nicht übergebene Light-Aufnahmen (Dienst `project_frames`, Merkliste
  `<AstroFlow-Daten>/skytrace/auto.json` → `sent`) je Nacht als Ordner `<Nacht>_observation_<Objekt>` (wie Stellina, damit der Titel der Objektname ist; ältere Läufe `<Objekt>_observation_<Nacht>-p<id>-<zeit>` erkennt `short_name`) aus **Hardlinks**
  (anderer Datenträger: Kopie) unter `<AstroFlow-Daten>/skytrace/auto/`. So überleben sie das Löschen hochgeladener Rohbilder.
- Warteschlangen-Thread startet je Ordner einen normalen Lauf (`Controller.launch(..., remember=False)` – Formularvorgaben bleiben),
  sobald SkyTrace frei und die Engine geprüft ist; danach wird der Ordner gelöscht. Vorher übernimmt `_keep_frames` die Aufnahmen,
  die der Bericht braucht (erstes Bild, Treffer ±2), nach `<Lauf>/<Beobachtung>/rohbilder/` und schreibt die Pfade in `*.jsonl`,
  `kandidaten.json`, `kandidaten/*/kandidat.json` um – hochgeladene Rohbilder sind dann oft schon gelöscht (M45: Aktualisieren schlug fehl). Unvollständige Engine → Eintrag mit Fehler in der Aktivität.
  Wartende Ordner erscheinen in der Aktivität, `status.auto_waiting` zählt sie. Abschalten: Einstellungen → SkyTrace (`skytrace.auto`).
- Achtung: Zeigt `SKYTRACE_HOME` auf den Ordner eines eigenständigen SkyTrace, nicht gleichzeitig aus beiden Oberflächen analysieren lassen.

## Satellitenabgleich: Zeitbezug und Uhrtoleranz (01.11.2026, Nutzerbefund „Satelliten werden nie zugeordnet“)
- **Befund** (alle Läufe in `AstroAnalyse/Ergebnisse`, Prüfskript-Idee: Bahn der nächsten Diagnose ±60 s propagieren und die Zeit
  suchen, zu der sie Spuranfang/-ende passiert): Bei ~90 von ~150 Spuren lag eine Bahn quer < 6′ und richtungsgleich (< 5°) auf der
  Spur, aber 1–6° daneben **entlang** der Bahn. Langsame Objekte, deren Spur ganz im Bild liegt (NORAD 33437, 36120, 35499), füllen
  genau [DATE-OBS − 10 s, DATE-OBS] → **Stellina-`DATE-OBS` ist das Belichtungsende**. Zusätzlich Uhrversatz je Nacht etwa −10…+14 s
  (Stellina-Uhr kommt vom Handy; M85 2022-05-14 nach, M31 2022-06-11 vor), Bahnfehler längs (TLE 0,3–2 Tage alt) wenige Sekunden.
- `motion_plausibility.exposure_info` → `reference` ('end' bei Stellina-EXPOSURE), Kandidat `time_reference`; `exposure_start(candidate)`
  (ältere Kandidaten: über `plausibility.exposure_source`). `catalog_matching._times` liefert damit den echten Belichtungsbeginn (alle Kataloge).
- `satellite_check` propagiert Belichtung ± `SATELLITE_CLOCK_SLACK_S` 20 s (Einstellung `satellite_clock_slack_s`), `angular_match(times=…)`
  liefert `passage` (Zeitpunkte an Spuranfang/-ende), Richtung an der Durchgangsstelle (`SATELLITE_MAX_ANGLE` 15°), Toleranz quer weiter 360″.
  Neu je Bahn: `mean_offset_arcsec`, `passage_seconds` (relativ zum Belichtungsbeginn), `time_offset_s` (0 = während der Belichtung);
  verworfen, wenn die Bahn für die Spur länger als die Belichtung bräuchte. Rang = mittlerer Abstand + 20″ je Sekunde außerhalb.
- **Namen:** Archivbahnen (Space-Track-Spiegel) haben nur NORAD-Nummern → `archive_sources.satellite_names` (seit 0.10.1):
  lokale Kopie des ganzen SATCAT `catalog_cache/archives/satcat.csv` (`https://celestrak.org/pub/satcat.csv`, Spalten wie die
  JSON-Einträge: `NORAD_CAT_ID`, `OBJECT_NAME`, `OBJECT_TYPE` …, geschätzt 12–15 MB) – nur geladen, wenn eine Nummer fehlt und die
  Kopie älter als 24 h ist (SATCAT ändert sich 1–2×/Tag; Regel „nur einmal je Aktualisierung“), atomar ersetzt, Kopfzeile geprüft.
  **Bewusste Ausnahme** von „keine weltweiten Vollarchive“ (Nutzerwunsch 01.11.2026): Namenskatalog ohne Ort/Zeit, ersetzt
  Einzelabfragen und verrät nicht, welche Objekte im Bild waren. Nummern jünger als die Kopie: höchstens 3 Einzelabfragen
  (`records.php`, Cache `satcat-names.json`, „nicht im Katalog“ nur bei leerer Liste bzw. „No SATCAT records found“).
  `object_type` Satellit/Raketenstufe/Trümmerteil. Rückgabe `(namen, hinweis)` → Katalogtext + `names_note` (UI unter der
  Bahntabelle) + `status`-Event. `catalog/status` → `satcat_file` (Datum/Größe der Kopie, Archivzeile in den Einstellungen).
- **SATCAT-Sperre (01.11.2026):** Nach dem Nachrechnen aller Läufe (Einzelabfrage je Nummer) antwortete `records.php` mit 403
  „Access from IP address … was blocked … Please be sure to follow our usage policy“ (Sperre je Anschluss, `gp.php` lief weiter –
  **nicht** auf andere Endpunkte ausweichen). CelesTrak-Nutzungsregeln (celestrak.org/usage-policy.php): bei jeder Antwort ≠ 200
  sofort aufhören und einem Menschen melden, sonst Firewall. Umsetzung `_satcat_get`: `allow_redirects=False`; ≠ 200 →
  `satcat-pause.json` `{stopped, http, reason}` ohne Ablauf, Freigabe nur per Knopf (Einstellungen → SkyTrace,
  `POST /catalog/satcat/release`, Status `GET /catalog/status` → `satcat`); keine HTTP-Antwort bzw. unbrauchbarer 200-Inhalt
  → `until` +15 min. `jsonDir.php` (Dateiliste mit Datum) wird nicht genutzt – Format ungeprüft, 24-h-Regel reicht.
- UI: `evidence.js classification` nennt bei passender Bahn „Satellit <Name>“ bzw. Objektart, Klasse `satellite` (Filter „Satelliten“);
  Kandidatenliste/Detail zeigen den Namen als Titel, K-Nummer klein. Bahntabelle mit Spalte „Durchgang“.
- Nachrechnen: `engine/refresh_satellites.py <Lauf> …` (nur Kandidaten-JSON + lokale Archive, schreibt Kandidaten, Projektion, Bericht).
  Ergebnis M31 2022-06-11 (`2026-11-01_12-19-46-886388`): 12 von 13 Spuren zugeordnet (Starlink-Züge, Iridium 914, SL-3/SL-16 R/B), vorher 0.
  M103 K0001/K0002 (unten als „84′/28′ entfernt“ beschrieben) = STARLINK-1484/-1062.
- Offen: Uhrversatz je Nacht aus mehreren sicheren Treffern schätzen (dann engere Toleranz); bei einigen Nächten konstant 10–18′ quer
  daneben (Standort der Nacht falsch? 1 km Standortfehler ≈ 7′ bei 500 km) – nicht untersucht. Test: `tests/test_skytrace.py`
  (läuft nur mit skyfield/sgp4, also im Engine-Interpreter).

## Erkennung im Teleskop-Bildfeld (28.10.2026, Nutzerbefund M74 2022-08-07)
Anlass: Ein schwacher Meteor (img-0041, ~120 px, ~1,5σ/px) wurde übersehen, ein langsames Objekt in sechs Aufnahmen (img-0177…0182)
als „Meteor“ gemeldet. Vergleichslauf: `Ergebnisse/2026-10-28_18-47-38-352557` (alt) gegen `2026-10-28_19-47-22-527055` (neu).
- **Kurze schwache Spuren** (`line_detection.py`): ab zwei 24-px-Abschnitten, Schwelle `required_score(bins)` = 5,2 + 0,9 je Abschnitt
  (ab 7 Abschnitten unverändert 10,5), Mindestlänge 90 statt 160 px, Hough-Stimmen 22. Kurze Spuren nur in **ruhigem Hintergrund**
  (`local_structure` ≤ 1,5: lokales/globales Rauschen ohne das Spurband, im Differenz- und im Originalbild) und nicht innerhalb 1° einer
  Sensorachse (Ausleseartefakt img-0292). Kalibriert: Meteor 5,5 gefunden; IC59/IC405/M103/NGC884 ohne neue Treffer (dortige kurze
  Fehlspuren lagen alle an hellen Sternen/Nebeln, Unruhe ≥ 2). Kurze Fragmente entlang einer stärkeren Spur gelten als dieselbe Spur.
  Kandidaten tragen `line_threshold` (in Messdetails als Schwelle) und `short_trail`.
- **Spurverkettung** (`trail_tracks.link_tracks`, aufgerufen in `candidate_report.extract_candidates` vor dem Gruppieren – wirkt also
  auch beim Aktualisieren alter Läufe): gemessene Segmente in verschiedenen Aufnahmen ≤ 180 s, parallel (≤ 8°), kollinear, Fortschritt
  = eigene Länge/Belichtung × Zeitabstand (Faktor 0,55–1,6), gleichmäßige Bewegung → ein Kandidat `kind='plane'`, `track_id='L…'`,
  `linked_track` (Bilder, Dauer, Rate, Begründung). Ein Meteor leuchtet nur in einer Aufnahme; verkettete Spuren sind nie Meteorkandidaten.
- **YOLO nur als Hinweis bei Teleskopen** (`motion_plausibility.narrow_field`: Bildfeld aus FOCAL/PIXSZ/NAXIS < 5°): Das OpenSkyCam-Modell
  ist für Weitwinkel-Himmelskameras trainiert. Erkennungen erhalten `model_scope='narrow_field'`; reine Modellboxen **ohne gemessene
  Linie sind keine Kandidaten** (nur in `frames.jsonl` → `model_proposals`). Anlass M45 2022-09-22 (Lauf `2026-10-28_23-34-00-163934`):
  22 Modellvorschläge auf Sternschweifen eines verwackelten Bildes (Bild 36) und auf hellen Plejadensternen, keiner echt; der
  Liniendetektor fand dort nichts. Bestätigt eine gemessene Linie die Box, bleibt die Klasse als `model_hints` daran.
- `trail_validation`: gleichgerichtet längliche Sternkerne (`elongated_fraction` ≥ 0,3, `direction_coherence` ≥ 0,75) zählen
  zusätzlich als unabhängiger Sternbefund für das Verwerfen verteilter Modellvorschläge (Bild 36: 0,425/0,84, Schweife inkohärent gemessen). `evidence.js classification`: Kataloghinweis →
  verkettete Spur → Modellhinweis (bei schmalem Bildfeld nur als Quelle/Notiz, nicht als Klassenname) → `kind`.
- **Blinkende Flugzeuge** (`line_detection.blink_pattern`, Nutzerhinweis 29.10.2026, **ohne echtes Beispiel**, nur synthetisch
  `check_blink_trails.py` und an den Vergleichsläufen auf Fehlalarme geprüft): Linienprofil mit ≥ 5 hellen Abschnitten auf einer Geraden,
  echte Lücken, konstantem Takt (Abstände = Vielfache der Periode, Median-Abweichung ≤ 12 %; einzelne von Sternen verdeckte Blitze
  erlaubt), Anteil ≤ 70 %, integrierter Kontrast ≥ `BLINK_SCORE` 8, auch im Originalbild; zwei Formen: gestrichelt oder Linie mit
  Knoten. → `kind='plane'`, `blink` (Blitze, Takt in Analysepixeln, Form), Klasse „Flugzeugkandidat (Blinklichter)“; nie Meteor.
  Taumelnde Satelliten können ähnlich aussehen. Kurze helle Striche bleiben in der Hough-Abstimmung und Blinkprüfung frei
  (Maske `dashes`, ab ~12 px gestreckt 16:1); durchgehende Spuren werden weiter mit der strengen Sternmaske bewertet
  (sonst wurden Sternschweife in IC59 Bild 114 zu kurzen Spuren). Mit erstem echten Flugzeugbeispiel nachkalibrieren.
- Prüfen: `tests/test_skytrace.py` (Verkettung, Bildfeld), `check_blink_trails.py`, Engine-Checks `check_banding`, `check_streak_merging`, `check_trail_validation`,
  `check_motion_plausibility` mit `SKYTRACE_HOME` und dem AstroAnalyse-Interpreter.

Das folgende Projektwissen stammt aus dem Original und gilt fachlich weiter (Pfadangaben beziehen sich auf den alten Projektordner).

---

# SkyTrace – Projektwissen für Agenten

Stand: 28.10.2026. Diese Datei gilt für `AstroAnalyse/`. Bei Änderungen an Architektur, Bedienung oder Datenquellen mitpflegen. Details: `README.md`, `Ereignisvideos.md`, `Bugpruefung.md`. Ältere README-Abschnitte beschreiben teilweise die frühere Desktop-/Fotooberfläche; aktueller Code und diese Übersicht haben Vorrang für den Projektstand.

## Ziel und feste Nutzerentscheidungen

- Projektname: **SkyTrace**. Englischer Untertitel: **Uncover what moves among the stars.** Der bestehende Projektordner heißt weiterhin `AstroAnalyse/`.
- Lokale Windows-Webanwendung zur Analyse vorhandener Astrofotos und Videos. Standardquelle: ein Stellina-Stammordner mit Unterordnern je Beobachtung. Keine RTSP-Kamera, Cloudflare-Installation oder Domain erforderlich.
- Deutsche, hochwertige Browseroberfläche; Analyse starten/anhalten, Livebild, Fortschritt, Protokoll, Beobachtungsvideos und Kandidatenberichte.
- Viele FITS-Aufnahmen einer Beobachtung werden chronologisch zu einem H.264-Zeitraffervideo. Einzelbildstreifen müssen auch ohne wandernden Punkt erkannt werden. Erkannte Spuren bleiben als Overlay im weiteren Video sichtbar.
- Teleskopbewegung über das gesamte Sternfeld kompensieren; gemeinsame Sternbewegung nicht als Asteroid interpretieren. Verwackelte/unscharfe Bilder aus Analyse und Video ausschließen.
- Kandidaten mit echten UTC-Zeiten und Vorher-/Nachher-Ausschnitten; Overlays abschaltbar. Messdetails, grafische Erklärungen und vergrößerbare Ansichten gehören zum Produkt.
- LAN-Zugriff **ohne Passwort ist ausdrücklich gewünscht**. Nur das konfigurierte Heimnetz, keine öffentliche Freigabe.
- Standortbezogene Online-Daten bevorzugen. Ausdrücklich erlaubte Ausnahme (28.10.2026): Satellitenbahnen zeitlich eng zur erkannten UTC-Aufnahmezeit abrufen und lokal nach Standort/Bildfeld filtern. **Keine automatischen weltweiten Jahres-/Monatsdownloads** als Ersatz für fehlende räumliche Abfragen.

## Unveränderliche Daten und wissenschaftliche Grenzen

- **Originale im Quellordner ausschließlich lesen.** Nicht löschen, verschieben, umbenennen oder mit Sidecars/ASTAP-Ergebnissen beschreiben. „Entfernen“ unscharfer Aufnahmen bedeutet Ausschluss aus der Verarbeitung.
- Ausgaben ausschließlich in `AstroAnalyse/Ergebnisse/<Lauf>/`; bestehende Nutzerläufe erhalten. Für geänderte Detektion/Re-Rendering einen eigenen Ergebnislauf verwenden. Berichtsergänzungen im ausdrücklich bearbeiteten Lauf sind möglich.
- Keine Fotos hochladen. Katalogabfragen benötigen nur Standort, UTC und Himmelskoordinaten. Zugangsdaten und genaue Standortwerte nicht in Dokumentation oder Diagnoseausgaben kopieren.
- Plate Solving bestimmt Himmelskoordinaten, **keinen Erdstandort**. Standort aus FITS, Beobachtungseinstellung oder Standard beziehen; Adresse/Browser-Geolocation ist nur geeignet, wenn es der damalige Standort ist.
- UTC-Aufnahmezeit und Belichtung nicht mit Videozeit verwechseln. Stellina-`EXPOSURE` kann Millisekunden enthalten. Bayer-FITS werden gegebenenfalls 2×2 gebinnt; Pixel/FWHM beziehen sich auf die jeweilige Analyseauflösung.
- Kandidat ≠ bestätigtes Objekt. Modellkonfidenz ≠ Identität. `line_snr` ist hier ein integrierter **Detektorwert**, keine kalibrierte Sigma-Signifikanz oder Prozentwahrscheinlichkeit.
- Fehlendes Archiv, Timeout und nicht prüfbar niemals als „kein Objekt vorhanden“ darstellen. Ein geometrischer Katalogtreffer bleibt ein Hinweis. Meteor und Meteorit nicht gleichsetzen.
- Sichtbarkeit meint den geometrischen WGS84-Horizont; Gelände, Wetter, Beleuchtung und Nachweisgrenze sind damit nicht vollständig modelliert.

## Start, Laufzeit und Betrieb

Eigene Python-Umgebung: `.venv\Scripts\python.exe` (derzeit Python 3.14). Abhängigkeiten: `requirements-lock.txt`; Einrichtung mit `Einrichten.cmd`. Die vorhandene Umgebung enthält eine eigenständige Kopie der zuvor verwendeten Pakete. Modellgewichte und unveränderte Herkunfts-/Lizenzdateien liegen in `models/night-detector/`. Keine Laufzeitpfade in das Nachbarprojekt OpenSkyCam verwenden.

```powershell
# Aus AstroAnalyse/:
.venv\Scripts\python.exe python/web_server.py --port 8765 --no-browser
# Normaler Nutzerstart: Starten.cmd, Starten.vbs oder AstroAnalyse.lnk
```

- `python/app.py` startet die Webanwendung; erneuter Start öffnet eine bereits laufende Instanz. `python/desktop_app.py` / `Desktop.cmd` sind die alte Tk-Oberfläche.
- Lokal: `http://127.0.0.1:8765`; im LAN über die Adresse des Rechners.
- `web_network.json`: LAN an/aus und erlaubtes Subnetz; Windows-Firewallregel nur für das private Netz, TCP 8765. Host-/Subnetzprüfung und Origin-/Token-Prüfung für Schreibzugriffe erhalten; Passwortlosigkeit ist kein Grund, diese Schutzprüfungen zu entfernen.
- Browser-Geolocation funktioniert über LAN-HTTP gewöhnlich nicht; Adresssuche anbieten. Keine Router-Portweiterleitung einrichten.
- Vor Serverneustart `/api/state` auf laufende Arbeit prüfen. Lauf nicht zur Bereitstellung einer Änderung abbrechen. Bei nötigem Neustart tatsächlichen Listener und dessen Kommandozeile verifizieren; `web-server.pid` kann den Launcher statt des Listener-Prozesses bezeichnen.
- Browser schließen beendet keinen Job. Stopp erfolgt kooperativ über eine STOP-Datei, MP4 wird finalisiert. Worker importieren neuen Python-Code beim nächsten Start; statische UI-Dateien nach Browser-Neuladen.
- Hintergrundprozesse auf Windows mit verborgenem Fenster starten. Netzwerk-/Socket-Prüfungen benötigen je nach Sandbox eine Eskalation; normale Dateien im Workspace sind beschreibbar.

## Architektur und wichtigste Dateien

Alle Python-Module, Startpunkte, Prüfungen und Wartungsskripte liegen unter `python/`. `python/project_paths.py` trennt `CODE` (Python-Ordner) und `BASE` (Projektwurzel). Daten, Konfiguration, `web/`, `models/`, `tools/` und `.venv/` bleiben unter der Projektwurzel. Unterprozesse über `CODE`, Daten über `BASE` adressieren; nicht vom aktuellen Arbeitsverzeichnis ableiten. Diagnosebilder und ältere Protokolle liegen lokal unter `diagnose/`; aktive Serverprotokolle können im Hauptordner neu entstehen.

| Dateien | Aufgabe |
|---|---|
| `python/web_server.py` | Standardbibliothek-HTTP-Server, API, Prozesscontroller, Start/Stopp, LAN-/Pfadschutz, Medien-/MP4-Range-Auslieferung |
| `web/index.html`, `web/app.js`, `web/style.css` | Browseroberfläche ohne Buildschritt; Polling, Live-/Archivansicht, Zeitreihen, Einstellungen |
| `python/analyse.py` | Bild-/FITS-Laden, Metadaten, Foto-/Videoanalyse; JSONL-Ereignisse über stdout |
| `python/sequence.py` | Bildfolgen, Registrierung, Detektion, bleibende Spuren, Qualitätsausschluss, Videos und Berichte |
| `python/astro_motion.py`, `python/frame_quality.py` | Sternensemble-Registrierung und Bildschärfe/Verwacklung |
| `python/line_detection.py` | Sensorbandkorrektur, Hintergrundvergleich, Hough-/Linienkontrastdetektion; `LINE_SCORE_THRESHOLD = 10.5` |
| `python/solving.py` | Lokales ASTAP, WCS und Himmelsobjektbeschriftung; erzeugt FITS-Kopie im Ergebnisordner |
| `python/candidate_report.py` | Kandidaten gruppieren, saubere Ausschnitte/Zeitreihen, WCS-Zuordnung, automatische Lösung bei fehlender WCS, Katalogprüfung |
| `python/candidate_evidence.py` | Ursprünglichen Detektorwert, Bild-/Registrierqualität, Winkellänge, Querprofil und registrierten zeitlichen Kontrast speichern |
| `web/evidence.js`, `web/evidence.css` | Gemeinsame Messgrafiken und Zoomdialoge für App und eigenständigen Bericht |
| `python/report_view.py` | Eigenständiges HTML mit eingebettetem Evidence-JS/CSS; Ausschnitte/Videos bleiben relative Begleitdateien |
| `python/catalog_matching.py`, `python/catalog_http.py` | Katalogvergleich, Cache, begrenzte/abbrechbare JPL-Abfragen und geometrische Diagnostik |
| `python/archive_sources.py`, `python/satellite_archive.py` | Vorhandene historische Archive, lokaler Zeitindex, SGP4 und konservative Satellitenvorauswahl |
| `python/opensky_archive.py`, `python/visibility.py` | OpenSky-Trino-OAuth, begrenzte Flugabfragen und gemeinsamer geometrischer Horizontfilter |
| `python/observation_settings.py` | Validierte Standort-/Katalogeinstellungen, FITS-/Beobachtungs-/Standardstandort |
| `python/json_store.py`, `python/image_store.py` | Atomare Veröffentlichung und Wiederholung bei vorübergehenden Windows-Dateisperren |

Videos: PyAV/libx264. ASTAP: bevorzugt `tools/astap/astap_cli.exe`, sonst `C:/Program Files/astap/astap_cli.exe`; Sternkatalog unter `tools/astap/catalog/`. Deep-Sky-Beschriftung aus `C:/Program Files/astap/deep_sky.csv`.

## Formate, Persistenz und bewährte Reparaturen

- Beobachtungsauswahl markiert vorhandene Bildfolgen anhand aller `videos.json` als „Bereits analysiert“ bzw. „Teilweise bearbeitet“, auch nach Neustart. Zuordnung über `source_folder` (bei alten Läufen erste Originaldatei aus `frames.jsonl`/`ausgeschlossen.jsonl`), damit gleichnamige Ordner anderer Quellen nicht markiert werden. Neue Zusammenfassungen speichern `limited` für begrenzte Bildanzahl. Wiederholungen bleiben möglich; Originalordner werden nicht beschrieben.
- Helle, zusammenhängende schmale Linien von der Sternmaske ausnehmen. Differenzbild-Spuren benötigen zusätzlich positiven Linienkontrast im aktuellen Originalbild, sonst entstehen Nebenloben nach dem Verschwinden einer hellen Spur. YOLO-Teilboxen über Schnitt mit gemessenen Segmenten zusammenfassen (`merge_streak_detections`), nicht allein über Box-IoU. Modellklassen bleiben `model_hints`; gemessene Linien bleiben unbestätigte Einzelbild-Spuren. `python/rebuild_trails.py --redetect-lines` misst alte Läufe neu in einen eigenen Ergebnisordner; danach Kandidatenausschnitte neu erzeugen.
- `python/motion_plausibility.py`: lokale Vorprüfung aus FITS-NAXIS, FOCAL [mm], PIXSZ [µm], EXPTIME [s] bzw. Stellina EXPOSURE [ms], nach Messung und vor Katalogen; vorhandene WCS hat für Spurlängen Vorrang. Binning/Video-Verkleinerung berücksichtigen. Belichtungszeit ist nur Obergrenze der Sichtbarkeit, Winkelgeschwindigkeit daher Untergrenze. Drei explizite Vergleichsszenarien (Flugzeug 10 km/0,25 km/s; Satellit 500 km/7,8 km/s; Meteor 100 km/30 km/s) sind **keine Klassengrenzen**. Ein unpassendes Szenario schließt nicht die Klasse aus. Keine Entfernung aus Pixeln behaupten. Fehlende/widersprüchliche Belichtung verhindert zeitabhängige Katalogabfragen; niemals ersatzweise 10 s erfinden. In App und Bericht unter „Physikalische Vorprüfung“ sichtbar.
- Browser-Overlays zeigen kompakt `≥ …″/s` (Mindest-Winkelgeschwindigkeit, niemals behauptete km/h). Zusatzinformationen nur beim Hover/Fokus auf Spur/Geschwindigkeit oder „Messdetails“. `python/video_overlays.py` rekonstruiert gemessene Dauerspuren samt Sterntransformation und echter `video_seconds`-Zeitachse aus gespeicherten Frames. App/Bericht verwenden dafür das saubere Video plus SVG; Overlay-Schalter, Rückwärtssuche und Vollbild des Containers erhalten. Heruntergeladenes altes MP4 bleibt unverändert. `python/check_video_overlays.py`, `node check_web_ui.cjs` prüfen Zeitzuordnung und Darstellung.
- Sichtbarkeit von SVG mit `AstroEvidence.setOverlayVisible` steuern: `SVGElement.hidden` allein entfernt kein vorhandenes `hidden`-Attribut. Hauptvorschau erst bei tatsächlich rekonstruierten Spuren auf sauberes Bild/Video umstellen; andernfalls annotierte Medien beibehalten. Regression prüft den vollständigen `renderPreview`-Pfad sowie Aus-/Einschalten.
- Overlay-Größe vor Beschriftung an die tatsächlich dargestellte Bildfläche anpassen. Bei Bildvorschau den Container (Vollbild/Resize) und Bildladen beobachten; ein `ResizeObserver` nur auf dem versteckten Video genügt nicht. Normale Ansicht und Vollbild müssen dieselben sternregistrierten Koordinaten zeigen.
- Overlay benennt die Kandidatenklasse wieder: vorhandene geometrische Kataloghinweise, sonst Original-Modellhinweise (`model_hints`) bzw. ursprüngliche Klasse; niemals eine Klasse nur aus Beispielgeschwindigkeiten ableiten. „Meteorkandidat“ bleibt unbestätigter Modellvorschlag. `python/refresh_overlay_labels.py <Lauf>` übernimmt alte Modellhinweise offline aus den gespeicherten Erkennungen und erneuert den Bericht.
- Einstellungen → Overlay-Anzeige: Winkelgeschwindigkeit oder `km/h*` mit explizit angenommener Entfernung in km; lineare Mindest-Schätzung = Winkelgeschwindigkeit [rad/s] × Entfernung [km] × 3600. Kein Entfernungsstandard erfinden. Annahme im Tooltip offenlegen. Anzeigepräferenz unter `stellina-overlay-display` pro Browser gespeichert; keine Veränderung von wissenschaftlichen Messwerten/Katalogprüfungen.
- Gewünschter Overlay-Schriftstil: gelbe Arial/Helvetica-Schrift mit dunkler Kontur direkt im Bild, keine hinterlegten Beschriftungskästen. Plate-Solving-Objekte bleiben als blaue Positionskreise und Namen sichtbar. `python/video_overlays.py` nutzt gespeicherte WCS und lokalen Katalog, pro Frame über `reference_to_current` positioniert; keine erfundenen Positionen bei fehlender Registrierung. Auch reine Plate-Solving-Läufe ohne Ereigniskandidaten unterstützen. Kein erneutes Solving/Online-Abruf nötig.
- Nutzeränderung: Messdetails dauerhaft geöffnet in der Analyse anzeigen (`analysisDetails`), nicht per Hover verstecken. Satellitenvergleich direkt über der echten vollständigen Aufnahme anzeigen, keine getrennte Satelliten-Grafik/kein separates Vergleichsfenster. `python/satellite_overlay.py` projiziert gespeicherte topozentrische Bahnen über WCS und Sternregistrierung in Bildpixel; außerhalb liegende Bahnen ausdrücklich kennzeichnen, niemals ins Bild verschieben. `context_image` bleibt unbeschriftet. `python/refresh_satellite_overlays.py <Lauf>` ergänzt vorhandene Berichte vollständig offline. Hauptvorschau zeigt die Prognosen des gewählten Kandidaten ab dessen Trefferbild mit expliziter Bildnummer.

- Lauf: `videos.json`, `bericht.html`, `kandidaten.json`; je Beobachtung `frames.jsonl`, `ausgeschlossen.jsonl`, `ereignisse.mp4`, `original.mp4`, `live.jpg`, `live-clean.jpg`, `spuren.json`, `platesolve/` und `kandidaten/Kxxxx/` mit PNGs und `kandidat.json`.
- `reference_to_current` bildet Referenz- auf aktuelle Bildkoordinaten ab. Für WCS aktuelle Spur zurück ins Referenzbild transformieren. Ausschnitt-/Analyse-/Originalkoordinaten und Bayer-Binning nicht vermischen.
- `kandidat.json`: unter anderem `world_path`, `frames`, `catalogs`, `evidence`. Messprofile sind ergänzende Diagnosewerte aus linearen Bilddaten, kein nachträglich erfundener Detektorwert. Unscharfe oder unzuverlässig registrierte Nachbarn nicht vermessen.
- JSON/Bilder atomar veröffentlichen; bei HTTP Content-Length und Inhalt aus demselben geöffneten Dateihandle. Windows kann während paralleler Vorschauzugriffe kurzzeitig `PermissionError` liefern; vorhandene Retry-Helfer nutzen. SQLite-Verbindungen ausdrücklich schließen.
- UI: veraltete Requests dürfen keine neuere Auswahl überschreiben; Token-/Logzustand bei Serverneustart erneuern, fremd gestartete Jobs übernehmen. Videoposition nur beim Overlaywechsel derselben Beobachtung erhalten. Tests dürfen `letzter-lauf.txt` und echte Einstellungen nicht überschreiben.
- Konfiguration: `catalog_settings.json`, `web_preferences.json`, `web_network.json`. Logs: `web-server.log`, `web-server-error.log`. Nicht mit Testdaten zurücksetzen.

## Kataloge: aktueller Funktionsumfang und Grenzen

- **Asteroiden:** JPL SB-Ident, echter Standort und Belichtungsmittelpunkt, tatsächliches Himmelsfeld, numerischer Zweipass-Abgleich. Anfragen seriell mit Dienstsperre und Cache. **45 s Gesamtzeitlimit inklusive Sperrwartezeit**, Fortschritt alle 5 s, stoppbarer HTTP-Unterprozess. Timeout betrifft die konkrete Abfrage; andere Felder weiter prüfen. Keine ungefähre Erstpassposition als Bestätigung ausgeben.
- **Satelliten:** vorhandenes Space-Track-Archiv/Mirror Julien Simon (CC-BY-4.0) bevorzugen; bei fehlenden zeitnahen Bahnen dynamischer ETH-Abruf (`archive_sources.eth_tles`) anhand des UTC-Belichtungsmittelpunkts, auch bei aktuellen Aufnahmen. Eine UTC-Minute plus je höchstens 3 Tage für benachbarte TLEs; vollständige Pagination mit unverändertem Zeitfilter, maximal 120 Seiten/180 s einschließlich stoppbarer Sperrwartezeit. Nur vollständige, nichtleere Antworten atomar cachen. ETH sammelt seit 10.03.2023, mit möglichen Lücken; fehlende Abdeckung ist kein Ausschluss. Keine aktuellen TLEs als Ersatz für alte Daten. Vorhandene Nachbarjahre werden am Jahreswechsel lokal berücksichtigt. Gerundete lokale Bahnelemente bzw. ETH-TLEs mit SGP4; standardmäßig höchstens 3 Tage Bahnalter. Erst Standort/Zeit/Bildfeld vorauswählen, dann exakt propagieren. Vergleichstoleranz 360″, mindestens 50 % Spurabdeckung und passende Ausrichtung. Bis zu 8 nächste geprüfte Bahnen samt Ablehnungsgründen für Grafiken speichern.
- **GMN:** vorhandene offizielle Monatsdateien mit gemessenen Meteorflugbahnen, lokaler SQLite-Zeitindex plus Standorthorizont. Kein vollständiger Meteorkatalog.
- **CNEOS:** nur bereits vorhandener Zeitcache, weil der Dienst keinen räumlichen Serverfilter bietet; unterhalb des Horizonts liegende Ereignisse verwerfen.
- **OpenSky:** Trino-OAuth ist implementiert, echter Flugabruf benötigt vom Anbieter freigeschalteten Archivzugang. Ohne solchen Zugang keine funktionsfähige Flugarchivprüfung behaupten. Keine Passwörter in App/Chat sammeln. Offizieller Client speichert Token im Windows-Anmeldespeicher. SQL auf Stundenpartition, Zeit, geografischen Bereich und WGS84-Horizont begrenzen.
- Cache: `catalog_cache/`, Archive darunter `archives/`. Vorhandene globale Bestände erhalten, lokal filtern. `archive_sources.download()` darf fehlende Sammelarchive nicht automatisch herunterladen. Manuell konfigurierte TLE-/CSV-Dateien oder HTTPS-URLs sind ausdrücklich gewählte alternative Quellen.

## Prüfen und Berichte ergänzen

Aus `AstroAnalyse/` jeweils `.venv\Scripts\python.exe python/<Datei>` ausführen. Nur passende Tests zur Änderung; einige nutzen echte lokale FITS und können länger dauern.

| Änderung | Relevante Prüfungen |
|---|---|
| UI/HTTP | `node check_web_ui.cjs`, `python/check_web.py`; Browser zusätzlich visuell prüfen |
| Kataloge/Stopps | `python/check_dynamic_satellites.py`, `python/check_auto_catalogues.py`, `python/check_catalog_wait.py`, `python/check_candidates.py` |
| Messdetails | `python/check_evidence.py`, `python/check_candidates.py`; App **und** eigenständigen Bericht mit Zoom/Overlay prüfen |
| Physikalische Vorprüfung | `python/check_motion_plausibility.py`, `python/check_evidence.py`, `python/check_candidates.py`, `node check_web_ui.cjs`; Belichtungseinheiten, fehlende Metadaten, WCS und Binning sowie keine pauschalen Klassenausschlüsse |
| Sensorstreifen | `python/check_banding.py` – echte IC405-Fehlspuren sowie echte/synthetische horizontale, vertikale und diagonale Linien erhalten |
| Doppelte Spuren/helle Linien | `python/check_streak_merging.py`, `python/check_banding.py`, `python/check_sequence.py` – NGC884/869: acht Modellboxen in Bild 31 zu zwei Linien zusammenfassen; Differenz-Nebenloben in Bild 32 verwerfen |
| Bewegung/Schärfe | `python/check_ensemble.py`, `python/check_quality.py`, `python/check_registration_data.py`, `python/check_sequence.py` |
| Schwache Sternschweife/Formprüfung | `python/check_field_tails.py`, `python/check_trail_validation.py`, `python/check_quality.py`, `python/check_streak_merging.py`; IC59-Bild 114 verwerfen, echte NGC884-/M82-Spuren und mehrere gleichzeitige echte Linien erhalten |
| Dateizugriff/LAN | `python/check_preview_race.py`, `python/check_lan.py` |

Temporäre Testordner im Workspace anlegen; bei Windows-Dateisperren temporäre SQLite-/Bilddateien vor Cleanup schließen. HTTP-Tests müssen ausschließlich ihre temporären Einstellungen, Ergebnisordner und Laufmarker verwenden.

- `python/refresh_evidence.py <Ergebnisordner>`: Messdetails bestehender Kandidaten und lokale Satellitendiagnostik ergänzen, **ohne neue Online-Abfragen**.
- `python/candidate_report.py <Ergebnisordner> --stop-file <Pfad>`: vollständige Zeitreihen-/Katalogaktualisierung; kann Online-Abfragen auslösen.
- `python/rebuild_trails.py <Quelllauf> <neuer Ziellauf> --filter-readout`: separat neu rendern und Sensorfehlspuren prüfen; Quelllauf erhalten.

Wichtige reale Vergleichsfälle:
- `Ergebnisse/2026-10-27_22-24-07-014274`: IC59, fünf Modellvorschläge nur in Bild 114. Schwache gleichgerichtete Schweife an 17/69 eindeutigen Sternkernen in vier Quadranten, Kohärenz 0,882; helle Kerne allein knapp unter alter Verwacklungsgrenze. `extended_star_tails` misst verbundene schwache Ausläufer jenseits der Kerne; schwache Nebenmaxima eines Schweifs dürfen nicht als zweite Referenzsterne gezählt werden. `assess_quality` verwirft belastbar globale Muster. `trail_validation` prüft gerade/gebogene Modellvorschläge und räumlich verteilte gleichzeitige Vorschläge nur zusammen mit unabhängigen Sternbefunden. Anzahl allein niemals Ausschlussgrund. Nachprüfung separat mit `python/rebuild_trails.py --validate-motion`; Quelle und Originale erhalten, sauberes und annotiertes Video beide neu erzeugen, ausgelassene Frames korrekt neu durchnummerieren.
- `Ergebnisse/IC405-Sensorrauschen-korrigiert`: horizontales statisches Sensorrauschen wurde als Spur fehlinterpretiert. Nicht pauschal horizontale Linien verbieten! Korrektur verlangt Übereinstimmung unabhängiger Sensorstreifen und beider Ränder.
- `Ergebnisse/2026-10-27_20-01-34-078878`: M103, K0001/Bild 99 und K0002/Bild 126. Detektorwerte 14,95/16,45, Winkellängen 35,612′/39,5795′. Messprofile und Zoom ergänzt; **keine bestätigte Objektidentität**. Nächste geprüfte Satellitenbahnen ca. 84,35′/27,73′ entfernt bei 6′ Toleranz.
