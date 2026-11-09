"""Erzeugt astroflow/modules/stacking/katalogdaten/messier_caldwell.csv aus OpenNGC und Wikipedia (nur beim Entwickeln, zur Laufzeit kein Netz).

Quellen (beide CC-BY-SA 4.0), feste Fassungen siehe Konstanten unten:
- OpenNGC (https://github.com/mattiaverga/OpenNGC): `NGC.csv`, `addendum.csv` – Position, Kennungen, Galaxiengrößen, Rückfall-Helligkeit.
- Wikipedia (en) „Messier object“ (Tabelle, Werte aus SEDS/Sky Catalogue) und „Caldwell catalogue“ – Helligkeit, sichtbare Größe (nur Messier).
  Als Wikitext per `https://en.wikipedia.org/w/api.php?action=query&prop=revisions&titles=<Titel>&rvprop=ids|timestamp|content&rvslots=main&format=json&formatversion=2`
  (Revision `oldid` fest) laden und den Wikitext als `Messier_object.wiki` bzw. `Caldwell_catalogue.wiki` in denselben Ordner legen.

Aufruf: python tools/openngc_extract.py <ordner-mit-NGC.csv-addendum.csv-und-*.wiki> [--artikel-holen] [--alt <alte deepsky.py>]
`--artikel-holen` lädt (mit Netz) die Wikipedia-Einzelartikel der Caldwell-Objekte als `Caldwell_artikel.json` in den Ordner; die Datei wird
zum Erzeugen zwingend gebraucht und beim ersten Lauf in einem frischen Ordner mit diesem Schalter angelegt.
`--alt` vergleicht zusätzlich mit den alten Handtabellen (`git show 84892e8:astroflow/modules/stacking/deepsky.py > alt.py`).
Immer ausgegeben: Abweichungen zwischen den Quellen (Helligkeit > 0,7 mag, Größe > 40 %, Position > 3′).

Vorrangregeln (Spalten `mag_src`, `size_src`: W = Wikipedia, O = OpenNGC V, B = OpenNGC B−0,85 (Galaxien), H = von Hand, unverifiziert):
- Zuordnung/Position: OpenNGC. Messier über Spalte `M` (M102 = NGC 5866, bei OpenNGC Dublette von M101); Caldwell über `C 0nn` in `Identifiers`
  bzw. Name `C0nn` (addendum.csv).
- Helligkeit: Wikipedia; fehlt sie dort: OpenNGC. OpenNGC-V ist bei Galaxien teils unbrauchbar (NGC 253: B 7,94 / V 11,11) → bei Galaxien V nur,
  wenn B vorhanden ist und 0,3 ≤ B−V ≤ 1,3 (oder B fehlt); sonst B − 0,85. Bei Nicht-Galaxien V, ersatzweise B.
- Größe (Hauptachse in Bogenminuten): Galaxien OpenNGC (D25), bei größerem Wikipedia-Wert (Edge-on, M108) dieser. Messier-Nichtgalaxien Wikipedia (sichtbare Ausdehnung), ersatzweise OpenNGC.
  Caldwell-Nichtgalaxien: OpenNGC; bei Haufen (OCl/GCl/Assoziation) ist `MajAx` ein Kernmaß (MWSC) und kleiner als die sichtbare Ausdehnung –
  dort gilt der Handwert (HAND_SIZE_C), außer OpenNGC liegt innerhalb 40 % davon. Fehlt alles: HAND_SIZE.
"""
from __future__ import annotations

import csv
import math
import re
import sys
from pathlib import Path


OPENNGC_VERSION = "v20260501"
OPENNGC_COMMIT = "36cb178a0f69dba8bfc03a99c10512831edf1c6b"
WIKI_MESSIER = ("Messier object", 1372398875, "2026-08-31")           # en.wikipedia.org, Tabelle „List of Messier objects“ (Weiterleitung)
WIKI_CALDWELL = ("Caldwell catalogue", 1383500944, "2026-11-03")
OUT = Path(__file__).resolve().parent.parent / "astroflow" / "modules" / "stacking" / "katalogdaten" / "messier_caldwell.csv"

