"""Observatorium, Himmelsatlas: Bildlage aus der Plate-Solving-Lösung (Ecken, Größe, Ausrichtung), API und Himmelsdaten."""
import json
import math
from pathlib import Path

import numpy as np
from fastapi.testclient import TestClient
from PIL import Image

from astroflow.core.config import load_settings
from astroflow.modules.stacking import observatory
from astroflow.modules.stacking.db import DB
from astroflow.server import create_app

HIMMEL = Path(observatory.__file__).parent / "static" / "himmel"


def _tan(ra, dec, scale_as, rot_deg, w, h):
    """TAN-Lösung wie von ASTAP: Norden oben, Osten links bei rot = 0 (CD1_1 < 0)."""
    s, r = scale_as / 3600, math.radians(rot_deg)
    return {"CTYPE1": "RA---TAN", "CTYPE2": "DEC--TAN", "CRVAL1": ra, "CRVAL2": dec, "CRPIX1": w / 2 + 0.5, "CRPIX2": h / 2 + 0.5,
            "CD1_1": -s * math.cos(r), "CD1_2": s * math.sin(r), "CD2_1": -s * math.sin(r), "CD2_2": s * math.cos(r)}


def _jpg(path, w, h):
    Image.fromarray(np.zeros((h, w, 3), np.uint8)).save(path)
    return str(path)


def _check_north_up_east_left(fp, ra, dec, fov_w, fov_h):
    tl, tr, br, bl = fp["corners"]
    assert abs(fp["ra"] - ra) < 1e-3 and abs(fp["dec"] - dec) < 1e-3
    assert abs(fp["fov"][0] - fov_w) < 0.01 * fov_w and abs(fp["fov"][1] - fov_h) < 0.01 * fov_h
    assert tl[0] > tr[0] and bl[0] > br[0]          # Osten (größere RA) links
    assert tl[1] > bl[1] and tr[1] > br[1]          # Norden oben


def test_stack_footprint_cropped_jpg(tmp_path):
    pid, W, H = 3, 400, 300
    (tmp_path / f"p{pid}").mkdir()
    (tmp_path / f"p{pid}" / "wcs.json").write_text(json.dumps({"wcs": _tan(83.82, -5.39, 2.0, 0, W, H), "key": f"abc|{H}x{W}"}))
    jpg = _jpg(tmp_path / "bild.jpg", 380, 280)                       # beschnitten → mittiger Ausschnitt
    fp = observatory.stack_footprint(tmp_path, pid, {"jpg": jpg})
    _check_north_up_east_left(fp, 83.82, -5.39, 380 * 2 / 3600, 280 * 2 / 3600)
    jpg2 = _jpg(tmp_path / "gross.jpg", 800, 600)                     # anderer Maßstab (2×): ganzes Feld
    fp2 = observatory.stack_footprint(tmp_path, pid, {"jpg": jpg2})
    assert abs(fp2["fov"][0] - W * 2 / 3600) < 1e-3


def test_stack_footprint_rotation():
    """90° gedreht: oben zeigt nach Osten bzw. Westen – die Kante TL→TR läuft dann in Deklination."""
    fp = observatory._describe(observatory._wcs(_tan(10.0, 41.0, 3.0, 90, 200, 100), 200, 100), 0, 0, 200, 100)
    tl, tr, br, bl = fp["corners"]
    assert abs(tl[1] - tr[1]) > 0.1 and abs(tl[0] - tr[0]) * math.cos(math.radians(41)) < 0.02


