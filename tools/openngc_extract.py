"""Erzeugt astroflow/modules/stacking/katalogdaten/messier_caldwell.csv aus OpenNGC (nur beim Entwickeln, zur Laufzeit kein Netz).

Quelle: https://github.com/mattiaverga/OpenNGC (CC-BY-SA 4.0), feste Fassung siehe OPENNGC_VERSION.
Aufruf (Dateien vorher selbst in einen leeren Ordner laden, z. B. von
https://raw.githubusercontent.com/mattiaverga/OpenNGC/<Commit>/database_files/NGC.csv und addendum.csv):

    python tools/openngc_extract.py <ordner-mit-NGC.csv-und-addendum.csv> [--alt <alte deepsky.py>]

`--alt` vergleicht mit den bisherigen, von Hand geschriebenen Tabellen (MESSIER/CALDWELL einer älteren deepsky.py,
z. B. per `git show 84892e8:astroflow/modules/stacking/deepsky.py > alt.py`) und listet Abweichungen
(Helligkeit > 0,5 mag, Position > 5′, Größe > 30 %).

Regeln:
- Messier: Spalte `M` (NGC.csv und addendum.csv). Ausnahme M102: OpenNGC führt es als Dublette von M101 (Typ `Dup`);
  wie bisher gilt NGC 5866 (Spindelgalaxie) als M102.
- Caldwell: Kennung `C 0nn` in `Identifiers` (NGC.csv) bzw. Name `C0nn` (addendum.csv).
- Helligkeit: V-Mag; fehlt sie, B-Mag (Spalte `band` = V oder B); fehlt beides, leer.
- Größe: Hauptachse `MajAx` in Bogenminuten (leer, wenn OpenNGC keine kennt).
- Position: J2000 aus OpenNGC, RA in Grad.
"""
from __future__ import annotations

import csv
import re
import sys
from pathlib import Path

OPENNGC_VERSION = "v20260501"
OPENNGC_COMMIT = "36cb178a0f69dba8bfc03a99c10512831edf1c6b"
OUT = Path(__file__).resolve().parent.parent / "astroflow" / "modules" / "stacking" / "katalogdaten" / "messier_caldwell.csv"

KOPF = f"""# Messier- und Caldwell-Katalog (Position J2000, Helligkeit, Größe) – ausgezogen aus OpenNGC {OPENNGC_VERSION}
# Quelle: https://github.com/mattiaverga/OpenNGC (Commit {OPENNGC_COMMIT}), Dateien NGC.csv und addendum.csv
# Lizenz der Daten: Creative Commons Attribution-ShareAlike 4.0 (CC-BY-SA 4.0), siehe LICENSE-OpenNGC in diesem Ordner.
# Namensnennung: OpenNGC, Mattia Verga und Mitwirkende. Änderungen: nur Auswahl der Zeilen/Spalten und Umrechnung (RA in Grad).
# Weitergabe (auch veränderter Fassungen) nur unter denselben Bedingungen (CC-BY-SA 4.0). Der AstroFlow-Code steht unter MIT.
# Helligkeit = V-Mag, ersatzweise B-Mag (Spalte band); leer = OpenNGC kennt keine. Größe = Hauptachse in Bogenminuten.
# Erzeugt mit tools/openngc_extract.py – nicht von Hand ändern.
"""
SPALTEN = ["cat", "num", "openngc", "ra_deg", "dec_deg", "typ", "mag", "band", "size_arcmin"]


def _hms(s: str, stunden: bool) -> float:
    m = re.match(r"^([+-]?)(\d+):(\d+):([\d.]+)$", s.strip())
    if not m:
        raise ValueError(s)
    wert = int(m.group(2)) + int(m.group(3)) / 60 + float(m.group(4)) / 3600
    wert = -wert if m.group(1) == "-" else wert
    return wert * 15 if stunden else wert


def lesen(ordner: Path) -> list[dict]:
    rows = []
    for f in ("NGC.csv", "addendum.csv"):
        with open(ordner / f, encoding="utf-8", newline="") as fh:
            rows.extend(csv.DictReader(fh, delimiter=";"))
    return rows


