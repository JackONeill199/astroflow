"""Verzerrungskorrektur (Polynom 2. Ordnung) in der Registrierung: Anpassung, Ablehnung, Warp, Ende zu Ende."""
import json

import numpy as np
import pytest

from astroflow.core.config import load_settings
from astroflow.modules.stacking import demo, quality, registration as R, skyquality
from astroflow.modules.stacking.db import DB
from astroflow.modules.stacking.pipeline import Pipeline
from astroflow.modules.stacking.reviewer import ReviewerConfig

H, W = 2080, 3072


def _undistort(q, k):
    p = q.copy()
    for _ in range(30):
        p = p - (demo.distort(p, k, (H, W)) - q)
    return p


def _lists(k, shift, n=400, noise=0.15, seed=2, drop=None):
    """Hellste n Sterne in Referenz und Aufnahme (Feld um `shift` gewandert, gleiche Verzeichnung k)."""
    rng = np.random.default_rng(seed)
    sky = rng.uniform(-400, [W + 400, H + 400], (3000, 2))
    sky = sky[np.argsort(-rng.uniform(0, 1, 3000))]
    ins = lambda p: (p[:, 0] > 10) & (p[:, 0] < W - 10) & (p[:, 1] > 10) & (p[:, 1] < H - 10)
    ref, tgt = demo.distort(sky, k, (H, W)), demo.distort(sky - shift, k, (H, W))
    rl, tl = ref[ins(ref)][:n], tgt[ins(tgt)][:n]
    if drop is not None:
        rl, tl = rl[~drop(rl)], tl[~drop(tl)]
    rl = rl + rng.normal(0, noise, rl.shape)
    tl = tl + rng.normal(0, noise, tl.shape)
    M = R.find_transform(rl[:100], tl[:100])[0]
    return rl, tl, M


def test_fit_distortion_corrects_wandering_field():
    k, shift = 1e-9, np.array([300.0, -180.0])
    rl, tl, M = _lists(k, shift)
    P, info = R.fit_distortion(rl, tl, M, (H, W))
    assert P is not None, info
    assert info["n"] > 250 and info["cv1"] < 0.6 * info["cv0"]
    gx, gy = np.meshgrid(np.linspace(0, W - 1, 30), np.linspace(0, H - 1, 20))
    g = np.c_[gx.ravel(), gy.ravel()]
    truth = demo.distort(_undistort(g, k) - shift, k, (H, W))
    aff = np.linalg.norm(R._apply(np.linalg.inv(M), g) - truth, axis=1).max()
    px, py = R.poly_eval(P, g[:, 0], g[:, 1])
    pol = np.linalg.norm(np.c_[px, py] - truth, axis=1).max()
    assert aff > 2.0 and pol < 0.3, (aff, pol)


def test_fit_distortion_keeps_similarity_without_distortion():
    rl, tl, M = _lists(0.0, np.array([300.0, -180.0]))
    P, info = R.fit_distortion(rl, tl, M, (H, W))
    assert P is None and info["why"] == "Restfehler sinkt nicht deutlich" and info["n"] > 250


def test_fit_distortion_needs_covered_corners():
    corner = lambda p: (p[:, 0] > 0.6 * W) & (p[:, 1] > 0.55 * H)           # Ecke unten rechts leer (z. B. Nebel)
    rl, tl, M = _lists(1e-9, np.array([200.0, -120.0]), drop=corner)
    P, info = R.fit_distortion(rl, tl, M, (H, W))
    assert P is None and info["why"] in ("Ecken nicht abgedeckt", "Bildviertel zu dünn besetzt"), info
    few = R.fit_distortion(rl[:20], tl[:20], M, (H, W))
    assert few[0] is None and few[1]["why"] == "zu wenige Sterne"


