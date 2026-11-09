"""Asteroiden in den eigenen Bildern (Wunsch 10): „Du hast 9 Asteroiden fotografiert, der schwächste 16,2 mag“.

Zwei Wege:
1. **Vorhersage** – welche bekannten Kleinkörper lagen laut JPL SB-Ident im Bildfeld (WCS des Master-Stacks, mittlere
   Aufnahmezeit je Nacht), wie hell und wie schnell? Abfrage **geozentrisch** (`compare.small_bodies`, MPC-Code 500) –
   der Standort verlässt den Rechner nie. Die Parallaxe (Hauptgürtel ≤ ~6″, erdnahe mehr) fängt die Suche im Bild ab:
   gesucht wird im Umkreis `SEARCH_AS` um die vorhergesagte Bahn.
2. **Nachweis** – solange die Rohbilder erreichbar sind: je Nacht ≤ `SAMPLE` Aufnahmen laden, je Aufnahme und Kleinkörper
   einen Ausschnitt um die vorhergesagte Stelle (Stack-Raster, über die gespeicherte Registrierung `skyquality.frame_links`)
   minus Master-Stack bilden und **entlang der Bahn** mitteln (Shift-and-Add wie im Kometenmodus). Im Stack selbst ist ein
   Asteroid weggeclippt oder eine schwache Spur. Nachgewiesen = Signal ≥ `SNR_MIN` im Suchkreis, rund, und in der frühen wie der späten Hälfte der
   Aufnahmen an derselben Stelle (gegen Rauschen/Hotpixel). Helligkeit über den Gaia-Nullpunkt des Stacks
   (`skyquality.zero_point`, V-Band-nah – wie JPLs V).
Ohne Rohbilder bleibt „im Feld, nicht nachgewiesen“. Gezählt werden nur nachgewiesene Kleinkörper.
"""
from __future__ import annotations

import json
import math
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from . import services

ASTEROID_VERSION = 1
SAMPLE = 40                 # Aufnahmen je Nacht für den Nachweis (über die Zeit verteilt)
MAX_NIGHTS = 3              # JPL-Abfragen je Bild (je Nacht eine, Asteroiden brauchen 15–120 s)
MAX_DETECT = 25             # höchstens so viele Kleinkörper je Nacht nachweisen (hellste zuerst)
SEARCH_AS = 15.0            # ″: Suchradius um die Vorhersage (geozentrisch ↔ Standort, Bahnfehler)
SEARCH_AS_ROUGH = 60.0      # ″: höchstens so groß für nur genäherte Bahnen (JPL erster Durchgang, Horizons nicht erreichbar)
ERR_OK = 5.0                # ″: JPL-Fehlerschätzung des ersten Durchgangs, bis zu der keine Horizons-Nachrechnung nötig ist
HZ_BUDGET = 90.0            # s für das Nachschärfen der Kandidaten über Horizons je Bild
HZ_TIMEOUT = 20.0           # s je Horizons-Abfrage
SNR_MIN = 5.0               # Signifikanz im gemittelten Ausschnitt
HALF_MIN = 2.5              # … und in der frühen wie der späten Hälfte der Aufnahmen an derselben Stelle
MAX_ELONG = 2.0             # Achsenverhältnis des Funds (eine mitgeschleppte Spur ist länglich)
MIN_MOTION_FWHM = 2.0       # langsamer: im Stack ein Stern, Differenz zum Stack zeigt nichts
MAG_MARGIN = 1.0            # Vorhersage bis Grenzgröße (5σ) + MAG_MARGIN versuchen
MAG_DEFAULT = 20.5          # ohne gemessene Grenzgröße
JPL_BUDGET = 300.0          # s für alle JPL-Abfragen eines Bildes (erster Durchgang ~20 s je Nacht, Zeitlimit je Abfrage 120 s)
RETRY_PREFIX = "Kleinkörper-Abfrage nicht möglich: JPL-Kleinkörperdienst"   # so beginnen auch ältere gespeicherte Zeitlimit-Fehler


class AsteroidError(Exception):
    """Verständliche Meldung für die Oberfläche. `retry`: JPL nur vorübergehend nicht erreichbar/zu langsam – das Ergebnis
    gilt als „offen“ (nicht „Fehler“) und wird beim nächsten Analyse-Lauf erneut versucht."""

    def __init__(self, msg: str, retry: bool = False):
        super().__init__(msg)
        self.retry = retry


def is_retry(data: dict | None) -> bool:
    """Gespeichertes Ergebnis = vorübergehender JPL-Fehler? Auch alte Einträge ohne `retry` (Zeitlimit-Meldung vor 0.52.x)."""
    return bool(data) and not data.get("ok") and (bool(data.get("retry")) or str(data.get("error") or "").startswith(RETRY_PREFIX))


def _num(v):
    try:
        x = float(str(v).strip().lstrip("+"))
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def _iso(t: float) -> str:
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


