"""Kleiner Objektkatalog + Bildbearbeitungsprofile je Objekttyp."""
from __future__ import annotations

import math
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class CatObject:
    id: str
    display: str
    ra: float      # Grad
    dec: float     # Grad
    kind: str      # galaxy|emission|reflection|planetary|snr|globular|open_cluster
    size: float    # Bogenminuten (grob)
    aliases: tuple = ()


def _o(id_, display, ra_h, dec, kind, size, *aliases):
    return CatObject(id_, display, ra_h * 15.0, dec, kind, size, tuple(a.upper() for a in aliases))


CATALOG: list[CatObject] = [
    _o("M1", "M1 Krebsnebel", 5.575, 22.017, "snr", 6, "NGC1952", "CRAB", "KREBSNEBEL"),
    _o("M2", "M2", 21.558, -0.82, "globular", 16, "NGC7089"),
    _o("M3", "M3", 13.703, 28.38, "globular", 18, "NGC5272"),
    _o("M4", "M4", 16.393, -26.53, "globular", 26, "NGC6121"),
    _o("M5", "M5", 15.31, 2.08, "globular", 23, "NGC5904"),
    _o("M8", "M8 Lagunennebel", 18.063, -24.38, "emission", 90, "NGC6523", "LAGOON"),
    _o("M11", "M11 Wildentenhaufen", 18.851, -6.27, "open_cluster", 14, "NGC6705"),
    _o("M13", "M13 Herkuleshaufen", 16.695, 36.46, "globular", 20, "NGC6205"),
    _o("M15", "M15", 21.5, 12.17, "globular", 18, "NGC7078"),
    _o("M16", "M16 Adlernebel", 18.313, -13.78, "emission", 35, "NGC6611", "IC4703", "EAGLE"),
    _o("M17", "M17 Omeganebel", 18.346, -16.17, "emission", 46, "NGC6618", "OMEGA", "SWAN"),
    _o("M20", "M20 Trifidnebel", 18.043, -23.03, "emission", 28, "NGC6514", "TRIFID"),
    _o("M22", "M22", 18.607, -23.9, "globular", 32, "NGC6656"),
    _o("M27", "M27 Hantelnebel", 19.994, 22.72, "planetary", 8, "NGC6853", "DUMBBELL"),
    _o("M31", "M31 Andromedagalaxie", 0.712, 41.27, "galaxy", 180, "NGC224", "ANDROMEDA"),
    _o("M33", "M33 Dreiecksgalaxie", 1.564, 30.66, "galaxy", 70, "NGC598", "TRIANGULUM"),
    _o("M35", "M35", 6.148, 24.33, "open_cluster", 28, "NGC2168"),
    _o("M42", "M42 Orionnebel", 5.588, -5.39, "emission", 65, "NGC1976", "ORION", "ORIONNEBEL"),
    _o("M43", "M43", 5.592, -5.27, "emission", 20, "NGC1982"),
    _o("M44", "M44 Krippe", 8.667, 19.67, "open_cluster", 95, "NGC2632", "BEEHIVE"),
    _o("M45", "M45 Plejaden", 3.79, 24.12, "reflection", 110, "PLEIADES", "PLEJADEN"),
    _o("M51", "M51 Whirlpoolgalaxie", 13.498, 47.20, "galaxy", 11, "NGC5194", "WHIRLPOOL"),
    _o("M57", "M57 Ringnebel", 18.893, 33.03, "planetary", 3.5, "NGC6720", "RING"),
    _o("M63", "M63 Sonnenblumengalaxie", 13.263, 42.03, "galaxy", 12, "NGC5055", "SUNFLOWER"),
    _o("M64", "M64 Schwarzauggalaxie", 12.945, 21.68, "galaxy", 10, "NGC4826"),
    _o("M65", "M65", 11.315, 13.09, "galaxy", 10, "NGC3623"),
    _o("M66", "M66", 11.337, 12.99, "galaxy", 9, "NGC3627"),
    _o("M74", "M74", 1.611, 15.78, "galaxy", 10, "NGC628"),
    _o("M76", "M76 Kleiner Hantelnebel", 1.703, 51.58, "planetary", 2.7, "NGC650"),
    _o("M78", "M78", 5.78, 0.08, "reflection", 8, "NGC2068"),
    _o("M81", "M81 Bodes Galaxie", 9.926, 69.07, "galaxy", 27, "NGC3031", "BODE"),
    _o("M82", "M82 Zigarrengalaxie", 9.931, 69.68, "galaxy", 11, "NGC3034", "CIGAR"),
    _o("M87", "M87", 12.514, 12.39, "galaxy", 8, "NGC4486", "VIRGO A"),
    _o("M92", "M92", 17.285, 43.14, "globular", 14, "NGC6341"),
    _o("M94", "M94", 12.848, 41.12, "galaxy", 11, "NGC4736"),
    _o("M97", "M97 Eulennebel", 11.246, 55.02, "planetary", 3.4, "NGC3587", "OWL"),
    _o("M101", "M101 Feuerradgalaxie", 14.054, 54.35, "galaxy", 29, "NGC5457", "PINWHEEL"),
    _o("M104", "M104 Sombrerogalaxie", 12.667, -11.62, "galaxy", 9, "NGC4594", "SOMBRERO"),
    _o("M106", "M106", 12.316, 47.30, "galaxy", 18, "NGC4258"),
    _o("M100", "M100", 12.382, 15.82, "galaxy", 7, "NGC4321"),
    _o("NGC7000", "NGC 7000 Nordamerikanebel", 20.98, 44.33, "emission", 120, "NORTH AMERICA", "NORDAMERIKA", "CALDWELL20"),
    _o("IC5070", "IC 5070 Pelikannebel", 20.83, 44.35, "emission", 60, "PELICAN", "PELIKAN"),
    _o("NGC6992", "NGC 6992 Cirrusnebel (Ost)", 20.93, 31.7, "snr", 60, "VEIL EAST", "NGC6995", "IC1340"),
    _o("NGC6960", "NGC 6960 Cirrusnebel (West)", 20.76, 30.7, "snr", 70, "VEIL WEST", "WITCHS BROOM"),
    _o("NGC2244", "NGC 2244 Rosettennebel", 6.53, 4.95, "emission", 80, "NGC2237", "ROSETTE", "ROSETTENNEBEL"),
    _o("IC434", "IC 434 Pferdekopfnebel", 5.683, -2.46, "emission", 60, "HORSEHEAD", "B33", "PFERDEKOPF"),
    _o("NGC2024", "NGC 2024 Flammennebel", 5.68, -1.9, "emission", 30, "FLAME"),
    _o("NGC7293", "NGC 7293 Helixnebel", 22.494, -20.84, "planetary", 16, "HELIX", "CALDWELL63"),
    _o("NGC891", "NGC 891", 2.376, 42.35, "galaxy", 13, "CALDWELL23"),
    _o("NGC253", "NGC 253 Sculptorgalaxie", 0.793, -25.29, "galaxy", 27, "SCULPTOR", "CALDWELL65"),
    _o("NGC4565", "NGC 4565 Nadelgalaxie", 12.605, 25.99, "galaxy", 16, "NEEDLE", "CALDWELL38"),
    _o("IC1396", "IC 1396 Elefantenrüssel", 21.65, 57.5, "emission", 170, "ELEPHANT TRUNK", "ELEFANTENRUESSEL"),
    _o("IC1805", "IC 1805 Herznebel", 2.553, 61.45, "emission", 60, "HEART", "HERZNEBEL"),
    _o("IC1848", "IC 1848 Seelennebel", 2.85, 60.4, "emission", 60, "SOUL", "SEELENNEBEL"),
    _o("NGC281", "NGC 281 Pacmannebel", 0.866, 56.6, "emission", 35, "PACMAN", "IC11"),
    _o("IC405", "IC 405 Flammender Stern", 5.27, 34.27, "emission", 37, "FLAMING STAR"),
    _o("NGC1499", "NGC 1499 Kalifornienebel", 4.05, 36.4, "emission", 145, "CALIFORNIA"),
    _o("NGC6888", "NGC 6888 Crescentnebel", 20.2, 38.35, "emission", 20, "CRESCENT", "CALDWELL27"),
    _o("NGC7635", "NGC 7635 Blasennebel", 23.34, 61.2, "emission", 15, "BUBBLE"),
]

