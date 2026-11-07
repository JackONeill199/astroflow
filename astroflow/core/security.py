"""Zugriffsschutz: Host-Prüfung, Herkunft schreibender Anfragen, Kopplung von Heimnetz-Geräten, Endpunkte nur am Rechner selbst.

* **Host-Prüfung:** Der `Host`-Kopf muss ein bekannter Name sein – `localhost`, eine IP-Adresse (eine Seite, die über eine
  IP-Adresse geladen wurde, gehört zu genau diesem Rechner) oder der eigene Rechnername (auch `<name>.local`, vollständiger
  Name). Weitere Namen: Umgebung `ASTROFLOW_HOSTS` (kommagetrennt) bzw. `settings.json` → `lan.hosts`. Sonst 400.
* **Herkunft:** Schreibende Anfragen (alles außer GET/HEAD/OPTIONS) brauchen `Origin` (Rückfall `Referer`) mit derselben
  Adresse wie `Host` und einem erlaubten Namen. Eigene Hilfsprogramme (Symbol im Infobereich, `astroflow stop`) setzen ihn.
* **Kopplung:** Anfragen, die nicht vom eigenen Rechner kommen (`request.client.host` nicht Loopback), brauchen ein
  Geräte-Token (Cookie `af_geraet`, HttpOnly, SameSite=Strict). Das Token bekommt ein Gerät einmalig über den Link mit dem
  Kopplungscode (`/api/pair?code=…`, QR-Code unter Einstellungen → Heimnetz). Gespeichert wird nur der SHA-256 des Tokens
  (`settings.json` → `pairing`). Der Kopplungscode gilt `CODE_TTL` und nur für ein Gerät.
* **Nur am Rechner selbst:** Programmpfade, Komponenten, StarNet-ZIP, Heimnetz/Firewall, Datenordner, Beenden/Neustart und die
  Kopplung selbst (`require_local`) – auch gekoppelte Geräte bekommen 403.
* Docker: Der veröffentlichte Port kommt im Container von der Bridge-Adresse. `ASTROFLOW_TRUSTED_NETS` (z. B. `172.16.0.0/12`)
  behandelt solche Netze wie den eigenen Rechner – nur zusammen mit `127.0.0.1:<port>:<port>` im Compose-File verwenden.
"""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import os
import secrets
import socket
import threading
import time
from urllib.parse import urlsplit

from fastapi import HTTPException

COOKIE = "af_geraet"
CODE_TTL = 15 * 60                    # Kopplungscode gilt 15 min und für ein Gerät
SEEN_EVERY = 600                      # „zuletzt gesehen“ höchstens alle 10 min speichern
MAX_DEVICES = 50
SAFE_METHODS = ("GET", "HEAD", "OPTIONS")
LOCAL_NAMES = {"localhost"}
# Ohne Kopplung erreichbar (Kopplungsseite und das, was sie braucht)
OPEN_PATHS = ("/api/pair", "/icon.svg", "/manifest.webmanifest")
LOCAL_ONLY_MSG = "Nur am Rechner selbst möglich, auf dem AstroFlow läuft."


def _strip(host: str) -> str:
    """Hostname ohne Port und Klammern, klein geschrieben (`[::1]:8766` → `::1`)."""
    h = (host or "").strip().lower()
    if h.startswith("["):
        h = h[1:].split("]", 1)[0]
    elif h.count(":") == 1:
        h = h.split(":", 1)[0]
    return h.rstrip(".")


def _is_ip(h: str) -> bool:
    try:
        ipaddress.ip_address(h)
        return True
    except ValueError:
        return False


def trusted_nets() -> list:
    out = []
    for part in (os.environ.get("ASTROFLOW_TRUSTED_NETS") or "").split(","):
        if part.strip():
            try:
                out.append(ipaddress.ip_network(part.strip(), strict=False))
            except ValueError:
                pass
    return out


def is_loopback(ip: str | None) -> bool:
    """Anfrage vom eigenen Rechner (127.0.0.0/8, ::1, ::ffff:127.x) bzw. aus `ASTROFLOW_TRUSTED_NETS`."""
    try:
        a = ipaddress.ip_address((ip or "").split("%", 1)[0])
    except ValueError:
        return False
    if getattr(a, "ipv4_mapped", None):
        a = a.ipv4_mapped
    return a.is_loopback or any(a in n for n in trusted_nets())


_NAMES: tuple[float, set] = (0.0, set())


