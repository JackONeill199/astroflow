'use strict';
// „Asteroiden in deinem Bild“ (seit 0.42.0, Wunsch 10) – Abschnitt im Reiter Analyse der Objektseite + Marken/Bahnstriche im Betrachter.
// Daten: /stacking/results/<id>/asteroids (eigener Endpunkt, Auswertung in insights.py → asteroids.py). Nutzt insightLoad/insightData
// und de1 aus depth.js.

// Marken im Bild: 'aus' | 'ok' (nur nachgewiesene, beschriftet) | 'alle' (dazu nicht nachgewiesene als kleine Punkte ohne Text).
// Seit 0.43.4 – vorher ein Knopf an/aus, der alle Körper beschriftete (M16: ~50 übereinanderliegende Namen). Alter Wert „aus“ bleibt aus.
const AST_MARKS = { mode: store.get('bild.asteroiden.markenart', store.get('bild.asteroiden.marken', true) ? 'ok' : 'aus') };
const AST_MODES = [['aus', 'Aus', 'Keine Asteroiden-Marken im Bild'], ['ok', 'Nachgewiesen', 'Nur in deinen Einzelbildern nachgewiesene Asteroiden markieren und beschriften'],
  ['alle', 'Alle', 'Zusätzlich alle laut JPL im Feld liegenden Kleinkörper als kleine Punkte (ohne Namen)']];
// Zeigt das Overlay in diesem Modus überhaupt etwas? (sonst blendet .ast-on die Objektnamen grundlos aus)
const astShows = (d) => !!d?.bild && (d.kleinkoerper || []).some(b => b.x != null && (AST_MARKS.mode === 'alle' || (AST_MARKS.mode === 'ok' && b.status === 'nachgewiesen')));
const AST_STATUS = {
  nachgewiesen: ['nachgewiesen', 'ok'], nicht_gefunden: ['nicht gefunden', ''], keine_rohbilder: ['im Feld, nicht nachgewiesen', ''],
  zu_langsam: ['zu langsam für den Nachweis', ''], zu_schwach: ['zu schwach für dein Bild', ''], nicht_geprueft: ['nicht geprüft', ''],
};
const AST_ROWS = 8;                    // Tabelle zeigt erst so viele Zeilen (wie die Veränderungen im Dashboard)
const astMag = (v) => v == null ? '–' : `${de1(v)} mag`;

