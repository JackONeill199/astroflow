"""HR-Diagramm von Sternhaufen (hrd.py) – offline mit synthetischem Haufen in einem erfundenen Feld und einer Gaia-Attrappe
(Positionen, G, BP−RP, Parallaxe, Eigenbewegung). Keine Netzabfragen."""
import json
import math
import time

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from astroflow.core.config import load_settings
from astroflow.modules.stacking import hrd, photometry
from astroflow.modules.stacking.db import DB
from astroflow.modules.stacking.fits_io import write_fits

H, W, RA0, DEC0, SCALE = 600, 900, 120.0, 30.0, 2.0 / 3600       # erfundenes Feld, 2″/px (0,5° × 0,33°)
ZP = 24.0
HDR = {"CTYPE1": "RA---TAN", "CTYPE2": "DEC--TAN", "CRVAL1": RA0, "CRVAL2": DEC0, "CRPIX1": W / 2 + 0.5, "CRPIX2": H / 2 + 0.5,
       "CD1_1": -SCALE, "CD1_2": 0.0, "CD2_1": 0.0, "CD2_2": SCALE}
PLX, PM, E = 2.0, (5.0, -3.0), 0.20                               # Haufen: 500 pc, Rötung E(BP−RP) 0,2
MU = 5 * math.log10(1000 / (PLX - hrd.PLX_ZERO)) - 5
TO_MG = 1.3                                                        # Abknickpunkt → log t ≈ 8,8 (~630 Mio. Jahre)


def _wcs():
    return photometry.wcs_from({**HDR, "NAXIS1": W, "NAXIS2": H})


def _cluster(seed=7, n_mem=150, n_field=350):
    """Katalog-Attrappe: Haufen (Hauptreihe bis zum Abknickpunkt + einige Riesen) um die Bildmitte, Feldsterne gleichverteilt."""
    rng = np.random.default_rng(seed)
    # Haufen: Eigenfarbe auf der Hauptreihe unterhalb des Abknickpunkts
    c0 = rng.uniform(np.interp(TO_MG, hrd.MS[:, 1], hrd.MS[:, 0]), 1.75, n_mem)
    mg = hrd.ms_mag(c0) + rng.normal(0, 0.05, n_mem)
    giants = 6
    c0[:giants], mg[:giants] = rng.uniform(1.0, 1.2, giants), rng.uniform(0.2, 0.8, giants)
    g_m = mg + MU + hrd.A_G_PER_E * E
    c_m = c0 + E
    rr = np.abs(rng.normal(0, 150, n_mem))
    th = rng.uniform(0, 2 * math.pi, n_mem)
    x_m, y_m = W / 2 + rr * np.cos(th), H / 2 + rr * np.sin(th)
    pm_m = np.c_[rng.normal(PM[0], 0.15, n_mem), rng.normal(PM[1], 0.15, n_mem)]
    # Feld: breite Bewegung, Parallaxen von nah bis fern
    x_f, y_f = rng.uniform(20, W - 20, n_field), rng.uniform(20, H - 20, n_field)
    g_f, c_f = rng.uniform(9.5, 19.0, n_field), rng.uniform(0.3, 2.2, n_field)
    pm_f = rng.normal(0, 8, (n_field, 2))
    plx_f = rng.uniform(0.1, 3.0, n_field)
    x, y = np.r_[x_m, x_f], np.r_[y_m, y_f]
    ok = (x > 15) & (x < W - 15) & (y > 15) & (y < H - 15)
    g = np.r_[g_m, g_f]
    err = 0.02 + 0.01 * 10 ** (0.2 * (g - 15))
    plx = np.r_[np.full(n_mem, PLX - hrd.PLX_ZERO), plx_f] + rng.normal(0, 1, len(g)) * err
    pm = np.r_[pm_m, pm_f] + rng.normal(0, 1, (len(g), 2)) * err[:, None] * 1.2
    ra, dec = _wcs().pixel_to_world_values(x, y)
    member = np.r_[np.ones(n_mem, bool), np.zeros(n_field, bool)]
    cat = {"source_id": list(range(5000, 5000 + len(g))), "ra": np.asarray(ra), "dec": np.asarray(dec), "g": g,
           "bp_rp": np.r_[c_m, c_f], "parallax": plx, "poe": plx / err, "plx_err": err, "pmra": pm[:, 0], "pmdec": pm[:, 1],
           "truncated": False}
    sel = {k: (np.asarray(v)[ok] if k not in ("truncated", "source_id") else v) for k, v in cat.items()}
    sel["source_id"] = [s for s, o in zip(cat["source_id"], ok) if o]
    return sel, x[ok], y[ok], member[ok]


