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
                           "&& Object.values(document.querySelector('#steHost')._ste.state().reveal).every(v => v >= 1)"
                           # Ebenen blenden auf langsamen Rechnern (CI ohne GPU) noch ein, wenn die Heatmap schon fertig ist
                           "&& document.querySelector('#steHost')._ste.state().layers.heat >= 1", timeout=15000)
    st = _state(page)
    assert st["night"] == "2022-08-01" and st["heatVisible"] == ["2022-08-01"] and st["borders"] > 100000
    assert st["layers"]["heat"] == 1 and st["layers"]["runs"] == 0 and st["marks"]["sites"] == 3
    # 01.08.2022 ≈ Mitternacht Ortszeit am erfundenen Ort in Mitteleuropa → Region auf der Nachtseite → Nachtsicht automatisch an
    assert st["nv"] == 1 and st["sun"][0] > 10 and page.locator(".ste-nvpill").is_visible()
    assert st["dist"] < 1.6                                                    # auf die Region gezoomt
    assert "verschiedene Spuren: 1 … 14" in page.locator(".ste-legend").inner_text()
    stage = page.locator(".ste-stage").bounding_box()
    assert stage["height"] >= 340 and stage["width"] >= (330 if width < 500 else 1080), stage   # Desktop: Karte über die ganze Breite
    # Kopf ohne Erklärtext, Erklärung hinter dem Info-Knopf
    assert page.locator(".ste-title p, .ste-title + p").count() == 0 and page.locator("#steInfo").is_hidden()
    page.locator(".ste-info").click()
    assert "Bedienung" in page.locator("#steInfo").inner_text() and page.locator(".ste-info").get_attribute("aria-expanded") == "true"
    assert not page.evaluate(FIND_OVERFLOW)
    page.locator(".ste-info-x").click()
    assert page.locator("#steInfo").is_hidden()
    if width >= 861:                                    # Ebenen/Nächte als einklappbares Panel in der Karte
        side = page.locator(".ste-stage > .ste-side")
        assert side.is_visible()
        page.locator(".ste-paneltg").click()
        page.wait_for_function("getComputedStyle(document.querySelector('.ste-stage > .ste-side')).visibility === 'hidden'")
        page.locator(".ste-paneltg").click()
        side.wait_for(state="visible")
    else:
        assert page.locator(".ste > .ste-side").is_visible() and page.locator(".ste-paneltg").is_hidden()
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
    # Heatmap: Deckkraft-Regler (Standard 40 %, gemerkt) und Knopf an der Legende blenden sie samt Legende aus
    assert page.locator(".ste-layers .ste-heatop").input_value() == "40"
    page.evaluate("(() => { const i = document.querySelector('.ste-layers .ste-heatop'); i.value = 60; i.dispatchEvent(new Event('input', { bubbles: true })); })()")
    assert page.evaluate("JSON.parse(localStorage.getItem('af.skytrace.erde.heatop'))") == 0.6
    page.locator(".ste-legend-off").click()
    page.wait_for_function("document.querySelector('#steHost')._ste.state().layers.heat === 0")
    assert page.locator(".ste-legend").is_hidden() and not page.locator("[data-layer=heat]").is_checked()
    page.locator(".ste-layer:has([data-layer=heat])").click()
    page.wait_for_function("(s => s.layers.heat === 1 && s.reveal['2022-08-01'] === 1)(document.querySelector('#steHost')._ste.state())")
    page.evaluate("(() => { const i = document.querySelector('.ste-layers .ste-heatop'); i.value = 40; i.dispatchEvent(new Event('input', { bubbles: true })); })()")
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


