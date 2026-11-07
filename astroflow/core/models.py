"""Modelllisten OpenAI-kompatibler Anbieter (OpenAI, OpenRouter, LM Studio …) für die Auswahlfelder der Oberfläche."""
from __future__ import annotations

import re
import threading
import time

import httpx

# Ohne Architekturangabe (OpenAI, LM Studio) anhand des Namens aussortieren, was kein Chat-Modell ist
NOT_CHAT = re.compile(r"embed|whisper|tts|dall-e|moderation|davinci|babbage|transcribe|realtime|audio|search|computer-use|gpt-image|sora", re.I)
_cache: dict[tuple, tuple[float, list]] = {}
_lock = threading.Lock()


class ModelListError(Exception):
    pass


def _price(m: dict, kind: str) -> str:
    p = m.get("pricing")
    if not p:                                   # lokale Server (LM Studio) nennen keine Preise
        return ""
    try:
        if kind == "speech":
            v = float(p.get("prompt") or 0) * 1000
            return "kostenlos" if not v else f"{v:.3f} $ / 1000 Zeichen".replace(".", ",")
        a, b = float(p.get("prompt") or 0) * 1e6, float(p.get("completion") or 0) * 1e6
        return "kostenlos" if not a and not b else f"{a:.2f} / {b:.2f} $ je Mio. Tokens".replace(".", ",")
    except (TypeError, ValueError):
        return ""


def same_url(a: str | None, b: str | None) -> bool:
    """Gleiche Server-Adresse (Groß-/Kleinschreibung von Schema und Host, abschließender Schrägstrich egal)."""
    from urllib.parse import urlsplit
    if not a or not b:
        return False
    ua, ub = urlsplit(a.strip().rstrip("/")), urlsplit(b.strip().rstrip("/"))
    return (ua.scheme.lower(), ua.netloc.lower(), ua.path) == (ub.scheme.lower(), ub.netloc.lower(), ub.path)


def key_for(url: str, key: str, *allowed: str | None) -> str:
    """Gespeicherten Schlüssel nur an eine der erlaubten Adressen (gespeicherte bzw. feste Anbieteradresse) schicken."""
    return key if key and any(same_url(url, a) for a in allowed) else ""


def list_models(base_url: str, api_key: str = "", kind: str = "text", ttl: float = 300) -> list[dict]:
    """`kind`: text (Chat), vision (Chat mit Bildeingabe) oder speech (Sprachausgabe, OpenRouter).
    Ergebnis: [{id, name, info, voices}] – nach Name sortiert, 5 min zwischengespeichert."""
    base = (base_url or "").rstrip("/")
    if not re.match(r"^https?://", base):
        raise ModelListError("Keine gültige Server-Adresse.")
    key = (base, bool(api_key), kind)
    with _lock:
        hit = _cache.get(key)
        if hit and time.time() - hit[0] < ttl:
            return hit[1]
    params = {"output_modalities": "speech"} if kind == "speech" else None
    try:
        r = httpx.get(base + "/models", params=params, timeout=12,
                      headers={"Authorization": f"Bearer {api_key}"} if api_key else {})
    except httpx.HTTPError as e:
        raise ModelListError(f"Server nicht erreichbar ({base}): {e}") from None
    if r.status_code in (401, 403):
        raise ModelListError("Zugriff verweigert – API-Schlüssel prüfen.")
    if r.status_code >= 400:
        raise ModelListError(f"Modellliste nicht abrufbar (HTTP {r.status_code}).")
    try:
        data = r.json().get("data") or []
    except (ValueError, AttributeError):
        raise ModelListError("Unerwartete Antwort des Servers.") from None
    out = []
    for m in data:
        mid = m.get("id")
        if not mid:
            continue
        arch = m.get("architecture") or {}
        ins, outs = arch.get("input_modalities"), arch.get("output_modalities")
        if kind == "speech":
            if (outs is not None and "speech" not in outs) or (outs is None and not re.search(r"tts|speech", mid, re.I)):
                continue
        else:
            if outs is not None and "text" not in outs:
                continue
            if outs is None and NOT_CHAT.search(mid):
                continue
            if kind == "vision" and ins is not None and "image" not in ins:
                continue
        info = [x for x in (_price(m, kind), f"{m['context_length'] // 1000}k Kontext" if m.get("context_length") and kind != "speech" else "") if x]
        out.append({"id": mid, "name": m.get("name") or mid, "info": " · ".join(info), "voices": m.get("supported_voices") or []})
    out.sort(key=lambda x: x["name"].lower())
    with _lock:
        _cache[key] = (time.time(), out)
    return out
