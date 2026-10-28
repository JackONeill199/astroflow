# Bildfolgen, Ereignisvideos und Plate Solving

**Dauerspuren:** Jede Erkennung bleibt ab ihrem ersten Treffer bis zum Videoende als farbige Leuchtspur mit ID und Ursprungsbild sichtbar, auch bei einem einzigen Trefferframe. Bekannte Bahnen werden nachgezeichnet und anhand der Sternregistrierung mitgeführt. Bei punktförmigen Einzeltreffern bleibt ein Leuchtring erhalten; es wird keine unbekannte Flugbahn erfunden. Diese Darstellung zeigt die Erkennungshistorie, nicht ein weiterhin sichtbares Objekt.

**Verwacklungsfilter (immer aktiv bei Bildfolgen):** Vor der Erkennung werden die Profile von bis zu 80 hellen, räumlich getrennten Sternen auf linearen Bilddaten geprüft. Gemeinsam verlängerte oder doppelte Sterne kennzeichnen Bewegung während der Belichtung. Deutlich verbreiterte Profile werden außerdem mit den schärferen Aufnahmen der Serie verglichen. Solche Bilder gelangen weder ins Video noch in die Ereigniserkennung oder die Referenzbilder; der nächste Vergleich verwendet das letzte geeignete Bild. Ein einzelner echter Streifen lässt die übrigen Sterne scharf und führt deshalb nicht zum Ausschluss des ganzen Bildes. Reine Verschiebung zwischen zwei scharfen Aufnahmen bleibt erlaubt.

Originaldateien werden **nicht gelöscht oder verschoben**. Ausschlüsse stehen mit Dateiname, Aufnahmezeit, Messwerten und Grund in `ausgeschlossen.jsonl`; rote `ausgeschlossen-*.jpg`-Vorschauen erleichtern die Kontrolle. Der Bericht nennt verwendete und ausgeschlossene Bilder. Die Videozeit verkürzt sich entsprechend, die echten Aufnahmezeiten bleiben erhalten. Bei zu wenigen geeigneten Sternprofilen ist eine sichere Schärfebeurteilung nicht möglich; der Status wird protokolliert, ohne eine sichere Verwacklung vorzutäuschen. Eine Stichprobe von bis zu 17 Bildern legt die Schärfereferenz fest; eine überwiegend schlechte Serie kann dadurch nur eingeschränkt beurteilt werden.

1. `AstroAnalyse.lnk` oder `Starten.cmd` öffnen. Als Quelle den Stellina-Stammordner wählen, darunter über **Beobachtung** die gewünschte Sitzung auswählen. Ein einzelner Beobachtungsordner kann auch direkt als Quelle gewählt werden. **Alle Beobachtungen** verarbeitet jede Serie getrennt. Standardauswahl ist die erste M82-Sitzung.
2. Modus **Bildfolge → Ereignisvideo** wählen. Es werden **alle Originalbilder** der Serie verwendet; die Stichprobeneinstellung gilt nur für Fotos. FITS haben Vorrang, `01-images-initial` wird bevorzugt, Stacks/bearbeitete Kopien werden nicht beigemischt. Mehrere Originalbildordner ergeben getrennte Videos.
3. Video-Bildrate wählen (Standard 8 Bilder/s). Das ist ein **Zeitraffer**, keine Echtzeitrekonstruktion. Echte Aufnahmezeiten stehen im Overlay und in `frames.jsonl`. FITS DATE-OBS hat Vorrang, sonst Dateinamenszeit (Zeitzone unbekannt), sonst natürliche Dateinamenreihenfolge. Aufnahmelücken werden nicht mit künstlichen Bildern aufgefüllt.
4. Optional **Plate Solving + Himmelsobjekt-Overlay** aktivieren. ASTAP gleicht das Sternmuster lokal gegen den D20-Katalog ab. Der Ordnername dient nur als Suchhinweis. Beschriftungen entstehen erst nach erfolgreicher Lösung aus WCS-Koordinaten und dem lokalen ASTAP/HNSKY-Katalog. Die erste Lösung wird über direkte Sternregistrierung auf Folgeframes übertragen; bei Ausrichtungsfehlern werden Beschriftungen ausgeblendet.
5. **Start**: Live-Vorschau, aktuelles Bild, Kandidaten und Fortschritt erscheinen. **Stopp**: aktuelles Bild abschließen und MP4 finalisieren. Beim Schließen wird ebenfalls erst gespeichert. Ein neuer Start beginnt einen neuen Lauf, keine automatische Fortsetzung.
6. **Video abspielen**, **Bericht öffnen** oder **Ergebnisordner** nutzen. Das MP4 ist nach Abschluss/Stopp abspielbar; währenddessen dient die Live-Vorschau als Ansicht.

## Erkennung

**Sternfeld-Bewegungskorrektur:** Sternzentren werden im gesamten Bild gemessen und über eine robuste affine RANSAC-Anpassung verglichen. Gemeinsame Verschiebung, Rotation und kleine Maßstabsänderungen werden abgezogen. Einzelne Tracks müssen zusätzlich klare Ausreißer gegenüber der Bewegung und Streuung des Sternensembles sein. Bei unzuverlässiger Anpassung werden keine unabhängigen Beweger freigegeben. `frames.jsonl` enthält dafür `star_field` mit Zahl der Referenzsterne, Anpassungsfehler, Streuung und Freigabegrund.

