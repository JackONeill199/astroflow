# Modul Bilder – Stacken (Schritte 1–6)

> Ausgelagert aus `AGENTS.md` am 09.11.2026, Text unverändert übernommen. Regeln, die beim Ändern von Code gelten,
> stehen zusätzlich in Kurzform in `AGENTS.md` → „Wenn du … änderst“. Neue Befunde hier ergänzen, nicht in `AGENTS.md`.

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
   Rauscheinheiten), `trails`/`trail_lines` (Satellitenspuren, s. „Satellitenspuren“ unten) → `frames.metrics`. `snr`/`grad` fließen in den Score, wenn alle Frames sie haben (ältere Analysen nicht).
   Spuren werden **vor** der Sternerkennung durch den Hintergrund ersetzt (sonst Spurpunkte als Sterne → FWHM hoch → Aufnahme verworfen). Nur neue Frames – oder solche
   mit `metrics.mv < quality.METRICS_VERSION` (dann auch automatisch Neu-Stacken). **Bei jeder Änderung an Analyse/Kosmetik `METRICS_VERSION` erhöhen.**
   Sternerkennung verwirft Einzelpixel-Spitzen (Nachbarn < 12 % des Peaks). `n_stars` ist bei `quality.MAX_STARS` (150) gedeckelt – das
   Diagramm „Qualität je Aufnahme“ zeigt dann den Himmelshintergrund, die Tabelle „150+“ (`/frames` liefert `star_cap`).
   **Vorschaubild je Aufnahme** (`frame_preview.py`, seit 10.11.2026): in derselben Analyse aus dem kalibrierten Rohbild (vor dem Löschen der Uploads),
   Bayer als Superpixel, Blockmittel auf ≤ `MAX_EDGE` 480 px, Hintergrund neutral, `auto_stretch`, JPEG q65 (~5–40 KB) unter
   `work/frame_previews/<frame-id>.jpg`. `GET /frames/{id}/preview` holt fehlende aus der noch vorhandenen Rohdatei nach (ohne Kalibrierung,
   ohne Neu-Analyse), sonst 404 → Platzhalter. `GET /frames/{id}` = Popup-Daten (Messwerte, Grund, 100 hellste Sterne, `shape`).
   Aus mit `ASTROFLOW_FRAME_PREVIEWS=0`. Frontend: `frameThumb`/`framePopup` in `stacking.js`, Stile `frames.css`. Test `test_frame_preview.py`.
