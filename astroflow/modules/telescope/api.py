"""Teleskop-API (unter /api/telescope). Herkunftsschutz und Kopplung gelten wie überall (nur dieser Rechner oder gekoppelte
Geräte); Bibliothekspfad und Geräteadresse nur am Rechner selbst. Fehlermeldungen fest und deutsch, ohne Ausnahmetexte.
Status und Protokoll enthalten keine Koordinaten des Ortes und keine Gerätekennungen."""
from __future__ import annotations

import threading

from fastapi import APIRouter, Body, HTTPException, Request, Response

from ...core import security
from . import MODELS, autopilot as ap, preview as pv, settings
from .driver import DriverError, Target

MAX_KEYS = 300
CONFIRM_MSG = "Bitte bestätigen, dass sich der Arm frei bewegen kann."      # Oberfläche erkennt daran den Bestätigungsdialog
NUM = {"cloud_max": (10, 100), "wind_max": (5, 80), "dew_min": (0, 10), "dew_min_stellina": (0, 10), "reopen_min": (5, 120),
       "min_alt": (10, 60), "exposure": (1, 60), "battery_min": (5, 50), "sim_speed": (1, 600), "night_max_targets": (1, 8)}
PLAN_EXISTS = "Für diese Nacht gibt es schon einen Plan aus dem Planer."    # Oberfläche fragt dann, ob er ersetzt werden soll


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

    def link(u) -> str:
        """Verbinden ohne jede Bewegung. Die Kontrolle wird nur genommen, wenn sie frei ist – nie der Handy-App entrissen.
        Rückgabe: Besitzer der Kontrolle danach ('us' | 'other' | 'none')."""
        u.driver.connect()
        master = u.driver.state().master
        if master == "none":
            u.driver.take_control()
            master = u.driver.state().master
        return master

    @r.get("/preview/info")
    def preview_info():
        """Klein und gepollt: gibt es ein Vorschaubild, welche Signatur, wann (Unix-Zeit) und das wievielte Bild."""
        p = pv.get(unit())
        return {"available": bool(p), **({"sig": p["sig"], "ts": p["ts"], "n": p["n"], "source": p["source"]} if p else {})}

    @r.get("/preview")
    def preview_image(request: Request):
        """Das Vorschaubild (JPEG) – nur das, was `preview.get` selbst erzeugt hat, nie ein Pfad vom Client. ETag → 304."""
        p = pv.get(unit())
        if not p:
            raise HTTPException(404, "Noch kein Vorschaubild.")
        tag = f'"{p["sig"]}"'
        if request.headers.get("if-none-match") == tag:
            return Response(status_code=304, headers={"ETag": tag})
        return Response(p["jpeg"], media_type="image/jpeg", headers={"ETag": tag, "Cache-Control": "no-cache"})

    @r.post("/connect")
    def connect():
        """Nur verbinden (Status, Akku, Speicher) und freie Kontrolle nehmen – bewegt das Teleskop nie, startet keinen Autopiloten."""
        u = unit()
        try:
            master = link(u)
        except DriverError as e:
            fail(e)
        if master == "other":
            u.pilot.log("Verbunden – die Handy-App steuert gerade, AstroFlow hat die Kontrolle nicht übernommen.", "warn", u.now())
        else:
            u.pilot.log("Verbunden – AstroFlow hat die Kontrolle. Das Teleskop wurde nicht bewegt.", "ok", u.now())
        u.pilot.tick(u.now())
        return {"ok": True, "master": master, "note": "Die Handy-App steuert gerade." if master == "other" else ""}

    @r.post("/disconnect")
    def disconnect():
        u = unit()
        if u.pilot.mode in ("laeuft", "wartet"):
            raise HTTPException(409, "Erst den Autopilot pausieren oder einklappen.")
        u.driver.disconnect()
        u.pilot.log("Verbindung getrennt.", "info", u.now())
        u.pilot.tick(u.now())
        return {"ok": True}

    def night_and_confirm(u, body: dict):
        """Gemeinsam für Start und Weiter: aktuelle Nacht laden und – bei echtem Gerät – die Bestätigung für genau diese Nacht
        verlangen. Durchgesetzt wird sie zusätzlich im Autopiloten direkt vor jedem Bewegungsbefehl (`Autopilot._cmd`)."""
        p, now = u.pilot, u.now()
        if p.night is None or p.night != p.current_night(now):
            p.load_night(now)
        if not p.confirmed(now):
            if not body.get("confirm"):
                raise HTTPException(409, CONFIRM_MSG)
            p.confirm(now)

    @r.post("/start")
    def start(body: dict = Body(default={})):
        u = unit()
        p = u.pilot
        now = u.now()
        night_and_confirm(u, body)
        if not p.state.connected:
            try:
                link(u)
            except DriverError as e:
                fail(e)
        if p.auto and p.auto.get("state") == "entwurf":
            # offener Entwurf von „Plane meine Nacht“: wie „Nacht starten“ (erst vermessen, dann neu planen) – sonst würden
            # die Entwurfsziele ohne Vermessung angefahren (Review PR #145)
            why = p.night_go(now)
            if why:
                raise HTTPException(409, why)
        else:
            p.start(now)
        threading.Thread(target=p.tick, args=(u.now(),), daemon=True).start()
        return {"ok": True}

    # ---- Plane meine Nacht ------------------------------------------------------------------------------------------
    @r.post("/night/plan")
    def night_plan(body: dict = Body(default={})):
        """Entwurf: Ziele und Zeitplan dieser Nacht automatisch wählen (bewegt nichts). Gibt es schon einen Plan aus dem
        Planer, erst nach Rückfrage (`replace`) – der gespeicherte Planer-Plan selbst bleibt unverändert. Mit „gleich starten“
        startet der Entwurf nach einer Frist von selbst; echtes Gerät: dann schon hier die Bestätigung je Nacht."""
        u = unit()
        p = u.pilot
        now = u.now()
        if p.mode in ("laeuft", "wartet"):
            raise HTTPException(409, "Der Autopilot läuft schon – erst pausieren oder einklappen.")
        if p.night is None or p.night != p.current_night(now):
            p.load_night(now)
        planner = ap.plan_items(mod.ctx.store.get("observatory").get("plan"), p.night, u.cfg["model"])
        if planner and not body.get("replace") and not p.auto:
            raise HTTPException(409, PLAN_EXISTS)
        autostart = bool(u.cfg["night_autostart"])
        if autostart:
            night_and_confirm(u, body)
        why = p.plan_night(now, autostart=autostart)
        if why:
            raise HTTPException(409, why)
        return {"ok": True, "auto": p.auto_view(u.now())}

    @r.post("/night/start")
    def night_start(body: dict = Body(default={})):
        """Entwurf starten (bewegt das Teleskop – Bestätigung je Nacht wie Start). `always`: künftig gleich starten."""
        u = unit()
        p = u.pilot
        night_and_confirm(u, body)
        if body.get("always"):
            mod.ctx.store.update("telescope", {"night_autostart": True})
            u.cfg["night_autostart"] = p.cfg["night_autostart"] = True
        if not p.state.connected:
            try:
                link(u)
            except DriverError as e:
                fail(e)
        why = p.night_go(u.now())
        if why:
            raise HTTPException(409, why)
        threading.Thread(target=p.tick, args=(u.now(),), daemon=True).start()
        return {"ok": True}

    @r.post("/night/cancel")
    def night_cancel():
        u = unit()
        why = u.pilot.night_cancel(u.now())
        if why:
            raise HTTPException(409, why)
        return {"ok": True}

    @r.post("/pause")
    def pause():
        u = unit()
        u.pilot.pause(u.now())
        return {"ok": True}

    @r.post("/resume")
    def resume(body: dict = Body(default={})):
        u = unit()
        night_and_confirm(u, body)
        u.pilot.resume(u.now())
        return {"ok": True}

    @r.post("/retry")
    def retry(body: dict = Body(default={})):
        """„Erneut versuchen“ nach Wolken im Bild / beschlagener Optik – bewegt das Teleskop (Bestätigung je Nacht wie Weiter)."""
        u = unit()
        night_and_confirm(u, body)
        if not u.pilot.retry_now(u.now()):
            raise HTTPException(409, "Gerade gibt es keine Wartezeit, die sich aufheben lässt.")
        threading.Thread(target=u.pilot.tick, args=(u.now(),), daemon=True).start()
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

    @r.post("/sky/scan")
    def sky_scan(body: dict = Body(default={})):
        """„Himmel vermessen“: Peilpunkte abfahren (bewegt das Teleskop – gleiche Bestätigung je Nacht wie Start)."""
        u = unit()
        p = u.pilot
        night_and_confirm(u, body)
        if not p.state.connected:
            try:
                link(u)
            except DriverError as e:
                fail(e)
        why = p.start_scan(u.now(), "kurz" if body.get("kind") == "kurz" else "voll")
        if why:
            raise HTTPException(409, why)
        threading.Thread(target=p.tick, args=(u.now(),), daemon=True).start()
        return {"ok": True}

    @r.post("/sky/stop")
    def sky_stop():
        u = unit()
        u.pilot.stop_scan(u.now())
        return {"ok": True}

    @r.post("/sky/accept")
    def sky_accept():
        """Gemessene Grenze dieser Nacht als Horizontmaske des Ortes übernehmen (nur auf ausdrücklichen Wunsch) – nur die
        Richtungen, in denen gemessen wurde; die übrigen bleiben, wie sie sind."""
        u = unit()
        p = u.pilot
        if u.simulated:                                   # erfundene Hindernisse nie in die Maske des echten Ortes
            raise HTTPException(409, "Im Simulator gemessene Grenzen lassen sich nicht als Horizontmaske übernehmen.")
        book = mod.ctx.service("sites")
        site = p.site or {}
        hz = p.horizon()
        if not book or not site.get("id") or not any(hz["known"]):
            raise HTTPException(409, "Noch keine Messung oder kein gespeicherter Standort für diese Nacht.")
        data = book.data()
        hit = next((s for s in data["list"] if s.get("id") == site["id"]), None)
        if hit is None:
            raise HTTPException(409, "Standort nicht gefunden.")
        old = list(hit.get("horizon") or [0.0] * len(hz["horizon"]))
        hit["horizon"] = [round(v, 1) if k else round(float(o or 0), 1) for v, k, o in zip(hz["horizon"], hz["known"], old)]
        try:
            book.save(data)
        except Exception:
            import traceback
            traceback.print_exc()
            raise HTTPException(500, "Horizontmaske konnte nicht gespeichert werden.") from None
        p.site = mod.site(p.night) or p.site
        p._hz_cache = None
        p.log(f"Horizontmaske aus der Messung übernommen ({sum(hz['known'])} Richtungen).", "ok", u.now())
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
        if body.get("sim_scenario") in ("demo", "clear", "balkon"):
            upd["sim_scenario"] = body["sim_scenario"]
        if "sun_limit" in body and body["sun_limit"] in (-12, -18, -12.0, -18.0):
            upd["sun_limit"] = float(body["sun_limit"])
        for k in ("weather", "fallback", "live_frames", "scan_auto", "night_autostart"):
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
