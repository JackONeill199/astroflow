# AstroFlow – Implementierungsplan (Stand 2026-10-27, v0.3 → v0.4)

Abgleich der Produktanforderungen mit der bestehenden Codebasis. Priorität: Qualität der
astronomischen Bildverarbeitung vor zusätzlicher UI.

## 1. Bestand (vorhanden und mit echten Daten geprüft)

| Bereich | Umsetzung | Modul |
|---|---|---|
| FITS-Analyse | Header, Bayer-Muster, Belichtung, Stellina-Header, Nächte (Mittag-zu-Mittag), Objektzuordnung, Duplikat-Fingerprint, Lesefehler → `failed` | `fits_io`, `scanner` |
| Plate-Solving | ASTAP / astrometry.net / nova, eine Lösung je Gruppe, Cache, Katalognamen aus `deep_sky.csv` | `platesolve`, `objects` |
| Qualität | Sterne, FWHM, Exzentrizität, Hintergrund, Rauschen; robuste Ausreißer-Ablehnung je Filter; Score | `quality` |
| Kalibrierung | Master-Dark/Bias/Flat (falls vorhanden), Dark-Skalierung, Hotpixel-Korrektur ohne Darks je CFA-Ebene | `calibration` |
| Registrierung | Dreiecks-Matching (Verschiebung, Rotation, Spiegelung), gemeinsame Referenz über Nächte und Filter | `registration` |
| Stacking | gewichtet (1/σ² · FWHM), Sigma-Clipping (Satelliten, Flugzeuge), additive + multiplikative Normalisierung je Frame (Sessions), inkrementell, RAM-Stapel, parallel | `stacking`, `pipeline` |
| Entwicklung | Gradient (Polynom + Randglühen), Farbkalibrierung über Sterne, Neutralisierung, Entfaltung, asinh/MTF-Stretch, Wavelet-Entrauschen, Sternreduktion, lokaler Kontrast, Sättigung, Sternfarb-Zähmung; Profile je Objekttyp | `processing`, `objects` |
| Ausgabe | TIFF 16 bit, PNG, JPEG, linearer FITS, Versionen | `pipeline._save` |
| Betrieb | Docker, Web-App, Watcher, Jobs, Upload-Aufräumen | `server`, `static` |

## 2. Lücken

| # | Anforderung | Lücke | Priorität |
|---|---|---|---|
| L1 | „Parameter dynamisch aus den Bilddaten“ (§6) | Profile sind feste Presets; nur FWHM fließt ein | **hoch** |
| L2 | „Automatische Qualitätskontrolle des Endergebnisses“ (§6.10) | nur optionaler KI-Prüfer; keine messbare Kontrolle | **hoch** |
| L3 | Zwei unabhängige Engines (§7) | `Pipeline.process` macht Stacking und Entwicklung in einem; Neu-Entwickeln läuft durch Analyse/Auswahl | **hoch** |
| L4 | Persistente Jobs mit Wiederaufnahme (§9) | Jobs werden nach Neustart nicht fortgesetzt; Frames werden je Stapel als `stacked` markiert, der Stack aber erst am Ende gespeichert → bei Abbruch inkonsistent | **hoch** (Fehler) |
| L5 | Qualitätsbewertung: SNR, Gradienten, Satellitenspuren (§2) | nicht gemessen (Spuren werden nur beim Stacken weggeclippt) | mittel |
| L6 | Vorher-Nachher-Vergleich, Qualitätsschätzung, Verbesserungsvorschläge (§8) | nur Versionsvergleich | mittel |
| L7 | Fotometrische Farbkalibrierung (§6.3) | Sternfarben-Mittelwert statt Katalogfarben | niedrig (Risiko R2) |
| L8 | Geometrische Verzerrung (§4) | nur affin | niedrig |
| L9 | GPU (§9) | keine | niedrig (optional) |
| L10 | Sensorprofile (§3) | keine | niedrig (keine Daten verfügbar) |

## 3. Umsetzung v0.4 (dieser Schritt)

1. **Develop Engine** (`astroflow/develop.py`, neu)
   - `measure(stack, cov)`: SNR des Objekts, Hintergrundrauschen, Signalanteil, Dynamik (Sterne/Kern).
   - `adapt(params, measure, kind)`: Startparameter aus den Messwerten (Entrauschen ∝ Rauschen, Stretch ∝ Signalanteil,
     Highlight-Kompression ∝ Dynamik, Sättigung ∝ SNR) – Profile bleiben Ausgangspunkt.
   - `assess(rgb)`: messbare Qualitätskontrolle (Hintergrundhelligkeit, Rauschen, Farbstich, Restgradient,
     Schwarz-/Weißclipping, Sättigung, Signal sichtbar) → Score 0–10, Probleme, Hinweise.
   - `develop(...)`: deterministische Korrekturschleife auf einer verkleinerten Vorschau (schnell), danach ein
     Render in voller Auflösung; optional KI-Prüfer obendrauf. Reproduzierbar: gleiche Daten → gleiche Parameter.
   - „Vorher“-Bild: einfacher linearer Auto-Stretch des Master-Stacks für den Vergleich.
2. **Engine-Trennung**: `Pipeline.stack()` (Analyse → Auswahl → Stacking → Master-Stack) und `Pipeline.develop()`
   (nur aus gespeichertem Master-Stack). `process()` = beides. API/UI: Bearbeitungsoptionen ändern → nur `develop`.
3. **Robuste Jobs**: Checkpoints während des Stackens (Frames erst nach gespeichertem Zustand als gestackt markieren),
   unterbrochene Jobs beim Start wieder einreihen.
4. **Frame-Metriken**: SNR, Hintergrundgradient, Satelliten-/Flugzeugspuren (verlängerte helle Strukturen);
   Spuren führen nicht zum Ausschluss (Clipping entfernt sie), Gradient/SNR fließen in den Score.
5. **UI**: Qualitätsbewertung mit Hinweisen, Vorher/Nachher-Schalter, Spuren-Spalte in „Aufnahmen“.

## 4. Später

- Fotometrische Farbkalibrierung mit Gaia-Farben (benötigt Katalogausschnitt ~ einige 100 MB oder Online-Abfrage).
- Verzerrungskorrektur (Polynom-Registrierung 2. Ordnung) für große Felder / Mosaike.
- GPU über CuPy als optionaler Ersatz für `ndimage` in Warp und Stacking.
- Drizzle, Mosaike, Komet-Modus.

## 5. Technische Risiken

| R | Risiko | Umgang |
|---|---|---|
| R1 | Automatik-Parameter passen nicht zu jedem Objekt | Qualitätskontrolle misst und korrigiert; Profile als sicherer Ausgangspunkt; manuelle Optionen bleiben |
| R2 | Fotometrische Farbkalibrierung braucht Katalog + exaktes WCS je Stack | zurückgestellt; Sternfarben-Kalibrierung bleibt |
| R3 | Gelöschte Rohdaten verhindern Neu-Stacken | Analyse bleibt gespeichert; Entwicklung arbeitet nur mit dem Master-Stack |
| R4 | RAM/Laufzeit bei 3 000+ Frames | Stapelgröße aus RAM, Checkpoints, Threads; GPU optional später |
| R5 | Keine erfundenen Korrekturen | nur gemessene Größen; fehlende Flats werden nicht simuliert, sondern als Hinweis gemeldet |
| R6 | Lizenzen | numpy/scipy/astropy (BSD), ASTAP (MPL-2, extern aufgerufen); SEP (LGPL) / Siril (GPL) bewusst nicht eingebunden |
