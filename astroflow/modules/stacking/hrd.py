"""HR-Diagramm (Farben-Helligkeits-Diagramm) von Sternhaufen aus dem eigenen Bild (Wunsch 9, seit 0.39.0).

Grundlage wie bei „Wie tief schaust du?“ (`depth.py`): Plate-Solving-Lösung des Master-Stacks + linearer Stack + die gemeinsame
Gaia-Schicht (`photometry.gaia_field`, jetzt mit Parallaxenfehler und Eigenbewegung – keine zweite Abfrage).
1. **Eigene Fotometrie:** erzwungene Aperturfotometrie an allen Gaia-Positionen, getrennt in R, G, B und der Luminanz.
   Instrumentenfarbe −2,5·log(B/R) wird robust gegen Gaia BP−RP geeicht (Gerade), die Helligkeit gegen Gaia G mit Farbterm.
   Die Werte im Diagramm sind also *eigene* Messungen auf der Gaia-Skala – Gaia selbst liegt als graue Wolke dahinter.
2. **Haufenmitglieder** allein aus der Gaia-Astrometrie: Häufung in der Eigenbewegung nahe der Haufenmitte (Dichte gegen das
   Umfeld), dann passende Parallaxe. Die Verunreinigung durch Feldsterne wird aus dem Umfeld hochgerechnet.
3. **Deutung (vorsichtig):** Entfernung aus der mittleren Parallaxe (Nullpunkt −0,017 mas, systematisch ±0,015 mas),
   Rötung über eine genäherte Hauptreihe (sonnennahe Metallizität), Abknickpunkt = obere Hauptreihe, Alter grob aus dessen
   absoluter Helligkeit (Faktor ~2 unsicher). Kugelsternhaufen: keine Altersschätzung (metallarm, Abknickpunkt meist zu schwach).
"""
from __future__ import annotations

import math

import numpy as np

from . import photometry, services, stackfix
from .depth import LY_PER_PC, PLX_ZERO, _empty_sky, _fwhm, _near_spots, _pos, _ring_phot

HRD_VERSION = 1
SNR_MIN = 10.0                     # Luminanz: darunter ist die Farbe zu unsicher
MIN_CAL = 15                       # Sterne für die Eichung
MIN_MEMBERS = 15                   # gemessene Mitglieder, ab denen das Diagramm etwas zeigt
PLX_SYS = 0.015                    # mas, systematische Unsicherheit der Gaia-Parallaxen auf kleinen Feldern
A_G_PER_E = 1.89                   # A_G / E(BP−RP), mittlere Sterne
MAX_MEMBERS, MAX_FIELD, MAX_GAIA = 1500, 900, 3000
CLUSTER_KINDS = ("open_cluster", "globular")
CLUSTER_NAMES = {"M45", "PLEIADES", "PLEJADEN", "NGC869", "NGC884", "HCHIPERSEI"}   # Haufen, die der kleine Katalog anders führt

# Genäherte Hauptreihe (Zwerge, sonnennahe Metallizität): (BP−RP)₀ → M_G, nach Pecaut & Mamajek (2013, Fassung 2022)
MS = np.array([(-0.30, -2.9), (-0.24, -1.9), (-0.16, -0.9), (-0.09, -0.1), (0.00, 1.1), (0.21, 1.9), (0.42, 2.5), (0.59, 3.3),
               (0.77, 4.2), (0.82, 4.6), (0.98, 5.6), (1.43, 6.9), (1.84, 8.2), (2.25, 9.3), (2.60, 10.3)])
# Abknickpunkt (absolute G-Helligkeit) → log10(Alter/Jahre), grob aus Isochronen sonnennaher Metallizität
TURNOFF_AGE = np.array([(-4.0, 7.0), (-2.5, 7.4), (-1.5, 7.7), (-0.8, 8.05), (0.5, 8.5), (1.3, 8.8), (2.4, 9.2), (3.4, 9.6), (4.0, 10.0)])


class HrdError(Exception):
    """Klare Meldung für die Oberfläche."""


def dra(ra, ra0):
    """Rektaszensionsdifferenz auf den kürzesten Bogen (−180…180°) – Felder über RA 0°/360°."""
    return (np.asarray(ra, float) - ra0 + 180.0) % 360.0 - 180.0


