"""Sage-Bildführung (Wunsch 19, Stufe 1): Projektion Bildpixel ↔ Himmel, Tour-Schritte, „Was ist an dieser Stelle?“
mit Antwort im Chat, Rückfälle ohne gespeicherte Projektion, Betrieb ohne Bilder-Modul und die Oberfläche im Browser.
SIMBAD ist eine Attrappe – kein Netz."""
import json
import math
import threading
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from astroflow.core.config import load_settings
from astroflow.modules.sage import guide, sources
from astroflow.server import create_app

STAR = {"main_id": "*  tet01 Ori C", "otype": "*", "ra": 83.8186, "dec": -5.3897, "V": 5.13, "sep": 0.0012}


def tap(rows, cols=None):
    cols = cols or (list(rows[0]) if rows else ["x"])
    return json.dumps({"metadata": [{"name": c} for c in cols], "data": [[r.get(c) for c in cols] for r in rows]})


QUERIES = []


def fake_fetch(self, url, data=None, params=None, headers=None, ttl=0, binary=False):
    q = (data or {}).get("QUERY", "")
    QUERIES.append(q)
    if "allfluxes" in q:
        return tap([STAR])
    if "JOIN ident AS i" in q:
        return tap([], ["oid", "main_id"])
    return tap([])


def offline_fetch(self, *a, **k):
    raise sources.SourceError("offline – keine zwischengespeicherte Antwort")


def solution(w=800, h=600, ra=83.82, dec=-5.39, scale=4.0):
    """Echte Bildbestimmung über `stacking.services.describe` (Katalogobjekte + Projektion in Anzeige-Pixeln)."""
    from astropy.wcs import WCS
    from astroflow.modules.stacking.services import describe
    wcs = WCS(naxis=2)
    wcs.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    wcs.wcs.crval = [ra, dec]
    wcs.wcs.crpix = [w / 2 + 0.5, h / 2 + 0.5]
    wcs.wcs.cd = [[-scale / 3600, 0], [0, scale / 3600]]
    return wcs, describe(wcs, w, h, "stack")


def add_entry(mod, sol, **extra):
    e = mod.store.add({"source": {"type": "upload"}, "preview_file": "uploads/orion.jpg", "width": sol["width"], "height": sol["height"],
                       "title": "Orion", "solution": {k: v for k, v in sol.items() if k != "objects"}, "objects": sol["objects"], **extra})
    mod.link_entry(e)
    return e


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("ASTROFLOW_MODULES", "stacking,sage")
    monkeypatch.setenv("ASTROFLOW_SAGE_OFFLINE", "0")
    monkeypatch.setattr(sources.Net, "fetch", fake_fetch)
    QUERIES.clear()
    app = create_app(load_settings(tmp_path))
    with TestClient(app) as c:
        c.app_mod = app.state.modules["sage"]
        c.app_ctx = app.state.ctx
        yield c


# ---------------------------------------------------------------- Projektion -- #
def test_projektion_wie_astropy():
    wcs, sol = solution()
    tan = sol["tan"]
    assert abs(guide.scale_arcsec(tan) - 4.0) < 1e-3
    for x, y in [(0, 0), (799, 599), (123.4, 456.7), (400, 300)]:
        (ra, dec), = wcs.pixel_to_world_values([[x, 599 - y]])          # Anzeige-y läuft entgegen FITS-y
        r2, d2 = guide.pix2sky(tan, x, y)
        assert math.hypot((ra - r2) * math.cos(math.radians(dec)), dec - d2) * 3600 < 0.1
        px, py = guide.sky2pix(tan, ra, dec)
        assert abs(px - x) < 0.05 and abs(py - y) < 0.05
    # Objektmarken liegen dort, wo die Projektion ihre Katalogposition hinlegt
    from astroflow.modules.stacking import catalog_resolve
    m42 = next(o for o in sol["objects"] if o["id"] == "M42")
    c = catalog_resolve("M42")
    px, py = guide.sky2pix(tan, c["ra"], c["dec"])
    assert abs(px - m42["x"]) < 0.5 and abs(py - m42["y"]) < 0.5


def test_projektion_aus_objektmarken_anpassen():
    _, sol = solution()
    tan = sol["tan"]
    pts = [(x, y, *guide.pix2sky(tan, x, y)) for x, y in [(50, 80), (700, 120), (420, 560), (200, 300)]]
    fit = guide.fit_tan(pts, 800, 600)
    for x, y in [(10, 10), (790, 590)]:
        a, b = guide.pix2sky(fit, x, y), guide.pix2sky(tan, x, y)
        assert math.hypot((a[0] - b[0]) * math.cos(math.radians(b[1])), a[1] - b[1]) * 3600 < 1
    assert guide.fit_tan(pts[:2], 800, 600) is None                                       # zu wenige Marken
    assert guide.fit_tan([(x, x, *guide.pix2sky(tan, x, x)) for x in (10, 200, 500)], 800, 600) is None   # auf einer Linie


