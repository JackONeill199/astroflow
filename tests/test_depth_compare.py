"""„Wie tief schaust du?“ (depth.py) und Vergleich mit einer Referenz (compare.py) – offline mit synthetischem Stack,
erfundenem Feld und Katalog-Attrappen (Gaia, SIMBAD, Durchmusterung). Keine Netzabfragen."""
import json
import math
import time

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from astroflow.core.config import load_settings
from astroflow.modules.stacking import compare, depth, photometry
from astroflow.modules.stacking.db import DB
from astroflow.modules.stacking.fits_io import write_fits
from astroflow.server import create_app

H, W, RA0, DEC0, SCALE = 400, 600, 150.0, 40.0, 1.2 / 3600      # erfundenes Feld, 1,2″/px
ZP = 24.0
HDR = {"CTYPE1": "RA---TAN", "CTYPE2": "DEC--TAN", "CRVAL1": RA0, "CRVAL2": DEC0, "CRPIX1": W / 2 + 0.5, "CRPIX2": H / 2 + 0.5,
       "CD1_1": -SCALE, "CD1_2": 0.0, "CD2_1": 0.0, "CD2_2": SCALE}


def _wcs():
    return photometry.wcs_from({**HDR, "NAXIS1": W, "NAXIS2": H})


def _field(seed=3, n=160):
    """Katalog: Sterne G 9…20 auf einem Gitter (keine Überlappung), jeder dritte mit guter Parallaxe."""
    rng = np.random.default_rng(seed)
    gx, gy = np.meshgrid(np.linspace(40, W - 40, 16), np.linspace(40, H - 40, 10))
    x, y = gx.ravel()[:n] + rng.uniform(-3, 3, n), gy.ravel()[:n] + rng.uniform(-3, 3, n)
    g = np.linspace(9, 20, n)
    rng.shuffle(g)
    ra, dec = _wcs().pixel_to_world_values(x, y)
    plx = np.where(np.arange(n) % 3 == 0, rng.uniform(0.5, 20, n), np.nan)
    poe = np.where(np.isfinite(plx), 50.0, np.nan)
    return {"source_id": list(range(1000, 1000 + n)), "ra": np.asarray(ra), "dec": np.asarray(dec), "g": g,
            "bp_rp": np.full(n, 0.8), "parallax": plx, "poe": poe, "truncated": False}, x, y


def _render(x, y, g, seed=1, extra=()):
    rng = np.random.default_rng(seed)
    img = rng.normal(100, 1.0, (H, W)).astype(np.float32)
    yy, xx = np.mgrid[0:H, 0:W]
    s = 1.4                                                      # PSF-Sigma (FWHM ≈ 3,3 px)
    for xi, yi, gi in [*zip(x, y, g), *extra]:
        f = 10 ** (-0.4 * (gi - ZP))
        r = int(6 * s)
        x0, y0 = int(xi), int(yi)
        sl = (slice(max(0, y0 - r), y0 + r + 1), slice(max(0, x0 - r), x0 + r + 1))
        img[sl] += f / (2 * math.pi * s * s) * np.exp(-((xx[sl] - xi) ** 2 + (yy[sl] - yi) ** 2) / (2 * s * s))
    return np.stack([img] * 3)


def _result(s, db, pid, data, name, nights):
    out = s.output_dir / f"p{pid}"
    out.mkdir(parents=True, exist_ok=True)
    f = out / f"{name}_linear.fits"
    write_fits(f, data)
    L = data.mean(0)
    v = np.clip((L - 95) / 200, 0, 1)
    Image.fromarray((np.arcsinh(v * 50) / np.arcsinh(50) * 255).astype(np.uint8)[::-1]).save(out / f"{name}.jpg")
    files = {"fits": str(f), "jpg": str(out / f"{name}.jpg")}
    return db.x("INSERT INTO results(project_id, created, n_frames, exposure, files, stats) VALUES(?,?,?,?,?,?)",
                (pid, time.time(), 50, 500, json.dumps(files), json.dumps({"nights": nights})))


