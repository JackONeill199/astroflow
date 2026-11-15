"""Teleskopsteuerung (modules/telescope): Simulator, Zustandsautomat, Autopilot mit Wächtern, Vaonis-Übersetzung, API.

Alles läuft gegen den Simulator mit virtueller Uhr – kein Test spricht ein echtes Gerät an, die Netzsperre bleibt.
Ort und Zeiten sind erfunden (50° N, 10° O, Nacht 20./21.11.2026).
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from astroflow.core import security
from astroflow.core.config import load_settings
from astroflow.modules.telescope import autopilot as ap, machine, plugins, sim, vaonis_read as vaonis
from astroflow.modules.telescope.driver import DriverError, Target, TelescopeState
from astroflow.server import create_app

SITE = {"id": "t1", "name": "Testgarten", "lat": 50.0, "lon": 10.0, "horizon": None}
T0 = dt.datetime(2026, 11, 20, 17, 0, tzinfo=dt.timezone.utc).timestamp()      # früher Abend, schon dunkel (Sonne < −12°)
H = 3600
M31 = ("M31", "M31", 10.68, 41.27)
M45 = ("M45", "M45", 56.75, 24.12)          # steigt gegen 19 Uhr im Osten auf – dort steht im Szenario die „Hauswand“
M81 = ("M81", "M81", 148.89, 69.07)
M15 = ("M15", "M15", 322.49, 12.17)          # abends hoch im Süden – andere Richtung als M31
WALL = {"az0": 60.0, "az1": 120.0, "alt": 40.0}


def item(t, start, end):
    return ap.Item(t[0], t[1], t[2], t[3], T0 + start * H, T0 + end * H, hours=end - start)


def make(items, scenario=None, cfg=None, forecast=None, model="vespera"):
    clock = sim.ManualClock(T0)
    d = sim.SimDriver(clock, scenario or sim.Scenario(), model=model)
    p = ap.Autopilot(d, ap.Env(site=lambda n: SITE, plan=lambda n, m: [ap.Item(**vars(i)) for i in items], forecast=forecast),
                     cfg, clock=clock)
    d.connect()
    d.take_control()
    p.start(clock.now())
    return p, d, clock


def run(p, clock, hours, step=20):
    for _ in range(int(hours * H / step)):
        p.tick(clock.now())
        clock.advance(step)


def texts(p):
    return " | ".join(e["text"] for e in p.events)


# ------------------------------------------------------------------ Automat --- #
def test_phase_und_befehle():
    m = machine.Machine()
    m.update(TelescopeState(connected=True, master="us", parked=True))
    assert m.phase == "eingeklappt" and m.can("open") and not m.can("observe") and m.can("park")
    m.update(TelescopeState(connected=True, master="us", parked=False, initialized=True, step="capture"))
    assert m.phase == "nimmt_auf" and m.can("observe") and m.can("stop") and not m.can("open")
    assert machine.phase_of(TelescopeState(connected=True, master="other")) == "fremd"
    assert machine.phase_of(TelescopeState(connected=False)) == "getrennt"
    assert not m.plausible("eingeklappt", "nimmt_auf") and m.plausible("eingeklappt", "geoeffnet")


def test_simulator_braucht_kontrolle_und_offenen_arm():
    clock = sim.ManualClock(T0)
    d = sim.SimDriver(clock)
    with pytest.raises(DriverError):
        d.open()                                   # nicht verbunden
    d.connect()
    with pytest.raises(DriverError):
        d.open()                                   # keine Kontrolle
    d.take_control()
    with pytest.raises(DriverError):
        d.observe(Target("M31", "M31", 10.68, 41.27), 10)       # noch eingeklappt
    d.open()
    clock.advance(10)
    assert d.state().parked and d.state().step == "opening"     # Vaonis-Arm schwenkt erst von −90° hoch
    clock.advance(40)
    assert not d.state().parked and d.state().alt == pytest.approx(sim.OPEN_ALT)
    d.init(50, 10)
    clock.advance(100)
    d.observe(Target("M31", "M31", 10.68, 41.27), 10)
    clock.advance(300)
    s = d.state()
    assert s.step == "capture" and s.stacked > 0 and s.alt > 30


# ---------------------------------------------------------------- Autopilot --- #
def test_ganze_nacht_und_einklappen_am_ende():
    p, d, clock = make([item(M31, 0.2, 1.5), item(M81, 1.5, 3.0)])
    run(p, clock, 3.5)
    st = {i.id: i.status for i in p.items}
    assert st == {"M31": "fertig", "M81": "fertig"}
    assert all(i.stacked > 100 for i in p.items)
    assert d.state().parked and p.mode == "fertig"
    assert d.commands[:3] == ["takeControl", "open", "init"]
    assert "Öffne" in texts(p) and "eingeklappt" in texts(p)


def test_hindernis_wird_erkannt_und_als_vorschlag_gemerkt():
    """Erstes Scheitern sperrt nur vorläufig (kein Vorschlag); nach 20 min neuer Versuch, scheitert dieselbe Richtung
    erneut, während andere klar sind → sicher ein Hindernis, Vorschlag für die Horizontmaske."""
    p, d, clock = make([item(M31, 0.2, 1.7), item(M45, 1.7, 4.0), item(M81, 4.0, 4.6)], sim.Scenario(obstacles=[WALL]))
    run(p, clock, 1.9)
    assert not p.proposals and p.blocked and not p.blocked[0]["sure"]
    assert "neuer Versuch in 20 min" in texts(p)
    run(p, clock, 0.5)
    assert p.proposals and p.proposals[0]["target"] == "M45" and p.proposals[0]["sure"]
    assert 60 <= p.proposals[0]["az"] <= 120
    assert "also ein Hindernis" in texts(p)
    run(p, clock, 2.5)
    m45 = [i for i in p.items if i.id == "M45"]
    assert any(i.status == "fertig" for i in m45), "später, über der Wand, wird M45 noch einmal versucht"
    assert d.state().parked


def test_wolken_sind_kein_hindernis():
    """Zwei Richtungen ohne Sterne ohne Erfolg dazwischen → Wolken: einklappen, Vermutung „Hindernis“ zurücknehmen,
    nach dem Wolkenband und 20 stabilen Minuten weiter."""
    sc = sim.Scenario(clouds=[(T0 + 1.0 * H, T0 + 1.6 * H)])
    p, d, clock = make([item(M31, 0.2, 1.3), item(M15, 1.3, 3.0)], sc)
    run(p, clock, 1.4)
    assert p.mode == "wartet" and p.hold.kind == "clouds"
    assert not p.proposals and not p.blocked
    assert "Wolken" in texts(p)
    assert d.state().parked
    run(p, clock, 0.9)
    assert p.mode == "laeuft" and any(i.status == "aktiv" for i in p.items)


def test_kurzer_wolkenzug_sperrt_keine_richtung():
    """Ein paar Minuten Wolke mitten in einer guten Aufnahme → weiter aufnehmen, keine Sperre, kein Vorschlag."""
    sc = sim.Scenario(clouds=[(T0 + 1.0 * H, T0 + 1.0 * H + 3 * 60)])
    p, d, clock = make([item(M31, 0.2, 1.6)], sc)
    run(p, clock, 1.7)
    m31 = p.items[0]
    assert m31.status == "fertig" and m31.rejected > ap.WINDOW_FRAMES
    assert not p.blocked and not p.proposals
    assert "wieder brauchbare Bilder" in texts(p) and "verdeckt" not in texts(p)


def test_einmal_verworfen_ist_kein_sicheres_hindernis():
    """Lange Wolke nur über einem Ziel, andere Richtung vorher klar: vorläufig gesperrt, aber nicht „sicher“, kein Vorschlag."""
    p, d, clock = make([item(M31, 0.0, 3.0)], cfg={"fallback": False})
    run(p, clock, 0.5)
    cur = p.items[0]
    p.ok_marks.append((clock.now() - 60, 200.0, 30.0))          # gutes Bild in ganz anderer Richtung
    d.sc.clouds = [(clock.now(), clock.now() + 2 * H)]
    run(p, clock, 0.4)
    assert cur.status == "verdeckt" and p.blocked and not p.blocked[0]["sure"] and not p.proposals


def test_bei_bewoelkung_nie_sicheres_hindernis():
    p, d, clock = make([item(M45, 1.7, 3.2), item(M81, 0.2, 1.7)], sim.Scenario(obstacles=[WALL]),
                       forecast=_forecast([(-2, 14, 65)]))   # bewölkt, aber unter der Einklapp-Grenze
    run(p, clock, 2.6)
    assert not p.proposals and all(not b["sure"] for b in p.blocked)
    assert "eher Wolken" in texts(p)


def _forecast(spans):
    def f(site, t0, t1):
        return {"hours": [{"t": T0 + k * H, "cloud": next((c for a, b, c in spans if a <= k < b), 10), "wind": 8, "dew_spread": 6.0}
                          for k in range(-2, 14)]}
    return f


def test_wetter_klappt_ein_und_oeffnet_nach_stabiler_besserung():
    """Vorhersage 90 % um 18 Uhr (zwischen den Stunden linear): ab ~17:45 über 70 %, ab ~18:15 wieder darunter."""
    p, d, clock = make([item(M31, 0.1, 4.0)], forecast=_forecast([(1, 2, 90)]))
    run(p, clock, 0.6)
    assert p.mode == "laeuft"
    run(p, clock, 0.55)                             # 18:09
    assert p.mode == "wartet" and p.hold.kind == "weather"
    run(p, clock, 0.25)                             # 18:24: eingeklappt, Besserung seit ~18:15 – noch keine 20 min
    assert d.state().parked and p.mode == "wartet"
    run(p, clock, 0.6)
    assert p.mode == "laeuft" and not d.state().parked


def test_tau_grenze_je_geraet():
    hours = lambda s, a, b: {"hours": [{"t": T0 + k * H, "cloud": 5, "wind": 5, "dew_spread": 2.5} for k in range(-2, 14)]}
    p, _, clock = make([item(M31, 0.1, 2)], forecast=hours, model="vespera")
    run(p, clock, 0.3)
    assert p.mode == "laeuft"
    p, _, clock = make([item(M31, 0.1, 2)], forecast=hours, model="stellina")
    run(p, clock, 0.3)
    assert p.mode == "wartet" and p.hold.kind == "dew"


def test_morgendaemmerung_beendet_die_nacht():
    p, d, clock = make([item(M81, 0.1, 15)])           # M81 bleibt hier die ganze Nacht über 30°
    run(p, clock, 13.5, step=60)
    assert p.mode == "fertig" and d.state().parked
    assert "Morgendämmerung" in texts(p)


def test_sofort_einklappen_aus_jeder_phase():
    for after in (0.002, 0.02, 0.05, 0.5):           # öffnet · richtet aus · schwenkt · nimmt auf
        p, d, clock = make([item(M31, 0.0, 3.0)])
        run(p, clock, after, step=5)
        p.emergency(clock.now())
        run(p, clock, 0.05, step=5)
        assert d.state().parked, after
        assert p.mode == "aus" and not p.alarm
        n = len(d.commands)
        run(p, clock, 0.2)
        assert len(d.commands) == n, "nach dem Not-Aus bewegt der Autopilot nichts mehr"


def test_einklappen_nicht_bestaetigt_gibt_alarm():
    class Stuck(sim.SimDriver):
        def park(self):
            self.commands.append("park")              # Gerät reagiert nicht
    clock = sim.ManualClock(T0)
    d = Stuck(clock)
    p = ap.Autopilot(d, ap.Env(site=lambda n: SITE, plan=lambda n, m: [item(M31, 0.0, 2.0)]), clock=clock)
    d.connect(); d.take_control()
    p.start(clock.now())
    run(p, clock, 0.3)
    p.emergency(clock.now())
    run(p, clock, 0.15, step=10)
    assert p.alarm and "von Hand" in p.alarm
    assert d.commands.count("park") == ap.PARK_TRIES


def test_verbindung_weg_geraete_plan_arbeitet_weiter():
    sc = sim.Scenario(offline=[(T0 + 0.6 * H, T0 + 1.0 * H)])
    p, d, clock = make([item(M31, 0.1, 0.8), item(M81, 0.8, 2.0)], sc)
    run(p, clock, 0.5)
    assert p.state.device_plan
    run(p, clock, 0.4)                              # offline; der Plan im Gerät wechselt um 0,8 h selbst zu M81
    assert "Plan im Gerät arbeitet weiter" in texts(p)
    assert d.target and d.target.id == "M81"
    run(p, clock, 0.3)
    assert "Verbindung wieder da" in texts(p)


def test_app_uebernimmt_kontrolle_pausiert():
    p, d, clock = make([item(M31, 0.1, 2.0)], sim.Scenario(app_takes_control_at=T0 + 0.5 * H))
    run(p, clock, 0.7)
    assert p.mode == "pausiert" and "Handy-App" in texts(p)


def test_ansicht_ohne_ort():
    p, d, clock = make([item(M31, 0.1, 1.0)])
    run(p, clock, 0.3)
    v = json.dumps(p.view(clock.now()))
    assert "50.0" not in v and '"lat"' not in v and '"lon"' not in v
    assert p.view(clock.now())["items"][0]["track"]


def test_plan_aus_dem_planer():
    plan = {"devices": [{"id": "vespera"}, {"id": "stellina"}],
            "targets": [{"id": "M31", "name": "Andromeda", "ra": 10.68, "dec": 41.27}, {"id": "M81", "name": "M81", "ra": 148.9, "dec": 69.1}],
            "nights": [{"night": "2026-11-20", "blocks": [{"device": "vespera", "target": "M31", "start": T0, "end": T0 + H, "hours": 1},
                                                          {"device": "stellina", "target": "M81", "start": T0, "end": T0 + H, "hours": 1}]}]}
    got = ap.plan_items(plan, "2026-11-20", "vespera")
    assert [i.name for i in got] == ["Andromeda"]
    assert ap.plan_items(plan, "2026-11-21", "vespera") == []


def test_horizontmaske_anheben():
    h = ap.horizon_with(None, [{"az": 90, "alt": 33}])
    assert h[9] == 35 and h[8] == 35 and h[10] == 35 and h[0] == 0
    assert ap.horizon_with(h, [{"az": 90, "alt": 10}])[9] == 35        # nie absenken


# ------------------------------------------------------------------- Vaonis --- #
STATUS = {"success": True, "result": {
    "model": "vespera", "masterDeviceId": "astroflow", "initialized": True, "challenge": "1AAAA",
    "motors": {"AZ": {"position": 120.5}, "ALT": {"position": 42.0}},
    "internalBattery": {"chargeLevel": 77}, "storage": {"public": {"size": 100, "available": 25}},
    "settings": {"storageFileCategories": ["JPEG"]},
    "currentOperation": {"type": "OBSERVATION", "target": {"objectId": "M31", "objectName": "Andromeda", "ra": 10.68, "de": 41.27},
                         "steps": [{"type": "POINT_DEEP_SKY", "steps": [{"type": "GO_TARGET"}, {"type": "ASTROMETRY"}]}],
                         "capture": {"acquisitionCount": 12, "stackingCount": 10, "stackingErrorCount": 2}},
}}


def test_vaonis_status_uebersetzen():
    s = vaonis.translate(STATUS)
    assert s.master == "us" and s.step == "capture" and not s.parked and s.initialized
    assert (s.frames, s.stacked, s.rejected) == (12, 10, 2)
    assert s.target["name"] == "Andromeda" and s.battery == 77 and s.storage_free == 0.25 and s.raw_enabled is False
    # eingeklappt wie bei Probe 1 (ALT −90°, atStop); offen und still nach einem Plan (ALT ~38°, ohne atStop)
    other = vaonis.translate({**STATUS["result"], "masterDeviceId": "handy", "currentOperation": None,
                              "motors": {"AZ": {"position": 0, "atStop": True}, "ALT": {"position": -89.9999988, "atStop": True}}})
    assert other.master == "other" and other.parked and other.step == "idle"
    still = vaonis.translate({**STATUS["result"], "currentOperation": None, "motors": {"ALT": {"position": 37.8, "state": "IDLE"}}})
    assert not still.parked and still.step == "idle"
    assert not vaonis.translate(STATUS, age_s=120).connected
    failed = json.loads(json.dumps(STATUS))
    failed["result"]["currentOperation"]["error"] = {"name": "ASTROMETRY.FAILED"}
    assert vaonis.translate(failed).astrometry_failed
    # Probe 2: beim Laden meldet die Vespera chargeLevel 0 → unbekannt, kein „Akku leer“
    charging = vaonis.translate({**STATUS["result"], "internalBattery": {"chargeStatus": "CHARGING", "chargeLevel": 0}})
    assert charging.charging and charging.battery is None
    assert vaonis.translate({**STATUS["result"], "internalBattery": {"chargeStatus": "OFF", "chargeLevel": 0}}).battery == 0


# ---------------------------------------------------------------------- API --- #
def _app(tmp_path, monkeypatch, **cfg):
    data = tmp_path / "data"
    data.mkdir(exist_ok=True)
    (data / "settings.json").write_text(json.dumps({"sites": {"list": [{k: v for k, v in SITE.items() if k != "horizon"}], "default": "t1"},
                                                    "telescope": {"sim_speed": 1, "sim_start": "now", "sim_scenario": "clear", **cfg}}), encoding="utf-8")
    monkeypatch.setattr(security, "lan_addresses", lambda: ["192.0.2.10"])
    monkeypatch.setenv("ASTROFLOW_MODULES", "stacking,telescope")
    return create_app(load_settings(data))


def test_api_simulator(tmp_path, monkeypatch):
    with TestClient(_app(tmp_path, monkeypatch), base_url="http://127.0.0.1:8766", client=("127.0.0.1", 50000),
                    headers={"Origin": "http://127.0.0.1:8766"}) as c:
        st = c.get("/api/telescope/state").json()
        assert st["unit"]["simulated"] and st["mode"] == "aus"
        assert '"lat"' not in json.dumps(st) and "50.0" not in json.dumps(st["site"])
        assert c.post("/api/telescope/targets", json={"name": "M 31", "hours": 1}).json()["item"]["name"]
        assert c.post("/api/telescope/targets", json={"name": "gibtsnicht"}).status_code == 404
        assert c.post("/api/telescope/start", json={}).status_code == 200
        assert c.get("/api/telescope/state").json()["mode"] in ("laeuft", "wartet")
        assert c.post("/api/telescope/settings", json={"cloud_max": 50}).status_code == 409      # läuft → erst einklappen
        assert c.post("/api/telescope/park").status_code == 200
        st = c.get("/api/telescope/state").json()
        assert st["mode"] == "aus" and st["parking"] or st["state"]["parked"]
        assert c.get("/api/telescope/events").json()["events"]
        assert c.get("/api/status").json()["modules"]["telescope"]["mode"] == "aus"
        r = c.post("/api/telescope/settings", json={"cloud_max": 55, "sun_limit": -18}).json()
        assert r["cloud_max"] == 55 and r["sun_limit"] == -18


GOOD_PLUGIN = """
from astroflow.modules.telescope.sim import SimDriver, ManualClock
class Treiber(SimDriver):
    kind = "testgeraet"
    label = "Testgerät"
    def __init__(self, options):
        super().__init__(ManualClock(0), model="testgeraet")
        self.ip = options.get("ip") or None
