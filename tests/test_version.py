"""Versionierung (AGENTS.md → „Versionierung“): eine Quelle, CHANGELOG passt dazu."""
import re
from pathlib import Path

import astroflow
from astroflow import server

ROOT = Path(__file__).resolve().parents[1]


def test_version_format_and_single_source():
    assert re.fullmatch(r"\d+\.\d+\.\d+", astroflow.__version__)
    assert server.VERSION == astroflow.__version__


def test_changelog_top_section_is_current_version():
    text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    first = next(l for l in text.splitlines() if l.startswith("## "))
    m = re.match(r"## (\d+\.\d+\.\d+) – \d{2}\.\d{2}\.\d{4}", first)
    assert m, f"oberste CHANGELOG-Überschrift ohne Version: {first!r}"
    assert m.group(1) == astroflow.__version__, "Version erhöht, aber CHANGELOG nicht ergänzt (oder umgekehrt)"
