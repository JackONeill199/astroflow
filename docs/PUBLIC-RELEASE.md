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
| `.github/workflows/windows-paket.yml` | Bei Tag `v*`: Release-Entwurf mit dem passenden Abschnitt aus `CHANGELOG.md` (`installer/release_notes.py`) |
| `.github/ISSUE_TEMPLATE/` | Formulare „Fehler melden“, „Wunsch äußern“, Verweis auf Discussions und vertrauliche Sicherheitsmeldung |
| `.github/rulesets/main-schutz.json` | Regeln für `main` zum Einspielen per API (s. u.) |
| `SECURITY.md`, `CONTRIBUTING.md` | Meldeweg für Lücken, Regeln für Beiträge |

## 1. Historie

Vor dem Umschalten entscheiden, wie mit der Historie verfahren wird (Bericht der Datenschutzprüfung liegt dem Maintainer
vor; Inhalte stehen bewusst nicht im Repo). Ein Force-Push ins bestehende Repo entfernt **keine** `refs/pull/*` – für eine
bereinigte Historie ein neues Repo anlegen. Danach lokal und auf GitHub:

- `git config --global user.email "<id>+<name>@users.noreply.github.com"`
- GitHub → Settings → Emails: „Keep my email addresses private“ und „Block command line pushes that expose my email“.
- Alte, gemergte Branches löschen (Settings → General: „Automatically delete head branches“ einschalten).
- Release-Entwürfe alter Versionen löschen bzw. nur den neuesten veröffentlichen.

## 2. Sichtbarkeit

Settings → General → Danger Zone → „Change repository visibility“ → Public.

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
  PyTorch/Ultralytics brauchen. Die leichten Engine-Pakete (skyfield, sgp4, OpenCV headless) installiert die CI mit.
- Die Suite lief bis zu dieser Änderung nur unter Windows; Fehlschläge unter Linux zuerst als Plattformproblem prüfen.
- Laufzeit: GitHub-Runner haben 4 Kerne, die Suite läuft mit 4 Workern (lokal 8).