@pytest.fixture
def setup(tmp_path, monkeypatch):
    s = load_settings(tmp_path / "d")
    s.auto = False
    s.gaia = True
    db = DB(s.db_path)
    pid = db.x("INSERT INTO projects(key, name, created, ra, dec) VALUES('t','Testgalaxie',0,?,?)", (RA0, DEC0))
    (s.work_dir / f"p{pid}").mkdir(parents=True, exist_ok=True)
    (s.work_dir / f"p{pid}" / "wcs.json").write_text(json.dumps({"key": f"x|{H}x{W}", "wcs": HDR}), encoding="utf-8")
    cat, x, y = _field()
    gal_xy = (300.0, 250.0)
    gra, gdec = _wcs().pixel_to_world_values(*gal_xy)
    far_ra, far_dec = _wcs().pixel_to_world_values(450.0, 120.0)
    monkeypatch.setattr(photometry, "gaia_field", lambda *a, **k: cat)
    monkeypatch.setattr(depth, "simbad_redshifts", lambda *a, **k: [
        {"name": "Galaxie A", "ra": float(gra), "dec": float(gdec), "otype": "G", "z": 0.05, "size": None, "mag": None},
        {"name": "Quasar fern", "ra": float(far_ra), "dec": float(far_dec), "otype": "QSO", "z": 2.5, "size": None, "mag": 21.0},
        {"name": "Galaxienhaufen", "ra": float(gra), "dec": float(gdec), "otype": "ClG", "z": 0.3, "size": None, "mag": None}])
    return s, db, pid, cat, x, y, gal_xy


def test_depth_limits_distances_galaxies(setup):
    s, db, pid, cat, x, y, gal_xy = setup
    data = _render(x, y, cat["g"], extra=[(*gal_xy, 16.0)])
    rid = _result(s, db, pid, data, "a", ["2026-01-01"])
    files = json.loads(db.one("SELECT files FROM results WHERE id=?", (rid,))["files"])
    out = depth.compute(s.work_dir, pid, files, "Testgalaxie", facts=lambda n: {"name": n, "distance_ly": 3.0e7}, stats={})
    g = out["grenze"]
    # Grenze: synthetisches Rauschen σ=1, Nullpunkt 24 → 5σ-Grenze grob bei G 18–20; helle Sterne vollständig
    assert 17 <= g["m5"] <= 21 and abs(g["nullpunkt"] - ZP) < 0.3
    assert all(b["erfasst"] == b["katalog"] for b in g["klassen"] if b["mag"] <= 15)
    assert g["lim50"] and 16 <= g["lim50"] <= 21 and g["schwaechster"]["g"] > 16
    # Entfernungen: nur Sterne mit Parallaxe, nächster = größte Parallaxe unter den erfassten
    det_plx = [p for p, gg in zip(cat["parallax"], cat["g"]) if np.isfinite(p) and gg < 16]
    near_ly = 1000 / (max(det_plx) - depth.PLX_ZERO) * depth.LY_PER_PC
    assert out["entfernung"]["naechster"]["ly"] == pytest.approx(near_ly, rel=0.01)
    assert out["entfernung"]["fernster"]["ly"] > out["entfernung"]["naechster"]["ly"]
    # Galaxien: sichtbare mit Lichtlaufzeit, zu schwacher Quasar im Feld, Haufen gar nicht
    names = {o["name"]: o for o in out["galaxien"]}
    assert names["Galaxie A"]["sichtbar"] and not names["Quasar fern"]["sichtbar"] and "Galaxienhaufen" not in names
    assert out["fernstes"]["name"] == "Galaxie A" and 6e8 < out["fernstes"]["ly"] < 7e8      # z 0,05 ≈ 0,66 Mrd. Jahre
    assert out["hauptobjekt"]["ly"] == 3.0e7 and out["hauptobjekt"]["quelle"] == "Sage-Wissensbasis"
    # Bildpositionen im JPG (Zeile 0 = oben): FITS-y gespiegelt
    a = names["Galaxie A"]
    assert a["x"] == pytest.approx(gal_xy[0], abs=1.5) and a["y"] == pytest.approx(H - 1 - gal_xy[1], abs=1.5)
    assert out["punkte"] and {p["t"] for p in out["punkte"]} == {"s", "g"} and out["bild"] == {"w": W, "h": H}


def test_depth_clear_errors(setup, monkeypatch):
    s, db, pid, cat, x, y, _ = setup
    rid = _result(s, db, pid, _render(x, y, cat["g"]), "a", None)
    files = json.loads(db.one("SELECT files FROM results WHERE id=?", (rid,))["files"])
    with pytest.raises(depth.DepthError, match="Gaia"):
        depth.compute(s.work_dir, pid, files, gaia=False)
    monkeypatch.setattr(photometry, "gaia_field", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("kein Netz")))
    with pytest.raises(depth.DepthError, match="nicht erreichbar"):
        depth.compute(s.work_dir, pid, files)
    (s.work_dir / f"p{pid}" / "wcs.json").unlink()
    with pytest.raises(depth.DepthError, match="Plate-Solving"):
        depth.compute(s.work_dir, pid, files)


