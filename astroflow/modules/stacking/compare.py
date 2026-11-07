"""Vergleich mit einer Referenz: neue oder veränderte Lichtquellen als **Hinweis** (Wunsch 13).

Referenz ist ein eigener früherer Stack desselben Himmelsausschnitts (andere Version/Nacht) oder – online, abschaltbar –
die Himmelsdurchmusterung DSS2 rot (STScI, sonst hips2fits/CDS). Beide Bilder liegen per Plate-Solving auf demselben Raster.
Ablauf: Hintergrund ab, Schärfe angleichen (die schärfere Aufnahme wird gefaltet), Helligkeit über gemeinsame Sterne
angleichen, Differenzbild, Quellen > K·σ (Rauschen kachelweise) mit Formprüfung: sternförmig, nicht am Rand,
keine Einzelpixel, kein Restfehler eines hellen Sterns. Übrig gebliebene Stellen werden – falls erlaubt – mit SIMBAD
(Veränderliche, Supernovae …) und JPL-Kleinkörpern zur Aufnahmezeit abgeglichen. Gesendet werden nur Himmelskoordinaten
und Zeiten (Kleinkörper geozentrisch, nie der Standort); nie Bilder.
Ergebnis ist ausdrücklich keine Entdeckung: Unterschiede in Filter, Kamera und Bearbeitung erzeugen Fehlalarme.
"""
from __future__ import annotations

import hashlib
import io
import json
import math
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from . import services, stackfix

COMPARE_VERSION = 3                # 2: Hotpixel-Reste (stackfix) werden entfernt bzw. verworfen; 3: Grenzgröße beider Bilder
# „verschwunden“/„neu“ nur, wenn das Bild ohne die Quelle sie hätte zeigen müssen: Quelle mindestens REACH_MARGIN heller als
# dessen 5σ-Grenzgröße → dort ≥ 5·10^(0,4·1) ≈ 12,6σ erwartet; fehlt sie (< 3σ), ist das kein Rauschen mehr. Der Abstand deckt
# die Unsicherheit von Nullpunkt und Grenzgröße (~0,2–0,3 mag) und Farbunterschiede der Kameras ab. Gegen die Durchmusterung
# (Rotfilter, Fotoplatte) mehr: rote Sterne und Galaxien erscheinen dort über 1 mag heller als bei uns.
REACH_MARGIN = 1.0
REACH_MARGIN_SURVEY = 1.5
DSS_M5 = 20.5                      # POSS-II/UKST rot: Grenzgröße ~20,5–21 (nur ohne eigene Eichung der Platte genutzt)
MIN_CAL = 6                        # Gaia-Sterne für einen Nullpunkt (Durchmusterung: 8 für eine Gerade, 25 für eine Parabel)
K_SIGMA = 6.0                      # Schwelle im Differenzbild (geglättet)
MAX_CANDIDATES = 25
MIN_DMAG_SURVEY = math.inf         # Durchmusterung: anderes Band – nur „neu“/„verschwunden“, keine Helligkeitsänderungen
MIN_DMAG = 0.5                     # mag: Mindeständerung eines in beiden Bildern sichtbaren Sterns
MIN_CHANGE = 0.3                   # relative Helligkeitsänderung, unter der ein heller Stern als Restfehler gilt
DSS_URL = "https://archive.stsci.edu/cgi-bin/dss_search"
HIPS2FITS = "https://alasky.cds.unistra.fr/hips-image-services/hips2fits"
JPL_SBIDENT = "https://ssd-api.jpl.nasa.gov/sb_ident.api"
JPL_BUDGET = 150.0                 # s für alle JPL-Abfragen eines Vergleichs
SIMBAD_TAP = ("https://simbad.cfa.harvard.edu/simbad/sim-tap/sync", "https://simbad.cds.unistra.fr/simbad/sim-tap/sync")
VARIABLE = re.compile(r"(V\*|RR\*|Ce\*|cC\*|EB\*|Mi\*|LP\*|Ro\*|SN\*|No\*|CV\*|Ir\*|Er\*|Fl\*|dS\*|gD\*|BY\*|RS\*|WV\*|S\*|sr\*|Pu\*|bC\*|El\*|V\*\?)")
OTYPE_LABEL = {"SN*": "Supernova", "No*": "Nova", "CV*": "kataklysmischer Veränderlicher", "Mi*": "Mira-Veränderlicher",
               "RR*": "RR-Lyrae-Stern", "Ce*": "Cepheide", "EB*": "Bedeckungsveränderlicher", "LP*": "langperiodischer Veränderlicher",
               "V*": "Veränderlicher Stern", "QSO": "Quasar (oft veränderlich)", "Bla": "Blazar (veränderlich)", "BLL": "BL-Lac-Objekt (veränderlich)",
               "AGN": "aktiver Galaxienkern", "Ro*": "rotationsveränderlicher Stern", "Fl*": "Flarestern"}


class CompareError(Exception):
    """Verständliche Meldung für die Oberfläche."""


# -------------------------------------------------------------- Referenzen -- #
def _ang(ra1, dec1, ra2, dec2) -> float:
    r = math.radians
    c = math.sin(r(dec1)) * math.sin(r(dec2)) + math.cos(r(dec1)) * math.cos(r(dec2)) * math.cos(r(ra1 - ra2))
    return math.degrees(math.acos(max(-1.0, min(1.0, c))))


def references(db, work_dir: Path, rid: int) -> list[dict]:
    """Eigene frühere Stacks, die denselben Himmelsausschnitt zeigen (gelöst, linearer Stack vorhanden)."""
    cur = db.one("SELECT r.*, p.ra, p.dec, p.name FROM results r JOIN projects p ON p.id=r.project_id WHERE r.id=?", (rid,))
    if not cur or cur["ra"] is None:
        return []
    out = []
    for r in db.q("SELECT r.id, r.project_id, r.created, r.n_frames, r.exposure, r.files, r.stats, p.ra, p.dec, p.name, p.options "
                  "FROM results r JOIN projects p ON p.id=r.project_id WHERE r.id != ? AND p.ra IS NOT NULL ORDER BY r.created DESC", (rid,)):
        if json.loads(r["options"] or "{}").get("input_type") == "picture" or _ang(cur["ra"], cur["dec"], r["ra"], r["dec"]) > 0.4:
            continue
        files = json.loads(r["files"] or "{}")
        if not files.get("fits") or not Path(files["fits"]).exists() or not (work_dir / f"p{r['project_id']}" / "wcs.json").exists():
            continue
        try:                                             # Lösung passt nicht zur Stackgröße dieser Version → nicht anbieten
            key = json.loads((work_dir / f"p{r['project_id']}" / "wcs.json").read_text(encoding="utf-8")).get("key", "")
        except (OSError, ValueError):
            continue
        if "|" in key and not _fits_shape_ok(files["fits"], key):
            continue
        st = json.loads(r["stats"] or "{}")
        out.append({"id": r["id"], "created": r["created"], "n_frames": r["n_frames"], "exposure": r["exposure"], "name": r["name"],
                    "same": r["project_id"] == cur["project_id"], "nights": st.get("nights")})
    return out


def _fits_shape_ok(path: str, key: str) -> bool:
    """Bildgröße des linearen Stacks (FITS-Kopf, ohne die Daten zu lesen) gegen den Lösungsschlüssel „…|HxW“."""
    from astropy.io import fits
    try:
        h = fits.getheader(path)
        return key.split("|")[-1] == f"{h.get('NAXIS2')}x{h.get('NAXIS1')}"
    except Exception:
        return False


