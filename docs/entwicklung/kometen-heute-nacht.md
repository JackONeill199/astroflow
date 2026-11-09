# Kometenmodus und Heute Nacht

> Ausgelagert aus `AGENTS.md` am 09.11.2026, Text unverändert übernommen. Regeln, die beim Ändern von Code gelten,
> stehen zusätzlich in Kurzform in `AGENTS.md` → „Wenn du … änderst“. Neue Befunde hier ergänzen, nicht in `AGENTS.md`.

## Kometenmodus (`modules/stacking/comet.py`, seit 0.22.0 – Nutzerwunsch, nur noch Kometenaufnahmen fehlten)
- **Erkennung:** `comet.designation/is_comet` (C/P/D/X/A/I + Jahr + Halbmonat, `12P`, Wörter Komet/Comet; `IC 1396`,
  `NGC 2024` nicht). `scanner.assign_project(info, centers, night)`: Kometen → Schlüssel `KOMET:<Name>|<Nacht>|<Kamera>`
  (je Nacht ein Objekt, Komet steht vor anderen Sternen), `kind='comet'`, Name „C/2022 E3“; nie über Koordinaten zugeordnet.
  Projektoption `comet_mode` auto|on|off (**nicht** `mode` – das ist bei Aufträgen „develop“), `comet_name` (Bezeichnung für Horizons).
- **Ablauf** (`Pipeline._comet` am Ende von `stack()`, nur ein Band): Sterne-Stack wie immer (Fortschritt 36–60 %), dann
  `_comet_track`: Horizons (`comet.horizons`, Cache `work/komet/`, geozentrisch abgefragt, Parallaxe lokal per astropy
  aus Standort `FrameInfo.site`, sonst Ort der Nacht aus der gemeinsamen Ortsliste (`services["sites"]`), sonst Tutor-Einstellung – Standort verlässt den Rechner nie; WCS des Sterne-Stacks über
  `_stack_wcs`, gleicher Cache `wcs.json` wie die Farbkalibrierung) und/oder Bildsuche: ≤ 36 Stichproben, Luminanz gebinnt
  auf ≤ 800 px, auf den Sterne-Stack registriert. Grob: Einzelbild − Sterne-Stack → Spitzen → RANSAC-Gerade. Fein (`locate`):
  Bahn so optimieren, dass der Kern im Mittel der entlang der Bahn ausgeschnittenen Differenzbilder (Referenz = Median der
  Stichproben) am schärfsten ist (Bandpass, 7×7-Gitter, 4 Runden); mit Ephemeride nur Versatz + lineare Drift.
  **Achtung:** Positionen je Einzelbild aus „Bild − Sterne-Stack“ überschätzen die Geschwindigkeit (~25 % synthetisch),
  weil die im Sterne-Stack verschmierte Spur abgezogen wird. Bahn = Polynom (`Track`, Grad 1, Ephemeride Grad 2).
- **Kometen-Stack** `work/p<id>/stack_comet[_<Band>]`: `_stack(shifts=…, mask=…, mark=False)` – je Frame Translation
  Kern(t0) − Kern(t) nach der Sternregistrierung; Sterne des Sterne-Stacks (`star_mask_for`, kleine/große Masken getrennt)
  verschoben als ungültig. Maskenteile auf der Bahn, deren Licht nicht im Kern sitzt (< 35 % in 1,5 FWHM), sind die
  verschmierte Koma und bleiben frei (sonst Loch im Kopf). Unter 3 FWHM Gesamtbewegung ist der Komet im Sterne-Stack kompakt →
  Bahnbereich ganz frei; unter 1 FWHM kein Kometen-Stack. Inkrementell: Frames in `state.json`, Bahn in `meta.comet`;
  weicht eine neue Bahn > 0,7 px ab, Neuaufbau. Infos in `work/p<id>/comet.json` → `results.stats.comet`.
- **Entwickeln** (`_comet_compose`): Sterne aus dem Sterne-Stack (`stars.separate`, StarNet falls da) + Kometen-Stack ohne
  Sternreste (Kern geschützt), Lücken aus dem sternlosen Sterne-Stack; Abdeckung geometrisch (sonst zu enger Ausschnitt).
  Profil `comet` (scnr 0, Gradient Grad 2), GraXpert nur Entrauschen – gemessen an C/2022 E3 (04.11.2026): KI-Hintergrund
  trägt die äußere Koma ab (Signal 0,050 → 0,027, Koma hart begrenzt, rotbrauner Hof, Score 10 → 7,9); KI-Entrauschen senkt
  das Farbrauschen 0,0061 → 0,0044 bei gleichem Signal (+20 s). `_save_singles`: `<base>_sterne.jpg`, `_komet.jpg`,
  `_komet_linear.fits` (Dateischlüssel `stars`, `comet`, `comet_fits`; UI-Umschalter aus `results[].views`).
- Tutor `comet_motion`: Bewegung je Aufnahme > max(1,5 px; 0,6 FWHM). Tests: `test_comet.py` (Demo `demo.generate_comet`,
  Horizons-Attrappe, `ASTROFLOW_HORIZONS=0` in `conftest.py`).

