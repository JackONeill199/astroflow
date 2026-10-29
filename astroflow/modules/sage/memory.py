"""Persönliches astronomisches Gedächtnis: eigene Aufnahmen (Bilder-Modul), Bildbestimmungen (Sage-Verlauf), Notizen,
Pläne und Interessen – je Objekt verknüpft. Abgeleitete Aussagen entstehen nur aus vorhandenen Daten und tragen ihre
Grundlage mit („Du hast M45 dreimal aufgenommen …“)."""
from __future__ import annotations

import time

from .kb import norm
from .research import angular_sep


def de(x: float, nd: int = 1) -> str:
    return f"{x:.{nd}f}".replace(".", ",")


def _match(kb, name: str, ra=None, dec=None, radius_deg: float = 0.25) -> dict | None:
    e = kb.resolve(name)
    if e or ra is None:
        return e
    # gleiche Himmelsstelle (z. B. Projekt „SH2-101“, Eintrag „Sh2-101 Tulpennebel“)
    near = kb.q("SELECT * FROM entities WHERE kind='object' AND ra IS NOT NULL AND ABS(dec-?)<?", (dec, radius_deg + 0.5))
    near = [x for x in near if angular_sep(ra, dec, x["ra"], x["dec"]) < max(radius_deg, (x["size"] or 0) / 120)]
    return min(near, key=lambda x: angular_sep(ra, dec, x["ra"], x["dec"])) if near else None


def observation_log(mod, ttl: float = 20) -> list[dict]:
    """Aufnahmeprotokoll des Bilder-Moduls – kurz zwischengespeichert (liest alle Frames; eine Seite fragt es mehrfach ab)."""
    hit = getattr(mod, "_obs_cache", None)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    get = mod.ctx.service("observation_log")
    try:
        log = get() if get else []
    except Exception:
        log = []
    mod._obs_cache = (time.time(), log)
    return log


def for_entity(mod, eid: str, log: list[dict] | None = None) -> dict:
    """Alle eigenen Daten zu einem Objekt + daraus abgeleitete Aussagen (jeweils mit Grundlage)."""
    kb = mod.kb
    e = kb.entity(eid)
    keys = {norm(a) for a in kb.aliases(eid)} | {norm(e["name"])}
    projects = []
    for p in observation_log(mod) if log is None else log:
        hit = _match(kb, p["name"], p.get("ra"), p.get("dec"))
        if (hit and hit["id"] == eid) or norm(p["name"]) in keys:
            projects.append(p)
    images = []
    for h in mod.store.entries():
        names = [o["name"] for o in h.get("objects") or []]
        if any((kb.resolve(n) or {}).get("id") == eid or norm(n) in keys for n in names):
            o = next(o for o in h.get("objects") or [] if (kb.resolve(o["name"]) or {}).get("id") == eid or norm(o["name"]) in keys)
            images.append({"id": h["id"], "title": h.get("title"), "created": h["created"], "image": h.get("preview_file"),
                           "source": h.get("source"), "has_story": bool(h.get("story")), "object": o, "n_objects": len(names),
                           "others": [n for n in names if n != o["name"]][:12], "width": h.get("width"), "height": h.get("height")})
    return {"projects": projects, "images": images, "notes": kb.notes(eid), "derived": derive(e, projects, images)}


