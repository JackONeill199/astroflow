"""Vorschaubilder der Einzelaufnahmen: bei der Analyse erzeugt (auch wenn Uploads danach gelöscht werden),
fehlende nachgeholt, solange die Rohdatei existiert, sauberer 404 ohne Rohdatei, aufräumbar."""
import io

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from astroflow.core.config import load_settings
from astroflow.modules.stacking import demo, frame_preview, housekeeping
from astroflow.modules.stacking.db import DB
from astroflow.modules.stacking.pipeline import Pipeline
from astroflow.modules.stacking.reviewer import ReviewerConfig
from astroflow.server import create_app


def test_render_klein_und_gestreckt():
    rng = np.random.default_rng(1)
    raw = rng.normal(1000, 20, (1200, 1800)).astype(np.float32)
    raw[600:604, 900:904] += 5000                      # ein Stern
    for pat, img in (("RGGB", raw), (None, raw), (None, np.stack([raw, raw * 1.1, raw * 0.9]))):
        data = frame_preview.render(img, pat)
        pic = Image.open(io.BytesIO(data))
        assert pic.format == "JPEG" and max(pic.size) <= frame_preview.MAX_EDGE
        assert len(data) < 80_000
        a = np.asarray(pic.convert("L"), float)
        assert 5 < np.median(a) < 120 and a.max() > 200     # Hintergrund dunkelgrau, Stern hell


@pytest.mark.parametrize("fid", ["", "../x", "a/b", "a.b"])
def test_pfad_nur_fuer_ids(tmp_path, fid):
    with pytest.raises(ValueError):
        frame_preview.path_for(tmp_path, fid)


def test_entsteht_bei_analyse_vor_dem_loeschen(tmp_path):
    s = load_settings(tmp_path / "data")
    assert s.delete_uploads                            # Standard: hochgeladene Rohbilder nach dem Stacken löschen
    db = DB(s.db_path)
    pl = Pipeline(s, db, ReviewerConfig())
    demo.generate(s.uploads_dir / "nacht1", n_lights=8, n_darks=0, n_flats=0)
    pl.scan()
    pid = db.one("SELECT id FROM projects")["id"]
    pl.process(pid)
    rows = db.q("SELECT id, deleted FROM frames WHERE kind='light'")
    assert rows and all(r["deleted"] for r in rows)    # Rohdateien sind weg …
    assert frame_preview.existing(s.work_dir) == {r["id"] for r in rows}   # … die Vorschauen bleiben

    with TestClient(create_app(s)) as c:
        fr = c.get(f"/api/stacking/projects/{pid}/frames").json()
        assert all(f["preview"] and f["star_cap"] for f in fr)
        r = c.get(f"/api/stacking/frames/{fr[0]['id']}/preview")
        assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
        d = c.get(f"/api/stacking/frames/{fr[0]['id']}").json()
        assert d["shape"] and d["stars"] and d["fwhm"] and "reason" in d
        # Vorschau weg + Rohdatei weg → kein Bild, sauberer 404, Liste meldet es
        frame_preview.path_for(s.work_dir, fr[0]["id"]).unlink()
        assert c.get(f"/api/stacking/frames/{fr[0]['id']}/preview").status_code == 404
        assert not c.get(f"/api/stacking/projects/{pid}/frames").json()[0]["preview"]
        assert c.get("/api/stacking/frames/gibtsnicht/preview").status_code == 404


def test_nachholen_ohne_neuanalyse_und_aufraeumen(tmp_path, monkeypatch):
    monkeypatch.setenv("ASTROFLOW_FRAME_PREVIEWS", "0")     # wie ältere Analysen: ohne Vorschau
    s = load_settings(tmp_path / "data")
    db = DB(s.db_path)
    pl = Pipeline(s, db, ReviewerConfig())
    demo.generate(s.input_dir / "nacht1", n_lights=6, n_darks=0, n_flats=0)
    pl.scan()
    pid = db.one("SELECT id FROM projects")["id"]
    pl.process(pid)
    assert not frame_preview.existing(s.work_dir)
    metrics = {r["id"]: r["metrics"] for r in db.q("SELECT id, metrics FROM frames WHERE kind='light'")}
    monkeypatch.setenv("ASTROFLOW_FRAME_PREVIEWS", "1")
    with TestClient(create_app(s)) as c:
        fr = c.get(f"/api/stacking/projects/{pid}/frames").json()
        assert all(f["preview"] for f in fr)               # Rohdateien liegen noch im Eingangsordner
        for f in fr:
            assert c.get(f"/api/stacking/frames/{f['id']}/preview").status_code == 200
    assert frame_preview.existing(s.work_dir) == set(metrics)
    assert {r["id"]: r["metrics"] for r in db.q("SELECT id, metrics FROM frames WHERE kind='light'")} == metrics  # keine Neu-Analyse

    # Aufräumen: Vorschauen gelöschter Aufnahmen vorausgewählt, alle Vorschauen nur auf Wunsch
    ghost = frame_preview.path_for(s.work_dir, "verwaist123")
    ghost.write_bytes(b"x" * 300)
    u = {c["key"]: c for c in housekeeping.usage(s, db)["categories"]}
    assert u["frame_previews"]["bytes"] > 300
    items = {i["key"]: i for i in housekeeping.scan(s, db)}
    assert items["orphan_previews"]["count"] == 1 and items["orphan_previews"]["default"]
    assert items["frame_previews"]["count"] == 7 and not items["frame_previews"]["default"]
    housekeeping.cleanup(s, db, ["orphan_previews"])
    assert not ghost.exists() and len(frame_preview.existing(s.work_dir)) == 6
    r = housekeeping.cleanup(s, db, ["frame_previews"])
    assert r["removed"]["frame_previews"] == 6 and r["freed"] > 0 and not frame_preview.existing(s.work_dir)


def test_gleichzeitig_dieselbe_vorschau(tmp_path):
    """Zwei Nachholungen derselben Aufnahme zugleich: keine geteilte Temp-Datei, kein Fehler, keine Reste."""
    from concurrent.futures import ThreadPoolExecutor
    raw = np.random.default_rng(2).normal(1000, 20, (600, 800)).astype(np.float32)
    with ThreadPoolExecutor(8) as ex:
        sizes = list(ex.map(lambda _: frame_preview.save(tmp_path, "abc123", raw, "RGGB"), range(16)))
    assert all(sizes) and frame_preview.existing(tmp_path) == {"abc123"}
    assert not list(frame_preview.folder(tmp_path).glob("*.tmp"))