KOPF = f"""# Messier- und Caldwell-Katalog (Position J2000, Helligkeit, sichtbare Größe) – zusammengestellt aus zwei Quellen
# 1) OpenNGC {OPENNGC_VERSION} (https://github.com/mattiaverga/OpenNGC, Commit {OPENNGC_COMMIT}; NGC.csv, addendum.csv), Mattia Verga und Mitwirkende, CC-BY-SA 4.0
# 2) Wikipedia (en): „{WIKI_MESSIER[0]}“ (Revision {WIKI_MESSIER[1]}, {WIKI_MESSIER[2]}; Werte dort aus SEDS/Sky Catalogue) und „{WIKI_CALDWELL[0]}“
#    (Revision {WIKI_CALDWELL[1]}, {WIKI_CALDWELL[2]}) sowie Infobox-Größen der Einzelartikel (Revisionen: README.md), Autoren siehe Versionsgeschichte, CC-BY-SA 4.0
# Lizenz der Daten: Creative Commons Attribution-ShareAlike 4.0 (CC-BY-SA 4.0), siehe LICENSE-OpenNGC in diesem Ordner. Weitergabe (auch veränderter
# Fassungen) nur unter denselben Bedingungen und mit dieser Namensnennung. Änderungen: Auswahl, Zusammenführung, Umrechnung (RA in Grad, Größe in Bogenminuten).
# Der AstroFlow-Code steht unter PolyForm Noncommercial 1.0.0. mag_src/size_src: W = Wikipedia, O = OpenNGC V, B = OpenNGC B-Helligkeit minus 0,85 (Galaxien), H = von Hand (unverifiziert).
# C46 (Hubbles veränderlicher Nebel) ist veränderlich (~9-10 mag). Vorrangregeln: tools/openngc_extract.py. Erzeugt mit diesem Skript – nicht von Hand ändern.
"""
SPALTEN = ["cat", "num", "openngc", "ra_deg", "dec_deg", "typ", "mag", "mag_src", "size_arcmin", "size_src"]

# Von Hand (unverifiziert, nur wo keine Quelle etwas liefert; Helligkeit braucht keinen Handwert mehr, Wikipedia kennt alle außer C46/C68/C99 → OpenNGC bzw. leer)
HAND_SIZE = {("M", 40): 1.0, ("M", 73): 3.0, ("C", 34): 70.0, ("C", 94): 10.0, ("C", 88): 10.0}   # C99: Wikipedia-Artikel 7° × 5°
# Begründung: C34 NGC 6960 – OpenNGC (210′) und Wikipedia (3°) meinen den ganzen Cirrus-Komplex, ~70′ für den westlichen Schleier ist Literaturwert;
# C94 Juwelenkästchen, C88 NGC 5823 – Wikipedia-Artikel ohne Größe, OpenNGC-MajAx ist Kernmaß.
VARIABEL = {46}            # C46 Hubbles veränderlicher Nebel: ~9–10 mag, schwankt mit R Mon
CLUSTER = ("OCl", "GCl", "*Ass", "Cl+N")
GALAXIE = ("G", "GPair", "GTrpl", "GGroup")       # nicht „GCl“ (Kugelhaufen)
TOL_MAG, TOL_SIZE, TOL_POS = 0.7, 0.4, 3.0


def _hms(s: str, stunden: bool) -> float:
    m = re.match(r"^([+-]?)(\d+):(\d+):([\d.]+)$", s.strip())
    if not m:
        raise ValueError(s)
    wert = int(m.group(2)) + int(m.group(3)) / 60 + float(m.group(4)) / 3600
    wert = -wert if m.group(1) == "-" else wert
    return wert * 15 if stunden else wert


def lesen(ordner: Path) -> list[dict]:
    rows = []
    for f in ("NGC.csv", "addendum.csv"):
        with open(ordner / f, encoding="utf-8", newline="") as fh:
            rows.extend(csv.DictReader(fh, delimiter=";"))
    return rows


def _f(s: str) -> float | None:
    s = s.strip()
    return float(s) if s else None


def ongc_mag(r: dict) -> tuple[float | None, str]:
    """OpenNGC-Helligkeit mit Plausibilitätsregel (Galaxien: V nur bei 0,3 ≤ B−V ≤ 1,3, sonst B−0,85)."""
    v, b = _f(r["V-Mag"]), _f(r["B-Mag"])
    if r["Type"] in GALAXIE:
        if v is not None and (b is None or 0.3 <= b - v <= 1.3):
            return v, "O"
        if b is not None:
            return round(b - 0.85, 2), "B"
        return None, ""
    if v is not None:
        return v, "O"
    return (b, "O") if b is not None else (None, "")


