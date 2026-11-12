"""Eigener Paketordner für nachinstallierte Komponenten (GPU, SkyTrace-Engine) – außerhalb des mitgelieferten Pythons.

Liegt im Windows-Paket in `python/Lib/site-packages/` (Linux-Paket: `python/lib/python3.12/site-packages/`) und wird über
`astroflow-pakete.pth` bei jedem Start geladen (auch mit `-I` und in den Engine-Unterprozessen). Ein Update ersetzt das
mitgelieferte Python vollständig, dieser Ordner bleibt: `%LOCALAPPDATA%\\AstroFlow\\Pakete\\cp312-win_amd64` bzw.
`~/.local/share/astroflow/packages/cp312-linux_x86_64` (portabel `<App>/Pakete/…`). Der Unterordner trägt Python-Version und
Plattform, ändert sich die Python-Version (andere ABI), werden die Pakete neu installiert statt falsch geladen.
Nur Standardbibliothek; jeder Fehler bleibt still (AstroFlow läuft dann ohne die Zusatzpakete).
"""
import os
import platform
import sys


def _platform_tag() -> str:
    if os.name == "nt":
        return "win_amd64" if sys.maxsize > 2**32 else "win32"
    return f"{sys.platform.rstrip('0123456789')}_{platform.machine() or 'unknown'}".lower()      # linux_x86_64, darwin_arm64


TAG = f"cp{sys.version_info[0]}{sys.version_info[1]}-{_platform_tag()}"


def base() -> str:
    home = os.environ.get("ASTROFLOW_HOME")
    if home:
        return os.path.join(home, "Pakete")
    if os.name == "nt":
        return os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "AstroFlow", "Pakete")
    return os.path.join(os.environ.get("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".local", "share"),
                        "astroflow", "packages")


DIR = os.path.join(base(), TAG)


def activate(path: str = DIR) -> None:
    """Ordner an sys.path anhängen (das mitgelieferte Python hat Vorrang) und für DLLs (PyTorch, C++-Laufzeit) freigeben."""
    if path not in sys.path:
        sys.path.append(path)
    if os.path.isdir(path) and hasattr(os, "add_dll_directory") and not getattr(sys, "_astroflow_pakete_dll", None):
        try:
            sys._astroflow_pakete_dll = os.add_dll_directory(path)
        except OSError:
            pass


try:
    activate()
except Exception:
    pass
