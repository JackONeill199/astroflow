"""„Frag Sage“: kontextbewusster Assistent mit Werkzeugen (Enzyklopädie, eigene Daten, SIMBAD, arXiv, Rechner).

Antworten unterscheiden ausdrücklich: Fakten mit Quelle [S<id>], persönliche Daten („Deine Daten“) und eigene
Interpretationen („Interpretation“). Der Verlauf wird je Kontext (Objekt, Bild, allgemein) gespeichert."""
from __future__ import annotations

import ast
import math
import operator
import re
import time

from . import agent, clients, memory, sources
from .research import Researcher
from .sources import PC_LY, SourceError

UNITS = {"ly": 1.0, "lj": 1.0, "pc": PC_LY, "kpc": PC_LY * 1e3, "mpc": PC_LY * 1e6, "au": 1.581e-5, "ae": 1.581e-5,
         "km": 1.057e-13, "c": 299792.458, "pi": math.pi, "e": math.e}
OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv, ast.Pow: operator.pow,
       ast.USub: operator.neg, ast.UAdd: operator.pos}
FUNCS = {"sqrt": math.sqrt, "log10": math.log10, "ln": math.log, "sin": lambda d: math.sin(math.radians(d)),
         "cos": lambda d: math.cos(math.radians(d)), "tan": lambda d: math.tan(math.radians(d)), "atan": lambda x: math.degrees(math.atan(x))}


def calculate(expression: str) -> dict:
    """Sicherer Rechner: Zahlen, + − × ÷ ^, sqrt/log10/ln/sin/cos/tan (Grad), Einheiten ly, pc, kpc, Mpc, au, km (→ Lichtjahre), c (km/s)."""
    def ev(n):
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)):
            return n.value
        if isinstance(n, ast.BinOp) and type(n.op) in OPS:
            return OPS[type(n.op)](ev(n.left), ev(n.right))
        if isinstance(n, ast.UnaryOp) and type(n.op) in OPS:
            return OPS[type(n.op)](ev(n.operand))
        if isinstance(n, ast.Name) and n.id.lower() in UNITS:
            return UNITS[n.id.lower()]
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in FUNCS and len(n.args) == 1:
            return FUNCS[n.func.id](ev(n.args[0]))
        raise ValueError("nicht erlaubter Ausdruck")
    expr = expression.replace("^", "**").replace("×", "*").replace("÷", "/").replace(",", ".")
    expr = re.sub(r"(\d)\s*(ly|lj|pc|kpc|mpc|au|ae|km)\b", r"\1*\2", expr, flags=re.I)
    if len(expr) > 200:
        raise ValueError("Ausdruck zu lang")
    return {"ausdruck": expression, "ergebnis": ev(ast.parse(expr, mode="eval").body)}


SYSTEM = ("Du bist Sage, ein astronomischer Experte und persönlicher Forschungsbegleiter in der Software AstroFlow. Du hast Werkzeuge "
          "für die Enzyklopädie, die eigenen Aufnahmen des Nutzers, SIMBAD, arXiv und einen Rechner. Wähle passende Werkzeuge, bevor du "
          "antwortest; erfinde keine Messwerte. Kennzeichne in der Antwort klar: Fakten mit Quellenangabe [S<nummer>] (Nummern aus den "
          "Werkzeugergebnissen, Feld `quelle`), Angaben aus den Daten des Nutzers mit „(Deine Daten)“ und eigene Schlussfolgerungen mit "
          "„(Interpretation)“. Ist etwas unsicher oder in der Forschung umstritten, sag das. Antworte auf Deutsch in Markdown, "
          "präzise und verständlich; passe die Tiefe der Frage an.")


