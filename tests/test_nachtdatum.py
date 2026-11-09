"""Nachtdatum nach Ortszeit (core/sites.night_of, Issue #39). Nur erfundene Orte."""
from __future__ import annotations

import datetime as dt
import json

import pytest

from astroflow.core import sites

UTC = dt.timezone.utc
EUROPA, USA_WEST, JAPAN = 9.7, -118.3, 139.2          # erfundene Längen (Grad Ost)


def _zone(name):
    """IANA-Zone, falls die Zeitzonendaten vorhanden sind (Windows ohne Paket tzdata: keine) – sonst None."""
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name)
    except Exception:
        return None


def _zones(*names):
    return [z for z in map(_zone, names) if z is not None]


def _old(t: dt.datetime) -> str:
    return (t.astimezone(UTC) - dt.timedelta(hours=12)).strftime("%Y-%m-%d")


def _site(i, lon, lat=40.0):
    return {"id": i, "name": f"Ort {i}", "lat": lat, "lon": lon, "alt_m": 0, "accuracy_m": 100}


class Book:
    """Attrappe eines SiteBook (nur cached_data/data)."""

    def __init__(self, data):
        self.d = data

    def cached_data(self):
        return self.d

    data = cached_data


@pytest.fixture(autouse=True)
def _kein_buch(monkeypatch):
    monkeypatch.setattr(sites, "_NIGHT_BOOK", None)


def _hours(start: dt.datetime, n: int, step_h: float = 1.0):
    return [start + dt.timedelta(hours=step_h * i) for i in range(n)]


def test_europa_unveraendert_ueber_ein_jahr():
    """Ort in Europa (Länge, Zeitzone, Rechnerzeit) – jede Stunde eines Jahres wie die alte Regel UTC − 12 h."""
    times = _hours(dt.datetime(2026, 1, 1, tzinfo=UTC), 365 * 24)
    for t in times:
        old = _old(t)
        assert sites.night_of(t, lon=EUROPA) == old
        assert sites.night_of(t.timestamp(), lon=-20.0) == old
    for z in _zones("Europe/Berlin", "Europe/London") + [dt.timezone(dt.timedelta(hours=2))]:
        for t in times[::7]:
            assert sites.night_of(t, tz=z) == _old(t)


def test_alte_aufrufe_bleiben():
    assert sites.night_of("2024-03-10T03:00:00", lon=EUROPA) == "2024-03-09"
    assert sites.night_of("2024-03-10") == "2024-03-10"
    assert sites.night_of(None) is None and sites.night_of("kaputt") is None and sites.night_of(True) is None


def test_usa_west_eine_nacht_ueber_utc_mitternacht():
    """19 Uhr bis 6 Uhr Ortszeit (PST) = 03 bis 14 Uhr UTC: früher zwei Nächte (Grenze 4 Uhr Ortszeit), jetzt eine."""
    frames = _hours(dt.datetime(2026, 1, 15, 3, tzinfo=UTC), 12)
    assert len({_old(t) for t in frames}) == 2
    assert {sites.night_of(t, lon=USA_WEST) for t in frames} == {"2026-01-14"}
    for z in _zones("America/Los_Angeles") + [dt.timezone(dt.timedelta(hours=-8))]:
        assert {sites.night_of(t, tz=z) for t in frames} == {"2026-01-14"}


def test_japan_abend_nicht_am_vortag():
    """19 Uhr JST am 10. Mai = 10 Uhr UTC: früher 9. Mai, jetzt 10. Mai; Morgen 5 Uhr JST bleibt dieselbe Nacht."""
    abend = dt.datetime(2026, 5, 10, 10, tzinfo=UTC)
    morgen = dt.datetime(2026, 5, 10, 20, tzinfo=UTC)
    assert _old(abend) == "2026-05-09"
    for kw in [{"lon": JAPAN}, {"tz": dt.timezone(dt.timedelta(hours=9))}] + [{"tz": z} for z in _zones("Asia/Tokyo")]:
        assert sites.night_of(abend, **kw) == "2026-05-10"
        assert sites.night_of(morgen, **kw) == "2026-05-10"


