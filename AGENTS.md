# AGENTS.md – AstroFlow

Kompakter Einstieg für KI-Agenten und neue Sessions. Falls vorhanden zusätzlich `AGENTS.local.md` lesen (lokal, nicht im Repo).
Details je Thema stehen in `docs/entwicklung/` (Wegweiser unten) – **nur die Datei lesen, die die Aufgabe betrifft**.
Nutzerdoku: `README.md`, Änderungen: `CHANGELOG.md` (bei Nutzer-sichtbaren Änderungen ergänzen), Wünsche für später:
`docs/FEATURE-WUENSCHE.md` (bei Umsetzung Status nachtragen).

## Was ist das
AstroFlow: lokale Astro-Plattform im Browser aus drei Modulen (seit v0.6, 28.10.2026 – Nutzerwunsch: „ein Produkt, einheitliche UI,
Module einfach wartbar und erweiterbar“):
- **Bilder** (`modules/stacking`, Kernmodul): vollautomatische Pipeline FITS-Rohbilder → fertiges Astrofoto.
- **SkyTrace** (`modules/skytrace`, aus Repo `JackONeill199/SkyTrace`): Lichtspuren/bewegte Objekte in Bildfolgen, Katalogabgleich. Details: `astroflow/modules/skytrace/AGENTS.md` (+ `docs/entwicklung/skytrace-*.md`).
- **Sage** (`modules/sage`, aus Repo `JackONeill199/starlight-sage`, Node → Python portiert): seit 2.0 lebende Astronomie-Enzyklopädie – Bilder bestimmen, Objekte aus Fachkatalogen + KI-Agent recherchieren (Belegpflicht), persönliches Beobachtungsgedächtnis, Wissensnetz, proaktive Entdeckungen, Chat, Geschichten/Audioführung. Details: `astroflow/modules/sage/AGENTS.md`, Architektur `docs/SAGE.md`.
Zielgruppe: Smart-Teleskope (Seestar, Vespera, Stellina) und klassische Astrokameras. Lokal, ein Nutzer, keine Konten.
Stack: Python 3.12+ · numpy/scipy/astropy · FastAPI + uvicorn · SQLite · Vanilla-JS-SPA ohne Build-Schritt. Lizenz PolyForm Noncommercial 1.0.0 (Quellcode offen, private und nicht-kommerzielle Nutzung frei; Stände bis v0.51.3 MIT) – SkyTrace-Engine `engine/`: AGPL-3.0-or-later (vom Server geladene Hilfsdateien doppelt lizenziert, `skytrace/AGENTS.md`), Detektormodell `models/`: AGPL-3.0, Katalogdaten CC-BY-SA 4.0 (siehe `NOTICE`). Version: `astroflow/__init__.py` (Regeln unter „Versionierung“).

**Sprache:** UI, README, Kommentare und Fehlermeldungen auf **Deutsch**. Der Nutzer schreibt Deutsch.