# ------------------------------------------------------------------- Führung -- #
def test_tour_schritte_mit_ziel_und_zoom(client):
    mod = client.app_mod
    _, sol = solution()
    e = add_entry(mod, sol)
    s = client.post("/api/sage/kb/entities/m42/stories", json={"format": "tour"}).json()
    g = client.get(f"/api/sage/kb/stories/{s['id']}/guide").json()
    assert g["entry"] == e["id"] and g["width"] == 800 and g["has_tan"] and g["image"].startswith("/api/sage/files/")
    first, last = g["steps"][0], g["steps"][-1]
    assert first["object"] is None and first["zoom"] == 1 and (first["x"], first["y"]) == (400, 300) and last["object"] is None
    m42 = next(o for o in sol["objects"] if o["id"] == "M42")
    st = next(x for x in g["steps"] if x["object"] == m42["name"])
    assert (st["x"], st["y"]) == (m42["x"], m42["y"]) and st["mark"] in ("circle", "outline", "arrow")
    assert 1 <= st["zoom"] <= guide.ZOOM_MAX and st["entity"] and st["text"]
    assert len(g["steps"]) == len(s["segments"])
    small = next((x for x in g["steps"] if x["r"] and x["r"] / 600 < 0.02), None)
    assert small is None or small["mark"] == "arrow"
    assert client.get("/api/sage/kb/stories/99999/guide").status_code == 404


def test_tour_ohne_bild_faellt_sauber_weg(client):
    mod = client.app_mod
    _, sol = solution()
    e = add_entry(mod, sol)
    s = client.post("/api/sage/kb/entities/m42/stories", json={"format": "tour"}).json()
    mod.store.delete(e["id"])
    r = client.get(f"/api/sage/kb/stories/{s['id']}/guide")
    assert r.status_code == 404 and "gibt es nicht mehr" in r.json()["detail"]


# ------------------------------------------------------- Fragen per Klick -- #
def test_stelle_fragen_antwort_im_chat(client):
    mod = client.app_mod
    _, sol = solution()
    e = add_entry(mod, sol)
    m42 = next(o for o in sol["objects"] if o["id"] == "M42")
    r = client.post(f"/api/sage/kb/history/{e['id']}/spot", json={"x": m42["x"], "y": m42["y"], "r": 10})
    assert r.status_code == 200, r.text
    d = r.json()
    assert abs(d["ra"] - 83.82) < 0.1 and abs(d["dec"] + 5.39) < 0.1 and d["scope"] == f"image:{e['id']}"
    names = [h["name"] for h in d["hits"]]
    assert m42["name"] in names and next(h for h in d["hits"] if h["name"] == m42["name"])["inside"]
    star = next(h for h in d["hits"] if h["origin"] == "simbad")
    assert star["name"] == "* tet01 Ori C" and star["source"] and star["x"] is not None
    assert "Stelle im Bild" in d["answer"] and f"[S{star['source']}]" in d["answer"] and "V = 5,1 mag" in d["answer"]
    assert [s["id"] for s in d["sources"]] == [star["source"]]
    # Koordinaten für den Cache gerundet, Umkreissuche über SIMBAD
    q = next(q for q in QUERIES if "allfluxes" in q)
    assert "CIRCLE('ICRS', 83.8" in q and "ORDER BY sep" in q
    hist = client.get("/api/sage/kb/chat", params={"scope": d["scope"]}).json()
    assert [m["role"] for m in hist] == ["user", "assistant"] and "Was ist an dieser Stelle" in hist[0]["content"]
    data = json.loads(hist[1]["data"]) if isinstance(hist[1]["data"], str) else hist[1]["data"]
    assert data["spot"]["x"] == m42["x"] and data["sources"][0]["id"] == star["source"]


def test_stelle_ohne_simbad_nur_lokal(client, monkeypatch):
    monkeypatch.setattr(sources.Net, "fetch", offline_fetch)
    mod = client.app_mod
    _, sol = solution()
    e = add_entry(mod, sol)
    d = client.post(f"/api/sage/kb/history/{e['id']}/spot", json={"x": 5, "y": 5, "r": 5}).json()
    assert "SIMBAD wurde nicht abgefragt" in d["answer"] and all(h["origin"] != "simbad" for h in d["hits"])
    if not d["hits"]:
        assert "kein Objekt" in d["answer"]


