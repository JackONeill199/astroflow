"""In SkyTrace zugewiesener Ort gilt für die Nacht auch in Bilder (Einzelnacht der gemeinsamen Ortsliste, Neuberechnung
von Himmelsqualität und Gerätediagnose). Nur erfundene Orte (Testhausen, Beispielberg …)."""
import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from astroflow.core import sites
from astroflow.core.config import load_settings
from astroflow.core.store import SettingsStore
from astroflow.server import create_app

HOME = {"id": "home", "name": "Testhausen", "lat": 47.5, "lon": 10.25, "alt_m": 400, "accuracy_m": 50}
TRIP = {"id": "trip", "name": "Beispielberg", "lat": 45.0, "lon": 6.0, "alt_m": 1800, "accuracy_m": 30}
FERN = {"name": "Fernwiese", "lat": 44.0, "lon": 8.0, "alt_m": 300}
ENGINE = Path(__file__).resolve().parents[1] / "astroflow" / "modules" / "skytrace" / "engine"


def book(tmp_path, **data):
    b = sites.SiteBook(SettingsStore(tmp_path / "settings.json"))
    b.save({"list": [HOME, TRIP], "default": "home", "periods": [], **data})
    return b


# ---------------------------------------------------------------- Kern ---
def test_sync_nights_legt_an_stellt_um_und_entfernt(tmp_path):
    b = book(tmp_path, periods=[{"site_id": "trip", "from": "2024-07-06", "to": "2024-07-06"},       # vom Nutzer
                                {"site_id": "home", "from": "2024-07-01", "to": "2024-07-31"}])
    calls = []
    b.on_change = lambda: calls.append(1)
    r = b.sync_nights("skytrace", {"2024-07-05": {**FERN, "why": "SkyTrace: M31"},
                                   "2024-07-06": FERN,
                                   "2024-07-08": {**TRIP, "lat": 45.0005, "name": "egal"}})            # ~55 m neben Beispielberg
    assert (r["added"], r["user"]) == (2, ["2024-07-06"]) and calls
    s = b.site_for(night="2024-07-05")
    assert (s["name"], s["source"], s["by"]) == ("Fernwiese", "period", "skytrace") and "SkyTrace: M31" in s["detail"]
    assert b.site_for(night="2024-07-06")["id"] == "trip" and "by" not in b.site_for(night="2024-07-06")   # Nutzer bleibt
    assert b.site_for(night="2024-07-08")["id"] == "trip"                                   # naher Listenort wiederverwendet
    assert b.site_for(night="2024-07-09")["id"] == "home"                                   # Zeitraum des Nutzers unverändert
    new = [x for x in b.data()["list"] if x.get("src") == "skytrace"]
    assert len(new) == 1 and new[0]["name"] == "Fernwiese"
    calls.clear()
    assert b.sync_nights("skytrace", {"2024-07-05": {**FERN, "why": "SkyTrace: M31"}, "2024-07-06": FERN,
                                      "2024-07-08": TRIP})["added"] == 0 and not calls        # nichts geändert → kein Ereignis
    r = b.sync_nights("skytrace", {"2024-07-05": {**TRIP, "why": "SkyTrace: M31"}})
    assert (r["updated"], r["removed"]) == (1, 1)
    assert b.site_for(night="2024-07-05")["id"] == "trip" and b.site_for(night="2024-07-08")["id"] == "home"
    assert not [x for x in b.data()["list"] if x.get("src")]                                # unbenutzter Auto-Ort ist weg
    b.set_night("2024-07-05", "home")                                                        # Nutzer stellt um → gehört ihm
    assert b.sync_nights("skytrace", {"2024-07-05": TRIP})["user"] == ["2024-07-05"]
    assert b.site_for(night="2024-07-05")["id"] == "home"
    # Bearbeiten der Liste in der Oberfläche (ganzer Abschnitt per POST) behält die Marken
    d = b.data()
    b.save({"list": d["list"], "default": d["default"], "periods": d["periods"]})
    assert b.sync_nights("skytrace", {})["removed"] == 0


def test_sync_nights_uebernimmt_alte_migration_einmal(tmp_path):
    b = book(tmp_path)
    sites.migrate_skytrace(b, {"sites": {"2024-07-05_observation_M31": FERN}}, key="skytrace:x")   # Einzelnacht ohne Marke
    assert "by" not in b.site_for(night="2024-07-05")
    r = b.sync_nights("skytrace", {"2024-07-05": FERN}, claim="skytrace:nachtabgleich")
    assert r["user"] == [] and b.site_for(night="2024-07-05")["by"] == "skytrace"
    b.set_night("2024-07-06", "trip")
    assert b.sync_nights("skytrace", {"2024-07-06": {**TRIP, "lat": 45.0001}}, claim="skytrace:nachtabgleich")["user"] == ["2024-07-06"]


