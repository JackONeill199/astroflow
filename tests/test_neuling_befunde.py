"""Befunde aus dem Neulingstest in der Windows-Sandbox (18.11.2026): Einrichtung merkt sich den Stand, Beenden fragt bei
Downloads nach, kein zweiter Tab nach dem Neustart, GraXpert nach dem ersten Bild, verständlicher Komponentenfortschritt,
kein Abgleichshinweis ohne SkyTrace-Analysen, sprechender JPEG-Name."""
import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from astroflow.core import components as comp, launcher
from astroflow.core.config import load_settings
from astroflow.server import create_app


@pytest.fixture
def home(tmp_path, monkeypatch):
    for k in ("ASTROFLOW_DATA", "ASTROFLOW_PORT", "ASTROFLOW_LAUNCHER"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("ASTROFLOW_HOME", str(tmp_path / "app"))
    return tmp_path / "app"


def _client(data: Path, monkeypatch, launched=True):
    if launched:
        monkeypatch.setenv("ASTROFLOW_LAUNCHER", "1")
    app = create_app(load_settings(data), only=("stacking",))
    return app, TestClient(app)


def _project(eng, name="M31 Andromedagalaxie", options=None, stats=None, files=None):
    pid = eng.db.x("INSERT INTO projects(key,name,kind,options,created) VALUES(?,?,?,?,?)",
                   (name.lower(), name, "galaxy", json.dumps(options or {}), time.time()))
    rid = eng.db.x("INSERT INTO results(project_id,created,n_frames,exposure,files,stats) VALUES(?,?,?,?,?,?)",
                   (pid, time.time(), 30, 300.0, json.dumps(files or {}), json.dumps(stats or {})))
    return pid, rid


def test_einrichtung_merkt_erstes_bild_und_orte(home, monkeypatch):
    app, c = _client(home / "Daten", monkeypatch)
    s = c.get("/api/setup").json()
    assert s["pending"] and s["pictures"] == 0 and s["sites"] == 0
    assert c.post("/api/sites", json={"list": [{"name": "Testort", "lat": 50.1, "lon": 10.2, "alt_m": 0, "accuracy_m": 2000}]}).status_code == 200
    assert c.get("/api/setup").json()["sites"] == 1
    _project(app.state.modules["stacking"].eng)
    s = c.get("/api/setup").json()
    # erstes Bild fertig → beim nächsten Start nicht wieder die Einrichtung, auch ohne „Abschließen“
    assert s["pictures"] == 1 and not s["pending"] and not s["done"]
    assert c.get("/api/app").json()["setup"]["pending"] is False


def test_downloads_zaehlen_als_auftrag_und_tabs(home, monkeypatch):
    app, c = _client(home / "Daten", monkeypatch)
    assert c.get("/api/tabs").json() == {"visible_age": None}
    app.state.setup.tasks["graxpert"] = {"state": "running", "progress": 0.3, "detail": "Lade GraXpert …", "started": time.time()}
    time.sleep(1.1)                                         # Status ist 1 s zwischengespeichert (pollcache)
    assert c.get("/api/status?tab=1&visible=0").json()["busy"] >= 1
    assert c.get("/api/tabs").json()["visible_age"] is None              # Tab im Hintergrund zählt nicht
    c.get("/api/status?tab=1&visible=1")
    assert c.get("/api/tabs").json()["visible_age"] is not None


def test_tab_am_starter_nur_echtes_loopback(monkeypatch):
    from astroflow.core import security
    monkeypatch.setenv("ASTROFLOW_TRUSTED_NETS", "172.16.0.0/12")
    assert security.is_loopback("127.0.0.1", strict=True) and not security.is_loopback("172.17.0.5", strict=True)
    assert security.is_loopback("172.17.0.5")                # Docker-Netz gilt sonst weiter als vertrauenswürdig


def test_starter_ohne_server_oeffnet_wie_bisher(monkeypatch):
    # kein Server (bzw. älterer ohne /api/tabs): sofort „nicht verbunden“ → Browser öffnen
    assert launcher.tab_reconnected(1, wait=0.5) is False


def test_beenden_fragt_bei_download(monkeypatch):
    from astroflow.core import tray
    t = tray.Tray(8799)
    asked, posted = [], []
    act = [{"key": "setup:graxpert", "title": "GraXpert", "state": "running", "progress": 0.36}]

    def refresh():
        t.status, t.activity = {"busy": 1}, act
        return True
    monkeypatch.setattr(t, "refresh", refresh)
    monkeypatch.setattr(t, "post", lambda path, body, timeout=10: posted.append(path))
    monkeypatch.setattr(t, "close", lambda: None)
    monkeypatch.setattr(tray, "ask", lambda text: asked.append(text) or False)
    t.quit()
    assert asked and "GraXpert" in asked[0] and "Downloads" in asked[0] and posted == []
    monkeypatch.setattr(tray, "ask", lambda text: True)
    t.quit()
    assert posted == ["/api/system/shutdown"]


def test_beenden_bei_fehlender_aktivitaet(monkeypatch):
    from astroflow.core import tray
    t = tray.Tray(8799)
    posted, closed = [], []

    def get(path, timeout=5):
        if "activity" in path:
            raise OSError("kaputt")
        return {"busy": 0}
    monkeypatch.setattr(t, "get", get)
    monkeypatch.setattr(t, "post", lambda path, body, timeout=10: posted.append(path))
    monkeypatch.setattr(t, "close", lambda: closed.append(1))
    t.quit()
    assert posted == ["/api/system/shutdown"] and closed        # Server lebt: Shutdown wird trotzdem geschickt


def test_pip_fortschritt_verstaendlich(home, monkeypatch):
    lines = ["Collecting torch==2.5.1", "Collecting scipy", "Downloading https://download.pytorch.org/whl/cpu/torch-2.5.1%2Bcpu-cp312-cp312-win_amd64.whl (204.3 MB)",
             "Downloading scipy-1.16.1-cp312-cp312-win_amd64.whl.metadata (60 kB)",
             "Downloading scipy-1.16.1-cp312-cp312-win_amd64.whl (38.5 MB)", "Installing collected packages: scipy, torch", "Successfully installed"]

    class Proc:
        stdout = iter(lines)

        def wait(self):
            return 0
    monkeypatch.setattr(comp, "packages_dir", lambda: None)
    monkeypatch.setattr(comp.subprocess, "Popen", lambda *a, **k: Proc())
    seen = []
    comp.pip(["x"], lambda f, d: seen.append((f, d)), 0.02, 0.97, 4, "Engine")
    texts = [d for _, d in seen]
    assert any("Schritt 2 von 3" in d and "bisher 243 MB" in d and "scipy 1.16.1" in d for d in texts)
    assert any("Schritt 3 von 3" in d for d in texts) and not any(".whl" in d for d in texts)
    fs = [f for f, _ in seen]
    assert fs == sorted(fs) and max(fs) < 0.97 and all("seit" in d for d in texts)
    assert comp._wheel_name("polars_runtime_32-1.33.1-cp39-abi3-win_amd64.whl") == "polars_runtime_32 1.33.1"
    assert comp.mb_of(0.46, 160) == "74 von ~160 MB" and comp._size_mb("Downloading a.whl (845 kB)") == pytest.approx(0.845)


def test_graxpert_nach_erstem_bild(home, monkeypatch):
    from astroflow.modules.stacking import graxpert
    app, c = _client(home / "Daten", monkeypatch)
    eng = app.state.modules["stacking"].eng
    pid, _ = _project(eng)
    _project(eng, "Mit KI", stats={"graxpert": {"bge": True, "denoise": 0.5}})
    _project(eng, "Fertiges Bild", options={"input_type": "picture"})
    _project(eng, "C/2023 A3")                                # Komet automatisch erkannt (ohne Option)
    _project(eng, "M42 Orionnebel", options={"comet_mode": "on"})
    pid2, _ = _project(eng, "M33 Dreiecksgalaxie", options={"comet_mode": "auto"})   # gespeicherte Optionen schließen nicht aus
    assert sorted(p["id"] for p in eng.without_ai()) == [pid, pid2]
    eng.db.x("DELETE FROM results WHERE project_id=?", (pid2,))
    assert c.get(f"/api/stacking/projects/{pid}").json()["ai_offer"] is False          # GraXpert fehlt (Tests: abgeschaltet)
    monkeypatch.setattr(graxpert, "graxpert_path", lambda: "GraXpert.exe")
    queued = lambda: [(r["project_id"], json.loads(r["options"])) for r in eng.db.q("SELECT * FROM jobs WHERE state='queued' ORDER BY id")]
    assert c.get(f"/api/stacking/projects/{pid}").json()["ai_offer"] is True
    msg = app.state.setup._redevelop_with_ai()
    assert queued() == [(pid, {"mode": "develop"})] and "neu entwickelt" in msg
    # läuft für das Objekt schon ein Auftrag (Entwickeln bzw. Plate-Solving), wird die KI-Entwicklung vorgemerkt statt verschluckt
    monkeypatch.setattr(eng, "pl", SimpleNamespace(develop=lambda *a, **k: 1, solve_project=lambda *a, **k: {"moved": {}},
                                                   begin_live=lambda *a, **k: None))
    monkeypatch.setattr(eng, "insights", SimpleNamespace(after_develop=lambda rid: None))
    monkeypatch.setattr("astroflow.modules.stacking.skyquality.auto_enabled", lambda: False)
    for opts, run in (({"mode": "develop"}, eng._develop_job), ({"action": "solve"}, eng._stack_job)):
        eng.db.x("DELETE FROM jobs")
        jid = eng.db.x("INSERT INTO jobs(project_id,state,created,options) VALUES(?,?,?,?)", (pid, "running", time.time(), json.dumps(opts)))
        assert eng.redevelop_with_ai() == ["M31 Andromedagalaxie"] and queued() == [] and pid in eng.ai_after
        run(jid)                                             # Auftrag endet, Bild weiter ohne KI → Folgeauftrag mit KI
        assert queued() == [(pid, {"mode": "develop"})] and pid not in eng.ai_after
    # zwischen Abschluss und Folgeauftrag kommt ein anderer Auftrag: Vormerkung bleibt bestehen
    eng.db.x("DELETE FROM jobs")
    eng.db.x("INSERT INTO jobs(project_id,state,created,options) VALUES(?,?,?,?)", (pid, "queued", time.time(), '{"action": "solve"}'))
    eng._ai_followup(pid)
    assert pid in eng.ai_after



def test_graxpert_waehrend_allererster_entwicklung(home, monkeypatch):
    """Review PR #166: GraXpert wird genau dann fertig, wenn ein Objekt zum allerersten Mal entwickelt wird (noch kein
    Ergebnis) – auch dieses erste Bild muss nach dem Auftrag mit KI neu entwickelt werden; mit KI entstanden: nichts."""
    from astroflow.modules.stacking import graxpert
    app, c = _client(home / "Daten", monkeypatch)
    eng = app.state.modules["stacking"].eng
    monkeypatch.setattr(graxpert, "graxpert_path", lambda: "GraXpert.exe")
    monkeypatch.setattr(eng, "insights", SimpleNamespace(after_develop=lambda rid: None))
    monkeypatch.setattr("astroflow.modules.stacking.skyquality.auto_enabled", lambda: False)
    queued = lambda: [(r["project_id"], json.loads(r["options"])) for r in eng.db.q("SELECT * FROM jobs WHERE state='queued' ORDER BY id")]
    for stats, followup in (({}, True), ({"graxpert": {"bge": True}}, False)):
        for t in ("jobs", "results", "projects"):
            eng.db.x(f"DELETE FROM {t}")
        eng.ai_after.clear()
        pid = eng.db.x("INSERT INTO projects(key,name,kind,options,created) VALUES(?,?,?,?,?)",
                       (f"neu{followup}", "M 81", "galaxy", "{}", time.time()))
        jid = eng.db.x("INSERT INTO jobs(project_id,state,created,options) VALUES(?,?,?,?)", (pid, "running", time.time(), "{}"))
        assert eng.redevelop_with_ai() == [] and pid in eng.ai_after            # kein Ergebnis, trotzdem vorgemerkt

        def finish(p, *a, _stats=stats, **k):                                    # erstes Bild entsteht (mit/ohne KI)
            return eng.db.x("INSERT INTO results(project_id,created,n_frames,exposure,files,stats) VALUES(?,?,?,?,?,?)",
                            (p, time.time(), 30, 300.0, "{}", json.dumps(_stats)))
        monkeypatch.setattr(eng, "pl", SimpleNamespace(finish=finish))
        eng._develop_job(jid)
        assert queued() == ([(pid, {"mode": "develop"})] if followup else []) and pid not in eng.ai_after

def test_jpeg_download_heisst_wie_das_bild(home, monkeypatch, tmp_path):
    app, c = _client(home / "Daten", monkeypatch, launched=False)
    jpg = tmp_path / "20261118-200500_24f.jpg"
    jpg.write_bytes(b"\xff\xd8\xff\xd9")
    _, rid = _project(app.state.modules["stacking"].eng, files={"jpg": str(jpg)})
    r = c.get(f"/api/stacking/results/{rid}/jpg")
    assert r.status_code == 200 and r.headers["content-disposition"].startswith("inline")
    import datetime as dt
    day = dt.date.today().isoformat()
    assert f'filename="M31_Andromedagalaxie_{day}.jpg"' in r.headers["content-disposition"]
    from astroflow.modules.stacking.api import download_name
    assert download_name("C/2023 A3 (Tsuchinshan)", None, "x.FITS", "_graxpert") == "C_2023_A3_Tsuchinshan_graxpert.fits"


def test_kein_abgleichshinweis_ohne_analysen(tmp_path):
    from astroflow.core.store import SettingsStore
    from astroflow.modules.skytrace.rematch import SiteJobs
    store = SettingsStore(tmp_path / "settings.json")
    results = tmp_path / "Ergebnisse"
    jobs = SiteJobs(SimpleNamespace(ctx=SimpleNamespace(store=store), ctl=SimpleNamespace(results=results)))
    store.update("sites", {"list": [{"id": "a", "name": "A", "lat": 50, "lon": 10}], "default": "a", "periods": []})
    assert jobs.sites_changed() is False
    store.update("sites", {"list": [{"id": "a", "name": "A", "lat": 51, "lon": 10}], "default": "a", "periods": []})
    assert jobs.sites_changed() is False                     # erster Ort, noch keine Analyse: kein „neu abgleichen?“
    run = results / "2026-11-18_20-00-00-000001"
    run.mkdir(parents=True)
    (run / "videos.json").write_text("[]", encoding="utf-8")
    store.update("sites", {"list": [{"id": "a", "name": "A", "lat": 52, "lon": 10}], "default": "a", "periods": []})
    assert jobs.sites_changed() is True
    gone = SiteJobs(SimpleNamespace(ctx=SimpleNamespace(store=store), ctl=SimpleNamespace(results=tmp_path / "weg" / "Ergebnisse")))
    store.update("sites", {"list": [{"id": "a", "name": "A", "lat": 53, "lon": 10}], "default": "a", "periods": []})
    assert gone.sites_changed() is False                     # Laufwerk getrennt: nichts melden …
    assert jobs.sites_changed() is True                      # … und den alten Stand nicht überschreiben
    link = tmp_path / "Link-Ergebnisse"
    try:
        link.symlink_to(tmp_path / "Laufwerk-weg" / "Ergebnisse", target_is_directory=True)
    except OSError:
        return                                               # Windows ohne Symlink-Recht: Fall nicht prüfbar
    via_link = SiteJobs(SimpleNamespace(ctx=SimpleNamespace(store=store), ctl=SimpleNamespace(results=link)))
    assert via_link._has_runs() is None and via_link.sites_changed() is False
