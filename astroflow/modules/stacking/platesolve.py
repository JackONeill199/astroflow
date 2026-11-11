"""Plate-Solving: Himmelskoordinaten aus dem Sternmuster bestimmen, wenn der Header keine hat.

Backends (in dieser Reihenfolge bei "auto"):
  astap       ASTAP-Kommandozeile + Sterndatenbank (D05/D20/D50/…) – lokal, schnell, empfohlen
  astrometry  lokales astrometry.net (`solve-field` + Index-Dateien)
  nova        nova.astrometry.net (Online-Dienst, API-Key) – nur wenn ausdrücklich gewählt,
              denn dabei wird ein verkleinertes Graustufenbild hochgeladen.

Gelöst wird ein aufbereitetes Luminanzbild (CFA 2×2 gebinnt) in einem Arbeitsordner – die
Originaldateien werden nie verändert, auch nicht auf schreibgeschützten Laufwerken.
"""
from __future__ import annotations

import glob
import math
import os
import shutil
import subprocess
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np
from astropy.io import fits
from astropy.wcs import WCS

from .fits_io import read_data
from .quality import analysis_image

BACKENDS = ("auto", "astap", "astrometry", "nova", "off")
NOVA_URL = "https://nova.astrometry.net/api"

# Bekannte Bildmaßstäbe (Bogensekunden/Pixel des Rohbilds) als Startwert für den Solver
KNOWN_SCALES = {"seestar s50": 2.39, "seestar s30": 3.68, "vespera": 3.0, "stellina": 1.24}

_ASTAP_CANDIDATES = (
    r"C:\Program Files\astap\astap_cli.exe", r"C:\Program Files\astap\astap.exe",
    "/opt/astap/astap_cli", "/opt/astap/astap", "/usr/bin/astap_cli", "/usr/local/bin/astap_cli",
    "/Applications/ASTAP.app/Contents/MacOS/astap",
)


AVAILABLE_TTL = 30            # s – erkannte Solver merken (neu installiertes ASTAP greift spätestens danach)
_AVAILABLE: dict = {}


class SolveError(Exception):
    pass


@dataclass
class Solution:
    ra: float            # Bildmitte, Grad
    dec: float
    scale: float         # Bogensekunden/Pixel bezogen auf das Rohbild
    rotation: float      # Grad, Norden über Osten
    fov_w: float         # Grad
    fov_h: float
    solver: str
    seconds: float = 0.0

    def to_dict(self) -> dict:
        return {k: (round(v, 5) if isinstance(v, float) else v) for k, v in asdict(self).items()}