"""


def _plugin(root, name="testplugin", contract=1, requires=(), code=GOOD_PLUGIN, cls="Treiber", module="driver.py"):
    d = root / name
    d.mkdir(parents=True)
    (d / "plugin.json").write_text(json.dumps({"name": name, "family": "test", "contract": contract, "module": module, "class": cls,
                                               "requires": list(requires), "options": [{"key": "ip", "label": "Adresse"}]}), encoding="utf-8")
    (d / "driver.py").write_text(code, encoding="utf-8")
    return d


def test_plugins_entdecken_und_pruefen(tmp_path):
    root = tmp_path / "data" / "plugins"
    _plugin(root, "gut")
    _plugin(root, "alt", contract=99)
    _plugin(root, "fehlt-paket", requires=["gibt_es_nicht_xyz"])
    _plugin(root, "kaputt", code="raise RuntimeError('kaputt')")
    _plugin(root, "flucht", module="../draussen.py")
    (root / "ohne-json").mkdir()
    (root / "unlesbar").mkdir()
    (root / "unlesbar" / "plugin.json").write_text("{kein json", encoding="utf-8")
    extra = _plugin(tmp_path / "anderswo", "extern")
    found = {i.name: i for i in plugins.discover(tmp_path / "data", [str(extra)], entry_points=False)}
    assert set(found) == {"gut", "alt", "fehlt-paket", "kaputt", "flucht", "unlesbar", "extern"}
    assert found["gut"].error is None and found["extern"].error is None
    assert "Vertrag 99" in found["alt"].error and "gibt_es_nicht_xyz" in found["fehlt-paket"].error
    assert "nicht im Plugin-Ordner" in found["flucht"].error and "unlesbar" in found["unlesbar"].error
    assert found["kaputt"].error is None                                    # erst beim Laden ausgeführt …
    with pytest.raises(DriverError, match="nicht laden"):
        plugins.load(found["kaputt"])                                       # … und dann mit fester Meldung
    with pytest.raises(DriverError, match="Vertrag"):
        plugins.load(found["alt"])
    d = plugins.load(found["gut"], {"ip": "192.0.2.5"})
    assert d.kind == "testgeraet" and d.ip == "192.0.2.5" and d.label == "Testgerät"
    assert found["gut"].id != found["extern"].id


def test_api_plugins_nur_am_rechner(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch)
    _plugin(tmp_path / "data" / "plugins", "testplugin")
    pc = "http://192.0.2.10:8766"
    with TestClient(app, base_url="http://127.0.0.1:8766", client=("127.0.0.1", 50000), headers={"Origin": "http://127.0.0.1:8766"}) as local:
        cfg = local.get("/api/telescope/settings").json()
        pl = cfg["plugins"][0]
        assert pl["name"] == "testplugin" and not pl["enabled"] and pl["usable"]
        drv = {d["id"]: d for d in cfg["drivers"]}
        assert drv["sim"]["usable"] and drv["vaonis-read"]["usable"] and not drv[f"plugin:{pl['id']}"]["usable"]
        assert local.post("/api/telescope/settings", json={"driver": f"plugin:{pl['id']}"}).status_code == 409     # nicht aktiviert
        link = local.get("/api/pairing").json()["links"][0]
        with TestClient(app, base_url=pc, client=("192.0.2.20", 40000), headers={"Origin": pc}) as ph:
            token = ph.get(link.replace(pc, ""), follow_redirects=False).headers["set-cookie"].split(";", 1)[0].split("=", 1)[1]
        with TestClient(app, base_url=pc, client=("192.0.2.20", 40000), headers={"Origin": pc, "Cookie": f"{security.COOKIE}={token}"}) as ph:
            assert ph.get("/api/telescope/state").status_code == 200                       # gekoppelt: ansehen und steuern
            assert ph.post("/api/telescope/plugins/enable", json={"id": pl["id"], "enable": True}).status_code == 403
            assert ph.post("/api/telescope/plugins/paths", json={"paths": ["C:/x"]}).status_code == 403
            assert ph.post("/api/telescope/settings", json={"driver": "vaonis-read"}).status_code == 403
            assert ph.post("/api/telescope/settings", json={"ip": "192.0.2.99"}).status_code == 403
            assert ph.post("/api/telescope/park").status_code == 200
        assert local.post("/api/telescope/plugins/enable", json={"id": pl["id"], "enable": True}).json()["plugins"][0]["enabled"]
        r = local.post("/api/telescope/settings", json={"driver": f"plugin:{pl['id']}", "plugin_options": {pl["id"]: {"ip": "192.0.2.7", "boese": 1}}})
        assert r.status_code == 200 and r.json()["plugin_options"][pl["id"]] == {"ip": "192.0.2.7"}
        st = local.get("/api/telescope/state").json()
        assert not st["unit"]["simulated"] and st["unit"]["driver"] == "testgeraet" and not st["unit"]["error"]
        assert local.post("/api/telescope/plugins/enable", json={"id": pl["id"], "enable": False}).json()["driver"] == "sim"
        with TestClient(app, base_url=pc, client=("192.0.2.30", 40000), headers={"Origin": pc}) as fremd:
            assert fremd.post("/api/telescope/park").status_code == 401                    # nicht gekoppelt


def test_kaputtes_plugin_legt_nichts_lahm(tmp_path, monkeypatch):
    d = _plugin(tmp_path / "data" / "plugins", "kaputt", code="raise RuntimeError('kaputt')")
    pid = plugins.discover(tmp_path / "data", entry_points=False)[0].id
    with TestClient(_app(tmp_path, monkeypatch, driver=f"plugin:{pid}", plugins_enabled=[pid]), base_url="http://127.0.0.1:8766",
                    client=("127.0.0.1", 50000), headers={"Origin": "http://127.0.0.1:8766"}) as c:
        st = c.get("/api/telescope/state").json()
        assert st["unit"]["simulated"] and "nicht laden" in st["unit"]["error"]
    assert d.exists()


def test_nur_status_ohne_plugin():
    class Http:
        def __init__(self):
            self.n = 0

        def get(self, url, timeout=None):
            self.n += 1
            assert url == "http://192.0.2.1:8082/v1/app/status"
            return type("R", (), {"raise_for_status": lambda s: None, "json": lambda s: STATUS})()
    h = Http()
    d = vaonis.VaonisReadOnly("192.0.2.1", http=h)
    d.connect()
    s = d.state()
    assert s.connected and s.model == "vespera" and s.step == "capture" and s.master == "us"
    with pytest.raises(DriverError, match="Plugin"):
        d.park()
    with pytest.raises(DriverError, match="Plugin"):
        d.observe(Target("M31", "M31", 10.68, 41.27), 10)
    assert "Signatur" not in Path(vaonis.__file__).read_text(encoding="utf-8").split('"""', 2)[2]      # keine Anmeldelogik im Repo