def _img(seed=1, h=300, w=420):
    rng = np.random.default_rng(seed)
    img = rng.normal(100, 5, (3, h, w)).astype(np.float32)
    yy, xx = np.mgrid[:h, :w]
    for y, x, f in zip(rng.uniform(10, h - 10, 60), rng.uniform(10, w - 10, 60), rng.uniform(200, 3000, 60)):
        img += (f * np.exp(-((yy - y) ** 2 + (xx - x) ** 2) / (2 * 1.8**2))).astype(np.float32)
    return img


def _as_poly(M, h, w):
    """Polynom, das genau die Ähnlichkeit M (Aufnahme→Referenz) umkehrt."""
    Mi = np.linalg.inv(M)
    ctr, s = ((w - 1) / 2, (h - 1) / 2), 0.5 * float(np.hypot(w, h))
    row = lambda a, b, c: [a * ctr[0] + b * ctr[1] + c, a * s, b * s, 0.0, 0.0, 0.0]
    return {"c": [row(*Mi[0]), row(*Mi[1])], "ctr": list(ctr), "s": s}


def test_poly_warp_matches_affine_for_similarity():
    img = _img()
    th = np.deg2rad(1.3)
    M = np.array([[np.cos(th), -np.sin(th), 7.3], [np.sin(th), np.cos(th), -4.6], [0, 0, 1]])
    P = _as_poly(M, *img.shape[1:])
    for scale in (1, 2):
        shape = (img.shape[1] * scale, img.shape[2] * scale)
        a, va = R.warp(img, M, shape, scale)
        b, vb = R.warp(img, M, shape, scale, poly=P)
        assert np.array_equal(va, vb)
        assert np.abs(a - b)[:, va].max() < 1e-3
    # Kometenverschiebung: Polynom mit verschobener Mitte = Ähnlichkeit gefolgt von der Verschiebung
    d = (3.5, -2.0)
    T = np.array([[1, 0, d[0]], [0, 1, d[1]], [0, 0, 1]], float) @ M
    a, va = R.warp(img, T, img.shape[1:])
    b, vb = R.warp(img, T, img.shape[1:], poly=R.shift_poly(P, d))
    assert np.array_equal(va, vb) and np.abs(a - b)[:, va].max() < 1e-3


def test_binned_poly_matches_binned_transform():
    """Kometenbahn-Stichproben (gebinnt): Polynom auf dem gebinnten Raster = comet.binned_transform der Ähnlichkeit."""
    from astroflow.modules.stacking import comet
    img = _img()[:1]
    th = np.deg2rad(1.3)
    M = np.array([[np.cos(th), -np.sin(th), 7.3], [np.sin(th), np.cos(th), -4.6], [0, 0, 1]])
    P = _as_poly(M, *img.shape[1:])
    for b in (1, 2, 3):
        Lb = comet.bin_image(img[0], b)
        a, va = R.warp(Lb[None], comet.binned_transform(M, b), Lb.shape, 1, order=1)
        c, vc = R.warp(Lb[None], None, Lb.shape, 1, order=1, poly=R.binned_poly(P, b))
        assert np.array_equal(va, vc) and np.abs(a - c)[:, va].max() < 1e-3


def test_summary_and_mode(monkeypatch):
    meta = {"frames": {"a": 1.0, "b": 1.0, "c": 1.0},
            "reg": {"a": {"n": 300, "cv0": 0.5, "cv1": 0.2, "k": 1.5, "poly": True},
                    "b": {"n": 280, "cv0": 0.3, "cv1": 0.29, "poly": False, "why": "Restfehler sinkt nicht deutlich"},
                    "c": {"n": 12, "poly": False, "why": "zu wenige Sterne"}, "alt": {"n": 1, "poly": True}}}
    s = R.summary(meta)
    assert s["frames"] == 3 and s["poly"] == 1 and s["max_corr"] == 1.5
    assert s["rms_before"] == 0.4 and s["rms_after"] == 0.25
    assert s["why"] == {"Restfehler sinkt nicht deutlich": 1, "zu wenige Sterne": 1}
    assert R.summary({"frames": {"a": 1}}) is None
    # Standard „auto“; Objektoption (auto/off) vor der globalen Einstellung, global/None/Unbekanntes = global
    monkeypatch.delenv("ASTROFLOW_DISTORTION", raising=False)
    assert R.DistortionConfig().mode == "auto"
    monkeypatch.setattr(R.DISTORTION, "mode", "auto")
    assert R.distortion_mode(None) == R.distortion_mode("global") == R.distortion_mode("quatsch") == "auto"
    assert R.distortion_mode("off") == "off"
    monkeypatch.setattr(R.DISTORTION, "mode", "off")
    assert R.distortion_mode(None) == R.distortion_mode("global") == "off"
    assert R.distortion_mode("auto") == "auto"
    monkeypatch.setenv("ASTROFLOW_DISTORTION", "0")
    assert R.DistortionConfig().mode == "off"
    from astroflow.modules.stacking.pipeline import DEFAULT_OPTIONS
    assert DEFAULT_OPTIONS["distortion"] == "global"


