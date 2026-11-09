"""Sammelfunktion für THIRD-PARTY-LICENSES.txt im Windows-Paket (installer/build_windows.py) – mit künstlichem site-packages."""
import importlib.util
from pathlib import Path

SPEC = importlib.util.spec_from_file_location("build_windows", Path(__file__).resolve().parent.parent / "installer" / "build_windows.py")
bw = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bw)


def _paket(site: Path, name: str, version: str, lic: str, text: str | None, sub: str = ""):
    d = site / f"{name}-{version}.dist-info"
    (d / sub).mkdir(parents=True)
    (d / "METADATA").write_text(f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\nLicense: {lic}\n", encoding="utf-8")
    if text is not None:
        (d / sub / "LICENSE").write_text(text, encoding="utf-8")


def test_collect_licenses(tmp_path):
    py = tmp_path / "python"
    site = py / "Lib" / "site-packages"
    site.mkdir(parents=True)
    (py / "LICENSE.txt").write_text("PSF LICENSE AGREEMENT TEXT", encoding="utf-8")
    _paket(site, "pystray", "0.19.5", "LGPL-3.0", "GNU LESSER GENERAL PUBLIC LICENSE Version 3")
    _paket(site, "numpy", "2.0.0", "BSD-3-Clause", "Copyright numpy", sub="licenses")
    _paket(site, "ohnelizenz", "1.0", "MIT", None)
    txt = bw.collect_licenses(py, "9.9.9")
    assert "AstroFlow 9.9.9" in txt and "PSF LICENSE AGREEMENT TEXT" in txt
    assert "pystray 0.19.5 – LGPL-3.0" in txt and "GNU LESSER GENERAL PUBLIC LICENSE" in txt
    assert "Copyright numpy" in txt and "licenses/LICENSE" in txt
    assert "(keine Lizenzdatei im Paket)" in txt
    assert txt.index("numpy 2.0.0") < txt.index("ohnelizenz") < txt.index("pystray 0.19.5")


def test_collect_licenses_ohne_python_lizenz(tmp_path):
    (tmp_path / "python" / "Lib" / "site-packages").mkdir(parents=True)
    assert "fehlt" in bw.collect_licenses(tmp_path / "python")