# ------------------------------------------------------------- Live-Bilder --- #
class FakeFtp:
    """Attrappe des anonymen FTP am Teleskop: user/<plan>/<beobachtung>/01-images-initial/img-NNNN.fits."""

    def __init__(self, tree):
        self.tree, self.cmds = tree, []

    def login(self):
        self.cmds.append("login")

    def mlsd(self, path, facts=None):
        node = self.tree
        for part in path.split("/"):
            node = node[part]
        for name, v in node.items():
            if isinstance(v, dict):
                yield name, {"type": "dir", "modify": v.get("_m", "20261120180000")}
            elif name != "_m":
                yield name, {"type": "file", "size": str(len(v))}

    def retrbinary(self, cmd, cb):
        assert cmd.startswith("RETR ")
        node = self.tree
        for part in cmd[5:].split("/"):
            node = node[part]
        cb(node)

    def quit(self):
        self.cmds.append("quit")


def test_live_bilder_per_ftp(tmp_path):
    from astroflow.modules.telescope import frames
    imgs = {"_m": "20261120183000", "img-0001.fits": b"A" * 10, "img-0002.fits": b"B" * 10, "notiz.txt": b"x", "../boese.fits": b"x"}
    tree = {"user": {"alt": {"_m": "20261119200000", "obs": {"_m": "20261119200000", "01-images-initial": {"img-0001.fits": b"alt"}}},
                     "plan": {"_m": "20261120183000", "obs2": {"_m": "20261120183000", "01-images-initial": imgs}}}}
    fake = FakeFtp(tree)
    f = frames.FtpFrames("10.0.0.1", ftp_factory=lambda: fake)
    folder, got = f.fetch(tmp_path / "M31")
    assert folder == "user/plan/obs2/01-images-initial" and got == []          # erster Blick: Größe noch nicht stabil
    imgs["img-0003.fits"] = b"C" * 5                                            # wird gerade geschrieben
    folder, got = f.fetch(tmp_path / "M31", folder)
    assert sorted(p.name for p in got) == ["img-0001.fits", "img-0002.fits"]
    imgs["img-0003.fits"] = b"C" * 10
    assert f.fetch(tmp_path / "M31", folder)[1] == []                           # Größe hat sich geändert → warten
    assert [p.name for p in f.fetch(tmp_path / "M31", folder, final=True)[1]] == ["img-0003.fits"]
    assert sorted(p.name for p in (tmp_path / "M31").iterdir()) == ["img-0001.fits", "img-0002.fits", "img-0003.fits"]
    assert not any("STOR" in c or "DELE" in c for c in fake.cmds)
    assert frames.safe_name("../M 31/..") == "M 31" and frames.safe_name("") == "Ziel"
    since = dt.datetime(2026, 11, 20, 19, 0, tzinfo=dt.timezone.utc).timestamp()
    assert f.latest_folder(fake, since) is None                                 # neue Beobachtung noch nicht angelegt


def test_aufnahmen_vom_teleskop_auflisten():
    from astroflow.modules.telescope import captures, frames
    items = captures.list_captures(frames.FtpFrames("sim", ftp_factory=sim.SimFtp.shared))
    assert items[0]["name"] == "M1 Krebsnebel" and {e["name"] for e in items} == {"M1 Krebsnebel", "M81", "M45", "M31"}     # neueste zuerst
    assert {e["key"] for e in items} == {"2026-11-12_19-58-10_plan_Demo/01-observation-m31", "2026-11-12_19-58-10_plan_Demo/02-observation-m45",
                                         "2026-11-13_22-41-05_plan_Demo/01-observation-m81", "2026-11-14_23-59-44_observation_M1_Krebsnebel"}
    assert items[0]["sub"] == "Einzelbeobachtung · 14.11.2026 23:59" and items[0]["plan"] == ""
    assert items[1]["sub"] == "Plan Demo · 13.11.2026 22:41"
    assert all(e["frames"] == 4 and e["bytes"] > 0 and e["modified"] for e in items)


def test_api_import_vom_teleskop(tmp_path, monkeypatch):
    import time as _t
    with TestClient(_app(tmp_path, monkeypatch), base_url="http://127.0.0.1:8766", client=("127.0.0.1", 50000),
                    headers={"Origin": "http://127.0.0.1:8766"}) as c:
        d = c.get("/api/telescope/captures").json()
        assert d["available"] and d["simulated"] and len(d["items"]) == 4 and not any(e["imported"] for e in d["items"])
        assert c.post("/api/telescope/captures/import", json={"keys": ["../../etc", "C:/Windows"]}).status_code == 409
        key = "2026-11-12_19-58-10_plan_Demo/01-observation-m31"
        # kleiner Import kann schon fertig sein, bevor die Antwort kommt
        assert c.post("/api/telescope/captures/import", json={"keys": [key, "../../etc"]}).json()["state"] in ("running", "done")
        for _ in range(100):
            j = c.get("/api/telescope/captures").json()["job"]
            if j["state"] != "running":
                break
            _t.sleep(0.05)
        assert j["state"] == "done" and j["files"] == 4
        out = tmp_path / "data" / "input" / "Teleskop" / "vespera" / "2026-11-12_19-58-10_plan_Demo" / "01-observation-m31"
        assert sorted(p.name for p in out.iterdir()) == [f"img-{k:04d}.fits" for k in range(4)]
        assert not list((tmp_path / "data" / "input").rglob("*.part"))
        d = c.get("/api/telescope/captures").json()
        assert next(e for e in d["items"] if e["key"] == key)["imported"]
        c.post("/api/telescope/captures/import", json={"keys": [key]})                  # noch einmal: nichts Neues
        for _ in range(100):
            j = c.get("/api/telescope/captures").json()["job"]
            if j["state"] != "running":
                break
            _t.sleep(0.05)
        assert j["files"] == 0
        assert any(a["kind"] == "Import" for a in c.get("/api/activity").json())
        single = "2026-11-14_23-59-44_observation_M1_Krebsnebel"            # Einzelbeobachtung: ohne leere Plan-Ebene
        c.post("/api/telescope/captures/import", json={"keys": [single]})
        for _ in range(100):
            j = c.get("/api/telescope/captures").json()["job"]
            if j["state"] != "running":
                break
            _t.sleep(0.05)
        assert j["state"] == "done" and j["files"] == 4
        out = tmp_path / "data" / "input" / "Teleskop" / "vespera" / single
        assert sorted(p.name for p in out.iterdir()) == [f"img-{k:04d}.fits" for k in range(4)]


