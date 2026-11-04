"""„Wie tief schaust du?“ – was das fertige Bild über den Himmel verrät (Wunsch 12).

Grundlage ist die Plate-Solving-Lösung des Master-Stacks (`work/p<id>/wcs.json`) und der lineare Stack der Version:
1. **Grenzgröße:** erzwungene Aperturfotometrie an allen Gaia-DR3-Positionen im Feld. Das Rauschen einer Apertur wird an
   leeren Himmelsstellen gemessen (Stack-Pixel sind durch die Registrierung korreliert – Pixelrauschen × √N wäre zu klein).
   Erfasst = SNR ≥ 5, Schwerpunkt passt zur Katalogposition, kein hellerer Nachbar in der Apertur.
   Daraus: Vollständigkeit je Größenklasse, schwächster sicher erfasster Stern und die rechnerische 5σ-Grenze.
2. **Entfernungen:** erfasste Sterne mit verlässlicher Parallaxe (Parallaxe/Fehler ≥ 5) → nächster/fernster Stern,
   Verteilung; Hauptobjekt aus Sage (`object_facts`); Galaxien/Quasare mit Rotverschiebung aus SIMBAD (Spiegel Harvard,
   sonst CDS) – im Bild „sichtbar“, wenn an ihrer Position SNR ≥ 5. Rotverschiebung → Lichtlaufzeit (Planck 2018).
Online gehen nur Feldmitte und Radius (Gaia, SIMBAD); Ergebnisse werden in `work/gaia` bzw. `work/simbad` gespeichert.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import numpy as np

from . import photometry, services

DEPTH_VERSION = 1
SNR_MIN = 5.0
MAG_TOL = 0.75                     # mag: gemessene gegen Katalog-Helligkeit (Farbterm der Kamera passt hinein)
POE_MIN = 5.0                      # Parallaxe / Fehler
PLX_ZERO = -0.017                  # mas, mittlerer Nullpunkt der Gaia-DR3-Parallaxen (Lindegren et al. 2021)
LY_PER_PC = 3.26156
DIST_EDGES = [1, 10, 30, 100, 300, 1000, 3000, 10000, 30000, 100000]      # Lichtjahre
MAX_POINTS = 1500                  # Punkte für den 3D-Tunnel
SIMBAD_TAP = ("https://simbad.cfa.harvard.edu/simbad/sim-tap/sync", "https://simbad.cds.unistra.fr/simbad/sim-tap/sync")
# SIMBAD-Objektarten, die als Galaxie bzw. aktiver Galaxienkern zählen (Gruppen/Haufen nicht – deren Rotverschiebung ist ein Mittel)
GALAXY_TYPES = {"G", "GiC", "GiG", "GiP", "BiC", "IG", "PaG", "SBG", "bCG", "LSB", "EmG", "H2G", "SyG", "Sy1", "Sy2",
                "AGN", "LIN", "QSO", "Bla", "BLL", "rG", "LeG", "LeQ"}
TYPE_LABEL = {"QSO": "Quasar", "Bla": "Blazar", "BLL": "BL-Lacertae-Objekt", "AGN": "aktiver Galaxienkern", "Sy1": "Seyfert-Galaxie",
              "Sy2": "Seyfert-Galaxie", "SyG": "Seyfert-Galaxie", "LIN": "LINER-Galaxie", "rG": "Radiogalaxie", "SBG": "Starburst-Galaxie",
              "LeQ": "Gravitationslinse (Quasar)", "LeG": "Gravitationslinse (Galaxie)"}


class DepthError(Exception):
    """Klare Meldung für die Oberfläche (kein Plate-Solving, kein Netz …)."""


# ------------------------------------------------------------- Kataloge -- #
def simbad_redshifts(ra: float, dec: float, radius: float, cache_dir: Path, timeout: float = 40.0) -> list[dict]:
    """Galaxien/Quasare mit Rotverschiebung im Feld (SIMBAD). Gesendet werden nur Feldmitte und Radius."""
    key = hashlib.sha1(f"z2|{ra:.3f}|{dec:.3f}|{radius:.3f}".encode()).hexdigest()[:16]
    f = Path(cache_dir) / f"simbad_z_{key}.json"
    if f.exists():
        return json.loads(f.read_text(encoding="utf-8"))
    q = (f"SELECT TOP 20000 main_id, ra, dec, otype, rvz_redshift, galdim_majaxis, filter, flux FROM basic "
         f"LEFT JOIN flux ON flux.oidref = basic.oid AND flux.filter IN ('G', 'R', 'r', 'V', 'g') WHERE "
         f"1=CONTAINS(POINT('ICRS', ra, dec), CIRCLE('ICRS', {ra:.5f}, {dec:.5f}, {radius:.4f})) AND rvz_redshift > 0.0003")
    rows, errors = None, []
    for url in SIMBAD_TAP:
        try:
            rows = photometry._tap_csv(url, q, timeout)
            break
        except Exception as e:
            errors.append(f"{url.split('/')[2]}: {e}")
    if rows is None:
        raise RuntimeError("SIMBAD nicht erreichbar (" + "; ".join(errors)[:160] + ")")
    out: dict[str, dict] = {}
    pref = {"G": 0, "R": 1, "r": 2, "V": 3, "g": 4}
    for r in rows:                                   # je Objekt eine Zeile je Filter – bevorzugt Gaia G, sonst R/r/V/g
        try:
            o = out.setdefault(r[0], {"name": " ".join(r[0].split()), "ra": float(r[1]), "dec": float(r[2]), "otype": r[3].strip(),
                                      "z": float(r[4]), "size": float(r[5]) if r[5] else None, "mag": None, "_f": 9})
            if r[6] in pref and r[7] and pref[r[6]] < o["_f"]:
                o["mag"], o["_f"] = round(float(r[7]), 2), pref[r[6]]
        except (ValueError, IndexError):
            continue
    out = [{k: v for k, v in o.items() if k != "_f"} for o in out.values()]
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(out), encoding="utf-8")
    return out


def lookback(z: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Rotverschiebung → (Lichtlaufzeit in Jahren, heutige mitbewegte Entfernung in Lichtjahren), Planck 2018."""
    from astropy.cosmology import Planck18

    z = np.asarray(z, dtype=float)
    t = Planck18.lookback_time(z).to("yr").value
    d = Planck18.comoving_distance(z).to("lyr").value
    return t, d


