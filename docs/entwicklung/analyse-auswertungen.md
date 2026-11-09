# Analyse – HR-Diagramm, Asteroiden, Gerätediagnose, Dashboard

> Ausgelagert aus `AGENTS.md` am 09.11.2026, Text unverändert übernommen. Regeln, die beim Ändern von Code gelten,
> stehen zusätzlich in Kurzform in `AGENTS.md` → „Wenn du … änderst“. Neue Befunde hier ergänzen, nicht in `AGENTS.md`.

## HR-Diagramm von Sternhaufen (`modules/stacking/hrd.py`, `static/hrd.js|css`; seit 0.41.0, Wunsch 9)
- **Ablauf** wie Tiefe/Vergleich über `insights.py` (Art `hrd`, Ergebnis `results.stats.hrd` mit `v` = `HRD_VERSION`, nicht in `_slim`),
  Endpunkte `GET|POST /results/{id}/hrd` (GET liefert zusätzlich `haufen_art` = `hrd.is_cluster(kind, name)`). `after_develop` misst nach der
  Tiefe automatisch, wenn das Objekt ein Haufen ist (`open_cluster`/`globular` oder Name in `CLUSTER_NAMES`, z. B. M45 – im kleinen Katalog
  „reflection“).
- **Fotometrie:** gleiche Bausteine wie `depth.py` (`_ring_phot`, `_empty_sky`, `_fwhm`, `stackfix.spots`), Apertur 1,0 FWHM (Farben brauchen den
  Kern), an allen Gaia-Positionen; Luminanz zuerst (SNR ≥ `SNR_MIN` 10), dann R/G/B nur für diese. Mischlicht: hellerer Nachbar (g < g+2) näher als
  r + 1 FWHM oder deutlich hellerer (g < g−1) im Ring. Übersättigt = Spitze/Fluss < 0,7 × Median heller sauberer Sterne (SNR > 200) – nicht das
  99,99-%-Perzentil wie bei PCC (verwarf synthetisch die hellsten unversehrten Sterne).
- **Eichung:** Instrumentenfarbe −2,5·log(B/R) → BP−RP als robuste Gerade (`_robust_line`, 3σ, ≥ `MIN_CAL` 15 Sterne mit SNR > 30), danach
  G − m_inst = ZP + k·Farbe. Ergebnis `eichung` (Steigung, Streuung Farbe/Helligkeit, Farbterm). Die Punkte im Diagramm sind eigene Messungen auf
  der Gaia-Skala; `gaia` = Gaias Werte aller Mitglieder (Hintergrund), `feld` = gemessene Nicht-Mitglieder (Stichprobe).
