"""Analyse-Dashboard (#/analyse): Aggregat-Endpunkt und Sammelauftrag „Alles analysieren“ – mit synthetischer Bibliothek,
Netzauswertungen durch Attrappen ersetzt (keine echten Abfragen)."""
import shutil
import threading
import time

import pytest
from fastapi.testclient import TestClient

from analysis_seed import OBJECTS, seed
from astroflow.core.config import load_settings
from astroflow.modules.stacking.db import DB
from astroflow.server import create_app


@pytest.fixture(scope="module")
def template(tmp_path_factory):
    """Einmal je Modul anlegen (Tutor-Nachtdaten kosten einige Sekunden), je Test kopieren."""
    root = tmp_path_factory.mktemp("dash") / "data"
    s = load_settings(root)
    ids = seed(DB(s.db_path), s.work_dir)
    return root, ids


@pytest.fixture
def app(template, tmp_path, monkeypatch):
    monkeypatch.setenv("ASTROFLOW_AUTO", "0")
    root, ids = template
    shutil.copytree(root, tmp_path / "data")
    s = load_settings(tmp_path / "data")
    with TestClient(create_app(s, only=("stacking",))) as c:
        yield c, ids, s


def _wait(c, cond, timeout=60):
    end = time.time() + timeout
    while time.time() < end:
        d = c.get("/api/stacking/analysis").json()
        if cond(d):
            return d
        time.sleep(0.1)
    raise AssertionError(d["job"])


def test_summary_aggregates(app):
    c, ids, _ = app
    d = c.get("/api/stacking/analysis").json()
    t = d["totals"]
    assert t["objects"] == len(OBJECTS)
    assert t["nights"] == len({o[4] for o in OBJECTS})
    assert abs(t["exposure"] - sum(o[6] for o in OBJECTS) * 3600) < 1
    # Rekorde = echte Maxima der gespeicherten Werte
    m5 = max(o[7] for o in OBJECTS if o[7])
    assert d["records"]["deepest"]["value"] == m5
    assert d["records"]["deepest"]["id"] == ids[[o[7] for o in OBJECTS].index(m5)]
    assert d["records"]["darkest"]["value"] == 21.6                      # M 13 (Dämmerung) hat keinen Ortswert
    # Ohne Gaia (conftest) ist die Tiefe für gelöste Bilder blockiert, ungelöste für Tiefe und Vergleich
    row = {r["id"]: r for r in d["rows"]}
    assert row[ids[6]]["blocked"] == {"depth": "nosolve", "compare": "nosolve"}
    assert row[ids[5]]["blocked"].get("depth") == "nogaia"
    assert row[ids[0]]["status"]["depth"] == "done" and row[ids[5]]["status"]["depth"] == "none"
    assert {b["code"] for b in d["blocked"]} >= {"nosolve", "nogaia"}
    # Kataloge, Befunde, Verläufe, Rangliste
    assert {31, 101, 13, 42, 57, 81} <= set(d["catalogs"]["messier"])
    assert "twilight" in {f["code"] for f in d["findings"]}                  # M 13 zur Sommersonnenwende
    assert len(d["depth_series"]) == sum(1 for o in OBJECTS if o[7])
    assert d["ranking"] == sorted(d["ranking"], key=lambda x: -x["m5_1h"])
    assert sum(d["score_hist"]) == len(OBJECTS)
    assert d["changes"] and all(ch["id"] in ids for ch in d["changes"])
    assert d["changes"][0].get("katalog") or d["changes"][0].get("kleinkoerper")   # erkannte Quellen zuerst


def test_filters_and_universe(app):
    c, ids, _ = app
    night = OBJECTS[1][4]
    d = c.get(f"/api/stacking/analysis?night={night}").json()
    assert [r["id"] for r in d["rows"]] == [ids[1]] and d["filtered"]
    assert d["totals"]["objects"] == 1
    assert c.get("/api/stacking/analysis?device=Andere").json()["totals"]["objects"] == 0
    assert c.get("/api/stacking/analysis?year=2026").json()["totals"]["objects"] == sum(1 for o in OBJECTS if o[4].startswith("2026"))
    u = c.get("/api/stacking/analysis/universe").json()
    assert u["points"] and {p["t"] for p in u["points"]} == {"s", "g"}
    assert all(-90 <= p["dec"] <= 90 and p["ly"] > 0 for p in u["points"])
    u1 = c.get(f"/api/stacking/analysis/universe?night={night}").json()
    assert {p["p"] for p in u1["points"]} == {ids[1]}


