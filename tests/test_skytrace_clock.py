"""SkyTrace Uhrversatz-Suche (engine/clock_scan.py): Kamera-Uhr einer Nacht falsch gestellt → Versatz aus den Satellitenspuren finden,
nur auf Knopfdruck übernehmen, danach wirkt er beim Abgleich. Synthetische LEO-Bahnen, erfundene Orte. Engine-Teil braucht skyfield –
direkt im Engine-Interpreter: `python tests/test_skytrace_clock.py`."""
import contextlib
import io
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_skytrace_rematch import TRUE, _engine, _patch, _satellites, _unload, pytest  # noqa: E402
from test_skytrace_nights import HOME, NIGHT, _observation, _search, _sites  # noqa: E402

CLOCK = -600.0                 # wahre UTC = Kamerazeit + CLOCK → Kamera-Uhr ging 10 min vor


@pytest.fixture
def engine(tmp_path, monkeypatch):
    pytest.importorskip("skyfield")
    pytest.importorskip("sgp4")
    yield _engine(tmp_path, monkeypatch)
    _unload()


def _shift_clock(tmp: Path, obs: dict, seconds: float):
    """Kamerazeit der Kandidaten verstellen (Spuren bleiben am wahren Himmel)."""
    path = tmp / "st" / "Ergebnisse" / obs["run"] / obs["folder"] / "kandidaten.json"
    cands = json.loads(path.read_text(encoding="utf-8"))
    for c in cands:
        c["timestamp"] -= seconds
    path.write_text(json.dumps(cands), encoding="utf-8")
    return cands


def _entries(tmp: Path, obs: dict):
    folder = tmp / "st" / "Ergebnisse" / obs["run"] / obs["folder"]
    return [(folder, c) for c in json.loads((folder / "kandidaten.json").read_text(encoding="utf-8"))]


def test_scan_finds_ten_minute_offset(engine, tmp_path, monkeypatch):
    _sites(tmp_path / "settings.json", [{**HOME, **TRUE}], "home")
    sats = _satellites(4)
    _patch(monkeypatch, sats)
    a = _observation(tmp_path, "2026-11-05_10-00-00-000001", f"{NIGHT}_observation_Feld_A", sats, [0, 1, 2, 3], TRUE)
    _shift_clock(tmp_path, a, CLOCK)
    import clock_scan
    settings = engine.offline_settings()
    entries = _entries(tmp_path, a)
    site = engine.site_for(entries[0][1], {}, settings)
    assert all(engine.check(c, site, dict(settings)).get("status") != "compatible" for _, c in entries)   # ohne Korrektur kein Treffer
    with contextlib.redirect_stdout(io.StringIO()):
        out = clock_scan.scan_night(engine.sitefit_module(), entries, site, settings)
    p = out["proposal"]
    assert p["offset_s"] == pytest.approx(CLOCK + 2.0, abs=5), out           # +2 s: Testspuren liegen 2 s nach der Belichtungszeit
    assert p["support"] == 4 and p["sure"] == 4 and p["before"] == 0
    assert p["confidence"] > 0.99 and p["nights"] == [NIGHT]
    # Übernommen (Einstellung clock_offsets) trifft der normale Abgleich wieder, Zeitstempel bleiben Kamerazeit
    fixed = dict(settings, clock_offsets={NIGHT: {"offset_s": p["offset_s"]}})
    res = [engine.check(c, site, dict(fixed)) for _, c in entries]
    assert all(r["status"] == "compatible" and r["clock_offset_s"] == p["offset_s"] for r in res)
    assert "Uhr der Kamera um" in res[0]["detail"]


def test_scan_random_trails_no_proposal(engine, tmp_path, monkeypatch):
    _sites(tmp_path / "settings.json", [{**HOME, **TRUE}], "home")
    sats = _satellites(4)
    _patch(monkeypatch, sats)
    a = _observation(tmp_path, "2026-11-05_10-00-00-000001", f"{NIGHT}_observation_Feld_A", sats, [0, 1, 2, 3], TRUE, shift_deg=25.0)
    _shift_clock(tmp_path, a, CLOCK)
    import clock_scan
    settings = engine.offline_settings()
    entries = _entries(tmp_path, a)
    with contextlib.redirect_stdout(io.StringIO()):
        out = clock_scan.scan_night(engine.sitefit_module(), entries, engine.site_for(entries[0][1], {}, settings), settings)
    assert "proposal" not in out and out["reason"], out


