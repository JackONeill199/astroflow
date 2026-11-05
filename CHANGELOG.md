# Änderungen

Neueste zuerst, je Version ein Abschnitt (Regeln: `AGENTS.md` → „Versionierung“). Technische Details
stehen in `AGENTS.md`, die Bedienung in `README.md`.

## 0.31.0 – 05.11.2026

### Neu
- **Ordner suchen im Ordnerdialog** („Hauptordner wählen“, SkyTrace „Ordner wählen“): Das Suchfeld filtert sofort die Unterordner des
  aktuellen Ordners und sucht zusätzlich bis zu 4 Ebenen tief (höchstens 200 Treffer). Groß-/Kleinschreibung, Leerzeichen, „_“ und „-“
  spielen keine Rolle – „NGC 4395“ findet also auch `…_observation_NGC4395` und `NGC_4395`. Tiefere Treffer stehen mit ihrem Pfad
  unter „In Unterordnern“; ein Klick öffnet den Ordner, gewählt wird wie bisher der aktuelle Ordner. Esc leert die Suche, Enter öffnet
  den ersten Treffer, die Pfeiltasten führen durch die Liste.

## 0.30.0 – 05.11.2026

### Neu
- **SkyTrace: Uhrversatz je Nacht suchen** (Ansicht „Standorte aller Nächte“, Häkchen „Uhrversatz suchen“): Ging die Kamera-Uhr
  einer Nacht falsch (z. B. rund 10 min in einer Augustwoche 2022), fand der Satellitenabgleich kaum Treffer. Bei Nächten mit weniger als
  der Hälfte zugeordneter Spuren verschiebt SkyTrace den Abgleich jetzt um bis zu ± 20 min und sucht den Versatz, bei dem die meisten
  verschiedenen Spuren eine Satellitenbahn treffen. Vorschlag nur ab 3 Spuren bei demselben Versatz und deutlich über dem Zufallsniveau
  (zufällig erwartete Treffer und Sicherheit werden angezeigt), Feinwert aus den Durchgangszeiten bzw. der Rückrechnung.
- Übernehmen nur auf Knopfdruck (einzeln oder mit „Alle übernehmen“), danach werden die Analysen der Nacht neu abgeglichen. Der Versatz
  wirkt nur beim Abgleich – Rohbilder und gespeicherte Aufnahmezeiten bleiben unverändert, Kandidaten zeigen die Kamerazeit mit dem
  Hinweis „Uhr korrigiert um …“. Ist ein Versatz vorgeschlagen, wird auch der Ort der Nacht gleich mit korrigierter Uhr geprüft.
- Die 3D-Ansicht nutzt den übernommenen Uhrversatz: Teleskopweg, Zeitleiste und Treffer liegen in wahrer UTC, ein Hinweis nennt die
  Korrektur. Stammen die Treffer noch aus einem Abgleich mit anderer Uhr, empfiehlt sie „Satelliten neu abgleichen“.

## 0.29.0 – 05.11.2026

### Neu
- **SkyTrace: Standorte für alle Nächte suchen** (SkyTrace-Statistik und Einstellungen → Standorte, Ansicht „Standorte aller Nächte“):
  Für jede Aufnahmenacht werden die Satellitenspuren aller Beobachtungen dieser Nacht gemeinsam geprüft – gegen jeden Ort der
  Ortsliste. Der Ort mit den meisten eindeutigen Treffern gewinnt (bei Gleichstand die kleinere Abweichung). Passt keiner gut,
  sucht SkyTrace mit einstellbarem Spielraum (± 5/10/20 km) um den besten und rechnet den wahrscheinlichsten Ort samt Uhrversatz
  aus allen Spuren der Nacht zurück. So reichen auch Beobachtungen mit nur einer Spur, wenn die Nacht zusammen genug hat.
- Vorschlagsliste je Nacht: bisheriger Ort, Vorschlag (bestehender Ort oder neu zurückgerechnet mit Genauigkeit), Treffer vorher und
  nachher, Abweichung quer, Uhrversatz. Ehrliche Grenzen: unter 3 eindeutigen Spuren nur Auswahl aus der Liste, ohne passende Spur
  „Kein Ort passt“. Übernehmen einzeln oder alle auf einmal, jeweils nach Bestätigung; danach werden nur die betroffenen
  Analysen neu abgeglichen. Die Liste bleibt nach dem Neuladen erhalten, die Suche ist abbrechbar (fertige Nächte bleiben).

## 0.28.0 – 05.11.2026

### Neu
- **SkyTrace: Analysen nach Standortänderungen neu prüfen.** „Analyse → Satelliten neu abgleichen“ bestimmt für jede Spur den Ort
  neu aus der Ortsliste (Ausnahme der Beobachtung → FITS → Nacht/Zeitraum → Standard-Ort, auch wenn die Rohbilder schon gelöscht sind)
  und gleicht die Satelliten mit den lokal gespeicherten Bahndaten neu ab. Kandidaten, Bahn-Einblendungen und Bericht werden
  aktualisiert. Läuft im Hintergrund mit Fortschritt unter Aktivität, ohne neue Archiv- oder Namensabfragen.
- **Alle SkyTrace-Analysen neu abgleichen** (SkyTrace-Statistik und Einstellungen → Standorte): prüft nur Spuren, deren Ort sich
  geändert hat. Ändern sich Orte oder Zeiträume, fragt ein Hinweis einmal nach, ob neu abgeglichen werden soll – Orte werden nie
  automatisch übernommen.
- **Standortsuche für Nächte ohne Treffer:** Unter Beobachtungen → „Standort der Nacht prüfen“ gibt es jetzt auch für Nächte mit
  weniger als drei zugeordneten Spuren „Standort suchen“: Abgleich mit ± 10 km Spielraum, Rückrechnung aus den eindeutigen
  Zuordnungen (Starlink-Züge und Ausreißer bleiben außen vor), danach strenger Abgleich am Vorschlag. Übernommen wird erst nach
  Bestätigung, danach lässt sich die Analyse direkt mit dem neuen Ort neu abgleichen.

## 0.27.1 – 05.11.2026

### Behoben
- **Analyse: 3D-Entfernungsansicht im Bild statt in einem zweiten Betrachter.** Der Knopf „3D“ in der Werkzeugleiste des Bildes
  (oder „Im Bild in 3D zeigen“ unter „Wie tief schaust du?“) lässt die Sterne direkt aus dem Foto heraus nach ihrer Entfernung in die
  Tiefe ziehen, während die Kamera schräg hinausfährt. Der erste Moment sieht genau aus wie das Bild oben (gleicher Ausschnitt, volle
  Auflösung und Helligkeit); auch in der Schrägansicht bleibt das Foto gut sichtbar. „3D“ erneut drücken fährt zurück zum flachen Bild.
  Mit „Bewegung reduzieren“ bleibt das Bild stehen, die Tiefe zeigt sich beim eigenen Drehen.
- Analyse: Knöpfe im leeren Zustand („Jetzt messen“, „Plate-Solver einrichten“ …) waren über die ganze Breite gezogen.
- 3D-Ansicht: Sterne und Galaxien als runde Punkte statt Quadrate.

## 0.27.0 – 05.11.2026

### Neu
- **Reiter „Analyse“ auf der Objektseite** (Taste 2): Die Auswertungen stehen nicht mehr gequetscht in der Detailspalte, sondern
  breit unter dem Bild. Oben das Bild mit denselben Einblendungen, darunter:
  - **Wie tief schaust du?** – Kennzahlen zählen beim Öffnen hoch, der 3D-Tunnel steht in voller Breite und startet mit einem
    kurzen Kameraflug vom Beobachter bis in die Tiefe (unter 3 s, „Überspringen“ oder Klick bricht ab, „Flug“ wiederholt).
    Klick auf nächsten/fernsten/schwächsten Stern oder das fernste Objekt zoomt im Bild darauf.
  - **Veränderungen** – Blink-Vergleich groß, die auffälligen Stellen daneben.
  - **Der Himmel in dieser Nacht** – Bortle-Skala 1–9 mit Zeiger (fährt an seinen Platz, die Spanne zeigt die Unsicherheit),
    Himmelshelligkeit und Bildschärfe als Verlauf über die Nacht, Windrose der Lichtrichtung des Ortes.
  - **Tutor** – Bildprüfung und Aufnahmebedingungen.
  Fehlen Daten, steht dort, warum, mit Knopf („Jetzt messen“, „Jetzt berechnen“, „Plate-Solver einrichten“).
- Die Detailspalte im Reiter Bild ist wieder kurz: Bildqualität, Grenzgröße, Bortle und Seeing – Klick führt in die Analyse.
- Animationen kurz und funktional (Hochzählen, Zeiger, Einblenden, Kurven zeichnen sich); „Bewegung reduzieren“ schaltet sie ab.