@pytest.mark.parametrize("width", [375, 1440])
def test_eine_3d_erde_mit_zwei_kontexten(server, browser, width, tmp_path):
    """Eine Ansicht, ein Globus: „Nächte & Standorte“ ↔ „Analysen“ (Lauf mit Zeitleiste dreht die Erde), alter Link #/skytrace/3d führt dorthin,
    gemeinsame Ebenen (Heatmap der Nacht des Laufs) bleiben schaltbar."""
    base, _ = server
    scene = _scene(tmp_path)
    page = browser.new_page(viewport={"width": width, "height": 900})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.route("**/api/skytrace/run3d?*", lambda route: route.fulfill(json=scene))
    page.route("**/api/skytrace/runs", lambda route: route.fulfill(json=[dict(id="synthetic", title="Testfeld", modified=1, sessions=1, frames=60, excluded=3, candidates=2, preview=None)]))
    page.goto(base + "/#/skytrace/erde/2022-08-01")
    page.wait_for_function("document.querySelector('#steHost')?._ste?.state().night === '2022-08-01'")
    assert page.locator("#nav a[href='#/skytrace/3d'], nav a[href='#/skytrace/3d']").count() == 0      # nur noch ein Navigationspunkt
    page.locator(".ste-ctx [data-ctx=runs]").click()
    page.wait_for_function("document.querySelector('#steHost')._ste.state().runReady")
    st = _state(page)
    assert st["ctx"] == "runs" and st["on"]["runs"] and page.url.endswith("#/skytrace/3d")
    assert page.locator(".st3d-runs [data-run]").count() >= 1 and page.locator(".st3d-bar").is_visible() and page.locator(".st3d-views").is_visible()
    assert page.locator(".ste-sec-nights").is_hidden() and page.locator(".ste-layer:has([data-layer=heat])").is_visible()
    # Zeitleiste dreht die Erde (Erdfestes dreht mit)
    page.locator(".st3d-range input").evaluate(f"r => {{ r.value = {scene['pointing']['t'][0] + 1800}; r.dispatchEvent(new Event('input')); }}")
    assert abs(_state(page)["earthRot"]) > 0.1
    run = page.evaluate("document.querySelector('#steHost')._st3d.state()")
    assert run["t"] == pytest.approx(scene["pointing"]["t"][0] + 1800)
    page.locator(".st3d-views [data-v=scope]").click()
    page.wait_for_function("document.querySelector('#steHost')._st3d.state().mode === 'scope'")
    assert not page.evaluate(FIND_OVERFLOW)
    page.screenshot(path=str(tmp_path / f"erde-analysen-{width}.png"))
    # Kartenknopf aus der Teleskop-Ansicht: ein Klick genügt, die Karte öffnet sich über dem Standort des Teleskops
    page.locator(".ste-mode").click()
    page.wait_for_function("document.querySelector('#steHost')._ste.state().flat.on")
    page.locator(".ste-mode").click()
    page.wait_for_function("!document.querySelector('#steHost')._ste.state().flat.on")
    # zurück: Erde dreht sich in die erdfeste Lage, Lauf-Teile verschwinden
    page.locator(".ste-ctx [data-ctx=nights]").click()
    page.wait_for_function("(s => s.ctx === 'nights' && !s.runReady && Math.abs(s.earthRot) < 1e-6 && !s.flying)(document.querySelector('#steHost')._ste.state())")
    assert page.locator(".st3d-bar").is_hidden() and "/skytrace/erde" in page.url
    # alter Link
    page.goto(base + "/#/skytrace/3d")
    page.wait_for_function("(s => s?.ctx === 'runs' && s.runReady)(document.querySelector('#steHost')?._ste?.state())")
    assert not errors, errors
    page.close()


def test_alternative_in_der_erde(server, browser):
    """Detailkarte der Nacht mit Alternative (Listenort 372 m neben dem zurückgerechneten Punkt), Marke auf Globus und flacher Karte."""
    base, _ = server
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(base + "/#/skytrace/erde/2022-05-17")
    page.wait_for_function("(s => s?.night === '2022-05-17' && !s.flying)(document.querySelector('#steHost')?._ste?.state())", timeout=15000)
    assert _state(page)["marks"]["alts"] == 1 and page.locator(".ste-lbl.alt").count() == 1
    page.locator('.ste-nights button[data-n="2022-05-17"], button[data-n="2022-05-17"]').first.click()
    card = page.locator(".ste-card")
    card.locator(".stn-alt").wait_for()
    assert "Beispielberg" in card.inner_text() and "Grenze für automatische Zuordnung 300 m" in card.inner_text()
    assert card.locator("[data-alt-apply=b]").count() == 1 and card.locator("[data-alt-move=b]").count() == 1
    page.locator(".ste-mode").click()                                       # flache Karte zeichnet die Verbindung mit
    page.wait_for_function("document.querySelector('#steHost')._ste.state().flat.on")
    assert not errors, errors
    page.close()


