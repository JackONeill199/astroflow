# SkyTrace: Erkennung (Liniendetektor, kurze Spuren, Verkettung, Modellhinweise)

> Ausgelagert aus `astroflow/modules/skytrace/AGENTS.md` am 09.11.2026 (Aufräumen nach dem Vorbild von PR #18). Befunde und Regeln
> unverändert, Nutzerzitate gekürzt, private Angaben entfernt. Neue Befunde hier ergänzen, nicht in `AGENTS.md`.

## Erkennung im Teleskop-Bildfeld (28.10.2026, Befund M74 2022-08-07)
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
- **Blinkende Flugzeuge** (`line_detection.blink_pattern`, Hinweis 29.10.2026, **ohne echtes Beispiel**, nur synthetisch
  `check_blink_trails.py` und an den Vergleichsläufen auf Fehlalarme geprüft): Linienprofil mit ≥ 5 hellen Abschnitten auf einer Geraden,
  echte Lücken, konstantem Takt (Abstände = Vielfache der Periode, Median-Abweichung ≤ 12 %; einzelne von Sternen verdeckte Blitze
  erlaubt), Anteil ≤ 70 %, integrierter Kontrast ≥ `BLINK_SCORE` 8, auch im Originalbild; zwei Formen: gestrichelt oder Linie mit
  Knoten. → `kind='plane'`, `blink` (Blitze, Takt in Analysepixeln, Form), Klasse „Flugzeugkandidat (Blinklichter)“; nie Meteor.
  Taumelnde Satelliten können ähnlich aussehen. Kurze helle Striche bleiben in der Hough-Abstimmung und Blinkprüfung frei
  (Maske `dashes`, ab ~12 px gestreckt 16:1); durchgehende Spuren werden weiter mit der strengen Sternmaske bewertet
  (sonst wurden Sternschweife in IC59 Bild 114 zu kurzen Spuren). Mit erstem echten Flugzeugbeispiel nachkalibrieren.
- Prüfen: `tests/test_skytrace.py` (Verkettung, Bildfeld), `check_blink_trails.py`, Engine-Checks `check_banding`, `check_streak_merging`, `check_trail_validation`,
  `check_motion_plausibility` mit `SKYTRACE_HOME` und dem Engine-Interpreter.

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

Für den Differenzvergleich hat die direkt gemessene Registrierung zum Nachbarbild Vorrang vor der Differenz zweier
globaler Referenzlösungen. Letztere können gegeneinander springen (bestätigt: lange blasse NGC-1499-Spur unten links,
Videobild 57). Globale Astrometrie für bewegte Punkte bleibt getrennt. Regression mit künstlich springenden Referenzfits.

## Liniendetektor: Leistung (03.11.2026)
`line_detection._best_section` prüft Abschnitte mit Präfixsummen vor und misst mögliche Gewinner mit der bisherigen
`np.mean`-Rechnung exakt nach (Rundungspuffer, gleiche Reihenfolge/Gleichstände). Geometrie-Cache begrenzt auf 32 Größen;
bei > 128 Abschnitten alte Schleife statt quadratischer Hilfsarrays. Stern-/Blinkmasken und Abtastachse nur einmal je Bild.
Keine geänderten Detektionsschwellen. `tests/test_skytrace_performance.py` läuft mit pytest, sofern OpenCV vorhanden ist,
oder direkt im Engine-Interpreter; vergleicht Grenzwerte und komplette Erkennungen mit der erschöpfenden Suche.
Synthetischer A/B-Test (1040×1536, Sternfeld + Spur): 0,75 → 0,58 s (rund 22 % weniger Zeit), kein Gesamtpipeline-Benchmark.

## Regeln aus dem Original (gelten weiter)
- Helle, zusammenhängende schmale Linien von der Sternmaske ausnehmen. Differenzbild-Spuren brauchen zusätzlich positiven Linienkontrast im aktuellen Originalbild, sonst
  entstehen Nebenloben nach dem Verschwinden einer hellen Spur. YOLO-Teilboxen über Schnitt mit gemessenen Segmenten zusammenfassen (`merge_streak_detections`), nicht allein per Box-IoU.
  Modellklassen bleiben `model_hints`; gemessene Linien bleiben unbestätigte Einzelbild-Spuren. `LINE_SCORE_THRESHOLD` = 10,5 (`line_detection.py`).
- Verwacklungsfilter bei Bildfolgen immer aktiv (Profile von bis zu 80 Sternen; Details `modules/skytrace/docs/Ereignisvideos.md`): Ein einzelner echter Streifen lässt die übrigen
  Sterne scharf und schließt das Bild nicht aus; reine Verschiebung zwischen scharfen Aufnahmen bleibt erlaubt.
- `rebuild_trails.py <Quelllauf> <Ziellauf> [--redetect-lines|--validate-motion|--filter-readout]` rendert separat neu (Quelle und Originale bleiben); danach Kandidatenausschnitte
  neu erzeugen, sauberes **und** annotiertes Video neu, ausgelassene Frames korrekt neu durchnummerieren.
- Sensorfehler: horizontales statisches Sensorrauschen (IC405) wurde einmal als Spur gedeutet – trotzdem nicht pauschal horizontale Linien verbieten; die Korrektur verlangt
  Übereinstimmung unabhängiger Sensorstreifen und beider Ränder.
- Schwache Sternschweife (IC59, Bild 114): `extended_star_tails` misst verbundene Ausläufer jenseits der Kerne; schwache Nebenmaxima eines Schweifs dürfen nicht als zweite
  Referenzsterne zählen; `trail_validation` prüft verteilte Modellvorschläge nur zusammen mit unabhängigen Sternbefunden. Echte NGC884-/M82-Spuren und mehrere gleichzeitige echte
  Linien müssen erhalten bleiben (Vergleichsfälle in `check_trail_validation`, `check_streak_merging`, `check_banding`).
- Winkellängen/Detektorwerte (M103-Fälle) dienen als Messbeispiel; **keine bestätigte Objektidentität**.
