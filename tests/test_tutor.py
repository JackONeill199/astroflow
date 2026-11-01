"""Bild-Tutor: Farbverlauf-Erkennung und -Ausgleich, Geräte, Nachtbedingungen, Befunde, API."""
import json
import time

import numpy as np
from fastapi.testclient import TestClient

from astroflow.core.config import load_settings
from astroflow.modules.stacking import demo, develop, fits_io, processing, tutor
from astroflow.server import create_app


def _sky(red_right: float = 0.0, seed: int = 0):
    """Neutraler Himmel (0,04) mit Galaxie; optional rötlicher Verlauf zum rechten Rand (Array-Orientierung)."""
    rng = np.random.default_rng(seed)
    img = np.full((3, 300, 400), 0.04, np.float32) + rng.normal(0, 0.002, (3, 300, 400)).astype(np.float32)
    yy, xx = np.mgrid[0:300, 0:400]
    img += (0.45 * np.exp(-((xx - 200) ** 2 + (yy - 150) ** 2) / 2000) * np.array([1.0, 0.6, 0.5])[:, None, None]).astype(np.float32)
    img[0] += (red_right * np.clip((xx - 200) / 200, 0, 1)).astype(np.float32)
    return img


def test_color_gradient_detected_only_where_sky_must_be_neutral():
    clean = develop.assess(_sky())
    assert "color_gradient" not in {i["code"] for i in clean["issues"]} and clean["v"] == develop.QC_VERSION
    q = develop.assess(_sky(0.04))
    issue = next(i for i in q["issues"] if i["code"] == "color_gradient")
    assert "rötlich" in issue["text"] and "rechts" in issue["text"] and q["score"] < 10
    # in der Milchstraße (evtl. echter Hα-Nebel) erst bei deutlich stärkerem Verlauf
    assert "color_gradient" not in {i["code"] for i in develop.assess(_sky(0.04), neutral_sky=False)["issues"]}
    assert develop.corrections(q, {"denoise": 0.3, "chroma_denoise": 1.5, "saturation": 1.3})["color_gradient"] == 2


def test_remove_color_gradient_keeps_luminance():
    rng = np.random.default_rng(1)
    img = np.full((3, 240, 320), 0.02, np.float32) + rng.normal(0, 0.001, (3, 240, 320)).astype(np.float32)
    xx = np.mgrid[0:240, 0:320][1] / 320
    img[0] += (0.01 * xx).astype(np.float32)
    out = processing.remove_color_gradient(img, 2)
    d = out[0] - out[1]
    assert abs(d[:, -40:].mean() - d[:, :40].mean()) < 0.001              # Verlauf weg
    L0, L1 = processing.luminance(img), processing.luminance(out)
    assert abs(float((L1 - L0).mean())) < 2e-4                              # Helligkeit bleibt


def test_detect_device_and_site_header():
    assert tutor.detect_device(["stellina-a1b2c3"])["id"] == "stellina"
    assert tutor.detect_device([None], (2080, 3072))["guessed"] is True
    assert tutor.detect_device(["Seestar S50"])["name"] == "ZWO Seestar S50"
    assert tutor.detect_device(["Vespera"])["id"] == "vespera"
    cam = tutor.detect_device(["ZWO ASI2600MC Pro"])
    assert cam["id"] == "camera" and cam["name"] == "ZWO ASI2600MC Pro"
    from astropy.io import fits
    h = fits.Header({"SITELAT": "+49 06 00", "SITELONG": 8.5})
    assert fits_io._site(h) == [49.1, 8.5]
    assert fits_io._site(fits.Header({"SITELAT": 0, "SITELONG": 0})) is None


def _frames(start: str, n: int = 60, step_s: int = 30, bg_end: float = 1.0):
    t0 = np.datetime64(start)
    return [{"date": str(t0 + np.timedelta64(i * step_s, "s")), "night": start[:10], "status": "selected", "reason": "",
             "n_stars": 150, "fwhm": 5.0, "ecc": 0.5, "bg": 1000 * (1 + (bg_end - 1) * i / n)} for i in range(n)]


def test_summer_twilight_and_humidity_findings():
    # NGC 891 am 24./25.06.2022 von 49° Nord (wie beim Nutzer): keine astronomische Nacht, Himmel wird heller
    fr = _frames("2022-06-24T01:11:00", bg_end=3.0)
    for f in fr:
        f["night"] = "2022-06-24"
    nights = tutor.night_conditions(fr, 35.64, 42.35, [49.1, 8.5],
                                    {"2022-06-24": {"cloud": 5, "cloud_max": 10, "humidity": 92, "temp_min": 15.9, "dew_spread": 1.3,
                                                    "wind": 11, "gust": 24}})
    n = nights[0]
    assert n["sun"]["dark"] == 0 and -15 < n["sun"]["alt_min"] < -10 and n["frames_trend"]["bg_trend"] > 2
    q = {"score": 8.9, "issues": [{"code": "color_gradient", "text": "Farbverlauf im Hintergrund: rötlich nach rechts"}]}
    ctx = {"exposure_s": 1310, "kind": "galaxy", "latitude": 49.1}
    stellina = tutor.findings(q, nights, tutor.detect_device(["stellina-x"]), ctx)
    codes = [f["code"] for f in stellina]
    assert codes[0] == "twilight" and "color_gradient" in codes
    assert "dew" not in codes                                   # Stellina hat eine Tauheizung
    assert "Dämmerung" in next(f for f in stellina if f["code"] == "color_gradient")["text"]
    seestar = [f["code"] for f in tutor.findings(q, nights, tutor.detect_device(["Seestar S50"]), ctx)]
    assert "dew" in seestar


