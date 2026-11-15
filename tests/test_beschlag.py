"""Beschlagene Optik: Schleier-Metrik und Auswahl (Bilder), Glühmodell aus beschlagenen Nächten, Autopilot.

Anlass 14.11.2026 (Vespera ohne Tauheizung): die beschlagenen M-35-Aufnahmen (heller, ortsfester Schleier) wurden als
„Sensorglühen“ der Nacht gelernt; der Abzug prägte C 51 und M 1 einen dunklen Fleck mit Kante auf. Alle Daten synthetisch.
"""
from __future__ import annotations

import datetime as dt

import numpy as np

from astroflow.modules.stacking import calibration, frame_quick_metrics, pipeline, quality
from astroflow.modules.stacking.fits_io import write_fits
from astroflow.modules.telescope import autopilot as ap, fog, sim

H, W = 540, 960                    # Analysebild einer Vespera (1080×1920 Bayer, 2×2 gebinnt)
SIGMA = 14.0


def _frame(rng, fog_amp=0.0, gradient=0.0, vignette=0.0, stars=120):
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    img = np.full((H, W), 400.0, np.float32) + rng.normal(0, SIGMA, (H, W)).astype(np.float32)
    img += gradient * SIGMA * (xx / W)                                          # Lichtverschmutzung: eben
    r2 = ((xx - W / 2) / (W / 2)) ** 2 + ((yy - H / 2) / (H / 2)) ** 2
    img *= 1 - vignette * r2 / 2                                                # Vignettierung ohne Flats
    img += fog_amp * SIGMA * np.clip((0.5 - xx / W) / 0.5, 0, 1)               # Beschlag: Rampe von links bis zur Mitte (M 35)
    for _ in range(stars):
        x, y = rng.integers(8, W - 8), rng.integers(8, H - 8)
        img[y - 1 : y + 2, x - 1 : x + 2] += rng.uniform(300, 3000)
    return img


# ----------------------------------------------------------- Schleier-Metrik --- #
def test_veil_separates_fog_from_gradient_and_vignetting():
    rng = np.random.default_rng(1)
    clean = quality.veil_metrics(_frame(rng))["veil"]
    flat_lp = quality.veil_metrics(_frame(rng, gradient=20))["veil"]
    vign = quality.veil_metrics(_frame(rng, vignette=0.2))["veil"]
    fogged = quality.veil_metrics(_frame(rng, fog_amp=14))["veil"]
    assert max(clean, flat_lp, vign) < 1.0 <= quality.VEIL_MIN < fogged
    assert np.shape(quality.veil_metrics(_frame(rng))["veil_map"]) == quality.VEIL_GRID


def test_veil_needs_enough_tiles():
    assert quality.veil_metrics(np.zeros((180, 240), np.float32) + 100) == {}         # Demo-Größe: keine Aussage


def _metrics(rng, n, fog_amp=0.0, moving=False):
    out = {}
    for i in range(n):
        img = _frame(rng, fog_amp=fog_amp)
        if moving:                                                              # wandernde Wolke statt ortsfestem Schleier
            img = np.roll(img, (i * 97) % W, axis=1) if i % 2 else img[:, ::-1].copy()
            img = np.roll(img, (i * 53) % H, axis=0)
        m = {"n_stars": 120, "fwhm": 3.8, "ecc": 0.5, "bg": float(np.median(img)), "snr": 200.0, "grad": 1.0}
        m.update(quality.veil_metrics(img))
        out[f"f{i}"] = m
    return out


def test_selection_rejects_fogged_series_with_reason():
    rng = np.random.default_rng(2)
    met = _metrics(rng, 8, fog_amp=14)                         # ganze Serie beschlagen – relativ fiele nichts auf
    sel = quality.select_frames(met)
    assert all(not ok and reason == quality.VEIL_REASON for ok, reason, _ in sel.values())
    assert "beschlagen" in quality.VEIL_REASON


def test_selection_keeps_clear_and_few_or_moving_veils():
    rng = np.random.default_rng(3)
    assert all(ok for ok, _, _ in quality.select_frames(_metrics(rng, 8)).values())
    few = {**_metrics(rng, 8), **{f"n{k}": v for k, v in _metrics(rng, quality.VEIL_MIN_FRAMES - 1, fog_amp=14).items()}}
    assert not any(r == quality.VEIL_REASON for _, r, _ in quality.select_frames(few).values())
    moving = _metrics(rng, 8, fog_amp=14, moving=True)
    assert len(quality._veiled(moving)) < 8                     # Muster wandert → nicht alle als beschlagen


