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
import sys
import threading
import time
import traceback
from pathlib import Path

from ...core.errors import user_message


def _is_upload(path: Path) -> bool:
    """Hochgeladene Rohbilder (`input/uploads/…`) – die löscht das Bilder-Modul direkt nach `stacking.stacked`."""
    parts = [p.lower() for p in Path(path).parts]
    return any(a == "input" and b == "uploads" for a, b in zip(parts, parts[1:]))


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
        # Server-Ende während einer Kopie: ohne gemerkte Kopierliste (alter Stand) die halbe Nacht verwerfen;
        # mit Liste setzt `start` die Kopie fort – schon gesicherte Uploads bleiben so erhalten
        for q in [q for q in self.state["queue"] if q.get("state") == "staging" and not q.get("copies")]:
            shutil.rmtree(self.dir / q["folder"], ignore_errors=True)
            self.state["queue"].remove(q)
            self.state["sent"] = {k: v for k, v in self.state["sent"].items() if v != q["folder"]}

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
                # wie Stellina „<Datum>_observation_<Objekt>“ – so zeigt SkyTrace den Objektnamen als Titel
                folder = self.dir / f"{night}_observation_{safe}"
                n = 2
                while folder.exists():
                    folder = self.dir / f"{night}_observation_{safe}_{n}"; n += 1
                # Synchron nur Hardlinks (schnell, Pipeline wartet); Kopien über Datenträger hinweg im eigenen Thread.
                # Fehler: halben Ordner wieder entfernen, die Aufnahmen gelten nicht als übergeben.
                copies = []
                try:
                    folder.mkdir(parents=True)
                    for i, f in enumerate(items):
                        src = Path(f["path"])
                        dst = folder / f"{i:05d}_{src.name}"
                        try:
                            os.link(src, dst)     # gleicher Datenträger: kein Platzbedarf, überlebt das Löschen des Uploads
                        except OSError:
                            if _is_upload(src):           # Upload löscht das Bilder-Modul gleich nach dem Ereignis: sofort kopieren
                                shutil.copy2(src, dst)
                            else:
                                copies.append((src, dst))     # anderer Datenträger (eingebundener Ordner): im Thread
                except OSError:
                    traceback.print_exc()
                    shutil.rmtree(folder, ignore_errors=True)
                    continue
                item = {"folder": folder.name, "project": project_id, "name": name, "night": night, "frames": len(items),
                        "queued": time.time(), "state": "staging" if copies else "queued", "run": None}
                for f in items:
                    self.state["sent"][f["id"]] = folder.name
                self.state["queue"].append(item)
                if copies:
                    item["copies"] = [[str(a), str(b)] for a, b in copies]     # für die Fortsetzung nach einem Neustart
                    item["ids"] = [f["id"] for f in items]
                    self._start_copy(item)
            self._save()
        self.wake.set()

    def _start_copy(self, item: dict):
        threading.Thread(target=self._copy, args=(item, [(Path(a), Path(b)) for a, b in item["copies"]], item.get("ids") or []),
                         daemon=True, name="skytrace-auto-copy").start()

    def _copy(self, item: dict, copies: list, ids: list):
        """Kopien für Aufnahmen auf anderen Datenträgern; scheitert eine, wird die Nacht nicht übergeben."""
        try:
            for src, dst in copies:
                if dst.exists() and (not src.exists() or dst.stat().st_size == src.stat().st_size):
                    continue                       # schon vor einem Neustart kopiert
                try:
                    shutil.copy2(src, dst)
                except FileNotFoundError:          # inzwischen gelöscht: ohne diese Aufnahme weiter
                    pass
            state = "queued" if sum(1 for _ in (self.dir / item["folder"]).iterdir()) >= 2 else None
            if not state:
                shutil.rmtree(self.dir / item["folder"], ignore_errors=True)
        except OSError:
            traceback.print_exc()
            shutil.rmtree(self.dir / item["folder"], ignore_errors=True)
            state = None
        with self.lock:
            if state:
                item["state"] = state
                item.pop("copies", None)
                item.pop("ids", None)
            else:
                self.state["queue"] = [q for q in self.state["queue"] if q is not item]
                for fid in ids:
                    if self.state["sent"].get(fid) == item["folder"]:
                        del self.state["sent"][fid]
            self._save()
        self.wake.set()

    def retry_waiting(self) -> int:
        """Übersprungene/fehlgeschlagene Nächte, deren Aufnahmen noch bereitliegen, erneut einreihen (z. B. nach dem
        Einrichten der Engine)."""
        with self.lock:
            again = [d for d in self.state["done"] if d.get("state") in ("skipped", "error") and (self.dir / d["folder"]).is_dir()]
            for d in again:
                d.update(state="queued", run=None, message=None, finished=None, queued=time.time())
            self.state["done"] = [d for d in self.state["done"] if d not in again]
            self.state["queue"] += again
            if again:
                self._save()
        if again:
            self.wake.set()
        return len(again)

    # ---- Warteschlange ------------------------------------------------------------
    def start(self):
        with self.lock:
            for q in self.state["queue"]:
                if q.get("state") == "staging" and q.get("copies"):
                    self._start_copy(q)                  # nach Neustart unterbrochene Kopie fortsetzen
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
            if (not health.get("ok") and health.get("missing") and health.get("python") == sys.executable
                    and not self.mod.ctx.store.get("skytrace").get("python")):
                # Engine gar nicht eingerichtet (Standard bei neuen Installationen): still überspringen statt Fehlermeldung je Nacht
                self._finish(item, "skipped", "SkyTrace-Analyse-Engine nicht installiert")
                continue
            if not health.get("ok") or not folder.is_dir():
                self._finish(item, "error", (health.get("error") or "Analyse-Engine unvollständig – Einstellungen → SkyTrace") if folder.is_dir()
                             else "Aufnahmen nicht mehr vorhanden")
                continue
            try:
                snap = ctl.launch({"source": str(self.dir), "observation": item["folder"], "samples": 0, "fps": 8,
                                   "solve": True, "remember": False})
            except (ValueError, OSError) as e:
                if ctl.process is not None:          # gerade jemand anderes gestartet → später erneut
                    self.stopped.wait(10)
                    continue
                self._finish(item, "error", user_message(e, "Analyse nicht startbar – Details im Server-Protokoll."))
                continue
            with self.lock:
                item.update(state="running", run=snap["run"], started=time.time())
                self._save()
            while ctl.process is not None and not self.stopped.is_set():
                self.stopped.wait(5)
            if self.stopped.is_set():
                return
            out = ctl.outcome(item["run"])           # Ergebnis genau dieses Laufs (nicht eines inzwischen manuell gestarteten)
            if out is None:
                self._finish(item, "error", "Ausgang des Laufs unbekannt")
            else:
                self._finish(item, "error" if out["failed"] else "stopped" if out["stopping"] else "done", out["status"])

    def _keep_frames(self, item: dict):
        """Aufnahmen, die der Bericht braucht (erstes Bild, je zwei vor/nach jedem Treffer), in den Ergebnisordner
        übernehmen und die Pfade im Lauf umschreiben – hochgeladene Rohbilder hat das Bilder-Modul oft schon gelöscht,
        ohne sie ließe sich der Lauf nicht mehr aktualisieren (M45 2022-09-22)."""
        run = self.mod.ctl.results / item["run"] if item.get("run") else None
        staged = self.dir / item["folder"]
        if not run or not run.is_dir() or not staged.is_dir():
            return
        for session in [p for p in run.iterdir() if (p / "frames.jsonl").exists()]:
            files, hits, reference = {}, set(), None
            for name in ("frames.jsonl", "ausgeschlossen.jsonl"):
                path = session / name
                for line in path.read_text(encoding="utf-8").splitlines() if path.exists() else []:
                    row = json.loads(line)
                    files[row["index"]] = row["file"]
                    if name == "frames.jsonl":
                        reference = row["index"] if reference is None else reference   # erste akzeptierte Aufnahme
                        if row.get("detections"):
                            hits.add(row["index"])
            if reference is None:
                continue
            # Referenz = erste Zeile in frames.jsonl (candidate_report lädt sie immer), nicht ein ausgeschlossenes Anfangsbild
            keep = {reference} | {i + d for i in hits for d in range(-2, 3)}
            target = session / "rohbilder"
            moved = {}
            for i in sorted(keep & files.keys()):
                src = Path(files[i])
                if src.parent != staged or not src.exists():
                    continue
                target.mkdir(exist_ok=True)
                dst = target / src.name
                try:
                    os.replace(src, dst)
                except OSError:
                    shutil.copy2(src, dst)
                moved[str(src)] = str(dst)
            if not moved:
                continue
            uris = {Path(old).as_uri(): Path(dst).as_uri() for old, dst in moved.items()}

            def swap(value):                      # Pfade und file://-Verweise in den dekodierten Werten (Unicode-sicher)
                if isinstance(value, str):
                    return moved.get(value) or uris.get(value) or value
                if isinstance(value, list):
                    return [swap(v) for v in value]
                if isinstance(value, dict):
                    return {k: swap(v) for k, v in value.items()}
                return value
            from json_store import replace_file, write_json
            for path in session.glob("*.jsonl"):  # atomar veröffentlichen – Browser und Bericht lesen evtl. gleichzeitig
                rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
                tmp = path.with_name(path.name + ".tmp")
                tmp.write_text("".join(json.dumps(swap(r), ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
                replace_file(tmp, path)
            for path in [*session.glob("kandidaten.json"), *session.glob("kandidaten/*/kandidat.json")]:
                write_json(path, swap(json.loads(path.read_text(encoding="utf-8"))))

    def _finish(self, item: dict, state: str, message: str | None):
        if state == "done":
            try:
                self._keep_frames(item)
            except Exception:
                traceback.print_exc()
        if state in ("done", "stopped"):
            shutil.rmtree(self.dir / item["folder"], ignore_errors=True)
        # bei Fehler/fehlender Engine bleiben die Aufnahmen liegen – `retry_waiting` reiht sie später erneut ein
        # (sonst wäre die Nacht verloren, wenn das Bilder-Modul die hochgeladenen Rohbilder schon gelöscht hat)
        with self.lock:
            item.update(state=state, message=message, finished=time.time())
            self.state["queue"] = [q for q in self.state["queue"] if q is not item]
            done = self.state["done"] + [item]
            for old in done[:-30]:                  # aus der Liste gefallen: liegengebliebene Aufnahmen nicht ewig behalten
                if old.get("state") in ("skipped", "error"):
                    shutil.rmtree(self.dir / old["folder"], ignore_errors=True)
            self.state["done"] = done[-30:]
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
