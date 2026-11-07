"""Satellit anklicken (0.45.0): Infokarte (`GET /api/skytrace/satellite`) und Bildausschnitt (`…/satellite/crop`).
Erfundener Ort, erfundene Bahn und erfundene SATCAT-Einträge (siehe test_skytrace_3d); nichts geht ins Netz."""
import json

import numpy as np
import pytest

pytest.importorskip("sgp4")
from test_skytrace_3d import L1, L2, T0, _run, _site, client  # noqa: E402,F401
from test_skytrace_3d_run import _frames  # noqa: E402

from astroflow.modules.skytrace import satdetail  # noqa: E402

SATCAT = ("OBJECT_NAME,OBJECT_ID,NORAD_CAT_ID,OBJECT_TYPE,OPS_STATUS_CODE,OWNER,LAUNCH_DATE,LAUNCH_SITE,DECAY_DATE,PERIOD,INCLINATION,APOGEE,PERIGEE,RCS,"
          "DATA_STATUS_CODE,ORBIT_CENTER,ORBIT_TYPE\n"
          "TESTSAT,2024-001A,99001,PAY,+,ESA,2024-01-15,FRGUI,,92.90,51.64,420,410,,,EA,ORB\n"
          "TESTSAT-B,2024-001B,99002,R/B,D,US,2024-01-15,AFETR,2024-06-02,92.90,51.64,421,409,,,EA,IMP\n")


def _png(path, w=600, h=380, line=((70, 300), (520, 90))):
    from PIL import Image, ImageDraw
    rng = np.random.default_rng(1)
    im = Image.fromarray(rng.normal(20, 4, (h, w)).clip(0, 255).astype(np.uint8)).convert("RGB")
    ImageDraw.Draw(im).line(line, fill=(140, 140, 140), width=2)
    path.parent.mkdir(parents=True, exist_ok=True)
    im.save(path)


def _setup(home, satcat=True):
    """Lauf mit Trefferbild (K0001, eindeutig), zweitem Kandidaten mit zwei fast gleich guten Bahnen ohne Ausschnitt (K0003)."""
    rid, _ = _run(home, _site())
    obs = home / "Ergebnisse" / rid / "Testfeld"
    _frames(obs, n=20)
    _png(obs / "kandidaten" / "K0001" / "bild-000031.png")
    cands = json.loads((obs / "kandidaten.json").read_text(encoding="utf-8"))
    k1 = cands[0]
    k1.update(timestamp_text="2024-03-01T21:00:05 UTC",
              frames=[dict(index=31, timestamp=k1["timestamp"], timestamp_text="2024-03-01T21:00:05 UTC", image="Testfeld/kandidaten/K0001/bild-000031.png",
                           width=600, height=380, hit=True, overlay={"points": [[70, 300], [520, 90]]})],
              evidence={"length_arcmin": 30.0, "length_pixels": 400.0, "minimum_angular_speed_arcsec_s": 180.0,
                        "profile": {"offset_pixels": list(range(-12, 13)), "contrast_adu": [0] * 9 + [5, 20, 60, 90, 60, 20, 5] + [0] * 9}},
              plausibility={"original_size": [3072, 2080], "analysis_size": [1536, 1040]})
    k1["catalogs"][0]["matches"][0].update(mean_offset_arcsec=12.5, coverage=1.0, alignment_degrees=0.1)
    m = k1["catalogs"][0]["matches"][0]
    k3 = json.loads(json.dumps(k1))
    k3.update(id="K0003", frames=[dict(k1["frames"][0], image="Testfeld/kandidaten/K0003/bild-000040.png")])     # Ausschnitt fehlt
    k3["catalogs"][0]["matches"] = [dict(m, name="TESTSAT-B", norad_id=99002, tle=[L1.replace("99001", "99002"), L2.replace("99001", "99002")], mean_offset_arcsec=20),
                                    dict(m, name="TESTSAT-C", norad_id=99003, tle=None, mean_offset_arcsec=25)]
    cands.append(k3)
    (obs / "kandidaten.json").write_text(json.dumps(cands), encoding="utf-8")
    if satcat:
        arch = home / "catalog_cache" / "archives"
        arch.mkdir(parents=True, exist_ok=True)
        (arch / "satcat.csv").write_text(SATCAT, encoding="utf-8")
    return rid, obs


