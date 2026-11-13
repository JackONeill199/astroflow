"""Fehler melden: Bericht zusammenstellen und bereinigen, dann öffnet der Browser das vorbefüllte GitHub-Issue-Formular.

Der Server sendet **nichts** nach außen – er liefert nur Text und eine Adresse auf das feste Repo (`REPO`). Nutzer sehen
vorher genau, was im Formular landet. Automatisch nur unkritische Angaben (Version, Betriebssystem, Module, Ansicht ohne
IDs, Browser-Fehler); alles – auch der eigene Text – läuft durch `Scrubber` (Pfade, Benutzer-/Rechnername, IPs, E-Mails,
Schlüssel, Koordinaten, Namen und Koordinaten aus der Ortsliste). Abschaltbar: Einstellungen → Module & System
(`settings.json` → `system.report`) bzw. `ASTROFLOW_REPORT=0`.
"""
from __future__ import annotations

import getpass
import os
import platform
import re
import socket
import sys
from pathlib import Path
from urllib.parse import quote, urlencode

from fastapi import APIRouter, Body, HTTPException, Request

from .. import __version__ as VERSION

REPO = "JackONeill199/astroflow"
NEW_ISSUE = f"https://github.com/{REPO}/issues/new"
TEMPLATE = "fehler.yml"
URL_MAX = 7000                                   # GitHub/Browser vertragen ~8 KB – mit Abstand darunter bleiben
LIMITS = {"title": 150, "actual": 4000, "steps": 3000, "expected": 2000}
MAX_ERRORS, MAX_ERROR_LEN = 20, 400
LOG_LINES, LOG_TAIL_BYTES = 40, 64 * 1024

# Bereiche des Issue-Formulars (.github/ISSUE_TEMPLATE/fehler.yml → dropdown `module`)
AREAS = {"stacking": "Bilder (Stacking/Entwicklung)", "skytrace": "SkyTrace (Spuren/Satelliten)",
         "sage": "Sage (Enzyklopädie/Bestimmung)", "setup": "Installation/Einrichtung", "ui": "Oberfläche allgemein",
         "unknown": "weiß nicht"}

_EMAIL = re.compile(r"\b[\w.+-]{1,64}@[\w-]{1,63}(?:\.[\w-]{1,63}){1,5}\b")
# Muster ohne mehrdeutige Wiederholungen (AGENTS.md, Sicherheit 7); Eingaben sind vorher gekürzt
_PATTERNS = [
    (re.compile(r"file:/{2,3}[^\s\"'<>]*", re.I), "<Pfad>"),
    (re.compile(r"\b[A-Za-z]:[\\/][^\s\"'<>|]*"), "<Pfad>"),
    (re.compile(r"\\\\[^\s\"'<>|]+"), "<Pfad>"),
    (re.compile(r"(?<![\w.:/-])(?:~|/home|/Users|/root|/mnt|/media|/Volumes|/run/media|/srv|/data|/opt|/tmp|/var|/private)"
                r"(?:/[^\s\"'<>]*)?"), "<Pfad>"),
    (re.compile(r"\b(?:sk|pk|rk)-[A-Za-z0-9_-]{16,}"), "<Schlüssel>"),
    (re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|AIza[0-9A-Za-z_-]{30,}|xox[abpr]-[A-Za-z0-9-]{10,})"),
     "<Schlüssel>"),
    (re.compile(r"(?i)\b(api[_-]?key|token|secret|passwor[dt]|authorization|bearer|cookie|af_geraet)\b([\"']?\s{0,3}[=:]\s{0,3}|\s)[^\s,;]+"),
     r"\1\2<Schlüssel>"),
    (re.compile(r"\b[A-Fa-f0-9]{32,}\b|\b[A-Za-z0-9+/_-]{40,}={0,2}"), "<Kennung>"),
    (re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"), "<IP>"),
    (re.compile(r"\b[0-9A-Fa-f]{1,4}(?::[0-9A-Fa-f]{1,4}){0,6}::(?:[0-9A-Fa-f]{1,4}(?::[0-9A-Fa-f]{1,4}){0,6})?|(?<![\w:])::1\b"), "<IP>"),
    (re.compile(r"\b(?:[0-9A-Fa-f]{1,4}:){3,7}[0-9A-Fa-f]{1,4}\b"), "<IP>"),
    # Koordinatenpaare (Dezimalgrad), Grad-Minuten-Angaben und benannte Breite/Länge
    (re.compile(r"[-+]?\d{1,3}[.,]\d{2,}\s{0,3}[,;/ ]\s{0,3}[-+]?\d{1,3}[.,]\d{2,}"), "<Koordinaten>"),
    (re.compile(r"\d{1,3}\s{0,2}°\s{0,2}\d{1,2}\s{0,2}['′](?:\s{0,2}\d{1,2}(?:[.,]\d{1,3})?\s{0,2}[\"″])?\s{0,2}[NSEOW]?"), "<Koordinaten>"),
    (re.compile(r"(?i)\b(lat|lon|lng|latitude|longitude|breite|länge|laenge)\b(\s{0,3}[=:]\s{0,3}|\s)[-+]?\d{1,3}(?:[.,]\d+)?"),
     r"\1\2<Koordinate>"),
]


