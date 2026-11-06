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


def version() -> str:
    return re.search(r'__version__\s*=\s*"([^"]+)"', (ROOT / "astroflow" / "__init__.py").read_text(encoding="utf-8")).group(1)


def log(*a):
    print("[build]", *a, flush=True)


def fetch(url: str, cache: Path) -> Path:
    cache.mkdir(parents=True, exist_ok=True)
    dest = cache / url.rsplit("/", 1)[-1].replace("%2B", "+")
    if not dest.exists():
        log("Lade", url)
        tmp = dest.with_suffix(".part")
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "AstroFlow-Build"}), timeout=120) as r, open(tmp, "wb") as f:
            shutil.copyfileobj(r, f, 1 << 20)
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


def write_sandbox(out: Path, zpath: Path):
    """Windows-Sandbox-Konfiguration neben die Pakete legen: Doppelklick auf `AstroFlow-Test.wsb` startet eine frische Sandbox,
    die das portable Paket mit installer/pruefe_paket.py prüft (Python aus dem Paket selbst) – Ergebnis in `sandbox-ergebnis.txt`.
    Der Installer liegt danach zum Ausprobieren auf dem Sandbox-Desktop. Pfad absolut, deshalb nicht im Repo."""
    shutil.copy2(HERE / "pruefe_paket.py", out / "pruefe_paket.py")
    box = r"C:\Users\WDAGUtilityAccount\Desktop\AstroFlow-Pruefung"
    (out / "pruefe-in-sandbox.cmd").write_text("\r\n".join([
        "@echo off", "chcp 65001 >nul", f'cd /d "{box}"',
        f'powershell -NoProfile -Command "Expand-Archive -Force \'{zpath.name}\' C:\\Entpackt"',
        f'C:\\Entpackt\\AstroFlow\\python\\python.exe -I pruefe_paket.py "{zpath.name}" --astap --dir C:\\Sauber > sandbox-ergebnis.txt 2>&1',
        "type sandbox-ergebnis.txt", "pause", ""]), encoding="utf-8")
    (out / "AstroFlow-Test.wsb").write_text(f"""<Configuration>
  <MappedFolders>
    <MappedFolder><HostFolder>{out.resolve()}</HostFolder><SandboxFolder>{box}</SandboxFolder><ReadOnly>false</ReadOnly></MappedFolder>
  </MappedFolders>
  <LogonCommand><Command>cmd /c start "" "{box}\\pruefe-in-sandbox.cmd"</Command></LogonCommand>
  <MemoryInMB>6144</MemoryInMB>
</Configuration>
""", encoding="utf-8")


def build(out: Path, installer: bool = True, cache: Path | None = None) -> dict:
    ver = version()
    cache = cache or ROOT / "build" / "cache"
    stage = ROOT / "build" / "windows" / "AstroFlow"
    if stage.parent.exists():
        shutil.rmtree(stage.parent)
    stage.mkdir(parents=True)
    out.mkdir(parents=True, exist_ok=True)

    # 1) Python entpacken (Archiv enthält den Ordner python/)
    tgz = fetch(PBS_URL, cache)
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

    # 3) Starter, Symbol, Lizenz
    make_icon(stage / "astroflow.ico")
    shutil.copy2(HERE / "AstroFlow.cmd", stage / "AstroFlow.cmd")
    shutil.copy2(HERE / "AstroFlow beenden.cmd", stage / "AstroFlow beenden.cmd")
    shutil.copy2(HERE / "LIESMICH.txt", stage / "LIESMICH.txt")
    shutil.copy2(ROOT / "LICENSE", stage / "LICENSE.txt")

    # Selbsttest im Paket: Import + Version
    r = subprocess.run([str(py), "-I", "-c", "import astroflow, astroflow.server, astroflow.modules.stacking.pipeline, scipy.ndimage, scipy.optimize, "
                        "scipy.signal, scipy.spatial, astropy.wcs, astropy.coordinates, imagecodecs, tifffile; print(astroflow.__version__)"],
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
        subprocess.run([iscc, "/Q", f"/DAppVersion={ver}", f"/DSourceDir={stage}", f"/DOutDir={out}", str(HERE / "astroflow.iss")], check=True)
        exe = out / f"AstroFlow-Setup-{ver}.exe"
        res.update(installer=str(exe), installer_mb=round(exe.stat().st_size / 1e6))
    elif installer:
        log("Inno Setup (ISCC.exe) nicht gefunden – nur portables ZIP gebaut.")
    write_sandbox(out, zpath)
    for k in ("zip", "installer"):
        if k in res:
            res[k + "_sha256"] = hashlib.sha256(Path(res[k]).read_bytes()).hexdigest()
    return res


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default=str(ROOT / "dist"))
    ap.add_argument("--no-installer", action="store_true")
    a = ap.parse_args()
    if os.name != "nt":
        sys.exit("Nur unter Windows (eingebettetes Windows-Python).")
    import json
    print(json.dumps(build(Path(a.out), not a.no_installer), indent=2))
