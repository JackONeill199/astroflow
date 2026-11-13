# Observatorium (Himmelsatlas, später Horizont und Mehrnächte-Planung)

Nutzerwunsch 11.11.2026: „Ein komplettes Observatorium, das auch mit den eigenen schon gemachten Fotos gefüllt wird, verknüpft mit einer
automatischen Beobachtungsplanung über mehrere Nächte.“ Ersetzt das kleine Panel „Position am Himmel“ der Sage-Objektseiten.
Etappen: 1 Atlas (dieser Stand) · 2 Horizontmaske je Standort + Ansicht „Von hier“ · 3 Planungs-Assistent (Nächte, Geräte, Wünsche) ·
4 Feinschliff (Milchstraße, Export). Lebt im Bilder-Modul (dort liegen Heute Nacht, Katalog und Plate-Solving-Lösungen).

## Etappe 1: Himmelsatlas `#/observatorium[?ra=&dec=&fov=&name=]`
- **Backend** `modules/stacking/observatory.py`, Endpunkt `GET /api/stacking/atlas` → `{photos, catalog}`. Kein Netz, keine Pfade vom Client.
  - `photos`: je Projekt das neueste Ergebnis (`LATEST` wie `project_picture`) mit vier Ecken des angezeigten Bildes in RA/Dec
    (oben links, oben rechts, unten rechts, unten links), Mitte, Bildfeld (`fov` [Breite, Höhe] in Grad), Vorschau-/Bild-URL, `href`.
  - Stacks: `work/p<id>/wcs.json`; Stackgröße aus dem Lösungsschlüssel (`…|HxW`, sonst FITS-Kopf). Das JPG ist meist leicht beschnitten –
    angenommen wird ein **mittiger Ausschnitt** (die genaue Lage per Korrelation wie `services.view_geometry` kostet ~1 s je Bild und den ganzen Stack).
    Anderer Maßstab (z. B. 2×) → ganzes Feld.
  - Importierte Bilder: `picture_solution.json`. Seit diesem Stand speichert `picture_solve.solve_picture` die lineare WCS mit (`wcs`, `observatory.wcs_subset`);
    ältere Lösungen haben nur Mitte/Maßstab/Drehung → TAN mit üblicher Spiegelung (CD-Determinante < 0, Norden oben/Osten links bei `rotation` = 180°).
  - Zwischenspeicher je Projekt `work/p<id>/atlas.json`, Stempel `[ATLAS_VERSION, result_id, mtime der Lösung]`. Rechnung geändert → `ATLAS_VERSION` erhöhen.
  - `catalog`: `deepsky.catalog()` wie „Heute Nacht“ (`src` m/c/a = Messier/Caldwell/ASTAP-Zusatz).
- **Himmelsdaten** (statisch, `static/himmel/`, erzeugt mit `tools/himmel_extract.py`, Quellen und Lizenzen in `NOTICE`):
  `sterne.json` (Yale BSC 5 bis 6,5 mag, flach `[ra, dec, V, B−V]`, hellste zuerst – der Renderer bricht bei der Grenzhelligkeit ab),
  `sternbilder.json` (d3-celestial: Linien, deutsche Namen, Beschriftungspunkt, `rank`). 89 Einträge (Serpens zweigeteilt).
- **Frontend** `static/observatory.js|css`: Canvas, stereografische Projektion um (ra, dec), `fov` = Höhe bzw. Breite der kürzeren Seite
  (0,3°–220°). Osten links, Norden oben. Fotos als Textur über zwei affine Dreiecke (`drawTri`), unter ~12 px nur als Punkt; ab ~360 px
  Bildbreite das volle JPG statt der Vorschau. Katalog bewusst **rund** (Galaxie Ellipse, Nebel gestrichelt, Haufen gepunktet) –
  eckige Rahmen gehören den eigenen Fotos; ein Katalogname entfällt, wenn ein beschriftetes Foto ihn zeigt.
  Überlappende Fotos: `photoOrder` (hinten → vorne) – großes Bildfeld hinten, kleines vorne, das ausgewählte und darüber das
  überfahrene Foto ganz vorne (hervorgehoben); Namen verdeckter Fotos entfallen. Nach einem Pinch setzt der verbleibende Finger die Zieh-Basis neu.
  Ebenen (Fotos, Katalog, Sternbilder, Gitter) und Blickrichtung je Browser (`store` `obs.layers`, `obs.view`).
  Klick → Kontextpanel (Foto: Projekt öffnen + Sage; Katalog: eigene Bilder des Objekts + Sage über `/sage/kb/card`, nur wenn Sage aktiv).
  Leiste „Meine Fotos am Himmel“ fliegt zum Bild. Animationen entfallen bei `prefers-reduced-motion`.
