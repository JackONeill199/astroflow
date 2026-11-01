"""Optionaler KI-Bildprüfer (Vision-LLM) über LM Studio oder OpenRouter.

Beide bieten eine OpenAI-kompatible Chat-Completions-API. Der Prüfer bekommt eine
Vorschau des fertigen Bildes plus die aktuellen Parameter und beantwortet:
"Sieht das so aus, wie es aussehen sollte?" – inklusive konkreter Parameterkorrekturen.
Die Pipeline wendet diese an und prüft erneut (max. N Runden).

Datenschutz: Bei LM Studio bleibt alles lokal. Bei OpenRouter wird die verkleinerte
Vorschau an einen externen Dienst gesendet – deshalb ist der Prüfer standardmäßig AUS.
"""
from __future__ import annotations

import base64
import io
import json
import os
import re
from dataclasses import dataclass

import httpx
import numpy as np
from PIL import Image

TUNABLE = {  # Parameter, die der Prüfer ändern darf, mit erlaubtem Bereich
    "target_bg": (0.05, 0.30), "asinh": (5, 400), "denoise": (0.0, 1.0), "chroma_denoise": (0.0, 6.0),
    "saturation": (0.8, 2.2), "contrast": (0.0, 0.8), "sharpen": (0.0, 0.8), "star_reduction": (0.0, 0.8),
    "scnr": (0.0, 1.0), "gradient_degree": (1, 4), "deconv": (0, 20), "bg_dark": (0.0, 0.12), "star_brightness": (0.3, 1.2),
    "color_gradient": (0, 3),
}

SYSTEM = """Du bist ein erfahrener Astrofotograf und Bildbearbeiter und prüfst ein automatisch bearbeitetes Astrofoto.
Beurteile: Hintergrund (neutral, nicht ausgebrannt, nicht zu hell), Restgradienten/Vignettierung, Farbstich,
Sterne (nicht aufgebläht, nicht zu hart beschnitten, kein Heiligenschein), Rauschen vs. Detailverlust (Plastik-Look),
Übersättigung, Artefakte (Ringe, Kanten, Sternhöfe) und ob das Objekt wie ein typisches Foto dieses Objekts wirkt.
Antworte AUSSCHLIESSLICH mit JSON in diesem Format:
{"ok": true|false, "score": 0-10, "issues": ["..."], "adjust": {"parametername": neuer_wert}}
Erlaubte Parameter in "adjust": target_bg (Helligkeit schwacher Strukturen 0.05-0.30), bg_dark (Helligkeit des
Himmelshintergrunds über den Schwarzpunkt 0-0.12, Standard 0.03), star_brightness (Helligkeit der Sterne gegenüber
dem Nebel 0.3-1.2, Standard 0.85 – kleiner, wenn Sterne vom Objekt ablenken), asinh (Highlight-Kompression 5-400),
denoise (0-1), chroma_denoise (0-6), saturation (0.8-2.2), contrast (0-0.8), sharpen (0-0.8),
star_reduction (0-0.8), scnr (Grünstich-Entfernung 0-1), gradient_degree (1-4), deconv (0-20),
color_gradient (Farbverlauf im Himmel ausgleichen, z. B. eine rötliche Bildseite: 0 aus, 2-3 Polynomgrad).
Setze ok=true, wenn das Bild ohne Änderung gut ist (dann adjust leer). Ändere nur wenige Parameter pro Runde und moderat."""


@dataclass
class ReviewerConfig:
    provider: str = "off"          # off | lmstudio | openrouter
    base_url: str = ""
    model: str = ""
    api_key: str = ""
    max_rounds: int = 3
    timeout: float = 300.0
    max_tokens: int = 8000         # Denkmodelle (Qwen3, Bonsai …) brauchen Tausende Tokens vor der Antwort

    @classmethod
    def from_env(cls) -> "ReviewerConfig":
        prov = os.environ.get("ASTROFLOW_REVIEW_PROVIDER", "off").lower()
        return cls(
            provider=prov,
            base_url=os.environ.get("ASTROFLOW_REVIEW_URL", ""),
            model=os.environ.get("ASTROFLOW_REVIEW_MODEL", ""),
            api_key=os.environ.get("ASTROFLOW_REVIEW_KEY", os.environ.get("OPENROUTER_API_KEY", "")),
            max_rounds=int(os.environ.get("ASTROFLOW_REVIEW_ROUNDS", "3")),
            timeout=float(os.environ.get("ASTROFLOW_REVIEW_TIMEOUT", "300")),
            max_tokens=int(os.environ.get("ASTROFLOW_REVIEW_MAX_TOKENS", "8000")),
        )

    def resolved(self) -> "ReviewerConfig":
        c = ReviewerConfig(**self.__dict__)
        if c.provider == "lmstudio" and not c.base_url:
            c.base_url = "http://host.docker.internal:1234/v1" if os.path.exists("/.dockerenv") else "http://localhost:1234/v1"
        if c.provider == "openrouter" and not c.base_url:
            c.base_url = "https://openrouter.ai/api/v1"
        return c

    @property
    def enabled(self) -> bool:
        c = self.resolved()
        return c.provider in ("lmstudio", "openrouter") and bool(c.model) and (c.provider == "lmstudio" or bool(c.api_key))


