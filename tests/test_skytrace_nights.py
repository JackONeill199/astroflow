"""SkyTrace „Standorte für alle Nächte suchen“ (engine/rematch_sites.py nights): je Nacht alle Beobachtungen gemeinsam, alle
Orte der Ortsliste prüfen, sonst zurückrechnen. Synthetische LEO-Bahnen, erfundene Orte. Engine-Teil braucht skyfield –
direkt im Engine-Interpreter: `python tests/test_skytrace_nights.py`."""
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_skytrace_rematch import (T0, TRUE, WRONG, _engine, _offset_km, _patch, _satellites, _twin, _unload, pytest)  # noqa: E402

NIGHT = "2024-07-02"


@pytest.fixture
def engine(tmp_path, monkeypatch):
    pytest.importorskip("skyfield")
    pytest.importorskip("sgp4")
    yield _engine(tmp_path, monkeypatch)
    _unload()


def _sites(path: Path, sites: list[dict], default: str):
    data = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    data["sites"] = {"list": sites, "default": default, "periods": []}
    path.write_text(json.dumps(data), encoding="utf-8")


def _observation(tmp: Path, run: str, name: str, sats: list, index: list[int], stored: dict, shift_deg=0.0):
    """Eine Beobachtung (eigener Lauf) mit Spuren der Satelliten `index` (Zeit T0 + 40 s·i, am wahren Ort gemessen)."""
    import numpy as np
    from astroflow.modules.skytrace import orbit3d, sitefit
    root = tmp / "st" / "Ergebnisse" / run
    obs = root / name
    (obs / "kandidaten").mkdir(parents=True)
    cands = []
    for k, i in enumerate(index):
        start = T0 + 40 * i
        tt = np.linspace(start, start + 10, 11) + 2.0
        u = sitefit.topo_directions(sats[i], tt, TRUE["lat"], TRUE["lon"], TRUE["alt_m"], orbit3d.icrs_to_teme(start + 5))
        ra, dec = (np.degrees(np.arctan2(u[:, 1], u[:, 0])) + shift_deg) % 360, np.degrees(np.arcsin(u[:, 2]))
        c = {"id": f"K{k + 1:04d}", "session": name, "kind": "trail", "timestamp": start, "timestamp_text": "UTC", "exposure_seconds": 10.0,
             "time_reference": "start", "source_file": str(tmp / "fehlt.fits"), "world_path": np.column_stack([ra, dec]).tolist(),
             "path": [[120, 120], [600, 500]], "context_image": {"width": 1000, "height": 800, "image": f"{name}/kandidaten/K{k + 1:04d}/kontext.png"},
             "site": dict(stored, accuracy_m=100), "catalogs": [{"provider": "satellites", "status": "checked_no_match", "matches": []}]}
        (obs / "kandidaten" / c["id"]).mkdir()
        cands.append(c)
    (obs / "kandidaten.json").write_text(json.dumps(cands), encoding="utf-8")
    (root / "videos.json").write_text(json.dumps([{"session": name, "video": f"{name}/video.mp4", "frames": 20, "hits": len(index),
                                                   "candidates_file": f"{name}/kandidaten.json"}]), encoding="utf-8")
    return {"run": run, "folder": name, "session": name}


def _search(rs, tmp: Path, observations: list[dict]):
    groups = tmp / "gruppen.json"
    groups.write_text(json.dumps([{"night": NIGHT, "observations": observations}]), encoding="utf-8")
    out = tmp / "naechte.json"
    rs.nights(groups, out)
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["state"] == "done" and len(data["nights"]) == 1
    return data["nights"][0]


HOME = {"id": "home", "name": "Testhausen", "accuracy_m": 100}


def test_two_observations_fitted_together(engine, tmp_path, monkeypatch):
    """Je Beobachtung zu wenige Spuren für eine Rückrechnung, gemeinsam genug: der Ort wird für die ganze Nacht gefunden."""
    _sites(tmp_path / "settings.json", [{**HOME, **WRONG}], "home")
    sats = _satellites(4)
    _patch(monkeypatch, sats)
    a = _observation(tmp_path, "2026-11-05_10-00-00-000001", f"{NIGHT}_observation_Feld_A", sats, [0], WRONG)
    b = _observation(tmp_path, "2026-11-05_11-00-00-000001", f"{NIGHT}_observation_Feld_B", sats, [1, 2, 3], WRONG)
    n = _search(engine, tmp_path, [a, b])
    assert n["tracks"] == 4 and n["before"] == 0 and n["current"]["id"] == "home"
    assert n["status"] == "better" and n["proposal"]["kind"] == "fit", n
    p = n["proposal"]
    assert p["sure"] == 4 and p["trails"] == 4                      # beide Beobachtungen gemeinsam
    assert _offset_km(p, TRUE) < 0.2 and p["clock_s"] == pytest.approx(2.0, abs=0.3)
    assert n["assign"] == [NIGHT]


