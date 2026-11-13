"""Teilen-Karte für Foren & Co. (seit 0.71, Nutzerwunsch „Bibliothek teilen“ – zuerst als Bilddatei, kein Link).

Aus einer fertigen Bildversion entsteht eine gestaltete JPEG-Karte: das Foto groß, daneben bzw. darunter Objekt, Aufnahme,
„Was AstroFlow herausgefunden hat“ (nur vorhandene Auswertungen, keine Netzabfrage) und eine dezente Fußzeile. Dazu derselbe
Inhalt als BBCode/Markdown zum Einfügen in einen Forenbeitrag.

Datenschutz (verbindlich, `tests/test_share.py`): Auf Karte und Text stehen **nie** Standort, Ortsnamen, Koordinaten des
Beobachtungsorts, Pfade, Dateinamen, Uhrzeiten oder Gerätenamen mit Seriennummer. `collect` liest deshalb nur ausgewählte
Felder und reicht nur fertig formatierte, bereinigte Texte weiter – `render`/`text` sehen die Datenbank nie.

Gerendert wird serverseitig mit Pillow und der mitgelieferten Schrift Inter (Teilmenge, SIL OFL 1.1, `schrift/`): überall
gleiches Ergebnis (auch auf gekoppelten Handys), ohne Systemschriften, und die Datei lässt sich in Tests prüfen.

Forengrößen (Stand 11/2026): Cloudy Nights verkleinert Bilder über 1600 × 1200 px, ältere Regeln nennen 500 KB, die
Nutzungsbedingungen 4,88 MB; deutsche Foren (Astrotreff, astronomie.de) nehmen Anhänge im Bereich 1–2 MB an. Voreinstellung
daher „Forum“: lange Kante 1600 px, Querformat höchstens 1600 × 1200, JPEG ≤ 1 MB; „Groß“: 2048 px, ≤ 1,5 MB.
"""
from __future__ import annotations

import io
import json
import math
import re
import unicodedata
from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

REPO = "github.com/JackONeill199/astroflow"
FONT = Path(__file__).parent / "schrift" / "Inter-AstroFlow.ttf"
HIMMEL = Path(__file__).parent / "static" / "himmel"
SIZES = {1600: 1_000_000, 2048: 1_450_000}             # lange Kante → Höchstgröße der JPEG-Datei
LAYOUTS = ("quer", "hoch")
MAX_FINDS = 4
MAX_LABELS = 5

# Farben wie das Designsystem (`static/style.css`, :root)
BG = (15, 17, 21)
PANEL = (20, 23, 28)
SURFACE = (26, 30, 36)
BORDER = (48, 54, 64)
FG = (240, 242, 245)
MUT = (170, 178, 189)
DIM = (130, 140, 153)
ACC = (76, 154, 255)
ACC_LIGHT = (143, 190, 255)
WARM = (255, 217, 168)


# ------------------------------------------------------------------ Formatieren --- #
def _de(v: float, nd: int = 1) -> str:
    """Zahl deutsch: Tausenderpunkt, Dezimalkomma."""
    s = f"{v:,.{nd}f}"
    return s.replace(",", "§").replace(".", ",").replace("§", ".")


def fmt_int(n: int) -> str:
    return _de(n, 0)


def fmt_years(ly: float) -> str:
    """Lichtlaufzeit/Entfernung in Jahren: „8.600“, „2,5 Mio.“, „1,2 Mrd.“"""
    if ly >= 1e9:
        return f"{_de(ly / 1e9, 1 if ly < 1e10 else 0)} Mrd."
    if ly >= 1e6:
        return f"{_de(ly / 1e6, 1 if ly < 1e7 else 0)} Mio."
    if ly >= 10_000:
        return fmt_int(round(ly, -2))
    return fmt_int(round(ly))


def fmt_dur(s: float | None) -> str | None:
    if not s or s <= 0:
        return None
    m = int(round(s / 60))
    if m < 1:
        return f"{int(round(s))} s"
    h, m = divmod(m, 60)
    return f"{h} h {m} min" if h and m else f"{h} h" if h else f"{m} min"


def _date(d: str) -> tuple[int, int, int] | None:
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", str(d or ""))
    return (int(m[1]), int(m[2]), int(m[3])) if m else None


def fmt_dates(nights: list[str]) -> str | None:
    """Nur Datum, nie Uhrzeit: „12.08.2026“, „12.–13.08.2026“, „30.08.–02.09.2026“, „12.08.2025 – 03.01.2026“, bei vielen
    verstreuten Nächten „Aug. 2025 – Jan. 2026“."""
    ds = sorted({x for x in (_date(n) for n in nights or []) if x})
    if not ds:
        return None
    a, b = ds[0], ds[-1]
    f = lambda d, y=True: f"{d[2]:02d}.{d[1]:02d}." + (f"{d[0]}" if y else "")
    if a == b:
        return f(a)
    if a[0] == b[0] and a[1] == b[1]:
        return f"{a[2]:02d}.–{f(b)}"
    if a[0] == b[0]:
        return f"{f(a, False)}–{f(b)}"
    return f"{f(a)} – {f(b)}"


# Seriennummern und Gerätekennungen: „stellina-b7337b“, „ASI294MC Pro SN 12345“, „Seestar S50 #4F2A9C1“
_SERIAL = re.compile(r"(?i)(?:\b(?:s/?n|serial|seriennr\.?)[\s:#-]*\w+)|(?:#\s?\w+)|(?:[-_][0-9a-f]{4,}\b)|(?:\b(?=[0-9a-f]*\d)(?=[0-9a-f]*[a-f])[0-9a-f]{6,}\b)|(?:\b\d{6,}\b)")


