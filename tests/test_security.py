"""Zugriffsschutz (core/security.py, server.py): Host-Prüfung, Herkunft schreibender Anfragen, Kopplung von Heimnetz-Geräten,
Endpunkte nur am Rechner selbst, Schlüssel nur an die eigene Adresse, Prüfsummen der Downloads, QR-Code.

Adressen nur aus den Dokumentationsbereichen (192.0.2.0/24, example.org) – keine echten Netze."""
import hashlib
import io
import json
import socket
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from astroflow.core import components as comp, qr, security
from astroflow.core.config import load_settings
from astroflow.server import create_app

PC = "http://192.0.2.10:8766"                       # Adresse des Rechners im Heimnetz (Dokumentationsbereich)
PHONE = ("192.0.2.20", 40000)                         # anderes Gerät im Heimnetz


def _app(tmp_path, monkeypatch, modules="stacking"):
    monkeypatch.setenv("ASTROFLOW_MODULES", modules)
    monkeypatch.setattr(security, "lan_addresses", lambda: ["192.0.2.10"])
    return create_app(load_settings(tmp_path / "data"))


def _local(app, **kw):
    return TestClient(app, base_url="http://127.0.0.1:8766", client=("127.0.0.1", 50000), **kw)


def _phone(app, cookie: str | None = None):
    headers = {"Origin": PC}
    if cookie:
        headers["Cookie"] = f"{security.COOKIE}={cookie}"
    return TestClient(app, base_url=PC, client=PHONE, headers=headers)


