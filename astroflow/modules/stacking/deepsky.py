"""Zielkatalog für „Heute Nacht“: Messier (110) + Caldwell (109) mit Daten aus OpenNGC und Wikipedia (katalogdaten/messier_caldwell.csv), dazu ASTAPs deep_sky.csv (falls vorhanden).

Warum zwei Quellen: `objects.CATALOG` kennt nur ~60 Objekte (Profile, Aliasse). Für Empfehlungen braucht es eine
vollständige Liste mit Helligkeit. ASTAPs deep_sky.csv hat **keine** Helligkeit (nur Ort, Typ, Größe) – deshalb sind Messier und
Caldwell mit Helligkeit aus OpenNGC (CC-BY-SA 4.0, siehe katalogdaten/LICENSE-OpenNGC und NOTICE) eingebaut (Koordinaten werden bei vorhandenem ASTAP von dort übernommen), und weitere ASTAP-Objekte
kommen nur dazu, wenn sie nach Name/Größe erkennbar auffällig sind (Näherung, im Ergebnis als „Helligkeit unbekannt“ markiert).

Doppelte Bezeichnungen (M31 = NGC 224 = C…) werden über `Target.keys` zusammengeführt; `keys_for(name)` liefert alle Schlüssel
eines beliebigen Namens, damit eigene Objekte zuverlässig ausgeschlossen werden.
"""
from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from pathlib import Path

from . import devices, objects

# Kurzcodes der Objektart → objects.KIND_LABEL
_K = {"g": "galaxy", "e": "emission", "r": "reflection", "p": "planetary", "s": "snr", "k": "globular", "o": "open_cluster", "x": "other"}

