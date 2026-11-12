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
  new|mix|deep, min_hours, watch[]} → rechnen und in `settings.json` → `observatory.plan` speichern; feste deutsche Fehlermeldungen).
- **Rechnung** je Nacht wie Heute Nacht (`tonight._alt/_limit/_sep/_series/_window/_forecast`): 10-min-Schritte, astronomisch dunkel, Höhe über
  max(30°, Horizontmaske des Ortes der Nacht – `site_for(night)`), Mondgewicht, Zenit-Abschlag je Gerät (Alt-Az), Wolken nur aus der vorhandenen
  Open-Meteo-Schicht (nur mit Tutor-Einstellung „Wetter“, sonst/später „ohne Wetter“).
- **Vergabe:** Nacht je Gerät in gleich lange Abschnitte ≥ min(Mindestbelichtung, 3 h); Abschnitte nach größtem Nutzen vergeben
  (Priorität × min(Gewinn, Restbedarf), angefangene Ziele ×1,6), nie dasselbe Ziel gleichzeitig auf zwei Geräten, Abschnitte mit < 25 %
  Nutzen bleiben frei (Wolken → Ziel wandert in spätere Nächte). Priorität: Wunsch (neu/Mischung/vertiefen), Objektart, Helligkeit,
  Merkliste ×2. Ausgeschlossen: Begleiter eigener Objekte (wie Heute Nacht) und Kometen-Projekte (wandern).
- **Geräte:** feste Liste `planner.DEVICES` (Bildfeld wie `picture_solve.DEVICE_FOV`) + „Gerät aus deinen Aufnahmen“ (`tonight.library`).
- **Oberfläche** `static/planner.js|css`: Reiter Atlas | Planung (`AFplan.tabs`), Assistent mit Chips, Zeitleiste (Breite = Panel, schmal kürzere
  Ränder), Zielliste mit Fortschritt (10 sichtbar, Rest aufklappbar), Klick auf einen Block → Kontextpanel mit „Im Atlas“.
  Atlas: Ebene „Plan“ (gestrichelte Ringe, „geplant · Tag“). Heute Nacht: Abschnitt „Dein Plan für diese Nacht“ (`AFplan.nightSection`).
- Tests: `tests/test_planner.py`.

## Geplant (Konzept vom 11.11.2026, Freigabe des Nutzers)
- **Horizontmaske (gebaut 12.11.2026, siehe `standorte-himmel.md`):** Feld, Editor, Wirkung in Heute Nacht. Offen aus Etappe 2:
  Vorschlag aus eigenen Aufnahmen und Atlas „Von hier“ (nach dem Planer, Reihenfolge Nutzerentscheidung 12.11.2026).
- **Etappe 2 (ursprünglich):** `horizon` je Standort im `SiteBook` (36 Höhenwerte, alle 10° Azimut), Panorama-Editor mit Vorlagen, Vorschlag aus den eigenen
  Aufnahmen (Alt/Az zur Aufnahmezeit, lokal); wirkt in Heute Nacht. Atlas-Ansicht „Von hier“ (Azimut/Höhe, Zeitregler).
- **Etappe 3:** Assistent (Nächte, Gerät(e) – zwei parallel möglich, Standard eines –, Neues/Vertiefen, Mindestbelichtung, Merkliste), Rechenkern
  aus `tonight.py`, Wetter nur über `tonight._forecast`. Plan in `settings.json` (Abschnitt `observatory`), Anzeige im Atlas und in Heute Nacht.