def test_stelle_fehler(client):
    mod = client.app_mod
    _, sol = solution()
    e = add_entry(mod, sol)
    bare = mod.store.add({"source": {"type": "upload"}, "preview_file": "uploads/x.jpg", "width": 100, "height": 80, "title": "x",
                          "solution": {"ra": 10.0, "dec": 20.0}, "objects": []})
    url = "/api/sage/kb/history/{}/spot"
    r = client.post(url.format(bare["id"]), json={"x": 10, "y": 10})
    assert r.status_code == 409 and "Himmelsposition" in r.json()["detail"]
    assert client.post(url.format(e["id"]), json={"x": 5000, "y": 10}).status_code == 400
    assert client.post(url.format(e["id"]), json={"y": 10}).status_code == 400
    assert client.post(url.format(e["id"]), json={"x": "nan", "y": 10}).status_code == 400
    assert client.post(url.format("gibtsnicht"), json={"x": 1, "y": 1}).status_code == 404


def test_projektion_aus_stack_loesung_nachgeholt(client):
    mod = client.app_mod
    _, sol = solution()
    calls = []
    client.app_ctx.services["project_solution"] = lambda pid: calls.append(pid) or sol
    e = add_entry(mod, {**sol, "tan": None}, source={"type": "project", "project": 7})
    assert client.post(f"/api/sage/kb/history/{e['id']}/spot", json={"x": 400, "y": 300}).status_code == 200
    assert calls == [7] and mod.store.get(e["id"])["solution"]["tan"] == sol["tan"]           # gemerkt …
    client.post(f"/api/sage/kb/history/{e['id']}/spot", json={"x": 400, "y": 300})
    assert calls == [7]                                                                        # … und nicht erneut geholt


def test_stack_loesung_einer_anderen_version_wird_nicht_uebernommen(client):
    """Neuere Version mit anderem Ausschnitt (Marken verschoben) darf die Projektion der alten Bildkopie nicht ersetzen."""
    mod = client.app_mod
    _, sol = solution()
    _, newer = solution(ra=83.80, dec=-5.37)
    client.app_ctx.services["project_solution"] = lambda pid: newer
    e = add_entry(mod, {**sol, "tan": None}, source={"type": "project", "project": 7})
    assert not guide.same_view(e, newer) and guide.same_view(e, sol)
    tan = guide.tan_for(mod, e)
    assert tan != newer["tan"]
    if tan:                                                                    # Rückfall aus den Objektmarken des Eintrags
        a, b = guide.pix2sky(tan, 400, 300), guide.pix2sky(sol["tan"], 400, 300)
        assert math.hypot((a[0] - b[0]) * math.cos(math.radians(b[1])), a[1] - b[1]) * 3600 < 60


def test_projektion_aus_objektmarken(client):
    mod, kb = client.app_mod, client.app_mod.kb
    _, sol = solution()
    tan = sol["tan"]
    objs = []
    for i, (x, y) in enumerate([(60, 70), (720, 110), (400, 540)]):
        ra, dec = guide.pix2sky(tan, x, y)
        kb.upsert_entity(f"obj-{i}", kind="object", name=f"Testobjekt {i}", ra=ra, dec=dec, status="stub")
        kb.add_alias(f"obj-{i}", f"Testobjekt {i}")
        objs.append({"name": f"Testobjekt {i}", "x": x, "y": y, "radius": 6})
    e = mod.store.add({"source": {"type": "upload"}, "preview_file": "uploads/x.jpg", "width": 800, "height": 600, "title": "x",
                       "solution": {"ra": sol["ra"], "dec": sol["dec"]}, "objects": objs})
    d = client.post(f"/api/sage/kb/history/{e['id']}/spot", json={"x": 60, "y": 70, "r": 4}).json()
    ra, dec = guide.pix2sky(tan, 60, 70)
    assert math.hypot((d["ra"] - ra) * math.cos(math.radians(dec)), d["dec"] - dec) * 3600 < 2
    assert d["hits"][0]["name"] == "Testobjekt 0" and d["hits"][0]["inside"]


