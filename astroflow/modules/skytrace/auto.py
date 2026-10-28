"""Gestackte Aufnahmen automatisch mit SkyTrace analysieren.

Nach jedem Stacking meldet das Bilder-Modul `stacking.stacked`. Die noch nicht analysierten Light-Aufnahmen des
Objekts werden je Nacht als Beobachtungsordner aus Hardlinks bereitgestellt (`<daten>/skytrace/auto/…`) –
so bleiben sie erhalten, auch wenn das Bilder-Modul hochgeladene Rohbilder direkt danach löscht, und die
Originale werden nie verändert. Eine Warteschlange startet die Läufe nacheinander, sobald SkyTrace frei ist;
danach wird der Ordner wieder entfernt (Ergebnisse liegen wie gewohnt in `Ergebnisse/`).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import threading
import time
from pathlib import Path


class AutoAnalysis:
    def __init__(self, mod):
        self.mod = mod
        self.dir = mod.ctx.module_dir("skytrace") / "auto"
        self.file = mod.ctx.module_dir("skytrace") / "auto.json"
        self.lock = threading.RLock()
        self.wake = threading.Event()
        self.stopped = threading.Event()
        self.thread = None
        try:
            self.state = json.loads(self.file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.state = {}
        self.state.setdefault("sent", {})          # Frame-ID → Ordnername
        self.state.setdefault("queue", [])         # wartende/laufende Ordner
        self.state.setdefault("done", [])          # erledigte (für die Aktivität)

    @property
    def enabled(self) -> bool:
        return self.mod.ctx.store.get("skytrace").get("auto", True) is not False

    def _save(self):
        tmp = self.file.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.state, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.file)

    # ---- Ereignis aus dem Bilder-Modul ------------------------------------------
    def stacked(self, project_id: int, result_id: int, name: str = ""):
        if not self.enabled:
            return
        frames = (self.mod.ctx.service("project_frames") or (lambda pid: []))(project_id)
        with self.lock:
            nights: dict[str, list[dict]] = {}
            for f in frames:
                if f["id"] not in self.state["sent"]:
                    nights.setdefault(f["night"] or "ohne-datum", []).append(f)
            for night, items in sorted(nights.items()):
                if len(items) < 2:                 # eine Bildfolge braucht mindestens zwei Aufnahmen
                    continue
                safe = re.sub(r"[^\w.-]+", "_", name or f"Objekt_{project_id}").strip("_")[:60]
                folder = self.dir / f"{safe}_observation_{night}-p{project_id}-{int(time.time())}"
                folder.mkdir(parents=True, exist_ok=True)
                for i, f in enumerate(items):
                    src = Path(f["path"])
                    dst = folder / f"{i:05d}_{src.name}"
                    try:
                        os.link(src, dst)         # gleicher Datenträger: kein Platzbedarf, überlebt das Löschen des Uploads
                    except OSError:
                        shutil.copy2(src, dst)    # anderer Datenträger (eingebundener Ordner)
                    self.state["sent"][f["id"]] = folder.name
                self.state["queue"].append({"folder": folder.name, "project": project_id, "name": name, "night": night,
                                            "frames": len(items), "queued": time.time(), "state": "queued", "run": None})
            self._save()
        self.wake.set()

    # ---- Warteschlange ------------------------------------------------------------
    def start(self):
        self.thread = threading.Thread(target=self.loop, daemon=True)
        self.thread.start()

    def stop(self):
        self.stopped.set()
        self.wake.set()

    def loop(self):
        ctl = self.mod.ctl
        while not self.stopped.is_set():
            with self.lock:
                item = next((q for q in self.state["queue"] if q["state"] in ("queued", "running")), None)
            if item is None:
                self.wake.wait(30)
                self.wake.clear()
                continue
            if item["state"] == "running":           # nach Neustart: Lauf war unterbrochen → neu starten
                item["state"] = "queued"
            health = self.mod.health
            if ctl.process is not None or not health.get("checked"):
                self.stopped.wait(10)                # SkyTrace ist belegt (z. B. manuelle Analyse) oder Prüfung läuft
                continue
            folder = self.dir / item["folder"]
            if not health.get("ok") or not folder.is_dir():
                self._finish(item, "error", "Analyse-Engine unvollständig – Einstellungen → SkyTrace" if folder.is_dir()
                             else "Aufnahmen nicht mehr vorhanden")
                continue
            try:
                snap = ctl.launch({"source": str(self.dir), "observation": item["folder"], "samples": 0, "fps": 8,
                                   "solve": True, "remember": False})
            except (ValueError, OSError) as e:
                if ctl.process is not None:          # gerade jemand anderes gestartet → später erneut
                    self.stopped.wait(10)
                    continue
                self._finish(item, "error", str(e))
                continue
            with self.lock:
                item.update(state="running", run=snap["run"], started=time.time())
                self._save()
            while ctl.process is not None and not self.stopped.is_set():
                self.stopped.wait(5)
            if self.stopped.is_set():
                return
            snap = ctl.snapshot(logs=False)
            self._finish(item, "error" if snap.get("failed") else "stopped" if snap.get("stopping") else "done", snap.get("status"))

    def _finish(self, item: dict, state: str, message: str | None):
        shutil.rmtree(self.dir / item["folder"], ignore_errors=True)
        with self.lock:
            item.update(state=state, message=message, finished=time.time())
            self.state["queue"] = [q for q in self.state["queue"] if q is not item]
            self.state["done"] = (self.state["done"] + [item])[-30:]
            self._save()

    def activity(self) -> list[dict]:
        out = []
        with self.lock:
            for q in self.state["queue"]:
                if q["state"] == "queued":
                    out.append({"key": f"skytrace-auto:{q['folder']}", "title": f"{q['name'] or 'Objekt'} · Nacht {q['night']}",
                                "kind": "Spurenanalyse (automatisch)", "detail": f"{q['frames']} Aufnahmen · wartet auf SkyTrace",
                                "state": "queued", "progress": None, "started": q["queued"], "finished": None, "href": "#/skytrace"})
            for q in self.state["done"]:
                if q["state"] == "error":
                    out.append({"key": f"skytrace-auto:{q['folder']}", "title": f"{q['name'] or 'Objekt'} · Nacht {q['night']}",
                                "kind": "Spurenanalyse (automatisch)", "detail": q.get("message") or "Fehler", "state": "error",
                                "progress": None, "started": q["queued"], "finished": q.get("finished"), "href": "#/skytrace"})
        return out

    def waiting(self) -> int:
        with self.lock:
            return sum(q["state"] == "queued" for q in self.state["queue"])
