# Daten, Import, Plate-Solving, Server

> Ausgelagert aus `AGENTS.md` am 09.11.2026, Text unverändert übernommen. Regeln, die beim Ändern von Code gelten,
> stehen zusätzlich in Kurzform in `AGENTS.md` → „Wenn du … änderst“. Neue Befunde hier ergänzen, nicht in `AGENTS.md`.

## Speicher, volles Laufwerk, Aufräumen (Schritte 11–13 der Pipeline)

11. **Speicher** (`Settings.delete_uploads`, Env `ASTROFLOW_DELETE_UPLOADS`, Standard an, UI: Einstellungen → Speicher): nach erfolgreichem
   Durchlauf löscht `Pipeline.delete_stacked_uploads` hochgeladene Lights (nur unter `input/uploads`, gestackt oder verworfen) → `frames.deleted=1`,
   `frames.size`. Hochgeladene Duplikate werden beim Scan sofort gelöscht. Folgen: kein komplettes Neu-Stacken mehr (Pipeline wirft klare
   `ValueError`, API 409, UI sperrt Menüpunkt/Auswahl/Auflösung/Ein-/Ausschließen); gelöschte Frames behalten Status und Analyse und werden nie
   reanalysiert; Nachstacken neuer Nächte und Bearbeitung laufen normal (nur Stack + `state.json`-Referenzsterne nötig). Eingebundene Ordner,
   am Ort eingebundene Hauptordner-Dateien (Pfad außerhalb von `input/uploads`, `Pipeline._is_upload`) und Kalibrierbilder werden nie gelöscht. DB-Spalten kommen über `db.MIGRATIONS` (einfaches ALTER TABLE beim Start).
12. **Volles Laufwerk** (`diskspace.py`, seit 01.11.2026 – Hauptordner-Import von E: kopierte 95 GB nach C:, Platte voll, jeder Auftrag
   scheiterte, auch SQLite): Worker startet nur ab `MIN_FREE` 5 GB (`Engine.hold()`), `prog` prüft alle 5 s und bricht unter `ABORT_FREE` 2 GB ab;
   Volle-Platte-Fehler (`diskspace.is_full`, auch `sqlite3 … full`) und Abbruch → Job wieder `queued` + `Engine.paused` (Grund in
   `status.hold`/`paused`). `POST /api/stacking/queue {paused}` (UI: Aktivität → „Aufträge anhalten/fortsetzen“; `cancel`-Event bricht beim
   nächsten Fortschritt ab, Stacking setzt am Checkpoint fort). Pause ist nur im Speicher (Neustart = fortsetzen). Watcher scannt unter 2 GB nicht.
   Import (`library.start_import`): kopiert seit 0.12.0 keine Rohdaten mehr (s. „Hauptordner durchsuchen“) → nur noch `MIN_FREE`-Prüfung, sonst 400;
   während des Imports Abbruch unter 5 GB – nicht übernommene Dateien bleiben in `to_import` (Gruppe `partial`), Import später fortsetzbar.
   Aufräumen (`housekeeping/cleanup`) ist bei stehender Warteschlange (`hold()`) trotz wartender Aufträge erlaubt und hält sie währenddessen an.
   Test: `test_diskspace.py`.
13. **Speicherbelegung & Aufräumen** (`housekeeping.py`, API `GET /housekeeping?keep=`, `POST /housekeeping/cleanup {keys, keep}`,
   UI: Einstellungen → Bilder → „Speicherbelegung“, seit 31.10.2026): Belegung je Bereich + größte Objekte; Aufräumbares: verwaiste
   Ausgabedateien, Stacks gelöschter Objekte, Temp (> 1 h), leere Ordner (> 1 h), DB verdichten (VACUUM) – vorausgewählt; ältere Versionen
   (neueste `keep` je Objekt bleiben, nicht bei importierten Bildern) und Sicherungen – nur auf Wunsch. Client schickt nur Schlüssel, der Server
   ermittelt die Pfade neu und löscht nur innerhalb des Datenordners; 409 während eines Jobs. Rohdaten und Stacks bestehender Objekte nie.