def test_compare_own_reference(setup):
    s, db, pid, cat, x, y, _ = setup
    g = np.array(cat["g"])
    keep = np.ones(len(g), bool)
    gone = int(np.argmin(np.abs(g - 13)))                       # verschwindet: heller Stern
    var = int(np.argmin(np.abs(g - 12.5)))                      # wird 1 mag heller
    keep[gone] = False
    g2 = g.copy()
    g2[var] -= 1.0
    old = _render(x, y, g, seed=5)
    new = _render(x[keep], y[keep], g2[keep], seed=6, extra=[(77.0, 333.0, 13.5)])     # neue Quelle
    r_old = _result(s, db, pid, old, "alt", ["2025-12-01"])
    r_new = _result(s, db, pid, new, "neu", ["2026-01-01"])
    refs = compare.references(db, s.work_dir, r_new)
    assert [r["id"] for r in refs] == [r_old]
    out = compare.compute(db, s.work_dir, r_new, "auto", catalogs=False)
    assert out["ref"]["art"] == "eigen" and out["ref"]["rid"] == r_old
    kinds = {c["art"]: c for c in out["kandidaten"]}
    assert set(kinds) == {"neu", "verschwunden", "heller"}, out["kandidaten"]
    assert kinds["neu"]["x"] == pytest.approx(77, abs=2) and kinds["neu"]["y"] == pytest.approx(H - 1 - 333, abs=2)
    assert kinds["verschwunden"]["x"] == pytest.approx(x[gone], abs=2)
    assert kinds["heller"]["dmag"] == pytest.approx(-1.0, abs=0.2)
    d = s.work_dir / f"p{pid}" / "vergleich" / f"r{r_new}"
    assert (d / "aktuell.jpg").exists() and (d / "referenz.jpg").exists() and (d / kinds["neu"]["bild"]).exists()
    assert any("ausgeschaltet" in h for h in out["hinweise"])


def test_compare_survey_reference(setup, monkeypatch):
    s, db, pid, cat, x, y, _ = setup
    g = np.array(cat["g"])
    # Durchmusterung: anderes Raster (halb so fein, gedreht ist nicht nötig) und nicht linear (Fotoplatte)
    big = _render(x, y, g, seed=9)[0]
    plate = np.sqrt(np.clip(big - 100, 0, None)) * 40 + 3000
    sw = photometry.wcs_from({**HDR, "NAXIS1": W, "NAXIS2": H})
    monkeypatch.setattr(compare, "survey_image", lambda *a, **k: (plate.astype(np.float32), sw, "DSS2 rot (Attrappe)", "1995-01-01"))
    new = _render(x, y, g, seed=6, extra=[(500.0, 90.0, 13.0)])
    rid = _result(s, db, pid, new, "neu", ["2026-01-01"])
    assert compare.references(db, s.work_dir, rid) == []
    with pytest.raises(compare.CompareError, match="ausgeschaltet"):
        compare.compute(db, s.work_dir, rid, "auto", survey=False, catalogs=False)
    out = compare.compute(db, s.work_dir, rid, "auto", catalogs=False)
    assert out["ref"]["art"] == "survey" and out["ref"]["datum"] == "1995-01-01"
    assert [c["art"] for c in out["kandidaten"]] == ["neu"]
    assert out["kandidaten"][0]["x"] == pytest.approx(500, abs=2)