_BY_ALIAS: dict[str, CatObject] = {}
for _c in CATALOG:
    for _k in (_c.id.upper(), *_c.aliases):
        _BY_ALIAS[re.sub(r"[^A-Z0-9]", "", _k)] = _c


def normalize_name(name: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", name.upper())


def resolve_name(name: str | None) -> CatObject | None:
    if not name:
        return None
    n = normalize_name(name)
    n = re.sub(r"^(MESSIER)", "M", n)
    if n in _BY_ALIAS:
        return _BY_ALIAS[n]
    # "M 31 - Andromeda" etc.: erstes Token versuchen
    tok = re.match(r"\s*([A-Za-z]{1,4})\s*0*(\d+)", name)
    if tok:
        k = (tok.group(1) + tok.group(2)).upper()
        if k in _BY_ALIAS:
            return _BY_ALIAS[k]
    return None


def ang_sep(ra1, dec1, ra2, dec2) -> float:
    r1, d1, r2, d2 = map(math.radians, (ra1, dec1, ra2, dec2))
    a = math.sin((d2 - d1) / 2) ** 2 + math.cos(d1) * math.cos(d2) * math.sin((r2 - r1) / 2) ** 2
    return math.degrees(2 * math.asin(min(1.0, math.sqrt(a))))


def nearest(ra: float, dec: float, slack_deg: float = 0.3) -> CatObject | None:
    best, best_sep = None, 1e9
    for c in CATALOG:
        sep = ang_sep(ra, dec, c.ra, c.dec)
        lim = max(slack_deg, c.size / 60.0 * 0.5)
        if sep < lim and sep < best_sep:
            best, best_sep = c, sep
    return best


# --------------------------------------------------------------------------- #
# Erweiterter Katalog: ASTAPs deep_sky.csv (~30 000 Objekte), falls installiert
# --------------------------------------------------------------------------- #
_EXT: list[CatObject] | None = None
_EXT_ALIAS: dict[str, CatObject] = {}
_EXT_KIND = [(r"^(PK|PN|PNG)", "planetary"), (r"^(Sh2|LBN|RCW|Ced)", "emission"), (r"^(vdB|LDN|B\d)", "reflection"),
             (r"^(PGC|UGC|MCG|ESO|LEDA|HCG|Arp)", "galaxy"), (r"^(Mel|Cr|Tr|Stock|King|Berk)", "open_cluster"), (r"^(GC|Pal)", "globular")]


def _ext_paths() -> list[str]:
    import os
    out = [os.environ.get("ASTROFLOW_DEEPSKY", "")]
    if os.environ.get("LOCALAPPDATA"):
        out.append(os.path.join(os.environ["LOCALAPPDATA"], "astap", "deep_sky.csv"))
    return out + [r"C:\Program Files\astap\deep_sky.csv", "/opt/astap/deep_sky.csv", "/usr/share/astap/deep_sky.csv"]


def _pretty(name: str) -> str:
    name = re.sub(r"^(NGC|IC|M|Sh2-|vdB|PGC|UGC|Abell|LBN|LDN)(\d)", r"\1 \2", name)
    return name.replace("_", " ").replace("Sh2- ", "Sh2-")


def extended_catalog() -> list[CatObject]:
    global _EXT
    if _EXT is not None:
        return _EXT
    import os
    _EXT = []
    path = next((p for p in _ext_paths() if p and os.path.isfile(p)), None)
    if not path:
        return _EXT
    with open(path, encoding="utf-8-sig", errors="replace") as f:
        for line in list(f)[2:]:
            p = line.rstrip("\n").split(",")
            if len(p) < 4 or not p[3].strip():
                continue                                          # Sterne/Pole ohne Ausdehnung
            try:
                ra, dec, size = int(p[0]) / 864000 * 360, int(p[1]) / 3600, int(p[3]) / 10
            except ValueError:
                continue
            names = [n for n in p[2].split("/") if n]
            common = next((n for n in names[1:] if "_" in n and not re.match(r"^(PK|PNG)", n)), None)
            kind = next((k for pat, k in _EXT_KIND if any(re.match(pat, n) for n in names)), "generic")
            if kind == "generic" and re.match(r"^(M|NGC|IC)\d", names[0]) and any(n.startswith("PGC") for n in names):
                kind = "galaxy"
            disp = _pretty(names[0]) + (f" {common.replace('_', ' ')}" if common else "")
            obj = CatObject(normalize_name(names[0]), disp, ra, dec, kind, size)
            _EXT.append(obj)
            for n in names:
                if not re.match(r"^(PK|PNG)", n):
                    _EXT_ALIAS.setdefault(normalize_name(n), obj)
    return _EXT


def resolve_extended(name: str | None) -> CatObject | None:
    """Objekt im großen Katalog (ASTAP) über einen seiner Namen – z. B. Ordnername „NGC4490“, den der kleine Katalog nicht kennt."""
    if not name:
        return None
    extended_catalog()
    n = re.sub(r"^MESSIER", "M", normalize_name(name))
    if n in _EXT_ALIAS:
        return _EXT_ALIAS[n]
    tok = re.match(r"\s*([A-Za-z]{1,4})\s*0*(\d+)", name)
    return _EXT_ALIAS.get((tok.group(1) + tok.group(2)).upper()) if tok else None


def nearest_extended(ra: float, dec: float, radius_deg: float = 0.3, min_size: float = 1.0) -> CatObject | None:
    """Auffälligstes Objekt nahe der Bildmitte: groß und zentral gewinnt."""
    best, best_score = None, 0.0
    for c in extended_catalog():
        if c.size < min_size or abs(c.dec - dec) > radius_deg:
            continue
        sep = ang_sep(ra, dec, c.ra, c.dec)
        if sep > radius_deg:
            continue
        score = c.size / (1 + (sep / (0.25 * radius_deg)) ** 2)
        if score > best_score:
            best, best_score = c, score
    return best


# --------------------------------------------------------------------------- #
# Bildbearbeitungsprofile
# --------------------------------------------------------------------------- #
# gradient_degree : Polynomgrad der Hintergrundextraktion
# target_bg       : Helligkeit des Himmelshintergrunds (0..1) nach dem Stretch – bestimmt, wie hell schwaches Signal wird
# bg_dark         : Endgültige Hintergrundhelligkeit über den Schwarzpunkt (Stellina ~0,03; None = aus)
# star_separation : off (Standard – Nutzer fand den Look ohne Trennung besser, NGC 7000) | auto (StarNet, falls
#                   installiert, sonst eigene Trennung) | starnet | intern; je Objekt unter Optionen wählbar
# star_brightness : Helligkeit der wieder eingesetzten Sterne (1 = wie bei normaler Streckung)
# asinh           : Stärke der asinh-Kompression (größer = mehr Highlight-Schutz)
# denoise         : Rauschreduzierung 0..1
# chroma_denoise  : Farbrauschen-Glättung (Sigma in Px bei 1500 Px Bildbreite)
# saturation      : Farbsättigung
# contrast        : lokaler Kontrast 0..1
# sharpen         : Schärfen 0..1
# deconv          : Entfaltungs-Iterationen (Richardson-Lucy)
# star_reduction  : Sterne verkleinern 0..1
# scnr            : Grünstich-Entfernung 0..1
# white_pct       : Weißpunkt als Perzentil (Standard 99,9; die Kontrolle hebt ihn bei ausgebranntem Kern)
# ai_denoise : Stärke des GraXpert-Entrauschens 0..1 (Standard graxpert.DEFAULT_STRENGTH 0,5; nur wenn installiert)
# linear_denoise_max: Obergrenze für das automatische lineare Entrauschen (Standard 1,0)
PROFILES: dict[str, dict] = {
    # denoise/chroma_denoise seit 2026-10-30 etwa halbiert (vorher 0,4–0,6 / 1,5–2,5): gleiche Objekthelligkeit, 15–35 % mehr
    # schwache Sterne, feines Korn statt glattem Himmel (wie Stellina) – gemessen an 10 Projekten, keines wurde dunkler
    # emission: lineares Entrauschen höchstens 0,3 – NGC 2359 (SNR 11, sonst 0,84) wurde weich, Filamente verwaschen, Sterne
    # aufgebläht (FWHM 6,0 statt 5,5 px bei Stellina, Stack 5,0); mit 0,3 knackiger, Nebel bleibt sichtbar. Übrige
    # Emissionsnebel (IC 405, NGC 6888, NGC 7000: 0,12–0,21) liegen darunter. M101 (Galaxie, SNR 11) braucht es weiter.
    "generic":      dict(gradient_degree=3, target_bg=0.08, asinh=30, denoise=0.3, chroma_denoise=1.5, saturation=1.35, contrast=0.25, sharpen=0.3, deconv=0, star_reduction=0.0, scnr=0.5, bg_dark=0.03, star_separation="off", star_brightness=0.85),
    "galaxy":       dict(gradient_degree=2, target_bg=0.07, asinh=120, denoise=0.3, chroma_denoise=1.5, saturation=1.30, contrast=0.35, sharpen=0.4, deconv=8, star_reduction=0.0, scnr=0.5, bg_dark=0.03, star_separation="off", star_brightness=0.85),
    "emission":     dict(gradient_degree=3, target_bg=0.08, asinh=40, denoise=0.3, chroma_denoise=1.5, saturation=1.50, contrast=0.35, sharpen=0.3, deconv=0, star_reduction=0.35, scnr=0.4, bg_dark=0.03, star_separation="off", star_brightness=0.85, linear_denoise_max=0.3),
    "reflection":   dict(gradient_degree=3, target_bg=0.08, asinh=30, denoise=0.3, chroma_denoise=1.5, saturation=1.40, contrast=0.3, sharpen=0.25, deconv=0, star_reduction=0.0, scnr=0.5, bg_dark=0.03, star_separation="off", star_brightness=0.85),
    "snr":          dict(gradient_degree=3, target_bg=0.08, asinh=40, denoise=0.3, chroma_denoise=1.5, saturation=1.50, contrast=0.35, sharpen=0.3, deconv=0, star_reduction=0.3, scnr=0.4, bg_dark=0.03, star_separation="off", star_brightness=0.85),
    # planetary: kleines, helles Objekt im Sternfeld – Weißpunkt höher und kein lineares Entrauschen (Ring brennt sonst
    # aus, Sterne blähen auf), wenig glätten und höheres target_bg: Korn und schwache Sterne bleiben (M57 gegen Stellina, 2026-10-30)
    "planetary":    dict(gradient_degree=3, target_bg=0.10, asinh=150, denoise=0.25, chroma_denoise=1.5, saturation=1.30, contrast=0.3, sharpen=0.5, deconv=12, star_reduction=0.0, scnr=0.5, bg_dark=0.03, star_separation="off", star_brightness=0.85, white_pct=99.98, linear_denoise_max=0.0, ai_denoise=0.3),
    "globular":     dict(gradient_degree=2, target_bg=0.06, asinh=200, denoise=0.3, chroma_denoise=1.5, saturation=1.25, contrast=0.2, sharpen=0.4, deconv=6, star_reduction=0.0, scnr=0.5, bg_dark=0.03, star_separation="off", star_brightness=1.0),
    "open_cluster": dict(gradient_degree=3, target_bg=0.06, asinh=60, denoise=0.3, chroma_denoise=1.5, saturation=1.30, contrast=0.2, sharpen=0.3, deconv=0, star_reduction=0.0, scnr=0.5, bg_dark=0.03, star_separation="off", star_brightness=1.0),
}

KIND_LABEL = {
    "galaxy": "Galaxie", "emission": "Emissionsnebel", "reflection": "Reflexionsnebel", "planetary": "Planetarischer Nebel",
    "snr": "Supernova-Überrest", "globular": "Kugelsternhaufen", "open_cluster": "Offener Sternhaufen", "generic": "Allgemein",
}


def profile_for(kind: str | None) -> dict:
    return dict(PROFILES.get(kind or "generic", PROFILES["generic"]))
