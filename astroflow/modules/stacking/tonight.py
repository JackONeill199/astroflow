"""Heute Nacht: Dämmerung, Mond, Höhenverlauf der eigenen Objekte, Empfehlungen, Nachtplan und Saison.

Alles offline mit astropy (IERS-Download aus, wie im Bild-Tutor). Standort aus Einstellungen → Bild-Tutor (tutor.lat/lon);
ohne Standort liefert plan() nur {"site": None}. Wolkenvorhersage (Open-Meteo, Ort auf 0,1° gerundet, 8 Tage, Wolkenschichten
und Wind) nur, wenn im Tutor „Wetter“ eingeschaltet ist – Cache 1 h in work/tutor/. Zeiten als Unix-Sekunden (UTC); die Oberfläche
zeigt Ortszeit. `night=k` plant k Nächte später (bis NIGHTS-1); `nights` fasst jede Nacht für die Nachtleiste zusammen.

Empfehlungen (seit 0.20.0):
* **Neue Ziele** aus `deepsky` (Messier + Caldwell fest, dazu ASTAP deep_sky.csv), gefiltert nach Helligkeit (je Gerät) und
  Größe zum Bildfeld; eigene Objekte werden über Katalog-Kennungen (M31 = NGC 224) und Position ausgeschlossen.
* **Mehr Zeit** für eigene Objekte mit wenig Belichtung/Rauschen; Gewinn nach der √t-Regel aus bisheriger Belichtung und den
  heute nutzbaren Stunden (`_gain`).
* **Rangfolge:** nutzbare Stunden in der dunklen Zeit, gewichtet mit Mond (Phase × Abstand), Wolken (falls Vorhersage an) und
  Zenitnähe (> 80° bei Alt-Az-Geräten: Feldrotation); Kulmination im Dunkeln; Auswahl mischt Objektarten und Kategorien.
* **Saison:** Stunden im Dunkeln über Mindesthöhe für die nächsten Wochen → „Letzte Gelegenheit“ bzw. „bald gut sichtbar“.
* **Nachtplan:** ein Ziel zur Zeit (Smart-Teleskop): Nacht in 1–4 Abschnitte, jedem das am besten stehende Ziel.
"""
from __future__ import annotations

import datetime as dt
import itertools
import json
import math
import time
import warnings
from pathlib import Path

import numpy as np

from . import deepsky, objects, tutor

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
STEP_MIN = 10            # Abtastung der Nacht
MIN_ALT = 30.0           # „gut sichtbar“ ab dieser Höhe
ZENITH_ALT = 80.0        # darüber starke Feldrotation bei Alt-Az-Montierung
ZENITH_WEIGHT = 0.4      # Gewicht dieser Stunden in der Rangfolge
MAX_CURVES = 6           # maximale Anzahl Empfehlungen in der Grafik
BLOCKED_PENALTY = 1.5    # Ziele, die nur wegen Wolken ausfallen, stehen hinter klar erreichbaren
MIN_GAIN = 0.10          # „Mehr Zeit“ nur, wenn das Rauschen heute um ≥ 10 % sinken kann
MIN_BLOCK = 2400         # Nachtplan: kürzere Blöcke entfallen (s)
NIGHTS = 14             # so viele Nächte kann man vorblättern (Astronomie immer, Wetter soweit die Vorhersage reicht)
FORECAST_DAYS = 8        # Vorhersage-Tage (Open-Meteo, gleicher Dienst und gleiche Ortsrundung wie bisher)
UNCERTAIN_FROM = 2       # ab der dritten Nacht ist die Wolkenvorhersage unsicher
OFFSETS = list(range(0, 197, 14))      # Tage ab heute für den Saison-Ausblick (bis ~6 Monate)
MONTHS = ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober", "November", "Dezember"]
_cache: dict = {}
_nights_cache: dict = {}
_CACHE_MAX = 4


def library(db) -> tuple[list[dict], dict]:
    """Nur gespeicherte Messwerte lesen, keine Bilder öffnen oder Tutor-/KI-Aufträge starten."""
    rows = db.q("SELECT p.id, p.name, p.kind, p.ra, p.dec, p.obj_id, r.exposure, "
                "json_extract(r.stats,'$.quality') quality, json_extract(r.stats,'$.tutor.findings') findings "
                "FROM projects p LEFT JOIN results r ON r.id="
                "(SELECT MAX(id) FROM results WHERE project_id=p.id) "
                "WHERE json_extract(p.options,'$.gallery_parent') IS NULL")
    for p in rows:
        p["stats"] = {"quality": json.loads(p.pop("quality") or "{}"),
                      "tutor": {"findings": json.loads(p.pop("findings") or "[]")}}
        cat = objects.resolve_name(p.get("obj_id") or p["name"])
        if cat:
            p["obj_id"] = cat.id
        if p["ra"] is None or p["dec"] is None:
            ext = cat or objects.resolve_extended(p.get("obj_id") or p["name"])
            if ext:
                p["ra"], p["dec"] = ext.ra, ext.dec
    # Das zuletzt verwendete Gerät; ein Frame genügt, auch wenn seine Rohdatei schon gelöscht ist.
    f = db.one("SELECT info FROM frames WHERE kind='light' AND project_id IS NOT NULL "
               "ORDER BY night DESC, rowid DESC LIMIT 1")
    info = json.loads(f["info"] or "{}") if f else {}
    device = tutor.detect_device([info.get("instrument")], info.get("shape"))
    sol = info.get("solve") or {}
    field = [sol.get("fov_w"), sol.get("fov_h")]
    source = "Plate-Solving der letzten Aufnahme"
    if not all(isinstance(v, (int, float)) and np.isfinite(v) and v > 0 for v in field):
        # Gesicherter Rückfall aus tutor.DEVICES, keine pauschalen Werte für Gerätefamilien.
        field = [1.0, 0.7] if device["id"] == "stellina" else None
        source = "ungefähres Stellina-Bildfeld" if field else None
    return rows, {"id": device["id"], "name": device["name"], "alt_az": bool(device.get("alt_az")), "fov": field, "source": source,
                  "mag_limit": deepsky.mag_limit(device)}


