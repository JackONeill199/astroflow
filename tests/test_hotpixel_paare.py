"""Ortsfeste Hotpixel-Paare: cosmetic() schützt sie als „Stern“ – die Fehlerkarte je Nacht muss sie finden (Sterne nicht)."""
import json

import numpy as np
from scipy import ndimage as ndi

from astroflow.core.config import load_settings
from astroflow.modules.stacking import calibration, demo, quality
from astroflow.modules.stacking.db import DB
from astroflow.modules.stacking.fits_io import read_data
from astroflow.modules.stacking.pipeline import Pipeline, stack_dir
from astroflow.modules.stacking.reviewer import ReviewerConfig
from astroflow.modules.stacking.stacking import StackState


def _lights(tmp_path, **kw):
    d = demo.generate_hotpixel_pairs(tmp_path / "n", **kw)
    files = sorted((d / "lights").glob("*.fit"))
    return [read_data(f) for f in files], np.load(d / "paare.npy")


def _map(frames):
    """Wie die Pipeline: Kosmetik je Aufnahme, ihre Ersetzungen zählen als Kandidaten."""
    fixed = [calibration.cosmetic(f, True) for f in frames]
    cands = [calibration.defect_candidates(c, True, fixed=np.flatnonzero(c != f)) for c, f in zip(fixed, frames)]
    return fixed, calibration.defect_map(cands, frames[0].size)


def test_cosmetic_keeps_pairs_defect_map_finds_them(tmp_path):
    frames, pairs = _lights(tmp_path)
    fixed, bad = _map(frames)
    assert (fixed[0].ravel()[pairs] > 5000).mean() > 0.9          # der alte Fehler: Paare überstehen die Kosmetik
    assert set(pairs.tolist()) <= set(bad.tolist())
    out = calibration.fix_defects(fixed[0], True, bad)
    assert (out.ravel()[pairs] - 300 < 150).all()                 # Paar durch Nachbarn derselben Farbe ersetzt
    assert bad.size < 0.002 * fixed[0].size
    # warme Einzelpixel knapp unter der Kosmetik-Schwelle (≈ 6σ) werden ebenfalls erfasst
    assert bad.size >= len(pairs) + 35                            # 40 warme Pixel


def test_static_field_stars_not_in_defect_map(tmp_path):
    """Steht das Feld still (Montierung mit Nachführung, kein Versatz), sitzen Sterne in jeder Aufnahme am selben Pixel –
    sie dürfen trotzdem nicht als Sensorfehler gelten (helle Nachbarn, auch in den anderen Farben)."""
    frames, pairs = _lights(tmp_path, n_groups=1, per_group=14, static=True)
    fixed, bad = _map(frames)
    assert set(pairs.tolist()) <= set(bad.tolist())
    st = quality.detect_stars(quality.analysis_image(fixed[0], True)[0], max_stars=60)
    py, px = np.divmod(pairs, demo.W)
    xy = [(x, y) for x, y in zip(st.x * 2, st.y * 2) if np.hypot(px - x, py - y).min() > 4]   # Paare gelten hier als „Stern“
    assert len(xy) >= 30
    by, bx = np.divmod(bad, demo.W)
    for x, y in xy:
        assert np.hypot(bx - x, by - y).min() > 2.5, (x, y)
    # Sternfluss bleibt erhalten
    out = calibration.fix_defects(fixed[0], True, bad)
    for x, y in xy[:30]:
        sl = (slice(int(y) - 6, int(y) + 7), slice(int(x) - 6, int(x) + 7))
        a, b = fixed[0][sl].sum() - 300 * 169, out[sl].sum() - 300 * 169
        assert abs(a - b) < 0.02 * abs(a) + 200, (x, y, a, b)


def _red_dots(img: np.ndarray, cov: np.ndarray) -> int:
    """Scharfe Punkte nur im R-Kanal des linearen Stacks (wie im fertigen Bild gesehen): R deutlich über der Umgebung,
    in der Nähe weder Grün noch Blau (Sternränder) und nicht am Rand der Abdeckung."""
    hp = np.stack([c - ndi.median_filter(c, size=7) for c in img])
    inner = ndi.minimum_filter(ndi.median_filter(cov, 7), 13) > 0.95     # Clipping macht einzelne Löcher in cov
    sig = [1.4826 * np.median(np.abs(h[inner])) + 1e-6 for h in hp]
    quiet = (ndi.maximum_filter(hp[1], 7) < 5 * sig[1]) & (ndi.maximum_filter(hp[2], 7) < 5 * sig[2])
    return ndi.label((hp[0] > 15 * sig[0]) & quiet & inner)[1]


def test_pipeline_stack_without_red_dots(tmp_path, monkeypatch):
    def run(data):
        s = load_settings(data)
        s.delete_uploads = False
        db = DB(s.db_path)
        pl = Pipeline(s, db, ReviewerConfig())
        demo.generate_hotpixel_pairs(s.input_dir / "n")
        pl.scan()
        pid = db.one("SELECT id FROM projects")["id"]
        pl.stack(pid)
        img, cov = StackState.load(stack_dir(s.work_dir / f"p{pid}", None)).result()
        return db, pid, img, cov

    db, pid, img, cov = run(tmp_path / "neu")
    info = json.loads(db.one("SELECT options FROM projects WHERE id=?", (pid,))["options"])["defect_info"]
    assert list(info.values())[0]["quelle"] == "nacht" and list(info.values())[0]["pixel"] >= 24
    assert _red_dots(img, cov) == 0
    # Gegenprobe ohne Fehlerkarte (alter Stand): die Paare bleiben als rote Punkte im Stack
    monkeypatch.setattr(calibration, "DEFECT_MIN_FRAMES", 10 ** 6)
    _, _, old, cov = run(tmp_path / "alt")
    assert _red_dots(old, cov) >= 5


