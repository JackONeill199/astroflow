# Installation, Komponenten, Infobereich, Paket

> Ausgelagert aus `AGENTS.md` am 09.11.2026, Text unverändert übernommen. Regeln, die beim Ändern von Code gelten,
> stehen zusätzlich in Kurzform in `AGENTS.md` → „Wenn du … änderst“. Neue Befunde hier ergänzen, nicht in `AGENTS.md`.

## Installation und Einrichtung (`core/launcher.py`, `core/setup.py`, `installer/`; seit 0.33.0 – Voraussetzung fürs öffentliche Repo)
- **Zwei Wege, getrennt:** Bestehende Instanzen (`serve --data`, `ASTROFLOW_DATA`, `scripts/windows/Start.bat` mit `./data`, Port 8080/8766) bleiben unverändert.
  **Heimnetz bei `serve`** (seit 0.46.0): ohne `--host` gilt `127.0.0.1`, außer `--lan` (Start.bat: `ASTROFLOW_LAN=1`) oder
  `settings.json` → `lan.enabled` (Einstellungen → Heimnetz, wirkt nach dem nächsten Start). Fehlt die Einstellung, setzt
  `security.lan_enabled` sie einmalig: bestehende Instanz (`astroflow.db` vorhanden – bis 0.45 lauschte `serve` immer auf 0.0.0.0)
  → an + `migrated` (Hinweis in Einstellungen → Heimnetz), neue Instanz → aus. `serve` setzt `ASTROFLOW_BIND` (→ `lan_active`).
  Docker: CMD `--host 0.0.0.0`, Compose veröffentlicht nur `127.0.0.1:8080` und setzt `ASTROFLOW_TRUSTED_NETS` für die Bridge.
  Heimnetz unter Docker nur mit `network_mode: host` (Linux) ohne `TRUSTED_NETS` – mit "8080:8080" kämen je nach Docker-Variante
  auch Heimnetz-Geräte von der Bridge-Adresse (dann keine Unterscheidung lokal/fremd; ohne `TRUSTED_NETS` könnte der Host nicht koppeln).
  Neu ist der **Starter** `astroflow` / `astroflow start` (Standard ohne Befehl): Port 8766, `127.0.0.1` (Heimnetz erst nach Einschalten im
  Assistenten bzw. Einstellungen → Heimnetz → `config.lan`, sonst fragte die Windows-Firewall beim ersten Start; Geräte dann koppeln, s. `AGENTS.md` → „Sicherheit“), Datenordner `--data` > `ASTROFLOW_DATA` > `config.json` →
  `data_dir` > `%USERPROFILE%\AstroFlow` bzw. `~/AstroFlow` (**nicht** „Dokumente“ – OneDrive, Entscheidung des Masters 06.11.2026).
  `config.json` liegt außerhalb des Datenordners (`%APPDATA%\AstroFlow`, `~/.config/astroflow`; portabel `ASTROFLOW_HOME` → `<App>\config.json`,
  Daten `<App>\Daten`). Läuft schon ein AstroFlow auf dem Port (`/api/status`) → nur Browser; fremdes Programm → Meldung (MessageBox ohne Konsole).
  Server als losgelöster `pythonw -I -X utf8 -m astroflow serve` mit `ASTROFLOW_LAUNCHER=1` (nur dann ist der Datenordner wählbar und der
  Assistent erscheint von selbst), Protokoll `server.log[.err]` im Datenordner. `astroflow stop` = `POST /api/system/shutdown` (SIGINT → Module
  stoppen sauber). Neustart (Datenordner/Heimnetz geändert, `POST /api/system/restart`): neuer Starter mit `--wait-pid`, alter Server beendet sich.
  `ASTROFLOW_NO_BROWSER=1` unterdrückt den Browser.
