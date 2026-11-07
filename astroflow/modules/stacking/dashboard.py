"""Analyse-Dashboard über alle Objekte (`#/analyse`, seit 0.36.0, Nutzerwunsch „eine eigene Seite, wo man alle gleichzeitig
analysieren kann, mit Statistiken und wichtigen Infos“).

Zwei Teile:
- `build` sammelt je Objekt die neueste Version mit den gespeicherten Auswertungen (Tiefe, Vergleich, Himmelsqualität, Tutor)
  und rechnet daraus Kennzahlen, Rekorde, Verläufe und Ranglisten. Nur gespeicherte Messwerte – fehlt etwas, steht es als
  Status „none/old/error“ bzw. als fehlende Voraussetzung („blocked“) in der Zeile. Teuer ist nur das Lesen der JSON-Spalten:
  `Dashboard.rows` hält das Ergebnis, bis sich Ergebnisse, Himmelsnächte, Lösungen oder Einstellungen ändern.
- `Batch` = „Alles analysieren“: arbeitet eine Liste (Art, Objekt) **nacheinander** ab. Tiefe und Vergleich laufen über die
  vorhandene Insights-Warteschlange (ein Thread), der Sammelauftrag reicht immer nur einen Auftrag weiter und wartet danach
  `GAP_S` – so fragt AstroFlow Gaia/SIMBAD/DSS/JPL nie parallel ab. Anhalten wirkt nach dem laufenden Schritt.
"""
from __future__ import annotations

import json
import math
import threading
import time
from collections import Counter

# Reihenfolge im Sammelauftrag: schnell/lokal zuerst, Vergleich, HR-Diagramm und Asteroiden nach der Tiefe (nutzen deren Nullpunkt/Grenzgröße)
KINDS = ("tutor", "sky", "depth", "compare", "hrd", "asteroids")
LABEL = {"tutor": "Tutor", "sky": "Himmel", "depth": "Tiefe", "compare": "Vergleich", "hrd": "HR-Diagramm", "asteroids": "Asteroiden"}
BLOCK = {"nosolve": "nicht per Plate-Solving gelöst", "nogaia": "Gaia-Abgleich ausgeschaltet", "noframes": "keine Einzelbild-Analysen",
         "noquality": "keine Bildprüfung gespeichert", "nosmall": "Kleinkörper-Abgleich (JPL) ausgeschaltet"}
NET_KINDS = ("depth", "compare", "hrd", "asteroids")  # HR-Diagramm nur bei Sternhaufen, sonst Status „na“ (entfällt)
GAP_S = 3.0                                          # Pause nach jeder Netzauswertung (Dienste schonen)
CACHE_S = 120.0                                      # spätestens dann neu sammeln (Nächte/Aufnahmen ändern sich selten)
MAX_CLOUD = 4000                                     # Punkte im „Universum“
CLOUD_STARS = 60                                     # Sterne je Objekt in der Wolke
LY_PER_PC = 3.26156


def _status(data: dict | None, version: int) -> str:
    if data and data.get("ok") and data.get("v") == version:
        return "done"
    if data and not data.get("ok"):
        return "error"
    return "old" if data else "none"


def _r(v, nd=2):
    return None if v is None or not math.isfinite(v) else round(float(v), nd)


def _depth_info(d: dict, exposure: float | None) -> dict:
    g, e = d.get("grenze") or {}, d.get("entfernung") or {}
    far, gal, main = e.get("fernster"), d.get("fernstes"), d.get("hauptobjekt")
    m5 = g.get("m5")
    hours = (exposure or 0) / 3600
    return {"m5": m5, "lim50": g.get("lim50"), "erfasst": g.get("erfasst"), "katalog": g.get("katalog"),
            # Grenzgröße auf 1 h Belichtung umgerechnet (Rauschen ∝ 1/√t → 1,25·log10 t): vergleicht Nächte/Geräte fair
            "m5_1h": _r(m5 - 1.25 * math.log10(hours), 2) if m5 is not None and hours > 0.01 else None,
            "far_star": {k: far.get(k) for k in ("id", "ly", "g", "x", "y")} if far else None,
            "near_star": {k: e["naechster"].get(k) for k in ("id", "ly", "g", "x", "y")} if e.get("naechster") else None,
            "far_gal": {k: gal.get(k) for k in ("name", "z", "ly", "art", "x", "y")} if gal else None,
            "main": {"name": main.get("name"), "ly": main.get("ly")} if main and main.get("ly") else None,
            "galaxies": d.get("galaxien_sichtbar") or 0, "stars_dist": e.get("mit_parallaxe") or 0,
            "bild": d.get("bild")}


