"""Satellit anklicken (0.45.0, Nutzerwunsch „auf so einen Satelliten klicken können und Infos zu diesem bekommen plus den Ausschnitt
vom Bild, das ich gemacht habe, wo er zu sehen ist“): Details eines zugeordneten Satelliten und ein kleiner Bildausschnitt der Spur.

Nur gespeicherte Daten, kein Netz: Kandidat aus `kandidaten.json` (Abgleich, Messwerte, Ausschnitt-PNG des Trefferbilds),
Bahnelemente aus dem Abgleich (`matches[].tle`), lokale SATCAT-Kopie (`catalog_cache/archives/satcat.csv` bzw. `satcat-names.json`),
Helligkeit wie in der Satellitenstatistik (`satellites.py`). Fehlt etwas, fehlt das Feld – nichts wird geschätzt oder erfunden.
Der Ausschnitt (`satellit-ausschnitt.jpg` im Kandidatenordner) ist ein Zwischenspeicher: klein fürs Handy, Spur in der Mitte, gestreckt."""
from __future__ import annotations

import csv
import math
import os
import re
import threading
from functools import lru_cache
from pathlib import Path

CROP_NAME = "satellit-ausschnitt.jpg"      # fester Name im Kandidatenordner (kein Teil stammt aus der Anfrage)
CROP_VERSION = 1                           # bei Änderungen an der Ausschnittrechnung erhöhen → Zwischenspeicher neu
CROP_MAX = 560                             # längste Seite in Pixeln (Handy: ~30–60 KB)
CROP_MIN = 160                             # kleinster Ausschnitt in Quellpixeln
CROP_ASPECT = 1.4                          # Breite/Höhe, soweit das Quellbild reicht
SURE_ARCSEC = 60.0                         # „eindeutig“: Bahn höchstens so weit neben der Spur …
SURE_COVER = .8                            # … deckt die Spur weitgehend ab …
SURE_TIME_S = 2.0                          # … und passt zeitlich (höchstens so weit außerhalb der Belichtung)
CID = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
TYPES = {"PAY": "Satellit", "R/B": "Raketenstufe", "DEB": "Trümmerteil", "UNK": "unbekanntes Objekt"}
OWNERS = {"US": "USA", "PRC": "China", "CIS": "Russland/GUS", "UK": "Großbritannien", "FR": "Frankreich", "GER": "Deutschland",
          "JPN": "Japan", "IND": "Indien", "ESA": "ESA", "EUME": "EUMETSAT", "IT": "Italien", "CA": "Kanada", "ISS": "ISS-Partner",
          "GLOB": "Globalstar", "O3B": "O3b/SES", "SES": "SES", "ORB": "ORBCOMM", "IRID": "Iridium", "ITSO": "Intelsat"}
_lock = threading.Lock()


def _num(v) -> float | None:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) else None


# ---------------------------------------------------------------- Kandidat ---
def find(run_path: Path, key: str):
    """(Beobachtungsordner, Kandidat, Sitzungsname) zu `<Ordner>/<K-Nummer>`; ValueError, wenn es ihn nicht gibt."""
    from .api import _read_json, within
    folder_name, _, cid = (key or "").partition("/")
    if not folder_name or not CID.match(cid):
        raise ValueError("Ungültiger Kandidat")
    for summary in _read_json(run_path / "videos.json", []) or []:
        if not isinstance(summary, dict) or not summary.get("candidates_file") or not summary.get("video"):
            continue
        folder = within(run_path, str(Path(summary["video"]).parent))
        if folder.name != folder_name:
            continue
        c = next((x for x in _read_json(within(run_path, summary["candidates_file"]), []) or [] if isinstance(x, dict) and x.get("id") == cid), None)
        if c is not None:
            return folder, c, summary.get("session")
    raise ValueError("Kandidat nicht gefunden")


def _sat_result(c: dict) -> dict:
    return next((r for r in c.get("catalogs") or [] if isinstance(r, dict) and r.get("provider") == "satellites"), {}) or {}


def _rank(m: dict) -> float:
    """Wie `sitefit._sure`: mittlerer Abstand + 20″ je Sekunde außerhalb der Belichtung."""
    return (m.get("mean_offset_arcsec") or m.get("separation_arcsec") or 0) + 20 * abs(m.get("time_offset_s") or 0)