def _need(p: dict) -> list[str]:
    """Mehr Zeit hilft bei Rauschen/Signal, nicht automatisch bei jedem schlechten Qualitätswert."""
    stats = p.get("stats") or {}
    quality = stats.get("quality") or {}
    codes = {x.get("code") for x in quality.get("issues", [])}
    findings = (stats.get("tutor") or {}).get("findings") or []
    codes.update(x.get("code") for x in findings if x.get("level") in ("warn", "bad"))
    reasons = []
    exp = p.get("exposure")
    if exp is not None and 0 < exp < 3600:
        reasons.append("Bisher weniger als eine Stunde belichtet.")
    if codes & {"noisy", "chroma_noise", "faint"}:
        reasons.append("Die Bildprüfung meldet Rauschen oder schwaches Signal.")
    snr = (quality.get("measure") or {}).get("snr")
    if snr is not None and 0 < snr < 10 and not reasons:
        reasons.append("Das gemessene Objekt-Signal ist noch schwach.")
    return reasons


def _dur(sec: float) -> str:
    """„37 min“, „5 h“, „1,5 h“ – für Texte aus dem Server."""
    if sec < 3600:
        return f"{max(1, round(sec / 60))} min"
    h = round(sec / 1800) / 2
    return (f"{h:.1f}".replace(".", ",") if h != int(h) else f"{int(h)}") + " h"


def _gain(exposure: float | None, hours: float, today: bool = True) -> dict | None:
    """Rauschgewinn durch heute mögliche Zusatzzeit. Zufälliges Rauschen im Stack sinkt mit 1/√t (gleiche Aufnahmequalität
    vorausgesetzt): t_neu = t + Zusatz → Rauschen × √(t / t_neu). Ohne bekannte Belichtung kein Wert."""
    if not exposure or exposure <= 0 or hours <= 0:
        return None
    add = round(hours * 2) / 2 * 3600                       # auf halbe Stunden gerundet
    if add <= 0:
        return None
    pct = 1 - math.sqrt(exposure / (exposure + add))
    return {"before": float(exposure), "add": float(add), "after": float(exposure + add), "noise_pct": round(pct * 100),
            "text": f"Bisher {_dur(exposure)}, {'heute' if today else 'in dieser Nacht'} bis zu {_dur(add)} möglich → Rauschen etwa −{round(pct * 100)} % "
                    f"(Rauschen sinkt mit 1/√Zeit)."}


def _window(now: float, lon: float) -> tuple[float, float]:
    """Mittag (mittlere Ortszeit) vor der kommenden Nacht bis Mittag danach. Zwischen Mitternacht und 6 Uhr Ortszeit
    gilt noch die laufende Nacht."""
    local = now + lon / 15 * 3600
    day = int(local // 86400) * 86400
    if local - day < 6 * 3600:
        day -= 86400
    noon = day + 12 * 3600 - lon / 15 * 3600
    return noon, noon + 86400


def _cross(t: np.ndarray, alt: np.ndarray, level: float, down: bool) -> float | None:
    """Erster Zeitpunkt, an dem alt `level` abwärts (down) bzw. aufwärts kreuzt – linear interpoliert."""
    a, b = alt[:-1] - level, alt[1:] - level
    idx = np.where((a > 0) & (b <= 0) if down else (a < 0) & (b >= 0))[0]
    if not len(idx):
        return None
    i = idx[0]
    return float(t[i] + (t[i + 1] - t[i]) * a[i] / (a[i] - b[i]))


def _spans(t: np.ndarray, ok: np.ndarray) -> list[list[float]]:
    out, start = [], None
    for ti, o in zip(t, ok):
        if o and start is None:
            start = ti
        elif not o and start is not None:
            out.append([float(start), float(ti)]); start = None
    if start is not None:
        out.append([float(start), float(t[-1])])
    return out


HOURLY = "cloud_cover,cloud_cover_low,cloud_cover_mid,cloud_cover_high,wind_speed_10m,relative_humidity_2m,temperature_2m,dew_point_2m"


def _forecast(site, cache: Path, t0: float, t1: float, timeout: float = 10.0) -> dict | None:
    """Stündliche Vorhersage für [t0, t1] (hours) und die 48 h danach (later, nur Wolken). Ort auf 0,1° gerundet; dieselbe
    Abfrage (höchstens eine je Stunde und Ort) deckt alle vorblätterbaren Nächte ab."""
    import httpx
    lat, lon = round(site[0], 1), round(site[1], 1)
    cache.mkdir(parents=True, exist_ok=True)
    f = cache / f"vorhersage{FORECAST_DAYS}_{lat}_{lon}.json"
    data = None
    if f.exists() and time.time() - f.stat().st_mtime < 3600:
        data = json.loads(f.read_text(encoding="utf-8"))
    else:
        r = httpx.get(FORECAST_URL, params={"latitude": lat, "longitude": lon, "hourly": HOURLY,
                                            "forecast_days": FORECAST_DAYS, "timezone": "UTC"}, timeout=timeout)
        r.raise_for_status()
        data = r.json().get("hourly") or {}
        if data.get("time"):
            f.write_text(json.dumps(data), encoding="utf-8")
    hours, later = [], []
    for i, s in enumerate(data.get("time") or []):
        ts = dt.datetime.fromisoformat(s).replace(tzinfo=dt.timezone.utc).timestamp()
        g = lambda k: (data.get(k) or [None] * (i + 1))[i]
        if t1 + 1800 < ts <= t1 + 48 * 3600 and g("cloud_cover") is not None:
            later.append({"t": ts, "cloud": g("cloud_cover")})
        if t0 - 1800 <= ts <= t1 + 1800:
            hours.append({"t": ts, "cloud": g("cloud_cover"), "low": g("cloud_cover_low"), "mid": g("cloud_cover_mid"), "high": g("cloud_cover_high"),
                          "wind": g("wind_speed_10m"), "humidity": g("relative_humidity_2m"),
                          "dew_spread": None if g("temperature_2m") is None or g("dew_point_2m") is None else round(g("temperature_2m") - g("dew_point_2m"), 1)})
    return {"hours": hours, "later": later, "source": "Open-Meteo-Vorhersage (Raster ~10 km)"} if hours else None


def _series(ts: np.ndarray, hours: list[dict], key: str) -> np.ndarray | None:
    """Stündliche Werte auf die Zeitachse legen; außerhalb der Vorhersage (±1 h) NaN."""
    hh = [h for h in hours if h.get(key) is not None]
    if not hh:
        return None
    v = np.interp(ts, [h["t"] for h in hh], [h[key] for h in hh])
    return np.where((ts < hh[0]["t"] - 3600) | (ts > hh[-1]["t"] + 3600), np.nan, v)


def _rounded(v: np.ndarray | None, sel) -> list | None:
    return None if v is None else [None if np.isnan(x) else round(float(x)) for x in v[sel]]


# ------------------------------------------------------------------ Geometrie (numpy, ohne AltAz-Transformation) #
def _alt(ra: np.ndarray, dec: np.ndarray, lst: np.ndarray, lat: float) -> np.ndarray:
    """Höhe in Grad: Objekte (N) × Zeiten (T). ra/dec in rad, lst in rad (Ortssternzeit), lat in Grad."""
    p = math.radians(lat)
    s = math.sin(p) * np.sin(dec)[:, None] + math.cos(p) * np.cos(dec)[:, None] * np.cos(lst[None, :] - ra[:, None])
    return np.degrees(np.arcsin(np.clip(s, -1, 1)))


def _sep(ra: np.ndarray, dec: np.ndarray, mra: np.ndarray, mdec: np.ndarray) -> np.ndarray:
    """Winkelabstand in Grad: Objekte (N) × Zeiten (T)."""
    c = np.sin(dec)[:, None] * np.sin(mdec)[None, :] + np.cos(dec)[:, None] * np.cos(mdec)[None, :] * np.cos(ra[:, None] - mra[None, :])
    return np.degrees(np.arccos(np.clip(c, -1, 1)))


def _season_word(month: int, lat: float) -> str:
    m = month if lat >= 0 else (month + 5) % 12 + 1          # Südhalbkugel: Jahreszeiten um ein halbes Jahr versetzt
    return "Winter" if m in (12, 1, 2) else "Frühjahr" if m in (3, 4, 5) else "Sommer" if m in (6, 7, 8) else "Herbst"


def _date(ts: float) -> dt.date:
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).date()


