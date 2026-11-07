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
| 5 | 04.11.2026 | Kometenmodus | umgesetzt in 0.22.0 |
| 6 | 04.11.2026 | Planetenmodus (Lucky Imaging) | offen |
| 7 | 04.11.2026 | Standorte nach Datum | umgesetzt in 0.23.0 (Kür Standort-Rückrechnung: Vorschlag, kein Automatismus) |
| 8 | 05.11.2026 | Lichtkurven veränderlicher Sterne | offen |
| 9 | 05.11.2026 | HR-Diagramm von Sternhaufen | offen |
| 10 | 05.11.2026 | Asteroiden in eigenen Bildern | offen |
| 11 | 05.11.2026 | Exoplanet-Transits | offen |
| 12 | 05.11.2026 | „Wie tief schaust du?“ – Gaia-Entfernungen, Grenzgröße, 3D | umgesetzt in 0.25.0 |
| 13 | 05.11.2026 | Vergleich mit Referenzbild/früheren Nächten (neue Lichtquellen) | umgesetzt in 0.25.0 (Durchmusterung: nur neu/fehlend) |
| 14 | 05.11.2026 | Himmelshelligkeit (mag/arcsec²) je Nacht und Standort | in Arbeit |
| 15 | 05.11.2026 | Richtung der Lichtverschmutzung | in Arbeit |
| 16 | 05.11.2026 | Seeing in Bogensekunden je Nacht und Standort | in Arbeit |
| 17 | 05.11.2026 | Gerätediagnose: Nachführung, Fokusdrift, Sensorneigung | offen |
| 18 | 05.11.2026 | SkyTrace: Satellitenhelligkeit und Satelliten pro Stunde | umgesetzt in 0.39.0 (Statistik; mag mit Nullpunkt aus den Bilder-Stacks, Rate je Belichtungsstunde) |
| 19 | 05.11.2026 | Interaktive Bildführung: KI zeigt und erklärt im Bild | offen |

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

**Ausbau in 0.20.0 (Status: umgesetzt):** vollständige Messier-/Caldwell-Liste + ASTAP, Helligkeits-/Größenfilter je Gerät, Rangfolge nach
dunkel/mondarm/klar nutzbaren Stunden mit Zenit-Abwertung und Objektart-Mischung, konkrete „Mehr Zeit“-Rechnung (1/√t), Saison-Hinweise,
Wetterstreifen, Nachtplan, „Nicht interessiert“ (lokal) und Sammlungsbezug über den Sage-Dienst `collection_progress`.
Offen: Sage-Entdeckungen/Alter der letzten Aufnahme als weitere Gründe, Ausblenden serverseitig/geräteübergreifend, Horizontprofil.

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

## 5. Kometenmodus – 04.11.2026

Kometen bewegen sich gegenüber den Sternen. Bisher richtet die Pipeline nur auf Sterne aus, der Komet verwischt.
Anlass: Eine Suche über alle Laufwerke ergab, dass nur noch Kometenaufnahmen (C/2022 E3 ZTF, ein „Komet“-Ordner) fehlen.

- Komet automatisch erkennen (OBJECT-Header, Ordnername wie `…_observation_C_2022_E3`), per Projektoption erzwingbar.
- Kometenposition je Aufnahme: Ephemeride (JPL Horizons) über Aufnahmezeit + Plate-Solving, ohne Netz Kern im Bild verfolgen.
- Zwei Stacks: auf die Sterne und auf den Kometenkern ausgerichtet; zusammengesetzt aus scharfen Sternen und scharfem Kometen.
- Eigenes Entwicklungsprofil (Koma und Schweif schonend strecken, Grünton erhalten), alle drei Ergebnisse ansehbar.

## 6. Planetenmodus (Lucky Imaging) – 04.11.2026

Planeten, Mond und Sonne brauchen ein anderes Verfahren als Deep-Sky: viele sehr kurze Aufnahmen bzw. Videos
(SER/AVI), nur die schärfsten 5–20 % behalten, Ausrichtung an der Planetenscheibe und an Oberflächendetails
(mehrere Ausrichtungspunkte), danach kräftiges Schärfen (Wavelets) – wie AutoStakkert!/RegiStax.

