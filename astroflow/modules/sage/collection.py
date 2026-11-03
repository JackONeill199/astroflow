"""Sammlung & Erfolge: Zuordnung der Objekte zu Katalogen (Messier, Caldwell), Objektarten, Sternbildern und Entfernungen
aus der Wissensbasis sowie Kennzahlen des Beobachtungsprotokolls (Nächte, Belichtung). Alles stammt aus vorhandenen Daten."""
from __future__ import annotations

import re
from datetime import datetime, timezone

# Messier-Nummer → NGC/IC-Kennung (ohne M24, M40, M45: kein NGC-Eintrag; M102 meist = NGC 5866)
MESSIER_NGC = {
    1: "ngc 1952", 2: "ngc 7089", 3: "ngc 5272", 4: "ngc 6121", 5: "ngc 5904", 6: "ngc 6405", 7: "ngc 6475", 8: "ngc 6523",
    9: "ngc 6333", 10: "ngc 6254", 11: "ngc 6705", 12: "ngc 6218", 13: "ngc 6205", 14: "ngc 6402", 15: "ngc 7078", 16: "ngc 6611",
    17: "ngc 6618", 18: "ngc 6613", 19: "ngc 6273", 20: "ngc 6514", 21: "ngc 6531", 22: "ngc 6656", 23: "ngc 6494", 25: "ic 4725",
    26: "ngc 6694", 27: "ngc 6853", 28: "ngc 6626", 29: "ngc 6913", 30: "ngc 7099", 31: "ngc 224", 32: "ngc 221", 33: "ngc 598",
    34: "ngc 1039", 35: "ngc 2168", 36: "ngc 1960", 37: "ngc 2099", 38: "ngc 1912", 39: "ngc 7092", 41: "ngc 2287", 42: "ngc 1976",
    43: "ngc 1982", 44: "ngc 2632", 46: "ngc 2437", 47: "ngc 2422", 48: "ngc 2548", 49: "ngc 4472", 50: "ngc 2323", 51: "ngc 5194",
    52: "ngc 7654", 53: "ngc 5024", 54: "ngc 6715", 55: "ngc 6809", 56: "ngc 6779", 57: "ngc 6720", 58: "ngc 4579", 59: "ngc 4621",
    60: "ngc 4649", 61: "ngc 4303", 62: "ngc 6266", 63: "ngc 5055", 64: "ngc 4826", 65: "ngc 3623", 66: "ngc 3627", 67: "ngc 2682",
    68: "ngc 4590", 69: "ngc 6637", 70: "ngc 6681", 71: "ngc 6838", 72: "ngc 6981", 73: "ngc 6994", 74: "ngc 628", 75: "ngc 6864",
    76: "ngc 650", 77: "ngc 1068", 78: "ngc 2068", 79: "ngc 1904", 80: "ngc 6093", 81: "ngc 3031", 82: "ngc 3034", 83: "ngc 5236",
    84: "ngc 4374", 85: "ngc 4382", 86: "ngc 4406", 87: "ngc 4486", 88: "ngc 4501", 89: "ngc 4552", 90: "ngc 4569", 91: "ngc 4548",
    92: "ngc 6341", 93: "ngc 2447", 94: "ngc 4736", 95: "ngc 3351", 96: "ngc 3368", 97: "ngc 3587", 98: "ngc 4192", 99: "ngc 4254",
    100: "ngc 4321", 101: "ngc 5457", 102: "ngc 5866", 103: "ngc 581", 104: "ngc 4594", 105: "ngc 3379", 106: "ngc 4258",
    107: "ngc 6171", 108: "ngc 3556", 109: "ngc 3992", 110: "ngc 205",
}
NGC_MESSIER = {v: k for k, v in MESSIER_NGC.items()}
MESSIER_ALIAS = {45: ("plejaden",), 24: ("kleine sagittarius-sternwolke",), 40: ("winnecke 4",)}

_M_NAME = re.compile(r"\s*(?:m|messier)\s?(\d{1,3})(?![\d.+/-])", re.I)
_M_ALIAS = re.compile(r"(?:m|messier) ?(\d{1,3})", re.I)
_C_ALIAS = re.compile(r"(?:c|caldwell) ?(\d{1,3})", re.I)
_NGC_NAME = re.compile(r"\s*(ngc|ic)\s?(\d+)(?![\d.+/-])", re.I)

# Anzeige-Reihenfolge der Objektarten (Schlüssel wie `objects.KIND_LABEL`)
KINDS = ("galaxy", "emission", "reflection", "planetary", "snr", "globular", "open_cluster")


def messier_number(name: str, aliases=()) -> int | None:
    """Messier-Nummer eines Objekts: aus dem Namen („M 31“, „M31 Andromedagalaxie“), über NGC/IC-Namen („NGC 224“) oder über
    die Kennungen der Wissensbasis (SIMBAD kennt „M 31“ als Alias von NGC 224)."""
    m = _M_NAME.match(name or "")
    if m and 1 <= int(m.group(1)) <= 110:
        return int(m.group(1))
    m = _NGC_NAME.match(name or "")
    if m and (n := NGC_MESSIER.get(f"{m.group(1).lower()} {int(m.group(2))}")):
        return n
    for a in aliases:
        m = _M_ALIAS.fullmatch((a or "").strip())
        if m and 1 <= int(m.group(1)) <= 110:
            return int(m.group(1))
    low = (name or "").lower()
    return next((n for n, names in MESSIER_ALIAS.items() if any(x in low for x in names)), None)