- **Sage-Einstieg**: `sage-wiki.js` → `obsHref(e)` setzt nur einen Link (Feld ≈ 6× Objektgröße, 3–60°), kein Import; ohne Bilder-Modul fehlt der Knopf.
  Mit `name` zeichnet der Atlas eine Marke und wählt das nächste Katalogobjekt (Abstand bis zur halben Objektgröße – SIMBAD- und
  OpenNGC-Mittelpunkte weichen bei großen Objekten ab). Auf schmalen Fenstern öffnet sich die Schublade dabei nicht von selbst.
- Tests: `tests/test_observatory.py` (Ecken/Ausrichtung/Drehung, alte und neue Import-Lösungen, API mit Zwischenspeicher, Himmelsdaten),
  Routen in `tests/test_ui_layout.py`.

## Planer `#/observatorium/plan` (12.11.2026, Schritt 2 nach der Horizontmaske)
- **Backend** `modules/stacking/planner.py`, API `GET/POST/DELETE /api/stacking/plan` (POST = Assistent {nights 1–14, devices 1–2, mode
  new|mix|deep, depth short|normal|thorough – alt `min_hours`, wird zu depth –, watch[], eq[]} → rechnen und in `settings.json` → `observatory.plan` speichern; feste deutsche Fehlermeldungen).
- **Rechnung** je Nacht wie Heute Nacht (`tonight._alt/_limit/_sep/_series/_window/_forecast`): 10-min-Schritte, astronomisch dunkel, Höhe über
  max(30°, Horizontmaske des Ortes der Nacht – `site_for(night)`), Mondgewicht, Bildfelddrehung je Gerät (`devices.weights`, EQ-Modus je Gerät im Assistenten, `wishes.eq`; `nachfuehrung.md`), Wolken nur aus der vorhandenen
  Open-Meteo-Schicht (nur mit Tutor-Einstellung „Wetter“, sonst/später „ohne Wetter“).
- **Bedarf je Ziel und Gerät**: `need.need_hours` – Objektart/Flächenhelligkeit, Optik, Himmelsqualität des Ortes, Faktor aus
  eigenen Stacks, × Gründlichkeit; schon gesammelte Belichtung eigener Projekte zählt mit (`have`). Herleitung: `belichtungsbedarf.md`.
- **Vergabe:** Nacht je Gerät in Halbstunden-Abschnitte (`SLOT_H`); Abschnitte nach größtem Nutzen vergeben (Priorität × min(Gewinn inkl.
  halber freier Nachbarn, Restbedarf), angefangene Ziele ×1,6, zweiter getrennter Block desselben Ziels in einer Nacht ×0,4); das gewählte
  Ziel wächst über benachbarte freie Abschnitte, bis sein Restbedarf gedeckt ist (Haufen 30 min, schwache Galaxie mehrere Stunden am Stück).
  Fortschritt als Anteil am Bedarf (zwei Geräte zählen anteilig). Nie dasselbe Ziel gleichzeitig auf zwei Geräten, Abschnitte mit < 25 %
  Nutzen bleiben frei (Wolken → Ziel wandert in spätere Nächte). Priorität: Wunsch (neu/Mischung/vertiefen), Objektart, Helligkeit,
  Merkliste ×2. Ausgeschlossen: Begleiter eigener Objekte (wie Heute Nacht) und Kometen-Projekte (wandern).
- **Geräte:** feste Liste `planner.DEVICES` (Bildfeld wie `picture_solve.DEVICE_FOV`) + „Gerät aus deinen Aufnahmen“ (`tonight.library`).
- **Oberfläche** `static/planner.js|css`: Reiter Atlas | Planung (`AFplan.tabs`), Assistent mit Chips, Zeitleiste (Breite = Panel, schmal kürzere
  Ränder), Zielliste mit Fortschritt (10 sichtbar, Rest aufklappbar), Klick auf einen Block → Kontextpanel mit „Im Atlas“.
  Atlas: Ebene „Plan“ (gestrichelte Ringe, „geplant · Tag“). Heute Nacht: Abschnitt „Dein Plan für diese Nacht“ (`AFplan.nightSection`).
- Tests: `tests/test_planner.py`.