def test_ordnernamen_lesbar():
    from astroflow.modules.telescope.captures import pretty
    assert pretty("2026-11-13_17-21-02_plan_Astroflow", "03-observation-m77") == ("M77", "Plan Astroflow · 13.11.2026 17:21")
    assert pretty("2026-11-13_17-21-02_plan_Mein_Plan", "01-observation-ngc2775")[0] == "NGC 2775"
    assert pretty("2026-11-13_17-21-02_plan_X", "02-observation-ic434")[0] == "IC 434"
    assert pretty("irgendwas", "anders") == ("anders", "irgendwas")                 # unbekanntes Schema: unverändert
    assert pretty("", "2026-11-14_23-59-44_observation_M1_Krebsnebel") == ("M1 Krebsnebel", "Einzelbeobachtung · 14.11.2026 23:59")
    assert pretty("", "2026-11-14_18-52-27_observation_C_51_IC_1613_")[0] == "C 51 IC 1613"
    assert pretty("", "2026-11-15_00-22-12_observation_M35")[0] == "M35"
    assert pretty("", "seltsam") == ("seltsam", "Einzelbeobachtung")


def test_plugin_bringt_eigene_pakete_mit(tmp_path, monkeypatch):
    """Pakete im Plugin-Ordner (site-packages/vendor) werden erst beim Laden und nur hinten in den Suchpfad genommen."""
    import sys
    monkeypatch.setattr(sys, "path", list(sys.path))
    code = GOOD_PLUGIN.replace("from astroflow", "import eigenes_paket_xyz\nfrom astroflow", 1) + "        self.wert = eigenes_paket_xyz.WERT\n"
    d = _plugin(tmp_path / "data" / "plugins", "mitpaket", requires=["eigenes_paket_xyz"], code=code)
    info = plugins.discover(tmp_path / "data", entry_points=False)[0]
    assert info.error and "eigenes_paket_xyz" in info.error and "site-packages" in info.error         # fehlt → klare Meldung
    (d / "site-packages" / "eigenes_paket_xyz").mkdir(parents=True)
    (d / "site-packages" / "eigenes_paket_xyz" / "__init__.py").write_text("WERT = 42\n", encoding="utf-8")
    info = plugins.discover(tmp_path / "data", entry_points=False)[0]
    assert info.error is None and str((d / "site-packages").resolve()) not in sys.path               # noch nicht im Suchpfad
    drv = plugins.load(info)
    assert drv.wert == 42 and sys.path[-1] == str((d / "site-packages").resolve())                   # hinten angehängt


def test_plugin_falsche_version_klare_meldung(tmp_path):
    _plugin(tmp_path / "data" / "plugins", "alt", contract=0)
    info = plugins.discover(tmp_path / "data", entry_points=False)[0]
    assert info.error.startswith("Plugin passt nicht zu dieser AstroFlow-Version")


def test_private_ordner_der_plugins(tmp_path):
    d = _plugin(tmp_path / "data" / "plugins", "mitbib")
    meta = json.loads((d / "plugin.json").read_text(encoding="utf-8"))
    (d / "plugin.json").write_text(json.dumps({**meta, "private": [".."]}), encoding="utf-8")
    dirs = plugins.private_dirs(tmp_path / "data", [], {"x": {"library": str(tmp_path / "bib" / "vaonis.py")}})
    assert str((tmp_path / "data" / "plugins").resolve()) in dirs and str(tmp_path / "bib") in dirs


def test_entry_point_plugin_erst_beim_laden_ausgefuehrt(monkeypatch):
    """Installierte Plugins (Entry Point) werden beim Entdecken nicht importiert – erst `load` (nach Aktivierung) führt Code aus."""
    import importlib.metadata as md
    calls = []

    class Treiber(sim.SimDriver):
        plugin = {"name": "Paket-Treiber", "contract": 1}

        def __init__(self, options):
            super().__init__(sim.ManualClock(0))

    class EP:
        name, value = "paket", "paket.modul:Treiber"

        def load(self):
            calls.append("load")
            return Treiber
    monkeypatch.setattr(md, "entry_points", lambda group=None: [EP()] if group == plugins.GROUP else [])
    infos = plugins._entry_points()
    assert calls == [] and infos[0].name == "paket" and infos[0].error is None
    d = plugins.load(infos[0])
    assert calls == ["load"] and isinstance(d, Treiber)
    Treiber.plugin = {"contract": 99}
    with pytest.raises(DriverError, match="passt nicht"):
        plugins.load(plugins._entry_points()[0])


def test_live_bilder_einzelbeobachtung_ohne_plan(tmp_path):
    """Echtbetrieb 15.11.2026: Einzelbeobachtungen (so startet der Autopilot jedes Ziel) liegen ohne Plan-Ebene unter `user/`.
    Vorher fand `latest_folder` sie nicht – keine Live-Bilder, „0 Bilder gestapelt“."""
    from astroflow.modules.telescope import captures, frames
    plan_imgs = {"_m": "20261113220000", "img-0001.fits": b"P" * 8}
    single_imgs = {"_m": "20261114230000", "img-0001.fits": b"A" * 8, "img-0002.fits": b"B" * 8}
    tree = {"user": {"2026-11-13_21-48-31_plan_Astroflow": {"_m": "20261113220000",
                                                            "01-observation-m77": {"_m": "20261113220000", "01-images-initial": plan_imgs}},
                     "2026-11-14_23-59-44_observation_M1_Krebsnebel": {"_m": "20261114230000", "01-images-initial": single_imgs},
                     "2026-11-14_18-52-27_observation_C_51_IC_1613_": {"_m": "20261114185300", "01-images-initial": {"_m": "20261114185300"}}}}
    fake = FakeFtp(tree)
    f = frames.FtpFrames("x", ftp_factory=lambda: fake)
    assert f.latest_folder(fake) == "user/2026-11-14_23-59-44_observation_M1_Krebsnebel/01-images-initial"
    since = dt.datetime(2026, 11, 14, 0, 0, tzinfo=dt.timezone.utc).timestamp()
    assert f.latest_folder(fake, since).startswith("user/2026-11-14_23-59-44_observation_M1")
    folder, got = f.fetch(lambda fo: tmp_path / fo.split("/")[-2], final=True)
    assert sorted(p.name for p in got) == ["img-0001.fits", "img-0002.fits"]
    assert (tmp_path / "2026-11-14_23-59-44_observation_M1_Krebsnebel" / "img-0002.fits").read_bytes() == b"B" * 8
    items = captures.list_captures(f)                                            # leere Beobachtung (C 51) fehlt, beide Formen da
    assert [(e["key"], e["frames"]) for e in items] == [("2026-11-14_23-59-44_observation_M1_Krebsnebel", 2),
                                                       ("2026-11-13_21-48-31_plan_Astroflow/01-observation-m77", 1)]
    assert not any("STOR" in c or "DELE" in c for c in fake.cmds)


def test_live_bilder_lange_beobachtung_und_eigener_ordner(tmp_path):
    from astroflow.modules.telescope import frames
    imgs = {"_m": "20261120183000", **{f"img-{k:04d}.fits": b"x" * 4 for k in range(450)}}       # > 400 Einträge
    fake = FakeFtp({"user": {"p": {"_m": "20261120183000", "02-observation-m31": {"_m": "20261120183000", "01-images-initial": imgs}}}})
    f = frames.FtpFrames("10.0.0.1", ftp_factory=lambda: fake)
    folder, got = f.fetch(lambda fo: tmp_path / fo.split("/")[-2], final=True)
    assert len(got) == 450 and (tmp_path / "02-observation-m31" / "img-0449.fits").exists()


def test_capture_liste_ohne_400er_grenze():
    from astroflow.modules.telescope import captures, frames
    imgs = {"_m": "20261120183000", **{f"img-{k:04d}.fits": b"x" for k in range(686)}}
    fake = FakeFtp({"user": {"p": {"_m": "20261120183000", "01-observation-m77": {"_m": "20261120183000", "01-images-initial": imgs}}}})
    assert captures.list_captures(frames.FtpFrames("x", ftp_factory=lambda: fake))[0]["frames"] == 686


def test_waechter_auch_in_der_pause():
    """Pause stoppt nur neue Ziele – bei Morgendämmerung (oder Wetter, Tau, Akku) klappt das offene Teleskop trotzdem ein."""
    p, d, clock = make([item(M31, 0.1, 15)])
    run(p, clock, 1.0)
    p.pause(clock.now())
    assert p.mode == "pausiert" and not d.state().parked
    run(p, clock, 13.0, step=60)
    assert d.state().parked and p.mode == "pausiert"
    assert "trotz Pause" in texts(p)
    assert d.commands.count("observe:M31") + sum(c.startswith("plan:") for c in d.commands) == 1      # nichts neu gestartet


def test_laufendes_ziel_verlaesst_freien_bereich():
    """Ein Ziel wird in jedem Takt geprüft: sinkt es unter die Mindesthöhe, endet es sauber, und es geht weiter (hier: einklappen)."""
    p, d, clock = make([item(M15, 0.1, 6.0)])           # M15 sinkt am Abend im Südwesten unter 30°
    run(p, clock, 4.0)
    m15 = [i for i in p.items if i.id == "M15"]
    assert m15[0].status in ("fertig", "uebersprungen") and "zu tief" in m15[0].why
    assert "verlässt den freien Bereich" in texts(p)
    assert not any(i.status == "aktiv" for i in p.items) and d.state().step != "capture"


def test_fremder_geraeteplan_wird_nicht_abgearbeitet():
    plan = {"devices": [{"id": "vespera"}, {"id": "seestar"}],
            "targets": [{"id": "M31", "name": "M31", "ra": 10.68, "dec": 41.27}],
            "nights": [{"night": "2026-11-20", "blocks": [{"device": "vespera", "target": "M31", "start": T0, "end": T0 + H},
                                                          {"device": "seestar", "target": "M31", "start": T0, "end": T0 + H}]}]}
    got = ap.plan_items(plan, "2026-11-20", "stellina")
    assert got == [] and "anderen Gerät" in got.note
    assert len(ap.plan_items(plan, "2026-11-20", "vespera")) == 1 and ap.plan_items(plan, "2026-11-20", "vespera").note is None
    clock = sim.ManualClock(T0)
    p = ap.Autopilot(sim.SimDriver(clock), ap.Env(site=lambda n: SITE, plan=lambda n, m: ap.plan_items(plan, "2026-11-20", "stellina")), clock=clock)
    p.load_night(T0)
    assert p.items == [] and "anderen Gerät" in p.view(T0)["plan_note"]


class _FakeVaonisDriver(sim.SimDriver):
    """Simulator, der sich wie ein echtes Gerät verhält (Bestätigung nötig)."""
    kind = "plugin"


def test_bestaetigung_gilt_nur_fuer_eine_nacht(tmp_path, monkeypatch):
    """Läuft der Server über mehrere Nächte, verlangt „Start“ am nächsten Abend erneut die Bestätigung."""
    app = _app(tmp_path, monkeypatch)
    with TestClient(app, base_url="http://127.0.0.1:8766", client=("127.0.0.1", 50000), headers={"Origin": "http://127.0.0.1:8766"}) as c:
        u = app.state.modules["telescope"].unit
        clock = sim.ManualClock(T0)
        u.clock, u.driver = clock, _FakeVaonisDriver(clock)
        u.pilot = ap.Autopilot(u.driver, u.pilot.env, u.pilot.cfg, clock=clock)
        u.pilot.requires_confirm = True                                                         # wie bei einem echten Gerät
        assert c.post("/api/telescope/start", json={}).status_code == 409                    # erste Nacht: Bestätigung nötig
        assert c.post("/api/telescope/start", json={"confirm": True}).status_code == 200
        first = u.pilot.night
        c.post("/api/telescope/park")
        clock.advance(24 * H)                                                                  # nächster Abend, Server läuft weiter
        assert c.post("/api/telescope/start", json={}).status_code == 409                    # neue Nacht → erneut bestätigen
        assert u.pilot.night != first


