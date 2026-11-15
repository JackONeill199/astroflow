"""Freier Himmel (modules/telescope: skymap, skyscan, Autopilot): Himmelsscan im Simulator, Wolken, Abbruch, gelernte
Aufstellungen, Wirkung auf Autopilot und Heute Nacht. Nur Simulator mit virtueller Uhr; Ort und Zeiten erfunden."""
from __future__ import annotations

import datetime as dt
import json

from fastapi.testclient import TestClient

from astroflow.core import security
from astroflow.core.config import load_settings
from astroflow.modules.telescope import autopilot as ap, sim, skymap, skyscan
from astroflow.server import create_app

SITE = {"id": "t1", "name": "Testbalkon", "lat": 50.0, "lon": 10.0, "horizon": None}
T0 = dt.datetime(2026, 11, 20, 17, 0, tzinfo=dt.timezone.utc).timestamp()      # schon dunkel
H = 3600


def make(scenario, store=None, items=(), cfg=None, forecast=None, frame_points=None, rain=None, t0=T0, site=None):
    clock = sim.ManualClock(t0)
    d = sim.SimDriver(clock, scenario)
    env = ap.Env(site=lambda n: site or SITE, plan=lambda n, m: [ap.Item(**vars(i)) for i in items], forecast=forecast,
                 sky_store=store if store is not None else skymap.SkyStore(None), frame_points=frame_points, rain=rain)
    p = ap.Autopilot(d, env, cfg, clock=clock)
    d.connect()
    d.take_control()
    return p, d, clock


def scan_until_done(p, clock, limit_s=2 * H, step=5):
    t = 0
    while (p.scan is not None or p.mode == "laeuft" or p.park_pending) and t < limit_s:
        p.tick(clock.now())
        clock.advance(step)
        t += step
    return t


def bin_(az):
    return skymap.bin_of(az)


def texts(p):
    return " | ".join(e["text"] for e in p.events)


# ------------------------------------------------------------------- Karte --- #
def test_karte_aus_punkten_hebt_an_senkt_nur_mit_sternen():
    pts = [{"az": 90, "alt": 15, "res": "verdeckt", "sure": True}, {"az": 90, "alt": 25, "res": "frei", "sure": True},
           {"az": 180, "alt": 20, "res": "frei", "sure": True}, {"az": 180, "alt": 70, "res": "verdeckt", "sure": True},
           {"az": 270, "alt": 40, "res": "verdeckt", "sure": True}]
    base = [0.0] * 36
    base[18] = 45.0                                  # Ortsmaske sagt 45° im Süden – heute waren dort bei 20° Sterne
    hz = skymap.horizon(pts, base)
    assert 17 <= hz["horizon"][9] <= 25              # zwischen verdeckt (15) und frei (25)
    assert hz["horizon"][18] == 20.0                 # abgesenkt: nachweislich Sterne bei 20°
    assert hz["top"][18] == 45.0                     # Dachüberstand über einem freien Punkt (Mitte zwischen 20 und 70)
    assert hz["horizon"][27] == 42.0 and hz["horizon"][26] == 42.0 and hz["horizon"][28] == 42.0   # verdeckt wirkt ±15°
    assert hz["horizon"][0] == 0.0 and not hz["known"][0]
    assert skymap.state_at(hz["cols"], 90, 30) == "frei" and skymap.state_at(hz["cols"], 270, 30) == "verdeckt"


def test_aufstellungen_trennen_und_ferne_hindernisse():
    def night(shift, far=True):
        p = []
        for az in range(0, 360, 30):
            near = 60 + shift <= az <= 120 + shift                         # nahe Hauswand, je Aufstellung woanders
            p.append({"az": az, "alt": 40 if near else 25, "res": "verdeckt" if near else "frei", "sure": True, "src": "scan"})
        if far:                                                            # fernes Haus im Westen – überall gleich
            p += [{"az": 270, "alt": 20, "res": "verdeckt", "sure": True, "src": "scan"}]
        return p
    nights = {"2026-11-01": night(0), "2026-11-02": night(0), "2026-11-03": night(90), "2026-11-05": night(0)}
    g = skymap.setups(nights)
    assert len(g) == 2
    assert g[0]["nights"] == ["2026-11-01", "2026-11-02", "2026-11-05"] and g[1]["nights"] == ["2026-11-03"]
    wide = skymap.site_wide(g)
    assert wide[27] is not None and wide[9] is None                       # Westen gilt überall, Hauswand nicht
    probes = skymap.probes_to_tell(g, 15)
    assert probes and len(probes) <= 3
    got = skymap.match(g, [{**probes[0], "res": "frei" if skymap.state_at(g[1]["cols"], probes[0]["az"], probes[0]["alt"]) == "frei" else "verdeckt"}])
    assert got["id"] == g[1]["id"]


