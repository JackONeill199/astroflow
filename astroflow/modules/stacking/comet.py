"""Kometenmodus: Erkennung, Kometenbahn je Aufnahme und Zusammensetzen aus Sterne- und Kometen-Stack.

Ein Komet bewegt sich gegenüber den Sternen (C/2022 E3 am 29.01.2023: ~11 px/min bei Stellina). Auf die Sterne gestackt
verschmiert er, auf den Kern gestackt werden die Sterne zu Strichen. Deshalb zwei Stacks auf demselben Referenzraster:
(a) wie immer auf die Sterne registriert, (b) zusätzlich je Aufnahme um die Kometenbewegung verschoben (Sterne vorher
maskiert, Reste fallen per Clipping weg). Das Ergebnis setzt Sterne aus (a) und Komet + Himmel aus (b) zusammen.

Kometenbahn (Position im Referenzraster über der Zeit):
- bevorzugt JPL Horizons (nur mit auflösbarer Bezeichnung, Netz und plate-gelöstem Stack; Cache in work/komet/),
  topozentrisch lokal gerechnet – der Standort verlässt den Rechner nie;
- sonst bzw. zusätzlich aus den Bildern: Einzelbild minus sternfreier Referenz zeigt nur, was sich bewegt;
  der Kern wird je Stichprobe gesucht und eine Gerade robust über die Zeit gefittet.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import os
import re
from pathlib import Path

import numpy as np
from scipy import ndimage as ndi

HORIZONS_URL = "https://ssd.jpl.nasa.gov/api/horizons.api"
HORIZONS_TIMEOUT = 25
SAMPLE = 36                    # Aufnahmen für die Bildsuche (über die Zeit verteilt)
MIN_MOTION_FWHM = 1.0          # Gesamtbewegung unter 1 FWHM: ein Stack genügt
COMPACT_FWHM = 3.0             # darunter bleibt der Komet im Sterne-Stack kompakt (sieht für die Sternmaske wie ein Stern aus)
REBUILD_PX = 0.7               # Bahn weicht für bereits gestackte Aufnahmen stärker ab -> Kometen-Stack neu aufbauen

# „C/2022 E3“, „C_2022_E3“, „C 2022 E3 (ZTF)“, „P/2019 LD2“ – Buchstabe nicht mitten in einem Wort (IC 1396, NGC 2024)
_DESIG = re.compile(r"(?<![A-Za-z0-9])([CPDXAI])\s*[/_ -]?\s*((?:18|19|20)\d\d)\s*[_ -]?\s*([A-HJ-Y][A-Z]?)\s*[_ -]?(\d{0,3})(?![A-Za-z0-9])")
# periodische Kometen „12P“, „12P/Pons-Brooks“, „0029P“
_PERIODIC = re.compile(r"(?<![A-Za-z0-9])0*(\d{1,4})([PD])(?:\s*/\s*[A-Za-z][\w' -]*)?(?![A-Za-z0-9])")
_WORD = re.compile(r"\b(komet|kometen|comet|comete|cometa)\b", re.I)


# ------------------------------------------------------------------ Erkennung #
def designation(name: str | None) -> str | None:
    """Offizielle Bezeichnung (für Horizons) aus Objekt- oder Ordnernamen, sonst None."""
    if not name:
        return None
    s = str(name)
    m = _DESIG.search(s)
    if m:
        return f"{m.group(1)}/{m.group(2)} {m.group(3)}{m.group(4)}"
    m = _PERIODIC.search(s)
    if m:
        return f"{int(m.group(1))}{m.group(2)}"
    return None


def is_comet(name: str | None) -> bool:
    return bool(name) and (designation(name) is not None or bool(_WORD.search(str(name))))


def display_name(name: str) -> str:
    """„C 2022 E3“ (aus dem Stellina-Ordnernamen) -> „C/2022 E3“; ohne Bezeichnung bleibt der Name."""
    d = designation(name)
    return d or str(name).strip()


def mode_of(opt: dict) -> str:
    """Projektoption comet_mode: auto (Standard) | on | off. Eigener Name, weil die Auftragsoption „mode“ schon
    „develop“ (nur neu entwickeln) bedeutet."""
    v = str(opt.get("comet_mode") or "auto").lower()
    return v if v in ("on", "off") else "auto"


def active(proj: dict, opt: dict) -> bool:
    m = mode_of(opt)
    if m != "auto":
        return m == "on"
    return proj.get("kind") == "comet" or is_comet(proj.get("name"))


# ------------------------------------------------------------------ Bahnmodell #
class Track:
    """Position im Referenzraster (Pixel, x = Spalte, y = Zeile) als Polynom der Zeit (s relativ zu t0)."""

    def __init__(self, t0: float, cx, cy, source: str, info: dict | None = None):
        self.t0, self.cx, self.cy, self.source = float(t0), [float(v) for v in cx], [float(v) for v in cy], source
        self.info = info or {}

    def at(self, t) -> np.ndarray:
        dt_ = np.asarray(t, float) - self.t0
        x = sum(c * dt_ ** i for i, c in enumerate(self.cx))
        y = sum(c * dt_ ** i for i, c in enumerate(self.cy))
        return np.stack([x, y], -1)

    def rate(self) -> float:
        """Geschwindigkeit in Pixel/s bei t0."""
        return float(math.hypot(self.cx[1] if len(self.cx) > 1 else 0, self.cy[1] if len(self.cy) > 1 else 0))

    def to_dict(self) -> dict:
        return {"t0": self.t0, "cx": self.cx, "cy": self.cy, "source": self.source, **self.info}

    @classmethod
    def from_dict(cls, d: dict | None) -> "Track | None":
        if not d or "cx" not in d:
            return None
        return cls(d["t0"], d["cx"], d["cy"], d.get("source", "?"), {k: v for k, v in d.items() if k not in ("t0", "cx", "cy", "source")})

    @classmethod
    def fit(cls, t, xy, source: str, deg: int = 1, t0: float | None = None, w=None, info: dict | None = None) -> "Track":
        t = np.asarray(t, float)
        xy = np.asarray(xy, float)
        t0 = float(np.median(t)) if t0 is None else t0
        deg = min(deg, max(0, len(t) - 1))
        cx = np.polynomial.polynomial.polyfit(t - t0, xy[:, 0], deg, w=w)
        cy = np.polynomial.polynomial.polyfit(t - t0, xy[:, 1], deg, w=w)
        return cls(t0, list(cx), list(cy), source, info)


def robust_line(t, xy, deg: int = 1, tol: float = 1.5, iters: int = 4):
    """Gerade (bzw. Parabel) durch Positionen, Ausreißer iterativ verwerfen. Gibt (Track|None, Inlier-Maske)."""
    t, xy = np.asarray(t, float), np.asarray(xy, float)
    keep = np.ones(len(t), bool)
    tr = None
    for _ in range(iters):
        if keep.sum() < max(3, deg + 2):
            return None, keep
        tr = Track.fit(t[keep], xy[keep], "bild", deg)
        res = np.hypot(*(tr.at(t) - xy).T)
        s = max(tol, 3 * 1.4826 * float(np.median(res[keep])))
        new = res <= s
        if (new == keep).all():
            break
        keep = new
    return tr, keep


# ------------------------------------------------------------------ Horizons #
def horizons_enabled() -> bool:
    return os.environ.get("ASTROFLOW_HORIZONS", "1") != "0"


def _http_get(params: dict, timeout: float) -> dict:
    import httpx

    r = httpx.get(HORIZONS_URL, params=params, timeout=timeout)
    r.raise_for_status()
    return r.json()


def _hz_time(t: float) -> str:
    return dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime("%Y-%m-%d %H:%M")


def parse_horizons(text: str) -> np.ndarray:
    """CSV-Tabelle (QUANTITIES 1,20, ANG_FORMAT DEG) -> Zeilen [Unixzeit, RA, Dec, Abstand AU]."""
    if "$$SOE" not in text:
        tail = " ".join(text.strip().splitlines()[-3:])[:200]
        raise ValueError(f"Horizons liefert keine Ephemeride ({tail or 'leere Antwort'})")
    head, body = text.split("$$SOE", 1)
    body = body.split("$$EOE", 1)[0]
    cols = [c.strip() for c in head.strip().splitlines()[-2].split(",")]
    ira = next(i for i, c in enumerate(cols) if c.startswith("R.A."))
    idec = next(i for i, c in enumerate(cols) if c.startswith("DEC"))
    idel = next((i for i, c in enumerate(cols) if c == "delta"), None)
    rows = []
    for line in body.strip().splitlines():
        p = [x.strip() for x in line.split(",")]
        d = dt.datetime.strptime(p[0], "%Y-%b-%d %H:%M").replace(tzinfo=dt.timezone.utc) if len(p[0]) <= 17 else \
            dt.datetime.strptime(p[0][:20], "%Y-%b-%d %H:%M:%S").replace(tzinfo=dt.timezone.utc)
        rows.append([d.timestamp(), float(p[ira]), float(p[idec]), float(p[idel]) if idel is not None and p[idel] else float("nan")])
    if len(rows) < 2:
        raise ValueError("Horizons-Ephemeride zu kurz")
    return np.array(rows)


def horizons(desig: str, t_start: float, t_stop: float, cache_dir: Path, timeout: float = HORIZONS_TIMEOUT) -> np.ndarray:
    """Geozentrische Ephemeride (astrometrisch, ICRF) zwischen zwei Unixzeiten, gecacht. Wirft bei Fehlern."""
    if not horizons_enabled():
        raise RuntimeError("Horizons abgeschaltet")
    t0 = math.floor(t_start / 60) * 60 - 600
    t1 = math.ceil(t_stop / 60) * 60 + 600
    step = max(2, int(math.ceil((t1 - t0) / 60 / 200)))                # höchstens ~200 Zeilen
    params = {"format": "json", "COMMAND": f"'DES={desig};CAP;NOFRAG'", "OBJ_DATA": "'NO'", "MAKE_EPHEM": "'YES'",
              "EPHEM_TYPE": "'OBSERVER'", "CENTER": "'500@399'", "START_TIME": f"'{_hz_time(t0)}'",
              "STOP_TIME": f"'{_hz_time(t1)}'", "STEP_SIZE": f"'{step}m'", "QUANTITIES": "'1,20'", "ANG_FORMAT": "'DEG'",
              "CSV_FORMAT": "'YES'", "EXTRA_PREC": "'YES'"}
    key = hashlib.sha1(json.dumps(params, sort_keys=True).encode()).hexdigest()[:16]
    f = Path(cache_dir) / f"horizons_{key}.json"
    if f.exists():
        return np.array(json.loads(f.read_text())["rows"])
    res = _http_get(params, timeout)
    rows = parse_horizons(res.get("result") or res.get("error") or "")
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps({"desig": desig, "rows": rows.tolist()}))
    return rows


def topocentric(rows: np.ndarray, t: np.ndarray, site: list | None) -> tuple[np.ndarray, np.ndarray, bool]:
    """Ephemeride auf Zeiten t interpolieren und – mit Standort – lokal auf den Beobachtungsort umrechnen
    (Parallaxe: C/2022 E3 bei 0,3 AU ~30″, ändert sich über eine Stunde um mehrere Pixel). Gibt (RA, Dec, topozentrisch)."""
    from scipy.interpolate import CubicSpline

    tt = rows[:, 0]
    ra = np.unwrap(np.radians(rows[:, 1]))
    dec = np.radians(rows[:, 2])
    ra_t = CubicSpline(tt, ra)(t)
    dec_t = CubicSpline(tt, dec)(t)
    delta = rows[:, 3]
    if site is None or not np.isfinite(delta).all():
        return np.degrees(ra_t) % 360, np.degrees(dec_t), False
    try:
        import warnings

        import astropy.units as u
        from astropy.coordinates import EarthLocation
        from astropy.time import Time
        from astropy.utils import iers

        d_t = np.interp(t, tt, delta) * 1.495978707e11                 # m
        vec = np.stack([np.cos(dec_t) * np.cos(ra_t), np.cos(dec_t) * np.sin(ra_t), np.sin(dec_t)], -1) * d_t[:, None]
        with warnings.catch_warnings(), iers.conf.set_temp("auto_download", False), iers.conf.set_temp("auto_max_age", None):
            warnings.simplefilter("ignore")
            loc = EarthLocation(lat=site[0] * u.deg, lon=site[1] * u.deg)
            pos, _ = loc.get_gcrs_posvel(Time(t, format="unix"))
        obs = np.stack([pos.x.to_value(u.m), pos.y.to_value(u.m), pos.z.to_value(u.m)], -1)
        v = vec - obs
        r = np.linalg.norm(v, axis=1)
        return np.degrees(np.arctan2(v[:, 1], v[:, 0])) % 360, np.degrees(np.arcsin(v[:, 2] / r)), True
    except Exception:
        return np.degrees(ra_t) % 360, np.degrees(dec_t), False


def ephemeris_pixels(desig: str, times: np.ndarray, wcs_hdr: dict, scale: int, site: list | None, cache_dir: Path) -> tuple[np.ndarray, bool]:
    """Kometenposition aus Horizons im Referenzraster (Pixel ohne Überabtastung). Gibt (xy (N,2), topozentrisch)."""
    from .photometry import wcs_from

    rows = horizons(desig, float(times.min()), float(times.max()), cache_dir)
    ra, dec, topo = topocentric(rows, times, site)
    x, y = wcs_from(wcs_hdr).world_to_pixel_values(ra, dec)              # Stack-Raster, 0-basiert
    xy = (np.stack([x, y], -1) + 0.5) / scale - 0.5                      # -> Referenzraster
    return xy, topo


# ------------------------------------------------------------------ Bildsuche #
def bin_image(a: np.ndarray, b: int) -> np.ndarray:
    if b <= 1:
        return a.astype(np.float32)
    h, w = a.shape[-2] // b * b, a.shape[-1] // b * b
    return a[..., :h, :w].reshape(*a.shape[:-2], h // b, b, w // b, b).mean((-3, -1)).astype(np.float32)


def binned_transform(M: np.ndarray, b: int) -> np.ndarray:
    """Transformation Quelle -> Referenz (volle Pixel) auf gebinnte Raster umrechnen."""
    B = np.array([[b, 0, (b - 1) / 2], [0, b, (b - 1) / 2], [0, 0, 1]], float)
    return np.linalg.inv(B) @ M @ B


def _noise(a: np.ndarray) -> float:
    v = a[np.isfinite(a)]
    if v.size == 0:
        return 1.0
    v = v[:: max(1, v.size // 200000)]
    return float(1.4826 * np.median(np.abs(v - np.median(v)))) or 1.0


def _peaks(d: np.ndarray, k: int = 5, thr: float = 5.0, min_dist: int = 4) -> list[tuple[float, float, float]]:
    """Hellste lokale Maxima (x, y, Höhe in σ) eines geglätteten Differenzbildes."""
    s = _noise(d)
    mx = ndi.maximum_filter(d, size=2 * min_dist + 1)
    ys, xs = np.nonzero((d == mx) & (d > thr * s))
    if not len(ys):
        return []
    order = np.argsort(d[ys, xs])[::-1][:k]
    return [(float(xs[i]), float(ys[i]), float(d[ys[i], xs[i]] / s)) for i in order]


def _subpixel(d: np.ndarray, x: int, y: int) -> tuple[float, float]:
    """Parabel durch das Maximum und seine Nachbarn."""
    H, W = d.shape
    if not (0 < x < W - 1 and 0 < y < H - 1):
        return float(x), float(y)
    def off(a, b, c):
        den = a - 2 * b + c
        return 0.0 if den >= 0 else float(np.clip(0.5 * (a - c) / den, -0.5, 0.5))
    return x + off(d[y, x - 1], d[y, x], d[y, x + 1]), y + off(d[y - 1, x], d[y, x], d[y + 1, x])


def _smooth_diff(d: np.ndarray, sigma: float) -> np.ndarray:
    d = np.where(np.isfinite(d), d, 0).astype(np.float32)
    d = ndi.median_filter(d, size=3)                    # Stern-/Hotpixelreste (≈ 1 Pixel) weg, Koma bleibt
    return ndi.gaussian_filter(d, sigma)


def ransac_track(t: np.ndarray, cands: list[list[tuple]], tol: float, min_inliers: int):
    """Gerade durch je einen Kandidaten pro Aufnahme. Gibt (Track|None, Punkte [(t, x, y)])."""
    t = np.asarray(t, float)
    idx = [i for i, c in enumerate(cands) if c]
    if len(idx) < min_inliers:
        return None, []
    best, best_score, best_pts = None, 0.0, []
    rng = np.random.default_rng(0)
    pairs = [(i, j) for a, i in enumerate(idx) for j in idx[a + 1:] if t[j] - t[i] > 0.25 * (t[idx[-1]] - t[idx[0]])]
    if len(pairs) > 400:
        pairs = [pairs[k] for k in rng.choice(len(pairs), 400, replace=False)]
    for i, j in pairs:
        for ci in cands[i][:3]:
            for cj in cands[j][:3]:
                v = (np.array(cj[:2]) - np.array(ci[:2])) / (t[j] - t[i])
                pts, score = [], 0.0
                for k in idx:
                    pred = np.array(ci[:2]) + v * (t[k] - t[i])
                    dd = [math.hypot(c[0] - pred[0], c[1] - pred[1]) for c in cands[k]]
                    m = int(np.argmin(dd))
                    if dd[m] <= tol:
                        pts.append((t[k], cands[k][m][0], cands[k][m][1]))
                        score += 1 + min(cands[k][m][2], 50) / 50
                if score > best_score:
                    best_score, best_pts = score, pts
    if len(best_pts) < min_inliers:
        return None, []
    tt = np.array([p[0] for p in best_pts])
    xy = np.array([p[1:] for p in best_pts])
    best, keep = robust_line(tt, xy)
    return best, [p for p, k in zip(best_pts, keep) if k]


def locate(samples: list[tuple[float, np.ndarray]], ref_L: np.ndarray, b: int, fwhm: float,
           prior: Track | None = None, log=lambda m: None) -> dict:
    """Kometenkern in Stichproben suchen. samples: (Zeit, Luminanz auf dem gebinnten Referenzraster, NaN = ungültig);
    ref_L: Luminanz des Sterne-Stacks auf demselben Raster. prior: Bahn aus der Ephemeride (dann nur Feinabgleich).
    Gibt {"track": Track (gebinnte Pixel) | None, "points": [(t, x, y)], "coma_px": Radius (gebinnt), "error": …}."""
    t = np.array([s[0] for s in samples])
    sig = max(1.0, 1.2 * fwhm / b)
    # Grobsuche: Einzelbild minus Sterne-Stack – stillstehende Sterne/Nebel heben sich auf, der Komet bleibt hell
    # (im Sterne-Stack ist er zu einer schwachen Spur verschmiert)
    if prior is None:
        cands = []
        for ti, L in samples:
            cands.append(_peaks(_smooth_diff(L - ref_L, sig), k=5, min_dist=max(3, int(3 * sig))))
        tol = max(2.5, 2.0 * sig)
        track, pts = ransac_track(t, cands, tol, max(4, int(0.4 * len(samples))))
        if track is None:
            return {"track": None, "points": [], "error": "Kometenkern in den Einzelbildern nicht gefunden"}
    else:
        track, pts = prior, []
    # Feinabgleich: Einzelbild minus Sterne-Stack (Sterne heben sich auf) entlang der vermuteten Bahn ausschneiden und
    # mitteln – mit der richtigen Bahn wird der Kern am schärfsten. Optimiert werden Versatz und eine lineare
    # Geschwindigkeitskorrektur (die Krümmung einer Ephemeride bleibt). Positionen je Einzelbild wären durch die im
    # Sterne-Stack verschmierte Kometenspur verzerrt (Geschwindigkeit synthetisch ~25 % zu hoch).
    out = {"points": pts}
    span_t = float(t.max() - t.min())
    if span_t < 1:
        return {**out, "track": track, "refined": False}
    # Referenz ohne Kometenkern: Median der Stichproben im Sternraster – der Kern liegt an jedem Ort nur in wenigen
    # Aufnahmen (der Sterne-Stack selbst enthält die Spur als Mittel und verzerrt die Geschwindigkeit nach oben)
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        med = np.nanmedian(np.stack([L for _, L in samples]), 0)
    ref = np.where(np.isfinite(med), med, ref_L)
    diffs = [(L - ref).astype(np.float32) for _, L in samples]
    R = int(np.clip(round(max(4 * sig, 12)), 12, 60))
    gy, gx = np.mgrid[-R:R + 1, -R:R + 1].astype(np.float32)
    base = track.at(t)
    t0 = track.t0

    def template(dv):
        c = base + np.outer(t - t0, dv)
        crops = np.empty((len(t), 2 * R + 1, 2 * R + 1), np.float32)
        for k, d in enumerate(diffs):
            crops[k] = ndi.map_coordinates(d, [gy + c[k, 1], gx + c[k, 0]], order=1, cval=np.nan, prefilter=False)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            return np.nan_to_num(np.nanmedian(crops, 0))

    inner = (gx ** 2 + gy ** 2) <= (0.6 * R) ** 2
    s1 = max(0.8, fwhm / 2.355 / b)                     # Kern ~ sternförmig: schmaler Bandpass
    s2 = max(3 * s1, 0.6 * sig)

    def score(dv):
        tm = template(dv)
        dog = ndi.gaussian_filter(tm, s1) - ndi.gaussian_filter(tm, s2)
        v = np.where(inner, dog, -np.inf)
        k = int(np.argmax(v))
        return float(v.flat[k]), dog, divmod(k, 2 * R + 1)

    v0 = track.rate()
    rng_v = (0.5 * v0 + 3 * sig / span_t) if prior is None else max(4 * sig / span_t, 0.05 * v0)
    best = np.zeros(2)
    best_s, dog, pk = score(best)
    for _ in range(4):                                  # Gitter 7×7, dreimal verfeinert
        grid = np.linspace(-rng_v, rng_v, 7)
        nb = best
        for gxv in grid:
            for gyv in grid:
                dv = best + (gxv, gyv)
                sc, d_, p_ = score(dv)
                if sc > best_s:
                    best_s, dog, pk, nb = sc, d_, p_, dv
        best = nb
        rng_v /= 3
    noise = _noise(dog[~inner]) if (~inner).any() else 1.0
    out["sharpness"] = round(best_s / max(noise, 1e-9), 1)
    tm = template(best)
    py0, px0 = pk
    rr = np.hypot(gx - (px0 - R), gy - (py0 - R))
    peak = float(ndi.gaussian_filter(tm, s1)[py0, px0])
    coma = float(R)
    for r_ in np.arange(1, R):                          # Koma-Radius: Profil fällt unter 10 % des Kerns
        ring = (rr >= r_ - 0.5) & (rr < r_ + 0.5)
        if ring.any() and float(np.mean(tm[ring])) < 0.1 * peak:
            coma = float(r_)
            break
    out["coma_px"] = coma
    if best_s < 6 * noise:
        return {**out, "track": track, "refined": False}
    py, px = pk
    dx, dy = _subpixel(dog, int(px), int(py))
    dc = np.array([dx - R, dy - R])
    cx, cy = list(track.cx), list(track.cy)
    while len(cx) < 2:
        cx.append(0.0)
        cy.append(0.0)
    cx[0] += dc[0]
    cy[0] += dc[1]
    cx[1] += best[0]
    cy[1] += best[1]
    return {**out, "track": Track(t0, cx, cy, track.source), "refined": True,
            "offset_px": float(np.hypot(*dc)), "dv_px_s": [float(best[0]), float(best[1])]}


def scale_track(tr: Track, b: int) -> Track:
    """Bahn vom gebinnten Raster auf volle Referenzpixel."""
    cx = [(tr.cx[0] + 0.5) * b - 0.5] + [c * b for c in tr.cx[1:]]
    cy = [(tr.cy[0] + 0.5) * b - 0.5] + [c * b for c in tr.cy[1:]]
    return Track(tr.t0, cx, cy, tr.source, tr.info)


def unscale_track(tr: Track, b: int) -> Track:
    cx = [(tr.cx[0] + 0.5) / b - 0.5] + [c / b for c in tr.cx[1:]]
    cy = [(tr.cy[0] + 0.5) / b - 0.5] + [c / b for c in tr.cy[1:]]
    return Track(tr.t0, cx, cy, tr.source, tr.info)


# ------------------------------------------------------------------ Masken #
def corridor(shape: tuple[int, int], pts: np.ndarray, radius: float) -> np.ndarray:
    """Pixel näher als `radius` an der Kometenbahn (Polylinie, Pixelraster)."""
    H, W = shape
    line = np.zeros((H, W), bool)
    for (x0, y0), (x1, y1) in zip(pts[:-1], pts[1:]):
        n = int(max(abs(x1 - x0), abs(y1 - y0))) + 2
        xs = np.clip(np.round(np.linspace(x0, x1, n)).astype(int), 0, W - 1)
        ys = np.clip(np.round(np.linspace(y0, y1, n)).astype(int), 0, H - 1)
        line[ys, xs] = True
    if len(pts) == 1:
        line[int(np.clip(round(pts[0][1]), 0, H - 1)), int(np.clip(round(pts[0][0]), 0, W - 1))] = True
    # Abstand auf 4-fach verkleinertem Raster genügt (Radius ≥ einige 10 px) und ist schnell
    f = 4 if min(H, W) > 400 else 1
    small = line[: H // f * f, : W // f * f].reshape(H // f, f, W // f, f).any((1, 3)) if f > 1 else line
    dist = ndi.distance_transform_edt(~small) * f
    if f > 1:
        dist = np.repeat(np.repeat(dist, f, 0), f, 1)
        dist = np.pad(dist, ((0, H - dist.shape[0]), (0, W - dist.shape[1])), mode="edge")
    return dist < radius


def star_mask_for(L: np.ndarray, fwhm: float, protect: np.ndarray | None, lane: np.ndarray | None = None) -> np.ndarray:
    """Sterne des Sterne-Stacks (kompakte Quellen, etwas vergrößert) für den Kometen-Stack ausblenden.
    protect: Bereich, dessen berührte Maskenteile frei bleiben (sehr langsamer Komet = kompakt wie ein Stern).
    lane: schmaler Streifen entlang der Kometenbahn im Sterne-Stack. Maskenteile darauf, deren Licht nicht im Kern
    sitzt, sind die verschmierte Koma selbst und bleiben frei (sonst fehlte im Kometen-Stack der Kopf)."""
    from .stars import star_mask

    small, big, _, _ = star_mask(L, fwhm)
    bg = float(np.median(L[::4, ::4]))
    r = 1.5 * fwhm
    parts = []
    for m in (small, big):                              # getrennt: Sterne in der Koma bleiben eigene, kompakte Teile
        lab, n = ndi.label(m)
        if n and protect is not None:
            hit = np.unique(lab[protect & m])
            m = m & ~np.isin(lab, hit[hit > 0])
        if n and lane is not None:
            objs = ndi.find_objects(lab)
            level = float(np.median(L[lane])) - bg               # Helligkeit der Kometenspur im Sterne-Stack
            drop = []
            for l in np.unique(lab[lane & m]):
                if l == 0:
                    continue
                sl = objs[l - 1]
                v = np.where(lab[sl] == l, L[sl] - bg, 0)
                py, px = np.unravel_index(int(np.argmax(v)), v.shape)
                yy, xx = np.mgrid[0:v.shape[0], 0:v.shape[1]]
                core = float(v[(xx - px) ** 2 + (yy - py) ** 2 <= r * r].sum())
                # Licht verteilt (verschmierte Koma) oder kaum heller als die Spur (Stück der Kernspur eines
                # schnellen Kometen) -> kein Stern
                if core < 0.35 * max(float(v.sum()), 1e-9) or float(v[py, px]) < 2.5 * max(level, 0.0):
                    drop.append(l)
            if drop:
                m = m & ~np.isin(lab, drop)
        parts.append(m)
    m = parts[0] | parts[1]
    g = max(1, int(round(fwhm)))
    return ndi.binary_dilation(m, iterations=g)


def shift_mask(m: np.ndarray, dx: float, dy: float) -> np.ndarray:
    """Maske um (dx, dy) Pixel verschieben (ganzzahlig, außerhalb False)."""
    ix, iy = int(round(dx)), int(round(dy))
    H, W = m.shape
    out = np.zeros_like(m)
    ys, yd = (slice(0, H - iy), slice(iy, H)) if iy >= 0 else (slice(-iy, H), slice(0, H + iy))
    xs, xd = (slice(0, W - ix), slice(ix, W)) if ix >= 0 else (slice(-ix, W), slice(0, W + ix))
    if abs(ix) < W and abs(iy) < H:
        out[yd, xd] = m[ys, xs]
    return out


# ------------------------------------------------------------------ Zusammensetzen #
def drop_ghosts(stars: np.ndarray, lane: np.ndarray, fwhm: float) -> tuple[np.ndarray, int]:
    """Längliche „Sterne“ auf der Kometenbahn aus dem Sternbild entfernen: Stellen, an denen der Komet im Sterne-Stack
    länger verweilte (z. B. erste kurze Serie vor einer Pause), sind kompakt genug für die Sterntrennung, aber in
    Bewegungsrichtung verschmiert – im Ergebnis stünde sonst ein zweiter, verwaschener Kometenkopf."""
    Ls = stars.mean(0)
    thr = 3 * _noise(Ls)
    lab, n = ndi.label(ndi.gaussian_filter(Ls, 0.5 * fwhm) > thr)
    if not n:
        return stars, 0
    objs = ndi.find_objects(lab)
    kill = np.zeros(Ls.shape, bool)
    count = 0
    for l in np.unique(lab[lane]):
        if l == 0:
            continue
        sl = objs[l - 1]
        comp = lab[sl] == l
        if comp.sum() < np.pi * fwhm ** 2:
            continue
        w = np.where(comp, np.maximum(Ls[sl], 0), 0)
        yy, xx = np.mgrid[0:w.shape[0], 0:w.shape[1]]
        tot = float(w.sum()) or 1.0
        my, mx = (w * yy).sum() / tot, (w * xx).sum() / tot
        cyy, cxx = (w * (yy - my) ** 2).sum() / tot, (w * (xx - mx) ** 2).sum() / tot
        cxy = (w * (yy - my) * (xx - mx)).sum() / tot
        ev = np.linalg.eigvalsh(np.array([[cyy, cxy], [cxy, cxx]]))
        if ev[0] > 0 and math.sqrt(ev[1] / ev[0]) > 1.8:
            kill[sl] |= comp
            count += 1
    if count:
        soft = np.clip(ndi.gaussian_filter(ndi.binary_dilation(kill, iterations=max(1, int(fwhm))).astype(np.float32), 0.5 * fwhm) * 1.5, 0, 1)
        stars = stars * (1 - soft)[None]
    return stars, count



def compose(A: np.ndarray, covA: np.ndarray, B: np.ndarray, covB: np.ndarray, fwhm: float, nucleus: tuple[float, float],
            track_pts: np.ndarray, lane_px: float, core_px: float, method: str = "auto",
            log=lambda m: None) -> tuple[np.ndarray, np.ndarray, dict]:
    """Sterne aus dem Sterne-Stack A + Komet/Himmel aus dem Kometen-Stack B (beide linear, gleiches Raster/Flussmaß).
    nucleus: Kernposition in B (Rasterpixel), track_pts: Bahn in A. lane_px > 0: Sterne aus A entlang der Bahn weglassen
    (nur bei langsamen Kometen nötig – dort ist der Kern auch im Sterne-Stack kompakt und gälte als Stern).
    core_px: um den Kern im Kometen-Stack nichts als Stern entfernen. Gibt (Bild, Abdeckung, Info)."""
    from .stars import separate

    H, W = A.shape[1:]
    sA, stars_A, how = separate(A, fwhm, method, log)
    ghosts = 0
    if lane_px > 0:
        lane = corridor((H, W), track_pts, lane_px)
        soft = ndi.gaussian_filter(lane.astype(np.float32), max(1.0, fwhm))
        stars_A = stars_A * (1 - np.clip(soft * 1.5, 0, 1))[None]
    else:
        stars_A, ghosts = drop_ghosts(stars_A, corridor((H, W), track_pts, 3 * fwhm), fwhm)
    # Kometen-Stack: verbliebene Sternreste entfernen, Kern und innere Koma schützen
    sB, stars_B, _ = separate(B, fwhm, method, log)
    yy, xx = np.mgrid[0:H, 0:W]
    core = np.clip(2.0 - np.hypot(xx - nucleus[0], yy - nucleus[1]) / max(core_px, 4 * fwhm), 0, 1).astype(np.float32)
    clean_B = B - stars_B * (1 - core)[None]
    # Lücken (alle Aufnahmen dort maskiert oder außerhalb) aus dem sternlosen Sterne-Stack füllen
    hole = covB < 0.05
    if hole.any():
        clean_B = np.where(hole[None], sA, clean_B)
    out = (clean_B + stars_A).astype(np.float32)
    # Abdeckung für den Ausschnitt geometrisch: Sterne-Abdeckung um die Kometenverschiebungen versetzt gemittelt.
    # Die gemessene Abdeckung des Kometen-Stacks ist an jedem maskierten Stern gedrückt und schnitte das Bild zu eng zu.
    shifts = np.asarray(nucleus, float)[None] - np.asarray(track_pts, float)
    geo = np.mean([ndi.shift(covA, (dy, dx), order=0, cval=0.0) for dx, dy in shifts], 0)
    cov = np.minimum(covA, geo).astype(np.float32)
    return out, cov, {"stars": how, "holes": round(float((hole & (geo > 0.5)).mean()), 4), "ghosts": ghosts}
