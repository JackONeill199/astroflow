"""Vaonis-Teleskope (Vespera, Stellina) **nur lesen** – ohne Signaturschlüssel, ohne Bibliothek.

Was hier steht, braucht keine Anmeldung: Status per `GET app/status` (REST, Port 8082) und die Übersetzung in den
Treiber-Vertrag (`translate`). Rohbilder kommen über den anonymen FTP (`frames.py`, `captures.py`). Steuern (Öffnen, Ausrichten,
Beobachten, Einklappen) braucht signierte Befehle und gehört in ein Treiber-Plugin (`plugins.py`), das der Nutzer selbst
bereitstellt – im Repo steht keine Anmelde- oder Befehlslogik.

Befunde der Proben (14.11.2026, Vespera 3.5.3) stecken in `_parked` (ALT −90°/`atStop`), `_battery` (beim Laden `chargeLevel: 0` =
unbekannt) und den Operationstypen `OPEN`/`PARK`/`OBSERVATION`/`PLAN`.
"""
from __future__ import annotations

import threading
import time

from .driver import Driver, DriverError, TelescopeState

DEVICE_ID = "astroflow"            # so meldet sich AstroFlow (bzw. ein Plugin) als steuerndes Gerät an
DEFAULT_HOST = "10.0.0.1"          # Werkseinstellung im WLAN des Teleskops
STATUS_PORT = 8082
STALE_S = 30                       # Status älter → gilt als getrennt
POLL_S = 2.0


def _find(d, key):
    stack, seen = [d], set()
    while stack:
        cur = stack.pop()
        if id(cur) in seen:
            continue
        seen.add(id(cur))
        if isinstance(cur, dict):
            if key in cur:
                return cur[key]
            stack.extend(cur.values())
        elif isinstance(cur, list):
            stack.extend(cur)
    return None


def _last_step(op: dict) -> str | None:
    steps = op.get("steps") or []
    while steps:
        last = steps[-1]
        if isinstance(last, dict) and last.get("steps"):
            steps = last["steps"]
            continue
        return last.get("type") if isinstance(last, dict) else None
    return None


def translate(raw: dict | None, me: str = DEVICE_ID, age_s: float = 0.0, connected: bool = True) -> TelescopeState:
    """Status des Geräts → `TelescopeState`. Kennungen und Ort werden nicht übernommen."""
    r = (raw or {}).get("result", raw) or {}
    motors = r.get("motors") or {}
    az = (motors.get("AZ") or {}).get("position")
    alt = (motors.get("ALT") or {}).get("position")
    ops = [o for o in [r.get("currentOperation")] + list(r.get("otherCurrentOperations") or []) if isinstance(o, dict)]
    step, target, cap, err, plan = "idle", None, {}, None, False
    for op in ops:
        typ = str(op.get("type") or "").upper()
        if typ == "PLAN":
            plan = True
        elif "PARK" in typ:
            step = "parking"
        elif "OPEN" in typ or "MAINTENANCE" in typ:
            step = "opening"
        elif "INIT" in typ:
            step = "init"
        elif typ == "OBSERVATION":
            ls = (_last_step(op) or "").upper()
            cap = op.get("capture") or {}
            step = "capture" if cap and not cap.get("endTime") else "astrometry" if "ASTROMETRY" in ls else "goto"
            t = op.get("target") or {}
            if t.get("ra") is not None:
                target = {"id": str(t.get("objectId") or t.get("objectName") or "?"), "name": str(t.get("objectName") or t.get("objectId") or "?"),
                          "ra": t.get("ra"), "dec": t.get("de")}
        if isinstance(op.get("error"), dict) and op["error"].get("name"):
            err = str(op["error"]["name"])
    master = r.get("masterDeviceId")
    st = r.get("storage") or {}
    pub = st.get("public") or st.get("data") or {}
    bat = r.get("internalBattery") or {}
    cats = (r.get("settings") or {}).get("storageFileCategories")
    hum = cap.get("startHumidity") if cap else None
    return TelescopeState(
        model=str(r.get("model") or ""), simulated=False, connected=connected and age_s < STALE_S,
        master="us" if master == me else "other" if master else "none",
        parked=_parked(motors, step, ops),
        initialized=bool(r.get("initialized")), az=az, alt=alt, step=step, target=target,
        frames=int(cap.get("acquisitionCount") or 0), stacked=int(cap.get("stackingCount") or 0), rejected=int(cap.get("stackingErrorCount") or 0),
        astrometry_failed=bool(err and "ASTROMETRY" in err.upper()), battery=_battery(bat), charging=_charging(bat),
        storage_free=(pub["available"] / pub["size"]) if pub.get("size") else None,
        raw_enabled=None if cats is None else "RAW" in cats, humidity=hum or None, error=err, device_plan=plan, age_s=age_s)


def _charging(bat: dict) -> bool:
    return str(bat.get("chargeStatus") or "").upper() == "CHARGING"


def _battery(bat: dict) -> int | None:
    """Akkustand in %. Probe 2 (14.11.2026): beim Laden meldet die Vespera `chargeLevel: 0` – das ist „unbekannt“, nicht leer."""
    lvl = bat.get("chargeLevel")
    if lvl is None or (_charging(bat) and not lvl):
        return None
    return int(lvl)


def _parked(motors: dict, step: str, ops: list) -> bool:
    """Eingeklappt: Probe 1 (14.11.2026, Vespera 3.5.3) zeigte ALT −90° mit `atStop: true`, AZ 0, keine Operation.
    Nach einem Plan ohne Einklappen stand ALT bei ~38° ohne `atStop`. Übergang beim Öffnen/Parken: Probe 3."""
    if ops or step in ("opening", "parking"):
        return False
    m = motors.get("ALT") or {}
    alt = m.get("position")
    return bool(m.get("atStop")) or (alt is not None and float(alt) <= -80)


class VaonisReadOnly(Driver):
    """Status anzeigen ohne Steuerung (kein Plugin nötig). Jeder Bewegungsbefehl → feste Meldung."""
    kind = "vaonis-read"
    label = "Vaonis (nur Status)"
    capabilities = frozenset()

    def __init__(self, ip: str | None = None, http=None):
        self.ip = ip or DEFAULT_HOST
        self._http = http
        self._raw: dict | None = None
        self._seen = 0.0
        self._lock = threading.Lock()
        self.connected = False

    def _get(self) -> dict:
        http = self._http
        if http is None:
            import httpx
            http = httpx
        r = http.get(f"http://{self.ip}:{STATUS_PORT}/v1/app/status", timeout=5)
        r.raise_for_status()
        return r.json()

    def connect(self):
        try:
            self._raw, self._seen = self._get(), time.time()
            self.connected = True
        except Exception:
            self.connected = False
            raise DriverError("Teleskop nicht erreichbar – ist der Rechner in seinem WLAN?") from None

    def disconnect(self):
        self.connected = False

    def take_control(self):
        pass                                          # nur lesen: nie Master

    def release_control(self):
        pass

    def state(self) -> TelescopeState:
        if not self.connected:
            return TelescopeState(connected=False)
        with self._lock:
            if time.time() - self._seen >= POLL_S:
                try:
                    self._raw, self._seen = self._get(), time.time()
                except Exception:
                    pass
        return translate(self._raw, DEVICE_ID, time.time() - self._seen, connected=True)

    def _no(self, *a, **kw):
        raise DriverError("Steuern braucht ein Teleskop-Treiber-Plugin (Einstellungen → Teleskop). Ohne Plugin zeigt AstroFlow nur den Status.")

    open = park = init = observe = stop = focus = _no