def _wheel_until(page, dy, cond, steps=80):
    for _ in range(steps):
        page.mouse.wheel(0, dy)
        page.wait_for_timeout(25)
        if page.evaluate(f"(s => {cond})(document.querySelector('#steHost')._ste.state())"):
            return True
    return False


@pytest.mark.parametrize("width", [375, 1440])
def test_kartenmodus_kugel_und_flache_karte(server, browser, width, tmp_path):
    """Wie bei Google Maps: nah heran wird der Globus zur flachen Karte (Punkt unter dem Zeiger bleibt), dort 1:1 verschieben ohne Nachlauf,
    Koordinaten/Maßstab, Antippen eines Vorschlags öffnet die Karte der Nacht, Kacheln nur auf Knopfdruck; herausgezoomt wieder die Kugel."""
    base, _ = server
    page = browser.new_page(viewport={"width": width, "height": 900}, reduced_motion="reduce" if width < 500 else "no-preference")
    errors, tiles = [], []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("request", lambda r: tiles.append(r.url) if "tile." in r.url else None)
    page.route("**/*tile*.org/**", lambda route: route.fulfill(status=204))       # nie ins Netz
    page.goto(base + "/#/skytrace/erde/2022-08-01")
    page.wait_for_function("document.querySelector('#steHost')?._ste && !document.querySelector('#steHost')._ste.state().flying", timeout=15000)
    page.locator(".ste-stage").scroll_into_view_if_needed()
    box = page.locator(".ste-stage").bounding_box()
    w, h = box["width"], box["height"]
    px, py = box["x"] + w * .6, box["y"] + h * .55
    page.mouse.move(px, py)
    assert _wheel_until(page, -120, "s.dist < 1.09 || s.flat.on"), _state(page)
    assert not _state(page)["flat"]["on"]
    page.mouse.move(px + 1, py); page.mouse.move(px, py)                         # Koordinate unter dem Zeiger auf der Kugel
    globe_pt = _state(page)["coord"]
    assert globe_pt
    assert _wheel_until(page, -120, "s.flat.on"), _state(page)
    page.wait_for_function("document.querySelector('#steHost')._ste.state().flat.fade === 1")
    st = _state(page)
    assert st["dist"] < 1.075 and st["flat"]["pxPerKm"] > 1
    # Übergang ohne Sprung: der Ort unter dem Zeiger ist auf der Karte derselbe (Zoom um den Zeiger, Kugel ↔ Mercator, ≤ 0,05°)
    lat, lon = page.evaluate(f"document.querySelector('#steHost')._ste.flat.at({w * .6}, {h * .55})")
    assert abs(globe_pt[0] - lat) < .05 and abs(globe_pt[1] - lon) < .05, (globe_pt, lat, lon)
    # 1:1 verschieben: Punkt unter dem Zeiger bleibt, kein Nachlauf
    a = page.evaluate(f"document.querySelector('#steHost')._ste.flat.at({w * .3}, {h * .4})")
    page.mouse.move(box["x"] + w * .3, box["y"] + h * .4)
    page.mouse.down()
    page.mouse.move(box["x"] + w * .55, box["y"] + h * .6, steps=6)
    page.mouse.up()
    b = page.evaluate(f"document.querySelector('#steHost')._ste.flat.at({w * .55}, {h * .6})")
    assert abs(a[0] - b[0]) < 1e-6 and abs(a[1] - b[1]) < 1e-6, (a, b)
    c1 = _state(page)["flat"]["center"]
    page.wait_for_timeout(300)
    assert _state(page)["flat"]["center"] == c1                                     # kein Nachlauf
    ro = page.locator(".ste-readout").inner_text()
    assert "°" in ro and ("km" in ro or " m" in ro)
    # Vorschlag antippen → Karte der Nacht
    sx, sy = page.evaluate("document.querySelector('#steHost')._ste.flat.screenOf(46.234567, 13.912345)")
    if not (10 < sx < w - 10 and 60 < sy < h - 60):
        page.evaluate("document.querySelector('#steHost')._ste.flat")             # außerhalb: zurück zur Mitte schieben
        page.mouse.move(box["x"] + w / 2, box["y"] + h / 2); page.mouse.down(); page.mouse.move(box["x"] + w / 2 - (sx - w / 2), box["y"] + h / 2 - (sy - h / 2), steps=4); page.mouse.up()
        sx, sy = page.evaluate("document.querySelector('#steHost')._ste.flat.screenOf(46.234567, 13.912345)")
    page.mouse.click(box["x"] + sx, box["y"] + sy)
    page.locator(".ste-card").get_by_text("übernehmen").wait_for()
    page.locator(".ste-card [data-a=x]").click()
    assert not tiles
    page.screenshot(path=str(tmp_path / f"erde-flach-{width}.png"))
    page.locator(".ste-tiles [data-tiles=osm]").click()                               # erst jetzt Kartenkacheln
    page.wait_for_timeout(200)
    assert tiles and all("openstreetmap" in u for u in tiles)
    assert not page.evaluate(FIND_OVERFLOW)
    # herauszoomen → wieder die Kugel
    page.mouse.move(box["x"] + w / 2, box["y"] + h / 2)
    assert _wheel_until(page, 250, "!s.flat.on && s.flat.fade === 0"), _state(page)
    st = _state(page)
    assert st["dist"] > 1.08 and page.locator(".ste-labels").is_visible()
    assert not errors, errors
    page.close()


