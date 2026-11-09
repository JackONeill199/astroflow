# Architektur, Sicherheit und Tests – Langfassung

> Ausgelagert aus `AGENTS.md` am 09.11.2026, Text unverändert übernommen. Regeln, die beim Ändern von Code gelten,
> stehen zusätzlich in Kurzform in `AGENTS.md` → „Wenn du … änderst“. Neue Befunde hier ergänzen, nicht in `AGENTS.md`.

Die verbindliche Kurzfassung steht in `AGENTS.md`. Hier der vollständige alte Text mit Dienstliste,
Sicherheitsbegründungen (CodeQL) und Testhistorie.

## Produkt-Architektur (Kern + Module)
```
astroflow/server.py        Kompositionswurzel: lädt Module, /api/app|status|activity|modules, Herkunftsschutz, statische Dateien
astroflow/core/            config.py (Settings), store.py (data/settings.json, ein Abschnitt je Thema), modules.py (BaseModule, Context, Registry), sites.py (Ortsliste)
astroflow/modules/<id>/    __init__.py (class Module), api.py (Router → /api/<id>), static/ (→ /m/<id>/), AGENTS.md
astroflow/static/          Shell: index.html, app.js (Router, Navigation, Übersicht, Aktivität, Einstellungen, Palette, Viewer, Helfer), style.css
```
- **Modul-Vertrag Backend** (`core/modules.py`): `router()`, `start()/stop()`, `status()` (klein, wird alle 2,5 s gepollt; `busy_jobs` zählt für die Anzeige),
  `activity()` (gemeinsames Format `{key,title,kind,detail,state,progress,started,finished,href}`), `scripts/styles`. Reihenfolge/Liste: `AVAILABLE`.
- **Module reden nicht per Import miteinander**, sondern über Dienste in `ctx.services`: Stacking stellt `plate_solver` (`PictureSolver`),
  `project_picture`, `project_solution`, `project_frames`, `observation_log`, `catalog_resolve`, `catalog_objects` und `site_sky` (Himmelsqualität je Ort) bereit, Stacking außerdem `night_zero_points` (Gaia-Nullpunkte je Nacht/Gerät, für SkyTrace-Satellitenhelligkeit); Sage `object_facts` (Typ/Entfernung aus der Wissensbasis) und `collection_progress` (entdeckte Messier-Nummern, für „Heute Nacht“); Sage und SkyTrace nutzen sie. Der Kern selbst stellt `sites` (`SiteBook`), `site_for` und die Liste `night_sources` bereit (s. `standorte-himmel.md`). Fehlt ein Dienst, muss das Modul trotzdem laufen.
- **Ereignisse** (`ctx.on` / `ctx.emit`, Liste in `core/modules.py`): `stacking.stacked(project_id, result_id, name)` kommt aus
  `Pipeline.process` nach dem Entwickeln und **vor** dem Löschen hochgeladener Rohbilder (`Pipeline.on_stacked`). Empfänger (seit 28.10.2026,
  Nutzerwunsch „gestackte Aufnahmen automatisch durch SkyTrace und in die Entdeckungen, mit animierten Erfolgen“):
  SkyTrace `auto.AutoAnalysis` (Hardlinks je Nacht → Warteschlange, Einstellung `skytrace.auto`) und Sage `Module._stacked`
  (Bild bestimmen → Entdeckungen, Einstellungen `sage.auto_discover`/`auto_story`). Empfänger kehren sofort zurück (eigene Threads).
- **Modul-Vertrag Frontend** (`AF.register(id, {nav, routes, summary, recent, discoveries, homeFeatured, settings, palette, status, drop, sitesPanel})`, Kommentar in `app.js`; `sitesPanel(host)` = eigener Abschnitt unter Einstellungen → Standorte;
  `nav[].area` 'work' = Arbeitsbereich, 'library' = Bibliothek, sonst Unterpunkt (optional `parent`, 'home' = unter der Übersicht);
  Kontextpanel rechts per `AF.ctx.open(el, {title, html|render})`, wird beim Ansichtswechsel geleert; Prozessanimationen `vizProcess(host, {kind, …})` aus `static/viz.js`): Helfer (`api`, `send`, `toast`,
  `createViewer`, `folderDialog(start, pick, {title, ok, hint})` (Ordner auf dem Server-Rechner wählen, über `/api/folders` → `core/folders.py`: Laufwerke, Unterordner, FITS-/Bildzählung; genutzt von Hauptordner und SkyTrace; Suchfeld seit 0.31.0: sofort im aktuellen Ordner, entprellt 300 ms + abbrechbar `/api/folders/search?path=&q=` = Breitensuche Tiefe ≤ 4, ≤ 200 Treffer, ≤ 300 000 Einträge/5 s – je Eintrag geprüft, auch mitten in großen Ordnern, Schlüssel `folders.norm`/`folderKey` ohne Leerzeichen/„_“/„-“; bewusst keine Mehrfachauswahl, beide Aufrufer nehmen genau einen Ordner), `picker` (Dropdown mit Suche für Modelle/Stimmen, Listen aus `core/models.list_models`), `jobLine`, `confirmDlg`, `crumbs`, `store` …) sind global. Modul-Skripte lädt die Shell aus `/api/app` mit `?v=<build>`
  (Build-Kennung aus Dateizeiten) – nur `index.html` braucht weiter das manuelle `?v=`.
