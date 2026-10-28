# Wie Hobby-Astrofotografen bearbeiten – und was AstroFlow davon automatisieren kann

Stand: 2026-10-28. Grundlage: Siril-/PixInsight-Workflows aus Tutorials und Foren (AstroBin, pixls.us, Seestar-Blog,
Vaonis-Blog), Siril-Dokumentation (GHS, StarNet) und GraXpert-Repository – Quellen am Ende. Die Reihenfolge einzelner
Schritte ist in der Community nicht einheitlich; unten steht der verbreitete Konsens.

## 1. Der typische Ablauf nach dem Stacken

**Lineare Phase** (Daten noch nicht gestreckt – hier wirken die physikalisch sauberen Korrekturen):

| # | Schritt | Übliche Werkzeuge | Worum es geht |
|---|---|---|---|
| 1 | Zuschneiden | Siril, GraXpert | Stacking-Ränder (Feldrotation, Drift) weg, bevor sie Gradienten- und Farbmessung stören |
| 2 | Hintergrund/Gradient | **GraXpert** (KI), Siril „Background Extraction“, PixInsight DBE | Lichtverschmutzung, Mond, Vignettierung entfernen |
| 3 | Farbkalibrierung | Siril **SPCC** / PCC, PixInsight SPCC | Plate-Solving + Katalogfarben (Gaia) der Sterne → physikalisch richtiger Weißabgleich; danach Grünstich (SCNR) |
| 4 | Entfaltung | BlurXTerminator (nur PixInsight, kommerziell), GraXpert-Deconvolution, Siril | Sterne verkleinern, Details schärfen – nur linear sinnvoll |
| 5 | Rauschreduzierung (linear) | GraXpert Denoise (KI), NoiseXTerminator, Cosmic Clarity | vor dem Strecken, damit das Rauschen nicht mit hochgezogen wird |

**Nichtlineare Phase:**

| # | Schritt | Übliche Werkzeuge | Worum es geht |
|---|---|---|---|
| 6 | **Sterne abtrennen** | **StarNet++ 2** (in Siril integriert), StarXTerminator | Bild in „sternlos“ + „nur Sterne“ teilen – der wichtigste Hebel der letzten Jahre |
| 7 | Strecken (sternlos) | **GHS** (Generalized Hyperbolic Stretch) in Siril/PixInsight, mehrere kleine Durchgänge | Symmetriepunkt knapp über den Himmelshintergrund, Schatten-/Lichterschutz; Nebel kräftig strecken, ohne dass Sterne wachsen |
| 8 | Sternloses Bild bearbeiten | Kurven, lokaler Kontrast, Sättigung, Masken | Nebel/Galaxie gezielt herausarbeiten – Sterne stören nicht mehr |
| 9 | Sterne getrennt strecken | sanfter Stretch, ggf. verkleinern, Farbe leicht anheben | kleine, farbige, nicht ausgebrannte Sterne |
| 10 | Wieder zusammenführen | Siril „Star Recomposition“, PixelMath, Bildschirm-Mischung | Sterne über das bearbeitete Objekt legen; Sternhelligkeit einstellbar |
| 11 | Feinschliff | GIMP/Photoshop/Siril | Schwarzpunkt, Endsättigung, letzte Rauschreduzierung, 16-bit-TIFF + JPEG |

Wiederkehrende Ratschläge: „weniger ist mehr“ (Kontrast, Sättigung nicht übertreiben), mehrere kleine Streckungen statt
einer großen, Autostretch nicht als Endergebnis nehmen („sonst wachsen die Sterne“).

## 2. Abgleich mit AstroFlow (v0.4)

