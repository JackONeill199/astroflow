"""Linux-Paket: Installation pro Nutzer (installer/linux_install.py), Skripte im Paket, Build-Konstanten. Ohne echtes Paket:
ein nachgebauter Paketordner im Testordner, Unterprozesse (astroflow stop/start, Desktop-Datenbank) durch Attrappen ersetzt."""
import importlib.util
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
INST = ROOT / "installer"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(f"{name}_test", INST / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def li(tmp_path, monkeypatch):
    mod = _load("linux_install")
    src = tmp_path / "paket" / "AstroFlow"
    (src / "python" / "bin").mkdir(parents=True)
    (src / "python" / "bin" / "python3").write_text("#!/bin/sh\n", encoding="utf-8")
    (src / "astroflow.png").write_bytes(b"\x89PNG")
    (src / "uninstall.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    (src / "Daten").mkdir()                                            # Rest einer portablen Nutzung
    monkeypatch.setattr(mod, "SRC", src)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "home" / ".local" / "share"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "home" / ".config"))
    calls = []
    monkeypatch.setattr(mod.subprocess, "run", lambda cmd, **kw: calls.append(list(cmd)))
    monkeypatch.setattr(mod.subprocess, "Popen", lambda cmd, **kw: calls.append(["Popen", *cmd]))
    monkeypatch.setattr(mod.shutil, "which", lambda n: None)
    mod.calls = calls
    return mod


def test_installieren_und_entfernen(li, tmp_path, monkeypatch):
    prefix, bindir = tmp_path / "home" / ".local" / "share" / "astroflow" / "app", tmp_path / "home" / ".local" / "bin"
    assert li.install(prefix, bindir, autostart=True) == 0
    assert (prefix / "python" / "bin" / "python3").is_file() and not (prefix / "Daten").exists()
    wrapper = (bindir / "astroflow").read_text(encoding="utf-8")
    assert wrapper.startswith("#!/bin/sh\n") and "-I -m astroflow \"$@\"" in wrapper and str(prefix / "python" / "bin" / "python3") in wrapper
    desk = li.desktop_file().read_text(encoding="utf-8")
    assert desk.startswith("[Desktop Entry]\n") and "Actions=beenden;" in desk and "Name=AstroFlow beenden" in desk
    assert re.search(r'^Exec=".*astroflow" start$', desk, re.M) and re.search(r'^Exec=".*astroflow" stop$', desk, re.M)
    assert li.icon_file().read_bytes() == b"\x89PNG"
    assert [str(bindir / "astroflow"), "autostart", "on"] in li.calls                     # über den Befehl, wie in den Einstellungen
    assert li.calls[-1][:2] == ["Popen", str(bindir / "astroflow")]                         # danach gestartet
    if sys.platform != "win32":
        assert (bindir / "astroflow").stat().st_mode & 0o111

    # Update: Programmordner ersetzt, laufender Server vorher beendet
    (prefix / "alt.txt").write_text("x", encoding="utf-8")
    li.calls.clear()
    assert li.install(prefix, bindir, start=False) == 0
    assert not (prefix / "alt.txt").exists() and [str(bindir / "astroflow"), "stop"] in li.calls
    assert not any(c[0] == "Popen" for c in li.calls)

    # Entfernen: Daten bleiben
    data = tmp_path / "home" / "AstroFlow"
    data.mkdir()
    from astroflow.core import tray
    monkeypatch.setattr(tray, "set_autostart", lambda on: None)
    assert li.uninstall(prefix, bindir) == 0
    for f in (prefix, bindir / "astroflow", bindir / "astroflow-deinstallieren", li.desktop_file(), li.icon_file()):
        assert not f.exists(), f
    assert data.is_dir()


def test_nicht_aus_dem_programmordner(li, tmp_path):
    assert li.install(li.SRC, tmp_path / "bin") == 1                       # install.sh im installierten Ordner: nichts überschreiben


def test_fremden_befehl_nicht_loeschen(li, tmp_path, monkeypatch):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "astroflow").write_text("#!/bin/sh\necho anderes Programm\n", encoding="utf-8")
    from astroflow.core import tray
    monkeypatch.setattr(tray, "set_autostart", lambda on: None)
    li.uninstall(tmp_path / "app", bindir)
    assert (bindir / "astroflow").is_file()


def test_paketskripte():
    for name in ("install.sh", "uninstall.sh", "AstroFlow", "AstroFlow-beenden"):
        text = (INST / "linux" / name).read_bytes().replace(b"\r\n", b"\n").decode("utf-8")
        assert text.startswith("#!/bin/sh\n"), name
        assert '"$DIR/python/bin/python3" -I' in text, name                # immer das mitgelieferte Python, isoliert
    assert 'ASTROFLOW_HOME="$DIR"' in (INST / "linux" / "AstroFlow").read_text(encoding="utf-8")
    assert "ASTROFLOW_HOME" not in (INST / "linux" / "install.sh").read_text(encoding="utf-8")


def test_build_konstanten():
    text = (INST / "build_linux.py").read_text(encoding="utf-8")
    sha = re.search(r'PBS_SHA256 = "([0-9a-f]{64})"', text)
    assert sha and "unknown-linux-gnu-install_only_stripped" in text and "https://github.com/astral-sh/" in text
    wf = (ROOT / ".github" / "workflows" / "linux-paket.yml").read_text(encoding="utf-8")
    assert "workflow_dispatch" in wf and "push:" not in wf and "contents: read" in wf    # nie bei jedem Push, nur der Release-Job darf schreiben
    assert wf.count("contents: write") == 1 and "--clobber" in wf and "--draft" in wf