## Schritt 3: „Von hier“ und Feinschliff (12.11.2026)
- **Atlas „Von hier“** (Schalter Himmel | Von hier, nur mit Standort aus `/api/sites/resolve`): dieselbe stereografische Projektion,
  aber in Azimut/Höhe – Himmelskoordinaten werden je Zeichnung über die Ortssternzeit umgerechnet (`gmst`, `horizontal`; Azimut wächst nach
  rechts = Blick nach Süden mit Osten links; Pseudo-Koordinaten der Ansicht: ra = 360 − Azimut, dec = Höhe). Eigene Ansicht je Blickart
  (`obs.view` / `obs.view.here`, `obs.mode`). Zeitregler jetzt … +16 h (10-min-Schritte), Himmel in der Dämmerung aufgehellt
  (genäherte Sonnenposition `sunRaDec`). Boden = alles außerhalb der Horizontlinie (Maske des Ortes, sonst 0°; `evenodd`-Füllung,
  Projektion fast bis zum Gegenpunkt – `projection(…, cut)`), Himmelsrichtungen knapp über 0°. Fotos, Katalog, Plan-Ringe und Suche
  funktionieren in beiden Blickarten (`fly` rechnet um).
- **Horizont-Vorschlag aus eigenen Aufnahmen:** `GET /api/stacking/horizon-points?site=<id>` (`observatory.horizon_points`): je Objekt
  und Nacht Azimut/Höhe der Bildmitte zur mittleren DATE-OBS-Zeit, Ort der Nacht aus der Ortsliste, ohne Kometen, nur Punkte über 0°.
  Der Editor (`horizonEditor(…, {points})`) zeigt sie als Punkte (rot = von der Maske verdeckt); „An Aufnahmen anpassen“ senkt die Maske
  dort auf 2° unter die Punkte. Ohne Bilder-Modul fehlt die Funktion.
- **Plan als Kalender:** Knopf „Kalender“ im Planer erzeugt im Browser eine `.ics` (ein Termin je Block, UTC; `AFplan.ics`).
- Bewusst nicht: Milchstraße (bräuchte weitere Fremddaten mit Lizenzprüfung) – bei Bedarf als eigener Schritt.

## Übergänge (12.11.2026, Nutzerwunsch „bei den Übergängen fehlen Animationen“)
- Atlas: `crossfade(change)` legt das alte Bild als Standbild (`#obsFade`) über die Karte, zeichnet neu und blendet es aus (Ebenen,
  Blickart, eintreffende Stern-/Atlasdaten; nicht während einer Kamerafahrt – `lastMove`). Blickart zusätzlich mit kurzer Fahrt hinein.
  `fly` dauert je nach Weg/Zoom 320–750 ms; Zoomknöpfe, `+`/`-`, Doppelklick (`zoomTo`) und Pfeiltasten (`nudge`) animiert, Rad/Pinch direkt.
  Zeitregler dreht den Himmel weich zum neuen Zeitpunkt (`setT`). Einstieg mit Ziel (Sage, Planer): weiter draußen beginnen, hineinfliegen.
- Planer: `draw(fresh)` blendet nur neu erscheinende Abschnitte ein (Schritte nacheinander), Zeitleiste wächst je Nacht (`.pl-grow`),
  Fortschrittsbalken füllen sich; Auswahlknöpfe schalten ohne Neuzeichnen; „Abbrechen“ blendet den Assistenten aus (`leave`).
- Horizont-Editor: Vorlagen/„An Aufnahmen anpassen“ gleiten zur neuen Linie (`morph`, Speichern nimmt das Ziel), Punkte blenden ein.
- Alles mit `prefers-reduced-motion`-Ausstieg (CSS und `reduced()` im JS).
## Bahnen im Atlas „Von hier“ (12.11.2026, Nutzerwunsch „Planung visueller“; erst eigene „Kuppel“-Seite, auf Wunsch in den Atlas)
- Für Gärten mit Mauern/Häusern: Ebene **„Bahnen“** (nur „Von hier“, an/aus) zeichnet die Ziele durch die Nacht in die Atlas-Projektion:
  gestrichelt = dunkel, aber tief/verdeckt; kräftig = frei (Planfarbe `AFplan.color` bzw. Akzent); dick = geplanter Block; Marke zur
  Uhrzeit des Reglers, Mond mit Beleuchtung. Angeklicktes Ziel: Stundenmarken (volle Ortsstunden, interpoliert), Kontextpanel.
- Grenze „frei ab“ = max(30°, Horizontmaske) gestrichelt über der Horizontlinie. „Ganzer Himmel“ in „Von hier“ = Blick von oben
  (`ZENITH`, Zenit Mitte, Norden oben, Osten links); `?blick=hier[&date=]` öffnet so mit Bahnen (Links aus Planer, „Dein Plan für diese
  Nacht“, Heute Nacht → Dämmerung und Mond). Zeitregler reicht mit Bahnen über die ganze Nacht (`setRange`).