def _zeile(cat: str, num: int, r: dict) -> dict:
    v, b = r["V-Mag"].strip(), r["B-Mag"].strip()
    return {"cat": cat, "num": num, "openngc": r["Name"], "ra_deg": f"{_hms(r['RA'], True):.4f}",
            "dec_deg": f"{_hms(r['Dec'], False):.4f}", "typ": r["Type"], "mag": v or b,
            "band": "V" if v else ("B" if b else ""), "size_arcmin": r["MajAx"].strip().rstrip("0").rstrip(".") if r["MajAx"].strip() else ""}


def auszug(rows: list[dict]) -> list[dict]:
    out, seen = [], set()
    for r in rows:
        if r["M"].strip() and r["Type"] != "Dup":
            n = int(r["M"])
            out.append(_zeile("M", n, r))
            seen.add(n)
    if 102 not in seen:                                               # OpenNGC: M102 = Dublette von M101 → NGC 5866
        r = next(x for x in rows if x["Name"] == "NGC5866")
        out.append(_zeile("M", 102, r))
    cs = {}
    for r in rows:
        for s in (r["Identifiers"], r["Name"]):
            for m in re.finditer(r"\bC ?(\d{3})\b", s):
                cs.setdefault(int(m.group(1)), r)
    out.extend(_zeile("C", n, cs[n]) for n in sorted(cs))
    out.sort(key=lambda z: (z["cat"] != "M", z["num"]))
    return out


def schreiben(zeilen: list[dict], pfad: Path = OUT) -> None:
    pfad.parent.mkdir(parents=True, exist_ok=True)
    with open(pfad, "w", encoding="utf-8", newline="") as fh:
        fh.write(KOPF)
        w = csv.DictWriter(fh, SPALTEN, lineterminator="\n")
        w.writeheader()
        w.writerows(zeilen)


def vergleich(zeilen: list[dict], alt_py: Path) -> None:
    ns: dict = {}
    src = alt_py.read_text(encoding="utf-8")
    src = src.split("# Zweitnamen")[0].replace("from . import objects", "objects = None")
    exec(compile(src, str(alt_py), "exec"), ns)
    sep = lambda a, b: __import__("math").hypot((a[0] - b[0]) * __import__("math").cos(__import__("math").radians((a[1] + b[1]) / 2)), a[1] - b[1]) * 60
    neu = {(z["cat"], int(z["num"])): z for z in zeilen}
    print("Abweichungen alt → neu (Helligkeit > 0,5 mag, Position > 5′, Größe > 30 %):")
    for cat, tab in (("M", ns["MESSIER"]), ("C", ns["CALDWELL"])):
        for n, ngc, name, ra_h, dec, k, mag, size in tab:
            z = neu.get((cat, n))
            lab = f"{cat}{n} {ngc or name}"
            if z is None:
                print(f"  {lab}: fehlt in OpenNGC"); continue
            teile = []
            nm = float(z["mag"]) if z["mag"] else None
            if mag is None and nm is not None:
                teile.append(f"Helligkeit alt –, neu {nm} ({z['band']})")
            elif mag is not None and nm is None:
                teile.append(f"Helligkeit alt {mag}, neu –")
            elif mag is not None and abs(mag - nm) > 0.5:
                teile.append(f"Helligkeit {mag} → {nm} ({z['band']}, Δ{nm - mag:+.1f})")
            d = sep((ra_h * 15, dec), (float(z["ra_deg"]), float(z["dec_deg"])))
            if d > 5:
                teile.append(f"Position Δ{d:.0f}′")
            ns_ = float(z["size_arcmin"]) if z["size_arcmin"] else None
            if ns_ is None:
                teile.append(f"Größe alt {size}, neu –")
            elif abs(ns_ - size) / max(size, 0.1) > 0.3:
                teile.append(f"Größe {size}′ → {ns_}′")
            if teile:
                print(f"  {lab}: " + "; ".join(teile))


def main(argv: list[str]) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    if len(argv) < 2:
        print(__doc__)
        return 2
    zeilen = auszug(lesen(Path(argv[1])))
    schreiben(zeilen)
    m = sum(z["cat"] == "M" for z in zeilen)
    print(f"{len(zeilen)} Zeilen ({m} Messier, {len(zeilen) - m} Caldwell) → {OUT}")
    if "--alt" in argv:
        vergleich(zeilen, Path(argv[argv.index("--alt") + 1]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