def _groesse(s: str) -> float | None:
    """„420″ × 290″“, „7′ × 5′“, „1.5°“ → größte Achse in Bogenminuten."""
    werte = []
    for zahl, einheit in re.findall(r"([\d.]+)\s*(′|″|°)", s):
        z = float(zahl)
        werte.append(z / 60 if einheit == "″" else z * 60 if einheit == "°" else z)
    return max(werte) if werte else None


def _zeilen(blk: str) -> list[str]:
    return [z[1:].strip() for z in blk.split("\n")[1:] if z.startswith("|") and not z.startswith(("|}", "|-"))]


def _sex(s: str, vorzeichen: bool) -> float | None:
    """{{RA|17|40.1}} / {{DEC|-32|13}} / {{RA|05|34|31.9}} → Grad-/Stundenzahl (Sekunden und Dezimalminuten optional)."""
    m = re.search(r"\|([+\-−]?)(\d+)\|([\d.]+)(?:\|([\d.]+))?\}\}", s)
    if not m:
        return None
    wert = int(m.group(2)) + float(m.group(3)) / 60 + (float(m.group(4)) / 3600 if m.group(4) else 0)
    return -wert if m.group(1) in ("-", "−") else wert


def wiki_messier(text: str) -> dict[int, dict]:
    out = {}
    for blk in re.split(r"\n\|-[^\n]*\n", text):
        m = re.match(r'!\s*scope="row"\s*\|\s*\[\[[^\]]*?\|?M(\d+)\]\]', blk.strip())
        if not m:
            continue
        mag, size, ra, dec = _zeilen(blk)[-4:]
        mm = re.match(r"^([\d.]+)", mag)
        h, d = _sex(ra, False), _sex(dec, True)
        out[int(m.group(1))] = {"mag": float(mm.group(1)) if mm else None, "size": _groesse(size),
                                "ra": h * 15 if h is not None else None, "dec": d}
    return out


def wiki_caldwell(text: str) -> dict[int, float | None]:
    out = {}
    for blk in re.split(r"\n\|-[^\n]*\n", text):
        m = re.match(r"\|\s*\{\{hs\|\d+\}\}C(\d+)", blk.strip())
        if not m:
            continue
        z = _zeilen(blk)
        mm = re.match(r"^[~≈]?\s*([\d.]+)", z[-1]) if z else None
        out[int(m.group(1))] = float(mm.group(1)) if mm else None
    return out


# ---- Wikipedia-Einzelartikel der Caldwell-Objekte (Infobox: size_v = „Apparent dimensions“, app_mag_v) ----
ARTIKEL_TITEL = {9: "Cave Nebula", 14: "Double Cluster", 33: "", 34: "",  # Schleiernebel-Artikel beschreibt nur den ganzen Komplex (3°) → Größe aus OpenNGC bzw. Handwert
                 37: "NGC 6885", 41: "Hyades (star cluster)",
                 49: "Rosette Nebula", 80: "Omega Centauri", 92: "Carina Nebula", 94: "Jewel Box (star cluster)", 99: "Coalsack Nebula",
                 103: "Tarantula Nebula", 106: "47 Tucanae"}
ARTIKEL_DATEI = "Caldwell_artikel.json"
UNIT = r"(′|'|″|\"|°|arcmin\w*|arcsec\w*|arcminute|arcsecond|degrees?|prime|pprime)"


def artikel_titel(n: int, name: str) -> str:
    if n in ARTIKEL_TITEL:
        return ARTIKEL_TITEL[n]
    m = re.match(r"^(NGC|IC)0*(\d+)$", name)
    return f"{m.group(1)} {m.group(2)}" if m else name


