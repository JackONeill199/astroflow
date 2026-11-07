"""SkyTrace nach Standortänderungen: Neu abgleichen mit der Ortsliste und Standortsuche ohne Treffer (engine/rematch_sites.py,
rematch.py). Synthetische LEO-Bahnen, erfundene Orte (Testhausen). Der Engine-Teil braucht skyfield/sgp4 – mit pytest im
AstroFlow-Python wird er sonst übersprungen; direkt im Engine-Interpreter: `python tests/test_skytrace_rematch.py`."""
import json
import math
import os
import sys
import time
from pathlib import Path

import numpy as np

try:
    import pytest
except ImportError:                                                  # Engine-Interpreter ohne pytest (Aufruf am Dateiende)
    class _Approx:
        def __init__(self, v, abs=1e-6): self.v, self.tol = v, abs
        def __eq__(self, other): return abs(other - self.v) <= self.tol

    class pytest:                                                    # noqa: N801 – minimaler Ersatz
        approx = _Approx
        fixture = staticmethod(lambda f: f)
        @staticmethod
        def importorskip(name): return __import__(name)

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "astroflow" / "modules" / "skytrace" / "engine"
TRUE = {"lat": 47.5, "lon": 10.25, "alt_m": 400.0}
# 5 km daneben (Nordnordwest, quer zu den meisten Spuren): an diesem Ort passt keine Bahn innerhalb der normalen 6′
WRONG = {"lat": TRUE["lat"] + 4.6 / 111.195, "lon": TRUE["lon"] - 2.0 / (111.195 * math.cos(math.radians(TRUE["lat"]))), "alt_m": 400.0}
T0 = 1.72e9


def _book(path: Path, site: dict, accuracy=100):
    """Ortsliste (data/settings.json, Abschnitt sites) mit einem erfundenen Standard-Ort."""
    data = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    data["sites"] = {"list": [{"id": "home", "name": "Testhausen", **site, "accuracy_m": accuracy}], "default": "home", "periods": []}
    path.write_text(json.dumps(data), encoding="utf-8")


# ---------------------------------------------------------------- Engine (skyfield) ---
ENGINE_MODULES = {"project_paths", "observation_settings", "catalog_matching", "archive_sources", "satellite_archive", "rematch_sites",
                  "json_store", "analyse", "archive_errors", "sequence", "satellite_overlay", "motion_plausibility", "clock_scan", "global_site"}


@pytest.fixture
def engine(tmp_path, monkeypatch):
    """Engine-Module mit eigenem SKYTRACE_HOME laden (Pfade werden beim Import festgelegt), danach wieder entladen."""
    pytest.importorskip("skyfield")
    pytest.importorskip("sgp4")
    yield _engine(tmp_path, monkeypatch)
    _unload()


def _unload():
    for name in [n for n in sys.modules if n in ENGINE_MODULES or n.startswith("skytrace_server")]:
        sys.modules.pop(name)


def _engine(tmp: Path, monkeypatch):
    monkeypatch.setenv("SKYTRACE_HOME", str(tmp / "st"))
    monkeypatch.setenv("ASTROFLOW_SITES", str(tmp / "settings.json"))
    _unload()
    monkeypatch.syspath_prepend(str(ENGINE))
    import observation_settings
    monkeypatch.setattr(observation_settings, "SITES_FILE", None)
    import rematch_sites
    return rematch_sites


def _satellites(n=6):
    """Erfundene LEO-Bahnen (550 km, 53°), je eine zur Zeit T0 + 40 s·i hoch über Testhausen."""
    from sgp4.api import Satrec, WGS72
    from astroflow.modules.skytrace import orbit3d
    out = []
    for raan in np.arange(0, 360, 7.0):
        t = T0 + 40 * len(out)
        jd = t / 86400 + 2440587.5
        up = orbit3d.geodetic_to_ecef(TRUE["lat"], TRUE["lon"], 0)
        up = up / np.linalg.norm(up)
        for ma in np.arange(0, 360, 1.5):
            s = Satrec()
            s.sgp4init(WGS72, "i", 90000 + len(out), jd - 2433281.5, 1e-5, 0, 0, 1e-4, 0.0, math.radians(53),
                       math.radians(ma), 2 * math.pi / 95.6, math.radians(raan))
            _, r, _ = s.sgp4(jd, 0.0)
            topo = orbit3d.rot_z(-orbit3d.gmst(t), np.asarray(r)) - orbit3d.geodetic_to_ecef(TRUE["lat"], TRUE["lon"], TRUE["alt_m"])
            if topo @ up / np.linalg.norm(topo) > math.sin(math.radians(40)):
                out.append(s)
                break
        if len(out) >= n:
            break
    return out