def test_live_bilder_neuer_versuch_neuer_ordner(tmp_path):
    from astroflow.modules.telescope import frames

    class U:
        cfg = {"model": "vespera"}
        pilot = type("P", (), {"night": "2026-11-20", "log": lambda *a, **k: None, "frames_fetched": 0})()
    col = frames.Collector(U(), tmp_path, "10.0.0.1")
    tree = {"user": {"p": {"_m": "20261120180000", "01-observation-m31": {"_m": "20261120180000", "01-images-initial": {"img-0000.fits": b"a"}}}}}
    col.ftp = frames.FtpFrames("x", ftp_factory=lambda: FakeFtp(tree))
    item_ = item(M31, 0.0, 3.0)
    item_.real_start = dt.datetime(2026, 11, 20, 18, 0, tzinfo=dt.timezone.utc).timestamp()
    col._take(item_, final=False)
    col._take(item_, final=True)
    tree["user"]["p"]["02-observation-m31"] = {"_m": "20261120200000", "01-images-initial": {"img-0000.fits": b"b"}}
    item_.real_start = dt.datetime(2026, 11, 20, 20, 0, tzinfo=dt.timezone.utc).timestamp()   # Weiter nach Pause: neuer Versuch
    col._take(item_, final=False)
    col._take(item_, final=True)
    base = tmp_path / "Teleskop" / "vespera" / "2026-11-20" / "M31"
    assert (base / "01-observation-m31" / "img-0000.fits").read_bytes() == b"a"
    assert (base / "02-observation-m31" / "img-0000.fits").read_bytes() == b"b"


def test_not_aus_ohne_verbindung_kein_fehlalarm():
    """Nie verbunden bzw. zuletzt eingeklappt: kein Alarm. Zuletzt offen und Verbindung weg: sofort deutliche Warnung."""
    clock = sim.ManualClock(T0)
    d = sim.SimDriver(clock)
    p = ap.Autopilot(d, ap.Env(site=lambda n: SITE, plan=lambda n, m: []), clock=clock)
    p.emergency(clock.now())
    run(p, clock, 0.3, step=10)
    assert p.alarm is None and not p.park_pending
    p2, d2, c2 = make([item(M31, 0.1, 3.0)], sim.Scenario(offline=[(T0 + 0.5 * H, T0 + 2 * H)]))
    run(p2, c2, 0.6)                                    # offen, dann Verbindung weg
    p2.emergency(c2.now())
    assert p2.alarm and "Keine Verbindung" in p2.alarm


def test_kontrolle_nicht_zurueckholen_nach_pause_oder_trennen():
    """Pause bzw. Trennen: AstroFlow verbindet sich nicht selbst neu und nimmt der App die Kontrolle nicht weg."""
    p, d, clock = make([item(M31, 0.1, 3.0)])
    run(p, clock, 0.5)
    p.pause(clock.now())
    d.disconnect()
    n = d.commands.count("takeControl")
    run(p, clock, 0.5)
    assert d.commands.count("takeControl") == n and not d.connected
    p.mode = "fertig"
    run(p, clock, 0.5)
    assert d.commands.count("takeControl") == n


def test_restbilder_nach_pause_werden_abgeholt(tmp_path):
    """Pause beendet das Ziel als „geplant“ – die zuletzt geschriebenen Bilder dieses Versuchs kommen trotzdem."""
    from astroflow.modules.telescope import frames
    imgs = {"_m": "20261120180500", "img-0000.fits": b"a"}
    tree = {"user": {"p": {"_m": "20261120180500", "01-observation-m31": {"_m": "20261120180500", "01-images-initial": imgs}}}}
    it = item(M31, 0.0, 3.0)
    it.status, it.real_start = "aktiv", T0 + 3600
    pilot = type("P", (), {"night": "2026-11-20", "log": lambda *a, **k: None, "frames_fetched": 0,
                           "state": TelescopeState(step="capture"), "_current": lambda self: it if it.status == "aktiv" else None})()

    class U:
        cfg = {"model": "vespera"}
    U.pilot = pilot
    col = frames.Collector(U(), tmp_path, "10.0.0.1")
    col.ftp = frames.FtpFrames("x", ftp_factory=lambda: FakeFtp(tree))
    col.step()
    col.step()                                         # img-0000 stabil → geholt
    imgs["img-0001.fits"] = b"bb"                      # gerade geschrieben, dann Pause
    it.status, it.real_start = "geplant", None
    col.step()
    out = tmp_path / "Teleskop" / "vespera" / "2026-11-20" / "M31" / "01-observation-m31"
    assert sorted(x.name for x in out.iterdir()) == ["img-0000.fits", "img-0001.fits"]


def test_ohne_bestaetigung_kein_bewegungsbefehl():
    """Echtes Gerät: ohne Bestätigung für die *aktuelle* Nacht kommt kein Öffnen/Ausrichten/GoTo beim Treiber an – weder beim
    ersten Start noch nach einem Nachtwechsel (Takt, Wiederöffnen, Weiter). Einklappen bleibt immer möglich."""
    p, d, clock = make([item(M31, 0.1, 30)], sim.Scenario(battery_per_h=0))       # M31 steht an beiden Abenden hoch
    p.requires_confirm = True
    run(p, clock, 0.2)
    assert not [c for c in d.commands if c in ("open", "init") or c.startswith(("observe", "plan"))]
    assert p.mode == "pausiert" and "Bestätigung" in texts(p)
    p.confirm(clock.now()); p.resume(clock.now())
    run(p, clock, 0.5)
    assert "open" in d.commands and any(c.startswith(("observe", "plan")) for c in d.commands)
    p.pause(clock.now())
    run(p, clock, 13, step=60)                          # Dämmerung, Tag: eingeklappt (Wächter), Pause bleibt
    n = len(d.commands)
    p.resume(clock.now())                               # nächster Abend: „Weiter“ ohne neue Bestätigung
    run(p, clock, 12, step=60)
    moves = [c for c in d.commands[n:] if c in ("open", "init") or c.startswith(("observe", "plan"))]
    assert moves == [] and p.mode == "pausiert"
    p.emergency(clock.now())
    assert "park" in d.commands[n:] or d.state().parked


def test_api_verbinden_bewegt_nie_und_trennen_nur_ohne_autopilot(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch)
    with TestClient(app, base_url="http://127.0.0.1:8766", client=("127.0.0.1", 50000), headers={"Origin": "http://127.0.0.1:8766"}) as c:
        u = app.state.modules["telescope"].unit
        assert c.get("/api/telescope/state").json()["state"]["connected"] is False
        r = c.post("/api/telescope/connect")
        assert r.status_code == 200 and r.json()["master"] == "us"
        st = c.get("/api/telescope/state").json()
        assert st["state"]["connected"] and st["mode"] == "aus"
        assert not [x for x in u.driver.commands if x in ("open", "init", "park") or x.startswith(("observe", "plan", "focus"))]
        assert c.post("/api/telescope/disconnect").status_code == 200                       # ohne Autopilot ok
        assert c.get("/api/telescope/state").json()["state"]["connected"] is False
        assert c.post("/api/telescope/start", json={}).status_code == 200
        r = c.post("/api/telescope/disconnect")
        assert r.status_code == 409 and "pausieren" in r.json()["detail"]                    # Autopilot läuft


def test_api_verbinden_nimmt_der_handy_app_die_kontrolle_nicht(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch)
    with TestClient(app, base_url="http://127.0.0.1:8766", client=("127.0.0.1", 50000), headers={"Origin": "http://127.0.0.1:8766"}) as c:
        u = app.state.modules["telescope"].unit
        u.driver.master = "other"
        u.driver.connected = False
        r = c.post("/api/telescope/connect")
        assert r.status_code == 200 and r.json()["master"] == "other" and "Handy-App" in r.json()["note"]
        assert "takeControl" not in u.driver.commands and u.driver.master == "other"


def test_api_vorschau_simulator_und_platzhalter(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch)
    with TestClient(app, base_url="http://127.0.0.1:8766", client=("127.0.0.1", 50000), headers={"Origin": "http://127.0.0.1:8766"}) as c:
        u = app.state.modules["telescope"].unit
        assert c.post("/api/telescope/connect").status_code == 200
        assert c.get("/api/telescope/preview/info").json() == {"available": False}           # noch keine Bilder → Platzhalter
        assert c.get("/api/telescope/preview").status_code == 404
        u.driver.frames, u._pv = 5, None
        u.pilot.tick(u.now())                                                                # der Takt liest die Bildzahl
        i = c.get("/api/telescope/preview/info").json()
        assert i["available"] and i["n"] == 5 and i["source"] == "device"
        r = c.get("/api/telescope/preview")
        assert r.status_code == 200 and r.content[:2] == bytes([255, 216]) and r.headers["etag"]
        assert c.get("/api/telescope/preview", headers={"If-None-Match": r.headers["etag"]}).status_code == 304


def test_vorschau_rueckfall_aus_letzter_fits(tmp_path):
    import types
    import numpy as np
    from astropy.io import fits
    from astroflow.modules.telescope import preview as pv

    class OhneVorschau(sim.SimDriver):
        capabilities = frozenset()

    p, d, clock = make([])
    unit = types.SimpleNamespace(driver=OhneVorschau(clock), pilot=p, collector=None)
    assert pv.get(unit) is None                                                          # kein Sammler → nichts
    f = tmp_path / "img-0001.fits"
    fits.writeto(f, (np.random.default_rng(1).random((400, 600)) * 4000).astype(np.uint16))
    unit.collector, unit._pv = types.SimpleNamespace(last=f, count=3), None
    r = pv.get(unit)
    assert r and r["source"] == "fits" and r["n"] == 3 and r["jpeg"][:2] == bytes([255, 216])
    unit.collector.last, unit._pv = tmp_path / "weg.fits", None
    assert pv.get(unit) is None                                                          # Datei verschwunden → Platzhalter


def test_api_weiter_verlangt_bestaetigung_der_neuen_nacht(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch)
    with TestClient(app, base_url="http://127.0.0.1:8766", client=("127.0.0.1", 50000), headers={"Origin": "http://127.0.0.1:8766"}) as c:
        u = app.state.modules["telescope"].unit
        clock = sim.ManualClock(T0)
        u.clock, u.driver = clock, _FakeVaonisDriver(clock)
        u.pilot = ap.Autopilot(u.driver, u.pilot.env, u.pilot.cfg, clock=clock)
        u.pilot.requires_confirm = True
        assert c.post("/api/telescope/start", json={"confirm": True}).status_code == 200
        c.post("/api/telescope/pause")
        clock.advance(24 * H)
        r = c.post("/api/telescope/resume", json={})
        assert r.status_code == 409 and "bestätigen" in r.json()["detail"]
        assert c.post("/api/telescope/resume", json={"confirm": True}).status_code == 200