def test_details_eindeutig_mit_ausschnitt(client):
    c, home = client
    rid, obs = _setup(home)
    r = c.get("/api/skytrace/satellite", params={"run": rid, "key": "Testfeld/K0001", "norad": 99001})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["name"] == "TESTSAT" and d["norad_id"] == 99001 and d["intl_id"] == "2024-001A" and d["launch_date"] == "2024-01-15"
    assert d["launch_year"] == 2024 and d["owner"] == "ESA" and d["kind"] == "Satellit" and d["catalog_source"] == "SATCAT-Kopie"
    assert d["orbit_catalog"] == {"period_min": 92.9, "inclination_deg": 51.64, "apogee_km": 420.0, "perigee_km": 410.0}
    assert d["certainty"]["level"] == "eindeutig" and any("12″" in f for f in d["certainty"]["facts"])
    # Winkelgeschwindigkeit aus Spurlänge und Durchgangszeit der Bahn (30′ in 0,8 s)
    assert d["speed"]["kind"] == "bahn" and d["speed"]["arcsec_s"] == pytest.approx(30 * 60 / 0.8, rel=1e-3)
    # Helligkeit wie in der Satellitenstatistik: Gerät „stellina-test“ → Richtwert (Schätzung)
    assert d["brightness"]["src"] == "estimate" and 0 < d["brightness"]["mag"] < 15
    assert "celestrak.org" in d["links"]["celestrak"] and d["links"]["n2yo"].endswith("s=99001")
    # Ausschnitt: Spur in der Mitte, auf ≤ 560 px verkleinert, Linie im Bild
    cr = d["crop"]
    assert cr["kind"] == "kandidat" and max(cr["width"], cr["height"]) <= satdetail.CROP_MAX
    for x, y in cr["line"]:
        assert 0 <= x <= cr["width"] and 0 <= y <= cr["height"]
    mid = np.mean(cr["line"], axis=0)
    assert abs(mid[0] - cr["width"] / 2) < cr["width"] * .15 and abs(mid[1] - cr["height"] / 2) < cr["height"] * .2
    img = c.get(cr["url"])
    assert img.status_code == 200 and img.headers["content-type"] == "image/jpeg" and img.content[:2] == b"\xff\xd8"
    assert len(img.content) < 120_000                                       # klein fürs Handy
    cache = obs / "kandidaten" / "K0001" / satdetail.CROP_NAME
    assert cache.is_file() and cache.read_bytes() == img.content
    stamp = cache.stat().st_mtime_ns
    assert c.get(cr["url"]).content == img.content and cache.stat().st_mtime_ns == stamp      # aus dem Zwischenspeicher
    from PIL import Image
    import io
    assert Image.open(io.BytesIO(img.content)).size == (cr["width"], cr["height"])


def test_details_mehrdeutig_alternative_ohne_ausschnitt(client):
    c, home = client
    rid, _ = _setup(home)
    b = c.get("/api/skytrace/satellite", params={"run": rid, "key": "Testfeld/K0003", "norad": 99002}).json()
    assert b["certainty"]["level"] == "mehrdeutig" and "TESTSAT-C" in b["certainty"]["others"]
    assert b["kind"] == "Raketenstufe" and b["decay_date"] == "2024-06-02" and b["owner"] == "USA"
    assert b["crop"] is None and "kein Bildausschnitt" in b["crop_note"]
    alt = c.get("/api/skytrace/satellite", params={"run": rid, "key": "Testfeld/K0003", "norad": 99003}).json()
    assert alt["certainty"]["level"] == "alternative" and "TESTSAT-B" in alt["certainty"]["text"]
    # nichts erfunden: ohne SATCAT-Eintrag und ohne TLE keine Kennung, kein Start
    assert alt["intl_id"] is None and alt["launch_year"] is None and alt["orbit_catalog"] is None
    assert c.get("/api/skytrace/satellite/crop", params={"run": rid, "key": "Testfeld/K0003"}).status_code == 404