// Abschnitt. opts.onFocus(x, y, w) zoomt im Betrachter oben.
function asteroidPanel(host, r, viewer, opts = {}) {
  if (!host) return;
  let timer = 0, astAll = false;
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
    viewer?.el.classList.toggle('ast-on', d.state === 'done' && astShows(d));
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
    const q = `?t=${Math.round(d.erstellt || 0)}`, nn = (d.naechte || []).length;
    const nAll = items.filter(b => b.x != null).length;
    const acts = `${d.bild && nAll ? `<span class="dim sm ast-seg-l">Im Bild</span><div class="seg ast-seg" role="group" aria-label="Asteroiden im Bild markieren">${AST_MODES.map(([k, l, t]) =>
        `<button type="button" data-ast-mode="${k}" class="${AST_MARKS.mode === k ? 'on' : ''}" aria-pressed="${AST_MARKS.mode === k}" title="${t}">${l}${k === 'alle' ? ` <span class="dim tnum">${nf.format(nAll)}</span>` : ''}</button>`).join('')}</div>` : ''}
      <button class="linkbtn sm" data-ast-run>Neu suchen</button>`;
    // dicht wie das Analyse-Dashboard: ein Satz, Kennzahlen als Leiste, Funde als Tabelle (erst AST_ROWS Zeilen)
    host.innerHTML = head(acts) + `
      <p class="ast-lead">${n ? `Du hast <b class="tnum">${n}</b> Asteroiden fotografiert${f ? `, der schwächste mit <b class="tnum">${astMag(f.mag)}</b>: ${esc(f.name)}` : ''}.`
        : items.length ? `Laut JPL lagen ${items.length} Kleinkörper im Feld – nachgewiesen ist keiner.` : 'Laut JPL lag in deinen Aufnahmenächten kein bekannter Kleinkörper im Bildfeld.'}
        <span class="dim">Bahnen von JPL (nur Himmelskoordinaten und Zeit), Nachweis entlang der Bahn in den Einzelbildern.</span></p>
      <div class="ad-kpis ast-kpis">
        <div class="ad-kpi"><small>Nachgewiesen</small><b class="tnum" data-count>${nf.format(n)}</b><small class="dim">in Einzelbildern</small></div>
        <div class="ad-kpi"><small>Schwächster</small><b class="tnum">${f ? astMag(f.mag) : '–'}</b><small class="dim">${f ? esc(f.name) : 'keine Helligkeit gemessen'}</small></div>
        <div class="ad-kpi"><small>Im Feld laut JPL</small><b class="tnum" data-count>${nf.format(d.im_feld || 0)}</b><small class="dim">${nn} ${nn === 1 ? 'Nacht' : 'Nächte'} abgeglichen</small></div>
        <div class="ad-kpi"><small>Suchgrenze</small><b class="tnum">${astMag(d.grenze)}</b><small class="dim">Kreis ${de1(d.suchradius || 0, 0)}″ um die Bahn</small></div>
      </div>
      ${items.length ? `<div class="ad-scroll"><table class="ad-tab ast-tab${astAll ? ' all' : ''}"><thead><tr><th></th><th>Kleinkörper</th><th>Status</th><th>gemessen</th><th>JPL</th><th>Bewegung</th><th>Nachweis</th></tr></thead><tbody>
        ${items.map((b) => {
          const [lab, cls] = AST_STATUS[b.status] || [b.status, ''], go = opts.onFocus && b.x != null && d.bild;
          return `<tr${go ? ` tabindex="0" data-x="${b.x}" data-y="${b.y}" title="Im Bild zeigen"` : ' class="nofocus"'}>
            <td class="ast-img">${b.bild ? `<img src="/api/stacking/results/${r.id}/asteroids/${esc(b.bild)}${q}" alt="Gemittelter Ausschnitt entlang der Bahn" loading="lazy" width="40" height="40">` : ''}</td>
            <td class="ad-name"><b>${esc(b.name)}</b>${b.art === 'Komet' ? ' <small class="mut">Komet</small>' : ''}<small class="mut"> · ${fnight(b.night)}</small><span class="chip ast-chip-m ${cls}">${esc(lab)}</span></td>
            <td><span class="chip ${cls}">${esc(lab)}</span></td>
            <td class="tnum">${b.mag != null ? `<b>${astMag(b.mag)}</b>${b.mag_err >= 0.05 ? ` ± ${de1(b.mag_err, 1)}` : ''}` : '–'}</td>
            <td class="tnum mut">${astMag(b.vmag)}</td><td class="tnum">${b.rate != null ? `${de1(b.rate, 0)}″/h` : '–'}</td>
            <td class="ast-why sm">${b.genau === false ? '<span title="Bahn nur genähert (JPL erster Durchgang) – größerer Suchkreis">Bahn genähert</span> · ' : ''}${b.status === 'nachgewiesen' && b.snr != null ? `SNR ${de1(b.snr)} · ${b.aufnahmen} Aufn. · ${de1(b.abweichung)}″ neben der Vorhersage` : esc(b.grund || '')}</td></tr>`;
        }).join('')}</tbody></table></div>
        ${items.length > AST_ROWS ? `<button class="linkbtn sm ad-more" data-ast-more>${astAll ? 'Weniger anzeigen' : `Alle ${nf.format(items.length)} anzeigen`}</button>` : ''}
        <p class="dim sm ast-note">Bildchen: alle Aufnahmen entlang der vorhergesagten Bahn gemittelt, Stack abgezogen – ein echter Asteroid erscheint als Punkt in der Mitte.</p>` : ''}
      ${(d.hinweise || []).map(h => `<p class="dim sm depth-note">${esc(h)}</p>`).join('')}`;
    $$('[data-ast-run]', host).forEach(b => b.addEventListener('click', run));
    const tab = $('.ast-tab', host);
    tab?.addEventListener('click', (ev) => { const tr = ev.target.closest('[data-x]'); if (tr) opts.onFocus(+tr.dataset.x, +tr.dataset.y, d.bild.w); });
    tab?.addEventListener('keydown', (ev) => { if (ev.key === 'Enter') ev.target.closest('[data-x]')?.click(); });
    $('[data-ast-more]', host)?.addEventListener('click', (ev) => {
      astAll = !astAll; tab.classList.toggle('all', astAll);
      ev.currentTarget.textContent = astAll ? 'Weniger anzeigen' : `Alle ${nf.format(items.length)} anzeigen`;
    });
    $('.ast-seg', host)?.addEventListener('click', (ev) => {
      const b = ev.target.closest('[data-ast-mode]'); if (!b) return;
      AST_MARKS.mode = b.dataset.astMode; store.set('bild.asteroiden.markenart', AST_MARKS.mode);
      $$('[data-ast-mode]', host).forEach(x => { const on = x === b; x.classList.toggle('on', on); x.setAttribute('aria-pressed', on); });
      viewer?.el.classList.toggle('ast-on', astShows(d)); viewer?.redraw();
    });
  }
  host.innerHTML = head() + '<div class="skel-line"></div>';
  poll(false);
}