# ------------------------------------------------------------------ Vorhersage -- #
def predict(bodies: list[dict], wcs, shape: tuple[int, int], cov: np.ndarray | None, t_mid: float, t0: float, t1: float,
            fetch_later=None) -> list[dict]:
    """Kleinkörper der JPL-Antwort → Bahn im Stack-Raster: Lage zur Zeit t_mid, Bewegung in px/s (aus der Rate oder einer
    zweiten Abfrage `fetch_later()` eine Stunde später). Nur Körper, deren Bahn [t0, t1] im bedeckten Bild liegt – bei nur
    genäherter Bahn (JPL erster Durchgang) auch knapp daneben, um den Unsicherheitskreis (`rough_search`): solche
    Randkandidaten tragen `_edge` und werden erst nach dem Nachschärfen bzw. der Suche behalten oder verworfen (Review PR #73)."""
    H, W = shape
    later = None
    pix_as = float(np.sqrt(abs(np.linalg.det(wcs.pixel_scale_matrix)))) * 3600
    out = []
    for b in bodies:
        ra, dec = b.get("ra"), b.get("dec")
        if ra is None or dec is None:
            continue
        x, y = (float(v) for v in wcs.world_to_pixel_values(ra, dec))
        r_ra, r_dec = _num(b.get("rate_ra")), _num(b.get("rate_dec"))
        if r_ra is None or r_dec is None:                     # ältere Antwort ohne Rate: Lage eine Stunde später
            if later is None:
                try:
                    later = {o["name"]: o for o in (fetch_later() if fetch_later else [])}
                except Exception:
                    later = {}
            o = later.get(b["name"])
            if o is None:
                vx = vy = None
            else:
                x2, y2 = (float(v) for v in wcs.world_to_pixel_values(o["ra"], o["dec"]))
                vx, vy = (x2 - x) / 3600, (y2 - y) / 3600
        else:                                                 # RA-Rate ist dRA·cos δ
            cosd = max(1e-3, math.cos(math.radians(dec)))
            x2, y2 = (float(v) for v in wcs.world_to_pixel_values(ra + r_ra / 3600 / cosd, dec + r_dec / 3600))
            vx, vy = (x2 - x) / 3600, (y2 - y) / 3600
        genau = bool(b.get("genau", True))
        inside = in_field(x, y, vx, vy, t0, t1, t_mid, (H, W), cov)
        edge = False
        if not any(inside):
            if genau or not any(in_field(x, y, vx, vy, t0, t1, t_mid, (H, W), cov, rough_search(_num(b.get("fehler")), 0.0, pix_as))):
                continue
            edge = True
        rate = None if r_ra is None else math.hypot(r_ra, r_dec)
        if rate is None and vx is not None:
            scale = float(np.sqrt(abs(np.linalg.det(wcs.pixel_scale_matrix)))) * 3600
            rate = math.hypot(vx, vy) * 3600 * scale
        out.append({"name": b["name"], "art": "Komet" if b.get("kind") == "c" else "Asteroid", "vmag": _num(b.get("vmag")),
                    "genau": genau, "fehler": _num(b.get("fehler")), "_edge": edge,
                    "ra": ra, "dec": dec, "rate": None if rate is None else round(rate, 1),
                    "_x": x, "_y": y, "_vx": vx, "_vy": vy, "_inside": all(inside)})
    out.sort(key=lambda o: o["vmag"] if o["vmag"] is not None else 99)
    return out


def in_field(x, y, vx, vy, t0, t1, t_mid, shape, cov, margin: float = 0.0) -> list[bool]:
    """Liegen Anfang, Ende und Mitte der Bahn im bedeckten Bild? Mit `margin` (px): auch so weit daneben; die Abdeckung
    wird dann an der nächsten Bildstelle geprüft."""
    H, W = shape
    pts = [(x, y)] if vx is None else [(x + vx * (t - t_mid), y + vy * (t - t_mid)) for t in (t0, t1)]
    out = []
    for px, py in pts + [(x, y)]:
        ok = -margin <= px < W + margin and -margin <= py < H + margin
        if ok and cov is not None:
            cx, cy = int(min(max(px, 0), W - 1)), int(min(max(py, 0), H - 1))
            if margin:                                    # nächste bedeckte Stelle im Umkreis zählt
                r = int(math.ceil(margin))
                win = cov[max(0, cy - r):cy + r + 1, max(0, cx - r):cx + r + 1]
                ok = bool(win.size) and float(win.max()) > 0.3
            else:
                ok = cov[cy, cx] > 0.3
        out.append(ok)
    return out


# ------------------------------------------------------------------- Nachweis -- #
def stamp_grid(half: int) -> tuple[np.ndarray, np.ndarray]:
    d = np.arange(-half, half + 1, dtype=np.float64)
    return np.meshgrid(d, d)                                  # (gx, gy): Spalten-, Zeilenversatz