def test_wolke_wandert_hindernis_bleibt():
    """Verdeckt aus Zielen/Aufnahmen (unsicher) zählt erst, wenn es sich in einer anderen Nacht wiederholt."""
    a = [{"az": 200, "alt": 30, "res": "verdeckt", "sure": False, "src": "ziel"}, {"az": 90, "alt": 30, "res": "frei", "sure": True, "src": "ziel"}]
    b = [{"az": 205, "alt": 28, "res": "verdeckt", "sure": False, "src": "bild"}, {"az": 90, "alt": 28, "res": "frei", "sure": True, "src": "bild"}]
    c = [{"az": 300, "alt": 30, "res": "verdeckt", "sure": False, "src": "bild"}, {"az": 90, "alt": 29, "res": "frei", "sure": True, "src": "bild"}]
    g = skymap.setups({"2026-11-01": a, "2026-11-02": b, "2026-11-03": c})
    assert len(g) == 1
    cols = g[0]["cols"]
    assert cols[bin_(200)]["lo"] is not None                              # Hindernis: zwei Nächte
    assert cols[bin_(300)]["lo"] is None                                  # Wolke: nur einmal


def test_ablage_ohne_koordinaten(tmp_path):
    st = skymap.SkyStore(tmp_path / "himmel.json")
    st.put_night("t1", "2026-11-20", [{"az": 1.0, "alt": 2.0, "res": "frei", "sure": True, "src": "scan", "t": 5, "lat": 50.0}])
    raw = (tmp_path / "himmel.json").read_text(encoding="utf-8")
    assert "lat" not in raw and "50.0" not in raw
    assert skymap.SkyStore(tmp_path / "himmel.json").nights("t1")["2026-11-20"][0]["az"] == 1.0


# -------------------------------------------------------------------- Scan --- #
def test_himmelsscan_balkon_im_simulator(tmp_path):
    store = skymap.SkyStore(tmp_path / "himmel.json")
    p, d, clock = make(sim.Scenario.balcony(T0), store)
    assert p.start_scan(clock.now()) is None
    took = scan_until_done(p, clock)
    assert p.mode == "aus" and d.parked and p.scan is None
    assert took < 30 * 60                                              # ganzer Himmel in unter einer halben Stunde
    hz = p.horizon()
    for az in (0, 30, 330):                                            # Hauswand im Norden
        assert hz["horizon"][bin_(az)] >= 50
    assert hz["horizon"][bin_(180)] <= 25 and hz["cols"][bin_(180)]["hi"] is not None   # Süden frei
    assert 30 <= hz["horizon"][bin_(210)] <= 40                        # Nachbarhaus bis 34°
    assert hz["top"][bin_(240)] is not None and hz["top"][bin_(240)] >= 55               # Dachüberstand ab 66°
    assert p.last_scan["state"] == "fertig" and "Himmel vermessen" in texts(p)
    assert store.nights("t1")[p.night]                                 # gespeichert
    assert all(c in ("observe", "stop", "open", "init", "park", "takeControl") or c.startswith("observe:scan-")
               for c in [x.split(":")[0] if not x.startswith("observe:scan-") else x for x in d.commands])
    # Wirkung im Autopiloten: Richtung Hauswand ist zu tief, Süden frei
    ra, dec = ap.sky.radec(0, 45, SITE["lat"], SITE["lon"], clock.now())
    assert not p.free(ra, dec, clock.now())[0]
    ra, dec = ap.sky.radec(180, 40, SITE["lat"], SITE["lon"], clock.now())
    assert p.free(ra, dec, clock.now())[0]