def _render(cat, x, y, seed=1):
    """Farbstack: Instrumentenfarbe −2,5·log(B/R) = 0,8·(BP−RP) − 0,3, Rauschen σ = 1 je Kanal."""
    rng = np.random.default_rng(seed)
    img = rng.normal(100, 1.0, (3, H, W)).astype(np.float32)
    yy, xx = np.mgrid[0:H, 0:W]
    s = 1.4
    for xi, yi, gi, ci in zip(x, y, cat["g"], cat["bp_rp"]):
        f = 10 ** (-0.4 * (gi - ZP))
        br = 10 ** (-0.4 * (0.8 * ci - 0.3))                          # B/R
        wr, wb = 2 / (1 + br), 2 * br / (1 + br)
        r = int(6 * s)
        x0, y0 = int(xi), int(yi)
        sl = (slice(max(0, y0 - r), y0 + r + 1), slice(max(0, x0 - r), x0 + r + 1))
        psf = np.exp(-((xx[sl] - xi) ** 2 + (yy[sl] - yi) ** 2) / (2 * s * s)) / (2 * math.pi * s * s)
        for ch, wch in ((0, wr), (1, 1.0), (2, wb)):
            img[ch][sl] += f * wch * psf
    return img


def _result(s, db, pid, data, name="haufen"):
    out = s.output_dir / f"p{pid}"
    out.mkdir(parents=True, exist_ok=True)
    f = out / f"{name}_linear.fits"
    write_fits(f, data)
    L = data.mean(0)
    v = np.clip((L - 95) / 200, 0, 1)
    rgb = np.stack([np.clip((c - 95) / 200, 0, 1) for c in data], -1)
    Image.fromarray((np.arcsinh(rgb * 50) / np.arcsinh(50) * 255).astype(np.uint8)[::-1]).save(out / f"{name}.jpg")
    files = {"fits": str(f), "jpg": str(out / f"{name}.jpg")}
    return db.x("INSERT INTO results(project_id, created, n_frames, exposure, files, stats) VALUES(?,?,?,?,?,?)",
                (pid, time.time(), 120, 1200, json.dumps(files), json.dumps({"nights": ["2026-01-01"]})))


@pytest.fixture
def setup(tmp_path, monkeypatch):
    s = load_settings(tmp_path / "d")
    s.auto = False
    s.gaia = True
    db = DB(s.db_path)
    pid = db.x("INSERT INTO projects(key, name, kind, created, ra, dec) VALUES('t','Testhaufen','open_cluster',0,?,?)", (RA0, DEC0))
    (s.work_dir / f"p{pid}").mkdir(parents=True, exist_ok=True)
    (s.work_dir / f"p{pid}" / "wcs.json").write_text(json.dumps({"key": f"x|{H}x{W}", "wcs": HDR}), encoding="utf-8")
    cat, x, y, member = _cluster()
    calls = []
    monkeypatch.setattr(photometry, "gaia_field", lambda *a, **k: calls.append(k) or cat)
    return s, db, pid, cat, x, y, member, calls


def _files(db, rid):
    return json.loads(db.one("SELECT files FROM results WHERE id=?", (rid,))["files"])