def test_details_ohne_satcat_aus_tle_und_einzelcache(client):
    c, home = client
    rid, _ = _setup(home, satcat=False)
    d = c.get("/api/skytrace/satellite", params={"run": rid, "key": "Testfeld/K0001", "norad": 99001}).json()
    assert d["intl_id"] == "2024-001A" and d["launch_year"] == 2024 and d["launch_date"] is None and d["catalog_source"] is None
    arch = home / "catalog_cache" / "archives"
    arch.mkdir(parents=True, exist_ok=True)
    (arch / "satcat-names.json").write_text(json.dumps({"99001": {"name": "TESTSAT", "type": "Satellit", "cospar": "2024-001A", "launch": "2024-01-15"}}), encoding="utf-8")
    d = c.get("/api/skytrace/satellite", params={"run": rid, "key": "Testfeld/K0001", "norad": 99001}).json()
    assert d["launch_date"] == "2024-01-15" and d["catalog_source"] == "SATCAT-Einzelabfrage"


def test_details_pruefungen(client):
    c, home = client
    rid, _ = _setup(home)
    for p in (dict(run="..", key="Testfeld/K0001", norad=99001), dict(run=rid, key="../Testfeld/K0001", norad=99001),
              dict(run=rid, key="Testfeld/..", norad=99001), dict(run=rid, key="Testfeld/K0001", norad=12345),
              dict(run=rid, key="Testfeld/K9999", norad=99001), dict(run="gibt-es-nicht", key="Testfeld/K0001", norad=99001)):
        assert c.get("/api/skytrace/satellite", params=p).status_code == 400, p
    assert c.get("/api/skytrace/satellite/crop", params={"run": rid, "key": "Testfeld/../../x"}).status_code == 400


def test_run3d_bodenspur_und_lauf(client):
    """run3d liefert den Laufnamen und je Satellit die Bodenspur (geodätisch, alle 10 s) – Punkt beim Durchgang gegen astropy."""
    import astropy.units as u
    from astropy.coordinates import EarthLocation
    from test_skytrace_3d import _astropy
    c, home = client
    site = _site()
    rid, _ = _setup(home)
    r = c.get("/api/skytrace/run3d", params={"run": rid}).json()
    assert r["ok"] and r["run"] == rid
    h = next(x for x in r["hits"] if x["norad_id"] == 99001)
    assert h["ground_step"] == 10 and len(h["ground"]) == 61
    i = round((h["t_pass"] - h["window"][0]) / h["ground_step"])
    t = h["window"][0] + i * h["ground_step"]
    loc = EarthLocation.from_geocentric(*(_astropy(t, site)[3] * u.km))
    assert h["ground"][i] == pytest.approx([loc.lat.deg, loc.lon.deg], abs=.01)


def test_helfer():
    assert satdetail.intl_from_tle([L1, L2]) == "2024-001A"
    assert satdetail.intl_from_tle(["1 25544U 98067A   24061.5 …", ""]) == "1998-067A"
    assert satdetail.intl_from_tle(None) is None
    assert satdetail.kind_of("STARLINK-1234", "PAY") == "Starlink"
    assert satdetail.kind_of("SL-16 R/B", "R/B") == "Raketenstufe"
    assert satdetail.kind_of("COSMOS 2251 DEB", "DEB") == "Trümmerteil"
    assert satdetail.kind_of("TESTSAT", None) is None
    box = satdetail.crop_box(1000, 800, [[100, 700], [900, 100]])
    assert 0 <= box[0] < box[2] <= 1000 and 0 <= box[1] < box[3] <= 800
