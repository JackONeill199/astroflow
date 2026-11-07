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
- Nicht übernommen: Tk-Desktop-Oberfläche (`desktop_app.py`, `catalog_dialog.py`, `location_picker.py`), Starter (`*.cmd/*.vbs`),
  Web-Prüfungen gegen den alten Server (`check_web.py`, `check_lan.py`, `check_preview_race.py`). Die übrigen `check_*.py` liegen in `engine/`
  und laufen mit gesetztem `SKYTRACE_HOME` (manche brauchen echte Stellina-Daten: Ordner per `SKYTRACE_SAMPLES`).
- Abhängigkeiten der Engine: `requirements.txt` (Lock aus SkyTrace). Docker: `--build-arg SKYTRACE=1`.

## Laufdaten fürs Handy klein halten (05.11.2026, 0.31.1)
Befund: Auf dem Handy (Galaxy S24, Brave, Heimnetz) luden Lauf-Ansicht und Medien endlos. Ursache war nicht die Medienauslieferung,
denn Range/206, H.264 und relative URLs waren in Ordnung. Ursache war `/api/skytrace/run`: `video_overlays.timeline` schrieb in jedes
Videobild alle Satellitenbahnen fertig umgerechnet (126 Bilder × 32 Bahnen = 25 MB JSON). Jetzt liegen die Bahnen einmal in
`overlay_timeline.satellite_paths` (`start` = erstes Trefferbild), und jedes Bild hat nur `index` und `reference`
(Registrierungsmatrix, auf 7 Stellen gerundet). Umrechnung und Bildfeldtest übernimmt `evidence.satellitesAt`
(Gegenstück in Python: `video_overlays.satellites_at`, für `check_video_overlays.py`). Ältere Antworten mit `frames[].satellites`
gehen weiter. Dazu komprimiert der Kern JSON/Text per `core/gzip.py` (ab 2 KB, nur Text/JSON; Medien und 206 bleiben unberührt).
Ergebnis: 25 MB → 1,4 MB (0,6 MB übertragen). Neue Felder je Videobild nur, wenn sie nicht mit Bilder × Kandidaten wachsen.
Nachstellen: Playwright `Pixel 7` + CDP `Network.emulateNetworkConditions` über die LAN-Adresse. Tests:
`test_skytrace.py::test_lauf_kompakt_und_medien_uebers_heimnetz`, `test_skytrace_video_ui.py` (Satellitenbahn im Overlay).

## Analysen löschen (29.10.2026, Nutzerwunsch)
`DELETE /api/skytrace/run?id=<Lauf>` → `Controller.delete_run`: nur Ordner unter `Ergebnisse/` mit Laufdateien (`videos.json`,
`bericht.html` oder `messwerte.jsonl`), nicht der gerade laufende; Windows-Sperren (Videowiedergabe) mit einem Wiederholversuch.
Originalaufnahmen liegen nie im Ergebnisordner. UI: Papierkorb auf den Karten (bei Hover), in der Lauf-Ansicht und in der Befehlspalette,
jeweils mit Bestätigungsdialog.

## Satellitenhelligkeit und Satelliten je Stunde (`satellites.py`, `static/skytrace-satelliten.js`, 0.39.0, Wunsch 18)
`GET /api/skytrace/statistics/satellites` → `Module.satellite_stats` (20 s gecacht) → `satellites.collect(stats.collect(...), zps, resolve_site)`. Läuft im
Server (nur numpy, kein Engine-Interpreter) auf den **gespeicherten** Läufen, ohne Neuanalyse; Antwort klein (Zellen je Ort/Jahr/Monat, ~3 KB für 200 Spuren).
- **Fluss:** `evidence.profile` des Kandidaten (Querprofil im Trefferbild, ADU nach 2×2-Mittelung, Seitenhintergrund abgezogen, Median entlang der
  Spur) – Summe über ±7 px × `length_pixels` × Binning² (`plausibility.original_size/analysis_size`) = Rohpixel-ADU der Spur; geteilt durch die
  Durchgangszeit `|passage_seconds[1] − [0]|` des Katalogtreffers = ADU/s (`phot_of`, `flux_rate`). Die Spurenden sind grob gerastert (Länge in 48-px-Schritten),
  Länge und Zeit kürzen sich aber weitgehend. Verworfen: kein Profil/Länge, Durchgangszeit < 0,02 s oder > 1,3 × Belichtung, Fluss ≤ 0.
  Nur Kandidaten, die `achievements.classify` als Satellit einordnet. Ältere Läufe ohne `evidence.profile` zählen für die Rate, haben keine mag.
- **Nullpunkt:** Dienst `night_zero_points` des Bilder-Moduls (`{nights: {"<Nacht>|<INSTRUME>|<Gain>": zp}, devices: {...}}`, Median der `sky_nights.zp`
  mit `zp_source == stack`, mag für 1 ADU/s über der Atmosphäre). Rangfolge `pick_zp`: Nacht → Gerät → `NOMINAL_ZP` (Stellina 22,0, Median der Gaia-Nullpunkte aller
  Nächte) als „Schätzung“. Gerät/Nacht aus `frames.jsonl` (`stats._frames_info`: `INSTRUME`, `GAIN` als Float, Belichtung, Zeiten). Ohne Dienst/Gerät keine mag.
  mag = ZP − `K_EXT` (0,20) × Luftmasse(Bildfeldhöhe aus `world_path`, Ort, Zeit) − 2,5·lg(ADU/s): scheinbare Helligkeit im Luminanzband, **keine** Normhelligkeit
  (keine Entfernungs-/Phasenreduktion), Unsicherheit grob 0,3 (Nacht) – 0,6 mag (Schätzung). An den echten Läufen (203 Spuren): Starlink-Median ≈ 7 mag (4,2–8,4),
  Raketenstufen bis 3,5 mag, GLONASS ≈ 11 mag – passt zur Erwartung.
- **Rate:** je Beobachtung (neueste vollständige Analyse, wie die Statistik) Σ der tatsächlichen Belichtungen je angenommener Aufnahme (`frames.jsonl`, `_exps` parallel zu `_times`; Rückfall Median) – Zellen je Standort **und Gerät** (`INSTRUME`, Schlüssel `<Ort>|<Gerät>`) bei Sonnenhöhe < −12° (`SUN_NIGHT`,
  Näherung ~0,05°), Ort über `observation_settings.resolve_site` (Ortsliste der Nacht), ohne Ort „Standort unbekannt“ ohne Nachtfilter. Spuren in der Dämmerung zählen nicht. Zeiträume
  unter 0,5 h zeigt die Oberfläche nicht als Rate. Die Rate hängt vom Bildfeld ab (Stellina 1,06° × 0,72°) und gilt je Gerät.
- `stats.collect` liefert zusätzlich `_full` (interne Sitzungen mit `_times/_exp/_dev/_sat`); `/statistics` entfernt alle `_`-Schlüssel, damit die Antwort schlank bleibt.
- UI: `window.stSatellites(host)` aus `statsView` (Abschnitt unter der Statistik): Balken je Jahr/Monat (Starlink/OneWeb/übrige), Standort-Auswahl, Histogramm, hellste Spur.
- Tests: `tests/test_skytrace_satelliten.py` (Astronomie, Fluss/mag, Nullpunkt-Rangfolge, synthetische Läufe mit erfundenem Ort), `test_skyquality.py::test_dienst_nullpunkte_je_nacht`.