Zu klären:
- Eingang: Videos (SER/AVI/MP4) und Bildserien, Erkennung über Objekt/Ordnername oder Bildinhalt (Scheibe ohne Sterne).
- Sinnvoll erst mit längerer Brennweite (Seestar-Planetenmodus, klassische Teleskope); bei Stellina/Vespera ist Jupiter nur
  wenige Dutzend Pixel groß – Hinweis statt enttäuschendem Ergebnis.
- Ausgabe: Stack + geschärftes Bild, optional Derotation (Jupiter) als späterer Schritt.

## 7. Standorte nach Datum – 04.11.2026

„Man nimmt sein Teleskop oft an verschiedene Orte, deshalb müssen die Standorte flexibel sein bzw. nach Datum auswählbar.“
Für Satelliten muss der Ort auf ~200–500 m stimmen (1 km ≈ 7′ quer bei 500 km), für Nachtplanung/Tutor/Wetter reichen 10 km.

- **Umgesetzt (0.23.0):** gemeinsame Ortsliste mit Genauigkeit, Standard-Ort, Zeiträumen und Ort je Nacht; Heute Nacht,
  Bild-Tutor und SkyTrace nutzen sie; SkyTrace weitet die Satelliten-Toleranz bei ungenauem Ort aus und kann Ort und
  Uhrversatz einer Nacht aus Satellitenspuren zurückrechnen (nur Vorschlag).
- Offen: Karte zum Anklicken (bisher Adresssuche, Koordinaten und Link zu OpenStreetMap), Höhe als dritte Unbekannte
  der Rückrechnung, Vorschlag gleich für mehrere Nächte auf einmal übernehmen.

## 8.–18. Was sich aus den Bilddaten noch berechnen lässt – 05.11.2026

Ideensammlung nach der Frage „was können wir mit den Bilddaten noch Interessantes berechnen?“. Grundlage: Plate-Solving (WCS),
Gaia-Abgleich der Farbkalibrierung, Einzelbild-Zeitreihen über Stunden, SkyTrace, Standorte (0.23.0). Bewertet für Smart-Teleskope
(Stellina/Vespera: ~80 mm, ~1,2″/px, Farbsensor, 10-s-Belichtungen).

**Wissenschaft mit eigenen Daten**
- **8 Lichtkurven:** Einzelbilder als Zeitreihe, Differenzphotometrie gegen Gaia-Vergleichssterne, Abgleich mit dem Katalog
  veränderlicher Sterne (AAVSO VSX). M3 hat > 200 RR-Lyrae-Sterne (Perioden ~0,5 d) – Änderungen in einer Nacht sichtbar;
  Bedeckungsveränderliche im Feld.
- **9 HR-Diagramm:** Farbe/Helligkeit jedes Sterns aus dem Stack, an Gaia geeicht → Farben-Helligkeits-Diagramm (M3, M45,
  h und χ Persei), Abknickpunkt/Alter; Erklärung über Sage.
- **10 Asteroiden:** langsame Punkte, die über die Einzelbilder wandern, mit JPL SB-Ident abgleichen (SkyTrace hat die Abfrage) –
  „Du hast 9 Asteroiden fotografiert, der schwächste 16,2 mag“.
- **11 Exoplanet-Transits:** bei hellen Sternen (z. B. HD 189733, 2,5 % Tiefe) mit 80 mm knapp machbar – ambitioniert.

**Was dein Bild zeigt**
- **12 „Wie tief schaust du?“:** Grenzgröße (schwächster erfasster Stern), nächster/fernster Stern aus Gaia-Parallaxen, Galaxien im
  Hintergrund mit Entfernung/Lichtlaufzeit aus Katalogen; Darstellung als Kennzahlen und als 3D-„Tunnel“ im vorhandenen 3D-Viewer.
- **13 Vergleich:** eigener Stack gegen Referenzbild (Pan-STARRS/DSS) oder eigene frühere Nächte → neue/veränderte Lichtquellen
  (Supernova, Nova, Veränderliche) als Hinweis, nie als Entdeckung behauptet.

