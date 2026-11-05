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
            return bool(self.process is not None and self.job and (run is None or self.job.get("all") or run in self.job["runs"]))

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
        href = "#/skytrace/statistik" if j.get("all") else f"#/skytrace/{j['runs'][0]}/beobachtungen" if j["runs"] else "#/skytrace"
        return [{"key": j["key"], "title": j["title"], "kind": "Spurenanalyse", "detail": detail, "state": j["state"],
                 "progress": (j["current"] / j["total"]) if j["state"] == "running" and j.get("total") else None,
                 "started": j["started"], "finished": j["finished"], "href": href}]

    def search_result(self, run: str, folder: str) -> dict | None:
        from .api import _read_json, within
        path = self.mod.ctl.run_path(run)
        return _read_json(within(path, folder) / "standortsuche.json", None)
