"""„Plane meine Nacht“ (Teleskop-Autopilot + Dienst `night_autoplan`): Entwurf, Start, Warten auf Dunkelheit, Vermessen,
Neuplanung mit gemessener Grenze, Ausfall → Neuplanung, Einklappen am Morgen, Nachtbilanz. Nur Simulator mit virtueller
Uhr; Ort und Zeiten erfunden."""
from __future__ import annotations

import datetime as dt
import json
import time

from fastapi.testclient import TestClient

from astroflow.core import security
from astroflow.core.config import load_settings
from astroflow.modules.telescope import autopilot as ap, sim, skymap
from astroflow.server import create_app

SITE = {"id": "t1", "name": "Testbalkon", "lat": 50.0, "lon": 10.0, "horizon": None}
DUSK = dt.datetime(2026, 11, 20, 15, 30, tzinfo=dt.timezone.utc).timestamp()     # noch hell (Sonne ~ −2°)
H = 3600


def _at(h: float, m: int = 0) -> float:
    """Uhrzeit (UTC) der Testnacht: Abend 20.11., nach Mitternacht 21.11."""
    day = dt.datetime(2026, 11, 20 if h >= 12 else 21, tzinfo=dt.timezone.utc).timestamp()
    return day + h * H + m * 60


# Plan der Attrappe (UTC): M33 und M45 frei im Osten/Süden, M31 läuft gegen 01 Uhr in die Hauswand (Norden), M44 frei
PLAN = [("M33", 23.46, 30.66, _at(17), _at(19, 30)), ("M45", 56.75, 24.12, _at(19, 30), _at(23, 30)),
        ("M31", 10.68, 41.27, _at(23, 30), _at(1, 30)), ("M44", 130.1, 19.67, _at(1, 30), _at(5, 30))]


class FakePlanner:
    """Attrappe für den Dienst: liefert den festen Plan ab `now` und merkt sich jede Anfrage (Grenze, Optionen)."""

    def __init__(self, plan=PLAN, drop=()):
        self.plan, self.drop, self.calls = plan, set(drop), []

    def __call__(self, night, horizon, now, opts):
        self.calls.append({"night": night, "horizon": list(horizon), "now": now, "opts": dict(opts)})
        out = []
        for name, ra, dec, a, b in self.plan:
            if b <= now + 600 or name in self.drop:
                continue
            out.append({"id": name, "name": name, "ra": ra, "dec": dec, "start": max(a, now), "end": b, "hours": (b - max(a, now)) / H,
                        "category": "new", "reason": f"{name}: steht hoch, braucht noch etwas"})
        return {"blocks": out[: opts["max_targets"]], "moon": 40, "cloud": None, "notes": []}


def make(scenario=None, planner=None, cfg=None, forecast=None, t0=DUSK, items=()):
    clock = sim.ManualClock(t0)
    d = sim.SimDriver(clock, scenario or sim.Scenario.balcony(t0))
    env = ap.Env(site=lambda n: SITE, plan=lambda n, m: [ap.Item(**vars(i)) for i in items], forecast=forecast,
                 sky_store=skymap.SkyStore(None), autoplan=planner if planner is not None else FakePlanner())
    p = ap.Autopilot(d, env, cfg, clock=clock)
    d.connect()
    d.take_control()
    return p, d, clock


def run(p, clock, until: float, step: float = 10):
    while clock.now() < until:
        p.tick(clock.now())
        clock.advance(step)


def texts(p):
    return " | ".join(e["text"] for e in p.events)


def by_name(p, name):
    return [i for i in p.items if i.name == name]


