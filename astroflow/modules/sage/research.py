"""Research Harness: ein Objekt recherchieren, prüfen und versioniert in der Wissensbasis ablegen.

Ablauf: Kennung auflösen (lokaler Katalog + SIMBAD) → Katalogfakten mit Quelle (Koordinaten, Typ, Parallaxe, alle
Entfernungsmessungen mit Bibcode, Größe, Spektraltyp) → Beziehungen (Hierarchie, Typ-Konzept) → Literatur (SIMBAD-
Referenzen mit Abstract, arXiv, ADS) → optional: Agent vertieft mit Werkzeugen und schreibt belegte Abschnitte →
Validierung (Belege, Widersprüche zu Katalogwerten) → speichern als „KI-Entwurf“. Ohne KI bleibt es beim Katalogwissen."""
from __future__ import annotations

import math
import re
import time

from . import agent, sources
from .kb import norm, slug
from .sources import PC_LY, SourceError

CURATED_KEY = "astroflow:grundwissen"
# Kuratiertes Grundwissen (Lehrbuchwissen, im Code gepflegt): Konzepte und ihre Beziehungen
CONCEPTS = {
    "k-sternentstehung": ("Sternentstehung", "Sterne entstehen, wenn dichte, kalte Kerne in Molekülwolken unter ihrer eigenen Schwerkraft kollabieren."),
    "k-staub": ("Interstellarer Staub", "Winzige Silikat- und Kohlenstoffkörner zwischen den Sternen; sie streuen und schwächen Sternlicht, blaues stärker als rotes."),
    "k-gas": ("Interstellares Gas", "Vor allem Wasserstoff und Helium zwischen den Sternen – neutral, ionisiert oder in Molekülform."),
    "k-reflexionsnebel": ("Reflexionsnebel", "Staubwolke, die das Licht naher Sterne streut; meist bläulich, weil kurzwelliges Licht stärker gestreut wird."),
    "k-emissionsnebel": ("Emissionsnebel", "Gaswolke, die von heißen jungen Sternen ionisiert wird und in eigenen Spektrallinien leuchtet, vor allem rot in H-alpha."),
    "k-dunkelwolke": ("Dunkelwolke", "Dichte, kalte Wolke aus Gas und Staub, die das Licht dahinterliegender Sterne verschluckt."),
    "k-offener-haufen": ("Offener Sternhaufen", "Locker gebundene Gruppe junger Sterne, die gemeinsam aus einer Wolke entstanden ist und sich mit der Zeit auflöst."),
    "k-kugelhaufen": ("Kugelsternhaufen", "Dichte, kugelförmige Ansammlung von Hunderttausenden sehr alter Sterne im Halo einer Galaxie."),
    "k-galaxie": ("Galaxie", "Durch Schwerkraft gebundenes System aus Sternen, Gas, Staub und Dunkler Materie."),
    "k-planetarischer-nebel": ("Planetarischer Nebel", "Leuchtende Gashülle, die ein sonnenähnlicher Stern am Ende seines Lebens abstößt."),
    "k-weisser-zwerg": ("Weißer Zwerg", "Heißer, kompakter Sternrest von etwa Erdgröße, der langsam auskühlt."),
    "k-supernova": ("Supernova", "Explosion eines massereichen Sterns oder eines Weißen Zwergs, die für Wochen eine ganze Galaxie überstrahlen kann."),
    "k-snr": ("Supernova-Überrest", "Sich ausdehnende Hülle aus Gas, die eine Supernova in das umgebende Medium geschleudert hat."),
    "k-sternentwicklung": ("Sternentwicklung", "Lebensweg eines Sterns von der Entstehung bis zum Endstadium, bestimmt vor allem durch seine Masse."),
    "k-stern": ("Stern", "Selbstleuchtende Gaskugel, die ihre Energie durch Kernfusion erzeugt."),
}
CONCEPT_EDGES = [
    ("k-reflexionsnebel", "k-staub", "leuchtet durch Streuung an"), ("k-staub", "k-dunkelwolke", "Bestandteil von"),
    ("k-dunkelwolke", "k-sternentstehung", "Geburtsort bei"), ("k-staub", "k-sternentstehung", "beteiligt an"),
    ("k-emissionsnebel", "k-gas", "besteht aus ionisiertem"), ("k-emissionsnebel", "k-sternentstehung", "Zeichen aktiver"),
    ("k-offener-haufen", "k-sternentstehung", "Ergebnis von"), ("k-planetarischer-nebel", "k-sternentwicklung", "Endphase in"),
    ("k-planetarischer-nebel", "k-weisser-zwerg", "hinterlässt"), ("k-snr", "k-supernova", "Überrest einer"),
    ("k-supernova", "k-sternentwicklung", "Endphase in"), ("k-snr", "k-gas", "reichert mit schweren Elementen an"),
    ("k-kugelhaufen", "k-galaxie", "Teil des Halos von"), ("k-stern", "k-sternentwicklung", "durchläuft"),
]
KIND_CONCEPT = {"reflection": "k-reflexionsnebel", "emission": "k-emissionsnebel", "dark": "k-dunkelwolke", "open_cluster": "k-offener-haufen",
                "globular": "k-kugelhaufen", "galaxy": "k-galaxie", "planetary": "k-planetarischer-nebel", "snr": "k-snr", "star": "k-stern"}