def clean_device(name: str | None) -> str | None:
    """Gerätename ohne Seriennummer/Kennung, höchstens 40 Zeichen. Bekannte Smart-Teleskope über `tutor.detect_device`."""
    if not name:
        return None
    from .tutor import detect_device
    d = detect_device([str(name)])
    if d["id"] != "camera":
        return d["name"]
    s = _SERIAL.sub(" ", unicodedata.normalize("NFKC", str(name)))
    s = re.sub(r"[\x00-\x1f]|[\\/:*?\"<>|]", " ", s)
    s = re.sub(r"\s{2,}", " ", s).strip(" -_.,")
    return s[:40] or None


def import_device(imp: dict) -> str | None:
    """Gerät eines importierten Bildes aus den EXIF-Feldern des Imports (`make`/`model`, Teleskopserie `series.instrument`)."""
    make, model = str(imp.get("make") or "").strip(), str(imp.get("model") or "").strip()
    series = (imp.get("series") or {}).get("instrument") if isinstance(imp.get("series"), dict) else None
    if model and make and not model.lower().startswith(make.lower()):
        return f"{make} {model}"
    return model or series or make or imp.get("camera") or imp.get("instrument") or None


def clean_text(v: str | None, limit: int = 60) -> str:
    """Freitext vom Client (Fotograf): ohne Steuerzeichen, eine Zeile, begrenzt."""
    s = unicodedata.normalize("NFKC", str(v or ""))
    s = "".join(ch for ch in s if unicodedata.category(ch)[0] != "C")
    return re.sub(r"\s+", " ", s).strip()[:limit]


def slug(name: str) -> str:
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")[:48] or "bild"


# ------------------------------------------------------------------ Daten sammeln --- #
@lru_cache(maxsize=1)
def _constellations() -> dict:
    try:
        return {c["id"]: c for c in json.loads((HIMMEL / "sternbilder.json").read_text(encoding="utf-8"))["items"]}
    except (OSError, ValueError, KeyError):
        return {}


@lru_cache(maxsize=1)
def _stars() -> np.ndarray:
    try:
        a = np.asarray(json.loads((HIMMEL / "sterne.json").read_text(encoding="utf-8"))["stars"], dtype=float)
        return a.reshape(-1, 4)
    except (OSError, ValueError, KeyError):
        return np.zeros((0, 4))


def constellation(ra: float | None, dec: float | None) -> dict | None:
    """Sternbild der Objektposition (astropy, lokal) mit deutschem Namen aus dem Himmelsatlas."""
    if ra is None or dec is None:
        return None
    try:
        from astropy.coordinates import SkyCoord
        abbr = str(SkyCoord(ra, dec, unit="deg").get_constellation(short_name=True))
    except Exception:
        return None
    c = _constellations().get(abbr)
    return {"id": abbr, "name": c["name"] if c else abbr}


_CAT = re.compile(r"^(?:NGC|IC|UGC|PGC|LEDA|MCG|CGCG|Mrk|Arp|ESO|2MASX|2MASS|SDSS|M|Abell)\s?[\w+.\-]{1,20}$")


def galaxy_name(name: str | None) -> str | None:
    """Katalognamen wie „PGC 2304“ oder „UGC 10“ zeigen; SIMBAD-Interna („NAME [G2012b] C039“) weglassen."""
    n = clean_text(re.sub(r"^NAME\s+", "", str(name or "")), 30)
    return n if _CAT.match(n) else None


def _plural(n: int, one: str, many: str) -> str:
    return f"{fmt_int(n)} {one if n == 1 else many}"


def _finds(st: dict, exposure: float | None, imported: bool, pic: dict | None, ecc: float | None) -> list[dict]:
    """Highlights in Rangfolge – nur aktuelle, erfolgreiche Auswertungen. value = große Zahl, label = Erklärung."""
    from . import grade
    from .asteroids import ASTEROID_VERSION
    from .dashboard import _asteroid_info, _depth_info, _hrd_info
    from .depth import DEPTH_VERSION
    from .hrd import HRD_VERSION
    out: list[dict] = []
    cur = lambda k, v: (st.get(k) or {}).get("ok") and (st.get(k) or {}).get("v") == v
    d = _depth_info(st["depth"], exposure) if cur("depth", DEPTH_VERSION) else None
    if d:
        if d["far_gal"] and d["far_gal"].get("ly"):
            g, gn = d["far_gal"], galaxy_name(d["far_gal"].get("name"))
            out.append({"key": "far", "value": f"{fmt_years(g['ly'])} Jahre",
                        "label": "war das Licht der fernsten Galaxie im Bild unterwegs" + (f" ({gn})" if gn else "")})
        elif d["far_star"] and d["far_star"].get("ly"):
            out.append({"key": "far", "value": f"{fmt_years(d['far_star']['ly'])} Jahre",
                        "label": "war das Licht des fernsten vermessenen Sterns (Gaia) unterwegs"})
        if d["m5"] is not None:
            out.append({"key": "depth", "value": f"{_de(d['m5'])} mag", "label": "Grenzgröße – so schwache Sterne zeigt das Bild noch"})
    a = _asteroid_info(st["asteroids"]) if cur("asteroids", ASTEROID_VERSION) else None
    if a and a["n"]:
        names = [i["name"] for i in a["items"] if i.get("name")][:2]
        out.append({"key": "asteroids", "value": _plural(a["n"], "Asteroid", "Asteroiden"),
                    "label": "im Bild nachgewiesen" + (f": {', '.join(names)}" if names else "")})
    h = _hrd_info(st["hrd"]) if cur("hrd", HRD_VERSION) else None
    if h and h["age"]:
        out.append({"key": "hrd", "value": f"≈ {fmt_years(h['age'])} Jahre",
                    "label": "alt ist der Sternhaufen – gemessen im eigenen HR-Diagramm" + (f" ({fmt_int(h['members'])} Sterne)" if h.get("members") else "")})
    tr = st.get("trails") or {}
    if (tr.get("lines") or 0) > 0:
        out.append({"key": "trails", "value": _plural(int(tr["lines"]), "Satellitenspur", "Satellitenspuren"),
                    "label": "automatisch erkannt und aus dem Bild entfernt"})
    if d and d.get("erfasst"):
        out.append({"key": "gaia", "value": _plural(int(d["erfasst"]), "Stern", "Sterne"), "label": "aus dem Gaia-Katalog im Bild wiedergefunden"})
    if d and (d.get("galaxies") or 0) > 1:
        out.append({"key": "galaxies", "value": _plural(int(d["galaxies"]), "Galaxie", "Galaxien"), "label": "im Bildfeld erkennbar"})
    sky = st.get("_sky")
    if sky and sky.get("sqm"):
        out.append({"key": "sky", "value": f"{_de(sky['sqm'], 2)} mag/″²",
                    "label": "Himmelshelligkeit, aus dem Bild gemessen" + (f" (Bortle {sky['bortle']})" if sky.get("bortle") else "")})
    g = None if imported else grade.of_result(st, ecc, DEPTH_VERSION)
    if g and g.get("score") is not None:
        out.append({"key": "grade", "value": f"{_de(g['score'])} von 10", "label": "Bildnote aus Tiefe, Rauschen und Sternschärfe"})
    if imported and pic:
        if pic.get("fov"):
            out.append({"key": "solve", "value": pic["fov"], "label": "Bildfeld – per Plate-Solving am Himmel bestimmt"})
        if pic.get("n_objects"):
            out.append({"key": "objects", "value": _plural(pic["n_objects"], "Katalogobjekt", "Katalogobjekte"), "label": "im Bildfeld gefunden"})
    return out