def test_older_metrics_without_veil_untouched():
    met = {f"f{i}": {"n_stars": 120, "fwhm": 3.8, "ecc": 0.5, "bg": 400.0} for i in range(10)}
    assert all(ok for ok, _, _ in quality.select_frames(met).values())


def test_frame_quick_metrics_reads_fits(tmp_path):
    rng = np.random.default_rng(4)
    raw = np.kron(_frame(rng, fog_amp=14), np.ones((2, 2), np.float32))          # Bayer-Rohbild in voller Auflösung
    p = tmp_path / "img-0001.fits"
    write_fits(p, np.clip(raw, 0, 65535).astype(np.uint16), {"BAYERPAT": "RGGB", "EXPTIME": 10.0, "INSTRUME": "Demo"})
    m = frame_quick_metrics(str(p))
    assert m["n_stars"] > 20 and m["fwhm"] and m["veiled"] and m["veil"] >= quality.VEIL_MIN


# ------------------------------------------------------------- Glühmodell --- #
def _night(rng, fog_amp=0.0, n=14):
    """Medianprofile einer Nacht (Bayer-Rohbilder) – mit Beschlag steht der Schleier ortsfest im Median."""
    frames = []
    for _ in range(n):
        f = np.kron(_frame(rng, fog_amp=fog_amp, stars=40), np.ones((2, 2), np.float32))[:400, :600]
        frames.append(f)
    return calibration.median_profiles([calibration.glow_profiles(f, True) for f in frames])


def test_glow_from_fogged_night_is_ignored(tmp_path):
    rng = np.random.default_rng(5)
    lib = calibration.GlowLibrary(tmp_path)
    lib.add("k", "2026-11-12", _night(rng))
    lib.add("k", "2026-11-13", _night(rng))
    lib.add("k", "2026-11-14", _night(rng, fog_amp=40))
    assert lib.implausible(lib._read("k")) == {"2026-11-14"}
    med, src = lib.medians("k", "2026-11-14")
    assert src.startswith("bibliothek (2 Nächte") and "unplausibel" in src
    assert calibration.glow_amplitude(calibration.model_from(med)) < 30
    assert lib.medians("k", "2026-11-12")[1] == "nacht"
    other, src = lib.medians("k", "2026-11-20")                  # Mittel der Kamera ohne die beschlagene Nacht
    assert src == "bibliothek (2 Nächte)" and calibration.glow_amplitude(calibration.model_from(other)) < 30


def test_single_night_is_never_implausible(tmp_path):
    rng = np.random.default_rng(6)
    lib = calibration.GlowLibrary(tmp_path)
    lib.add("k", "2026-11-14", _night(rng, fog_amp=40))
    assert lib.medians("k", "2026-11-14")[1] == "nacht"


def test_glow_change_triggers_reanalysis():
    assert pipeline._glow_changed(311.9, 5.3) and pipeline._glow_changed(5.0, 60.0)
    assert not pipeline._glow_changed(2.1, 9.1) and not pipeline._glow_changed(80.0, 95.0)
    assert not pipeline._glow_changed(None, 5.0)


# --------------------------------------------------------------- Autopilot --- #
T0 = dt.datetime(2026, 11, 20, 17, 0, tzinfo=dt.timezone.utc).timestamp()
SITE = {"id": "t1", "name": "Testgarten", "lat": 50.0, "lon": 10.0, "horizon": None}
M31 = ("M31", "M31", 10.68, 41.27)


def _pilot(dew_after_h=0.5, forecast=None):
    clock = sim.ManualClock(T0)
    d = sim.SimDriver(clock, sim.Scenario(dew_from=T0 + dew_after_h * 3600), model="vespera")
    items = [ap.Item(*M31, T0, T0 + 4 * 3600, hours=4)]
    p = ap.Autopilot(d, ap.Env(site=lambda n: SITE, plan=lambda n, m: [ap.Item(**vars(i)) for i in items], forecast=forecast),
                     None, clock=clock)
    d.connect()
    d.take_control()
    p.start(clock.now())
    return p, d, clock


def _run(p, clock, hours, step=20):
    for _ in range(int(hours * 3600 / step)):
        p.tick(clock.now())
        clock.advance(step)


def _clear(site, t0, t1):
    return {"hours": [{"t": t, "cloud": 5, "wind": 5, "dew_spread": 3.1} for t in range(int(t0), int(t1), 3600)]}