def enabled(store) -> bool:
    if os.environ.get("ASTROFLOW_REPORT") == "0":
        return False
    return bool(store.get("system").get("report", True))


def _coord_variants(v) -> list[str]:
    """Koordinaten eines Orts in den Schreibweisen, in denen sie in Meldungen/Oberfläche auftauchen können."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return []
    out = set()
    for n in (2, 3, 4, 5, 6):
        s = f"{f:.{n}f}"
        out.update({s, s.replace(".", ",")})
    return [s for s in out if len(s.lstrip("-")) >= 5]     # „49.1“ allein wäre zu allgemein


def private_words(ctx) -> list[str]:
    """Sperrliste: Benutzer- und Rechnername, Datenordner, Namen/Notizen/Koordinaten der Ortsliste."""
    words: set[str] = set()
    for fn in (getpass.getuser, socket.gethostname, lambda: os.environ.get("USERNAME", ""), lambda: os.environ.get("USER", "")):
        try:
            w = str(fn() or "").strip()
        except Exception:
            w = ""
        if w:
            words.add(w)
            words.add(w.split(".")[0])
    for p in (Path.home(), ctx.settings.data_dir):
        try:
            words.update({str(p), str(Path(p).resolve()), Path(p).name})
        except OSError:
            pass
    book = ctx.services.get("sites")
    if book is not None:
        try:
            d = book.data()
        except Exception:
            d = {"list": [], "periods": []}
        for s in d.get("list") or []:
            for k in ("name", "note", "notes"):
                if s.get(k):
                    words.add(str(s[k]).strip()[:80])
            for k in ("lat", "lon"):
                words.update(_coord_variants(s.get(k)))
        for p in d.get("periods") or []:
            if p.get("why"):
                words.add(str(p["why"]).strip()[:80])
    # Allgemeine Wörter nicht maskieren (sonst wird halber Text zu <privat>)
    common = {"astroflow", "data", "daten", "home", "user", "users", "standort", "beobachtungsort", "localhost", "admin"}
    return sorted((w for w in words if len(w) >= 3 and w.lower() not in common), key=len, reverse=True)[:400]


_TOKEN = "\x00privat\x00"


class Scrubber:
    def __init__(self, words: list[str]):
        self.words = re.compile("|".join(re.escape(w) for w in words), re.I) if words else None

    def __call__(self, text: str, limit: int = 20000) -> str:
        t = str(text or "")[:limit].replace("\x00", "")
        # zuerst die eigenen Wörter – als Platzhalter, der Pfade nicht unterbricht (sonst bliebe der Rest eines Pfads stehen)
        t = _EMAIL.sub("<E-Mail>", t)                  # vor den Wörtern: sonst bliebe „<privat>@domain“ stehen
        if self.words is not None:
            t = self.words.sub(_TOKEN, t)
        for rx, rep in _PATTERNS:
            t = rx.sub(rep, t)
        return t.replace(_TOKEN, "<privat>")


_SEG = re.compile(r"^[a-zäöü][a-zäöü-]{0,30}$")


def clean_route(route: str) -> str:
    """Ansicht ohne IDs/Namen: nur feste Wörter bleiben, alles andere (Projekt-IDs, Objektnamen, Ordner) wird `:id`."""
    r = str(route or "")[:300].split("?")[0].lstrip("#")
    segs = [s for s in r.split("/") if s]
    return "#/" + "/".join(s if _SEG.match(s) else ":id" for s in segs[:6])


def os_name() -> str:
    s = platform.system()
    if s == "Windows":
        try:
            build = int(platform.version().split(".")[-1])
        except ValueError:
            build = 0
        return "Windows 11" if build >= 22000 else f"Windows {platform.release()}"
    if s == "Darwin":
        return "macOS " + (platform.mac_ver()[0] or "")
    if s == "Linux":
        try:
            for line in Path("/etc/os-release").read_text(encoding="utf-8").splitlines():
                if line.startswith("PRETTY_NAME="):
                    return "Linux (" + line.split("=", 1)[1].strip().strip('"')[:40] + ")"
        except OSError:
            pass
        return "Linux"
    return s or "unbekannt"


def os_choice() -> str:
    """Option des Dropdowns `os` im Issue-Formular."""
    if Path("/.dockerenv").exists():
        return "Docker"
    n = os_name()
    return "Windows 11" if n == "Windows 11" else "Windows 10" if n.startswith("Windows") else \
        "macOS" if n.startswith("macOS") else "Linux" if n.startswith("Linux") else "anderes"


def install_kind() -> str:
    if Path("/.dockerenv").exists():
        return "Docker"
    if os.environ.get("ASTROFLOW_LAUNCHER") == "1" or getattr(sys, "frozen", False):
        return "Linux-Paket" if platform.system() == "Linux" else "Windows-Paket" if platform.system() == "Windows" else "Paket"
    return "Aus dem Quellcode (pip/venv)"


def environment(app_state) -> dict:
    mods = getattr(app_state, "modules", {}) or {}
    return {"version": VERSION, "os": os_name(), "install": install_kind(),
            "python": platform.python_version(), "modules": sorted(mods)}


def log_excerpt(data_dir: Path, scrub: Scrubber, lines: int = LOG_LINES) -> str:
    """Letzte Zeilen aus server.log.err (Fehlerausgabe des Starters), sonst server.log – bereinigt."""
    for name in ("server.log.err", "server.log"):
        p = Path(data_dir) / name
        try:
            with open(p, "rb") as f:
                f.seek(0, 2)
                size = f.tell()
                f.seek(max(0, size - LOG_TAIL_BYTES))
                raw = f.read().decode("utf-8", errors="replace")
        except OSError:
            continue
        rows = [r for r in raw.splitlines()[1 if size > LOG_TAIL_BYTES else 0:] if r.strip()][-lines:]
        if rows:
            return "\n".join(scrub(r, 600) for r in rows)
    return "(kein Protokoll gefunden – AstroFlow wurde nicht über den Starter gestartet)"


def _url(fields: dict) -> str:
    return NEW_ISSUE + "?" + urlencode({"template": TEMPLATE, **{k: v for k, v in fields.items() if v}}, quote_via=quote)


def build(fields: dict) -> tuple[str, list[str]]:
    """Formular-URL unter `URL_MAX`: zuerst Protokoll, dann Umgebung durch einen Hinweis ersetzen, danach lange Texte kürzen.
    Rückgabe (URL, Felder, die nur per Zwischenablage mitkommen)."""
    f = dict(fields)
    moved: list[str] = []
    hint = "(zu lang für den Link – AstroFlow hat den vollständigen Bericht in die Zwischenablage kopiert, bitte hier einfügen)"
    for k in ("log", "umgebung"):
        if len(_url(f)) <= URL_MAX:
            break
        if f.get(k):
            f[k] = hint
            moved.append(k)
    for k in ("actual", "steps", "expected", "title"):
        orig = fields.get(k) or ""
        n = len(orig)
        while len(_url(f)) > URL_MAX and n > 80:          # Länge des Originals schrumpft je Runde → endet sicher
            n = max(80, int(n * 0.75))
            f[k] = orig[:n].rstrip() + " … (gekürzt)"
            if k not in moved:
                moved.append(k)
    return _url(f), moved


def compose(ctx, app_state, body: dict) -> dict:
    scrub = Scrubber(private_words(ctx))
    txt = {k: scrub(str(body.get(k) or "").strip()[:n]) for k, n in LIMITS.items()}
    if not txt["title"] or not txt["actual"]:
        raise HTTPException(400, "Bitte Titel und „Was ist passiert?“ ausfüllen.")
    env = environment(app_state)
    area = str(body.get("area") or "")
    browser = str(body.get("browser") or "")[:40]
    browser = browser if re.fullmatch(r"[A-Za-z ]{1,24} \d{1,4}", browser) else "unbekannt"
    errs = body.get("errors") if isinstance(body.get("errors"), list) else []
    errs = [scrub(str(e), MAX_ERROR_LEN) for e in errs[-MAX_ERRORS:] if str(e).strip()]
    width = body.get("width")
    width = f"{int(width)} px" if isinstance(width, (int, float)) and 200 <= width <= 10000 else "unbekannt"
    umgebung = "\n".join([
        f"AstroFlow {env['version']} · {env['os']} · {env['install']} · Python {env['python']}",
        f"Browser: {browser} · Fensterbreite {width}",
        f"Module: {', '.join(env['modules']) or '–'}",
        f"Ansicht: {clean_route(str(body.get('route') or ''))}",
    ] + (["", "Browser-Fehler (neueste zuletzt):", *errs] if errs else []))
    log = log_excerpt(ctx.settings.data_dir, scrub) if body.get("log") else ""
    fields = {"title": "[Fehler] " + txt["title"], "version": env["version"], "os": os_choice(),
              "install": {"Windows-Paket": "Windows-Installer"}.get(env["install"], env["install"]) if env["install"] != "Paket" else "",
              "module": AREAS.get(area, ""), "steps": txt["steps"], "expected": txt["expected"], "actual": txt["actual"],
              "umgebung": umgebung, "log": log}
    url, moved = build(fields)
    labels = {"title": "Titel", "version": "AstroFlow-Version", "os": "Betriebssystem", "install": "Installationsart",
              "module": "Bereich", "steps": "Schritte zum Nachstellen", "expected": "Erwartet", "actual": "Tatsächlich passiert",
              "umgebung": "Umgebung (automatisch)", "log": "Protokoll"}
    preview = [{"id": k, "label": labels[k], "value": v, "in_link": k not in moved} for k, v in fields.items() if v]
    text = "\n\n".join(f"### {labels[k]}\n\n" + (f"```text\n{v}\n```" if k in ("umgebung", "log") else v)
                       for k, v in fields.items() if v and k != "title")
    return {"url": url, "title": fields["title"], "preview": preview, "clipboard": fields["title"] + "\n\n" + text, "moved": moved,
            "repo": f"https://github.com/{REPO}"}


def build_router(ctx, app_state) -> APIRouter:
    r = APIRouter()

    def _check():
        if not enabled(ctx.store):
            raise HTTPException(403, "„Fehler melden“ ist in den Einstellungen ausgeschaltet.")

    @r.get("/report")
    def get_report():
        """Für den Dialog: unkritische Umgebung und die Sperrliste zum Maskieren vor dem Screenshot (bleibt im Browser)."""
        _check()
        return {"enabled": True, "environment": environment(app_state), "mask": private_words(ctx),
                "areas": AREAS, "repo": f"https://github.com/{REPO}"}

    @r.post("/report/compose")
    def post_compose(request: Request, body: dict = Body(...)):
        _check()
        cl = request.headers.get("content-length") or "0"
        if not cl.isdigit() or int(cl) > 64 * 1024:
            raise HTTPException(413, "Der Bericht ist zu groß.")
        try:
            return compose(ctx, app_state, body)
        except HTTPException:
            raise
        except Exception:
            import traceback
            traceback.print_exc()
            raise HTTPException(500, "Der Bericht ließ sich nicht zusammenstellen. Details stehen im Protokoll.")

    return r