def pick_reference(refs: list[dict], cur: dict) -> dict | None:
    """Automatische Wahl: anderes Objekt/Gerät oder älterer Stand mit deutlich weniger Aufnahmen (= andere Nächte)."""
    cn = set((cur.get("stats") or {}).get("nights") or [])
    best, best_score = None, -1e9
    for r in refs:
        rn = set(r.get("nights") or [])
        if cn and rn and not (rn - cn) and r["same"] and r["n_frames"] >= 0.8 * cur["n_frames"]:
            continue                                     # gleiche Nächte, fast gleicher Stack: zeigt keine Veränderung
        if r["same"] and not (cn and rn) and r["n_frames"] >= 0.8 * cur["n_frames"]:
            continue
        # anderes Objekt/Gerät > andere Nächte > älterer Stand mit möglichst wenigen gemeinsamen Aufnahmen
        score = (2 if not r["same"] else 1.5 * (1 - r["n_frames"] / max(1, cur["n_frames"])))             + (1 if cn and rn and not (cn & rn) else 0) + 0.2 * math.log10(1 + (r["exposure"] or 0))
        if score > best_score:
            best, best_score = r, score
    return best


# ------------------------------------------------------------ Durchmusterung -- #
def survey_image(ra: float, dec: float, w_deg: float, h_deg: float, cache_dir: Path, timeout: float = 90.0):
    """DSS2 rot für das Feld (höchstens 60′ je Seite bei STScI) → (Bild, WCS, Quelle, Aufnahmedatum)."""
    from astropy.io import fits
    from astropy.wcs import WCS
    import warnings

    import httpx

    w_am, h_am = min(60.0, w_deg * 60 + 2), min(60.0, h_deg * 60 + 2)
    key = hashlib.sha1(f"dss|{ra:.4f}|{dec:.4f}|{w_am:.1f}|{h_am:.1f}".encode()).hexdigest()[:16]
    f = Path(cache_dir) / f"dss_{key}.fits"
    errors = []
    if not f.exists():
        tries = [(DSS_URL, {"v": "poss2ukstu_red", "r": f"{ra:.5f}", "d": f"{dec:.5f}", "e": "J2000", "h": f"{h_am:.1f}",
                            "w": f"{w_am:.1f}", "f": "fits", "c": "none"}, "DSS2 rot (STScI)"),
                 (HIPS2FITS, {"hips": "CDS/P/DSS2/red", "ra": f"{ra:.5f}", "dec": f"{dec:.5f}", "fov": f"{max(w_am, h_am) / 60:.4f}",
                              "width": "2000", "height": str(int(2000 * h_am / max(w_am, h_am))) if w_am >= h_am else "2000",
                              "projection": "TAN", "format": "fits"}, "DSS2 rot (CDS hips2fits)")]
        for url, params, label in tries:
            try:
                r = httpx.get(url, params=params, timeout=timeout, follow_redirects=True)
                r.raise_for_status()
                if not r.content.startswith(b"SIMPLE"):
                    raise RuntimeError("keine FITS-Datei erhalten")
                f.parent.mkdir(parents=True, exist_ok=True)
                f.write_bytes(r.content)
                f.with_suffix(".txt").write_text(label, encoding="utf-8")
                break
            except Exception as e:
                errors.append(f"{url.split('/')[2]}: {str(e)[:80]}")
        if not f.exists():
            raise CompareError("Himmelsdurchmusterung nicht erreichbar (" + "; ".join(errors)[:200] + ")")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        with fits.open(f) as hl:
            hdr = hl[0].header
            data = hl[0].data.astype(np.float32)
            wcs = WCS(hdr, naxis=2)
    label = f.with_suffix(".txt").read_text(encoding="utf-8") if f.with_suffix(".txt").exists() else "DSS2"
    date = str(hdr.get("DATE-OBS") or "")[:10] or None
    return data, wcs, label, date


# --------------------------------------------------------------- Bildarbeit -- #
def reproject(src: np.ndarray, src_wcs, dst_wcs, shape: tuple[int, int], step: int = 32) -> np.ndarray:
    """`src` auf das Pixelraster von `dst_wcs` bringen (bilinear; außerhalb NaN). Koordinaten auf grobem Gitter
    umgerechnet und interpoliert – genau genug für glatte Abbildungen, viel schneller als je Pixel."""
    from scipy import ndimage as ndi

    H, W = shape
    gy = np.arange(0, H + step, step, dtype=float).clip(0, H - 1)
    gx = np.arange(0, W + step, step, dtype=float).clip(0, W - 1)
    xx, yy = np.meshgrid(gx, gy)
    ra, dec = dst_wcs.pixel_to_world_values(xx.ravel(), yy.ravel())
    sx, sy = src_wcs.world_to_pixel_values(ra, dec)
    sx, sy = np.asarray(sx).reshape(xx.shape), np.asarray(sy).reshape(xx.shape)
    fy = (np.arange(H) / step).astype(np.float32)
    fx = (np.arange(W) / step).astype(np.float32)
    YY, XX = np.meshgrid(fy, fx, indexing="ij")
    mx = ndi.map_coordinates(sx, [YY, XX], order=1, mode="nearest")
    my = ndi.map_coordinates(sy, [YY, XX], order=1, mode="nearest")
    out = ndi.map_coordinates(np.nan_to_num(src), [my, mx], order=1, cval=np.nan)
    inside = (mx >= 0) & (my >= 0) & (mx <= src.shape[1] - 1) & (my <= src.shape[0] - 1)
    out[~inside] = np.nan
    return out.astype(np.float32)


