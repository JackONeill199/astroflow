"""Gemeinsame Ortsliste für ganz AstroFlow: Standorte, Standard-Ort und Zeiträume („von–bis an diesem Ort“).

Gespeichert in `data/settings.json`, Abschnitt `sites`:
  {"list": [{id, name, lat, lon, alt_m, accuracy_m, note}], "default": id,
   "periods": [{id, site_id, from: "JJJJ-MM-TT", to: "JJJJ-MM-TT"|"", src?, why?}], "migrated": {quelle: Zeit}}
Ein Zeitraum mit from == to ist eine einzelne Nacht („heute bin ich in …“, Nachtliste). `src` markiert Einzelnächte (und Orte), die ein
Modul automatisch pflegt (`sync_nights`, z. B. SkyTrace-Ausnahmen je Beobachtung) – ohne `src` hat sie der Nutzer angelegt, sie bleiben unberührt. Nacht-Datum wie im Projekt:
`night_of` (Mittag bis Mittag Ortszeit, nahe UTC wie bisher DATE-OBS − 12 h). Überlappen Zeiträume, gewinnt der kürzeste (der genaueste).

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
HORIZON_STEP = 10                  # Horizontmaske: Höhe des freien Himmels alle 10° Azimut (N = 0°, O = 90°)
HORIZON_N = 360 // HORIZON_STEP
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


# ---------------------------------------------------------------- Hilfen ---
def distance_m(a: dict, b: dict) -> float:
    """Großkreisabstand zweier Orte in Metern (Kugel, für Duplikate und Hinweise genau genug)."""
    p1, p2 = math.radians(a["lat"]), math.radians(b["lat"])
    dl = math.radians(b["lon"] - a["lon"])
    h = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371008.8 * math.asin(min(1.0, math.sqrt(h)))


# ------------------------------------------------------------ Nachtdatum ---
# Eine Beobachtungsnacht läuft von Mittag bis Mittag Ortszeit und trägt das Datum ihres Abends. Einzige Stelle, die das
# Nachtdatum aus einer Zeit ableitet – alle Module rufen `night_of` (Pflichtregel in AGENTS.md, Issue #39).
#
# Ortszeit, automatisch und offline, in dieser Reihenfolge:
#   1. Länge des Aufnahmeorts: ausdrücklich (`lon`), aus dem FITS-Kopf (`header`), aus der Ortsliste (Zeitraum der Nacht,
#      sonst Standard-Ort) → mittlere Ortszeit lon/15 h. Orte haben keine Zeitzone; die Sonnenzeit kennt keine Sommerzeit,
#      Mitternacht (Nachtmitte) liegt bis auf ±16 min (Zeitgleichung) genau in der Mitte – die robusteste Grenze.
#   2. sonst Zeitzone (`tz`: tzinfo oder IANA-Name – Letzterer braucht Zeitzonendaten, unter Windows fehlen sie ohne
#      Paket tzdata) bzw. die des Rechners (Betriebssystem, immer verfügbar) – bürgerliche Zeit mit Sommerzeit, je
#      Aufnahme zum Aufnahmezeitpunkt; weicht von der Sonnenzeit höchstens wenige Stunden ab.
#   3. ohne beides (Zeitzone nicht ermittelbar): UTC.
# Verträglichkeit: Liegt die Ortszeit höchstens COMPAT_H Stunden neben UTC (Europa, Afrika, Atlantik), bleibt die Grenze
# bei 12 Uhr UTC (alte Regel „UTC − 12 h“) – dort liegt sie zwischen 9 und 15 Uhr Ortszeit, also am Tag; gespeicherte
# Nächte ändern sich nicht. Weiter östlich/westlich gilt Mittag Ortszeit (USA: Nacht nicht mehr um 4 Uhr geteilt,
# Japan: der Abend landet nicht am Vortag).
COMPAT_H = 3.0
_NIGHT_BOOK = None               # SiteBook, dessen Ortsliste `night_of` ohne Ortsangabe nutzt (Server: create_app)


def use_book(book) -> None:
    """Ortsliste für die automatische Ortszeit festlegen (None = nur Rechnerzeitzone)."""
    global _NIGHT_BOOK
    _NIGHT_BOOK = book


def _instant(when) -> dt.datetime | None:
    """Zeitpunkt (UTC) aus Unix-Sekunden, datetime oder ISO-Text; ohne Zeitzone gilt UTC (FITS DATE-OBS)."""
    if isinstance(when, bool):
        return None
    if isinstance(when, str):
        try:
            when = dt.datetime.fromisoformat(when.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    if isinstance(when, (int, float)):
        try:
            return dt.datetime.fromtimestamp(float(when), dt.timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(when, dt.datetime):
        return (when.replace(tzinfo=dt.timezone.utc) if when.tzinfo is None else when).astimezone(dt.timezone.utc)
    return None


def _tz_hours(t: dt.datetime, tz=None) -> float | None:
    """Abstand Ortszeit − UTC in Stunden zum Zeitpunkt t (mit Sommerzeit): Zeitzone `tz`, sonst die des Rechners."""
    try:
        if isinstance(tz, str):
            from zoneinfo import ZoneInfo
            tz = ZoneInfo(tz)
        off = t.astimezone(tz).utcoffset() if tz is not None else t.astimezone().utcoffset()
        return off.total_seconds() / 3600 if off is not None else None
    except Exception:                    # unbekannte Zone, fehlende tzdata, Zeit außerhalb des Systembereichs
        return None


def night_shift_h(offset_h: float | None) -> float:
    """Verschiebung der Nachtgrenze gegenüber 12 Uhr UTC (Stunden); 0 innerhalb von ±COMPAT_H (alte Regel)."""
    if offset_h is None or not math.isfinite(offset_h) or abs(offset_h) <= COMPAT_H:
        return 0.0
    return float(offset_h)


def _date_at(t: dt.datetime, offset_h: float | None) -> str:
    return (t + dt.timedelta(hours=night_shift_h(offset_h) - 12)).strftime("%Y-%m-%d")


def _lon(v) -> float | None:
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) and -180 <= v <= 180 else None


def night_in(data: dict | None, t: dt.datetime, tz=None) -> str:
    """Nacht zum Zeitpunkt t (UTC) nach der Ortsliste `data`: vorläufig mit dem Standard-Ort (ohne Orte: Zeitzone `tz`
    bzw. des Rechners). Gilt in dieser Nacht oder einer direkt daneben ein Zeitraum mit einem anderen Ort (Reise), zählt
    dessen Ortszeit – aber nur, wenn derselbe Ort auch für die damit bestimmte Nacht gilt (kein Pingpong an Grenzen)."""
    data = data or {}
    by_id = {s.get("id"): s for s in data.get("list") or [] if isinstance(s, dict)}
    home = by_id.get(data.get("default")) or (next(iter(by_id.values())) if by_id else None)
    off = _lon(home.get("lon")) / 15 if home and _lon(home.get("lon")) is not None else _tz_hours(t, tz)
    night = _date_at(t, off)
    if not data.get("periods"):
        return night
    d0 = dt.date.fromisoformat(night)
    for k in (0, -1, 1):              # Reise: vorläufige Nacht kann um einen Tag neben dem Zeitraum des Reiseorts liegen
        p = period_for(data, (d0 + dt.timedelta(days=k)).isoformat())
        site = by_id.get(p["site_id"]) if p else None
        if not site or _lon(site.get("lon")) is None:
            continue
        other = _date_at(t, _lon(site["lon"]) / 15)
        if other == night:
            continue
        q = period_for(data, other)
        if q and q.get("site_id") == site.get("id"):          # nur wenn der Ort auch für die neue Nacht gilt
            return other
    return night


def night_of(when, lon=None, tz=None, header=None, book=None) -> str | None:
    """Nacht-Datum (JJJJ-MM-TT) einer Aufnahmezeit – Unix-Sekunden, datetime oder ISO-Text (ohne Zone = UTC);
    ein fertiges Datum JJJJ-MM-TT kommt unverändert zurück. Ortszeit: `lon` (Grad Ost) → Ort im FITS-Kopf `header`
    → `tz` → Ortsliste (`book`, sonst die per `use_book` gesetzte) → Rechnerzeitzone → UTC. Siehe Abschnittskopf."""
    if when is None or when == "":
        return None
    if isinstance(when, str) and _DATE.match(when.strip()) and len(when.strip()) == 10:
        return when.strip()
    t = _instant(when)
    if t is None:
        return None
    lon = _lon(lon)
    if lon is None and header:
        s = from_header(header)
        lon = _lon(s.get("lon")) if s else None
    if lon is not None:
        return _date_at(t, lon / 15)
    if tz is not None:
        return _date_at(t, _tz_hours(t, tz))
    book = book if book is not None else _NIGHT_BOOK
    if book is not None:
        try:
            return night_in(book.cached_data(), t)
        except Exception:                 # Ortsliste unlesbar: Nacht trotzdem bestimmen
            pass
    return _date_at(t, _tz_hours(t))


def clock_of(when, lon=None, book=None) -> str | None:
    """Uhrzeit „HH:MM“ (Ortszeit) einer Aufnahme für die Anzeige – gleiche Quellen wie `night_of`, aber Uhrzeit statt
    Sonnenzeit: Rechnerzeitzone mit Sommerzeit, solange der Aufnahmeort (`lon`, sonst Ortsliste) dazu passt; liegt er
    über 2,5 h daneben (Reise in eine andere Zeitzone), die zur Länge gehörige ganze Stunde. Ohne alles: UTC."""
    t = _instant(when)
    if t is None:
        return None
    lon = _lon(lon)
    book = book if book is not None else _NIGHT_BOOK
    if lon is None and book is not None:
        try:
            s = resolve(book.cached_data(), when=t)
            lon = _lon(s.get("lon")) if s else None
        except Exception:                 # Ortsliste unlesbar: Rechnerzeitzone
            pass
    off = _tz_hours(t)
    if lon is not None and (off is None or abs(lon / 15 - off) > 2.5):
        off = float(round(lon / 15))
    return (t + dt.timedelta(hours=off or 0)).strftime("%H:%M")


def sun_alt(when, lat=None, lon=None) -> float | None:
    """Sonnenhöhe in Grad zum Aufnahmezeitpunkt (Genauigkeit ~1°, reicht für Dämmerung/Nacht). Ohne Ort: Näherung mit
    45° Breite und der Länge aus der Rechnerzeitzone – nur für die Darstellung „Stand der Nacht“ (Aufnahmen-Diagramm)."""
    t = _instant(when)
    if t is None:
        return None
    lat, lon = _lon(lat), _lon(lon)
    if lat is None or lon is None or abs(lat) > 90:
        off = _tz_hours(t) or 0.0
        try:                                  # Normalzeit ohne Sommerzeit ≈ Zonenmeridian
            off -= (t.astimezone().dst() or dt.timedelta()).total_seconds() / 3600
        except (OverflowError, OSError, ValueError):
            pass
        lat, lon = 45.0, off * 15
    d = (t - dt.datetime(2000, 1, 1, 12, tzinfo=dt.timezone.utc)).total_seconds() / 86400
    g = math.radians((357.528 + 0.9856003 * d) % 360)
    lam = math.radians((280.460 + 0.9856474 * d) % 360 + 1.915 * math.sin(g) + 0.020 * math.sin(2 * g))
    eps = math.radians(23.439)
    ra, dec = math.atan2(math.cos(eps) * math.sin(lam), math.cos(lam)), math.asin(math.sin(eps) * math.sin(lam))
    lst = math.radians((280.46061837 + 360.98564736629 * d + lon) % 360)
    p = math.radians(lat)
    return math.degrees(math.asin(math.sin(p) * math.sin(dec) + math.cos(p) * math.cos(dec) * math.cos(lst - ra)))


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


def clean_horizon(h) -> list[float] | None:
    """Horizontmaske prüfen: 36 Höhen in Grad (0–90) ab Norden über Osten; alles 0 bzw. leer = freier Horizont (None)."""
    if not h:
        return None
    if not isinstance(h, (list, tuple)) or len(h) != HORIZON_N:
        raise ValueError(f"Horizont: {HORIZON_N} Höhenwerte (alle {HORIZON_STEP}°) erwartet.")
    try:
        out = [round(min(90.0, max(0.0, float(v))), 1) for v in h]
    except (TypeError, ValueError):
        raise ValueError("Horizont: Höhen als Zahlen in Grad angeben.") from None
    if any(v != v for v in out):                                  # NaN
        raise ValueError("Horizont: Höhen als Zahlen in Grad angeben.")
    return out if any(out) else None


def horizon_at(h: list | None, az: float) -> float:
    """Höhe der Horizontmaske bei Azimut `az` (Grad, linear zwischen den Stützstellen); ohne Maske 0."""
    if not h:
        return 0.0
    x = (az % 360) / HORIZON_STEP
    i = int(x) % HORIZON_N
    f = x - int(x)
    return h[i] * (1 - f) + h[(i + 1) % HORIZON_N] * f


def _clean_site(s: dict, sid: str | None = None) -> dict:
    s = valid(s)
    name = str(s.get("name") or "").strip()[:80] or "Standort"
    out = {"id": str(sid or s.get("id") or _new_id()), "name": name, "lat": round(s["lat"], 6), "lon": round(s["lon"], 6),
           "alt_m": round(s["alt_m"], 1), "accuracy_m": round(s.get("accuracy_m") or DEFAULT_ACCURACY_M, 1),
           "note": str(s.get("note") or "").strip()[:300]}
    hz = clean_horizon(s.get("horizon"))
    if hz:
        out["horizon"] = hz
    return _with_src(out, s)


def _with_src(out: dict, raw: dict) -> dict:
    """Herkunftsmarke automatisch gepflegter Einträge (`src`, Kurztext `why`) übernehmen."""
    for k, n in (("src", 40), ("why", 160)):
        v = str(raw.get(k) or "").strip()[:n]
        if v:
            out[k] = v
    return out


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
    return _with_src({"id": str(p.get("id") or _new_id()), "site_id": sid, "from": a, "to": b}, p)


def normalize(data: dict) -> dict:
    """Abschnitt prüfen (für POST): unbekannte Verweise und doppelte IDs werden abgewiesen."""
    sites = [_clean_site(s) for s in (data.get("list") or [])][:MAX_SITES]
    ids = [s["id"] for s in sites]
    if len(set(ids)) != len(ids):
        raise ValueError("Doppelte Standort-Kennung.")
    periods = [_clean_period(p, set(ids)) for p in (data.get("periods") or [])]
    default = data.get("default")
    if default not in ids:                  # automatisch angelegte Orte (`src`) nie stillschweigend zum Standard machen
        default = next((s["id"] for s in sites if not s.get("src")), None)
    return {"list": sites, "default": default, "periods": periods}


# ---------------------------------------------------------------- Auflösung ---
def _public(s: dict, source: str, detail: str, by: str | None = None) -> dict:
    out = {"id": s.get("id"), "name": s.get("name") or "Standort", "lat": s["lat"], "lon": s["lon"], "alt_m": s.get("alt_m", 0),
           "accuracy_m": s.get("accuracy_m") or DEFAULT_ACCURACY_M, "source": source, "detail": detail}
    if s.get("horizon"):
        out["horizon"] = s["horizon"]               # Horizontmaske des Ortes (Heute Nacht, Observatorium)
    if by:
        out["by"] = by                              # automatisch gepflegte Einzelnacht (z. B. "skytrace")
    return out


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


def shadowed_periods(data: dict) -> list[str]:
    """IDs von Zeiträumen, die in keiner Nacht gelten, weil ein kürzerer sie vollständig überdeckt.
    Der Sieger wechselt nur an Anfang/Ende eines Zeitraums – dort (und je einen Tag davor/danach) wird geprüft."""
    ps = [p for p in data.get("periods") or [] if is_date(p.get("from") or "") and (not p.get("to") or is_date(p["to"]))]
    probes = set()
    for p in ps:
        for key in ("from", "to"):
            if p.get(key):
                d = dt.date.fromisoformat(p[key])
                probes.update((d + dt.timedelta(days=k)).isoformat() for k in (-1, 0, 1))
    winners = {id(period_for(data, n)) for n in probes}
    return [p["id"] for p in ps if id(p) not in winners and p.get("id")]


def unused_sites(data: dict) -> list[str]:
    """IDs von Orten, die nirgends gelten: nicht der Standard-Ort und in keinem Zeitraum/keiner Einzelnacht genannt."""
    by_id = [s.get("id") for s in data.get("list") or []]
    default = data.get("default") if data.get("default") in by_id else (by_id[0] if by_id else None)
    used = {p.get("site_id") for p in data.get("periods") or []} | {default}
    return [i for i in by_id if i not in used]


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
    if not night:
        t = None if isinstance(when, str) and _DATE.match(when.strip()) else _instant(when)
        night = night_in(data, t) if t is not None else night_of(when)       # Ortszeit nach dieser Ortsliste
    by_id = {s["id"]: s for s in data.get("list") or []}
    p = period_for(data, night)
    if p and p["site_id"] in by_id:
        span = "diese Nacht" if p["from"] == p["to"] else (f"ab {p['from']}" if not p["to"] else f"{p['from']} bis {p['to']}")
        detail = f"Zeitraum {span}" + (f" · {p['why']}" if p.get("why") else "")
        return _public(by_id[p["site_id"]], "period", detail, p.get("src"))
    d = by_id.get(data.get("default")) or next((s for s in by_id.values() if not s.get("src")), None)
    if d:
        return _public(d, "default", "Standard-Ort")
    return None


# ---------------------------------------------------------------- Ablage ---
class SiteBook:
    """Lesen/Schreiben des Abschnitts `sites`. Mit SettingsStore (Server) oder nur einem Dateipfad (Engine, nur lesend)."""

    CACHE_S = 5.0                      # Nachtbestimmung je Aufnahme: Datei höchstens alle 5 s neu lesen

    def __init__(self, store=None, path: Path | str | None = None):
        self.store, self.path = store, Path(path) if path else None
        self._cache: tuple[float, dict] | None = None
        self.on_change = None          # Server: () → Ereignis `sites.changed` (Bilder rechnet betroffene Nächte neu)

    def _changed(self) -> None:
        self._cache = None
        if self.on_change:
            try:
                self.on_change()
            except Exception:
                import traceback
                traceback.print_exc()

    def cached_data(self) -> dict:
        """`data()` mit kurzem Zwischenspeicher (für viele Aufrufe von `night_of` hintereinander)."""
        now = time.monotonic()
        c = self._cache
        if c is None or now - c[0] > self.CACHE_S:
            c = self._cache = (now, self.data())
        return c[1]

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
        self._changed()
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
        if added or clean.get("periods"):
            self._changed()
        return added

    # ---- automatisch gepflegte Einzelnächte ------------------------------------
    def sync_nights(self, src: str, wanted: dict[str, dict], claim: str | None = None) -> dict:
        """Einzelnächte eines Moduls abgleichen (z. B. SkyTrace-Ausnahmen je Beobachtung → Nacht in Bilder).
        wanted: {Nacht: {name, lat, lon, alt_m?, accuracy_m?, note?, why?}}. Einzelnächte mit `src` werden angelegt, umgestellt
        oder entfernt; Einzelnächte des Nutzers (ohne `src`) bleiben – außer beim einmaligen `claim` (Marke in `migrated`), der
        frühere Übernahmen ohne Marke mit gleichem Ort (≤ MERGE_M) als eigene kennzeichnet. Orte ≤ MERGE_M werden
        wiederverwendet, neue Orte bekommen `src` und verschwinden wieder, sobald sie nirgends mehr gelten.
        Rückgabe {added, updated, removed, user: [Nächte des Nutzers, die Vorrang behalten]}."""
        d = self.data()
        do_claim = bool(claim) and claim not in d["migrated"]
        by_id = {s["id"]: s for s in d["list"]}
        n_sites = len(d["list"])
        out = {"added": 0, "updated": 0, "removed": 0, "user": []}

        def site_id(e: dict) -> str | None:
            try:
                s = valid(e)
            except (TypeError, ValueError, KeyError):
                return None
            if not s:
                return None
            same = next((x for x in d["list"] if distance_m(x, s) <= MERGE_M), None)
            if same is None:
                if len(d["list"]) >= MAX_SITES:
                    return None
                same = _clean_site({**s, "src": src, "note": e.get("note") or "automatisch angelegt"})
                d["list"].append(same)
                by_id[same["id"]] = same
            return same["id"]

        keep, seen = [], set()
        for p in d["periods"]:
            night = p.get("from")
            if not (night and night == p.get("to")):
                keep.append(p)
                continue
            seen.add(night)
            mine = p.get("src") == src
            if not mine and do_claim and night in wanted and p.get("site_id") in by_id \
                    and distance_m(by_id[p["site_id"]], wanted[night]) <= MERGE_M:
                p, mine = {**p, "src": src}, True
            if not mine:
                keep.append(p)
                if night in wanted:
                    out["user"].append(night)
                continue
            sid = site_id(wanted[night]) if night in wanted else None
            if sid is None:
                out["removed"] += 1
                continue
            why = str(wanted[night].get("why") or "")[:160]
            if sid != p.get("site_id") or why != (p.get("why") or ""):
                out["updated"] += 1
            keep.append({**p, "site_id": sid, "src": src, "why": why})
        for night, e in sorted(wanted.items()):
            if night in seen or not is_date(night):
                continue
            sid = site_id(e)
            if sid:
                keep.append({"site_id": sid, "from": night, "to": night, "src": src, "why": str(e.get("why") or "")[:160]})
                out["added"] += 1
        d["periods"] = keep
        used = {p.get("site_id") for p in keep} | {d.get("default")}
        d["list"] = [s for s in d["list"] if s.get("src") != src or s["id"] in used]
        changed = bool(out["added"] or out["updated"] or out["removed"] or len(d["list"]) != n_sites)
        if changed or do_claim:
            clean = normalize(d) if d["list"] else {"list": [], "default": None, "periods": []}
            migrated = {**d["migrated"], **({claim: int(time.time())} if do_claim else {})}
            self.store.update(SECTION, {**clean, "migrated": migrated})
            if changed:
                self._changed()
            else:
                self._cache = None
        return out


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