def test_picture_footprint_old_and_new():
    w, h = 300, 200
    # ältere Lösung ohne WCS: Mitte/Maßstab/Drehung wie `services.describe` (rotation = atan2(CD2_1, CD1_1) = 180° bei Norden oben)
    fp = observatory.picture_footprint({"ra": 200.0, "dec": 20.0, "scale": 5.0, "rotation": 180.0, "width": w, "height": h})
    _check_north_up_east_left(fp, 200.0, 20.0, w * 5 / 3600, h * 5 / 3600)
    # neuere Lösung mit WCS
    fp2 = observatory.picture_footprint({"ra": 200.0, "dec": 20.0, "width": w, "height": h, "wcs": _tan(200.0, 20.0, 5.0, 0, w, h)})
    assert np.allclose(fp["corners"], fp2["corners"], atol=1e-4)
    assert observatory.picture_footprint({"ra": None, "width": w, "height": h}) is None


def test_wcs_subset():
    hdr = {**_tan(1, 2, 3, 0, 10, 10), "NAXIS": 2, "COMMENT": "x", "SIMPLE": True, "A_ORDER": 2}
    sub = observatory.wcs_subset(hdr)
    assert set(sub) == {"CTYPE1", "CTYPE2", "CRVAL1", "CRVAL2", "CRPIX1", "CRPIX2", "CD1_1", "CD1_2", "CD2_1", "CD2_2"}


def test_atlas_api(tmp_path):
    s = load_settings(tmp_path / "d")
    s.auto = False
    with TestClient(create_app(s)) as c:
        db = DB(s.db_path)
        pid = db.x("INSERT INTO projects(key, name, kind, created) VALUES('a','Orionnebel','emission',0)")
        jpg = _jpg(tmp_path / "m42.jpg", 400, 300)
        rid = db.x("INSERT INTO results(project_id, created, n_frames, exposure, files, stats) VALUES(?,?,?,?,?,?)",
                   (pid, 5, 10, 600, json.dumps({"jpg": jpg, "thumb": jpg}), "{}"))
        db.x("INSERT INTO projects(key, name, created) VALUES('b','Ohne Lösung',0)")
        wd = s.work_dir / f"p{pid}"
        wd.mkdir(parents=True, exist_ok=True)
        (wd / "wcs.json").write_text(json.dumps({"wcs": _tan(83.82, -5.39, 2.0, 0, 400, 300), "key": "k|300x400"}))
        d = c.get("/api/stacking/atlas").json()
        assert [p["name"] for p in d["photos"]] == ["Orionnebel"]
        p = d["photos"][0]
        assert p["pid"] == pid and p["rid"] == rid and p["href"] == f"#/p/{pid}" and p["thumb"].endswith(f"/{rid}/thumb")
        assert len(p["corners"]) == 4 and abs(p["ra"] - 83.82) < 1e-3
        assert (wd / "atlas.json").exists()
        ids = {o["id"] for o in d["catalog"]}
        assert {"M42", "M31"} <= ids and all(0 <= o["ra"] < 360 for o in d["catalog"])
        # Zwischenspeicher: gleiche Antwort; neue Lösung → neu berechnet
        assert c.get("/api/stacking/atlas").json()["photos"] == d["photos"]
        (wd / "wcs.json").write_text(json.dumps({"wcs": _tan(84.0, -5.0, 2.0, 0, 400, 300), "key": "k|300x400"}))
        import os
        os.utime(wd / "wcs.json", (1e9 + 10, 2e9))
        assert abs(c.get("/api/stacking/atlas").json()["photos"][0]["ra"] - 84.0) < 1e-3


def test_sky_data_files():
    st = json.loads((HIMMEL / "sterne.json").read_text(encoding="utf-8"))["stars"]
    assert len(st) % 4 == 0 and 8000 < len(st) // 4 < 9200
    ra, dec, mag = st[0], st[1], st[2]                               # hellster Stern zuerst: Sirius
    assert abs(ra - 101.29) < 0.05 and abs(dec + 16.72) < 0.05 and mag < -1
    assert all(0 <= v < 360 for v in st[0::4]) and all(-90 <= v <= 90 for v in st[1::4])
    cs = json.loads((HIMMEL / "sternbilder.json").read_text(encoding="utf-8"))["items"]
    names = {c["id"]: c["name"] for c in cs}
    assert len(cs) >= 88 and names["Ori"] == "Orion" and names["UMa"] == "Großer Bär"
    for c in cs:
        for seg in c["lines"]:
            assert len(seg) % 2 == 0 and all(0 <= v < 360 for v in seg[0::2])


