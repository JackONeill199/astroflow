"""Windows-Paket bauen: portables ZIP und (mit Inno Setup) Installer.

    python installer/build_windows.py [--out dist] [--no-installer]

Ablauf: eigenständiges Python (python-build-standalone, fest gewählte Version) laden und entpacken → AstroFlow-Wheel aus diesem
Checkout bauen und samt Abhängigkeiten hineininstallieren → Starter (`AstroFlow.cmd`, portabel) und Symbol dazu → ZIP.
Mit `ISCC.exe` (Inno Setup 6, auf GitHub-Windows-Runnern vorinstalliert; sonst `ISCC`-Umgebungsvariable) zusätzlich
`AstroFlow-Setup-<Version>.exe` (Installation pro Nutzer, ohne Administratorrechte).

Nicht enthalten (Lizenz bzw. Größe, Download auf Knopfdruck im Einrichtungsassistenten): ASTAP + Sterndatenbank, GraXpert, StarNet,
SkyTrace-Analyse-Engine.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import os
import re
import shutil
import subprocess
import sys
import tarfile
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HERE = Path(__file__).resolve().parent
# Fest gewählt (reproduzierbar); bei Bedarf bewusst anheben: https://github.com/astral-sh/python-build-standalone/releases
PBS_TAG = "20261021"
PBS_PY = "3.12.15"
PBS_FILE = f"cpython-{PBS_PY}+{PBS_TAG}-x86_64-pc-windows-msvc-install_only_stripped.tar.gz"
PBS_URL = f"https://github.com/astral-sh/python-build-standalone/releases/download/{PBS_TAG}/{PBS_FILE.replace('+', '%2B')}"
# Aus SHA256SUMS des Releases, beim Festlegen gegen die geladene Datei nachgerechnet (07.11.2026). Beim Anheben mit ändern.
PBS_SHA256 = "aac5d331fc3307343f95928fdbea4583f0d53973d08c7e1d081884fbd229a0e1"


def version() -> str:
    return re.search(r'__version__\s*=\s*"([^"]+)"', (ROOT / "astroflow" / "__init__.py").read_text(encoding="utf-8")).group(1)


def log(*a):
    print("[build]", *a, flush=True)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def fetch(url: str, cache: Path, sha256: str) -> Path:
    """Datei laden (bzw. aus dem Zwischenspeicher) und gegen die feste Prüfsumme prüfen – sonst Abbruch."""
    cache.mkdir(parents=True, exist_ok=True)
    dest = cache / url.rsplit("/", 1)[-1].replace("%2B", "+")
    if dest.exists() and _sha256(dest) != sha256:
        log("Zwischenspeicher passt nicht zur Prüfsumme – lade neu:", dest.name)
        dest.unlink()
    if not dest.exists():
        log("Lade", url)
        tmp = dest.with_suffix(".part")
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "AstroFlow-Build"}), timeout=120) as r, open(tmp, "wb") as f:
            if not r.geturl().startswith("https://"):
                raise SystemExit(f"Unsichere Weiterleitung beim Laden von {url}")
            shutil.copyfileobj(r, f, 1 << 20)
        got = _sha256(tmp)
        if got != sha256:
            tmp.unlink(missing_ok=True)
            raise SystemExit(f"Prüfsumme von {dest.name} stimmt nicht ({got} statt {sha256}) – Abbruch.")
        tmp.replace(dest)
    return dest


def make_icon(path: Path):
    """App-Symbol (wie static/icon.svg: dunkles Quadrat, schräge Galaxie, helle Sterne) als .ico zeichnen – ohne SVG-Renderer."""
    from PIL import Image, ImageDraw, ImageFilter
    n = 256
    img = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((0, 0, n - 1, n - 1), radius=56, fill=(10, 13, 22, 255))
    glow = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    g = ImageDraw.Draw(glow)
    for i in range(40, 0, -1):               # radialer Verlauf #c7d6ff → #6d8dff → transparent
        t = i / 40
        col = (int(199 * (1 - t) + 109 * t), int(214 * (1 - t) + 141 * t), 255, int(255 * (1 - t) ** 0.7))
        g.ellipse((128 - 100 * t, 128 - 40 * t, 128 + 100 * t, 128 + 40 * t), fill=col)
    glow = glow.rotate(30, resample=Image.BICUBIC).filter(ImageFilter.GaussianBlur(3))
    img.alpha_composite(glow)
    d = ImageDraw.Draw(img)
    for cx, cy, r, c in ((128, 128, 18, (255, 255, 255)), (56, 60, 6, (255, 255, 255)), (200, 192, 5, (255, 255, 255)), (196, 56, 7, (255, 217, 168))):
        d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=c + (255,))
    img.save(path, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])


def write_sandbox(out: Path, results: Path | None = None) -> Path:
    """Windows-Sandbox-Prüfung vorbereiten: `AstroFlow-Test.wsb` startet eine frische Sandbox (Netz an, für ASTAP), bindet den
    Paketordner `out` **schreibgeschützt** und einen leeren Ergebnisordner beschreibbar ein – sonst nichts (keine Arbeitsinstanz, keine
    echten Daten). Der Logonbefehl führt nur `pruefe-in-sandbox.cmd` aus: Werkzeug-Python aus dem ZIP entpacken (nicht gemessen), dann
    installer/pruefe_paket.py für den Installer (falls vorhanden) und das portable ZIP, Protokolle + `fertig.txt` im Ergebnisordner.
    Absolute Pfade → nur neben den Paketen, nie im Repo. Start: Doppelklick oder `WindowsSandbox.exe <wsb>`."""
    out = out.resolve()
    results = (results or out.parent / (out.name + "-sandbox-ergebnis")).resolve()
    results.mkdir(parents=True, exist_ok=True)
    for f in results.glob("*.txt"):
        f.unlink()
    shutil.copy2(HERE / "pruefe_paket.py", out / "pruefe_paket.py")
    pkg, res = r"C:\Pruefung\Paket", r"C:\Pruefung\Ergebnis"
    py = r"C:\Werkzeug\AstroFlow\python\python.exe"
    cmd = r"""@echo off