def artikel_holen(zeilen: list[dict], ordner: Path) -> None:
    """Lädt die Wikipedia-Artikel der Caldwell-Objekte (nur beim Entwickeln) und legt Revision, size_v und app_mag_v als JSON ab."""
    import json
    import urllib.parse
    import urllib.request
    titel = {z["num"]: artikel_titel(z["num"], z["openngc"]) for z in zeilen if z["cat"] == "C"}
    titel = {n: t for n, t in titel.items() if t}
    items = sorted(titel.items())
    out = {}
    for i in range(0, len(items), 25):
        chunk = items[i:i + 25]
        q = urllib.parse.urlencode({"action": "query", "redirects": 1, "prop": "revisions", "titles": "|".join(t for _, t in chunk),
                                    "rvprop": "ids|content", "rvslots": "main", "format": "json", "formatversion": 2})
        req = urllib.request.Request("https://en.wikipedia.org/w/api.php?" + q, headers={"User-Agent": "AstroFlow-dev/1.0 (Katalogdaten)"})
        d = json.load(urllib.request.urlopen(req, timeout=60))
        redir = {x["from"]: x["to"] for x in d["query"].get("redirects", [])}
        norm = {x["from"]: x["to"] for x in d["query"].get("normalized", [])}
        pages = {p["title"]: p for p in d["query"]["pages"]}
        for n, t in chunk:
            tt = redir.get(norm.get(t, t), norm.get(t, t))
            p = pages.get(tt)
            if not p or "revisions" not in p:
                out[n] = {"title": t, "revid": None, "size_v": "", "app_mag_v": ""}
                continue
            c = p["revisions"][0]["slots"]["main"]["content"]

            def feld(k: str) -> str:
                return next((re.sub(r"\s+", " ", l.split("=", 1)[1].strip()) for l in c.split("\n") if re.match(rf"\s*\|\s*{k}\s*=", l)), "")
            out[n] = {"title": tt, "revid": p["revisions"][0]["revid"], "size_v": feld("size_v"), "app_mag_v": feld("app_mag_v") or feld("appmag_v")}
    (ordner / ARTIKEL_DATEI).write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{len(out)} Artikel → {ordner / ARTIKEL_DATEI}")


def artikel_groesse(s: str) -> float | None:
    """Infobox-Feld size_v → größte Achse in Bogenminuten (Vorlagen {{prime}}, {{Val|…}}, „36′.3“, „1.3 °“, „arcmin“ usw.)."""
    s = s.split("<ref")[0].split("<br")[0]                              # Quellenangaben (teils auf mehreren Zeilen) abschneiden
    s = re.sub(r"\{\{(?:sfn|r)\|[^}]*\}\}", "", s)
    s = re.sub(r"\[\[[^\]|]*\|([^\]]*)\]\]", r"\1", s)
    s = re.sub(r"\[\[|\]\]", "", s)
    s = re.sub(r"\{\{[Pp]rime\}\}", "′", s)
    s = re.sub(r"\{\{[Pp]prime\}\}", "″", s)

    def val(m: re.Match) -> str:
        teile = m.group(1).split("|")
        einheit = next((t.split("=", 1)[1] for t in teile if t.startswith(("u=", "ul="))), "")
        e = "″" if "sec" in einheit else "°" if "deg" in einheit else "′"
        return " × ".join(f"{t}{e}" for t in teile if re.match(r"^[\d.]+$", t))
    s = re.sub(r"\{\{[Vv]al\|([^}]*)\}\}", val, s)
    s = re.sub(r"\{\{nowrap\|([^}]*)\}\}", r"\1", s)
    s = re.sub(r"(\d+)′\.(\d+)", r"\1.\2′", s)                        # „36′.3“ = 36,3′
    werte = []
    for m in re.finditer(rf"([\d.]+)\s*{UNIT}?", s):
        zahl, einheit = m.group(1), m.group(2)
        if not einheit:
            rest = re.search(UNIT, s[m.end():])                        # „120 × 100 arcmin“: Einheit steht hinter der Zahlenfolge
            einheit = rest.group(1) if rest else ""
        try:
            z = float(zahl.strip("."))
        except ValueError:
            continue
        if einheit.startswith(("″", '"', "arcsec", "pprime")):
            z /= 60
        elif einheit.startswith(("°", "deg")):
            z *= 60
        elif not einheit:
            continue
        werte.append(z)
    return max(werte) if werte else None


