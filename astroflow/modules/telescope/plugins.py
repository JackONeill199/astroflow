"""Teleskop-Treiber-Plugins: fremde Treiber, die AstroFlow nur über den Treiber-Vertrag (`driver.py`, `CONTRACT`) anspricht.

Ein Plugin ist ein Ordner mit `plugin.json` und einer Python-Datei mit einer Klasse, die `Driver` erfüllt:

    {"name": "…", "family": "vaonis", "models": ["vespera"], "contract": 1, "module": "driver.py", "class": "MeinTreiber",
     "requires": ["requests"], "options": [{"key": "ip", "label": "Adresse", "hint": "…", "type": "text|bool"}]}

Gefunden wird in `<datenordner>/plugins/<ordner>/` und in den vom Nutzer eingetragenen Ordnern (`telescope.plugin_paths`,
je Eintrag ein Plugin-Ordner oder ein Ordner mit Plugin-Ordnern), außerdem über den Entry Point `astroflow.telescope_drivers`
(installierte Pakete; Wert = Klasse, Beschreibung als Klassenattribut `plugin`). Ausgeführt wird ein Plugin erst, wenn der
Nutzer es am Rechner selbst aktiviert hat (`telescope.plugins_enabled`) – es ist fremder Code mit allen Rechten des Servers.
Ein defektes oder unpassendes Plugin legt AstroFlow nicht lahm: es erscheint mit fester deutscher Meldung, der Simulator bleibt.

**Eigene Pakete:** Ein Plugin bringt seine Abhängigkeiten im eigenen Ordner mit (`site-packages/` oder `vendor/`, z. B.
`pip install --target <plugin>/site-packages paket`). AstroFlow nimmt diese Ordner erst beim Laden des Plugins in den Suchpfad –
**hinten** angehängt, damit sie AstroFlows eigene Pakete nie verdrängen. So bleibt die Python-Umgebung von AstroFlow (auch der
installierten Windows-Fassung) unberührt; die Pakete gehören nie in `requirements.txt`.
"""
from __future__ import annotations

import hashlib
import importlib.machinery
import importlib.util
import json
import re
import sys
import traceback
from dataclasses import dataclass, field
from pathlib import Path

from .driver import CONTRACT, Driver, DriverError

GROUP = "astroflow.telescope_drivers"
ID_RE = re.compile(r"[^a-z0-9_-]+")
MAX_PLUGINS = 50
VENDOR_DIRS = ("site-packages", "vendor")      # Paketordner im Plugin


@dataclass
class PluginInfo:
    id: str
    name: str
    family: str = ""
    models: list = field(default_factory=list)
    contract: int | None = None
    path: str = ""                 # Ordner (oder „Paket“ bei Entry Points)
    module: str = "driver.py"
    cls: str = ""
    requires: list = field(default_factory=list)
    options: list = field(default_factory=list)
    error: str | None = None       # feste deutsche Meldung, warum es nicht nutzbar ist
    missing: list = field(default_factory=list)
    entry: object = None           # Entry Point (nicht serialisieren)
    vendor: list = field(default_factory=list)     # eigene Paketordner des Plugins
    private: list = field(default_factory=list)    # weitere private Ordner (plugin.json „private“, relativ zum Plugin)

    def view(self, enabled: set) -> dict:
        return {"id": self.id, "name": self.name, "family": self.family, "models": self.models, "contract": self.contract,
                "path": self.path, "options": self.options, "error": self.error, "missing": self.missing,
                "enabled": self.id in enabled, "usable": self.error is None}


def _pid(name: str, where: str) -> str:
    base = ID_RE.sub("-", name.lower()).strip("-")[:30] or "plugin"
    return f"{base}-{hashlib.sha1(where.encode('utf-8')).hexdigest()[:6]}"       # gleicher Name an zwei Orten ≠ gleiches Plugin