def _run(tmp: Path, sats, stored_site: dict):
    """Ein Lauf mit einer Beobachtung: je Satellit eine Spur (10 s Belichtung, ganz im Bild), gemessen am wahren Ort."""
    from astroflow.modules.skytrace import orbit3d, sitefit
    run = tmp / "st" / "Ergebnisse" / "2026-11-05_10-00-00-000001"
    obs = run / "2024-07-02_observation_Testfeld"
    (obs / "kandidaten").mkdir(parents=True)
    cands = []
    for i, s in enumerate(sats):
        start = T0 + 40 * i
        tt = np.linspace(start, start + 10, 11) + 2.0                   # Kamera geht 2 s nach
        u = sitefit.topo_directions(s, tt, TRUE["lat"], TRUE["lon"], TRUE["alt_m"], orbit3d.icrs_to_teme(start + 5))
        ra, dec = np.degrees(np.arctan2(u[:, 1], u[:, 0])) % 360, np.degrees(np.arcsin(u[:, 2]))
        c = {"id": f"K{i + 1:04d}", "session": obs.name, "kind": "trail", "timestamp": start, "timestamp_text": "UTC", "exposure_seconds": 10.0,
             "time_reference": "start", "source_file": str(tmp / "fehlt" / f"img-{i}.fits"), "world_path": np.column_stack([ra, dec]).tolist(),
             "path": [[120, 120], [600, 500]], "context_image": {"width": 1000, "height": 800, "image": f"{obs.name}/kandidaten/K{i + 1:04d}/kontext.png"}, "site": dict(stored_site, accuracy_m=100, name="Testhausen"),
             "catalogs": [{"provider": "satellites", "status": "checked_no_match", "matches": []}]}
        (obs / "kandidaten" / c["id"]).mkdir()
        cands.append(c)
    (obs / "kandidaten.json").write_text(json.dumps(cands), encoding="utf-8")
    (run / "videos.json").write_text(json.dumps([{"session": obs.name, "video": f"{obs.name}/video.mp4", "frames": 50, "hits": len(sats), "excluded": 0,
                                                  "candidates_file": f"{obs.name}/kandidaten.json"}]), encoding="utf-8")
    return run, obs


def _twin(s, dma_deg):
    """Zweiter Satellit derselben Bahn, wenige km dahinter (wie ein Starlink-Zug) – macht die Zuordnung mehrdeutig."""
    from sgp4.api import Satrec, WGS72
    t = Satrec()
    t.sgp4init(WGS72, "i", 99999, s.jdsatepoch + s.jdsatepochF - 2433281.5, s.bstar, 0, 0, s.ecco, s.argpo, s.inclo,
               s.mo + math.radians(dma_deg), s.no_kozai, s.nodeo)
    return t


def _patch(monkeypatch, sats, extra=()):
    """Bahnarchiv = die erfundenen Satelliten; keine Namens- oder Archivabfragen ins Netz."""
    from skyfield.api import EarthSatellite
    import satellite_archive, archive_sources, catalog_matching
    sats = list(sats) + list(extra)
    models = [(EarthSatellite.from_satrec(s, catalog_matching.TS), 0.1) for s in sats]
    for s, (m, _) in zip(sats, models):
        m.name = f"TESTSAT-{s.satnum}"
    monkeypatch.setattr(satellite_archive, "historical_models", lambda start, settings: (models, "Testarchiv", len(models)))
    monkeypatch.setattr(archive_sources, "satellite_names", lambda ids, settings, limit=3: ({}, None))
    import sequence
    monkeypatch.setattr(sequence, "report", lambda out, summaries: (out / "videos.json").write_text(json.dumps(summaries), encoding="utf-8"))


def _rows(capsys):
    return [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.startswith("{")]


def _offset_km(a, b):
    return math.hypot((a["lat"] - b["lat"]) * 111.195, (a["lon"] - b["lon"]) * 111.195 * math.cos(math.radians(b["lat"])))