def tools_for(mod, r: Researcher) -> list[agent.Tool]:
    kb = mod.kb

    def search(query: str):
        return [{"id": e["id"], "name": e["name"], "typ": e["otype_label"], "zusammenfassung": (e["summary"] or "")[:240]}
                for e in kb.search(query, 8)]

    def entry(id: str):
        e = kb.resolve(id)
        if not e:
            return {"fehler": "kein Eintrag – mit research_object anlegen"}
        facts = [{"wert": f["key"], "inhalt": f["value"], "quelle": f["source_id"], "status": f["status"], "hinweis": f["note"]}
                 for f in kb.facts(e["id"]) if f["status"] in ("current", "conflict")]
        secs = [{"titel": s["title"], "text": s["body"][:1800], "status": "KI-Entwurf (ungeprüft)" if s["status"] == "draft" else "geprüft"}
                for s in kb.sections(e["id"])]
        rel = [{"von": x["src"], "beziehung": x["rel"], "zu": x["dst"], "status": x["status"]} for x in kb.edges(e["id"])][:20]
        papers = [{"quelle": s["id"], "titel": s["title"], "jahr": s["year"]} for s in kb.sources((e["data"] or {}).get("papers", [])[:8])]
        return {"id": e["id"], "name": e["name"], "typ": e["otype_label"], "zusammenfassung": e["summary"], "fakten": facts,
                "abschnitte": secs, "beziehungen": rel, "artikel": papers}

    def mine(id: str):
        e = kb.resolve(id)
        if not e:
            return {"fehler": "kein Eintrag"}
        m = memory.for_entity(mod, e["id"])
        return {"aussagen": m["derived"], "nächte": [{k: s[k] for k in ("night", "frames", "used", "used_s", "instruments", "filters", "fwhm_px")}
                                                    for p in m["projects"] for s in p["sessions"]],
                "versionen": [r for p in m["projects"] for r in p["results"]],
                "bilder": [{"id": i["id"], "titel": i["title"], "weitere_objekte": i["others"]} for i in m["images"]],
                "notizen": [{"art": n["kind"], "titel": n["title"], "text": n["body"][:400], "datum": n["date"]} for n in m["notes"]]}

    def profile():
        p = memory.profile(mod)
        return {"objekte": p["objects"][:40], "objektarten": p["kinds"], "geräte": p["instruments"], "interessen": p["interests"]}

    def image(history_id: str):
        h = mod.store.get(history_id)
        if not h:
            return {"fehler": "Bild nicht gefunden"}
        sol = h.get("solution") or {}
        return {"titel": h.get("title"), "bildmitte": [sol.get("ra"), sol.get("dec")], "bildfeld_grad": [sol.get("fov_w"), sol.get("fov_h")],
                "objekte": [{"name": o["name"], "typ": o.get("kind_label"), "größe_bogenmin": o.get("size"),
                             "eintrag": (kb.resolve(o["name"]) or {}).get("id")} for o in h.get("objects") or []],
                "quelle": h.get("source")}

    def simbad(name: str):
        o = r.simbad.resolve(name)
        if not o:
            return {"fehler": "nicht in SIMBAD"}
        sid = kb.add_source(f"simbad:{o['main_id']}", "catalog", f"SIMBAD – {' '.join(o['main_id'].split())}", url=r.simbad.source_url(o["main_id"]))
        return {"quelle": sid, "kennung": o["main_id"], "typ": o["otype_label"], "ra": o["ra"], "dec": o["dec"], "parallaxe_mas": o.get("plx_value"),
                "entfernung_lj_aus_parallaxe": round(1000 / o["plx_value"] * PC_LY) if o.get("plx_value") else None,
                "spektraltyp": o.get("sp_type"), "größe_bogenmin": o.get("galdim_majaxis"), "kennungen": o["ids"][:10]}

    def arxiv(query: str):
        out = []
        for p in sources.arxiv_search(r.net, query, limit=5):
            sid = kb.add_source(f"arxiv:{p['id']}", "preprint", p["title"], url=p["url"], authors=", ".join(p["authors"]),
                                year=int(p["published"][:4]), publisher="arXiv", abstract=p["abstract"])
            out.append({"quelle": sid, "titel": p["title"], "datum": p["published"], "abstract": p["abstract"][:600]})
        return out

    def research(name: str):
        e = r.research(name, deep=False)
        return {"id": e["id"], "name": e["name"], "typ": e["otype_label"], "zusammenfassung": e["summary"]}

    def sources_of(ids: str):
        return [{"quelle": s["id"], "titel": s["title"], "jahr": s["year"], "abstract": (s["abstract"] or "")[:900]}
                for s in kb.sources(re.findall(r"\d+", ids)[:5])]

    return [agent.tool("search_encyclopedia", "Enzyklopädie durchsuchen (Namen, Kennungen, Begriffe).", search, query=("string", "Suchtext")),
            agent.tool("get_entry", "Eintrag mit Fakten (mit Quellennummer), Abschnitten, Beziehungen und Artikeln.", entry,
                       id=("string", "Eintrags-ID oder Kennung, z. B. 'm45'")),
            agent.tool("my_observations", "Eigene Aufnahmen des Nutzers zu einem Objekt: Nächte, Belichtung, Geräte, Versionen, Notizen.", mine,
                       id=("string", "Eintrags-ID oder Kennung")),
            agent.tool("my_profile", "Überblick über alle Aufnahmen, bevorzugte Objektarten, Geräte und Interessen des Nutzers.", profile),
            agent.tool("image_objects", "Objekte in einem bestimmten Bild des Nutzers (Sage-Bildbestimmung).", image,
                       history_id=("string", "ID des Bildes")),
            agent.tool("read_sources", "Titel und Abstracts von Quellen lesen.", sources_of, ids=("string", "Quellennummern, z. B. '12, 15'")),
            agent.tool("simbad_lookup", "Objekt im Fachkatalog SIMBAD nachschlagen.", simbad, name=("string", "Kennung")),
            agent.tool("arxiv_search", "Neueste Fachartikel auf arXiv suchen (englische Begriffe).", arxiv, query=("string", "Suchbegriff")),
            agent.tool("research_object", "Neues Objekt aus dem Fachkatalog in die Enzyklopädie aufnehmen.", research, name=("string", "Kennung")),
            agent.tool("calculate", "Rechnen (z. B. Lichtlaufzeit, Größe aus Winkel und Entfernung). Einheiten werden in Lichtjahre umgerechnet.",
                       lambda expression: calculate(expression), expression=("string", "Ausdruck, z. B. '444 * tan(1.8)'"))]


