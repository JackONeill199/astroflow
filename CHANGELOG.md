# Änderungen

Neueste zuerst, je Version ein Abschnitt (Regeln: `AGENTS.md` → „Versionierung“). Technische Details
stehen in `AGENTS.md`, die Bedienung in `README.md`.

## 0.14.0 – 03.11.2026

### Neu
- **SkyTrace-Vollbild-Zeitreihe:** Kandidaten zeigen wahlweise die ganze Aufnahme in Analyseauflösung oder den
  Ausschnitt. Miniaturen, Treffer-Sprung und „Im Video öffnen“ führen direkt zur gewählten Aufnahme; ausgelassene
  Bilder werden anhand der gespeicherten Videozeiten berücksichtigt. Neue Berichte speichern gemeinsame Vollbilder,
  ältere Läufe nutzen vorhandene Vergleichsbilder oder das unbeschriftete Video.

### Behoben
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