def test_right_list_site_replaces_wrong(engine, tmp_path, monkeypatch):
    _sites(tmp_path / "settings.json", [{**HOME, **WRONG}, {"id": "berg", "name": "Beispielberg", **TRUE, "accuracy_m": 100}], "home")
    sats = _satellites(2)
    _patch(monkeypatch, sats)
    a = _observation(tmp_path, "2026-11-05_10-00-00-000001", f"{NIGHT}_observation_Feld_A", sats, [0, 1], WRONG)
    n = _search(engine, tmp_path, [a])
    assert {t["id"]: t["sure"] for t in n["tested"]} == {"home": 0, "berg": 2}
    assert n["proposal"]["kind"] == "list" and n["proposal"]["id"] == "berg" and n["proposal"]["sure"] == 2, n
    assert "zu wenig für eine Rückrechnung" in n["note"]              # 2 Spuren: nur Ortswahl


def test_no_matching_trail_no_proposal(engine, tmp_path, monkeypatch):
    _sites(tmp_path / "settings.json", [{**HOME, **WRONG}, {"id": "berg", "name": "Beispielberg", **TRUE, "accuracy_m": 100}], "home")
    sats = _satellites(3)
    _patch(monkeypatch, sats)
    a = _observation(tmp_path, "2026-11-05_10-00-00-000001", f"{NIGHT}_observation_Feld_A", sats, [0, 1, 2], WRONG, shift_deg=25.0)
    n = _search(engine, tmp_path, [a])
    assert n["status"] == "none" and n["proposal"] is None, n
    assert n["note"].startswith("Kein Ort passt")


def test_ambiguous_train_not_used(engine, tmp_path, monkeypatch):
    _sites(tmp_path / "settings.json", [{**HOME, **WRONG}], "home")
    sats = _satellites(5)
    _patch(monkeypatch, sats, extra=[_twin(sats[0], 0.05)])
    a = _observation(tmp_path, "2026-11-05_10-00-00-000001", f"{NIGHT}_observation_Feld_A", sats, [0, 1, 2, 3, 4], WRONG)
    n = _search(engine, tmp_path, [a])
    p = n["proposal"]
    assert p and p["kind"] == "fit" and p["trails"] == 4 and p["sure"] == 4, n   # Zwilling macht Spur 1 mehrdeutig
    assert _offset_km(p, TRUE) < 0.2


def test_stop_keeps_finished_nights(engine, tmp_path, monkeypatch):
    _sites(tmp_path / "settings.json", [{**HOME, **TRUE}], "home")
    sats = _satellites(2)
    _patch(monkeypatch, sats)
    a = _observation(tmp_path, "2026-11-05_10-00-00-000001", f"{NIGHT}_observation_Feld_A", sats, [0], TRUE)
    b = _observation(tmp_path, "2026-11-05_11-00-00-000001", "2024-07-03_observation_Feld_B", sats, [1], TRUE)
    real, calls = engine.satellite_check, []

    def interrupted(item, site, settings):
        calls.append(1)
        if item["session"].startswith("2024-07-03"):
            raise InterruptedError("angehalten")
        return real(item, site, settings)
    monkeypatch.setattr(engine, "satellite_check", interrupted)
    groups = tmp_path / "gruppen.json"
    groups.write_text(json.dumps([{"night": NIGHT, "observations": [a]}, {"night": "2024-07-03", "observations": [b]}]), encoding="utf-8")
    engine.nights(groups, tmp_path / "naechte.json")
    data = json.loads((tmp_path / "naechte.json").read_text(encoding="utf-8"))
    assert data["state"] == "stopped" and [x["night"] for x in data["nights"]] == [NIGHT]
    assert data["nights"][0]["status"] == "ok"                       # richtiger Ort, nichts zu ändern


