"""„AstroFlow unterstützen“: freundlicher, seltener Hinweis auf Spendenwege (Nutzerentscheidung 22.11.2026).

Zustand in `settings.json` → `support` (`snoozed_until`, `never`, `shown_count`). Der Server entscheidet nur, ob der Hinweis
*fällig* ist (genug fertige Bilder, nicht ausgeblendet); ob er gerade passt (Einrichtung, Dialog offen, Import läuft,
schon einmal in dieser Sitzung gezeigt), prüft das Frontend. Kein Tracking, keine Abfrage nach außen."""
from __future__ import annotations

import time

from fastapi import APIRouter, Body, HTTPException

# Spendenwege an einer Stelle: (id, Beschriftung, Adresse). Leere Adresse = Knopf entfällt; ohne jede Adresse gibt es keinen Dialog.
LINKS: list[tuple[str, str, str]] = [
    ("github", "GitHub Sponsors", "https://github.com/sponsors/JackONeill199"),
    ("kofi", "Ko-fi", "https://ko-fi.com/jackoneill199"),
]
AFTER_PICTURES = 3          # erst nach so vielen fertigen Bildern
SNOOZE_DAYS = 30            # „Später“


def links() -> list[dict]:
    return [{"id": i, "label": l, "url": u} for i, l, u in LINKS if u.startswith("https://")]


def due(state: dict, pictures: int, now: float | None = None) -> bool:
    """Ist der Hinweis fällig? (Links vorhanden, genug Bilder, nicht „nie“, Wartezeit abgelaufen.)"""
    now = time.time() if now is None else now
    if not links() or state.get("never") or pictures < AFTER_PICTURES:
        return False
    return now >= float(state.get("snoozed_until") or 0)


def build_router(ctx) -> APIRouter:
    r = APIRouter()

    def pictures() -> int:
        fn = ctx.services.get("finished_pictures")      # fehlt das Bilder-Modul, gibt es nichts zu zeigen
        try:
            return int(fn()) if fn else 0
        except Exception:
            return 0

    @r.get("/support")
    def get_support():
        st = ctx.store.get("support")
        n = pictures()
        return {"due": due(st, n), "links": links(), "pictures": n, "shown_count": int(st.get("shown_count") or 0)}

    @r.post("/support")
    def post_support(body: dict = Body(...)):
        """`action`: `shown` (zählt), `later` (30 Tage ausblenden), `never` (für immer)."""
        action = body.get("action")
        st = ctx.store.get("support")
        if action == "shown":
            ctx.store.update("support", {"shown_count": int(st.get("shown_count") or 0) + 1})
        elif action == "later":
            ctx.store.update("support", {"snoozed_until": time.time() + SNOOZE_DAYS * 86400})
        elif action == "never":
            ctx.store.update("support", {"never": True})
        else:
            raise HTTPException(400, "Unbekannte Aktion.")
        return {"ok": True}

    return r
