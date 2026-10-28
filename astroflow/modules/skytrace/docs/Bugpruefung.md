# Bugprüfung · 27. Oktober 2026

Die gefundenen Fehler wurden korrigiert und durch Regressionstests abgesichert.

## Behobene Fehler

- **Scheinbar festhängender Asteroidenabgleich:** JPL braucht je Kandidat teils mehrere zehn Sekunden. Die Live-Anzeige zeigt jetzt Kandidatenzähler und verstrichene Antwortwartezeit auch ohne neue Protokollzeile. Neue Abfrageprozesse melden alle fünf Sekunden ihren Fortschritt; einschließlich JPL-Warteschlange gilt ein Gesamtzeitlimit von 45 Sekunden. Anhalten beendet auch eine wartende HTTP-Verbindung. Zeitüberschreitungen werden als unvollständiger Abgleich ausgewiesen.
- **Live-Ansicht auf mehreren Geräten:** Eine auf einem anderen Gerät gestartete Analyse wird jetzt automatisch übernommen. Alte Kandidaten und Berichtslinks werden beim Wechsel geleert.
- **Veraltete Antworten:** Langsame Anfragen für einen früher gewählten Lauf überschreiben keine neuere Auswahl mehr. Hintergrundaktualisierungen verdrängen keine laufende manuelle Auswahl.
- **Serverneustart:** Browser erneuern Zugriffstoken und Einstellungen; Protokollzähler beginnen für die neue Serverinstanz korrekt neu.
- **Einstellungen:** Beim Öffnen werden aktuelle Werte vom Server gelesen. Gleichzeitige Speichervorgänge verschiedener Geräte gehen nicht mehr verloren. Teiländerungen löschen keinen vorhandenen Standort.
- **Videofortschritt:** Gezählt werden verarbeitete Frames statt der nur gelegentlich gesendeten Vorschau-Ereignisse. Auch das letzte Frame wird gemeldet.
- **Videowiedergabe:** Ein anderes Beobachtungsvideo beginnt bei null. Beim Umschalten der Overlays innerhalb desselben Videos bleibt die Position erhalten.
- **Kandidatenfilter:** Ein neu ausgewählter Kandidat öffnet bei seinem eigenen Trefferbild statt bei der zuletzt benutzten Position eines anderen Kandidaten.
- **Nicht erreichbarer Quellordner:** Gespeicherte Berichte bleiben zugänglich und die Bedienung wird trotzdem initialisiert.
- **Fehlerhafte Eingaben:** Falsche JSON-Strukturen, gebrochene Bilderzahlen und ungültige Einstellungstypen werden mit verständlichen Eingabefehlern zurückgewiesen. Fotoanalysen bieten keine ungeeignete Bildfolgen-Aktualisierung mehr an.
- **Gleichzeitiges Schreiben und Lesen:** Berichtsmetadaten und Live-Bilder werden vollständig veröffentlicht. HTTP-Längen und Dateiinhalte stammen aus derselben geöffneten Dateiversion. Kleine Vorschauen werden vor dem Senden aus der Dateisperre freigegeben.
- **Sporadische Windows-Dateisperre:** Der Fehler wurde mit parallelen Bildwechseln als „Permission denied“ reproduziert. Kurze Wiederholungen beim Öffnen und Ersetzen verhindern den vorübergehenden Fehler, ohne unvollständige Bilder auszuliefern.
- **Testisolation:** HTTP-Tests verwenden einen eigenen Marker für den letzten Lauf und verändern den Marker der laufenden Anwendung nicht mehr.

## Bestandene Prüfungen

| Prüfung | Inhalt |
|---|---|
| `python/check_evidence.py` | Gemessener Linienkontrast, sternregistrierte Nachbarn, unverfälschter Detektorwert, Winkel über RA 0° und Ausschluss unscharfen Kontexts |
| `python/check_catalog_wait.py` | Echter lokaler HTTP-Abruf, verzögerte Antwort, hartes Gesamtzeitlimit, Abbruch während HTTP und Dateisperre sowie regelmäßige Wartehinweise |
| `check_web_ui.cjs` | Umgekehrte Antwortreihenfolge, Hintergrundabrufe, Token-Erneuerung, fremder Analysestart, Neustart, Kandidatenauswahl, fehlender Quellordner, frische Einstellungen, Videoposition |
| `python/check_web.py` | HTTP-Schnittstelle, Host/Origin/Token, Pfadgrenzen, MP4-Bereiche, 16 parallele Standortänderungen, Videofortschritt, Live-Ereignisse, echte Analyse-Unterprozesse, Bilderlimit und kooperativer Stopp |
| `python/check_preview_race.py` | 500 Bildwechsel und 800 gleichzeitige HTTP-Abrufe: vollständige und dekodierbare Bilder |
| `python/check_candidates.py` | Unbeschriftete Ausschnitte, UTC-Kontext, WCS, historische Datenabdeckung und Kataloggeometrie |
| `python/check_lan.py` | Erlaubtes Heimnetz, fremde Subnetze, Hosts und Ports |
| `python/check_quality.py` | Verwacklung, Doppelbilder, Defokus, Erhalt einzelner echter Spuren und unveränderte Originaldateien |
| `python/check_sequence.py` | Registrierung, feste Quellen, bleibende Spuren, Aufnahmezeiten und H.264-Ausgabe |
| `python/check_ensemble.py` | Gemeinsame Sternfeldbewegung, unabhängiger Beweger, schwache Linien, Hotpixel und wechselnde Referenzsterne |

Zusätzlich wurden Einstellungen, Kandidatenfilter und Zeitreihen in der Browseroberfläche geprüft. LAN-HTTP wurde auf dem Analyse-PC über dessen Netzwerkadresse getestet; ein separates physisches Endgerät wurde nicht ferngesteuert. Die Prüfung des Programmverhaltens bestätigt keine wissenschaftliche Identität einzelner Himmelsobjekte.