def _hrd_info(h: dict) -> dict:
    hf, a, d = h.get("haufen") or {}, h.get("alter") or {}, h.get("entfernung") or {}
    return {"members": hf.get("n_gemessen"), "found": bool(hf.get("erkannt")), "age": a.get("jahre"), "age_lo": a.get("lo"),
            "age_hi": a.get("hi"), "pc": d.get("pc"), "globular": bool(hf.get("kugel"))}


def _compare_info(c: dict) -> dict:
    cands = c.get("kandidaten") or []
    return {"ref": (c.get("ref") or {}).get("art"), "ref_label": (c.get("ref") or {}).get("label") or (c.get("ref") or {}).get("name"),
            "n": len(cands), "counts": dict(Counter(k["art"] for k in cands)),
            "variables": sum(1 for k in cands if (k.get("katalog") or {}).get("veraenderlich")),
            "catalog": sum(1 for k in cands if k.get("katalog")), "small": sum(1 for k in cands if k.get("kleinkoerper")),
            "bild": c.get("bild")}


def _asteroid_info(a: dict) -> dict:
    found = [b for b in a.get("kleinkoerper") or [] if b.get("status") == "nachgewiesen"]
    return {"n": a.get("nachgewiesen") or 0, "in_field": a.get("im_feld") or 0, "faintest": a.get("schwaechster"), "bild": a.get("bild"),
            "items": [{k: b.get(k) for k in ("name", "art", "mag", "vmag", "rate", "night", "x", "y", "snr")} for b in found]}          # vollständig: summarize zählt und sucht den Rekord, gekürzt wird erst die Ausgabe