def _pair(app) -> str:
    """Kopplung wie mit dem Handy: Code am Rechner holen, Link am Gerät öffnen → Token aus dem Cookie."""
    with _local(app, headers={"Origin": "http://127.0.0.1:8766"}) as pc:
        link = pc.get("/api/pairing").json()["links"][0]
    with _phone(app) as ph:
        r = ph.get(link.replace(PC, ""), follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/"
    sc = r.headers["set-cookie"]
    assert "httponly" in sc.lower() and "samesite=strict" in sc.lower() and sc.startswith(security.COOKIE + "=")
    return sc.split(";", 1)[0].split("=", 1)[1]


def test_host_pruefung(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch)
    for base in ("http://localhost:8766", "http://127.0.0.1:8766", "http://[::1]:8766", PC, f"http://{socket.gethostname()}:8766"):
        with _local(app) as c:
            assert c.get("/api/status", headers={"host": base.split("//")[1]}).status_code == 200, base
    with _local(app) as c:                                # fremder Name (z. B. eine Webseite, die auf 127.0.0.1 zeigt)
        r = c.get("/api/status", headers={"host": "boese.example.org:8766"})
        assert r.status_code == 400 and "Hostname" in r.json()["detail"]
        assert c.get("/", headers={"host": "boese.example.org:8766"}).status_code == 400
    monkeypatch.setenv("ASTROFLOW_HOSTS", "sternwarte.example.org")       # zusätzliche Namen per Einstellung
    with _local(app) as c:
        assert c.get("/api/status", headers={"host": "sternwarte.example.org:8766"}).status_code == 200


def test_herkunft_schreibender_anfragen(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch)
    with _local(app, headers={}) as c:
        assert c.post("/api/stacking/scan").status_code == 403                                     # ohne Origin/Referer
        assert c.post("/api/stacking/scan", headers={"origin": "http://boese.example.org"}).status_code == 403
        assert c.post("/api/stacking/scan", headers={"origin": "http://localhost:3000"}).status_code == 403   # andere Seite am PC
        assert c.post("/api/stacking/scan", headers={"origin": "null"}).status_code == 403
        assert c.post("/api/stacking/scan", headers={"origin": "http://127.0.0.1:8766"}).status_code == 200
        assert c.post("/api/stacking/scan", headers={"referer": "http://127.0.0.1:8766/#/import"}).status_code == 200
        assert c.get("/api/status").status_code == 200                                              # lesend ohne Origin
        # Origin passt zum Namen, aber nicht zum Host-Kopf der Anfrage
        assert c.post("/api/stacking/scan", headers={"origin": "http://localhost:8766"}).status_code == 403


def test_fremdes_geraet_braucht_kopplung(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch)
    with _phone(app) as ph:
        r = ph.get("/api/status")
        assert r.status_code == 401 and r.json()["pair"]
        page = ph.get("/")
        assert page.status_code == 401 and "Gerät koppeln" in page.text and "Einstellungen → Heimnetz" in page.text
        assert ph.post("/api/stacking/scan").status_code == 401
        assert ph.get("/api/pair", params={"code": "falsch"}).status_code == 403
    with _phone(app, cookie="erfunden") as ph:
        assert ph.get("/api/status").status_code == 401
    with _local(app) as c:                                  # eigener Rechner braucht keine Kopplung
        assert c.get("/api/status").status_code == 200 and c.get("/api/app").json()["local"] is True


def test_kopplung_ablauf(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch)
    with _local(app) as pc:
        d = pc.get("/api/pairing").json()
        assert d["links"] == [f"{PC}/api/pair?code={d['links'][0].split('code=')[1]}"] and d["qr"].startswith("<svg")
        assert d["devices"] == [] and d["expires"] > 0
        old = d["links"][0]
    token = _pair(app)
    with _phone(app, token) as ph:
        assert ph.get("/api/status").status_code == 200
        assert ph.get("/api/app").json()["local"] is False
        assert ph.post("/api/stacking/scan").status_code == 200                          # schreibend mit eigener Herkunft
        assert ph.post("/api/stacking/scan", headers={"origin": "http://boese.example.org"}).status_code == 403
        assert ph.get(old.replace(PC, "")).status_code == 403                          # Code gilt nur einmal
    with _local(app) as pc:
        d = pc.get("/api/pairing").json()
        assert len(d["devices"]) == 1 and d["links"][0] != old                          # neuer Code für das nächste Gerät
        dev = d["devices"][0]["id"]
        new = pc.post("/api/pairing/code").json()["links"][0]
        assert new != d["links"][0]                                                    # „Neuen Kopplungscode erzeugen“
    raw = (tmp_path / "data" / "settings.json").read_text(encoding="utf-8")
    assert token not in raw and hashlib.sha256(token.encode()).hexdigest() in raw       # nur der Hash wird gespeichert
    with _local(app) as pc:
        assert pc.post("/api/pairing/remove", json={"id": dev}).status_code == 200
    with _phone(app, token) as ph:
        assert ph.get("/api/status").status_code == 401                                  # entfernt → wieder koppeln


def test_kopplungscode_laeuft_ab(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch)
    with _local(app) as pc:
        link = pc.get("/api/pairing").json()["links"][0]
    acc = app.state.access
    acc.store.update("pairing", {"code_created": acc.store.get("pairing")["code_created"] - security.CODE_TTL - 1})
    with _phone(app) as ph:
        assert ph.get(link.replace(PC, ""), follow_redirects=False).status_code == 403


def test_gefaehrliche_endpunkte_nur_am_rechner(tmp_path, monkeypatch):
    monkeypatch.setenv("SKYTRACE_HOME", str(tmp_path / "st"))
    app = _app(tmp_path, monkeypatch, "stacking,skytrace")
    token = _pair(app)
    zipped = io.BytesIO()
    with zipfile.ZipFile(zipped, "w") as z:
        z.writestr("starnet2.exe", b"x")
    with _phone(app, token) as ph:
        for path, body in [("/api/setup/firewall", None), ("/api/system/shutdown", None), ("/api/system/restart", None),
                           ("/api/components/install", {"id": "astap", "option": "d05"}), ("/api/components/remove", {"id": "astap"}),
                           ("/api/setup/astap", {"db": "d05"}), ("/api/setup/gpu", None),
                           ("/api/setup", {"lan": True}), ("/api/setup", {"data_dir": str(tmp_path / "x")}),
                           ("/api/stacking/starnet", {"path": str(tmp_path / "starnet2.exe")}),
                           ("/api/stacking/graxpert", {"path": str(tmp_path / "GraXpert.exe")}),
                           ("/api/stacking/solver", {"astap": str(tmp_path / "astap_cli.exe")}),
                           ("/api/skytrace/engine", {"python": str(tmp_path / "python.exe")}),
                           ("/api/pairing/code", None), ("/api/pairing/remove", {"id": "x"})]:
            r = ph.post(path, json=body or {})
            assert r.status_code == 403 and "Rechner selbst" in r.json()["detail"], (path, r.status_code, r.text)
        r = ph.post("/api/stacking/starnet/install", files={"file": ("starnet.zip", zipped.getvalue(), "application/zip")})
        assert r.status_code == 403
        assert ph.delete("/api/stacking/starnet").status_code == 403
        assert ph.get("/api/pairing").status_code == 403
        # Harmloses geht weiter, auch wenn das Formular die Pfadfelder unverändert mitschickt
        assert ph.post("/api/setup", json={"telescope": "stellina"}).status_code == 200
        gx = ph.get("/api/stacking/graxpert").json()
        assert ph.post("/api/stacking/graxpert", json={"mode": gx["mode"], "gpu": False, "path": gx["custom"] or ""}).status_code == 200
        sv = ph.get("/api/stacking/solver").json()          # liefert den gefundenen ASTAP-Pfad (auch ohne Einstellung)
        assert ph.post("/api/stacking/solver", json={"astap": sv.get("astap") or "", "astap_db": sv.get("astap_db") or "",
                                                     "timeout": 120}).status_code == 200
        monkeypatch.setattr(type(app.state.modules["stacking"].eng.pl.solver_cfg), "astap_path", lambda self: str(tmp_path / "astap_cli.exe"))
        assert ph.post("/api/stacking/solver", json={"astap": str(tmp_path / "astap_cli.exe")}).status_code == 200   # = gefunden
        assert ph.post("/api/stacking/solver", json={"astap": str(tmp_path / "anderes.exe")}).status_code == 403
        assert ph.post("/api/skytrace/engine", json={"auto": False}).status_code == 200
    assert not (tmp_path / "x").exists()


def test_schluessel_nur_an_eigene_adresse(tmp_path, monkeypatch):
    from astroflow.core import models
    seen = []

    class R:
        status_code = 200

        def json(self):
            return {"data": [{"id": "m1", "architecture": {"input_modalities": ["text", "image"], "output_modalities": ["text"]}}]}

    monkeypatch.setattr(models.httpx, "get", lambda url, params=None, headers=None, timeout=None: seen.append((url, headers)) or R())
    monkeypatch.setattr(models, "_cache", {})
    app = _app(tmp_path, monkeypatch, "stacking,sage")
    with _local(app) as c:
        # KI-Prüfer: gespeicherter OpenRouter-Schlüssel
        c.post("/api/stacking/reviewer", json={"provider": "openrouter", "api_key": "sk-or-geheim"})
        assert c.get("/api/stacking/reviewer/models", params={"provider": "openrouter", "base_url": "https://boese.example.org/x"}).status_code in (404, 405)
        assert c.post("/api/stacking/reviewer/models", json={"provider": "openrouter", "base_url": "https://boese.example.org/x"}).status_code == 200
        assert seen[-1] == ("https://boese.example.org/x/models", {})
        c.post("/api/stacking/reviewer/models", json={"provider": "openrouter", "base_url": "https://openrouter.ai/api/v1/"})
        assert seen[-1][1] == {"Authorization": "Bearer sk-or-geheim"}
        # Sage: Schlüssel der Erzähl-KI
        c.post("/api/sage/config", json={"story_provider": "openai", "story_api_key": "sk-geheim"})
        assert c.get("/api/sage/models", params={"kind": "story", "base_url": "https://boese.example.org/v1"}).status_code in (404, 405)
        c.post("/api/sage/models", json={"kind": "story", "base_url": "https://boese.example.org/v1"})
        assert seen[-1] == ("https://boese.example.org/v1/models", {})
        c.post("/api/sage/models", json={"kind": "story", "provider": "openrouter", "base_url": "https://boese.example.org/v1"})
        assert seen[-1][1] == {}
        c.post("/api/sage/models", json={"kind": "story"})
        assert seen[-1] == ("https://api.openai.com/v1/models", {"Authorization": "Bearer sk-geheim"})
    from astroflow.modules.sage import clients
    cfg = {"story_provider": "custom", "story_base_url": "https://mein-server.example.org/v1", "story_api_key": "k"}
    assert clients.story_endpoint(cfg) == ("https://mein-server.example.org/v1", "k")
    assert clients.story_endpoint(cfg, base_url="https://anderer.example.org/v1")[1] == ""


def test_falsche_pruefsumme_bricht_ab(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "lad"))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "lad"))
    monkeypatch.setattr(comp.platform, "system", lambda: "Windows")
    monkeypatch.setattr(comp.platform, "machine", lambda: "AMD64")
    monkeypatch.setenv("ASTROFLOW_HOME", str(tmp_path / "app"))
    bogus = io.BytesIO()
    with zipfile.ZipFile(bogus, "w") as z:
        z.writestr("astap_cli.exe", b"nicht die echte Datei")
    monkeypatch.setattr(comp, "download", lambda url, prog, to=None, resume=False: bogus.getvalue())
    ran = []
    monkeypatch.setattr(comp.subprocess, "run", lambda *a, **k: ran.append(a))
    with pytest.raises(comp.ChecksumError, match="Prüfsumme"):
        comp.install("astap", option="d05")
    dest = tmp_path / "lad" / "astap"
    assert not list(dest.glob("astap_cli*")) and not ran                         # nichts geschrieben, nichts gestartet
    part = tmp_path / "d05.zip.part"
    part.write_bytes(b"falsch")
    with pytest.raises(comp.ChecksumError):
        comp.verify_astap(comp.ASTAP_DBS["d05"][0], part)
    assert not part.exists()                                                     # verworfen, nächster Versuch lädt neu
    assert all(len(h) == 64 for hs in comp.ASTAP_SHA256.values() for h in hs)
    assert set(comp.ASTAP_SHA256) == {*comp.ASTAP_CLI.values(), comp.ASTAP_CATALOG, *(v[0] for v in comp.ASTAP_DBS.values())}


