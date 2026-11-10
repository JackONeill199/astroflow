"""AstroFlow Develop Engine: linearer Master-Stack -> fertiges Astrofoto.

Unabhängig von der Stack Engine: arbeitet nur mit dem gespeicherten Master-Stack (mean, cov),
eine erneute Entwicklung braucht also nie ein erneutes Stacking.

Ablauf
  1. measure(): Signal, Rauschen und Dynamik des linearen Stacks messen.
  2. adapt():   Profilwerte (objects.PROFILES) an die Messwerte anpassen.
  3. Qualitätskontrolle: rendern -> assess() -> gezielt nachregeln (deterministisch, max. QC_ROUNDS Renderings).
  4. optional der KI-Prüfer (reviewer.py) obendrauf.

Alles ist reproduzierbar (gleiche Daten -> gleiche Parameter) und verändert nur Helligkeit, Kontrast,
Farbe und Rauschen – es werden keine Strukturen erzeugt.
"""
from __future__ import annotations

import numpy as np
from scipy import ndimage as ndi

from . import processing
from .processing import crop_to_coverage, luminance
from .quality import noise_sigma

QC_ROUNDS = 3
QC_VERSION = 3                 # Stand von assess(); ältere Bewertungen prüft der Tutor am gespeicherten Bild nach
# 3 (10.11.2026): Himmel = dunkelste SKY_PCT % (geglättet, ohne Sterne) statt dunkle Bildhälfte – in Staub-/Nebelfeldern
#   war die dunkle Hälfte Staub; sichtbarer Staub senkte den Score (NGC 7023: 10 → 8) und die Korrekturen drehten ihn zurück
SKY_PCT = 15
PREVIEW_SIDE = 1400

# Zielbereiche der Qualitätskontrolle (Anzeigewerte 0..1 nach der Entwicklung)
# QC_VERSION 3: Schwellen auf den neuen Himmel (dunkelste 15 %) umgerechnet – an 48 Bildern (11 Objekte) gemessen ergab die neue
# Messung im Median 0,77 × Himmelshelligkeit, 1,17 × Rauschen, 0,89 × Farbrauschen gegenüber der dunklen Bildhälfte
BG_RANGE = (0.015, 0.08)       # Himmelshintergrund: dunkel wie bei Stellina, aber nicht abgeschnitten (bis v2: 0,02–0,10)
NOISE_MAX = 0.018              # sichtbares Hintergrundrauschen (σ; bis v2: 0,016)
CAST_MAX = 0.012               # Farbstich des Hintergrunds
CHROMA_NOISE_MAX = 0.0062      # Farbrauschen (Flecken) im Hintergrund (bis v2: 0,007)
GRADIENT_MAX = 0.03            # Helligkeitsunterschied im Hintergrund (Kacheln)
BLACK_CLIP_MAX = 0.005         # Anteil abgeschnittener Schwarzwerte
WHITE_CLIP_MAX = 0.003         # Anteil ausgebrannter Pixel
SAT_RANGE = (0.10, 0.55)       # Farbigkeit des Objekts
# Farbverlauf im Himmel (Ebene durch R−G/B−G der Himmelskacheln, Änderung über die Bildbreite). Gemessen 01.11.2026 an
# allen Ergebnissen: saubere Galaxien/Haufen ≤ 0,010, sichtbar rötlicher Rand NGC 891 0,019, NGC 7318 0,022, M85 0,018.
# In der Milchstraße ist farbiger Himmel oft echter Hα-Nebel (IC 1795 0,020, M103 0,018, NGC 2359 0,030) -> höhere Schwelle.
COLOR_GRAD_MAX = 0.013
COLOR_GRAD_MAX_NEBULA = 0.035
# Ausgebrannter Kern: größte weiße Fläche (L > 0,95) mindestens CORE_MIN_FRAC des Bildes und CORE_STAR_RATIO-mal so groß
# wie typische Sternkerne. Gemessen (Vorschau ~1200 px): NGC 4490 114 px gegen Sterne ~20, M31/M3 ~390 gegen ~10;
# helle Sterne (M45 223 gegen ~140, IC 405 94 gegen ~25) bleiben darunter – Sternkerne dürfen weiß sein.
# 2026-10-30 nachgemessen (Verhältnis zu den 10 nächstgrößten Flächen): M57 24, M63 12, M3 48, M31 90 gegen
# AE Aurigae in IC 405 4,4 (galt bei 4,0 als ausgebrannter Kern) -> 5,0; NGC 4490 (~5,7) bleibt erkannt
CORE_MIN_FRAC = 1e-4
CORE_STAR_RATIO = 5.0
WHITE_STEPS = (99.9, 99.98, 99.995)   # Weißpunkt-Perzentile, die die Kontrolle bei ausgebranntem Kern nacheinander probiert
DECONV_MIN_SNR = 15                   # darunter keine Entfaltung (Objekt-SNR aus measure())


