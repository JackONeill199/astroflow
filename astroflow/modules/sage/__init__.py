"""Sage: lebende Astronomie-Enzyklopädie – Bilder bestimmen, Objekte recherchieren (Fachkataloge + KI-Agent mit Belegpflicht),
persönliches Beobachtungsgedächtnis, Wissensnetz, proaktive Entdeckungen, Chat, Geschichten und Vorlesen.
Ursprung: starlight-sage (Node/Express, portiert); lokal für einen Nutzer, ohne Konten. Architektur: docs/SAGE.md."""
from __future__ import annotations

import os
import secrets
import threading
import time
from pathlib import Path

from ...core.modules import BaseModule

# Standardwerte; Umgebungsvariablen wie in starlight-sage (.env) werden als Vorgabe übernommen
ENV = {"story_api_key": "OPENAI_API_KEY", "story_model": "OPENAI_MODEL", "nova_key": "ASTROMETRY_API_KEY",
       "elevenlabs_key": "ELEVENLABS_API_KEY", "elevenlabs_voice": "ELEVENLABS_VOICE_ID", "elevenlabs_model": "ELEVENLABS_MODEL",
       "qwen_base_url": "QWEN_TTS_BASE_URL", "qwen_voice": "QWEN_TTS_VOICE", "qwen_model": "QWEN_TTS_MODEL",
       "qwen_language": "QWEN_TTS_LANGUAGE", "qwen_api_key": "QWEN_TTS_API_KEY", "openrouter_key": "OPENROUTER_API_KEY"}
DEFAULTS = {"story_provider": "openai", "story_base_url": "", "story_model": "gpt-4o", "story_api_key": "",
            "online_solve": False, "nova_key": "", "tts_provider": "openrouter",
            "qwen_base_url": "http://127.0.0.1:8880", "qwen_voice": "Ryan", "qwen_model": "tts-1", "qwen_language": "German",
            "qwen_api_key": "", "elevenlabs_key": "", "elevenlabs_voice": "", "elevenlabs_model": "eleven_flash_v2_5",
            "openrouter_key": "", "openrouter_tts_model": "elevenlabs/eleven-v4", "openrouter_tts_voice": "george",
            "auto_discover": True, "auto_story": True,
            # Enzyklopädie & Recherche
            "research_model": "", "catalog_online": True, "auto_research": True, "ads_token": "",
            "budget_month_usd": 2.0, "budget_run_usd": 0.15, "max_steps": 6, "fallback_price_mtok": 5.0,
            # Hintergrundaufgaben („Meine Entdeckungen“)
            "proactive": True, "proactive_hours": 24, "proactive_llm": False, "stale_days": 180, "observer_lat": "", "observer_lon": "",
            "task_fill": True, "task_papers": True, "task_field": True, "task_region": True, "task_stale": True, "task_targets": True}
SECRETS = ("story_api_key", "nova_key", "elevenlabs_key", "qwen_api_key", "openrouter_key", "ads_token")


