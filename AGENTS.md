# AGENTS.md – AstroFlow

Kompakter Einstieg für KI-Agenten und neue Sessions. Falls vorhanden zusätzlich `AGENTS.local.md` lesen (lokal, nicht im Repo). Nutzerdoku: `README.md`, Änderungen: `CHANGELOG.md` (bei Nutzer-sichtbaren Änderungen ergänzen), Wünsche für später: `docs/FEATURE-WUENSCHE.md` (bei Umsetzung Status nachtragen).

## Was ist das
AstroFlow: lokale Astro-Plattform im Browser aus drei Modulen (seit v0.6, 28.10.2026 – Nutzerwunsch: „ein Produkt, einheitliche UI,
Module einfach wartbar und erweiterbar“):
- **Bilder** (`modules/stacking`, Kernmodul): vollautomatische Pipeline FITS-Rohbilder → fertiges Astrofoto.
- **SkyTrace** (`modules/skytrace`, aus Repo `JackONeill199/SkyTrace`): Lichtspuren/bewegte Objekte in Bildfolgen, Katalogabgleich. Details: `astroflow/modules/skytrace/AGENTS.md`.
- **Sage** (`modules/sage`, aus Repo `JackONeill199/starlight-sage`, Node → Python portiert): seit 2.0 lebende Astronomie-Enzyklopädie – Bilder bestimmen, Objekte aus Fachkatalogen + KI-Agent recherchieren (Belegpflicht), persönliches Beobachtungsgedächtnis, Wissensnetz, proaktive Entdeckungen, Chat, Geschichten/Audioführung. Details: `astroflow/modules/sage/AGENTS.md`, Architektur `docs/SAGE.md`.
Zielgruppe: Smart-Teleskope (Seestar, Vespera, Stellina) und klassische Astrokameras. Lokal, ein Nutzer, keine Konten.
Stack: Python 3.12+ · numpy/scipy/astropy · FastAPI + uvicorn · SQLite · Vanilla-JS-SPA ohne Build-Schritt. Lizenz MIT (Detektormodell in SkyTrace: AGPL-3.0, siehe `models/`). Version: `astroflow/__init__.py` (Regeln unter „Versionierung“).

**Sprache:** UI, README, Kommentare und Fehlermeldungen auf **Deutsch**. Der Nutzer schreibt Deutsch.

## Produkt-Architektur (Kern + Module)
```
astroflow/server.py        Kompositionswurzel: lädt Module, /api/app|status|activity|modules, Herkunftsschutz, statische Dateien
astroflow/core/            config.py (Settings), store.py (data/settings.json, ein Abschnitt je Thema), modules.py (BaseModule, Context, Registry), sites.py (Ortsliste)
astroflow/modules/<id>/    __init__.py (class Module), api.py (Router → /api/<id>), static/ (→ /m/<id>/), AGENTS.md
astroflow/static/          Shell: index.html, app.js (Router, Navigation, Übersicht, Aktivität, Einstellungen, Palette, Viewer, Helfer), style.css
```
- **Modul-Vertrag Backend** (`core/modules.py`): `router()`, `start()/stop()`, `status()` (klein, wird alle 2,5 s gepollt; `busy_jobs` zählt für die Anzeige),
  `activity()` (gemeinsames Format `{key,title,kind,detail,state,progress,started,finished,href}`), `scripts/styles`. Reihenfolge/Liste: `AVAILABLE`.
- **Module reden nicht per Import miteinander**, sondern über Dienste in `ctx.services`: Stacking stellt `plate_solver` (`PictureSolver`),
  `project_picture`, `project_solution`, `project_frames`, `observation_log`, `catalog_resolve`, `catalog_objects` und `site_sky` (Himmelsqualität je Ort) bereit, Sage `object_facts` (Typ/Entfernung aus der Wissensbasis) und `collection_progress` (entdeckte Messier-Nummern, für „Heute Nacht“); Sage und SkyTrace nutzen sie. Der Kern selbst stellt `sites` (`SiteBook`), `site_for` und die Liste `night_sources` bereit (s. „Standorte“). Fehlt ein Dienst, muss das Modul trotzdem laufen.
- **Ereignisse** (`ctx.on` / `ctx.emit`, Liste in `core/modules.py`): `stacking.stacked(project_id, result_id, name)` kommt aus
  `Pipeline.process` nach dem Entwickeln und **vor** dem Löschen hochgeladener Rohbilder (`Pipeline.on_stacked`). Empfänger (seit 28.10.2026,
  Nutzerwunsch „gestackte Aufnahmen automatisch durch SkyTrace und in die Entdeckungen, mit animierten Erfolgen“):
  SkyTrace `auto.AutoAnalysis` (Hardlinks je Nacht → Warteschlange, Einstellung `skytrace.auto`) und Sage `Module._stacked`
  (Bild bestimmen → Entdeckungen, Einstellungen `sage.auto_discover`/`auto_story`). Empfänger kehren sofort zurück (eigene Threads).
- **Modul-Vertrag Frontend** (`AF.register(id, {nav, routes, summary, recent, discoveries, homeFeatured, settings, palette, status, drop, sitesPanel})`, Kommentar in `app.js`; `sitesPanel(host)` = eigener Abschnitt unter Einstellungen → Standorte;
  `nav[].area` 'work' = Arbeitsbereich, 'library' = Bibliothek, sonst Unterpunkt (optional `parent`, 'home' = unter der Übersicht);
  Kontextpanel rechts per `AF.ctx.open(el, {title, html|render})`, wird beim Ansichtswechsel geleert; Prozessanimationen `vizProcess(host, {kind, …})` aus `static/viz.js`): Helfer (`api`, `send`, `toast`,
  `createViewer`, `folderDialog(start, pick, {title, ok, hint})` (Ordner auf dem Server-Rechner wählen, über `/api/folders` → `core/folders.py`: Laufwerke, Unterordner, FITS-/Bildzählung; genutzt von Hauptordner und SkyTrace; Suchfeld seit 0.31.0: sofort im aktuellen Ordner, entprellt 300 ms + abbrechbar `/api/folders/search?path=&q=` = Breitensuche Tiefe ≤ 4, ≤ 200 Treffer, ≤ 300 000 Einträge/5 s – je Eintrag geprüft, auch mitten in großen Ordnern, Schlüssel `folders.norm`/`folderKey` ohne Leerzeichen/„_“/„-“; bewusst keine Mehrfachauswahl, beide Aufrufer nehmen genau einen Ordner), `picker` (Dropdown mit Suche für Modelle/Stimmen, Listen aus `core/models.list_models`), `jobLine`, `confirmDlg`, `crumbs`, `store` …) sind global. Modul-Skripte lädt die Shell aus `/api/app` mit `?v=<build>`
  (Build-Kennung aus Dateizeiten) – nur `index.html` braucht weiter das manuelle `?v=`.
- Module ein/aus: Einstellungen → Module & System bzw. `ASTROFLOW_MODULES=stacking,skytrace`; ein defektes Modul erscheint mit Fehler, die App läuft weiter.
- Sicherheit: schreibende Anfragen mit fremdem `Origin` → 403 (Middleware `guard`). Antworten ab 2 KB werden per `core/gzip.py` komprimiert (seit 0.31.1, Handy im Heimnetz; eigene Middleware statt Starlettes `GZipMiddleware`, deren Ausschlussliste ältere erlaubte Versionen nicht kennen). Nur Text/JSON/JS/SVG mit Status 200, nie Downloads, Medien oder 206. Große JSON-Antworten trotzdem klein halten, siehe SkyTrace „Laufdaten fürs Handy“. Schlüssel stehen im Klartext in `data/settings.json`.
- **Neues Modul:** Unterpaket mit `Module(BaseModule)` anlegen, in `AVAILABLE` eintragen, `static/<id>.js` mit `AF.register`, Tests in `tests/`.

## Versionierung (verbindlich, seit 01.11.2026 – Nutzerwunsch)
- **Eine Quelle:** `astroflow/__init__.py` → `__version__`. `server.py` (`VERSION`, `/api/app`, `/api/status`) liest sie; nirgends sonst
  eine Nummer pflegen. Nicht verwechseln mit `quality.METRICS_VERSION`, `graxpert.GRAXPERT_VERSION` (Cache-Stände) und den Ergebnis-Versionen je Objekt.
- **Schema `0.MINOR.PATCH`** (vor 1.0): PATCH = Fehlerbehebung, Leistung, Texte – ohne neue Funktion. MINOR = neue Funktion/Ansicht/Einstellung/Modul,
  geänderte DB-Struktur, Datenformate oder API. 1.0 nur auf Entscheidung des Nutzers.
- **Jede für Nutzer sichtbare Änderung, die nach `main` geht**, erhöht im selben Branch die Version und bekommt ganz oben im `CHANGELOG.md`
  einen Abschnitt `## <Version> – <TT.MM.JJJJ>` (darunter `### Neu` / `### Behoben`). Mehrere Änderungen bis zum nächsten Merge = eine Version.
  Rein interne Änderungen (Tests, AGENTS.md, Refactoring ohne Wirkung) ohne Erhöhung.
- **Git:** Arbeit auf `feature/…` bzw. `fix/…` von aktuellem `main`, PR gegen `main`; direkt auf `main` nur auf ausdrücklichen Wunsch.
  Sind zwei Branches mit Versionserhöhung offen, nimmt der später gemergte die nächste freie Nummer (beim Merge-Konflikt anpassen).
- **Nach dem Merge in `main`:** annotiertes Tag auf den Merge-Commit – `git tag -a v<Version> -m "AstroFlow <Version>"` und `git push origin v<Version>`.
- **Prüfung:** `tests/test_version.py` – Format, oberste CHANGELOG-Version = `__version__`, Server meldet dieselbe.

## Befehle
```bash
.venv/Scripts/python -m pytest -q                # Windows-venv liegt im Repo (.venv); parallel (xdist, pytest.ini) ~1,2 min auf 16 Kernen (seriell 5,5 min); muss grün sein
.venv/Scripts/python -m pytest -q -m "not ui"    # schneller Teilsatz ohne Browser-Tests (~40 s)
.venv/Scripts/python -m pytest -q -p no:xdist -o addopts=""   # seriell (zum Debuggen eines einzelnen Tests, `-s`, pdb)
python -m astroflow serve --port 8080 --data ./data   # Web-UI (alle Module)
python -m astroflow process --data ./data             # alles per CLI verarbeiten
Start.bat / Stop.bat                                  # Windows: Server im Hintergrund starten/beenden (Port 8766, ./data)
Netzwerk-freigeben.bat [entfernen]                    # Firewall-Regel „AstroFlow LAN“ (TCP 8766, privat, LocalSubnet; braucht Admin – Nutzer führt aus)
python -m astroflow demo ./data/input/demo            # synthetische Testdaten (Bayer, 2 Nächte, Darks/Flats)
docker compose up -d --build                          # Image enthält ASTAP + D05 (ARG ASTAP_DB=d05|d50)
python installer/build_windows.py                     # Windows: portables ZIP (+ Installer mit Inno Setup) nach dist/, s. „Installation und Einrichtung“
astroflow                                             # installierte Version: Server im Hintergrund + Browser (Port 8766, ~/AstroFlow); astroflow stop
```
Git-Repo: github.com/JackONeill199/astroflow (Branch main; `data/`, `data_real/`, `.venv`, `AGENTS.local.md` per .gitignore ausgeschlossen).
Tests laden standardmäßig nur das Stacking-Modul (`tests/conftest.py`); Modultests: `test_modules.py`, `test_sage.py`, `test_skytrace.py`.
SkyTrace-Engine-Pakete (OpenCV, PyTorch …) sind **nicht** in `requirements.txt` – siehe `astroflow/modules/skytrace/requirements.txt`. Kein Linter/Formatter konfiguriert – Stil des umliegenden Codes übernehmen (knapp, deutsche Kommentare).

## Modul Bilder – Ablauf (`modules/stacking/pipeline.py`; alle Pipeline-Dateien liegen in `astroflow/modules/stacking/`)
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
   **Fertige Bilder anderer Software** (`fits_io._processed` → `info.processed`, `classify` → `kind='processed'`, `status='ignored'`, nie gestackt):
   `NUMFRAME`/`STACKCNT`/`NCOMBINE` > 1, `INTEGRAT` oder Farbwürfel mit 3 Ebenen. Anlass 01.11.2026: APP-Integrationen (`M17.fits`,
   `M17-lpc-cbg*.fits`, 3263×2194×3) lagen direkt im Stellina-Ordner `…_observation_M17` (der `APP`-Ordnerausschluss griff nicht) →
   zwei falsche Objekte M17 (596/597), 597 auch an Sage/SkyTrace gemeldet; bereinigt, Sicherung `data/backups/astroflow.db.vor-m17-bereinigung`.
2. **Analyse** (`quality.py`, parallel): Sterne, FWHM, Exzentrizität, Hintergrund, Rauschen, `snr`, `grad` (Hintergrundgradient in
   Rauscheinheiten), `trails` → `frames.metrics`. `snr`/`grad` fließen in den Score, wenn alle Frames sie haben (ältere Analysen nicht).
   `detect_trails` erkennt die Demo-Satellitenspur noch NICHT zuverlässig (offener Punkt) – entfernt werden Spuren ohnehin per Clipping. Nur neue Frames – oder solche
   mit `metrics.mv < quality.METRICS_VERSION` (dann auch automatisch Neu-Stacken). **Bei jeder Änderung an Analyse/Kosmetik `METRICS_VERSION` erhöhen.**
   Sternerkennung verwirft Einzelpixel-Spitzen (Nachbarn < 12 % des Peaks).
