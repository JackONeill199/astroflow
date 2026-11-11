"""REST der Enzyklopädie unter /api/sage/kb: Suche, Objektseiten, Recherche, Notizen, Wissensnetz, Entdeckungen, Chat,
Erzählformate und Himmelsbilder. Wird von `api.build_router` an den Sage-Router gehängt."""
from __future__ import annotations

import re
import secrets
import time

from fastapi import Body, HTTPException
from fastapi.responses import Response

from . import agent, clients, memory, narrate, sources
from .kb import ENTITY_STATUS, norm
from .research import CONCEPTS, Researcher, seed_concepts
from .sources import SourceError

FACT_LABELS = {"otype": "Objekttyp", "constellation": "Sternbild", "distance_ly": "Entfernung", "parallax": "Parallaxe", "ra": "Rektaszension",
               "dec": "Deklination", "size": "Ausdehnung", "sp_type": "Spektraltyp", "morph_type": "Morphologie", "redshift": "Rotverschiebung",
               "radvel": "Radialgeschwindigkeit", "nbref": "Fachartikel in SIMBAD", "distance": "Entfernungsmessung",
               "mag": "Helligkeit", "diameter": "Durchmesser"}
NOTE_KINDS = ("note", "plan", "observation", "interest")


def extend(r, mod, url):
    kb = mod.kb
    seed_concepts(kb)

    def task_view(t: dict) -> dict:
        return {k: v for k, v in t.items() if k != "cancel"}

    def need(eid: str) -> dict:
        e = kb.entity(eid) or kb.resolve(eid)
        if not e:
            raise HTTPException(404, "Eintrag nicht gefunden.")
        return e

    def picture_index() -> dict[str, dict]:
        """Objekt-ID → bestes eigenes Bild (Sage-Bildbestimmung oder entwickeltes Ergebnis aus der Galerie)."""
        out: dict[str, dict] = {}
        for p in memory.observation_log(mod):
            e = memory._match(kb, p["name"], p.get("ra"), p.get("dec"))
            if e and p["results"]:
                out.setdefault(e["id"], {"thumb": f"/api/stacking/results/{p['results'][-1]['id']}/thumb", "kind": "project"})
        for h in mod.store.entries():
            for o in h.get("objects") or []:
                e = kb.resolve(o["name"])
                if e and e["id"] not in out and h.get("preview_file"):
                    out[e["id"]] = {"thumb": url(h["preview_file"]), "kind": "image"}
        return out

    def card(e: dict, pics: dict | None = None, mine: set | None = None) -> dict:
        d = kb.fact(e["id"], "distance_ly")
        return {"id": e["id"], "name": e["name"], "kind": e["kind"], "otype_label": e["otype_label"], "status": e["status"],
                "status_label": ENTITY_STATUS.get(e["status"], e["status"]), "summary": e["summary"], "af_kind": (e["data"] or {}).get("af_kind"),
                "distance": d and d["value"], "distance_conflict": bool(d and d["status"] == "conflict"),
                "constellation": (kb.fact(e["id"], "constellation") or {}).get("value"), "stale": bool((e["data"] or {}).get("stale")),
                "image": (pics or {}).get(e["id"], {}).get("thumb"), "observed": e["id"] in (mine or set()), "updated": e["updated"]}

    def observed_ids() -> set:
        return {o["entity"] for o in memory.profile(mod)["objects"] if o["entity"]}

    # ---- Suche & Liste ------------------------------------------------------------------
    @r.get("/kb/entities")
    def entities(q: str = "", filter: str = "all", limit: int = 200):
        pics, mine = picture_index(), observed_ids()
        rows = kb.search(q, limit) if q else kb.q("SELECT * FROM entities WHERE kind='object' ORDER BY updated DESC LIMIT ?", (limit,))
        items = [card(e, pics, mine) for e in rows]
        hidden = 0
        if filter == "all" and not q:
            # Grundeinträge ohne eigenes Bild (meist SIMBAD-Gruppen aus der Hierarchie) nur über Suche/Wissensnetz – füllten sonst das Raster
            shown = [i for i in items if i["status"] != "stub" or i["image"] or i["observed"]]
            hidden, items = len(items) - len(shown), shown
        if filter == "observed":
            items = [i for i in items if i["observed"]]
        elif filter == "ai":
            items = [i for i in items if i["status"] == "researched"]
        elif filter == "concepts":
            items = [card(e) for e in kb.q("SELECT * FROM entities WHERE kind='concept' ORDER BY name")]
        stats = kb.one("SELECT COUNT(*) AS n, SUM(status='researched') AS ai, SUM(status='reviewed') AS ok FROM entities WHERE kind='object'")
        return {"items": items, "stats": {"objects": stats["n"], "researched": stats["ai"] or 0, "reviewed": stats["ok"] or 0,
                                          "observed": len(mine), "hidden_stubs": hidden, "sources": kb.one("SELECT COUNT(*) AS n FROM sources")["n"]}}

    @r.get("/kb/card")
    def knowledge_card(name: str):
        """Kompakte Wissenskarte beim Klick auf ein Objekt im Bild (legt bei Bedarf einen Grundeintrag an)."""
        e = kb.resolve(name) or Researcher(mod).ensure_stub(name)
        if not e and mod.config().get("catalog_online", True):
            try:                                          # nicht im lokalen Katalog: direkt in SIMBAD nachschlagen (ohne KI, ~1–2 s)
                e = Researcher(mod).research(name, deep=False)
            except Exception:
                e = None
        if not e:
            return {"found": False, "name": name}
        m = memory.for_entity(mod, e["id"])
        return {"found": True, **card(e), "facts": [{"label": FACT_LABELS.get(f["key"], f["key"]), "value": f["value"], "status": f["status"]}
                                                    for f in kb.facts(e["id"]) if f["status"] in ("current", "conflict")
                                                    and f["key"] in ("otype", "distance_ly", "constellation", "size", "sp_type")],
                "mine": m["derived"][:2], "researching": e["id"] in mod._research_q}

    # ---- Objektseite ---------------------------------------------------------------------
    @r.get("/kb/entities/{eid}")
    def entity(eid: str):
        e = need(eid)
        eid = e["id"]
        facts = kb.facts(eid)
        secs = kb.sections(eid)
        data = e["data"] or {}
        cited = {int(n) for s in secs for n in re.findall(r"\[S(\d+)\]", s["body"])} | {int(n) for n in re.findall(r"\[S(\d+)\]", e["summary"] or "")}
        src_ids = {f["source_id"] for f in facts if f["source_id"]} | cited | set(data.get("papers") or []) | set(data.get("preprints") or [])
        srcs = {s["id"]: {k: s[k] for k in ("id", "key", "kind", "title", "url", "authors", "year", "publisher", "abstract", "retrieved")}
                for s in kb.sources(src_ids)}
        neigh, pics, mine_ids = [], picture_index(), observed_ids()
        for x in kb.edges(eid):
            other = kb.entity(x["dst"] if x["src"] == eid else x["src"])
            if other:
                neigh.append({**x, "dir": "out" if x["src"] == eid else "in", "other": card(other, pics, mine_ids)})
        mine = memory.for_entity(mod, eid)
        images = gallery(mine)
        hero = data.get("hero")
        if hero:
            images.sort(key=lambda i: i["key"] != hero)
        notes_n = {k: sum(n["kind"] == k for n in mine["notes"]) for k in NOTE_KINDS}
        runs = kb.q("SELECT id, kind, state, started, finished, cost, tokens, model, error, steps FROM runs WHERE entity_id=? AND kind='research' "
                    "ORDER BY id DESC LIMIT 5", (eid,))
        return {"entity": {**card(e, pics, mine_ids), "main_id": e["main_id"], "otype": e["otype"], "ra": e["ra"], "dec": e["dec"],
                           "size": e["size"], "data": {k: v for k, v in data.items() if k in ("summary_origin", "summary_flags", "uncertainties", "af_kind", "nbref")},
                           "refreshed": e["refreshed"], "aliases": kb.aliases(eid)},
                "facts": [{**f, "label": FACT_LABELS.get(f["key"], f["key"])} for f in facts],
                "sections": secs, "sources": srcs, "papers": [srcs[i] for i in data.get("papers") or [] if i in srcs],
                "preprints": [srcs[i] for i in data.get("preprints") or [] if i in srcs], "edges": neigh,
                "mine": {**mine, "images": [{**i, "image": url(i["image"])} for i in mine["images"]]}, "gallery": images,
                "notes_n": notes_n, "designations": designations(eid),
                "stories": stories_of(eid), "runs": runs, "researching": eid in mod._research_q,
                "formats": {k: v[0] for k, v in narrate.FORMATS.items()}, "llm": agent.LLM(mod.config(), kb).ready()}

    def gallery(mine: dict) -> list[dict]:
        """Eigene Aufnahmen eines Objekts, neueste zuerst – je Aufnahme nur ein Bild: Versionen aus denselben Frames
        (gleiche Bildzahl und Belichtung = nur neu entwickelt) und Bildbestimmungen eines Galerie-Ergebnisses werden zusammengefasst.
        Bevorzugt wird das bestimmte Bild (es trägt die Objektmarken), sonst die neueste Version."""
        groups: dict[str, dict] = {}
        of_result: dict[int, str] = {}
        for p in mine["projects"]:
            inst = sorted({i for s in p["sessions"] for i in s["instruments"]})
            ses = sorted([s for s in p["sessions"] if s.get("used")], key=lambda s: s["night"] or "")
            cum = [sum(s["used"] for s in ses[:i + 1]) for i in range(len(ses))]
            for r in sorted(p["results"], key=lambda r: r["created"] or 0):
                # welche Nächte deckt die Version ab? (gestackte Frames kumuliert nach Nacht; Teilstacks einer Nacht zählen zu ihr)
                k = next((i + 1 for i, c in enumerate(cum) if c >= 0.8 * (r["n_frames"] or 0)), len(ses)) or 1
                nights = [s["night"] for s in ses[:k]]
                gk = f"p{p['id']}-{'+'.join(nights) or round(r['exposure_s'] or 0)}"
                of_result[r["id"]] = gk
                groups[gk] = {"key": f"r{r['id']}", "kind": "result", "src": f"/api/stacking/results/{r['id']}/jpg",
                              "thumb": f"/api/stacking/results/{r['id']}/thumb", "created": r["created"], "minutes": round((r["exposure_s"] or 0) / 60),
                              "instrument": ", ".join(inst), "href": f"#/p/{p['id']}/versionen", "title": p["name"],
                              "nights": nights, "versions": groups.get(gk, {}).get("versions", 0) + 1}
        for i in sorted(mine["images"], key=lambda i: i["created"]):
            if not i["image"]:
                continue
            t = time.mktime(time.strptime(i["created"][:19], "%Y-%m-%dT%H:%M:%S")) - time.timezone
            src = i.get("source") or {}
            gk = of_result.get(src.get("result")) or (f"p{src['project']}-x-{round(src.get('exposure') or 0)}" if src.get("project") else f"h{i['id']}")
            base = groups.get(gk, {})
            groups[gk] = {**base, "key": f"h{i['id']}", "kind": "image", "src": url(i["image"]), "thumb": url(i["image"]),
                          "created": base.get("created") or t, "minutes": base.get("minutes") or round((src.get("exposure") or 0) / 60) or None,
                          "entry": i["id"], "href": f"#/sage/{i['id']}", "title": i["title"]}
        return sorted(groups.values(), key=lambda x: -(x["created"] or 0))

    def designations(eid: str) -> list[str]:
        """Gängige Bezeichnungen: Messier, NGC/IC, gebräuchliche Namen."""
        al = kb.aliases(eid)
        e = kb.entity(eid)
        cat = [a for a in al if re.match(r"^(M|NGC|IC|Mel|Cr|Sh2?|C) ?-?\d", a)]
        names = (e["data"] or {}).get("common_names") or [a[5:] for a in al if a.startswith("NAME ")]
        out, seen = [], set()
        for a in [*sorted(cat, key=lambda a: (not a.startswith("M "), not a.startswith("M"), len(a))), *names]:
            m = re.match(r"^(M|NGC|IC|Mel|Cr|Sh2?|C) ?-?(\d+)$", a.strip())
            if not m and re.match(r"^(M|NGC|IC|Mel|Cr|Sh2?|C) ?-?\d", a):     # „M63 Sonnenblumengalaxie“ = Anzeigename
                continue
            if m:
                a = f"M{m.group(2)}" if m.group(1) == "M" else f"{m.group(1)} {m.group(2)}"
            if norm(a) not in seen:
                seen.add(norm(a))
                out.append(a)
        return out[:6]

    @r.get("/kb/featured")
    def featured():
        """Objekte für das Dashboard der Startseite: zuletzt fotografierte zuerst."""
        objs = [o for o in memory.profile(mod)["objects"] if o["entity"]]
        imgs = {}
        for h in mod.store.entries():
            for o in h.get("objects") or []:
                e = kb.resolve(o["name"])
                if e:
                    imgs.setdefault(e["id"], h["created"][:10])
        objs.sort(key=lambda o: max(o.get("last") or "", imgs.get(o["entity"], "")), reverse=True)
        return [{"id": o["entity"], "name": o["name"], "last": o.get("last")} for o in objs]

    @r.get("/kb/home")
    def home():
        """Startseite der Enzyklopädie: Fokus-Objekte (zuletzt fotografiert, mit Aufnahmedaten und großem Bild) und
        „Heute Nacht am Himmel“ (Kulmination um Mitternacht aus der Sonnen-RA, ohne Standort für 50° N)."""
        from .insights import sun_ra
        pics, mine = picture_index(), observed_ids()
        prof = {o["entity"]: o for o in memory.profile(mod)["objects"] if o["entity"]}
        feat = []
        for f in featured()[:10]:
            e = kb.entity(f["id"])
            if not e:
                continue
            c, o = card(e, pics, mine), prof.get(f["id"], {})
            thumb = c["image"] or ""
            feat.append({**c, "last": f["last"], "nights": o.get("nights", 0), "hours": round(o.get("hours", 0.0), 1),
                         "picture": thumb[:-6] + "/jpg" if thumb.startswith("/api/stacking/results/") and thumb.endswith("/thumb") else thumb})
        try:
            lat, assumed = float(str(mod.config().get("observer_lat") or "").replace(",", ".")), False
        except ValueError:
            lat, assumed = 50.0, True
        mid = (sun_ra() + 180) % 360
        cand = {}
        cat = mod.ctx.service("catalog_objects")
        pts = [(c["id"], c["display"], c["ra"], c["dec"], c["kind"], c["kind_label"], c["size"]) for c in (cat() if cat else [])]
        pts += [(x["id"], x["name"], x["ra"], x["dec"], (x["data"] or {}).get("af_kind"), x["otype_label"], 0)
                for x in kb.q("SELECT id, name, ra, dec, data, otype_label FROM entities WHERE kind='object' AND ra IS NOT NULL")
                if x["id"] in mine]
        for cid, name, ra, dec, kind, label, size in pts:
            alt, dra = 90 - abs(lat - dec), (ra - mid + 180) % 360 - 180
            if alt < 30 or abs(dra) > 75:
                continue
            e = kb.entity(cid) or kb.resolve(cid)
            key = e["id"] if e else cid
            if key in cand:
                continue
            seen = bool(e and e["id"] in mine)
            cand[key] = {"id": e and e["id"], "name": e["name"] if e else name, "kind": kind, "kind_label": label, "alt": round(alt),
                         "transit": round((24 + dra / 15) % 24, 1) % 24, "mine": seen, "image": e and pics.get(e["id"], {}).get("thumb"),
                         "score": alt / 90 - abs(dra) / 150 + min(size or 0, 60) / 150 + (0.25 if seen else 0)}
        tonight = sorted(cand.values(), key=lambda c: -c["score"])[:8]
        return {"featured": feat, "tonight": {"lat": lat, "assumed": assumed, "items": tonight}}

    @r.get("/kb/skymap/{eid}")
    def skymap(eid: str, radius: float = 22):
        """Umgebung am Himmel: helle Katalogobjekte und eigene Objekte im Umkreis (für die Positionskarte)."""
        e = need(eid)
        if e["ra"] is None:
            return {"center": None, "objects": []}
        from .research import angular_sep
        cat = mod.ctx.service("catalog_objects")
        mine = observed_ids()
        pts = {}
        for c in (cat() if cat else []):
            if angular_sep(e["ra"], e["dec"], c["ra"], c["dec"]) <= radius:
                x = kb.resolve(c["id"])
                pts[(x or {}).get("id") or c["id"]] = {"id": (x or {}).get("id"), "name": c["display"], "ra": c["ra"], "dec": c["dec"],
                                                       "kind": c["kind"], "mine": bool(x and x["id"] in mine)}
        for x in kb.q("SELECT id, name, ra, dec, data FROM entities WHERE kind='object' AND ra IS NOT NULL"):
            if x["id"] not in pts and angular_sep(e["ra"], e["dec"], x["ra"], x["dec"]) <= radius and (x["id"] in mine or x["id"] == e["id"]):
                pts[x["id"]] = {"id": x["id"], "name": x["name"], "ra": x["ra"], "dec": x["dec"], "kind": (x["data"] or {}).get("af_kind"),
                                "mine": x["id"] in mine}
        pts.setdefault(e["id"], {"id": e["id"], "name": e["name"], "ra": e["ra"], "dec": e["dec"], "kind": (e["data"] or {}).get("af_kind"),
                                 "mine": e["id"] in mine})
        return {"center": {"ra": e["ra"], "dec": e["dec"], "id": e["id"]}, "radius": radius,
                "constellation": (kb.fact(e["id"], "constellation") or {}).get("value"), "objects": list(pts.values())}

    @r.get("/kb/entities/{eid}/export.md")
    def export(eid: str):
        """Objektseite als Markdown mit Quellenliste (zum Weitergeben/Archivieren)."""
        d = entity(eid)
        e, srcs = d["entity"], d["sources"]
        order = {}

        def cite(m):
            sid = int(m.group(1))
            order.setdefault(sid, len(order) + 1)
            return f"[{order[sid]}]"

        lines = [f"# {e['name']}", "", f"*{e['otype_label'] or ''}*" + (f" · {', '.join(d['designations'])}" if d["designations"] else ""), "",
                 re.sub(r"\[S(\d+)\]", cite, e["summary"] or ""), "", "## Steckbrief", ""]
        for f in d["facts"]:
            if f["status"] in ("current", "conflict") and f["key"] not in ("distance",):
                lines.append(f"- **{f['label']}:** {f['value']}" + (f" [{order.setdefault(f['source_id'], len(order) + 1)}]" if f["source_id"] else "")
                             + (" (Messungen streuen)" if f["status"] == "conflict" else ""))
        for sec in d["sections"]:
            lines += ["", f"## {sec['title']}" + (" (KI-Entwurf, ungeprüft)" if sec["status"] == "draft" else ""), "",
                      re.sub(r"\[S(\d+)\]", cite, sec["body"])]
        if d["mine"]["derived"]:
            lines += ["", "## Meine Beobachtungen", ""] + [f"- {x['text']} *(Grundlage: {x['basis']})*" for x in d["mine"]["derived"]]
        lines += ["", "## Quellen", ""]
        for sid, n in sorted(order.items(), key=lambda x: x[1]):
            s = srcs.get(sid) or kb.source(sid)
            if s:
                lines.append(f"{n}. {s['title']}" + (f" ({s['year']})" if s.get("year") else "") + (f" – {s['url']}" if s.get("url") else ""))
        lines += ["", f"*Exportiert aus AstroFlow Sage am {time.strftime('%d.%m.%Y')}.*"]
        name = re.sub(r"[^\w-]+", "_", e["name"]).strip("_") or "objekt"
        return Response("\n".join(lines), media_type="text/markdown; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="{name}.md"'})

    @r.patch("/kb/entities/{eid}")
    def patch_entity(eid: str, body: dict = Body(...)):
        e = need(eid)
        vals = {}
        if "hero" in body:                                 # Titelbild (Schlüssel aus `gallery`)
            vals["data"] = {"hero": str(body["hero"])[:60]}
        if "summary" in body:
            vals.update(summary=str(body["summary"])[:2000], data={"summary_origin": "user"})
        if body.get("reviewed"):
            vals["status"] = "reviewed"
            for s in kb.sections(e["id"]):
                kb.x("UPDATE sections SET status='reviewed' WHERE id=? AND status='draft'", (s["id"],))
        if "name" in body and str(body["name"]).strip():
            vals["name"] = str(body["name"]).strip()[:120]
            kb.add_alias(e["id"], vals["name"])
        kb.upsert_entity(e["id"], **vals)
        return entity(e["id"])

    @r.delete("/kb/entities/{eid}")
    def delete_entity(eid: str):
        e = need(eid)
        if e["kind"] == "concept" and e["id"] in CONCEPTS:
            raise HTTPException(400, "Grundbegriffe gehören zum eingebauten Wissen.")
        kb.delete_entity(e["id"])
        return {"ok": True}

    @r.post("/kb/research")
    def research(body: dict = Body(...)):
        name = str(body.get("name") or "").strip()
        if not name:
            raise HTTPException(400, "Bitte ein Objekt angeben.")
        return task_view(mod.research(name, deep=bool(body.get("deep", True))))

    # ---- Abschnitte (KI-Entwürfe prüfen, bearbeiten) ---------------------------------------
    @r.patch("/kb/sections/{sid}")
    def patch_section(sid: int, body: dict = Body(...)):
        s = kb.one("SELECT * FROM sections WHERE id=?", (sid,))
        if not s:
            raise HTTPException(404)
        if "body" in body:                                # Nutzer schreibt um → neue Version, Herkunft „user“
            kb.put_section(s["entity_id"], s["key"], str(body.get("title") or s["title"])[:120], str(body["body"])[:20000],
                           sources=s["sources"], origin="user", status="reviewed")
        elif body.get("status") in ("reviewed", "draft"):
            kb.x("UPDATE sections SET status=?, updated=? WHERE id=?", (body["status"], time.time(), sid))
        return {"ok": True, "sections": kb.sections(s["entity_id"])}

    @r.delete("/kb/sections/{sid}")
    def delete_section(sid: int):
        s = kb.one("SELECT * FROM sections WHERE id=?", (sid,))
        if not s:
            raise HTTPException(404)
        kb.x("UPDATE sections SET status='superseded' WHERE id=?", (sid,))
        kb.index(s["entity_id"])
        return {"ok": True}

    @r.get("/kb/sections/{sid}/history")
    def section_history(sid: int):
        s = kb.one("SELECT entity_id, key FROM sections WHERE id=?", (sid,))
        if not s:
            raise HTTPException(404)
        return kb.q("SELECT id, title, body, origin, status, model, version, created FROM sections WHERE entity_id=? AND key=? ORDER BY version DESC",
                    (s["entity_id"], s["key"]))

    # ---- Persönliches ----------------------------------------------------------------------
    @r.get("/kb/notes")
    def notes(entity: str | None = None, kind: str | None = None):
        out = kb.notes(entity, kind)
        names = {e["id"]: e["name"] for e in kb.q("SELECT id, name FROM entities")}
        return [{**n, "entity_name": names.get(n["entity_id"])} for n in out]

    @r.post("/kb/notes")
    def add_note(body: dict = Body(...)):
        kind = body.get("kind") if body.get("kind") in NOTE_KINDS else "note"
        eid = body.get("entity_id")
        if eid:
            eid = need(eid)["id"]
        nid = kb.x("INSERT INTO notes(entity_id, kind, title, body, date, data, created, updated) VALUES(?,?,?,?,?,?,?,?)",
                   (eid, kind, str(body.get("title") or "")[:200], str(body.get("body") or "")[:20000], str(body.get("date") or "")[:20],
                    kb._j(body.get("data") or {}), time.time(), time.time()))
        return kb.one("SELECT * FROM notes WHERE id=?", (nid,))

    @r.patch("/kb/notes/{nid}")
    def patch_note(nid: int, body: dict = Body(...)):
        n = kb.one("SELECT * FROM notes WHERE id=?", (nid,))
        if not n:
            raise HTTPException(404)
        vals = {k: str(body[k])[:20000 if k == "body" else 200] for k in ("title", "body", "date") if k in body}
        if body.get("kind") in NOTE_KINDS:
            vals["kind"] = body["kind"]
        if "data" in body and isinstance(body["data"], dict):
            vals["data"] = kb._j(body["data"])
        if vals:
            kb.x(f"UPDATE notes SET {', '.join(f'{k}=?' for k in vals)}, updated=? WHERE id=?", (*vals.values(), time.time(), nid))
        return kb.one("SELECT * FROM notes WHERE id=?", (nid,))

    @r.delete("/kb/notes/{nid}")
    def delete_note(nid: int):
        kb.x("DELETE FROM notes WHERE id=?", (nid,))
        return {"ok": True}

    @r.get("/kb/profile")
    def profile():
        p = memory.profile(mod)
        return {**p, "notes": len(kb.notes()), "plans": notes(kind="plan"), "interests": notes(kind="interest")}

    # ---- Wissensnetz -------------------------------------------------------------------------
    @r.get("/kb/graph")
    def graph(center: str | None = None, depth: int = 2, personal: bool = True, limit: int = 90):
        """Wissensnetz. Mit `center`: Nachbarschaft bis `depth` samt persönlicher Kette (Aufnahme → Session → Gerät).
        Ohne: Überblick über die eigenen Objekte (Nächte/Stunden am Knoten statt eigener Aufnahme-Knoten), dazu Begriffe und
        Katalogobjekte, die mindestens zwei eigene Objekte verbinden (z. B. Galaxiengruppen)."""
        nodes, links = {}, {}
        prof = {o["entity"]: o for o in memory.profile(mod)["objects"] if o["entity"]}

        def node(e: dict, level: int):
            if e["id"] not in nodes:
                o = prof.get(e["id"]) or {}
                nodes[e["id"]] = {"id": e["id"], "label": e["name"], "kind": e["kind"], "group": (e["data"] or {}).get("af_kind") or e["kind"],
                                  "status": e["status"], "level": level, "type": e["otype_label"],
                                  "constellation": (kb.fact(e["id"], "constellation") or {}).get("value"),
                                  "nights": o.get("nights") or 0, "hours": round(o.get("hours") or 0, 2), "images": o.get("images") or 0}

        def link(a, b, rel, origin, status="verified", note="", xid=None):
            k = (a, b, rel)
            if a in nodes and b in nodes and k not in links:
                links[k] = {"source": a, "target": b, "rel": rel, "origin": origin, "status": status, "note": note, "id": xid}

        mine = set(prof)
        if center:
            c = need(center)
            node(c, 0)
            frontier = [c["id"]]
            for lvl in range(1, max(1, min(depth, 3)) + 1):
                nxt = []
                for nid in frontier:
                    # von einem Begriff aus nur zu weiteren Begriffen („ist eine Galaxie“ holte sonst alle Galaxien als Nachbarn
                    # 2. Grades); ist das Zentrum selbst ein Begriff, gilt das nicht
                    only_concepts = nid != c["id"] and nodes[nid]["kind"] == "concept" and c["kind"] != "concept"
                    for x in kb.edges(nid):
                        o = x["dst"] if x["src"] == nid else x["src"]
                        oe = kb.entity(o)
                        if oe and len(nodes) < limit and not (only_concepts and oe["kind"] != "concept" and o not in nodes):
                            new = o not in nodes
                            node(oe, lvl)
                            if new:
                                nxt.append(o)
                frontier = nxt
        else:                                              # Überblick: eigene Objekte, ihre Begriffe und verbindende Katalogobjekte
            for eid in mine:
                e = kb.entity(eid)
                if e:
                    node(e, 0)
            cap, touch = len(nodes) + max(limit, 150), {}
            for eid in list(nodes):
                for x in kb.edges(eid):
                    o = x["dst"] if x["src"] == eid else x["src"]
                    if o not in nodes:
                        touch.setdefault(o, set()).add(eid)
            for o, hits in sorted(touch.items(), key=lambda t: -len(t[1])):
                oe = kb.entity(o)
                if oe and len(nodes) < cap and (oe["kind"] == "concept" or len(hits) >= 2):
                    node(oe, 1)
        for nid in list(nodes):
            for x in kb.edges(nid):
                link(x["src"], x["dst"], x["rel"], x["origin"], x["status"], x["note"], x["id"])
        pics = picture_index()
        for n in nodes.values():
            n["observed"] = n["id"] in mine
            n["image"] = (pics.get(n["id"]) or {}).get("thumb")
        if personal:
            seen_pairs = set()
            for h in mod.store.entries():                   # gemeinsam in einem eigenen Bild
                ids = [e["id"] for e in (kb.resolve(o["name"]) for o in h.get("objects") or []) if e and e["id"] in nodes]
                for i, a in enumerate(ids):
                    for b in ids[i + 1:]:
                        if (a, b) not in seen_pairs and len(seen_pairs) < 60:
                            seen_pairs.add((a, b))
                            link(a, b, "im selben Bild", "personal")
            # Aufnahme-Kette nur um ein Objekt; im Überblick stehen Nächte/Stunden am Objektknoten (vorher 2 Extraknoten je Objekt)
            targets = [nodes[next(iter(nodes))]["id"]] if center and nodes else []
            for p in memory.observation_log(mod) if targets else []:
                if not p["sessions"]:
                    continue
                e = memory._match(kb, p["name"], p.get("ra"), p.get("dec"))
                if not e or e["id"] not in targets or e["id"] not in nodes:
                    continue
                pid = f"proj:{p['id']}"
                hours = sum(s["used_s"] for s in p["sessions"]) / 3600
                nodes[pid] = {"id": pid, "label": f"Deine Aufnahme ({len(p['sessions'])} Nacht{'' if len(p['sessions']) == 1 else 'e'}, "
                                                  f"{hours:.1f} h)".replace(".", ","), "kind": "observation", "group": "personal",
                              "href": f"#/p/{p['id']}", "level": 1}
                link(e["id"], pid, "fotografiert", "personal")
                for s in p["sessions"][-4:] if center else []:
                    sid = f"night:{p['id']}:{s['night']}"
                    nodes[sid] = {"id": sid, "label": f"Session {s['night']}", "kind": "session", "group": "personal", "level": 2}
                    link(pid, sid, "Beobachtungssession", "personal")
                    for ins in s["instruments"]:
                        qid = f"eq:{ins}"
                        nodes.setdefault(qid, {"id": qid, "label": ins, "kind": "equipment", "group": "personal", "level": 3})
                        link(sid, qid, "aufgenommen mit", "personal")
                if p["results"]:
                    vid = f"ver:{p['id']}"
                    nodes[vid] = {"id": vid, "label": f"Bildbearbeitung ({len(p['results'])} Version{'' if len(p['results']) == 1 else 'en'})",
                                  "kind": "processing", "group": "personal", "href": f"#/p/{p['id']}/versionen", "level": 2}
                    link(pid, vid, "entwickelt", "personal")
        return {"nodes": list(nodes.values()), "links": list(links.values()), "center": next(iter(nodes)) if center and nodes else None}

    @r.post("/kb/edges")
    def add_edge(body: dict = Body(...)):
        a = need(str(body.get("src") or ""))
        b = kb.resolve(str(body.get("dst") or "")) or Researcher(mod).ensure_stub(str(body.get("dst") or ""))
        if not b:
            raise HTTPException(404, "Zielobjekt unbekannt – erst recherchieren.")
        kb.add_edge(a["id"], b["id"], str(body.get("rel") or "verwandt mit")[:60], origin="user", status="verified", note=str(body.get("note") or "")[:300])
        return {"ok": True}

    @r.patch("/kb/edges/{xid}")
    def patch_edge(xid: int, body: dict = Body(...)):
        if body.get("status") not in ("verified", "rejected"):
            raise HTTPException(400)
        kb.x("UPDATE edges SET status=?, origin=CASE WHEN ?='verified' AND origin='ai' THEN 'ai+user' ELSE origin END WHERE id=?",
             (body["status"], body["status"], xid))
        return {"ok": True}

    # ---- Meine Entdeckungen --------------------------------------------------------------------
    @r.get("/kb/insights")
    def insights(include_dismissed: bool = False):
        from .insights import TASKS, enabled
        rows = kb.q("SELECT * FROM insights" + ("" if include_dismissed else " WHERE dismissed=0") + " ORDER BY created DESC LIMIT 200")
        srcs = {s["id"]: s for s in kb.sources({i for x in rows for i in x["sources"]})}
        names = {e["id"]: e["name"] for e in kb.q("SELECT id, name FROM entities")}
        cfg = mod.config()
        return {"items": [{**x, "sources": [{k: srcs[i][k] for k in ("id", "title", "url", "kind", "year")} for i in x["sources"] if i in srcs],
                           "entity_names": {i: names.get(i, i) for i in x["entities"]}} for x in rows],
                "report": kb.meta("proactive_report"), "last": kb.meta("proactive_last"),
                "tasks": [{"id": k, "title": v[0], "description": v[1], "enabled": enabled(cfg, k)} for k, v in TASKS.items()],
                "running": mod.proactive_running(),
                "proactive": cfg.get("proactive", True), "hours": cfg.get("proactive_hours"), "observer_lat": cfg.get("observer_lat")}

    @r.post("/kb/insights/run")
    def run_insights(body: dict = Body(default={})):
        t = mod.run_proactive(body.get("only"))
        if t is None:
            raise HTTPException(409, "Die Suche läuft bereits.")
        return task_view(t)

    @r.patch("/kb/insights/{iid}")
    def patch_insight(iid: int, body: dict = Body(...)):
        for k in ("seen", "dismissed"):
            if k in body:
                kb.x(f"UPDATE insights SET {k}=? WHERE id=?", (int(bool(body[k])), iid))
        return {"ok": True}

    @r.post("/kb/insights/seen")
    def all_seen():
        kb.x("UPDATE insights SET seen=1 WHERE seen=0")
        return {"ok": True}

    # ---- Chat ----------------------------------------------------------------------------------
    @r.get("/kb/chat")
    def chat_history(scope: str = "global"):
        from .assistant import history
        return history(kb, scope)

    @r.post("/kb/chat")
    def chat(body: dict = Body(...)):
        from .assistant import ask
        q = str(body.get("question") or "").strip()
        if not q:
            raise HTTPException(400, "Bitte eine Frage stellen.")
        scope = str(body.get("scope") or "global")[:80]
        try:
            return ask(mod, q[:2000], scope, body.get("entity"), body.get("image"))
        except agent.BudgetError as e:
            raise HTTPException(402, str(e))
        except (clients.ServiceError, SourceError) as e:
            raise HTTPException(502, str(e))

    @r.delete("/kb/chat")
    def chat_clear(scope: str = "global"):
        kb.x("DELETE FROM chats WHERE scope=?", (scope,))
        return {"ok": True}

    # ---- Erzählformate & Vorlesen ------------------------------------------------------------
    def story_view(s: dict) -> dict:
        img = s.get("image") or {}
        return {**s, "image": {**img, "url": url(img.get("image"))} if img else None,
                "audio": {p: {**a, "urls": [url(f) for f in a.get("files") or []]} for p, a in (s.get("audio") or {}).items()}}

    def stories_of(eid: str) -> list[dict]:
        return [story_view(s) for s in kb.q("SELECT * FROM stories WHERE entity_id=? ORDER BY id DESC", (eid,))]

    @r.post("/kb/entities/{eid}/stories")
    def new_story(eid: str, body: dict = Body(...)):
        e = need(eid)
        try:
            s = narrate.generate(mod, e["id"], str(body.get("format") or "doku"), body.get("history_id"))
        except agent.BudgetError as x:
            raise HTTPException(402, str(x))
        except clients.ServiceError as x:
            raise HTTPException(400 if "Keine KI" in str(x) or "braucht" in str(x) else 502, str(x))
        return story_view(s)

    @r.delete("/kb/stories/{sid}")
    def delete_story(sid: int):
        s = kb.one("SELECT * FROM stories WHERE id=?", (sid,))
        if not s:
            raise HTTPException(404)
        for a in (s.get("audio") or {}).values():
            for f in a.get("files") or []:
                if f:
                    (mod.store.root / f).unlink(missing_ok=True)
        kb.x("DELETE FROM stories WHERE id=?", (sid,))
        return {"ok": True}

    SPEAK = {"elevenlabs": clients.eleven_speak, "openrouter": clients.openrouter_speak, "qwen": clients.qwen_speak}

    @r.post("/kb/stories/{sid}/speak")
    def speak_story(sid: int, body: dict = Body(default={})):
        s = kb.one("SELECT * FROM stories WHERE id=?", (sid,))
        if not s:
            raise HTTPException(404)
        c = mod.config()
        provider = body.get("provider") or c["tts_provider"]
        provider = "qwen" if provider == "local" else provider
        if provider not in SPEAK:
            raise HTTPException(400, "Unbekannter Sprachdienst.")
        seg = body.get("segment")
        seg = int(seg) if seg is not None else None
        var = clients.tts_variant(c, provider)
        audio = s.get("audio") or {}
        have = audio.get(provider) or {}
        n = len(s["segments"]) if seg is not None else 1
        files = (have.get("files") or [None] * n) if have.get("variant") == var else [None] * n
        files = (files + [None] * n)[:n]
        i = seg or 0
        if files[i] and not body.get("force") and (mod.store.root / files[i]).is_file():
            return {"url": url(files[i]), "cached": True}
        try:
            data = SPEAK[provider](c, narrate.speak_text(s, seg))
        except clients.ServiceError as x:
            raise HTTPException(502, str(x))
        rel = f"audio/story-{sid}-{i}-{provider}-{secrets.token_hex(3)}.mp3"
        (mod.store.root / rel).write_bytes(data)
        if files[i]:
            (mod.store.root / files[i]).unlink(missing_ok=True)
        files[i] = rel
        audio[provider] = {"variant": var, "files": files}
        kb.x("UPDATE stories SET audio=? WHERE id=?", (kb._j(audio), sid))
        return {"url": url(rel), "cached": False}

    # ---- Himmelsbild & Kosten -----------------------------------------------------------------
    @r.get("/kb/sky/{eid}.jpg")
    def sky(eid: str):
        e = need(eid)
        if e["ra"] is None:
            raise HTTPException(404)
        cache = mod.store.root / "sky"
        cache.mkdir(exist_ok=True)
        f = cache / f"{re.sub(r'[^a-z0-9+-]', '', e['id'])}-2.jpg"                 # -2: Farbstreckung je Kanal (ältere Dateien hatten Farbstich)
        if not f.is_file():
            if not mod.config().get("catalog_online", True):
                raise HTTPException(404, "Online-Quellen sind ausgeschaltet.")
            fov = min(3.0, max(0.25, (e["size"] or 20) / 60 * 1.8))
            try:
                f.write_bytes(sources.sky_image(sources.Net(kb), e["ra"], e["dec"], fov))
            except SourceError as x:
                raise HTTPException(502, str(x))
            kb.upsert_entity(e["id"], data={"sky_fov": fov})
        return Response(f.read_bytes(), media_type="image/jpeg", headers={"Cache-Control": "private, max-age=604800"})

    @r.get("/kb/usage")
    def usage():
        c = mod.config()
        return {**kb.usage_summary(), "budget_month": c.get("budget_month_usd"), "budget_run": c.get("budget_run_usd"),
                "model": c.get("research_model") or c.get("story_model"), "provider": c.get("story_provider"),
                "recent": kb.q("SELECT ts, model, tokens_in, tokens_out, cost, estimated, purpose FROM usage ORDER BY id DESC LIMIT 20")}

    @r.get("/kb/tasks/{tid}")
    def kb_task(tid: str):
        t = mod.tasks.get(tid)
        if not t:
            raise HTTPException(404, "Auftrag nicht gefunden.")
        return {k: v for k, v in task_view(t).items() if k != "result" or isinstance(v, (str, dict))}

    return r


__all__ = ["extend", "norm"]
