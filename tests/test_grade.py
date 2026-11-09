"""Bildqualität der Aufnahme (stacking/grade.py): Note 0–10 aus Tiefe, Rauschen, Schärfe und Sternform."""
import math

import pytest

from astroflow.modules.stacking import grade
from astroflow.modules.stacking.db import DB


def test_extremes_and_bounds():
    best = grade.grade(m5=21.0, snr=500.0, fwhm_px=2.0, ecc=0.1)
    worst = grade.grade(m5=12.0, snr=1.0, fwhm_px=12.0, ecc=0.95)
    assert best["score"] == 10.0 and worst["score"] == 0.0                     # außerhalb der Anker begrenzt
    assert all(0 <= p["points"] <= 10 for g in (best, worst) for p in g["parts"])
    mid = grade.grade(m5=16.5, snr=math.sqrt(3 * 60), fwhm_px=5.5, ecc=0.6)   # jeweils genau die Mitte
    assert mid["score"] == 5.0 and all(p["points"] == 5.0 for p in mid["parts"])


def test_each_measure_moves_the_score():
    base = dict(m5=17.0, snr=15.0, fwhm_px=5.0, ecc=0.6)
    s0 = grade.grade(**base)["score"]
    for k, better in (("m5", 18.0), ("snr", 30.0), ("fwhm_px", 4.0), ("ecc", 0.45)):
        assert grade.grade(**{**base, k: better})["score"] > s0, k
    # Gewichte: Tiefe zählt am meisten, Sternform am wenigsten
    w = {p["key"]: p["weight"] for p in grade.grade(**base)["parts"]}
    assert w["depth"] > w["noise"] == w["sharp"] > w["shape"] and abs(sum(w.values()) - 1) < 1e-9


def test_typical_images_differ():
    """Kurze, flache Aufnahme gegen lange, tiefe – früher beide 10/10 (Bearbeitung ohne Restfehler)."""
    short = grade.grade(m5=15.9, snr=4.5, fwhm_px=6.6, ecc=0.7)
    deep = grade.grade(m5=18.3, snr=40.0, fwhm_px=4.3, ecc=0.5)
    assert short["score"] < 4 < 7 < deep["score"]


def test_missing_parts_reweighted_and_minimum():
    g = grade.grade(snr=15.0, fwhm_px=5.0)
    assert g["missing"] == ["depth", "shape"] and abs(sum(p["share"] for p in g["parts"]) - 1) < 1e-3
    assert grade.grade(snr=15.0) is None and grade.grade() is None             # unter zwei Teilen keine Note
    assert grade.grade(snr=float("nan"), fwhm_px=5.0) is None                  # ungültige Werte zählen nicht
    assert grade.grade(snr=0.0, fwhm_px=5.0, ecc=0.5)["missing"] == ["depth", "noise"]


def test_explanation_texts():
    g = grade.grade(m5=17.25, snr=12.0, fwhm_px=4.5, ecc=0.48, scale=1.24)
    t = {p["key"]: p["text"] for p in g["parts"]}
    assert t["depth"] == "Grenzgröße 17,2 mag" or t["depth"] == "Grenzgröße 17,3 mag"
    assert t["sharp"] == "Sterne 4,5 px breit (5,6″)" and "rund" in t["shape"] and t["noise"] == "Objekt-SNR 12"


def test_of_result_uses_current_depth_only():
    st = {"quality": {"score": 10, "measure": {"snr": 20.0}}, "fwhm_px": 4.8,
          "depth": {"ok": True, "v": 3, "grenze": {"m5": 17.5}, "feld": {"skala": 1.24}}}
    assert {p["key"] for p in grade.of_result(st, 0.55, depth_version=3)["parts"]} == {"depth", "noise", "sharp", "shape"}
    assert grade.of_result(st, 0.55, depth_version=4)["missing"] == ["depth"]  # veraltete Tiefe zählt nicht
    assert grade.of_result({"quality": {"score": 10}}) is None                 # nur Bearbeitungsprüfung: keine Note


def test_project_ecc(tmp_path):
    db = DB(tmp_path / "t.db")
    pid = db.x("INSERT INTO projects(key,name,options,created) VALUES('k','X','{}',0)")
    for i, (ecc, status) in enumerate(((0.4, "selected"), (0.6, "selected"), (0.95, "rejected"))):
        db.x("INSERT INTO frames(id,project_id,path,kind,metrics,status) VALUES(?,?,?,?,?,?)",
             (f"f{i}", pid, f"{i}.fits", "light", f'{{"ecc": {ecc}}}', status))
    assert grade.project_ecc(db) == {pid: pytest.approx(0.5)}                   # aussortierte Bilder zählen nicht
    assert grade.project_ecc(db, [pid + 1]) == {} and grade.project_ecc(db, []) == {}
