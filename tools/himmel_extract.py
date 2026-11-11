"""Erzeugt die Himmelsdaten des Observatoriums (nur beim Entwickeln, zur Laufzeit kein Netz):
`astroflow/modules/stacking/static/himmel/sterne.json` und `sternbilder.json`.

Quellen (feste Fassungen, unverändert geladen):
- Yale Bright Star Catalogue, 5. Ausgabe (Hoffleit & Warren 1991), ASCII-Fassung `bsc5.dat.gz`
  (http://tdc-www.harvard.edu/catalogs/bsc5.dat.gz, gleiches Format wie CDS VizieR V/50 `catalog.gz`) – gemeinfrei (NASA/ADC).
- d3-celestial von Olaf Frohn (https://github.com/ofrohn/d3-celestial, `data/constellations.lines.json` und
  `data/constellations.json`) – BSD-3-Clause, Lizenztext in `static/himmel/LICENSE-d3-celestial.txt`.

Aufruf: python tools/himmel_extract.py <ordner mit bsc5.dat.gz, constellations.lines.json, constellations.json>

Ausgabe (kompakt, für den Canvas-Atlas):
- sterne.json: {"src": …, "stars": [ra°, dec°, Vmag, B−V, …]} flach je 4 Zahlen, bis MAG_LIMIT, hellste zuerst.
- sternbilder.json: {"src": …, "items": [{"id", "name" (deutsch), "ra", "dec" (Beschriftung), "lines": [[ra, dec, ra, dec …], …]}]}
  Rektaszension 0…360° (d3-celestial speichert −180…180°).
"""
from __future__ import annotations

import gzip
import json
import sys
from pathlib import Path

MAG_LIMIT = 6.5
OUT = Path(__file__).resolve().parent.parent / "astroflow" / "modules" / "stacking" / "static" / "himmel"


def _num(s: str) -> float | None:
    s = s.strip()
    try:
        return float(s) if s else None
    except ValueError:
        return None


def stars(path: Path) -> list[float]:
    """Feste Spalten laut ReadMe V/50 (1-basiert): RAh 76–77, RAm 78–79, RAs 80–83, DE- 84, DEd 85–86, DEm 87–88, DEs 89–90,
    Vmag 103–107, B−V 110–114. Einträge ohne Position (Novae, gestrichene Sterne) entfallen."""
    rows = []
    with gzip.open(path, "rt", encoding="latin-1") as f:
        for line in f:
            line = line.rstrip("\n").ljust(114)
            rah, ram, ras = _num(line[75:77]), _num(line[77:79]), _num(line[79:83])
            ded, dem, des = _num(line[84:86]), _num(line[86:88]), _num(line[88:90])
            mag = _num(line[102:107])
            if None in (rah, ram, ras, ded, dem, des, mag) or mag > MAG_LIMIT:
                continue
            ra = (rah + ram / 60 + ras / 3600) * 15
            dec = (ded + dem / 60 + des / 3600) * (-1 if line[83] == "-" else 1)
            bv = _num(line[109:114])
            rows.append((mag, round(ra, 3), round(dec, 3), round(mag, 2), round(bv if bv is not None else 0.6, 2)))
    rows.sort()
    return [v for r in rows for v in r[1:]]


def _ra(lon: float) -> float:
    return round(lon % 360, 3)


def constellations(lines_path: Path, names_path: Path) -> list[dict]:
    lines = {f["id"]: f["geometry"]["coordinates"] for f in json.loads(lines_path.read_text(encoding="utf-8"))["features"]}
    out = []
    for f in json.loads(names_path.read_text(encoding="utf-8"))["features"]:
        cid, p = f["id"], f["properties"]
        lon, lat = f["geometry"]["coordinates"]
        segs = [[v for pt in seg for v in (_ra(pt[0]), round(pt[1], 3))] for seg in lines.get(cid, [])]
        out.append({"id": cid, "name": " ".join(str(p.get("de") or p.get("name")).split()), "ra": _ra(lon), "dec": round(lat, 3),
                    "rank": int(p.get("rank") or 3), "lines": segs})
    return sorted(out, key=lambda c: c["id"])


def main(src: Path) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    st = stars(src / "bsc5.dat.gz")
    (OUT / "sterne.json").write_text(json.dumps({"src": "Yale Bright Star Catalogue 5 (gemeinfrei)", "mag_limit": MAG_LIMIT,
                                                 "stars": st}, separators=(",", ":")), encoding="utf-8")
    cs = constellations(src / "constellations.lines.json", src / "constellations.json")
    (OUT / "sternbilder.json").write_text(json.dumps({"src": "d3-celestial (Olaf Frohn), BSD-3-Clause", "items": cs},
                                                     ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"{len(st) // 4} Sterne bis {MAG_LIMIT} mag, {len(cs)} Sternbilder -> {OUT}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(Path(sys.argv[1]))
