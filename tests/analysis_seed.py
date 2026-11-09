"""Synthetische Bibliothek fürs Analyse-Dashboard (Tests und Vorschau-Bildschirmfotos): Objekte mit gespeicherter Tiefe,
Vergleich, Himmelsqualität und Tutor-Nachtdaten. Alle Werte sind erfunden, der Ort ebenfalls."""
from __future__ import annotations

import datetime as dt
import json
import time

import numpy as np

from astroflow.modules.stacking.asteroids import ASTEROID_VERSION
from astroflow.modules.stacking.compare import COMPARE_VERSION
from astroflow.modules.stacking.depth import DEPTH_VERSION

# (Name, Art, RA, Dec, Nacht, Startstunde UTC, Belichtung h, m5 oder None, Status)
OBJECTS = [
    ("M 31", "galaxy", 10.68, 41.27, "2025-10-18", 21, 3.2, 17.9, "ok"),
    ("M 101", "galaxy", 210.80, 54.35, "2026-04-12", 22, 5.8, 18.4, "ok"),
    ("M 13", "globular", 250.42, 36.46, "2025-06-21", 21, 0.8, 16.6, "ok"),        # Mittsommer → Dämmerung
    ("M 42", "emission", 83.82, -5.39, "2026-01-03", 20, 1.5, 16.9, "ok"),
    ("NGC 7000", "emission", 314.70, 44.30, "2025-08-30", 21, 2.4, 17.2, "ok"),
    ("M 57", "planetary", 283.40, 33.03, "2025-07-15", 22, 1.1, None, "none"),      # gelöst, Tiefe noch nicht gemessen
    ("NGC 891", "galaxy", 35.64, 42.35, "2025-11-24", 20, 2.0, None, "nosolve"),   # nicht gelöst
    ("M 81", "galaxy", 148.89, 69.07, "2026-02-14", 21, 4.0, 18.1, "ok"),
]
SITE = {"id": "a", "name": "Beispielwiese", "lat": 47.5, "lon": 10.25, "alt_m": 400}


def _depth(rng, name, ra, dec, m5, hours):
    w, h = 1600, 1100
    stars = []
    for i in range(140):
        g = float(rng.uniform(8, m5))
        stars.append({"t": "s", "x": round(float(rng.uniform(20, w - 20)), 1), "y": round(float(rng.uniform(20, h - 20)), 1),
                      "ly": round(float(10 ** rng.uniform(1.6, 4.3)), 1), "g": round(g, 1), "bp_rp": round(float(rng.uniform(0.2, 2.4)), 2),
                      "n": f"Gaia DR3 {rng.integers(10 ** 17, 10 ** 18)}"})
    far = max(stars, key=lambda s: s["ly"])
    near = min(stars, key=lambda s: s["ly"])
    gal = []
    for i in range(int(rng.integers(2, 9))):
        z = float(rng.uniform(0.01, 0.35 if m5 > 18 else 0.12))
        gal.append({"name": f"PGC {rng.integers(1000, 99999)}", "z": round(z, 4), "ly": round(z * 1.36e10, -6), "art": "Galaxie",
                    "sichtbar": True, "x": round(float(rng.uniform(40, w - 40)), 1), "y": round(float(rng.uniform(40, h - 40)), 1)})
    pts = stars + [{"t": "g", "x": o["x"], "y": o["y"], "ly": o["ly"], "n": o["name"], "z": o["z"], "art": o["art"]} for o in gal]
    fg = max(gal, key=lambda o: o["z"])
    return {"v": DEPTH_VERSION, "ok": True, "grenze": {"m5": m5, "lim50": round(m5 - 0.4, 1), "erfasst": int(80 + hours * 300), "katalog": int(200 + hours * 400),
                                          "nullpunkt": 21.4, "fwhm_px": 3.1, "klassen": []},
            "entfernung": {"mit_parallaxe": len(stars), "naechster": {"id": near["n"], "ly": near["ly"], "g": near["g"], "x": near["x"], "y": near["y"]},
                           "fernster": {"id": far["n"], "ly": far["ly"], "g": far["g"], "x": far["x"], "y": far["y"]}, "median_ly": 2400, "verteilung": []},
            "hauptobjekt": {"name": name, "ly": 2.5e6 if name == "M 31" else 2.1e7 if name == "M 101" else None, "quelle": "Testdaten"},
            "galaxien": gal, "galaxien_fehler": None, "galaxien_sichtbar": len(gal), "teile": 0, "fernstes": fg, "punkte": pts, "hinweise": [],
            "bild": {"w": w, "h": h}, "feld": {"ra": ra, "dec": dec, "radius": 0.55, "skala": 1.24}, "erstellt": time.time()}