def test_autopilot_ends_target_when_optics_fog():
    p, d, clock = _pilot(forecast=_clear)
    _run(p, clock, 1.5)
    it = p.items[0]
    assert it.status == "abgebrochen" and it.why == "Optik beschlagen?"
    log = " | ".join(e["text"] for e in p.events)
    assert "Optik vermutlich beschlagen" in log and "Taupunktabstand laut Vorhersage 3,1 °C" in log
    assert p.hold and p.hold.kind == "fog" and d.parked
    # erkannt, bevor das Hindernis-Kriterium („fast nur verworfene Bilder“) zugeschlagen hätte
    assert "verdeckt" not in log
    assert it.real_end - (T0 + 0.5 * 3600) < 25 * 60


def test_autopilot_resume_clears_fog_hold():
    p, d, clock = _pilot(forecast=_clear)
    _run(p, clock, 1.5)
    assert p.fog_at is not None
    p.pause(clock.now())
    p.resume(clock.now())
    assert p.fog_at is None and not any(h.kind == "fog" for h in p.conditions(clock.now()))


def test_autopilot_cloudy_forecast_is_not_fog():
    def cloudy(site, t0, t1):
        return {"hours": [{"t": t, "cloud": 65, "wind": 5, "dew_spread": 5.0} for t in range(int(t0), int(t1), 3600)]}
    p, d, clock = _pilot(forecast=cloudy, dew_after_h=0.3)
    p.cfg["cloud_max"] = 90                                    # Wolkenwächter selbst greift nicht
    _run(p, clock, 1.0)
    assert p.items[0].why != "Optik beschlagen?" and p.fog_at is None
    assert "eher Wolken als beschlagene Optik" in " | ".join(e["text"] for e in p.events)


def test_autopilot_clear_night_keeps_going():
    p, d, clock = _pilot(dew_after_h=99, forecast=_clear)
    _run(p, clock, 1.5)
    assert p.items[0].status == "aktiv" and p.fog_at is None


def test_fogwatch_trend_vs_passing_clouds():
    w = fog.FogWatch()
    for i in range(60):                                        # 10 min klar, dann 10 min stetig schlechter
        t = T0 + i * 20
        bad = max(0.0, (i - 30) / 30)
        w.add(t, {"n_stars": 150 * (1 - 0.6 * bad), "fwhm": 3.8 * (1 + 0.6 * bad), "veil": 0.4})
    r = w.check(T0 + 59 * 20, T0)
    assert r and r["why"] == "trend" and r["stars"][1] < r["stars"][0]
    c = fog.FogWatch()
    for i in range(60):                                        # Wolkenzug: jedes zweite Bild schlecht
        c.add(T0 + i * 20, {"n_stars": 40 if i % 2 else 150, "fwhm": 3.8, "veil": 0.4})
    assert c.check(T0 + 59 * 20, T0) is None
    v = fog.FogWatch()
    for i in range(30):                                        # Schleier von Anfang an (M 35)
        v.add(T0 + i * 20, {"n_stars": 150, "fwhm": 3.7, "veil": 4.8})
    assert v.check(T0 + 29 * 20, T0)["why"] == "veil"
    assert "Optik vermutlich beschlagen" in fog.describe("M 35", v.check(T0 + 29 * 20, T0))


def test_short_series_measures_no_glow(tmp_path, monkeypatch):
    """Serie kürzer als GLOW_MIN_SPAN_S: Feld steht still, Himmel/Schleier wäre im Median – keine eigene Glühmessung."""
    import json

    from astroflow.core.config import load_settings
    from astroflow.modules.stacking import demo
    from astroflow.modules.stacking.db import DB
    from astroflow.modules.stacking.pipeline import Pipeline
    from astroflow.modules.stacking.reviewer import ReviewerConfig

    monkeypatch.setattr(calibration, "GLOW_MIN_SPAN_S", 30 * 24 * 3600)
    s = load_settings(tmp_path / "data")
    db = DB(s.db_path)
    pl = Pipeline(s, db, ReviewerConfig())
    demo.generate(s.input_dir / "n", n_lights=30, n_darks=0, n_flats=0)
    pl.scan()
    pid = db.one("SELECT id FROM projects")["id"]
    pl.process(pid)
    assert "glow_info" not in json.loads(db.one("SELECT options FROM projects WHERE id=?", (pid,))["options"])
    assert not list((s.work_dir / "glow").glob("*.npz"))


def test_frames_of_previous_attempt_are_dropped():
    """Zielwechsel während des Holens/Messens: Messwerte des alten Versuchs zählen nicht für das neue Ziel."""
    p, d, clock = _pilot(dew_after_h=99, forecast=_clear)
    _run(p, clock, 0.2)
    cur = p._current()
    assert cur is not None
    n = len(p.fog.frames)
    assert p.frame_seen(clock.now(), {"n_stars": 150, "fwhm": 3.8, "veil": 0.4}, cur, cur.real_start)
    assert not p.frame_seen(clock.now(), {"n_stars": 150, "fwhm": 3.8, "veil": 0.4}, cur, cur.real_start - 60)
    other = ap.Item(*M31, T0, T0 + 3600, hours=1)
    assert not p.frame_seen(clock.now(), {"n_stars": 150, "fwhm": 3.8, "veil": 0.4}, other, other.real_start)
    assert len(p.fog.frames) == n + 1