## 0.26.0 – 05.11.2026

### Neu
- **Himmelsqualität** (Übersicht → Himmelsqualität, `#/himmel`): AstroFlow misst aus deinen eigenen Aufnahmen, wie dunkel der
  Himmel war – ein „Sky Quality Meter ohne Gerät“. Die Hintergrundhelligkeit jeder Einzelaufnahme wird an Sternen mit bekannter
  Helligkeit (Gaia-Katalog) geeicht und in **mag/″²** (auf den Zenit umgerechnet) angegeben, dazu eine **Bortle-Schätzung** mit
  Spanne statt Scheingenauigkeit. Als Wert des Ortes zählen nur dunkle, mondlose, dunstfreie Aufnahmen über 30° Höhe;
  andere Nächte zeigen ihren Verlauf mit Kennzeichnung (Mond, Dämmerung, Dunst, tiefes Objekt).
- **Je Standort** (aus der Ortsliste, automatisch je Nacht): Mittelwert, dunkelste Nacht, Verlauf über die Nächte,
  Bildschärfe – „Garten 19,6 · Ferienhaus 21,1“. Die Werte stehen auch in Einstellungen → Standorte und in „Heute Nacht“
  („Himmel hier“).
- **Richtung der Lichtverschmutzung:** aus dem Helligkeitsgefälle vieler Bilder, Blickrichtung und Feldrotation – als Windrose
  mit Unsicherheit; erst mit Zielen in verschiedenen Himmelsrichtungen und mehreren Nächten belastbar.
- **Seeing in Bogensekunden:** Sternschärfe (FWHM) jeder Aufnahme × Bildmaßstab, als „effektive Bildschärfe“ gekennzeichnet –
  bei Smart-Teleskopen stecken Optik, Fokus und Nachführung mit drin.
- **Objektseite:** Kasten „Himmel in der Aufnahmenacht“ mit Himmelshelligkeit, Bortle, Bildschärfe, Durchsicht, Verlauf über die
  Nacht und einer kurzen Erklärung für Einsteiger.
- **Für alle Nächte berechnen:** wertet auch ältere Objekte aus – ohne Rohbilder, aus den gespeicherten Messwerten, dem Stack und
  seiner Plate-Solving-Lösung; Fortschritt unter Aktivität. Neue Objekte werden nach dem Entwickeln automatisch ausgewertet.

## 0.25.0 – 05.11.2026

### Neu
- **„Wie tief schaust du?“** (Objektseite → Bild, Detailspalte): für jedes plate-gesolvte Ergebnis die **Grenzgröße**
  (5σ-Grenze aus gemessenem Himmelsrauschen, dazu „Hälfte der Gaia-Sterne erfasst bis G …“), erfasste Sterne je
  Helligkeitsklasse, der schwächste sicher erfasste Stern, **nächster und fernster Stern** mit verlässlicher Gaia-Parallaxe
  samt Entfernungsverteilung, die Entfernung des Hauptobjekts (aus Sage) und **Galaxien und Quasare mit Rotverschiebung**
  (SIMBAD), die im Bild wirklich zu sehen sind – mit Lichtlaufzeit und heutiger Entfernung: „das fernste Objekt in deinem Bild“.
  Regionen im Hauptobjekt selbst (z. B. HII-Gebiete in M101) zählen nicht als Hintergrundgalaxien. Wird nach jeder
  Entwicklung im Hintergrund gemessen, für ältere Bilder per „Jetzt messen“.
- **Im Bild markieren:** nächster/fernster/schwächster Stern und die erkannten Galaxien mit Entfernung direkt im Betrachter.
- **3D-Tunnel:** die Sterne und Galaxien des Bildfelds räumlich nach Entfernung (logarithmisch) – von vorn wie das Foto,
  schräg oder seitlich als Trichter in die Tiefe; Klick zeigt Name, Entfernung und Lichtlaufzeit.
- **Veränderungen** (Objektseite → Bild): Vergleich mit einer früheren eigenen Aufnahme desselben Himmelsausschnitts oder
  – online, abschaltbar – mit der Himmelsdurchmusterung DSS2. Schärfe und Helligkeit werden angeglichen, auffällige Stellen
  (neu, verschwunden, heller, schwächer) mit Formprüfung gegen Rand, Einzelpixel, Spuren und Reste heller Sterne gefiltert und
  mit SIMBAD (bekannte Veränderliche) sowie JPL-Kleinkörpern zur Aufnahmezeit abgeglichen. Ausdrücklich als **Hinweis**,
  nie als Entdeckung. Dazu ein **Blink-Vergleich** (Trennlinie ziehen oder automatisch wechseln) und Ausschnitte
  Referenz · aktuell · Differenz je Stelle.
- **Einstellungen → Bilder → „Was dein Bild zeigt“:** automatisch messen, SIMBAD, Durchmusterung und Kleinkörper-Abgleich
  einzeln abschaltbar. Gesendet werden nur Himmelskoordinaten und Aufnahmezeiten – nie Bilder oder der Standort
  (Kleinkörper geozentrisch).

### Geändert
- three.js liegt jetzt in der Shell (`astroflow/static/vendor/`) und wird von SkyTrace-3D und dem Tiefen-Tunnel gemeinsam genutzt.

## 0.24.1 – 05.11.2026

### Behoben
- **Kometen ohne „Objekt unbekannt“:** Am Namen erkannte Kometen (z. B. „C/2022 E3“ aus dem Stellina-Ordner) zeigten oben den
  Hinweis „Objekt unbekannt“ mit „Jetzt bestimmen“, weil die Aufnahmen keine Koordinaten haben. Der Hinweis und der Menüpunkt
  „Objekt per Plate-Solving bestimmen“ entfallen bei Kometen. Stattdessen erscheint nur dann ein kurzer Hinweis, wenn die Bahn mangels
  Plate-Solver allein aus dem Bild gemessen wurde – mit eingerichtetem Solver kommt sie zusätzlich aus der JPL-Ephemeride.
- Kometenmodus nutzt für die Parallaxe jetzt den Ort der Nacht aus der gemeinsamen Ortsliste (vorher nur die alte Tutor-Einstellung).

## 0.24.0 – 05.11.2026

### Neu
- **Stil mit Atmosphäre:** Unter Einstellungen → Module & System → Darstellung gibt es drei Stile. „Dezent“ (Standard) und
  „Nebula“ bringen einen ruhigen Navy-Violett-Verlauf im Hintergrund, halbtransparente Glasflächen mit feinem hellem Rand,
  Primärknöpfe und aktive Navigation mit Violett-Blau-Verlauf. „Nebula“ ist kräftiger und hat ein kaum sichtbares Sternenfeld.
  „Schlicht“ ist das neutrale Anthrazit von 0.21. Aufbau, Abstände und Lesbarkeit bleiben gleich.
- **Sternschnuppen nach dem echten Himmel:** Selten fliegt hinter den Inhalten eine Sternschnuppe durch – so oft, wie es draußen gerade
  passt: Die 13 großen Meteorströme (Aktivität nach Sonnenlänge, Höhe des Radianten über deinem Standort), sporadische Meteore (morgens
  mehr als abends), Dämmerung und Mondlicht; tagsüber keine. Angezeigt wird etwa jede fünfte – in einer normalen Nacht ein, zwei pro
  Stunde, im Maximum der Geminiden oder Perseiden alle paar Minuten. Strom-Meteore fliegen vom Radianten weg, schnelle (Leoniden,
  Perseiden) sind kurz und grünlich, langsame (Geminiden, Tauriden) länger und gelblich. Unter Darstellung steht, was der Himmel
  gerade hergibt; abschaltbar, aus bei „Bewegung reduzieren“ und im Stil „Schlicht“.
- **Direktere Rückmeldung:** Knöpfe, Reiter, Schalter, Navigation und Karten geben beim Drücken leicht nach; Primärknöpfe heben sich beim
  Überfahren an, Fokus zeigt einen weichen Leuchtrand. Löst ein Knopf eine Aktion beim Server aus, ist er sofort gesperrt und zeigt nach
  0,15 s einen Kreisel, bis die Antwort da ist – kein doppeltes Klicken mehr, kein Rätseln, ob der Klick angekommen ist.

### Behoben
- **Doppelter Import-Knopf:** „Bilder“ und „Galerie“ hatten neben „Importieren“ in der Werkzeugleiste einen zweiten Import-Knopf im Seitenkopf.
  Jetzt gibt es nur noch den oben (in leeren Ansichten bleibt der Hinweis zum Importieren).

## 0.23.0 – 04.11.2026

