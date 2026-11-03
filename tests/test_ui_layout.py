"""Oberfläche im echten Browser: ragt auf Handy- oder Desktopbreite etwas über den Rand seines Kastens oder der Seite?

Braucht Playwright + Chromium (`pip install playwright && python -m playwright install chromium`); fehlt es, wird übersprungen.
Prüft jede Ansicht mit Demodaten (ein entwickeltes Objekt mit absichtlich langem Namen, alle Module geladen).
"""
import socket
import threading
import time

import pytest

sync_api = pytest.importorskip("playwright.sync_api")

import uvicorn

from astroflow.core.config import load_settings
from astroflow.core.modules import AVAILABLE
from astroflow.core.store import SettingsStore
from astroflow.modules.stacking import demo
from astroflow.modules.stacking.db import DB
from astroflow.modules.stacking.pipeline import Pipeline
from astroflow.modules.stacking.reviewer import ReviewerConfig
from astroflow.server import create_app

LONG = "NGC 6888 Crescentnebel im Sternbild Schwan (Langname)"
SIZES = {"handy": (375, 812), "schmal": (790, 1100), "desktop": (1440, 900)}

# Sucht Elemente, die über ihren nächsten sichtbaren Kasten (Rahmen oder Hintergrund) bzw. die Seite hinausragen.
# Absichtlich beschnittene oder scrollende Bereiche (overflow ≠ visible) und schwebende Elemente zählen nicht.
FIND_OVERFLOW = """() => {
  const vw = document.documentElement.clientWidth, out = [];
  const boxy = (s) => parseFloat(s.borderRightWidth) > 0 || !/rgba\\(0, 0, 0, 0\\)|transparent/.test(s.backgroundColor);
  const name = (e) => e.tagName.toLowerCase() + (e.id ? '#' + e.id : '') + (typeof e.className === 'string' && e.className ? '.' + e.className.trim().split(/\\s+/).join('.') : '');
  if (document.documentElement.scrollWidth > vw + 1) out.push(`Seite ${document.documentElement.scrollWidth} px breit statt ${vw}`);
  for (const e of document.querySelectorAll('#view *, .side *, .top *')) {
    const r = e.getBoundingClientRect();
    if (!r.width || !r.height || e.closest('svg') && e.tagName !== 'svg') continue;
    const s = getComputedStyle(e);
    if (s.visibility === 'hidden' || s.position === 'fixed') continue;
    let box = null, clipped = false;
    for (let a = e.parentElement; a && a !== document.body; a = a.parentElement) {
      const as = getComputedStyle(a);
      if (as.overflowX !== 'visible' || as.position === 'fixed' || as.position === 'sticky') { clipped = true; break; }
      if (boxy(as)) { box = a; break; }
    }
    if (clipped) continue;
    const right = box ? box.getBoundingClientRect().right : vw;
    if (r.right > right + 1) out.push(`${name(e)} ragt ${Math.round(r.right - right)} px über ${box ? name(box) : 'die Seite'}: ${(e.textContent || '').trim().slice(0, 40)}`);
    else if (s.overflowX === 'visible' && e.children.length === 0 && e.scrollWidth > e.clientWidth + 1 && e.clientWidth > 0 && s.display !== 'inline')
      out.push(`Text in ${name(e)} ragt ${e.scrollWidth - e.clientWidth} px heraus: ${(e.textContent || '').trim().slice(0, 40)}`);
  }
  return [...new Set(out)].slice(0, 15);
}"""


def _free_port():
    with socket.socket() as so:
        so.bind(("127.0.0.1", 0))
        return so.getsockname()[1]