def test_bright_centred_galaxy_is_not_fog():
    """Große, helle Galaxie bei wenig Rauschen (lange Belichtung, gekühlte Kamera): Halo reicht über die Bildmitte hinaus,
    ist aber in der Mitte am hellsten – kein Schleier (CI-Befund: Mono-Demo M 31 wurde sonst ganz verworfen)."""
    rng = np.random.default_rng(7)
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    u, v = (xx - W / 2) / (W / 2), (yy - H / 2) / (H / 2)
    gal = 40 * SIGMA * np.exp(-np.hypot(u, v / 0.5) / 0.35)
    met = {}
    for i in range(8):
        img = _frame(rng) + gal
        m = {"n_stars": 120, "fwhm": 3.8, "ecc": 0.5, "bg": float(np.median(img)), "snr": 200.0, "grad": 1.0}
        m.update(quality.veil_metrics(img))
        met[f"g{i}"] = m
    assert all(m["veil_edge"] is False for m in met.values() if m["veil"] >= quality.VEIL_MIN)
    assert not any(r == quality.VEIL_REASON for _, r, _ in quality.select_frames(met).values())


def test_autopilot_without_forecast_does_not_abort():
    """Keine (aktuelle) Vorhersage: Beschlag und Wolken lassen sich nicht unterscheiden – kein Abbruch, nur ein Hinweis."""
    p, d, clock = _pilot(forecast=None)
    _run(p, clock, 1.5)
    assert p.items[0].why != "Optik beschlagen?" and p.fog_at is None
    assert "ohne aktuelle Wettervorhersage" in " | ".join(e["text"] for e in p.events)


def _glow_pipeline(tmp_path):
    import json

    from astroflow.core.config import load_settings
    from astroflow.modules.stacking import demo
    from astroflow.modules.stacking.db import DB
    from astroflow.modules.stacking.pipeline import Pipeline
    from astroflow.modules.stacking.reviewer import ReviewerConfig

    s = load_settings(tmp_path / "data")
    db = DB(s.db_path)
    pl = Pipeline(s, db, ReviewerConfig())
    demo.generate(s.input_dir / "n", n_lights=30, n_darks=0, n_flats=0)
    pl.scan()
    pid = db.one("SELECT id FROM projects")["id"]
    pl.process(pid)
    opts = lambda: json.loads(db.one("SELECT options FROM projects WHERE id=?", (pid,))["options"])
    return pl, db, pid, opts


def test_glow_pending_kept_while_raw_frames_offline(tmp_path, monkeypatch):
    """Glühmodell einer Nacht ändert sich, ihre Rohbilder sind gerade nicht erreichbar: Nacht bleibt vorgemerkt und wird
    nachgeholt, sobald die Platte wieder da ist (Review PR #140)."""
    import json
    pl, db, pid, opts = _glow_pipeline(tmp_path)
    night = sorted(opts()["glow_info"])[0]
    info = {**opts()["glow_info"], night: {"quelle": "nacht", "adu": 500.0}}         # „alte“ Analyse mit falschem Modell
    db.x("UPDATE projects SET options=json_set(options,'$.glow_info',json(?)) WHERE id=?", (json.dumps(info), pid))
    rows = db.q("SELECT id FROM frames WHERE project_id=? AND night=?", (pid, night))
    gone = {r["id"] for r in rows[:2]}
    real = pl.offline
    monkeypatch.setattr(pl, "offline", lambda rs: {r["id"] for r in rs if r["id"] in gone})
    try:
        pl.process(pid)
    except Exception:
        pass                                                   # Platte fehlt: Lauf darf abbrechen, Markierung muss bleiben
    assert opts().get("glow_pending") == [night]
    monkeypatch.setattr(pl, "offline", real)
    for i in gone:                                             # Marke: verschwindet nur durch eine Neu-Analyse
        db.x("UPDATE frames SET metrics=json_set(metrics,'$.marke',1) WHERE id=?", (i,))
    pl.process(pid)
    assert "glow_pending" not in opts()
    after = {r["id"]: json.loads(r["metrics"]) for r in db.q("SELECT id, metrics FROM frames WHERE project_id=?", (pid,))}
    assert all("marke" not in after[i] for i in gone)
