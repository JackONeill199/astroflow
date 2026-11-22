// „AstroFlow unterstützen“ (core/support.py): freundlicher Dialog, der erst nach dem 3. fertigen Bild und nie mitten in einer Arbeit erscheint.
// Dauerhaft erreichbar über Hilfe (#/hilfe) und die Palette (Strg+K). Links nur aus `/api/app` → `support` (Konstante im Server).
(() => {
  const BRAND = {   // Markenfarben nur als Knopfhintergrund, Symbole eigen (kein externes Bild/Skript)
    github: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 20.5s-7.5-4.6-7.5-10.2A4.3 4.3 0 0 1 12 7.6a4.3 4.3 0 0 1 7.5 2.7c0 5.6-7.5 10.2-7.5 10.2z" fill="currentColor"/></svg>',
    kofi: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 8h12v6a4 4 0 0 1-4 4H8a4 4 0 0 1-4-4zM16 9.5h1.5a2.5 2.5 0 0 1 0 5H16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  };
  let shown = false, started = false;
  const links = () => AF.app?.support || [];

  // Passt es gerade? Nie in der Einrichtung, über einem offenen Dialog, im Import oder während Aufträge laufen.
  function fits() {
    if (shown || document.hidden || !links().length) return false;
    if (document.querySelector('dialog[open]') || !$('#drop')?.hidden) return false;
    if (AF.app.setup?.pending || /^#?\/(willkommen|import)/.test(location.hash)) return false;
    return !(AF.status?.busy > 0);
  }

  function open(auto = false) {
    const ls = links();
    if (!ls.length || document.querySelector('#supportDlg')) return;
    const d = document.createElement('dialog');
    d.id = 'supportDlg'; d.className = 'support-dlg'; d.setAttribute('aria-labelledby', 'supportTitle');
    d.innerHTML = `<button class="sp-x" data-act="close" aria-label="Schließen">×</button>
      <div class="sp-head"><span class="sp-heart" aria-hidden="true">${icon('heart')}</span><h2 id="supportTitle">Gefällt dir AstroFlow?</h2></div>
      <p class="mut">AstroFlow ist kostenlos und wird in meiner Freizeit entwickelt. Wenn es dir Zeit spart und deine Aufnahmen ordentlich entwickelt,
        unterstütze das Projekt gern – jeder Beitrag hilft, es weiterzuentwickeln.</p>
      <div class="sp-links">${ls.map(l => `<a class="sp-btn sp-${esc(l.id)}" href="${esc(l.url)}" target="_blank" rel="noopener noreferrer" data-act="link">${BRAND[l.id] || ''}<span>${esc(l.label)}</span></a>`).join('')}</div>
      <p class="dim sm sp-note">AstroFlow läuft komplett auf deinem Rechner und sammelt keine Daten. Die Links öffnen die Seite im Browser.</p>
      <div class="sp-foot">${auto ? '<button class="linkbtn sm" data-act="never">Nicht mehr zeigen</button>' : ''}
        <button class="btn ghost" data-act="${auto ? 'later' : 'close'}">${auto ? 'Vielleicht später' : 'Schließen'}</button></div>`;
    document.body.appendChild(d);
    const done = (act) => {
      if (auto && act) send('/support', { action: act }).catch(() => {});
      d.close();
    };
    d.addEventListener('click', (e) => {
      const b = e.target.closest('[data-act]');
      if (b) { if (b.dataset.act === 'link') { if (auto) done('later'); } else done(b.dataset.act === 'never' ? 'never' : auto ? 'later' : null); }
      else if (e.target === d) done(auto ? 'later' : null);
    });
    d.addEventListener('cancel', (e) => { e.preventDefault(); done(auto ? 'later' : null); });   // Esc = später
    d.addEventListener('close', () => d.remove());
    d.showModal();
    $('.sp-btn', d)?.focus();
    if (auto) { shown = true; send('/support', { action: 'shown' }).catch(() => {}); }
  }

  async function check() {
    if (shown || !fits()) return;
    try {
      const s = await api('/support');
      if (s.due && fits()) open(true);
    } catch { /* kein Hinweis ist besser als ein Fehler */ }
  }

  // Verkettet (nie setInterval für Serverabfragen): nächste Prüfung erst, wenn die vorige fertig ist; Schluss, sobald der Dialog kam
  async function loop(wait) {
    await new Promise(r => setTimeout(r, wait));
    await check();
    if (!shown) loop(60000);
  }

  function start() {
    if (started) return;
    started = true;
    loop(8000);
  }

  window.support = { open, start, check };
  if (typeof AF !== 'undefined' && AF.app) start();   // AF ist eine const in app.js (keine window-Eigenschaft)
})();
