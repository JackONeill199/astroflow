"""Release-Text aus dem CHANGELOG (installer/release_notes.py, genutzt vom Workflow windows-paket.yml)."""
import importlib.util
from pathlib import Path

import pytest

from astroflow import __version__

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("release_notes", ROOT / "installer" / "release_notes.py")
rn = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rn)

TEXT = """# Änderungen

## 0.2.0 – 02.01.2026

### Neu
- Zwei

## 0.1.10 – 01.01.2026

### Behoben
- Eins
"""


def test_abschnitt_der_version():
    n = rn.notes("v0.2.0", TEXT)
    assert "AstroFlow-Setup-0.2.0.exe" in n and "- Zwei" in n
    assert "Eins" not in n and "## 0.2.0" not in n
    assert "- Eins" in rn.notes("0.1.10", TEXT)
    assert rn.section(TEXT, "0.1.1") is None                 # kein Präfix-Treffer auf 0.1.10


def test_unbekannte_version():
    with pytest.raises(SystemExit):
        rn.notes("9.9.9", TEXT)


def test_aktuelle_version_hat_text():
    assert "###" in rn.notes(__version__)