def test_batch_runs_one_network_job_at_a_time(app, monkeypatch):
    c, ids, s = app
    from astroflow.modules.stacking import depth, compare, dashboard, skyquality, tutor
    c.post("/api/stacking/storage", json={"gaia": True})
    active, peak, calls, lock = [0], [0], [], threading.Lock()

    def fake(kind):
        def run(*a, **k):
            with lock:
                active[0] += 1
                peak[0] = max(peak[0], active[0])
            time.sleep(0.05)
            with lock:
                active[0] -= 1
            calls.append(kind)
            return {"v": 1, "ok": True, "grenze": {"m5": 15.0}, "entfernung": {}, "galaxien_sichtbar": 0, "punkte": [], "kandidaten": []}
        return run
    monkeypatch.setattr(depth, "compute", fake("depth"))
    monkeypatch.setattr(compare, "compute", fake("compare"))
    monkeypatch.setattr(skyquality, "compute_project", lambda *a, **k: calls.append("sky") or {"nights": 0})
    monkeypatch.setattr(tutor, "build_report", lambda *a, **k: calls.append("tutor") or {})
    monkeypatch.setattr(dashboard, "GAP_S", 0.0)
    plan = c.post("/api/stacking/analysis/plan", json={"kinds": ["depth", "compare"], "scope": "missing"}).json()
    assert plan["per_kind"] == {"depth": 1, "compare": 1}                  # M 57: gelöst, aber noch nicht gemessen/verglichen
    assert plan["skipped"].get("nosolve") == 2                            # NGC 891 für beide Arten
    r = c.post("/api/stacking/analysis/run", json={"kinds": ["depth", "compare", "sky", "tutor"], "scope": "all"}).json()
    assert r["state"] == "running" and r["total"] > 0
    assert c.post("/api/stacking/analysis/run", json={}).status_code == 409
    act = c.get("/api/activity").json()
    assert any(a["key"].startswith("stacking:analysis") or a.get("href") == "#/analyse" for a in (act if isinstance(act, list) else act.get("items", [])))
    d = _wait(c, lambda d: d["job"]["state"] == "done")
    assert d["job"]["done"] == r["total"] and not d["job"]["errors"]
    assert peak[0] == 1                                                    # nie zwei Netzauswertungen gleichzeitig
    # Reihenfolge: lokal zuerst, Vergleich nach der Tiefe
    first = {k: calls.index(k) for k in ("tutor", "sky", "depth", "compare")}
    assert first["tutor"] < first["sky"] < first["depth"] < first["compare"]
    assert calls.count("depth") == len(OBJECTS) - 1                        # alle gelösten


def test_batch_pause_and_stop(app, monkeypatch):
    c, ids, _ = app
    from astroflow.modules.stacking import tutor
    gate = threading.Event()
    seen = []
    monkeypatch.setattr(tutor, "build_report", lambda *a, **k: (seen.append(1), gate.wait(5)))
    r = c.post("/api/stacking/analysis/run", json={"kinds": ["tutor"], "scope": "all"}).json()
    assert r["total"] == len(OBJECTS)                                       # alle haben eine Bildprüfung
    c.post("/api/stacking/analysis/pause", json={"paused": True})
    gate.set()
    d = _wait(c, lambda d: d["job"]["state"] == "paused" and d["job"]["done"] == 1)
    time.sleep(0.4)
    assert len(seen) == 1 and c.get("/api/stacking/analysis").json()["job"]["done"] == 1      # angehalten: nichts Neues
    c.post("/api/stacking/analysis/pause", json={"paused": False})
    c.post("/api/stacking/analysis/stop")
    d = _wait(c, lambda d: d["job"]["state"] in ("stopped", "done"))
    assert d["job"]["done"] < r["total"] or d["job"]["state"] == "done"


def test_develop_needs_solver(app):
    c, ids, _ = app
    res = c.post("/api/stacking/analysis/develop", json={})
    assert res.status_code in (200, 400)
    if res.status_code == 200:                                              # Solver vorhanden: Seed-Objekte haben keinen Stack
        assert res.json()["queued"] == 0


def test_empty_selection_runs_nothing(app):
    c, ids, _ = app
    assert c.post("/api/stacking/analysis/plan", json={"kinds": []}).json()["total"] == 0
    assert c.post("/api/stacking/analysis/run", json={"kinds": []}).status_code == 400
    assert c.get("/api/stacking/analysis").json()["job"]["state"] == "idle"


def test_year_filter_applies_to_sky_nights(app):
    """Objekt mit Himmelsmessungen aus zwei Jahren: der Jahresfilter darf die Nacht des anderen Jahres nicht als Rekord zeigen."""
    import json
    c, ids, s = app
    db = DB(s.db_path)
    db.x("INSERT INTO sky_nights VALUES(?,?,?,?,?,?,?,?)", (ids[0], "2026-03-01", "a", "Beispielwiese", "stellina|200", time.time(), 1,
                                                          json.dumps({"night": "2026-03-01", "sqm": 22.4, "seeing": 1.1})))
    assert c.get("/api/stacking/analysis").json()["records"]["darkest"]["value"] == 22.4
    d = c.get("/api/stacking/analysis?year=2025").json()
    assert d["records"]["darkest"]["value"] != 22.4 and d["records"]["seeing"]["value"] != 1.1
    assert all(n["night"].startswith("2025") for n in d["sky_series"])
