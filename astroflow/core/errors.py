"""Fehlermeldungen für den Browser: nie Ausnahmetexte (Pfade, Adressen, Antwortinhalte) weitergeben.

`user_message(e)` reicht eigene Meldungen durch (`UserError`, `ValueError` mit Klartext und die Klassen aus `allow`),
alles andere wird zu einer festen deutschen Meldung; Details gehen per Traceback ins Server-Protokoll."""
from __future__ import annotations

import json
import re
import traceback

GENERIC = "Unerwarteter Fehler – Details im Server-Protokoll."
# Pfade (Windows-Laufwerk, UNC, absolute Unix-Pfade) und Python-Interna verraten mehr, als der Nutzer braucht
_LEAKY = re.compile(r"[A-Za-z]:[\\/]|\\\\|(?:^|[\s'\"(=])/[\w.~-]+/|Traceback|Errno|File \"")


class UserError(Exception):
    """Fehler mit deutscher Meldung für den Nutzer – der Text darf unverändert in die Oberfläche."""


def _plain(text: str) -> bool:
    return bool(text) and len(text) <= 400 and not _LEAKY.search(text)


def user_message(e: BaseException, fallback: str = GENERIC, allow: tuple = (), log: bool = True) -> str:
    """Anzeigbarer Text zu `e`. Eigene Meldungen (UserError, Klassen aus `allow`, ValueError ohne Pfad) bleiben erhalten,
    sonst `fallback`; dann landet der Traceback im Protokoll (`log=False`, wenn der Aufrufer schon protokolliert)."""
    text = str(e).strip()
    if isinstance(e, (UserError, *allow)) and text:             # bewusst formulierte Meldungen (dürfen Ordner nennen)
        return text
    if isinstance(e, ValueError) and not isinstance(e, (UnicodeError, json.JSONDecodeError)) and _plain(text):
        return text
    if log:
        traceback.print_exception(type(e), e, e.__traceback__)
    return fallback
