"""Werkzeug-Agent für OpenAI-kompatible Chat-APIs (OpenRouter, OpenAI, LM Studio, eigener Server).

Das Modell darf Werkzeuge aufrufen (Kataloge, Literatur, eigene Daten), jeder Schritt wird protokolliert. Budgets:
Schritte je Lauf, Kosten je Lauf, Kosten je Monat (Tabelle `usage`). OpenRouter meldet die echten Kosten
(`usage.cost`); sonst wird aus den Tokens mit einem vorsichtigen Preis geschätzt. Modelle ohne Function Calling
bekommen die Aufgabe ohne Werkzeuge."""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Callable

import httpx

from . import clients


class BudgetError(Exception):
    pass


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict
    fn: Callable[..., object]

    def schema(self) -> dict:
        return {"type": "function", "function": {"name": self.name, "description": self.description, "parameters": self.parameters}}


def tool(name: str, description: str, fn, /, **props) -> Tool:
    """Kurzform: tool("x", "…", f, query=("string", "Suchbegriff")) – alle Parameter Pflicht außer mit Standardwert ''."""
    req = [k for k, v in props.items() if len(v) < 3]
    return Tool(name, description, {"type": "object", "properties": {k: {"type": v[0], "description": v[1]} for k, v in props.items()},
                                    "required": req}, fn)


@dataclass
class LLM:
    """Ein konfiguriertes Modell samt Budget. `cfg` = Sage-Einstellungen."""
    cfg: dict
    kb: object
    purpose: str = "research"
    run_id: int | None = None
    cost: float = 0.0
    tokens: int = 0
    calls: int = 0
    tools_ok: bool = True
    log: list = field(default_factory=list)

    @property
    def model(self) -> str:
        return self.cfg.get("research_model") or self.cfg.get("story_model") or "gpt-4o"

    @property
    def provider(self) -> str:
        return self.cfg.get("story_provider") or "openai"

    def ready(self) -> bool:
        return self.provider in ("lmstudio", "custom") or clients.configured_key(self.cfg.get("story_api_key"))

    def check_budget(self):
        month = float(self.cfg.get("budget_month_usd") or 0)
        run = float(self.cfg.get("budget_run_usd") or 0)
        if month and self.kb.month_cost() >= month:
            raise BudgetError(f"Monatsbudget von {month:.2f} $ ist aufgebraucht (Einstellungen → Sage → Recherche).")
        if run and self.cost >= run:
            raise BudgetError(f"Budget dieses Laufs ({run:.2f} $) erreicht.")

    def chat(self, messages: list[dict], tools: list[Tool] | None = None, max_tokens: int = 3000, json_mode: bool = False,
             timeout: float = 240) -> dict:
        self.check_budget()
        base, key = clients.story_endpoint(self.cfg)
        if not base:
            raise clients.ServiceError("Für die KI ist keine Adresse eingetragen.")
        body = {"model": self.model, "messages": messages, "temperature": 0.2}
        body["max_completion_tokens" if self.provider == "openai" else "max_tokens"] = max_tokens if self.provider == "openai" else max_tokens * 2
        if tools and self.tools_ok:
            body["tools"] = [t.schema() for t in tools]
        if json_mode and self.provider in ("openai", "openrouter"):
            body["response_format"] = {"type": "json_object"}
        if self.provider == "openrouter":
            body["usage"] = {"include": True}
        headers = {"Authorization": f"Bearer {key}"} if key else {}
        headers["X-Title"] = "AstroFlow Sage"
        try:
            r = httpx.post(f"{base}/chat/completions", json=body, headers=headers, timeout=timeout)
        except httpx.HTTPError as e:
            raise clients.ServiceError(f"KI nicht erreichbar ({base}): {e}") from None
        if r.status_code == 400 and "tools" in body and re.search(r"tool|function", r.text, re.I):
            self.tools_ok = False                       # Modell kann keine Werkzeuge – ohne erneut versuchen
            return self.chat(messages, None, max_tokens, json_mode, timeout)
        if r.status_code >= 400:
            try:
                msg = r.json().get("error", {}).get("message") or r.text
            except ValueError:
                msg = r.text
            raise clients.ServiceError(f"KI meldet HTTP {r.status_code}: {str(msg)[:300]}")
        d = r.json()
        u = d.get("usage") or {}
        tin, tout = int(u.get("prompt_tokens") or 0), int(u.get("completion_tokens") or 0)
        cost, est = u.get("cost"), False
        if cost is None:
            # Kosten unbekannt (OpenAI, lokale Server): mit vorsichtigem Preis schätzen; lokal kostet nichts
            price = 0.0 if self.provider == "lmstudio" else float(self.cfg.get("fallback_price_mtok") or 5.0)
            cost, est = (tin + tout) * price / 1e6, True
        self.cost += float(cost)
        self.tokens += tin + tout
        self.calls += 1
        self.kb.log_usage(self.model, tin, tout, float(cost), est, self.run_id, self.purpose)
        msg = d["choices"][0]["message"]
        if msg.get("content"):
            msg["content"] = clients.strip_think(msg["content"])
        return msg


MAX_CALLS = 24                                       # Werkzeugaufrufe je Lauf (gesamt)
TOOL_LIMITS = {"research_object": 3}                 # schreibende/teure Werkzeuge je Lauf


