"""Fachquellen für die Recherche: SIMBAD (TAP), arXiv, NASA ADS, SkyView – mit Antwort-Cache in der Wissensbasis.

Messwerte kommen von hier, nie aus dem Sprachmodell. Der Cache macht Läufe reproduzierbar (gleiche Antwort, Abrufzeit
gespeichert) und spart Abfragen. CDS Straßburg war nicht erreichbar → SIMBAD-Spiegel in Harvard."""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
import xml.etree.ElementTree as ET
from urllib.parse import quote

import httpx

UA = "AstroFlow-Sage/2.0 (lokale Astrofotografie-Software; https://github.com/JackONeill199/astroflow)"
SIMBAD = ("https://simbad.cfa.harvard.edu/simbad/sim-tap/sync", "https://simbad.cds.unistra.fr/simbad/sim-tap/sync")
ARXIV = "https://export.arxiv.org/api/query"
ADS = "https://api.adsabs.harvard.edu/v1/search/query"
SKYVIEW = "https://skyview.gsfc.nasa.gov/current/cgi/runquery.pl"
DAY = 86400.0
PC_LY = 3.26156

# SIMBAD-Objekttypen → deutsche Bezeichnung + AstroFlow-Art (für Profile, Konzepte und Farben im Wissensnetz)
OTYPES = {
    "OpC": ("Offener Sternhaufen", "open_cluster"), "GlC": ("Kugelsternhaufen", "globular"), "Cl*": ("Sternhaufen", "open_cluster"),
    "As*": ("Sternassoziation", "open_cluster"), "MGr": ("Bewegungsgruppe", "open_cluster"),
    "RNe": ("Reflexionsnebel", "reflection"), "HII": ("HII-Region (Emissionsnebel)", "emission"), "EmO": ("Emissionsobjekt", "emission"),
    "ISM": ("Interstellare Materie", "emission"), "Cld": ("Wolke", "emission"), "GNe": ("Galaktischer Nebel", "emission"),
    "DNe": ("Dunkelwolke", "dark"), "MoC": ("Molekülwolke", "dark"), "SFR": ("Sternentstehungsgebiet", "emission"),
    "PN": ("Planetarischer Nebel", "planetary"), "SNR": ("Supernova-Überrest", "snr"), "Bub": ("Blase (Sternwind)", "emission"),
    "G": ("Galaxie", "galaxy"), "Sy1": ("Seyfert-1-Galaxie", "galaxy"), "Sy2": ("Seyfert-2-Galaxie", "galaxy"), "SyG": ("Seyfert-Galaxie", "galaxy"),
    "LIN": ("LINER-Galaxie", "galaxy"), "SBG": ("Starburst-Galaxie", "galaxy"), "AGN": ("Aktiver Galaxienkern", "galaxy"),
    "IG": ("Wechselwirkende Galaxien", "galaxy"), "GiG": ("Galaxie in Gruppe", "galaxy"), "GiC": ("Galaxie im Haufen", "galaxy"),
    "BiC": ("Hellste Galaxie im Haufen", "galaxy"), "rG": ("Radiogalaxie", "galaxy"), "H2G": ("HII-Galaxie", "galaxy"),
    "EmG": ("Emissionslinien-Galaxie", "galaxy"), "GrG": ("Galaxiengruppe", "galaxy"), "ClG": ("Galaxienhaufen", "galaxy"),
    "QSO": ("Quasar", "galaxy"), "*": ("Stern", "star"), "**": ("Doppelstern", "star"), "PM*": ("Stern (Eigenbewegung)", "star"),
    "V*": ("Veränderlicher Stern", "star"), "Be*": ("Be-Stern", "star"), "WR*": ("Wolf-Rayet-Stern", "star"), "BY*": ("BY-Draconis-Stern", "star"),
    "Em*": ("Emissionslinienstern", "star"), "SB*": ("Spektroskopischer Doppelstern", "star"), "EB*": ("Bedeckungsveränderlicher", "star"),
    "Psr": ("Pulsar", "star"), "sh": ("HI-Schale", "snr"), "HVC": ("Hochgeschwindigkeitswolke", "emission"),
    "Neb": ("Nebel", "emission"), "smm": ("Submillimeter-Quelle", "dark"), "Rad": ("Radioquelle", "generic"), "C*": ("Kohlenstoffstern", "star"), "RG*": ("Roter Riese", "star"), "s*b": ("Blauer Überriese", "star"),
}


class SourceError(Exception):
    pass


def otype_label(otype: str) -> tuple[str, str]:
    return OTYPES.get((otype or "").strip(), (otype or "unbekannter Typ", "generic"))


