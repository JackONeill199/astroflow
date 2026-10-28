"""Fotometrische Farbkalibrierung (Gaia) und StarNet-Installation."""
import zipfile

import numpy as np
import pytest
from fastapi.testclient import TestClient

from astroflow.modules.stacking import photometry, stars
from astroflow.core.config import load_settings
from astroflow.server import create_app

H, W = 400, 600
HDR = {"CTYPE1": "RA---TAN", "CTYPE2": "DEC--TAN", "CRVAL1": 300.0, "CRVAL2": 40.0, "CRPIX1": W / 2 + 0.5,
       "CRPIX2": H / 2 + 0.5, "CD1_1": -1.5 / 3600, "CD1_2": 0.0, "CD2_1": 0.0, "CD2_2": 1.5 / 3600}


def _field(gain_r, gain_b, n=250, seed=0):
    """Sterne mit Katalogfarbe BP−RP; Kamera mit verfälschtem Weißabgleich (gain_r, gain_b) gegenüber „wahr“."""
    rng = np.random.default_rng(seed)
    w = photometry.wcs_from(HDR)
    x, y = rng.uniform(20, W - 20, n), rng.uniform(20, H - 20, n)
    ra, dec = w.pixel_to_world_values(x, y)
    bprp = rng.uniform(0.2, 1.8, n)
    img = np.full((3, H, W), 50.0, np.float32) + rng.normal(0, 1, (3, H, W)).astype(np.float32)
    yy, xx = np.mgrid[0:H, 0:W]
    for xi, yi, c, f in zip(x, y, bprp, rng.uniform(800, 4000, n)):
        # wahres Farbverhältnis: G2V (0,82) ist neutral, rötere Sterne mehr R, weniger B
        r, b = np.exp(0.6 * (c - 0.82)), np.exp(-0.25 * (c - 0.82))
        psf = f * np.exp(-((xx - xi) ** 2 + (yy - yi) ** 2) / (2 * 1.6 ** 2)) / (2 * np.pi * 1.6 ** 2)
        img[0] += (psf * r * gain_r).astype(np.float32)
        img[1] += psf.astype(np.float32)
        img[2] += (psf * b * gain_b).astype(np.float32)
    return img, np.c_[ra, dec, bprp]


def test_pcc_makes_g2v_white():
    img, cat = _field(gain_r=1.6, gain_b=0.7)            # Kamera: zu rot, zu wenig blau
    gains, info = photometry.pcc_gains(img, np.ones((H, W), np.float32), HDR, cat, fwhm=3.8)
    assert info["sterne"] >= 100
    assert abs(gains[0] * 1.6 - 1) < 0.05 and abs(gains[2] * 0.7 - 1) < 0.05 and gains[1] == 1.0


def test_pcc_refuses_wrong_wcs():
    img, cat = _field(1.0, 1.0)
    wrong = {**HDR, "CRVAL1": 301.0}                        # Lösung passt nicht zum Bild
    with pytest.raises(ValueError):
        photometry.pcc_gains(img, np.ones((H, W), np.float32), wrong, cat, fwhm=3.8)


def test_gaia_cache_avoids_network(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(photometry, "_esa", lambda *a: calls.append(a) or [[300.0, 40.0, 0.8]])
    photometry.gaia_stars(300, 40, 0.2, tmp_path)
    photometry.gaia_stars(300, 40, 0.2, tmp_path)
    assert len(calls) == 1                                  # zweites Mal aus dem lokalen Zwischenspeicher


def test_starnet_install_zip(tmp_path, monkeypatch):
    monkeypatch.setattr(stars, "selftest", lambda exe: None)
    z = tmp_path / "StarNet2_CLI_Win.zip"
    with zipfile.ZipFile(z, "w") as f:
        f.writestr("StarNet2/starnet2.exe", b"MZ")
        f.writestr("StarNet2/StarNet2_weights.pb", b"x")
    exe = stars.install_zip(z, tmp_path / "tools" / "starnet")
    assert exe.endswith("starnet2.exe") and (tmp_path / "tools" / "starnet" / "StarNet2" / "StarNet2_weights.pb").exists()
    bad = tmp_path / "bad.zip"
    with zipfile.ZipFile(bad, "w") as f:
        f.writestr("../../evil.exe", b"x")
    with pytest.raises(ValueError):
        stars.install_zip(bad, tmp_path / "tools" / "starnet2")
    empty = tmp_path / "empty.zip"
    with zipfile.ZipFile(empty, "w") as f:
        f.writestr("readme.txt", b"x")
    with pytest.raises(ValueError, match="kein StarNet"):
        stars.install_zip(empty, tmp_path / "tools" / "starnet3")


def test_starnet_api(tmp_path, monkeypatch):
    monkeypatch.setattr(stars, "selftest", lambda exe: None)
    monkeypatch.setattr(stars.shutil, "which", lambda name: None)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "nichts"))
    s = load_settings(tmp_path / "d")
    s.auto = False
    with TestClient(create_app(s)) as c:
        assert c.get("/api/stacking/starnet").json()["found"] is False
        z = tmp_path / "sn.zip"
        with zipfile.ZipFile(z, "w") as f:
            f.writestr("starnet2.exe", b"MZ")
        r = c.post("/api/stacking/starnet/install", files={"file": ("sn.zip", z.read_bytes(), "application/zip")}).json()
        assert r["found"] and r["installed_here"]
        assert c.post("/api/stacking/starnet", json={"path": str(tmp_path / "fehlt.exe")}).status_code == 400
        assert c.delete("/api/stacking/starnet").json()["found"] is False
    stars.STARNET.path = ""