# ------------------------------------------------------------------ Ablauf --- #
def test_ganze_nacht_im_simulator():
    """Klick → Entwurf → Start → wartet auf Dunkelheit → vermessen → neu planen mit gemessener Grenze → beobachten →
    M31 läuft in die Hauswand (Ausfall → Rest neu planen) → Morgendämmerung → einklappen → Nachtbilanz."""
    fp = FakePlanner()
    p, d, clock = make(planner=fp)
    assert p.plan_night(clock.now()) is None
    assert p.auto["state"] == "entwurf" and [i.name for i in p.items] == ["M33", "M45", "M31", "M44"]
    assert all(i.auto and i.reason for i in p.items)
    assert not [c for c in d.commands if c.split(":")[0] in ap.MOVES]         # Entwurf bewegt nichts
    assert p.mode == "aus"
    assert p.night_go(clock.now()) is None and p.mode == "laeuft" and p.auto["state"] == "vermessen"
    run(p, clock, _at(16, 40))
    assert p.mode == "wartet" and "warte auf die Dunkelheit (ab " in texts(p)
    assert d.parked and "Dunkelheit" in p.auto_view(clock.now())["label"]
    run(p, clock, _at(19))
    assert p.last_scan and p.last_scan["state"] == "fertig"                   # Himmel vermessen
    assert p.auto["state"] == "laeuft" and p.auto["replans"] >= 1
    after_scan = fp.calls[1]["horizon"]
    assert after_scan[skymap.bin_of(0)] >= 50                                 # Hauswand im Norden gemessen → Grenze für die Planung
    assert fp.calls[0]["horizon"][skymap.bin_of(0)] < 50                     # Entwurf kannte sie noch nicht
    assert "Plan neu berechnet (nach der Vermessung)" in texts(p)
    run(p, clock, _at(7))
    assert p.mode == "fertig" and d.parked
    m31 = by_name(p, "M31")
    assert m31 and m31[-1].status in ("fertig", "uebersprungen", "verdeckt")
    assert "M31 verlässt den freien Bereich" in texts(p) or "M31 verdeckt" in texts(p)
    assert len(fp.calls) >= 3                                                 # Rest der Nacht nach dem Ausfall neu geplant
    for name in ("M33", "M45", "M44"):
        assert any(i.status == "fertig" and i.stacked > 0 for i in by_name(p, name)), name
    s = p.auto["summary"]
    assert p.auto["state"] == "fertig" and s and s["frames"] > 0 and s["measured"]
    assert "Nachtbilanz" in texts(p) and "Morgendämmerung" in texts(p)
    assert '"lat"' not in json.dumps(p.view(clock.now()))


def test_verdeckt_plant_rest_neu_und_hebt_grenze_an():
    """Ohne Vermessung läuft ein Ziel in ein Hindernis: verdeckt → Rest neu geplant, die Richtung ist für die Planung gesperrt."""
    t0 = _at(23, 50)
    fp = FakePlanner(plan=[("M31", 10.68, 41.27, t0, _at(2)), ("M44", 130.1, 19.67, _at(2), _at(5))])
    p, d, clock = make(planner=fp, t0=t0, cfg={"scan_auto": False})
    p.night_points = [{"az": 180, "alt": 40, "res": "frei", "sure": True, "src": "scan", "t": t0}]   # „schon vermessen“
    p.plan_night(clock.now())
    p.night_go(clock.now())
    run(p, clock, _at(1, 50))
    assert "M31 verdeckt" in texts(p) or "M31 verlässt" in texts(p)
    assert any("verdeckt" in e["text"] and "Plan neu berechnet" in e["text"] for e in p.events) or len(fp.calls) >= 3
    assert "M31" in fp.calls[-1]["opts"]["exclude"]                          # heute verdeckt: nicht wieder wählen
    if p.blocked:                                                              # gesperrte Richtung hebt die Grenze für die Planung
        b = p.blocked[0]
        assert fp.calls[-1]["horizon"][skymap.bin_of(b["az"])] >= b["alt"]
    assert any(i.name == "M44" and i.status in ("aktiv", "geplant", "fertig") for i in p.items)


def test_wiederoeffnen_nach_wetter_plant_neu():
    t0 = _at(20)
    bad = (_at(21, 30), _at(22, 30))

    def forecast(site, a, b):
        return {"hours": [{"t": t0 + k * 1800, "cloud": 95 if bad[0] <= t0 + k * 1800 < bad[1] else 5, "wind": 5, "dew_spread": 8}
                          for k in range(-2, 30)]}
    fp = FakePlanner(plan=[("M45", 56.75, 24.12, t0, _at(23, 30)), ("M1", 83.63, 22.01, _at(23, 30), _at(3))])
    p, d, clock = make(planner=fp, t0=t0, forecast=forecast, cfg={"scan_auto": False})
    p.night_points = [{"az": 180, "alt": 40, "res": "frei", "sure": True, "src": "scan", "t": t0}]
    p.plan_night(clock.now())
    p.night_go(clock.now())
    run(p, clock, _at(21))
    assert p.auto["state"] == "laeuft" and p.mode == "laeuft"
    run(p, clock, _at(22))
    assert p.mode == "wartet" and p.hold.kind == "weather" and d.parked
    run(p, clock, _at(23, 30))
    assert p.mode == "laeuft" and "Plan neu berechnet (nach der Pause)" in texts(p)


