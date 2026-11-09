# AGENTS.md – SkyTrace-Modul

Kompakter Einstieg für das Modul `astroflow/modules/skytrace`. Allgemeine Regeln, Sicherheit, Versionierung und Tests: Haupt-`AGENTS.md`.
Details je Thema stehen in `docs/entwicklung/skytrace-*.md` (Wegweiser unten) – **nur die Datei lesen, die die Aufgabe betrifft**.
Befunde mit Datum/Version und Eichwerte stehen dort, nicht hier. Nutzerdoku: `docs/README.md` (Modulordner), `docs/Ereignisvideos.md`, `docs/Bugpruefung.md`.

## Zweck
SkyTrace findet **Lichtspuren und bewegte Objekte** in Bildfolgen (Stellina, Seestar, Vespera, klassische Kameras) und gleicht sie mit Katalogen ab
(Satelliten, Asteroiden, Meteore, Flugzeuge). Es kommt aus dem Repo `JackONeill199/SkyTrace` (Engine unverändert bis auf Pfade, Oberfläche in die
AstroFlow-Shell portiert). Englischer Untertitel: *Uncover what moves among the stars.*
Neben der Analyse: Lauf-Ansicht mit Video und Messgrafiken, Statistik und Erfolge, **Standort- und Uhrenprüfung** (Rückrechnung aus Satellitenspuren),
**3D-Erde** (Nächte, Standorte, Heatmap, Satellitenbahnen) und automatische Analyse nach dem Stacken.

## Lizenz (AGPL-Teil)
Die Erkennung (Modell `models/night-detector/`, AGPL-3.0, und `ultralytics`, AGPL-3.0, von `requirements-engine.txt` nachinstalliert) ist AGPL-3.0; die Engine importiert `ultralytics`, steht deshalb selbst unter AGPL-3.0-or-later (`engine/LICENSE`) und läuft für die Erkennung als eigener Unterprozess (Quellcode offen im Repo). AstroFlow liefert `ultralytics` nie mit (auch nicht das Windows-Paket); der Server importiert es nie. Der übrige AstroFlow-Code steht unter PolyForm Noncommercial 1.0.0 (bis v0.51.3 MIT). **Doppellizenz** (Nutzerentscheidung 09.11.2026): Die Engine-Hilfsdateien, die der Server in den eigenen Prozess lädt (`json_store`, `observation_settings`, `project_paths`, `motion_plausibility`, `satellite_overlay`, `video_overlays`), stehen wahlweise unter PolyForm Noncommercial 1.0.0 oder AGPL-3.0-or-later (SPDX-Kopfzeile je Datei, Erklärung in `engine/LICENSE` und `NOTICE`). Importiert der Server künftig weitere `engine/`-Module, diese genauso kennzeichnen. Texte: `NOTICE`, README → Lizenz, `core/components.py` (`license` von `skytrace`).

## Feste Nutzerentscheidungen und wissenschaftliche Grenzen (verbindlich)
- **Originale nur lesen.** Nie löschen, verschieben, umbenennen oder mit Sidecars/ASTAP-Ergebnissen beschreiben. „Entfernen“ unscharfer Aufnahmen
  heißt Ausschluss aus der Verarbeitung. Ausgaben nur unter `<SKYTRACE_HOME>/Ergebnisse/<Lauf>/`; bestehende Läufe erhalten, für geänderte
  Detektion/Re-Rendering einen eigenen Ergebnislauf nehmen. Löschen eines Laufs nur über `Controller.delete_run` (nie die Originalaufnahmen).
- **Keine Fotos hochladen.** Katalogabfragen brauchen nur Standort, UTC und Himmelskoordinaten. Zugangsdaten und genaue Standorte nicht in Doku,
  Diagnoseausgaben, Tests oder Screenshots (Tests: erfundene Orte).
- **Keine automatischen weltweiten Massendownloads** als Ersatz für fehlende räumliche Abfragen; standortbezogene Online-Daten bevorzugen.
  Erlaubte Ausnahmen: Satellitenbahnen zeitlich eng zur Aufnahmezeit (ETH-Abruf, lokal nach Standort/Bildfeld gefiltert) und der **SATCAT-Namenskatalog**
  (ohne Ort/Zeit, höchstens 1× je 24 h). `archive_sources.download()` lädt fehlende Sammelarchive nie automatisch.
