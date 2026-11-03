"""SkyTrace-Erfolge: Freischaltung, Stufen, rückwirkende Datierung, leerer Bestand, Zustandsdatei, API (erfundene Läufe)."""
import datetime as dt
import json

from fastapi.testclient import TestClient

from astroflow.core.config import load_settings
from astroflow.modules.skytrace import achievements as ach
from astroflow.server import create_app


def sat(name, norad, typ="Satellit", status="compatible"):
    return {"provider": "satellites", "status": status, "matches": [{"name": name, "norad_id": norad, "object_type": typ}]}


def cand(run, session, cid, catalogs=None, ts=None, **kw):
    return {"id": cid, "run": run, "session": session, "kind": kw.pop("kind", "trail"), "timestamp": ts, "catalogs": catalogs or [], **kw}


def sess(run, session, night, frames=120, excluded=0, n=0):
    return {"run": run, "session": session, "night": night, "frames": frames, "excluded": excluded, "candidate_count": n}


def data(sessions, cands):
    return {"sessions": sessions, "candidates": cands}


def unlocked(res):
    return set(res["when"])


def test_leerer_bestand():
    res = ach.compute(data([], []))
    assert res["when"] == {} and all(v == 0 for v in res["metrics"].values())


def test_ids_eindeutig_und_stufen_sinnvoll():
    assert len({a[0] for a in ach.ACHIEVEMENTS}) == len(ach.ACHIEVEMENTS)
    assert all(a[4] in ach.GROUPS and a[5] in ach.KEYS and a[6] >= 1 for a in ach.ACHIEVEMENTS)


def test_satelliten_stufen_und_datum_des_ausloesenden_laufs():
    cands, sessions = [], []
    for i in range(12):                                   # 12 Läufe, je 1 Starlink
        run = f"2026-10-{10 + i:02d}_20-00-00-000001"
        sessions.append(sess(run, f"S{i}", f"2026-10-{10 + i:02d}", n=1))
        cands.append(cand(run, f"S{i}", "K0001", [sat(f"STARLINK-{1000 + i}", 40000 + i)], ts=1.7e9 + i))
    res = ach.compute(data(sessions, cands))
    w = res["when"]
    assert w["sat_1"] == "2026-10-10T20:00:00" and w["starlink_1"] == w["sat_1"]
    assert w["sat_10"] == "2026-10-19T20:00:00" and w["starlink_10"] == w["sat_10"]      # zehnter Lauf
    assert "sat_50" not in w and res["metrics"]["sats"] == 12 and res["metrics"]["objects"] == 12
    assert "starlink_run_3" not in w and res["metrics"]["families"] == 1


def test_gruppen_nur_aus_katalogdaten():
    """Kein Satellit ohne passende Bahn: nicht prüfbar/kein Treffer zählt nie."""
    run = "2026-10-10_20-00-00-000001"
    c = [cand(run, "S", "K1", [sat("STARLINK-1", 1, status="checked_no_match")]), cand(run, "S", "K2", [sat("STARLINK-2", 2, status="needs_archive")])]
    res = ach.compute(data([sess(run, "S", "2026-10-10", n=2)], c))
    assert res["metrics"]["sats"] == 0 and "sat_1" not in res["when"] and res["metrics"]["cands"] == 2


def test_schrott_stationen_und_familien():
    run = "2026-10-10_20-00-00-000001"
    cs = [cand(run, "S", "K1", [sat("SL-14 R/B", 1, "Raketenstufe")]), cand(run, "S", "K2", [sat("COSMOS 2251 DEB", 2, "Trümmerteil")]),
          cand(run, "S", "K3", [sat("ISS (ZARYA)", 25544)]), cand(run, "S", "K4", [sat("IRIDIUM 914", 3)]),
          cand(run, "S", "K5", [sat("GLOBALSTAR M073", 4)]), cand(run, "S", "K6", [sat("CSS (TIANHE)", 48274)]), cand(run, "S", "K7", [sat("HST", 20580)])]
    m = ach.compute(data([sess(run, "S", "2026-10-10", n=7)], cs))
    assert {"rb_1", "deb_1", "iss_1", "css_1", "hst_1", "families_3"} <= unlocked(m)
    assert m["metrics"]["scrap"] == 2 and m["metrics"]["families"] == 5        # R/B und DEB zählen nicht als Familie


