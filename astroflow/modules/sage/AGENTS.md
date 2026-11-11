# Sage-Modul in AstroFlow

## Sage 2.0 – Enzyklopädie (seit 29.10.2026, Architektur + Plan: `docs/SAGE.md`)
Wissensbasis `data/sage/knowledge.db` (SQLite + FTS5, `kb.py`): entities/aliases/sources/facts/sections (versioniert)/edges/notes/
insights/runs/usage/stories/chats/http_cache. Regeln: **Messwerte nur aus Fachquellen** (`sources.py`: SIMBAD-TAP – Spiegel
**Harvard zuerst**, CDS war nicht erreichbar –, arXiv, ADS mit Token, SkyView/DSS2); KI-Texte sind `draft` mit Prüfhinweisen
(`research.validate`: unbelegt, unbekannte Quelle, Entfernung widerspricht Katalog); KI-Kanten `suggested`; Belege im Text als `[S<source_id>]`.
- `research.Researcher`: `ensure_stub` (lokaler Katalog über Dienst `catalog_resolve`, ohne Netz) → `research(name, deep)`:
  SIMBAD-Fakten (Entfernung = Parallaxe oder Median `mesDistance`, Streuung > 1,5× = `conflict`), Hierarchie (`h_link`), Literatur,
  dann optional Agent (`agent.run_agent`, Werkzeuge paper_details/arxiv_search/simbad_lookup, JSON-Ausgabe). Unspezifische SIMBAD-Typen
  (`VAGUE`: Cl*, sh …, z. B. NGC 7000, NGC 6992) → Objektart des AstroFlow-Katalogs, SIMBAD-Typ als Fakt.
- `agent.LLM`: Anbieter wie die Erzähl-KI (`story_*`), Modell `research_model`; Budgets `budget_month_usd`/`budget_run_usd`/`max_steps`;
  OpenRouter-Kosten echt (`usage.cost`), sonst Schätzung `fallback_price_mtok`; 400 mit „tool“ → ohne Werkzeuge weiter.
- `memory.py`: Dienst `observation_log` (Bilder-Modul: Nächte, Frames, Belichtung, Instrument, FWHM, Versionen mit Objekt-SNR; 20 s Cache)
  + Sage-Verlauf + Notizen → `derive()` (jede Aussage mit `basis`).
- `insights.py`: Aufgaben fill/papers/field/region/stale/targets, `Scheduler` (Intervall `proactive_hours`, erst 90 s nach Start), KI nur mit
  `proactive_llm`. Dubletten über `insights.key`. Erster papers-Lauf meldet nur die letzten 60 Tage.
- `assistant.py` (Chat mit Werkzeugen, sicherer Rechner), `narrate.py` (6 Formate; Audioführung = Segmente je Bildobjekt, ohne KI Vorlage).
- `kb_api.py` → `/api/sage/kb/…`; Oberfläche `static/sage-wiki.js|css` (Routen `#/sage/wiki`, `#/sage/o/<id>/<tab>`, `#/sage/entdeckungen`,
  `#/sage/netz[/<id>]`; Sammlung & Erfolge jetzt `#/sage/sammlung`). Klick ins Bild: Shell-`createViewer(…, {onTap})` + `SageWiki.pick`
  (geometrisch – Kreise sind ungefüllt). Beim Start `Module.backfill`: alte Bildbestimmungen/Aufnahmen verknüpfen, fehlende im Hintergrund aus SIMBAD.