def _season(h: np.ndarray, t0: float, lat: float) -> dict | None:
    """h: Stunden im Dunkeln über MIN_ALT in der Nacht von heute + OFFSETS. Letzte Gelegenheit / bald gut sichtbar."""
    h0 = h[0]
    day = lambda i: _date(t0 + OFFSETS[i] * 86400 + 43200)
    if h0 >= 2.5:
        # Verschwindet innerhalb von ~8 Wochen fast aus dem dunklen Fenster: Stunden sinken auf ≤ 40 % (mindestens aber unter 3 h)
        i = next((k for k in range(1, 5) if h[k] <= max(1.5, 0.4 * h0) and h[k] < 3), None)
        if i is None:
            return None
        back = next((k for k in range(i + 1, len(h)) if h[k] >= 3), None)
        until = f"bis zum {_season_word(day(back).month, lat)}" if back is not None else "für diese Saison"
        return {"kind": "last", "from": f"{day(i):%d.%m.}",
                "text": f"Letzte Gelegenheit {until}: ab etwa {day(i):%d.%m.} nur noch rund {math.floor(h[i])} h im Dunkeln über {MIN_ALT:.0f}°."}
    if h0 >= 2.5:
        return None
    i = next((k for k in range(1, 5) if h[k] >= max(3, 2 * h0 + 1)), None)
    if i is None:
        return None
    return {"kind": "soon", "from": f"{day(i):%d.%m.}",
            "text": f"Bald gut sichtbar: ab etwa {day(i):%d.%m.} über {math.floor(h[i])} h im Dunkeln über {MIN_ALT:.0f}°."}