def test_evaluate_needs_cluster_above_chance():
    """Statistik ohne Bahnrechnung: viele zufällige Einzeltreffer ergeben keinen Vorschlag, eine Häufung schon."""
    pytest.importorskip("skyfield")
    import random
    from test_skytrace_rematch import ENGINE
    sys.path.insert(0, str(ENGINE))
    import clock_scan
    rnd = random.Random(1)
    noise = [dict(id=f"K{i}", hits=[dict(norad=1000 * i + j, name="DEB", offset=rnd.uniform(-1200, 1200), half=4, cross=100) for j in range(3)])
             for i in range(12)]
    ev = clock_scan.evaluate(noise)
    assert not clock_scan.proposal_ok(ev)[0], ev                     # 36 Zufallstreffer: höchstens Zufallshäufungen
    for i, tr in enumerate(noise[:4]):
        tr["hits"].append(dict(norad=99 + i, name="SAT", offset=-640 + i, half=2, cross=40))
    ev = clock_scan.evaluate(noise)
    assert clock_scan.proposal_ok(ev)[0] and ev["offset_s"] == pytest.approx(-638.5, abs=10) and ev["support"] >= 4, ev
    same = [dict(id=f"K{i}", hits=[dict(norad=7, name="SAT", offset=-640, half=2, cross=40)]) for i in range(4)]
    assert clock_scan.evaluate(same)["support"] == 1                   # derselbe Satellit zählt nur einmal


def test_nights_proposes_clock_and_rematch_applies(engine, tmp_path, monkeypatch):
    """„Standorte aller Nächte“: Nacht mit falscher Uhr → Uhrvorschlag; übernommen → Neuabgleich erkennt die geänderte Uhr."""
    _sites(tmp_path / "settings.json", [{**HOME, **TRUE}], "home")
    sats = _satellites(4)
    _patch(monkeypatch, sats)
    a = _observation(tmp_path, "2026-11-05_10-00-00-000001", f"{NIGHT}_observation_Feld_A", sats, [0, 1, 2, 3], TRUE)
    _shift_clock(tmp_path, a, CLOCK)
    with contextlib.redirect_stdout(io.StringIO()):
        n = _search(engine, tmp_path, [a])
    q = n["clock"]["proposal"]
    assert n["status"] == "better" and n["proposal"] is None and q["offset_s"] == pytest.approx(CLOCK + 2, abs=5), n
    assert "korrigierter Uhr" in n["note"]
    import observation_settings
    observation_settings.update_settings({"clock_offsets": {NIGHT: {"offset_s": q["offset_s"], "sigma_s": q["sigma_s"]}}})
    assert observation_settings.load_settings()["clock_offsets"][NIGHT]["offset_s"] == q["offset_s"]
    root = tmp_path / "st" / "Ergebnisse" / a["run"]
    with contextlib.redirect_stdout(io.StringIO()):
        engine.rematch([root])                                       # ohne --force: nur Kandidaten mit geänderter Uhr/Ort
    cands = json.loads((root / a["folder"] / "kandidaten.json").read_text(encoding="utf-8"))
    sat = [next(r for r in c["catalogs"] if r["provider"] == "satellites") for c in cands]
    assert all(s["status"] == "compatible" and s["clock_offset_s"] == q["offset_s"] for s in sat)
    assert cands[0]["timestamp"] == T0_CAMERA                        # Kamerazeit unverändert
    for bad in ({"gestern": 5}, {NIGHT: 99999}, {NIGHT: True}):
        try:
            observation_settings.update_settings({"clock_offsets": bad})
        except ValueError:
            continue
        raise AssertionError(f"ungültiger Uhrversatz angenommen: {bad}")
    observation_settings.update_settings({"clock_offsets": {NIGHT: None}})
    assert NIGHT not in observation_settings.load_settings()["clock_offsets"]


