import json
import time

import httpx
import numpy as np
from fastapi.testclient import TestClient

from astroflow.modules.stacking import demo, reviewer
from astroflow.core.config import load_settings
from astroflow.server import create_app


def test_api_end_to_end(tmp_path):
    s = load_settings(tmp_path / "d")
    s.auto = True
    demo.generate(s.input_dir / "n", n_lights=14, n_darks=4, n_flats=4)
    with TestClient(create_app(s)) as c:
        c.post("/api/stacking/scan")
        ps = c.get("/api/stacking/projects").json()
        assert len(ps) == 1 and ps[0]["name"].startswith("M31")
        for _ in range(120):                       # Auto-Verarbeitung abwarten
            p = c.get("/api/stacking/projects").json()[0]
            if p["result"] or (p["job"] and p["job"]["state"] == "error"):
                break
            time.sleep(0.5)
        assert p["result"], p["job"]
        rid = p["result"]["id"]
        assert c.get(f"/api/stacking/results/{rid}/jpg").headers["content-type"] == "image/jpeg"
        assert len(c.get(f"/api/stacking/projects/{p['id']}/frames").json()) == 14
        assert c.get("/").status_code == 200


def test_review_loop_applies_adjustments_with_mock_llm(monkeypatch):
    replies = iter([
        {"ok": False, "score": 5, "issues": ["Hintergrund zu hell"], "adjust": {"target_bg": 0.08, "bogus": 3}},
        {"ok": True, "score": 8, "issues": [], "adjust": {}},
    ])

    def fake_post(url, headers=None, json=None, timeout=None):
        assert url.endswith("/chat/completions") and json["messages"][1]["content"][1]["image_url"]["url"].startswith("data:image/jpeg")
        body = {"choices": [{"message": {"content": "```json\n" + __import__("json").dumps(next(replies)) + "\n```"}}]}
        return httpx.Response(200, json=body, request=httpx.Request("POST", url))

    monkeypatch.setattr(reviewer.httpx, "post", fake_post)
    cfg = reviewer.ReviewerConfig(provider="lmstudio", model="m", max_rounds=3)
    assert cfg.enabled
    renders = []

    def render(p):
        renders.append(dict(p))
        return np.random.rand(3, 16, 16).astype(np.float32), {}

    rgb, stats, params, hist = reviewer.review_loop(cfg, render, {"target_bg": 0.15}, "M31", "galaxy")
    assert abs(params["target_bg"] - 0.1125) < 1e-6 and "bogus" not in params   # 15 % des Bereichs 0.05..0.30 pro Runde
    assert reviewer.limit_steps({"target_bg": 0.12, "denoise": 0.5}, {"target_bg": 0.12, "denoise": 0.5}) == {}
    assert len(renders) == 2 and [h["ok"] for h in hist] == [False, True]


def test_review_loop_survives_unreachable_llm(monkeypatch):
    def boom(*a, **k):
        raise httpx.ConnectError("nope")
    monkeypatch.setattr(reviewer.httpx, "post", boom)
    cfg = reviewer.ReviewerConfig(provider="lmstudio", model="m")
    rgb, stats, params, hist = reviewer.review_loop(cfg, lambda p: (np.zeros((3, 8, 8)), {}), {"target_bg": 0.1}, "x", None)
    assert hist[0]["error"] and params["target_bg"] == 0.1


def test_upload_keeps_folders_and_blocks_traversal(tmp_path):
    import io
    s = load_settings(tmp_path / "d")
    s.auto = False
    buf = io.BytesIO()
    from astropy.io import fits
    fits.PrimaryHDU(np.zeros((8, 8), np.uint16)).writeto(buf)
    data = buf.getvalue()
    with TestClient(create_app(s)) as c:
        r = c.post("/api/stacking/upload", files=[("files", ("2022-08-06_observation_M57/img-0001r.fits", data)),
                                         ("files", ("../../evil.fits", data)), ("files", ("notes.txt", b"x"))])
        assert r.json()["saved"] == 2
    up = s.input_dir / "uploads"
    assert list(up.rglob("2022-08-06_observation_M57/img-0001r.fits"))
    assert not (s.data_dir / "evil.fits").exists() and list(up.rglob("evil.fits"))


def _reply(msg: dict, finish="stop"):
    def fake_post(url, headers=None, json=None, timeout=None):
        assert json["max_tokens"] >= 4000            # Denkmodelle brauchen Platz vor der eigentlichen Antwort
        return httpx.Response(200, json={"choices": [{"message": msg, "finish_reason": finish}]}, request=httpx.Request("POST", url))
    return fake_post


def test_reasoning_models(monkeypatch):
    cfg = reviewer.ReviewerConfig(provider="lmstudio", model="m")
    rgb = np.zeros((3, 8, 8))
    ans = '{"ok": true, "score": 8, "issues": [], "adjust": {}}'
    # Antwort im content, Nachdenken getrennt (LM Studio)
    monkeypatch.setattr(reviewer.httpx, "post", _reply({"content": ans, "reasoning_content": "Hmm {nicht das}"}))
    assert reviewer.review(cfg, rgb, {}, "x", None)["score"] == 8
    # Nachdenken im content mit <think>-Block
    monkeypatch.setattr(reviewer.httpx, "post", _reply({"content": "<think>{falsch}</think>" + ans}))
    assert reviewer.review(cfg, rgb, {}, "x", None)["ok"] is True
    # Token-Limit beim Nachdenken aufgebraucht: verständliche Meldung statt "keine JSON-Antwort"
    monkeypatch.setattr(reviewer.httpx, "post", _reply({"content": "", "reasoning_content": "Here's a thinking"}, "length"))
    try:
        reviewer.review(cfg, rgb, {}, "x", None)
        raise AssertionError("Fehler erwartet")
    except ValueError as e:
        assert "abgeschnitten" in str(e)


def test_settings_survive_restart(tmp_path):
    s = load_settings(tmp_path / "d")
    s.auto = False
    with TestClient(create_app(s)) as c:
        c.post("/api/stacking/reviewer", json={"provider": "lmstudio", "model": "bonsai", "max_rounds": 2})
        c.post("/api/stacking/storage", json={"delete_uploads": False})
    s2 = load_settings(tmp_path / "d")
    s2.auto = False
    with TestClient(create_app(s2)) as c:
        r = c.get("/api/stacking/reviewer").json()
        assert (r["provider"], r["model"], r["max_rounds"]) == ("lmstudio", "bonsai", 2)
        assert c.get("/api/stacking/status").json()["delete_uploads"] is False
