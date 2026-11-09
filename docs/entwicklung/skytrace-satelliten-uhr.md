# SkyTrace: Satellitenabgleich, SATCAT, Uhrversatz

> Ausgelagert aus `astroflow/modules/skytrace/AGENTS.md` am 09.11.2026 (Aufräumen nach dem Vorbild von PR #18). Befunde und Regeln
> unverändert, Nutzerzitate gekürzt, private Angaben entfernt. Neue Befunde hier ergänzen, nicht in `AGENTS.md`.

## Satellitenabgleich: Zeitbezug und Uhrtoleranz (01.11.2026, Befund „Satelliten werden nie zugeordnet“)
- **Befund** (alle damaligen Läufe unter `Ergebnisse/`, Prüfskript-Idee: Bahn der nächsten Diagnose ±60 s propagieren und die Zeit
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
  **Bewusste Ausnahme** von „keine weltweiten Vollarchive“ (Entscheidung 01.11.2026): Namenskatalog ohne Ort/Zeit, ersetzt
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
  gespeicherten Ort – mit neu aufgelöstem Ort und aus der Oberfläche: `rematch_sites.py`, s. `skytrace-standorte.md` → „Neu abgleichen und Standortsuche“).
  Ergebnis M31 2022-06-11 (`2026-11-01_12-19-46-886388`): 12 von 13 Spuren zugeordnet (Starlink-Züge, Iridium 914, SL-3/SL-16 R/B), vorher 0.
  M103 K0001/K0002 (früher als „84′/28′ entfernt“ beschrieben) = STARLINK-1484/-1062.
- Uhrversatz und Standort je Nacht: siehe `skytrace-standorte.md` → „Standorte, Genauigkeit und Rückrechnung“ (0.23.0). Die konstant 10–18′ quer daneben
  liegenden Nächte passen zu einem um ~1–6 km falschen Standort. Test: `tests/test_skytrace.py`
  (läuft nur mit skyfield/sgp4, also im Engine-Interpreter).

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
- **Nachtrag 0.32.0:** Der Vorschlag −639 s für den 01.08.2022 war falsch – das Teleskop stand an einem anderen Ort, ~250 km vom nächsten Listenort. Am wahren Ort (Suche ohne
  Vorwissen, `skytrace-standorte.md`) passen 12 von 12 Spuren mit normaler Uhr (+0,7 ± 0,7 s). Ein Uhrversatz vom falschen Ort ist also kein Beleg für eine falsche Uhr.
