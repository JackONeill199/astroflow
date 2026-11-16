"""Lichtkurven veränderlicher Sterne (lightcurve.py): synthetische Bildfolge aus demo.generate_variable mit einem bekannten und
einem unbekannten Veränderlichen (bekannte Amplitude und Periode), einer Wolken-Aufnahme und übersättigten Sternen. Gaia und
VSX als Attrappen, kein Netz."""
import json
import math
import shutil
import time

import numpy as np
import pytest

from astroflow.core.config import load_settings
from astroflow.modules.stacking import demo, lightcurve, photometry
from astroflow.modules.stacking.db import DB
from astroflow.modules.stacking.pipeline import Pipeline, stack_dir
from astroflow.modules.stacking.platesolve import SolverConfig
from astroflow.modules.stacking.reviewer import ReviewerConfig
from astroflow.modules.stacking.stacking import StackState

RA0, DEC0, SCALE_AS, ZP = 150.0, 20.0, 1.2, 24.0


def _hdr(H, W):
    d = SCALE_AS / 3600
    return {"CTYPE1": "RA---TAN", "CTYPE2": "DEC--TAN", "CRVAL1": RA0, "CRVAL2": DEC0, "CRPIX1": (W + 1) / 2, "CRPIX2": (H + 1) / 2,
            "CD1_1": -d, "CD1_2": 0.0, "CD2_1": 0.0, "CD2_2": d}


def _g_from_v(v, c):
    """Umkehrung von lightcurve.gaia_v (G = V + (G − V)(BP−RP))."""
    return v + (-0.02704 + 0.01424 * c - 0.2156 * c ** 2 + 0.01426 * c ** 3)


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    root = tmp_path_factory.mktemp("lichtkurve")
    s = load_settings(root / "data")
    s.delete_uploads = False
    _, truth = demo.generate_variable(s.input_dir / "feld")
    db = DB(s.db_path)
    pl = Pipeline(s, db, ReviewerConfig(), SolverConfig(backend="off"))
    pl.scan()
    p = db.one("SELECT * FROM projects")
    rid = pl.process(p["id"])
    st = StackState.load(stack_dir(s.work_dir / f"p{p['id']}", None))
    H, W = st.sw.shape[1:]
    (s.work_dir / f"p{p['id']}" / "wcs.json").write_text(json.dumps({"key": f"{st.meta['ref_fid']}|{H}x{W}", "wcs": _hdr(H, W)}))
    # Gaia-Attrappe: wahre Lage im Referenzraster (= Stack) → Himmelskoordinaten über dieselbe WCS
    rows = db.q("SELECT id FROM frames WHERE kind='light' ORDER BY path")
    i_ref = [r["id"] for r in rows].index(st.meta["ref_fid"])
    stars = np.array(truth["stars"])
    sh, sw = truth["shape"]
    fh, fw = truth["frame"]
    th = np.deg2rad(truth["angle"][i_ref])
    c, s_ = np.cos(th), np.sin(th)
    cs = np.array([sw / 2, sh / 2]) + np.array(truth["shift"][i_ref])
    dx, dy = stars[:, 0] - cs[0], stars[:, 1] - cs[1]
    x, y = c * dx + s_ * dy + fw / 2, -s_ * dx + c * dy + fh / 2
    ra, dec = photometry.wcs_from({**_hdr(H, W), "NAXIS1": W, "NAXIS2": H}).pixel_to_world_values(x, y)
    col = 0.4 + 1.2 * stars[:, 3]
    v = ZP - 2.5 * np.log10(stars[:, 2])
    inside = (x > 2) & (y > 2) & (x < W - 3) & (y < H - 3)
    cat = {"source_id": [1000 + i for i in range(len(stars)) if inside[i]], "ra": np.asarray(ra)[inside], "dec": np.asarray(dec)[inside],
           "g": _g_from_v(v, col)[inside], "bp_rp": col[inside], "truncated": False}
    for k in ("parallax", "poe", "plx_err", "pmra", "pmdec"):
        cat[k] = np.full(int(inside.sum()), np.nan)
    j0 = truth["var"][0]
    vsx = [{"name": "V0001 Tst", "ra": float(np.asarray(ra)[j0]), "dec": float(np.asarray(dec)[j0]), "type": "RRC", "max": 13.4,
            "band": "V", "amp": 0.5, "period": truth["variables"][0]["period"], "epoch": truth["jd0"], "oid": 1, "quelle": "VSX"}]
    return s, db, pl, p, rid, truth, cat, vsx, {"x": x, "y": y, "inside": inside}


@pytest.fixture
def fakes(env, monkeypatch):
    s, db, pl, p, rid, truth, cat, vsx, _ = env
    calls = []
    monkeypatch.setattr(photometry, "gaia_field", lambda ra, dec, radius, cache_dir, **k: calls.append((ra, dec, radius)) or cat)
    monkeypatch.setattr(lightcurve, "vsx_field", lambda *a, **k: vsx)
    return calls