- **CelesTrak-Nutzungsregeln:** bei jeder Antwort ≠ 200 sofort aufhören (`satcat-pause.json`, Freigabe nur per Knopf), **nicht** auf andere Endpunkte ausweichen.
- **Plate-Solving liefert Himmelskoordinaten, keinen Erdstandort.** Standort aus Ausnahme → FITS → Ortsliste (`core/sites.py`) → alter SkyTrace-Standard
  (`observation_settings.resolve_site`).
- **UTC-Aufnahmezeit ≠ Videozeit.** Stellina-`DATE-OBS` ist das **Belichtungsende**, `EXPOSURE` kann Millisekunden enthalten. Nie Originalnummer/fps in
  Videozeit umrechnen (ausgelassene Aufnahmen fehlen im Video; echte `video_seconds` aus `frames.jsonl`). Bayer-FITS werden ggf. 2×2 gebinnt –
  Pixel/FWHM gelten für die Analyseauflösung; Ausschnitt-/Analyse-/Originalkoordinaten nie vermischen.
- **Kandidat ≠ bestätigtes Objekt.** Modellkonfidenz ≠ Identität; ein geometrischer Kataloghinweis bleibt ein Hinweis. `line_snr` ist ein integrierter
  **Detektorwert**, keine Sigma-Signifikanz oder Wahrscheinlichkeit. Meteor ≠ Meteorit. Klasse nie nur aus Beispielgeschwindigkeiten ableiten.
- **„Nicht prüfbar“ nie als „nichts da“** darstellen (fehlendes Archiv, Timeout, `needs_archive`, `site_uncertain`). Standort-/Uhr-Vorschläge ändern
  **nie automatisch** etwas – nur auf Klick des Nutzers.
- **Physikalische Vorprüfung** (`motion_plausibility`): Belichtungszeit ist nur Obergrenze, Winkelgeschwindigkeit damit Untergrenze (`≥ …″/s`); die drei
  Vergleichsszenarien sind keine Klassengrenzen; keine Entfernung aus Pixeln behaupten; fehlende/widersprüchliche Belichtung verhindert zeitabhängige
  Katalogabfragen (nie 10 s erfinden). `km/h*` nur mit ausdrücklich angenommener Entfernung (Annahme im Tooltip, Voreinstellung nie erfunden).
- **Sichtbarkeit** = geometrischer WGS84-Horizont (Gelände, Wetter, Nachweisgrenze nicht modelliert).
- **YOLO-Modell** (OpenSkyCam, AGPL-3.0, `models/night-detector/`) ist für Weitwinkel trainiert: bei Teleskopen (Bildfeld < 5°) nur **Hinweis**; reine
  Modellboxen ohne gemessene Linie sind keine Kandidaten. Keine Laufzeitpfade ins Nachbarprojekt OpenSkyCam.
- **Flugzeuge:** kein automatischer Flugabgleich (OpenSky seit 0.45.2 entfernt); nur eigene Flugdaten (`flight_file`/`flight_url`), sonst `needs_archive`.
  Alte Schlüssel `opensky_user`/`auto_aircraft` ignoriert `load_settings`.
- **Overlay-Stil:** gelbe Arial-Schrift mit dunkler Kontur direkt im Bild, keine Beschriftungskästen; Plate-Solving-Objekte als blaue Kreise. Messdetails
  dauerhaft offen (`analysisDetails`); Satellitenvergleich direkt über der echten Aufnahme, kein eigenes Fenster; Bahnen außerhalb des Bilds kennzeichnen,
  nie ins Bild schieben. Anzeigepräferenz je Browser in `stellina-overlay-display`.