# Eigene Zuordnung (AstroFlow-Code, nicht CC-BY-SA): Nummer → (NGC/IC-Kennung für Anzeige/Schlüssel, deutscher Name, Objektart-Kürzel).
# Positionen, Helligkeit und Größe stehen NICHT hier, sondern in katalogdaten/messier_caldwell.csv (OpenNGC + Wikipedia, CC-BY-SA 4.0).
# Caldwell: C50 (NGC 2244) steckt in C49, C61 (NGC 4039) in C60 – beide stehen in der Datendatei, sind aber keine eigenen Ziele.
MESSIER = [
    (1, "NGC1952", "Krebsnebel", "s"), (2, "NGC7089", "", "k"), (3, "NGC5272", "", "k"), (4, "NGC6121", "", "k"),
    (5, "NGC5904", "", "k"), (6, "NGC6405", "Schmetterlingshaufen", "o"), (7, "NGC6475", "Ptolemäushaufen", "o"),
    (8, "NGC6523", "Lagunennebel", "e"), (9, "NGC6333", "", "k"), (10, "NGC6254", "", "k"),
    (11, "NGC6705", "Wildentenhaufen", "o"), (12, "NGC6218", "", "k"), (13, "NGC6205", "Herkuleshaufen", "k"),
    (14, "NGC6402", "", "k"), (15, "NGC7078", "", "k"), (16, "NGC6611", "Adlernebel", "e"), (17, "NGC6618", "Omeganebel", "e"),
    (18, "NGC6613", "", "o"), (19, "NGC6273", "", "k"), (20, "NGC6514", "Trifidnebel", "e"), (21, "NGC6531", "", "o"),
    (22, "NGC6656", "", "k"), (23, "NGC6494", "", "o"), (24, "", "Kleine Sagittarius-Sternwolke", "x"), (25, "IC4725", "", "o"),
    (26, "NGC6694", "", "o"), (27, "NGC6853", "Hantelnebel", "p"), (28, "NGC6626", "", "k"), (29, "NGC6913", "", "o"),
    (30, "NGC7099", "", "k"), (31, "NGC224", "Andromedagalaxie", "g"), (32, "NGC221", "", "g"),
    (33, "NGC598", "Dreiecksgalaxie", "g"), (34, "NGC1039", "", "o"), (35, "NGC2168", "", "o"), (36, "NGC1960", "", "o"),
    (37, "NGC2099", "", "o"), (38, "NGC1912", "", "o"), (39, "NGC7092", "", "o"), (40, "", "Winnecke 4", "x"),
    (41, "NGC2287", "", "o"), (42, "NGC1976", "Orionnebel", "e"), (43, "NGC1982", "", "e"), (44, "NGC2632", "Krippe", "o"),
    (45, "", "Plejaden", "r"), (46, "NGC2437", "", "o"), (47, "NGC2422", "", "o"), (48, "NGC2548", "", "o"),
    (49, "NGC4472", "", "g"), (50, "NGC2323", "", "o"), (51, "NGC5194", "Whirlpoolgalaxie", "g"), (52, "NGC7654", "", "o"),
    (53, "NGC5024", "", "k"), (54, "NGC6715", "", "k"), (55, "NGC6809", "", "k"), (56, "NGC6779", "", "k"),
    (57, "NGC6720", "Ringnebel", "p"), (58, "NGC4579", "", "g"), (59, "NGC4621", "", "g"), (60, "NGC4649", "", "g"),
    (61, "NGC4303", "", "g"), (62, "NGC6266", "", "k"), (63, "NGC5055", "Sonnenblumengalaxie", "g"),
    (64, "NGC4826", "Schwarzauggalaxie", "g"), (65, "NGC3623", "", "g"), (66, "NGC3627", "", "g"), (67, "NGC2682", "", "o"),
    (68, "NGC4590", "", "k"), (69, "NGC6637", "", "k"), (70, "NGC6681", "", "k"), (71, "NGC6838", "", "k"),
    (72, "NGC6981", "", "k"), (73, "NGC6994", "", "x"), (74, "NGC628", "", "g"), (75, "NGC6864", "", "k"),
    (76, "NGC650", "Kleiner Hantelnebel", "p"), (77, "NGC1068", "", "g"), (78, "NGC2068", "", "r"), (79, "NGC1904", "", "k"),
    (80, "NGC6093", "", "k"), (81, "NGC3031", "Bodes Galaxie", "g"), (82, "NGC3034", "Zigarrengalaxie", "g"),
    (83, "NGC5236", "Südliche Feuerradgalaxie", "g"), (84, "NGC4374", "", "g"), (85, "NGC4382", "", "g"),
    (86, "NGC4406", "", "g"), (87, "NGC4486", "", "g"), (88, "NGC4501", "", "g"), (89, "NGC4552", "", "g"),
    (90, "NGC4569", "", "g"), (91, "NGC4548", "", "g"), (92, "NGC6341", "", "k"), (93, "NGC2447", "", "o"),
    (94, "NGC4736", "", "g"), (95, "NGC3351", "", "g"), (96, "NGC3368", "", "g"), (97, "NGC3587", "Eulennebel", "p"),
    (98, "NGC4192", "", "g"), (99, "NGC4254", "", "g"), (100, "NGC4321", "", "g"), (101, "NGC5457", "Feuerradgalaxie", "g"),
    (102, "NGC5866", "Spindelgalaxie", "g"), (103, "NGC581", "", "o"), (104, "NGC4594", "Sombrerogalaxie", "g"),
    (105, "NGC3379", "", "g"), (106, "NGC4258", "", "g"), (107, "NGC6171", "", "k"), (108, "NGC3556", "", "g"),
    (109, "NGC3992", "", "g"), (110, "NGC205", "", "g"),
]

