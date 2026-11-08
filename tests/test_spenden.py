"""Spendenlinks: FUNDING.yml, README und Einstellungsseite (nur Dateiprüfung, ohne Netz)."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPONSORS = "https://github.com/sponsors/JackONeill199"
KOFI = "https://ko-fi.com/jackoneill199"


def test_funding_yml():
    t = (ROOT / ".github" / "FUNDING.yml").read_text(encoding="utf-8")
    assert "github: JackONeill199" in t and "ko_fi: jackoneill199" in t


def test_readme_und_einstellungen_nennen_die_links():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    js = (ROOT / "astroflow" / "static" / "setup.js").read_text(encoding="utf-8")
    for text in (readme, js):
        assert SPONSORS in text and KOFI in text
    assert "AstroFlow unterstützen" in readme
    assert js.count('target="_blank" rel="noopener"') >= 2