### Neu
- **Standorte für ganz AstroFlow** (Einstellungen → Standorte): eine gemeinsame Ortsliste für Heute Nacht, Bild-Tutor
  und SkyTrace mit Name, Koordinaten, Höhe, **Genauigkeit** und Notiz, Adresssuche, Gerätestandort und Geländehöhe auf
  Knopfdruck. Ein Standard-Ort, dazu **Zeiträume** („von–bis war das Teleskop hier“) als Zeitleiste und eine **Nachtliste**,
  in der sich der Ort jeder Aufnahmenacht umstellen lässt. Je Nacht wird der passende Ort automatisch gewählt
  (Ausnahme der Beobachtung → Ort aus den FITS-Daten → Nacht/Zeitraum → Standard-Ort).
- **Heute Nacht:** „Heute bin ich in …“ wählt den Ort für die heutige Planung direkt in der Ansicht.
- **Bild-Tutor** nimmt den Ort der jeweiligen Aufnahmenacht aus der Ortsliste und nennt ihn.
- **SkyTrace:** Kandidaten und Laufbericht speichern, welcher Ort mit welcher Genauigkeit genutzt wurde. Ist der Ort
  ungenau, weitet der Satellitenabgleich die Toleranz quer zur Bahn passend aus (höchstens 30′) und meldet
  „Standort ungenau – Zuordnung unsicher“ statt „kein Treffer“. Bei genauem Ort bleibt alles wie bisher.
- **SkyTrace → Beobachtungen → „Standort der Nacht prüfen“:** rechnet aus mehreren zugeordneten Satellitenspuren
  Standort und Uhrversatz der Kamera zurück (mit Fehlerellipse) und schlägt einen besseren Ort vor – übernommen wird
  nur auf Knopfdruck, als neuer Ort für diese Nacht.

### Geändert
- Bisherige Standorte (Bild-Tutor, SkyTrace) werden beim ersten Start einmalig übernommen; Orte näher als 200 m werden
  zusammengefasst. Die alten Einstellungen bleiben erhalten. Das Standortfeld im Bild-Tutor verweist jetzt auf die Ortsliste,
  in SkyTrace bleiben nur Ausnahmen für einzelne Beobachtungen.

## 0.22.0 – 04.11.2026

### Neu
- **Kometenmodus:** Kometen werden am Namen erkannt (OBJECT-Header oder Ordner wie `…_observation_C_2022_E3`,
  `12P`, „Komet“) und je Nacht als eigenes Objekt geführt. Neben dem gewohnten Stack auf die Sterne entsteht ein
  zweiter, auf den Kometenkern ausgerichteter Stack (Sterne werden dort ausgeblendet). Das Ergebnis setzt scharfe
  Sterne und einen scharfen Kometen zusammen; auf der Objektseite lässt sich zwischen **Ergebnis**, **Sterne-Stack**
  und **Kometen-Stack** umschalten (beide Einzelstacks auch als Datei).
- **Kometenbahn:** mit auflösbarer Bezeichnung und Plate-Solving aus der JPL-Horizons-Ephemeride (Parallaxe lokal am
  Standort gerechnet – der Standort wird nicht übertragen), im Bild nachgemessen; ohne Netz wird der Kern in den
  Einzelbildern gesucht und seine Bewegung gefittet.
- Eigenes Entwicklungsprofil **Komet**: Koma und Schweif sanft gestreckt, Grünton der Koma bleibt (kein SCNR),
  GraXpert nur zum Entrauschen (die KI-Hintergrundentfernung könnte den Schweif abtragen).
- Objekt → Optionen: Kometenmodus automatisch/ein/aus und Kometen-Bezeichnung für die Bahn.
- Bild-Tutor: Hinweis, wenn der Komet schon während einer Einzelaufnahme merklich wandert, mit passender Belichtungszeit.
- `python -m astroflow demo --comet` erzeugt eine synthetische Kometenserie.

## 0.21.1 – 04.11.2026

### Behoben
- **Übersicht ohne Ladeanimation:** Die Übersicht wartete, bis alle Abschnitte geladen waren (auch „Heute Nacht“), und erschien dann
  auf einmal. Jetzt steht das Gerüst sofort; jeder Abschnitt hat einen Platzhalter in fester Höhe und blendet ein, sobald seine Daten
  da sind – schnelle Kacheln sofort, langsame (Heute Nacht, Sage, SkyTrace) danach. Schlägt ein Abschnitt fehl, steht der Hinweis nur dort.
- **Rückmeldung beim Seitenwechsel:** Dauert ein Wechsel länger als 0,15 s, läuft unter der Werkzeugleiste ein schmaler Ladebalken.
  Galerie und Bilder zeigen beim Laden Platzhalter-Kacheln statt einer leeren Fläche.

## 0.21.0 – 04.11.2026

### Neu
- **Neue Oberfläche:** einheitliches, ruhiges Designsystem (neutrales Anthrazit, ein blauer Akzent, keine Verläufe,
  kein Sternenhintergrund) – die Astrofotos stehen im Vordergrund. Alle Farben, Abstände und Schriftgrößen kommen aus
  zentralen Tokens in `style.css`; die Module übernehmen sie automatisch.
- **Neuer Rahmen:** Werkzeugleiste oben (Suche/Befehle mit Strg+K, Importieren, Auftragsanzeige, Systemstatus),
  einklappbare Seitenleiste mit **Arbeitsbereichen** (Übersicht, Bilder, SkyTrace, Sage), **Bibliothek** (Galerie,
  Sammlung) und **System** (Aufträge, Einstellungen). Unterseiten eines Bereichs klappen unter ihm auf; auf dem Handy
  stehen sie als Leiste über der Ansicht, die Hauptbereiche unten.
- **Kontextpanel rechts:** Details und Aktionen zur aktuellen Auswahl (z. B. Bild-Details mit Bild-Tutor, Kanälen,
  Himmelsposition und „Geschichte erzählen“). Ein- und ausklappbar; auf kleineren Fenstern und dem Handy als Schublade.
- **Übersicht neu:** Modulkarten mit Live-Zahlen, aktive Aufträge, zuletzt bearbeitete Bilder, neueste Entdeckungen
  (SkyTrace-Kandidaten, Sage-Geschichten und -Entdeckungen) und daneben „Heute Nacht“. Die Aufteilung bleibt wählbar.
- **Auftragsanzeige** in der Werkzeugleiste: Zahl laufender Aufträge, Klick öffnet die Liste mit Fortschritt.
  Die Seite **Aufträge** (vorher „Aktivität“) zeigt zusätzlich Start und Dauer.
- **Bilder als Werkbank:** alle Sessions als Karten oder kompakte Tabelle mit Status. Die Objektseite zeigt die
  **Verarbeitungs-Pipeline** (Import → Analyse → Auswahl → Stacking → Entwicklung → Ausgabe) mit echtem Status je Schritt,
  einen großen Bildbetrachter und die Details im Kontextpanel. „Aktualisieren“ heißt jetzt **Nachstacken**.
- **Galerie** (neu, unter Bibliothek): nur fertige Bilder, rahmenlos im großen oder kleinen Raster oder als Liste,
  mit Suche, Filter nach Objekttyp und Sortierung.
- **Prozessvisualisierung:** Während ein Auftrag läuft, zeigt eine kleine schematische Animation, was gerade passiert
  (Aufnahmen vermessen, aussortieren, an Sternen ausrichten und mitteln – mit echter Aufnahmezahl und Rauschen ≈ 1/√N –,
  Helligkeitskurve strecken; bei SkyTrace die Spurensuche mit echtem Fortschritt). Als „Schema“ gekennzeichnet,
  pausierbar, bei „Bewegung reduzieren“ ein Standbild. Echte Zwischenbilder zeigt weiterhin die Live-Vorschau.
- **SkyTrace:** Analysen als Tabelle oder Karten, sachlicher Seitenkopf.
- **Oberflächengröße:** Auf großen Monitoren (WQHD, 4K) werden Schrift und Bedienelemente automatisch größer;
  unter Einstellungen → Module & System → Darstellung fest wählbar (90–140 %, je Browser).
- **Hauptordner durchsuchen – Filter:** eine Werkzeugleiste direkt über der Liste. Reiter nach Zustand (Offen, Neu, Teilweise,
  Fertig, Alle – Standard „Offen“) mit Anzahl, dazu Menüs „Art“ (Aufnahmen, fertige Bilder, Kalibrierung) und „Format“
  (FITS, JPG, PNG, TIFF …) mit Mehrfachauswahl. „Alle neuen“ und „Importieren“ wirken nur auf die angezeigten Gruppen;
  die Filterwahl bleibt gemerkt.
- **Ruhigeres, offeneres Layout:** Übersichten, Listen, Wissensseiten und Import stehen zentriert mit begrenzter Breite;
  auf großen Monitoren wachsen nur die Außenränder. Lesetexte haben eine angenehme Zeilenlänge, Arbeitsflächen (Bild bearbeiten,
  SkyTrace-Lauf, 3D) nutzen weiter die volle Breite. Größere Schrift, deutlichere Überschriften, weniger Rahmen und keine Karten in Karten.