## Architektur (Modulordner `astroflow/modules/skytrace/`)
| Datei/Ordner | Aufgabe |
|---|---|
| `__init__.py` | `Module`: Router, `status()`, `activity()`, `check_engine` (prüft `ENGINE_PACKAGES`), `engine_env` (setzt `SKYTRACE_HOME`, `ASTROFLOW_SITES`), `satellite_stats`, `_achievements_loop`; Dienst `skytrace_achievements` |
| `api.py` | FastAPI-Router `/api/skytrace` + `Controller` (Start/Stopp, `run_data`, `delete_run`, Medien mit Range-Anfragen, `orbit3d`, `run3d`) |
| `engine/` | Analyse-Engine, flache Python-Module (`sequence`, `line_detection`, `candidate_report`, `catalog_matching`, `satellite_archive`, `rematch_sites`, `clock_scan`, `global_site` …) und `check_*.py`-Prüfungen. Läuft als **Unterprozess** mit dem Engine-Interpreter |
| `engine/project_paths.py` | `CODE` = `engine/`, `ASSETS` = Modulordner (Modell unter `models/`, `static/evidence.*`), `BASE` = `SKYTRACE_HOME` (Standard `<AstroFlow-Daten>/skytrace`, einstellbar unter Einstellungen → SkyTrace, wirkt nach Neustart) |
| `auto.py` | `AutoAnalysis`: Ereignis `stacking.stacked` → Hardlink-Ordner → Warteschlange (Einstellung `skytrace.auto`) |
| `reanalysis.py` | `/reanalyse`: Originale aus Manifesten wiederfinden (`eingang.json`) |
| `rematch.py` | `SiteJobs`: Neuabgleich, Standortsuche, Nächte, Heatmap als Aufträge neben dem `Controller` |
| `sitefit.py`, `orbit3d.py`, `run3d.py`, `satdetail.py` | Rückrechnung Ort+Uhr; 3D-Bahnen; Lauf-Ansicht (Teleskopweg); Satelliten-Infokarte (nur Gespeichertes) |
| `stats.py`, `satellites.py`, `achievements.py` | Statistik, Satellitenhelligkeit/-rate, Erfolge (`erfolge.json`) |
| `static/` | `skytrace.js` (Ansichten), `evidence.js/css` (Messgrafiken, auch im Bericht), `skytrace-erde.js` (Globus + Seitenleiste), `skytrace-3d.js` (Lauf-Ebene), `skytrace-karte.js` (flache Karte), `skytrace-erfolge.js`, `skytrace-satelliten.js`; Erdtextur unter `static/vendor/` |
| `docs/`, `models/`, `requirements-engine.txt` | Nutzerdoku; Modellgewichte + Lizenz; Engine-Pakete (PyTorch vorher aus dem CPU-Index, s. `docs/entwicklung/installation.md`; Docker `--build-arg SKYTRACE=1`) |

- **Interpreter:** Analysen laufen mit dem Interpreter aus Einstellungen → SkyTrace, sonst `<SKYTRACE_HOME>/.venv`, sonst dem Python von AstroFlow.
  Der Server importiert nur **leichte** Engine-Teile (`json_store`, `observation_settings`, `video_overlays`) –
  ohne OpenCV/PyTorch startet die App trotzdem. `sitefit.py`/`orbit3d.py` laufen bei fehlendem `sgp4` im Server als Skript im Engine-Interpreter.
- **Sicherheit:** kein eigener Token; der AstroFlow-Kern (Host-/Herkunftsprüfung, Gerätekopplung) schützt die Routen (Haupt-`AGENTS.md` → Sicherheit).
  LAN-Zugriff regelt AstroFlow. Dateien nur unterhalb fester Wurzeln ausliefern (`skytrace.within`, Medien-Route); Pfade vom Client wie in der Haupt-`AGENTS.md` Punkt 6.
- **Kern-Dienste, optional:** `night_zero_points` (Bilder), `project_frames`, `catalog_resolve`, `object_facts` (Sage), `sites`/`site_for`. Fehlt einer, läuft SkyTrace weiter.
- **`core/sites.py`** bleibt reine Standardbibliothek: die Engine lädt sie über den Pfad (`ASTROFLOW_SITES`).
- **Nicht übernommen** aus dem Original: Tk-Oberfläche, Starter-Skripte, Web-Prüfungen gegen den alten Server, eigener Port. `engine/check_*.py`
  laufen im Engine-Interpreter mit gesetztem `SKYTRACE_HOME` (manche brauchen echte Stellina-Daten: Ordner per `SKYTRACE_SAMPLES`).