def test_restbilder_nach_ftp_abbruch_werden_nachgeholt(tmp_path):
    """Bricht der Abschlussabruf nach dem Ende eines Versuchs ab (FTP kurz weg), bleibt der Versuch vorgemerkt und die
    Restbilder kommen beim nächsten Durchgang – auch wenn inzwischen ein anderes Ziel läuft."""
    from astroflow.modules.telescope import frames
    imgs = {"_m": "20261120180500", "img-0000.fits": b"a"}
    tree = {"user": {"p": {"_m": "20261120180500", "01-observation-m31": {"_m": "20261120180500", "01-images-initial": imgs}}}}
    it = item(M31, 0.0, 3.0)
    it.status, it.real_start = "aktiv", T0 + 3600
    logs = []
    pilot = type("P", (), {"night": "2026-11-20", "log": lambda self, *a, **k: logs.append(a), "frames_fetched": 0,
                           "state": TelescopeState(step="capture"), "_current": lambda self: it if it.status == "aktiv" else None})()

    class U:
        cfg = {"model": "vespera"}
    U.pilot = pilot
    col = frames.Collector(U(), tmp_path, "10.0.0.1")
    down = {"on": False}

    def factory():
        if down["on"]:
            raise OSError("FTP weg")
        return FakeFtp(tree)
    col.ftp = frames.FtpFrames("x", ftp_factory=factory)
    col.step()
    col.step()                                         # img-0000 geholt
    imgs["img-0001.fits"] = b"bb"
    it.status, it.real_start = "geplant", None         # Pause, genau dann bricht FTP ab
    down["on"] = True
    col.step()
    col.step()
    assert col.error and len(col.pending) == 1 and [a[1] for a in logs].count("warn") == 1     # einmal gewarnt, Versuch bleibt vorgemerkt
    down["on"] = False
    col.step()
    out = tmp_path / "Teleskop" / "vespera" / "2026-11-20" / "M31" / "01-observation-m31"
    assert sorted(x.name for x in out.iterdir()) == ["img-0000.fits", "img-0001.fits"]
    assert col.pending == [] and col.error is None


# ------------------------------------------------------------- Akku-Restzeit --- #
def test_akku_restzeit_schaetzung():
    from astroflow.modules.telescope import battery as bt
    b = bt.BatteryEta()
    assert b.view(None, False, "vespera", True, 15) is None                         # ohne Akkuwert nichts
    b.update(T0, 80, False)
    v = b.view(80, False, "vespera", True, 15)
    assert not v["measured"] and v["rate"] == bt.DRAIN_PCT_H["vespera"]["aktiv"]     # vor der Messung: Erfahrungswert
    assert b.rate("vespera", False) < b.rate("vespera", True)                        # eingeklappt hält länger
    for k in range(1, 31):                                                          # 30 min, 20 %/h
        b.update(T0 + k * 60, round(80 - 20 * k / 60), False)
    v = b.view(70, False, "vespera", True, 15)
    assert v["measured"] and 17 <= v["rate"] <= 23
    assert abs(v["empty_s"] - 70 / v["rate"] * 3600) < 60 and v["limit_s"] < v["empty_s"]
    b.update(T0 + 3600, 72, True)
    assert b.view(72, True, "vespera", True, 15) == {"charging": True} and b.seconds_left(72, "vespera", True) is None
    assert bt.fmt_left(2 * 3600 + 40 * 60) == "2 h 40 min" and bt.fmt_left(35 * 60) == "35 min"


def test_akku_reicht_nicht_ziel_wird_gekuerzt():
    """Gemessene Entladung 30 %/h ab 50 %: bis zur Grenze 15 % bleiben ~70 min – das 3-h-Ziel wird gekürzt; die Grenze
    battery_min klappt wie bisher ein."""
    p, d, clock = make([item(M81, 0.0, 0.5), item(M31, 0.5, 3.5)], sim.Scenario(battery=50, battery_per_h=30))
    run(p, clock, 0.6, step=20)
    assert p.view(clock.now())["conditions"]["battery_left"]["measured"]
    t = texts(p)
    assert "Akku reicht nur noch ca." in t and "gekürzt" in t
    m31 = next(i for i in p.items if i.id == M31[0])
    assert m31.end < T0 + 3.0 * H
    run(p, clock, 1.5, step=60)
    assert d.state().parked and "Akku" in texts(p)


def test_geraet_beschaeftigt_beim_einrichten_kein_fehler_und_backoff():
    """Nach dem Öffnen richtet sich das Gerät noch selbst ein und lehnt Ausrichten als „beschäftigt“ ab: kein roter Fehler,
    genau ein Eintrag, neue Versuche mit wachsendem Abstand statt jeden Takt – danach läuft alles normal weiter."""
    p, d, clock = make([item(M31, 0.1, 3.0)], sim.Scenario(setup_busy_s=4 * 60))
    run(p, clock, 0.5, step=2.5)
    assert d.commands.count("init-busy") <= 6                       # 10, 20, 40, 80 s … statt ~100 Versuche
    assert "init" in d.commands and p.state.initialized
    bad = [e for e in p.events if e["kind"] == "bad"]
    assert not bad, bad
    assert [e["text"] for e in p.events].count("Teleskop ist noch beschäftigt – Ausrichten folgt, sobald es fertig ist.") == 1
    assert any(c.startswith(("observe", "plan")) for c in d.commands)


def test_ausrichten_abgelehnt_nach_zeitgrenze_einklappen_und_anhalten():
    """Lehnt das Gerät Ausrichten dauerhaft ab (nicht beschäftigt), erst nach der Zeitgrenze ein echter Fehler: einklappen,
    anhalten – vorher nur ein Hinweis, keine Schleife im Protokoll."""
    p, d, clock = make([item(M31, 0.1, 3.0)], sim.Scenario(init_rejected=True))
    run(p, clock, 0.2, step=2.5)
    assert p.mode == "laeuft" and not [e for e in p.events if e["kind"] == "bad"]
    assert "neuer Versuch in" in p.view(clock.now())["phase_label"]
    run(p, clock, 0.3, step=2.5)
    assert p.mode == "pausiert" and "park" in d.commands
    assert len([e for e in p.events if e["kind"] == "bad"]) == 1
    assert d.commands.count("init-rejected") <= 15
    run(p, clock, 0.3, step=2.5)
    assert d.state().parked and p.mode == "pausiert"


def test_restdauer_beim_einrichten():
    p, d, clock = make([item(M31, 0.1, 3.0)])
    for _ in range(200):
        p.tick(clock.now())
        if p.machine.phase == "richtet_aus":
            break
        clock.advance(2)
    v = p.view(clock.now())
    assert v["phase"] == "richtet_aus" and v["eta"] and "noch ca." in v["phase_label"]
    run(p, clock, 0.1, step=2)
    assert "richtet_aus" in p.took and abs(p.took["richtet_aus"] - sim.INIT_S) < 10   # gemessene Dauer ersetzt den Erfahrungswert


def test_zeitgrenze_keine_bewegung_im_selben_takt_und_geraeteplan_nur_rueckfall():
    """Codex #126: Nach der Zeitgrenze (einklappen, anhalten) darf im selben Takt kein GoTo folgen; ein abgelehnter
    Geräte-Plan ist nur ein Rückfall und löst keine Zeitgrenze aus."""
    p, d, clock = make([item(M31, 0.1, 3.0)], cfg={"fallback": False})
    p.retry["observe"] = {"first": clock.now() - 3600, "next": 0, "n": 3, "msg": "x"}
    calls = []
    real = d.observe

    def no(*a, **k):
        calls.append("observe")
        raise ap.DriverError("abgelehnt")
    d.observe = no
    run(p, clock, 0.5)
    assert p.mode == "pausiert" and "park" in d.commands
    n = len(calls)
    d.observe = real
    p.mode = "laeuft"                                   # selbst wenn der Modus noch „läuft“: Einklappen steht an → keine Bewegung
    p.park_pending = {"t": clock.now(), "tries": 0}
    assert p._cmd("observe", d.observe, p.items[0].target, 10.0, now=clock.now()) is False
    p.mode, p.park_pending = "pausiert", None
    run(p, clock, 0.2)
    assert len(calls) == n and not any(c.startswith("observe") for c in d.commands)
    assert "device_plan" not in ap.GIVE_UP


def test_stoppen_ohne_backoff():
    p, d, clock = make([item(M31, 0.1, 3.0)])
    run(p, clock, 0.5)
    real, tries = d.stop, []

    def once(*a):
        tries.append(1)
        if len(tries) == 1:
            raise ap.DriverError("abgelehnt")
        return real()
    d.stop = once
    p.pause(clock.now()); p.resume(clock.now()); run(p, clock, 0.05)
    p.pause(clock.now())
    assert len(tries) >= 2 and "stop" not in p.retry


def test_keine_bewegung_waehrend_einklappen_ansteht():
    """Steht Einklappen an (Zeitgrenze, Wächter), darf im selben Takt kein GoTo mehr raus – auch wenn die Phase noch „bereit“ ist."""
    p, d, clock = make([item(M31, 0.1, 3.0)])
    run(p, clock, 0.5)
    assert p.machine.can("observe")
    p.park_pending = {"t": clock.now(), "tries": 0}
    n = len(d.commands)
    assert p._cmd("observe", d.observe, p.items[0].target, 10.0, now=clock.now()) is False and len(d.commands) == n


def test_vaonis_status_abgebrochene_beobachtung():
    """Erster Autopilot-Lauf: Gerät brach nach 6× ASTROMETRY_FAILED selbst ab, danach keine Operation – der Grund steht nur in
    previousOperations. Frisch → astrometry_failed; alt (über FRESH_S) → kein Fehler."""
    from astroflow.modules.telescope import vaonis_read as vr
    end = 1794678479308
    raw = {"result": {"initialized": True, "timestamp": end + 170_000, "currentOperation": None, "otherCurrentOperations": [],
                      "motors": {"AZ": {"position": 130.6}, "ALT": {"position": 48.4}},
                      "previousOperations": {"observation": {
                          "type": "OBSERVATION", "startTime": end - 56_000, "endTime": end, "stopped": True,
                          "error": {"name": "GENERAL.POINTING_STOPPED", "chain": [{"name": "GENERAL.TOO_MANY_ASTROMETRY_FAILED"}]}}}}}
    s = vr.translate(raw)
    assert s.step == "idle" and s.astrometry_failed and "TOO_MANY_ASTROMETRY_FAILED" in s.error
    raw["result"]["timestamp"] = end + (vr.FRESH_S + 60) * 1000
    s = vr.translate(raw)
    assert not s.astrometry_failed and s.error is None