def is_cluster(kind: str | None, name: str | None = "") -> bool:
    from .objects import normalize_name
    return kind in CLUSTER_KINDS or normalize_name(name or "") in CLUSTER_NAMES or normalize_name((name or "").split()[0] if name else "") in CLUSTER_NAMES


def ms_mag(c0) -> np.ndarray:
    """Absolute G-Helligkeit der genäherten Hauptreihe bei Eigenfarbe c0 (außerhalb des Bereichs NaN)."""
    c0 = np.asarray(c0, float)
    m = np.interp(c0, MS[:, 0], MS[:, 1])
    return np.where((c0 >= MS[0, 0]) & (c0 <= MS[-1, 0]), m, np.nan)


def age_from_turnoff(mg: float) -> float:
    return float(np.interp(mg, TURNOFF_AGE[:, 0], TURNOFF_AGE[:, 1]))


def _robust_line(x, y, n_min=MIN_CAL):
    """Gerade y = a·x + b mit iterativem 3σ-Verwerfen (Doppelsterne, Veränderliche, Mischlicht). Rückgabe a, b, Streuung, Auswahl."""
    use = np.isfinite(x) & np.isfinite(y)
    a = b = s = None
    for _ in range(6):
        if use.sum() < n_min:
            return None
        a, b = np.polyfit(x[use], y[use], 1)
        res = y - (a * x + b)
        s = 1.4826 * np.median(np.abs(res[use])) + 1e-6
        new = np.isfinite(res) & (np.abs(res) < 3 * s)
        if (new == use).all():
            break
        use = new
    return float(a), float(b), float(s), use