def _compare(rng, i):
    kinds = ["neu", "verschwunden", "heller", "schwächer"]
    cands = []
    for k in range(int(rng.integers(1, 5))):
        c = {"art": kinds[(i + k) % 4], "sigma": round(float(rng.uniform(6, 30)), 1), "dmag": round(float(rng.uniform(-1.5, 1.5)), 2),
             "x": round(float(rng.uniform(50, 1500)), 1), "y": round(float(rng.uniform(50, 1000)), 1), "bild": f"k{k}.png", "laenglich": 1.1}
        if k == 0 and i % 2:
            c["katalog"] = {"name": f"V{rng.integers(100, 999)} Her", "art": "Veränderlicher Stern", "abstand": 1.2, "veraenderlich": True}
        if k == 1 and i % 3 == 0:
            c["kleinkoerper"] = {"name": f"({rng.integers(1000, 99999)}) Beispiel", "abstand": 3.4, "vmag": 17.8, "zeit": "2026-01-03T21:00:00"}
        cands.append(c)
    return {"v": COMPARE_VERSION, "ok": True, "ref": {"art": "survey", "label": "DSS2 rot", "datum": "1990"}, "kandidaten": cands,
            "verworfen": {}, "fwhm_px": 3.1, "skala": 1.0, "bild": {"w": 1500, "h": 1050}, "hinweise": [], "erstellt": time.time(), "referenzen": 0}


ASTEROIDS = {0: [("(1036) Ganymed", 15.8, 42.0), ("(2201) Oljato", 17.9, 61.0)], 1: [("(4179) Toutatis", 16.4, 88.0)],
             7: [("(1036) Ganymed", 16.1, 40.0), ("(99942) Apophis", 18.6, 120.0)]}      # erfundene Funde (Namen echter Körper)


def _asteroids(rng, i, night):
    found = ASTEROIDS.get(i, [])
    items = [{"name": n, "art": "Asteroid", "vmag": round(m - 0.2, 1), "mag": m, "mag_err": 0.08, "rate": r, "night": night, "status": "nachgewiesen",
              "snr": round(float(rng.uniform(8, 40)), 1), "abweichung": 2.1, "aufnahmen": 40, "x": round(float(rng.uniform(80, 1400)), 1),
              "y": round(float(rng.uniform(80, 1000)), 1), "bahn": None} for n, m, r in found]
    items.append({"name": "(31415) Beispiel", "art": "Asteroid", "vmag": 19.4, "rate": 33.0, "night": night, "status": "keine_rohbilder",
                  "x": 300.0, "y": 200.0, "bahn": None})
    faint = max(items[:len(found)], key=lambda b: b["mag"]) if found else None
    return {"v": ASTEROID_VERSION, "ok": True, "erstellt": time.time(), "naechte": [{"night": night, "aufnahmen": 40, "rohbilder": 40, "im_feld": len(items)}],
            "kleinkoerper": items, "nachgewiesen": len(found), "im_feld": len(items),
            "schwaechster": {"name": faint["name"], "mag": faint["mag"], "x": faint["x"], "y": faint["y"]} if faint else None,
            "grenze": 19.5, "nullpunkt": 21.3, "suchradius": 15.0, "bild": {"w": 1500, "h": 1050}, "hinweise": []}


