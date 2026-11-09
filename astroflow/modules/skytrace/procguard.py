"""Wächter für Engine-Kindprozesse: erst höflich (STOP-Datei, setzt der Aufrufer), nach einer Frist `terminate()`,
danach `kill()` – ein hängender Prozess blockiert so weder „Anhalten“ noch überlebt er das Server-Ende."""
from __future__ import annotations

import subprocess
import threading

STOP_GRACE = 60.0          # Sekunden für das geordnete Anhalten (aktuelles Bild/Video speichern)
KILL_GRACE = 10.0


def end_process(proc: subprocess.Popen, grace: float = STOP_GRACE) -> None:
    """Warten, bis der Prozess von selbst endet; sonst beenden, notfalls hart."""
    for step in (None, "terminate", "kill"):
        if step:
            try:
                getattr(proc, step)()
            except OSError:
                pass
        try:
            proc.wait(grace if step is None else KILL_GRACE)
            return
        except subprocess.TimeoutExpired:
            continue


def guard_process(proc: subprocess.Popen, grace: float = STOP_GRACE) -> None:
    """`end_process` im Hintergrund (der Aufrufer kehrt sofort zurück)."""
    threading.Thread(target=end_process, args=(proc, grace), daemon=True, name="skytrace-procguard").start()
