"""Optionale Komponenten: Zustand, Installieren, Reparieren, Entfernen – gemeinsam für Installer, Einrichtungsassistent,
Einstellungen → Module & System und die Kommandozeile (`astroflow components …`, vom Deinstaller benutzt).

| Komponente | Quelle | Ziel |
|---|---|---|
| `astap`    | SourceForge „astap-program“ (nie mitgeliefert: `deep_sky.csv` nur nicht-kommerziell frei, DB-Lizenz unklar) | `%LOCALAPPDATA%\\astap` bzw. `~/astap` |
| `graxpert` | GitHub-Release, feste Version + SHA-256 (GPL-3, Quellcode github.com/Steffenhir/GraXpert); MSI wird nur als  | `<Werkzeuge>/GraXpert` |
|            | Admin-Abbild ausgepackt (`msiexec /a`, ohne Adminrechte, ohne Registrierung) |  |
| `gpu`      | PyPI `pyopencl` | Python von AstroFlow |
| `skytrace` | PyPI + PyTorch-CPU-Index, `modules/skytrace/requirements-engine.txt` | Python von AstroFlow |
| `starnet`  | **kein Download** („all rights reserved“) – nur Link + ZIP-Import unter Einstellungen → Bilder | – |

pip-Installationen laufen mit Constraints auf die Versionen der Pakete, die der laufende Server geladen hat (numpy, scipy …) –
sonst würde pip sie ersetzen wollen, und unter Windows sind geladene .pyd-Dateien gesperrt.
Was AstroFlow selbst installiert hat, steht in `<Werkzeuge>/komponenten.json`; nur das wird beim Entfernen gelöscht.
"""
from __future__ import annotations

import hashlib
import importlib.metadata
import importlib.util
import io
import json
import os
import platform
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path

from . import launcher

NO_WINDOW = 0x08000000 if os.name == "nt" else 0

SF = "https://sourceforge.net/projects/astap-program/files/{}/download"
ASTAP_CLI = {   # (Betriebssystem, Architektur) → Datei auf SourceForge
    ("Windows", "AMD64"): "windows_installer/astap_command-line_version_win64.zip",
    ("Linux", "x86_64"): "linux_installer/astap_command-line_version_Linux_amd64.zip",
    ("Linux", "aarch64"): "linux_installer/astap_command-line_version_Linux_aarch64.zip",
    ("Darwin", "arm64"): "macOS installer/astap_command-line_version_macOS_M1.zip",
    ("Darwin", "x86_64"): "macOS installer/astap_command-line_version_macOS_x86_64.zip",
}
ASTAP_CATALOG = "windows_installer/astapwin32.zip"          # enthält deep_sky.csv (plattformunabhängig, ~1 MB)
ASTAP_DBS = {"d05": ("star_databases/d05_star_database.zip", 102), "d50": ("star_databases/d50_star_database.zip", 900)}
# SourceForge hat keine versionierten Dateinamen (die Dateien werden bei neuen ASTAP-Versionen ersetzt). Deshalb gilt nur die
# geprüfte Fassung: SHA-256 selbst gebildet am 07.11.2026 (alle Weiterleitungen über https). Neue ASTAP-Fassung → Abbruch mit
# Hinweis; dann hier bewusst ergänzen (Tupel = erlaubte Fassungen).
ASTAP_SHA256 = {
    "windows_installer/astap_command-line_version_win64.zip": ("19918dcc91db608b14153b9233e5776e7ca8d5c839cba52a117d559174c01b0c",),
    "linux_installer/astap_command-line_version_Linux_amd64.zip": ("5e2229e936fc53587dbacc8b0892822552af275b0c2d6f66b38947e0e41aeb47",),
    "linux_installer/astap_command-line_version_Linux_aarch64.zip": ("2e56d79a626c895298d49c9d841d4a449154f5764e2f39eee8bba669fdc6fa35",),
    "macOS installer/astap_command-line_version_macOS_M1.zip": ("9c64e6bd5087ce910ff43d56e860a4dcea6abacfd556aca80dcf9eae129096e7",),
    "macOS installer/astap_command-line_version_macOS_x86_64.zip": ("f75a359a86d3b320d62df9f72df26a23f754c574b155372e38eea71c22078517",),
    "windows_installer/astapwin32.zip": ("f39b180633896f494b2db26bd8aca6b7e17c33c355fbccc19f6813e1e638eb4e",),
    "star_databases/d05_star_database.zip": ("39d59b73cb960fa0956c35162e70b09a8af10ef20a1e8f5cae7b77d48be5ce31",),
    "star_databases/d50_star_database.zip": ("ca89a958ca0808185f0e546541b4ac73addc40fee4ad8bdd8bb79981c1dd8b43",),
}


class ChecksumError(RuntimeError):
    pass