def test_defect_library_falls_back_to_camera(tmp_path):
    lib = calibration.DefectLibrary(tmp_path)
    lib.add("k", "2022-07-01", np.array([5, 7, 9]))
    lib.add("k", "2022-08-01", np.array([5, 9, 11]))
    own, src = lib.get("k", "2022-07-01")
    assert src == "nacht" and own.tolist() == [5, 7, 9]
    other, src = lib.get("k", "2022-09-01")                       # Pixel aus mindestens der Hälfte der Nächte
    assert src.startswith("bibliothek") and set(other.tolist()) == {5, 7, 9, 11}
    assert lib.get("x", "2022-09-01") == (None, "")


def test_static_mono_undersampled_stars_not_in_defect_map():
    """Mono, nachgeführt ohne Versatz, knapp abgetastete Sterne (σ = 1 px, Mitte zwischen zwei Pixeln): Beide Spitzen haben
    nur einen ähnlich hellen Nachbarn – sie dürfen trotzdem nicht in die Karte (kein Farbvergleich wie bei CFA möglich)."""
    rng = np.random.default_rng(11)
    H, W = 240, 320
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    sky = np.full((H, W), 300, np.float32)
    stars = [(x + 0.5 * (i % 2), y + 0.5 * (i // 2 % 2)) for i, (x, y) in
             enumerate(zip(rng.integers(10, W - 10, 60), rng.integers(10, H - 10, 60)))]
    for x, y in stars:
        sky += rng.uniform(2000, 20000) * np.exp(-((xx - x) ** 2 + (yy - y) ** 2) / 2)
    hot = np.zeros((H, W), np.float32)
    py, px = rng.integers(5, H - 6, 10), rng.integers(5, W - 6, 10)
    hot[py, px] += 15000
    hot[py + 1, px] += 13000                                     # Paar direkt übereinander (Mono: Nachbarpixel)
    frames = [sky + hot + rng.normal(0, 14, (H, W)).astype(np.float32) for _ in range(14)]
    fixed = [calibration.cosmetic(f, False) for f in frames]
    cands = [calibration.defect_candidates(c, False, fixed=np.flatnonzero(c != f)) for c, f in zip(fixed, frames)]
    bad = calibration.defect_map(cands, H * W)
    pairs = set((py * W + px).tolist()) | set(((py + 1) * W + px).tolist())
    clean = {i for i in pairs if all(np.hypot(i % W - x, i // W - y) > 4 for x, y in stars)}
    assert clean and clean <= set(bad.tolist())
    by, bx = np.divmod(bad, W)
    near = [(x, y) for x, y in stars if np.hypot(bx - x, by - y).min() <= 2 and
            np.hypot(px - x, py - y).min() > 4]
    assert not near, near


def test_incremental_onto_old_stack_without_map(tmp_path, monkeypatch):
    """Alter Stack ohne Fehlerkarte + neue Aufnahmen mit Karte: nachstacken, nicht neu aufbauen; nichts geht kaputt,
    die alten Punkte dürfen bleiben (verschwinden erst mit „Neu stacken“)."""
    import shutil
    src = demo.generate_hotpixel_pairs(tmp_path / "quelle") / "lights"
    files = sorted(src.glob("*.fit"))
    s = load_settings(tmp_path / "data")
    s.delete_uploads = False
    db = DB(s.db_path)
    pl = Pipeline(s, db, ReviewerConfig())
    dst = s.input_dir / "n"
    dst.mkdir(parents=True)
    for f in files[:12]:
        shutil.copy(f, dst / f.name)
    monkeypatch.setattr(calibration, "DEFECT_MIN_FRAMES", 10 ** 6)     # Stand vor der Fehlerkarte
    pl.scan()
    pid = db.one("SELECT id FROM projects")["id"]
    pl.stack(pid)
    wd = stack_dir(s.work_dir / f"p{pid}", None)
    old = StackState.load(wd)
    old_img, old_cov = old.result()
    monkeypatch.undo()
    for f in files[12:]:
        shutil.copy(f, dst / f.name)
    pl.scan()
    pl.stack(pid)
    new = StackState.load(wd)
    img, cov = new.result()
    assert db.one("SELECT restack FROM projects WHERE id=?", (pid,))["restack"] == 0
    assert new.meta["ref_fid"] == old.meta["ref_fid"] and len(new.meta["frames"]) == 24     # nachgestackt, nicht neu
    assert np.isfinite(img).all()
    assert json.loads(db.one("SELECT options FROM projects WHERE id=?", (pid,))["options"])["defect_info"]
    assert _red_dots(img, cov) <= _red_dots(old_img, old_cov)
    # Hinweis „Neu stacken entfernt die Punkte“: nur Stellina, nur Stacks ohne Marke (von vor der Fehlerkarte), Rohbilder da
    assert "defects" in old.meta and not pl.hotpixel_hint(pid)                        # neuer Code setzt die Marke
    db.x("UPDATE frames SET info=json_set(info,'$.instrument','stellina-test')")
    st = json.loads((wd / "state.json").read_text(encoding="utf-8"))
    st.pop("defects")
    (wd / "state.json").write_text(json.dumps(st), encoding="utf-8")
    import os, time
    os.utime(wd / "state.json", (time.time() + 5, time.time() + 5))
    assert pl.hotpixel_hint(pid)
    db.x("UPDATE frames SET deleted=1 WHERE id=(SELECT id FROM frames WHERE stacked=1 LIMIT 1)")
    os.utime(wd / "state.json", (time.time() + 10, time.time() + 10))
    assert not pl.hotpixel_hint(pid)                                                   # Neu stacken nicht möglich
