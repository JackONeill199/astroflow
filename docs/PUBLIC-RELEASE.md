# Veröffentlichung des Repos – Checkliste

Was im Repo vorbereitet ist und welche Schritte der Maintainer auf GitHub selbst ausführt. Reihenfolge einhalten:
Einige Funktionen (Rulesets, CodeQL, Pages) sind für private Repos im kostenlosen Plan nicht verfügbar und wirken erst nach
dem Umschalten auf „öffentlich“.

## Im Repo vorbereitet

| Datei | Zweck |
|---|---|
| `.github/workflows/tests.yml` | Volle Testsuite auf Ubuntu + Windows (Python 3.12, Playwright-Chromium), bei jedem PR und Push auf `main`. Statusprüfungen `pytest (ubuntu-latest)` und `pytest (windows-latest)` |
| `.github/workflows/codeql.yml` | CodeQL für Python und JavaScript; läuft nur im öffentlichen Repo |
| `.github/workflows/pages.yml` | Projektseite aus `docs/site/` + `docs/media/`; läuft nur im öffentlichen Repo |
| `.github/workflows/windows-paket.yml` | Nur manuell; auf einem Tag `v*` gestartet: Release-Entwurf mit dem passenden Abschnitt aus `CHANGELOG.md` (`installer/release_notes.py`) |
| `.github/ISSUE_TEMPLATE/` | Formulare „Fehler melden“, „Wunsch äußern“, Verweis auf Discussions und vertrauliche Sicherheitsmeldung |
| `.github/rulesets/main-schutz.json` | Regeln für `main` zum Einspielen per API (s. u.) |
| `SECURITY.md`, `CONTRIBUTING.md` | Meldeweg für Lücken, Regeln für Beiträge |
| `installer/historie_bereinigen.py` | Bereinigte Historie für das neue Repo erzeugen und prüfen (Abschnitt 1); private Begriffe nur aus einer lokalen Datei |

## 1. Historie bereinigen – neues Repo

Entscheidung: Das bestehende Repo bleibt **privat als Archiv**. Ein neues, öffentliches Repo bekommt die vollständige,
bereinigte Historie: nur `main` und die Tags `v*`, Autor-Adressen auf GitHub-noreply und private Begriffe ersetzt.
Ein Force-Push ins bestehende Repo genügt nicht, weil GitHub die `refs/pull/*` alter PRs behält.

Erst ausführen, wenn alle gewünschten PRs gemergt sind. Offene Branches gehen nicht mit, sie lassen sich später
per `git format-patch` übertragen.

**a) Ersetzungsliste anlegen (nur lokal).** Die Datei `historie-ersetzungen.local.txt` kommt in den Repo-Wurzelordner
(`*.local.txt` steht in `.gitignore`). Die Begriffe stehen in `AGENTS.local.md`: Benutzername, Heimnetz,
Rohdaten-Laufwerk, Geräte-Kennung. Format siehe Kopf von `installer/historie_bereinigen.py`:

```text
Pfad Benutzer | regex:(?i)(users(?:\\\\|\\|/)+)<benutzername>\b ==> \1nutzer
Heimnetz      | regex:192\.168\.<netz>\.(\d+) ==> 192.168.1.\1
Laufwerk      | regex:\b<LW>:((?:\\\\|\\|/)+)<Ordner> ==> D:\1Astro
Gerätekennung | <kennung> ==> a1b2c3
```

**b) Umschreiben und prüfen.** Das braucht `pip install git-filter-repo` und `gh auth login` für die noreply-ID.
Der Ordner muss leer oder neu sein:

```bash
python installer/historie_bereinigen.py --ziel ../astroflow-neu
```

Das Skript arbeitet in diesen Schritten:
- Es klont das alte Repo zweimal (`alt.git` zum Vergleich, `neu.git`) und behält in `neu.git` nur `main` und die `v*`-Tags.
- Es ruft `git filter-repo` mit `--mailmap` (jede Adresse außer GitHub-noreply wird zu `<ID>+JackONeill199@users.noreply.github.com`),
  `--replace-text` und `--replace-message` auf.