# --------------------------------------------------------------- Mitglieder -- #
def members(ra, dec, pmra, pmdec, plx, plx_err, g, c_ra, c_dec, r_in, areas=None) -> dict:
    """Haufenmitglieder aus Eigenbewegung und Parallaxe. r_in = Haufenradius (Grad), areas = (Fläche innen, Fläche Umfeld)
    in Grad² innerhalb des Bildes (sonst als Kreis genähert). Rückgabe: Maske `mitglied` über alle Sterne + Kenngrößen;
    `ok` False, wenn keine Häufung erkennbar ist."""
    from scipy.spatial import cKDTree

    n = len(ra)
    cosd = math.cos(math.radians(c_dec))
    sep = np.hypot(dra(ra, c_ra) * cosd, dec - c_dec)
    usable = np.isfinite(pmra) & np.isfinite(pmdec) & np.isfinite(plx) & np.isfinite(plx_err) & (g < 19.5)
    inner = usable & (sep < r_in)
    outer = usable & (sep > 1.5 * r_in)
    r_max = float(np.nanmax(sep[usable])) if usable.any() else 0.0
    # Flächen (Grad²) für die Hochrechnung der Feldsterne – Umfeld nur, wenn es nennenswert groß ist
    a_in, a_out = areas or (math.pi * min(r_in, r_max) ** 2, max(0.0, math.pi * (r_max ** 2 - (1.5 * r_in) ** 2)))
    out = {"ok": False, "mitglied": np.zeros(n, bool), "n_innen": int(inner.sum())}
    if inner.sum() < 10:
        return out
    pm = np.c_[pmra, pmdec]
    ti = cKDTree(pm[inner])
    dens = np.array([len(x) for x in ti.query_ball_point(pm[inner], 1.0)], float)
    if outer.sum() >= 20 and a_out > 0.2 * a_in:
        to = cKDTree(pm[outer])
        dens -= np.array([len(x) for x in to.query_ball_point(pm[inner], 1.0)], float) * a_in / a_out
    c = pm[inner][int(np.argmax(dens))]
    w = 2.0
    for _ in range(6):                               # Mitte und Fensterbreite in der Eigenbewegung verfeinern
        d = np.hypot(*(pm[inner] - c).T)
        sel = d < w
        if sel.sum() < 5:
            return out
        c = np.median(pm[inner][sel], axis=0)
        s1 = 1.4826 * np.median(np.abs(pm[inner][sel] - c)) + 1e-3          # robuste Streuung je Achse
        w = float(np.clip(3.0 * s1 * 1.18, 0.4, 5.0))                         # ~2,5σ-Radius in 2D
    dpm = np.hypot(*(pm - c).T)
    pm_ok = usable & (dpm < w)
    core = pm_ok & inner & (plx_err > 0)
    if core.sum() < 5:
        return out
    wts = 1 / plx_err[core] ** 2
    p0 = float(np.sum(plx[core] * wts) / wts.sum())
    for _ in range(3):
        tol = 3 * np.sqrt(plx_err ** 2 + (0.06 * max(p0, 0)) ** 2 + 0.03 ** 2)
        sel = core & (np.abs(plx - p0) < tol)
        if sel.sum() < 5:
            break
        wts = 1 / plx_err[sel] ** 2
        p0 = float(np.sum(plx[sel] * wts) / wts.sum())
    tol = 3 * np.sqrt(np.nan_to_num(plx_err, nan=9) ** 2 + (0.06 * max(p0, 0)) ** 2 + 0.03 ** 2)
    # Mittel und Unsicherheit aus derselben Auswahl (Kernsterne mit passender Parallaxe) – äußere Mitglieder gehen nicht ein;
    # Unsicherheit mindestens die Streuung der Auswahl / √n (Haufentiefe, unterschätzte Fehler)
    fit = core & (np.abs(np.nan_to_num(plx, nan=-99) - p0) < tol)
    if fit.sum() < 5:
        fit = core
    wts = 1 / plx_err[fit] ** 2
    p0 = float(np.sum(plx[fit] * wts) / wts.sum())
    p_err = float(max(math.sqrt(1 / wts.sum()), 1.4826 * np.median(np.abs(plx[fit] - p0)) / math.sqrt(fit.sum())))
    mem = pm_ok & (np.abs(np.nan_to_num(plx, nan=-99) - p0) < tol) & (sep < max(2.0 * r_in, 1e-6))
    # Feldsterne mit zufällig passender Bewegung: aus dem Umfeld je Fläche hochgerechnet
    contam = None
    n_in = int((mem & inner).sum())
    if outer.sum() >= 20 and a_out > 0.2 * a_in and n_in:
        per_area = (pm_ok & outer & (np.abs(np.nan_to_num(plx, nan=-99) - p0) < tol)).sum() / a_out
        contam = float(min(1.0, per_area * a_in / n_in))
    signif = n_in >= 8 and (contam is None or contam < 0.5)
    return {"ok": bool(signif), "mitglied": mem, "pm": [round(float(c[0]), 3), round(float(c[1]), 3)], "pm_radius": round(w, 2),
            "plx": round(p0, 4), "plx_err": round(p_err, 4), "n_parallaxe": int(fit.sum()), "n_innen": int(inner.sum()), "verunreinigung": contam and round(contam, 2)}


