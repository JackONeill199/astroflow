"""SkyTrace: ehrliche Einstufung bewegter Spuren (Satellit oder Flugzeug / wahrscheinlich Flugzeug / langsamer Beweger).

Synthetische Kandidaten mit erfundenen Werten; keine Engine-Pakete nötig (läuft auch unter Linux ohne OpenCV/PyTorch).
"""
import sys
from pathlib import Path

from astroflow.modules.skytrace import motion_class as mc
from astroflow.modules.skytrace.achievements import classify

ENGINE = Path(__file__).parents[1] / "astroflow" / "modules" / "skytrace" / "engine"
PLAUS = {"field_of_view_deg": [1.6, 0.9], "analysis_size": [960, 540]}       # ~6″ je Analysepixel


def sat(status="checked_no_match", **match):
    result = {"provider": "satellites", "status": status, "checked_trajectories": 60, "matches": []}
    if match:
        result["matches"] = [dict({"name": "TESTSAT R/B", "norad_id": 99999}, **match)]
    return result


def cand(cid="K0001", kind="trail", hits=(10,), segment=((100, 100), (400, 300)), catalogs=(), **extra):
    return dict({"id": cid, "kind": kind, "hit_indices": list(hits), "evidence": {"segment": [list(p) for p in segment]},
                 "catalogs": list(catalogs), "plausibility": PLAUS}, **extra)


def test_parallele_positionslichter_sind_wahrscheinlich_flugzeug():
    """Zwei parallele Lichter in derselben Aufnahme (Flügelspitzen) – Befund M2 2026-11-13, Bild 397."""
    a = cand("K0007", segment=((370, 522), (33, 141)))
    b = cand("K0008", segment=((288, 522), (32, 230)))
    other = cand("K0009", hits=(11,), segment=((300, 522), (40, 220)))         # andere Aufnahme: kein Partner
    mc.annotate([a, b, other])
    assert a["motion_class"]["verdict"] == "likely_plane" and a["motion_class"]["features"]["twin"]["partner"] == "K0008"
    assert b["motion_class"]["verdict"] == "likely_plane"
    assert other["motion_class"]["verdict"] is None                           # Einzelbild-Spur ohne Merkmal bleibt offen
    assert "Flügelspitzen" in " ".join(a["motion_class"]["reasons"])


def test_quer_und_weit_auseinander_ist_kein_paar():
    a = cand("K1", segment=((100, 100), (400, 300)))
    crossing = cand("K2", segment=((100, 300), (400, 100)))
    far = cand("K3", segment=((100, 400), (400, 600)))
    mc.annotate([a, crossing, far])
    assert all(c["motion_class"]["verdict"] is None for c in (a, crossing, far))


def test_blinkspur_gewinnt_gegen_grob_passende_bahn():
    """NGC 2244 K0004: 6 Blitze, PSLV R/B nur grob (3′, 4,8 s außerhalb, 12° abweichend) → wahrscheinlich Flugzeug."""
    weak = sat("compatible", mean_offset_arcsec=187.7, time_offset_s=4.82, alignment_degrees=12.2)
    c = cand(kind="plane", blink={"flashes": 6, "period_pixels": 225, "form": "gestrichelt"}, catalogs=[weak])
    result = mc.assess(c)
    assert result["verdict"] == "likely_plane" and result["name"] == "Wahrscheinlich Flugzeug"
    assert "grob" in " ".join(result["reasons"])
    assert classify(dict(c, motion_class=result))[0] == "plane"


def test_eng_passende_bahn_bleibt_satellit():
    strong = sat("compatible", mean_offset_arcsec=2.7, time_offset_s=0.0, alignment_degrees=None)
    linked = cand(kind="plane", linked_track={"count": 11, "span_seconds": 237, "rate_pixels_s": 3.6}, catalogs=[strong])
    assert mc.assess(linked)["verdict"] == "satellite"
    blink = cand(kind="plane", blink={"flashes": 5}, catalogs=[strong])      # taumelnder Satellit kann blinken
    assert mc.assess(blink)["verdict"] == "satellite"
    assert classify(dict(linked, motion_class=mc.assess(linked)))[0] == "satellite"


