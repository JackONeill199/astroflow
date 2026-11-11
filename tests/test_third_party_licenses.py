"""Sammelfunktion für THIRD-PARTY-LICENSES.txt im Windows-Paket (installer/build_windows.py) – mit künstlichem site-packages."""
import importlib.util
import re
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


def test_lizenzseite_installer():
    """Installer-Lizenzseite: deutsche Kurzfassung, Originaltext ohne Markdown, Wortlaut und Required Notice erhalten."""
    md = (Path(__file__).resolve().parent.parent / "LICENSE").read_text(encoding="utf-8")
    txt = bw.license_page(md)
    kurz, original = txt.split("_" * 78)
    assert "Lizenz in Kürze" in kurz and "nicht-kommerzielle" in kurz and "englische Originaltext" in kurz
    assert not any(z in original for z in "#*<>[]")
    assert "Required Notice: Copyright" in original and "https://polyformproject.org/licenses/noncommercial/1.0.0" in original
    # Wortlaut unverändert: ohne Markdown-Zeichen und Leerraum identisch
    rein = lambda s: "".join(c for c in s if c.isalnum())
    ohne_md = rein(re.sub(r"\]\(#[^)]*\)", "", md))
    assert rein(original) == ohne_md
