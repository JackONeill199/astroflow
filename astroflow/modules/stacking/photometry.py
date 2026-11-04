"""Fotometrische Farbkalibrierung (wie PCC in PixInsight bzw. Siril): Weißabgleich über Katalogfarben der Sterne.

1. Master-Stack plate-solven (ASTAP) -> WCS: jedes Pixel hat eine Himmelskoordinate.
2. Gaia DR3 (Spiegel GAVO/ARI Heidelberg, sonst ESA-Gaia-Archiv bzw. VizieR) liefert für das Feld Positionen und Farbindizes BP−RP. Gesendet werden nur Feldmitte und
   Radius, das Ergebnis wird lokal gespeichert. Abschaltbar (Einstellungen bzw. ASTROFLOW_GAIA=0).
3. Katalogsterne den Sternen im Stack zuordnen, Fluss je Farbkanal messen (Apertur, lokaler Hintergrund).
4. Für jeden Kanal log(Kanal/Grün) gegen BP−RP robust linear anpassen und so verstärken, dass ein sonnenähnlicher
   Stern (G2V, BP−RP ≈ 0,82) weiß erscheint. Weißreferenz G2V wie bei PCC – keine Annahme über Kamera oder Filter.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import numpy as np

G2V_BP_RP = 0.82
MIN_STARS = 20
VIZIER = "https://vizier.cds.unistra.fr/viz-bin/asu-tsv"
ESA_TAP = "https://gea.esac.esa.int/tap-server/tap/sync"
GAVO_TAP = "https://dc.zah.uni-heidelberg.de/tap/sync"
ARI_TAP = "https://gaia.ari.uni-heidelberg.de/tap/sync"


def _tap(url, table, color, ra, dec, radius, gmag_max, timeout, need="bp_rp IS NOT NULL") -> list:
    import httpx

    q = (f"SELECT TOP 6000 ra, dec, {color} FROM {table} WHERE 1=CONTAINS(POINT('ICRS', ra, dec), "
         f"CIRCLE('ICRS', {ra:.5f}, {dec:.5f}, {radius:.4f})) AND phot_g_mean_mag < {gmag_max} AND {need} "
         f"ORDER BY phot_g_mean_mag")
    r = httpx.post(url, data={"REQUEST": "doQuery", "LANG": "ADQL", "FORMAT": "csv", "QUERY": q}, timeout=timeout)
    r.raise_for_status()
    rows = []
    for line in r.text.splitlines()[1:]:
        try:
            rows.append([float(v) for v in line.split(",")[:3]])
        except ValueError:
            continue
    return rows


def _gavo(ra, dec, radius, gmag_max, timeout) -> list:
    # GAVO-Spiegel (Heidelberg), Auszug gaia.dr3lite ohne Spalte bp_rp – gleiche Definition aus den Mittelwerten
    return _tap(GAVO_TAP, "gaia.dr3lite", "phot_bp_mean_mag - phot_rp_mean_mag", ra, dec, radius, gmag_max, min(timeout, 20),
                need="phot_bp_mean_mag IS NOT NULL AND phot_rp_mean_mag IS NOT NULL")


def _ari(ra, dec, radius, gmag_max, timeout) -> list:
    return _tap(ARI_TAP, "gaiadr3.gaia_source", "bp_rp", ra, dec, radius, gmag_max, min(timeout, 30))


def _esa(ra, dec, radius, gmag_max, timeout) -> list:
    return _tap(ESA_TAP, "gaiadr3.gaia_source", "bp_rp", ra, dec, radius, gmag_max, timeout)


def _vizier(ra, dec, radius, gmag_max, timeout) -> list:
    import httpx

    params = {"-source": "I/355/gaiadr3", "-c": f"{ra:.5f} {dec:+.5f}", "-c.rd": f"{radius:.4f}",
              "-out": "RA_ICRS,DE_ICRS,BP-RP", "-out.max": "6000", "Gmag": f"<{gmag_max}", "-sort": "Gmag"}
    r = httpx.get(VIZIER, params=params, timeout=timeout)
    r.raise_for_status()
    rows = []
    for line in r.text.splitlines():
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        p = line.split("	")
        try:
            rows.append([float(p[0]), float(p[1]), float(p[2])])
        except (ValueError, IndexError):
            continue                                       # Kopfzeilen, Sterne ohne Farbindex
    return rows


def gaia_stars(ra: float, dec: float, radius_deg: float, cache_dir: Path, gmag_max: float = 14.5,
               timeout: float = 120.0) -> np.ndarray:   # gemessen 01.11.2026: GAVO 1,3 s, ARI ~9 s, ESA 131 s
    """(N,3) Array RA, Dec, BP−RP der Gaia-DR3-Sterne im Feld (heller als gmag_max). Mit lokalem Zwischenspeicher."""
    key = hashlib.sha1(f"{ra:.3f}|{dec:.3f}|{radius_deg:.3f}|{gmag_max}".encode()).hexdigest()[:16]
    f = Path(cache_dir) / f"gaia_{key}.json"
    if f.exists():
        return np.array(json.loads(f.read_text()), dtype=np.float64).reshape(-1, 3)
    rows, errors = None, []
    for fetch in (_gavo, _ari, _esa, _vizier):             # schnelle Spiegel zuerst, ESA/VizieR als Ausweich
        try:
            rows = fetch(ra, dec, radius_deg, gmag_max, timeout)
            break
        except Exception as e:
            errors.append(f"{fetch.__name__[1:]}: {e}")
    if rows is None:
        raise RuntimeError("Gaia-Katalog nicht erreichbar (" + "; ".join(errors)[:160] + ")")
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(rows))
    return np.array(rows, dtype=np.float64).reshape(-1, 3)


FIELD_COLS = ("source_id", "ra", "dec", "g", "bp_rp", "parallax", "poe")


def _tap_csv(url: str, query: str, timeout: float, maxrec: int = 60000) -> list[list[str]]:
    """ADQL-Abfrage (synchron, CSV) → Zeilen als Textlisten (ohne Kopfzeile)."""
    import csv
    import io

    import httpx

    r = httpx.post(url, data={"REQUEST": "doQuery", "LANG": "ADQL", "FORMAT": "csv", "MAXREC": str(maxrec), "QUERY": query},
                   timeout=timeout)
    r.raise_for_status()
    if r.text.lstrip().startswith("<"):                    # VOTable mit Fehlermeldung statt CSV
        raise RuntimeError("Abfrage abgelehnt")
    return list(csv.reader(io.StringIO(r.text)))[1:]


def _num(v: str):
    try:
        x = float(v)
        return x if math.isfinite(x) else None
    except ValueError:
        return None


def gaia_field(ra: float, dec: float, radius_deg: float, cache_dir: Path, gmag_max: float = 20.7,
               top: int = 40000, timeout: float = 60.0) -> dict:
    """Gaia-DR3-Sterne im Feld bis `gmag_max` mit Helligkeit, Farbe und Parallaxe (für „Wie tief schaust du?“).
    Gesendet werden nur Feldmitte und Radius. Rückgabe: Spaltenarrays (`FIELD_COLS`) + `truncated` (TOP-Grenze erreicht)."""
    key = hashlib.sha1(f"field|{ra:.3f}|{dec:.3f}|{radius_deg:.3f}|{gmag_max}|{top}".encode()).hexdigest()[:16]
    f = Path(cache_dir) / f"gaia_feld_{key}.json"
    if f.exists():
        rows = json.loads(f.read_text())
    else:
        circle = f"1=CONTAINS(POINT('ICRS', ra, dec), CIRCLE('ICRS', {ra:.5f}, {dec:.5f}, {radius_deg:.4f}))"
        queries = (
            (GAVO_TAP, f"SELECT TOP {top} source_id, ra, dec, phot_g_mean_mag, phot_bp_mean_mag - phot_rp_mean_mag, parallax, "
                       f"parallax / parallax_error FROM gaia.dr3lite WHERE {circle} AND phot_g_mean_mag < {gmag_max} "
                       f"ORDER BY phot_g_mean_mag", min(timeout, 40)),
            (ARI_TAP, f"SELECT TOP {top} source_id, ra, dec, phot_g_mean_mag, bp_rp, parallax, parallax_over_error "
                      f"FROM gaiadr3.gaia_source WHERE {circle} AND phot_g_mean_mag < {gmag_max} ORDER BY phot_g_mean_mag", timeout),
            (ESA_TAP, f"SELECT TOP {top} source_id, ra, dec, phot_g_mean_mag, bp_rp, parallax, parallax_over_error "
                      f"FROM gaiadr3.gaia_source WHERE {circle} AND phot_g_mean_mag < {gmag_max} ORDER BY phot_g_mean_mag", timeout * 2))
        rows, errors = None, []
        for url, q, t in queries:
            try:
                rows = [[int(r[0])] + [_num(v) for v in r[1:7]] for r in _tap_csv(url, q, t) if len(r) >= 7 and r[0].isdigit()]
                break
            except Exception as e:
                errors.append(f"{url.split('/')[2]}: {e}")
        if rows is None:
            raise RuntimeError("Gaia-Katalog nicht erreichbar (" + "; ".join(errors)[:160] + ")")
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(rows))
    a = np.array([[np.nan if v is None else v for v in r[1:]] for r in rows], dtype=np.float64).reshape(-1, 6)
    out = {c: a[:, i] for i, c in enumerate(FIELD_COLS[1:])}
    out["source_id"] = [r[0] for r in rows]                 # 19 Stellen – als float gingen Ziffern verloren
    out["truncated"] = len(rows) >= top
    return out


def wcs_from(header: dict):
    from astropy.wcs import WCS

    return WCS({**header, "NAXIS": 2})


def _fluxes(img: np.ndarray, x: np.ndarray, y: np.ndarray, r: float):
    """Aperturfluss je Kanal mit lokalem Hintergrund (Ring); Zeilen ohne vollständige Apertur -> NaN."""
    C, H, W = img.shape
    ri = int(math.ceil(r * 2.4))
    yy, xx = np.mgrid[-ri : ri + 1, -ri : ri + 1]
    rr = np.hypot(yy, xx)
    ap, ann = rr <= r, (rr > r * 1.5) & (rr <= r * 2.4)
    out = np.full((len(x), C), np.nan)
    peak = np.full(len(x), np.nan)
    for i, (cx, cy) in enumerate(zip(np.round(x).astype(int), np.round(y).astype(int))):
        if cx < ri or cy < ri or cx >= W - ri or cy >= H - ri:
            continue
        cut = img[:, cy - ri : cy + ri + 1, cx - ri : cx + ri + 1]
        out[i] = [c[ap].sum() - np.median(c[ann]) * ap.sum() for c in cut]
        peak[i] = cut[:, ap].max()
    return out, peak


def pcc_gains(img: np.ndarray, cov: np.ndarray, header: dict, cat: np.ndarray, fwhm: float) -> tuple[list[float], dict]:
    """Verstärkungen [R, G, B] (G = 1), so dass G2V-Sterne weiß sind. Wirft ValueError bei zu wenig Sternen."""
    from scipy.spatial import cKDTree

    from .quality import detect_stars

    C, H, W = img.shape
    if C != 3 or len(cat) < MIN_STARS:
        raise ValueError("zu wenige Katalogsterne")
    w = wcs_from(header)
    px, py = w.world_to_pixel_values(cat[:, 0], cat[:, 1])          # 0-basiert: x = Spalte, y = Zeile
    inside = (px > 10) & (py > 10) & (px < W - 10) & (py < H - 10)
    px, py, bprp = px[inside], py[inside], cat[inside, 2]
    st = detect_stars(img.mean(0), max_stars=1500, thresh=6)
    if len(st) < MIN_STARS or len(px) < MIN_STARS:
        raise ValueError("zu wenige Sterne im Bild")
    d, j = cKDTree(np.c_[st.x, st.y]).query(np.c_[px, py], distance_upper_bound=max(3.0, fwhm))
    ok = np.isfinite(d)
    if ok.sum() < MIN_STARS:
        raise ValueError(f"nur {int(ok.sum())} Katalogsterne zugeordnet – Plate-Solving passt nicht zum Stack?")
    xs, ys, bprp = st.x[j[ok]], st.y[j[ok]], bprp[ok]
    fl, peak = _fluxes(img, xs, ys, max(2.5, 1.5 * fwhm))
    cv = cov[np.clip(np.round(ys).astype(int), 0, H - 1), np.clip(np.round(xs).astype(int), 0, W - 1)]
    top = np.nanpercentile(img.max(0), 99.99)
    good = np.isfinite(fl).all(1) & (fl > 0).all(1) & (peak < 0.8 * top) & (cv > 0.9) & (bprp > -0.3) & (bprp < 2.5)
    if good.sum() < MIN_STARS:
        raise ValueError(f"nur {int(good.sum())} brauchbare Sterne für die Farbmessung")
    fl, bprp = fl[good], bprp[good]
    gains, fits_ = [1.0, 1.0, 1.0], {}
    for c, name in ((0, "R"), (2, "B")):
        yv = np.log(fl[:, c] / fl[:, 1])
        use = np.ones(len(yv), bool)
        for _ in range(5):                                           # robuste Gerade (Ausreißer: Doppelsterne, Variable)
            a, b = np.polyfit(bprp[use], yv[use], 1)
            res = yv - (a * bprp + b)
            s = 1.4826 * np.median(np.abs(res[use])) + 1e-9
            new = np.abs(res) < 3 * s
            if new.sum() < MIN_STARS or (new == use).all():
                break
            use = new
        gains[c] = float(np.exp(-(a * G2V_BP_RP + b)))
        fits_[name] = {"steigung": round(float(a), 3), "streuung": round(float(s), 3)}
    gains = [float(np.clip(g, 0.25, 4.0)) for g in gains]
    return gains, {"sterne": int(len(fl)), "fit": fits_}
