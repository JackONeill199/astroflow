"""Aktivität zählt Aufnahme-Metadaten statt Uploads, Ergebnisversionen oder vorhandene Dateien."""
import json

from astroflow.modules.stacking.activity_stats import summarize


def frame(stamp, exposure=10, **kw):
    return {"kind": "light", "info": json.dumps({"date_obs": stamp, "exptime": exposure}), "project_id": 1, **kw}


def test_nights_utc_year_boundary_and_deleted_frames():
    rows = [frame("2023-01-01T01:00:00Z", deleted=1), frame("2022-12-31T23:30:00-02:00", 20),
            frame("2023-01-01T20:00:00", 30, project_id=2)]
    d = summarize(rows)
    assert d["frames"] == 3 and d["nights"] == 2 and d["exposure"] == 60 and d["objects"] == 2
    assert d["years"] == [2023, 2022]
    assert d["top_nights"][0] == {"key": "2022-12-31", "frames": 2, "exposure": 30}
    assert d["hours"][1]["frames"] == 2 and d["weekdays"][5]["frames"] == 2
    selected = summarize(rows, 2022)
    assert selected["frames"] == 2 and len(selected["months"]) == 12
    assert selected["months"][-1]["frames"] == 2 and selected["top_month"]["key"] == "2022-12"


def test_excludes_duplicates_calibration_and_invalid_dates():
    rows = [frame("2022-06-01T20:00:00"), frame("2022-06-01T20:00:00", kind="duplicate"),
            frame("2022-06-01T20:00:00", kind="ignored"), frame("2022-06-01T20:00:00", kind="dark"),
            frame(None), frame("kein Datum"), {"kind": "light", "info": "kaputt"},
            frame("2022-06-01T21:00:00", "NaN"), frame("2022-06-01T22:00:00", -1)]
    d = summarize(rows)
    assert d["frames"] == 3 and d["missing_dates"] == 3 and d["exposure"] == 10
    assert d["nights"] == 1


def test_empty_period():
    d = summarize([frame("2022-06-01T20:00:00")], 2024)
    assert d["frames"] == 0 and d["first"] is None and d["last"] is None
    assert len(d["hours"]) == 24 and len(d["weekdays"]) == 7
    assert d["years"] == [2022]