- Danach prüft es:
  - Es bleiben keine Muster oder alten Adressen übrig, weder in Inhalten noch in Commit- und Tag-Texten oder Autoren.
  - Main hat gleich viele Commits.
  - Jedes Tag zeigt auf den umgeschriebenen Commit.
  - Die Bäume von `main` und allen Tags sind bis auf die Ersetzungen gleich.
- Am Ende steht `ERGEBNIS: in Ordnung`.
- Der Bericht nennt nur die Art („Heimnetz“ …), nie die Begriffe selbst.
- Gepusht wird **nichts**.

**c) Altes Repo umbenennen, neues anlegen, hochladen:**

1. GitHub → altes Repo → Settings → General → Repository name: `astroflow-archiv` (bleibt privat).
2. Neues, **leeres** Repo `JackONeill199/astroflow` anlegen (ohne README/Lizenz, zunächst privat).
3. Hochladen: `git -C ../astroflow-neu/neu.git push --mirror https://github.com/JackONeill199/astroflow.git`
4. Release-Entwürfe neu erzeugen: Der Tag-Push startet den Workflow „Windows-Paket“ für **jedes** Tag. Besser zuerst
   nur `main` pushen (`git push <url> main`), dann ausschließlich das neueste Tag. Ältere Tags danach pushen, wenn der
   Workflow sie nicht bauen soll: Die Tags vor 0.33.0 kennen den Workflow nicht, alle neueren würden bauen.
   Alternativ den Workflow vorher in den Repo-Einstellungen kurz deaktivieren.

**d) Nacharbeiten:**

- **Lokale Klone neu holen:** Der alte Verlauf hat andere Commit-IDs. Bestehende Checkouts und Worktrees zeigen auf das
  Archiv (`git remote -v`). Am einfachsten frisch klonen. `AGENTS.local.md`, `data/` und `.venv` von Hand übernehmen.
- **Adresse:** `git config --global user.email "<ID>+JackONeill199@users.noreply.github.com"`. Mit
  `git config user.email` in jedem Klon prüfen. Ein lokal gesetzter Wert hat Vorrang vor dem globalen.
- **GitHub → Settings → Emails:** „Keep my email addresses private“ und „Block command line pushes that expose my email“.
- **Actions:** Das alte Repo hat keine Secrets oder Variablen (geprüft am 07.11.2026), die Workflows brauchen nur das
  automatische `GITHUB_TOKEN`. Im neuen Repo unter Settings → Actions → General → Workflow permissions „Read and write“
  setzen, sonst kann „Windows-Paket“ keinen Release-Entwurf anlegen.
- **Update-Check der App:** `astroflow/core/setup.py` → `REPO = "JackONeill199/astroflow"` fragt
  `…/releases/latest` ab. Bleibt der Name `astroflow`, finden auch installierte Versionen das neue Repo. Bei einem
  anderen Namen `REPO`, die URLs in `installer/astroflow.iss`, `modules/sage/sources.py`, README, `docs/site/index.html`,
  `SECURITY.md` und den Issue-Vorlagen anpassen. GitHub leitet nach dem Umbenennen des alten Repos **nicht** auf das neue
  weiter, sobald dort ein Repo gleichen Namens existiert.
- **PR-Verweise:** Links auf alte PRs (`…/pull/<n>`) in CHANGELOG oder AGENTS.md führen im neuen Repo ins Leere bzw. auf
  andere Nummern. Sie stehen nur in der Doku und sind unkritisch.

## 2. Sichtbarkeit

Im neuen Repo: Settings → General → Danger Zone → „Change repository visibility“ → Public. Danach
„Automatically delete head branches“ einschalten (Settings → General → Pull Requests).

## 3. Geschützter `main` (Ruleset)

**Per API** (nach der Umstellung, Maintainer-Konto mit `gh auth login`):