CALDWELL = [
    (1, "NGC188", "", "o"), (2, "NGC40", "Bow-Tie-Nebel", "p"), (3, "NGC4236", "", "g"), (4, "NGC7023", "Irisnebel", "r"),
    (5, "IC342", "Versteckte Galaxie", "g"), (6, "NGC6543", "Katzenaugennebel", "p"), (7, "NGC2403", "", "g"),
    (8, "NGC559", "", "o"), (9, "SH2155", "Höhlennebel", "e"), (10, "NGC663", "", "o"), (11, "NGC7635", "Blasennebel", "e"),
    (12, "NGC6946", "Feuerwerksgalaxie", "g"), (13, "NGC457", "Eulenhaufen", "o"),
    (14, "NGC869", "Doppelhaufen h und χ Persei", "o"), (15, "NGC6826", "Blinkender Planetarischer", "p"),
    (16, "NGC7243", "", "o"), (17, "NGC147", "", "g"), (18, "NGC185", "", "g"), (19, "IC5146", "Kokonnebel", "e"),
    (20, "NGC7000", "Nordamerikanebel", "e"), (21, "NGC4449", "", "g"), (22, "NGC7662", "Blauer Schneeball", "p"),
    (23, "NGC891", "", "g"), (24, "NGC1275", "", "g"), (25, "NGC2419", "", "k"), (26, "NGC4244", "", "g"),
    (27, "NGC6888", "Crescentnebel", "e"), (28, "NGC752", "", "o"), (29, "NGC5005", "", "g"), (30, "NGC7331", "", "g"),
    (31, "IC405", "Flammender Stern", "e"), (32, "NGC4631", "Walgalaxie", "g"), (33, "NGC6992", "Cirrusnebel (Ost)", "s"),
    (34, "NGC6960", "Cirrusnebel (West)", "s"), (35, "NGC4889", "", "g"), (36, "NGC4559", "", "g"), (37, "NGC6885", "", "o"),
    (38, "NGC4565", "Nadelgalaxie", "g"), (39, "NGC2392", "Eskimonebel", "p"), (40, "NGC3626", "", "g"),
    (41, "MEL25", "Hyaden", "o"), (42, "NGC7006", "", "k"), (43, "NGC7814", "", "g"), (44, "NGC7479", "", "g"),
    (45, "NGC5248", "", "g"), (46, "NGC2261", "Hubbles veränderlicher Nebel", "r"), (47, "NGC6934", "", "k"),
    (48, "NGC2775", "", "g"), (49, "NGC2237", "Rosettennebel", "e"), (51, "IC1613", "", "g"), (52, "NGC4697", "", "g"),
    (53, "NGC3115", "Sextans-Spindel", "g"), (54, "NGC2506", "", "o"), (55, "NGC7009", "Saturnnebel", "p"),
    (56, "NGC246", "Totenkopfnebel", "p"), (57, "NGC6822", "Barnards Galaxie", "g"), (58, "NGC2360", "", "o"),
    (59, "NGC3242", "Geist des Jupiter", "p"), (60, "NGC4038", "Antennengalaxien", "g"), (62, "NGC247", "", "g"),
    (63, "NGC7293", "Helixnebel", "p"), (64, "NGC2362", "", "o"), (65, "NGC253", "Sculptorgalaxie", "g"),
    (66, "NGC5694", "", "k"), (67, "NGC1097", "", "g"), (68, "NGC6729", "", "r"), (69, "NGC6302", "Käfernebel", "p"),
    (70, "NGC300", "", "g"), (71, "NGC2477", "", "o"), (72, "NGC55", "", "g"), (73, "NGC1851", "", "k"),
    (74, "NGC3132", "Achtfach-Nebel", "p"), (75, "NGC6124", "", "o"), (76, "NGC6231", "", "o"),
    (77, "NGC5128", "Centaurus A", "g"), (78, "NGC6541", "", "k"), (79, "NGC3201", "", "k"),
    (80, "NGC5139", "Omega Centauri", "k"), (81, "NGC6352", "", "k"), (82, "NGC6193", "", "o"), (83, "NGC4945", "", "g"),
    (84, "NGC5286", "", "k"), (85, "IC2391", "", "o"), (86, "NGC6397", "", "k"), (87, "NGC1261", "", "k"),
    (88, "NGC5823", "", "o"), (89, "NGC6087", "", "o"), (90, "NGC2867", "", "p"), (91, "NGC3532", "", "o"),
    (92, "NGC3372", "Carinanebel", "e"), (93, "NGC6752", "", "k"), (94, "NGC4755", "Juwelenkästchen", "o"),
    (95, "NGC6025", "", "o"), (96, "NGC2516", "", "o"), (97, "NGC3766", "", "o"), (98, "NGC4609", "", "o"),
    (99, "", "Kohlensack", "x"), (100, "IC2944", "", "e"), (101, "NGC6744", "", "g"), (102, "IC2602", "", "o"),
    (103, "NGC2070", "Tarantelnebel", "e"), (104, "NGC362", "", "k"), (105, "NGC4833", "", "k"),
    (106, "NGC104", "47 Tucanae", "k"), (107, "NGC6101", "", "k"), (108, "NGC4372", "", "k"), (109, "NGC3195", "", "p"),
]

MERGED = {("C", 50), ("C", 61)}
DATA_FILE = Path(__file__).parent / "katalogdaten" / "messier_caldwell.csv"     # erzeugt mit tools/openngc_extract.py
# Zweitnamen, die zu einem Katalogeintrag gehören (zusammenführen statt doppelt vorschlagen)
EXTRA_KEYS = {("C", 49): ("NGC2244", "C50", "ROSETTE"), ("C", 60): ("NGC4039", "C61"), ("C", 14): ("NGC884",),
              ("C", 33): ("NGC6995", "IC1340"), ("C", 20): ("IC5070",), ("C", 4): (), ("M", 45): ("PLEIADES", "PLEJADEN"),
              ("M", 42): ("ORION",), ("M", 31): ("ANDROMEDA",), ("C", 41): ("HYADES", "HYADEN")}