- **Assistent** `#/willkommen` (`static/setup.js|css`, Präfix `su-`): erscheint, wenn `/api/app` → `setup.pending` (Starter + `settings.json` →
  `setup.done` nicht gesetzt) und die Startseite aufgerufen wird; sonst über Einstellungen → Module & System (`setupSystemPanel`: Updates,
  Neu starten, Beenden). Schritte: Datenordner (Laufwerke + frei, `launcher.cloud_folder` warnt bei OneDrive/Dropbox/…; Wechsel legt im neuen
  Ordner `setup` an und startet neu – **alte Daten werden nicht verschoben**, DB-Pfade sind absolut), Teleskop (→ D05 bzw. D50 für „andere“),
  Standort (Link), ASTAP, Demo, Extras (GPU per pip, GraXpert/StarNet nur Links, Heimnetz + Firewall-Regel wie `scripts/windows/Netzwerk-freigeben.bat` per UAC,
  Update-Prüfung). Hintergrundaufgaben `Setup._run` → `tasks` + Aktivität (`setup:<key>`).
- **Komponenten** (`core/components.py`, seit 0.44.0, Nutzerwunsch „vollständiger Installer, auch GraXpert“): `astap` (Option d05/d50),
  `graxpert`, `gpu`, `skytrace`, `starnet` (nur Zustand + Link). `status()` → Zustand installed|partial|missing|unsupported, Version,
  Lizenz/Quelle; `install(name, prog, option, force)` (force = „Reparieren“), `remove()` nur für Selbstinstalliertes laut
  `<Werkzeuge>/komponenten.json` (Werkzeuge = `%LOCALAPPDATA%\AstroFlow\Werkzeuge`, portabel `<App>\Werkzeuge`). API `GET /api/components`,
  `POST /api/components/install {id, option, force}`, `…/remove {id}`; UI `componentsPanel` in `setup.js` (Einstellungen → Module & System,
  Assistent → Extras ohne ASTAP). CLI `astroflow components status|install NAME[:OPT]|remove NAME` (Deinstaller).
  - **GraXpert** 3.0.2 (letzte stabile, 3.1 nur rc): MSI von GitHub, SHA-256 fest im Code (GitHub liefert keine), **`msiexec /a`** =
    Admin-Abbild nach `<Werkzeuge>/GraXpert` – ohne Adminrechte, ohne Registrierung (18 s, 413 MB). **Ein normal installiertes GraXpert
    hat Vorrang** (`graxpert.system_graxpert` vor `own_graxpert`; Entscheidung des Masters) und wird nie angefasst: Zustand „eigene
    Installation“, keine Knöpfe, `install` lädt dann nichts. Nur Windows (sonst „unsupported“ + Hinweis). GPL-3 + Quellcode-Link in UI/Installer.
  - **SkyTrace-Engine + GPU per pip** (kein zweiter Interpreter; `Module.python` fällt ohnehin auf `sys.executable` zurück).
    Im Windows-Paket mit `--target` in den **eigenen Paketordner** `%LOCALAPPDATA%\AstroFlow\Pakete\cp312-win_amd64` (portabel
    `<App>\Pakete\…`): `installer/astroflow_pakete.py` + `astroflow-pakete.pth` im mitgelieferten site-packages hängen ihn bei jedem
    Start an `sys.path` (hinten – das Bündel hat Vorrang) und per `os.add_dll_directory` für DLLs frei, auch in Engine-Unterprozessen und
    mit `-I`. Update ersetzt `{app}\python`, der Paketordner bleibt; `_install_gpu/_skytrace` prüfen dann nur (Module da + Importtest →
    „nichts neu geladen“). Anderer Python-Minor = anderer Unterordner (ABI) → neu installieren, alte Tags löscht `_drop_old_tags`.
    `_prune_duplicates` entfernt, was pip --target an Abhängigkeiten doppelt ablegt (numpy, pillow … – gleiche Version per Constraints).
    Deinstallation löscht den Paketordner. Ohne Haken (Entwickler-venv, macOS/Linux) `packages_dir()` = None → pip wie gewohnt.
    Paketquellen: erst `torch torchvision` aus dem PyTorch-CPU-Index, dann `modules/skytrace/requirements-engine.txt`
    (plattformneutral; das frühere Windows-Lockfile `requirements.txt` ist seit 0.49.1 entfernt). Immer mit `-c <Werkzeuge>/constraints.txt` = Versionen
    der vom Server geladenen Pakete (numpy, scipy, astropy …) – sonst will pip sie ersetzen, unter Windows sind geladene .pyd gesperrt.
    Nur CPU-PyTorch (GPU-Variante nicht verlässlich ohne Hardwareprüfung). Danach `skytrace.Module.check_engine()`; `gpu._TRIED` zurück.
  - **Prüfsummen** (seit 0.46.0): ASTAP-CLI je System, `astapwin32.zip` (deep_sky.csv), D05/D50 nur mit SHA-256 aus `ASTAP_SHA256`
    (SourceForge hat keine versionierten Dateinamen → geprüfte Fassung vom 07.11.2026; neue ASTAP-Fassung = `ChecksumError` mit Hinweis,
    dann bewusst ergänzen), GraXpert `GRAXPERT_SHA256`, Paketbau `PBS_SHA256` (aus `SHA256SUMS` des Releases). Jede Anfrage der Kette
    (auch Weiterleitungen) nur https (`_https_only`, Ausnahme Loopback für Tests).
  - Downloads (`components.download`) setzen Teil-Downloads per HTTP-Range fort (`.part` im Werkzeugordner); kaputtes ZIP → neu laden.
    Seit 0.45.2 auch innerhalb eines Laufs: bis `DL_TRIES` 6 Versuche (Pause `DL_PAUSE`·2ⁿ, ≤ 30 s) bei Verbindungsabbruch, zu wenig Bytes,
    5xx/429 oder HTML statt Datei; auch Downloads in den Speicher (ASTAP-CLI, Katalog) setzen per Range fort, `iter_raw` + `Accept-Encoding: identity`
    (sonst gingen bis 1 MB gepufferte Bytes verloren). Anlass: Tag-Lauf v0.44.0, SourceForge trennte nach 1,4 von 5,8 MB. Test `test_download.py`.
  - **Installer-Auftrag:** Inno schreibt die Auswahl nach `%APPDATA%\AstroFlow\komponenten-auftrag.txt` (eine je Zeile, `astap:d05` …);
    `Setup.run_order` (Thread beim Start, nur mit Starter, `ASTROFLOW_COMPONENTS_AUTO=0` aus) übernimmt sie in `config.json` →
    `components_pending`, arbeitet in der Reihenfolge astap, graxpert, gpu, skytrace, **demo zuletzt** (erstes Bild schon mit GraXpert,
    Spurenanalyse läuft automatisch mit) ab und streicht nur Erfolge – Fehlschläge (kein Netz) bleiben vorgemerkt („vom Installer vorgemerkt“).
  - **Microsoft-C++-Laufzeit:** PyTorch braucht `msvcp140.dll` u. a., auf frischem Windows fehlt sie (Sandbox 07.11.2026: `c10.dll`
    ließ sich nicht laden, Analyse brach nach 1 s ab; python-build-standalone bringt nur `vcruntime140*.dll`). Deshalb steht
    `msvc-runtime` (PyPI, nur mit der Engine geladen, nie mitgeliefert; Lizenzangabe „Microsoft Visual C++ Runtime (weitergebbar laut
    Microsoft)“) in `requirements-engine.txt`. pip --target übernimmt Datendateien nicht → `_install_vc_runtime` legt die DLLs aus dem Wheel
    in den Paketordner. `vc_runtime_ok()` prüft sie im Zustand („unvollständig“ → Reparieren) und in `skytrace.check_engine`
    (`health.error` = `VC_MISSING`, Automatik meldet das als Fehler statt still zu überspringen); nach der Installation Importtest
    `import torch, cv2, ultralytics` im eigenen Prozess (`c10.dll`/WinError 126 → dieselbe klare Meldung).