def test_scan_bei_wolken_endet_ohne_falsche_hindernisse():
    p, d, clock = make(sim.Scenario(clouds=[(T0, T0 + 5 * H)]))
    p.start_scan(clock.now())
    scan_until_done(p, clock)
    assert p.last_scan["state"] == "wolken" and p.clouds_at is not None
    assert d.parked
    assert not any(p.horizon()["known"])                               # nichts Sicheres → Maske unverändert
    assert "Wolken" in texts(p)


def test_gegenprobe_wolkenzug_wiederholt_zweifel():
    """Wolke zieht mitten im Scan durch: Gegenprobe scheitert → verdeckte Punkte seitdem werden wiederholt, am Ende stimmt die Karte."""
    sc = sim.Scenario.balcony(T0, clouds=[(T0 + 6 * 60, T0 + 9 * 60)])
    p, d, clock = make(sc)
    p.start_scan(clock.now())
    scan_until_done(p, clock)
    assert p.last_scan["state"] == "fertig"
    whys = [x["why"] for x in p.last_scan["points"]]
    assert "wiederholung" in whys and "gegenprobe" in whys
    hz = p.horizon()
    assert hz["horizon"][bin_(180)] <= 25                              # Süden trotz Wolke frei


def test_scan_vorhersage_bewoelkt_startet_nicht():
    fc = lambda site, a, b: {"hours": [{"t": T0 + k * H, "cloud": 90, "wind": 5, "dew_spread": 5} for k in range(-1, 12)]}
    p, d, clock = make(sim.Scenario(), forecast=fc)
    p.load_night(clock.now())
    p._refresh_weather(clock.now())
    why = p.start_scan(clock.now())
    assert why and "Wolken" in why and p.scan is None and "open" not in d.commands


def test_scan_regen_hat_vorrang():
    state = {"rain": False}
    rain = lambda site, minutes: {"rain": state["rain"], "in_minutes": 10 if state["rain"] else None, "probability": 90}
    p, d, clock = make(sim.Scenario.balcony(T0), rain=rain)
    p.start_scan(clock.now())
    for _ in range(80):                                                # ~7 min: einige Punkte gemessen
        p.tick(clock.now())
        clock.advance(5)
    n = len(p.scan.done)
    assert n >= 3
    state["rain"] = True
    p.rain_t = 0
    for _ in range(60):
        p.tick(clock.now())
        clock.advance(5)
    assert p.scan is None and p.mode == "aus" and d.parked
    assert p.last_scan["state"] == "abgebrochen" and len([x for x in p.night_points if x["src"] == "scan"]) >= n - 1
    assert "Regen" in texts(p)


def test_scan_mitten_im_plan_setzt_plan_fort():
    m31 = ap.Item("M31", "M31", 10.68, 41.27, T0, T0 + 4 * H, hours=4)
    p, d, clock = make(sim.Scenario.balcony(T0), items=[m31])
    p.start(clock.now())
    for _ in range(60):
        p.tick(clock.now())
        clock.advance(5)
    assert p._current() is not None
    p.start_scan(clock.now())
    assert p._current() is None and not p.scan_only
    scan_until_done(p, clock, limit_s=40 * 60)
    for _ in range(40):
        p.tick(clock.now())
        clock.advance(5)
    assert p.scan is None and p.mode == "laeuft"
    assert p._current() is not None and p._current().id == "M31"       # Plan läuft weiter


def test_kurzscan_erkennt_aufstellung(tmp_path):
    """Zwei frühere Nächte am Balkon (gleiche Aufstellung) und eine anders aufgestellte: in der neuen Nacht erkennt der
    Autopilot die Aufstellung an wenigen Peilpunkten und prüft nur noch die Grenzen."""
    store = skymap.SkyStore(tmp_path / "himmel.json")
    for k, shift in enumerate((0.0, 0.0, 60.0)):
        t = T0 - (3 - k) * 86400
        p, d, clock = make(sim.Scenario.balcony(t, shift=shift), store, t0=t)
        p.start_scan(clock.now())
        scan_until_done(p, clock)
        assert p.last_scan["state"] == "fertig"
    m31 = ap.Item("M15", "M15", 322.49, 12.17, T0, T0 + 3 * H, hours=3)
    p, d, clock = make(sim.Scenario.balcony(T0), store, items=[m31])
    p.start(clock.now())
    assert len(p.groups) == 2
    t = 0
    while t < 40 * 60 and not p.last_scan:
        p.tick(clock.now())
        clock.advance(5)
        t += 5
    sc = p.last_scan
    assert sc and sc["kind"] == "kurz" and sc["setup"] is not None
    assert sc["n"] <= 12 and sc["elapsed"] < 12 * 60
    assert p.setup and len(p.setup["nights"]) == 2
    assert p.horizon()["horizon"][bin_(0)] >= 50                        # Hauswand aus der gelernten Aufstellung


