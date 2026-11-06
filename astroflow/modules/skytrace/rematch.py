"""Neu abgleichen nach Standortänderungen und Standortsuche als Hintergrundauftrag (Engine `rematch_sites.py`).

Ein Auftrag zur Zeit, getrennt vom Analyseprozess (`Controller`); der gerade analysierte bzw. aktualisierte Lauf wird
nie angefasst. Fortschritt kommt als JSON-Zeilen aus der Engine und erscheint in `activity()`. Ob sich Orte oder
Zeiträume seit dem letzten Abgleich aller Läufe geändert haben, verrät `sites_signature` (Einstellung
`skytrace.rematched_sites`) – die Oberfläche bietet den Abgleich dann an, übernimmt aber nie selbst einen Ort."""
from __future__ import annotations

import hashlib
import json
import subprocess
import threading
import time
from pathlib import Path


def sites_signature(section: dict | None) -> str:
    """Fingerabdruck der Ortsliste: nur, was die Auflösung beeinflusst (Koordinaten, Genauigkeit, Zeiträume, Standard)."""
    s = section or {}
    keep = {"list": sorted([[x.get("id"), x.get("lat"), x.get("lon"), x.get("alt_m"), x.get("accuracy_m")] for x in s.get("list") or []], key=str),
            "periods": sorted([[p.get("site_id"), p.get("from"), p.get("to")] for p in s.get("periods") or []], key=str),
            "default": s.get("default")}
    return hashlib.sha1(json.dumps(keep, sort_keys=True, default=str).encode()).hexdigest()[:16]


# ---- Alternativen zu einem zurückgerechneten Ort (0.38.0) ------------------------------------------------------------
GOOD_RMS_ARCSEC = 60          # wie engine/rematch_sites.GOOD_RMS_ARCSEC
SNAP_FIT_M, SNAP_GLOBAL_M = 300, 2000   # Einrastgrenzen (rematch_sites.SAME_SITE_M, global_site.NEAR_LIST_KM)
ALT_NEAR_M, ALT_SIGMA = 2000, 5         # „nah“: ≤ 2 km oder ≤ 5σ des Vorschlags
PARALLAX_ARCSEC_PER_M = 0.3   # erwartete Querablage, wenn der Ort um 1 m daneben liegt (≈ 1/700 km bei erdnahen Satelliten)


def _dist_m(a: dict, b: dict) -> float:
    import math
    p1, p2 = math.radians(a["lat"]), math.radians(b["lat"])
    h = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(b["lon"] - a["lon"]) / 2) ** 2
    return 2 * 6371008.8 * math.asin(math.sqrt(min(1.0, h)))


def alternatives(n: dict, sites: list[dict]) -> tuple[list[dict], dict | None]:
    """Listenorte nahe einem zurückgerechneten Vorschlag (`fit`/`global`), die streng ebenfalls gut passen – aus `tested` der Nacht.
    Gut = höchstens eine eindeutige Spur weniger und quer-RMS ≤ max(60″, 3 × Vorschlag) + erwartete Ablage durch den Abstand
    (0,3″ je m: ein 372 m entfernter Ort liegt bei ~700 km Entfernung ~110″ quer daneben – das ist kein schlechter Ort, nur ein verschobener).
    Dazu `near` = nächster Listenort mit Abstand und Einrastgrenze (für den Begründungstext)."""
    p = n.get("proposal") or {}
    if p.get("kind") not in ("fit", "global") or p.get("lat") is None or n.get("applied"):
        return [], None
    acc = float(p.get("accuracy_m") or 100)
    limit = float(p.get("snap_limit_m") or max(SNAP_FIT_M if p["kind"] == "fit" else SNAP_GLOBAL_M, 2 * acc))
    tested = {t["id"]: t for t in n.get("tested") or [] if t.get("id")}
    near, out = None, []
    for s in sites:
        if s.get("lat") is None or s.get("lon") is None:
            continue
        d = _dist_m(s, p)
        if near is None or d < near["distance_m"]:
            near = {"id": s.get("id"), "name": s.get("name"), "distance_m": round(d), "limit_m": round(limit)}
        t = tested.get(s.get("id"))
        if not t or d > max(ALT_NEAR_M, ALT_SIGMA * acc):
            continue
        sure, rms = int(t.get("sure") or 0), t.get("rms")
        if rms is None or sure < 1 or sure < int(p.get("sure") or 0) - 1:
            continue
        if rms > max(GOOD_RMS_ARCSEC, 3 * float(p.get("rms") or 0)) + PARALLAX_ARCSEC_PER_M * d:
            continue
        out.append({"id": s.get("id"), "name": s.get("name"), "distance_m": round(d), "sure": sure, "rms": rms, "tracks": n.get("tracks")})
    out.sort(key=lambda a: (-a["sure"], a["distance_m"]))
    return out[:3], (near if near and near["distance_m"] <= 10000 else None)


