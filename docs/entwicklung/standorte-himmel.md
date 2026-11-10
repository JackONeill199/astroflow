# Standorte und Himmelsqualität

> Ausgelagert aus `AGENTS.md` am 09.11.2026, Text unverändert übernommen. Regeln, die beim Ändern von Code gelten,
> stehen zusätzlich in Kurzform in `AGENTS.md` → „Wenn du … änderst“. Neue Befunde hier ergänzen, nicht in `AGENTS.md`.

## Nachtdatum nach Ortszeit (`core/sites.night_of`, Issue #39, Nutzerwunsch 09.11.2026 „die lokale Zeit soll automatisch geholt werden“)
- **Einzige Stelle**, die aus einer Zeit das Nachtdatum macht: `sites.night_of(when, lon=None, tz=None, header=None, book=None)`. Nacht = Mittag bis
  Mittag Ortszeit, Datum des Abends. Ein fertiges Datum `JJJJ-MM-TT` kommt unverändert zurück; Zeiten ohne Zone gelten als UTC (DATE-OBS).
- **Ortszeit automatisch, offline:** `lon` (Grad Ost) → Ort im FITS-Kopf (`header`, `from_header`; Scanner: `FrameInfo.site`) → `tz` →
  Ortsliste (`book` bzw. per `use_book` gesetzt – Server: `create_app`; Engine: `observation_settings.night_book` über `ASTROFLOW_SITES`) →
  Zeitzone des Rechners (`datetime.astimezone()`, Betriebssystem, mit Sommerzeit) → UTC.
  Ortsliste (`night_in`): vorläufig mit dem Standard-Ort; gilt in dieser Nacht oder direkt daneben ein Zeitraum mit anderem Ort (Reise), zählt dessen
  Länge – nur wenn derselbe Ort auch für die damit bestimmte Nacht gilt. `SiteBook.cached_data` puffert die Liste 5 s (viele Aufrufe je Scan).
- **Warum Länge statt IANA-Zone:** Orte haben keine Zeitzone, und Zone aus Koordinaten bräuchte ein großes Zusatzpaket; `zoneinfo` hat unter
  Windows ohne Paket `tzdata` gar keine Zonen. Mittlere Ortszeit lon/15 h kennt keine Sommerzeit und legt die Grenze genau gegenüber der Nachtmitte –
  robuster als bürgerliche Zeit. Die Rechnerzeitzone (bürgerlich, je Aufnahme zum Aufnahmezeitpunkt) ist nur Rückfall ohne Ort.
- **Verträglichkeit (`COMPAT_H` = 3 h):** Weicht die Ortszeit höchstens 3 h von UTC ab (Europa, Afrika, Atlantik, Moskau), bleibt die Grenze bei
  12 Uhr UTC wie bis 0.53 (liegt dort zwischen 9 und 15 Uhr Ortszeit, also am Tag) – in Europa ändert sich kein Nachtdatum (Test über ein Jahr).
  Weiter weg gilt Mittag Ortszeit: USA-West nicht mehr um 4 Uhr geteilt, Japan-Abend nicht mehr am Vortag. Weil jede Grenze am Tag liegt, bleibt eine
  Nacht auch über einen Sommerzeitwechsel eine Nacht.
- **Uhrzeit zur Anzeige:** `sites.clock_of(when, lon=None, book=None)` → „HH:MM“ (Aufnahmen-Diagramm, `/frames` → `clock`). Bürgerliche
  Rechnerzeit mit Sommerzeit, solange der Ort (`lon` bzw. Ortsliste) höchstens 2,5 h daneben liegt, sonst die ganze Stunde von lon/15 – nicht die
  Sonnenzeit wie bei `night_of` (die läge bis ~2 h neben der Uhr). Das Frontend rechnet keine Zeiten um.