def test_starlink_zug_und_lauf():
    run = "2026-10-10_20-00-00-000001"
    base = 1.7e9
    cs = [cand(run, "S", f"K{i}", [sat(f"STARLINK-{i}", 50000 + i)], ts=base + 40 * i) for i in range(5)]
    cs.append(cand(run, "S", "K9", [sat("STARLINK-99", 50099)], ts=base + 3600))            # weit weg: kein Teil des Zuges
    m = ach.compute(data([sess(run, "S", "2026-10-10", n=6)], cs))
    assert m["metrics"]["burst"] == 5 and m["metrics"]["starlink_run"] == 6
    assert {"burst_3", "burst_5", "starlink_run_3", "starlink_run_6"} <= unlocked(m) and "starlink_run_10" not in m["when"]


def test_meteor_flugzeug_verwechslung():
    run = "2026-10-10_20-00-00-000001"
    plane = {"provider": "aircraft", "status": "compatible", "matches": [{"name": "x"}]}
    met = {"provider": "meteors", "status": "compatible", "matches": [{"name": "x"}]}
    cs = [cand(run, "S", "K1", [plane], kind="meteor"),                                   # Vorschlag Meteor, Katalog Flugzeug
          cand(run, "S", "K2", [sat("STARLINK-1", 1)], kind="meteor"),                    # Vorschlag Meteor, Katalog Satellit
          cand(run, "S", "K3", [met], kind="trail"),
          cand(run, "S", "K4", kind="trail", linked_track={"count": 4, "reason": "gleichmäßig"})]
    m = ach.compute(data([sess(run, "S", "2026-10-10", n=4)], cs))
    assert {"meteor_plane_1", "meteor_sat_1", "meteor_1", "meteor_cat_1", "plane_1"} <= unlocked(m)
    assert m["metrics"]["planes"] == 2 and m["metrics"]["meteor_other"] == 2


def test_modell_meteor_im_teleskopbild_ist_nur_hinweis():
    c = cand("r", "S", "K1", kind="trail", model_scope="narrow_field", model_hints=[{"kind": "meteor", "confidence": 0.9}])
    kind, m, proposal = ach.classify(c)
    assert kind == "trail" and m is None and proposal == ""


def test_verkehrsstau_saubere_naechte_nachtschicht():
    r1, r2, r3 = ("2026-10-1%d_20-00-00-000001" % i for i in (1, 2, 3))
    late = dt.datetime(2026, 10, 12, 3, 30).timestamp()                                    # Ortszeit, wie ausgewertet
    early = dt.datetime(2026, 10, 12, 22, 0).timestamp()
    sessions = [sess(r1, "A", "2026-10-11", frames=300, n=0), sess(r2, "B", "2026-10-12", frames=99, n=0), sess(r3, "C", "2026-10-13", n=5)]
    cs = [cand(r3, "C", f"K{i}", ts=late if i == 0 else early) for i in range(5)]
    m = ach.compute(data(sessions, cs))
    assert m["metrics"]["clean"] == 1 and m["when"]["clean_1"] == "2026-10-11T20:00:00"    # 99 Aufnahmen reichen nicht
    assert m["when"]["traffic_5"] == "2026-10-13T20:00:00" and m["metrics"]["late"] == 1 and "late_1" in m["when"]


def test_beobachtungen_nacht_aufnahmen_ausgelassen():
    sessions = [sess(f"2026-10-{i + 1:02d}_20-00-00-000001", f"S{i}", f"2026-10-{i + 1:02d}", frames=1100, excluded=15) for i in range(10)]
    m = ach.compute(data(sessions, []))
    assert {"runs_1", "runs_10", "nights_10", "frames_1k", "frames_10k", "excl_100"} <= unlocked(m)
    assert m["when"]["frames_1k"] == "2026-10-01T20:00:00" and m["when"]["frames_10k"] == "2026-10-10T20:00:00"


def test_wiederholte_objekte_ueber_naechte():
    sessions, cs = [], []
    for i in range(2):
        run = f"2026-10-0{i + 1}_20-00-00-000001"
        sessions.append(sess(run, "S%d" % i, f"2026-10-0{i + 1}", n=1))
        cs.append(cand(run, "S%d" % i, "K1", [sat("COSMOS 1234", 12345)]))
    m = ach.compute(data(sessions, cs))
    assert m["metrics"]["repeat"] == 2 and "repeat_2" in m["when"] and "repeat_4" not in m["when"]


