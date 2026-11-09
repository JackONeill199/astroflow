"""Proaktive Hintergrundaufgaben → „Meine Entdeckungen“. Jede Entdeckung hat Begründung, Objekte und Quellen.

Aufgaben (einzeln abschaltbar, gemeinsames Intervall): fehlende Einträge ergänzen, neue Forschung zu fotografierten Objekten,
weitere katalogisierte Galaxien in eigenen Bildfeldern, gemeinsame Regionen/Gruppen, veraltete Einträge, Beobachtungsvorschläge.
Das Sprachmodell wird hier nur benutzt, wenn `proactive_llm` an ist – und dann innerhalb des Budgets."""
from __future__ import annotations

import math
import time

from ...core.errors import user_message
from . import agent, memory, sources
from .kb import norm
from .research import Researcher, angular_sep, fmt_ly
from .sources import SourceError

TASKS = {
    "fill": ("Fehlende Einträge ergänzen", "Fotografierte Objekte ohne Katalogdaten aus SIMBAD recherchieren"),
    "papers": ("Neue Forschung", "arXiv nach neuen Arbeiten zu deinen Objekten durchsuchen"),
    "field": ("Mehr in deinen Bildern", "Weitere katalogisierte Galaxien in den Bildfeldern deiner Aufnahmen finden"),
    "region": ("Gemeinsame Regionen", "Objekte erkennen, die zur selben Gruppe oder Region gehören"),
    "stale": ("Veraltetes erkennen", "Einträge markieren und auffrischen, deren Recherche lange zurückliegt"),
    "targets": ("Beobachtungsvorschläge", "Passende neue Ziele für die nächsten Wochen (braucht den Standort)"),
}
MONTHS = ("Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober", "November", "Dezember")
STAR_FORMING = {"emission", "reflection", "dark", "open_cluster"}


def enabled(cfg: dict, task: str) -> bool:
    return cfg.get(f"task_{task}", True) is not False


def run_all(mod, only: list[str] | None = None, progress=lambda p, m: None) -> dict:
    """Alle (bzw. ausgewählte) Aufgaben nacheinander; Fehler einer Aufgabe stoppen die anderen nicht."""
    cfg = mod.config()
    report, todo = {}, [t for t in TASKS if (only is None and enabled(cfg, t)) or (only and t in only)]
    for i, t in enumerate(todo):
        progress(0.05 + 0.9 * i / max(1, len(todo)), f"{TASKS[t][0]} …")
        t0 = time.time()
        try:
            n = globals()[f"task_{t}"](mod)
            report[t] = {"ok": True, "new": n, "seconds": round(time.time() - t0, 1)}
        except Exception as e:
            msg = user_message(e, "Aufgabe fehlgeschlagen – Details im Server-Protokoll.", allow=(SourceError, agent.BudgetError))
            report[t] = {"ok": False, "error": msg, "seconds": round(time.time() - t0, 1)}
    mod.kb.set_meta("proactive_report", {"finished": time.time(), "tasks": report})
    mod.kb.set_meta("proactive_last", time.time())
    return report


def observed(mod) -> list[dict]:
    """Eigene Objekte (Aufnahmen oder bestimmte Bilder), die einen Enzyklopädie-Eintrag haben."""
    return [mod.kb.entity(o["entity"]) for o in memory.profile(mod)["objects"] if o["entity"] and mod.kb.entity(o["entity"])]


# --------------------------------------------------------------------- Aufgaben -- #
def task_fill(mod) -> int:
    r, n = Researcher(mod), 0
    deep = bool(mod.config().get("proactive_llm"))
    # erst sicherstellen, dass jedes fotografierte Objekt einen Eintrag hat
    for p in memory.observation_log(mod):
        if p["sessions"]:
            r.ensure_stub(p["name"], ra=p.get("ra"), dec=p.get("dec"))
    for h in mod.store.entries():
        for o in h.get("objects") or []:
            r.ensure_stub(o["name"], o.get("kind_label") or "")
    for e in observed(mod):
        if e["status"] == "stub" or (deep and e["status"] == "catalog"):
            try:
                r.research(e["id"], deep=deep)
                n += 1
            except SourceError:
                continue
            if n >= 8:                                     # je Lauf begrenzt (Abfragen schonen)
                break
    return n


