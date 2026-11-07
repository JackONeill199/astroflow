"""CLI: astroflow [start] | stop | serve | process | demo"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main(argv=None):
    ap = argparse.ArgumentParser(prog="astroflow", description="Vollautomatische Astrofotografie")
    sub = ap.add_subparsers(dest="cmd")
    st = sub.add_parser("start", help="Server im Hintergrund starten und Browser öffnen (Standard ohne Befehl)")
    st.add_argument("--data", default=None)
    st.add_argument("--port", type=int, default=None)
    st.add_argument("--no-browser", action="store_true")
    st.add_argument("--home", default=None, help="portabler Programmordner (wie ASTROFLOW_HOME; Autostart der portablen Version)")
    st.add_argument("--wait-pid", type=int, default=None, help=argparse.SUPPRESS)
    co = sub.add_parser("components", help="optionale Komponenten: status | install NAME[:OPTION] … | remove NAME …")
    co.add_argument("args", nargs="*")
    tr = sub.add_parser("tray", help="Symbol im Infobereich (startet „astroflow start“ von selbst)")
    tr.add_argument("--port", type=int, default=None)
    so = sub.add_parser("stop", help="laufenden Server beenden")
    so.add_argument("--port", type=int, default=None)
    sv = sub.add_parser("serve", help="Web-Oberfläche starten")
    sv.add_argument("--host", default=None, help="Adresse (Standard 127.0.0.1 = nur dieser Rechner; mit --lan bzw. "
                    "Einstellungen → Heimnetz 0.0.0.0)")
    sv.add_argument("--lan", action="store_true", help="auch im Heimnetz erreichbar (Geräte koppeln per QR-Code)")
    sv.add_argument("--port", type=int, default=8080)
    sv.add_argument("--data", default=None)
    pr = sub.add_parser("process", help="Eingabeordner scannen und alles verarbeiten (ohne Server)")
    pr.add_argument("--data", default=None)
    dm = sub.add_parser("demo", help="synthetische Demo-Aufnahmen erzeugen")
    dm.add_argument("out", nargs="?", default="./data/input/demo")
    dm.add_argument("--lights", type=int, default=30)
    dm.add_argument("--comet", action="store_true", help="Kometenserie (Komet vor Sternfeld) statt Galaxie")
    a = ap.parse_args(argv)

    if a.cmd in (None, "start"):
        import os
        if getattr(a, "home", None):
            os.environ["ASTROFLOW_HOME"] = a.home        # gilt auch für Server und Symbol (Unterprozesse erben sie)
        from .core import launcher
        return launcher.start(getattr(a, "data", None), getattr(a, "port", None), not getattr(a, "no_browser", False),
                              getattr(a, "wait_pid", None))
    if a.cmd == "tray":
        from .core import tray
        return tray.main(a.port)
    if a.cmd == "components":
        from .core import components
        return components.cli(a.args)
    if a.cmd == "stop":
        from .core import launcher
        return launcher.stop(a.port)
    if a.cmd == "serve":
        import uvicorn
        from .core import security
        from .core.config import load_settings
        from .server import create_app
        import os
        os.environ["ASTROFLOW_PORT"] = str(a.port)                    # Einrichtung/Neustart kennen den eigenen Port
        s = load_settings(a.data)
        host = a.host or ("0.0.0.0" if a.lan or security.lan_enabled(s) else "127.0.0.1")
        os.environ["ASTROFLOW_BIND"] = host                            # Einstellungen → Heimnetz zeigt, ob es gerade gilt
        uvicorn.run(create_app(s), host=host, port=a.port)
    elif a.cmd == "process":
        from .core.config import load_settings
        from .modules.stacking.db import DB
        from .modules.stacking.pipeline import Pipeline
        s = load_settings(a.data)
        db = DB(s.db_path)
        pl = Pipeline(s, db)
        r = pl.scan()
        print(f"{r['new_frames']} neue Dateien, Projekte: {r['new_by_project']}", *r["errors"], sep="\n")
        for p in db.q("SELECT * FROM projects"):
            print(f"== {p['name']}")
            try:
                rid = pl.process(p["id"], progress=lambda f, st, m="": print(f"  {f*100:5.1f}% {st} {m}", end="\r"))
                print("\n  ->", db.one("SELECT files FROM results WHERE id=?", (rid,))["files"])
            except Exception as e:
                print("\n  Fehler:", e)
    elif a.cmd == "demo":
        from .modules.stacking.demo import generate, generate_comet
        (generate_comet if a.comet else generate)(Path(a.out), n_lights=a.lights)
        print("Demo-Daten in", a.out)


if __name__ == "__main__":
    sys.exit(main())
