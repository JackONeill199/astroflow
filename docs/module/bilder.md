# Bilder – vom Rohbild zum fertigen Astrofoto

Das Kernmodul von AstroFlow: Aufnahmen sortieren, kalibrieren, stacken und entwickeln, dazu Analysen, Heute Nacht, Observatorium und Kometenmodus.

[← Zurück zur Übersicht](../../README.md)


## Vom Rohbild zum fertigen Foto

Jedes Objekt erscheint automatisch, sobald seine Rohbilder im Eingangsordner liegen – Objekt, Kamera und Nacht erkennt AstroFlow aus Header, Ordnername oder
Plate-Solving. Neue Nächte werden zum vorhandenen Stack dazugerechnet, verschobene Bildfelder (Mosaikmodus, Nachführsprünge) zu einem größeren Bild
zusammengesetzt. Eine Grafikkarte oder Zusatzprogramme braucht es dafür nicht. Beim Stacken zeigt die **Live-Ansicht** echte Zwischenvorschauen mit dem aktuellen Schritt.

![Live-Ansicht beim Stacken](../media/stacking-live.webp)

Der **Stacking-Verlauf** zeigt, was 1, 3, 10, 30, 100 … Aufnahmen ausmachen, und **Vorher/Nachher** vergleicht den linearen Stack mit dem entwickelten Bild
(Gradienten, Gaia-Farbkalibrierung, optional GraXpert und StarNet).

| | |
|---|---|
| ![Stacking-Verlauf](../media/stacking-stufen.webp) | ![Vorher/Nachher](../media/vorher-nachher.webp) |

*(Stacking-Verlauf: M16, Vorher/Nachher und Live-Ansicht: M17 – echte Aufnahmen eines Smart-Teleskops.)*

## Ergebnisse

Fertige Bilder aus AstroFlow, vollautomatisch aus Rohaufnahmen eines Smart-Teleskops (80 mm Öffnung) entwickelt, verkleinert:

| | | |
|---|---|---|
| ![NGC 7000](../screenshots/ergebnis-ngc7000.webp) | ![NGC 6888](../screenshots/ergebnis-ngc6888.webp) | ![M16](../screenshots/ergebnis-m16.webp) |
| NGC 7000, 58 min | NGC 6888, 45 min | M16 Adlernebel, 1 h 05 min |
| ![M17](../screenshots/ergebnis-m17.webp) | ![NGC 7023](../screenshots/ergebnis-ngc7023.webp) | ![IC 1396](../screenshots/ergebnis-ic1396.webp) |
| M17 Omeganebel, 50 min | NGC 7023, 3 h 58 min | IC 1396, 60 min |

Die **Galerie** zeigt alle fertigen Bilder, die **Übersicht** das Zuletzt-Bearbeitete – am Handy genauso wie am großen Bildschirm.

![Galerie](../screenshots/galerie.webp)

## Das einzelne Objekt: Bild, Qualität, Aufnahmen

Die Objektseite zeigt das Ergebnis im Zoom-Betrachter, den **Stacking-Verlauf** und eine **Bildqualität als Note** (Tiefe, Rauschen, Schärfe und Sternform
einzeln als Balken, dazu Grenzgröße, Bortle und Seeing). Ein kurzer Tutor-Hinweis nennt, was bei dieser Aufnahme störte (Dämmerung, Mond, Wolken).

![Objektseite mit Bildqualität](../screenshots/objekt.webp)

Im Reiter **Aufnahmen** steht jede Einzelaufnahme mit Vorschaubild und der Begründung, warum sie gestackt oder verworfen wurde. Das Diagramm darüber zeigt
Himmelshelligkeit und Schärfe über die Zeit, die Nächte als Bänder mit Dämmerung und Mond sowie die verworfenen Aufnahmen.

![Aufnahmen-Diagramm](../screenshots/aufnahmen.webp)

Verschiebt das Teleskop das Bildfeld deutlich (Mosaikmodus der Stellina, Nachführsprünge), setzt AstroFlow die Felder zu einem größeren Bild zusammen
(Optionen: Mosaik automatisch/ein/aus, Zuschnitt groß oder kompakt).

![Mosaik aus verschobenen Bildfeldern](../screenshots/mosaik.webp)

## Analyse: Was dein Bild zeigt

Im Reiter **Analyse** jedes Objekts misst AstroFlow aus dem plate-gelösten Bild die **Grenzgröße**, die erfassten Sterne, den nächsten und fernsten Stern
(Gaia-Parallaxen) und die fernsten sichtbaren Galaxien und Quasare (Rotverschiebung, Lichtlaufzeit) – als Kennzahlen, Markierungen und **in 3D**.

![Analyse: Wie tief schaust du?](../screenshots/analyse-tiefe.webp)

![Tiefe in 3D](../media/tiefe-3d.webp)

