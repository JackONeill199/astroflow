"""Interaktive Bildführung (Wunsch 19, Stufe 1): geführte Tour durch das eigene Bild und „Was ist an dieser Stelle?“.

Grundlage ist das Plate-Solving, keine Bilderkennung: Die Bildbestimmung liefert eine lineare Gnomonische Projektion in
Anzeige-Pixeln (`solution.tan`, aus `stacking/services.display_tan`). Fehlt sie (ältere Einträge, nova), wird sie aus der
gespeicherten Stack-Lösung (Dienst `project_solution`) neu geholt oder aus den Objektmarken mit Katalogkoordinaten
angepasst. Antworten stammen nur aus Katalogen (Bildmarken, AstroFlow-Katalog, Enzyklopädie, SIMBAD mit Cache) –
nichts wird erfunden; SIMBAD-Treffer bekommen eine Quelle [S<id>] wie im Chat."""
from __future__ import annotations

import math
import re
import time
import traceback

from . import clients
from .kb import norm
from .sources import SourceError, otype_label

ZOOM_MAX = 6.0


# ------------------------------------------------------------------ Projektion -- #
def _std(ra0: float, dec0: float, ra: float, dec: float) -> tuple[float, float]:
    """Himmelsort → Standardkoordinaten (Grad) um (ra0, dec0)."""
    r0, d0, r, d = map(math.radians, (ra0, dec0, ra, dec))
    c = math.sin(d0) * math.sin(d) + math.cos(d0) * math.cos(d) * math.cos(r - r0)
    if c <= 0:
        raise ValueError("Punkt auf der abgewandten Himmelshälfte")
    return (math.degrees(math.cos(d) * math.sin(r - r0) / c),
            math.degrees((math.cos(d0) * math.sin(d) - math.sin(d0) * math.cos(d) * math.cos(r - r0)) / c))


def _sky(ra0: float, dec0: float, xi: float, eta: float) -> tuple[float, float]:
    """Standardkoordinaten (Grad) → Himmelsort."""
    x, y, r0, d0 = math.radians(xi), math.radians(eta), math.radians(ra0), math.radians(dec0)
    den = math.cos(d0) - y * math.sin(d0)
    ra = r0 + math.atan2(x, den)
    dec = math.atan2(math.sin(d0) + y * math.cos(d0), math.hypot(x, den))
    return math.degrees(ra) % 360, math.degrees(dec)


def pix2sky(tan: dict, x: float, y: float) -> tuple[float, float]:
    (a, b), (c, d) = tan["cd"]
    dx, dy = x - tan["crpix"][0], y - tan["crpix"][1]
    return _sky(*tan["crval"], a * dx + b * dy, c * dx + d * dy)


def sky2pix(tan: dict, ra: float, dec: float) -> tuple[float, float] | None:
    (a, b), (c, d) = tan["cd"]
    det = a * d - b * c
    try:
        xi, eta = _std(*tan["crval"], ra, dec)
    except ValueError:
        return None
    if not det:
        return None
    return tan["crpix"][0] + (d * xi - b * eta) / det, tan["crpix"][1] + (-c * xi + a * eta) / det


def scale_arcsec(tan: dict) -> float:
    (a, b), (c, d) = tan["cd"]
    return math.sqrt(abs(a * d - b * c)) * 3600


def fit_tan(points: list[tuple[float, float, float, float]], w: float, h: float) -> dict | None:
    """Projektion aus ≥ 3 Objektmarken (x, y, ra, dec) anpassen – Rückfall für Einträge ohne gespeicherte Lösung."""
    import numpy as np
    if len(points) < 3:
        return None
    cx, cy = w / 2 - 0.5, h / 2 - 0.5
    ra0, dec0 = points[0][2], points[0][3]
    for _ in range(3):                                   # Bezugspunkt zur Bildmitte wandern lassen
        try:
            xy = np.array([[p[0] - cx, p[1] - cy, 1.0] for p in points])
            st = np.array([_std(ra0, dec0, p[2], p[3]) for p in points])
        except ValueError:
            return None
        if np.linalg.matrix_rank(xy[:, :2] - xy[:, :2].mean(0)) < 2:
            return None                                  # alle Marken auf einer Linie
        sol, *_ = np.linalg.lstsq(xy, st, rcond=None)
        ra0, dec0 = _sky(ra0, dec0, sol[2, 0], sol[2, 1])
    tan = {"crpix": [cx, cy], "crval": [ra0, dec0], "cd": [[float(sol[0, 0]), float(sol[1, 0])], [float(sol[0, 1]), float(sol[1, 1])]]}
    # Probe: Abweichung der Marken in Pixeln – große Objekte haben ungenaue Mitten, darum großzügig (5 % der Bildgröße)
    err = max(math.hypot(*(np.subtract(sky2pix(tan, p[2], p[3]) or (1e9, 1e9), p[:2]))) for p in points)
    return tan if err < 0.05 * max(w, h) and scale_arcsec(tan) > 0 else None