def _tile_png(z, x, y):
    """Kachel-Attrappe (kein echter Kartendienst): scharfe 1-px-Linien und Text mit z/x/y und einem erfundenen Ortsnamen."""
    import io
    from PIL import Image, ImageDraw
    im = Image.new("RGB", (256, 256), (232, 228, 218))
    d = ImageDraw.Draw(im)
    d.rectangle([0, 0, 255, 255], outline=(150, 150, 150))
    for i in range(0, 256, 16):
        d.line([(i, 0), (i, 255)], fill=(210, 205, 195))
    d.text((12, 12), f"{z}/{x}/{y}", fill=(20, 20, 20))
    d.text((60, 120), "Beispieldorf", fill=(30, 30, 30))
    b = io.BytesIO(); im.save(b, "PNG"); return b.getvalue()


@pytest.mark.parametrize("dpr", [1, 2])
def test_kartenkacheln_passend_zur_aufloesung(server, browser, dpr, tmp_path):
    """Kacheln in der flachen Karte: Zoomstufe so, dass 1 Kachelpixel ≈ 1 Gerätepixel, höchstens 96 je Ansicht, Nachladen beim Zoomen,
    nur auf Knopfdruck und nur als Attrappe (kein OSM-Zugriff)."""
    import math
    import re
    base, _ = server
    page = browser.new_page(viewport={"width": 1440, "height": 900}, device_scale_factor=dpr, reduced_motion="reduce")
    errors, asked = [], []
    page.on("pageerror", lambda e: errors.append(str(e)))

    def tile(route):
        z, x, y = map(int, re.search(r"/(\d+)/(\d+)/(\d+)\.png", route.request.url).groups())
        asked.append((z, x, y))
        route.fulfill(status=200, content_type="image/png", body=_tile_png(z, x, y))
    page.route("**/*tile*.org/**", tile)
    page.goto(base + "/#/skytrace/erde/2022-08-01")
    page.wait_for_function("document.querySelector('#steHost')?._ste && !document.querySelector('#steHost')._ste.state().flying", timeout=15000)
    page.evaluate("document.querySelector('#steHost')._ste.flat.enter()")
    page.wait_for_function("document.querySelector('#steHost')._ste.state().flat.fade === 1")
    assert not asked                                                                  # ohne Knopfdruck keine Kacheln
    page.locator(".ste-tiles [data-tiles=osm]").click()
    page.wait_for_function("(f => f.tileZ && f.tileWant > 0)(document.querySelector('#steHost')._ste.state().flat)")
    box = page.locator(".ste-stage").bounding_box()
    page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    for _ in range(14):                                                               # stark hineinzoomen: feinere Kacheln werden nachgeladen
        page.mouse.wheel(0, -160); page.wait_for_timeout(40)
    page.wait_for_timeout(600)
    f = _state(page)["flat"]
    # nach der Überblendung keine groben Platzhalter mehr: Mittelpixel der Ansicht stammt aus einer Kachel der Zielstufe
    page.wait_for_function("document.querySelector('#steHost')._ste.state().flat.tileZ !== null")
    want = round(math.log2(f["Z"] * f["dpr"] / 256))
    assert f["dpr"] == dpr and f["tileZ"] == min(want, 19), f                         # 1 Kachelpixel ≈ 1 Gerätepixel
    assert f["tileWant"] <= 96 and max(z for z, _, _ in asked) == f["tileZ"]
    assert len({a for a in asked}) == len(asked)                                       # nichts doppelt geladen (Cache)
    assert "OpenStreetMap" in page.locator(".ste-attr").inner_text()                  # Quellenangabe sichtbar
    # Datumsgrenze: Mitte bei 179,9° O, ein Ort bei 179,9° W liegt gleich daneben (nicht eine Weltbreite entfernt)
    page.evaluate("document.querySelector('#steHost')._ste.flat.flyTo(10, 179.9, 2)")
    sx, _ = page.evaluate("document.querySelector('#steHost')._ste.flat.screenOf(10, -179.9)")
    assert 30 < sx - box["width"] / 2 < 60, sx                                         # 0,2° bei 10° Breite und 2 px/km ≈ 44 px östlich
    page.screenshot(path=str(tmp_path / f"erde-kacheln-dpr{dpr}.png"))
    assert not errors, errors
    page.close()


