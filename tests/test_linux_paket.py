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
    mod.stop_code = 0

    def fake_run(cmd, **kw):
        calls.append(list(cmd))
        return type("R", (), {"returncode": mod.stop_code if cmd[-1:] == ["stop"] else 0})()
    monkeypatch.setattr(mod.subprocess, "run", fake_run)
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


def test_server_laesst_sich_nicht_beenden(li, tmp_path, monkeypatch):
    """`astroflow stop` meldet Fehler → weder Update noch Deinstallation fassen das laufende Programm an."""
    prefix, bindir = tmp_path / "app", tmp_path / "bin"
    assert li.install(prefix, bindir, start=False) == 0
    (prefix / "alt.txt").write_text("x", encoding="utf-8")
    li.stop_code = 1
    assert li.install(prefix, bindir, start=False) == 1 and (prefix / "alt.txt").is_file()
    assert li.uninstall(prefix, bindir) == 1 and prefix.is_dir() and (bindir / "astroflow").is_file()


def test_deinstallieren_kennt_eigenen_befehlsordner(li, tmp_path):
    bindir = tmp_path / "eigener-bin"
    assert li.install(tmp_path / "app", bindir, start=False) == 0
    text = (bindir / "astroflow-deinstallieren").read_text(encoding="utf-8")
    assert "uninstall.sh" in text and f"--bin {li._sh(str(bindir))}" in text


def test_fremder_prefix_bleibt_unberuehrt(li, tmp_path):
    """Versehentlich `--prefix ~/Dokumente`: weder Installation noch Deinstallation ersetzen oder löschen fremde Ordner."""
    docs = tmp_path / "Dokumente"
    docs.mkdir()
    (docs / "brief.txt").write_text("wichtig", encoding="utf-8")
    bindir = tmp_path / "bin"
    assert li.install(docs, bindir, start=False) == 1
    assert li.uninstall(docs, bindir) == 1
    assert (docs / "brief.txt").read_text(encoding="utf-8") == "wichtig" and sorted(p.name for p in docs.iterdir()) == ["brief.txt"]
    assert not (bindir / "astroflow").exists() and not any(c[-1:] == ["stop"] for c in li.calls)      # nichts gestartet/beendet
    # fremder Ordner neben dem Ziel („.alt“) blockiert ebenfalls
    (tmp_path / "app.alt").mkdir()
    (tmp_path / "app.alt" / "x.txt").write_text("x", encoding="utf-8")
    assert li.install(tmp_path / "app", bindir, start=False) == 1 and (tmp_path / "app.alt" / "x.txt").is_file()
    # leerer Ordner ist in Ordnung, danach trägt er die Marke und darf ersetzt/gelöscht werden
    empty = tmp_path / "leer"
    empty.mkdir()
    assert li.install(empty, bindir, start=False) == 0 and (empty / li.MARKER).is_file()
    assert li.install(empty, bindir, start=False) == 0
    from astroflow.core import tray
    tray_set = tray.set_autostart
    try:
        tray.set_autostart = lambda on: None
        assert li.uninstall(empty, bindir) == 0 and not empty.exists()
    finally:
        tray.set_autostart = tray_set


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
