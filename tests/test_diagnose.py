"""Gerätediagnose (diagnose.py): Nachführung aus den Registrierungsmatrizen, Stöße, Fokusdrift mit Luftmasse, Verkippung und
Wölbung des Bildfelds, Gerätetrend, Tutor-Befunde – alles synthetisch, ohne Netz."""
import datetime as dt
import json

import numpy as np
import pytest

from astroflow.core.config import load_settings
from astroflow.modules.stacking import demo, diagnose, quality, tutor
from astroflow.modules.stacking.db import DB
from astroflow.modules.stacking.pipeline import Pipeline
from astroflow.modules.stacking.platesolve import SolverConfig
from astroflow.modules.stacking.reviewer import ReviewerConfig

T0 = dt.datetime(2026, 9, 1, 21, 0, tzinfo=dt.timezone.utc)
STELLINA = {"id": "stellina", **tutor.DEVICES["stellina"]}
CAMERA = {"id": "camera", **tutor.DEVICES["camera"]}


def _frames(n, step=20.0, fwhm=6.0, ecc=0.4, **kw):
    out = []
    for i in range(n):
        m = {"fwhm": fwhm(i) if callable(fwhm) else fwhm, "ecc": ecc(i) if callable(ecc) else ecc, "n_stars": 120}
        out.append({"id": f"f{i}", "night": "2026-09-01", "t": T0 + dt.timedelta(seconds=i * step), "status": "selected",
                    "exptime": 10.0, "temp": None, "folder": None, "metrics": m, **kw})
    return out


def _links(frames, pos, rot=None):
    out = {}
    for i, f in enumerate(frames):
        a = np.radians(rot[i]) if rot is not None else 0.0
        M = np.eye(3)
        M[:2, :2] = [[np.cos(a), -np.sin(a)], [np.sin(a), np.cos(a)]]
        c = np.array([(480 - 1) / 2, (360 - 1) / 2])
        M[:2, 2] = pos[i] + c - M[:2, :2] @ c                   # Bildmitte landet bei c + pos
        out[f["id"]] = (1.0, M)
    return out


