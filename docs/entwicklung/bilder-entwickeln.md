# Modul Bilder – Entwickeln, Tutor, KI-Prüfer, Ausgabe (Schritte 7–10)

> Ausgelagert aus `AGENTS.md` am 09.11.2026, Text unverändert übernommen. Regeln, die beim Ändern von Code gelten,
> stehen zusätzlich in Kurzform in `AGENTS.md` → „Wenn du … änderst“. Neue Befunde hier ergänzen, nicht in `AGENTS.md`.

Zwei Engines (`Pipeline.stack()` / `Pipeline.develop()`): siehe Kopf von `bilder-stacken.md`.
Schritte 11–13 (Speicher, volles Laufwerk, Aufräumen): `daten-server.md`.

7. **Develop Engine** (`develop.py`): `measure()` (Objekt-SNR, Signalfläche, Dynamik auf gebinnter Vorschau) → `adapt()` passt
   Profilwerte an (Entrauschen, `linear_denoise` = (10/SNR)^1,5, Sättigung, asinh, keine Entfaltung bei wenig Signal) → bis zu 3 Renderings
   in voller Auflösung mit `assess()` (Hintergrund, Farbstich, Farbrauschen [Bandpass 2–12 px], Restgradient, Clipping, Sättigung, Signal)
   und `corrections()`; Restgradient wird nur gemeldet (höherer Polynomgrad trägt Nebel ab). Vom Nutzer gesetzte `params` sind tabu
   (`fixed`). Ergebnis in `stats.quality` (Score, Probleme, `hints`) und `stats.qc` (Verlauf). Schwellen kalibriert an NGC 1579, M3, M74,
   IC 405, M57 (gute Bilder: Farbrauschen 0,0035–0,006; fleckig ab ~0,009).
   **Ausgebrannter Kern** (`burnt_core`, Code `burnt_core`): größte Fläche L > 0,95 ≥ 1e-4 des Bildes und ≥ 4× typischer Sternkern
   (NGC 4490 114 px vs. ~20, M31/M3 ~390; helle Sterne M45/IC 405 bleiben darunter). Ursache ist der Weißpunkt der Kurve
   (Perzentil 99,9 → alles darüber hart weiß; mehr asinh hilft dagegen nicht) → Korrektur hebt `white_pct` stufenweise
   (`WHITE_STEPS` 99,9/99,98/99,995) und asinh ×1,5. Objekttyp: Ordner-/OBJECT-Namen, die der kleine Katalog nicht kennt, bekommen
   ihren Typ aus ASTAPs `deep_sky.csv` (`objects.resolve_extended`; NGC 4490 lief vorher mit Profil „generic“, asinh 30 statt 120);
   ältere Projekte ohne Typ werden beim Entwickeln nachgetragen. Seit 14.11.2026 über `objects.kind_of`: kleiner Katalog → alle
   Messier/Caldwell (`deepsky.lookup`) → ASTAP. ASTAP führt viele Messier-Objekte ohne Art (M77, M32, M85, M110, M56, M14, M103 …) –
   M77 (Vespera) lief so mit „generic“. Projekte mit Typ fehlend oder „generic“ bekommen beim Entwickeln den richtigen Typ.
   **Kompakte helle Objekte** (13.11.2026, M1 Vespera 51 min: Kern weiße Scheibe, `burnt_core` blieb drei QC-Runden stehen):
   Bei Nebeln mit Sternentrennung streckt `stars.Stretch` (GHS) mit eigenem Weißpunkt – `white_pct`/`asinh` aus `adapt`/`corrections`
   wirken dort **nicht** auf den Nebel. GHS legte den Kern auf 0,7–0,84, `local_contrast` und `grade` multiplizierten ihn dann ins
   Weiß (40 % ≥ 0,97, einzelne Kanäle bei 255). Abhilfe: (1) `processing.soft_gain` – Aufhellungen in `local_contrast`/`grade`
   laufen weich gegen den Spielraum des hellsten Kanals aus (allein schon: M57 Ring ≥ 0,97 11 % → 0 %); (2) Messwert
   `object_burn` in `assess` (`QC_VERSION` 4): Anteil des hellen Objekts ≥ 0,90 ohne Sternkerne (Schwelle 0,90, weil `soft_gain`
   flache Kerne knapp unter Weiß hält); über 0,03 → Problem `object_burn`; (3) Korrektur: mit Sternentrennung Lichterschutz `hdr`
   (`HDR_STEPS` 1,5/3), ohne zuerst wie bisher `WHITE_STEPS`, danach `hdr`. `processing.compress_highlights` läuft **nach**
   `grade` (direkt nach dem Stretch zogen Kontrast/Endbearbeitung den Kern wieder hoch): geglättete Helligkeit über 0,5 bekommt
   eine Schulter, Feinstruktur wird additiv übernommen, im gedämpften Bereich etwas Farbe zurück. Vorher/Nachher: M1 Kern-Median
   0,95 → 0,69, nichts ≥ 0,9, Filamente sichtbar; M16/IC 443 unverändert (mittl. Abweichung < 0,0003). Der M1-Kern ist auch linear
   fast neutral (R:G:B ≈ 1,06 : 1 : 0,90) – cremeweiß ist dort richtig. Bekannt: sehr helle Einzelsterne (Polaris, γ Cas)
   melden `object_burn` wie schon `burnt_core`; `hdr` ändert an ihnen kaum etwas.
   **Bearbeitung** (`processing.py`, Reihenfolge in `process()`): Sättigungsmaske → Farbkanäle auf Grün ausrichten (`align_channels`,
   atmosphärische Dispersion bei Alt-Az-Geräten) → Polynom-Gradient → `remove_edge_glow` (steiles Randglühen, nur in 15-%-Randbändern,
   plus 2D-Eckenkarte) → Polynom Grad 2 → `trim_bad_edges` (max. 5 % je Seite) → Farbkalibrierung über Sterne → gesättigte Kerne neutral →
   Entfaltung, `denoise_linear` (vor dem Stretch, holt schwaches Signal), asinh/MTF-Stretch, Wavelet-Entrauschen, Sternreduktion, Kontrast, Sättigung → `tame_star_color` (Farbsäume in Halos heller
   Sterne; Nebelkerne über Formfilter geschützt) → `neutral_display_background`. `want_before=True` liefert `stats["before"]`
   (gleicher Ausschnitt, nur linear gestreckt) → `<base>_before.jpg` für den Vorher-Nachher-Vergleich. Parameter aus Profil je Objekttyp (`objects.PROFILES`).
   Achtung Reihenfolge: Randglühen **vor** dem ersten Polynom zu korrigieren wurde getestet und war deutlich schlechter.
   Polynom-Fit ohne die äußeren 12 % (`EDGE_EXCLUDE`) – sonst schwingt es beim Stellina-Randglühen über → dunkle Bänder im Bild.
   **Stellina-Vergleich (NGC 6888, Stellina-Fertigbild vom Nutzer):** Unser linearer Stack ist gleich gut (FWHM 5,5 vs. 5,45 px). Der Unterschied
   lag in der Entwicklung: (1) Hintergrund 0,12 statt ~0,03 → grau, Sterne wirkten zahlreicher/größer; (2) der alte **farberhaltende**
   Stretch ließ helle Sterne als große gesättigte rote/blaue Scheiben mit Ringen erscheinen. Jetzt Standard: Kurve **je Kanal**
   (`_stretch_channels`, Sternkerne → Weiß; alt per Parameter `color_preserve`), Profil-`target_bg` 0,06–0,08 (= Helligkeit schwachen Signals),
   danach `darken_background` auf `bg_dark` = 0,03 (Schwarzpunkt anheben; Nutzer wünscht dunklen Himmel – natürlicher, Artefakte
   weniger sichtbar). Ein niedrigeres `target_bg` statt Schwarzpunkt machte Nebel und Sterne mit dunkel – getestet, schlechter. QC-`BG_RANGE` 0,02–0,10.
   **Sternentrennung + GHS** (`stars.py`, Profil `star_separation` auto|starnet|intern|off, `star_brightness`): linear trennen
   (StarNet2-CLI, falls gefunden: `ASTROFLOW_STARNET`/PATH/`%LOCALAPPDATA%\starnet`; sonst eigene Trennung: Top-Hat runder kompakter
   Quellen, Füllen per Öffnung + Versatzausgleich, nie heller als das Original, sternlos + Sterne = Original). Sternlos: `Stretch` =
   automatische GHS-Folge (3 Durchgänge, SP am Himmelshintergrund, D ≤ 25, Rest per MTF; Weißpunkt vom Bild **mit** Sternen).
   Sterne: `channel_curve(img) − channel_curve(sternlos)` (knackig, Kerne weiß), sättigen, × `star_brightness`, Screen-Mischung.
   **Heutiger Stand (Profile `objects.PROFILES`):** Nebel (emission/reflection/snr) `starnet` = nur echtes StarNet, ohne StarNet keine Trennung
   (`Pipeline.develop` setzt dann `off`); alle übrigen Profile (Galaxie, Haufen, Planetarisch, Komet, generic) `off`. Anlass (NGC 7000, 01.11.2026): mit
   StarNet + GraXpert deutlich besser (Nebel ohne Sterne gestreckt, Sterne kleiner), während die eigene Trennung (`intern`) matschiger und
   kontrastärmer wirkte und bei Sternhaufen den Kern ausbrannte (M3) – `intern`/`auto` nur je Objekt per Option `star_separation` zuschaltbar.
   Dazu **Endbearbeitung** `processing.grade` (Profil `grade` 1,0 / `vibrance` 0,5, vor dem Einsetzen der Sterne): Wavelet-Ebenen 4–32 px
   ×(1+0,35…0,6) nur im Objekt (Maske gegen 10-%-Perzentil der geglätteten Luminanz – Median/MAD versagte, wenn der Nebel das Bild füllt:
   0,4 % Maske) und ohne helle Sterne; Vibrance sättigt blasse Farben stärker. Galaxien/Haufen ohne beides. Ohne Trennung: `channel_curve`.
   `darken_background` mit weichem Fuß (hartes Abschneiden ließ dunkle Nebelpartien schwarz werden).
   **Fotometrische Farbkalibrierung** (`photometry.py`, `Pipeline._photometric`, Einstellung `gaia` / `ASTROFLOW_GAIA`): Master-Stack mit
   `platesolve.solve_image` lösen (WCS auf vollem Pixelraster, Cache `work/p<id>/wcs.json` je Referenzbild), Gaia-DR3-Farben BP−RP
   per TAP/ADQL, Reihenfolge GAVO-Spiegel (`gaia.dr3lite`, BP−RP aus den Mittelwerten, Timeout 20 s) → ARI Heidelberg (30 s) → ESA-Archiv → VizieR
   (gemessen 01.11.2026: 0,6 s / ~8 s / 131 s / nicht erreichbar), Cache `work/gaia/`.
   Zuordnung ≤ max(3 px, FWHM), Aperturphotometrie, robuste Gerade log(R/G), log(B/G) gegen BP−RP, Weiß = G2V (0,82).
   Ergebnis als `params.wb_gains` + `_pcc`; ohne Solver/Netz/Sterne → Sternmittel mit Grund in `stats.color_cal`.
   Stellina-Werte (5 Objekte): R ≈ 0,97, B ≈ 0,88, Streuung ~3 %. Tests offline (`tests/conftest.py` setzt ASTROFLOW_GAIA=0).
   **StarNet** optional: Einstellungen → „ZIP installieren“ (`stars.install_zip` → `data/tools/starnet`, Pfadprüfung, Selbsttest)
   oder Pfad (`settings.json` → `starnet.path`). Kein automatischer Download: Lizenz „all rights reserved“, Download-Links nur per JS.
   **GraXpert** (`graxpert.py`, seit 01.11.2026, Nutzerwunsch nach Astrobiscuit-Vergleich – dessen Workflow: APP → GraXpert → StarNet2 → Affinity):
   optionales externes Programm (GPL-3, Windows nur als MSI → Suche in `Program Files\GraXpert`, `%LOCALAPPDATA%\Programs\GraXpert`, PATH,
   `ASTROFLOW_GRAXPERT`; `=0` schaltet ab, setzt `tests/conftest.py`). `Pipeline.develop` ruft `graxpert.apply` **einmal** auf dem linearen Stack auf
   (vor Gaia-PCC, nicht je QC-Runde; Cache `work/p<id>/graxpert/`): KI-Hintergrund (`background-extraction`, Subtraction) und/oder KI-Entrauschen
   (`denoising`, Stärke Profilwert `ai_denoise`, Standard 0,5, planetary 0,3). Ungedeckte Ränder werden vorher mit Himmelswert gefüllt. Ergebnis als
   `params._ai` → `processing.process` überspringt dann Polynom/Randglühen bzw. `denoise_linear`; `stats.graxpert`. Modus global (Einstellungen →
   Bilder, `settings.json` → `graxpert.mode/gpu/path`) und je Objekt (Option `graxpert`). Fehler → eigene Verfahren. Test: `test_graxpert.py` (Attrappe).
   **M64 (01.11.2026, Nutzer: „um Welten besser“):** Restgradient 0,020 → 0,0017, Farbrauschen 0,0059 → 0,0037, Himmel 0,055 → 0,031,
   diagonale Schlieren (Walking Noise, vom Polynom nicht erfassbar) fast weg. Halo schwächer (Signal 0,015 → 0,007; KI-Entrauschen senkt σ →
   härterer Stretch), vom Nutzer aber als ausreichend erkennbar bewertet – so belassen.
   Schnelles Iterieren: `StackState.load(work/p<id>/stack).result()` → `processing.process(...)` (~10 s), kein Neu-Stacken nötig.
   **Schwaches Signal / M57-Vergleich mit Stellina (2026-10-30):** Lineare Stacks waren gleich gut, die Entwicklung schlechter.
   Ursache: `linear_denoise` senkt das gemessene σ → Schwarzpunkt der Kurve (Median − 2,8σ) rückt an den Himmel → viel härterer Stretch.
   Das macht schwache Galaxien/Nebel erst sichtbar (Kurve aus dem Bild *vor* dem Entrauschen getestet: M63/NGC 1579/M101 fast unsichtbar –
   verworfen), bläht bei kompakten hellen Objekten aber Sterne auf und brennt sie aus → Profilwert `linear_denoise_max` (planetary 0).
   Profil planetary: target_bg 0,10, denoise 0,25, white_pct 99,98 (Korn und schwache Sterne bleiben wie bei Stellina).
   Entfaltung erst ab Objekt-SNR `DECONV_MIN_SNR` 15, Schärfen ab SNR 10 anlaufend bis 25 (sonst dunkle Ringe um Sterne).
   Kern weiter ausgebrannt, obwohl `WHITE_STEPS` ausgereizt → `corrections` erhöht asinh weiter.
   Danach alle Profile: `denoise` 0,3 / `chroma_denoise` 1,5 (vorher 0,4–0,6 / 1,5–2,5) – an 10 Projekten gemessen: Objekthelligkeit
   gleich (Nutzer: „ohne dass sie dunkler werden“), 10–35 % mehr schwache Sterne, Sterngröße gleich. `linear_denoise_max` 0,5 für alle
   getestet und verworfen (M63/M101/NGC 1579 dunkler). `CORE_STAR_RATIO` 4 → 5: AE Aurigae in IC 405 (4,4) galt als ausgebrannter Kern.
   **Stack-Schärfe nachgemessen (01.11.2026):** kein Stack-Problem. Gleiches Verfahren wie bei Frames (2×2 gebinnt, ×2): Stack schärfer als
   der Median-Frame (NGC 1579 6,8 gegen 8,2; M101 5,9 gegen 10,1), über das Feld gleichmäßig (Mitte = Rand), Exzentrizität = die der Frames
   (Sternform kommt aus den Rohbildern, nicht aus der Registrierung). Frame- und Stack-FWHM (`stats.fwhm_px`, ungebinnt) nie direkt vergleichen.
   M101 Versuch mit fester FWHM-Grenze (≤ k × 10-%-Quantil): k 1,5 → 232 statt 504 Frames, FWHM −6 %, Rauschen +28 %; k 1,25 → −8 % / +54 %
   → Auswahl bleibt relativ (Gewichtung reicht; wer Schärfe will: Option „Nur die besten X %“).
   **Farbverlauf im Himmel** (seit 01.11.2026, NGC 891 bekam trotz rötlichem rechtem Rand 10/10 – `cast` misst nur den
   globalen Median): `develop.color_gradient` = Ebene durch R−G/B−G der Himmelskacheln (8×8, getrimmtes Mittel), Stärke =
   Änderung über die Bildbreite, dazu Seite/Farbton. Gemessen an allen Ergebnissen: sauber ≤ 0,010, NGC 891 0,019, NGC 7318 0,022,
   M85 0,018; in der Milchstraße echter Hα-Nebel bis 0,030 → `COLOR_GRAD_MAX` 0,013 bzw. `_NEBULA` 0,035, Auswahl über
   `tutor.neutral_sky` (|b| ≥ 15° bzw. Galaxie/Kugelhaufen ab 8°). Korrektur `processing.remove_color_gradient` (Polynom über
   Kanal − Luminanz, Helligkeit bleibt) per Parameter `color_gradient` = Grad; QC setzt erst 2, dann 3 (Ebene überkorrigierte NGC 891).
   `assess` liefert `v` = `QC_VERSION` – **bei Änderungen an `assess` erhöhen**, der Tutor prüft ältere Ergebnisse dann am gespeicherten PNG nach.
   **Ohne GraXpert/GPU (seit 10.11.2026, Nutzerwunsch „so gut wie die Smart-Teleskope selbst, ohne GraXpert“; Grundlage: private
   Profi-Workflow-Recherche, Messung an 11 Fällen gegen Stellina-Nativbilder derselben Nacht):**
   - Hintergrund: Standard bleibt die Polynomkette (`background` „poly“). **Option** `processing.sky_background` (`background` „sky“,
     je Objekt): Ebenheit des Himmels (Kachel-Mediane ohne Objekt, p95−p5 in σ) gemessen an 11 Fällen – Galaxien/Haufen mit Polynom
     ebener (M3 0,5 gegen 1,1, M101 0,5 gegen 1,0), Nebel teils besser (M17 3,0 → 2,2, M45 2,6 → 1,6), NGC 6888 schlechter (violette
     Randbänder). Das Verfahren: DBE-artige Stützpunkte –
     Kachel-Mediane ohne Sterne, unruhige Kacheln raus, Auswahl **asymmetrisch** (Kacheln über dem Modell = Nebel/Staub/Galaxie fallen
     heraus), Polynomgrad 1→3 nur bei deutlich besserem Fit (strenges BIC), dazu ein Term (1 − Abdeckung): Bereiche, die nur ein Teil der
     Aufnahmen abdeckt, haben einen anderen Himmelswert – die „Rahmen“-Stufe bei Stellina; Randbänder (15 %) mit symmetrischer Auswahl
     (dort ist ein Anstieg Randglühen). Danach `remove_edge_glow` und ein zweiter Durchgang Grad 2. Grenze: gerade
     Stufen mitten im Bild von Nächten mit anderem Ausschnitt (NGC 7023 links) bleiben – das wäre ein Stacking-Thema (Himmel je Nacht angleichen).
   - **Reihenfolge (Nutzerentscheidung 10.11.2026): GraXpert bevorzugt, CPU-Weg als Rückfall.** Ist GraXpert installiert und läuft
     (auch ohne GPU), bleibt es bei Hintergrund + KI-Entrauschen wie bisher; `denoise_multiscale` greift nur, wenn kein KI-Entrauschen
     lief (nicht installiert, abgeschaltet, Fehler, Modus nur „bge“). **Ohne Grafikkarte** (Einstellung `graxpert.gpu` aus oder GraXpert
     meldet nur `CPUExecutionProvider` in „Used inference providers“, `graxpert.uses_gpu`) läuft im Modus „all“ nur der KI-Hintergrund
     (~4 s), entrauscht wird auf der CPU (`stats.graxpert.denoise_skipped`): GraXpert-Entrauschen dauerte auf der CPU 306 s statt 21 s
     (M66, 6 MP) – Nutzerentscheidung „zu lang“. `settings.json` → `graxpert.cpu_denoise: true` erzwingt es trotzdem.
   - Entrauschen `processing.denoise_multiscale` (statt `denoise_linear`, Stärke `cpu_denoise`, Standard = `ai_denoise` bzw. 0,5):
     à-trous 5 Ebenen auf L, Farbe getrennt bis 32 px mit höheren Schwellen; Rauschen je Ebene am echten Himmel gemessen
     (`dark_sky_mask`), örtlich nur über die Abdeckung skaliert (eine Anhebung mit der Helligkeit machte Galaxienarme weich – M66);
     Garrote-Schwelle + Mehrskalen-Träger um *positive* Koeffizienten (ohne Träger gewannen Sterne ~7 % Fluss; Träger auch um negative
     ließ kalte Pixel als dunkle Pünktchen stehen). Farbe bis 64 px mit hohen Schwellen in den groben Ebenen gegen großflächigen
     „RGB-Brei“; Objekt- und Sternfarbe schützt eine Luminanzmaske. ~6 s für 6 MP.
   - QC: „Schwarzwerte abgeschnitten“ bei sonst passendem Himmel verbreitert den weichen Fuß (`bg_toe`, `darken_background(toe=)`)
     statt `bg_dark` anzuheben – auf dem CPU-Weg hob die Kontrolle den Himmel bei Galaxien sonst auf 0,06 (grau, Farbrauschen sichtbar).
   - Tutor: `exposure_recommendation` (SNR wächst mit √t → Zielzeit t·(30/SNR)², halbe Stunden, max. 12 h) als Befund `exposure_more`
     (Stufe warn, steht oben) und als `recommendation` im Bericht, sobald deutlich mehr Belichtung nötig ist oder eine Galaxie
     Bildqualität (`grade.py`) unter 6 hat. Anlass M66: „Bearbeitung 10,0“ neben „Bildqualität 4,5“, Belichtungshinweis zugeklappt.
   - „Himmel“ überall = dunkelste Teile statt dunkle Bildhälfte (`dark_sky_mask`): `neutralize_background`/`neutral_display_background`
     10 %, `assess` 15 % (`QC_VERSION` 3; Kachelverlauf und Farbverlauf weiter über die dunkle Hälfte, sie brauchen Himmel in vielen Kacheln).
   - GraXpert: Glättung je Profil (`bge_smoothing`, reflection 1,0, sonst 0,1 – 0,1 trug bei NGC 7023 die Hälfte des Staubs ab), ab 0,5
     danach `remove_edge_glow`; Cache-Schlüssel nur bei abweichender Glättung neu. Profil reflection: `target_bg` 0,10, `bg_dark` 0,045,
     `grade_large` 1,0 (zusätzlicher Lokalkontrast 64/128 px in `grade`, Profi-Äquivalent LHE ~150 px).
   - Staubfarbe (`continuum_color`, Profil reflection 1,0, seit 13.11.2026): Glättung 1,0 lässt bei Stellina rotes Sensor-/IR-Glühen
     stehen (NGC 7023: Anteil 1,0 − 0,1 hat R:G:B ≈ 1,8 : 0,3 : 0,15, am stärksten an den Rändern) – nach Sättigung/Vibrance ein fleckiger
     roter Schleier, die QC sah nichts (Himmel = dunkelste 15 %, dort neutral). `processing.continuum_color` begrenzt nach der
     Hintergrund-Neutralisierung die sternfreie, geglättete Farbe (σ 8 px) auf Kontinuumsfarben: R·B ≤ G² bei gleichem R/B, R und B
     ≤ 1,8 × G; nur R/B werden gesenkt. Brauner Staub und blauer Nebel bleiben. Nicht bei Emissionsnebeln (Hα/OIII ist echtes Magenta).
     Randbeschnitt: ab Glättung 0,5 räumt `remove_edge_glow` die Ränder, deshalb greift `trim_bad_edges` meist nicht mehr (kein `edge_trim`).
   - Ausgabe: `_linear.fits` = unbearbeiteter Stack (bis 0.55 stand dort das GraXpert-Ergebnis), mit GraXpert zusätzlich `_linear_graxpert.fits`
     (`files.fits_graxpert`, Export-Menü).
   - Messwerkzeug: Skripte im privaten Recherche-Ordner bzw. PR-Beschreibung (Kennzahlen am fertigen Bild, Himmel = dunkelste 12 %).