- **Übersicht:** zuletzt bearbeitete Bilder und Entdeckungen zuerst, Module kompakt darunter; ohne laufende Aufträge nur eine Zeile.
  „Heute Nacht“ zeigt kompakt Dunkelheit, Mond, Wetter und bis zu drei Ziele – die volle Grafik gibt es in der Aufteilung „Nachtplanung“.
- **Bilddetails:** Der Tutor trennt **Bildqualität**
  (Bewertung der Messwerte am fertigen Bild) und **Aufnahmebedingungen** (Hinweise zur Nacht, senken die Bewertung nicht); die
  Aufnahmenächte sind aufklappbar.
- **Import:** zwei Einstiege mit kurzer Erklärung – „Dateien nach AstroFlow kopieren“ oder „Vorhandenen Ordner einbinden“;
  technische Erläuterungen aufklappbar.
- **Objektnamen im Bild:** Fährt man mit der Maus über ein Bild (Bilder und Galerie-Betrachter), erscheinen Kreise und Namen
  der Katalogobjekte dort, wo das Plate-Solving sie gefunden hat; sie wandern mit Zoom und Verschieben (auf Touch-Geräten immer sichtbar).
- **Ruhigere Seitenwechsel:** Die bisherige Seite bleibt stehen, bis die neue fertig ist – kein kurz aufblitzendes Standard-Skelett
  und kein leerer Moment mehr; ein Platzhalter erscheint nur, wenn das Laden länger dauert. Im Betrachter werden Nachbarbilder und
  ihr Objektwissen vorab geladen, sodass ← → ohne Wartezeit und ohne springende Spalte wechseln. Die Werkzeugleiste oben verschiebt
  sich nicht mehr, je nachdem ob eine Seite ein Panel hat. Beim Verlassen der 3D-Ansicht blitzt die Bühne nicht mehr weiß auf.
- **Galerie mit eigenem Betrachter:** Ein Klick auf ein Bild öffnet es groß (nicht mehr die Bearbeitungsseite). Daneben steht
  **„Über das Objekt“** aus Sage – Kurztext, Kernwerte, deine bisherigen Aufnahmen und der Link zum Artikel – sowie die Daten deiner
  Aufnahme. ← → blättert durch die Galerie, F für Vollbild, „Bearbeiten“ führt zur Verarbeitung unter „Bilder“.
- **Bilder** dient der Verarbeitung: Die Objektseite steht wie die Übersicht zentriert, die Bild-Details (Kennzahlen,
  Bildqualität, Kanäle, Details, danach „Über das Objekt“ und Himmelsposition) stehen als Spalte neben dem Bild – „Details“
  blendet sie aus und ein. Keine eigene Schrittleiste mehr: Läuft eine Verarbeitung, zeigt die Bildfläche selbst die
  Live-Vorschau mit Fortschritt und Schritten.
- **SkyTrace:** Analysen standardmäßig als große Bildkarten (wie unter „Bilder“), Tabelle weiter umschaltbar.
  In der **3D-Ansicht** stehen alle Analysen rechts in einer Liste (Suche, Filter „Mit Kandidaten“) – ein Klick lädt Teleskopweg
  und Bahnen direkt, ohne die Seite zu verlassen.
- **SkyTrace:** gleichnamige Analysen heißen in der Liste „Variante n von m“ (die neueste „aktuell“); die Vorschau ist nie höher als
  das Fenster, Wiedergabe und Kandidatenwahl bleiben darunter sichtbar.
- **Sage:** kompakter Objektkopf (Foto, Kurztext, Kernwerte), darunter eine Hauptspalte (Beschreibung, Schnellfakten, Forschung) und
  rechts Himmelsposition, eigene Aufnahmen, Verwandtes und Notizen – ohne doppeltes Bild, doppelte Beschreibung oder doppelte Fakten.
  Das Foto mit Objektmarken steht unter „Bilder & Aufnahmen“. Die Himmelskarte beschriftet nicht mehr überlappend.
- **SkyTrace 3D-Ansicht als eigene Ansicht statt Fenster:** im Lauf als Reiter „3D“ mit großer Bühne, Zeitleiste und Objektliste
  daneben (Handy: darunter), dazu der Menüpunkt **SkyTrace → 3D-Ansicht** mit Auswahl der Analyse und ein Einstieg auf der
  SkyTrace-Startseite. Kamera (Erde, Teleskop, Durchgang, Folgen), Vollbild (Taste F), im neuen Design. „3D-Ansicht“ beim
  Kandidaten öffnet den Reiter mit diesem Objekt im Fokus. Läufe ohne Treffer zeigen weiter Teleskopweg und Blickfeld.

## 0.20.1 – 04.11.2026

### Behoben
- **Heute Nacht bei Bewölkung:** Ist die Nacht laut Vorhersage bedeckt, bleibt die Übersicht nicht mehr leer mit dem irreführenden
  „keine Empfehlungen über 30°“. Stattdessen steht „Heute Nacht bedeckt (x % Bewölkung laut Vorhersage)“, mit dem nächsten
  klaren Fenster (auch in der folgenden Nacht) bzw. dem Hinweis, dass die Vorhersage keines zeigt. Die Ziele erscheinen trotzdem,
  gestrichelt und als „bei klarem Himmel“ gekennzeichnet; bei teils bewölktem Himmel stehen Ziele in klaren Stunden vorn.
- Der Kopf der Übersicht passt sich an („Heute Nacht bedeckt“ / „Teils bewölkt“ / „Klarer Himmel heute Nacht“, ohne Wetter
  „Was lohnt sich heute Nacht?“). Meldungen unterscheiden bedeckt, zu niedrig (Höhe), Nacht schon vorbei und keine Dunkelheit.

## 0.20.0 – 04.11.2026

### Neu
- **Bessere Empfehlungen für heute Nacht:** Neue Ziele kommen jetzt aus der **vollständigen Messier-Liste (110) und dem
  Caldwell-Katalog (109)** – fest eingebaut, auch ohne ASTAP –, dazu auffällige Objekte aus ASTAPs `deep_sky.csv`.
  Gefiltert wird nach Helligkeit (je Gerät: Stellina bis 11,5 mag, Vespera/Seestar 10,5, Dwarf 9,5, sonst 11) und nach
  Größe zum Bildfeld (3–90 % der kurzen Seite). Doppelte Bezeichnungen (M31 = NGC 224) und Begleiter im selben Feld
  (M32/M110 bei M31) werden zusammengeführt bzw. nicht vorgeschlagen.
- **Neue Rangfolge:** zählt die nutzbaren Stunden in der dunklen Zeit, gewichtet mit Mondphase und -abstand, Wolken
  (wenn die Vorhersage im Bild-Tutor an ist) und bei Alt-Az-Geräten der Zenitnähe (> 80°, Feldrotation); dazu Kulmination
  im Dunkeln. Die Auswahl mischt Objektarten (höchstens zwei je Art) – „Neu“ und „Mehr Zeit“ stehen gleichwertig nebeneinander.
- **„Mehr Zeit“ konkret:** „Bisher 37 min, heute bis zu 5 h möglich → Rauschen etwa −67 %“ (1/√Zeit-Regel). Ziele,
  bei denen heute nur wenige Prozent zu holen wären, werden nicht mehr vorgeschlagen.
- **Saison:** „Letzte Gelegenheit bis zum Frühjahr“ für Objekte, die in den nächsten Wochen aus dem dunklen Fenster
  wandern, und eine Liste „Bald gut sichtbar“ auf der Seite „Heute Nacht“.
- **Wetter:** Mit eingeschalteter Vorhersage zeigt die Grafik die Bewölkung als Streifen; bewölkte Stunden zählen weniger
  (ab 80 % gar nicht), der Nachtplan legt die Ziele ins klare Fenster.
- **Nachtplan:** Ein Smart-Teleskop nimmt nur ein Ziel gleichzeitig auf – die Seite „Heute Nacht“ teilt die Nacht in
  1–4 Abschnitte („20:15–23:00 A, danach B“).
- **„Nicht interessiert“** blendet ein Ziel aus (nur lokal im Browser gemerkt, wieder einblendbar).
- **Sammlung:** Ist Sage aktiv, steht bei Messier-Zielen „Wäre dein 31. Messier-Objekt“ (neuer Dienst `collection_progress`).

## 0.19.1 – 04.11.2026

### Behoben
- **SkyTrace 3D-Lauf-Ansicht:** Bei Läufen ohne Kandidaten stimmten Blickrichtung, Bildlage und Teleskopweg nicht, wenn
  das Analysebild verkleinert war; die Registrierung wird jetzt in Analysepixeln angewendet und erst danach umgerechnet.