class Net:
    """HTTP mit Cache (Tabelle `http_cache`). `offline=True` liefert nur Gecachtes."""

    def __init__(self, kb, offline: bool = False, timeout: float = 30):
        self.kb = kb
        self.offline = offline
        self.timeout = timeout
        self.calls = 0

    def fetch(self, url: str, data: dict | None = None, params: dict | None = None, headers: dict | None = None,
              ttl: float = 30 * DAY, binary: bool = False):
        key = hashlib.sha1(json.dumps([url, data, params], sort_keys=True).encode()).hexdigest()
        # bei jedem Abruf prüfen, nicht nur beim Anlegen: Hintergrundthreads (Recherche, Entdeckungen) laufen länger als
        # die Einstellung, mit der sie gestartet wurden (Tests: ASTROFLOW_SAGE_OFFLINE wird nach dem Test wieder 1)
        offline = self.offline or os.environ.get("ASTROFLOW_SAGE_OFFLINE") == "1"
        if not binary:
            hit = self.kb.one("SELECT body, fetched FROM http_cache WHERE key=?", (key,))
            if hit and (offline or time.time() - hit["fetched"] < ttl):
                return hit["body"]
        if offline:
            raise SourceError("offline – keine zwischengespeicherte Antwort")
        self.calls += 1
        try:
            h = {"User-Agent": UA, **(headers or {})}
            r = (httpx.post(url, data=data, headers=h, timeout=self.timeout) if data is not None
                 else httpx.get(url, params=params, headers=h, timeout=self.timeout, follow_redirects=True))
        except httpx.HTTPError as e:
            raise SourceError(f"{url.split('/')[2]} nicht erreichbar ({type(e).__name__})") from None
        if r.status_code >= 400:
            msg = re.findall(r"<INFO[^>]*>([^<]{3,})", r.text)
            raise SourceError(f"{url.split('/')[2]}: HTTP {r.status_code} {(msg[0] if msg else r.text[:160]).strip()}")
        if binary:
            return r.content
        self.kb.x("INSERT OR REPLACE INTO http_cache(key, url, body, fetched) VALUES(?,?,?,?)", (key, url, r.text, time.time()))
        return r.text


# --------------------------------------------------------------------- SIMBAD -- #
def _esc(s: str) -> str:
    return (s or "").replace("'", "''")


