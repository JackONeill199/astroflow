# AGENTS.md – AstroFlow

Kompakter Einstieg für KI-Agenten und neue Sessions. Nutzerdoku: `README.md`, Änderungen: `CHANGELOG.md` (bei Nutzer-sichtbaren Änderungen ergänzen).

## Was ist das
AstroFlow: lokale Astro-Plattform im Browser aus drei Modulen (seit v0.6, 28.10.2026 – Nutzerwunsch: „ein Produkt, einheitliche UI,
Module einfach wartbar und erweiterbar“):
- **Bilder** (`modules/stacking`, Kernmodul): vollautomatische Pipeline FITS-Rohbilder → fertiges Astrofoto.
- **SkyTrace** (`modules/skytrace`, aus Repo `JackONeill199/SkyTrace`): Lichtspuren/bewegte Objekte in Bildfolgen, Katalogabgleich. Details: `astroflow/modules/skytrace/AGENTS.md`.
- **Sage** (`modules/sage`, aus Repo `JackONeill199/starlight-sage`, Node → Python portiert): seit 2.0 lebende Astronomie-Enzyklopädie – Bilder bestimmen, Objekte aus Fachkatalogen + KI-Agent recherchieren (Belegpflicht), persönliches Beobachtungsgedächtnis, Wissensnetz, proaktive Entdeckungen, Chat, Geschichten/Audioführung. Details: `astroflow/modules/sage/AGENTS.md`, Architektur `docs/SAGE.md`.
Zielgruppe: Smart-Teleskope (Seestar, Vespera, Stellina) und klassische Astrokameras. Lokal, ein Nutzer, keine Konten.
Stack: Python 3.12+ · numpy/scipy/astropy · FastAPI + uvicorn · SQLite · Vanilla-JS-SPA ohne Build-Schritt. Lizenz MIT (Detektormodell in SkyTrace: AGPL-3.0, siehe `models/`). Version 0.6.0.

**Sprache:** UI, README, Kommentare und Fehlermeldungen auf **Deutsch**. Der Nutzer schreibt Deutsch.

## Produkt-Architektur (Kern + Module)
```
astroflow/server.py        Kompositionswurzel: lädt Module, /api/app|status|activity|modules, Herkunftsschutz, statische Dateien
astroflow/core/            config.py (Settings), store.py (data/settings.json, ein Abschnitt je Thema), modules.py (BaseModule, Context, Registry)
astroflow/modules/<id>/    __init__.py (class Module), api.py (Router → /api/<id>), static/ (→ /m/<id>/), AGENTS.md
astroflow/static/          Shell: index.html, app.js (Router, Navigation, Übersicht, Aktivität, Einstellungen, Palette, Viewer, Helfer), style.css
```
- **Modul-Vertrag Backend** (`core/modules.py`): `router()`, `start()/stop()`, `status()` (klein, wird alle 2,5 s gepollt; `busy_jobs` zählt für die Anzeige),
  `activity()` (gemeinsames Format `{key,title,kind,detail,state,progress,started,finished,href}`), `scripts/styles`. Reihenfolge/Liste: `AVAILABLE`.
- **Module reden nicht per Import miteinander**, sondern über Dienste in `ctx.services`: Stacking stellt `plate_solver` (`PictureSolver`),
  `project_picture`, `project_solution`, `project_frames`, `observation_log`, `catalog_resolve` und `catalog_objects` bereit; Sage und SkyTrace nutzen sie. Fehlt ein Dienst, muss das Modul trotzdem laufen.
- **Ereignisse** (`ctx.on` / `ctx.emit`, Liste in `core/modules.py`): `stacking.stacked(project_id, result_id, name)` kommt aus
  `Pipeline.process` nach dem Entwickeln und **vor** dem Löschen hochgeladener Rohbilder (`Pipeline.on_stacked`). Empfänger (seit 28.10.2026,
  Nutzerwunsch „gestackte Aufnahmen automatisch durch SkyTrace und in die Entdeckungen, mit animierten Erfolgen“):
  SkyTrace `auto.AutoAnalysis` (Hardlinks je Nacht → Warteschlange, Einstellung `skytrace.auto`) und Sage `Module._stacked`
  (Bild bestimmen → Entdeckungen, Einstellungen `sage.auto_discover`/`auto_story`). Empfänger kehren sofort zurück (eigene Threads).