8. **Bild-Tutor** (`tutor.py`, seit 01.11.2026, Nutzerwunsch „Tutor für Anfänger, Hinweise passend zum Instrument, Mond/Wetter der Nacht“):
   `build_report` (API `GET /results/{id}/tutor`, Cache in `results.stats.tutor` mit `TUTOR_VERSION`) = Gerät (`detect_device`:
   INSTRUME/TELESCOP, Rückfall Bildformat; `DEVICES` mit vorsichtigen Tipps – nur Gesichertes; Stellina hat eine Tauheizung →
   kein Tau-Befund aus Wetterdaten, Hinweis des Nutzers) + `night_conditions` (astropy offline, IERS-Download aus; ≤ 48 Stützzeiten
   je Nacht: Sonne/Mond/Objekthöhe, Mondphase aus Elongation; Frame-Verlauf aus `frames.metrics`: Sternzahl-Einbrüche, `bg`/`fwhm`-Trend
   letztes/erstes Fünftel, Exzentrizität > 0,8) + optional `fetch_weather` (Open-Meteo-Archiv/ERA5, Ort auf 0,1° gerundet, Cache
   `work/tutor/`) → `findings` (code, level bad|warn|info|ok, Erklärung, Tipps). Standort: `FrameInfo.site` (SITELAT/LONG, OBSGEO; Stellina
   schreibt keinen) oder `settings.json` → `tutor.lat/lon`. NGC 891 (24./25.06.2022, Mitteleuropa): Sonne nur −10…−14°, Himmel ×3,2 heller →
   Dämmerung als Ursache des Rotstichs. KI auf Knopfdruck (`POST …/tutor/ki`, `ask_llm` über `reviewer.chat`, eigener Anbieter/Modell
   `tutor.llm_provider/llm_model`, URL/Schlüssel vom KI-Prüfer), Ergebnis in `results.stats.tutor_ki`. Einstellung `tutor.mode`
   einsteiger|experte (UI: Befunde offen bzw. zugeklappt). Test: `test_tutor.py`.
9. **KI-Prüfer** (`reviewer.py`, optional, Standard aus): Vision-LLM (LM Studio/OpenRouter) bewertet Vorschau, darf nur Whitelist-Parameter in festen Grenzen ändern.
   Läuft nach der messbaren Kontrolle (`first=` = bereits gerendertes Bild). Denkmodelle (Bonsai, Qwen3) brauchen ~2 500 Tokens vor der
   Antwort → `max_tokens` 8000, Timeout 300 s; `<think>`-Blöcke und `reasoning_content` werden behandelt. Lokal z. B. LM Studio mit einem Vision-Denkmodell (~1 min/Runde).
10. **Ausgabe**: TIFF16/PNG/JPG/Thumb + linearer FITS in `output/<Objekt>/`, Zeile in `results` (Versionen). Bild wird beim Speichern vertikal gespiegelt (Norden oben).