- **Symbol im Infobereich** (`core/tray.py`, seit 0.44.0, Nutzerwunsch; App-Fenster ohne Adressleiste ausdrücklich nicht gewünscht):
  eigener leichter Prozess `pythonw -m astroflow tray --port P` (nur HTTP zum Server, kein Modulimport), `launcher.start` stößt ihn
  nach dem Serverstart an (`tray.ensure`), je Benutzer einmal (`tray.pid` neben config.json). pystray + Pillow (in `requirements.txt`);
  ohne pystray/Infobereich bzw. `ASTROFLOW_NO_TRAY=1` kein Symbol. Abfrage alle 5 s `/api/status` (+ `/api/activity`): Symbol mit
  orangem Punkt bei `busy`, Tooltip ≤ 120 Zeichen, Benachrichtigung bei fertigem Bilder-Auftrag (`system.tray_notify`, auch im Menü).
  „Beim Anmelden starten“ = Registry `HKCU\…\Run\AstroFlow` (erkennt auch die Autostart-Verknüpfung der Installer-Aufgabe).
  Beenden: Menü → Rückfrage bei laufendem Auftrag → `/api/system/shutdown`; Shutdown aus der Oberfläche und `astroflow stop` legen
  `tray.stop` ab (das Symbol endet sauber beim nächsten Abfragen, ohne Geistersymbol), `stop` beendet es notfalls hart (Deinstallation:
  sonst bliebe python gesperrt). Server länger als `GONE_S` 40 s weg → Symbol endet selbst; Neustart lässt es stehen.