def certainty(sat: dict, m: dict, idx: int) -> dict:
    """Wie sicher ist die Zuordnung? Stufe + Begründung aus den gespeicherten Abgleichswerten."""
    ms = [x for x in sat.get("matches") or [] if isinstance(x, dict) and x.get("norad_id") is not None and x.get("compatible", True)]
    sep = _num(m.get("mean_offset_arcsec")) or _num(m.get("separation_arcsec"))
    cover, toff = _num(m.get("coverage")), abs(_num(m.get("time_offset_s")) or 0)
    others = [x for x in ms if x is not m]
    facts = []
    if sep is not None:
        facts.append(f"Bahn im Mittel {sep:.0f}″ neben der Spur" if sep >= 1 else "Bahn liegt auf der Spur")
    if cover is not None:
        facts.append(f"deckt {round(cover * 100)} % der Spur ab")
    de = lambda v: f"{v:.1f}".replace(".", ",")
    if _num(m.get("alignment_degrees")) is not None:
        facts.append(f"Richtung auf {de(m['alignment_degrees'])}° gleich")
    facts.append("zeitlich während der Belichtung" if toff < .05 else f"{de(toff)} s außerhalb der Belichtung (Uhrtoleranz)")
    if sat.get("status") != "compatible":
        return dict(level="keine", text="Kein gültiger Katalogtreffer mehr (Abgleich neu gerechnet).", facts=facts)
    if idx > 0:
        best = ms[0] if ms else {}
        return dict(level="alternative", text=f"Alternative – „{best.get('name') or 'NORAD ' + str(best.get('norad_id'))}“ passt besser zu dieser Spur.", facts=facts)
    if m.get("site_widened"):
        return dict(level="unsicher", text="Nur mit der Standortunsicherheit vereinbar – Zuordnung unsicher.", facts=facts)
    if others and _rank(others[0]) < 1.5 * _rank(m) + 30:
        return dict(level="mehrdeutig", text=f"Mehrdeutig – {len(ms)} Bahnen passen fast gleich gut (typisch für Starlink-Züge).", facts=facts,
                    others=[x.get("name") or f"NORAD {x.get('norad_id')}" for x in others[:4]])
    if sep is not None and sep <= SURE_ARCSEC and (cover is None or cover >= SURE_COVER) and toff <= SURE_TIME_S:
        return dict(level="eindeutig", text="Eindeutig – Lage, Richtung und Zeit passen, keine andere Bahn kommt nahe.", facts=facts)
    return dict(level="wahrscheinlich", text="Wahrscheinlich – passt in Lage, Richtung und Zeit, aber mit größerem Abstand.", facts=facts)


