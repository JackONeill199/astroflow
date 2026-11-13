# Nachführung (Tracking) – Recherche, Konzept und Umsetzung

Recherche 12.11.2026 (PR #96), Umsetzung ab 12.11.2026 (Abschnitt 0). Quellen je Aussage in Klammern; **[?]** = unsicher / nur Forum bzw.
Händlerseite, vor Verwendung prüfen. Fremde Texte sind nur zusammengefasst.

## 0. Umsetzung (Stand im Code)

**Entscheidungen des Nutzers (12.11.2026):** Forum-/Testwerte kommen als **„vorschlag“** in die Tabelle (je Wert Quelle + Status) und werden
später mit eigenen Aufnahmen kalibriert. EQ-Modus wird **automatisch erkannt** und ist im Planer je Gerät einstellbar. **Stellina** hat nahe am
Zenit kein Problem (Nutzererfahrung, als gesichert eingetragen: keine Zenit-Grenze).

**Geräte-Tabelle** `stacking/devices.json` + Lader `stacking/devices.py` – eine Quelle statt `tutor.DEVICES`, `planner.DEVICES`,
`deepsky.MAG_LIMITS` und festem 80°-Zenit (die drei Namen bleiben als abgeleitete Ansichten: `tutor_devices()`, `planner_devices()`,
`mag_limits()`). Je Gerät: `mount` (altaz/eq), `derotator` (none/software/optical), `eq_mode`, `zenith_limit` (Höhe, `null` = keine bzw.
unbekannt), `exp_altaz`/`exp_eq` (s), `sensor` (Pixel, für das Randverschmieren), `fov` (°), dazu Grenzgröße, Öffnung und Brennweite (mit Quelle, für den Belichtungsbedarf – `belichtungsbedarf.md`), Böen,
Tauheizung, Tipps (`texts`, Familie über `family`). Werte mit Herkunft: `{"v": …, "status": "gesichert"|"vorschlag", "quelle": "…"}`.
Neue Geräte: Eintrag ergänzen (Test `test_devices.py` prüft Status/Quelle); in den Planer nur mit `planner: true` und bekanntem `fov`.

**Bildfelddrehung statt Zenit-Abschlag** (`devices.profile/weights`, genutzt von `tonight.plan` und `planner._night`):
Rate `ω·cos φ·|cos A| / cos h` (°/min, nahe Zenit auf cos 89,5° begrenzt) → Randverschmieren je Belichtung = Drehung (rad) × halbe
Sensordiagonale (px). Gewicht = `min(1, 1,5 px / Verschmieren)`, mindestens 0,15; über der Zenit-Grenze des Geräts zusätzlich × 0,4.
Modus je Gerät: `software` (Vespera, Seestar, Dwarf, eVscope – Rate voll), `derot` (Stellina – Rest 10 %), `eq` (EQ-Modus/Montierung – kein
Abschlag), `legacy` (unbekanntes Alt-Az-Gerät – wie bisher > 80°: 0,4), `none` (Astrokamera/unbekannt ohne Alt-Az – wie bisher kein Abschlag).
Belichtung: aus den eigenen Aufnahmen, sonst `exp_altaz`, sonst 10 s. `zenith_hours` heißt jetzt „Stunden mit Abschlag“ (Gewicht < 0,75);
der Hinweis empfiehlt „vor oder nach der höchsten Stellung“, nennt die Zenit-Grenze mit Status und bei EQ-fähigen Geräten den EQ-Modus.
`devices.max_exposure` rechnet `T_max` (ω in rad/s). Planer: Assistent-Schalter „im EQ-Modus“ je gewähltem EQ-fähigem Gerät (`wishes.eq`).

**Beispielnacht** (erfundener Ort 50° N / 10° O, 02.11.2026, M31/NGC 7000/M45 als eigene Objekte): Heute Nacht, nutzbare Stunden M31
(kulminiert ~88°) – vorher 7,3 h für alle Alt-Az-Geräte; jetzt Stellina 7,9 h (Derotator, kein Abschlag), Vespera 6,9 h, Seestar 5,6 h
(Grenze 70°), Seestar im EQ-Modus 7,9 h. Planer (3 Nächte, Mischung, 2 h): für Stellina/Vespera gleich, für den Seestar wandert C4
(NGC 7023, +68°) aus dem Abendblock in die zweite Nachthälfte, abends kommt M27.

**Gerätediagnose (DIAG_VERSION 2):** je Nacht `sky_track` (Höhe, Azimut, parallaktischer Winkel q, Drehrate je Aufnahme aus Zeit, Ort
der Nacht über `tutor.pick_site` und Objektlage; nichts in der DB, nur im Diagnose-Ergebnis). **Montierung** `mount`: Theil-Sen der
gemessenen Bilddrehung (Registrierung) gegen q (`tracking.rot_vs_q`); |Steigung| ≥ 0,6 = `altaz` (dreht ohne Ausgleich), ≤ 0,2 = steht still →
`derot` bei optischem Derotator, `eq` bei EQ-fähigem Gerät bzw. Astrokamera, sonst `None` (unerwartet, nichts behauptet); q muss sich um
≥ 3° drehen. `diagnose.eq_detected(db)` → neueste erkannte Nacht je Gerät; **Heute Nacht** übernimmt `eq` für das zuletzt verwendete Gerät,
der **Planer** setzt den EQ-Schalter vor (`eq_default`). **Verworfene Aufnahmen** `sky`: Höhenklassen mit wegen Sternform verworfenen
Aufnahmen; Zone = Randverschmieren ≥ 1 px (aus gemessener Montierung, sonst Geräteprofil) oder über der Zenit-Grenze. Urteil `rotation`,
wenn die Zone ≥ 3 solche Aufnahmen hat und ihr Anteil ≥ doppelt und ≥ 15 Punkte über dem Rest liegt; `mixed`, wenn zusätzlich die Sterne in
Driftrichtung länglich sind (`elong_vs_drift` ≤ 25°, Kohärenz ≥ 0,4); sonst bei vielen Formfehlern `drift` (Länglichkeit in Driftrichtung oder
`smear_ratio` ≥ 0,5) bzw. `unclear`. Befunde `mount_eq` und `zenith_rejects` (nur bei `rotation`/`mixed`). Hinweis: Ein Objekt genau durch den
Zenit läuft dort ost-west (cos A → 0) – die Feldrotation bleibt endlich, problematisch ist dann die Azimutgeschwindigkeit; dafür steht die
getrennte Zenit-Grenze je Gerät. Alte Diagnosen (Version 1) zeigen „veraltet“ und werden mit „Neu berechnen“ ergänzt.

**Offen:** Randbeschnitt durch Rotation im Stack (B2), Kalibrierung der Vorschlagswerte aus eigenen Aufnahmen (Auswertung `sky.bins` über
viele Nächte), räumliche Elongationsmetrik (tangential = Drehung) – siehe Abschnitt 4.

**FITS-Kopfzeilen (geprüft 12.11.2026, nur lesend):** Stellina-Rohbilder enthalten `DATE-OBS`, `INSTRUME`, `EXPOSURE` (ms), `GAIN`, `OFFSET`,
`TEMP`, `PIXSZ`, `FOCAL`, `BAYERPAT` – **keine** Koordinaten, keinen Ort, keine Höhe/Azimut, keinen Montierungsmodus; die Begleitdatei
`params.json` nennt nur Belichtung/Gain/Anzahl/Spiegelung. Vespera-/Seestar-Dateien lagen nicht vor. Höhe/Azimut also immer aus Zeit + Ort
der Nacht (`core/sites`) + Objektposition rechnen. Gemessene Bilddrehung (Registrierung, `diagnose.tracking`) der Stellina: 0,002–0,19 °/min
(meist ~0,01–0,03) gegen typisch 0,1–0,5 °/min ohne Ausgleich – passt zum Derotator; konstante Lage allein ist deshalb **kein** EQ-Nachweis.

## 1. Grundlagen

**Alt-Az vs. äquatorial.** Eine äquatoriale Montierung (EQ) dreht um eine zur Erdachse parallele Achse: Das Bildfeld steht still. Eine azimutale
Montierung (Alt-Az) braucht zwei Achsen mit wechselnden Geschwindigkeiten, das Bildfeld dreht sich während der Belichtung (Bildfelddrehung).

**Drehrate (Standardformel).** Drehung über die Belichtungszeit T: `Q = ω·T·cos(φ)·cos(A) / cos(h)` mit Breite φ, Azimut A (von Nord), Höhe h,
ω = Erdrate (15″/s ≈ 0,00417 °/s). Gleichwertig mit Zenitdistanz Z: `dp/dt ≈ 0,262·cos φ·cos A / sin Z` rad/h
(arXiv 2012.12680 SOXS; arXiv astro-ph/0609624 IRAIT). Folgerungen:
- maximal im Meridian nahe Nord/Süd (cos A = ±1), null im Ost-/West-Durchgang (A = 90°/270°);
- strebt gegen unendlich, wenn h → 90° (Zenit) – dort auch die Totzone der Achsen (IRAIT: kritische Geschwindigkeiten nahe Zenit);
- Rand-Verschmierung = Drehrate × T × Abstand zur Bildmitte (Keicher, „Field Rotation in Alt-Az Mounts“, 2005, via Suche; Wert nicht selbst geprüft).
  Daraus max. Belichtung: `T_max = Δmax / (r_Rand · ω · cos φ · |cos A| / cos h)` mit Δmax ≈ 1 Pixel (oder 0,5 × Sterngröße). **ω muss hier in Radiant pro Sekunde stehen** (7,29·10⁻⁵ rad/s); mit 0,00417 °/s käme eine um 180/π ≈ 57,3 zu kurze Zeit heraus.
  Pixelgröße/Brennweite gehen über r_Rand (Halbdiagonale in Pixel) ein.
- **Vorsicht:** die Formel auf lambdageeks.com weicht ab (cos Alt·sin Az / cos Lat) – nicht verwenden. Die „Polausrichtungsfehler“-Rechner im Netz sind etwas anderes.

**Weitere Fehlerquellen** (allgemeines Wissen, nicht je Gerät belegt): periodischer Schneckenfehler (nur klassische EQ-Montierungen, Periode = Schneckenumlauf,
typ. Minuten); Polausrichtungsfehler → gleichmäßige Drift (Deklination) + langsame Feldrotation; Wind/Durchbiegung/weicher Boden → Stoßereignisse und Drift
(AstroFlow erkennt Böen schon, `diagnose.findings`); Kabelzug (Alt-Az-Gerät mit Kabel); atmosphärische Refraktion nahe Horizont (Drift in Höhe, verfärbte/verzogene Sterne,
Rechenkorrektur der Goto-Software, bei kleiner Höhe unzuverlässig); Meridianwechsel nur bei GEM (Umschlag der Montierung, Bildrotation um 180°).

**Derotator vs. Software-Derotation.** Ein Derotator dreht Sensor oder Bild mechanisch/optisch gegen die Feldrotation. Software-Derotation macht kurze Einzelbelichtungen
(Rotation innerhalb des Bilds klein) und dreht beim Stacken die Bilder zurück: kein Sternverzug im Einzelbild nötig, aber die Überlappung der Randbereiche schrumpft →
Beschnitt (Vaonis: Vespera-Fotos sind „oft etwas kleiner“ wegen automatischem Zuschnitt [Waloszek-Seite], Vaonis-Support: kein mechanischer Derotator, Bildfeld dreht beim Stacken).

## 2. Geräte

| Gerät | Montierung / Ausgleich | Belichtung / Grenzen | Quelle |
|---|---|---|---|
| **Stellina** | Alt-Az, **integrierter Feld-Derotator** (optisch/mechanisch, Drehgeschwindigkeit je Ziel nachgeführt) | laut Vaonis-FAQ keine Zeitgrenze durch Rotation; Zenit-Grenze nicht gefunden **[?]**; Feld schrumpft durch Zuschnitt | Vaonis-Support „Is the field derotator integrated?“, Vaonis-Blog „Stellina & Vespera Initialization“ |
| **Vespera / II / Pro** | Alt-Az, **kein mechanischer Derotator**, Software-Derotation; EQ-Modus für Pro (1) nicht belegt, Pro II „nicht veröffentlicht“ | Einzelbelichtungen 10 s, damit Rotation im Bild nicht sichtbar; Vespera II laut Test besser nahe Zenit (10° davon: Sterne mittig rund, am Rand leichte Streifen), Rat: trotzdem meiden. **Hinweis:** ein Händler schreibt dem „Pro II“ ein Wegfallen der Rotation ohne EQ zu – Herstellerbeleg fehlt **[?]** | Vaonis-Support „How Vespera compensate…“, Mile High Astro (Vespera II First Impressions), waloszek.de |
| **Seestar S50 / S30** | Alt-Az, Software; **EQ-Modus** (Polhöhenwiege/Neigung nach Breite + Einnorden) | Alt-Az: Standard 10 s (Agena-Test: kürzer ist besser); EQ: 30 s und länger (Agena, S50-Pro-Tests); Zenit: Community-Wiki nennt **ab ca. 70° Höhe** Streifen/Verwerfen **[?]**, kein Herstellerwert | Agena Astro „How to set up EQ mode…“, Unofficial Seestar Wiki (FAQ) |
| **Dwarf II / 3 / Mini** | Alt-Az; EQ-Modus per Neigung nach Breite + Wizard; Sonne/Mond nur Alt-Az | EQ: mehrfach genannt bis 60 s (Dwarf 3, nicht offiziell belegt **[?]**), Alt-Az deutlich kürzer; Mini: 15 s Alt-Az vs. 120 s EQ (nur Mini) | DWARFLAB-Handbuch Teil 2, Sky at Night (Test Dwarf 3), High Point Scientific |
| **Unistellar eVscope / eQuinox** | Alt-Az, Software-Derotation + „Epochen“; eQuinox ähnlich | max. Zenitwinkel 85°, **empfohlen unter 80°**; Hilfe-Artikel nennt zusätzlich „20°“ (unklar ob Min-Höhe) **[?]** | help.unistellar.com „About Derotation, Zenith Angle and Epochs“ |
| **Celestron Origin** | Alt-Az (Gabel), optional **Keilmontierung** (EQ) + Autoguiding | Dritte: 15–30 s Alt-Az (Scopetrader); Celestron selbst: 10 s ohne Rotation, ab ~30 s sichtbar; Zenit nur mit Keil sinnvoll | Celestron-Wissensdatenbank/Produktseite, scopetrader.com |
| **Klassische EQ (GEM)** | Rotation nur durch Polfehler; PE ≈ ±10–30″ (typisch), mit Guiding < 1″ | Zenit unkritisch, aber **Meridianwechsel** | allgemeines Wissen |
| **Alt-Az mit Derotator** (z. B. Teleskope mit Feldrotator) | wie Stellina | Zenit-Totzone bleibt wegen Achsgeschwindigkeit | IRAIT-Paper |

**FITS-Kopfzeilen.** Im Code (`fits_io.py`) liest AstroFlow nur wenig (EXPTIME, Bildformat/INSTRUME für die Geräteerkennung in `tutor.detect_device`). Welche der folgenden
Schlüssel die echten Dateien enthalten, wurde **nicht** geprüft (echte Kopfzeilen der Arbeitsinstanz nicht angesehen – offene Aufgabe, siehe Fragen):
typisch bei Smart-Teleskopen sind Zielkoordinaten (RA/DEC, OBJCTRA/OBJCTDEC), Zeit (DATE-OBS), Breite/Länge (SITELAT/SITELONG) – **Höhe/Azimut und Montierungsmodus stehen
wahrscheinlich nicht drin**. Das ist unproblematisch: Höhe/Azimut lassen sich aus Zeit + Ort + gelöstem RA/Dec berechnen (`tonight.py` rechnet das ohnehin ohne AltAz-Transformation).
Den EQ-Modus erkennt man am besten **aus den Daten**: bei EQ driftet die Bildfeldrotation nicht (Rotationswinkel der Plate-Solves über die Nacht konstant). **Aber:** konstante Rotation zeigt auch ein Alt-Az-Gerät mit mechanischem Derotator (Stellina), dort bleibt die Achsen-Totzone am Zenit. Daher gilt konstante Orientierung nur zusammen mit der Gerätefähigkeit (`eq_mode`) oder einer vom Nutzer bestätigten Montierungsart als EQ-Nachweis, nie allein.

## 3. Was AstroFlow schon hat

- **Planer/Heute Nacht:** `tonight.ZENITH_ALT = 80°`, `ZENITH_WEIGHT = 0,4`, für alle Geräte mit `alt_az`; `planner.DEVICES` (stellina, vespera, vespera2, seestar, seestar30) mit `fov`, `alt_az`,
  Mindesthöhe `tonight.MIN_ALT` (30°) bzw. Horizontmaske. **Ein** Zenit-Wert für alle Geräte, kein Gerät mit eigener Zenit-Totzone, kein EQ-Modus, keine Feldrotationsrate.
- **Gerätediagnose** (`diagnose.py`): Nachführung (`tracking`): Bildverschiebung während der Belichtung (`smear_px`, `smear_ratio`), Verhältnis zur Sterngröße, Vorzugsrichtung länglicher
  Sterne (`ecc_pa`, `quality.py`) gegen Driftrichtung (`elong_vs_drift`), periodischer Fehler (nur Nicht-Smart), Stöße/Böen, Fokus. Tipps bei Alt-Az: „Objekte zwischen 30° und 70° Höhe“.
  `feldrot`-Hinweis steht schon bei Mosaiken (`field["smeared"]`).
- **Tutor** (`tutor.py`): `DEVICES` mit `alt_az`, `gust`, Hinweis „Zenit“ bei Höhe > 78° (`alt_max`); Geräteerkennung aus INSTRUME/TELESCOP bzw. Bildformat.
- **Frame-Metriken:** `ecc_pa`, `trail_lines`, FWHM; Plate-Solves je Aufnahme (Lage/Rotation).
- **Fehlt:** Höhe/Azimut/Feldrotationsrate **je Aufnahme** (nicht gespeichert); Beschnitt im Stack durch Feldrotation; eine zentrale **Geräte-Tabelle** (die Daten stehen jetzt dreifach: `tutor.DEVICES`,
  `planner.DEVICES`, `tonight`); Montierungsmodus (EQ/Alt-Az) je Nacht; Auswertung „verworfen nach Höhe/Azimut“; Dwarf, eVscope, Origin im Planer.

## 4. Konzept

Priorität A = klein und wertvoll, B = mittel, C = groß/später.

**A1 – Geräte-Tabelle als Daten** (`core`- oder `stacking/devices.json`, ein Eintrag je Gerät). Ersetzt die drei Insellisten. Felder: Name, Montierung (`altaz`/`eq`/`altaz+derot`), `derotator`
(`none`/`software`/`optical`), `zenith_deadzone_deg` (ab Höhe), `max_alt_recommended`, `min_alt`, `exp_typ_s` (Alt-Az), `exp_typ_eq_s`, `fov`, `gust`, `eq_mode` (ja/nein/Zubehör), Quelle je Wert, Status `gesichert/forum`. Aufwand klein (Daten + Umstellen von drei Stellen), Test: Werte plausibel.
**Startwerte** (mit Vorbehalt): eVscope 80° empfohlen / 85° max (Hersteller); Seestar 70° (Forum **[?]**); Vespera II ~75–80° (Test, **[?]**); Stellina/Vespera ~80° (jetzige Annahme, Stellina mit Derotator **[?]**).

**A2 – Feldrotationsrate im Planer und „Heute Nacht“.** Statt hartem Schwellwert eine Rate `rot(h,A,φ)` (siehe Formel) und `T_max`; Gewicht der Stunde = f(Rand-Verschmierung gegen Sterngröße). Pro Gerät
(Derotator: Rate zählt wenig, Totzone bleibt; Software-Derotation: Rate zählt, Totzone + Randbeschnitt; EQ: Rate = 0). Dazu Hinweis **„besser Ost/West als Meridian bei Nord/Süd“** (cos A klein) und
„vor/nach Meridian“ bei GEM. Aufwand mittel (tonight/planner), nur numpy.

**A3 – EQ-Modus als Nachtoption** im Planer (Geräte mit `eq_mode`): wenn der Nutzer EQ meldet (Schalter je Nacht/Gerät) oder die Analyse konstante Rotation zeigt, entfällt der Zenit-Abschlag. Klein bis mittel.

**B1 – Aufnahmen je Höhe/Azimut erklären (Analyse).** Je Aufnahme aus Zeit+Ort+Plate-Solve Höhe/Azimut/Rotationsrate berechnen (in `frames.info`, kein neues DB-Feld, optional wie die Zusatzmetriken ohne `METRICS_VERSION`-Erhöhung)
und in der Diagnose zeigen: „von N verworfenen Aufnahmen lagen M in Höhe > 75° (Zenit-Totzone)“; Verhältnis Rotationsrate ↔ Elongation (Sternlängen **tangential** zur Bildmitte = Feldrotation; alle gleich gerichtet = Drift/Wind;
rundum gleichmäßig = Fokus). **Der gespeicherte `ecc_pa` reicht dafür nicht:** er ist nur ein Mittelwinkel plus Einheitlichkeit je Aufnahme, die Zuordnung Stern-Position ↔ Richtung geht verloren.
Nötig ist eine neue räumliche Zusatzmetrik (z. B. je Bildkachel Winkel und Elongation, oder Positions-/Winkelpaare der Sterne; wie `tiles`/`ecc_pa` ohne `METRICS_VERSION`-Erhöhung nach Nutzerentscheidung). Aufwand mittel; Version `DIAG_VERSION` erhöhen.

**B2 – Randbeschnitt durch Rotation im Stack anzeigen** (Anteil verlorene Fläche; ggf. Empfehlung „weniger lange Sessions im Meridian“). Daten sind in der Registrierung vorhanden (Rotationswinkel je Frame); Aufwand klein–mittel.

**B3 – Empfehlungen je Gerät** aus der Tabelle (Texte statt Code): EQ-Modus anbieten, Mosaik-Modus, Tauheizung, Wind – ersetzt Tipps in `diagnose.findings`/`tutor`.

**C1 – Echtzeit-Prognose** („in 40 min läuft das Ziel in die Zenit-Totzone“) für den Planer, **C2** Gerätemodelle Dwarf/Origin/eVscope vollständig (braucht eigene Daten).

## 5. Offene Entscheidungen / Fragen an den Master bzw. Nutzer (beantwortet 12.11.2026, siehe Abschnitt 0)
1. Zenit-Werte der Geräte: Forum-/Testwerte (Seestar 70°, Vespera II ~75°) in der Tabelle als „Vorschlag“ nutzen oder erst mit eigenen Aufnahmen kalibrieren (A-Auswertung B1 liefert die Daten)?
2. Echte FITS-Kopfzeilen der Stellina/Vespera auf Höhe/Azimut/Montierungsmodus prüfen – lohnt als eigener kleiner Auftrag (nur lesen).
3. EQ-Modus: vom Nutzer pro Nacht angeben lassen, oder automatisch aus Plate-Solve-Rotation ableiten?
4. Stellina-Derotator: laut Vaonis-Support vorhanden (optisch/mechanisch) – eigene Erfahrung des Nutzers (Randbeschnitt? Zenit-Verhalten?) bestätigen.

## Quellenübersicht
- Vaonis-Support: *Does Vespera feature a field derotator?*, *How Vespera compensate for the lack of field derotator?*, *Is the field derotator integrated?* (support.vaonis.com), Vaonis-Blog *Stellina & Vespera Initialization*.
- Mile High Astro, *Vespera II First Impressions*; waloszek.de (Vespera-FAQ/Tipps, Vespera Pro).
- Agena Astro, *How to Set Up & Use EQ Mode on the ZWO Seestar S50 & S30*; Unofficial Seestar Wiki (FAQ).
- DWARFLAB-Handbuch (Dwarf 3, Teil 2); Sky at Night Magazine, Dwarf 3 Test; High Point Scientific (Dwarf 3/Mini).
- Unistellar-Hilfe *About Derotation, Zenith Angle and Epochs*.
- Celestron-Wissensdatenbank (*What is field rotation?*), Produktseite Origin; scopetrader.com (Origin).
- Formeln: arXiv 2012.12680 (SOXS), arXiv astro-ph/0609624 (IRAIT); Keicher, *Field Rotation in Altitude over Azimuth Mounts* (2005); Cloudy Nights (Thread zu Feldrotation).
