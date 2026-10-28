"""Externe Dienste von Sage: Erzähl-KI (OpenAI-kompatibel), Vorlesen (Qwen3-TTS lokal / ElevenLabs), nova.astrometry.net.
Portiert aus starlight-sage (server.js, lib/qwen-tts.js, lib/elevenlabs.js)."""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

import httpx

PROVIDERS = {"openai": "https://api.openai.com/v1", "lmstudio": "http://localhost:1234/v1",
             "openrouter": "https://openrouter.ai/api/v1", "custom": ""}
NOVA = "https://nova.astrometry.net/api"
ELEVEN = "https://api.elevenlabs.io/v1"
ELEVEN_FALLBACK_MODEL = "eleven_flash_v2_5"

SYSTEM = ("Du bist ein leidenschaftlicher und kenntnisreicher Astronomie-Erzähler, der komplexe astronomische Konzepte "
          "auf eine zugängliche und fesselnde Weise erklärt. Du formatierst deine Antworten immer in klarem Markdown.")


class ServiceError(Exception):
    pass


def configured_key(v) -> bool:
    """Platzhalter aus .env.example und leere Werte zählen nicht."""
    return isinstance(v, str) and bool(v.strip()) and not re.match(r"^(your_|<|xxx|sk-\.\.\.)", v.strip(), re.I)


# ------------------------------------------------------------------ Erzählung -- #
def story_prompt(data: dict) -> str:
    objs = data.get("objects") or []
    lines = [f"- {o['name']}" + (f" ({o['kind_label']})" if o.get("kind_label") else "") for o in objs[:20]]
    ctx = []
    if data.get("ra") is not None:
        ctx.append(f"Bildmitte: RA {data['ra']:.3f}°, Dec {data['dec']:.3f}°")
    if data.get("fov_w"):
        ctx.append(f"Bildfeld: {data['fov_w']:.2f}° × {data['fov_h']:.2f}°")
    src = data.get("source") or {}
    if src.get("exposure"):
        ctx.append(f"Gesamtbelichtung: {src['exposure'] / 60:.0f} Minuten aus {src.get('n_frames') or '?'} Einzelbildern (eigene Aufnahme)")
    if src.get("name"):
        ctx.append(f"Hauptobjekt laut Nutzer: {src['name']}" + (f" ({src['kind_label']})" if src.get("kind_label") else ""))
    return ("Du bist ein leidenschaftlicher Astronomie-Erzähler. Basierend auf folgenden astronomischen Daten, erzähle eine "
            "faszinierende Geschichte über das, was in diesem Bild zu sehen ist:\n\n"
            + ("\n".join(ctx) + "\n\n" if ctx else "")
            + "Objekte im Bild:\n" + ("\n".join(lines) if lines else "- Keine bekannten Objekte identifiziert") + "\n\n"
            "Erzähle eine lebendige, informative und fesselnde Geschichte auf Deutsch, die sowohl für Anfänger als auch für "
            "erfahrene Astronomen interessant ist. Erkläre die Bedeutung der Objekte, ihre Entfernung, ihre Geschichte und "
            "warum sie so faszinierend sind.\n\nFormatiere die Antwort als gut strukturiertes Markdown:\n"
            "- Beginne mit einer einzeiligen Überschrift (## Titel)\n- Gliedere den Text in 3–5 Absätze mit Leerzeilen dazwischen\n"
            "- Hebe wichtige Begriffe und Objektnamen mit **Fettdruck** hervor\n"
            "- Optional: Abschluss mit einem kurzen inspirierenden Satz in *Kursivschrift*")


def write_story(cfg: dict, data: dict, timeout: float = 300) -> str:
    provider = cfg.get("story_provider") or "openai"
    base = (cfg.get("story_base_url") or PROVIDERS.get(provider) or "").rstrip("/")
    key = cfg.get("story_api_key") or ""
    if not base:
        raise ServiceError("Für die Erzähl-KI ist keine Adresse eingetragen.")
    if provider in ("openai", "openrouter") and not configured_key(key):
        raise ServiceError("Kein API-Schlüssel für die Erzähl-KI – unter Einstellungen → Sage eintragen.")
    body = {"model": cfg.get("story_model") or "gpt-4o",
            "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": story_prompt(data)}]}
    # OpenAI verlangt bei neuen Modellen max_completion_tokens; lokale Server kennen nur max_tokens (Denkmodelle brauchen Luft)
    body["max_completion_tokens" if provider == "openai" else "max_tokens"] = 1200 if provider == "openai" else 6000
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    try:
        r = httpx.post(f"{base}/chat/completions", json=body, headers=headers, timeout=timeout)
    except httpx.HTTPError as e:
        raise ServiceError(f"Erzähl-KI nicht erreichbar ({base}): {e}") from None
    if r.status_code >= 400:
        try:
            msg = r.json().get("error", {}).get("message") or r.text
        except ValueError:
            msg = r.text
        raise ServiceError(f"Erzähl-KI meldet HTTP {r.status_code}: {str(msg)[:300]}")
    text = r.json()["choices"][0]["message"].get("content") or ""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()     # Denkmodelle (Qwen3 u. a.)
    if not text:
        raise ServiceError("Die Erzähl-KI hat keinen Text geliefert.")
    return text