class Simbad:
    def __init__(self, net: Net):
        self.net = net
        self.mirror = net.kb.meta("simbad_mirror", SIMBAD[0])

    def tap(self, adql: str) -> list[dict]:
        errors = []
        for url in dict.fromkeys([self.mirror, *SIMBAD]):
            try:
                body = self.net.fetch(url, data={"REQUEST": "doQuery", "LANG": "ADQL", "FORMAT": "json", "QUERY": adql})
            except SourceError as e:
                errors.append(str(e))
                if "HTTP 400" in str(e):                    # Abfragefehler: Spiegel probieren hilft nicht
                    break
                continue
            if url != self.mirror:
                self.mirror = url
                self.net.kb.set_meta("simbad_mirror", url)
            d = json.loads(body)
            cols = [c["name"] for c in d["metadata"]]
            return [dict(zip(cols, row)) for row in d["data"]]
        raise SourceError("SIMBAD: " + "; ".join(errors))

    @staticmethod
    def source_url(main_id: str) -> str:
        return "https://simbad.cfa.harvard.edu/simbad/sim-id?Ident=" + quote(main_id)

    def resolve(self, name: str) -> dict | None:
        rows = self.tap("SELECT b.oid, b.main_id, b.otype, b.ra, b.dec, b.plx_value, b.plx_err, b.plx_bibcode, b.sp_type, "
                        "b.rvz_redshift, b.rvz_radvel, b.galdim_majaxis, b.galdim_minaxis, b.morph_type, b.nbref "
                        f"FROM basic AS b JOIN ident AS i ON i.oidref=b.oid WHERE i.id='{_esc(name)}'")
        if not rows:
            return None
        o = rows[0]
        o["ids"] = [r["id"] for r in self.tap(f"SELECT id FROM ident WHERE oidref={int(o['oid'])}")]
        o["otype_label"], o["af_kind"] = otype_label(o["otype"])
        return o

    def distances(self, oid: int) -> list[dict]:
        rows = self.tap(f"SELECT dist, unit, minus_err, plus_err, method, bibcode FROM mesDistance WHERE oidref={int(oid)}")
        out = []
        for r in rows:
            try:
                v = float(r["dist"])
            except (TypeError, ValueError):
                continue
            unit = (r["unit"] or "").strip().lower()
            f = {"pc": 1.0, "kpc": 1e3, "mpc": 1e6}.get(unit)
            if not f or v <= 0:
                continue
            err = max(abs(r["minus_err"] or 0), abs(r["plus_err"] or 0)) * f or None
            out.append({"pc": v * f, "err_pc": err, "method": (r["method"] or "").strip(), "bibcode": r["bibcode"],
                        "year": int(r["bibcode"][:4]) if (r["bibcode"] or "")[:4].isdigit() else None})
        return out

    def hierarchy(self, oid: int, limit: int = 12) -> dict:
        """Übergeordnete Objekte (Mitglied von) und benannte Mitglieder (z. B. die hellen Plejadensterne)."""
        parents = self.tap(f"SELECT DISTINCT b.main_id, b.otype, h.membership, h.link_bibcode FROM h_link AS h JOIN basic AS b ON b.oid=h.parent "
                           f"WHERE h.child={int(oid)} AND (h.membership IS NULL OR h.membership >= 50)")
        children = self.tap(f"SELECT DISTINCT TOP {int(limit) * 8} b.main_id, b.otype, i.id AS name, h.membership, h.link_bibcode FROM h_link AS h "
                            f"JOIN basic AS b ON b.oid=h.child JOIN ident AS i ON i.oidref=h.child "
                            f"WHERE h.parent={int(oid)} AND i.id LIKE 'NAME %' AND (h.membership IS NULL OR h.membership >= 50)")
        # je Objekt nur eine Zeile (SIMBAD liefert eine Zeile je Zugehörigkeitsbeleg und Name)
        uniq = lambda rows: list({r["main_id"]: r for r in reversed(rows)}.values())[::-1]
        return {"parents": uniq(parents), "children": uniq(children)[:int(limit)]}

    def references(self, oid: int, limit: int = 12, min_freq: int = 3) -> list[dict]:
        """Artikel, in denen das Objekt wesentlich vorkommt (obj_freq), neueste zuerst – mit Abstract."""
        rows = self.tap(f"SELECT TOP {int(limit)} r.bibcode, r.title, r.\"year\" AS yr, r.journal, r.doi, r.abstract, h.obj_freq "
                        f"FROM has_ref AS h JOIN ref AS r ON r.oidbib=h.oidbibref WHERE h.oidref={int(oid)} AND h.obj_freq >= {int(min_freq)} "
                        "ORDER BY yr DESC")
        return [{**r, "year": r.pop("yr")} for r in rows]

    def fluxes(self, oid: int) -> dict:
        """Helligkeiten (mag) je Filter, z. B. {"V": 1.6, "B": 1.7}."""
        rows = self.tap(f"SELECT filter, flux, flux_err, bibcode FROM flux WHERE oidref={int(oid)} AND filter IN ('V', 'B', 'G')")
        return {r["filter"].strip(): r for r in rows if r.get("flux") is not None}

    def cone(self, ra: float, dec: float, radius: float, otype: str = "G..", min_size: float = 0.5, limit: int = 40) -> list[dict]:
        return self.tap(f"SELECT TOP {int(limit)} main_id, otype, ra, dec, galdim_majaxis, nbref FROM basic "
                        f"WHERE CONTAINS(POINT('ICRS', ra, dec), CIRCLE('ICRS', {float(ra)}, {float(dec)}, {float(radius)}))=1 "
                        f"AND otype='{_esc(otype)}' AND galdim_majaxis >= {float(min_size)} ORDER BY galdim_majaxis DESC")