# --------------------------------------------------------- Fotometrie -- #
def _ring_phot(L: np.ndarray, x: np.ndarray, y: np.ndarray, r: float):
    """Aperturfluss (Ring-Median als Hintergrund), Ring-Streuung und Schwerpunktversatz je Position."""
    H, W = L.shape
    ri = int(math.ceil(r * 3.2))
    yy, xx = np.mgrid[-ri: ri + 1, -ri: ri + 1]
    rr = np.hypot(yy, xx)
    ap, ann = rr <= r, (rr > r * 2.0) & (rr <= r * 3.2)
    n_ap = int(ap.sum())
    flux = np.full(len(x), np.nan)
    sig = np.full(len(x), np.nan)
    off = np.full(len(x), np.nan)
    peak = np.full(len(x), np.nan)
    xi, yi = np.round(x).astype(int), np.round(y).astype(int)
    for i in range(len(x)):
        cx, cy = xi[i], yi[i]
        if cx < ri or cy < ri or cx >= W - ri or cy >= H - ri:
            continue
        cut = L[cy - ri: cy + ri + 1, cx - ri: cx + ri + 1]
        ring = cut[ann]
        bg = np.median(ring)
        a = cut[ap] - bg
        flux[i] = a.sum()
        sig[i] = 1.4826 * np.median(np.abs(ring - bg))
        peak[i] = cut[ap].max()
        w = np.clip(a, 0, None)
        s = w.sum()
        if s > 0:
            off[i] = math.hypot((w * (xx[ap] + cx - x[i])).sum() / s, (w * (yy[ap] + cy - y[i])).sum() / s)
    return flux, sig, off, peak, n_ap


