"""Geräte-Tabelle (devices.json) und Bildfelddrehung (devices.py)."""
import math

import numpy as np

from astroflow.modules.stacking import deepsky, devices, planner, tutor


def test_table_values_have_status_and_source():
    for key, raw in devices.table().items():
        assert raw["name"]
        for f in devices.VALUE_FIELDS:
            assert f in raw, (key, f)
            v = raw[f]
            if isinstance(v, dict):
                assert v["status"] in ("gesichert", "vorschlag") and v["quelle"], (key, f)
        d = devices.get(key)
        assert d["mount"] in (None, "altaz", "eq") and d["derotator"] in (None, "none", "software", "optical")


def test_user_decisions_12_11_2026():
    st, se, v2 = devices.get("stellina"), devices.get("seestar"), devices.get("vespera2")
    assert st["derotator"] == "optical" and st["zenith_limit"] is None and st["sources"]["zenith_limit"]["status"] == "gesichert"
    assert "Nutzer" in st["sources"]["zenith_limit"]["quelle"]
    assert se["zenith_limit"] == 70 and se["sources"]["zenith_limit"]["status"] == "vorschlag" and se["eq_mode"]
    assert v2["zenith_limit"] == 75 and v2["sources"]["zenith_limit"]["status"] == "vorschlag"
    assert devices.profile({"id": "stellina"})["zenith_limit"] is None
    assert devices.profile({"id": "dwarf"})["zenith_limit"] == devices.DEFAULT_ZENITH       # ohne Wert: bisherige 80°


def test_views_replace_the_old_lists():
    assert set(tutor.DEVICES) == {"stellina", "vespera", "seestar", "dwarf", "evscope", "camera"}
    assert tutor.DEVICES["stellina"]["dew_heater"] and not tutor.DEVICES["camera"]["alt_az"]
    assert set(planner.DEVICES) == {"stellina", "vespera", "vespera2", "seestar", "seestar30"}
    assert planner.DEVICES["vespera2"]["mag_id"] == "vespera" and planner.DEVICES["seestar"]["eq_mode"]
    assert deepsky.MAG_LIMITS["stellina"] == 11.5 and deepsky.MAG_LIMITS["evscope"] == 12.0


def test_profiles():
    assert devices.profile({"id": "vespera"})["mode"] == "software"
    assert devices.profile({"id": "stellina"})["mode"] == "derot"
    assert devices.profile({"id": "seestar", "eq": True})["mode"] == "eq"
    assert devices.profile({"id": "camera", "alt_az": False})["mode"] == "none"
    assert devices.profile({"id": "unbekannt", "alt_az": True})["mode"] == "legacy"
    lib = devices.profile({"id": "library", "lib_id": "seestar", "exptime": 20})
    assert lib["mode"] == "software" and lib["exp"] == 20 and lib["eq_mode"]
    assert math.isclose(devices.profile({"id": "stellina"})["radius_px"], math.hypot(3072, 2080) / 2)


def test_rotation_rate_formula():
    # Meridian im Süden, 45° Höhe, 50° Breite: ω·cos φ / cos h
    r = float(devices.rotation_rate(45.0, 180.0, 50.0))
    assert math.isclose(r, devices.OMEGA * math.cos(math.radians(50)) / math.cos(math.radians(45)), rel_tol=1e-9)
    assert float(devices.rotation_rate(45.0, 90.0, 50.0)) < 1e-12                       # Ost/West: keine Drehung
    assert devices.rotation_rate(85.0, 180.0, 50.0) > 5 * devices.rotation_rate(30.0, 180.0, 50.0)
    # T_max: 1 Pixel am Rand – ω in rad/s (nicht °/s), sonst wäre die Zeit 57× zu kurz
    p = devices.profile({"id": "seestar"})
    t = float(devices.max_exposure(45.0, 180.0, 50.0, p))
    w = math.radians(r / 60)
    assert math.isclose(t, 1 / (w * p["radius_px"]), rel_tol=1e-9) and 10 < t < 60


def test_weights_by_mode():
    alt = np.array([40.0, 70.0, 78.0, 86.0])
    az = np.full(4, 180.0)
    w = {m: devices.weights(alt, az, 50.0, devices.profile(d)) for m, d in
         (("soft", {"id": "seestar"}), ("derot", {"id": "stellina"}), ("eq", {"id": "seestar", "eq": True}),
          ("legacy", {"id": "x", "alt_az": True}))}
    assert np.all(w["eq"] == 1)
    assert list(w["legacy"]) == [1, 1, 1, devices.ZENITH_WEIGHT]
    assert w["soft"][0] == 1 and w["soft"][2] < 0.4 and np.all(np.diff(w["soft"]) <= 0)
    assert np.all(w["derot"][:3] == 1) and w["derot"][3] >= w["soft"][3]
    assert np.all(devices.weights(alt, np.full(4, 90.0), 50.0, devices.profile({"id": "vespera"}))[:3] == 1)  # Ost/West


def test_detected_model_and_exposure_reach_the_profile(tmp_path):
    """Vespera II / Seestar S30 aus der Erkennung nutzen ihr eigenes Profil; Heute Nacht übernimmt die echte Belichtung."""
    import json
    from astroflow.modules.stacking import tonight
    from astroflow.modules.stacking.db import DB
    v2, s30 = tutor.detect_device(["Vespera II"]), tutor.detect_device(["Seestar S30"])
    assert v2["id"] == "vespera" and v2["model"] == "vespera2" and s30["model"] == "seestar30"
    assert "model" not in tutor.detect_device(["Vespera Pro"])                     # Pro: kein Tabelleneintrag → Familie
    p = devices.profile({"id": "vespera", "model": "vespera2"})
    assert p["zenith_limit"] == 75 and math.isclose(p["radius_px"], math.hypot(3840, 2160) / 2)
    db = DB(tmp_path / "a.db")
    db.x("INSERT INTO projects(id, key, name) VALUES(1, 'k', 'M31')")
    db.x("INSERT INTO frames(id, project_id, kind, night, info) VALUES('f', 1, 'light', '2026-10-05', ?)",
         (json.dumps({"instrument": "Vespera II", "exptime": 20.0}),))
    _, dev = tonight.library(db)
    prof = devices.profile(dev)
    assert dev["model"] == "vespera2" and prof["exp"] == 20.0 and prof["zenith_limit"] == 75
    cam = {**dev, "id": "camera", "model": None, "alt_az": True}                  # eigene Kamera → „library“ mit Belichtung
    lib = planner.device_options(cam)[0]
    assert lib["id"] == "library" and devices.profile(lib)["exp"] == 20.0


def test_vespera_model_not_from_serial_number():
    """INSTRUME „vespera-<hex>“: eine „2“ in der Seriennummer macht keine Vespera II; das Bildformat entscheidet."""
    from astroflow.modules.stacking import tutor
    serial = "vespera-2a4f0c2b7e21"                                   # erfunden
    d = tutor.detect_device([serial], (1080, 1920))
    assert d["id"] == "vespera" and d["name"] == "Vaonis Vespera" and "model" not in d
    assert "model" not in tutor.detect_device([serial])
    assert tutor.detect_device([serial], (3, 2160, 3840))["model"] == "vespera2"
    assert tutor.detect_device([serial], (3536, 3536))["name"] == "Vaonis Vespera Pro"
    assert tutor.detect_device(["Vespera II"], (1080, 1920))["model"] == "vespera2"     # ausdrückliches Wort gewinnt
    assert tutor.detect_device(["Vespera 2"])["model"] == "vespera2"
    assert tutor.detect_device(["Vespera Pro"])["name"] == "Vaonis Vespera Pro"