def test_mausrad_ueber_seitenleiste_scrollt_statt_zu_zoomen(server, browser, tmp_path):
    """Nutzerbefund 06.11.2026: über dem Glas-Panel (Nachtliste) zoomte das Mausrad die Karte – auf Kugel und flacher Karte."""
    base, _ = server
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.route("**/*tile*.org/**", lambda route: route.fulfill(status=204))
    page.goto(base + "/#/skytrace/erde/2022-08-01")
    page.wait_for_function("document.querySelector('#steHost')?._ste && !document.querySelector('#steHost')._ste.state().flying", timeout=15000)
    page.locator(".ste-stage").scroll_into_view_if_needed()
    nights = page.locator(".ste-stage > .ste-side .ste-nights")
    nights.evaluate("el => { el.style.maxHeight = '3rem' }")                # sicher scrollbar, auch mit wenigen Nächten
    nb = nights.bounding_box()
    d0 = _state(page)["dist"]
    page.mouse.move(nb["x"] + nb["width"] / 2, nb["y"] + nb["height"] / 2)
    for _ in range(4):
        page.mouse.wheel(0, 120); page.wait_for_timeout(40)
    assert _state(page)["dist"] == d0
    assert nights.evaluate("el => el.scrollTop") > 0
    # flache Karte: ebenso
    box = page.locator(".ste-stage").bounding_box()
    page.mouse.move(box["x"] + box["width"] * .35, box["y"] + box["height"] * .55)
    assert _wheel_until(page, -120, "s.flat.on"), _state(page)
    page.wait_for_function("document.querySelector('#steHost')._ste.state().flat.fade === 1")
    z0 = _state(page)["flat"]["pxPerKm"]
    nights.evaluate("el => { el.scrollTop = 0 }")
    page.mouse.move(nb["x"] + nb["width"] / 2, nb["y"] + nb["height"] / 2)
    for _ in range(4):
        page.mouse.wheel(0, 120); page.wait_for_timeout(40)
    assert _state(page)["flat"]["pxPerKm"] == z0 and nights.evaluate("el => el.scrollTop") > 0
    # Kartenknöpfe über der Karte deckend (Glas-Stil machte sie fast durchsichtig)
    alpha = page.evaluate("getComputedStyle(document.querySelector('.ste-tiles .seg')).backgroundColor")
    assert alpha.startswith("rgba(11, 13, 16, 0.9"), alpha
    page.screenshot(path=str(tmp_path / "erde-seitenleiste.png"))
    assert not errors, errors
    page.close()