| Schritt | AstroFlow heute | Bewertung |
|---|---|---|
| Zuschneiden | `crop_to_coverage`, `trim_bad_edges` | ✅ |
| Gradient | Polynom + Randglühen + **Sensorglühen vor dem Stacken** | ✅ – GraXpert-KI wäre bei großen Nebeln besser (Polynom kann Nebel anknabbern) |
| Farbkalibrierung | Mittelwert der Sternfarben | ⚠️ kein SPCC; Plate-Solving-Lösung ist vorhanden, Katalogfarben fehlen |
| Entfaltung | Richardson-Lucy, profilabhängig | ⚠️ einfach; bei Rauschen riskant |
| Rauschen linear | `denoise_linear` (Wavelet) | ✅ ohne KI |
| **Sterne abtrennen** | fehlt | ❌ größte Lücke |
| Strecken | asinh + MTF je Kanal, Schwarzpunkt (`bg_dark`) | ⚠️ ein Durchgang, kein GHS mit Symmetriepunkt |
| Sternlos bearbeiten | Kontrast/Sättigung auf dem Gesamtbild | ❌ ohne Trennung nicht möglich |
| Sterne getrennt | `reduce_stars`, `tame_star_color` (Maskentricks) | ⚠️ Ersatzlösung |
| Zusammenführen | – | ❌ |
| Feinschliff/Qualität | messbare QC + optional KI-Prüfer | ✅ (Hobbyisten haben das nicht automatisiert) |

## 3. Was zuerst kommen sollte

1. **Sternentrennung + getrennte Bearbeitung (Schritte 6–10)** – der Hauptgrund, warum Community-Bilder „sauberer“ wirken als
   automatische: Nebel kann kräftig gestreckt werden, Sterne bleiben klein. Umsetzung:
   - **StarNet2-Kommandozeile** als optionales externes Programm (wie ASTAP): kostenlos, aber nicht quelloffen – AstroFlow
     ruft es nur auf, liefert es nicht mit. Siril nutzt genau diesen Weg.
   - Fallback ohne StarNet: eigene Sternmaske aus der vorhandenen Sternerkennung + Ausbessern (Inpainting) – schwächer, aber
     ohne Zusatzsoftware.
   - Danach: sternloses Bild mit GHS (s. 2) strecken, Sterne sanft und separat, Rekombination mit einstellbarer Sternhelligkeit
     (Screen-Mischung).
2. **GHS statt asinh+MTF**: Symmetriepunkt automatisch am Histogrammgipfel des Himmels (Nebel) bzw. leicht darüber (Galaxien),
   2–3 Durchgänge, Schatten-/Lichterschutz aus den Messwerten. Algorithmus ist veröffentlicht (Payne & Cranfield) und in Siril
   GPL-lizenziert – Nachbau in numpy ist überschaubar.
3. **Fotometrische Farbkalibrierung (SPCC-artig)**: Plate-Solving liefert Sternpositionen; Sternfarben aus Gaia (online, nur
   Koordinaten werden abgefragt) oder einem lokalen Katalogausschnitt. Verbessert vor allem Galaxien und Reflexionsnebel.
4. **GraXpert als optionales Backend** (GPL-3, Kommandozeile `-cli -cmd background-extraction|denoising`): KI-Hintergrund für
   Felder voller Nebel (IC 405, NGC 1579), KI-Entrauschen. Wird wie ASTAP nur aufgerufen, wenn installiert.

## 4. Wofür ein LLM taugt – und wofür nicht

**Nicht für Pixelarbeit.** Sprachmodelle verändern keine Bilder zuverlässig; generative Bildmodelle würden Strukturen
erfinden – das schließt der Produktanspruch („keine erfundenen Strukturen“) aus.

**Gut geeignet als Planer und Prüfer** – genau dort, wo Hobbyisten Erfahrung einsetzen:

| Aufgabe | Wie | Absicherung |
|---|---|---|
| **Bearbeitungsplan wählen** | LLM bekommt Objekt (Katalog, Typ, Größe im Feld), Messwerte (SNR, Dynamik, Sternanzahl, Hintergrund, Glühen), Aufnahmedaten und eine kleine Vorschau; antwortet mit einem JSON-Plan aus einer **festen Liste** von Schritten mit Parametern in Grenzen (z. B. „Sterne trennen, GHS SP=0,08 D=4 zweimal, Sternhelligkeit 0,7“) | nur Whitelist-Schritte, Parameter geklemmt, Plan wird mit dem Ergebnis gespeichert (reproduzierbar) |
| **Ergebnis beurteilen** | wie der heutige KI-Prüfer, zusätzlich Vergleich mehrerer Varianten („welche sieht natürlicher aus?“) | messbare QC entscheidet mit; bestes Bild nach QC-Score + LLM-Votum |
| **Erklären** | Text für die Nutzeroberfläche: was gemacht wurde, warum, was mehr Belichtung bringen würde | rein informativ |
| **Lernen aus Vorlieben** | Nutzer bewertet Versionen (👍/👎) → als Beispiele in den nächsten Plan-Prompt | lokal gespeichert |

