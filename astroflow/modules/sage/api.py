"""Sage-API (unter /api/sage): Bild bestimmen → Geschichte schreiben → Verlauf/Entdeckungen; Vorlesen."""
from __future__ import annotations

import re
import secrets
import shutil
import threading
import time
import traceback
from pathlib import Path

from fastapi import APIRouter, Body, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response

from . import SECRETS, clients
from .store import plain

PICTURE_EXT = (".jpg", ".jpeg", ".png", ".tif", ".tiff", ".gif", ".fit", ".fits", ".fts")


def make_preview(src: Path, dst: Path, max_side: int = 2400) -> tuple[int, int]:
    """Browsertaugliche JPG-Vorschau (FITS/TIFF werden gestreckt). Liefert die Originalgröße (B, H)."""
    import numpy as np
    from PIL import Image
    if src.suffix.lower() in (".fit", ".fits", ".fts"):
        from astropy.io import fits
        with fits.open(src, memmap=False) as h:
            a = next(x.data for x in h if x.data is not None and x.data.ndim >= 2).astype(np.float32)
        if a.ndim == 3:
            a = np.moveaxis(a, 0, -1) if a.shape[0] in (1, 3) else a
            a = a[..., 0] if a.shape[-1] == 1 else a
        a = np.flipud(a)
        lo, hi = np.percentile(a, [25, 99.8])
        a = np.clip((a - lo) / max(hi - lo, 1e-9), 0, 1)
        a = np.arcsinh(a * 20) / np.arcsinh(20)
        im = Image.fromarray((a * 255).astype(np.uint8))
    else:
        im = Image.open(src)
        im.seek(0)
        im = im.convert("RGB") if im.mode not in ("RGB", "L") else im
    size = im.size
    im.thumbnail((max_side, max_side))
    im.convert("RGB").save(dst, "JPEG", quality=90)
    return size