@pytest.fixture(scope="module")
def result(env):
    s, db, pl, p, rid, truth, cat, vsx, _ = env
    mp = pytest.MonkeyPatch()
    mp.setattr(photometry, "gaia_field", lambda *a, **k: cat)
    mp.setattr(lightcurve, "vsx_field", lambda *a, **k: vsx)
    try:
        return lightcurve.compute(pl, rid)
    finally:
        mp.undo()


def _by_gaia(out, sid):
    return next((e for e in out["sterne"] if e.get("gaia") == f"Gaia DR3 {sid}"), None)


def test_known_variable_amplitude_and_period(env, result):
    s, db, pl, p, rid, truth, cat, vsx, geo = env
    out = result
    assert out["ok"] and out["v"] == lightcurve.LC_VERSION
    e = next(e for e in out["sterne"] if e.get("name") == "V0001 Tst")
    assert e["bekannt"] and e["quelle"] == "VSX" and e["typ_de"].startswith("RR-Lyrae")
    assert e["signifikant"] and not e["mischlicht"]
    true = truth["variables"][0]
    assert e["amp_fit"] == pytest.approx(true["amp"], abs=0.06)
    assert e["eigene_periode"]["periode"] == pytest.approx(true["period"], rel=0.03)
    assert e["genauigkeit"] < 0.03
    # Zeiten: BJD liegt höchstens ~8,3 min neben JD, Phase passt zur Katalogepoche
    curves = lightcurve.load_curves(s.work_dir, p["id"], rid)
    f0 = curves["frames"][0]
    assert abs(f0["bjd"] - f0["jd"]) < 0.0058 and abs(f0["hjd"] - f0["jd"]) < 0.0058


def test_unknown_variable_is_only_possible(env, result):
    s, db, pl, p, rid, truth, cat, vsx, geo = env
    j = truth["var"][1]
    e = _by_gaia(result, 1000 + j)
    assert e is not None and e.get("moeglich") and not e["bekannt"] and e["name"] is None
    true = truth["variables"][1]
    assert e["eigene_periode"]["periode"] == pytest.approx(true["period"], rel=0.05)
    assert e["amp"] == pytest.approx(true["amp"], abs=0.08)
    # keine weiteren „möglichen Veränderlichen“: konstante Sterne bleiben flach
    others = [x for x in result["sterne"] if x.get("moeglich") and x.get("gaia") != f"Gaia DR3 {1000 + j}"]
    assert not others, [(x["gaia"], x["rms"], x["erwartet"], x["eta"]) for x in others]


def test_constant_stars_flat_and_accuracy(env, result):
    out = result
    ck = next(e for e in out["sterne"] if e.get("pruefstern"))
    assert ck["rms"] < 0.03 and not ck["signifikant"]
    assert 0.3 < ck["chi2"] < 3.0, ck                  # Fehlerbalken passen zur tatsächlichen Streuung
    assert out["genauigkeit"] and out["beste_genauigkeit"]["rms"] < 0.02
    assert out["ensemble"]["n"] >= 20
    assert out["filter"] == "TG" and out["aufnahmen"]["n"] >= 30


def test_saturated_and_cloud(env, result):
    s, db, pl, p, rid, truth, cat, vsx, geo = env
    for j in truth["sat"]:
        e = _by_gaia(result, 1000 + j)
        assert e is None or e["gesaettigt"]
    # Wolkenaufnahme: entweder schon bei der Auswahl verworfen oder hier als Lichtverlust markiert
    curves = lightcurve.load_curves(s.work_dir, p["id"], rid)
    fl = [f["flag"] for f in curves["frames"]]
    stacked = {r["id"] for r in db.q("SELECT id FROM frames WHERE stacked=1")}
    cloud_id = db.q("SELECT id FROM frames WHERE kind='light' ORDER BY path")[truth["cloud"]]["id"]
    if cloud_id in stacked:
        k = [f["id"] for f in curves["frames"]].index(cloud_id)
        assert fl[k] == "wolke"
    assert sum(1 for f in fl if f) <= 3


def test_differential_flags_clouds_and_variable_comps():
    rng = np.random.default_rng(3)
    nf, ns = 40, 60
    v = rng.uniform(10, 14, ns)
    col = rng.uniform(0.3, 1.5, ns)
    F = 10 ** (-0.4 * (v - 20)) * (1 + rng.normal(0, 0.005, (nf, ns)))
    F[7] *= 0.5                                         # Wolke: −0,75 mag in allen Sternen
    F[:, 3] *= 10 ** (-0.4 * 0.4 * np.sin(np.arange(nf) / 3))   # ein „Vergleichsstern“ schwankt
    xy = rng.uniform(0, 1000, (ns, 2))
    d = lightcurve.differential(F, np.full((nf, ns), 1.0), lightcurve.gaia_v(_g_from_v(v, col), col), col, xy, np.ones(ns, bool),
                                np.zeros(nf, int), np.zeros((nf, ns), bool))
    assert d["flags"][7] == "wolke" and (d["flags"] == "").sum() >= nf - 2
    assert not d["comp"][3] and d["n_comp_rejected"] >= 1
    ok = d["flags"] == ""
    rms = np.std(d["mag"][ok][:, 10])
    assert rms < 0.015
    assert np.nanmedian(d["mag"][ok][:, 10]) == pytest.approx(v[10], abs=0.02)


