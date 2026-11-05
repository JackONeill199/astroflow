"""Bildbetrachter: hängende bzw. fehlgeschlagene Bilder zeigen einen Hinweis mit „Erneut versuchen“ (Handy im Heimnetz)."""
import io

from PIL import Image


def test_viewer_hinweis_und_erneut_versuchen(server, browser, tmp_path):
    base, _ = server
    page = browser.new_page(viewport={'width': 375, 'height': 800}, has_touch=True, reduced_motion='reduce')
    errors = []; page.on('pageerror', lambda e: errors.append(str(e)))
    out = io.BytesIO(); Image.new('RGB', (64, 48), (30, 40, 70)).save(out, format='JPEG'); jpg = out.getvalue()
    calls = []
    def image(route):
        calls.append(route.request.url)
        if 'neu=' in route.request.url: route.fulfill(body=jpg, content_type='image/jpeg')
        # erster Versuch: keine Antwort – wie eine hängende Verbindung
    page.route('**/haengt.jpg*', image)
    page.route('**/kaputt.jpg', lambda route: route.abort('blockedbyclient'))
    page.goto(base + '/#/')
    page.wait_for_function('typeof createViewer === "function"')
    page.evaluate("""() => { for (const [id, src] of [['vh', '/haengt.jpg'], ['vk', '/kaputt.jpg']]) {
        const d = document.createElement('div'); d.id = id; d.style.cssText = 'width:340px;height:240px;position:relative';
        document.body.prepend(d); createViewer(d, src, null, undefined, null, { slowAfter: 300 }); } }""")
    page.wait_for_selector('#vh .vload [data-retry]')
    assert 'Lädt noch' in page.locator('#vh .vload').inner_text()
    page.wait_for_selector('#vk .vload [data-retry]')
    assert 'konnte nicht geladen werden' in page.locator('#vk .vload').inner_text()
    page.screenshot(path=str(tmp_path / 'viewer-langsam-375.png'))
    page.locator('#vh [data-retry]').tap()
    page.wait_for_selector('#vh .viewer.ready')
    assert any('neu=' in u for u in calls) and not errors