def sha256_of(data) -> str:
    h = hashlib.sha256()
    if isinstance(data, (bytes, bytearray)):
        h.update(data)
    else:
        with open(data, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
    return h.hexdigest()


def verify_astap(path: str, data) -> None:
    """Geladene ASTAP-Datei gegen die geprüfte Fassung vergleichen; Abweichung → Abbruch (Datei wird nicht verwendet)."""
    if sha256_of(data) not in ASTAP_SHA256.get(path, ()):
        if isinstance(data, Path):
            data.unlink(missing_ok=True)
        raise ChecksumError(f"Prüfsumme von „{Path(path).name}“ stimmt nicht mit der geprüften Fassung überein – Datei verworfen. "
                            "Vermutlich gibt es eine neue ASTAP-Version: AstroFlow aktualisieren oder ASTAP selbst von "
                            "hnsky.org installieren (AstroFlow findet es dann von allein).")

# GraXpert: letzte stabile Version (3.1.0 nur als Vorabversion). Prüfsumme selbst gebildet (GitHub nennt keine), 07.11.2026.
GRAXPERT_VERSION = "3.0.2"
GRAXPERT_MSI = f"https://github.com/Steffenhir/GraXpert/releases/download/{GRAXPERT_VERSION}/graxpert-windows-amd64.msi"
GRAXPERT_SHA256 = "767fac98f57d89185a0ac5d1c1aee43083f1310e77682168a9aa3a70a88c8bd6"
GRAXPERT_MB = 160
GRAXPERT_SOURCE = "https://github.com/Steffenhir/GraXpert"

TORCH_CPU_INDEX = "https://download.pytorch.org/whl/cpu"
ENGINE_REQS = Path(__file__).resolve().parent.parent / "modules" / "skytrace" / "requirements-engine.txt"
ENGINE_MODULES = ("numpy", "cv2", "astropy", "PIL", "av", "torch", "ultralytics", "skyfield", "sgp4", "requests", "filelock")
# Vom Server geladene Pakete: bei pip-Installationen auf die vorhandene Version festhalten
SERVER_PACKAGES = ("numpy", "scipy", "astropy", "pillow", "tifffile", "imagecodecs", "fastapi", "starlette", "uvicorn", "pydantic",
                   "pydantic-core", "httpx", "httpcore", "anyio", "sgp4", "python-multipart", "pyerfa", "astropy-iers-data")

ORDER = ("astap", "graxpert", "gpu", "skytrace", "starnet")
INFO = {
    "astap": {"title": "Plate-Solving: ASTAP + Sterndatenbank", "license": "ASTAP: MPL-2.0/LGPL-3 · deep_sky.csv nur nicht-kommerziell",
              "source": "https://www.hnsky.org/astap.htm", "what": "Himmelsausschnitt bestimmen, Objektnamen, Gaia-Farbkalibrierung, Tiefe"},
    "graxpert": {"title": "GraXpert", "license": f"GPL-3.0 · lädt Version {GRAXPERT_VERSION} von GitHub", "source": GRAXPERT_SOURCE,
                 "what": "KI-Hintergrund und KI-Entrauschen des linearen Stacks"},
    "gpu": {"title": "GPU-Beschleunigung (pyopencl)", "license": "MIT", "source": "https://pypi.org/project/pyopencl/",
            "what": "Ausrichten und Stacking auf der Grafikkarte (OpenCL), etwa 3× schneller"},
    "skytrace": {"title": "SkyTrace-Analyse-Engine",
                 "license": "PyTorch BSD-3 · OpenCV Apache-2.0 · ultralytics + Detektormodell AGPL-3.0, SkyTrace-Engine AGPL-3.0-or-later, übriges AstroFlow PolyForm Noncommercial 1.0.0 · Microsoft Visual C++ Runtime (weitergebbar laut Microsoft)",
                 "source": "https://pytorch.org", "what": "Lichtspuren in Bildfolgen finden (OpenCV, PyTorch CPU, YOLO)"},
    "starnet": {"title": "StarNet", "license": "Hersteller: „all rights reserved“ – kein automatischer Download",
                "source": "https://www.starnetastro.com", "what": "Sterntrennung bei Nebeln (ZIP unter Einstellungen → Bilder → StarNet)"},
}
SIZES_MB = {"astap": 105, "graxpert": 413, "gpu": 40, "skytrace": 1400}      # belegter Platz nach der Installation (gemessen bzw. grob)


def system() -> tuple[str, str]:
    """(Betriebssystem, Architektur). platform.machine() ist unter Windows ohne PROCESSOR_ARCHITECTURE leer → Zeigerbreite."""
    sysname, mach = platform.system(), platform.machine()
    if sysname == "Windows" and not mach:
        mach = "AMD64" if sys.maxsize > 2**32 else "x86"
    return sysname, mach


def astap_dir() -> Path:
    if os.name == "nt" and os.environ.get("LOCALAPPDATA"):
        return Path(os.environ["LOCALAPPDATA"]) / "astap"
    return Path.home() / "astap"


def tools_dir() -> Path:
    """Von AstroFlow installierte Werkzeuge (GraXpert, Verzeichnis komponenten.json). Portabel: `<App>\\Werkzeuge`."""
    if launcher.home():
        return launcher.home() / "Werkzeuge"
    if os.name == "nt" and os.environ.get("LOCALAPPDATA"):
        return Path(os.environ["LOCALAPPDATA"]) / "AstroFlow" / "Werkzeuge"
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "astroflow" / "tools"


def graxpert_dir() -> Path:
    return tools_dir() / "GraXpert"


def _record_path() -> Path:
    return tools_dir() / "komponenten.json"


def records() -> dict:
    try:
        d = json.loads(_record_path().read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _record(name: str, value: dict | None):
    d = records()
    if value is None:
        d.pop(name, None)
    else:
        d[name] = value
    p = _record_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(p)


def console_python() -> str:
    """python.exe statt pythonw.exe (pip braucht eine Konsole für saubere Ausgabe; ohne Fenster gestartet)."""
    exe = Path(sys.executable)
    if exe.name.lower() == "pythonw.exe" and exe.with_name("python.exe").is_file():
        return str(exe.with_name("python.exe"))
    return str(exe)


# --------------------------------------------------------------------------- #
# Zustand
# --------------------------------------------------------------------------- #
def status(solver_cfg=None, skytrace_health: dict | None = None) -> list[dict]:
    rec = records()
    out = []
    for name in ORDER:
        s = {"id": name, **INFO[name], "size_mb": SIZES_MB.get(name), "ours": name in rec, "can_install": True,
             "can_remove": name in ("astap", "graxpert") and name in rec}
        try:
            s.update(globals()[f"_status_{name}"](rec.get(name) or {}, solver_cfg, skytrace_health))
        except Exception as e:
            s.update(state="error", detail=f"Zustand nicht lesbar: {e}")
        out.append(s)
    return out


def _status_astap(rec, solver_cfg, _):
    from ..modules.stacking import platesolve
    cfg = solver_cfg or platesolve.SolverConfig.from_env()
    path, dbs = cfg.astap_path(), cfg.astap_databases()
    supported = system() in ASTAP_CLI
    if path and dbs:
        if not rec:                                   # vorhandenes ASTAP, nichts davon von AstroFlow: nie „reparieren“
            return {"state": "installed", "version": ", ".join(dbs), "supported": supported, "can_install": False,
                    "detail": f"{path} – eigene Installation, AstroFlow verändert sie nicht"}
        return {"state": "installed", "version": ", ".join(dbs), "detail": path, "supported": supported}
    if path:
        return {"state": "partial", "detail": f"{path} – Sterndatenbank fehlt", "supported": supported}
    return {"state": "missing" if supported else "unsupported", "supported": supported,
            "detail": "" if supported else "Für dieses System ASTAP bitte selbst installieren (hnsky.org)."}


def _status_graxpert(rec, *_):
    from ..modules.stacking import graxpert
    p = graxpert.graxpert_path()
    supported = os.name == "nt"
    if p and p != graxpert.own_graxpert():
        return {"state": "installed", "version": "", "detail": f"{p} – eigene Installation, AstroFlow verändert sie nicht",
                "supported": supported, "can_install": False, "can_remove": False}
    if p:
        return {"state": "installed", "version": rec.get("version") or "", "detail": p, "supported": supported}
    return {"state": "missing" if supported else "unsupported", "supported": supported,
            "detail": "" if supported else "Unter macOS/Linux bitte von der GraXpert-Seite installieren – AstroFlow findet es dann."}


def _status_gpu(rec, *_):
    if importlib.util.find_spec("pyopencl") is None:
        return {"state": "missing", "supported": True}
    ver = _dist_version("pyopencl")
    try:
        from ..modules.stacking import gpu
        g = gpu.status()
        dev = g.get("device")
        detail = f"Grafikkarte: {dev}" if dev else "pyopencl vorhanden, aber keine geeignete OpenCL-Grafikkarte (≥ 2 GB) gefunden – es rechnet der Prozessor."
        if g.get("mode") == "off":
            detail = "pyopencl vorhanden, GPU in den Einstellungen abgeschaltet."
    except Exception as e:
        detail = f"pyopencl vorhanden ({e})"
    return {"state": "installed", "version": ver, "detail": detail, "supported": True}


def vc_runtime_ok() -> bool:
    """Windows: Microsoft-C++-Laufzeit für PyTorch vorhanden (neben python.exe aus „msvc-runtime“ oder systemweit)?"""
    if os.name != "nt":
        return True
    sysdir = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32"
    dirs = [Path(sys.prefix), Path(sys.executable).parent, sysdir] + ([packages_dir()] if packages_dir() else [])
    return any((d / "msvcp140.dll").is_file() for d in dirs)


VC_MISSING = ("Microsoft-C++-Laufzeit fehlt (msvcp140.dll) – PyTorch lässt sich ohne sie nicht laden. "
              "Einstellungen → Module & System → Komponenten → SkyTrace-Analyse-Engine → Reparieren.")


# --------------------------------------------------------------------------- #
# Eigener Paketordner (Windows-Paket): Zusatzpakete überstehen Updates des mitgelieferten Pythons
# --------------------------------------------------------------------------- #
def packages_dir() -> Path | None:
    """`%LOCALAPPDATA%\\AstroFlow\\Pakete\\cp312-win_amd64`, wenn das Paket den Haken `astroflow_pakete` (.pth) mitbringt;
    sonst None → pip installiert wie gewohnt ins laufende Python (Entwickler-venv, macOS/Linux)."""
    try:
        import astroflow_pakete
        return Path(astroflow_pakete.DIR)
    except Exception:
        return None


def _activate_packages(pk: Path):
    """Frisch angelegten Paketordner im laufenden Server sofort nutzbar machen."""
    try:
        import astroflow_pakete
        astroflow_pakete.activate(str(pk))
    except Exception:
        if str(pk) not in sys.path:
            sys.path.append(str(pk))
    sys.path_importer_cache.pop(str(pk), None)
    importlib.invalidate_caches()


def _prune_duplicates(pk: Path):
    """pip --target legt auch Abhängigkeiten ab, die das mitgelieferte Python schon hat (numpy, pillow … – per Constraints
    in gleicher Version). Diese Doppel entfernen, damit nur eine Kopie geladen werden kann und der Ordner klein bleibt."""
    norm = lambda n: (n or "").lower().replace("_", "-")
    own = set()                                       # (Name, Version) außerhalb des Paketordners – Test-Python oder Bündel
    for d in importlib.metadata.distributions():
        loc = Path(str(d.locate_file(""))).resolve()
        if pk.resolve() not in (loc, *loc.parents):
            own.add((norm(d.metadata["Name"]), d.version))
    for info in pk.glob("*.dist-info"):
        name, _, ver = info.name.removesuffix(".dist-info").rpartition("-")      # „numpy-2.5.4.dist-info“ → numpy, 2.5.4
        if (norm(name), ver) not in own:              # nur exakt gleiche Version (andere bleiben unangetastet)
            continue
        try:
            rows = (info / "RECORD").read_text(encoding="utf-8").splitlines()
        except OSError:
            rows = []
        for row in rows:
            rel = row.split(",")[0]
            if rel and not rel.startswith(".."):
                (pk / rel).unlink(missing_ok=True)
        shutil.rmtree(info, ignore_errors=True)
    for d in sorted((p for p in pk.rglob("*") if p.is_dir()), key=lambda p: -len(p.parts)):
        try:
            d.rmdir()                                 # leere Paketordner der entfernten Doppel
        except OSError:
            pass


def _drop_old_tags(pk: Path):
    """Paketordner anderer Python-Versionen (ABI) entfernen – sie würden nie mehr geladen."""
    for other in pk.parent.glob("cp*"):
        if other.is_dir() and other.name != pk.name:
            shutil.rmtree(other, ignore_errors=True)


def _install_vc_runtime(pk: Path, prog):
    """msvc-runtime-Wheel laden und seine DLLs in den Paketordner legen (pip --target übernimmt Datendateien nicht)."""
    if (pk / "msvcp140.dll").is_file():
        return
    tmp = tools_dir() / "vc-runtime"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    prog(0.965, "Lade Microsoft-C++-Laufzeit …")
    r = subprocess.run([console_python(), "-m", "pip", "download", "--disable-pip-version-check", "--no-deps", "--only-binary=:all:",
                        "msvc-runtime", "-d", str(tmp)], capture_output=True, text=True, timeout=600, creationflags=NO_WINDOW)
    whl = next(tmp.glob("msvc_runtime-*.whl"), None)
    if r.returncode or not whl:
        raise RuntimeError(VC_MISSING + " (Download: " + (r.stderr or r.stdout).strip()[-200:] + ")")
    with zipfile.ZipFile(whl) as z:
        for n in z.namelist():
            parts = n.split("/")
            if len(parts) == 3 and parts[1] == "data" and n.lower().endswith(".dll"):
                (pk / parts[2]).write_bytes(z.read(n))
    shutil.rmtree(tmp, ignore_errors=True)


def _status_skytrace(rec, solver_cfg, health):
    missing = [m for m in ENGINE_MODULES if importlib.util.find_spec(m) is None]
    if not missing and not vc_runtime_ok():
        missing = ["Microsoft-C++-Laufzeit (msvcp140.dll)"]
    if health and health.get("checked") and health.get("python") and Path(health["python"]).resolve() != Path(sys.executable).resolve():
        # eigener Interpreter in Einstellungen → SkyTrace
        return {"state": "installed" if health.get("ok") else "partial", "supported": True,
                "detail": f"Eigener Interpreter: {health['python']}" + ("" if health.get("ok") else f" – fehlt: {', '.join(health.get('missing') or [])}"),
                "version": ""}
    if not missing:
        pk = packages_dir()
        return {"state": "installed", "version": f"torch {_dist_version('torch')}", "supported": True,
                "detail": f"Paketordner {pk} (bleibt bei Updates)" if pk else "im Python von AstroFlow"}
    if len(missing) < len(ENGINE_MODULES) - 3:
        return {"state": "partial", "detail": "fehlt: " + ", ".join(missing), "supported": True}
    return {"state": "missing", "supported": True}


def _status_starnet(*_):
    try:
        from ..modules.stacking import stars
        p = stars.starnet_path()
    except Exception:
        p = None
    return {"state": "installed" if p else "missing", "detail": p or "", "supported": True, "can_install": False}


def _dist_version(name: str) -> str:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return ""


# --------------------------------------------------------------------------- #
# Installieren / Entfernen
# --------------------------------------------------------------------------- #
def install(name: str, prog=lambda f, d: None, option: str | None = None, force: bool = False) -> str:
    if name not in ("astap", "graxpert", "gpu", "skytrace"):
        raise ValueError("StarNet lädt AstroFlow nicht herunter (Lizenz) – bitte ZIP unter Einstellungen → Bilder → StarNet einspielen."
                         if name == "starnet" else f"Unbekannte Komponente: {name}")
    return globals()[f"_install_{name}"](prog, option, force)


def remove(name: str) -> str:
    rec = records().get(name)
    if not rec:
        return f"{name}: nicht von AstroFlow installiert – bleibt unverändert."
    if name == "graxpert":
        shutil.rmtree(rec.get("dir") or graxpert_dir(), ignore_errors=True)
    elif name == "astap":
        base = Path(rec.get("dir") or astap_dir())
        for f in rec.get("files") or []:
            try:
                (base / f).unlink()
            except OSError:
                pass
        try:
            base.rmdir()                          # nur, wenn nichts Fremdes mehr drin liegt
        except OSError:
            pass
    _record(name, None)
    return f"{INFO[name]['title']} entfernt."


def _install_astap(prog, option, force) -> str:
    db = (option or "d05").lower()
    if db not in ASTAP_DBS:
        raise ValueError("Unbekannte Sterndatenbank.")
    key = system()
    if key not in ASTAP_CLI:
        raise ValueError(f"Für {key[0]} {key[1]} gibt es keine ASTAP-Kommandozeile – bitte ASTAP selbst installieren.")
    dest = astap_dir()
    dest.mkdir(parents=True, exist_ok=True)
    rec = records().get("astap") or {"dir": str(dest), "files": []}
    files, steps = set(rec.get("files") or []), []
    owned = set(files)

    def need(p: Path) -> bool:
        """Fehlt → laden. Vorhanden → nur beim Reparieren und nur, wenn AstroFlow die Datei selbst angelegt hat."""
        return not p.exists() or (force and p.name in owned)

    cli = dest / ("astap_cli.exe" if os.name == "nt" else "astap_cli")
    if need(cli):
        prog(0.01, "Lade ASTAP-Kommandozeile …")
        data = download(SF.format(ASTAP_CLI[key]), lambda f: prog(0.01 + 0.04 * f, "Lade ASTAP-Kommandozeile …"))
        verify_astap(ASTAP_CLI[key], data)
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            name = next((n for n in z.namelist() if Path(n).name.startswith("astap_cli")), None)
            if not name:
                raise RuntimeError("ASTAP-Paket enthält keine astap_cli.")
            cli.write_bytes(z.read(name))
        if os.name != "nt":
            cli.chmod(0o755)
        files.add(cli.name)
        steps.append("Kommandozeile")
    if need(dest / "deep_sky.csv"):
        prog(0.06, "Lade Objektkatalog deep_sky.csv …")
        data = download(SF.format(ASTAP_CATALOG), lambda f: prog(0.06 + 0.04 * f, "Lade Objektkatalog deep_sky.csv …"))
        verify_astap(ASTAP_CATALOG, data)
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            for n in z.namelist():
                if Path(n).name in ("deep_sky.csv", "acknowledgement of databases.txt") and need(dest / Path(n).name):
                    (dest / Path(n).name).write_bytes(z.read(n))
                    files.add(Path(n).name)
        steps.append("Objektkatalog")
    dbfiles = list(dest.glob(f"{db}_*"))
    if not list(dest.glob(f"{db}_0101.*")) or (force and dbfiles and all(f.name in owned for f in dbfiles)):
        mb = ASTAP_DBS[db][1]
        tmp = tools_dir() / f".{db}.zip.part"
        tmp.parent.mkdir(parents=True, exist_ok=True)
        download(SF.format(ASTAP_DBS[db][0]), lambda f: prog(0.10 + 0.8 * f, f"Lade Sterndatenbank {db.upper()} (~{mb} MB) … {f*100:.0f} %"),
                 to=tmp, resume=True)
        prog(0.91, "Prüfe Prüfsumme …")
        verify_astap(ASTAP_DBS[db][0], tmp)
        prog(0.92, "Entpacke Sterndatenbank …")
        try:
            with zipfile.ZipFile(tmp) as z:
                for n in z.namelist():
                    base = Path(n).name
                    if base.lower().startswith(db) and not n.endswith("/") and need(dest / base):
                        with z.open(n) as src, open(dest / base, "wb") as out:
                            shutil.copyfileobj(src, out, 1 << 20)
                        files.add(base)
        except zipfile.BadZipFile:
            tmp.unlink(missing_ok=True)              # kaputter Teil-Download: beim nächsten Versuch neu laden
            raise RuntimeError("Sterndatenbank-Download beschädigt – bitte erneut versuchen.")
        tmp.unlink(missing_ok=True)
        if not list(dest.glob(f"{db}_0101.*")):
            raise RuntimeError("Die Sterndatenbank wurde nicht gefunden – Paketaufbau hat sich geändert?")
        steps.append(f"Sterndatenbank {db.upper()}")
    prog(0.97, "Prüfe ASTAP …")
    try:
        _astap_selftest(cli)
    except OSError as e:
        raise RuntimeError(f"ASTAP lässt sich nicht starten: {e}")
    if steps:                                         # nur Selbstinstalliertes merken (vorhandenes ASTAP bleibt fremd)
        _record("astap", {"dir": str(dest), "files": sorted(files), "db": db, "installed": time.time()})
    _refresh_astap()
    return ("Installiert: " + ", ".join(steps) if steps else "Bereits vorhanden.") + f" Ordner: {dest}"


def _astap_selftest(cli):
    """Selbstprüfung der installierten ASTAP-CLI (Haken: Tests ersetzen ihn, statt eine EXE zu starten)."""
    subprocess.run([str(cli), "-h"], capture_output=True, timeout=30, creationflags=NO_WINDOW)


def _refresh_astap():
    try:
        from ..modules.stacking import objects, platesolve
        platesolve._AVAILABLE.clear()
        objects._EXT = None
    except Exception:
        pass


def _install_graxpert(prog, option, force) -> str:
    if os.name != "nt":
        raise ValueError("GraXpert installiert AstroFlow nur unter Windows selbst – bitte von der GraXpert-Seite installieren.")
    from ..modules.stacking import graxpert as gx
    other = gx.system_graxpert()
    if other:                                         # normal installiertes GraXpert hat Vorrang und wird nie angefasst
        return f"GraXpert ist bereits installiert ({other}) – AstroFlow nutzt diese Installation."
    dest = graxpert_dir()
    exe = dest / "GraXpert.exe"
    if exe.is_file() and not force:
        _record("graxpert", {"dir": str(dest), "version": GRAXPERT_VERSION, "installed": time.time()})
        return f"GraXpert {GRAXPERT_VERSION} ist bereits installiert."
    msi = tools_dir() / f".graxpert-{GRAXPERT_VERSION}.msi.part"
    msi.parent.mkdir(parents=True, exist_ok=True)
    prog(0.01, f"Lade GraXpert {GRAXPERT_VERSION} (~{GRAXPERT_MB} MB) von GitHub …")
    download(GRAXPERT_MSI, lambda f: prog(0.01 + 0.7 * f, f"Lade GraXpert {GRAXPERT_VERSION} (~{GRAXPERT_MB} MB) … {f*100:.0f} %"),
             to=msi, resume=True)
    prog(0.72, "Prüfe Prüfsumme …")
    if sha256_of(msi) != GRAXPERT_SHA256:
        msi.unlink(missing_ok=True)
        raise RuntimeError("Prüfsumme des GraXpert-Pakets stimmt nicht – Download verworfen, bitte erneut versuchen.")
    prog(0.75, "Packe GraXpert aus …")
    stage = dest.with_name("GraXpert.neu")
    shutil.rmtree(stage, ignore_errors=True)
    final = msi.with_suffix("")                       # msiexec mag keine „.part“-Endung
    msi.replace(final)
    log = tools_dir() / "graxpert-msi.log"
    r = subprocess.run(["msiexec", "/a", str(final), "/qn", f"TARGETDIR={stage}", "/l*", str(log)], capture_output=True, timeout=1800,
                       creationflags=NO_WINDOW)
    final.unlink(missing_ok=True)
    if r.returncode or not (stage / "GraXpert.exe").is_file():
        shutil.rmtree(stage, ignore_errors=True)
        raise RuntimeError(f"GraXpert ließ sich nicht auspacken (msiexec {r.returncode}, Protokoll {log}).")
    for f in stage.glob("*.msi"):                     # Admin-Abbild legt eine Kopie des MSI ab
        f.unlink(missing_ok=True)
    shutil.rmtree(dest, ignore_errors=True)
    stage.replace(dest)
    log.unlink(missing_ok=True)
    _record("graxpert", {"dir": str(dest), "version": GRAXPERT_VERSION, "sha256": GRAXPERT_SHA256, "installed": time.time()})
    return f"GraXpert {GRAXPERT_VERSION} installiert ({dest}). GPL-3.0, Quellcode: {GRAXPERT_SOURCE}"


def _constraints() -> Path:
    lines = [f"{n}=={v}" for n in SERVER_PACKAGES if (v := _dist_version(n))]
    p = tools_dir() / "constraints.txt"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


def pip(args: list[str], prog, start: float, end: float, expect: int, label: str) -> None:
    """pip im Python von AstroFlow; Fortschritt grob über die Zahl der „Collecting“-Zeilen."""
    pk = packages_dir()
    target = ["--target", str(pk), "--upgrade"] if pk else []
    if pk:
        pk.mkdir(parents=True, exist_ok=True)
    cmd = [console_python(), "-m", "pip", "install", "--disable-pip-version-check", "--no-warn-script-location",
           "--progress-bar", "off", "-c", str(_constraints()), *target, *args]
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PIP_NO_INPUT": "1"}
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
                         env=env, creationflags=NO_WINDOW)
    tail, n = [], 0
    for line in p.stdout:
        line = line.strip()
        if not line:
            continue
        tail = (tail + [line])[-12:]
        if line.startswith(("Collecting", "Downloading", "Installing collected")):
            n += line.startswith("Collecting")
            what = line.split()[1] if line.startswith(("Collecting", "Downloading")) else "Pakete"
            prog(start + (end - start) * min(0.95, n / max(1, expect)), f"{label}: {what[:60]}")
    if p.wait():
        raise RuntimeError("pip: " + " | ".join(tail[-3:])[-400:])
    if pk:
        _prune_duplicates(pk)
        _drop_old_tags(pk)
        _activate_packages(pk)


def _install_gpu(prog, option, force) -> str:
    if not force and importlib.util.find_spec("pyopencl") is not None:
        return f"pyopencl {_dist_version('pyopencl')} ist bereits installiert."
    prog(0.05, "Installiere pyopencl …")
    pip((["--force-reinstall", "--no-deps"] if force else []) + ["pyopencl"], prog, 0.05, 0.95, 4, "GPU")
    try:
        from ..modules.stacking import gpu
        gpu._TRIED = False                            # beim nächsten Stack neu nach einer Karte suchen
        gpu._ENGINE = None
    except Exception:
        pass
    return "pyopencl installiert."


def _engine_import_test() -> str | None:
    """Importtest im eigenen Prozess (fängt fehlende DLLs ab); None = in Ordnung, sonst Fehlermeldung."""
    r = subprocess.run([console_python(), "-c", "import torch, cv2, ultralytics; print(torch.__version__)"], capture_output=True,
                       text=True, timeout=300, creationflags=NO_WINDOW)
    if not r.returncode:
        return None
    last = ((r.stderr or r.stdout).strip().splitlines() or ["?"])[-1][:300]
    return VC_MISSING if ("c10.dll" in last or "WinError 126" in last) else "PyTorch lässt sich nicht laden: " + last


def _install_skytrace(prog, option, force) -> str:
    pk = packages_dir()
    if not force and all(importlib.util.find_spec(m) for m in ENGINE_MODULES) and vc_runtime_ok():
        prog(0.5, "Prüfe vorhandene Engine …")       # nach einem Update: Pakete im Paketordner sind noch da
        if not _engine_import_test():
            return f"SkyTrace-Engine vorhanden (torch {_dist_version('torch')}) – nichts neu geladen."
    extra = ["--force-reinstall"] if force else []
    prog(0.02, "Installiere PyTorch (CPU), OpenCV, YOLO, Skyfield …")
    # Ein einziger Lauf: der CPU-Index von PyTorch zuerst, PyPI für den Rest. „2.x+cpu“ ist nach PEP 440 neuer als „2.x“,
    # pip bleibt also bei den CPU-Wheels. (Zwei Läufe mit --target --upgrade ersetzten die CPU-Wheels im zweiten durch PyPI.)
    pip(extra + ["--index-url", TORCH_CPU_INDEX, "--extra-index-url", "https://pypi.org/simple", "-r", str(ENGINE_REQS)],
        prog, 0.02, 0.97, 60, "Engine")
    if pk and os.name == "nt":
        _install_vc_runtime(pk, prog)
        _activate_packages(pk)
    importlib.invalidate_caches()
    missing = [m for m in ENGINE_MODULES if importlib.util.find_spec(m) is None]
    if missing:
        raise RuntimeError("Nach der Installation fehlen noch: " + ", ".join(missing))
    if not vc_runtime_ok():
        raise RuntimeError(VC_MISSING)
    prog(0.98, "Prüfe PyTorch …")
    err = _engine_import_test()
    if err:
        raise RuntimeError(err)
    return f"SkyTrace-Engine installiert (torch {_dist_version('torch')}, CPU)."


# --------------------------------------------------------------------------- #
# Herunterladen (mit Fortsetzen)
# --------------------------------------------------------------------------- #
DL_TRIES = 6            # Versuche je Datei (SourceForge-Spiegel brechen gelegentlich mitten in der Übertragung ab)
DL_PAUSE = 2.0          # Wartezeit vor dem n. neuen Versuch: DL_PAUSE · 2^(n−1) s, höchstens 30 s


class _Retry(Exception):
    """Vorübergehender Fehler – neu ansetzen (zu kurze Übertragung, HTTP 5xx/429, HTML-Seite statt Datei)."""


def download(url: str, prog, to: Path | None = None, resume: bool = False, tries: int | None = None) -> bytes | None:
    """Datei laden – nach `to` oder in den Speicher (Rückgabe der Bytes).

    Bricht die Übertragung ab (Verbindung getrennt, Zeitüberschreitung, zu wenige Bytes, Serverfehler), wird bis zu
    `tries`-mal neu angesetzt und per HTTP-Range fortgesetzt; ignoriert der Server Range, beginnt der Versuch von vorn.
    Mit `to` + `resume` wird außerdem ein Teil-Download aus einem früheren Lauf fortgesetzt – die `.part`-Datei bleibt
    bei einem endgültigen Fehlschlag liegen (Anlass: Tag-Lauf v0.44.0, SourceForge trennte nach 1,4 von 5,8 MB)."""
    import httpx
    tries = max(1, tries or DL_TRIES)
    if to and not resume:
        to.unlink(missing_ok=True)
    mem = None if to else bytearray()
    for attempt in range(1, tries + 1):
        try:
            _fetch(httpx, url, prog, to, mem)
            return None if to else bytes(mem)
        except (httpx.TransportError, _Retry) as e:
            if attempt >= tries:
                raise RuntimeError(f"Download abgebrochen nach {tries} Versuchen ({_short(e)}) – bitte später erneut "
                                   "versuchen.") from e
            time.sleep(min(30.0, DL_PAUSE * 2 ** (attempt - 1)))


def _short(e: Exception) -> str:
    return ((str(e) or type(e).__name__).splitlines() or [type(e).__name__])[0][:160]


def _https_only(request) -> None:
    """Jede Anfrage der Kette (auch nach Weiterleitungen) nur über https – außer an den eigenen Rechner (Tests, lokaler Spiegel)."""
    if request.url.scheme != "https" and request.url.host not in ("127.0.0.1", "localhost", "::1"):
        raise ChecksumError(f"Unsichere Download-Adresse ({request.url.scheme}://{request.url.host}) – abgebrochen, nur https ist erlaubt.")


def _fetch(httpx, url: str, prog, to: Path | None, mem: bytearray | None) -> None:
    """Ein Versuch: setzt bei vorhandenen Bytes (Datei bzw. `mem`) per Range fort."""
    have = len(mem) if mem is not None else (to.stat().st_size if to.exists() else 0)
    headers = {"User-Agent": "AstroFlow", "Accept-Encoding": "identity"}   # Bytes wie auf dem Server (Range zählt sie)
    if have:
        headers["Range"] = f"bytes={have}-"
    with httpx.Client(follow_redirects=True, timeout=httpx.Timeout(60, read=120), event_hooks={"request": [_https_only]}) as cl, \
            cl.stream("GET", url, headers=headers) as r:
        if r.status_code == 416 and have:             # schon vollständig
            prog(1.0)
            return
        if r.status_code >= 500 or r.status_code == 429:
            raise _Retry(f"HTTP {r.status_code}")
        r.raise_for_status()
        if "text/html" in (r.headers.get("content-type") or ""):
            raise _Retry("Download-Seite statt Datei erhalten – Quelle nicht erreichbar?")
        append = bool(have) and r.status_code == 206
        if not append:                                # Server ignoriert Range (oder neuer Download) → von vorn
            have = 0
            if mem is not None:
                del mem[:]
        size = r.headers.get("content-length") or ""
        total = int(size) + have if size.isdigit() else 0
        rng = r.headers.get("content-range") or ""    # „bytes 1381144-5785578/5785579“
        if append and rng.rsplit("/", 1)[-1].isdigit():
            total = int(rng.rsplit("/", 1)[-1])
        got = have
        out = open(to, "ab" if append else "wb") if to else None
        try:
            for chunk in r.iter_raw():                # ungepuffert: bei Abbruch ist alles Empfangene schon gesichert
                if out:
                    out.write(chunk)
                else:
                    mem.extend(chunk)
                got += len(chunk)
                if total:
                    prog(min(1.0, got / total))
        finally:
            if out:
                out.close()
        if total and got < total:                     # Verbindung ohne Fehler beendet, aber zu kurz
            raise _Retry(f"nur {got} von {total} Bytes erhalten")


# --------------------------------------------------------------------------- #
# Auftrag aus dem Installer
# --------------------------------------------------------------------------- #
def order_path() -> Path:
    """Vom Inno-Installer geschriebene Liste der gewählten Komponenten (eine je Zeile, z. B. „astap:d05“)."""
    return launcher.config_path().parent / "komponenten-auftrag.txt"


def take_order() -> list[str]:
    """Auftrag des Installers übernehmen: in config.json → components_pending verschieben (übersteht Neustarts und Abbrüche)."""
    p = order_path()
    items = []
    try:
        items = [l.strip() for l in p.read_text(encoding="utf-8-sig").splitlines() if l.strip()]
    except OSError:
        pass
    if items:
        pend = list(dict.fromkeys((launcher.read_config().get("components_pending") or []) + items))
        launcher.write_config({"components_pending": pend})
        try:
            p.unlink()
        except OSError:
            pass
    return launcher.read_config().get("components_pending") or []


def done_order(item: str):
    pend = [x for x in (launcher.read_config().get("components_pending") or []) if x != item]
    launcher.write_config({"components_pending": pend})


def cli(argv: list[str]) -> int:
    """`astroflow components [status | install NAME[:OPTION] … | remove NAME …]`"""
    act = argv[0] if argv else "status"
    if act == "status":
        for s in status():
            print(f"{s['id']:9} {s['state']:11} {s.get('version') or '':18} {s.get('detail') or ''}")
        return 0
    rc = 0
    for item in argv[1:]:
        name, _, opt = item.partition(":")
        try:
            msg = install(name, lambda f, d: print(f"  {f*100:5.1f} % {d}", flush=True), opt or None) if act == "install" else remove(name)
            print(msg)
        except Exception as e:
            print(f"{name}: {e}", file=sys.stderr)
            rc = 1
    return rc
