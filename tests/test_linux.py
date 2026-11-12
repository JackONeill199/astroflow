"""Linux-Unterstützung: XDG-Autostart, eingehängte Datenträger, Firewall-Freigabe (ufw/firewalld per pkexec), GraXpert aus dem
Linux-ZIP, Meldungen ohne Konsole, Engine-Hinweise, Dockerfile-Prüfsummen. Alles mit Attrappen – läuft unter Linux und Windows,
nichts wird echt ausgeführt (kein pkexec, keine Firewall, kein Download)."""
import hashlib
import ipaddress
import re
import stat
import sys
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from astroflow.core import components as comp, firewall as fw, launcher, setup as setup_mod, tray
from astroflow.core.config import load_settings
from astroflow.server import create_app

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def home(tmp_path, monkeypatch):
    for k in ("ASTROFLOW_DATA", "ASTROFLOW_PORT", "ASTROFLOW_LAUNCHER"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("ASTROFLOW_HOME", str(tmp_path / "app"))
    return tmp_path / "app"


# ---- Autostart (XDG) ------------------------------------------------------------------------------------------------
def test_xdg_autostart(home, tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setattr(launcher, "_gui_python", lambda: "/opt/Astro Flow/python/bin/python3")
    assert tray.autostart_supported() and not tray.autostart_enabled()
    tray.set_autostart(True)
    f = tmp_path / "cfg" / "autostart" / "astroflow.desktop"
    text = f.read_text(encoding="utf-8")
    assert tray.autostart_enabled()
    assert text.startswith("[Desktop Entry]\nType=Application\n")
    exec_line = next(l for l in text.splitlines() if l.startswith("Exec="))
    assert exec_line.startswith('Exec="/opt/Astro Flow/python/bin/python3" ')
    assert '"-m" "astroflow" "start" "--no-browser"' in exec_line
    assert f'"--home" "{home}"'.replace("\\", "\\\\\\\\") in exec_line      # portabel: --home, Backslashes maskiert
    tray.set_autostart(False)
    assert not f.exists() and not tray.autostart_enabled()


def test_desktop_quote():
    # Desktop Entry Specification: in "…", " ` $ \ maskiert, danach Backslashes verdoppelt, % verdoppelt
    assert tray._desktop_quote("/usr/bin/x") == '"/usr/bin/x"'
    assert tray._desktop_quote('a"b') == '"a\\\\"b"'
    assert tray._desktop_quote("50%") == '"50%%"'
    assert tray._desktop_quote("$HOME") == '"\\\\$HOME"'


def test_cli_autostart(home, tmp_path, monkeypatch, capsys):
    from astroflow.__main__ import main
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    assert main(["autostart", "on"]) == 0 and "an" in capsys.readouterr().out
    assert (tmp_path / "cfg" / "autostart" / "astroflow.desktop").is_file()
    assert main(["autostart", "off"]) == 0 and "aus" in capsys.readouterr().out


# ---- Datenträger ----------------------------------------------------------------------------------------------------
def test_eingehaengte_datentraeger(tmp_path, monkeypatch):
    media, mnt = tmp_path / "media", tmp_path / "mnt"
    usb, intern, leer = media / "nutzer" / "USB-Platte", mnt / "daten", mnt / "leer"
    for d in (usb, intern, leer):
        d.mkdir(parents=True)
    mounts = {str(usb), str(intern)}
    monkeypatch.setattr(setup_mod.os.path, "ismount", lambda p: str(p) in mounts)
    assert setup_mod._mounts((str(media), str(mnt), str(tmp_path / "fehlt"))) == [str(usb), str(intern)]


# ---- Firewall -------------------------------------------------------------------------------------------------------
IP_ADDR = """2: enp3s0    inet 10.20.30.23/24 brd 10.20.30.255 scope global dynamic enp3s0\\       valid_lft 86000sec
3: docker0    inet 172.17.0.1/16 brd 172.17.255.255 scope global docker0\\       valid_lft forever
4: wg0    inet 100.64.0.2/32 scope global wg0\\       valid_lft forever"""


def test_lokales_subnetz():
    assert fw.local_subnet(IP_ADDR, "10.20.30.23") == ipaddress.IPv4Network("10.20.30.0/24")
    assert fw.local_subnet(IP_ADDR, "172.17.0.1") == ipaddress.IPv4Network("172.17.0.0/16")
    assert fw.local_subnet("1: eth0 inet 203.0.113.5/24 scope global eth0", None) is None      # öffentlich → keine Freigabe
    assert fw.local_subnet("", None) is None


def test_firewall_befehle_fest(monkeypatch):
    monkeypatch.setattr(fw, "_which", lambda n: f"/usr/sbin/{n}")
    net = ipaddress.IPv4Network("192.168.1.0/24")
    assert fw.commands("ufw", net, 8766) == [["/usr/sbin/ufw", "allow", "from", "192.168.1.0/24", "to", "any", "port", "8766",
                                              "proto", "tcp", "comment", "AstroFlow LAN"]]
    assert fw.commands("ufw", net, 8766, remove=True)[0][:3] == ["/usr/sbin/ufw", "delete", "allow"]
    add = fw.commands("firewalld", net, 8766, zone="home")
    rich = 'rule family="ipv4" source address="192.168.1.0/24" port port="8766" protocol="tcp" accept'
    assert add == [["/usr/sbin/firewall-cmd", "--zone=home", "--add-rich-rule", rich],                 # Laufzeit …
                   ["/usr/sbin/firewall-cmd", "--permanent", "--zone=home", "--add-rich-rule", rich]]  # … und dauerhaft, kein --reload
    assert fw.commands("firewalld", net, 8766, remove=True, zone="home")[1][3] == "--remove-rich-rule"
    with pytest.raises(ValueError):
        fw.commands("firewalld", net, 8766, zone="home; rm -rf /")
    for bad in (0, 70000, "8766; rm -rf /"):
        with pytest.raises(ValueError):
            fw.commands("ufw", net, bad)
    with pytest.raises(ValueError):
        fw.commands("ufw", "8.8.8.8/24; x", 8766)
    # ein Passwortdialog: zwei Befehle über /bin/sh -c mit maskierten, festen Teilen
    monkeypatch.setenv("DISPLAY", ":0")
    argv = fw.pkexec_argv(add)
    assert argv[:3] == ["/usr/sbin/pkexec", "/bin/sh", "-c"] and " && " in argv[3]
    assert fw.pkexec_argv(fw.commands("ufw", net, 8766))[0:2] == ["/usr/sbin/pkexec", "/usr/sbin/ufw"]
    monkeypatch.delenv("DISPLAY")
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    assert fw.pkexec_argv(add) is None                     # ohne grafische Sitzung: nur anzeigen


def test_firewalld_zone_der_schnittstelle(monkeypatch):
    assert fw.home_link(IP_ADDR, "10.20.30.23") == (ipaddress.IPv4Network("10.20.30.0/24"), "enp3s0")
    monkeypatch.setattr(fw, "_which", lambda n: f"/usr/bin/{n}")
    asked = []

    def fake(cmd, timeout=10):
        asked.append(cmd[1])
        out = {"--get-zone-of-interface=enp3s0": "home\n", "--get-default-zone": "public\n"}.get(cmd[1], "")
        return type("R", (), {"returncode": 0 if out else 2, "stdout": out})()
    monkeypatch.setattr(fw, "_run", fake)
    assert fw.firewalld_zone("enp3s0") == "home"
    assert fw.firewalld_zone("wlan9") == "public"                        # Schnittstelle in keiner Zone → Standardzone
    monkeypatch.setattr(fw, "_run", lambda cmd, timeout=10: type("R", (), {"returncode": 0, "stdout": "bö$e zone"})())
    assert fw.firewalld_zone("enp3s0") is None


def test_firewall_erkennung(tmp_path, monkeypatch):
    conf = tmp_path / "ufw.conf"
    monkeypatch.setattr(fw, "UFW_CONF", conf)
    monkeypatch.setattr(fw, "_which", lambda n: f"/usr/sbin/{n}" if n == "ufw" else None)
    assert fw.detect() is None
    conf.write_text("# ufw\nENABLED=yes\nLOGLEVEL=low\n", encoding="utf-8")
    assert fw.detect() == "ufw"
    monkeypatch.setattr(fw, "_which", lambda n: f"/usr/bin/{n}")
    monkeypatch.setattr(fw, "_run", lambda cmd, timeout=10: type("R", (), {"returncode": 0, "stdout": "running\n"})())
    assert fw.detect() == "firewalld"


def test_pkexec_ergebnis(monkeypatch):
    for code, ok, text in ((0, True, ""), (126, False, "Abgebrochen"), (1, False, "abgelehnt")):
        monkeypatch.setattr(fw, "_run", lambda cmd, timeout=10, c=code: type("R", (), {"returncode": c, "stdout": "", "stderr": "geheim"})())
        got = fw.execute(["pkexec", "x"])
        assert got[0] is ok and text in got[1] and "geheim" not in got[1]


def _client(tmp_path, monkeypatch):
    return TestClient(create_app(load_settings(tmp_path / "data"), only=("stacking",)), base_url="http://127.0.0.1:8766",
                      client=("127.0.0.1", 50000), headers={"Origin": "http://127.0.0.1:8766"})


def test_firewall_endpunkt_linux(home, tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    monkeypatch.setattr(sys, "platform", "linux")
    calls = []
    net = ipaddress.IPv4Network("192.168.1.0/24")
    monkeypatch.setattr(fw, "detect", lambda: "ufw")
    monkeypatch.setattr(fw, "home_link", lambda: (net, "eth0"))
    monkeypatch.setattr(fw, "_which", lambda n: f"/usr/sbin/{n}")
    monkeypatch.setattr(fw, "execute", lambda argv, timeout=300: calls.append(argv) or (True, ""))
    monkeypatch.setenv("DISPLAY", ":0")
    r = c.post("/api/setup/firewall", json={}).json()
    assert r["ok"] and r["pending"] and "192.168.1.0/24" in r["detail"]
    for _ in range(100):
        t = c.get("/api/setup").json()["tasks"].get("firewall") or {}
        if t.get("state") == "done":
            break
        import time
        time.sleep(0.05)
    assert t["state"] == "done" and "192.168.1.0/24" in t["detail"]
    assert calls[0][:3] == ["/usr/sbin/pkexec", "/usr/sbin/ufw", "allow"]
    # ohne pkexec: Befehl nur anzeigen, nichts ausführen
    monkeypatch.setattr(fw, "_which", lambda n: None if n == "pkexec" else f"/usr/sbin/{n}")
    r = c.post("/api/setup/firewall", json={"remove": True}).json()
    assert r["ok"] and r["manual"].startswith("sudo /usr/sbin/ufw delete allow") and len(calls) == 1
    # keine Firewall aktiv
    monkeypatch.setattr(fw, "detect", lambda: None)
    assert "Keine aktive Firewall" in c.post("/api/setup/firewall", json={}).json()["detail"]


def test_firewall_nur_lokal(home, tmp_path, monkeypatch):
    c = TestClient(create_app(load_settings(tmp_path / "data"), only=("stacking",)), base_url="http://192.168.1.5:8766",
                   client=("192.168.1.7", 50000), headers={"Origin": "http://192.168.1.5:8766"})
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(fw, "plan", lambda *a, **k: pytest.fail("darf nicht laufen"))
    assert c.post("/api/setup/firewall", json={}).status_code in (401, 403)


# ---- GraXpert aus dem Linux-ZIP ---------------------------------------------------------------------------------------
def _graxpert_zip(path: Path, evil: str | None = None) -> bytes:
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("GraXpert-linux/", "")
        prog = zipfile.ZipInfo("GraXpert-linux/GraXpert")
        prog.external_attr = (stat.S_IFREG | 0o755) << 16
        z.writestr(prog, b"#!programm")
        z.writestr("GraXpert-linux/lib/modul.pyc", b"daten")
        if evil:
            z.writestr(evil, b"boese")
    return path.read_bytes()


def test_graxpert_linux_zip(home, tmp_path, monkeypatch):
    blob = _graxpert_zip(tmp_path / "q.zip")
    monkeypatch.setattr(comp, "GRAXPERT_LINUX_SHA256", hashlib.sha256(blob).hexdigest())
    monkeypatch.setattr(comp, "graxpert_exe", lambda: comp.graxpert_dir() / "GraXpert")

    def fake_dl(url, prog, to=None, resume=False):
        assert url == comp.GRAXPERT_LINUX_ZIP and url.startswith("https://") and resume
        Path(to).write_bytes(blob)
    monkeypatch.setattr(comp, "download", fake_dl)
    msg = comp._install_graxpert_zip(lambda f, d: None, False, comp.graxpert_dir())
    exe = comp.graxpert_dir() / "GraXpert"
    assert exe.read_bytes() == b"#!programm" and (comp.graxpert_dir() / "lib" / "modul.pyc").is_file() and "GPL-3.0" in msg
    if sys.platform != "win32":
        assert exe.stat().st_mode & 0o111
    assert comp.records()["graxpert"]["sha256"] == comp.GRAXPERT_LINUX_SHA256
    assert not list(comp.tools_dir().glob("*.part"))
    # falsche Prüfsumme → verworfen, Ordner bleibt wie er war
    monkeypatch.setattr(comp, "GRAXPERT_LINUX_SHA256", "0" * 64)
    with pytest.raises(RuntimeError, match="Prüfsumme"):
        comp._install_graxpert_zip(lambda f, d: None, True, comp.graxpert_dir())
    assert exe.is_file()


def test_graxpert_zip_pfad_ausserhalb(tmp_path):
    z = tmp_path / "boese.zip"
    _graxpert_zip(z, evil="GraXpert-linux/../../ausserhalb.txt")
    with pytest.raises(RuntimeError, match="Unzulässiger Pfad"):
        comp._extract_tree(z, tmp_path / "ziel" / "stage")
    assert not (tmp_path / "ausserhalb.txt").exists() and not (tmp_path / "ziel" / "ausserhalb.txt").exists()


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Installationsweg je Betriebssystem")
def test_graxpert_linux_ueber_komponente(home, tmp_path, monkeypatch):
    blob = _graxpert_zip(tmp_path / "q.zip")
    monkeypatch.setattr(comp, "GRAXPERT_LINUX_SHA256", hashlib.sha256(blob).hexdigest())
    monkeypatch.setattr(comp, "system", lambda: ("Linux", "x86_64"))
    monkeypatch.setattr(comp, "download", lambda url, prog, to=None, resume=False: Path(to).write_bytes(blob))
    from astroflow.modules.stacking import graxpert as gx
    monkeypatch.setattr(gx, "system_graxpert", lambda: None)
    assert "GPL-3.0" in comp.install("graxpert")
    monkeypatch.delenv("ASTROFLOW_GRAXPERT")
    assert gx.graxpert_path() == str(comp.graxpert_dir() / "GraXpert")
    st = next(c for c in comp.status() if c["id"] == "graxpert")
    assert st["state"] == "installed" and st["supported"]


def test_graxpert_unterstuetzt(monkeypatch):
    monkeypatch.setattr(comp, "system", lambda: ("Linux", "x86_64"))
    assert comp.graxpert_supported()
    if sys.platform != "win32":
        monkeypatch.setattr(comp, "system", lambda: ("Linux", "aarch64"))
        assert not comp.graxpert_supported()                # kein offizielles ARM-Paket
        monkeypatch.setattr(comp, "system", lambda: ("Darwin", "arm64"))
        assert not comp.graxpert_supported()


# ---- Meldungen, Engine, Paketordner, Dockerfile -----------------------------------------------------------------------
def test_meldung_auf_dem_desktop(monkeypatch):
    seen = []
    monkeypatch.setattr("shutil.which", lambda n: f"/usr/bin/{n}" if n == "notify-send" else None)
    monkeypatch.setattr(launcher.subprocess, "run", lambda cmd, **kw: seen.append(cmd))
    assert launcher._desktop_message("Port belegt", error=True)
    assert seen[0][0] == "notify-send" and "critical" in seen[0] and seen[0][-1] == "Port belegt"
    monkeypatch.setattr("shutil.which", lambda n: None)
    assert not launcher._desktop_message("x", error=False)


def test_engine_libgl_hinweis(monkeypatch):
    err = "ImportError: libGL.so.1: cannot open shared object file: No such file or directory"
    monkeypatch.setattr(comp.subprocess, "run", lambda *a, **k: type("R", (), {"returncode": 1, "stderr": err, "stdout": ""})())
    assert comp._engine_import_test() == comp.LIBGL_MISSING


def test_paketordner_linux_tag(tmp_path, monkeypatch):
    import importlib.util
    spec = importlib.util.spec_from_file_location("astroflow_pakete_linux", ROOT / "installer" / "astroflow_pakete.py")
    mod = importlib.util.module_from_spec(spec)
    before = list(sys.path)
    monkeypatch.delenv("ASTROFLOW_HOME", raising=False)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "share"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.path[:] = before
    if sys.platform == "win32":
        assert mod.DIR.startswith(str(tmp_path / "local" / "AstroFlow" / "Pakete"))
    else:
        import platform
        assert mod.DIR == str(tmp_path / "share" / "astroflow" / "packages" /
                              f"cp{sys.version_info[0]}{sys.version_info[1]}-{sys.platform.rstrip('0123456789')}_{platform.machine()}".lower())


def test_dockerfile_prueft_astap():
    text = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    shas = dict(re.findall(r"ARG (ASTAP_\w+_SHA256)=([0-9a-f]{64})", text))
    assert shas["ASTAP_CLI_SHA256"] in comp.ASTAP_SHA256["linux_installer/astap_command-line_version_Linux_amd64.zip"]
    assert shas["ASTAP_D05_SHA256"] in comp.ASTAP_SHA256["star_databases/d05_star_database.zip"]
    assert shas["ASTAP_D50_SHA256"] in comp.ASTAP_SHA256["star_databases/d50_star_database.zip"]
    assert text.count("sha256sum -c") == 2 and "http://" not in text


def test_kein_windows_only_subprocess_flag():
    """`subprocess.CREATE_NO_WINDOW` gibt es nur unter Windows – immer über getattr(…, 0) bzw. eine Konstante."""
    bad = [f"{p.relative_to(ROOT)}:{i}" for p in (ROOT / "astroflow").rglob("*.py")
           for i, line in enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines(), 1)
           if re.search(r"(?<!['\"])\bsubprocess\.CREATE_NO_WINDOW\b", line) and "getattr" not in line]
    assert not bad, bad