def collect(eng, rid: int, objects_fn=None) -> dict:
    """Alle Inhalte der Karte für eine Ergebnisversion – fertig formatiert und bereinigt. KeyError, wenn es sie nicht gibt.
    objects_fn(rid) liefert die Plate-Solving-Objekte in JPG-Pixeln (wie `/results/{rid}/objects`)."""
    from . import grade, skyquality, tutor
    from .objects import KIND_LABEL, resolve_name
    db = eng.db
    x = db.one("SELECT * FROM results WHERE id=?", (rid,))
    if not x:
        raise KeyError(rid)
    p = db.one("SELECT * FROM projects WHERE id=?", (x["project_id"],))
    if not p:
        raise KeyError(rid)
    st, files = json.loads(x["stats"] or "{}"), json.loads(x["files"] or "{}")
    opts = json.loads(p["options"] or "{}")
    imported = opts.get("input_type") == "picture"
    pid = p["id"]

    # Objekt
    name = clean_text(p["name"], 80) or "Astrofoto"
    imp_info = st.get("imported") or opts.get("imported") or {}
    if imported and not (p["obj_id"] or opts.get("name_custom") or imp_info.get("series")):
        name = "Astrofoto"                  # Name stammt noch aus dem Dateinamen (Ort, Zeit, Seriennummer möglich) – nie zeigen
    obj = {"name": name, "kind": KIND_LABEL.get(p["kind"]) if p["kind"] and p["kind"] != "generic" else None,
           "constellation": None, "distance": None}
    const = constellation(p["ra"], p["dec"]) if p["obj_id"] or not imported or (opts.get("picture_solve") or {}).get("state") in ("done", "manual") else None
    if const:
        obj["constellation"] = const["name"]
    facts = None
    fn = eng.ctx.services.get("object_facts") if getattr(eng, "ctx", None) else None
    if fn:
        try:
            facts = fn(p["obj_id"] or p["name"])
        except Exception:
            facts = None
    if facts:
        if not obj["kind"] and facts.get("otype"):
            obj["kind"] = clean_text(facts["otype"], 40)
        if facts.get("distance_ly"):
            obj["distance"] = f"{fmt_years(float(facts['distance_ly']))} Lichtjahre"
    main = ((st.get("depth") or {}).get("hauptobjekt") or {}) if (st.get("depth") or {}).get("ok") else {}
    if not obj["distance"] and main.get("ly"):
        obj["distance"] = f"{fmt_years(float(main['ly']))} Lichtjahre"

    # Aufnahme
    shot: dict = {}
    pic = None
    if imported:
        imp = st.get("imported") or {}
        shot["device"] = clean_device(import_device(imp))
        shot["dates"] = fmt_dates([str(imp.get("date_obs") or "")[:10]])
        ps = opts.get("picture_solve") or {}
        sol = ps.get("solution") or {}
        if ps.get("state") == "done" and sol.get("fov_w"):
            pic = {"fov": f"{_de(float(sol['fov_w']), 2)}° × {_de(float(sol['fov_h']), 2)}°",
                   "n_objects": len([o for o in sol.get("objects") or [] if o.get("x") is not None])}
    else:
        ins = [r["i"] for r in db.q("SELECT json_extract(info,'$.instrument') i, COUNT(*) n FROM frames WHERE project_id=? AND kind='light' "
                                    "GROUP BY i ORDER BY n DESC", (pid,)) if r["i"]]
        shape = st.get("shape")
        dev = tutor.detect_device(ins, tuple(shape) if shape else None)
        shot["device"] = dev["name"] if dev["id"] != "camera" else clean_device(dev["name"])
        if dev.get("guessed"):
            shot["device"] = None                       # nur aus dem Bildformat geraten – lieber weglassen
        shot["exposure"] = fmt_dur(x["exposure"])
        shot["frames"] = int(x["n_frames"] or 0)
        rej = db.one("SELECT COUNT(*) n FROM frames WHERE project_id=? AND kind='light' AND status='rejected'", (pid,))
        shot["rejected"] = int(rej["n"]) if rej else 0
        nights = st.get("nights") or [r["night"] for r in db.q("SELECT DISTINCT night FROM frames WHERE project_id=? AND kind='light' "
                                                                  "AND night IS NOT NULL", (pid,))]
        shot["nights"] = len(set(nights))
        shot["dates"] = fmt_dates(nights)
        bands = [b for b in (st.get("bands") or {}) if b not in ("RGB", "L")]
        filt = {clean_text(r["f"], 20) for r in db.q("SELECT DISTINCT json_extract(info,'$.filter') f FROM frames WHERE project_id=? "
                                                      "AND kind='light' AND status!='rejected'", (pid,)) if r["f"]}
        filt = sorted(f for f in filt if f and f.lower() not in ("none", "no", "clear", "lum", "l"))
        shot["filter"] = " · ".join(bands or filt)[:40] or None
    # Himmelshelligkeit: nur Zahlen der Nächte dieses Objekts (Ortsname/Standort bleiben draußen)
    if not imported:
        try:
            own = [n for n in skyquality.nights_of(db, pid) if n.get("sqm") is not None]
            if own:
                best = max(own, key=lambda n: n["sqm"])
                st["_sky"] = {"sqm": float(best["sqm"]), "bortle": best.get("bortle")}
        except Exception:
            pass
    ecc = None if imported else grade.project_ecc(db, [pid]).get(pid)
    finds = _finds(st, x["exposure"], imported, pic, ecc)

    # Objektnamen im Bild (Plate-Solving): 2–5 größte/hellste
    labels = []
    if objects_fn:
        try:
            o = objects_fn(rid) or {}
        except Exception:
            o = {}
        w, h = o.get("width"), o.get("height")
        if w and h:
            # Objekte, die weit über das Bild reichen (Cirrusnebel, M31 im engen Feld), bekommen keine Marke: ihre Mitte sagt nichts
            cand = [c for c in o.get("objects") or [] if c.get("x") is not None and 0 <= c["x"] < w and 0 <= c["y"] < h and c.get("name")
                    and (c.get("radius") or 0) <= 0.45 * max(w, h)]
            cand.sort(key=lambda c: (-(c.get("radius") or 0), -(c.get("size") or 0)))

            seen: set[str] = set()
            for c in cand:
                # deutscher Name aus dem eigenen Katalog („M 31 Andromeda Galaxy“ → Projektname); Aliase (NGC 6853 = M27) nur einmal
                k = resolve_name(c.get("id"))
                key = k.id if k else (c.get("id") or c["name"])
                # Teil eines Objekts unter eigenem Namen (IC 1340 im Cirrusnebel) bleibt beim eigenen Namen
                n = name if c.get("id") == p["obj_id"] else clean_text(k.display if k and k.id == c.get("id") else c["name"], 40)
                if n and key not in seen and n not in seen and len(labels) < MAX_LABELS:
                    seen |= {key, n}
                    labels.append({"name": n, "x": float(c["x"]) / w, "y": float(c["y"]) / h, "r": float(c.get("radius") or 0) / max(w, h)})
    sky_pos = {"ra": float(p["ra"]), "dec": float(p["dec"]), "const": const["id"] if const else None} if const else None

    from ... import __version__
    jpg = files.get("jpg")
    return {"rid": rid, "pid": pid, "imported": imported, "object": obj, "shot": shot, "finds": finds, "labels": labels,
            "sky": sky_pos, "version": __version__, "photo": jpg if jpg and Path(jpg).is_file() else None}


