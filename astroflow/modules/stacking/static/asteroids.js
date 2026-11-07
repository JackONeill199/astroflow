'use strict';
// „Asteroiden in deinem Bild“ (seit 0.39.0, Wunsch 10) – Abschnitt im Reiter Analyse der Objektseite + Marken/Bahnstriche im Betrachter.
// Daten: /stacking/results/<id>/asteroids (eigener Endpunkt, Auswertung in insights.py → asteroids.py). Nutzt insightLoad/insightData
// und de1 aus depth.js.

const AST_MARKS = { on: store.get('bild.asteroiden.marken', true) };
const AST_STATUS = {
  nachgewiesen: ['nachgewiesen', 'ok'], nicht_gefunden: ['nicht gefunden', ''], keine_rohbilder: ['im Feld, nicht nachgewiesen', ''],
  zu_langsam: ['zu langsam für den Nachweis', ''], zu_schwach: ['zu schwach für dein Bild', ''], nicht_geprueft: ['nicht geprüft', ''],
};
const astMag = (v) => v == null ? '–' : `${de1(v)} mag`;

// Abschnitt. opts.onFocus(x, y, w) zoomt im Betrachter oben.
function asteroidPanel(host, r, viewer, opts = {}) {
  if (!host) return;
  let timer = 0;
  const head = (extra = '') => `<div class="panel-h"><h2>Asteroiden</h2><span class="grow"></span>${extra}</div>`;
  const poll = async (force) => {
    clearTimeout(timer);
    if (!host.isConnected) return;
    let d;
    try { d = await insightLoad('asteroids', r.id, force); } catch (e) { host.innerHTML = head() + `<p class="mut sm">${esc(e.message)}</p>`; return; }
    if (!host.isConnected) return;
    draw(d);
    if (d.state === 'queued' || d.state === 'running') timer = setTimeout(() => poll(true), 2000);
    else if (d.state === 'done') viewer?.redraw();
    viewer?.el.classList.toggle('ast-on', AST_MARKS.on && d.state === 'done' && !!d.bild);
  };
  const run = async () => {
    try { await send(`/stacking/results/${r.id}/asteroids`, {}); insightData.delete(`asteroids:${r.id}`); poll(true); } catch (e) { toast(e.message, 'bad'); }
  };
  function draw(d) {
    if (d.state === 'queued' || d.state === 'running') {
      host.innerHTML = head() + `<div class="depth-wait"><span class="spinner"></span><span class="mut sm">${esc(d.message || 'wird gesucht')} …</span></div>`;
      return;
    }
    if (d.state !== 'done') {
      const why = !d.solved ? 'Dieses Bild ist nicht per Plate-Solving gelöst – ohne Himmelskoordinaten lässt sich nicht vorhersagen, welche Asteroiden im Feld lagen.'
        : !d.enabled ? 'Der Abgleich mit Kleinkörpern (JPL) ist ausgeschaltet – siehe Einstellungen → Bilder.'
        : d.state === 'error' ? esc(d.message || 'Suche fehlgeschlagen')
        : d.state === 'old' ? 'Die gespeicherte Suche stammt von einer älteren Version der Auswertung.'
        : 'Welche Asteroiden lagen in deinem Bildfeld, und welche hast du wirklich erwischt? Die Bahnen kommen von JPL (gesendet werden nur Himmelskoordinaten und Zeit, nie dein Standort), der Nachweis aus deinen Einzelbildern.';
      host.innerHTML = head() + `<p class="mut sm">${why}</p>${d.solved && d.enabled ? `<div class="row depth-empty-acts"><button class="btn sm" data-ast-run>${icon('orbit')}${d.state === 'none' ? 'Asteroiden suchen' : 'Neu suchen'}</button></div>` : ''}`;
      $('[data-ast-run]', host)?.addEventListener('click', run);
      return;
    }
    const items = d.kleinkoerper || [], n = d.nachgewiesen || 0, f = d.schwaechster;
    const at = (b) => opts.onFocus && b.x != null && d.bild ? ` class="ast-item focusable" tabindex="0" data-x="${b.x}" data-y="${b.y}" title="Im Bild zeigen"` : ' class="ast-item"';
    const q = `?t=${Math.round(d.erstellt || 0)}`;
    host.innerHTML = head('<span class="chip">JPL · Gaia</span>') + `
      <p class="ast-lead">${n ? `Du hast <b class="tnum" data-count>${n}</b> Asteroiden fotografiert${f ? `, der schwächste mit <b class="tnum">${astMag(f.mag)}</b>: ${esc(f.name)}` : ''}.`
        : items.length ? `Laut JPL lagen ${items.length} Kleinkörper im Feld – nachgewiesen ist keiner.` : 'Laut JPL lag in deinen Aufnahmenächten kein bekannter Kleinkörper im Bildfeld.'}</p>
      <div class="depth-kpis ast-kpis">
        <div><small>Nachgewiesen</small><b class="tnum" data-count>${nf.format(n)}</b><span>in deinen Einzelbildern</span></div>
        <div><small>Schwächster</small><b class="tnum">${f ? astMag(f.mag) : '–'}</b><span>${f ? esc(f.name) : 'keine Helligkeit gemessen'}</span></div>
        <div><small>Im Feld laut JPL</small><b class="tnum" data-count>${nf.format(d.im_feld || 0)}</b><span>${(d.naechte || []).length} ${(d.naechte || []).length === 1 ? 'Nacht' : 'Nächte'} abgeglichen</span></div>
        <div><small>Suchgrenze</small><b class="tnum">${astMag(d.grenze)}</b><span>Suchkreis ${de1(d.suchradius || 0, 0)}″ um die Bahn</span></div>
      </div>
      ${items.length ? `<ul class="ast-list">${items.map((b) => {
        const [lab, cls] = AST_STATUS[b.status] || [b.status, ''];
        return `<li${at(b)}>${b.bild ? `<img src="/api/stacking/results/${r.id}/asteroids/${esc(b.bild)}${q}" alt="Gemittelter Ausschnitt entlang der Bahn" loading="lazy" width="72" height="72">` : `<span class="ast-ph">${icon('orbit')}</span>`}
          <div><div><b>${esc(b.name)}</b>${b.art === 'Komet' ? ' <small class="mut">Komet</small>' : ''} <span class="chip ${cls}">${esc(lab)}</span></div>
          <div class="sm tnum">${b.mag != null ? `gemessen <b>${astMag(b.mag)}</b>${b.mag_err >= 0.05 ? ` ± ${de1(b.mag_err, 1)}` : ''} · ` : ''}JPL ${astMag(b.vmag)}${b.rate != null ? ` · ${de1(b.rate, 0)}″/h` : ''}</div>
          <div class="dim sm">${fnight(b.night)}${b.snr != null && b.status === 'nachgewiesen' ? ` · SNR ${de1(b.snr)} in ${b.aufnahmen} Aufnahmen · ${de1(b.abweichung)}″ neben der Vorhersage` : b.grund ? ` · ${esc(b.grund)}` : ''}</div></div></li>`;
      }).join('')}</ul>
        <p class="dim sm">Bild links: alle Aufnahmen entlang der vorhergesagten Bahn gemittelt (Stack abgezogen) – ein echter Asteroid erscheint als Punkt in der Mitte.</p>` : ''}
      <div class="row depth-acts">${d.bild && items.length ? `<button class="btn ghost sm${AST_MARKS.on ? ' on' : ''}" data-ast-marks aria-pressed="${AST_MARKS.on}">${icon('target')}Im Bild markieren</button>` : ''}
        <span class="grow"></span><button class="linkbtn sm" data-ast-run>Neu suchen</button></div>
      ${(d.hinweise || []).map(h => `<p class="dim sm depth-note">${esc(h)}</p>`).join('')}`;
    $('[data-ast-run]', host)?.addEventListener('click', run);
    const list = $('.ast-list', host);
    list?.addEventListener('click', (ev) => { const li = ev.target.closest('[data-x]'); if (li) opts.onFocus(+li.dataset.x, +li.dataset.y, d.bild.w); });
    list?.addEventListener('keydown', (ev) => { if (ev.key === 'Enter') ev.target.closest('[data-x]')?.click(); });
    $('[data-ast-marks]', host)?.addEventListener('click', (ev) => {
      AST_MARKS.on = !AST_MARKS.on; store.set('bild.asteroiden.marken', AST_MARKS.on);
      ev.currentTarget.classList.toggle('on', AST_MARKS.on); ev.currentTarget.setAttribute('aria-pressed', AST_MARKS.on);
      viewer?.el.classList.toggle('ast-on', AST_MARKS.on); viewer?.redraw();
    });
  }
  host.innerHTML = head() + '<div class="skel-line"></div>';
  poll(false);
}