def test_horizon_points_from_own_frames(tmp_path):
    """Horizont-Vorschlag: Azimut/Höhe der eigenen Aufnahmen am Ort zur mittleren Aufnahmezeit (lokal gerechnet)."""
    import datetime as dt
    from astropy import units as u
    from astropy.coordinates import AltAz, EarthLocation, SkyCoord
    from astropy.time import Time
    s = load_settings(tmp_path / "d")
    db = DB(s.db_path)
    pid = db.x("INSERT INTO projects(key, name, ra, dec, created) VALUES('m31','M31',10.68,41.27,0)")
    for k, t in enumerate(("2026-11-02T21:00:00", "2026-11-02T21:20:00", "2026-11-02T21:40:00")):
        db.x("INSERT INTO frames(id, project_id, path, kind, night, info) VALUES(?,?,?,?,?,?)",
             (f"f{k}", pid, f"x/{k}.fits", "light", "2026-11-02", json.dumps({"date_obs": t})))
    site = {"id": "s1", "lat": 50.0, "lon": 10.0}
    pts = observatory.horizon_points(db, lambda night: site, "s1")
    assert len(pts) == 1 and pts[0]["n"] == 3 and pts[0]["night"] == "2026-11-02"
    # Referenz mit astropy wie in tonight.py ohne IERS-Download/Altersprüfung (Netzsperre, leerer Cache): genügt für den Vergleich
    import warnings
    from astropy.utils import iers
    with warnings.catch_warnings(), iers.conf.set_temp("auto_download", False), iers.conf.set_temp("auto_max_age", None), \
            iers.conf.set_temp("iers_degraded_accuracy", "warn"):
        warnings.simplefilter("ignore")
        ref = SkyCoord(10.68 * u.deg, 41.27 * u.deg).transform_to(AltAz(
            obstime=Time(dt.datetime(2026, 11, 2, 21, 20, tzinfo=dt.timezone.utc)), location=EarthLocation(lat=50 * u.deg, lon=10 * u.deg)))
    from astroflow.modules.stacking.objects import ang_sep
    assert ang_sep(pts[0]["az"], pts[0]["alt"], ref.az.deg, ref.alt.deg) < 0.6       # ohne Präzession genähert (nahe Zenit wirkt Azimut größer)
    assert observatory.horizon_points(db, lambda night: site, "anderer") == []

def test_photo_draw_order(server, browser):
    """Überlappende eigene Fotos: großes Bildfeld hinten, kleines vorne; ausgewählt und überfahren ganz vorne
    (Fehler 12.11.2026: ausgewähltes Herznebel-Foto blieb hinter dem Stellina-Stack von IC 1795)."""
    base, _ = server
    page = browser.new_page()
    page.goto(f"{base}/#/observatorium")
    page.wait_for_function("() => !!(window.AFobs && window.AFobs.photoOrder)")
    order = lambda sel=None, hov=None: page.evaluate(
        """([s, h]) => window.AFobs.photoOrder([
             {pid: 1, fov: [1.0, 0.7]}, {pid: 2, fov: [2.3, 1.3]}, {pid: 3, fov: [0.5, 0.4]}, {pid: 4, fov: [1.0, 0.7]}], s, h).map(p => p.pid)""",
        [sel, hov])
    assert order() == [2, 1, 4, 3]                       # hinten → vorne: großes Feld zuerst, gleich groß nach Kennung
    assert order(sel=2) == [1, 4, 3, 2]                  # ausgewählt nach vorne
    assert order(sel=2, hov=1) == [4, 3, 2, 1]           # überfahren vor ausgewählt
    assert order(sel=3, hov=3) == [2, 1, 4, 3]
    page.close()