def plan(projects: list[dict], cfg: dict, cache: Path, now: float | None = None, device: dict | None = None,
         hidden: set | frozenset | None = None, collection: dict | None = None, night: int = 0,
         night_sites: list | None = None) -> dict:
    """projects: Objektzeilen mit exposure (Sekunden des neuesten Ergebnisses) und optional gespeicherten Befunden.
    hidden: Kennungen (Katalog-ID bzw. Projekt-ID als Text), die der Nutzer ausgeblendet hat.
    collection: optionaler Dienst „collection_progress“ (Sage): {"messier": [Nummern bereits entdeckter Messier-Objekte]}.
    night: 0 = die Nacht, die gerade geplant wird (wie bisher), 1 … NIGHTS-1 = so viele Nächte später.
    night_sites: optional je Nacht [lat, lon] aus der Ortsliste (Nachtleiste rechnet jede Nacht an ihrem Ort); fehlt = überall `site`."""
    if cfg.get("lat") is None or cfg.get("lon") is None:
        return {"site": None}
    now = time.time() if now is None else now
    device = device or {"id": "camera", "name": "Gerät unbekannt", "alt_az": False, "fov": None, "source": None, "mag_limit": deepsky.MAG_DEFAULT}
    hidden = frozenset(str(h) for h in (hidden or ()))
    collection = collection or {}
    site = [float(cfg["lat"]), float(cfg["lon"])]
    night = max(0, min(NIGHTS - 1, int(night or 0)))
    base0, _ = _window(now, site[1])
    t0, t1 = base0 + night * 86400, base0 + (night + 1) * 86400
    ref = max(now, t0)                                   # „ab jetzt“ – in späteren Nächten zählt die ganze Nacht
    night_sites = [[float(v) for v in s] if s else site for s in (night_sites or [])][:NIGHTS]
    night_sites += [site] * (NIGHTS - len(night_sites))
    key = (str(cache.resolve()), tuple(site), int(now // 600), night, json.dumps(night_sites), json.dumps(projects, sort_keys=True), json.dumps(device, sort_keys=True),
           bool(cfg.get("weather")), tuple(sorted(hidden)), json.dumps(collection, sort_keys=True), deepsky.signature())
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < 600:
        return {**hit[1], "now": now}

    from astropy import units as u
    from astropy.coordinates import EarthLocation, get_body
    from astropy.time import Time
    from astropy.utils import iers

    ts = np.arange(t0, t1 + 1, STEP_MIN * 60, dtype=float)
    own = [p for p in projects if p.get("ra") is not None and p.get("dec") is not None]
    owned_keys: set[str] = set()
    for p in projects:
        owned_keys |= deepsky.keys_for(p.get("obj_id")) | deepsky.keys_for(p.get("name"))
    owned_m = {int(n) for n in collection.get("messier") or []}
    # Größe eigener Objekte aus dem Katalog: Begleiter im selben Feld (M32/M110 bei M31) sind kein neues Ziel
    own_r = [max(0.3, max([t.size for t in deepsky.catalog() if t.keys & (deepsky.keys_for(p.get("obj_id")) | deepsky.keys_for(p.get("name")))] or [0]) / 120)
             for p in own]
    cands = [c for c in deepsky.catalog()
             if not (c.keys & owned_keys) and not (c.source == "messier" and c.number in owned_m) and c.id not in hidden
             and deepsky.usable(c, device, device.get("fov"))
             and all(objects.ang_sep(c.ra, c.dec, p["ra"], p["dec"]) > max(r, c.size / 120) for p, r in zip(own, own_r))
             and 90 - abs(site[0] - c.dec) >= MIN_ALT]                      # kommt am Ort überhaupt je über die Mindesthöhe
    ras = np.radians([p["ra"] for p in own] + [c.ra for c in cands])
    decs = np.radians([p["dec"] for p in own] + [c.dec for c in cands])
    n_own = len(own)

    with warnings.catch_warnings(), iers.conf.set_temp("auto_download", False), iers.conf.set_temp("auto_max_age", None):
        warnings.simplefilter("ignore")
        t = Time(ts, format="unix")
        loc = EarthLocation(lat=site[0] * u.deg, lon=site[1] * u.deg)
        lst = t.sidereal_time("mean", longitude=site[1] * u.deg).rad
        sun_g, moon_g, moon_c = get_body("sun", t), get_body("moon", t, loc), get_body("moon", t)
        sra, sdec = sun_g.ra.rad, sun_g.dec.rad
        mra, mdec = moon_g.ra.rad, moon_g.dec.rad
        illum = (1 - np.cos(sun_g.separation(moon_c).rad)) / 2
        # Höhe von Sonne/Mond: je Zeitpunkt eigene Koordinaten → Diagonale der Matrix
        p_ = math.radians(site[0])
        def body_alt(ra, dec):
            return np.degrees(np.arcsin(np.clip(math.sin(p_) * np.sin(dec) + math.cos(p_) * np.cos(dec) * np.cos(lst - ra), -1, 1)))
        sun, moon = body_alt(sra, sdec), body_alt(mra, mdec)
        alt = _alt(ras, decs, lst, site[0]) if len(ras) else np.zeros((0, len(ts)))
        msep = _sep(ras, decs, mra, mdec) if len(ras) else np.zeros((0, len(ts)))
        # Saison-Ausblick: Nacht von heute + 0, 14, 28 … Tage in Stundenschritten
        ots = (t0 + np.array(OFFSETS)[:, None] * 86400 + np.arange(25)[None, :] * 3600).ravel()
        ot = Time(ots, format="unix")
        olst = ot.sidereal_time("mean", longitude=site[1] * u.deg).rad
        osun = get_body("sun", ot)
        osun_alt = np.degrees(np.arcsin(np.clip(math.sin(p_) * np.sin(osun.dec.rad) + math.cos(p_) * np.cos(osun.dec.rad) * np.cos(olst - osun.ra.rad), -1, 1)))
        o_dark = (osun_alt <= -18).reshape(len(OFFSETS), 25)
        o_good = ((_alt(ras, decs, olst, site[0]) >= MIN_ALT).reshape(len(ras), len(OFFSETS), 25) & o_dark[None]) if len(ras) else None
        o_hours = o_good.sum(axis=2) if o_good is not None else np.zeros((0, len(OFFSETS)))

    tw = {"sunset": _cross(ts, sun, -0.833, True), "civil": _cross(ts, sun, -6, True), "nautical": _cross(ts, sun, -12, True),
          "dark_start": _cross(ts, sun, -18, True), "dark_end": _cross(ts, sun, -18, False), "nautical_end": _cross(ts, sun, -12, False),
          "sunrise": _cross(ts, sun, -0.833, False)}
    dark = sun <= -18
    moonless = dark & (moon <= 0)
    view = [max(t0, tw["sunset"] - 1800) if tw["sunset"] else t0,
            min(t1, tw["sunrise"] + 1800) if tw["sunrise"] else t1]
    sel = (ts >= view[0]) & (ts <= view[1])
    vt, vm = ts[sel], moon[sel]                       # Mondauf-/-untergang nur innerhalb der gezeigten Nacht
    mid_dark = int(np.argmax(dark)) + int(dark.sum()) // 2 if dark.any() else len(ts) // 2
    waxing = bool(illum[min(mid_dark + 6, len(illum) - 1)] > illum[mid_dark])

    # ---- Wetter: Wolken je Schritt (nur mit Vorhersage); unbekannte Stunden zählen als klar -------------------------
    weather, weather_error, all_hours = None, None, []
    if cfg.get("weather"):
        try:
            wide = _forecast(site, cache, base0, base0 + NIGHTS * 86400)          # alle Nächte aus derselben Abfrage
        except Exception as e:                          # Netz weg: Planung geht trotzdem
            wide, weather_error = None, f"Wettervorhersage nicht erreichbar ({type(e).__name__})"
        if wide:
            last = max([h["t"] for h in wide.get("hours", [])] or [0])
            all_hours = sorted([*wide.get("hours", []), *({"t": h["t"], "cloud": h["cloud"]} for h in wide.get("later", []) if h["t"] > last)],
                               key=lambda h: h["t"])
            hours = [h for h in all_hours if view[0] - 1800 <= h["t"] <= view[1] + 1800]
            later = [{"t": h["t"], "cloud": h["cloud"]} for h in all_hours
                     if view[1] + 1800 < h["t"] <= view[1] + 48 * 3600 and h.get("cloud") is not None]
            weather = {"hours": hours, "later": later, "source": wide.get("source"), "until": all_hours[-1]["t"]} if hours else None
    cloud = _series(ts, weather["hours"], "cloud") if weather else None
    clear = np.ones(len(ts)) if cloud is None else np.clip(1 - np.nan_to_num(cloud, nan=0.0) / 80, 0, 1)      # ab 80 % Wolken nicht nutzbar

    # ---- Gewichtung je Objekt und Schritt ------------------------------------------------------------------------
    future = ts >= ref
    good = dark[None, :] & (alt >= MIN_ALT) & future[None, :]
    moon_up = (moon > 0)[None, :]
    loss = illum[None, :] * np.clip(1 - msep / 80, 0, 1)                     # Phase × Nähe
    w_moon = np.where(moon_up, (1 - 0.85 * loss) * (1 - 0.3 * illum[None, :]), 1.0)
    w_zen = np.where((alt > ZENITH_ALT) & bool(device.get("alt_az", True)), ZENITH_WEIGHT, 1.0)
    weight = good * w_moon * w_zen * clear[None, :]
    eff = weight.sum(axis=1) * STEP_MIN / 60                                 # nutzbare Stunden
    eff_clear = (good * w_moon * w_zen).sum(axis=1) * STEP_MIN / 60          # dieselben Stunden bei klarem Himmel (ohne Wetter)
    sky = _sky(cloud, dark & (ts >= ref), weather, ts, sun, t0, ref)
    raw = good.sum(axis=1) * STEP_MIN / 60
    zen_h = (good & (alt > ZENITH_ALT)).sum(axis=1) * STEP_MIN / 60 if bool(device.get("alt_az", True)) else np.zeros(len(alt))
    jmax = np.argmax(np.where(dark[None, :] & future[None, :], alt, -90), axis=1) if len(alt) else np.zeros(0, int)
    jtr = np.argmax(alt, axis=1) if len(alt) else np.zeros(0, int)           # Kulmination des ganzen Tages
    culm = (dark[jtr] & (alt[np.arange(len(alt)), jtr] >= MIN_ALT)) if len(alt) else np.zeros(0, bool)
    seasons = [_season(o_hours[i], t0, site[0]) for i in range(len(alt))]

    def entry(i: int, info: dict) -> dict:
        a = alt[i]
        spans = _spans(ts, good[i])
        moon_visible = good[i] & (moon > 0)
        sep = float(msep[i][moon_visible].min()) if moon_visible.any() else 180.0
        return {**info, "max_alt": round(float(a[jmax[i]]), 1), "max_t": float(ts[jmax[i]]),
                "good_hours": round(float(raw[i]), 2), "eff_hours": round(float(eff[i]), 2), "eff_clear": round(float(eff_clear[i]), 2), "good": spans,
                "moon_sep": round(sep, 1), "alt": [round(float(v), 1) for v in a[sel]],
                "culm_dark": bool(culm[i]), "zenith_hours": round(float(zen_h[i]), 2), "season": seasons[i]}

    targets = [entry(i, {"id": p["id"], "name": p["name"], "kind": p.get("kind"), "exposure": p.get("exposure") or 0}) for i, p in enumerate(own)]
    targets = sorted([x for x in targets if x["good_hours"] > 0], key=lambda x: -x["good_hours"])

    # ---- Bewertung ----------------------------------------------------------------------------------------------------
    pool = []
    by_id = {p["id"]: p for p in projects}
    for x in targets:
        if str(x["id"]) in hidden:
            continue
        p = by_id[x["id"]]
        reasons = _need(p)
        blocked = x["eff_hours"] < 1 <= x["eff_clear"] and cloud is not None          # nur wegen Wolken nicht nutzbar → „bei klarem Himmel“
        h = x["eff_clear"] if blocked else x["eff_hours"]
        g = _gain(p.get("exposure"), min(h, 8), night == 0)
        if not reasons or h < 1 or (g and g["noise_pct"] < MIN_GAIN * 100):
            continue
        sc = _score(h, x["max_alt"], x["culm_dark"], x["season"], (g["noise_pct"] / 100 * 2.2) if g else 0.6) - (BLOCKED_PENALTY if blocked else 0)
        reasons = reasons + _visibility_reasons({**x, "eff_hours": h}, device, night == 0) + (["Laut Vorhersage zu bewölkt – gilt bei klarem Himmel."] if blocked else [])
        pool.append({**x, "category": "more", "reasons": reasons, "gain": g, "kind_label": objects.KIND_LABEL.get(x.get("kind") or "", ""),
                     "benefit": g["text"] if g else "Mehr Gesamtbelichtung senkt das Rauschen (1/√Zeit) und macht schwache Details klarer.",
                     "href": f"#/p/{x['id']}", "rank": sc, "weather_blocked": blocked})
    new_pool = []
    for k, c in enumerate(cands):
        i = n_own + k
        blocked = bool(eff[i] < 1.5 <= eff_clear[i] and cloud is not None)
        if (eff[i] < 1.5 and not blocked) or raw[i] < 2:
            continue
        x = entry(i, {"id": c.id, "name": c.display, "kind": c.kind, "kind_label": objects.KIND_LABEL.get(c.kind, ""), "size": c.size,
                      "mag": c.mag, "source": c.source})
        lim = device.get("mag_limit") or deepsky.MAG_DEFAULT
        bright = max(0.0, min(1.0, (lim - c.mag) / 6)) * 0.8 if c.mag is not None else (0.4 if c.source != "astap" else 0.0)
        h = x["eff_clear"] if blocked else x["eff_hours"]
        sc = _score(h, x["max_alt"], x["culm_dark"], x["season"], bright) * (0.92 if c.source == "astap" else 1.0) * KIND_APPEAL.get(c.kind, 1.0)
        new_pool.append({**x, "category": "new", "rank": sc - (BLOCKED_PENALTY if blocked else 0), "weather_blocked": blocked, "eff_hours": h})
    new_pool.sort(key=lambda x: -x["rank"])
    top_new = new_pool[:10]
    for x in top_new:
        x["reasons"] = _new_reasons(x, device, owned_m, collection, night == 0) + (["Laut Vorhersage zu bewölkt – gilt bei klarem Himmel."] if x["weather_blocked"] else [])
        x["benefit"] = "Eine neue Aufnahme ergänzt deine Sammlung."
        x["href"] = f"#/nacht/{x['id']}"
    for x in pool + top_new:
        x["moon_warning"] = x["moon_sep"] < 40 and x["good_hours"] > 0
    recommendations = _select(pool + top_new)

    # ---- Ablaufplan: ein Ziel zur Zeit ---------------------------------------------------------------------------------
    blocks = _night_plan([r for r in recommendations if not r["weather_blocked"]], own, cands, n_own, weight, ts, ref, clear, cloud)
    status = _status(tw, dark, ts, ref, sky, recommendations, targets)

    # ---- „Bald gut sichtbar“ ---------------------------------------------------------------------------------------------
    soon = []
    for k, c in enumerate(cands):
        i = n_own + k
        s = seasons[i]
        if s and s["kind"] == "soon":
            soon.append({"id": c.id, "name": c.display, "kind_label": objects.KIND_LABEL.get(c.kind, ""), "mag": c.mag, "size": c.size, "season": s, "good": [], "reasons": [s["text"]],
                         "href": f"#/nacht/{c.id}", "_k": (s["from"][3:5] + s["from"][:2], c.mag if c.mag is not None else 99)})
    soon.sort(key=lambda x: x["_k"])
    for x in soon:
        x.pop("_k")

    out = {"site": site, "start": view[0], "end": view[1], "step": STEP_MIN * 60, "times": [float(v) for v in ts[sel]],
           "twilight": tw, "dark_hours": round(sum(b - a for a, b in _spans(ts, dark)) / 3600, 2),
           "moon": {"illum": round(float(illum[mid_dark]) * 100), "waxing": waxing, "alt": [round(float(v), 1) for v in moon[sel]],
                    "rise": _cross(vt, vm, 0, False), "set": _cross(vt, vm, 0, True), "moonless": _spans(ts, moonless)},
           "targets": targets[:MAX_CURVES], "more_targets": max(0, len(targets) - MAX_CURVES), "plan": blocks,
           "suggestions": top_new, "recommendations": recommendations, "soon": soon[:5], "device": device,
           "sun": [round(float(v), 1) for v in sun[sel]], "cloud": _rounded(cloud, sel),
           "clouds": {k: _rounded(_series(ts, weather["hours"], k), sel) for k in ("low", "mid", "high")} if weather else None,
           "wind": _rounded(_series(ts, weather["hours"], "wind"), sel) if weather else None,
           "night_index": night, "nights": _strip(night_sites, site, base0, all_hours, now, cache, bool(cfg.get("weather"))),
           "forecast": {"covered": cloud is not None and bool(np.isfinite(cloud[sel]).any()), "uncertain": night >= UNCERTAIN_FROM},
           "weather": weather, "weather_error": weather_error, "weather_on": bool(cfg.get("weather")), "min_alt": MIN_ALT,
           "catalog": {"candidates": len(cands), "messier": sum(c.source == "messier" for c in cands), "astap": sum(c.source == "astap" for c in cands),
                       "astap_found": bool(objects.extended_catalog()), "mag_limit": device.get("mag_limit")},
           "hidden": sorted(hidden), "sky": sky, "status": status}
    if len(_cache) >= _CACHE_MAX:
        _cache.pop(next(iter(_cache)))
    _cache[key] = (time.time(), out)
    return {**out, "now": now}


def _strip(night_sites, site, base0, hours, now, cache, weather) -> list[dict]:
    """Nachtleiste mit dem Ort jeder Nacht: je eigenem Ort eigene Rechnung und Vorhersage (gleicher Dienst, gleiche Rundung)."""
    out = [None] * NIGHTS
    for s in {tuple(x) for x in night_sites}:
        s = list(s)
        if s == site:
            b0, hh = base0, hours
        else:
            b0, hh = _window(now, s[1])[0], []
            if weather:
                try:
                    w = _forecast(s, cache, b0, b0 + NIGHTS * 86400) or {}
                    last = max([h["t"] for h in w.get("hours", [])] or [0])
                    hh = sorted([*w.get("hours", []), *(h for h in w.get("later", []) if h["t"] > last)], key=lambda h: h["t"])
                except Exception:                      # Netz weg: diese Nächte ohne Wolken
                    hh = []
        rows = _nights(s, b0, hh, now)
        for k in range(NIGHTS):
            if night_sites[k] == s:
                out[k] = rows[k]
    return out


def _nights(site: list[float], base0: float, hours: list[dict], now: float) -> list[dict]:
    """Nachtleiste: je Nacht (ab der geplanten) Datum, dunkle Stunden, Mond (Beleuchtung, Stunden über dem Horizont im
    Dunkeln) und – soweit die Vorhersage reicht – mittlere Bewölkung im Dunkeln. Datum über core.sites.planning_night."""
    key = ("nights", tuple(site), int(base0), int(now // 3600), tuple((h["t"], h.get("cloud")) for h in hours))
    if key in _nights_cache:
        return _nights_cache[key]
    from astropy import units as u
    from astropy.coordinates import EarthLocation, get_body
    from astropy.time import Time
    from astropy.utils import iers

    from ...core.sites import planning_night
    step = 1200
    ts = base0 + np.arange(0, NIGHTS * 86400, step, dtype=float)
    with warnings.catch_warnings(), iers.conf.set_temp("auto_download", False), iers.conf.set_temp("auto_max_age", None):
        warnings.simplefilter("ignore")
        t = Time(ts, format="unix")
        lst = t.sidereal_time("mean", longitude=site[1] * u.deg).rad
        sun_g, moon_g = get_body("sun", t), get_body("moon", t, EarthLocation(lat=site[0] * u.deg, lon=site[1] * u.deg))
        p_ = math.radians(site[0])
        alt = lambda b: np.degrees(np.arcsin(np.clip(math.sin(p_) * np.sin(b.dec.rad) + math.cos(p_) * np.cos(b.dec.rad) * np.cos(lst - b.ra.rad), -1, 1)))
        sun, moon = alt(sun_g), alt(moon_g)
        illum = (1 - np.cos(sun_g.separation(get_body("moon", t)).rad)) / 2
    cloud = _series(ts, hours, "cloud") if hours else None
    first = dt.date.fromisoformat(planning_night(base0 + 43200, site[1]))
    out = []
    for k in range(NIGHTS):
        m = (ts >= base0 + k * 86400) & (ts < base0 + (k + 1) * 86400)
        dark = m & (sun <= -18)
        idx = np.where(dark if dark.any() else m)[0]
        mid = int(idx[len(idx) // 2])
        cl = cloud[dark] if cloud is not None else np.array([])
        cl = cl[np.isfinite(cl)]
        out.append({"index": k, "date": (first + dt.timedelta(days=k)).isoformat(), "dark_hours": round(float(dark.sum() * step / 3600), 1),
                    "moon": round(float(illum[mid]) * 100), "waxing": bool(illum[min(mid + 3, len(illum) - 1)] > illum[mid]),
                    "moon_dark_hours": round(float((dark & (moon > 0)).sum() * step / 3600), 1),
                    "cloud": round(float(cl.mean())) if dark.any() and len(cl) >= dark.sum() / 2 else None,
                    "uncertain": k >= UNCERTAIN_FROM})
    if len(_nights_cache) >= _CACHE_MAX:
        _nights_cache.pop(next(iter(_nights_cache)))
    _nights_cache[key] = out
    return out


KIND_APPEAL = {"open_cluster": 0.85, "globular": 0.95}      # offene Haufen wirken auf Smart-Teleskopen weniger spektakulär


def _clear_window(weather: dict | None, ts: np.ndarray, sun: np.ndarray, t0: float, now: float) -> dict | None:
    """Nächstes Fenster (≥ 2 h, Wolken < 50 %, dunkel) laut Vorhersage ab jetzt – auch in der folgenden Nacht (Sonnenstand um
    ganze Tage zurückgerechnet, Fehler wenige Minuten)."""
    if not weather:
        return None
    pts = sorted([(h["t"], h["cloud"]) for h in weather.get("hours", []) if h.get("cloud") is not None]
                 + [(h["t"], h["cloud"]) for h in weather.get("later", [])])
    # 10-Minuten-Schritte innerhalb klarer Stunden, nur im Dunkeln und ab jetzt; zusammenhängende Läufe ≥ 2 h
    step = STEP_MIN * 60
    samples = []
    for t, c in pts:
        if c >= 50:
            continue
        for u in np.arange(t, t + 3600, step):
            if u < now:
                continue
            k = max(0, int((u - t0) // 86400))
            if float(np.interp(u - k * 86400, ts, sun)) <= -18:
                samples.append(float(u))
    run: list[float] = []
    for u in sorted(set(samples)) + [None]:
        if run and (u is None or u - run[-1] > step):
            if run[-1] + step - run[0] >= 7200:
                return {"start": run[0], "end": run[-1] + step, "tonight": run[0] < t0 + 86400}
            run = []
        if u is not None:
            run.append(u)
    return None


def _sky(cloud, mask: np.ndarray, weather, ts, sun, t0, now) -> dict | None:
    """Wetterlage der verbleibenden dunklen Zeit: overcast | partly | clear (nur mit Vorhersage)."""
    if cloud is None:
        return None
    m = mask & ~np.isnan(cloud)
    if not m.any():
        return None
    mean = float(cloud[m].mean())
    usable = float((cloud[m] < 80).sum() * STEP_MIN / 60)
    remaining = float(m.sum() * STEP_MIN / 60)
    # „bedeckt“ nur nach der Wolkenlage – bleibt weniger als eine Stunde Dunkelheit, entscheidet allein der Mittelwert
    state = "overcast" if mean >= 85 or (remaining >= 1 and usable < 1) else "partly" if mean >= 30 else "clear"
    return {"state": state, "mean": round(mean), "usable_hours": round(usable, 1),
            "next_clear": _clear_window(weather, ts, sun, t0, now) if state != "clear" else None}


def _status(tw, dark, ts, now, sky, recs, targets) -> dict:
    """Warum es (keine) Empfehlungen gibt – getrennt nach Ursache: no_dark | over | overcast | partly | low_alt | ok."""
    if not dark.any():
        return {"code": "no_dark"}
    if not (dark & (ts >= now)).any():
        return {"code": "over"}
    if sky and sky["state"] == "overcast":
        return {"code": "overcast"}
    if not recs:
        return {"code": "low_alt"}
    return {"code": "partly" if sky and sky["state"] == "partly" else "ok"}


def _score(eff: float, max_alt: float, culm: bool, season: dict | None, extra: float) -> float:
    """Gemeinsamer Maßstab für Neu und Mehr Zeit (≈ 0–6). extra: Helligkeit (neu) bzw. Rauschgewinn (mehr Zeit)."""
    return (3.0 * min(eff, 7.0) / 7.0 + 0.8 * min(max_alt, 90) / 90 + (0.5 if culm else 0.0) + extra
            + (0.6 if season and season["kind"] == "last" else 0.0))


def _visibility_reasons(x: dict, device: dict, today: bool = True) -> list[str]:
    r = [f"{'Heute' if today else 'In dieser Nacht'} etwa {_dur(x['eff_hours'] * 3600)} nutzbar (dunkel, über {MIN_ALT:.0f}°), höchster Stand {round(x['max_alt'])}°."]
    if x["culm_dark"]:
        r.append("Kulminiert mitten in der dunklen Zeit.")
    if x["zenith_hours"] >= 0.5:
        r.append("Steht zeitweise sehr hoch (> 80°): bei Alt-Az-Montierung starke Feldrotation, diese Stunden zählen weniger.")
    if x["season"]:
        r.append(x["season"]["text"])
    return r


def _new_reasons(x: dict, device: dict, owned_m: set, collection: dict, today: bool = True) -> list[str]:
    src = {"messier": "Messier-Katalog", "caldwell": "Caldwell-Katalog", "astap": "ASTAP-Katalog (Helligkeit unbekannt, nach Größe/Name gewählt)"}[x["source"]]
    r = [f"Fehlt noch in deiner Bibliothek ({src})."]
    if x["source"] == "messier" and collection.get("messier") is not None:
        r.append(f"Wäre dein {len(owned_m) + 1}. Messier-Objekt (bisher {len(owned_m)} von 110).")
    if device.get("fov"):
        r.append(f"Passt mit Rand ins Bildfeld (Objekt ca. {str(round(x['size'], 1)).rstrip('0').rstrip('.').replace('.', ',')}′, Bildfeld {min(device['fov']) * 60:.0f}′ kurze Seite).")
    if x.get("mag") is not None:
        de = lambda v: str(round(v, 1)).replace(".", ",")
        r.append(f"Helligkeit ca. {de(x['mag'])} mag – für dein Gerät erreichbar (Grenze etwa {de(device.get('mag_limit', deepsky.MAG_DEFAULT))} mag).")
    return r + _visibility_reasons(x, device, today)


def _select(pool: list[dict]) -> list[dict]:
    """Greedy: höchste Bewertung zuerst, aber Objektarten und Kategorien mischen. Beide Kategorien kommen vor, wenn vorhanden."""
    ranked = sorted(pool, key=lambda x: -x["rank"])
    picked: list[dict] = []
    for cat in ("more", "new"):
        first = next((x for x in ranked if x["category"] == cat), None)
        if first:
            picked.append(first)
    left = [x for x in ranked if x not in picked]
    while left and len(picked) < MAX_CURVES:
        full = {k for k in {p.get("kind") for p in picked} if sum(1 for p in picked if p.get("kind") == k) >= 2}
        if any(x.get("kind") not in full for x in left):               # höchstens 2 je Objektart, solange es Alternativen gibt
            cand = [x for x in left if x.get("kind") not in full]
        else:
            cand = left
        def adj(x):
            same_kind = sum(1 for p in picked if p.get("kind") == x.get("kind"))
            same_cat = sum(1 for p in picked if p["category"] == x["category"])
            return x["rank"] - 0.7 * same_kind - 0.4 * max(0, same_cat - 2)
        best = max(cand, key=adj)
        picked.append(best)
        left.remove(best)
    return sorted(picked, key=lambda x: -x["rank"])


def _night_plan(recs, own, cands, n_own, weight, ts, now, clear, cloud) -> list[dict]:
    """Ein Smart-Teleskop nimmt nur ein Ziel gleichzeitig auf. Die nutzbare Nacht (ab jetzt, dunkel) wird in n gleich lange
    Abschnitte (≈ 3 h, 1–4) geteilt; jedem Abschnitt kommt ein anderes Ziel zu, so dass die Summe der gewichteten Sichtbarkeit
    (Höhe, Mond, Zenit, Wolken) maximal wird – Ziele, die früh untergehen, kommen früh, später aufgehende spät.
    Jeder Wechsel kostet Aufstellen/Fokus, deshalb gibt es höchstens 4 Abschnitte; Abschnitte unter 40 min entfallen."""
    if not recs:
        return []
    own_ix = {p["id"]: i for i, p in enumerate(own)}
    cand_ix = {c.id: n_own + k for k, c in enumerate(cands)}
    rows = [own_ix[r["id"]] if r["category"] == "more" else cand_ix[r["id"]] for r in recs]
    top = max(r["rank"] for r in recs) or 1
    v = np.array([weight[i] * (1 + 0.3 * r["rank"] / top) for i, r in zip(rows, recs)])         # K × T
    active = np.where(v.max(axis=0) > 0)[0]
    if not len(active):
        return []
    lo, hi = int(active[0]), int(active[-1]) + 1
    n = int(min(4, max(1, round((hi - lo) * STEP_MIN / 60 / 3.0)), len(recs)))
    edges = np.linspace(lo, hi, n + 1).round().astype(int)
    gain = np.array([[v[k, edges[j]:edges[j + 1]].sum() for j in range(n)] for k in range(len(recs))])      # K × n
    best, choice = -1.0, None
    for perm in itertools.permutations(range(len(recs)), n):
        total = sum(gain[k, j] for j, k in enumerate(perm))
        if total > best:
            best, choice = total, perm
    blocks = []
    for j, k in enumerate(choice or ()):
        seg = np.where(v[k, edges[j]:edges[j + 1]] > 0)[0] + edges[j]
        for run in np.split(seg, np.where(np.diff(seg) > 1)[0] + 1) if len(seg) else []:       # getrennte klare Fenster = getrennte Blöcke
            blocks.append({"k": k, "start": float(ts[run[0]]), "end": float(ts[run[-1]] + STEP_MIN * 60)})
    out = []
    for b in blocks:
        r = recs[b["k"]]
        if b["end"] - b["start"] < MIN_BLOCK:
            continue
        mid = int(np.searchsorted(ts, (b["start"] + b["end"]) / 2))
        c = None if cloud is None or np.isnan(cloud[min(mid, len(ts) - 1)]) else round(float(cloud[min(mid, len(ts) - 1)]))
        out.append({"id": r["id"], "name": r["name"], "category": r["category"], "start": b["start"], "end": b["end"],
                    "cloud": c, "href": r["href"]})
    return out
