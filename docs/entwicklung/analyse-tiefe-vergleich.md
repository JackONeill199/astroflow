# Analyse – Tiefe und Vergleich („Was dein Bild zeigt“)

> Ausgelagert aus `AGENTS.md` am 09.11.2026, Text unverändert übernommen. Regeln, die beim Ändern von Code gelten,
> stehen zusätzlich in Kurzform in `AGENTS.md` → „Wenn du … änderst“. Neue Befunde hier ergänzen, nicht in `AGENTS.md`.

## Was dein Bild zeigt – Tiefe und Vergleich (`depth.py`, `compare.py`, `insights.py`; seit 0.25.0, Wünsche 12/13)
- **Ablauf:** `insights.Insights` = eigener Thread mit Warteschlange (`Module.start`), Zustand im Speicher, Ergebnis in
  `results.stats.depth` bzw. `.compare` (je mit `v` = `DEPTH_VERSION`/`COMPARE_VERSION`; Fehler als `{v:0, ok:false, error}`).
  `Engine._develop_job` ruft nach jeder Entwicklung `after_develop(rid)` (nur mit `wcs.json`, Gaia an, Einstellung `auto_depth`).
  Die Projektansicht (`_slim`) liefert beide **nicht** mit (wird alle 2,5 s gepollt; Tiefe hat bis 1 500 Punkte) – eigene Endpunkte
  `GET|POST /results/{id}/depth|compare`, Dateien `/results/{id}/compare/(aktuell|referenz).jpg|k<n>.png` (streng geprüft),
  Einstellungen `GET|POST /insights` (`data/settings.json` → `insights`: `auto_depth`, `simbad`, `survey`, `small_bodies`; Gaia = `storage.gaia`).
- **Geometrie:** `services.view_geometry` (aus `project_solution` herausgelöst) = WCS des linearen Stacks der Version + Lage des
  JPG-Ausschnitts (Korrelation), `to_view` rechnet FITS-Pixel → JPG-Pixel (Zeile 0 oben). Schlüssel in `wcs.json` muss zur Stackgröße passen.
- **Tiefe:** `photometry.gaia_field` (GAVO `gaia.dr3lite` → ARI → ESA, bis G 20,7, TOP 40 000, Cache `work/gaia/gaia_feld2_*` mit
  Parallaxenfehler/Eigenbewegung seit 0.41.0; ältere `gaia_feld_*` gelten für die Tiefe weiter, Spalten dann NaN, `need_pm=True` fragt neu) →
  erzwungene Aperturfotometrie an allen Katalogpositionen (Apertur 0,8 FWHM, Ring-Median). **Rauschen an leeren Himmelsstellen
  messen**: Stack-Pixel sind korreliert, Pixel-σ×√N war bei M101 2,9× zu klein. Erfasst = SNR ≥ 5, Schwerpunkt ≤ 0,6 FWHM,
  kein hellerer Nachbar und gemessene Helligkeit ±0,75 mag zur Katalog-G (sonst zählten HII-Knoten in M101 als G-20-Sterne).
  m5 = Nullpunkt − 2,5 log(5 σ_Apertur). Parallaxe mit Nullpunkt −0,017 mas, Parallaxe/Fehler ≥ 5. Galaxien: SIMBAD-TAP (Harvard-Spiegel,
  CDS ist aus manchen Netzen nicht erreichbar) mit Rotverschiebung und G/R/V-Helligkeit, nur Galaxientypen (keine Haufen/Gruppen),
  sichtbar = SNR ≥ 5 + Schwerpunkt + kein Vordergrundstern + Katalog-Helligkeit ≤ m5+1; `_mark_members` nimmt Einträge im
  Hauptobjekt (gleiche z, innerhalb der Katalogausdehnung) heraus. Lichtlaufzeit/mitbewegte Entfernung: astropy `Planck18`.
  Gemessen (Stellina): M101 692 Frames m5 17,7, fernstes z 0,54 (5,5 Mrd. Jahre); M81 m5 17,4, Quasar z 5,8 im Feld, aber zu schwach.
