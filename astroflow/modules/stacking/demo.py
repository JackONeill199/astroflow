"""Synthetische Demo-Aufnahmen (Galaxie + Sternfeld, Bayer-Sensor, Rotation, defekte Frames; Komet vor Sternfeld)."""
from __future__ import annotations

import datetime as dt
from pathlib import Path

import numpy as np
from scipy import ndimage as ndi

from .fits_io import write_fits

H, W = 360, 480


def _sky(rng, size=(620, 800)):
    sh, sw = size
    n = 450
    stars = np.column_stack([
        rng.uniform(0, sw, n), rng.uniform(0, sh, n),
        10 ** rng.uniform(2.9, 4.6, n),                       # Fluss
        rng.uniform(0.0, 1.0, n),                              # Farbe: 0 = blau, 1 = rot
    ])
    yy, xx = np.mgrid[0:sh, 0:sw].astype(np.float32)
    cy, cx = sh / 2, sw / 2
    th = np.deg2rad(35)
    u = (xx - cx) * np.cos(th) + (yy - cy) * np.sin(th)
    v = -(xx - cx) * np.sin(th) + (yy - cy) * np.cos(th)
    r = np.sqrt((u / 1.0) ** 2 + (v / 0.4) ** 2)
    gal = 70 * np.exp(-7.67 * ((r / 110) ** 0.25 - 1)) * (r < 330)
    arms = ndi.gaussian_filter(rng.normal(size=(sh, sw)), 6) * 90
    ext = np.stack([gal * 1.05 + np.clip(arms, 0, None) * 0.6 * (r < 220),
                    gal * 0.95 + np.clip(arms, 0, None) * 0.5 * (r < 220),
                    gal * 0.75 + np.clip(arms, 0, None) * 0.8 * (r < 220)]).astype(np.float32)
    return stars, ext


def _frame(rng, stars, ext, angle_deg, shift, fwhm, trail=0.0, clouds=1.0, sat_trail=False, tilt=0.0):
    """tilt: Sterngröße wächst linear nach rechts (±tilt um die Mitte) – verkippter Sensor für die Gerätediagnose."""
    sh, sw = ext.shape[1:]
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    th = np.deg2rad(angle_deg)
    c, s = np.cos(th), np.sin(th)
    cf = np.array([W / 2, H / 2])
    cs = np.array([sw / 2, sh / 2]) + shift
    sx = c * (xx - cf[0]) - s * (yy - cf[1]) + cs[0]
    sy = s * (xx - cf[0]) + c * (yy - cf[1]) + cs[1]
    img = np.stack([ndi.map_coordinates(e, [sy, sx], order=1, mode="constant") for e in ext])
    img = ndi.gaussian_filter(img, (0, fwhm / 2.355, fwhm / 2.355))
    # Sterne
    sig = fwhm / 2.355
    R = 7
    d = np.arange(-R, R + 1)
    for x, y, f, col in stars:
        dx, dy = x - cs[0], y - cs[1]
        px = c * dx + s * dy + cf[0]
        py = -s * dx + c * dy + cf[1]
        if not (R < px < W - R - 1 and R < py < H - R - 1):
            continue
        ix, iy = int(px), int(py)
        sg = sig * (1 + tilt * (px / W - 0.5) * 2) if tilt else sig
        gx = np.exp(-((d + ix - px) ** 2) / (2 * (sg * (1 + trail)) ** 2))
        gy = np.exp(-((d + iy - py) ** 2) / (2 * sg**2))
        psf = np.outer(gy, gx)
        psf /= psf.sum()
        rgb = np.array([0.5 + col, 1.0, 1.5 - col])
        rgb /= rgb.mean()
        for ch in range(3):
            img[ch, iy - R : iy + R + 1, ix - R : ix + R + 1] += f * rgb[ch] * psf
    if sat_trail:
        for t in np.linspace(0, 1, 800):
            x, y = int(t * W), int(40 + t * 200)
            if 0 <= y < H and x < W:
                img[:, y, x] += 800
    return img * clouds


def _mosaic(img3):  # RGGB
    raw = np.empty((H, W), np.float32)
    raw[0::2, 0::2] = img3[0, 0::2, 0::2]
    raw[0::2, 1::2] = img3[1, 0::2, 1::2]
    raw[1::2, 0::2] = img3[1, 1::2, 0::2]
    raw[1::2, 1::2] = img3[2, 1::2, 1::2]
    return raw


