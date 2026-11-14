import json
import sys
from pathlib import Path

import numpy as np
import pytest
from astropy.io import fits

from astroflow.modules.stacking import demo, platesolve
from astroflow.core.config import load_settings
from astroflow.modules.stacking.db import DB
from astroflow.modules.stacking.pipeline import Pipeline
from astroflow.modules.stacking.reviewer import ReviewerConfig

# Attrappe der ASTAP-CLI: schreibt eine .ini im echten ASTAP-Format neben das Eingabebild.
# Modus steht in mode.txt (ok|fail), jeder Aufruf wird in calls.txt protokolliert.
FAKE = r'''
import sys, pathlib
from astropy.io import fits
a = sys.argv[1:]
img = pathlib.Path(a[a.index("-f") + 1])
here = pathlib.Path(__file__).parent
with open(here / "calls.txt", "a") as f:
    f.write(" ".join(a) + "\n")
h, w = fits.getdata(img).shape
ini = img.with_suffix(".ini")
if (here / "mode.txt").read_text().strip() != "ok":
    ini.write_text("PLTSOLVD=F\nERROR=No solution found!\nWARNING=\n")
    sys.exit(1)
s = 4.0 / 3600   # 4"/px im (gebinnten) Lösungsbild, Norden oben
ini.write_text(f"PLTSOLVD=T\nCRPIX1={(w + 1) / 2}\nCRPIX2={(h + 1) / 2}\nCRVAL1=10.6847\nCRVAL2=41.2690\n"
               f"CDELT1={-s}\nCDELT2={s}\nCROTA1=0\nCROTA2=0\nCD1_1={-s}\nCD1_2=0\nCD2_1=0\nCD2_2={s}\nWARNING=\n")
'''


@pytest.fixture
def fake_astap(tmp_path):
    d = tmp_path / "fake"
    d.mkdir()
    (d / "astap.py").write_text(FAKE, encoding="utf-8")
    (d / "mode.txt").write_text("ok")
    cfg = platesolve.SolverConfig(backend="astap", cmd_prefix=[sys.executable, str(d / "astap.py")], timeout=60)
    return cfg, d


def calls(d):
    p = d / "calls.txt"
    return p.read_text().splitlines() if p.exists() else []


def test_solution_from_astap_ini(fake_astap, tmp_path):
    cfg, d = fake_astap
    demo.generate(tmp_path / "in", n_lights=1, n_darks=0, n_flats=0, anonymous=True)
    f = next((tmp_path / "in" / "lights").glob("*.fit"))
    sol = platesolve.solve_frame(cfg, str(f), bayer=True, instrument="DemoScope", work=tmp_path / "w")
    assert abs(sol.ra - 10.6847) < 1e-3 and abs(sol.dec - 41.269) < 1e-3
    assert abs(sol.scale - 2.0) < 1e-3              # 4"/px im 2x2-gebinnten Bild = 2"/px im Rohbild
    assert abs(sol.fov_w - 480 * 2 / 3600) < 1e-4
    assert sol.solver == "astap"
    assert not list((tmp_path / "w").glob("*")), "Arbeitsdateien werden aufgeräumt"
    assert "-r 180" in calls(d)[0]                  # Blind-Solve ohne Vorwissen


def test_hints_from_header(tmp_path):
    p = tmp_path / "x.fits"
    fits.PrimaryHDU(np.zeros((100, 150), np.uint16), header=fits.Header({"FOCALLEN": 250.0, "XPIXSZ": 2.9})).writeto(p)
    h = platesolve._hints(str(p), (100, 150), None)
    assert abs(h["scale"] - 206.265 * 2.9 / 250) < 1e-6
    assert platesolve._hints(str(p), (1920, 1080), "Seestar S50")["scale"] == pytest.approx(206.265 * 2.9 / 250)
    q = tmp_path / "y.fits"
    fits.PrimaryHDU(np.zeros((10, 10), np.uint16)).writeto(q)
    assert platesolve._hints(str(q), (1920, 1080), "Seestar S50")["fov_h"] == pytest.approx(1920 * 2.39 / 3600)


def test_scan_solves_once_per_group_and_finds_object(fake_astap, tmp_path):
    cfg, d = fake_astap
    s = load_settings(tmp_path / "data")
    demo.generate(s.input_dir / "cam", n_lights=12, n_darks=2, n_flats=2, anonymous=True)
    db = DB(s.db_path)
    pl = Pipeline(s, db, ReviewerConfig(), cfg)
    r = pl.scan()
    assert r["solved"] == 12
    assert len(calls(d)) == 2                      # zwei Nächte = zwei Gruppen, je eine Lösung
    ps = db.q("SELECT * FROM projects")
    assert len(ps) == 1 and ps[0]["obj_id"] == "M31"
    info = json.loads(db.q("SELECT info FROM frames WHERE kind='light' LIMIT 1")[0]["info"])
    assert info["solve"]["solver"] == "astap"


