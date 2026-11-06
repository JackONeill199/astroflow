"""SkyTrace „Standorte aller Nächte“: Alternativen zu einem zurückgerechneten Ort (0.38.0) – nahe Listenorte, die streng ebenfalls gut passen
(rematch.alternatives), samt Begründung zur Einrastgrenze; Engine: nahe, noch nicht geprüfte Listenorte werden streng nachgeprüft.
Erfundene Orte. Engine-Teil braucht skyfield – direkt im Engine-Interpreter: `python tests/test_skytrace_alternatives.py`."""
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_skytrace_rematch import TRUE, _engine, _offset_km, _patch, _satellites, _unload, pytest  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _at(lat, lon, north_m, east_m):
    return {"lat": lat + north_m / 111195.0, "lon": lon + east_m / (111195.0 * math.cos(math.radians(lat)))}


def _night(**kw):
    p = dict(kind="fit", lat=47.5, lon=10.25, accuracy_m=115, sure=3, rms=6.1)
    p.update(kw.pop("proposal", {}))
    return dict(night="2022-05-17", tracks=3, proposal=p, current={"id": "home", "name": "Testhausen"}, **kw)


def test_alternative_near_and_good():
    from astroflow.modules.skytrace.rematch import alternatives, with_alternatives
    near = dict(id="near", name="Ferienhaus", **_at(47.5, 10.25, 300, 220))        # ~372 m
    far = dict(id="far", name="Bergwiese", **_at(47.5, 10.25, 3000, 0))           # 3 km – zu weit
    home = dict(id="home", name="Testhausen", **_at(47.5, 10.25, 4000, 1000))
    tested = [dict(id="near", sure=3, rms=113.3), dict(id="far", sure=3, rms=20), dict(id="home", sure=0, rms=None)]
    alts, z = alternatives(_night(tested=tested), [near, far, home])
    assert [a["id"] for a in alts] == ["near"] and abs(alts[0]["distance_m"] - 372) < 5 and alts[0]["sure"] == 3 and alts[0]["rms"] == 113.3
    assert z["id"] == "near" and z["limit_m"] == 300                               # Grenze max(300 m, 2 × 115 m)
    # quer zu schlecht (über max(60″, 3 × Vorschlag) + 0,3″ je m Abstand) bzw. zwei Spuren weniger → keine Alternative
    assert alternatives(_night(tested=[dict(id="near", sure=3, rms=200)]), [near])[0] == []
    assert alternatives(_night(tested=[dict(id="near", sure=1, rms=10)]), [near])[0] == []
    assert alternatives(_night(tested=[dict(id="near", sure=2, rms=10)]), [near])[0][0]["id"] == "near"   # eine weniger ist erlaubt
    # ≤ 5σ zählt auch über 2 km: Vorschlag ± 1 km → bis 5 km
    wide = _night(tested=tested, proposal=dict(accuracy_m=1000, rms=30))
    assert {a["id"] for a in alternatives(wide, [near, far, home])[0]} == {"near", "far"}
    # nur bei fit/global, nicht übernommen; Ergebnis der Suche wird ergänzt, ohne die Datei zu ändern
    assert alternatives(_night(tested=tested, proposal=dict(kind="list", id="near")), [near]) == ([], None)
    assert alternatives(_night(tested=tested, applied=1), [near]) == ([], None)
    res = {"nights": [_night(tested=tested), dict(night="2022-05-18", proposal=None)]}
    out = with_alternatives(res, [near, far, home])
    assert out["nights"][0]["alternatives"][0]["id"] == "near" and "alternatives" not in res["nights"][0]
    assert "alternatives" not in out["nights"][1]


