# Sage 2.0 – lebende Astronomie-Enzyklopädie (Architektur & Plan)

Stand 29.10.2026. Sage war bisher „Bild bestimmen → Geschichte → Vorlesen → Sammlung“. Sage 2.0 macht daraus ein
Wissenssystem, das Fachkataloge, eigene Aufnahmen und KI-Recherche verbindet. Alles Bisherige bleibt erhalten
(Upload/Galerie-Analyse, Objektmarken, Geschichten, OpenRouter/ElevenLabs/Qwen-Vorlesen, Erfolge).

## Grundsätze
1. **Messwerte kommen aus Fachquellen, nicht vom Sprachmodell.** Koordinaten, Typ, Entfernung, Parallaxe, Spektraltyp,
   Größe, Hierarchie (Mitglied von / enthält) stammen aus SIMBAD (jede Entfernungsmessung mit Bibcode). Das LLM recherchiert,
   interpretiert und schreibt – jede Aussage muss auf eine nummerierte Quelle verweisen.
2. **Nichts KI-Erzeugtes gilt ungeprüft als Wissen.** Abschnitte der KI haben den Status `draft` („KI-Entwurf“), bis der Nutzer
   sie prüft. Der Validator markiert Absätze ohne Beleg, Verweise auf unbekannte Quellen und Zahlen, die den Katalogwerten
   widersprechen. KI-Beziehungen im Wissensnetz sind `suggested`, bis sie bestätigt werden.
3. **Persönliche Aussagen nur aus echten Daten.** „Du hast M45 dreimal aufgenommen …“ wird aus der Bilder-Datenbank (Nächte,
   Frames, Belichtung, Instrument, gemessenes Objekt-SNR je Version) berechnet und mit seiner Grundlage angezeigt.
4. **Begrenzte, nachvollziehbare Agenten.** Jeder Lauf hat Schritt-, Kosten- und Monatsbudget, ein Protokoll (`runs.steps`)
   und speichert die abgerufenen Quellen samt Abrufzeit. HTTP-Antworten werden gecacht (Kosten, Reproduzierbarkeit).
5. **Modular.** Alles lebt im Sage-Modul; vom Bilder-Modul kommen nur Dienste (`observation_log`, `catalog_resolve`, …).

## Speicher: SQLite statt PostgreSQL/pgvector (geprüft)
Ein Nutzer, ein Container (Unraid), Rest der App ist SQLite. PostgreSQL brächte einen zweiten Dienst, Migrationen und Betrieb,
ohne bei dieser Datenmenge (Hunderte bis wenige Tausend Objekte) etwas zu gewinnen. Gewählt: `data/sage/knowledge.db`
(WAL) mit **FTS5** für die Volltextsuche (BM25, Umlaute/Diakritika-tolerant). Wissensgraph = relationale Kantentabelle.
Semantische Suche (Phase 3): Embeddings als BLOB-Tabelle, Kosinus per numpy – bei < 50 000 Vektoren im Millisekundenbereich.
Ein Umstieg auf PostgreSQL bleibt möglich, weil der Zugriff in `kb.py` gekapselt ist.

| Tabelle | Inhalt |
|---|---|
| `entities` | Objekte, Konzepte (Reflexionsnebel, Sternentstehung …); Name, SIMBAD-Hauptkennung, Typ, Koordinaten, Zusammenfassung, Status |
| `aliases` | normierte Kennungen → Objekt (M 45, NGC …, NAME Pleiades, Plejaden) |
| `sources` | Katalog-Abfragen, Fachartikel (Bibcode, DOI, Abstract), Preprints (arXiv), AstroFlow-Grundwissen, mit Abrufzeit |
| `facts` | Messwerte je Objekt mit Quelle, Herkunft (`catalog/derived/ai/user`) und Status (`current/alternative/conflict`) |
| `sections` | Texte je Objekt (Überblick, Physik, Forschung, offene Fragen …) mit Quellenliste, Validierungs-Hinweisen, **Versionen** |
| `edges` | Beziehungen (Mitglied von, Typ, verwandt …) mit Herkunft, Status und Beleg |
| `notes` | persönliche Notizen, geplante Beobachtungen, Interessen (anlegen/ändern/löschen) |
| `insights` | „Meine Entdeckungen“ mit Begründung, Objekten, Quellen (eindeutiger Schlüssel gegen Dubletten) |
| `runs` / `usage` | Recherche- und Chat-Läufe mit Protokoll; Token und Kosten je LLM-Aufruf (Budget) |
| `stories`, `chats`, `http_cache`, `meta` | Erzählformate samt Audio, Gesprächsverlauf, Antwort-Cache, Zeitstempel der Hintergrundaufgaben |

