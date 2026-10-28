"""Sage: Bildbestimmung → Geschichte → Verlauf/Entdeckungen (Solver und KI sind Attrappen, kein Netz)."""
import io
import time

import numpy as np
from fastapi.testclient import TestClient
from PIL import Image

from astroflow.core.config import load_settings
from astroflow.modules.sage import clients
from astroflow.modules.sage.store import SageStore, normalize, plain
from astroflow.modules.stacking import services
from astroflow.server import create_app


class FakeSolver:
    available = True

    def solve(self, path, filename=None, hint=None):
        return {"ra": 83.82, "dec": -5.39, "scale": 2.0, "rotation": 0, "fov_w": 1.0, "fov_h": 0.7, "width": 64, "height": 48,
                "solver": "astap", "objects": [{"name": "M42 Orionnebel", "kind_label": "Emissionsnebel", "x": 30, "y": 20, "radius": 9},
                                               {"name": "NGC 1977", "kind_label": "Reflexionsnebel", "x": 32, "y": 8, "radius": 5}]}


def _jpg():
    b = io.BytesIO()
    Image.fromarray((np.random.default_rng(1).random((48, 64)) * 255).astype(np.uint8)).save(b, "JPEG")
    return b.getvalue()


def _wait(c, tid):
    for _ in range(100):
        t = c.get(f"/api/sage/tasks/{tid}").json()
        if t["state"] != "running":
            return t
        time.sleep(0.05)
    raise AssertionError("Auftrag hängt")


def test_analyse_geschichte_entdeckungen(tmp_path, monkeypatch):
    monkeypatch.setenv("ASTROFLOW_MODULES", "stacking,sage")
    seen = {}

    def story(cfg, data):
        seen["prompt"] = clients.story_prompt(data)
        return "## Die Wiege der Sterne\n\nIm **Orionnebel** entstehen Sterne.\n\n*Schau hin.*"

    monkeypatch.setattr(clients, "write_story", story)
    app = create_app(load_settings(tmp_path))
    app.state.ctx.services["plate_solver"] = FakeSolver()
    with TestClient(app) as c:
        assert c.post("/api/sage/analyze", files={"file": ("notiz.txt", b"x")}).status_code == 400
        c.post("/api/sage/config", json={"story_provider": "lmstudio"})            # lokal: kein Schlüssel nötig
        t = _wait(c, c.post("/api/sage/analyze", files={"file": ("orion.jpg", _jpg(), "image/jpeg")}).json()["id"])
        assert t["state"] == "done" and t["discovery"]["new"] == ["M42 Orionnebel", "NGC 1977"]
        assert {a["id"] for a in t["discovery"]["achievements"]} >= {"first_star", "messier_first", "ngc_first"}
        assert "M42 Orionnebel (Emissionsnebel)" in seen["prompt"]
        e = c.get(f"/api/sage/history/{t['entry']}").json()
        assert e["title"] == "Die Wiege der Sterne" and e["image"].endswith("-preview.jpg") and "file" not in e
        assert c.get(e["image"]).status_code == 200
        assert c.get("/api/sage/files/../settings.json").status_code == 404
        # zweite Analyse desselben Objekts: bekannt, kein neues Objekt
        t2 = _wait(c, c.post("/api/sage/analyze", files={"file": ("orion2.jpg", _jpg(), "image/jpeg")}).json()["id"])
        assert t2["discovery"]["new"] == [] and "M42 Orionnebel" in t2["discovery"]["known"]
        d = c.get("/api/sage/discoveries").json()
        assert d["stats"]["total_objects"] == 2 and d["stats"]["analyses"] == 2 and d["stats"]["repeat_objects"] == 2
        acts = c.get("/api/activity").json()
        assert len([a for a in acts if a["module"] == "sage"]) == 2
        # Löschen berechnet die Entdeckungen neu
        assert c.delete(f"/api/sage/history/{t['entry']}").json()["ok"]
        assert c.get("/api/sage/discoveries").json()["stats"]["analyses"] == 1
        # Schlüssel werden nie ausgeliefert; leer lassen behält den gespeicherten
        c.post("/api/sage/config", json={"nova_key": "abc123", "online_solve": True})
        cfg = c.post("/api/sage/config", json={"nova_key": ""}).json()
        assert cfg["has_nova_key"] and "nova_key" not in cfg and cfg["online_solve"] is True


def test_ohne_solver_klare_meldung(tmp_path, monkeypatch):
    monkeypatch.setenv("ASTROFLOW_MODULES", "stacking,sage")
    app = create_app(load_settings(tmp_path))
    app.state.ctx.services["plate_solver"] = None
    with TestClient(app) as c:
        t = _wait(c, c.post("/api/sage/analyze", files={"file": ("x.jpg", _jpg(), "image/jpeg")}).json()["id"])
        assert t["state"] == "error" and "Plate-Solver" in t["error"]
        assert c.get("/api/sage/history").json() == []
        assert not list((tmp_path / "sage" / "uploads").iterdir())         # fehlgeschlagene Uploads werden entfernt


def test_store_und_namen(tmp_path):
    assert normalize("Messier 42") == normalize("M42 Orionnebel") == normalize("m 42") == "m 42"
    assert plain("## Titel\n\n**fett** und *kursiv*") == "Titel\n\nfett und kursiv"
    st = SageStore(tmp_path)
    e = st.add({"objects": [{"name": "IC 434"}], "story": "## Pferdekopf\n\nText"})
    assert st.record(e)["new"] == ["IC 434"] and st.summary(e)["title"] == "Pferdekopf"
    assert services.name_hint("2024-01-03_M42_stack.jpg").id == "M42"
    assert services.name_hint("NGC7000 Nordamerika.png") is not None
    assert services.name_hint("bild.jpg") is None


def test_bildausschnitt_im_stack_finden():
    rng = np.random.default_rng(3)
    big = rng.normal(100, 3, (400, 600)).astype(np.float32)
    for y, x in rng.integers(10, [390, 590], (250, 2)):
        big[y - 2:y + 3, x - 2:x + 3] += rng.uniform(200, 2000)
    small = big[37:337, 52:552] * 1.7 + 5                      # anders gestreckt, aber gleicher Ausschnitt
    dy, dx, q = services.locate(services._stretch(small, 4), services._stretch(big, 4))
    assert abs(dy * 4 - 37) <= 4 and abs(dx * 4 - 52) <= 4 and q > 8
