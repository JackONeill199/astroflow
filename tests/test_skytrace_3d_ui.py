"""3D-Lauf-Ansicht im Browser (eigener Reiter „3D“ und zentrale Ansicht #/skytrace/3d): Reiter im Lauf, Knopf im Kandidaten, Zeitregler über die Nacht, Teleskop-/Zielkarte, Treffer,
Sprung zum Kandidaten, Hinweis ohne Standort – Desktop und Handy. Szene aus run3d mit erfundenem Ort und erfundener Bahn;
Lauf und Endpunkt werden im Browser ersetzt."""
import pytest

from test_ui_layout import FIND_OVERFLOW  # noqa: F401
from test_skytrace_3d import T0, _run, _site

pytest.importorskip("sgp4")


def _scene(tmp_path):
    from astroflow.modules.skytrace import run3d
    from test_skytrace_3d_run import _frames
    home = tmp_path / "home"
    rid, _ = _run(home, _site())
    _frames(home / "Ergebnisse" / rid / "Testfeld", n=60)
    run3d._cached.cache_clear()
    return run3d.build_run(home / "Ergebnisse" / rid, None, None, home, {})


@pytest.mark.parametrize("width", [375, 1440])
def test_3d_lauf_ansicht(server, browser, width, tmp_path):
    base, _ = server
    scene = _scene(tmp_path)
    assert scene["ok"] and scene["hits"][0]["in_fov"]
    page = browser.new_page(viewport={"width": width, "height": 900}, reduced_motion="reduce")
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    frame = dict(index=1, url="/synthetic/crop.png", width=40, height=30, hit=True, timestamp=T0, timestamp_text="Bild 1")
    cand = dict(id="K0001", key="Testfeld/K0001", session="Testfeld", kind="trail", timestamp=T0 + 5, exposure_seconds=10, view3d=True,
                frames=[frame], initial_frame=0, hit_indices=[0], crop=[0, 0, 40, 30], plausibility={},
                catalogs=[dict(provider="satellites", status="compatible", detail="Test", matches=[dict(name="TESTSAT", norad_id=99001, separation_arcsec=12)])])
    other = dict(cand, id="K0002", key="Testfeld/K0002", view3d=False, catalogs=[])
    run = dict(id="synthetic", sessions=[dict(session="Testfeld", frames=60, excluded=3, hits=1, video_url="/synthetic/v.mp4")],
               candidates=[other, cand], report_url=None, modified=1)
    asked = []
    page.route("**/api/skytrace/run?id=synthetic*", lambda route: route.fulfill(json=run))
    page.route("**/api/skytrace/run3d?*", lambda route: (asked.append(route.request.url), route.fulfill(json=scene)))
    page.goto(base + "/#/skytrace/synthetic/vorschau")
    # Reiter „3D“: Teleskopweg ohne Fokus, Bühne füllt den Inhaltsbereich
    page.locator("#tabs a[data-t='3d']").click()
    page.wait_for_function("document.querySelector('#st3dHost')?._st3d")
    assert "focus=" not in asked[-1] and page.locator("#st3dHost .st3d-stage canvas").first.is_visible()
    stage = page.locator(".st3d-stage").bounding_box()
    assert stage["height"] >= 300 and stage["width"] >= (300 if width < 500 else 800), stage
    st = page.evaluate("document.querySelector('#st3dHost')._st3d.state()")
    assert st["t"] == pytest.approx(scene["pointing"]["t"][0]) and st["hits"] == 1
    assert "Aufnahme 1" in page.locator(".st3d-time").inner_text()
    # Zeitregler über die ganze Nacht, Pause wird erkannt
    page.locator(".st3d-range input").evaluate(f"r => {{ r.value = {scene['gaps'][0][0] + 300}; r.dispatchEvent(new Event('input')); }}")
    assert "Pause" in page.locator(".st3d-time").inner_text()
    page.locator(".st3d-legend [data-k='0']").click()
    card = page.locator(".st3d-card")
    assert "Blickrichtung" in card.inner_text() and "Feldrotation" in card.inner_text() and "1 Pause" in card.inner_text()
    page.locator(".st3d-views [data-v=scope]").click()
    page.wait_for_function("document.querySelector('#st3dHost')._st3d.state().mode === 'scope'")
    # Ziel: Strahl entlang der Blickrichtung
    page.locator(".st3d-legend [data-k='1']").click()
    assert "nicht maßstäblich" in card.inner_text()
    assert page.evaluate("document.querySelector('#st3dHost')._st3d.state().mode") == "aim"
    # Treffer: Sprung zum Durchgang, Ausblenden, Kandidat öffnen
    page.locator(".st3d-legend [data-k='2']").click()
    st = page.evaluate("document.querySelector('#st3dHost')._st3d.state()")
    assert st["t"] == pytest.approx(T0) and st["visible"] == 1 and "NORAD 99001" in card.inner_text()
    assert not page.evaluate(FIND_OVERFLOW)
    page.screenshot(path=str(tmp_path / f"skytrace-3d-lauf-{width}.png"))
    page.locator(".st3d-legend input[type=checkbox]").uncheck()
    assert page.evaluate("document.querySelector('#st3dHost')._st3d.state().visible") == 0
    page.locator(".st3d-legend input[type=checkbox]").check()
    page.locator(".st3d-card [data-a=cand]").click()
    page.wait_for_selector(".ccard.on")
    assert page.locator("#st3dHost").count() == 0 and "K0001" in page.locator(".ccard.on").inner_text()
    # Kandidaten-Knopf: Reiter „3D“ mit Fokus
    page.locator(".st3d-open").click()
    page.wait_for_function("document.querySelector('#st3dHost')?._st3d")
    assert "focus=Testfeld%2FK0001" in asked[-1] and page.url.endswith("/synthetic/3d")
    # zentrale Ansicht ohne Standort: Hinweis mit Verweis auf die Einstellungen
    page.unroute("**/api/skytrace/run3d?*")
    page.route("**/api/skytrace/run3d?*", lambda route: route.fulfill(json=dict(ok=False, need_site=True, reason="Der Beobachtungsstandort fehlt.")))
    page.route("**/api/skytrace/runs", lambda route: route.fulfill(json=[dict(id="synthetic", title="Testfeld", modified=1, sessions=1, frames=60, excluded=3, candidates=2, preview=None)]))
    page.goto(base + "/#/skytrace/3d")
    page.wait_for_selector(".st3d-msg.hint")
    assert page.locator(".st3d-msg a").get_attribute("href") == "#/settings/skytrace"
    assert page.locator(".st3d-runs [data-run=synthetic]").is_visible()       # Analysen rechts wählbar
    assert not page.evaluate(FIND_OVERFLOW)
    assert not errors, errors
