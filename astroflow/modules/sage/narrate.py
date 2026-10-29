"""Erzählformate auf Grundlage der Enzyklopädie – Dokumentation, Geschichte, Beobachtungsbericht, Einsteiger, Deep Dive und
Audioführung durch das eigene Bild (Abschnitte je Bildobjekt; beim Abspielen wird das Objekt im Bild hervorgehoben)."""
from __future__ import annotations

import re
import time

from . import agent, clients, memory
from .research import fmt_ly

FORMATS = {
    "doku": ("Wissenschaftliche Dokumentation", "eine sachliche, gut gegliederte Dokumentation wie ein Wissenschaftsfilm – mit Zahlen aus den Fakten"),
    "story": ("Spannende Geschichte", "eine fesselnde, bildhafte Erzählung, die trotzdem fachlich stimmt"),
    "report": ("Persönlicher Beobachtungsbericht", "einen Bericht in der Du-Form über die eigenen Aufnahmen: Nächte, Belichtung, Geräte, Fortschritt – "
                                                    "verbunden mit dem, was auf dem Bild astronomisch zu sehen ist"),
    "beginner": ("Einsteigererklärung", "eine Erklärung für Einsteiger ohne Vorwissen, mit Alltagsvergleichen, kurzen Sätzen und ohne Fachjargon"),
    "deep": ("Wissenschaftlicher Deep Dive", "einen fachlichen Deep Dive auf Studienniveau: Physik, Messmethoden, aktuelle Forschung, offene Fragen"),
    "tour": ("Audioführung durch dein Bild", "eine Audioführung durch das eigene Foto, Objekt für Objekt"),
}


def context(mod, eid: str) -> tuple[str, list[dict], dict]:
    """Wissen zu einem Objekt als nummerierter Kontext (Quellen [n]) + eigene Daten."""
    kb = mod.kb
    e = kb.entity(eid)
    facts = [f for f in kb.facts(eid) if f["status"] in ("current", "conflict")]
    srcs, idx = [], {}

    def num(sid):
        if sid and sid not in idx:
            s = kb.source(sid)
            if s:
                srcs.append(s)
                idx[sid] = len(srcs)
        return idx.get(sid)

    lines = [f"- {f['key']}: {f['value']}" + (f" ({f['note']})" if f["note"] else "") + (f" [{num(f['source_id'])}]" if num(f["source_id"]) else "")
             for f in facts]
    secs = []
    for s in kb.sections(eid):
        body = re.sub(r"\[S(\d+)\]", lambda m: f"[{num(int(m.group(1))) or '?'}]", s["body"])
        secs.append(f"### {s['title']} ({'geprüft' if s['status'] == 'reviewed' else 'KI-Entwurf'})\n{body[:2500]}")
    mine = memory.for_entity(mod, eid)
    text = (f"Objekt: {e['name']} – {e['otype_label']}\nZusammenfassung: {re.sub(r'\[S\d+\]', '', e['summary'] or '')}\n\nFakten:\n" + "\n".join(lines)
            + ("\n\nWissen:\n" + "\n\n".join(secs) if secs else "")
            + ("\n\nEigene Daten des Nutzers:\n" + "\n".join(f"- {d['text']} (Grundlage: {d['basis']})" for d in mine["derived"]) if mine["derived"] else "")
            + ("\n\nNotizen des Nutzers:\n" + "\n".join(f"- {n['title']}: {n['body'][:300]}" for n in mine["notes"]) if mine["notes"] else ""))
    return text, srcs, mine


def pick_image(mine: dict) -> dict | None:
    """Bestes eigenes Bild mit Objektpositionen (für die Audioführung)."""
    imgs = [i for i in mine["images"] if (i["object"] or {}).get("x") is not None]
    return max(imgs, key=lambda i: (i["n_objects"], i["created"])) if imgs else None


