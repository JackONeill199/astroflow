# AstroFlow – Feature-Wünsche

Ideen und Wünsche für künftige Versionen. Neue Einträge unten anfügen (fortlaufende Nummer, Datum des Wunsches).
Wird ein Wunsch umgesetzt: Status auf „umgesetzt“ setzen und Version/PR nennen, Eintrag nicht löschen.
Laufende Arbeit und Grenzen stehen in `AGENTS.md`, Erledigtes im `CHANGELOG.md`.

| Nr. | Gewünscht am | Wunsch | Status |
|---|---|---|---|
| 1 | 03.11.2026 | Bibliothek teilen (nur ansehen) | offen |
| 2 | 03.11.2026 | Doppelte UI-Elemente vereinheitlichen | offen |
| 3 | 03.11.2026 | Sage-Inhalte direkt in Bilder | offen |
| 4 | 03.11.2026 | Übersicht: empfohlene Ziele für heute Nacht | Grundversion in 0.19.0 |

## 1. Bibliothek teilen (nur ansehen) – 03.11.2026

Die eigene Objekt-Bibliothek mit anderen teilen. Wer den Zugang bekommt, darf nur **ansehen**: keine Daten ändern,
nichts löschen, keine Aufträge starten, keine Einstellungen sehen.

Zu klären:
- Weg des Teilens: Freigabe-Link im Heimnetz oder Export als statische Seite/Archiv, das ohne AstroFlow läuft.
- Was sichtbar ist: fertige Bilder, Versionen, Datenblatt, Sage-Texte. Was nie sichtbar ist: Pfade, Beobachtungsort,
  Schlüssel, Einstellungen.
- Auswahl: ganze Bibliothek oder einzelne Objekte, Freigabe widerrufbar.
- AstroFlow hat heute keine Konten (ein Nutzer, lokal). Der Nur-Lesen-Zugang muss serverseitig erzwungen werden
  (schreibende Anfragen und Einstellungs-Endpunkte gesperrt), nicht nur in der Oberfläche ausgeblendet.

## 2. Doppelte UI-Elemente vereinheitlichen – 03.11.2026

Gleiche Bausteine, die es in mehreren Modulen getrennt gibt, zusammenführen. Beispiel: die Statistikseiten in Bilder und
SkyTrace. Die Module sollen dabei getrennt bleiben (kein Modul importiert ein anderes, Modul-Vertrag `AF.register`).

Ansatz: gemeinsame Bausteine in der Shell (`astroflow/static/app.js`, z. B. Kennzahlen-Leiste, Diagramm, Zeitverlauf,
Rangliste). Die Module liefern nur ihre Daten bzw. registrieren Abschnitte, ähnlich wie `home` oder `objectPanels`.
Bestandsaufnahme vorher: welche Elemente doppelt sind (Statistik, Suchfelder, Kachelraster, Fortschritt, Leerzustände).

Abhängigkeit: Der UI-Umbau (PR #18, „Logbuch“, pausiert) ordnet die Oberfläche ohnehin neu. Am besten gemeinsam mit
oder direkt nach ihm angehen.

## 3. Sage-Inhalte direkt in Bilder – 03.11.2026

Weniger Klicks bis zur Information über das eigene Bild. Ist Sage aktiv, erscheinen die Sage-Inhalte direkt auf der
Objektseite in Bilder, mindestens der einfache Geschichtstext zum Objekt. Man muss nicht mehr extra in Sage wechseln.

- Auf der Objektseite: Geschichte/Kurztext, wichtigste Fakten (Typ, Entfernung, Sternbild), im Bild bestimmte Objekte,
  Link zum vollen Enzyklopädie-Eintrag.
- Ist Sage aus oder hat noch nichts, bleibt die Objektseite wie bisher, ohne Leerstellen oder Fehler.
- Technisch über einen optionalen Hook bzw. Dienst (`ctx.services`), damit die Module getrennt bleiben.
- Teilweise im UI-Umbau PR #18 schon angelegt (Hook `objectStory`). Von dort übernehmen statt neu bauen.

## 4. Übersicht: empfohlene Ziele für heute Nacht – 03.11.2026

**Grundversion in 0.19.0:** Empfehlungen und Höhenkurven auf der Übersicht, Detailplanung unter „Heute Nacht“.
Berücksichtigt den verbleibenden dunklen Teil der Nacht, vorhandene Objekte (auch Katalognamen/Aliasse), letzte
Ergebnisbelichtung und gespeicherte Signal-/Rauschbefunde. Das letzte Aufnahmegerät liefert das Bildfeld aus Plate-Solving,
bei Stellina ersatzweise den bekannten Näherungswert. Ohne Bildfeld kein Größenfilter. Der eingebaute Katalog ist eine
Auswahl bekannter heller Ziele, kein vollständiger Messier-Katalog. Alter der Aufnahme und Sage-Entdeckungen sind
noch keine zusätzlichen Empfehlungsgründe.

Mehr Inhalt auf der Übersichts- bzw. Dashboardseite. Kernstück ist eine Grafik mit den Zielen, die **heute Nacht sichtbar**
sind und sich lohnen. Die Empfehlung richtet sich nach der eigenen Bibliothek und dem Teleskop:
- **Noch nicht aufgenommen:** helle, gut stehende Objekte, die zum Bildfeld passen und noch fehlen (z. B. Messier-Lücken).
- **Braucht mehr Belichtung:** eigene Objekte mit wenig Integrationszeit, hohem Rauschen oder schwachem Tutor-Befund,
  die heute gut stehen. Dazu ein Hinweis, was mehr Zeit bringt.
- Weitere Gründe, wenn die Daten es hergeben: lange nicht mehr fotografiert, neue Sage-Entdeckung zum Objekt.

Grafik: Höhenverlauf der empfohlenen Ziele über die Nacht (Dämmerung, dunkle Zeit, Mond, Mindesthöhe), je Ziel der Grund
der Empfehlung, Klick führt zum Objekt bzw. Enzyklopädie-Eintrag.

Grundlagen, die es schon gibt: Standort `tutor.lat/lon`, Nachtberechnung `tutor.night_conditions` (astropy offline),
Wetter optional über `tutor.fetch_weather`, Katalog `deep_sky.csv`, Belichtung und Qualität je Objekt aus Bilder,
Gerät/Bildfeld aus `tutor.detect_device`.

Abhängigkeit: Der UI-Umbau (PR #18, pausiert) bringt bereits eine Seite „Heute Nacht“ (`stacking/tonight.py`, `#/nacht`)
mit Höhenverlauf und einfachen Vorschlägen. Darauf aufbauen statt neu bauen. Die Empfehlungslogik (Bibliothek + Gerät) kommt dazu.