def test_downloads_nur_https():
    import httpx
    with pytest.raises(comp.ChecksumError, match="nur https"):
        comp.download("http://downloads.example.org/astap.zip", lambda f: None, tries=1)
    comp._https_only(httpx.Request("GET", "https://downloads.example.org/x"))
    comp._https_only(httpx.Request("GET", "http://127.0.0.1:9/x"))                # eigener Rechner (Tests, Spiegel)


def test_build_prueft_python_pruefsumme():
    import importlib.util
    spec = importlib.util.spec_from_file_location("bw", Path(__file__).resolve().parent.parent / "installer" / "build_windows.py")
    bw = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bw)
    assert len(bw.PBS_SHA256) == 64 and bw.PBS_URL.startswith("https://")


def test_starnet_zip_pfadpruefung(tmp_path):
    from astroflow.modules.stacking import stars
    target = tmp_path / "starnet"
    z = tmp_path / "b.zip"
    with zipfile.ZipFile(z, "w") as f:                      # Nachbarordner mit gleichem Anfang („starnet.neu2“)
        f.writestr("../starnet.neu2/starnet2.exe", b"x")
    with pytest.raises(ValueError, match="unzulässiger Pfad"):
        stars.install_zip(z, target)
    assert not (tmp_path / "starnet.neu2").exists() and not target.exists()