def test_compare_catalog_and_small_bodies(setup, monkeypatch):
    """Katalogabgleich: SIMBAD-Treffer als Veränderlicher, Kleinplanet zur Aufnahmezeit (geozentrisch, ohne Standort)."""
    s, db, pid, cat, x, y, _ = setup
    cands = [{"ra": 150.0, "dec": 40.0, "art": "neu"}, {"ra": 150.01, "dec": 40.01, "art": "heller"}]
    monkeypatch.setattr(compare, "_simbad_near", lambda ra, dec, r, c: [{"name": "V* XX Test", "otype": "Mi*", "sep": 0.7}] if ra == 150.0 else [])
    seen = []
    monkeypatch.setattr(compare, "small_bodies", lambda when, *a, **k: seen.append(when) or [{"name": "(4711) Testoid", "ra": 150.01, "dec": 40.0101, "vmag": "15.2"}])
    notes = compare._catalog_check(cands, _wcs(), (H, W), ["2026-01-01T22:00:00"], [], s.work_dir)
    assert cands[0]["katalog"]["veraenderlich"] and cands[0]["katalog"]["art"] == "Mira-Veränderlicher"
    assert cands[1]["kleinkoerper"]["name"] == "(4711) Testoid" and seen == ["2026-01-01T22:00:00"]
    assert notes == []


def test_api_flow(tmp_path, monkeypatch):
    """Endpunkte: Status ohne Lösung, Auftrag im Hintergrund, Ergebnis mit Version, Dateien streng geprüft, Einstellungen."""
    s = load_settings(tmp_path / "d")
    s.auto = False
    with TestClient(create_app(s)) as c:
        db = DB(s.db_path)
        pid = db.x("INSERT INTO projects(key, name, created, ra, dec) VALUES('t','Testgalaxie',0,?,?)", (RA0, DEC0))
        rid = db.x("INSERT INTO results(project_id, created, n_frames, exposure, files, stats) VALUES(?,?,?,?,?,?)",
                   (pid, 0, 1, 1, "{}", "{}"))
        assert c.get("/api/stacking/results/999999/depth").status_code == 404
        d = c.get(f"/api/stacking/results/{rid}/depth").json()
        assert d == {"state": "none", "solved": False}
        calls = []
        monkeypatch.setattr(depth, "compute", lambda *a, **k: calls.append(k) or {"v": depth.DEPTH_VERSION, "ok": True, "grenze": {"m5": 18.2}})
        assert c.post(f"/api/stacking/results/{rid}/depth").json()["state"] in ("queued", "running")
        for _ in range(100):
            d = c.get(f"/api/stacking/results/{rid}/depth").json()
            if d["state"] not in ("queued", "running"):
                break
            time.sleep(0.05)
        assert d["state"] == "done" and d["grenze"]["m5"] == 18.2 and calls and calls[0]["gaia"] is False   # Tests: ASTROFLOW_GAIA=0
        # die Projektansicht bleibt schlank (Auswertungen nur über die eigenen Endpunkte)
        assert "depth" not in c.get(f"/api/stacking/projects/{pid}").json()["results"][0]["stats"]
        # Fehler werden mit Meldung gespeichert
        monkeypatch.setattr(compare, "compute", lambda *a, **k: (_ for _ in ()).throw(compare.CompareError("Keine passende Referenz")))
        assert c.post(f"/api/stacking/results/{rid}/compare", json={"ref": "auto"}).json()["state"] in ("queued", "running")
        for _ in range(100):
            d = c.get(f"/api/stacking/results/{rid}/compare").json()
            if d["state"] not in ("queued", "running"):
                break
            time.sleep(0.05)
        assert d["state"] == "error" and "Referenz" in d["message"] and d["refs"] == []
        assert c.post(f"/api/stacking/results/{rid}/compare", json={"ref": "../x"}).status_code == 400
        for bad in ("..%2Fwcs.json", "x.jpg", "k1.jpg"):
            assert c.get(f"/api/stacking/results/{rid}/compare/{bad}").status_code == 404
        # Einstellungen
        assert c.get("/api/stacking/insights").json()["survey"] is True
        assert c.post("/api/stacking/insights", json={"survey": False, "bogus": 1}).json()["survey"] is False
        assert "bogus" not in c.get("/api/stacking/insights").json()


def test_compare_rejects_mismatched_resolution(setup):
    """Eine ältere Version mit anderer Stackgröße passt nicht zur (neuesten) wcs.json – nicht als Referenz anbieten."""
    s, db, pid, cat, x, y, _ = setup
    small = _render(x, y, cat["g"], seed=5)[:, :300, :400]
    r_old = _result(s, db, pid, small, "alt", ["2025-12-01"])
    r_new = _result(s, db, pid, _render(x, y, cat["g"], seed=6), "neu", ["2026-01-01"])
    assert compare.references(db, s.work_dir, r_new) == []
    with pytest.raises(compare.CompareError, match="Auflösung"):
        compare.compute(db, s.work_dir, r_new, r_old, catalogs=False)
    with pytest.raises(depth.DepthError, match="anderen Stack"):
        depth.compute(s.work_dir, pid, json.loads(db.one("SELECT files FROM results WHERE id=?", (r_old,))["files"]))


