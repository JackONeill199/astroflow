# Daten: Messier und Caldwell

`messier_caldwell.csv` ist ein Auszug aus **OpenNGC** (https://github.com/mattiaverga/OpenNGC, Release v20260501,
Commit 36cb178a0f69dba8bfc03a99c10512831edf1c6b; Dateien `NGC.csv` und `addendum.csv`), Mattia Verga und Mitwirkende,
Lizenz **CC-BY-SA 4.0** (Volltext: `LICENSE-OpenNGC`). Die Datei ist von AstroFlow ausgewählt und umgerechnet
(RA in Grad, Helligkeit V, ersatzweise B); sie darf nur unter denselben Bedingungen (CC-BY-SA 4.0, mit Namensnennung)
weitergegeben werden. Der übrige AstroFlow-Code (MIT) bleibt davon getrennt; deutsche Namen und Objektarten liegen in
`deepsky.py`.

Neu erzeugen: `python tools/openngc_extract.py <ordner-mit-NGC.csv-und-addendum.csv>` (nur beim Entwickeln, zur Laufzeit kein Netz).
