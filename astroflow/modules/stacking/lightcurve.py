"""Lichtkurven veränderlicher Sterne aus den Einzelbildern (Wunsch 8).

Grundlage: die Plate-Solving-Lösung des Master-Stacks (`work/p<id>/wcs.json`), der Stack-Zustand (`stack/state.json`: Referenzbild,
Sternliste, Maßstab) und die noch vorhandenen, gestackten Rohbilder.
1. **Ziele:** Gaia-DR3-Sterne im Feld (gemeinsame Schicht `photometry.gaia_field`, derselbe Zwischenspeicher wie die Tiefe),
   bekannte Veränderliche aus AAVSO VSX (VizieR-Spiegel, sonst SIMBAD-Objekttypen). Gesendet werden nur Feldmitte und Radius –
   nie der Standort. Ohne Netz: nur Gaia aus dem Zwischenspeicher bzw. eigene Erkennung, ohne Gaia keine Eichung (Fehler).
2. **Fotometrie je Rohbild:** Positionen Himmel → Stack → Referenzbild → Aufnahme (Umkehrung der Registrierung aus den
   gespeicherten Sternlisten, danach affine Feinanpassung an hellen Sternen). Kein Warpen, also kein Interpolationsrauschen.
   Aperturfotometrie (Radius 1,5 × FWHM der Aufnahme, Subpixel-Mitte, Hintergrundring) auf den grünen Bayer-Pixeln bzw. dem
   Mono-Bild. Übersättigung: abgeflachter Kern (Spitze/Fluss deutlich unter dem Wert heller, sauberer Sterne).
3. **Differenzfotometrie:** V der Vergleichssterne aus Gaia G und BP−RP (Riello et al. 2021). Erst ein globales Ensemble je
   Aufnahme (Nullpunkt + Farbterm), schwankende Vergleichssterne werden iterativ verworfen, Aufnahmen mit Lichtverlust (Wolken,
   Dunst) oder großer Ensemble-Streuung markiert. Danach je Stern ein eigenes Ensemble der nächsten Vergleichssterne in
   Helligkeit, Farbe und Ort. Fehler = Hintergrund- und Photonenrauschen (Verstärkung aus den konstanten Sternen geschätzt) ⊕
   Unsicherheit des Ensemble-Nullpunkts.
4. **Auswertung:** Streuung gegen die erwartete Streuung bei dieser Helligkeit (laufender Median aller Sterne), glatter Verlauf
   (von-Neumann-Verhältnis) → „möglicher Veränderlicher“ (nie „Entdeckung“). Periode aus dem Katalog, sonst Lomb-Scargle nur bei
   mindestens 1,5 erfassten Zyklen. Zeiten: Belichtungsmitte → BJD_TDB und HJD (astropy, geozentrisch – der Standort wird nicht
   gebraucht; Fehler < 0,03 s), Lichtlaufzeit für die Feldmitte (im Feld < 10 s Unterschied).
Ergebnis: Zusammenfassung in `results.stats.lightcurves` (nicht in `_slim`), Kurven in `work/p<id>/lichtkurven_r<rid>.json`.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

from . import photometry, registration, services
from .fits_io import to_channels

LC_VERSION = 1
MIN_FRAMES = 12                    # so viele brauchbare Aufnahmen braucht eine Lichtkurve
MAX_STARS = 2500                   # gemessene Gaia-Sterne (hellste zuerst)
MAX_CANDIDATES = 40                # „mögliche Veränderliche“ höchstens
MAX_KNOWN = 400                    # bekannte Veränderliche, deren Kurven gespeichert werden
SNR_FRAME = 4.0                    # Mindest-SNR je Aufnahme (aus dem Stack-SNR / √N abgeschätzt)
APER = 1.0                         # Aperturradius in FWHM der Aufnahme (Hintergrund-begrenzt: kleiner = besseres SNR; Stellina M3 gemessen)
N_COMP = 15                        # Vergleichssterne je Ensemble
MIN_COMP = 5
CLOUD_DMAG = 0.4                   # mag Lichtverlust gegenüber dem Median der Nacht → Wolken/Dunst
OUTLIER_K = 3.0                    # Ensemble-Streuung einer Aufnahme × Median → Ausreißer
SYS_FLOOR = 0.002                  # mag: Untergrenze je Messpunkt (Flat, Szintillation)
VAR_RATIO = 2.5                    # Streuung / erwartete Streuung bei dieser Helligkeit
VAR_CHI2 = 4.0
VAR_ETA = 1.2                      # von-Neumann-Verhältnis: ~2 = Rauschen, ≪ 2 = glatter Verlauf
EDGE = 0.06                        # Randstreifen (Anteil der kurzen Bildseite) ohne Messung
MATCH_AS = 3.0                     # Bogensekunden: VSX ↔ Gaia
VSX_TAP = ("https://vizier.cfa.harvard.edu/viz-bin/asu-tsv", "https://vizier.cds.unistra.fr/viz-bin/asu-tsv")
FLAG_TEXT = {"wolke": "Lichtverlust (Wolken/Dunst)", "ausreisser": "Ausreißer im Ensemble", "wenig": "zu wenige Vergleichssterne"}
# VSX-Typen grob auf Deutsch (Anfang des Typs entscheidet; Rest steht im Original daneben)
TYPE_LABEL = (("RRAB", "RR-Lyrae-Stern (ab)"), ("RRC", "RR-Lyrae-Stern (c)"), ("RRD", "RR-Lyrae-Stern (d)"), ("RR", "RR-Lyrae-Stern"),
              ("DCEP", "Delta-Cephei-Stern"), ("CEP", "Cepheide"), ("CW", "W-Virginis-Stern"), ("ACEP", "anomaler Cepheide"),
              ("EA", "Bedeckungsveränderlicher (Algol)"), ("EB", "Bedeckungsveränderlicher (Beta Lyrae)"),
              ("EW", "Bedeckungsveränderlicher (W UMa)"), ("E", "Bedeckungsveränderlicher"), ("DSCT", "Delta-Scuti-Stern"),
              ("HADS", "Delta-Scuti-Stern (große Amplitude)"), ("SX", "SX-Phoenicis-Stern"), ("M", "Mira-Stern"),
              ("SR", "halbregelmäßiger Veränderlicher"), ("L", "langsam unregelmäßiger Veränderlicher"), ("ROT", "Rotationsveränderlicher"),
              ("BY", "BY-Draconis-Stern"), ("RS", "RS-Canum-Venaticorum-Stern"), ("UG", "Zwergnova"), ("N", "Nova"),
              ("CV", "kataklysmischer Veränderlicher"), ("YSO", "junger Stern"), ("INS", "junger Stern"), ("UV", "Flare-Stern"),
              ("GDOR", "Gamma-Doradus-Stern"), ("ELL", "ellipsoidischer Veränderlicher"), ("VAR", "Veränderlicher (Typ offen)"))


class LightcurveError(Exception):
    """Klare Meldung für die Oberfläche."""


def type_label(t: str | None) -> str:
    t = (t or "").upper().strip(":| ")
    for k, v in TYPE_LABEL:
        if t.startswith(k):
            return v
    return "Veränderlicher" if t else "Veränderlicher (Typ offen)"


# ------------------------------------------------------------- Kataloge -- #
def _f(v: str):
    try:
        x = float(v)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def vsx_field(ra: float, dec: float, radius: float, cache_dir: Path, timeout: float = 40.0) -> list[dict]:
    """Bekannte Veränderliche im Feld aus AAVSO VSX (VizieR B/vsx). Gesendet werden nur Feldmitte und Radius."""
    import httpx

    key = hashlib.sha1(f"vsx2|{ra:.3f}|{dec:.3f}|{radius:.3f}".encode()).hexdigest()[:16]
    f = Path(cache_dir) / f"vsx_{key}.json"
    if f.exists():
        return json.loads(f.read_text(encoding="utf-8"))
    params = {"-source": "B/vsx/vsx", "-c": f"{ra:.5f} {dec:+.5f}", "-c.rd": f"{radius:.4f}", "-out.max": "5000",
              "-out": "OID Name RAJ2000 DEJ2000 Type max n_max f_min min n_min Epoch Period"}
    text, errors = None, []
    for url in VSX_TAP:
        try:
            r = httpx.get(url, params=params, timeout=timeout)
            r.raise_for_status()
            if "OID\tName" not in r.text:
                raise RuntimeError("unerwartete Antwort")
            text = r.text
            break
        except Exception as e:
            errors.append(f"{url.split('/')[2]}: {e}")
    if text is None:
        raise RuntimeError("VSX nicht erreichbar (" + "; ".join(errors)[:160] + ")")
    out, head = [], None
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        p = [v.strip() for v in line.split("\t")]
        if p[0] == "OID":
            head = p
            continue
        if head is None or len(p) < len(head) or not p[0].isdigit():
            continue                                   # Einheiten- und Trennzeilen
        d = dict(zip(head, p))
        ra_, de_ = _f(d["RAJ2000"]), _f(d["DEJ2000"])
        if ra_ is None or de_ is None:
            continue
        mx, mn = _f(d["max"]), _f(d["min"])
        is_amp = d.get("f_min") in ("(", "Y")              # VizieR: „Y“ (VSX: „(“) = Spalte min ist eine Amplitude
        amp = mn if is_amp else (mn - mx if mx is not None and mn is not None else None)
        out.append({"name": " ".join(d["Name"].split()), "ra": ra_, "dec": de_, "type": d["Type"], "max": mx,
                    "band": d.get("n_max") or "", "amp": None if amp is None else round(abs(amp), 3),
                    "period": _f(d["Period"]), "epoch": _f(d["Epoch"]), "oid": int(d["OID"]), "quelle": "VSX"})
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(out), encoding="utf-8")
    return out


def simbad_variables(ra: float, dec: float, radius: float, cache_dir: Path, timeout: float = 40.0) -> list[dict]:
    """Rückfall ohne VSX: SIMBAD-Objekte mit Veränderlichen-Typ im Feld (ohne Periode). Nur Feldmitte und Radius."""
    from .compare import OTYPE_LABEL, SIMBAD_TAP, VARIABLE

    key = hashlib.sha1(f"simvar1|{ra:.3f}|{dec:.3f}|{radius:.3f}".encode()).hexdigest()[:16]
    f = Path(cache_dir) / f"simbad_var_{key}.json"
    if f.exists():
        return json.loads(f.read_text(encoding="utf-8"))
    q = (f"SELECT TOP 5000 main_id, ra, dec, otype FROM basic WHERE 1=CONTAINS(POINT('ICRS', ra, dec), "
         f"CIRCLE('ICRS', {ra:.5f}, {dec:.5f}, {radius:.4f})) AND otype IN ("
         + ",".join(f"'{t}'" for t in ("V*", "RR*", "Ce*", "cC*", "EB*", "Mi*", "LP*", "dS*", "BY*", "RS*", "sr*", "Pu*", "El*",
                                       "WV*", "CV*", "No*", "Ro*", "gD*", "Ir*", "Er*", "V*?")) + ")")
    rows, errors = None, []
    for url in SIMBAD_TAP:
        try:
            rows = photometry._tap_csv(url, q, timeout)
            break
        except Exception as e:
            errors.append(f"{url.split('/')[2]}: {e}")
    if rows is None:
        raise RuntimeError("SIMBAD nicht erreichbar (" + "; ".join(errors)[:160] + ")")
    out = []
    for r in rows:
        try:
            ot = r[3].strip()
            if not VARIABLE.fullmatch(ot):
                continue
            out.append({"name": " ".join(r[0].split()), "ra": float(r[1]), "dec": float(r[2]), "type": ot,
                        "type_de": OTYPE_LABEL.get(ot), "max": None, "band": "", "amp": None, "period": None, "epoch": None,
                        "quelle": "SIMBAD"})
        except (ValueError, IndexError):
            continue
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(out), encoding="utf-8")
    return out


def known_variables(ra, dec, radius, cache_dir, log=lambda m: None) -> tuple[list[dict], str | None]:
    """VSX, sonst SIMBAD. Rückgabe (Liste, Hinweis bei Fehlschlag). Ohne Netz läuft alles mit leerer Liste weiter."""
    try:
        return vsx_field(ra, dec, radius, cache_dir), None
    except Exception as e:
        log("VSX nicht erreichbar – versuche SIMBAD")
        err = str(e)
    try:
        return simbad_variables(ra, dec, radius, cache_dir), "AAVSO VSX nicht erreichbar – Veränderliche nur aus SIMBAD (ohne Perioden)."
    except Exception:
        return [], f"Kein Veränderlichen-Katalog erreichbar ({err[:80]}) – nur die eigene Erkennung."


# ----------------------------------------------------------------- Zeit -- #
def barycentric(unix: np.ndarray, ra: float, dec: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Unixzeiten (UTC, Belichtungsmitte) → (JD_UTC, BJD_TDB, HJD_UTC). Geozentrisch (ohne Standort, Fehler < 0,03 s),
    eingebaute Ephemeride – keine Netzabfrage (ΔUT1 = 0 gesetzt, damit astropy keine IERS-Tabellen lädt)."""
    from astropy import units as u
    from astropy.coordinates import EarthLocation, SkyCoord
    from astropy.time import Time

    jd = np.asarray(unix, float) / 86400.0 + 2440587.5
    if not len(jd):
        return jd, jd, jd
    t = Time(jd, format="jd", scale="utc", location=EarthLocation.from_geocentric(0, 0, 0, unit=u.m))
    t.delta_ut1_utc = 0.0
    c = SkyCoord(ra * u.deg, dec * u.deg)
    bary = t.light_travel_time(c, kind="barycentric")
    helio = t.light_travel_time(c, kind="heliocentric")
    return jd, (t.tdb + bary).jd, (t.utc + helio).jd