def with_alternatives(result: dict | None, sites: list[dict]) -> dict | None:
    """Ergebnis von „Standorte aller Nächte“ um `alternatives`/`near` ergänzen – nach der aktuellen Ortsliste (auch für ältere Suchen)."""
    if not result:
        return result
    out = dict(result, nights=[])
    for n in result.get("nights") or []:
        alts, near = alternatives(n, sites)
        out["nights"].append(dict(n, alternatives=alts, near=near) if (alts or near) else n)
    return out


class SiteJobs:
    SCRIPT = Path(__file__).parent / "engine" / "rematch_sites.py"

    def __init__(self, mod):
        self.mod = mod
        self.lock = threading.RLock()
        self.process = None
        self.job: dict | None = None
        self.stop_file: Path | None = None

    # ---- Zustand ----------------------------------------------------------
    def busy_with(self, run: str | None) -> bool:
        with self.lock:
            # Die Nachtsuche liest nur – sie sperrt keine Läufe (zählt aber als laufender Auftrag)
            return bool(self.process is not None and self.job and (run is None or self.job.get("kind") not in ("nights", "heatmap") and (self.job.get("all") or run in self.job["runs"])))

    def snapshot(self) -> dict | None:
        with self.lock:
            return dict(self.job) if self.job else None

    def signature(self) -> str:
        return sites_signature(self.mod.ctx.store.get("sites"))

    def sites_changed(self) -> bool:
        """Haben sich Orte/Zeiträume seit dem letzten Abgleich aller Läufe geändert? Erster Aufruf merkt sich den Stand."""
        cfg = self.mod.ctx.store.get("skytrace")
        sig = self.signature()
        if not cfg.get("rematched_sites"):
            self.mod.ctx.store.update("skytrace", {"rematched_sites": sig})
            return False
        return cfg["rematched_sites"] != sig

    # ---- Starten ----------------------------------------------------------
    def _runs(self, runs: list[str] | None) -> list[Path]:
        from .api import _read_json
        ctl = self.mod.ctl
        busy = ctl.snapshot(logs=False)
        skip = busy["run"] if busy["running"] else None
        if runs is None:
            root = ctl.results
            found = sorted(p for p in (root.iterdir() if root.exists() else []) if p.is_dir() and (p / "videos.json").is_file())
            return [p for p in found if p.name != skip and any(s.get("candidates_file") for s in _read_json(p / "videos.json", []) or [])]
        paths = [ctl.run_path(r) for r in runs]
        if skip and skip in runs:
            raise ValueError("Dieser Lauf wird gerade analysiert bzw. aktualisiert – bitte danach neu abgleichen.")
        for p in paths:
            if not (p / "videos.json").is_file():
                raise ValueError("Dieser Lauf hat keine Bildfolgen.")
        return paths

    def start_rematch(self, runs: list[str] | None = None, force: bool | None = None) -> dict:
        paths = self._runs(runs)
        if not paths:
            raise ValueError("Keine SkyTrace-Analysen mit Kandidaten vorhanden.")
        force = (runs is not None) if force is None else bool(force)      # einzelner Lauf: alles, alle Läufe: nur geänderte Orte
        title = "Alle SkyTrace-Analysen neu abgleichen" if runs is None else "SkyTrace-Analyse neu abgleichen"
        args = ["rematch", *map(str, paths)] + (["--force"] if force else [])
        return self._launch(dict(kind="rematch", all=runs is None, runs=[p.name for p in paths], force=force, title=title,
                                 signature=self.signature() if runs is None else None), args)

    def start_nights(self, radius_km: float = 10.0, clock: bool = True, global_km: float = 0.0) -> dict:
        """Standorte aller Nächte: Gruppen je Nacht aus der Statistik (neueste vollständige Analyse je Beobachtung).
        `global_km` > 0: ohne passenden Listenort den Ort ohne Vorwissen in diesem Umkreis um die Listenorte suchen (`global_site`)."""
        from .stats import collect
        if not 1 <= float(radius_km) <= 50:
            raise ValueError("Suchradius 1–50 km.")
        if global_km and not 100 <= float(global_km) <= 3000:
            raise ValueError("Suchgebiet ohne Vorwissen 100–3000 km (0 = aus).")
        busy = self.mod.ctl.snapshot(logs=False)
        data = collect(self.mod.ctl.results, skip=busy["run"] if busy["running"] else None)
        groups: dict[str, list] = {}
        for s in data["sessions"]:
            if s.get("night") and s.get("folder") and s.get("candidate_count"):
                groups.setdefault(s["night"], []).append({"run": s["run"], "folder": s["folder"], "session": s["session"]})
        if not groups:
            raise ValueError("Keine SkyTrace-Analysen mit Spuren vorhanden.")
        from json_store import write_json
        path = self.mod.home / "standortsuche-gruppen.json"
        write_json(path, [{"night": n, "observations": o} for n, o in sorted(groups.items(), reverse=True)])
        runs = sorted({o["run"] for obs in groups.values() for o in obs})
        return self._launch(dict(kind="nights", all=False, runs=runs, title="Standorte für alle Nächte suchen", radius_km=float(radius_km), clock=bool(clock),
                                 global_km=float(global_km or 0)),
                            ["nights", str(path), str(self.nights_file), "--radius-km", str(float(radius_km)), "--global-km", str(float(global_km or 0))]
                            + ([] if clock else ["--no-clock"]))

    def start_heatmap(self, night: str, global_km: float = 1500.0, clock: bool = True) -> dict:
        """Heatmap der Rückprojektion für eine Nacht berechnen (auch wenn ein Listenort passt) – nur für die Karte, ändert keinen Vorschlag."""
        import re
        from .api import _read_json
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(night or "")):
            raise ValueError("Ungültige Nacht.")
        if not 100 <= float(global_km) <= 3000:
            raise ValueError("Suchgebiet 100–3000 km.")
        groups = self.mod.home / "standortsuche-gruppen.json"
        if not any(g.get("night") == night for g in _read_json(groups, []) or []):
            raise ValueError("Diese Nacht ist nicht in der letzten Suche – bitte zuerst „Standorte aller Nächte“ suchen.")
        return self._launch(dict(kind="heatmap", all=False, runs=[], night=night, title=f"Heatmap der Standortsuche ({night})", global_km=float(global_km)),
                            ["heatmap", str(groups), night, "--global-km", str(float(global_km))] + ([] if clock else ["--no-clock"]))

    def heat(self, night: str) -> dict | None:
        import re
        from .api import _read_json
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(night or "")):
            raise ValueError("Ungültige Nacht.")
        return _read_json(self.heat_dir / f"{night}.json", None)

    def heat_nights(self) -> list[str]:
        d = self.heat_dir
        return sorted(p.stem for p in d.glob("*.json")) if d.is_dir() else []

    @property
    def heat_dir(self) -> Path:
        return self.mod.home / "standortsuche-karten"

    @property
    def nights_file(self) -> Path:
        return self.mod.home / "standortsuche-naechte.json"

    def nights_result(self) -> dict | None:
        from .api import _read_json
        return _read_json(self.nights_file, None)

    def mark_applied(self, nights: list[str]) -> dict | None:
        """Übernommene Nächte in der gespeicherten Vorschlagsliste kennzeichnen (Liste bleibt nach dem Neuladen richtig)."""
        from json_store import write_json
        with self.lock:
            if self.process is not None and (self.job or {}).get("kind") == "nights":
                raise ValueError("Die Suche läuft noch – bitte warten oder anhalten.")
            data = self.nights_result()
            if not data:
                return None
            for n in data.get("nights") or []:
                if n.get("night") in nights:
                    n["applied"] = time.time()
            write_json(self.nights_file, data)
            return data

    def start_search(self, run: str, folder: str) -> dict:
        from .api import within
        path = self._runs([run])[0]
        if not (within(path, folder) / "kandidaten.json").is_file():
            raise ValueError("Beobachtung nicht gefunden")
        return self._launch(dict(kind="search", all=False, runs=[run], folder=folder, title="Standort der Nacht suchen"),
                            ["search", str(path), folder])

    def _launch(self, job: dict, args: list[str]) -> dict:
        with self.lock:
            if self.process is not None:
                raise ValueError("Ein Neuabgleich läuft bereits – bitte warten.")
            home = self.mod.home
            self.stop_file = home / f"STOP-abgleich-{time.time_ns()}"
            cmd = [self.mod.python, "-u", str(self.SCRIPT), *args, "--stop-file", str(self.stop_file)]
            self.process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
                                            cwd=home, env={**self.mod.engine_env(), "PYTHON_COLORS": "0"}, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            self.job = dict(job, key=f"skytrace:abgleich:{time.time_ns()}", state="running", started=time.time(), finished=None,
                            current=0, total=0, text="Wird vorbereitet …", result=None, error=None, log=[])
            threading.Thread(target=self._read, args=(self.process,), daemon=True).start()
            return dict(self.job)

    def stop(self) -> dict | None:
        with self.lock:
            if self.process is not None and self.stop_file:
                self.stop_file.touch()
                self.job["text"] = "Wird angehalten …"
            return self.snapshot()

    def _read(self, proc) -> None:
        result = error = None
        try:
            for line in proc.stdout:
                try:
                    row = json.loads(line)
                except ValueError:
                    row = {"type": "log", "text": line.strip()}
                if not isinstance(row, dict):
                    continue
                with self.lock:
                    kind = row.get("type")
                    if kind == "progress":
                        self.job.update(current=row.get("current", 0), total=row.get("total", 0))
                    if kind == "result":
                        result = row
                    if kind == "error":
                        error = row.get("text")
                    if row.get("text"):
                        self.job["text"] = row["text"]
                        self.job["log"] = (self.job["log"] + [row["text"]])[-40:]
        finally:
            code = proc.wait()
            if self.stop_file:
                self.stop_file.unlink(missing_ok=True)
            with self.lock:
                self.process = None
                job = self.job
                job["finished"] = time.time()
                job["result"] = result
                stopped = bool(result and result.get("stopped"))
                if code or error or not result:
                    job.update(state="error", error=error or (job["log"][-1] if job["log"] else f"Engine beendet mit Code {code}"))
                else:
                    job["state"] = "stopped" if stopped else "done"
                if job["state"] == "done" and job.get("signature"):
                    # Stand der Ortsliste beim Start: ändert sie sich währenddessen, bleibt der Hinweis bestehen
                    self.mod.ctx.store.update("skytrace", {"rematched_sites": job["signature"]})

    # ---- Anzeige ----------------------------------------------------------
    def activity(self) -> list[dict]:
        j = self.snapshot()
        if not j:
            return []
        detail = j.get("error") if j["state"] == "error" else j.get("text")
        # gleiche Regel wie rmHref in skytrace.js (die offene Ansicht meldet das Ende selbst, mit Zahlen)
        href = "#/skytrace/standorte" if j.get("kind") in ("nights", "heatmap") or len(j["runs"]) > 1 and not j.get("all") else "#/skytrace/statistik" if j.get("all") else f"#/skytrace/{j['runs'][0]}/beobachtungen" if j["runs"] else "#/skytrace"
        return [{"key": j["key"], "title": j["title"], "kind": "Spurenanalyse", "detail": detail, "state": j["state"],
                 "progress": (j["current"] / j["total"]) if j["state"] == "running" and j.get("total") else None,
                 "started": j["started"], "finished": j["finished"], "href": href}]

    def search_result(self, run: str, folder: str) -> dict | None:
        from .api import _read_json, within
        path = self.mod.ctl.run_path(run)
        return _read_json(within(path, folder) / "standortsuche.json", None)
