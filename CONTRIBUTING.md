# Mitmachen

Danke für dein Interesse! AstroFlow ist auf **Deutsch**: Oberfläche, Doku, Kommentare, Fehlermeldungen und Commit-Texte.
Technische Einzelheiten zu Aufbau und Modulen stehen in [`AGENTS.md`](AGENTS.md) (Einstieg und Regeln) und in [`docs/entwicklung/`](docs/entwicklung/) (Details je Thema).

## Ablauf

1. Fehler oder Wunsch zuerst als Issue beschreiben (Vorlagen „Fehler melden“ / „Wunsch äußern“), Fragen in den Discussions.
2. Branch von aktuellem `main`: `feature/…` für Neues, `fix/…` für Korrekturen.
3. Pull Request gegen `main`. Direkt auf `main` wird nicht gearbeitet – der Branch ist geschützt, die Tests müssen grün sein.

## Entwicklungsumgebung

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements-dev.txt     # Linux/macOS: .venv/bin/python
.venv/Scripts/python -m playwright install chromium              # für die Oberflächentests
.venv/Scripts/python -m astroflow serve --port 8080 --data ./data
```

## Tests

```bash
.venv/Scripts/python -m pytest -q                 # alles, parallel (pytest.ini)
.venv/Scripts/python -m pytest -q -m "not ui"     # ohne Browser-Tests
```

- Neue Funktionen bekommen Tests in `tests/`. Nur `tmp_path` für Dateien, Server auf freiem Port, Umgebungsvariablen nur
  per `monkeypatch`, kein Netz (Attrappen statt echter Dienste).
- Die CI (Workflow „Tests“) läuft auf Ubuntu und Windows. Was dort nicht geht – GPU/OpenCL, die PyTorch-Engine von
  SkyTrace, GraXpert unter Linux – überspringen die Tests selbst; bitte nicht löschen, sondern sauber überspringen.

## Versionierung

- Die Version steht nur in `astroflow/__init__.py`. Schema `0.MINOR.PATCH`: PATCH = Fehlerbehebung/Leistung/Texte,
  MINOR = neue Funktion, Einstellung, Ansicht, Datenformat oder API.
- Jede für Nutzer sichtbare Änderung erhöht die Version im selben PR und bekommt oben in `CHANGELOG.md` einen Abschnitt
  `## <Version> – <TT.MM.JJJJ>` mit `### Neu` bzw. `### Behoben`. Rein interne Änderungen (Tests, Doku, CI) ohne Erhöhung.
- Nach dem Merge setzt der Maintainer das Tag `v<Version>`; die CI baut daraus Installer und ZIP.

## Keine privaten Angaben

Ins Repo gehören **nie**: Benutzerpfade, Laufwerksbuchstaben echter Daten, IP-Adressen aus dem Heimnetz, Beobachtungsorte
und Koordinaten, Seriennummern/Kennungen echter Geräte, API-Schlüssel – auch nicht in Tests, Commits, Screenshots oder
PR-Texten. Tests nutzen erfundene Orte und Pfade, Screenshots werden vorher maskiert. Eigene lokale Notizen gehören in
`AGENTS.local.md` (steht in `.gitignore`). Bitte `git add` gezielt verwenden statt `git add -A`.

## Stil

Kein Formatter konfiguriert – Stil des umliegenden Codes übernehmen (knapp, deutsche Kommentare). Oberfläche: Vanilla-JS
ohne Build-Schritt, Maße in `rem`.

## Lizenz

AstroFlow steht unter der **PolyForm Noncommercial License 1.0.0** (`LICENSE`): Quellcode offen, private und nicht-kommerzielle
Nutzung frei. Ausnahmen: SkyTrace-Engine (`astroflow/modules/skytrace/engine/`, AGPL-3.0-or-later, einzelne Hilfsdateien doppelt lizenziert)
und Detektormodell (`models/`, AGPL-3.0), die Katalogdaten unter CC-BY-SA 4.0 (Überblick in `NOTICE`). Stände bis einschließlich v0.51.3 bleiben MIT.

**Rechte an deinem Beitrag:** Mit einem Pull Request bestätigst du, dass du den Beitrag selbst erstellt hast (oder ihn
weitergeben darfst). Du räumst dem Autor von AstroFlow (JackONeill199) das einfache, zeitlich und räumlich unbeschränkte,
unwiderrufliche Recht ein, deinen Beitrag zu nutzen, zu ändern und weiterzugeben – unter der Projektlizenz und auch unter
anderen Lizenzen, kommerzielle Verwertung eingeschlossen. Dein Urheberrecht bleibt bei dir, du darfst deinen Beitrag weiter
selbst verwenden. Wenn du damit nicht einverstanden bist, schreib das bitte vorher in ein Issue.
