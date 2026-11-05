"""SkyTrace Ortssuche ohne Vorwissen (engine/global_site.py): Teleskop stand Hunderte Kilometer von jedem Listenort (Urlaub), die
Kamera-Uhr ging zusätzlich falsch → Ort und Uhr allein aus den Spuren. Synthetische LEO-Bahnen, erfundene Orte. Engine-Teil braucht
skyfield – direkt im Engine-Interpreter: `python tests/test_skytrace_global.py`."""
import contextlib
import io
import json
import math
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_skytrace_rematch import T0, TRUE, _engine, _offset_km, _patch, _satellites, _unload, pytest  # noqa: E402
from test_skytrace_nights import HOME, NIGHT, _sites  # noqa: E402

CLOCK = -300.0                  # wahre UTC = Kamerazeit + CLOCK → Kamera-Uhr ging 5 min vor
# Listenort rund 300 km südöstlich des wahren Orts (erfunden)
FAR = {"lat": TRUE["lat"] - 1.9, "lon": TRUE["lon"] + 2.7, "alt_m": 0.0}


@pytest.fixture
def engine(tmp_path, monkeypatch):
    pytest.importorskip("skyfield")
    pytest.importorskip("sgp4")
    yield _engine(tmp_path, monkeypatch)
    _unload()


def _distractors(n=40, seed=3):
    """Weitere erfundene LEO-Bahnen (zufällige Ebenen) – liefern zufällige Bodenpunkte wie im echten Archiv."""
    from sgp4.api import Satrec, WGS72
    rnd = np.random.default_rng(seed)
    out = []
    for k in range(n):
        s = Satrec()
        s.sgp4init(WGS72, "i", 80000 + k, T0 / 86400 + 2440587.5 - 2433281.5, 1e-5, 0, 0, 1e-4, 0.0, math.radians(rnd.uniform(40, 98)),
                   math.radians(rnd.uniform(0, 360)), 2 * math.pi / rnd.uniform(92, 105), math.radians(rnd.uniform(0, 360)))
        out.append(s)
    return out


def _observation(tmp: Path, sats, clock=CLOCK, shift_deg=0.0):
    """Eine Beobachtung: je Satellit eine Spur quer durchs Bild (2 s einer 10-s-Belichtung, an verschiedenen Stellen der Belichtung),
    am wahren Ort gemessen; Kamerazeit = wahre Zeit − clock."""
    from astroflow.modules.skytrace import orbit3d, sitefit
    run = "2026-11-05_10-00-00-000001"
    name = f"{NIGHT}_observation_Urlaub"
    root = tmp / "st" / "Ergebnisse" / run
    obs = root / name
    (obs / "kandidaten").mkdir(parents=True)
    cands = []
    for i, s in enumerate(sats):
        start = T0 + 40 * i                                     # wahre Zeit des Belichtungsbeginns
        tau = (1.0, 4.0, 7.0)[i % 3]
        tt = np.linspace(start + tau, start + tau + 2, 5)
        u = sitefit.topo_directions(s, tt, TRUE["lat"], TRUE["lon"], TRUE["alt_m"], orbit3d.icrs_to_teme(start + 5))
        ra, dec = (np.degrees(np.arctan2(u[:, 1], u[:, 0])) + shift_deg * (1 + i % 2)) % 360, np.degrees(np.arcsin(u[:, 2]))
        c = {"id": f"K{i + 1:04d}", "session": name, "kind": "trail", "timestamp": start - clock, "timestamp_text": "UTC", "exposure_seconds": 10.0,
             "time_reference": "start", "source_file": str(tmp / "fehlt.fits"), "world_path": np.column_stack([ra, dec])[[0, -1]].tolist(),
             "path": [[0, 100], [999, 700]], "context_image": {"width": 1000, "height": 800, "image": f"{name}/kandidaten/K{i + 1:04d}/kontext.png"},
             "site": dict(FAR, accuracy_m=100), "catalogs": [{"provider": "satellites", "status": "checked_no_match", "matches": []}]}
        (obs / "kandidaten" / c["id"]).mkdir()
        cands.append(c)
    (obs / "kandidaten.json").write_text(json.dumps(cands), encoding="utf-8")
    (root / "videos.json").write_text(json.dumps([{"session": name, "video": f"{name}/video.mp4", "frames": 20, "hits": len(sats),
                                                   "candidates_file": f"{name}/kandidaten.json"}]), encoding="utf-8")
    return {"run": run, "folder": name, "session": name}, [(obs, c) for c in cands]


def _setup(engine, tmp_path, monkeypatch, shift_deg=0.0, n=6):
    _sites(tmp_path / "settings.json", [{**HOME, **FAR}], "home")
    sats = _satellites(n)
    assert len(sats) == n
    _patch(monkeypatch, sats, extra=_distractors())
    return _observation(tmp_path, sats, shift_deg=shift_deg)


