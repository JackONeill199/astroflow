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


class _Resp:
    def __init__(self, status=200, data=None, content=b"", ctype="application/json"):
        self.status_code, self._data, self.content, self.headers, self.text = status, data, content, {"content-type": ctype}, ""

    def json(self):
        return self._data


def test_modelllisten_filtern(monkeypatch):
    from astroflow.core import models
    seen = {}

    def get(url, params=None, headers=None, timeout=None):
        seen["params"], seen["auth"] = params, headers
        return _Resp(data={"data": [
            {"id": "elevenlabs/eleven-v4", "name": "ElevenLabs: Eleven v4", "architecture": {"output_modalities": ["speech"]},
             "pricing": {"prompt": "0.00008"}, "supported_voices": ["george", "sarah"]},
            {"id": "openai/gpt-4o", "name": "GPT-4o", "architecture": {"input_modalities": ["text", "image"], "output_modalities": ["text"]},
             "pricing": {"prompt": "0.0000025", "completion": "0.00001"}, "context_length": 128000},
            {"id": "x/nur-text", "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]}},
            {"id": "text-embedding-3-small"}, {"id": "gpt-4.1-mini"}]})

    monkeypatch.setattr(models.httpx, "get", get)
    models._cache.clear()
    sp = models.list_models("https://openrouter.ai/api/v1", "", "speech")
    assert [m["id"] for m in sp] == ["elevenlabs/eleven-v4"] and sp[0]["voices"] == ["george", "sarah"]
    assert seen["params"] == {"output_modalities": "speech"} and "0,080 $" in sp[0]["info"]
    txt = models.list_models("https://openrouter.ai/api/v1", "k", "text")
    assert [m["id"] for m in txt] == ["gpt-4.1-mini", "openai/gpt-4o", "x/nur-text"] and seen["auth"] == {"Authorization": "Bearer k"}
    assert [m["id"] for m in models.list_models("https://openrouter.ai/api/v1", "k", "vision")] == ["gpt-4.1-mini", "openai/gpt-4o"]


def test_openrouter_sprachausgabe(tmp_path, monkeypatch):
    monkeypatch.setenv("ASTROFLOW_MODULES", "stacking,sage")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    calls = []

    def post(url, json=None, headers=None, timeout=None):
        calls.append((url, json, headers))
        return _Resp(content=b"ID3" + json["input"][:3].encode(), ctype="audio/mpeg")

    monkeypatch.setattr(clients.httpx, "post", post)
    assert len(clients.chunks("Satz eins. " * 900, 4000)) == 3 and all(len(c) <= 4000 for c in clients.chunks("x" * 9000))
    with TestClient(create_app(load_settings(tmp_path))) as c:
        cfg = c.get("/api/sage/config").json()
        assert cfg["tts_provider"] == "openrouter" and not cfg["openrouter_speech_key"]
        assert c.post("/api/sage/tts/speak", json={"provider": "openrouter", "text": "Hallo"}).status_code == 503
        # Schlüssel der Erzähl-KI (OpenRouter) wird mitbenutzt
        c.post("/api/sage/config", json={"story_provider": "openrouter", "story_api_key": "sk-or-test", "openrouter_tts_voice": "sarah"})
        assert c.get("/api/sage/tts/config").json()["openrouter"]["available"]
        e = SageStore(tmp_path / "sage").add({"objects": [], "story": "## Titel\n\nDer **Orionnebel** leuchtet."})
        r = c.post("/api/sage/tts/speak", json={"provider": "openrouter", "history_id": e["id"]}).json()
        assert r["variant"] == "elevenlabs/eleven-v4|sarah" and not r["cached"]
        url, body, headers = calls[-1]
        assert url.endswith("/audio/speech") and body == {"model": "elevenlabs/eleven-v4", "input": "Titel\n\nDer Orionnebel leuchtet.",
                                                          "response_format": "mp3", "voice": "sarah"}
        assert headers["Authorization"] == "Bearer sk-or-test" and c.get(r["url"]).content.startswith(b"ID3")
        assert c.post("/api/sage/tts/speak", json={"provider": "openrouter", "history_id": e["id"]}).json()["cached"]
        # andere Stimme → neu vertonen, alte Datei wird ersetzt
        c.post("/api/sage/config", json={"openrouter_tts_voice": "george"})
        r2 = c.post("/api/sage/tts/speak", json={"provider": "openrouter", "history_id": e["id"]}).json()
        assert not r2["cached"] and r2["variant"].endswith("|george") and len(list((tmp_path / "sage" / "audio").iterdir())) == 1


def test_fits_mit_wcs_ohne_solver(tmp_path):
    """Eingebettete WCS wird auch ohne eingerichteten Solver gelesen; ohne WCS klare Ablehnung."""
    from astropy.io import fits
    from astroflow.modules.stacking import platesolve
    solver = services.PictureSolver(platesolve.SolverConfig(backend="off"), tmp_path)
    assert not solver.available
    hdr = fits.Header({"CTYPE1": "RA---TAN", "CTYPE2": "DEC--TAN", "CRVAL1": 83.82, "CRVAL2": -5.39, "CRPIX1": 32, "CRPIX2": 24,
                       "CDELT1": -0.001, "CDELT2": 0.001})
    fits.writeto(tmp_path / "m42.fits", np.random.default_rng(2).random((48, 64)).astype(np.float32), hdr)
    sol = solver.solve(tmp_path / "m42.fits")
    assert sol["solver"] == "header" and abs(sol["ra"] - 83.82) < 0.1
    fits.writeto(tmp_path / "leer.fits", np.zeros((48, 64), np.float32))
    try:
        solver.solve(tmp_path / "leer.fits", filename="M42.fits")
        raise AssertionError("ohne Solver darf nicht gelöst werden")
    except platesolve.SolveError as e:
        assert "kein lokaler" in str(e)


def test_messier_zuordnung_ueber_namen_und_ngc():
    from astroflow.modules.sage.collection import caldwell_number, messier_number
    assert messier_number("M 31") == messier_number("M31 Andromedagalaxie") == messier_number("NGC 224") == messier_number("Messier 31") == 31
    assert messier_number("NGC 1976 Orionnebel") == 42 and messier_number("IC 4725") == 25
    assert messier_number("M45 Plejaden") == 45 and messier_number("Plejaden") == 45
    assert messier_number("NGC 6888 Crescentnebel") is None and messier_number("M 1-92") is None and messier_number("M 111") is None
    assert messier_number("NGC 999", aliases=["M 77x", "M 12"]) == 12                  # Kennung der Wissensbasis
    assert caldwell_number("NGC 7000", ["NGC 7000", "C 20", "C 1339+286"]) == 20


def _fill(st, names):
    for n in names:
        st.record(st.add({"objects": [{"name": n}]}))


def test_erfolge_rueckwirkend_fortschritt_und_meilensteine(tmp_path):
    from astroflow.modules.sage.store import ACHIEVEMENTS, MILESTONES
    assert MILESTONES[-1] >= 1000 and len({a[0] for a in ACHIEVEMENTS}) == len(ACHIEVEMENTS)
    st = SageStore(tmp_path)
    # Altbestand: nur alte Erfolge freigeschaltet, M31 über den NGC-Namen
    _fill(st, ["NGC 224", "M 31 Andromedagalaxie", "M42", "NGC 891"] + [f"IC {1000 + i}" for i in range(100)])
    st.enrich = lambda names: {n: {"kind": "galaxy" if n == "NGC 891" else None, "con": "And" if n == "NGC 224" else "", "mly": 25.0 if n == "NGC 891" else 0} for n in names}
    st.log_stats = lambda: {"nights": 6, "night_owl": 1, "seasons": 2, "max_obj_hours": 1.5, "total_hours": 8.0, "dates": {"nights_5": "2026-01-05T12:00:00+00:00"}}
    d = st._disc()
    d["achievements"].pop("explorer_100", None)
    for k in ("nights_5", "first_galaxy", "dist_10m", "explorer_50"):
        assert k not in d["achievements"] or d["achievements"].pop(k)
    st._save(st.disc_file, d)
    ov = st.overview()
    by = {a["id"]: a for a in ov["achievements"]}
    assert ov["stats"]["messier"] == 2 and ov["stats"]["total_objects"] == 104                       # M31 zählt einmal
    assert by["explorer_100"]["unlocked"] and by["explorer_50"]["unlocked"] and by["first_galaxy"]["unlocked"]
    assert by["nights_5"]["unlocked"] == "2026-01-05T12:00:00+00:00" and by["nights_10"]["progress"] == {"cur": 6, "goal": 10}
    assert by["night_owl"]["unlocked"] and by["exposure_1h"]["unlocked"] and not by["exposure_10h"]["unlocked"]
    assert by["messier_50"]["progress"] == {"cur": 2, "goal": 50} and not by["messier_50"]["unlocked"]
    assert by["dist_10m"]["unlocked"] and not by["dist_100m"]["unlocked"]
    assert ov["next_milestone"] == 150 and [m["n"] for m in ov["messier"] if m["have"]] == [31, 42]
    assert next(m for m in ov["messier"] if m["n"] == 31)["name"] == "NGC 224"
    # rückwirkende Vergabe wird einmal gefeiert und ist beim zweiten Laden stabil
    assert st.celebrations() and st.celebrations()[-1]["achievements"]
    again = st.overview()
    assert [a["unlocked"] for a in again["achievements"]] == [a["unlocked"] for a in ov["achievements"]]


def test_belichtung_ungerundet_und_nachteule_aus_allen_frames():
    from astroflow.modules.sage.collection import log_stats
    ses = lambda used, **k: {"night": "2026-01-02", "frames": 5, "used_s": used, **k}
    st = log_stats([{"id": 1, "sessions": [ses(3456)]}])
    assert st["max_obj_hours"] < 1 and "exposure_1h" not in st["dates"] and st["night_owl"] == 0
    st = log_stats([{"id": 1, "sessions": [ses(3600, owl=True, first="2026-01-02T01:00:00", last="2026-01-02T07:00:00")]}])
    assert st["night_owl"] == 1 and st["max_obj_hours"] == 1