def generate(mod, eid: str, fmt: str, history_id: str | None = None) -> dict:
    kb = mod.kb
    if fmt not in FORMATS:
        raise clients.ServiceError("Unbekanntes Format.")
    e = kb.entity(eid)
    text, srcs, mine = context(mod, eid)
    llm = agent.LLM(mod.config(), kb, purpose="story")
    image = None
    if fmt == "tour":
        h = mod.store.get(history_id) if history_id else None
        pic = ({"id": h["id"], "image": h.get("preview_file"), "objects": h.get("objects") or [], "width": h.get("width")} if h else None)
        if not pic:
            best = pick_image(mine)
            h = best and mod.store.get(best["id"])
            pic = h and {"id": h["id"], "image": h.get("preview_file"), "objects": h.get("objects") or [], "width": h.get("width")}
        if not pic:
            raise clients.ServiceError("Für die Audioführung braucht es ein eigenes bestimmtes Bild mit Objektmarken (Sage → Bild bestimmen).")
        image = {"entry": pic["id"], "image": pic["image"], "width": pic["width"]}
        objs = [o for o in pic["objects"] if o.get("x") is not None][:8]
        if not llm.ready():
            segs = template_tour(mod, e, objs)
            title, body, model = f"Rundgang durch dein Bild: {e['name']}", "\n\n".join(s["text"] for s in segs), "Vorlage (ohne KI)"
        else:
            obj_txt = "\n".join(f"- {o['name']} ({o.get('kind_label') or ''}): {(kb.resolve(o['name']) or {}).get('summary') or ''}"[:400] for o in objs)
            msg = llm.chat([{"role": "system", "content": "Du sprichst eine Audioführung durch ein Astrofoto. Nur belegte Fakten aus dem Kontext, "
                                                          "keine erfundenen Zahlen. Deutsch, gesprochen klingend, ohne Markdown."},
                            {"role": "user", "content": f"{text}\n\nObjekte im Bild (in dieser Reihenfolge besprechen):\n{obj_txt}\n\n"
                                                        "Antworte nur mit JSON: {\"title\": \"…\", \"segments\": [{\"object\": \"Name aus der Liste oder null\", "
                                                        "\"text\": \"2–4 Sätze\"}]} – erst eine Einleitung (object null), dann je Objekt ein Abschnitt, "
                                                        "zum Schluss ein kurzer Ausklang."}], max_tokens=2500, json_mode=True)
            d = agent.parse_json(msg.get("content") or "")
            names = {o["name"] for o in objs}
            segs = [{"object": s.get("object") if s.get("object") in names else None, "text": str(s.get("text") or "").strip()}
                    for s in d.get("segments") or [] if s.get("text")]
            title, body, model = d.get("title") or f"Führung: {e['name']}", "\n\n".join(s["text"] for s in segs), llm.model
    else:
        if not llm.ready():
            raise clients.ServiceError("Keine KI eingerichtet – Einstellungen → Sage → Erzähl-KI.")
        label, how = FORMATS[fmt]
        src_txt = "\n".join(f"[{i + 1}] {s['title']} ({s.get('year') or 'o. J.'})" for i, s in enumerate(srcs))
        msg = llm.chat([{"role": "system", "content": clients.SYSTEM + " Du nutzt ausschließlich Fakten aus dem gegebenen Kontext und erfindest "
                                                                   "keine Zahlen. Persönliche Angaben stammen nur aus „Eigene Daten“."},
                        {"role": "user", "content": f"{text}\n\nQuellen:\n{src_txt}\n\nSchreibe {how}. Format: Markdown, beginne mit „## Titel“, "
                                                    "4–7 Absätze; belege Fakten mit [n]."}], max_tokens=3000)
        body = (msg.get("content") or "").strip()
        if not body:
            raise clients.ServiceError("Die KI hat keinen Text geliefert.")
        body = re.sub(r"\[(\d+)\]", lambda m: f"[S{srcs[int(m.group(1)) - 1]['id']}]" if 0 < int(m.group(1)) <= len(srcs) else "", body)
        mt = re.search(r"^#{1,3}\s+(.+)$", body, re.M)
        title, segs, model = (mt.group(1).strip() if mt else label), [], llm.model
    sid = kb.x("INSERT INTO stories(entity_id, format, title, body, segments, image, model, created) VALUES(?,?,?,?,?,?,?,?)",
               (eid, fmt, title, body, kb._j(segs), kb._j(image or {}), model, time.time()))
    return kb.one("SELECT * FROM stories WHERE id=?", (sid,))


def template_tour(mod, e: dict, objs: list[dict]) -> list[dict]:
    """Führung ohne Sprachmodell – nur aus Katalogwerten."""
    kb = mod.kb
    segs = [{"object": None, "text": f"Willkommen zu deinem Bild von {e['name']}. Wir gehen gemeinsam durch die {len(objs)} "
                                     f"markierten Objekte."}]
    for o in objs:
        x = kb.resolve(o["name"])
        d = x and kb.fact(x["id"], "distance_ly")
        label = (x and x.get("otype_label")) or o.get("kind_label") or ""
        t = f"Hier siehst du {o['name']}" + (f" ({label})" if label and label != "Allgemein" else "")
        t += f" – rund {d['value']} entfernt." if d else "."
        if x and x.get("summary") and (x["data"] or {}).get("summary_origin") != "catalog":
            t += " " + re.sub(r"\s*\[S\d+\]", "", x["summary"])
        segs.append({"object": o["name"], "text": t})
    segs.append({"object": None, "text": "Das war die Führung durch dein Bild. Jedes Objekt kannst du anklicken, um mehr zu erfahren."})
    return segs


def speak_text(story: dict, segment: int | None) -> str:
    if segment is not None:
        segs = story.get("segments") or []
        if not 0 <= segment < len(segs):
            raise clients.ServiceError("Abschnitt nicht vorhanden.")
        return segs[segment]["text"]
    from .store import plain
    return re.sub(r"\s*\[S\d+\]", "", plain(story["body"]))


__all__ = ["FORMATS", "generate", "speak_text", "fmt_ly"]