- **Modul-Vertrag Frontend** (`AF.register(id, {nav, routes, home, settings, palette, status, drop})`, Kommentar in `app.js`): Helfer (`api`, `send`, `toast`,
  `createViewer`, `folderDialog(start, pick, {title, ok, hint})` (Ordner auf dem Server-Rechner wählen, über `/api/folders` → `core/folders.py`: Laufwerke, Unterordner, FITS-/Bildzählung; genutzt von Hauptordner und SkyTrace), `picker` (Dropdown mit Suche für Modelle/Stimmen, Listen aus `core/models.list_models`), `jobLine`, `confirmDlg`, `crumbs`, `store` …) sind global. Modul-Skripte lädt die Shell aus `/api/app` mit `?v=<build>`
  (Build-Kennung aus Dateizeiten) – nur `index.html` braucht weiter das manuelle `?v=`.
- Module ein/aus: Einstellungen → Module & System bzw. `ASTROFLOW_MODULES=stacking,skytrace`; ein defektes Modul erscheint mit Fehler, die App läuft weiter.
- Sicherheit: schreibende Anfragen mit fremdem `Origin` → 403 (Middleware `guard`). Schlüssel stehen im Klartext in `data/settings.json`.
- **Neues Modul:** Unterpaket mit `Module(BaseModule)` anlegen, in `AVAILABLE` eintragen, `static/<id>.js` mit `AF.register`, Tests in `tests/`.

## Befehle
```bash
.venv/Scripts/python -m pytest -q tests          # Windows-venv liegt im Repo (.venv); ~30 s, muss grün sein
python -m astroflow serve --port 8080 --data ./data   # Web-UI (alle Module)
python -m astroflow process --data ./data             # alles per CLI verarbeiten
Start.bat / Stop.bat                                  # Windows: Server im Hintergrund starten/beenden (Port 8766, ./data)
Netzwerk-freigeben.bat [entfernen]                    # Firewall-Regel „AstroFlow LAN“ (TCP 8766, privat, LocalSubnet; braucht Admin – Nutzer führt aus)
python -m astroflow demo ./data/input/demo            # synthetische Testdaten (Bayer, 2 Nächte, Darks/Flats)
docker compose up -d --build                          # Image enthält ASTAP + D05 (ARG ASTAP_DB=d05|d50)
```
Git-Repo: github.com/JackONeill199/astroflow (privat, Branch main; `data/`, `data_real/`, `.venv` per .gitignore ausgeschlossen).
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
   (gemessen 01.11.2026: 0,6 s / ~8 s / 131 s / beim Nutzer nicht erreichbar), Cache `work/gaia/`.
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
   „bisher + erste k Frames des Stapels“ (Clipping-Maske aus dem ganzen Stapel) → `work/p<id>/stufen[_<Band>]/n<t>.npz` (float16,
   ADU/`STAGE_SCALE`, ~40 MB je Stufe bei Stellina) + `.json` (n, Belichtung). Neuaufbau löscht sie mit. `develop` rendert nach dem
   Speichern (`_render_stages`, nur bei **einem** Kanal) alle Stufen < Frame-Zahl mit den Endparametern und festem Ausschnitt
   (`processing.process(crop=stats.crop)` – gleiche Pixelgröße wie das fertige Bild) → `<base>_s<n>.jpg`, `results.files.s<n>`,
   `stats.stages`; GraXpert (`_ai`) und StarNet werden dafür weggelassen (`stats.stages_simplified`). ~11 s je Stufe (3072×2080).
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
   → Auswahl bleibt relativ (Gewichtung reicht; wer Schärfe will: Option „Nur die besten X %“). Auf `D:\Astro\Stellina` liegen ungenutzte
   Nächte: NGC 1579 2022-08-07 (422 Frames), M101 2023-02-07 (247) – Import über „Hauptordner durchsuchen“.
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
11. **Volles Laufwerk** (`diskspace.py`, seit 01.11.2026 – Hauptordner-Import von E: kopierte 95 GB nach C:, Platte voll, jeder Auftrag
   scheiterte, auch SQLite): Worker startet nur ab `MIN_FREE` 5 GB (`Engine.hold()`), `prog` prüft alle 5 s und bricht unter `ABORT_FREE` 2 GB ab;
   Volle-Platte-Fehler (`diskspace.is_full`, auch `sqlite3 … full`) und Abbruch → Job wieder `queued` + `Engine.paused` (Grund in
   `status.hold`/`paused`). `POST /api/stacking/queue {paused}` (UI: Aktivität → „Aufträge anhalten/fortsetzen“; `cancel`-Event bricht beim
   nächsten Fortschritt ab, Stacking setzt am Checkpoint fort). Pause ist nur im Speicher (Neustart = fortsetzen). Watcher scannt unter 2 GB nicht.
   Import (`library.start_import`): Kopien über Laufwerksgrenzen (`same_drive`) müssen `IMPORT_RESERVE` 20 GB übrig lassen, sonst 400; beim
   Kopieren Abbruch unter 5 GB – nicht übernommene Dateien bleiben in `to_import` (Gruppe `partial`), Import später fortsetzbar.
   Aufräumen (`housekeeping/cleanup`) ist bei stehender Warteschlange (`hold()`) trotz wartender Aufträge erlaubt und hält sie währenddessen an.
   Test: `test_diskspace.py`.
