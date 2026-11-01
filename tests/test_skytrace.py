"""SkyTrace-Modul: Läufe lesen, Medien ausliefern (Range), Pfadschutz, Eingabeprüfung – ohne Analyse-Engine."""
import json

import pytest
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


def test_spuren_ueber_mehrere_aufnahmen_verketten():
    """M74 2022-08-07: ein langsames Objekt in vier Aufnahmen (gemessene Segmente) wird verkettet, der Meteor bleibt einzeln."""
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parents[1] / "astroflow" / "modules" / "skytrace" / "engine"))
    from motion_plausibility import narrow_field
    from trail_tracks import link_tracks

    meta = {"EXPOSURE": "10000", "INSTRUME": "stellina-a1b2c3", "FOCAL": "400", "PIXSZ": "2.4", "NAXIS1": "3072", "NAXIS2": "2080"}
    def row(index, t, *segments):
        return {"index": index, "timestamp": t, "meta": meta,
                "detections": [{"kind": "trail", "segment": s, "line_snr": 9.0, "model_hints": [{"kind": "meteor", "confidence": .8}]} for s in segments]}
    rows = [row(40, 1659913500, [[538, 112], [491, 193]]),
            row(176, 1659916067, [[1507, 463], [1418, 433]]), row(177, 1659916082, [[1412, 433], [1320, 415]]),
            row(179, 1659916112, [[996, 343], [858, 307]]), row(181, 1659916171, [[160, 129], [70, 105]]),
            row(217, 1659916700, [[1369, 143], [1278, 119]])]
    assert link_tracks(rows) == 1
    linked = [r["index"] for r in rows if r["detections"][0].get("track_id") == "L1"]
    assert linked == [176, 177, 179, 181]
    d = rows[1]["detections"][0]
    assert d["kind"] == "plane" and d["single_frame_kind"] == "trail" and d["linked_track"]["frames"] == [177, 178, 180, 182]
    assert rows[0]["detections"][0]["kind"] == "trail" and "track_id" not in rows[5]["detections"][0]
    # Teleskop (Stellina, ~1°) vs. Weitwinkel-Himmelskamera
    assert narrow_field(meta) and not narrow_field({"FOCAL": "4", "PIXSZ": "2.9", "NAXIS1": "1920", "NAXIS2": "1080"})
    assert not narrow_field({})


def test_lauf_loeschen(tmp_path, monkeypatch):
    monkeypatch.setenv("ASTROFLOW_MODULES", "stacking,skytrace")
    home = tmp_path / "skytrace"
    rid = _lauf(home)
    (home / "Ergebnisse" / "kein-lauf").mkdir()                     # fremder Ordner ohne Laufdateien
    monkeypatch.setenv("SKYTRACE_HOME", str(home))
    with TestClient(create_app(load_settings(tmp_path / "data"))) as c:
        assert c.delete("/api/skytrace/run", params={"id": "kein-lauf"}).status_code == 400
        assert c.delete("/api/skytrace/run", params={"id": "../skytrace"}).status_code == 400
        r = c.delete("/api/skytrace/run", params={"id": rid}).json()
        assert r["deleted"] == rid and r["bytes"] > 0 and not (home / "Ergebnisse" / rid).exists()
        assert [x["id"] for x in c.get("/api/skytrace/runs").json()] == []
        assert (home / "Ergebnisse" / "kein-lauf").exists()


def test_stellina_zeitstempel_ist_belichtungsende():
    """Stellina schreibt in DATE-OBS das Ende der Belichtung; Kataloge müssen davor suchen."""
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parents[1] / "astroflow" / "modules" / "skytrace" / "engine"))
    from motion_plausibility import exposure_info, exposure_start

    assert exposure_info({"INSTRUME": "stellina-a1b2c3", "EXPOSURE": "10000"})["reference"] == "end"
    assert exposure_info({"EXPTIME": "30"})["reference"] == "start"
    assert exposure_start({"timestamp": 1000, "exposure_seconds": 10, "time_reference": "end"}) == 990
    assert exposure_start({"timestamp": 1000, "exposure_seconds": 10, "time_reference": "start"}) == 1000
    # ältere Läufe ohne time_reference: über die Belichtungsquelle
    assert exposure_start({"timestamp": 1000, "exposure_seconds": 10, "plausibility": {"exposure_source": "Stellina EXPOSURE [ms]"}}) == 990


def test_satellitenbahn_trotz_uhrversatz_zuordnen(tmp_path, monkeypatch):
    """Spur, die die Bahn 8 s vor DATE-OBS kreuzt (Stellina: Belichtungsende), wird dem Satelliten zugeordnet –
    auch mit 10 s Uhrversatz; ohne Toleranz nicht (vorher lag die Prognose 1–6° neben fast jeder echten Spur)."""
    import datetime as dt
    import math
    import sys
    from pathlib import Path
    pytest.importorskip("skyfield")
    pytest.importorskip("sgp4")
    monkeypatch.setenv("SKYTRACE_HOME", str(tmp_path))
    sys.path.insert(0, str(Path(__file__).parents[1] / "astroflow" / "modules" / "skytrace" / "engine"))
    from sgp4.api import Satrec, WGS72, jday
    from sgp4.exporter import export_tle
    from skyfield.api import EarthSatellite, wgs84
    import catalog_matching as cm

    t0 = dt.datetime(2022, 6, 24, 22, 0, 0, tzinfo=dt.timezone.utc)
    jd, fr = jday(2022, 6, 24, 12, 0, 0)
    model = Satrec()
    model.sgp4init(WGS72, "i", 25544, jd + fr - 2433281.5, 1e-4, 0, 0, 5e-4, math.radians(80), math.radians(51.6),
                   math.radians(280), 15.5 * 2 * math.pi / 1440, math.radians(300))
    l1, l2 = export_tle(model)
    (tmp_path / "bahnen.tle").write_text("TESTSAT\n" + l1 + "\n" + l2 + "\n", encoding="utf-8")
    sat = EarthSatellite(l1, l2, "TESTSAT", cm.TS)
    below = wgs84.subpoint_of(sat.at(cm.TS.from_datetime(t0 - dt.timedelta(seconds=8))))
    site = {"lat": below.latitude.degrees, "lon": below.longitude.degrees, "alt_m": 0}
    observer = cm._observer(site)
    path = []
    for s in (-8.4, -7.6):
        ra, dec, _ = (sat - observer).at(cm.TS.from_datetime(t0 + dt.timedelta(seconds=s))).radec()
        path.append([ra.hours * 15, dec.degrees])
    cand = {"id": "K0001", "timestamp": t0.timestamp(), "timestamp_text": "x", "exposure_seconds": 10,
            "time_reference": "end", "world_path": path}
    settings = {"tle_file": str(tmp_path / "bahnen.tle")}

    r = cm.satellite_check(cand, site, settings)
    assert r["status"] == "compatible" and r["matches"][0]["name"] == "TESTSAT"
    m = r["matches"][0]
    assert m["time_offset_s"] == 0 and -9 < m["passage_seconds"][0] - 10 < m["passage_seconds"][1] - 10 < -7
    # Kamerauhr 10 s vor: Durchgang liegt außerhalb der Belichtung, aber innerhalb der Uhrtoleranz
    late = dict(cand, timestamp=t0.timestamp() + 10)
    m = cm.satellite_check(late, site, settings)["matches"][0]
    assert 6 < m["time_offset_s"] < 10
    assert cm.satellite_check(late, site, dict(settings, satellite_clock_slack_s=3))["status"] == "checked_no_match"