def _empty_sky(L, valid, cat_x, cat_y, r, n=1500, seed=1):
    """Aperturrauschen an leeren Stellen (abseits aller Katalogsterne) – berücksichtigt korreliertes Rauschen."""
    from scipy.spatial import cKDTree

    H, W = L.shape
    rng = np.random.default_rng(seed)
    m = int(math.ceil(r * 3.2)) + 2
    x = rng.uniform(m, W - m, n * 4)
    y = rng.uniform(m, H - m, n * 4)
    ok = valid[y.astype(int), x.astype(int)]
    if len(cat_x):
        d, _ = cKDTree(np.c_[cat_x, cat_y]).query(np.c_[x, y])
        ok &= d > 4 * r
    x, y = x[ok][:n], y[ok][:n]
    if len(x) < 50:
        return None
    f, s, _, _, _ = _ring_phot(L, x, y, r)
    f, s = f[np.isfinite(f)], s[np.isfinite(s)]
    sig_ap = 1.4826 * np.median(np.abs(f - np.median(f)))
    return float(sig_ap), float(np.median(s))


def _fwhm(L: np.ndarray) -> float:
    from .quality import detect_stars

    h, w = L.shape
    cy, cx = h // 2, w // 2
    sub = L[max(0, cy - 900): cy + 900, max(0, cx - 1200): cx + 1200]       # Mitte genügt, spart Zeit
    st = detect_stars(np.nan_to_num(sub), max_stars=300, thresh=8)
    return float(np.median(st.fwhm)) if len(st) >= 10 else 3.0


