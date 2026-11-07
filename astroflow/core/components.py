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
    "skytrace": {"title": "SkyTrace-Analyse-Engine", "license": "PyTorch BSD · Ultralytics/Detektormodell AGPL-3.0",
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
        return {"state": "installed", "version": ", ".join(dbs), "detail": path, "supported": supported}
    if path:
        return {"state": "partial", "detail": f"{path} – Sterndatenbank fehlt", "supported": supported}
    return {"state": "missing" if supported else "unsupported", "supported": supported,
            "detail": "" if supported else "Für dieses System ASTAP bitte selbst installieren (hnsky.org)."}


def _status_graxpert(rec, *_):
    from ..modules.stacking import graxpert
    p = graxpert.graxpert_path()
    supported = os.name == "nt"
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
    return any((d / "msvcp140.dll").is_file() for d in (Path(sys.prefix), Path(sys.executable).parent, sysdir))


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
        return {"state": "installed", "version": f"torch {_dist_version('torch')}", "detail": "im Python von AstroFlow", "supported": True}
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
    cli = dest / ("astap_cli.exe" if os.name == "nt" else "astap_cli")
    if force or not cli.exists():
        prog(0.01, "Lade ASTAP-Kommandozeile …")
        data = download(SF.format(ASTAP_CLI[key]), lambda f: prog(0.01 + 0.04 * f, "Lade ASTAP-Kommandozeile …"))
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            name = next((n for n in z.namelist() if Path(n).name.startswith("astap_cli")), None)
            if not name:
                raise RuntimeError("ASTAP-Paket enthält keine astap_cli.")
            cli.write_bytes(z.read(name))
        if os.name != "nt":
            cli.chmod(0o755)
        files.add(cli.name)
        steps.append("Kommandozeile")
    if force or not (dest / "deep_sky.csv").exists():
        prog(0.06, "Lade Objektkatalog deep_sky.csv …")
        data = download(SF.format(ASTAP_CATALOG), lambda f: prog(0.06 + 0.04 * f, "Lade Objektkatalog deep_sky.csv …"))
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            for n in z.namelist():
                if Path(n).name in ("deep_sky.csv", "acknowledgement of databases.txt"):
                    (dest / Path(n).name).write_bytes(z.read(n))
                    files.add(Path(n).name)
        steps.append("Objektkatalog")
    if force or not list(dest.glob(f"{db}_0101.*")):
        mb = ASTAP_DBS[db][1]
        tmp = tools_dir() / f".{db}.zip.part"
        tmp.parent.mkdir(parents=True, exist_ok=True)
        download(SF.format(ASTAP_DBS[db][0]), lambda f: prog(0.10 + 0.8 * f, f"Lade Sterndatenbank {db.upper()} (~{mb} MB) … {f*100:.0f} %"),
                 to=tmp, resume=True)
        prog(0.92, "Entpacke Sterndatenbank …")
        try:
            with zipfile.ZipFile(tmp) as z:
                for n in z.namelist():
                    base = Path(n).name
                    if base.lower().startswith(db) and not n.endswith("/"):
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
        subprocess.run([str(cli), "-h"], capture_output=True, timeout=30, creationflags=NO_WINDOW)
    except OSError as e:
        raise RuntimeError(f"ASTAP lässt sich nicht starten: {e}")
    if steps:                                         # nur Selbstinstalliertes merken (vorhandenes ASTAP bleibt fremd)
        _record("astap", {"dir": str(dest), "files": sorted(files), "db": db, "installed": time.time()})
    _refresh_astap()
    return ("Installiert: " + ", ".join(steps) if steps else "Bereits vorhanden.") + f" Ordner: {dest}"


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
    h = hashlib.sha256()
    with open(msi, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    if h.hexdigest() != GRAXPERT_SHA256:
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
    cmd = [console_python(), "-m", "pip", "install", "--disable-pip-version-check", "--no-warn-script-location",
           "--progress-bar", "off", "-c", str(_constraints()), *args]
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


def _install_gpu(prog, option, force) -> str:
    prog(0.05, "Installiere pyopencl …")
    pip((["--force-reinstall", "--no-deps"] if force else []) + ["pyopencl"], prog, 0.05, 0.95, 4, "GPU")
    try:
        from ..modules.stacking import gpu
        gpu._TRIED = False                            # beim nächsten Stack neu nach einer Karte suchen
        gpu._ENGINE = None
    except Exception:
        pass
    return "pyopencl installiert."


def _install_skytrace(prog, option, force) -> str:
    extra = ["--force-reinstall"] if force else []
    prog(0.02, "Installiere PyTorch (CPU) …")
    pip(extra + ["--index-url", TORCH_CPU_INDEX, "torch", "torchvision"], prog, 0.02, 0.6, 12, "PyTorch")
    prog(0.6, "Installiere OpenCV, YOLO, Skyfield …")
    pip(extra + ["-r", str(ENGINE_REQS)], prog, 0.6, 0.97, 45, "Engine")
    importlib.invalidate_caches()
    missing = [m for m in ENGINE_MODULES if importlib.util.find_spec(m) is None]
    if not vc_runtime_ok():
        missing.append("Microsoft-C++-Laufzeit")
    if missing:
        raise RuntimeError("Nach der Installation fehlen noch: " + ", ".join(missing))
    prog(0.98, "Prüfe PyTorch …")                    # Importtest im eigenen Prozess: fängt fehlende DLLs ab
    r = subprocess.run([console_python(), "-c", "import torch, cv2, ultralytics; print(torch.__version__)"], capture_output=True,
                       text=True, timeout=300, creationflags=NO_WINDOW)
    if r.returncode:
        raise RuntimeError("PyTorch lässt sich nicht laden: " + (r.stderr or r.stdout).strip().splitlines()[-1][:300])
    return f"SkyTrace-Engine installiert (torch {_dist_version('torch')}, CPU)."


# --------------------------------------------------------------------------- #
# Herunterladen (mit Fortsetzen)
# --------------------------------------------------------------------------- #
def download(url: str, prog, to: Path | None = None, resume: bool = False) -> bytes | None:
    """Datei laden. Mit `to` + `resume` wird ein vorhandener Teil-Download per Range fortgesetzt (Abbruch, Netzwechsel)."""
    import httpx
    have = to.stat().st_size if (to and resume and to.exists()) else 0
    headers = {"User-Agent": "AstroFlow"}
    if have:
        headers["Range"] = f"bytes={have}-"
    with httpx.stream("GET", url, follow_redirects=True, timeout=httpx.Timeout(60, read=120), headers=headers) as r:
        if r.status_code == 416 and have:             # schon vollständig
            prog(1.0)
            return None
        r.raise_for_status()
        if "text/html" in (r.headers.get("content-type") or ""):
            raise RuntimeError("Download-Seite statt Datei erhalten – Quelle nicht erreichbar?")
        append = have and r.status_code == 206
        total = int(r.headers.get("content-length") or 0) + (have if append else 0)
        got = have if append else 0
        buf = open(to, "ab" if append else "wb") if to else io.BytesIO()
        try:
            for chunk in r.iter_bytes(1 << 20):
                buf.write(chunk)
                got += len(chunk)
                if total:
                    prog(min(1.0, got / total))
        finally:
            if to:
                buf.close()
        return None if to else buf.getvalue()


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