- **ASTAP auf Knopfdruck:** nur von SourceForge „astap-program“ (CLI je OS, `astapwin32.zip` nur für `deep_sky.csv` – laut Danksagung
  **nur nicht-kommerziell frei**, Lizenz der Sterndatenbanken nicht eindeutig → nie mitliefern), nach `%LOCALAPPDATA%\astap` bzw. `~/astap`
  (sucht `platesolve`/`objects` ohnehin dort), vorhandene Dateien bleiben. D05 als ZIP (~102 MB), D50 ~900 MB.
- **Update-Hinweis:** `Setup.update_loop` (20 s nach Start, dann stündlich prüfen, ob 24 h um) → GitHub `releases/latest`, anonym,
  `system.update_check` (Standard an), jeder Fehler still (privates Repo = 404). `/api/app.update` nur wenn neuer; Frontend-Toast einmal je Version
  (`localStorage af.update.seen`). `ASTROFLOW_UPDATE_CHECK=0` setzt `conftest.py`.
- **Paket:** `pyproject.toml` (setuptools ≥ 77, alle Dateien unter `astroflow/` als Paketdaten, Abhängigkeiten = `requirements.txt`, Test prüft
  Gleichheit; noch nicht auf PyPI). `installer/build_windows.py`: python-build-standalone (fest `PBS_TAG`/`PBS_PY`) + Wheel + pip-Install,
  numpy/scipy-Tests entfernt, Symbol gezeichnet (Pillow), `AstroFlow.cmd`/`AstroFlow beenden.cmd` (portabel), ZIP; mit ISCC Inno-Installer
  (`astroflow.iss`: pro Nutzer, `%LOCALAPPDATA%\Programs\AstroFlow`, Startmenü, optional Desktop/Autostart, stoppt laufenden Server vor Update/
  Deinstallation, Daten bleiben; seit 0.44.0 Typen „Vollständig“/„Nur AstroFlow“/benutzerdefiniert und Komponentenseite mit
  `ExtraDiskSpaceRequired` als Größenangabe, StarNet-/GPL-Hinweis im Seitentext; Update löscht `{app}\python` vorab (`[InstallDelete]`,
  keine Reste alter Paketstände) – GPU/SkyTrace kommen über den Auftrag neu, aus dem pip-Zwischenspeicher; Deinstallation fragt, ob
  GraXpert/ASTAP mit weg sollen, still `/WERKZEUGE=entfernen`). Daneben `AstroFlow-Test.wsb` + `pruefe-in-sandbox.cmd` (Windows-Sandbox, absoluter Pfad → nicht im Repo;
  für heruntergeladene Pakete `build_windows.py --sandbox-only --out <Ordner>`): Paketordner schreibgeschützt, daneben `<Ordner>-sandbox-ergebnis`
  beschreibbar, Netz an, sonst nichts eingebunden; Logonbefehl prüft Installer (`pruefe_paket.py --setup`, nur in Sandbox/VM – installiert ins
  echte Profil) und ZIP, Protokolle + `fertig.txt` im Ergebnisordner. Start per Doppelklick oder `WindowsSandbox.exe <wsb>` (geht auch aus der Sitzung).
  CI: `.github/workflows/windows-paket.yml` läuft **nur manuell** (seit 09.11.2026, Nutzerwunsch: kein Bau nach jedem Fix/Tag); auf einem Tag `v*` gestartet → Release-Entwurf. Baut, prüft sauber und installiert still. Nach Änderungen an `installer/`, Launcher, Setup oder Komponenten den Lauf vor einem Release selbst anstoßen.
  Rechte: Workflow `contents: read`; nur der Job `release` (Tags `v*`, lädt das Artefakt des Bau-Jobs) hat `contents: write`.
