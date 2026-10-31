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


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    root = tmp_path_factory.mktemp("ui")
    with pytest.MonkeyPatch.context() as mp:
        for k, v in {"ASTROFLOW_MODULES": ",".join(AVAILABLE), "ASTROFLOW_AUTO": "0", "ASTROFLOW_GAIA": "0", "ASTROFLOW_SAGE_OFFLINE": "1"}.items():
            mp.setenv(k, v)
        mp.delenv("SKYTRACE_HOME", raising=False)          # SkyTrace setzt sie beim Laden prozessweit – danach wieder entfernen
        s = load_settings(root / "data")
        SettingsStore(s.data_dir / "settings.json").update("skytrace", {"home": str(root / "skytrace")})
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
            pytest.skip(f"Chromium fehlt: {e}")
        yield b
        b.close()


ROUTES = ["#/", "#/bilder", "#/p/{pid}/bild", "#/p/{pid}/aufnahmen", "#/p/{pid}/versionen", "#/p/{pid}/optionen",
          "#/import", "#/activity", "#/skytrace", "#/skytrace/neu", "#/sage", "#/sage/wiki", "#/sage/entdeckungen",
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