# -------------------------------------------------------------------- Vorlesen -- #
def qwen_cfg(cfg: dict) -> dict:
    return {"base": (cfg.get("qwen_base_url") or "http://127.0.0.1:8880").rstrip("/"), "voice": cfg.get("qwen_voice") or "Ryan",
            "model": cfg.get("qwen_model") or "tts-1", "language": cfg.get("qwen_language") or "German",
            "key": cfg.get("qwen_api_key") or ""}


def qwen_health(cfg: dict) -> bool:
    q = qwen_cfg(cfg)
    if not re.match(r"^https?://", q["base"]):
        return False
    headers = {"Authorization": f"Bearer {q['key']}"} if q["key"] else {}
    for ep in ("/health", "/v1/models"):
        try:
            if httpx.get(q["base"] + ep, headers=headers, timeout=1.5).status_code == 200:
                return True
        except httpx.HTTPError:
            pass
    return False


def qwen_voices(cfg: dict) -> list[dict]:
    q = qwen_cfg(cfg)
    r = httpx.get(q["base"] + "/v1/voices", headers={"Authorization": f"Bearer {q['key']}"} if q["key"] else {}, timeout=10)
    data = r.json()
    voices = data.get("data") or data.get("voices") or [] if isinstance(data, dict) else data
    return [{"id": v.get("id") or v.get("name") or v.get("voice_id"), "name": v.get("name") or v.get("id"),
             "language": v.get("language")} for v in voices if isinstance(v, dict)]


def qwen_speak(cfg: dict, text: str) -> bytes:
    q = qwen_cfg(cfg)
    body = {"model": q["model"], "input": text, "voice": q["voice"], "response_format": "mp3"}
    if q["language"]:
        body["language"] = q["language"]
    try:
        r = httpx.post(q["base"] + "/v1/audio/speech", json=body, timeout=300,
                       headers={"Authorization": f"Bearer {q['key']}"} if q["key"] else {})
    except httpx.ConnectError:
        raise ServiceError("Qwen3-TTS-Server nicht erreichbar. Lokalen Dienst starten und Adresse in den Einstellungen prüfen.") from None
    except httpx.HTTPError as e:
        raise ServiceError(f"Qwen3 TTS: {e}") from None
    if r.status_code >= 400:
        raise ServiceError(f"Qwen3 TTS (HTTP {r.status_code}): {r.text[:200]}")
    return r.content


def _eleven_error(r: httpx.Response) -> str:
    try:
        d = r.json().get("detail")
    except ValueError:
        d = r.text
    if isinstance(d, dict):
        if r.status_code == 402 and d.get("code") == "paid_plan_required":
            return ("ElevenLabs Free-Tier: Bibliotheksstimmen sind über die API nicht verfügbar. Unter elevenlabs.io eine eigene "
                    "Stimme mit Voice Design erstellen und in den Einstellungen wählen.")
        return "ElevenLabs: " + str(d.get("message") or d)
    return f"ElevenLabs: {d}" if d else f"ElevenLabs-Anfrage fehlgeschlagen (HTTP {r.status_code})"


def eleven_voices(key: str) -> list[dict]:
    r = httpx.get(f"{ELEVEN}/voices", headers={"xi-api-key": key}, timeout=30)
    if r.status_code >= 400:
        raise ServiceError(_eleven_error(r))
    return [{"id": v["voice_id"], "name": v["name"], "category": v.get("category"), "recommended": v.get("category") == "generated"}
            for v in r.json().get("voices", [])]


