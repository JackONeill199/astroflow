"""Lineare (ungestreckte) TIFF/PNG fertiger Bilder: erkennen, Vorschau strecken, wie einen Stack entwickeln."""
import io

import numpy as np
import pytest
import tifffile
from fastapi.testclient import TestClient
from PIL import Image

from astroflow.core.config import load_settings
from astroflow.modules.stacking import picture_import as pi
from astroflow.server import create_app

H, W = 240, 320


def star_field(seed=3, sky=(1300, 1100, 1600), n=60):
    """Lineares RGB-Feld in ADU wie ein DSS-/Siril-Stack: Himmel ~2 % des Wertebereichs, Sterne, Rauschen."""
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[:H, :W]
    a = np.empty((H, W, 3), np.float32)
    a[:] = np.array(sky, np.float32)
    for _ in range(n):
        y, x, f = rng.uniform(10, H - 10), rng.uniform(10, W - 10), rng.uniform(2000, 30000)
        a += (f * np.exp(-((yy - y) ** 2 + (xx - x) ** 2) / (2 * 1.4 ** 2)))[..., None] * np.array([1.0, 0.95, 0.9])
    a += rng.normal(0, 25, a.shape)
    return np.clip(a, 0, 65535)


def tiff_bytes(a, **kw):
    buf = io.BytesIO()
    tifffile.imwrite(buf, a, **kw)
    return buf.getvalue()


def app_at(tmp_path):
    s = load_settings(tmp_path / "data")
    s.auto = False
    return create_app(s)


def upload(c, name, data):
    d = c.post("/api/stacking/upload", files={"files": (name, data)}).json()
    assert not d["errors"], d
    return c.get(f"/api/stacking/projects/{d['pictures'][0]['id']}").json()


def preview_of(c, rid):
    return np.asarray(Image.open(io.BytesIO(c.get(f"/api/stacking/results/{rid}/png").content)).convert("RGB"), np.float32)


def test_linear_16bit_tiff_is_recognized_stretched_and_developed(tmp_path):
    data = tiff_bytes(star_field().astype(np.uint16), photometric="rgb")
    app = app_at(tmp_path)
    eng = app.state.modules["stacking"].eng
    with TestClient(app) as c:
        p = upload(c, "Stacked 155 Frames.tif", data)
        lin = p["result"]["stats"]["imported"]["linear"]
        assert lin["median"] < 0.05 and not lin.get("bayer") and p["linear"]["rid"] == p["result"]["id"]
        prev = preview_of(c, p["result"]["id"])
        assert np.median(prev) > 20                     # nicht mehr schwarz (ungestreckt wäre der Himmel ≈ 5/255)
        # Hintergrund je Kanal gestreckt: kein einfarbiger Farbstich in der Vorschau
        med = np.median(prev.reshape(-1, 3), axis=0)
        assert med.max() - med.min() < 8
        assert c.get(f"/api/stacking/results/{p['result']['id']}/original").content == data

        rid = eng.pl.develop(p["id"], {"mode": "develop"}, start=0.05)
        q = c.get(f"/api/stacking/projects/{p['id']}").json()
        assert [r["id"] for r in q["results"]][:2] == [rid, p["result"]["id"]]
        st = q["results"][0]["stats"]
        assert st["developed"]["from"] == p["result"]["id"] and st["imported"]["filename"] == "Stacked 155 Frames.tif"
        assert st["stars"] > 10
        out = np.asarray(Image.open(io.BytesIO(c.get(f"/api/stacking/results/{rid}/jpg").content)), np.float32)
        assert out.ndim == 3 and out.mean() > 15 and np.percentile(out, 99.9) > 150    # Himmel sichtbar, Sterne hell
        for kind in ("tif", "fits"):
            assert c.get(f"/api/stacking/results/{rid}/{kind}").status_code == 200
        # das Original bleibt Quelle, auch für ein weiteres Neu-Entwickeln
        assert c.get(f"/api/stacking/projects/{p['id']}").json()["linear"]["rid"] == p["result"]["id"]


def test_develop_endpoint_queues_job_for_linear_picture(tmp_path, monkeypatch):
    app = app_at(tmp_path)
    eng = app.state.modules["stacking"].eng
    jobs = []
    monkeypatch.setattr(eng, "enqueue", lambda pid, opts=None: jobs.append((pid, opts)) or 7)
    with TestClient(app) as c:
        p = upload(c, "M81.tif", tiff_bytes(star_field().astype(np.uint16), photometric="rgb"))
        assert c.post(f"/api/stacking/projects/{p['id']}/process", json={"mode": "develop"}).json() == {"job": 7}
        assert jobs == [(p["id"], {"mode": "develop"})]
        for options in ({}, {"restack": True}, {"mode": "develop", "restack": True}):
            assert c.post(f"/api/stacking/projects/{p['id']}/process", json=options).status_code == 409


@pytest.mark.parametrize("bits", [8, 16])
def test_stretched_picture_stays_unchanged(tmp_path, bits):
    rng = np.random.default_rng(1)
    a = np.clip(rng.normal(0.25, 0.05, (H, W, 3)), 0, 1)       # gestreckt: Himmel bei ~25 %, breite Verteilung
    a[40:80, 40:120] = 0.9
    arr = (a * 255).astype(np.uint8) if bits == 8 else (a * 65535).astype(np.uint16)
    with TestClient(app_at(tmp_path)) as c:
        p = upload(c, "fertig.tif", tiff_bytes(arr, photometric="rgb"))
        assert "linear" not in p["result"]["stats"]["imported"] and p["linear"] is None
        prev = preview_of(c, p["result"]["id"])
        expect = arr.astype(np.float32) * (1 if bits == 8 else 255 / 65535)
        assert np.abs(prev - expect).max() <= 1
        assert c.post(f"/api/stacking/projects/{p['id']}/process", json={"mode": "develop"}).status_code == 409