def build_router(mod) -> APIRouter:
    r = APIRouter()
    st = mod.store

    def url(rel: str | None) -> str | None:
        return f"/api/sage/files/{rel}" if rel else None

    def view(e: dict) -> dict:
        out = {k: v for k, v in e.items() if k not in ("file", "preview_file")}
        out["image"] = url(e.get("preview_file"))
        out["audio"] = {p: url(a["file"]) for p, a in (e.get("audio") or {}).items()}
        out["audio_variant"] = {p: a.get("variant") for p, a in (e.get("audio") or {}).items()}
        return out

    # ---- Ablauf einer Analyse ------------------------------------------------------
    def run(task: dict, image: Path, rel_preview: str, rel_file: str | None, size, source: dict):
        def step(p, msg, name=None):
            task.update(progress=p, message=msg, step=name or task["step"])

        try:
            cfg = mod.config()
            solver = mod.ctx.service("plate_solver")
            step(0.2, "Sternmuster wird gesucht …", "solve")
            sol, local_err = None, None
            if source.get("type") == "project":              # eigenes Bild: gespeicherte Lösung des Master-Stacks nutzen
                get = mod.ctx.service("project_solution")
                try:
                    sol = get(source["project"]) if get else None
                except Exception as e:
                    local_err = str(e)
            if sol is None and solver:                         # WCS im FITS-Header braucht keinen eingerichteten Solver
                hint = {"ra": source["ra"], "dec": source["dec"]} if source.get("ra") is not None else None
                try:
                    sol = solver.solve(image, filename=source.get("filename") or source.get("name"), hint=hint)
                except Exception as e:
                    if solver.available:
                        local_err = str(e)
            if sol is None and cfg.get("online_solve") and clients.configured_key(cfg.get("nova_key")):
                step(0.25, "Bild wird an nova.astrometry.net übermittelt …", "solve")
                sol = clients.nova_solve(cfg["nova_key"], image, step, lambda: task["cancel"])
            if sol is None and source.get("type") == "project":       # wenigstens das bekannte Hauptobjekt erzählen
                if source.get("imported") and source.get("ra") is None:
                    raise clients.ServiceError("Bild importiert, aber Himmelsposition nicht bestimmbar. Ein Objektname im Dateinamen (z. B. M42.jpg) oder ein eingerichteter Plate-Solver hilft bei der Bestimmung.")
                sol = {"ra": source.get("ra"), "dec": source.get("dec"), "solver": "project", "fov_w": None, "fov_h": None,
                       "objects": [{"name": source["name"], "kind_label": source.get("kind_label") or ""}]}
            if sol is None:
                if local_err:
                    raise clients.ServiceError(f"Himmelsposition nicht bestimmbar: {local_err}")
                raise clients.ServiceError("Kein Plate-Solver verfügbar: ASTAP unter Einstellungen → Bilder einrichten "
                                           "oder unter Einstellungen → Sage das Online-Lösen erlauben.")
            n = len(sol["objects"])
            step(0.82, f"{n} Himmelsobjekt{'' if n == 1 else 'e'} identifiziert" if n else "Bildfeld wurde kalibriert", "identify")
            data = {**sol, "source": source}
            story, story_err = None, None
            if task["cancel"]:
                raise clients.ServiceError("Abgebrochen.")
            if source.get("auto") and not cfg.get("auto_story", True):
                story_err = "Automatisch eingetragen – Geschichte über „Neu erzählen“ schreiben lassen."
            elif mod.story_ready():
                step(0.9, "Die KI schreibt die Geschichte …", "story")
                try:
                    story = clients.write_story(cfg, data)
                except clients.ServiceError as e:
                    story_err = str(e)
            else:
                story_err = "Keine Erzähl-KI eingerichtet – Einstellungen → Sage."
            from .store import story_title
            # Titel: Überschrift der Geschichte > Objektname > Hauptobjekt im Bild > Dateiname
            title = (story_title(story) if story else source.get("name") or (sol["objects"][0]["name"] if sol["objects"] else None)
                     or re.sub(r"\.\w+$", "", source.get("filename") or "") or "Analyse")
            entry = st.add({"source": source, "file": rel_file, "preview_file": rel_preview, "width": size[0], "height": size[1],
                            "solution": {k: v for k, v in sol.items() if k != "objects"}, "objects": sol["objects"],
                            "story": story, "story_error": story_err, "title": title})
            disc = st.record(entry)
            st.update(entry["id"], discovery=disc)
            try:
                mod.link_entry(entry)                                # Objekte → Enzyklopädie (fehlende im Hintergrund)
            except Exception:
                traceback.print_exc()
            st.celebrate(entry, disc)
            task.update(state="done", step="complete", progress=1, finished=time.time(), entry=entry["id"],
                        message="Analyse abgeschlossen" + (" (ohne Geschichte)" if not story else ""), discovery=disc,
                        title=entry["title"])
        except Exception as e:
            if not isinstance(e, clients.ServiceError):
                traceback.print_exc()
            task.update(state="error", error=str(e), message=str(e), finished=time.time())
            for rel in (rel_file, rel_preview):                      # fehlgeschlagen: Dateien nicht aufheben
                if rel:
                    (st.root / rel).unlink(missing_ok=True)

    def start(title: str, image: Path, rel_preview: str, rel_file: str | None, size, source: dict) -> dict:
        task = mod.new_task(title)
        threading.Thread(target=run, args=(task, image, rel_preview, rel_file, size, source), daemon=True).start()
        return task

    def task_view(t: dict) -> dict:
        return {k: v for k, v in t.items() if k != "cancel"}

    @r.post("/analyze")
    async def analyze(file: UploadFile = File(...)):
        name = Path(file.filename or "bild").name
        ext = Path(name).suffix.lower()
        if ext not in PICTURE_EXT:
            raise HTTPException(400, "Nur Bilddateien (JPG, PNG, TIFF, GIF, FITS) sind erlaubt.")
        stem = f"{time.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(3)}"
        rel_file, rel_prev = f"uploads/{stem}{ext}", f"uploads/{stem}-preview.jpg"
        with open(st.root / rel_file, "wb") as out:
            shutil.copyfileobj(file.file, out)
        try:
            size = make_preview(st.root / rel_file, st.root / rel_prev)
        except Exception as e:
            (st.root / rel_file).unlink(missing_ok=True)
            raise HTTPException(400, f"Bild nicht lesbar: {e}")
        return task_view(start(re.sub(r"\.\w+$", "", name), st.root / rel_file, rel_prev, rel_file, size,
                               {"type": "upload", "filename": name}))

    @r.post("/analyze/project/{pid}")
    def analyze_project(pid: int, auto: bool = False):
        """Geschichte zum neuesten Ergebnis eines Objekts aus dem Bilder-Modul (`auto`: nach dem Stacken eingetragen)."""
        get = mod.ctx.service("project_picture")
        pic = get(pid) if get else None
        if not pic:
            raise HTTPException(404, "Für dieses Objekt gibt es noch kein fertiges Bild.")
        if auto and any((e.get("source") or {}).get("result") == pic["result_id"] for e in st.entries()):
            return None                                           # dieses Ergebnis steht schon in den Entdeckungen
        # eigene Vorschau je Analyse: Löschen eines Eintrags darf das Bild eines anderen nicht mitnehmen
        rel_prev = f"uploads/projekt-{pid}-{pic['result_id']}-{secrets.token_hex(3)}.jpg"
        size = make_preview(Path(pic["image"]), st.root / rel_prev)
        src = {"type": "project", "project": pid, "result": pic["result_id"], "name": pic["name"], "kind_label": pic["kind_label"],
               "imported": pic.get("imported", False),
               "exposure": pic["exposure"], "n_frames": pic["n_frames"], "ra": pic["ra"], "dec": pic["dec"], "auto": auto}
        return task_view(start(pic["name"], Path(pic["image"]), rel_prev, None, size, src))

    mod.analyze_project = analyze_project
    from .kb_api import extend
    extend(r, mod, url)

    @r.post("/celebrations/ack")
    def ack(body: dict = Body(...)):
        st.ack([str(i) for i in body.get("ids") or []])
        return {"ok": True}

    @r.get("/tasks")
    def tasks():
        """Laufende und kürzlich beendete Aufträge (auch aus anderen Ansichten gestartete)."""
        return [task_view(t) for t in mod.tasks.values() if t["state"] == "running" or time.time() - (t["finished"] or 0) < 600]

    @r.get("/tasks/{tid}")
    def task(tid: str):
        t = mod.tasks.get(tid)
        if not t:
            raise HTTPException(404, "Auftrag nicht gefunden.")
        return task_view(t)

    @r.post("/tasks/{tid}/cancel")
    def cancel(tid: str):
        t = mod.tasks.get(tid)
        if not t:
            raise HTTPException(404)
        t["cancel"] = True
        return task_view(t)

    # ---- Verlauf & Entdeckungen ------------------------------------------------------
    @r.get("/history")
    def history():
        return [{**st.summary(e), "image": url(e.get("preview_file"))} for e in st.entries()]

    @r.get("/history/{hid}")
    def entry(hid: str):
        e = st.get(hid)
        if not e:
            raise HTTPException(404, "Analyse nicht gefunden.")
        return view(e)

    @r.patch("/history/{hid}")
    def patch_entry(hid: str, body: dict = Body(...)):
        vals = {k: str(v)[:120] for k, v in body.items() if k == "title"}
        if "seen" in body:
            vals["seen"] = True
        e = st.update(hid, **vals)
        if not e:
            raise HTTPException(404)
        return view(e)

    @r.delete("/history/{hid}")
    def delete(hid: str):
        if not st.delete(hid):
            raise HTTPException(404, "Analyse nicht gefunden.")
        return {"ok": True}

    @r.post("/history/{hid}/story")
    def retell(hid: str):
        e = st.get(hid)
        if not e:
            raise HTTPException(404)
        if not mod.story_ready():
            raise HTTPException(400, "Keine Erzähl-KI eingerichtet – Einstellungen → Sage.")
        try:
            story = clients.write_story(mod.config(), {**(e.get("solution") or {}), "objects": e.get("objects"), "source": e.get("source")})
        except clients.ServiceError as x:
            raise HTTPException(502, str(x))
        for a in (e.get("audio") or {}).values():                     # alte Vertonung passt nicht mehr
            (st.root / a["file"]).unlink(missing_ok=True)
        from .store import story_title
        return view(st.update(hid, story=story, story_error=None, title=story_title(story), audio={}))

    @r.get("/discoveries")
    def discoveries():
        ov = st.overview()
        imgs = {e["id"]: url(e.get("preview_file")) for e in st.entries()}
        for o in ov["objects"]:
            o["image"] = imgs.get(o["first_entry"])
        return ov

    @r.get("/files/{path:path}")
    def files(path: str):
        p = (st.root / path).resolve()
        if not p.is_relative_to(st.root.resolve()) or not p.is_file():
            raise HTTPException(404)
        return FileResponse(p, headers={"Cache-Control": "private, max-age=86400"})

    # ---- Einstellungen ---------------------------------------------------------------
    @r.get("/config")
    def get_config():
        return mod.public_config()

    @r.post("/config")
    def set_config(body: dict = Body(...)):
        from . import DEFAULTS
        vals = {}
        for k, v in body.items():
            if k not in DEFAULTS:
                continue
            if k in SECRETS:
                if v:                                     # leer = gespeicherten Schlüssel behalten
                    vals[k] = str(v).strip()
                continue
            if isinstance(DEFAULTS[k], bool):
                vals[k] = bool(v)
            elif isinstance(DEFAULTS[k], (int, float)):
                try:
                    vals[k] = type(DEFAULTS[k])(float(str(v).replace(",", ".")))
                except ValueError:
                    raise HTTPException(400, f"Ungültige Zahl für {k}.") from None
            else:
                vals[k] = str(v).strip()
        for k in SECRETS:                                 # ausdrücklich löschen: {"clear": ["nova_key"]}
            if k in (body.get("clear") or []):
                vals[k] = None
        mod.ctx.store.update("sage", vals)
        return mod.public_config()

    # ---- Modelle (Auswahlfelder) --------------------------------------------------------
    @r.get("/models")
    def models(kind: str = "story", provider: str | None = None, base_url: str | None = None):
        """Modelle für die Auswahlfelder: story (Erzähl-KI des gewählten Anbieters), speech (OpenRouter), elevenlabs."""
        from ...core.models import ModelListError, list_models
        c = mod.config()
        try:
            if kind == "speech":
                return list_models(clients.OPENROUTER, clients.openrouter_key(c), "speech")
            if kind == "elevenlabs":
                if not clients.configured_key(c.get("elevenlabs_key")):
                    raise HTTPException(400, "Erst den ElevenLabs-Schlüssel speichern.")
                return clients.eleven_models(c["elevenlabs_key"])
            base, key = clients.story_endpoint(c, provider, base_url)
            return list_models(base, key, "text")
        except (ModelListError, clients.ServiceError) as e:
            raise HTTPException(502, str(e))

    # ---- Vorlesen ---------------------------------------------------------------------
    variant = clients.tts_variant

    @r.get("/tts/config")
    def tts_config():
        c = mod.config()
        q = clients.qwen_cfg(c)
        return {"default": c["tts_provider"],
                "qwen": {"available": clients.qwen_health(c), "base_url": q["base"], "voice": q["voice"]},
                "elevenlabs": {"available": clients.configured_key(c.get("elevenlabs_key")), "voice": c.get("elevenlabs_voice") or None},
                "openrouter": {"available": bool(clients.openrouter_key(c)), "model": c.get("openrouter_tts_model"),
                               "voice": c.get("openrouter_tts_voice")}}

    @r.get("/tts/voices")
    def tts_voices(provider: str = "qwen", model: str | None = None):
        c = mod.config()
        try:
            if provider == "openrouter":
                from ...core.models import ModelListError, list_models
                model = model or c.get("openrouter_tts_model")
                try:
                    m = next((x for x in list_models(clients.OPENROUTER, clients.openrouter_key(c), "speech") if x["id"] == model), None)
                except ModelListError as e:
                    raise clients.ServiceError(str(e))
                return [{"id": v, "name": v} for v in (m or {}).get("voices", [])]
            if provider == "elevenlabs":
                if not clients.configured_key(c.get("elevenlabs_key")):
                    raise HTTPException(400, "ElevenLabs-API-Schlüssel nicht eingetragen.")
                return clients.eleven_voices(c["elevenlabs_key"])
            return clients.qwen_voices(c)
        except clients.ServiceError as e:
            raise HTTPException(502, str(e))
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(502, f"Stimmen nicht abrufbar: {e}")

    SPEAK = {"elevenlabs": clients.eleven_speak, "openrouter": clients.openrouter_speak, "qwen": clients.qwen_speak}

    @r.post("/tts/speak")
    def speak(body: dict = Body(...)):
        c = mod.config()
        provider = body.get("provider") or c["tts_provider"]
        if provider == "local":
            provider = "qwen"
        if provider not in SPEAK:
            raise HTTPException(400, "Unbekannter Sprachdienst.")
        hid = body.get("history_id")
        e = st.get(hid) if hid else None
        if hid and not e:
            raise HTTPException(404, "Verlaufseintrag nicht gefunden.")
        var = variant(c, provider)
        have = (e or {}).get("audio", {}).get(provider)
        if have and not body.get("force") and have.get("variant", var) == var and (st.root / have["file"]).is_file():
            return {"url": url(have["file"]), "cached": True, "variant": var}
        text = plain(body.get("text") or (e or {}).get("story") or "")
        if not text:
            raise HTTPException(400, "Kein Text zum Vorlesen vorhanden.")
        try:
            audio = SPEAK[provider](c, text)
        except clients.ServiceError as x:
            raise HTTPException(503 if "nicht erreichbar" in str(x) or "nicht eingetragen" in str(x) or "Kein " in str(x) else 502, str(x))
        if not e:
            return Response(audio, media_type="audio/mpeg")
        if have:
            (st.root / have["file"]).unlink(missing_ok=True)
        rel = f"audio/{re.sub(r'[^A-Za-z0-9_-]', '', hid)}-{provider}-{secrets.token_hex(3)}.mp3"
        (st.root / rel).write_bytes(audio)
        st.update(hid, audio={**(e.get("audio") or {}), provider: {"file": rel, "created": time.time(), "variant": var}})
        return {"url": url(rel), "cached": False, "variant": var}

    return r