def test_hrd_members_calibration_age(setup):
    s, db, pid, cat, x, y, member, calls = setup
    rid = _result(s, db, pid, _render(cat, x, y))
    out = hrd.compute(s.work_dir, pid, _files(db, rid), "Testhaufen", kind="open_cluster")
    assert calls and calls[0].get("need_pm") is True                 # gemeinsame Gaia-Schicht, mit Eigenbewegung
    hf = out["haufen"]
    assert hf["erkannt"] and not hf["kugel"]
    assert hf["pm"] == pytest.approx(PM, abs=0.2) and hf["plx"] == pytest.approx(PLX - hrd.PLX_ZERO, abs=0.05)
    # Mitglieder: fast alle echten, kaum Feldsterne
    ids = {p["id"] for p in out["mitglieder"]}
    true_ids = {f"Gaia DR3 {sid}" for sid, m in zip(cat["source_id"], member) if m}
    assert len(ids & true_ids) >= 0.6 * len(true_ids) and len(ids - true_ids) <= 0.1 * len(ids)
    assert hf["n_gemessen"] >= hrd.MIN_MEMBERS
    # Eichung: eigene Farbe/Helligkeit auf der Gaia-Skala
    for p in out["mitglieder"]:
        if p["g"] < 16:
            assert abs(p["c"] - p["gc"]) < 0.08 and abs(p["g"] - p["gg"]) < 0.06
    assert out["eichung"]["farbe_streuung"] < 0.05
    # Entfernung, Rötung, Abknickpunkt und Alter grob richtig
    d = out["entfernung"]
    assert d["sicher"] and d["pc"] == pytest.approx(500, rel=0.03) and d["mu"] == pytest.approx(MU, abs=0.1)
    assert out["roetung"]["E"] == pytest.approx(E, abs=0.08)
    to = out["abknick"]
    assert to["MG"] == pytest.approx(TO_MG, abs=0.6)
    a = out["alter"]
    assert a["lo"] < 6.3e8 < a["hi"] and 1e8 < a["jahre"] < 2e9
    assert out["hauptreihe"] and out["gaia"] and out["feld"]
    # Bildposition im JPG (Zeile 0 = oben) für den Klick ins Bild
    assert out["bild"] == {"w": W, "h": H}
    p = out["mitglieder"][0]
    sid = int(p["id"].split()[-1])
    i = cat["source_id"].index(sid)
    assert p["x"] == pytest.approx(x[i], abs=1.5) and p["y"] == pytest.approx(H - 1 - y[i], abs=1.5)


def test_hrd_no_cluster_and_errors(setup, monkeypatch):
    s, db, pid, cat, x, y, member, _ = setup
    # nur Feldsterne → keine Häufung, keine Deutung
    field = {k: (np.asarray(v)[~member] if k not in ("truncated", "source_id") else v) for k, v in cat.items()}
    field["source_id"] = [sid for sid, m in zip(cat["source_id"], member) if not m]
    monkeypatch.setattr(photometry, "gaia_field", lambda *a, **k: field)
    rid = _result(s, db, pid, _render(field, x[~member], y[~member]))
    out = hrd.compute(s.work_dir, pid, _files(db, rid), "Testhaufen", kind="open_cluster")
    assert not out["haufen"]["erkannt"] and out["alter"] is None and out["entfernung"] is None
    with pytest.raises(hrd.HrdError, match="Gaia"):
        hrd.compute(s.work_dir, pid, _files(db, rid), gaia=False)
    mono = s.output_dir / "mono.fits"
    write_fits(mono, _render(field, x[~member], y[~member]).mean(0))
    with pytest.raises(hrd.HrdError, match="Farbbild"):
        hrd.compute(s.work_dir, pid, {**_files(db, rid), "fits": str(mono)})
    (s.work_dir / f"p{pid}" / "wcs.json").unlink()
    with pytest.raises(hrd.HrdError, match="Plate-Solving"):
        hrd.compute(s.work_dir, pid, _files(db, rid))


def test_hrd_globular_no_age():
    rng = np.random.default_rng(1)
    c, g = rng.uniform(0.8, 1.4, 40), rng.uniform(13, 16, 40)
    out = hrd.estimate(c, g, 15.0, 0.3, True, 11.0, 18.0)
    assert out["alter"] is None and out["abknick"]["erwartet"] and out["abknick"]["g"] == pytest.approx(18.8)
    assert not out["abknick"]["sichtbar"]
    assert hrd.is_cluster("globular") and hrd.is_cluster("reflection", "M45 Plejaden") and not hrd.is_cluster("galaxy", "M101")


def test_gaia_field_old_cache(tmp_path, monkeypatch):
    """Ältere Gaia-Zwischenspeicher (ohne Eigenbewegung) bleiben für die Tiefe nutzbar; das HR-Diagramm fragt einmal neu ab."""
    import hashlib
    key = hashlib.sha1(f"field|{RA0:.3f}|{DEC0:.3f}|{0.2:.3f}|20.7|40000".encode()).hexdigest()[:16]
    (tmp_path / f"gaia_feld_{key}.json").write_text(json.dumps([[1, RA0, DEC0, 10.0, 0.8, 2.0, 50.0]]))
    seen = []

    def fake(url, q, t, maxrec=60000):
        seen.append(q)
        return [["2", str(RA0), str(DEC0), "11", "0.9", "2.1", "40", "0.05", "4.5", "-3.1"]]
    monkeypatch.setattr(photometry, "_tap_csv", fake)
    old = photometry.gaia_field(RA0, DEC0, 0.2, tmp_path)
    assert not seen and old["g"][0] == 10.0 and np.isnan(old["pmra"][0])
    new = photometry.gaia_field(RA0, DEC0, 0.2, tmp_path, need_pm=True)
    assert len(seen) == 1 and "pmra" in seen[0] and new["pmra"][0] == 4.5 and new["plx_err"][0] == 0.05
    again = photometry.gaia_field(RA0, DEC0, 0.2, tmp_path)           # danach nutzt auch die Tiefe die neue Datei
    assert len(seen) == 1 and again["pmdec"][0] == -3.1