def own_names() -> set[str]:
    """Rechnername in den üblichen Schreibweisen (für Links wie http://mein-pc:8766 im Heimnetz); 5 min zwischengespeichert,
    weil `getfqdn` eine Namensauflösung auslösen kann."""
    global _NAMES
    if time.time() - _NAMES[0] < 300:
        return _NAMES[1]
    names = set()
    try:
        h = socket.gethostname().lower()
        if h:
            names |= {h, h + ".local", h + ".fritz.box", h + ".home", h + ".lan"}
        fq = socket.getfqdn().lower()
        if fq and fq != "localhost":
            names.add(fq)
    except OSError:
        pass
    _NAMES = (time.time(), names)
    return names


def lan_addresses() -> list[str]:
    """IPv4-Adressen dieses Rechners im lokalen Netz (für Link und QR-Code). Kein Paket verlässt den Rechner:
    `connect` auf einen UDP-Socket fragt nur die Routingtabelle."""
    out = []
    for probe in ("10.255.255.255", "192.168.255.255", "172.31.255.255"):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect((probe, 1))
                ip = s.getsockname()[0]
            if ip and not ip.startswith("127.") and ip != "0.0.0.0" and ip not in out:
                out.append(ip)
        except OSError:
            pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith("127.") and not ip.startswith("169.254.") and ip not in out:
                out.append(ip)
    except OSError:
        pass
    priv = [ip for ip in out if ipaddress.ip_address(ip).is_private]
    return priv + [ip for ip in out if ip not in priv]


def device_name(ua: str) -> str:
    """Grobe Bezeichnung aus dem User-Agent – nur zur Wiedererkennung in der Geräteliste."""
    ua = ua or ""
    dev = next((n for k, n in (("iPhone", "iPhone"), ("iPad", "iPad"), ("Android", "Android"), ("Windows", "Windows-PC"),
                               ("Macintosh", "Mac"), ("CrOS", "Chromebook"), ("Linux", "Linux")) if k in ua), "Gerät")
    br = next((n for k, n in (("Edg/", "Edge"), ("OPR/", "Opera"), ("SamsungBrowser", "Samsung Internet"), ("Firefox", "Firefox"),
                              ("FxiOS", "Firefox"), ("CriOS", "Chrome"), ("Chrome", "Chrome"), ("Safari", "Safari")) if k in ua), "")
    return f"{dev} · {br}" if br else dev


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class Access:
    """Zustand und Prüfungen; ein Objekt je Server (`app.state.access`)."""

    def __init__(self, store):
        self.store = store
        self._lock = threading.Lock()

    # ---- Host / Herkunft ------------------------------------------------------
    def extra_hosts(self) -> set[str]:
        vals = (os.environ.get("ASTROFLOW_HOSTS") or "").split(",") + list(self.store.get("lan").get("hosts") or [])
        return {_strip(v) for v in vals if str(v).strip()}

    def host_ok(self, host: str) -> bool:
        h = _strip(host)
        if not h:
            return False
        return h in LOCAL_NAMES or _is_ip(h) or h in own_names() or h in self.extra_hosts()

    def origin_ok(self, request) -> bool:
        """Schreibende Anfrage: Origin (sonst Referer) muss dieselbe Adresse wie Host haben und erlaubt sein."""
        host = request.headers.get("host") or ""
        src = request.headers.get("origin")
        if not src or src == "null":
            src = request.headers.get("referer") or ""
        try:
            u = urlsplit(src)
        except ValueError:
            return False
        if u.scheme not in ("http", "https") or not u.netloc:
            return False
        return u.netloc.lower() == host.lower() and self.host_ok(u.netloc)

    # ---- Kopplung -------------------------------------------------------------
    def _data(self) -> dict:
        d = self.store.get("pairing")
        return {"code": d.get("code"), "code_created": d.get("code_created") or 0, "devices": list(d.get("devices") or [])}

    def code(self, renew: bool = False) -> dict:
        """Gültiger Kopplungscode (legt bei Bedarf einen neuen an)."""
        with self._lock:
            d = self._data()
            if renew or not d["code"] or time.time() - d["code_created"] > CODE_TTL:
                d["code"], d["code_created"] = secrets.token_urlsafe(18), time.time()
                self.store.update("pairing", {"code": d["code"], "code_created": d["code_created"]})
            return {"code": d["code"], "expires": d["code_created"] + CODE_TTL}

    def redeem(self, code: str, ua: str = "") -> str | None:
        """Kopplungscode einlösen → neues Geräte-Token (Klartext nur hier); der Code ist danach verbraucht."""
        with self._lock:
            d = self._data()
            ok = (d["code"] and code and time.time() - d["code_created"] <= CODE_TTL
                  and hmac.compare_digest(str(code).encode(), str(d["code"]).encode()))
            if not ok:
                return None
            token = secrets.token_urlsafe(32)
            now = time.time()
            devs = d["devices"][-(MAX_DEVICES - 1):] + [{"id": secrets.token_hex(6), "name": device_name(ua), "hash": _hash(token),
                                                        "created": now, "seen": now}]
            self.store.update("pairing", {"devices": devs, "code": None, "code_created": 0})
            return token

    def device_ok(self, token: str | None) -> bool:
        if not token:
            return False
        h = _hash(token)
        devs = self._data()["devices"]
        hit = next((x for x in devs if hmac.compare_digest(x.get("hash") or "", h)), None)
        if hit and time.time() - (hit.get("seen") or 0) > SEEN_EVERY:
            with self._lock:
                devs = self._data()["devices"]
                for x in devs:
                    if x.get("hash") == h:
                        x["seen"] = time.time()
                self.store.update("pairing", {"devices": devs})
        return hit is not None

    def devices(self) -> list[dict]:
        return [{k: x.get(k) for k in ("id", "name", "created", "seen")} for x in self._data()["devices"]]

    def remove(self, dev_id: str) -> bool:
        with self._lock:
            devs = self._data()["devices"]
            keep = [x for x in devs if x.get("id") != dev_id]
            self.store.update("pairing", {"devices": keep})
            return len(keep) != len(devs)


