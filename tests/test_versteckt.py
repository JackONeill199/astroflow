"""Versteckte Dateien/Ordner (Name mit „.“, unter Windows Attribut „versteckt“) werden nirgends angezeigt oder eingelesen.
Anlass: Stellina-Platte mit `.thumbnail`, `.webaxs_L`, `.webview` … – tauchten als „Fertige Bilder · neu“ auf."""
import os
import subprocess

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from astroflow.core.config import load_settings
from astroflow.core.folders import list_folders, search_folders
from astroflow.modules.stacking import library, scanner
from astroflow.server import create_app


def _platte(root):
    obs = root / "2022-07-18_21-45-37_observation_M57"
    (obs / "01-images-initial").mkdir(parents=True)
    (obs / "01-images-initial" / "img-0001r.fits").write_bytes(b"")
    (obs / "01-images-initial" / ".img-0002r.fits").write_bytes(b"")          # versteckte Datei
    (obs / ".cache").mkdir()
    (obs / ".cache" / "img-0003r.fits").write_bytes(b"")                     # versteckter Unterordner
    for d in (".thumbnail", ".webaxs_L", ".webview"):
        (root / d).mkdir()
        for i in range(3):
            Image.new("RGB", (8, 6)).save(root / d / f"bild{i}.jpg")
    (root / "fertig").mkdir()
    Image.new("RGB", (8, 6)).save(root / "fertig" / "m42.jpg")
    Image.new("RGB", (8, 6)).save(root / "fertig" / ".m42-vorschau.jpg")
    return obs


def test_scanner_ignores_dot_entries(tmp_path):
    _platte(tmp_path)
    found = [p.relative_to(tmp_path).as_posix() for p in scanner.list_fits(tmp_path)]
    assert found == ["2022-07-18_21-45-37_observation_M57/01-images-initial/img-0001r.fits"]


def test_library_groups_ignore_dot_entries(tmp_path):
    _platte(tmp_path)
    groups = {g["dir"].name: g for g in library.find_groups(tmp_path)}
    assert set(groups) == {"2022-07-18_21-45-37_observation_M57", "fertig"}
    assert [f.name for f in groups["2022-07-18_21-45-37_observation_M57"]["files"]] == ["img-0001r.fits"]
    assert [f.name for f in groups["fertig"]["files"]] == ["m42.jpg"]


def test_chosen_hidden_root_is_still_read(tmp_path):
    """Wer bewusst einen versteckten Ordner als Startordner wählt, bekommt dessen Inhalt – nur darunter wird gefiltert."""
    root = tmp_path / ".stellina"
    (root / "nacht").mkdir(parents=True)
    (root / "nacht" / "img-0001r.fits").write_bytes(b"")
    assert [p.name for p in scanner.list_fits(root)] == ["img-0001r.fits"]
    assert [g["dir"].name for g in library.find_groups(root)] == ["nacht"]


def test_folder_dialog_ignores_dot_entries(tmp_path):
    _platte(tmp_path)
    r = list_folders(str(tmp_path))
    assert [f["name"] for f in r["folders"]] == ["2022-07-18_21-45-37_observation_M57", "fertig"]
    assert list_folders(str(tmp_path / "fertig"))["pictures"] == 1
    assert search_folders(str(tmp_path), "webaxs")["hits"] == []


@pytest.mark.skipif(os.name != "nt", reason="Attribut „versteckt“ gibt es nur unter Windows")
def test_windows_hidden_attribute(tmp_path):
    (tmp_path / "Vorschau").mkdir()
    Image.new("RGB", (8, 6)).save(tmp_path / "Vorschau" / "a.jpg")
    (tmp_path / "nacht").mkdir()
    (tmp_path / "nacht" / "img-0001r.fits").write_bytes(b"")
    (tmp_path / "nacht" / "img-0002r.fits").write_bytes(b"")
    subprocess.run(["attrib", "+h", str(tmp_path / "Vorschau")], check=True)
    subprocess.run(["attrib", "+h", str(tmp_path / "nacht" / "img-0002r.fits")], check=True)
    assert [f["name"] for f in list_folders(str(tmp_path))["folders"]] == ["nacht"]
    assert [p.name for p in scanner.list_fits(tmp_path)] == ["img-0001r.fits"]
    assert [g["dir"].name for g in library.find_groups(tmp_path)] == ["nacht"]


def test_upload_skips_dot_entries(tmp_path):
    s = load_settings(tmp_path / "data")
    s.auto = False
    with TestClient(create_app(s)) as c:
        files = [("files", ("obs_observation_M57/img-0001r.fits", b"SIMPLE")),
                 ("files", ("obs_observation_M57/.cache/img-0002r.fits", b"SIMPLE")),
                 ("files", (".thumbnail/bild.jpg", b"x"))]
        r = c.post("/api/stacking/upload", files=files, data={"upload_id": "abcdef123456"}).json()
        assert r["raw"] == 1 and r["skipped"] == 2 and not r["pictures"]
        saved = [p.name for p in (s.input_dir / "uploads").rglob("*") if p.is_file()]
        assert saved == ["img-0001r.fits"]