def _has(module: str, vendor: list[str]) -> bool:
    """Paket vorhanden – in AstroFlows Umgebung oder in den Paketordnern des Plugins (ohne etwas zu importieren)."""
    top = module.split(".")[0]
    try:
        if importlib.util.find_spec(top) is not None:
            return True
    except (ImportError, ValueError):
        pass
    return bool(vendor) and importlib.machinery.PathFinder.find_spec(top, vendor) is not None


def _check(info: PluginInfo) -> PluginInfo:
    if info.contract != CONTRACT:
        info.error = (f"Plugin passt nicht zu dieser AstroFlow-Version (Treiber-Vertrag {info.contract if info.contract is not None else '?'}, "
                      f"diese Version erwartet {CONTRACT}).")
        return info
    info.missing = [m for m in info.requires if not _has(str(m), info.vendor)]
    if info.missing:
        info.error = ("Es fehlen Pakete: " + ", ".join(info.missing) + ". Sie gehören in den Plugin-Ordner unter „site-packages“ "
                      "(pip install --target …) – AstroFlow selbst bleibt unverändert.")
    return info


def _from_dir(d: Path) -> PluginInfo | None:
    f = d / "plugin.json"
    if not f.is_file():
        return None
    try:
        meta = json.loads(f.read_text(encoding="utf-8-sig"))
        if not isinstance(meta, dict):
            raise ValueError
    except (OSError, ValueError):
        return PluginInfo(id=_pid(d.name, str(d)), name=d.name, path=str(d), error="Beschreibung plugin.json ist unlesbar.")
    try:
        contract = int(meta.get("contract"))
    except (TypeError, ValueError):
        contract = None
    info = PluginInfo(id=_pid(str(meta.get("name") or d.name), str(d.resolve())), name=str(meta.get("name") or d.name)[:80],
                      family=str(meta.get("family") or "")[:40], models=[str(m)[:40] for m in meta.get("models") or []][:20],
                      contract=contract, path=str(d), module=str(meta.get("module") or "driver.py"), cls=str(meta.get("class") or ""),
                      requires=[str(m) for m in meta.get("requires") or []][:20],
                      options=[o for o in meta.get("options") or [] if isinstance(o, dict) and o.get("key")][:20])
    info.vendor = [str((d / v).resolve()) for v in VENDOR_DIRS if (d / v).is_dir()]
    info.private = [str((d / str(x)).resolve()) for x in (meta.get("private") or [])[:5] if str(x).strip()]
    mod = (d / info.module).resolve()
    if mod.parent != d.resolve() or mod.suffix != ".py" or not mod.is_file():
        info.error = "Treiberdatei fehlt oder liegt nicht im Plugin-Ordner."
        return info
    if not info.cls:
        info.error = "In plugin.json fehlt der Klassenname („class“)."
        return info
    return _check(info)


def _entry_points() -> list[PluginInfo]:
    out = []
    try:
        from importlib.metadata import entry_points
        eps = entry_points(group=GROUP)
    except Exception:
        return out
    for ep in list(eps)[:MAX_PLUGINS]:
        meta = {}
        info = PluginInfo(id=_pid(ep.name, f"ep:{ep.value}"), name=ep.name, path=f"Paket ({ep.value})", entry=ep)
        try:
            meta = getattr(ep.load(), "plugin", {}) or {}         # Laden erst hier, Ausführen erst nach Aktivierung
        except Exception:
            info.error = "Plugin-Paket lässt sich nicht laden."
            out.append(info)
            continue
        info.name = str(meta.get("name") or ep.name)[:80]
        info.family, info.models = str(meta.get("family") or ""), list(meta.get("models") or [])
        try:
            info.contract = int(meta.get("contract"))
        except (TypeError, ValueError):
            info.contract = None
        info.requires, info.options = list(meta.get("requires") or []), list(meta.get("options") or [])
        out.append(_check(info))
    return out


