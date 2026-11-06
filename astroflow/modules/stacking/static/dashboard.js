'use strict';
/* Analyse-Dashboard über alle Objekte (#/analyse, seit 0.36.0). Daten aus GET /stacking/analysis (Aggregat mit Zwischenspeicher, eigener
   Endpunkt – nicht in der gepollten Projektansicht) und /analysis/universe (Punkte fürs „Universum“, erst beim Erreichen geladen).
   „Alles analysieren“ = POST /analysis/run; der Server arbeitet die Liste nacheinander ab (Netzauswertungen einzeln mit Pause).
   Animationen: Zahlen zählen hoch (anCountUp), Abschnitte blenden ein (anReveal aus analysis.js), Diagramme bauen sich auf – alles
   per CSS an `.an-sec.in`, aus bei prefers-reduced-motion. Jeder Abschnitt zeichnet nur neu, wenn sich seine Daten ändern. */
(() => {
const KINDS = [['depth', 'Tiefe', 'target'], ['compare', 'Vergleich', 'compare'], ['sky', 'Himmel', 'globe'], ['tutor', 'Tutor', 'sparkle']];
const KL = Object.fromEntries(KINDS.map(([k, l]) => [k, l]));
const ST = { done: 'berechnet', old: 'veraltet', none: 'noch nicht berechnet', error: 'Fehler', blocked: 'Voraussetzung fehlt', queued: 'wartet', running: 'läuft' };
const ART = { neu: 'neu', verschwunden: 'verschwunden', heller: 'heller', 'schwächer': 'schwächer' };
const de = (v, nd = 1) => v == null ? '–' : Number(v).toLocaleString('de-DE', { minimumFractionDigits: nd, maximumFractionDigits: nd });
const fday = s => s ? new Date(s + 'T12:00:00Z').toLocaleDateString('de-DE', { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC' }) : '–';
const lyT = (ly) => ly == null ? '–' : ly < 1e6 ? lyText(ly) : ly < 1e9 ? `${de(ly / 1e6, 1)} Mio. Lj` : `${de(ly / 1e9, 1)} Mrd. Lj`;
const hrs = (s) => s >= 3600 ? `${de(s / 3600, 1)} h` : `${Math.round(s / 60)} min`;
const reduced = () => matchMedia('(prefers-reduced-motion: reduce)').matches;
const load = (k, d) => { try { return JSON.parse(localStorage.getItem(k)) ?? d; } catch { return d; } };
const save = (k, v) => { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* privat */ } };

// Stelle im Bild: der Reiter Analyse liest das beim Öffnen und zoomt dorthin (analysisTab)
function goFocus(id, rid, x, y, w) {
  try { if (x != null && w) sessionStorage.setItem('af.an.focus', JSON.stringify({ rid, x, y, w })); } catch { /* egal */ }
  location.hash = `#/p/${id}/analyse`;
}

// ---- kleine Diagramm-Helfer (SVG, keine Fremdbibliothek) -------------------------------------------------
function axisTime(P, W, L, R) {
  const ts = P.map(p => Date.parse(p.night + 'T12:00:00Z') / 1000);
  const t0 = Math.min(...ts), t1 = Math.max(...ts, t0 + 86400 * 20);
  const pad = (t1 - t0) * 0.04;
  return { ts, X: t => L + (t - t0 + pad) / (t1 - t0 + 2 * pad) * (W - L - R), t0, t1 };
}

function scatter(P, { key, inv = false, fmt = (v) => de(v, 1), unit = '', href, hollow, label, line = false }) {
  P = P.filter(p => p[key] != null || (hollow && p[hollow] != null));
  if (!P.length) return '<p class="mut sm">Noch keine Messwerte.</p>';
  const W = matchMedia('(max-width: 760px)').matches ? 380 : 640, H = 200, L = 40, R = 10, T = 12, B = 24;   // Handy: Schrift bleibt lesbar
  const val = p => p[key] ?? p[hollow];
  const { ts, X } = axisTime(P, W, L, R);
  let lo = Math.min(...P.map(val)), hi = Math.max(...P.map(val));
  if (hi - lo < 1) { const m = (hi + lo) / 2; lo = m - 0.5; hi = m + 0.5; }
  const Y = v => T + (inv ? (v - lo) : (hi - v)) / (hi - lo) * (H - T - B);
  let g = '';
  for (const v of [lo, (lo + hi) / 2, hi]) g += `<line x1="${L}" x2="${W - R}" y1="${Y(v)}" y2="${Y(v)}" class="ad-grid"/><text x="${L - 6}" y="${Y(v) + 4}" text-anchor="end">${fmt(v)}</text>`;
  g += `<text x="${L}" y="${H - 6}">${fday(P[0].night)}</text><text x="${W - R}" y="${H - 6}" text-anchor="end">${fday(P.at(-1).night)}</text>`;
  if (line) {
    const Q = P.map((p, i) => [X(ts[i]), p[key]]).filter(q => q[1] != null).sort((a, b) => a[0] - b[0]);
    if (Q.length > 1) g += `<path class="ad-line" pathLength="1" d="${Q.map((q, i) => `${i ? 'L' : 'M'}${q[0].toFixed(1)} ${Y(q[1]).toFixed(1)}`).join('')}"/>`;
  }
  g += P.map((p, i) => {
    const own = p[key] != null, v = val(p);
    const c = `<circle cx="${X(ts[i]).toFixed(1)}" cy="${Y(v).toFixed(1)}" r="${own ? 4.5 : 3.5}" class="ad-pt${own ? '' : ' off'}" style="--i:${i}"><title>${esc(label(p))}: ${fmt(v)}${unit}${own ? '' : ' (gestört)'}</title></circle>`;
    return href ? `<a href="${href(p)}">${c}</a>` : c;
  }).join('');
  return `<svg class="ad-chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(key)} im Verlauf">${g}</svg>`;
}

function ring(cov, total) {
  const order = [['done', 'var(--ok)'], ['old', 'var(--warn)'], ['running', 'var(--acc)'], ['queued', 'color-mix(in srgb, var(--acc) 45%, transparent)'],
    ['error', 'var(--bad)'], ['blocked', 'var(--dim)']];
  const C = 2 * Math.PI * 34;
  let off = 0, segs = '';
  for (const [k, col] of order) {
    const n = cov[k] || 0;
    if (!n || !total) continue;
    const len = n / total * C;
    segs += `<circle r="34" cx="40" cy="40" class="ad-seg" style="stroke:${col};--len:${len.toFixed(2)};--off:${(-off).toFixed(2)};--C:${C.toFixed(2)}"/>`;
    off += len;
  }
  const pct = total ? Math.round((cov.done || 0) / total * 100) : 0;
  return `<svg class="ad-ring" viewBox="0 0 80 80"><circle r="34" cx="40" cy="40" class="ad-ring-bg"/>${segs}</svg><b class="tnum" data-count>${pct} %</b>`;
}

// ---- Ansicht ----------------------------------------------------------------------------------------------
async function analysisView(el) {
  crumbs([['Bilder', '#/bilder'], ['Analyse']]);
  let f = load('af.analysis.filter', { q: '', device: '', year: '', night: '' });
  let kinds = load('af.analysis.kinds', ['depth', 'compare', 'sky', 'tutor']), scope = 'missing';
  let data = null, ticket = 0, lastFull = 0, uniLoaded = '', sort = load('af.analysis.sort', { k: 'name', d: 1 });
  const sigs = {};
  el.innerHTML = `<div class="pagehead"><div><h1>Analyse</h1><p class="mut">Alle Objekte auf einen Blick: wie tief, wie dunkel, was sich verändert hat – nur gemessene Werte aus deinen Bildern.</p></div>
      <span class="grow"></span><div class="row"><a class="btn ghost sm" href="#/himmel">${icon('globe')}Himmelsqualität</a><button class="btn sm" id="adGo">${icon('play')}Alles analysieren</button></div></div>
    <div class="ad-filters" id="adFilters"><div class="fsearch ad-q">${icon('search')}<input id="adQ" type="search" placeholder="Objekt suchen" aria-label="Objekt suchen" value="${esc(f.q)}"></div>
      <select id="adDev" aria-label="Gerät"><option value="">Alle Geräte</option></select>
      <select id="adYear" aria-label="Jahr"><option value="">Alle Jahre</option></select>
      <select id="adNight" aria-label="Nacht"><option value="">Alle Nächte</option></select>
      <span class="grow"></span><span class="mut sm" id="adCount"></span><button class="linkbtn" id="adReset" hidden>Filter zurücksetzen</button></div>
    <section class="panel ad-run" id="adRun"></section>
    <div class="ad-kpis" id="adKpis">${'<div class="ad-kpi skel"></div>'.repeat(7)}</div>
    <div class="ad-grid2">
      <section class="panel an-sec" id="adCov"><span class="skel skel-card"></span></section>
      <section class="panel an-sec" id="adRec"><span class="skel skel-card"></span></section>
    </div>
    <section class="panel an-sec" id="adUni"><span class="skel skel-feat"></span></section>
    <div class="ad-grid2">
      <section class="panel an-sec" id="adDepthT"></section>
      <section class="panel an-sec" id="adSkyT"></section>
      <section class="panel an-sec" id="adRank"></section>
      <section class="panel an-sec" id="adNights"></section>
      <section class="panel an-sec" id="adScore"></section>
      <section class="panel an-sec" id="adFind"></section>
    </div>
    <section class="panel an-sec" id="adChanges"></section>
    <section class="panel an-sec" id="adCat"></section>
    <section class="panel an-sec" id="adTable"></section>`;
  $$('.an-sec', el).forEach(anReveal);
  const q = (s) => $(s, el);
  const qs = () => new URLSearchParams(Object.entries(f).filter(([, v]) => v)).toString();
  const setFilter = (k, v) => { f[k] = v; if (k === 'year') f.night = ''; save('af.analysis.filter', f); refresh(true); };
  q('#adQ').addEventListener('input', debounce((e) => setFilter('q', e.target.value.trim()), 250));
  q('#adDev').onchange = (e) => setFilter('device', e.target.value);
  q('#adYear').onchange = (e) => setFilter('year', e.target.value);
  q('#adNight').onchange = (e) => setFilter('night', e.target.value);
  q('#adReset').onclick = () => { f = { q: '', device: '', year: '', night: '' }; q('#adQ').value = ''; save('af.analysis.filter', f); refresh(true); };
  q('#adGo').onclick = () => { if (data?.job?.key && !['running', 'paused'].includes(data.job.state)) markSeen(data.job); q('#adRun').classList.add('open'); q('#adRun').scrollIntoView({ behavior: reduced() ? 'auto' : 'smooth', block: 'nearest' }); drawRun(true); };

  // Abschnitt nur bei geänderten Daten neu zeichnen (Animationen laufen sonst bei jedem Poll erneut)
  const paint = (id, payload, html) => {
    const s = JSON.stringify(payload);
    const host = q('#' + id);
    if (!host || sigs[id] === s) return host;
    sigs[id] = s;
    host.innerHTML = typeof html === 'function' ? html() : html;
    if (host.classList.contains('in')) anCountUp(host);
    return host;
  };

  function drawFilters(d) {
    const opt = (sel, list, cur, lab = (x) => x) => {
      const s = q(sel), first = s.options[0].outerHTML;
      s.innerHTML = first + list.map(x => `<option value="${esc(x)}"${x === cur ? ' selected' : ''}>${esc(lab(x))}</option>`).join('');
    };
    opt('#adDev', d.filters.devices, f.device);
    opt('#adYear', d.filters.years, f.year);
    opt('#adNight', d.filters.nights.filter(n => !f.year || n.startsWith(f.year)), f.night, fday);
    q('#adDev').hidden = d.filters.devices.length < 2 && !f.device;
    q('#adCount').textContent = `${nf.format(d.totals.objects)} Objekte${d.filtered ? ' (gefiltert)' : ''}`;
    q('#adReset').hidden = !d.filtered;
  }

  // ---- Sammelauftrag ------------------------------------------------------------------------------------
  let planSig = '', planOut = null;
  const ids = () => data?.filtered ? data.rows.map(r => r.id) : null;
  async function plan() {
    const body = { kinds, scope, ids: ids() }, s = JSON.stringify(body);
    if (s === planSig && planOut) return planOut;
    planSig = s;
    try { planOut = await send('/stacking/analysis/plan', body); } catch { planOut = null; }
    return planOut;
  }
  async function drawRun(force = false) {
    const host = q('#adRun'), j = data?.job || {};
    if (!host || !data) return;
    const busy = j.state === 'running' || j.state === 'paused';
    const ended = (j.state === 'done' || j.state === 'stopped') && j.key !== seenJob;     // Ergebnis bleibt sichtbar, bis es geschlossen wird
    host.classList.toggle('active', busy);
    host.hidden = !busy && !ended && !host.classList.contains('open');
    q('#adGo').disabled = busy;
    if (host.hidden) return;
    if (busy || ended) {
      const pk = Object.entries(j.per_kind || {});
      const s = JSON.stringify([j.state, j.done, j.item, j.errors?.length, j.detail]);
      if (!force && host.dataset.sig === s) return;
      host.dataset.sig = s;
      const fin = !busy;
      host.innerHTML = `<div class="panel-h"><h2>${fin ? (j.state === 'stopped' ? 'Analyse abgebrochen' : 'Analyse fertig') : j.state === 'paused' ? 'Analyse angehalten' : 'Analyse läuft'}</h2>
          <span class="grow"></span>${busy ? `<button class="btn ghost sm" data-pause>${icon(j.state === 'paused' ? 'play' : 'pause')}${j.state === 'paused' ? 'Fortsetzen' : 'Anhalten'}</button>
          <button class="btn ghost sm" data-stop>${icon('stop')}Abbrechen</button>` : `<button class="btn ghost sm" data-new>${icon('refresh')}Neue Auswahl</button><button class="btn ghost sm icon" data-close aria-label="Schließen">${icon('x')}</button>`}</div>
        <div class="ad-prog"><div class="progress"><i style="width:${(j.done / Math.max(1, j.total) * 100).toFixed(1)}%"></i></div>
          <span class="tnum">${nf.format(j.done)} / ${nf.format(j.total)}</span></div>
        ${busy && j.item ? `<p class="sm ad-now">${j.state === 'running' ? '<span class="spinner"></span>' : ''}<b>${esc(j.item.label)}</b> · <a href="#/p/${j.item.id}/analyse">${esc(j.item.name)}</a> <span class="mut">${esc(j.detail || '')}</span></p>` : ''}
        <div class="ad-kinds">${pk.map(([k, v]) => `<div><small>${esc(KL[k])}</small><div class="progress"><i style="width:${(v.done / Math.max(1, v.total) * 100).toFixed(0)}%"></i></div><small class="tnum mut">${v.done}/${v.total}</small></div>`).join('')}</div>
        ${Object.keys(j.skipped || {}).length ? `<p class="mut sm">Übersprungen (Voraussetzung fehlt): ${Object.entries(j.skipped).map(([c, n]) => `${n}× ${esc(BLOCK_TEXT[c] || c)}`).join(', ')}</p>` : ''}
        ${j.errors?.length ? `<details class="ad-errs"><summary class="sm">${j.errors.length} Auswertungen ohne Ergebnis</summary><ul class="sm">${j.errors.slice(0, 30).map(e => `<li><a href="#/p/${e.id}/analyse">${esc(e.name)}</a> · ${esc(e.label)}: <span class="mut">${esc(e.error)}</span></li>`).join('')}</ul></details>` : ''}
        <p class="dim sm">Online-Abfragen (Gaia, SIMBAD, DSS, JPL) laufen nacheinander mit kurzer Pause – nur Himmelskoordinaten verlassen den Rechner.</p>`;
      $('[data-pause]', host)?.addEventListener('click', async () => { await send('/stacking/analysis/pause', { paused: j.state !== 'paused' }).catch(e => toast(e.message, 'err')); refresh(); });
      $('[data-stop]', host)?.addEventListener('click', async () => { await send('/stacking/analysis/stop').catch(e => toast(e.message, 'err')); refresh(); });
      $('[data-new]', host)?.addEventListener('click', () => { markSeen(j); host.classList.add('open'); drawRun(true); });
      $('[data-close]', host)?.addEventListener('click', () => { markSeen(j); host.classList.remove('open'); host.hidden = true; });
      return;
    }
    // Auswahl
    host.dataset.sig = '';
    const p = await plan();
    if (!host.isConnected || data.job?.state === 'running') return;
    const sv = data.insights || {};
    host.innerHTML = `<div class="panel-h"><h2>Alles analysieren</h2><span class="grow"></span><button class="btn ghost sm icon" data-close aria-label="Schließen">${icon('x')}</button></div>
      <div class="ad-pick">${KINDS.map(([k, l, ic]) => `<label class="ad-kind${kinds.includes(k) ? ' on' : ''}"><input type="checkbox" value="${k}"${kinds.includes(k) ? ' checked' : ''}>${icon(ic)}<span>${l}</span>
        <small class="tnum">${p?.per_kind?.[k] != null ? nf.format(p.per_kind[k]) : '–'}</small></label>`).join('')}</div>
      <div class="row ad-scope"><div class="seg" role="group" aria-label="Umfang">${[['missing', 'Fehlende und veraltete'], ['errors', '+ fehlgeschlagene'], ['all', 'Alles neu']].map(([v, l]) => `<button data-scope="${v}"${scope === v ? ' class="on"' : ''}>${l}</button>`).join('')}</div>
        <span class="grow"></span><span class="sm mut">${p ? `${nf.format(p.total)} Auswertungen${data.filtered ? ` für ${nf.format(data.rows.length)} gefilterte Objekte` : ''}` : ''}${p && Object.keys(p.skipped).length ? ` · ${Object.values(p.skipped).reduce((a, b) => a + b, 0)} übersprungen (Voraussetzung fehlt)` : ''}</span>
        <button class="btn sm" data-start${p?.total ? '' : ' disabled'}>${icon('play')}Starten</button></div>
      <p class="dim sm">Online: Gaia ${sv.gaia ? 'an' : 'aus'} · SIMBAD ${sv.simbad ? 'an' : 'aus'} · DSS ${sv.survey ? 'an' : 'aus'} · JPL ${sv.small_bodies ? 'an' : 'aus'} – nacheinander, nur Koordinaten werden gesendet.
        Reihenfolge: Tutor und Himmel (lokal) zuerst, dann Tiefe und Vergleich. Fortschritt auch unter Aktivität.</p>`;
    $$('.ad-kind input', host).forEach(i => i.onchange = () => {
      kinds = $$('.ad-kind input', host).filter(x => x.checked).map(x => x.value); save('af.analysis.kinds', kinds); drawRun(true);
    });
    $$('[data-scope]', host).forEach(b => b.onclick = () => { scope = b.dataset.scope; drawRun(true); });
    $('[data-close]', host).onclick = () => { host.classList.remove('open'); host.hidden = true; };
    $('[data-start]', host).onclick = async () => {
      try { data.job = await send('/stacking/analysis/run', { kinds, scope, ids: ids() }); toast('Analyse gestartet – Fortschritt hier und unter Aktivität.', 'ok'); }
      catch (e) { toast(e.message, 'err'); }
      planSig = ''; refresh(true);
    };
  }
  let seenJob = load('af.analysis.seen', '');
  const markSeen = (j) => { seenJob = j.key || ''; save('af.analysis.seen', seenJob); };
  const BLOCK_TEXT = { nosolve: 'nicht gelöst', nogaia: 'Gaia aus', noframes: 'ohne Einzelbild-Analysen', noquality: 'ohne Bildprüfung' };

  // ---- Abschnitte -----------------------------------------------------------------------------------------
  function drawKpis(d) {
    const t = d.totals, r = d.records;
    const k = (label, v, sub, href) => `<a class="ad-kpi" ${href ? `href="${href}"` : 'tabindex="-1"'}><small>${label}</small><b class="tnum" data-count>${v}</b><small class="dim">${sub}</small></a>`;
    paint('adKpis', [t, r.deepest, r.far_gal, r.far_star, r.darkest], () => [
      k('Objekte', nf.format(t.objects), t.imported ? `+ ${nf.format(t.imported)} fertige Bilder` : `${nf.format(t.analysed)} mit Tiefenmessung`, null),
      k('Nächte', nf.format(t.nights), `${nf.format(t.frames)} Aufnahmen gestackt`, '#/bilder/statistik'),
      k('Belichtung', hrs(t.exposure), 'in den fertigen Bildern', null),
      k('Tiefste Grenzgröße', r.deepest ? `${de(r.deepest.value, 1)} mag` : '–', r.deepest ? esc(r.deepest.name) : 'noch nicht gemessen', r.deepest ? `#/p/${r.deepest.id}/analyse` : ''),
      k('Am weitesten', r.far_gal ? lyT(r.far_gal.value) : r.far_star ? lyT(r.far_star.value) : '–', r.far_gal ? `${esc(r.far_gal.gal.name)} · z ${de(r.far_gal.gal.z, 3)}` : r.far_star ? 'fernster Stern' : 'noch nicht gemessen',
        r.far_gal ? `#/p/${r.far_gal.id}/analyse` : r.far_star ? `#/p/${r.far_star.id}/analyse` : ''),
      k('Dunkelster Himmel', r.darkest ? `${de(r.darkest.value, 2)}` : '–', r.darkest ? `mag/″² · ${fday(r.darkest.night)}` : 'noch nicht berechnet', r.darkest ? `#/p/${r.darkest.id}/analyse` : ''),
      k('Veränderungen', nf.format(t.changes), d.change_counts.variables || d.change_counts.small ? `${nf.format(d.change_counts.variables || 0)} Veränderliche · ${nf.format(d.change_counts.small || 0)} Kleinplaneten` : 'Kandidaten im Vergleich', null),
    ].map((h, i) => h.replace('class="ad-kpi"', `class="ad-kpi" style="--i:${i}"`)).join(''));
    if (!reduced()) anCountUp(q('#adKpis'));
  }

  function drawCoverage(d) {
    paint('adCov', [d.coverage, d.blocked, d.solver, d.gaia], () => {
      const tot = d.totals.objects;
      const fix = (b) => {
        if (b.code === 'nosolve') return d.solver ? `<button class="btn ghost sm" data-develop="${b.ids.join(',')}">${icon('refresh')}Neu entwickeln – löst den Stack</button>`
          : `<a class="btn ghost sm" href="#/settings/stacking">${icon('star')}Plate-Solver einrichten</a>`;
        if (b.code === 'nogaia') return `<button class="btn ghost sm" data-gaia>${icon('globe')}Gaia-Abgleich einschalten</button>`;
        return '';
      };
      return `<div class="panel-h"><h2>Was schon berechnet ist</h2></div>
        <div class="ad-rings">${KINDS.map(([k, l, ic]) => {
          const c = d.coverage[k] || {};
          return `<div class="ad-ringbox" title="${Object.entries(c).map(([s, n]) => `${n} ${ST[s] || s}`).join(' · ')}">${ring(c, tot)}<span>${icon(ic)}${l}</span>
            <small class="mut">${[['done', 'fertig'], ['old', 'veraltet'], ['none', 'offen'], ['error', 'Fehler'], ['blocked', 'blockiert']].filter(([s]) => c[s]).map(([s, w]) => `${c[s]} ${w}`).join(' · ') || '–'}</small></div>`;
        }).join('')}</div>
        ${d.blocked.length ? `<h3 class="ad-h3">Voraussetzungen fehlen</h3><ul class="ad-blocked">${d.blocked.map(b => `<li><div><b>${b.ids.length} ${b.ids.length === 1 ? 'Objekt' : 'Objekte'}</b> ${esc(b.text)}
            <small class="mut">– betrifft ${b.kinds.map(k => KL[k]).join(', ')}</small>
            <div class="sm ad-ids">${b.ids.slice(0, 8).map(i => { const r = d.rows.find(x => x.id === i); return r ? `<a href="#/p/${i}/analyse">${esc(r.name)}</a>` : ''; }).join(', ')}${b.ids.length > 8 ? ` und ${b.ids.length - 8} weitere` : ''}</div></div>${fix(b)}</li>`).join('')}</ul>
            <p class="dim sm">Die Plate-Solving-Lösung entsteht beim Entwickeln (mit eingerichtetem ASTAP). „Neu entwickeln“ rechnet nur aus dem gespeicherten Stack – Rohbilder sind nicht nötig, kostet aber je Objekt etwas Rechenzeit.</p>`
          : '<p class="mut sm ad-allok">Für alle Objekte sind die Voraussetzungen erfüllt.</p>'}`;
    });
    const host = q('#adCov');
    $$('[data-develop]', host).forEach(b => b.onclick = async () => {
      const list = b.dataset.develop.split(',').map(Number);
      if (!await confirmDlg('Objekte neu entwickeln?', `${list.length} Objekte werden aus ihrem gespeicherten Stack neu entwickelt und dabei per Plate-Solving gelöst. Danach misst AstroFlow die Tiefe automatisch.`, 'Neu entwickeln')) return;
      try { const r = await send('/stacking/analysis/develop', { ids: list }); toast(`${r.queued} Objekte zum Entwickeln eingereiht.`, 'ok'); } catch (e) { toast(e.message, 'err'); }
    });
    $('[data-gaia]', host)?.addEventListener('click', async () => {
      if (!await confirmDlg('Gaia-Abgleich einschalten?', 'Für Farbkalibrierung, Grenzgröße und Himmelshelligkeit fragt AstroFlow den Gaia-Sternkatalog ab. Dabei werden nur Himmelskoordinaten gesendet.', 'Einschalten')) return;
      try { await send('/stacking/storage', { gaia: true }); toast('Gaia-Abgleich eingeschaltet.', 'ok'); planSig = ''; refresh(true); } catch (e) { toast(e.message, 'err'); }
    });
  }

  function drawRecords(d) {
    const r = d.records;
    const item = (ic, label, v, sub, x) => x ? `<li><button class="ad-rec" data-id="${x.id}" data-rid="${x.rid || ''}" ${x.pos ? `data-x="${x.pos.x}" data-y="${x.pos.y}" data-w="${x.pos.w}"` : ''}>
        <span class="ad-rec-i">${icon(ic)}</span><span class="ad-rec-t"><small>${label}</small><b class="tnum">${v}</b><small class="mut">${sub}</small></span>${icon('arrow')}</button></li>` : '';
    paint('adRec', r, () => {
      const list = [
        item('target', 'Tiefstes Bild', r.deepest && `${de(r.deepest.value, 1)} mag`, r.deepest && `${esc(r.deepest.name)} · Grenzgröße 5σ`, r.deepest),
        item('orbit', 'Fernste Galaxie', r.far_gal && lyT(r.far_gal.value), r.far_gal && `${esc(r.far_gal.gal.name)} in ${esc(r.far_gal.name)} · z ${de(r.far_gal.gal.z, 3)}`,
          r.far_gal && { ...r.far_gal, pos: r.far_gal.bild && r.far_gal.gal.x != null ? { x: r.far_gal.gal.x, y: r.far_gal.gal.y, w: r.far_gal.bild.w } : null }),
        item('star', 'Fernster Stern', r.far_star && lyT(r.far_star.value), r.far_star && `in ${esc(r.far_star.name)} · G ${de(r.far_star.star.g, 1)} (Gaia-Parallaxe)`,
          r.far_star && { ...r.far_star, pos: r.far_star.bild && r.far_star.star.x != null ? { x: r.far_star.star.x, y: r.far_star.star.y, w: r.far_star.bild.w } : null }),
        item('layers', 'Meiste Galaxien', r.most_gal && nf.format(r.most_gal.value), r.most_gal && esc(r.most_gal.name), r.most_gal),
        item('pulse', 'Am effizientesten', r.efficient && `${de(r.efficient.value, 2)} mag`, r.efficient && `${esc(r.efficient.name)} · Grenzgröße auf 1 h umgerechnet`, r.efficient),
        item('globe', 'Dunkelster Himmel', r.darkest && `${de(r.darkest.value, 2)} mag/″²`, r.darkest && `${fday(r.darkest.night)} · ${esc(r.darkest.name)}`, r.darkest),
        item('sparkle', 'Schärfste Nacht', r.seeing && `${de(r.seeing.value, 1)}″`, r.seeing && `${fday(r.seeing.night)} · ${esc(r.seeing.name)}`, r.seeing),
        item('medal', 'Beste Bildqualität', r.score && `${de(r.score.value, 1)}/10`, r.score && esc(r.score.name), r.score),
        item('camera', 'Längste Belichtung', r.exposure && hrs(r.exposure.value), r.exposure && esc(r.exposure.name), r.exposure),
      ].join('');
      return `<div class="panel-h"><h2>Rekorde</h2></div>${list ? `<ul class="ad-recs">${list}</ul>` : '<p class="mut">Noch keine Messwerte – starte „Alles analysieren“.</p>'}`;
    });
    $$('.ad-rec', q('#adRec')).forEach(b => b.onclick = () => goFocus(+b.dataset.id, +b.dataset.rid, b.dataset.x != null ? +b.dataset.x : null, +b.dataset.y, +b.dataset.w));
  }

  // Universum: logarithmische Entfernungsskala aller Bilder (fernster Stern, Hauptobjekt, fernste Galaxie), 3D-Wolke auf Knopfdruck
  let cloud = null;
  function drawUniverse(d) {
    const items = [];
    for (const r of d.rows) {
      const dp = r.depth;
      if (!dp) continue;
      if (dp.far_star?.ly) items.push({ t: 's', ly: dp.far_star.ly, r, label: `fernster Stern in ${r.name}`, pos: dp.far_star });
      if (dp.main?.ly) items.push({ t: 'm', ly: dp.main.ly, r, label: dp.main.name || r.name, pos: null });
      if (dp.far_gal?.ly) items.push({ t: 'g', ly: dp.far_gal.ly, r, label: `${dp.far_gal.name} in ${r.name}`, pos: dp.far_gal });
    }
    const lo = 0, hi = 10.5;                      // 1 Lj … 30 Mrd. Lj
    const X = ly => Math.max(0, Math.min(100, (Math.log10(Math.max(1, ly)) - lo) / (hi - lo) * 100));
    const marks = [[4.2, 'Nächster Stern'], [2.6e4, 'Zentrum der Milchstraße'], [2.5e6, 'Andromeda'], [6e7, 'Virgo-Haufen'], [1.38e10, 'Rand des sichtbaren Universums']];
    paint('adUni', [items.map(i => [i.t, i.ly, i.r.id]), d.totals.stars, d.totals.galaxies], () => `<div class="panel-h"><div><h2>Dein Universum</h2>
        <p class="mut sm">Wie weit deine Bilder reichen – Entfernungen logarithmisch: jeder Strich ist zehnmal so weit wie der vorige. ${nf.format(d.totals.stars)} erfasste Sterne, ${nf.format(d.totals.galaxies)} Galaxien.</p></div>
        <span class="grow"></span><button class="btn ghost sm" id="adCloudBtn"${items.length ? '' : ' disabled'}>${icon('orbit')}3D-Wolke</button></div>
      ${items.length ? `<div class="ad-uni"><div class="ad-uni-axis">${Array.from({ length: 11 }, (_, i) => `<i style="left:${X(10 ** i)}%"><small>${i === 0 ? '1 Lj' : i < 3 ? nf.format(10 ** i) + ' Lj' : i < 6 ? nf.format(10 ** (i - 3)) + '.000 Lj' : i < 9 ? nf.format(10 ** (i - 6)) + ' Mio.' : nf.format(10 ** (i - 9)) + ' Mrd.'}</small></i>`).join('')}</div>
          ${marks.map(([v, l], i) => `<span class="ad-uni-mark${i === marks.length - 1 ? ' end' : ''}" style="left:${X(v)}%"><small>${l}</small></span>`).join('')}
          <span class="ad-uni-earth" title="Erde">${icon('globe')}</span>
          ${items.sort((a, b) => a.ly - b.ly).map((i, n) => `<button class="ad-u t-${i.t}" style="--x:${X(i.ly).toFixed(2)}%;--row:${n % 4};--i:${n}" data-id="${i.r.id}" data-rid="${i.r.rid}"
            ${i.pos?.x != null && i.r.depth.bild ? `data-x="${i.pos.x}" data-y="${i.pos.y}" data-w="${i.r.depth.bild.w}"` : ''} title="${esc(i.label)}: ${lyT(i.ly)}"><i></i></button>`).join('')}</div>
          <div class="ad-uni-leg sm"><span><i class="lg s"></i>fernster Stern je Bild (Gaia)</span><span><i class="lg m"></i>Hauptobjekt</span><span><i class="lg g"></i>fernste Galaxie je Bild (SIMBAD)</span></div>
          <div class="ad-cloud" id="adCloud" hidden></div>`
        : '<p class="mut">Noch keine Tiefenmessungen – „Alles analysieren“ mit „Tiefe“ füllt diese Skala.</p>'}`);
    const host = q('#adUni');
    $$('.ad-u', host).forEach(b => b.onclick = () => goFocus(+b.dataset.id, +b.dataset.rid, b.dataset.x != null ? +b.dataset.x : null, +b.dataset.y, +b.dataset.w));
    const btn = q('#adCloudBtn');
    if (btn) btn.onclick = () => toggleCloud(btn);
  }

  async function toggleCloud(btn) {
    const box = q('#adCloud');
    if (cloud) { cloud.close(); cloud = null; box.hidden = true; btn.classList.remove('on'); return; }
    box.hidden = false; btn.classList.add('on');
    box.innerHTML = '<div class="ad-cloud-load"><span class="spinner"></span>Punkte und 3D-Bibliothek werden geladen …</div>';
    try {
      const [u, lib] = await Promise.all([api('/stacking/analysis/universe?' + qs()), loadThree()]);
      if (!box.isConnected) return;
      cloud = universe3d(box, u, lib);
    } catch (e) { box.innerHTML = `<p class="mut">3D-Ansicht nicht verfügbar: ${esc(e.message)}</p>`; }
  }

  function drawTimes(d) {
    paint('adDepthT', d.depth_series, () => `<div class="panel-h"><div><h2>Grenzgröße über die Zeit</h2><p class="mut sm">Schwächste sicher erkannte Sterne (5σ) je Bild, nach Aufnahmenacht. Oben = tiefer.</p></div></div>
      ${scatter(d.depth_series, { key: 'm5', fmt: v => de(v, 1), unit: ' mag', href: p => `#/p/${p.id}/analyse`, label: p => `${p.name} · ${fday(p.night)} · ${de(p.hours, 1)} h` })}`);
    paint('adSkyT', d.sky_series, () => `<div class="panel-h"><div><h2>Himmel über die Zeit</h2><p class="mut sm">Himmelshelligkeit je Nacht (Median der Objekte). Oben = dunkler; helle Punkte = Mond, Dämmerung oder Dunst.</p></div><a class="linkbtn sm" href="#/himmel">Standorte</a></div>
      ${scatter(d.sky_series, { key: 'sqm', hollow: 'sqm_all', fmt: v => de(v, 1), unit: ' mag/″²', line: true, href: p => `#/p/${p.objects[0].id}/analyse`, label: p => `${fday(p.night)} · ${p.objects.map(o => o.name).join(', ')}` })}
      ${d.sky_series.some(n => n.seeing != null) ? `<small class="mut">Bildschärfe (FWHM) – unten = schärfer</small>${scatter(d.sky_series, { key: 'seeing', inv: false, fmt: v => de(v, 1), unit: '″', href: p => `#/p/${p.objects[0].id}/analyse`, label: p => fday(p.night) }).replace('class="ad-chart"', 'class="ad-chart ad-chart-s"')}` : ''}`);
  }

  function drawRank(d) {
    paint('adRank', d.ranking, () => {
      const R = d.ranking, mx = Math.max(...R.map(x => x.m5_1h), 0), mn = Math.min(...R.map(x => x.m5_1h), mx) - 0.5;
      return `<div class="panel-h"><div><h2>Was am meisten gebracht hat</h2><p class="mut sm">Grenzgröße auf 1 Stunde Belichtung umgerechnet (Rauschen sinkt mit √Zeit) – vergleicht Nächte, Orte und Ziele fair.</p></div></div>
        ${R.length ? `<ol class="ad-bars">${R.map((x, i) => `<li style="--i:${i}"><a href="#/p/${x.id}/analyse"><span class="ad-bl">${esc(x.name)}</span>
          <span class="ad-bt"><i style="--w:${((x.m5_1h - mn) / Math.max(0.1, mx - mn) * 100).toFixed(1)}%"></i></span><b class="tnum">${de(x.m5_1h, 2)}</b>
          <small class="mut tnum">${de(x.m5, 1)} mag in ${de(x.hours, 1)} h</small></a></li>`).join('')}</ol>` : '<p class="mut">Noch keine Tiefenmessungen.</p>'}`;
    });
  }

  function drawNights(d) {
    paint('adNights', d.best_nights, () => `<div class="panel-h"><h2>Beste Nächte</h2></div>
      ${d.best_nights.length ? `<ol class="ad-nights">${d.best_nights.map((n, i) => {
        const b = d.rows.find(r => r.id === n.objects[0].id)?.sky?.bortle;
        return `<li style="--i:${i}"><span class="ad-medal">${i + 1}</span><div><b>${fday(n.night)}</b><small class="mut">${n.objects.map(o => `<a href="#/p/${o.id}/analyse">${esc(o.name)}</a>`).join(', ')}</small></div>
          <span class="tnum"><b>${de(n.sqm, 2)}</b> <small class="mut">mag/″²</small>${n.seeing != null ? `<small class="mut"> · ${de(n.seeing, 1)}″</small>` : ''}</span>${b ? `<span class="chip sq-b${b.class}">${esc(b.text)}</span>` : ''}</li>`;
      }).join('')}</ol>` : '<p class="mut">Noch keine ungestörte Nacht mit Ortswert (dunkel, ohne Mond, über 30°).</p>'}`);
  }

  function drawScore(d) {
    paint('adScore', d.score_hist, () => {
      const H = d.score_hist, mx = Math.max(1, ...H), n = H.reduce((a, b) => a + b, 0);
      return `<div class="panel-h"><div><h2>Bildqualität</h2><p class="mut sm">Bewertung der automatischen Bildprüfung (0–10) über ${nf.format(n)} Bilder.</p></div></div>
        ${n ? `<div class="ad-hist">${H.map((v, i) => `<div title="${v} Bilder mit ${i}/10"><i style="--h:${(v / mx * 100).toFixed(1)}%;--i:${i}" class="${i >= 8 ? 'ok' : i >= 5 ? 'mid' : 'low'}"></i><small>${i}</small></div>`).join('')}</div>` : '<p class="mut">Keine Bildprüfungen gespeichert.</p>'}`;
    });
  }

  function drawFindings(d) {
    paint('adFind', d.findings, () => {
      const F = d.findings, mx = Math.max(1, ...F.map(x => x.n));
      return `<div class="panel-h"><div><h2>Häufigste Tutor-Befunde</h2><p class="mut sm">Was deine Bilder am häufigsten gestört hat – aus Nachtbedingungen und Bildprüfung.</p></div></div>
        ${F.length ? `<ul class="ad-find">${F.slice(0, 8).map((x, i) => `<li style="--i:${i}"><details><summary><span class="ad-fl"><i class="lv ${x.level}"></i>${esc(x.title)}</span>
          <span class="ad-bt"><i style="--w:${(x.n / mx * 100).toFixed(1)}%" class="${x.level}"></i></span><b class="tnum">${x.n}</b></summary>
          <div class="sm ad-ids">${x.ids.map(id => { const r = d.rows.find(y => y.id === id); return r ? `<a href="#/p/${id}/analyse">${esc(r.name)}</a>` : ''; }).join(', ')}</div></details></li>`).join('')}</ul>`
          : `<p class="mut">${(d.coverage.tutor?.done || 0) ? 'Keine Störungen gefunden.' : 'Noch keine Tutor-Auswertungen – „Alles analysieren“ mit „Tutor“.'}</p>`}`;
    });
  }

  function drawChanges(d) {
    paint('adChanges', [d.changes, d.change_counts], () => {
      const C = d.change_counts;
      const what = (c) => c.kleinkoerper ? `<span class="chip acc">${icon('orbit')}Kleinplanet</span> ${esc(c.kleinkoerper.name)}`
        : c.katalog ? `<span class="chip ${c.katalog.veraenderlich ? 'warn' : ''}">${esc(c.katalog.veraenderlich ? 'Veränderlich' : 'Katalog')}</span> ${esc(c.katalog.name)} <small class="mut">${esc(c.katalog.art || '')}</small>`
        : '<span class="mut">nicht im Katalog</span>';
      return `<div class="panel-h"><div><h2>Veränderungen und Entdeckungen</h2><p class="mut sm">Unterschiede gegen eine Referenz (eigene ältere Aufnahme oder Himmelsdurchmusterung DSS), mit Kataloge abgeglichen. Kandidaten – keine Bestätigungen.</p></div></div>
        <div class="ad-chips">${['neu', 'verschwunden', 'heller', 'schwächer'].filter(a => C[a]).map(a => `<span class="chip"><b class="tnum">${C[a]}</b>&nbsp;${ART[a]}</span>`).join('')}
          ${C.variables ? `<span class="chip warn"><b class="tnum">${C.variables}</b>&nbsp;Veränderliche</span>` : ''}${C.small ? `<span class="chip acc"><b class="tnum">${C.small}</b>&nbsp;Kleinplaneten</span>` : ''}</div>
        ${d.changes.length ? `<div class="ad-scroll"><table class="ad-tab ad-chg"><thead><tr><th>Objekt</th><th>Art</th><th>Stärke</th><th>Erkannt als</th></tr></thead><tbody>
          ${d.changes.map((c, i) => `<tr tabindex="0" data-id="${c.id}" data-rid="${c.rid}" ${c.x != null && c.bild ? `data-x="${c.x}" data-y="${c.y}" data-w="${c.bild.w}"` : ''}>
            <td class="ad-name">${esc(c.name)}</td><td><span class="chip">${esc(ART[c.art] || c.art)}</span></td><td class="tnum">${de(c.sigma, 0)}σ${c.dmag != null ? ` · ${c.dmag > 0 ? '+' : ''}${de(c.dmag, 1)} mag` : ''}</td><td class="ad-what">${what(c)}</td></tr>`).join('')}
          </tbody></table></div>` : `<p class="mut">${(d.coverage.compare?.done || 0) ? 'Keine Unterschiede gefunden.' : 'Noch keine Vergleiche – „Alles analysieren“ mit „Vergleich“.'}</p>`}`;
    });
    $$('.ad-chg tr[data-id]', q('#adChanges')).forEach(tr => {
      const go = () => goFocus(+tr.dataset.id, +tr.dataset.rid, tr.dataset.x != null ? +tr.dataset.x : null, +tr.dataset.y, +tr.dataset.w);
      tr.onclick = go; tr.onkeydown = (e) => { if (e.key === 'Enter') go(); };
    });
  }

  function drawCatalogs(d) {
    paint('adCat', d.catalogs, () => {
      const c = d.catalogs, grid = (pre, n, have, skip = []) => `<div class="ad-cat">${Array.from({ length: n }, (_, i) => {
        const k = i + 1, own = have.includes(k), id = c.links[pre + k];
        return own ? `<a class="on" href="#/p/${id}/analyse" style="--i:${have.indexOf(k)}" title="${pre === 'M' ? 'M' : 'C '}${k}">${k}</a>` : `<span class="${skip.includes(k) ? 'na' : ''}" title="${pre === 'M' ? 'M' : 'C '}${k}">${k}</span>`;
      }).join('')}</div>`;
      return `<div class="panel-h"><h2>Kataloge</h2><span class="grow"></span><a class="linkbtn sm" href="#/nacht">Was heute Nacht geht</a></div>
        <div class="ad-cats"><div><h3 class="ad-h3">Messier <b class="tnum" data-count>${c.messier.length}</b><span class="mut"> von 110</span></h3>${grid('M', 110, c.messier, [24, 40, 73])}</div>
        <div><h3 class="ad-h3">Caldwell <b class="tnum" data-count>${c.caldwell.length}</b><span class="mut"> von 109</span></h3>${grid('C', 109, c.caldwell)}</div></div>`;
    });
  }

  function drawTable(d) {
    const dot = (r, k) => {
      const s = r.blocked?.[k] && !['done', 'old'].includes(r.status[k]) ? 'blocked' : r.status[k];
      return `<i class="ad-dot s-${s}" title="${KL[k]}: ${ST[s] || s}${r.blocked?.[k] ? ' – ' + esc(BLOCK_TEXT[r.blocked[k]] || '') : ''}"></i>`;
    };
    const val = {
      name: r => r.name.toLowerCase(), night: r => r.nights.at(-1) || '', exposure: r => r.exposure, score: r => r.score ?? -1,
      m5: r => r.depth?.m5 ?? -1, sqm: r => r.sky?.sqm ?? -1, gal: r => r.depth?.far_gal?.z ?? -1,
    };
    const rows = d.rows.slice().sort((a, b) => { const x = val[sort.k](a), y = val[sort.k](b); return (x > y ? 1 : x < y ? -1 : 0) * sort.d; });
    const th = (k, l) => `<th data-sort="${k}" class="${sort.k === k ? (sort.d > 0 ? 'asc' : 'desc') : ''}">${l}</th>`;
    paint('adTable', [rows.map(r => [r.id, r.status, r.depth?.m5, r.sky?.sqm, r.score]), sort], () => `<div class="panel-h"><h2>Alle Objekte</h2><span class="grow"></span>
        <span class="ad-legend sm">${['done', 'old', 'none', 'error', 'blocked'].map(s => `<span><i class="ad-dot s-${s}"></i>${ST[s]}</span>`).join('')}</span></div>
      <div class="ad-scroll"><table class="ad-tab ad-all"><thead><tr>${th('name', 'Objekt')}${th('night', 'Letzte Nacht')}${th('exposure', 'Belichtung')}${th('score', 'Qualität')}${th('m5', 'Grenzgröße')}${th('gal', 'Fernste Galaxie')}${th('sqm', 'Himmel')}<th>Tiefe · Vergl. · Himmel · Tutor</th></tr></thead><tbody>
        ${rows.map(r => `<tr data-href="#/p/${r.id}/analyse" tabindex="0"><td class="ad-name"><a href="#/p/${r.id}/analyse">${esc(r.name)}</a><small class="mut">${esc(r.device || '')}</small></td>
          <td>${fday(r.nights.at(-1))}${r.nights.length > 1 ? ` <small class="mut">+${r.nights.length - 1}</small>` : ''}</td><td class="tnum">${hrs(r.exposure)}</td>
          <td class="tnum">${r.score != null ? de(r.score, 1) : '–'}</td><td class="tnum">${r.depth?.m5 != null ? de(r.depth.m5, 1) + ' mag' : '–'}</td>
          <td class="tnum">${r.depth?.far_gal ? lyT(r.depth.far_gal.ly) : '–'}</td><td class="tnum">${r.sky?.sqm != null ? de(r.sky.sqm, 2) : '–'}</td>
          <td class="ad-dots">${KINDS.map(([k]) => dot(r, k)).join('')}</td></tr>`).join('')}
      </tbody></table></div>`);
    const host = q('#adTable');
    $$('th[data-sort]', host).forEach(t => t.onclick = () => { sort = { k: t.dataset.sort, d: sort.k === t.dataset.sort ? -sort.d : (t.dataset.sort === 'name' ? 1 : -1) }; save('af.analysis.sort', sort); drawTable(data); });
    $$('tr[data-href]', host).forEach(tr => { tr.onclick = (e) => { if (!e.target.closest('a')) location.hash = tr.dataset.href; }; tr.onkeydown = (e) => { if (e.key === 'Enter') location.hash = tr.dataset.href; }; });
  }

  function draw(d) {
    drawFilters(d);
    if (!d.totals.objects && !d.filtered) {
      el.querySelectorAll('.ad-grid2, .an-sec, #adKpis').forEach(x => x.remove());
      if (!q('#adEmpty')) el.insertAdjacentHTML('beforeend', `<div class="empty" id="adEmpty">${icon('target')}<h2>Noch keine fertigen Bilder</h2><p>Sobald Aufnahmen gestackt sind, erscheint hier die Auswertung über alle Objekte.</p><a class="btn sm" href="#/import">${icon('upload')}Aufnahmen importieren</a></div>`);
      q('#adFilters').hidden = true; q('#adGo').disabled = true;
      return;
    }
    drawKpis(d); drawCoverage(d); drawRecords(d); drawUniverse(d); drawTimes(d); drawRank(d); drawNights(d); drawScore(d); drawFindings(d); drawChanges(d); drawCatalogs(d); drawTable(d);
    drawRun();
  }

  async function refresh(force = false) {
    const cur = ++ticket;
    try {
      const d = await api('/stacking/analysis?' + qs());
      if (cur !== ticket || !el.isConnected) return;
      const filtersChanged = data && JSON.stringify([data.filtered, data.rows.map(r => r.id)]) !== JSON.stringify([d.filtered, d.rows.map(r => r.id)]);
      data = d; lastFull = Date.now();
      if (filtersChanged) { planSig = ''; if (cloud) { cloud.close(); cloud = null; const b = q('#adCloud'); if (b) b.hidden = true; q('#adCloudBtn')?.classList.remove('on'); } }
      draw(d);
    } catch (e) {
      if (cur === ticket && el.isConnected && !data) $('#adKpis', el).outerHTML = `<div class="panel">${esc(e.message)}</div>`;
    }
    void force;
  }
  await refresh();
  return {
    // Poll der Shell (2,5 s): während eines Auftrags laufend, sonst nur gelegentlich (der Server-Zwischenspeicher hält das billig)
    update: () => { const busy = ['running', 'paused'].includes(data?.job?.state); if (busy || Date.now() - lastFull > 15000) refresh(); },
    destroy: () => { ticket++; cloud?.close(); cloud = null; },
  };
}

function debounce(fn, ms) { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; }

// ---- 3D-Wolke: Blick von der Erde (Mitte) in die echten Himmelsrichtungen, Abstand log10(Lichtjahre) ----------------------
function universe3d(box, u, { THREE, OrbitControls }) {
  box.innerHTML = `<div class="ad-cloud-stage"></div><div class="ad-cloud-info sm" hidden></div>
    <div class="ad-cloud-leg sm"><span><i class="lg s"></i>Stern (Gaia-Parallaxe)</span><span><i class="lg g"></i>Galaxie (Rotverschiebung)</span><span class="mut">Abstand logarithmisch · ziehen dreht · Mausrad zoomt · Klick zeigt Namen</span></div>`;
  const stage = $('.ad-cloud-stage', box), info = $('.ad-cloud-info', box);
  const renderer = new THREE.WebGLRenderer({ antialias: true });
  renderer.setPixelRatio(Math.min(2, devicePixelRatio));
  stage.append(renderer.domElement);
  const scene = new THREE.Scene();
  scene.background = new THREE.Color(0x06070a);
  const cam = new THREE.PerspectiveCamera(55, 1, 0.01, 200);
  cam.position.set(0, 9, 22);
  const ctl = new OrbitControls(cam, renderer.domElement);
  ctl.enableDamping = true; ctl.autoRotate = !reduced(); ctl.autoRotateSpeed = 0.35;
  const P = u.points, R = (ly) => Math.max(0.3, Math.log10(Math.max(1, ly)));     // 10 Lj → 1, 10 Mrd. Lj → 10
  const pos = new Float32Array(P.length * 3), col = new Float32Array(P.length * 3), target = new Float32Array(P.length * 3);
  P.forEach((p, i) => {
    const a = p.ra * Math.PI / 180, b = p.dec * Math.PI / 180, r = R(p.ly);
    target.set([r * Math.cos(b) * Math.cos(a), r * Math.sin(b), -r * Math.cos(b) * Math.sin(a)], i * 3);
    const c = p.t === 'g' ? [0.76, 0.6, 1] : starColor(p.c);
    col.set(c, i * 3);
  });
  const geo = new THREE.BufferGeometry();
  geo.setAttribute('position', new THREE.BufferAttribute(pos, 3));
  geo.setAttribute('color', new THREE.BufferAttribute(col, 3));
  const mat = new THREE.PointsMaterial({ size: 0.16, vertexColors: true, transparent: true, opacity: 0.95, depthWrite: false });
  const pts = new THREE.Points(geo, mat);
  scene.add(pts);
  // Entfernungsringe 10 … 10¹⁰ Lj in der Äquatorebene
  for (let k = 1; k <= 10; k += (k < 4 ? 1 : 3)) {
    const ring = new THREE.LineLoop(new THREE.BufferGeometry().setFromPoints(Array.from({ length: 96 }, (_, i) => new THREE.Vector3(k * Math.cos(i / 96 * 2 * Math.PI), 0, k * Math.sin(i / 96 * 2 * Math.PI)))),
      new THREE.LineBasicMaterial({ color: 0x2a3446, transparent: true, opacity: 0.6 }));
    scene.add(ring);
  }
  scene.add(new THREE.Mesh(new THREE.SphereGeometry(0.12, 16, 12), new THREE.MeshBasicMaterial({ color: 0x4c9aff })));
  // Einflug: Punkte wachsen von der Erde in ihre Entfernung (sofort am Ziel bei reduzierter Bewegung)
  const t0 = performance.now(), D = reduced() ? 0 : 1800;
  let alive = true, raf = 0;
  const ro = new ResizeObserver(() => { const w = stage.clientWidth, h = stage.clientHeight; renderer.setSize(w, h, false); cam.aspect = w / Math.max(1, h); cam.updateProjectionMatrix(); });
  ro.observe(stage);
  const ray = new THREE.Raycaster(); ray.params.Points.threshold = 0.25;
  renderer.domElement.addEventListener('click', (e) => {
    const b = renderer.domElement.getBoundingClientRect();
    ray.setFromCamera(new THREE.Vector2((e.clientX - b.left) / b.width * 2 - 1, -(e.clientY - b.top) / b.height * 2 + 1), cam);
    const hit = ray.intersectObject(pts)[0];
    if (!hit) { info.hidden = true; return; }
    const p = P[hit.index];
    info.hidden = false;
    info.innerHTML = `<b>${esc(p.t === 'g' ? p.n || 'Galaxie' : 'Stern' + (p.g != null ? ` · G ${de(p.g, 1)}` : ''))}</b> · ${lyT(p.ly)}${p.z != null ? ` · z ${de(p.z, 3)}` : ''}<br><a href="#/p/${p.p}/analyse">${esc(u.names[p.p] || 'Objekt')}</a>`;
  });
  const loop = (now) => {
    if (!alive) return;
    const k = D ? Math.min(1, (now - t0) / D) : 1, e = 1 - (1 - k) ** 3;
    if (k <= 1 && (k < 1 || pos[0] !== target[0])) { for (let i = 0; i < pos.length; i++) pos[i] = target[i] * e; geo.attributes.position.needsUpdate = true; }
    ctl.update();
    renderer.render(scene, cam);
    raf = requestAnimationFrame(loop);
  };
  raf = requestAnimationFrame(loop);
  return { close: () => { alive = false; cancelAnimationFrame(raf); ro.disconnect(); ctl.dispose(); geo.dispose(); mat.dispose(); renderer.dispose(); box.innerHTML = ''; } };
}

const m = AF.modules.stacking;
m.nav.push({ href: '#/analyse', label: 'Analyse', icon: 'target', key: 'analysis' });
m.routes.push([/^\/analyse$/, 'analysis', analysisView]);
m.palette = ((orig) => async function () { return [['target', 'Analyse aller Objekte', 'Kennzahlen, Rekorde, alles analysieren', '#/analyse'], ...await orig.call(this)]; })(m.palette);
})();