```bash
gh api --method POST repos/JackONeill199/astroflow/rulesets --input .github/rulesets/main-schutz.json
```

Prüfen: `gh api repos/JackONeill199/astroflow/rulesets`. Ändern später: `--method PUT …/rulesets/<id>`.

**Oder per Oberfläche:** Settings → Rules → Rulesets → New ruleset → New branch ruleset

1. Name `main-schutz`, Enforcement status **Active**.
2. Target branches → Add target → **Include default branch**.
3. Haken bei **Restrict deletions** und **Block force pushes**.
4. **Require a pull request before merging** – Required approvals 0 (Einzelprojekt; sonst kann der Maintainer eigene PRs nicht mergen).
5. **Require status checks to pass** → Add checks: `pytest (ubuntu-latest)` und `pytest (windows-latest)` (erscheinen in der
   Liste, sobald der Workflow „Tests“ einmal gelaufen ist; Quelle „GitHub Actions“).
6. Bypass list leer lassen. Create.

Hinweis: Mit „PR Pflicht“ gehen Tags weiterhin direkt (`git push origin v<Version>`) – das Ruleset betrifft nur `main`.
Der Workflow „Windows-Paket“ läuft nur bei Änderungen am Paket und ist deshalb keine Pflichtprüfung.

## 4. Sicherheitsfunktionen

Settings → Code security:
- **Private vulnerability reporting**: Enable (Meldeweg aus `SECURITY.md`).
- **Dependabot alerts**: Enable. Optional Dependabot security updates.
- **Secret scanning** + **Push protection**: Enable (für öffentliche Repos kostenlos).
- **CodeQL**: Der Workflow `codeql.yml` ist eingerichtet – **nicht** zusätzlich „Default setup“ wählen.

## 5. Issues, Labels, Discussions

- Settings → General → Features: **Discussions** einschalten (Kategorie „Fragen“ o. ä.); die Issue-Vorlagen verweisen darauf.
- Labels anlegen, die die Vorlagen setzen:

```bash
gh label create fehler --color d73a4a --description "Etwas funktioniert nicht" -R JackONeill199/astroflow
gh label create wunsch --color a2eeef --description "Neue Funktion oder Verbesserung" -R JackONeill199/astroflow
```

## 6. GitHub Pages

Settings → Pages → Build and deployment → Source **GitHub Actions**. Danach Actions → „Pages“ → Run workflow (oder
eine Änderung unter `docs/site/` nach `main`). Adresse: `https://jackoneill199.github.io/astroflow/`. Der Download-Knopf
zeigt auf das neueste veröffentlichte Release (Installer direkt, sonst die Release-Seite).

## 7. Release

1. PR mit Versionserhöhung + CHANGELOG mergen, Tag setzen: `git tag -a v<Version> -m "AstroFlow <Version>"` und
   `git push origin v<Version>`.
2. Die CI baut Installer und ZIP, prüft beide und legt einen **Entwurf** mit dem CHANGELOG-Abschnitt als Text an.
3. Entwurf ansehen, „Publish release“. Releases nur aus der CI bauen – ein lokaler Build packt alle Dateien unter
   `astroflow/` ein, auch nicht eingecheckte (`pyproject.toml`: `"*" = ["*"]`).

## Bekannte Grenzen der CI

- Übersprungen (bewusst): GPU-Vergleich (`test_gpu.py`, kein OpenCL), GraXpert-Auspacken unter Linux, SkyTrace-Tests, die
  PyTorch/Ultralytics brauchen. Die leichten Engine-Pakete (skyfield, sgp4, OpenCV headless, photutils, PyAV) installiert die CI mit.
- Die Suite lief bis zu dieser Änderung nur unter Windows; Fehlschläge unter Linux zuerst als Plattformproblem prüfen.
- Laufzeit: GitHub-Runner haben 4 Kerne, die Suite läuft mit 4 Workern (lokal 8).