def test_frist_startet_von_selbst_und_ohne_dienst_klare_meldung(monkeypatch):
    p, d, clock = make(cfg={"night_autostart": True})
    assert p.plan_night(clock.now(), autostart=True) is None
    assert p.auto_view(clock.now())["deadline_in"] > 0
    p.tick(clock.now())
    assert p.auto["state"] == "entwurf"                                       # Frist läuft noch
    p.auto["deadline"] = time.time() - 1
    p.tick(clock.now())
    assert p.auto["state"] == "vermessen" and p.mode in ("laeuft", "wartet")
    q, _, c2 = make()
    q.env.autoplan = None
    assert "Bilder-Modul" in q.plan_night(c2.now())


def test_verwerfen_stellt_planer_plan_wieder_her():
    planned = [ap.Item("M45", "M45", 56.75, 24.12, _at(19), _at(23))]
    p, d, clock = make(items=planned)
    p.load_night(clock.now())
    assert [i.name for i in p.items] == ["M45"] and not p.items[0].auto
    p.plan_night(clock.now())
    assert all(i.auto for i in p.items)
    assert p.night_cancel(clock.now()) is None
    assert p.auto is None and [i.name for i in p.items] == ["M45"]


def test_keine_ziele_klappt_ein_und_bilanziert():
    p, d, clock = make(planner=FakePlanner(plan=[]), cfg={"scan_auto": False}, t0=_at(20))
    p.night_points = [{"az": 180, "alt": 40, "res": "frei", "sure": True, "src": "scan", "t": clock.now()}]
    p.plan_night(clock.now())
    p.night_go(clock.now())
    run(p, clock, _at(21, 30))
    assert p.mode == "fertig" and d.parked and p.auto["summary"]["frames"] == 0
    assert "Keine passenden Ziele" in texts(p)


# ---------------------------------------------------------------------- API --- #
def _app(tmp_path, monkeypatch, plan=None, **cfg):
    data = tmp_path / "data"
    data.mkdir(exist_ok=True)
    st = {"sites": {"list": [{k: v for k, v in SITE.items() if k != "horizon"}], "default": "t1"},
          "telescope": {"sim_speed": 1, "sim_start": "now", "sim_scenario": "balkon", "weather": False, **cfg}}
    if plan:
        st["observatory"] = {"plan": plan}
    (data / "settings.json").write_text(json.dumps(st), encoding="utf-8")
    monkeypatch.setattr(security, "lan_addresses", lambda: ["192.0.2.10"])
    monkeypatch.setenv("ASTROFLOW_MODULES", "stacking,telescope")
    return create_app(load_settings(data))


def _client(app):
    return TestClient(app, base_url="http://127.0.0.1:8766", client=("127.0.0.1", 50000), headers={"Origin": "http://127.0.0.1:8766"})


def test_api_echte_zielwahl_mit_begruendung(tmp_path, monkeypatch):
    """Mit dem echten Dienst des Bilder-Moduls (Planer-Regeln, Katalog, ohne Wetter): höchstens N Ziele, Begründung je Ziel."""
    with _client(_app(tmp_path, monkeypatch, night_max_targets=3)) as c:
        r = c.post("/api/telescope/night/plan", json={})
        assert r.status_code == 200, r.text
        st = c.get("/api/telescope/state").json()
        assert st["auto"]["state"] == "entwurf" and st["auto"]["max_targets"] == 3
        auto = [i for i in st["items"] if i["auto"]]
        if st["dark"] and st["dark"]["end"] - st["now"] > 3 * H:              # echte Uhr: nur mit genug Nacht prüfbar
            assert auto
        assert len({i["id"] for i in auto}) <= 3
        for i in auto:
            assert "steht" in i["reason"] and "braucht" in i["reason"] and i["end"] - i["start"] >= 20 * 60
        assert '"lat"' not in json.dumps(st["auto"])
        assert c.post("/api/telescope/night/cancel").status_code == 200
        assert c.get("/api/telescope/state").json()["auto"] is None