# ------------------------------------------------------------------ Optionen --- #
PARTS = ("object", "shot", "finds", "map", "labels")


def options(q: dict, data: dict) -> dict:
    """Optionen aus der Anfrage prüfen (feste Werte, Freitext bereinigt). Voreinstellung: alles an, erste 3 Highlights."""
    layout = q.get("layout") if q.get("layout") in LAYOUTS else "quer"
    try:
        size = int(q.get("size") or 1600)
    except ValueError:
        size = 1600
    size = size if size in SIZES else 1600
    parts = set(PARTS) if q.get("parts") in (None, "") else {s for s in str(q["parts"]).split(",") if s in PARTS}
    avail = [f["key"] for f in data["finds"]]
    finds = avail[:3] if q.get("finds") in (None, "") else [k for k in str(q["finds"]).split(",") if k in avail][:MAX_FINDS]
    return {"layout": layout, "size": size, "parts": parts, "finds": finds,
            "photographer": clean_text(q.get("photographer")), "own": str(q.get("own") or "") in ("1", "true")}


def needs_owner(data: dict, o: dict) -> bool:
    """Fremd importierte Bilder: nur mit Bestätigung „mein Bild“ oder genanntem Fotografen."""
    return data["imported"] and not o["own"] and not o["photographer"]


# ------------------------------------------------------------------ Text --- #
def lines(data: dict, o: dict) -> dict:
    """Inhalt als neutrale Zeilen – Grundlage für Karte und Forentext."""
    ob, sh = data["object"], data["shot"]
    show = "object" in o["parts"]
    sub2 = " · ".join(s for s in (f"im Sternbild {ob['constellation']}" if ob["constellation"] else None,
                                   f"{ob['distance']} entfernt" if ob["distance"] else None) if s)
    shot = []
    if "shot" in o["parts"]:
        if sh.get("device"):
            shot.append(("Gerät", sh["device"]))
        if sh.get("exposure"):
            shot.append(("Belichtung", sh["exposure"] + " gesamt"))
        if sh.get("frames"):
            shot.append(("Aufnahmen", f"{fmt_int(sh['frames'])} gestackt" + (f" · {fmt_int(sh['rejected'])} aussortiert" if sh.get("rejected") else "")))
        if sh.get("dates"):
            label = "Aufgenommen" if data["imported"] or (sh.get("nights") or 0) <= 1 else f"{sh['nights']} Nächte"
            shot.append((label, sh["dates"]))
        if sh.get("filter"):
            shot.append(("Filter", sh["filter"]))
    finds = [f for k in o["finds"] for f in data["finds"] if f["key"] == k] if "finds" in o["parts"] else []
    credit = f"Foto: {o['photographer']}" if o["photographer"] else None
    foot = "Bestimmt und analysiert mit AstroFlow" if data["imported"] else "Vollautomatisch gestackt und analysiert mit AstroFlow"
    return {"title": ob["name"], "sub": (ob["kind"] or "") if show else "", "sub2": sub2 if show else "",
            "shot": shot, "finds": finds, "credit": credit, "foot": foot}


