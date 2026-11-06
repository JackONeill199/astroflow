"""Portables Windows-Paket in sauberer Umgebung prüfen und „Minuten bis zum ersten Bild“ messen.

    python installer/pruefe_paket.py dist/AstroFlow-<Version>-Windows-portabel.zip [--astap] [--keep]

Entpackt in einen leeren Ordner, startet `AstroFlow.cmd` mit leerem Benutzerprofil (USERPROFILE/APPDATA/LOCALAPPDATA im
Testordner) und einem PATH ohne Python, geht die Schritte des Einrichtungsassistenten über dieselbe API wie die Oberfläche durch
(Teleskop, optional ASTAP + D05 von SourceForge, Demo) und wartet auf das erste fertige Bild. Danach `AstroFlow beenden.cmd`.
Läuft auch in der Windows-Sandbox (installer/AstroFlow-Test.wsb).
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def call(port: int, path: str, body: dict | None = None, timeout: float = 30):
    req = urllib.request.Request(f"http://127.0.0.1:{port}/api{path}", method="POST" if body is not None else "GET",
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def wait(fn, timeout: float, step: float = 1.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            v = fn()
            if v:
                return v
        except Exception:
            pass
        time.sleep(step)
    raise TimeoutError


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("zip")
    ap.add_argument("--astap", action="store_true", help="ASTAP + D05 wirklich herunterladen (~100 MB)")
    ap.add_argument("--keep", action="store_true", help="Testordner behalten")
    ap.add_argument("--dir", default=None)
    a = ap.parse_args()

    root = Path(a.dir or tempfile.mkdtemp(prefix="astroflow-sauber-"))
    home = root / "home"
    for d in (home / "AppData" / "Roaming", home / "AppData" / "Local", root / "tmp"):
        d.mkdir(parents=True, exist_ok=True)
    win = os.environ.get("SystemRoot", r"C:\Windows")
    port = free_port()
    env = {"SystemRoot": win, "WINDIR": win, "ComSpec": os.path.join(win, "System32", "cmd.exe"),
           "PATH": os.pathsep.join([os.path.join(win, "System32"), win, os.path.join(win, "System32", "WindowsPowerShell", "v1.0")]),
           "PATHEXT": ".COM;.EXE;.BAT;.CMD", "USERPROFILE": str(home), "HOMEDRIVE": str(home)[:2], "HOMEPATH": str(home)[2:],
           "APPDATA": str(home / "AppData" / "Roaming"), "LOCALAPPDATA": str(home / "AppData" / "Local"),
           "TEMP": str(root / "tmp"), "TMP": str(root / "tmp"), "USERNAME": "test", "ASTROFLOW_PORT": str(port), "ASTROFLOW_NO_BROWSER": "1"}
    r = subprocess.run(["where", "python"], env=env, capture_output=True, text=True)
    assert r.returncode != 0, f"Python im PATH gefunden: {r.stdout}"
    times, t0 = {}, time.time()
    with zipfile.ZipFile(a.zip) as z:
        z.extractall(root)
    app = root / "AstroFlow"
    times["entpacken_s"] = round(time.time() - t0, 1)
    print(f"Testordner {root}, Port {port}, entpackt in {times['entpacken_s']} s", flush=True)

    t1 = time.time()
    subprocess.Popen(["cmd", "/c", str(app / "AstroFlow.cmd")], env=env, cwd=str(app),
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    wait(lambda: call(port, "/status"), 120, 0.5)
    times["start_s"] = round(time.time() - t1, 1)
    info = call(port, "/app")
    st = call(port, "/setup")
    assert info["setup"]["pending"] and st["launched"], "Einrichtungsassistent sollte beim ersten Start erscheinen"
    assert Path(st["data"]["path"]).resolve() == (app / "Daten").resolve(), st["data"]["path"]
    times["astap_vorher"] = st["astap"]["path"] or "keins"             # z. B. systemweit unter „Program Files“
    print(f"Server läuft nach {times['start_s']} s, Version {info['version']}, Daten {st['data']['path']}", flush=True)

    call(port, "/setup", {"telescope": "stellina"})
    if a.astap:
        t2 = time.time()
        call(port, "/setup/astap", {"db": "d05"})
        def astap_done():
            t = call(port, "/setup")["tasks"].get("astap") or {}
            if t.get("state") == "error":
                raise SystemExit("ASTAP: " + t.get("detail", ""))
            return t.get("state") == "done" and t
        print("ASTAP:", wait(astap_done, 1200, 2)["detail"], flush=True)
        times["astap_s"] = round(time.time() - t2, 1)
        s2 = call(port, "/setup")["astap"]
        assert s2["path"] and Path(s2["path"]).parent == Path(s2["target"]) and "D05" in s2["databases"], s2

    t3 = time.time()
    call(port, "/setup/demo", {})
    res = wait(lambda: next((p["result"] for p in call(port, "/stacking/projects") if p.get("result")), None), 900, 2)
    times["demo_bis_bild_s"] = round(time.time() - t3, 1)
    jpg = urllib.request.urlopen(f"http://127.0.0.1:{port}/api/stacking/results/{res['id']}/jpg", timeout=30).read()
    assert jpg[:2] == b"\xff\xd8" and len(jpg) > 5000, "kein JPG"
    call(port, "/setup", {"done": True})
    times["gesamt_min"] = round((time.time() - t0) / 60, 2)
    print(f"Erstes Bild nach {times['demo_bis_bild_s']} s ({res['n_frames']} Aufnahmen, {len(jpg) // 1024} KB JPG)", flush=True)

    t4 = time.time()
    subprocess.run(["cmd", "/c", str(app / "AstroFlow beenden.cmd")], env=env, cwd=str(app), capture_output=True, timeout=120)
    with socket.socket() as s:
        s.settimeout(1)
        assert s.connect_ex(("127.0.0.1", port)) != 0, "Server läuft nach dem Beenden noch"
    times["beenden_s"] = round(time.time() - t4, 1)
    print(json.dumps(times, indent=2))
    if not a.keep:
        time.sleep(1)
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