Der **Blinkvergleich** stellt das Bild gegen eine frühere eigene Aufnahme oder die DSS2-Durchmusterung. Neue oder verschwundene Lichtquellen werden nur gemeldet,
wenn sie über der Grenzgröße beider Bilder liegen, und mit SIMBAD und JPL-Kleinkörpern abgeglichen – als Hinweis, nicht als Entdeckung.

![Veränderungen im Bild](../screenshots/analyse-vergleich.webp)

![Blinkvergleich](../media/blinkvergleich.webp)

Weitere Auswertungen je Objekt, alle aus gespeicherten Daten:

- **HR-Diagramm** bei Sternhaufen: Farben-Helligkeits-Diagramm aus deinem eigenen Bild mit Abknickpunkt, Entfernung, Rötung und grobem Alter – mit Gegenprobe gegen Gaia.
- **Asteroiden:** welche bekannten Kleinplaneten im Feld lagen und welche du in den Einzelbildern wirklich erwischt hast, mit gemessener Helligkeit.
- **Dein Gerät:** Nachführung (Drift, Verschmieren), Fokus über die Nacht, Schärfekarte des Bildfelds und der Verlauf über alle Nächte des Geräts.
- **Himmel der Aufnahmenacht** (Bortle-Schätzung, Seeing, Lichtrichtung) und **Bild-Tutor** mit Tipps passend zum Gerät.

![HR-Diagramm eines Sternhaufens](../screenshots/analyse-hrd.webp)

| | |
|---|---|
| ![Asteroiden im Bild](../screenshots/analyse-asteroiden.webp) | ![Gerätediagnose](../screenshots/analyse-geraet.webp) |

*(Das Asteroiden-Beispiel stammt aus synthetischen Demodaten mit einer JPL-Attrappe.)*

Das **Analyse-Dashboard** (Bilder → Analyse) fasst die ganze Bibliothek zusammen: Kennzahlen, Rekorde, „Dein Universum“ als 3D-Wolke, Verläufe und die Frage,
was am meisten gebracht hat; „Alles analysieren“ rechnet fehlende Auswertungen im Hintergrund nach.

![Analyse-Dashboard](../screenshots/analyse-dashboard.webp)

## Himmelsqualität und „Heute Nacht“

| | |
|---|---|
| ![Heute Nacht](../screenshots/heute-nacht.webp) | ![Himmelsqualität](../screenshots/himmel.webp) |
| **Heute Nacht:** gut sichtbare Ziele für die gewählte Nacht (mit ‹ › durch die nächsten 14 Nächte blättern), Höhenverläufe mit Dämmerung und Mond, Nachtplan, optional Wetter. | **Himmelsqualität:** aus deinen Aufnahmen gemessen, an Gaia-Sternen geeicht – mag/″², Bortle mit Spanne, Seeing, Lichtrichtung, dazu die **sauberen Nächte** mit Ranglisten. |

*(Demodaten mit erfundenem Ort.)*

**Observatorium:** ein zoombarer Himmelsatlas des ganzen Himmels – Sterne, Sternbilder, Messier-/Caldwell-Objekte und **deine eigenen Fotos an ihrer echten Position,
in echter Größe und Drehung** (aus dem Plate-Solving). Klick auf ein Foto öffnet das Projekt, auf ein Katalogobjekt die Sage-Seite; Sage springt von jeder Objektseite hierher.
Unter **Planung** fragt ein Assistent nach Nächten, Gerät(en) – Stellina und Vespera auch gleichzeitig –, Wunsch (Neues oder vorhandene vertiefen), Mindestbelichtung und Merkliste und rechnet einen **Plan über mehrere Nächte** aus Sichtbarkeit, Horizont, Mond und (soweit vorhanden) Wetter.

![Observatorium: eigene Fotos im Schwan an ihrer echten Position](../screenshots/observatorium.webp)

Orte legst du unter Einstellungen → Standorte **nach Datum** an (Zeiträume, einzelne Nächte, Standard-Ort); „Heute bin ich in …“ wechselt den Ort für die
aktuelle Nacht. Der Ort einer Aufnahme ergibt sich aus FITS-Kopf → Zeitraum → Standard.
Je Ort lässt sich ein **Horizont** einzeichnen (Bäume, Häuser, Balkon) – „Heute Nacht“ rechnet dann nur mit dem freien Himmel. Punkte zeigen, wo du dort schon fotografiert hast; im Observatorium zeigt „Von hier“ den Himmel über deinem Horizont zu einer wählbaren Uhrzeit. Die Ebene **Bahnen** zeigt dort, wie deine Ziele durch die Nacht wandern und wann sie im freien Ausschnitt über deinem Horizont stehen – „Ganzer Himmel“ zeigt alles von oben. Mit einem Plan siehst du dort auch, wann dein Teleskop welches Objekt anpeilt und wohin es danach schwenkt.

## Kometenmodus

Kometen bekommen zwei Stacks – auf die Sterne und auf den Kometenkern – und werden zu einem Bild zusammengesetzt (Bahn aus der JPL-Ephemeride oder im Bild
gemessen). Beide Einzelstacks bleiben ansehbar.
