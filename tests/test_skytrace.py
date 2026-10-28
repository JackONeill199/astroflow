"""SkyTrace-Modul: Läufe lesen, Medien ausliefern (Range), Pfadschutz, Eingabeprüfung – ohne Analyse-Engine."""
import json

from fastapi.testclient import TestClient

from astroflow.core.config import load_settings
from astroflow.server import create_app


def _lauf(home):
    run = home / "Ergebnisse" / "2026-10-27_20-01-34-000001"
    obs = run / "M103"
    (obs / "kandidaten" / "K0001").mkdir(parents=True)
    (run / "bericht.html").write_text("<html>Bericht</html>", encoding="utf-8")
    (obs / "live.jpg").write_bytes(b"\xff\xd8bild")
    (obs / "ereignisse.mp4").write_bytes(bytes(range(256)) * 40)
    (obs / "kandidaten" / "K0001" / "a.png").write_bytes(b"png")
    cands = [{"id": "K0001", "session": "2026_observation_M103", "kind": "trail", "initial_frame": 0,
              "frames": [{"image": "M103/kandidaten/K0001/a.png", "index": 99, "hit": True}]}]
    (obs / "kandidaten.json").write_text(json.dumps(cands), encoding="utf-8")
    (run / "videos.json").write_text(json.dumps([{"session": "2026_observation_M103", "video": "M103/ereignisse.mp4", "frames": 120,
                                                  "excluded": 4, "hits": 2, "candidate_count": 1, "state": "Fertig",
                                                  "candidates_file": "M103/kandidaten.json"}]), encoding="utf-8")
    return run.name


def test_laeufe_und_medien(tmp_path, monkeypatch):
    monkeypatch.setenv("ASTROFLOW_MODULES", "stacking,skytrace")
    home = tmp_path / "skytrace"
    rid = _lauf(home)
    s = load_settings(tmp_path / "d")
    from astroflow.core.store import SettingsStore
    SettingsStore(s.data_dir / "settings.json").update("skytrace", {"home": str(home)})
    with TestClient(create_app(s)) as c:
        runs = c.get("/api/skytrace/runs").json()
        assert runs[0]["id"] == rid and runs[0]["candidates"] == 1 and runs[0]["frames"] == 120
        assert runs[0]["preview"].endswith("M103/live.jpg")
        d = c.get("/api/skytrace/run", params={"id": rid}).json()
        assert d["candidates"][0]["key"] == "M103/K0001"
        frame_url = d["candidates"][0]["frames"][0]["url"]
        assert c.get(frame_url).content == b"png"
        assert c.get(d["report_url"]).headers["content-security-policy"].startswith("default-src 'self'")
        v = c.get(d["sessions"][0]["video_url"], headers={"range": "bytes=10-19"})
        assert v.status_code == 206 and v.content == bytes(range(10, 20))
        # kein Zugriff außerhalb des Ergebnisordners, keine fremden Läufe
        assert c.get("/api/skytrace/media/..%2F..%2Fsettings.json").status_code in (400, 404)
        assert c.get("/api/skytrace/run", params={"id": "../d"}).status_code == 400
        # Eingaben werden geprüft, bevor ein Prozess startet
        r = c.post("/api/skytrace/start", json={"source": str(tmp_path / "fehlt")})
        assert r.status_code == 400 and "existiert nicht" in r.json()["detail"]
        r = c.post("/api/skytrace/start", json={"source": str(home), "fps": 500})
        assert r.status_code == 400
        assert c.get("/api/skytrace/state").json()["running"] is False
        acts = [a for a in c.get("/api/activity").json() if a["module"] == "skytrace"]
        assert acts[0]["href"] == f"#/skytrace/{rid}" and acts[0]["state"] == "done"
        obs = c.get("/api/skytrace/observations", params={"source": str(home / "Ergebnisse" / rid)}).json()
        assert obs["observations"] == ["M103"]
