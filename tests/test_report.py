"""Fehler melden (core/report.py): Bereinigung, Ansicht ohne IDs, URL-Grenze, Endpunkte, Abschalten, Herkunftsschutz.
Der Server sendet nichts nach außen – geprüft wird nur, was er für das GitHub-Formular zusammenstellt."""
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from astroflow.core import report
from astroflow.core.config import load_settings
from astroflow.server import create_app

SITE = {"name": "Sternwarte Erfundenhausen", "lat": 47.6543, "lon": 9.8765, "alt_m": 400}


@pytest.fixture
def app_client(tmp_path, monkeypatch):
    monkeypatch.delenv("ASTROFLOW_REPORT", raising=False)
    monkeypatch.delenv("ASTROFLOW_LAUNCHER", raising=False)
    c = TestClient(create_app(load_settings(tmp_path / "data"), only=()))
    r = c.post("/api/sites", json={"list": [SITE]})
    assert r.status_code == 200, r.text
    return c, tmp_path / "data"


def test_scrubber_masks_private_parts():
    s = report.Scrubber(["Sternwarte Erfundenhausen", "47.6543", "erika"])
    out = s(r"Datei C:\Users\erika\AstroFlow\input\m42.fits fehlt; /home/erika/AstroFlow/x.fits auch; "
            "Ort Sternwarte Erfundenhausen bei 47.6543, 9.88; lat=47.65 lon: 9.87; 48° 07′ 24″ N; "
            "key=sk-abcdefghijklmnopqrstuvwx token: geheim123; von 192.168.178.20, fe80::1:2:3:4; Mail erika@example.org")
    for leak in ("erika", "Users", "m42.fits", "Erfundenhausen", "47.65", "9.87", "48° 07", "sk-abc", "geheim123", "192.168", "fe80", "example.org"):
        assert leak not in out, (leak, out)
    for tag in ("<Pfad>", "<privat>", "<Koordinate>", "<Schlüssel>", "<IP>", "<E-Mail>"):
        assert tag in out
    # Unkritisches bleibt lesbar
    keep = s("TypeError: x is undefined (stacking.js:12) · AstroFlow 0.71.0 · Python 3.12.1 · 12:30:45")
    assert keep == "TypeError: x is undefined (stacking.js:12) · AstroFlow 0.71.0 · Python 3.12.1 · 12:30:45"


def test_route_without_ids():
    assert report.clean_route("#/bilder/projekt/123/ergebnis?x=1") == "#/bilder/projekt/:id/ergebnis"
    assert report.clean_route("#/sage/objekt/M42") == "#/sage/objekt/:id"
    assert report.clean_route("#/settings/standorte") == "#/settings/standorte"
    assert report.clean_route("#/skytrace/lauf/C%3A%5CUsers%5Cx") == "#/skytrace/lauf/:id"


def test_url_stays_below_limit():
    url, moved = report.build({"title": "x", "actual": "a" * 9000, "steps": "s" * 4000, "log": "l" * 3000, "umgebung": "u" * 500})
    assert len(url) <= report.URL_MAX
    assert url.startswith(report.NEW_ISSUE + "?template=fehler.yml")
    assert moved[:2] == ["log", "umgebung"] and "actual" in moved
    # Endet auch, wenn alle Felder bis zur Mindestlänge gekürzt werden müssen (Codex P1: Endlosschleife)
    url, moved = report.build({"title": "ä" * 150, "actual": "a" * 4000, "steps": "ä" * 2000, "expected": "e" * 2000})
    assert len(url) <= report.URL_MAX and {"actual", "steps"} <= set(moved)
    url, _ = report.build({"title": "ä" * 150, "actual": "ä" * 4000, "steps": "ä" * 3000, "expected": "ä" * 2000, "log": "ä" * 5000})
    assert url.startswith(report.NEW_ISSUE)              # Mindestlängen erreicht: endet trotzdem
    short, none = report.build({"title": "kurz", "actual": "kurz"})
    assert none == [] and "actual=kurz" in short


def test_compose_endpoint(app_client):
    c, data = app_client
    data.mkdir(parents=True, exist_ok=True)
    (data / "server.log.err").write_text(f"Traceback …\n  File \"{data / 'x.py'}\", line 3\nValueError: Ort {SITE['name']}\n",
                                         encoding="utf-8")
    info = c.get("/api/report").json()
    assert info["enabled"] and SITE["name"] in info["mask"] and info["environment"]["version"]
    r = c.post("/api/report/compose", json={
        "title": f"Absturz in {SITE['name']}", "actual": r"Fehler bei C:\Users\erika\bild.fits", "expected": "kein Absturz",
        "area": "stacking", "route": "#/bilder/projekt/42", "errors": ["TypeError: kaputt (app.js:3)", "x" * 2000],
        "browser": "Firefox 140", "width": 1440, "log": True})
    assert r.status_code == 200, r.text
    d = r.json()
    q = parse_qs(urlsplit(d["url"]).query)
    assert d["url"].startswith("https://github.com/JackONeill199/astroflow/issues/new?")
    assert q["template"] == ["fehler.yml"] and q["title"][0].startswith("[Fehler] Absturz in <privat>")
    assert q["module"] == ["Bilder (Stacking/Entwicklung)"] and q["version"]
    assert "#/bilder/projekt/:id" in q["umgebung"][0] and "Firefox 140" in q["umgebung"][0] and "TypeError: kaputt" in q["umgebung"][0]
    assert "ValueError: Ort <privat>" in q["log"][0]
    whole = d["url"] + d["clipboard"] + str(d["preview"])
    for leak in (SITE["name"], "erika", str(data), "47.6543"):
        assert leak not in whole, leak
    assert len(d["url"]) <= report.URL_MAX


def test_compose_requires_text_and_limits_size(app_client):
    c, _ = app_client
    assert c.post("/api/report/compose", json={"title": "", "actual": ""}).status_code == 400
    assert c.post("/api/report/compose", json={"title": "x", "actual": "y" * 70000}).status_code == 413


def test_report_can_be_switched_off(app_client, monkeypatch):
    c, _ = app_client
    assert c.get("/api/app").json()["report"] is True
    assert c.post("/api/setup", json={"report": False}).json()["report"] is False
    assert c.get("/api/app").json()["report"] is False
    assert c.get("/api/report").status_code == 403
    assert c.post("/api/report/compose", json={"title": "x", "actual": "y"}).status_code == 403
    c.post("/api/setup", json={"report": True})
    monkeypatch.setenv("ASTROFLOW_REPORT", "0")
    assert c.get("/api/app").json()["report"] is False


def test_compose_needs_own_origin(app_client):
    c, _ = app_client
    r = c.post("/api/report/compose", json={"title": "x", "actual": "y"}, headers={"Origin": "https://example.org"})
    assert r.status_code == 403


def test_issue_form_has_prefilled_fields():
    """Die Feld-IDs, die compose() füllt, müssen im Issue-Formular stehen (sonst landet der Text nirgends)."""
    form = (Path(__file__).resolve().parent.parent / ".github" / "ISSUE_TEMPLATE" / "fehler.yml").read_text(encoding="utf-8")
    for fid in ("version", "os", "install", "module", "steps", "expected", "actual", "umgebung", "log"):
        assert f"id: {fid}\n" in form, fid
    for opt in report.AREAS.values():
        assert f"- {opt}\n" in form, opt