- Läuft `sgp4` nur im SkyTrace-Interpreter, öffnet die Lauf-Ansicht mit Satellitentreffern wieder (vorher Fehler 500);
  scheitert die Bahnrechnung, erscheint ein Hinweis.
- Ein in den Einstellungen nachgetragener Standort wirkt sofort, statt dass die Ansicht weiter „Standort fehlt“ meldet.

## 0.19.0 – 03.11.2026

### Neu
- **Empfohlene Ziele für heute Nacht:** Auf der Übersicht zeigt eine responsive Höhenkurve bis zu sechs Ziele,
  Dämmerung, dunkle Zeit, Mond und die Mindesthöhe von 30°. Empfehlungen ergänzen die Bibliothek um bekannte helle
  Katalogziele oder schlagen mehr Belichtung bei kurzer Integrationszeit, Rauschen und schwachem Signal vor.
  Das zuletzt verwendete Gerät und sein bekanntes Bildfeld grenzen neue Ziele ein; unbekannte Bildfelder werden ausgewiesen.
- **Heute Nacht:** Detailplanung mit Dämmerungszeiten, mondlosen Zeitfenstern und optionaler Wettervorhersage.
  Standort und Wetterfreigabe kommen aus dem Bild-Tutor. Berechnung offline; Wetter nur bei aktivierter Freigabe.
  Verlinkung zu eigenen Objekten und bei aktivem Sage zum Enzyklopädie-Eintrag; sonst zur lokalen Katalogkarte.
- **Interaktive Nachtgrafik:** Größere Höhenkurven mit Zeitzeiger per Maus, Touch oder Tastaturregler, Höhenwerten
  zur gewählten Zeit, Zielauswahl und einzeln ausblendbaren Kurven. Begründungen sind kompakt aufklappbar.
  Auf Desktopbreite stehen die Modulkarten mit größeren, beschrifteten Vorschaubildern direkt neben der Planung.
  Große Bildschirme nutzen eine breitere Übersicht; Statusänderungen erhalten die Diagrammauswahl.

- **Dashboard-Aufteilung:** „Ausgewogen“, „Nachtplanung“ oder „Bilder & Module“ wählen; die Auswahl bleibt lokal
  gespeichert. Auf großen Bildschirmen stehen SkyTrace und Sage nebeneinander unter der größeren Bilderkarte.
  Die Nachtplanung zeigt Dunkelzeit, Mond und Gerät als kompakte Kennzahlen; Bildfeld und Berechnungsgrundlagen
  sind aufklappbar. Der Kopfbereich konzentriert sich auf Datum, Import und Ansicht.

## 0.18.0 – 03.11.2026

### Neu
- **SkyTrace-Erfolge** (SkyTrace → Erfolge): 60 gestufte Erfolge mit Fortschritt („37 / 100“), Freischaltdatum und kleiner Animation für neue.
  Ernste Meilensteine (erster Satellit, 10/100/1 000 Zuordnungen, verschiedene Bahnen und Satellitenfamilien, ISS, Raketenstufen und
  Trümmerteile, Flugzeuge, Meteorkandidaten, Beobachtungen, Nächte, 10 000 Aufnahmen) und augenzwinkernde wie „Die Konstellation grüßt“
  (100 Starlinks), „Zug verpasst? Nein, mittendrin“, „Kosmischer Verkehrsstau“, „Sternschnuppe? Nein, Flugzeug“ oder „Saubere Nacht“.
  Gezählt wird nur, was SkyTrace gespeichert hat (Kataloghinweise, keine bestätigte Identität). Bestehende Analysen werden beim ersten Öffnen
  rückwirkend nach dem Lauf datiert, der den Erfolg ausgelöst hat; endet später eine Analyse mit neuem Erfolg, meldet ihn ein Hinweis.

## 0.17.0 – 03.11.2026

### Neu
- **SkyTrace 3D-Ansicht für jeden Lauf:** Knopf „3D-Ansicht“ im Lauf-Kopf, auch ohne erkanntes Objekt. Der Zeitregler
  reicht von der ersten bis zur letzten Aufnahme (Abspielen bis 1200×), eine Leiste zeigt genutzte und ausgelassene
  Aufnahmen, Pausen und Treffer. Über dem Standort zeigt eine Himmelskuppel den Weg der Blickrichtung durch die Nacht
  (Höhe/Azimut aus dem Plate-Solving je Aufnahme). Die Teleskopkarte nennt Bildlage, Feldrotation einer
  Alt-Az-Montierung, Bildfeld und Pausen. Ohne Plate-Solving dient die Katalogkoordinate des Ziels als Schätzung.
  Fehlt der Standort, verweist ein Hinweis auf die Einstellungen.
- **Blickfeld bis zum Objekt:** Strahl vom Kegel ins Unendliche bis zum Zielobjekt auf der Sternkugel, beschriftet mit Name,
  Typ und Entfernung, soweit Katalog und Sage-Wissensbasis sie kennen. Kennzeichnung „nicht maßstäblich“; Ansicht „Ziel“
  blickt vom Standort entlang des Strahls.
- **Alle Treffer eines Laufs zusammen:** Satelliten, Meteore und Flugzeuge aller Kandidaten in eigener Farbe, Durchgang
  durchs Blickfeld markiert, Legende mit Uhrzeit zum Ein-/Ausblenden. Ein Eintrag springt zum Durchgang, „Kandidat
  öffnen“ zum Kandidaten. Der Knopf im Kandidaten öffnet dieselbe Ansicht mit diesem Objekt im Fokus.

## 0.16.1 – 03.11.2026

### Behoben
- **SkyTrace schneller:** Der Liniendetektor prüft mögliche Spurabschnitte gemeinsam vor und verwendet Sternmasken
  mehrfach. Gewinner werden mit der bisherigen Rechnung exakt nachgeprüft; Schwellen, Messwerte und Erkennungsregeln
  bleiben unverändert. Im synthetischen A/B-Test rund 22 % weniger Zeit für die Liniensuche; keine Aussage über die
  Dauer eines gesamten Laufs mit Modell, Videos und Katalogabfragen.

## 0.16.0 – 03.11.2026

### Neu
- **SkyTrace 3D-Ansicht:** Kandidaten mit zugeordnetem Satelliten (bzw. Meteorbahn oder Flugzeugspur) zeigen
  „3D-Ansicht“: drehbare Erde mit Tag-Nacht-Grenze zur Aufnahmezeit, Standort, Teleskop-Blickfeld als Kegel bis in
  Satellitenhöhe und die per SGP4 berechnete Bahn – ganzer Umlauf, Stück im Blickfeld hervorgehoben. Zeitregler
  ±10 min mit Abspielen, Ansichten Erde/Blickfeld/Folgen, Infokarte mit Höhe, Geschwindigkeit, Entfernung,
  NORAD-Nummer und Alter der Bahnelemente. Läuft offline (three.js und Erdtextur liegen bei).
- Neue Katalogabgleiche speichern die Bahnelemente (TLE), Meteor-Flugbahnen und Flugzeugspuren mit. Ältere Läufe
  holen die Bahn aus dem lokalen Bahnarchiv, sonst erklärt ein Hinweis, dass „Zeitreihen aktualisieren“ sie ergänzt.
- Python-Paket `sgp4` in `requirements.txt` (Bahnrechnung im Server; fehlt es, rechnet der SkyTrace-Interpreter).

## 0.15.0 – 03.11.2026

### Neu
- **Sage · Sammlung & Erfolge verständlicher:** Das Kachelraster unter dem Meilenstein-Balken ist jetzt ein eigener Abschnitt
  „Messier-Katalog · x von 110“ mit Erklärung, Nummer auf jeder Kachel, deinem eigenen Bild auf gefüllten Kacheln und dem Filter
  „Fehlende“. Klick öffnet den Enzyklopädie-Eintrag, leere Kacheln ohne Eintrag melden „noch nicht fotografiert“.
- **Messier-Zuordnung robuster:** Objekte zählen auch unter NGC/IC-Namen (NGC 224 = M31) oder über die Kennungen der Wissensbasis.
- **26 neue Erfolge** in Gruppen (Sammlung, Kataloge, Objektarten, Himmel, Aktivität, Beobachtung): Messier 10/50/alle 110, Caldwell,
  erste Galaxie/Planetarischer Nebel/Kugelsternhaufen/offener Haufen/Supernova-Überrest, Sternbilder, Entfernung über 10 bzw. 100 Mio.
  Lichtjahre, Beobachtungsnächte, Nachteule, alle Jahreszeiten, Belichtung 1 h/10 h je Objekt und 100 h insgesamt u. a.
  Gesperrte Erfolge zeigen ihren Fortschritt („17 / 25“). Für den Bestand werden neue Erfolge beim nächsten Laden rückwirkend
  mit plausiblem Datum freigeschaltet und einmal gesammelt gefeiert.
