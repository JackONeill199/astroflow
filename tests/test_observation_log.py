"""Aufnahmeprotokoll für Sage (Dienst `observation_log`): Felder per json_extract, robust gegen leere/kaputte JSON-Spalten."""
import json

from astroflow.core.config import load_settings
from astroflow.server import create_app


def test_observation_log_liest_nur_noetige_felder(tmp_path):
    s = load_settings(tmp_path / "data")
    s.auto = False
    mod = create_app(s, only=("stacking",)).state.modules["stacking"]
    db = mod.eng.db
    pid = db.x("INSERT INTO projects(name, kind) VALUES('M31', 'galaxy')")
    info = {"exptime": 10, "instrument": "Teleskop A", "filter": "L", "date_obs": "2026-10-01T22:00:00"}
    for i, (inf, met, st) in enumerate([
        (json.dumps(info), json.dumps({"fwhm": 2.0, "tiles": list(range(50))}), "accepted"),
        (json.dumps({**info, "date_obs": "2026-10-01T23:00:00"}), json.dumps({"fwhm": 3.0}), "rejected"),
        ("", None, "new"),                                          # kaputt/leer: darf nichts abbrechen
    ]):
        db.x("INSERT INTO frames(id, project_id, path, kind, night, info, metrics, status) VALUES(?,?,?,?,?,?,?,?)",
             (f"f{i}", pid, f"x{i}.fit", "light", "2026-10-01", inf, met, st))
    db.x("INSERT INTO frames(id, project_id, path, kind, night, info) VALUES('d', ?, 'd.fit', 'dark', '2026-10-01', '{}')", (pid,))
    other = db.x("INSERT INTO projects(name) VALUES('M42')")
    db.x("INSERT INTO results(project_id, created, n_frames, exposure, stats) VALUES(?, 1, 1, 10, ?)",
         (pid, json.dumps({"quality": {"score": 7, "measure": {"snr": 12.5}}, "fwhm_px": 2.1})))
    log = mod.observation_log()
    assert next(x for x in log if x["id"] == other) == {**next(x for x in log if x["id"] == other), "sessions": [], "results": []}
    assert [(r["score"], r["snr"], r["fwhm_px"]) for r in next(x for x in log if x["id"] == pid)["results"]] == [(7, 12.5, 2.1)]
    p = next(x for x in log if x["id"] == pid)
    (n,) = p["sessions"]
    assert n["frames"] == 3 and n["used"] == 1 and n["exposure_s"] == 20 and n["used_s"] == 10
    assert n["instruments"] == ["Teleskop A"] and n["filters"] == ["L"] and n["fwhm_px"] == 3.0
    assert n["first"] == "2026-10-01T22:00:00" and n["last"] == "2026-10-01T23:00:00"