## Statistik (`stats.py`, 01.11.2026, Nutzerwunsch)
`GET /api/skytrace/statistics` → `stats.collect`: alle Läufe mit `videos.json` (laufende Analyse ausgenommen), je Beobachtung
(`session` + Name der ersten Aufnahme aus `frames.jsonl`/`ausgeschlossen.jsonl`) nur die neueste vollständige Analyse (sonst die
neueste; `repeated` = nicht gezählte ältere). Die erste Aufnahme trennt automatische Stapel derselben Nacht (gleicher Ordnername,
aber nur neue Aufnahmen – Codex-Review PR #15) von echten Wiederholungen. Kandidaten kommen schlank
zurück (nur Felder für `evidence.js classification`, Zeit, Geschwindigkeit, Katalogstatus) – **Einordnung und Zählung macht die
Oberfläche** (`statsView` in `skytrace.js`), damit Zahlen und Kandidatenfilter übereinstimmen. Nacht = Datum im Ordnernamen,
sonst früheste Kandidatenzeit − 12 h. Cache je Lauf nach Dateizeiten (Arbeitsinstanz: 119 Läufe, 0,4 s erstmals, danach 0,07 s).
Route `#/skytrace/statistik` steht vor der Lauf-Route. Test: `test_skytrace.py`, Ansicht in `test_ui_layout.py`.

## Erfolge (`achievements.py`, `static/skytrace-erfolge.js|css`, 03.11.2026, Nutzerwunsch „auch sarkastische, die in der Astrofotoszene beliebt sind“)
`GET /api/skytrace/achievements` → `Achievements.overview`, `POST …/achievements/seen`. Liste `ACHIEVEMENTS` (id, Titel, Beschreibung, Symbol, Gruppe,
Kennzahl, Ziel, Spruch) – IDs bestehender Erfolge nie ändern. Rechnung `compute(stats.collect(...))`: Beobachtungen (neueste vollständige Analyse je
Beobachtung, also dieselbe Datenbasis wie die Statistik) werden in Reihenfolge der Lauf-ID durchlaufen, laufende Kennzahlen (Summen, Mengen, Maxima je
Beobachtung); der Erfolg bekommt das Datum des Laufs, in dem das Ziel erstmals erreicht war (**rückwirkend datiert**). Einordnung in Python nach dem
Vorbild von `evidence.js classification` (`classify`): Satellit nur bei Kataloghinweis `compatible`; Starlink/ISS/R/B/DEB aus Name, NORAD-Nummer und
SATCAT-Objektart; „Meteorvorschlag“ = Kandidat mit `kind == meteor` (kein Modellhinweis – im Teleskopbild nur Hinweis); „Nachtschicht“ = Ortszeit des
Rechners 3–6 Uhr; „Saubere Nacht“ = ≥ 100 Aufnahmen ohne Kandidat; Starlink-Zug = Treffer innerhalb 300 s. Nicht umgesetzt (Daten fehlen):
„Spur quer durchs Bildzentrum“ (Kandidaten speichern keine Bildgeometrie in Bildpixeln). Schwellen an den echten Läufen des Nutzers geeicht
(141 Beobachtungen: 164 Satelliten, 84 Starlink, max. 8 je Beobachtung, Zug ≤ 4, 25 Raketenstufen, 10 Trümmerteile → 42 von 60 freigeschaltet).
Zustand `<SKYTRACE_HOME>/erfolge.json` (`unlocked{id: {unlocked, seen}}`): einmal vergeben bleibt vergeben (auch wenn ein Lauf gelöscht wird),
das Datum ändert sich nie. Erste Datei = alles rückwirkend und „gesehen“ (keine Feier-Flut); später neu erreichte sind ungesehen (`status.achievements_new`
→ Hinweis über `toast`, Karte mit „Neu“ und Animation, nach 6 s bzw. Verlassen der Seite als gesehen markiert). Ein Hintergrund-Thread (`Module._achievements_loop`, unabhängig vom Browser) stellt beim Start den Ausgangsstand her und stößt die Prüfung an,
wenn eine Analyse endet. Frontend hängt sich per `AF.modules.skytrace` ein (Route, Navigation, Palette, Status) – `skytrace.js` blieb unverändert.
Optionaler Dienst `ctx.services["skytrace_achievements"]` (= `overview`) für andere Module; Sage nutzt ihn bisher nicht. Ton: freundlich-ironisch,
keine Firmen-/Personennamen als Zielscheibe (Betreibernamen nur als Fakt aus dem Katalogtreffer). Test: `tests/test_skytrace_achievements.py`.

## Vollbild-Zeitreihe und Video (03.11.2026)
Dropdown „Analyse“: `POST /reanalyse` (`check:true` prüft nur) liest über `reanalysis.prepare` alle akzeptierten und
ausgeschlossenen Originale aus den Manifesten. Keine Teilausführung bei fehlenden oder mehrdeutigen Dateien; optionaler
Ersatzordner ordnet Dateinamen zu (automatisches fünfstelliges Präfix darf fehlen). Neuer Lauf erhält `eingang.json`;
`analyse --input-manifest` übergibt exakt diese Bildgruppen und Bildraten an `sequence`, ohne weitere Dateien aus dem
Ordner einzulesen. Alte Ergebnisse und Formularvorgaben bleiben erhalten. „Zeitreihen aktualisieren“ bleibt `/refresh`.
`candidate_report` speichert unbeschriftete JPG-Vollbilder in Analyseauflösung je Originalnummer einmal unter
`<Beobachtung>/vollbilder/`; `frames[].full_image` enthält relativen Pfad und Maße. Ausschnitt-PNGs bleiben erhalten.
API ergänzt sichere URLs, übernimmt ältere `context_image`-Trefferbilder und liefert echte `video_seconds` aus
akzeptierten `frames.jsonl`-Einträgen (Cache nach Größe/Dateizeit). Niemals Originalnummer/fps rechnen: ausgelassene
Aufnahmen stehen nicht im Video. Ausgeschlossene Kontextbilder haben keinen Video-Sprung.
App: Ganzes Bild/Ausschnitt, Treffer-Markierungen, Sprung zur Videostelle; ältere Nachbarn ohne JPG aus dem sauberen
Video anzeigen. `AstroEvidence.frameImage` verschiebt Ausschnitt-Markierungen um `crop` in Vollbildkoordinaten und
wird auch im Bericht verwendet. SVG-Hintergrund bleibt `pointer-events:none`, nur Beschriftungen fangen Mausereignisse
ab; sonst verdeckt die Overlay-Fläche die nativen Video-Bedienelemente. Medienbeobachter beim Tabwechsel aufräumen.
Prüfungen: `test_skytrace_frames.py`, `test_skytrace_video_ui.py` (synthetisches 3-s-H.264-Video, auch Handybreite).

## Kurze helle Spuren (03.11.2026)
`line_detection.compact_trails` ergänzt nur bei registriertem Vergleich die bestehende Hough-Suche: native Auflösung,
beidseitig > 6× robustes Rauschen in Original und Differenz, schmale gerade Komponenten mit 12–90 px Länge, mindestens
4:1, keine Einzelpixel-Grate, lokale Struktur ≤ `CALM_LIMIT`, integrierter Kontrastwert ≥ 12. Lange Hough-Spuren und
ihre Schwellen bleiben unverändert; kompakte Fragmente bereits erkannter Spuren werden zusammengeführt. Anlass:
NGC 1499, kurze helle Spur im letzten Bild eines 61-Frame-Laufs (in Originalkopien geprüft). Keine Identitätsbehauptung.
`tests/test_skytrace_compact.py` läuft bei vorhandenem OpenCV mit pytest oder direkt im Engine-Interpreter.
`frame_quality.ensemble_star_tails` prüft zusätzlich bis zu 20 helle, isolierte Sternumgebungen (Radius 64 px):
Median der auf Kernhelligkeit normierten Profile, Richtungsprofile von 10–56 px mit seitlichem Hintergrund.
Verlangt Score ≥ 6, vier positive Radialabschnitte und deutliche Asymmetrie gegenüber der Gegenrichtung; zusätzlich
mindestens fünf individuell belegte Schweife (Score ≥ 5) in drei Quadranten und über >35 % beider Bildachsen.
So werden auch unterbrochene lange Schweife außerhalb der bisherigen Kernprüfung als globale Bewegung verworfen.
Test: `test_skytrace_field_motion.py`; scharfe Felder, mehrere echte Linien, symmetrische Beugung und lokale/ungeordnete
Schweife bleiben erhalten. Originalbefund: drei Fehlkandidaten in einem verwackelten NGC-1499-Bild.
Ersatzdateien bei `/reanalyse` müssen anhand FITS-DATE-OBS zur gespeicherten UTC passen: gleiche Dateinamen werden
in unterschiedlichen Nächten wiederverwendet. Ohne prüfbare Identität keine Zuordnung allein nach Namen.
**Mehrere gleichnamige Kandidaten (0.31.3, 05.11.2026):** Stellina-Unterordner `01-images-initial`, `02-images-adjust-focus` …
beginnen alle wieder bei `img-0001r.fits`, automatische Läufe tragen zusätzlich ein Präfix `00164_`. `reanalysis.prepare` indexiert den
Ersatzordner rekursiv (`os.walk`, nur gesuchte Namen, Obergrenze `MAX_ENTRIES` 500 000 → Meldung „engeren Ordner wählen“) und wählt
über `pick` unter gleichnamigen Dateien die mit passender `DATE-OBS` (±1 ms). `date_obs` liest nur die ersten Header-Blöcke (≤ 12 × 2880 Byte,
ohne astropy), Ergebnis je Pfad gemerkt. Die Prüfreihenfolge dient nur der Leistung: Ordner früherer Treffer, dann Pfade mit dem Nachtdatum
(Zeit −36…+12 h), dann der Rest – so reicht auch bei der ganzen Platte meist ein Header je Aufnahme. Genau ein Kandidat wird wie bisher streng geprüft
(Abweichung = Fehler „gehört nicht zur ursprünglichen Aufnahme“). Bei mehreren ohne Treffer: „Unter n gleichnamigen Dateien … gehört keine …“.
Der Start nutzt dieselbe `prepare`-Ausgabe; `eingang.json` enthält die aufgelösten Originalpfade (nicht die Kopien). Gemessen an
IC59 2022-08-06 (549 Aufnahmen, Lauf mit Präfix-Namen, Platte E:): Beobachtungsordner 7 s, ganze Stellina-Platte 14 s, 0 fehlend.
Identische Kopien derselben Aufnahme (gleiche Zeit) gelten als gleichwertig, die erste gewinnt. Tests: `test_skytrace_reanalysis.py`.
Für den Differenzvergleich hat die direkt gemessene Registrierung zum Nachbarbild Vorrang vor der Differenz zweier
globaler Referenzlösungen. Letztere können gegeneinander springen (bestätigt: lange blasse NGC-1499-Spur unten links,
Videobild 57). Globale Astrometrie für bewegte Punkte bleibt getrennt. Regression mit künstlich springenden Referenzfits.

## 3D-Ansicht (`orbit3d.py`, `static/skytrace-3d.js|css`, 03.11.2026, Nutzerwunsch „Spielerei“, Bahn muss stimmen)
`GET /api/skytrace/orbit3d?run=<Lauf>&key=<Ordner>/<K-Nr>` → `Controller.orbit3d` → `orbit3d.payload_for` (Kandidat,
WCS aus `<Beobachtung>/platesolve`, Bildecken der Trefferaufnahme über `context_image.reference_to_current`) → `build`.
Fehlt etwas (Standort, Zuordnung, Zeit, Plate-Solving): `{ok:false, reason}` statt Fehler. `run_data` setzt je Kandidat
`view3d` (`orbit3d.available`); nur dann zeigt das Detail den Knopf. Rechnung: numpy + erfa (aus astropy) + `sgp4`;
fehlt `sgp4` im Server, läuft `orbit3d.py` als Skript im Engine-Interpreter (JSON stdin/stdout).
- **Bezugssystem D** = erdfest (ECEF) zu t0, danach raumfest (TEME um GMST(t0) gedreht). Erde dreht sich darin mit
  `earth_rate`·(t − t0); Bahnen geschlossen; das nachgeführte Blickfeld zeigt fest (ICRS → TEME per `pnm06a` + `eqeq94`),
  sein Ursprung wandert mit dem Standort. Erdfestes (Standort, Meteore, Flugzeuge) kommt als ECEF. Frontend: three = (x, z, −y)/R.
  UT1 ≈ UTC, keine Polbewegung. Geprüft gegen astropy: Satellit 9 m, Zenitrichtung 16″ (Aberration), Sonne 0,006°.
- t0 = Belichtungsbeginn (`satellites.exposure_start`, sonst wie `motion_plausibility.exposure_start`) + Mittel von
  `passage_seconds` des besten Treffers. Je Satellit (≤ 5 Treffer): Umlauf (360 Punkte), ±10 min alle 2 s
  (Position, Höhe, Geschwindigkeit, Entfernung), Durchstoßstücke im Blickfeld (±2 min, 0,05 s, Punkt-im-Viereck in
  gnomonischer Projektion, über dem Horizont).
- **Bahnelemente:** neue Abgleiche speichern `matches[].tle` (`sgp4.exporter.export_tle`), Meteore `trajectory`
  (GMN Anfang/Ende + Zeit), Flugzeuge `track` ([t, lat, lon, Höhe m]). Ältere Läufe: `archive_satrec` sucht ohne Netz in
  `catalog_cache/archives/orbits-<Datum>.json` (wie `satellite_archive.model_from_record`) und `eth-<hash>.json`
  (Schlüssel wie `eth_tles`); sonst `missing[].reason` → Hinweis in der Ansicht.
- **Seit 0.35.0** zeichnet die Lauf-Ebene auf dem gemeinsamen Globus der 3D-Erde (s. „3D-Erde“ unten); die Punkte zur Darstellung gelten weiter, Einbindung und Seitenleiste dort.
- Frontend (seit 0.21.0 kein Dialog mehr, Nutzerwunsch „zentrales Feature statt Popup“): `SkyTrace3D.mount(host, run, {focus, session, onCandidate})`
  zeichnet Bühne (Kamera-Wahl, „Schema“, Vollbild), Zeitleiste und Objektliste in einen Behälter (`host._st3d` = Szene, `state()` für Tests) und
  gibt `{dispose, fullscreen}` zurück. Genutzt im Lauf-Reiter `3d` (`tab3d`, nur mit Bildfolge und nicht während einer Analyse; Fokus-Kandidat über
  `focus3d`, da der Router keine `?`-Parameter kennt) und in `#/skytrace/3d` (`sky3dView`: Analysenliste `.st3d-runs` oben in der Seitenleiste – nach jedem `mount` wieder eingesetzt –,
  Filter „Mit Kandidaten“ `skytrace.3dhits`, zuletzt gewählter Lauf in `skytrace.3drun`). Liste der Läufe standardmäßig als Karten (`skytrace.view2`).
  Eine gemeinsame Szene über alle Läufe gibt es nicht (jede Beobachtung eigene Zeit/Standort). Taste F = Vollbild. three.js r160 +
  OrbitControls (Import auf `./three.module.min.js` umgeschrieben) seit 0.25.0 in der Shell (`astroflow/static/vendor/`, auch für den
  Tiefen-Tunnel in Bilder), Erdtextur unter `static/vendor/` (Lizenzen in den jeweiligen `README.md`), erst beim Öffnen per `import()` geladen. Zeichnet nur bei Änderung; Kamerafahrten entfallen bei
  `prefers-reduced-motion`, kein Auto-Drehen. Kegellänge = 1,35 × Entfernung des Satelliten im Blickfeld (echte Öffnung,
  daher erst in „Blickfeld“ sichtbar). Atmosphärensaum blendet sich nahe der Erde aus. `#st3dHost._st3d.state()` für Tests.
- **Lauf-Ansicht (0.17.0, Nutzerwunsch: „unabhängig davon, ob ein Objekt erkannt wurde … wohin das Teleskop geschaut hat“):**
  `GET /api/skytrace/run3d?run=&session=&focus=` → `run3d.build_run`. Je Beobachtung: Blickrichtung je Aufnahme aus
  Referenz-WCS + `frames.jsonl.reference_to_current` (≤ `MAX_SAMPLES` 360 Stichproben, Ecken → Bildlage/Positionswinkel),
  Az/Alt zur Belichtungsmitte (Stellina: Zeitstempel = Ende), parallaktischer Winkel (= Feldrotation einer Alt-Az-Montierung),
  Lücken (> max(120 s, 5 × Median, 3 × Belichtung)), ausgelassene Aufnahmen aus `ausgeschlossen.jsonl`. Ohne WCS:
  Zielkoordinate über `catalog_resolve` (Namensteile von lang nach kurz) und Bildfeld aus FOCAL/PIXSZ/NAXIS, `source='katalog'`.
  Ziel: `catalog_resolve` (Typ) + Sage-Dienst `object_facts` (Entfernung, nur Gespeichertes). Standort: Kandidat → `resolve_site`
  (Ausnahme, FITS, Ortsliste der Nacht, sonst Einstellungen); fehlt er → `{ok:false, need_site}`. Treffer: `orbit3d.build` je Kandidat im gemeinsamen
  System (`anchor` = erste Aufnahme), ±5 min, Umlauf 180 Punkte, bester Satellit je Kandidat (Fokus-Kandidat alle).
  Ergebnis per `lru_cache` nach Dateizeiten (echter Lauf mit 8 Treffern: 0,3 s, dann 2 ms; 240 KB).
- Frontend `SkyTrace3D.mount(host, run, {focus, session, onCandidate})`; Kandidaten-Knopf setzt `focus3d` und wechselt zum Reiter „3D“.
  Kuppel (`DOME` 0,045 R) und Sternkugel (`SKY` 60 R) sind erdfest bzw. raumfest und nicht maßstäblich. Ansichten Erde/Teleskop/
  Durchgang/Folgen, „Ziel“ beim Antippen des Ziels; Teleskop und Ziel mit lokaler Senkrechten als „oben“ (OrbitControls
  liest `camera.up` nur beim Anlegen → `makeControls` legt sie neu an) und folgen dem Standort, wenn sich die Erde dreht.
  „Kandidat öffnen“ setzt `focusNext` in `skytrace.js` und wechselt zum Reiter Kandidaten.
- Tests: `test_skytrace_3d.py` (gegen astropy, alte Läufe, Hinweise), `test_skytrace_3d_run.py` (Teleskopweg gegen astropy,
  Lücke, Bildlage, ohne Treffer/Standort/Plate-Solving), `test_skytrace_3d_ui.py` (Playwright, 375/1440 px).
  Offen: Flugzeug- und Meteordarstellung nur mit synthetischen Daten geprüft. Uhrversatz der Nacht: s. „Uhrversatz je Nacht“.

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

## Liniendetektor: Leistung (03.11.2026)
`line_detection._best_section` prüft Abschnitte mit Präfixsummen vor und misst mögliche Gewinner mit der bisherigen
`np.mean`-Rechnung exakt nach (Rundungspuffer, gleiche Reihenfolge/Gleichstände). Geometrie-Cache begrenzt auf 32 Größen;
bei > 128 Abschnitten alte Schleife statt quadratischer Hilfsarrays. Stern-/Blinkmasken und Abtastachse nur einmal je Bild.
Keine geänderten Detektionsschwellen. `tests/test_skytrace_performance.py` läuft mit pytest, sofern OpenCV vorhanden ist,
oder direkt im Engine-Interpreter; vergleicht Grenzwerte und komplette Erkennungen mit der erschöpfenden Suche.
Synthetischer A/B-Test (1040×1536, Sternfeld + Spur): 0,75 → 0,58 s (rund 22 % weniger Zeit), kein Gesamtpipeline-Benchmark.

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
- Nachrechnen: `engine/refresh_satellites.py <Lauf> …` (nur Kandidaten-JSON + lokale Archive, schreibt Kandidaten, Projektion, Bericht; nimmt den
  gespeicherten Ort – mit neu aufgelöstem Ort und aus der Oberfläche: `rematch_sites.py`, s. „Neu abgleichen und Standortsuche“).
  Ergebnis M31 2022-06-11 (`2026-11-01_12-19-46-886388`): 12 von 13 Spuren zugeordnet (Starlink-Züge, Iridium 914, SL-3/SL-16 R/B), vorher 0.
  M103 K0001/K0002 (unten als „84′/28′ entfernt“ beschrieben) = STARLINK-1484/-1062.
- Uhrversatz und Standort je Nacht: siehe „Standorte, Genauigkeit und Rückrechnung“ (0.23.0). Die konstant 10–18′ quer daneben
  liegenden Nächte passen zu einem um ~1–6 km falschen Standort. Test: `tests/test_skytrace.py`
  (läuft nur mit skyfield/sgp4, also im Engine-Interpreter).

## Standorte, Genauigkeit und Rückrechnung (0.23.0, 04.11.2026, Nutzerwunsch „Standorte nach Datum“)
- **Auflösung** `observation_settings.resolve_site(path, session, settings, when=None)`: `sites[Beobachtung]` (Ausnahme, bleibt hier) → FITS-Kopf →
  gemeinsame Ortsliste von AstroFlow (`astroflow/core/sites.py`, Zeitraum der Nacht aus `when` bzw. DATE-OBS, sonst Standard-Ort) → alter
  SkyTrace-Standard `site`. Ergebnis immer mit `accuracy_m`, `origin` (override|fits|period|default|skytrace), `site_id`, `source` (Text).
  Rohbild weg → Ortsliste (früher OSError). Kandidaten speichern den Ort (`site`), der Laufbericht `videos.json` → `site`.
  Einstellungen → SkyTrace zeigt nur noch Ausnahmen je Beobachtung; Orte, Zeiträume, Standard: Einstellungen → Standorte.
- **Toleranz** (`catalog_matching.site_tolerance`): quer `max(360″, min(1800″, 2 · accuracy_m / Entfernung))` je Bahn (Median-Entfernung im
  Zeitfenster) – bei genauem Ort (≤ ~400 m für LEO) unverändert. Treffer nur wegen der Ausweitung: `site_widened`, Hinweis in `evidence`.
  Ohne Treffer und `site_uncertain` (Ausweitung schon bei 1000 km nötig, ≈ ab 900 m) → Status **`site_uncertain`** „Standort ungenau – Zuordnung
  unsicher“ statt `checked_no_match`; dazu `site_note`, `site_accuracy_m`; `tolerance_arcsec` je Bahn in `diagnostics`/`matches`.
- **Rückrechnung** (`sitefit.py`, `GET /api/skytrace/sitefit?run=&folder=`, UI Lauf → Beobachtungen → „Standort der Nacht prüfen“ ab 3 Treffern; darunter Standortsuche, s. „Neu abgleichen und Standortsuche“):
  Unbekannte Nord/Ost (km) + Uhrversatz (s, wahr = Kamera + dt); Residuen quer (5 Punkte je Spur, σ 30″) und – nur bei Spuren ganz im Bild
  (Pixelpfad bzw. Bildecken, 3-%-Rand) – längs über die Durchgangszeiten an Anfang/Ende gegen die Belichtung (σ 1 s); schwache Vorinformation
  ±50 km/±30 s, Gauß-Newton mit numerischer Jacobi-Matrix, Ausreißer > 4σ einmal entfernt, Kovarianz × reduziertes χ². Nur eindeutige Treffer
  (Zweitbester nicht fast gleich gut, sonst Starlink-Zug mehrdeutig); Bahn aus `matches[].tle` oder den lokalen Archiven (`orbit3d.archive_satrec`).
  `worth` = Verschiebung > 0,3 km, > 3σ und quer-RMS < 70 %. „Übernehmen“ legt einen neuen Ort an (Genauigkeit = große Halbachse, ≥ 50 m) und
  ordnet ihm die Nacht zu – nie automatisch. Höhe ist keine Unbekannte (Höhe des Orts prüfen: 0 statt ~400 m verschiebt das Ergebnis um ~0,3 km).
- **Befund an den echten Läufen** (04.11.2026, nur gelesen, ohne Koordinaten): 20 Nächte mit ≥ 3 sicheren Spuren, alle mit einer Verschiebung
  von 0,6–1,7 km (± 0,04–0,4 km) in dieselbe Richtung, quer-RMS 100–320″ → 1–50″; mit Höhe 300–600 m statt 0 m im Mittel 0,7–0,9 km →
  der hinterlegte Standort liegt etwa 1 km neben dem Aufstellort. Uhrversatz −13…+4 s (± 1 s), passend zum Befund oben.
  Eine Nacht (2022-05-09, IC 1396): bei 100 m Genauigkeit 0 von 5 Spuren zugeordnet, mit ± 2 km 5 von 5 (598–1000″ quer); Rückrechnung
  ≈ 6 km Verschiebung, quer-RMS 867″ → 16″ – das Teleskop stand in dieser Nacht woanders.
  NGC 6781 2022-06-17 mit dem Vorschlag neu abgeglichen: Abstände quer im Mittel 97″ → 22″ (10/10 Treffer wie vorher).
- Tests: `tests/test_sites.py` (Auflösung, Engine-Laden ohne Paket, Toleranz – nur mit skyfield –, Rückrechnung an synthetischen LEO-Bahnen:
  Ort auf < 50 m, Uhr auf < 0,1 s).

## Neu abgleichen und Standortsuche (0.28.0, 05.11.2026, Nutzerbefund „Analysen nach Standortänderung nicht erneut prüfbar“)
- **Engine** `engine/rematch_sites.py` (JSON-Zeilen `progress`/`status`/`result`, Stopp-Datei):
  `rematch <Lauf>… [--force]` löst den Ort je Kandidat neu auf (`resolve_site(source_file, session, when=timestamp)`, Rohbild weg →
  Ortsliste) und rechnet nur `satellite_check` neu – Flugzeuge/Meteore/Asteroiden bleiben (dafür „Zeitreihen aktualisieren“).
  Ohne `--force` nur Kandidaten mit geändertem Ort (`site_key`: lat/lon 6 Stellen, Höhe, Genauigkeit). Schreibt `kandidaten.json`,
  `kandidaten/<K>/kandidat.json` (inkl. `site`, `satellite_overlay`), `videos.json` → `site`, danach Bericht (Fehler dort → nur Hinweis).
  Fehlen Bahndaten lokal (`needs_archive` …), bleibt der alte Abgleich samt altem Ort stehen (`missing_orbits`) – sonst ginge er verloren.
  `search <Lauf> <Ordner>`: Abgleich mit `accuracy_m = SEARCH_RADIUS_KM/2` (2σ-Toleranz = 10 km) und Obergrenze
  `site_tolerance_cap_arcsec` = `SEARCH_CAP_ARCSEC` 7200″ (neuer optionaler Parameter von `catalog_matching.site_tolerance`, nur über
  diese Einstellung) → `sitefit.observations` (nur `_sure`-Treffer, also ohne Starlink-Mehrdeutigkeit) → `sitefit.fit` → strenger Abgleich
  am Vorschlag → zweite Rückrechnung aus den strengen Treffern (`stage` strict, sonst search). `worth` zusätzlich nur, wenn der strenge
  Abgleich mehr Treffer bringt als bisher. Ergebnis nur in `<Beobachtung>/standortsuche.json` (`tracks`, `before`, `wide`, `after`, Fit-Felder,
  `night`, `site`), Kandidaten bleiben unverändert. `sitefit.py` wird ohne AstroFlow-Paket über ein Ersatzpaket `skytrace_server` geladen.
- **Netz:** `orbits_cache_only` → `archive_sources.eth_tles` liefert nur den Minuten-Cache des ursprünglichen Laufs (sonst `needs_archive`);
  Space-Track-Spiegel ohnehin nur lokal. SATCAT: `check` setzt nach dem ersten Abgleich `satcat_offline` – höchstens ein Aufruf mit Netz je
  Auftrag (Sperre vom 01.11.2026), die Suche ganz ohne.
- **Server** `rematch.py` (`SiteJobs`, `mod.sitejobs`): ein Auftrag zur Zeit, eigener Prozess neben dem `Controller`; der laufende Analyse-/
  Aktualisierungslauf wird übersprungen bzw. abgelehnt, `/refresh` und Löschen lehnen Läufe im Neuabgleich ab. `activity()` (Schlüssel
  `skytrace:abgleich:<ns>`), `status.rematch` (`sites_changed`, `signature`, Zustand, Fortschritt), `busy_jobs` zählt mit.
  `sites_signature` = Hash aus IDs/Koordinaten/Genauigkeit der Orte, Zeiträumen und Standard (Namen egal); `skytrace.rematched_sites` merkt den
  Stand beim Start des letzten erfolgreichen „alle Läufe“-Auftrags (erster Blick setzt ihn ohne Hinweis).
  API: `GET|POST /rematch` (`{run}` = alle Spuren eines Laufs, `{all:true}` = nur geänderte Orte, `force`), `POST /rematch/stop`,
  `POST /sitesearch {run, folder}`, `GET /sitesearch?run=&folder=` (`job` nur für diese Beobachtung, `result` = Datei). `run_data` liefert je
  Beobachtung `trails` (Spuren mit Himmelskoordinaten) und `site_search` (Kurzfassung).
- **UI** (`skytrace.js`): Analyse-Menü „Satelliten neu abgleichen“; Beobachtungen → „Standort der Nacht prüfen“ ab 3 Treffern „Prüfen“ (wie bisher),
  sonst „Standort suchen“ (`watchSearch` pollt, `searchHtml` + `fitHtml`, Übernehmen über `applyFit`, danach Knopf „Jetzt … neu abgleichen“).
  `rematchPanel` in der Statistik und über den neuen Frontend-Hook `sitesPanel` unter Einstellungen → Standorte; `rematchStatus` meldet das Ende
  eigener Aufträge (auch wenn sie zwischen zwei Abfragen fertig wurden) und bietet bei geänderter Ortsliste einmal je `signature`
  (localStorage `af.skytrace.rematchAsked`) per Toast den Abgleich an – nie automatisch.
- **Befund** (05.11.2026, Kopie der vier Läufe vom 2022-05-09, nur gelesen): IC 1396 strenger Abgleich 0 von 5, Suche 5 von 5 → Verschiebung
  4,7 ± 0,1 km, quer-RMS 704″ → 16″, am Vorschlag streng 5 von 5. Keine Spur ganz im Bild → Uhrversatz unbestimmt (± 30 s = Vorinformation).
  M101 und NGC 4565 derselben Nacht haben je nur eine Spur (keine eigene Rückrechnung möglich); übernimmt man den IC-1396-Ort für die Nacht,
  gleicht „alle neu abgleichen“ sie mit ab. Seit 0.29.0 gemeinsam über „Standorte aller Nächte“ (unten).
- Tests: `tests/test_skytrace_rematch.py` (Engine-Teil nur mit skyfield – direkt im Engine-Interpreter ausführbar: 5 km falscher Ort →
  Suche < 200 m und Uhr ± 0,3 s, strenger Abgleich danach mit Treffern; Starlink-Zwilling wird nicht genutzt; geänderte Ortsliste wirkt;
  fehlende Bahndaten behalten den alten Abgleich; Server: Signatur, Hinweis, Auftrag mit Engine-Attrappe, Aktivität). UI: Beobachtungen-Ansicht in
  `test_ui_layout.py` (`ROUTES`, Fixture mit `standortsuche.json`).

## Standorte aller Nächte (0.29.0, 05.11.2026, Nutzerwunsch „für Spuren Treffer finden, indem er alle Standorte prüft bzw. zurückrechnet“)
- **Gruppen** (`SiteJobs.start_nights`, Server): `stats.collect` (je Beobachtung nur die neueste vollständige Analyse, Nacht = Datum im Ordnernamen
  bzw. früheste Zeit − 12 h; `stats._run` liefert dafür neu `folder`) → `<SKYTRACE_HOME>/standortsuche-gruppen.json` `[{night, observations:[{run, folder, session}]}]`.
  Auftrag `kind='nights'` (sperrt keine Läufe, `busy_with`), Aktivität → `#/skytrace/standorte`.
- **Engine** `rematch_sites.py nights <gruppen> <ausgabe> [--radius-km]` → `night()` je Gruppe, Ergebnis nach jeder Nacht in
  `standortsuche-naechte.json` (`state` running|done|stopped; Anhalten über `InterruptedError`/Stopp-Datei behält fertige Nächte):
  1. Bisheriger Ort = `resolve_site` des ersten Kandidaten; FITS-Ort → `status='fits'` (Liste hätte keine Wirkung). `stored` = eindeutige Treffer aus
     `kandidaten.json` (`_score`: `sitefit._sure`, quer-RMS der besten Bahn = `mean_offset_arcsec`).
  2. Ab `MIN_TRAILS` (3) eindeutigen Treffern nur verfeinern; sonst jeden Listenort (und eine Ausnahme/den alten SkyTrace-Ort) streng prüfen → `tested`.
     Bester = meiste eindeutige Treffer, dann kleinerer quer-RMS. „Gut“ = ≥ 1 Treffer und RMS ≤ `GOOD_RMS_ARCSEC` 60″ (Orte ~1 km daneben lagen bei 100–320″).
  3. Nicht gut: Rückrechnung aus den Treffern des besten (ab 3), sonst Suche mit Spielraum (`accuracy = Radius/2`, Obergrenze 2°) um den besten –
     ohne jeden Treffer um den bisherigen und die nächsten Listenorte (≤ `MAX_CENTERS` 3). `_night_fit` = `sitefit.fit` über alle Beobachtungen der Nacht
     (gemeinsamer Ort + Uhrversatz; „ganz im Bild“ je Ordner). Danach streng am Vorschlag und zweite Rückrechnung.
  4. Vorschlag `fit` nur, wenn er mehr eindeutige Treffer bringt oder den RMS um > 30 % senkt; liegt er ≤ max(300 m, 2 × Genauigkeit) an einem Listenort,
     wird dieser vorgeschlagen (`list`). Sonst `list`, wenn ein anderer Listenort mehr Treffer hat. Unter 3 Spuren Hinweis „nur Auswahl“; ohne Treffer
     `status='none'` „Kein Ort passt“. `assign` = alle `night_of(timestamp)` der Kandidaten (danach richtet sich `resolve_site`).
- **UI** `nightsView` (`#/skytrace/standorte`, Knopf im `rematchPanel`): Spielraum 5/10/20 km (`af.skytrace.nightsRadius`), Filter „Mit Vorschlag“/„Alle“,
  `applyNights` (wie `applyFit`: SkyTrace-Ausnahmen der Beobachtungen entfernen, neue Orte in einem `POST /sites`, je `assign`-Nacht `/sites/night`),
  dann `POST /sitesearch/nights/applied` und `POST /rematch {runs, force:false}` für die betroffenen Läufe; `ackSites` verhindert danach die Rückfrage
  „Standorte geändert“. Toasts: die Ansicht am `href` des Auftrags meldet mit Zahlen (`rmHref`), sonst der allgemeine Hinweis der Shell.
- **Befund** (Kopie der Läufe vom 2022-05-09, nur gelesen): 3 Beobachtungen, 7 Spuren; Listenorte 3/7 (335″), 7/7 (24″), 7/7 (162″) → Vorschlag
  Listenort mit 7/7, 24″ (bisher 0 Treffer).
- Tests: `tests/test_skytrace_nights.py` (zwei Beobachtungen einer Nacht gemeinsam zurückgerechnet, falscher Listenort durch richtigen ersetzt,
  keine passende Spur → kein Vorschlag, Starlink-Zwilling nicht genutzt, Anhalten; Server: Gruppen, Ergebnis, „übernommen“). UI: `#/skytrace/standorte`
  in `test_ui_layout.py` (Fixture `standortsuche-naechte.json`).

## Uhrversatz je Nacht (0.30.0, 05.11.2026, Befund „Augustwoche 2022: Kamera-Uhr ~10 min falsch, ~50 Spuren, 1 Treffer“)
- **Engine** `engine/clock_scan.py` (aufgerufen aus `rematch_sites.night()`, Schalter `--no-clock`, UI-Häkchen „Uhrversatz suchen“, Server
  `start_nights(clock=…)` / `POST /sitesearch/nights {clock}`): nur Nächte mit ≥ 3 Spuren und < 50 % eindeutig zugeordnet (`needs_clock`).
  1. `trail_hits`: je Spur `satellite_check` blockweise über ± `SCAN_RANGE_S` 1200 s (Blöcke Belichtung ± `BLOCK_SLACK_S` 60 s, 20 Abgleiche je Spur,
     5 Bahnpunkte/s – mit 2/s gingen kurze Spuren verloren; `satellite_max_matches` 1000, `_archive_time` = Kamerazeit, damit der ETH-Minuten-Cache passt).
     Jede passende Bahn → Versatz = Blockverschiebung + Mitte der Durchgangszeiten − Belichtung/2, Spielraum `half` = (Belichtung − Durchgangsdauer)/2
     (Spur ganz im Bild → ≈ 0).
  2. `evaluate`: Gitter 2 s; Stütze = verschiedene Spuren mit **verschiedenen NORAD-Nummern** (derselbe Starlink auf zwei Spuren zählt einmal), Treffer
     gilt bei |Versatz − t| ≤ half + `MATCH_TOL_S` 30 s. Feinwert: Schnitt der Spielräume, sonst Median (± Streuung/√n). Zufallsniveau λ = Σ je Spur
     1 − exp(−Abdeckung der Treffer fern vom Maximum (> 60 s) / Fensterbreite); Fehlalarm = Poisson(≥ k | λ) × Zahl unabhängiger Versatzschritte (2·1200/(2·30+10)).
  3. `proposal_ok`: ≥ `MIN_TRAILS` 3, Fehlalarm ≤ `MAX_FALSE` 1 %, |Versatz| ≥ `MIN_SHIFT_S` 20 s (sonst reicht die normale Uhrtoleranz), mehr Spuren als bisher.
     Danach strenger Abgleich mit dem Versatz (normale ± 20 s) → `after_sure`; mit ≥ 3 eindeutigen und Spuren ganz im Bild `_night_fit` für die letzten Sekunden.
     Ergebnis `night.clock` (`proposal {offset_s, sigma_s, support, sure, before, confidence, chance, nights}` bzw. `reason`). Mit Vorschlag prüft `night()`
     die Orte danach mit korrigierter Uhr (nur im Auftrag), Status `better`. Ort aus FITS: Uhrsuche läuft trotzdem, nur keine Ortsänderung.
     Ist schon eine Korrektur übernommen, suchen `trail_hits` und `evaluate(center=…)` um sie herum; „passt“ heißt dann Abweichung < 20 s von ihr.
- **Ablage** (Entscheidung): SkyTrace-Einstellung `catalog_settings.json` → `clock_offsets {Nacht: {offset_s, sigma_s, support, confidence, applied}}`,
  Nacht = `catalog_matching.night_key` (UTC − 12 h, wie `core/sites.night_of`). Nicht in der Ortsliste: die Uhr gehört zur Kamera, nicht zum Ort;
  die Ortsliste (`core/sites.py`) prüft ihr Schema und wird auch vom Bilder-Modul gelesen. `update_settings` prüft Nacht (JJJJ-MM-TT) und |Versatz| ≤ 3600 s,
  `null` entfernt. Wahre UTC = Kamerazeit + `offset_s` (negativ = Kamera-Uhr ging vor).
- **Wirkung:** `catalog_matching._times(candidate, settings)` addiert `clock_offset` – für alle Kataloge (Satelliten, Asteroiden, Flugzeuge, Meteore);
  `timestamp`/`timestamp_text` und Rohbilder bleiben Kamerazeit. Das Satellitenergebnis trägt `clock_offset_s` und `exposure_start` (korrigiert →
  `sitefit`/`orbit3d` rechnen damit weiter); Bahndaten werden zur Kamerazeit geladen. `rematch` (ohne `--force`) gleicht Kandidaten neu ab, deren
  gespeichertes `clock_offset_s` vom eingestellten abweicht. UI: Kandidatendetail „(Kamerazeit; Uhr korrigiert um …)“; `nightsView` Spalte
  „Uhrversatz“ (`clockScanHtml`, Chip „Uhr falsch gestellt“), übernommene Korrektur aus `GET /settings` → `clock_offsets`; `applyNights` schreibt
  `POST /settings {clock_offsets}` und startet den Neuabgleich wie bei Orten. Nie automatisch.
- **3D-Ansicht:** `observation_settings.clock_offset_at` (leichte Schicht, auch `catalog_matching` nutzt sie) → `orbit3d.clock_offset`. `run3d` rechnet alle
  Aufnahmezeiten (Teleskopweg, Az/Alt, parallaktischer Winkel, Zeitleiste, `excluded_times`, Lücken) in wahrer UTC (Zeitstempel − Belichtung bei Stellina + Versatz);
  Versatz zusätzlich im `lru_cache`-Schlüssel. `orbit3d.payload_for`: t0 aus `sat.exposure_start` (beim Abgleich korrigiert, passt zu den Durchgangszeiten),
  Rückfall `_exposure_start` = Kamerazeit + Versatz. Stammt der Abgleich von einer anderen Korrektur (`sat.clock_offset_s` ≠ eingestellt), `clock_pending` →
  Hinweis „Satelliten neu abgleichen“. Antwort `clock_offset_s` → Hinweis „Uhr korrigiert um …“ (`skytrace-3d.js`). Test: `test_skytrace_3d_run.py`.
- **Befund** (05.11.2026, Kopie der Kandidaten-JSONs aller Läufe 12.07.–07.08.2022, nur gelesen): Einzeltreffer streuen längs um ± 25 s (alte Trümmer-TLEs),
  daher `MATCH_TOL_S` 30 statt 8 s (mit 8 s: 01.08. nur 3 Spuren, Fehlalarm 5 %). Ergebnis je Nacht (Spuren / bisher eindeutig → Ergebnis):
  12.07. 2/1 → zu wenig; 15.07. 9/9 → Uhr richtig (−7 s, 9 Spuren, zufällig 0,5); 18.07. 9/7 → richtig (−2 s, 6 Spuren);
  **01.08. 12/0 → Vorschlag −639 ± 3 s (Grobwert −646 s aus 5 verschiedenen Spuren in NGC 6823, M15, Helix; zufällig 0,4; Fehlalarm 0,2 %;
  Feinwert aus `_night_fit` mit 4 Spuren ganz im Bild), streng mit Korrektur 5 von 12 eindeutig (vorher 0), quer-RMS 158″ (Ort wohl ~1 km daneben)** – Starlink-2253 traf zwei Helix-Spuren
  bei −624 s und zählt einmal; 03.08. 4/0 → nur 2 Spuren (NGC 246 −661/−686 s); 06.08. 32/1 → keine Häufung (bester 4 Spuren bei −809 s, zufällig 0,8,
  Fehlalarm 37 %); 07.08. 5/0 → keine Häufung. M15 K0001/K0003 bei −433/−460 s gehören nicht zur Häufung. Laufzeit ~10 s je Spur (06.08.: 324 s).
  Nicht umgesetzt: gemeinsame Suche über benachbarte Nächte mit linearer Uhrdrift (würde 03.08. mit 2 Spuren stützen).
- Tests: `tests/test_skytrace_clock.py` (Engine nur mit skyfield, direkt im Engine-Interpreter ausführbar: −600 s → Vorschlag ± 5 s, 4/4 Spuren, danach
  normaler Abgleich mit Korrektur; zufällige Spuren → kein Vorschlag; richtige Uhr → „geht richtig“; Statistik mit Rauschen/Häufung/gleichem Satelliten;
  Nachtsuche + `update_settings` + `rematch` erkennt geänderte Uhr). UI: `#/skytrace/standorte` in `test_ui_layout.py` (Nacht mit Uhrvorschlag, übernommene Korrektur).
- **Nachtrag 0.32.0:** Der Vorschlag −639 s für den 01.08.2022 war falsch – das Teleskop stand ~250 km entfernt (Urlaub). Am wahren Ort (Suche ohne
  Vorwissen, unten) passen 12 von 12 Spuren mit normaler Uhr (+0,7 ± 0,7 s). Ein Uhrversatz vom falschen Ort ist also kein Beleg für eine falsche Uhr.

## Standort ohne Vorwissen (0.32.0, 06.11.2026, Befund „Augustwoche 2022 im Urlaub, ~250 km von jedem Listenort“)
- **Anlass:** IC59 2022-08-06 (16 Spuren): am Listenort 0 Treffer, auch mit Uhr ± 60 min; die Suche mit Spielraum reicht nur ≤ 20 km um Listenorte.
  Am wahren Ort (vom Nutzer nachträglich angelegt, beim Test ausgeblendet) 12/16 mit normaler Uhr, quer 7–67″.
- **Engine** `engine/global_site.py`, aufgerufen aus `rematch_sites.night(…, global_km)` als letzter Schritt, nur wenn kein Ort (Liste, Rückrechnung)
  mindestens max(3, Hälfte der Spuren) eindeutig zuordnet (`needs_global`) – normale Nächte bleiben gleich schnell. CLI `nights … --global-km <km>`
  (0 = aus), Server `start_nights(global_km)` (100–3000 km), `POST /sitesearch/nights {global_km}` (fehlt → 1500), UI-Auswahl „Ohne Vorwissen“.
  1. **Rückprojektion:** Spurmitte d (ICRS → TEME) und Satellit S → Beobachter liegt auf S − ρ·d; erster Schnitt mit dem WGS84-Ellipsoid (in TEME,
     Ellipsoid ist um z symmetrisch) = einziger passender Bodenpunkt. Prüfungen dort: Höhe ≥ 10°, Satellit im Sonnenlicht (Zylinderschatten,
     60 km Rand), scheinbare Bewegung (inkl. Erddrehung des Beobachters) ≤ 6° zur Spurrichtung, Durchgang ≤ 1,2 × Belichtung + 1 s.
     **Spuren „ganz im Bild“ nicht auf Durchgang = Belichtung festlegen** – IC59 K0004/K0012 sind kurze Aufblitzer (0,2–0,9 s), mit dieser Bedingung fehlten sie.
     Vorauswahl für alle Bahnen des Archivs (`historical_models` zur Kamerazeit, wie der normale Abgleich) auf einem 15-s-Raster (`SatrecArray`),
     dann 1-s-Schritte nur für Paare mit Bodenpunkt im Suchgebiet (Umkreis `global_km` um Listenorte + bisherigen Ort).
  2. **Abstimmen in zwei Stufen:** Ein Paar ist eine Fläche im Raum (Ort, Uhrversatz) – für Spuren über den Bildrand ist der Durchgangszeitpunkt in der
     Belichtung frei, die Stimme gilt für ein Versatzintervall. Grob: Zellen 20 km × 4 s in überlappenden 2×2×2-Blöcken, Zahl verschiedener Spuren
     → die 40 besten Blöcke. **Grob allein reicht nicht:** im 40-km-Block erreichten zufällige Häufungen so viele Spuren wie der wahre Ort (IC59: je 10;
     der wahre Block lag auf Rang 2). Fein (`local`): Raster 1 km × 1 s (± 30 km, ± 12 s), Bahnpunkte alle 0,25 s, Spur deckt einen Rasterpunkt ab, wenn
     ihre Bodenspur ≤ max(2,5 km, Entfernung × 300″) + 0,9 km vorbeiläuft und der Versatz passt. Stütze = verschiedene Spuren mit **verschiedenen
     NORAD-Nummern** (Spuren mit wenigen Möglichkeiten zuerst). Echte Bodenpunkte lagen 0,6–3,3 km am wahren Ort (1-s-Schritte, Bahnfehler).
  3. **Zufallsniveau:** λ = Σ je Spur 1 − exp(−abgedeckte Fläche × Versatzbreite / Volumen) im Umkreis 300 km aus allen Stimmen; Fehlalarm =
     Poisson(≥ k | λ) × Zahl unabhängiger Prüfstellen (Fläche/(5 km)² × Versatzbereich/8 s). Uhr erst ± 60 s, ohne Ergebnis ± 20 min (mit „Uhrversatz
     suchen“). **Eichung an echten Spuren** (Kopien 01./06.08.2022, Zeit je Spur zufällig um ± 3–15 min verschoben → kein gemeinsamer Ort,
     14 Läufe): kleinste Schätzung meist 13–100 %, einmal 0,9 % (7 von 32 Spuren, streng danach 5 eindeutig – der Ausgleich erzwingt mit 3 freien
     Größen immer einige Treffer). Die Poisson-Schätzung ist also rund 10× zu optimistisch (Starlink-Züge, korrelierte Stimmen) →
     `MAX_FALSE` 0,1 %; echte Funde lagen bei ≤ 1e-5. Mit 0,1 % in allen 14 Läufen kein Vorschlag.
  4. **Verfeinern** (`refine`, ≤ 4 Häufungen): `sitefit.fit` aus den beteiligten Bahnen (Ort + Uhr, Höhe 0 m), strenger Abgleich aller Spuren der Nacht
     am Ergebnis mit dem Versatz (`check`, normale Toleranzen) und zweite Rückrechnung aus dessen eindeutigen Treffern. Vorschlag nur bei ≥ 3 eindeutigen
     und mehr als bisher.
- **Ergebnis** `night.global` (Stufen mit Paaren/Stimmen/Häufungen, Grund) und `proposal.kind='global'` (`lat/lon` 6 Stellen fürs Übernehmen, `alt_m` 0,
  `accuracy_m`, `sure`, `rms`, `support`, `confidence`, `clock_s`, `nearest_name/km`); liegt er ≤ max(2 km, 2 × Genauigkeit) an einem Listenort →
  `kind='list'` mit `source='global'`. Uhr: |Versatz| ≥ 20 s → `night.clock.proposal` (`source='global'`), sonst wird ein Uhrvorschlag vom bisherigen Ort
  verworfen (`dropped`, Grund „Folge des falschen Orts“). UI `globalHtml`: Koordinaten auf 0,01°, Unsicherheit, Spuren, Sicherheit, Uhr, „Höhe unbekannt“;
  Karte seit 0.34.0 je Nacht (unten). `applyNights` legt „Unbekannter Ort (Satelliten <Nacht>)“ an, Hinweis auf Höhe/Namen. Nie automatisch.
- **Leistung:** IC59 allein 19 s (Stufe ± 60 s: 1 137 Paare, 4 s Rückprojektion), ganze Nacht 06.08. (32 Spuren) 39 s; ± 20 min ~36 s mehr. `MAX_PAIRS` 60 000
  als Notbremse. Bahnen nur lokal (`orbits_cache_only`), keine SATCAT-Abfragen (`satcat_offline`), Anhalten über die Stopp-Datei (`check_stop`).
- **Befund** (06.11.2026, Kopien der Kandidaten-JSONs, `nights … --global-km 1500`, Urlaubsort aus der Ortsliste ausgeblendet; Abstand zum
  später angelegten Ort, nächster Listenort ~244 km entfernt):

  | Nacht | Spuren | bisher eindeutig | Ergebnis | Abstand | Uhr | Dauer global |
  |---|---|---|---|---|---|---|
  | 01.08. | 12 | 0 (mit −639 s: 5) | `global` 12/12, quer 80″ | 0,68 km | +0,7 ± 0,7 s → −639 s verworfen | 9 s |
  | 03.08. | 4 | 0 | kein Vorschlag (keine Häufung über dem Zufall) | – | – | 6 s |
  | 06.08. | 32 | 1 | `global` 26/32, quer 29″ (IC59 allein 12/16, 25″) | 0,13 km | −4,1 ± 0,5 s | 55 s |
  | 07.08. | 8 | 1 | `global` 8/8, quer 55″ | 0,67 km | −7,6 ± 0,9 s | 5 s |
  | 15.07., 18.07., 17.06. (Kontrolle, zu Hause) | 9 / 9 / 16 | – | Listenort passt (9, 7, 15 eindeutig), Suche läuft nicht | – | – | – |

  Ohne Listenprüfung direkt gesucht fanden auch die Kontrollnächte ihren Heimatbereich (7–15 Spuren). 03.08. (NGC 246/NGC 7023) bleibt offen –
  dort passt vermutlich derselbe Urlaubsort; übernimmt man ihn für die Woche, gleicht „neu abgleichen“ die Nacht mit ab.
- Tests: `tests/test_skytrace_global.py` (Engine im Engine-Interpreter direkt ausführbar: Ort 300 km neben dem Listenort, Uhr −300 s, 40 Störbahnen →
  < 5 km, ± 5 s, alle Spuren eindeutig, erst in der ± 20-min-Stufe; verdrehte Spuren → kein Vorschlag; Nachtsuche mit/ohne `global_km`; guter Listenort →
  Suche läuft nicht; Geometrie der Rückprojektion; Server reicht `--global-km` durch). UI: `#/skytrace/standorte` in `test_ui_layout.py` (Nacht mit `global`).

## Karte und Heatmap je Nacht (0.34.0, 06.11.2026, Nutzerwunsch „sehe keine Karte“ – sie erschien nur bei `kind='global'` – „eine Art Heatmap“)
- **Daten** (`global_site.heat_map`): grob je 20-km-Zelle (gleiches Gitter wie die erste Stufe, Sinusprojektion um das Mittel der Orte) die größte Zahl
  verschiedener Spuren bei einem Versatz – nur der belegte Ausschnitt, uint8 Base64 (Zeilen Süd → Nord); fein aus `local` (1 km, ± 30 km, je Punkt bester
  Versatz) **um das Endergebnis nach `refine`** neu gerechnet (der grobe Block lag in der Probe 38 km daneben). Am Ergebnis bilden Spuren über den Bildrand
  ein Plateau längs der Bahn (synthetisch 95 km² gleich hoch) – das ist echte Unbestimmtheit, kein Fehler.
- **Ablage** `<SKYTRACE_HOME>/standortsuche-karten/<Nacht>.json` (`rematch_sites.save_heat`; `coarse`, `fine`, `clock_s`, `region_km`, `computed`, `result` =
  Kurzfassung mit Vorschlag/Häufungen). **Nicht** in `standortsuche-naechte.json` (gepollt; `_heat` wird vor dem Speichern entfernt). Echte Nacht: ~40 KB.
  Koordinaten bleiben lokal (Datenordner), nichts davon ins Repo.
- **Berechnen** (Entscheidung): Lief die Suche ohne Vorwissen nicht, weil ein Listenort passte, gibt es Knopf **„Heatmap berechnen“** statt sie immer zu rechnen
  (normale Nächte bleiben schnell) und statt nur einer `sitefit`-Ellipse (die zeigt nichts über andere mögliche Orte). Engine `rematch_sites.py heatmap
  <gruppen> <Nacht> [--global-km] [--no-clock]` → `global_site.search_night` für diese Nacht, ändert keinen Vorschlag; Server `SiteJobs.start_heatmap`
  (Auftrag `kind='heatmap'`, sperrt keine Läufe, nur Nächte aus `standortsuche-gruppen.json`), `POST /sitesearch/nights/heatmap {night, global_km, clock}`,
  `GET /sitesearch/nights/heatmap?night=` (`heat`, `job`), `GET /sitesearch/nights` → `maps` (Nächte mit Datei). Echte Kontrollnacht: 10 s.
- **UI** (`openNightMap` in `skytrace.js`): Knopf „Karte“ in jeder Nachtzeile (bei `global` gleich offen), Ansicht „Übersicht“ (Orte + heiße Zellen ≥ 60 % des
  Maximums) / „Nah“, Heatmap als Canvas-Bild im SVG über den Umrissen (Natural Earth, `outlines`), Farbskala dunkel → hell (`heatColor`), Orte grau,
  bisheriger Ort als Ring, Vorschlag blau mit Ellipse (`ellipse_km` von `sitefit`, 1σ, Winkel von Nord über Ost; sonst Kreis mit `accuracy_m`;
  bei `list` Koordinaten aus der Ortsliste). „Straßenkarte“/„Gelände“ = Leaflet (`leafletMap`, Zellen als Rechtecke, Canvas-Renderer, ≤ 4 000) – erst auf
  Knopfdruck; „Karten immer laden“ = `af.skytrace.mapsOnline`. Offene Karten bleiben beim Neuzeichnen offen (`opened`, `mapState`). Kopf: Leiste
  „n Vorschläge noch nicht übernommen“ mit „Alle übernehmen“ (`.stn-open`).
- Tests: `test_skytrace_global.py` (Heatmap-Datei: hellste grobe Zelle < 40 km, Mitte des feinen Plateaus < 5 km am wahren Ort, nicht in der Ergebnisdatei;
  „berechnen“ bei passendem Listenort; Server: Prüfungen, Datei, `maps`), `test_ui_layout.py::test_night_map_heatmap` (synthetische Heatmap, Umrisse, Nah,
  Nacht ohne Heatmap mit Knopf, keine Kachelanfragen) und Fixture `_heat_fixture`.

## 3D-Erde – eine Ansicht für Nächte, Standorte und Analysen (0.35.0, 06.11.2026, Nutzerwünsche „Karte zu klein, zentrales Feature, mit der 3D-Map kombinieren, Filter, Nachtsicht, animiert“ und „die 3D-Ansicht kombinieren, aktuell gibt es zwei“)
- **Eine Komponente, ein Globus:** `static/skytrace-erde.js|css` (Präfix `ste-`) = Globus-Engine `globe()` + Seitenleiste; `static/skytrace-3d.js` ist nur noch die
  **Lauf-Ebene** `SkyTrace3D.runLayer(G, data, {interactive})` auf dieser Szene (kein eigener Renderer). Navigation **„3D-Erde“** (ein Eintrag), Seite
  `#/skytrace/erde[/<Nacht>]` = Kontext **„Nächte & Standorte“**, `#/skytrace/3d` = Kontext **„Analysen“** (alter Link bleibt gültig; der Umschalter oben in der
  Seitenleiste ändert die Adresse per `replaceState`). Lauf-Reiter „3D“ (`tab3d`) **bettet dieselbe Komponente ein** (`SkyTraceErde.embed`, Lauf fest, ohne Lauf-/Nachtliste,
  mit allen Ebenen) – Entscheidung: im Lauf springt man zwischen Kandidaten, Vorschau und 3D; ein Sprung in die zentrale Ansicht würde den Lauf mit seinen Reitern verlassen.
  Kandidatenknopf „3D-Ansicht“ wechselt wie bisher zum Reiter mit Fokus (`focus3d`). `SkyTrace3D.mount` = Alias auf `embed`.
- **Helfer** aus `skytrace.js` über `window.SkyTraceHelpers` (Heatmap-Zellen, `nightMarks`, `ellipsePts`, `outlines`, `applyNights`, Texte, `runVariants`, `runDate`, `media`,
  `openCandidate`). Datenweg wie 0.34.0 (`standortsuche-naechte.json`, `standortsuche-karten/<Nacht>.json`, Ortsliste, `run3d`); neu nur `GET /sitesearch/nights/heatmaps?nights=a,b`
  (leer = alle, ≤ 120 je Anfrage; das Frontend lädt in Paketen zu 100 und holt eine fehlende Nacht beim Wählen einzeln nach). `nightsView` hat je Nacht „Auf der Erde zeigen“.
- **Szene:** Erde = Gruppe `earth` (WGS84-Ellipsoid: Kugelgeometrie per `toEllipsoid` umgerechnet, Breite = geodätisch – sonst lagen Listenort und Teleskopstandort bei 47° ~20 km
  auseinander). Alles Erdfeste (Textur, Grenzen, Heatmap, Orte, Kacheln, Kuppel/Standort des Laufs) hängt daran. **Analysen:** die Zeitleiste dreht `earth` um
  `earth_rate`·(t − anchor) (System D wie bisher, Raumfestes direkt in der Szene). **Nächte:** Drehung 0 = erdfest; die Ebene „Satellitenbahnen und Teleskop“ zeigt dieselbe
  Lauf-Ebene **statisch zur Ankerzeit** (bei Drehung 0 ist D = ECEF des Ankers, also ohne Umrechnung und ohne zweite Darstellung): Kuppel, Teleskopweg, Blickfeld der ersten
  Aufnahme, Satelliten an ihrem Durchgang, ohne Zielstrahl. Kontextwechsel: Lauf entfernen, Erde in 0,9 s zurückdrehen, Kamera fährt zur Nacht; Laufwahl = Kamerafahrt der Lauf-Ebene.
- **Schnittstelle G** (für `runLayer`): `THREE, sc, earth, camera, controls(), makeControls(up)` (OrbitControls liest `camera.up` nur beim Anlegen), `zoomMode('globe'|'orbit')`
  (Globus: eigenes Zoomen über die Höhe; Teleskop/Durchgang/Folgen: OrbitControls zum Blickpunkt), `setFly`, `dirty`, `addMat(layer, m, base)`/`dropMats` (Deckkraft der Ebene),
  `layerK`, `hook('draw'|'frame'|'resize'|'pick', fn)` → Abmelden, `cardEl` (eine Infokarte für Nächte und Lauf-Objekte), `setSun`, `onRunPick`.
- **Ebenen** (`LAYERS`, localStorage `skytrace.erde.layers`; im Analysen-Kontext ist „Satellitenbahnen“ eingeschaltet): Orte der Liste, Ort je Nacht (Ring), Heatmap, Vorschläge
  (Punkt + 1σ-Ellipse; übernommene Nächte ohne), Satellitenbahnen und Teleskop, Grenzen/Küsten (ein `LineSegments`, ~190 000 Punkte), Tag/Nacht-Grenze. Deckkraft `k` je Ebene,
  450 ms weich (`applyK`). Im Analysen-Kontext gelten Ort/Vorschlag/Heatmap der **Nacht des Laufs** (Anker − 12 h).
- **Heatmap:** je Nacht eine Kappe über dem belegten Ausschnitt mit Wert-Textur (r = Spuren/Höchstwert, 3 px je Zelle, ≤ 1024²), Farbe per Shader aus der Skala von `heatColor`;
  Zellen mit 1–2 Stimmen fast durchsichtig (Rauschen einzelner Spuren; die Streifen im ersten Screenshot waren Zufallswerte der Test-Fixture). Grob (20 km) immer, fein (1 km, ± 30 km)
  blendet zwischen 1,55 und 1,2 R ein. „Aufbau“: `reveal` 0 → 1 in 1,4 s, hellste Zellen zuerst. „Alle Nächte“ = alle Heatmaps, je Nacht relativ.
- **Nachtsicht** (`EARTH_FS`, Uniform `nv`, 600 ms): Nachtseite → gedämpftes Blaugrün, Umrisse hell-türkis; Auto (`skytrace.erde.nv`) = an, wenn eine Datenebene an ist und der
  Blickpunkt auf der Nachtseite liegt (Skalarprodukt mit der Sonne < 0,12); An/Aus übersteuert. **Sonne:** Analysen = `run3d.sun` (raumfest – die Grenze wandert mit der Zeit);
  Nächte = vereinfachte Formel (`sunLatLon`, ~0,01°) zur Ankerzeit des ersten Laufs der Nacht, sonst Mitternacht Ortszeit an der Region.
- **Kamera:** Globus-Fahrt = Slerp + Abstand mit Bogen (1,5 s), Ausschnitt aus Heatmap, Vorschlag, bisherigem Ort und Bahnen, mindestens 1,09 R; Lauf-Ansichten Erde/Teleskop/Durchgang/
  Folgen/Ziel wie bisher (1,1 s). `prefers-reduced-motion`: alles sofort.
- **Kacheln:** „Karte laden“ erst unter 1,08 R im Globus-Zoom, 4 × 4 OSM-Kacheln als Textur der Erdgruppe, entfernt sich ab 1,5 R. Sonst nie Netz.
- **Leistung** (06.11.2026, 1440 × 900, Playwright-Chromium, mittlerer Bildabstand): mit GPU (RX 9070 XT, D3D11) 16,7 ms bei Nachtwechsel, Kontextwechsel, Teleskop-Ansicht
  und Abspielen (einzelne 50 ms beim Neuaufbau der Marken); ohne GPU (SwiftShader) 20–28 ms. Zeichnen < 0,2 ms CPU.
- Tests: `host._ste.state()` (Kontext, Nacht, Lauf, `earthRot`, `nv`, `layers`, `reveal`, `runs`, Bildzeiten), `host._st3d.state()` = Lauf (wie bisher). `test_skytrace_erde_ui.py`
  (Ebenen, Nachtsicht, Karte, Regler, keine Kacheln; Bahnen-Ebene statisch; Kontextwechsel mit Erddrehung und Rückdrehung, alter Link; Sammel-Endpunkt),
  `test_skytrace_3d_ui.py` (Lauf-Reiter eingebettet, zentrale Ansicht ohne Standort), `test_ui_layout.py` `ROUTES`.

### Flache Karte (0.37.0, 06.11.2026, Nutzerbefund „Standorte betrachten geht nicht gut – Erde zu schnell und ungenau; wie Google Maps: reingezoomt normale Karte, rausgezoomt Kugel, mit Heatmap“)
- **`static/skytrace-karte.js`** (`SkyTraceKarte.FlatMap(stage, deps)`): Canvas 2D in Web-Mercator über der WebGL-Bühne (Klasse `ste-flat`), Weltkoordinaten x, y ∈ [0, 1],
  Maßstab `Z` px je Welteinheit (px/km = Z / (40 075 · cos φ)). Zeichnet nur bei Änderung; ist sie ganz eingeblendet, zeichnet der Globus nicht (`flat.tick` → `flatFull`).
  Daten kommen über Getter aus `globe()` (Deckkraft der Ebenen, `nv`, `shade`, Sonne im erdfesten System, sichtbare Heatmaps, `M.flatMarks` aus `rebuildMarks`, Teleskopstandorte).
- **Übergang:** `zoomBy` (Mausrad/Pinch zum Zeiger, Punkt unter dem Zeiger per Quaternion nachgeführt) ruft unter `FLAT_IN` 1,07 R (≈ 450 km) `enterFlat(sx, sy)`:
  Sehstrahl → Erdpunkt, Maßstab = Bildschirmhöhe/2 / (tan(fov/2) · Abstand Kamera–Punkt) → `flat.enter(lat, lon, px/km, sx, sy)` → derselbe Ort am selben Pixel, 220 ms
  Überblendung (reduzierte Bewegung: sofort). Zurück, wenn der Kartenmaßstab unter den von `FLAT_OUT` 1,085 R fällt (Hysterese): `leaveFlat` setzt die Kamera über die Kartenmitte
  in passender Höhe und dreht zweimal so nach, dass der Ort unter dem Zeiger bleibt. Knopf „Karte/Globus“; Kamerafahrten (`flyTo`, andere Nacht, Übersicht) verlassen die Karte zuerst;
  Antippen derselben Nacht in der Karte zeigt nur ihre Detailkarte (`M.lastNight`). Lauf-Ansichten Teleskop/Durchgang/Folgen (`zoomMode('orbit')`) verlassen die Karte.
- **Bedienung:** Karte – Ziehen hält den Weltpunkt unter dem Zeiger fest, Rad/Pinch um den Zeiger, keine Trägheit, Zoom bis ~2,5 m/px. Globus – OrbitControls drehen nur noch in den
  Lauf-Ansichten; im Globus-Modus **Greif-Drehen** (Sehstrahl-Treffer beim Anfassen bleibt unter dem Zeiger, Quaternion je Bewegung, Pole begrenzt, daneben im All langsam nach Höhe).
  Mausrad auf der ganzen Bühne (vorher schluckten Schilder es).
- **Ebenen in der Karte:** Erdbild in drei vorab berechneten Fassungen (Tag, Nacht, Nachtsicht – Formeln wie `EARTH_FS`), in 48 Streifen von der Plattkarte nach Mercator; Nachtmaske
  72 × 48 aus der Sonnenrichtung (`destination-in`). Grenzen als Pfade (Ringe nach Umriss-Rechteck verworfen), Heatmap als scharfe Zellen (Farbe/Durchsicht wie `HEAT_FS`; fein ab 1,5 px/km
  eingeblendet, voll ab 6 px/km, grob dann 40 %), Orte, Ort je Nacht (Ring), Vorschläge mit Ellipse, Teleskopstandorte (Quadrat). **Satellitenbahnen nicht** – Bahnen im Raum
  (Hunderte km hoch, ein Umlauf um die ganze Erde) ergäben auf einer ebenen Karte nur verzerrte Linien ohne Bezug zum Bodenort; gezeigt wird, wo das Teleskop stand.
  Kacheln (OSM/OpenTopoMap) nur nach Klick auf „Straßenkarte“/„Gelände“; die alte Kugelkappe mit Kacheln (0.35, eine grobe Stufe als Textur gestreckt – Nutzerbefund
  „Auflösung schlecht“) entfällt. **Zoomstufe** z = round(log2(Z · devicePixelRatio / 256)) → 1 Kachelpixel ≈ 1 Gerätepixel (Faktor 0,7–1,4); in Gerätepixeln gezeichnet,
  auf ganze Pixel gerundet, deckend. Nur sichtbare Kacheln, höchstens `MAX_TILES` 96 je Ansicht (sonst eine Stufe gröber – 48 drückten bei dpr 2 die Stufe), LRU-Cache 400
  im Speicher, Ladevorgänge außer Sicht abgebrochen, keine Vorab-Downloads, Quellenangabe sichtbar (Nutzungsregeln OSM/OpenTopoMap; einen eigenen User-Agent kann der Browser
  nicht setzen). Bis feine Kacheln da sind, steht der nächste geladene Vorfahr (≤ 4 Stufen) als Platzhalter, dann 200 ms Überblendung (`S.again` hält das Zeichnen am Laufen).
  Globus-Textur: Anisotropie = Maximum der Karte, trilineare Mipmaps.
- **Panel in der Karte** (Nutzerwunsch „Optionen rechts in die Karte integrieren, damit diese mehr Platz nutzt“): ab 861 px hängt `component` die Seitenleiste
  `.ste-side` in die Bühne (`.ste-over`, Glas, rechts, einklappbar über `.ste-paneltg`, localStorage `skytrace.erde.panel`); Detailkarte und Maßstab weichen nach links aus
  (`.panel-open`). Darunter (`matchMedia`-Wechsel) zurück unter die Karte.
- **Anzeige:** `.ste-readout` = Maßstabsleiste (1–2–5-Stufen, 60–150 px) + Koordinaten unter dem Zeiger auf 0,01° (auch auf dem Globus unter 1,6 R).
- **Leistung** (06.11.2026, 1440 × 900): Karte zeichnen 0,9 ms (GPU) bzw. 5 ms (SwiftShader), Verschieben/Zoomen 60 fps in beiden; Zoom bis zur Karte inkl. Überblendung 17–19 ms je Bild.
- Tests: `test_skytrace_erde_ui.py::test_kartenmodus_kugel_und_flache_karte` (375 mit reduzierter Bewegung, 1440: Übergang am Zeiger ≤ 0,05°, 1:1-Verschieben exakt, kein Nachlauf,
  Maßstab/Koordinaten, Antippen → Karte der Nacht, Kacheln erst nach Klick, zurück zur Kugel), `test_kartenkacheln_passend_zur_aufloesung` (dpr 1/2, Kachel-Attrappe mit z/x/y: Stufe passend,
  ≤ 96 Kacheln, nichts doppelt, Quellenangabe). `host._ste.state().flat`, `_ste.flat.at/screenOf`.

## Satellit anklicken – Infokarte und Bildausschnitt (0.45.0, 07.11.2026, Nutzerwunsch „auf so einen Satelliten klicken können und Infos zu diesem bekommen plus den Ausschnitt vom Bild, das ich gemacht habe, wo er zu sehen ist“)
- **Antippen im Bildschirmraum** (`skytrace-3d.js` `near`, Hook `near` in `globe()`): je Treffer Stützpunkte `polys` (Bahn ±5 min jede 3., Umlauf jede 2., Durchstoß, Punkt
  selbst), projiziert, Abstand zu Punkt/Segment; hinter der Erde (`behind`) zählt nicht. Radius Maus 14 px, Stift 18, Finger 26 (`pointerType`). Ein Treffer → `select`,
  mehrere → Auswahl `.ste-pick` (≤ 8, Pfeiltasten/Enter/Esc, Fokus per `setTimeout` nach dem Klick). Erst danach die alten Raycast-`pick`-Hooks (Teleskop, Ziel, Orte).
  Überfahren (nur Maus, einmal je Bild): `setHover` → `emph` (Linien-Basisdeckkraft ×2,4, Punkt ×1,6 über `G.refresh` = `applyK`), Namensschild `.ste-tip`, Zeiger `.ste-hot`.
  Die Bühne darf nie scrollen (`scroll`-Wächter) – sonst verschob ein Fokus/`scrollIntoView` im Menü die ganze Karte.
- **Statisch (Nächte & Standorte)** öffnet ein Satellit jetzt seine Karte statt der Nachtkarte; Teleskop/Ziel weiter `onRunPick`. Kandidat öffnen über
  `onCandidate` bzw. `SkyTraceHelpers.openCandidate(data.run, key)` – dafür liefert `run3d` jetzt `run` (Laufname).
- **Flache Karte:** `orbit3d.build` gibt je Satellit `ground` (geodätisch, `erfa.gc2gd`, jede `GROUND_EVERY` = 5. Stützstelle → 10 s, 61 Punkte ≈ 1 KB) und `ground_step`.
  `runLayer().ground()` → `deps.satellites()`: gestrichelte Bodenspur (Länge stetig über die Datumsgrenze, **verankert am Punkt beim Durchgang** in der Weltkopie nächst der Mitte wie alle Marken – an `pts[0]` verankert lag er bei Kartenmitte nahe dem Gegenpunkt genau eine Weltbreite daneben, CI-Befund), Raute = Punkt unter dem Satelliten beim Durchgang, Schild nur bei
  Hervorhebung oder ≤ 3 Satelliten. Abstand auch zur Spur (`distTo`), Satelliten vor Orten/Nächten, mehrere → `{kind:'multi'}` → dieselbe Auswahl. (0.37 zeigte bewusst keine
  Bahnen im Raum auf der Karte – das bleibt so; die Bodenspur ist eindeutig ein Erdort.)
- **Karte** (`card` in `runLayer`): Grundzeilen sofort aus `run3d` (Durchgang, Höhe/Entfernung/Geschwindigkeit beim Durchgang bzw. zur Zeitleiste, Umlauf, Neigung,
  Bahnelemente, Blickfeld, Abgleich); gleiche Auswahl beim Abspielen erneuert nur `.st3d-rows` (Ausschnitt flackert nicht). Details erst beim Anklicken:
  `GET /api/skytrace/satellite?run=&key=<Ordner>/<K>&norad=` (`satdetail.details`, Antwort ~2 KB, im Browser je Satellit gemerkt, solange die Lauf-Ebene lebt – neue Ansicht/Laufwahl lädt neu, nach einem Neuabgleich also nichts Veraltetes; Review PR #85) → Ausschnitt + Zuordnung oben, Katalogzeilen unten.
- **`satdetail.py` (nur Gespeichertes, kein Netz):** Kandidat über `videos.json` (`find`, K-Nummer `^[A-Za-z0-9_-]{1,40}$`, Ordner muss eine Beobachtung sein);
  SATCAT aus `catalog_cache/archives/satcat.csv` (eigener kleiner Leser, `lru_cache` je Dateistand; Spalten Name/COSPAR/Art/Betreiber/Start/Wiedereintritt/Bahn), sonst
  `satcat-names.json`, sonst COSPAR aus TLE-Zeile 1 (`intl_from_tle`). Art: Konstellation aus dem Namen (`kind_of`), sonst SATCAT-Typ. Winkelgeschwindigkeit = Spurlänge ÷
  Durchgangszeit der Bahn (`passage_seconds`), sonst Untergrenze aus `evidence`/`plausibility` („≥“). Helligkeit exakt wie die Statistik (`satellites.phot_of` des
  angeklickten Treffers, `pick_zp` mit Nacht/Gerät aus `stats._run`, Dienst `night_zero_points`). **Zuordnung** `certainty`: `alternative` (nicht der beste Treffer),
  `unsicher` (`site_widened`), `mehrdeutig` (Zweiter wie `sitefit._sure` fast gleich gut, Namen in `others`), `eindeutig` (≤ 60″, Abdeckung ≥ 80 %, ≤ 2 s außerhalb), sonst
  `wahrscheinlich`; dazu Fakten (Abstand, Abdeckung, Richtung, Zeit). Externe Links CelesTrak/N2YO nur als `<a rel="noopener noreferrer">`.
- **Ausschnitt:** Quelle = Ausschnitt-PNG des Trefferbilds (`frames[hit].image` + `overlay.points`, bei fehlender Linie `context_image.points` − `crop`), sonst Vollbild
  (`full_image`/`context_image`). `crop_box`: Spur + Rand (≥ 24 px bzw. 12 %), ≥ 160 px, Seitenverhältnis 1,4, im Bild; ≤ 560 px, Streckung Perzentil 1/99,8, γ 0,85, JPEG 82
  (~30–60 KB). `GET /api/skytrace/satellite/crop?run=&key=` liefert ihn; Zwischenspeicher **`kandidaten/<K>/satellit-ausschnitt.jpg`** (fester Name, Kennung `v<CROP_VERSION>-<mtime>-<box>`
  als Kommentar hinter dem JPEG-Ende, atomar ersetzt; schreibgeschützter Lauf → nur ausliefern). Die Karte legt zwei gestrichelte Linien (±9 px) beidseits der Spur
  (SVG über dem Bild); Klick öffnet den Kandidaten im Lauf. Fehlt alles → `crop: null` + `crop_note` (Hinweis auf „Zeitreihen aktualisieren“).
- Prüfwerte: `host._st3d.screen()` (je Treffer ein sichtbarer Bildschirmpunkt), `_st3d.sel()`, `_ste.satScreen()`, `_ste.satSel()`, `_ste.chooser()`, `_ste.state().flat.sats`.
- Tests: `test_skytrace_satellit.py` (Details, Stufen, ohne SATCAT, Pfadprüfungen, Zwischenspeicher, Bodenspur gegen astropy), `test_skytrace_erde_ui.py::test_satellit_anklicken`,
  `::test_satellit_in_der_flachen_karte`, `::test_satellit_antippen_handy` (zwei Satelliten auf derselben Bahn → Auswahl; Details lazy; Ausschnitt → Kandidat).

## Alternativen zum zurückgerechneten Ort (0.38.0, 07.11.2026, Nutzerfrage „Wieso wird hier nicht <Listenort> vorgeschlagen?“)
- **Anlass** (nur gelesen): M108 2022-05-17, 3 Spuren, Vorschlag `fit` ± 115 m, 3/3, quer 6″. Ein Listenort (Genauigkeit 30 m, Koordinaten aus der
  Adresssuche) lag **372 m** daneben und hatte streng ebenfalls 3/3, quer 113″ – nicht vorgeschlagen, weil die Einrastgrenze max(300 m, 2 × Genauigkeit)
  300 m ist. Gleiches Muster in fünf weiteren Nächten (386–673 m, gleich viele Spuren) → ein Listenort liegt einige hundert Meter neben dem Aufstellort.
- **Auswahl** `rematch.alternatives(night, sites)` (Server, reine Funktion; `with_alternatives` ergänzt `GET /sitesearch/nights` → `alternatives`, `near` – nach der
  **aktuellen** Ortsliste, also auch für ältere Suchen; die Datei bleibt unverändert): nur `fit`/`global`, nicht übernommen; Listenort aus `tested` mit
  Abstand ≤ max(2 km, 5 × `accuracy_m`), eindeutige Spuren ≥ Vorschlag − 1 (und ≥ 1), quer-RMS ≤ max(60″, 3 × Vorschlag) **+ 0,3″ je m Abstand**.
  Entscheidung zum Zusatz: Der Auftrag nannte „≤ 60″ oder ≤ 3× Vorschlag“ – damit wäre der Anlassfall (113″ bei 6″) **nicht** erschienen. Ein Ort, der x m
  neben dem Aufstellort liegt, hat bei erdnahen Satelliten (~700 km) zwangsläufig ~x/700 km quer (372 m ≈ 110″); er ist dann nicht schlechter, nur
  verschoben – genau der Fall zum „Verschieben“. Höchstens 3, sortiert nach Spuren, dann Abstand. `near` = nächster Listenort mit `distance_m`/`limit_m`
  (Vorschlag `snap_limit_m`, sonst 300 m bzw. 2 km) für den Begründungstext, nur ≤ 10 km.
- **Engine** (`rematch_sites.night`): bei `fit`/`global` setzt sie `snap_limit_m` und prüft nahe Listenorte, die noch nicht in `tested` stehen (Weg „genug
  Treffer → nur verfeinern“), streng nach (`alternative: true`). So erscheint auch der **bisherige Ort** selbst als Alternative, wenn er ein paar hundert Meter
  daneben liegt – dort zeigt die Oberfläche nur „verschieben“ (Zuordnen wäre ohne Wirkung).
- **UI** (`altHtml`, `nearText`, `applyAlt`, `moveSite` in `skytrace.js`, über `SkyTraceHelpers` auch in der Detailkarte der 3D-Erde): „<Ort> für diese Nacht
  übernehmen …“ = `applyNights` mit `kind:'list'`. „<Ort> hierher verschieben …“: Dialog mit Nutzung des Orts (Standard-Ort, Zeiträume außer der Einzelnacht,
  weitere Nächte dieser Suche mit bisherigem Abgleich) → `POST /sites` (Koordinaten = Vorschlag, Genauigkeit = max(30 m, Vorschlag), Höhe bleibt, Notiz mit
  Datum und alten Koordinaten – nur lokal), Nacht dem Ort zuordnen (außer er ist schon der bisherige), „übernommen“, `ackSites`, `POST /rematch {all, force:false}`
  (alle Läufe, nur Spuren mit geändertem Ort). Besser/schlechter je anderer Nacht wird nicht vorab gerechnet (bräuchte je Nacht einen strengen Abgleich);
  der Dialog zeigt deren bisherigen Abgleich, der Neuabgleich zeigt das Ergebnis.
- **Karte:** `nightMarks` → `alts` (Koordinaten aus der Ortsliste); Nachtkarte (SVG/Leaflet), Globus (`LineDashedMaterial` + Schild „Alternative: …“) und flache Karte
  (`marks().alts`, gestrichelt mit Abstand) verbinden Vorschlag und Listenort gelb gestrichelt.
- Tests: `test_skytrace_alternatives.py` (Auswahl, Grenzen, 5σ, nur fit/global, API ergänzt ohne Datei zu ändern; Engine: bisheriger Ort ~600 m daneben wird
  nachgeprüft und Alternative), `test_ui_layout.py::test_standort_alternative` (Knöpfe, Grund, Dialog mit „2 Zeiträume“, Karte), `test_skytrace_erde_ui.py::
  test_alternative_in_der_erde` (Detailkarte, Schild, flache Karte); Fixture-Nacht `2022-05-17` (erfundene Orte).

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
| `python/archive_errors.py`, `python/visibility.py` | Gemeinsame Archiv-Ausnahme `ArchiveAccessError` (Status needs_archive, not_checkable …) und geometrischer Horizontfilter |
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
- **Flugzeuge:** Kein automatischer Flug-Abgleich. Die OpenSky-Trino-Anbindung (Archivzugang nur für Forschung/Institutionen) wurde in 0.45.2 entfernt; `aircraft_check` nutzt nur eigene Flugdaten (`flight_file`/`flight_url`, CSV timestamp, icao24, latitude, longitude, geo_altitude, optional callsign), sonst Status `needs_archive`. Alte Einstellungen `opensky_user`/`auto_aircraft` und `catalog_cache/opensky-connection.json` werden ignoriert (`load_settings` entfernt die Schlüssel). Die Erkennung von Flugzeugen über Bewegung/Modell bleibt.
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

- **Heatmap in der 3D-Erde (0.43.5):** Deckkraft = Regler unter Ebenen (`localStorage af.skytrace.erde.heatop`, Standard 0,4 = Deckkraft des heißesten Bereichs; Alpha ∝ 0,08 + 0,92·v^1,3, Shader `HEAT_FS` und `drawHeat` in `skytrace-karte.js` gleich). Aus: Häkchen, Knopf „Ausblenden“ an der Legende, „Heatmap ausblenden“ in der Nachtkarte (`setLayer`). Legende nur sichtbar, solange die Ebene an ist.