# ------------------------------------------------------------- Helligkeit -- #
def gaia_v(g, c):
    """Johnson V aus Gaia G und BP−RP (Riello et al. 2021, Tab. C.2; gültig −0,5 < BP−RP < 2,75, Streuung 0,046 mag)."""
    c = np.clip(np.asarray(c, float), -0.5, 2.75)
    return np.asarray(g, float) - (-0.02704 + 0.01424 * c - 0.2156 * c ** 2 + 0.01426 * c ** 3)


def _robust_std(a, axis=None):
    a = np.asarray(a, float)
    med = np.nanmedian(a, axis=axis, keepdims=True)
    return 1.4826 * np.nanmedian(np.abs(a - med), axis=axis)


# ------------------------------------------------------------- Fotometrie -- #
def aperture(img: np.ndarray, x: np.ndarray, y: np.ndarray, r: float, w: np.ndarray | None = None):
    """Aperturfotometrie an Subpixel-Positionen. w: Gewichtskarte (1 = Pixel zählt, z. B. grüne Bayer-Pixel) oder None.
    Ring 2,0–3,0 r (mindestens 3 px breit), Median als Hintergrund. Rückgabe dict mit flux, var_bg (Hintergrundanteil der
    Varianz), peak (Rohwert), cx/cy (Schwerpunkt), n (wirksame Pixel); außerhalb des Bildes NaN."""
    H, Wd = img.shape
    r_in, r_out = 2.0 * r, max(3.0 * r, 2.0 * r + 3.0)
    k = int(math.ceil(r_out)) + 1
    g = np.arange(-k, k + 1)
    n = len(x)
    out = {kk: np.full(n, np.nan) for kk in ("flux", "var_bg", "peak", "cx", "cy", "n", "bg")}
    xi, yi = np.round(x).astype(int), np.round(y).astype(int)
    ok = np.isfinite(x) & np.isfinite(y) & (xi >= k) & (yi >= k) & (xi < Wd - k) & (yi < H - k)
    idx = np.nonzero(ok)[0]
    for s in range(0, len(idx), 400):                  # in Blöcken – Speicher bleibt klein
        ii = idx[s: s + 400]
        Y = yi[ii, None, None] + g[None, :, None]
        X = xi[ii, None, None] + g[None, None, :]
        cut = img[Y, X].astype(np.float64)
        wt = np.ones_like(cut) if w is None else w[Y, X].astype(np.float64)
        dx = g[None, None, :] - (x[ii] - xi[ii])[:, None, None]
        dy = g[None, :, None] - (y[ii] - yi[ii])[:, None, None]
        rr = np.hypot(dx, dy)
        ap = np.clip(r + 0.5 - rr, 0.0, 1.0) * wt          # weicher Rand: Teilpixel
        ann = (rr >= r_in) & (rr <= r_out) & (wt > 0)
        ring = np.where(ann, cut, np.nan).reshape(len(ii), -1)
        bg = np.nanmedian(ring, axis=1)
        sig = 1.4826 * np.nanmedian(np.abs(ring - bg[:, None]), axis=1)
        n_ann = ann.reshape(len(ii), -1).sum(1)
        a = cut - bg[:, None, None]
        n_ap = ap.reshape(len(ii), -1).sum(1)
        flux = (ap * a).reshape(len(ii), -1).sum(1)
        pos = np.clip(a, 0, None) * ap
        sp = pos.reshape(len(ii), -1).sum(1)
        with np.errstate(invalid="ignore", divide="ignore"):
            cx = x[ii] + (pos * dx).reshape(len(ii), -1).sum(1) / sp
            cy = y[ii] + (pos * dy).reshape(len(ii), -1).sum(1) / sp
        out["flux"][ii] = flux
        out["var_bg"][ii] = n_ap * sig ** 2 * (1 + math.pi / 2 * n_ap / np.maximum(n_ann, 1))
        out["peak"][ii] = np.where(ap > 0.5, cut, -np.inf).reshape(len(ii), -1).max(1)
        out["cx"][ii], out["cy"][ii] = cx, cy
        out["n"][ii] = n_ap
        out["bg"][ii] = bg
    return out


def _fit_affine(src: np.ndarray, dst: np.ndarray) -> np.ndarray | None:
    """Affine Abbildung src → dst (kleinste Quadrate, 2 Durchgänge mit Ausreißer-Verwurf)."""
    use = np.ones(len(src), bool)
    A = None
    for _ in range(2):
        if use.sum() < 8:
            return None
        X = np.c_[src[use], np.ones(use.sum())]
        A, *_ = np.linalg.lstsq(X, dst[use], rcond=None)
        res = np.hypot(*(np.c_[src, np.ones(len(src))] @ A - dst).T)
        use = res < max(0.6, 3 * float(np.median(res[use])))
    return A