def test_search_finds_site_without_matches(engine, tmp_path, monkeypatch, capsys):
    rs = engine
    _book(tmp_path / "settings.json", WRONG)
    sats = _satellites()
    assert len(sats) >= 5
    _patch(monkeypatch, sats)
    run, obs = _run(tmp_path, sats, WRONG)
    # Strenger Abgleich am falschen Ort: keine Spur zugeordnet (Ausgangslage wie IC 1396 2022-05-09)
    rs.rematch([run], force=True)
    res = _rows(capsys)[-1]["runs"][0]
    assert res["after"] == 0 and res["checked"] == len(sats), res
    # Suchmodus: erweiterte Toleranz → eindeutige Zuordnungen → Rückrechnung → strenger Abgleich am Vorschlag
    rs.search(run, obs.name)
    out = json.loads((obs / "standortsuche.json").read_text(encoding="utf-8"))
    assert out["ok"] and out["worth"], out
    assert out["before"] == 0 and out["after"] >= len(sats) - 1
    assert _offset_km(out, TRUE) < 0.2, out                         # Ort auf < 200 m
    assert out["clock_s"] == pytest.approx(2.0, abs=0.3)
    assert out["night"] == "2024-07-02" and out["site"]["site_id"] == "home"
    # Kandidaten bleiben unverändert – übernommen wird erst nach Bestätigung
    assert all(c["site"]["lat"] == pytest.approx(WRONG["lat"]) for c in json.loads((obs / "kandidaten.json").read_text(encoding="utf-8")))


def test_search_skips_ambiguous_train(engine, tmp_path, monkeypatch, capsys):
    rs = engine
    _book(tmp_path / "settings.json", WRONG)
    sats = _satellites()
    _patch(monkeypatch, sats, extra=[_twin(sats[0], 0.05)])          # zweiter Satellit ~6 km hinter dem ersten
    run, obs = _run(tmp_path, sats, WRONG)
    rs.search(run, obs.name)
    out = json.loads((obs / "standortsuche.json").read_text(encoding="utf-8"))
    assert out["ok"] and out["trails"] == len(sats) - 1, out        # mehrdeutige Spur geht nicht in die Rückrechnung
    assert _offset_km(out, TRUE) < 0.2, out


def test_rematch_uses_changed_site_list(engine, tmp_path, monkeypatch, capsys):
    rs = engine
    _book(tmp_path / "settings.json", WRONG)
    sats = _satellites(4)
    _patch(monkeypatch, sats)
    run, obs = _run(tmp_path, sats, WRONG)
    rs.rematch([run])                                                # Ort unverändert → nichts zu tun
    res = _rows(capsys)[-1]["runs"][0]
    assert res["unchanged"] == len(sats) and res["checked"] == 0
    _book(tmp_path / "settings.json", TRUE)                          # Nutzer korrigiert die Ortsliste
    rs.rematch([run])
    res = _rows(capsys)[-1]["runs"][0]
    assert res["checked"] == len(sats) and res["after"] == len(sats), res
    cands = json.loads((obs / "kandidaten.json").read_text(encoding="utf-8"))
    assert all(c["site"]["lat"] == pytest.approx(TRUE["lat"]) and c["site"]["origin"] == "default" for c in cands)
    sat = next(r for r in cands[0]["catalogs"] if r["provider"] == "satellites")
    assert sat["status"] == "compatible" and sat["matches"][0]["norad_id"] == sats[0].satnum
    assert json.loads((obs / "kandidaten" / "K0001" / "kandidat.json").read_text(encoding="utf-8"))["site"]["lat"] == pytest.approx(TRUE["lat"])
    assert json.loads((run / "videos.json").read_text(encoding="utf-8"))[0]["site"]["site_id"] == "home"


def test_rematch_stop_keeps_finished_candidates(engine, tmp_path, monkeypatch, capsys):
    """Anhalten während des zweiten Abgleichs: der erste Kandidat steht auch in kandidaten.json und videos.json am neuen Ort."""
    rs = engine
    _book(tmp_path / "settings.json", TRUE)
    sats = _satellites(3)
    _patch(monkeypatch, sats)
    real, calls = rs.satellite_check, []

    def interrupted(item, site, settings):
        calls.append(item["id"])
        if len(calls) == 2:
            raise InterruptedError("Archivabruf angehalten")
        return real(item, site, settings)
    monkeypatch.setattr(rs, "satellite_check", interrupted)
    run, obs = _run(tmp_path, sats, WRONG)
    rs.rematch([run])
    res = _rows(capsys)[-1]
    assert res["stopped"] and res["runs"][0]["checked"] == 1, res
    cands = json.loads((obs / "kandidaten.json").read_text(encoding="utf-8"))
    assert cands[0]["site"]["lat"] == pytest.approx(TRUE["lat"]) and cands[1]["site"]["lat"] == pytest.approx(WRONG["lat"])
    assert json.loads((obs / "kandidaten" / "K0001" / "kandidat.json").read_text(encoding="utf-8"))["site"]["lat"] == pytest.approx(TRUE["lat"])
    assert json.loads((run / "videos.json").read_text(encoding="utf-8"))[0]["site"]["site_id"] == "home"