- **Vergleich:** Referenz = eigenes Ergebnis desselben Felds (`references`, ≤ 0,4°, gelöst; `pick_reference` bevorzugt anderes
  Objekt/Gerät, dann andere Nächte, dann deutlich weniger Aufnahmen) oder DSS2 rot (STScI `dss_search`, ≤ 60′, sonst CDS hips2fits;
  astropy liest die DSS-Plattenlösung). `reproject` über grobes Koordinatengitter + `map_coordinates`. Hintergrund ab, schärferes Bild
  falten, `_flux_relation` (eigen: Faktor; DSS: Parabel in log-log, **Fotoplatte ist nicht linear**), Differenz glätten, Kachel-MAD → K = 6σ.
  `_candidates` verwirft Rand, Einzelpixel, Form (Achsen > 3:1, Größe außerhalb 0,5–2,5 FWHM), gesättigte/helle Reste, Helligkeits-
  abweichung < 0,5 mag, und „neu/verschwunden“ nur, wenn das andere Bild die Quelle sicher zeigen müsste (DSS: 30σ erwartet –
  Galaxien und rote Riesen sind im Rotfilter heller, M3 gab sonst 25 Fehlalarme). Gegen DSS keine Helligkeitsänderungen.
  Katalog: SIMBAD-Kegel 8″ je Stelle (Veränderlichen-Typen), JPL SB-Ident **geozentrisch** (`mpc-code=500`) je Nacht (≤ 2), Kometen
  und Asteroiden getrennt. **Seit 0.43.2:** Kometen mit zweitem Durchgang (genau, ~4 s), Asteroiden **nur erster Durchgang**
  (Zweikörper-Näherung, ~20 s; Einträge `genau: false`) – der zweite brauchte am 07.11.2026 für ein 1°-Feld 127 s und scheiterte am
  Zeitlimit immer. Cache-Schlüssel `sb3|…`. Zeitüberschreitung/Verbindungsfehler → `SmallBodyError`, Marke `work/vergleich/jpl_fehler.json`,
  `JPL_FAIL_PAUSE` 30 min keine neue Abfrage (dienstweit); Meldungen über `compare.jpl_message` („sehr langsam“ / „nicht erreichbar“).
  Ausgabe: `work/p<id>/vergleich/r<rid>/` Blinkbilder (gleiche Streckung, Raster des Stacks) + Ausschnitte; Caches `work/vergleich/`.
- **Hotpixel-Reste** (seit 0.38.2, `stackfix.spots`): Ältere Stacks ohne `hotfix` enthalten Hotpixel-Paare (ein Kanal, R/G). Im
  Kanalmittel und nach dem Falten der schärferen Aufnahme wurden sie sternförmig und passierten Form-/Einzelpixelprüfung (Arbeitsinstanz:
  3 von 5 Meldungen, Punkte mit 33–52σ im R-Kanal). Vergleich und Tiefe rufen daher vor dem Kanalmittel `stackfix.spots` auf beiden
  Bildern auf: Gruppen wie beim Entwickeln reparieren (`repair` mit vorhandenem `find`-Ergebnis), dazu alle Einzelpunkte gleicher Art
  (`find(group=False)`, gleiche Schwelle `K`) als Stellen. Kandidaten ≤ max(3 px, FWHM) an einer Stelle → `verworfen.hotpixel`
  (Referenzstellen per WCS umgerechnet), Ergebnis `hotpixel {aktuell, referenz}`; Tiefe: Sterne/Galaxien ≤ max(2, 0,8 FWHM) an einer
  Stelle gelten nicht als erfasst/sichtbar. Schwelle nicht senken: ab 5σ fanden sich 1 400–9 000 Rauschspitzen je Stellina-Stack.
  `COMPARE_VERSION` 2; `DEPTH_VERSION` bleibt 1 (keine der 18 sichtbaren Galaxien der Arbeitsinstanz lag auf einem Punkt).
