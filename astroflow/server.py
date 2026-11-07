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
from .core.folders import list_folders, search_folders
from .core.gzip import GZip
from .core.modules import AVAILABLE, Context, load_modules
from .core import sites as sites_mod
from .core import setup as setup_mod
from . import __version__ as VERSION
from .core.store import SettingsStore

STATIC = Path(__file__).parent / "static"


def create_app(settings: Settings | None = None, only: tuple[str, ...] | None = None) -> FastAPI:
    """`only` beschränkt die geladenen Module (Tests); sonst gelten Einstellungen bzw. `ASTROFLOW_MODULES`."""
    settings = settings or load_settings()
    ctx = Context(settings, SettingsStore(settings.data_dir / "settings.json"))
    # Gemeinsame Ortsliste (core/sites.py): vor den Modulen, damit sie den Dienst schon beim Anlegen finden
    book = sites_mod.SiteBook(ctx.store)
    try:
        sites_mod.migrate_tutor(book, ctx.store)
    except Exception:
        import traceback
        traceback.print_exc()
    ctx.services["sites"] = book
    ctx.services["site_for"] = book.site_for
    ctx.services["night_sources"] = []          # Module hängen hier Funktionen an: () → [{night, label, module}]
    loaded = load_modules(ctx)
    if only is not None:
        loaded = [m for m in loaded if m.id in only]
    active = [m for m in loaded if m.module]

    @asynccontextmanager
    async def lifespan(app):
        for m in active:
            m.module.start()
        import threading
        threading.Thread(target=setup.update_loop, daemon=True, name="update-check").start()
        threading.Thread(target=setup.run_order, daemon=True, name="komponenten").start()   # Auftrag aus dem Installer
        yield
        setup._stop.set()
        for m in active:
            m.module.stop()

    app = FastAPI(title="AstroFlow", version=VERSION, lifespan=lifespan)
    app.state.ctx = ctx
    app.state.modules = {m.id: m.module for m in active}
    setup = setup_mod.Setup(ctx, app.state)           # Einrichtungsassistent, Werkzeuge, Update-Hinweis, Beenden
    app.state.setup = setup

    # Build-Kennung aus den Änderungszeiten aller Oberflächendateien: Modul-Skripte werden mit ?v=<build> geladen,
    # damit der Browser nach einem Update nie alte Dateien aus dem Cache nimmt
    statics = [STATIC, *[m.module.static_dir for m in active if m.module.static_dir]]
    stamp = sorted(f"{p}:{p.stat().st_mtime_ns}" for d in statics for p in d.rglob("*") if p.is_file())
    build = hashlib.sha1("|".join(stamp).encode()).hexdigest()[:10]

    @app.get("/api/app")
    def app_info():
        upd = ctx.store.get("system").get("update") or {}
        return {"version": VERSION, "build": build, "modules": [m.info() for m in loaded],
                "setup": {"pending": setup.state()["pending"] if setup.launched else False, "launched": setup.launched},
                "update": upd if upd.get("newer") and ctx.store.get("system").get("update_check", True) else None}

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
        items += setup.activity()
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

    @app.get("/api/folders")
    def folders(path: str = ""):
        """Ordnerdialog: Unterordner auf dem Server-Rechner (leer = Laufwerke)."""
        try:
            return list_folders(path)
        except ValueError as e:
            raise HTTPException(400, str(e))

    @app.get("/api/folders/search")
    def folders_search(path: str = "", q: str = "", depth: int = 4, limit: int = 200):
        """Ordnerdialog: Unterordner (bis depth Ebenen) suchen, deren Name q enthält – ohne Leerzeichen/„_“/„-“."""
        try:
            return search_folders(path, q, depth, limit)
        except ValueError as e:
            raise HTTPException(400, str(e))

    # ---- Standorte (gemeinsam für alle Module) --------------------------------
    def _nights() -> list[dict]:
        out: dict[str, dict] = {}
        for fn in ctx.services.get("night_sources") or []:
            try:
                for n in fn() or []:
                    if n.get("night"):
                        e = out.setdefault(n["night"], {"night": n["night"], "labels": [], "modules": []})
                        if n.get("label") and n["label"] not in e["labels"]:
                            e["labels"].append(n["label"])
                        if n.get("module") and n["module"] not in e["modules"]:
                            e["modules"].append(n["module"])
            except Exception:
                import traceback
                traceback.print_exc()
        return sorted(out.values(), key=lambda n: n["night"], reverse=True)

    def _sites_view(nights: bool = True) -> dict:
        d = book.data()
        default = book.site_for(night="1900-01-01") or {}
        today = sites_mod.planning_night(time.time(), default.get("lon") or 0)   # dieselbe Nacht wie „Heute Nacht“
        view = {**d, "today": today, "today_site": book.site_for(night=today),
                "unused": sites_mod.unused_sites(d), "shadowed": sites_mod.shadowed_periods(d)}
        if ctx.services.get("site_sky"):                  # Bilder-Modul: Himmelsqualität je Ort (optional)
            try:
                view["sky"] = ctx.services["site_sky"]()
            except Exception:
                view["sky"] = {}
        if nights:
            single = {p["from"]: p["site_id"] for p in d["periods"] if p.get("from") == p.get("to")}
            view["nights"] = [{**n, "site": book.site_for(night=n["night"]), "fixed": single.get(n["night"])} for n in _nights()[:400]]
        return view

    @app.get("/api/sites")
    def get_sites(nights: bool = True):
        return _sites_view(nights)

    @app.post("/api/sites")
    def set_sites(body: dict = Body(...)):
        try:
            book.save({**book.data(), **{k: body[k] for k in ("list", "default", "periods") if k in body}})
        except (ValueError, TypeError, KeyError) as e:
            raise HTTPException(400, str(e)[:250])
        return _sites_view()

    @app.post("/api/sites/night")
    def set_site_night(body: dict = Body(...)):
        """Eine Nacht einem Ort zuordnen („heute bin ich in …“, Nachtliste); site_id null = Zuordnung entfernen."""
        try:
            book.set_night(str(body.get("night") or ""), body.get("site_id") or None)
        except (ValueError, TypeError) as e:
            raise HTTPException(400, str(e)[:250])
        return _sites_view(bool(body.get("nights", True)))

    @app.get("/api/sites/resolve")
    def resolve_site(night: str = "", when: float | None = None):
        return {"site": book.site_for(when=when, night=night or None)}

    @app.get("/api/sites/geocode")
    def geocode(q: str = ""):
        """Adresssuche über Photon/OpenStreetMap (nur auf Knopfdruck; geht als Suchtext an komoot.io)."""
        import httpx
        q = q.strip()
        if len(q) < 2 or len(q) > 250:
            raise HTTPException(400, "Bitte einen Ort oder eine Adresse eingeben.")
        try:
            data = httpx.get("https://photon.komoot.io/api/", params={"q": q, "limit": 6, "lang": "de"}, timeout=15,
                             headers={"User-Agent": "AstroFlow"}).json()
        except Exception as e:
            raise HTTPException(502, f"Adresssuche nicht erreichbar: {e}")
        rows = []
        for f in data.get("features", []):
            p = f.get("properties") or {}
            lon, lat = f["geometry"]["coordinates"][:2]
            label = ", ".join(str(p[k]) for k in ("name", "street", "housenumber", "postcode", "city", "country") if p.get(k))
            # Hausnummer ≈ 30 m, Straße ≈ 150 m, Ort/Gemeinde ≈ 2 km (grob, nur Vorschlag für die Genauigkeit)
            acc = 30 if p.get("housenumber") else 150 if p.get("street") else 2000 if p.get("type") in ("city", "district", "county") else 500
            rows.append(dict(name=label, lat=lat, lon=lon, accuracy_m=acc))
        return rows

    @app.get("/api/sites/elevation")
    def elevation(lat: float, lon: float):
        """Geländehöhe (Open-Meteo, Copernicus-DEM 90 m) – nur auf Knopfdruck."""
        import httpx
        try:
            v = httpx.get("https://api.open-meteo.com/v1/elevation", params={"latitude": round(lat, 5), "longitude": round(lon, 5)},
                          timeout=15).json()["elevation"][0]
        except Exception as e:
            raise HTTPException(502, f"Höhenabfrage nicht erreichbar: {e}")
        return {"alt_m": float(v)}

    app.include_router(setup_mod.build_router(setup), prefix="/api", tags=["setup"])

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

    # JSON/Text komprimieren (Handy im Heimnetz); eigene Middleware, damit ältere Starlette-Versionen weiter laufen
    app.add_middleware(GZip)

    for m in active:
        if m.module.static_dir:
            app.mount(f"/m/{m.id}", StaticFiles(directory=m.module.static_dir), name=f"m-{m.id}")
    app.mount("/", StaticFiles(directory=STATIC, html=True), name="ui")
    return app