def text(data: dict, o: dict, fmt: str = "bbcode") -> str:
    """Forentext (BBCode) bzw. Markdown – dieselben Angaben wie auf der Karte, Repo-Adresse als Text am Ende."""
    L = lines(data, o)
    b = (lambda s: f"[b]{s}[/b]") if fmt == "bbcode" else (lambda s: f"**{s}**")
    out = [b(L["title"])]
    head = " · ".join(s for s in (L["sub"], L["sub2"]) if s)
    if head:
        out.append(head)
    if L["credit"]:
        out.append(L["credit"])
    lst = (lambda items: ["[list]" + "\n".join(f"[*]{i}" for i in items) + "[/list]"]) if fmt == "bbcode" else \
          (lambda items: [f"- {i}" for i in items])
    if L["shot"]:
        out += ["", b("Aufnahme")] + lst([f"{k}: {v}" for k, v in L["shot"]])
    if L["finds"]:
        out += ["", b("Was AstroFlow herausgefunden hat")] + lst([f"{f['value']} {f['label']}" for f in L["finds"]])
    tail = f"{L['foot']} {data['version']} – {REPO}"
    out += ["", f"[size=85]{tail}[/size]" if fmt == "bbcode" else f"_{tail}_"]
    return "\n".join(out)


# ------------------------------------------------------------------ Zeichnen --- #
@lru_cache(maxsize=64)
def font(size: int, weight: int = 400) -> ImageFont.FreeTypeFont:
    try:
        f = ImageFont.truetype(str(FONT), size)
        try:
            f.set_variation_by_axes([max(14, min(32, size * 0.75)), weight])
        except (OSError, AttributeError):
            pass
        return f
    except OSError:                                   # Schrift fehlt (beschädigte Installation): Pillow-Standardschrift
        return ImageFont.load_default(size)


def _tw(f, s: str) -> float:
    return f.getlength(s)


def wrap(s: str, f, width: float, max_lines: int = 3) -> list[str]:
    words, out, cur = s.split(), [], ""
    for w in words:
        t = f"{cur} {w}".strip()
        if _tw(f, t) <= width or not cur:
            cur = t
        else:
            out.append(cur)
            cur = w
    if cur:
        out.append(cur)
    if len(out) > max_lines:
        out = out[:max_lines]
        while out[-1] and _tw(f, out[-1] + " …") > width:
            out[-1] = out[-1].rsplit(" ", 1)[0] if " " in out[-1] else out[-1][:-1]
        out[-1] += " …"
    return out


def logo(px: int) -> Image.Image:
    """AstroFlow-Zeichen wie `static/icon.svg` (Galaxie auf dunkler Kachel), in 4-facher Größe gerechnet und verkleinert."""
    n = px * 4
    k = n / 64
    yy, xx = np.mgrid[0:n, 0:n].astype(np.float32) / k
    a = math.radians(30)
    dx, dy = xx - 32, yy - 32
    u, v = dx * math.cos(a) - dy * math.sin(a), dx * math.sin(a) + dy * math.cos(a)
    r = np.sqrt((u / 25) ** 2 + (v / 10) ** 2)
    stops = [(0, (199, 214, 255, 255)), (0.45, (109, 141, 255, 255)), (1.0, (42, 27, 94, 0))]
    img = np.zeros((n, n, 4), np.float32)
    img[...] = (10, 13, 22, 255)
    col = np.zeros((n, n, 4), np.float32)
    for (t0, c0), (t1, c1) in zip(stops, stops[1:]):
        m = (r >= t0) & (r <= t1)
        f = ((r - t0) / (t1 - t0))[m][:, None]
        col[m] = np.array(c0) * (1 - f) + np.array(c1) * f
    al = col[..., 3:4] / 255
    img[..., :3] = img[..., :3] * (1 - al) + col[..., :3] * al
    for cx, cy, rr, c in ((32, 32, 4.5, (255, 255, 255)), (14, 15, 1.4, (255, 255, 255)), (50, 48, 1.1, (255, 255, 255)), (49, 14, 1.7, WARM)):
        m = np.clip(rr - np.hypot(xx - cx, yy - cy), 0, 1)[..., None] * 1.0
        img[..., :3] = img[..., :3] * (1 - m) + np.array(c) * m
    out = Image.fromarray(img.clip(0, 255).astype(np.uint8), "RGBA")
    mask = Image.new("L", (n, n), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, n - 1, n - 1), radius=14 * k, fill=255)
    out.putalpha(mask)
    return out.resize((px, px), Image.LANCZOS)