def frame_stamp(L: np.ndarray, Minv: np.ndarray, sc: float, cx: float, cy: float, half: int) -> np.ndarray:
    """Ausschnitt der Aufnahme L (Frame-Pixel) auf dem Stack-Raster um (cx, cy); Minv = Referenz→Frame, sc = resolution_scale."""
    from scipy import ndimage as ndi
    gx, gy = stamp_grid(half)
    xs, ys = cx + gx, cy + gy
    xr, yr = (xs + 0.5) / sc - 0.5, (ys + 0.5) / sc - 0.5      # Stack-Raster → Referenzraster (nativ)
    fx = Minv[0, 0] * xr + Minv[0, 1] * yr + Minv[0, 2]
    fy = Minv[1, 0] * xr + Minv[1, 1] * yr + Minv[1, 2]
    return ndi.map_coordinates(L, [fy, fx], order=1, mode="constant", cval=np.nan)


def stack_stamp(L: np.ndarray, cx: float, cy: float, half: int) -> np.ndarray:
    from scipy import ndimage as ndi
    gx, gy = stamp_grid(half)
    return ndi.map_coordinates(L, [cy + gy, cx + gx], order=1, mode="constant", cval=np.nan)


def _combine(stamps: list[np.ndarray]) -> np.ndarray:
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        return np.nanmean(np.stack(stamps), 0)


def _sigma(a: np.ndarray, mask: np.ndarray) -> float:
    v = a[mask & np.isfinite(a)]
    if len(v) < 30:
        return float("nan")
    return float(1.4826 * np.median(np.abs(v - np.median(v)))) or float("nan")


