"""Produkt-Kern: Modul-Registry, gemeinsame Endpunkte, Herkunftsschutz, Ausfallsicherheit."""
import json

from fastapi.testclient import TestClient

from astroflow.core.config import load_settings
from astroflow.core.modules import Context, load_modules
from astroflow.core.store import SettingsStore
from astroflow.server import create_app


def test_alle_module_laden(tmp_path, monkeypatch):
    monkeypatch.setenv("ASTROFLOW_MODULES", "stacking,skytrace,sage")
    monkeypatch.setenv("SKYTRACE_HOME", str(tmp_path / "st"))
    with TestClient(create_app(load_settings(tmp_path / "d"))) as c:
        info = c.get("/api/app").json()
        assert [m["id"] for m in info["modules"]] == ["stacking", "skytrace", "sage"]
        assert all(m["available"] and not m["error"] for m in info["modules"])
        assert info["build"] and "/m/skytrace/evidence.js" in info["modules"][1]["scripts"]
        st = c.get("/api/status").json()
        assert set(st["modules"]) == {"stacking", "skytrace", "sage"} and st["busy"] == 0
        assert c.get("/api/activity").json() == []
        assert c.get("/m/sage/sage.js").status_code == 200
        assert c.get("/").headers["cache-control"] == "no-cache"
        # Schreibzugriffe nur von der eigenen Oberfläche
        assert c.post("/api/stacking/scan", headers={"origin": "http://boese.example"}).status_code == 403
        assert c.post("/api/stacking/scan", headers={"origin": "http://127.0.0.1:8766"}).status_code == 200
        # Module abschalten wirkt nach dem Neustart; das Kernmodul bleibt
        assert c.post("/api/modules", json={"sage": False, "stacking": False}).json()["saved"] == {"sage": False}
    with TestClient(create_app(load_settings(tmp_path / "d"))) as c:
        mods = {m["id"]: m for m in c.get("/api/app").json()["modules"]}
        assert not mods["sage"]["enabled"] and mods["stacking"]["available"]
        assert c.get("/api/sage/config").status_code in (404, 405)


def test_defektes_modul_legt_app_nicht_lahm(tmp_path, monkeypatch):
    monkeypatch.setenv("ASTROFLOW_MODULES", "stacking,sage")
    s = load_settings(tmp_path)
    ctx = Context(s, SettingsStore(tmp_path / "settings.json"))

    def factory(mid):
        if mid == "sage":
            raise ImportError("Paket fehlt")
        import importlib
        return importlib.import_module(f"astroflow.modules.{mid}").Module

    loaded = {m.id: m for m in load_modules(ctx, factory)}
    assert loaded["stacking"].module and loaded["sage"].module is None
    assert "Paket fehlt" in loaded["sage"].info()["error"]


def test_settings_store_abschnitte(tmp_path):
    st = SettingsStore(tmp_path / "s.json")
    st.update("solver", {"backend": "astap"})
    st.update("sage", {"story_model": "x", "nova_key": "geheim"})
    st.update("sage", {"nova_key": None})
    data = json.loads((tmp_path / "s.json").read_text(encoding="utf-8"))
    assert data == {"solver": {"backend": "astap"}, "sage": {"story_model": "x"}}