- **Startseite als Portal** (seit 31.10.2026, Nutzerwunsch „Beispiel an anderen Projekten nehmen“ – Vorbild Wikipedia-Hauptseite,
  Telescopius, Stellarium): `#/sage/wiki` = Kopf mit Kennzahlen + Suche, Fokus-Banner (großes Bild `picture` = Ergebnis-JPG, Kurztext,
  Nächte/Belichtung/zuletzt, umschaltbar über Bildleiste), „Heute Nacht am Himmel“ (Kulmination um Mitternacht aus `insights.sun_ra`,
  ohne `observer_lat` für 50° N; Objekte ohne Eintrag öffnen die Wissenskarte), „Neu entdeckt“ (jüngste Entdeckung je Art),
  „Nach Objektart stöbern“ (filtert das Raster), „Alle Einträge“. Daten: `/kb/home` (`featured` + `tonight`), `/kb/entities`, `/kb/insights`.
  Vorher war hier die komplette Objektseite eingebettet (~3000 px vor dem Raster). Vorschaubilder sind um 1,3× hineingezoomt (Objekt klein in der Feldmitte). Das Bannerfoto dagegen nie zuschneiden (Nutzerwunsch: Seitenverhältnis behalten; auf 2560 px wirkte `cover` + Zoom wie ein Panorama-Streifen).
- **Dashboard der Objektseite** (Nutzerwunsch 29.10.2026, Vorlage: Mockup „Plejaden“; `objectView(…, {embedded: true})` bettet sie ohne Brotkrumen ein).
  Objektkopf: Titelbild (`data.hero`, wählbar), Kurzname, Bezeichnungen (`designations`), Faktenkarte, Himmelskarte (`/kb/skymap`).
  Übersicht: Bild + Galerie (`gallery`), KI-Zusammenfassung/Forschung/Persönlicher Kontext, Beschreibung, Schnellfakten
  (Durchmesser aus Winkelgröße × Entfernung, Helligkeit aus SIMBAD `flux` – fehlt z. B. für M45), Verwandte, Notizen, Aufnahmen.
  Export als Markdown mit Quellen: `/kb/entities/<id>/export.md`.
  Raster „Alle“ (`/kb/entities` ohne `q`) blendet Grundeinträge ohne eigenes Bild aus (`stats.hidden_stubs`; meist SIMBAD-Gruppen aus
  `_related_stub`, beim Nutzer 67 von 90 – Nutzerwunsch 31.10.2026); Suche, Wissensnetz und „Verwandte“ zeigen sie weiter. SIMBAD `h_link` liefert eine Zeile je Beleg → nach `main_id` deduplizieren.
- **Wissensnetz** (überarbeitet 11.11.2026, Befund: Ring aus losen Dreiergruppen + Klumpen, Text unlesbar, Seite sekundenlang leer):
  Zeichnen in `static/sage-netz.js` (`SageNetz.draw(host, graph, {center, mode, onChange})`), **ohne Kräftesimulation** – Überblick =
  Gruppen nach Objektart bzw. Sternbild als Sonnenblumen-Spiralen, gierig gepackt; Fokus (`center`) = Ringe nach Breitensuche (Winkel nach
  Nachfahren). Beides O(n). Gezeichnet in Bildschirmkoordinaten: Beschriftungen gleich groß, nach Wichtigkeit ohne Überlappung gesetzt.
  Begriffe sitzen mitten in der Gruppe, zu der sie überwiegend gehören (sonst Linienfächer); nach Sternbild ohne Begriffe, Katalog-Gruppen
  ohne Sternbild beim Sternbild ihrer Nachbarn. Tippen = Infokarte (Verbindungen, KI-Vorschläge bestätigen/verwerfen per `PATCH /kb/edges/<id>`).
  `/kb/graph` ohne `center`: eigene Objekte mit `nights/hours/images/constellation/image` am Knoten (keine Aufnahme-/Bearbeitungsknoten
  mehr) + Begriffe + Katalogobjekte, die ≥ 2 eigene verbinden; mit `center`: Begriffe fächern nur zu Begriffen auf. Kanten tragen `id`.
  Leere Seite kam vom ersten `observation_log` (json.loads aller Frame-Metriken + je Projekt zwei Verbindungen, beim Nutzer 2–5 s) →
  `json_extract` in SQL, eine Abfrage für alle Frames (~0,6 s); die Ansicht zeigt sofort ein Gerüst und füllt das Netz nach.
