# SkyTrace: Standorte, Rückrechnung, Standortsuche, Alternativen

> Ausgelagert aus `astroflow/modules/skytrace/AGENTS.md` am 09.11.2026 (Aufräumen nach dem Vorbild von PR #18). Befunde und Regeln
> unverändert, Nutzerzitate gekürzt, private Angaben entfernt. Neue Befunde hier ergänzen, nicht in `AGENTS.md`.

## Standorte, Genauigkeit und Rückrechnung (0.23.0, 04.11.2026, Standorte nach Datum)
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

## Ausnahme je Beobachtung gilt für die Nacht (10.11.2026, Nutzerwunsch: Ort aus SkyTrace auch für Bilder)
- **Ablage der Orte in SkyTrace:** Ausnahmen je Beobachtung in `catalog_settings.json` → `sites[<Beobachtung>]` (nur dort fehlte der Weg zu Bilder);
  „Übernehmen“ aus Rückrechnung/Standortsuche/Nachtsuche schreibt schon immer über `/api/sites` + `/api/sites/night` in die Ortsliste.
- **Abgleich** `site_sync.py` (Server, außerhalb `engine/`): Nacht je Beobachtung wie die Statistik (`stats._night`: Datum im Stellina-Namen,
  sonst `night_of` der frühesten Kandidatenzeit; ohne Lauf Datum im Namen) → `SiteBook.sync_nights("skytrace", …, claim="skytrace:nachtabgleich")`.
  Mehrere Beobachtungen einer Nacht: ohne Ausnahme zählen sie nicht; Ausnahmen > `MERGE_M` auseinander → keine Einzelnacht (`conflicts`).
  Läuft beim Start (Thread, liest alle Läufe), nach `POST /settings {session, site}` (Antwort `site_sync`: `added/updated/removed`, `user` =
  Nächte mit eigener Zuordnung des Nutzers, `conflicts`; Toast in den SkyTrace-Einstellungen) und nach dem Ende einer Analyse.
  Ohne Dienst `sites` passiert nichts. Folge: Der Ort gilt über die Ortsliste auch für andere Beobachtungen derselben Nacht ohne Ausnahme
  (geänderte Signatur → „neu abgleichen“ wird wie bei jeder Ortsänderung angeboten). Tests: `tests/test_ort_aus_skytrace.py`.

## Neu abgleichen und Standortsuche (0.28.0, 05.11.2026, Befund: Analysen waren nach einer Standortänderung nicht erneut prüfbar)
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

## Standorte aller Nächte (0.29.0, 05.11.2026, Ziel: Treffer finden, indem alle Standorte geprüft bzw. zurückgerechnet werden)
- **Gruppen** (`SiteJobs.start_nights`, Server): `stats.collect` (je Beobachtung nur die neueste vollständige Analyse, Nacht = Datum im Ordnernamen
  bzw. Nacht der frühesten Zeit nach `core/sites.night_of` (Ortszeit, `standorte-himmel.md`); `stats._run` liefert dafür neu `folder`) → `<SKYTRACE_HOME>/standortsuche-gruppen.json` `[{night, observations:[{run, folder, session}]}]`.
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

## Standort ohne Vorwissen (0.32.0, 06.11.2026, Befund: eine Augustwoche 2022 mit Aufstellort ~250 km von jedem Listenort)
- **Anlass:** IC59 2022-08-06 (16 Spuren): am Listenort 0 Treffer, auch mit Uhr ± 60 min; die Suche mit Spielraum reicht nur ≤ 20 km um Listenorte.
  Am wahren Ort (nachträglich angelegt, beim Test aus der Liste ausgeblendet) 12/16 mit normaler Uhr, quer 7–67″.
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
  Karte seit 0.34.0 je Nacht (`skytrace-erde-3d.md`). `applyNights` legt „Unbekannter Ort (Satelliten <Nacht>)“ an, Hinweis auf Höhe/Namen. Nie automatisch.
- **Leistung:** IC59 allein 19 s (Stufe ± 60 s: 1 137 Paare, 4 s Rückprojektion), ganze Nacht 06.08. (32 Spuren) 39 s; ± 20 min ~36 s mehr. `MAX_PAIRS` 60 000
  als Notbremse. Bahnen nur lokal (`orbits_cache_only`), keine SATCAT-Abfragen (`satcat_offline`), Anhalten über die Stopp-Datei (`check_stop`).
- **Befund** (06.11.2026, Kopien der Kandidaten-JSONs, `nights … --global-km 1500`, wahrer Ort aus der Ortsliste ausgeblendet; Abstand zum
  später angelegten Ort, nächster Listenort ~244 km entfernt):

  | Nacht | Spuren | bisher eindeutig | Ergebnis | Abstand | Uhr | Dauer global |
  |---|---|---|---|---|---|---|
  | 01.08. | 12 | 0 (mit −639 s: 5) | `global` 12/12, quer 80″ | 0,68 km | +0,7 ± 0,7 s → −639 s verworfen | 9 s |
  | 03.08. | 4 | 0 | kein Vorschlag (keine Häufung über dem Zufall) | – | – | 6 s |
  | 06.08. | 32 | 1 | `global` 26/32, quer 29″ (IC59 allein 12/16, 25″) | 0,13 km | −4,1 ± 0,5 s | 55 s |
  | 07.08. | 8 | 1 | `global` 8/8, quer 55″ | 0,67 km | −7,6 ± 0,9 s | 5 s |
  | 15.07., 18.07., 17.06. (Kontrolle, zu Hause) | 9 / 9 / 16 | – | Listenort passt (9, 7, 15 eindeutig), Suche läuft nicht | – | – | – |

  Ohne Listenprüfung direkt gesucht fanden auch die Kontrollnächte ihren Heimatbereich (7–15 Spuren). 03.08. (NGC 246/NGC 7023) bleibt offen –
  dort passt vermutlich derselbe Ort; übernimmt man ihn für die Woche, gleicht „neu abgleichen“ die Nacht mit ab.
- Tests: `tests/test_skytrace_global.py` (Engine im Engine-Interpreter direkt ausführbar: Ort 300 km neben dem Listenort, Uhr −300 s, 40 Störbahnen →
  < 5 km, ± 5 s, alle Spuren eindeutig, erst in der ± 20-min-Stufe; verdrehte Spuren → kein Vorschlag; Nachtsuche mit/ohne `global_km`; guter Listenort →
  Suche läuft nicht; Geometrie der Rückprojektion; Server reicht `--global-km` durch). UI: `#/skytrace/standorte` in `test_ui_layout.py` (Nacht mit `global`).

## Alternativen zum zurückgerechneten Ort (0.38.0, 07.11.2026, Frage: Wieso wird hier nicht der nahe Listenort vorgeschlagen?)
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