# ---------------------------------------------------------------- SkyTrace ---
def test_plan_mehrere_beobachtungen_einer_nacht():
    from astroflow.modules.skytrace.site_sync import plan
    near = {**FERN, "lat": 44.001}                                                           # ~110 m daneben
    wanted, conflicts = plan(
        {"2024-07-05_observation_M31": FERN, "2024-07-05_observation_M33": near,           # gleiche Nacht, gleicher Ort
         "2024-07-07_observation_M1": FERN, "2024-07-07_observation_M42": TRIP,            # gleiche Nacht, verschiedene Orte
         "lauf-ohne-datum": FERN, "kaputt": {"lat": 200, "lon": 0}},
        {"lauf-ohne-datum": {"2024-08-01"}})                                               # Nacht aus der Statistik
    assert sorted(wanted) == ["2024-07-05", "2024-08-01"]
    assert wanted["2024-07-05"]["why"] == "SkyTrace: M31, M33" and wanted["2024-07-05"]["name"] == "Fernwiese"
    assert conflicts == {"2024-07-07": ["M1", "M42"]}


@pytest.fixture
def obs(tmp_path, monkeypatch):
    """Engine-Einstellungen in tmp_path (wie test_sites.obs); danach wieder entladen."""
    before = set(sys.modules)
    if "observation_settings" not in sys.modules:
        monkeypatch.setenv("SKYTRACE_HOME", str(tmp_path / "st"))
    monkeypatch.syspath_prepend(str(ENGINE))
    import observation_settings
    monkeypatch.setattr(observation_settings, "CONFIG", tmp_path / "catalog_settings.json")
    yield observation_settings
    for name in set(sys.modules) - before:                     # nur Engine-Module (numpy & Co. lassen sich nicht neu laden)
        f = getattr(sys.modules.get(name), "__file__", None) or ""
        if f and Path(f).resolve().parent == ENGINE:
            sys.modules.pop(name, None)


def _lauf(home, sessions):
    run = home / "Ergebnisse" / "2024-07-06_01-00-00-000001"
    run.mkdir(parents=True)
    rows = [{"session": s, "video": f"{s}/ereignisse.mp4", "frames": 10, "state": "Fertig"} for s in sessions]
    (run / "videos.json").write_text(json.dumps(rows), encoding="utf-8")


def test_ausnahme_gilt_fuer_die_nacht_in_bilder(tmp_path, monkeypatch, obs):
    monkeypatch.setenv("ASTROFLOW_MODULES", "stacking,skytrace")
    home = tmp_path / "skytrace"
    _lauf(home, ["2024-07-05_observation_M31", "2024-07-06_observation_M1", "2024-07-07_observation_M2",
                 "2024-07-07_observation_M3"])
    s = load_settings(tmp_path / "d")
    store = SettingsStore(s.data_dir / "settings.json")
    store.update("skytrace", {"home": str(home)})
    store.update("sites", {"list": [HOME, TRIP], "default": "home", "periods": [{"id": "u1", "site_id": "trip", "from": "2024-07-06", "to": "2024-07-06"}]})
    with TestClient(create_app(s)) as c:
        r = c.post("/api/skytrace/settings", json={"session": "2024-07-05_observation_M31", "site": FERN}).json()
        assert r["site_sync"]["nights"] == ["2024-07-05"] and r["sites"]["2024-07-05_observation_M31"]["lat"] == 44.0
        site = c.get("/api/sites/resolve", params={"night": "2024-07-05"}).json()["site"]
        assert (site["name"], site["by"]) == ("Fernwiese", "skytrace")                      # Bilder: Dienst site_for
        n = next(x for x in c.get("/api/sites").json()["nights"] if x["night"] == "2024-07-05")
        assert n["fixed_by"] == "skytrace" and n["site"]["name"] == "Fernwiese"            # in der Ortsliste sichtbar
        # Einzelnacht des Nutzers bleibt, auch wenn SkyTrace dort eine Ausnahme hat
        r = c.post("/api/skytrace/settings", json={"session": "2024-07-06_observation_M1", "site": FERN}).json()
        assert r["site_sync"]["user"] == ["2024-07-06"]
        assert c.get("/api/sites/resolve", params={"night": "2024-07-06"}).json()["site"]["id"] == "trip"
        # zwei Beobachtungen einer Nacht an verschiedenen Orten → keine Zuordnung, Hinweis
        c.post("/api/skytrace/settings", json={"session": "2024-07-07_observation_M2", "site": FERN})
        r = c.post("/api/skytrace/settings", json={"session": "2024-07-07_observation_M3", "site": TRIP}).json()
        assert r["site_sync"]["conflicts"] == {"2024-07-07": ["M2", "M3"]}
        assert c.get("/api/sites/resolve", params={"night": "2024-07-07"}).json()["site"]["id"] == "home"
        # Ausnahme entfernt → Einzelnacht und der automatisch angelegte Ort verschwinden
        for sess in ("2024-07-05_observation_M31", "2024-07-06_observation_M1", "2024-07-07_observation_M2",
                     "2024-07-07_observation_M3"):
            c.post("/api/skytrace/settings", json={"session": sess, "site": None})
        assert c.get("/api/sites/resolve", params={"night": "2024-07-05"}).json()["site"]["id"] == "home"
        d = c.get("/api/sites").json()
        assert [x["name"] for x in d["list"]] == ["Testhausen", "Beispielberg"]
        assert [p["id"] for p in d["periods"]] == ["u1"]