VAGUE = {"Cl*", "sh", "ISM", "Cld", "Rad", "GNe", "EmO", "Neb"}     # unspezifische SIMBAD-Typen
SECTION_TITLES = {"overview": "Überblick", "physics": "Physik & Aufbau", "history": "Entdeckung & Geschichte",
                  "research": "Aktuelle Forschung", "open_questions": "Offene Fragen", "observing": "Beobachten & Fotografieren"}


def seed_concepts(kb):
    if kb.meta("concepts_v") == 1:
        return
    sid = kb.add_source(CURATED_KEY, "curated", "AstroFlow-Grundwissen (kuratiertes Lehrbuchwissen)", publisher="AstroFlow")
    for cid, (name, text) in CONCEPTS.items():
        kb.upsert_entity(cid, kind="concept", name=name, summary=text, status="reviewed", data={"summary_origin": "curated"})
        kb.add_alias(cid, name)
    for a, b, rel in CONCEPT_EDGES:
        kb.add_edge(a, b, rel, origin="curated", source_id=sid)
    kb.set_meta("concepts_v", 1)


def constellation(ra: float, dec: float) -> str | None:
    try:
        from astropy.coordinates import SkyCoord, get_constellation
        return get_constellation(SkyCoord(ra, dec, unit="deg"))
    except Exception:
        return None


def fmt_ly(ly: float) -> str:
    if ly >= 1e6:
        return f"{ly / 1e6:.1f}".replace(".", ",") + " Mio. Lichtjahre"
    return f"{ly:,.0f} Lichtjahre".replace(",", ".")


def entity_id_for(ids: list[str], fallback: str) -> str:
    """Bevorzugte Kennung als stabile ID: Messier > NGC > IC > Sh2 > SIMBAD-Hauptkennung."""
    by = {re.split(r"\s", i.strip())[0].upper(): i for i in ids}
    for p in ("M", "NGC", "IC", "SH", "UGC", "MEL"):
        if p in by:
            return slug(by[p])
    return slug(fallback)


def display_name(ids: list[str], main_id: str) -> str:
    """„M45 Pleiades“: kompakte Katalognummer + gebräuchlicher Name aus SIMBAD."""
    nm = next((i[5:].strip() for i in ids if i.startswith("NAME ")), None)
    des = next((re.sub(r"\s+", "", i) if i.startswith("M ") else re.sub(r"\s+", " ", i)
                for p in ("M ", "NGC ", "IC ") for i in ids if i.startswith(p)), None)
    if des and nm:
        return f"{des} {nm}"
    return des or nm or re.sub(r"\s+", " ", main_id.replace("NAME ", ""))


# --------------------------------------------------------------- Validierung -- #
_DIST = re.compile(r"(\d[\d.,]*)\s*(Millionen|Mio\.?|Tausend)?\s*(Lichtjahre\w*|Lj\.?|ly|Parsec|pc)\b", re.I)


def _num(s: str) -> float | None:
    s = s.strip(".,")
    if re.fullmatch(r"\d{1,3}(\.\d{3})+", s):            # 1.350 = tausend
        s = s.replace(".", "")
    s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def validate(article: dict, n_sources: int, distance_ly: float | None) -> list[dict]:
    """Belegpflicht und Plausibilität. Ergänzt je Abschnitt `flags` und `cites`; entfernt Verweise auf unbekannte Quellen."""
    out = []
    for sec in article.get("sections") or []:
        body, flags = str(sec.get("body") or ""), []
        bad = sorted({int(n) for n in re.findall(r"\[(\d+)\]", body) if not 1 <= int(n) <= n_sources})
        if bad:
            flags.append({"type": "unknown_source", "text": f"Verweis auf unbekannte Quelle {', '.join(map(str, bad))} entfernt"})
            body = re.sub(r"\[(\d+)\]", lambda m: m.group(0) if 1 <= int(m.group(1)) <= n_sources else "", body)
        for p in [p for p in re.split(r"\n{2,}", body) if p.strip() and not p.lstrip().startswith("#")]:
            if not re.search(r"\[\d+\]", p) and len(p) > 60:
                flags.append({"type": "uncited", "text": "Ohne Beleg: „" + re.sub(r"\s+", " ", p.strip())[:90] + "…“"})
        if distance_ly:
            for m in _DIST.finditer(body):
                v = _num(m.group(1))
                if v is None:
                    continue
                v *= {"millionen": 1e6, "mio": 1e6, "mio.": 1e6, "tausend": 1e3}.get((m.group(2) or "").lower(), 1)
                ly = v * PC_LY if m.group(3).lower() in ("parsec", "pc") else v
                if ly > 0 and not (0.7 < ly / distance_ly < 1.43):
                    flags.append({"type": "conflict", "text": f"„{m.group(0)}“ weicht vom Katalogwert ({fmt_ly(distance_ly)}) ab"})
        cites = sorted({int(n) for n in re.findall(r"\[(\d+)\]", body)})
        out.append({**sec, "body": body.strip(), "flags": flags, "cites": cites})
    return out


