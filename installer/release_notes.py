"""Release-Text aus CHANGELOG.md: Abschnitt der Version + kurzer Download-Hinweis (für den Release-Entwurf der CI).

    python installer/release_notes.py 0.44.1 [notizen.md]
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

HEAD = """**Windows:** `AstroFlow-Setup-{v}.exe` (Installer, empfohlen) oder `AstroFlow-{v}-Windows-portabel.zip` (ohne Installation).
Andere Systeme: aus dem Quellcode bzw. per Docker, siehe README.

"""


def section(text: str, version: str) -> str | None:
    """Text unter `## <version> – …` bis zur nächsten Versionsüberschrift (ohne die Überschrift)."""
    m = re.search(rf"^## {re.escape(version)}\b[^\n]*\n(.*?)(?=^## |\Z)", text, re.M | re.S)
    return m.group(1).strip() if m else None


def notes(version: str, changelog: str | None = None) -> str:
    version = version.lstrip("v")
    text = changelog if changelog is not None else (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    body = section(text, version)
    if not body:
        raise SystemExit(f"CHANGELOG.md hat keinen Abschnitt „## {version}“.")
    return HEAD.format(v=version) + body + "\n"


if __name__ == "__main__":
    if len(sys.argv) not in (2, 3):
        raise SystemExit(__doc__)
    out = notes(sys.argv[1])
    if len(sys.argv) == 3:                                   # Datei direkt schreiben (PowerShell-Pipes verfälschen UTF-8)
        Path(sys.argv[2]).write_text(out, encoding="utf-8")
    else:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stdout.write(out)
