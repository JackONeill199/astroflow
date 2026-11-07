"""SkyTrace: Satellitenhelligkeit (mag) und Satelliten je Belichtungsstunde – synthetische Läufe, erfundener Ort, kein Netz."""
import datetime as dt
import json

import pytest
from fastapi.testclient import TestClient

from astroflow.core.config import load_settings
from astroflow.modules.skytrace import satellites as sat
from astroflow.server import create_app

OFFSETS = [float(o) for o in range(-12, 13)]


def _ts(y, mo, d, h, mi=0):
    return dt.datetime(y, mo, d, h, mi, tzinfo=dt.timezone.utc).timestamp()


def _cand(cid, ts, name, norad, profile=True, passage=(1.0, 3.0), session="2022-08-10_observation_Beispielfeld"):
    c = {"id": cid, "session": session, "kind": "trail", "timestamp": ts, "exposure_seconds": 10.0, "time_reference": "end",
         "world_path": [[120.0, 30.0], [120.1, 30.0]], "plausibility": {"original_size": ["3072", "2080"], "analysis_size": [1536, 1040]},
         "catalogs": [{"provider": "satellites", "status": "compatible",
                       "matches": [{"name": name, "norad_id": norad, "object_type": "Satellit", "passage_seconds": list(passage)}]}]}
    if profile:       # 5 Pixel à 10 ADU = 50 ADU·px (Mittelwert nach 2×2-Binning), Rest 0
        c["evidence"] = {"length_pixels": 100.0, "profile": {"offset_pixels": OFFSETS, "contrast_adu": [10.0 if abs(o) <= 2 else 0.0 for o in OFFSETS]}}
    return c


def test_astronomie_und_hilfen():
    # Polarstern steht in Höhe der Breite; Sonne mittags am Äquator im Zenit, um Mitternacht tief unten
    assert sat.altitude(_ts(2022, 8, 10, 22), 37.95, 89.26, 47.0, 8.0) == pytest.approx(47.7, abs=1.0)
    assert float(sat.sun_altitude(_ts(2022, 3, 20, 12), 0.0, 0.0)) > 85
    assert float(sat.sun_altitude(_ts(2022, 3, 20, 0), 0.0, 0.0)) < -85
    assert float(sat.airmass(90)) == pytest.approx(1.0, abs=0.01) and float(sat.airmass(30)) == pytest.approx(2.0, abs=0.05)
    assert [sat.family(n) for n in ("STARLINK-1007", "ONEWEB-0126", "COSMOS 2522 [GLONASS-M]", None)] == ["starlink", "oneweb", "other", "other"]


def test_fluss_und_helligkeit():
    ph = sat.phot_of(_cand("K1", 1.0, "X", 1), {"passage_seconds": [1.0, 3.0]})
    assert ph["s"] == 50 and ph["L"] == 100 and ph["t"] == 2 and ph["b"] == 2.0
    assert sat.flux_rate(ph) == pytest.approx(50 * 4 * 100 / 2)                     # 10 000 ADU/s
    ph = dict(ph, ra=None, dec=None)                                                # ohne Ort: Luftmasse 1,4
    assert sat.magnitude(ph, None, 22.0) == pytest.approx(22.0 - 0.2 * 1.4 - 10.0)
    # doppelt so viel Fluss = 0,753 mag heller
    assert sat.magnitude(dict(ph, s=100), None, 22.0) == pytest.approx(sat.magnitude(ph, None, 22.0) - 0.753, abs=0.001)
    # unbrauchbare Messwerte: kein Profil, Durchgang länger als die Belichtung, Durchgangszeit fehlt, kein positiver Fluss
    assert sat.phot_of(_cand("K1", 1.0, "X", 1, profile=False), {"passage_seconds": [1, 3]}) is None
    assert sat.phot_of(_cand("K1", 1.0, "X", 1), {"passage_seconds": [0, 20]}) is None
    assert sat.phot_of(_cand("K1", 1.0, "X", 1), {}) is None
    flat = _cand("K1", 1.0, "X", 1)
    flat["evidence"]["profile"]["contrast_adu"] = [0.0] * 25
    assert sat.phot_of(flat, {"passage_seconds": [1, 3]}) is None


def test_nullpunkt_rangfolge():
    z = {"nights": {"2022-08-10|stellina-t|200.0": 21.9}, "devices": {"stellina-t|200.0": 22.1}}
    assert sat.pick_zp(z, "2022-08-10", "stellina-t|200.0") == (21.9, "night")
    assert sat.pick_zp(z, "2022-08-11", "stellina-t|200.0") == (22.1, "device")
    assert sat.pick_zp(None, "2022-08-11", "stellina-t|200.0") == (sat.NOMINAL_ZP["stellina"], "estimate")
    assert sat.pick_zp(None, "2022-08-11", "unbekannt|1.0") == (None, None)


