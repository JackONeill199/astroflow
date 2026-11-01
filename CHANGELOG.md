# Änderungen

Neueste zuerst. Technische Details stehen in `AGENTS.md`, die Bedienung in `README.md`.

## 01.11.2026 – Ordner wählen statt Pfad eintippen

### Neu
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

### Behoben
- SkyTrace-Einstellungen: Beim schnellen Wechseln der Einstellungsseiten trat gelegentlich ein
  Skriptfehler auf (Archivstatus wurde in eine nicht mehr angezeigte Seite geschrieben).

## 01.11.2026 – Bildqualität wie im Astrobiscuit-Workflow

Anlass: Astrobiscuit erreicht mit Smart-Teleskopen sehr saubere Bilder über
Astro Pixel Processor → GraXpert → StarNet2 → Affinity. AstroFlow übernimmt die
wirksamen Schritte automatisch.

### Neu
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

### Behoben
- **StarNet-Installation** schlug fehl („exit status 4294967295“ bzw. „requires the
  'imagecodecs' package“):
  - StarNet 2.6 speichert LZW-komprimierte TIFFs; dafür ist jetzt `imagecodecs` in
    `requirements.txt` eingetragen.
  - Das Probebild ist größer.
  - Fehlermeldungen zeigen StarNets eigene Ausgabe.

### Hinweis zum Aktualisieren
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