def test_verkettete_spur_ohne_bahn():
    fast = cand(kind="plane", linked_track={"count": 3, "span_seconds": 21, "rate_pixels_s": 17.8}, catalogs=[sat()])
    slow = cand(kind="plane", linked_track={"count": 2, "span_seconds": 83, "rate_pixels_s": 1.0}, catalogs=[sat()])
    open_ = cand(kind="plane", linked_track={"count": 3, "span_seconds": 21, "rate_pixels_s": 17.8},
                 catalogs=[{"provider": "satellites", "status": "needs_archive"}])
    assert mc.assess(fast)["verdict"] == "likely_plane"                       # ~107″/s, Bahndaten vorhanden, nichts passt
    assert mc.assess(slow)["verdict"] == "satellite_or_plane"                 # 6″/s: eher hoher Satellit
    assert mc.assess(open_)["verdict"] == "satellite_or_plane"                # nicht prüfbar ≠ nichts da
    unknown = cand(kind="plane", linked_track={"count": 3, "span_seconds": 21, "rate_pixels_s": 17.8}, catalogs=[sat()], plausibility={})
    assert mc.assess(unknown)["verdict"] == "satellite_or_plane"              # ohne Winkelmaß keine Aussage „schnell“
    assert "nicht möglich" in " ".join(mc.assess(open_)["reasons"])


def test_asteroid_nur_bei_langsamer_punktbewegung():
    frames = [{"timestamp": 1000 + 10 * k} for k in range(12)]
    slow = cand(kind="moving", hits=range(8), segment=[(100 + 0.1 * k, 100) for k in range(8)], frames=frames)
    fast = cand(kind="moving", hits=range(8), segment=[(100 + 5 * k, 100) for k in range(8)], frames=frames)
    assert mc.assess(slow)["verdict"] == "asteroid" and mc.assess(slow)["kind"] == "moving"
    assert mc.assess(fast)["verdict"] is None                                 # 3″/s: keine Klasse aus der Geschwindigkeit allein
    unknown = cand(kind="moving", hits=range(8), segment=[(100 + 0.1 * k, 100) for k in range(8)], frames=frames, plausibility={})
    assert mc.assess(unknown)["verdict"] is None                              # ohne Winkelmaß nicht „langsam“ behaupten


def test_server_rechnet_veraltete_einstufung_neu():
    c = cand(kind="plane", blink={"flashes": 5}, catalogs=[sat()], motion_class={"version": 0, "verdict": "satellite"})
    mc.current([c])
    assert c["motion_class"]["version"] == mc.VERSION and c["motion_class"]["verdict"] == "likely_plane"


def test_vespera_belichtung_in_sekunden():
    """Vespera: EXPOSURE in Sekunden (ohne EXPTIME) – vorher „fehlt“, alle Kataloge nicht prüfbar, keine Verkettung."""
    sys.path.insert(0, str(ENGINE))
    from motion_plausibility import exposure_info
    info = exposure_info({"INSTRUME": "vespera-0000", "EXPOSURE": "10.0"})
    assert info["seconds"] == 10.0 and info["status"] == "known" and info["source"] == "Vespera EXPOSURE [s]"
    assert exposure_info({"EXPOSURE": "10.0"})["status"] == "missing"         # unbekanntes Gerät: nichts erfinden
    assert exposure_info({"INSTRUME": "vespera-0000", "EXPOSURE": "10.0", "exposure_seconds": 10.0})["seconds"] == 10.0


def test_verkettung_mit_vespera_belichtung():
    """Drei aneinander anschließende Spuren (Vespera, 10 s) werden zu einer Kette – vorher ohne Belichtung nicht."""
    sys.path.insert(0, str(ENGINE))
    from trail_tracks import link_tracks
    meta = {"INSTRUME": "vespera-0000", "EXPOSURE": "10.0"}
    rows = [{"index": i, "timestamp": 1000 + 10 * i, "meta": meta,
             "detections": [{"kind": "trail", "segment": [[884 - 200 * i, 357 + 55 * i], [712 - 200 * i, 406 + 55 * i]], "line_snr": 24}]}
            for i in range(3)]
    assert link_tracks(rows) == 1
    assert rows[0]["detections"][0]["label"] == "Bewegtes Objekt in 3 Aufnahmen (Satellit oder Flugzeug)"