def _coords(mod, name: str) -> tuple[float, float] | None:
    e = mod.kb.resolve(name)
    if e and e.get("ra") is not None:
        return e["ra"], e["dec"]
    look = mod.ctx.service("catalog_resolve")
    try:
        c = look(name) if look else None
    except Exception:
        c = None
    return (c["ra"], c["dec"]) if c else None


def tan_for(mod, entry: dict) -> dict | None:
    """Projektion eines Verlaufseintrags: gespeichert → Stack-Lösung (einmal nachgeholt und gemerkt) → aus Objektmarken."""
    sol = entry.get("solution") or {}
    if sol.get("tan"):
        return sol["tan"]
    src = entry.get("source") or {}
    tan = None
    get = mod.ctx.service("project_solution")
    if src.get("type") == "project" and get:
        try:
            fresh = get(src["project"])
            tan = (fresh or {}).get("tan")
        except Exception:
            traceback.print_exc()
    if not tan:
        pts = []
        for o in entry.get("objects") or []:
            if o.get("x") is None:
                continue
            c = _coords(mod, o["name"])
            if c:
                pts.append((o["x"], o["y"], *c))
        tan = fit_tan(pts, entry.get("width") or sol.get("width") or 0, entry.get("height") or sol.get("height") or 0)
    if tan:
        mod.store.update(entry["id"], solution={**sol, "tan": tan})
    return tan


# ---------------------------------------------------------------- Geführte Tour -- #
def tour_steps(mod, story: dict) -> dict:
    """Erzählung → Schritte {text, object, x, y, r, zoom, mark, entity} in Bildpixeln des Eintrags (wie die Objektmarken).
    Einleitung/Ausklang zeigen das ganze Bild. Zoom ist relativ zur eingepassten Ansicht (createViewer.zoomTo)."""
    img = story.get("image") or {}
    h = mod.store.get(img.get("entry")) if img.get("entry") else None
    if not h:
        raise clients.ServiceError("Das Bild zu dieser Führung gibt es nicht mehr.")
    W, H = h.get("width") or 0, h.get("height") or 0
    objs = {o["name"]: o for o in h.get("objects") or [] if o.get("x") is not None}
    short = max(1.0, min(W, H)) if W and H else 1.0
    out = []
    for seg in story.get("segments") or []:
        text = re.sub(r"\s*\[S\d+\]", "", str(seg.get("text") or "")).strip()
        o = objs.get(seg.get("object"))
        step = {"text": text, "object": seg.get("object") if o else None, "x": W / 2, "y": H / 2, "r": None, "zoom": 1.0, "mark": None, "entity": None}
        if o:
            r = max(8.0, float(o.get("radius") or 8))
            frac = r / short
            e = mod.kb.resolve(o["name"])
            step.update(x=o["x"], y=o["y"], r=r, entity=e and e["id"], kind=o.get("kind_label") or (e or {}).get("otype_label") or "",
                        zoom=round(min(ZOOM_MAX, max(1.0, short / (r * 5))), 2),
                        # klein → Pfeil (Kreis wäre kaum zu sehen), mittel → Kreis, fast bildfüllend → gestrichelter Umriss
                        mark="arrow" if frac < 0.02 else "outline" if frac > 0.3 else "circle")
        out.append(step)
    return {"entry": h["id"], "width": W, "height": H, "steps": out, "has_tan": bool((h.get("solution") or {}).get("tan"))}