- **Laufdaten** (`Ergebnisse/<Lauf>/`): `videos.json`, `bericht.html`, `kandidaten.json`; je Beobachtung `frames.jsonl`, `ausgeschlossen.jsonl`, `ereignisse.mp4`,
  `original.mp4`, `live.jpg`, `spuren.json`, `platesolve/`, `kandidaten/Kxxxx/` (PNGs, `kandidat.json` mit `world_path`, `frames`, `catalogs`, `evidence`).
  Einstellungen: `catalog_settings.json`, Katalog-Cache `catalog_cache/` (Archive darunter `archives/`).

## Wenn du … änderst (Pflichtregeln)
- **JSON/Bilder** atomar veröffentlichen (`json_store`, `image_store`; bei HTTP Content-Length und Inhalt aus demselben Dateihandle). Windows liefert bei parallelen
  Vorschauzugriffen kurz `PermissionError` → vorhandene Retry-Helfer nutzen; SQLite-Verbindungen ausdrücklich schließen.
- **Detektion/Schwellen** (`line_detection`, `frame_quality`, `trail_validation`): Fehlalarme an den Vergleichsfällen prüfen (`skytrace-erkennung.md`); nie
  horizontale Linien pauschal verbieten; Anzahl allein ist nie Ausschlussgrund; für geänderte Detektion einen eigenen Ergebnislauf (`rebuild_trails.py`).
- **Kandidatenfelder / Antworten von `/run`:** neue Felder je Videobild nur, wenn sie nicht mit Bilder × Kandidaten wachsen (Handy!); große Daten in eigene
  Endpunkte. `/statistics` entfernt alle `_`-Schlüssel.
- **Zeit:** Kamerazeit bleibt Kamerazeit; der Uhrversatz (`clock_offsets`, wahre UTC = Kamerazeit + `offset_s`) wirkt nur über `catalog_matching._times` /
  `observation_settings.clock_offset_at`. Neue Zeitrechnung (3D, Abgleich) muss ihn beachten.
- **Standort:** immer über `resolve_site`; `accuracy_m` bestimmt die Abgleichstoleranz. Neue Vorschläge (Ort/Uhr) nur als Vorschlag, mit Zufalls-/Fehlalarmschätzung
  und ohne Netz (`orbits_cache_only`, `satcat_offline`).
- **Erfolge:** IDs bestehender Erfolge nie ändern; einmal vergeben bleibt vergeben. Ton freundlich-ironisch, keine Firmen-/Personennamen als Zielscheibe.
- **Statistik und Oberfläche** zählen mit derselben Einordnung (`evidence.js classification` ↔ `achievements.classify`) – beide ändern, wenn eine sich ändert.
- **Neue Ansichten:** Route in `ROUTES` von `tests/test_ui_layout.py`; Frontend hängt sich über `AF.modules.skytrace` ein; Router kennt keine `?`-Parameter
  (Fokus über Merker wie `focus3d`). Overlay-Sichtbarkeit über `AstroEvidence.setOverlayVisible`; Medienbeobachter beim Tabwechsel aufräumen.
- **Kandidaten-Abgleiche ohne Netz neu rechnen:** `engine/refresh_evidence.py` (Messdetails/lokale Satellitendiagnostik), `refresh_satellites.py`, `refresh_overlay_labels.py`,
  `refresh_satellite_overlays.py` – nur lokale Daten. `candidate_report.py <Lauf> --stop-file …` kann dagegen Online-Abfragen auslösen.
- **Laufender Server:** vor einem Neustart `/api/skytrace/state` auf laufende Arbeit prüfen; Läufe nie wegen einer Änderung abbrechen. Stopp ist kooperativ
  (STOP-Datei, MP4 wird finalisiert); Worker laden neuen Python-Code erst beim nächsten Start. Nicht gleichzeitig aus zwei Oberflächen auf denselben `SKYTRACE_HOME` analysieren.
- **Doku:** Befunde ins passende `skytrace-*.md`; hier nur Regeln. Dateiangaben gegen das Repo prüfen.