- **Meilensteine** gehen über 100 hinaus (150, 250, 500, 1000); „Alle erreicht“ erscheint erst nach dem letzten.

## 0.14.0 – 03.11.2026

### Neu
- **SkyTrace erneut analysieren:** Dropdown „Analyse“ mit vollständiger neuer Erkennung oder schneller Aktualisierung
  der Zeitreihen. Neuer Lauf aus den bisherigen Originalaufnahmen einschließlich ausgeschlossener Bilder; fehlen
  Originale, lässt sich ihr Ordner wählen. Mehrdeutige oder fehlende Dateien verhindern einen unvollständigen Neustart;
  Wiederholungen zählen in der Statistik auch nach einem Ordnerwechsel nur einmal.
- **SkyTrace-Vollbild-Zeitreihe:** Kandidaten zeigen wahlweise die ganze Aufnahme in Analyseauflösung oder den
  Ausschnitt. Miniaturen, Treffer-Sprung und „Im Video öffnen“ führen direkt zur gewählten Aufnahme; ausgelassene
  Bilder werden anhand der gespeicherten Videozeiten berücksichtigt. Neue Berichte speichern gemeinsame Vollbilder,
  ältere Läufe nutzen vorhandene Vergleichsbilder oder das unbeschriftete Video.

### Behoben
- **SkyTrace-Teleskopbewegung:** Zusätzliche Prüfung des gemeinsamen Profils heller Sterne erkennt auch lange,
  schwache und unterbrochene Schweife bei noch runden Sternkernen. Verlangt ein asymmetrisches Muster im Medianprofil
  und unabhängige Belege an mindestens fünf Sternen in drei Quadranten; betroffene Bilder werden als verwackelt
  ausgeschlossen. Echte Einzelspuren und symmetrische Beugungsmuster bleiben geschützt.
- **Originalordner aus falscher Nacht:** Bei Ersatzdateien genügt ein gleicher FITS-Dateiname nicht mehr.
  Das tatsächliche Aufnahmedatum muss zur gespeicherten Originalaufnahme passen, sonst startet keine Wiederholung.
- **SkyTrace-Video mit Overlay:** Die transparente Overlay-Fläche blockiert die Videosteuerung nicht mehr;
  Beschriftungen bleiben interaktiv. Wiedergabe und Position bleiben beim Umschalten erhalten.
- **Kurze helle Spuren:** Zusätzliche Prüfung in nativer Analyseauflösung für schmale transiente Grate unterhalb
  der bisherigen Hough-Mindestlänge. Verlangt übereinstimmende Form-/Kontrastbelege im Original und im registrierten
  Differenzbild sowie ruhigen Hintergrund. Als unbestätigte Spur, ohne automatische Objektidentifikation.
- **NGC-1499-Spur übersehen:** Für den Differenzvergleich bleibt die direkte Registrierung benachbarter Aufnahmen
  erhalten. Sie wird nicht mehr durch die Differenz zweier unabhängig bestimmter Referenzlösungen ersetzt; ein
  Sprung dieser Lösungen hatte im bestätigten Fall die Sterne um rund 22 Pixel versetzt und die schwache Spur verdeckt.

## 0.13.0 – 03.11.2026

### Neu
- **Live-Bildaufbau:** Im Bild-Tab erscheinen echte, verkleinerte Zwischenbilder während des Stackens und der
  Entwicklung. Neue Vorschauen werden weich eingeblendet. Eine schmale Statuszeile zeigt den tatsächlichen Schritt
  (auch GraXpert, Gaia, StarNet, Stretch und Qualitätskontrolle); der Verlauf steht unter „Details“.
- **Schnellerer Stacking-Verlauf:** Kleine Live-Vorschauen werden direkt für den späteren Regler gespeichert.
  Die zusätzliche vollständige Entwicklung jeder Zwischenstufe entfällt. Das fertige Bild erhält weiterhin die volle
  Bearbeitung; die Vorschauen zeigen den tatsächlichen Bildaufbau und können in Farbe, Helligkeit und Ausschnitt abweichen.

### Behoben
- **Verlaufsregler:** Beim Wechsel zwischen kleinen Live-Vorschauen und dem hochauflösenden Endbild bleiben
  die sichtbare Bildgröße, der relative Zoom und der gewählte Bildausschnitt erhalten.
- **SkyTrace-Ladeanzeige:** Eine kompakte animierte Bildfolge mit Spur und Bahnvergleich ersetzt den Ladekreis
  im laufenden Auftrag und den Punkt bei „Analysen“. Die Schleife zeigt den Ablauf symbolisch; der echte Fortschritt
  bleibt separat sichtbar. Bei reduzierter Bewegung erscheint ein ruhiges Symbol.

## 0.12.0 – 02.11.2026

### Neu
- **„Hauptordner durchsuchen“ kopiert keine Rohdaten mehr.** Liegen die Aufnahmen auf einer anderen Platte als der
  Datenordner (z. B. Stellina-Platte E: und AstroFlow auf C:), bindet der Import sie **am Ort** ein: AstroFlow liest die
  FITS-Dateien direkt von dort, es wird kein Speicher auf dem Datenlaufwerk belegt. Auf derselben Platte bleibt es beim
  Hardlink (kostet ebenfalls keinen Platz). Die Originale werden nie verändert oder gelöscht – auch nicht nach dem Stacken
  oder beim Löschen eines Objekts.
- Import-Ansicht: Hinweis, dass Hochladen über den Browser eine Kopie anlegt und für große Datenmengen auf demselben PC
  „Hauptordner durchsuchen“ besser ist.

### Behoben
- Platte mit eingebundenen Aufnahmen abgesteckt: Bearbeiten und Nachstacken aus dem vorhandenen Stack laufen weiter,
  nicht erreichbare Aufnahmen werden weder als gelöscht noch als fehlerhaft markiert und kommen dran, sobald die Platte
  wieder da ist – auch wenn die Platte schon vor der ersten Verarbeitung abgesteckt wurde (der Auftrag wartet und läuft
  danach automatisch weiter). Bereits berechnete Master-Darks/-Flats bleiben nutzbar, wenn die Kalibrierbilder auf der
  abgesteckten Platte liegen. Komplett neu stacken meldet klar, welcher Ordner fehlt. In der Aufnahmeliste steht
  „nicht erreichbar“.
- Der Import verlangte für Aufnahmen von einer anderen Platte bisher 20 GB Reserve zusätzlich zur vollen Datenmenge –
  das entfällt.

## 0.11.0 – 01.11.2026

### Neu
- **SkyTrace-Statistik** (SkyTrace → Statistik, auch über „Statistik“ in der Analysen-Übersicht): Kandidaten nach Objektart,
  häufigste zugeordnete Satelliten, Katalogabgleich je Quelle (Treffer / kein Treffer / nicht prüfbar mit Grund),
  Uhrzeit der Kandidaten, Kandidaten je Monat und die ergiebigsten Beobachtungen – filterbar nach Jahr. Mehrfach
  analysierte Beobachtungen zählen nur einmal (neueste vollständige Analyse).

## 0.10.1 – 01.11.2026

### Behoben
- **SkyTrace: Satellitennamen fehlten** („Satellit NORAD 51779“ statt Name): CelesTrak hatte die Namensabfrage für den
  Internetanschluss gesperrt (HTTP 403), weil jede Katalognummer einzeln abgefragt wurde und nach der Sperre jeder weitere
  Kandidat erneut anfragte. Die Zuordnung zur Bahn war davon nicht betroffen – nur der Name.
- Namen kommen jetzt aus einer **lokalen Kopie des ganzen CelesTrak-Satellitenkatalogs**. Sie wird nur bei Bedarf
  geladen und höchstens einmal am Tag erneuert; einzeln nachgefragt werden nur Nummern, die jünger sind als die Kopie.
  CelesTrak erfährt so auch nicht mehr, welche Satelliten in deinen Aufnahmen vorkamen. Stand der Kopie:
  Einstellungen → SkyTrace (Archivzeile).
- Gemäß den CelesTrak-Nutzungsregeln hält SkyTrace die Abfrage bei **jeder** Fehlerantwort (z. B. 301, 403, 404, 5xx)
  sofort an und zeigt den Grund im Satellitenabgleich des Kandidaten, im Laufstatus und unter Einstellungen → SkyTrace.
  Dort lässt sie sich nach Prüfung wieder freigeben. Ohne Verbindung oder bei unbrauchbarer Antwort wird nach
  15 Minuten erneut versucht; eine unbrauchbare Liste ersetzt nie die vorhandene Kopie.

## 0.10.0 – 01.11.2026

### Neu
- **Bildversionen einzeln löschen:** Im Tab „Versionen“ eines Objekts hat jede Version einen Papierkorb. Gelöscht werden
  Eintrag und Bilddateien dieser Version; Master-Stack und Rohdaten bleiben. Die einzige Version eines Objekts lässt sich
  so nicht löschen (dafür „Objekt löschen“).