K3 = np.array([[0.25, 0.5, 0.25], [0.5, 1, 0.5], [0.25, 0.5, 0.25]], np.float32)   # bilineares Debayer eines R-Pixels


def _hot_pairs(img, spots, amp, ch=0):
    """Ortsfeste Hotpixel-Paare (zwei R-Pixel übereinander, debayert) wie in unbereinigten Stellina-Stacks."""
    out = img.copy()
    for x, y in spots:
        for yo in (0, 2):
            out[ch, y + yo - 1:y + yo + 2, x - 1:x + 2] += amp * K3
    return out


# Punktgruppe (eine je Zeigegruppe) abseits der Katalogsterne, dazu ein einzelner schwächerer Punkt
HOT_GROUP = [(110, 95), (128, 108), (97, 121), (140, 86), (120, 135)]
HOT_SINGLE = [(470, 300)]


def test_compare_ignores_hotpixel_pairs(setup, monkeypatch):
    """Vergleich bestehender, unbereinigter Stacks: Hotpixel-Paare (nur ein Kanal, schärfer als ein Stern) sind keine
    Veränderung – weder als Gruppe (wird wie beim Entwickeln entfernt) noch einzeln; die echte neue Quelle bleibt."""
    s, db, pid, cat, x, y, _ = setup
    g = np.array(cat["g"])
    old = _hot_pairs(_render(x, y, g, seed=5), [(500, 60)], 80)                   # auch die Referenz: sehr deutlicher Punkt
    new = _render(x, y, g, seed=6, extra=[(77.0, 333.0, 13.5)])
    new = _hot_pairs(_hot_pairs(new, HOT_GROUP, 40), HOT_SINGLE, 15)
    r_old = _result(s, db, pid, old, "alt", ["2025-12-01"])
    r_new = _result(s, db, pid, new, "neu", ["2026-01-01"])
    hot = HOT_GROUP + HOT_SINGLE + [(500, 60)]

    def near_hot(c):
        return any(math.hypot(c["x"] - hx, c["y"] - (H - 1 - hy - 1)) < 6 for hx, hy in hot)

    # ohne Bereinigung (Zustand bis 0.38.1): die Punkte erscheinen als „neu“ bzw. „verschwunden“
    # (auch ohne die Grenzgrößen-Prüfung seit 0.43.1 – die verwirft schwache Punkte inzwischen ebenfalls)
    real, real_lim = compare.stackfix.spots, compare._limits
    monkeypatch.setattr(compare.stackfix, "spots", lambda d: (d, {"n": 0}, np.zeros((0, 2))))
    monkeypatch.setattr(compare, "_limits", _ohne_grenze(real_lim))
    before = compare.compute(db, s.work_dir, r_new, r_old, catalogs=False)
    assert sum(near_hot(c) for c in before["kandidaten"]) >= 3, before["kandidaten"]
    monkeypatch.setattr(compare.stackfix, "spots", real)
    monkeypatch.setattr(compare, "_limits", real_lim)

    out = compare.compute(db, s.work_dir, r_new, r_old, catalogs=False)
    assert out["v"] == compare.COMPARE_VERSION == 3
    assert not [c for c in out["kandidaten"] if near_hot(c)], out["kandidaten"]
    neu = [c for c in out["kandidaten"] if c["art"] == "neu"]
    assert len(neu) == 1 and neu[0]["x"] == pytest.approx(77, abs=2)
    assert out["hotpixel"]["aktuell"] >= len(HOT_GROUP) and out["hotpixel"]["referenz"] >= 1
    assert out["verworfen"]["hotpixel"] >= 1                       # der einzelne Punkt: nicht entfernt, aber verworfen
    assert any("Hotpixel" in h for h in out["hinweise"])