def _background(a: np.ndarray, valid: np.ndarray, tile: int = 64) -> np.ndarray:
    """Großräumiger Hintergrund: Median je Kachel (nur gültige Pixel), glatt hochskaliert."""
    from scipy import ndimage as ndi

    H, W = a.shape
    ny, nx = max(1, H // tile), max(1, W // tile)
    grid = np.full((ny, nx), np.nan, np.float32)
    for i in range(ny):
        for j in range(nx):
            v = a[i * tile:(i + 1) * tile, j * tile:(j + 1) * tile][valid[i * tile:(i + 1) * tile, j * tile:(j + 1) * tile]]
            if v.size > tile:
                grid[i, j] = np.median(v)
    fill = np.nanmedian(grid) if np.isfinite(grid).any() else 0.0
    grid = np.where(np.isfinite(grid), grid, fill)
    grid = ndi.median_filter(grid, 3)
    return ndi.zoom(grid, (H / ny, W / nx), order=1)[:H, :W] if (ny, nx) != (H, W) else grid


def _noise_map(a: np.ndarray, valid: np.ndarray, tile: int = 96) -> np.ndarray:
    """Robuste Streuung je Kachel (MAD), hochskaliert."""
    from scipy import ndimage as ndi

    H, W = a.shape
    ny, nx = max(1, H // tile), max(1, W // tile)
    grid = np.full((ny, nx), np.nan, np.float32)
    for i in range(ny):
        for j in range(nx):
            v = a[i * tile:(i + 1) * tile, j * tile:(j + 1) * tile][valid[i * tile:(i + 1) * tile, j * tile:(j + 1) * tile]]
            if v.size > tile * 4:
                grid[i, j] = 1.4826 * np.median(np.abs(v - np.median(v)))
    fill = np.nanmedian(grid) if np.isfinite(grid).any() else 1.0
    grid = np.where(np.isfinite(grid) & (grid > 0), grid, fill)
    return ndi.zoom(grid, (H / ny, W / nx), order=1)[:H, :W]


def _fwhm(a: np.ndarray) -> float:
    from .quality import detect_stars

    st = detect_stars(np.nan_to_num(a), max_stars=300, thresh=8)
    return float(np.median(st.fwhm)) if len(st) >= 8 else 3.0


def _stretch(a: np.ndarray, lo: float, hi: float) -> np.ndarray:
    x = np.clip((np.nan_to_num(a, nan=lo) - lo) / max(hi - lo, 1e-9), 0, 1)
    return (np.arcsinh(x * 40) / np.arcsinh(40) * 255 + 0.5).astype(np.uint8)


# ------------------------------------------------------------- Auswertung -- #
def compute(db, work_dir: Path, rid: int, ref: str | int = "auto", survey: bool = True, catalogs: bool = True,
            log=lambda m: None, simbad: bool = True, small_bodies: bool = True, gaia: bool = True) -> dict:
    """Differenz gegen eine Referenz. Wirft CompareError mit verständlicher Meldung."""
    from scipy import ndimage as ndi

    cur = db.one("SELECT r.*, p.ra, p.dec, p.name FROM results r JOIN projects p ON p.id=r.project_id WHERE r.id=?", (rid,))
    if not cur:
        raise CompareError("Version nicht gefunden")
    cur = {**dict(cur), "files": json.loads(cur["files"] or "{}"), "stats": json.loads(cur["stats"] or "{}")}
    geo = services.view_geometry(work_dir, cur["project_id"], cur["files"], need_stack=True)
    if geo is None:
        raise CompareError("Dieses Bild ist nicht per Plate-Solving gelöst – ohne Himmelskoordinaten kein Vergleich möglich.")
    if not services.geometry_matches(geo):
        raise CompareError("Die gespeicherte Plate-Solving-Lösung gehört zu einem anderen Stack – bitte neu entwickeln.")
    H, W, wcs = geo["H"], geo["W"], geo["wcs"]
    # Hotpixel-Punkte älterer Stacks (vor 0.31.2, nicht neu entwickelt): im Kanalmittel und nach dem Angleichen der Schärfe
    # sehen Paare wie Sterne aus – vorher entfernen bzw. ihre Stellen merken (dieselbe Erkennung wie beim Entwickeln)
    data, fixA, spots = stackfix.spots(geo["data"])
    A = data.mean(0).astype(np.float32)
    validA = np.isfinite(A) & (A != 0)

    refs = references(db, work_dir, rid)
    if ref == "auto":
        pick = pick_reference(refs, cur)
        ref = pick["id"] if pick else "survey"
    info: dict = {}
    if ref == "survey":
        if not survey:
            raise CompareError("Keine passende eigene Aufnahme und der Online-Vergleich (Himmelsdurchmusterung) ist ausgeschaltet.")
        ra0, dec0 = (float(v) for v in wcs.pixel_to_world_values((W - 1) / 2, (H - 1) / 2))
        scale = float(np.sqrt(abs(np.linalg.det(wcs.pixel_scale_matrix))))
        log("Himmelsdurchmusterung laden (DSS2)")
        S, swcs, label, date = survey_image(ra0, dec0, W * scale, H * scale, work_dir / "vergleich")
        B = reproject(S, swcs, wcs, (H, W))
        info = {"art": "survey", "label": label, "datum": date}
        ref_time = []
        fixB = None
        r2 = None
        area = 1.0
    else:
        r2 = db.one("SELECT r.*, p.name FROM results r JOIN projects p ON p.id=r.project_id WHERE r.id=?", (int(ref),))
        if not r2:
            raise CompareError("Referenz-Version nicht gefunden")
        g2 = services.view_geometry(work_dir, r2["project_id"], json.loads(r2["files"] or "{}"), need_stack=True)
        if g2 is None:
            raise CompareError("Die Referenz ist nicht per Plate-Solving gelöst bzw. ihr linearer Stack fehlt.")
        if not services.geometry_matches(g2):
            raise CompareError("Die Plate-Solving-Lösung passt nicht zur Auflösung dieser Referenz-Version – bitte eine andere wählen.")
        log("Referenz auf das Raster bringen")
        data2, fixB, spots2 = stackfix.spots(g2["data"])
        B = reproject(data2.mean(0), g2["wcs"], wcs, (H, W))
        # `reproject` tastet Pixelwerte ab, erhält den Fluss nicht: auf einem feineren Raster wächst der Aperturfluss
        # um das Flächenverhältnis der Pixel (für den gespeicherten Nullpunkt der Referenz, `_limits`)
        area = _pix_scale(g2["wcs"]) ** 2 / max(_pix_scale(wcs) ** 2, 1e-30)
        if len(spots2):                              # Stellen der Referenz auf das Raster dieses Bilds
            sx, sy = wcs.world_to_pixel_values(*g2["wcs"].pixel_to_world_values(spots2[:, 0], spots2[:, 1]))
            spots = np.vstack([spots, np.c_[sx, sy]])
        st2 = json.loads(r2["stats"] or "{}")
        info = {"art": "eigen", "rid": r2["id"], "name": r2["name"], "created": r2["created"], "n_frames": r2["n_frames"],
                "nights": st2.get("nights"), "label": f"eigene Aufnahme vom {datetime.fromtimestamp(r2['created']).strftime('%d.%m.%Y')}"}
        # Aufnahmezeiten der Referenz nur, wenn ihre Nächte bekannt sind (ältere Versionen speichern sie nicht)
        ref_time = (_night_times(db, r2["project_id"], st2.get("nights"))
                    if st2.get("nights") or r2["project_id"] != cur["project_id"] else [])
    validB = np.isfinite(B) & (B != 0)
    valid = ndi.binary_erosion(validA & validB, iterations=16, border_value=0)
    if valid.mean() < 0.15:
        raise CompareError("Referenz und Bild überlappen kaum.")
    A = np.where(validA, A, 0) - _background(np.where(validA, A, 0), valid)
    B = np.where(validB, B, 0) - _background(np.where(validB, B, 0), valid)

    log("Schärfe und Helligkeit angleichen")
    fa, fb = _fwhm(np.where(valid, A, 0)), _fwhm(np.where(valid, B, 0))
    if fa < fb:                                      # die schärfere Aufnahme weichzeichnen
        A = ndi.gaussian_filter(A, math.sqrt(fb ** 2 - fa ** 2) / 2.3548)
    elif fb < fa:
        B = ndi.gaussian_filter(B, math.sqrt(fa ** 2 - fb ** 2) / 2.3548)
    fwhm = max(fa, fb)
    rel = _flux_relation(A, B, valid, fwhm, curved=info["art"] == "survey")
    if rel is None:
        raise CompareError("Zu wenige gemeinsame Sterne, um die Helligkeit anzugleichen.")
    k, expect = rel
    B = B * k
    D = np.where(valid, A - B, 0)
    Ds = ndi.gaussian_filter(D, fwhm / 2.3548)
    Z = Ds / _noise_map(Ds, valid)
    Z[~valid] = 0

    log("Grenzgröße beider Bilder bestimmen")
    lim, lim_notes = _limits(db, work_dir, cur, r2, info, A, B, valid, wcs, fwhm, k, gaia, area)

    log("Unterschiede prüfen")
    # Durchmusterung: anderes Band (Rotfilter, Fotoplatte) – rote Sterne (z. B. Riesen in Kugelhaufen) erscheinen dort
    # über 1 mag heller; Helligkeitsänderungen werden nur gegen eigene Aufnahmen gemeldet
    cands, rejected = _candidates(A, B, D, Z, valid, fwhm, lambda fb: expect(fb / k), spots=spots,
                                  min_dmag=MIN_DMAG_SURVEY if info["art"] == "survey" else MIN_DMAG,
                                  # „fehlt bei mir“ gegen die Platte nur bei sehr hellen Quellen: Galaxien und rote Sterne sind
                                  # im Rotfilter heller, als die an Sternen angepasste Helligkeitsbeziehung erwartet (M3: 25 Fehlalarme)
                                  vanish=30.0 if info["art"] == "survey" else 10.0, reach=lim["reach"],
                                  survey_ref=info["art"] == "survey")
    for c in cands:
        for key, fn in (("f_neu", lim["mag_a"]), ("f_ref", lim["mag_b"])):
            if fn and c[key] > 0:
                c["mag_" + key[2:]] = round(fn(c[key]), 1)
        (c["ra"], c["dec"]), = [tuple(float(v) for v in wcs.pixel_to_world_values(c["_x"], c["_y"]))]
        c["ra"], c["dec"] = round(c["ra"], 5), round(c["dec"], 5)

    notes = []
    if catalogs and cands:
        log("Kataloge abgleichen (SIMBAD, JPL)")
        notes += _catalog_check(cands, wcs, (H, W), _night_times(db, cur["project_id"], cur["stats"].get("nights")), ref_time,
                                work_dir / "vergleich", simbad, small_bodies)
    elif not catalogs:
        notes.append("Katalogabgleich ausgeschaltet – Veränderliche und Kleinplaneten wurden nicht geprüft.")
    if info["art"] == "survey":
        notes.append("Gegen die Durchmusterung (Fotoplatten mit Rotfilter, Jahrzehnte alt) werden nur neue bzw. fehlende Quellen "
                     "gemeldet – Helligkeiten sind wegen des anderen Farbbands nicht vergleichbar; Eigenbewegungen und Galaxien "
                     "können trotzdem als Unterschied erscheinen.")

    # Blink-Bilder (gleiche Streckung, Norden oben) und Ausschnitte je Kandidat
    out_dir = work_dir / f"p{cur['project_id']}" / "vergleich" / f"r{rid}"
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("*"):
        old.unlink()
    lo, hi = np.percentile(A[valid], [20, 99.8])
    from PIL import Image
    Image.fromarray(_stretch(np.where(valid, A, lo), lo, hi)[::-1]).save(out_dir / "aktuell.jpg", quality=88)
    Image.fromarray(_stretch(np.where(valid, B, lo), lo, hi)[::-1]).save(out_dir / "referenz.jpg", quality=88)
    for i, c in enumerate(cands):
        c["bild"] = f"k{i}.png"
        _cutout(A, B, D, c["_x"], c["_y"], lo, hi, out_dir / c["bild"])
        c["x"], c["y"] = round(c.pop("_x"), 1), round(H - 1 - c.pop("_y"), 1)
    notes += lim_notes
    if rejected.get("unter_grenze"):
        n = rejected["unter_grenze"]
        notes.append(f"{n} {'Stelle' if n == 1 else 'Stellen'} verworfen, weil das Bild ohne die Quelle sie mit seiner Belichtung "
                     "nicht sicher zeigen konnte (Quelle schwächer als dessen Grenzgröße − "
                     f"{de_num(lim['abstand'])} mag) – das ist kein Verschwinden, sondern fehlende Tiefe.")
    hot = {"aktuell": fixA.get("n", 0), "referenz": (fixB or {}).get("n", 0)}
    if hot["aktuell"] or hot["referenz"]:
        notes.append(f"Hotpixel-Punkte des Sensors vor dem Vergleich entfernt ({hot['aktuell']} im Bild"
                     + (f", {hot['referenz']} in der Referenz" if fixB else "") + ") – sie sind keine Veränderung am Himmel.")
    return {"v": COMPARE_VERSION, "ok": True, "ref": info, "kandidaten": cands, "verworfen": rejected, "fwhm_px": round(fwhm, 2),
            "skala": round(float(k), 4), "bild": {"w": W, "h": H}, "hinweise": notes, "erstellt": time.time(),
            "referenzen": len(refs), "hotpixel": hot,
            "grenze": {"aktuell": lim["aktuell"], "referenz": lim["referenz"], "abstand": lim["abstand"]}}


def de_num(v: float, nd: int = 1) -> str:
    return f"{v:.{nd}f}".replace(".", ",")


# ------------------------------------------------------------- Grenzgröße -- #
def _calibrate(img, valid, gx, gy, g, r, sig_ap, curved=False):
    """Helligkeitsskala eines Vergleichsbilds aus Gaia-Sternen (gleiche Apertur wie die Kandidaten): Funktion
    Fluss → G-Helligkeit. Eigene Stacks sind linear (Nullpunkt), die Fotoplatte nicht (Gerade bzw. Parabel in log Fluss)."""
    from scipy.spatial import cKDTree

    from .depth import _ring_phot

    if len(gx) < MIN_CAL or not sig_ap > 0:
        return None
    flux, _, off, peak, _ = _ring_phot(img, gx, gy, r)
    top = float(np.nanpercentile(img[valid], 99.99))
    tree = cKDTree(np.c_[gx, gy])
    blended = np.array([any(j != i and g[j] < g[i] + 2.0 for j in nb)
                        for i, nb in enumerate(tree.query_ball_point(np.c_[gx, gy], r * 3.2))], bool)
    with np.errstate(invalid="ignore"):
        ok = (np.isfinite(flux) & (flux > 10 * sig_ap) & (np.nan_to_num(off, nan=99) < max(1.5, 0.6 * r))
              & ~blended & (np.nan_to_num(peak, nan=np.inf) < 0.7 * top))
    if ok.sum() < MIN_CAL:
        return None
    x, y = np.log10(flux[ok]), g[ok]
    deg = 0 if not curved else 2 if ok.sum() >= 25 else 1 if ok.sum() >= 8 else None
    if deg is None:
        return None
    use = np.ones(len(x), bool)
    for _ in range(5):                               # robust: Veränderliche, Doppelsterne, Galaxienkerne fallen heraus
        if deg == 0:
            zp = float(np.median(y[use] + 2.5 * x[use]))
            res = y - (zp - 2.5 * x)
        else:
            c = np.polyfit(x[use], y[use], deg)
            res = y - np.polyval(c, x)
        s = 1.4826 * np.median(np.abs(res[use])) + 0.02
        new = np.abs(res) < 3 * s
        if new.sum() < MIN_CAL or (new == use).all():
            break
        use = new
    lo, hi = float(x[use].min()), float(x[use].max())
    if deg == 0:
        return {"fn": lambda f: zp - 2.5 * math.log10(max(float(f), 1e-9)), "n": int(use.sum()), "streuung": round(float(s), 2)}
    poly = np.poly1d(c)
    dpoly = poly.deriv()
    if not all(dpoly(v) < 0 for v in np.linspace(lo, hi, 9)):      # Platte: Skala muss mit dem Fluss heller werden
        return None

    def fn(f):                                        # außerhalb des Messbereichs mit der Steigung am Rand fortsetzen
        lf = math.log10(max(float(f), 1e-9))
        edge = lo if lf < lo else hi if lf > hi else None
        return float(poly(lf)) if edge is None else float(poly(edge) + dpoly(edge) * (lf - edge))
    return {"fn": fn, "n": int(use.sum()), "streuung": round(float(s), 2)}


def _device_m5(db, pid: int, exposure: float | None, skip_rid: int | None = None) -> float | None:
    """Grob geschätzte Grenzgröße aus anderen Bildern derselben Kamera (`INSTRUME`): deren m5 auf 1 h umgerechnet
    (Rauschen ∝ 1/√t → 1,25·log10 t, wie `m5_1h` im Dashboard), Median, dann auf diese Belichtung."""
    if not exposure or exposure <= 0:
        return None
    ins: dict[int, tuple[int, str]] = {}
    for x in db.q("SELECT project_id, json_extract(info,'$.instrument') ins, COUNT(*) n FROM frames WHERE kind='light' "
                  "GROUP BY project_id, ins"):
        if x["ins"] and x["n"] > ins.get(x["project_id"], (0, ""))[0]:
            ins[x["project_id"]] = (x["n"], x["ins"])
    mine = ins.get(pid, (0, None))[1]
    if not mine:
        return None
    vals = []
    for r in db.q("SELECT id, project_id, exposure, json_extract(stats,'$.depth.grenze.m5') m5 FROM results WHERE exposure > 60"):
        if r["id"] != skip_rid and r["m5"] is not None and ins.get(r["project_id"], (0, None))[1] == mine:
            vals.append(float(r["m5"]) - 1.25 * math.log10(r["exposure"] / 3600))
    if len(vals) < 2:
        return None
    return float(np.median(vals)) + 1.25 * math.log10(exposure / 3600)


def _pix_scale(w) -> float:
    return float(np.sqrt(abs(np.linalg.det(w.pixel_scale_matrix))))


def _limits(db, work_dir: Path, cur: dict, r2, info: dict, A, B, valid, wcs, fwhm: float, k: float, gaia: bool,
            area: float = 1.0):
    """Grenzgröße (5σ in der Vergleichsapertur) und Helligkeitsskala beider Vergleichsbilder – dynamisch je Teleskop und
    Belichtung. Reihenfolge: Gaia-Sterne im Bild (gleiche Apertur, Schärfe und Skalierung wie die Kandidaten) → gespeicherte
    Tiefe der Version (`stats.depth.grenze`: m5, sonst Nullpunkt + Aperturrauschen, sonst 50-%-Vollständigkeit) → andere
    Bilder derselben Kamera über das √t-Gesetz → bei der Durchmusterung ihre Nenngrenze. Liefert `reach(art, fa, fb)`:
    True/False = Quelle hätte im anderen Bild sicher erscheinen müssen bzw. nicht, None = nicht bestimmbar."""
    H, W = A.shape
    r = max(2.0, fwhm)
    survey = info["art"] == "survey"
    sig_a, sig_b = _ap_noise(A, valid, r), _ap_noise(B, valid, r)
    notes: list[str] = []
    gx = gy = g = np.zeros(0)
    if gaia:
        from . import photometry
        ra0, dec0 = (float(v) for v in wcs.pixel_to_world_values((W - 1) / 2, (H - 1) / 2))
        scale = float(np.sqrt(abs(np.linalg.det(wcs.pixel_scale_matrix))))
        try:                                          # gleiche Abfrage wie die Tiefe → gemeinsamer Zwischenspeicher
            cat = photometry.gaia_field(ra0, dec0, 0.5 * scale * math.hypot(W, H), work_dir / "gaia")
            px, py = wcs.world_to_pixel_values(cat["ra"], cat["dec"])
            px, py = np.asarray(px, float), np.asarray(py, float)
            inside = np.isfinite(px) & np.isfinite(py) & (px >= 0) & (py >= 0) & (px < W) & (py < H)
            inside[inside] = valid[py[inside].astype(int), px[inside].astype(int)]
            gx, gy, g = px[inside], py[inside], np.asarray(cat["g"], float)[inside]
        except Exception as e:
            notes.append(f"Gaia nicht erreichbar – Grenzgrößen nur aus gespeicherten Werten geschätzt ({str(e)[:80]}).")
    cal_a = _calibrate(A, valid, gx, gy, g, r, sig_a)
    cal_b = _calibrate(B, valid, gx, gy, g, r, sig_b, curved=survey)

    def grenze(st):
        return ((st or {}).get("depth") or {}).get("grenze") or {}
    ga = grenze(cur["stats"])
    gb = grenze(json.loads(r2["stats"] or "{}")) if r2 is not None else {}
    # Rückfall ohne Gaia-Sterne im Vergleichsbild: Nullpunkt aus der gespeicherten Tiefe (Apertur 0,8 FWHM statt FWHM →
    # Quellen hier ~0,1–0,3 mag heller, vom Abstand gedeckt). B ist mit k auf A skaliert: fb/k = eigener Fluss der Referenz,
    # ihr Nullpunkt gilt also unabhängig von k; nur wenn einer fehlt, rechnet k zwischen beiden um. `area` = Pixelfläche
    # Referenz/aktuell: auf das Raster dieses Bilds abgetastet ist ihr Aperturfluss um diesen Faktor größer als im eigenen Stack.
    def zp_fn(zp, kk=1.0):
        return lambda f: zp - 2.5 * math.log10(max(float(f) / kk, 1e-9))
    za, zb = ga.get("nullpunkt"), (None if survey else gb.get("nullpunkt"))
    ok_k = k > 0 and math.isfinite(k) and area > 0 and math.isfinite(area)
    zfa = zp_fn(za) if za else (zp_fn(zb, k * area) if zb and ok_k else None)
    zfb = zp_fn(zb, k * area) if zb and ok_k else (zp_fn(za) if za and not survey else None)
    mag_a = cal_a["fn"] if cal_a else zfa
    mag_b = cal_b["fn"] if cal_b else zfb

    def side(cal, sig, gr, zfn, pid, rid, exposure, n_frames, plate=False):
        out = {"belichtung_s": exposure, "aufnahmen": n_frames, "m5": None, "quelle": None}
        if cal:
            out.update(m5=cal["fn"](5 * sig), quelle="gaia", sterne=cal["n"])
        elif not plate and gr.get("m5"):
            out.update(m5=float(gr["m5"]), quelle="tiefe")
        elif not plate and zfn:
            out.update(m5=zfn(5 * sig), quelle="nullpunkt")
        elif not plate and gr.get("lim50"):
            out.update(m5=float(gr["lim50"]), quelle="vollstaendigkeit")
        elif not plate and (m := _device_m5(db, pid, exposure, rid)) is not None:
            out.update(m5=m, quelle="geraet")
        elif plate:
            out.update(m5=DSS_M5, quelle="durchmusterung")
        if out["m5"] is not None:
            out["m5"] = round(float(out["m5"]), 2)
        return out

    a = side(cal_a, sig_a, ga, zfa, cur["project_id"], cur["id"], cur.get("exposure"), cur.get("n_frames"))
    b = (side(cal_b, sig_b, {}, None, None, None, None, None, plate=True) if survey else
         side(cal_b, sig_b, gb, zfb, r2["project_id"], r2["id"], r2["exposure"], r2["n_frames"]))
    margin = REACH_MARGIN_SURVEY if survey else REACH_MARGIN
    tree = None
    if len(gx):
        from scipy.spatial import cKDTree
        tree = cKDTree(np.c_[gx, gy])

    def reach(kind, fa, fb, x, y):
        """Konnte das Bild ohne die Quelle sie zeigen? Rückgabe (True/False/None, Helligkeit der Quelle, Grenze)."""
        if kind == "verschwunden":
            m5, m = a["m5"], (mag_b(fb) if mag_b and fb > 0 else None)
            if m is not None and tree is not None:
                # Gaia-Stern an der Stelle: dessen G, falls schwächer als geschätzt (Rotfilter, Farbterm) – vorsichtig
                d, j = tree.query((x, y))
                if d <= max(2.0, 0.8 * fwhm) and g[j] - m < 2.5:
                    m = max(m, float(g[j]))
        else:
            m5, m = b["m5"], (mag_a(fa) if mag_a and fa > 0 else None)
        if m is None or m5 is None:
            return None, m, m5
        return m <= m5 - margin, m, m5

    return {"aktuell": a, "referenz": b, "abstand": margin, "mag_a": mag_a, "mag_b": mag_b, "reach": reach}, notes


def _flux_relation(A, B, valid, fwhm, curved=False):
    """Helligkeitsbeziehung aktuell ↔ Referenz über gemeinsame Sterne: Faktor k fürs Differenzbild und eine Funktion
    erwarteter Fluss(Referenzfluss). Fotoplatten (DSS) sind nicht linear → dort Parabel in log-log, robust angepasst."""
    from .depth import _ring_phot
    from .quality import detect_stars

    st = detect_stars(np.where(valid, A, 0), max_stars=600, thresh=8)
    if len(st) < 8:
        return None
    r = max(2.0, fwhm)
    fa, _, _, pa, _ = _ring_phot(A, st.x, st.y, r)
    fb, _, _, pb, _ = _ring_phot(B, st.x, st.y, r)
    topa, topb = np.nanpercentile(A[valid], 99.95), np.nanpercentile(B[valid], 99.95)
    ok = np.isfinite(fa) & np.isfinite(fb) & (fa > 0) & (fb > 0) & (pa < 0.8 * topa) & (pb < 0.8 * topb)
    if ok.sum() < 8:
        return None
    k = float(np.median(fa[ok] / fb[ok]))
    if not curved or ok.sum() < 30:
        return k, lambda f: k * np.asarray(f)
    x, y = np.log10(fb[ok]), np.log10(fa[ok])
    use = np.ones(len(x), bool)
    for _ in range(5):
        c = np.polyfit(x[use], y[use], 2)
        res = y - np.polyval(c, x)
        s = 1.4826 * np.median(np.abs(res[use])) + 1e-9
        new = np.abs(res) < 3 * s
        if new.sum() < 20 or (new == use).all():
            break
        use = new
    lo, hi = x.min(), x.max()
    def expect(f):           # außerhalb des angepassten Bereichs (auf der Platte gesättigt bzw. schwächer als die Messsterne): unbekannt
        lf = np.log10(np.clip(np.asarray(f, float), 1e-9, None))
        return np.where((lf >= lo) & (lf <= hi + 0.1), 10 ** np.polyval(c, lf), np.nan)
    return k, expect


def _candidates(A, B, D, Z, valid, fwhm, expect, min_dmag: float = 0.5, vanish: float = 10.0,
                spots=None, reach=None, survey_ref: bool = False) -> tuple[list[dict], dict]:
    """Auffällige Stellen im Differenzbild. `expect(fb)` = erwarteter aktueller Fluss bei Referenzfluss fb (B ist schon
    mit k skaliert). Bei Sternen, die in beiden Bildern da sind, zählt nur eine Abweichung ≥ `MIN_DMAG` von dieser Beziehung.
    `spots` (N, 2: x, y) = Hotpixel-Stellen aus `stackfix.spots` beider Bilder – Kandidaten dort werden verworfen.
    `reach(art, fa, fb, x, y)` → (hätte das andere Bild die Quelle sicher gezeigt?, Helligkeit, Grenzgröße) aus `_limits`;
    „neu“/„verschwunden“ unter dieser Grenze → `unter_grenze`. Ohne Antwort gilt für „verschwunden“ die Flussbeziehung
    (`vanish`·σ), gegen die Durchmusterung (`survey_ref`, Platte nicht geeicht) dann gar nicht."""
    from scipy import ndimage as ndi
    from scipy.spatial import cKDTree

    from .depth import _ring_phot

    H, W = Z.shape
    mx = ndi.maximum_filter(np.abs(Z), size=max(5, int(2 * fwhm) | 1))
    ys, xs = np.nonzero((np.abs(Z) == mx) & (np.abs(Z) > K_SIGMA))
    order = np.argsort(-np.abs(Z[ys, xs]))[:400]
    edge = ndi.distance_transform_edt(valid)
    hot = cKDTree(spots) if spots is not None and len(spots) else None
    hot_r = max(3.0, fwhm)                            # Kandidat im Differenzbild: Lage nach dem Weichzeichnen ± 1–2 px
    rej = {"hotpixel": 0, "rand": 0, "rauschen": 0, "form": 0, "einzelpixel": 0, "heller_stern": 0, "helligkeit": 0, "zu_schwach": 0,
           "unter_grenze": 0}
    margin = max(3 * fwhm + 4, 0.02 * min(H, W))       # Abstand zum Rand der gemeinsamen Fläche
    out = []
    r = max(2.0, fwhm)
    sig_ap = None
    topa, topb = float(np.nanpercentile(A[valid], 99.99)), float(np.nanpercentile(B[valid], 99.99))
    for i in order:
        x0, y0 = int(xs[i]), int(ys[i])
        z = float(Z[y0, x0])
        if edge[y0, x0] < margin:
            rej["rand"] += 1
            continue
        if hot is not None and hot.query_ball_point((x0, y0), hot_r):
            rej["hotpixel"] += 1
            continue
        # örtliches Rauschen: an Stack-Rändern (weniger Aufnahmen) und in Nebeln streut die Differenz stärker als in der Kachel
        win = Z[max(0, y0 - 24):y0 + 25, max(0, x0 - 24):x0 + 25].copy()
        cy, cx = y0 - max(0, y0 - 24), x0 - max(0, x0 - 24)
        win[max(0, cy - 5):cy + 6, max(0, cx - 5):cx + 6] = np.nan
        loc = 1.4826 * np.nanmedian(np.abs(win - np.nanmedian(win)))
        if np.isfinite(loc) and abs(z) / max(loc, 1.0) < K_SIGMA:
            rej["rauschen"] += 1
            continue
        s = int(math.ceil(2 * fwhm)) + 2
        if y0 < s or x0 < s or y0 >= H - s or x0 >= W - s:
            rej["rand"] += 1
            continue
        cut = D[y0 - s:y0 + s + 1, x0 - s:x0 + s + 1] * np.sign(z)
        w = np.clip(cut, 0, None)
        pk = w[s, s] if w[s, s] > 0 else w.max()
        nb = (w[s - 1, s] + w[s + 1, s] + w[s, s - 1] + w[s, s + 1]) / 4
        if pk <= 0 or nb < 0.15 * pk:
            rej["einzelpixel"] += 1
            continue
        w = np.where(w > 0.2 * w.max(), w, 0)
        yy, xx = np.mgrid[-s:s + 1, -s:s + 1]
        f = w.sum()
        cx, cy = (w * xx).sum() / f, (w * yy).sum() / f
        sxx, syy, sxy = ((w * (xx - cx) ** 2).sum() / f, (w * (yy - cy) ** 2).sum() / f, (w * (xx - cx) * (yy - cy)).sum() / f)
        tr, det = sxx + syy, sxx * syy - sxy ** 2
        disc = math.sqrt(max(tr * tr / 4 - det, 0))
        l1, l2 = tr / 2 + disc, max(tr / 2 - disc, 1e-6)
        size = 2.3548 * math.sqrt(max(tr / 2, 0))
        if l1 / l2 > 3.0 or not (0.5 * fwhm <= size <= 2.5 * fwhm):     # Spur, Kante oder Fleck statt Stern
            rej["form"] += 1
            continue
        fa, _, _, _, _ = _ring_phot(A, np.array([x0 + cx]), np.array([y0 + cy]), r)
        fb, _, _, _, _ = _ring_phot(B, np.array([x0 + cx]), np.array([y0 + cy]), r)
        fa, fb = float(fa[0]), float(fb[0])
        if sig_ap is None:                           # Aperturrauschen der Differenz und beider Bilder (gleiche Einheiten)
            sig_ap, sig_a, sig_b = _ap_noise(A - B, valid, r), _ap_noise(A, valid, r), _ap_noise(B, valid, r)
        big = max(fa, fb)
        sat = (np.nanmax(A[y0 - 2:y0 + 3, x0 - 2:x0 + 3]) > 0.5 * topa) or (np.nanmax(B[y0 - 2:y0 + 3, x0 - 2:x0 + 3]) > 0.5 * topb)
        if (big > 30 * sig_ap and abs(fa - fb) < MIN_CHANGE * big) or sat:   # Rest eines hellen/gesättigten Sterns
            rej["heller_stern"] += 1
            continue
        if any(math.hypot(c["_x"] - x0, c["_y"] - y0) < 2 * fwhm for c in out):
            continue
        kind = ("neu" if fb < 3 * sig_b else "heller") if z > 0 else ("verschwunden" if fa < 3 * sig_a else "schwächer")
        # Fehlt eine Quelle in einem Bild, muss das andere Bild sie sicher zeigen UND das fehlende sie zeigen können:
        # eine tiefere Referenz (Durchmusterung, längerer Stack) enthält sonst zwangsläufig „verschwundene“ schwache Sterne.
        if kind == "neu" and not (fa > 8 * sig_b and fa > 5 * sig_a):
            rej["zu_schwach"] += 1
            continue
        if kind == "verschwunden" and not fb > 5 * sig_b:
            rej["zu_schwach"] += 1
            continue
        lim_m5 = None
        if kind in ("neu", "verschwunden"):
            # Physikalisch: Helligkeit der Quelle gegen die Grenzgröße des Bilds ohne sie (Teleskop + Belichtung).
            # Die Flussbeziehung k allein genügt nicht: bei kurzen Stacks stützt sie sich auf wenige, teils gesättigte
            # Sterne, gegen die Fotoplatte ist sie zudem nicht linear (M104, 4 × 10 s: k ~20× zu groß → 25 Fehlalarme)
            ok, m_src, lim_m5 = reach(kind, fa, fb, x0 + cx, y0 + cy) if reach else (None, None, None)
            if ok is False:
                rej["unter_grenze"] += 1
                continue
            if ok is None and kind == "verschwunden":
                ex = float(expect(fb))               # ohne Grenzgröße: bisherige Prüfung über die Flussbeziehung
                if survey_ref or not (math.isfinite(ex) and ex > vanish * sig_a):
                    rej["unter_grenze" if survey_ref else "zu_schwach"] += 1
                    continue
        dm = None
        if fa > 3 * sig_a and fb > 3 * sig_b:            # in beiden Bildern: gegen die Helligkeitsbeziehung prüfen
            ex = float(expect(fb))
            if not math.isfinite(ex) or ex <= 0:
                rej["heller_stern"] += 1
                continue
            dm = -2.5 * math.log10(fa / ex)
            if abs(dm) < min_dmag:
                rej["helligkeit"] += 1
                continue
        out.append({"_x": x0 + cx, "_y": y0 + cy, "sigma": round(abs(z), 1), "art": kind, "f_neu": round(fa, 2), "f_ref": round(fb, 2),
                    "dmag": None if dm is None else round(dm, 2), "laenglich": round(math.sqrt(l1 / l2), 2),
                    **({"grenze": round(lim_m5, 1)} if lim_m5 is not None else {})})
        if len(out) >= MAX_CANDIDATES:
            break
    return out, rej


def _ap_noise(D, valid, r) -> float:
    from .depth import _ring_phot

    rng = np.random.default_rng(2)
    H, W = D.shape
    x, y = rng.uniform(40, W - 40, 3000), rng.uniform(40, H - 40, 3000)
    ok = valid[y.astype(int), x.astype(int)]
    f, _, _, _, _ = _ring_phot(D, x[ok][:1200], y[ok][:1200], r)
    f = f[np.isfinite(f)]
    return float(1.4826 * np.median(np.abs(f - np.median(f)))) if len(f) > 30 else 1.0


def _cutout(A, B, D, x, y, lo, hi, path: Path, half: int = 24, zoom: int = 3):
    """Ausschnitt Referenz | aktuell | Differenz (Norden oben) als PNG."""
    from PIL import Image

    H, W = A.shape
    xi, yi = int(round(x)), int(round(y))
    sl = (slice(max(0, yi - half), min(H, yi + half + 1)), slice(max(0, xi - half), min(W, xi + half + 1)))
    d = D[sl]
    dl = np.nanpercentile(np.abs(d), 99.5) or 1.0
    panels = [_stretch(B[sl], lo, hi), _stretch(A[sl], lo, hi), (np.clip(d / dl, -1, 1) * 127 + 128).astype(np.uint8)]
    h = max(p.shape[0] for p in panels)
    gap = np.full((h, 2), 40, np.uint8)
    row = np.hstack([np.pad(panels[0], ((0, h - panels[0].shape[0]), (0, 0))), gap,
                     np.pad(panels[1], ((0, h - panels[1].shape[0]), (0, 0))), gap,
                     np.pad(panels[2], ((0, h - panels[2].shape[0]), (0, 0)))])[::-1]
    Image.fromarray(row).resize((row.shape[1] * zoom, row.shape[0] * zoom), Image.NEAREST).save(path)


def _night_times(db, pid: int, nights: list | None, limit: int = 3) -> list[str]:
    """Mittlere Aufnahmezeit (UTC, ISO) je Nacht der gestackten Aufnahmen, neueste zuerst – für den Abgleich mit
    Kleinplaneten (die ziehen in einer Nacht weiter; ein Stack über mehrere Nächte zeigt sie höchstens aus einer)."""
    rows = db.q("SELECT night, info FROM frames WHERE project_id=? AND kind='light' AND stacked=1", (pid,))
    by: dict[str, list[float]] = {}
    for r in rows:
        if nights and r["night"] not in nights:
            continue
        d = json.loads(r["info"] or "{}").get("date_obs")
        if not d:
            continue
        try:
            t = datetime.fromisoformat(str(d).replace("Z", "+00:00"))
            by.setdefault(r["night"] or "?", []).append((t if t.tzinfo else t.replace(tzinfo=timezone.utc)).timestamp())
        except ValueError:
            continue
    out = []
    for n in sorted(by, reverse=True)[:limit]:
        ts = sorted(by[n])
        out.append(datetime.fromtimestamp(ts[len(ts) // 2], timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"))
    return out


# -------------------------------------------------------------- Kataloge -- #
def _simbad_near(ra: float, dec: float, radius_as: float, cache_dir: Path) -> list[dict]:
    from .photometry import _tap_csv

    key = hashlib.sha1(f"near|{ra:.5f}|{dec:.5f}|{radius_as:.0f}".encode()).hexdigest()[:16]
    f = cache_dir / f"simbad_{key}.json"
    if f.exists():
        return json.loads(f.read_text(encoding="utf-8"))
    q = (f"SELECT TOP 10 main_id, otype, ra, dec FROM basic WHERE 1=CONTAINS(POINT('ICRS', ra, dec), "
         f"CIRCLE('ICRS', {ra:.6f}, {dec:.6f}, {radius_as / 3600:.6f}))")
    err = None
    for url in SIMBAD_TAP:
        try:
            rows = _tap_csv(url, q, 30)
            break
        except Exception as e:
            err = e
    else:
        raise RuntimeError(f"SIMBAD nicht erreichbar ({err})")
    out = []
    for r in rows:
        try:
            out.append({"name": " ".join(r[0].split()), "otype": r[1].strip(), "sep": round(3600 * _ang(ra, dec, float(r[2]), float(r[3])), 1)})
        except (ValueError, IndexError):
            continue
    out.sort(key=lambda o: o["sep"])
    cache_dir.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(out), encoding="utf-8")
    return out


JPL_FAIL_PAUSE = 1800.0            # s: nach einem Zeitüberschreitungs-/Verbindungsfehler nicht sofort erneut fragen


class SmallBodyError(RuntimeError):
    """JPL SB-Ident nicht erreichbar oder zu langsam – mit ehrlicher Meldung für die Oberfläche."""


def jpl_message(e: Exception) -> str:
    """Verständliche Meldung zu einem Fehler der Kleinkörper-Abfrage."""
    if isinstance(e, SmallBodyError):
        return str(e)
    t = str(e)
    if "timed out" in t or "Timeout" in type(e).__name__:
        return "JPL-Kleinkörperdienst antwortet sehr langsam – Zeitlimit überschritten."
    return f"JPL-Kleinkörperdienst nicht erreichbar ({t[:80]})."


def small_bodies(when: str, ra: float, dec: float, hw_ra: float, hw_dec: float, cache_dir: Path, timeout: float = 60) -> list[dict]:
    """Bekannte Kleinplaneten/Kometen im Feld zur Zeit `when` (UTC) – JPL SB-Ident, **geozentrisch** (MPC-Code 500,
    der Standort verlässt den Rechner nie). Positionen sind daher bis ~10″ ungenau (Parallaxe naher Objekte).

    Kometen mit zweitem Durchgang (genaue Positionen, wenige Sekunden). Asteroiden nur im ersten Durchgang: der zweite
    brauchte am 07.11.2026 für ein 1°-Feld 127 s statt 18 s und scheiterte damit systematisch am Zeitlimit. Der erste
    Durchgang rechnet die Bahnen vereinfacht (Zweikörperproblem ab der Epoche der Bahnelemente) – je weiter die Aufnahme
    davon entfernt ist, desto ungenauer (Bogensekunden bis Bogenminuten). Einträge tragen deshalb `genau` (True/False);
    wer es genau braucht (Asteroiden-Nachweis), schärft einzelne Kandidaten über Horizons nach (`asteroids.refine`).
    Nach einem Zeitüberschreitungs-/Verbindungsfehler wird `JPL_FAIL_PAUSE` lang nicht erneut gefragt (Marke im Cache)."""
    import httpx

    # v3: Asteroiden nur erster Durchgang (Feld `genau`); v2 hatte zwei Durchgänge für beide Arten
    key = hashlib.sha1(f"sb3|{when}|{ra:.3f}|{dec:.3f}|{hw_ra:.2f}|{hw_dec:.2f}".encode()).hexdigest()[:16]
    f = cache_dir / f"jpl_{key}.json"
    if f.exists():
        return json.loads(f.read_text(encoding="utf-8"))
    fail = cache_dir / "jpl_fehler.json"                  # dienstweit: ist JPL gerade sehr langsam, gilt das für alle Felder
    try:
        last = json.loads(fail.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        last = None
    if last and time.time() - float(last.get("t", 0)) < JPL_FAIL_PAUSE:
        wait = max(1, round((JPL_FAIL_PAUSE - (time.time() - float(last["t"]))) / 60))
        raise SmallBodyError(f"{last.get('msg') or 'JPL-Kleinkörperdienst war zuletzt nicht erreichbar.'} "
                             f"Nächster Versuch in etwa {wait} min.")
    h, m = divmod(ra / 15 * 60, 60)
    rs = f"{int(h):02d}-{int(m):02d}-{(m % 1) * 60:05.2f}"
    sgn, ad = ("-" if dec < 0 else ""), abs(dec)
    dd, dm = int(ad), (ad - int(ad)) * 60
    ds = f"{sgn}{dd:02d}-{int(dm):02d}-{(dm % 1) * 60:05.2f}"
    params = {"mpc-code": "500", "obs-time": when.replace("T", "_"), "fov-ra-center": rs, "fov-dec-center": ds,
              "fov-ra-hwidth": f"{min(hw_ra, 10):.3f}", "fov-dec-hwidth": f"{min(hw_dec, 10):.3f}"}
    passes = {"c": {"two-pass": "true", "suppress-first-pass": "true"}, "a": {}}
    out = []
    for kind in ("c", "a"):
        try:
            r = httpx.get(JPL_SBIDENT, params={**params, **passes[kind], "sb-kind": kind}, timeout=timeout)
            r.raise_for_status()
            j = r.json()
        except (httpx.TimeoutException, httpx.TransportError) as e:
            msg = jpl_message(e)
            cache_dir.mkdir(parents=True, exist_ok=True)
            fail.write_text(json.dumps({"t": time.time(), "msg": msg}), encoding="utf-8")
            raise SmallBodyError(msg) from e
        second = bool(j.get("data_second_pass"))
        fields = j.get("fields_second") if second else j.get("fields_first") or []
        for row in (j.get("data_second_pass") if second else j.get("data_first_pass")) or []:
            d = dict(zip(fields, row))
            try:
                ra_s = d.get("Astrometric RA (hh:mm:ss)") or ""
                de_s = d.get("Astrometric Dec (dd mm'ss\")") or ""
                hh, mm, ss = (float(v) for v in re.split(r"[: ]", ra_s.strip())[:3])
                sg = -1 if de_s.strip().startswith("-") else 1
                a, b, c = (abs(float(v)) for v in re.split(r"[ '\"]+", de_s.strip().lstrip("+-"))[:3])
                rate = {k.split(" ")[0]: v for k, v in d.items() if " rate" in k}          # „RA rate ("/h)“, „Dec rate ("/h)“
                out.append({"name": d.get("Object name") or "", "ra": (hh + mm / 60 + ss / 3600) * 15, "dec": sg * (a + b / 60 + c / 3600),
                            "vmag": d.get("Visual magnitude (V)"), "kind": kind, "genau": second,
                            "rate_ra": _num(rate.get("RA")), "rate_dec": _num(rate.get("Dec"))})
            except (ValueError, TypeError):
                continue
    cache_dir.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(out), encoding="utf-8")
    fail.unlink(missing_ok=True)
    return out


def _num(v):
    try:
        x = float(str(v).strip().lstrip("+"))
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def _catalog_check(cands, wcs, shape, t_cur: list, t_ref: list, cache_dir: Path, simbad=True, small=True) -> list[str]:
    notes = []
    H, W = shape
    try:
        for c in (cands[:15] if simbad else []):
            near = _simbad_near(c["ra"], c["dec"], 8, cache_dir)
            if near:
                o = near[0]
                c["katalog"] = {"name": o["name"], "art": OTYPE_LABEL.get(o["otype"], o["otype"]), "abstand": o["sep"],
                                "veraenderlich": bool(VARIABLE.fullmatch(o["otype"])) or o["otype"] in ("QSO", "Bla", "BLL", "AGN")}
    except Exception as e:
        notes.append(f"SIMBAD-Abgleich nicht möglich: {str(e)[:100]}")
    ra0, dec0 = (float(v) for v in wcs.pixel_to_world_values((W - 1) / 2, (H - 1) / 2))
    scale = float(np.sqrt(abs(np.linalg.det(wcs.pixel_scale_matrix))))
    hw_ra, hw_dec = 0.55 * max(W, H) * scale, 0.55 * max(W, H) * scale
    if not small:
        notes.append("Abgleich mit Kleinplaneten ausgeschaltet.")
        return notes
    t0 = time.time()
    for times, kinds in ((t_cur[:2], ("neu", "heller")), (t_ref[:2], ("verschwunden", "schwächer"))):
        sel = [c for c in cands if c["art"] in kinds]
        if not sel:
            continue
        if not times:
            notes.append("Ohne Aufnahmezeit der Referenz kein Abgleich mit Kleinplaneten für „" + "/".join(kinds) + "“.")
            continue
        for when in times:
            left = JPL_BUDGET - (time.time() - t0)
            if left < 15:                                # Gesamtzeit begrenzen: der Dienst ist mal schnell, mal sehr langsam
                notes.append("Kleinplaneten-Abgleich nach Zeitlimit abgebrochen – nicht alle Nächte geprüft.")
                break
            try:
                bodies = small_bodies(when, ra0, dec0, hw_ra, hw_dec, cache_dir, timeout=min(60.0, left))
            except Exception as e:
                notes.append("Kleinplaneten-Abgleich nicht möglich: " + jpl_message(e))
                break
            for c in sel:
                best = min(bodies, key=lambda b: _ang(c["ra"], c["dec"], b["ra"], b["dec"]), default=None)
                sep = _ang(c["ra"], c["dec"], best["ra"], best["dec"]) * 3600 if best else 1e9
                if sep < 60 and "kleinkoerper" not in c:
                    c["kleinkoerper"] = {"name": best["name"], "abstand": round(sep, 1), "vmag": best["vmag"], "zeit": when}
    return notes