# ----------------------------------------------------------------- Deutung -- #
def estimate(c, g, mu: float | None, mu_err: float | None, globular: bool, bright_lim: float, faint_lim: float,
             sat_g=None) -> dict:
    """Rötung, Hauptreihe, Abknickpunkt und grobes Alter aus Farbe c (BP−RP) und Helligkeit g der Mitglieder. sat_g: Gaia-G der
    Mitglieder, die im Bild übersättigt sind – liegen sie über dem Abknickpunkt, ist das Alter nur eine Obergrenze."""
    out = {"roetung": None, "hauptreihe": None, "abknick": None, "alter": None}
    if mu is None or len(c) < MIN_MEMBERS:
        return out
    Mg = g - mu
    if globular:
        # metallarme Hauptreihe passt nicht zur genäherten Hauptreihe – nur der erwartete Abknickpunkt (M_G ≈ 3,8, ohne Rötung)
        out["abknick"] = {"g": round(mu + 3.8, 2), "erwartet": True, "MG": 3.8,
                          "sichtbar": bool(mu + 3.8 < faint_lim)}
        return out
    best, loss = None, None
    for E in np.arange(0.0, 1.51, 0.01):
        res = Mg - A_G_PER_E * E - ms_mag(c - E)
        ok = np.isfinite(res)
        if ok.sum() < 8:
            continue
        L = np.minimum(np.abs(res[ok]), 0.8).mean() + 0.8 * (1 - ok.mean())     # Sterne außerhalb der Tabelle zählen voll
        if loss is None or L < loss:
            best, loss = float(E), float(L)
    if best is None:
        return out
    E = best
    res = Mg - A_G_PER_E * E - ms_mag(c - E)
    on_ms = np.isfinite(res) & (res > -1.0) & (res < 0.6)          # bis 1 mag darüber: Doppelsterne, beginnende Entwicklung
    if on_ms.sum() < 8:
        return out
    # Unsicherheit der Rötung: Bereich, in dem der Fehler kaum schlechter ist
    tol = []
    for E2 in np.arange(0.0, 1.51, 0.01):
        r2 = Mg - A_G_PER_E * E2 - ms_mag(c - E2)
        ok = np.isfinite(r2)
        if ok.sum() >= 8 and np.minimum(np.abs(r2[ok]), 0.8).mean() + 0.8 * (1 - ok.mean()) < loss + 0.03:
            tol.append(E2)
    out["roetung"] = {"E": round(E, 2), "A_G": round(A_G_PER_E * E, 2), "E_lo": round(min(tol), 2), "E_hi": round(max(tol), 2)}
    cs = np.linspace(MS[0, 0], MS[-1, 0], 40)
    line = [[round(float(x + E), 3), round(float(m + mu + A_G_PER_E * E), 2)] for x, m in zip(cs, ms_mag(cs))]
    out["hauptreihe"] = [p for p in line if bright_lim - 1.5 <= p[1] <= faint_lim + 0.5]
    # Abknickpunkt = obere Hauptreihe: Median der 2.–4. hellsten Hauptreihensterne (ein einzelner Blauer Nachzügler zählt nicht)
    k = np.nonzero(on_ms)[0][np.argsort(g[on_ms])]
    top = k[1:4] if len(k) >= 4 else k[:3]
    g_to, c_to = float(np.median(g[top])), float(np.median(c[top]))
    mg_to = g_to - mu - A_G_PER_E * E
    edge = "hell" if sat_g is not None and len(sat_g) and np.min(sat_g) < g_to else None   # hellere Mitglieder übersättigt
    out["abknick"] = {"g": round(g_to, 2), "c": round(c_to, 3), "MG": round(mg_to, 2), "c0": round(c_to - E, 3), "grenze": edge}
    lg = age_from_turnoff(mg_to)
    spread = math.hypot(0.5, mu_err or 0, A_G_PER_E * (out["roetung"]["E_hi"] - out["roetung"]["E_lo"]) / 2)
    lo, hi = age_from_turnoff(mg_to - spread) - 0.15, age_from_turnoff(mg_to + spread) + 0.15
    out["alter"] = {"log": round(lg, 2), "jahre": float(f"{10 ** lg:.2g}"), "lo": float(f"{10 ** lo:.2g}"), "hi": float(f"{10 ** hi:.2g}"),
                    "hoechstens": edge == "hell"}
    return out