def test_rematch_keeps_old_result_without_local_orbits(engine, tmp_path, monkeypatch, capsys):
    rs = engine
    _book(tmp_path / "settings.json", TRUE)
    sats = _satellites(1)
    _patch(monkeypatch, sats)
    from archive_errors import ArchiveAccessError
    import satellite_archive

    def missing(start, settings):
        assert settings.get("orbits_cache_only")                    # Neuabgleich fragt keine Archive ab
        raise ArchiveAccessError("needs_archive", "nicht im Cache")
    monkeypatch.setattr(satellite_archive, "historical_models", missing)
    run, obs = _run(tmp_path, sats, WRONG)
    rs.rematch([run])
    res = _rows(capsys)[-1]["runs"][0]
    assert res["missing_orbits"] == 1 and res["checked"] == 0
    c = json.loads((obs / "kandidaten.json").read_text(encoding="utf-8"))[0]
    assert c["site"]["lat"] == pytest.approx(WRONG["lat"]) and c["catalogs"][0]["status"] == "checked_no_match"


# ---------------------------------------------------------------- Server (ohne skyfield) ---
def test_old_opensky_settings_are_ignored(engine, tmp_path):
    """Alte catalog_settings.json mit der entfernten OpenSky-Anbindung lädt ohne Fehler; die Schlüssel verschwinden."""
    import observation_settings as obs
    obs.CONFIG.parent.mkdir(parents=True, exist_ok=True)
    obs.CONFIG.write_text(json.dumps({"opensky_user": "jemand", "auto_aircraft": True, "auto_meteors": False, "flight_file": "x.csv"}), encoding="utf-8")
    s = obs.load_settings()
    assert "opensky_user" not in s and "auto_aircraft" not in s
    assert s["auto_meteors"] is False and s["flight_file"] == "x.csv"       # übrige Werte bleiben
    saved = obs.update_settings({"auto_satellites": False})
    assert "opensky_user" not in saved and "auto_aircraft" not in saved


def test_aircraft_check_needs_own_flight_data(engine):
    """Ohne eigene Flugdatei kein Flug-Abgleich (kein Trino mehr), auch wenn die alte Einstellung auto_aircraft noch gesetzt ist."""
    import catalog_matching as cm
    c = dict(timestamp=1.7e9, timestamp_text="UTC", exposure_seconds=10, world_path=[[10.0, 20.0]])
    r = cm.aircraft_check(c, dict(lat=49.0, lon=8.0, alt_m=100), {"auto_aircraft": True})
    assert r["status"] == "needs_archive"


def test_sites_signature_and_jobs(tmp_path, monkeypatch):
    from astroflow.modules.skytrace.rematch import sites_signature
    a = {"list": [{"id": "home", "name": "Testhausen", "lat": 47.5, "lon": 10.25, "accuracy_m": 100}], "default": "home", "periods": []}
    assert sites_signature(a) == sites_signature({**a, "list": [{**a["list"][0], "name": "Umbenannt", "note": "x"}]})   # Name egal
    assert sites_signature(a) != sites_signature({**a, "periods": [{"site_id": "home", "from": "2024-07-01", "to": None}]})
    assert sites_signature(a) != sites_signature({**a, "list": [{**a["list"][0], "lat": 47.51}]})