- **Gemessen 06.11.2026** (`installer/pruefe_paket.py`, leeres Profil, PATH ohne Python, 16 Kerne): Paket 150 MB ZIP / 379 MB entpackt;
  entpacken 8 s, erster Start 21 s, ASTAP + D05 12 s (schnelle Leitung), Demo bis Bild 11 s, Beenden 4 s → **0,9 min bis zum ersten Bild**.
  **Windows-Sandbox** (frisches Windows ohne Python, CI-Pakete): Installer 96 MB – installieren 18 s, Start 9 s, ASTAP + D05 20 s, Demo bis Bild 9 s
  → **0,93 min**, Beenden 3 s, Deinstallieren 3 s (Daten bleiben); portabel 0,58 min. CI-Runner: Installer 0,84 min, portabel 3,6 min (ASTAP 177 s).
  `platform.machine()` ist unter Windows ohne `PROCESSOR_ARCHITECTURE` leer → `components.system()` rät über die Zeigerbreite.
  **Vollinstallation 0.44.0** (Sandbox 07.11.2026, `pruefe_paket.py --setup … --voll`, alle Komponenten): installieren 16 s, Start 9 s,
  Symbol im Infobereich läuft; Komponenten im Hintergrund 334 s (ASTAP + D05 131 s, GraXpert 144 s, GPU 3 s – Sandbox reicht die
  Grafikkarte durch –, SkyTrace-Engine 50 s); **erstes Bild mit GraXpert nach 6,7 min** ab Installerstart, SkyTrace-Lauf auf den
  Demodaten 15 s danach (18 Aufnahmen, 1 Kandidat); Beenden 7 s ohne übrige Prozesse, Deinstallation samt Werkzeugen 7 s, Daten bleiben.
  Mit Paketordner (Commit ed5088b): erstes Bild 7,0 min; **Update** (gleicher Installer drüber) 19 s + 28 s Prüfen der Komponenten,
  Engine und GPU „nichts neu geladen“ (Paketordner 1,4 GB bleibt); Deinstallation entfernt Paketordner und Werkzeuge.
  Mit einem pip-Lauf + CPU-Index (66fac85): Engine 81 s, erstes Bild 7,4 min, Paketordner 1,1 GB (Doppel entfernt), Update 21 s + 28 s;
  Infobereich-Menü per Prüfskript durchgespielt (Öffnen, Aufträge, Anhalten/Fortsetzen, Autostart an/aus, Beenden 2 s, keine
  übrigen Prozesse); portabler Autostart-Eintrag mit `--home` startet ohne ASTROFLOW_HOME in den portablen Daten.