def mosaic(pattern):
    """Undebayertes Einzelbild: Farbfeld durch eine Bayer-Maske (Himmel R/G/B verschieden hell)."""
    rgb = star_field(seed=5, sky=(1800, 2600, 1200), n=40)
    raw = np.empty((H, W), np.float32)
    for i, ch in enumerate(pattern):
        raw[i // 2::2, i % 2::2] = rgb[i // 2::2, i % 2::2, "RGB".index(ch)]
    return raw.astype(np.uint16)


@pytest.mark.parametrize("pattern", ["RGGB", "GRBG"])
def test_bayer_detection(pattern):
    raw = mosaic(pattern)
    assert pi.bayer_pattern(pi.normalized(raw)) == pattern
    info = pi.linear_info(pi.normalized(raw), 16)
    assert info["bayer"] == pattern and info["single"] and info["channels"] == 3
    gray = star_field()[..., 1].astype(np.uint16)              # Mono ohne Farbmaske
    assert pi.bayer_pattern(pi.normalized(gray)) is None


def test_bayer_tiff_is_debayered_and_developed(tmp_path):
    app = app_at(tmp_path)
    eng = app.state.modules["stacking"].eng
    with TestClient(app) as c:
        p = upload(c, "img-0049.tif", tiff_bytes(mosaic("RGGB"), photometric="minisblack"))
        lin = p["linear"]
        assert lin["bayer"] == "RGGB" and lin["single"]
        prev = preview_of(c, p["result"]["id"])
        # debayert: kein 2×2-Schachbrett mehr (gerade und ungerade Zeilen/Spalten gleich hell)
        for k in range(3):
            ch = prev[..., k]
            assert abs(ch[:, 0::2].mean() - ch[:, 1::2].mean()) < 3 and abs(ch[0::2].mean() - ch[1::2].mean()) < 3
        assert np.median(prev) > 20
        rid = eng.pl.develop(p["id"], {"mode": "develop"}, start=0.05)
        st = c.get(f"/api/stacking/projects/{p['id']}").json()["results"][0]["stats"]
        assert st["developed"]["single"] and rid
        out = np.asarray(Image.open(io.BytesIO(c.get(f"/api/stacking/results/{rid}/jpg").content)), np.float32)
        assert out.ndim == 3 and out.mean() > 15


def test_linear_16bit_png(tmp_path):
    g = star_field()[..., 1].astype(np.uint16)
    buf = io.BytesIO()
    Image.fromarray(g).save(buf, "PNG")
    with TestClient(app_at(tmp_path)) as c:
        p = upload(c, "M81.png", buf.getvalue())
        assert p["linear"] and p["linear"]["channels"] == 1
        assert np.median(preview_of(c, p["result"]["id"])) > 20


def test_edge_coverage_cuts_noisy_border():
    m = np.moveaxis(star_field(), -1, 0)
    rng = np.random.default_rng(0)
    m[:, -12:] = rng.uniform(0, 65535, (3, 12, W))              # DSS-Rand: nur von wenigen Aufnahmen gedeckt
    cov = pi.edge_coverage(m)
    assert cov[: H - 12].all() and not cov[H - 12:].any()


@pytest.mark.parametrize("orientation", range(1, 9))
def test_read_linear_follows_exif_orientation(tmp_path, orientation):
    """Entwickeln nutzt dieselbe Ausrichtung wie die Vorschau (ImageOps.exif_transpose)."""
    from PIL import ImageOps
    a = (np.arange(6 * 10, dtype=np.uint16).reshape(6, 10) * 1000)
    f = tmp_path / "bild.png"
    im = Image.fromarray(a)
    ex = im.getexif()
    ex[274] = orientation
    im.save(f, exif=ex.tobytes())
    want = np.asarray(ImageOps.exif_transpose(Image.open(f)), np.float32)
    got = pi.read_linear(f, {})[0, ::-1]                      # zurück in Bildorientierung
    assert got.shape == want.shape and np.allclose(got / 65535, want / 65535, atol=1e-4)


def test_original_keeps_object_labels_after_develop(tmp_path):
    """Die Stack-WCS einer entwickelten Version darf die Objektnamen des Originals (Lösung der Anzeigeversion) nicht verdrängen."""
    app = app_at(tmp_path)
    eng = app.state.modules["stacking"].eng
    with TestClient(app) as c:
        p = upload(c, "M81.tif", tiff_bytes(star_field().astype(np.uint16), photometric="rgb"))
        sol = {"ra": 148.9, "dec": 69.07, "fov_w": 0.4, "fov_h": 0.3, "scale": 4.0, "rotation": 0.0, "width": W, "height": H,
               "solver": "astap", "seconds": 1.0, "objects": [{"name": "M81", "id": "M81", "kind": "galaxy", "kind_label": "Galaxie",
                                                               "size": 20.0, "x": 160.0, "y": 120.0, "radius": 30.0}]}
        eng.picture_solve._apply(p["id"], sol)
        eng.pl.develop(p["id"], {"mode": "develop"}, start=0.05)
        (eng.s.work_dir / f"p{p['id']}" / "wcs.json").write_text('{"key": "x", "wcs": {}}')     # wie nach Gaia-Kalibrierung
        objs = c.get(f"/api/stacking/results/{p['result']['id']}/objects").json()
        assert [o["name"] for o in objs["objects"]] == ["M81"]