## Katalogabgleich (Kurzfassung; Langfassung `skytrace-satelliten-uhr.md`)
- **Asteroiden:** JPL SB-Ident, serielle Abfragen mit Dienstsperre und Cache, **45 s Gesamtzeitlimit** (inkl. Sperrwartezeit), stoppbarer HTTP-Unterprozess.
- **Satelliten:** lokale Archive (Space-Track-Spiegel, CC-BY-4.0) bevorzugt, sonst ETH-Abruf je Belichtungsmitte (Pagination ≤ 120 Seiten/180 s; nur
  vollständige Antworten atomar cachen; ETH sammelt seit 10.03.2023 – fehlende Abdeckung ist kein Ausschluss; keine aktuellen TLEs für alte Aufnahmen).
  SGP4, Bahnalter standardmäßig ≤ 3 Tage, Vorauswahl vor exakter Propagation, Toleranz 360″, Spurabdeckung ≥ 50 %; bis zu 8 geprüfte Bahnen samt Ablehnungsgründen.
- **GMN** (Meteorbahnen): vorhandene Monatsdateien, SQLite-Zeitindex plus Horizont. **CNEOS:** nur vorhandener Zeitcache, Ereignisse unter dem Horizont verwerfen.
- ASTAP (Plate-Solving, `engine/solving.py`): bevorzugt `tools/astap/astap_cli.exe` unter `SKYTRACE_HOME`, sonst die ASTAP-Installation; Deep-Sky-Beschriftung aus `deep_sky.csv`.

## Tests
- Server/Modul: `tests/test_skytrace.py` (Engine-Teile nur mit skyfield/sgp4), `test_skytrace_achievements.py`, `_satelliten.py`, `_satellit.py`, `_rematch.py`, `_nights.py`,
  `_clock.py`, `_global.py`, `_alternatives.py`, `_reanalysis.py`, `_frames.py`, `_3d.py`, `_3d_run.py`, `_field_motion.py`, `_compact.py`, `_performance.py` (die letzten
  drei laufen mit OpenCV unter pytest oder direkt im Engine-Interpreter).
- Browser (Playwright): `test_skytrace_3d_ui.py`, `_erde_ui.py`, `_video_ui.py`, außerdem alle SkyTrace-Routen in `test_ui_layout.py`. Fixtures mit erfundenen Orten.
- Welche Engine-`check_*.py` wozu (jeweils `<Engine-Python> engine/<Datei>` mit `SKYTRACE_HOME`; einige nutzen echte lokale FITS):

| Änderung | Prüfungen |
|---|---|
| Kataloge/Stopps | `check_dynamic_satellites`, `check_auto_catalogues`, `check_catalog_wait`, `check_candidates` |
| Messdetails / Vorprüfung | `check_evidence`, `check_candidates`, `check_motion_plausibility` (App **und** eigenständigen Bericht mit Zoom/Overlay ansehen) |
| Sensorstreifen, doppelte Spuren | `check_banding`, `check_streak_merging`, `check_sequence` |
| Bewegung/Schärfe, Sternschweife | `check_ensemble`, `check_quality`, `check_registration_data`, `check_field_tails`, `check_trail_validation` |
| Overlays / Blinklichter | `check_video_overlays`, `check_satellite_overlay`, `check_blink_trails` |

## Wegweiser (Thema → Datei)
| Thema | Datei |
|---|---|
| Erkennung: Liniendetektor, kurze Spuren, Spurverkettung, Modellhinweise, Blinklichter, Leistung, Vergleichsfälle | `docs/entwicklung/skytrace-erkennung.md` |
| Satellitenabgleich, Zeitbezug, SATCAT-Sperre, Uhrversatz je Nacht | `docs/entwicklung/skytrace-satelliten-uhr.md` |
| Standorte, Toleranz, Rückrechnung, Neu abgleichen, Standortsuche (Nächte, ohne Vorwissen), Alternativen | `docs/entwicklung/skytrace-standorte.md` |
| 3D-Erde, flache Karte, Heatmap, Lauf-Ansicht in 3D, Satellit anklicken, `orbit3d` | `docs/entwicklung/skytrace-erde-3d.md` |
| Lauf-Ansicht (Zeitreihe, Video, Overlays), Neuanalyse (`/reanalyse`), Handy-Größe, Löschen, Auto-Analyse nach dem Stacken | `docs/entwicklung/skytrace-lauf-ansicht.md` |
| Statistik, Satellitenhelligkeit und -rate, Erfolge | `docs/entwicklung/skytrace-statistik-erfolge.md` |
| Standortliste des Kerns (`core/sites.py`) | `docs/entwicklung/standorte-himmel.md` |
