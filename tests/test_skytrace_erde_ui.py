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