def caldwell_number(name: str, aliases=()) -> int | None:
    for a in (name, *aliases):
        m = _C_ALIAS.fullmatch((a or "").strip())
        if m and 1 <= int(m.group(1)) <= 109:
            return int(m.group(1))
    return None


def kind_of(label: str = "", af_kind: str | None = None, otype: str = "") -> str | None:
    """Objektart aus KB-Art, Beschriftung oder SIMBAD-Typ; None = unbekannt/allgemein."""
    if af_kind in KINDS:
        return af_kind
    t = f"{label} {otype}".lower()
    for key, words in (("snr", ("supernova", "snr")), ("planetary", ("planet", "pn")), ("globular", ("kugel", "glc")),
                       ("open_cluster", ("offener", "opc")), ("galaxy", ("galax", "agn", "grg")), ("reflection", ("reflex", "rne")),
                       ("emission", ("emission", "hii"))):
        if any(w in t.split() or (len(w) > 3 and w in t) for w in words):
            return key
    return None


def enrich(mod, names: list[str]) -> dict[str, dict]:
    """Je Objektname: Messier-/Caldwell-Nummer, Art, Sternbild, Entfernung (Mio. Lichtjahre) aus der Wissensbasis."""
    kb, out = mod.kb, {}
    for n in names:
        e = None
        try:
            e = kb.resolve(n)
        except Exception:
            pass
        al = kb.aliases(e["id"]) if e else []
        fact = (lambda k: (kb.fact(e["id"], k) if e else None))
        dist = fact("distance_ly")
        con = fact("constellation")
        out[n] = {"m": messier_number(n, al), "c": caldwell_number(n, al), "entity": e and e["id"],
                  "kind": kind_of(label=(e or {}).get("otype_label") or "", af_kind=((e or {}).get("data") or {}).get("af_kind"),
                                  otype=(e or {}).get("otype") or ""),
                  "con": (con or {}).get("value"), "mly": round(dist["num"] / 1e6, 2) if dist and dist.get("num") else 0}
    return out


# ---- Beobachtungsprotokoll ---------------------------------------------------------------------
SEASONS = {12: "winter", 1: "winter", 2: "winter", 3: "spring", 4: "spring", 5: "spring",
           6: "summer", 7: "summer", 8: "summer", 9: "autumn", 10: "autumn", 11: "autumn"}


def _local_hour(iso: str | None) -> int | None:
    try:
        t = datetime.fromisoformat((iso or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    return (t if t.tzinfo else t.replace(tzinfo=timezone.utc)).astimezone().hour


def _at(night: str) -> str:
    return f"{night}T12:00:00+00:00"


def log_stats(log: list[dict]) -> dict:
    """Nächte, Nachteule, Jahreszeiten und Belichtung aus dem Aufnahmeprotokoll; `dates` = Zeitpunkte für rückwirkende Erfolge."""
    sessions = [(p, s) for p in log for s in p.get("sessions") or [] if s.get("frames") and s.get("night") not in (None, "?")]
    nights = sorted({s["night"] for _, s in sessions})
    owl = sorted(s["night"] for _, s in sessions
                 if any(h is not None and 2 <= h < 6 for h in (_local_hour(s.get("first")), _local_hour(s.get("last")))))
    seasons: dict[str, str] = {}
    for n in nights:
        try:
            seasons.setdefault(SEASONS[int(n[5:7])], n)
        except (ValueError, KeyError):
            pass
    secs = lambda s: s.get("used_s") or s.get("exposure_s") or 0
    per, best = {}, {}
    for p, s in sorted(sessions, key=lambda x: x[1]["night"]):
        per[p["id"]] = per.get(p["id"], 0) + secs(s)
        for h in (1, 10):
            if per[p["id"]] >= h * 3600:
                best.setdefault(h, s["night"])
    total, all_100 = 0.0, None
    for _, s in sorted(sessions, key=lambda x: x[1]["night"]):
        total += secs(s)
        if total >= 360000 and not all_100:
            all_100 = s["night"]
    dates = {}
    for k, n in (("nights_5", 5), ("nights_10", 10)):
        if len(nights) >= n:
            dates[k] = _at(nights[n - 1])
    if owl:
        dates["night_owl"] = _at(owl[0])
    if len(seasons) == 4:
        dates["seasons_4"] = _at(max(seasons.values()))
    for k, h in (("exposure_1h", 1), ("exposure_10h", 10)):
        if h in best:
            dates[k] = _at(best[h])
    if all_100:
        dates["total_100h"] = _at(all_100)
    return {"nights": len(nights), "night_owl": len(owl), "seasons": len(seasons), "season_list": sorted(seasons),
            "max_obj_hours": round(max(per.values(), default=0) / 3600, 1), "total_hours": round(sum(
                secs(s) for _, s in sessions) / 3600, 1), "dates": dates}