def _skytrace_run(run):
    """Ein SkyTrace-Lauf mit langen Namen und Katalogergebnissen (für Statistik und Laufliste)."""
    import json
    (run / "obs").mkdir(parents=True)
    sat = {"provider": "satellites", "status": "compatible", "matches": [{"name": "COSMOS 2251 DEB (Trümmerteil mit Langname)", "norad_id": 34427, "object_type": "Trümmerteil"}]}
    cands = [{"id": f"K{i:04d}", "session": f"2022-09-01_observation_{LONG}", "kind": "trail", "timestamp": 1662069600.0 + 3600 * i,
              "catalogs": [sat if i % 2 else {"provider": "satellites", "status": "checked_no_match"}, {"provider": "aircraft", "status": "needs_access"}]}
             for i in range(5)]
    (run / "obs" / "kandidaten.json").write_text(json.dumps(cands), encoding="utf-8")
    (run / "videos.json").write_text(json.dumps([{"session": f"2022-09-01_observation_{LONG}", "frames": 1234, "excluded": 56,
                                                  "state": "Fertig", "candidates_file": "obs/kandidaten.json"}]), encoding="utf-8")


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    root = tmp_path_factory.mktemp("ui")
    with pytest.MonkeyPatch.context() as mp:
        for k, v in {"ASTROFLOW_MODULES": ",".join(AVAILABLE), "ASTROFLOW_AUTO": "0", "ASTROFLOW_GAIA": "0", "ASTROFLOW_SAGE_OFFLINE": "1"}.items():
            mp.setenv(k, v)
        mp.delenv("SKYTRACE_HOME", raising=False)          # SkyTrace setzt sie beim Laden prozessweit – danach wieder entfernen
        s = load_settings(root / "data")
        SettingsStore(s.data_dir / "settings.json").update("skytrace", {"home": str(root / "skytrace")})
        _skytrace_run(root / "skytrace" / "Ergebnisse" / "2026-10-28_10-00-00-000001")
        demo.generate(s.input_dir / "night", n_lights=16, n_darks=0, n_flats=0)
        db = DB(s.db_path)
        pl = Pipeline(s, db, ReviewerConfig())
        pl.scan()
        pid = db.q("SELECT id FROM projects")[0]["id"]
        pl.process(pid)
        for _ in range(2):                                   # 3 Versionen -> Speicherbelegung bietet „Ältere Versionen“ an
            pl.process(pid, {"mode": "develop"})
        db.x("UPDATE projects SET name=? WHERE id=?", (LONG, pid))
        port = _free_port()
        srv = uvicorn.Server(uvicorn.Config(create_app(s), host="127.0.0.1", port=port, log_level="warning"))
        th = threading.Thread(target=srv.run, daemon=True)
        th.start()
        for _ in range(200):
            if srv.started:
                break
            time.sleep(0.05)
        yield f"http://127.0.0.1:{port}", pid
        srv.should_exit = True
        th.join(10)


@pytest.fixture(scope="module")
def browser():
    with sync_api.sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception as e:                               # Paket da, Browser nicht installiert
            try:
                b = p.chromium.launch(channel='msedge')      # vorhandenes Windows-Edge geht ebenfalls
            except Exception:
                pytest.skip(f"Chromium/Edge fehlt: {e}")
        yield b
        b.close()


ROUTES = ["#/", "#/bilder", "#/p/{pid}/bild", "#/p/{pid}/aufnahmen", "#/p/{pid}/versionen", "#/p/{pid}/optionen",
          "#/import", "#/activity", "#/skytrace", "#/skytrace/neu", "#/skytrace/statistik", "#/skytrace/erfolge", "#/sage", "#/sage/wiki", "#/sage/entdeckungen",
          "#/settings/stacking", "#/settings/skytrace", "#/settings/sage", "#/settings/module"]


@pytest.mark.parametrize("size", list(SIZES))
def test_nothing_overflows(server, browser, size):
    base, pid = server
    w, h = SIZES[size]
    page = browser.new_page(viewport={"width": w, "height": h})
    errors, cur = [], [""]
    page.on("pageerror", lambda e: errors.append(f"{cur[0]}: {e.stack or e}"))
    problems = {}
    for route in ROUTES:
        cur[0] = route
        page.goto(f"{base}/{route.format(pid=pid)}")
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(600)                           # Einblend-Animationen, nachgeladene Abschnitte
        found = page.evaluate(FIND_OVERFLOW)
        if found:
            problems[route] = found
    page.close()
    assert not errors, errors
    assert not problems, "\n".join(f"{r}:\n  " + "\n  ".join(p) for r, p in problems.items())