# ----------------------------------------------------------- Fragen per Klick -- #
def sexa(v: float, hours: bool) -> str:
    x = (v / 15) if hours else abs(v)
    a = int(x)
    m = (x - a) * 60
    if hours:
        return f"{a:02d}h {int(m):02d}m {int(round((m - int(m)) * 60)) % 60:02d}s"
    return f"{'−' if v < 0 else '+'}{a:02d}° {int(m):02d}′ {int(round((m - int(m)) * 60)) % 60:02d}″"


def _arc(sec: float) -> str:
    return f"{sec:.0f}″" if sec < 90 else f"{sec / 60:.1f}′".replace(".", ",")


def around(mod, entry: dict, x: float, y: float, r_px: float = 20.0) -> dict:
    """Stelle (x, y) in Bildpixeln des Eintrags → Himmelsort → Katalogobjekte in der Umgebung."""
    from .research import Researcher
    tan = tan_for(mod, entry)
    if not tan:
        raise clients.ServiceError("Für dieses Bild ist keine Himmelsposition gespeichert – erst „Bild bestimmen“ (Plate-Solving).")
    ra, dec = pix2sky(tan, x, y)
    sc = scale_arcsec(tan)
    rad = min(900.0, max(20.0, r_px * sc))                       # Suchradius in Bogensekunden (20″ … 15′)
    hits: dict[str, dict] = {}

    def add(name, kind, sep, origin, **extra):
        k = norm(name)
        if not k:
            return
        cur = hits.get(k)
        if cur is None or (cur["origin"] != "simbad" and origin == "simbad" and extra.get("source")):
            hits[k] = {**(cur or {}), "name": name, "kind": kind or (cur or {}).get("kind") or "", "sep": sep, "origin": origin, **extra}

    # 1. Objektmarken im Bild: Stelle liegt im Umriss oder nahe dabei
    for o in entry.get("objects") or []:
        if o.get("x") is None:
            continue
        d = math.hypot(o["x"] - x, o["y"] - y)
        if d <= max(float(o.get("radius") or 8), r_px):
            add(o["name"], o.get("kind_label"), d * sc, "bild", inside=d <= float(o.get("radius") or 8), x=o["x"], y=o["y"])
    # 2. Enzyklopädie (Einträge mit Koordinaten, auch ohne Bildmarke)
    box = rad / 3600 + 1.0
    for e in mod.kb.q("SELECT id, name, otype_label, ra, dec, size FROM entities WHERE ra IS NOT NULL AND dec BETWEEN ? AND ?",
                      (dec - box, dec + box)):
        try:
            p = _std(ra, dec, e["ra"], e["dec"])
        except ValueError:
            continue
        sep = math.hypot(*p) * 3600
        if sep <= max(rad, (e["size"] or 0) * 30):                 # size in Bogenminuten → halber Durchmesser in ″
            add(e["name"], e["otype_label"], sep, "enzyklopaedie", entity=e["id"], inside=sep <= (e["size"] or 0) * 30)
    # 3. SIMBAD-Umkreissuche (über den Antwort-Cache der Wissensbasis)
    note = None
    r = Researcher(mod)
    try:
        for s in r.simbad.around(ra, dec, rad / 3600, limit=12):
            mid = " ".join(str(s["main_id"]).split())
            sid = mod.kb.add_source(f"simbad:{s['main_id']}", "catalog", f"SIMBAD – {mid}", url=r.simbad.source_url(s["main_id"]))
            add(mid, otype_label(s.get("otype"))[0], float(s.get("sep") or 0) * 3600, "simbad", source=sid,
                mag=s.get("V"), simbad_type=(s.get("otype") or "").strip(), ra=s.get("ra"), dec=s.get("dec"))
    except SourceError:
        note = "SIMBAD wurde nicht abgefragt (Online-Quellen aus oder nicht erreichbar) – nur lokale Kataloge."
    except Exception:
        traceback.print_exc()
        note = "SIMBAD-Abfrage fehlgeschlagen – nur lokale Kataloge."
    items = sorted(hits.values(), key=lambda h: (not h.get("inside"), h["sep"]))[:12]
    for h in items:                                                # Position im Bild für die Markierung
        if h.get("x") is None and h.get("ra") is not None:
            p = sky2pix(tan, h["ra"], h["dec"])
            if p:
                h["x"], h["y"] = round(p[0], 1), round(p[1], 1)
        if h.get("entity") is None:
            e = mod.kb.resolve(h["name"])
            h["entity"] = e and e["id"]
        h["sep"] = round(h["sep"], 1)
    return {"ra": round(ra, 6), "dec": round(dec, 6), "x": x, "y": y, "radius_arcsec": round(rad, 1), "scale": round(sc, 3),
            "hits": items, "note": note}