def auszug(rows: list[dict], wm: dict, wc: dict, log: list[str], artikel: dict | None = None) -> list[dict]:
    artikel = artikel or {}
    treffer = []                                                      # (cat, num, OpenNGC-Zeile)
    seen = set()
    for r in rows:
        if r["M"].strip() and r["Type"] != "Dup":
            treffer.append(("M", int(r["M"]), r))
            seen.add(int(r["M"]))
    if 102 not in seen:                                               # OpenNGC: M102 = Dublette von M101 → NGC 5866
        treffer.append(("M", 102, next(x for x in rows if x["Name"] == "NGC5866")))
    cs = {}
    for r in rows:
        for s in (r["Identifiers"], r["Name"]):
            for m in re.finditer(r"\bC ?(\d{3})\b", s):
                cs.setdefault(int(m.group(1)), r)
    treffer.extend(("C", n, cs[n]) for n in sorted(cs))
    out = []
    for cat, n, r in sorted(treffer, key=lambda x: (x[0] != "M", x[1])):
        lab = f"{cat}{n} {r['Name']}"
        ra, dec = _hms(r["RA"], True), _hms(r["Dec"], False)
        typ = r["Type"]
        w = wm.get(n, {}) if cat == "M" else {"mag": wc.get(n)}
        art = artikel.get(n, {}) if cat == "C" else {}
        om, oq = ongc_mag(r)
        if w.get("mag") is not None:                                  # Helligkeit
            mag, msrc = w["mag"], "W"
            if om is not None and abs(om - mag) > TOL_MAG:
                log.append(f"  {lab}: Helligkeit Wikipedia {mag} ↔ OpenNGC {om} ({oq}) → Wikipedia" + ("  [UNSICHER: > 2 mag]" if abs(om - mag) > 2 else ""))
        elif cat == "C" and n in VARIABEL and (am := re.match(r"^[~+]?([\d.]+)", art.get("app_mag_v", ""))):
            mag, msrc = float(am.group(1)), "W"
            log.append(f"  {lab}: veränderlich – Helligkeit aus Wikipedia-Artikel {mag:g} (OpenNGC {om})")
        elif om is not None:
            mag, msrc = om, oq
            log.append(f"  {lab}: Helligkeit nur OpenNGC {om} ({oq})")
        else:
            mag, msrc = None, ""
            log.append(f"  {lab}: keine Helligkeit")
        osz = _f(r["MajAx"])                                          # Größe
        wsz = w.get("size") if cat == "M" else None
        if cat == "M":
            first, second = ((osz, "O"), (wsz, "W")) if typ in GALAXIE else ((wsz, "W"), (osz, "O"))
            size, ssrc = next(((v, s) for v, s in (first, second) if v is not None), (None, ""))
            if typ in GALAXIE and osz is not None and wsz is not None and wsz > osz:       # D25 bei Edge-on-Galaxien teils zu klein (M108)
                size, ssrc = wsz, "W"
            if osz is not None and wsz is not None and abs(osz - wsz) / max(osz, wsz) > TOL_SIZE:
                log.append(f"  {lab}: Größe OpenNGC {osz:g}′ ↔ Wikipedia {wsz:g}′ → {ssrc} {size:g}′")
            if w.get("ra") is not None:
                dd = math.hypot((ra - w["ra"]) * math.cos(math.radians(dec)), dec - w["dec"]) * 60
                if dd > TOL_POS:
                    log.append(f"  {lab}: Position OpenNGC ↔ Wikipedia Δ{dd:.1f}′ → OpenNGC")
        else:
            asz = artikel_groesse(art.get("size_v", "")) if art.get("size_v") else None
            size, ssrc = (osz, "O") if osz is not None else (None, "")
            if typ in GALAXIE:
                pass                                                  # D25 aus OpenNGC
            elif typ in CLUSTER or typ == "PN":                       # OpenNGC-MajAx ist Kernmaß → größerer Wert, Artikel bevorzugt
                if asz is not None and (osz is None or asz >= osz):
                    size, ssrc = asz, "W"
            elif asz is not None and (cat, n) not in HAND_SIZE:      # Nebel/SNR/Reflexion/Dunkelwolke: Artikel vor OpenNGC
                size, ssrc = asz, "W"
            if asz is not None and osz is not None and abs(asz - osz) / max(asz, osz) > TOL_SIZE:
                log.append(f"  {lab}: Größe OpenNGC {osz:g}′ ↔ Wikipedia-Artikel {asz:g}′ → {ssrc} {size:g}′")
            if (cat, n) in HAND_SIZE and (asz is None or n in (34,)):
                size, ssrc = HAND_SIZE[(cat, n)], "H"
        if size is None and (cat, n) in HAND_SIZE:
            size, ssrc = HAND_SIZE[(cat, n)], "H"
        if size is None:
            log.append(f"  {lab}: keine Größe")
        out.append({"cat": cat, "num": n, "openngc": r["Name"], "ra_deg": f"{ra:.4f}", "dec_deg": f"{dec:.4f}", "typ": typ,
                    "mag": "" if mag is None else f"{mag:g}", "mag_src": msrc,
                    "size_arcmin": "" if size is None else f"{round(size, 2):g}", "size_src": ssrc})
    return out