- **Teleskope** (Plan-Geräte, `dome.devices` + Blöcke): Fadenkreuz am gerade angepeilten Ziel („Vaonis Stellina · bis 23:00“),
  gestrichelter Pfeil von Block zu Block mit Uhrzeit des Schwenks; über den Listen „Ablauf deiner Teleskope“ (Schritt anklicken →
  Zeitregler auf den Blockbeginn, Ziel gewählt).
- Listen unter der Karte (`freeList`): „Jetzt frei“, „Später frei“, „Schon vorbei“, „In dieser Nacht nie frei“ (mit Grund); Klick fliegt hin.
- Rechnung `dome.py` (`GET /api/stacking/dome?night=&date=`): Nacht = Sonne unter −6°, 10-min-Schritte, Ort je Nacht aus der Ortsliste,
  astropy ohne IERS-Download wie der Planer. Ziele: zuerst die des gespeicherten Plans (mit ihren Blöcken), sonst eigene Objekte
  (ohne Kometen), höchstens 80. Je Ziel `alt`/`az`, `free` (Regel wie Heute Nacht/Planer: `tonight._limit` & astronomisch dunkel), `why`;
  Mond. An den Browser gehen nur Name und Maske des Ortes, keine Koordinaten. Zeitfenster sind halboffen [Beginn, Ende).

## Vollbild und Boden (13.11.2026, Nutzerwunsch)
- **Vollbild:** Knopf unten rechts (bei den Zoom-Knöpfen) und Taste `F`, Esc/Knopf beendet. Fullscreen-API auf das ganze Dokument
  (so bleibt das Kontextpanel rechts darüber nutzbar), die Karte füllt per `body.obs-full` das Fenster – ohne API (iPhone) nur die Klasse.
  Im Vollbild ist die Einblend-Animation der Seite aus (`.page.swap` mit `both` bildet sonst einen Stapelkontext unter der Kopfleiste).
  Größe rechnet der ResizeObserver der Karte neu.
- **Boden „Von hier“** (`ground()`, Nutzerentscheidung 13.11.2026): Boden **nur unter 0°**, deckend und fast schwarz (`color-mix` aus Tokens, `readColors`)
  mit minimalem Dunstsaum am Horizont, in der Dämmerung kaum heller; Sterne/Linien/Katalog darunter verdeckt. Der von der **Horizontmaske** verdeckte
  Himmel bleibt Himmel: leicht abgedunkelt, dezent schraffiert, mit klarer Kontur. Die grüne gestrichelte Grenze „frei ab“ (max(30°, Maske)) bleibt.
  Leistung (Messung 13.11.2026, Handy gedrosselt ×4): Boden-/Kuppelschicht gepuffert (`groundLayer`, Schlüssel Ansicht/Ort/Farben/Dämmerung, bei Bewegung direkt), Sterne/Sternbilder unter dem Boden nicht gezeichnet, kleine Sterne als Quadrat, Listen beim Zeitregler verzögert → Zeitregler 30 → 60 fps.
  Kuppel-Eindruck (Nutzerwunsch): Himmelslicht innen am 0°-Rand (`col.skyGlow`), schwacher Glanz oben links (vom Zenit aus), Hauch Spiegelung auf dem Boden.
  Himmelsrichtungen auf dem Boden knapp unter dem Horizont. Marke aus Sage/Planer unter dem Horizont nur blass mit „unter dem Horizont“.

## Geplant (Konzept vom 11.11.2026, Freigabe des Nutzers)
- **Horizontmaske (gebaut 12.11.2026, siehe `standorte-himmel.md`):** Feld, Editor, Wirkung in Heute Nacht. Offen aus Etappe 2:
  Vorschlag aus eigenen Aufnahmen und Atlas „Von hier“ (nach dem Planer, Reihenfolge Nutzerentscheidung 12.11.2026).
- **Etappe 2 (ursprünglich):** `horizon` je Standort im `SiteBook` (36 Höhenwerte, alle 10° Azimut), Panorama-Editor mit Vorlagen, Vorschlag aus den eigenen
  Aufnahmen (Alt/Az zur Aufnahmezeit, lokal); wirkt in Heute Nacht. Atlas-Ansicht „Von hier“ (Azimut/Höhe, Zeitregler).
- **Etappe 3:** Assistent (Nächte, Gerät(e) – zwei parallel möglich, Standard eines –, Neues/Vertiefen, Mindestbelichtung, Merkliste), Rechenkern
  aus `tonight.py`, Wetter nur über `tonight._forecast`. Plan in `settings.json` (Abschnitt `observatory`), Anzeige im Atlas und in Heute Nacht.