def derive(e: dict, projects: list[dict], images: list[dict]) -> list[dict]:
    """Aussagen aus echten Daten – jede mit `basis` (woraus sie berechnet wurde)."""
    out = []
    sessions = sorted([s for p in projects for s in p["sessions"] if s["frames"]], key=lambda s: s["night"] or "")
    if sessions:
        n = len(sessions)
        word = {1: "einmal", 2: "zweimal", 3: "dreimal", 4: "viermal", 5: "fünfmal"}.get(n, f"{n}-mal")
        tot = sum(s["used_s"] or s["exposure_s"] for s in sessions)
        amount = f"{de(tot / 3600)} h" if tot >= 5400 else f"{tot / 60:.0f} min"
        out.append({"text": f"Du hast {e['name']} bisher {word} aufgenommen ({n} Nacht{'' if n == 1 else 'e'}, insgesamt {amount} verwendete Belichtung).",
                    "basis": f"{sum(s['frames'] for s in sessions)} Einzelbilder in der Bilder-Datenbank"})
        last = sessions[-1]
        out.append({"text": f"Deine letzte Session ({last['night']}) hatte eine Gesamtbelichtungszeit von {(last['used_s'] or last['exposure_s']) / 60:.0f} Minuten"
                            f" – {last['used']} von {last['frames']} Bildern wurden gestackt.",
                    "basis": "Frames der Nacht " + (last["night"] or "?") + (f", Instrument {', '.join(last['instruments'])}" if last["instruments"] else "")})
        fw = [s for s in sessions if s.get("fwhm_px")]
        if len(fw) >= 2:
            best = min(fw, key=lambda s: s["fwhm_px"])
            out.append({"text": f"Die schärfste Nacht war {best['night']} (Sternbreite FWHM {de(best['fwhm_px'])} px, Median "
                                f"aller Nächte {de(sorted(s['fwhm_px'] for s in fw)[len(fw) // 2])} px).",
                        "basis": "gemessene Sternbreiten der Einzelbilder"})
        inst = sorted({i for s in sessions for i in s["instruments"]})
        filt = sorted({f for s in sessions for f in s["filters"]})
        if inst:
            out.append({"text": f"Aufgenommen mit {', '.join(inst)}" + (f", Filter {', '.join(filt)}" if filt else "") + ".",
                        "basis": "FITS-Header INSTRUME/FILTER"})
    res = [r for p in projects for r in p["results"]]
    snr = [r for r in res if r.get("snr")]
    if len(snr) >= 2 and snr[-1]["snr"] != snr[0]["snr"]:
        a, b = snr[0], snr[-1]
        better = b["snr"] > a["snr"]
        out.append({"text": f"Gegenüber deiner ersten Version ist das gemessene Objekt-Signal-Rausch-Verhältnis von {a['snr']:.0f} auf "
                            f"{b['snr']:.0f} {'gestiegen' if better else 'gesunken'}"
                            + (f" (Belichtung {a['exposure_s'] / 60:.0f} → {b['exposure_s'] / 60:.0f} min)" if a.get("exposure_s") and b.get("exposure_s") else "")
                            + (" – schwache Strukturen sollten deutlicher sichtbar sein." if better and b["snr"] / a["snr"] > 1.2 else "."),
                    "basis": f"Bildqualitätsmessung der Versionen {a['id']} und {b['id']}", "kind": "interpretation" if better else "fact"})
    if images:
        out.append({"text": f"In {len(images)} deiner bestimmten Bilder taucht {e['name']} auf"
                            + (f", zusammen mit {', '.join(sorted({o for i in images for o in i['others']})[:5])}" if any(i["others"] for i in images) else "") + ".",
                    "basis": "Sage-Bildbestimmungen (Plate-Solving + Katalog)"})
    return out


def profile(mod) -> dict:
    """Überblick über alle eigenen Beobachtungen – für Assistent, Vorschläge und das Wissensnetz."""
    kb, log = mod.kb, observation_log(mod)
    objs, kinds, inst = {}, {}, {}
    for p in log:
        e = _match(kb, p["name"], p.get("ra"), p.get("dec"))
        used = sum(s["used_s"] for s in p["sessions"])
        if not p["sessions"]:
            continue
        key = e["id"] if e else f"projekt-{p['id']}"
        o = objs.setdefault(key, {"entity": e and e["id"], "name": e["name"] if e else p["name"], "nights": 0, "hours": 0.0,
                                  "last": None, "kind": (e and (e["data"] or {}).get("af_kind")) or p.get("kind")})
        o["nights"] += len(p["sessions"])
        o["hours"] += used / 3600
        o["last"] = max(o["last"] or "", p["sessions"][-1]["night"] or "")
        kinds[o["kind"] or "generic"] = kinds.get(o["kind"] or "generic", 0) + 1
        for s in p["sessions"]:
            for i in s["instruments"]:
                inst[i] = inst.get(i, 0) + 1
    for h in mod.store.entries():
        for ob in h.get("objects") or []:
            e = kb.resolve(ob["name"])
            if e and e["kind"] == "object":
                o = objs.setdefault(e["id"], {"entity": e["id"], "name": e["name"], "nights": 0, "hours": 0.0, "last": None,
                                              "kind": (e["data"] or {}).get("af_kind")})
                o["images"] = o.get("images", 0) + 1
    interests = kb.notes(kind="interest")
    return {"objects": sorted(objs.values(), key=lambda o: -(o["hours"] or 0)), "kinds": kinds, "instruments": inst,
            "interests": [n["title"] for n in interests], "updated": time.time()}