def skymap(d: int, sky: dict, radius_deg: float = 28.0) -> Image.Image:
    """Kleine runde Himmelskarte um das Objekt: Sternbildlinien (eigenes Sternbild hervorgehoben), helle Sterne, Marke."""
    n = d * 3
    img = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    dr = ImageDraw.Draw(img)
    ra0, de0 = math.radians(sky["ra"]), math.radians(sky["dec"])
    R = n / 2 - 2
    scale = R / (2 * math.tan(math.radians(radius_deg) / 2))

    def proj(ra, dec):
        ra, dec = math.radians(ra), math.radians(dec)
        c = math.sin(de0) * math.sin(dec) + math.cos(de0) * math.cos(dec) * math.cos(ra - ra0)
        if c < 0.3:
            return None
        k = 2 / (1 + c)
        x = k * math.cos(dec) * math.sin(ra - ra0)
        y = k * (math.cos(de0) * math.sin(dec) - math.sin(de0) * math.cos(dec) * math.cos(ra - ra0))
        return n / 2 - x * scale, n / 2 - y * scale               # Osten links wie am Himmel

    dr.ellipse((1, 1, n - 2, n - 2), fill=(12, 16, 26, 255), outline=BORDER + (255,), width=3)
    lw = max(2, n // 160)
    for cid, c in _constellations().items():
        own = cid == sky.get("const")
        for line in c.get("lines") or []:
            pts = [proj(line[i], line[i + 1]) for i in range(0, len(line) - 1, 2)]
            for a, b in zip(pts, pts[1:]):
                if a and b:
                    dr.line((a, b), fill=(ACC_LIGHT + (235,)) if own else (90, 110, 150, 150), width=lw * 2 if own else lw)
    for ra, dec, mag, _bv in _stars():
        if mag > 5.0:
            continue
        p = proj(ra, dec)
        if p:
            rr = max(1.2, (5.6 - mag) * n / 300)
            dr.ellipse((p[0] - rr, p[1] - rr, p[0] + rr, p[1] + rr), fill=(235, 240, 255, 230))
    c = n / 2
    for rr, w, col in ((n * 0.07, max(3, n // 90), WARM + (255,)),):
        dr.ellipse((c - rr, c - rr, c + rr, c + rr), outline=col, width=w)
    dr.line((c, c - n * 0.13, c, c - n * 0.085), fill=WARM + (255,), width=max(3, n // 120))
    dr.line((c, c + n * 0.085, c, c + n * 0.13), fill=WARM + (255,), width=max(3, n // 120))
    dr.line((c - n * 0.13, c, c - n * 0.085, c), fill=WARM + (255,), width=max(3, n // 120))
    dr.line((c + n * 0.085, c, c + n * 0.13, c), fill=WARM + (255,), width=max(3, n // 120))
    mask = Image.new("L", (n, n), 0)
    ImageDraw.Draw(mask).ellipse((1, 1, n - 2, n - 2), fill=255)
    img.putalpha(Image.composite(img.getchannel("A"), mask, mask))
    ImageDraw.Draw(img).ellipse((1, 1, n - 2, n - 2), outline=BORDER + (255,), width=3)
    return img.resize((d, d), Image.LANCZOS)


def _photo(data: dict) -> Image.Image:
    if not data.get("photo"):
        raise FileNotFoundError("kein Bild")
    with Image.open(data["photo"]) as im:
        im.draft("RGB", (4096, 4096))
        return im.convert("RGB")


def _fit(im: Image.Image, w: int, h: int) -> Image.Image:
    s = min(w / im.width, h / im.height)
    return im.resize((max(1, round(im.width * s)), max(1, round(im.height * s))), Image.LANCZOS)


def _labels(ph: Image.Image, labels: list[dict], u: float) -> Image.Image:
    """Dezente Objektnamen: dünner Kreis (bei ausgedehnten Objekten) bzw. kleine Marke, Name mit dunklem Saum."""
    if not labels:
        return ph
    ov = Image.new("RGBA", ph.size, (0, 0, 0, 0))
    dr = ImageDraw.Draw(ov)
    f = font(round(17 * u), 550)
    m = max(ph.size)
    taken: list[tuple] = []
    for lb in labels:
        x, y, r = lb["x"] * ph.width, lb["y"] * ph.height, lb["r"] * m
        r = r if 10 * u <= r <= 0.35 * m else 0
        w = 2 if u < 1.2 else 3
        if r:
            dr.ellipse((x - r, y - r, x + r, y + r), outline=(255, 255, 255, 120), width=w)
            ty = y + r + 6 * u
        else:
            g, s = 7 * u, 14 * u
            for ax, ay in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                dr.line((x + ax * g, y + ay * g, x + ax * s, y + ay * s), fill=(255, 255, 255, 170), width=w)
            ty = y + s + 4 * u
        tw = _tw(f, lb["name"])
        tx = min(max(8 * u, x - tw / 2), ph.width - tw - 8 * u)
        if ty + 22 * u > ph.height:
            ty = (y - r - 28 * u) if r else (y - 14 * u - 28 * u)
        box = (tx, ty, tx + tw, ty + 22 * u)
        if any(not (box[2] < b[0] or box[0] > b[2] or box[3] < b[1] or box[1] > b[3]) for b in taken):
            continue
        taken.append(box)
        dr.text((tx, ty), lb["name"], font=f, fill=(255, 255, 255, 225), stroke_width=max(2, round(2.5 * u)), stroke_fill=(0, 0, 0, 170))
    out = ph.convert("RGBA")
    out.alpha_composite(ov)
    return out.convert("RGB")


class _Col:
    """Spalte mit Textblöcken: erst messen (height), dann zeichnen (draw)."""

    def __init__(self, width: float, u: float):
        self.w, self.u, self.items = width, u, []

    def gap(self, h: float):
        self.items.append(("gap", h))

    def text(self, s: str, size: float, weight: int, color, max_lines: int = 2, lead: float = 1.25, track: bool = False):
        f = font(round(size * self.u), weight)
        ls = wrap(s.upper() if track else s, f, self.w, max_lines)
        self.items.append(("text", ls, f, color, size * lead * self.u))

    def rows(self, rows: list[tuple[str, str]]):
        self.items.append(("rows", rows))

    def find(self, f: dict):
        self.items.append(("find", f))

    def _find_h(self, f: dict) -> tuple[float, list, list]:
        u = self.u
        fv, fl = font(round(30 * u), 650), font(round(17 * u), 400)
        lv = wrap(f["value"], fv, self.w - 20 * u, 1)
        ll = wrap(f["label"], fl, self.w - 20 * u, 3)
        return 34 * u + len(ll) * 22 * u + 18 * u, lv, ll

    def height(self) -> float:
        h, u = 0.0, self.u
        for it in self.items:
            if it[0] == "gap":
                h += it[1]
            elif it[0] == "text":
                h += len(it[1]) * it[4]
            elif it[0] == "rows":
                h += len(it[1]) * 31 * u
            elif it[0] == "find":
                h += self._find_h(it[1])[0]
        return h

    def draw(self, dr: ImageDraw.ImageDraw, x: float, y: float):
        u = self.u
        for it in self.items:
            if it[0] == "gap":
                y += it[1]
            elif it[0] == "text":
                _, ls, f, col, lh = it
                for line in ls:
                    dr.text((x, y), line, font=f, fill=col)
                    y += lh
            elif it[0] == "rows":
                fk, fv = font(round(17 * u), 450), font(round(19 * u), 550)
                kw = max(_tw(fk, k) for k, _ in it[1]) + 18 * u
                kw = min(kw, self.w * 0.42)
                for k, v in it[1]:
                    dr.text((x, y + 2 * u), k, font=fk, fill=DIM)
                    vv = wrap(v, fv, self.w - kw, 1)[0]
                    dr.text((x + kw, y), vv, font=fv, fill=FG)
                    y += 31 * u
            elif it[0] == "find":
                h, lv, ll = self._find_h(it[1])
                dr.rounded_rectangle((x, y + 2 * u, x + 4 * u, y + h - 18 * u), radius=2 * u, fill=ACC)
                dr.text((x + 18 * u, y - 2 * u), lv[0], font=font(round(30 * u), 650), fill=ACC_LIGHT)
                yy = y + 36 * u
                for line in ll:
                    dr.text((x + 18 * u, yy), line, font=font(round(17 * u), 400), fill=MUT)
                    yy += 22 * u
                y += h


def _head(col: _Col, L: dict):
    big = 40 if len(wrap(L["title"], font(round(40 * col.u), 650), col.w, 3)) == 1 else 34
    col.text(L["title"], big, 650, FG, 2, 1.15)
    if L["sub"] or L["sub2"]:
        col.gap(6 * col.u)
    for t in (L["sub"], L["sub2"]):
        if t:
            col.text(t, 20, 450, MUT, 2, 1.3)
    if L["credit"]:
        col.gap(6 * col.u)
        col.text(L["credit"], 18, 550, WARM, 1)


def _section(col: _Col, title: str):
    col.text(title, 14, 700, ACC_LIGHT, 1, 1.3, track=True)
    col.gap(10 * col.u)


def _footer(img: Image.Image, dr, x: float, y: float, w: float, u: float, L: dict, version: str):
    """Fußzeile: Logo, Schriftzug, Satz, Repo-Adresse."""
    lg = round(46 * u)
    img.paste(logo(lg), (round(x), round(y)), logo(lg))
    tx = x + lg + 14 * u
    dr.text((tx, y - 1 * u), "AstroFlow", font=font(round(22 * u), 700), fill=FG)
    vx = tx + _tw(font(round(22 * u), 700), "AstroFlow") + 8 * u
    dr.text((vx, y + 5 * u), version, font=font(round(14 * u), 500), fill=DIM)
    ft = font(round(14.5 * u), 450)
    line = wrap(L["foot"], ft, w - lg - 14 * u, 1)[0]
    dr.text((tx, y + 27 * u), line, font=ft, fill=MUT)
    return tx


FOOT_H = 50


def _stage(photo: Image.Image, w: int, h: int, labels: list[dict], u: float) -> Image.Image:
    """Bildfläche w × h: das Foto unverändert eingepasst; freie Ränder füllt eine stark abgedunkelte, unscharfe Fassung
    desselben Fotos (wirkt ruhiger als schwarze Balken und verrät nichts Neues)."""
    ph = _labels(_fit(photo, w, h), labels, u)
    s = max(w / photo.width, h / photo.height)
    small = photo.resize((max(1, round(photo.width * s / 8)), max(1, round(photo.height * s / 8))), Image.BILINEAR)
    bg = small.filter(ImageFilter.GaussianBlur(6)).resize((max(w, round(photo.width * s)), max(h, round(photo.height * s))), Image.BILINEAR)
    bg = bg.crop(((bg.width - w) // 2, (bg.height - h) // 2, (bg.width - w) // 2 + w, (bg.height - h) // 2 + h))
    bg = Image.blend(Image.new("RGB", (w, h), BG), bg, 0.35)
    bg.paste(ph, ((w - ph.width) // 2, (h - ph.height) // 2))
    return bg


def _mapblock(img: Image.Image, dr, x: float, y: float, md: int, w: float, u: float, data: dict):
    sm = skymap(md, data["sky"])
    img.paste(sm, (round(x), round(y)), sm)
    cname = data["object"]["constellation"]
    tf, tl = font(round(14 * u), 700), font(round(19 * u), 550)
    tx = x + md + 18 * u
    dr.text((tx, y + md / 2 - 26 * u), "AM HIMMEL", font=tf, fill=ACC_LIGHT)
    for i, line in enumerate(wrap(f"Sternbild {cname}" if cname else "Position", tl, w - md - 18 * u, 2)):
        dr.text((tx, y + md / 2 - 2 * u + i * 24 * u), line, font=tl, fill=FG)


def render(data: dict, o: dict) -> Image.Image:
    """Karte als RGB-Bild (Querformat: Info rechts; Hochformat 4:5: Info unten)."""
    L = lines(data, o)
    photo = _photo(data)
    size = o["size"]
    show_map = bool("map" in o["parts"] and data.get("sky"))
    labels = data["labels"] if "labels" in o["parts"] else []
    if o["layout"] == "quer":
        W = size
        u = W / 1600
        P = round(W * 0.315)
        pad = 44 * u
        cw = P - 2 * pad
        col = _Col(cw, u)
        _head(col, L)
        if L["shot"]:
            col.gap(30 * u)
            _section(col, "Aufnahme")
            col.rows(L["shot"])
        if L["finds"]:
            col.gap(26 * u)
            _section(col, "Was AstroFlow herausgefunden hat")
            for f in L["finds"]:
                col.find(f)
        md = round(118 * u) if show_map else 0
        need = lambda: pad + col.height() + (md + 30 * u if md else 0) + 24 * u + FOOT_H * u + pad
        natural = (W - P) * photo.height / photo.width
        H = math.ceil(min(max(natural, need(), W * 0.5), W * 0.75))
        # Passt der Inhalt nicht (höchstens 1600 × 1200 für Foren): erst die Himmelskarte, dann Highlights von hinten weglassen
        while need() > H + 0.5 and (md or any(i[0] == "find" for i in col.items)):
            if md:
                md = 0
            else:
                col.items.pop(max(i for i, it in enumerate(col.items) if it[0] == "find"))
        img = Image.new("RGB", (W, H), PANEL)
        img.paste(_stage(photo, W - P, H, labels, u), (0, 0))
        dr = ImageDraw.Draw(img)
        dr.line((W - P, 0, W - P, H), fill=BORDER, width=max(1, round(u)))
        x0 = W - P + pad
        col.draw(dr, x0, pad)
        fy = H - pad - FOOT_H * u
        if md:
            _mapblock(img, dr, x0, fy - 30 * u - md, md, cw, u, data)
        dr.line((x0, fy - 22 * u, W - pad, fy - 22 * u), fill=BORDER, width=max(1, round(u)))
        _footer(img, dr, x0, fy, cw, u, L, data["version"])
        ft = font(round(14.5 * u), 500)
        dr.text((x0 + round(46 * u) + 14 * u, fy + 47 * u), REPO, font=ft, fill=ACC_LIGHT)
        return img
    # Hochformat 4:5 (Instagram & Co.; Foren nehmen es ebenfalls)
    H = size
    W = round(size * 0.8)
    u = W / 1280
    pad = 52 * u
    gap = 44 * u
    cw = (W - 2 * pad - gap) / 2
    left, right = _Col(cw, u), _Col(cw, u)
    _head(left, L)
    if L["shot"]:
        left.gap(26 * u)
        _section(left, "Aufnahme")
        left.rows(L["shot"])
    if L["finds"]:
        _section(right, "Was AstroFlow herausgefunden hat")
        for f in L["finds"]:
            right.find(f)
    md = round(112 * u) if show_map else 0
    lh, rh = left.height(), right.height()
    map_in_right = bool(md) and (rh + md + 24 * u <= lh + 10 * u or not L["finds"])
    lh_tot = lh + (md + 24 * u if md and not map_in_right else 0)
    rh_tot = rh + (md + 24 * u if map_in_right else 0)
    info_h = pad + max(lh_tot, rh_tot) + 34 * u + FOOT_H * u + pad * 0.9
    photo_h = round(H - info_h)
    img = Image.new("RGB", (W, H), PANEL)
    img.paste(_stage(photo, W, photo_h, labels, u), (0, 0))
    dr = ImageDraw.Draw(img)
    dr.line((0, photo_h, W, photo_h), fill=BORDER, width=max(1, round(u)))
    y0 = photo_h + pad
    left.draw(dr, pad, y0)
    right.draw(dr, pad + cw + gap, y0)
    if md:
        mx, my = (pad + cw + gap, y0 + rh + 24 * u) if map_in_right else (pad, y0 + lh + 24 * u)
        _mapblock(img, dr, mx, my, md, cw, u, data)
    fy = H - pad * 0.9 - FOOT_H * u
    dr.line((pad, fy - 22 * u, W - pad, fy - 22 * u), fill=BORDER, width=max(1, round(u)))
    _footer(img, dr, pad, fy, W - 2 * pad, u, L, data["version"])
    ft = font(round(15 * u), 500)
    dr.text((W - pad - _tw(ft, REPO), fy + 4 * u), REPO, font=ft, fill=ACC_LIGHT)
    return img


def jpeg(img: Image.Image, limit: int) -> bytes:
    """JPEG ohne Metadaten (kein EXIF, kein Kommentar), Qualität so hoch wie die Größengrenze erlaubt."""
    data = b""
    for q in (92, 89, 86, 83, 80, 76, 72, 68, 62, 56):
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=q, optimize=True, progressive=True, subsampling=0 if q >= 86 else 2)
        data = buf.getvalue()
        if len(data) <= limit:
            break
    return data


def card(data: dict, o: dict) -> bytes:
    return jpeg(render(data, o), SIZES[o["size"]])


def filename(data: dict, o: dict) -> str:
    return f"astroflow-{slug(data['object']['name'])}-{o['layout']}.jpg"


def info(data: dict, o: dict) -> dict:
    """Antwort für den Dialog: verfügbare Abschnitte/Highlights, Voreinstellung und der Forentext."""
    return {"name": data["object"]["name"], "imported": data["imported"], "has_photo": bool(data["photo"]),
            "has_map": bool(data.get("sky")), "has_labels": bool(data["labels"]), "has_shot": any(lines(data, {**o, "parts": set(PARTS)})["shot"]),
            "finds": [{"key": f["key"], "value": f["value"], "label": f["label"]} for f in data["finds"]],
            "defaults": {"layout": o["layout"], "size": o["size"], "finds": o["finds"], "parts": sorted(o["parts"])},
            "sizes": list(SIZES), "filename": filename(data, o),
            "text": {"bbcode": text(data, o, "bbcode"), "markdown": text(data, o, "markdown")}}
