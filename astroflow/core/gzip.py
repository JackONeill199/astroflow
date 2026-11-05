"""JSON/Text gzip-komprimiert ausliefern – unabhängig von der Starlette-Version.

Übers Heimnetz (Handy) dauerten große Antworten sonst ewig (SkyTrace-Lauf, Objektliste). Komprimiert wird nur,
was als Text gekennzeichnet ist (Positivliste), ab `MIN_SIZE` und nur bei vollständigen 200-Antworten.
Bilder, Videos, FITS/TIFF, Downloads und Teilantworten (206, Range) laufen unverändert durch.
"""
from __future__ import annotations

import zlib

MIN_SIZE = 2048
TYPES = ("application/json", "application/javascript", "text/", "image/svg+xml")


def _compressible(headers: list) -> bool:
    h = {k.lower(): v for k, v in headers}
    if b"content-encoding" in h or b"content-range" in h or b"content-disposition" in h:
        return False
    ctype = h.get(b"content-type", b"").decode("latin-1").split(";")[0].strip().lower()
    return any(ctype.startswith(t) for t in TYPES)


class GZip:
    def __init__(self, app, minimum_size: int = MIN_SIZE, level: int = 5):
        self.app, self.minimum_size, self.level = app, minimum_size, level

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or b"gzip" not in dict(scope.get("headers") or []).get(b"accept-encoding", b""):
            await self.app(scope, receive, send)
            return
        state: dict = {"start": None, "mode": None, "buf": b"", "z": None}

        async def wrapped(message):
            kind = message["type"]
            if kind == "http.response.start":
                state["start"] = message
                if message["status"] != 200 or not _compressible(message.get("headers") or []):
                    state["mode"] = "pass"
                    await send(message)
                return
            if state["mode"] == "pass" or kind != "http.response.body":
                await send(message)
                return
            body, more = message.get("body", b""), message.get("more_body", False)
            if state["mode"] is None:                                   # noch nicht entschieden: sammeln
                state["buf"] += body
                if not more and len(state["buf"]) < self.minimum_size:
                    state["mode"] = "pass"
                    await send(state["start"])
                    await send({"type": "http.response.body", "body": state["buf"]})
                    return
                if more and len(state["buf"]) < self.minimum_size:
                    return
                state["mode"] = "gzip"
                state["z"] = zlib.compressobj(self.level, zlib.DEFLATED, 31)
                start = state["start"]
                headers = [(k, v) for k, v in start.get("headers") or [] if k.lower() != b"content-length"]
                headers += [(b"content-encoding", b"gzip"), (b"vary", b"Accept-Encoding")]
                if not more:                                            # ganze Antwort auf einmal → mit Länge
                    data = state["z"].compress(state["buf"]) + state["z"].flush()
                    headers.append((b"content-length", str(len(data)).encode()))
                    await send({**start, "headers": headers})
                    await send({"type": "http.response.body", "body": data})
                    return
                await send({**start, "headers": headers})
                body, state["buf"] = state["buf"], b""
            z = state["z"]
            data = z.compress(body) + (b"" if more else z.flush())
            await send({"type": "http.response.body", "body": data, "more_body": more})

        await self.app(scope, receive, wrapped)
