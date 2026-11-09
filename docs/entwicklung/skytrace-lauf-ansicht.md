# SkyTrace: Lauf-Ansicht, Video, Neuanalyse, Handy, Auto-Analyse

> Ausgelagert aus `astroflow/modules/skytrace/AGENTS.md` am 09.11.2026 (Aufräumen nach dem Vorbild von PR #18). Befunde und Regeln
> unverändert, Nutzerzitate gekürzt, private Angaben entfernt. Neue Befunde hier ergänzen, nicht in `AGENTS.md`.

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

Ersatzdateien bei `/reanalyse` müssen anhand FITS-DATE-OBS zur gespeicherten UTC passen: gleiche Dateinamen werden
in unterschiedlichen Nächten wiederverwendet. Ohne prüfbare Identität keine Zuordnung allein nach Namen.
**Mehrere gleichnamige Kandidaten (0.31.3, 05.11.2026):** Stellina-Unterordner `01-images-initial`, `02-images-adjust-focus` …
beginnen alle wieder bei `img-0001r.fits`, automatische Läufe tragen zusätzlich ein Präfix `00164_`. `reanalysis.prepare` indexiert den
Ersatzordner rekursiv (`os.walk`, nur gesuchte Namen, Obergrenze `MAX_ENTRIES` 500 000 → Meldung „engeren Ordner wählen“) und wählt
über `pick` unter gleichnamigen Dateien die mit passender `DATE-OBS` (±1 ms). `date_obs` liest nur die ersten Header-Blöcke (≤ 12 × 2880 Byte,
ohne astropy), Ergebnis je Pfad gemerkt. Die Prüfreihenfolge dient nur der Leistung: Ordner früherer Treffer, dann Pfade mit dem Nachtdatum
(Zeit −36…+12 h), dann der Rest – so reicht auch bei einem sehr großen Ersatzordner meist ein Header je Aufnahme. Genau ein Kandidat wird wie bisher streng geprüft
(Abweichung = Fehler „gehört nicht zur ursprünglichen Aufnahme“). Bei mehreren ohne Treffer: „Unter n gleichnamigen Dateien … gehört keine …“.
Der Start nutzt dieselbe `prepare`-Ausgabe; `eingang.json` enthält die aufgelösten Originalpfade (nicht die Kopien). Gemessen an
IC59 2022-08-06 (549 Aufnahmen, Lauf mit Präfix-Namen): Beobachtungsordner 7 s, ganzer Stellina-Datenträger 14 s, 0 fehlend.
Identische Kopien derselben Aufnahme (gleiche Zeit) gelten als gleichwertig, die erste gewinnt. Tests: `test_skytrace_reanalysis.py`.

## Laufdaten fürs Handy klein halten (05.11.2026, 0.31.1)
Befund: Auf einem Android-Handy im Heimnetz luden Lauf-Ansicht und Medien endlos. Ursache war nicht die Medienauslieferung,
denn Range/206, H.264 und relative URLs waren in Ordnung. Ursache war `/api/skytrace/run`: `video_overlays.timeline` schrieb in jedes
Videobild alle Satellitenbahnen fertig umgerechnet (126 Bilder × 32 Bahnen = 25 MB JSON). Jetzt liegen die Bahnen einmal in
`overlay_timeline.satellite_paths` (`start` = erstes Trefferbild), und jedes Bild hat nur `index` und `reference`
(Registrierungsmatrix, auf 7 Stellen gerundet). Umrechnung und Bildfeldtest übernimmt `evidence.satellitesAt`
(Gegenstück in Python: `video_overlays.satellites_at`, für `check_video_overlays.py`). Ältere Antworten mit `frames[].satellites`
gehen weiter. Dazu komprimiert der Kern JSON/Text per `core/gzip.py` (ab 2 KB, nur Text/JSON; Medien und 206 bleiben unberührt).
Ergebnis: 25 MB → 1,4 MB (0,6 MB übertragen). Neue Felder je Videobild nur, wenn sie nicht mit Bilder × Kandidaten wachsen.
Nachstellen: Playwright-Gerät `Pixel 7` + CDP `Network.emulateNetworkConditions` über die LAN-Adresse. Tests:
`test_skytrace.py::test_lauf_kompakt_und_medien_uebers_heimnetz`, `test_skytrace_video_ui.py` (Satellitenbahn im Overlay).

## Analysen löschen (29.10.2026)
`DELETE /api/skytrace/run?id=<Lauf>` → `Controller.delete_run`: nur Ordner unter `Ergebnisse/` mit Laufdateien (`videos.json`,
`bericht.html` oder `messwerte.jsonl`), nicht der gerade laufende; Windows-Sperren (Videowiedergabe) mit einem Wiederholversuch.
Originalaufnahmen liegen nie im Ergebnisordner. UI: Papierkorb auf den Karten (bei Hover), in der Lauf-Ansicht und in der Befehlspalette,
jeweils mit Bestätigungsdialog.

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

## Overlays, Messdetails, Beobachtungsauswahl (Regeln aus dem Original, gelten weiter)
- Beobachtungsauswahl markiert Bildfolgen anhand aller `videos.json` als „Bereits analysiert“ bzw. „Teilweise bearbeitet“ (Zuordnung über `source_folder`, bei alten
  Läufen über die erste Originaldatei aus `frames.jsonl`/`ausgeschlossen.jsonl`; neue Zusammenfassungen speichern `limited`). Wiederholungen bleiben möglich.
- `video_overlays.py` rekonstruiert Dauerspuren samt Sterntransformation und echter `video_seconds`-Achse aus gespeicherten Frames; App und Bericht zeigen sauberes Video
  plus SVG (Schalter, Rückwärtssuche, Vollbild des Containers erhalten). Das heruntergeladene alte MP4 bleibt unverändert. Positionen pro Frame über `reference_to_current`;
  **keine erfundenen Positionen** bei fehlender Registrierung; reine Plate-Solving-Läufe ohne Kandidaten werden unterstützt, ohne erneutes Solving.
- Sichtbarkeit nur mit `AstroEvidence.setOverlayVisible` (`SVGElement.hidden` allein entfernt kein vorhandenes `hidden`). Hauptvorschau erst bei tatsächlich rekonstruierten
  Spuren auf sauberes Bild/Video umstellen. Overlay-Größe vor der Beschriftung an die dargestellte Bildfläche anpassen; bei Bildvorschau Container (Vollbild/Resize) **und**
  Bildladen beobachten (`ResizeObserver` nur auf dem versteckten Video genügt nicht). Normale Ansicht und Vollbild zeigen dieselben sternregistrierten Koordinaten.
- Overlay-Klasse: vorhandene Kataloghinweise, sonst Modellhinweise (`model_hints`) bzw. ursprüngliche Klasse. `refresh_overlay_labels.py <Lauf>` übernimmt alte
  Modellhinweise offline. Satellitenprognosen (`satellite_overlay.py`, gespeicherte topozentrische Bahnen über WCS und Registrierung in Bildpixel) erscheinen für den gewählten
  Kandidaten ab dessen Trefferbild mit Bildnummer; `context_image` bleibt unbeschriftet; `refresh_satellite_overlays.py <Lauf>` ergänzt alte Berichte offline.
- Geschwindigkeit im Overlay kompakt als `≥ …″/s`; `km/h*` nur mit explizit angenommener Entfernung (Mindest-Schätzung = Winkelgeschwindigkeit [rad/s] × Entfernung [km] × 3600).
  Zusatzinfos beim Hover/Fokus bzw. „Messdetails“. Messprofile (`evidence.profile`) sind ergänzende Diagnosewerte aus linearen Bilddaten, kein nachträglicher Detektorwert;
  unscharfe oder unzuverlässig registrierte Nachbarn werden nicht vermessen.
- UI-Zustand: veraltete Requests dürfen keine neuere Auswahl überschreiben; Videoposition nur beim Overlaywechsel derselben Beobachtung erhalten. Tests dürfen keine echten
  Einstellungen (`catalog_settings.json`, `web_preferences.json`) überschreiben.