def test_fast_navigation(server, browser):
    """Schnell weiterklicken, bevor eine Ansicht fertig geladen ist: keine JS-Fehler, die zuletzt gewählte Ansicht bleibt stehen.
    (Verworfene Ansichten schrieben früher nach dem Laden über die aktuelle bzw. griffen auf schon entfernte Elemente zu.)"""
    base, pid = server
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    errors, cur = [], [""]
    page.on("pageerror", lambda e: errors.append(f"{cur[0]}: {e.stack or e}"))
    page.goto(f"{base}/#/")
    for wait in (0, 60, 200):
        for route in ROUTES:
            cur[0] = route
            page.goto(f"{base}/{route.format(pid=pid)}")
            page.wait_for_timeout(wait)
    page.goto(f"{base}/#/settings/stacking")
    page.wait_for_selector("#hkForm")
    page.wait_for_timeout(1500)                              # späte Antworten verworfener Ansichten abwarten
    assert page.locator("#hkForm").count() == 1 and page.locator("#view > div").count() == 1
    page.close()
    assert not errors, "\n".join(sorted(set(errors))[:10])


@pytest.mark.parametrize('size', ['handy', 'desktop'])
def test_live_processing_is_compact_and_updates_in_place(server, browser, size, tmp_path):
    """Echte Oberfläche mit kontrollierten Job-Antworten: Vorschauwechsel, Details und Abschluss."""
    base, pid = server
    w, h = SIZES[size]
    page = browser.new_page(viewport={'width': w, 'height': h}, reduced_motion='reduce')
    errors, state = [], {'step': 0, 'done': False, 'rid': None}
    page.on('pageerror', lambda e: errors.append(str(e)))

    def project_response(route):
        response = route.fetch()
        data = response.json()
        state['rid'] = data['results'][0]['id']
        label = 'Sterne trennen (StarNet)' if state['step'] else 'Entrauschen (GraXpert-KI)'
        data['job'] = {'id': 999, 'state': 'done' if state['done'] else 'running', 'progress': .91,
                       'stage': 'Bearbeitung', 'message': label}
        data['live'] = {'job': 999, 'preview': f"1-{state['step'] + 1}.jpg", 'n': 12, 'exposure': 120,
                        'steps': [{'stage': 'Bearbeitung', 'label': label, 'done': False}]}
        route.fulfill(response=response, json=data)

    def preview_response(route):
        route.fulfill(response=route.fetch(url=f"{base}/api/stacking/results/{state['rid']}/jpg"))

    page.route(f'**/api/stacking/projects/{pid}', project_response)
    page.route(f'**/api/stacking/projects/{pid}/live/*', preview_response)
    page.goto(f'{base}/#/p/{pid}/bild')
    page.wait_for_selector('.sl-image.on')
    assert page.locator('#job').is_hidden()                   # kein doppelter Fortschrittsbereich
    assert page.locator('.sl-pop').is_hidden()
    assert page.locator('.sl-status').bounding_box()['height'] <= 50
    assert abs(page.locator('.sl-picture').bounding_box()['width'] - page.locator('.sl-status').bounding_box()['width']) <= 1
    page.screenshot(path=str(tmp_path / 'live.png'))
    assert page.evaluate("getComputedStyle(document.querySelector('.sl-pulse')).animationName") == 'none'
    page.locator('.sl-details summary').click()
    page.locator('.stack-live').evaluate("el => el.dataset.preserved = 'yes'")
    state['step'] = 1
    page.wait_for_function("document.querySelector('.sl-label')?.textContent.includes('StarNet')")
    assert page.locator('.stack-live').get_attribute('data-preserved') == 'yes'
    assert page.locator('.sl-details').get_attribute('open') is not None
    page.wait_for_selector('.sl-image.on[src$="1-2.jpg"]')
    assert not page.evaluate(FIND_OVERFLOW)
    state['done'] = True
    page.wait_for_selector('#viewer .viewer img.b')
    assert not page.locator('.stack-live').count()
    page.close()
    assert not errors, errors


def test_switch_rows_toggle(server, browser):
    """Ein Klick auf die Zeile schaltet ihren Schalter – auch wenn die Zeile noch eine Auswahl enthält (Ältere Versionen)."""
    base, _ = server
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    page.goto(f"{base}/#/settings/stacking")
    page.wait_for_selector("#hkForm")
    rows = page.locator("#hkForm label.modrow")
    assert rows.count() > 0
    for i in range(rows.count()):
        row = rows.nth(i)
        box = row.locator("input[type=checkbox]")
        before = box.is_checked()
        row.locator("b").first.click()
        assert box.is_checked() != before, row.inner_text()
    page.close()