# ---- Satellit anklicken (0.45.0): Hover, Auswahl bei mehreren, Infokarte mit Ausschnitt, flache Karte, Handy ----
def _sat_detail(name, norad):
    crop = {"url": "/api/skytrace/satellite/crop?run=x&key=Testfeld%2FK0001", "width": 300, "height": 200, "line": [[30, 170], [270, 30]],
            "kind": "kandidat", "frame": 31, "time": "2024-03-01T21:00:05 UTC"}
    return dict(ok=True, name=name, norad_id=norad, intl_id="2024-001A", kind="Satellit", launch_date="2024-01-15", launch_year=2024, owner="ESA",
                decay_date=None, catalog_source="SATCAT-Kopie", orbit_catalog={"perigee_km": 410, "apogee_km": 420}, tle_age_days=.4,
                speed={"arcsec_s": 2250.0, "kind": "bahn", "text": "Spurlänge ÷ Durchgangszeit der Bahn"},
                brightness={"mag": 7.2, "err": .6, "src": "estimate", "note": "Richtwert des Geräts – nur Schätzung"},
                certainty={"level": "eindeutig", "text": "Eindeutig – Lage, Richtung und Zeit passen.", "facts": ["Bahn im Mittel 12″ neben der Spur"]},
                links={"celestrak": f"https://celestrak.org/satcat/table-satcat.php?CATNR={norad}", "n2yo": f"https://www.n2yo.com/satellite/?s={norad}"},
                crop=crop if norad == 99001 else None,
                crop_note=None if norad == 99001 else "Für diese Spur ist kein Bildausschnitt gespeichert.")


def _sat_page(browser, base, tmp_path, width, touch=False):
    """Seite mit zwei Satelliten auf derselben Bahn (wie ein Starlink-Zug); Details, Ausschnitt und Lauf werden im Browser ersetzt."""
    import io
    from PIL import Image
    scene = _scene(tmp_path)
    hit = next(h for h in scene["hits"] if h["kind"] == "sat")
    scene["hits"].append(dict(hit, name="TESTSAT-B", norad_id=99002, key="Testfeld/K0003", candidate="K0003", focus=False))
    buf = io.BytesIO()
    Image.new("RGB", (300, 200), (12, 14, 18)).save(buf, "PNG")
    page = browser.new_page(viewport={"width": width, "height": 900}, reduced_motion="reduce", has_touch=touch)
    errors, asked = [], []
    page.on("pageerror", lambda e: errors.append(str(e)))

    def detail(route):
        asked.append(route.request.url)
        b = "99002" in route.request.url
        route.fulfill(json=_sat_detail("TESTSAT-B" if b else "TESTSAT", 99002 if b else 99001))
    page.route("**/api/skytrace/run3d?*", lambda route: route.fulfill(json=scene))
    page.route("**/api/skytrace/runs", lambda route: route.fulfill(json=[dict(id=scene["run"], title="Testfeld", modified=1, sessions=1, frames=60, excluded=3,
                                                                              candidates=2, preview=None)]))
    page.route("**/api/skytrace/satellite?**", detail)
    page.route("**/api/skytrace/satellite/crop?**", lambda route: route.fulfill(body=buf.getvalue(), content_type="image/png"))
    page.goto(base + "/#/skytrace/3d")
    page.wait_for_function("(s => s?.runReady && !s.flying)(document.querySelector('#steHost')?._ste?.state())", timeout=15000)
    page.locator(".ste-stage").scroll_into_view_if_needed()
    return page, scene, errors, asked