# -------------------------------------------------------------- Auswertung -- #
def compute(work_dir, pid: int, files: dict, name: str = "", kind: str | None = None, gaia: bool = True, log=lambda m: None) -> dict:
    from scipy import ndimage as ndi
    from scipy.spatial import cKDTree

    from .objects import resolve_extended, resolve_name

    geo = services.view_geometry(work_dir, pid, files, need_stack=True)
    if geo is None:
        raise HrdError("Dieses Bild ist nicht per Plate-Solving gelöst – ohne Himmelskoordinaten lässt sich kein HR-Diagramm erstellen.")
    if not gaia:
        raise HrdError("Gaia-Abfragen sind ausgeschaltet (Einstellungen → Bilder → Fotometrische Farbkalibrierung).")
    if not services.geometry_matches(geo):
        raise HrdError("Die gespeicherte Plate-Solving-Lösung gehört zu einem anderen Stack – bitte neu entwickeln.")
    data, wcs, H, W = geo["data"], geo["wcs"], geo["H"], geo["W"]
    if data.ndim != 3 or data.shape[0] != 3:
        raise HrdError("Für Farben braucht es ein Farbbild – dieser Stack hat nur einen Kanal.")
    data, hotfix, spots = stackfix.spots(data)
    data = np.nan_to_num(data.astype(np.float32, copy=False))
    L = data.mean(0)
    valid = ndi.binary_erosion(np.all(data != 0, axis=0), iterations=12, border_value=0)
    if valid.mean() < 0.2:
        raise HrdError("Zu wenig gültige Bildfläche im Stack.")
    log("Sternschärfe messen")
    fwhm = _fwhm(L)
    r = max(2.0, 1.0 * fwhm)                        # etwas größer als bei der Tiefe: Farben brauchen den ganzen Kern

    ra0, dec0 = (float(v) for v in wcs.pixel_to_world_values((W - 1) / 2, (H - 1) / 2))
    scale = float(np.sqrt(abs(np.linalg.det(wcs.pixel_scale_matrix))))
    radius = 0.5 * scale * math.hypot(W, H)
    log("Gaia-Sterne mit Eigenbewegung abfragen")
    try:
        cat = photometry.gaia_field(ra0, dec0, radius, work_dir / "gaia", need_pm=True)
    except Exception as e:
        raise HrdError(f"Gaia-Katalog nicht erreichbar – ohne Netz kein HR-Diagramm ({str(e)[:120]}).")
    px, py = wcs.world_to_pixel_values(cat["ra"], cat["dec"])
    inside = np.isfinite(px) & np.isfinite(py) & (px >= 0) & (py >= 0) & (px < W) & (py < H)
    inside[inside] = valid[py[inside].astype(int), px[inside].astype(int)]
    idx = np.nonzero(inside)[0]
    if len(idx) < 30:
        raise HrdError("Zu wenige Katalogsterne im Bildfeld – passt die Plate-Solving-Lösung?")
    if not np.isfinite(cat["pmra"][idx]).any():
        raise HrdError("Der Gaia-Auszug enthält keine Eigenbewegungen – bitte erneut messen.")
    g, bprp = cat["g"][idx], cat["bp_rp"][idx]
    x, y = px[idx], py[idx]

    # Haufen: Mitte und Größe aus dem Katalog, sonst Bildmitte
    c = resolve_name(name) or resolve_extended(name) if name else None
    if c is not None and math.hypot(float(dra(c.ra, ra0)) * math.cos(math.radians(dec0)), c.dec - dec0) < radius:
        c_ra, c_dec, r_in = c.ra, c.dec, float(np.clip(c.size / 60 / 2, 0.02, radius * 0.9))
        kind = kind or c.kind
    else:
        c_ra, c_dec, r_in = ra0, dec0, radius * 0.5
    globular = kind == "globular"
    # Flächen innen/Umfeld im Bild (Gleichverteilung über die gültige Fläche) – für die Hochrechnung der Feldsterne
    rng = np.random.default_rng(0)
    vy, vx = np.nonzero(valid[::8, ::8])
    pick = rng.choice(len(vx), min(len(vx), 20000), replace=False)
    sra, sdec = wcs.pixel_to_world_values(vx[pick] * 8.0, vy[pick] * 8.0)
    ssep = np.hypot(dra(sra, c_ra) * math.cos(math.radians(c_dec)), np.asarray(sdec) - c_dec)
    total = float(valid.sum()) * scale ** 2
    areas = (total * float((ssep < r_in).mean()), total * float((ssep > 1.5 * r_in).mean()))
    log("Haufenmitglieder über Gaia-Eigenbewegung und Parallaxe bestimmen")
    mem = members(cat["ra"][idx], cat["dec"][idx], cat["pmra"][idx], cat["pmdec"][idx], cat["parallax"][idx], cat["plx_err"][idx],
                  g, c_ra, c_dec, r_in, areas)

    log(f"{len(idx)} Katalogsterne messen")
    fl, sig_ring, off, peak, _ = _ring_phot(L, x, y, r)
    noise = _empty_sky(L, valid, x, y, r)
    if noise is None:
        raise HrdError("Kein freier Himmel für die Rauschmessung gefunden.")
    sig_ap, sig_med = noise
    local = np.clip(np.nan_to_num(sig_ring / max(sig_med, 1e-12), nan=1.0), 0.7, 5.0)
    snr = fl / (sig_ap * local)
    tree = cKDTree(np.c_[x, y])
    blended = np.zeros(len(x), bool)
    near = r + 1.0 * fwhm                           # Nachbarlicht in der Apertur verfälscht Farbe und Helligkeit
    for i, nb in enumerate(tree.query_ball_point(np.c_[x, y], r * 3.2)):
        for j in nb:
            if j != i and (g[j] < g[i] + 2.0 if math.hypot(x[j] - x[i], y[j] - y[i]) < near else g[j] < g[i] - 1.0):
                blended[i] = True                       # im Ring zählt nur ein deutlich hellerer Stern (Median ist robust)
                break
    good = (snr >= SNR_MIN) & (np.nan_to_num(off, nan=99) < max(1.5, 0.6 * fwhm)) & ~blended \
        & ~_near_spots(spots, x, y, max(2.0, 0.8 * fwhm))
    k = np.nonzero(good)[0]
    log(f"Farben von {len(k)} Sternen messen")
    fc = np.full((3, len(x)), np.nan)
    sc = np.full((3, len(x)), np.nan)
    for ch in range(3):
        f, s, _, _, _ = _ring_phot(data[ch], x[k], y[k], r)
        nz = _empty_sky(data[ch], valid, x, y, r, seed=2 + ch)
        fc[ch, k] = f
        sc[ch, k] = (nz[0] if nz else np.nan) * np.clip(np.nan_to_num(s / max(nz[1] if nz else 1, 1e-12), nan=1), 0.7, 5)
    # Übersättigt: abgeflachter Kern – Spitze im Verhältnis zum Fluss deutlich kleiner als bei hellen, sauberen Sternen
    with np.errstate(invalid="ignore", divide="ignore"):
        ratio = (peak - float(np.median(L[valid]))) / fl
    ref = good & (snr > 50) & (snr < 2000)
    r0 = float(np.nanmedian(ratio[ref])) if ref.sum() >= 10 else None
    sat = (snr > 200) & (ratio < 0.7 * r0) if r0 else np.zeros(len(x), bool)
    good &= (fc[0] > 0) & (fc[2] > 0) & ~sat
    with np.errstate(invalid="ignore", divide="ignore"):
        m_inst = -2.5 * np.log10(np.where(good, fl, np.nan))
        c_inst = -2.5 * np.log10(fc[2] / fc[0])
        ce_inst = 1.0857 * np.hypot(sc[2] / fc[2], sc[0] / fc[0])

    # Eichung: Instrumentenfarbe → BP−RP, dann Instrumentenhelligkeit → G mit Farbterm
    cal = good & (snr > 30) & np.isfinite(bprp) & (bprp > -0.4) & (bprp < 3.5)
    fit = _robust_line(c_inst[cal], bprp[cal])
    if fit is None:
        raise HrdError(f"Nur {int(cal.sum())} helle, freistehende Sterne mit Gaia-Farbe – zu wenig, um die Farben zu eichen.")
    a, b, s_col, use = fit
    col = a * c_inst + b
    fit2 = _robust_line(col[cal], (g - m_inst)[cal])
    if fit2 is None:
        raise HrdError("Die Helligkeiten ließen sich nicht an Gaia eichen.")
    kc, zp, s_mag, _ = fit2
    mag = m_inst + zp + kc * col
    col_err = np.abs(a) * ce_inst
    mag_err = 1.0857 / np.clip(snr, 1e-3, None)
    meas = good & np.isfinite(col) & np.isfinite(mag)

    mm = mem["mitglied"] & meas
    hinweise = []
    if hotfix.get("n"):
        hinweise.append(f"{hotfix['n']} Hotpixel-Punkte des Sensors vor der Messung entfernt.")
    if cat["truncated"]:
        hinweise.append(f"Sehr sternreiches Feld: Gaia nur bis G {float(np.nanmax(cat['g'])):.1f} abgefragt.")
    if mem.get("verunreinigung") is not None and mem["verunreinigung"] > 0.2:
        hinweise.append(f"Etwa {round(mem['verunreinigung'] * 100)} % der Mitglieder könnten Feldsterne mit zufällig passender Bewegung sein.")

    # Entfernung aus der mittleren Parallaxe
    dist = None
    if mem.get("plx_err") is not None and mem["ok"]:
        p = mem["plx"] - PLX_ZERO
        pe = math.hypot(mem["plx_err"], PLX_SYS)
        if p > 0:
            pc = 1000 / p
            dist = {"plx": round(p, 4), "plx_err": round(pe, 4), "pc": round(pc, 0), "ly": round(pc * LY_PER_PC, -1 if pc < 3000 else -2),
                    "pc_lo": round(1000 / (p + pe), 0), "pc_hi": round(1000 / (p - pe), 0) if p > pe else None,
                    "mu": round(5 * math.log10(pc) - 5, 2), "mu_err": round(2.1715 * pe / p, 2), "sicher": p / pe >= 5}
            if not dist["sicher"]:
                hinweise.append("Die Parallaxe ist für diesen fernen Haufen nur grob bestimmt – Entfernung und Alter entsprechend unsicher.")
    mi = np.nonzero(mm)[0]
    bright = float(np.nanmin(mag[meas])) if meas.any() else 0.0
    faint = float(np.nanmax(mag[meas])) if meas.any() else 0.0
    est = estimate(col[mi], mag[mi], dist and dist["mu"], dist and dist["mu_err"], globular, bright, faint,
                   sat_g=g[mem["mitglied"] & sat]) if mem["ok"] else estimate([], [], None, None, globular, 0, 0)
    gi = np.nonzero(mem["mitglied"] & np.isfinite(bprp) & np.isfinite(g))[0]
    est_gaia = estimate(bprp[gi], g[gi], dist and dist["mu"], dist and dist["mu_err"], globular,
                        bright, float(np.nanmax(g[gi])) if len(gi) else 0) if mem["ok"] and len(gi) >= MIN_MEMBERS else None
    if globular:
        hinweise.append("Kugelsternhaufen sind sehr alt (meist 10–13 Mrd. Jahre) und arm an schweren Elementen – die genäherte "
                        "Hauptreihe passt nicht, deshalb keine eigene Altersschätzung.")

    pos = lambda i: _pos(geo, x[i], y[i])
    order = mi[np.argsort(mag[mi])][:MAX_MEMBERS]
    pts = [{"c": round(float(col[i]), 3), "g": round(float(mag[i]), 2), "ce": round(float(min(col_err[i], 9)), 3),
            "ge": round(float(mag_err[i]), 3), "gc": None if not np.isfinite(bprp[i]) else round(float(bprp[i]), 3),
            "gg": round(float(g[i]), 2), "id": f"Gaia DR3 {cat['source_id'][idx[i]]}", **pos(i)} for i in order]
    fi = np.nonzero(meas & ~mem["mitglied"])[0]
    fi = fi[:: max(1, len(fi) // MAX_FIELD)][:MAX_FIELD]
    gi = gi[np.argsort(g[gi])][:MAX_GAIA]
    v = geo["view"]
    return {"v": HRD_VERSION, "ok": True, "haufen": {"name": (c.display if c else name) or name, "art": "Kugelsternhaufen" if globular else "Offener Sternhaufen",
                                                     "kugel": globular, "erkannt": bool(mem["ok"]), "radius_bogenmin": round(r_in * 60, 1),
                                                     **{k: mem.get(k) for k in ("pm", "pm_radius", "plx", "verunreinigung")},
                                                     "n_gaia": int(mem["mitglied"].sum()), "n_gemessen": int(mm.sum())},
            "mitglieder": pts, "feld": [[round(float(col[i]), 3), round(float(mag[i]), 2)] for i in fi],
            "gaia": [[round(float(bprp[i]), 3), round(float(g[i]), 2)] for i in gi],
            "eichung": {"n": int(use.sum()), "steigung": round(a, 3), "farbe_streuung": round(s_col, 3), "hell_streuung": round(s_mag, 3),
                        "farbterm": round(kc, 3)},
            "grenzen": {"hell": round(bright, 2), "schwach": round(faint, 2), "snr_min": SNR_MIN},
            "entfernung": dist, **est, "gaia_schaetzung": est_gaia and {k: est_gaia.get(k) for k in ("roetung", "abknick", "alter")},
            "hinweise": hinweise, "fwhm_px": round(fwhm, 2),
            "bild": {"w": v[0], "h": v[1]} if geo["offset"] is not None else None}
