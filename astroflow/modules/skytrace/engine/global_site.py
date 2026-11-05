"""Aufstellort einer Nacht ohne Vorwissen aus den Satellitenspuren zurückrechnen (0.32.0) – nur Vorschlag.

Anlass (05.11.2026): In einer Augustwoche 2022 stand das Teleskop rund 250 km von jedem Ort der Ortsliste entfernt (Urlaub).
Die bisherige Suche (`rematch_sites.night`, `sitefit`) sucht nur ≤ 20 km um Listenorte und fand dort 0 Treffer – auch mit
± 60 min Uhrversatz. Die Stellina speichert keinen Ort. Verfahren:

  1. Rückprojektion: Ein Satellit S, der zur Zeit t in Richtung d (Spurmitte, aus `world_path`) gesehen wird, legt den
     Beobachter auf die Gerade S − ρ·d fest; ihr erster Schnitt mit dem WGS84-Ellipsoid ist der einzige Bodenpunkt, von dem
     aus das passt. Für jede Spur und jede Bahn des lokalen Archivs liefert das je Zeitpunkt einen Bodenpunkt. Er zählt nur,
     wenn der Satellit dort ≥ `MIN_ALT_DEG` hoch steht, im Sonnenlicht ist, sich am Himmel in Spurrichtung bewegt
     (≤ `ANGLE_DEG`) und die Spur innerhalb der Belichtung schaffen kann (Spur ganz im Bild: Dauer ≈ Belichtung).
     Vorauswahl auf einem 15-s-Raster für alle Bahnen (SGP4 vektorisiert), dann 1-s-Schritte nur für die übrigen Paare.
  2. Abstimmen: grob in Zellen (`CELL_KM`) × Uhrversatz-Klassen (`CLOCK_BIN_S`), dann fein (`FINE_KM` × 1 s) um die besten
     Blöcke; ein Punkt zur Zeit t stimmt für alle Versätze, bei denen der Durchgang in die Belichtung fällt. Eine Häufung braucht
     Stimmen verschiedener Spuren mit **verschiedenen NORAD-Nummern**.
     Uhrversatz als zweite Unbekannte: erst ± 60 s (normale Uhr), ohne Ergebnis ± 20 min. Ein Uhrfehler verschiebt die
     Bodenpunkte entlang der jeweiligen Bahn – Spuren mit verschiedenen Richtungen treffen sich dann nicht mehr.
  3. Zufallsniveau: λ = Summe je Spur der Wahrscheinlichkeit, eine zufällige Stelle (Ort, Versatz) abzudecken – aus der Dichte
     ihrer Stimmen im Umkreis `LOCAL_KM`; Fehlalarm = Poisson(≥ k | λ) × Zahl unabhängiger Prüfstellen im Suchraum.
     Vorschlag nur ab `MIN_TRAILS` verschiedenen Spuren und Fehlalarm ≤ `MAX_FALSE` (0,1 %).
  4. Verfeinern: `sitefit.fit` aus den beteiligten Bahnen (Ort + Uhr), dann strenger Abgleich aller Spuren der Nacht am
     Ergebnis (`rematch_sites.check`, normale Toleranzen) und zweite Rückrechnung aus dessen eindeutigen Treffern.

Keine Netzabfragen: Bahnen nur aus den lokalen Archiven (`satellite_archive.historical_models` mit `orbits_cache_only`)."""
import math
import time

import numpy as np

A_KM, B_KM = 6378.137, 6356.752314245          # WGS84
E2 = 1 - (B_KM / A_KM) ** 2
R_MEAN = 6371.0
OMEGA = 7.292115e-5                             # rad/s

REGION_KM = 1500.0              # Suchgebiet: so weit um die Orte der Ortsliste (Einstellung im Auftrag)
CELL_KM = 20.0                  # grobe Zelle (Blöcke 2 × 2 → 40 km) – nur Vorauswahl für die feine Stufe
CLOCK_BIN_S = 4.0
CLOCK_STAGES_S = (60.0, 1200.0) # erst normale Uhr, dann ± 20 min
COARSE_S = 15.0                 # Vorauswahl-Raster für alle Bahnen
COARSE_MARGIN_KM = 250.0        # Bodenpunkt wandert zwischen zwei Rasterpunkten bis ~110 km (LEO)
FINE_S = 1.0
MIN_ALT_DEG = 10.0
ANGLE_DEG = 6.0                 # Richtung der Bahn am Himmel gegen die Spurrichtung
COARSE_ANGLE_DEG = 25.0
TIME_TOL_S = 4.0                # Bahnfehler längs / Rundung der Belichtungszeiten
SHADOW_MARGIN_KM = 60.0         # Halbschatten, Bahnfehler: erst so tief im Erdschatten gilt ein Satellit als dunkel
TOP_BLOCKS = 40                 # so viele grobe Blöcke fein prüfen
BOX_KM = 30.0                   # feine Stufe: ± so weit um die Blockmitte …
BOX_S = 12.0                    # … und ± so viele Sekunden Versatz
FINE_KM = 1.0
TOL_KM = 2.5                    # Spielraum quer am Boden (echte Treffer IC59: Bodenpunkte 0,6–3,3 km neben dem Ort bei 1-s-Schritten)
ANG_TOL_ARCSEC = 300.0          # ferne Bahnen: Spielraum = Entfernung × diesem Winkel (strenge Toleranz 360″)
LOCAL_KM = 300.0                # Umkreis für die Dichte zufälliger Stimmen
MIN_TRAILS = 3
MAX_FALSE = 0.001               # 10× strenger als nominal: echte Zufallsläufe (verschobene Spurzeiten) erreichten einmal 0,9 %
MAX_CLUSTERS = 4                # so viele Häufungen höchstens streng nachprüfen
NEAR_LIST_KM = 2.0              # so nah an einem Listenort → diesen vorschlagen
MAX_PAIRS = 60000               # Notbremse gegen Speicher/Laufzeit