def test_geraet_beendet_beobachtung_still_einmal_neu_dann_weiter():
    """Ziel aktiv, Gerät tut nichts mehr (ohne Fehler): nach STALL_S einmal neu starten; passiert es wieder, Ziel abbrechen
    und weiter – nie endlos warten."""
    p, d, clock = make([item(M31, 0.0, 3.0)], sim.Scenario(stop_silently_at=T0 + 0.15 * H), cfg={"fallback": False})
    run(p, clock, 0.3, step=10)
    assert "starte sie einmal neu" in texts(p) and sum(c.startswith("observe") for c in d.commands) >= 2
    m31 = p.items[0]
    assert m31.status == "aktiv" and m31.restarts == 1
    d.sc.stop_silently_at = clock.now()                    # zweites Mal
    run(p, clock, 0.2, step=10)
    # mit gestapelten Bildern „fertig“, sonst „abgebrochen“ – in beiden Fällen weiter statt warten
    assert (m31.status, "selbst beendet" in texts(p)) == ("fertig", True) if m31.stacked else \
        (m31.status, "überspringe das Ziel" in texts(p)) == ("abgebrochen", True)
    assert sum(c.startswith("observe") for c in d.commands) == 2            # nur einmal neu gestartet


def test_ziel_ohne_bilder_nach_stillstand_abgebrochen():
    p, d, clock = make([item(M31, 0.0, 3.0)], cfg={"fallback": False})
    run(p, clock, 0.3, step=10)
    m31 = p.items[0]
    m31.restarts, m31.stacked = 1, 0                       # schon einmal neu gestartet, keine Bilder
    d.step, d.target = "idle", None                        # Gerät hört still auf
    d.sc.stop_silently_at = clock.now()
    run(p, clock, 0.1, step=10)
    assert m31.status == "abgebrochen" and "überspringe das Ziel" in texts(p)


def test_alter_astrometriefehler_trifft_neues_ziel_nicht():
    p, d, clock = make([item(M31, 0.0, 3.0)], cfg={"fallback": False})
    d.astrometry_failed = True                             # Fehler vom vorigen Versuch steht noch im Status
    cur = p.items[0]
    s = d.state()
    cur.status, cur.seen = "aktiv", False
    assert p._failed(cur, s, clock.now()) is False
    cur.seen = True
    assert p._failed(cur, s, clock.now()) is True


def test_verbindung_weg_ohne_geraeteplan_kein_falscher_hinweis(monkeypatch):
    """Kann der Treiber keinen Geräte-Plan, darf das Protokoll beim Verbindungsverlust nicht behaupten, der Plan laufe weiter."""
    monkeypatch.setattr(sim.SimDriver, "capabilities", frozenset({"focus"}))
    sc = sim.Scenario(offline=[(T0 + 0.5 * H, T0 + 0.6 * H)])
    p, d, clock = make([item(M31, 0.0, 1.0)], sc)
    run(p, clock, 0.55)
    assert "Verbindung zum Teleskop verloren" in texts(p) and "Plan im Gerät" not in texts(p)


def _feed(p, cur, clock, bad_at):
    """Bilder einzeln (10 s) einspielen; bad_at(n) → verworfen? Gibt die Bildnummer zurück, bei der `_failed` greift."""
    cur.status, cur.seen = "aktiv", True
    rej, t = 0, clock.now()
    for n in range(1, 400):
        rej += bool(bad_at(n))
        s = TelescopeState(connected=True, parked=False, target={"id": cur.id}, step="capture", frames=n, stacked=n - rej, rejected=rej)
        t += 10
        p._track(s, t)
        if p._failed(cur, s, t):
            return n, rej
    return None, rej


def test_m1_balkon_zunehmend_verdeckt_wird_erkannt():
    """Echtbetrieb 14.11.2026: M1 (Az 146°, Höhe 59°) lief zunehmend hinter den Balkon – Verwurf langsam steigend, zwischendurch
    gute Bilder, nach 71 Bildern 23 verworfen (32 %). Das ist ein Hindernis und muss als verdeckt erkannt werden, spätestens
    `BAD_GRACE_S` nachdem die letzten 30 Bilder überwiegend verworfen sind."""
    p, d, clock = make([item(M31, 0.0, 3.0)], cfg={"fallback": False})
    acc = {"x": 0.0}

    def bad(n):                                    # Anteil steigt gleichmäßig: 0 → 64 % bei Bild 71, danach weiter bis 100 %
        acc["x"] += min(1.0, 0.64 * n / 71)
        if acc["x"] >= 1.0:
            acc["x"] -= 1.0
            return True
        return False
    hit, rej = _feed(p, p.items[0], clock, bad)
    assert hit is not None, "zunehmendes Hindernis rutscht durch"
    assert hit <= 71 + ap.LONG_FRAMES + ap.BAD_GRACE_S / 10, hit
    assert rej / hit < 0.8                         # erkannt, obwohl insgesamt längst nicht „fast alles“ verworfen ist


def test_wolkenzug_mit_pausen_erst_nach_wartezeit():
    """Schübe von je 7 verworfenen Bildern mit 4 guten dazwischen: nicht sofort verdeckt, sondern erst nach `BAD_GRACE_S`
    (kann auch ein Hindernis sein – C 51 ist ungeklärt). Das erste Scheitern sperrt ohnehin nur vorläufig."""
    p, d, clock = make([item(M31, 0.0, 3.0)], cfg={"fallback": False})
    hit, _ = _feed(p, p.items[0], clock, lambda n: n > 40 and (n - 41) % 11 >= 4)
    assert hit is not None and hit >= 40 + ap.BAD_GRACE_S / 10


def test_dauerhaft_verworfen_ist_verdeckt():
    """Gute Bilder, dann kippt alles (Ziel sinkt hinter einen Baum): nach `BAD_GRACE_S` am Stück verdeckt."""
    p, d, clock = make([item(M31, 0.0, 3.0)], cfg={"fallback": False})
    cur = p.items[0]
    cur.status, cur.seen = "aktiv", True
    t, n, rej, hit = clock.now(), 0, 0, None
    for k in range(200):
        n += 1
        rej += k >= 60
        s = TelescopeState(connected=True, parked=False, target={"id": cur.id}, step="capture", frames=n, stacked=n - rej, rejected=rej)
        t += 10
        p._track(s, t)
        if p._failed(cur, s, t):
            hit = k
            break
    assert hit is not None and 60 + ap.BAD_GRACE_S / 10 <= hit <= 60 + ap.BAD_GRACE_S / 10 + ap.LONG_FRAMES


def test_dienst_telescope_sky():
    p, d, clock = make([item(M31, 0.0, 3.0)], cfg={"fallback": False})
    assert p.sky_now(clock.now()) is None                          # noch eingeklappt/keine Bilder
    run(p, clock, 0.3)
    v = p.sky_now(clock.now())
    assert v["clear"] and v["ratio"] >= 0.9 and clock.now() - v["t"] < 120
    assert p.sky_now(clock.now() + ap.SKY_FRESH_S + 300) is None   # zu alt
    d.sc.clouds = [(clock.now(), clock.now() + 2 * H)]
    run(p, clock, 0.05)
    v = p.sky_now(clock.now())
    assert v and not v["clear"] and v["ratio"] < 0.5                # Wolkenzug: laut Teleskop bewölkt


def test_dienst_telescope_sky_im_modul(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch)
    fn = app.state.ctx.services["telescope_sky"]
    assert fn() is None                                            # Simulator: nie ein erfundener Himmel
    mod = app.state.modules["telescope"]
    monkeypatch.setattr(type(mod.unit), "simulated", property(lambda self: False))
    monkeypatch.setattr(mod.unit.pilot, "sky_now", lambda now: {"clear": True, "ratio": 0.9, "t": now, "why": "x"})
    assert fn()["clear"] and fn()["source"] == "Vaonis Vespera"


def test_akku_rate_je_betriebsart():
    """Codex #129: Eine eingeklappt gemessene Rate gilt nicht für offen (und umgekehrt) – Wechsel beginnt neuen Verlauf."""
    from astroflow.modules.telescope import battery as bt
    b = bt.BatteryEta()
    for k in range(0, 31):                                       # eingeklappt: 4 %/h
        b.update(T0 + k * 120, round(90 - 4 * k * 120 / 3600), False, active=False)
    assert b.measured and b.rate("vespera", False) == b.measured
    assert b.rate("vespera", True) == bt.DRAIN_PCT_H["vespera"]["aktiv"]          # offen: Erfahrungswert, nicht 4 %/h
    b.update(T0 + 4000, 85, False, active=True)                 # geöffnet → neuer Verlauf
    assert b.measured is None and len(b.samples) == 1


def test_akku_reicht_nicht_vorige_beobachtung_wird_gestoppt():
    """Codex #129: Wird das nächste Ziel wegen Akku abgelehnt, läuft die vorige Beobachtung nicht unbeobachtet weiter."""
    p, d, clock = make([item(M15, 0.0, 0.1), item(M31, 0.1, 3.0)], sim.Scenario(battery=18, battery_per_h=20), cfg={"fallback": False})
    run(p, clock, 0.2, step=20)
    assert "starte M31 nicht mehr" in texts(p) and "stop" in d.commands and d.step != "capture"


def test_einstellungen_speichern_behaelt_eigene_ziele(tmp_path, monkeypatch):
    """Echtbetrieb 14.11.2026: Wer Einstellungen änderte, verlor von Hand hinzugefügte Ziele (M1) – der Autopilot wird neu
    gebaut und übernimmt sie jetzt (ebenso Sperren, Vorschläge, Protokoll)."""
    app = _app(tmp_path, monkeypatch)
    with TestClient(app, base_url="http://127.0.0.1:8766", client=("127.0.0.1", 50000),
                    headers={"Origin": "http://127.0.0.1:8766"}) as c:
        assert c.post("/api/telescope/targets", json={"name": "M 1", "hours": 1}).status_code == 200
        app.state.modules["telescope"].unit.pilot.proposals.append({"az": 146.0, "alt": 59.0, "t": 0.0, "target": "M1", "sure": True})
        old = app.state.modules["telescope"].unit.pilot
        assert c.post("/api/telescope/settings", json={"cloud_max": 55}).status_code == 200
        new = app.state.modules["telescope"].unit.pilot
        assert new is not old
        assert [i.name for i in new.items if i.manual] == [i.name for i in old.items if i.manual] and new.items
        assert new.proposals and "hinzugefügt" in texts(new)
        names = [i["name"] for i in c.get("/api/telescope/state").json()["items"]]
        assert any("1" in n for n in names)


def _rain_at(clock, start, end):
    """Attrappe des Dienstes `weather_rain`: Regen zwischen start und end (Unix-Sekunden)."""
    def f(site, minutes):
        now = clock.now()
        if now >= end or start - now > minutes * 60:
            return {"rain": False, "in_minutes": None, "probability": 10, "precip": 0.0}
        return {"rain": True, "in_minutes": max(0, round((start - now) / 60)), "probability": 80, "precip": 1.2}
    return f


