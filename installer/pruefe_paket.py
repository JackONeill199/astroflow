"""Windows-Paket in sauberer Umgebung prüfen und „Minuten bis zum ersten Bild“ messen.

    python installer/pruefe_paket.py dist/AstroFlow-<Version>-Windows-portabel.zip [--astap] [--keep]
    python installer/pruefe_paket.py --setup dist/AstroFlow-Setup-<Version>.exe [--astap]     # Installer (echtes Benutzerprofil!)

Installer-Modus: still installieren (wie Doppelklick, ohne Rückfragen), Start wie über den Startmenü-Eintrag, Assistent + Demo,
Beenden, still deinstallieren; danach müssen Programmordner weg und Datenordner (%USERPROFILE%\\AstroFlow) noch da sein.
Nur in einer Sandbox/VM ausführen – er installiert in das echte Profil des laufenden Nutzers.

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
import urllib.error
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
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:                          # Fehlermeldung des Servers mit ausgeben
        raise RuntimeError(f"{path}: HTTP {e.code} {e.read().decode('utf-8', 'replace')[:300]}") from None


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


def flow(port: int, times: dict, t0: float, astap: bool):
    """Assistent wie in der Oberfläche: Teleskop, optional ASTAP + D05, Demo → erstes Bild."""
    call(port, "/setup", {"telescope": "stellina"})
    if astap:
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



def check_setup(exe: Path, astap: bool):
    env = {k: v for k, v in os.environ.items() if not k.startswith("PYTHON")}
    with socket.socket() as sk:                                   # Standardport wie nach dem Installieren, falls frei
        port = free_port() if sk.connect_ex(("127.0.0.1", 8766)) == 0 else 8766
    env.update(ASTROFLOW_PORT=str(port), ASTROFLOW_NO_BROWSER="1")
    for k in ("ASTROFLOW_DATA", "ASTROFLOW_HOME"):
        env.pop(k, None)
    app = Path(os.environ["LOCALAPPDATA"]) / "Programs" / "AstroFlow"
    data = Path(os.environ["USERPROFILE"]) / "AstroFlow"
    times, t0 = {}, time.time()
    subprocess.run([str(exe), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/CURRENTUSER", "/NORESTART"], check=True, timeout=900)
    times["installieren_s"] = round(time.time() - t0, 1)
    pyw = app / "python" / "pythonw.exe"
    assert pyw.is_file(), f"Installation fehlt: {pyw}"
    menu = Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "AstroFlow"
    times["startmenue"] = sorted(p.name for p in menu.glob("*.lnk"))
    print(f"Installiert nach {times['installieren_s']} s in {app}, Startmenü: {times['startmenue']}", flush=True)
    t1 = time.time()
    subprocess.Popen([str(pyw), "-I", "-m", "astroflow", "start"], env=env, cwd=str(app))    # = Startmenü „AstroFlow“
    wait(lambda: call(port, "/status"), 180, 0.5)
    times["start_s"] = round(time.time() - t1, 1)
    st = call(port, "/setup")
    assert call(port, "/app")["setup"]["pending"] and st["launched"], "Einrichtungsassistent sollte erscheinen"
    assert Path(st["data"]["path"]).resolve() == data.resolve(), st["data"]["path"]
    times["astap_vorher"] = st["astap"]["path"] or "keins"
    print(f"Server läuft nach {times['start_s']} s, Daten {st['data']['path']}", flush=True)
    flow(port, times, t0, astap)
    t4 = time.time()
    subprocess.run([str(pyw), "-I", "-m", "astroflow", "stop"], env=env, cwd=str(app), timeout=120)   # = „AstroFlow beenden“
    with socket.socket() as s:
        s.settimeout(1)
        assert s.connect_ex(("127.0.0.1", port)) != 0, "Server läuft nach dem Beenden noch"
    times["beenden_s"] = round(time.time() - t4, 1)
    t5 = time.time()
    subprocess.run([str(app / "unins000.exe"), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"], check=True, timeout=300)
    wait(lambda: not (app / "python").exists(), 120, 1)          # Deinstaller kopiert sich und läuft kurz weiter
    times["deinstallieren_s"] = round(time.time() - t5, 1)
    assert not menu.exists() or not list(menu.glob("*.lnk")), "Startmenü-Einträge nach Deinstallation noch da"
    assert (data / "astroflow.db").is_file(), "Daten sollten nach der Deinstallation bleiben"
    print(json.dumps(times, indent=2, ensure_ascii=False))

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("zip", nargs="?")
    ap.add_argument("--setup", default=None, help="Installer prüfen statt ZIP (nur in Sandbox/VM)")
    ap.add_argument("--astap", action="store_true", help="ASTAP + D05 wirklich herunterladen (~100 MB)")
    ap.add_argument("--keep", action="store_true", help="Testordner behalten")
    ap.add_argument("--dir", default=None)
    a = ap.parse_args()
    if a.setup:
        return check_setup(Path(a.setup), a.astap)

    root = Path(a.dir or tempfile.mkdtemp(prefix="astroflow-sauber-"))
    home = root / "home"
    for d in (home / "AppData" / "Roaming", home / "AppData" / "Local", root / "tmp"):
        d.mkdir(parents=True, exist_ok=True)
    win = os.environ.get("SystemRoot", r"C:\Windows")
    port = free_port()
    env = {"SystemRoot": win, "WINDIR": win, "ComSpec": os.path.join(win, "System32", "cmd.exe"),
           "PATH": os.pathsep.join([os.path.join(win, "System32"), win, os.path.join(win, "System32", "WindowsPowerShell", "v1.0")]),
           "PATHEXT": ".COM;.EXE;.BAT;.CMD", "PROCESSOR_ARCHITECTURE": os.environ.get("PROCESSOR_ARCHITECTURE", "AMD64"), "USERPROFILE": str(home), "HOMEDRIVE": str(home)[:2], "HOMEPATH": str(home)[2:],
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

    flow(port, times, t0, a.astap)

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
