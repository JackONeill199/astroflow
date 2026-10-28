"""CLI: python -m astroflow serve | process | demo"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main(argv=None):
    ap = argparse.ArgumentParser(prog="astroflow", description="Vollautomatische Astrofotografie")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sv = sub.add_parser("serve", help="Web-Oberfläche starten")
    sv.add_argument("--host", default="0.0.0.0")
    sv.add_argument("--port", type=int, default=8080)
    sv.add_argument("--data", default=None)
    pr = sub.add_parser("process", help="Eingabeordner scannen und alles verarbeiten (ohne Server)")
    pr.add_argument("--data", default=None)
    dm = sub.add_parser("demo", help="synthetische Demo-Aufnahmen erzeugen")
    dm.add_argument("out", nargs="?", default="./data/input/demo")
    dm.add_argument("--lights", type=int, default=30)
    a = ap.parse_args(argv)

    if a.cmd == "serve":
        import uvicorn
        from .config import load_settings
        from .server import create_app
        uvicorn.run(create_app(load_settings(a.data)), host=a.host, port=a.port)
    elif a.cmd == "process":
        from .config import load_settings
        from .db import DB
        from .pipeline import Pipeline
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
        from .demo import generate
        generate(Path(a.out), n_lights=a.lights)
        print("Demo-Daten in", a.out)


if __name__ == "__main__":
    sys.exit(main())
