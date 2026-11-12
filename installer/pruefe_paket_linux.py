"""Linux-Paket in sauberer Umgebung prüfen und „Minuten bis zum ersten Bild“ messen.

    python3 installer/pruefe_paket_linux.py dist/AstroFlow-<Version>-Linux-x86_64.tar.gz [--astap] [--keep] [--dir D]

Alles in einem leeren Testordner: eigenes HOME und XDG-Ordner, PATH ohne Python (nur /usr/bin:/bin – ein System-Python dort darf
nicht benutzt werden: der Server muss aus dem Paket laufen), keine grafische Sitzung (kein Browser, kein Infobereich).
1. Entpacken, `install.sh --no-start --autostart` → Programmordner, Befehl, Menüeintrag, Symbol, Autostart vorhanden.
2. `astroflow start` (wie der Menüeintrag) → Assistent über dieselbe API wie die Oberfläche (Teleskop, optional ASTAP + D05 von
   SourceForge, Demo) → erstes Bild. Der Server muss das Python des Pakets nutzen.
3. `astroflow stop`, Update (install.sh desselben Pakets erneut: Daten bleiben), `astroflow-deinstallieren` → Programm, Befehl,
   Menüeintrag, Autostart weg; Datenordner ~/AstroFlow bleibt.
4. Portabel: `./AstroFlow` aus dem entpackten Ordner → Daten unter `AstroFlow/Daten`, `./AstroFlow-beenden`.
Nur Standardbibliothek; läuft auch in Containern (debian, fedora …) mit irgendeinem python3 als Prüfwerkzeug.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pruefe_paket import call, flow, free_port, wait  # noqa: E402


def port_closed(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(1)
        return s.connect_ex(("127.0.0.1", port)) != 0


def server_python(port: int) -> str:
    """Programmpfad des Server-Prozesses (`… -m astroflow serve … --port P`), über /proc ohne Zusatzwerkzeuge."""
    for d in Path("/proc").glob("[0-9]*"):
        try:
            args = (d / "cmdline").read_bytes().split(b"\0")
            if b"serve" in args and str(port).encode() in args and b"astroflow" in args:
                return os.readlink(d / "exe")
        except OSError:
            continue
    return ""


def run(cmd: list[str], env: dict, timeout: float = 300, **kw) -> subprocess.CompletedProcess:
    r = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL, **kw)
    if r.returncode:
        raise SystemExit(f"{' '.join(cmd)} → {r.returncode}\n{r.stdout[-2000:]}\n{r.stderr[-2000:]}")
    return r


def show_log(home: Path, data: Path | None = None):
    for f in ((data or home / "AstroFlow") / "server.log.err",):
        if f.is_file():
            print("--- server.log.err (Ende)\n" + f.read_text(encoding="utf-8", errors="replace")[-4000:], flush=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("tar")
    ap.add_argument("--astap", action="store_true", help="ASTAP + D05 wirklich herunterladen (~100 MB)")
    ap.add_argument("--keep", action="store_true", help="Testordner behalten")
    ap.add_argument("--dir", default=None)
    a = ap.parse_args()

    root = Path(a.dir or tempfile.mkdtemp(prefix="astroflow-sauber-")).resolve()
    home = root / "home"
    for d in (home, root / "tmp"):
        d.mkdir(parents=True, exist_ok=True)
    port = free_port()
    env = {"HOME": str(home), "USER": "test", "LOGNAME": "test", "LANG": "C.UTF-8", "PATH": "/usr/bin:/bin", "TMPDIR": str(root / "tmp"),
           "XDG_CONFIG_HOME": str(home / ".config"), "XDG_DATA_HOME": str(home / ".local" / "share"),
           "ASTROFLOW_PORT": str(port), "ASTROFLOW_NO_BROWSER": "1", "ASTROFLOW_NO_TRAY": "1"}
    times, t0 = {}, time.time()
    with tarfile.open(a.tar) as t:                     # Debian 12 hat Python 3.11.2 – dort noch ohne filter-Argument
        t.extractall(root / "paket", **({"filter": "tar"} if hasattr(tarfile, "tar_filter") else {}))
    src = root / "paket" / "AstroFlow"
    times["entpacken_s"] = round(time.time() - t0, 1)
    for f in ("AstroFlow", "AstroFlow-beenden", "install.sh", "uninstall.sh"):
        assert os.access(src / f, os.X_OK), f"{f} nicht ausführbar"
        assert b"\r\n" not in (src / f).read_bytes(), f"{f} hat Windows-Zeilenenden"
    print(f"Testordner {root}, Port {port}, entpackt in {times['entpacken_s']} s", flush=True)

    # 1) Installieren
    t1 = time.time()
    print(run([str(src / "install.sh"), "--no-start", "--autostart"], env, cwd=str(src)).stdout, flush=True)
    times["installieren_s"] = round(time.time() - t1, 1)
    share = home / ".local" / "share"
    app, wrapper = share / "astroflow" / "app", home / ".local" / "bin" / "astroflow"
    desktop, autostart = share / "applications" / "astroflow.desktop", home / ".config" / "autostart" / "astroflow.desktop"
    for f in (app / "python" / "bin" / "python3", wrapper, home / ".local" / "bin" / "astroflow-deinstallieren", desktop,
              share / "icons" / "hicolor" / "256x256" / "apps" / "astroflow.png", autostart):
        assert f.exists(), f"fehlt nach der Installation: {f}"
    dtext = desktop.read_text(encoding="utf-8")
    assert f'Exec="{wrapper}" start' in dtext and "[Desktop Action beenden]" in dtext, dtext
    assert str(app / "python" / "bin" / "python3") in autostart.read_text(encoding="utf-8")
    df = shutil.which("desktop-file-validate")
    if df:
        r = subprocess.run([df, str(desktop)], capture_output=True, text=True)
        assert r.returncode == 0, r.stdout + r.stderr

    # 2) Start wie aus dem Menü, Assistent, Demo
    t2 = time.time()
    run([str(wrapper), "start"], env, timeout=120)
    wait(lambda: call(port, "/status"), 120, 0.5)
    times["start_s"] = round(time.time() - t2, 1)
    info, st = call(port, "/app"), call(port, "/setup")
    assert info["setup"]["pending"] and st["launched"], "Einrichtungsassistent sollte beim ersten Start erscheinen"
    assert Path(st["data"]["path"]).resolve() == (home / "AstroFlow").resolve(), st["data"]["path"]
    assert st["platform"] == "Linux"
    exe = server_python(port)
    assert not exe or exe.startswith(str(app)), f"Server läuft nicht mit dem Python des Pakets: {exe}"
    print(f"Server läuft nach {times['start_s']} s, Version {info['version']}, Daten {st['data']['path']}, Python {exe or '?'}", flush=True)
    comps = {c["id"]: c for c in call(port, "/components")["items"]}
    assert comps["graxpert"]["supported"] and comps["astap"]["supported"], comps
    try:
        flow(port, times, t0, a.astap)
    except BaseException:
        show_log(home)
        raise

    # 3) Beenden, Update, Deinstallieren
    t4 = time.time()
    run([str(wrapper), "stop"], env, timeout=120)
    assert wait(lambda: port_closed(port), 30, 0.5), "Server läuft nach dem Beenden noch"
    times["beenden_s"] = round(time.time() - t4, 1)
    marker = home / "AstroFlow" / "pruef-marke.txt"
    marker.write_text("bleibt", encoding="utf-8")
    t5 = time.time()
    run([str(src / "install.sh"), "--no-start"], env, cwd=str(src))
    times["update_s"] = round(time.time() - t5, 1)
    assert marker.is_file() and (home / ".config" / "astroflow").is_dir() and autostart.is_file()
    t6 = time.time()
    print(run([str(home / ".local" / "bin" / "astroflow-deinstallieren")], env).stdout, flush=True)
    times["deinstallieren_s"] = round(time.time() - t6, 1)
    for f in (app, wrapper, desktop, autostart):
        assert not f.exists(), f"nach der Deinstallation noch da: {f}"
    assert marker.is_file(), "Datenordner wurde beim Deinstallieren angefasst"

    # 4) Portabel
    port2 = free_port()
    env2 = {**env, "ASTROFLOW_PORT": str(port2)}
    t7 = time.time()
    run([str(src / "AstroFlow")], env2, timeout=120)
    wait(lambda: call(port2, "/status"), 120, 0.5)
    times["portabel_start_s"] = round(time.time() - t7, 1)
    st = call(port2, "/setup")
    assert Path(st["data"]["path"]).resolve() == (src / "Daten").resolve(), st["data"]["path"]
    run([str(src / "AstroFlow-beenden")], env2, timeout=120)
    assert wait(lambda: port_closed(port2), 30, 0.5), "portabler Server läuft nach dem Beenden noch"
    times["gesamt_min"] = round((time.time() - t0) / 60, 2)
    print(json.dumps(times, indent=2), flush=True)
    if not a.keep:
        shutil.rmtree(root, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