@pytest.fixture(scope="module")
def series(tmp_path_factory):
    src = tmp_path_factory.mktemp("verzerrung") / "serie"
    # klein und schnell: Ecke ~4 px Verzeichnung, Feld springt um bis zu 80 px
    demo.generate_distortion(src, n_lights=10, per_seg=2, size=(512, 768), k=4e-8, wander=80.0, n_stars=900)
    return src


def _pipeline(tmp_path, src):
    import shutil
    s = load_settings(tmp_path / "data")
    shutil.copytree(src, s.input_dir / "serie")
    db = DB(s.db_path)
    pl = Pipeline(s, db, ReviewerConfig())
    pl.scan()
    return s, db, pl, db.one("SELECT id FROM projects")["id"]


def test_stack_with_distortion_end_to_end(tmp_path, series, monkeypatch):
    monkeypatch.setattr(R.DISTORTION, "mode", "auto")
    s, db, pl, pid = _pipeline(tmp_path, series)
    st = pl.stack(pid, extra={"distortion": "off"})[None]   # Objekt „aus“ schlägt global „an“
    assert "reg" not in st.meta and "ref_dense" not in st.meta and st.meta["distortion"] is False
    assert pl.distortion_state(pid) is False
    db.x("UPDATE projects SET restack=1 WHERE id=?", (pid,))
    st = pl.stack(pid)[None]                                  # Standard (Objekt „global“, global „an“)
    assert st.meta["distortion"] is True and pl.distortion_state(pid) is True
    reg, n = st.meta["reg"], len(st.meta["frames"])
    assert len(st.meta["ref_dense"]) > 100 and len(reg) == n - 1           # Referenzbild selbst ohne Eintrag
    poly = [r for r in reg.values() if r["poly"]]
    assert len(poly) >= 3, reg
    assert all(r["cv1"] < r["cv0"] for r in poly)
    summ = R.summary(st.meta)
    assert summ["poly"] == len(poly) and summ["rms_after"] < summ["rms_before"]
    # state.json bleibt lesbar und kompatibel: Gerätediagnose/Himmelsqualität ordnen weiter über die Ähnlichkeit zu
    saved = json.loads((s.work_dir / f"p{pid}" / "stack" / "state.json").read_text())
    assert saved["reg"] == json.loads(json.dumps(reg))
    frames = [{"id": r["id"], "metrics": json.loads(r["metrics"])} for r in db.q("SELECT id, metrics FROM frames WHERE stacked=1")]
    links = skyquality.frame_links(saved, frames)
    assert len(links) >= n - 1
    # Entwickeln: Zusammenfassung in den Ergebnis-Stats
    rid = pl.develop(pid)
    stats = json.loads(db.one("SELECT stats FROM results WHERE id=?", (rid,))["stats"])
    assert stats["distortion"]["poly"] == len(poly)