def lan_enabled(settings) -> bool:
    """Heimnetz-Einstellung für `serve` ohne `--host`/`--lan` (`settings.json` → `lan.enabled`).
    Bis 0.45 lauschte `serve` (und damit Start.bat) immer auch im Heimnetz. Bestehende Instanzen (Datenbank vorhanden) behalten
    das deshalb beim ersten Start – jetzt nur noch mit Kopplung (`migrated`, Hinweis unter Einstellungen → Heimnetz).
    Neue Instanzen sind nur auf dem eigenen Rechner erreichbar."""
    from .store import SettingsStore
    st = SettingsStore(settings.data_dir / "settings.json")
    sec = st.get("lan")
    if "enabled" not in sec:
        legacy = settings.db_path.exists()
        st.update("lan", {"enabled": legacy, "migrated": time.time() if legacy else None})
        return legacy
    return bool(sec["enabled"])


def bind_is_lan() -> bool:
    """Lauscht dieser Server im Heimnetz? (`serve` setzt `ASTROFLOW_BIND`.)"""
    return (os.environ.get("ASTROFLOW_BIND") or "127.0.0.1") not in ("127.0.0.1", "localhost", "::1")


def require_local(request, what: str = "") -> None:
    """Gefährliche Endpunkte (Programme ausführen, Pfade, Netz, Beenden …) nur vom eigenen Rechner."""
    client = request.client.host if request.client else ""
    if not is_loopback(client):
        raise HTTPException(403, f"{what}: {LOCAL_ONLY_MSG}" if what else LOCAL_ONLY_MSG)


PAIR_PAGE = """<!doctype html><html lang="de"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>AstroFlow – Gerät koppeln</title><style>
body{{margin:0;min-height:100vh;display:grid;place-items:center;background:#0d1020;color:#e6e8f0;font:16px/1.5 system-ui,sans-serif}}
main{{max-width:28rem;padding:1.5rem}}h1{{font-size:1.3rem;margin:0 0 .75rem}}p{{color:#a7adc2;margin:.5rem 0}}b{{color:#e6e8f0}}
</style></head><body><main><h1>{title}</h1>{body}</main></body></html>"""


def pair_page(title: str, body: str) -> str:
    return PAIR_PAGE.format(title=title, body=body)


NEED_PAIRING = pair_page(
    "Gerät koppeln",
    "<p>Dieses Gerät ist noch nicht mit AstroFlow gekoppelt.</p>"
    "<p>Am Rechner, auf dem AstroFlow läuft, unter <b>Einstellungen → Heimnetz</b> den QR-Code mit diesem Gerät scannen "
    "oder den Link dort öffnen. Danach bleibt das Gerät gekoppelt.</p>")
