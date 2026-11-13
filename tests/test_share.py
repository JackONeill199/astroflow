"""Teilen-Karte (share.py): Inhalt, Formate, Datenschutz (kein Ort, keine Pfade, keine Seriennummer), Urheber bei Importen."""
import io
import json
import time

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from astroflow.core.config import load_settings
from astroflow.modules.stacking import share
from astroflow.modules.stacking.asteroids import ASTEROID_VERSION
from astroflow.modules.stacking.db import DB
from astroflow.modules.stacking.depth import DEPTH_VERSION
from astroflow.server import create_app

# Was nie auf Karte oder Text darf (erfundene Werte, die in den Testdaten stecken)
SECRETS = ("b7337b", "Erfundenhausen", "47.5", "10.25", "geheimer_ordner", "img-0001", ".fits", "22:14", "Sk-geheim")


def _photo(path, w=1200, h=800):
    rng = np.random.default_rng(3)
    a = rng.normal(25, 5, (h, w, 3)).clip(0, 255)
    for _ in range(150):
        y, x = rng.integers(4, h - 4), rng.integers(4, w - 4)
        a[y - 2:y + 3, x - 2:x + 3] = 240
    Image.fromarray(a.astype(np.uint8)).save(path, quality=92)


@pytest.fixture
def app_db(tmp_path, monkeypatch):
    """Ein gestacktes Objekt (M27) mit Tiefe/Asteroiden/Satellitenspuren und ein importiertes fremdes Bild – ohne Pipeline."""
    monkeypatch.setenv("ASTROFLOW_MODULES", "stacking")
    monkeypatch.setenv("ASTROFLOW_AUTO", "0")
    monkeypatch.setenv("ASTROFLOW_PICTURE_SOLVE", "0")
    s = load_settings(tmp_path / "data")
    db = DB(s.db_path)
    out = s.data_dir / "output" / "geheimer_ordner"
    out.mkdir(parents=True)
    _photo(out / "M27_20220521-221455_36f.jpg")
    pid = db.x("INSERT INTO projects(key,name,obj_id,kind,ra,dec,options,created) VALUES(?,?,?,?,?,?,?,?)",
               ("m27", "M27 Hantelnebel", "M27", "planetary", 299.9, 22.72, "{}", time.time()))
    stats = {"shape": [800, 1200], "fwhm_px": 3.1, "stars": 300, "nights": ["2022-05-21", "2022-05-22"],
             "trails": {"frames": 2, "lines": 3, "pixels": 900, "kept": 0}, "bands": {"RGB": {"frames": 36, "exposure": 360}},
             "depth": {"v": DEPTH_VERSION, "ok": True, "grenze": {"m5": 15.91, "erfasst": 2605, "katalog": 23114},
                       "entfernung": {"fernster": {"id": "Gaia 1", "ly": 36100.0, "g": 15.0}},
                       "fernstes": {"name": "NAME [G2012b] C039", "ly": 1.6e9, "z": 0.12},
                       "hauptobjekt": {"name": "M27", "ly": 1269.0}, "galaxien_sichtbar": 2},
             "asteroids": {"v": ASTEROID_VERSION, "ok": True, "nachgewiesen": 1, "im_feld": 2,
                           "kleinkoerper": [{"name": "(433) Eros", "status": "nachgewiesen", "mag": 14.2}]}}
    rid = db.x("INSERT INTO results(project_id,created,n_frames,exposure,files,stats,review) VALUES(?,?,?,?,?,?,?)",
               (pid, time.time(), 36, 360.0, json.dumps({"jpg": str(out / "M27_20220521-221455_36f.jpg")}), json.dumps(stats), "[]"))
    for i in range(40):
        db.x("INSERT INTO frames(id,project_id,path,kind,night,info,status) VALUES(?,?,?,?,?,?,?)",
             (f"f{i}", pid, str(tmp_path / "geheimer_ordner" / f"img-{i:04d}.fits"), "light", "2022-05-21",
              json.dumps({"instrument": "stellina-b7337b", "date_obs": "2022-05-21T22:14:55", "filter": None,
                          "site": {"lat": 47.5, "lon": 10.25, "name": "Erfundenhausen"}}), "rejected" if i < 4 else "selected"))
    # Plate-Solving-Objekte für die Beschriftung (wie /results/{rid}/objects)
    w = s.work_dir / f"p{pid}"
    w.mkdir(parents=True)
    (w / "wcs.json").write_text("{}", encoding="utf-8")
    time.sleep(0.02)
    (w / f"objekte_r{rid}.json").write_text(json.dumps({"width": 1200, "height": 800, "objects": [
        {"name": "M 27 Dumbbell Nebula", "id": "M27", "x": 600.0, "y": 400.0, "radius": 80.0, "size": 8},
        {"name": "NGC 6853", "id": "NGC6853", "x": 610.0, "y": 410.0, "radius": 70.0, "size": 8},          # Alias von M27
        {"name": "Riesennebel", "id": "SH2-999", "x": 100.0, "y": 100.0, "radius": 3000.0, "size": 600},   # reicht weit übers Bild
        {"name": "NGC 6820", "id": "NGC6820", "x": 200.0, "y": 150.0, "radius": 30.0, "size": 2}]}), encoding="utf-8")
    # importiertes fremdes Bild (Kamera mit Seriennummer im Namen)
    _photo(out / "fremd.jpg", 900, 900)
    ipid = db.x("INSERT INTO projects(key,name,obj_id,kind,ra,dec,options,created) VALUES(?,?,?,?,?,?,?,?)",
                ("imp", "Fremdes Bild", None, None, None, None, json.dumps({"input_type": "picture", "name_custom": True}), time.time()))
    irid = db.x("INSERT INTO results(project_id,created,n_frames,exposure,files,stats,review) VALUES(?,?,?,?,?,?,?)",
                (ipid, time.time(), 1, 0, json.dumps({"jpg": str(out / "fremd.jpg")}),
                 json.dumps({"imported": {"make": "ZWO", "model": "ASI2600MC Pro SN 12345678", "date_obs": "2025-01-03T22:14:55", "format": "JPEG",
                                         "filename": "Erfundenhausen_2025-01-03_221455.jpg"}}), "[]"))
    # importiert, nicht bestimmt, nicht umbenannt: Name = Dateistamm (wie picture_import) → nie auf die Karte
    npid = db.x("INSERT INTO projects(key,name,obj_id,kind,ra,dec,options,created) VALUES(?,?,?,?,?,?,?,?)",
                ("imp2", "Erfundenhausen_2025-01-03_221455", None, None, None, None, json.dumps({"input_type": "picture"}), time.time()))
    db.x("INSERT INTO results(project_id,created,n_frames,exposure,files,stats,review) VALUES(?,?,?,?,?,?,?)",
         (npid, time.time(), 1, 0, json.dumps({"jpg": str(out / "fremd.jpg")}),
          json.dumps({"imported": {"model": "Seestar S50", "filename": "Erfundenhausen_2025-01-03_221455.jpg"}}), "[]"))
    return create_app(s), rid, irid