- **Grenzgröße / Erreichbarkeit** (seit 0.43.1, `compare._limits`, Nutzerbefund: ~40-s-Bilder meldeten „Stern verschwunden“): Ursache
  an echten Daten M104 (4 × 10 s gegen DSS): `_flux_relation` hatte nur wenige ungesättigte Sterne (Gerade statt Parabel, Platte nicht
  linear) → k ≈ 0,22 statt ~0,01, also erwarteter Fluss ~20× zu hoch; `expect(fb) > vanish·σ` hielt deshalb nicht. Jetzt je Bild
  m5 = 5σ-Grenze **in der Vergleichsapertur** (r = FWHM, nach Weichzeichnen/Skalieren – dieselben Flüsse wie die Kandidaten) und eine
  Helligkeitsskala: `_calibrate` an Gaia-Positionen (`photometry.gaia_field` mit denselben Parametern wie die Tiefe → gemeinsamer Cache;
  SNR > 10, frei, ungesättigt, ≥ `MIN_CAL` 6; eigene Stacks Nullpunkt, Platte Gerade/Parabel in log Fluss, monoton). Rückfälle:
  `stats.depth.grenze` (m5 → Nullpunkt + Aperturrauschen → `lim50`) → `_device_m5` (andere Bilder derselben `INSTRUME`, m5 auf 1 h
  über 1,25·log10 t, ≥ 2 Werte) → Platte `DSS_M5` 20,5. Referenz-Nullpunkt wirkt auf fb/(k·area) (k-unabhängig; `area` = Pixelfläche Referenz/aktuell,
  weil `reproject` Pixelwerte abtastet und den Fluss nicht erhält – Review PR #72). „verschwunden“ nur, wenn
  Helligkeit in der Referenz (bei Gaia-Stern an der Stelle das schwächere von beiden, solange < 2,5 mag Unterschied) ≤ m5(aktuell) −
  `REACH_MARGIN` 1,0 (≈ 12,6σ erwartet; Durchmusterung 1,5 wegen Rotfilter), „neu“ spiegelbildlich gegen m5(Referenz); sonst
  `verworfen.unter_grenze`. Ohne Aussage: eigene Referenz wie bisher über `expect`, Durchmusterung nie „verschwunden“. Ergebnis
  `grenze {aktuell, referenz: {m5, quelle, belichtung_s, aufnahmen, sterne}, abstand}`, Kandidat `grenze`. M104 danach: m5 13,0 (10 Sterne)
  gegen 19,0 → 0 statt 25 Meldungen; M33/M32/NGC 1579 (eigene Referenzen) unverändert. `COMPARE_VERSION` 3. Test
  `test_compare_short_stack_reachability` (kurzer gesättigter Stack, Flussbeziehung wie bei M104 verfälscht).
- **Helle Nachbarn / örtliche Grenze** (seit 0.43.4, Nutzerbefund NGC 2024: ζ Ori C „neu“, 19,8σ, 9,2 mag gegen DSS2 rot):
  Ursache an echten Daten: Alnitak ist auf der Platte ein gesättigtes Plateau (~20 000 bei Himmel ~7 400, Radius ~200 px auf dem
  Stack-Raster), ζ Ori C liegt 46 px daneben darin. `_background` (Kachel-Median 64 px) nahm das Plateau als Himmel → Referenz dort
  ≈ 0 (f_ref 22) → „neu“; die `sat`-Prüfung sah nach dem Hintergrundabzug nichts mehr, `reach` nutzte die globale m5 17,6.
  Jetzt: `_blind` auf den **Rohbildern** (vor dem Hintergrundabzug): Wert > Himmel + `BLIND_LEVEL` 0,7·(99,99-%-Wert − Himmel)
  (Plateau lag bei 0,86), um `BLIND_GROW`·Apertur vergrößert (`_grow`). Stelle im Blindbereich des Bilds **ohne** die Quelle
  („neu“ → Referenz, „verschwunden“ → aktuell, sonst beide) → `verworfen.heller_nachbar` (eine helle Nova darf im eigenen Bild
  gesättigt sein). Örtliche Grenze: `_local_factor` = robuste Streuung im Ring 3–8 FWHM gegen die des ganzen Bilds (`_pixel_noise`),
  m5 − 2,5·log10(fac) in `reach` (gesättigte Plateaus sind flach – die fängt nur `_blind`). Katalog: `reach` liefert als 4. Wert G
  eines Gaia-Sterns an der Stelle (≤ max(2, 0,8 FWHM)) mit G ≤ örtliche m5(Referenz) − Abstand (nur „neu“); `compute` verwirft ihn als
  `katalogstern`, außer SIMBAD (8″) nennt einen Veränderlichen (Mira …) – ohne SIMBAD vorsichtig verwerfen. Gegencheck an Kopien
  aller 73 gespeicherten Vergleiche (ohne Katalogabgleich): 69 Meldungen gleich, ASASSN-16og (NGC 1579, eigene Referenz) bleibt; weg sind
  ζ Ori C (NGC 2024, `heller_nachbar`) und je ein „neu“ in IC 410 (11,3 mag), M15 (10,9) und SH2-155 (15,4, LSPM-Eigenbewegungsstern –
  `katalogstern` verbirgt also auch Eigenbewegung gegen die alte Platte). `COMPARE_VERSION` 4. Tests `test_compare_bright_neighbor_halo_not_new` (Platte mit gesättigtem
  Plateau + Spikes, Nachbar darin; Nova im freien Feld bleibt), `test_compare_known_star_missing_in_reference` (Gaia-Stern fehlt auf
  der Platte; als Mira bleibt er „neu“).
- **UI** (`static/depth.js|css`, globale Funktionen, `stacking.js` prüft `typeof depthPanel`): `depthPanel`, `depthOverlay`
  (umhüllt `plateLabels`, Klasse `marks-on`), `depthTunnel` (three.js aus `/vendor/`, Richtung = Bildposition aufgeweitet, Abstand
  log10(Lj)), `comparePanel` + `compareViewer` (Blinken über `createViewer().setSplit`, Stelle anfahren mit `zoomTo`).
- Tests: `tests/test_depth_compare.py` (synthetisches Feld, Attrappen für Gaia/SIMBAD/DSS/JPL).