def best_distance(plx_mas: float | None, plx_err: float | None, measures: list[dict]) -> dict | None:
    """Entfernung: Parallaxe (wenn genau genug), sonst Median der Messungen ab 2000. Dazu Streuung und Widerspruchsflagge."""
    recent = [m for m in measures if (m["year"] or 0) >= 2000] or measures
    vals = sorted(m["pc"] for m in recent)
    spread = None
    if len(vals) >= 2:
        lo, hi = vals[max(0, len(vals) // 10)], vals[min(len(vals) - 1, len(vals) - 1 - len(vals) // 10)]
        spread = (lo, hi)
    if plx_mas and plx_mas > 0 and (not plx_err or plx_err / plx_mas < 0.2):
        d = {"pc": 1000.0 / plx_mas, "err_pc": 1000.0 * plx_err / plx_mas ** 2 if plx_err else None, "method": "Parallaxe", "n": len(vals)}
    elif vals:
        d = {"pc": vals[len(vals) // 2], "err_pc": None, "method": "Median der Messungen", "n": len(vals)}
    else:
        return None
    d["spread"] = spread
    d["conflict"] = bool(spread and spread[1] / max(spread[0], 1e-9) > 1.5)
    return d


# ---------------------------------------------------------------------- arXiv -- #
def detex(t: str) -> str:
    """LaTeX-Reste aus arXiv-Titeln entfernen („Activity$-$Rotation“ → „Activity–Rotation“)."""
    t = re.sub(r"\$\s*-\s*\$", "–", t or "")
    t = re.sub(r"\\(?:rm|it|bf|mathrm)\b|\\sim\b|[{}$]", "", t)
    return re.sub(r"\s+", " ", t).strip()


def arxiv_search(net: Net, query: str, limit: int = 8, ttl: float = DAY) -> list[dict]:
    q = " AND ".join(f'all:"{t}"' if " " in t else f"all:{t}" for t in [query])
    body = net.fetch(ARXIV, params={"search_query": q, "sortBy": "submittedDate", "sortOrder": "descending", "max_results": limit},
                     ttl=ttl)
    ns = {"a": "http://www.w3.org/2005/Atom"}
    out = []
    for e in ET.fromstring(body).findall("a:entry", ns):
        aid = re.sub(r"v\d+$", "", (e.findtext("a:id", "", ns) or "").rsplit("/abs/", 1)[-1])
        out.append({"id": aid, "title": detex(e.findtext("a:title", "", ns)),
                    "abstract": re.sub(r"\s+", " ", e.findtext("a:summary", "", ns)).strip(),
                    "authors": [a.findtext("a:name", "", ns) for a in e.findall("a:author", ns)][:6],
                    "published": e.findtext("a:published", "", ns)[:10], "url": f"https://arxiv.org/abs/{aid}"})
    return out


# ------------------------------------------------------------------- NASA ADS -- #
def ads_search(net: Net, token: str, query: str, limit: int = 8) -> list[dict]:
    if not token:
        raise SourceError("kein NASA-ADS-Token eingetragen")
    body = net.fetch(ADS, params={"q": query, "fl": "bibcode,title,year,author,abstract,doi,citation_count", "rows": limit,
                                  "sort": "date desc"}, headers={"Authorization": f"Bearer {token}"}, ttl=DAY)
    docs = json.loads(body).get("response", {}).get("docs", [])
    return [{"bibcode": d["bibcode"], "title": (d.get("title") or [""])[0], "year": int(d.get("year") or 0) or None,
             "authors": (d.get("author") or [])[:6], "abstract": d.get("abstract") or "", "doi": (d.get("doi") or [None])[0],
             "citations": d.get("citation_count")} for d in docs]


def ads_url(bibcode: str) -> str:
    return f"https://ui.adsabs.harvard.edu/abs/{bibcode}/abstract"


# -------------------------------------------------------------------- SkyView -- #
def sky_image(net: Net, ra: float, dec: float, fov_deg: float, pixels: int = 900) -> bytes:
    """Farbiges DSS2-Himmelsbild (IR/Rot/Blau → RGB) als JPG – für Objekte ohne eigene Aufnahme."""
    import io

    import numpy as np
    from PIL import Image
    chans = []
    for survey in ("DSS2 IR", "DSS2 Red", "DSS2 Blue"):
        raw = net.fetch(SKYVIEW, params={"Survey": survey, "Position": f"{ra:.5f},{dec:.5f}", "Size": f"{fov_deg:.3f}",
                                         "Pixels": str(pixels), "Return": "JPEG", "Scaling": "Log"}, binary=True)
        try:
            chans.append(np.asarray(Image.open(io.BytesIO(raw)).convert("L"), dtype=np.float32))
        except Exception:
            raise SourceError("SkyView lieferte kein Bild") from None
    if len({c.shape for c in chans}) != 1:
        chans = [chans[1]] * 3
    # Je Kanal strecken: die drei Platten haben verschiedene Himmelshelligkeit – gemeinsam gestreckt wurde der Hintergrund gelb/orange
    out_ch = []
    for c in chans:
        bg, pk = np.median(c), np.percentile(c, 99.8)
        out_ch.append(np.clip(0.05 + 0.95 * (c - bg) / max(pk - bg, 1), 0, 1))
    rgb = np.stack(out_ch, -1) ** 0.9
    out = io.BytesIO()
    Image.fromarray((rgb * 255).astype(np.uint8)).save(out, "JPEG", quality=88)
    return out.getvalue()