@pytest.mark.parametrize("start", [dt.datetime(2026, 3, 8, 2, tzinfo=UTC),      # Nacht 7./8. März: PST → PDT um 2 Uhr
                                   dt.datetime(2026, 11, 1, 2, tzinfo=UTC)])    # Nacht 31.10./1.11.: PDT → PST
def test_sommerzeitwechsel_bleibt_eine_nacht(start, monkeypatch):
    frames = _hours(start, 22, 0.5)                    # 11 h Aufnahmen über den Wechsel
    want = {(start - dt.timedelta(days=1)).strftime("%Y-%m-%d")}
    assert {sites.night_of(t, lon=USA_WEST) for t in frames} == want
    for z in _zones("America/Los_Angeles"):
        assert {sites.night_of(t, tz=z) for t in frames} == want
    # Rechnerzeitzone mit Wechsel um 2 Uhr Ortszeit (10 Uhr UTC), überall lauffähig nachgebildet
    switch = start.replace(hour=10)
    spring = start.month == 3
    monkeypatch.setattr(sites, "_tz_hours", lambda t, tz=None: (-8.0 if t < switch else -7.0) if spring else (-7.0 if t < switch - dt.timedelta(hours=1) else -8.0))
    assert {sites.night_of(t) for t in frames} == want


def test_ort_im_fits_kopf():
    t = dt.datetime(2026, 5, 10, 10, tzinfo=UTC)
    assert sites.night_of(t, header={"SITELAT": 35.0, "SITELONG": JAPAN}) == "2026-05-10"
    assert sites.night_of(t, header={"SITELAT": 35.0, "SITELONG": EUROPA}) == "2026-05-09"


def test_rueckfall_ohne_ort(monkeypatch):
    """Ohne Ort: Zeitzone des Rechners; ist keine ermittelbar: UTC (alte Regel)."""
    t = dt.datetime(2026, 1, 15, 13, tzinfo=UTC)                      # 5 Uhr PST
    monkeypatch.setattr(sites, "_tz_hours", lambda t, tz=None: None)
    assert sites.night_of(t) == _old(t) == "2026-01-15"
    monkeypatch.setattr(sites, "_tz_hours", lambda t, tz=None: -8.0)
    assert sites.night_of(t) == "2026-01-14"
    monkeypatch.setattr(sites, "_tz_hours", lambda t, tz=None: 1.0)  # Europa
    assert sites.night_of(t) == _old(t)


def test_ortsliste_standard_und_reise(monkeypatch):
    monkeypatch.setattr(sites, "_tz_hours", lambda t, tz=None: 1.0)  # Rechner in Europa
    data = {"list": [_site("heim", EUROPA), _site("reise", JAPAN), _site("west", USA_WEST)], "default": "heim",
            "periods": [{"site_id": "heim", "from": "2020-01-01", "to": ""},
                        {"site_id": "reise", "from": "2026-05-10", "to": "2026-05-12"}]}
    book = Book(data)
    abend = dt.datetime(2026, 5, 10, 10, tzinfo=UTC)                 # 19 Uhr am Reiseort
    assert sites.night_of(abend, book=book) == "2026-05-10"
    assert sites.night_of(dt.datetime(2026, 5, 13, 10, tzinfo=UTC), book=book) == "2026-05-12"   # nach der Reise: Heimregel
    assert sites.night_of(dt.datetime(2026, 5, 9, 21, tzinfo=UTC), book=book) == "2026-05-09"    # zu Hause
    # Standard-Ort in den USA: ganze Nacht ein Datum, auch ohne Zeitraum
    west = Book({"list": [_site("west", USA_WEST)], "default": "west", "periods": []})
    frames = _hours(dt.datetime(2026, 1, 15, 3, tzinfo=UTC), 12)
    assert {sites.night_of(t, book=west) for t in frames} == {"2026-01-14"}
    # use_book: night_of ohne Angaben nutzt die gesetzte Ortsliste, resolve dieselbe Nacht
    sites.use_book(west)
    assert sites.night_of(frames[-1]) == "2026-01-14"
    assert sites.resolve(west.d, when=frames[-1].timestamp())["id"] == "west"