def detect(stamps: list[np.ndarray], fwhm: float, search_px: float, aperture: float) -> dict:
    """Gemittelter Ausschnitt (Shift-and-Add) → Fund im Suchkreis. stamps: Differenzen Aufnahme − Stack, je auf die
    vorhergesagte Lage zentriert, zeitlich geordnet. Gibt {found, snr, dx, dy, flux, flux_err, img} zurück."""
    from scipy import ndimage as ndi
    A = _combine(stamps)
    half = A.shape[0] // 2
    gx, gy = stamp_grid(half)
    rr = np.hypot(gx, gy)
    sig = fwhm / 2.3548
    A0 = np.where(np.isfinite(A), A, 0.0)
    S = ndi.gaussian_filter(A0, sig)
    outer = rr > search_px + 2 * fwhm
    s_sm = _sigma(S, outer)
    res = {"found": False, "img": A}
    if not math.isfinite(s_sm):
        res["why"] = "zu wenig Bildfläche"
        return res
    inner = (rr <= search_px) & np.isfinite(A)
    Si = np.where(inner, S, -np.inf)
    iy, ix = np.unravel_index(int(np.argmax(Si)), S.shape)
    z = float(S[iy, ix] / s_sm)
    # Unterpixel-Lage (Schwerpunkt im 3×3)
    w = np.clip(S[iy - 1:iy + 2, ix - 1:ix + 2], 0, None) if 0 < iy < S.shape[0] - 1 and 0 < ix < S.shape[1] - 1 else None
    if w is not None and w.sum() > 0:
        oy, ox = np.mgrid[-1:2, -1:2]
        fy, fx = iy + float((w * oy).sum() / w.sum()), ix + float((w * ox).sum() / w.sum())
    else:
        fy, fx = float(iy), float(ix)
    # frühe und späte Hälfte der Aufnahmen müssen den Punkt an derselben Stelle zeigen: ein echter Asteroid bleibt beim Mitführen
    # stehen, alles andere (Stern-Rest, anderer Kleinkörper, falsche Bahn) wandert im Ausschnitt und liegt in den Hälften woanders
    halves, spots = [], []
    n = len(stamps)
    rw = int(math.ceil(2 * fwhm))
    y0, x0 = int(round(fy)), int(round(fx))
    for part in (stamps[: n // 2], stamps[n // 2:]):
        if len(part) < 2:
            halves.append(0.0)
            spots.append((fx, fy))
            continue
        Hh = ndi.gaussian_filter(np.where(np.isfinite(h := _combine(part)), h, 0.0), sig)
        sh = _sigma(Hh, outer)
        ys, xs = slice(max(0, y0 - rw), y0 + rw + 1), slice(max(0, x0 - rw), x0 + rw + 1)
        win = Hh[ys, xs]
        if not math.isfinite(sh) or not win.size:
            halves.append(0.0)
            spots.append((fx, fy))
            continue
        wy, wx = np.unravel_index(int(np.argmax(win)), win.shape)
        spots.append((xs.start + wx, ys.start + wy))
        halves.append(float(win[wy, wx] / sh))
    drift = math.hypot(spots[0][0] - spots[1][0], spots[0][1] - spots[1][1])
    # Form: rund (Achsenverhältnis ≤ 2) – eine mitgeschleppte Spur ist länglich
    yy, xx = np.mgrid[max(0, y0 - rw):y0 + rw + 1, max(0, x0 - rw):x0 + rw + 1]
    wv = A0[max(0, y0 - rw):y0 + rw + 1, max(0, x0 - rw):x0 + rw + 1]
    wv = np.where(wv > 0.2 * wv.max(), wv, 0) if wv.size and wv.max() > 0 else np.zeros_like(wv)
    elong = 99.0
    if wv.sum() > 0:
        f = wv.sum()
        cx, cy = (wv * xx).sum() / f, (wv * yy).sum() / f
        sxx, syy, sxy = (wv * (xx - cx) ** 2).sum() / f, (wv * (yy - cy) ** 2).sum() / f, (wv * (xx - cx) * (yy - cy)).sum() / f
        tr, det = sxx + syy, sxx * syy - sxy ** 2
        disc = math.sqrt(max(tr * tr / 4 - det, 0))
        elong = math.sqrt((tr / 2 + disc) / max(tr / 2 - disc, 1e-6))
    # Aperturfotometrie auf dem ungeglätteten Mittel (Ring 2–3 Aperturradien)
    d = np.hypot(gx - (fx - half), gy - (fy - half))
    ap, ring = (d <= aperture) & np.isfinite(A), (d > 2 * aperture) & (d <= 3 * aperture) & np.isfinite(A)
    bg = float(np.median(A[ring])) if ring.sum() > 10 else 0.0
    flux = float((A[ap] - bg).sum())
    s_pix = _sigma(A, outer)
    flux_err = s_pix * math.sqrt(max(1, ap.sum())) * 1.2 if math.isfinite(s_pix) else float("nan")
    steady = drift <= max(2.0, 0.8 * fwhm)
    res.update(snr=round(z, 1), halves=[round(h, 1) for h in halves], drift=round(drift, 1), elong=round(elong, 2),
               dx=fx - half, dy=fy - half, flux=flux, flux_err=flux_err,
               found=z >= SNR_MIN and min(halves) >= HALF_MIN and steady and elong <= MAX_ELONG and flux > 0)
    if not res["found"]:
        res["why"] = ("kein Signal" if z < SNR_MIN else "nicht in allen Aufnahmen" if min(halves) < HALF_MIN
                      else "wandert gegen die Bahn – anderes Objekt" if not steady or elong > MAX_ELONG else "kein Fluss")
    return res


def self_overlap(track_px: np.ndarray, at: np.ndarray, fwhm: float, n_stack: int) -> float:
    """Anteil des Asteroidenlichts, den der Master-Stack an derselben Stelle selbst enthält (er wird mit abgezogen):
    Mittel über die Aufnahmezeiten der Stichprobe von Σ_f exp(−d²/(4σ²)) / N_Stack (Überlapp zweier Gauß-Profile)."""
    if not len(track_px) or n_stack <= 0:
        return 0.0
    s2 = (fwhm / 2.3548) ** 2
    d2 = ((at[:, None, :] - track_px[None, :, :]) ** 2).sum(-1)
    return float(np.mean(np.exp(-d2 / (4 * s2)).sum(1)) / n_stack)


def save_png(img: np.ndarray, aperture: float, path: Path, zoom: int = 3) -> None:
    """Gemittelter Ausschnitt (Norden oben) als PNG mit Ring an der Mitte."""
    from PIL import Image
    a = np.where(np.isfinite(img), img, 0.0)
    lo, hi = np.percentile(a, 5), np.percentile(a, 99.7)
    hi = hi if hi > lo else lo + 1
    g = (np.clip((a - lo) / (hi - lo), 0, 1) ** 0.6 * 255).astype(np.uint8)[::-1]
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(g).resize((g.shape[1] * zoom, g.shape[0] * zoom), Image.NEAREST).save(path)


# ------------------------------------------------------------------- Ablauf -- #
def _frames(db, pid: int, ids: set) -> list[dict]:
    from .pipeline import frame_time
    out = []
    for r in db.q("SELECT id, night, path, deleted, info, metrics FROM frames WHERE project_id=? AND kind='light' AND stacked=1", (pid,)):
        if ids and r["id"] not in ids:
            continue
        t = frame_time(r)
        if t is None:
            continue
        out.append({**r, "t": t, "metrics": json.loads(r["metrics"] or "{}"), "infod": json.loads(r["info"] or "{}")})
    return out


def compute(pl, rid: int, *, small_bodies=None, gaia: bool = True, m5: float | None = None, log=lambda m: None) -> dict:
    """Asteroiden eines Ergebnisses vorhersagen und – wo die Rohbilder erreichbar sind – nachweisen.
    pl: Pipeline (DB, Master-Stack, Kalibrierung), small_bodies(when, ra, dec, hw_ra, hw_dec, cache_dir, timeout)."""
    from concurrent.futures import ThreadPoolExecutor

    from . import compare, photometry, skyquality
    from .fits_io import to_channels
    small_bodies = small_bodies or compare.small_bodies
    db, wd = pl.db, pl.s.work_dir
    r = db.one("SELECT r.*, p.name pname FROM results r JOIN projects p ON p.id=r.project_id WHERE r.id=?", (rid,))
    if not r:
        raise AsteroidError("Ergebnis nicht gefunden")
    pid = r["project_id"]
    states = pl._master(pid)
    st = states.get(None) or (next(iter(states.values())) if states else None)
    if st is None:
        raise AsteroidError("Kein Master-Stack vorhanden – Asteroiden lassen sich nur in selbst gestackten Bildern suchen.")
    mean, cov = st.result()
    H, W = mean.shape[1:]
    try:
        saved = json.loads((wd / f"p{pid}" / "wcs.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise AsteroidError("Dieses Bild ist nicht per Plate-Solving gelöst – ohne Himmelskoordinaten keine Vorhersage.")
    if saved.get("key", "").split("|")[-1] != f"{H}x{W}":
        raise AsteroidError("Die gespeicherte Plate-Solving-Lösung passt nicht zum Master-Stack – bitte neu entwickeln.")
    hdr = saved["wcs"]
    wcs = photometry.wcs_from(hdr)
    sc = float(st.meta.get("scale") or 1)
    pix_as = float(np.sqrt(abs(np.linalg.det(wcs.pixel_scale_matrix)))) * 3600     # ″ je Stack-Pixel
    fwhm = float(st.meta.get("ref_fwhm") or 3.0) * sc
    ra0, dec0 = (float(v) for v in wcs.pixel_to_world_values((W - 1) / 2, (H - 1) / 2))
    hw = 0.55 * max(W, H) * pix_as / 3600
    frames = _frames(db, pid, set(st.meta.get("frames") or []))
    if not frames:
        raise AsteroidError("Keine gestackten Aufnahmen mit Aufnahmezeit.")
    by: dict[str, list] = {}
    for f in frames:
        by.setdefault(f["night"] or "?", []).append(f)
    nights = sorted(by, key=lambda n: -len(by[n]))[:MAX_NIGHTS]
    notes: list[str] = []
    if len(by) > MAX_NIGHTS:
        notes.append(f"Abgeglichen wurden die {MAX_NIGHTS} Nächte mit den meisten Aufnahmen (von {len(by)}).")
    exps = [float(f["infod"].get("exptime") or 0) for f in frames]
    exptime = float(np.median(exps)) if exps else 1.0
    # Nullpunkt (Gaia) für Helligkeiten
    zp = None
    if gaia:
        try:
            log("Nullpunkt aus Gaia-Sternen bestimmen")
            zp = _zero_point(mean, cov, hdr, ra0, dec0, pix_as, W, H, exptime, wd)
        except Exception as e:
            notes.append(f"Ohne Helligkeitsmessung: Nullpunkt nicht bestimmbar ({str(e)[:100]}).")
    else:
        notes.append("Ohne Helligkeitsmessung: Gaia-Abgleich ist ausgeschaltet.")
    aperture = float(zp["aperture_px"]) if zp else max(3.0, 2.0 * fwhm)
    lim = (m5 + MAG_MARGIN) if m5 else MAG_DEFAULT
    Lstack = skyquality.luma(mean)
    links = skyquality.frame_links(st.meta, [{"id": f["id"], "metrics": f["metrics"]} for f in frames])
    k0 = float(st.meta.get("flux_k0") or 1.0)
    cache = wd / "vergleich"                                  # gemeinsamer JPL-Zwischenspeicher mit dem Vergleich
    out_dir = wd / f"p{pid}" / "asteroiden" / f"r{rid}"
    for old in out_dir.glob("a*.png") if out_dir.exists() else []:
        old.unlink(missing_ok=True)
    geo = services.view_geometry(wd, pid, json.loads(r["files"] or "{}"))
    geo = geo if geo and services.geometry_matches(geo) else None
    pos = lambda x, y: (lambda v: {"x": round(float(v[0]), 1), "y": round(float(v[1]), 1)} if v is not None else {"x": None, "y": None})(
        services.to_view(geo, x, y) if geo else None)
    search_px = max(2.5 * fwhm, SEARCH_AS / pix_as)
    half = int(math.ceil(search_px + 3 * aperture + 2 * fwhm + 2))
    lib, fcache, hz_left = None, {}, HZ_BUDGET
    items, night_info, t_start = [], [], time.time()
    n_stack = len(st.meta.get("frames") or frames)
    for night in nights:
        fr = sorted(by[night], key=lambda f: f["t"])
        t0, t1 = fr[0]["t"], fr[-1]["t"]
        t_mid = fr[len(fr) // 2]["t"]
        left = JPL_BUDGET - (time.time() - t_start)
        if left < 15:
            notes.append("JPL-Abfrage nach Zeitlimit abgebrochen – nicht alle Nächte geprüft.")
            break
        log(f"Kleinkörper im Feld abfragen ({night})")
        try:
            bodies = small_bodies(_iso(t_mid), ra0, dec0, hw, hw, cache, timeout=min(compare.JPL_TIMEOUT, left))
        except Exception as e:
            raise AsteroidError("Kleinkörper-Abfrage nicht möglich: " + compare.jpl_message(e),
                                retry=getattr(e, "retry", not isinstance(e, (ValueError, TypeError, KeyError)))) from e
        pred = predict(bodies, wcs, (H, W), cov, t_mid, t0, t1,
                       fetch_later=lambda: small_bodies(_iso(t_mid + 3600), ra0, dec0, hw, hw, cache,
                                                         timeout=min(compare.JPL_TIMEOUT, max(15.0, JPL_BUDGET - (time.time() - t_start)))))
        avail = [f for f in fr if not f["deleted"] and f["path"] and Path(f["path"]).exists() and f["id"] in links]
        ni = {"night": night, "zeit": _iso(t_mid), "aufnahmen": len(fr), "rohbilder": len(avail), "im_feld": 0,
              "dauer_min": round((t1 - t0) / 60, 1)}
        night_info.append(ni)
        if not avail:                                        # ohne Rohbilder lässt sich ein Randkandidat nie klären
            pred = [b for b in pred if not b["_edge"]]
        todo = []
        for b in pred:
            b.update(night=night, status="keine_rohbilder")
            moving = b["_vx"] is not None
            motion = math.hypot(b["_vx"], b["_vy"]) * (t1 - t0) if moving else 0.0
            b["bewegung_px"] = round(motion, 1)
            if not avail:
                continue
            if not moving or motion < MIN_MOTION_FWHM * fwhm:
                b["status"] = "zu_langsam"
            elif b["vmag"] is not None and b["vmag"] > lim:
                b["status"] = "zu_schwach"
            elif len(todo) < MAX_DETECT:
                b["status"] = "nicht_gefunden"
                todo.append(b)
            else:
                b["status"] = "nicht_geprueft"
        if todo:
            for b in todo:                                # JPL schätzt den Fehler selbst klein: wie genau behandeln
                if not b["genau"] and b["fehler"] is not None and b["fehler"] <= ERR_OK:
                    b["genau"] = True
            rough = [b for b in todo if not b["genau"]]
            if rough:
                log(f"{len(rough)} Bahnen bei JPL Horizons genau berechnen ({night})")
                hz_left = refine_all(rough, wcs, t_mid, t0, t1, wd / "komet", hz_left)
                still = sum(1 for b in rough if not b["genau"])
                if still:
                    notes.append(f"{still} Kleinkörper nur mit genäherter Bahn gesucht (Horizons nicht erreichbar) – "
                                 f"größerer Suchkreis nach JPLs Fehlerschätzung, höchstens {SEARCH_AS_ROUGH:.0f}″.")
            gone = {id(b) for b in todo if b["_edge"] and b["genau"]
                    and not any(in_field(b["_x"], b["_y"], b["_vx"], b["_vy"], t0, t1, t_mid, (H, W), cov))}
            if gone:                                          # genau gerechnet: liegt doch außerhalb
                todo = [b for b in todo if id(b) not in gone]
                pred = [b for b in pred if id(b) not in gone]
            for b in todo:
                b["_search"] = search_px if b["genau"] else rough_search(b["fehler"], search_px, pix_as)
            half = int(math.ceil(max(b["_search"] for b in todo) + 3 * aperture + 2 * fwhm + 2))
            pick = [avail[int(i)] for i in np.unique(np.linspace(0, len(avail) - 1, min(SAMPLE, len(avail))).round())]
            if lib is None:
                lib = pl._library()
            _sensor_cache(pl, pid, pick, night, fcache)
            log(f"{len(todo)} Kleinkörper in {len(pick)} Aufnahmen suchen ({night})")

            def one(f):
                try:
                    raw, _ = pl._load(f["path"], f["infod"], lib, night, fcache)
                except Exception:
                    return None
                L = skyquality.luma(to_channels(raw, f["infod"].get("bayerpat")))
                kr, M = links[f["id"]]
                Minv, k = np.linalg.inv(M), kr / k0
                res = []
                for b in todo:
                    cx, cy = b["_x"] + b["_vx"] * (f["t"] - t_mid), b["_y"] + b["_vy"] * (f["t"] - t_mid)
                    a = frame_stamp(L, Minv, sc, cx, cy, half) * k - stack_stamp(Lstack, cx, cy, half)
                    ok = np.isfinite(a)
                    if ok.mean() < 0.7:
                        res.append(None)
                        continue
                    res.append((a - np.median(a[ok])).astype(np.float32))
                return f["t"], res

            with ThreadPoolExecutor(max(1, pl.s.threads)) as ex:
                got = [g for g in ex.map(one, pick) if g is not None]
            ts = np.array([g[0] for g in got])
            for j, b in enumerate(todo):
                stamps = [g[1][j] for g in got if g[1][j] is not None]
                if len(stamps) < 6:
                    b["status"] = "nicht_geprueft"
                    b["grund"] = "zu wenige Aufnahmen decken die Bahn ab"
                    continue
                d = detect(stamps, fwhm, b["_search"], aperture)
                b["snr"] = d.get("snr")
                if not d["found"]:
                    b["grund"] = d.get("why")
                    continue
                at = np.c_[b["_x"] + b["_vx"] * (ts - t_mid), b["_y"] + b["_vy"] * (ts - t_mid)]
                every = np.array([f["t"] for f in fr])
                trk = np.c_[b["_x"] + b["_vx"] * (every - t_mid), b["_y"] + b["_vy"] * (every - t_mid)]
                phi = self_overlap(trk, at, fwhm, n_stack)
                if phi > 0.5:
                    b["status"] = "zu_langsam"
                    continue
                flux = d["flux"] / (1 - phi)
                b.update(status="nachgewiesen", snr=d["snr"], abweichung=round(math.hypot(d["dx"], d["dy"]) * pix_as, 1),
                         _dx=d["dx"], _dy=d["dy"], aufnahmen=len(stamps))
                if zp:
                    b["mag"] = round(zp["zp"] - 2.5 * math.log10(flux / max(exptime, 1e-3)), 2)
                    if d["flux_err"] and math.isfinite(d["flux_err"]):
                        b["mag_err"] = round(1.0857 * d["flux_err"] / d["flux"], 2)
                b["_img"] = d["img"]
        for b in pred:                                         # Bahn und Lage im angezeigten Bild
            # Randkandidat (genäherte Bahn knapp außerhalb): nur behalten, wenn nachgewiesen oder genau gerechnet im Feld
            if b.pop("_edge") and b["status"] != "nachgewiesen" and not (
                    b["genau"] and any(in_field(b["_x"], b["_y"], b["_vx"], b["_vy"], t0, t1, t_mid, (H, W), cov))):
                b.pop("_img", None)
                continue
            ni["im_feld"] += 1
            dx, dy = b.pop("_dx", 0.0), b.pop("_dy", 0.0)
            x, y, vx, vy = b.pop("_x"), b.pop("_y"), b.pop("_vx"), b.pop("_vy")
            b.update(pos(x + dx, y + dy))
            if vx is not None:
                p0, p1 = pos(x + dx + vx * (t0 - t_mid), y + dy + vy * (t0 - t_mid)), pos(x + dx + vx * (t1 - t_mid), y + dy + vy * (t1 - t_mid))
                b["bahn"] = [[p0["x"], p0["y"]], [p1["x"], p1["y"]]] if p0["x"] is not None else None
            b.pop("_inside", None)
            b.pop("_search", None)
            img = b.pop("_img", None)
            if img is not None:
                name = f"a{len(items)}.png"
                save_png(img, aperture, out_dir / name)
                b["bild"] = name
            items.append(b)
    found = [b for b in items if b["status"] == "nachgewiesen"]
    names = {b["name"] for b in found}
    with_mag = [b for b in found if b.get("mag") is not None]
    faint = max(with_mag, key=lambda b: b["mag"]) if with_mag else None
    if not any(n["rohbilder"] for n in night_info):
        notes.append("Die Rohbilder sind nicht mehr erreichbar – gezeigt wird nur, was laut JPL im Feld lag (nicht nachgewiesen).")
    order = {"nachgewiesen": 0, "nicht_gefunden": 1, "keine_rohbilder": 2, "zu_langsam": 3, "nicht_geprueft": 4, "zu_schwach": 5}
    items.sort(key=lambda b: (order.get(b["status"], 9), b.get("mag") or b["vmag"] or 99))
    return {"v": ASTEROID_VERSION, "ok": True, "erstellt": time.time(), "naechte": night_info, "kleinkoerper": items,
            "nachgewiesen": len(names), "im_feld": len({b["name"] for b in items}),
            "schwaechster": {"name": faint["name"], "mag": faint["mag"], "x": faint.get("x"), "y": faint.get("y")} if faint else None,
            "grenze": round(lim, 1), "nullpunkt": round(zp["zp"], 2) if zp else None, "suchradius": round(search_px * pix_as, 1),
            "bild": {"w": geo["view"][0], "h": geo["view"][1]} if geo and geo.get("offset") is not None else None,
            "hinweise": notes}


_DESIG = r"\d{4} [A-Z]{1,2}\d*"                # vorläufige Bezeichnung „1927 LA“, „1988 RH9“, „2004 MN4“


def horizons_command(name: str) -> str:
    """Horizons-Befehl aus dem SB-Ident-Namen. Nummer → „433;“ („433 Eros (A898 PA)“, „(4711)“, „(4711) Testoid“);
    vorläufige Bezeichnung → „DES=1927 LA;“ – SB-Ident schreibt sie ohne Nummer **in Klammern** („(1927 LA)“), eine
    Klammer nur mit Ziffern ist dagegen eine Nummer. Sonst der Name selbst („Eros;“)."""
    import re
    n = name.strip()
    m = re.fullmatch(r"\(([^)]*)\)(.*)", n)
    if m:                                             # „(…)“ am Anfang: Ziffern = Nummer, sonst Bezeichnung
        inner = m.group(1).strip()
        return f"{inner};" if inner.isdigit() else f"DES={inner};"
    if re.fullmatch(_DESIG, n.split("(")[0].strip()):
        return f"DES={n.split('(')[0].strip()};"
    m = re.match(r"^(\d+)(\s|$)", n)
    if m:
        return f"{m.group(1)};"
    return f"{n.split('(')[0].strip()};"


def rough_search(err_as: float | None, search_px: float, pix_as: float) -> float:
    """Suchkreis (Stack-Pixel) für eine nur genäherte Bahn: Normalkreis + 3 × JPL-Fehlerschätzung, höchstens SEARCH_AS_ROUGH;
    ohne Schätzung gleich SEARCH_AS_ROUGH."""
    if err_as is None:
        return max(search_px, SEARCH_AS_ROUGH / pix_as)
    return max(search_px, min(SEARCH_AS_ROUGH, SEARCH_AS + 3 * err_as) / pix_as)


def refine(b: dict, wcs, t_mid: float, t0: float, t1: float, cache_dir: Path, timeout: float = HZ_TIMEOUT) -> None:
    """Genaue geozentrische Bahn eines Kandidaten über JPL Horizons (wie der Kometenmodus; nur Name und Zeiten werden
    gesendet). Setzt Lage zur Zeit t_mid und Bewegung neu, `genau` = True. Wirft bei Fehlern."""
    from . import comet
    rows = comet.horizons(b["name"], t0, t1, cache_dir, timeout=timeout, command=horizons_command(b["name"]))
    span = max(600.0, t1 - t0)
    ra, dec, _ = comet.topocentric(rows, np.array([t_mid - span / 2, t_mid, t_mid + span / 2]), None)
    x, y = (np.asarray(v, float) for v in wcs.world_to_pixel_values(ra, dec))
    b["_x"], b["_y"] = float(x[1]), float(y[1])
    b["_vx"], b["_vy"] = float((x[2] - x[0]) / span), float((y[2] - y[0]) / span)
    b["genau"] = True


def refine_all(bodies: list[dict], wcs, t_mid: float, t0: float, t1: float, cache_dir: Path, budget: float) -> float:
    """Kandidaten nacheinander nachschärfen, solange das Zeitbudget reicht; gibt das restliche Budget zurück."""
    from . import comet
    if not comet.horizons_enabled():
        return budget
    for b in bodies:
        if budget < 3:
            break
        t = time.time()
        try:
            refine(b, wcs, t_mid, t0, t1, cache_dir, timeout=min(HZ_TIMEOUT, budget))
        except Exception:
            pass                                          # bleibt genähert, größerer Suchkreis
        budget -= time.time() - t
    return budget


def _sensor_cache(pl, pid: int, frames: list[dict], night: str, cache: dict) -> None:
    """Gespeicherte Glüh- und Fehlerkarten der Kamera in den Lade-Cache legen – `Pipeline._load` wendet sie nur an, wenn sie
    dort stehen. Sonst behielten die Einzelbilder Randglühen und Hotpixel-Grüppchen, die im Master-Stack schon fehlen
    (Review PR #67). Nur gespeicherte Bibliotheken, kein Nachmessen; Glühen nur bei eingeschalteter Projektoption."""
    from . import calibration
    p = pl.db.one("SELECT options FROM projects WHERE id=?", (pid,))
    glow = json.loads((p or {}).get("options") or "{}").get("sensor_glow", True)
    glib = calibration.GlowLibrary(pl.s.work_dir / "glow")
    dlib = calibration.DefectLibrary(pl.s.work_dir / "defects")
    for key in {glib.key(f["infod"]) for f in frames}:
        if glow and ("glow", key, night) not in cache:
            try:
                med, _ = glib.medians(key, night)
                model = calibration.model_from(med)
                if model:
                    cache[("glow", key, night)] = model
            except Exception:
                pass
        if ("defects", key, night) not in cache:
            try:
                bad, _ = dlib.get(key, night)
                if bad is not None and len(bad):
                    cache[("defects", key, night)] = bad
            except Exception:
                pass


def _zero_point(mean, cov, hdr, ra0, dec0, pix_as, W, H, exptime, wd) -> dict:
    """Gaia-Nullpunkt des Stacks (wie die Himmelsqualität; gemeinsamer Gaia-Zwischenspeicher)."""
    from . import photometry, skyquality
    cat = photometry.gaia_stars(ra0, dec0, 0.5 * pix_as / 3600 * math.hypot(W, H), wd / "gaia", gmag_max=skyquality.GMAG_MAX, with_g=True)
    return skyquality.zero_point(mean, cov, hdr, cat, exptime)


def tally(db) -> dict:
    """Über alle Bilder: verschiedene nachgewiesene Kleinkörper und der schwächste (für Dashboard und Sage-Erfolge)."""
    seen: dict[str, float | None] = {}
    for r in db.q("SELECT json_extract(stats,'$.asteroids') a FROM results WHERE json_extract(stats,'$.asteroids.ok')=1"):
        try:
            d = json.loads(r["a"] or "{}")
        except ValueError:
            continue
        for b in d.get("kleinkoerper") or []:
            if b.get("status") == "nachgewiesen":
                m = b.get("mag")
                seen[b["name"]] = max(seen.get(b["name"]) or -99, m) if m is not None else seen.get(b["name"])
    mags = [m for m in seen.values() if m is not None]
    return {"asteroids": len(seen), "faintest_asteroid": max(mags) if mags else None}