def test_depth_ignores_hotpixel_pair_at_galaxy(setup):
    """Tiefe: ein Hotpixel-Paar genau an einer Katalog-Galaxie macht sie nicht „sichtbar“ (und nicht zum fernsten Objekt)."""
    s, db, pid, cat, x, y, gal_xy = setup
    data = _hot_pairs(_render(x, y, cat["g"]), [(int(gal_xy[0]), int(gal_xy[1]) - 1)], 25)
    rid = _result(s, db, pid, data, "a", ["2026-01-01"])
    files = json.loads(db.one("SELECT files FROM results WHERE id=?", (rid,))["files"])
    out = depth.compute(s.work_dir, pid, files, "Testgalaxie", stats={})
    names = {o["name"]: o for o in out["galaxien"]}
    assert not names["Galaxie A"]["sichtbar"] and out["fernstes"] is None
    assert out["grenze"]["m5"] and abs(out["grenze"]["nullpunkt"] - ZP) < 0.3      # Sterne unverändert gemessen


def _ohne_grenze(real):
    """`compare._limits` ohne Aussage (Zustand bis 0.43.0): „verschwunden“ nur über die Flussbeziehung geprüft."""
    def f(*a, **k):
        lim, notes = real(*a, **k)
        return {**lim, "reach": lambda *x: (None, None, None)}, notes
    return f


def _short_stack(x, y, g, seed, noise=300.0, ceiling=6000.0):
    """Sehr kurzer Stack (wenige Aufnahmen): Rauschen ×300 (≈ 6 mag flacher), helle Sterne gesättigt."""
    d = _render(x, y, g, seed=seed)
    rng = np.random.default_rng(seed + 50)
    return np.minimum(d + rng.normal(0, noise, d.shape[1:]).astype(np.float32)[None], ceiling)


def test_compare_short_stack_reachability(setup, monkeypatch):
    """Nutzerbefund: kurzer Stack (~40 s) gegen tiefe Referenz meldete Sterne als „verschwunden“, die das kurze Bild gar nicht
    zeigen konnte. Ursache an echten Daten (M104, 4 × 10 s gegen DSS): die Flussbeziehung stützte sich auf wenige Sterne und war
    ~20× zu groß – die erwarteten Flüsse damit auch. Jetzt entscheidet die Grenzgröße des kurzen Bilds (Gaia-geeicht)."""
    s, db, pid, cat, x, y, _ = setup
    g = np.array(cat["g"])
    gone = int(np.argmin(np.abs(g - 11)))                          # hell, ungesättigt in der Referenz: echt verschwunden
    keep = np.ones(len(g), bool)
    keep[gone] = False
    r_ref = _result(s, db, pid, _render(x, y, g, seed=5), "tief", ["2025-12-01"])
    r_new = _result(s, db, pid, _short_stack(x[keep], y[keep], g[keep], seed=6), "kurz", ["2026-01-01"])
    db.x("UPDATE results SET n_frames=4, exposure=40 WHERE id=?", (r_new,))
    real_rel = compare._flux_relation

    def biased(*a, **k):                                           # Flussbeziehung wie bei M104 verfälscht
        k0, _ = real_rel(*a, **k)
        return 20 * k0, (lambda f: 20 * k0 * np.asarray(f))
    monkeypatch.setattr(compare, "_flux_relation", biased)

    def gone_mags(out):
        res = []
        for c in out["kandidaten"]:
            if c["art"] == "verschwunden":
                j = int(np.argmin(np.hypot(x - c["x"], y - (H - 1 - c["y"]))))
                res.append(float(g[j]))
        return res

    # bisher: schwache Sterne der Referenz unterhalb der Grenze des kurzen Bilds galten als verschwunden
    real_lim = compare._limits
    monkeypatch.setattr(compare, "_limits", _ohne_grenze(real_lim))
    before = gone_mags(compare.compute(db, s.work_dir, r_new, r_ref, catalogs=False))
    assert sum(m > 13 for m in before) >= 3, before
    monkeypatch.setattr(compare, "_limits", real_lim)

    out = compare.compute(db, s.work_dir, r_new, r_ref, catalogs=False)
    lim = out["grenze"]
    a, b = lim["aktuell"], lim["referenz"]
    assert a["quelle"] == b["quelle"] == "gaia" and a["belichtung_s"] == 40 and a["aufnahmen"] == 4
    assert 12.5 < a["m5"] < 15 and b["m5"] > 18 and lim["abstand"] == compare.REACH_MARGIN
    mags = gone_mags(out)
    assert mags == [pytest.approx(11.0, abs=0.1)], out["kandidaten"]          # nur der echte, helle Stern
    c = next(c for c in out["kandidaten"] if c["art"] == "verschwunden")
    assert c["grenze"] == pytest.approx(a["m5"], abs=0.05) and c["mag_ref"] == pytest.approx(11.0, abs=0.3)
    assert out["verworfen"]["unter_grenze"] >= 3 and any("Grenzgröße" in h for h in out["hinweise"])

    # ohne Gaia: gespeicherte Tiefe (m5, Nullpunkt) der Versionen – die Nullpunkte gelten je Bild, unabhängig vom
    # (hier weiter verfälschten) Faktor k
    db.x("UPDATE results SET stats=? WHERE id=?", (json.dumps({"nights": ["2026-01-01"], "depth": {"grenze": {
        "m5": a["m5"], "nullpunkt": ZP}}}), r_new))
    db.x("UPDATE results SET stats=? WHERE id=?", (json.dumps({"nights": ["2025-12-01"], "depth": {"grenze": {
        "nullpunkt": ZP}}}), r_ref))
    out = compare.compute(db, s.work_dir, r_new, r_ref, catalogs=False, gaia=False)
    assert out["grenze"]["aktuell"]["quelle"] == "tiefe" and out["grenze"]["referenz"]["quelle"] == "nullpunkt"
    assert gone_mags(out) == [pytest.approx(11.0, abs=0.1)], out["kandidaten"]