def task_papers(mod) -> int:
    kb, cfg, new = mod.kb, mod.config(), 0
    r = Researcher(mod)
    for e in observed(mod)[:15]:
        names = r.search_names(e["id"])
        try:
            found = sources.arxiv_search(r.net, names[0], limit=8)
        except SourceError:
            raise
        seen = set(kb.meta(f"papers_seen:{e['id']}", []))
        fresh = [p for p in found if p["id"] not in seen and r.mentions(p["title"] + " " + p["abstract"], names)]
        kb.set_meta(f"papers_seen:{e['id']}", sorted(seen | {p["id"] for p in found}))
        if not seen:                                     # erster Lauf: nur wirklich Neues (letzte 60 Tage) melden
            cutoff = time.strftime("%Y-%m-%d", time.localtime(time.time() - 60 * 86400))
            fresh = [p for p in fresh if p["published"] >= cutoff]
        if not fresh:
            continue
        sids = [kb.add_source(f"arxiv:{p['id']}", "preprint", p["title"], url=p["url"], authors=", ".join(p["authors"]),
                              year=int(p["published"][:4]), publisher="arXiv", abstract=p["abstract"], data={"published": p["published"]})
                for p in fresh]
        data = e.get("data") or {}
        kb.upsert_entity(e["id"], data={"preprints": list(dict.fromkeys([*sids, *(data.get("preprints") or [])]))[:20]})
        body = "\n".join(f"- **{p['title']}** ({p['published']})" for p in fresh[:5])
        summary = _llm_summary(mod, e, fresh) if cfg.get("proactive_llm") else None
        if kb.add_insight(f"papers:{e['id']}:{fresh[0]['id']}", "research", f"Neue Forschung zu {e['name']}",
                          (summary + "\n\n" if summary else "") + body,
                          reason=f"Du hast {e['name']} fotografiert; arXiv listet {len(fresh)} neue Arbeit{'en' if len(fresh) > 1 else ''}, "
                                 f"die das Objekt im Titel oder Abstract nennen.",
                          entities=[e["id"]], sources=sids, origin="ai" if summary else "rule",
                          data={"ai_summary": bool(summary)}):
            new += 1
    return new


def _llm_summary(mod, e: dict, papers: list[dict]) -> str | None:
    llm = agent.LLM(mod.config(), mod.kb, purpose="proactive")
    if not llm.ready():
        return None
    txt = "\n\n".join(f"[{i + 1}] {p['title']}\n{p['abstract'][:900]}" for i, p in enumerate(papers[:4]))
    try:
        m = llm.chat([{"role": "system", "content": "Fasse neue astronomische Fachartikel für Hobby-Astrofotografen auf Deutsch zusammen. "
                                                    "Nur Aussagen aus den Abstracts, jede mit [n]. Höchstens 4 Sätze."},
                      {"role": "user", "content": f"Objekt: {e['name']}\n\n{txt}"}], max_tokens=500)
    except (agent.BudgetError, Exception):
        return None
    return (m.get("content") or "").strip() or None


def task_field(mod) -> int:
    kb, new = mod.kb, 0
    r = Researcher(mod)
    for h in mod.store.entries()[:30]:
        sol = h.get("solution") or {}
        if sol.get("ra") is None or not sol.get("fov_w"):
            continue
        key = f"field:{h['id']}"
        if kb.one("SELECT id FROM insights WHERE key=?", (key,)) or kb.meta(key):
            continue
        radius = min(3.0, math.hypot(sol["fov_w"], sol["fov_h"] or sol["fov_w"]) / 2)
        try:
            gal = r.simbad.cone(sol["ra"], sol["dec"], radius, "G..", min_size=0.6 if radius > 0.5 else 0.3)
        except SourceError:
            raise
        known = set()
        for o in h.get("objects") or []:
            known.add(norm(o["name"]))
            e = kb.resolve(o["name"])
            if e:
                known |= {norm(a) for a in kb.aliases(e["id"])}
        extra = [g for g in gal if norm(g["main_id"]) not in known]
        kb.set_meta(key, {"checked": time.time(), "found": len(extra)})
        if not extra:
            continue
        sid = kb.add_source(f"simbad:cone:{h['id']}", "catalog", f"SIMBAD-Kegelsuche um RA {sol['ra']:.2f}°, Dec {sol['dec']:.2f}° (r = {radius:.2f}°)",
                            url="https://simbad.cfa.harvard.edu/simbad/sim-coo?Coord=" + f"{sol['ra']}+{sol['dec']}&Radius={radius * 60:.0f}&Radius.unit=arcmin")
        lines = "\n".join(f"- **{' '.join(g['main_id'].split())}** ({sources.otype_label(g['otype'])[0]}, {g['galdim_majaxis']:.1f}′)" for g in extra[:12])
        word = {1: "eine weitere katalogisierte Galaxie", 2: "zwei weitere katalogisierte Galaxien", 3: "drei weitere katalogisierte Galaxien"}
        if kb.add_insight(key, "field", f"In „{h.get('title') or 'deiner Aufnahme'}“ befinden sich {word.get(len(extra), f'{len(extra)} weitere katalogisierte Galaxien')}",
                          lines, reason=f"SIMBAD kennt im Bildfeld ({sol['fov_w'] * 60:.0f}′ × {(sol['fov_h'] or 0) * 60:.0f}′) Galaxien ab "
                                        f"{0.6 if radius > 0.5 else 0.3}′ Größe, die bei der Bildbestimmung nicht genannt wurden. Ob sie im Bild "
                                        "sichtbar sind, hängt von Belichtung und Helligkeit ab.",
                          entities=[e["id"] for e in (kb.resolve(o["name"]) for o in h.get("objects") or []) if e][:3], sources=[sid],
                          data={"entry": h["id"], "objects": [g["main_id"] for g in extra]}):
            new += 1
    return new