Erfahrungen aus AstroFlow selbst: Ein lokales Denkmodell (Bonsai 27B) braucht ~1 min pro Runde und regelt nicht konsistent
(Kontrast rauf, nächste Runde runter). Deshalb: kleine Schritte, Rückfall auf die regelbasierte Automatik, LLM optional.
Die messbare QC bleibt die Instanz, die nie halluziniert.

## 5. Vorgeschlagene Reihenfolge

Umgesetzt (2026-10-28): **A** (GHS mit automatischem Symmetriepunkt, für das sternlose Bild) und **B** (Sternentrennung:
StarNet2 optional, eigene Trennung als Standard; getrennte Bearbeitung, Rekombination; Sternhaufen ohne Trennung) sowie
**D** (Farbkalibrierung über Gaia-Sternfarben, Weiß = G2V) und die optionale StarNet-Installation über die Einstellungen.
Nach dem Vergleich an NGC 7000 ist die Sternentrennung **standardmäßig aus** (mit der eigenen Trennung wirkten flächige Nebel
matschiger); sie bleibt je Objekt zuschaltbar und sollte mit StarNet erneut verglichen werden.

| Stufe | Inhalt | Aufwand |
|---|---|---|
| A | GHS-Stretch mit automatischem Symmetriepunkt (ersetzt asinh+MTF, bisherige Variante als Option) | klein |
| B | Sternentrennung: StarNet2-CLI optional + eigener Fallback; getrennte Bearbeitung und Rekombination | mittel |
| C | LLM-Bearbeitungsplaner (Whitelist-JSON, Varianten-Vergleich, Plan im Ergebnis gespeichert) | mittel |
| D | SPCC-artige Farbkalibrierung über Gaia-Farben | mittel |
| E | GraXpert-CLI als optionales Hintergrund-/Entrausch-Backend | klein |

## Quellen

- Vaonis: [Smart-Teleskop – Software und Nachbearbeitung](https://vaonis.com/blogs/travel-journal/astrophotography-with-a-smart-telescope-software-and-post-processing-workflow)
- Seestar: [Quick post-processing guide for Wizard Nebula](https://www.seestar.com/blogs/tutorial/quick-post-processing-guide-for-wizard-nebula)
- Siril: [GHS-Tutorial](https://siril.org/tutorials/ghs), [Stretching-Doku](https://siril.readthedocs.io/en/1.2/processing/stretching.html),
  [StarNet-Integration](https://siril.org/tutorials/integrated-starnet/), [StarNet-Doku](https://siril.readthedocs.io/en/1.2/processing/stars/starnet.html)
- [GraXpert (GitHub, GPL-3, CLI)](https://github.com/Steffenhir/GraXpert)
- Siril-Workflow (FAS37): [PDF 2025](https://www.fas37.org/wp/wp-content/uploads/2025/05/Siril-Workflow.pdf)
- AstroBin-Foren: [Workflow 2025](https://app.astrobin.com/forum/topic/160846/please-share-with-a-newbie-what-is-your-processing-workflow-in-2025),
  [PixInsight-Workflow-Fallstricke](https://app.astrobin.com/forum/topic/58018/workflow-caveats-in-pixinsight-with-popular-tools),
  [Ohne StarNet besser?](https://app.astrobin.com/forum/topic/152301/can-we-process-astrophotography-images-better-without-starnet),
  [GHS-Stretching](https://app.astrobin.com/forum/topic/58316/processing/hows-my-stretching)
- pixls.us: [GHS](https://discuss.pixls.us/t/generalized-hyperbolic-stretch/45827),
  [Siril-Automatisierung (Starbash)](https://discuss.pixls.us/t/new-tool-for-automating-siril-and-other-workflows/54700),
  [LLM-Skill für Siril (Mond)](https://discuss.pixls.us/t/siril-skill-moon-stacking/60808)