ORIGIN = {"bild": "Bildmarke aus dem Plate-Solving", "enzyklopaedie": "Enzyklopädie", "simbad": "SIMBAD"}


def answer_text(res: dict) -> str:
    """Antwort für den Chat – nur Katalogangaben, SIMBAD mit Beleg."""
    head = f"**Stelle im Bild:** RA {sexa(res['ra'], True)}, Dec {sexa(res['dec'], False)} (Umkreis {_arc(res['radius_arcsec'])})"
    inside = [h for h in res["hits"] if h.get("inside")]
    near = [h for h in res["hits"] if not h.get("inside")]
    lines = [head, ""]
    if inside:
        lines.append("Die Stelle liegt innerhalb von:")
        lines += [f"- **{h['name']}**" + (f" – {h['kind']}" if h.get("kind") else "") + f" ({ORIGIN[h['origin']]})"
                  + (f" [S{h['source']}]" if h.get("source") else "") for h in inside]
        lines.append("")
    if near:
        lines.append("Katalogobjekte in der Nähe (nächstes zuerst):")
        for h in near:
            t = f"- **{h['name']}**" + (f" – {h['kind']}" if h.get("kind") else "") + f", {_arc(h['sep'])} entfernt"
            if h.get("mag") is not None:
                t += f", V = {float(h['mag']):.1f} mag".replace(".", ",")
            t += f" [S{h['source']}]" if h.get("source") else f" ({ORIGIN[h['origin']]})"
            lines.append(t)
    if not res["hits"]:
        lines.append("In diesem Umkreis verzeichnen die abgefragten Kataloge kein Objekt. Was hier zu sehen ist, hat also "
                     "keinen eigenen Katalogeintrag (oder ist sehr lichtschwach) – eine Deutung wäre nur eine Vermutung.")
    if res.get("note"):
        lines += ["", f"*{res['note']}*"]
    return "\n".join(lines).strip()


def ask_spot(mod, entry: dict, x: float, y: float, r_px: float = 20.0) -> dict:
    """Frage und Antwort in den Sage-Chat des Bildes schreiben (Scope `image:<id>`), damit Folgefragen daran anknüpfen."""
    res = around(mod, entry, x, y, r_px)
    kb = mod.kb
    scope = f"image:{entry['id']}"
    q = f"Was ist an dieser Stelle im Bild? (RA {sexa(res['ra'], True)}, Dec {sexa(res['dec'], False)})"
    text = answer_text(res)
    cited = sorted({h["source"] for h in res["hits"] if h.get("source")})
    srcs = [{"id": s["id"], "title": s["title"], "kind": s["kind"], "url": s["url"], "year": s["year"]} for s in kb.sources(cited)]
    data = {"steps": [{"tool": "Umkreissuche", "args": {"ra": res["ra"], "dec": res["dec"], "radius": f"{res['radius_arcsec']}″"},
                       "summary": f"{len(res['hits'])} Treffer", "ok": True}],
            "sources": srcs, "spot": {"x": x, "y": y, "ra": res["ra"], "dec": res["dec"]}, "model": "Kataloge (ohne KI)"}
    now = time.time()
    kb.x("INSERT INTO chats(scope, role, content, created) VALUES(?,?,?,?)", (scope, "user", q, now))
    kb.x("INSERT INTO chats(scope, role, content, data, created) VALUES(?,?,?,?,?)", (scope, "assistant", text, kb._j(data), now))
    return {**res, "scope": scope, "question": q, "answer": text, "sources": srcs}


__all__ = ["pix2sky", "sky2pix", "fit_tan", "tan_for", "tour_steps", "around", "ask_spot", "answer_text"]