def test_heimnetz_voreinstellung_fuer_serve(tmp_path, monkeypatch):
    """Neue Instanz: nur dieser Rechner. Bestehende Instanz (bis 0.45 lauschte serve immer im Heimnetz): bleibt im Heimnetz,
    jetzt mit Kopplung. `--lan` erzwingt das Heimnetz, eine gespeicherte Wahl gilt."""
    from astroflow import __main__ as cli
    from astroflow import server as server_mod
    import uvicorn
    seen = []
    monkeypatch.setattr(uvicorn, "run", lambda app, host, port: seen.append(host))
    monkeypatch.setattr(server_mod, "create_app", lambda s: None)
    monkeypatch.setenv("ASTROFLOW_PORT", "1")
    monkeypatch.setenv("ASTROFLOW_BIND", "")
    cli.main(["serve", "--data", str(tmp_path / "neu"), "--port", "1"])
    cli.main(["serve", "--data", str(tmp_path / "neu"), "--port", "1", "--lan"])
    old = tmp_path / "alt"
    old.mkdir()
    load_settings(old).db_path.write_bytes(b"")                              # Datenbank vorhanden = bestehende Instanz
    cli.main(["serve", "--data", str(old), "--port", "1"])
    assert seen == ["127.0.0.1", "0.0.0.0", "0.0.0.0"]
    sec = json.loads((old / "settings.json").read_text(encoding="utf-8"))["lan"]
    assert sec["enabled"] is True and sec["migrated"]
    sec_new = json.loads((tmp_path / "neu" / "settings.json").read_text(encoding="utf-8"))["lan"]
    assert sec_new == {"enabled": False}
    cli.main(["serve", "--data", str(old), "--port", "1", "--host", "127.0.0.1"])
    assert seen[-1] == "127.0.0.1"


