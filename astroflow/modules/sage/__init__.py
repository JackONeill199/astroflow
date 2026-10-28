"""Sage: Astrofotos bestimmen lassen und als Geschichte erzählen – mit Vorlesefunktion, Verlauf und Entdeckungen.
Portiert aus starlight-sage (Node/Express); lokal für einen Nutzer, ohne Konten."""
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
            "auto_discover": True, "auto_story": True}
SECRETS = ("story_api_key", "nova_key", "elevenlabs_key", "qwen_api_key", "openrouter_key")


class Module(BaseModule):
    id = "sage"
    name = "Sage"
    description = "Himmelsobjekte im Bild bestimmen und von der KI als Geschichte erzählen lassen – auch zum Anhören."
    icon = "book"
    scripts = ("sage.js",)
    styles = ("sage.css",)

    def __init__(self, ctx):
        super().__init__(ctx)
        from .store import SageStore
        self.store = SageStore(ctx.module_dir("sage"))
        self.tasks: dict[str, dict] = {}
        self.lock = threading.RLock()
        self.analyze_project = None                 # setzt api.build_router
        ctx.on("stacking.stacked", self._stacked)

    def _stacked(self, project_id: int, result_id: int, name: str = ""):
        """Fertig gestacktes Bild automatisch bestimmen und in die Entdeckungen eintragen."""
        if self.config().get("auto_discover", True) and self.analyze_project:
            self.analyze_project(project_id, auto=True)

    # ---- Einstellungen ----------------------------------------------------------
    def config(self) -> dict:
        cfg = dict(DEFAULTS)
        cfg.update({k: os.environ[e] for k, e in ENV.items() if os.environ.get(e)})
        cfg.update(self.ctx.store.get("sage"))
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
    def new_task(self, title: str) -> dict:
        t = {"id": secrets.token_hex(6), "title": title, "state": "running", "step": "upload", "message": "Bild wird vorbereitet …",
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
        return {"busy_jobs": len(running), "entries": len(self.store.entries()), "objects": ov["total_objects"],
                "story_ready": self.story_ready(), "celebrate": cel}

    def story_ready(self) -> bool:
        from .clients import configured_key
        c = self.config()
        return c["story_provider"] in ("lmstudio", "custom") or configured_key(c.get("story_api_key"))

    def activity(self, limit: int = 30) -> list[dict]:
        out = [{"key": f"sage:{t['id']}", "title": t["title"], "kind": "Bildbestimmung & Geschichte", "detail": t["error"] or t["message"],
                "state": t["state"], "progress": t["progress"], "started": t["started"], "finished": t["finished"],
                "href": f"#/sage/{t['entry']}" if t["entry"] else "#/sage"} for t in self.tasks.values()]
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