## Wegweiser (Thema → Datei)
| Thema | Datei |
|---|---|
| Bilder: Scan, Analyse, Auswahl, Kalibrierung, Sensorfehler, Stack-Reparatur (`stackfix`), Stacking, Performance/GPU, Satellitenspuren, Verzerrungskorrektur, Kanäle, Sensorglühen, Stacking-Stufen, Live-Ansicht | `docs/entwicklung/bilder-stacken.md` |
| Bilder: Develop Engine, Bearbeitung (`processing`), Sternentrennung/GHS, Gaia-Farbkalibrierung, StarNet, GraXpert, Stellina-/M57-Vergleiche, Farbverlauf, Tutor, KI-Prüfer, Ausgabe | `docs/entwicklung/bilder-entwickeln.md` |
| Speicher/Upload-Löschen, volles Laufwerk, Aufräumen, Hauptordner-Import, Plate-Solving, Daten & Konfiguration (Env), Server/Engine/REST, gzip | `docs/entwicklung/daten-server.md` |
| Installation, Starter, Assistent, Komponenten (ASTAP, GraXpert, GPU, SkyTrace-Engine), Prüfsummen, Infobereich, Paket/Inno/Sandbox, Messwerte | `docs/entwicklung/installation.md` |
| Standorte (`core/sites.py`), Himmelsqualität, saubere Nächte | `docs/entwicklung/standorte-himmel.md` |
| Kometenmodus, Heute Nacht (`tonight`, `deepsky`) | `docs/entwicklung/kometen-heute-nacht.md` |
| Tiefe, Vergleich (Grenzgröße, helle Nachbarn, Hotpixel-Reste) | `docs/entwicklung/analyse-tiefe-vergleich.md` |
| HR-Diagramm, Asteroiden, Gerätediagnose, Analyse-Dashboard | `docs/entwicklung/analyse-auswertungen.md` |
| Web-UI: Routing, Designsystem, Look, Seitenwechsel, Viewer, 3D, Frontend-Helfer (`folderDialog`, `picker` …), Import fertiger Bilder | `docs/entwicklung/web-ui.md` |
| Langfassung Architektur (Dienstliste), Sicherheit (CodeQL-Begründungen), Tests (Historie) | `docs/entwicklung/architektur-sicherheit-tests.md` |
| SkyTrace (Überblick, feste Regeln, Architektur) | `astroflow/modules/skytrace/AGENTS.md` |
| SkyTrace: Erkennung · Satelliten/Uhr · Standorte/Rückrechnung · 3D-Erde/Karten · Lauf-Ansicht/Video/Auto · Statistik/Erfolge | `docs/entwicklung/skytrace-erkennung.md`, `skytrace-satelliten-uhr.md`, `skytrace-standorte.md`, `skytrace-erde-3d.md`, `skytrace-lauf-ansicht.md`, `skytrace-statistik-erfolge.md` |
| Sage | `astroflow/modules/sage/AGENTS.md`, `docs/SAGE.md` |
| Ausbauplan (historisch), Bearbeitungs-Recherche, Release | `docs/PLAN.md`, `docs/BEARBEITUNG.md`, `docs/RELEASE.md`, `docs/PUBLIC-RELEASE.md` |

## Produkt-Architektur (Kern + Module)
```
astroflow/server.py        Kompositionswurzel: lädt Module, /api/app|status|activity|modules, Herkunftsschutz, statische Dateien
astroflow/core/            config.py (Settings), store.py (data/settings.json, ein Abschnitt je Thema), modules.py (BaseModule, Context, Registry), sites.py (Ortsliste)
astroflow/modules/<id>/    __init__.py (class Module), api.py (Router → /api/<id>), static/ (→ /m/<id>/), AGENTS.md
astroflow/static/          Shell: index.html, app.js (Router, Navigation, Übersicht, Aktivität, Einstellungen, Palette, Viewer, Helfer), style.css
```
- **Modul-Vertrag Backend** (`core/modules.py`): `router()`, `start()/stop()`, `status()` (klein, wird alle 2,5 s gepollt; `busy_jobs` zählt für die Anzeige),
  `activity()` (gemeinsames Format `{key,title,kind,detail,state,progress,started,finished,href}`), `scripts/styles`. Reihenfolge/Liste: `AVAILABLE`.
- **Module reden nicht per Import miteinander**, sondern über Dienste in `ctx.services`. Stacking: `plate_solver`, `project_picture`,
  `project_solution`, `project_frames`, `observation_log`, `catalog_resolve`, `catalog_objects`, `site_sky`, `night_zero_points`, `asteroid_tally`;
  Sage: `object_facts`, `collection_progress`; Kern: `sites` (`SiteBook`), `site_for`, `night_sources`. **Fehlt ein Dienst, muss das Modul trotzdem laufen.**
  Bedeutung je Dienst: Langfassung in `architektur-sicherheit-tests.md`.
- **Ereignisse** (`ctx.on` / `ctx.emit`, Liste in `core/modules.py`): `stacking.stacked(project_id, result_id, name)` kommt aus
  `Pipeline.process` nach dem Entwickeln und **vor** dem Löschen hochgeladener Rohbilder (`Pipeline.on_stacked`). Empfänger: SkyTrace
  `auto.AutoAnalysis` (Einstellung `skytrace.auto`) und Sage `Module._stacked` (`sage.auto_discover`/`auto_story`). **Empfänger kehren sofort
  zurück** (eigene Threads). Weitere: `stacking.imported` (fertige Bilder → Sage), `stacking.asteroids` (→ Sage `store.refresh`).