def load_calibrated(row: dict, info: dict, lib, cache: dict, pred: np.ndarray, fwhm: float) -> np.ndarray:
    """Rohbild mit Dark/Flat (wie Pipeline._load), Hotpixel-Kosmetik aber nur abseits der Sterne: `calibration.cosmetic`
    stutzt sonst die Spitzen heller, scharfer Sterne (gemessen: einige Prozent Fluss, je Aufnahme verschieden)."""
    from scipy.spatial import cKDTree

    from . import calibration
    from .fits_io import read_data
    key = (tuple(info["shape"]), info["exptime"], info["gain"], info["filter"], row["night"])
    if key not in cache:
        cache[key] = lib.masters_for(info["shape"], info["exptime"], info["gain"], info["filter"], row["night"], info["bayerpat"])
    cal = calibration.apply(read_data(row["path"]), cache[key])
    fixed = calibration.cosmetic(cal, bool(info.get("bayerpat")))
    idx = np.flatnonzero(fixed != cal)
    if idx.size and len(pred):
        h, w = cal.shape[-2:]
        yy, xx = np.unravel_index(idx % (h * w), (h, w))
        d, _ = cKDTree(pred[np.isfinite(pred).all(1)]).query(np.c_[xx, yy])
        keep = idx[d > max(4.0, 2.0 * APER * fwhm)]      # nur Korrekturen außerhalb von Apertur und Ring
        cal = cal.copy()
        cal.flat[keep] = fixed.flat[keep]
    return cal