def burnt_core(L: np.ndarray) -> float:
    """Anteil des Bildes, den eine ausgebrannte flächige Region (Galaxien-/Nebelkern) einnimmt; 0 = keine."""
    lab, n = ndi.label(L > 0.95)
    if not n:
        return 0.0
    areas = np.sort(ndi.sum(np.ones_like(L), lab, np.arange(1, n + 1)))[::-1]
    stars = float(np.median(areas[1:11])) if n > 1 else 1.0
    big = float(areas[0])
    return big / L.size if big >= CORE_MIN_FRAC * L.size and big >= CORE_STAR_RATIO * max(stars, 1.0) else 0.0


def _bin(a: np.ndarray, f: int) -> np.ndarray:
    if f <= 1:
        return a
    h, w = a.shape[-2] // f * f, a.shape[-1] // f * f
    a = a[..., :h, :w]
    return a.reshape(*a.shape[:-2], h // f, f, w // f, f).mean((-3, -1))


def preview(mean: np.ndarray, cov: np.ndarray, side: int = PREVIEW_SIDE) -> tuple[np.ndarray, np.ndarray, int]:
    """Verkleinerter Stack (Mittelwert-Binning) für schnelle Messungen."""
    f = max(1, int(np.ceil(max(mean.shape[-2:]) / side)))
    return _bin(mean.astype(np.float32), f), _bin(cov.astype(np.float32), f), f


# ----------------------------------------------------------------- messen #
def measure(mean: np.ndarray, cov: np.ndarray) -> dict:
    """Kennzahlen des linearen Stacks (nach Gradientenentfernung, auf der Vorschau)."""
    img = crop_to_coverage(mean, cov).astype(np.float32)
    img = processing.remove_gradient(img, 2)
    L = img[0] if img.shape[0] == 1 else luminance(img)
    med = float(np.median(L))
    sig = noise_sigma(L)                                  # Pixelrauschen
    sm = ndi.gaussian_filter(L, 2.0)
    sig_sm = 1.4826 * float(np.median(np.abs(sm - np.median(sm)))) + 1e-12
    signal = sm > med + 3 * sig_sm                         # flächiges Signal (Nebel, Galaxie, Sterne)
    stars = sm > med + 60 * sig_sm
    ext = signal & ~ndi.binary_dilation(stars, iterations=3)
    obj = float(np.percentile(sm[ext], 90) - med) if ext.sum() > 50 else float(np.percentile(sm, 99) - med)
    peak = float(np.percentile(L, 99.95) - med)
    return {
        "snr": round(max(obj, 0) / sig, 2),                         # Objekt (helle 10 %) zu Pixelrauschen
        "signal_frac": round(float(ext.mean()), 4),                 # Fläche mit flächigem Signal
        "dynamic": round(peak / max(obj, sig), 1),                  # Sterne/Kerne relativ zum Objekt
    }


def adapt(params: dict, m: dict, mono: bool = False) -> tuple[dict, list[str]]:
    """Startparameter aus Profil + Messwerten. Gibt (params, Begründungen)."""
    p = dict(params)
    why = []
    snr = max(m["snr"], 0.1)
    # Rauschen: schwaches Signal -> stärker entrauschen, starkes Signal -> Details schonen
    f = float(np.clip((12 / snr) ** 0.35, 0.65, 1.5))
    if abs(f - 1) > 0.05:
        p["denoise"] = round(float(np.clip(p["denoise"] * f, 0.2, 0.9)), 3)
        p["chroma_denoise"] = round(float(np.clip(p["chroma_denoise"] * f, 1.0, 5.0)), 2)
        why.append(f"Entrauschen {'verstärkt' if f > 1 else 'reduziert'} (Objekt-SNR {snr:.0f})")
    # lineares Entrauschen vor dem Stretch: holt schwaches Signal aus dem Rauschen (stärker bei wenig Signal)
    # (bei hohem SNR kaum: es hebt sonst auch schwache Restgradienten und Streifen hervor).
    # Es senkt das gemessene Rauschen -> Schwarzpunkt (Median − 2,8σ) rückt an den Himmel, die Kurve streckt härter.
    # Das macht schwache Galaxien/Nebel erst sichtbar (M63, NGC 1579, M101 ohne diesen Effekt fast unsichtbar), bläht
    # bei kompakten hellen Objekten aber Sterne auf und brennt sie aus (M57) -> Obergrenze je Profil
    p["linear_denoise"] = round(float(np.clip((10 / snr) ** 1.5, 0.0, p.get("linear_denoise_max", 1.0))), 2)
    if p["linear_denoise"] >= 0.1:
        why.append(f"lineares Entrauschen {p['linear_denoise']:.2f} (Objekt-SNR {snr:.0f})")
    # sehr schwaches Signal: dunklerer Hintergrund versteckt Restrauschen, Sättigung vorsichtiger
    if snr < 6:
        p["target_bg"] = round(max(0.05, p["target_bg"] - 0.015), 3)
        why.append("dunklerer Hintergrund wegen schwachem Signal")
    if not mono:
        g = float(np.clip(snr / 10, 0.5, 1.15))
        if abs(g - 1) > 0.05:
            p["saturation"] = round(1 + (p["saturation"] - 1) * g, 3)
            why.append(f"Sättigung {'erhöht' if g > 1 else 'gedämpft'} (Farbrauschen)")
    # hohe Dynamik (helle Kerne/Sterne neben schwachem Nebel): mehr Highlight-Kompression
    if m["dynamic"] > 40:
        k = float(np.clip((m["dynamic"] / 40) ** 0.5, 1.0, 2.5))
        p["asinh"] = round(float(np.clip(p["asinh"] * k, 5, 400)), 1)
        why.append(f"Highlight-Kompression erhöht (Dynamik {m['dynamic']:.0f}×)")
    # Entfaltung und Schärfen nur mit genug Signal – sonst schärfen sie Rauschen und ziehen dunkle Ringe um Sterne
    # (M57, SNR 10: Entfaltung 12 + Schärfen 0,5 -> Ringe, verzogene Sterne betont)
    if p.get("deconv", 0) > 0 and snr < DECONV_MIN_SNR:
        p["deconv"] = 0
        why.append("keine Entfaltung (zu wenig Signal)")
    k = float(np.clip((snr - 10) / 15, 0.0, 1.0))
    if p.get("sharpen", 0) > 0 and k < 1:
        p["sharpen"] = round(p["sharpen"] * k, 2)
        why.append("weniger Schärfen (zu wenig Signal)" if k > 0 else "kein Schärfen (zu wenig Signal)")
    return p, why


# ------------------------------------------------------------- bewerten #
SIDES = ["rechts", "rechts oben", "oben", "links oben", "links", "links unten", "unten", "rechts unten"]


def color_gradient(img: np.ndarray, L: np.ndarray, sky: np.ndarray, n: int = 8) -> dict | None:
    """Farbverlauf des Himmels: Ebene durch R−G und B−G der Himmelskacheln. img in Array-Orientierung (Zeile 0 = unten,
    wie es gespeichert wird: gespiegelt). Gibt Stärke (Änderung über die Bildbreite), Richtung und Farbton zurück."""
    h, w = L.shape
    pts = []
    for i in range(n):
        for j in range(n):
            sl = (slice(i * h // n, (i + 1) * h // n), slice(j * w // n, (j + 1) * w // n))
            s = sky[sl]
            if s.mean() < 0.2:
                continue
            v = img[(slice(None),) + sl][:, s]
            lo, hi = np.percentile(v, (20, 80), axis=1)
            m = [float(v[c][(v[c] >= lo[c]) & (v[c] <= hi[c])].mean()) for c in range(3)]
            pts.append(((j + 0.5) / n - 0.5, (i + 0.5) / n - 0.5, m[0] - m[1], m[2] - m[1]))
    if len(pts) < 12:
        return None
    p = np.array(pts)
    A = np.c_[np.ones(len(p)), p[:, 0], p[:, 1]]
    cr = np.linalg.lstsq(A, p[:, 2], rcond=None)[0]
    cb = np.linalg.lstsq(A, p[:, 3], rcond=None)[0]
    ar, ab = float(np.hypot(cr[1], cr[2])), float(np.hypot(cb[1], cb[2]))
    # Richtung, in die der stärkere Farbanteil zunimmt (gy > 0: zum oberen Bildrand, da Zeile 0 = unten)
    if max(ar, ab) < 1e-9:                                      # völlig neutral (synthetische Bilder)
        return {"amount": 0.0, "side": "", "tone": ""}
    gx, gy = (cr[1], cr[2]) if ar >= ab else (cb[1], cb[2])
    ang = float(np.degrees(np.arctan2(gy, gx)))
    ux, uy = gx / max(ar, ab), gy / max(ar, ab)
    dr, db = cr[1] * ux + cr[2] * uy, cb[1] * ux + cb[2] * uy   # Farbänderung in dieser Richtung
    if dr > 0:
        tone = "violett" if db > 0.5 * dr else "rötlich"
    else:
        tone = "bläulich" if db > 0 else "grünlich"
    return {"amount": round(max(ar, ab), 4), "side": SIDES[int(round((ang % 360) / 45)) % 8], "tone": tone}


def assess(rgb: np.ndarray, mono: bool = False, neutral_sky: bool = True) -> dict:
    """Messbare Qualitätskontrolle eines entwickelten Bildes (3,H,W) 0..1.
    neutral_sky: Feld abseits der Milchstraße – ein Farbverlauf im Himmel ist dann sicher ein Fehler (sonst evtl. Nebel).
    Gibt Kennzahlen, Score 0..10 und Probleme (code, Text) zurück."""
    f = max(1, int(np.ceil(max(rgb.shape[1:]) / 1200)))
    img = _bin(rgb.astype(np.float32), f)
    L = luminance(img)
    half = L <= np.percentile(L, 50)                  # Kachelverläufe brauchen Himmel in vielen Kacheln
    sky = processing.dark_sky_mask(L, SKY_PCT, smooth=4.0)
    if sky.sum() < 500:
        sky = L <= np.percentile(L, SKY_PCT)
    bg = float(np.median(L[sky]))
    # sichtbares Rauschen: feine + fleckige Struktur (Hochpass bis ~6 px) nur auf Himmelspixeln
    hp = L - ndi.gaussian_filter(L, 3.0)
    core = ndi.binary_erosion(sky, iterations=3)
    v = hp[core] if core.sum() > 500 else hp[sky]
    noise = 1.4826 * float(np.median(np.abs(v - np.median(v))))
    cast = float(max(abs(np.median(img[c][sky]) - bg) for c in range(3))) if not mono else 0.0
    # Farbrauschen: fleckige Chrominanz im Himmel
    chroma_noise = 0.0
    if not mono:
        c0 = img - L[None]
        ch = ndi.gaussian_filter(c0, (0, 2, 2)) - ndi.gaussian_filter(c0, (0, 12, 12))   # Flecken, nicht Nebelfarbe
        chroma_noise = float(max(1.4826 * np.median(np.abs(c[sky] - np.median(c[sky]))) for c in ch))
    # Restgradient: Median des Hintergrunds in 6x6 Kacheln (nur Himmelspixel)
    h, w = L.shape
    tiles = []
    for i in range(6):
        for j in range(6):
            t = L[i * h // 6 : (i + 1) * h // 6, j * w // 6 : (j + 1) * w // 6]
            s = half[i * h // 6 : (i + 1) * h // 6, j * w // 6 : (j + 1) * w // 6]
            if s.mean() > 0.2:
                tiles.append(np.median(t[s]))
    gradient = float(np.percentile(tiles, 95) - np.percentile(tiles, 5)) if len(tiles) >= 6 else 0.0
    cg = color_gradient(img, L, half) if not mono else None
    black = float((L < 0.004).mean())
    white = float((rgb.max(0) >= 0.999).mean())
    core = burnt_core(L)
    sig =ndi.gaussian_filter(L, 1.5) > bg + 0.12
    if not mono and sig.sum() > 100:
        mx, mn = img.max(0)[sig], img.min(0)[sig]
        satur = float(np.median((mx - mn) / np.maximum(mx, 1e-3)))
    else:
        satur = None
    m = {"bg": round(bg, 4), "noise": round(noise, 4), "cast": round(cast, 4), "chroma_noise": round(chroma_noise, 4), "gradient": round(gradient, 4),
         "black_clip": round(black, 5), "white_clip": round(white, 5), "burnt_core": round(core, 5),
         "saturation": None if satur is None else round(satur, 3), "signal": round(float(sig.mean()), 4),
         "color_gradient": cg["amount"] if cg else 0.0}

    issues: list[tuple[str, str, float]] = []      # (code, Text, Abzug)
    if bg < BG_RANGE[0]:
        issues.append(("bg_dark", "Hintergrund zu dunkel – schwache Strukturen gehen verloren", 1.5))
    elif bg > BG_RANGE[1]:
        issues.append(("bg_bright", "Hintergrund zu hell (grau, flau)", 1.5))
    if noise > NOISE_MAX:
        issues.append(("noisy", "Hintergrund sichtbar verrauscht", min(3.0, 1.0 + 60 * (noise - NOISE_MAX))))
    if cast > CAST_MAX:
        issues.append(("cast", "Farbstich im Hintergrund", min(2.0, 60 * (cast - CAST_MAX) + 0.5)))
    cg_max = COLOR_GRAD_MAX if neutral_sky else COLOR_GRAD_MAX_NEBULA
    if cg and cg["amount"] > cg_max:
        issues.append(("color_gradient", f"Farbverlauf im Hintergrund: {cg['tone']} nach {cg['side']}",
                       min(2.5, 0.8 + 80 * (cg["amount"] - cg_max))))
    if chroma_noise > CHROMA_NOISE_MAX:
        issues.append(("chroma_noise", "Farbrauschen (bunte Flecken) im Hintergrund", min(2.0, 0.5 + 150 * (chroma_noise - CHROMA_NOISE_MAX))))
    if gradient > GRADIENT_MAX:
        issues.append(("gradient", "Ungleichmäßiger Hintergrund (Restgradient/Vignettierung)", min(2.5, 30 * (gradient - GRADIENT_MAX) + 0.5)))
    if black > BLACK_CLIP_MAX:
        issues.append(("black_clip", "Schwarzwerte abgeschnitten", 1.0))
    if white > WHITE_CLIP_MAX:
        issues.append(("white_clip", "Helle Bereiche ausgebrannt", min(2.0, 0.5 + 100 * (white - WHITE_CLIP_MAX))))
    if core:
        issues.append(("burnt_core", "Objektkern ausgebrannt (keine Zeichnung im Kern)", 1.5))
    if satur is not None and satur > SAT_RANGE[1]:
        issues.append(("oversat", "Farben übersättigt", 1.0))
    elif satur is not None and satur < SAT_RANGE[0]:
        issues.append(("undersat", "Farben blass", 0.5))
    if m["signal"] < 0.002:
        issues.append(("faint", "Objekt kaum sichtbar", 1.5))
    score = max(0.0, 10.0 - sum(i[2] for i in issues))
    return {"metrics": m, "score": round(score, 1), "issues": [{"code": c, "text": t} for c, t, _ in issues], "v": QC_VERSION,
            **({"color_gradient": {**cg, "limit": cg_max}} if cg else {})}


def corrections(qc: dict, p: dict) -> dict:
    """Parameteränderungen, die gemessene Probleme beheben (nur bei Problemen, kleine Schritte)."""
    codes = {i["code"] for i in qc["issues"]}
    m = qc["metrics"]
    adj: dict = {}
    # Hintergrundhelligkeit: über den Schwarzpunkt (bg_dark), falls aktiv – so bleibt das Signal unverändert hell
    if "bg_dark" in codes or "black_clip" in codes:
        if p.get("bg_dark") is not None:
            adj["bg_dark"] = min(0.08, p["bg_dark"] + 0.015)
        else:
            adj["target_bg"] = min(0.2, p["target_bg"] + max(0.015, BG_RANGE[0] + 0.02 - m["bg"]))
    elif "bg_bright" in codes:
        if p.get("bg_dark") is not None:
            adj["bg_dark"] = max(0.0, min(p["bg_dark"], BG_RANGE[1]) - 0.015)
        else:
            adj["target_bg"] = max(0.04, p["target_bg"] - max(0.015, m["bg"] - BG_RANGE[1] + 0.02))
    if "noisy" in codes:
        if p["denoise"] < 0.9:
            adj["denoise"] = min(0.9, p["denoise"] + 0.15)
            adj["chroma_denoise"] = min(5.0, p["chroma_denoise"] + 0.5)
        elif "bg_dark" not in codes:
            adj["target_bg"] = max(0.05, adj.get("target_bg", p["target_bg"]) - 0.015)
    if "white_clip" in codes:
        if p.get("star_separation", "auto") not in ("off", None, False):
            adj["star_brightness"] = max(0.5, p.get("star_brightness", 0.85) - 0.1)
        else:
            adj["asinh"] = min(400.0, p["asinh"] * 1.6)
    if "burnt_core" in codes:
        # Alles über dem Weißpunkt wird hart weiß – mehr asinh allein hilft nicht. Weißpunkt anheben, asinh hält die
        # schwachen Bereiche dabei hell.
        nxt = [w for w in WHITE_STEPS if w > p.get("white_pct", WHITE_STEPS[0])]
        if nxt:
            adj["white_pct"] = nxt[0]
            adj["asinh"] = min(400.0, max(adj.get("asinh", p["asinh"]), p["asinh"] * 1.5))
        else:                                        # Weißpunkt ausgereizt: Lichter stärker komprimieren
            adj["asinh"] = min(400.0, max(adj.get("asinh", p["asinh"]), p["asinh"] * 1.5))
    if "color_gradient" in codes and p.get("color_gradient", 0) < 3:
        # Polynomgrad des Farbausgleichs: Grad 2, dann 3. Eine Ebene überkorrigierte NGC 891 (Rotstich nur am Rand,
        # 0,018 -> 0,017 zur Gegenseite), Grad 2: 0,004
        adj["color_gradient"] = max(2, int(p.get("color_gradient", 0)) + 1)
    if "oversat" in codes:
        adj["saturation"] = max(1.0, p["saturation"] - 0.15)
    elif "undersat" in codes and "noisy" not in codes:
        adj["saturation"] = min(1.9, p["saturation"] + 0.15)
    if "chroma_noise" in codes:
        if p["chroma_denoise"] < 5.0:
            adj["chroma_denoise"] = min(5.0, adj.get("chroma_denoise", p["chroma_denoise"]) + 1.0)
        elif p.get("linear_denoise", 0) > 0.4:
            adj["linear_denoise"] = max(0.4, p["linear_denoise"] - 0.25)
    # Restgradient wird nur gemeldet: Nebel im Feld verfälscht die Messung, ein höherer Polynomgrad würde ihn abtragen
    return {k: (round(v, 3) if isinstance(v, float) else v) for k, v in adj.items() if v != p.get(k)}


HINTS = {
    "noisy": "Mehr Belichtungszeit (weitere Nächte) verringert das Rauschen am wirksamsten.",
    "gradient": "Flat-Frames würden Vignettierung und Staub zuverlässig entfernen; Mond und Streulicht meiden.",
    "faint": "Das Objekt ist sehr schwach – deutlich mehr Belichtungszeit oder ein dunklerer Himmel helfen.",
    "chroma_noise": "Mehr Belichtungszeit verringert Farbrauschen; Dithering zwischen den Aufnahmen hilft zusätzlich.",
    "cast": "Ein Lichtverschmutzungsfilter oder Aufnahmen bei dunklerem Himmel verringern Farbstiche.",
    "white_clip": "Kürzere Einzelbelichtungen schützen helle Sterne und Kerne vor dem Ausbrennen.",
    "burnt_core": "Der Objektkern ist sehr hell – kürzere Einzelbelichtungen erhalten mehr Zeichnung im Kern.",
    "color_gradient": "Farbverläufe entstehen meist durch Dämmerung, Mondlicht oder Horizont-Lichtverschmutzung – dunkle, mondlose Nächte und höher stehende Objekte helfen.",
}


def hints(qc: dict, ctx: dict) -> list[str]:
    """Verbesserungsvorschläge für den Nutzer (nur aus gemessenen Problemen und echten Daten)."""
    out = []
    for i in qc["issues"]:
        h = HINTS.get(i["code"])
        if i["code"] == "gradient" and ctx.get("has_flats"):
            h = "Restgradient trotz Flats – Mond, Streulicht oder Tau prüfen."
        if h and h not in out:
            out.append(h)
    exp = ctx.get("exposure_s") or 0
    if exp and exp < 3600 and "noisy" not in {i["code"] for i in qc["issues"]}:
        out.append(f"Bisher {exp / 60:.0f} min Belichtung – mit mehr Nächten werden schwache Bereiche deutlich klarer.")
    if ctx.get("rejected_pct", 0) > 30:
        out.append(f"{ctx['rejected_pct']:.0f} % der Aufnahmen wurden aussortiert (Wolken, Unschärfe, Nachführung).")
    return out


# --------------------------------------------------------------- entwickeln #
def develop(mean: np.ndarray, cov: np.ndarray, params: dict, narrowband: bool, palette: str | None,
            ctx: dict | None = None, fixed: dict | None = None, log=lambda m: None, render_log=lambda m: None, on_preview=None):
    """Gibt (rgb, stats, params, qc_verlauf) zurück. ctx: exposure_s, has_flats, rejected_pct, neutral_sky.
    fixed: vom Nutzer gesetzte Parameter – werden weder angepasst noch nachgeregelt."""
    ctx = ctx or {}
    fixed = fixed or {}
    pm, pc, f = preview(mean, cov)
    log("Bilddaten vermessen")
    m = measure(pm, pc)
    params, why = adapt(params, m, mono=mean.shape[0] == 1)
    params.update(fixed)
    history = [{"round": 0, "measure": m, "adapt": why, "params": dict(params)}]

    def render(p, a, c, lg):
        return processing.process(a, c, p, narrowband=narrowband, palette=palette, log=lg, want_before=True, preview=on_preview)

    # Kontrolle in voller Auflösung: Entrauschen und Farbe wirken pixelbezogen, eine Vorschau bewertet sie falsch
    for rnd in range(1, QC_ROUNDS + 1):
        log(f"Entwicklung und Qualitätskontrolle {rnd}")
        rgb, stats = render(params, mean, cov, render_log)
        render_log(f"Qualitätsprüfung {rnd}")
        qc = assess(rgb, mono=mean.shape[0] == 1, neutral_sky=ctx.get("neutral_sky", True))
        adj = {k: v for k, v in corrections(qc, params).items() if k not in fixed} if rnd < QC_ROUNDS else {}
        history.append({"round": rnd, "score": qc["score"], "issues": [i["text"] for i in qc["issues"]], "adjust": adj})
        if not adj:
            break
        params = {**params, **adj}
    qc["hints"] = hints(qc, ctx)
    qc["measure"] = m
    stats["quality"] = qc
    stats["qc"] = history
    return rgb, stats, params, history