def generate(out: Path, n_lights=30, n_darks=8, n_flats=8, seed=1, anonymous=False) -> Path:
    """anonymous=True: Lights ohne OBJECT/RA/DEC (wie bei Kameras ohne Montierungsanbindung)."""
    rng = np.random.default_rng(seed)
    out = Path(out)
    (out / "lights").mkdir(parents=True, exist_ok=True)
    (out / "darks").mkdir(exist_ok=True)
    (out / "flats").mkdir(exist_ok=True)
    stars, ext = _sky(rng)
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    vign = 1 - 0.45 * (((xx - W / 2) / (W / 2)) ** 2 + ((yy - H / 2) / (H / 2)) ** 2) / 2
    hot = np.zeros((H, W), np.float32)
    idx = rng.integers(0, H * W, 60)
    hot.ravel()[idx] = rng.uniform(300, 2500, 60)
    fixed = 100 + hot + 30 * (xx / W)                         # Dunkelstrom + Offset
    hdr = dict(INSTRUME="DemoScope", BAYERPAT="RGGB", EXPTIME=10.0, GAIN=80.0)
    t0 = dt.datetime(2026, 8, 12, 22, 0, 0)
    for i in range(n_lights):
        night2 = i >= int(n_lights * 0.65)                   # zweite Nacht, andere Feldrotation
        ang = (i * 0.35 if not night2 else 40 + (i - int(n_lights * 0.65)) * 0.35)
        shift = rng.normal(0, 6, 2)
        fw, tr, cl, st = rng.normal(3.6, 0.2), 0.0, 1.0, False
        if i % 11 == 7:
            fw, kind = 7.5, "seeing"
        if i % 13 == 5:
            cl = 0.25
        if i % 17 == 9:
            tr = 1.2
        if i % 19 == 3:
            st = True
        sky = _frame(rng, stars, ext, ang, shift, fw, tr, cl, st)
        sky = sky * vign
        raw = _mosaic(sky) + fixed + 40 * (cl < 1) + rng.normal(0, 14, (H, W))
        d = t0 + dt.timedelta(minutes=i * 10 + (24 * 60 if night2 else 0))
        write_fits(out / "lights" / f"Light_M31_{i:03d}.fit", np.clip(raw, 0, 65535).astype(np.uint16),
                   dict(hdr, **({} if anonymous else dict(OBJECT="M 31", RA=10.684, DEC=41.269)), **{"DATE-OBS": d.isoformat()}, IMAGETYP="Light"))
    for i in range(n_darks):
        write_fits(out / "darks" / f"Dark_{i:02d}.fit", (fixed + rng.normal(0, 14, (H, W))).astype(np.uint16),
                   dict(hdr, IMAGETYP="Dark", **{"DATE-OBS": t0.isoformat()}))
    for i in range(n_flats):
        fl = 20000 * vign + rng.normal(0, 60, (H, W)) + fixed * 0.0
        write_fits(out / "flats" / f"Flat_{i:02d}.fit", (fl + 100).astype(np.uint16),
                   dict(hdr, IMAGETYP="Flat", EXPTIME=0.5, **{"DATE-OBS": t0.isoformat()}))
    return out