- **Hauptordner durchsuchen zeigt nur noch Importierbares:** Gruppen, die schon vollständig in AstroFlow sind, werden
  ausgeblendet statt ausgegraut; ein Link unter der Liste blendet sie bei Bedarf wieder ein.

## 0.9.0 – 01.11.2026

### Neu
- **SkyTrace erkennt Satelliten jetzt zuverlässig:** Spuren werden über ihre Bahn einem Satelliten zugeordnet und mit
  Namen angezeigt, z. B. „Satellit STARLINK-1277“, „Raketenstufe SL-3 R/B“ oder „Trümmerteil“ (Namen aus dem
  CelesTrak-Katalog, nur die Katalognummer wird abgefragt). Kandidatenliste und Detailansicht zeigen die Objektart
  statt „K0001“ als Titel; neuer Filter „Satelliten“; die Bahntabelle nennt, ob die Bahn während der Belichtung passt.
- **Alte Läufe neu abgleichen:** „Zeitreihen aktualisieren“ oder offline für viele Läufe
  `engine/refresh_satellites.py <Lauf> …` (nur lokale Bahnarchive, keine Originalbilder nötig).

### Behoben
- **Satellitenabgleich fand fast nie etwas** (11 Treffer bei 173 Prüfungen in allen bisherigen Läufen): Stellina schreibt in `DATE-OBS`
  das *Ende* der Belichtung, der Abgleich suchte die 10 s danach. Außerdem gehen die Kamerauhren um einige Sekunden
  falsch. Jetzt wird die Belichtung richtig gelegt und ±20 s Uhrtoleranz zugelassen; bewertet werden Abstand quer zur
  Bahn, Richtung an der Durchgangsstelle und die nötige Zeit. Beispiel M31 (11.06.2022): 12 von 13 Spuren zugeordnet,
  vorher keine.

## 0.8.2 – 01.11.2026

### Behoben
- **Keine privaten Angaben mehr im Repository:** SkyTrace schlägt als Quellordner das Benutzerverzeichnis vor statt eines festen
  Laufwerkspfads (gespeicherte Auswahl bleibt), Prüfskripte lesen echte Beispieldaten aus `SKYTRACE_SAMPLES`, Dokumentation ohne
  Heimnetz-Adressen, Tests mit neutralen Koordinaten. Lokale Notizen für Agenten gehören in `AGENTS.local.md` (nicht im Repo).

## 0.8.1 – 01.11.2026

### Neu
- **README mit Rundgang:** 16 Screenshots aller Module (Galerie, Objektseite mit Bild-Tutor, Vorher/Nachher, SkyTrace,
  Sage-Enzyklopädie, Entdeckungen, Sammlung, Befehlspalette, Einstellungen) mit kurzen Beschreibungen; Sage-Abschnitt
  auf die Enzyklopädie aktualisiert.
- **Lizenzdatei** `LICENSE` (MIT; Detektormodell von SkyTrace weiter AGPL-3.0).

## 0.8.0 – 01.11.2026

### Neu
- **Bild-Tutor** (Einstellungen → Bilder → „Bild-Tutor“, Darstellung *Einsteiger* oder *Erfahren*): erklärt jede
  Auffälligkeit des Bildes mit Ursache und Tipps fürs nächste Mal und bewertet die Aufnahmenacht.
  - **Gerät** wird automatisch erkannt (Stellina, Vespera, Seestar, Dwarf, eVscope, Astrokamera); Tipps passen
    dazu (Filter, Wind, Tau, Bildfeldrotation azimutaler Montierungen). Stellina: kein Tau-Hinweis (eingebaute Heizung).
  - **Nacht**: Dämmerung (Sonnenstand), Mondphase/-höhe/-abstand und Objekthöhe – offline berechnet, Standort aus
    den Aufnahmen oder den Einstellungen. Dazu der Verlauf der Einzelaufnahmen (Himmel wird heller, Sterne
    verschwinden = Wolken, Sterne werden unschärfer, verzogene Sterne = Wind).
  - **Wetter** (optional): Wolken, Luftfeuchte/Taupunkt, Frost und Wind der Aufnahmenacht aus dem
    Open-Meteo-Archiv; gesendet werden nur der auf ~10 km gerundete Ort und das Datum.
  - **KI-Einschätzung** auf Knopfdruck: ein Vision-Modell (LM Studio oder OpenRouter) sieht das Bild mit allen
    Messwerten und ergänzt, was es sieht.
- **Farbverläufe im Himmel** werden erkannt (z. B. „rötlich nach rechts“) und beim Entwickeln automatisch
  ausgeglichen, ohne die Helligkeit zu ändern. In der Milchstraße, wo farbiger Himmel echter Nebel sein kann,
  gilt eine höhere Schwelle.

### Behoben
- Die Bildprüfung gab Bildern mit deutlichem Farbstich an einer Seite 10 von 10 Punkten (NGC 891: rötlicher
  rechter Rand durch Morgendämmerung im Juni). Ältere Bewertungen werden beim Öffnen am gespeicherten Bild
  nachgeprüft; „Mehr → Neu entwickeln“ gleicht den Verlauf aus.

## 0.7.0 – 01.11.2026

Fasst alles seit 0.6.0 zusammen (dazu auch die Kurzfassungen vom 29. bis 31.10. weiter unten).

### Schutz vor vollem Laufwerk

#### Behoben
- **Volles Datenlaufwerk legte alles lahm**: Der Import der ganzen Stellina-Platte (E:) hat die Aufnahmen
  nach C: kopiert – Verknüpfungen statt Kopien gehen nur auf demselben Laufwerk. C: lief voll, danach
  scheiterte jeder Auftrag, und die Fehler ließen sich nicht stoppen.
- Der Import rechnet jetzt vorher aus, wie viel kopiert werden muss, und lehnt ab, wenn danach weniger als
  20 GB frei blieben (mit Hinweis, weniger Gruppen zu wählen). Während des Kopierens hält er bei unter 5 GB an.
- Aufträge starten erst ab 5 GB freiem Speicher. Sinkt der freie Platz während eines Auftrags unter 2 GB
  oder ist die Platte voll, hält die Warteschlange an. Der Auftrag wartet dann und setzt nach dem Fortsetzen
  am letzten Zwischenstand fort. Ein Hinweis erscheint in der Seitenleiste und unter „Aktivität“.

#### Neu
- **Stacken und Endbearbeitung laufen parallel**: Sobald ein Objekt gestackt ist, wird es im Hintergrund
  entwickelt, und das nächste Objekt in der Warteschlange wird sofort gestackt. Höchstens ein fertiger
  Stack wartet auf die Endbearbeitung.
- **„Aufträge anhalten“ / „Aufträge fortsetzen“** unter „Aktivität“: bricht die laufende Bildverarbeitung ab
  (nichts geht verloren, das Stacking macht am letzten Zwischenstand weiter) und hält die Warteschlange an.

### Stacking-Verlauf

#### Neu
- **Regler „Stacking-Verlauf“ unter dem Bild**: zeigt dasselbe Objekt nach 1, 3, 10, 30, 100, 300 …
  Aufnahmen – mit denselben Einstellungen und genau demselben Ausschnitt wie das fertige Bild. Man sieht
  direkt, wie das Rauschen mit jeder weiteren Aufnahme sinkt. Standardmäßig steht der Regler auf dem
  fertigen Bild; Zoom und Ausschnitt bleiben beim Verschieben erhalten.
- Die Zwischenstände entstehen beim Stacken. Bei Objekten, deren Einzelbilder schon gelöscht sind, gibt es
  sie erst für künftige Stufen (z. B. „300“, sobald neue Nächte die Zahl überschreiten).
- Hinweise: Je Stufe dauert das Entwickeln etwas (Stellina-Format rund 11 s), die Zwischenstände belegen
  im Arbeitsordner etwa 40 MB je Stufe. Wurde das fertige Bild mit GraXpert oder StarNet bearbeitet,
  werden die Stufen mit den eigenen Verfahren entwickelt (steht dann unter dem Regler).

### Ordner wählen statt Pfad eintippen

#### Neu
- **Stacking auf der Grafikkarte**: Ausrichten und Ausreißer-Erkennung laufen jetzt auf der GPU, wenn
  `pyopencl` installiert ist. Das funktioniert mit AMD-, NVIDIA- und Intel-Karten. Gemessen mit
  219 Stellina-Aufnahmen auf einer Radeon RX 9070 XT: Stacking 22 statt 67 Sekunden, der ganze Durchlauf
  49 statt 96 Sekunden. Das Ergebnis weicht im Mittel um weniger als ein Tausendstel des Bildrauschens ab.
  Ein- und ausschalten unter Einstellungen → Bilder → System. Ohne geeignete Karte oder bei einem
  Fehler rechnet wie bisher der Prozessor.