# ------------------------------------------------------------------ Nachführung ---
def test_tracking_segments_drift_and_smear():
    v = np.array([0.3, -0.12])
    fr = _frames(48)
    jumps = [(0, 0), (-40, -25), (-35, 30), (25, 35), (-30, -35), (5, 45), (-40, 0), (30, -40)]
    pos = np.array([np.array(jumps[i // 6]) + v * (i % 6) * 20 for i in range(48)])
    pos += np.random.default_rng(1).normal(0, 0.3, pos.shape)
    t = diagnose.tracking(fr, _links(fr, pos), 10.0, 1.24, (360, 480))
    assert t["recenter"] == 7 and t["measured"] == 8
    assert t["speed_px_s"] == pytest.approx(np.hypot(*v), rel=0.05)
    assert t["smear_px"] == pytest.approx(np.hypot(*v) * 10, rel=0.05)
    assert t["smear_ratio"] == pytest.approx(t["smear_px"] / 6.0, rel=0.01)
    assert t["speed_as_min"] == pytest.approx(np.hypot(*v) * 1.24 * 60, rel=0.05)
    assert t["jitter_px"] < 0.6 and t["coherence"] > 0.95
    assert t["direction"] == pytest.approx(np.degrees(np.arctan2(v[1], v[0])) % 360, abs=3)
    assert t["periodic"] is None and len(t["series"]) == 48


def test_tracking_rotation_and_periodic_error():
    """Lange Abschnitte (Montierung ohne Nachzentrieren): periodischer Fehler wird gefunden, Bilddrehung gemessen."""
    n, step = 120, 10.0
    fr = _frames(n, step=step)
    tt = np.arange(n) * step
    pos = np.c_[0.02 * tt + 2.5 * np.sin(2 * np.pi * tt / 300), 0.01 * tt]
    rot = 0.002 * tt                                             # 0,12°/min
    t = diagnose.tracking(fr, _links(fr, pos, rot), 10.0, None, (360, 480))
    assert t["measured"] == 1 and t["recenter"] == 0
    assert t["periodic"] and t["periodic"]["period_s"] == pytest.approx(300, rel=0.08)
    assert t["periodic"]["amp_px"] == pytest.approx(2.5, rel=0.3)
    assert t["rot_deg_min"] == pytest.approx(0.12, rel=0.05)
    assert "speed_as_min" not in t                               # ohne Maßstab nur Pixel


def test_events_groups_but_not_singles():
    bad = {10, 11, 12, 13, 14, 30}                               # Böe über 5 Aufnahmen + einzelne Seeing-Spitze
    fr = _frames(60, fwhm=lambda i: 12.0 if i in bad else 6.0 + 0.1 * np.sin(i), ecc=lambda i: 0.85 if i in bad else 0.4)
    clouds = _frames(1)[0]
    ev = diagnose.events(fr)
    assert len(ev["events"]) == 1 and ev["events"][0]["n"] == 5 and ev["frames"] == 6
    # verschwundene Sterne = Wolken, kein Stoß
    for i in (40, 41, 42):
        fr[i]["metrics"].update(fwhm=12.0, n_stars=20)
    assert len(diagnose.events(fr)["events"]) == 1
    assert clouds


# ------------------------------------------------------------------ Fokus ---
def test_focus_drift_over_time_and_temperature():
    n = 200                                                      # 2 h
    fr = _frames(n, step=36.0, fwhm=lambda i: 5.0 * (1 + 0.10 * i * 36 / 3600) + 0.05 * np.sin(i * 1.7))
    for i, f in enumerate(fr):
        f["temp"] = 14.0 - 4.0 * i / n                           # kühlt ab
    f = diagnose.focus(fr, None, 1.24, set())
    assert f["time"]["sig"] and f["time"]["pct_h"] == pytest.approx(10 * 5.0 / f["fwhm"] * 1.24, rel=0.25)
    assert f["temp"]["sig"] and f["temp"]["pct_c"] < 0 and f["unit"] == "as"
    found = diagnose.findings({"ok": True, "nights": [{"night": "2026-09-01", "focus": f}]}, STELLINA)
    fd = next(x for x in found if x["code"] == "focus_drift")
    assert "Sensortemperatur" in fd["text"] and "neue Beobachtung" in fd["tips"][0]


def test_seeing_from_airmass_is_not_focus_drift():
    """Objekt sinkt, die Sterne werden nur durch die Luftmasse größer → keine Fokusdrift (und auch kein „stabil“)."""
    from astroflow.modules.stacking.skyquality import airmass
    n = 200
    alt = np.linspace(60, 22, n)
    fr = _frames(n, step=36.0, fwhm=lambda i: 5.0 * airmass(alt[i]) ** 0.6)
    f = diagnose.focus(fr, alt, None, set())
    assert f["airmass"] and f["time"]["raw"] > 10 and abs(f["time"]["corr"]) < 2
    assert not f["time"]["sig"] and not f["time"]["stable"]
    codes = {x["code"] for x in diagnose.findings({"ok": True, "nights": [{"night": "2026-09-01", "focus": f}]}, STELLINA)}
    assert "focus_drift" not in codes and "focus_stable" not in codes


def test_refocus_mark_and_stable_focus():
    n = 200
    fr = _frames(n, step=36.0, fwhm=lambda i: (6.0 if i < 100 else 5.0) + 0.05 * np.sin(i))
    for i, f in enumerate(fr):
        f["folder"] = "01" if i < 100 else "02"
    f = diagnose.focus(fr, None, None, set())
    m = f["marks"][0]
    assert m["kind"] == "refocus" and m["change"] == pytest.approx(-16.7, abs=1.5)
    flat = diagnose.focus(_frames(n, step=36.0, fwhm=lambda i: 5.0 + 0.05 * np.sin(i)), None, None, set())
    assert flat["time"]["stable"] and not flat["time"]["sig"]
    codes = {x["code"] for x in diagnose.findings({"ok": True, "nights": [{"night": "n", "focus": flat}]}, STELLINA)}
    assert "focus_stable" in codes


# ------------------------------------------------------------------ Bildfeld ---
def _field(tilt=0.0, curv=0.0, H=600, W=900, n=900, seed=4):
    """Sternfeld (C, H, W) mit Sterngröße σ(x, y) = σ0 · (1 + tilt·u + curv·r²) (u: −1 links … +1 rechts)."""
    rng = np.random.default_rng(seed)
    img = rng.normal(100, 3, (H, W)).astype(np.float32)
    R = 9
    d = np.arange(-R, R + 1)
    for _ in range(n):
        x, y = rng.uniform(R + 2, W - R - 3), rng.uniform(R + 2, H - R - 3)
        u, v = (x - W / 2) / (W / 2), (y - H / 2) / (H / 2)
        sg = 1.4 * (1 + tilt * u + curv * (u * u + v * v) / 2)
        ix, iy = int(x), int(y)
        psf = np.outer(np.exp(-((d + iy - y) ** 2) / (2 * sg**2)), np.exp(-((d + ix - x) ** 2) / (2 * sg**2)))
        img[iy - R:iy + R + 1, ix - R:ix + R + 1] += 10 ** rng.uniform(3.0, 4.0) * psf / psf.sum()
    return np.stack([img] * 3), np.ones((H, W), np.float32)


def test_field_tilt_detected_with_side():
    mean, cov = _field(tilt=0.25)
    f = diagnose.field_stack(mean, cov)
    assert f["tilt_sig"] and f["tilt_pct"] > 20 and f["soft_side"] == "rechts"
    assert not f["curv_sig"]
    row = [v for v in f["map"][1] if v is not None]
    assert row[-1] > row[0]                                      # rechte Kacheln größer
    found = diagnose.findings({"ok": True, "nights": [], "field": f}, STELLINA)
    tl = next(x for x in found if x["code"] == "tilt")
    assert "rechts" in tl["text"] and "Hersteller" in tl["tips"][0]
    assert "Tilt-Adapter" in next(x for x in diagnose.findings({"ok": True, "nights": [], "field": f}, CAMERA) if x["code"] == "tilt")["tips"][0]


def test_field_flat_and_curved():
    flat = diagnose.field_stack(*_field())
    assert not flat["tilt_sig"] and not flat["curv_sig"] and flat["tilt_pct"] < 6
    curved = diagnose.field_stack(*_field(curv=0.5))
    assert curved["curv_sig"] and curved["curv_pct"] > 12 and not curved["tilt_sig"]
    assert any(x["code"] == "curvature" for x in diagnose.findings({"ok": True, "nights": [], "field": curved}, CAMERA))


def test_side_text_flips_vertically():
    # Stack-y wächst nach unten in den Daten, das fertige Bild ist vertikal gespiegelt (Norden oben)
    assert diagnose.side_text(0) == "rechts" and diagnose.side_text(180) == "links"
    assert diagnose.side_text(90) == "oben" and diagnose.side_text(270) == "unten"


def test_shape_metrics_new_fields():
    """Neue Frame-Metriken (ohne METRICS_VERSION-Erhöhung): Vorzugsrichtung länglicher Sterne und 3×3-Schärfekarte."""
    rng = np.random.default_rng(2)
    H, W = 300, 450
    img = rng.normal(100, 3, (H, W)).astype(np.float32)
    yy, xx = np.mgrid[-8:9, -8:9]
    a = np.radians(30)
    u, v = xx * np.cos(a) + yy * np.sin(a), -xx * np.sin(a) + yy * np.cos(a)
    psf = np.exp(-(u**2 / (2 * 2.6**2) + v**2 / (2 * 1.3**2)))
    for _ in range(120):
        x, y = rng.integers(12, W - 12), rng.integers(12, H - 12)
        img[y - 8:y + 9, x - 8:x + 9] += 3000 * psf
    m = quality.analyze_frame(img, False)
    assert m["ecc_pa"][0] == pytest.approx(30, abs=5) and m["ecc_pa"][1] > 0.6
    assert len(m["tiles"]) == 3 and all(len(r) == 3 for r in m["tiles"])
    assert quality.METRICS_VERSION == 2


# ------------------------------------------------------------------ Ende zu Ende ---
@pytest.fixture(scope="module")
def drift_env(tmp_path_factory):
    root = tmp_path_factory.mktemp("diag")
    s = load_settings(root / "data")
    s.delete_uploads = False
    demo.generate_drift(s.input_dir / "drift", tilt=0.3)
    db = DB(s.db_path)
    pl = Pipeline(s, db, ReviewerConfig(), SolverConfig(backend="off"))
    pl.scan()
    p = db.one("SELECT * FROM projects")
    rid = pl.process(p["id"])
    return s, db, pl, p, rid


def test_end_to_end_drift_tilt_and_temperature(drift_env):
    s, db, pl, p, rid = drift_env
    d = diagnose.compute(pl, p["id"])
    assert d["ok"] and d["v"] == diagnose.DIAG_VERSION and d["device"]["id"] == "stellina"
    assert d["temps"] == 36                                      # Stellina-Kopf TEMP wird gelesen
    n = d["nights"][0]
    t = n["tracking"]
    assert t["recenter"] == 5 and t["measured"] == 6
    assert t["speed_px_s"] == pytest.approx(np.hypot(*demo.DRIFT_V), rel=0.1)
    assert abs(t["rot_deg_min"]) < 0.05
    assert [m["kind"] for m in (n["focus"] or {"marks": []})["marks"]] in ([], ["refocus"])
    f = d["field"]
    assert f["tilt_sig"] and f["soft_side"] == "rechts", f
    # Neue Frame-Metriken aus der echten Analyse: Karten je Aufnahme bestätigen die Verkippung
    ff = d["field_frames"]
    assert ff and ff["frames"] >= 30 and ff["tilt_sig"] and "rechts" in ff["soft_side"]   # 3×3 Kacheln: Richtung grob


def test_insights_store_tutor_and_device_summary(drift_env):
    from astroflow.modules.stacking import dashboard

    s, db, pl, p, rid = drift_env

    class Ctx:
        services = {}

        class store:
            @staticmethod
            def get(k):
                return {}

    class Eng:
        pass
    eng = Eng()
    eng.db, eng.pl, eng.s, eng.ctx = db, pl, s, Ctx()
    from astroflow.modules.stacking.insights import Insights
    ins = Insights(eng)
    ins.diagnose(rid, lambda m: None)
    st = json.loads(db.one("SELECT stats FROM results WHERE id=?", (rid,))["stats"])
    assert st["diagnose"]["ok"] and tutor.diag_of(st)
    # Tutor-Bericht enthält die Gerätebefunde
    rep = tutor.build_report(db, rid, tutor.DEFAULTS | {"weather": False}, s.work_dir)
    codes = {f["code"] for f in rep["findings"]}
    assert "tilt" in codes and "tracking_drift" in codes
    # Gerätetrend: eine Nacht, noch kein Trend
    devs = diagnose.device_summary(db)
    assert len(devs) == 1 and devs[0]["id"] == "stellina" and len(devs[0]["nights"]) == 1 and devs[0]["trends"] == {}
    # Dashboard: Kurzfassung je Zeile
    info = dashboard._diag_info(st["diagnose"])
    assert info["tilt_sig"] and info["smear"] > 0


def test_device_trend_detects_worsening(tmp_path):
    db = DB(tmp_path / "t.db")
    for i in range(10):
        night = (dt.date(2024, 1, 1) + dt.timedelta(days=40 * i)).isoformat()
        db.x("INSERT INTO projects(id, key, name) VALUES(?,?,?)", (i + 1, f"k{i}", f"Obj {i}"))
        d = {"v": diagnose.DIAG_VERSION, "ok": True, "device": {"id": "stellina", "name": "Vaonis Stellina"}, "device_key": "stellina|x",
             "field": {"tilt_pct": 4.0 + 1.5 * i}, "nights": [{"night": night, "tracking": {"smear_ratio": 0.4, "speed_as_min": 20.0},
                                                             "focus": {"fwhm_corr": 3.0, "unit": "as"}}]}
        db.x("INSERT INTO results(project_id, created, stats) VALUES(?,?,?)", (i + 1, 0, json.dumps({"diagnose": d})))
    dv = diagnose.device_summary(db)[0]
    assert len(dv["nights"]) == 10
    assert dv["trends"]["tilt"]["worse"] and not dv["trends"]["fwhm"]["worse"]


def test_device_trend_uses_only_night_specific_tilt(tmp_path):
    """Ein Stack über mehrere Nächte ist kein Messwert je Nacht: ohne eigene Karten der Nacht keine Kippung im Trend."""
    db = DB(tmp_path / "t.db")
    db.x("INSERT INTO projects(id, key, name) VALUES(1, 'k', 'Obj')")
    nights = [{"night": "2026-01-01", "field_frames": {"tilt_pct": 7.0}}, {"night": "2026-02-01"}]
    d = {"v": diagnose.DIAG_VERSION, "ok": True, "device": {"id": "stellina", "name": "Vaonis Stellina"}, "device_key": "stellina|x",
         "field": {"tilt_pct": 15.0, "curv_pct": 3.0}, "nights": nights}
    db.x("INSERT INTO results(project_id, created, stats) VALUES(1, 0, ?)", (json.dumps({"diagnose": d}),))
    ns = diagnose.device_summary(db)[0]["nights"]
    assert [n["tilt"] for n in ns] == [7.0, None] and all(n["curv"] is None for n in ns)