# ------------------------------------------------------------ Auswertung -- #
def compute(work_dir: Path, pid: int, files: dict, name: str = "", facts=None, gaia: bool = True, simbad: bool = True,
            stats: dict | None = None, log=lambda m: None) -> dict:
    """Kennzahlen zur Bildtiefe einer Version. Wirft DepthError mit verständlicher Meldung."""
    geo = services.view_geometry(work_dir, pid, files, need_stack=True)
    if geo is None:
        raise DepthError("Dieses Bild ist nicht per Plate-Solving gelöst – ohne Himmelskoordinaten lässt sich die Tiefe nicht messen.")
    if not gaia:
        raise DepthError("Gaia-Abfragen sind ausgeschaltet (Einstellungen → Bilder → Fotometrische Farbkalibrierung).")
    key = geo.get("key", "")
    if "|" in key and key.split("|")[1] != f"{geo['H']}x{geo['W']}":
        raise DepthError("Die gespeicherte Plate-Solving-Lösung gehört zu einem anderen Stack – bitte neu entwickeln.")
    data, wcs, H, W = geo["data"], geo["wcs"], geo["H"], geo["W"]
    L = data.mean(0).astype(np.float32)
    valid = np.isfinite(L) & (L != 0)
    from scipy import ndimage as ndi
    valid = ndi.binary_erosion(valid, iterations=12, border_value=0)
    if valid.mean() < 0.2:
        raise DepthError("Zu wenig gültige Bildfläche im Stack.")
    L = np.where(np.isfinite(L), L, 0)
    notes = []
    log("Sternschärfe messen")
    fwhm = _fwhm(L)
    r = max(2.0, 0.8 * fwhm)                         # Apertur nahe am SNR-Optimum für schwache Sterne

    ra0, dec0 = (float(v) for v in wcs.pixel_to_world_values((W - 1) / 2, (H - 1) / 2))
    scale = float(np.sqrt(abs(np.linalg.det(wcs.pixel_scale_matrix))))           # Grad/Pixel
    radius = 0.5 * scale * math.hypot(W, H)
    log("Gaia-Sterne im Feld abfragen")
    try:
        cat = photometry.gaia_field(ra0, dec0, radius, work_dir / "gaia")
    except Exception as e:
        raise DepthError(f"Gaia-Katalog nicht erreichbar – ohne Netz keine Tiefenmessung ({str(e)[:120]}).")
    px, py = wcs.world_to_pixel_values(cat["ra"], cat["dec"])
    inside = np.isfinite(px) & np.isfinite(py) & (px >= 0) & (py >= 0) & (px < W) & (py < H)
    inside[inside] = valid[py[inside].astype(int), px[inside].astype(int)]
    idx = np.nonzero(inside)[0]
    if len(idx) < 20:
        raise DepthError("Zu wenige Katalogsterne im Bildfeld – passt die Plate-Solving-Lösung?")
    g, plx, poe = cat["g"][idx], cat["parallax"][idx], cat["poe"][idx]
    x, y = px[idx], py[idx]
    log(f"{len(idx)} Katalogsterne messen")
    flux, sig_ring, off, peak, n_ap = _ring_phot(L, x, y, r)
    noise = _empty_sky(L, valid, x, y, r)
    if noise is None:
        raise DepthError("Kein freier Himmel für die Rauschmessung gefunden.")
    sig_ap, sig_ring_med = noise
    # Rauschen je Stern: leerer Himmel × örtliches Verhältnis (Nebel, Galaxienscheibe, Randbereiche rauschen stärker)
    local = np.clip(np.nan_to_num(sig_ring / max(sig_ring_med, 1e-12), nan=1.0), 0.7, 5.0)
    snr = flux / (sig_ap * local)
    # Nachbarn: ein hellerer Stern in Apertur oder Ring verfälscht die Messung
    from scipy.spatial import cKDTree
    tree = cKDTree(np.c_[x, y])
    blended = np.zeros(len(x), bool)
    for i, nb in enumerate(tree.query_ball_point(np.c_[x, y], r * 3.2)):
        blended[i] = any(j != i and g[j] < g[i] + 1.0 for j in nb)
    top = np.nanpercentile(L[valid], 99.99)
    good_pos = np.nan_to_num(off, nan=99) < max(1.5, 0.6 * fwhm)
    det = (snr >= SNR_MIN) & good_pos & ~blended
    meas = ~blended                                 # sinnvoll messbar (für die Vollständigkeit)

    # Nullpunkt: helle, ungesättigte, freistehende Sterne
    zp_sel = det & (snr > 30) & (peak < 0.7 * top)
    zp = float(np.median(g[zp_sel] + 2.5 * np.log10(flux[zp_sel]))) if zp_sel.sum() >= 10 else None
    if zp is not None:
        # gemessene Helligkeit muss zum Katalog passen – sonst misst die Apertur Nebel, Galaxienknoten oder Nachbarlicht
        with np.errstate(invalid="ignore", divide="ignore"):
            m_meas = zp - 2.5 * np.log10(np.clip(flux, 1e-12, None))
        det &= np.abs(m_meas - g) < MAG_TOL
    m5 = round(zp - 2.5 * math.log10(SNR_MIN * sig_ap), 2) if zp is not None and sig_ap > 0 else None

    # Vollständigkeit je Größenklasse
    bins = []
    for m in range(int(np.floor(np.nanmin(g))), 22):
        sel = meas & (g >= m) & (g < m + 1)
        n = int(sel.sum())
        if n:
            bins.append({"mag": m, "katalog": n, "erfasst": int((det & sel).sum())})
    lim50 = None
    for b in bins:                                   # schwächste Klasse, bis zu der mindestens die Hälfte erfasst ist
        if b["katalog"] >= 5 and b["erfasst"] / b["katalog"] >= 0.5:
            lim50 = b["mag"] + 1
        elif b["katalog"] >= 5 and lim50 is not None:
            break
    pos = lambda i: _pos(geo, x[i], y[i])
    star = lambda i, **k: {"id": f"Gaia DR3 {cat['source_id'][idx[i]]}", "g": round(float(g[i]), 2), "snr": round(float(snr[i]), 1), **pos(i), **k}
    di = np.nonzero(det)[0]
    faint = int(di[np.argmax(g[di])]) if len(di) else None
    limit = {"fwhm_px": round(fwhm, 2), "apertur_px": round(r, 2), "m5": m5, "lim50": lim50, "nullpunkt": zp and round(zp, 2),
             "erfasst": int(det.sum()), "katalog": int(len(idx)), "klassen": bins,
             "schwaechster": star(faint) if faint is not None else None,
             "katalog_bis": round(float(np.nanmax(cat["g"])), 2) if cat["truncated"] else None}
    if cat["truncated"]:
        notes.append(f"Sehr sternreiches Feld: Katalog nur bis G {limit['katalog_bis']:.1f} abgefragt – die Grenzgröße kann tiefer liegen.")
    if (stats or {}).get("graxpert", {}).get("denoise"):
        notes.append("Der Stack wurde KI-entrauscht (GraXpert) – Rauschen und damit die Grenzgröße wirken etwas günstiger.")

    # Entfernungen der erfassten Sterne
    pc = 1000.0 / (plx - PLX_ZERO)
    ok = det & np.isfinite(poe) & (poe >= POE_MIN) & (plx > 0)
    ly = np.where(ok, pc * LY_PER_PC, np.nan)
    oi = np.nonzero(ok)[0]
    dist = {"mit_parallaxe": int(len(oi))}
    if len(oi):
        near, far = int(oi[np.nanargmin(ly[oi])]), int(oi[np.nanargmax(ly[oi])])
        dist.update(naechster=star(near, ly=round(float(ly[near]), 1)), fernster=star(far, ly=round(float(ly[far]), 0)),
                    median_ly=round(float(np.median(ly[oi])), 0),
                    verteilung=[{"bis": e, "n": int(((ly[oi] >= a) & (ly[oi] < e)).sum())} for a, e in zip(DIST_EDGES, DIST_EDGES[1:])])

    # Hauptobjekt
    main = None
    if facts:
        try:
            f = facts(name)
        except Exception:
            f = None
        if f and f.get("distance_ly"):
            main = {"name": f.get("name") or name, "ly": float(f["distance_ly"]), "quelle": "Sage-Wissensbasis", "text": f.get("distance")}

    # Galaxien und Quasare mit Rotverschiebung
    gal, gal_err = [], None
    if simbad:
        log("Galaxien im Feld abfragen (SIMBAD)")
        try:
            gal = _galaxies(simbad_redshifts(ra0, dec0, radius, work_dir / "simbad"), wcs, valid, L, r, sig_ap, sig_ring_med,
                            fwhm, x, y, poe, geo, zp, m5)
        except Exception as e:
            gal_err = f"SIMBAD nicht erreichbar ({str(e)[:100]})"
    else:
        gal_err = "Online-Abfrage der Rotverschiebungen ist ausgeschaltet"
    _mark_members(gal, name)
    if main is None and name:
        for o in gal:                                 # Hauptobjekt ohne Sage-Wert: grob aus der Rotverschiebung
            if o["sichtbar"] and o["z"] < 0.01 and _same_name(o["name"], name):
                main = {"name": name, "ly": o["ly"], "quelle": "Rotverschiebung (bei nahen Galaxien ungenau)"}
    seen = [o for o in gal if o["sichtbar"] and not o.get("teil")]
    far_obj = max(seen, key=lambda o: o["z"]) if seen else None

    # Punkte für den 3D-Tunnel (Bildposition 0…1, Entfernung in Lichtjahren)
    pts = []
    order = oi[np.argsort(g[oi])][:MAX_POINTS]
    for i in order:
        p = pos(i)
        if p.get("x") is not None:
            pts.append({"t": "s", "x": p["x"], "y": p["y"], "ly": round(float(ly[i]), 1), "g": round(float(g[i]), 1),
                        "bp_rp": None if not np.isfinite(cat["bp_rp"][idx[i]]) else round(float(cat["bp_rp"][idx[i]]), 2),
                        "n": f"Gaia DR3 {cat['source_id'][idx[i]]}"})
    for o in seen:
        if o.get("x") is not None:
            pts.append({"t": "g", "x": o["x"], "y": o["y"], "ly": o["ly"], "n": o["name"], "z": o["z"], "art": o["art"]})
    v = geo["view"]
    return {"v": DEPTH_VERSION, "ok": True, "grenze": limit, "entfernung": dist, "hauptobjekt": main,
            "galaxien": sorted(gal, key=lambda o: (not o["sichtbar"], -o["z"]))[:200], "galaxien_fehler": gal_err,
            "galaxien_sichtbar": len(seen), "teile": sum(1 for o in gal if o.get("teil") and o["sichtbar"]), "fernstes": far_obj, "punkte": pts, "hinweise": notes,
            "bild": {"w": v[0], "h": v[1]} if geo["offset"] is not None else None,
            "feld": {"ra": round(ra0, 4), "dec": round(dec0, 4), "radius": round(radius, 3), "skala": round(scale * 3600, 3)}}