- **Modul-Vertrag Frontend**: `AF.register(id, {nav, routes, summary, recent, discoveries, homeFeatured, settings, palette, status, drop, sitesPanel})`
  (Kommentar in `app.js`). `nav[].area` 'work' | 'library' | sonst Unterpunkt (`parent`, 'home' = unter der Übersicht). Kontextpanel `AF.ctx.open(el, {title, html|render})`,
  Prozessanimationen `vizProcess` (`static/viz.js`). Globale Helfer (`api`, `send`, `toast`, `createViewer`, `folderDialog`, `picker`, `jobLine`, `confirmDlg`,
  `crumbs`, `store` …): `web-ui.md`. Modul-Skripte lädt die Shell aus `/api/app` mit `?v=<build>` – nur `index.html` braucht das manuelle `?v=`.
- Module ein/aus: Einstellungen → Module & System bzw. `ASTROFLOW_MODULES=stacking,skytrace`; ein defektes Modul erscheint mit Fehler, die App läuft weiter.
- **Neues Modul:** Unterpaket mit `Module(BaseModule)` anlegen, in `AVAILABLE` eintragen, `static/<id>.js` mit `AF.register`, Tests in `tests/`.

## Sicherheit (verbindlich; `core/security.py`, Middleware `guard` in `server.py`, seit 0.46.0)
1. **Host-Prüfung:** `Host` muss `localhost`, eine IP-Adresse, der Rechnername (auch `.local`, `.fritz.box`, `.home`, `.lan`, FQDN) oder ein Name
   aus `ASTROFLOW_HOSTS`/`settings.json` → `lan.hosts` sein, sonst 400.
2. **Herkunft:** schreibende Methoden brauchen `Origin` (Rückfall `Referer`) mit derselben Adresse wie `Host` (sonst 403) – **auch eigene
   Hilfsprogramme setzen ihn** (`tray.py`, `launcher.stop`, `installer/pruefe_paket.py`).
3. **Kopplung:** Clients, die nicht Loopback sind (`request.client.host`; Docker: `ASTROFLOW_TRUSTED_NETS`), brauchen das Cookie `af_geraet`
   (HttpOnly, SameSite=Strict, 10 Jahre) aus `GET /api/pair?code=` (Code aus Einstellungen → Heimnetz, QR per `core/qr.py`, 15 min, einmalig).
   Gespeichert nur der SHA-256 (`settings.json` → `pairing.devices`). Sonst 401 (`{pair: true}` unter `/api`, sonst HTML „Gerät koppeln“).
   `GET/POST /api/pairing[/code|/remove]` nur lokal. Frontend `static/pairing.js`.
4. **Nur am Rechner selbst** (`security.require_local(request, was)` → 403, auch mit Token): Komponenten installieren/entfernen,
   `/setup/astap|gpu|firewall`, `/setup {lan|data_dir}`, `/system/shutdown|restart`, StarNet-Pfad/-ZIP/-Entfernen, GraXpert-, ASTAP- und
   SkyTrace-Pfade (nur bei **geänderten** Werten – Formulare schicken die Felder mit). **Neue gefährliche Endpunkte genauso schützen.**
   UI: `data-local-only` an Knöpfen/Feldern/Formularen – `app.js` (`markLocalOnly`) graut sie aus, wenn `/api/app` → `local: false`.
5. **GET ohne Seiteneffekte mit Geheimnissen:** Modelllisten (`/stacking/reviewer/models`, `/sage/models`) sind POST; gespeicherte Schlüssel
   nur an die gespeicherte bzw. feste Anbieteradresse (`core/models.key_for`).