- Ohne eingerichtete SkyTrace-Engine (kein Interpreter gewählt, keine venv) überspringt `skytrace.auto` still (Zustand `skipped`) statt je Nacht
  „Analyse-Engine unvollständig“ zu melden. Engine per Knopf seit 0.44.0 (Komponente `skytrace`).

## Linux (seit Linux-Paket, 12.11.2026 – Freigabe des Masters/Nutzers)
- **Paketform:** `AstroFlow-<Version>-Linux-x86_64.tar.gz` aus `installer/build_linux.py` (python-build-standalone Linux-gnu mit fester
  `PBS_SHA256`, gleiche `PBS_TAG`/`PBS_PY` wie Windows, gemeinsame Teile aus `build_windows.py`: `fetch`, `make_icon` (PNG), `collect_licenses`).
  Inhalt: `python/`, Starter `AstroFlow`/`AstroFlow-beenden` (portabel, `ASTROFLOW_HOME` = Paketordner), `install.sh`/`uninstall.sh` →
  `linux_install.py` (mit dem mitgelieferten Python, `-I`), `astroflow.png`, Lizenzen, `LIESMICH.txt`. Skripte werden beim Bau mit LF und 0755
  geschrieben. pip-Skripte in `python/bin` (absoluter Bau-Pfad im `#!`) werden entfernt. Bewusst **kein** AppImage (libfuse2 fehlt ab Ubuntu
  22.04), kein .deb/.rpm (root, je Distro), kein Flatpak (Sandbox vs. freie Datenordner/ASTAP/GraXpert). Rückfall: `pipx`/`uv tool install` aus
  GitHub, Docker. aarch64 (Raspberry Pi 5) erst nach dem öffentlichen Repo (ARM-Runner).
- **Installation pro Nutzer, ohne root** (`linux_install.py`): Programm `~/.local/share/astroflow/app` (Update: über `.neu`/`.alt` ersetzt,
  laufender Server vorher per `astroflow stop` beendet; Reste portabler Nutzung `Daten/`, `Pakete/`, `Werkzeuge/`, `config.json` werden nicht
  mitkopiert), Befehle `~/.local/bin/astroflow` (sh → `python3 -I -m astroflow`) und `astroflow-deinstallieren`, Menüeintrag
  `~/.local/share/applications/astroflow.desktop` mit Aktion „AstroFlow beenden“, Symbol `~/.local/share/icons/hicolor/256x256/apps/`.
  `--autostart` → `astroflow autostart on`. Deinstallation entfernt nur einen `astroflow`-Befehl, der „AstroFlow“ enthält; Daten bleiben,
  `--werkzeuge` entfernt per `components remove` ASTAP/GraXpert (nur Selbstinstalliertes) und `packages`/`tools`.
- **Pfade:** Daten `~/AstroFlow`, Einstellungen `~/.config/astroflow`, Werkzeuge `~/.local/share/astroflow/tools`, ASTAP `~/astap`,
  Paketordner `~/.local/share/astroflow/packages/cp312-linux_x86_64` (`astroflow_pakete.py`, Tag per `_platform_tag`).
- **Autostart:** XDG `~/.config/autostart/astroflow.desktop` (`tray.set_autostart`, `Exec=` nach Desktop Entry Specification maskiert,
  `tray._desktop_quote`), auch CLI `astroflow autostart on|off|status`. Infobereich per pystray nur, wo die Arbeitsfläche ihn anbietet.