def _cloud(pid: int, d: dict) -> list[dict]:
    """Punkte eines Bildes am Himmel: Feldmitte + Lage im Bild (genähert, Norden oben), Entfernung in Lichtjahren."""
    f, b = d.get("feld"), d.get("bild")
    if not f or not b or not b.get("w"):
        return []
    half = math.hypot(b["w"], b["h"]) / 2
    cosd = max(0.05, math.cos(math.radians(f["dec"])))
    out, stars = [], [p for p in d.get("punkte") or [] if p.get("t") == "s"]
    stars = stars[:: max(1, len(stars) // CLOUD_STARS)][:CLOUD_STARS]
    for p in stars + [p for p in d.get("punkte") or [] if p.get("t") == "g"]:
        if p.get("x") is None or not p.get("ly"):
            continue
        dx, dy = (p["x"] - b["w"] / 2) / half * f["radius"], (b["h"] / 2 - p["y"]) / half * f["radius"]
        out.append({"p": pid, "t": p["t"], "ra": round(f["ra"] - dx / cosd, 4), "dec": round(f["dec"] + dy, 4), "ly": p["ly"],
                    "g": p.get("g"), "c": p.get("bp_rp"), "z": p.get("z"), "n": p.get("n") if p["t"] == "g" else None})
    return out


def collect(eng) -> dict:
    """Je Objekt die neueste Version mit Status und Kennzahlen der Auswertungen (ohne Live-Zustand der Warteschlangen)."""
    from . import deepsky, objects, skyquality, tutor
    from .asteroids import ASTEROID_VERSION
    from .compare import COMPARE_VERSION
    from .depth import DEPTH_VERSION
    from .hrd import HRD_VERSION, is_cluster
    db, wd = eng.db, eng.s.work_dir
    skyquality.ensure_table(db)
    gaia, solver = bool(eng.s.gaia), bool(eng.pl.solver_cfg.enabled)
    small = bool(eng.insights.settings().get("small_bodies"))
    projs = {p["id"]: p for p in db.q("SELECT id, name, kind, obj_id, options FROM projects")}
    newest = db.q("SELECT * FROM (SELECT id rid, project_id, created, exposure, n_frames, stats, ROW_NUMBER() OVER (PARTITION BY project_id "
                  "ORDER BY COALESCE(json_extract(stats,'$.imported.series.sequence'),0) DESC, id DESC) rn FROM results) WHERE rn=1")
    frames: dict[int, dict] = {}
    for x in db.q("SELECT project_id, night, json_extract(info,'$.instrument') ins, json_extract(info,'$.shape') shape, "
                  "COUNT(*) n, SUM(metrics IS NOT NULL) m FROM frames WHERE kind='light' GROUP BY project_id, night, ins"):
        f = frames.setdefault(x["project_id"], {"nights": set(), "ins": Counter(), "shape": None, "metrics": 0})
        if x["night"]:
            f["nights"].add(x["night"])
        f["ins"][x["ins"] or ""] += x["n"]
        f["metrics"] += x["m"] or 0
        if x["shape"] and not f["shape"]:
            try:
                f["shape"] = tuple(json.loads(x["shape"]))
            except (ValueError, TypeError):
                pass
    sky_rows: dict[int, list] = {}
    for n in skyquality.nights_of(db):
        n.pop("dirs", None)
        n.pop("points", None)
        sky_rows.setdefault(n["project_id"], []).append(n)
    sky_ver = {x["project_id"]: x["v"] for x in db.q("SELECT project_id, MIN(version) v FROM sky_nights GROUP BY project_id")}
    fixed = deepsky.fixed()
    rows, cloud, imported, devices = [], [], 0, {}
    for r in newest:
        p = projs.get(r["project_id"])
        if not p:
            continue
        opts = json.loads(p["options"] or "{}")
        if opts.get("gallery_parent"):
            continue
        pid, st = p["id"], json.loads(r["stats"] or "{}")
        keys = deepsky.keys_for(p["obj_id"]) | deepsky.keys_for(p["name"])
        cat = sorted({f"{t.source[0].upper()}{t.number}" for t in fixed if t.number and t.source in ("messier", "caldwell") and t.keys & keys})
        if opts.get("input_type") == "picture":       # fertig importierte Bilder: keine Analyse möglich, zählen nur für Kataloge
            imported += 1
            rows.append({"id": pid, "name": p["name"], "imported": True, "catalog": cat, "rid": r["rid"]})
            continue
        fr = frames.get(pid, {"nights": set(), "ins": Counter(), "shape": None, "metrics": 0})
        ins = [i for i, _ in fr["ins"].most_common()]
        dev = tutor.detect_device(ins, fr["shape"])
        devices[dev["name"]] = dev["id"]
        nights = sorted(st.get("nights") or fr["nights"])
        solved = (wd / f"p{pid}" / "wcs.json").exists()
        quality = st.get("quality") or {}
        # Status je Art
        status = {"depth": _status(st.get("depth"), DEPTH_VERSION), "compare": _status(st.get("compare"), COMPARE_VERSION),
                  "asteroids": _status(st.get("asteroids"), ASTEROID_VERSION)}
        tc = st.get("tutor") or {}
        status["tutor"] = "done" if str(tc.get("key", "")).startswith(f"{tutor.TUTOR_VERSION}|") else "old" if tc else "none"
        status["sky"] = "none" if pid not in sky_ver else "done" if sky_ver[pid] >= skyquality.SKY_VERSION else "old"
        status["hrd"] = _status(st.get("hrd"), HRD_VERSION) if is_cluster(p["kind"], p["name"]) or st.get("hrd") else "na"
        blocked = {}
        if not solved:
            blocked["depth"] = blocked["compare"] = blocked["asteroids"] = "nosolve"
        elif not gaia:
            blocked["depth"] = "nogaia"
        if status["hrd"] != "na" and "depth" in blocked:
            blocked["hrd"] = blocked["depth"]
        if not fr["metrics"]:
            blocked["sky"] = "noframes"
        if solved and not small:
            blocked["asteroids"] = "nosmall"
        if not quality:
            blocked["tutor"] = "noquality"
        row = {"id": pid, "rid": r["rid"], "name": p["name"], "kind": p["kind"], "kind_label": objects.KIND_LABEL.get(p["kind"] or "generic"),
               "created": r["created"], "exposure": r["exposure"] or 0, "n_frames": r["n_frames"] or 0, "nights": nights,
               "device": dev["name"], "score": quality.get("score"), "solved": solved, "status": status, "blocked": blocked,
               "catalog": cat, "depth": None, "compare": None, "sky": None, "hrd": None, "asteroids": None, "findings": [],
               "can_develop": any((wd / f"p{pid}").glob("stack*/state.json"))}
        if status["depth"] in ("done", "old") and st["depth"].get("ok"):
            row["depth"] = _depth_info(st["depth"], r["exposure"])
            if status["depth"] == "done":
                cloud += _cloud(pid, st["depth"])
        if status["compare"] in ("done", "old") and st["compare"].get("ok"):
            row["compare"] = _compare_info(st["compare"])
            row["compare"]["items"] = [{k: c.get(k) for k in ("art", "sigma", "dmag", "mag_neu", "mag_ref", "x", "y", "katalog", "kleinkoerper")}
                                       for c in st["compare"].get("kandidaten") or []][:30]
        if status["hrd"] in ("done", "old") and st["hrd"].get("ok"):
            row["hrd"] = _hrd_info(st["hrd"])
        if status["asteroids"] in ("done", "old") and st["asteroids"].get("ok"):
            row["asteroids"] = _asteroid_info(st["asteroids"])
        ns = sky_rows.get(pid) or []
        if ns:
            own = [n for n in ns if n.get("sqm") is not None]
            see = [n["seeing"] for n in ns if n.get("seeing") is not None]
            best = max(own, key=lambda n: n["sqm"]) if own else None
            row["sky"] = {"sqm": best and best["sqm"], "bortle": best and best.get("bortle"), "night": best and best["night"],
                          "seeing": _r(min(see), 2) if see else None,
                          "nights": [{"night": n["night"], "sqm": n.get("sqm"), "sqm_all": n.get("sqm_all"), "seeing": n.get("seeing"),
                                      "site": n.get("site_name")} for n in ns]}
        if quality and tc.get("nights") is not None:
            try:
                fs = tutor.findings(quality, tc["nights"], tc.get("device") or dev,
                                    {"exposure_s": r["exposure"], "kind": p["kind"], "latitude": None, "comet": st.get("comet"),
                                     "auto_fixed": bool(st.get("color_gradient") or (st.get("params") or {}).get("color_gradient"))})
                row["findings"] = [{"code": f["code"], "level": f["level"], "title": f["title"]} for f in fs if f["level"] in ("bad", "warn")]
            except Exception:
                pass
        rows.append(row)
    if len(cloud) > MAX_CLOUD:
        cloud = [c for c in cloud if c["t"] == "g"] + [c for c in cloud if c["t"] == "s"][:: max(1, len(cloud) // MAX_CLOUD)]
        cloud = cloud[:MAX_CLOUD]
    return {"rows": rows, "cloud": cloud, "imported": imported, "devices": sorted(devices), "gaia": gaia, "solver": solver,
            "built": time.time()}


def _match(row: dict, f: dict) -> bool:
    if f.get("device") and row.get("device") != f["device"]:
        return False
    if f.get("night") and f["night"] not in (row.get("nights") or []):
        return False
    if f.get("year") and not any(n.startswith(str(f["year"])) for n in row.get("nights") or []):
        return False
    if f.get("q") and f["q"].lower() not in row["name"].lower():
        return False
    return True


def summarize(base: dict, f: dict | None = None) -> dict:
    """Kennzahlen, Rekorde und Verläufe über die (gefilterten) Objekte. Nur gemessene Werte; leer bleibt leer."""
    f = f or {}
    all_rows = [r for r in base["rows"] if not r.get("imported")]
    rows = [r for r in all_rows if _match(r, f)]
    filtered = any(f.get(k) for k in ("device", "night", "year", "q"))
    nights = sorted({n for r in rows for n in r["nights"]})
    cov = {k: Counter() for k in KINDS}
    blocked: dict[str, dict] = {}
    for r in rows:
        for k in KINDS:
            if r["status"][k] == "na":                   # entfällt (HR-Diagramm bei Nicht-Haufen)
                continue
            b = r["blocked"].get(k)
            cov[k]["blocked" if b and r["status"][k] not in ("done", "old") else r["status"][k]] += 1
            if b:
                x = blocked.setdefault(b, {"code": b, "text": BLOCK[b], "kinds": set(), "ids": []})
                x["kinds"].add(k)
                if r["id"] not in x["ids"]:
                    x["ids"].append(r["id"])
    for x in blocked.values():
        x["kinds"] = [k for k in KINDS if k in x["kinds"]]
    D = [r for r in rows if r["depth"]]
    ref = lambda r, **k: {"id": r["id"], "name": r["name"], "rid": r["rid"], **k}

    def best(items, key, hi=True):
        items = [x for x in items if key(x) is not None]
        return (max if hi else min)(items, key=key) if items else None

    rec = {}
    if x := best(D, lambda r: r["depth"]["m5"]):
        rec["deepest"] = ref(x, value=x["depth"]["m5"])
    if x := best(D, lambda r: (r["depth"]["far_star"] or {}).get("ly")):
        rec["far_star"] = ref(x, value=x["depth"]["far_star"]["ly"], star=x["depth"]["far_star"], bild=x["depth"]["bild"])
    if x := best(D, lambda r: (r["depth"]["far_gal"] or {}).get("z")):
        rec["far_gal"] = ref(x, value=x["depth"]["far_gal"]["ly"], gal=x["depth"]["far_gal"], bild=x["depth"]["bild"])
    if x := best(D, lambda r: r["depth"]["galaxies"] or None):
        rec["most_gal"] = ref(x, value=x["depth"]["galaxies"])
    if x := best(D, lambda r: r["depth"]["m5_1h"]):
        rec["efficient"] = ref(x, value=x["depth"]["m5_1h"])
    H = [r for r in rows if r.get("hrd") and r["hrd"]["found"] and not r["hrd"]["globular"]]
    if x := best(H, lambda r: r["hrd"]["age"]):                # grobe Schätzung aus dem Abknickpunkt (HR-Diagramm)
        rec["oldest_cluster"] = ref(x, value=x["hrd"]["age"], lo=x["hrd"]["age_lo"], hi=x["hrd"]["age_hi"], members=x["hrd"]["members"])
    if x := best(rows, lambda r: r["score"]):
        rec["score"] = ref(x, value=x["score"])
    if x := best(rows, lambda r: r["exposure"] or None):
        rec["exposure"] = ref(x, value=x["exposure"])
    # Asteroiden: verschiedene nachgewiesene Kleinkörper, schwächster als Rekord
    ast, seen = [], {}
    for r in rows:
        for b in (r.get("asteroids") or {}).get("items") or []:
            ast.append({"id": r["id"], "name_obj": r["name"], "rid": r["rid"], "bild": r["asteroids"]["bild"], **b})
            old = seen.get(b["name"])
            if old is None or (b.get("mag") is not None and (old.get("mag") is None or b["mag"] > old["mag"])):
                seen[b["name"]] = ast[-1]
    ast_unique = sorted(seen.values(), key=lambda b: -(b.get("mag") or 0))
    if x := best(ast_unique, lambda b: b.get("mag")):
        rec["faint_asteroid"] = {"id": x["id"], "name": x["name_obj"], "rid": x["rid"], "value": x["mag"], "body": x["name"],
                                 "x": x.get("x"), "y": x.get("y"), "bild": x.get("bild")}
    sky_nights = [dict(n, id=r["id"], name=r["name"]) for r in rows if r["sky"] for n in r["sky"]["nights"]]
    if f.get("night"):
        sky_nights = [n for n in sky_nights if n["night"] == f["night"]]
    if f.get("year"):                                  # Objekte mit mehreren Jahren: nur die Nächte des gewählten Jahres
        sky_nights = [n for n in sky_nights if n["night"].startswith(str(f["year"]))]
    if x := best(sky_nights, lambda n: n.get("sqm")):
        rec["darkest"] = {"id": x["id"], "name": x["name"], "value": x["sqm"], "night": x["night"]}
    if x := best(sky_nights, lambda n: n.get("seeing"), hi=False):
        rec["seeing"] = {"id": x["id"], "name": x["name"], "value": x["seeing"], "night": x["night"]}

    # Himmel je Nacht (Median über Objekte), beste Nächte
    per_night: dict[str, dict] = {}
    for n in sky_nights:
        x = per_night.setdefault(n["night"], {"night": n["night"], "sqm": [], "sqm_all": [], "seeing": [], "objects": []})
        for k in ("sqm", "sqm_all", "seeing"):
            if n.get(k) is not None:
                x[k].append(n[k])
        x["objects"].append({"id": n["id"], "name": n["name"]})
    med = lambda v: _r(sorted(v)[len(v) // 2] if len(v) % 2 else sum(sorted(v)[len(v) // 2 - 1: len(v) // 2 + 1]) / 2) if v else None
    sky_series = [{"night": k, "sqm": med(v["sqm"]), "sqm_all": med(v["sqm_all"]), "seeing": med(v["seeing"]), "objects": v["objects"]}
                  for k, v in sorted(per_night.items())]
    best_nights = sorted([n for n in sky_series if n["sqm"] is not None], key=lambda n: -n["sqm"])[:5]

    # Grenzgröße über die Zeit (letzte Nacht der Version), Rangliste „Was hat am meisten gebracht“
    depth_series = [{"id": r["id"], "name": r["name"], "night": r["nights"][-1], "m5": r["depth"]["m5"], "m5_1h": r["depth"]["m5_1h"],
                     "hours": round(r["exposure"] / 3600, 2)} for r in D if r["depth"]["m5"] is not None and r["nights"]]
    ranking = sorted([x for x in depth_series if x["m5_1h"] is not None], key=lambda x: -x["m5_1h"])[:10]

    hist = [0] * 11
    for r in rows:
        if r["score"] is not None:
            hist[max(0, min(10, int(round(r["score"]))))] += 1
    fc: dict[str, dict] = {}
    for r in rows:
        for x in r["findings"]:
            y = fc.setdefault(x["code"], {"code": x["code"], "title": x["title"], "level": x["level"], "n": 0, "ids": []})
            y["n"] += 1
            y["ids"].append(r["id"])
            if x["level"] == "bad":
                y["level"] = "bad"
    findings = sorted(fc.values(), key=lambda y: -y["n"])

    # Kataloge über alle eigenen Objekte (auch fertig importierte Bilder)
    cat_rows = [r for r in base["rows"] if (not r.get("imported") and _match(r, f)) or (r.get("imported") and not filtered)]
    owned = {c: r["id"] for r in cat_rows for c in r["catalog"]}
    catalogs = {"messier": sorted(int(c[1:]) for c in owned if c[0] == "M"), "caldwell": sorted(int(c[1:]) for c in owned if c[0] == "C"),
                "links": owned}

    # Veränderungen über alle Objekte: im Katalog erkannte zuerst, dann nach Signifikanz
    changes, counts = [], Counter()
    for r in rows:
        c = r["compare"]
        if not c:
            continue
        counts.update(c["counts"])
        counts["variables"] += c["variables"]
        counts["small"] += c["small"]
        for k in c["items"]:
            changes.append({"id": r["id"], "name": r["name"], "rid": r["rid"], "bild": c["bild"], **k})
    changes.sort(key=lambda k: (not (k.get("kleinkoerper") or k.get("katalog")), -(k.get("sigma") or 0)))

    totals = {"objects": len(rows), "nights": len(nights), "exposure": sum(r["exposure"] for r in rows),
              "frames": sum(r["n_frames"] for r in rows), "analysed": sum(1 for r in rows if r["depth"]),
              "changes": sum(r["compare"]["n"] for r in rows if r["compare"]), "galaxies": sum(r["depth"]["galaxies"] for r in D),
              "stars": sum(r["depth"]["erfasst"] or 0 for r in D), "imported": base["imported"], "asteroids": len(seen)}
    years = sorted({n[:4] for r in all_rows for n in r["nights"]}, reverse=True)
    return {"totals": totals, "records": rec, "coverage": {k: dict(v) for k, v in cov.items()}, "blocked": list(blocked.values()),
            "sky_series": sky_series, "best_nights": best_nights, "depth_series": depth_series, "ranking": ranking,
            "score_hist": hist, "findings": findings, "catalogs": catalogs, "changes": changes[:40], "change_counts": dict(counts),
            "asteroids": ast_unique[:40],
            "rows": [{k: v for k, v in r.items() if k not in ("compare", "asteroids")}
                     | {"compare": {k: v for k, v in (r["compare"] or {}).items() if k != "items"} or None,
                        "asteroids": {k: v for k, v in (r.get("asteroids") or {}).items() if k != "items"} or None}
                     for r in rows],
            "filters": {"devices": base["devices"], "years": years, "nights": sorted({n for r in all_rows for n in r["nights"]}, reverse=True)},
            "gaia": base["gaia"], "solver": base["solver"], "filtered": filtered, "built": base["built"]}


class Dashboard:
    """Zwischenspeicher fürs Dashboard + Sammelauftrag."""

    def __init__(self, eng):
        self.eng = eng
        self._base, self._sig, self._at = None, None, 0.0
        self._lock = threading.Lock()
        self.batch = Batch(eng, self)

    def _signature(self):
        db = self.eng.db
        a = db.one("SELECT COUNT(*) n, MAX(id) m, SUM(LENGTH(stats)) s FROM results")
        try:
            b = db.one("SELECT COUNT(*) n, MAX(updated) u FROM sky_nights")
        except Exception:
            b = {}
        wcs = tuple(sorted(p.parent.name for p in self.eng.s.work_dir.glob("p*/wcs.json")))
        return (tuple(a.values()), tuple((b or {}).values()), wcs, bool(self.eng.s.gaia))   # Solver-Suche kostet ~0,3 s → nur über CACHE_S

    def base(self) -> dict:
        with self._lock:
            sig = self._signature()
            if self._base is None or sig != self._sig or time.time() - self._at > CACHE_S:
                self._base, self._sig, self._at = collect(self.eng), sig, time.time()
            return self._base

    def live(self, rows: list[dict]) -> None:
        """Laufende Aufträge (Insights-Warteschlange, Sammelauftrag) in die Statuswerte übernehmen – ohne den Zwischenspeicher zu ändern."""
        ins = self.eng.insights.state
        cur = self.batch.state.get("item") if self.batch.state.get("state") in ("running", "paused") else None
        todo = {(k, i) for k, i in self.batch.todo()}
        for r in rows:
            if r.get("imported"):
                continue
            r["status"] = dict(r["status"])
            for k in KINDS:
                st = ins.get((k, r["rid"])) if k in NET_KINDS else None
                if r["status"][k] == "na" and not st:
                    continue
                if st and st.get("state") in ("queued", "running", "error"):
                    r["status"][k] = st["state"]
                elif cur and cur[0] == k and cur[1] == r["id"]:
                    r["status"][k] = "running"
                elif (k, r["id"]) in todo:
                    r["status"][k] = "queued"

    def summary(self, f: dict | None = None) -> dict:
        out = summarize(self.base(), f)
        self.live(out["rows"])
        out["job"] = self.batch.public()
        out["insights"] = self.eng.insights.settings()
        return out

    def universe(self, f: dict | None = None) -> dict:
        base = self.base()
        ids = {r["id"] for r in base["rows"] if not r.get("imported") and _match(r, f or {})}
        names = {r["id"]: r["name"] for r in base["rows"]}
        pts = [c for c in base["cloud"] if c["p"] in ids]
        return {"points": pts, "names": {i: names[i] for i in {c["p"] for c in pts}}}

    def invalidate(self):
        with self._lock:
            self._base = None


class Batch:
    """„Alles analysieren“: eine Liste (Art, Objekt) nacheinander; Netzauswertungen einzeln mit Pause dazwischen."""

    def __init__(self, eng, dash):
        self.eng, self.dash = eng, dash
        self.state: dict = {"state": "idle"}
        self.items: list[tuple[str, int, int, str]] = []
        self.pos = 0
        self._pause = threading.Event()
        self._stop = threading.Event()
        self._lock = threading.Lock()

    def busy(self) -> bool:
        return self.state.get("state") in ("running", "paused")

    def todo(self) -> list[tuple[str, int]]:
        return [(k, pid) for k, pid, _, _ in self.items[self.pos + 1:]] if self.busy() else []

    def plan(self, kinds: list[str], scope: str = "missing", ids: list[int] | None = None) -> tuple[list, dict]:
        """Was liefe? scope: missing = fehlend/veraltet, errors = dazu fehlgeschlagene, all = alles neu."""
        want = {"missing": ("none", "old"), "errors": ("none", "old", "error"), "all": ("none", "old", "error", "done")}[scope]
        rows = [r for r in self.dash.base()["rows"] if not r.get("imported") and (ids is None or r["id"] in ids)]
        items, skipped = [], Counter()
        for k in KINDS:
            if k not in kinds:
                continue
            for r in rows:
                if r["blocked"].get(k):
                    skipped[r["blocked"][k]] += 1
                elif r["status"][k] in want:
                    items.append((k, r["id"], r["rid"], r["name"]))
        return items, dict(skipped)

    def start(self, kinds: list[str], scope: str = "missing", ids: list[int] | None = None) -> dict:
        with self._lock:
            if self.busy():
                raise RuntimeError("Es läuft schon eine Sammelanalyse.")
            items, skipped = self.plan(kinds, scope, ids)
            self.items, self.pos = items, -1
            self._pause.clear()
            self._stop.clear()
            self.state = {"state": "running" if items else "done", "total": len(items), "done": 0, "errors": [], "skipped": skipped,
                          "kinds": [k for k in KINDS if k in kinds], "scope": scope, "started": time.time(),
                          "finished": None if items else time.time(), "item": None, "detail": "", "key": f"analysis:{int(time.time())}",
                          "per_kind": {k: {"total": sum(1 for i in items if i[0] == k), "done": 0} for k in KINDS if k in kinds}}
            if items:
                threading.Thread(target=self._run, daemon=True, name="stacking-analysis").start()
            return self.public()

    def pause(self, on: bool) -> dict:
        if self.busy():
            (self._pause.set if on else self._pause.clear)()
            self.state["state"] = "paused" if on else "running"
        return self.public()

    def stop(self) -> dict:
        if self.busy():
            self._stop.set()
            self._pause.clear()
        return self.public()

    def public(self) -> dict:
        s = dict(self.state)
        if s.get("item"):
            k, pid, rid, name = s["item"]
            s["item"] = {"kind": k, "label": LABEL[k], "id": pid, "name": name}
        return s

    def _wait(self, seconds: float):
        end = time.time() + seconds
        while time.time() < end and not self._stop.is_set() and not self.eng.stop.is_set():
            time.sleep(0.1)

    def _run(self):
        try:
            for i, item in enumerate(self.items):
                while self._pause.is_set() and not self._stop.is_set() and not self.eng.stop.is_set():
                    time.sleep(0.2)
                if self._stop.is_set() or self.eng.stop.is_set():
                    break
                self.pos = i
                k, pid, rid, name = item
                self.state.update(item=item, detail="läuft")
                err = None
                try:
                    err = self._one(k, pid, rid)
                except Exception as e:                          # ein Objekt darf die Liste nicht anhalten
                    err = str(e)[:200]
                if err:
                    self.state["errors"].append({"kind": k, "label": LABEL[k], "id": pid, "name": name, "error": err})
                self.state["done"] += 1
                self.state["per_kind"][k]["done"] += 1
                self.dash.invalidate()
                if k in NET_KINDS or (k == "tutor" and self._weather()):
                    self._wait(GAP_S)
        finally:
            self.state.update(state="stopped" if self._stop.is_set() else "done", finished=time.time(), item=None, detail="")
            self.pos = len(self.items)
            self.dash.invalidate()

    def _weather(self) -> bool:
        from . import tutor
        return bool(tutor.settings(self.eng.ctx.store).get("weather"))

    def _one(self, k: str, pid: int, rid: int) -> str | None:
        eng = self.eng
        if k in NET_KINDS:
            eng.insights.submit(k, rid)
            while not self._stop.is_set() and not eng.stop.is_set():
                st = eng.insights.status(k, rid)
                if not st or st.get("state") not in ("queued", "running"):
                    return st.get("message") if st and st.get("state") == "error" else None
                self.state["detail"] = st.get("message") or ""
                time.sleep(0.3)
            return None
        if k == "sky":
            from . import skyquality
            while eng.sky.busy() and not self._stop.is_set():       # „Für alle Nächte berechnen“ läuft gerade: nicht doppelt rechnen
                self.state["detail"] = "wartet auf die Himmelsberechnung"
                time.sleep(0.5)
            skyquality.compute_project(eng.pl, pid, eng.ctx.services.get("site_for"),
                                       log=lambda m: self.state.update(detail=m))
            return None
        if k == "tutor":
            from . import tutor
            tutor.build_report(eng.db, rid, tutor.settings(eng.ctx.store), eng.s.work_dir, refresh=True,
                               site_for=eng.ctx.services.get("site_for"))
            return None
        return None

    def activity(self) -> list[dict]:
        s = self.state
        if s.get("state") in (None, "idle"):
            return []
        it = s.get("item")
        run = s["state"] in ("running", "paused")
        det = (f"{s['done']}/{s['total']}" + (f" · {LABEL[it[0]]} · {it[3]}" if it else "") + (" · angehalten" if s["state"] == "paused" else "")) if run \
            else f"{s['done']} Auswertungen" + (f", {len(s['errors'])} Fehler" if s.get("errors") else "") + (" · abgebrochen" if s["state"] == "stopped" else "")
        return [{"key": s["key"], "title": "Analyse aller Objekte", "kind": "Analyse", "detail": det,
                 "state": "running" if run else "error" if s.get("errors") and not s["done"] else "done",
                 "progress": s["done"] / max(1, s["total"]), "started": s.get("started"), "finished": s.get("finished"), "href": "#/analyse"}]