def test_heimnetz_einstellung_ohne_starter(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch)
    with _local(app) as c:
        s = c.get("/api/setup").json()
        assert s["lan"] is False and s["lan_active"] is False
        r = c.post("/api/setup", json={"lan": True}).json()
        assert r["lan"] is True and not r["restart"]                       # serve/Start.bat: gilt nach dem nächsten Start
        assert c.get("/api/pairing").json()["lan"] is True


def test_loopback_und_vertrauenswuerdige_netze(monkeypatch):
    assert security.is_loopback("127.0.0.1") and security.is_loopback("::1") and security.is_loopback("::ffff:127.0.0.1")
    assert not security.is_loopback("192.0.2.20") and not security.is_loopback("testclient") and not security.is_loopback("")
    monkeypatch.setenv("ASTROFLOW_TRUSTED_NETS", "172.16.0.0/12")              # Docker-Bridge (docker-compose.yml)
    assert security.is_loopback("172.17.0.1") and not security.is_loopback("192.0.2.20")
    assert security.device_name("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) Version/17.0 Mobile Safari/604.1") == "iPhone · Safari"


def test_qr_code_aufbau():
    for text, size in [("AstroFlow", 21), (f"{PC}/api/pair?code=Q2xhdWRlVGVzdENvZGUxMjM0", 33)]:
        m = qr.matrix(text)
        assert len(m) == size and all(len(r) == size for r in m)
        for x0, y0 in ((0, 0), (size - 7, 0), (0, size - 7)):                     # Suchmuster in drei Ecken
            assert all(m[y0][x0 + i] and m[y0 + 6][x0 + i] and m[y0 + i][x0] and m[y0 + i][x0 + 6] for i in range(7))
            assert all(m[y0 + 2 + i][x0 + 2 + j] for i in range(3) for j in range(3))
        bits = sum(m[i][8] << i for i in range(6)) | m[7][8] << 6 | m[8][8] << 7 | m[8][7] << 8 \
            | sum(m[8][14 - i] << i for i in range(9, 15))
        assert ((bits ^ 0x5412) >> 13) == 0                                       # Fehlerkorrekturstufe M
        assert m[size - 8][8]                                                     # festes dunkles Modul
    with pytest.raises(ValueError):
        qr.matrix("x" * 400)
    cv2 = pytest.importorskip("cv2")
    np = pytest.importorskip("numpy")
    for text in ("AstroFlow", f"{PC}/api/pair?code=Q2xhdWRlVGVzdENvZGUxMjM0"):
        img = np.pad((~np.array(qr.matrix(text), dtype=bool)).astype(np.uint8) * 255, 4, constant_values=255)
        img = cv2.resize(img, None, fx=8, fy=8, interpolation=cv2.INTER_NEAREST)
        assert cv2.QRCodeDetector().detectAndDecode(img)[0] == text