@pytest.mark.parametrize('size', ['handy', 'desktop'])
def test_stage_slider_preserves_size_zoom_and_pan(server, browser, size):
    """Kleine Stufen und großes Endbild bleiben beim Einpassen und Zoomen gleich groß sichtbar."""
    import io
    import re
    from PIL import Image
    base, pid = server
    w, h = SIZES[size]
    page = browser.new_page(viewport={'width': w, 'height': h}, reduced_motion='reduce')
    errors = []
    page.on('pageerror', lambda e: errors.append(str(e)))

    def png(width, height):
        out = io.BytesIO()
        Image.new('RGB', (width, height), (40, 50, 70)).save(out, format='PNG')
        return out.getvalue()

    large, small = png(3200, 2000), png(800, 500)
    page.route(re.compile(r'/api/stacking/results/\d+/jpg$'),
               lambda route: route.fulfill(body=large, content_type='image/png'))
    page.route(re.compile(r'/api/stacking/results/\d+/s\d+$'),
               lambda route: route.fulfill(body=small, content_type='image/png'))
    page.goto(f'{base}/#/p/{pid}/bild')
    page.wait_for_selector('#viewer .viewer.ready')
    page.wait_for_function("document.querySelector('#viewer img.b').naturalWidth === 3200")
    max_stage = int(page.locator('#strange').get_attribute('max'))
    assert max_stage >= 1
    box = """() => { const i = document.querySelector('#viewer img.b').getBoundingClientRect();
        const v = document.querySelector('#viewer .viewer').getBoundingClientRect();
        return [i.x-v.x,i.y-v.y,i.width,i.height]; }"""
    for zoomed in (False, True):
        if zoomed:
            page.locator('#viewer [data-a=in]').click()
            page.locator('#viewer [data-a=in]').click()
            page.wait_for_timeout(450)
            view_box = page.locator('#viewer .viewer').bounding_box()
            x, y = view_box['x'] + view_box['width'] / 2, view_box['y'] + view_box['height'] / 2
            page.mouse.move(x, y)
            page.mouse.down()
            page.mouse.move(x - 35, y - 20)
            page.mouse.up()
        expected = page.evaluate(box)
        for index in (0, max_stage, 0, max_stage):
            page.locator('#strange').evaluate('(el, i) => { el.value = i; el.dispatchEvent(new Event("input", {bubbles:true})); }', index)
            width = 3200 if index == max_stage else 800
            page.wait_for_function("w => document.querySelector('#viewer img.b').naturalWidth === w", arg=width)
            # naturalWidth steht bereits vor dem load-Handler fest; dessen neue Transformation abwarten.
            page.wait_for_function("""expected => {
                const i = document.querySelector('#viewer img.b').getBoundingClientRect();
                const v = document.querySelector('#viewer .viewer').getBoundingClientRect();
                return [i.x-v.x,i.y-v.y,i.width,i.height].every((n,j) => Math.abs(n-expected[j]) < 1);
            }""", arg=expected, timeout=5000)
            actual = page.evaluate(box)
            assert all(abs(a-b) < 1 for a, b in zip(actual, expected)), (zoomed, index, actual, expected)
    page.close()
    assert not errors, errors


@pytest.mark.parametrize("size", ["handy", "desktop"])
def test_folder_dialog(server, browser, size):
    """Hauptordner wählen: Dialog zeigt Laufwerke, lässt sich in Ordner und zurück navigieren, passt aufs Handy."""
    base, _ = server
    w, h = SIZES[size]
    page = browser.new_page(viewport={"width": w, "height": h})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(f"{base}/#/import")
    page.click("#libPick")
    page.wait_for_selector("#dlg[open] .frow")
    assert page.locator("#fdr .chip").count() >= 1
    page.locator("#fl .frow").first.click()                 # Laufwerk öffnen
    page.wait_for_function("document.querySelector('#fcr button') !== null")
    page.click("#fup")                                      # wieder hoch
    page.wait_for_timeout(300)
    assert page.evaluate("(() => { const d = document.querySelector('#dlg'); return d.scrollWidth <= d.clientWidth + 1 })()")
    page.click("#fx")
    assert not page.locator("#dlg[open]").count()
    page.close()
    assert not errors, errors