def hot_pairs(rng, n_pairs=12, n_warm=40):
    """Ortsfeste Sensorfehler wie bei der Stellina: R-Pixel-Paare (übereinander in der R-Ebene, ~10 000–20 000 ADU)
    und warme Einzelpixel knapp unter der Kosmetik-Schwelle (≈ 7σ bei Rauschen 14). Rückgabe (Bild, Paar-Indizes)."""
    hot = np.zeros((H, W), np.float32)
    ys = 2 * rng.integers(10, H // 2 - 10, n_pairs)            # RGGB: R in geraden Zeilen/Spalten
    xs = 2 * rng.integers(10, W // 2 - 10, n_pairs)
    v = rng.uniform(10000, 20000, n_pairs)
    hot[ys, xs] += v
    hot[ys + 2, xs] += v * rng.uniform(0.8, 1.0, n_pairs)
    wy, wx = 2 * rng.integers(5, H // 2 - 5, n_warm), 2 * rng.integers(5, W // 2 - 5, n_warm)
    hot[wy, wx] += 110
    return hot, np.concatenate([ys * W + xs, (ys + 2) * W + xs])


def generate_hotpixel_pairs(out: Path, n_groups=6, per_group=4, seed=5, static=False) -> Path:
    """Stellina-artige Serie ohne Darks: Das Gerät verharrt je `per_group` Aufnahmen auf derselben Stelle und springt
    dann (Versatz + leichte Feldrotation) – so treffen ortsfeste Hotpixel-Paare mehrmals dieselbe Himmelsstelle.
    static=True: Feld steht still (Sterne bleiben am selben Sensorpixel – sie dürfen nicht als Fehler gelten)."""
    rng = np.random.default_rng(seed)
    out = Path(out)
    (out / "lights").mkdir(parents=True, exist_ok=True)
    stars, ext = _sky(rng)
    hot, pairs = hot_pairs(rng)
    hdr = dict(INSTRUME="DemoScope", BAYERPAT="RGGB", EXPTIME=10.0, GAIN=80.0, OBJECT="M 31", RA=10.684, DEC=41.269)
    t0 = dt.datetime(2026, 8, 12, 22, 0, 0)
    i = 0
    for g in range(n_groups):
        shift = np.zeros(2) if static else rng.uniform(-25, 25, 2)
        ang = 0.0 if static else g * 0.6
        sky = _frame(rng, stars, ext * 0.3, ang, shift, 3.6)
        for _ in range(per_group):
            raw = _mosaic(sky) + 300 + hot + rng.normal(0, 14, (H, W))
            d = t0 + dt.timedelta(minutes=i * 2)
            write_fits(out / "lights" / f"Light_M31_{i:03d}.fit", np.clip(raw, 0, 65535).astype(np.uint16),
                       dict(hdr, **{"DATE-OBS": d.isoformat()}, IMAGETYP="Light"))
            i += 1
    np.save(out / "paare.npy", pairs)
    return out


# Mono-Kamera mit Filterrad: Kanalgewichte (R,G,B des Himmelsmodells) je Filter
BAND_WEIGHTS = {"L": (0.34, 0.33, 0.33), "R": (1, 0, 0), "G": (0, 1, 0), "B": (0, 0, 1),
                "Ha": (0.35, 0, 0), "OIII": (0, 0.12, 0.2), "SII": (0.15, 0, 0)}


def generate_mono(out: Path, bands=("L", "R", "G", "B"), per_band=6, seed=2, obj="M 31") -> Path:
    """Mono-Aufnahmen mit mehreren Filtern (eine Nacht, leichte Drift/Rotation zwischen den Filtern)."""
    rng = np.random.default_rng(seed)
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    stars, ext = _sky(rng)
    t0 = dt.datetime(2026, 9, 3, 21, 0, 0)
    n = 0
    for b in bands:
        wts = np.array(BAND_WEIGHTS[b], np.float32)
        for i in range(per_band):
            sky = _frame(rng, stars, ext, n * 0.3, rng.normal(0, 5, 2), rng.normal(3.4, 0.15))
            img = np.tensordot(wts, sky, 1) * 3 + 300 + rng.normal(0, 10, (H, W))
            d = t0 + dt.timedelta(minutes=5 * n)
            write_fits(out / f"M31_{b}_{i:03d}.fit", np.clip(img, 0, 65535).astype(np.uint16),
                       dict(INSTRUME="MonoCam", OBJECT=obj, FILTER=b, EXPTIME=60.0, GAIN=100.0,
                            IMAGETYP="Light", **{"DATE-OBS": d.isoformat()}))
            n += 1
    return out


def comet_light(shape, x, y, amp=260.0, coma=7.0, tail_deg=200.0, tail_len=70.0):
    """Diffuser Komet im Himmelsraster: Kern + Koma (grünlich, C2) + schwacher Schweif. Gibt (3,H,W)."""
    sh, sw = shape
    yy, xx = np.mgrid[0:sh, 0:sw].astype(np.float32)
    r2 = (xx - x) ** 2 + (yy - y) ** 2
    th = np.deg2rad(tail_deg)
    u = (xx - x) * np.cos(th) + (yy - y) * np.sin(th)
    v = -(xx - x) * np.sin(th) + (yy - y) * np.cos(th)
    tail = np.where(u > 0, np.exp(-u / tail_len) * np.exp(-v ** 2 / (2 * (4 + 0.12 * np.maximum(u, 0)) ** 2)), 0)
    body = amp * np.exp(-r2 / (2 * coma ** 2)) + 3 * amp * np.exp(-r2 / (2 * 1.4 ** 2)) + 0.35 * amp * tail
    return np.stack([0.65 * body, 1.25 * body, 0.95 * body]).astype(np.float32)


def generate_comet(out: Path, n_lights=30, seed=3, name="C_2026_A1", rate=(1.1, 0.45), exptime=10.0) -> Path:
    """Stellina-artige Kometenserie: Ordner „…_observation_<name>“ ohne OBJECT/RA/DEC, Bayer, leichte Drift/Feldrotation;
    der Komet wandert mit `rate` (Himmelspixel je Aufnahme) linear vor den Sternen."""
    rng = np.random.default_rng(seed)
    out = Path(out)
    d = out / f"2026-08-14_22-00-00_observation_{name}" / "01-images-initial"
    d.mkdir(parents=True, exist_ok=True)
    stars, ext = _sky(rng)
    ext = ext * 0.0                                            # nur Sterne, keine Galaxie
    sh, sw = ext.shape[1:]
    t0 = dt.datetime(2026, 8, 14, 22, 0, 0)
    c0 = np.array([sw / 2 - 0.5 * rate[0] * n_lights, sh / 2 - 0.5 * rate[1] * n_lights])
    for i in range(n_lights):
        cx, cy = c0 + np.array(rate) * i
        sky_ext = comet_light((sh, sw), cx, cy)
        img = _frame(rng, stars, sky_ext, i * 0.25, rng.normal(0, 3, 2), rng.normal(3.6, 0.15))
        raw = _mosaic(img) + 300 + rng.normal(0, 12, (H, W))
        t = t0 + dt.timedelta(seconds=i * (exptime + 5))
        write_fits(d / f"img-{i + 1:04d}r.fits", np.clip(raw, 0, 65535).astype(np.uint16),
                   dict(INSTRUME="DemoScope", BAYERPAT="RGGB", EXPTIME=exptime, GAIN=100.0, **{"DATE-OBS": t.isoformat()}))
    return out


DRIFT_V = (0.25, -0.1)                                         # Himmelspixel je Sekunde (generate_drift)
DRIFT_JUMPS = [(0, 0), (-35, -25), (30, 30), (-30, 35), (35, -35), (-5, 45)]   # Nachzentrieren: deutliche Sprünge wie bei Stellina


def generate_drift(out: Path, n_lights=36, per_seg=6, step_s=20.0, exptime=10.0, tilt=0.3, seed=7, temp0=12.0,
                   start: dt.datetime | None = None) -> Path:
    """Stellina-artige Serie für die Gerätediagnose: gleichmäßige Drift `DRIFT_V` zwischen Nachzentrierungen (alle `per_seg`
    Aufnahmen Sprung auf den nächsten Versatz aus `DRIFT_JUMPS`), Sterne zur rechten Seite größer (`tilt`), Ordner „01-images-initial“
    und danach „02-images-adjust-focus“ (Nachfokussieren), Sensortemperatur im Kopf (TEMP, fällt um 0,1 °C je Aufnahme)."""
    rng = np.random.default_rng(seed)
    out = Path(out)
    t0 = start or dt.datetime(2026, 8, 20, 22, 0, 0)
    base = out / f"{t0:%Y-%m-%d_%H-%M-%S}_observation_M31"
    stars, ext = _sky(rng)
    ext = ext * 0.3
    for i in range(n_lights):
        seg, k = divmod(i, per_seg)
        t = i * step_s
        jump = np.array(DRIFT_JUMPS[seg % len(DRIFT_JUMPS)], float)
        shift = jump + np.array(DRIFT_V) * (t - seg * per_seg * step_s)
        img = _frame(rng, stars, ext, 0.0, shift, rng.normal(3.6, 0.1), tilt=tilt)
        raw = _mosaic(img) + 300 + rng.normal(0, 10, (H, W))
        d = base / ("01-images-initial" if i < per_seg else "02-images-adjust-focus")
        d.mkdir(parents=True, exist_ok=True)
        write_fits(d / f"img-{i + 1:04d}r.fits", np.clip(raw, 0, 65535).astype(np.uint16),
                   dict(INSTRUME="stellina-demo", BAYERPAT="RGGB", EXPTIME=exptime, GAIN=200.0, OBJECT="M 31", RA=10.684, DEC=41.269,
                        TEMP=round(temp0 - 0.1 * i, 1), **{"DATE-OBS": (t0 + dt.timedelta(seconds=t)).isoformat()}))
    return out


def generate_asteroid(out: Path, n_lights=36, seed=11, rate=(0.9, 0.35), flux=900.0, exptime=10.0, gap=20.0):
    """Sternfeld mit einem schwachen, wandernden Punkt (Asteroid): Bayer, leichte Drift/Feldrotation wie eine Stellina.
    Gibt (Ordner, Wahrheit) zurück – Wahrheit = Himmelsraster-Lage je Aufnahme und die Lage (Drehwinkel, Versatz) jeder Aufnahme,
    damit Tests die wahre Bahn im Referenzraster nachrechnen können (demo._frame: Himmel → Aufnahme)."""
    rng = np.random.default_rng(seed)
    out = Path(out)
    d = out / "2026-09-20_22-00-00_observation_Feld" / "01-images-initial"
    d.mkdir(parents=True, exist_ok=True)
    stars, ext = _sky(rng)
    ext = ext * 0.0
    sh, sw = ext.shape[1:]
    t0 = dt.datetime(2026, 9, 20, 22, 0, 0)
    c0 = np.array([sw / 2 - 0.5 * rate[0] * n_lights - 25, sh / 2 - 0.5 * rate[1] * n_lights + 20])
    truth = {"sky": [], "angle": [], "shift": [], "time": [], "shape": (sh, sw), "frame": (H, W)}
    for i in range(n_lights):
        pos = c0 + np.array(rate) * i
        ang, shift = i * 0.2, rng.normal(0, 3, 2)
        st = np.vstack([stars, [[pos[0], pos[1], flux, 0.5]]])
        img = _frame(rng, st, ext, ang, shift, rng.normal(3.6, 0.12))
        raw = _mosaic(img) + 300 + rng.normal(0, 12, (H, W))
        t = t0 + dt.timedelta(seconds=i * gap)
        write_fits(d / f"img-{i + 1:04d}r.fits", np.clip(raw, 0, 65535).astype(np.uint16),
                   dict(INSTRUME="DemoScope", BAYERPAT="RGGB", EXPTIME=exptime, GAIN=100.0, **{"DATE-OBS": t.isoformat()}))
        truth["sky"].append(pos.tolist())
        truth["angle"].append(ang)
        truth["shift"].append(shift.tolist())
        truth["time"].append((t + dt.timedelta(seconds=exptime / 2)).replace(tzinfo=dt.timezone.utc).timestamp())
    return out, truth


def distort(p: np.ndarray, k: float, size: tuple[int, int]) -> np.ndarray:
    """Radiale Verzeichnung (Kissen bei k > 0) um die Bildmitte: r' = r·(1 + k·r²), p in Pixeln (N, 2)."""
    h, w = size
    c = np.array([(w - 1) / 2, (h - 1) / 2])
    d = p - c
    return c + d * (1 + k * (d * d).sum(1, keepdims=True))


def generate_distortion(out: Path, n_lights=24, per_seg=4, size=(1024, 1536), k=6.4e-9, wander=120.0, fwhm=3.4,
                        n_stars=2500, seed=13, exptime=10.0) -> tuple[Path, dict]:
    """Stellina-artige Serie für die Verzerrungskorrektur: feste radiale Verzeichnung der Optik (`k`, Ecke ~5 px bei
    1536×1024), das Feld springt alle `per_seg` Aufnahmen auf einen neuen Versatz (±`wander` px, Nachzentrieren) und
    driftet dazwischen leicht. Graue Sterne auf Bayer-Sensor, ohne Nebel. Gibt (Ordner, Wahrheit) zurück."""
    rng = np.random.default_rng(seed)
    out = Path(out)
    h, w = size
    t0 = dt.datetime(2026, 9, 2, 22, 0, 0)
    base = out / f"{t0:%Y-%m-%d_%H-%M-%S}_observation_M13"
    base.mkdir(parents=True, exist_ok=True)
    m = wander + 40
    sky = np.column_stack([rng.uniform(-m, w + m, n_stars), rng.uniform(-m, h + m, n_stars)])
    flux = 10 ** rng.uniform(2.6, 4.8, n_stars)
    sig = fwhm / 2.355
    R = 7
    d = np.arange(-R, R + 1)
    truth: dict = {"k": k, "shift": []}
    jump = np.zeros(2)
    for i in range(n_lights):
        seg, j = divmod(i, per_seg)
        if j == 0:
            jump = np.zeros(2) if seg == 0 else rng.uniform(-wander, wander, 2)
        shift = jump + np.array([0.4, -0.25]) * j + rng.normal(0, 0.3, 2)
        p = distort(sky - shift, k, size)
        img = np.zeros((h, w), np.float32)
        for (px, py), f in zip(p, flux):
            if not (R < px < w - R - 1 and R < py < h - R - 1):
                continue
            ix, iy = int(px), int(py)
            psf = np.outer(np.exp(-((d + iy - py) ** 2) / (2 * sig**2)), np.exp(-((d + ix - px) ** 2) / (2 * sig**2)))
            img[iy - R: iy + R + 1, ix - R: ix + R + 1] += f * psf / psf.sum()
        raw = img + 300 + rng.normal(0, 10, (h, w))
        write_fits(base / f"img-{i + 1:04d}r.fits", np.clip(raw, 0, 65535).astype(np.uint16),
                   dict(INSTRUME="stellina-demo", BAYERPAT="RGGB", EXPTIME=exptime, GAIN=200.0, OBJECT="M 13",
                        RA=250.423, DEC=36.461, **{"DATE-OBS": (t0 + dt.timedelta(seconds=30 * i)).isoformat()}))
        truth["shift"].append(shift.tolist())
    return out, truth


def generate_mosaic(out: Path, grid=(2, 2), step=(0.5, 0.5), per_tile=5, seed=13, jitter=3.0, exptime=10.0,
                    obj="NGC 2244") -> Path:
    """Mosaik wie der Stellina-Mosaikmodus: Raster aus grid = (Spalten, Zeilen) Kacheln im Abstand step (Anteil von
    Bildbreite/-höhe), je Kachel `per_tile` Aufnahmen (abwechselnd, wie das Teleskop die Positionen wiederholt), dazu
    kleines Zittern. Ein Nebel liegt außermittig (Kacheln mit viel/wenig Nebel: der Median-Hintergrund unterscheidet
    sich), der Himmelshintergrund schwankt je Aufnahme und hat einen Verlauf im Bildfeld. Nebel und Sterne sind
    Bayer-RGGB wie bei generate()."""
    rng = np.random.default_rng(seed)
    out = Path(out)
    d = out / "lights"
    d.mkdir(parents=True, exist_ok=True)
    nx, ny = grid
    sx, sy = step[0] * W, step[1] * H
    size = (int(H + sy * (ny - 1) + 160), int(W + sx * (nx - 1) + 160))
    sh, sw = size
    n = int(450 * sh * sw / (620 * 800))
    stars = np.column_stack([rng.uniform(0, sw, n), rng.uniform(0, sh, n), 10 ** rng.uniform(2.9, 4.6, n), rng.uniform(0, 1, n)])
    yy, xx = np.mgrid[0:sh, 0:sw].astype(np.float32)
    neb = 120 * np.exp(-(((xx - 0.7 * sw) / (0.22 * sw)) ** 2 + ((yy - 0.35 * sh) / (0.25 * sh)) ** 2))
    neb = neb * (0.7 + 0.3 * ndi.gaussian_filter(rng.normal(size=size), 8) / 0.05).clip(0.3, 1.6)
    ext = np.stack([neb * 1.3, neb * 0.6, neb * 0.5]).astype(np.float32)
    # Kachelmitten relativ zur Himmelsmitte
    offs = [((i - (nx - 1) / 2) * sx, (j - (ny - 1) / 2) * sy) for j in range(ny) for i in range(nx)]
    hdr = dict(INSTRUME="DemoScope", BAYERPAT="RGGB", EXPTIME=exptime, GAIN=80.0, OBJECT=obj, RA=97.98, DEC=4.94)
    t0 = dt.datetime(2026, 12, 27, 21, 0, 0)
    for i in range(per_tile * len(offs)):
        o = np.array(offs[i % len(offs)]) + rng.normal(0, jitter, 2)
        img = _frame(rng, stars, ext, 0.0, o, rng.normal(3.4, 0.15))
        sky = rng.uniform(150, 260) + 40 * (xx[:H, :W] / W)               # Himmel je Aufnahme anders, Verlauf im Bildfeld
        raw = _mosaic(img) + sky + rng.normal(0, 12, (H, W))
        write_fits(d / f"Light_{obj.replace(' ', '')}_{i:03d}.fit", np.clip(raw, 0, 65535).astype(np.uint16),
                   dict(hdr, IMAGETYP="Light", **{"DATE-OBS": (t0 + dt.timedelta(seconds=30 * i)).isoformat()}))
    return out