# ---------------------------------------------------------------- Server (ohne skyfield) ---
def test_nights_api_groups_and_applied(tmp_path, monkeypatch):
    """Gruppen je Nacht (mehrere Läufe, nur neueste vollständige Analyse je Beobachtung), Ergebnisdatei, „übernommen“, Abgleich mehrerer Läufe."""
    import time
    from fastapi.testclient import TestClient
    from astroflow.core.config import load_settings
    from astroflow.core.store import SettingsStore
    from astroflow.server import create_app
    import astroflow.modules.skytrace.rematch as rm
    data, home = tmp_path / "data", tmp_path / "st"
    data.mkdir()
    SettingsStore(data / "settings.json").update("skytrace", {"home": str(home)})

    def run(rid, session, state="Fertig", first="img-1.fits"):
        root = home / "Ergebnisse" / rid
        (root / "obs").mkdir(parents=True)
        (root / "obs" / "kandidaten.json").write_text(json.dumps([{"id": "K0001", "session": session, "timestamp": T0, "world_path": [[1, 2], [1.1, 2.1]]}]), encoding="utf-8")
        (root / "videos.json").write_text(json.dumps([{"session": session, "video": "obs/video.mp4", "frames": 3, "state": state, "source_first_file": first,
                                                       "candidates_file": "obs/kandidaten.json"}]), encoding="utf-8")
    run("2026-11-01_10-00-00-000001", f"{NIGHT}_observation_Feld_A")
    run("2026-11-02_10-00-00-000001", f"{NIGHT}_observation_Feld_A", state="Angehalten")   # neuer, aber unvollständig → zählt nicht
    run("2026-11-03_10-00-00-000001", f"{NIGHT}_observation_Feld_B", first="img-9.fits")
    run("2026-11-04_10-00-00-000001", "2024-07-05_observation_Feld_C")
    fake = tmp_path / "fake.py"
    fake.write_text("import json,sys\nout=sys.argv[sys.argv.index('nights')+2]\n"
                    "json.dump({'created':1,'radius_km':float(sys.argv[sys.argv.index('--radius-km')+1]),'state':'done','nights':[{'night':'" + NIGHT + "','proposal':{'kind':'list'}}]},open(out,'w'))\n"
                    "print(json.dumps({'type':'result','text':'1 Nächte geprüft.'}),flush=True)\n", encoding="utf-8")
    monkeypatch.setattr(rm.SiteJobs, "SCRIPT", fake)
    monkeypatch.setenv("ASTROFLOW_MODULES", "stacking,skytrace")
    app = create_app(load_settings(data))
    with TestClient(app) as c:
        assert c.post("/api/skytrace/sitesearch/nights", json={"radius_km": 99}).status_code == 400
        j = c.post("/api/skytrace/sitesearch/nights", json={"radius_km": 5}).json()
        assert j["kind"] == "nights" and j["title"] == "Standorte für alle Nächte suchen"
        groups = {g["night"]: g["observations"] for g in json.loads((home / "standortsuche-gruppen.json").read_text(encoding="utf-8"))}
        assert sorted(o["run"] for o in groups[NIGHT]) == ["2026-11-01_10-00-00-000001", "2026-11-03_10-00-00-000001"]
        assert [o["folder"] for o in groups["2024-07-05"]] == ["obs"]
        for _ in range(100):
            d = c.get("/api/skytrace/sitesearch/nights").json()
            if d["job"]["state"] != "running":
                break
            time.sleep(0.05)
        assert d["job"]["state"] == "done" and d["result"]["radius_km"] == 5.0
        assert [a["href"] for a in c.get("/api/activity").json() if a["key"].startswith("skytrace:abgleich:")] == ["#/skytrace/standorte"]
        r = c.post("/api/skytrace/sitesearch/nights/applied", json={"nights": [NIGHT]}).json()
        assert r["result"]["nights"][0]["applied"]
        assert c.post("/api/skytrace/rematch", json={"runs": ["../x"]}).status_code == 400


if __name__ == "__main__":
    import contextlib, io, os, tempfile
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

    class MP:
        def __init__(self): self.undo = []
        def setattr(self, obj, name, value): self.undo.append((obj, name, getattr(obj, name))); setattr(obj, name, value)
        def setenv(self, k, v): os.environ[k] = v
        def syspath_prepend(self, p): sys.path.insert(0, p)
        def close(self): [setattr(o, n, v) for o, n, v in reversed(self.undo)]; _unload()
    for test in (test_two_observations_fitted_together, test_right_list_site_replaces_wrong, test_no_matching_trail_no_proposal,
                 test_ambiguous_train_not_used, test_stop_keeps_finished_nights):
        mp = MP()
        with tempfile.TemporaryDirectory() as d:
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    test(_engine(Path(d), mp), Path(d), mp)
            finally:
                mp.close()
        print("OK", test.__name__)