3. **Auswahl**: Ausreißer je **Filter-Band** verwerfen (Wolken, Unschärfe, Nachführfehler); manuelle Ein-/Ausschlüsse (`frames.manual` ±1) gewinnen.
   Verzogene Sterne zusätzlich absolut (`quality.ECC_MAX` 0,8 ≈ 1,7:1), aber höchstens `ECC_MAX_REJECT` = ⅓ der Frames – rein relativ
   ließ M57 (Wind/Nachführung, 89 von 133 Frames ≥ 1,5:1) alles durch; jetzt 91 Frames, Stack 1,50 → 1,39:1 (Stellina 1,36).
   **Sternschweif (Ruck der Montierung)**: runder Kern + schwacher gleichgerichteter Schweif entgeht `ecc` (Momente sehen nur den Kern).
   `core/star_tails.tail_profile` stapelt bis zu 40 subpixelgenau zentrierte, auf den Kernfluss normierte Sterne zum mittleren Profil
   und misst den einseitigen Fluss (Richtung minus Gegenrichtung) plus Belege je Stern; `has_tail` = Anteil ≥ 3,5 %, Länge ≥ 3 FWHM,
   ≥ 50 % der Sterne, ≥ 3 Bildviertel. Felder `tail_profile_*` in `frames.metrics` sind eine **Zusatzmetrik ohne `METRICS_VERSION`**
   (nur neu analysierte Frames; `select_frames` nutzt sie nur, wenn alle Frames des Bands sie haben), Grenze höchstens ⅓ der Frames
   wie bei `ecc`. Gemessen an einer Stellina-Serie (931 × 10 s): gute Bilder 0,4–2,2 %, Schweifbilder 4–29 %. Test `test_star_tails.py`.
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
   Deshalb werden Satellitenspuren seit 0.49.0 gezielt maskiert (folgender Punkt).
   **Satellitenspuren** (`trails.py`, seit 0.49.0, Auftrag des Masters nach StackingWizard-Vergleich): Befund vorher (Demo): `detect_trails`
   (Labeling + Streckung, nur eine Zahl) fand roh 0/2 Spuren; Spurpunkte zählten als Sterne (88 statt ~60) → beide Spur-Frames „unscharf“
   verworfen bzw. manuell eingeschlossen Gewicht 0,1; bei vollem Gewicht ließ Clipping allein die Spur stehen (Stapel à 3, 30 Frames
   +10/+17 ADU; ein Stapel à 24 +7 ADU; 8 Frames +42 ADU; 4 Frames +71 ADU, Stackrauschen 1–4 ADU). SkyTrace (`line_detection`) braucht
   OpenCV/Engine und läuft erst nach dem Stacken → nicht als Quelle genutzt.
   **Erkennung `find`** (je Aufnahme in `analyze_frame`, nur numpy/scipy, ~0,1–0,5 s): Arbeitsbild ≤ 800 px breit, Hintergrund (64-px-Kacheln) ab,
   Quellmaske `_sources`: flächig = Öffnung von z > 3 mit 17-px-Scheibe ohne langgestreckte Reste (helle breite Spur); Sterne = Maxima > 7σ,
   deren Ring (4/8/14 px, auf σ 1 geglättet – eine 1-px-Spur verfehlten die Ringproben sonst) **nicht** in zwei gegenüberliegenden Richtungen
   hell ist, Radius aus der Helligkeit (≤ 12 px; nicht „ganze Komponente“ – in dichten Feldern verbindet eine Spur viele Sterne). Hough per
   numpy (0,25°, 1 px) über z > 1,5 ohne Quellen, Linie per Hauptachse der nahen Stimmen verfeinert, Prüfung wie SkyTrace: Kontrast in
   24-px-Abschnitten (gekappt ±4; Seiten = Median ohne Proben auf Sternen, schmal = gegen die dunklere Seite; zweite Skala für helle breite
   Spuren nur bei Grat > 8σ), Abschnitt ≥ 4 (verdeckte dürfen innen liegen), Score wie SkyTrace (10,5 bzw. kürzer weniger); Enden über
   verdeckte/schwache Abschnitte bis zum Rand verlängert; Halbbreite aus dem Querprofil (> 1σ je Pixel); angenommene Spur danach aus der
   Prüfmaske (keine Doppeltreffer). > 15 % Fläche → nichts. Ergebnis `metrics.trail_lines` = [x1, y1, x2, y2, Halbbreite, Kontrast] in
   Vollauflösungs-Pixeln, `trails` = Anzahl. **Kein `METRICS_VERSION`-Schritt** (Entscheidung Nutzer): ältere Frames ohne `trail_lines`
   werden in `_stack.work` im ohnehin geladenen Bild nachgesucht; ihre Metriken/Gewichte bleiben wie sie sind.
   Synthetisch (σ 14, 540×720): 0 Fehlalarme bei 300–30 000 Sternen, Nebel, 1040×1536; gefunden ab ~30 ADU (1 px breit) über die ganze
   Länge, hell (3000), breit (σ 4 px), zwei gekreuzte; nicht: ≤ 15 ADU (stört auch im Stack nicht), kurze Spuren ~120 px (bleiben dem Clipping).
   **Maske beim Stacken** (`Pipeline._trail_masks`, im Hauptthread je Stapel): Linien per `transform` (M + `scale`) ins Referenzraster; mit
   Verzerrungskorrektur (Polynom `P` der Aufnahme) rastert `trails.band(..., poly=P, ref_lines=…)` stattdessen in **Aufnahme**-Pixeln:
   jedes Ausgabepixel im Umfeld der groben Lage (über M, +8 px) wird mit demselben Polynom (`registration.poly_out`/`_terms`) wie im Warp
   in die Aufnahme abgebildet und dort gegen die Spur geprüft – Maske und Bild liegen gleich (Saum dann FWHM + 1 in Aufnahme-Pixeln). `confirm`:
   Band muss gegen den bisherigen Stack bzw. den Median der übrigen Aufnahmen des Stapels signifikant **und** zu ≥ 50 % seines eigenen
   Signals neu sein – ein statischer gerader Nebelfaden wird erkannt, aber nie maskiert (ohne Anteilsregel 9 von 24 Frames, weil ein dünner
   Faden in scharfen Aufnahmen spitzer ist). Ohne Vergleich (erste Aufnahme allein) wird maskiert. Band = Halbbreite + FWHM + 1 px,
   `V[b] &= ~band` → CPU und GPU (`clip_stats/clip_add`) beachten es ohne Änderung. `StackState.swm` (H, W) = Gewicht der ausgeblendeten
   Pixel, nur für die Abdeckung (`result`: `sw.min + swm`, gespeichert als `swm.npy` nur wenn ≠ 0) – sonst sähe ein Band in kleinen Stacks
   wie ein ungedeckter Rand aus (GraXpert füllt Abdeckung ≤ 0,5 mit Himmel, `stackfix`/`coverage_box` ebenso). `meta.trails`
   {frames, lines, pixels, kept} (kept = erkannt, aber nicht neu) → `results.stats.trails` (alle Bänder); der Kometen-Stack übernimmt es nicht.
   Gemessen (Demo, gleiche Referenz, Differenz zum Stack ohne Spur entlang der Spur): 30 Frames à 3 +0,08/−0,10, 8 Frames −0,14, 4 Frames
   +0,27 ADU; beide Spur-Frames bleiben ohne manuelles Einschließen im Stack. Tutor-Hinweis bewusst nicht (Nutzer: bringt nichts).
   Tests `test_trails.py`. Messskripte (Demo mit/ohne Spur, gleiche mtime → gleiche Frame-IDs/Referenz) lagen im Scratchpad der Sitzung.
   **Verzerrungskorrektur** (seit 08.11.2026, Branch `feature/verzerrung`; Anlass: ein Vergleichsprogramm passte bei Stellina-Aufnahmen
   ein Polynom 2. Ordnung über ~360 Sternpaare an, Restfehler 0,50 → 0,37 px): Die Stellina zentriert alle 1–3 min nach, das Feld wandert
   über die Optik, die Verzeichnung bleibt am Sensor – die Ähnlichkeit (Verschieben/Drehen/Maßstab) lässt am Rand Restfehler.
   **Standard an** (Nutzerentscheidung 08.11.2026): global `settings.json` → `distortion.mode` auto|off (`registration.DISTORTION`, PERSIST in
   `api.py`, `GET|POST /api/stacking/distortion`, UI Einstellungen → Bilder; ohne gespeicherten Wert `ASTROFLOW_DISTORTION`, sonst auto),
   je Objekt Projektoption `distortion` global (Standard) | auto | off – `registration.distortion_mode(option)` = Objekt vor global.
   Neue Stacks merken den Modus (`meta.distortion`, `Pipeline.distortion_state` → `/projects/{id}` `distortion {global, stack}`); weicht
   der wirksame Modus ab, zeigt der Reiter Optionen den Hinweis „Komplett neu stacken“, eine Änderung dort verlangt Neu-Stacken. Ablauf in `_stack.work`: `find_transform` wie bisher (100 gespeicherte Sterne, Dreiecke) → `quality.dense_stars`
   (gleiche Erkennung wie die Analyse, bis `DENSE_STARS` 400 Sterne, ~130 ms bei 3072×2080) → `registration.fit_distortion`: alle Sterne
   über M zuordnen (`POLY_TOL` 2,5 px, eindeutig), Polynom Referenz→Aufnahme in u, v (Mitte, halbe Diagonale), Ausreißer 3σ am Polynom.
   Übernommen nur mit ≥ 30 Paaren, ≥ 5 je Viertel und ≥ 2 je Eckfeld (3×3) **des Überlappfelds**, größter Korrektur ≤ 4 px und
   Restfehler aus 5-facher **Kreuzvalidierung** −10 % und −0,02 px (sonst gewinnt das Polynom mit 12 statt 4 Parametern schon am
   Messrauschen); sonst Ähnlichkeit mit Grund (`why`). Referenzliste `meta.ref_dense` (neue Stacks aus dem Referenzbild; ältere: Referenzbild
   nachmessen, ohne Rohbild aus dem Stack selbst, `Pipeline._dense_ref`; weitere Filter übernehmen sie). Je Aufnahme `meta.reg[fid]` =
   `{n, r0, r1, cv0, cv1, quad, corner, k, poly, why}` in `state.json` (nur mit Korrektur); `registration.summary` → `results.stats.distortion`
   (`frames, poly, pairs, rms_before, rms_after` = wirksamer Restfehler, `max_corr, why`), Details-Liste im Bild-Tab. `warp(..., poly=P)`:
   CPU `spline_filter` (Spiegelrand) + `map_coordinates` in Streifen `STRIP_ROWS` (für eine reine Ähnlichkeit bitgleich zum affinen Weg), GPU
   Kernel `warp_poly` (gemeinsame `sample`-Funktion mit `warp`, Polynom in Ausgabepixeln über `poly_out`). Kometen-Stack: Polynom aus der
   Sternregistrierung, Kernverschiebung über `shift_poly`; die Bahn-Stichproben (`_comet_track`) richtet dasselbe Polynom aus
   (`binned_poly` auf das gebinnte Raster, Review PR #9 – sonst enthielte die Bahn Registrierungsreste, die der Stack nicht mehr hat). `frame_links`/Gerätediagnose/Asteroiden bleiben bei der Ähnlichkeit (unverändert).
   Kein automatisches Neu-Stacken, inkrementell gemischt möglich (alte Aufnahmen affin, neue mit Polynom).
   Gemessen (synthetisch, `demo.generate_distortion`, 1536×1024, Ecke ~5 px Verzeichnung, Feld ±120 px, 18 Aufnahmen): Polynom bei 14 von 17,
   Restfehler (KV) 0,42 → 0,21 px, ~285 Paare; Stack Exzentrizität Ecken 0,33 → 0,23 (Mitte 0,25), FWHM Ecken 3,68 → 3,62 (Mitte 3,63);
   ohne Verzeichnung 0 von 16 übernommen, Stack identisch. 3072×2080 je Aufnahme: Warp CPU 1,13 → 1,19 s, GPU 72 → 156 ms (Maske auf der
   CPU), Fit 1 ms. Tests `test_distortion.py`, `test_gpu.py::test_warp_poly_matches_cpu`. **Offen:** Messung an echten Stellina-Daten.
   **Mosaik** (`mosaic.py`, seit 11.11.2026, Anlass Stellina-Rosette: Mosaikmodus fährt ein Raster ~415 px ab, Stack war auf 38 % der
   Referenzfläche zugeschnitten). Projektoption `mosaic` auto|on|off (Standard auto; Kometenmodus: auto = aus), `mosaic_crop` wide|compact
   (nur Entwicklung). Ablauf beim **Neuaufbau** in `_stack` (`_mosaic_plan`): Transformationen aller Aufnahmen aus den gespeicherten
   Sternlisten (`_transforms`) → `mosaic.plan`: Mosaik, wenn die Bildmitten robust (5.–95. Perzentil) über > `DETECT` 25 % der Bildbreite/-höhe
   streuen (Drift, Dithering, Feldrotation bleiben darunter) und die Leinwand ≥ `MIN_GAIN` 1,15× größer wäre. Leinwand = Rechteck um
   alle Rasterzellen, die ≥ 2 % der Aufnahmen abdecken, gedeckelt auf `MAX_AREA` 6× Referenzfläche bzw. `MAX_PIXELS` 48 Mio. Ausgabepixel
   (Rand mit der geringsten Abdeckung fällt zuerst). Die Referenz rückt auf die Aufnahme nahe der Mitte aller Aufnahmen (`center_ref`,
   unter den besten 50 %) – sonst teilen die äußeren Kacheln kaum Sterne mit ihr. `state.json`: `mosaic` {spread, area, frames, mode},
   `origin` [x, y] (Leinwandecke in Referenzpixeln), `canvas` [H, W] (nativ), `bg_match` (Zähler). Ohne diese Schlüssel = gewöhnlicher
   Stack (`mosaic.origin/canvas` liefern dann 0 bzw. das Referenzbild; weitere Filter übernehmen die Schlüssel über `ref_meta`).
   **Warp:** `M = shift(origin) @ M` bzw. `shift_poly(P, −origin)`; nur das Rechteck der Aufnahme wird gewarpt (`footprint`) und in die
   Leinwand eingesetzt. **Reihenfolge** beim Neuaufbau: von der Mitte nach außen (`order`), Ergänzen später wie gehabt.
   **Nähte:** (1) Hintergrund nicht über den eigenen Median (hängt am Nebelanteil der Kachel), sondern `match_background`: je Kanal
   robuste Ebene der Differenz zum Ziel im Überlapp (Versatz, wenn der Überlapp < 40 % der Aufnahme überspannt); Ziel = bisheriger
   Stack (verkleinert, zählt ×3) + schon angeglichene Aufnahmen des Stapels. (2) Weiche Randgewichte `feather` (Smoothstep über 6 % der
   kürzeren Bildseite, Rand 0,05) als Pixelgewicht `F` in `StackState.add_batch` (dann CPU statt GPU-Akkumulation).
   **Leistung:** mit `F` rechnet `add_batch` nur im Rechteck, das der Stapel trifft; `_masked_median` nimmt bei > 20 % teilbedeckter
   Pixel Sortieren + Greifen statt `nanmedian` (dort ~10× langsamer; gewöhnliche Stacks unverändert auf dem alten Weg). Ohne beides
   lief die Rosette mit ~0,13 Aufnahmen/s, mit beidem ~1,2/s.
   **Entwicklung:** `mosaic.effective_cov` wählt die Mindesttiefe t aus 0,5/0,4/0,3/0,22/0,15 (Fläche × √t maximal, `compact` = 0,5)
   und bildet die Abdeckung so ab, dass t bei 0,5 liegt – Zuschnitt (`coverage_box`), GraXpert-Lückenfüllung, `stackfix`, Gaia-Farbkalibrierung
   (cov > 0,9) arbeiten mit ihren gewohnten Schwellen. `params._mosaic` schaltet `remove_edge_glow` ab (Mosaikränder sind keine
   Sensorränder; das Glühen ist je Aufnahme schon abgezogen – sonst helle Bänder bei 60 % des Randbands). `results.stats.mosaic`
   (Fläche, Leinwand, Mindesttiefe, Zuschnittsart), Details im Bild-Tab. `skyquality.frame_links` enthält den Ursprung (Matrix
   Aufnahme → Stack-Raster: Himmelsqualität, Asteroiden), `_dense_ref` rechnet Stack-Sterne zurück. Plate-Solving löst den Stack selbst
   (Schlüssel `ref_fid|HxW`). Kometenmodus mit einem Mosaik-Stack: Fehler in `comet.json` („braucht einen gewöhnlichen Stack“).
   **Grenzen:** Aufnahmen brauchen gemeinsame Sterne mit der Referenz (Raster bis ~1 Bildbreite Abstand zur Mitte); die Leinwand steht nach
   dem ersten Stack fest (später weiter außen liegende Aufnahmen werden abgeschnitten, „Komplett neu stacken“ plant neu); ein bestehender
   Stack wird erst durch „Komplett neu stacken“ zum Mosaik. Tests `test_mosaic.py` (`demo.generate_mosaic`: 2×2-Raster, Nebel außermittig,
   Himmel je Aufnahme verschieden).
6. **Kanäle** (`channels.py`): L/R/G/B/Ha/OIII/SII → RGB, LRGB, RGB+Hα, HOO, SHO oder Mono (Option `palette`).

## Weitere Bausteine des Stackens (Sensorglühen, Stacking-Stufen, Live-Ansicht)
Stand im alten Text unter Schritt 7 eingeordnet; gehören technisch zu `Pipeline.stack()`.

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
   **„Das Bild entsteht aus dem Rauschen“** (seit 0.47.0, Nutzerwunsch; vorher verworfen: Sternmuster-Overlays und 3D-Ebenen – zu dominant):
   Beim Stacken echte Zwischenstände des Stacks nach `live.SCHEDULE` (1, 2, 3, 4, 5, 6, 8, 10, 12, 15, 19, 24, 30, dann je ×1,5) plus
   höchstens alle `INTERVAL` 4 s am Stapelende. Innerhalb eines Stapels (nur bis `EARLY` 80 Aufnahmen) über `mini_inputs` (verkleinerte
   Kopien jedes n-ten Pixels: Stack-Summen vor dem Stapel + Aufnahmen, im Ablauf ~50 ms je Stapel bei 3072×2080) und `mini_snapshots`
   = derselbe `StackState.add_batch` mit `prefixes` auf den Kopien (gleiches Clipping je Pixel; Test: gleich wie der volle Stand an diesen
   Pixeln). Am Stapelende `stack_sample` (~6 ms). Rechnen und Rendern im Hintergrund-Thread `LiveRun.later` (einer, Reihenfolge bleibt;
   Mini-Stack ~0,36 s für 10 Stände, Rendern ~27 ms je Stand) → Stacken praktisch nicht langsamer (Bericht PR #8: < 1 % im Ablauf).
   `StackState._add_rows` rechnet die Zwischenstände (auch die Stacking-Stufen) seit 0.47.0 über laufende Summen statt je k neu – der Stack
   selbst ist unverändert. **Feste Streckung** je Lauf und Kanal (`fixed_stretch` aus dem ersten Stand: Schwarzpunkt Himmel − 1σ je Kanal,
   gemeinsamer Weißpunkt am 99,95-%-Wert, asinh mit Himmel bei ~0,13, als Tabelle) – sonst würde jede Auto-Streckung das Rauschen wieder gleich
   hell machen. `noise_of` = robuste Streuung benachbarter Pixel der Luminanz; `frames[].noise` relativ zum ersten Stand (sinkt ~1/√n).
   `state.json` → `frames` (letzte `FRAMES_KEEP` 12, Dateien `<lauf>-f<n>.jpg`, `/live/{name}` erlaubt `\d+-f?\d+\.jpg`; `picture()` räumt
   nur `<lauf>-<n>.jpg` auf). Browser: neue Stände werden über das Abrufintervall verteilt nacheinander überblendet (Takt 2,5 s / Anzahl,
   350–1500 ms, Überblendung ≤ 800 ms über `--sl-fade`; neu geöffnet nur der jüngste, höchstens 8 in der Warteschlange), Bildunterschrift
   „n Aufnahmen · Rauschen 37 % · Belichtung“ (Anteil des Anfangsrauschens, Start = 100 %); sobald die Endbearbeitung eine Vorschau (`preview`) liefert, hat diese Vorrang.
   Reduzierte Bewegung → harte Wechsel. Kein Umschalter. Tests `test_live_rauschen.py`, UI `test_live_stack_emerges_from_noise`.
   UI: Regler unter dem Viewer, Standard = fertiges Bild, `viewer.setSrc` tauscht das Bild ohne Zoomverlust.
