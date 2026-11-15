"""Heute Nacht: Wettervorhersage – hohe Wolken schwächer, zweites Modell, Regen, Teleskop-Messung (offline, Attrappen)."""
import datetime as dt

import pytest

from astroflow.modules.stacking import deepsky, tonight

STELLINA = {"id": "stellina", "name": "Testgerät", "alt_az": True, "fov": [1, .7], "source": "Test", "mag_limit": 11.5}
SITE = {"lat": 50.0, "lon": 10.0, "weather": True}
EVENING = dt.datetime(2026, 11, 2, 19, 0, tzinfo=dt.timezone.utc).timestamp()


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.setenv("ASTROFLOW_DEEPSKY", "0")
    deepsky.reset()
    tonight._cache.clear()
    yield
    deepsky.reset()
    tonight._cache.clear()


def test_high_clouds_count_less():
    veil = tonight.effective_cover(5, 5, 100, 100)
    low = tonight.effective_cover(100, 5, 5, 100)
    assert veil < 50 < low
    assert tonight.effective_cover(5, 75, 100, 100) < 90        # der Fall vom 15.11.2026: nicht „100 %“
    assert tonight.effective_cover(None, 10, 10, 77) == 77      # ohne Schichten gilt die Gesamtzahl
    assert tonight.effective_cover(None, None, None, None) is None


def test_rain_rule():
    assert tonight.is_rainy(60, 0) and tonight.is_rainy(0, 0.5) and not tonight.is_rainy(10, 0.0) and not tonight.is_rainy(None, None)


class _Resp:
    def __init__(self, hourly): self._h = hourly
    def raise_for_status(self): pass
    def json(self): return {"hourly": self._h}


