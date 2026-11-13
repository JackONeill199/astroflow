"""Upload in Teilanfragen: Starlette erlaubt höchstens 1000 Dateien je Multipart-Anfrage (Fehler erst nach der Übertragung)."""
import io
import threading
import time

import numpy as np
import pytest
from astropy.io import fits
from fastapi.testclient import TestClient

from astroflow.core.config import load_settings
from astroflow.server import create_app


def _fits(v=1):
    buf = io.BytesIO()
    fits.PrimaryHDU(np.full((4, 4), v, np.uint16)).writeto(buf)
    return buf.getvalue()


def test_teilanfragen_gleicher_ordner(tmp_path):
    s = load_settings(tmp_path / "d")
    s.auto = False
    with TestClient(create_app(s)) as c:
        for k in range(3):
            r = c.post("/api/stacking/upload", data={"upload_id": "vorgang123456"},
                       files=[("files", (f"Light_M31_10.0s_LP_20261001-22{k:02d}{i:02d}.fit", _fits(k * 10 + i))) for i in range(4)])
            assert r.status_code == 200 and r.json()["raw"] == 4
    ordner = {p.parent for p in (s.input_dir / "uploads").rglob("*.fit")}
    assert len(ordner) == 1 and len(list(ordner.pop().glob("*.fit"))) == 12


def test_zu_viele_dateien_verstaendliche_meldung(tmp_path):
    s = load_settings(tmp_path / "d")
    s.auto = False
    with TestClient(create_app(s)) as c:
        r = c.post("/api/stacking/upload", files=[("files", (f"a{i}.fit", b"x")) for i in range(1001)])
    assert r.status_code == 413
    assert "Zu viele Dateien" in r.json()["detail"] and "Teilen" in r.json()["detail"]
    assert not list((s.input_dir / "uploads").rglob("*.fit")) if (s.input_dir / "uploads").exists() else True


@pytest.fixture()
def eigener_server(tmp_path, monkeypatch):
    """Eigener Server (nur Stacking, leerer Datenordner) – der Upload schreibt Dateien."""
    pytest.importorskip("playwright.sync_api")
    import uvicorn
    from test_ui_layout import _free_port
    monkeypatch.setenv("ASTROFLOW_MODULES", "stacking")
    for k in ("AUTO", "GAIA", "GPU", "GRAXPERT", "PICTURE_SOLVE"):
        monkeypatch.setenv(f"ASTROFLOW_{k}", "0")
    s = load_settings(tmp_path / "data")
    port = _free_port()
    srv = uvicorn.Server(uvicorn.Config(create_app(s), host="127.0.0.1", port=port, log_level="warning"))
    th = threading.Thread(target=srv.run, daemon=True)
    th.start()
    for _ in range(200):
        if srv.started:
            break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}", s
    srv.should_exit = True
    th.join(10)


def _dateien(n):
    return [{"name": f"Light_M31_10.0s_LP_20261001-{i:06d}.fit", "mimeType": "application/octet-stream", "buffer": _fits(i % 60000)} for i in range(n)]


def test_ui_upload_in_teilen(eigener_server, browser):
    url, s = eigener_server
    page = browser.new_page()
    errors, posts = [], []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("request", lambda r: posts.append(r.url) if r.method == "POST" and r.url.endswith("/api/stacking/upload") else None)
    page.goto(f"{url}/#/import")
    page.wait_for_selector("#fi", state="attached")
    page.set_input_files("#fi", _dateien(450))
    page.wait_for_selector(".upl.done", timeout=60000)
    assert len(posts) == 3                                                # 200 + 200 + 50
    ordner = {p.parent for p in (s.input_dir / "uploads").rglob("*.fit")}
    assert len(ordner) == 1                                               # alle Teile mit derselben upload_id
    assert len(list(next(iter(ordner)).glob("*.fit"))) == 450
    assert "450" in page.inner_text(".upl .pct")
    page.close()
    assert not errors, errors


def test_ui_teil_wird_einmal_wiederholt(eigener_server, browser):
    url, s = eigener_server
    page = browser.new_page()
    zaehler = {"n": 0}

    def route(r):
        zaehler["n"] += 1
        if zaehler["n"] == 2:                                             # zweiter Teil scheitert beim ersten Versuch
            return r.abort()
        r.continue_()
    page.route("**/api/stacking/upload", route)
    page.goto(f"{url}/#/import")
    page.wait_for_selector("#fi", state="attached")
    page.set_input_files("#fi", _dateien(250))
    page.wait_for_selector(".upl.done", timeout=60000)
    assert zaehler["n"] == 3                                              # Teil 1, Teil 2 (fehlgeschlagen), Teil 2 erneut
    assert len(list((s.input_dir / "uploads").rglob("*.fit"))) == 250
    page.close()


def test_ui_teil_scheitert_endgueltig(eigener_server, browser):
    url, s = eigener_server
    page = browser.new_page()
    zaehler = {"n": 0}

    def route(r):
        zaehler["n"] += 1
        if zaehler["n"] >= 2:                                             # ab dem zweiten Teil immer Fehler
            return r.abort()
        r.continue_()
    page.route("**/api/stacking/upload", route)
    page.goto(f"{url}/#/import")
    page.wait_for_selector("#fi", state="attached")
    page.set_input_files("#fi", _dateien(450))
    page.wait_for_selector(".upl p.bad", timeout=60000)
    text = page.inner_text(".upl p.bad")
    assert "200 von 450" in text and "250 fehlen" in text
    assert zaehler["n"] == 3                                              # 1 gut, 2 mal der zweite Teil
    assert len(list((s.input_dir / "uploads").rglob("*.fit"))) == 200     # Übertragenes bleibt
    page.close()