def test_failed_solve_then_resolve_moves_frames(fake_astap, tmp_path):
    cfg, d = fake_astap
    (d / "mode.txt").write_text("fail")
    s = load_settings(tmp_path / "data")
    demo.generate(s.input_dir / "cam", n_lights=8, n_darks=0, n_flats=0, anonymous=True)
    db = DB(s.db_path)
    pl = Pipeline(s, db, ReviewerConfig(), cfg)
    r = pl.scan()
    assert r["solved"] == 0 and any("Plate-Solving fehlgeschlagen" in e for e in r["errors"])
    unknown = db.one("SELECT * FROM projects")
    assert unknown["key"].startswith("UNBEKANNT")
    n_fail_calls = len(calls(d))
    assert n_fail_calls == 4                       # 2 Gruppen × 2 Versuche
    pl.scan()                                      # bekannte Dateien: kein erneuter Versuch
    assert len(calls(d)) == n_fail_calls

    (d / "mode.txt").write_text("ok")
    res = pl.solve_project(unknown["id"])
    assert sum(res["moved"].values()) == 8 and res["remaining"] == 0
    ps = db.q("SELECT * FROM projects")
    assert len(ps) == 1 and ps[0]["obj_id"] == "M31"
    assert db.one("SELECT COUNT(*) n FROM frames WHERE project_id=?", (ps[0]["id"],))["n"] == 8


def test_auto_chain_without_solvers_is_disabled(tmp_path):
    cfg = platesolve.SolverConfig(backend="auto", astap=str(tmp_path / "nope.exe"))
    cfg.astap_path = lambda: None                  # kein ASTAP auf dem Testrechner annehmen
    cfg.solve_field_path = lambda: None
    assert not cfg.enabled
    with pytest.raises(platesolve.SolveError):
        platesolve.solve_frame(cfg, "x.fits", False, None, tmp_path)
    cfg.nova_key = "k"
    assert cfg.chain() == []                       # Online-Dienst nie automatisch


def test_extended_catalog_names_solved_field(tmp_path, monkeypatch):
    from astroflow.modules.stacking import objects
    from astroflow.modules.stacking.fits_io import FrameInfo
    from astroflow.modules.stacking.scanner import assign_project
    csv = tmp_path / "deep_sky.csv"
    csv.write_text("header\nunits\n162142,127007,NGC1579/Sh2-222,120,80\n162520,127602,IC2067\n"
                   "680151,118907,M57/NGC6720/Ring_Nebula/PNG063.1+13.9,30,24,55\n", encoding="utf-8")
    monkeypatch.setenv("ASTROFLOW_DEEPSKY", str(csv))
    monkeypatch.setattr(objects, "_EXT", None)
    info = FrameInfo(path="x", shape=(2080, 3072), ra=67.535, dec=35.277, instrument="stellina",
                     solve={"fov_w": 1.06, "fov_h": 0.72})
    a = assign_project(info, {})
    assert a.obj_id == "NGC1579" and a.name == "NGC 1579" and a.kind == "emission"
    info.solve = None                               # ohne Plate-Solving kein Raten im großen Katalog
    assert assign_project(info, {}).obj_id is None
    monkeypatch.setattr(objects, "_EXT", None)


def test_folder_name_gets_kind_from_extended_catalog(tmp_path, monkeypatch):
    """Ordnername „…_observation_NGC4490“: Name bleibt, Profil (Galaxie) kommt aus dem großen Katalog."""
    from astroflow.modules.stacking import objects
    from astroflow.modules.stacking.fits_io import FrameInfo
    from astroflow.modules.stacking.scanner import assign_project
    csv = tmp_path / "deep_sky.csv"
    csv.write_text("header\nunits\n450361,149914,NGC4490/PGC41333,64,32,125\n", encoding="utf-8")
    monkeypatch.setenv("ASTROFLOW_DEEPSKY", str(csv))
    monkeypatch.setattr(objects, "_EXT", None)
    monkeypatch.setattr(objects, "_EXT_ALIAS", {})
    a = assign_project(FrameInfo(path="x", shape=(2080, 3072), object="NGC4490"), {})
    assert a.name == "NGC4490" and a.kind == "galaxy"
    assert objects.resolve_extended("PGC 41333").id == "NGC4490"
    monkeypatch.setattr(objects, "_EXT", None)
    monkeypatch.setattr(objects, "_EXT_ALIAS", {})


def test_messier_kind_without_small_catalog(tmp_path, monkeypatch):
    """M77 fehlt im kleinen Katalog, ASTAP führt es ohne Art: Profil kommt aus Messier/Caldwell (deepsky), nicht „generic“."""
    from astroflow.modules.stacking import deepsky, objects
    from astroflow.modules.stacking.fits_io import FrameInfo
    from astroflow.modules.stacking.scanner import assign_project
    csv = tmp_path / "deep_sky.csv"
    csv.write_text("header\nunits\n97685,1,M77/NGC1068,72,60\n", encoding="utf-8")
    monkeypatch.setenv("ASTROFLOW_DEEPSKY", str(csv))
    deepsky.reset()
    try:
        assert objects.resolve_name("M77") is None and objects.resolve_extended("M77").kind == "generic"
        for name in ("M77", "Messier 77", "NGC 1068"):
            assert objects.kind_of(name) == "galaxy"
        assert objects.kind_of("C 14") == "open_cluster" and objects.kind_of("M56") == "globular"
        assert objects.kind_of("M31") == "galaxy" and objects.kind_of("POLARIS") is None
        a = assign_project(FrameInfo(path="x", shape=(1080, 1920), object="M77"), {})
        assert a.key.startswith("M77|") and a.kind == "galaxy"          # Schlüssel bleibt, nur das Profil ändert sich
        info = FrameInfo(path="x", shape=(1080, 1920), ra=40.67, dec=-0.013, solve={"fov_w": 1.6, "fov_h": 0.9})
        assert assign_project(info, {}).kind == "galaxy"                 # gelöstes Feld ohne Namen
    finally:
        deepsky.reset()