MAG_LIMITS = devices.mag_limits()      # Grenzgröße je Gerät aus devices.json
MAG_DEFAULT = 11.0
MIN_SIZE = 2.0            # Bogenminuten: darunter sind Objekte auf Smart-Teleskopen nur Pünktchen
MIN_FRACTION = 0.03       # oder weniger als 3 % der kurzen Bildfeldseite
MAX_FRACTION = 0.9        # größer als 90 % der kurzen Seite passt mit Rand nicht ins Bild


def mag_limit(device: dict | None) -> float:
    return MAG_LIMITS.get((device or {}).get("id"), MAG_DEFAULT)


@dataclass(frozen=True)
class Target:
    id: str                # eindeutige Kennung, z. B. "M31", "C20", "NGC1234"
    display: str
    ra: float              # Grad
    dec: float
    kind: str
    size: float            # Bogenminuten (größte Ausdehnung)
    mag: float | None      # None = unbekannt (Nebel: Helligkeit nicht aussagekräftig; ASTAP: nicht im Katalog)
    source: str            # messier | caldwell | astap
    keys: frozenset = field(default_factory=frozenset)
    number: int | None = None
    fallback: bool = False  # Größe/Helligkeit von Hand (unverifiziert), weil keine Quelle sie kennt (Spalten size_src/mag_src = H)


