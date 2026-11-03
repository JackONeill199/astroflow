"""3D-Ansicht im Browser: öffnen, Zeitregler, Infokarte, Hinweis bei fehlenden Daten – Desktop und Handy.
Szene aus orbit3d.build mit erfundenem Ort und erfundener Bahn; Lauf und Endpunkt werden im Browser ersetzt."""
import pytest

from test_ui_layout import browser, server, FIND_OVERFLOW  # noqa: F401
from test_skytrace_3d import L1, L2, T0

pytest.importorskip("sgp4")


def _scene():
    from astroflow.modules.skytrace import orbit3d
    sat = orbit3d.satrec_from_tle(L1, L2)
    import numpy as np
    _, r, _ = sat.sgp4_array(*orbit3d._jd(np.array([T0])))
    d = orbit3d.rot_z(-orbit3d.gmst(T0), r[0])                       # Satellit genau über einem erfundenen Ort
    import erfa
    lon, lat, _ = (float(np.degrees(x)) for x in erfa.gc2gd(1, d * 1000))
    z = d - orbit3d.geodetic_to_ecef(lat, lon, 0)
    z /= np.linalg.norm(z)
    tm = orbit3d.icrs_to_teme(T0).T @ orbit3d.rot_z(orbit3d.gmst(T0), z)   # D → ICRS
    ra, dec = float(np.degrees(np.arctan2(tm[1], tm[0])) % 360), float(np.degrees(np.arcsin(tm[2])))
    return orbit3d.build(dict(site=dict(lat=lat, lon=lon, alt_m=0), t0=T0, exposure=dict(start=T0 - 5, seconds=10),
                              fov=dict(center=[ra, dec], radius_deg=.6, source="wcs", size_deg=[1.2, .9]),
                              trail=[[ra - .05, dec], [ra + .05, dec]],
                              satellites=[dict(name="TESTSAT", norad_id=99001, tle=[L1, L2], tle_age_days=.4)]))


@pytest.mark.parametrize("width", [375, 1440])
def test_3d_ansicht(server, browser, width, tmp_path):
    base, _ = server
    scene = _scene()
    assert scene["satellites"][0]["in_fov"]
    page = browser.new_page(viewport={"width": width, "height": 900}, reduced_motion="reduce")
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    cand = dict(id="K0001", key="obs/K0001", session="Test", kind="trail", timestamp=T0 + 5, exposure_seconds=10, view3d=True,
                frames=[dict(index=1, url="/synthetic/crop.png", width=40, height=30, hit=True, timestamp=T0, timestamp_text="Bild 1")],
                initial_frame=0, hit_indices=[0], crop=[0, 0, 40, 30], plausibility={},
                catalogs=[dict(provider="satellites", status="compatible", detail="Test", matches=[dict(name="TESTSAT", norad_id=99001, separation_arcsec=12)])])
    other = dict(cand, id="K0002", key="obs/K0002", view3d=False, catalogs=[])
    run = dict(id="synthetic", sessions=[dict(session="Test", frames=1, excluded=0, hits=1)], candidates=[cand, other], report_url=None, modified=1)
    page.route("**/api/skytrace/run?id=synthetic*", lambda route: route.fulfill(json=run))
    page.route("**/api/skytrace/orbit3d?*", lambda route: route.fulfill(
        json=scene if "K0001" in route.request.url else dict(ok=False, reason="Der Aufnahmestandort fehlt – Testhinweis.")))
    page.goto(base + "/#/skytrace/synthetic/kandidaten")
    page.wait_for_selector(".st3d-open")
    page.locator(".st3d-open").click()
    page.wait_for_function("document.querySelector('dialog.st3d')?._st3d")
    assert page.locator("dialog.st3d canvas").is_visible() and page.locator(".st3d-msg").is_hidden()
    labels = page.locator(".st3d-lbl")
    assert labels.count() == 2 and "TESTSAT" in labels.all_inner_texts()
    # Zeitregler: Satellit wandert, Zeit wird angezeigt
    assert "21:00:00 UTC" in page.locator(".st3d-time").inner_text()
    page.locator(".st3d-range input").evaluate("r => { r.value = +r.value + 30; r.dispatchEvent(new Event('input')); }")
    assert "+0:30" in page.locator(".st3d-time").inner_text()
    # Infokarte über die Legende, Blickfeld-Ansicht
    page.locator(".st3d-legend button", has_text="TESTSAT").click()
    card = page.locator(".st3d-card")
    assert card.is_visible() and "NORAD 99001" in card.inner_text() and "km/s" in card.inner_text()
    page.locator(".st3d-legend button", has_text="Teleskop").click()
    assert "Blickrichtung" in card.inner_text() and "1,20° × 0,90°" in card.inner_text()
    page.locator(".st3d-views [data-v=fov]").click()
    page.wait_for_function("document.querySelector('dialog.st3d')._st3d.state().mode === 'fov'")
    assert not page.evaluate(FIND_OVERFLOW)
    page.screenshot(path=str(tmp_path / f"skytrace-3d-{width}.png"))
    page.locator("dialog.st3d [data-x]").click()
    assert page.locator("dialog.st3d").count() == 0
    # Kandidat ohne Bahn: kein Knopf; Endpunkt mit Hinweis statt Fehler
    page.locator(".ccard", has_text="K0002").click()
    assert page.locator(".st3d-open").count() == 0
    page.evaluate("SkyTrace3D.open('synthetic', {id: 'K0002', key: 'obs/K0002'})")
    page.wait_for_selector(".st3d-msg.hint")
    assert "Testhinweis" in page.locator(".st3d-msg").inner_text()
    page.keyboard.press("Escape")
    assert page.locator("dialog.st3d").count() == 0
    assert not errors, errors