chcp 65001 >nul
cd /d "PKG"
for %%f in (AstroFlow-*-Windows-portabel.zip) do set ZIP=%%f
for %%f in (AstroFlow-Setup-*.exe) do set SETUP=%%f
powershell -NoProfile -Command "Expand-Archive -Force '%ZIP%' C:\Werkzeug" > "RES\werkzeug.txt" 2>&1
if not defined SETUP goto portabel
"PY" -I pruefe_paket.py --setup "%SETUP%" --voll >"RES\installer.txt" 2>&1
echo Exitcode %errorlevel% >> "RES\installer.txt"
rem Zum Ausprobieren von Hand (Symbol im Infobereich, Menü): danach noch einmal schlank installieren und starten
"%SETUP%" /VERYSILENT /SUPPRESSMSGBOXES /CURRENTUSER /NORESTART /TYPE=compact
start "" "%LOCALAPPDATA%\Programs\AstroFlow\python\pythonw.exe" -I -m astroflow start
:portabel
"PY" -I pruefe_paket.py "%ZIP%" --astap --autostart --dir C:\Sauber > "RES\portabel.txt" 2>&1
echo Exitcode %errorlevel% >> "RES\portabel.txt"
echo fertig > "RES\fertig.txt"
"""
    cmd = cmd.replace("PKG", pkg).replace("RES", res).replace("PY", py)
    (out / "pruefe-in-sandbox.cmd").write_text(cmd.replace("\n", "\r\n"), encoding="utf-8")
    wsb = out / "AstroFlow-Test.wsb"
    wsb.write_text(f"""<Configuration>
  <Networking>Enable</Networking>
  <MemoryInMB>8192</MemoryInMB>
  <ClipboardRedirection>Disable</ClipboardRedirection>
  <PrinterRedirection>Disable</PrinterRedirection>
  <MappedFolders>
    <MappedFolder><HostFolder>{out}</HostFolder><SandboxFolder>{pkg}</SandboxFolder><ReadOnly>true</ReadOnly></MappedFolder>
    <MappedFolder><HostFolder>{results}</HostFolder><SandboxFolder>{res}</SandboxFolder><ReadOnly>false</ReadOnly></MappedFolder>
  </MappedFolders>
  <LogonCommand><Command>{pkg}\\pruefe-in-sandbox.cmd</Command></LogonCommand>
