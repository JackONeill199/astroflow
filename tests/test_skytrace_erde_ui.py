"""SkyTrace „Erde“ (#/skytrace/erde): zentrale Erd-Ansicht mit Ebenen, Nachtsicht, Heatmap der Standortsuche und Bahnen der Nacht.
Erfundene Orte (Fixture `_skytrace_run` in test_ui_layout.py); run3d wird im Browser durch eine synthetische Szene ersetzt.
Dazu der Sammel-Endpunkt `GET /sitesearch/nights/heatmaps` (ohne Browser)."""
import json
import os
import shutil

import pytest
from fastapi.testclient import TestClient

from test_ui_layout import FIND_OVERFLOW, _heat_fixture


def test_heatmaps_endpoint(tmp_path, monkeypatch):
    from astroflow.core.config import load_settings
    from astroflow.core.store import SettingsStore
    from astroflow.server import create_app
    monkeypatch.setenv("ASTROFLOW_MODULES", "stacking,skytrace")
    s = load_settings(tmp_path / "d")
    SettingsStore(s.data_dir / "settings.json").update("skytrace", {"home": str(tmp_path / "skytrace")})
    karten = tmp_path / "skytrace" / "standortsuche-karten"
    karten.mkdir(parents=True)
    for night in ("2022-08-01", "2022-08-06"):
        (karten / f"{night}.json").write_text(json.dumps(dict(_heat_fixture(10.5, 20.5), night=night)), encoding="utf-8")
    with TestClient(create_app(s)) as c:
        d = c.get("/api/skytrace/sitesearch/nights/heatmaps").json()["heat"]                  # leer = alle gespeicherten
        assert sorted(d) == ["2022-08-01", "2022-08-06"] and d["2022-08-06"]["fine"]["size"] == 61
        d = c.get("/api/skytrace/sitesearch/nights/heatmaps?nights=2022-08-06,2022-08-07").json()["heat"]
        assert d["2022-08-07"] is None and d["2022-08-06"]["night"] == "2022-08-06"
        assert c.get("/api/skytrace/sitesearch/nights/heatmaps?nights=../x").status_code == 400
        many = ",".join(f"2022-01-{i % 28 + 1:02d}" for i in range(121))
        assert c.get("/api/skytrace/sitesearch/nights/heatmaps?nights=" + many).status_code == 400


def _scene(tmp_path):
    pytest.importorskip("sgp4")
    from test_skytrace_3d_ui import _scene as scene
    return scene(tmp_path)


def _state(page):
    return page.evaluate("document.querySelector('#steHost')._ste.state()")


