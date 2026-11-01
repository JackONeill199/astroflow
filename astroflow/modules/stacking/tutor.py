"""Bild-Tutor: erklärt die technische Bildprüfung, bewertet die Aufnahmenacht und gibt Tipps passend zum Gerät.

Bausteine (alles aus echten Daten, nichts erfunden):
  - Gerät:       aus INSTRUME/TELESCOP bzw. Bildformat (Stellina, Vespera, Seestar, Dwarf, eVscope, Astrokamera).
  - Nacht:       Sonne (Dämmerung), Mond (Phase, Höhe, Abstand zum Objekt) und Objekthöhe offline mit astropy;
                 Verlauf der Einzelaufnahmen (Himmelshelligkeit, Sternzahl, Schärfe, verzogene Sterne);
                 optional Wetter (Wolken, Feuchte/Taupunkt, Frost, Wind) aus dem Open-Meteo-Archiv (Ort auf 0,1° gerundet).
  - Befunde:     Bildprobleme aus develop.assess + Nachtbedingungen -> Erklärung + Tipps, je Gerät angepasst.
  - KI (optional, auf Knopfdruck): Vision-LLM sieht das Bild mit den Messwerten und ergänzt sichtbare Auffälligkeiten.

Standort: Header (fits_io._site) oder Einstellungen → Bilder → Bild-Tutor. Ohne Standort nur Mondphase/-abstand.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import warnings
from pathlib import Path

import numpy as np

TUTOR_VERSION = 2
MODES = ("einsteiger", "experte")
WEATHER_URL = "https://archive-api.open-meteo.com/v1/archive"
WEATHER_VARS = ("temperature_2m", "relative_humidity_2m", "dew_point_2m", "cloud_cover", "wind_speed_10m", "wind_gusts_10m")


# ------------------------------------------------------------------ Geräte #
# Nur gesicherte Angaben; Tipps vorsichtig formuliert. alt_az: Bildfeldrotation; gust: Böen (km/h), ab denen das Gerät
# erfahrungsgemäß unruhig wird (leichte Geräte früher).
DEVICES = {
    "stellina": {"name": "Vaonis Stellina", "smart": True, "alt_az": True, "aperture": 80, "focal": 400, "gust": 30, "dew_heater": True,
                 "filter": "Für Stellina bietet Vaonis einen Lichtverschmutzungsfilter (CLS) an – er hilft bei Stadtlicht und Mond vor allem bei Emissionsnebeln.",
                 "wind": "Stellina windgeschützt aufstellen (Hauswand, Hecke, Auto als Windschutz) und auf festen Untergrund achten.",
                 "dew": "",                                       # eingebaute Tauheizung (Hinweis des Nutzers)
                 "fov": "Bildfeld ca. 1° × 0,7° – kleine Galaxien bleiben klein, große Nebel passen oft nicht ganz hinein."},
    "vespera": {"name": "Vaonis Vespera", "smart": True, "alt_az": True, "aperture": 50, "focal": 200, "gust": 25,
                "filter": "Für Vespera gibt es von Vaonis einen Dual-Band-Filter (Hα/OIII) für Emissionsnebel bei Mond/Stadtlicht und einen CLS-Filter. Für Galaxien und Sternhaufen hilft nur dunkler Himmel.",
                "wind": "Vespera ist leicht – windgeschützt und auf festem Boden aufstellen, Stativ möglichst niedrig.",
                "dew": "Bei feuchter Luft Objektiv auf Beschlag prüfen; eine Taukappe hilft.",
                "fov": "Großes Bildfeld – für große Objekte den Mosaik-Modus der App nutzen."},
    "seestar": {"name": "ZWO Seestar", "smart": True, "alt_az": True, "aperture": 50, "focal": 250, "gust": 20,
                "filter": "Seestar hat einen eingebauten Lichtverschmutzungsfilter (Dual-Band): in der App für Emissionsnebel einschalten, für Galaxien und Sternhaufen aus.",
                "wind": "Seestar ist sehr leicht – bei Wind tief aufstellen und windgeschützt; für lange Sessions ist der EQ-Modus mit Polhöhenwiege ruhiger.",
                "dew": "Die Taukappenheizung des Seestar in der App einschalten, wenn die Luft feucht ist.",
                "fov": "Großes Bildfeld – für kleine Galaxien sehr viel Belichtungszeit sammeln."},
    "dwarf": {"name": "DWARFLAB Dwarf", "smart": True, "alt_az": True, "aperture": 35, "focal": 100, "gust": 20,
              "filter": "Den eingebauten Astro- bzw. Dual-Band-Filter passend zum Objekt wählen (Dual-Band nur für Emissionsnebel).",
              "wind": "Sehr leichtes Gerät – unbedingt windgeschützt aufstellen.",
              "dew": "Linse bei feuchter Luft auf Beschlag prüfen.", "fov": ""},
    "evscope": {"name": "Unistellar eVscope", "smart": True, "alt_az": True, "aperture": 114, "focal": 450, "gust": 30,
                "filter": "", "wind": "Windgeschützt aufstellen.", "dew": "Bei feuchter Luft auf Beschlag am Spiegel/Tubus achten.", "fov": ""},
    "camera": {"name": "Astrokamera", "smart": False, "alt_az": False, "gust": 30,
               "filter": "Bei Mond oder Stadtlicht: Schmalband-/Dual-Band-Filter für Emissionsnebel; Galaxien besser in mondlosen Nächten.",
               "wind": "Montierung und Stativ windgeschützt aufstellen, Gegengewichte ausbalancieren; Autoguiding glättet Böen teilweise.",
               "dew": "Taukappe und Heizmanschette am Objektiv bzw. an der Schmidt-Platte verwenden.", "fov": ""},
}
# Bildformate der Smart-Teleskope (H, B) als Rückfall, wenn der Header kein Gerät nennt
SHAPES = {(2080, 3072): "stellina"}


def detect_device(instruments: list[str], shape: tuple | None = None) -> dict:
    """Gerät aus INSTRUME/TELESCOP (häufigster Eintrag) bzw. Bildformat."""
    text = " ".join(i for i in instruments if i).lower()
    for key, pat in (("stellina", r"stellina"), ("vespera", r"vespera"), ("seestar", r"seestar"), ("dwarf", r"dwarf"),
                     ("evscope", r"evscope|unistellar|equinox|odyssey")):
        if re.search(pat, text):
            d = {"id": key, **DEVICES[key]}
            if key == "seestar" and (m := re.search(r"s\s?(30|50)", text)):
                d["name"] = f"ZWO Seestar S{m.group(1)}"
            if key == "vespera" and re.search(r"pro|ii|2", text):
                d["name"] += " II/Pro"
            return d
    key = SHAPES.get(tuple(shape[-2:])) if shape else None
    if key:
        return {"id": key, **DEVICES[key], "guessed": True}
    name = next((i for i in instruments if i), None)
    return {"id": "camera", **DEVICES["camera"], "name": name or DEVICES["camera"]["name"]}


# ------------------------------------------------------------- Himmelslage #
def galactic_b(ra: float | None, dec: float | None) -> float | None:
    if ra is None or dec is None:
        return None
    from astropy.coordinates import SkyCoord
    return float(SkyCoord(ra, dec, unit="deg").galactic.b.deg)


def neutral_sky(ra: float | None, dec: float | None, kind: str | None) -> bool:
    """Muss der Himmelshintergrund neutral sein? Abseits der Milchstraße (|b| ≥ 15°) ja; in der Milchstraße ist
    farbiger Himmel oft echter Hα-Nebel – dort nur bei Galaxien/Kugelsternhaufen streng prüfen."""
    b = galactic_b(ra, dec)
    if b is None:
        return kind in ("galaxy", "globular", "planetary", "open_cluster")
    return abs(b) >= 15 or (kind in ("galaxy", "globular") and abs(b) >= 8)


# -------------------------------------------------------------- Nacht/Ephemeriden #
def _times(frames: list[dict]) -> list[dt.datetime]:
    out = []
    for f in frames:
        try:
            t = dt.datetime.fromisoformat(str(f["date"]).replace("Z", ""))
            out.append(t.replace(tzinfo=dt.timezone.utc) if t.tzinfo is None else t.astimezone(dt.timezone.utc))
        except (KeyError, ValueError, TypeError):
            out.append(None)
    return out


def ephemeris(times: list[dt.datetime], ra: float | None, dec: float | None, site: list | None) -> dict:
    """Sonne, Mond und Objekt zu den Zeitpunkten (offline). Ohne Standort nur Mondphase und Abstand Mond–Objekt."""
    from astropy import units as u
    from astropy.coordinates import AltAz, EarthLocation, SkyCoord, get_body
    from astropy.time import Time
    from astropy.utils import iers
    t = Time(times)
    with warnings.catch_warnings(), iers.conf.set_temp("auto_download", False), iers.conf.set_temp("auto_max_age", None):
        warnings.simplefilter("ignore")
        sun, moon = get_body("sun", t), get_body("moon", t)
        illum = (1 - np.cos(sun.separation(moon).rad)) / 2
        out = {"moon_illum": illum}
        target = SkyCoord(ra, dec, unit="deg") if ra is not None and dec is not None else None
        if target is not None:
            out["moon_sep"] = moon.separation(target).deg
        if site:
            loc = EarthLocation(lat=site[0] * u.deg, lon=site[1] * u.deg)
            frame = AltAz(obstime=t, location=loc)
            out["sun_alt"] = sun.transform_to(frame).alt.deg
            out["moon_alt"] = get_body("moon", t, loc).transform_to(frame).alt.deg
            if target is not None:
                out["target_alt"] = target.transform_to(frame).alt.deg
                moon_topo = get_body("moon", t, loc)
                out["moon_sep"] = moon_topo.separation(target).deg
    return {k: np.asarray(v, float) for k, v in out.items()}


def _trend(v: np.ndarray) -> float | None:
    """Verhältnis Median letztes Fünftel / erstes Fünftel."""
    v = v[np.isfinite(v)]
    if len(v) < 15:
        return None
    k = max(3, len(v) // 5)
    a, b = np.median(v[:k]), np.median(v[-k:])
    return float(b / a) if a > 0 else None


def night_conditions(frames: list[dict], ra, dec, site: list | None, weather: dict | None = None) -> list[dict]:
    """Je Nacht: Zeitraum, Dämmerung, Mond, Objekthöhe, Verlauf der Einzelaufnahmen, Wetter."""
    nights: dict[str, list] = {}
    for f, t in zip(frames, _times(frames)):
        if t is not None and f.get("night"):
            nights.setdefault(f["night"], []).append((t, f))
    out = []
    for night, rows in sorted(nights.items()):
        rows.sort(key=lambda r: r[0])
        ts = [r[0] for r in rows]
        fr = [r[1] for r in rows]
        idx = np.unique(np.linspace(0, len(ts) - 1, min(len(ts), 48)).round().astype(int))
        e = ephemeris([ts[i] for i in idx], ra, dec, site)
        n = {"night": night, "start": ts[0].isoformat(), "end": ts[-1].isoformat(), "frames": len(fr),
             "hours": round((ts[-1] - ts[0]).total_seconds() / 3600, 2),
             "rejected": sum(1 for f in fr if f.get("status") == "rejected")}
        n["moon"] = {"illum": round(float(np.mean(e["moon_illum"])), 2)}
        if "moon_sep" in e:
            n["moon"]["sep"] = round(float(np.min(e["moon_sep"])), 1)
        if "moon_alt" in e:
            n["moon"]["up"] = round(float(np.mean(e["moon_alt"] > 0)), 2)
            n["moon"]["alt_max"] = round(float(np.max(e["moon_alt"])), 1)
        if "sun_alt" in e:
            n["sun"] = {"alt_max": round(float(np.max(e["sun_alt"])), 1), "alt_min": round(float(np.min(e["sun_alt"])), 1),
                        "dark": round(float(np.mean(e["sun_alt"] <= -18)), 2), "nautical": round(float(np.mean(e["sun_alt"] > -12)), 2)}
        if "target_alt" in e:
            n["target"] = {"alt_min": round(float(np.min(e["target_alt"])), 1), "alt_max": round(float(np.max(e["target_alt"])), 1),
                           "alt_med": round(float(np.median(e["target_alt"])), 1)}
        # Verlauf der Einzelaufnahmen (Messwerte aus quality.analyze_frame, bleiben nach dem Löschen der Rohbilder)
        get = lambda k: np.array([f.get(k) if f.get(k) is not None else np.nan for f in fr], float)
        stars, fwhm, ecc, bg = get("n_stars"), get("fwhm"), get("ecc"), get("bg")
        tr = {}
        if np.isfinite(stars).sum() >= 10:
            med = np.nanmedian(stars)
            tr["star_dips"] = round(float(np.nanmean(stars < 0.5 * med)), 2)        # Wolken/Dunst: Sterne verschwinden
        if (x := _trend(bg)) is not None:
            tr["bg_trend"] = round(x, 3)
        if (x := _trend(fwhm)) is not None:
            tr["fwhm_trend"] = round(x, 3)
        if np.isfinite(ecc).sum() >= 10:
            tr["ecc_high"] = round(float(np.nanmean(ecc > 0.8)), 2)                 # quality.ECC_MAX
        temps = [f["temp"] for f in fr if f.get("temp") is not None]
        if temps:
            tr["sensor_temp"] = round(float(np.median(temps)), 1)
        reasons: dict[str, int] = {}
        for f in fr:
            if f.get("status") == "rejected" and f.get("reason"):
                reasons[f["reason"]] = reasons.get(f["reason"], 0) + 1
        if reasons:
            tr["reasons"] = dict(sorted(reasons.items(), key=lambda kv: -kv[1])[:4])
        n["frames_trend"] = tr
        if weather and night in weather:
            n["weather"] = weather[night]
        out.append(n)
    return out


# ------------------------------------------------------------------ Wetter #
def fetch_weather(site: list, nights: list[dict], cache: Path, timeout: float = 15.0) -> dict:
    """Stündliche Wetterdaten (Open-Meteo-Archiv, ERA5) für die Aufnahmezeiträume. Ort auf 0,1° gerundet; Cache je Tag."""
    import httpx
    lat, lon = round(site[0], 1), round(site[1], 1)
    cache.mkdir(parents=True, exist_ok=True)
    out = {}
    for n in nights:
        t0, t1 = dt.datetime.fromisoformat(n["start"]), dt.datetime.fromisoformat(n["end"])
        d0, d1 = t0.date().isoformat(), t1.date().isoformat()
        f = cache / f"wetter_{lat}_{lon}_{d0}_{d1}.json"
        if f.exists():
            data = json.loads(f.read_text(encoding="utf-8"))
        else:
            r = httpx.get(WEATHER_URL, params={"latitude": lat, "longitude": lon, "start_date": d0, "end_date": d1,
                                               "hourly": ",".join(WEATHER_VARS), "timezone": "UTC", "wind_speed_unit": "kmh"},
                          timeout=timeout)
            r.raise_for_status()
            data = r.json().get("hourly") or {}
            if data.get("time"):
                f.write_text(json.dumps(data), encoding="utf-8")
        hrs = [i for i, s in enumerate(data.get("time") or [])
               if t0 - dt.timedelta(minutes=30) <= dt.datetime.fromisoformat(s).replace(tzinfo=dt.timezone.utc) <= t1 + dt.timedelta(minutes=30)]
        if not hrs:
            continue
        v = {k: np.array([data[k][i] for i in hrs if data.get(k) and data[k][i] is not None], float) for k in WEATHER_VARS}
        if not all(len(x) for x in v.values()):
            continue
        out[n["night"]] = {
            "cloud": round(float(v["cloud_cover"].mean())), "cloud_max": round(float(v["cloud_cover"].max())),
            "humidity": round(float(v["relative_humidity_2m"].max())), "temp_min": round(float(v["temperature_2m"].min()), 1),
            "dew_spread": round(float((v["temperature_2m"] - v["dew_point_2m"]).min()), 1),
            "wind": round(float(v["wind_speed_10m"].mean())), "gust": round(float(v["wind_gusts_10m"].max())),
            "source": "Open-Meteo (ERA5-Reanalyse, Raster ~25 km)"}
    return out


# --------------------------------------------------------------- Befunde #
# Bildprobleme (Codes aus develop.assess): Was bedeutet das? + allgemeine Tipps. Geräte-Tipps kommen in findings() dazu.
EXPLAIN = {
    "color_gradient": ("Der Himmelshintergrund ist nicht überall gleich gefärbt.",
                       "Ursache ist fast immer Licht, das nicht von den Sternen kommt: Dämmerung, Mondlicht oder Lichtverschmutzung "
                       "nahe am Horizont. Es fällt schräg ein und färbt eine Bildseite."),
    "cast": ("Der ganze Hintergrund hat einen Farbstich.", "Typisch für Lichtverschmutzung (Natriumlicht: orange, LED: eher neutral/bläulich)."),
    "gradient": ("Der Hintergrund ist an einer Seite heller als an der anderen.",
                 "Restlicht von Mond, Stadt oder Dämmerung oder Vignettierung (abgedunkelte Ecken) der Optik."),
    "noisy": ("Der Hintergrund wirkt körnig.", "Es fehlt Belichtungszeit – Rauschen sinkt mit der Wurzel der Gesamtbelichtung."),
    "chroma_noise": ("Im Hintergrund sind bunte Flecken.", "Farbrauschen: zu wenig Belichtungszeit oder ein warmer Sensor."),
    "faint": ("Das Objekt hebt sich kaum vom Himmel ab.", "Sehr schwaches Objekt, heller Himmel oder zu kurze Gesamtbelichtung."),
    "white_clip": ("Helle Bereiche sind rein weiß ohne Zeichnung.", "Sehr helle Sterne oder Kerne übersteigen den Bereich des Sensors."),
    "burnt_core": ("Der Kern des Objekts ist ausgebrannt.", "Der Kern ist viel heller als die Außenbereiche."),
    "bg_bright": ("Der Hintergrund ist zu hell und wirkt grau.", "Die Bearbeitung konnte den Himmel nicht weiter abdunkeln."),
    "bg_dark": ("Der Hintergrund ist sehr dunkel.", "Schwache Strukturen können dabei verloren gehen."),
    "black_clip": ("Dunkle Bereiche sind abgeschnitten.", "Schwarzpunkt zu hoch – schwache Ausläufer gehen verloren."),
    "oversat": ("Die Farben sind übersättigt.", "Kräftige Farben verstärken auch Farbrauschen."),
    "undersat": ("Die Farben wirken blass.", "Wenig Farbsignal – oft bei kurzer Belichtung oder hellem Himmel."),
}
TIPS = {
    "color_gradient": ["Objekte aufnehmen, wenn sie hoch stehen und weit vom Mond entfernt sind.",
                       "Nur in astronomisch dunklen Stunden aufnehmen (Sonne tiefer als 18° unter dem Horizont)."],
    "cast": ["Unter dunklerem Himmel aufnehmen oder einen Lichtverschmutzungsfilter verwenden."],
    "gradient": ["Flat-Frames aufnehmen – sie entfernen Vignettierung und Staubschatten zuverlässig."],
    "noisy": ["Mehr Belichtungszeit sammeln – weitere Nächte auf dasselbe Objekt werden automatisch ergänzt."],
    "chroma_noise": ["Mehr Belichtungszeit sammeln; kühle Nächte rauschen weniger."],
    "faint": ["Deutlich mehr Belichtungszeit sammeln (mehrere Stunden) und mondlose Nächte nutzen."],
    "white_clip": ["Bei hellen Objekten kürzere Einzelbelichtungen wählen, falls das Gerät das erlaubt."],
    "burnt_core": ["Bei hellen Kernen kürzere Einzelbelichtungen wählen, falls das Gerät das erlaubt."],
}


def _f(code, level, title, text, tips, evidence=None):
    return {"code": code, "level": level, "title": title, "text": text, "tips": [t for t in tips if t],
            **({"evidence": evidence} if evidence else {})}


def findings(quality: dict, nights: list[dict], device: dict, ctx: dict) -> list[dict]:
    """Befunde aus Bildprüfung und Nachtbedingungen, mit Erklärung und gerätebezogenen Tipps (ctx: exposure_s, kind,
    site, latitude)."""
    out = []
    issues = {i["code"]: i["text"] for i in quality.get("issues", [])}
    kind = ctx.get("kind")
    # --- Nacht: Dämmerung -------------------------------------------------------
    dusk = [n for n in nights if n.get("sun") and n["sun"]["dark"] < 0.5]
    if dusk:
        n = max(dusk, key=lambda n: n["frames"])
        dark_text = "keine Aufnahme entstand" if not n["sun"]["dark"] else f"nur {n['sun']['dark'] * 100:.0f} % der Aufnahmen entstanden"
        summer = int(n["night"][5:7]) in (5, 6, 7) and abs(ctx.get("latitude") or 50) > 45
        text = (f"In der Nacht vom {_d(n['night'])} stand die Sonne während der Aufnahmen höchstens {abs(n['sun']['alt_min']):.0f}° unter "
                f"dem Horizont – {dark_text} bei astronomischer Dunkelheit (ab 18°). "
                "Restliches Dämmerungslicht hellt den Himmel auf und färbt ihn oft zu einer Seite.")
        if (bt := n["frames_trend"].get("bg_trend", 1)) > 1.15:
            text += f" In den Einzelaufnahmen wurde der Himmel bis zum Ende auf das {_n(bt)}-Fache heller."
        if summer:
            text += " Um die Sommersonnenwende wird es in Mitteleuropa die ganze Nacht nicht richtig dunkel."
        tips = ["Galaxien und schwache Nebel in dunklen Monaten (August bis April) aufnehmen.",
                "Im Sommer helle Ziele wählen: Kugelsternhaufen, Planetarische Nebel, Doppelsterne."
                + (" Emissionsnebel gehen mit Dual-Band-Filter." if device["id"] in ("vespera", "seestar", "dwarf", "camera") else "")]
        out.append(_f("twilight", "bad" if n["sun"]["nautical"] > 0.3 else "warn", "Keine richtige Nacht (Dämmerung)", text, tips,
                      {"sonne_min": n["sun"]["alt_min"]}))
    # --- Nacht: Mond ------------------------------------------------------------
    for n in nights:
        m = n["moon"]
        up = m.get("up", 1.0)
        if m["illum"] < 0.3 or up < 0.2:
            continue
        sep = m.get("sep")
        strong = m["illum"] > 0.7 and up > 0.5 and (sep is None or sep < 70)
        broadband = kind in ("galaxy", "reflection", "globular", "open_cluster", None)
        text = (f"Am {_d(n['night'])} war der Mond zu {m['illum'] * 100:.0f} % beleuchtet"
                + (f" und stand während {up * 100:.0f} % der Aufnahmen über dem Horizont" if "up" in m else "")
                + (f", nur {sep:.0f}° vom Objekt entfernt" if sep is not None and sep < 60 else f", {sep:.0f}° vom Objekt entfernt" if sep is not None else "")
                + ". Mondlicht hellt den Himmel auf, senkt den Kontrast und erzeugt Farbverläufe.")
        tips = ["Galaxien und Reflexionsnebel bei Neumond bzw. vor Mondaufgang/nach Monduntergang aufnehmen." if broadband
                else "Bei Mond eignen sich Emissionsnebel mit Schmalband-/Dual-Band-Filter."]
        if device.get("filter"):
            tips.append(device["filter"])
        out.append(_f("moon", "bad" if strong else "warn", "Mond störte die Aufnahmen" if strong else "Mond am Himmel", text, tips,
                      {"beleuchtet": m["illum"], "abstand": sep}))
        break
    # --- Objekthöhe --------------------------------------------------------------
    for n in nights:
        t = n.get("target")
        if not t:
            continue
        if t["alt_med"] < 35:
            out.append(_f("low_alt", "warn" if t["alt_med"] < 25 else "info", "Objekt stand tief",
                          f"Das Objekt stand am {_d(n['night'])} meist nur {t['alt_med']:.0f}° hoch (min. {t['alt_min']:.0f}°). Tief am Horizont "
                          "schaut man durch viel mehr Luft: Lichtverschmutzung, Dunst und Luftunruhe sind stärker, es entstehen Farbverläufe zum Horizont.",
                          ["Das Objekt aufnehmen, wenn es um den Meridian (höchster Stand) steht – mindestens 30–40° Höhe."]))
            break
        if device.get("alt_az") and t["alt_max"] > 78:
            ev = n["frames_trend"]
            out.append(_f("zenith", "info", "Objekt nahe am Zenit",
                          f"Das Objekt stand am {_d(n['night'])} bis zu {t['alt_max']:.0f}° hoch. {device['name']} hat eine azimutale "
                          "Montierung – nahe am Zenit dreht sich das Bildfeld sehr schnell, Sterne werden in Einzelbildern länglich und "
                          "mehr Aufnahmen werden verworfen." + (f" In dieser Nacht hatten {ev['ecc_high'] * 100:.0f} % der Aufnahmen verzogene Sterne."
                                                                if ev.get("ecc_high", 0) > 0.1 else ""),
                          ["Bereiche über etwa 75–80° Höhe meiden oder die Sitzung so planen, dass das Objekt nicht durch den Zenit läuft."]))
            break
    # --- Verlauf der Einzelaufnahmen + Wetter ---------------------------------------
    for n in nights:
        tr, w = n.get("frames_trend", {}), n.get("weather") or {}
        clouds = tr.get("star_dips", 0) > 0.15 or w.get("cloud", 0) > 50
        if clouds:
            parts = []
            if tr.get("star_dips", 0) > 0.15:
                parts.append(f"in {tr['star_dips'] * 100:.0f} % der Aufnahmen war weniger als die Hälfte der üblichen Sterne sichtbar")
            if w.get("cloud", 0) > 50:
                parts.append(f"die Wetterdaten zeigen im Mittel {w['cloud']} % Bewölkung")
            out.append(_f("clouds", "warn", "Wolken oder Dunst", f"Am {_d(n['night'])}: {' und '.join(parts)}. Wolkige Aufnahmen werden "
                          "aussortiert oder bringen kaum Signal.",
                          ["Vor der Session Wolkenradar/Astro-Wetter prüfen (z. B. Clear Outside, Meteoblue Astronomy Seeing)."]))
        if tr.get("bg_trend", 1) > 1.15 and not any(f["code"] == "twilight" for f in out):
            out.append(_f("bg_rise", "info", "Himmel wurde während der Session heller",
                          f"Am {_d(n['night'])} stieg die Himmelshelligkeit in den Einzelaufnahmen um {(tr['bg_trend'] - 1) * 100:.0f} % "
                          "(Mondaufgang, beginnende Dämmerung oder aufziehender Dunst).",
                          ["Die Session vorher beenden oder nach Mondaufgang auf ein Schmalbandziel wechseln."]))
        windy = tr.get("ecc_high", 0) > 0.15 or w.get("gust", 0) > device.get("gust", 30)
        if windy and not any(f["code"] == "zenith" for f in out):
            parts = []
            if tr.get("ecc_high", 0) > 0.15:
                parts.append(f"{tr['ecc_high'] * 100:.0f} % der Aufnahmen hatten verzogene Sterne")
            if w.get("gust", 0) > device.get("gust", 30):
                parts.append(f"Böen bis {w['gust']} km/h")
            out.append(_f("wind", "warn", "Wind oder Nachführung", f"Am {_d(n['night'])}: {', '.join(parts)}. "
                          "Wind bringt das Gerät zum Schwingen, Sterne werden länglich.", [device.get("wind")]))
        soft = tr.get("fwhm_trend", 1) > 1.25
        humid = bool(w) and w.get("dew_spread", 9) < 2 and not device.get("dew_heater")   # eingebaute Heizung hält die Optik frei
        dew = soft or humid
        if humid:
            parts = [f"Temperatur und Taupunkt lagen nur {_n(w['dew_spread'])} °C auseinander (Luftfeuchte bis {w['humidity']} %)"]
            if soft:
                parts.append(f"die Sterne wurden im Lauf der Nacht um {(tr['fwhm_trend'] - 1) * 100:.0f} % unschärfer")
            out.append(_f("dew", "warn", "Tau oder Feuchtigkeit", f"Am {_d(n['night'])}: {' und '.join(parts)}. "
                          "Beschlägt die Optik, werden Sterne weich und der Himmel heller.", [device.get("dew")]))
        elif soft:
            out.append(_f("softening", "warn", "Sterne wurden im Lauf der Nacht unschärfer",
                          f"Am {_d(n['night'])} wurden die Sterne bis zum Ende um {(tr['fwhm_trend'] - 1) * 100:.0f} % unschärfer – "
                          "typisch für nachlassende Luftruhe, ein sinkendes Objekt oder Fokusdrift durch Abkühlung"
                          + (" (Tau ist dank der eingebauten Heizung unwahrscheinlich)." if device.get("dew_heater") else " oder beginnenden Tau."),
                          ["Bei starkem Temperaturabfall zwischendurch neu fokussieren lassen." if device.get("smart")
                           else "Nach größeren Temperaturänderungen nachfokussieren (Autofokus nach ΔT)."]
                          + ([device["dew"]] if not device.get("dew_heater") and device.get("dew") else [])))
        if w and w.get("humidity", 0) >= 95 and not dew:
            out.append(_f("fog", "warn", "Sehr feuchte Luft / Dunst", f"Am {_d(n['night'])} lag die Luftfeuchte bei bis zu {w['humidity']} %. "
                          "Dunst streut Stadtlicht und hellt den Himmel auf.", ["Nächte nach Kaltfronten mit trockener, klarer Luft nutzen."]))
        if w and w.get("temp_min", 10) < 0:
            out.append(_f("frost", "info", "Frost", f"Am {_d(n['night'])} fiel die Temperatur auf {_n(w['temp_min'])} °C.",
                          ["Akkus verlieren in der Kälte Kapazität – Powerbank warm halten bzw. extern versorgen.",
                           "Das Gerät nach der Session in der geschlossenen Tasche langsam aufwärmen lassen, damit kein Kondenswasser entsteht."]))
        if tr.get("sensor_temp", 0) >= 25:
            out.append(_f("warm_sensor", "info", "Warmer Sensor", f"Der Sensor hatte am {_d(n['night'])} etwa {tr['sensor_temp']:.0f} °C. "
                          "Ungekühlte Sensoren rauschen bei Wärme stärker und zeigen mehr Hotpixel.",
                          ["Darks bei gleicher Temperatur aufnehmen (Objektiv abdecken) – AstroFlow zieht sie automatisch ab."
                           if not device.get("smart") else "Gerät vor der Session im Schatten auskühlen lassen; kühle Nächte rauschen weniger."]))
    # --- Bildprobleme -----------------------------------------------------------
    cond = {f["code"] for f in out}
    for code, text in issues.items():
        what, why = EXPLAIN.get(code, (text, ""))
        tips = list(TIPS.get(code, []))
        if code == "color_gradient":
            causes = [name for c, name in (("twilight", "die Dämmerung"), ("moon", "das Mondlicht"), ("low_alt", "die geringe Objekthöhe"))
                      if c in cond]
            if causes:
                why += f" In dieser Nacht {'passt' if len(causes) == 1 else 'passen'} dazu " + " und ".join(causes) + "."
            if device.get("filter"):
                tips.append(device["filter"])
            if ctx.get("auto_fixed"):
                why += " AstroFlow hat den Verlauf bereits soweit möglich ausgeglichen."
            else:
                tips.append("„Mehr → Neu entwickeln“: AstroFlow gleicht den Farbverlauf jetzt automatisch aus.")
        elif code == "gradient" and device.get("smart"):
            tips = ["Smart-Teleskope nehmen keine Flats automatisch auf – ein heller, gleichmäßiger Hintergrund (z. B. Tablet mit weißem Bild "
                    "oder Himmel in der Dämmerung mit T-Shirt vor dem Objektiv) liefert brauchbare Flats."] + [t for t in tips if "Flat" not in t]
        out.append(_f(code, "warn", text, f"{what} {why}".strip(), tips))
    # --- Belichtungszeit ----------------------------------------------------------
    exp = ctx.get("exposure_s") or 0
    if exp and exp < 3 * 3600 and not issues.keys() & {"noisy", "faint"}:
        nightly = exp / max(1, len(nights))
        out.append(_f("exposure", "info", f"Bisher {_dur(exp)} Belichtung",
                      "Je mehr Belichtungszeit, desto weniger Rauschen und desto mehr schwache Ausläufer werden sichtbar – doppelte Zeit "
                      "bringt etwa 40 % weniger Rauschen.",
                      [f"Weitere Nächte auf dasselbe Objekt (bisher ~{_dur(nightly)} je Nacht) – AstroFlow stackt sie automatisch dazu."
                       + (" Mit Smart-Teleskopen sind 2–5 Stunden für Galaxien ein guter Richtwert." if device.get("smart") and kind == "galaxy" else "")]))
    if not out:
        out.append(_f("good", "ok", "Gute Bedingungen, sauberes Bild", "Weder die Bildprüfung noch die Nachtdaten zeigen Auffälligkeiten.", []))
    order = {"bad": 0, "warn": 1, "info": 2, "ok": 3}
    return sorted(out, key=lambda f: order[f["level"]])


def _d(night: str) -> str:
    try:
        return dt.date.fromisoformat(night).strftime("%d.%m.%Y")
    except ValueError:
        return night


def _n(x: float, nd: int = 1) -> str:
    return f"{x:.{nd}f}".replace(".", ",")


def _dur(s: float) -> str:
    h, m = int(s // 3600), int(s % 3600 // 60)
    return f"{h} h {m:02d} min" if h else f"{m} min"


# ---------------------------------------------------------------------- KI #
SYSTEM = """Du bist Astrofotografie-Tutor und prüfst ein fertig bearbeitetes Astrofoto auf Fehler.
Du bekommst Messwerte der automatischen Bildprüfung und Daten zur Aufnahmenacht (Gerät, Mond, Dämmerung, Wetter). Diese sind
korrekt – widersprich ihnen nicht und wiederhole sie nicht. Nenne nur PROBLEME, die du im Bild SIEHST (Farbverlauf mit Ort,
Flecken, Streifen, Ringe/Höfe um Sterne, verzogene Sterne, Staubschatten, Ränder, Rauschen), und konkrete Tipps passend zum Gerät.
Stil: Stichpunkte, je höchstens 12 Wörter, keine ganzen Sätze, keine Beschreibung des Objekts oder dessen, was in Ordnung ist
(also NICHT „Die Galaxie NGC 891 erscheint …“). Höchstens 4 Punkte je Liste. Nichts erfinden. Deutsch.
Antworte AUSSCHLIESSLICH mit JSON: {"kurz": "Gesamturteil in höchstens 8 Wörtern", "gesehen": ["..."], "tipps": ["..."]}"""


def facts_for_llm(name: str, kind: str | None, device: dict, quality: dict, nights: list[dict], found: list[dict]) -> dict:
    return {"objekt": name, "typ": kind, "geraet": device.get("name"), "smart_teleskop": device.get("smart"),
            "eingebaute_tauheizung": bool(device.get("dew_heater")),
            "bildpruefung": {"score": quality.get("score"), "probleme": [i["text"] for i in quality.get("issues", [])],
                             "messwerte": quality.get("metrics")},
            "naechte": [{k: n.get(k) for k in ("night", "hours", "frames", "rejected", "moon", "sun", "target", "weather", "frames_trend")} for n in nights],
            "befunde": [f["title"] for f in found]}


def ask_llm(cfg, image_path: str, facts: dict) -> dict:
    """KI-Einschätzung; cfg = reviewer.ReviewerConfig. Bild als verkleinertes JPEG."""
    import base64
    import io

    from PIL import Image

    from . import reviewer
    im = Image.open(image_path).convert("RGB")
    im.thumbnail((1280, 1280))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=88)
    user = [{"type": "text", "text": "Daten zur Aufnahme:\n" + json.dumps(facts, ensure_ascii=False, default=str)},
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()}}]
    text = re.sub(r"<think>.*?</think>", "", reviewer.chat(cfg, SYSTEM, user), flags=re.S)
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError("Die KI hat keine auswertbare Antwort geliefert.")
    d = json.loads(m.group(0))
    return {"summary": str(d.get("kurz") or d.get("zusammenfassung") or "")[:120],
            "seen": [str(x)[:160] for x in (d.get("gesehen") or [])][:4],
            "tips": [str(x)[:160] for x in (d.get("tipps") or [])][:4], "model": cfg.model}


# ------------------------------------------------------------------ Bericht #
DEFAULTS = {"mode": "einsteiger", "lat": None, "lon": None, "weather": False, "llm_provider": "off", "llm_model": ""}


def settings(store) -> dict:
    return {**DEFAULTS, **{k: v for k, v in store.get("tutor").items() if k in DEFAULTS}}


def _image(files: dict) -> str | None:
    for k in ("png", "jpg"):
        if files.get(k) and Path(files[k]).exists():
            return files[k]
    return None


def recheck(stats: dict, files: dict, sky: bool) -> dict | None:
    """Bewertung älterer Ergebnisse (vor QC_VERSION) am gespeicherten Bild nachholen – ohne neu zu entwickeln."""
    from PIL import Image

    from . import develop
    q = stats.get("quality")
    path = _image(files)
    if not q or q.get("v", 0) >= develop.QC_VERSION or not path:
        return None
    a = np.asarray(Image.open(path).convert("RGB"), np.float32)[::-1] / 255      # gespeichert gespiegelt (Norden oben)
    mono = bool(np.allclose(a[..., 0], a[..., 1], atol=1 / 255) and np.allclose(a[..., 1], a[..., 2], atol=1 / 255))
    new = develop.assess(np.moveaxis(a, -1, 0), mono=mono, neutral_sky=sky)
    return {**new, "hints": q.get("hints", []), "measure": q.get("measure"), "rechecked": True}


def project_frames(db, pid: int) -> list[dict]:
    out = []
    for x in db.q("SELECT night, status, reason, info, metrics FROM frames WHERE project_id=? AND kind='light'", (pid,)):
        i, m = json.loads(x["info"]), json.loads(x["metrics"] or "{}")
        out.append({"date": i.get("date_obs"), "night": x["night"], "status": x["status"], "reason": x["reason"], "temp": i.get("temp"),
                    "instrument": i.get("instrument"), "shape": i.get("shape"), "site": i.get("site"),
                    "n_stars": m.get("n_stars"), "fwhm": m.get("fwhm"), "ecc": m.get("ecc"), "bg": m.get("bg")})
    return out


def build_report(db, rid: int, cfg: dict, work_dir: Path, refresh: bool = False) -> dict:
    """Tutor-Bericht zu einem Ergebnis; Nachtdaten werden in results.stats.tutor zwischengespeichert."""
    r = db.one("SELECT r.*, p.id pid, p.name, p.kind, p.ra, p.dec, p.options FROM results r JOIN projects p ON p.id=r.project_id WHERE r.id=?", (rid,))
    if not r:
        raise KeyError(rid)
    stats, files = json.loads(r["stats"] or "{}"), json.loads(r["files"] or "{}")
    base = {"mode": cfg["mode"], "weather": bool(cfg["weather"]), "llm": cfg["llm_provider"] != "off"}
    if (json.loads(r["options"] or "{}").get("input_type") == "picture") or not stats.get("quality"):
        return {**base, "available": False}
    sky = neutral_sky(r["ra"], r["dec"], r["kind"])
    changed = False
    if q := recheck(stats, files, sky):
        stats["quality"], changed = q, True
    frames = project_frames(db, r["pid"])
    sites = [tuple(f["site"]) for f in frames if f.get("site")]
    site, site_src = (list(max(set(sites), key=sites.count)), "header") if sites else (
        ([float(cfg["lat"]), float(cfg["lon"])], "settings") if cfg.get("lat") not in (None, "") and cfg.get("lon") not in (None, "") else (None, None))
    key = f"{TUTOR_VERSION}|{site}|{cfg['weather']}|{len(frames)}"
    cached = stats.get("tutor") or {}
    if refresh or cached.get("key") != key:
        weather, werr = {}, None
        nights = night_conditions(frames, r["ra"], r["dec"], site)
        if cfg["weather"] and site:
            try:
                weather = fetch_weather(site, nights, work_dir / "tutor")
            except Exception as e:                 # offline/Dienst weg: Rest des Berichts bleibt
                werr = f"Wetterdaten nicht abrufbar: {str(e)[:150]}"
            nights = night_conditions(frames, r["ra"], r["dec"], site, weather) if weather else nights
        device = detect_device([f["instrument"] for f in frames], tuple(frames[0]["shape"]) if frames and frames[0].get("shape") else None)
        cached = {"key": key, "device": device, "nights": nights, "weather_error": werr}
        stats["tutor"], changed = cached, True
    if changed:
        db.x("UPDATE results SET stats=? WHERE id=?", (json.dumps(stats), rid))
    ctx = {"exposure_s": r["exposure"], "kind": r["kind"], "latitude": site and site[0],
           "auto_fixed": bool(stats.get("color_gradient") or (stats.get("params") or {}).get("color_gradient"))}
    return {**base, "available": True, "quality": stats["quality"], "device": cached["device"], "nights": cached["nights"],
            "site": site_src, "weather_error": cached.get("weather_error"), "neutral_sky": sky,
            "findings": findings(stats["quality"], cached["nights"], cached["device"], ctx), "ki": stats.get("tutor_ki")}


def llm_config(cfg: dict, review_cfg):
    """KI-Verbindung des Tutors: eigener Anbieter/Modell, Server-URL und Schlüssel vom KI-Prüfer."""
    from dataclasses import replace
    if cfg["llm_provider"] not in ("lmstudio", "openrouter"):
        return None
    c = replace(review_cfg, provider=cfg["llm_provider"], model=cfg["llm_model"] or review_cfg.model,
                base_url=review_cfg.base_url if review_cfg.provider == cfg["llm_provider"] else "")
    return c if c.enabled else None


def ask_report(db, rid: int, cfg: dict, review_cfg, work_dir: Path) -> dict:
    c = llm_config(cfg, review_cfg)
    if not c:
        raise ValueError("Für die KI-Einschätzung zuerst unter Einstellungen → Bilder → Bild-Tutor einen KI-Anbieter und ein Modell wählen.")
    rep = build_report(db, rid, cfg, work_dir)
    if not rep.get("available"):
        raise ValueError("Für dieses Bild gibt es keine Bildprüfung.")
    r = db.one("SELECT r.files, r.stats, p.name, p.kind FROM results r JOIN projects p ON p.id=r.project_id WHERE r.id=?", (rid,))
    files, stats = json.loads(r["files"]), json.loads(r["stats"])
    img = files.get("jpg") if files.get("jpg") and Path(files["jpg"]).exists() else _image(files)
    ki = {**ask_llm(c, img, facts_for_llm(r["name"], r["kind"], rep["device"], rep["quality"], rep["nights"], rep["findings"])),
          "created": dt.datetime.now(dt.timezone.utc).isoformat()}
    stats["tutor_ki"] = ki
    db.x("UPDATE results SET stats=? WHERE id=?", (json.dumps(stats), rid))
    return ki