// Overlay für createViewer: base (Objektnamen, Tiefenmarken) + Asteroiden: Bahnstrich der Nacht, Kreis an der Lage, Name.
function asteroidOverlay(rid, base) {
  return (svg, width) => {
    base?.(svg, width);
    svg.querySelector('.ast-marks')?.remove();
    const d = insightData.get(`asteroids:${rid}`);
    if (!AST_MARKS.on || !d?.bild) return;
    const sc = width / (d.bild.w || width), fs = Math.max(12, width / 60);
    const html = (d.kleinkoerper || []).filter(b => b.x != null).map((b) => {
      const ok = b.status === 'nachgewiesen', rr = fs * (ok ? 1.1 : 0.8);
      const line = b.bahn ? `<line x1="${b.bahn[0][0] * sc}" y1="${b.bahn[0][1] * sc}" x2="${b.bahn[1][0] * sc}" y2="${b.bahn[1][1] * sc}"/>` : '';
      const label = ok && b.mag != null ? `${b.name} · ${de1(b.mag)} mag` : b.name;
      return `<g class="am ${ok ? 'am-ok' : 'am-pred'}">${line}<circle cx="${b.x * sc}" cy="${b.y * sc}" r="${rr}"/>
        <text x="${b.x * sc + rr + fs * .3}" y="${b.y * sc + fs * .35}" font-size="${fs}">${esc(label)}</text><title>${esc(label)}</title></g>`;
    }).join('');
    svg.insertAdjacentHTML('beforeend', `<g class="ast-marks">${html}</g>`);
  };
}
