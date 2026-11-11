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
  for (const e of document.querySelectorAll('#view *, .side *, .top *, #ctx *')) {
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


def _sky_seed(db, pid):
    """Himmelsqualität für die Oberflächentests: zwei Nächte am erfundenen Ort „a“, eine davon mit Mond."""
    import datetime as dt
    import json as _json
    import numpy as np
    from astroflow.modules.stacking import skyquality as sq
    site = {"id": "a", "name": LONG, "lat": 47.5, "lon": 10.25, "alt_m": 400}
    sq.ensure_table(db)
    for night, t0 in (("2026-01-20", dt.datetime(2026, 1, 20, 21, 0, tzinfo=dt.timezone.utc)),
                      ("2026-01-03", dt.datetime(2026, 1, 3, 21, 0, tzinfo=dt.timezone.utc))):
        fr = [{"id": f"{night}{i}", "night": night, "date": (t0 + dt.timedelta(minutes=4 * i)).isoformat(), "exptime": 10.0,
               "gain": 200.0, "metrics": {"bg": 960 + i, "sky": 921 + i * 0.5, "noise": 58 + (i % 7) * 0.4, "fwhm": 6 + (i % 5) * 0.3, "n_stars": 100,
                                          "calib": "keine Kalibrierung", "bgplane": [0.0, 0.0]}} for i in range(60)]
        links = {f["id"]: (1.0, np.eye(3)) for f in fr}
        n = sq.evaluate(fr, zp_stack=21.7, zp_device=None, flux_k0=1.0, links=links, excess=0.0, scale=1.24, site=site,
                        ra=100.0, dec=40.0, device_id="stellina")[night]
        n["dirs"] = [[az, 50.0, 0.001 * np.cos(np.radians(az - 120)), 0.001 * np.sin(np.radians(az - 120)) * 0.5] for az in range(0, 360, 8)]
        db.x("INSERT OR REPLACE INTO sky_nights VALUES(?,?,?,?,?,?,?,?)", (pid, night, "a", LONG, "stellina|200.0", 1.0, sq.SKY_VERSION, _json.dumps(n)))


def _skytrace_run(run):
    """Ein SkyTrace-Lauf mit langen Namen und Katalogergebnissen (für Statistik und Laufliste)."""
    import json
    (run / "obs").mkdir(parents=True)
    sat = {"provider": "satellites", "status": "compatible", "matches": [{"name": "COSMOS 2251 DEB (Trümmerteil mit Langname)", "norad_id": 34427, "object_type": "Trümmerteil"}]}
    cands = [{"id": f"K{i:04d}", "session": f"2022-09-01_observation_{LONG}", "kind": "trail", "timestamp": 1662069600.0 + 3600 * i,
              "world_path": [[10.0, 20.0], [10.2, 20.1]],
              "catalogs": [sat if i % 2 else {"provider": "satellites", "status": "checked_no_match"}, {"provider": "aircraft", "status": "needs_access"}]}
             for i in range(5)]
    (run / "obs" / "kandidaten.json").write_text(json.dumps(cands), encoding="utf-8")
    (run / "videos.json").write_text(json.dumps([{"session": f"2022-09-01_observation_{LONG}", "frames": 1234, "excluded": 56, "video": "obs/video.mp4",
                                                  "state": "Fertig", "candidates_file": "obs/kandidaten.json"}]), encoding="utf-8")
    # letzte Standortsuche (Beobachtungen → „Standort der Nacht prüfen“, 2 von 5 Spuren zugeordnet)
    (run / "obs" / "standortsuche.json").write_text(json.dumps({"ok": True, "worth": True, "before": 0, "after": 4, "tracks": 5, "wide": 5, "radius_km": 10,
        "created": 1762300000, "trails": 4, "timed": 2, "dropped": [], "shift_km": 4.8, "shift_sigma_km": 0.05, "bearing_deg": 160, "north_km": -4.5, "east_km": 1.6,
        "ellipse_km": {"major": 0.06, "minor": 0.03, "angle_deg": 20}, "clock_s": 2.1, "clock_sigma_s": 0.2, "rms_before_arcsec": 1400, "rms_after_arcsec": 9,
        "lat": 10.0, "lon": 20.0, "alt_m": 0, "accuracy_m": 60, "site": {"name": LONG}, "night": "2022-09-01"}), encoding="utf-8")
    # Standorte aller Nächte (#/skytrace/standorte): neuer Ort, Listenort, kein Ort, übernommen
    obs = [{"run": run.name, "folder": "obs", "session": f"2022-09-01_observation_{LONG}"}] * 3
    cur = {"id": "home", "name": LONG, "sure": 0, "rms": None}
    fit = {"kind": "fit", "lat": 10.0, "lon": 20.0, "alt_m": 0, "accuracy_m": 80, "sure": 5, "rms": 14.2, "trails": 5, "timed": 2, "clock_s": -3.1,
           "clock_sigma_s": 0.4, "rms_before_arcsec": 903, "rms_after_arcsec": 14, "shift_from_current_km": 4.8, "from_name": LONG}
    # Rückrechnung 372 m neben einem Listenort (über der Einrastgrenze 300 m): Listenort streng ebenfalls gut → Alternative (0.38.0)
    alt = {"night": "2022-05-17", "observations": obs[:1], "tracks": 3, "before": 0, "status": "better", "current": cur, "assign": ["2022-05-17"],
           "tested": [{"id": "a", "name": LONG, "sure": 0, "compatible": 0, "rms": None}, {"id": "b", "name": "Beispielberg", "sure": 3, "compatible": 3, "rms": 113.3}],
           "proposal": {"kind": "fit", "lat": 45.0 + 0.30 / 111.195, "lon": 6.0 + 0.22 / (111.195 * 0.7071), "alt_m": 0, "accuracy_m": 115, "sure": 3, "rms": 6.1,
                        "trails": 3, "timed": 1, "clock_s": -2.4, "clock_sigma_s": 0.6, "rms_before_arcsec": 900, "snap_limit_m": 300,
                        "ellipse_km": {"major": 0.115, "minor": 0.04, "angle_deg": 30}, "shift_from_current_km": 380.0, "from_name": "Beispielberg"},
           "clock": {"proposal": {"offset_s": -42.0, "sigma_s": 1.0, "support": 3, "sure": 3, "before": 0, "confidence": 0.999, "chance": 0.1, "nights": ["2022-05-17"]}}}
    nights = [alt, {"night": "2022-09-01", "observations": obs, "tracks": 5, "before": 0, "status": "better", "current": cur, "proposal": fit, "assign": ["2022-09-01"]},
              {"night": "2022-08-30", "observations": obs[:1], "tracks": 2, "before": 0, "status": "better", "current": cur,
               "proposal": {"kind": "list", "id": "b", "name": LONG, "sure": 2, "rms": 40}, "note": "Nur 2 eindeutige Spur(en) – zu wenig für eine Rückrechnung, nur Auswahl aus der Ortsliste."},
              {"night": "2022-08-29", "observations": obs[:1], "tracks": 1, "before": 0, "status": "none", "current": cur, "proposal": None,
               "note": "Kein Ort passt: keine Spur ließ sich einer Satellitenbahn eindeutig zuordnen (auch nicht mit ± 10 km Spielraum)."},
              {"night": "2022-08-28", "observations": obs[:1], "tracks": 3, "before": 3, "status": "better", "current": cur, "proposal": fit, "applied": 1762300000},
              # Uhrversatz (clock_scan): Kamera-Uhr 10 min falsch, Ort passt
              {"night": "2022-08-03", "observations": obs, "tracks": 12, "before": 1, "status": "better", "current": cur, "proposal": None,
               "note": "Der bisherige Ort passt – mit korrigierter Uhr.",
               "clock": {"range_s": 1200, "tracks": 12, "support": 7, "chance": 0.4, "confidence": 0.9999, "offset_s": -642.5,
                         "proposal": {"offset_s": -642.5, "sigma_s": 1.5, "support": 7, "sure": 8, "before": 1, "confidence": 0.9999, "chance": 0.4,
                                      "nights": ["2022-08-03"], "base_s": 0}}},
              {"night": "2022-08-02", "observations": obs[:1], "tracks": 4, "before": 0, "status": "none", "current": cur, "proposal": None,
               "note": "Kein Ort passt.", "clock": {"support": 1, "reason": "Höchstens 1 Spur(en) bei demselben Versatz – zu wenig (nötig 3)."}},
              # Ort ohne Vorwissen (global_site): weit weg von jedem Listenort (erfundene Koordinaten), Uhrversatz vom falschen Ort verworfen
              {"night": "2022-08-01", "observations": obs, "tracks": 16, "before": 0, "status": "better", "current": cur,
               "proposal": {"kind": "global", "lat": 46.234567, "lon": 13.912345, "alt_m": 0, "accuracy_m": 105, "sure": 12, "rms": 24.9, "trails": 12,
                            "timed": 2, "clock_s": -3.8, "clock_sigma_s": 0.7, "support": 12, "confidence": 1.0, "region_km": 1500,
                            "from_name": "Unbekannter Ort", "nearest_name": LONG, "nearest_km": 301.6, "shift_from_current_km": 301.6},
               "note": "Kein Listenort passte – der Ort wurde ohne Vorwissen aus 12 Spuren zurückgerechnet, 302 km vom nächsten Listenort.",
               "clock": {"source": "global", "proposal": None, "reason": "Am zurückgerechneten Ort geht die Uhr richtig (-4 s) – der am bisherigen Ort gefundene Versatz -639 s war eine Folge des falschen Orts."}}]
    cfg = run.parent.parent / "catalog_settings.json"                  # übernommene Uhrkorrektur einer Nacht
    data = json.loads(cfg.read_text(encoding="utf-8")) if cfg.is_file() else {}
    cfg.write_text(json.dumps(dict(data, clock_offsets={"2022-08-30": {"offset_s": -615.0, "sigma_s": 2.0}})), encoding="utf-8")
    (run.parent.parent / "standortsuche-naechte.json").write_text(json.dumps({"created": 1762300000, "radius_km": 10, "state": "done", "nights": nights}), encoding="utf-8")
    # Heatmap der Rückprojektion (engine/global_site.heat_map, synthetisch: Häufung am erfundenen Vorschlag) – Karte je Nacht
    karten = run.parent.parent / "standortsuche-karten"
    karten.mkdir(exist_ok=True)
    (karten / "2022-08-01.json").write_text(json.dumps(_heat_fixture(46.234567, 13.912345)), encoding="utf-8")


def _heat_fixture(lat, lon):
    """Synthetische Heatmap im Format von `standortsuche-karten/<Nacht>.json`: grob 40 × 40 Zellen à 20 km, fein 61 × 61 km."""
    import base64
    import math
    coarse = bytes(min(255, round(12 * math.exp(-((c - 20 + .5) ** 2 + (r - 20 + .5) ** 2) * 400 / (2 * 80 ** 2)) + (c * 7 + r * 3) % 3 * (c % 4 == 0)))
                   for r in range(40) for c in range(40))
    fine = bytes(min(255, round(12 * math.exp(-((c - 30) ** 2 + (r - 30) ** 2) / (2 * 3 ** 2)))) for r in range(61) for c in range(61))
    return {"night": "2022-08-01", "created": 1762300000, "computed": False, "clock_s": 60.0, "region_km": 1500,
            "coarse": {"lat0": lat, "lon0": lon, "cell_km": 20.0, "off": 60, "x0": 40, "y0": 40, "w": 40, "h": 40, "max": 12,
                       "data": base64.b64encode(coarse).decode()},
            "fine": {"lat": lat, "lon": lon, "km": 1.0, "half_km": 30.0, "size": 61, "data": base64.b64encode(fine).decode()},
            "result": {"tracks": 16, "seconds": 18.2, "proposal": {"lat": lat, "lon": lon, "support": 12, "sure": 12}, "clusters": []}}


# Fixtures `server` und `browser` (je Worker einmal) stehen in conftest.py


ROUTES = ["#/", "#/nacht", "#/bilder", "#/analyse", "#/galerie", "#/galerie/{pid}", "#/p/{pid}/bild", "#/p/{pid}/analyse", "#/p/{ipid}/analyse", "#/p/{pid}/aufnahmen", "#/p/{pid}/versionen", "#/p/{pid}/optionen",
          "#/import", "#/activity", "#/himmel", "#/skytrace", "#/skytrace/neu", "#/skytrace/statistik", "#/skytrace/2026-10-28_10-00-00-000001/beobachtungen", "#/skytrace/standorte", "#/skytrace/erfolge", "#/skytrace/3d", "#/skytrace/erde", "#/skytrace/erde/2022-08-01", "#/sage", "#/sage/wiki", "#/sage/entdeckungen", "#/sage/sammlung", "#/sage/netz",
          "#/settings/stacking", "#/settings/skytrace", "#/settings/sage", "#/settings/standorte", "#/settings/heimnetz", "#/settings/module", "#/willkommen"]


def _imported_pid(base: str) -> int:
    """Das im Server-Fixture importierte, gelöste fertige Bild (Reiter „Analyse“ für fertige Bilder)."""
    import httpx
    return next(p["id"] for p in httpx.get(f"{base}/api/stacking/projects").json() if p.get("imported"))


@pytest.mark.parametrize("size", list(SIZES))
def test_nothing_overflows(server, browser, size):
    base, pid = server
    ipid = _imported_pid(base)
    w, h = SIZES[size]
    page = browser.new_page(viewport={"width": w, "height": h})
    errors, cur = [], [""]
    page.on("pageerror", lambda e: errors.append(f"{cur[0]}: {e.stack or e}"))
    problems = {}
    for route in ROUTES:
        cur[0] = route
        page.goto(f"{base}/{route.format(pid=pid, ipid=ipid)}")
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
    ipid = _imported_pid(base)
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    errors, cur = [], [""]
    page.on("pageerror", lambda e: errors.append(f"{cur[0]}: {e.stack or e}"))
    page.goto(f"{base}/#/")
    for wait in (0, 60, 200):
        for route in ROUTES:
            cur[0] = route
            page.goto(f"{base}/{route.format(pid=pid, ipid=ipid)}")
            page.wait_for_timeout(wait)
    page.goto(f"{base}/#/settings/stacking")
    page.wait_for_selector("#hkForm")
    page.wait_for_timeout(1500)                              # späte Antworten verworfener Ansichten abwarten
    assert page.locator("#hkForm").count() == 1 and page.locator("#view > div").count() == 1
    page.close()
    assert not errors, "\n".join(sorted(set(errors))[:10])


def test_standort_alternative(server, browser):
    """Zurückgerechneter Punkt 372 m neben einem Listenort (Grenze 300 m): Alternative mit zwei Knöpfen, verständlicher Grund, Karte mit Verbindung;
    „hierher verschieben“ nennt Zeiträume, in denen der Ort ebenfalls gilt (Abbrechen ändert nichts)."""
    base, _ = server
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(f"{base}/#/skytrace/standorte")
    row = page.locator('.stn-night[data-night="2022-05-17"]')
    row.locator(".stn-alt").wait_for()
    text = row.inner_text()
    assert "Beispielberg" in text and "372 m entfernt · 3/3 eindeutig · quer 113″" in text
    assert "liegt 372 m neben „Beispielberg“ (Grenze für automatische Zuordnung 300 m)" in text
    assert row.locator("[data-alt-apply=b]").inner_text().startswith("Beispielberg für diese Nacht übernehmen")
    row.locator("[data-alt-move=b]").click()
    dlg = page.locator("#dlg")
    dlg.get_by_text("hierher verschieben?").wait_for()
    assert "2 Zeiträume" in dlg.inner_text() and "wirkt die Verschiebung mit" in dlg.inner_text()
    assert "Uhrkorrektur −42 s" in dlg.inner_text()                     # Uhr gehört zum zurückgerechneten Ort (Review PR #62)
    page.keyboard.press("Escape")
    row.locator('[data-map-open="2022-05-17"]').click()
    row.locator("svg.stn-map line.alt").wait_for()
    assert row.locator("svg.stn-map text.alt").text_content() == "372 m"
    assert not errors, errors
    page.close()


def test_saubere_naechte_himmel(server, browser):
    """#/himmel: Abschnitt „Saubere Nächte“ (Seed: zwei Nächte am erfundenen Ort) – Kennzahlen, Reiter, Heatmap, Verlinkung."""
    base, _ = server
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    page.goto(f"{base}/#/himmel")
    page.wait_for_selector(".sq-clean .sq-top tbody tr")
    assert "Bester Ort" in page.inner_text(".sq-clean") and page.locator(".sq-heat td:not(.empty)").count() >= 1
    n = page.locator(".sq-top tbody tr").count()
    assert n >= 1 and page.locator(".sq-top tbody tr a[href^='#/p/']").count() >= 1
    page.locator("[data-ctab=seeing]").click()
    page.wait_for_function("document.querySelector('.sq-tabs a.on').dataset.ctab === 'seeing'")
    assert "Schärfstes Seeing" in page.inner_text(".sq-top thead")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.close()


def test_night_map_heatmap(server, browser):
    """Standorte aller Nächte: Karte je Nacht mit Heatmap (synthetisch), Umrissen, Nah-Ansicht; ohne Heatmap Knopf „berechnen“ – ohne Kartendienst."""
    base, _ = server
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    tiles = []
    page.on("request", lambda r: tiles.append(r.url) if "tile." in r.url else None)
    page.goto(f"{base}/#/skytrace/standorte")
    page.wait_for_selector(".stn-open [data-apply-all]")                    # offene Vorschläge im Kopf
    assert "noch nicht übernommen" in page.inner_text(".stn-open")
    box = page.locator('.stn-mapbox[data-map-night="2022-08-01"]')         # unbekannter Ort: Karte gleich offen
    box.locator("svg.stn-map image.heat").wait_for()
    page.wait_for_function("document.querySelector('.stn-mapbox[data-map-night=\"2022-08-01\"] .land path')")
    assert "Spuren je 20-km-Zelle" in box.inner_text()
    box.locator("[data-map-mode=near]").click()
    box.get_by_text("verschiedene Spuren je km²").wait_for()
    assert box.locator("svg.stn-map image.heat").count() == 1
    page.locator("[data-show=all]").click()
    page.locator('[data-map-open="2022-08-03"]').click()                 # Nacht ohne Heatmap
    other = page.locator('.stn-mapbox[data-map-night="2022-08-03"]')
    other.locator("[data-heat]").wait_for()
    assert "Heatmap berechnen" in other.inner_text() and other.locator("image.heat").count() == 0
    assert not tiles                                                       # Kacheln nur auf Knopfdruck
    page.close()


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


SPINNING = """() => [...document.querySelectorAll('*')].filter(el => /(^|, )spin(,|$)/.test(getComputedStyle(el).animationName)
  && el.checkVisibility({opacityProperty: true, visibilityProperty: true})).map(el => el.className.baseVal ?? el.className)"""


def test_spinners_on_running_object_page(server, browser):
    """Objektseite während eines Auftrags: nur Auftragsanzeige oben und Statuszeile drehen sich, im selben Tempo
    (Nutzerbefund: vier Kreisel, alle verschieden schnell – Bildmitte und „Läuft …“ waren zu viel)."""
    base, pid = server
    page = browser.new_page(viewport={'width': 1440, 'height': 900})
    errors = []
    page.on('pageerror', lambda e: errors.append(str(e)))
    job = {'id': 999, 'state': 'running', 'progress': .1, 'stage': 'Analyse', 'message': 'Analyse'}

    def project_response(route):
        response = route.fetch()
        data = response.json()
        data['job'], data['live'] = job, {'job': 999, 'steps': [{'stage': 'Analyse', 'label': 'Analyse', 'done': False}]}
        route.fulfill(response=response, json=data)

    def status_response(route):
        response = route.fetch()
        route.fulfill(response=response, json={**response.json(), 'busy': 1})

    activity = [{'key': 'stacking:999', 'module': 'stacking', 'title': 'Testobjekt', 'kind': 'Verarbeitung', 'detail': '',
                 'state': 'running', 'progress': .1, 'stage': 'Analyse', 'started': 0, 'finished': None, 'href': f'#/p/{pid}'}]
    page.route(f'**/api/stacking/projects/{pid}', project_response)
    page.route('**/api/status', status_response)
    page.route('**/api/activity*', lambda route: route.fulfill(json=activity))
    page.goto(f'{base}/#/p/{pid}/bild')
    page.wait_for_selector('.sl-pulse')
    page.wait_for_function("!document.querySelector('#jobpill').classList.contains('idle')")
    page.wait_for_selector('#jobpill .spinner')
    assert sorted(page.evaluate(SPINNING)) == ['sl-pulse', 'spinner']
    assert page.locator('#jobpill .spinner').is_visible() and page.locator('#run').is_disabled()
    # gemeinsames Tempo: alle Kreisel über dasselbe Token
    for sel in ('.sl-pulse', '#jobpill .spinner'):
        assert page.evaluate(f"getComputedStyle(document.querySelector('{sel}')).animationDuration") == '0.9s'
    page.close()
    assert not errors, errors


@pytest.mark.parametrize('size', ['handy', 'desktop'])
def test_live_stack_emerges_from_noise(server, browser, size):
    """Beim Stacken: neue echte Zwischenstände werden nacheinander überblendet, mit Aufnahmenzahl und Rauschmaß;
    die Endbearbeitung (preview) hat danach Vorrang. Reduzierte Bewegung: harte Wechsel."""
    from playwright.sync_api import Error as PlaywrightError
    base, pid = server
    w, h = SIZES[size]
    state = {'k': 4, 'rid': None, 'preview': None, 'ready': False}
    seq = [1, 2, 3, 4, 5, 6, 8, 10, 12, 15]

    def project_response(route):
        try:
            response = route.fetch()
            data = response.json()
        except PlaywrightError:                                           # Seite schon zu: nichts mehr auszuliefern
            return
        state['rid'] = data['results'][0]['id']
        if state['ready']:                                                # erst wenn die Ansicht steht: je Abruf 3 neue Stände
            state['k'] = min(state['k'] + 3, len(seq))
        frames = [{'name': f'1-f{i + 1}.jpg', 'n': n, 'exposure': n * 10, 'band': None, 'noise': round(n ** -0.5, 3)}
                  for i, n in enumerate(seq[:state['k']])]
        data['job'] = {'id': 997, 'state': 'running', 'progress': .4, 'stage': 'Stacking', 'message': 'Stacking'}
        data['live'] = {'job': 997, 'run': '1', 'frames': frames, 'preview': state['preview'], 'n': frames[-1]['n'],
                        'steps': [{'stage': 'Stacking', 'label': 'Stacking', 'done': False}]}
        try:
            route.fulfill(response=response, json=data)
        except PlaywrightError:
            pass

    def live_file(route):
        try:
            route.fulfill(response=route.fetch(url=f"{base}/api/stacking/results/{state['rid']}/jpg"))
        except PlaywrightError:
            pass

    for reduced in ('no-preference', 'reduce'):
        state.update(k=4, preview=None, ready=False)
        context = browser.new_context(viewport={'width': w, 'height': h}, reduced_motion=reduced)    # je Durchlauf eigener Kontext: nichts Verwaistes zieht den nächsten mit
        page = context.new_page()
        errors, shown = [], []
        page.on('pageerror', lambda e: errors.append(str(e)))
        page.on('request', lambda r: shown.append(r.url.rsplit('/', 1)[-1]) if '/live/1-f' in r.url else None)
        page.route(f'**/api/stacking/projects/{pid}', project_response)
        page.route(f'**/api/stacking/projects/{pid}/live/*', live_file)
        page.goto(f'{base}/#/p/{pid}/bild')
        page.wait_for_selector('.sl-image.on[src$="1-f4.jpg"]')        # neu geöffnet: nur der jüngste Stand
        assert '1-f1.jpg' not in shown                                    # nichts nachgeholt
        state['ready'] = True
        page.wait_for_function("document.querySelector('.sl-caption').textContent.includes('Rauschen 50 %')", timeout=20000)   # 4 Aufnahmen: 1/√4
        assert '−' not in page.inner_text('.sl-caption')                  # Anteil des Anfangsrauschens, kein Minus
        page.wait_for_function("document.querySelector('.sl-caption').textContent.includes('15 Aufnahmen')", timeout=20000)
        assert {'1-f5.jpg', '1-f6.jpg', '1-f7.jpg'} <= set(shown)        # mehrere echte Stände je Abruf nacheinander
        dur = page.evaluate("getComputedStyle(document.querySelector('.sl-image')).transitionDuration")
        assert (dur == '0s') == (reduced == 'reduce')
        assert not page.evaluate(FIND_OVERFLOW)
        state['preview'] = '1-9.jpg'                                      # Endbearbeitung: ihre Vorschau gewinnt
        page.wait_for_selector('.sl-image.on[src$="1-9.jpg"]')
        page.unroute_all(behavior='ignoreErrors')                         # keine neuen Weiterleitungen mehr, Fehler laufender Abrufe egal
        context.close()
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
    page.click(".imp-opt[data-m=link]")                     # Einstieg „Vorhandenen Ordner einbinden“
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


@pytest.mark.parametrize("size", ["handy", "desktop"])
def test_folder_dialog_search(server, browser, size, tmp_path):
    """Ordnersuche: sofort im aktuellen Ordner filtern, Treffer in Unterordnern mit relativem Pfad, Klick öffnet, Esc leert."""
    from tests.test_folders import _baum
    base = _baum(tmp_path)
    (base / "NGC4395_Bearbeitung").mkdir()
    url, _ = server
    w, h = SIZES[size]
    page = browser.new_page(viewport={"width": w, "height": h})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(f"{url}/#/import")
    page.click(".imp-opt[data-m=link]")
    page.click("#libPick")
    page.wait_for_selector("#dlg[open] .frow")
    assert page.evaluate("document.activeElement.id") == "fq"             # Fokus im Suchfeld
    page.click(".fmanual summary")
    page.fill("#fp", str(base))
    page.click("#fgo")
    page.wait_for_function("document.querySelectorAll('#fl .frow').length === 4")   # 2022, 2023, a, NGC4395_Bearbeitung
    page.fill("#fq", "NGC 4395")
    # sofort: nur der passende Ordner hier, danach die Treffer tiefer mit relativem Pfad
    assert page.locator("#fl .frow").count() == 1
    page.wait_for_selector("#fl .frel")
    rels = page.locator("#fl .frel").all_inner_texts()
    assert [r.replace("\\", "/") for r in rels] == ["2022/2022-05-06_21-41-48_observation_NGC4395",
                                                    "2023/Fruehling/2023-04-01_20-00-00_observation_NGC_4395"]
    assert "3 Treffer" in page.inner_text("#fct")
    assert page.evaluate("(() => { const d = document.querySelector('#dlg'); return d.scrollWidth <= d.clientWidth + 1 })()")
    page.focus("#fq")
    page.keyboard.press("Escape")                                          # leert nur, Dialog bleibt offen
    assert page.input_value("#fq") == "" and page.locator("#dlg[open]").count()
    assert page.locator("#fl .frow").count() == 4
    page.fill("#fq", "ngc_4395")
    page.locator("#fl .frow", has=page.locator(".frel")).first.click()     # Treffer öffnen
    page.wait_for_function("document.querySelector('#fp').value.endsWith('observation_NGC4395')")
    assert page.input_value("#fq") == ""
    page.wait_for_selector("#fl .frow")                                    # Unterordner 01-images-initial
    page.click("#fok")                                                     # wählt den aktuellen Ordner wie bisher
    assert not page.locator("#dlg[open]").count()
    page.close()
    assert not errors, errors

@pytest.mark.parametrize('size', ['handy', 'schmal', 'desktop'])
def test_tonight_browse_nights(server, browser, size, tmp_path, monkeypatch):
    """Nächte blättern: Pfeile, Nachtleiste und ←/→; Wolkenband, Legende aus Symbolen, kein Überlauf."""
    from pathlib import Path
    from astroflow.modules.stacking import tonight
    from tests.test_tonight import SITE, EVENING, M31
    t0 = tonight._window(EVENING, SITE['lon'])[0]
    hours = [{'t': t0 + 3600 * i, 'cloud': (i * 37) % 100, 'low': (i * 13) % 100, 'mid': (i * 29) % 100, 'high': (i * 7) % 100, 'wind': 5 + i % 30}
             for i in range(3 * 24)]
    monkeypatch.setattr(tonight, '_forecast', lambda *a, **k: {'hours': hours, 'later': [], 'source': 'Test'})
    tonight._cache.clear()
    dev = {'name': 'Testgerät', 'fov': [1, .7], 'source': 'Testmessung'}
    plans = {k: tonight.plan([{**M31, 'exposure': 1800}], {**SITE, 'weather': True}, Path(tmp_path), EVENING, dev, night=k) for k in (0, 1, 2, 9)}
    plans[1]['clouds'] = {**plans[1]['clouds'], 'mid': None, 'high': None}       # Vorhersage nur mit einer Schicht
    asked = []

    def answer(route):
        import urllib.parse as up
        k = int(up.parse_qs(up.urlparse(route.request.url).query).get('night', ['0'])[0])
        asked.append(k)
        route.fulfill(json=plans[k])
    base, _ = server
    w, h = SIZES[size]
    page = browser.new_page(viewport={'width': w, 'height': h}, has_touch=size == 'handy')
    errors = []
    page.on('pageerror', lambda e: errors.append(str(e)))
    page.route('**/api/stacking/tonight*', answer)
    page.goto(base + '/#/nacht')
    page.wait_for_selector('af-tonight-chart svg')
    assert page.locator('.tn-strip [role=tab]').count() == tonight.NIGHTS
    assert page.locator('.tn-nav-name').inner_text().startswith('Heute Nacht')
    assert page.locator('.tn-step.prev').is_disabled()
    assert page.locator('.tn-chart linearGradient[id$="bhigh"]').count() == 1           # Wolkenband nach Höhe gestaffelt
    assert page.locator('.tn-series .tn-high').count() == len(plans[0]['recommendations'])
    assert page.locator('.tn-legend li').count() >= 5 and len(page.locator('.tn-legend').inner_text()) < 80
    assert not page.evaluate(FIND_OVERFLOW)
    page.locator('.tn-step.next').click()
    page.wait_for_selector('.tn-strip [data-tn-night="1"][aria-selected=true]')
    assert page.locator('.tn-nav-name').inner_text().startswith('Morgen Nacht') and asked[-1] == 1
    page.locator('.tn-nav-name').click()                                      # Fokus aus dem Eingabefeld
    page.keyboard.press('ArrowRight')
    page.wait_for_selector('.tn-strip [data-tn-night="2"][aria-selected=true]')
    assert page.locator('.tn-unsure').inner_text() == 'Vorhersage unsicher'
    page.keyboard.press('ArrowLeft')
    page.wait_for_selector('.tn-strip [data-tn-night="1"][aria-selected=true]')
    page.locator('.tn-strip [data-tn-night="9"]').click()
    page.wait_for_selector('.tn-strip [data-tn-night="9"][aria-selected=true]')
    assert page.locator('.tn-unsure').inner_text() == 'ohne Wetter'
    assert page.locator('.tn-chart linearGradient[id$="bhigh"]').count() == 0          # jenseits der Vorhersage nur Astronomie
    page.locator('.tn-time').focus()
    page.keyboard.press('ArrowRight')                                         # im Zeitregler: Uhrzeit, keine andere Nacht
    assert page.locator('.tn-strip [data-tn-night="9"]').get_attribute('aria-selected') == 'true'
    assert not page.evaluate(FIND_OVERFLOW)
    page.screenshot(path=str(tmp_path / 'naechte.png'), full_page=True)
    page.goto(base + '/#/')                                                   # Übersicht verlassen → wieder heute
    page.goto(base + '/#/nacht')
    page.wait_for_selector('af-tonight-chart svg')
    assert page.locator('.tn-nav-name').inner_text().startswith('Heute Nacht')
    page.close()
    assert not errors, errors


@pytest.mark.parametrize('size', ['handy', 'desktop', 'gross'])
def test_tonight_recommendations_chart_and_links(server, browser, size, tmp_path):
    """Echte Nachtkurven mit festem Datum: Übersicht, responsive Grafik, lokale Katalogkarte und Sage-Klick."""
    from astroflow.modules.stacking import tonight
    from tests.test_tonight import SITE, EVENING, M31
    from pathlib import Path
    d = tonight.plan([{**M31, 'exposure': 1800}], SITE, Path('unused'), EVENING,
                     {'name': 'Testgerät', 'fov': [1, .7], 'source': 'Testmessung'})
    base, pid = server
    w, h = SIZES.get(size, (2560, 1273))
    page = browser.new_page(viewport={'width': w, 'height': h}, has_touch=size == 'handy')
    errors = []
    page.on('pageerror', lambda e: errors.append(str(e)))
    page.route('**/api/stacking/tonight', lambda r: r.fulfill(json=d))
    projects = page.request.get(base + '/api/stacking/projects').json()
    gallery = [{**projects[0], 'name': f'Testobjekt {i + 1}'} for i in range(6)]
    page.route('**/api/stacking/projects', lambda r: r.fulfill(json=gallery))
    page.goto(base + '/#/')
    # Standard „Ausgewogen“: kompakte Karte (Lage + bis zu drei Ziele + Verweis), keine Grafik
    page.wait_for_selector('.home-featured .tn-top li')
    assert page.locator('.home-featured af-tonight-chart').count() == 0
    assert 1 <= page.locator('.home-featured .tn-top li').count() <= 3
    assert page.locator('.home-featured a[href="#/nacht"]').is_visible()
    if size == 'gross':                                    # zentriert: Inhalt begrenzt, Außenränder wachsen
        grid = page.locator('.home-grid').bounding_box()
        assert grid['width'] < 1700 and grid['x'] > 400, grid
    page.get_by_label('Dashboard-Aufteilung').select_option('planning')
    page.wait_for_selector('af-tonight-chart svg')
    assert round(page.locator('.tn-chart').bounding_box()['height']) >= 340
    total = len(d['recommendations'])
    assert page.locator('.tn-choice').count() == total
    visible = page.locator('.tn-series').count()
    page.locator('[data-visible="0"]').uncheck()
    assert page.locator('.tn-series').count() == visible - 1
    page.locator('[data-visible="0"]').check()
    page.locator('[data-select="1"]').click()
    assert page.locator('[data-select="1"]').get_attribute('aria-pressed') == 'true'
    page.locator('.tn-target summary').click()
    assert page.locator('.tn-target').get_attribute('open') is not None
    slider = page.locator('.tn-time')
    slider.focus()
    slider.press('End')
    assert slider.input_value() == str(len(d['times']) - 1)
    assert page.locator('.tn-tooltip').is_visible()
    assert page.locator('.tn-cursor line').count() == 1
    diagram = page.locator('.tn-chart')
    diagram.hover(position={'x': 100, 'y': 120})
    assert int(slider.input_value()) < len(d['times']) - 1
    if size == 'handy':
        diagram.tap(position={'x': 170, 'y': 150})
        assert page.locator('.tn-tooltip').is_visible()
    slider.focus()
    slider.press('End')
    # Hauptspalte (Bilder, Entdeckungen, Module) steht auf Desktopbreite links neben „Heute Nacht“.
    if size != 'handy':
        main = page.locator('.home-main').bounding_box()
        diagram = page.locator('.home-featured').bounding_box()
        assert diagram['x'] > main['x'] + main['width'] - 1
        assert page.locator('.hthumbs a').first.bounding_box()['width'] >= (140 if size == 'gross' else 85)
        assert page.locator('.hthumbs span').first.is_visible()
    # Aufteilung wechselt ohne Verlust der Kurvenauswahl oder des Zeitzeigers.
    mode = page.get_by_label('Dashboard-Aufteilung')
    chosen = page.locator('[data-select][aria-pressed="true"]').get_attribute('data-select')
    # Andere Aufteilungen zeigen die kompakte Karte; zurück auf „Nachtplanung“ kommt die Grafik mit derselben Auswahl wieder
    for value in ['library', 'balanced']:
        mode.select_option(value)
        page.wait_for_selector('.home-featured .tn-top li')
        assert page.locator('.home-featured af-tonight-chart').count() == 0
        assert not page.evaluate(FIND_OVERFLOW)
    mode.select_option('planning')
    page.wait_for_selector('af-tonight-chart svg')
    page.locator(f'[data-select="{chosen}"]').click()
    assert page.locator(f'[data-select="{chosen}"]').get_attribute('aria-pressed') == 'true'
    slider = page.locator('.tn-time')
    slider.focus()
    slider.press('End')
    assert not page.evaluate(FIND_OVERFLOW)
    if size == 'gross':
        sky = page.locator('.home-modules [data-m="skytrace"]').bounding_box()
        sage = page.locator('.home-modules [data-m="sage"]').bounding_box()
        assert abs(sky['y'] - sage['y']) < 2 and sage['x'] > sky['x']
    assert not page.evaluate(FIND_OVERFLOW)
    page.screenshot(path=str(tmp_path / 'nacht.png'), full_page=True)
    # Kein Neuzeichnen der Grafik beim unveränderten Status-Polling.
    chart = page.locator('af-tonight-chart').element_handle()
    selected = page.locator('[data-select][aria-pressed="true"]').get_attribute('data-select')
    page.route('**/api/stacking/projects', lambda r: r.fulfill(json=[]))
    page.wait_for_timeout(3000)
    assert chart.evaluate('(e) => e.isConnected')
    assert page.locator(f'[data-select="{selected}"]').get_attribute('aria-pressed') == 'true'
    assert slider.input_value() == str(len(d['times']) - 1)
    page.unroute('**/api/stacking/projects')
    page.goto(base + '/#/nacht')
    page.wait_for_selector('af-tonight-chart svg')
    assert page.locator('h1').inner_text() == 'Heute Nacht'
    assert not page.evaluate(FIND_OVERFLOW)
    new = next(x for x in d['recommendations'] if x['category'] == 'new')
    page.goto(base + '/#/nacht/' + new['id'])
    page.wait_for_selector('af-tonight-chart svg')
    assert new['name'] in page.locator('#view').inner_text()
    # Sage-Karten werden erst durch den Klick aufgelöst (keine automatische Recherche).
    page.route('**/api/sage/kb/card?*', lambda r: r.fulfill(json={'found': True, 'id': 'test-target'}))
    page.locator('.tn-selection a[data-night-catalog]').first.click()
    page.wait_for_url('**/#/sage/o/test-target')
    page.goto(base + '/#/')
    page.get_by_label('Dashboard-Aufteilung').select_option('library')
    page.reload()
    page.wait_for_selector('.home-featured .tn-top li')
    assert page.get_by_label('Dashboard-Aufteilung').input_value() == 'library'
    page.close()
    assert not errors, errors


@pytest.mark.parametrize("size", ["handy", "desktop"])
def test_unused_site_hint_and_assign(server, browser, size):
    """Ein Ort ohne Zeitraum zeigt „Wird noch nicht verwendet“; „Zeitraum zuweisen“ legt einen Zeitraum an, danach verschwindet der Hinweis."""
    base, _ = server
    page = browser.new_page(viewport={"width": SIZES[size][0], "height": SIZES[size][1]})
    page.goto(f"{base}/#/settings/standorte")
    row = page.locator('.site-row[data-id="c"]')
    row.wait_for()
    assert "Wird noch nicht verwendet" in row.locator(".site-unused").inner_text()
    assert page.locator('.site-row[data-id="a"] .site-unused, .site-row[data-id="b"] .site-unused').count() == 0
    try:
        row.locator('[data-act="assign"]').click()
        form = row.locator("#siteAssign")
        form.wait_for()
        form.locator("[name=from]").fill("2030-02-01")
        form.locator("[name=to]").fill("2030-02-05")
        form.locator("button[type=submit]").click()
        page.wait_for_selector('.site-row[data-id="c"]:not(:has(.site-unused)):not(:has(#siteAssign))')
        assert "2030-02-01" in page.eval_on_selector_all(".site-periods input[data-f=from]", "els => els.map(e => e.value)")
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
    finally:                                                 # gemeinsame Fixture wiederherstellen
        d = page.evaluate("fetch('/api/sites?nights=false').then(r => r.json())")
        page.evaluate("(d) => fetch('/api/sites', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(d)})",
                      {**d, "periods": [p for p in d["periods"] if p["from"] != "2030-02-01"]})
    page.close()


def test_einstellungen_schalter_und_breite(server, browser):
    """Einstellungen: normale Seitenbreite, Ein/Aus-Optionen sind sichtbare Schalter (Klick auf die Zeile und Leertaste schalten),
    kein nacktes Kästchen mehr in den Einstellungen."""
    base, _ = server
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    page.goto(f"{base}/#/settings/stacking")
    page.wait_for_selector("#stf")
    page.wait_for_timeout(500)
    assert "narrow" not in page.locator("#view .page").first.get_attribute("class")
    assert page.locator("#sbody").bounding_box()["width"] > 700
    assert page.locator("#sbody input[type=checkbox]:not(#hkForm input)").count() == page.locator("#sbody .modrow:not(#hkForm .modrow) .switch").count()
    box = page.locator("#stf input[name=gaia]")
    row = page.locator("#stf label.modrow", has=page.locator("input[name=gaia]"))
    sw = row.locator(".switch")
    assert sw.bounding_box()["width"] >= 36 and sw.bounding_box()["height"] >= 20
    before = box.is_checked()
    row.locator("b").click()
    assert box.is_checked() != before
    box.focus()
    page.keyboard.press("Space")
    assert box.is_checked() == before
    page.close()


@pytest.mark.parametrize("size", list(SIZES))
def test_aufnahme_popup(server, browser, size):
    """Reiter „Aufnahmen“: Vorschaubild in der Statusspalte öffnet das Popup mit Begründung und Messwerten – passt in jede Breite,
    Pfeiltaste blättert, Sternmarken lassen sich zuschalten."""
    base, pid = server
    w, h = SIZES[size]
    page = browser.new_page(viewport={"width": w, "height": h})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(f"{base}/#/p/{pid}/aufnahmen")
    thumb = page.locator("#ftable .fthumb").first
    thumb.scroll_into_view_if_needed()
    thumb.click()
    dlg = page.locator("#dlg .fpv")
    dlg.locator(".fpv-facts").wait_for()
    first = dlg.locator("h2").inner_text()
    assert dlg.locator(".fpv-verdict b").inner_text()
    assert page.evaluate("(() => { const d = document.querySelector('#dlg'); return d.scrollWidth <= d.clientWidth + 1 })()")
    if dlg.locator("#fpvStars").count():
        dlg.locator("label.modrow").click()
        assert page.locator(".fpv-stars").is_visible()
    page.keyboard.press("ArrowRight")
    page.wait_for_function("(n) => document.querySelector('#dlg h2')?.textContent !== n", arg=first)
    page.keyboard.press("Escape")
    page.close()
    assert not errors, errors