- Tests: `tests/test_sage_kb.py` (SIMBAD/arXiv/KI als Attrappen). `ASTROFLOW_SAGE_OFFLINE=1` (conftest) schaltet Fachdienste ab – `sources.Net.fetch` prüft sie bei jedem Abruf (seit 0.45.4; Hintergrundthreads überleben ihren Test).

Herkunft: Repo `JackONeill199/starlight-sage` („Astronomy Buddy“, Node/Express – bleibt unverändert bestehen), hier nach Python portiert.
Lokal für **einen** Nutzer: Konten, Login, Rechtsseiten (Impressum/DSGVO) und der Git-Updater wurden bewusst weggelassen (Nutzerentscheidung 28.10.2026).

## Ablauf (`api.py` → `run`)
1. Standard: zentral unter **Import** → Bilder-Galerie (`stacking.imported` → `_stacked`, wie nach dem Stacken; nur mit `auto_discover`).
   Sage zeigt **Zum Import** statt einer zweiten Uploadfläche. Ohne Bilder-Modul bleibt der direkte Upload verfügbar.
   Kompatibler Upload (`POST /api/sage/analyze`, JPG/PNG/TIFF/GIF/FITS) → `data/sage/uploads/<stamp>.<ext>` + JPG-Vorschau (`make_preview`, FITS gestreckt und
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

## Automatisch nach dem Stacken + Erfolge feiern
- Ereignis `stacking.stacked` → `Module._stacked` → `analyze_project(pid, auto=True)` (von `api.build_router` gesetzt). Quelle bekommt
  `auto: true`; dasselbe Ergebnis (`source.result`) wird nicht doppelt eingetragen. Geschichte nur, wenn `auto_story` (Standard an) und
  eine Erzähl-KI eingerichtet ist; sonst Eintrag ohne Geschichte („Neu erzählen“). Abschalten: `auto_discover`.
- Galerie-Analysen bekommen eine eigene Vorschaudatei (`projekt-<pid>-<result>-<zufall>.jpg`), Löschen trifft nur den eigenen Eintrag.
- Feiern: `SageStore.celebrate` merkt neue Objekte/Erfolge jeder Analyse in `celebrations.json` (max. 30); ungesehene gehen mit
  `/api/status` → `modules.sage.celebrate` raus. `sage.js celebrate()` zeigt sie app-weit **wie Steam-Erfolge unten rechts**
  (Nutzerwunsch; in der Hinweis-Spalte `#toasts`, Medaille, Funken, Glanz, Zeitbalken 7 s, beim Überfahren pausiert,
  `prefers-reduced-motion` beachtet). Ablauf, Klick oder × → `POST /api/sage/celebrations/ack` (gesehen). In den Entdeckungen leuchten
  Erfolge der letzten 3 Tage (`.ach.fresh`).

## Sammlung & Erfolge (seit 0.15.0, `store.py` + `collection.py`)
- `store.ACHIEVEMENTS` = `(id, Titel, Beschreibung, Symbol, Gruppe, Kennzahl, Ziel)` – jeder Erfolg ist ein Zähler gegen `stats[Kennzahl]`;
  daraus kommt auch der Fortschritt („17 / 25“). **IDs bestehender Erfolge nie ändern.** Neue Erfolge: Zeile ergänzen, Kennzahl in
  `SageStore._ext` bzw. `collection.log_stats` liefern; Kennzahlen, die nur von den Objekten abhängen, in `OBJECT_KEYS` (dann datiert `_dates` rückwirkend).
- Statistik = gespeicherte Basis (`discoveries.json`) + `_full_stats`: Objekte über Hook `store.enrich` (`collection.enrich`: Wissensbasis →
  Messier-/Caldwell-Nummer, Art, Sternbild, Entfernung) und Beobachtungsprotokoll über `store.log_stats` (Nächte, Nachteule = Aufnahme 2–6 Uhr
  Ortszeit des Rechners, Jahreszeiten nach Nachtdatum, Belichtung). Ohne Hooks (Tests) zählen nur Namen + Tabelle. Messier-Nummer: Name
  („M 31“, „M31 Andromedagalaxie“) → NGC/IC-Tabelle (`MESSIER_NGC`) → Kennungen der Wissensbasis; Messier zählt je Nummer einmal.
- `refresh()` (bei jedem `overview()` und nach `backfill`) vergibt neue Erfolge rückwirkend (Datum: erreichter Stand in der Reihenfolge der
  Entdeckungen bzw. Nacht aus dem Protokoll, sonst jetzt) und feiert sie einmal gesammelt (`erfolge-<ts>`). `status()` nutzt nur `store.stats()` (billig).
- Caldwell nur über Kennungen der Wissensbasis (SIMBAD „C n“), keine eigene Tabelle – zählt also nur, was dort verzeichnet ist.
- Oberfläche: `discView` – Meilenstein-Balken, eigener Messier-Abschnitt (`/api/sage/discoveries` → `messier[{n, have, name, entry, entity, image}]`),
  Erfolge nach Gruppen. Sage-Erfolge beruhen nur auf echten Daten; SkyTrace-Erfolge fehlen bewusst (kein Dienst dafür).

## Wichtige Erkenntnisse
- **ASTAP löst gestreckte 8-Bit-Bilder (fertige JPG/PNG) beim Nutzer nicht** – auch nicht mit exakter Position und Bildfeld; der lineare Stack
  derselben Aufnahme löst in 0,7 s. Deshalb Galerie-Bilder über die Stack-Lösung, Uploads bevorzugt als FITS oder per nova.
- ASTAP braucht ein Bild in FITS-Orientierung (Zeile 0 = unten); gespiegelt findet es nichts. `services.load_fits_oriented` dreht JPGs um,
  `describe` rechnet Objektpositionen in Anzeige-Koordinaten (Zeile 0 = oben) zurück.
- Ohne Positionshinweis blind lösen dauert mit D05 > 30 s je Bildfeld und scheitert oft – wird daher nicht versucht.
- Entdeckungsdatei wird beim Start angelegt; sonst zählt die erste Analyse doppelt (Test `test_sage.py`).

## Einstellungen (Abschnitt `sage` in `data/settings.json`; Umgebungsvariablen wie im Original als Vorgabe)
`story_provider|story_base_url|story_model|story_api_key` (`OPENAI_API_KEY`, `OPENAI_MODEL`), `online_solve`, `nova_key` (`ASTROMETRY_API_KEY`),
`tts_provider` (Standard `openrouter`), `openrouter_key` (`OPENROUTER_API_KEY`; leer = Schlüssel der Erzähl-KI, wenn diese OpenRouter nutzt),
`openrouter_tts_model` (Standard `elevenlabs/eleven-v4`), `openrouter_tts_voice` (Standard `george`), `qwen_*` (`QWEN_TTS_*`, lokaler
OpenAI-kompatibler Sprachserver, Standard `http://127.0.0.1:8880`), `elevenlabs_*` (`ELEVENLABS_*`, direkt mit eigenem Konto).

## Modelle und Sprachausgabe
- Auswahlfelder (Shell-Baustein `picker` in `app.js`) holen Listen über `GET /api/sage/models?kind=story|speech|elevenlabs` bzw.
  `GET /api/stacking/reviewer/models` (Bildeingabe). Gemeinsamer Abruf: `core/models.list_models` (OpenAI-kompatibles `/models`, 5 min Cache;
  OpenRouter liefert Preise, Kontext und bei Sprachmodellen `supported_voices`).
- OpenRouter-Sprachausgabe: `POST https://openrouter.ai/api/v1/audio/speech` `{model, input, voice, response_format: "mp3"}` → MP3-Bytes.
  Lange Texte in Stücken ≤ 4000 Zeichen (`clients.chunks`), MP3 einfach aneinandergehängt. Kosten je Zeichen (Eleven v4 ~0,08 $/1000).
- Gespeicherte Vertonung merkt sich `variant` (Modell|Stimme); bei anderer Stimme wird neu vertont, die alte Datei ersetzt.
Schlüssel werden nie an die Oberfläche ausgeliefert (`has_<key>`); leer speichern behält den alten Wert.