def test_rematch_api(tmp_path, monkeypatch):
    """Hinweis bei geänderter Ortsliste, Auftrag mit Fortschritt in der Aktivität, Ergebnis der Standortsuche."""
    from fastapi.testclient import TestClient
    from astroflow.core.config import load_settings
    from astroflow.core.store import SettingsStore
    from astroflow.server import create_app
    import astroflow.modules.skytrace.rematch as rm
    data = tmp_path / "data"
    home = tmp_path / "st"
    run = home / "Ergebnisse" / "2026-11-05_10-00-00-000001"
    (run / "obs").mkdir(parents=True)
    (run / "obs" / "kandidaten.json").write_text(json.dumps([{"id": "K0001", "world_path": [[1, 2], [1.1, 2.1]], "catalogs": []}]), encoding="utf-8")
    (run / "videos.json").write_text(json.dumps([{"session": "2024-07-02_observation_Testfeld", "video": "obs/video.mp4", "frames": 3,
                                                  "candidates_file": "obs/kandidaten.json"}]), encoding="utf-8")
    (run / "obs" / "standortsuche.json").write_text(json.dumps({"ok": True, "worth": True, "before": 0, "after": 4, "tracks": 5}), encoding="utf-8")
    data.mkdir()
    SettingsStore(data / "settings.json").update("skytrace", {"home": str(home)})
    _book(data / "settings.json", WRONG)
    # Attrappe der Engine: meldet Fortschritt und ein Ergebnis (der echte Abgleich ist oben getestet)
    fake = tmp_path / "fake_engine.py"
    fake.write_text("import json,sys,time\nprint(json.dumps({'type':'progress','current':1,'total':2,'text':'Spur 1'}),flush=True)\n"
                    "time.sleep(0.3)\nprint(json.dumps({'type':'result','runs':[],'text':'1 Spuren neu abgeglichen.'}),flush=True)\n", encoding="utf-8")
    monkeypatch.setattr(rm.SiteJobs, "SCRIPT", fake)
    monkeypatch.setenv("ASTROFLOW_MODULES", "stacking,skytrace")
    app = create_app(load_settings(data))
    with TestClient(app) as c:
        st = c.get("/api/skytrace/rematch").json()
        assert st["sites_changed"] is False                        # erster Blick merkt sich den Stand
        _book(data / "settings.json", TRUE)
        st = c.get("/api/status").json()["modules"]["skytrace"]["rematch"]
        assert st["sites_changed"] is True
        r = c.post("/api/skytrace/rematch", json={"all": True}).json()
        assert r["all"] and r["runs"] == [run.name] and r["force"] is False
        assert c.post("/api/skytrace/rematch", json={"all": True}).status_code == 400      # nur ein Auftrag zur Zeit
        act = [a for a in c.get("/api/activity").json() if a["key"].startswith("skytrace:abgleich:")]
        assert act and act[0]["title"] == "Alle SkyTrace-Analysen neu abgleichen"
        for _ in range(100):
            j = c.get("/api/skytrace/rematch").json()["job"]
            if j["state"] != "running":
                break
            time.sleep(0.05)
        assert j["state"] == "done" and j["text"] == "1 Spuren neu abgeglichen."
        assert c.get("/api/skytrace/rematch").json()["sites_changed"] is False             # Abgleich aller Läufe quittiert den Stand
        res = c.get(f"/api/skytrace/sitesearch?run={run.name}&folder=obs").json()
        assert res["result"]["after"] == 4 and res["job"] is None
        runs = c.get(f"/api/skytrace/run?id={run.name}").json()
        assert runs["sessions"][0]["trails"] == 1 and runs["sessions"][0]["site_search"]["worth"] is True
        assert c.post("/api/skytrace/sitesearch", json={"run": run.name, "folder": "../x"}).status_code == 400


if __name__ == "__main__":                                          # Engine-Interpreter ohne pytest
    import tempfile

    sys.path.insert(0, str(ROOT))

    class MP:
        def __init__(self): self.undo = []
        def setattr(self, obj, name, value): self.undo.append((obj, name, getattr(obj, name))); setattr(obj, name, value)
        def setenv(self, k, v): os.environ[k] = v
        def syspath_prepend(self, p): sys.path.insert(0, p)
        def close(self): [setattr(o, n, v) for o, n, v in reversed(self.undo)]; _unload()

    class Cap:
        def __init__(self): import io; self.buf = io.StringIO()
        def readouterr(self):
            out = self.buf.getvalue(); self.buf.seek(0); self.buf.truncate()
            return type("R", (), {"out": out})()
    import contextlib
    for test in (test_search_finds_site_without_matches, test_search_skips_ambiguous_train, test_rematch_uses_changed_site_list, test_rematch_stop_keeps_finished_candidates, test_rematch_keeps_old_result_without_local_orbits):
        mp, cap = MP(), Cap()
        with tempfile.TemporaryDirectory() as d:
            try:
                with contextlib.redirect_stdout(cap.buf):
                    test(_engine(Path(d), mp), Path(d), mp, cap)
            finally:
                mp.close()
        print("OK", test.__name__)