3. **Auswahl**: Ausreißer je **Filter-Band** verwerfen (Wolken, Unschärfe, Nachführfehler); manuelle Ein-/Ausschlüsse (`frames.manual` ±1) gewinnen.
   Verzogene Sterne zusätzlich absolut (`quality.ECC_MAX` 0,8 ≈ 1,7:1), aber höchstens `ECC_MAX_REJECT` = ⅓ der Frames – rein relativ
   ließ M57 (Wind/Nachführung, 89 von 133 Frames ≥ 1,5:1) alles durch; jetzt 91 Frames, Stack 1,50 → 1,39:1 (Stellina 1,36).
4. **Kalibrierung** (`calibration.py`): Master-Dark/Flat/Bias passend nach Größe/Gain/Belichtung/Filter/Nacht, gecacht in `work/calibration`.
   Danach immer `cosmetic()`: Hot-/Cold-Pixel je CFA-Farbebene durch Nachbar-Median ersetzen (Sterne geschützt: hellster Nachbar muss dunkel sein).
   Grund: Ohne Darks richtete die Registrierung Stellina-Aufnahmen an den ortsfesten Hotpixeln aus → Sterne mehrfach im Stack (behoben, Test `test_hotpixels.py`).
   `cosmetic` rechnet den Nachbar-Median nur an Kandidaten (exakter Vorfilter, „oberer Median“ wie ndimage) – bitgleich zur alten Version, 5× schneller.
   **Ortsfeste Sensorfehler** (seit 0.31.2, `calibration.defect_candidates/defect_map/fix_defects`, `DefectLibrary` → `work/defects/<key>.npz`,
   Schlüssel wie `GlowLibrary`): `cosmetic` schützt alles mit hellem Nachbarn als Stern – auch Hotpixel-**Paare**. Beleg NGC 7293 (Stellina, 293 Frames):
   zwei übereinanderliegende R-Pixel (~42 000 ADU) blieben stehen; die Stellina verharrt zwischen ihren Versätzen 2–6 Aufnahmen auf derselben
   Stelle, so traf das Paar mehrmals dieselbe Himmelsstelle und überstand das Clipping (≤ 9 Frames je Stapel, s. Nebenbefund) → Gruppe aus ~12
   roten Punkten (eine je Zeigegruppe), nur im R-Kanal des linearen Stacks. Feldrotation spielt dabei keine Rolle (Stellina hat einen Derotator).
   Jetzt in Schritt 0 von `stack()` (`Pipeline._sensor_maps`, gleiche Stichprobe ≤ 30 Lights wie das Glühen, jede Datei einmal gelesen):
   Kandidat je Aufnahme = ≥ 5σ über/unter dem Nachbar-Median der Farbebene mit höchstens 2 hellen Nachbarn, dazu die von `cosmetic` ersetzten
   Pixel ohne 3 helle Nachbarn. Stern-Schutz: drei Nachbarn deutlich über dem Hintergrund im Ring bei Abstand 2 (fängt knapp abgetastete
   Sterne auch bei Mono und stillstehendem Feld – der Nachbar-Median liegt dort schon im Sternprofil; Review PR #51), bei CFA zusätzlich
   die 4 direkten Nachbarn anderer Farbe erhöht. Fehler = Kandidat in ≥ 50 %
   der Stichprobe (Sterne wandern), > 1 % der Pixel → keine Karte. `_load` ersetzt die Pixel nach `cosmetic` durch den Median der Nachbarn
   derselben Farbe (ohne andere Fehler). Nacht ohne eigene Messung (< 12 Rohbilder): Pixel aus ≥ der Hälfte der Nächte der Kamera.
   Info je Nacht in `projects.options.defect_info`. Gemessen NGC 7293: ~860–950 Pixel (0,015 %; mit den `cosmetic`-Ersetzungen 6 047).
   **Kein automatisches Neu-Stacken** (Nutzerwunsch, `METRICS_VERSION` bleibt 2): bestehende Bilder verlieren die Punkte erst mit
   „Komplett neu stacken“ (nur mit erreichbaren Rohbildern); Nachstacken auf einen alten Stack ist sicher (Test), alte Punkte bleiben
   bis dahin. Ohne Karte analysierte Frames zählen Hotpixel-Grüppchen als Sterne (Helix: 5–12 von 150, SNR ~12 % zu hoch) – SNR geht
   nur mit Exponent 0,3 in den Score, Verwerfen/Gewichte nicht → gemischte Analysen hinnehmbar. Neue Stacks tragen `defects` in
   `state.json`; `Pipeline.hotpixel_hint` (Stellina, Stack ohne Marke, keine gestackten Rohbilder gelöscht, Stichprobe der Pfade
   erreichbar, gecacht je state.json-Stand) → `/projects/{id}` `hotpixel_hint` (seit 0.37.1 für „Neu entwickeln“, s. u.). Test `test_hotpixel_paare.py`
   (`demo.generate_hotpixel_pairs`, auch stillstehendes Feld; Mono-Stillstand mit σ = 1-px-Sternen). Nebenbefund: `cosmetic` kappt die Spitze heller, scharfer Sterne, deren Kern in der
   R/B-Ebene liegt (Demo: 5458 → 3240 ADU, ~2 % Fluss) – unverändert, nur nicht in die Karte übernommen.
   **Bestehende Stacks ohne Neu-Stacken** (seit 0.37.1, `stackfix.py`, Nutzerbefund M16: 163 von 451 Rohbildern gelöscht → Neu stacken
   gesperrt; `work/defects` war in der Arbeitsinstanz noch leer, eine projizierte Fehlerkarte hätte also meist keine Quelle): `Pipeline.develop`
   ruft bei Farbstacks ohne `defects`-Marke `stackfix.repair(mean, cov)` auf dem linearen Stack auf (vor GraXpert/PCC; Stack selbst bleibt).
   Fund = Spitze > `K` 10σ über dem 7×7-Median in einem Kanal, Median der 8 Ringpunkte bei 3 px < 15 % der Spitze (ein länglicher Fleck
   trifft 2–3, ein Stern alle; Mittelwert versagte bei Plateaus), andere Kanäle im 3×3-Kern < max(4σ, 20 % der Spitze) (rote Sterne haben in
   Grün ≥ ~30 %; Anteil in Bildintensität, nicht in σ je Kanal – Review PR #61; 5×5 traf den Halo von Nachbarsternen), keine helle Umgebung (Median auf ~20 px < 300σ – sonst M45: Halo-Struktur im
   B-Kanal), Gruppe ≥ 3 in 80 px oder ≥ 40σ. Fleck = Pixel ≤ 3 px (Saum 2 px) um die Spitze über Ring-Hintergrund (6–10 px) bzw. 7×7-Median
   (je der größere Überschuss), in denen die anderen Kanäle ruhig sind (trennt den Sternhang), ≤ 150 px; Füllung = Ebene aus einem Ring um
   den Fleck, auf dessen Wertebereich begrenzt (eine quadratische Fläche schoss über und erzeugte neue Punkte). Ergebnis `stats.hotfix`
   (`v` = `stackfix.VERSION`, `n`, `channels`, `areas`, `key`). `key` geht als `tag` in den GraXpert-Cacheschlüssel ein (der hasht nur jeden
   7. Pixel und sähe die Reparatur sonst nicht; ohne Reparatur bleibt der alte Schlüssel). Über alle 90 Stacks der Arbeitsinstanz (nur gelesen): 14 ohne Funde (große Nebel),
   sonst R/G (Paare auf dem Sensor), Blau nur vereinzelt. Hinweis: `hotpixel_hint` = Stellina-Stack ohne Marke und neueste Version ohne
   `hotfix` → Menü „Mehr“: „Neu entwickeln – entfernt Hotpixel-Punkte“. Kein automatisches Neu-Entwickeln.
5. **Stacking** (`registration.py` Dreiecks-Matching + `stacking.py` gewichtetes Sigma-Clipping): **inkrementell** – Zustand
   (`sw/swx/swx2.npy` + `state.json`) in `work/p<id>/stack` (Farbe) bzw. `stack_<Band>` (Mono-Filter). Neue Frames werden nur nachgestackt;
   `projects.restack=1` erzwingt Neuaufbau (wird nach dem Zurücksetzen sofort auf 0 gesetzt).
   `frames.stack_notified` bestätigt die Meldung an Sage/SkyTrace erst nach erfolgreicher Entwicklung und Callback;
   nach Checkpoint/Abbruch wird sie beim nächsten vollständigen Prozesslauf vor dem Löschen der Rohbilder nachgeholt.
   **Checkpoints:** `_stack(save_dir=…)` speichert den Zustand spätestens alle `CHECKPOINT_S` (120 s) und am Ende; Frames werden erst
   danach `stacked=1`. `StackState.save` ist atomar (`.tmp` → Tausch, `.old` als Rückfall beim Laden). Alle Bänder registrieren auf **eine** gemeinsame Referenz; Flussnormierung je Band relativ (`flux_k0`).
   **Performance** (gemessen, 80 Stellina-Frames 3072×2080, 16 Kerne: gesamt 267 s → 44 s, Stacking 245 s → 29 s):
   Warp-Threads = `settings.threads` (ndimage gibt den GIL frei), nächster Stapel wird vorbereitet während `add_batch` läuft (doppelte Pufferung),
   `add_batch` arbeitet in Zeilenstreifen parallel, Stapelgröße aus RAM-Budget (`ASTROFLOW_BATCH_MB`, Standard RAM/8, 700–4000 MB; Docker-Limit wird beachtet).
   Clipping: Streuung nur einmal aus allen gültigen Werten schätzen, κ = 3/3 – die alte Variante (Streuung neu aus beschnittenen Werten, κhi 2,5)
   hob bei großen Stapeln das Rauschen um 4,5 %. Messskript-Idee: gleiche Frames vorher/nachher stacken, Hintergrundrauschen + Sternfluss vergleichen.
   **Profil (01.11.2026, 219 M101-Frames, 9800X3D, 12 Threads; Rechenzeit über alle Threads):** `_add_rows` 539 s (davon `clip_batch` 399 s),
   `warp` 424 s, `cosmetic` 148 s, Debayer 76 s, FITS lesen 44 s, Glühen 29 s, Dreiecke 10 s – Stacking-Phase CPU-gebunden (alle Kerne voll).
   Bitgleich beschleunigt: Clip-Median per `np.sort` (`stacking._median0`, 2–3× schneller als `np.median` über Achse 0) und
   Hotpixel-Korrektur je Datei nur einmal je Lauf (`Pipeline._load` merkt die ersetzten Pixel im `cache`) → `stack()` −12–14 % (A/B abwechselnd).
   Offen: Thread-Überbelegung (12 Warp-Worker + 12 `add_batch`-Streifen auf 16 logischen Kernen) und Streifenhöhe (Cache) – nur auf
   ruhigem Rechner messbar (seit GPU-Stacking weniger wichtig).
   **GPU** (`gpu.py`, seit 01.11.2026, optional `pyopencl` – nicht in `requirements.txt`): OpenCL-Kernel für `registration.warp` (kubischer
   B-Spline wie ndimage order=3: Vorfilter rekursiv je Zeile/Spalte mit Spiegelrand, außerhalb 0; Maske weiter auf der CPU) und
   `StackState.add_batch` (`clip_stats` = Median/Streuung der gültigen Werte je Pixel, `clip_add` = 2 Clip-Runden + Abgleich mit dem Stack +
   Aufsummieren; Rauschboden je Stapel statt je CPU-Streifen; Stapel ≤ 32 Frames, Upload in Streifen ≤ 1 GB, Zustand erst nach allen Streifen
   übernommen). Eine `Engine`, Aufrufe per Lock serialisiert; Fehler → `Engine.fail` schaltet die GPU ab, CPU rechnet weiter.
   Karte: größter Speicher × Recheneinheiten (iGPU fällt raus). Einstellung `settings.json` → `gpu.mode` auto|off (UI: Einstellungen → Bilder →
   System), `ASTROFLOW_GPU=0` (setzt `tests/conftest.py`; Vergleich GPU↔CPU in `test_gpu.py`, ohne GPU übersprungen).
   Gemessen M101 (219 Frames, RX 9070 XT, Rechner ruhig): Stacking-Phase 66,9 → 21,5 s, `stack()` 96 → 49 s; Stack-Abweichung mittel
   0,0008 ADU bei 5,6 ADU Rauschen, 0,004 % der Pixel mit anderer Clip-Entscheidung. Danach begrenzen Lesen, `cosmetic`, Debayer (CPU).
   **Nebenbefund:** Mit RMS um den Median kann ein einzelner Ausreißer in einem Stapel von B Frames höchstens √B·σ abweichen –
   bei B ≤ 9 wird er im Stapel also nie verworfen, nur über den Abgleich mit dem bestehenden Stack (`check`, ab ~8 Frames Gewicht).
6. **Kanäle** (`channels.py`): L/R/G/B/Ha/OIII/SII → RGB, LRGB, RGB+Hα, HOO, SHO oder Mono (Option `palette`).
7. **Develop Engine** (`develop.py`): `measure()` (Objekt-SNR, Signalfläche, Dynamik auf gebinnter Vorschau) → `adapt()` passt
   Profilwerte an (Entrauschen, `linear_denoise` = (10/SNR)^1,5, Sättigung, asinh, keine Entfaltung bei wenig Signal) → bis zu 3 Renderings
   in voller Auflösung mit `assess()` (Hintergrund, Farbstich, Farbrauschen [Bandpass 2–12 px], Restgradient, Clipping, Sättigung, Signal)
   und `corrections()`; Restgradient wird nur gemeldet (höherer Polynomgrad trägt Nebel ab). Vom Nutzer gesetzte `params` sind tabu
   (`fixed`). Ergebnis in `stats.quality` (Score, Probleme, `hints`) und `stats.qc` (Verlauf). Schwellen kalibriert an NGC 1579, M3, M74,
   IC 405, M57 (gute Bilder: Farbrauschen 0,0035–0,006; fleckig ab ~0,009).
   **Ausgebrannter Kern** (`burnt_core`, Code `burnt_core`): größte Fläche L > 0,95 ≥ 1e-4 des Bildes und ≥ 4× typischer Sternkern
   (NGC 4490 114 px vs. ~20, M31/M3 ~390; helle Sterne M45/IC 405 bleiben darunter). Ursache ist der Weißpunkt der Kurve
   (Perzentil 99,9 → alles darüber hart weiß; mehr asinh hilft dagegen nicht) → Korrektur hebt `white_pct` stufenweise
   (`WHITE_STEPS` 99,9/99,98/99,995) und asinh ×1,5. Objekttyp: Ordner-/OBJECT-Namen, die der kleine Katalog nicht kennt, bekommen
   ihren Typ aus ASTAPs `deep_sky.csv` (`objects.resolve_extended`; NGC 4490 lief vorher mit Profil „generic“, asinh 30 statt 120);
   ältere Projekte ohne Typ werden beim Entwickeln nachgetragen.
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
   **Seit 01.11.2026 für Nebel (emission/reflection/snr) `starnet`** = nur echtes StarNet, ohne StarNet keine Trennung (`Pipeline.develop`
   setzt dann `off`); NGC 7000 mit StarNet + GraXpert deutlich besser (Nebel ohne Sterne gestreckt, Sterne kleiner). Dazu **Endbearbeitung**
   `processing.grade` (Profil `grade` 1,0 / `vibrance` 0,5, vor dem Einsetzen der Sterne): Wavelet-Ebenen 4–32 px ×(1+0,35…0,6) nur im Objekt
   (Maske gegen 10-%-Perzentil der geglätteten Luminanz – Median/MAD versagte, wenn der Nebel das Bild füllt: 0,4 % Maske) und ohne helle
   Sterne; Vibrance sättigt blasse Farben stärker. Galaxien/Haufen ohne beides. Früherer Stand: **Standard seit 2026-10-28: aus** (alle Profile `off`) – der Nutzer verglich NGC 7000 und fand die Trennung (eigenes Verfahren)
   matschiger und kontrastärmer; gewählt: ohne Trennung + Gaia-Farbe. Je Objekt per Option `star_separation` (auto/intern) zuschaltbar.
   Bei Sternhaufen brannte die Trennung den Kern aus (M3). Ohne Trennung: `channel_curve`.
   `darken_background` mit weichem Fuß (hartes Abschneiden ließ dunkle Nebelpartien schwarz werden).
   **Fotometrische Farbkalibrierung** (`photometry.py`, `Pipeline._photometric`, Einstellung `gaia` / `ASTROFLOW_GAIA`): Master-Stack mit
   `platesolve.solve_image` lösen (WCS auf vollem Pixelraster, Cache `work/p<id>/wcs.json` je Referenzbild), Gaia-DR3-Farben BP−RP
   per TAP/ADQL, Reihenfolge GAVO-Spiegel (`gaia.dr3lite`, BP−RP aus den Mittelwerten, Timeout 20 s) → ARI Heidelberg (30 s) → ESA-Archiv → VizieR
   (gemessen 01.11.2026: 0,6 s / ~8 s / 131 s / nicht erreichbar), Cache `work/gaia/`.
   Zuordnung ≤ max(3 px, FWHM), Aperturphotometrie, robuste Gerade log(R/G), log(B/G) gegen BP−RP, Weiß = G2V (0,82).
   Ergebnis als `params.wb_gains` + `_pcc`; ohne Solver/Netz/Sterne → Sternmittel mit Grund in `stats.color_cal`.
   Stellina-Werte (5 Objekte): R ≈ 0,97, B ≈ 0,88, Streuung ~3 %. Tests offline (`tests/conftest.py` setzt ASTROFLOW_GAIA=0).
   **StarNet** optional: Einstellungen → „ZIP installieren“ (`stars.install_zip` → `data/tools/starnet`, Pfadprüfung, Selbsttest)
   oder Pfad (`settings.json` → `starnet.path`). Kein automatischer Download: Lizenz „all rights reserved“, Download-Links nur per JS.
   **GraXpert** (`graxpert.py`, seit 01.11.2026, Nutzerwunsch nach Astrobiscuit-Vergleich – dessen Workflow: APP → GraXpert → StarNet2 → Affinity):
   optionales externes Programm (GPL-3, Windows nur als MSI → Suche in `Program Files\GraXpert`, `%LOCALAPPDATA%\Programs\GraXpert`, PATH,
   `ASTROFLOW_GRAXPERT`; `=0` schaltet ab, setzt `tests/conftest.py`). `Pipeline.develop` ruft `graxpert.apply` **einmal** auf dem linearen Stack auf
   (vor Gaia-PCC, nicht je QC-Runde; Cache `work/p<id>/graxpert/`): KI-Hintergrund (`background-extraction`, Subtraction) und/oder KI-Entrauschen
   (`denoising`, Stärke Profilwert `ai_denoise`, Standard 0,5, planetary 0,3). Ungedeckte Ränder werden vorher mit Himmelswert gefüllt. Ergebnis als
   `params._ai` → `processing.process` überspringt dann Polynom/Randglühen bzw. `denoise_linear`; `stats.graxpert`. Modus global (Einstellungen →
   Bilder, `settings.json` → `graxpert.mode/gpu/path`) und je Objekt (Option `graxpert`). Fehler → eigene Verfahren. Test: `test_graxpert.py` (Attrappe).
   **M64 (01.11.2026, Nutzer: „um Welten besser“):** Restgradient 0,020 → 0,0017, Farbrauschen 0,0059 → 0,0037, Himmel 0,055 → 0,031,
   diagonale Schlieren (Walking Noise, vom Polynom nicht erfassbar) fast weg. Halo schwächer (Signal 0,015 → 0,007; KI-Entrauschen senkt σ →
   härterer Stretch), vom Nutzer aber als ausreichend erkennbar bewertet – so belassen.
   **Sensorglühen** (Schritt 0 in `stack()`, `Pipeline._glow`, `calibration.GlowLibrary`): ortsfestes Randglühen (Stellina oben/unten
   80–170 ADU) verschmiert sonst durch die Feldrotation zu Schlieren. Je Kamera (Gerät+Format+Gain+Belichtung+Filter) und Nacht:
   Zeilen-/Spaltenprofile (30-%-Perzentil je CFA-Ebene) aus ≤ 30 Lights, Median → `work/glow/<key>.npz` (wächst mit jeder Nacht,
   bleibt nach dem Löschen der Rohbilder). Abzug in `_load` nur in den Randbändern (`GLOW_BAND` 25 %, Mitte unberührt), nur ohne
   echte Darks. Nacht mit < 12 Frames → Median aller Nächte der Kamera. Info je Nacht in `projects.options.glow_info`;
   abschaltbar per Projektoption `sensor_glow`. Gemessen (135 IC-405-Frames): Randglühen im Stack 5,9σ/3,6σ → 0,1σ/0,6σ.
   **2D-Restkarte** (seit 2026-10-30, NGC 4490: Profile ließen Ecke +18 ADU und eine Seite −7 ADU übrig ≈ Stack-Rauschen →
   Flecken + violettes Eckglühen): je Ebene zusätzlich 30-%-Perzentil in Kacheln `GLOW_BIN` 32 px des Rests nach den Profilen
   (Achse 2 im `.npz`). `model_from` → `_rest_map`: Gesamtmuster minus *ausgeblendet* abgezogene Profile (sonst bleibt, was die
   Profile außerhalb ihrer Bänder nicht abziehen), kompakte helle Kacheln (Galaxie) durch Median ersetzt – nicht in den 2 äußersten
   Kachelreihen (dort sitzt das Eckglühen), auf die Mitte bezogen. Wirkt voll bis 18 %, ausgeblendet bis 35 % vom Rand
   (`GLOW_MAP_FULL/BAND`), Mitte unberührt. Ergebnis Rohbild NGC 4490: Rest überall ≤ ±4 ADU. Ältere Bibliotheksnächte (nur Profile)
   werden bei vorhandenen Rohbildern neu gemessen (`nights(with_map=True)`), sonst weiter ohne Karte genutzt.
   **Stacking-Stufen** (seit 01.11.2026, Nutzerwunsch „Slider, was mehr Bilder ausmachen“): `_stack` sichert beim Überschreiten von
   `STAGES` (1, 3, 10, 30, 100 … Frames, kumulativ über alle Läufe) den Zwischenstand – `StackState.add_batch(prefixes=…)` rechnet
   „bisher + erste k Frames des Stapels“ (Clipping-Maske aus dem ganzen Stapel). Seit 02.11.2026 direkt kleine JPGs (≤ 800 px)
   in `work/p<id>/stufen[_<Band>]/n<t>.jpg` + `.json` (n, Belichtung), keine neuen Vollbild-NPZs. Neuaufbau löscht sie mit.
   `_render_stages` kopiert sie nur noch zu `<base>_s<n>.jpg` (`results.files.s<n>`, `stats.stages`, `stats.stages_live`);
   alte float16-NPZs (ADU / `STAGE_SCALE`) werden nur verkleinert gestreckt. Keine zusätzliche vollständige Entwicklung.
   Live-Ansicht: `live.py` je Projekt/Auftrag, atomare `work/p<id>/live/state.json` und zwei aktuelle JPGs;
   `_track_live` protokolliert echte Schritte, Vorschau-Hooks in GraXpert/Processing, höchstens alle 4 s plus Endergebnis.
   `/projects/{pid}` liefert `live` nur für den neuesten Auftrag; `/projects/{pid}/live/{name}` liefert streng geprüfte JPG-Namen.
   `static/live.js|css`: Bildwechsel auf zwei Ebenen, kompakte Statuszeile mit aufklappbarem Ablauf; reduzierte Bewegung beachtet.
   UI: Regler unter dem Viewer, Standard = fertiges Bild, `viewer.setSrc` tauscht das Bild ohne Zoomverlust.
   Schnelles Iterieren: `StackState.load(work/p<id>/stack).result()` → `processing.process(...)` (~10 s), kein Neu-Stacken nötig.
   **Schwaches Signal / M57-Vergleich mit Stellina (2026-10-30):** Lineare Stacks waren gleich gut, die Entwicklung schlechter.
   Ursache: `linear_denoise` senkt das gemessene σ → Schwarzpunkt der Kurve (Median − 2,8σ) rückt an den Himmel → viel härterer Stretch.
   Das macht schwache Galaxien/Nebel erst sichtbar (Kurve aus dem Bild *vor* dem Entrauschen getestet: M63/NGC 1579/M101 fast unsichtbar –
   verworfen), bläht bei kompakten hellen Objekten aber Sterne auf und brennt sie aus → Profilwert `linear_denoise_max` (planetary 0).
   Profil planetary: target_bg 0,10, denoise 0,25, white_pct 99,98 (Korn und schwache Sterne bleiben wie bei Stellina).
   Entfaltung erst ab Objekt-SNR `DECONV_MIN_SNR` 15, Schärfen ab SNR 10 anlaufend bis 25 (sonst dunkle Ringe um Sterne).
   Kern weiter ausgebrannt, obwohl `WHITE_STEPS` ausgereizt → `corrections` erhöht asinh weiter.
   Danach alle Profile: `denoise` 0,3 / `chroma_denoise` 1,5 (vorher 0,4–0,6 / 1,5–2,5) – an 10 Projekten gemessen: Objekthelligkeit
   gleich (Nutzer: „ohne dass sie dunkler werden“), 10–35 % mehr schwache Sterne, Sterngröße gleich. `linear_denoise_max` 0,5 für alle
   getestet und verworfen (M63/M101/NGC 1579 dunkler). `CORE_STAR_RATIO` 4 → 5: AE Aurigae in IC 405 (4,4) galt als ausgebrannter Kern.
   **Stack-Schärfe nachgemessen (01.11.2026):** kein Stack-Problem. Gleiches Verfahren wie bei Frames (2×2 gebinnt, ×2): Stack schärfer als
   der Median-Frame (NGC 1579 6,8 gegen 8,2; M101 5,9 gegen 10,1), über das Feld gleichmäßig (Mitte = Rand), Exzentrizität = die der Frames
   (Sternform kommt aus den Rohbildern, nicht aus der Registrierung). Frame- und Stack-FWHM (`stats.fwhm_px`, ungebinnt) nie direkt vergleichen.
   M101 Versuch mit fester FWHM-Grenze (≤ k × 10-%-Quantil): k 1,5 → 232 statt 504 Frames, FWHM −6 %, Rauschen +28 %; k 1,25 → −8 % / +54 %
   → Auswahl bleibt relativ (Gewichtung reicht; wer Schärfe will: Option „Nur die besten X %“).
   **Farbverlauf im Himmel** (seit 01.11.2026, NGC 891 bekam trotz rötlichem rechtem Rand 10/10 – `cast` misst nur den
   globalen Median): `develop.color_gradient` = Ebene durch R−G/B−G der Himmelskacheln (8×8, getrimmtes Mittel), Stärke =
   Änderung über die Bildbreite, dazu Seite/Farbton. Gemessen an allen Ergebnissen: sauber ≤ 0,010, NGC 891 0,019, NGC 7318 0,022,
   M85 0,018; in der Milchstraße echter Hα-Nebel bis 0,030 → `COLOR_GRAD_MAX` 0,013 bzw. `_NEBULA` 0,035, Auswahl über
   `tutor.neutral_sky` (|b| ≥ 15° bzw. Galaxie/Kugelhaufen ab 8°). Korrektur `processing.remove_color_gradient` (Polynom über
   Kanal − Luminanz, Helligkeit bleibt) per Parameter `color_gradient` = Grad; QC setzt erst 2, dann 3 (Ebene überkorrigierte NGC 891).
   `assess` liefert `v` = `QC_VERSION` – **bei Änderungen an `assess` erhöhen**, der Tutor prüft ältere Ergebnisse dann am gespeicherten PNG nach.
8. **Bild-Tutor** (`tutor.py`, seit 01.11.2026, Nutzerwunsch „Tutor für Anfänger, Hinweise passend zum Instrument, Mond/Wetter der Nacht“):
   `build_report` (API `GET /results/{id}/tutor`, Cache in `results.stats.tutor` mit `TUTOR_VERSION`) = Gerät (`detect_device`:
   INSTRUME/TELESCOP, Rückfall Bildformat; `DEVICES` mit vorsichtigen Tipps – nur Gesichertes; Stellina hat eine Tauheizung →
   kein Tau-Befund aus Wetterdaten, Hinweis des Nutzers) + `night_conditions` (astropy offline, IERS-Download aus; ≤ 48 Stützzeiten
   je Nacht: Sonne/Mond/Objekthöhe, Mondphase aus Elongation; Frame-Verlauf aus `frames.metrics`: Sternzahl-Einbrüche, `bg`/`fwhm`-Trend
   letztes/erstes Fünftel, Exzentrizität > 0,8) + optional `fetch_weather` (Open-Meteo-Archiv/ERA5, Ort auf 0,1° gerundet, Cache
   `work/tutor/`) → `findings` (code, level bad|warn|info|ok, Erklärung, Tipps). Standort: `FrameInfo.site` (SITELAT/LONG, OBSGEO; Stellina
   schreibt keinen) oder `settings.json` → `tutor.lat/lon`. NGC 891 (24./25.06.2022, Mitteleuropa): Sonne nur −10…−14°, Himmel ×3,2 heller →
   Dämmerung als Ursache des Rotstichs. KI auf Knopfdruck (`POST …/tutor/ki`, `ask_llm` über `reviewer.chat`, eigener Anbieter/Modell
   `tutor.llm_provider/llm_model`, URL/Schlüssel vom KI-Prüfer), Ergebnis in `results.stats.tutor_ki`. Einstellung `tutor.mode`
   einsteiger|experte (UI: Befunde offen bzw. zugeklappt). Test: `test_tutor.py`.
9. **KI-Prüfer** (`reviewer.py`, optional, Standard aus): Vision-LLM (LM Studio/OpenRouter) bewertet Vorschau, darf nur Whitelist-Parameter in festen Grenzen ändern.
   Läuft nach der messbaren Kontrolle (`first=` = bereits gerendertes Bild). Denkmodelle (Bonsai, Qwen3) brauchen ~2 500 Tokens vor der
   Antwort → `max_tokens` 8000, Timeout 300 s; `<think>`-Blöcke und `reasoning_content` werden behandelt. Lokal z. B. LM Studio mit einem Vision-Denkmodell (~1 min/Runde).
10. **Ausgabe**: TIFF16/PNG/JPG/Thumb + linearer FITS in `output/<Objekt>/`, Zeile in `results` (Versionen). Bild wird beim Speichern vertikal gespiegelt (Norden oben).
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

## Installation und Einrichtung (`core/launcher.py`, `core/setup.py`, `installer/`; seit 0.33.0 – Voraussetzung fürs öffentliche Repo)
- **Zwei Wege, getrennt:** Bestehende Instanzen (`serve --data`, `ASTROFLOW_DATA`, `Start.bat` mit `./data`, Port 8080/8766) bleiben unverändert.
  Neu ist der **Starter** `astroflow` / `astroflow start` (Standard ohne Befehl): Port 8766, `127.0.0.1` (Heimnetz erst nach Einschalten im
  Assistenten → `config.lan`, sonst fragte die Windows-Firewall beim ersten Start), Datenordner `--data` > `ASTROFLOW_DATA` > `config.json` →
  `data_dir` > `%USERPROFILE%\AstroFlow` bzw. `~/AstroFlow` (**nicht** „Dokumente“ – OneDrive, Entscheidung des Masters 06.11.2026).
  `config.json` liegt außerhalb des Datenordners (`%APPDATA%\AstroFlow`, `~/.config/astroflow`; portabel `ASTROFLOW_HOME` → `<App>\config.json`,
  Daten `<App>\Daten`). Läuft schon ein AstroFlow auf dem Port (`/api/status`) → nur Browser; fremdes Programm → Meldung (MessageBox ohne Konsole).
  Server als losgelöster `pythonw -I -X utf8 -m astroflow serve` mit `ASTROFLOW_LAUNCHER=1` (nur dann ist der Datenordner wählbar und der
  Assistent erscheint von selbst), Protokoll `server.log[.err]` im Datenordner. `astroflow stop` = `POST /api/system/shutdown` (SIGINT → Module
  stoppen sauber). Neustart (Datenordner/Heimnetz geändert, `POST /api/system/restart`): neuer Starter mit `--wait-pid`, alter Server beendet sich.
  `ASTROFLOW_NO_BROWSER=1` unterdrückt den Browser.
- **Assistent** `#/willkommen` (`static/setup.js|css`, Präfix `su-`): erscheint, wenn `/api/app` → `setup.pending` (Starter + `settings.json` →
  `setup.done` nicht gesetzt) und die Startseite aufgerufen wird; sonst über Einstellungen → Module & System (`setupSystemPanel`: Updates,
  Neu starten, Beenden). Schritte: Datenordner (Laufwerke + frei, `launcher.cloud_folder` warnt bei OneDrive/Dropbox/…; Wechsel legt im neuen
  Ordner `setup` an und startet neu – **alte Daten werden nicht verschoben**, DB-Pfade sind absolut), Teleskop (→ D05 bzw. D50 für „andere“),
  Standort (Link), ASTAP, Demo, Extras (GPU per pip, GraXpert/StarNet nur Links, Heimnetz + Firewall-Regel wie `Netzwerk-freigeben.bat` per UAC,
  Update-Prüfung). Hintergrundaufgaben `Setup._run` → `tasks` + Aktivität (`setup:<key>`).
- **ASTAP auf Knopfdruck:** nur von SourceForge „astap-program“ (CLI je OS, `astapwin32.zip` nur für `deep_sky.csv` – laut Danksagung
  **nur nicht-kommerziell frei**, Lizenz der Sterndatenbanken nicht eindeutig → nie mitliefern), nach `%LOCALAPPDATA%\astap` bzw. `~/astap`
  (sucht `platesolve`/`objects` ohnehin dort), vorhandene Dateien bleiben. D05 als ZIP (~102 MB), D50 ~900 MB.
- **Update-Hinweis:** `Setup.update_loop` (20 s nach Start, dann stündlich prüfen, ob 24 h um) → GitHub `releases/latest`, anonym,
  `system.update_check` (Standard an), jeder Fehler still (privates Repo = 404). `/api/app.update` nur wenn neuer; Frontend-Toast einmal je Version
  (`localStorage af.update.seen`). `ASTROFLOW_UPDATE_CHECK=0` setzt `conftest.py`.
- **Paket:** `pyproject.toml` (setuptools ≥ 77, alle Dateien unter `astroflow/` als Paketdaten, Abhängigkeiten = `requirements.txt`, Test prüft
  Gleichheit; noch nicht auf PyPI). `installer/build_windows.py`: python-build-standalone (fest `PBS_TAG`/`PBS_PY`) + Wheel + pip-Install,
  numpy/scipy-Tests entfernt, Symbol gezeichnet (Pillow), `AstroFlow.cmd`/`AstroFlow beenden.cmd` (portabel), ZIP; mit ISCC Inno-Installer
  (`astroflow.iss`: pro Nutzer, `%LOCALAPPDATA%\Programs\AstroFlow`, Startmenü, optional Desktop/Autostart, stoppt laufenden Server vor Update/
  Deinstallation, Daten bleiben). Daneben `AstroFlow-Test.wsb` + `pruefe-in-sandbox.cmd` (Windows-Sandbox, absoluter Pfad → nicht im Repo;
  für heruntergeladene Pakete `build_windows.py --sandbox-only --out <Ordner>`): Paketordner schreibgeschützt, daneben `<Ordner>-sandbox-ergebnis`
  beschreibbar, Netz an, sonst nichts eingebunden; Logonbefehl prüft Installer (`pruefe_paket.py --setup`, nur in Sandbox/VM – installiert ins
  echte Profil) und ZIP, Protokolle + `fertig.txt` im Ergebnisordner. Start per Doppelklick oder `WindowsSandbox.exe <wsb>` (geht auch aus der Sitzung).
  CI: `.github/workflows/windows-paket.yml` (PR mit Paketänderungen, manuell, Tag `v*` → Release-Entwurf) baut, prüft sauber und installiert still.
- **Gemessen 06.11.2026** (`installer/pruefe_paket.py`, leeres Profil, PATH ohne Python, 16 Kerne): Paket 150 MB ZIP / 379 MB entpackt;
  entpacken 8 s, erster Start 21 s, ASTAP + D05 12 s (schnelle Leitung), Demo bis Bild 11 s, Beenden 4 s → **0,9 min bis zum ersten Bild**.
  **Windows-Sandbox** (frisches Windows ohne Python, CI-Pakete): Installer 96 MB – installieren 18 s, Start 9 s, ASTAP + D05 20 s, Demo bis Bild 9 s
  → **0,93 min**, Beenden 3 s, Deinstallieren 3 s (Daten bleiben); portabel 0,58 min. CI-Runner: Installer 0,84 min, portabel 3,6 min (ASTAP 177 s).
  `platform.machine()` ist unter Windows ohne `PROCESSOR_ARCHITECTURE` leer → `setup._system()` rät über die Zeigerbreite.
- Ohne eingerichtete SkyTrace-Engine (kein Interpreter gewählt, keine venv) überspringt `skytrace.auto` still (Zustand `skipped`) statt je Nacht
  „Analyse-Engine unvollständig“ zu melden. Engine per Knopf = Phase 2 (dafür plattformneutrale Anforderungen statt des Windows-Lockfiles).

## Standorte (`core/sites.py`, seit 0.23.0, Nutzerwunsch 04.11.2026 „Teleskop oft an verschiedenen Orten, Standorte nach Datum“)
- **Ablage:** `data/settings.json` → Abschnitt `sites` = `{list: [{id, name, lat, lon, alt_m, accuracy_m, note}], default, periods: [{id, site_id,
  from, to}], migrated: {quelle: zeit}}`. `to` leer = offen; `from == to` = einzelne Nacht („heute bin ich in …“, Nachtliste). Nacht-Datum wie im
  Projekt (Zeit − 12 h, UTC). Überlappende Zeiträume: der kürzeste gewinnt. `accuracy_m` = Radius ~1σ (Standard 100 m, FITS 30 m).
- **Auflösung** `site_for(when|night, header, override)`: Override → FITS-Kopf (OBSGEO-X/Y/Z, OBSGEO-B/L, SITELAT/SITELONG auch sexagesimal,
  LAT-OBS; 0/0 = kein GPS-Fix) → Zeitraum → Standard-Ort. Rückgabe mit `source` (override|fits|period|default) und `detail`.
- **Nur Standardbibliothek**, weil die SkyTrace-Engine (eigener Interpreter ohne AstroFlow-Paket) die Datei über den Pfad lädt
  (`engine/observation_settings.sites_module`, Umgebung `ASTROFLOW_SITES` aus `skytrace.Module.engine_env`, im Server `observation_settings.SITES_FILE`).
- **Migration** (einmalig, Marke in `migrated`): `tutor.lat/lon` (`server.create_app`) und SkyTrace `site`/`sites[Beobachtung]` (`skytrace.Module._sites_setup`,
  Datum aus dem Beobachtungsnamen → Einzelnacht); Orte ≤ 200 m werden zusammengefasst, alte Felder bleiben unverändert.
- **API (Kern):** `GET /api/sites[?nights=false]` (Liste, `today`, `today_site`, `nights` aus `night_sources`: Bilder = Frames je Nacht/Objekt,
  SkyTrace = Datum im Beobachtungsnamen), `POST /api/sites` (ganzer Abschnitt, geprüft), `POST /api/sites/night {night, site_id|null}`,
  `GET /api/sites/resolve`, `GET /api/sites/geocode?q=` (Photon/komoot, nur Suchtext), `GET /api/sites/elevation` (Open-Meteo, nur auf Knopfdruck).
- **Nutzer:** `tutor.pick_site` (FITS → `site_for(night=häufigste Nacht)` → alte `tutor.lat/lon`), `GET /stacking/tonight` (Ort für jetzt,
  `site_info`), SkyTrace `resolve_site` (s. `modules/skytrace/AGENTS.md`). UI: `static/sites.js|css` (Einstellungen → Standorte, Abschnitt des Kerns
  in `settingsView`), `tonight.js` „Heute bin ich in …“, die Tutor-Einstellung verweist nur noch auf die Liste.
- **Datenschutz:** Orte sind privat – Tests nur mit erfundenen Orten (`tests/test_sites.py`, UI-Fixture in `conftest.py`), Screenshots maskiert.

## Himmelsqualität (`modules/stacking/skyquality.py`, seit 0.26.0 – Wünsche 14–16 „Himmel und Standort“)
- **Messung ohne Rohbilder** aus gespeicherten Daten: Master-Stack + `wcs.json` (bzw. `Pipeline._stack_wcs`) + Gaia mit G
  (`photometry.gaia_stars(with_g=True)` → (N,4), eigener Cache-Schlüssel – gemeinsame Gaia-Schicht, keine zweite Abfrage bauen).
  `zero_point`: Apertur 2·FWHM auf der Luminanz (R+2G+B)/4 (= Gewichtung von `quality.analysis_image`), isolierte Sterne,
  Gaia G → V (Riello 2021), Farbterm frei, Wert bei BP−RP 0,82 → ≈ SQM/V-Band. Nullpunkt je Aufnahme über `frame_links`
  (dieselbe Dreieckszuordnung wie `_stack`, kr = Sternfluss Referenz/Frame, `flux_k0`): ZP_f = ZP_Stack + 2,5·log(flux_k0/kr).
  Gerätenullpunkt der Nacht = 85-%-Perzentil von ZP_f + `K_EXT`·Luftmasse (0,20 mag); Abstand darunter = Dunst (`CLOUD_LOSS` 0,35).
- **Himmel je Aufnahme – zwei Wege:** (a) mit Darks/Bias kalibriert (`calibrated(metrics.calib)`): Pegel = Himmel, also `metrics.sky`
  (Ebene in der Bildmitte ohne Objekt; neu in `analyze_frame` über `bg_plane`, **ohne** `METRICS_VERSION`-Erhöhung – sonst würde alles neu
  analysiert/gestackt) bzw. älter `metrics.bg` − Objektanteil (`stack_excess`). (b) **ohne Darks aus dem Photonenrauschen**:
  Himmel = (`metrics.noise`² − Ausleserauschen²)/k (`NOISE_MODEL`). **Befund 05.11.2026:** Die Stellina verschiebt ihren Schwarzpunkt
  zwischen Aufnahmen (NGC 1579, gleiche Nacht, gleiches Rauschen 50: Pegel 680 ↔ 866; Darks bei 29 °C 772) – ein fester Bias-Abzug ergab
  Unsinn (Werte bis 22,3 bzw. negativer Himmel). Rauschen² über Pegel ist dagegen je Nacht eine Gerade mit Steigung k = 12,9 (30 Nächte,
  r ≈ 0,99) → 0,019 e⁻/ADU; `fit_gain` misst k je Objekt nach (±30 % um den Gerätewert). Ausleserauschen 42 ± 3 (Luminanz-ADU; ≈ 1,6 e⁻;
  Grenzen: Darks 49,3 inkl. Dunkelstrom, dunkelste Aufnahme 47,1) – dominiert die Unsicherheit bei dunklem Himmel (`err` je Nacht).
  Unbekanntes Gerät ohne Darks → kein Wert. mag/″² = ZP − 2,5·log(Himmel/t/Pixelfläche), Zenitwert per `zenith_offset` (∝ Luftmasse, gedämpft).
  Ortswert einer Nacht (`site_value`) nur aus ≥ `MIN_GOOD` Aufnahmen ohne Flags (Sonne < −18°, Mond unter Horizont, ≥ 30°, kein Dunst);
  sonst Verlauf + `sqm_all` in Klammern. Bortle mit Spanne aus `err` = `SYS_ERR` 0,2 ⊕ Rausch-Unsicherheit (Gerätenullpunkt aus anderen Nächten: +0,1).
- **Lichtrichtung:** `metrics.bgplane` (ADU/px) → `orientation` (WCS ∘ Registrierungsmatrix → „oben“/„rechts“ in Frame-Pixeln je Grad,
  Feldrotation automatisch) → relatives Gefälle je Grad. `light_direction` passt über alle Aufnahmen eines Standorts Sockel (Restglühen,
  sensorfest bei Alt-Az) + natürlichen Anstieg (`_nat_slope`) + b·Richtung zum Horizontpunkt φ an; Unsicherheit per Bootstrap über
  Nächte/Objekte, `reliable` erst ab 3 Gruppen, 60° Azimutabdeckung, ±45°. Nur `tutor.neutral_sky`-Objekte, ohne Mond/Dämmerung.
  Ältere Frames ohne Ebene: `_backfill` misst ≤ 40 je Nacht nach, wenn das Rohbild noch erreichbar ist (gleiches `_load`, Glühmodell aus der Bibliothek).
- **Seeing** = `metrics.fwhm` (Vollauflösungs-Pixel, aus 2×2-Bild ×2, +1 px Messsockel) × nativer Maßstab (WCS-Maßstab × `resolution_scale`; Nullpunkt −5·log sc) → „effektive Bildschärfe“. Ohne Standort kein Ortswert (Flag `nosite`); gelöschte Objekte entfernen ihre `sky_nights`.
- **Ablage:** Tabelle `sky_nights(project_id, night, site_id, site_name, device, updated, version, data)` (`ensure_table`, nicht in `db.SCHEMA`);
  Ort je Nacht über Dienst `site_for` (FITS-Ort → `fits:<lat,lon>`), gespeichert nur ID/Name. `summary` mittelt je Ort (Nacht = Median ihrer Objekte).
- **Ablauf:** nach jeder Endbearbeitung `SkyRunner.queue` (eigener Thread, ohne Nachmessen; `ASTROFLOW_SKY=0` aus, setzt `conftest.py`),
  „Für alle Nächte berechnen“ = `POST /api/stacking/sky/compute {project_id?|missing?}` (Objekte mit `wcs.json` zuerst – ihre Nullpunkte
  ergeben den Gerätenullpunkt `device_zp` für die übrigen), Fortschritt in `activity()`. API `GET /sky` (je Ort, mit Lichtrichtung),
  `GET /projects/{pid}/sky` (Nächte mit Verlauf); `tonight` liefert `sky_quality` des gewählten Orts (`sky` ist dort die Wettervorhersage); Dienst `site_sky` → `/api/sites` → `sky` je Ort.
- **UI:** `static/skyquality.js|css` (Präfix `sq-`): Ansicht `#/himmel` (unter Übersicht), Kasten „Himmel in der Aufnahmenacht“ auf der Objektseite
  (`AFsky.panel`), Kachel in Heute Nacht (`AFsky.tonightTile`), Zeile je Ort in Einstellungen → Standorte (`sites.js` → `skyLine`).
- Tests: `test_skyquality.py` (synthetisch, erfundener Ort), UI-Fixture `_sky_seed`.

## Kometenmodus (`modules/stacking/comet.py`, seit 0.22.0 – Nutzerwunsch, nur noch Kometenaufnahmen fehlten)
- **Erkennung:** `comet.designation/is_comet` (C/P/D/X/A/I + Jahr + Halbmonat, `12P`, Wörter Komet/Comet; `IC 1396`,
  `NGC 2024` nicht). `scanner.assign_project(info, centers, night)`: Kometen → Schlüssel `KOMET:<Name>|<Nacht>|<Kamera>`
  (je Nacht ein Objekt, Komet steht vor anderen Sternen), `kind='comet'`, Name „C/2022 E3“; nie über Koordinaten zugeordnet.
  Projektoption `comet_mode` auto|on|off (**nicht** `mode` – das ist bei Aufträgen „develop“), `comet_name` (Bezeichnung für Horizons).
- **Ablauf** (`Pipeline._comet` am Ende von `stack()`, nur ein Band): Sterne-Stack wie immer (Fortschritt 36–60 %), dann
  `_comet_track`: Horizons (`comet.horizons`, Cache `work/komet/`, geozentrisch abgefragt, Parallaxe lokal per astropy
  aus Standort `FrameInfo.site`, sonst Ort der Nacht aus der gemeinsamen Ortsliste (`services["sites"]`), sonst Tutor-Einstellung – Standort verlässt den Rechner nie; WCS des Sterne-Stacks über
  `_stack_wcs`, gleicher Cache `wcs.json` wie die Farbkalibrierung) und/oder Bildsuche: ≤ 36 Stichproben, Luminanz gebinnt
  auf ≤ 800 px, auf den Sterne-Stack registriert. Grob: Einzelbild − Sterne-Stack → Spitzen → RANSAC-Gerade. Fein (`locate`):
  Bahn so optimieren, dass der Kern im Mittel der entlang der Bahn ausgeschnittenen Differenzbilder (Referenz = Median der
  Stichproben) am schärfsten ist (Bandpass, 7×7-Gitter, 4 Runden); mit Ephemeride nur Versatz + lineare Drift.
  **Achtung:** Positionen je Einzelbild aus „Bild − Sterne-Stack“ überschätzen die Geschwindigkeit (~25 % synthetisch),
  weil die im Sterne-Stack verschmierte Spur abgezogen wird. Bahn = Polynom (`Track`, Grad 1, Ephemeride Grad 2).
- **Kometen-Stack** `work/p<id>/stack_comet[_<Band>]`: `_stack(shifts=…, mask=…, mark=False)` – je Frame Translation
  Kern(t0) − Kern(t) nach der Sternregistrierung; Sterne des Sterne-Stacks (`star_mask_for`, kleine/große Masken getrennt)
  verschoben als ungültig. Maskenteile auf der Bahn, deren Licht nicht im Kern sitzt (< 35 % in 1,5 FWHM), sind die
  verschmierte Koma und bleiben frei (sonst Loch im Kopf). Unter 3 FWHM Gesamtbewegung ist der Komet im Sterne-Stack kompakt →
  Bahnbereich ganz frei; unter 1 FWHM kein Kometen-Stack. Inkrementell: Frames in `state.json`, Bahn in `meta.comet`;
  weicht eine neue Bahn > 0,7 px ab, Neuaufbau. Infos in `work/p<id>/comet.json` → `results.stats.comet`.
- **Entwickeln** (`_comet_compose`): Sterne aus dem Sterne-Stack (`stars.separate`, StarNet falls da) + Kometen-Stack ohne
  Sternreste (Kern geschützt), Lücken aus dem sternlosen Sterne-Stack; Abdeckung geometrisch (sonst zu enger Ausschnitt).
  Profil `comet` (scnr 0, Gradient Grad 2), GraXpert nur Entrauschen – gemessen an C/2022 E3 (04.11.2026): KI-Hintergrund
  trägt die äußere Koma ab (Signal 0,050 → 0,027, Koma hart begrenzt, rotbrauner Hof, Score 10 → 7,9); KI-Entrauschen senkt
  das Farbrauschen 0,0061 → 0,0044 bei gleichem Signal (+20 s). `_save_singles`: `<base>_sterne.jpg`, `_komet.jpg`,
  `_komet_linear.fits` (Dateischlüssel `stars`, `comet`, `comet_fits`; UI-Umschalter aus `results[].views`).
- Tutor `comet_motion`: Bewegung je Aufnahme > max(1,5 px; 0,6 FWHM). Tests: `test_comet.py` (Demo `demo.generate_comet`,
  Horizons-Attrappe, `ASTROFLOW_HORIZONS=0` in `conftest.py`).

## Heute Nacht – Empfehlungen (`modules/stacking/tonight.py`, `deepsky.py`; seit 0.20.0)
- **Katalog** (`deepsky.py`): `MESSIER` (110, M24/M40/M73 als Nicht-Ziele markiert) und `CALDWELL` (109, C50/C61 stecken in C49/C60) fest eingebaut mit
  Helligkeit und Größe; ASTAPs `deep_sky.csv` hat **keine Helligkeit** → Koordinaten von dort nur zur Genauigkeit, weitere ASTAP-Objekte
  (`astap_extras`) nur wenn benannt, Galaxie ≥ 8′ oder NGC/IC-Haufen 10–45′ (`mag=None`, Bewertung ×0,92). `ASTROFLOW_DEEPSKY=0` schaltet die
  Datei ab (Tests), `deepsky.reset()` leert die Zwischenspeicher. Filter `usable`: Helligkeitsgrenze je Gerät (`MAG_LIMITS`), Größe 3–90 % der kurzen
  Bildfeldseite, mind. 2′. Eigene Objekte: `keys_for(name)` (Schlüssel M/NGC/IC/C + Namen + ASTAP-Zweitnamen) und Position (Radius = Katalogausdehnung des
  eigenen Objekts, damit M32/M110 bei M31 entfallen). `objects.extended_catalog` speichert nun die Zweitnamen in `aliases`.
- **Rechnung** (`plan`): Höhen analytisch aus Ortssternzeit (numpy, kein AltAz je Objekt) → ~140–250 Kandidaten in ~1 s, Ergebnis 10 min gecacht (Schlüssel
  enthält Projekte, Gerät, `hide`, `collection`). Nutzbare Stunden `eff_hours` = dunkel × Höhe ≥ 30° × Mondgewicht (Phase × Abstand, `w_moon`) × Zenit
  (> 80° bei `alt_az`: 0,4) × Wolken (`clear = 1 − cloud/80`). `_score` ≈ 0–6 gemeinsam für „new“ und „more“; `_select` greedy, max. 2 je Objektart, beide
  Kategorien vertreten. Saison: Nächte heute + 0…196 Tage (14-Tage-Schritte, stündlich) → `_season` (`last`/`soon`).
- **Mehr Zeit** `_gain`: Rauschen × √(t/(t+Zusatz)), Zusatz = nutzbare Stunden (auf 0,5 h, max. 8); unter 10 % Gewinn entfällt das Ziel.
- **Nachtplan** `_night_plan`: nutzbare Nacht in 1–4 Abschnitte (≈ 3 h), Zuordnung der Ziele per Permutation (Summe der gewichteten Sichtbarkeit); Blöcke < 40 min entfallen.
- **API** `GET /api/stacking/tonight?hide=M35,12`: ausgeblendete Kennungen (localStorage `af.tonight.hidden`). Optionaler Dienst `collection_progress` (Sage →
  `{"messier": [Nummern]}`, 2 min gecacht): „Wäre dein n. Messier-Objekt“, bereits entdeckte Messier-Nummern entfallen. Tests: `tests/test_tonight.py`.

## Was dein Bild zeigt – Tiefe und Vergleich (`depth.py`, `compare.py`, `insights.py`; seit 0.25.0, Wünsche 12/13)
- **Ablauf:** `insights.Insights` = eigener Thread mit Warteschlange (`Module.start`), Zustand im Speicher, Ergebnis in
  `results.stats.depth` bzw. `.compare` (je mit `v` = `DEPTH_VERSION`/`COMPARE_VERSION`; Fehler als `{v:0, ok:false, error}`).
  `Engine._develop_job` ruft nach jeder Entwicklung `after_develop(rid)` (nur mit `wcs.json`, Gaia an, Einstellung `auto_depth`).
  Die Projektansicht (`_slim`) liefert beide **nicht** mit (wird alle 2,5 s gepollt; Tiefe hat bis 1 500 Punkte) – eigene Endpunkte
  `GET|POST /results/{id}/depth|compare`, Dateien `/results/{id}/compare/(aktuell|referenz).jpg|k<n>.png` (streng geprüft),
  Einstellungen `GET|POST /insights` (`data/settings.json` → `insights`: `auto_depth`, `simbad`, `survey`, `small_bodies`; Gaia = `storage.gaia`).
- **Geometrie:** `services.view_geometry` (aus `project_solution` herausgelöst) = WCS des linearen Stacks der Version + Lage des
  JPG-Ausschnitts (Korrelation), `to_view` rechnet FITS-Pixel → JPG-Pixel (Zeile 0 oben). Schlüssel in `wcs.json` muss zur Stackgröße passen.
- **Tiefe:** `photometry.gaia_field` (GAVO `gaia.dr3lite` → ARI → ESA, bis G 20,7, TOP 40 000, Cache `work/gaia/gaia_feld_*`) →
  erzwungene Aperturfotometrie an allen Katalogpositionen (Apertur 0,8 FWHM, Ring-Median). **Rauschen an leeren Himmelsstellen
  messen**: Stack-Pixel sind korreliert, Pixel-σ×√N war bei M101 2,9× zu klein. Erfasst = SNR ≥ 5, Schwerpunkt ≤ 0,6 FWHM,
  kein hellerer Nachbar und gemessene Helligkeit ±0,75 mag zur Katalog-G (sonst zählten HII-Knoten in M101 als G-20-Sterne).
  m5 = Nullpunkt − 2,5 log(5 σ_Apertur). Parallaxe mit Nullpunkt −0,017 mas, Parallaxe/Fehler ≥ 5. Galaxien: SIMBAD-TAP (Harvard-Spiegel,
  CDS ist aus manchen Netzen nicht erreichbar) mit Rotverschiebung und G/R/V-Helligkeit, nur Galaxientypen (keine Haufen/Gruppen),
  sichtbar = SNR ≥ 5 + Schwerpunkt + kein Vordergrundstern + Katalog-Helligkeit ≤ m5+1; `_mark_members` nimmt Einträge im
  Hauptobjekt (gleiche z, innerhalb der Katalogausdehnung) heraus. Lichtlaufzeit/mitbewegte Entfernung: astropy `Planck18`.
  Gemessen (Stellina): M101 692 Frames m5 17,7, fernstes z 0,54 (5,5 Mrd. Jahre); M81 m5 17,4, Quasar z 5,8 im Feld, aber zu schwach.
- **Vergleich:** Referenz = eigenes Ergebnis desselben Felds (`references`, ≤ 0,4°, gelöst; `pick_reference` bevorzugt anderes
  Objekt/Gerät, dann andere Nächte, dann deutlich weniger Aufnahmen) oder DSS2 rot (STScI `dss_search`, ≤ 60′, sonst CDS hips2fits;
  astropy liest die DSS-Plattenlösung). `reproject` über grobes Koordinatengitter + `map_coordinates`. Hintergrund ab, schärferes Bild
  falten, `_flux_relation` (eigen: Faktor; DSS: Parabel in log-log, **Fotoplatte ist nicht linear**), Differenz glätten, Kachel-MAD → K = 6σ.
  `_candidates` verwirft Rand, Einzelpixel, Form (Achsen > 3:1, Größe außerhalb 0,5–2,5 FWHM), gesättigte/helle Reste, Helligkeits-
  abweichung < 0,5 mag, und „neu/verschwunden“ nur, wenn das andere Bild die Quelle sicher zeigen müsste (DSS: 30σ erwartet –
  Galaxien und rote Riesen sind im Rotfilter heller, M3 gab sonst 25 Fehlalarme). Gegen DSS keine Helligkeitsänderungen.
  Katalog: SIMBAD-Kegel 8″ je Stelle (Veränderlichen-Typen), JPL SB-Ident **geozentrisch** (`mpc-code=500`) je Nacht (≤ 2), zwei
  Durchgänge, Kometen und Asteroiden getrennt – Asteroiden brauchen je nach Last 15–120 s (Zeitlimit 60 s → Hinweis).
  Ausgabe: `work/p<id>/vergleich/r<rid>/` Blinkbilder (gleiche Streckung, Raster des Stacks) + Ausschnitte; Caches `work/vergleich/`.
- **Hotpixel-Reste** (seit 0.38.2, `stackfix.spots`): Ältere Stacks ohne `hotfix` enthalten Hotpixel-Paare (ein Kanal, R/G). Im
  Kanalmittel und nach dem Falten der schärferen Aufnahme wurden sie sternförmig und passierten Form-/Einzelpixelprüfung (Arbeitsinstanz:
  3 von 5 Meldungen, Punkte mit 33–52σ im R-Kanal). Vergleich und Tiefe rufen daher vor dem Kanalmittel `stackfix.spots` auf beiden
  Bildern auf: Gruppen wie beim Entwickeln reparieren (`repair` mit vorhandenem `find`-Ergebnis), dazu alle Einzelpunkte gleicher Art
  (`find(group=False)`, gleiche Schwelle `K`) als Stellen. Kandidaten ≤ max(3 px, FWHM) an einer Stelle → `verworfen.hotpixel`
  (Referenzstellen per WCS umgerechnet), Ergebnis `hotpixel {aktuell, referenz}`; Tiefe: Sterne/Galaxien ≤ max(2, 0,8 FWHM) an einer
  Stelle gelten nicht als erfasst/sichtbar. Schwelle nicht senken: ab 5σ fanden sich 1 400–9 000 Rauschspitzen je Stellina-Stack.
  `COMPARE_VERSION` 2; `DEPTH_VERSION` bleibt 1 (keine der 18 sichtbaren Galaxien der Arbeitsinstanz lag auf einem Punkt).
- **UI** (`static/depth.js|css`, globale Funktionen, `stacking.js` prüft `typeof depthPanel`): `depthPanel`, `depthOverlay`
  (umhüllt `plateLabels`, Klasse `marks-on`), `depthTunnel` (three.js aus `/vendor/`, Richtung = Bildposition aufgeweitet, Abstand
  log10(Lj)), `comparePanel` + `compareViewer` (Blinken über `createViewer().setSplit`, Stelle anfahren mit `zoomTo`).
- Tests: `tests/test_depth_compare.py` (synthetisches Feld, Attrappen für Gaia/SIMBAD/DSS/JPL).

## Gerätediagnose (`modules/stacking/diagnose.py`, `static/diagnose.js|css`; seit 0.39.0, Wunsch 17)
Nachführung, Fokus und Bildfeld **ohne Rohbilder**, nur aus `frames.metrics`, Master-Stack und FITS-Infos. Ergebnis `results.stats.diagnose`
(`v` = `DIAG_VERSION`, Nächte der Version aus `stats.nights`), Kind `diagnose` in der Insights-Warteschlange (lokal): nach jedem Entwickeln
(`after_develop`, `ASTROFLOW_DIAG=0` schaltet ab – setzt `conftest.py`), beim ersten `GET /results/{id}/diagnose` (rechnet dann im Hintergrund),
`POST` = neu. Gerätetrend `GET /diagnose/devices` = `device_summary` (neueste Version je Objekt, je `device_key` alle Nächte, Theil-Sen über
die Zeit, `worse` erst wenn der 95-%-Bereich über 0 liegt, ab 8 Nächten / 3 Monaten). Rechenzeit Arbeitsinstanz: 3–20 s je Objekt.
- **Nachführung** (`tracking`): `skyquality.frame_links` (gleiche Dreieckszuordnung wie beim Stacken) → Lage der Bildmitte im Referenzraster und
  Drehung je Aufnahme. `segments` teilt bei Pause > `GAP_S` oder Abweichung von der Fortsetzung > `JUMP_PX` + 30 % des Driftwegs (Nachzentrieren,
  Dithern); kleine Sprünge in Driftrichtung (< ~12 px) bleiben unerkannt. Je Abschnitt Gerade → Drift, Unruhe (RMS), Drehrate. Verschmieren =
  Drift × Belichtung (Annahme: gleichmäßige Bewegung, durch `ecc_vs_speed` gestützt). Periodischer Fehler (Lomb-Scargle 1–20 min, FAP < 1 %) nur in
  Abschnitten ≥ 10 min/24 Aufnahmen – bei Stellina nie (zentriert alle 1–3 min neu).
  **Befund Arbeitsinstanz (Stellina, nur gelesen):** Drift 0,2–0,7 px/s (16–52″/min), Verschmieren 0,35–1,0 der Sterngröße bei 10 s, Drehung
  < 0,12°/min (Derotator), Unruhe 2–5 px. Warnung erst ab Verschmieren ≥ 1 × FWHM, Hinweis ab 0,5.
- **Stöße/Böen** (`events`): ≥ 3 Aufnahmen kurz hintereinander mit FWHM > Median + max(4 MAD, 35 %) bzw. ecc > max(0,8; …) bei ≥ 50 % der
  Sternzahl (sonst Wolken). Achtung: `n_stars` ist auf 150 gedeckelt, Wolken erkennt das nur grob.
- **Fokus** (`focus`): FWHM ohne Stoß-Aufnahmen; mit Standort (`tutor.pick_site`) Luftmasse aus `tutor.ephemeris`, Korrektur FWHM·X^−0,6.
  Zeit und Luftmasse sind bei auf-/untergehenden Objekten fast kollinear – an echten Daten drehte die Korrektur die Richtung oft um
  (roh −7 %/h, korrigiert +8 %/h). Daher: `sig` nur, wenn roh und korrigiert sicher dasselbe Vorzeichen haben und |kleinerer Wert| ≥ `FOCUS_MIN`
  4 %/h; `stable` nur, wenn beide Bereiche innerhalb ±4 %/h liegen. Temperatur: `FrameInfo.temp` (jetzt auch Stellina `TEMP`; ältere Frames:
  `header_temps` liest bis 400 erreichbare Köpfe, ändert die DB nicht). Marken: Stellina-Ordner `NN-images…` – Wechsel innerhalb einer Beobachtung
  = `refocus`, zurück auf `01` = `restart`; Änderung der Sterngröße 10 Aufnahmen davor/danach.
- **Bildfeld** (`field_stack`): Luminanz des Master-Stacks (auf native Pixel gebinnt, Abdeckung ≥ 0,9, 12 px Rand weg), `detect_stars` (jetzt mit
  Richtung `pa`), ohne hellste 3 %. Fit FWHM = a + b·u + c·v + d·r² (u, v auf die halbe Diagonale), robust; Verkippung = 2·|(b, c)|/a (Ecke zu Ecke),
  Wölbung = d/a, Fehler per Bootstrap. Gemeldet ab `TILT_MIN` 10 % bzw. `CURV_MIN` 12 % und 3σ. Drehen sich die Aufnahmen um > 3° (`rot_spread`),
  gilt die Karte als verwischt (`smeared`, nie gemeldet). **Richtung:** Stack-Zeile wächst nach unten in den Daten, das fertige Bild ist vertikal
  gespiegelt → Winkel mathematisch (y oben) = Stack-Winkel, `side_text`; Karten werden für die Anzeige zeilenweise umgedreht. Arbeitsinstanz:
  6–9 % (± 0,3–1,6), keine Meldung. `field_frames`: Mittel der neuen 3×3-Karten je Aufnahme (`metrics.tiles`), unabhängig von der Registrierung.
- **Neue Frame-Metriken ohne `METRICS_VERSION`-Erhöhung** (`quality.shape_metrics`): `ecc_pa` = [Winkel der Längsachse, Einheitlichkeit] und
  `tiles` (3×3 Median-FWHM) – nur neu analysierte Aufnahmen; der Vergleich Längsachse ↔ Driftrichtung (`elong_vs_drift`) fehlt bei Altbestand.
- **Tutor:** `tutor.findings` hängt `diagnose.findings` an (`ctx["diag"]` = `tutor.diag_of(stats)`), Codes `tracking_drift`, `periodic_error`
  (nur Nicht-Smart), `gusts`, `focus_drift`, `focus_stable`, `tilt`, `curvature`; mit Fokusmessung entfällt das ältere `softening`.
  Tipps gerätebezogen und vorsichtig (Stellina: Tauheizung → kein Tau-Hinweis; Sensorlage nicht selbst justierbar → Support bei bleibender Kippung).
- **UI:** `diagPanel(host, r)` (Abschnitt `#anDiag` in `analysisTab`, lädt nach Fertigstellung den Tutor neu), `diagDevices(list, host)` im
  Dashboard (`#adDiag`). Diagramme eigene SVGs, Breite `CW` aus der Spaltenbreite (Schrift bleibt 11 px).
- Tests: `tests/test_diagnose.py` (synthetische Matrizen/Metriken, Sternfelder mit Kippung/Wölbung, `demo.generate_drift` Ende zu Ende).

## Analyse-Dashboard über alle Objekte (`modules/stacking/dashboard.py`, `static/dashboard.js|css`; seit 0.36.0)
Nutzerwunsch „eigene Seite, wo man alle gleichzeitig analysieren kann, mit Statistiken und Animationen“. Route `#/analyse` (Unterpunkt von Bilder, `PAGE_WIDE`).
- **Aggregat:** `collect(eng)` = je Objekt die neueste Version (gleiche Reihenfolge wie `project_views`), Status je Art `depth|compare|sky|tutor`
  (`done|old|none|error`; Tiefe/Vergleich über `v` = `DEPTH_/COMPARE_VERSION`, Himmel über `sky_nights.version`, Tutor über den Schlüssel
  in `stats.tutor`) + fehlende Voraussetzung `blocked` (`nosolve` = kein `wcs.json`, `nogaia`, `noframes` = keine Frame-Metriken, `noquality`).
  Tutor-Befunde ohne Netz aus `stats.tutor` über `tutor.findings` (nur bad/warn). Kataloge über `deepsky.keys_for` gegen `deepsky.fixed()` (auch
  fertig importierte Bilder). „Universum“-Punkte: je Bild ≤ 60 Sterne + alle sichtbaren Galaxien aus `depth.punkte`, Himmelsrichtung aus
  `feld` + Bildlage (genähert, Norden oben), gesamt ≤ 4000. `Dashboard.base()` hält das Ergebnis, bis sich `results` (Anzahl/Max-ID/Summe der
  stats-Längen), `sky_nights`, die `wcs.json`-Menge oder Gaia ändern, spätestens `CACHE_S` 120 s (Solver-Suche kostet ~0,3 s → nicht in der Signatur).
  `summarize(base, filter)` rechnet Kennzahlen, Rekorde, Verläufe, Rangliste (`m5_1h` = m5 − 1,25·log10 t[h], √t-Gesetz), Befunde, Veränderungen
  je Anfrage aus dem Zwischenspeicher; `live()` blendet laufende Insights-/Sammelaufträge in die Status ein.
- **API:** `GET /api/stacking/analysis?device=&year=&night=&q=`, `GET /analysis/universe` (erst auf Knopfdruck „3D-Wolke“), `POST /analysis/plan|run
  {kinds, scope: missing|errors|all, ids?}`, `POST /analysis/pause {paused}`, `POST /analysis/stop`, `POST /analysis/develop {ids}` (Objekte mit Stack,
  ohne `wcs.json` → Entwicklungsauftrag; die Lösung entsteht beim Entwickeln, `auto_depth` misst danach). Nicht in `/projects` (wird gepollt).
- **Sammelauftrag** `Batch` (eigener Thread, Aktivität „Analyse aller Objekte“, zählt in `busy_jobs`): Reihenfolge `KINDS` = Gerät (`diagnose`, seit 0.39.0), Tutor, Himmel (lokal),
  Tiefe, Vergleich (nutzt den Nullpunkt der Tiefe). Tiefe/Vergleich gehen einzeln durch `insights.submit` und werden abgewartet, danach `GAP_S` 3 s
  Pause → nie zwei Netzauswertungen gleichzeitig. Himmel = `skyquality.compute_project` (wartet, solange `SkyRunner` rechnet), Tutor = `build_report(refresh=True)`
  (mit Wetter ebenfalls Pause). Anhalten wirkt nach dem laufenden Schritt; Zustand nur im Speicher.
- **UI:** Abschnitte zeichnen nur bei geänderten Daten neu (`paint` mit Signatur – sonst liefen Animationen bei jedem Poll erneut); Poll der Shell lädt nur
  während eines Auftrags laufend, sonst alle 15 s. Diagramme als eigene SVG-Helfer (`scatter`, `ring`), 3D-Wolke mit three.js aus `/vendor/` (`loadThree`).
  Animationen über `.an-sec.in` (`anReveal`/`anCountUp` aus `analysis.js`), aus bei `prefers-reduced-motion`. Sprung zur Bildstelle: `sessionStorage af.an.focus`
  `{rid, x, y, w}` → `analysisTab` zoomt nach dem Laden des Fotos. localStorage `af.analysis.filter|kinds|sort`.
- Tests: `tests/test_analysis_dashboard.py` (synthetische Bibliothek `tests/analysis_seed.py`, Attrappen statt Netz; prüft u. a. höchstens eine
  Netzauswertung gleichzeitig und Anhalten), Route `#/analyse` in `test_ui_layout.py`.

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
UI-Filter (seit 0.21.0, eine Leiste über der Liste): Zustand als Reiter (`LIB_TABS`, Standard „Offen“ = `n_new > 0`), Art/Format als `.menu`-Mehrfachauswahl,
localStorage `af.library.filter` (`{tab, kind, ext}`); Formate je Gruppe aus `library.file_formats` → `groups[].exts` (`{"fits": 141}`);
„Alle neuen“ (Dreifach-Kästchen) und Import wirken nur auf sichtbare Gruppen. Beim ersten Laden sind Gruppen mit Neuem vorgewählt (wie vor 0.21.0).
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
Tiefe/Vergleich: `results/{id}/depth|compare[/<datei>]`, `insights` (s. „Was dein Bild zeigt“).
Kern: `/api/app` (Module, Build), `/api/folders?path=` (Ordnerdialog), `/api/sites…` (Ortsliste), `/api/status` (`{version, busy, modules:{id: status}}`), `/api/activity` (alle Module), `/api/modules` (ein/aus).
**Achtung:** Zwei Server auf demselben Datenordner = zwei Worker (beim Start werden „running“-Jobs des anderen neu eingereiht) – Tests/Vorschauen mit eigenem Datenordner.

## Web-UI (`astroflow/static/` + `modules/*/static/`)
Hash-Routing: `#/` Übersicht, `#/nacht`, `#/himmel` (Himmelsqualität), `#/analyse` (Analyse-Dashboard), `#/bilder` (Sessions), `#/galerie` (fertige Bilder), `#/p/<id>/<bild|analyse|aufnahmen|versionen|optionen>`, `#/import`,
`#/skytrace[/neu|/3d (= 3D-Erde, Analysen)|/erde[/<Nacht>]|/statistik|/<lauf>/<vorschau|kandidaten|beobachtungen|protokoll|3d>]`, `#/sage[/wiki|/entdeckungen|/sammlung|/<eintrag>]`, `#/activity` (Aufträge), `#/settings/<modul|standorte|module>`.
**Designsystem (seit 0.21.0, Nutzerauftrag „professionelles, konsistentes Designsystem wie eine native Desktop-App“):** Tokens in `:root` von `style.css`
(`--bg/--sidebar/--surface/--surface-2/--border/--fg/--mut/--dim/--acc/--ok/--warn/--bad`, Maße, `--t` 160 ms); alte Namen (`--panel`, `--grad`, `--acc2/3` …)
sind Aliase. **Inhaltsbreiten:** der Router gibt jedem Ansichtsbehälter die Klasse `page` (zentriert, `--page-max` 80rem), `page wide`
(Arbeitsflächen: `PAGE_WIDE` in `app.js` – Galerie-Betrachter, SkyTrace-Lauf und 3D; die Objektseite `#/p/…` ist eine normale Seite) bzw. `page narrow` (`--page-narrow` 60rem, Einstellungen);
Lesetext mit `.readable` / `--read` 42rem. Keine Karte in der Karte (`.panel .panel` ohne Fläche). Kontextpanel startet geschlossen
(`AF.ctx.shown`, je Ansicht neu, nicht gespeichert), Knöpfe mit `data-ctx-toggle` öffnen es. Objektseite, Reiter Bild: Details als
Spalte `.imgwrap > #imgSide` neben dem Bild (Standard sichtbar, `bild.details`); der „Details“-Knopf schaltet dort die Spalte (`data-side`).
Die Spalte zeigt nur noch Kernzahlen (`analysisSummary`: Bildqualität, Grenzgröße, Bortle, Seeing → Link in die Analyse); importierte Bilder behalten die Bildprüfung.
**Reiter Analyse** (seit 0.27.0, `static/analysis.js|css`, Nutzerwunsch „zu gequetscht in der Detailspalte“): `analysisTab(t, {p, r, solve, viewerFor})` = kompakter
Betrachter (gleiche Overlays) + Abschnittsliste, darunter breite `.an-sec`: `depthPanel(host, r, viewer, {wide, onFocus, onSolve})` (Kennzahlen `data-count`,
Fakten mit `data-x/y` → `viewer.zoomTo`; `opts.on3d`/`onData` für den 3D-Knopf), `comparePanel` mit
`compareViewer` links (`.an-cmp.has-blink`), `AFsky.analysis(host, pid)` (Bortle-Skala `bortleScale`, Kurven `sqm`/`seeing`, Windrose aus `/stacking/sky`),
`tutorPanel`. `anReveal` blendet Abschnitte per IntersectionObserver ein (`.in`), `anCountUp` zählt die erste Zahl eines Texts hoch (deutsches Format);
CSS-Animationen hängen an `.an-sec.in` und entfallen bei `prefers-reduced-motion`.
**3D im Betrachter** (seit 0.27.1, Nutzerwunsch „kein 2. Betrachterfeld, aus dem Bild oben hervorgehen“): Knopf `[data-v3d]` in der `.vbar` →
`depthTunnel(d, r, viewer)` legt `.tunnel.in-viewer` über das Foto (vor die `.vbar`, Ziehen/Mausrad gehen nicht an den flachen Betrachter) und gibt
`{close(animiert)}` zurück. Startbild = Foto als Textur (gleiche Datei, sRGB, deckend), Kamera im Ursprung mit `fitFov` = Einpassen (contain);
Punkte liegen auf den Sichtstrahlen ihrer Bildposition (`dirOf × Abstand`) – von vorn decken sie sich daher mit dem Foto. Übergang: `k` schiebt sie
von der Bildebene (`tF` ≈ 30 % des Trichters) in ihre Entfernung, `fade` (Blickwinkel zur Bildebene) blendet Punkte/Rahmen ein und das Foto auf
75 % ab. Ohne Betrachter bleibt der Vollbild-Dialog. Galerie = Betrachter `#/galerie/<id>`
(`pictureView`, Vor/Zurück aus `galleryOrder`), Bilder = Verarbeitung `#/p/<id>`; `objectKnowledge(host, name)` holt Sages Wissenskarte
(`/api/sage/kb/card`, legt bei Bedarf einen Grundeintrag an) für Betrachter und Details-Panel. **Alle Maße in `rem`** (1rem = 16px; nur 1–2px-Rahmen und Media-Queries in px) – `html` wird ab 1800/2300/3200 px Breite größer
(17,5/19,5/23 px) bzw. per Einstellung „Darstellung“ (`localStorage af.ui.scale`, `applyUiScale`); neue Stile daher in `rem` schreiben. Nur dunkel. Keine Marketing-Überschriften. **Atmosphäre (seit 0.24.0, Nutzerwunsch „Farbverläufe und bisschen glassy look wie früher“):**
`static/look.css` über `<html data-look="dezent|nebula">` (ohne Attribut = „Schlicht“, das Anthrazit von 0.21; `index.html` setzt es schon im `<head>`,
`applyLook`/`UI_LOOKS` in `app.js`, `localStorage af.ui.look`, Standard „dezent“). Tokens `--bg-grad`, `--glass`, `--glass-bar`, `--glass-border`, `--glass-hi`,
`--grad-acc`, `--grad-soft`, `--glow`. Verlauf nur im Seitenhintergrund (am `<html>`, `fixed`), auf Primärknöpfen, aktiver Navigation, Reiterstrich;
Glühen nur auf Primärknöpfen. `backdrop-filter` nur auf Werkzeugleiste, Seitenleiste, Kontextpanel; Karten/Panels nur halbtransparent (Glasregeln per
`:where(:root[data-look])` mit niedriger Spezifität, damit flache Varianten wie `.img-side .panel` gewinnen). Menüs, Dialoge, Eingaben deckend.
Eingaben nur `background-color` setzen (Suchfeld trägt sein Symbol als `background-image`). `prefers-reduced-transparency` → deckend ohne Unschärfe.
Sternschnuppen: `static/meteors.js` (`window.meteors`, Ebene `#meteors`, `z-index: -1` hinter allem; Nutzerwunsch „selten, Algorithmus nach
echten Daten“): `sky(date, lat, lon)` rechnet die sichtbare Rate – `SHOWERS` (IAU-MDC: λ☉max, ZHR, Jenniskens-Profil 10^(−B·Δλ), Radiant, v, r),
HR = ZHR · sin(Radiantenhöhe) · r^(Grenzgröße − 6,5), sporadisch 2 + 10·sin(Apexhöhe), Sonne über −6° → 0, Mondphase senkt die Grenzgröße.
Ort aus `/api/sites/resolve` (sonst 50° N, Länge aus der Zeitzone). Alle 15 s Poisson-Ziehung mit Rate HR/5; Strom-Meteore fliegen vom Radianten weg
(Blick Süd), Tempo/Länge/Farbe aus v. `info()` liefert den Text unter Darstellung. `localStorage af.ui.meteors`, aus bei reduzierter Bewegung und
ohne Stil. Test: `test_meteors.py` (Geminiden-/Perseidenmaximum, Tag, Apex morgens > abends, Radiant unter dem Horizont).
**Rückmeldung** (seit 0.24.0, Nutzerwunsch „responsiver“, Ende von `style.css`, gilt für alle Stile): Drücken = `scale(.94–.985)` in 120 ms, nur
transform/opacity/Farben. `api()` merkt den zuletzt gedrückten Knopf (`lastPress`, ≤ 0,6 s) und setzt bei schreibenden Anfragen `.pending`
(gesperrt, Kreisel nach 150 ms über `::before/::after`) – `.btn` darf daher keine eigenen `::before/::after` bekommen. Aufwendige Animationen nur für echte Prozesse (`viz.js`, als „Schema“
gekennzeichnet, mit echten Zahlen aus Job/Engine), sonst 150–250 ms. AppShell: Grid `top / side main ctx`; Seite scrollt als Ganzes (Layout-Test braucht das),
Sidebar/Panel `sticky`. Breiten: ≥ 1280 Panel im Raster, darunter Schublade (`.shell.ctx-shown`); 861–1100 Sidebar nur Symbole; ≤ 860 Leiste unten + `#subnav`.
Navigation wird einmal gebaut (`buildNav`), `navState` setzt nur Klassen. Bilder-Objektseite: keine Schrittleiste im Kopf (Nutzerwunsch) – der Fortschritt steht in der Bildfläche (`live.js`); `pipeStep` bildet `jobs.stage` auf Schritte ab (für die Schema-Animation in den übrigen Reitern) (Kalibrierung und
Registrierung laufen innerhalb „Stacking“ und werden nicht getrennt gemeldet).
**Seitenwechsel** (`route`): neue Ansicht als `.page.pending` (unsichtbar) aufbauen, die alte bleibt als `.page-ghost` (fest positioniert,
IDs entfernt, nicht klickbar) stehen; fertig → Geist sofort weg + `.swap` (kurze Einblendung von 70 %); dauert es > `SKEL_AFTER` 350 ms,
Geist ausblenden und Skelett zeigen. Keine Kreuzblende (Text lag doppelt übereinander). Messen: Layout-Shift-Observer + Zwischenbilder.
Ladebalken `loadbar` (unter der Werkzeugleiste, erst nach `LOADBAR_AFTER` 150 ms). **Langsame Ansichten nicht als Ganzes abwarten**
(seit 0.21.1, Nutzerbefund „Übersicht ploppt auf einmal auf“): Gerüst sofort zurückgeben, Abschnitte einzeln füllen – Übersicht: `load()`
startet je Abschnitt eigene Anfragen (`paintCard` je Modul, `paintFeatured` je homeFeatured), Platzhalter `.skel-*` in fester Höhe, erste Füllung `.fill`.
Prüfen mit künstlich verzögerter API (Playwright async `page.route` + `asyncio.sleep` – synchron blockiert `time.sleep` alles).
Polling alle 2,5 s (`/api/status`), Ansichten zeichnen nur bei geänderter Signatur neu (Formulare/Zoom bleiben erhalten). `route()` verwirft Ansichten, die erst
nach einem weiteren Klick fertig werden (`routeTicket`), und gibt jeder Ansicht einen eigenen Behälter in `#view` (sonst schrieb eine
verworfene, noch ladende Ansicht über die aktuelle). Ansichten, die nach `await` global per `$('#…')` suchen, vorher `el.isConnected` prüfen. Bildbetrachter `createViewer(host, src, cmpSrc, labels, ph, {overlay})` mit Zoom/Pan/Pinch, Vergleich
und optionalem SVG-Overlay in Bildpixeln (Sage-Objektmarken). Achtung: in `createViewer` keine lokale Variable `esc` (TDZ-Fehler). Nach `opts.slowAfter` (Standard 20 s) ohne `load` bzw. bei `error` zeigt `.vload` einen Hinweis mit „Erneut versuchen“ (lädt mit `?neu=` neu; Test `test_viewer_slow.py`).
Globale Auftragsanzeige oben (`showJobPill` aus `/api/activity`, alle Module) + Toast beim Fertigwerden. Dateien überall ablegen: zentraler Bilder-Import für FITS und JPG/PNG/TIFF.
Fertige Bilder: `modules/stacking/picture_import.py`, Projektoption `input_type='picture'`, Original + Vorschauen als Ergebnis ohne Rohframes/Stack;
SHA256 gegen doppelte Uploads, eigenes Ereignis `stacking.imported` → Sage (kein SkyTrace). Keine erfundenen Belichtungs-/Frame- oder Qualitätswerte.
Stellina-Verlaufsbilder: EXIF Make/Model/ImageDescription/DateTime + `img-…-output` → `imported.series`, ein Projekt je Objekt/Gerät/Nacht mit geordneten Versionen.
`picture_import.attach_previews` ordnet diese bei passenden Rohdaten per `options.gallery_parent` zu (nach Upload und Scan); Hauptgalerie/Statistik/Sage-Auswahl zeigen nur eigenständige Projekte.
Begleitbilder bleiben über den Tab `teleskopbilder` erreichbar; beim Löschen des Elternprojekts wird die Zuordnung aufgehoben, keine Begleitdatei mitgelöscht.
Zusätzlicher Schutz (`picture_import.skipped_pictures`, Server + Frontend): > 10 FITS im Upload → nur FITS (auch kein TIFF, Nutzerwunsch 31.10.);
sonst > 10 TIFF/FITS → alle JPG/PNG übersprungen
(am 30.10.2026 landeten 233 Stellina-Vorschauen in der Galerie – der Server lief noch ohne die Verlaufsbild-Erkennung).
Sage zeigt einen Verweis zum zentralen Import; eigener Upload nur als Rückfall bei deaktiviertem Bilder-Modul. API `/sage/analyze` bleibt kompatibel.
Befehlspalette Strg+K: Einträge aller Module (`palette()`) + Aktionen der aktuellen Ansicht (`actions()`).
Tastenkürzel: `/` Suche in Listen, `1–4` Tabs (Objekt, SkyTrace-Lauf), `F` Vollbild, `B` Vorher/Nachher. Server schickt `Cache-Control: no-cache` für `/`.
**Nach Änderungen an `index.html`/`app.js`/`style.css` `?v=` in `index.html` hochzählen**; Modul-Dateien bekommen die Build-Kennung automatisch.

## Tests (`tests/`)
**Parallel (seit 04.11.2026):** `pytest.ini` setzt `-n auto --maxprocesses=8 --dist load` (höchstens 8 Worker; voller Lauf ~82 s, zusätzlicher RAM-Bedarf gemessen ~3,3 GB) (pytest-xdist, `requirements-dev.txt` – dort auch Playwright). Seriell 5,5 min → parallel ~1,2 min,
gleiche 240 bestanden / 4 übersprungen. Regeln für neue Tests: nur `tmp_path`/`tmp_path_factory` für Dateien (nie feste Pfade oder die Arbeitsinstanz), Server auf Port 0/`_free_port()`,
Umgebungsvariablen nur per `monkeypatch`, keine Abhängigkeit von der Reihenfolge oder anderen Tests. `conftest.py` begrenzt unter xdist BLAS-/Pipeline-Threads auf 2 je Worker
(`ASTROFLOW_THREADS`), sonst bremsen sich viele Worker gegenseitig. `conftest.py` setzt `ASTROFLOW_GRAXPERT/GPU/GAIA=0` schon beim Import (vor allen Modul-/Session-Fixtures, von Unterprozessen geerbt) und verbietet per `subprocess.Popen`-Schutz echte `graxpert*`/`starnet*`-Programme außerhalb des Temp-Ordners (`test_no_real_tools.py`) – am 04.11.2026 starteten Browser-Fixtures ein installiertes GraXpert dutzendfach und ließen den Rechner abstürzen. Browser-Fixtures `server`/`browser` stehen in `conftest.py` (Session-Scope = je Worker einmal); alle Browser-Tests
tragen automatisch den Marker `ui`. **Sitzungen:** vor dem PR den vollen Lauf; beim Entwickeln `-m "not ui"` bzw. nur die betroffene Datei.
Synthetische Daten aus `demo.py` (`generate`, `generate(anonymous=True)` ohne Koordinaten, `generate_mono` mit Filtern).
Plate-Solving-Tests nutzen eine ASTAP-Attrappe (Python-Skript, schreibt echtes `.ini`-Format) über `SolverConfig(cmd_prefix=[...])` – kein echtes ASTAP nötig.
**Oberfläche** (`test_ui_layout.py`, seit 31.10.2026): echter Chromium über Playwright (in `requirements-dev.txt`, nicht in `requirements.txt`;
fehlt es, wird übersprungen – `pip install playwright && python -m playwright install chromium`). Alle Ansichten mit Demodaten bei 375/790/1440 px:
nichts darf über seinen Kasten (nächster Vorfahr mit Rahmen/Hintergrund) oder die Seite ragen, keine JS-Fehler; Schalterzeilen müssen per Klick umschalten.
Neue Ansichten in `ROUTES` eintragen. Achtung: globales `td { white-space: nowrap }` – Namensspalten in schmalen Tabellen brauchen `white-space: normal`.

## Stolperfallen (Windows)
- Shell ist Git-Bash: In Heredocs/`python -c` werden Backslashes umgedeutet (`\a` wurde zu BEL). Windows-Pfade in Skripten → Skript per Datei schreiben, raw strings.
- Python-Dateien explizit mit `encoding="utf-8"` schreiben (Standard ist cp1252).
- ASTAP kann an mehreren Orten liegen (`C:\Program Files\astap` oft nicht beschreibbar, `%LOCALAPPDATA%\astap` für die aktuelle CLI + Datenbank).

## Lokale Instanz und private Angaben
**Persönliches gehört nie ins Repo** (Benutzerpfade, Laufwerke, Heimnetz/IP, Beobachtungsort, Schlüssel). Wer eine eigene Instanz betreibt,
legt Pfade, laufende Server und echte Daten in **`AGENTS.local.md`** ab (steht in `.gitignore`) – Agenten lesen sie, falls vorhanden, zusätzlich.
Tests und Beispiele nutzen erfundene Orte/Pfade; Screenshots nur mit maskierten Pfaden und Standort.
- **Nie Testdaten in eine Arbeitsinstanz schreiben** – Experimente in eigenem Datenordner mit kopierten Frames (s. „Achtung“ unter Server).
- Datenordner beim Verschieben: Pfade stehen absolut in `frames.path`, `frames.info.path`, `results.files`, `solves.key` → alle umschreiben.
  Frame-IDs hängen am Pfad relativ zum Eingangsordner – bleibt die Struktur unter `input/` gleich, werden Dateien nicht neu eingelesen.

## Grenzen / mögliche nächste Schritte
Keine Mosaike (Stackfläche = Referenzbild), kein echtes Drizzle (2× interpoliert), keine Planeten-/Mond-Modi (Kometenmodus seit 0.22.0, nur ein Band), kleiner eingebauter Katalog
(großer nur mit ASTAP-`deep_sky.csv`), Dockerfile (ASTAP-Download) noch nicht real gebaut/getestet, Satellitenspur-Erkennung unzuverlässig,
GraXpert/StarNet nicht im Docker-Image, keine Verzerrungskorrektur, keine GPU, waagerechte Hintergrundstreifen bei NGC 1579 (Stack).