def _wait(c, url):
    for _ in range(200):
        d = c.get(url).json()
        if d["state"] not in ("queued", "running"):
            return d
        time.sleep(0.05)
    return d


def test_api_insights_dashboard(tmp_path, monkeypatch):
    """Endpunkt, Hintergrundauftrag (insights), automatische Messung nach dem Entwickeln nur bei Haufen, Dashboard-Status."""
    from astroflow.modules.stacking import dashboard
    from astroflow.server import create_app
    s = load_settings(tmp_path / "d")
    s.auto = False
    with TestClient(create_app(s)) as c:
        db = DB(s.db_path)
        pc = db.x("INSERT INTO projects(key, name, kind, created) VALUES('a','M67','open_cluster',0)")
        pg = db.x("INSERT INTO projects(key, name, kind, created) VALUES('b','M101','galaxy',0)")
        rc = db.x("INSERT INTO results(project_id, created, n_frames, exposure, files, stats) VALUES(?,0,1,1,'{}','{}')", (pc,))
        rg = db.x("INSERT INTO results(project_id, created, n_frames, exposure, files, stats) VALUES(?,0,1,1,'{}','{}')", (pg,))
        assert c.get("/api/stacking/results/999999/hrd").status_code == 404
        assert c.get(f"/api/stacking/results/{rc}/hrd").json() == {"state": "none", "solved": False, "haufen_art": True}
        assert c.get(f"/api/stacking/results/{rg}/hrd").json()["haufen_art"] is False
        calls = []
        monkeypatch.setattr(hrd, "compute", lambda *a, **k: calls.append((a, k)) or {"v": hrd.HRD_VERSION, "ok": True,
                                                                                       "haufen": {"erkannt": True, "n_gemessen": 80}})
        assert c.post(f"/api/stacking/results/{rc}/hrd").json()["state"] in ("queued", "running")
        d = _wait(c, f"/api/stacking/results/{rc}/hrd")
        assert d["state"] == "done" and d["haufen"]["n_gemessen"] == 80
        assert calls[0][0][3] == "M67" and calls[0][1]["kind"] == "open_cluster" and calls[0][1]["gaia"] is False   # Tests: ASTROFLOW_GAIA=0
        assert "hrd" not in c.get(f"/api/stacking/projects/{pc}").json()["results"][0]["stats"]
        # Fehler mit Meldung
        monkeypatch.setattr(hrd, "compute", lambda *a, **k: (_ for _ in ()).throw(hrd.HrdError("Zu wenige Katalogsterne")))
        c.post(f"/api/stacking/results/{rg}/hrd")
        d = _wait(c, f"/api/stacking/results/{rg}/hrd")
        assert d["state"] == "error" and "Katalogsterne" in d["message"]
        # nach dem Entwickeln: Tiefe immer, HR-Diagramm nur beim Haufen
        submitted = []
        for p in (pc, pg):
            (s.work_dir / f"p{p}").mkdir(parents=True, exist_ok=True)
            (s.work_dir / f"p{p}" / "wcs.json").write_text("{}")
        ins = c.app.state.modules["stacking"].eng.insights
        monkeypatch.setattr(ins, "submit", lambda kind, rid, **k: submitted.append((kind, rid)))
        monkeypatch.setattr(ins, "settings", lambda: {"auto_depth": True, "gaia": True, "simbad": False, "survey": False, "small_bodies": False})
        ins.after_develop(rc)
        ins.after_develop(rg)
        assert submitted == [("depth", rc), ("hrd", rc), ("depth", rg)]
        # Dashboard: Haufen mit Status, Galaxie „entfällt“ und zählt nicht in der Abdeckung
        dash = c.get("/api/stacking/analysis").json()
        rows = {r["id"]: r for r in dash["rows"]}
        assert rows[pc]["status"]["hrd"] == "done" and rows[pg]["status"]["hrd"] == "error"   # gemessen, obwohl kein Haufen
        assert "hrd" in dashboard.KINDS and dash["coverage"]["hrd"].get("done") == 1

