"""Eigene gzip-Middleware (core/gzip.py): nur Text/JSON, nie Medien, Downloads oder Teilantworten."""
import gzip

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.testclient import TestClient

from astroflow.core.gzip import GZip


def _app(tmp_path):
    app = FastAPI()
    app.add_middleware(GZip)
    big = {"x": list(range(5000))}
    (tmp_path / "v.mp4").write_bytes(bytes(range(256)) * 100)
    (tmp_path / "gross.json").write_text('{"a": "' + "b" * 300_000 + '"}', encoding="utf-8")
    app.get("/json")(lambda: JSONResponse(big))
    app.get("/klein")(lambda: JSONResponse({"ok": 1}))
    app.get("/jpg")(lambda: Response(b"JFIF" + b"0" * 5000, media_type="image/jpeg"))
    app.get("/png")(lambda: Response(b"\x89PNG" + b"0" * 5000, media_type="image/png"))
    app.get("/datei")(lambda: FileResponse(tmp_path / "gross.json"))
    app.get("/video")(lambda: FileResponse(tmp_path / "v.mp4"))
    app.get("/download")(lambda: FileResponse(tmp_path / "gross.json", filename="gross.json"))
    app.get("/strom")(lambda: StreamingResponse((("zeile %d\n" % i) * 50 for i in range(400)), media_type="text/plain"))
    return app


def test_gzip_nur_fuer_text(tmp_path):
    with TestClient(_app(tmp_path)) as c:
        h = {"accept-encoding": "gzip"}
        r = c.get("/json", headers=h)
        assert r.headers["content-encoding"] == "gzip" and r.json()["x"][-1] == 4999
        raw = c.get("/datei", headers=h)                                    # FileResponse streamt in Stücken
        assert raw.headers["content-encoding"] == "gzip" and raw.json()["a"] == "b" * 300_000
        s = c.get("/strom", headers=h)
        assert s.headers["content-encoding"] == "gzip" and s.text == "".join(("zeile %d\n" % i) * 50 for i in range(400))
        for path in ("/klein", "/png", "/jpg", "/video", "/download"):   # Bilder/MP4 auch ohne Range nie gzip
            assert "content-encoding" not in c.get(path, headers=h).headers, path
        part = c.get("/video", headers={**h, "range": "bytes=0-9"})
        assert part.status_code == 206 and part.content == bytes(range(10)) and "content-encoding" not in part.headers
        assert "content-encoding" not in c.get("/json", headers={"accept-encoding": "identity"}).headers  # ohne gzip unverändert


def test_gzip_bytes_gueltig(tmp_path):
    """Gepackte Bytes direkt prüfen (ohne automatisches Entpacken des Clients)."""
    import asyncio
    app = _app(tmp_path)
    chunks, starts = [], []

    async def run(path):
        scope = {"type": "http", "method": "GET", "path": path, "raw_path": path.encode(), "query_string": b"", "root_path": "",
                 "headers": [(b"accept-encoding", b"gzip, deflate")], "scheme": "http", "server": ("t", 80), "client": ("c", 1), "http_version": "1.1"}
        sent = []
        async def receive():
            if sent:
                await asyncio.sleep(3600)                               # wie ein offener Client: erst beim Abbruch weiter
            sent.append(1)
            return {"type": "http.request", "body": b"", "more_body": False}
        async def send(m):
            (starts if m["type"] == "http.response.start" else chunks).append(m)
        await app(scope, receive, send)
    import threading                                                    # eigener Thread: Browser-Tests im selben Prozess halten eine Schleife offen
    t = threading.Thread(target=lambda: asyncio.run(run("/datei"))); t.start(); t.join(30)
    data = gzip.decompress(b"".join(m.get("body", b"") for m in chunks))
    assert data.startswith(b'{"a": "bbb') and len(data) == (tmp_path / "gross.json").stat().st_size
    assert not any(k == b"content-length" for k, _ in starts[0]["headers"])