@dataclass
class SolverConfig:
    backend: str = "auto"
    astap: str = ""          # Pfad zur ASTAP-CLI (leer = automatisch suchen)
    astap_db: str = ""       # Ordner der Sterndatenbank (leer = ASTAP-Standard)
    nova_key: str = ""
    timeout: int = 180
    cmd_prefix: list = field(default_factory=list)   # nur für Tests (z. B. ["python", "fake.py"])

    @classmethod
    def from_env(cls) -> "SolverConfig":
        e = os.environ.get
        return cls(backend=e("ASTROFLOW_SOLVER", "auto").lower(), astap=e("ASTROFLOW_ASTAP", ""),
                   astap_db=e("ASTROFLOW_ASTAP_DB", ""), nova_key=e("ASTROFLOW_NOVA_KEY", ""),
                   timeout=int(e("ASTROFLOW_SOLVE_TIMEOUT", "180")))

    # ---- Erkennung ------------------------------------------------------- #
    def astap_path(self) -> str | None:
        if self.cmd_prefix:
            return "astap(test)"
        local = os.path.join(os.environ.get("LOCALAPPDATA", ""), "astap", "astap_cli.exe") if os.environ.get("LOCALAPPDATA") else None
        home = str(Path.home() / "astap" / "astap_cli")            # Einrichtungsassistent unter macOS/Linux
        for c in (self.astap, shutil.which("astap_cli"), shutil.which("astap"), local, home, *_ASTAP_CANDIDATES):
            if c and Path(c).is_file():
                return str(c)
        return None

    def _db_dirs(self) -> list[str]:
        exe = self.astap_path()
        dirs = [self.astap_db] if self.astap_db else []
        if exe:
            dirs.append(str(Path(exe).parent))
        dirs += ["/opt/astap", "/usr/share/astap", str(Path.home() / "astap"), str(Path.home() / ".astap")]
        if os.environ.get("LOCALAPPDATA"):                     # Windows ohne Admin-Rechte
            dirs.append(os.path.join(os.environ["LOCALAPPDATA"], "astap"))
        return dirs

    def db_dir(self) -> str | None:
        """Erster Ordner mit einer Sterndatenbank."""
        return next((d for d in self._db_dirs() if glob.glob(os.path.join(d, "*_0101.*"))), None)

    def astap_databases(self) -> list[str]:
        """Gefundene Sterndatenbanken (z. B. ['D05'])."""
        if self.cmd_prefix:
            return ["TEST"]
        d = self.db_dir()
        return sorted({Path(f).name.split("_")[0].upper() for f in glob.glob(os.path.join(d, "*_0101.*"))}) if d else []

    def solve_field_path(self) -> str | None:
        return shutil.which("solve-field")

    def available(self) -> list[str]:
        # Suche über PATH/Dateisystem kostet unter Windows ~190 ms und /status (alle 2,5 s gepollt) fragt sie ab –
        # deshalb kurz merken; geänderte Einstellungen oder PATH ergeben einen neuen Schlüssel
        key = (self.backend, self.astap, self.astap_db, bool(self.nova_key), tuple(self.cmd_prefix),
               os.environ.get("PATH"), os.environ.get("LOCALAPPDATA"))
        hit = _AVAILABLE.get(key)
        if hit and time.monotonic() - hit[0] < AVAILABLE_TTL:
            return list(hit[1])
        out = self._available()
        _AVAILABLE.clear()
        _AVAILABLE[key] = (time.monotonic(), out)
        return list(out)

    def _available(self) -> list[str]:
        out = []
        if self.astap_path() and self.astap_databases():
            out.append("astap")
        if self.solve_field_path():
            out.append("astrometry")
        if self.nova_key:
            out.append("nova")
        return out

    def chain(self) -> list[str]:
        if self.backend == "off":
            return []
        if self.backend == "auto":
            return [b for b in self.available() if b != "nova"]   # Online-Dienst nie automatisch
        return [self.backend]

    @property
    def enabled(self) -> bool:
        return bool(self.chain())

    def status(self) -> dict:
        return {"backend": self.backend, "active": self.chain(), "astap": self.astap_path(),
                "astap_databases": self.astap_databases(), "solve_field": self.solve_field_path(),
                "has_nova_key": bool(self.nova_key), "astap_db": self.astap_db, "astap_db_found": self.db_dir(),
                "timeout": self.timeout}


# ------------------------------------------------------------------ Hilfen -- #
def _hints(path: str, shape_hw: tuple[int, int], instrument: str | None) -> dict:
    """Startwerte aus dem Header: Bildfeld (FOCALLEN+XPIXSZ oder bekanntes Smart-Teleskop)."""
    h = {}
    try:
        hdr = fits.getheader(path)
    except Exception:
        hdr = {}
    scale = None
    try:
        f = float(hdr.get("FOCALLEN") or hdr.get("FOCAL") or 0)                     # FOCAL/PIXSZ: Stellina
        px = float(hdr.get("XPIXSZ") or hdr.get("PIXSIZE1") or hdr.get("PIXSZ") or 0)
        bin_ = float(hdr.get("XBINNING") or 1)
        if f > 10 and px > 0:
            scale = 206.265 * px * bin_ / f
    except (TypeError, ValueError):
        pass
    if scale is None:
        inst = (instrument or "").lower()
        scale = next((v for k, v in KNOWN_SCALES.items() if k in inst), None)
    if scale:
        h["scale"] = scale
        h["fov_h"] = shape_hw[0] * scale / 3600
    return h