def _sat_xy(page):
    box = page.locator(".ste-stage canvas:not(.ste-flat)").bounding_box()
    pts = page.evaluate("document.querySelector('#steHost')._st3d.screen()")
    p = next(p for p in pts if p["x"] is not None)
    return box["x"] + p["x"], box["y"] + p["y"]


def _shot(page, tmp_path, name):
    page.screenshot(path=str(tmp_path / name))
    out = os.environ.get("ASTROFLOW_SCREENSHOTS")
    if out:
        shutil.copy(tmp_path / name, out)


def test_satellit_anklicken(server, browser, tmp_path):
    from test_skytrace_3d import T0
    base, _ = server
    page, scene, errors, asked = _sat_page(browser, base, tmp_path, 1440)
    x, y = _sat_xy(page)
    assert not asked                                                   # Details erst beim Anklicken (Handy: run3d bleibt schlank)
    # Überfahren: Zeiger, Namensschild, Hervorhebung
    page.mouse.move(x + 6, y + 6)
    page.wait_for_selector(".ste-tip:not([hidden])")
    assert "TESTSAT" in page.locator(".ste-tip").inner_text() and "ste-hot" in page.locator(".ste-stage canvas:not(.ste-flat)").get_attribute("class")
    # Klick: zwei Objekte an dieser Stelle → Auswahl
    page.mouse.click(x + 6, y + 6)
    page.wait_for_selector(".ste-pick:not([hidden])")
    assert page.evaluate("document.querySelector('#steHost')._ste.chooser()") == ["TESTSAT", "TESTSAT-B"]
    page.locator(".ste-pick [role=menuitem]").first.click()
    card = page.locator(".ste-card")
    page.wait_for_function("document.querySelector('.ste-card .st3d-crop img')?.naturalWidth > 0")
    txt = card.inner_text()
    for want in ("NORAD 99001", "Eindeutig", "2024-001A", "15.01.2024", "7,2 mag", "Spur zwischen den Linien", "410–420 km"):
        assert want in txt, want
    link = card.locator("a", has_text="CelesTrak")
    assert link.get_attribute("href").endswith("CATNR=99001") and link.get_attribute("rel") == "noopener noreferrer"
    assert page.evaluate("document.querySelector('#steHost')._ste.satSel()")["name"] == "TESTSAT"
    assert len(asked) == 1 and "norad=99001" in asked[0] and page.locator(".ste-pick").is_hidden()
    assert not page.evaluate(FIND_OVERFLOW)
    _shot(page, tmp_path, "satellit-karte-1440.png")
    # zweiter Satellit: ohne Ausschnitt → klarer Hinweis; erneut der erste (per Tastatur) → aus dem Zwischenspeicher
    page.mouse.click(x + 6, y + 6)
    page.locator(".ste-pick [role=menuitem]").nth(1).click()
    page.wait_for_function("document.querySelector('.ste-card').innerText.includes('kein Bildausschnitt')")
    assert "TESTSAT-B" in card.inner_text()
    page.mouse.click(x + 6, y + 6)
    page.wait_for_function("document.activeElement?.closest('.ste-pick')")
    page.keyboard.press("Enter")
    page.wait_for_selector(".ste-card .st3d-crop img")
    assert len(asked) == 2
    # Klick auf den Ausschnitt öffnet die Aufnahme im Lauf (Kandidat mit Trefferbild)
    frame = dict(index=31, url="/synthetic/crop.png", width=40, height=30, hit=True, timestamp=T0, timestamp_text="Bild 1")
    cand = dict(id="K0001", key="Testfeld/K0001", session="Testfeld", kind="trail", timestamp=T0 + 5, exposure_seconds=10, view3d=True, frames=[frame],
                initial_frame=0, hit_indices=[0], crop=[0, 0, 40, 30], plausibility={}, catalogs=[])
    run = dict(id=scene["run"], sessions=[dict(session="Testfeld", frames=60, excluded=3, hits=1, video_url="/synthetic/v.mp4")], candidates=[cand],
               report_url=None, modified=1)
    page.route("**/api/skytrace/run?id=*", lambda route: route.fulfill(json=run))
    page.locator(".st3d-crop button").click()
    page.wait_for_selector(".ccard.on")
    assert page.url.endswith(f"#/skytrace/{scene['run']}/kandidaten") and "K0001" in page.locator(".ccard.on").inner_text()
    assert not errors, errors
    page.close()