def _mark_members(gal: list[dict], name: str) -> None:
    """Einträge, die zum Hauptobjekt gehören (HII-Regionen, Sternhaufen einer nahen Galaxie mit deren Rotverschiebung,
    z. B. NGC 5461 in M101), als `teil` markieren – sie sind keine eigenen Galaxien im Hintergrund."""
    from .objects import ang_sep, resolve_name

    c = resolve_name(name) if name else None
    if not c or c.kind != "galaxy":
        return
    rad = max(c.size, 2.0) / 60 * 0.6                 # Grad, etwas mehr als der halbe Katalogdurchmesser
    zs = [o["z"] for o in gal if _same_name(o["name"], name)]
    z0 = zs[0] if zs else None
    for o in gal:
        if o["z"] < 0.01 and ang_sep(c.ra, c.dec, o["ra"], o["dec"]) < rad and (z0 is None or abs(o["z"] - z0) < 0.002):
            o["teil"] = True


def _same_name(a: str, b: str) -> bool:
    from .objects import normalize_name
    na, nb = normalize_name(a), normalize_name(b)
    return bool(na) and (na == nb or nb.startswith(na) or na.startswith(nb))


def _pos(geo, x, y) -> dict:
    v = services.to_view(geo, x, y)
    if v is None:
        return {"x": None, "y": None}
    return {"x": round(float(v[0]), 1), "y": round(float(v[1]), 1)}