# ---------------------------------------------------------------- Katalog ---
@lru_cache(maxsize=2)
def _satcat(path: str, mtime: int) -> dict:
    """Lokale SATCAT-Kopie {Nummer: Zeile} – nur die Spalten für die Infokarte (~6 MB, einmal je Dateistand eingelesen)."""
    keep = ("OBJECT_NAME", "OBJECT_ID", "OBJECT_TYPE", "OWNER", "LAUNCH_DATE", "LAUNCH_SITE", "DECAY_DATE", "OPS_STATUS_CODE", "PERIOD", "INCLINATION", "APOGEE", "PERIGEE")
    out = {}
    with open(path, encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            n = (row.get("NORAD_CAT_ID") or "").strip()
            if n.isdigit():
                out[int(n)] = {k: (row.get(k) or "").strip() for k in keep}
    return out


def catalog_entry(home: Path, norad: int) -> dict | None:
    """SATCAT-Eintrag aus der lokalen Kopie, sonst aus dem Einzel-Cache `satcat-names.json` (nur Name/Art/COSPAR/Start)."""
    from .api import _read_json
    base = home / "catalog_cache" / "archives"
    p = base / "satcat.csv"
    try:
        row = _satcat(str(p), p.stat().st_mtime_ns).get(int(norad))
    except (OSError, ValueError, csv.Error):
        row = None
    if row:
        return dict(row, _source="SATCAT-Kopie")
    single = _read_json(base / "satcat-names.json", {}) or {}
    e = single.get(str(norad)) if isinstance(single, dict) else None
    if isinstance(e, dict) and (e.get("cospar") or e.get("launch") or e.get("name")):
        return dict(OBJECT_NAME=e.get("name") or "", OBJECT_ID=e.get("cospar") or "", OBJECT_TYPE=e.get("type") or "",
                    LAUNCH_DATE=e.get("launch") or "", _source="SATCAT-Einzelabfrage")
    return None


def intl_from_tle(tle) -> str | None:
    """Internationale Kennung aus TLE-Zeile 1 (Spalten 10–17, „22041AX“ → „2022-041AX“)."""
    try:
        s = str(tle[0])[9:17].strip()
    except (TypeError, IndexError):
        return None
    m = re.match(r"^(\d\d)(\d{3})([A-Z]{1,3})$", s)
    if not m:
        return None
    yy = int(m[1])
    return f"{1900 + yy if yy >= 57 else 2000 + yy}-{m[2]}{m[3]}"


def kind_of(name: str | None, otype: str | None) -> str | None:
    """Art für die Karte: Konstellationen aus dem Namen, sonst die SATCAT-Objektart."""
    n = (name or "").upper()
    for pre, label in (("STARLINK", "Starlink"), ("ONEWEB", "OneWeb"), ("IRIDIUM", "Iridium"), ("GLOBALSTAR", "Globalstar"),
                       ("ORBCOMM", "ORBCOMM"), ("COSMOS", None), ("NAVSTAR", "GPS"), ("GPS ", "GPS"), ("GLONASS", "GLONASS"),
                       ("GALILEO", "Galileo"), ("BEIDOU", "BeiDou"), ("ISS (ZARYA)", "Raumstation ISS"), ("CSS (TIANHE)", "Raumstation Tiangong")):
        if n.startswith(pre) and label:
            return label + (" · Raketenstufe" if "R/B" in n else " · Trümmerteil" if " DEB" in n else "")
    if "R/B" in n:
        return "Raketenstufe"
    if " DEB" in n or n.endswith("DEB"):
        return "Trümmerteil"
    return TYPES.get(otype or "", otype) or None


# ---------------------------------------------------------------- Messwerte ---
def angular_speed(c: dict, m: dict) -> dict | None:
    """Winkelgeschwindigkeit: Spurlänge / Durchgangszeit (Bahn), sonst die Untergrenze aus Spurlänge / Belichtung."""
    ev = c.get("evidence") or {}
    length = _num(ev.get("length_arcmin"))
    ps = m.get("passage_seconds")
    if length and isinstance(ps, (list, tuple)) and len(ps) == 2 and all(_num(x) is not None for x in ps) and abs(ps[1] - ps[0]) >= .02:
        return dict(arcsec_s=round(length * 60 / abs(ps[1] - ps[0]), 1), kind="bahn", text="Spurlänge ÷ Durchgangszeit der Bahn")
    v = _num(ev.get("minimum_angular_speed_arcsec_s"))
    if v is None:
        pl = c.get("plausibility") or {}
        v = _num(pl.get("minimum_angular_speed_deg_s"))
        v = v * 3600 if v is not None else None
    return dict(arcsec_s=round(v, 1), kind="mindestens", text="mindestens (Spurlänge ÷ Belichtung)") if v else None


def brightness(run_path: Path, folder: Path, c: dict, m: dict, zps) -> dict | None:
    """Scheinbare Helligkeit wie in der Satellitenstatistik (gleiche Messwerte, Nullpunkt-Rangfolge, Extinktion)."""
    from .satellites import ZP_ERR, magnitude, phot_of, pick_zp
    from .stats import _run
    ph = phot_of(c, m)
    if not ph:
        return None
    session = next((s for s in _run(run_path).get("sessions", []) if s.get("folder") == folder.name), {})
    zp, src = pick_zp(zps, session.get("night"), session.get("_dev"))
    if zp is None:
        return dict(mag=None, note="Kein Nullpunkt für dieses Gerät – Helligkeit nicht bestimmbar.")
    return dict(mag=round(magnitude(ph, c.get("timestamp"), zp), 1), err=ZP_ERR[src], src=src,
                note={"night": "Nullpunkt dieser Nacht (Gaia-geeichte Stacks)", "device": "Nullpunkt des Geräts aus anderen Nächten",
                      "estimate": "Richtwert des Geräts – nur Schätzung"}[src])


# ---------------------------------------------------------------- Ausschnitt ---
def _hit_frame(c: dict) -> dict | None:
    hits = [f for f in c.get("frames") or [] if isinstance(f, dict) and f.get("hit")]
    return next((f for f in hits if (f.get("overlay") or {}).get("points")), hits[0] if hits else None)


def crop_source(run_path: Path, c: dict):
    """(Quellbild, Linie [[x,y],[x,y]] in dessen Pixeln, Art, Bild-Infos) – Ausschnitt-PNG des Trefferbilds, sonst Vollbild/Vergleichsbild."""
    from .api import within
    f = _hit_frame(c)
    if f and f.get("image"):
        try:
            p = within(run_path, f["image"])
        except ValueError:
            p = None
        pts = (f.get("overlay") or {}).get("points")
        ctx = c.get("context_image") or {}
        if not pts and ctx.get("points") and c.get("crop"):         # Linie nur im Vollbild gespeichert: in den Ausschnitt verschieben
            x0, y0 = c["crop"][:2]
            pts = [[p_[0] - x0, p_[1] - y0] for p_ in ctx["points"]]
        if p and p.is_file() and pts:
            return p, pts, "kandidat", f
    ctx = c.get("context_image") or {}
    for img, pts in (((f or {}).get("full_image") or {}).get("image"), ctx.get("points")), (ctx.get("image"), ctx.get("points")):
        if img and pts:
            try:
                p = within(run_path, img)
            except ValueError:
                continue
            if p.is_file():
                return p, pts, "vollbild", f or {"index": ctx.get("frame"), "timestamp_text": ctx.get("timestamp_text")}
    return None


def crop_box(w: int, h: int, pts) -> tuple[int, int, int, int]:
    """Ausschnitt um die Spur: Spur + Rand, mindestens CROP_MIN, Seitenverhältnis CROP_ASPECT, im Bild."""
    (x1, y1), (x2, y2) = pts[0][:2], pts[-1][:2]
    L = math.hypot(x2 - x1, y2 - y1)
    pad = max(24.0, .12 * L)
    bw, bh = max(abs(x2 - x1) + 2 * pad, CROP_MIN), max(abs(y2 - y1) + 2 * pad, CROP_MIN * .7)
    if bw < bh * CROP_ASPECT:
        bw = bh * CROP_ASPECT
    else:
        bh = max(bh, bw / CROP_ASPECT * .6)               # flache Spuren nicht zu einem Strich zusammenschneiden
    bw, bh = min(bw, w), min(bh, h)
    cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
    left = min(max(cx - bw / 2, 0), w - bw)
    top = min(max(cy - bh / 2, 0), h - bh)
    return int(round(left)), int(round(top)), int(round(left + bw)), int(round(top + bh))


def crop_geometry(run_path: Path, c: dict) -> dict | None:
    """Lage des Ausschnitts und der Spur darin (ohne Bild zu lesen außer der Größe)."""
    src = crop_source(run_path, c)
    if not src:
        return None
    path, pts, kind, frame = src
    try:
        from PIL import Image
        with Image.open(path) as im:
            w, h = im.size
    except Exception:
        return None
    box = crop_box(w, h, pts)
    s = min(1.0, CROP_MAX / max(box[2] - box[0], box[3] - box[1]))
    line = [[round((x - box[0]) * s, 1), round((y - box[1]) * s, 1)] for x, y in (pts[0][:2], pts[-1][:2])]
    return dict(path=path, box=box, scale=s, width=max(1, round((box[2] - box[0]) * s)), height=max(1, round((box[3] - box[1]) * s)),
                line=line, kind=kind, frame=frame.get("index"), time=frame.get("timestamp_text"), stamp=path.stat().st_mtime_ns)


def crop_jpeg(run_path: Path, folder: Path, c: dict) -> bytes | None:
    """Ausschnitt als JPEG; Zwischenspeicher `kandidaten/<K>/satellit-ausschnitt.jpg` (neu bei neuerer Quelle oder anderer Version)."""
    g = crop_geometry(run_path, c)
    if not g:
        return None
    cache = folder / "kandidaten" / c["id"] / CROP_NAME
    tag = f"v{CROP_VERSION}-{g['stamp']}-{','.join(map(str, g['box']))}".encode()
    try:
        data = cache.read_bytes()
        if data.endswith(b"<!--" + tag + b"-->"):        # Kennung hinter dem JPEG-Ende (Bildbetrachter ignorieren sie)
            return data
    except OSError:
        pass
    import io
    import numpy as np
    from PIL import Image
    with Image.open(g["path"]) as im:
        im = im.convert("RGB").crop(g["box"])
        if g["scale"] < 1:
            im = im.resize((g["width"], g["height"]), Image.LANCZOS)
        a = np.asarray(im, np.float32)
    lum = a.mean(axis=2)
    lo, hi = np.percentile(lum, 1), np.percentile(lum, 99.8)       # strecken: Himmel dunkel, Spur und Sterne hell
    if hi - lo > 4:
        a = np.clip((a - lo) / (hi - lo), 0, 1) ** .85 * 255
    buf = io.BytesIO()
    Image.fromarray(a.astype(np.uint8)).save(buf, "JPEG", quality=82, optimize=True)
    data = buf.getvalue() + b"<!--" + tag + b"-->"
    try:                                                          # atomar ersetzen; schreibgeschützter Lauf → nur ausliefern
        tmp = cache.with_name(f".{CROP_NAME}.{os.getpid()}.{threading.get_ident()}.tmp")
        tmp.write_bytes(data)
        with _lock:
            os.replace(tmp, cache)
    except OSError:
        pass
    return data


# ---------------------------------------------------------------- Details ---
def details(run_path: Path, key: str, norad: int, home: Path, zps=None) -> dict:
    """Infokarte eines Satelliten (klein, erst beim Anklicken geladen). `zps` = Dienst `night_zero_points` oder None."""
    folder, c, session = find(run_path, key)
    sat = _sat_result(c)
    ms = [m for m in sat.get("matches") or [] if isinstance(m, dict)]
    idx, m = next(((i, x) for i, x in enumerate(ms) if str(x.get("norad_id")) == str(norad)), (None, None))
    if m is None:
        raise ValueError("Dieser Satellit gehört nicht zu diesem Kandidaten")
    cat = catalog_entry(home, norad) or {}
    name = m.get("name") or cat.get("OBJECT_NAME") or f"NORAD {norad}"
    intl = cat.get("OBJECT_ID") or intl_from_tle(m.get("tle"))
    launch = cat.get("LAUNCH_DATE") or None
    otype = m.get("object_type") or TYPES.get(cat.get("OBJECT_TYPE") or "", cat.get("OBJECT_TYPE")) or None
    out = dict(ok=True, run=run_path.name, key=key, candidate=dict(id=c["id"], label=c.get("label"), session=session),
               name=name, norad_id=int(norad), intl_id=intl, object_type=otype, kind=kind_of(name, cat.get("OBJECT_TYPE") or None),
               launch_date=launch, launch_year=int(launch[:4]) if launch and launch[:4].isdigit() else
               (int(intl[:4]) if intl and intl[:4].isdigit() else None),
               owner=OWNERS.get(cat.get("OWNER") or "", cat.get("OWNER") or None) if cat.get("OWNER") else None,
               decay_date=cat.get("DECAY_DATE") or None, catalog_source=cat.get("_source"),
               orbit_catalog={k: _num(float(cat[c_])) for k, c_ in (("period_min", "PERIOD"), ("inclination_deg", "INCLINATION"),
                                                                   ("apogee_km", "APOGEE"), ("perigee_km", "PERIGEE"))
                              if re.match(r"^-?\d+(\.\d+)?$", cat.get(c_) or "")} or None,
               tle_age_days=_num(m.get("tle_age_days")), tle_saved=bool(m.get("tle")),
               time=c.get("timestamp_text"), passage_seconds=m.get("passage_seconds"),
               exposure_s=_num(c.get("exposure_seconds")), exposure_start=_num(sat.get("exposure_start")),
               speed=angular_speed(c, m), length_arcmin=_num((c.get("evidence") or {}).get("length_arcmin")),
               certainty=certainty(sat, m, idx), rank=idx, matches=len([x for x in ms if x.get("compatible", True)]),
               links=dict(celestrak=f"https://celestrak.org/satcat/table-satcat.php?CATNR={int(norad)}",
                          n2yo=f"https://www.n2yo.com/satellite/?s={int(norad)}"))
    try:
        out["brightness"] = brightness(run_path, folder, c, m, zps)
    except Exception:                                             # Statistik-Teile fehlen (alter Lauf) → ohne Helligkeit
        out["brightness"] = None
    g = crop_geometry(run_path, c)
    if g:
        from urllib.parse import quote
        out["crop"] = dict(url=f"/api/skytrace/satellite/crop?run={quote(run_path.name, safe='')}&key={quote(key, safe='')}&v={g['stamp'] % 10 ** 9}",
                           width=g["width"], height=g["height"], line=g["line"], kind=g["kind"], frame=g["frame"], time=g["time"])
    else:
        out["crop"] = None
        out["crop_note"] = ("Für diese Spur ist kein Bildausschnitt gespeichert. „Zeitreihen aktualisieren“ im Lauf erzeugt die Ausschnitte neu, "
                            "solange die Rohbilder erreichbar sind.")
    return out