def run_agent(llm: LLM, system: str, user: str, tools: list[Tool], max_steps: int = 6, on_step=None,
              final_hint: str = "", max_tokens: int = 3000, json_mode: bool = False, max_calls: int = MAX_CALLS,
              limits: dict | None = None) -> tuple[str, list[dict]]:
    """Werkzeugschleife: Modell ruft Werkzeuge, bis es antwortet oder Schritte/Budget erschöpft sind.
    `max_calls` begrenzt die Werkzeugaufrufe je Lauf, `limits` einzelne Werkzeuge (Standard: schreibende wie
    `research_object`); überzählige Aufrufe lehnt die Schleife mit einer Meldung an das Modell ab.
    Liefert (Antworttext, Protokoll); Werkzeugschritte tragen `sources` (gelieferte Quellennummern)."""
    limits = TOOL_LIMITS if limits is None else limits
    used: dict[str, int] = {}
    msgs = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    steps: list[dict] = []
    by_name = {t.name: t for t in tools}
    for i in range(max_steps):
        try:
            msg = llm.chat(msgs, tools, max_tokens=max_tokens, json_mode=json_mode and not tools)
        except BudgetError as e:
            steps.append({"type": "budget", "text": str(e), "t": time.time()})
            raise
        calls = msg.get("tool_calls") or []
        if not calls:
            return msg.get("content") or "", steps
        msgs.append({"role": "assistant", "content": msg.get("content") or "", "tool_calls": calls})
        for c in calls:
            fn = c.get("function") or {}
            name = fn.get("name", "")
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except ValueError:
                args = {}
            if not isinstance(args, dict):               # gültiges JSON, aber kein Objekt („m45“, [])
                args = {}
            t0 = time.time()
            used[name] = used.get(name, 0) + 1
            total = sum(used.values())
            try:
                if name not in by_name:
                    raise ValueError(f"unbekanntes Werkzeug {name}")
                if total > max_calls:
                    raise ValueError(f"Werkzeuglimit erreicht ({max_calls} Aufrufe je Lauf) – antworte mit den vorhandenen Ergebnissen")
                if name in limits and used[name] > limits[name]:
                    raise ValueError(f"{name} höchstens {limits[name]}× je Lauf")
                res = by_name[name].fn(**{k: v for k, v in args.items() if k in by_name[name].parameters["properties"]})
                ok = True
            except Exception as e:                       # Fehler gehen als Ergebnis an das Modell zurück
                res, ok = {"fehler": str(e)}, False
            text = json.dumps(res, ensure_ascii=False, default=str)[:6000]
            step = {"type": "tool", "tool": name, "args": args, "ok": ok, "summary": _summary(res), "ms": round((time.time() - t0) * 1000),
                    "t": time.time(), "sources": sorted(source_ids(res)) if ok else []}
            steps.append(step)
            if on_step:
                on_step(step)
            msgs.append({"role": "tool", "tool_call_id": c.get("id") or name, "content": text})
    # Schritte aufgebraucht: ohne Werkzeuge abschließen lassen
    msgs.append({"role": "user", "content": final_hint or "Keine weiteren Werkzeuge. Antworte jetzt abschließend."})
    msg = llm.chat(msgs, None, max_tokens=max_tokens, json_mode=json_mode)
    steps.append({"type": "limit", "text": f"Schrittlimit ({max_steps}) erreicht", "t": time.time()})
    return msg.get("content") or "", steps


CITING_TEXT = ("zusammenfassung", "text")          # Felder mit belegtem Enzyklopädie-Text (nicht Fremdtext wie Abstracts)


def source_ids(res, depth: int = 0) -> set[int]:
    """Quellennummern (Feld `quelle`) in einem Werkzeugergebnis – nur diese darf eine Antwort als Beleg nennen."""
    out: set[int] = set()
    if depth > 6:
        return out
    if isinstance(res, dict):
        for k, v in res.items():
            if k == "quelle" and isinstance(v, int) and not isinstance(v, bool):
                out.add(v)
            elif k in CITING_TEXT and isinstance(v, str):  # Zusammenfassung/Abschnitte der Enzyklopädie belegen selbst mit [S…]
                out |= {int(n) for n in re.findall(r"\[S(\d{1,9})\]", v)}
            else:
                out |= source_ids(v, depth + 1)
    elif isinstance(res, (list, tuple)):
        for v in res:
            out |= source_ids(v, depth + 1)
    return out


def _summary(res) -> str:
    if isinstance(res, dict) and "fehler" in res:
        return f"Fehler: {res['fehler']}"[:160]
    if isinstance(res, list):
        return f"{len(res)} Treffer"
    if isinstance(res, dict):
        return ", ".join(f"{k}" for k in list(res)[:6])
    return str(res)[:120]


def parse_json(text: str) -> dict:
    """JSON aus einer Modellantwort (auch in ```json-Blöcken oder mit Vor-/Nachtext)."""
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", (text or "").strip(), flags=re.M)
    try:
        return json.loads(t)
    except ValueError:
        pass
    a, b = t.find("{"), t.rfind("}")
    if a >= 0 and b > a:
        try:
            return json.loads(t[a:b + 1])
        except ValueError:
            pass
    raise clients.ServiceError("Die KI hat kein gültiges JSON geliefert.")