Für Stellina werden vorhandene `*.Info.txt`-Sternzentren verwendet und auf die Analysepixel skaliert. Ansonsten sucht DAOFIND auf linearen Bilddaten nach Sternprofilen. Bei sparsamen Feldern wird eine Ähnlichkeitstransformation (Verschiebung/Rotation/einheitlicher Maßstab) mit mindestens vier robusten, räumlich verteilten Zuordnungen und Leave-one-out-Gegenprüfung verwendet; für volle affine Modelle bleiben mindestens zwölf nötig. Der mediane Anpassungsfehler bleibt auf 0,45 Pixel begrenzt. Eine Dreiecksmuster-Suche und die zuletzt erfolgreich ausgerichtete Aufnahme dienen als Rückfall, wenn die Startschätzung oder die erste Referenz nicht mehr passt. Weniger als vier vorhandene Sternzentren werden weiterhin als unsicher gewertet. Sensorfeste Defekte werden ausgeschlossen und unabhängige Beweger erst nach drei aufeinanderfolgenden Ausreißerbestätigungen ausgegeben.

**Einzelbild-Streifen:** Eine zusätzliche Linienintegration sucht auch schwache lange Spuren, ohne einen bewegten Punkt vorauszusetzen. Hintergrundabzug, Sternmaskierung, Hough-Suche und integrierter schmaler Linienkontrast werden mit einer ausgerichteten Nachbaraufnahme verglichen, um feste Galaxien und Sternreste auszusondern. Das findet auch Spuren aus nur einer Belichtung. Die Klassifizierung Meteor/Satellit/Flugzeug bleibt ohne weitere Belege offen.

Das offizielle OpenSkyCam-Modell kennt Meteore und Flugzeuge. Für Teleskopaufnahmen werden feste Sterne und punktförmige Fehlalarme durch Sternregistrierung, Bilddifferenzen und eine Mindestspurlänge unterdrückt. Das kann kurze/schwache Ereignisse übersehen. Lange transiente Spuren werden zusätzlich durch Hough-Linien gesucht. Ohne sichere Modellklasse lautet die Markierung **Spurkandidat: Meteor/Satellit/Flugzeug**.

Langsame Punktquellen werden relativ zum Sternfeld über mindestens acht Detektionen verfolgt und gegen die Bewegung des gesamten Sternensembles geprüft. Genügend unabhängige, geradlinige Verschiebung führt zur Markierung **Unabhängiger Beweger (unbestätigt)**. Artefakte/Hotpixel/Registrierungsfehler sind weiter möglich. **Kein Ephemeriden-/MPC-Abgleich, keine bestätigte Asteroidenidentifikation.** Mit gültiger WCS werden RA/Dec der Kandidaten gespeichert.

Prozentwerte sind Modellkonfidenzen, keine kalibrierten Wahrscheinlichkeiten. Das Modell wurde auf einer anderen Weitwinkelkamera trainiert. Satelliten können als Flugzeuge erscheinen. Lange Belichtungen und Aufnahmelücken begrenzen die Unterscheidung. Keine Kandidaten ist kein Nachweis für Abwesenheit.

Die FITS-Vorschau wird gestreckt und um den jeweiligen Hintergrund zentriert; Bayerbilder werden 2×2 gebinnt. Das Video ist keine photometrische Messgrundlage.

## Gespeicherte Dateien

Pro Lauf unter `Ergebnisse`: `bericht.html`, `videos.json`. Pro Serie:

- `ereignisse.mp4`: alle lesbaren Frames, H.264/yuv420p, Zeit-/Ereignis-/optionale Himmelsobjekt-Overlays.
- `frames.jsonl`: Originaldateien, Aufnahmezeit, Videozeit, Metadaten, Registrierungsqualität und Detektionsboxen.
- `ereignisse.jsonl`: Frames mit Kandidaten, Klassen und Nachweismethode.
- `treffer-*.jpg`: markierte Trefferbilder; `live.jpg`: letztes Bild.
- `platesolve`: generierte FITS-Kopie, WCS und Solverprotokolle.

Originale werden nur gelesen. ASTAP schreibt ausschließlich im Ergebnisordner. Es werden keine Fotos hochgeladen.

## Prüfungen und Quellen

`.venv\Scripts\python.exe python/check_sequence.py` prüft Sternregistrierung, Unterdrückung fester Quellen, injizierte Spuren/Beweger, Sortierung, Auswahl der Originalserie und dekodierbare H.264-Ausgabe.

`.venv\Scripts\python.exe python/check_ensemble.py` prüft gemeinsam wackelnde/rotierende Sternfelder, einen unabhängigen Beweger darin, schwache Einzelbildstreifen und die Unterdrückung fester ausgedehnter Quellen.

[OpenSkyCam-Modellkarte](https://github.com/Deibler/openskycam/tree/main/models/night-detector), [ASTAP-Dokumentation](https://www.hnsky.org/astap.htm), [D20/Gaia-Katalog](https://sourceforge.net/projects/astap-program/files/star_databases/). Solver und D20 liegen in `tools/astap`. Der Deep-Sky-Katalog wird aus der vorhandenen ASTAP-Installation gelesen. Datenherkunft und Lizenzhinweise stehen in den Originalpaketen und ASTAP-Dokumenten.