def _lauf(home, rid="2026-11-01_20-00-00-000001"):
    run = home / "Ergebnisse" / rid
    obs = run / "Beispielfeld-ab12"
    obs.mkdir(parents=True)
    session = "2022-08-10_observation_Beispielfeld"
    night = [_ts(2022, 8, 10, 23) + 15 * i for i in range(120)]       # Nacht: Sonne weit unter −12°
    day = [_ts(2022, 8, 10, 14) + 15 * i for i in range(30)]          # mitten am Tag (Fehlzeit, darf nicht zählen)
    meta = {"INSTRUME": "stellina-test", "GAIN": "200", "exposure_seconds": 10.0}
    (obs / "frames.jsonl").write_text("\n".join(json.dumps({"file": f"/nirgends/img-{i:04d}.fits", "index": i, "timestamp": t, "meta": meta})
                                                  for i, t in enumerate(day + night)), encoding="utf-8")
    cands = [_cand("K0001", night[10], "STARLINK-1007", 44713), _cand("K0002", night[40], "STARLINK-1008", 44714, passage=(2.0, 3.0)),
             _cand("K0003", night[70], "ONEWEB-0126", 47272), _cand("K0004", night[90], "COSMOS 2522 [GLONASS-M]", 42939, profile=False),
             _cand("K0005", day[3], "STARLINK-1009", 44715)]        # am Tag → zählt weder als Spur noch als Zeit
    (obs / "kandidaten.json").write_text(json.dumps(cands), encoding="utf-8")
    (run / "videos.json").write_text(json.dumps([{"session": session, "video": "Beispielfeld-ab12/ereignisse.mp4", "frames": 150, "state": "Fertig",
                                                  "candidates_file": "Beispielfeld-ab12/kandidaten.json"}]), encoding="utf-8")
    return run.name


def _app(tmp_path, monkeypatch, with_site=True):
    monkeypatch.setenv("ASTROFLOW_MODULES", "stacking,skytrace")
    home = tmp_path / "skytrace"
    _lauf(home)
    data = tmp_path / "data"
    data.mkdir()
    if with_site:
        (data / "settings.json").write_text(json.dumps({"sites": {"list": [{"id": "bsp", "name": "Beispielberg", "lat": 47.0, "lon": 8.0, "alt_m": 500,
                                                                           "accuracy_m": 100}], "default": "bsp", "periods": []}}), encoding="utf-8")
    monkeypatch.setenv("SKYTRACE_HOME", str(home))
    return TestClient(create_app(load_settings(data)))


def test_rate_und_helligkeit_je_standort(tmp_path, monkeypatch):
    with _app(tmp_path, monkeypatch) as c:
        d = c.get("/api/skytrace/statistics/satellites").json()
        st = c.get("/api/skytrace/statistics").json()
    assert "_full" not in st and all("ph" not in x for x in st["candidates"]) and "_sat" not in st["sessions"][0]
    assert [s["name"] for s in d["sites"]] == ["Beispielberg"]
    (cell,) = d["cells"]
    assert (cell["y"], cell["m"], cell["site"]) == (2022, 8, "bsp")
    assert cell["h"] == pytest.approx(120 * 10 / 3600, abs=0.01)                     # nur die Nachtaufnahmen, Tagesaufnahmen zählen nicht
    assert (cell["n"], cell["sl"], cell["ow"]) == (4, 2, 1)                          # Tag-Spur fehlt; GLONASS ohne Profil zählt, hat aber keine mag
    assert len(cell["mags"]) == 3 and all(10.5 < m < 12.0 for m in cell["mags"])     # 22 − 0,2·X − 10 (Richtwert Stellina)
    assert d["zp"] == {"night": 0, "device": 0, "estimate": 3, "none": 1}
    # kürzere Durchgangszeit (1 s statt 2 s) = doppelter Fluss = 0,75 mag heller
    assert min(cell["mags"]) == pytest.approx(max(cell["mags"]) - 0.75, abs=0.15)
    b = d["brightest"][0]
    assert b["name"] == "STARLINK-1008" and b["family"] == "starlink" and b["src"] == "estimate" and b["site"] == "Beispielberg"
    assert len(json.dumps(d)) < 4000                                                 # klein fürs Handy


def test_nullpunkt_aus_dem_bilder_modul_und_ohne_standort(tmp_path, monkeypatch):
    with _app(tmp_path, monkeypatch, with_site=False) as c:
        base = c.get("/api/skytrace/statistics/satellites").json()
    assert base["sites"][0]["name"] == "Standort unbekannt"                          # ohne Ort: alle Aufnahmen, nichts wird als Tag ausgeschlossen
    assert base["cells"][0]["h"] == pytest.approx(150 * 10 / 3600, abs=0.01) and base["cells"][0]["n"] == 5
    # direkt mit Nullpunkt der Nacht aus dem Dienst
    from astroflow.modules.skytrace import stats
    data = stats.collect(tmp_path / "skytrace" / "Ergebnisse")
    out = sat.collect(data, {"nights": {"2022-08-10|stellina-test|200.0": 20.0}, "devices": {}}, lambda s: None)
    assert out["zp"]["night"] == 4 and out["zp"]["estimate"] == 0
    assert all(8.0 < m < 10.0 for m in out["cells"][0]["mags"])
