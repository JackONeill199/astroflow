"""Linux-Paket bauen: `AstroFlow-<Version>-Linux-x86_64.tar.gz` (portabel + `install.sh` für die Installation pro Nutzer).

    python installer/build_linux.py [--out dist]

Ablauf wie beim Windows-Paket (gemeinsame Teile aus build_windows.py): eigenständiges Python (python-build-standalone, fest gewählte
Version, feste SHA-256) laden und entpacken → AstroFlow-Wheel aus diesem Checkout samt Abhängigkeiten (manylinux-Wheels) hinein →
Starter (`AstroFlow`, `AstroFlow-beenden`), `install.sh`/`uninstall.sh` + `linux_install.py`, Symbol (PNG), Lizenzen → tar.gz.
Läuft unter Linux x86_64 mit glibc ≥ 2.28 (Wheels von numpy/scipy/imagecodecs). aarch64 (Raspberry Pi 5) kommt später.

Nicht enthalten (Lizenz bzw. Größe, Download auf Knopfdruck im Einrichtungsassistenten): ASTAP + Sterndatenbank, GraXpert, StarNet,
SkyTrace-Analyse-Engine.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from build_windows import ROOT, PBS_PY, PBS_TAG, collect_licenses, fetch, log, make_icon, version  # noqa: E402

ARCH = "x86_64"
PBS_FILE = f"cpython-{PBS_PY}+{PBS_TAG}-{ARCH}-unknown-linux-gnu-install_only_stripped.tar.gz"
PBS_URL = f"https://github.com/astral-sh/python-build-standalone/releases/download/{PBS_TAG}/{PBS_FILE.replace('+', '%2B')}"
# Aus SHA256SUMS des Releases (stimmt mit der Angabe von GitHub überein), 12.11.2026. Beim Anheben von PBS_TAG/PBS_PY mit ändern.
PBS_SHA256 = "6d7521138fd33afddc1ccd08abb007d9a2829e38117c2576f9efdc0ec73ce772"
PYV = ".".join(PBS_PY.split(".")[:2])                      # 3.12
SCRIPTS = {"AstroFlow": 0o755, "AstroFlow-beenden": 0o755, "install.sh": 0o755, "uninstall.sh": 0o755, "LIESMICH.txt": 0o644}


def _copy_text(src: Path, dest: Path, mode: int):
    """Skripte immer mit LF (ein Windows-Checkout mit autocrlf hätte CRLF – dann liefe /bin/sh nicht)."""
    dest.write_bytes(src.read_bytes().replace(b"\r\n", b"\n"))
    dest.chmod(mode)


def _tar_filter(info: tarfile.TarInfo) -> tarfile.TarInfo:
    info.uid = info.gid = 0
    info.uname = info.gname = ""
    return info


def build(out: Path, cache: Path | None = None) -> dict:
    ver = version()
    cache = cache or ROOT / "build" / "cache"
    stage = ROOT / "build" / "linux" / "AstroFlow"
    if stage.parent.exists():
        shutil.rmtree(stage.parent)
    stage.mkdir(parents=True)
    out.mkdir(parents=True, exist_ok=True)

    # 1) Python entpacken (Archiv enthält den Ordner python/)
    tgz = fetch(PBS_URL, cache, PBS_SHA256)
    log("Entpacke", tgz.name)
    with tarfile.open(tgz) as t:
        t.extractall(stage, filter="tar")                  # „tar“ statt „data“: Symlinks im bin/-Ordner (python3 → python3.12) bleiben
    py = stage / "python" / "bin" / "python3"
    assert py.is_file(), "python/bin/python3 fehlt im Archiv"

    # 2) Wheel bauen und hineininstallieren
    wheels = ROOT / "build" / "wheel"
    shutil.rmtree(wheels, ignore_errors=True)
    log("Baue AstroFlow-Wheel")
    subprocess.run([sys.executable, "-m", "pip", "wheel", "--no-deps", "-q", "-w", str(wheels), str(ROOT)], check=True)
    whl = next(wheels.glob("astroflow-*.whl"))
    log("Installiere", whl.name, "+ Abhängigkeiten")
    env = {**os.environ, "PIP_DISABLE_PIP_VERSION_CHECK": "1", "PYTHONNOUSERSITE": "1"}
    subprocess.run([str(py), "-m", "pip", "install", "--no-warn-script-location", "--no-cache-dir", "--only-binary=:all:", "-q", str(whl)],
                   check=True, env=env)
    shutil.rmtree(ROOT / "astroflow.egg-info", ignore_errors=True)
    site = stage / "python" / "lib" / f"python{PYV}" / "site-packages"
    for pkg in ("numpy", "scipy"):                         # Testordner (~80 MB) braucht zur Laufzeit niemand
        for p in sorted((site / pkg).rglob("tests"), key=lambda p: len(p.parts)):
            if p.is_dir():
                shutil.rmtree(p, ignore_errors=True)
    # Eigener Paketordner für GPU/SkyTrace-Pakete (übersteht Updates): Haken per .pth, siehe installer/astroflow_pakete.py
    shutil.copy2(HERE / "astroflow_pakete.py", site / "astroflow_pakete.py")
    (site / "astroflow-pakete.pth").write_text("import astroflow_pakete\n", encoding="utf-8")
    for f in (stage / "python" / "bin").glob("*"):          # pip-Skripte mit absolutem Bau-Pfad im #! würden nach dem Entpacken nicht laufen
        if f.is_file() and not f.is_symlink() and not f.name.startswith("python") and f.read_bytes()[:2] == b"#!":
            f.unlink()

    # 3) Starter, Installation, Symbol, Lizenzen
    for name, mode in SCRIPTS.items():
        _copy_text(HERE / "linux" / name, stage / name, mode)
    shutil.copy2(HERE / "linux_install.py", stage / "linux_install.py")
    make_icon(stage / "astroflow.png")
    shutil.copy2(ROOT / "LICENSE", stage / "LICENSE.txt")
    shutil.copy2(ROOT / "NOTICE", stage / "NOTICE.txt")
    (stage / "THIRD-PARTY-LICENSES.txt").write_text(
        collect_licenses(stage / "python", ver, site=site, py_license=stage / "python" / "lib" / f"python{PYV}" / "LICENSE.txt"),
        encoding="utf-8")

    # Selbsttest im Paket: Import + Version (mit -I wie die Starter)
    r = subprocess.run([str(py), "-I", "-c", "import astroflow, astroflow.server, astroflow.modules.stacking.pipeline, scipy.ndimage, scipy.optimize, "
                        "scipy.signal, scipy.spatial, astropy.wcs, astropy.coordinates, imagecodecs, tifffile, astroflow_pakete, "
                        "astroflow.core.firewall; print(astroflow.__version__)"], capture_output=True, text=True, env=env)
    assert r.returncode == 0 and r.stdout.strip() == ver, r.stderr
    size = sum(f.stat().st_size for f in stage.rglob("*") if f.is_file() and not f.is_symlink())
    log(f"Paket: {size / 1e6:.0f} MB entpackt")

    # 4) tar.gz (Rechte bleiben erhalten, Eigentümer neutral)
    tpath = out / f"AstroFlow-{ver}-Linux-{ARCH}.tar.gz"
    log("Schreibe", tpath.name)
    with tarfile.open(tpath, "w:gz", compresslevel=6) as t:
        t.add(stage, arcname="AstroFlow", filter=_tar_filter)
    sha = hashlib.sha256(tpath.read_bytes()).hexdigest()
    (out / (tpath.name + ".sha256")).write_text(f"{sha}  {tpath.name}\n", encoding="utf-8")
    return {"version": ver, "unpacked_mb": round(size / 1e6), "tar": str(tpath), "tar_mb": round(tpath.stat().st_size / 1e6), "tar_sha256": sha}


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default=str(ROOT / "dist"))
    a = ap.parse_args()
    if not sys.platform.startswith("linux"):
        sys.exit("Nur unter Linux (Paket mit Linux-Python und manylinux-Wheels).")
    import json
    print(json.dumps(build(Path(a.out)), indent=2))