</Configuration>
""", encoding="utf-8")
    return wsb


def build(out: Path, installer: bool = True, cache: Path | None = None) -> dict:
    ver = version()
    cache = cache or ROOT / "build" / "cache"
    stage = ROOT / "build" / "windows" / "AstroFlow"
    if stage.parent.exists():
        shutil.rmtree(stage.parent)
    stage.mkdir(parents=True)
    out.mkdir(parents=True, exist_ok=True)

    # 1) Python entpacken (Archiv enthält den Ordner python/)
    tgz = fetch(PBS_URL, cache, PBS_SHA256)
    log("Entpacke", tgz.name)
    with tarfile.open(tgz) as t:
        t.extractall(stage, filter="data")
    py = stage / "python" / "python.exe"
    assert py.is_file(), "python.exe fehlt im Archiv"

    # 2) Wheel bauen und hineininstallieren
    wheels = ROOT / "build" / "wheel"
    shutil.rmtree(wheels, ignore_errors=True)
    log("Baue AstroFlow-Wheel")
    subprocess.run([sys.executable, "-m", "pip", "wheel", "--no-deps", "-q", "-w", str(wheels), str(ROOT)], check=True)
    whl = next(wheels.glob("astroflow-*.whl"))
    log("Installiere", whl.name, "+ Abhängigkeiten")
    env = {**os.environ, "PIP_DISABLE_PIP_VERSION_CHECK": "1", "PYTHONNOUSERSITE": "1"}
    subprocess.run([str(py), "-m", "pip", "install", "--no-warn-script-location", "--no-cache-dir", "-q", str(whl)], check=True, env=env)
    shutil.rmtree(ROOT / "astroflow.egg-info", ignore_errors=True)     # Rest von setuptools im Checkout
    # Testordner von numpy/scipy (~80 MB) braucht zur Laufzeit niemand (astropy/tests dagegen schon – bleibt)
    site = stage / "python" / "Lib" / "site-packages"
    for pkg in ("numpy", "scipy"):
        for p in sorted((site / pkg).rglob("tests"), key=lambda p: len(p.parts)):
            if p.is_dir():
                shutil.rmtree(p, ignore_errors=True)
    # Eigener Paketordner für GPU/SkyTrace-Pakete (übersteht Updates): Haken per .pth, siehe installer/astroflow_pakete.py
    shutil.copy2(HERE / "astroflow_pakete.py", site / "astroflow_pakete.py")
    (site / "astroflow-pakete.pth").write_text("import astroflow_pakete\n", encoding="utf-8")

    # 3) Starter, Symbol, Lizenz
    make_icon(stage / "astroflow.ico")
    shutil.copy2(HERE / "AstroFlow.cmd", stage / "AstroFlow.cmd")
    shutil.copy2(HERE / "AstroFlow beenden.cmd", stage / "AstroFlow beenden.cmd")
    shutil.copy2(HERE / "LIESMICH.txt", stage / "LIESMICH.txt")
    shutil.copy2(ROOT / "LICENSE", stage / "LICENSE.txt")
    shutil.copy2(ROOT / "NOTICE", stage / "NOTICE.txt")

    # Selbsttest im Paket: Import + Version
    r = subprocess.run([str(py), "-I", "-c", "import astroflow, astroflow.server, astroflow.modules.stacking.pipeline, scipy.ndimage, scipy.optimize, "
                        "scipy.signal, scipy.spatial, astropy.wcs, astropy.coordinates, imagecodecs, tifffile, pystray, astroflow_pakete; print(astroflow.__version__)"],
                       capture_output=True, text=True, env=env)
    assert r.returncode == 0 and r.stdout.strip() == ver, r.stderr
    size = sum(f.stat().st_size for f in stage.rglob("*") if f.is_file())
    log(f"Paket: {size / 1e6:.0f} MB entpackt")

    # 4) Portables ZIP
    zpath = out / f"AstroFlow-{ver}-Windows-portabel.zip"
    log("Schreibe", zpath.name)
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for f in sorted(stage.rglob("*")):
            if f.is_file():
                z.write(f, Path("AstroFlow") / f.relative_to(stage))
    res = {"version": ver, "unpacked_mb": round(size / 1e6), "zip": str(zpath), "zip_mb": round(zpath.stat().st_size / 1e6)}

    # 5) Installer
    iscc = os.environ.get("ISCC") or next((p for p in (r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe", r"C:\Program Files\Inno Setup 6\ISCC.exe",
                                                       os.path.expandvars(r"%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe")) if os.path.isfile(p)), None)
    if installer and iscc:
        log("Baue Installer mit", iscc)
        subprocess.run([iscc, "/Q", f"/DAppVersion={ver}", f"/DSourceDir={stage}", f"/DOutDir={out.resolve()}", str(HERE / "astroflow.iss")], check=True)
        exe = out / f"AstroFlow-Setup-{ver}.exe"
        res.update(installer=str(exe), installer_mb=round(exe.stat().st_size / 1e6))
    elif installer:
        log("Inno Setup (ISCC.exe) nicht gefunden – nur portables ZIP gebaut.")
    write_sandbox(out)
    for k in ("zip", "installer"):
        if k in res:
            res[k + "_sha256"] = hashlib.sha256(Path(res[k]).read_bytes()).hexdigest()
    return res


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default=str(ROOT / "dist"))
    ap.add_argument("--no-installer", action="store_true")
    ap.add_argument("--sandbox-only", action="store_true", help="nur AstroFlow-Test.wsb für vorhandene Pakete in --out schreiben")
    a = ap.parse_args()
    if a.sandbox_only:
        sys.exit(print(write_sandbox(Path(a.out))))
    if os.name != "nt":
        sys.exit("Nur unter Windows (eingebettetes Windows-Python).")
    import json
    print(json.dumps(build(Path(a.out), not a.no_installer), indent=2))
