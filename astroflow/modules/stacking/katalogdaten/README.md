# Daten: Messier und Caldwell

`messier_caldwell.csv` ist aus zwei Quellen zusammengestellt (beide **CC-BY-SA 4.0**, Volltext: `LICENSE-OpenNGC`):

1. **OpenNGC** (https://github.com/mattiaverga/OpenNGC, Release v20260501, Commit 36cb178a0f69dba8bfc03a99c10512831edf1c6b; `NGC.csv`, `addendum.csv`),
   Mattia Verga und Mitwirkende – Position J2000, Kennungen, Galaxiengrößen, Rückfall-Helligkeit.
2. **Wikipedia (en)**, „Messier object“ (Revision 1372398875, 31.08.2026; Werte dort aus SEDS/Sky Catalogue) und „Caldwell catalogue“
   (Revision 1383500944, 03.11.2026), Autoren laut Versionsgeschichte – Helligkeit und sichtbare Größe (Größe nur Messier).
3. **Wikipedia (en), Einzelartikel der Caldwell-Objekte** (Infobox „Apparent dimensions“ bzw. bei C46 die Helligkeit) – sichtbare Größe von Haufen,
   Nebeln und Planetarischen Nebeln; benutzte Artikel und Revisionen: `quellen_artikel.csv`.

Die Datei ist von AstroFlow ausgewählt, zusammengeführt und umgerechnet (RA in Grad, Größe in Bogenminuten); sie darf nur unter denselben
Bedingungen (CC-BY-SA 4.0, mit Namensnennung) weitergegeben werden. Der übrige AstroFlow-Code (PolyForm Noncommercial 1.0.0) bleibt davon getrennt; deutsche Namen und
Objektarten liegen in `deepsky.py`.

Spalten `mag_src`/`size_src`: W = Wikipedia, O = OpenNGC, B = OpenNGC B-Helligkeit − 0,85 (Galaxien), H = von Hand (unverifiziert; nur C34, C88, C94 – Größe, Begründung im Skript).
Vorrangregeln und die Prüfung der Quellen gegeneinander stehen in `tools/openngc_extract.py`.

Neu erzeugen: `NGC.csv`, `addendum.csv`, `Messier_object.wiki`, `Caldwell_catalogue.wiki` in einen Ordner legen (siehe Skriptkopf), dann `python tools/openngc_extract.py <ordner> --artikel-holen` ausführen (lädt einmalig `Caldwell_artikel.json`; danach genügt ohne Schalter) – nur beim Entwickeln, zur Laufzeit kein Netz.
