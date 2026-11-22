"""„AstroFlow unterstützen“ (core/support.py): Anzeige-Logik, Endpunkte, Links, Herkunftsschutz."""
import time

import pytest
from fastapi.testclient import TestClient

from astroflow.core import support
from astroflow.core.config import load_settings
from astroflow.server import create_app


@pytest.fixture
def client(tmp_path):
    app = create_app(load_settings(tmp_path / "data"), only=())
    n = {"v": 0}
    app.state.ctx.services["finished_pictures"] = lambda: n["v"]
    c = TestClient(app)
    c.n = n
    return c


def test_due_logic():
    assert not support.due({}, 0)
    assert not support.due({}, 2)                      # vor dem 3. Bild nein
    assert support.due({}, 3)                          # danach ja
    assert not support.due({"never": True}, 10)        # „Nicht mehr zeigen“
    now = time.time()
    assert not support.due({"snoozed_until": now + 100}, 10, now)   # „Später“ läuft noch
    assert support.due({"snoozed_until": now - 1}, 10, now)         # abgelaufen


def test_no_links_no_dialog(monkeypatch):
    monkeypatch.setattr(support, "LINKS", [("kofi", "Ko-fi", "")])
    assert support.links() == [] and not support.due({}, 10)
    monkeypatch.setattr(support, "LINKS", [("github", "GitHub Sponsors", "https://github.com/sponsors/x"), ("kofi", "Ko-fi", "")])
    assert [l["id"] for l in support.links()] == ["github"]    # leerer Link blendet nur seinen Knopf aus


def test_links_are_https_and_in_app_info(client):
    ls = client.get("/api/app").json()["support"]
    assert {l["id"] for l in ls} == {"github", "kofi"}
    assert all(l["url"].startswith("https://") for l in ls)


def test_endpoints_flow(client):
    s = client.get("/api/support").json()
    assert s["due"] is False and s["pictures"] == 0
    client.n['v'] = 3
    assert client.get("/api/support").json()["due"] is True
    assert client.post("/api/support", json={"action": "shown"}).status_code == 200
    assert client.get("/api/support").json()["shown_count"] == 1
    client.post("/api/support", json={"action": "later"})
    assert client.get("/api/support").json()["due"] is False       # 30 Tage Ruhe
    st = client.app.state.ctx.store.get("support")
    assert 29 * 86400 < st["snoozed_until"] - time.time() <= 30 * 86400
    client.app.state.ctx.store.update("support", {"snoozed_until": None})
    assert client.get("/api/support").json()["due"] is True
    client.post("/api/support", json={"action": "never"})
    assert client.get("/api/support").json()["due"] is False
    assert client.post("/api/support", json={"action": "x"}).status_code == 400


def test_post_needs_same_origin(client):
    r = client.post("/api/support", json={"action": "never"}, headers={"Origin": "http://evil.example"})
    assert r.status_code == 403
    assert client.get("/api/support").json()["due"] is False
    assert not client.app.state.ctx.store.get("support").get("never")


@pytest.mark.parametrize("size", ["desktop", "handy"])
def test_support_dialog_ui(server, browser, size):
    """Dialog: erscheint bei „fällig“ nur einmal je Sitzung, nie in der Einrichtung; Esc = später; Links sicher; Hilfe und Palette öffnen ihn
    ohne Ausblend-Knöpfe; passt aufs Handy."""
    from test_ui_layout import FIND_OVERFLOW, SIZES
    base, _ = server
    w, h = SIZES[size]
    page = browser.new_page(viewport={"width": w, "height": h})
    errors, posts = [], []
    page.on("pageerror", lambda e: errors.append(str(e)))

    def mock(route):
        req = route.request
        if req.method == "POST":
            posts.append(req.post_data_json["action"])
            return route.fulfill(json={"ok": True})
        route.fulfill(json={"due": True, "pictures": 5, "shown_count": 0, "links": []})
    page.route("**/api/support", mock)

    page.goto(f"{base}/#/willkommen")
    page.wait_for_load_state("networkidle")
    page.evaluate("window.support.check()")
    page.wait_for_timeout(500)
    assert not page.locator("#supportDlg").count()                       # nie in der Einrichtung

    page.goto(f"{base}/#/")
    page.wait_for_load_state("networkidle")
    page.evaluate("window.support.check()")
    page.wait_for_selector("#supportDlg[open]")
    txt = page.inner_text("#supportDlg")
    assert "kostenlos" in txt and "sammelt keine Daten" in txt and "Vielleicht später" in txt and "Nicht mehr zeigen" in txt
    links = page.locator("#supportDlg a.sp-btn")
    assert links.count() == 2
    for i in range(2):
        a = links.nth(i)
        assert a.get_attribute("href").startswith("https://") and a.get_attribute("target") == "_blank"
        assert a.get_attribute("rel") == "noopener noreferrer"
    assert not page.evaluate(FIND_OVERFLOW)
    assert page.evaluate("(() => { const d = document.querySelector('#supportDlg'); return d.scrollWidth <= d.clientWidth + 1 })()")
    page.keyboard.press("Escape")                                        # Esc = später
    page.wait_for_function("!document.querySelector('#supportDlg')")
    assert posts == ["shown", "later"], posts
    page.evaluate("window.support.check()")
    page.wait_for_timeout(300)
    assert not page.locator("#supportDlg").count()                       # höchstens einmal je Sitzung

    page.goto(f"{base}/#/hilfe")
    page.click("[data-support]")
    page.wait_for_selector("#supportDlg[open]")
    assert "Nicht mehr zeigen" not in page.inner_text("#supportDlg")
    page.keyboard.press("Escape")
    page.wait_for_function("!document.querySelector('#supportDlg')")
    assert posts == ["shown", "later"]                                   # von Hand geöffnet verändert nichts

    page.keyboard.press("Control+k")
    page.fill("#pal input", "Spenden")
    page.wait_for_selector("#pal li.it")
    assert "AstroFlow unterstützen" in page.inner_text("#pal")
    page.keyboard.press("Enter")
    page.wait_for_selector("#supportDlg[open]")
    page.close()
    assert not errors, errors
