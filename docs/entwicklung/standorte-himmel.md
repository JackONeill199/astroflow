# Standorte und Himmelsqualität

> Ausgelagert aus `AGENTS.md` am 09.11.2026, Text unverändert übernommen. Regeln, die beim Ändern von Code gelten,
> stehen zusätzlich in Kurzform in `AGENTS.md` → „Wenn du … änderst“. Neue Befunde hier ergänzen, nicht in `AGENTS.md`.

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
  `site_info`), SkyTrace `resolve_site` (s. `docs/entwicklung/skytrace-standorte.md`). UI: `static/sites.js|css` (Einstellungen → Standorte, Abschnitt des Kerns
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
- **Saubere Nächte** (seit 0.39.0, Nutzerwunsch „wo und wann waren die saubersten Nächte“; `skyquality.clean_nights`, `GET /api/stacking/sky/clean`, Abschnitt oben in `#/himmel`,
  `cleanSection` in `skyquality.js`): reine Auswertung von `sky_nights`, nichts wird gemessen. Eine Nacht je Ort = Median ihrer Objekte. Dunkelheit (`sqm`, nur Ortswert ≥ `MIN_GOOD`) ist die
  Hauptwertung; `sure` = Fehler ≤ `SURE_ERR` 0,35 (sonst Liste `uncertain`, getrennt). Seeing/Durchsicht (`transparency` = Verlust in mag) zählen auch aus Nächten ohne Ortswert in ihren eigenen
  Listen. `score` 0–100 = 60 % Dunkelheit, 20 % Seeing, 20 % Durchsicht (Rang unter den Nächten mit Ortswert, fehlende Teile renormiert). `window` = bester Abschnitt (Median aus 5 Kurvenpunkten ohne
  Flags, Uhrzeit im Browser). Wochentag/Monat vom Nacht-Datum (= Abend). Heatmap Ort × Monat = Median der Ortswerte über alle Jahre, `best_month` bevorzugt Monate mit ≥ 2 Nächten, bester Ort
  bevorzugt ≥ 3 Nächte. Bewusst auf `#/himmel` statt im Analyse-Dashboard (das Dashboard hat andere Sitzungen im Umbau; Himmel-Daten gehören ohnehin hierher).
- Tests: `test_skyquality.py` (synthetisch, erfundener Ort; `test_clean_nights`), UI-Fixture `_sky_seed`, `test_saubere_naechte_himmel`.