def eleven_speak(cfg: dict, text: str) -> bytes:
    key = cfg.get("elevenlabs_key") or ""
    if not configured_key(key):
        raise ServiceError("ElevenLabs-API-Schlüssel nicht eingetragen.")
    voice = (cfg.get("elevenlabs_voice") or "").strip()
    voices = None
    if not voice:                                        # erste brauchbare Stimme (Free-Tier: eigene Stimmen zuerst)
        voices = eleven_voices(key)
        pick = next((v for c in ("generated", "cloned", "premade", "professional") for v in voices if v["category"] == c), None)
        if not pick:
            raise ServiceError("Keine ElevenLabs-Stimme verfügbar. Unter elevenlabs.io eine Stimme mit Voice Design erstellen.")
        voice = pick["id"]
    model = cfg.get("elevenlabs_model") or ELEVEN_FALLBACK_MODEL
    last = None
    for m in dict.fromkeys([model, ELEVEN_FALLBACK_MODEL]):
        body = {"text": text, "model_id": m, "voice_settings": {"stability": 0.5, "similarity_boost": 0.75}}
        if any(x in m for x in ("flash", "turbo", "multilingual")):
            body["language_code"] = "de"
        r = httpx.post(f"{ELEVEN}/text-to-speech/{voice}", json=body, timeout=120,
                       headers={"xi-api-key": key, "Accept": "audio/mpeg"})
        if r.status_code < 400:
            return r.content
        last = r
        if r.status_code != 402:
            break
    raise ServiceError(_eleven_error(last))


# ---------------------------------------------------------- nova.astrometry.net -- #
def nova_solve(key: str, image: Path, progress, should_stop=lambda: False, attempts: int = 36, wait: float = 10) -> dict:
    """Bild hochladen (nicht öffentlich), auf die Lösung warten, Objekte + Pixelpositionen abholen."""
    with httpx.Client(timeout=60) as c:
        r = c.post(f"{NOVA}/login", data={"request-json": json.dumps({"apikey": key})}).json()
        if r.get("status") != "success":
            raise ServiceError("Anmeldung bei nova.astrometry.net fehlgeschlagen – API-Schlüssel prüfen.")
        meta = {"session": r["session"], "publicly_visible": "n", "allow_modifications": "d", "allow_commercial_use": "d"}
        with open(image, "rb") as f:
            r = c.post(f"{NOVA}/upload", data={"request-json": json.dumps(meta)},
                       files={"file": (image.name, f, "application/octet-stream")}, timeout=300).json()
        if r.get("status") != "success":
            raise ServiceError("Upload zu nova.astrometry.net fehlgeschlagen.")
        sub = r["subid"]
        for i in range(attempts):
            progress(0.3 + 0.45 * i / attempts, f"Sternenkarte wird berechnet … (Prüfung {i + 1}/{attempts})")
            for _ in range(int(wait * 2)):
                if should_stop():
                    raise ServiceError("Abgebrochen.")
                time.sleep(0.5)
            st = c.get(f"{NOVA}/submissions/{sub}").json()
            job = next((j for j in st.get("jobs") or [] if j), None)
            if not job:
                continue
            info = c.get(f"{NOVA}/jobs/{job}/info/").json()
            if info.get("status") == "failure":
                raise ServiceError("nova.astrometry.net konnte das Bild nicht lösen.")
            if info.get("status") != "success":
                continue
            ann = c.get(f"{NOVA}/jobs/{job}/annotations/").json().get("annotations") or []
            cal = info.get("calibration") or {}
            objs, seen = [], set()
            for a in ann:
                names = a.get("names") or []
                if not names or a.get("type") == "bright" or names[0] in seen:
                    continue
                seen.add(names[0])
                objs.append({"name": names[0], "aliases": names[1:], "kind": a.get("type"), "kind_label": "",
                             "x": a.get("pixelx"), "y": a.get("pixely"), "radius": a.get("radius") or 8})
            for n in info.get("objects_in_field") or []:
                if n not in seen and not any(n in o["aliases"] for o in objs):
                    seen.add(n)
                    objs.append({"name": n, "kind": "", "kind_label": ""})
            return {"ra": cal.get("ra"), "dec": cal.get("dec"), "scale": cal.get("pixscale"), "rotation": cal.get("orientation"),
                    "fov_w": None, "fov_h": None, "solver": "nova", "objects": objs, "nova": {"subid": sub, "job": job},
                    "tags": info.get("machine_tags") or []}
        raise ServiceError("Die Analyse dauerte zu lange. Bitte ein anderes Bild versuchen.")
