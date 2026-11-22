"""„Jetzt aktualisieren“ (core/updater.py, Wunsch 23): Download nur https von GitHub, Weiterleitungen geprüft, SHA-256 aus dem Release,
Abbruch/Fehler ohne Reste. Netz nur über httpx.MockTransport, der Start des Installers bzw. von install.sh ist immer eine Attrappe –
nie wird ein echtes oder kopiertes Programm ausgeführt."""
import hashlib
import io
import tarfile
import threading
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from astroflow.core import setup as setup_mod, updater
from astroflow.core.config import load_settings
from astroflow.server import create_app

REL = "https://github.com/x/y/releases/download/v99.0.0/"
CDN = "https://objects.githubusercontent.com/github-production-release-asset/1/abc"
EXE = b"MZ-Attrappe-des-Installers" * 4000              # ~100 KB, mehrere Blöcke
EXE_NAME = "AstroFlow-Setup-99.0.0.exe"


def _assets(sha=True, linux=False):
    out = [{"name": EXE_NAME, "browser_download_url": REL + EXE_NAME, "size": len(EXE)}]
    if sha:
        out.append({"name": EXE_NAME + ".sha256", "browser_download_url": REL + EXE_NAME + ".sha256"})
    if linux:
        n = "AstroFlow-99.0.0-Linux-x86_64.tar.gz"
        out += [{"name": n, "browser_download_url": REL + n}, {"name": n + ".sha256", "browser_download_url": REL + n + ".sha256"}]
    return out