def _global(engine, entries):
    import global_site
    settings = engine.offline_settings()
    with contextlib.redirect_stdout(io.StringIO()):
        return global_site.search_night(engine.sitefit_module(), entries, settings, [dict(lat=FAR["lat"], lon=FAR["lon"])], region_km=1500)


def test_finds_far_site_and_clock(engine, tmp_path, monkeypatch):
    """300 km vom Listenort, Uhr −300 s: am Listenort keine Spur, ohne Vorwissen Ort < 5 km und Uhr ± 5 s, danach alle eindeutig."""
    _, entries = _setup(engine, tmp_path, monkeypatch)
    settings = engine.offline_settings()
    far = engine.site_for(entries[0][1], {}, settings)
    assert _offset_km(far, TRUE) > 250
    assert all(engine.check(c, far, dict(settings)).get("status") != "compatible" for _, c in entries)
    out = _global(engine, entries)
    p = out.get("proposal")
    assert p, out
    assert _offset_km(p, TRUE) < 5, (p, _offset_km(p, TRUE))
    assert p["clock_s"] == pytest.approx(CLOCK, abs=5), p
    assert p["sure"] == len(entries) and p["support"] >= 5 and p["false_alarm"] < 0.01
    assert [s["clock_s"] for s in out["stages"]] == [60.0, 1200.0]       # mit normaler Uhr nichts, erst ± 20 min


def test_random_trails_no_proposal(engine, tmp_path, monkeypatch):
    """Spuren, die zu keiner Bahn passen (Himmelsrichtung verdreht): kein Vorschlag, ehrlicher Grund."""
    _, entries = _setup(engine, tmp_path, monkeypatch, shift_deg=25.0)
    out = _global(engine, entries)
    assert "proposal" not in out and out["reason"], out


def test_nights_proposes_unknown_site(engine, tmp_path, monkeypatch):
    """„Standorte aller Nächte“ mit Suche ohne Vorwissen: neuer Ort `global` samt Uhr; ohne die Einstellung kein Vorschlag."""
    obs, _ = _setup(engine, tmp_path, monkeypatch)
    groups, out = tmp_path / "gruppen.json", tmp_path / "naechte.json"
    groups.write_text(json.dumps([{"night": NIGHT, "observations": [obs]}]), encoding="utf-8")
    with contextlib.redirect_stdout(io.StringIO()):
        engine.nights(groups, out, clock=False)
    n = json.loads(out.read_text(encoding="utf-8"))["nights"][0]
    assert n["status"] == "none" and not n["proposal"] and "global" not in n, n
    with contextlib.redirect_stdout(io.StringIO()) as log:
        engine.nights(groups, out, global_km=1500)                         # mit Uhrsuche: ± 20 min auch ohne Vorwissen
    data = json.loads(out.read_text(encoding="utf-8"))
    n = data["nights"][0]
    assert data["global_km"] == 1500 and n["status"] == "better", n
    p = n["proposal"]
    assert p["kind"] == "global" and _offset_km(p, TRUE) < 5 and p["sure"] == 6 and p["alt_m"] == 0
    assert p["nearest_name"] == HOME["name"] and p["nearest_km"] > 250 and p["from_name"] == "Unbekannter Ort"
    q = n["clock"]["proposal"]
    assert n["clock"]["source"] == "global" and q["offset_s"] == pytest.approx(CLOCK, abs=5) and q["nights"] == [NIGHT]
    assert n["assign"] == [NIGHT] and "ohne Vorwissen" in n["note"]
    assert "unbekannte Orte zurückgerechnet" in log.getvalue()


def test_good_list_site_skips_global(engine, tmp_path, monkeypatch):
    """Passt ein Listenort, läuft die Suche ohne Vorwissen gar nicht (normale Nächte werden nicht langsamer)."""
    obs, _ = _setup(engine, tmp_path, monkeypatch)
    _sites(tmp_path / "settings.json", [{**HOME, **FAR}, {"id": "urlaub", "name": "Ferienort", "accuracy_m": 100, **TRUE}], "home")
    import catalog_matching
    path = tmp_path / "st" / "catalog_settings.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"clock_offsets": {NIGHT: {"offset_s": CLOCK}}}), encoding="utf-8")   # Uhr schon korrigiert
    groups, out = tmp_path / "gruppen.json", tmp_path / "naechte.json"
    groups.write_text(json.dumps([{"night": NIGHT, "observations": [obs]}]), encoding="utf-8")
    import global_site
    called = []
    monkeypatch.setattr(global_site, "search_night", lambda *a, **k: called.append(1) or {})
    with contextlib.redirect_stdout(io.StringIO()):
        engine.nights(groups, out, clock=False, global_km=1500)
    n = json.loads(out.read_text(encoding="utf-8"))["nights"][0]
    assert not called and n["proposal"]["kind"] == "list" and n["proposal"]["id"] == "urlaub", n
    assert catalog_matching.night_key(T0) == NIGHT