def prepare_image(path: str, bayer: bool, out: Path, max_side: int = 2400) -> tuple[Path, float, tuple[int, int]]:
    """Luminanzbild (gebinnt) als 16-bit-FITS. Gibt (Datei, Binning-Faktor, Rohbildgröße (H,W))."""
    raw = read_data(path)
    hw = raw.shape[-2:]
    img, s = analysis_image(raw, bayer and raw.ndim == 2)
    while max(img.shape) > max_side:                          # weiter binnen: schneller, robuster
        h, w = img.shape[0] // 2 * 2, img.shape[1] // 2 * 2
        img = img[:h, :w].reshape(h // 2, 2, w // 2, 2).mean((1, 3))
        s *= 2
    lo, hi = np.percentile(img[::4, ::4], [0.5, 99.99])
    u16 = (np.clip((img - lo) / max(hi - lo, 1e-9), 0, 1) * 65000).astype(np.uint16)
    out.parent.mkdir(parents=True, exist_ok=True)
    fits.PrimaryHDU(u16).writeto(out, overwrite=True)
    return out, float(s), tuple(hw)


def _solution_from_header(h: fits.Header, img_hw: tuple[int, int], s: float, raw_hw: tuple[int, int], solver: str) -> Solution:
    h = h.copy()
    h.setdefault("CTYPE1", "RA---TAN")
    h.setdefault("CTYPE2", "DEC--TAN")
    h["NAXIS"], h["NAXIS1"], h["NAXIS2"] = 2, img_hw[1], img_hw[0]
    w = WCS(h, naxis=2)
    ra, dec = (float(v) for v in w.pixel_to_world_values((img_hw[1] - 1) / 2, (img_hw[0] - 1) / 2))
    cd = w.pixel_scale_matrix
    scale_img = math.sqrt(abs(np.linalg.det(cd))) * 3600
    rot = math.degrees(math.atan2(cd[0, 1], cd[1, 1])) % 360
    scale = scale_img / s
    return Solution(ra % 360, dec, scale, rot, raw_hw[1] * scale / 3600, raw_hw[0] * scale / 3600, solver)


def _read_ini(p: Path) -> dict:
    d = {}
    for line in p.read_text(errors="replace").splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            d[k.strip().upper()] = v.strip()
    return d


ASTAP_EXIT = {1: "keine Lösung gefunden", 2: "zu wenige Sterne erkannt", 16: "Bild nicht lesbar",
              32: "keine Sterndatenbank gefunden", 33: "Sterndatenbank nicht lesbar"}


# ---------------------------------------------------------------- Backends -- #
def _astap(cfg: SolverConfig, img: Path, hw, hints: dict) -> fits.Header:
    exe = cfg.astap_path()
    if not exe:
        raise SolveError("ASTAP nicht gefunden")
    # -s: höchstens so viele Sterne – sternreiche Webbilder (Schmalband, Sterne verkleinert) brauchen mehr als 500
    cmd = (cfg.cmd_prefix or [exe]) + ["-f", str(img), "-z", "0", "-s", str(int(hints.get("stars") or 500))]
    if "ra" in hints:
        cmd += ["-ra", f"{hints['ra'] / 15:.5f}", "-spd", f"{hints['dec'] + 90:.5f}", "-r", "15"]
    else:
        cmd += ["-r", "180"]                                   # Blind-Solve über den ganzen Himmel
    cmd += ["-fov", f"{hints['fov_h_img']:.4f}" if "fov_h_img" in hints else "0"]
    db = cfg.astap_db or (None if cfg.cmd_prefix else cfg.db_dir())
    if db and Path(db) != Path(exe).parent:
        cmd += ["-d", db]
    ini = img.with_suffix(".ini")
    ini.unlink(missing_ok=True)
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=cfg.timeout)
    except subprocess.TimeoutExpired:
        raise SolveError(f"ASTAP: Zeitüberschreitung nach {cfg.timeout} s")
    if not ini.exists():
        tail = (r.stderr or r.stdout or "").strip().splitlines()[-1:]
        raise SolveError(f"ASTAP: {ASTAP_EXIT.get(r.returncode, f'Fehler (Code {r.returncode})')}" + (f" – {tail[0][:200]}" if tail else ""))
    d = _read_ini(ini)
    if d.get("PLTSOLVD", "F").upper() != "T":
        err = d.get("ERROR") or ASTAP_EXIT.get(r.returncode, "keine Lösung gefunden")
        if "database" in err.lower() and any(b.startswith("D") for b in cfg.astap_databases()):
            err += " – die ASTAP-Version ist zu alt für die D-Datenbanken (D05/D20/D50); bitte ASTAP aktualisieren"
        raise SolveError("ASTAP: " + err)
    h = fits.Header()
    for k in ("CRPIX1", "CRPIX2", "CRVAL1", "CRVAL2", "CD1_1", "CD1_2", "CD2_1", "CD2_2", "CDELT1", "CDELT2", "CROTA2"):
        if k in d:
            h[k] = float(d[k])
    return h