def test_moon_without_site():
    # Vollmond 2022-07-13: ohne Standort nur Phase und Abstand
    nights = tutor.night_conditions(_frames("2022-07-13T22:00:00"), 280.0, -20.0, None)
    assert nights[0]["moon"]["illum"] > 0.95 and "sun" not in nights[0] and "sep" in nights[0]["moon"]
    f = tutor.findings({"score": 10, "issues": []}, nights, tutor.detect_device(["Vespera"]), {"kind": "galaxy"})
    moon = next(x for x in f if x["code"] == "moon")
    assert any("Dual-Band" in t for t in moon["tips"])


def test_tutor_api(tmp_path, monkeypatch):
    s = load_settings(tmp_path / "d")
    s.auto = True
    demo.generate(s.input_dir / "n", n_lights=14, n_darks=0, n_flats=0)
    with TestClient(create_app(s)) as c:
        c.post("/api/stacking/scan")
        for _ in range(120):
            p = c.get("/api/stacking/projects").json()[0]
            if p["result"] or (p["job"] and p["job"]["state"] == "error"):
                break
            time.sleep(0.5)
        rid = p["result"]["id"]
        assert c.post("/api/stacking/tutor", json={"lat": "abc"}).status_code == 400
        t = c.post("/api/stacking/tutor", json={"mode": "experte", "lat": "49,1", "lon": "8,5", "weather": False}).json()
        assert t["mode"] == "experte" and t["lat"] == 49.1 and t["llm_ready"] is False
        rep = c.get(f"/api/stacking/results/{rid}/tutor").json()
        assert rep["available"] and rep["mode"] == "experte" and rep["site"] == "settings" and rep["nights"]
        assert rep["findings"] and all({"code", "level", "title", "text", "tips"} <= set(f) for f in rep["findings"])
        assert "sun" in rep["nights"][0]
        assert c.post(f"/api/stacking/results/{rid}/tutor/ki").status_code == 400          # kein KI-Anbieter
        # KI mit Attrappe
        from astroflow.modules.stacking import reviewer
        monkeypatch.setattr(reviewer, "chat", lambda cfg, system, user: json.dumps(
            {"zusammenfassung": "Rötlicher Rand rechts.", "gesehen": ["rechte Hälfte rötlich"], "tipps": ["mondlose Nacht"]}))
        c.post("/api/stacking/reviewer", json={"api_key": "x", "model": "m"})
        c.post("/api/stacking/tutor", json={"llm_provider": "openrouter"})
        ki = c.post(f"/api/stacking/results/{rid}/tutor/ki").json()
        assert ki["seen"] == ["rechte Hälfte rötlich"]
        assert c.get(f"/api/stacking/results/{rid}/tutor").json()["ki"]["summary"] == "Rötlicher Rand rechts."


def test_report_uses_only_frames_of_the_result():
    fr = _frames("2022-06-24T22:00:00", n=10) + _frames("2022-08-01T22:00:00", n=10)
    for f in fr[10:]:
        f["night"] = "2022-08-01"
    assert {f["night"] for f in tutor.result_frames(fr, {"nights": ["2022-06-24"]}, 0)} == {"2022-06-24"}
    # ältere Ergebnisse ohne stats.nights: nur Aufnahmen vor dem Ergebnis, keine noch unverarbeiteten
    created = np.datetime64("2022-07-01T00:00:00").astype("datetime64[s]").astype(int)
    fr[0]["status"] = "new"
    old = tutor.result_frames(fr, {}, float(created))
    assert len(old) == 9 and all(f["night"] == "2022-06-24" for f in old)


def test_llm_config_keeps_custom_server_when_reviewer_off():
    from astroflow.modules.stacking.reviewer import ReviewerConfig
    cfg = {**tutor.DEFAULTS, "llm_provider": "lmstudio", "llm_model": "qwen-vl"}
    off = ReviewerConfig(provider="off", base_url="http://pc:1234/v1", model="x")
    assert tutor.llm_config(cfg, off).base_url == "http://pc:1234/v1"
    other = ReviewerConfig(provider="openrouter", base_url="https://openrouter.ai/api/v1", model="x", api_key="k")
    assert tutor.llm_config(cfg, other).base_url == ""          # LM-Studio-Standard statt OpenRouter-URL