def test_evaluate_around_applied_correction():
    """Schon übernommene Korrektur −1100 s: die Suche sucht um sie herum, −1300 s liegt im Fenster (Review PR #48)."""
    pytest.importorskip("skyfield")
    from test_skytrace_rematch import ENGINE
    sys.path.insert(0, str(ENGINE))
    import clock_scan
    trails = [dict(id=f"K{i}", hits=[dict(norad=10 + i, name="SAT", offset=-1300 + i, half=2, cross=40)]) for i in range(3)]
    ev = clock_scan.evaluate(trails, center=-1100.0)
    assert ev["support"] == 3 and ev["offset_s"] == pytest.approx(-1299, abs=3) and clock_scan.proposal_ok(ev)[0], ev
    same = [dict(id=f"K{i}", hits=[dict(norad=10 + i, name="SAT", offset=-1102 + i, half=2, cross=40)]) for i in range(3)]
    ok, why = clock_scan.proposal_ok(clock_scan.evaluate(same, center=-1100.0))
    assert not ok and "übernommene Uhrkorrektur passt" in why


def test_nights_fits_site_still_gets_clock(engine, tmp_path, monkeypatch):
    """Ort aus FITS: keine Ortsänderung, aber der Uhrversatz wird trotzdem gesucht (Review PR #48)."""
    _sites(tmp_path / "settings.json", [{**HOME, **TRUE}], "home")
    sats = _satellites(4)
    _patch(monkeypatch, sats)
    a = _observation(tmp_path, "2026-11-05_10-00-00-000001", f"{NIGHT}_observation_Feld_A", sats, [0, 1, 2, 3], TRUE)
    _shift_clock(tmp_path, a, CLOCK)
    monkeypatch.setattr(engine, "site_for", lambda item, summary, settings: dict(TRUE, origin="fits", accuracy_m=30, name="FITS"))
    with contextlib.redirect_stdout(io.StringIO()):
        n = _search(engine, tmp_path, [a])
    assert n["status"] == "better" and n["proposal"] is None and "FITS" in n["note"], n
    assert n["clock"]["proposal"]["offset_s"] == pytest.approx(CLOCK + 2, abs=5)


from test_skytrace_rematch import T0  # noqa: E402
T0_CAMERA = T0 - CLOCK


def test_correct_clock_not_proposed(engine, tmp_path, monkeypatch):
    """Uhr richtig (nur die üblichen 2 s): Suche bestätigt, kein Vorschlag."""
    _sites(tmp_path / "settings.json", [{**HOME, **TRUE}], "home")
    sats = _satellites(4)
    _patch(monkeypatch, sats)
    a = _observation(tmp_path, "2026-11-05_10-00-00-000001", f"{NIGHT}_observation_Feld_A", sats, [0, 1, 2, 3], TRUE)
    import clock_scan
    settings = engine.offline_settings()
    entries = _entries(tmp_path, a)
    with contextlib.redirect_stdout(io.StringIO()):
        out = clock_scan.scan_night(engine.sitefit_module(), entries, engine.site_for(entries[0][1], {}, settings), settings)
    assert "proposal" not in out and out["support"] == 4 and abs(out["offset_s"]) < 6, out
    assert "richtig" in out["reason"]


if __name__ == "__main__":
    import os, tempfile
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

    class MP:
        def __init__(self): self.undo = []
        def setattr(self, obj, name, value): self.undo.append((obj, name, getattr(obj, name))); setattr(obj, name, value)
        def setenv(self, k, v): os.environ[k] = v
        def syspath_prepend(self, p): sys.path.insert(0, p)
        def close(self): [setattr(o, n, v) for o, n, v in reversed(self.undo)]; _unload()
    test_evaluate_needs_cluster_above_chance(); print("OK test_evaluate_needs_cluster_above_chance")
    test_evaluate_around_applied_correction(); print("OK test_evaluate_around_applied_correction")
    for test in (test_scan_finds_ten_minute_offset, test_scan_random_trails_no_proposal, test_nights_proposes_clock_and_rematch_applies,
                 test_correct_clock_not_proposed, test_nights_fits_site_still_gets_clock):
        mp = MP()
        with tempfile.TemporaryDirectory() as d:
            try:
                test(_engine(Path(d), mp), Path(d), mp)
            finally:
                mp.close()
        print("OK", test.__name__)