def test_regen_klappt_vorsorglich_ein_und_oeffnet_danach():
    """Regen in ≤ 30 min → laufende Beobachtung beenden, einklappen; danach (kein Regen mehr + stabile Minuten) weiter."""
    p, d, clock = make([item(M31, 0.0, 4.0)], cfg={"fallback": False})
    p.env.rain = _rain_at(clock, T0 + 1.5 * H, T0 + 2.0 * H)
    run(p, clock, 0.9)
    assert p.mode == "laeuft" and p.items[0].status == "aktiv"                # Regen erst in 36 min: Ziel läuft weiter
    run(p, clock, 0.4)
    assert p.mode == "wartet" and p.hold.kind == "rain" and "Regen in ca." in texts(p)
    assert d.state().parked
    run(p, clock, 0.6)
    assert d.state().parked                                                  # regnet noch
    run(p, clock, 0.9)
    assert p.mode == "laeuft" and not d.state().parked


def test_regen_bald_kein_neues_ziel_und_nicht_oeffnen():
    """Regen in 30–60 min: kein neues Ziel – statt zu wechseln einklappen; eingeklappt gar nicht erst öffnen."""
    p, d, clock = make([item(M31, 0.0, 0.5), item(M15, 0.5, 3.0)], cfg={"fallback": False})
    p.env.rain = _rain_at(clock, T0 + 1.3 * H, T0 + 3.0 * H)
    run(p, clock, 0.6)
    assert "starte M15 nicht mehr" in texts(p) and p.hold.kind == "rain"
    assert not any(i.id == "M15" and i.status == "aktiv" for i in p.items)
    p2, d2, clock2 = make([item(M31, 0.0, 3.0)], cfg={"fallback": False})
    p2.env.rain = lambda site, minutes: {"rain": True, "in_minutes": 50, "probability": 70, "precip": 0.5}
    run(p2, clock2, 0.2)
    assert d2.state().parked and "open" not in d2.commands and p2.hold.kind == "rain"


def test_regen_bald_geraeteplan_wechselt_nicht_selbst():
    """Mit Geräte-Plan (Rückfall an) schwenkt das Gerät am Blockende selbst weiter – bei Regen in ≤ 60 min vorher einklappen."""
    p, d, clock = make([item(M31, 0.0, 0.5), item(M15, 0.5, 3.0)])
    p.env.rain = _rain_at(clock, T0 + 1.3 * H, T0 + 3.0 * H)
    seen = set()
    for _ in range(int(0.7 * H / 20)):
        p.tick(clock.now())
        clock.advance(20)
        if (t := d.state().target):
            seen.add(t.get("id"))
    assert any(c.startswith("plan:") for c in d.commands)                    # Geräte-Plan war wirklich aktiv
    assert "M15" not in seen and p.hold.kind == "rain" and d.state().parked
    assert "kein Zielwechsel mehr" in texts(p)


def test_regen_unbekannt_oder_ohne_dienst_wie_bisher():
    p, d, clock = make([item(M31, 0.0, 1.0)], cfg={"fallback": False})
    p.env.rain = lambda site, minutes: {"rain": None, "in_minutes": None, "probability": None, "precip": None}
    run(p, clock, 0.5)
    assert p.mode == "laeuft" and p.rain_in(clock.now()) is None and p.view(clock.now())["conditions"]["rain_in"] is None


def test_regen_dienst_im_modul(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch)
    calls = []
    app.state.ctx.services["weather_rain"] = lambda minutes=120, site=None, now=None: calls.append((minutes, site)) or {"rain": False}
    env = app.state.modules["telescope"].unit.pilot.env
    assert env.rain(SITE, 60) == {"rain": False} and calls == [(60, {"lat": 50.0, "lon": 10.0})]
    app.state.ctx.services.pop("weather_rain")
    assert env.rain(SITE, 60) is None                                     # Dienst fehlt → kein Regenwächter


def test_neustart_nach_stillstand_zaehlt_nur_angenommen_und_neuer_versuch():
    """Review PR #126: Ein abgelehnter Neustart zählt nicht (nächster Stillstand → neuer Versuch); ein angenommener setzt
    `real_start` neu, damit die Live-Bilder den neuen Ordner am Gerät abholen."""
    p, d, clock = make([item(M31, 0.0, 3.0)], sim.Scenario(stop_silently_at=T0 + 0.5 * H), cfg={"fallback": False})
    real_observe, refused = d.observe, []

    def observe(*a, **k):
        if not refused:
            refused.append(clock.now())
            raise DriverError("Teleskop ist beschäftigt.")
        return real_observe(*a, **k)
    run(p, clock, 0.4, step=10)
    m31 = p.items[0]
    first = m31.real_start
    assert m31.status == "aktiv" and first
    d.observe = observe
    while not refused and clock.now() < T0 + H:
        run(p, clock, 10 / H, step=10)
    assert refused and m31.status == "aktiv" and m31.restarts == 0 and m31.real_start == first
    run(p, clock, 0.2, step=10)
    assert m31.restarts == 1 and m31.real_start > first and "starte sie einmal neu" in texts(p)


def test_live_bilder_von_beobachtung_aus_der_app(tmp_path):
    """Review PR #119: Treiber „nur Status und Bilder“ – die Beobachtung läuft aus der Vaonis-App, der Autopilot hat kein
    Ziel. Bei `capture` wird der neueste Beobachtungsordner (mit und ohne Plan-Ebene) als eigener Versuch abgeholt."""
    from astroflow.modules.telescope import frames
    imgs = {"_m": "20261120183000", "img-0001.fits": b"A" * 8}
    tree = {"user": {"2026-11-20_19-30-00_observation_M1": {"_m": "20261120183000", "01-images-initial": imgs}}}
    pilot = type("P", (), {"night": "2026-11-20", "log": lambda *a, **k: None, "frames_fetched": 0, "mode": "aus",
                           "state": TelescopeState(step="capture", target={"id": "M1", "name": "M1"}),
                           "_current": lambda self: None})()

    class U:
        cfg = {"model": "vespera"}
    U.pilot = pilot
    col = frames.Collector(U(), tmp_path, "10.0.0.1")
    col.ftp = frames.FtpFrames("x", ftp_factory=lambda: FakeFtp(tree))
    col.step()
    col.step()
    out = tmp_path / "Teleskop" / "vespera" / "2026-11-20" / "M1"
    assert [x.name for x in out.rglob("*.fits")] == ["img-0001.fits"]
    imgs["img-0002.fits"] = b"B" * 8                       # gerade geschrieben, dann am Gerät beendet → Rest abholen
    pilot.state = TelescopeState(step="idle")
    col.step()
    assert sorted(x.name for x in out.rglob("*.fits")) == ["img-0001.fits", "img-0002.fits"]
    tree["user"]["plan"] = {"_m": "20261120210000", "obs-m42": {"_m": "20261120210000", "01-images-initial": {"img-0001.fits": b"C" * 8}}}
    pilot.state = TelescopeState(step="capture", target={"id": "M42", "name": "M42"})        # Plan aus der App: neues Ziel
    col.step()
    pilot.state = TelescopeState(step="idle")
    col.step()
    assert (tmp_path / "Teleskop" / "vespera" / "2026-11-20" / "M42" / "obs-m42" / "img-0001.fits").read_bytes() == b"C" * 8
    # Review PR #148: dasselbe Ziel in der App neu gestartet (zwischen zwei Abfragen) → neuer Ordner, Rest des alten kommt mit
    pilot.state = TelescopeState(step="capture", target={"id": "M42", "name": "M42"})
    col.step()
    col.step()
    first = tree["user"]["plan"]["obs-m42"]["01-images-initial"]
    first["img-0002.fits"] = b"D" * 8
    tree["user"]["plan"]["obs-m42b"] = {"_m": "20261120220000", "01-images-initial": {"img-0001.fits": b"E" * 8}}
    col.step()
    col.step()
    m42 = tmp_path / "Teleskop" / "vespera" / "2026-11-20" / "M42"
    assert (m42 / "obs-m42" / "img-0002.fits").read_bytes() == b"D" * 8
    assert (m42 / "obs-m42b" / "img-0001.fits").read_bytes() == b"E" * 8
    pilot.state = TelescopeState(step="idle")
    col.step()
    pilot.mode = "laeuft"                                  # Autopilot steuert, nur zwischen zwei Zielen: nichts Fremdes holen
    pilot.state = TelescopeState(step="capture", target={"id": "M42", "name": "M42"})
    n = col.count
    col.step()
    col.step()
    assert col.count == n and col.attempt is None


def test_entry_point_plugin_optionen_nach_aktivierung(monkeypatch):
    """Review PR #119: Optionsfelder eines installierten Plugins (Klassenattribut `plugin`) erscheinen nach der Aktivierung
    wie bei Ordner-Plugins – vorher wird nichts importiert."""
    import importlib.metadata as md
    calls = []

    class Treiber(sim.SimDriver):
        plugin = {"name": "Paket-Treiber", "contract": 1, "options": [{"key": "ip", "label": "Adresse"}, {"label": "ohne Schlüssel"}]}

        def __init__(self, options):
            super().__init__(sim.ManualClock(0))
            self.options = options

    class EP:
        name, value = "paket", "paket.modul:Treiber"

        def load(self):
            calls.append("load")
            return Treiber
    monkeypatch.setattr(md, "entry_points", lambda group=None: [EP()] if group == plugins.GROUP else [])
    pid = plugins._entry_points()[0].id
    assert calls == [] and plugins._entry_points()[0].options == []
    info = plugins._entry_points({pid})[0]
    assert calls == ["load"] and [o["key"] for o in info.options] == ["ip"] and info.error is None
    assert plugins.load(plugins._entry_points()[0], {"ip": "10.0.0.2"}).options == {"ip": "10.0.0.2"}
    info2 = plugins._entry_points()[0]
    plugins.load(info2)
    assert [o["key"] for o in info2.options] == ["ip"]


def test_nach_bewoelkter_nacht_gilt_ab_mittag_die_neue_nacht():
    """Review PR #119: Ganz bewölkte Nacht, kein Ziel gestartet – der Autopilot wartet den Tag über. Ab dem Nachtwechsel
    (Mittag, `core/sites.night_of`) gilt der Plan der neuen Nacht, nicht noch einmal der alte."""
    asked = []

    def forecast(site, a, b):
        return {"hours": [{"t": T0 + k * 1800, "cloud": 95, "wind": 5, "dew_spread": 8} for k in range(-4, 80)]}
    p, d, clock = make([item(M31, 0.0, 3.0)], forecast=forecast)
    plan = p.env.plan
    p.env.plan = lambda n, m: asked.append(n) or plan(n, m)
    run(p, clock, 2.0, step=60)
    first = p.night
    assert p.mode == "wartet" and p.hold.kind == "weather" and not p._started_tonight()
    run(p, clock, 16.0, step=120)                          # bis 09 Uhr UTC: noch dieselbe Nacht (Mittag-zu-Mittag)
    assert p.night == first and p.mode == "wartet"
    run(p, clock, 4.0, step=120)                           # über Mittag: neue Nacht
    assert p.night != first and asked and asked[-1] == p.night and p.mode == "wartet"