def _galaxies(rows, wcs, valid, L, r, sig_ap, sig_ring_med, fwhm, sx, sy, poe, geo, zp=None, m5=None) -> list[dict]:
    """SIMBAD-Objekte mit Rotverschiebung im Bild; sichtbar = SNR ≥ 5 an der Position, Lichtschwerpunkt dort, kein
    Vordergrundstern davor und – mit Katalog-Helligkeit – nicht schwächer als die Bildgrenze bzw. nicht viel heller gemessen."""
    rows = [o for o in rows if o["otype"] in GALAXY_TYPES]
    if not rows:
        return []
    from scipy.spatial import cKDTree

    ra = np.array([o["ra"] for o in rows])
    dec = np.array([o["dec"] for o in rows])
    x, y = wcs.world_to_pixel_values(ra, dec)
    H, W = L.shape
    ins = np.isfinite(x) & (x >= 0) & (y >= 0) & (x < W) & (y < H)
    ins[ins] = valid[y[ins].astype(int), x[ins].astype(int)]
    k = np.nonzero(ins)[0]
    if not len(k):
        return []
    ra_ = 1.5 * r                                  # etwas größer als bei Sternen: Galaxienkerne sind breiter
    f, s, off, _, _ = _ring_phot(L, x[k], y[k], ra_)
    snr = f / (sig_ap * (ra_ / r) * np.clip(np.nan_to_num(s / max(sig_ring_med, 1e-12), nan=1), 0.7, 5))
    # Vordergrundstern (echte Parallaxe) näher als 1,5 FWHM: die Helligkeit dort gehört dem Stern
    stars = np.isfinite(poe) & (poe >= 3)
    fg = np.zeros(len(k), bool)
    if stars.any():
        d, _ = cKDTree(np.c_[sx[stars], sy[stars]]).query(np.c_[x[k], y[k]])
        fg = d < 1.5 * fwhm
    t, dc = lookback([rows[i]["z"] for i in k])
    out = []
    for j, i in enumerate(k):
        o = rows[i]
        vis = bool(snr[j] >= SNR_MIN and not fg[j] and np.nan_to_num(off[j], nan=99) < max(2.0, 0.8 * fwhm))
        mag = o.get("mag")
        if vis and mag is not None and zp is not None:
            m_meas = zp - 2.5 * math.log10(max(f[j], 1e-12))
            # zu schwach für dieses Bild oder deutlich heller gemessen (Nachbarlicht) → nicht „gesehen“
            vis = mag <= (m5 or 99) + 1.0 and m_meas > mag - 1.0
        out.append({"name": o["name"], "art": TYPE_LABEL.get(o["otype"], "Galaxie"), "otype": o["otype"], "z": round(o["z"], 5),
                    "ra": round(o["ra"], 5), "dec": round(o["dec"], 5),
                    "ly": float(f"{t[j]:.3g}"), "heute_ly": float(f"{dc[j]:.3g}"), "snr": None if not np.isfinite(snr[j]) else round(float(snr[j]), 1),
                    "mag": mag, "sichtbar": vis, "vordergrund": bool(fg[j]), **_pos(geo, x[i], y[i])})
    return out