def ask(mod, question: str, scope: str = "global", entity: str | None = None, image: str | None = None) -> dict:
    kb = mod.kb
    llm = agent.LLM(mod.config(), kb, purpose="chat")
    if not llm.ready():
        raise clients.ServiceError("Keine KI eingerichtet – Einstellungen → Sage → Erzähl-KI.")
    r = Researcher(mod)
    ctx = []
    if entity:
        e = kb.entity(entity)
        if e:
            ctx.append(f"Der Nutzer betrachtet gerade den Enzyklopädie-Eintrag „{e['name']}“ (id {e['id']}).")
    if image:
        h = mod.store.get(image)
        if h:
            ctx.append(f"Der Nutzer betrachtet sein Bild „{h.get('title')}“ (history_id {image}) mit den Objekten: "
                       + ", ".join(o["name"] for o in (h.get("objects") or [])[:15]))
    hist = kb.q("SELECT role, content FROM chats WHERE scope=? ORDER BY id DESC LIMIT 8", (scope,))[::-1]
    convo = "\n".join(f"{'Nutzer' if m['role'] == 'user' else 'Sage'}: {m['content'][:800]}" for m in hist)
    user = ("\n".join(ctx) + "\n\n" if ctx else "") + (f"Bisheriges Gespräch:\n{convo}\n\n" if convo else "") + f"Frage: {question}"
    run = kb.start_run("chat", entity, llm.model)
    llm.run_id = run
    kb.x("INSERT INTO chats(scope, role, content, created) VALUES(?,?,?,?)", (scope, "user", question, time.time()))
    try:
        answer, steps = agent.run_agent(llm, SYSTEM, user, tools_for(mod, r), max_steps=int(mod.config().get("max_steps") or 6), max_tokens=2500)
    except Exception as e:
        kb.finish_run(run, "error", [], llm.cost, llm.tokens, str(e))
        raise
    cited = sorted({int(n) for n in re.findall(r"\[S(\d+)\]", answer)})
    srcs = [{"id": s["id"], "title": s["title"], "kind": s["kind"], "url": s["url"], "year": s["year"]} for s in kb.sources(cited)]
    data = {"steps": [{k: s.get(k) for k in ("tool", "args", "summary", "ok")} for s in steps if s.get("type") == "tool"],
            "sources": srcs, "cost": round(llm.cost, 5), "model": llm.model}
    kb.x("INSERT INTO chats(scope, role, content, data, created) VALUES(?,?,?,?,?)", (scope, "assistant", answer, kb._j(data), time.time()))
    kb.finish_run(run, "done", steps, llm.cost, llm.tokens)
    return {"answer": answer, **data}


def history(kb, scope: str) -> list[dict]:
    return kb.q("SELECT id, role, content, data, created FROM chats WHERE scope=? ORDER BY id", (scope,))


__all__ = ["ask", "history", "calculate", "SourceError"]
