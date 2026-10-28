# Sage-Modul in AstroFlow

Herkunft: Repo `JackONeill199/starlight-sage` („Astronomy Buddy“, Node/Express – bleibt unverändert bestehen), hier nach Python portiert.
Lokal für **einen** Nutzer: Konten, Login, Rechtsseiten (Impressum/DSGVO) und der Git-Updater wurden bewusst weggelassen (Nutzerentscheidung 28.10.2026).

## Ablauf (`api.py` → `run`)
1. Upload (`POST /api/sage/analyze`, JPG/PNG/TIFF/GIF/FITS) → `data/sage/uploads/<stamp>.<ext>` + JPG-Vorschau (`make_preview`, FITS gestreckt und
   in Anzeige-Orientierung). Oder Bild aus der Galerie (`POST /api/sage/analyze/project/<id>`, Dienst `project_picture`).
2. Bestimmen, in dieser Reihenfolge:
   - Galerie-Bild: gespeicherte Lösung des Master-Stacks (Dienst `project_solution` → `stacking/services.project_solution`: `work/p<id>/wcs.json`
     + Lage des JPG-Ausschnitts im Stack per Phasenkorrelation; ~0,3 s, kein Neu-Lösen).
   - Lokaler Solver (Dienst `plate_solver` = `stacking/services.PictureSolver`): WCS im FITS-Header → sonst ASTAP **nur mit Positionshinweis**
     (Objektname im Dateinamen, z. B. „M42.jpg“, oder Projektkoordinaten) und Bildfeld-Schätzungen `FOV_GUESSES`.
   - nova.astrometry.net nur, wenn „Online-Lösen erlauben“ gesetzt ist und ein Schlüssel vorliegt (lädt das Bild hoch, nicht öffentlich).
   - Galerie-Bild ohne Lösung: wenigstens das bekannte Hauptobjekt erzählen.
3. Geschichte (`clients.write_story`, OpenAI-kompatibel: OpenAI, LM Studio, OpenRouter, eigener Server). Ohne Erzähl-KI wird trotzdem gespeichert.
4. Verlauf (`data/sage/history.json`) + Entdeckungen/Erfolge (`discoveries.json`, Port von `lib/discoveries.js`).

Aufträge laufen in Threads (`Module.tasks`, im Speicher), Fortschritt per Polling (`/api/sage/tasks[/id]`) statt SSE wie im Original.

## Wichtige Erkenntnisse
- **ASTAP löst gestreckte 8-Bit-Bilder (fertige JPG/PNG) beim Nutzer nicht** – auch nicht mit exakter Position und Bildfeld; der lineare Stack
  derselben Aufnahme löst in 0,7 s. Deshalb Galerie-Bilder über die Stack-Lösung, Uploads bevorzugt als FITS oder per nova.
- ASTAP braucht ein Bild in FITS-Orientierung (Zeile 0 = unten); gespiegelt findet es nichts. `services.load_fits_oriented` dreht JPGs um,
  `describe` rechnet Objektpositionen in Anzeige-Koordinaten (Zeile 0 = oben) zurück.
- Ohne Positionshinweis blind lösen dauert mit D05 > 30 s je Bildfeld und scheitert oft – wird daher nicht versucht.
- Entdeckungsdatei wird beim Start angelegt; sonst zählt die erste Analyse doppelt (Test `test_sage.py`).

## Einstellungen (Abschnitt `sage` in `data/settings.json`; Umgebungsvariablen wie im Original als Vorgabe)
`story_provider|story_base_url|story_model|story_api_key` (`OPENAI_API_KEY`, `OPENAI_MODEL`), `online_solve`, `nova_key` (`ASTROMETRY_API_KEY`),
`tts_provider`, `qwen_*` (`QWEN_TTS_*`, lokaler OpenAI-kompatibler Sprachserver, Standard `http://127.0.0.1:8880`), `elevenlabs_*` (`ELEVENLABS_*`).
Schlüssel werden nie an die Oberfläche ausgeliefert (`has_<key>`); leer speichern behält den alten Wert.