def test_satellit_in_der_flachen_karte(server, browser, tmp_path):
    """Flache Karte: Bodenspur + Punkt unter dem Satelliten beim Durchgang, anklickbar (zwei an derselben Stelle → Auswahl)."""
    base, _ = server
    page, scene, errors, asked = _sat_page(browser, base, tmp_path, 1440)
    page.locator(".ste-mode").click()
    page.wait_for_function("document.querySelector('#steHost')._ste.state().flat.on")
    h = next(x for x in scene["hits"] if x["kind"] == "sat")
    lat, lon = h["ground"][round((h["t_pass"] - h["window"][0]) / h["ground_step"])]
    page.evaluate(f"document.querySelector('#steHost')._ste.flat.flyTo({lat}, {lon}, 0.12)")
    # flyTo setzt nur „dirty“ – die Lage der Punkte stammt aus dem nächsten gezeichneten Bild (CI ohne GPU zeichnet langsamer)
    near = ("(() => { const s = document.querySelector('#steHost')._ste.state().flat.sats, b = document.querySelector('.ste-stage')"
            ".getBoundingClientRect(); return s.length === 2 && Math.abs(s[0].x - b.width / 2) < 2 && Math.abs(s[0].y - b.height / 2) < 2 })()")
    try:
        page.wait_for_function(near, timeout=10000)                       # Punkt beim Durchgang liegt am Bodenpunkt
    except Exception:
        s = page.evaluate("document.querySelector('#steHost')._ste.state().flat.sats")
        raise AssertionError(f"Satellitenpunkt nicht am Bodenpunkt (Bühne {page.locator('.ste-stage').bounding_box()}): {s}")
    s = page.evaluate("document.querySelector('#steHost')._ste.state().flat.sats")
    box = page.locator(".ste-stage").bounding_box()
    px, py = box["x"] + s[0]["x"] + 5, box["y"] + s[0]["y"] + 4
    page.mouse.move(px, py)
    page.wait_for_selector(".ste-tip:not([hidden])")
    page.wait_for_function("document.querySelector('#steHost')._ste.state().flat.sats.some(x => x.on)")
    page.mouse.click(px, py)
    page.wait_for_selector(".ste-pick:not([hidden])")
    page.locator(".ste-pick [role=menuitem]").nth(1).click()
    page.wait_for_function("document.querySelector('.ste-card').innerText.includes('NORAD 99002')")
    assert page.evaluate("document.querySelector('#steHost')._ste.satSel()")["name"] == "TESTSAT-B"
    assert not errors, errors
    page.close()


def test_satellit_antippen_handy(server, browser, tmp_path):
    """Handy: Antippen mit dem Finger trifft auch knapp neben der Bahn (große Trefferfläche), Karte unten, nichts ragt heraus."""
    base, _ = server
    page, scene, errors, asked = _sat_page(browser, base, tmp_path, 375, touch=True)
    x, y = _sat_xy(page)
    page.touchscreen.tap(x + 15, y + 12)                               # bis ~19 px neben der Bahn: Fingerradius 26 px (Maus 14 px)
    page.wait_for_selector(".ste-pick:not([hidden])")
    page.locator(".ste-pick [role=menuitem]").first.tap()
    page.wait_for_selector(".ste-card .st3d-crop img")
    card = page.locator(".ste-card").bounding_box()
    stage = page.locator(".ste-stage").bounding_box()
    assert card["y"] > stage["y"] + stage["height"] * .3 and card["x"] >= stage["x"] and card["x"] + card["width"] <= stage["x"] + stage["width"] + 1
    assert not page.evaluate(FIND_OVERFLOW)
    _shot(page, tmp_path, "satellit-karte-375.png")
    assert not errors, errors
    page.close()