def discover(data_dir: Path, extra: list[str] | None = None, entry_points: bool = True) -> list[PluginInfo]:
    """Alle Plugins (auch defekte, mit Meldung). Nichts wird ausgeführt."""
    roots: list[Path] = [Path(data_dir) / "plugins"] + [Path(p) for p in (extra or []) if str(p).strip()]
    found, seen = [], set()
    for root in roots:
        cands = [root] if (root / "plugin.json").is_file() else (sorted(p for p in root.iterdir() if p.is_dir()) if root.is_dir() else [])
        for d in cands[:MAX_PLUGINS]:
            key = str(d.resolve()).lower()
            if key in seen:
                continue
            seen.add(key)
            info = _from_dir(d)
            if info:
                found.append(info)
    if entry_points:
        found += _entry_points()
    return found[:MAX_PLUGINS]


def private_dirs(data_dir: Path, extra: list[str] | None = None, options: dict | None = None) -> list[str]:
    """Ordner, deren Inhalt nie in Fehlerberichte/Protokollauszüge gehört (Plugins und ihre Bibliotheken können Schlüssel
    enthalten): `<daten>/plugins`, eingetragene Plugin-Ordner, gefundene Plugins samt `private`-Ordnern, Pfade in Plugin-Optionen."""
    out = {str(Path(data_dir) / "plugins")}
    for p in extra or []:
        if str(p).strip():
            out.add(str(p).strip())
    for i in discover(data_dir, extra, entry_points=False):
        out.add(i.path)
        out.update(i.private)
    for vals in (options or {}).values():
        for v in (vals or {}).values() if isinstance(vals, dict) else []:
            if isinstance(v, str) and ("/" in v or "\\" in v):
                q = Path(v)
                out.add(str(q.parent if q.suffix else q))
    res = set()
    for p in out:
        res.add(p)
        try:
            res.add(str(Path(p).resolve()))
        except OSError:
            pass
    return sorted(res, key=len, reverse=True)


def load(info: PluginInfo, options: dict | None = None) -> Driver:
    """Treiber erzeugen. Jeder Fehler → DriverError mit fester Meldung (Details nur im Protokoll)."""
    if info.error:
        raise DriverError(info.error)
    for v in info.vendor:                                   # eigene Pakete des Plugins: hinten an den Suchpfad
        if v not in sys.path:
            sys.path.append(v)
    try:
        if info.entry is not None:
            cls = info.entry.load()
        else:
            name = f"astroflow_tplugin_{info.id.replace('-', '_')}"
            spec = importlib.util.spec_from_file_location(name, Path(info.path) / info.module)
            mod = importlib.util.module_from_spec(spec)
            sys.modules[name] = mod
            spec.loader.exec_module(mod)
            cls = getattr(mod, info.cls)
    except SystemExit:
        raise DriverError("Das Plugin hat sich beim Laden beendet (fehlen Pakete?).") from None
    except DriverError:
        raise
    except Exception:
        traceback.print_exc()
        raise DriverError("Das Plugin lässt sich nicht laden – Details im Protokoll.") from None
    missing = [m for m in ("connect", "state", "open", "park", "init", "observe", "stop") if not callable(getattr(cls, m, None))]
    if missing:
        raise DriverError("Das Plugin erfüllt den Treiber-Vertrag nicht (es fehlen: " + ", ".join(missing) + ").")
    try:
        drv = cls(dict(options or {}))
    except DriverError:
        raise
    except SystemExit:
        raise DriverError("Das Plugin hat sich beim Start beendet (fehlen Pakete?).") from None
    except Exception:
        traceback.print_exc()
        raise DriverError("Das Plugin ließ sich nicht starten – Details im Protokoll.") from None
    if not getattr(drv, "kind", None) or drv.kind in ("sim", "vaonis-read"):
        drv.kind = "plugin"
    drv.label = str(getattr(drv, "label", "") or info.name)
    return drv
