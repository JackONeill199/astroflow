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


def _baum(root):
    """Erfundener Stellina-Baum: Beobachtungsordner in Jahresordnern, einer zu tief für die Suche."""
    for rel in ("Stellina/2022/2022-05-06_21-41-48_observation_NGC4395/01-images-initial",
                "Stellina/2022/2022-05-06_22-12-15_observation_M3",
                "Stellina/2023/Fruehling/2023-04-01_20-00-00_observation_NGC_4395",
                "Stellina/a/b/c/d/2024-01-01_observation_NGC 4395",
                "Stellina/.versteckt/observation_NGC4395"):
        (root / rel).mkdir(parents=True)
    (root / "Stellina" / "ngc4395-notiz.txt").write_bytes(b"x")    # Datei, kein Treffer
    return root / "Stellina"


def test_search_folders_normalizes_and_limits(tmp_path):
    from astroflow.core.folders import norm, search_folders
    assert norm("NGC 4395") == norm("ngc_4395") == norm("Ngc-4395") == "ngc4395"
    base = _baum(tmp_path)
    for q in ("ngc4395", "NGC 4395", "NGC_4395", "ngc-4395"):
        r = search_folders(str(base), q)
        assert [h["rel"].replace("\\", "/") for h in r["hits"]] == [
            "2022/2022-05-06_21-41-48_observation_NGC4395",
            "2023/Fruehling/2023-04-01_20-00-00_observation_NGC_4395"], q    # Tiefe 5 und versteckte Ordner fehlen
        assert not r["truncated"] and r["depth"] == 4
    assert [h["depth"] for h in r["hits"]] == [2, 3]
    assert search_folders(str(base), "ngc4395", depth=5)["hits"][-1]["name"] == "2024-01-01_observation_NGC 4395"
    one = search_folders(str(base), "observation", limit=1)
    assert len(one["hits"]) == 1 and one["truncated"]
    assert search_folders(str(base), " - ")["hits"] == []                        # leerer Suchschlüssel
    with pytest.raises(ValueError):
        search_folders("", "m3")
    with pytest.raises(ValueError):
        search_folders(str(tmp_path / "gibtsnicht"), "m3")


def test_folders_search_endpoint(tmp_path):
    s = load_settings(tmp_path / "data")
    s.auto = False
    base = _baum(tmp_path)
    with TestClient(create_app(s)) as c:
        r = c.get("/api/folders/search", params={"path": str(base), "q": "m 3"}).json()
        assert [h["name"] for h in r["hits"]] == ["2022-05-06_22-12-15_observation_M3"]
        assert r["hits"][0]["path"] == str(base.resolve() / "2022" / "2022-05-06_22-12-15_observation_M3")
        assert c.get("/api/folders/search", params={"path": str(tmp_path / "nein"), "q": "x"}).status_code == 400


def test_search_budget_inside_large_folder(tmp_path, monkeypatch):
    """Budget greift auch mitten in einem großen Ordner (je Eintrag, Dateien zählen mit) – Ergebnis als begrenzt markiert."""
    from astroflow.core import folders
    for i in range(300):
        (tmp_path / f"obs_{i:03d}").mkdir()
    monkeypatch.setattr(folders, "SEARCH_VISIT", 128)
    r = folders.search_folders(str(tmp_path), "obs", depth=1)
    assert r["truncated"] and 0 < len(r["hits"]) < 300 and r["visited"] <= 129
    monkeypatch.setattr(folders, "SEARCH_VISIT", 10 ** 6)
    monkeypatch.setattr(folders, "SEARCH_SECONDS", -1.0)                       # Zeit sofort abgelaufen
    r = folders.search_folders(str(tmp_path), "obs", depth=1)
    assert r["truncated"] and r["hits"] == []