def test_ohne_bilder_modul(tmp_path, monkeypatch):
    """Sage allein: gespeicherte Projektion reicht für die Stelle, die Führung läuft mit den gespeicherten Marken."""
    monkeypatch.setenv("ASTROFLOW_MODULES", "stacking,sage")
    monkeypatch.setattr(sources.Net, "fetch", fake_fetch)
    app = create_app(load_settings(tmp_path), only=("sage",))          # Bilder-Modul ist sonst immer an
    for k in ("project_solution", "catalog_resolve", "plate_solver", "project_picture"):   # Dienste meldet es schon beim Anlegen an
        app.state.ctx.services.pop(k, None)
    with TestClient(app) as c:
        mod = app.state.modules["sage"]
        assert "stacking" not in app.state.modules
        _, sol = solution()
        kb = mod.kb
        kb.upsert_entity("m42", kind="object", name="M42 Orionnebel", ra=83.82, dec=-5.39, status="stub")
        kb.add_alias("m42", "M42 Orionnebel")
        e = add_entry(mod, sol)
        assert c.post(f"/api/sage/kb/history/{e['id']}/spot", json={"x": 400, "y": 300}).status_code == 200
        s = c.post("/api/sage/kb/entities/m42/stories", json={"format": "tour"}).json()
        assert c.get(f"/api/sage/kb/stories/{s['id']}/guide").json()["steps"]


# ------------------------------------------------------------ Oberfläche -- #
@pytest.fixture
def guide_server(tmp_path, monkeypatch):
    pytest.importorskip("playwright.sync_api")
    import uvicorn
    from PIL import Image, ImageDraw
    from test_ui_layout import _free_port
    for k, v in {"ASTROFLOW_MODULES": "stacking,sage", "ASTROFLOW_AUTO": "0", "ASTROFLOW_SAGE_OFFLINE": "1"}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(sources.Net, "fetch", offline_fetch)
    s = load_settings(tmp_path / "data")
    from astroflow.core.store import SettingsStore
    SettingsStore(s.data_dir / "settings.json").update("support", {"never": True})
    app = create_app(s)
    port = _free_port()
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    th = threading.Thread(target=srv.run, daemon=True)
    th.start()
    for _ in range(200):
        if srv.started:
            break
        time.sleep(0.05)
    mod = app.state.modules["sage"]
    _, sol = solution(ra=283.396, dec=33.029, scale=2.0)              # Ringnebel: kleine Objekte → Führung zoomt hinein
    im = Image.new("RGB", (800, 600), (8, 10, 18))
    dr = ImageDraw.Draw(im)
    for i in range(140):
        x, y = (i * 137) % 800, (i * 263) % 600
        dr.ellipse([x - 1, y - 1, x + 1, y + 1], fill=(230, 230, 255))
    im.save(mod.store.root / "uploads" / "orion.jpg", quality=85)
    add_entry(mod, sol)
    base = f"http://127.0.0.1:{port}"
    r = httpx.post(f"{base}/api/sage/kb/entities/m57/stories", json={"format": "tour"}, headers={"Origin": base}, timeout=30)
    assert r.status_code == 200, r.text
    yield base
    srv.should_exit = True
    th.join(10)


@pytest.mark.parametrize("size", [(375, 812), (1440, 900)])
def test_fuehrung_im_browser(guide_server, browser, size):
    from test_ui_layout import FIND_OVERFLOW
    base = guide_server
    ctx = browser.new_context(viewport={"width": size[0], "height": size[1]}, reduced_motion="reduce")
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(base + "/#/sage/o/m57/geschichten")
    page.wait_for_selector(".sw-guide .viewer.ready", timeout=20000)
    page.locator("label.sw-ton").click()                                   # ohne Vorlesen (kein Sprachdienst im Test)
    assert not page.locator("#swTon").is_checked()
    z0 = page.locator("#swTV .vbar .z").inner_text()
    page.locator(".sw-guide").focus()
    page.keyboard.press("ArrowRight")                                      # Tastatur: Einleitung
    page.wait_for_function("document.querySelector('#swSubN').textContent.startsWith('1 /')")
    page.keyboard.press("ArrowRight")                                      # erstes Objekt: Betrachter fährt hin und markiert
    page.wait_for_function("document.querySelector('#swSubN').textContent.startsWith('2 /')")
    assert page.locator(".sw-segs li.on").get_attribute("data-i") == "1"
    assert page.locator("#swSubT").inner_text().strip()
    page.wait_for_selector(".vov .sw-mk")
    n = int(page.locator("#swSubN").inner_text().split("/")[1].split()[0])
    for _ in range(n):                                                     # großes Objekt = Umriss ohne Zoom → bis zu einem kleineren
        if page.locator("#swTV .vbar .z").inner_text() != z0:
            break
        page.keyboard.press("ArrowRight")
        page.wait_for_timeout(150)
    assert page.locator("#swTV .vbar .z").inner_text() != z0
    # Ausstieg für reduzierte Bewegung: keine Zeichen-/Fahranimation
    assert page.evaluate("getComputedStyle(document.querySelector('.vov .sw-mk circle, .vov .sw-mk')).animationName") == "none"
    page.keyboard.press("k")                                               # Abspielen / Pause
    assert "Pause" in page.locator("#swPlay").inner_text()
    page.keyboard.press("k")
    assert "Abspielen" in page.locator("#swPlay").inner_text()
    cur = int(page.locator("#swSubN").inner_text().split("/")[0])
    page.keyboard.press("ArrowLeft")
    page.wait_for_function(f"document.querySelector('#swSubN').textContent.startsWith('{cur - 1} /')")
    assert not page.evaluate(FIND_OVERFLOW)
    # Stelle anklicken (freie Ecke) → Antwort im Sage-Chat, Stelle im Bild markiert
    box = page.locator("#swTV .viewer").bounding_box()
    page.mouse.click(box["x"] + box["width"] * 0.08, box["y"] + box["height"] * 0.9)
    page.wait_for_selector("#swChat.open .sw-m.a:has-text('Stelle im Bild')", timeout=15000)
    assert page.locator(".vov .sw-spot").count() == 1
    assert "SIMBAD wurde nicht abgefragt" in page.locator("#swChat").inner_text()
    # Tastatur-Weg zur selben Frage: „Hier fragen“ (Mitte der Ansicht)
    page.locator("#swChatX").click()
    page.locator("[data-g=spot]").click()
    page.wait_for_function("document.querySelectorAll('#swChat.open .sw-m.u').length >= 2", timeout=15000)
    ctx.close()
    assert not errors, errors


