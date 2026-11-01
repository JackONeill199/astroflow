"""GHS-Stretch und Sternentrennung – der Weg, den Hobby-Astrofotografen gehen (Siril/PixInsight + StarNet):

  linear:   Bild -> sternlos + Sterne
  strecken: sternloses Bild kräftig (GHS, mehrere Durchgänge, Symmetriepunkt am Himmelshintergrund),
            Sterne = T(Original) - T(sternlos) mit derselben Kurve -> sehen aus wie bei normaler Streckung
  bearbeiten: Kontrast/Sättigung/Entrauschen nur am sternlosen Bild, Sterne getrennt (Helligkeit)
  zusammen: Screen-Mischung

Sterne trennen: StarNet2 (externes Programm, optional, wie in Siril) oder eine eigene Methode ohne Zusatzsoftware
(morphologisches Top-Hat, runde kompakte Quellen, Ausbessern aus der Umgebung). Es werden keine Strukturen erzeugt:
das sternlose Bild ist das Original mit durch Umgebungswerte ersetzten Sternen, Original = sternlos + Sterne.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import numpy as np
from scipy import ndimage as ndi

from .quality import noise_sigma


def luminance(img: np.ndarray) -> np.ndarray:
    return img.mean(0)


# ------------------------------------------------------------------ GHS #
def _f(u: np.ndarray, D: float, b: float) -> np.ndarray:
    """GHS-Grundfunktion (Payne & Cranfield): b > 0 hyperbolisch, b = 0 exponentiell."""
    if b > 1e-6:
        return 1 - (1 + b * D * u) ** (-1 / b)
    return 1 - np.exp(-D * u)


def _df(u, D, b):
    if b > 1e-6:
        return D * (1 + b * D * u) ** (-1 / b - 1)
    return D * np.exp(-D * u)


def ghs(x: np.ndarray, D: float, b: float, SP: float, LP: float = 0.0, HP: float = 1.0) -> np.ndarray:
    """Generalized Hyperbolic Stretch auf 0..1: größter Kontrastgewinn am Symmetriepunkt SP, unterhalb LP und oberhalb HP
    linear fortgesetzt (Schatten-/Lichterschutz). D = Stärke, b = Bündelung um SP."""
    if D <= 0:
        return x

    def T(v):
        v = np.asarray(v, dtype=np.float64)
        out = np.where(v >= SP, _f(np.maximum(v - SP, 0), D, b), -_f(np.maximum(SP - v, 0), D, b))
        if LP < SP:
            tl, dl = -_f(SP - LP, D, b), _df(SP - LP, D, b)
            out = np.where(v < LP, tl + dl * (v - LP), out)
        if HP > SP:
            th, dh = _f(HP - SP, D, b), _df(HP - SP, D, b)
            out = np.where(v > HP, th + dh * (v - HP), out)
        return out

    t0, t1 = float(T(0.0)), float(T(1.0))
    return ((T(x) - t0) / (t1 - t0)).astype(np.float32)


D_MAX = 25.0          # größere Stärken heben nur noch Rauschen an


class Stretch:
    """Automatische GHS-Folge: normiert (Schwarz-/Weißpunkt aus dem Bild), dann `passes` Durchgänge, die den
    Himmelshintergrund schrittweise auf `target` bringen; je Durchgang Symmetriepunkt = Hintergrund + sp_sigma·σ.
    Die Kurve wird je Farbkanal gleich angewandt (Sternkerne laufen nach Weiß). Wiederverwendbar: dieselbe Kurve
    für Original und sternloses Bild."""

    def __init__(self, ref: np.ndarray, target: float, passes: int = 3, sp_sigma: float = 0.0, hp: float = 0.92,
                 full: np.ndarray | None = None):
        """ref: Bild, dessen Hintergrund eingemessen wird (sternlos); full: Bild mit Sternen für den Weißpunkt –
        sonst wäre der Weißpunkt eines sternlosen Bildes winzig und Sterne würden später abgeschnitten."""
        L = luminance(ref)
        med = float(np.median(L))
        sig = 1.4826 * float(np.median(np.abs(L - med))) + 1e-12
        self.black = med - 2.8 * sig
        Lw = luminance(full) if full is not None else L
        self.white = max(float(np.percentile(Lw, 99.95)), med + 10 * sig)
        x = np.clip((L[::3, ::3] - self.black) / (self.white - self.black), 0, 1)       # Vorschau zum Einmessen
        self.steps: list[tuple] = []
        bs = [6.0, 3.0, 1.0, 0.5][:passes] if passes <= 4 else [3.0] * passes
        for k, b in enumerate(bs):
            bg = float(np.median(x))
            s = 1.4826 * float(np.median(np.abs(x - bg))) + 1e-6
            goal = bg * (target / max(bg, 1e-6)) ** ((k + 1) / len(bs))          # geometrisch zum Ziel
            if goal <= bg * 1.01:
                break
            SP = min(0.5, bg + sp_sigma * s)
            lo, hi = 0.0, D_MAX
            for _ in range(40):                                                   # Stärke D per Bisektion
                D = (lo + hi) / 2
                if float(np.median(ghs(x, D, b, SP, 0.0, hp))) < goal:
                    lo = D
                else:
                    hi = D
            D = (lo + hi) / 2
            self.steps.append((round(D, 3), b, round(SP, 5), 0.0, hp))
            x = ghs(x, D, b, SP, 0.0, hp)
        # Rest bis zum Ziel (falls die GHS-Stärke begrenzt hat) mit einer sanften Mitteltonkurve
        bg = float(np.median(x))
        self.mtf = None
        if 1e-5 < bg < target * 0.97:
            self.mtf = float(np.clip(bg * (target - 1) / (2 * target * bg - target - bg), 1e-4, 0.999))

    def __call__(self, img: np.ndarray) -> np.ndarray:
        x = np.clip((img - self.black) / (self.white - self.black), 0, 1).astype(np.float32)
        for D, b, SP, LP, HP in self.steps:
            x = ghs(x, D, b, SP, LP, HP)
        if self.mtf is not None:
            m = self.mtf
            x = ((m - 1) * x) / ((2 * m - 1) * x - m)
        return x.astype(np.float32)


# --------------------------------------------------------- Sterne trennen #
def _odd(v: float, lo: int, hi: int) -> int:
    k = int(np.clip(round(v), lo, hi))
    return k + 1 - k % 2


def _open_big(a: np.ndarray, size: int) -> np.ndarray:
    """Morphologisches Öffnen mit großem Fenster auf 4-fach verkleinertem Bild (schnell), zurück auf volle Größe."""
    if min(a.shape) < 400:
        return ndi.grey_opening(a, size=(size, size))
    small = a[: a.shape[0] // 4 * 4, : a.shape[1] // 4 * 4].reshape(a.shape[0] // 4, 4, a.shape[1] // 4, 4).mean((1, 3))
    k = _odd(size / 4, 3, 41)
    op = ndi.grey_opening(small, size=(k, k))
    full = ndi.zoom(op, 4, order=1)
    return np.pad(full, ((0, a.shape[0] - full.shape[0]), (0, a.shape[1] - full.shape[1])), mode="edge")[: a.shape[0], : a.shape[1]]


def star_mask(L: np.ndarray, fwhm: float) -> tuple[np.ndarray, np.ndarray, int, int]:
    """Masken (klein, hell) runder, kompakter Quellen. Nebelfilamente sind langgestreckt bzw. größer als die
    Öffnung und bleiben draußen."""
    s1 = _odd(2.5 * fwhm + 1, 5, 31)
    s2 = _odd(10 * fwhm + 1, 21, 141)
    sig = noise_sigma(L)
    sm = ndi.gaussian_filter(L, 0.7)
    top1 = sm - ndi.grey_opening(sm, size=(s1, s1))
    top2 = sm - _open_big(sm, s2)
    out = []
    for top, thr, size in ((top1, 4.0, s1), (top2, 40.0, s2)):
        m = top > thr * sig
        lab, n = ndi.label(m)
        if n:
            idx = np.arange(1, n + 1)
            area = ndi.sum_labels(np.ones_like(L), lab, idx)
            sl = ndi.find_objects(lab)
            hgt = np.array([s[0].stop - s[0].start for s in sl], float)
            wid = np.array([s[1].stop - s[1].start for s in sl], float)
            fill = area / np.maximum(hgt * wid, 1)
            ok = (area <= np.pi * (0.75 * size) ** 2 * 1.6) & (np.maximum(hgt, wid) / np.maximum(np.minimum(hgt, wid), 1) < 2.2) & (fill > 0.35)
            m = np.isin(lab, idx[ok])
        out.append(m)
    return out[0], out[1], s1, s2


def separate_internal(img: np.ndarray, fwhm: float) -> tuple[np.ndarray, np.ndarray]:
    """Eigene Sternentrennung (linear). Gibt (sternlos, Sterne) zurück, sternlos + Sterne = Original."""
    L = luminance(img)
    small, big, s1, s2 = star_mask(L, fwhm)
    g = max(1, int(round(fwhm)))
    w_small = ndi.gaussian_filter(ndi.binary_dilation(small, iterations=g).astype(np.float32), fwhm / 2.5)
    w_big = ndi.gaussian_filter(ndi.binary_dilation(big, iterations=3 * g).astype(np.float32), fwhm)
    w_small = np.clip(w_small * 1.6, 0, 1)
    w_big = np.clip(w_big * 1.6, 0, 1)
    bg = (w_small < 0.01) & (w_big < 0.01)
    starless = np.empty_like(img)
    for c in range(img.shape[0]):
        ch = img[c]
        o1 = ndi.grey_opening(ch, size=(s1, s1))
        o2 = _open_big(o1, s2)
        # Öffnen liegt im Rauschen systematisch zu tief -> Versatz aus sternfreien Bereichen ausgleichen
        o1 = ndi.gaussian_filter(o1, fwhm / 2) + float(np.median((ch - o1)[bg]))
        o2 = ndi.gaussian_filter(o2, fwhm) + float(np.median((ch - o2)[bg]))
        fill = o1 * (1 - w_big) + o2 * w_big
        w = np.maximum(w_small, w_big)
        starless[c] = np.minimum(ch, ch * (1 - w) + fill * w)       # nie heller als das Original
    return starless.astype(np.float32), (img - starless).astype(np.float32)


# ---------------------------------------------------------------- StarNet #
# StarNet2 (Mikita Misiura) ist kostenlos, aber nicht frei lizenziert („all rights reserved“): AstroFlow liefert es nicht mit
# und lädt es nicht selbst herunter. Der Nutzer lädt die Kommandozeilen-Version von starnetastro.com und installiert die ZIP
# über die Einstellungen (-> <data>/tools/starnet) oder trägt einen Pfad ein.
STARNET_PAGE = "https://www.starnetastro.com/cli-tools/"
EXE_NAMES = ("starnet2.exe", "starnet++.exe", "starnet2", "starnet++")


class StarnetConfig:
    path: str = ""                 # vom Nutzer gesetzt (Einstellungen)
    tools_dir: Path | None = None  # Installationsordner (data/tools/starnet)


STARNET = StarnetConfig()


def _find_exe(root: Path) -> Path | None:
    for name in EXE_NAMES:
        hits = sorted(root.rglob(name), key=lambda p: len(p.parts))
        if hits:
            return hits[0]
    return None


def starnet_path() -> str | None:
    """StarNet2-Kommandozeile suchen: Einstellung, ASTROFLOW_STARNET, AstroFlow-Installation, PATH, übliche Ordner."""
    for p in (STARNET.path, os.environ.get("ASTROFLOW_STARNET")):
        if p and Path(p).is_file():
            return p
    if STARNET.tools_dir and STARNET.tools_dir.exists():
        exe = _find_exe(STARNET.tools_dir)
        if exe:
            return str(exe)
    for name in ("starnet2", "starnet++", "starnet2.exe", "starnet++.exe"):
        p = shutil.which(name)
        if p:
            return p
    for d in (Path(os.environ.get("LOCALAPPDATA", "")) / "starnet", Path("C:/Program Files/StarNet2"), Path("/opt/starnet")):
        for name in ("starnet2.exe", "starnet++.exe", "starnet2", "starnet++"):
            if (d / name).exists():
                return str(d / name)
    return None


def separate_starnet(img: np.ndarray, exe: str, timeout: int = 900) -> tuple[np.ndarray, np.ndarray]:
    """StarNet arbeitet auf gestreckten 16-bit-TIFFs: wie Siril (Option -stretch) mit einer MTF strecken, StarNet aufrufen,
    Ergebnis mit der Umkehrkurve zurück ins Lineare bringen."""
    import tifffile

    lo = float(np.percentile(img, 0.1))
    hi = float(np.percentile(img, 99.995))
    x = np.clip((img - lo) / (hi - lo), 0, 1)
    m0 = float(np.clip(np.median(x), 1e-5, 0.5))
    t = 0.25
    m = m0 * (t - 1) / (2 * t * m0 - t - m0)                          # MTF, die den Median auf 0,25 bringt
    fwd = lambda v: ((m - 1) * v) / ((2 * m - 1) * v - m)
    inv = lambda y: (m * y) / ((2 * m - 1) * y - (m - 1))             # Umkehrfunktion der MTF
    with tempfile.TemporaryDirectory() as td:
        src, dst = Path(td) / "in.tif", Path(td) / "out.tif"
        tifffile.imwrite(src, (np.moveaxis(fwd(x), 0, -1) * 65535 + 0.5).astype(np.uint16), photometric="rgb")
        r = subprocess.run([exe, "-i", str(src), "-o", str(dst)], cwd=str(Path(exe).parent), timeout=timeout,
                           capture_output=True, text=True, errors="replace")
        if r.returncode != 0 or not dst.exists():
            tail = " | ".join((r.stdout + r.stderr).strip().splitlines()[-4:])
            raise RuntimeError(f"StarNet beendet mit Code {r.returncode & 0xFFFFFFFF:#x}: {tail or 'keine Ausgabe'}")
        y = tifffile.imread(dst).astype(np.float32) / 65535
    y = np.moveaxis(y, -1, 0) if y.ndim == 3 else np.repeat(y[None], 3, 0)
    starless = np.minimum(inv(np.clip(y, 0, 0.9999)) * (hi - lo) + lo, img)
    return starless.astype(np.float32), (img - starless).astype(np.float32)


def install_zip(zip_path: Path, target: Path) -> str:
    """Vom Nutzer heruntergeladene StarNet-ZIP entpacken (ohne Pfade außerhalb des Zielordners), Programm suchen,
    ausführbar machen und mit einem kleinen Probebild testen. Gibt den Programmpfad zurück."""
    import zipfile

    tmp = target.with_name(target.name + ".neu")
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    try:
        with zipfile.ZipFile(zip_path) as z:
            for m in z.infolist():
                dest = (tmp / m.filename).resolve()
                if not str(dest).startswith(str(tmp.resolve())):
                    raise ValueError(f"unzulässiger Pfad in der ZIP: {m.filename}")
            z.extractall(tmp)
        exe = _find_exe(tmp)
        if not exe:
            raise ValueError("In der ZIP wurde kein StarNet-Programm (starnet2 / starnet++) gefunden – "
                             "bitte die Kommandozeilen-Version (CLI) herunterladen.")
        if os.name != "nt":
            for f in exe.parent.iterdir():
                if f.is_file():
                    f.chmod(f.stat().st_mode | 0o111)
        selftest(str(exe))
        shutil.rmtree(target, ignore_errors=True)
        tmp.rename(target)
        return str(_find_exe(target))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def selftest(exe: str) -> None:
    """Kleines künstliches Sternfeld durch StarNet schicken; wirft bei Fehlern."""
    rng = np.random.default_rng(0)
    n = 512                                               # StarNet arbeitet in Kacheln – kein zu kleines Probebild
    img = np.full((3, n, n), 100.0, np.float32) + rng.normal(0, 2, (3, n, n)).astype(np.float32)
    yy, xx = np.mgrid[0:n, 0:n]
    for _ in range(60):
        x, y = rng.uniform(20, n - 20, 2)
        img += (2000 * np.exp(-((xx - x) ** 2 + (yy - y) ** 2) / 4.5)).astype(np.float32)
    starless, _ = separate_starnet(img, exe, timeout=300)
    if not np.isfinite(starless).all() or starless.max() > img.max():
        raise ValueError("StarNet lieferte kein gültiges Ergebnis")


def separate(img: np.ndarray, fwhm: float, method: str = "auto", log=lambda m: None) -> tuple[np.ndarray, np.ndarray, str]:
    """method: auto (StarNet, falls installiert, sonst intern) | starnet | intern. Gibt (sternlos, Sterne, Verfahren)."""
    exe = starnet_path() if method in ("auto", "starnet") else None
    if exe:
        try:
            log("Sterne trennen (StarNet)")
            return (*separate_starnet(img, exe), "starnet")
        except Exception as e:                            # StarNet-Fehler brechen die Bearbeitung nie ab
            log(f"StarNet fehlgeschlagen ({e}) – eigene Sternentrennung")
    log("Sterne trennen")
    return (*separate_internal(img, fwhm), "intern")


def screen(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    return 1 - (1 - a) * (1 - b)