def _astrometry(cfg: SolverConfig, img: Path, hw, hints: dict) -> fits.Header:
    exe = cfg.solve_field_path()
    if not exe:
        raise SolveError("solve-field nicht gefunden")
    out = img.parent / (img.stem + "_anet")
    shutil.rmtree(out, ignore_errors=True)
    cmd = [exe, "--no-plots", "--overwrite", "--dir", str(out), "--new-fits", "none", "--corr", "none",
           "--match", "none", "--rdls", "none", "--index-xyls", "none", "--cpulimit", str(cfg.timeout), str(img)]
    if "scale_img" in hints:
        cmd[1:1] = ["--scale-units", "arcsecperpix", "--scale-low", f"{hints['scale_img'] * 0.8:.3f}",
                    "--scale-high", f"{hints['scale_img'] * 1.25:.3f}"]
    try:
        subprocess.run(cmd, capture_output=True, text=True, timeout=cfg.timeout + 30)
    except subprocess.TimeoutExpired:
        raise SolveError("astrometry.net: Zeitüberschreitung")
    wcs = out / (img.stem + ".wcs")
    if not wcs.exists():
        raise SolveError("astrometry.net: keine Lösung gefunden")
    return fits.getheader(wcs)


def _nova(cfg: SolverConfig, img: Path, hw, hints: dict) -> fits.Header:
    import json

    import httpx
    if not cfg.nova_key:
        raise SolveError("nova.astrometry.net: kein API-Key")
    with httpx.Client(timeout=60) as c:
        r = c.post(f"{NOVA_URL}/login", data={"request-json": json.dumps({"apikey": cfg.nova_key})}).json()
        if r.get("status") != "success":
            raise SolveError("nova.astrometry.net: Anmeldung fehlgeschlagen")
        req = {"session": r["session"], "publicly_visible": "n", "allow_modifications": "n", "allow_commercial_use": "n"}
        if "scale_img" in hints:
            req.update(scale_units="arcsecperpix", scale_type="ev", scale_est=hints["scale_img"], scale_err=25)
        with open(img, "rb") as f:
            sub = c.post(f"{NOVA_URL}/upload", data={"request-json": json.dumps(req)},
                         files={"file": (img.name, f, "application/octet-stream")}).json()
        if sub.get("status") != "success":
            raise SolveError("nova.astrometry.net: Upload fehlgeschlagen")
        t0, job = time.time(), None
        while time.time() - t0 < cfg.timeout:
            time.sleep(5)
            if job is None:
                jobs = [j for j in c.get(f"{NOVA_URL}/submissions/{sub['subid']}").json().get("jobs", []) if j]
                job = jobs[0] if jobs else None
                continue
            st = c.get(f"{NOVA_URL}/jobs/{job}").json().get("status")
            if st == "success":
                wcs = c.get(f"https://nova.astrometry.net/wcs_file/{job}")
                return fits.Header.fromstring(wcs.content.decode("latin-1"))
            if st == "failure":
                raise SolveError("nova.astrometry.net: keine Lösung gefunden")
        raise SolveError("nova.astrometry.net: Zeitüberschreitung")


_BACKENDS = {"astap": _astap, "astrometry": _astrometry, "nova": _nova}