def task_region(mod) -> int:
    kb, new = mod.kb, 0
    ents = [e for e in observed(mod) if e["ra"] is not None]
    dist = {e["id"]: (kb.fact(e["id"], "distance_ly") or {}).get("num") for e in ents}
    # 1) gemeinsames übergeordnetes Objekt laut SIMBAD (z. B. Assoziation, Galaxiengruppe)
    parents: dict[str, list[str]] = {}
    for e in ents:
        for ed in kb.q("SELECT dst, source_id FROM edges WHERE src=? AND rel='Mitglied von' AND status='verified'", (e["id"],)):
            parents.setdefault(ed["dst"], []).append(e["id"])
    for pid, members in parents.items():
        if len(members) >= 2:
            p = kb.entity(pid)
            names = ", ".join(kb.entity(m)["name"] for m in members)
            if kb.add_insight(f"parent:{pid}:{'+'.join(sorted(members))}", "region", f"{len(members)} deiner Objekte gehören zu {p['name']}",
                              f"{names} sind laut SIMBAD Mitglieder von **{p['name']}** ({p['otype_label']}).",
                              reason="Gemeinsames übergeordnetes Objekt in der SIMBAD-Hierarchie.", entities=[*members, pid],
                              sources=[s for s in [(kb.entity(members[0])["data"] or {}).get("simbad_source")] if s]):
                new += 1
    # 2) gleiche Himmelsregion und ähnliche Entfernung – als Interpretation gekennzeichnet
    done = set()
    for i, a in enumerate(ents):
        group = [a]
        for b in ents[i + 1:]:
            da, db = dist.get(a["id"]), dist.get(b["id"])
            if not da or not db or angular_sep(a["ra"], a["dec"], b["ra"], b["dec"]) > 12 or not 0.75 < da / db < 1.33:
                continue
            group.append(b)
        ids = sorted(g["id"] for g in group)
        if len(group) < 2 or tuple(ids) in done:
            continue
        done.add(tuple(ids))
        kinds = {(g["data"] or {}).get("af_kind") for g in group}
        sf = kinds <= STAR_FORMING
        title = (f"{len(group)} deiner Objekte gehören vermutlich zur selben Sternentstehungsregion" if sf
                 else f"{len(group)} deiner Objekte liegen nah beieinander im Raum")
        lines = "\n".join(f"- **{g['name']}** – {g['otype_label']}, {fmt_ly(dist[g['id']])}" for g in group)
        seps = max(angular_sep(x["ra"], x["dec"], y["ra"], y["dec"]) for x in group for y in group)
        if kb.add_insight(f"region:{'+'.join(ids)}", "region", title, lines,
                          reason=f"Abstand am Himmel höchstens {seps:.1f}°, Entfernungen weichen um weniger als 33 % voneinander ab "
                                 f"(Katalogwerte). Das ist ein Hinweis, kein Beleg für eine gemeinsame Entstehung.",
                          entities=ids, sources=[s for s in [(g["data"] or {}).get("simbad_source") for g in group] if s],
                          data={"kind": "interpretation"}):
            new += 1
    return new