- **Gespeicherte Nächte:** `stacking/nights.migrate` beim Start des Bilder-Moduls bestimmt `frames.night` aus DATE-OBS (+ FITS-Ort) neu, nur wenn sich
  die Grundlage geändert hat (Signatur aus Regel `RULE`, `COMPAT_H`, Rechnerzeitzone Jan/Jul, Ortslisten-Längen, Standard, Zeiträume →
  `settings.json` → `nights`). Nur abweichende Zeilen; Aufnahmen ohne DATE-OBS behalten ihr Datum; Kometenprojekte (Nacht im Schlüssel `KOMET:…|<nacht>|…`)
  bekommen den neuen Schlüssel, wenn alle Lights in einer Nacht liegen und er frei ist (nie zusammenlegen); für betroffene Projekte wird `sky_nights`
  verworfen (Himmelsqualität „Fehlende berechnen“). Keine Rohdaten, keine Ergebnisversion. Nachtschlüssel in Einstellungen (Einzelnacht-Zeiträume,
  SkyTrace-`clock_offsets`, Heatmaps) werden nicht umgeschrieben: außerhalb Europas stimmt das alte Datum mit dem neuen für den Hauptteil der Nacht
  überein (USA: Abend bis 4 Uhr, Japan: ab 21 Uhr), nur der Rand wechselt.
- **Nicht betroffen:** `planning_night` (Heute Nacht plant schon nach mittlerer Ortszeit, Grenze 6 Uhr), Stellina-Ordnerdatum in SkyTrace (`stats._night`).
- Tests: `tests/test_nachtdatum.py` (Europa ein Jahr unverändert, USA-West eine Nacht, Japan-Abend, Sommerzeitwechsel, Rückfall, Reise, Migration).

## Standorte (`core/sites.py`, seit 0.23.0, Nutzerwunsch 04.11.2026 „Teleskop oft an verschiedenen Orten, Standorte nach Datum“)
- **Ablage:** `data/settings.json` → Abschnitt `sites` = `{list: [{id, name, lat, lon, alt_m, accuracy_m, note}], default, periods: [{id, site_id,
  from, to}], migrated: {quelle: zeit}}`. `to` leer = offen; `from == to` = einzelne Nacht („heute bin ich in …“, Nachtliste). Nacht-Datum über
  `night_of` (siehe „Nachtdatum nach Ortszeit“). Überlappende Zeiträume: der kürzeste gewinnt. `accuracy_m` = Radius ~1σ (Standard 100 m, FITS 30 m).
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
- **Automatisch gepflegte Einzelnächte** (10.11.2026, Nutzerwunsch „in SkyTrace zugewiesener Ort soll für die Nacht auch in Bilder gelten“):
  `SiteBook.sync_nights(src, {Nacht: Ort}, claim)` legt Einzelnächte mit Marke `src` (+ Kurztext `why`) an, stellt sie um oder entfernt sie;
  Einzelnächte **ohne** `src` gehören dem Nutzer und bleiben (auch beim Umstellen in der Nachtliste verliert eine Nacht ihre Marke → gehört
  dann dem Nutzer). Orte ≤ `MERGE_M` werden wiederverwendet, neue Orte tragen `src` und verschwinden, wenn sie nirgends mehr gelten.
  `claim` (einmal, Marke in `migrated`) übernimmt frühere Einzelnächte ohne Marke mit gleichem Ort. `resolve` liefert dann `by` (= `src`) und
  `why` im `detail`; `/api/sites` → `nights[].fixed_by`, Einzelnächte ohne Aufnahmen erscheinen trotzdem in der Nachtliste („Ort aus SkyTrace“).
  Quelle SkyTrace: `modules/skytrace/site_sync.py` (`docs/entwicklung/skytrace-standorte.md`).
- **Ereignis `sites.changed`** (`SiteBook.on_change`, gesetzt in `create_app`; bei `save`, `set_night`, `absorb`, `sync_nights` mit Änderung):
  Bilder (`stacking/site_watch.py`, 2 s gebündelt) rechnet Nächte mit anderem Ort neu – Himmelsqualität über `sky_nights.data.site_key`
  (ID + Koordinaten + Höhe; ältere Zeilen: Orts-ID) → `SkyRunner.queue`, Gerätediagnose über `nights[].site` (ältere ohne Ort nur bei
  automatisch gepflegter Einzelnacht) → `insights.submit("diagnose")`. Tutor: Ort steht schon im Cacheschlüssel. Keine Versionserhöhung nötig.
  `ASTROFLOW_SKY=0`/`ASTROFLOW_DIAG=0` schalten das ab (Tests). Tests: `tests/test_ort_aus_skytrace.py`.

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
