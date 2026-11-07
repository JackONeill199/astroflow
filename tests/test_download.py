"""Download der Komponenten (ASTAP, GraXpert …): abgebrochene Übertragungen werden fortgesetzt bzw. neu versucht.

Anlass: Tag-Lauf v0.44.0 – SourceForge trennte die Verbindung nach 1,4 von 5,8 MB („peer closed connection without
sending complete message body“), der ganze Paketbau scheiterte. Lokaler HTTP-Server, kein Netz.
"""
import http.server
import threading

import pytest

from astroflow.core import components as comp

DATA = bytes(range(256)) * 4000          # ~1 MB


class _Server:
    """Liefert DATA; `plan` = Liste der Antworten je Anfrage: 'cut' (Teil, dann Verbindung zu), 'full', '500', 'html'."""

    def __init__(self, plan, ranges=True):
        self.plan, self.ranges, self.requests = list(plan), ranges, []
        srv = self

        class H(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def do_GET(self):
                rng = self.headers.get("Range")
                srv.requests.append(rng)
                mode = srv.plan.pop(0) if srv.plan else "full"
                if mode == "500":
                    self.send_response(500)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                if mode == "html":
                    body = b"<html>Mirror waehlen</html>"
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                start = int(rng.split("=")[1].rstrip("-")) if (rng and srv.ranges) else 0
                part = DATA[start:]
                self.send_response(206 if start else 200)
                self.send_header("Content-Type", "application/zip")
                self.send_header("Content-Length", str(len(part)))
                if start:
                    self.send_header("Content-Range", f"bytes {start}-{len(DATA) - 1}/{len(DATA)}")
                self.end_headers()
                if mode == "cut":                 # nur ein Drittel, dann Verbindung trennen
                    self.wfile.write(part[: len(part) // 3])
                    self.wfile.flush()
                    self.close_connection = True
                    self.connection.close()
                    return
                self.wfile.write(part)

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}/datei.zip"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def server(monkeypatch):
    monkeypatch.setattr(comp, "DL_PAUSE", 0)
    made = []

    def make(plan, ranges=True):
        made.append(_Server(plan, ranges))
        return made[-1]
    yield make
    for s in made:
        s.close()


def test_abbruch_im_speicher_wird_fortgesetzt(server):
    s = server(["cut", "cut", "full"])
    seen = []
    assert comp.download(s.url, seen.append) == DATA
    assert s.requests[0] is None and s.requests[1] and s.requests[2]   # 2. und 3. Versuch per Range
    assert seen[-1] == pytest.approx(1.0)


def test_abbruch_in_datei_wird_fortgesetzt(server, tmp_path):
    s = server(["cut", "full"])
    part = tmp_path / ".d05.zip.part"
    assert comp.download(s.url, lambda f: None, to=part, resume=True) is None
    assert part.read_bytes() == DATA
    assert s.requests[1] == f"bytes={len(DATA) // 3}-"


def test_server_ohne_range_beginnt_neu(server, tmp_path):
    s = server(["cut", "full"], ranges=False)
    assert comp.download(s.url, lambda f: None) == DATA
    part = tmp_path / "x.part"
    s2 = server(["cut", "full"], ranges=False)
    comp.download(s2.url, lambda f: None, to=part, resume=True)
    assert part.read_bytes() == DATA                                   # nicht doppelt angehängt


def test_serverfehler_und_html_werden_wiederholt(server):
    s = server(["500", "html", "full"])
    assert comp.download(s.url, lambda f: None) == DATA
    assert len(s.requests) == 3


def test_endgueltiger_fehlschlag_behaelt_teil(server, tmp_path):
    s = server(["cut"] * 10)
    part = tmp_path / "y.part"
    with pytest.raises(RuntimeError, match="nach 3 Versuchen"):
        comp.download(s.url, lambda f: None, to=part, resume=True, tries=3)
    assert 0 < part.stat().st_size < len(DATA)
    s2 = server(["full"])                                              # nächster Lauf setzt fort
    comp.download(s2.url, lambda f: None, to=part, resume=True)
    assert part.read_bytes() == DATA and s2.requests[0].startswith("bytes=")


def test_ohne_resume_alte_datei_verworfen(server, tmp_path):
    part = tmp_path / "z.bin"
    part.write_bytes(b"alt" * 100)
    s = server(["full"])
    comp.download(s.url, lambda f: None, to=part)
    assert part.read_bytes() == DATA and s.requests == [None]
