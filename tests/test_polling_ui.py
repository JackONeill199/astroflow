"""Polling der Oberfläche: Runden überlappen nie, verborgene Tabs pausieren (Stau unter Last, Browser-Trace 14.11.2026)."""

# langsamer Server nachgestellt: jede Statusabfrage braucht 3 s – länger als der Abstand von 2,5 s zwischen den Runden.
# Mit setInterval liefen so mehrere Runden gleichzeitig; verkettet startet die nächste erst nach der Antwort.
SLOW_STATUS = """
(() => {
  const real = window.fetch;
  window.__st = { inflight: 0, max: 0, n: 0 };
  window.fetch = async (u, o) => {
    if (!String(u).includes('/api/status')) return real(u, o);
    __st.inflight++; __st.n++; __st.max = Math.max(__st.max, __st.inflight);
    try { await new Promise(r => setTimeout(r, 3000)); return await real(u, o); } finally { __st.inflight--; }
  };
})();
"""


def test_polling_wartet_auf_antwort_und_pausiert_verborgen(server, browser):
    base, _ = server
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.add_init_script(SLOW_STATUS)
    page.goto(base + "/#/bilder")
    page.wait_for_function("window.__st && __st.n >= 2", timeout=20000)
    st = page.evaluate("window.__st")
    assert st["max"] == 1, st                      # nie zwei Statusabfragen gleichzeitig

    # verborgener Tab: keine neuen Runden
    page.evaluate("""() => { Object.defineProperty(document, 'hidden', { configurable: true, get: () => true });
                             document.dispatchEvent(new Event('visibilitychange')); }""")
    page.wait_for_function("__st.inflight === 0", timeout=10000)
    n = page.evaluate("__st.n")
    page.wait_for_timeout(4000)
    assert page.evaluate("__st.n") == n

    # wieder sichtbar: sofort eine Runde
    page.evaluate("""() => { Object.defineProperty(document, 'hidden', { configurable: true, get: () => false });
                             document.dispatchEvent(new Event('visibilitychange')); }""")
    page.wait_for_function(f"__st.n > {n}", timeout=5000)
    assert page.evaluate("window.__st")["max"] == 1
    assert not errors, errors
    page.close()