## Hauptordner durchsuchen (`modules/stacking/library.py`, seit 31.10.2026)
Import-Ansicht → „Hauptordner durchsuchen“ → „Ordner wählen …“ (`folderDialog`, kein Pfad eintippen; Wahl startet die Suche): Ordner auf dem Server-Rechner (z. B. ganze Stellina-Platte). Hintergrund-Thread findet Gruppen
(`find_groups`: „…_observation_X“ = eine Gruppe samt Unterordnern, sonst je Ordner mit FITS; Ordnernamen dark/flat/bias → Kalibrierung; Ordner nur mit
JPG/PNG/TIFF → fertige Bilder, nicht unterhalb von FITS-Gruppen), bildet je Datei den Inhalts-Fingerabdruck (`scanner.fingerprint`, Bilder SHA256) und
gleicht mit `frames.info.fp` bzw. `results.stats.imported.sha256` ab – **Schnellpfade** (seit 01.11.2026; Stellina-Platte 31 718 FITS: vorher ~19 min, erster Lauf 56 s, danach 1,1 s ohne Lesezugriff): (1) `name_key` = Pfad ab dem `…_observation_X`-Ordner + Dateiname (+ `frames.size`, falls bekannt – wird seit 01.11. beim Einlesen gesetzt, älter nur nach dem Löschen) → bekannt ohne Lesen; (2) Tabelle `library_index` (Pfad, Größe, mtime → Fingerabdruck); (3) Beobachtungsordner ohne Namenstreffer: nur `SAMPLE` = 4 Stichproben (gemerkte zählen mit) – trifft keine, ist der Rest neu (`skipped`), sonst ganze Gruppe lesen (z. B. M3: früher ohne Ordnernamen hochgeladen). Größen aus `os.scandir` (kein stat je Datei) → Zustand `new|partial|unstacked|done` (fertig = gestackt/verworfen/Rohbild gelöscht/Duplikat).
Import übernimmt nur **unbekannte** Dateien und **kopiert nie** (seit 0.12.0, Nutzerwunsch – vorher Rückfall Kopie, 95 GB von E: nach C:):
gleiches Laufwerk (`diskspace.same_drive`) → Hardlink unter `input/uploads/bibliothek/<hash>/<Gruppenordner>/…` (wird nach dem Stacken wie ein
Upload gelöscht, Original bleibt); sonst (anderes Laufwerk, exFAT, Netzlaufwerk, `os.link` scheitert) **am Ort eingebunden**:
`scan_and_queue(extra=[Dateien])` → `scanner.scan_dir(..., extra)` liest genau diese Dateien einmal ein, Frame-ID = sha1(absoluter Pfad|Größe|mtime),
`frames.path` zeigt auf das Original. Der Watcher sieht nur `input/` – solche Dateien werden nie erneut gescannt, also auch nicht doppelt
oder als verschwunden behandelt. Gelöscht werden sie nie (`_is_upload`). Wiedererkennung beim nächsten Suchlauf über `name_key`/Fingerabdruck wie gehabt.
**Quelle nicht erreichbar** (USB-Platte ab): `Pipeline.offline(rows)` = nicht gelöscht, Datei fehlt. `stack()` analysiert/stackt sie nicht (kein
„Lesefehler“, Status bleibt), erzwingt wegen ihnen kein Neu-Stacken und wirft bei Neuaufbau mit fehlenden ausgewählten Frames einen klaren `ValueError`
(API `process {restack}` → 409); `Engine.pending_projects` zählt sie nicht (sonst endlose Leerläufe), holt sie aber nach, sobald die Platte wieder da ist;
`/projects/{id}/frames` liefert `offline`. Fehlen Aufnahmen bzw. Kalibrierbilder ganz, wirft die Pipeline `calibration.SourceOffline` (mit `dirs`):
`Engine._failed` merkt die Ordner in `jobs.options.offline`, `pending_projects` gibt das Objekt erst wieder frei, wenn alle zurück sind (sonst gälte
„letzter Auftrag Fehler → nicht neu versuchen“ für immer, die Dateien werden ja nie neu gescannt). Analyse markiert dabei nichts als `failed`.
Master-Kalibrierung: `CalibrationLibrary._cached` merkt je Pfadliste den zuletzt gebauten Master (`<tag>_<hash>.ref`) – ohne erreichbare
Quelle (keine Änderungszeit für den Schlüssel) wird er weiter genutzt; Bauen/Lesen unter `lock` (parallele Analyse-Threads).
Test: `tests/test_import_in_place.py`.
**Versteckte Einträge** (seit 0.46.2, `core/hidden.py` → `hidden_entry`): Name mit „.“ (Stellina-Platte: `.thumbnail`, `.webaxs_*`, `.webview` erschienen als
„Fertige Bilder · neu“) und unter Windows Attribut „versteckt“ (bei Dateien auch „System“) fallen in `library._walk`, `scanner.list_fits`,
Ordnerdialog/Suche und Upload (Server + `droppedFiles`/`upload` im Browser) weg; ein bewusst gewählter Startordner selbst wird gelesen. Test `test_versteckt.py`.
**Unlesbare FITS-Dateien** (`scanner.looks_like_fits(path, size)`: ≥ 2880 Byte und erste 9 Byte `SIMPLE  =`; Anlass echte Stellina-Platte: 0-Byte-,
abgebrochene und kopflose Dateien blieben ewig „neu“, Gruppe dauerhaft „teilweise“): Der Suchlauf prüft nur Dateien ohne Namens-/Index-Treffer, die sonst als neu
gälten – Größe < 2880 ohne Lesezugriff, sonst die ersten Bytes (Thread-Pool, vor dem Fingerabdruck). Kaputte Dateien bekommen in `library_index.fp` die Marke
`library.BROKEN` (gleiche Größe + mtime = nicht erneut prüfen; gültige werden je Lauf nur kurz neu angeschaut, Lesefehler gelten nicht als kaputt) und zählen in
`_describe` weder als neu noch als bekannt: Gruppe bekommt `n_broken` + `broken` (relative Pfade, ≤ 50), `n_files`/`n_new`/Zustand rechnen ohne sie (alle übrigen
fertig → `done`); nie in `to_import`. UI: Chip „n unlesbar“ (warn, Tooltip mit Dateinamen), Zustand `partial` heißt „teilweise importiert“, Link „Objekt: X“.
UI-Filter (seit 0.21.0, eine Leiste über der Liste): Zustand als Reiter (`LIB_TABS`, Standard „Offen“ = `n_new > 0`), Art/Format als `.menu`-Mehrfachauswahl,
localStorage `af.library.filter` (`{tab, kind, ext}`); Formate je Gruppe aus `library.file_formats` → `groups[].exts` (`{"fits": 141}`);
„Alle neuen“ (Dreifach-Kästchen) und Import wirken nur auf sichtbare Gruppen. Beim ersten Laden sind Gruppen mit Neuem vorgewählt (wie vor 0.21.0).
**Ziel neuer Gruppen** (`Library._target`): Lights-Gruppen ohne bekannte Aufnahmen bekommen `target {id, name}` = Projekt, dem der Scan sie zuordnen würde (`scanner.assign_project` mit den einmal je Lauf geladenen Projekten, erste Light-Datei, ohne Plate-Solving; Kometen/Felder/Unbekannt kein Ziel); UI-Chip „ergänzt: …“ (`project` bleibt „Dateien schon drin“). Test `test_library_predicts_target_of_new_observation`.
Bilder gehen über `import_picture` (Kopie in die Ausgabe). Quelldateien bleiben unberührt. API: `GET /library`, `POST /library/scan {root}`, `POST /library/import {ids}` (Ids stammen
aus dem letzten Suchlauf im Speicher). Zuletzt genutzter Pfad: `data/settings.json` → `library.root`. Test: `tests/test_library.py`.

