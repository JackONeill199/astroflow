"""Gemeinsame Ortsliste für ganz AstroFlow: Standorte, Standard-Ort und Zeiträume („von–bis an diesem Ort“).

Gespeichert in `data/settings.json`, Abschnitt `sites`:
  {"list": [{id, name, lat, lon, alt_m, accuracy_m, note}], "default": id,
   "periods": [{id, site_id, from: "JJJJ-MM-TT", to: "JJJJ-MM-TT"|""}], "migrated": {quelle: Zeit}}
Ein Zeitraum mit from == to ist eine einzelne Nacht („heute bin ich in …“, Nachtliste). Nacht-Datum wie im Projekt:
Datum von DATE-OBS − 12 h (UTC). Überlappen Zeiträume, gewinnt der kürzeste (der genaueste).

Auflösung `site_for`: Override je Beobachtung → FITS-Kopf (OBSGEO/SITELAT …) → Zeitraum der Nacht → Standard-Ort.
Rückgabe immer mit `accuracy_m` (Radius in Metern, ~1σ) und `source` – SkyTrace weitet damit die Satelliten-Toleranz aus.

Nur Standardbibliothek: Die SkyTrace-Engine läuft in einem eigenen Interpreter und lädt diese Datei direkt über den Pfad
(Umgebung `ASTROFLOW_SITES` = Pfad zu settings.json). Standorte sind privat – nie ins Repo, Tests nur mit erfundenen Orten.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import re
import secrets
import time
from pathlib import Path

SECTION = "sites"
MERGE_M = 200                      # Migration: Orte näher als 200 m sind derselbe Ort
DEFAULT_ACCURACY_M = 100           # ohne Angabe (Adresssuche, Koordinaten von Hand)
HEADER_ACCURACY_M = 30             # Kamera mit GPS schreibt den Ort in den FITS-Kopf
MAX_SITES = 200
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


# ---------------------------------------------------------------- Hilfen ---
def distance_m(a: dict, b: dict) -> float:
    """Großkreisabstand zweier Orte in Metern (Kugel, für Duplikate und Hinweise genau genug)."""
    p1, p2 = math.radians(a["lat"]), math.radians(b["lat"])
    dl = math.radians(b["lon"] - a["lon"])
    h = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371008.8 * math.asin(min(1.0, math.sqrt(h)))


def night_of(when) -> str | None:
    """Nacht-Datum (JJJJ-MM-TT) aus Unix-Sekunden, datetime oder ISO-Text (UTC): Datum von Zeit − 12 h."""
    if when is None or when == "":
        return None
    if isinstance(when, str):
        if _DATE.match(when):
            return when
        try:
            when = dt.datetime.fromisoformat(when.replace("Z", "+00:00"))
        except ValueError:
            return None
    if isinstance(when, (int, float)):
        when = dt.datetime.fromtimestamp(float(when), dt.timezone.utc)
    if isinstance(when, dt.datetime):
        if when.tzinfo is None:
            when = when.replace(tzinfo=dt.timezone.utc)
        return (when.astimezone(dt.timezone.utc) - dt.timedelta(hours=12)).strftime("%Y-%m-%d")
    return None


def is_date(s) -> bool:
    """JJJJ-MM-TT und ein echtes Kalenderdatum (kein 30. Februar)."""
    if not isinstance(s, str) or not _DATE.match(s):
        return False
    try:
        dt.date.fromisoformat(s)
        return True
    except ValueError:
        return False


def planning_night(now: float, lon: float = 0.0) -> str:
    """Nacht, die „Heute Nacht“ gerade plant (wie tonight._window): Datum nach mittlerer Ortszeit, zwischen Mitternacht
    und 6 Uhr gilt noch die laufende Nacht. Vormittags ist das schon die kommende Nacht – anders als night_of()."""
    local = float(now) + float(lon or 0) / 15 * 3600
    day = int(local // 86400) * 86400
    if local - day < 6 * 3600:
        day -= 86400
    return dt.datetime.fromtimestamp(day, dt.timezone.utc).strftime("%Y-%m-%d")


def _angle(v) -> float | None:
    """Grad aus Zahl oder Sexagesimaltext („49 12 30“, „+49:12:30“, „8d30m“)."""
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().upper().replace(",", ".")
    try:
        return float(s)
    except ValueError:
        pass
    sign = -1.0 if s.startswith("-") or s.endswith(("S", "W")) else 1.0
    parts = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", s)]
    if not parts:
        return None
    return sign * sum(p / 60 ** i for i, p in enumerate(parts[:3]))


def _ecef_to_geo(x: float, y: float, z: float) -> tuple[float, float, float]:
    """OBSGEO-X/Y/Z (m, WGS84) → Breite, Länge, Höhe (Bowring, cm-genau)."""
    a, f = 6378137.0, 1 / 298.257223563
    b, e2 = a * (1 - f), f * (2 - f)
    ep2 = (a * a - b * b) / (b * b)
    p = math.hypot(x, y)
    th = math.atan2(z * a, p * b)
    lat = math.atan2(z + ep2 * b * math.sin(th) ** 3, p - e2 * a * math.cos(th) ** 3)
    n = a / math.sqrt(1 - e2 * math.sin(lat) ** 2)
    alt = p / math.cos(lat) - n if abs(math.cos(lat)) > 1e-9 else abs(z) - b
    return math.degrees(lat), math.degrees(math.atan2(y, x)), alt


def from_header(header) -> dict | None:
    """Standort aus einem FITS-Kopf (oder dict). Stellina schreibt keinen; andere Kameras mit GPS schon."""
    if not header:
        return None
    get = header.get
    if all(get(k) not in (None, "") for k in ("OBSGEO-X", "OBSGEO-Y", "OBSGEO-Z")):
        try:
            lat, lon, alt = _ecef_to_geo(float(get("OBSGEO-X")), float(get("OBSGEO-Y")), float(get("OBSGEO-Z")))
            return valid({"lat": lat, "lon": lon, "alt_m": alt})
        except (TypeError, ValueError):
            pass
    for la, lo, al in (("OBSGEO-B", "OBSGEO-L", "OBSGEO-H"), ("SITELAT", "SITELONG", "SITEELEV"), ("LAT-OBS", "LONG-OBS", "ALT-OBS")):
        if get(la) not in (None, "") and get(lo) not in (None, ""):
            try:
                s = valid({"lat": _angle(get(la)), "lon": _angle(get(lo)), "alt_m": float(get(al) or 0)})
            except (TypeError, ValueError):
                continue
            if s and not (s["lat"] == 0 and s["lon"] == 0):        # 0/0 = Kamera ohne GPS-Fix
                return s
    return None


def valid(site: dict | None) -> dict | None:
    """Prüft und normiert einen Ort; wirft ValueError bei unmöglichen Werten."""
    if not site:
        return None
    lat, lon = float(site["lat"]), float(site["lon"])
    alt = float(site.get("alt_m") or 0)
    acc = site.get("accuracy_m")
    acc = float(acc) if acc not in (None, "") else None
    if not (-90 <= lat <= 90 and -180 <= lon <= 180 and -500 <= alt <= 10000):
        raise ValueError("Ungültige Standortkoordinaten oder Höhe (Breite −90…90°, Länge −180…180°, Höhe −500…10 000 m).")
    if acc is not None and not (0 < acc <= 500_000):
        raise ValueError("Genauigkeit in Metern angeben (größer als 0, höchstens 500 km).")
    out = dict(site, lat=lat, lon=lon, alt_m=alt)
    if acc is not None:
        out["accuracy_m"] = acc
    return out


def _clean_site(s: dict, sid: str | None = None) -> dict:
    s = valid(s)
    name = str(s.get("name") or "").strip()[:80] or "Standort"
    return {"id": str(sid or s.get("id") or _new_id()), "name": name, "lat": round(s["lat"], 6), "lon": round(s["lon"], 6),
            "alt_m": round(s["alt_m"], 1), "accuracy_m": round(s.get("accuracy_m") or DEFAULT_ACCURACY_M, 1),
            "note": str(s.get("note") or "").strip()[:300]}


def _new_id() -> str:
    return "s" + secrets.token_hex(4)


def _clean_period(p: dict, ids: set) -> dict:
    sid = str(p.get("site_id") or "")
    if sid not in ids:
        raise ValueError("Zeitraum verweist auf einen unbekannten Standort.")
    a, b = str(p.get("from") or "").strip(), str(p.get("to") or "").strip()
    if not is_date(a) or (b and not is_date(b)):
        raise ValueError("Zeitraum als gültiges Datum JJJJ-MM-TT angeben (bis darf leer sein = offen).")
    if b and b < a:
        raise ValueError("Das Ende des Zeitraums liegt vor dem Anfang.")
    return {"id": str(p.get("id") or _new_id()), "site_id": sid, "from": a, "to": b}


def normalize(data: dict) -> dict:
    """Abschnitt prüfen (für POST): unbekannte Verweise und doppelte IDs werden abgewiesen."""
    sites = [_clean_site(s) for s in (data.get("list") or [])][:MAX_SITES]
    ids = [s["id"] for s in sites]
    if len(set(ids)) != len(ids):
        raise ValueError("Doppelte Standort-Kennung.")
    periods = [_clean_period(p, set(ids)) for p in (data.get("periods") or [])]
    default = data.get("default")
    if default not in ids:
        default = ids[0] if ids else None
    return {"list": sites, "default": default, "periods": periods}


# ---------------------------------------------------------------- Auflösung ---
def _public(s: dict, source: str, detail: str) -> dict:
    return {"id": s.get("id"), "name": s.get("name") or "Standort", "lat": s["lat"], "lon": s["lon"], "alt_m": s.get("alt_m", 0),
            "accuracy_m": s.get("accuracy_m") or DEFAULT_ACCURACY_M, "source": source, "detail": detail}


def period_for(data: dict, night: str | None) -> dict | None:
    """Zeitraum, der die Nacht enthält (bei Überlappung der kürzeste, dann der zuletzt angelegte)."""
    if not night:
        return None
    best, span = None, None
    for i, p in enumerate(data.get("periods") or []):
        a, b = p.get("from") or "", p.get("to") or ""
        if not is_date(a) or (b and not is_date(b)):              # von Hand verdorbene Datei: Eintrag überspringen
            continue
        if a <= night and (not b or night <= b):
            length = (dt.date.fromisoformat(b) - dt.date.fromisoformat(a)).days if b else 10 ** 6
            if span is None or (length, -i) < span:
                best, span = p, (length, -i)
    return best


def resolve(data: dict, when=None, night: str | None = None, header=None, override: dict | None = None) -> dict | None:
    """Kern der Auflösung auf einem gelesenen Abschnitt (ohne Dateizugriff, für Tests und die Engine)."""
    if override:
        try:
            s = valid(override)
        except (TypeError, ValueError, KeyError):
            s = None
        if s:
            return _public(s, "override", "für diese Beobachtung festgelegt")
    if header is not None:
        s = from_header(header)
        if s:
            acc = None
            for k in ("SITEACC", "GPSACC", "POSACC"):               # keine Norm – einige Kameras schreiben eine Genauigkeit
                try:
                    acc = float(header.get(k)) if header.get(k) not in (None, "") else acc
                except (TypeError, ValueError):
                    pass
            return _public({**s, "name": "Aufnahmestandort (FITS)", "accuracy_m": acc or HEADER_ACCURACY_M}, "fits", "aus den FITS-Metadaten")
        if night is None and when is None:
            when = header.get("DATE-OBS") or header.get("DATE")
    night = night or night_of(when)
    by_id = {s["id"]: s for s in data.get("list") or []}
    p = period_for(data, night)
    if p and p["site_id"] in by_id:
        span = "diese Nacht" if p["from"] == p["to"] else (f"ab {p['from']}" if not p["to"] else f"{p['from']} bis {p['to']}")
        return _public(by_id[p["site_id"]], "period", f"Zeitraum {span}")
    d = by_id.get(data.get("default")) or (next(iter(by_id.values())) if by_id else None)
    if d:
        return _public(d, "default", "Standard-Ort")
    return None


# ---------------------------------------------------------------- Ablage ---
class SiteBook:
    """Lesen/Schreiben des Abschnitts `sites`. Mit SettingsStore (Server) oder nur einem Dateipfad (Engine, nur lesend)."""

    def __init__(self, store=None, path: Path | str | None = None):
        self.store, self.path = store, Path(path) if path else None

    def data(self) -> dict:
        if self.store is not None:
            raw = self.store.get(SECTION)
        else:
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8")).get(SECTION) or {}
            except (OSError, ValueError, AttributeError):
                raw = {}
        return {"list": list(raw.get("list") or []), "default": raw.get("default"), "periods": list(raw.get("periods") or []),
                "migrated": dict(raw.get("migrated") or {})}

    def save(self, data: dict) -> dict:
        clean = normalize(data)
        self.store.update(SECTION, clean)
        return self.data()

    def site_for(self, when=None, night: str | None = None, header=None, override: dict | None = None) -> dict | None:
        return resolve(self.data(), when=when, night=night, header=header, override=override)

    def set_night(self, night: str, site_id: str | None) -> dict:
        """Eine Nacht einem Ort zuordnen (None = Einzelnacht-Zuordnung entfernen, dann gelten Zeiträume/Standard)."""
        if not is_date(night):
            raise ValueError("Nacht als gültiges Datum JJJJ-MM-TT angeben.")
        d = self.data()
        d["periods"] = [p for p in d["periods"] if not (p.get("from") == night and p.get("to") == night)]
        if site_id:
            d["periods"].append({"site_id": site_id, "from": night, "to": night})
        return self.save(d)

    # ---- Migration -----------------------------------------------------------
    def absorb(self, source: str, entries: list[dict], default_first: bool = True) -> int:
        """Einmalige Übernahme alter Standorte (`source` merkt die Herkunft, nie zweimal).
        entries: {name, lat, lon, alt_m?, accuracy_m?, note?, night?}. Orte innerhalb MERGE_M werden zusammengefasst;
        `night` legt eine Einzelnacht an. Alte Felder bleiben unberührt. Rückgabe: Zahl neuer Orte."""
        d = self.data()
        if source in d["migrated"]:
            return 0
        added = 0
        for e in entries:
            try:
                s = valid(e)
            except (TypeError, ValueError, KeyError):
                continue
            if not s:
                continue
            same = next((x for x in d["list"] if distance_m(x, s) <= MERGE_M), None)
            if same is None:
                same = _clean_site(s)
                d["list"].append(same)
                added += 1
            elif not same.get("alt_m") and s.get("alt_m"):
                same["alt_m"] = round(float(s["alt_m"]), 1)
            if e.get("night") and is_date(e["night"]) and not any(p.get("from") == p.get("to") == e["night"] for p in d["periods"]):
                d["periods"].append({"site_id": same["id"], "from": e["night"], "to": e["night"]})
            elif default_first and not d.get("default"):
                d["default"] = same["id"]
        clean = normalize(d) if d["list"] else {"list": [], "default": None, "periods": []}
        self.store.update(SECTION, {**clean, "migrated": {**d["migrated"], source: int(time.time())}})
        return added


def night_from_name(name: str) -> str | None:
    """Datum aus einem Beobachtungsordner („2022-06-11_observation_M31“, „20220611_…“) – sonst None."""
    m = re.search(r"(20\d{2}|19\d{2})-?(\d{2})-?(\d{2})", name or "")
    if not m:
        return None
    try:
        return dt.date(int(m[1]), int(m[2]), int(m[3])).isoformat()
    except ValueError:
        return None


def migrate_tutor(book: SiteBook, store) -> int:
    t = store.get("tutor")
    if t.get("lat") in (None, "") or t.get("lon") in (None, ""):
        book.absorb("tutor", [])
        return 0
    return book.absorb("tutor", [{"name": "Beobachtungsort", "lat": t["lat"], "lon": t["lon"],
                                  "note": "aus dem Bild-Tutor übernommen"}])


def migrate_skytrace(book: SiteBook, cfg: dict, key: str = "skytrace") -> int:
    """SkyTrace `site` (Standard) und `sites[Beobachtung]` übernehmen. Overrides bleiben in SkyTrace bestehen."""
    entries = []
    if isinstance(cfg.get("site"), dict) and cfg["site"].get("lat") is not None:
        s = cfg["site"]
        entries.append({**s, "name": s.get("name") or "SkyTrace-Standort", "note": "aus SkyTrace übernommen"})
    for session, s in (cfg.get("sites") or {}).items():
        if isinstance(s, dict) and s.get("lat") is not None:
            entries.append({**s, "name": s.get("name") or str(session)[:60], "note": f"aus SkyTrace ({str(session)[:80]})",
                            "night": night_from_name(str(session))})
    return book.absorb(key, entries)