def test_api_liefert_alternativen(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from astroflow.core.config import load_settings
    from astroflow.core.store import SettingsStore
    from astroflow.server import create_app
    monkeypatch.setenv("ASTROFLOW_MODULES", "stacking,skytrace")
    s = load_settings(tmp_path / "d")
    home = tmp_path / "st"
    home.mkdir()
    store = SettingsStore(s.data_dir / "settings.json")
    store.update("skytrace", {"home": str(home)})
    store.update("sites", {"list": [dict(id="near", name="Ferienhaus", alt_m=0, accuracy_m=30, note="", **_at(47.5, 10.25, 300, 220))],
                           "default": "near", "periods": []})
    (home / "standortsuche-naechte.json").write_text(json.dumps({"created": 1, "radius_km": 10, "state": "done",
                                                                "nights": [_night(tested=[dict(id="near", sure=3, rms=113.3)])]}), encoding="utf-8")
    with TestClient(create_app(s)) as c:
        n = c.get("/api/skytrace/sitesearch/nights").json()["result"]["nights"][0]
    assert n["alternatives"][0]["name"] == "Ferienhaus" and n["near"]["distance_m"] > n["near"]["limit_m"]
    assert "alternatives" not in json.loads((home / "standortsuche-naechte.json").read_text(encoding="utf-8"))["nights"][0]


# ---------------------------------------------------------------- Engine (skyfield) ---
@pytest.fixture
def engine(tmp_path, monkeypatch):
    pytest.importorskip("skyfield")
    pytest.importorskip("sgp4")
    yield _engine(tmp_path, monkeypatch)
    _unload()


def test_engine_prueft_nahen_listenort_nach(engine, tmp_path, monkeypatch):
    """Bisheriger Ort (Standard) liegt ~400 m daneben: genug Treffer → nur verfeinern (kein Listenort geprüft); der zurückgerechnete Punkt
    bekommt die Einrastgrenze, der nahe Listenort wird streng nachgeprüft und erscheint als Alternative (hier = bisheriger Ort zum Verschieben)."""
    from test_skytrace_nights import NIGHT, _observation, _search, _sites
    from astroflow.modules.skytrace.rematch import alternatives
    near = dict(_at(TRUE["lat"], TRUE["lon"], 450, -400), alt_m=TRUE["alt_m"])                 # ~600 m daneben
    _sites(tmp_path / "settings.json", [{"id": "home", "name": "Testhausen", "accuracy_m": 30, **near}], "home")
    sats = _satellites(5)
    _patch(monkeypatch, sats)
    obs = _observation(tmp_path, "2026-11-05_10-00-00-000001", f"{NIGHT}_observation_Feld", sats, [0, 1, 2, 3, 4], near)
    import contextlib, io
    with contextlib.redirect_stdout(io.StringIO()):
        engine.rematch([tmp_path / "st" / "Ergebnisse" / obs["run"]], force=True)     # gespeicherte Treffer am bisherigen Ort
    n = _search(engine, tmp_path, [obs])
    p = n["proposal"]
    assert p and p["kind"] == "fit" and _offset_km(p, TRUE) < 0.2 and p["snap_limit_m"] == round(max(300, 2 * p["accuracy_m"])), n
    t = next(t for t in n["tested"] if t["id"] == "home")
    assert t["alternative"] and t["sure"] >= 4
    alts, z = alternatives(n, [{"id": "home", "name": "Testhausen", **near}])
    assert alts and alts[0]["id"] == "home" and 450 < alts[0]["distance_m"] < 750 and z["limit_m"] == p["snap_limit_m"] < z["distance_m"]


if __name__ == "__main__":
    import os, tempfile

    class MP:
        def __init__(self): self.undo = []
        def setattr(self, obj, name, value): self.undo.append((obj, name, getattr(obj, name))); setattr(obj, name, value)
        def setenv(self, k, v): os.environ[k] = v
        def syspath_prepend(self, p): sys.path.insert(0, p)
        def close(self): [setattr(o, n, v) for o, n, v in reversed(self.undo)]; _unload()
    test_alternative_near_and_good(); print("OK test_alternative_near_and_good")
    mp = MP()
    with tempfile.TemporaryDirectory() as d:
        try:
            test_engine_prueft_nahen_listenort_nach(_engine(Path(d), mp), Path(d), mp)
        finally:
            mp.close()
    print("OK test_engine_prueft_nahen_listenort_nach")