def test_api_planer_plan_hat_vorrang_bis_zur_rueckfrage(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch, sim_start="night")                        # Simulator ab Dämmerung, unabhängig von der echten Uhrzeit
    with _client(app) as c:
        mod = app.state.modules["telescope"]
        night = mod.unit.pilot.current_night(mod.unit.now())
        now = mod.unit.now()
        plan = {"devices": [{"id": "vespera"}], "targets": [{"id": "M45", "name": "M45", "ra": 56.75, "dec": 24.12}],
                "nights": [{"night": night, "blocks": [{"device": "vespera", "target": "M45", "start": now, "end": now + 2 * H}]}]}
        app.state.ctx.store.update("observatory", {"plan": plan})
        r = c.post("/api/telescope/night/plan", json={})
        assert r.status_code == 409 and "schon einen Plan" in r.json()["detail"]
        assert c.get("/api/telescope/state").json()["auto"] is None
        assert c.post("/api/telescope/night/plan", json={"replace": True}).status_code == 200
        assert c.get("/api/telescope/state").json()["auto"]["state"] == "entwurf"
        assert app.state.ctx.store.get("observatory").get("plan") == plan       # gespeicherter Planer-Plan bleibt
        r = c.post("/api/telescope/night/start", json={"always": True})
        assert r.status_code == 200
        assert c.get("/api/telescope/settings").json()["night_autostart"] is True
        assert c.post("/api/telescope/night/cancel").status_code == 409        # läuft: erst pausieren/einklappen
        assert c.post("/api/telescope/park").status_code == 200


def test_planer_haelt_ueberhang_und_hoechstzahl(tmp_path):
    """Planer-Regeln für „Plane meine Nacht“: höchstens N Ziele; unter einem gemessenen Überhang wird nichts geplant."""
    from astroflow.modules.stacking import planner
    dev = next(d for d in planner.device_options(None) if d["id"] == "vespera")
    site = {**SITE, "horizon": [20.0] * 36}
    w = {"nights": 1, "depth": "normal", "devices": ["vespera"], "mode": "mix", "watch": [], "eq": []}
    out = planner.plan([], lambda n: site, w, [dev], tmp_path, now=_at(17), max_targets=2)
    ids = {b["target"] for b in out["nights"][0]["blocks"]}
    assert 1 <= len(ids) <= 2
    roof = planner.plan([], lambda n: {**site, "top": [45.0] * 36}, w, [dev], tmp_path, now=_at(17), max_targets=4)
    blocks = roof["nights"][0]["blocks"]
    assert blocks and all(b["max_alt"] < 45 for b in blocks)


def test_not_aus_waehrend_frist_startet_nicht():
    """Review PR #145: „Sofort einklappen“ während der Frist – der Entwurf startet danach nicht von selbst."""
    p, d, clock = make(cfg={"night_autostart": True}, t0=_at(20))
    p.plan_night(clock.now(), autostart=True)
    p.emergency(clock.now())
    assert p.auto["deadline"] is None
    p.auto["deadline"] = time.time() - 1                                     # auch eine schon abgelaufene Frist startet nicht
    run(p, clock, _at(20, 30))
    assert p.auto["state"] == "entwurf" and p.mode == "aus"
    assert not [c for c in d.commands if c.split(":")[0] in ("open", "init")]


def test_hoechstzahl_gilt_auch_beim_neuplanen():
    """Review PR #145: schon N Ziele beobachtet → Neuplanung nimmt keine neuen mehr dazu (bisherige dürfen weiterlaufen)."""
    fp = FakePlanner(plan=[("M45", 56.75, 24.12, _at(20), _at(23)), ("M1", 83.63, 22.01, _at(20), _at(23))])
    p, d, clock = make(planner=fp, cfg={"night_max_targets": 1}, t0=_at(20))
    p.plan_night(clock.now())
    p.auto["state"] = "laeuft"
    done = ap.Item("M33", "M33", 23.46, 30.66, _at(18), _at(20), auto=True, status="fertig", real_start=_at(18), stacked=50)
    p.items.append(done)
    assert p._replan(clock.now(), "Test", force=True)
    assert not [i for i in p.items if i.status == "geplant" and i.id not in ("M33",)]
    assert fp.calls[-1]["opts"]["max_targets"] == 1


def test_entwurf_ueberlebt_einstellungen_mit_ort_und_sonnensperre():
    """Review PR #146: Entwurf offen → Einstellungen speichern (`adopt`) → Start: Ort und Dunkelheit sind da, die Sonnensperre greift."""
    p, d, clock = make()                                       # DUSK: noch hell
    p.plan_night(clock.now())
    q = ap.Autopilot(d, p.env, {}, clock=clock)
    q.adopt(p)
    assert q.site and q.dark and q.auto["state"] == "entwurf" and [i.name for i in q.items] == [i.name for i in p.items]
    assert q.night_go(clock.now()) is None
    run(q, clock, DUSK + 20 * 60)
    assert q.mode == "wartet" and q.hold.kind == "twilight" and d.parked
    assert not [c for c in d.commands if c.split(":")[0] in ("open", "init")]