# ---- Befunde der Codeanalyse (CodeQL) ----------------------------------------------------------------------------
def test_keine_pfadabfrage_ohne_zweck(tmp_path, monkeypatch):
    """Ungenutzte Endpunkte, die beliebige Pfade prüften bzw. auflisteten, sind weg; der Ordnerdialog (/api/folders) bleibt."""
    monkeypatch.setenv("SKYTRACE_HOME", str(tmp_path / "st"))
    app = _app(tmp_path, monkeypatch, "stacking,skytrace")
    (tmp_path / "quelle" / "M42").mkdir(parents=True)
    with _local(app) as c:
        for path in ("/api/setup/folder", "/api/skytrace/folders"):
            assert c.get(path, params={"path": str(tmp_path)}).status_code in (404, 405), path
        d = c.get("/api/folders", params={"path": str(tmp_path / "quelle")}).json()
        assert [f["name"] for f in d["folders"]] == ["M42"]
        assert c.get("/api/skytrace/observations", params={"source": str(tmp_path / "quelle")}).json()["observations"] == ["M42"]


def test_fehler_ohne_interna(tmp_path, monkeypatch):
    """Ausnahmetexte (Pfade, Adressen, Bibliotheksmeldungen) gehen nicht an den Browser, nur feste deutsche Meldungen."""
    import httpx
    from astroflow.modules.stacking import api as st_api, tutor
    app = _app(tmp_path, monkeypatch)
    gif = io.BytesIO()
    from PIL import Image
    Image.new("RGB", (4, 4)).save(gif, "GIF")
    with _local(app, headers={"Origin": "http://127.0.0.1:8766"}) as c:
        r = c.post("/api/stacking/upload", files=[("files", ("kaputt.jpg", b"kein Bild " * 50, "image/jpeg")),
                                                   ("files", ("animiert.png", gif.getvalue(), "image/png"))])
        assert r.status_code == 200
        errors = r.json()["errors"]
        assert errors[0] == "kaputt.jpg: Bild nicht importiert: Datei ist kein lesbares Bild."
        assert errors[1].endswith("Nur JPG, PNG und TIFF sind als fertige Bilder unterstützt.")
        assert not any("cannot identify" in e or str(tmp_path) in e for e in errors)

        def boom(*a, **k):
            raise RuntimeError(f"geheim {tmp_path} http://192.0.2.99/v1")
        monkeypatch.setattr(tutor, "ask_report", boom)
        r = c.post("/api/stacking/results/1/tutor/ki")
        assert r.status_code == 502 and "geheim" not in r.text and "192.0.2.99" not in r.text and "Protokoll" in r.text
    req = httpx.Request("POST", "http://192.0.2.99/v1/chat")
    assert "Schlüssel" in st_api.ki_error(httpx.HTTPStatusError("x", request=req, response=httpx.Response(401, request=req)))
    assert st_api.ki_error(httpx.ConnectTimeout("x", request=req)) == "KI nicht erreichbar: Zeitüberschreitung."


def test_regex_ohne_quadratische_laufzeit():
    """Markdown-Entfernen (Sage) und Kennungsvergleich bleiben bei bösartigen Eingaben schnell und verhalten sich wie vorher."""
    import time
    from astroflow.modules.sage.kb import norm
    from astroflow.modules.sage.store import plain
    assert norm("M 045") == "m 45" and norm("NGC  0001976") == "ngc 1976" and norm("Messier 0") == "m 0" and norm("m00") == "m 0"
    assert plain("# Titel\n**fett** und *kursiv* [Link](https://example.org) __u__ _i_") == "Titel\nfett und kursiv Link u i"
    t = time.perf_counter()
    norm("m" + "0" * 50000 + "x")
    plain("[" * 30000)
    plain("[](" * 30000)
    plain("*a" * 30000 + "\n" + "_" * 30000)
    assert time.perf_counter() - t < 2