**Himmel und Standort**
- **14 Himmelshelligkeit:** Hintergrund in mag/arcsec² über die Gaia-Eichung (Sky-Quality-Meter ohne Gerät), Bortle-Schätzung je
  Nacht und Standort, Verlauf über die Nacht.
- **15 Lichtverschmutzungs-Richtung:** aus Hintergrundverlauf, Blickrichtung (Az/Alt) und Feldrotation die Himmelsrichtung der Störquelle.
- **16 Seeing:** FWHM in Bogensekunden über die Nacht und je Standort (Statistik).

**Gerätediagnose**
- **17:** Nachführfehler, Fokusdrift über Nacht/Temperatur, Sensorneigung (Schärfeverteilung über das Bildfeld) → Tutor-Hinweise.

**SkyTrace**
- **18:** Helligkeit der Satellitenspuren in mag, Satelliten pro Stunde je Standort und Jahr („wie füllt sich der Himmel“).

## 19. Interaktive Bildführung: KI zeigt und erklärt im Bild – 05.11.2026

Die Erzählung/Audioführung von Sage interaktiv machen: Die KI „sieht“ die Elemente im Bild, reagiert darauf und stellt sie
visuell dar. Grundlage ist nicht Bilderkennung, sondern Plate-Solving: Für jedes Pixel ist der Himmelsort bekannt
(`work/p<id>/wcs.json`), Katalogobjekte stehen schon heute als Bildmarken bereit (`results/{id}/objects`, `plateLabels`),
Sage liefert belegtes Wissen je Objekt – verlässlich, nichts erfunden.

**Stufe 1 (mittel, eine Sitzung):**
- **Geführte Tour:** Erzählung als Folge von Schritten {Text, Ziel in Bildpixeln, Zoom}; während der Audioführung fährt der
  Viewer (`createViewer`) zum Objekt, zoomt und markiert es (Kreis, Pfeil, Umriss). Vor/Zurück, Pause, Untertitel.
- **Fragen per Klick:** Stelle anklicken → Himmelskoordinaten → Katalogabfrage in der Umgebung (Sage, SIMBAD) → Antwort im Chat,
  Stelle im Bild markiert („Was ist dieser rote Fleck?“).

**Stufe 2 (schwerer):**
- Strukturen ohne Katalogeintrag (Staubbänder, Nebelfilamente, Sternfarben) per Vision-LLM am Bildausschnitt, als
  „KI-Einschätzung“ deutlich getrennt vom belegten Wissen (Belegpflicht von Sage); Umrisse im Bild.
- Risiko: lokale Vision-Modelle sind bei Astrofotos unzuverlässig und erfinden – nur mit Kennzeichnung und Rückfall auf Stufe 1.
## 14.–16. Himmel und Standort – Stand 05.11.2026

(Ideen 14 Himmelshelligkeit, 15 Lichtverschmutzungs-Richtung, 16 Seeing aus der Liste „Was sich aus den Bilddaten noch berechnen lässt“.)

- **Umgesetzt (0.26.0):** Himmelshelligkeit in mag/″² je Aufnahme, Nacht und Standort (Gaia-geeicht, Zenit, Kennzeichnung von
  Mond/Dämmerung/Dunst/tiefem Objekt), Bortle mit Spanne, Seeing als effektive Bildschärfe, Lichtrichtung als Windrose mit
  Unsicherheit; Ansicht „Himmelsqualität“, Objektseite, Heute Nacht, Ortsliste; „Für alle Nächte berechnen“ ohne Rohbilder.
- Offen: Rauschmodell (Ausleserauschen, Photonen-Transfer) für Vespera/Seestar/Dwarf vermessen – bisher nur Stellina; ohne Darks
  liefern andere Geräte keinen Wert. Ausleserauschen der Stellina mit Bias-Aufnahmen genauer bestimmen (größte Unsicherheit
  bei dunklem Himmel). Lichtrichtung braucht die neue Hintergrundebene je Aufnahme – ältere Nächte nur, wenn Rohbilder noch da sind.