def test_bestehende_ausnahmen_beim_start(tmp_path, monkeypatch, obs):
    monkeypatch.setenv("ASTROFLOW_MODULES", "stacking,skytrace")
    home = tmp_path / "skytrace"
    _lauf(home, ["2024-07-05_observation_M31"])
    (tmp_path / "catalog_settings.json").write_text(json.dumps({"sites": {"2024-07-05_observation_M31": FERN}}), encoding="utf-8")
    s = load_settings(tmp_path / "d")
    store = SettingsStore(s.data_dir / "settings.json")
    store.update("skytrace", {"home": str(home)})
    store.update("sites", {"list": [HOME], "default": "home", "periods": [], "migrated": {f"skytrace:{home.resolve()}": 1}})
    with TestClient(create_app(s)) as c:
        mod = c.app.state.modules["skytrace"]
        from astroflow.modules.skytrace.site_sync import sync
        sync(mod)                                                   # wie der Start-Thread (dort im Hintergrund)
        site = c.get("/api/sites/resolve", params={"night": "2024-07-05"}).json()["site"]
        assert (site["name"], site["by"]) == ("Fernwiese", "skytrace")


# ---------------------------------------------------------------- Bilder ---
def test_bilder_rechnet_naechte_mit_neuem_ort_neu(tmp_path, monkeypatch):
    from astroflow.modules.stacking import skyquality as sq, site_watch
    from astroflow.modules.stacking.diagnose import DIAG_VERSION
    monkeypatch.setattr(site_watch, "DELAY_S", 0.01)
    s = load_settings(tmp_path / "data")
    with TestClient(create_app(s, only=("stacking",))) as c:
        c.post("/api/sites", json={"list": [HOME, TRIP], "default": "home", "periods": []})
        eng = c.app.state.modules["stacking"].eng
        sf = eng.ctx.services["site_for"]
        sq.ensure_table(eng.db)
        a = eng.db.x("INSERT INTO projects(key, name, created) VALUES('a', 'A', 0)")
        b = eng.db.x("INSERT INTO projects(key, name, created) VALUES('b', 'B', 0)")
        old = eng.db.x("INSERT INTO projects(key, name, created) VALUES('c', 'Alt', 0)")
        home_night = sf(night="2024-07-05")
        for pid, key in ((a, sq.site_key(home_night)), (b, sq.site_key(sf(night="2024-08-01")))):
            eng.db.x("INSERT INTO sky_nights VALUES(?,?,?,?,?,?,?,?)", (pid, "2024-07-05" if pid == a else "2024-08-01", "home",
                     "Testhausen", "d", 1.0, sq.SKY_VERSION, json.dumps({"site_key": key})))
        eng.db.x("INSERT INTO sky_nights VALUES(?,?,?,?,?,?,?,?)", (old, "2024-07-05", "home", "Testhausen", "d", 1.0,
                 sq.SKY_VERSION, json.dumps({})))                                             # älter, ohne Fingerabdruck
        diag = {"v": DIAG_VERSION, "ok": True, "nights": [{"night": "2024-07-05", "site_source": "sites", "site": [47.5, 10.25]}]}
        rid = eng.db.x("INSERT INTO results(project_id, created, stats) VALUES(?, 0, ?)", (a, json.dumps({"diagnose": diag})))
        assert site_watch.stale_sky(eng.db, sf) == [] and site_watch.stale_diagnose(eng.db, sf) == []
        queued, submitted = [], []
        monkeypatch.setattr(eng.sky, "queue", queued.append)
        monkeypatch.setattr(eng.insights, "submit", lambda kind, r, **_: submitted.append((kind, r)))
        monkeypatch.setenv("ASTROFLOW_SKY", "1")
        monkeypatch.setenv("ASTROFLOW_DIAG", "1")
        eng.ctx.services["sites"].sync_nights("skytrace", {"2024-07-05": FERN})               # wie SkyTrace → Ereignis
        watch = c.app.state.modules["stacking"].site_watch
        watch._timer.join(5)
        assert sorted(queued) == sorted([a, old]) and submitted == [("diagnose", rid)]
        assert watch.last == {"sky": sorted([a, old]), "diagnose": [rid]}