def test_blinkprofil_wenige_blitze_nur_im_takt():
    """Unter 5 Blitzen muss jeder Abstand im Takt liegen; unregelmäßige Dreiergruppen und Rauschen bleiben ohne Blinkmuster."""
    import pytest
    pytest.importorskip("cv2")
    import numpy as np
    sys.path.insert(0, str(ENGINE))
    import line_detection as ld

    def profile(starts, length=12, level=6.):
        c = np.zeros(700)
        for s in starts:
            c[s:s + length] = level
        return c

    valid = np.ones(700, bool)
    old = (ld.BLINK_MIN_FLASHES, ld.BLINK_SCORE)
    try:
        ld.BLINK_MIN_FLASHES = 3
        found = ld.blink_pattern(profile([100, 250, 400]), valid)
        assert found and found["flashes"] == 3 and found["max_deviation"] <= ld.BLINK_REGULARITY
        assert ld.blink_pattern(profile([100, 250, 460]), valid) is None          # zweiter Abstand 40 % länger
        rng = np.random.default_rng(3)
        assert ld.blink_pattern(rng.normal(0, 1, 700), valid) is None
    finally:
        ld.BLINK_MIN_FLASHES, ld.BLINK_SCORE = old


def test_beweger_tempo_aus_echten_zeiten_der_pfadpunkte():
    """Review PR #130: Lücken im Pfad (ausgeschlossene/fehlende Bilder) zählen mit – Tempo aus den Zeitstempeln bzw.
    Bildnummern der Pfadpunkte, nicht aus „ein Bildabstand je Punkt“."""
    frames = [{"timestamp": 1000 + 10 * k} for k in range(30)]
    seg = [(100 + 0.5 * k, 100) for k in range(8)]                       # 3,5 px ≈ 21″
    plain = cand(kind="moving", hits=range(8), segment=seg, frames=frames)
    assert abs(mc.mover_speed(plain) - 3.5 * 6 / 70) < 0.05                 # alte Näherung: 7 Bildabstände
    timed = cand(kind="moving", hits=range(8), segment=seg, frames=frames)
    timed["evidence"]["segment_times"] = [1000 + 30 * k for k in range(8)]   # jedes dritte Bild (dazwischen ausgeschlossen)
    numbered = cand(kind="moving", hits=range(8), segment=seg, frames=frames)
    numbered["evidence"]["segment_frames"] = [1 + 3 * k for k in range(8)]
    assert abs(mc.mover_speed(timed) - mc.mover_speed(plain) / 3) < 1e-6
    assert abs(mc.mover_speed(numbered) - mc.mover_speed(plain) / 3) < 1e-6
    broken = cand(kind="moving", hits=range(8), segment=seg, frames=frames)
    broken["evidence"]["segment_times"] = [None] * 8                         # unbrauchbar → Rückfall
    assert mc.mover_speed(broken) == mc.mover_speed(plain)



def test_engine_fuehrt_bilder_der_pfadpunkte_mit(monkeypatch):
    """Die Engine legt je Pfadpunkt Bildnummer und Aufnahmezeit in den Nachweis (Bild 3 fehlt im Pfad)."""
    import pytest
    pytest.importorskip("cv2")
    import numpy as np
    monkeypatch.syspath_prepend(str(ENGINE))
    from candidate_evidence import enrich
    rows = {i: dict(index=i, file=f"b{i}", timestamp=1000 + 10 * i, reference_to_current=[[1, 0, 0], [0, 1, 0]],
                    star_field=dict(accepted=True), quality=dict(excluded=False)) for i in range(6)}
    data = {f"b{i}": np.full((200, 200), 100, dtype="float32") for i in range(6)}
    cand_ = dict(id="K1", hit_indices=[4], world_path=None, exposure_seconds=10, kind="moving",
                 frames=[dict(index=i + 1, timestamp=1000 + 10 * i, hit=i == 4, excluded=False) for i in range(6)])
    det = dict(kind="moving", path=[[100, 100], [101, 100], [102, 100], [104, 100]], path_frames=[0, 1, 2, 4])
    ev = enrich(cand_, rows[4], det, rows, lambda name: (data[name], {}), 200, 200)
    assert ev["segment_frames"] == [1, 2, 3, 5] and ev["segment_times"] == [1000, 1010, 1020, 1040]