12. **Speicherbelegung & Aufräumen** (`housekeeping.py`, API `GET /housekeeping?keep=`, `POST /housekeeping/cleanup {keys, keep}`,
   UI: Einstellungen → Bilder → „Speicherbelegung“, seit 31.10.2026): Belegung je Bereich + größte Objekte; Aufräumbares: verwaiste
   Ausgabedateien, Stacks gelöschter Objekte, Temp (> 1 h), leere Ordner (> 1 h), DB verdichten (VACUUM) – vorausgewählt; ältere Versionen
   (neueste `keep` je Objekt bleiben, nicht bei importierten Bildern) und Sicherungen – nur auf Wunsch. Client schickt nur Schlüssel, der Server
   ermittelt die Pfade neu und löscht nur innerhalb des Datenordners; 409 während eines Jobs. Rohdaten und Stacks bestehender Objekte nie.

## Hauptordner durchsuchen (`modules/stacking/library.py`, seit 31.10.2026)
Import-Ansicht → „Hauptordner durchsuchen“ → „Ordner wählen …“ (`folderDialog`, kein Pfad eintippen; Wahl startet die Suche): Ordner auf dem Server-Rechner (z. B. ganze Stellina-Platte). Hintergrund-Thread findet Gruppen
(`find_groups`: „…_observation_X“ = eine Gruppe samt Unterordnern, sonst je Ordner mit FITS; Ordnernamen dark/flat/bias → Kalibrierung; Ordner nur mit
JPG/PNG/TIFF → fertige Bilder, nicht unterhalb von FITS-Gruppen), bildet je Datei den Inhalts-Fingerabdruck (`scanner.fingerprint`, Bilder SHA256) und
gleicht mit `frames.info.fp` bzw. `results.stats.imported.sha256` ab – **Schnellpfade** (seit 01.11.2026; Stellina-Platte 31 718 FITS: vorher ~19 min, erster Lauf 56 s, danach 1,1 s ohne Lesezugriff): (1) `name_key` = Pfad ab dem `…_observation_X`-Ordner + Dateiname (+ `frames.size`, falls bekannt – wird seit 01.11. beim Einlesen gesetzt, älter nur nach dem Löschen) → bekannt ohne Lesen; (2) Tabelle `library_index` (Pfad, Größe, mtime → Fingerabdruck); (3) Beobachtungsordner ohne Namenstreffer: nur `SAMPLE` = 4 Stichproben (gemerkte zählen mit) – trifft keine, ist der Rest neu (`skipped`), sonst ganze Gruppe lesen (z. B. M3: früher ohne Ordnernamen hochgeladen). Größen aus `os.scandir` (kein stat je Datei) → Zustand `new|partial|unstacked|done` (fertig = gestackt/verworfen/Rohbild gelöscht/Duplikat).
Import legt nur **unbekannte** Dateien als Hardlink (Rückfall Kopie) unter `input/uploads/bibliothek/<hash>/<Gruppenordner>/…` ab, dann `scan_and_queue()`;
Bilder gehen über `import_picture`. Quelldateien bleiben unberührt. API: `GET /library`, `POST /library/scan {root}`, `POST /library/import {ids}` (Ids stammen
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
REST unter `/api/stacking`: `status`, `scan`, `projects[/{id}]` (GET/PATCH/DELETE), `projects/{id}/process|solve|frames`, `frames/{id}` (PATCH manual),
`jobs`, `develop-all` (alle Objekte mit Master-Stack neu entwickeln, Galerie-Knopf), `upload` (behält bis zu 4 Ordnerebenen, blockt `..`), `results/{id}/{tif|png|jpg|thumb|fits|before}`, `reviewer`, `solver`, `storage`, `starnet`.
Kern: `/api/app` (Module, Build), `/api/folders?path=` (Ordnerdialog), `/api/status` (`{version, busy, modules:{id: status}}`), `/api/activity` (alle Module), `/api/modules` (ein/aus).
**Achtung:** Zwei Server auf demselben Datenordner = zwei Worker (beim Start werden „running“-Jobs des anderen neu eingereiht) – Tests/Vorschauen mit eigenem Datenordner.

## Web-UI (`astroflow/static/` + `modules/*/static/`)
Hash-Routing: `#/` Übersicht (Karten aller Module), `#/bilder`, `#/p/<id>/<bild|aufnahmen|versionen|optionen>`, `#/import`,
`#/skytrace[/neu|/<lauf>/<vorschau|kandidaten|beobachtungen|protokoll>]`, `#/sage[/entdeckungen|/<eintrag>]`, `#/activity`, `#/settings/<modul|module>`.
Navigation wird aus den Modulen gebaut (Gruppe je Modul; `sec`-Einträge sind auf dem Handy ausgeblendet).
Polling alle 2,5 s (`/api/status`), Ansichten zeichnen nur bei geänderter Signatur neu (Formulare/Zoom bleiben erhalten). `route()` verwirft Ansichten, die erst
nach einem weiteren Klick fertig werden (`routeTicket`), und gibt jeder Ansicht einen eigenen Behälter in `#view` (sonst schrieb eine
verworfene, noch ladende Ansicht über die aktuelle). Ansichten, die nach `await` global per `$('#…')` suchen, vorher `el.isConnected` prüfen. Bildbetrachter `createViewer(host, src, cmpSrc, labels, ph, {overlay})` mit Zoom/Pan/Pinch, Vergleich
und optionalem SVG-Overlay in Bildpixeln (Sage-Objektmarken). Achtung: in `createViewer` keine lokale Variable `esc` (TDZ-Fehler).
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
Synthetische Daten aus `demo.py` (`generate`, `generate(anonymous=True)` ohne Koordinaten, `generate_mono` mit Filtern).
Plate-Solving-Tests nutzen eine ASTAP-Attrappe (Python-Skript, schreibt echtes `.ini`-Format) über `SolverConfig(cmd_prefix=[...])` – kein echtes ASTAP nötig.
**Oberfläche** (`test_ui_layout.py`, seit 31.10.2026): echter Chromium über Playwright (nur in der `.venv`, nicht in `requirements.txt`;
fehlt es, wird übersprungen – `pip install playwright && python -m playwright install chromium`). Alle Ansichten mit Demodaten bei 375/790/1440 px:
nichts darf über seinen Kasten (nächster Vorfahr mit Rahmen/Hintergrund) oder die Seite ragen, keine JS-Fehler; Schalterzeilen müssen per Klick umschalten.
Neue Ansichten in `ROUTES` eintragen. Achtung: globales `td { white-space: nowrap }` – Namensspalten in schmalen Tabellen brauchen `white-space: normal`.

## Stolperfallen (Windows-Umgebung des Nutzers)
- Shell ist Git-Bash: In Heredocs/`python -c` werden Backslashes umgedeutet (`\a` wurde zu BEL). Windows-Pfade in Skripten → Skript per Datei schreiben, raw strings.
- Port 8765 belegt ein anderes Programm des Nutzers; Vorschau-Server landen auf 8766/8767.
- Python-Dateien explizit mit `encoding="utf-8"` schreiben (Standard ist cp1252).
- ASTAP beim Nutzer: alte CLI (2022) in `C:\Program Files\astap` (nicht beschreibbar, hat `deep_sky.csv`), aktuelle `astap_cli.exe` + D05 in `%LOCALAPPDATA%\astap`.

## Echte Daten des Nutzers / laufende Instanz
- **Arbeitsinstanz:** Datenordner `astroflow/data/` (seit 2026-10-27; vorher Temp – umgezogen, DB-Pfade umgeschrieben, Sicherung `data/astroflow.db.vor-umzug`).
  Start über `.claude/launch.json` im Elternordner (`Claude Code/.claude`), Konfiguration „astroflow“, Port 8766, `--data …/astroflow/data`.
  Testinstanz: Konfiguration „astroflow-test“ (Port 8768, Datenordner im Scratchpad, `SKYTRACE_HOME` = `AstroAnalyse/` nur lesend ansehen – keine Analysen starten).
- SkyTrace-Daten des Nutzers: `C:\Users\nutzer\Documents\Claude Code\AstroAnalyse` (Ergebnisse, Katalog-Cache, Standort, `.venv` mit allen Engine-Paketen,
  ASTAP unter `tools/`). Das alte SkyTrace läuft dort weiter auf Port 8765 – nicht gleichzeitig Analysen aus beiden Oberflächen starten.
  **Nie Testdaten in diese Instanz schreiben** – Experimente in eigenem Datenordner (z. B. Scratchpad) mit kopierten Frames.
- Projekte: NGC 1579 (422 Stellina-Frames, 404 gestackt, per ASTAP gelöst), M3, M74, IC 405, M39 – Rohdaten nach dem Stacking gelöscht,
  also nur noch Nachstacken neuer Nächte und Neu-Entwickeln möglich.
- `data_real/` (im Projekt): ältere Stellina-M57-Session (Stack + Ergebnisse); `reproc.py` = Ad-hoc-Skript zum Nachbearbeiten.
- Datenordner beim Verschieben: Pfade stehen absolut in `frames.path`, `frames.info.path`, `results.files`, `solves.key` → alle umschreiben.
  Frame-IDs hängen am Pfad relativ zum Eingangsordner – bleibt die Struktur unter `input/` gleich, werden Dateien nicht neu eingelesen.

## Grenzen / mögliche nächste Schritte
Keine Mosaike (Stackfläche = Referenzbild), kein echtes Drizzle (2× interpoliert), keine Komet-/Planeten-/Mond-Modi, kleiner eingebauter Katalog
(großer nur mit ASTAP-`deep_sky.csv`), Dockerfile (ASTAP-Download) noch nicht real gebaut/getestet, Satellitenspur-Erkennung unzuverlässig,
GraXpert/StarNet nicht im Docker-Image, keine Verzerrungskorrektur, keine GPU, waagerechte Hintergrundstreifen bei NGC 1579 (Stack).