class Net:
    """Attrappe für GitHub: Release-Dateien unter github.com leiten wie echt auf den CDN-Host weiter."""

    def __init__(self, files: dict[str, bytes], redirect_to: str = CDN):
        self.files, self.redirect_to, self.seen = files, redirect_to, []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.seen.append(url)
        if url.startswith(REL):
            name = url[len(REL):]
            if name not in self.files:
                return httpx.Response(404)
            return httpx.Response(302, headers={"location": f"{self.redirect_to}?f={name}"})
        if url.startswith(self.redirect_to):
            name = url.split("?f=", 1)[1]
            return httpx.Response(200, content=self.files[name])
        return httpx.Response(404)


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Installierte Windows-Fassung (Starter, kein ASTROFLOW_HOME), Ablage im Testordner, Start nur als Attrappe."""
    for k in ("ASTROFLOW_DATA", "ASTROFLOW_PORT", "ASTROFLOW_HOME"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("ASTROFLOW_LAUNCHER", "1")
    monkeypatch.setenv("ASTROFLOW_UPDATE_DIR", str(tmp_path / "upd"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setattr(setup_mod, "VERSION", "0.1.0")
    monkeypatch.setattr(setup_mod, "install_kind", lambda: "windows")
    started = []
    monkeypatch.setattr(updater, "launch_windows", lambda exe: started.append(("windows", exe, exe.read_bytes())))
    monkeypatch.setattr(updater, "launch_linux",
                        lambda root, prefix, bindir: started.append(("linux", root, (root / "install.sh").read_bytes(), prefix, bindir)))
    return {"tmp": tmp_path, "started": started}


def _client(env, monkeypatch, assets, files):
    class R:
        status_code = 200

        def json(self):
            return {"tag_name": "v99.0.0", "html_url": "https://github.com/x/y/releases/tag/v99.0.0", "assets": assets}
    monkeypatch.setattr(httpx, "get", lambda url, **kw: R())
    net = Net(files)
    monkeypatch.setattr(updater, "TRANSPORT", httpx.MockTransport(net))
    c = TestClient(create_app(load_settings(env["tmp"] / "data"), only=("stacking",)))
    return c, net


def _wait(c, until=("done", "error", "cancelled"), timeout=15):
    end = time.time() + timeout
    while time.time() < end:
        t = c.get("/api/system/update/install").json()["task"]
        if t and t["state"] in until:
            return t
        time.sleep(0.05)
    raise AssertionError("Update-Auftrag endet nicht")


def _sha(data: bytes, name: str) -> bytes:
    return f"{hashlib.sha256(data).hexdigest()}  {name}\n".encode()


def test_adressen_und_pruefsummen_format():
    assert updater.allowed(REL + EXE_NAME) and updater.allowed(CDN)
    assert updater.allowed("https://release-assets.githubusercontent.com/a")
    for bad in ("http://github.com/x", "https://github.com.boese.example/x", "https://boese.example/x",
                "https://user:pw@github.com/x", "https://github.com:8443/x", "ftp://github.com/x", ""):
        assert not updater.allowed(bad), bad
    h = "a" * 64
    assert updater.parse_sha256(f"{h}  datei.exe\n", "datei.exe") == h
    assert updater.parse_sha256(f"{h.upper()} *datei.exe", "datei.exe") == h
    assert updater.parse_sha256(h, "datei.exe") == h
    assert updater.parse_sha256(f"{h}  andere.exe", "datei.exe") is None
    assert updater.parse_sha256("kaputt", "datei.exe") is None
    for bad in ("../boese.exe", "a/b.exe", "a\\b.exe", ""):
        with pytest.raises(updater.UpdateError):
            updater.safe_name(bad)


def test_release_mit_pruefsumme_bietet_automatik(env, monkeypatch):
    c, _ = _client(env, monkeypatch, _assets(), {})
    up = c.post("/api/system/update/check").json()["update"]
    assert up["download"]["sha256_url"] == REL + EXE_NAME + ".sha256" and up["auto"] is True
    monkeypatch.setenv("ASTROFLOW_HOME", str(env["tmp"] / "portabel"))          # portable Fassung → nur Download
    up = c.get("/api/app").json()["update"]
    assert up["auto"] is False and up["manual_reason"] == "portable"


def test_windows_update_laedt_prueft_und_startet(env, monkeypatch):
    c, net = _client(env, monkeypatch, _assets(), {EXE_NAME: EXE, EXE_NAME + ".sha256": _sha(EXE, EXE_NAME)})
    r = c.post("/api/system/update/install")
    assert r.status_code == 200 and r.json()["task"]["state"] == "running"
    t = _wait(c)
    assert t["state"] == "done" and "Installer gestartet" in t["detail"], t
    kind, exe, data = env["started"][0]
    assert kind == "windows" and exe.name == EXE_NAME and data == EXE
    assert all(u.startswith(("https://github.com/", "https://objects.githubusercontent.com/")) for u in net.seen)
    act = [a for a in c.get("/api/activity").json() if a["key"] == "setup:update"]
    assert act and act[0]["state"] == "done" and act[0]["title"] == "Update auf 99.0.0"


def test_falsche_pruefsumme_startet_nichts_und_raeumt_auf(env, monkeypatch):
    c, _ = _client(env, monkeypatch, _assets(), {EXE_NAME: EXE, EXE_NAME + ".sha256": _sha(b"anders", EXE_NAME)})
    c.post("/api/system/update/install")
    t = _wait(c)
    assert t["state"] == "error" and "Prüfsumme stimmt nicht" in t["detail"]
    assert env["started"] == [] and not (env["tmp"] / "upd").exists()


def test_ohne_sha256_kein_automatismus(env, monkeypatch):
    c, net = _client(env, monkeypatch, _assets(sha=False), {EXE_NAME: EXE})
    up = c.post("/api/system/update/check").json()["update"]
    assert up["auto"] is False and up["manual_reason"] == "nosha" and up["download"]["url"].endswith(EXE_NAME)
    r = c.post("/api/system/update/install")
    assert r.status_code == 409 and "keine Prüfsumme" in r.json()["detail"]
    assert env["started"] == [] and net.seen == []


def test_weiterleitung_auf_fremden_host(env, monkeypatch):
    c, net = _client(env, monkeypatch, _assets(), {EXE_NAME: EXE, EXE_NAME + ".sha256": _sha(EXE, EXE_NAME)})
    net.redirect_to = "https://boese.example/datei"
    c.post("/api/system/update/install")
    t = _wait(c)
    assert t["state"] == "error" and "Unerwartete Download-Adresse" in t["detail"]
    assert env["started"] == [] and not any("boese.example" in u for u in net.seen)        # fremder Host nie angefragt
    net.redirect_to = "http://objects.githubusercontent.com/x"                             # erlaubter Host, aber ohne https
    with pytest.raises(updater.UpdateError):
        updater.fetch(REL + EXE_NAME, None)


def test_fremder_host_in_der_release_liste(env, monkeypatch):
    assets = [{"name": EXE_NAME, "browser_download_url": "https://boese.example/" + EXE_NAME},
              {"name": EXE_NAME + ".sha256", "browser_download_url": "https://boese.example/" + EXE_NAME + ".sha256"}]
    c, net = _client(env, monkeypatch, assets, {})
    assert c.post("/api/system/update/check").json()["update"]["download"] is None      # fällt schon in release_assets weg
    assert c.post("/api/system/update/install").status_code == 409 and net.seen == []
    with pytest.raises(updater.UpdateError, match="Unerwartete Download-Adresse"):
        updater.fetch("https://boese.example/x", None)


def test_abbruch_raeumt_auf(env, monkeypatch):
    gate = threading.Event()

    class Slow(httpx.SyncByteStream):
        def __iter__(self):
            yield EXE[:1000]
            gate.wait(10)
            for i in range(1000, len(EXE), 1000):
                yield EXE[i:i + 1000]
    c, net = _client(env, monkeypatch, _assets(), {EXE_NAME + ".sha256": _sha(EXE, EXE_NAME)})

    def handler(request):
        url = str(request.url)
        if url == REL + EXE_NAME:
            return httpx.Response(200, headers={"content-length": str(len(EXE))}, stream=Slow())
        return net(request)
    monkeypatch.setattr(updater, "TRANSPORT", httpx.MockTransport(handler))
    c.post("/api/system/update/install")
    end = time.time() + 10
    while not (env["tmp"] / "upd" / EXE_NAME).exists() and time.time() < end:
        time.sleep(0.02)
    assert c.post("/api/system/update/cancel").json()["task"]["detail"] == "Wird abgebrochen …"
    gate.set()
    t = _wait(c)
    assert t["state"] == "cancelled" and "nichts wurde geändert" in t["detail"]
    assert env["started"] == [] and not (env["tmp"] / "upd").exists()
    act = [a for a in c.get("/api/activity").json() if a["key"] == "setup:update"][0]
    assert act["state"] == "done"                                                     # Abbruch ist kein Fehler


def _linux_pkg(evil: bool = False) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as t:
        for name, data in [("AstroFlow/install.sh", b"#!/bin/sh\necho attrappe\n"), ("AstroFlow/python/bin/python3", b"attrappe")]:
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = 0o755
            t.addfile(info, io.BytesIO(data))
        if evil:
            info = tarfile.TarInfo("../ausserhalb.txt")
            info.size = 1
            t.addfile(info, io.BytesIO(b"x"))
    return buf.getvalue()


@pytest.mark.parametrize("evil", [False, True])
def test_linux_update_entpackt_und_startet_install_sh(env, monkeypatch, evil):
    monkeypatch.setattr(setup_mod, "install_kind", lambda: "linux")
    where = (env["tmp"] / "eigenes" / "app", env["tmp"] / "eigenes" / "bin")      # eigene --prefix/--bin werden durchgereicht
    monkeypatch.setattr(updater, "linux_target", lambda: where)
    n = "AstroFlow-99.0.0-Linux-x86_64.tar.gz"
    pkg = _linux_pkg(evil)
    c, _ = _client(env, monkeypatch, _assets(linux=True), {n: pkg, n + ".sha256": _sha(pkg, n)})
    c.post("/api/system/update/install")
    t = _wait(c)
    if evil:                                                    # Pfad außerhalb des Zielordners → tarfile-Filter verweigert
        assert t["state"] == "error" and env["started"] == [] and not (env["tmp"] / "upd").exists()
        assert not (env["tmp"] / "ausserhalb.txt").exists()
        return
    assert t["state"] == "done" and "Installation läuft" in t["detail"], t
    kind, root, script, prefix, bindir = env["started"][0]
    assert kind == "linux" and root.name == "AstroFlow" and script.startswith(b"#!/bin/sh") and (prefix, bindir) == where
    assert not (root.parent.parent / n).exists()                # Archiv nach dem Entpacken gelöscht


def test_quellcode_ohne_automatismus(env, monkeypatch):
    monkeypatch.setattr(setup_mod, "install_kind", lambda: "source")
    c, net = _client(env, monkeypatch, _assets(), {})
    up = c.post("/api/system/update/check").json()["update"]
    assert up["auto"] is False and up["manual_reason"] == "source"
    r = c.post("/api/system/update/install")
    assert r.status_code == 409 and "Quellcode" in r.json()["detail"] and net.seen == []


def test_reste_beim_start_aufgeraeumt(env, monkeypatch):
    old = env["tmp"] / "upd"
    old.mkdir()
    (old / EXE_NAME).write_bytes(b"alt")
    _client(env, monkeypatch, _assets(), {})
    assert not old.exists()


def test_linux_installation_unbekannt_ohne_automatismus(env, monkeypatch):
    monkeypatch.setattr(setup_mod, "install_kind", lambda: "linux")
    monkeypatch.setattr(updater, "linux_target", lambda: None)
    c, net = _client(env, monkeypatch, _assets(linux=True), {})
    up = c.post("/api/system/update/check").json()["update"]
    assert up["auto"] is False and up["manual_reason"] == "unknown"
    assert c.post("/api/system/update/install").status_code == 409 and net.seen == []


def test_linux_ziel_aus_laufender_installation(tmp_path, monkeypatch):
    prefix = (tmp_path / "eigen app").resolve()
    py = prefix / "python" / "bin" / "python3"
    py.parent.mkdir(parents=True)
    py.write_bytes(b"")
    monkeypatch.setattr(updater.sys, "executable", str(py))
    monkeypatch.setattr(updater.Path, "home", lambda: tmp_path / "home")
    bindir = tmp_path / "werkzeug"
    bindir.mkdir()
    wrapper = bindir / "astroflow"
    wrapper.write_text(f"#!/bin/sh\nexec '{py}' -I -m astroflow \"$@\"\n", encoding="utf-8")
    monkeypatch.setattr(updater.shutil, "which", lambda name: str(wrapper))
    assert updater.linux_target() is None                                  # ohne Markierung (portabel/fremd) → kein Automatismus
    (prefix / updater.LINUX_MARKER).write_text("x", encoding="utf-8")
    assert updater.linux_target() == (prefix, bindir)
    wrapper.write_text("#!/bin/sh\nexec /anderswo/python3 -I -m astroflow\n", encoding="utf-8")
    assert updater.linux_target() is None                                  # Befehl gehört zu einer anderen Installation