def test_ohne_ort_wird_nie_geoeffnet():
    """Ohne Standort weder Sonnenhöhe noch Ausrichten: der Autopilot öffnet nie, sondern wartet mit klarer Meldung."""
    p, d, clock = make(t0=_at(21))
    p.env.site = lambda n: None
    p.start(clock.now())
    run(p, clock, _at(21, 30))
    assert p.mode == "wartet" and p.hold.kind == "site" and d.parked
    assert not [c for c in d.commands if c.split(":")[0] in ("open", "init")]
    assert "Kein Standort" in texts(p)
    q, d2, c2 = make(t0=_at(21))
    q.plan_night(c2.now())
    q.site, q.env.site = None, (lambda n: None)
    assert "kein Standort" in q.night_go(c2.now())
    run(q, c2, _at(21, 30))
    assert not [c for c in d2.commands if c.split(":")[0] in ("open", "init")]


def test_pause_bewahrt_gesammelte_bilder_beim_neuplanen():
    """Review PR #145: Wetterpause unterbricht ein Ziel (zurück auf „geplant“, Bilder bleiben) – nach der Neuplanung bleiben
    die Bilder als Verlauf, zählen für den Bedarf und in der Nachtbilanz."""
    fp = FakePlanner(plan=[("M45", 56.75, 24.12, _at(20), _at(23, 30))])
    p, d, clock = make(planner=fp, t0=_at(20))
    p.plan_night(clock.now())
    p.auto["state"] = "laeuft"
    it = p.items[0]
    it.stacked = 100                                  # wie nach _end_item(…, "geplant", …): Bilder da, real_start gelöscht
    assert p._replan(clock.now(), "nach der Pause", force=True)
    old = [i for i in p.items if i.stacked == 100]
    assert old and old[0].status == "abgebrochen" and fp.calls[-1]["opts"]["have_add"]["M45"] > 0
    assert p._replan(clock.now() + 900, "nochmal", force=True)
    assert [i for i in p.items if i.stacked == 100] and fp.calls[-1]["opts"]["have_add"]["M45"] > 0
    p._finish_auto(clock.now())
    assert p.auto["summary"]["frames"] == 100


def test_nachfuellen_mit_spaeteren_zielen_wartet_statt_einzuklappen():
    """Review PR #145: Plan früh erledigt, die Neuplanung liefert nur ein Ziel, das erst später frei wird (M44 geht erst nach
    Mitternacht hoch) – der Autopilot wartet und beobachtet es dann, statt die Nacht zu beenden."""
    t0 = _at(20)

    class Later(FakePlanner):
        def __call__(self, night, horizon, now, opts):
            self.plan = [("M45", 56.75, 24.12, t0, _at(21))] if now < _at(21) else [("M44", 130.1, 19.67, _at(1, 30), _at(5))]
            return super().__call__(night, horizon, now, opts)
    p, d, clock = make(planner=Later(), t0=t0, cfg={"scan_auto": False})
    p.night_points = [{"az": 180, "alt": 40, "res": "frei", "sure": True, "src": "scan", "t": t0}]       # „schon vermessen“
    p.plan_night(clock.now())
    p.night_go(clock.now())
    run(p, clock, _at(22))
    assert "Plan neu berechnet (Plan erledigt" in texts(p)
    assert not p.free(130.1, 19.67, clock.now())[0]                     # M44 jetzt noch nicht frei
    assert p.mode in ("laeuft", "wartet") and p.auto["state"] == "laeuft"
    assert any(i.name == "M44" and i.status == "geplant" for i in p.items)
    run(p, clock, _at(3))
    assert any(i.name == "M44" and i.stacked > 0 for i in p.items)


def test_api_start_bei_entwurf_startet_die_nacht(tmp_path, monkeypatch):
    """Review PR #145: Der normale Start bei offenem Entwurf fährt die Entwurfsziele nicht ohne Vermessung an, sondern
    startet die Nacht wie „Nacht starten“."""
    app = _app(tmp_path, monkeypatch, sim_start="night")                        # Simulator ab Dämmerung, unabhängig von der echten Uhrzeit
    with _client(app) as c:
        p = app.state.modules["telescope"].unit.pilot
        assert c.post("/api/telescope/night/plan", json={}).status_code == 200
        assert p.auto["state"] == "entwurf"
        assert c.post("/api/telescope/start", json={}).status_code == 200
        assert p.auto["state"] in ("vermessen", "laeuft") and p.scan_auto_night == p.night
        assert "Plane meine Nacht läuft" in texts(p)
        assert c.post("/api/telescope/park").status_code == 200
