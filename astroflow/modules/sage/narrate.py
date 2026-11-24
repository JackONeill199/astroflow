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
        # höchstens 6 Objekte, vom nächsten zum fernsten („Flug in die Tiefe“); ohne Entfernung ans Ende
        facts = sorted((object_facts(mod, o) for o in pic["objects"] if o.get("x") is not None),
                       key=lambda f: (f["ly"] is None, f["ly"] or 0))[:6]
        objs = [f["obj"] for f in facts]
        if not llm.ready():
            segs = template_tour(mod, e, objs, facts)
            title, body, model = f"Rundgang durch dein Bild: {e['name']}", "\n\n".join(s["text"] for s in segs), "Vorlage (ohne KI)"
        else:
            obj_txt = "\n".join(f"- {f['name']} ({f['kind']})" + (f", Entfernung {f['dist']}" if f["dist"] else "")
                                + (f", Durchmesser {f['diam']}" if f["diam"] else "") + (f", Sternbild {f['con']}" if f["con"] else "")
                                + (f": {f['summary']}" if f["summary"] else "") for f in facts)
            msg = llm.chat([{"role": "system", "content": "Du sprichst eine kurze, lebendige Führung durch ein Astrofoto. Nur Fakten aus dem Kontext, "
                                                          "keine erfundenen Zahlen; Vergleiche nur, wenn sie sich aus den gegebenen Zahlen ausrechnen lassen. "
                                                          "Deutsch, gesprochen klingend, ohne Markdown."},
                            {"role": "user", "content": f"{text}\n\nObjekte im Bild (vom nächsten zum fernsten, in dieser Reihenfolge besprechen):\n{obj_txt}\n\n"
                                                        "Antworte nur mit JSON: {\"title\": \"…\", \"segments\": [{\"object\": \"Name aus der Liste oder null\", "
                                                        "\"text\": \"…\"}]} – Einleitung (object null, 1–2 Sätze), je Objekt 2–3 Sätze mit einem "
                                                        "überraschenden, aber belegbaren Bild (Lichtlaufzeit, wahre Größe, Vergleich zu einem anderen Objekt im Bild), "
                                                        "zum Schluss 1 Satz Ausklang. Insgesamt kurz – eine Führung von etwa zwei Minuten."}], max_tokens=2000, json_mode=True)
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


def object_facts(mod, o: dict) -> dict:
    """Belegte Angaben zu einem Bildobjekt aus der Enzyklopädie (Entfernung, Durchmesser, Sternbild, Kurztext)."""
    kb = mod.kb
    x = kb.resolve(o["name"])
    f = (lambda k: kb.fact(x["id"], k)) if x else (lambda k: None)
    d = f("distance_ly")
    summ = ""
    if x and x.get("summary") and (x["data"] or {}).get("summary_origin") != "catalog":
        summ = re.split(r"(?<=[.!?])\s", re.sub(r"\s*\[S\d+\]", "", x["summary"]).strip(), maxsplit=1)[0]
    return {"obj": o, "name": o["name"], "entity": x and x["id"],
            "kind": (x and x.get("otype_label")) or o.get("kind_label") or "",
            "ly": float(d["num"]) if d and d.get("num") else None, "dist": d and d["value"],
            "diam": (f("diameter") or {}).get("value"), "con": (f("constellation") or {}).get("value"), "summary": summ}


def _round2(v: float) -> int:
    """Auf zwei geltende Ziffern runden (1234 → 1200) – Entfernungen sind nie genauer."""
    if v < 100:
        return int(round(v))
    k = 10 ** (len(str(int(v))) - 2)
    return int(round(v / k) * k)


def _dots(n: int) -> str:
    return f"{n:,}".replace(",", ".")


def light_age(ly: float, year: int | None = None) -> str:
    """Wann ist das Licht losgeflogen? Reine Rechnung aus der Entfernung (Lichtjahre = Jahre Laufzeit)."""
    year = year or time.localtime().tm_year
    start = year - ly
    if start > 0:
        return f"Das Licht, das dein Sensor eingefangen hat, ist um das Jahr {_round2(start) if start >= 100 else int(start)} losgeflogen."
    years = fmt_ly(ly).replace("Lichtjahre", "Jahre")
    if ly < 300_000:
        return f"Sein Licht war rund {_dots(_round2(ly))} Jahre unterwegs – es startete um {_dots(_round2(-start))} v. Chr."
    if ly < 66e6:
        return f"Sein Licht war {years} unterwegs – als es losflog, gab es noch keine Menschen."
    return f"Sein Licht war {years} unterwegs – es startete noch vor dem Ende der Dinosaurier."


def template_tour(mod, e: dict, objs: list[dict], facts: list[dict] | None = None) -> list[dict]:
    """Führung ohne Sprachmodell – kurz, nur aus belegten Katalogwerten, vom nächsten zum fernsten Objekt; dazu
    Lichtlaufzeit, wahre Größe, eigene Aufnahmen und ein Tiefenvergleich (alles ausgerechnet, nichts erfunden)."""
    facts = facts or [object_facts(mod, o) for o in objs]
    known = [f for f in facts if f["ly"]]
    intro = f"Dein Bild von {e['name']}: {len(facts)} Objekt{'' if len(facts) == 1 else 'e'} im Bildfeld."
    if len(known) >= 2:
        intro += f" Wir fliegen vom nächsten ({known[0]['dist']}) bis zum fernsten ({known[-1]['dist']})."
    segs = [{"object": None, "text": intro}]
    for f in facts:
        t = f["name"] + (f" – {f['kind']}" if f["kind"] and f["kind"] != "Allgemein" else "") + (f" im Sternbild {f['con']}" if f["con"] else "") + "."
        if f["summary"]:
            t += " " + f["summary"]
        if f["ly"]:
            t += " " + light_age(f["ly"])
        if f["diam"]:
            t += f" Durchmesser: {f['diam'].lstrip('≈ ')}."
        if f["entity"]:
            mine = memory.for_entity(mod, f["entity"]).get("derived") or []
            if mine:
                t += " " + mine[0]["text"]
        segs.append({"object": f["name"], "text": t})
    if len(known) >= 2 and known[-1]["ly"] / known[0]["ly"] >= 2:
        near, far = known[0], known[-1]
        ratio = far["ly"] / near["ly"]
        rtxt = _dots(_round2(ratio)) if ratio >= 10 else f"{ratio:.1f}".replace(".", ",")
        segs.append({"object": far["name"], "text": f"Tiefenblick: {far['name']} ist {rtxt}-mal so weit weg wie {near['name']}. "
                                                    "Auf deinem Bild stehen beide nebeneinander – in Wirklichkeit liegen Welten dazwischen."})
    segs.append({"object": None, "text": "Tipp: Tippe auf eine freie Stelle im Bild – Sage schaut in den Katalogen nach, was dort liegt."})
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