## Heute Nacht – Empfehlungen (`modules/stacking/tonight.py`, `deepsky.py`; seit 0.20.0)
- **Katalog** (`deepsky.py`): `MESSIER` (110, M24/M40/M73 als Nicht-Ziele markiert) und `CALDWELL` (109, C50/C61 stecken in C49/C60) fest eingebaut mit
  Helligkeit und Größe; ASTAPs `deep_sky.csv` hat **keine Helligkeit** → Koordinaten von dort nur zur Genauigkeit, weitere ASTAP-Objekte
  (`astap_extras`) nur wenn benannt, Galaxie ≥ 8′ oder NGC/IC-Haufen 10–45′ (`mag=None`, Bewertung ×0,92). `ASTROFLOW_DEEPSKY=0` schaltet die
  Datei ab (Tests), `deepsky.reset()` leert die Zwischenspeicher. Filter `usable`: Helligkeitsgrenze je Gerät (`MAG_LIMITS`), Größe 3–90 % der kurzen
  Bildfeldseite, mind. 2′. Eigene Objekte: `keys_for(name)` (Schlüssel M/NGC/IC/C + Namen + ASTAP-Zweitnamen) und Position (Radius = Katalogausdehnung des
  eigenen Objekts, damit M32/M110 bei M31 entfallen). `objects.extended_catalog` speichert nun die Zweitnamen in `aliases`.
- **Rechnung** (`plan`): Höhen analytisch aus Ortssternzeit (numpy, kein AltAz je Objekt) → ~140–250 Kandidaten in ~1 s, Ergebnis 10 min gecacht (Schlüssel
  enthält Projekte, Gerät, `hide`, `collection`). Nutzbare Stunden `eff_hours` = dunkel × Höhe ≥ 30° × Mondgewicht (Phase × Abstand, `w_moon`) × Zenit
  (> 80° bei `alt_az`: 0,4) × Wolken (`clear = 1 − cloud/80`). `_score` ≈ 0–6 gemeinsam für „new“ und „more“; `_select` greedy, max. 2 je Objektart, beide
  Kategorien vertreten. Saison: Nächte heute + 0…196 Tage (14-Tage-Schritte, stündlich) → `_season` (`last`/`soon`).
- **Mehr Zeit** `_gain`: Rauschen × √(t/(t+Zusatz)), Zusatz = nutzbare Stunden (auf 0,5 h, max. 8); unter 10 % Gewinn entfällt das Ziel.
- **Nachtplan** `_night_plan`: nutzbare Nacht in 1–4 Abschnitte (≈ 3 h), Zuordnung der Ziele per Permutation (Summe der gewichteten Sichtbarkeit); Blöcke < 40 min entfallen.
- **API** `GET /api/stacking/tonight?hide=M35,12`: ausgeblendete Kennungen (localStorage `af.tonight.hidden`). Optionaler Dienst `collection_progress` (Sage →
  `{"messier": [Nummern]}`, 2 min gecacht): „Wäre dein n. Messier-Objekt“, bereits entdeckte Messier-Nummern entfallen. Tests: `tests/test_tonight.py`.

## Messier-/Caldwell-Daten (OpenNGC + Wikipedia)
- `deepsky.py` lädt Position, Helligkeit und Größe von Messier (110) und Caldwell (109) aus `stacking/katalogdaten/messier_caldwell.csv`.
  Die Datei ist aus **OpenNGC** (Position, Kennungen, Galaxiengrößen) und den englischen **Wikipedia**-Listen (Helligkeit, sichtbare Größe; Werte
  dort aus SEDS) zusammengestellt, beide CC-BY-SA 4.0 – Quellen, Revisionen und Lizenz in Kopfzeile, `README.md`, `LICENSE-OpenNGC`, `NOTICE`.
  Die CC-BY-SA-Datei bleibt getrennt vom MIT-Code; deutsche Namen, Objektart-Kürzel und die Caldwell-Zuordnung (Nummer → NGC/IC) sind eigene
  Tabellen in `deepsky.py` (`MESSIER`, `CALDWELL`).
- Neu erzeugen: `python tools/openngc_extract.py <ordner> [--alt <alte deepsky.py>]` (nur beim Entwickeln, kein Netz zur Laufzeit; Ordner mit
  `NGC.csv`, `addendum.csv`, `Messier_object.wiki`, `Caldwell_catalogue.wiki`, Bezug im Skriptkopf). Das Skript prüft beide Quellen gegeneinander
  und gibt Abweichungen aus (Helligkeit > 0,7 mag, Größe > 40 %, Position > 3′). Vorrangregeln im Skriptkopf; Spalten `mag_src`/`size_src`.
- Warum nicht OpenNGC allein: `V-Mag` ist bei Galaxien teils unbrauchbar (NGC 253: V 11,1 bei B 7,9), `MajAx` ist bei Haufen ein Kernmaß (M7 22′
  statt ~80′), bei Planetarischen Nebeln nur die helle Kernfigur (M57 1,27′ statt ~3,8′) – M57/M76 würden sonst am `MIN_SIZE`-Filter scheitern.
- Von Hand (unverifiziert, `Target.fallback`): sichtbare Größe von 13 Caldwell-Haufen (C10, C75, C78, C79, C82, C85, C86, C88, C91, C97, C102, C107)
  und C99 (Kohlensack). M102 = NGC 5866 (OpenNGC: Dublette von M101). C50/C61 stehen in der Datei, sind aber keine eigenen Ziele (stecken in C49/C60).
  Emissionsnebel (Art `e`) bekommen bewusst keine Helligkeit (integrale Helligkeit sagt über die Sichtbarkeit nichts aus).
