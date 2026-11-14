"""Oft abgefragte Endpunkte teilen ihr Ergebnis (Status, Aktivität, Objektliste).

Unter Last (Stacking, Entwickeln, Auto-Analysen im selben Prozess) kämpfen alle Python-Threads um den GIL. Fragten mehrere Tabs
und das Handy gleichzeitig dieselbe Liste ab, rechnete jede Anfrage sie neu und belegte einen Thread des gemeinsamen Pools –
der Stau ließ selbst `/api/status` ~48 s warten (Browser-Trace 14.11.2026). Hier rechnet je Schlüssel höchstens ein Thread,
die übrigen warten auf genau dieses Ergebnis, und ein frisches Ergebnis wird wiederverwendet.

Frisch heißt: gleiche Signatur (z. B. Schreibzähler der DB) und keine schreibende Anfrage seither (`touch`, aus der Middleware),
höchstens `max_age` alt. Ändert sich die Signatur ständig (Analyse schreibt je Frame), gilt ein Ergebnis trotzdem `hold`
Sekunden – mindestens `min_age`, bei langsamer Berechnung länger (doppelte Rechenzeit, höchstens 10 s): Unter Last fragt der
Server so von selbst seltener nach. Eine Aktion des Nutzers (POST/PATCH/DELETE) ist immer sofort sichtbar.
"""
from __future__ import annotations

import threading
import time
import weakref

_epoch = 0                      # zählt abgeschlossene schreibende Anfragen


def touch() -> None:
    """Nach jeder schreibenden Anfrage: alle geteilten Ergebnisse gelten als veraltet."""
    global _epoch
    _epoch += 1


class Shared:
    def __init__(self, fn, *, min_age: float = 0.0, max_age: float = 60.0, signature=None, background: bool = False):
        """background=True: hat sich nur die Signatur geändert (keine Nutzeraktion, nicht älter als max_age), kommt sofort
        der letzte Stand zurück und ein Hintergrund-Thread rechnet neu – Pollen wartet dann nie auf eine Neuberechnung."""
        self.fn, self.min_age, self.max_age, self.signature = fn, min_age, max_age, signature
        self.background = background
        self._lock = threading.Lock()
        self._keys: dict = {}           # key -> Lock (ein Rechner je Schlüssel)
        self._cache: dict = {}          # key -> (sig, epoch, fertig, dauer, wert)

    def _sig(self):
        return self.signature() if self.signature else None

    def fresh(self, key=None, sig=None):
        """Wert, falls noch frisch, sonst None (kein Rechnen, kein Warten – darf auf der Ereignisschleife laufen)."""
        e = self._cache.get(key)
        if not e or e[1] != _epoch:
            return None
        age = time.monotonic() - e[2]
        if age > self.max_age:
            return None
        hold = max(self.min_age, min(10.0, 2 * e[3]))
        if age < hold or (self.signature and e[0] == (self._sig() if sig is None else sig)):
            return e
        return None

    def get(self, *args):
        """Ergebnis von fn(*args): geteilt, solange frisch; sonst rechnet genau ein Thread, die anderen warten auf ihn."""
        key = args
        if e := self.fresh(key):
            return e[4]
        with self._lock:
            lk = self._keys.setdefault(key, threading.Lock())
        e = self._cache.get(key)
        if self.background and e and e[1] == _epoch and time.monotonic() - e[2] <= self.max_age:
            if lk.acquire(blocking=False):            # sonst rechnet schon jemand
                threading.Thread(target=self._refresh, args=(lk, args), daemon=True, name="pollcache").start()
            return e[4]
        with lk:
            if e := self.fresh(key):        # während des Wartens fertig geworden
                return e[4]
            return self._compute(args)

    def _compute(self, args):
        ep, sig = _epoch, self._sig()         # vor dem Rechnen festhalten: Änderungen währenddessen machen es wieder veraltet
        t = time.monotonic()
        v = self.fn(*args)
        self._cache[args] = (sig, ep, time.monotonic(), time.monotonic() - t, v)
        return v

    def _refresh(self, lk, args):
        try:
            if not self.fresh(args):
                self._compute(args)
        except Exception:
            import traceback
            traceback.print_exc()
        finally:
            lk.release()

    async def aget(self, *args):
        """Wie `get`, aber für `async def`-Endpunkte: Frisches kommt ohne Thread zurück, sonst rechnet ein Thread der
        eigenen kleinen Spur (`lane`) – Status und Aktivität warten so nie hinter vollen Pool-Threads anderer Endpunkte."""
        if e := self.fresh(args):
            return e[4]
        import anyio
        return await anyio.to_thread.run_sync(lambda: self.get(*args), limiter=lane())


_lanes: "weakref.WeakKeyDictionary" = weakref.WeakKeyDictionary()


def lane(tokens: int = 4):
    """Eigener kleiner Threadpool für die Status-Abfragen (je Ereignisschleife; Tests starten mehrere)."""
    import asyncio
    import anyio
    loop = asyncio.get_running_loop()
    lim = _lanes.get(loop)
    if lim is None:
        lim = _lanes[loop] = anyio.CapacityLimiter(tokens)
    return lim