def test_lernen_aus_aufnahmen_senkt_maske():
    """Frühere Aufnahmen (Dienst `frame_sky_points`): wo Sterne waren, darf die Karte die Maske senken."""
    site = {**SITE, "horizon": [40.0] * 36}
    pts = {"2026-11-10": [{"az": 180, "alt": 25, "res": "frei", "sure": True, "src": "bild"}],
           "2026-11-12": [{"az": 180, "alt": 26, "res": "frei", "sure": True, "src": "bild"}]}
    p, d, clock = make(sim.Scenario(), frame_points=lambda sid: pts, site=site)
    p.load_night(clock.now())
    assert len(p.groups) == 1
    p._add_point({"az": 180, "alt": 30, "res": "frei", "sure": True, "src": "ziel", "t": clock.now()})
    assert p.setup is None or p.setup["id"] == "a1"
    p.setup = p._which_setup()
    hz = p.horizon()
    assert hz["horizon"][bin_(180)] <= 26 and hz["horizon"][bin_(90)] == 40.0


# --------------------------------------------------------------------- API --- #
def _app(tmp_path, monkeypatch, **cfg):
    data = tmp_path / "data"
    data.mkdir(exist_ok=True)
    (data / "settings.json").write_text(json.dumps({"sites": {"list": [{k: v for k, v in SITE.items() if k != "horizon"}], "default": "t1"},
                                                    "telescope": {"sim_speed": 1, "sim_start": "now", "sim_scenario": "balkon", **cfg}}),
                                        encoding="utf-8")
    monkeypatch.setattr(security, "lan_addresses", lambda: ["192.0.2.10"])
    monkeypatch.setenv("ASTROFLOW_MODULES", "stacking,telescope")
    return create_app(load_settings(data))


def test_api_scan_annehmen_und_simulator_getrennt(tmp_path, monkeypatch):
    with TestClient(_app(tmp_path, monkeypatch), base_url="http://127.0.0.1:8766", client=("127.0.0.1", 50000),
                    headers={"Origin": "http://127.0.0.1:8766"}) as c:
        st = c.get("/api/telescope/state").json()
        assert st["sky"]["scan"] is None and not st["sky"]["can_accept"]
        assert c.post("/api/telescope/sky/accept").status_code == 409
        mod = _telescope(c.app)
        r = c.post("/api/telescope/sky/scan", json={})
        assert r.status_code in (200, 409)                               # 409 nur, wenn es (echte Uhr) gerade hell ist
        if r.status_code == 200:
            assert c.get("/api/telescope/state").json()["sky"]["scanning"]
            assert c.post("/api/telescope/sky/stop").status_code == 200
            assert not c.get("/api/telescope/state").json()["sky"]["scanning"]
        assert '"lat"' not in json.dumps(c.get("/api/telescope/state").json()["sky"])
        assert mod.horizon() is None                      # Simulator: nie in Heute Nacht
    assert not (tmp_path / "data" / "telescope" / "himmel.json").exists()   # Simulator schreibt nie in die echte Karte


def test_accept_uebernimmt_nur_gemessene_richtungen(tmp_path, monkeypatch):
    with TestClient(_app(tmp_path, monkeypatch, driver="vaonis-read"), base_url="http://127.0.0.1:8766", client=("127.0.0.1", 50000),
                    headers={"Origin": "http://127.0.0.1:8766"}) as c:
        c.get("/api/telescope/state")
        mod = _telescope(c.app)
        p = mod.unit.pilot
        now = mod.unit.now()
        p.load_night(now)
        for az, alt, res in ((0, 40, "verdeckt"), (0, 55, "frei"), (180, 20, "frei")):
            p._add_point({"az": az, "alt": alt, "res": res, "sure": True, "src": "scan", "t": now})
        assert c.get("/api/telescope/state").json()["sky"]["can_accept"]
        assert c.post("/api/telescope/sky/accept").status_code == 200
        site = c.get("/api/sites").json()["list"][0]
        assert 42 <= site["horizon"][0] <= 55 and site["horizon"][9] == 0.0