def test_sexagesimal_mit_uebertrag():
    assert guide.sexa(14.999999, True) == "01h 00m 00s"
    assert guide.sexa(359.99999, True) == "00h 00m 00s"
    assert guide.sexa(-5.3999999, False) == "−05° 24′ 00″"
    assert guide.sexa(83.82, True) == "05h 35m 17s"


def test_grosses_objekt_der_enzyklopaedie_umfasst_die_stelle(client, monkeypatch):
    """Mitte 1,2° entfernt, Objekt 3° groß: Vorauswahl berücksichtigt den Radius; Treffer bekommt eine Bildposition."""
    monkeypatch.setattr(sources.Net, "fetch", offline_fetch)
    mod, kb = client.app_mod, client.app_mod.kb
    _, sol = solution()
    ra, dec = guide.pix2sky(sol["tan"], 400, 300)
    kb.upsert_entity("riesen", kind="object", name="Riesenwolke", ra=ra, dec=dec - 1.2, size=180.0, status="stub")
    kb.upsert_entity("nah", kind="object", name="Nahes Objekt", ra=ra, dec=dec + 5 / 3600, size=0.1, status="stub")
    e = mod.store.add({"source": {"type": "upload"}, "preview_file": "uploads/x.jpg", "width": 800, "height": 600, "title": "x",
                       "solution": {k: v for k, v in sol.items() if k != "objects"}, "objects": []})
    d = client.post(f"/api/sage/kb/history/{e['id']}/spot", json={"x": 400, "y": 300, "r": 5}).json()
    hits = {h["name"]: h for h in d["hits"]}
    assert hits["Riesenwolke"]["inside"]
    assert abs(hits["Nahes Objekt"]["x"] - 400) < 1 and abs(hits["Nahes Objekt"]["y"] - 298.75) < 1   # 5″ nördlich = 1,25 px nach oben


def test_eine_gemeinsame_marke_reicht_nicht():
    """Eine einzelne Marke an gleicher Stelle beweist nichts (Drehung um sie bliebe unbemerkt) – zwei getrennte schon."""
    one = {"width": 800, "height": 600, "objects": [{"name": "A", "x": 400, "y": 300}]}
    assert not guide.same_view(one, {**one, "tan": {}})
    two = {"width": 800, "height": 600, "objects": [{"name": "A", "x": 400, "y": 300}, {"name": "B", "x": 650, "y": 420}]}
    assert guide.same_view(two, dict(two))
    near = {"width": 800, "height": 600, "objects": [{"name": "A", "x": 400, "y": 300}, {"name": "B", "x": 420, "y": 300}]}
    assert not guide.same_view(near, dict(near))                              # zu dicht beieinander
    rotated = {**two, "objects": [{"name": "A", "x": 400, "y": 300}, {"name": "B", "x": 520, "y": 560}]}
    assert not guide.same_view(two, rotated)