def test_sitebook_puffer(tmp_path):
    p = tmp_path / "settings.json"
    p.write_text(json.dumps({"sites": {"list": [_site("a", USA_WEST)], "default": "a"}}), encoding="utf-8")
    book = sites.SiteBook(path=p)
    t = dt.datetime(2026, 1, 15, 13, tzinfo=UTC)
    assert sites.night_of(t, book=book) == "2026-01-14"
    assert book.cached_data() is book.cached_data()


def test_migration_europa_unveraendert_usa_angepasst(tmp_path):
    from astroflow.core.store import SettingsStore
    from astroflow.modules.stacking import nights
    from astroflow.modules.stacking.db import DB

    db = DB(tmp_path / "a.db")
    rows = []
    for i, t in enumerate(_hours(dt.datetime(2026, 1, 15, 3, tzinfo=UTC), 12)):
        info = {"date_obs": t.strftime("%Y-%m-%dT%H:%M:%S")}
        rows.append((f"f{i}", 1, f"/x/{i}.fits", "light", _old(t), json.dumps(info)))
    rows.append(("ohne", 1, "/x/o.fits", "light", "2026-01-01", json.dumps({})))
    db.many("INSERT INTO frames(id,project_id,path,kind,night,info) VALUES(?,?,?,?,?,?)", rows)
    store = SettingsStore(tmp_path / "settings.json")

    store.update("sites", {"list": [_site("heim", EUROPA)], "default": "heim"})
    book = sites.SiteBook(store)
    out = nights.migrate(db, store, book)
    assert out == {"frames": 0, "projects": []}
    assert nights.migrate(db, store, book) is None                     # gleiche Grundlage: nichts zu tun

    store.update("sites", {"list": [_site("west", USA_WEST)], "default": "west"})
    book = sites.SiteBook(store)
    out = nights.migrate(db, store, book)
    assert out["frames"] > 0 and out["projects"] == [1]
    got = {r["id"]: r["night"] for r in db.q("SELECT id, night FROM frames")}
    assert {v for k, v in got.items() if k != "ohne"} == {"2026-01-14"}
    assert got["ohne"] == "2026-01-01"                                 # ohne DATE-OBS unverändert
    assert nights.migrate(db, store, book) is None
    assert nights.renight(db, book)["frames"] == 0                     # idempotent


def test_migration_kometenschluessel(tmp_path):
    from astroflow.modules.stacking import nights
    from astroflow.modules.stacking.db import DB

    db = DB(tmp_path / "a.db")
    abend = dt.datetime(2026, 5, 10, 10, tzinfo=UTC)                 # 19 Uhr am Ort in Japan, alt: 9. Mai
    db.x("INSERT INTO projects(id,key,name) VALUES(1,?,?)", ("KOMET:c2026a1|2026-05-09|cam", "C/2026 A1"))
    info = {"date_obs": abend.strftime("%Y-%m-%dT%H:%M:%S"), "site": [35.0, JAPAN]}
    db.x("INSERT INTO frames(id,project_id,path,kind,night,info) VALUES('k',1,'/x/k.fits','light','2026-05-09',?)", (json.dumps(info),))
    assert nights.renight(db, Book({}))["frames"] == 1
    assert db.one("SELECT key FROM projects WHERE id=1")["key"] == "KOMET:c2026a1|2026-05-10|cam"


def test_aktivitaet_mit_fits_ort(monkeypatch):
    from astroflow.modules.stacking.activity_stats import summarize
    monkeypatch.setattr(sites, "_tz_hours", lambda t, tz=None: 0.0)  # Rechner auf UTC
    info = {"date_obs": "2026-05-10T10:00:00", "site": [35.0, JAPAN], "exptime": 10}
    out = summarize([{"kind": "light", "info": json.dumps(info), "project_id": 1}])
    assert "2026-05-10" in json.dumps(out) and "2026-05-09" not in json.dumps(out)