def _all_text(c, rid, **q) -> str:
    info = c.get(f"/api/stacking/results/{rid}/share", params=q).json()
    return json.dumps(info, ensure_ascii=False)


def test_inhalt_und_datenschutz(app_db):
    app, rid, _ = app_db
    with TestClient(app) as c:
        info = c.get(f"/api/stacking/results/{rid}/share").json()
        bb, md = info["text"]["bbcode"], info["text"]["markdown"]
        assert info["name"] == "M27 Hantelnebel" and info["has_labels"] and info["has_map"]
        assert "[b]M27 Hantelnebel[/b]" in bb and "**M27 Hantelnebel**" in md
        assert "Vaonis Stellina" in bb                                       # Gerät ohne Seriennummer
        assert "im Sternbild Fuchs" in bb and "1.269 Lichtjahre" in bb
        assert "36 gestackt · 4 aussortiert" in bb and "21.–22.05.2022" in bb and "6 min gesamt" in bb
        assert "github.com/JackONeill199/astroflow" in bb and "github.com/JackONeill199/astroflow" in md
        keys = [f["key"] for f in info["finds"]]
        assert keys[:3] == ["far", "depth", "asteroids"] and "trails" in keys and "gaia" in keys
        assert info["defaults"]["finds"] == keys[:3]
        far = next(f for f in info["finds"] if f["key"] == "far")
        assert far["value"] == "1,6 Mrd. Jahre" and "G2012b" not in far["label"]       # SIMBAD-Interna weggelassen
        assert "(433) Eros" in next(f for f in info["finds"] if f["key"] == "asteroids")["label"]
        eng = app.state.modules["stacking"].eng
        objs = c.get(f"/api/stacking/results/{rid}/objects").json
        labels = share.collect(eng, rid, lambda _r: objs())["labels"]
        assert [lb["name"] for lb in labels] == ["M27 Hantelnebel", "NGC 6820"]       # deutsch, Alias einmal, Riesenobjekt ohne Marke
        everything = _all_text(c, rid, finds=",".join(keys[:4]), photographer="Sternfreund")
        for s in SECRETS:
            assert s not in everything, s
        assert "Foto: Sternfreund" in everything