## Bausteine (`astroflow/modules/sage/`)
- `kb.py` – Wissensbasis (Schema, Suche, Graph-Abfragen, Budget-Summen).
- `sources.py` – Fachquellen mit Cache: SIMBAD-TAP (CDS, **Spiegel Harvard** – CDS war nicht erreichbar), arXiv,
  NASA ADS (Token optional), SkyView (NASA, DSS2-Farbbild als Himmelsansicht ohne eigenes Foto).
- `agent.py` – OpenAI-kompatibler Werkzeug-Agent (OpenRouter, OpenAI, LM Studio): Werkzeugaufrufe, Budget, Protokoll,
  Rückfall ohne Werkzeuge für Modelle ohne Function Calling.
- `research.py` – Research Harness: Auflösen → Katalogfakten → Hierarchie/Typ-Kanten → Literatur → (optional) agentische
  Vertiefung + Synthese → Validierung → versioniert speichern. Läuft auch ohne LLM (dann nur Katalogwissen).
- `memory.py` – persönliches astronomisches Gedächtnis: Aufnahmen, Nächte, Geräte, Versionen, Bildbestimmungen, Notizen;
  abgeleitete Aussagen mit Grundlage.
- `insights.py` – Hintergrundaufgaben (konfigurierbar, mit Intervall): neue Forschung zu fotografierten Objekten, weitere
  katalogisierte Galaxien in eigenen Bildfeldern, gemeinsame Regionen/Gruppen, fehlende Einträge ergänzen, veraltete markieren,
  Beobachtungsvorschläge (mit Standort).
- `assistant.py` – „Frag Sage“: Chat mit Werkzeugen (Enzyklopädie, eigene Daten, SIMBAD, arXiv, Rechner); Antworten trennen
  **Fakt [Quelle]**, **Deine Daten** und **Interpretation**.
- `kb_api.py` – REST unter `/api/sage/kb/…`; `static/sage-wiki.js|css` – Enzyklopädie, Objektseiten mit 7 Tabs, Wissensnetz,
  Entdeckungen, Chat, Audioführung.

## Abläufe
- **Nach jeder Bildbestimmung** (Upload, Galerie, automatisch nach dem Stacken): erkannte Objekte bekommen sofort einen Eintrag
  (lokaler Katalog), fehlende werden im Hintergrund aus SIMBAD ergänzt; im Bild ist jedes Objekt anklickbar → Wissenskarte.
- **Objektseite:** Übersicht · Wissen & Details · Meine Beobachtungen · Forschung & Entdeckungen · Bilder & Vergleiche ·
  Zusammenhänge · Geschichten & Audio, dazu der Chat am Rand.
- **Erzählformate:** Dokumentation, Geschichte, Beobachtungsbericht, Einsteiger, Deep Dive, **Audioführung** (Abschnitte je
  Bildobjekt; beim Abspielen wird das jeweilige Objekt im Bild hervorgehoben). Ohne KI gibt es eine Führung aus Katalogdaten.

## Priorisierter Plan
- **Phase 1 (umgesetzt):** Wissensbasis, SIMBAD/arXiv/ADS/SkyView mit Cache, Research Harness mit Validierung und Budget,
  Objektseiten mit Tabs, Wissenskarte beim Klick ins Bild, persönliches Gedächtnis + Notizen/Pläne, Wissensnetz, Meine
  Entdeckungen mit Hintergrundaufgaben, Chat mit Werkzeugen, Erzählformate + Audioführung.
- **Phase 2:** Gaia-DR3-Mitglieder für Sternhaufen (Alter/Entfernung aus eigenem Fit), VizieR-Kataloge, NED für Galaxien,
  Abgleich neuer Forschung mit bestehenden Abschnitten (Diff → „Aktualisierung vorgeschlagen“), Bildregionen aus dem Stack
  (Helligkeitsmaxima/Nebelstrukturen) als „interessante Stellen“.
- **Phase 3:** Embeddings für semantische Suche und Chat-Retrieval, Graph-Exploration mit Pfadsuche, Export/Teilen von
  Objektseiten, Mehrsprachigkeit.

## Risiken
- Externe Dienste können ausfallen oder blockiert sein → Spiegel, Cache, klare Meldung, nichts bricht ab.
- LLM-Kosten → Monats- und Laufbudget, Hintergrundaufgaben nutzen das LLM nur nach ausdrücklicher Freigabe.
- Halluzinationen → Belegpflicht, Validator, Status „KI-Entwurf“, Fachwerte nur aus Katalogen.
