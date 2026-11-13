# Belichtungsbedarf je Ziel und Gerät (`modules/stacking/need.py`)

Frage des Nutzers: Haben Objekte je nach Teleskop Vorrang – ein Sternhaufen braucht keine 2 h, ein dunkler Nebel aber mehr?
Bis dahin verteilte der Planer für alle Ziele dieselbe Mindestbelichtung (`wishes.min_hours`, Standard 2 h). Jetzt schätzt
`need.need_hours(target, device, sqm)` den Bedarf je Ziel und Gerät; Planer und „Heute Nacht“ nutzen ihn.

Die Schätzung ist eine **Planungshilfe**, kein Messwert. Sie endet in Stufen `STAGES` = 0,5 / 1 / 2 / 3 / 5 / 8 / 12 h
(geometrisch nächste Stufe), begrenzt je Gründlichkeit (`DEPTH_CAP`: kurz 3 h, normal 8 h, gründlich 12 h).

## Formel

    Stunden = Grundbedarf × Gerät^e_dev × Himmel^e_sky × Eichung × Gründlichkeit (kurz 0,5 · normal 1 · gründlich 1,6)

### Grundbedarf (Referenz: 50 mm f/4 unter SQM 20)
- **Mit Helligkeit und Größe** (Galaxien, Planetarische Nebel, kleine Supernova-Reste ≤ 20′): mittlere Flächenhelligkeit
  S = mag + 2,5·log10(π/4·(0,8·Größe·60)²) mag/″² (Kreis mit 0,8 × großer Achse, weil die meisten Objekte länglich sind).
  Grundbedarf = 2 h × 10^(0,3·(S − 22)) – je mag schwächer etwa doppelt so lange.
  Physikalisch wäre die Zeit für gleiches Signal/Rauschen ∝ 10^(0,8·S) (zehnmal so lange je mag). Smart-Teleskop-Bilder
  schwacher Objekte gelten aber mit deutlich weniger S/N als „fertig“; der gedämpfte Exponent 0,3 gibt die üblichen
  Erfahrungswerte wieder (Smart-Teleskop-Foren, eigene Stellina-Stacks): M51/M81 ~2–3 h, M101/M33 ~5 h, NGC 891 5–8 h.
- **Ohne aussagekräftige Helligkeit** nach Objektart (`KIND_HOURS`): offener Haufen 0,4 h, Kugelhaufen 1 h, Emissions- und
  Reflexionsnebel 3 h, große Supernova-Reste (Cirrus) 4 h, Dunkelnebel 8 h. Helle Emissionsnebel (M42, M43, M8, M17, M16, M20,
  Carina, Tarantel) haben eigene Werte (`BRIGHT_EMISSION`). Emissionsnebel tragen im Katalog absichtlich keine Helligkeit
  (`deepsky.fixed`), Reflexionsnebel-Helligkeiten gehören zum beleuchtenden Stern.
- **Sternhaufen**: schwache Haufen etwas länger, helle kürzer (× 10^(0,1·(mag − 6)), 0,6–1,6).
- Untergrenzen je Art (`KIND_FLOOR`), z. B. Galaxie mindestens 1 h.

### Gerät
Zeit ∝ f-Zahl / Öffnung = Brennweite / Öffnung² – geometrisches Mittel aus „Licht je Pixel“ (∝ f-Zahl², zählt für flächige
Objekte bei gleicher Pixelgröße) und „Licht je Bogensekunde“ (∝ 1/Öffnung², zählt für Sterne und Auflösung). Referenz
200 mm / (50 mm)² = 0,08. Ergebnis: Stellina 0,78 · Vespera 1,0 · Vespera II/Seestar S50 1,25 · Dwarf 3 1,5 · Seestar S30 2,1.
Begrenzt auf 0,3–3. Öffnung und Brennweite stehen in `devices.json` mit Quelle (`aperture`, `focal`); ohne Optik (eigene
Kamera) wird wie 50 mm f/4 gerechnet und das als Annahme angezeigt.

### Himmel
Hintergrundbegrenzt: Zeit ∝ Himmelshelligkeit = 10^(−0,4·(SQM − 20)), begrenzt auf 0,3–4. SQM kommt aus der
Himmelsqualität des Ortes (`skyquality.for_site`, gemessen in den eigenen Aufnahmen); ohne Messwert gilt SQM 20 (Vorort,
Bortle 5) als Annahme. Die Exponenten mildern, wo der Himmel weniger zählt: Sternhaufen 0,5 (Ziel „Sterne sauber“),
Emissions-/Planetarische Nebel und Supernova-Reste 0,7 (Dual-Band-/Schmalbandfilter der Smart-Teleskope).

## Eichung aus eigenen Stacks (`calibration_rows`, `calibration`)
Die Tiefe-Auswertung misst die Gaia-Grenzgröße m5 (5σ) jedes Stacks. Für 82 eigene Stellina-Stacks ergab ein Ausgleich
m5 = 17,31 + 1,20·log10(t/1 h) – die Steigung 1,2 bestätigt das 1/√t-Rauschen (erwartet 1,25). Daraus die Referenz
`M5_REF` = 17,35 nach 1 h (80 mm, Vorortshimmel). Erwartung für ein Gerät: M5_REF + 2,5·log10(Öffnung/80 mm) + 0,5·(SQM − 20).
Weicht der Median der eigenen Stacks (auf 1 h umgerechnet) davon ab, wird der Bedarf mit 10^(−0,4·Abweichung) skaliert
(halbe physikalische Wirkung, wie beim Grundbedarf), begrenzt auf 0,6–1,6, erst ab 3 Stacks je Gerät (Gerät aus dem
Kopf eines Frames, `tutor.detect_device`; geschätzte Geräte außer Stellina zählen nicht). Der Faktor ist immer „Vorschlag“
und erscheint im Planer am Gerät (Tooltip über „Himmel …“).

## Nutzung
- **Planer** (`planner.needs`, `observatorium.md`): Bedarf je Ziel × Gerät; eigene Projekte bringen ihre Belichtung mit
  (`have`, Belichtung des neuesten Ergebnisses). Halbstunden-Abschnitte, ein gewähltes Ziel wächst über Nachbarabschnitte bis
  zum Restbedarf – zwei Haufen passen in zwei Stunden, eine schwache Galaxie bekommt einen langen Block. Alte Wünsche mit
  `min_hours` gelten weiter (≤ 1 h → kurz, ≥ 3 h → gründlich, sonst normal).
- **Heute Nacht** (`tonight._need_info`): je Empfehlung `need` = {hours, have, rest, nights, text, why}; Text „reicht heute“
  bzw. „braucht ~3 Nächte“ aus den heute nutzbaren Stunden.

## Grenzen
Keine Unterscheidung nach Filter (Dual-Band an/aus), Sensor-QE oder Pixelgröße; Flächenhelligkeit aus Gesamthelligkeit und
großer Achse ist für Objekte mit hellem Kern (M31) zu pessimistisch; Dunkelnebel stehen bisher nicht im Zielkatalog.
Die Stufen sind bewusst grob – sie sollen Haufen und schwache Nebel trennen, nicht auf die halbe Stunde genau sein.