def seed(db, work_dir, files: dict | None = None, device="Vaonis Stellina") -> list[int]:
    """Legt die Objekte samt Frames, Ergebnis und Himmelsnächten an. Rückgabe: Projekt-IDs in der Reihenfolge von OBJECTS."""
    from astroflow.modules.stacking import skyquality as sq
    from astroflow.modules.stacking import tutor
    rng = np.random.default_rng(7)
    sq.ensure_table(db)
    cfg = {**tutor.DEFAULTS, "weather": False, "lat": SITE["lat"], "lon": SITE["lon"]}
    ids = []
    for i, (name, kind, ra, dec, night, hour, hours, m5, state) in enumerate(OBJECTS):
        pid = db.x("INSERT INTO projects(key,name,obj_id,kind,ra,dec,options,created) VALUES(?,?,?,?,?,?,?,?)",
                   (f"seed|{name}", name, name.replace(" ", ""), kind, ra, dec, "{}", time.time()))
        ids.append(pid)
        t0 = dt.datetime.fromisoformat(night).replace(hour=hour, tzinfo=dt.timezone.utc)
        n = 40
        for k in range(n):
            t = t0 + dt.timedelta(seconds=hours * 3600 * k / n)
            info = {"date_obs": t.strftime("%Y-%m-%dT%H:%M:%S"), "instrument": device, "exptime": hours * 3600 / n, "shape": [2080, 3072]}
            met = {"n_stars": int(120 - (30 if k > 34 and i == 2 else 0)), "fwhm": 5.5 + 0.02 * k, "ecc": 0.4, "bg": 900 + k * (3 if i == 2 else 0.5),
                   "noise": 55.0}
            db.x("INSERT INTO frames(id,project_id,path,kind,night,info,metrics,status,stacked) VALUES(?,?,?,?,?,?,?,?,1)",
                 (f"seed{pid}_{k}", pid, f"seed/{pid}/{k}.fits", "light", night, json.dumps(info), json.dumps(met), "stacked"))
        score = round(float(np.clip(9.4 - 0.6 * i + (1.2 if i == 2 else 0), 3, 10)), 1)
        issues = [{"code": "color_cast", "text": "leichter Farbstich"}] if i % 3 == 0 else []
        # Bildqualität (grade.py): Rauschen und Schärfe je Objekt verschieden, Bearbeitung (score) davon unabhängig
        stats = {"quality": {"score": score, "issues": issues, "hints": [], "measure": {"snr": round(6 + 4 * hours, 1)}},
                 "fwhm_px": round(4.0 + 0.3 * i, 2), "nights": [night]}
        if state == "ok":
            stats["depth"] = _depth(rng, name, ra, dec, m5, hours)
            stats["compare"] = _compare(rng, i)
            stats["asteroids"] = _asteroids(rng, i, night)
        rid = db.x("INSERT INTO results(project_id,created,n_frames,exposure,files,stats) VALUES(?,?,?,?,?,?)",
                   (pid, time.time() - 86400 * (len(OBJECTS) - i), n, hours * 3600, json.dumps(files or {}), json.dumps(stats)))
        if state != "nosolve":
            (work_dir / f"p{pid}").mkdir(parents=True, exist_ok=True)
            (work_dir / f"p{pid}" / "wcs.json").write_text(json.dumps({"key": "seed", "wcs": {}}), encoding="utf-8")
        if i in (0, 2, 3):                                  # Tutor-Nachtdaten echt aus den Aufnahmezeiten (astropy, offline)
            tutor.build_report(db, rid, cfg, work_dir)
        if i < 5:                                           # Himmelsqualität: Ortswert, dunkler mit dem Index
            sqm = round(21.6 - 0.25 * i + (-1.2 if i == 2 else 0), 2)
            data = {"night": night, "sqm": None if i == 2 else sqm, "sqm_all": sqm, "seeing": round(2.2 + 0.3 * i, 2),
                    "bortle": sq.bortle(sqm, 0.25) if i != 2 else None, "err": 0.25, "flags": [], "points": []}
            db.x("INSERT OR REPLACE INTO sky_nights VALUES(?,?,?,?,?,?,?,?)",
                 (pid, night, SITE["id"], SITE["name"], "stellina|200", time.time(), sq.SKY_VERSION, json.dumps(data)))
    return ids