6. **Pfade vom Client:** Ordner *auflisten* (Namen + Bildzählung) ist für gekoppelte Geräte gewollt – `/api/folders[/search]` und
   `/api/skytrace/observations`; ebenso eine Analyse/Reanalyse aus einem gewählten Ordner starten (**Entscheidung des Nutzers 09.11.2026**, bewusst
   ohne `require_local`; CodeQL `py/path-injection` dort = Fehlalarm, Begründungen in PR #17). Dateien *ausliefern* nur unterhalb fester Wurzeln
   (`skytrace.within`, Ergebnis-/Ausgabeordner aus der DB). Programm- und Datenordnerpfade nur mit `require_local`. **Keine neuen Endpunkte, die
   beliebige Pfade prüfen/lesen, ohne einen dieser Wege.**
7. **Keine Ausnahmetexte an den Browser** (`py/stack-trace-exposure`): feste deutsche Meldungen, Details per `traceback.print_exc()` ins Protokoll
   (Beispiele `picture_import.user_message`, `stacking/api.ki_error`). Regex auf Nutzer-/KI-Text ohne mehrdeutige Wiederholungen bzw. mit begrenzter
   Länge (`sage/store.plain`, `sage/kb.norm`); im Frontend alles über `esc()`.

Schlüssel stehen im Klartext in `data/settings.json`. Tests: `tests/test_security.py` (`conftest.py`: TestClient als 127.0.0.1 mit eigener `Origin`).

## Wenn du … änderst (Pflichtregeln, Details in der jeweiligen Datei)
- **Frame-Analyse oder Kosmetik** (`quality.py`, `calibration.cosmetic`): `quality.METRICS_VERSION` erhöhen (löst Neu-Analyse + Neu-Stacken aus).
  Ausnahmen bewusst ohne Erhöhung: neue Zusatzmetriken (`sky`, `bgplane`, `ecc_pa`, `tiles`, `trail_lines`) und Sensorfehlerkarten – nur nach Nutzerentscheidung.
- **`develop.assess`**: `QC_VERSION` erhöhen. **Tutor-Bericht**: `TUTOR_VERSION`. **Tiefe/Vergleich/HR-Diagramm/Asteroiden/Gerätediagnose**:
  `DEPTH_/COMPARE_/HRD_/ASTEROID_/DIAG_VERSION` erhöhen, wenn alte Ergebnisse falsch wären. **`stackfix`**: `stackfix.VERSION`.
- **Große Ergebnisse** (Tiefe, Vergleich, HRD, Asteroiden) nie in die gepollte Projektansicht (`_slim`), sondern eigene Endpunkte.
- **Neue DB-Spalte**: in `db.MIGRATIONS` eintragen; sonst lieber ins JSON (`frames.info`/`metrics`, `results.stats`, `projects.options`).
- **Neuer gefährlicher Endpunkt / Pfad vom Client**: Sicherheit 4 und 6; Fehlertexte nach 7.
- **Netzabfragen** (Gaia, SIMBAD, JPL, Horizons …): gemeinsame Schichten und Caches nutzen (`photometry.gaia_stars/gaia_field`, `compare.small_bodies`,
  `comet.horizons`); **Standort verlässt den Rechner nie** (geozentrisch abfragen, Parallaxe lokal). Online-Plate-Solving (`nova`) nie automatisch.
- **`core/sites.py`**: nur Standardbibliothek (die SkyTrace-Engine lädt die Datei über den Pfad).
- **Nachtdatum nur über `core/sites.night_of`** (Mittag bis Mittag Ortszeit, Ort/Rechnerzeitzone automatisch; Engine: `observation_settings.night_key`,
  Frontend: Nacht vom Server liefern) – nie selbst „Zeit − 12 h“ rechnen. Regel geändert → `stacking/nights.RULE` erhöhen (`standorte-himmel.md`).
- **Lizenz** (Nutzerentscheidung 09.11.2026): eigener Code steht unter PolyForm Noncommercial 1.0.0 (`LICENSE`), Ausnahmen haben eine eigene
  Lizenzdatei (`NOTICE`). Neuer SkyTrace-Code nach Möglichkeit **außerhalb** von `skytrace/engine/` (AGPL-3.0-or-later) – dort nur, was
  ultralytics/die Erkennung selbst braucht. Engine-Dateien, die der Server importiert, brauchen den Doppellizenz-Kopf
  (`# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0 OR AGPL-3.0-or-later`); neue solche Importe nur mit diesem Kopf.
  Neue Fremdbibliotheken/-daten: Lizenz prüfen und in `NOTICE` eintragen (Windows-Paket: `THIRD-PARTY-LICENSES.txt` sammelt automatisch).
- **Komponenten-Downloads**: feste SHA-256 und nur https (`_https_only`); neue ASTAP-Fassung → `ASTAP_SHA256` bewusst ergänzen.
  Nach Änderungen an `installer/`, Launcher, Setup oder Komponenten den manuellen Windows-Paket-Lauf vor einem Release anstoßen.
- **Ordnerlisten/Scans**: versteckte Einträge über `core/hidden.py` → `hidden_entry` ausblenden.
- **Projektoptionen**: Kometenmodus heißt `comet_mode` (**nicht** `mode` – das ist bei Aufträgen „develop“).
- **Reihenfolge Bearbeitung** (`processing.process`): Randglühen nicht vor dem ersten Polynom korrigieren (getestet, schlechter); Frame- und Stack-FWHM nie direkt vergleichen.
- **`index.html`/`app.js`/`style.css`**: `?v=` in `index.html` hochzählen.
- **CSS/UI**: alle Maße in `rem` (nur 1–2px-Rahmen und Media-Queries in px); nur Tokens aus `:root` (alte Aliase wie `--panel`/`--line` sind entfernt);
  `.btn` bekommt **keine eigenen `::before/::after`** (belegt durch `.pending`-Kreisel); Kreisel nur über `animation: var(--spin)`; Ein/Aus immer
  `label.modrow` + `input[type=checkbox]` + `span.switch`; keine Karte in der Karte; Animationen mit `prefers-reduced-motion`-Ausstieg;
  Menüs/Dialoge/Eingaben deckend, Eingaben nur `background-color` setzen.
- **Ansichten (JS)**: nach `await` vor globalem `$('#…')` `el.isConnected` prüfen; nur bei geänderter Signatur neu zeichnen; langsame Ansichten
  als Gerüst sofort zurückgeben und Abschnitte einzeln füllen; in `createViewer` keine lokale Variable `esc` (TDZ). Neue Ansicht → in
  `ROUTES` von `tests/test_ui_layout.py` eintragen; globales `td { white-space: nowrap }` beachten.
- **Neue Pipeline-Artefakte im Cacheschlüssel**: GraXpert hasht nur jeden 7. Pixel – Vorverarbeitung muss als `tag` in den Schlüssel (Beispiel `stats.hotfix.key`).

## Versionierung (verbindlich, seit 01.11.2026 – Nutzerwunsch)
- **Eine Quelle:** `astroflow/__init__.py` → `__version__`. `server.py` (`VERSION`, `/api/app`, `/api/status`) liest sie; nirgends sonst
  eine Nummer pflegen. Nicht verwechseln mit `quality.METRICS_VERSION`, `graxpert.GRAXPERT_VERSION` (Cache-Stände) und den Ergebnis-Versionen je Objekt.
- **Schema `0.MINOR.PATCH`** (vor 1.0): PATCH = Fehlerbehebung, Leistung, Texte – ohne neue Funktion. MINOR = neue Funktion/Ansicht/Einstellung/Modul,
  geänderte DB-Struktur, Datenformate oder API. 1.0 nur auf Entscheidung des Nutzers.
- **Jede für Nutzer sichtbare Änderung, die nach `main` geht**, erhöht im selben Branch die Version und bekommt ganz oben im `CHANGELOG.md`
  einen Abschnitt `## <Version> – <TT.MM.JJJJ>` (darunter `### Neu` / `### Behoben`). Mehrere Änderungen bis zum nächsten Merge = eine Version.
  Rein interne Änderungen (Tests, AGENTS.md, Refactoring ohne Wirkung) ohne Erhöhung.
- **Git:** Arbeit auf `feature/…` bzw. `fix/…` von aktuellem `main`, PR gegen `main`; direkt auf `main` nur auf ausdrücklichen Wunsch.
  Sind zwei Branches mit Versionserhöhung offen, nimmt der später gemergte die nächste freie Nummer (beim Merge-Konflikt anpassen).
- **Nach dem Merge in `main`:** annotiertes Tag auf den Merge-Commit – `git tag -a v<Version> -m "AstroFlow <Version>"` und `git push origin v<Version>`.
- **Prüfung:** `tests/test_version.py` – Format, oberste CHANGELOG-Version = `__version__`, Server meldet dieselbe.

## Befehle
```bash
.venv/Scripts/python -m pytest -q                # Windows-venv liegt im Repo (.venv); parallel (xdist, pytest.ini) ~1,2 min auf 16 Kernen (seriell 5,5 min); muss grün sein
.venv/Scripts/python -m pytest -q -m "not ui"    # schneller Teilsatz ohne Browser-Tests (~40 s)
.venv/Scripts/python -m pytest -q -p no:xdist -o addopts=""   # seriell (zum Debuggen eines einzelnen Tests, `-s`, pdb)
python -m astroflow serve --port 8080 --data ./data   # Web-UI (alle Module), nur dieser Rechner; --lan = auch Heimnetz (Kopplung)
python -m astroflow process --data ./data             # alles per CLI verarbeiten
Start.bat / Stop.bat                                  # Windows: Server im Hintergrund starten/beenden (Port 8766, ./data)
Netzwerk-freigeben.bat [entfernen]                    # Firewall-Regel „AstroFlow LAN“ (TCP 8766, privat, LocalSubnet; braucht Admin – Nutzer führt aus)
python -m astroflow demo ./data/input/demo            # synthetische Testdaten (Bayer, 2 Nächte, Darks/Flats)
docker compose up -d --build                          # Image enthält ASTAP + D05 (ARG ASTAP_DB=d05|d50)
python installer/build_windows.py                     # Windows: portables ZIP (+ Installer mit Inno Setup) nach dist/, s. docs/entwicklung/installation.md
astroflow                                             # installierte Version: Server im Hintergrund + Browser (Port 8766, ~/AstroFlow); astroflow stop
```
Git-Repo: github.com/JackONeill199/astroflow (Branch main; `data/`, `data_real/`, `.venv`, `AGENTS.local.md` per .gitignore ausgeschlossen).
SkyTrace-Engine-Pakete (OpenCV, PyTorch …) sind **nicht** in `requirements.txt` – siehe `astroflow/modules/skytrace/requirements-engine.txt` (Docker: `--build-arg SKYTRACE=1`).
Kein Linter/Formatter konfiguriert – Stil des umliegenden Codes übernehmen (knapp, deutsche Kommentare).

## Tests (`tests/`)
- **CI** (`.github/workflows/tests.yml`): volle Suite bei jedem PR/Push auf `main`, Ubuntu + Windows, Python 3.12, Playwright-Chromium (SkyTrace-Tests
  ohne PyTorch). Pflichtprüfungen für `main`: `pytest (ubuntu-latest)`, `pytest (windows-latest)`. **Neue Tests müssen auch unter Linux laufen oder sauber überspringen.**
- **Parallel:** `pytest.ini` setzt `-n auto --maxprocesses=8 --dist load` (pytest-xdist, `requirements-dev.txt` – dort auch Playwright). `conftest.py`
  begrenzt unter xdist BLAS-/Pipeline-Threads auf 2 je Worker. Browser-Fixtures `server`/`browser` in `conftest.py`; Browser-Tests tragen automatisch
  den Marker `ui`. Beim Entwickeln `-m "not ui"` bzw. nur die betroffene Datei, vor dem PR der volle Lauf.
- **Regeln für neue Tests:** nur `tmp_path`/`tmp_path_factory` (nie feste Pfade oder die Arbeitsinstanz), Server auf Port 0/`_free_port()`,
  Umgebungsvariablen nur per `monkeypatch`, keine Abhängigkeit von Reihenfolge oder anderen Tests. Tests laden standardmäßig nur das Stacking-Modul
  (Modultests: `test_modules.py`, `test_sage.py`, `test_skytrace.py`). Synthetische Daten aus `demo.py` (`generate`, `generate(anonymous=True)`, `generate_mono` …).
- **Schutz:** `conftest.py` setzt beim Import `ASTROFLOW_GRAXPERT/GPU/GAIA=0` (außerdem `HORIZONS`, `SKY`, `DIAG`, `UPDATE_CHECK` aus) und verbietet echte `graxpert*`/`starnet*`-Programme außerhalb des Temp-Ordners (`test_no_real_tools.py`; Absturz 04.11.2026). **Netzsperre**
  (`conftest._block_network`): httpx an andere Hosts als localhost/`testserver` → `ConnectError`; Attrappen per monkeypatch. Plate-Solving über
  ASTAP-Attrappe (`SolverConfig(cmd_prefix=[...])`).
- **Keine kopierten/umbenannten Programme starten** (nie `sys.executable` kopieren und als `.exe` ausführen – Virenscanner beenden den ganzen
  Prozessbaum, 09.11.2026). Skripte per `[sys.executable, skript.py]` sind ok; Selbstprüfungen über Haken (`components._astap_selftest`) ersetzen.
- **Oberfläche** (`test_ui_layout.py`): echter Chromium über Playwright, alle Ansichten mit Demodaten bei 375/790/1440 px: nichts ragt über seinen
  Kasten oder die Seite, keine JS-Fehler, Schalterzeilen schalten per Klick. Fehlt Playwright, wird übersprungen.

## Stolperfallen (Windows)
- Shell ist Git-Bash: In Heredocs/`python -c` werden Backslashes umgedeutet (`\a` wurde zu BEL). Windows-Pfade in Skripten → Skript per Datei schreiben, raw strings.
- Python-Dateien explizit mit `encoding="utf-8"` schreiben (Standard ist cp1252).
- ASTAP kann an mehreren Orten liegen (`C:\Program Files\astap` oft nicht beschreibbar, `%LOCALAPPDATA%\astap` für die aktuelle CLI + Datenbank).
- Zwei Server auf demselben Datenordner = zwei Worker (beim Start werden „running“-Jobs des anderen neu eingereiht) – Tests/Vorschauen mit eigenem Datenordner.

## Lokale Instanz und private Angaben
**Persönliches gehört nie ins Repo** (Benutzerpfade, Laufwerke, Heimnetz/IP, Beobachtungsort, Schlüssel). Wer eine eigene Instanz betreibt,
legt Pfade, laufende Server und echte Daten in **`AGENTS.local.md`** ab (steht in `.gitignore`) – Agenten lesen sie, falls vorhanden, zusätzlich.
Tests und Beispiele nutzen erfundene Orte/Pfade; Screenshots nur mit maskierten Pfaden und Standort.
- **Nie Testdaten in eine Arbeitsinstanz schreiben** – Experimente in eigenem Datenordner mit kopierten Frames (s. Stolperfallen: zwei Server).
- Datenordner beim Verschieben: Pfade stehen absolut in `frames.path`, `frames.info.path`, `results.files`, `solves.key` → alle umschreiben.
  Frame-IDs hängen am Pfad relativ zum Eingangsordner – bleibt die Struktur unter `input/` gleich, werden Dateien nicht neu eingelesen.

## Grenzen / mögliche nächste Schritte
Keine Mosaike (Stackfläche = Referenzbild), kein echtes Drizzle (2× interpoliert), keine Planeten-/Mond-Modi (Kometenmodus seit 0.22.0, nur ein Band), kleiner eingebauter Katalog
(großer nur mit ASTAP-`deep_sky.csv`), Dockerfile (ASTAP-Download, SkyTrace-Engine per `SKYTRACE=1`) noch nicht real gebaut/getestet, kurze Spuren (< ~120 px) nur per Clipping,
GraXpert/StarNet nicht im Docker-Image, Verzerrungskorrektur nur Polynom 2. Ordnung (Messung an echten Stellina-Daten
offen), GPU nur optional per OpenCL für Registrierung/Stacking (`gpu.py`; Entwicklung, Docker und die SkyTrace-Engine rechnen auf der CPU),
waagerechte Hintergrundstreifen bei NGC 1579 (Stack).