def _fake_meteo(monkeypatch, t0, n=30, **over):
    """Attrappe der Open-Meteo-Antwort mit zwei Modellen (Felder `<größe>_<modell>`)."""
    times = [dt.datetime.fromtimestamp(t0 + 3600 * i, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M") for i in range(n)]
    h = {"time": times}
    base = {"cloud_cover": 100, "cloud_cover_low": 5, "cloud_cover_mid": 75, "cloud_cover_high": 100, "wind_speed_10m": 3,
            "relative_humidity_2m": 60, "temperature_2m": 5, "dew_point_2m": 2, "precipitation_probability": 0, "precipitation": 0.0}
    base.update(over.get("main", {}))
    for k, v in base.items():
        h[f"{k}_best_match"] = [v] * n
    alt = {"cloud_cover": 38, "cloud_cover_low": 0, "cloud_cover_mid": 10, "cloud_cover_high": 38}
    alt.update(over.get("alt", {}))
    for k, v in alt.items():
        h[f"{k}_ecmwf_ifs025"] = [v] * n
    seen = {}

    def get(url, params=None, timeout=None):
        seen.update(params)
        return _Resp(h)
    import httpx
    monkeypatch.setattr(httpx, "get", get)
    return seen


def test_forecast_asks_both_models_and_parses_layers(tmp_path, monkeypatch):
    t0 = EVENING
    seen = _fake_meteo(monkeypatch, t0)
    fc = tonight._forecast([50.04, 10.06], tmp_path, t0, t0 + 20 * 3600)
    assert seen["models"] == "best_match,ecmwf_ifs025" and seen["latitude"] == 50.0 and seen["longitude"] == 10.1
    assert "precipitation_probability" in seen["hourly"] and "precipitation" in seen["hourly"]
    h = fc["hours"][3]
    assert h["total"] == 100 and h["cover"] < 90 and h["cover_alt"] < 50 and h["rain"] == 0
    assert list(tmp_path.glob("vorhersage*m_*"))                          # neuer Cache-Name
    assert not list(tmp_path.glob("vorhersage8_*"))


def test_rain_hours_are_unusable_and_warned(tmp_path, monkeypatch):
    t0 = EVENING
    _fake_meteo(monkeypatch, t0 - 6 * 3600, n=60, main={"cloud_cover": 5, "cloud_cover_low": 0, "cloud_cover_mid": 0, "cloud_cover_high": 5,
                                       "precipitation_probability": 70, "precipitation": 0.4}, alt={"cloud_cover": 5, "cloud_cover_low": 0, "cloud_cover_mid": 0, "cloud_cover_high": 5})
    d = tonight.plan([], SITE, tmp_path, EVENING, STELLINA)
    assert max(v for v in d["cloud"] if v is not None) == 100 and max(v for v in d["rain"] if v is not None) == 100               # trotz nur 5 % Wolken
    assert d["sky"]["state"] == "overcast" and d["sky"]["rain_hours"] > 0
    assert all(x["weather_blocked"] for x in d["recommendations"]) or not d["plan"]
    assert not d["plan"]


def test_models_disagree_and_veil(tmp_path, monkeypatch):
    _fake_meteo(monkeypatch, EVENING - 6 * 3600, n=60, main={"cloud_cover": 100, "cloud_cover_low": 70, "cloud_cover_mid": 70, "cloud_cover_high": 100},
                alt={"cloud_cover": 20, "cloud_cover_low": 0, "cloud_cover_mid": 5, "cloud_cover_high": 20})
    m = tonight.plan([], SITE, tmp_path, EVENING, STELLINA)["forecast"]["models"]
    assert m["disagree"] and m["alt_label"] == "ECMWF" and m["alt"] < 20 < m["main_cmp"]


def test_high_veil_flagged_not_overcast(tmp_path, monkeypatch):
    _fake_meteo(monkeypatch, EVENING - 6 * 3600, n=60, main={"cloud_cover": 100, "cloud_cover_low": 2, "cloud_cover_mid": 3, "cloud_cover_high": 100},
                alt={"cloud_cover": 90, "cloud_cover_low": 2, "cloud_cover_mid": 3, "cloud_cover_high": 100})
    d = tonight.plan([], SITE, tmp_path, EVENING, STELLINA)
    assert d["forecast"]["models"]["veil"] and not d["forecast"]["models"]["disagree"]
    assert next(v for v in d["total"] if v is not None) == 100 and next(v for v in d["cover"] if v is not None) < 50 and d["sky"]["state"] != "overcast"


def test_rain_soon():
    t = 1_000_000.0
    fc = {"hours": [{"t": t + 3600 * i, "rain": 100 if i == 2 else 0, "rain_p": 80 if i == 2 else 5, "precip": 0.5 if i == 2 else 0} for i in range(-1, 6)]}
    assert tonight.rain_soon(fc, t, 60)["rain"] is False
    r = tonight.rain_soon(fc, t, 180)
    assert r["rain"] is True and r["in_minutes"] == 120 and r["probability"] == 80
    assert tonight.rain_soon(None, t, 60)["rain"] is None


def test_weather_rain_service_and_telescope_sky(tmp_path, monkeypatch):
    import time
    from fastapi.testclient import TestClient
    from astroflow.core.config import load_settings
    from astroflow.core.store import SettingsStore
    from astroflow.server import create_app
    now = time.time()
    _fake_meteo(monkeypatch, now - 6 * 3600, n=60, main={"precipitation_probability": 60})
    s = load_settings(tmp_path)
    app = create_app(s)
    with TestClient(app) as client:
        ctx = app.state.ctx
        svc = ctx.services["weather_rain"]
        assert svc(120, site={"lat": 50.0, "lon": 10.0})["rain"] is True
        assert svc(30)["rain"] is None                                    # ohne Ort: unbekannt, kein Fehler
        SettingsStore(s.data_dir / "settings.json").update("tutor", SITE)
        base = client.get("/api/stacking/tonight").json()
        assert base["telescope_sky"] is None                              # Dienst fehlt: wie bisher
        ctx.services["telescope_sky"] = lambda: {"clear": True, "ratio": 0.9, "t": now - 60, "source": "Vespera"}
        assert client.get("/api/stacking/tonight").json()["telescope_sky"] == {"clear": True, "ratio": 0.9, "t": now - 60, "source": "Vespera"}
        ctx.services["telescope_sky"] = lambda: {"clear": False, "t": now - 7200, "source": "Vespera"}      # zu alt
        assert client.get("/api/stacking/tonight").json()["telescope_sky"] is None
        ctx.services["telescope_sky"] = lambda: 1 / 0                      # defekter Dienst stört nicht
        assert client.get("/api/stacking/tonight").status_code == 200


def test_precipitation_belongs_to_preceding_hour(tmp_path, monkeypatch):
    """Regen mit Zeitstempel t gilt für (t−1 h, t]: er landet in der Stunde davor; ein kurzes Fenster davor sieht ihn."""
    t0 = 1_000_000 // 3600 * 3600
    times = [dt.datetime.fromtimestamp(t0 + 3600 * i, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M") for i in range(10)]
    wet = [0] * 10
    wet[5] = 90                                                            # Wert für 04:00–05:00 (Stempel 05:00)
    h = {"time": times, "cloud_cover_best_match": [0] * 10, "precipitation_probability_best_match": wet, "precipitation_best_match": [0.0] * 10}
    import httpx
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _Resp(h))
    fc = tonight._forecast([50.0, 10.0], tmp_path, t0, t0 + 9 * 3600)
    rainy = [x["t"] - t0 for x in fc["hours"] if x["rain"]]
    assert rainy == [4 * 3600]                                             # Stunde ab 4 h, nicht ab 5 h
    now = t0 + 4 * 3600 - 15 * 60                                          # 15 min davor
    assert tonight.rain_soon(fc, now, 20) == {"rain": True, "in_minutes": 15, "probability": 90, "precip": 0.0}
    assert tonight.rain_soon(fc, t0 + 5 * 3600 + 60, 20)["rain"] is False  # danach vorbei
