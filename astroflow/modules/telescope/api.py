"""Teleskop-API (unter /api/telescope). Herkunftsschutz und Kopplung gelten wie überall (nur dieser Rechner oder gekoppelte
Geräte); Bibliothekspfad und Geräteadresse nur am Rechner selbst. Fehlermeldungen fest und deutsch, ohne Ausnahmetexte.
Status und Protokoll enthalten keine Koordinaten des Ortes und keine Gerätekennungen."""
from __future__ import annotations

import threading

from fastapi import APIRouter, Body, HTTPException, Request

from ...core import security
from . import MODELS, autopilot as ap, settings
from .driver import DriverError, Target

MAX_KEYS = 300
NUM = {"cloud_max": (10, 100), "wind_max": (5, 80), "dew_min": (0, 10), "dew_min_stellina": (0, 10), "reopen_min": (5, 120),
       "min_alt": (10, 60), "exposure": (1, 60), "battery_min": (5, 50), "sim_speed": (1, 600)}


def build_router(mod) -> APIRouter:
    r = APIRouter()

    def unit():
        return mod.unit

    def fail(e: DriverError, code: int = 409):
        raise HTTPException(code, str(e))

    @r.get("/state")
    def state():
        u = unit()
        now = u.now()
        if u.pilot.night is None:
            u.pilot.load_night(now)
        out = u.pilot.view(now)
        out["unit"] = u.info()
        return out

    @r.get("/events")
    def events(since: int = 0):
        return {"events": unit().pilot.events_since(since)}

    @r.post("/connect")
    def connect():
        u = unit()
        try:
            u.driver.connect()
            u.driver.take_control()
        except DriverError as e:
            fail(e)
        u.pilot.log("Verbunden – AstroFlow hat die Kontrolle.", "ok", u.now())
        u.pilot.tick(u.now())
        return {"ok": True}

    @r.post("/disconnect")
    def disconnect():
        u = unit()
        if u.pilot.mode in ("laeuft", "wartet"):
            raise HTTPException(409, "Erst den Autopilot pausieren oder einklappen.")
        u.driver.disconnect()
        u.pilot.log("Verbindung getrennt.", "info", u.now())
        return {"ok": True}

    @r.post("/start")
    def start(body: dict = Body(default={})):
        u = unit()
        p = u.pilot
        now = u.now()
        if p.night is None:
            p.load_night(now)
        if not u.simulated and p.confirmed_night != p.night:
            if not body.get("confirm"):
                raise HTTPException(409, "Bitte bestätigen, dass sich der Arm frei bewegen kann.")
            p.confirmed_night = p.night
        if u.simulated:
            p.confirmed_night = p.night
        if not p.state.connected:
            try:
                u.driver.connect()
                u.driver.take_control()
            except DriverError as e:
                fail(e)
        p.start(now)
        threading.Thread(target=p.tick, args=(u.now(),), daemon=True).start()
        return {"ok": True}

    @r.post("/pause")
    def pause():
        u = unit()
        u.pilot.pause(u.now())
        return {"ok": True}

    @r.post("/resume")
    def resume():
        u = unit()
        u.pilot.resume(u.now())
        return {"ok": True}

    @r.post("/park")
    def park():
        """Not-Aus: sofort stoppen und einklappen – ohne Warteschlange, immer erlaubt."""
        u = unit()
        u.pilot.emergency(u.now())
        return {"ok": True}

    @r.post("/reload")
    def reload_plan():
        u = unit()
        if u.pilot.mode in ("laeuft", "wartet"):
            raise HTTPException(409, "Während der Autopilot läuft, bleibt der Plan der Nacht stehen.")
        u.pilot.items = [i for i in u.pilot.items if i.manual]
        u.pilot.load_night(u.now())
        return {"ok": True, "items": len(u.pilot.items)}

    @r.post("/targets")
    def add_target(body: dict = Body(...)):
        name = str(body.get("name") or "").strip()[:80]
        resolve = mod.ctx.service("catalog_resolve")
        c = resolve(name) if (resolve and name) else None
        if not c:
            raise HTTPException(404, "Objekt nicht im Katalog gefunden.")
        try:
            hours = max(0.25, min(8.0, float(body.get("hours") or 1.0)))
        except (TypeError, ValueError):
            hours = 1.0
        u = unit()
        if u.pilot.night is None:
            u.pilot.load_night(u.now())
        it = u.pilot.add_target(Target(str(c["id"]), c.get("display") or str(c["id"]), float(c["ra"]), float(c["dec"])), u.now(), hours)
        return {"ok": True, "item": it.view()}

    @r.post("/proposals/accept")
    def accept(body: dict = Body(default={})):
        """Verdeckte Richtungen in die Horizontmaske des Ortes dieser Nacht übernehmen (nur auf ausdrücklichen Wunsch)."""
        u = unit()
        p = u.pilot
        book = mod.ctx.service("sites")
        site = p.site or {}
        if not book or not site.get("id") or not p.proposals:
            raise HTTPException(409, "Keine Vorschläge oder kein gespeicherter Standort für diese Nacht.")
        idx = body.get("indices")
        pts = [x for k, x in enumerate(p.proposals) if idx is None or k in idx]
        data = book.data()
        hit = next((s for s in data["list"] if s.get("id") == site["id"]), None)
        if hit is None:
            raise HTTPException(409, "Standort nicht gefunden.")
        hit["horizon"] = ap.horizon_with(hit.get("horizon"), pts)
        try:
            book.save(data)
        except Exception:
            import traceback
            traceback.print_exc()
            raise HTTPException(500, "Horizontmaske konnte nicht gespeichert werden.") from None
        p.proposals = [x for x in p.proposals if x not in pts]
        p.site = mod.site(p.night) or p.site
        p.log(f"Horizontmaske angepasst ({len(pts)} Richtung{'en' if len(pts) != 1 else ''}).", "ok", u.now())
        return {"ok": True}

    @r.post("/proposals/dismiss")
    def dismiss():
        unit().pilot.proposals = []
        return {"ok": True}

    @r.get("/captures")
    def captures(fresh: bool = False):
        """Aufnahmen auf dem Teleskop (FTP, nur lesen). Nicht erreichbar → available: false (kein Fehler)."""
        return mod.captures.listing(fresh)

    @r.post("/captures/import")
    def captures_import(body: dict = Body(...)):
        keys = [str(k)[:300] for k in (body.get("keys") or [])][:MAX_KEYS]
        try:
            return mod.captures.start(keys)
        except RuntimeError as e:
            raise HTTPException(409, str(e)) from None

    def drivers(cfg: dict) -> tuple[list[dict], list]:
        infos = mod.plugins()
        enabled = set(cfg["plugins_enabled"])
        out = [{"id": "sim", "label": "Simulator (ohne Gerät)", "usable": True},
               {"id": "vaonis-read", "label": "Vaonis – nur Status und Bilder (ohne Steuerung)", "usable": True}]
        out += [{"id": f"plugin:{i.id}", "label": f"{i.name} (Plugin)", "usable": i.error is None and i.id in enabled}
                for i in infos]
        return out, infos

    @r.get("/settings")
    def get_settings():
        cfg = settings(mod.ctx.store)
        drv, infos = drivers(cfg)
        return {**cfg, "models": MODELS, "drivers": drv, "plugins": [i.view(set(cfg["plugins_enabled"])) for i in infos]}

    @r.post("/plugins/enable")
    def plugin_enable(request: Request, body: dict = Body(...)):
        """Fremden Code aktivieren/deaktivieren – nur am Rechner selbst."""
        security.require_local(request, "Treiber-Plugins aktivieren")
        if unit().pilot.mode in ("laeuft", "wartet", "pausiert"):
            raise HTTPException(409, "Erst den Autopilot beenden (Einklappen).")
        cfg = settings(mod.ctx.store)
        pid = str(body.get("id") or "")
        if not any(i.id == pid for i in mod.plugins()):
            raise HTTPException(404, "Plugin nicht gefunden.")
        on = [x for x in cfg["plugins_enabled"] if x != pid] + ([pid] if body.get("enable") else [])
        upd = {"plugins_enabled": on}
        if not body.get("enable") and cfg["driver"] == f"plugin:{pid}":
            upd["driver"] = "sim"                                     # deaktiviert → zurück zum Simulator
        mod.ctx.store.update("telescope", upd)
        mod.rebuild()
        return get_settings()

    @r.post("/plugins/paths")
    def plugin_paths(request: Request, body: dict = Body(...)):
        security.require_local(request, "Plugin-Ordner")
        paths = [str(x).strip()[:500] for x in (body.get("paths") or []) if str(x).strip()][:10]
        mod.ctx.store.update("telescope", {"plugin_paths": paths})
        return get_settings()

    @r.post("/settings")
    def set_settings(request: Request, body: dict = Body(...)):
        if unit().pilot.mode in ("laeuft", "wartet", "pausiert"):
            raise HTTPException(409, "Erst den Autopilot beenden (Einklappen), dann die Einstellungen ändern.")
        cur = settings(mod.ctx.store)
        upd = {}
        if "ip" in body and str(body["ip"] or "").strip() != str(cur.get("ip") or ""):
            security.require_local(request, "Geräteadresse")
            upd["ip"] = str(body["ip"] or "").strip()[:100]
        if "driver" in body and body["driver"] != cur["driver"]:
            drv, _ = drivers(cur)
            hit = next((d for d in drv if d["id"] == body["driver"]), None)
            if hit is None:
                raise HTTPException(400, "Unbekannte Steuerung.")
            if hit["id"] != "sim":
                security.require_local(request, "Echtes Teleskop einrichten")
            if not hit["usable"]:
                raise HTTPException(409, "Dieses Plugin ist nicht aktiviert oder nicht nutzbar.")
            upd["driver"] = hit["id"]
        if isinstance(body.get("plugin_options"), dict):
            infos = {i.id: i for i in mod.plugins()}
            opts = dict(cur["plugin_options"] or {})
            for pid, vals in body["plugin_options"].items():
                info = infos.get(str(pid))
                if not info or not isinstance(vals, dict):
                    continue
                keys = {o["key"]: o.get("type", "text") for o in info.options}
                new = {k: (bool(v) if keys[k] == "bool" else str(v or "").strip()[:500]) for k, v in vals.items() if k in keys}
                if new != (opts.get(info.id) or {}):
                    security.require_local(request, "Plugin-Einstellungen")
                    opts[info.id] = new
            upd["plugin_options"] = opts
        if body.get("model") in MODELS:
            upd["model"] = body["model"]
        if body.get("sim_start") in ("now", "night"):
            upd["sim_start"] = body["sim_start"]
        if body.get("sim_scenario") in ("demo", "clear"):
            upd["sim_scenario"] = body["sim_scenario"]
        if "sun_limit" in body and body["sun_limit"] in (-12, -18, -12.0, -18.0):
            upd["sun_limit"] = float(body["sun_limit"])
        for k in ("weather", "fallback", "live_frames"):
            if k in body:
                upd[k] = bool(body[k])
        for k, (lo, hi) in NUM.items():
            if k in body:
                try:
                    upd[k] = max(lo, min(hi, float(body[k])))
                except (TypeError, ValueError):
                    raise HTTPException(400, "Bitte Zahlen eingeben.") from None
        mod.ctx.store.update("telescope", upd)
        try:
            mod.rebuild()
        except DriverError as e:
            fail(e)
        return get_settings()

    return r