def test_dense_reference_for_older_stacks(tmp_path, series, monkeypatch):
    """Stack ohne dichte Referenzliste (vor der Verzerrungskorrektur): erst aus dem Referenzbild, ohne Rohbild aus dem Stack."""
    from pathlib import Path
    monkeypatch.setattr(R.DISTORTION, "mode", "off")
    s, db, pl, pid = _pipeline(tmp_path, series)
    st = pl.stack(pid)[None]
    assert "ref_dense" not in st.meta
    ref = Path(db.one("SELECT path FROM frames WHERE id=?", (st.meta["ref_fid"],))["path"])
    lib = pl._library()
    a = pl._dense_ref(st, lib, {})
    assert len(a) > 100
    st.meta.pop("ref_dense")
    ref.rename(ref.with_suffix(".weg"))
    b = pl._dense_ref(st, lib, {})
    assert len(b) > 100
    # aus dem Stack gemessene Sterne liegen (fast) auf denen des Referenzbilds
    from scipy.spatial import cKDTree
    d, _ = cKDTree(a).query(b[:80])
    assert np.median(d) < 0.5


def test_dense_stars_match_analysis():
    raw = np.random.default_rng(4).normal(300, 10, (512, 768)).astype(np.float32)
    yy, xx = np.mgrid[:512, :768]
    for y, x in np.random.default_rng(5).uniform([20, 20], [490, 740], (250, 2)):
        raw += (2000 * np.exp(-((yy - y) ** 2 + (xx - x) ** 2) / (2 * 1.5**2))).astype(np.float32)
    dense = np.array(quality.dense_stars(raw, True))
    stored = np.array(quality.analyze_frame(raw, True)["stars"])
    assert len(dense) > len(stored) == 100
    # gleiche Erkennung und Koordinaten: die gespeicherten Sterne stehen (bis auf die Rundung) auch in der dichten Liste
    from scipy.spatial import cKDTree
    d, _ = cKDTree(dense[:, :2]).query(stored[:, :2])
    assert np.mean(d < 0.02) > 0.95


def test_global_setting_api_and_object_override(tmp_path, monkeypatch):
    """Einstellungen → Bilder: global standardmäßig an, gespeichert in settings.json (übersteht den Neustart);
    die Objektoption (an/aus) hat Vorrang, „global“ folgt der Einstellung."""
    from fastapi.testclient import TestClient
    from astroflow.server import create_app
    monkeypatch.delenv("ASTROFLOW_DISTORTION", raising=False)
    monkeypatch.setattr(R.DISTORTION, "mode", "auto")         # stellt den Prozesszustand nach dem Test wieder her
    s = load_settings(tmp_path / "d")
    s.auto = False
    demo.generate(s.input_dir / "n", n_lights=4, n_darks=0, n_flats=0)
    with TestClient(create_app(s, only=("stacking",))) as c:
        assert c.get("/api/stacking/distortion").json()["mode"] == "auto"
        c.post("/api/stacking/scan")
        pid = c.get("/api/stacking/projects").json()[0]["id"]
        p = c.get(f"/api/stacking/projects/{pid}").json()
        assert p["options"].get("distortion", "global") == "global"
        assert p["distortion"] == {"global": "auto", "stack": None}
        assert c.post("/api/stacking/distortion", json={"mode": "quatsch"}).status_code == 400
        assert c.post("/api/stacking/distortion", json={"mode": "off"}).json()["mode"] == "off"
        assert c.get(f"/api/stacking/projects/{pid}").json()["distortion"]["global"] == "off"
        c.patch(f"/api/stacking/projects/{pid}", json={"options": {"distortion": "auto"}})
        opt = c.get(f"/api/stacking/projects/{pid}").json()["options"]
        assert R.distortion_mode(opt["distortion"]) == "auto" and R.distortion_mode("global") == "off"
    saved = json.loads((s.data_dir / "settings.json").read_text(encoding="utf-8"))
    assert saved["distortion"]["mode"] == "off"
    R.DISTORTION.mode = "auto"                                # Neustart: gespeicherter Wert gewinnt
    s2 = load_settings(tmp_path / "d")
    s2.auto = False
    with TestClient(create_app(s2, only=("stacking",))) as c:
        assert c.get("/api/stacking/distortion").json()["mode"] == "off"