def test_zustand_rueckwirkend_still_danach_neu_markiert(tmp_path):
    run = "2026-10-10_20-00-00-000001"
    state = {"cands": [cand(run, "S", "K1", [sat("STARLINK-1", 1)])]}
    sessions = [sess(run, "S", "2026-10-10", n=1)]
    a = ach.Achievements(tmp_path / "erfolge.json", lambda: data(sessions, state["cands"]))
    o = a.overview()
    first = {i["id"]: i for i in o["achievements"] if i["unlocked"]}
    assert {"sat_1", "starlink_1", "runs_1"} <= set(first) and first["sat_1"]["unlocked"] == "2026-10-10T20:00:00"
    assert o["new"] == [] and a.new() == []                                  # Altbestand: rückwirkend, keine Feier
    # zweiter Lauf: ISS dazu → neu und ungesehen
    run2 = "2026-10-20_20-00-00-000001"
    sessions.append(sess(run2, "T", "2026-10-20", n=1))
    state["cands"].append(cand(run2, "T", "K1", [sat("ISS (ZARYA)", 25544)]))
    o = a.overview()
    assert "iss_1" in o["new"] and [n["id"] for n in a.new()] == ["iss_1"]
    assert next(i for i in o["achievements"] if i["id"] == "iss_1")["unlocked"] == "2026-10-20T20:00:00"
    assert next(i for i in o["achievements"] if i["id"] == "sat_1")["unlocked"] == "2026-10-10T20:00:00"    # alter Zeitpunkt bleibt
    a.mark_seen()
    assert a.overview()["new"] == []
    # Lauf gelöscht → Erfolg bleibt, Fortschritt gedeckelt
    sessions.pop(), state["cands"].pop()
    o = a.overview()
    iss = next(i for i in o["achievements"] if i["id"] == "iss_1")
    assert iss["unlocked"] and iss["progress"]["cur"] == iss["progress"]["goal"]


def test_fortschritt_gesperrter_erfolge():
    run = "2026-10-10_20-00-00-000001"
    cs = [cand(run, "S", f"K{i}", [sat(f"STARLINK-{i}", i)]) for i in range(37)]
    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as t:
        o = ach.Achievements(Path(t) / "e.json", lambda: data([sess(run, "S", "2026-10-10", n=37)], cs)).overview()
    s100 = next(i for i in o["achievements"] if i["id"] == "starlink_100")
    assert s100["unlocked"] is None and s100["progress"] == {"cur": 37, "goal": 100} and s100["joke"] == ""      # Spruch erst nach Freischaltung
    s10 = next(i for i in o["achievements"] if i["id"] == "starlink_10")
    assert s10["unlocked"] and s10["joke"]


def test_api_mit_erfundenen_laeufen(tmp_path, monkeypatch):
    monkeypatch.setenv("ASTROFLOW_MODULES", "stacking,skytrace")
    home = tmp_path / "skytrace"
    run = home / "Ergebnisse" / "2026-10-27_20-01-34-000001"
    (run / "M31").mkdir(parents=True)
    cands = [{"id": "K0001", "session": "2022-06-11_20-58-48_observation_M31", "kind": "trail", "timestamp": 1654982580.0,
              "catalogs": [sat("STARLINK-1484", 44933)]}]
    (run / "M31" / "kandidaten.json").write_text(json.dumps(cands), encoding="utf-8")
    (run / "videos.json").write_text(json.dumps([{"session": "2022-06-11_20-58-48_observation_M31", "frames": 150, "excluded": 3, "state": "Fertig",
                                                  "candidates_file": "M31/kandidaten.json"}]), encoding="utf-8")
    monkeypatch.setenv("SKYTRACE_HOME", str(home))
    with TestClient(create_app(load_settings(tmp_path / "data"))) as c:
        d = c.get("/api/skytrace/achievements").json()
        got = {a["id"]: a for a in d["achievements"]}
        assert got["sat_1"]["unlocked"] == "2026-10-27T20:01:34" and got["starlink_1"]["unlocked"] and not got["sat_10"]["unlocked"]
        assert d["unlocked"] == sum(bool(a["unlocked"]) for a in d["achievements"]) and d["metrics"]["sats"] == 1
        assert (home / "erfolge.json").exists()
        assert c.post("/api/skytrace/achievements/seen").json() == {"ok": True}
        assert "achievements_new" in c.get("/api/status").json()["modules"]["skytrace"]