- **Hauptordner-Suche viel schneller**: Bereits bekannte Aufnahmen erkennt AstroFlow am
  Beobachtungsordner und Dateinamen (Stellina: Datum, Uhrzeit und Objekt im Namen), ohne die Datei zu
  lesen. Neue Beobachtungsordner werden nur stichprobenartig angelesen, einmal Geprüftes wird gemerkt.
  Gemessen auf der Stellina-Platte (31 718 FITS): früher rund 19 Minuten, jetzt 56 Sekunden beim ersten
  und gut 1 Sekunde bei jedem weiteren Suchlauf.
- **„Hauptordner durchsuchen“ mit Ordnerauswahl**: Statt einen Pfad einzutippen, öffnet
  „Ordner wählen …“ einen Dialog mit Laufwerken, anklickbarem Pfad und Unterordnern. Er zeigt
  auch, wie viele FITS-Dateien und Bilder im Ordner liegen. Die Auswahl startet die Suche sofort,
  „Erneut durchsuchen“ wiederholt sie. Das funktioniert auch vom Handy oder einem anderen PC im
  Heimnetz, denn angezeigt werden die Ordner des Rechners, auf dem AstroFlow läuft.
- SkyTrace nutzt denselben Dialog für den Beobachtungsordner (jetzt auch mit Laufwerkswechsel).
- **Schnellere Seitenwechsel**: Die Objektliste (Galerie, Übersicht, Statistik) lädt in rund 0,1 statt
  1,8 Sekunden, die Statusabfrage im Hintergrund in wenigen Millisekunden statt 0,4 Sekunden.

#### Behoben
- **Stacken schneller**: Das Aussortieren von Ausreißern rechnet schneller, und die Hotpixel-Korrektur
  wird je Aufnahme nur noch einmal statt zweimal berechnet. Ein Durchlauf mit 219 Stellina-Aufnahmen
  ist dadurch etwa 13 % schneller, das Ergebnis bleibt bitgenau gleich.
- **Farbkalibrierung schneller**: Die Gaia-Sternfarben kommen jetzt zuerst von Spiegelservern in
  Heidelberg (GAVO, ARI) statt vom ESA-Archiv. Der Schritt „Gaia-Sternfarben abfragen“ dauert dadurch
  etwa 1 Sekunde statt rund 2 Minuten. Ist ein Spiegel nicht erreichbar, wird wie bisher die ESA gefragt.
- **Fertige Bilder anderer Programme nicht mehr als Rohbilder stacken**: Bei M17 lagen im
  Stellina-Beobachtungsordner neben den Rohbildern vier fertige Bilder aus Astro Pixel Processor. Sie
  wurden als eigene Aufnahmen eingelesen und ergaben zwei zusätzliche Objekte „M17 Omeganebel“, die auch
  an Sage und SkyTrace gingen. Fertige Stacks (`NUMFRAME`/`STACKCNT`/`NCOMBINE` > 1, `INTEGRAT`) und
  Farbbilder mit drei Ebenen erkennt AstroFlow jetzt am Header und lässt sie beim Stacken aus.
- SkyTrace-Einstellungen: Beim schnellen Wechseln der Einstellungsseiten trat gelegentlich ein
  Skriptfehler auf (Archivstatus wurde in eine nicht mehr angezeigte Seite geschrieben).

### Bildqualität wie im Astrobiscuit-Workflow

Anlass: Astrobiscuit erreicht mit Smart-Teleskopen sehr saubere Bilder über
Astro Pixel Processor → GraXpert → StarNet2 → Affinity. AstroFlow übernimmt die
wirksamen Schritte automatisch.

#### Neu
- **GraXpert (KI) eingebunden** – optional, kostenlos, Open Source.
  - **KI-Hintergrund** entfernt Lichtverschmutzungsverläufe, ohne großflächige Nebel
    abzutragen. Bisher erledigte das ein Polynom, das bei höherem Grad Nebel mit abtrug.
  - **KI-Entrauschen** arbeitet auf dem linearen Stack, vor dem Strecken.
  - Läuft einmal je Entwicklung, das Ergebnis wird je Objekt zwischengespeichert.
    Schlägt GraXpert fehl, nutzt AstroFlow die eigenen Verfahren und zeigt den Grund an.
  - Einstellungen → Bilder → „GraXpert (KI)“: Modus (Hintergrund + Entrauschen / nur
    Hintergrund / nur Entrauschen / aus), Grafikkarte, Programmpfad. AstroFlow findet
    eine normale Installation automatisch.
  - Je Objekt unter „Optionen“ abweichend einstellbar. Die Bilddetails zeigen, was
    angewendet wurde.
  - Ergebnis M64: Restgradient −90 %, Farbrauschen −37 %, dunkler Himmel; die
    diagonalen Rauschschlieren aus dem Stack sind fast verschwunden. M3: Restgradient
    halbiert, Farbrauschen −35 %.
- **Nebel mit StarNet getrennt bearbeitet** (Emissions-, Reflexionsnebel,
  Supernova-Überreste). Der Nebel wird ohne Sterne gestreckt, die Sterne kommen
  verkleinert zurück. Dadurch tritt der Nebel deutlich hervor und geht nicht mehr im
  Sternfeld unter (NGC 7000). Die Trennung läuft nur mit installiertem StarNet. Fehlt
  es, bleibt sie aus; die eigene Trennung wird nicht mehr automatisch genutzt.
  Galaxien und Sternhaufen bleiben ohne Trennung, weil sie bei Haufen den Kern ausbrennt.
- **Endbearbeitung für Nebel** (wie der letzte Schliff in Affinity):
  - hebt Strukturen von 4–64 px an (Filamente, Wände, Dunkelwolken), nur im Objekt,
    nicht im Himmel und nicht an Sternen;
  - „Vibrance“ sättigt blasse Farben stärker als bereits kräftige;
  - die Qualitätskontrolle begrenzt die Sättigung weiterhin.
- **„Alle neu entwickeln“** in der Galerie: Alle Objekte werden aus ihrem
  gespeicherten Master-Stack neu bearbeitet, ohne Stacking und ohne Rohdaten. Die
  bisherigen Bilder bleiben als ältere Versionen erhalten.
- Objektoptionen „Sterne getrennt bearbeiten“: wie Profil / nur mit StarNet /
  StarNet oder eigene Trennung / eigene Trennung / nein.

#### Behoben
- **StarNet-Installation** schlug fehl („exit status 4294967295“ bzw. „requires the
  'imagecodecs' package“):
  - StarNet 2.6 speichert LZW-komprimierte TIFFs; dafür ist jetzt `imagecodecs` in
    `requirements.txt` eingetragen.
  - Das Probebild ist größer.
  - Fehlermeldungen zeigen StarNets eigene Ausgabe.

#### Hinweis zum Aktualisieren
- `.venv\Scripts\pip install -r requirements.txt` ausführen (neu: `imagecodecs`).
- GraXpert von https://github.com/Steffenhir/GraXpert/releases/latest installieren.
  Beim ersten Lauf lädt es seine KI-Modelle selbst herunter.
- Danach in der Galerie „Alle neu entwickeln“ wählen.

## Frühere Versionen (Kurzfassung)

- **31.10.2026** – Import: Hauptordner (z. B. ganze Stellina-Platte) durchsuchen und
  gezielt importieren; bei mehr als 10 FITS nur FITS übernehmen; vorhandene Rohdaten
  melden. Speicherbelegung anzeigen und aufräumen. Sage-Startseite als Portal.
  Layout-Test im Browser.
- **30.10.2026** – Sensorglühen als 2D-Karte, Kernschutz gegen ausgebrannte Zentren,
  Objekttyp aus dem ASTAP-Katalog. Stellina-Verlaufsbilder zusammengefasst und
  Rohdaten zugeordnet. Verzogene Sterne absolut aussortieren; schonendere Entwicklung
  bei schwachem Signal, weniger Glätten. Darstellung auf dem Handy verbessert.
- **29.10.2026** – Gemeinsamer Import für Rohaufnahmen und fertige Bilder. Sage 2.0
  (Enzyklopädie, Recherche, Beobachtungsgedächtnis). Beobachtungsstatistik.
  SkyTrace: blinkende Flugzeuge erkennen, Analysen löschbar. Späte Uploads automatisch
  nachstacken.
- **28.10.2026** – **AstroFlow 0.6**: SkyTrace und Sage als Module unter einer
  Oberfläche; nach dem Stacken automatisch SkyTrace und Entdeckungen mit Erfolgen.
  Modellauswahl per Dropdown, Zugriff im Heimnetz.
- **AstroFlow 0.5** – Automatische Pipeline von FITS-Rohbildern zum fertigen
  Astrofoto mit Web-Oberfläche.