def to_data_url(rgb: np.ndarray, max_side: int = 1280) -> str:
    """rgb float (3,H,W) 0..1 -> JPEG-Data-URL (Astro-Orientierung: Zeile 0 unten)."""
    a = (np.clip(np.moveaxis(rgb, 0, -1), 0, 1) * 255).astype(np.uint8)[::-1]
    im = Image.fromarray(a)
    im.thumbnail((max_side, max_side))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=88)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def parse_reply(text: str) -> dict:
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S)       # eingebettetes Nachdenken (manche Modelle)
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError("keine JSON-Antwort")
    d = json.loads(m.group(0))
    adj = {}
    for k, v in (d.get("adjust") or {}).items():
        if k in TUNABLE:
            try:
                lo, hi = TUNABLE[k]
                adj[k] = float(min(max(float(v), lo), hi))
            except (TypeError, ValueError):
                pass
    return {"ok": bool(d.get("ok")), "score": d.get("score"), "issues": [str(i) for i in (d.get("issues") or [])][:8], "adjust": adj}


def limit_steps(params: dict, adjust: dict, frac: float = 0.15) -> dict:
    """Pro Runde nur kleine Schritte (15 % des erlaubten Bereichs) – schützt vor Ausreißern schwacher Modelle."""
    out = {}
    for k, v in adjust.items():
        lo, hi = TUNABLE[k]
        cur = float(params.get(k, v))
        new = cur + max(-frac * (hi - lo), min(frac * (hi - lo), v - cur))
        if k in ("gradient_degree", "deconv", "color_gradient"):
            new = float(round(new))
        if abs(new - cur) > 1e-9:
            out[k] = new
    return out


def chat(cfg: ReviewerConfig, system: str, user: list) -> str:
    """Eine Chat-Anfrage (OpenAI-kompatibel) mit Bild; gibt den Antworttext zurück (auch bei Denkmodellen)."""
    c = cfg.resolved()
    headers = {"Content-Type": "application/json"}
    if c.api_key:
        headers["Authorization"] = f"Bearer {c.api_key}"
    body = {"model": c.model, "temperature": 0.2, "max_tokens": c.max_tokens,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
    r = httpx.post(c.base_url.rstrip("/") + "/chat/completions", headers=headers, json=body, timeout=c.timeout)
    r.raise_for_status()
    ch = r.json()["choices"][0]
    msg = ch.get("message") or {}
    content = msg.get("content") or ""
    if "{" not in content:
        # Denkmodelle: Antwort fehlt, wenn das Token-Limit schon beim Nachdenken erreicht war
        if ch.get("finish_reason") == "length":
            raise ValueError(f"Antwort abgeschnitten – das Modell hat das Limit von {c.max_tokens} Tokens beim Nachdenken "
                             "aufgebraucht (ASTROFLOW_REVIEW_MAX_TOKENS erhöhen oder ein Modell ohne Denkmodus wählen)")
        content = msg.get("reasoning_content") or msg.get("reasoning") or content
    return content


def review(cfg: ReviewerConfig, rgb: np.ndarray, params: dict, object_name: str, kind: str | None) -> dict:
    shown = {k: round(float(params[k]), 3) for k in TUNABLE if k in params}
    user = [
        {"type": "text", "text": f"Objekt: {object_name} ({kind or 'unbekannter Typ'}).\nAktuelle Parameter: {json.dumps(shown)}\nPrüfe das Bild."},
        {"type": "image_url", "image_url": {"url": to_data_url(rgb)}},
    ]
    return parse_reply(chat(cfg, SYSTEM, user))


def review_loop(cfg: ReviewerConfig, render, params: dict, object_name: str, kind: str | None, log=lambda m: None,
                first: tuple | None = None):
    """render(params) -> (rgb, stats). Gibt (rgb, stats, params, verlauf) zurück.
    first: bereits gerendertes (rgb, stats) zu params. Fehler des Prüfers brechen die Verarbeitung nie ab."""
    history = []
    rgb, stats = first or render(params)
    for rnd in range(1, cfg.max_rounds + 1):
        try:
            log(f"KI-Prüfung Runde {rnd}")
            v = review(cfg, rgb, params, object_name, kind)
        except Exception as e:
            history.append({"round": rnd, "error": str(e)})
            log(f"KI-Prüfer nicht erreichbar: {e}")
            break
        history.append({"round": rnd, **v, "params": {k: params[k] for k in v["adjust"] if k in params}})
        if v["ok"] or not v["adjust"]:
            break
        trial = {**params, **limit_steps(params, v["adjust"])}
        if trial == params:                      # Modell wiederholt nur die aktuellen Werte
            break
        rgb, stats = render(trial)
        params = trial
    return rgb, stats, params, history