- **Mitglieder** (`members`, nur Gaia-Astrometrie, G < 19,5): Haufenmitte/-radius aus dem Katalog (`resolve_name`/`resolve_extended`), sonst
  Bildmitte und halber Feldradius. Dichte in der Eigenbewegung (Nachbarn ≤ 1 mas/a) innen minus Umfeld × Flächenverhältnis → Startpunkt,
  dann Median/Fenster 6× verfeinern (Fenster 3·1,18·robuste Streuung, 0,4–5 mas/a), Parallaxe gewichtet mit Toleranz
  3·√(σ² + (6 % ϖ)² + 0,03²); Mitglieder nur bis 2 Haufenradien. Flächen innen/Umfeld aus Gleichverteilung über die gültige Bildfläche.
  RA-Differenzen über `dra` (kürzester Bogen, Felder über 0°/360°). Mittel und Unsicherheit der Parallaxe aus derselben Auswahl (Kernsterne
  mit passender Parallaxe, Unsicherheit ≥ Streuung/√n; äußere Mitglieder senken sie nicht – Review PR #66). `verunreinigung` = Umfeld-Treffer je Fläche hochgerechnet; `ok` erst ab 8 Mitgliedern innen und Verunreinigung < 50 %.
- **Deutung (vorsichtig, `estimate`):** Entfernung aus der gewichteten Parallaxe (Nullpunkt −0,017 mas, systematisch `PLX_SYS` 0,015 mas;
  `sicher` ab ϖ/σ ≥ 5). Genäherte Hauptreihe `MS` (Pecaut & Mamajek, sonnennahe Metallizität), Rötung E(BP−RP) per Gitter 0–1,5 mit begrenztem
  Fehler min(|Δ|, 0,8) (Riesen/Doppelsterne zählen gedeckelt), A_G = 1,89·E, Spanne = Bereich mit Fehler < Minimum + 0,03. Abknickpunkt = Median
  der 2.–4. hellsten Hauptreihensterne (Δ −1…+0,6 mag), Alter aus `TURNOFF_AGE` (M_G → log t, grob aus Isochronen) mit Spanne aus ±0,5 mag ⊕
  Entfernung ⊕ Rötung und ±0,15 dex; übersättigte Mitglieder heller als der Abknickpunkt → `hoechstens`. Gegenprobe `gaia_schaetzung` mit Gaias
  Werten. **Kugelsternhaufen:** keine Rötung/kein Alter (metallarm), nur erwarteter Abknickpunkt M_G 3,8 als Linie.
- **UI** (`hrdPanel(host, r, {onFocus, onShow})` in `analysisTab`, Abschnitt `#anHrd` + Inhaltsverzeichnis-Eintrag erst sichtbar bei `haufen_art`
  oder gefundenem Haufen): Kennzahlen, SVG-Diagramm (`hrdSvg`, rechte Achse M_G), Ebenen Gaia/Feld (localStorage `af.analyse.hrd.gaia|feld`),
  Klick auf einen Stern → `viewer.zoomTo`, Einsteigertext in `<details>`. Dashboard: Art `hrd` in `KINDS`/`NET_KINDS`; Nicht-Haufen haben Status
  `na` (zählt nicht in der Abdeckung, nie im Sammelauftrag), die Zeile der Status-Matrix (`sbar`) bezieht sich nur auf Haufen; `_hrd_info` in den
  Zeilen, Rekord `oldest_cluster` „Ältester Sternhaufen“ (nur offene Haufen mit erkanntem Haufen). Diagramm kompakt (SVG 680×340, Fuß mit Erklärung + „Neu messen“ in einer Zeile).
- Tests: `tests/test_hrd.py` (synthetischer Haufen 500 pc, E 0,2, Abknickpunkt M_G 1,3 im erfundenen Feld, Gaia-Attrappe; Feld ohne Haufen;
  alter Gaia-Zwischenspeicher; API/Insights/Dashboard).

## Asteroiden im Bild (`modules/stacking/asteroids.py`, `static/asteroids.js|css`; seit 0.42.0 – Wunsch 10)
Ziel: „Du hast 9 Asteroiden fotografiert, der schwächste 16,2 mag“. Läuft als vierte Art in `insights.Insights` (`kind="asteroids"`,
Ergebnis `results.stats.asteroids`, `v` = `ASTEROID_VERSION`), schaltbar über `insights.small_bodies` (gleiche Einstellung wie der
Kleinkörper-Abgleich im Vergleich). Endpunkte `GET|POST /results/{id}/asteroids`, Ausschnitte `/results/{id}/asteroids/a<n>.png`
(streng geprüft, Ablage `work/p<id>/asteroiden/r<rid>/`); `_slim` lässt `asteroids` aus der gepollten Projektansicht weg.
- **Vorhersage:** Master-Stack (`pl._master`) + `wcs.json` (Schlüssel muss zur Stackgröße passen), je Nacht (≤ `MAX_NIGHTS` 3, die mit den
  meisten Aufnahmen) eine JPL-SB-Ident-Abfrage zur mittleren Aufnahmezeit über `compare.small_bodies` (geozentrisch, MPC 500, Cache
  `work/vergleich/jpl_*` – Schlüssel `sb3|…`, mit `rate_ra`/`rate_dec` („/h, RA-Rate = dRA·cos δ), `kind` a/c und `genau`).
  `predict` rechnet Lage + Bewegung (px/s) im Stack-Raster; fehlt die Rate, zweite Abfrage eine Stunde später. Zeitbudget `JPL_BUDGET` 180 s.
  **Nachschärfen (seit 0.43.2):** Asteroiden kommen nur genähert (erster Durchgang, s. „Vergleich“ in `analyse-tiefe-vergleich.md`). Die Nachweis-Kandidaten (`todo`) rechnet
  `refine_all` einzeln über `comet.horizons(…, command=horizons_command(name))` nach (geozentrisch 500@399, Cache `work/komet/`), Budget
  `HZ_BUDGET` 90 s, je Abfrage `HZ_TIMEOUT` 20 s. **Namen:** SB-Ident schreibt vorläufige Bezeichnungen ohne Nummer in Klammern („(1927 LA)“ →
  „DES=1927 LA;“), nur Ziffern in Klammern sind eine Nummer („(4711)“ → „4711;“), „433 Eros (A898 PA)“ → „433;“ – sonst rechnete Horizons einen
  falschen Körper. Der erste Durchgang liefert `Est. error RA/Dec (″)` → `fehler` (Betrag): ≤ `ERR_OK` 5″ gilt als genau (keine Nachrechnung),
  sonst bleibt eine nicht nachgerechnete Bahn genähert mit Suchkreis `rough_search` = 15″ + 3 × Fehler, höchstens `SEARCH_AS_ROUGH` 60″ (ohne
  Schätzung 60″; Ausschnittgröße je Nacht nach dem größten Kreis) und Hinweis; UI „Bahn genähert“.
  **Randkandidaten** (Review PR #73): `predict` behält genäherte Bahnen auch knapp außerhalb des bedeckten Bildes (`in_field` mit Rand =
  `rough_search`, Abdeckung an der nächsten Bildstelle) als `_edge`. Ohne Rohbilder fallen sie weg; nach dem Nachschärfen genau außerhalb → weg;
  am Ende bleiben sie nur, wenn nachgewiesen oder genau gerechnet im Feld. `naechte[].im_feld` zählt die behaltenen.
  **Standort nie senden:** die Parallaxe (Hauptgürtel ≤ ~6″) fängt der Suchkreis `SEARCH_AS` 15″ ab.
- **Nachweis** (nur mit erreichbaren Rohbildern, `frames.deleted=0`): je Nacht ≤ `SAMPLE` 40 Aufnahmen über die Zeit verteilt, je
  Aufnahme `pl._load` (Kalibrierung + cosmetic wie beim Stacken; gespeicherte Glüh- und Fehlerkarten legt `_sensor_cache` vorher in den Cache,
  sonst wendet `_load` sie nicht an – Review PR #67) → Luminanz (`skyquality.luma`), Registrierung aus den gespeicherten
  Sternlisten (`skyquality.frame_links`, Flussfaktor kr/flux_k0), je Kleinkörper ein Ausschnitt um die vorhergesagte Lage auf dem Stack-Raster
  (`frame_stamp`, Maßstab `resolution_scale` beachtet) minus Master-Stack. `detect` mittelt die Ausschnitte (Shift-and-Add), Fund =
  geglättetes Maximum im Suchkreis ≥ `SNR_MIN` 5σ, **frühe und späte Hälfte** der Aufnahmen je ≥ `HALF_MIN` 2,5σ an derselben Stelle
  (≤ max(2 px, 0,8 FWHM)) und rund (Achsenverhältnis ≤ `MAX_ELONG` 2). Die Hälften-Prüfung trennt Asteroiden von allem, was im
  Ausschnitt wandert: mit falscher Bahnrichtung wurde ein heller Punkt sonst als verschmierte Spur „gefunden“ (Test). Gesucht werden nur
  Körper bis Grenzgröße (`depth.grenze.m5`) + 1 mag (ohne Messung 20,5), höchstens `MAX_DETECT` 25 je Nacht; Gesamtbewegung < 2 FWHM →
  `zu_langsam` (im Stack ein Stern, die Differenz zeigt nichts).
- **Helligkeit:** Aperturfotometrie (Radius = Nullpunkt-Apertur 2·FWHM) auf dem Mittel; Nullpunkt `skyquality.zero_point` (Gaia, V-nah,
  gemeinsamer Gaia-Cache), mag = ZP − 2,5 log(F/t). Der Master-Stack enthält die Asteroidenspur selbst (ungeclippte Anteile) und wird
  mit abgezogen → Korrektur `self_overlap` (Überlapp der Bahn-Gauß-Profile / N_Stack; > 50 % → `zu_langsam`). Ohne Gaia: kein mag, Hinweis.
  Synthetisch (Test): Fluss 900 ADU in 10 s → erwartete Helligkeit ±0,35 mag getroffen, SNR ~70 aus 36 Aufnahmen.
- **Status je Körper:** `nachgewiesen`, `nicht_gefunden`, `keine_rohbilder` (UI „im Feld, nicht nachgewiesen“), `zu_langsam`,
  `zu_schwach`, `nicht_geprueft`. Gezählt (`nachgewiesen`, Dashboard, Sage) werden nur nachgewiesene, je Name einmal.
- **Dashboard:** Art `asteroids` in `KINDS`/`NET_KINDS` (nach dem Vergleich, nutzt die Grenzgröße der Tiefe), Sperre `nosolve` bzw.
  `nosmall`; `totals.asteroids` = verschiedene Namen, Rekord `faint_asteroid`, Liste `asteroids`. **Sage:** Dienst `asteroid_tally`
  (Stacking) → Kennzahl `asteroids` in `log_stats`, Erfolge `asteroid_first`/`asteroids_10`; Ereignis `stacking.asteroids` → `store.refresh`.
- **UI:** `asteroidPanel` (Abschnitt „Asteroiden“ im Reiter Analyse, dicht wie das Dashboard: Satz + Kennzahlen als Leiste, Liste als Tabelle
  mit kleinem Ausschnitt, erst 8 Zeilen), `asteroidOverlay` umhüllt `depthOverlay` im Analyse-Betrachter. Marken über `.seg` „Aus / Nachgewiesen /
  Alle“ (`AST_MARKS.mode` aus|ok|alle, localStorage `bild.asteroiden.markenart`, Standard `ok`; alter Schalter `bild.asteroiden.marken` = false → aus).
  Seit 0.43.3 (Nutzerbefund M16, ~50 Körper, Namen übereinander): beschriftet werden nur nachgewiesene, Namen gierig ohne Überlappung (rechts, links,
  sonst nur Kreis); „Alle“ = übrige als kleine Punkte ohne Text/Bahn (das Overlay hat `pointer-events: none`, Hover-Namen je Marke gehen nicht).
  Klasse `ast-on` nur, wenn der Modus etwas zeigt (`astShows`) – sie blendet sonst die Objektnamen aus. Test: `tests/test_asteroids.py` (`demo.generate_asteroid` =
  wandernder Punkt mit bekannter Bahn, JPL-Attrappe, fester Nullpunkt; Seed in `tests/analysis_seed.py`).

## Gerätediagnose (`modules/stacking/diagnose.py`, `static/diagnose.js|css`; seit 0.43.0, Wunsch 17)
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
- **UI** (Formensprache des dichten Dashboards 0.38.3): `diagPanel(host, r)` (Abschnitt `#anDiag` in `analysisTab`) = Kennzahlenleiste der Nacht
  (`dg-strip`, Zellen mit Trennstrichen) → Dreierreihe Nachführung/Fokus/Bildfeld (`dg-c3`, SVG mit fester viewBox 400 breit) → eine Zeile
  Stöße/Längsachse/Einzelkarten → Gerät über alle Nächte (Leiste + drei Verläufe) → Erklärung eingeklappt; lädt nach Fertigstellung den Tutor neu,
  „Neu berechnen“ per Delegation am Behälter. Dashboard: eigene Zeile „Gerät“ in der Status-Matrix (`KINDS`, lokal, nicht in `NET_KINDS`), Abschnitt `#adDiag` = `diagDevices(list)`.
- Tests: `tests/test_diagnose.py` (synthetische Matrizen/Metriken, Sternfelder mit Kippung/Wölbung, `demo.generate_drift` Ende zu Ende).

## Analyse-Dashboard über alle Objekte (`modules/stacking/dashboard.py`, `static/dashboard.js|css`; seit 0.36.0)
Nutzerwunsch „eigene Seite, wo man alle gleichzeitig analysieren kann, mit Statistiken und Animationen“. Route `#/analyse` (Unterpunkt von Bilder, `PAGE_WIDE`).
- **Aggregat:** `collect(eng)` = je Objekt die neueste Version (gleiche Reihenfolge wie `project_views`), Status je Art `depth|compare|sky|tutor|diagnose`
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
- **Sammelauftrag** `Batch` (eigener Thread, Aktivität „Analyse aller Objekte“, zählt in `busy_jobs`): Reihenfolge `KINDS` = Gerät (`diagnose`, seit 0.43.0), Tutor, Himmel (lokal),
  Tiefe, Vergleich (nutzt den Nullpunkt der Tiefe), HR-Diagramm (nur Haufen), Asteroiden (seit 0.42.0). Tiefe/Vergleich/HR-Diagramm/Asteroiden gehen einzeln durch `insights.submit` und werden abgewartet, danach `GAP_S` 3 s
  Pause → nie zwei Netzauswertungen gleichzeitig. Himmel = `skyquality.compute_project` (wartet, solange `SkyRunner` rechnet), Tutor = `build_report(refresh=True)`
  (mit Wetter ebenfalls Pause). Anhalten wirkt nach dem laufenden Schritt; Zustand nur im Speicher.
- **UI:** Abschnitte zeichnen nur bei geänderten Daten neu (`paint` mit Signatur – sonst liefen Animationen bei jedem Poll erneut); Poll der Shell lädt nur
  während eines Auftrags laufend, sonst alle 15 s. Diagramme als eigene Helfer (`scatter` = SVG mit festem viewBox 400 breit, `sbar` = gestapelter Balken je Art), 3D-Wolke mit three.js aus `/vendor/` (`loadThree`).
  **Dichtes Raster** (seit 0.38.3, Nutzerwunsch „Informationen dichter“): Seite `.ad-page` mit engeren Panels, Kennzahlen als eine Leiste (`.ad-kpis`, nur Mengen –
  Rekordwerte nur unter „Rekorde“), Reihen `.ad-row` (`.ad-top` 2:3, `.ad-c3` dreispaltig → ab 1279 px zwei, ab 760 px eine Spalte, `.ad-chgrow` Veränderungen 3:2 Kataloge),
  Abschnitte einer Reihe gleich hoch. Veränderungen zeigen erst `CHG_ROWS` 8 Zeilen (CSS `.ad-chg:not(.all)`), „Alle anzeigen“ schaltet ohne Neuzeichnen um.
  Asteroiden (seit 0.42.0): Kennzahl in der Leiste (7 Felder; ≤ 1100 px 4 + 3), Zeile in der Status-Matrix, Rekord „Schwächster Asteroid“ und Abschnitt
  `#adAst` als schmale Tabelle über die volle Breite vor „Alle Objekte“, ebenfalls erst `CHG_ROWS` Zeilen (`.ad-asts:not(.all)`).
  Animationen über `.an-sec.in` (`anReveal`/`anCountUp` aus `analysis.js`), aus bei `prefers-reduced-motion`. Sprung zur Bildstelle: `sessionStorage af.an.focus`
  `{rid, x, y, w}` → `analysisTab` zoomt nach dem Laden des Fotos. localStorage `af.analysis.filter|kinds|sort`.
- Tests: `tests/test_analysis_dashboard.py` (synthetische Bibliothek `tests/analysis_seed.py`, Attrappen statt Netz; prüft u. a. höchstens eine
  Netzauswertung gleichzeitig und Anhalten), Route `#/analyse` in `test_ui_layout.py`.