def _o3():
    import importlib
    return importlib.import_module('skytrace_server.orbit3d')


def _haversine_km(lat1, lon1, lat2, lon2):
    p1, p2 = np.radians(lat1), np.radians(lat2)
    a = np.sin((p2 - p1) / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(np.radians(lon2 - lon1) / 2) ** 2
    return 2 * R_MEAN * np.arcsin(np.sqrt(np.clip(a, 0, 1)))


def ground_points(r, d):
    """Erster Schnitt der Geraden r − ρ·d (ρ > 0) mit dem WGS84-Ellipsoid. r (N, 3) km, d (3,) oder (N, 3) Einheitsvektor.
    TEME genügt: Das Ellipsoid ist um die z-Achse symmetrisch (Polbewegung vernachlässigt)."""
    k = np.array([1.0, 1.0, A_KM / B_KM])
    rs, ds = r * k, np.broadcast_to(d, r.shape) * k
    dd = (ds * ds).sum(-1); rd = (rs * ds).sum(-1); rr = (rs * rs).sum(-1)
    disc = rd * rd - dd * (rr - A_KM * A_KM)
    ok = disc > 0
    rho = (rd - np.sqrt(np.where(ok, disc, 0))) / dd
    ok &= rho > 0
    g = r - rho[..., None] * np.broadcast_to(d, r.shape)
    return g, rho, ok


def geodetic(g, theta):
    """Punkte auf dem Ellipsoid (TEME) → geodätische Breite/Länge in Grad (θ = GMST)."""
    p = np.hypot(g[..., 0], g[..., 1])
    lat = np.degrees(np.arctan2(g[..., 2], p * (1 - E2)))
    lon = (np.degrees(np.arctan2(g[..., 1], g[..., 0]) - theta) + 180) % 360 - 180
    return lat, lon


def _normal(g):
    n = g / np.array([A_KM ** 2, A_KM ** 2, B_KM ** 2])
    return n / np.linalg.norm(n, axis=-1, keepdims=True)


def _motion(r, v, g, matrix):
    """Scheinbare Winkelgeschwindigkeit (ICRS, rad/s) des Satelliten für einen erdfesten Beobachter in g (alles TEME)."""
    rho = r - g
    dist = np.linalg.norm(rho, axis=-1, keepdims=True)
    u = rho / dist
    vg = OMEGA * np.stack([-g[..., 1], g[..., 0], np.zeros(g.shape[:-1])], axis=-1)
    rd = v - vg
    w = (rd - (rd * u).sum(-1, keepdims=True) * u) / dist
    return w @ matrix                                           # TEME → ICRS (Zeilenvektoren: teme = M · icrs)


def _sunlit(r, sun):
    proj = r @ sun
    perp = np.linalg.norm(r - proj[..., None] * sun, axis=-1)
    return (proj > 0) | (perp > R_MEAN - SHADOW_MARGIN_KM)


def _jd(t):
    t = np.asarray(t, float)
    day = np.floor(t / 86400)
    return 2440587.5 + day, (t - day * 86400) / 86400


# ---------------------------------------------------------------- Eingaben ---
def prepare(sitefit, entries, settings):
    """(Ordner, Kandidat) → Spuren mit Mitte/Richtung/Länge (ICRS), Belichtung (wahre UTC mit übernommenem Versatz)."""
    from catalog_matching import _times, clock_offset
    from motion_plausibility import exposure_start
    o3 = _o3()
    out = []
    for folder, item in entries:
        try:
            start, dur = _times(item, settings)
            cam = exposure_start(item) + dur / 2
        except (ValueError, TypeError, KeyError):
            continue
        wp = np.asarray(item.get('world_path') or [], float)
        if wp.ndim != 2 or len(wp) < 2:
            continue
        u = o3.unit_radec(wp[:, 0], wp[:, 1])
        a, b = u[0], u[-1]
        mid = a + b; mid /= np.linalg.norm(mid)
        length = float(np.arccos(np.clip(a @ b, -1, 1)))
        e = b - a; e -= (e @ mid) * mid
        if length <= 0 or np.linalg.norm(e) == 0:
            continue
        e /= np.linalg.norm(e)
        out.append(dict(folder=folder, item=item, id=item.get('id'), start=float(start), dur=float(dur), cam=float(cam),
                        base=clock_offset(item, settings), mid=mid, dir=e, length=length,
                        inside=bool(sitefit._inside(folder, item)), matrix=o3.icrs_to_teme(start + dur / 2)))
    return out


def load_orbits(trails, settings):
    """Bahnen je Spur aus den lokalen Archiven (wie der normale Abgleich, `historical_models` zur Kamerazeit) → Gruppen
    {Schlüssel: (SatrecArray, [Satrec], [NORAD])}; Spuren mit gleicher Bahnauswahl teilen sich eine Gruppe."""
    from satellite_archive import historical_models
    from opensky_archive import ArchiveAccessError
    from sgp4.api import SatrecArray
    groups, missing = {}, 0
    for tr in trails:
        try:
            models, _, _ = historical_models(tr['cam'], dict(settings, _archive_time=tr['cam']))
        except (ArchiveAccessError, OSError, ValueError):
            models = []
        recs = [m.model for m, _ in models]
        if not recs:
            tr['group'] = None; missing += 1; continue
        # Gruppe nur bei völlig gleicher Auswahl (Nummern und Epochen) – über UTC-Mitternacht bzw. neuere ETH-TLEs ändern sich die Bahnen
        key = hash(tuple((r.satnum, r.jdsatepoch, r.jdsatepochF) for r in recs))
        if key not in groups:
            groups[key] = (SatrecArray(recs), recs, np.array([r.satnum for r in recs]))
        tr['group'] = key
    return groups, missing


# ---------------------------------------------------------------- Rückprojektion ---
def back_project(trails, groups, centers, region_km, clock_s, tick=None, stop=None):
    """Bodenpunkte aller Paare (Spur, Bahn) → Liste je Spur: Arrays lat, lon, c_lo, c_hi, norad (Versätze relativ zum übernommenen)."""
    o3 = _o3()
    clat = np.array([c['lat'] for c in centers]); clon = np.array([c['lon'] for c in centers])

    def near(lat, lon, km):
        d = _haversine_km(lat[..., None], lon[..., None], clat, clon)
        return d.min(-1) <= km

    cos_c = math.cos(math.radians(COARSE_ANGLE_DEG)); sin_alt_c = math.sin(math.radians(MIN_ALT_DEG - 5))
    pairs = {}                                              # (Spur, Index) → [t_min, t_max]
    for key, (arr, recs, norads) in groups.items():
        members = [j for j, tr in enumerate(trails) if tr.get('group') == key]
        if not members: continue
        windows = {j: (trails[j]['start'] - clock_s - 2 * TIME_TOL_S, trails[j]['start'] + trails[j]['dur'] + clock_s + 2 * TIME_TOL_S) for j in members}
        epochs = sorted({round(t / COARSE_S) * COARSE_S for j in members
                         for t in np.arange(windows[j][0] - COARSE_S, windows[j][1] + 2 * COARSE_S, COARSE_S)})
        for chunk in range(0, len(epochs), 24):
            ts = np.array(epochs[chunk:chunk + 24])
            if stop: stop()
            if tick: tick(f'Bahnen rückprojizieren ({min(len(epochs), chunk + 24)}/{len(epochs)} Zeitpunkte)')
            err, r, v = arr.sgp4(*_jd(ts))
            r, v = np.asarray(r), np.asarray(v)
            for k, t in enumerate(ts):
                active = [j for j in members if windows[j][0] - COARSE_S <= t <= windows[j][1] + COARSE_S]
                if not active: continue
                theta = float(o3.gmst(t))
                sun = o3.rot_z(theta, o3.sun_d(t))
                good = err[:, k] == 0
                rk, vk = r[:, k], v[:, k]
                lit = _sunlit(rk, sun) & good
                for j in active:
                    tr = trails[j]
                    d = tr['matrix'] @ tr['mid']
                    g, rho, ok = ground_points(rk, d)
                    ok &= lit
                    if not ok.any(): continue
                    ok &= (_normal(g) @ d) >= sin_alt_c
                    idx = np.nonzero(ok)[0]
                    if not len(idx): continue
                    lat, lon = geodetic(g[idx], theta)
                    inr = near(lat, lon, region_km + COARSE_MARGIN_KM)
                    idx = idx[inr]
                    if not len(idx): continue
                    w = _motion(rk[idx], vk[idx], g[idx], tr['matrix'])
                    rate = np.linalg.norm(w, axis=-1)
                    cosang = np.abs(w @ tr['dir']) / np.maximum(rate, 1e-12)
                    fast = tr['length'] / np.maximum(rate, 1e-12) <= 1.5 * tr['dur'] + 2
                    for i in idx[(cosang >= cos_c) & fast]:
                        p = pairs.get((j, i))
                        pairs[(j, i)] = [t, t] if p is None else [min(p[0], t), max(p[1], t)]
                if len(pairs) > MAX_PAIRS:
                    raise ValueError(f'Zu viele mögliche Bahnen ({len(pairs)}) – Suchgebiet verkleinern.')
    return fine_points(trails, groups, pairs, centers, region_km, clock_s, tick, stop), len(pairs)


def samples(tr, rec, ts, sun=None):
    """Bodenpunkte eines Paars (Spur, Bahn) zu Zeiten ts samt Prüfungen → Felder je Zeitpunkt (`ok` = alle Prüfungen bestanden)."""
    o3 = _o3()
    cos_f = math.cos(math.radians(ANGLE_DEG)); sin_alt = math.sin(math.radians(MIN_ALT_DEG))
    e, r, v = rec.sgp4_array(*_jd(ts))
    r, v = np.asarray(r), np.asarray(v)
    theta = o3.gmst(ts)
    if sun is None:
        tm = float(np.median(ts))
        sun = o3.rot_z(float(o3.gmst(tm)), o3.sun_d(tm))          # Sonne in TEME (inertial, über Minuten fest)
    d = tr['matrix'] @ tr['mid']
    g, rho, ok = ground_points(r, d)
    ok &= (np.asarray(e) == 0) & _sunlit(r, sun)
    ok &= (_normal(g) @ d) >= sin_alt
    lat, lon = geodetic(g, theta)
    w = _motion(r, v, g, tr['matrix'])
    rate = np.linalg.norm(w, axis=-1)
    ok &= np.abs(w @ tr['dir']) / np.maximum(rate, 1e-12) >= cos_f
    pas = tr['length'] / np.maximum(rate, 1e-12)                  # Dauer des Durchgangs durch die Spur
    ok &= pas <= 1.2 * tr['dur'] + 1                              # auch „ganz im Bild“ kann ein kurzes Aufblitzen sein
    # Versätze, bei denen der Durchgang (Spurmitte zur Zeit t, Dauer pas) in die Belichtung fällt
    c_lo = ts + pas / 2 - tr['start'] - tr['dur'] - TIME_TOL_S
    c_hi = ts - pas / 2 - tr['start'] + TIME_TOL_S
    ok &= c_hi >= c_lo
    # Spielraum am Boden: Querablage der strengen Toleranz in der Entfernung des Satelliten (ferne Bahnen: großer Bereich)
    tol = np.maximum(TOL_KM, rho * ANG_TOL_ARCSEC / 206264.806)
    return dict(ok=ok, lat=lat, lon=lon, c_lo=c_lo, c_hi=c_hi, rho=rho, tol=tol)


def fine_points(trails, groups, pairs, centers, region_km, clock_s, tick=None, stop=None):
    """Je Paar 1-s-Schritte um die Vorauswahl → gültige Stimmen (Bodenpunkt, Versatzintervall, Spielraum, Bahnstrecke je Schritt)."""
    clat = np.array([c['lat'] for c in centers]); clon = np.array([c['lon'] for c in centers])
    keys = ('lat', 'lon', 'c_lo', 'c_hi', 'tol', 'seg', 'norad', 't', 'rec')
    out = [{k: [] for k in keys} for _ in trails]
    by_trail = {}
    for (j, i), (t0, t1) in pairs.items():
        by_trail.setdefault(j, []).append((i, t0, t1))
    for n, (j, items) in enumerate(by_trail.items()):
        if stop: stop()
        if tick and n % 4 == 0: tick(f'Spur {trails[j]["id"]}: {len(items)} mögliche Bahnen genau prüfen')
        tr = trails[j]
        _, recs, norads = groups[tr['group']]
        lo_w, hi_w = tr['start'] - clock_s - TIME_TOL_S - tr['dur'], tr['start'] + 2 * tr['dur'] + clock_s + TIME_TOL_S
        o = out[j]
        for i, t0, t1 in items:
            ts = np.arange(max(lo_w, t0 - COARSE_S), min(hi_w, t1 + COARSE_S) + FINE_S / 2, FINE_S)
            if len(ts) < 2: continue
            s = samples(tr, recs[i], ts)
            # Bahnstrecke am Boden je Schritt (für die Dichte der Stimmen)
            x, y = s['lon'] * 111.195 * np.cos(np.radians(s['lat'])), s['lat'] * 111.195
            seg = np.minimum(np.hypot(np.gradient(x), np.gradient(y)), 50.0)
            ok = s['ok'] & (s['c_hi'] >= -clock_s) & (s['c_lo'] <= clock_s)
            ok &= _haversine_km(s['lat'][:, None], s['lon'][:, None], clat, clon).min(-1) <= region_km
            if not ok.any(): continue
            for k in ('lat', 'lon', 'c_lo', 'c_hi', 'tol'): o[k].append(s[k][ok])
            o['seg'].append(seg[ok]); o['t'].append(ts[ok])
            o['norad'].append(np.full(ok.sum(), norads[i])); o['rec'].append(np.full(ok.sum(), i))
    for o in out:
        for k in keys:
            o[k] = np.concatenate(o[k]) if o[k] else np.zeros(0)
        o['norad'] = o['norad'].astype(np.int64); o['rec'] = o['rec'].astype(np.int64)
    return out


# ---------------------------------------------------------------- Abstimmen ---
class Grid:
    """Grobes Gitter der ersten Stufe: Zellen `CELL_KM` (Sinusprojektion) × Versatzklassen `CLOCK_BIN_S`."""
    def __init__(self, centers, region_km, clock_s):
        self.lat0 = float(np.mean([c['lat'] for c in centers])); self.lon0 = float(np.mean([c['lon'] for c in centers]))
        self.clock_s = clock_s
        self.nc = int(math.ceil(2 * clock_s / CLOCK_BIN_S)) + 1
        # Gitter deckt alle Suchgebiete ab (Mitte = Mittel der Orte; weit auseinanderliegende Orte vergrößern es)
        reach = max(float(_haversine_km(self.lat0, self.lon0, c['lat'], c['lon'])) for c in centers)
        self.off = int(math.ceil((region_km + reach + 2 * CELL_KM) / CELL_KM * 1.6)) + 2   # x bis ~1/cos(Breite) breiter
        self.n = 2 * self.off + 1

    def xy(self, lat, lon):
        lon = (np.asarray(lon) - self.lon0 + 180) % 360 - 180
        return lon * 111.195 * np.cos(np.radians(lat)), (np.asarray(lat) - self.lat0) * 111.195

    def latlon(self, x, y):
        lat = self.lat0 + y / 111.195
        return lat, self.lon0 + x / (111.195 * max(1e-6, math.cos(math.radians(lat))))

    def keys(self, pts):
        """Block-Schlüssel (überlappende 2×2×2-Blöcke aus Versatzklasse, x, y) aller Stimmen einer Spur."""
        if not len(pts['lat']): return np.zeros(0, np.int64)
        x, y = self.xy(pts['lat'], pts['lon'])
        cx = np.floor(x / CELL_KM).astype(np.int64) + self.off
        cy = np.floor(y / CELL_KM).astype(np.int64) + self.off
        inside = (cx >= 1) & (cx < self.n) & (cy >= 1) & (cy < self.n)           # nie an den Rand schieben (falsche Häufungen)
        if not inside.all():
            pts = {k: v[inside] for k, v in pts.items()}; cx, cy = cx[inside], cy[inside]
            if not len(cx): return np.zeros(0, np.int64)
        lo = np.clip(np.floor((pts['c_lo'] + self.clock_s) / CLOCK_BIN_S).astype(np.int64), 0, self.nc - 1)
        hi = np.clip(np.floor((pts['c_hi'] + self.clock_s) / CLOCK_BIN_S).astype(np.int64), 0, self.nc - 1)
        reps = hi - lo + 1
        idx = np.repeat(np.arange(len(lo)), reps)
        ci = lo[idx] + (np.arange(reps.sum()) - np.repeat(np.cumsum(reps) - reps, reps))
        cx, cy = cx[idx], cy[idx]
        out = [(np.maximum(ci - a, 0) * self.n + (cx - b)) * self.n + (cy - e) for a in (0, 1) for b in (0, 1) for e in (0, 1)]
        return np.unique(np.concatenate(out))

    def center(self, key):
        cy = key % self.n; rest = key // self.n
        cx = rest % self.n; ci = rest // self.n
        c = (ci + 1) * CLOCK_BIN_S - self.clock_s                    # Mitte des Blocks (2 Klassen, 2 × 2 Zellen)
        lat, lon = self.latlon((cx - self.off + 1) * CELL_KM, (cy - self.off + 1) * CELL_KM)
        return float(lat), float(lon), float(c)


def coarse_blocks(pts, centers, region_km, clock_s, top=None):
    """Erste Stufe: Blöcke mit den meisten verschiedenen Spuren (nur Kandidaten für die feine Stufe – grob zu ungenau für eine Aussage)."""
    top = top or TOP_BLOCKS
    grid = Grid(centers, region_km, clock_s)
    keys = [k for k in (grid.keys(p) for p in pts) if len(k)]
    if not keys: return []
    uk, cnt = np.unique(np.concatenate(keys), return_counts=True)
    out = []
    for k in np.argsort(-cnt, kind='stable'):
        if cnt[k] < MIN_TRAILS or len(out) >= top: break
        lat, lon, c = grid.center(uk[k])
        if any(_haversine_km(lat, lon, q[0], q[1]) < CELL_KM and abs(c - q[2]) <= CLOCK_BIN_S for q in out): continue
        out.append((lat, lon, c, int(cnt[k])))
    return out


def _poisson_tail(k, lam):
    """P(X ≥ k) für X ~ Poisson(λ)."""
    if k <= 0: return 1.0
    term = math.exp(-lam); cdf = term
    for i in range(1, k):
        term *= lam / i; cdf += term
    return max(0.0, 1.0 - cdf)


def trials(region_km, clock_s):
    """Zahl unabhängiger Prüfstellen im Suchraum (Ort im Raster 2·TOL_KM, Versatz in Schritten 2·TIME_TOL_S)."""
    return max(1.0, math.pi * region_km ** 2 / (2 * TOL_KM) ** 2) * max(1.0, (2 * clock_s + 2 * TIME_TOL_S) / (2 * TIME_TOL_S))


def local(trails, groups, pts, block, region_km, clock_s):
    """Zweite Stufe um einen groben Block: Raster `FINE_KM` × 1 s, je Paar Bahnpunkte alle 0,25 s. Eine Spur deckt einen
    Rasterpunkt ab, wenn ihre Bodenspur höchstens den Spielraum (≈ strenge Toleranz quer) entfernt vorbeiläuft und der Versatz
    passt. Zufallsniveau λ = Σ je Spur Anteil der abgedeckten Fläche × Versätze im Umkreis `LOCAL_KM` (aus allen Stimmen)."""
    lat0, lon0, c0, _ = block
    H = BOX_KM
    kx = 111.195 * math.cos(math.radians(lat0))
    to_xy = lambda lat, lon: (((np.asarray(lon) - lon0 + 180) % 360 - 180) * kx, (np.asarray(lat) - lat0) * 111.195)
    gx = np.arange(-H, H + FINE_KM / 2, FINE_KM)
    GX, GY = np.meshgrid(gx, gx); GX, GY = GX.ravel(), GY.ravel()
    cs = np.arange(max(-clock_s, c0 - BOX_S), min(clock_s, c0 + BOX_S) + 0.5, 1.0)
    if not len(cs): return None
    covers, options = [], []
    for j, p in enumerate(pts):
        if not len(p['lat']): continue
        x, y = to_xy(p['lat'], p['lon'])
        near = (np.abs(x) <= H + 20) & (np.abs(y) <= H + 20) & (p['c_hi'] >= cs[0]) & (p['c_lo'] <= cs[-1])
        if not near.any(): continue
        tr = trails[j]; recs = groups[tr['group']][1]
        cover_j = np.zeros((len(GX), len(cs)), bool)
        for i in np.unique(p['rec'][near]):
            tt = p['t'][near & (p['rec'] == i)]
            s = samples(tr, recs[i], np.arange(tt.min() - 2, tt.max() + 2.01, 0.25))
            ok = s['ok']
            if not ok.any(): continue
            sx, sy = to_xy(s['lat'][ok], s['lon'][ok])
            dist = np.hypot(GX[:, None] - sx[None, :], GY[:, None] - sy[None, :])          # Rasterpunkte × Bahnpunkte
            within = dist <= (s['tol'][ok] + 0.9)[None, :]                                    # + halber Abstand der Bahnpunkte
            if not within.any(): continue
            cm = (cs[None, :] >= s['c_lo'][ok][:, None]) & (cs[None, :] <= s['c_hi'][ok][:, None])   # Bahnpunkte × Versätze
            cov = (within.astype(np.float32) @ cm.astype(np.float32)) > 0
            if cov.any():
                cover_j |= cov
                options.append((j, int(groups[tr['group']][2][i]), int(i), cov))
        if cover_j.any(): covers.append(cover_j)
    if not covers: return None
    count = np.sum(covers, axis=0)
    bp, bc = np.unravel_index(int(np.argmax(count)), count.shape)
    run = np.nonzero(count[bp] == count[bp, bc])[0]
    bc = int(run[len(run) // 2])                          # Spuren ohne Zeitinformation: Mitte des passenden Versatzbereichs
    # verschiedene NORAD-Nummern (derselbe Satellit zählt für zwei Spuren nur einmal); Spuren mit wenigen Möglichkeiten zuerst
    here = {}
    for j, norad, i, cov in options:
        if cov[bp, bc]: here.setdefault(j, []).append((norad, i))
    used, members = set(), []
    for j, opts in sorted(here.items(), key=lambda kv: len(kv[1])):
        pick = next(((n, i) for n, i in opts if n not in used), None)
        if pick is None: continue
        used.add(pick[0]); members.append(dict(trail=j, norad=pick[0], rec=pick[1]))
    lat_b = lat0 + GY[bp] / 111.195; lon_b = lon0 + GX[bp] / kx
    lam = 0.0
    vol = math.pi * LOCAL_KM ** 2 * (2 * clock_s + 2 * TIME_TOL_S)
    for p in pts:
        if not len(p['lat']): continue
        m = _haversine_km(p['lat'], p['lon'], lat_b, lon_b) <= LOCAL_KM
        span = (np.minimum(p['c_hi'][m], clock_s) - np.maximum(p['c_lo'][m], -clock_s)).clip(0)
        cover = float(np.sum(p['seg'][m] * 2 * (p['tol'][m] + 0.9) * span))
        lam += 1 - math.exp(-cover / vol)
    k = len(members)
    false = min(1.0, trials(region_km, clock_s) * _poisson_tail(k, lam))
    return dict(lat=float(lat_b), lon=float(lon_b), clock=float(cs[bc]), support=k, trails_here=int(count[bp, bc]),
                chance=round(lam, 3), false_alarm=false, members=members, coarse=block[3])


def vote(trails, groups, pts, centers, region_km, clock_s, tick=None, stop=None):
    """Grobe Blöcke → feine Prüfung je Block → Häufungen (sortiert nach Fehlalarm-Wahrscheinlichkeit)."""
    blocks = coarse_blocks(pts, centers, region_km, clock_s)
    clusters = []
    for n, b in enumerate(blocks):
        if stop: stop()
        if tick and n % 5 == 0: tick(f'Häufungen fein prüfen ({n + 1}/{len(blocks)})')
        c = local(trails, groups, pts, b, region_km, clock_s)
        if not c or c['support'] < MIN_TRAILS: continue
        if any(_haversine_km(c['lat'], c['lon'], q['lat'], q['lon']) < 2 * TOL_KM and abs(c['clock'] - q['clock']) < 2 * TIME_TOL_S for q in clusters):
            continue
        clusters.append(c)
    clusters.sort(key=lambda q: (q['false_alarm'], -q['support']))
    return blocks, clusters


# ---------------------------------------------------------------- Verfeinern ---
def refine(sitefit, cluster, trails, groups, entries, settings, tick=None):
    """Häufung → sitefit (Ort + Uhr) → strenger Abgleich aller Spuren am Ergebnis → zweite Rückrechnung."""
    from rematch_sites import check, _score, _night_fit
    from catalog_matching import night_key
    obs = []
    for m in cluster['members']:
        tr = trails[m['trail']]
        rec = groups[tr['group']][1][m['rec']]
        obs.append(dict(sat=rec, trail=tr['item']['world_path'], start=tr['start'] + cluster['clock'], duration=tr['dur'],
                        inside=tr['inside'], label=f'{tr["id"]} NORAD {m["norad"]}', id=tr['id']))
    site0 = dict(lat=cluster['lat'], lon=cluster['lon'], alt_m=0.0)
    first = sitefit.fit(obs, site0)
    lat, lon, clock = cluster['lat'], cluster['lon'], cluster['clock']
    accuracy = 5000.0
    if first.get('ok'):
        lat, lon, clock = first['lat'], first['lon'], cluster['clock'] + first['clock_s']
        accuracy = max(300.0, float(first['accuracy_m']))
    base = trails[0]['base'] if trails else 0.0
    nights = sorted({night_key(i['timestamp']) for _, i in entries if isinstance(i.get('timestamp'), (int, float))})

    def strict(lat, lon, clock, accuracy):
        s = dict(settings, clock_offsets=dict(settings.get('clock_offsets') or {}, **{n: base + clock for n in nights}))
        site = dict(lat=lat, lon=lon, alt_m=0.0, accuracy_m=accuracy, name='Zurückgerechnet', origin='global')
        res = []
        for f, i in entries:
            if tick: tick(f'{i.get("id")}: strenger Abgleich am zurückgerechneten Ort')
            res.append((f, i, check(i, site, s)))
        return res, site

    triples, site = strict(lat, lon, clock, accuracy)
    score = _score(sitefit, triples)
    final = _night_fit(sitefit, triples, site) if score['sure'] >= sitefit.MIN_TRAILS else None
    out = dict(cluster_lat=cluster['lat'], cluster_lon=cluster['lon'], cluster_clock_s=round(cluster['clock'], 1),
               support=cluster['support'], false_alarm=cluster['false_alarm'], first_ok=bool(first.get('ok')),
               sure=score['sure'], compatible=score['compatible'], rms=score['rms'], lat=lat, lon=lon, clock_s=clock,
               accuracy_m=accuracy, trails=first.get('trails'), timed=first.get('timed'),
               clock_sigma_s=first.get('clock_sigma_s'), ellipse_km=first.get('ellipse_km'), rms_after_arcsec=first.get('rms_after_arcsec'))
    if final and final.get('ok') and final['trails'] >= score['sure'] - 1:
        out.update(lat=final['lat'], lon=final['lon'], clock_s=clock + final['clock_s'], accuracy_m=max(50.0, float(final['accuracy_m'])),
                   trails=final['trails'], timed=final['timed'], clock_sigma_s=final['clock_sigma_s'], ellipse_km=final['ellipse_km'],
                   rms_after_arcsec=final['rms_after_arcsec'], dropped=final.get('dropped'))
    if not out['timed']:
        out['clock_sigma_s'] = max(float(out.get('clock_sigma_s') or 0), TIME_TOL_S)
    out['matches'] = [dict(id=i.get('id'), name=(sitefit._sure(s or {}) or {}).get('name'), norad=(sitefit._sure(s or {}) or {}).get('norad_id'))
                      for _, i, s in triples if sitefit._sure(s or {})]
    out['_triples'] = triples
    return out


# ---------------------------------------------------------------- Ablauf ---
def search_night(sitefit, entries, settings, centers, region_km=REGION_KM, clock=True, tick=None, before_sure=0):
    """Eine Nacht ohne Vorwissen über den Ort. Ergebnis mit `proposal` {lat, lon, accuracy_m, clock_s, …} oder `reason`."""
    from archive_sources import check_stop
    t_begin = time.monotonic()
    stop = lambda: check_stop(settings)
    out = dict(region_km=region_km, tracks=len(entries), before=before_sure, stages=[])
    if not centers:
        return dict(out, reason='Keine Orte in der Ortsliste – das Suchgebiet ergibt sich aus ihnen.')
    trails = prepare(sitefit, entries, settings)
    if len(trails) < MIN_TRAILS:
        return dict(out, reason=f'Zu wenige prüfbare Spuren ({len(trails)}, nötig {MIN_TRAILS}).')
    groups, missing = load_orbits(trails, settings)
    out['unchecked'] = missing
    usable = [t for t in trails if t.get('group') is not None]
    if len(usable) < MIN_TRAILS:
        return dict(out, reason='Bahndaten fehlen lokal für die meisten Spuren.')
    stages = [s for s in CLOCK_STAGES_S if clock or s <= CLOCK_STAGES_S[0]]
    best = None
    for clock_s in stages:
        t0 = time.monotonic()
        pts, n_pairs = back_project(usable, groups, centers, region_km, clock_s, tick, stop)
        blocks, clusters = vote(usable, groups, pts, centers, region_km, clock_s, tick, stop)
        st = dict(clock_s=clock_s, pairs=n_pairs, votes=int(sum(len(p['lat']) for p in pts)), blocks=len(blocks), clusters=len(clusters),
                  top=[{k: (round(c[k], 6) if isinstance(c[k], float) else c[k]) for k in ('support', 'chance', 'false_alarm', 'clock')} for c in clusters[:5]])
        out['_clusters'] = clusters
        out['stages'].append(st)
        good = [c for c in clusters if c['support'] >= MIN_TRAILS and c['false_alarm'] <= MAX_FALSE][:MAX_CLUSTERS]
        tried = []
        for c in good:
            stop()
            r = refine(sitefit, c, usable, groups, entries, settings, tick)
            tried.append(r)
            if best is None or (r['sure'], -(r['rms'] or 1e9)) > (best['sure'], -(best['rms'] or 1e9)):
                best = r
        st['refined'] = [{k: r[k] for k in ('support', 'false_alarm', 'sure', 'rms')} for r in tried]
        st['seconds'] = round(time.monotonic() - t0, 1)
        if best and best['sure'] >= MIN_TRAILS and best['sure'] > before_sure:
            break
        if not good and clusters:
            c = clusters[0]
            st['reason'] = (f'Beste Häufung {c["support"]} verschiedene Spuren an einem Ort, zufällig wären dort etwa {c["chance"]:.1f} zu erwarten '
                            f'(Fehlalarm-Wahrscheinlichkeit im ganzen Suchgebiet {min(100, round(100 * c["false_alarm"]))} %).')
    out['seconds'] = round(time.monotonic() - t_begin, 1)
    if best and best['sure'] >= MIN_TRAILS and best['sure'] > before_sure:
        out['proposal'] = {k: v for k, v in best.items() if not k.startswith('_')}
        out['_triples'] = best['_triples']
        out['confidence'] = round(1 - best['false_alarm'], 4)
    else:
        last = out['stages'][-1] if out['stages'] else {}
        if best:
            out['reason'] = f'Häufung gefunden, aber am zurückgerechneten Ort passen streng nur {best["sure"]} Spuren eindeutig (bisher {before_sure}).'
        else:
            out['reason'] = last.get('reason') or ('Keine Häufung von Bodenpunkten verschiedener Spuren – '
                                                   f'innerhalb von {region_km:g} km um die Listenorte passt kein Ort'
                                                   + (' (Uhr ± 20 min geprüft).' if clock else '.'))
    return out