class Module(BaseModule):
    id = "sage"
    name = "Sage"
    description = "Lebende Astronomie-Enzyklopädie: Objekte im Bild bestimmen, recherchieren, mit deinen Aufnahmen verknüpfen und erzählen lassen."
    icon = "book"
    scripts = ("sage.js", "sage-wiki.js")
    styles = ("sage.css", "sage-wiki.css")

    def __init__(self, ctx):
        super().__init__(ctx)
        from .store import SageStore
        from .insights import Scheduler
        from .kb import KB
        self.store = SageStore(ctx.module_dir("sage"))
        self.kb = KB(self.store.root / "knowledge.db")
        self.scheduler = Scheduler(self)
        self._research_q: list[str] = []
        self.tasks: dict[str, dict] = {}
        self.lock = threading.RLock()
        self.analyze_project = None                 # setzt api.build_router
        ctx.on("stacking.stacked", self._stacked)
        ctx.on("stacking.imported", self._stacked)

    def _stacked(self, project_id: int, result_id: int, name: str = ""):
        """Fertig gestacktes Bild automatisch bestimmen und in die Entdeckungen eintragen."""
        if self.config().get("auto_discover", True) and self.analyze_project:
            self.analyze_project(project_id, auto=True)

    def start(self):
        threading.Thread(target=self.backfill, daemon=True).start()
        threading.Thread(target=self.scheduler.loop, daemon=True).start()

    def backfill(self):
        """Bisherige Bildbestimmungen und eigene Aufnahmen mit der Enzyklopädie verknüpfen (lokal, schnell);
        Katalogdaten für fehlende Objekte holt `link_entry` im Hintergrund."""
        from .memory import observation_log
        from .research import Researcher
        try:
            r = Researcher(self)
            objs = [{"name": p["name"], "kind_label": ""} for p in observation_log(self) if p["sessions"]]
            for p in observation_log(self):
                if p["sessions"]:
                    r.ensure_stub(p["name"], ra=p.get("ra"), dec=p.get("dec"))
            for e in self.store.entries():
                objs += e.get("objects") or []
            self.link_entry({"objects": objs})
        except Exception:
            import traceback
            traceback.print_exc()

    def stop(self):
        self.scheduler.stop = True

    # ---- Enzyklopädie: Hintergrundarbeiten ------------------------------------------------
    def run_task(self, title: str, kind: str, fn, href: str = "#/sage/wiki") -> dict:
        """Längere Arbeit als Auftrag (Fortschritt, Aktivität). `fn(task, progress)` liefert das Ergebnis."""
        t = self.new_task(title, kind)
        t["href"] = href

        def go():
            try:
                res = fn(t, lambda p, m: t.update(progress=p, message=m))
                t.update(state="done", progress=1, finished=time.time(), message=t.get("done_msg") or "Fertig", result=res)
            except Exception as e:
                t.update(state="error", error=str(e), message=str(e), finished=time.time())
        threading.Thread(target=go, daemon=True).start()
        return t

    def research(self, name: str, deep: bool = True) -> dict:
        def fn(t, progress):
            from .research import Researcher
            r = Researcher(self)
            try:
                e = r.research(name, deep=deep, progress=progress)
            finally:
                t["steps"] = r.steps
            t.update(entity=e["id"], href=f"#/sage/o/{e['id']}", done_msg=f"{e['name']} recherchiert")
            return e["id"]
        return self.run_task(f"Recherche: {name}", "Enzyklopädie-Recherche", fn)

    def run_proactive(self, only: list[str] | None = None) -> dict:
        def fn(t, progress):
            from .insights import run_all
            self.scheduler.busy = True
            try:
                rep = run_all(self, only, progress)
            finally:
                self.scheduler.busy = False
            n = sum(r.get("new") or 0 for r in rep.values())
            t["done_msg"] = f"{n} neue Entdeckung{'' if n == 1 else 'en'}" if n else "Keine neuen Entdeckungen"
            return rep
        return self.run_task("Sage sucht nach Entdeckungen", "Hintergrundrecherche", fn, "#/sage/entdeckungen")

    def link_entry(self, entry: dict):
        """Objekte einer Bildbestimmung mit der Enzyklopädie verknüpfen; fehlende im Hintergrund aus SIMBAD ergänzen."""
        from .research import Researcher
        r = Researcher(self)
        todo = []
        for o in entry.get("objects") or []:
            e = r.ensure_stub(o["name"], o.get("kind_label") or "")
            if e and e["status"] == "stub":
                todo.append(e["id"])
        cfg = self.config()
        if not (todo and cfg.get("auto_research", True) and cfg.get("catalog_online", True)):
            return
        with self.lock:
            fresh = [t for t in todo if t not in self._research_q]
            self._research_q += fresh
        if fresh:
            def go():
                for eid in fresh:
                    try:
                        Researcher(self).research(eid, deep=False)
                    except Exception:
                        pass
                    finally:
                        with self.lock:
                            self._research_q.remove(eid)
            threading.Thread(target=go, daemon=True).start()

    # ---- Einstellungen ----------------------------------------------------------
    def config(self) -> dict:
        cfg = dict(DEFAULTS)
        cfg.update({k: os.environ[e] for k, e in ENV.items() if os.environ.get(e)})
        cfg.update(self.ctx.store.get("sage"))
        if os.environ.get("ASTROFLOW_SAGE_OFFLINE") == "1":          # Tests / abgeschottete Installationen: keine Fachdienste
            cfg["catalog_online"] = False
        return cfg

    def public_config(self) -> dict:
        c = self.config()
        out = {k: v for k, v in c.items() if k not in SECRETS}
        out.update({f"has_{k}": bool(c.get(k)) for k in SECRETS})
        solver = self.ctx.service("plate_solver")
        out["local_solver"] = bool(solver and solver.available)
        from .clients import openrouter_key
        out["openrouter_speech_key"] = bool(openrouter_key(c))      # eigener Schlüssel oder der der Erzähl-KI
        return out

    # ---- Aufträge -----------------------------------------------------------------
    def new_task(self, title: str, kind: str = "Bildbestimmung & Geschichte") -> dict:
        t = {"id": secrets.token_hex(6), "title": title, "kind": kind, "state": "running", "step": "upload", "message": "Bild wird vorbereitet …",
             "progress": 0.05, "started": time.time(), "finished": None, "entry": None, "error": None, "cancel": False}
        with self.lock:
            self.tasks[t["id"]] = t
            for k in [k for k, v in self.tasks.items() if v["finished"] and time.time() - v["finished"] > 3600]:
                del self.tasks[k]
        return t

    def router(self):
        from .api import build_router
        return build_router(self)

    def status(self) -> dict:
        running = [t for t in self.tasks.values() if t["state"] == "running"]
        ov = self.store.overview()["stats"]
        cel = [{**c, "image": f"/api/sage/files/{c['image']}" if c.get("image") else None} for c in self.store.celebrations()]
        unseen = self.kb.one("SELECT COUNT(*) AS n FROM insights WHERE seen=0 AND dismissed=0")["n"]
        return {"busy_jobs": len(running), "entries": len(self.store.entries()), "objects": ov["total_objects"],
                "story_ready": self.story_ready(), "celebrate": cel, "insights": unseen}

    def story_ready(self) -> bool:
        from .clients import configured_key
        c = self.config()
        return c["story_provider"] in ("lmstudio", "custom") or configured_key(c.get("story_api_key"))

    def activity(self, limit: int = 30) -> list[dict]:
        out = [{"key": f"sage:{t['id']}", "title": t["title"], "kind": t.get("kind") or "Bildbestimmung & Geschichte",
                "detail": t["error"] or t["message"], "state": t["state"], "progress": t["progress"], "started": t["started"],
                "finished": t["finished"], "href": f"#/sage/{t['entry']}" if t["entry"] else t.get("href") or "#/sage"}
               for t in self.tasks.values()]
        known = {a["href"] for a in out}
        for e in self.store.entries()[:limit]:
            href = f"#/sage/{e['id']}"
            if href in known:
                continue
            ts = time.mktime(time.strptime(e["created"][:19], "%Y-%m-%dT%H:%M:%S")) - time.timezone
            out.append({"key": f"sage:{e['id']}", "title": e.get("title") or "Analyse", "kind": "Bildbestimmung & Geschichte",
                        "detail": f"{len(e.get('objects') or [])} Objekte" + ("" if e.get("story") else " · ohne Geschichte"),
                        "state": "done", "progress": 1, "started": ts, "finished": ts, "href": href})
        return out[:limit]

    @property
    def root(self) -> Path:
        return self.store.root