- **Meldungen ohne Konsole:** `launcher.message` nutzt unter Linux zenity, kdialog oder notify-send.
- **Datenträger im Assistenten:** zusätzlich Einhängepunkte unter `/media/<Nutzer>`, `/run/media/<Nutzer>`, `/mnt` (`setup._mounts`).
- **GraXpert unter Linux x86_64:** offizielles `graxpert-linux-amd64.zip` (~375 MB, entpackt ~1,1 GB inkl. CUDA-Bibliothek), feste
  `GRAXPERT_LINUX_SHA256`, fortsetzbarer Download, sicheres Entpacken ohne den Wurzelordner `GraXpert-linux/` (`components._extract_tree`:
  keine Pfade/Verweise nach außen, Ausführrechte aus dem ZIP), Programm `<Werkzeuge>/GraXpert/GraXpert`. Normal installiertes GraXpert hat
  Vorrang wie unter Windows. macOS und ARM: „unsupported“ + Hinweis.
- **Heimnetz-Firewall** (`core/firewall.py`, Nutzerentscheidung 12.11.2026): nur auf Klick und nur am Rechner selbst (`/setup/firewall`
  mit `require_local`, UI `data-local-only`, Einstellungen → Heimnetz „Firewall freigeben“/„Freigabe entfernen“). Erkennung ohne root:
  `firewall-cmd --state` bzw. `/etc/ufw/ufw.conf` `ENABLED=yes`. Feste Befehle, einzige Werte: Port (int 1–65535) und das lokale Subnetz aus
  `ip -o -4 addr` (nur RFC 1918, Schnittstelle der Hauptadresse zuerst). ufw: `allow from <Netz> to any port <P> proto tcp comment 'AstroFlow LAN'`
  (Entfernen `delete allow …`); firewalld: Rich-Rule `--permanent` + `--reload` in einem `pkexec /bin/sh -c` (ein Passwortdialog). Ergebnis als
  Einrichtungsauftrag „Firewall-Freigabe“ in der Aktivität, feste deutsche Meldungen. Ohne pkexec oder grafische Sitzung: Befehl mit `sudo` nur
  anzeigen. Keine aktive Firewall: Hinweis, nichts zu tun. Tests nur mit Attrappen (`tests/test_linux.py`).
- **Engine/GPU-Hinweise:** fehlt libGL/libglib (OpenCV), meldet die Engine-Prüfung `LIBGL_MISSING` mit Paketnamen; ohne OpenCL-Treiber nennt
  der GPU-Zustand die passenden Pakete.
- **Selbstprüfung** `installer/pruefe_paket_linux.py` (nur Standardbibliothek, nutzt `call/flow/wait` aus `pruefe_paket.py`): leeres HOME/XDG,
  PATH `/usr/bin:/bin`, ohne Browser/Infobereich; `install.sh --no-start --autostart` → Dateien + `desktop-file-validate` → `astroflow start`
  → Assistent/Demo (optional `--astap`) → Server nutzt das Paket-Python (über `/proc`) → `stop` → Update (Daten bleiben) →
  `astroflow-deinstallieren` → portabel (`./AstroFlow`, Daten in `AstroFlow/Daten`).
- **CI** `.github/workflows/linux-paket.yml`: nur manuell; Bau + Prüfung (mit ASTAP-Download) auf ubuntu-22.04, Prüfung ohne Download in
  `debian:12-slim` und `fedora:41`; auf einem Tag `v*` hängt der Job `release` (einziger mit `contents: write`) Paket + `.sha256` an den
  **vorhandenen** Release-Entwurf des Tags an (`--clobber`) bzw. legt ihn an. Der Windows-Workflow macht es seit demselben Stand genauso –
  die beiden Läufe nacheinander starten (gleichzeitig könnten zwei Entwürfe entstehen). Veröffentlichte Releases werden nie verändert.
- **Docker:** ASTAP-CLI und Sterndatenbank (jetzt das ZIP statt .deb) werden im `Dockerfile` gegen dieselben SHA-256 wie
  `components.ASTAP_SHA256` geprüft (`tests/test_linux.py::test_dockerfile_prueft_astap`).