def solve_frame(cfg: SolverConfig, path: str, bayer: bool, instrument: str | None, work: Path,
                ra_hint: float | None = None, dec_hint: float | None = None) -> Solution:
    """Löst eine Aufnahme. Wirft SolveError mit verständlicher Meldung, wenn kein Backend Erfolg hat."""
    chain = cfg.chain()
    if not chain:
        raise SolveError("kein Plate-Solver eingerichtet (ASTAP + Sterndatenbank empfohlen)")
    t0 = time.time()
    img, s, raw_hw = prepare_image(path, bayer, work / (Path(path).stem[:40] + "_solve.fits"))
    img_hw = fits.getdata(img).shape
    h = _hints(path, raw_hw, instrument)
    hints = {}
    if "scale" in h:
        hints["scale_img"] = h["scale"] * s
        hints["fov_h_img"] = h["fov_h"]
    if ra_hint is not None and dec_hint is not None:
        hints.update(ra=ra_hint, dec=dec_hint)
    errors = []
    try:
        for b in chain:
            for attempt in ([hints, {k: v for k, v in hints.items() if k not in ("fov_h_img", "scale_img")}]
                            if "fov_h_img" in hints else [hints]):
                try:
                    hdr = _BACKENDS[b](cfg, img, img_hw, attempt)
                    sol = _solution_from_header(hdr, img_hw, s, raw_hw, b)
                    sol.seconds = time.time() - t0
                    return sol
                except SolveError as e:
                    errors.append(str(e))
                except Exception as e:                       # defekte Ausgabe o. Ä.
                    errors.append(f"{b}: {e}")
    finally:
        for p in img.parent.glob(img.stem + "*"):
            if p.is_dir():
                shutil.rmtree(p, ignore_errors=True)
            else:
                p.unlink(missing_ok=True)
    raise SolveError("; ".join(dict.fromkeys(errors)) or "keine Lösung")


def solve_image(cfg: SolverConfig, L: np.ndarray, work: Path, ra_hint: float | None = None, dec_hint: float | None = None,
                fov_h: float | None = None, stars: int | None = None) -> dict:
    """Löst ein (lineares) Luminanzbild, z. B. den Master-Stack, und gibt die WCS-Kopfzeilen bezogen auf **dieses**
    Bild zurück (0-basierte Zeile r, Spalte c ↔ FITS-Pixel x = c + 1, y = r + 1)."""
    chain = cfg.chain()
    if not chain:
        raise SolveError("kein Plate-Solver eingerichtet")
    img, s = L.astype(np.float32), 1
    while max(img.shape) > 2400:
        h, w = img.shape[0] // 2 * 2, img.shape[1] // 2 * 2
        img = img[:h, :w].reshape(h // 2, 2, w // 2, 2).mean((1, 3))
        s *= 2
    lo, hi = np.percentile(img[::4, ::4], [0.5, 99.99])
    work.mkdir(parents=True, exist_ok=True)
    path = work / "stack_solve.fits"
    fits.PrimaryHDU((np.clip((img - lo) / max(hi - lo, 1e-9), 0, 1) * 65000).astype(np.uint16)).writeto(path, overwrite=True)
    hints = {}
    if ra_hint is not None and dec_hint is not None:
        hints.update(ra=ra_hint, dec=dec_hint)
    if fov_h:
        hints["fov_h_img"] = fov_h
    if stars:
        hints["stars"] = stars
    errors = []
    try:
        for b in chain:
            if b == "nova":                                  # nie automatisch Bilder hochladen
                continue
            try:
                h = _BACKENDS[b](cfg, path, img.shape, hints)
                break
            except SolveError as e:
                errors.append(str(e))
        else:
            raise SolveError("; ".join(errors) or "keine Lösung")
    finally:
        for p in work.glob("stack_solve*"):
            p.unlink(missing_ok=True) if p.is_file() else shutil.rmtree(p, ignore_errors=True)
    # vom gebinnten auf das volle Pixelraster umrechnen
    out = {"CTYPE1": "RA---TAN", "CTYPE2": "DEC--TAN", "CRVAL1": float(h["CRVAL1"]), "CRVAL2": float(h["CRVAL2"])}
    for k in ("CRPIX1", "CRPIX2"):
        out[k] = (float(h[k]) - 0.5) * s + 0.5
    if "CD1_1" in h:
        for k in ("CD1_1", "CD1_2", "CD2_1", "CD2_2"):
            out[k] = float(h.get(k, 0.0)) / s
    else:                                                    # CDELT/CROTA -> CD
        c1, c2, rot = float(h["CDELT1"]) / s, float(h["CDELT2"]) / s, math.radians(float(h.get("CROTA2", 0.0)))
        out.update(CD1_1=c1 * math.cos(rot), CD1_2=-c2 * math.sin(rot), CD2_1=c1 * math.sin(rot), CD2_2=c2 * math.cos(rot))
    return out