def test_karte_formate_und_metadaten(app_db):
    app, rid, _ = app_db
    with TestClient(app) as c:
        r = c.get(f"/api/stacking/results/{rid}/share/card.jpg")
        assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
        assert r.headers["content-disposition"].startswith("inline")
        im = Image.open(io.BytesIO(r.content))
        assert im.format == "JPEG" and im.width == 1600 and 800 <= im.height <= 1200       # Cloudy Nights: höchstens 1600 × 1200
        assert len(r.content) <= share.SIZES[1600] and not im.getexif() and "comment" not in im.info
        r = c.get(f"/api/stacking/results/{rid}/share/card.jpg", params={"layout": "hoch", "size": "2048", "download": "1"})
        im = Image.open(io.BytesIO(r.content))
        assert im.size == (1638, 2048) and len(r.content) <= share.SIZES[2048]
        cd = r.headers["content-disposition"]
        assert cd == 'attachment; filename="astroflow-m27-hantelnebel-hoch.jpg"'
        # ungültige Werte fallen auf die Voreinstellung zurück, alles aus geht auch
        r = c.get(f"/api/stacking/results/{rid}/share/card.jpg", params={"layout": "../x", "size": "99999", "parts": "none", "finds": "none"})
        assert r.status_code == 200 and Image.open(io.BytesIO(r.content)).width == 1600
        assert c.get("/api/stacking/results/9999/share").status_code == 404
        assert c.get("/api/stacking/results/9999/share/card.jpg").status_code == 404


def test_importiertes_bild_braucht_urheber(app_db):
    app, _, irid = app_db
    with TestClient(app) as c:
        info = c.get(f"/api/stacking/results/{irid}/share").json()
        assert info["imported"] and "Bestimmt und analysiert mit AstroFlow" in info["text"]["bbcode"]
        assert "gestackt" not in info["text"]["bbcode"]                     # fertiges Bild: nichts behaupten, was nicht passiert ist
        assert "ZWO ASI2600MC Pro" in info["text"]["bbcode"] and "12345678" not in json.dumps(info)
        assert "03.01.2025" in info["text"]["bbcode"] and "22:14" not in json.dumps(info)
        info2 = c.get(f"/api/stacking/results/{irid + 1}/share").json()
        assert info2["name"] == "Astrofoto" and "ZWO Seestar S50" in info2["text"]["bbcode"]
        assert "Erfundenhausen" not in json.dumps(info2) and "221455" not in json.dumps(info2)
        r = c.get(f"/api/stacking/results/{irid}/share/card.jpg")
        assert r.status_code == 409 and "eigenes Bild" in r.json()["detail"]
        assert c.get(f"/api/stacking/results/{irid}/share/card.jpg", params={"own": "1"}).status_code == 200
        r = c.get(f"/api/stacking/results/{irid}/share/card.jpg", params={"photographer": "  Anna\u0000 B.\n"})
        assert r.status_code == 200
        assert "Foto: Anna B." in c.get(f"/api/stacking/results/{irid}/share", params={"photographer": "Anna\u0000 B."}).json()["text"]["bbcode"]


@pytest.mark.parametrize("raw, want", [
    ("stellina-b7337b", "Vaonis Stellina"), ("Seestar S50 #4F2A9C1", "ZWO Seestar S50"),
    ("ZWO ASI294MC Pro SN 12345", "ZWO ASI294MC Pro"), ("Canon EOS 600D", "Canon EOS 600D"),
    ("QHY268C_a1b2c3d4", "QHY268C"), ("Kamera 0x1234 Serial: AB12CD", "Kamera 0x1234"), (None, None)])
def test_geraetename_ohne_seriennummer(raw, want):
    assert share.clean_device(raw) == want


def test_formatierung():
    assert share.fmt_dates(["2022-05-21"]) == "21.05.2022"
    assert share.fmt_dates(["2022-05-21T22:14:55", "2022-05-23"]) == "21.–23.05.2022"
    assert share.fmt_dates(["2026-08-30", "2026-09-02"]) == "30.08.–02.09.2026"
    assert share.fmt_dates(["2025-12-30", "2026-01-02"]) == "30.12.2025 – 02.01.2026"
    assert share.fmt_years(2.5e6) == "2,5 Mio." and share.fmt_years(1.6e9) == "1,6 Mrd." and share.fmt_years(36123) == "36.100"
    assert share.fmt_dur(4380) == "1 h 13 min" and share.fmt_dur(360) == "6 min" and share.fmt_dur(0) is None
    assert share.galaxy_name("PGC 2304") == "PGC 2304" and share.galaxy_name("NAME [G2012b] C039") is None
    assert share.slug("NGC 6992 Cirrusnebel (Ost)") == "ngc-6992-cirrusnebel-ost"
