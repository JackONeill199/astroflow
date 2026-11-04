"""Sternschnuppen-Modell (static/meteors.js): Raten nach echtem Himmel – Ströme, Radiant, Tag/Nacht, Apex."""
from pathlib import Path

import pytest

JS = Path(__file__).resolve().parent.parent / "astroflow" / "static" / "meteors.js"


@pytest.fixture(scope="module")
def page(browser):
    pg = browser.new_page()
    pg.set_content("<html><body></body></html>")
    pg.evaluate("window.store = { get: (k, d) => d }")
    pg.add_script_tag(path=str(JS))
    yield pg
    pg.close()


def sky(page, iso, lat=49.0, lon=8.4):
    return page.evaluate("""([iso, lat, lon]) => { const k = meteors.sky(new Date(iso), lat, lon);
        return { total: k.total, sunAlt: k.sunAlt, moon: k.moon, src: Object.fromEntries(k.sources.map(s => [s.name, { hr: s.hr, zhr: s.zhrNow ?? null, alt: s.alt ?? null }])) }; }""",
                         [iso, lat, lon])


def test_geminiden_im_maximum(page):
    k = sky(page, "2026-12-14T01:00:00Z")
    gem = k["src"]["Geminiden"]
    assert gem["zhr"] > 100 and gem["alt"] > 50
    assert gem["hr"] > 40 and k["total"] > 45


def test_perseiden_und_neumond(page):
    k = sky(page, "2026-08-13T01:00:00Z")          # Sonnenfinsternis 12.08.2026 = Neumond
    assert k["moon"] < 0.05
    assert k["src"]["Perseiden"]["hr"] > 30


def test_tagsueber_keine(page):
    k = sky(page, "2026-12-14T12:00:00Z")
    assert k["sunAlt"] > 0 and k["total"] == 0


def test_ruhige_nacht_nur_sporadisch_und_morgens_mehr(page):
    dawn, dusk = sky(page, "2026-03-15T03:30:00Z"), sky(page, "2026-03-15T20:30:00Z")
    assert all(v["zhr"] is None or v["zhr"] < 5 for n, v in dawn["src"].items() if n != "Sporadisch")
    assert 1 < dawn["src"]["Sporadisch"]["hr"] < 15
    assert dawn["src"]["Sporadisch"]["hr"] > 1.5 * dusk["src"]["Sporadisch"]["hr"]


def test_radiant_unter_dem_horizont(page):
    # Quadrantiden (Dec +49°) sind von Südaustralien aus nie über dem Horizont
    k = sky(page, "2026-01-03T18:00:00Z", lat=-35.0, lon=138.6)
    assert k["src"]["Quadrantiden"]["zhr"] > 20 and k["src"]["Quadrantiden"]["hr"] == 0
