"""Linux-Paket: Installation pro Nutzer (ohne root), Update und Deinstallation. Läuft mit dem mitgelieferten Python des Pakets
(`install.sh` / `uninstall.sh` rufen es auf) und nutzt dieselben Pfade wie AstroFlow selbst (XDG):

    Programm        ~/.local/share/astroflow/app          (Update: wird ersetzt)
    Befehl          ~/.local/bin/astroflow                (kleines sh-Skript → mitgeliefertes Python)
    Menüeintrag     ~/.local/share/applications/astroflow.desktop   (Rechtsklick-Aktion „AstroFlow beenden“)
    Symbol          ~/.local/share/icons/hicolor/256x256/apps/astroflow.png
    Autostart       ~/.config/autostart/astroflow.desktop  (optional, wie Einstellungen/Infobereich)
    Daten           ~/AstroFlow  (bleiben beim Deinstallieren), Einstellungen ~/.config/astroflow
    Zusatzpakete    ~/.local/share/astroflow/packages      (GPU, SkyTrace-Engine – überstehen Updates)
    Werkzeuge       ~/.local/share/astroflow/tools, ~/astap (GraXpert, ASTAP – nur mit --werkzeuge entfernt)

    python3 -I linux_install.py install [--prefix DIR] [--bin DIR] [--autostart] [--no-desktop] [--no-start]
    python3 -I linux_install.py uninstall [--werkzeuge]
Nur Standardbibliothek + das mitinstallierte Paket `astroflow` (für Autostart/Pfade).
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

SRC = Path(__file__).resolve().parent                      # entpacktes Paket (enthält python/, astroflow.png …)


def data_home() -> Path:
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")


def default_prefix() -> Path:
    return data_home() / "astroflow" / "app"


def default_bin() -> Path:
    return Path.home() / ".local" / "bin"


def desktop_file() -> Path:
    return data_home() / "applications" / "astroflow.desktop"


def icon_file() -> Path:
    return data_home() / "icons" / "hicolor" / "256x256" / "apps" / "astroflow.png"


def _q(arg: str) -> str:
    from astroflow.core.tray import _desktop_quote
    return _desktop_quote(arg)


def wrapper_text(prefix: Path) -> str:
    py = prefix / "python" / "bin" / "python3"
    return ("#!/bin/sh\n# AstroFlow – Befehl `astroflow` (start | stop | autostart on|off | components …). Erzeugt von install.sh.\n"
            f"exec {_sh(str(py))} -I -m astroflow \"$@\"\n")


def _sh(s: str) -> str:
    import shlex
    return shlex.quote(s)


def desktop_text(wrapper: Path) -> str:
    w = _q(str(wrapper))
    return ("[Desktop Entry]\nType=Application\nName=AstroFlow\nGenericName=Astrofotografie\n"
            "Comment=Rohbilder automatisch zum fertigen Astrofoto – läuft im Browser\n"
            f"Exec={w} start\nIcon=astroflow\nTerminal=false\nCategories=Science;Astronomy;Graphics;\n"
            "Keywords=Astronomie;Teleskop;Stacking;FITS;Seestar;Vespera;Stellina;\nStartupNotify=false\nActions=beenden;\n\n"
            f"[Desktop Action beenden]\nName=AstroFlow beenden\nExec={w} stop\n")


def _log(*a):
    print("[AstroFlow]", *a, flush=True)


def _running_stop(wrapper: Path) -> bool:
    """Laufenden Server (und Symbol) beenden, bevor Dateien ersetzt werden. False, wenn er sich nicht beenden ließ
    (`astroflow stop` meldet dann 1) – dann nichts ersetzen oder löschen, sonst liefe er ohne sein Python weiter."""
    if not wrapper.is_file():
        return True
    try:
        r = subprocess.run([str(wrapper), "stop"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=90, check=False)
    except (OSError, subprocess.SubprocessError):
        return False
    return r.returncode == 0


STOP_FAILED = ("AstroFlow läuft noch und ließ sich nicht beenden – abgebrochen, nichts verändert. Bitte AstroFlow beenden "
               "(Einstellungen → Module & System → AstroFlow beenden) und erneut versuchen.")


def install(prefix: Path, bindir: Path, autostart: bool = False, desktop: bool = True, start: bool = True) -> int:
    if not (SRC / "python" / "bin" / "python3").is_file():
        _log("Fehler: install.sh bitte im entpackten AstroFlow-Ordner ausführen (python/ fehlt).")
        return 1
    if SRC.resolve() == prefix.resolve():
        _log("Ist bereits installiert – zum Aktualisieren install.sh aus dem neu entpackten Paket starten.")
        return 1
    wrapper = bindir / "astroflow"
    update = prefix.exists()
    if not _running_stop(wrapper):
        _log(STOP_FAILED)
        return 1
    _log("Aktualisiere" if update else "Installiere", "nach", prefix)
    stage = prefix.with_name(prefix.name + ".neu")
    shutil.rmtree(stage, ignore_errors=True)
    shutil.copytree(SRC, stage, symlinks=True)
    for portable in ("Daten", "Pakete", "Werkzeuge", "config.json"):     # Reste einer portablen Nutzung nicht mitnehmen
        p = stage / portable
        if p.is_dir():
            shutil.rmtree(p, ignore_errors=True)
        elif p.exists():
            p.unlink()
    old = prefix.with_name(prefix.name + ".alt")
    shutil.rmtree(old, ignore_errors=True)
    if prefix.exists():
        prefix.rename(old)
    stage.rename(prefix)
    shutil.rmtree(old, ignore_errors=True)

    bindir.mkdir(parents=True, exist_ok=True)
    tmp = wrapper.with_suffix(".tmp")
    tmp.write_text(wrapper_text(prefix), encoding="utf-8")
    tmp.chmod(0o755)
    tmp.replace(wrapper)
    un = bindir / "astroflow-deinstallieren"
    un.write_text("#!/bin/sh\n# AstroFlow entfernen (Daten bleiben; --werkzeuge entfernt auch ASTAP, GraXpert, Zusatzpakete)\n"
                  f"exec {_sh(str(prefix / 'uninstall.sh'))} --bin {_sh(str(bindir))} \"$@\"\n", encoding="utf-8")
    un.chmod(0o755)
    if desktop:
        icon_file().parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(prefix / "astroflow.png", icon_file())
        desktop_file().parent.mkdir(parents=True, exist_ok=True)
        desktop_file().write_text(desktop_text(wrapper), encoding="utf-8")
        for cmd in (["update-desktop-database", str(desktop_file().parent)],
                    ["gtk-update-icon-cache", "-q", "-t", str(data_home() / "icons" / "hicolor")]):
            if shutil.which(cmd[0]):
                subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    if autostart:
        subprocess.run([str(wrapper), "autostart", "on"], stdin=subprocess.DEVNULL, check=False)
    _log("Fertig." + (" Einstellungen, Daten und Zusatzpakete sind geblieben." if update else ""))
    _log("Starten: Anwendungsmenü → AstroFlow, oder im Terminal: astroflow")
    if str(bindir) not in os.environ.get("PATH", "").split(os.pathsep):
        _log(f"Hinweis: {bindir} steht nicht im PATH – im Terminal dann {wrapper} statt astroflow (nach Ab-/Anmelden meist erledigt).")
    _log("Deinstallieren: astroflow-deinstallieren bzw.", prefix / "uninstall.sh")
    if start:
        subprocess.Popen([str(wrapper), "start"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
    return 0


def uninstall(prefix: Path, bindir: Path, tools: bool = False) -> int:
    wrapper = bindir / "astroflow"
    if not _running_stop(wrapper):
        _log(STOP_FAILED)
        return 1
    if tools:                                          # nur, was AstroFlow selbst installiert hat (komponenten.json)
        py = prefix / "python" / "bin" / "python3"
        if py.is_file():
            for name in ("graxpert", "astap"):
                subprocess.run([str(py), "-I", "-m", "astroflow", "components", "remove", name], stdin=subprocess.DEVNULL, check=False)
        for d in (data_home() / "astroflow" / "packages", data_home() / "astroflow" / "tools"):
            shutil.rmtree(d, ignore_errors=True)
    try:
        from astroflow.core import tray
        tray.set_autostart(False)
    except Exception:
        (Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "autostart" / "astroflow.desktop").unlink(missing_ok=True)
    for f in (desktop_file(), icon_file()):
        f.unlink(missing_ok=True)
    if wrapper.is_file() and "AstroFlow" in wrapper.read_text(encoding="utf-8", errors="replace"):
        wrapper.unlink()
    (bindir / "astroflow-deinstallieren").unlink(missing_ok=True)
    shutil.rmtree(prefix, ignore_errors=True)
    for d in (prefix.parent,):                         # leeren Ordner ~/.local/share/astroflow aufräumen
        try:
            d.rmdir()
        except OSError:
            pass
    _log("AstroFlow wurde entfernt. Deine Daten (~/AstroFlow) und Einstellungen (~/.config/astroflow) sind geblieben."
         + ("" if tools else " ASTAP, GraXpert und Zusatzpakete auch entfernen: mit --werkzeuge."))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="linux_install.py", description="AstroFlow für Linux installieren bzw. entfernen (pro Nutzer, ohne root)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    i = sub.add_parser("install")
    i.add_argument("--prefix", default=None, help="Programmordner (Standard ~/.local/share/astroflow/app)")
    i.add_argument("--bin", default=None, help="Ordner für den Befehl astroflow (Standard ~/.local/bin)")
    i.add_argument("--autostart", action="store_true", help="beim Anmelden im Hintergrund starten")
    i.add_argument("--no-desktop", action="store_true", help="keinen Menüeintrag anlegen")
    i.add_argument("--no-start", action="store_true", help="danach nicht starten")
    u = sub.add_parser("uninstall")
    u.add_argument("--prefix", default=None)
    u.add_argument("--bin", default=None)
    u.add_argument("--werkzeuge", action="store_true", help="auch ASTAP, GraXpert und Zusatzpakete (GPU, SkyTrace) entfernen")
    a = ap.parse_args(argv)
    prefix = Path(a.prefix).expanduser().resolve() if a.prefix else default_prefix()
    bindir = Path(a.bin).expanduser() if a.bin else default_bin()
    if a.cmd == "install":
        return install(prefix, bindir, a.autostart, not a.no_desktop, not a.no_start)
    return uninstall(prefix, bindir, a.werkzeuge)


if __name__ == "__main__":
    sys.exit(main())