def test_compare_reference_zero_point_other_pixel_scale(setup):
    """Ohne Gaia: Nullpunkt einer eigenen Referenz mit gröberem Pixelmaßstab (2,4″ statt 1,2″). `reproject` erhält den Fluss
    nicht – auf dem feineren Raster ist der Aperturfluss ~4× größer; ohne Flächenfaktor wäre die Quelle 1,5 mag zu hell."""
    s, db, pid, cat, x, y, _ = setup
    g = np.array(cat["g"])
    gone = int(np.argmin(np.abs(g - 11)))
    keep = np.ones(len(g), bool)
    keep[gone] = False
    # Referenz als eigenes Objekt: halbe Auflösung, gleiche Feldmitte, gleicher Nullpunkt je Stern
    h2, w2 = H // 2, W // 2
    hdr2 = {**HDR, "CRPIX1": w2 / 2 + 0.5, "CRPIX2": h2 / 2 + 0.5, "CD1_1": -2 * SCALE, "CD2_2": 2 * SCALE}
    pid2 = db.x("INSERT INTO projects(key, name, created, ra, dec) VALUES('t2','Testgalaxie alt',0,?,?)", (RA0, DEC0))
    (s.work_dir / f"p{pid2}").mkdir(parents=True, exist_ok=True)
    (s.work_dir / f"p{pid2}" / "wcs.json").write_text(json.dumps({"key": f"x|{h2}x{w2}", "wcs": hdr2}), encoding="utf-8")
    x2, y2 = photometry.wcs_from({**hdr2, "NAXIS1": w2, "NAXIS2": h2}).world_to_pixel_values(cat["ra"], cat["dec"])
    rng = np.random.default_rng(11)
    img = rng.normal(100, 1.0, (h2, w2)).astype(np.float32)
    yy, xx = np.mgrid[0:h2, 0:w2]
    sg = 0.9
    for xi, yi, gi in zip(x2, y2, g):
        img += np.float32(10 ** (-0.4 * (gi - ZP)) / (2 * math.pi * sg * sg)) * np.exp(-((xx - xi) ** 2 + (yy - yi) ** 2) / (2 * sg * sg)).astype(np.float32)
    r_ref = _result(s, db, pid2, np.stack([img] * 3), "grob", ["2025-12-01"])
    r_new = _result(s, db, pid, _short_stack(x[keep], y[keep], g[keep], seed=6, noise=60.0), "kurz", ["2026-01-01"])
    for rid, st in ((r_new, {"nights": ["2026-01-01"], "depth": {"grenze": {"nullpunkt": ZP}}}),
                    (r_ref, {"nights": ["2025-12-01"], "depth": {"grenze": {"nullpunkt": ZP}}})):
        db.x("UPDATE results SET stats=? WHERE id=?", (json.dumps(st), rid))
    out = compare.compute(db, s.work_dir, r_new, r_ref, catalogs=False, gaia=False)
    assert out["grenze"]["referenz"]["quelle"] == "nullpunkt"
    c = [c for c in out["kandidaten"] if c["art"] == "verschwunden"]
    assert len(c) == 1 and c[0]["mag_ref"] == pytest.approx(11.0, abs=0.4), out["kandidaten"]
