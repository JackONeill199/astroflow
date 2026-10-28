"""Kompositionswurzel: lädt die Module, hängt ihre APIs und Oberflächen ein und stellt die gemeinsamen Kern-Endpunkte bereit."""
from __future__ import annotations

import hashlib
import time
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import Body, FastAPI, HTTPException
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .core.config import Settings, load_settings
from .core.modules import AVAILABLE, Context, load_modules
from .core.store import SettingsStore

VERSION = "0.6.0"
STATIC = Path(__file__).parent / "static"


def create_app(settings: Settings | None = None, only: tuple[str, ...] | None = None) -> FastAPI:
    """`only` beschränkt die geladenen Module (Tests); sonst gelten Einstellungen bzw. `ASTROFLOW_MODULES`."""
    settings = settings or load_settings()
    ctx = Context(settings, SettingsStore(settings.data_dir / "settings.json"))
    loaded = load_modules(ctx)
    if only is not None:
        loaded = [m for m in loaded if m.id in only]
    active = [m for m in loaded if m.module]

    @asynccontextmanager
    async def lifespan(app):
        for m in active:
            m.module.start()
        yield
        for m in active:
            m.module.stop()

    app = FastAPI(title="AstroFlow", version=VERSION, lifespan=lifespan)
    app.state.ctx = ctx
    app.state.modules = {m.id: m.module for m in active}

    # Build-Kennung aus den Änderungszeiten aller Oberflächendateien: Modul-Skripte werden mit ?v=<build> geladen,
    # damit der Browser nach einem Update nie alte Dateien aus dem Cache nimmt
    statics = [STATIC, *[m.module.static_dir for m in active if m.module.static_dir]]
    stamp = sorted(f"{p}:{p.stat().st_mtime_ns}" for d in statics for p in d.rglob("*") if p.is_file())
    build = hashlib.sha1("|".join(stamp).encode()).hexdigest()[:10]

    @app.get("/api/app")
    def app_info():
        return {"version": VERSION, "build": build, "modules": [m.info() for m in loaded]}

    @app.get("/api/status")
    def status():
        mods, busy = {}, 0
        for m in active:
            try:
                st = m.module.status()
            except Exception as e:                       # ein Modul darf das Polling der anderen nicht stören
                st = {"error": str(e)}
            mods[m.id] = st
            busy += int(st.get("busy_jobs") or 0)
        return {"version": VERSION, "busy": busy, "modules": mods}

    @app.get("/api/activity")
    def activity(limit: int = 40):
        items = []
        for m in active:
            try:
                for a in m.module.activity(limit):
                    items.append({**a, "module": m.id})
            except Exception as e:
                items.append({"key": f"{m.id}:fehler", "module": m.id, "title": m.module.name, "state": "error",
                              "detail": f"Aktivität nicht lesbar: {e}", "started": time.time()})
        rank = {"running": 0, "queued": 1}
        items.sort(key=lambda a: (rank.get(a.get("state"), 2), -(a.get("started") or 0)))
        return items[:limit]

    @app.post("/api/modules")
    def set_modules(body: dict = Body(...)):
        """Module ein-/ausschalten – wirkt nach einem Neustart des Servers."""
        vals = {k: bool(v) for k, v in body.items() if k in AVAILABLE and k != "stacking"}
        if not vals:
            raise HTTPException(400, "Unbekanntes Modul.")
        ctx.store.update("modules", vals)
        return {"saved": vals, "restart": True}

    for m in active:
        r = m.module.router()
        if r is not None:
            app.include_router(r, prefix=f"/api/{m.id}", tags=[m.id])

    @app.middleware("http")
    async def guard(request, call_next):
        # Schreibende Anfragen nur von der eigenen Oberfläche (Schutz gegen fremde Webseiten im selben Netz)
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            origin = request.headers.get("origin")
            if origin and urlsplit(origin).netloc != request.headers.get("host"):
                return JSONResponse({"detail": "Anfrage nicht zugelassen (fremde Herkunft)."}, status_code=403)
        r = await call_next(request)
        # Startseite immer neu prüfen – sonst hängt der Browser an alten ?v=-Verweisen auf app.js/style.css
        if request.url.path in ("/", "/index.html"):
            r.headers["Cache-Control"] = "no-cache"
        return r

    for m in active:
        if m.module.static_dir:
            app.mount(f"/m/{m.id}", StaticFiles(directory=m.module.static_dir), name=f"m-{m.id}")
    app.mount("/", StaticFiles(directory=STATIC, html=True), name="ui")
    return app
