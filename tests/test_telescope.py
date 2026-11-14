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
    p, d, clock = make([item(M31, 0.2, 1.7), item(M45, 1.7, 3.2), item(M81, 3.2, 4.0)], sim.Scenario(obstacles=[WALL]))
    run(p, clock, 2.0)
    assert p.proposals and p.proposals[0]["target"] == "M45" and p.proposals[0]["sure"]
    assert 60 <= p.proposals[0]["az"] <= 120
    assert "Hindernis" in texts(p)
    run(p, clock, 2.3)
    m45 = [i for i in p.items if i.id == "M45"]
    assert any(i.status == "fertig" for i in m45), "später, über der Wand, wird M45 noch einmal versucht"
    assert d.state().parked


def test_wolken_sind_kein_hindernis():
    """Zwei Richtungen ohne Sterne ohne Erfolg dazwischen → Wolken: einklappen, Vermutung „Hindernis“ zurücknehmen,
    nach dem Wolkenband und 20 stabilen Minuten weiter."""
    sc = sim.Scenario(clouds=[(T0 + 1.0 * H, T0 + 1.6 * H)])
    p, d, clock = make([item(M31, 0.2, 1.3), item(M15, 1.3, 3.0)], sc)
    run(p, clock, 1.2)
    assert p.mode == "wartet" and p.hold.kind == "clouds"
    assert not p.proposals and not p.blocked
    assert "Wolken" in texts(p)
    run(p, clock, 0.1)
    assert d.state().parked
    run(p, clock, 1.0)
    assert p.mode == "laeuft" and any(i.status == "aktiv" for i in p.items)


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
    assert items[0]["name"] == "M81" and {e["name"] for e in items} == {"M81", "M45", "M31"}     # neueste zuerst
    assert {e["key"] for e in items} == {"2026-11-12_19-58-10_plan_Demo/01-observation-m31", "2026-11-12_19-58-10_plan_Demo/02-observation-m45",
                                         "2026-11-13_22-41-05_plan_Demo/01-observation-m81"}
    assert items[0]["sub"] == "Plan Demo · 13.11.2026 22:41"
    assert all(e["frames"] == 4 and e["bytes"] > 0 and e["modified"] for e in items)


def test_api_import_vom_teleskop(tmp_path, monkeypatch):
    import time as _t
    with TestClient(_app(tmp_path, monkeypatch), base_url="http://127.0.0.1:8766", client=("127.0.0.1", 50000),
                    headers={"Origin": "http://127.0.0.1:8766"}) as c:
        d = c.get("/api/telescope/captures").json()
        assert d["available"] and d["simulated"] and len(d["items"]) == 3 and not any(e["imported"] for e in d["items"])
        assert c.post("/api/telescope/captures/import", json={"keys": ["../../etc", "C:/Windows"]}).status_code == 409
        key = "2026-11-12_19-58-10_plan_Demo/01-observation-m31"
        assert c.post("/api/telescope/captures/import", json={"keys": [key, "../../etc"]}).json()["state"] == "running"
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


def test_ordnernamen_lesbar():
    from astroflow.modules.telescope.captures import pretty
    assert pretty("2026-11-13_17-21-02_plan_Astroflow", "03-observation-m77") == ("M77", "Plan Astroflow · 13.11.2026 17:21")
    assert pretty("2026-11-13_17-21-02_plan_Mein_Plan", "01-observation-ngc2775")[0] == "NGC 2775"
    assert pretty("2026-11-13_17-21-02_plan_X", "02-observation-ic434")[0] == "IC 434"
    assert pretty("irgendwas", "anders") == ("anders", "irgendwas")                 # unbekanntes Schema: unverändert


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