- Module ein/aus: Einstellungen → Module & System bzw. `ASTROFLOW_MODULES=stacking,skytrace`; ein defektes Modul erscheint mit Fehler, die App läuft weiter.
- **Sicherheit** (`core/security.py`, Middleware `guard` in `server.py`, seit 0.46.0; Befunde eines Reviews vor der Veröffentlichung):
  (1) **Host-Prüfung:** `Host` muss `localhost`, eine IP-Adresse (eine über eine IP geladene Seite gehört zu diesem Rechner), der
  Rechnername (auch `.local`, `.fritz.box`, `.home`, `.lan`, FQDN) oder ein Name aus `ASTROFLOW_HOSTS`/`settings.json` → `lan.hosts` sein, sonst 400.
  (2) **Herkunft:** schreibende Methoden brauchen `Origin` (Rückfall `Referer`) mit derselben Adresse wie `Host` (sonst 403) – auch
  eigene Hilfsprogramme setzen ihn (`tray.py`, `launcher.stop`, `installer/pruefe_paket.py`).
  (3) **Kopplung:** Clients, die nicht Loopback sind (`request.client.host`; Docker: `ASTROFLOW_TRUSTED_NETS`), brauchen das Cookie
  `af_geraet` (HttpOnly, SameSite=Strict, 10 Jahre). Es kommt aus `GET /api/pair?code=` (Code aus Einstellungen → Heimnetz, QR-Code
  per `core/qr.py` – eigener Encoder, Byte-Modus/Stufe M, ohne Fremdpaket; Code 15 min, einmalig, danach neuer). Gespeichert wird nur
  der SHA-256 (`settings.json` → `pairing.devices`). Sonst 401: JSON `{pair: true}` unter `/api`, sonst HTML-Seite „Gerät koppeln“.
  `GET/POST /api/pairing[/code|/remove]` nur lokal. Frontend `static/pairing.js` (Abschnitt `heimnetz` in `settingsView`).
  (4) **Nur am Rechner selbst** (`security.require_local(request, was)` → 403, auch mit Token): Komponenten installieren/entfernen,
  `/setup/astap|gpu|firewall`, `/setup {lan|data_dir}`, `/system/shutdown|restart`, StarNet-Pfad/-ZIP/-Entfernen, GraXpert-, ASTAP- und
  SkyTrace-Pfade (nur bei **geänderten** Werten – Formulare schicken die Felder mit). Neue gefährliche Endpunkte genauso schützen.
  UI: `data-local-only` an Knöpfen/Feldern/Formularen – `app.js` (`markLocalOnly`, MutationObserver) graut sie aus, wenn
  `/api/app` → `local: false`. (5) **GET ohne Seiteneffekte mit Geheimnissen:** Modelllisten (`/stacking/reviewer/models`,
  `/sage/models`) sind POST; gespeicherte Schlüssel nur an die gespeicherte bzw. feste Anbieteradresse (`core/models.key_for`).
  (6) **Pfade vom Client** (CodeQL `py/path-injection`, abgearbeitet 09.11.2026): Ordner *auflisten* (nur Namen + Bildzählung) ist für
  gekoppelte Geräte gewollt – `/api/folders[/search]` (Ordnerdialog) und `/api/skytrace/observations`; ebenso eine Analyse/Reanalyse aus
  einem gewählten Ordner starten. Dateien *ausliefern* nur unterhalb fester Wurzeln (`skytrace.within`, Ergebnis-/Ausgabeordner aus der DB).
  Programm- und Datenordnerpfade nur mit `require_local`. Keine neuen Endpunkte, die beliebige Pfade prüfen/lesen, ohne einen dieser Wege
  (`/api/setup/folder` und `/api/skytrace/folders` waren ungenutzt und sind entfernt). **Entscheidung des Nutzers 09.11.2026:** gekoppelte
  Geräte dürfen weiter Ordner auflisten und Analysen aus jedem Ordner starten – bewusst ohne `require_local`; die CodeQL-Meldungen
  `py/path-injection` an diesen Stellen (`core/folders.py`, `/api/skytrace/observations`, `skytrace/reanalysis.py`) gelten deshalb als
  Fehlalarm, ebenso die hinter `require_local` bzw. `within` (Begründung je Meldung in PR #17).
  (7) **Keine Ausnahmetexte an den Browser** (`py/stack-trace-exposure`): Antworten nennen feste deutsche Meldungen, Details per
  `traceback.print_exc()` ins Protokoll (Beispiele `picture_import.user_message`, `stacking/api.ki_error`). Regex auf Nutzer-/KI-Text
  ohne mehrdeutige Wiederholungen bzw. mit begrenzter Länge (`sage/store.plain`, `sage/kb.norm`); im Frontend alles über `esc()`.
  Tests: `tests/test_security.py`; `conftest.py` lässt den TestClient standardmäßig als 127.0.0.1 mit eigener `Origin` laufen.
  Antworten ab 2 KB werden per `core/gzip.py` komprimiert (seit 0.31.1, Handy im Heimnetz; eigene Middleware statt Starlettes `GZipMiddleware`, deren Ausschlussliste ältere erlaubte Versionen nicht kennen). Nur Text/JSON/JS/SVG mit Status 200, nie Downloads, Medien oder 206. Große JSON-Antworten trotzdem klein halten, siehe SkyTrace „Laufdaten fürs Handy“. Schlüssel stehen im Klartext in `data/settings.json`.
- **Neues Modul:** Unterpaket mit `Module(BaseModule)` anlegen, in `AVAILABLE` eintragen, `static/<id>.js` mit `AF.register`, Tests in `tests/`.

## Tests (`tests/`)
**CI** (seit 0.45.2, `.github/workflows/tests.yml`): volle Suite bei jedem PR/Push auf `main`, Ubuntu + Windows, Python 3.12, Playwright-Chromium, dazu skyfield/sgp4/OpenCV headless/photutils/PyAV (SkyTrace-Tests ohne PyTorch). Pflichtprüfungen für `main`: `pytest (ubuntu-latest)`, `pytest (windows-latest)` (Ruleset `.github/rulesets/main-schutz.json`, Veröffentlichung: `docs/PUBLIC-RELEASE.md`). Neue Tests müssen auch unter Linux laufen oder sauber überspringen.
**Parallel (seit 04.11.2026):** `pytest.ini` setzt `-n auto --maxprocesses=8 --dist load` (höchstens 8 Worker; voller Lauf ~82 s, zusätzlicher RAM-Bedarf gemessen ~3,3 GB) (pytest-xdist, `requirements-dev.txt` – dort auch Playwright). Seriell 5,5 min → parallel ~1,2 min,
gleiche 240 bestanden / 4 übersprungen. Regeln für neue Tests: nur `tmp_path`/`tmp_path_factory` für Dateien (nie feste Pfade oder die Arbeitsinstanz), Server auf Port 0/`_free_port()`,
Umgebungsvariablen nur per `monkeypatch`, keine Abhängigkeit von der Reihenfolge oder anderen Tests. `conftest.py` begrenzt unter xdist BLAS-/Pipeline-Threads auf 2 je Worker
(`ASTROFLOW_THREADS`), sonst bremsen sich viele Worker gegenseitig. `conftest.py` setzt `ASTROFLOW_GRAXPERT/GPU/GAIA=0` schon beim Import (vor allen Modul-/Session-Fixtures, von Unterprozessen geerbt) und verbietet per `subprocess.Popen`-Schutz echte `graxpert*`/`starnet*`-Programme außerhalb des Temp-Ordners (`test_no_real_tools.py`) – am 04.11.2026 starteten Browser-Fixtures ein installiertes GraXpert dutzendfach und ließen den Rechner abstürzen. **Netzsperre** (seit 0.45.4, `conftest._block_network`): httpx-Anfragen an andere Hosts als localhost/`testserver` scheitern sofort mit `ConnectError` – Attrappen per monkeypatch statt echtem Netz. Browser-Fixtures `server`/`browser` stehen in `conftest.py` (Session-Scope = je Worker einmal); alle Browser-Tests
tragen automatisch den Marker `ui`. **Sitzungen:** vor dem PR den vollen Lauf; beim Entwickeln `-m "not ui"` bzw. nur die betroffene Datei.
Synthetische Daten aus `demo.py` (`generate`, `generate(anonymous=True)` ohne Koordinaten, `generate_mono` mit Filtern).
Plate-Solving-Tests nutzen eine ASTAP-Attrappe (Python-Skript, schreibt echtes `.ini`-Format) über `SolverConfig(cmd_prefix=[...])` – kein echtes ASTAP nötig.
**Keine kopierten/umbenannten Programme starten** (nie `sys.executable` kopieren und als `.exe` ausführen): Virenscanner (Bitdefender, 09.11.2026) werten das als Malware und beenden den ganzen Prozessbaum. Skripte per `[sys.executable, skript.py]` sind ok; ZIP-Inhalte mit Platzhalter-Bytes, Selbstprüfungen über Haken (`components._astap_selftest`) ersetzen.
**Oberfläche** (`test_ui_layout.py`, seit 31.10.2026): echter Chromium über Playwright (in `requirements-dev.txt`, nicht in `requirements.txt`;
fehlt es, wird übersprungen – `pip install playwright && python -m playwright install chromium`). Alle Ansichten mit Demodaten bei 375/790/1440 px:
nichts darf über seinen Kasten (nächster Vorfahr mit Rahmen/Hintergrund) oder die Seite ragen, keine JS-Fehler; Schalterzeilen müssen per Klick umschalten.
Neue Ansichten in `ROUTES` eintragen. Achtung: globales `td { white-space: nowrap }` – Namensspalten in schmalen Tabellen brauchen `white-space: normal`.