# ------------------------------------------------------------------ Recherche -- #
class Researcher:
    def __init__(self, mod, offline: bool | None = None):
        self.mod = mod
        self.kb = mod.kb
        self.cfg = mod.config()
        self.net = sources.Net(self.kb, offline=not self.cfg.get("catalog_online", True) if offline is None else offline)
        self.simbad = sources.Simbad(self.net)
        self.steps: list[dict] = []
        seed_concepts(self.kb)

    def step(self, text: str, kind: str = "info", **extra):
        self.steps.append({"type": kind, "text": text, "t": time.time(), **extra})

    # ---- sofort, ohne Netz: Eintrag aus dem lokalen Katalog --------------------------------
    def ensure_stub(self, name: str, kind_label: str = "", ra=None, dec=None, size=None) -> dict | None:
        e = self.kb.resolve(name)
        if e:
            return e
        look = self.mod.ctx.service("catalog_resolve")
        c = look(name) if look else None
        if c:
            e = self.kb.resolve(c["id"])
            if e:
                self.kb.add_alias(e["id"], name)
                return e
        if not c and ra is None:
            return None
        eid = slug(c["id"] if c else name)
        e = self.kb.upsert_entity(eid, kind="object", name=c["display"] if c else name, otype_label=(c or {}).get("kind_label") or kind_label,
                                  ra=(c or {}).get("ra", ra), dec=(c or {}).get("dec", dec), size=(c or {}).get("size", size),
                                  status="stub", data={"af_kind": (c or {}).get("kind")})
        for a in {name, *(([c["id"], c["display"], *c.get("aliases", [])]) if c else [])}:
            self.kb.add_alias(eid, a)
        if c and c.get("kind") in KIND_CONCEPT:
            self.kb.add_edge(eid, KIND_CONCEPT[c["kind"]], "ist ein", origin="catalog", note="laut AstroFlow-Katalog")
        return e

    # ---- vollständige Recherche ------------------------------------------------------------
    def research(self, name: str, deep: bool = True, progress=lambda p, m: None) -> dict:
        self.progress = progress
        progress(0.05, "Kennung wird aufgelöst …")
        stub = self.ensure_stub(name)
        look = self.mod.ctx.service("catalog_resolve")
        local = look(name) if look else None
        tries = [x for x in dict.fromkeys([*(stub and stub.get("main_id") and [stub["main_id"]] or []), name,
                                          *([local["id"], *local.get("aliases", [])] if local else [])]) if x]
        info = None
        for t in tries:
            try:
                info = self.simbad.resolve(re.sub(r"^(M|NGC|IC)(\d)", r"\1 \2", t))
            except SourceError as e:
                self.step(f"SIMBAD nicht erreichbar: {e}", "warn")
                break
            if info:
                self.step(f"SIMBAD: „{t}“ → {info['main_id']} ({info['otype_label']})", "tool", tool="simbad_resolve")
                break
        if not info and not stub:
            raise SourceError(f"„{name}“ ist weder im lokalen Katalog noch in SIMBAD zu finden.")
        if not info:
            self.step("Nur lokaler Katalog – Fachkatalog nicht verfügbar.", "warn")
            return stub
        eid = stub["id"] if stub else entity_id_for(info["ids"], info["main_id"])
        e = self._catalog(eid, info, local)
        progress(0.35, "Literatur wird gesammelt …")
        self._literature(e, info)
        if deep:
            llm = agent.LLM(self.cfg, self.kb, purpose="research")
            if llm.ready():
                progress(0.55, "KI recherchiert und schreibt …")
                self._synthesize(e, llm)
            else:
                self.step("Keine KI eingerichtet – nur Katalogwissen gespeichert.", "warn")
        self.kb.upsert_entity(eid, refreshed=time.time())
        return self.kb.entity(eid)

    def _catalog(self, eid: str, o: dict, local: dict | None) -> dict:
        kb = self.kb
        sid = kb.add_source(f"simbad:{o['main_id']}", "catalog", f"SIMBAD – {re.sub(r'\s+', ' ', o['main_id'])}",
                            url=self.simbad.source_url(o["main_id"]), publisher="CDS Straßburg (Spiegel: CfA Harvard)",
                            data={"oid": o["oid"], "mirror": self.simbad.mirror})
        size = o.get("galdim_majaxis")
        kind = o["af_kind"] if o["af_kind"] != "generic" else (local or {}).get("kind")
        # SIMBAD stuft große Nebel oft ungewohnt ein (NGC 7000 als Sternhaufen, NGC 6992 als „sh“): dann zeigt Sage die
        # Objektart des AstroFlow-Katalogs und nennt den SIMBAD-Typ als eigenen Fakt
        label = o["otype_label"]
        if local and local.get("kind_label") and local["kind"] != o["af_kind"] and (o["af_kind"] == "generic" or o["otype"] in VAGUE):
            kind, label = local["kind"], local["kind_label"]
        prev = (kb.entity(eid) or {}).get("status")
        e = kb.upsert_entity(eid, kind="object", name=(local or {}).get("display") or display_name(o["ids"], o["main_id"]),
                             main_id=o["main_id"], otype=o["otype"], otype_label=label, ra=o["ra"], dec=o["dec"],
                             size=size or (local or {}).get("size"), status="catalog" if prev in (None, "stub") else prev,
                             data={"af_kind": kind, "nbref": o.get("nbref"), "simbad_source": sid,
                                   "common_names": [i[5:].strip() for i in o["ids"] if i.startswith("NAME ")]})
        for i in o["ids"]:
            kb.add_alias(eid, re.sub(r"\s+", " ", i))
        kb.add_alias(eid, e["name"])

        def f(key, val, source_id=sid, **kw):
            kb.set_fact(eid, key, val, source_id=source_id, **kw)
        f("ra", f"{o['ra']:.5f}", num=o["ra"], unit="deg")
        f("dec", f"{o['dec']:.5f}", num=o["dec"], unit="deg")
        f("otype", o["otype_label"], note=f"SIMBAD-Typ {o['otype']}" + (f"; AstroFlow-Katalog: {label}" if label != o["otype_label"] else ""))
        con = constellation(o["ra"], o["dec"])
        if con:
            kb.set_fact(eid, "constellation", con, origin="derived", source_id=sid, note="aus den Koordinaten berechnet (astropy)")
        if o.get("sp_type"):
            f("sp_type", o["sp_type"].strip())
        if o.get("morph_type"):
            f("morph_type", o["morph_type"].strip())
        if size:
            f("size", f"{size:.1f}′" + (f" × {o['galdim_minaxis']:.1f}′" if o.get("galdim_minaxis") else ""), num=size, unit="arcmin")
        if o.get("rvz_redshift") and abs(o["rvz_redshift"]) > 1e-4 and o["af_kind"] == "galaxy":
            f("redshift", f"{o['rvz_redshift']:.5f}", num=o["rvz_redshift"])
        if o.get("rvz_radvel") is not None:
            f("radvel", f"{o['rvz_radvel']:.1f} km/s", num=o["rvz_radvel"], unit="km/s")
        if o.get("nbref"):
            f("nbref", str(o["nbref"]), num=o["nbref"], note="Fachartikel, die das Objekt nennen")
        plx_sid = None
        if o.get("plx_value"):
            plx_sid = kb.add_source(f"bib:{o['plx_bibcode']}", "paper", f"Parallaxe {o['plx_bibcode']}", url=sources.ads_url(o["plx_bibcode"]),
                                    year=int(o["plx_bibcode"][:4])) if o.get("plx_bibcode") else sid
            f("parallax", f"{o['plx_value']:.3f} mas", num=o["plx_value"], err=o.get("plx_err"), unit="mas", source_id=plx_sid)
        # Entfernung: alle Messungen mit Beleg, daraus ein bester Wert samt Streuung (Widerspruch = deutlich unterschiedliche Messungen)
        try:
            dm = self.simbad.distances(o["oid"])
        except SourceError as x:
            dm = []
            self.step(f"Entfernungsmessungen nicht abrufbar: {x}", "warn")
        with kb._lock:          # Löschen + Neueintragen unteilbar: ein zweiter Lauf (Hintergrund-Verknüpfung) darf nicht dazwischen lesen/löschen
            kb.x("DELETE FROM facts WHERE entity_id=? AND key='distance' AND origin='catalog'", (eid,))
            for m in sorted(dm, key=lambda m: -(m["year"] or 0))[:12]:
                ms = kb.add_source(f"bib:{m['bibcode']}", "paper", f"Entfernungsmessung {m['bibcode']}", url=sources.ads_url(m["bibcode"]),
                                   year=m["year"])
                kb.set_fact(eid, "distance", f"{m['pc']:,.0f} pc".replace(",", "."), num=m["pc"], err=m["err_pc"], unit="pc", source_id=ms,
                            status="alternative", note=m["method"])
        best = sources.best_distance(o.get("plx_value"), o.get("plx_err"), dm)
        if best:
            note = f"{best['method']}; {best['n']} Messung{'en' if best['n'] != 1 else ''} in SIMBAD"
            if best["spread"]:
                lo, hi = best["spread"]
                note += (f"; Spanne {lo / 1e6:.1f}–{hi / 1e6:.1f} Mpc".replace(".", ",") if hi >= 1e5
                         else f"; Spanne {lo:,.0f}–{hi:,.0f} pc".replace(",", "."))
            kb.set_fact(eid, "distance_ly", fmt_ly(best["pc"] * PC_LY), num=best["pc"] * PC_LY, unit="ly", origin="derived",
                        err=best["err_pc"] * PC_LY if best["err_pc"] else None, source_id=plx_sid if best["method"] == "Parallaxe" else sid,
                        status="conflict" if best["conflict"] else "current", note=note)
            self.step(f"Entfernung {fmt_ly(best['pc'] * PC_LY)} ({note})" + (" – Messungen widersprechen sich" if best["conflict"] else ""),
                      "warn" if best["conflict"] else "info")
        try:
            fl = self.simbad.fluxes(o["oid"])
        except SourceError:
            fl = {}
        band = next((b for b in ("V", "G", "B") if b in fl), None)
        if band:
            m = fl[band]
            ms = kb.add_source(f"bib:{m['bibcode']}", "paper", f"Helligkeit {m['bibcode']}", url=sources.ads_url(m["bibcode"])) if m.get("bibcode") else sid
            kb.set_fact(eid, "mag", f"{m['flux']:.1f} mag ({band})".replace(".", ","), num=m["flux"], err=m.get("flux_err"), unit="mag", source_id=ms)
        if best and size:                                 # wahre Größe aus Winkelgröße und Entfernung
            import math
            dly = 2 * best["pc"] * PC_LY * math.tan(math.radians(size / 60) / 2)
            kb.set_fact(eid, "diameter", f"≈ {dly:,.0f} Lichtjahre".replace(",", ".") if dly >= 10 else f"≈ {dly:.1f} Lichtjahre".replace(".", ","),
                        num=dly, unit="ly", origin="derived", source_id=sid, note="aus Winkelgröße und Entfernung berechnet")
        # Beziehungen: Typ-Konzept, AstroFlow-Art, Hierarchie aus SIMBAD (die vorläufige Typ-Kante des Grundeintrags ersetzen)
        kb.x("DELETE FROM edges WHERE src=? AND rel='ist ein' AND note='laut AstroFlow-Katalog'", (eid,))
        kb.x("DELETE FROM edges WHERE src=? AND rel IN ('ist ein', 'zeigt') AND origin='catalog'", (eid,))
        if kind in KIND_CONCEPT:                         # endgültige Objektart (bei unspezifischem SIMBAD-Typ die des AstroFlow-Katalogs)
            kb.add_edge(eid, KIND_CONCEPT[kind], "ist ein", source_id=sid if kind == o["af_kind"] else None,
                        note="" if kind == o["af_kind"] else "laut AstroFlow-Katalog")
        if local and local.get("kind") in KIND_CONCEPT and local["kind"] != kind:
            kb.add_edge(eid, KIND_CONCEPT[local["kind"]], "zeigt", origin="catalog", note="laut AstroFlow-Katalog")
        try:
            h = self.simbad.hierarchy(o["oid"])
        except SourceError as x:
            h = {"parents": [], "children": []}
            self.step(f"Hierarchie nicht abrufbar: {x}", "warn")
        for p in h["parents"]:
            pid = self._related_stub(p["main_id"], p["otype"])
            ls = kb.add_source(f"bib:{p['link_bibcode']}", "paper", f"Zugehörigkeit {p['link_bibcode']}", url=sources.ads_url(p["link_bibcode"])) \
                if p.get("link_bibcode") else sid
            kb.add_edge(eid, pid, "Mitglied von", source_id=ls, note=f"Zugehörigkeit {p['membership']} %" if p.get("membership") else "")
        for c in h["children"]:
            cid = self._related_stub(c["name"][5:].strip(), c["otype"], main_id=c["main_id"])
            kb.add_edge(cid, eid, "Mitglied von", source_id=sid, note=f"Zugehörigkeit {c['membership']} %" if c.get("membership") else "")
        if h["parents"] or h["children"]:
            self.step(f"Hierarchie: {len(h['parents'])} übergeordnet, {len(h['children'])} benannte Mitglieder", "tool", tool="simbad_hierarchy")
        if not kb.entity(eid)["summary"] or (kb.entity(eid)["data"] or {}).get("summary_origin") in (None, "catalog"):
            kb.upsert_entity(eid, summary=self.catalog_summary(eid), data={"summary_origin": "catalog"})
        return kb.entity(eid)

    def _related_stub(self, name: str, otype: str, main_id: str | None = None) -> str:
        e = self.kb.resolve(main_id or name) or self.kb.resolve(name)
        if e:
            return e["id"]
        label, kind = sources.otype_label(otype)
        name = re.sub(r"^NAME\s+", "", name)
        eid = slug(name)
        self.kb.upsert_entity(eid, kind="object", name=re.sub(r"\s+", " ", name), main_id=main_id or name, otype=otype, otype_label=label,
                              status="stub", data={"af_kind": kind})
        self.kb.add_alias(eid, name)
        if main_id:
            self.kb.add_alias(eid, re.sub(r"\s+", " ", main_id))
        if kind in KIND_CONCEPT:
            self.kb.add_edge(eid, KIND_CONCEPT[kind], "ist ein", origin="catalog")
        return eid

    def catalog_summary(self, eid: str) -> str:
        """Steckbrief-Satz nur aus Katalogwerten (kein Sprachmodell)."""
        e = self.kb.entity(eid)
        d, con = self.kb.fact(eid, "distance_ly"), self.kb.fact(eid, "constellation")
        s = f"{e['name']} ist {'eine' if (e['otype_label'] or '').endswith(('e', 'ion', 'ung', 'gruppe', 'wolke')) else 'ein'} {e['otype_label'] or 'Himmelsobjekt'}"
        if con:
            s += f" im Sternbild {con['value']}"
        if d:
            s += f", rund {d['value']} entfernt" + (" (Messungen streuen)" if d["status"] == "conflict" else "")
        return s + "."

    def _literature(self, e: dict, info: dict):
        kb, eid, papers, pre = self.kb, e["id"], [], []
        try:
            for r in self.simbad.references(info["oid"]):
                papers.append(kb.add_source(f"bib:{r['bibcode']}", "paper", r["title"] or r["bibcode"], url=sources.ads_url(r["bibcode"]),
                                            year=r["year"], publisher=r.get("journal") or "", abstract=r.get("abstract") or "",
                                            data={"doi": r.get("doi"), "obj_freq": r.get("obj_freq")}))
            self.step(f"SIMBAD-Literatur: {len(papers)} Artikel mit Schwerpunkt auf dem Objekt", "tool", tool="simbad_refs")
        except SourceError as x:
            self.step(f"Literatur aus SIMBAD nicht abrufbar: {x}", "warn")
        names = self.search_names(eid)
        try:
            for p in sources.arxiv_search(self.net, names[0], limit=10):
                if self.mentions(p["title"] + " " + p["abstract"], names):
                    pre.append(kb.add_source(f"arxiv:{p['id']}", "preprint", p["title"], url=p["url"], authors=", ".join(p["authors"]),
                                             year=int(p["published"][:4]) if p["published"][:4].isdigit() else None,
                                             publisher="arXiv", abstract=p["abstract"], data={"published": p["published"]}))
            self.step(f"arXiv: {len(pre)} passende Preprints zu „{names[0]}“", "tool", tool="arxiv")
        except (SourceError, Exception) as x:
            self.step(f"arXiv nicht abrufbar: {x}", "warn")
        token = self.cfg.get("ads_token")
        if token:
            try:
                for d in sources.ads_search(self.net, token, f'object:"{names[0]}"', limit=8):
                    papers.append(kb.add_source(f"bib:{d['bibcode']}", "paper", d["title"], url=sources.ads_url(d["bibcode"]), year=d["year"],
                                                authors=", ".join(d["authors"]), abstract=d["abstract"],
                                                data={"doi": d.get("doi"), "citations": d.get("citations")}))
                self.step("NASA ADS: neueste Artikel ergänzt", "tool", tool="ads")
            except SourceError as x:
                self.step(f"NASA ADS: {x}", "warn")
        data = e.get("data") or {}
        kb.upsert_entity(eid, data={"papers": list(dict.fromkeys([*papers, *(data.get("papers") or [])]))[:30],
                                    "preprints": list(dict.fromkeys([*pre, *(data.get("preprints") or [])]))[:20]})

    def search_names(self, eid: str) -> list[str]:
        """Suchbegriffe: gebräuchlicher englischer Name (SIMBAD NAME), dann Katalognummern."""
        al = self.kb.aliases(eid)
        e = self.kb.entity(eid)
        common = (e["data"] or {}).get("common_names") or [a[5:] for a in al if a.startswith("NAME ")]
        names = sorted(common, key=lambda n: (n.lower() not in e["name"].lower(), len(n)))
        names += [a for a in al if re.match(r"^(M|NGC|IC) ?\d+$", a)]
        return list(dict.fromkeys([*names, e["name"]]))[:4]

    @staticmethod
    def mentions(text: str, names: list[str]) -> bool:
        t = re.sub(r"\s+", " ", text.lower())
        for n in names:
            n = n.lower().strip()
            pat = re.escape(n).replace(r"\ ", r"\s?")
            if re.search(rf"(?<![\w]){pat}(?![\d])", t):
                return True
        return False

    # ---- KI: vertiefen und belegt schreiben ---------------------------------------------------
    def _synthesize(self, e: dict, llm: agent.LLM):
        kb, eid = self.kb, e["id"]
        data = e.get("data") or {}
        src_ids = [data.get("simbad_source"), *(data.get("papers") or [])[:12], *(data.get("preprints") or [])[:6]]
        cur = kb.source(kb.one("SELECT id FROM sources WHERE key=?", (CURATED_KEY,))["id"])
        numbered = [s for s in [*(kb.source(i) for i in src_ids if i), cur] if s]

        def listing(s, i):
            ab = (s.get("abstract") or "")[:350]
            return f"[{i}] {s['title']} ({s.get('year') or 'o. J.'}; {s['kind']})" + (f"\n    {ab}…" if ab else "")

        facts = [f for f in kb.facts(eid) if f["status"] in ("current", "conflict")]
        fact_txt = "\n".join(f"- {f['key']}: {f['value']}" + (f" ({f['note']})" if f["note"] else "") + (" [WIDERSPRUCH]" if f["status"] == "conflict" else "")
                             for f in facts)

        def paper(n: int):
            n = int(n)
            if not 1 <= n <= len(numbered):
                raise ValueError("keine Quelle mit dieser Nummer")
            s = numbered[n - 1]
            return {"nr": n, "titel": s["title"], "jahr": s.get("year"), "abstract": s.get("abstract") or "(kein Abstract)"}

        def arxiv(query: str):
            res = []
            for p in sources.arxiv_search(self.net, query, limit=6):
                sid = kb.add_source(f"arxiv:{p['id']}", "preprint", p["title"], url=p["url"], authors=", ".join(p["authors"]),
                                    year=int(p["published"][:4]), publisher="arXiv", abstract=p["abstract"])
                numbered.append(kb.source(sid))
                res.append({"nr": len(numbered), "titel": p["title"], "datum": p["published"], "abstract": p["abstract"][:500]})
            return res

        def lookup(name: str):
            o = self.simbad.resolve(name)
            if not o:
                return {"fehler": "nicht in SIMBAD"}
            sid = kb.add_source(f"simbad:{o['main_id']}", "catalog", f"SIMBAD – {o['main_id']}", url=self.simbad.source_url(o["main_id"]))
            numbered.append(kb.source(sid))
            return {"nr": len(numbered), "kennung": o["main_id"], "typ": o["otype_label"], "ra": o["ra"], "dec": o["dec"],
                    "parallaxe_mas": o.get("plx_value"), "kennungen": o["ids"][:12]}

        tools = [agent.tool("paper_details", "Volltext-Abstract einer nummerierten Quelle lesen.", paper, n=("integer", "Quellennummer")),
                 agent.tool("arxiv_search", "Neueste arXiv-Preprints suchen (englische Suchbegriffe). Neue Treffer bekommen Quellennummern.", arxiv,
                            query=("string", "Suchbegriff, z. B. 'Pleiades brown dwarfs'")),
                 agent.tool("simbad_lookup", "Ein verwandtes Objekt in SIMBAD nachschlagen (Typ, Position, Kennungen).", lookup,
                            name=("string", "Kennung, z. B. 'NGC 1435'"))]
        system = ("Du bist der Recherche-Agent einer astronomischen Enzyklopädie. Messwerte stammen ausschließlich aus den Katalogfakten; "
                  "du erfindest keine Zahlen. Jede inhaltliche Aussage braucht einen Beleg als [n] aus der Quellenliste; was du nicht "
                  "belegen kannst, lässt du weg oder kennzeichnest es als offene Frage. Widersprüche zwischen Quellen benennst du offen. "
                  "Nutze Werkzeuge sparsam (höchstens 4 Aufrufe), um Abstracts zu lesen oder neue Forschung zu finden. Schreibe auf Deutsch, "
                  "verständlich für interessierte Laien, aber fachlich korrekt.")
        user = (f"Objekt: {e['name']} (SIMBAD: {e['main_id']}, Typ: {e['otype_label']})\n\nKatalogfakten:\n{fact_txt}\n\n"
                "Quellen:\n" + "\n".join(listing(s, i + 1) for i, s in enumerate(numbered)) +
                "\n\nAntworte am Ende ausschließlich mit JSON:\n"
                '{"summary": "2–3 Sätze mit [n]", "sections": [{"key": "overview|physics|history|research|open_questions|observing", '
                '"title": "…", "body": "Markdown-Absätze, jeder mit [n]"}], "relations": [{"name": "Kennung eines verwandten Objekts", '
                '"rel": "kurze Beziehung, z. B. „liegt vor“, „gleiche Sternentstehungsregion“", "sources": [n], "reason": "…"}], '
                '"uncertainties": ["…"]}\nSchreibe 4–6 Abschnitte, darunter „research“ (was neue Arbeiten zeigen) und „open_questions“.')
        run = kb.start_run("research", eid, llm.model)
        llm.run_id = run
        try:
            n_steps = int(self.cfg.get("max_steps") or 6)
            done = []
            names = {"paper_details": "liest einen Fachartikel", "arxiv_search": "sucht neue Preprints", "simbad_lookup": "schlägt in SIMBAD nach"}

            def on_step(st):                                  # Fortschritt 55 → 85 % über die Werkzeugschritte, danach Schreiben
                done.append(st)
                getattr(self, "progress", lambda p, m: None)(min(0.85, 0.55 + 0.3 * len(done) / n_steps),
                                                            f"KI {names.get(st.get('tool'), 'arbeitet')} …")
            text, steps = agent.run_agent(llm, system, user, tools, max_steps=n_steps, json_mode=True, on_step=on_step,
                                          final_hint="Keine Werkzeuge mehr. Antworte jetzt ausschließlich mit dem JSON-Objekt.",
                                          max_tokens=4000)
            getattr(self, "progress", lambda p, m: None)(0.9, "Text wird geprüft und gespeichert …")
            art = agent.parse_json(text)
        except Exception as x:
            kb.finish_run(run, "error", self.steps, llm.cost, llm.tokens, str(x))
            self.step(f"KI-Recherche abgebrochen: {x}", "warn")
            return
        self.steps += [{**s, "text": s.get("text") or f"KI nutzt {s.get('tool')}: {s.get('summary')}"} for s in steps]
        dist = kb.fact(eid, "distance_ly")
        secs = validate(art, len(numbered), dist["num"] if dist else None)
        for sec in secs:
            key = sec.get("key") if sec.get("key") in SECTION_TITLES else "overview"
            kb.put_section(eid, key, sec.get("title") or SECTION_TITLES[key], sec["body"], sources=[numbered[n - 1]["id"] for n in sec["cites"]],
                           flags=sec["flags"], origin="ai", status="draft", model=llm.model, run_id=run)
        summ = validate({"sections": [{"body": art.get("summary") or ""}]}, len(numbered), dist["num"] if dist else None)[0]
        if summ["body"]:
            refs = {n: numbered[n - 1]["id"] for n in summ["cites"]}
            kb.upsert_entity(eid, summary=re.sub(r"\[(\d+)\]", lambda m: f"[S{refs.get(int(m.group(1)), '?')}]", summ["body"]),
                             data={"summary_origin": "ai", "summary_flags": summ["flags"], "uncertainties": art.get("uncertainties") or []})
        # Querverweise in den Abschnitten auf globale Quellen-IDs umschreiben ([3] → [S17]) – stabil über Versionen
        for s in kb.sections(eid):
            if s["run_id"] == run:
                body = re.sub(r"\[(\d+)\]", lambda m: f"[S{numbered[int(m.group(1)) - 1]['id']}]" if 1 <= int(m.group(1)) <= len(numbered) else "", s["body"])
                kb.x("UPDATE sections SET body=? WHERE id=?", (body, s["id"]))
        n_rel = 0
        for r in art.get("relations") or []:
            target = self.kb.resolve(str(r.get("name") or ""))
            if not target:
                try:
                    o = self.simbad.resolve(str(r.get("name") or ""))
                except SourceError:
                    o = None
                if not o:
                    continue                                 # nur Objekte, die es im Fachkatalog gibt
                target = self.kb.entity(self._related_stub(o["main_id"], o["otype"], main_id=o["main_id"]))
            cites = [numbered[int(n) - 1]["id"] for n in r.get("sources") or [] if str(n).isdigit() and 1 <= int(n) <= len(numbered)]
            if target and target["id"] != eid:
                kb.add_edge(eid, target["id"], str(r.get("rel") or "verwandt mit")[:60], origin="ai", status="suggested",
                            source_id=cites[0] if cites else None, note=str(r.get("reason") or "")[:300])
                n_rel += 1
        kb.upsert_entity(eid, status="researched" if e["status"] != "reviewed" else "reviewed")
        flags = sum(len(s["flags"]) for s in secs)
        self.step(f"KI schrieb {len(secs)} Abschnitte, {n_rel} Beziehungsvorschläge; {flags} Prüfhinweis{'e' if flags != 1 else ''}", "info")
        kb.finish_run(run, "done", self.steps, llm.cost, llm.tokens)


def angular_sep(ra1, dec1, ra2, dec2) -> float:
    r = math.radians
    c = math.sin(r(dec1)) * math.sin(r(dec2)) + math.cos(r(dec1)) * math.cos(r(dec2)) * math.cos(r(ra1 - ra2))
    return math.degrees(math.acos(max(-1.0, min(1.0, c))))