// Overlay für createViewer: base (Objektnamen, Tiefenmarken) + Asteroiden. Nachgewiesene: Bahnstrich der Nacht, Kreis, Name –
// Namen überlappen nie (gierig vom hellsten an: rechts, sonst links, sonst ohne Text). Modus „Alle“: übrige nur als kleine
// gedämpfte Punkte ohne Text und ohne Bahn (bei M16 lagen ~50 Körper im Feld; Namen stehen in der Tabelle darunter).
function asteroidOverlay(rid, base) {
  return (svg, width) => {
    base?.(svg, width);
    svg.querySelector('.ast-marks')?.remove();
    const d = insightData.get(`asteroids:${rid}`);
    if (AST_MARKS.mode === 'aus' || !d?.bild) return;
    const sc = width / (d.bild.w || width), fs = Math.max(12, width / 60);
    const all = (d.kleinkoerper || []).filter(b => b.x != null);
    const ok = all.filter(b => b.status === 'nachgewiesen').sort((a, b) => (a.mag ?? 99) - (b.mag ?? 99));
    const rest = AST_MARKS.mode === 'alle' ? all.filter(b => b.status !== 'nachgewiesen') : [];
    const dots = rest.map(b => `<circle cx="${b.x * sc}" cy="${b.y * sc}" r="${fs * .32}"/>`).join('');
    const boxes = [], hit = (q) => boxes.some(o => q.x < o.x + o.w && o.x < q.x + q.w && q.y < o.y + o.h && o.y < q.y + q.h);
    for (const b of ok) boxes.push({ x: b.x * sc - fs * 1.1, y: b.y * sc - fs * 1.1, w: fs * 2.2, h: fs * 2.2 });   // Kreise freihalten
    const marks = ok.map((b) => {
      const x = b.x * sc, y = b.y * sc, rr = fs * 1.1;
      const line = b.bahn ? `<line x1="${b.bahn[0][0] * sc}" y1="${b.bahn[0][1] * sc}" x2="${b.bahn[1][0] * sc}" y2="${b.bahn[1][1] * sc}"/>` : '';
      const label = b.mag != null ? `${b.name} · ${de1(b.mag)} mag` : b.name, w = label.length * fs * 0.56, h = fs * 1.25;
      let text = '';
      for (const [tx, anchor] of [[x + rr + fs * .3, 'start'], [x - rr - fs * .3, 'end']]) {
        const box = { x: anchor === 'start' ? tx : tx - w, y: y - h * .6, w, h };
        if (box.x < 0 || box.x + w > width || hit(box)) continue;
        boxes.push(box);
        text = `<text x="${tx}" y="${y + fs * .35}" font-size="${fs}" text-anchor="${anchor}">${esc(label)}</text>`; break;
      }
      return `<g class="am am-ok">${line}<circle cx="${x}" cy="${y}" r="${rr}"/>${text}<title>${esc(label)}</title></g>`;
    }).join('');
    svg.insertAdjacentHTML('beforeend', `<g class="ast-marks">${dots ? `<g class="am am-pred">${dots}</g>` : ''}${marks}</g>`);
  };
}