def test_binning_and_periodless():
    t = np.linspace(0, 0.1, 60)
    m = 12 + np.random.default_rng(1).normal(0, 0.02, 60)
    e = np.full(60, 0.02)
    fl = np.array([""] * 60, dtype=object)
    fl[5] = "wolke"
    bt, bm, be = lightcurve.bin_points(t, m, e, fl, 5)
    assert len(bt) == 12 and np.all(be < 0.02)
    assert lightcurve.period_search(t, m, e) is None   # reines Rauschen: keine Periode


def test_no_catalog_still_runs(env, monkeypatch, tmp_path):
    s, db, pl, p, rid, truth, cat, vsx, geo = env
    monkeypatch.setattr(photometry, "gaia_field", lambda *a, **k: cat)

    def down(*a, **k):
        raise RuntimeError("kein Netz")
    monkeypatch.setattr(lightcurve, "vsx_field", down)
    monkeypatch.setattr(lightcurve, "simbad_variables", down)
    known, why = lightcurve.known_variables(RA0, DEC0, 0.1, tmp_path)
    assert known == [] and "eigene Erkennung" in why


def test_export_and_api(env, result, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from astroflow.server import create_app
    s, db, pl, p, rid, truth, cat, vsx, geo = env
    db.x("UPDATE results SET stats=json_set(COALESCE(stats,'{}'),'$.lightcurves',json(?)) WHERE id=?", (json.dumps(result), rid))
    data = tmp_path / "data"
    shutil.copytree(s.data_dir, data)
    monkeypatch.setenv("ASTROFLOW_AUTO", "0")
    with TestClient(create_app(load_settings(data), only=("stacking",))) as c:
        g = c.get(f"/api/stacking/results/{rid}/lightcurves").json()
        assert g["state"] == "done" and g["sterne"]
        e = next(x for x in g["sterne"] if x.get("name") == "V0001 Tst")
        k = c.get(f"/api/stacking/results/{rid}/lightcurves/star/{e['i']}?bin=3").json()
        assert len(k["punkte"]) >= 30 and k["gebinnt"] and k["phase"]["quelle"] == "Katalog" and k["phase"]["modell"]
        assert c.get(f"/api/stacking/results/{rid}/lightcurves/star/99999").status_code == 404
        csv = c.get(f"/api/stacking/results/{rid}/lightcurves/export?fmt=csv").text
        assert "NICHT GEPRÜFT" in csv and "V0001 Tst" in csv
        aavso = c.get(f"/api/stacking/results/{rid}/lightcurves/export?fmt=aavso&star={e['i']}").text
        assert "#TYPE=EXTENDED" in aavso and "NICHT GEPRÜFT" in aavso and "V0001 Tst" in aavso and ",TG,NO,STD,ENSEMBLE," in aavso
        assert c.get(f"/api/stacking/results/{rid}/lightcurves/export?fmt=xls").status_code == 400
        # Projektansicht bleibt schlank
        assert "lightcurves" not in json.dumps(c.get(f"/api/stacking/projects/{p['id']}").json().get("results", [{}])[0].get("stats", {}))
    db.x("UPDATE results SET stats=json_remove(stats,'$.lightcurves') WHERE id=?", (rid,))


def test_queue_job_and_errors(env, fakes, monkeypatch):
    """Auftrag über die Insights-Warteschlange; ohne Gaia bzw. ohne Rohbilder klare Meldungen."""
    s, db, pl, p, rid, truth, cat, vsx, geo = env
    with pytest.raises(lightcurve.LightcurveError, match="Gaia"):
        lightcurve.compute(pl, rid, gaia=False)
    monkeypatch.setattr(lightcurve, "MIN_FRAMES", 10_000)
    with pytest.raises(lightcurve.LightcurveError, match="Rohbilder"):
        lightcurve.compute(pl, rid)


def test_common_pattern_is_artifact():
    """Zwei „Kandidaten“ mit demselben Sprung (Ausleuchtung am Rand) sind ein Bildfehler, ein unabhängiger Veränderlicher nicht."""
    rng = np.random.default_rng(5)
    nf = 80
    step = np.where(np.arange(nf) > 50, -1.2, 0.0)
    mag = np.c_[13 + step + rng.normal(0, 0.03, nf), 12.5 + step + rng.normal(0, 0.03, nf),
                12 + 0.3 * np.sin(np.arange(nf) / 6) + rng.normal(0, 0.03, nf)]
    assert lightcurve.common_pattern([0, 1, 2], mag) == {0, 1}