def green_weights(shape: tuple[int, int], bayerpat: str | None) -> np.ndarray | None:
    """Gewichtskarte der grünen Bayer-Pixel (Rohbild), None bei Mono-/Farbwürfel-Daten."""
    if not bayerpat:
        return None
    w = np.zeros(shape, np.float32)
    for i, c in enumerate(bayerpat.upper()[:4]):
        if c == "G":
            w[i // 2:: 2, i % 2:: 2] = 1.0
    return w


def measure_frame(img: np.ndarray, w: np.ndarray | None, pred: np.ndarray, fwhm: float, bright: np.ndarray,
                  r: float | None = None) -> dict:
    """Ein Bild vermessen: vorhergesagte Positionen `pred` (N,2) → Feinanpassung an hellen Sternen → Aperturfotometrie.
    bright: Maske der Sterne, die für die Feinanpassung taugen. Rückgabe dict (flux, var_bg, peak, n, shift)."""
    r = float(r or np.clip(APER * (fwhm or 3.0), 2.0, 18.0))
    x, y = pred[:, 0].copy(), pred[:, 1].copy()
    first = aperture(img, x[bright], y[bright], r, w)
    good = np.isfinite(first["cx"]) & (np.hypot(first["cx"] - x[bright], first["cy"] - y[bright]) < max(3.0, 1.5 * (fwhm or 3)))
    shift = 0.0
    if good.sum() >= 8:
        A = _fit_affine(pred[bright][good], np.c_[first["cx"], first["cy"]][good])
        if A is not None:
            new = np.c_[pred, np.ones(len(pred))] @ A
            shift = float(np.median(np.hypot(*(new - pred).T)))
            x, y = new[:, 0], new[:, 1]
    elif good.sum() >= 3:
        d = np.median(np.c_[first["cx"] - x[bright], first["cy"] - y[bright]][good], axis=0)
        x, y, shift = x + d[0], y + d[1], float(np.hypot(*d))
    out = aperture(img, x, y, r, w)
    out["r"], out["shift"], out["x"], out["y"] = r, shift, x, y
    return out


# -------------------------------------------------------- Differenzfotometrie -- #
def _running_median(mag: np.ndarray, val: np.ndarray, width: float = 0.75) -> callable:
    """Laufender Median von val über mag (für die erwartete Streuung bei gegebener Helligkeit)."""
    ok = np.isfinite(mag) & np.isfinite(val)
    m, v = mag[ok], val[ok]
    if len(m) < 5:
        med = float(np.nanmedian(val)) if np.isfinite(val).any() else np.nan
        return lambda q: np.full(np.shape(q), med)
    grid = np.arange(np.floor(m.min()), np.ceil(m.max()) + 0.26, 0.25)
    gv = np.array([np.median(v[np.abs(m - g) < width / 2]) if (np.abs(m - g) < width / 2).sum() >= 4 else np.nan for g in grid])
    good = np.isfinite(gv)
    if good.sum() < 2:
        med = float(np.median(v))
        return lambda q: np.full(np.shape(q), med)
    gv = np.maximum.accumulate(np.interp(grid, grid[good], gv[good]))   # schwächere Sterne streuen nie weniger
    return lambda q: np.interp(q, grid, gv)


def differential(*a, **k) -> dict:
    """Differenzfotometrie (siehe _differential); leere Spalten (Stern in keiner Aufnahme messbar) sind erlaubt."""
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        return _differential(*a, **k)


def _differential(F: np.ndarray, VB: np.ndarray, vcat: np.ndarray, col: np.ndarray, xy: np.ndarray, comp_ok: np.ndarray,
                  nights: np.ndarray, sat: np.ndarray, log=lambda m: None) -> dict:
    """F: Fluss (Aufnahmen × Sterne), VB: Hintergrundvarianz, vcat: Katalog-V (NaN = unbekannt), col: BP−RP, xy: Pixelorte
    (für die Nähe), comp_ok: taugt als Vergleichsstern, nights: Nacht-Index je Aufnahme, sat: übersättigt (Aufnahmen × Sterne).
    Rückgabe: mag/err (Aufnahmen × Sterne, NaN = keine Messung), Aufnahme-Markierungen, Kennzahlen."""
    nf, ns = F.shape
    with np.errstate(invalid="ignore", divide="ignore"):
        valid = np.isfinite(F) & (F > 0) & ~sat
        m_inst = np.where(valid, -2.5 * np.log10(np.where(valid, F, 1.0)), np.nan)
    c_fill = np.where(np.isfinite(col), col, np.nanmedian(col[comp_ok]) if comp_ok.any() else 0.8)
    comp = comp_ok & np.isfinite(vcat) & (valid.mean(0) > 0.8)
    if comp.sum() < MIN_COMP:
        raise LightcurveError(f"Nur {int(comp.sum())} brauchbare Vergleichssterne mit Gaia-Helligkeit – zu wenige für eine Eichung.")
    # Farbterm aus dem mittleren Bild: V − m_inst = Z + k·c
    med_inst = np.nanmedian(m_inst, axis=0)
    from .hrd import _robust_line
    fit = _robust_line(c_fill[comp], (vcat - med_inst)[comp], n_min=MIN_COMP)
    k = float(np.clip(fit[0], -0.5, 0.5)) if fit else 0.0
    dz = vcat - k * c_fill                             # Katalogwert ohne Farbanteil
    # 1. globales Ensemble, schwankende Vergleichssterne iterativ verwerfen
    def glob(m_inst):
        use = comp.copy()
        for it in range(4):
            res = dz[None, :] - m_inst                 # = Z_f je Stern
            Z = np.nanmedian(np.where(use[None, :], res, np.nan), axis=1)
            r = res - Z[:, None]
            rms = _robust_std(r, axis=0)
            ref = _running_median(med_inst[use], rms[use])
            bad = use & (rms > 2.5 * ref(med_inst)) & (rms > 0.01)
            if not bad.any() or (use & ~bad).sum() < MIN_COMP:
                break
            use &= ~bad
        return use, Z, r, rms

    use, Z, r, rms = glob(m_inst)
    s_int = np.where(np.isfinite(rms), rms, np.nan)
    # Fehlermodell aus den konstanten Sternen: Varianz = Hintergrundanteil + a·F (Photonen, a = 1/Verstärkung) + b·F²
    # (Flat, Szintillation, Unschärfe der Positionen) – a, b ≥ 0 über NNLS, relativ gewichtet
    from scipy.optimize import nnls
    Fm = np.nanmedian(np.where(valid, F, np.nan), axis=0)
    var_obs = (s_int / 1.0857 * Fm) ** 2
    vb = np.nanmedian(VB, axis=0)
    gsel = use & np.isfinite(var_obs) & np.isfinite(vb) & (Fm > 0)
    a_ph = b_sys = 0.0
    if gsel.sum() >= 5:
        wt = 1.0 / var_obs[gsel]
        try:
            (a_ph, b_sys), _ = nnls(np.c_[Fm[gsel], Fm[gsel] ** 2] * wt[:, None], (var_obs[gsel] - vb[gsel]) * wt)
        except Exception:
            a_ph = b_sys = 0.0
    gain = 1.0 / a_ph if a_ph > 0 else math.inf
    with np.errstate(invalid="ignore", divide="ignore"):
        Fc = np.clip(F, 0, None)
        var = VB + a_ph * Fc + b_sys * Fc ** 2
        e_phot = np.hypot(1.0857 * np.sqrt(var) / F, SYS_FLOOR)
    e_phot = np.where(valid, e_phot, np.nan)
    # Aufnahmen markieren: Lichtverlust gegenüber der Nacht, Ensemble-Streuung, zu wenige Vergleichssterne
    flags = np.array([""] * nf, dtype=object)
    loss = np.zeros(nf)
    for n in np.unique(nights):
        sel = nights == n
        loss[sel] = np.nanmedian(Z[sel]) - Z[sel]          # Z = V − m_inst sinkt, wenn Licht fehlt
    scat = _robust_std(np.where(use[None, :] & (med_inst < np.nanpercentile(med_inst[use], 60))[None, :], r, np.nan), axis=1)
    n_c = (np.isfinite(r) & use[None, :]).sum(1)
    flags[loss > CLOUD_DMAG] = "wolke"
    flags[(flags == "") & (scat > OUTLIER_K * np.nanmedian(scat))] = "ausreisser"
    flags[(flags == "") & (n_c < max(MIN_COMP, 0.5 * use.sum()))] = "wenig"
    fok = flags == ""
    # 2. je Stern eigenes Ensemble: nächste Vergleichssterne in Helligkeit, Farbe und Ort
    ci = np.nonzero(use)[0]
    span = float(np.ptp(xy[ci], axis=0).max()) or 1.0
    feats = np.c_[med_inst[ci] / 1.0, c_fill[ci] / 0.4, xy[ci] / (0.5 * span)]
    from scipy.spatial import cKDTree
    tree = cKDTree(feats)
    q = np.c_[med_inst / 1.0, c_fill / 0.4, xy / (0.5 * span)]
    q[~np.isfinite(q)] = 99.0
    _, nn = tree.query(q, k=min(len(ci), N_COMP + 1))
    nn = np.atleast_2d(nn)
    mag = np.full((nf, ns), np.nan)
    err = np.full((nf, ns), np.nan)
    for i in range(ns):
        cj = ci[[j for j in nn[i] if ci[j] != i][:N_COMP]]
        if len(cj) < MIN_COMP or not np.isfinite(med_inst[i]):
            continue
        rr = dz[cj][None, :] - m_inst[:, cj]          # Z je Aufnahme und Vergleichsstern
        ww = np.where(np.isfinite(rr), 1.0 / (e_phot[:, cj] ** 2 + np.maximum(s_int[cj], SYS_FLOOR)[None, :] ** 2), 0.0)
        sw = ww.sum(1)
        with np.errstate(invalid="ignore", divide="ignore"):
            zf = np.nansum(np.where(ww > 0, rr, 0.0) * ww, 1) / sw
            ze = np.sqrt(1.0 / sw)
            # Streuung des Ensembles um den Mittelwert (je Aufnahme) als Untergrenze der Nullpunkt-Unsicherheit
            sc = np.sqrt(np.nansum(ww * (np.where(ww > 0, rr, 0.0) - zf[:, None]) ** 2, 1) / sw / np.maximum((ww > 0).sum(1) - 1, 1))
        zerr = np.maximum(ze, sc)
        mag[:, i] = m_inst[:, i] + zf + k * c_fill[i]
        err[:, i] = np.hypot(e_phot[:, i], zerr)
    rmsm = _robust_std(np.where(fok[:, None], mag, np.nan), axis=0)
    return {"mag": mag, "err": err, "flags": flags, "loss": loss, "scatter": scat, "k": k, "comp": use, "rms": rmsm,
            "med_inst": med_inst, "gain": gain, "sys": b_sys, "n_comp_used": int(use.sum()), "n_comp_rejected": int((comp & ~use).sum())}


# ------------------------------------------------------------- Auswertung -- #
def curve_stats(t: np.ndarray, m: np.ndarray, e: np.ndarray) -> dict | None:
    ok = np.isfinite(t) & np.isfinite(m) & np.isfinite(e) & (e > 0)
    if ok.sum() < MIN_FRAMES:
        return None
    t, m, e = t[ok], m[ok], e[ok]
    o = np.argsort(t)
    t, m, e = t[o], m[o], e[o]
    w = 1 / e ** 2
    mu = float(np.sum(w * m) / w.sum())
    chi2 = float(np.sum(w * (m - mu) ** 2) / (len(m) - 1))
    var = float(np.var(m))
    eta = float(np.mean(np.diff(m) ** 2) / var) if var > 0 else 2.0
    sm = m if len(m) < 9 else np.convolve(m, np.ones(5) / 5, mode="valid")
    amp = float(np.percentile(sm, 97.5) - np.percentile(sm, 2.5))
    return {"n": int(len(m)), "mittel": round(mu, 4), "rms": round(float(_robust_std(m)), 4), "std": round(float(np.std(m)), 4),
            "fehler": round(float(np.median(e)), 4), "chi2": round(chi2, 2), "eta": round(eta, 3), "amp": round(amp, 3)}


def fourier_fit(t, m, e, period: float, epoch: float | None = None, nh: int = 2):
    """Fourierreihe mit fester Periode → (Modell-Funktion von Phase, Spitze-Spitze-Amplitude, Phasenabdeckung 0…1)."""
    ok = np.isfinite(m) & np.isfinite(e) & (e > 0)
    t, m, e = t[ok], m[ok], e[ok]
    ph = ((t - (epoch or 0.0)) / period) % 1.0
    cols = [np.ones_like(ph)]
    for h in range(1, nh + 1):
        cols += [np.cos(2 * np.pi * h * ph), np.sin(2 * np.pi * h * ph)]
    A = np.array(cols).T / e[:, None]
    coef, *_ = np.linalg.lstsq(A, m / e, rcond=None)

    def model(p):
        p = np.asarray(p, float)
        out = np.full_like(p, coef[0])
        for h in range(1, nh + 1):
            out += coef[2 * h - 1] * np.cos(2 * np.pi * h * p) + coef[2 * h] * np.sin(2 * np.pi * h * p)
        return out
    grid = np.linspace(0, 1, 200, endpoint=False)
    mv = model(grid)
    cover = len(np.unique(np.floor(ph * 20))) / 20
    return model, float(mv.max() - mv.min()), cover


def period_search(t, m, e) -> dict | None:
    """Lomb-Scargle über Perioden, die mindestens 1,5-mal in die Beobachtungsspanne passen. Nur deutliche Signale
    (Fehlalarm-Wahrscheinlichkeit < 0,1 %). Mehrere Nächte: Aliasperioden möglich – wird vermerkt."""
    from astropy.timeseries import LombScargle

    ok = np.isfinite(t) & np.isfinite(m) & np.isfinite(e) & (e > 0)
    t, m, e = t[ok], m[ok], e[ok]
    if len(t) < 2 * MIN_FRAMES:
        return None
    span = float(t.max() - t.min())
    dt = float(np.median(np.diff(np.sort(t))))
    if span <= 0 or dt <= 0:
        return None
    fmin, fmax = 1.5 / span, min(1 / (4 * dt), 200.0)     # bis 4 Punkte je Zyklus, höchstens 200 Zyklen/Tag
    if fmax <= fmin * 1.2:
        return None
    ls = LombScargle(t - t.min(), m, e)                # relative Zeit: bei JD ~2,46·10⁶ verliert die schnelle Methode Genauigkeit
    freq = np.linspace(fmin, fmax, int(min(200000, max(2000, 10 * span * (fmax - fmin)))))
    pw = ls.power(freq)
    i = int(np.argmax(pw))
    fap = float(ls.false_alarm_probability(pw[i], minimum_frequency=fmin, maximum_frequency=fmax))
    if not fap < 1e-3:
        return None
    if i <= 2:                                        # Gipfel am langen Ende = Trend über die ganze Serie, keine Periode
        return None
    p = 1 / float(freq[i])
    # Alias: zweithöchster Gipfel (abseits ±5 % der Frequenz) fast genauso hoch
    far = np.abs(freq - freq[i]) > 0.05 * freq[i]
    alias = bool(far.any() and pw[far].max() > 0.85 * pw[i])
    return {"periode": round(p, 6), "fap": float(f"{fap:.2g}"), "zyklen": round(span / p, 1), "alias": alias}


def common_pattern(idx: list[int], mag: np.ndarray, r_min: float = 0.7) -> set[int]:
    """Kandidaten, deren Verlauf mit dem eines anderen Kandidaten stark übereinstimmt (Korrelation > r_min): gemeinsamer
    Bildfehler statt eines Veränderlichen. Echte Veränderliche schwanken unabhängig voneinander."""
    if len(idx) == 0:
        return set()

    def norm(a):
        a = a - np.nanmedian(a)
        return a / (np.nanstd(a) or 1.0)
    curves = {i: norm(mag[:, i]) for i in idx}
    out = set()
    for a_ in range(len(idx)):
        for b_ in range(a_ + 1, len(idx)):
            x, y = curves[idx[a_]], curves[idx[b_]]
            ok = np.isfinite(x) & np.isfinite(y)
            if ok.sum() >= MIN_FRAMES and float(np.mean(x[ok] * y[ok])) / max(float(np.std(x[ok]) * np.std(y[ok])), 1e-9) > r_min:
                out |= {idx[a_], idx[b_]}
    return out


def bin_points(t, m, e, flags, n: int, gap: float = 0.02) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Je n aufeinanderfolgende gute Messungen mitteln (gewichtet), nicht über Lücken > gap Tage (Nachtwechsel, Pausen)."""
    ok = np.isfinite(m) & np.isfinite(e) & (e > 0) & (np.asarray(flags) == "")
    t, m, e = np.asarray(t)[ok], m[ok], e[ok]
    if n <= 1 or len(t) == 0:
        return t, m, e
    o = np.argsort(t)
    t, m, e = t[o], m[o], e[o]
    groups, cur = [], [0]
    for j in range(1, len(t)):
        if len(cur) >= n or t[j] - t[j - 1] > gap:
            groups.append(cur)
            cur = []
        cur.append(j)
    groups.append(cur)
    bt, bm, be = [], [], []
    for g in groups:
        w = 1 / e[g] ** 2
        bt.append(float(np.mean(t[g])))
        bm.append(float(np.sum(w * m[g]) / w.sum()))
        # Fehler: Mittelwertfehler, mindestens die Streuung der Gruppe / √n
        sd = float(np.std(m[g]) / math.sqrt(len(g))) if len(g) > 2 else 0.0
        be.append(max(float(math.sqrt(1 / w.sum())), sd))
    return np.array(bt), np.array(bm), np.array(be)


# --------------------------------------------------------- Gesamtablauf -- #
def _frames(pl, pid: int, meta: dict) -> list[dict]:
    rows = pl.db.q("SELECT id, path, info, metrics, night, deleted FROM frames WHERE project_id=? AND kind='light' AND stacked=1",
                   (pid,))
    in_stack = set((meta.get("frames") or {}).keys())
    out = []
    for r in rows:
        if r["deleted"] or (in_stack and r["id"] not in in_stack) or not r["path"] or not Path(r["path"]).exists():
            continue
        out.append(r)
    return out


def available_frames(pl, pid: int) -> int:
    """Wie viele gestackte Rohbilder des Objekts noch auf dem Datenträger liegen (für die Ansicht)."""
    rows = pl.db.q("SELECT path FROM frames WHERE project_id=? AND kind='light' AND stacked=1 AND deleted=0", (pid,))
    return sum(1 for r in rows if r["path"] and Path(r["path"]).exists())


def _state_meta(pl, pid: int) -> tuple[dict, str | None]:
    """Stack-Zustand mit den meisten Aufnahmen (Farbkamera: einer; Mono: bevorzugt L bzw. ohne Filter, dann G)."""
    wd = pl.s.work_dir / f"p{pid}"
    best, band = None, None
    for d in sorted(wd.glob("stack*")):
        if not d.is_dir() or d.name.endswith((".tmp", ".old")):
            continue
        try:
            meta = json.loads((d / "state.json").read_text())
        except (OSError, ValueError):
            continue
        b = None if d.name == "stack" else d.name[len("stack_"):]
        rank = (b in (None, "L", "G"), len(meta.get("frames") or {}))
        if best is None or rank > best[0]:
            best, band = (rank, meta), b
    if best is None:
        raise LightcurveError("Kein Master-Stack vorhanden – bitte das Objekt zuerst verarbeiten.")
    return best[1], band


def filter_code(band: str | None, color: bool) -> str:
    """AAVSO-Filterkürzel der Messung: Farbkamera → grüne Pixel (TG), Mono ohne Filter/L → CV, sonst Tricolor-Kürzel."""
    if color:
        return "TG"
    return {"G": "TG", "R": "TR", "B": "TB"}.get((band or "").upper(), "CV")


def compute(pl, rid: int, gaia: bool = True, catalogs: bool = True, log=lambda m: None) -> dict:
    from scipy.spatial import cKDTree

    from . import mosaic
    from .depth import _empty_sky, _ring_phot
    from .pipeline import frame_time

    r = pl.db.one("SELECT r.*, p.name, p.options FROM results r JOIN projects p ON p.id=r.project_id WHERE r.id=?", (rid,))
    if not r:
        raise LightcurveError("Version nicht gefunden.")
    pid = r["project_id"]
    wd = pl.s.work_dir
    if not gaia:
        raise LightcurveError("Gaia-Abfragen sind ausgeschaltet (Einstellungen → Bilder → Fotometrische Farbkalibrierung) – "
                              "ohne Vergleichssterne keine Lichtkurven.")
    meta, band = _state_meta(pl, pid)
    if "comet" in meta:
        raise LightcurveError("Kometen-Stacks sind auf den Kern ausgerichtet – Lichtkurven gehen nur bei Sternfeldern.")
    rows = _frames(pl, pid, meta)
    if band is not None:
        from .pipeline import band_of_row
        rows = [x for x in rows if band_of_row(x) == band]
    if len(rows) < MIN_FRAMES:
        raise LightcurveError(f"Nur noch {len(rows)} Rohbilder vorhanden – für eine Lichtkurve braucht es mindestens {MIN_FRAMES}. "
                              "Rohdaten, die nach dem Stacken gelöscht wurden, fehlen hier.")
    geo = services.view_geometry(wd, pid, json.loads(r["files"] or "{}"), need_stack=True)
    if geo is None:
        raise LightcurveError("Dieses Bild ist nicht per Plate-Solving gelöst – ohne Himmelskoordinaten lassen sich die Sterne "
                              "nicht Gaia zuordnen.")
    if not services.geometry_matches(geo):
        raise LightcurveError("Die gespeicherte Plate-Solving-Lösung gehört zu einem anderen Stack – bitte neu entwickeln.")
    wcs, H, W = geo["wcs"], geo["H"], geo["W"]
    L = np.nan_to_num(geo["data"].mean(0).astype(np.float32))
    from scipy import ndimage as ndi
    valid = ndi.binary_erosion(L != 0, iterations=12, border_value=0)
    sc = int(meta.get("scale") or 1)
    ox, oy = mosaic.origin(meta)
    ra0, dec0 = (float(v) for v in wcs.pixel_to_world_values((W - 1) / 2, (H - 1) / 2))
    pscale = float(np.sqrt(abs(np.linalg.det(wcs.pixel_scale_matrix))))          # Grad je Stack-Pixel
    radius = 0.5 * pscale * math.hypot(W, H)
    hinweise = []

    log("Gaia-Sterne im Feld abfragen")
    try:
        cat = photometry.gaia_field(ra0, dec0, radius, wd / "gaia")
    except Exception as e:
        raise LightcurveError(f"Gaia-Katalog nicht erreichbar – ohne Vergleichssterne keine Lichtkurven ({str(e)[:120]}).")
    known, why = ([], None)
    if catalogs:
        log("Bekannte Veränderliche im Feld suchen (VSX)")
        known, why = known_variables(ra0, dec0, radius, wd / "vsx", log)
    else:
        why = "Katalogabgleich ausgeschaltet (Einstellungen → Bilder) – nur die eigene Erkennung."
    if why:
        hinweise.append(why)

    # Stack-Fotometrie: welche Sterne sind hell genug für eine Messung je Aufnahme?
    px, py = wcs.world_to_pixel_values(cat["ra"], cat["dec"])
    inside = np.isfinite(px) & np.isfinite(py) & (px >= 0) & (py >= 0) & (px < W) & (py < H)
    inside[inside] = valid[py[inside].astype(int), px[inside].astype(int)]
    gi = np.nonzero(inside & np.isfinite(cat["g"]))[0]
    gi = gi[np.argsort(cat["g"][gi])]
    from .depth import _fwhm
    fw_stack = _fwhm(L)
    r_st = max(2.0, 1.0 * fw_stack)
    fl, sig_ring, _, peak_st, _ = _ring_phot(L, px[gi], py[gi], r_st)
    noise = _empty_sky(L, valid, px[gi], py[gi], r_st)
    if noise is None:
        raise LightcurveError("Kein freier Himmel für die Rauschmessung im Stack gefunden.")
    snr = fl / max(noise[0], 1e-12)
    n_tot = len(rows)
    need = SNR_FRAME * math.sqrt(n_tot)
    sel = np.nonzero(snr >= need)[0][:MAX_STARS]
    gi = gi[sel]
    # bekannte Veränderliche ↔ Gaia (nächster gemessener Stern innerhalb MATCH_AS), sonst eigene Position
    cosd = math.cos(math.radians(dec0))
    gtree = cKDTree(np.c_[cat["ra"][gi] * cosd, cat["dec"][gi]]) if len(gi) else None
    targets: dict[int, dict] = {}
    extra = []                                      # Veränderliche ohne gemessenen Gaia-Partner: an der Katalogposition messen
    kx, ky = wcs.world_to_pixel_values(np.array([k["ra"] for k in known] or [0.0]), np.array([k["dec"] for k in known] or [0.0]))
    for j, kv in enumerate(known):
        if not (0 <= kx[j] < W and 0 <= ky[j] < H) or not valid[int(ky[j]), int(kx[j])]:
            continue
        hit = None
        if gtree is not None:
            # im Umkreis der Gaia-Stern mit passender Helligkeit (Katalog: Maximum + halbe Amplitude), sonst der nächste
            ref_mag = kv["max"] + (kv.get("amp") or 0) / 2 if kv.get("max") is not None else None
            best = None
            for cand in gtree.query_ball_point([kv["ra"] * cosd, kv["dec"]], MATCH_AS / 3600):
                dpos = math.hypot((cat["ra"][gi[cand]] - kv["ra"]) * cosd, cat["dec"][gi[cand]] - kv["dec"]) * 3600
                score = abs(cat["g"][gi[cand]] - ref_mag) + 0.2 * dpos if ref_mag is not None else dpos
                if ref_mag is not None and abs(cat["g"][gi[cand]] - ref_mag) > 2.5:
                    continue                          # ganz andere Helligkeit: anderer Stern
                if best is None or score < best:
                    hit, best = cand, score
        if hit is not None and hit not in targets:
            targets[hit] = kv
        elif hit is None:
            extra.append((kv, float(kx[j]), float(ky[j])))
    # Katalogposition: messen, wenn der Stack dort genug Licht sieht
    if extra:
        efl, _, _, _, _ = _ring_phot(L, np.array([e[1] for e in extra]), np.array([e[2] for e in extra]), r_st)
        extra = [e for e, f in zip(extra, efl) if np.isfinite(f) and f / max(noise[0], 1e-12) >= 0.6 * need][:MAX_KNOWN]
    if len(gi) + len(extra) < 20:
        raise LightcurveError("Zu wenige Sterne sind hell genug für eine Messung in jeder Aufnahme.")
    sx = np.r_[px[gi], [e[1] for e in extra]]
    sy = np.r_[py[gi], [e[2] for e in extra]]
    g = np.r_[cat["g"][gi], np.full(len(extra), np.nan)]
    col = np.r_[cat["bp_rp"][gi], np.full(len(extra), np.nan)]
    ns = len(sx)
    vcat = gaia_v(g, col)
    vcat[~np.isfinite(col)] = np.nan
    # Nachbarn: Mischlicht in der Apertur bzw. heller Stern im Umfeld (Stack-Pixel; Apertur = APER × FWHM)
    ap_st = APER * fw_stack
    allx, ally = px[inside], py[inside]
    allg = cat["g"][inside]
    t_all = cKDTree(np.c_[allx, ally])
    blend = np.zeros(ns, bool)
    crowd = np.zeros(ns, bool)
    for i, nb in enumerate(t_all.query_ball_point(np.c_[sx, sy], 3.0 * ap_st)):
        gi_ = g[i] if np.isfinite(g[i]) else 15.0
        for j in nb:
            d = math.hypot(allx[j] - sx[i], ally[j] - sy[i])
            if d < 0.3:
                continue
            if d < 1.5 * ap_st and allg[j] < gi_ + 2.5:
                blend[i] = True
            if allg[j] < gi_ + 4.0:
                crowd[i] = True
    is_target = np.zeros(ns, bool)
    is_target[list(targets)] = True
    is_target[len(gi):] = True
    comp_ok = ~blend & ~crowd & ~is_target & np.isfinite(col) & (col > -0.3) & (col < 2.6)

    # Stack-Pixel → Referenzbild (native Pixel); je Aufnahme: Referenz → Aufnahme über die Umkehrung der Registrierung
    ref_xy = np.c_[(sx + 0.5) / sc - 0.5 + ox, (sy + 0.5) / sc - 0.5 + oy]
    ref_stars = np.array([[s[0], s[1]] for s in (meta.get("ref_stars") or [])], float).reshape(-1, 2)
    if len(ref_stars) < 5:
        raise LightcurveError("Im Stack-Zustand fehlt die Sternliste des Referenzbilds – bitte neu stacken.")
    snr_sel = snr[sel] if len(sel) else np.array([])
    bright = np.zeros(ns, bool)
    if len(gi):
        bright[: len(gi)] = (snr_sel > 8 * need) & ~blend[: len(gi)]
        if bright.sum() < 20:
            bright[: len(gi)] = ~blend[: len(gi)] & (np.arange(len(gi)) < 200)
    lib = pl._library()
    cache: dict = {}
    color = None

    def one(row):
        try:
            info = json.loads(row["info"])
            m = json.loads(row["metrics"] or "{}")
            if row["id"] == meta.get("ref_fid"):
                M = np.eye(3)
            else:
                tg = np.array([[s[0], s[1]] for s in m.get("stars", [])], float).reshape(-1, 2)
                res = registration.find_transform(ref_stars, tg)
                if res is None:
                    return row, None, "Registrierung fehlgeschlagen"
                M = res[0]
            pred = registration._apply(np.linalg.inv(M), ref_xy)
            raw = load_calibrated(row, info, lib, cache, pred, float(m.get("fwhm") or 3.0))
            bayer = info.get("bayerpat") if raw.ndim == 2 else None
            if raw.ndim == 3:                       # Farbwürfel: Grünkanal
                img = to_channels(raw, None)[min(1, raw.shape[0] - 1)]
                wmap = None
            else:
                img, wmap = raw, green_weights(raw.shape, bayer)
            out = measure_frame(img.astype(np.float32, copy=False), wmap, pred, float(m.get("fwhm") or 3.0), bright)
            out["t"] = frame_time(row)
            out["shape"] = img.shape[-2:]
            out["color"] = bool(bayer) or raw.ndim == 3
            out["exptime"] = float(info.get("exptime") or 0)
            return row, out, None
        except Exception as e:                      # nie den ganzen Lauf verlieren
            return row, None, f"Fehler: {str(e)[:80]}"

    log(f"{ns} Sterne in {n_tot} Aufnahmen messen")
    meas, fails = [], 0
    with ThreadPoolExecutor(max(1, min(4, int(getattr(pl.s, "threads", 2) or 2)))) as ex:
        for k_, (row, out, err) in enumerate(ex.map(one, rows)):
            if out is None or out["t"] is None:
                fails += 1
                continue
            meas.append((row, out))
            color = out["color"] if color is None else color
            if k_ % 10 == 0:
                log(f"Aufnahme {k_ + 1} von {n_tot} vermessen")
    if len(meas) < MIN_FRAMES:
        raise LightcurveError(f"Nur {len(meas)} Aufnahmen ließen sich vermessen – zu wenige für eine Lichtkurve.")
    if fails:
        hinweise.append(f"{fails} Aufnahmen ließen sich nicht vermessen (Registrierung oder Datei).")
    meas.sort(key=lambda x: x[1]["t"])
    F = np.array([o["flux"] for _, o in meas])
    VB = np.array([o["var_bg"] for _, o in meas])
    PK = np.array([o["peak"] for _, o in meas])
    BG = np.array([o["bg"] for _, o in meas])
    # Bildrand: ohne Flats ist die Ausleuchtung dort am schwächsten – wandert das Feld (Drift, Nachzentrieren), „schwankt“ ein
    # Stern am Rand nur scheinbar (M39, Stellina: bis Faktor 3). Messungen näher als EDGE × kurze Bildseite fallen weg.
    for f, (_, o) in enumerate(meas):
        h_, w_ = o["shape"]
        m_ = EDGE * min(h_, w_)
        near = ~((o["x"] >= m_) & (o["x"] < w_ - m_) & (o["y"] >= m_) & (o["y"] < h_ - m_))
        F[f, near] = np.nan
    unix = np.array([o["t"] for _, o in meas])
    night_names = [row["night"] or "" for row, _ in meas]
    uniq = sorted(set(night_names))
    nights = np.array([uniq.index(n) for n in night_names])

    # Übersättigung: abgeflachter Kern – Spitze/Fluss deutlich kleiner als bei hellen, sauberen Sternen derselben Aufnahme
    with np.errstate(invalid="ignore", divide="ignore"):
        ratio = (PK - BG) / F
        top = np.nanmax(PK, axis=1, keepdims=True)
        mid = (F > 0) & (PK < 0.5 * top)
        r0 = np.nanmedian(np.where(mid & comp_ok[None, :], ratio, np.nan), axis=1, keepdims=True)
        sat = (PK > 0.6 * top) & (ratio < 0.7 * r0)
    sat_star = sat.mean(0) > 0.2
    sat = sat | sat_star[None, :]
    comp_ok &= ~sat_star

    log("Differenzfotometrie mit Gaia-Vergleichssternen")
    d = differential(F, VB, vcat, col, np.c_[sx, sy], comp_ok, nights, sat, log)
    mag, err, flags = d["mag"], d["err"], d["flags"]
    fok = flags == ""
    jd, bjd, hjd = barycentric(unix, ra0, dec0)

    # Kennzahlen je Stern und erwartete Streuung bei dieser Helligkeit
    st = [curve_stats(bjd[fok], mag[fok, i], err[fok, i]) for i in range(ns)]
    vm = np.array([s["mittel"] if s else np.nan for s in st])
    rms = np.array([s["rms"] if s else np.nan for s in st])
    ref = _running_median(vm[d["comp"]], rms[d["comp"]])
    acc = {}
    for v in range(6, 20):
        sel_v = d["comp"] & (np.abs(vm - v) < 0.5)
        if sel_v.sum() >= 3:
            acc[v] = round(float(np.median(rms[sel_v])), 4)

    stars = []

    def entry(i, kv=None):
        s = st[i]
        if s is None:
            return None
        ra_, de_ = (float(v) for v in wcs.pixel_to_world_values(sx[i], sy[i]))
        e = {"i": i, "ra": round(ra_, 6), "dec": round(de_, 6), **s, "erwartet": round(float(ref(s["mittel"])), 4),
             "g": None if not np.isfinite(g[i]) else round(float(g[i]), 3),
             "bp_rp": None if not np.isfinite(col[i]) else round(float(col[i]), 3),
             "gaia": f"Gaia DR3 {cat['source_id'][gi[i]]}" if i < len(gi) else None,
             "mischlicht": bool(blend[i]), "gesaettigt": bool(sat_star[i])}
        e["genauigkeit"] = round(float(max(s["fehler"], min(s["rms"], e["erwartet"]))), 4)
        e.update(services_pos(geo, sx[i], sy[i]))
        if kv:
            e.update(name=kv["name"], typ=kv.get("type"), typ_de=kv.get("type_de") or type_label(kv.get("type")),
                     quelle=kv["quelle"], katalog_periode=kv.get("period"), epoche=kv.get("epoch"),
                     katalog_max=kv.get("max"), katalog_amp=kv.get("amp"), katalog_band=kv.get("band"), bekannt=True)
        else:
            e.update(bekannt=False, name=None)
        t_ok = bjd[fok]
        m_ok = mag[fok, i]
        e_ok = err[fok, i]
        p_cat = kv.get("period") if kv else None
        if p_cat and p_cat > 0:
            _, amp, cover = fourier_fit(t_ok, m_ok, e_ok, p_cat, kv.get("epoch"))
            span = float(np.nanmax(t_ok) - np.nanmin(t_ok))
            e["phase_abdeckung"] = round(cover, 2)
            e["zyklen"] = round(span / p_cat, 2)
            if cover >= 0.6:
                e["amp_fit"] = round(amp, 3)
        # Gedränge: Mischlicht in der Apertur oder (bekannter Stern) deutlich heller gemessen als der Katalog erlaubt –
        # dann misst die Apertur vor allem Nachbarn; Schwankungen sind dort nicht dem Stern zuzuordnen
        cat_hi = kv.get("max") if kv else None
        # ohne Katalogstern: gegen Gaia (mittleres V) – deutlich anders gemessen = fremdes Licht in der Apertur
        off_gaia = not kv and bool(np.isfinite(vcat[i])) and abs(s["mittel"] - vcat[i]) > 0.75
        e["gedraenge"] = bool(blend[i] or off_gaia or (cat_hi is not None and kv.get("band") in ("V", "G", "CV", "g", "TG", "Vis", "")
                                                       and s["mittel"] < cat_hi - 0.75))
        e["signifikant"] = bool(s["rms"] > VAR_RATIO * e["erwartet"] and s["chi2"] > VAR_CHI2 and not e["gedraenge"])
        # eigene Periode nur bei nachweislicher Schwankung – sonst findet Lomb-Scargle in zeitlich korreliertem Rauschen „Perioden“
        if e["signifikant"] and (ps := period_search(t_ok, m_ok, e_ok)):
            e["eigene_periode"] = ps
        return e

    log("Veränderliche auswerten")
    for i, kv in list(targets.items()) + [(len(gi) + j, ex_[0]) for j, ex_ in enumerate(extra)]:
        e = entry(i, kv)
        if e:
            stars.append(e)
    stars.sort(key=lambda e: (not e["signifikant"], e.get("g") or 99))
    stars = stars[:MAX_KNOWN]
    # eigene Erkennung: deutlich mehr Streuung als erwartet + glatter Verlauf, kein Mischlicht, nicht übersättigt, kein Katalogstern
    cand = []
    for i in range(len(gi)):
        s = st[i]
        if s is None or is_target[i] or blend[i] or sat_star[i]:
            continue
        exp_ = float(ref(s["mittel"]))
        if np.isfinite(vcat[i]) and abs(s["mittel"] - vcat[i]) > 0.75:
            continue                                    # Helligkeit passt nicht zu Gaia: Nachbarlicht/Artefakt, kein Kandidat
        if s["rms"] > VAR_RATIO * exp_ and s["chi2"] > VAR_CHI2 and s["eta"] < VAR_ETA and s["n"] >= 0.6 * fok.sum() \
                and s["amp"] > 4 * s["fehler"] and s["rms"] > 0.01:
            cand.append((s["rms"] / exp_, i))
    cand.sort(reverse=True)
    cand = cand[: 3 * MAX_CANDIDATES]
    shared = common_pattern([i for _, i in cand], mag[fok])
    if shared:
        hinweise.append(f"{len(shared)} auffällige Sterne schwankten im Gleichtakt mit anderen Sternen – das ist ein Bildfehler "
                        "(Ausleuchtung, Tau, Nachführung), kein Veränderlicher; sie werden nicht gezeigt.")
    cand = [(q, i) for q, i in cand if i not in shared]
    for _, i in cand[:MAX_CANDIDATES]:
        e = entry(i)
        if e:
            e["moeglich"] = True
            stars.append(e)
    # Prüfstern (AAVSO „check star“): heller, konstanter Vergleichsstern mittlerer Helligkeit
    comp_idx = np.nonzero(d["comp"] & np.isfinite(rms))[0]
    check = None
    if len(comp_idx):
        hell = comp_idx[vm[comp_idx] <= np.nanpercentile(vm[comp_idx], 35)]      # helleres Drittel: aussagekräftiger Prüfstern
        hell = hell if len(hell) else comp_idx
        check = int(hell[np.argmin(rms[hell])])
        ce = entry(check)
        if ce:
            ce["pruefstern"] = True
            ce["name"] = ce["gaia"]
            stars.append(ce)

    # Kurven speichern (nur Veränderliche, Kandidaten, Prüfstern) – eigener Datei, nicht in der Datenbank
    keep = [e["i"] for e in stars]
    curves = {str(i): [[None if not np.isfinite(mag[f, i]) else round(float(mag[f, i]), 4),
                        None if not np.isfinite(err[f, i]) else round(float(err[f, i]), 4)] for f in range(len(meas))] for i in keep}
    frames_out = [{"jd": round(float(jd[f]), 6), "bjd": round(float(bjd[f]), 6), "hjd": round(float(hjd[f]), 6),
                   "nacht": night_names[f], "flag": flags[f], "verlust": round(float(d["loss"][f]), 3),
                   "streuung": None if not np.isfinite(d["scatter"][f]) else round(float(d["scatter"][f]), 4),
                   "belichtung": meas[f][1]["exptime"], "id": meas[f][0]["id"]} for f in range(len(meas))]
    datei = f"p{int(pid)}/lichtkurven_r{int(rid)}.json"
    path = wd / datei
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"v": LC_VERSION, "frames": frames_out, "curves": curves}), encoding="utf-8")

    n_flag = {k: int((flags == k).sum()) for k in FLAG_TEXT if (flags == k).any()}
    if n_flag:
        hinweise.append("Ausgelassen: " + ", ".join(f"{v} × {FLAG_TEXT[k]}" for k, v in n_flag.items()) + ".")
    if d["n_comp_rejected"]:
        hinweise.append(f"{d['n_comp_rejected']} Vergleichssterne schwankten selbst und wurden verworfen.")
    if len(uniq) > 1:
        hinweise.append(f"{len(uniq)} Nächte zusammengeführt – jede Aufnahme ist einzeln an den Vergleichssternen geeicht.")
    n_crowd = sum(1 for e in stars if e.get("gedraenge"))
    if n_crowd:
        hinweise.append(f"{n_crowd} bekannte Veränderliche liegen im Gedränge (heller Nachbar in der Apertur) – bei dieser Auflösung "
                        "nicht vom Nachbarlicht zu trennen, ihre Kurven zeigen vor allem Rauschen.")
    span_h = float((unix.max() - unix.min()) / 3600)
    fcode = filter_code(band, bool(color))
    best = min(acc.items(), key=lambda kv: kv[1]) if acc else None
    return {"v": LC_VERSION, "ok": True, "datei": datei, "sterne": stars,
            "aufnahmen": {"n": len(meas), "gut": int(fok.sum()), "markiert": n_flag, "naechte": uniq,
                          "spanne_h": round(span_h, 2), "belichtung": round(float(np.median([o["exptime"] for _, o in meas])), 1)},
            "ensemble": {"n": d["n_comp_used"], "verworfen": d["n_comp_rejected"], "farbterm": round(d["k"], 3),
                         "verstaerkung": None if not math.isfinite(d["gain"]) else round(d["gain"], 3),
                         "systematik": round(float(math.sqrt(d["sys"])) * 1.0857, 4), "je": N_COMP},
            "genauigkeit": [{"v": v, "rms": a} for v, a in sorted(acc.items())],
            "beste_genauigkeit": best and {"v": best[0], "rms": best[1]},
            "filter": fcode, "skala": "V (aus Gaia G, BP−RP)", "n_gemessen": ns, "n_bekannt_feld": len(known),
            "katalog": known[0]["quelle"] if known else None, "pruefstern": check,
            "mitte": {"ra": round(ra0, 4), "dec": round(dec0, 4)}, "hinweise": hinweise,
            "bild": {"w": geo["view"][0], "h": geo["view"][1]} if geo["offset"] is not None else None,
            "erstellt": time.time()}


def de_num(v: float, nd: int = 2) -> str:
    return f"{v:.{nd}f}".replace(".", ",")


def services_pos(geo, x, y) -> dict:
    from .depth import _pos
    return _pos(geo, x, y)


# ------------------------------------------------------------- Ausgabe -- #
_CURVE_FILE = re.compile(r"p[0-9]{1,9}/lichtkurven_r[0-9]{1,9}\.json")


def load_curves(work_dir: Path, summary: dict) -> dict | None:
    """Kurven zur gespeicherten Zusammenfassung. Der Dateiname kommt aus der Datenbank (`summary["datei"]`, von compute
    geschrieben), nie aus der Anfrage, und muss dem festen Muster entsprechen."""
    name = str(summary.get("datei") or "")
    if not _CURVE_FILE.fullmatch(name):
        return None
    f = Path(work_dir) / name
    try:
        d = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return d if d.get("v") == LC_VERSION else None


def star_curve(summary: dict, curves: dict, i: int, bin_n: int = 1) -> dict:
    """Kurve eines Sterns für die Ansicht: Punkte (BJD, mag, Fehler, Nacht, Markierung), gebinnt, phasengefaltet."""
    fr = curves["frames"]
    c = curves["curves"].get(str(i))
    if c is None:
        raise KeyError(i)
    e = next((s for s in summary["sterne"] if s["i"] == i), {})
    t = np.array([f["bjd"] for f in fr])
    m = np.array([np.nan if v[0] is None else v[0] for v in c])
    er = np.array([np.nan if v[1] is None else v[1] for v in c])
    fl = np.array([f["flag"] for f in fr], dtype=object)
    nights = sorted({f["nacht"] for f in fr})
    pts = [[round(float(t[k]), 6), round(float(m[k]), 4), round(float(er[k]), 4), nights.index(fr[k]["nacht"]), fl[k]]
           for k in range(len(fr)) if np.isfinite(m[k])]
    bt, bm, be = bin_points(t, m, er, fl, bin_n)
    out = {"i": i, "punkte": pts, "gebinnt": [[round(float(a), 6), round(float(b), 4), round(float(cc), 4)] for a, b, cc in zip(bt, bm, be)]
           if bin_n > 1 else None, "bin": bin_n, "naechte": nights}
    period = e.get("katalog_periode") or ((e.get("eigene_periode") or {}).get("periode"))
    if period:
        ep = e.get("epoche") if e.get("katalog_periode") else None
        t0 = ep if ep else float(np.nanmin(t))
        pt, pm, pe = (bt, bm, be) if bin_n > 1 else (t[fl == ""], m[fl == ""], er[fl == ""])
        ok = np.isfinite(pm)
        ph = ((pt[ok] - t0) / period) % 1.0
        g_ok = (fl == "") & np.isfinite(m)
        model, amp, cover = fourier_fit(t[g_ok], m[g_ok], er[g_ok], period, t0) if g_ok.sum() >= MIN_FRAMES else (None, None, 0)
        out["phase"] = {"periode": period, "epoche": t0, "quelle": "Katalog" if e.get("katalog_periode") else "eigene Schätzung",
                        "punkte": [[round(float(a), 4), round(float(b), 4), round(float(cc), 4)] for a, b, cc in zip(ph, pm[ok], pe[ok])],
                        "modell": [[round(float(p), 3), round(float(v), 4)] for p, v in zip(np.linspace(0, 1, 101), model(np.linspace(0, 1, 101)))]
                        if model is not None and cover >= 0.6 else None, "abdeckung": round(cover, 2)}
    return out


WARN = "NICHT GEPRÜFT – automatisch erzeugt. Vor dem Einreichen jede Zeile kontrollieren (Vergleichssterne, Filter, Zeiten, Ausreißer)."


def export_csv(summary: dict, curves: dict, ids: list[int], bin_n: int = 1) -> str:
    fr = curves["frames"]
    lines = [f"# AstroFlow Lichtkurven – {WARN}",
             f"# Skala: {summary.get('skala')}; Filter {summary.get('filter')}; Ensemble aus Gaia DR3 ({summary['ensemble']['n']} Vergleichssterne)",
             "# Zeit: Belichtungsmitte; BJD_TDB und HJD_UTC für die Feldmitte (geozentrisch)",
             "stern,bjd_tdb,hjd_utc,jd_utc,mag,fehler,nacht,markierung"]
    for i in ids:
        e = next((s for s in summary["sterne"] if s["i"] == i), None)
        c = curves["curves"].get(str(i))
        if e is None or c is None:
            continue
        name = (e.get("name") or e.get("gaia") or f"Stern {i}").replace(",", " ")
        if bin_n > 1:
            t = np.array([f["bjd"] for f in fr])
            m = np.array([np.nan if v[0] is None else v[0] for v in c])
            er = np.array([np.nan if v[1] is None else v[1] for v in c])
            bt, bm, be = bin_points(t, m, er, [f["flag"] for f in fr], bin_n)
            dt_h = np.array([f["hjd"] - f["bjd"] for f in fr])
            dt_j = np.array([f["jd"] - f["bjd"] for f in fr])
            off_h, off_j = float(np.median(dt_h)), float(np.median(dt_j))
            for a, b, cc in zip(bt, bm, be):
                lines.append(f"{name},{a:.6f},{a + off_h:.6f},{a + off_j:.6f},{b:.4f},{cc:.4f},,gebinnt {bin_n}")
            continue
        for f, v in zip(fr, c):
            if v[0] is None:
                continue
            lines.append(f"{name},{f['bjd']:.6f},{f['hjd']:.6f},{f['jd']:.6f},{v[0]:.4f},{v[1]:.4f},{f['nacht']},{f['flag']}")
    return "\n".join(lines) + "\n"


def export_aavso(summary: dict, curves: dict, ids: list[int], version: str, bin_n: int = 1) -> str:
    """AAVSO Extended File Format (Ensemble-Fotometrie, Vergleichssterne aus Gaia DR3 statt AAVSO-Karte). Beobachterkürzel
    bleibt ein Platzhalter. Ausgelassene Aufnahmen (Wolken, Ausreißer) fehlen."""
    fr = curves["frames"]
    ck = summary.get("pruefstern")
    ck_e = next((s for s in summary["sterne"] if s["i"] == ck), None) if ck is not None else None
    ck_c = curves["curves"].get(str(ck)) if ck is not None else None
    lines = [f"# {WARN}", "# Vergleichssterne: Ensemble aus Gaia DR3, V nach Riello et al. (2021) – keine AAVSO-Sequenz (CHART=na).",
             "# OBSCODE und OBSTYPE vor dem Einreichen anpassen.",
             "#TYPE=EXTENDED", "#OBSCODE=XXXXX", f"#SOFTWARE=AstroFlow {version}", "#DELIM=,", "#DATE=HJD", "#OBSTYPE=CCD",
             "#NAME,DATE,MAG,MERR,FILT,TRANS,MTYPE,CNAME,CMAG,KNAME,KMAG,AMASS,GROUP,CHART,NOTES"]
    kname = (ck_e.get("gaia") or "na").replace(",", " ") if ck_e else "na"
    for i in ids:
        e = next((s for s in summary["sterne"] if s["i"] == i), None)
        c = curves["curves"].get(str(i))
        if e is None or c is None or not e.get("bekannt"):
            continue                                     # nur Katalogsterne – mögliche Veränderliche haben keinen AAVSO-Namen
        name = e["name"].replace(",", " ")
        note = f"Ensemble {summary['ensemble']['n']} Gaia-DR3-Sterne|Fehler aus Rauschen und Ensemble"
        if bin_n > 1:
            t = np.array([f["hjd"] for f in fr])
            m = np.array([np.nan if v[0] is None else v[0] for v in c])
            er = np.array([np.nan if v[1] is None else v[1] for v in c])
            bt, bm, be = bin_points(t, m, er, [f["flag"] for f in fr], bin_n)
            for a, b, cc in zip(bt, bm, be):
                lines.append(f"{name},{a:.5f},{b:.3f},{cc:.3f},{summary['filter']},NO,STD,ENSEMBLE,na,{kname},na,na,na,na,"
                             f"{note}|Mittel aus {bin_n} Aufnahmen")
            continue
        for k, (f, v) in enumerate(zip(fr, c)):
            if v[0] is None or f["flag"]:
                continue
            kmag = ck_c[k][0] if ck_c and ck_c[k][0] is not None else None
            lines.append(f"{name},{f['hjd']:.5f},{v[0]:.3f},{v[1]:.3f},{summary['filter']},NO,STD,ENSEMBLE,na,{kname},"
                         f"{'na' if kmag is None else f'{kmag:.3f}'},na,na,na,{note}")
    return "\n".join(lines) + "\n"