def _telescope(app):
    return app.state.modules["telescope"]


def test_heute_nacht_nutzt_gemessene_grenze(tmp_path, monkeypatch):
    """Dienst `telescope_horizon`: Heute Nacht der aktuellen Nacht rechnet mit der gemessenen Grenze."""
    data = tmp_path / "data"
    data.mkdir()
    (data / "settings.json").write_text(json.dumps({"sites": {"list": [{k: v for k, v in SITE.items() if k != "horizon"}], "default": "t1"}}),
                                        encoding="utf-8")
    monkeypatch.setattr(security, "lan_addresses", lambda: ["192.0.2.10"])
    monkeypatch.setenv("ASTROFLOW_MODULES", "stacking")
    app = create_app(load_settings(data))
    seen = []
    with TestClient(app, base_url="http://127.0.0.1:8766", client=("127.0.0.1", 50000), headers={"Origin": "http://127.0.0.1:8766"}) as c:
        svc = _services(app)
        svc["telescope_horizon"] = lambda night: (seen.append(night), {"horizon": [35.0] * 36, "night": night, "source": "Testgerät"})[1]
        d = c.get("/api/stacking/tonight").json()
        assert seen and d["horizon"] == [35.0] * 36 and "Testgerät" in d["site_info"]["horizon_src"]
        d1 = c.get("/api/stacking/tonight?night=1").json()
        assert not d1.get("horizon")                                     # andere Nacht: Ortsmaske (hier keine)


def _services(app):
    return app.state.ctx.services


def test_not_aus_beendet_scan_sofort():
    p, d, clock = make(sim.Scenario.balcony(T0))
    p.start_scan(clock.now())
    for _ in range(40):
        p.tick(clock.now())
        clock.advance(5)
    assert p.scan is not None and not d.parked
    p.emergency(clock.now())
    assert p.scan is None and not p.scan_only and p.mode == "aus"
    for _ in range(30):
        p.tick(clock.now())
        clock.advance(5)
    assert d.parked and p.last_scan["state"] == "abgebrochen"
    n = len(d.commands)
    for _ in range(20):                                                # nach Not-Aus keine Bewegung mehr
        p.tick(clock.now())
        clock.advance(5)
    assert len(d.commands) == n


def test_simulator_aendert_nie_die_ortsmaske(tmp_path, monkeypatch):
    with TestClient(_app(tmp_path, monkeypatch), base_url="http://127.0.0.1:8766", client=("127.0.0.1", 50000),
                    headers={"Origin": "http://127.0.0.1:8766"}) as c:
        c.get("/api/telescope/state")
        p = _telescope(c.app).unit.pilot
        p.load_night(p._now())
        p._add_point({"az": 0, "alt": 40, "res": "verdeckt", "sure": True, "src": "scan", "t": p._now()})
        assert not c.get("/api/telescope/state").json()["sky"]["can_accept"]
        assert c.post("/api/telescope/sky/accept").status_code == 409
        assert not c.get("/api/sites").json()["list"][0].get("horizon")


def test_verdeckt_vor_erster_gegenprobe_wird_wiederholt():
    """Wolke über den ersten Punkten, dann klar: verdeckte Punkte vor dem ersten freien werden nicht bestätigt, sondern neu gemessen."""
    sc = skyscan.Scan("voll", T0)
    for res in ("verdeckt", "verdeckt"):
        sc.next_probe(T0)
        sc.result(res, T0)
    first = [p for p in sc.done if p["res"] == "verdeckt"]
    sc.next_probe(T0)
    sc.result("frei", T0, solved=True)
    assert sc.ref and not any(p["sure"] for p in first)
    assert sum(1 for q in sc.queue if q["why"] == "wiederholung") == len(first)
