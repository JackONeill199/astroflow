# SkyTrace: Statistik, Satellitenhelligkeit, Erfolge

> Ausgelagert aus `astroflow/modules/skytrace/AGENTS.md` am 09.11.2026 (Aufräumen nach dem Vorbild von PR #18). Befunde und Regeln
> unverändert, Nutzerzitate gekürzt, private Angaben entfernt. Neue Befunde hier ergänzen, nicht in `AGENTS.md`.

## Statistik (`stats.py`, 01.11.2026)
`GET /api/skytrace/statistics` → `stats.collect`: alle Läufe mit `videos.json` (laufende Analyse ausgenommen), je Beobachtung
(`session` + Name der ersten Aufnahme aus `frames.jsonl`/`ausgeschlossen.jsonl`) nur die neueste vollständige Analyse (sonst die
neueste; `repeated` = nicht gezählte ältere). Die erste Aufnahme trennt automatische Stapel derselben Nacht (gleicher Ordnername,
aber nur neue Aufnahmen – Codex-Review PR #15) von echten Wiederholungen. Kandidaten kommen schlank
zurück (nur Felder für `evidence.js classification`, Zeit, Geschwindigkeit, Katalogstatus) – **Einordnung und Zählung macht die
Oberfläche** (`statsView` in `skytrace.js`), damit Zahlen und Kandidatenfilter übereinstimmen. Nacht = Datum im Ordnernamen,
sonst früheste Kandidatenzeit − 12 h. Cache je Lauf nach Dateizeiten (bei rund 120 Läufen 0,4 s erstmals, danach 0,07 s).
Route `#/skytrace/statistik` steht vor der Lauf-Route. Test: `test_skytrace.py`, Ansicht in `test_ui_layout.py`.

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

## Erfolge (`achievements.py`, `static/skytrace-erfolge.js|css`, 03.11.2026, auch ironische, in der Astrofotoszene beliebte)
`GET /api/skytrace/achievements` → `Achievements.overview`, `POST …/achievements/seen`. Liste `ACHIEVEMENTS` (id, Titel, Beschreibung, Symbol, Gruppe,
Kennzahl, Ziel, Spruch) – IDs bestehender Erfolge nie ändern. Rechnung `compute(stats.collect(...))`: Beobachtungen (neueste vollständige Analyse je
Beobachtung, also dieselbe Datenbasis wie die Statistik) werden in Reihenfolge der Lauf-ID durchlaufen, laufende Kennzahlen (Summen, Mengen, Maxima je
Beobachtung); der Erfolg bekommt das Datum des Laufs, in dem das Ziel erstmals erreicht war (**rückwirkend datiert**). Einordnung in Python nach dem
Vorbild von `evidence.js classification` (`classify`): Satellit nur bei Kataloghinweis `compatible`; Starlink/ISS/R/B/DEB aus Name, NORAD-Nummer und
SATCAT-Objektart; „Meteorvorschlag“ = Kandidat mit `kind == meteor` (kein Modellhinweis – im Teleskopbild nur Hinweis); „Nachtschicht“ = Ortszeit des
Rechners 3–6 Uhr; „Saubere Nacht“ = ≥ 100 Aufnahmen ohne Kandidat; Starlink-Zug = Treffer innerhalb 300 s. Nicht umgesetzt (Daten fehlen):
„Spur quer durchs Bildzentrum“ (Kandidaten speichern keine Bildgeometrie in Bildpixeln). Schwellen an echten Läufen geeicht
(141 Beobachtungen: 164 Satelliten, 84 Starlink, max. 8 je Beobachtung, Zug ≤ 4, 25 Raketenstufen, 10 Trümmerteile → 42 von 60 freigeschaltet).
Zustand `<SKYTRACE_HOME>/erfolge.json` (`unlocked{id: {unlocked, seen}}`): einmal vergeben bleibt vergeben (auch wenn ein Lauf gelöscht wird),
das Datum ändert sich nie. Erste Datei = alles rückwirkend und „gesehen“ (keine Feier-Flut); später neu erreichte sind ungesehen (`status.achievements_new`
→ Hinweis über `toast`, Karte mit „Neu“ und Animation, nach 6 s bzw. Verlassen der Seite als gesehen markiert). Ein Hintergrund-Thread (`Module._achievements_loop`, unabhängig vom Browser) stellt beim Start den Ausgangsstand her und stößt die Prüfung an,
wenn eine Analyse endet. Frontend hängt sich per `AF.modules.skytrace` ein (Route, Navigation, Palette, Status) – `skytrace.js` blieb unverändert.
Optionaler Dienst `ctx.services["skytrace_achievements"]` (= `overview`) für andere Module; Sage nutzt ihn bisher nicht. Ton: freundlich-ironisch,
keine Firmen-/Personennamen als Zielscheibe (Betreibernamen nur als Fakt aus dem Katalogtreffer). Test: `tests/test_skytrace_achievements.py`.