def norm(name: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", name.upper())


def _ident(code: str) -> str:
    m = re.match(r"^(NGC|IC|MEL|SH2)(\d+)$", code)
    if not m:
        return code
    return {"NGC": "NGC ", "IC": "IC ", "MEL": "Mel ", "SH2": "Sh2-"}[m.group(1)] + m.group(2)


def _display(prefix: str, n: int, ngc: str, name: str) -> str:
    head = f"M{n}" if prefix == "M" else f"C {n}" + (f" ({_ident(ngc)})" if ngc else "")
    return f"{head} {name}".strip()


_FIXED: list[Target] | None = None
_ASTAP: list[Target] | None = None
_KEYS: dict[str, Target] = {}


def _data() -> dict[tuple[str, int], dict]:
    """Zeilen der Auszugsdatei: („M“|„C“, Nummer) → {ra, dec, mag, size, hand}. None = keine Quelle kennt einen Wert;
    hand = Wert (Helligkeit oder Größe) stammt von Hand und ist unverifiziert (Spalten mag_src/size_src = H)."""
    out = {}
    with open(DATA_FILE, encoding="utf-8", newline="") as fh:
        for r in csv.DictReader(line for line in fh if not line.startswith("#")):
            out[(r["cat"], int(r["num"]))] = {
                "ra": float(r["ra_deg"]), "dec": float(r["dec_deg"]),
                "mag": float(r["mag"]) if r["mag"] else None, "size": float(r["size_arcmin"]) if r["size_arcmin"] else None,
                "hand": "H" in (r["mag_src"], r["size_src"])}
    return out


def fixed() -> list[Target]:
    """Messier + Caldwell. Koordinaten/Größe kommen aus ASTAP, wenn der Eintrag dort über NGC/IC/M steht (genauer als OpenNGC-Auszug)."""
    global _FIXED
    if _FIXED is not None:
        return _FIXED
    ext = {}
    for c in objects.extended_catalog():
        ext.setdefault(c.id, c)
    data = _data()
    out = []
    for prefix, rows in (("M", MESSIER), ("C", CALDWELL)):
        for n, ngc, name, k in rows:
            kind = _K[k]
            if kind == "other" or (prefix, n) in MERGED:
                continue
            d = data[(prefix, n)]
            ra, dec = d["ra"], d["dec"]
            mag, size, fb = d["mag"], d["size"] or 0.0, d["hand"]
            if kind == "emission":                 # Emissionsnebel: integrale Helligkeit sagt über die Sichtbarkeit nichts aus → unbekannt
                mag = None
            e = ext.get(f"M{n}" if prefix == "M" else "") or ext.get(norm(ngc))
            if e and objects.ang_sep(ra, dec, e.ra, e.dec) < 0.5:         # nur übernehmen, wenn es dasselbe Objekt ist
                ra, dec = e.ra, e.dec
                if e.size > 0 and abs(e.size - size) / max(size, 1) < 3:
                    size = max(size, e.size) if prefix == "M" or kind in ("galaxy",) else size
            keys = {norm(f"{prefix}{n}")}
            if prefix == "C":
                keys.add(f"CALDWELL{n}")
            else:
                keys.add(f"MESSIER{n}")
            if ngc:
                keys.add(norm(ngc))
            keys.update(norm(x) for x in EXTRA_KEYS.get((prefix, n), ()))
            tid = f"{prefix}{n}"
            if name:
                keys.add(norm(name))
            out.append(Target(tid, _display(prefix, n, ngc, name), ra, dec, kind, float(size), mag,
                              "messier" if prefix == "M" else "caldwell", frozenset(keys), n, fb))
    _FIXED = out
    for t in out:                                                           # Messier gewinnt bei gleicher NGC-Nummer (C/M gibt es nie doppelt)
        for k in t.keys:
            _KEYS.setdefault(k, t)
    return out


def astap_extras() -> list[Target]:
    """Auffällige weitere Objekte aus ASTAPs deep_sky.csv. Es gibt dort keine Helligkeit; Auswahl über Name und Größe:
    benannte Objekte, größere Galaxien (≥ 8′) und NGC/IC-Sternhaufen 10–45′. Was Messier/Caldwell schon abdeckt, fällt weg."""
    global _ASTAP
    if _ASTAP is not None:
        return _ASTAP
    base = fixed()
    taken = {k for t in base for k in t.keys}
    out = []
    for c in objects.extended_catalog():
        if c.kind == "generic" or c.size < MIN_SIZE or not re.match(r"^(NGC|IC|MEL|CR|TR|STOCK)\d", c.id):
            continue
        if c.id in taken or any(a in taken for a in c.aliases):
            continue
        if any(abs(t.dec - c.dec) < 1 and objects.ang_sep(c.ra, c.dec, t.ra, t.dec) < max(0.1, t.size / 120) for t in base):
            continue
        named = len(c.display.split()) > (2 if re.match(r"^(NGC|IC|M) \d", c.display) else 1)
        if named or (c.kind == "galaxy" and c.size >= 8) or (c.kind in ("open_cluster", "globular") and 10 <= c.size <= 45 and c.id[:2] in ("NG", "IC")):
            out.append(Target(c.id, c.display, c.ra, c.dec, c.kind, c.size, None, "astap", frozenset({c.id, *c.aliases})))
    _ASTAP = out
    return out


def catalog() -> list[Target]:
    return fixed() + astap_extras()


def signature() -> tuple:
    """Ändert sich, wenn die ASTAP-Datei dazukommt/wegfällt (Zwischenspeicher der Planung)."""
    return (len(fixed()), len(astap_extras()))


def reset() -> None:
    """Nach Änderung der ASTAP-Datei bzw. in Tests: Zwischenspeicher leeren."""
    global _FIXED, _ASTAP
    _FIXED = _ASTAP = None
    _KEYS.clear()
    objects._EXT = None
    objects._EXT_ALIAS.clear()


def keys_for(name: str | None) -> set[str]:
    """Alle Schlüssel eines Objektnamens (z. B. „NGC 224“ → {NGC224, M31, MESSIER31, …}) für den Abgleich eigener Objekte."""
    if not name:
        return set()
    fixed()
    found = {norm(name)}
    n = re.sub(r"^MESSIER", "M", norm(name))
    found.add(n)
    tok = re.match(r"\s*([A-Za-z]{1,8})\s*0*(\d+)", name)
    if tok:
        found.add((tok.group(1) + tok.group(2)).upper())
        found.add(re.sub(r"^MESSIER", "M", (tok.group(1) + tok.group(2)).upper()))
        found.add(re.sub(r"^CALDWELL", "C", (tok.group(1) + tok.group(2)).upper()))
    cat = objects.resolve_name(name)
    if cat:
        found |= {norm(cat.id), *(norm(a) for a in cat.aliases)}
    ext = objects.resolve_extended(name)
    if ext:
        found.add(norm(ext.id))
    out = set(found)
    for k in found:
        t = _KEYS.get(k)
        if t:
            out |= t.keys
    return out


def usable(t: Target, device: dict | None, field_fov: list | None) -> bool:
    """Passt das Ziel zu Gerät und Bildfeld (Helligkeit, Größe)?"""
    if t.mag is not None and t.mag > mag_limit(device):
        return False
    if t.size < MIN_SIZE:
        return False
    if field_fov:
        short = min(field_fov) * 60
        if t.size < MIN_FRACTION * short or t.size > MAX_FRACTION * short:
            return False
    elif t.size > 120:
        return False
    return True
