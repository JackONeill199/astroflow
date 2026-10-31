"""Hauptordner durchsuchen und ausgewählte Gruppen importieren."""
import time

from fastapi.testclient import TestClient
from PIL import Image

from astroflow.core.config import load_settings
from astroflow.modules.stacking import demo
from astroflow.server import create_app


def _wait(c, want=("done", "error"), t=60):
    for _ in range(int(t / 0.1)):
        st = c.get("/api/stacking/library").json()
        if st["state"] in want:
            return st
        time.sleep(0.1)
    raise AssertionError(st)


def test_library_scan_and_import(tmp_path):
    root = tmp_path / "Platte"
    demo.generate(root / "2026-08-12_observation_M31", n_lights=6, n_darks=2, n_flats=2)
    demo.generate_mono(root / "mono" / "nacht", bands=("L",), per_band=3)
    (root / "fertig").mkdir(parents=True)
    Image.new("RGB", (32, 24), (90, 40, 20)).save(root / "fertig" / "m42.png")
    s = load_settings(tmp_path / "d")
    s.auto = False
    with TestClient(create_app(s)) as c:
        assert c.post("/api/stacking/library/scan", json={"root": str(root / "gibt-es-nicht")}).status_code == 400
        assert c.post("/api/stacking/library/scan", json={"root": str(root)}).status_code == 200
        st = _wait(c)
        groups = {g["name"]: g for g in st["groups"]}
        assert st["state"] == "done" and len(groups) == 3, groups.keys()
        assert all(g["state"] == "new" for g in groups.values())
        obs = groups["M31"] if "M31" in groups else next(g for g in groups.values() if g["rel"].endswith("M31"))
        assert obs["kind"] == "lights" and obs["n_files"] == 6 + 2 + 2
        assert c.get("/api/stacking/projects").json() == []           # Suchen legt nichts an

        mono = next(g for g in st["groups"] if g["kind"] == "lights" and g is not obs and g["n_files"] == 3)
        assert c.post("/api/stacking/library/import", json={"ids": []}).status_code == 400
        assert c.post("/api/stacking/library/import", json={"ids": [mono["id"]]}).status_code == 200
        st = _wait(c)
        assert st["imported"]["raw"] == 3 and not st["imported"]["errors"], st
        ps = c.get("/api/stacking/projects").json()
        assert len(ps) == 1 and ps[0]["total"] == 3
        assert list((s.input_dir / "uploads" / "bibliothek").rglob("*.fit"))
        assert list(root.rglob("M31_L_000.fit"))                      # Quelle bleibt

        # erneut suchen: importierte Gruppe ist bekannt (noch nicht gestackt), der Rest weiterhin neu
        c.post("/api/stacking/library/scan", json={"root": str(root)})
        st = _wait(c)
        by = {g["id"]: g for g in st["groups"]}
        assert sum(g["state"] == "unstacked" for g in by.values()) == 1
        assert sum(g["state"] == "new" for g in by.values()) == 2
        unst = next(g for g in by.values() if g["state"] == "unstacked")
        assert unst["project"]["id"] == ps[0]["id"]

        # gestackt -> fertig
        from astroflow.modules.stacking.db import DB
        DB(s.db_path).x("UPDATE frames SET stacked=1 WHERE kind='light'")
        c.post("/api/stacking/library/scan", json={"root": str(root)})
        st = _wait(c)
        assert next(g for g in st["groups"] if g["id"] == unst["id"])["state"] == "done"

        # Rest (Rohdaten + Bild) importieren
        rest = [g["id"] for g in st["groups"] if g["state"] == "new"]
        c.post("/api/stacking/library/import", json={"ids": rest})
        st = _wait(c)
        assert st["imported"]["raw"] == 10 and st["imported"]["pictures"] == 1, st
        names = [p["name"] for p in c.get("/api/stacking/projects").json()]
        assert len(names) == 3 and "M42 Orionnebel" in names, names      # M31 je Kamera ein Projekt + importiertes Bild