def test_ground_points_geometry():
    """Rückprojektion: Bodenpunkt liegt auf dem Ellipsoid, und von dort steht der Satellit genau in der Blickrichtung."""
    pytest.importorskip("erfa")
    from test_skytrace_rematch import ENGINE
    sys.path.insert(0, str(ENGINE))
    import global_site as gs
    from astroflow.modules.skytrace import orbit3d
    g0 = orbit3d.geodetic_to_ecef(TRUE["lat"], TRUE["lon"], 0.0)
    up = g0 / np.linalg.norm(g0)
    d = up + np.array([0.2, -0.1, 0.05]); d /= np.linalg.norm(d)
    r = g0 + 900.0 * d                                                   # Satellit 900 km in Richtung d
    g, rho, ok = gs.ground_points(r[None], d)
    assert ok[0] and rho[0] == pytest.approx(900.0, abs=1e-6) and np.allclose(g[0], g0, atol=1e-6)
    lat, lon = gs.geodetic(g, 0.0)
    assert lat[0] == pytest.approx(TRUE["lat"], abs=1e-6) and lon[0] == pytest.approx(TRUE["lon"], abs=1e-6)
    _, _, ok = gs.ground_points(r[None], -d)                              # Blick vom Boden weg: kein Schnitt
    assert not ok[0]


def test_grid_covers_distant_centers():
    """Weit auseinanderliegende Listenorte: Stimmen am Rand eines Suchgebiets landen in ihrem eigenen Block, nicht an den Gitterrand geschoben."""
    from test_skytrace_rematch import ENGINE
    sys.path.insert(0, str(ENGINE))
    import global_site as gs
    centers = [dict(lat=0.0, lon=10.0), dict(lat=60.0, lon=10.0)]
    grid = gs.Grid(centers, 500, 60)
    pts = {k: np.array([v]) for k, v in dict(lat=0.1, lon=10.2, c_lo=-2.0, c_hi=2.0, tol=2.5, seg=7.0, norad=1, t=0.0, rec=0).items()}
    keys = grid.keys(pts)
    assert len(keys)
    lat, lon, _ = grid.center(int(keys.max()))
    assert abs(lat - 0.1) < 1 and abs(lon - 10.2) < 1, (lat, lon)
    far = {k: np.array([v]) for k, v in dict(lat=-30.0, lon=10.0, c_lo=-2.0, c_hi=2.0, tol=2.5, seg=7.0, norad=1, t=0.0, rec=0).items()}
    assert len(grid.keys(far)) == 0                                       # außerhalb: verworfen statt am Rand gezählt


def test_server_passes_global_km(tmp_path, monkeypatch):
    """Server: Suchgebiet wird geprüft und an die Engine durchgereicht (Standard 1500 km, 0 = aus)."""
    from astroflow.modules.skytrace import rematch as rm

    class Mod:
        home = tmp_path
        python = sys.executable
        class ctl:
            results = tmp_path / "Ergebnisse"
            @staticmethod
            def snapshot(logs=False): return {"running": False, "run": None}
        @staticmethod
        def engine_env(): return {}
    jobs = rm.SiteJobs(Mod)
    seen = {}
    monkeypatch.setattr(rm.SiteJobs, "_launch", lambda self, job, args: seen.update(job=job, args=args) or job)
    import astroflow.modules.skytrace.stats as stats
    monkeypatch.setattr(stats, "collect", lambda root, skip=None: {"sessions": [{"night": NIGHT, "folder": "obs", "candidate_count": 2, "run": "r", "session": "s"}]})
    with pytest.raises(ValueError):
        jobs.start_nights(10, True, 50)
    jobs.start_nights(10, True, 1500)
    assert seen["args"][seen["args"].index("--global-km") + 1] == "1500.0" and seen["job"]["global_km"] == 1500
    jobs.start_nights(10, False, 0)
    assert seen["args"][seen["args"].index("--global-km") + 1] == "0.0" and "--no-clock" in seen["args"]


if __name__ == "__main__":
    import os, tempfile
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

    class MP:
        def __init__(self): self.undo = []
        def setattr(self, obj, name, value): self.undo.append((obj, name, getattr(obj, name))); setattr(obj, name, value)
        def setenv(self, k, v): os.environ[k] = v
        def syspath_prepend(self, p): sys.path.insert(0, p)
        def close(self): [setattr(o, n, v) for o, n, v in reversed(self.undo)]; _unload()
    test_ground_points_geometry(); print("OK test_ground_points_geometry")
    test_grid_covers_distant_centers(); print("OK test_grid_covers_distant_centers")
    for test in (test_finds_far_site_and_clock, test_random_trails_no_proposal, test_nights_proposes_unknown_site, test_good_list_site_skips_global):
        mp = MP()
        with tempfile.TemporaryDirectory() as d:
            try:
                test(_engine(Path(d), mp), Path(d), mp)
            finally:
                mp.close()
        print("OK", test.__name__)