## Plate-Solving (`modules/stacking/platesolve.py`)
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
  Einstellungen landen in `data/settings.json` (`modules/stacking/api.PERSIST`, über `core/store.py`) und haben Vorrang vor Env (API-Schlüssel dort im Klartext).

## Server (`server.py` + `modules/stacking/api.py`)
Stacking: Stack-Thread `Engine.worker` (Job-Queue `q`, `jobs`-Tabelle; Option `{"action":"solve"}` = Plate-Solving-Job) + Entwicklungs-Thread
`Engine.developer` (Queue `dq`, seit 01.11.2026, Nutzerwunsch): nach `Pipeline.stack` übernimmt er `Pipeline.finish` (Entwickeln, Melden,
Uploads löschen) bzw. reine Neu-Entwicklungen, während schon das nächste Objekt gestackt wird; höchstens ein fertiger Stack wartet
(Job bleibt `running`, 86 %, „wartet auf die Endbearbeitung“). Abbruch/volle Platte in beiden Phasen → Job zurück in `q` + Watcher-Thread (Scan alle N s, Auto-Verarbeitung),
beide in `modules/stacking/api.py` (`Engine`). `Pipeline._scan_lock` serialisiert Scan und Nach-Solving. Beim Start werden unterbrochene Jobs wieder eingereiht.
Während eines laufenden Jobs eingelesene Lights (`status='new'`) werden nach Erfolg automatisch in einem Folgelauf ergänzt;
der Watcher berücksichtigt auch bereits gescannte, noch neue Frames. Letzte Fehler werden ohne weiteren Upload nicht endlos neu gestartet.
`Engine._queue_lock` verhindert doppelte Aufträge durch gleichzeitig arbeitende Worker/Watcher.
Objekt löschen (`DELETE /api/stacking/projects/{id}` → `Pipeline.delete_project`, 409 während eines Jobs): Ergebnisse, Stack, Jobs, hochgeladene
Rohbilder weg; Frames aus eingebundenen Ordnern bleiben als `kind='ignored'` in der DB, `_fingerprints` überspringt sie.
`results/{id}/objects` (seit 0.21.0): Katalogobjekte in JPG-Pixeln aus `work/p<id>/wcs.json` über `services.project_solution`, Cache
`work/p<id>/objekte_r<rid>.json` (neu, wenn wcs.json jünger) – Frontend `plateLabels(rid)` als `createViewer`-Overlay, Klasse `.hover-labels`
(nur beim Überfahren sichtbar). Seitenwechsel-Geist blendet `canvas`/`video` aus (freigegebenes WebGL wäre weiß). Test: `test_result_objects.py`.
REST unter `/api/stacking`: `status`, `scan`, `projects[/{id}]` (GET/PATCH/DELETE), `projects/{id}/process|solve|frames`, `frames/{id}` (PATCH manual),
`jobs`, `develop-all` (alle Objekte mit Master-Stack neu entwickeln, Galerie-Knopf), `tutor`, `results/{id}/tutor[/ki]`, `upload` (behält bis zu 4 Ordnerebenen, blockt `..`), `results/{id}/{tif|png|jpg|thumb|fits|before}`, `reviewer`, `solver`, `storage`, `starnet`.
Tiefe/Vergleich: `results/{id}/depth|compare[/<datei>]`, `insights` (s. `analyse-tiefe-vergleich.md`).
Kern: `/api/app` (Module, Build), `/api/folders?path=` (Ordnerdialog), `/api/sites…` (Ortsliste), `/api/status` (`{version, busy, modules:{id: status}}`), `/api/activity` (alle Module), `/api/modules` (ein/aus).
**Achtung:** Zwei Server auf demselben Datenordner = zwei Worker (beim Start werden „running“-Jobs des anderen neu eingereiht) – Tests/Vorschauen mit eigenem Datenordner.


## Antwortkompression (aus „Sicherheit“ verschoben)

Antworten ab 2 KB werden per `core/gzip.py` komprimiert (seit 0.31.1, Handy im Heimnetz; eigene Middleware statt Starlettes `GZipMiddleware`, deren Ausschlussliste ältere erlaubte Versionen nicht kennen). Nur Text/JSON/JS/SVG mit Status 200, nie Downloads, Medien oder 206. Große JSON-Antworten trotzdem klein halten, siehe SkyTrace „Laufdaten fürs Handy“. Schlüssel stehen im Klartext in `data/settings.json`.