def schreiben(zeilen: list[dict], pfad: Path = OUT) -> None:
    pfad.parent.mkdir(parents=True, exist_ok=True)
    with open(pfad, "w", encoding="utf-8", newline="") as fh:
        fh.write(KOPF)
        w = csv.DictWriter(fh, SPALTEN, lineterminator="\n")
        w.writeheader()
        w.writerows(zeilen)


def vergleich(zeilen: list[dict], alt_py: Path) -> None:
    ns: dict = {}
    src = alt_py.read_text(encoding="utf-8").split("# Zweitnamen")[0].replace("from . import objects", "objects = None")
    exec(compile(src, str(alt_py), "exec"), ns)
    neu = {(z["cat"], int(z["num"])): z for z in zeilen}
    print("Abweichungen alt (Handtabelle) → neu (Helligkeit > 0,5 mag, Position > 5′, Größe > 30 %):")
    for cat, tab in (("M", ns["MESSIER"]), ("C", ns["CALDWELL"])):
        for n, ngc, name, ra_h, dec, k, mag, size in tab:
            z = neu[(cat, n)]
            teile = []
            nm = float(z["mag"]) if z["mag"] else None
            if mag is None or nm is None:
                if (mag is None) != (nm is None):
                    teile.append(f"Helligkeit {mag} → {nm}")
            elif abs(mag - nm) > 0.5:
                teile.append(f"Helligkeit {mag} → {nm} ({z['mag_src']})")
            d = math.hypot((ra_h * 15 - float(z["ra_deg"])) * math.cos(math.radians(dec)), dec - float(z["dec_deg"])) * 60
            if d > 5:
                teile.append(f"Position Δ{d:.0f}′")
            ns_ = float(z["size_arcmin"]) if z["size_arcmin"] else None
            if ns_ is None or abs(ns_ - size) / max(size, 0.1) > 0.3:
                teile.append(f"Größe {size}′ → {ns_}′ ({z['size_src']})")
            if teile:
                print(f"  {cat}{n} {ngc or name}: " + "; ".join(teile))


def main(argv: list[str]) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    if len(argv) < 2:
        print(__doc__)
        return 2
    ordner = Path(argv[1])
    wm = wiki_messier((ordner / "Messier_object.wiki").read_text(encoding="utf-8"))
    wc = wiki_caldwell((ordner / "Caldwell_catalogue.wiki").read_text(encoding="utf-8"))
    log: list[str] = []
    rows = lesen(ordner)
    if "--artikel-holen" in argv:
        artikel_holen(auszug(rows, wm, wc, []), ordner)
    import json
    if not (ordner / ARTIKEL_DATEI).is_file():
        print(f"{ARTIKEL_DATEI} fehlt im Ordner. Einmalig mit Netzzugriff erzeugen: python tools/openngc_extract.py {ordner} --artikel-holen")
        return 1
    artikel = {int(k): v for k, v in json.loads((ordner / ARTIKEL_DATEI).read_text(encoding="utf-8")).items()}
    zeilen = auszug(rows, wm, wc, log, artikel)
    schreiben(zeilen)
    with open(OUT.parent / "quellen_artikel.csv", "w", encoding="utf-8", newline="") as fh:       # Revisionen der benutzten Einzelartikel
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["caldwell", "wikipedia_artikel_en", "revision"])
        w.writerows([n, a["title"], a["revid"]] for n, a in sorted(artikel.items()) if a.get("revid"))
    m = sum(z["cat"] == "M" for z in zeilen)
    print(f"{len(zeilen)} Zeilen ({m} Messier, {len(zeilen) - m} Caldwell) → {OUT}")
    print(f"Wikipedia: {len(wm)} Messier, {len(wc)} Caldwell gelesen")
    print(f"Abweichungen/Entscheidungen zwischen den Quellen (Helligkeit > {TOL_MAG} mag, Größe > {TOL_SIZE:.0%}, Position > {TOL_POS}′):")
    print("\n".join(log))
    if "--alt" in argv:
        vergleich(zeilen, Path(argv[argv.index("--alt") + 1]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
