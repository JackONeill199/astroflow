"""Ordnerdialog: Server listet Ordner (keine Dateien) und zählt Bilddateien im aktuellen Ordner."""
import pytest
from fastapi.testclient import TestClient

from astroflow.core.config import load_settings
from astroflow.core.folders import list_folders
from astroflow.server import create_app


def test_list_folders(tmp_path):
    (tmp_path / "b_obs").mkdir()
    (tmp_path / "A_obs").mkdir()
    (tmp_path / ".versteckt").mkdir()
    (tmp_path / "$RECYCLE.BIN").mkdir()
    for n in ("img-0001r.fits", "img-0002r.FIT", "vorschau.jpg", "notiz.txt"):
        (tmp_path / n).write_bytes(b"x")
    r = list_folders(str(tmp_path))
    assert [f["name"] for f in r["folders"]] == ["A_obs", "b_obs"]
    assert r["fits"] == 2 and r["pictures"] == 1
    assert r["parent"] == str(tmp_path.parent) and r["drives"]
    root = list_folders("")
    assert root["path"] == "" and root["folders"] and root["parent"] is None
    with pytest.raises(ValueError):
        list_folders(str(tmp_path / "gibtsnicht"))


def test_folders_endpoint(tmp_path):
    s = load_settings(tmp_path / "data")
    s.auto = False
    (tmp_path / "Stellina").mkdir()
    with TestClient(create_app(s)) as c:
        r = c.get("/api/folders", params={"path": str(tmp_path)}).json()
        assert "Stellina" in [f["name"] for f in r["folders"]]
        assert c.get("/api/folders", params={"path": str(tmp_path / "nein")}).status_code == 400