@pytest.mark.parametrize("width", [375, 1440])
def test_erde_ansicht(server, browser, width, tmp_path):
    base, _ = server
    page = browser.new_page(viewport={"width": width, "height": 900})
    errors, tiles = [], []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("request", lambda r: tiles.append(r.url) if "tile." in r.url else None)
    page.goto(base + "/#/skytrace/erde/2022-08-01")
    page.wait_for_function("document.querySelector('#steHost')?._ste && !document.querySelector('#steHost')._ste.state().flying "
                           "&& Object.values(document.querySelector('#steHost')._ste.state().reveal).every(v => v >= 1)", timeout=15000)
    st = _state(page)
    assert st["night"] == "2022-08-01" and st["heatVisible"] == ["2022-08-01"] and st["borders"] > 100000
    assert st["layers"]["heat"] == 1 and st["layers"]["runs"] == 0 and st["marks"]["sites"] == 3
    # 01.08.2022 ≈ Mitternacht Ortszeit am erfundenen Ort in Mitteleuropa → Region auf der Nachtseite → Nachtsicht automatisch an
    assert st["nv"] == 1 and st["sun"][0] > 10 and page.locator(".ste-nvpill").is_visible()
    assert st["dist"] < 1.6                                                    # auf die Region gezoomt
    assert "verschiedene Spuren: 1 … 14" in page.locator(".ste-legend").inner_text()
    stage = page.locator(".ste-stage").bounding_box()
    assert stage["height"] >= 340 and stage["width"] >= (330 if width < 500 else 760), stage
    page.screenshot(path=str(tmp_path / f"erde-{width}.png"))
    # Nachtsicht übersteuern: aus → weich auf 0, Auto → wieder an
    page.locator(".ste-nv [data-nv=off]").click()
    page.wait_for_function("document.querySelector('#steHost')._ste.state().nv === 0")
    page.locator(".ste-nv [data-nv=auto]").click()
    page.wait_for_function("document.querySelector('#steHost')._ste.state().nv === 1")
    # Ebene aus: blendet weich aus (Zwischenwert), dann 0; wieder an: Heatmap baut sich neu auf
    page.locator(".ste-layer:has([data-layer=heat])").click()
    page.wait_for_function("document.querySelector('#steHost')._ste.state().layers.heat === 0")
    assert page.locator(".ste-legend").is_hidden()
    page.locator(".ste-layer:has([data-layer=heat])").click()
    page.wait_for_function("(s => s.layers.heat === 1 && s.reveal['2022-08-01'] === 1)(document.querySelector('#steHost')._ste.state())")
    # Detailkarte über das Schild des Vorschlags: Übernehmen, Heatmap, Lauf
    page.locator(".ste-lbl", has_text="Unbekannter Ort").first.click()
    card = page.locator(".ste-card")
    card.wait_for()
    assert "übernehmen" in card.inner_text() and "Heatmap neu berechnen" in card.inner_text() and "46,23° N" in card.inner_text()
    assert not page.evaluate(FIND_OVERFLOW)
    page.screenshot(path=str(tmp_path / f"erde-karte-{width}.png"))
    card.locator("[data-a=x]").click()
    # Alle Nächte: Kamera fährt heraus, Vorschläge aller offenen Nächte
    page.locator(".ste-nights [data-n='']").click()
    page.wait_for_function("(s => s.night === null && !s.flying)(document.querySelector('#steHost')._ste.state())")
    st = _state(page)
    assert st["marks"]["proposals"] > 2 and page.url.endswith("#/skytrace/erde")
    # Nacht per Regler/Pfeil: Adresse folgt
    page.locator(".ste-nightbar [data-step='1']").click()
    page.wait_for_function("document.querySelector('#steHost')._ste.state().night !== null")
    assert "/skytrace/erde/20" in page.url
    assert not tiles and not errors, (tiles, errors)                       # Kartenkacheln nur auf Knopfdruck
    page.close()


def test_erde_bahnen_und_verweis(server, browser, tmp_path):
    """Ebene „Satellitenbahnen und Teleskop“ lädt run3d je Beobachtung der Nacht; „Auf der Erde zeigen“ aus „Standorte aller Nächte“."""
    base, _ = server
    scene = _scene(tmp_path)
    page = browser.new_page(viewport={"width": 1440, "height": 900}, reduced_motion="reduce")
    errors, asked = [], []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.route("**/api/skytrace/run3d?*", lambda route: (asked.append(route.request.url), route.fulfill(json=scene)))
    page.goto(base + "/#/skytrace/standorte")
    page.locator('a[href="#/skytrace/erde/2022-09-01"]').click()
    page.wait_for_function("document.querySelector('#steHost')?._ste?.state().night === '2022-09-01'")
    assert _state(page)["layers"]["heat"] == 1 and not page.locator(".ste-legend").is_visible()   # Nacht ohne Heatmap
    assert "keine Heatmap" in page.locator(".ste-note").inner_text()
    page.locator(".ste-layer:has([data-layer=runs])").click()
    page.wait_for_function("(s => s.runs['2022-09-01']?.ready && s.layers.runs === 1)(document.querySelector('#steHost')._ste.state())")
    st = _state(page)
    assert st["runs"]["2022-09-01"]["hits"] == len(scene["hits"]) and len(asked) == 1            # drei gleiche Beobachtungen → eine Anfrage
    assert "Teleskop-Blickrichtung" in page.locator(".ste-note").inner_text()
    out = os.environ.get("ASTROFLOW_SCREENSHOTS")
    page.screenshot(path=str(tmp_path / "erde-bahnen.png"))
    if out:
        shutil.copy(tmp_path / "erde-bahnen.png", out)
    assert not errors, errors
    page.close()