def task_stale(mod) -> int:
    kb, days = mod.kb, float(mod.config().get("stale_days") or 180)
    cut, n = time.time() - days * 86400, 0
    mine = {e["id"] for e in observed(mod)}
    for e in kb.q("SELECT * FROM entities WHERE kind='object' AND refreshed IS NOT NULL AND refreshed < ?", (cut,)):
        kb.upsert_entity(e["id"], data={"stale": True})
        if e["id"] in mine and n < 5:
            try:
                Researcher(mod).research(e["id"], deep=False)
                kb.upsert_entity(e["id"], data={"stale": False})
                n += 1
            except SourceError:
                pass
    return n


def sun_ra(t: float | None = None) -> float:
    """Rektaszension der Sonne (Grad, auf ~2° genau) – genügt für „wann steht ein Objekt um Mitternacht hoch“."""
    d = (t or time.time()) / 86400 - 10957.5                   # Tage seit J2000
    L = math.radians((280.46 + 0.9856474 * d) % 360)
    g = math.radians((357.528 + 0.9856003 * d) % 360)
    lam = L + math.radians(1.915) * math.sin(g) + math.radians(0.02) * math.sin(2 * g)
    eps = math.radians(23.439)
    return math.degrees(math.atan2(math.cos(eps) * math.sin(lam), math.cos(lam))) % 360


def task_targets(mod) -> int:
    cfg, kb = mod.config(), mod.kb
    try:
        lat = float(str(cfg.get("observer_lat") or "").replace(",", "."))
    except ValueError:
        return 0
    cat = mod.ctx.service("catalog_objects")
    if not cat:
        return 0
    prof = memory.profile(mod)
    mine = {o["entity"] for o in prof["objects"] if o["entity"]} | {norm(o["name"]) for o in prof["objects"]}
    kinds = prof["kinds"] or {}
    mid = (sun_ra() + 180) % 360
    cand = []
    for c in cat():
        alt = 90 - abs(lat - c["dec"])
        dra = abs((c["ra"] - mid + 180) % 360 - 180)
        if alt < 35 or dra > 50 or norm(c["id"]) in mine or (kb.resolve(c["id"]) or {}).get("id") in mine:
            continue
        score = alt / 90 + (kinds.get(c["kind"], 0) / max(1, sum(kinds.values()))) * 1.5 - dra / 200 + min(c["size"], 60) / 120
        cand.append((score, c, alt, dra))
    cand.sort(key=lambda x: -x[0])
    if not cand:
        return 0
    month = time.strftime("%Y-%m")
    lines = []
    for _, c, alt, dra in cand[:6]:
        why = [f"kulminiert {'um' if dra < 15 else 'nahe'} Mitternacht in {alt:.0f}° Höhe"]
        if kinds.get(c["kind"]):
            why.append(f"du fotografierst gern {c['kind_label']}")
        lines.append(f"- **{c['display']}** ({c['kind_label']}, {c['size']:.0f}′) – {', '.join(why)}")
    return int(bool(kb.add_insight(f"targets:{month}:{round(lat)}", "targets", f"Ziele für {MONTHS[time.localtime().tm_mon - 1]} {time.localtime().tm_year}", "\n".join(lines),
                                   reason=f"Berechnet für {lat:.1f}° Breite: Objekte, die in diesen Wochen um Mitternacht hoch stehen "
                                          "und die du noch nicht aufgenommen hast; bevorzugt Objektarten aus deinen bisherigen Aufnahmen.",
                                   entities=[], data={"targets": [c["id"] for _, c, _, _ in cand[:6]]})))


# ------------------------------------------------------------------- Zeitplan -- #
class Scheduler:
    """Prüft minütlich, ob die Hintergrundaufgaben fällig sind (Intervall `proactive_hours`)."""

    def __init__(self, mod):
        self.mod = mod
        self.stop = False
        self.busy = False

    def due(self) -> bool:
        cfg = self.mod.config()
        if not cfg.get("proactive", True) or not cfg.get("catalog_online", True):
            return False
        last = self.mod.kb.meta("proactive_last", 0) or 0
        return time.time() - last > float(cfg.get("proactive_hours") or 24) * 3600

    def loop(self):
        time.sleep(90)                                   # Start nicht ausbremsen
        while not self.stop:
            if self.due() and not self.busy:
                self.mod.run_proactive()
            for _ in range(60):
                if self.stop:
                    return
                time.sleep(1)
