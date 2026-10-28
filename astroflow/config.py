"""Zentrale Einstellungen (per Umgebungsvariablen steuerbar)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Settings:
    data_dir: Path
    input_dir: Path
    work_dir: Path
    output_dir: Path
    db_path: Path
    auto: bool = True          # neue Aufnahmen automatisch verarbeiten
    scan_interval: int = 60    # Sekunden zwischen Ordner-Scans
    threads: int = 4
    batch_mb: int = 700        # RAM-Budget pro Stacking-Stapel (Standard aus load_settings: 1/8 des RAM)
    delete_uploads: bool = True  # hochgeladene Einzelbilder nach erfolgreichem Stacking löschen (Speicher)
    gaia: bool = True            # Farbkalibrierung über Gaia-Sternfarben (online, nur Feldkoordinaten werden gesendet)

    @property
    def uploads_dir(self) -> Path:
        return self.input_dir / "uploads"

    def ensure(self) -> "Settings":
        for p in (self.data_dir, self.input_dir, self.work_dir, self.output_dir):
            p.mkdir(parents=True, exist_ok=True)
        return self


def total_ram_mb() -> int:
    """Verfügbarer Arbeitsspeicher in MB (Docker-Limit hat Vorrang); 4096, falls unbekannt."""
    try:                                                     # cgroup v2/v1: Speicherlimit des Containers
        for p in ("/sys/fs/cgroup/memory.max", "/sys/fs/cgroup/memory/memory.limit_in_bytes"):
            if os.path.exists(p):
                v = open(p).read().strip()
                if v.isdigit() and int(v) < 1 << 50:
                    return int(v) // 2**20
        if hasattr(os, "sysconf") and "SC_PHYS_PAGES" in os.sysconf_names:
            return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") // 2**20
        import ctypes                                        # Windows

        class MEMSTAT(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
        m = MEMSTAT()
        m.dwLength = ctypes.sizeof(MEMSTAT)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m)):
            return m.ullTotalPhys // 2**20
    except Exception:
        pass
    return 4096


def free_ram_mb() -> int | None:
    """Aktuell freier Arbeitsspeicher in MB (Linux MemAvailable, Windows ullAvailPhys); None, falls unbekannt."""
    try:
        if os.path.exists("/proc/meminfo"):
            for line in open("/proc/meminfo"):
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) // 1024
        import ctypes

        class MEMSTAT(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
        m = MEMSTAT()
        m.dwLength = ctypes.sizeof(MEMSTAT)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m)):
            return m.ullAvailPhys // 2**20
    except Exception:
        pass
    return None


def load_settings(data_dir: str | os.PathLike | None = None) -> Settings:
    root = Path(data_dir or os.environ.get("ASTROFLOW_DATA", "./data")).resolve()
    return Settings(
        data_dir=root,
        input_dir=Path(os.environ.get("ASTROFLOW_INPUT", root / "input")).resolve(),
        work_dir=root / "work",
        output_dir=Path(os.environ.get("ASTROFLOW_OUTPUT", root / "output")).resolve(),
        db_path=root / "astroflow.db",
        auto=os.environ.get("ASTROFLOW_AUTO", "1") not in ("0", "false", "no"),
        scan_interval=int(os.environ.get("ASTROFLOW_SCAN_INTERVAL", "60")),
        threads=int(os.environ.get("ASTROFLOW_THREADS", str(max(2, min(12, (os.cpu_count() or 4) - 1))))),
        # Standard: 1/8 des RAM für den Stacking-Stapel (700 MB … 4 GB) – größere Stapel = schneller und besseres Clipping
        batch_mb=int(os.environ.get("ASTROFLOW_BATCH_MB", str(int(min(4000, max(700, total_ram_mb() // 8)))))),
        delete_uploads=os.environ.get("ASTROFLOW_DELETE_UPLOADS", "1") not in ("0", "false", "no"),
        gaia=os.environ.get("ASTROFLOW_GAIA", "1") not in ("0", "false", "no"),
    ).ensure()
