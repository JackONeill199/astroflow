'use strict';
/* Observatorium, Planer #/observatorium/plan: Assistent (Nächte, Geräte, Wunsch, Gründlichkeit, Merkliste) → Plan über
   mehrere Nächte (/api/stacking/plan, planner.py), Zeitleiste je Nacht und Gerät, Fortschritt je Ziel. Der gespeicherte Plan
   erscheint zusätzlich im Atlas (Ringe) und in „Heute Nacht“ (AFplan.nightSection). */
(() => {
const NIGHTS = [1, 3, 5, 7, 14], MODES = [['new', 'Neues'], ['mix', 'Mischung'], ['deep', 'Vertiefen']];
const DEPTHS = [['short', 'Kurz'], ['normal', 'Normal'], ['thorough', 'Gründlich']];
// alte gespeicherte Wünsche (feste Mindestbelichtung) → Gründlichkeit, wie planner.clean_wishes
const depthOf = (w) => w.depth || (w.min_hours == null ? 'normal' : w.min_hours <= 1 ? 'short' : w.min_hours >= 3 ? 'thorough' : 'normal');
const de = (v, n = 1) => Number(v).toFixed(n).replace('.', ',');
const hm = (t) => new Date(t * 1000).toLocaleTimeString('de-DE', { hour: '2-digit', minute: '2-digit' });
const dayName = (n) => new Date(n + 'T12:00:00Z').toLocaleDateString('de-DE', { weekday: 'short', day: 'numeric', month: 'numeric', timeZone: 'UTC' });
const hours = (h) => h >= 1 ? `${de(h, h % 1 ? 1 : 0)} h` : `${Math.round(h * 60)} min`;
// stabile Farbe je Ziel (Farbton aus der Kennung) – Datenfarbe, kein UI-Token
const hue = (id) => { let x = 0; for (const c of String(id)) x = (x * 31 + c.charCodeAt(0)) % 360; return x; };
const color = (id) => `hsl(${hue(id)} 62% 62%)`;
const tabs = (cur) => `<nav class="seg obs-tabs" aria-label="Observatorium"><a href="#/observatorium" class="${cur === 'atlas' ? 'on' : ''}">${icon('globe')}Atlas</a><a href="#/observatorium/plan" class="${cur === 'plan' ? 'on' : ''}">${icon('list')}Planung</a></nav>`;

// Zeitleiste: Zeilen = Nächte, je Gerät eine Spur; x = Ortszeit von der frühesten Dämmerung bis zur spätesten
function timeline(plan, width = 760) {
  const ns = plan.nights, devs = plan.devices;
  const spans = ns.flatMap(n => n.dark.map(([a, b]) => [a - n.dark[0][0], b - n.dark[0][0]]));
  if (!spans.length) return '<p class="mut sm">In diesem Zeitraum wird es nicht astronomisch dunkel.</p>';
  // Bezug: Uhrzeit des ersten Abends; alle Nächte auf dieselbe Achse (Sekunden ab 18 Uhr Ortszeit des Browsers)
  const base = (n) => { const d = new Date(n.dark[0]?.[0] * 1000 || Date.parse(n.night + 'T18:00:00')); d.setHours(17, 0, 0, 0); return d.getTime() / 1000; };
  let x0 = Infinity, x1 = -Infinity;
  ns.forEach(n => { const b = base(n); n.dark.forEach(([a, e]) => { x0 = Math.min(x0, a - b); x1 = Math.max(x1, e - b); }); });
  x0 = Math.floor(x0 / 3600) * 3600; x1 = Math.ceil(x1 / 3600) * 3600;
  // Breite = echte Breite des Panels (Schrift bleibt lesbar); schmal: kürzere Ränder, Wetter nur als Zahl
  const W = Math.max(300, Math.round(width)), narrow = W < 560;
  const lane = devs.length > 1 ? 13 : 20, rowH = lane * devs.length + 12, L = narrow ? 52 : 76, R = narrow ? 40 : 78, T = 20, H = T + ns.length * rowH + 4;
  const X = (s) => L + (s - x0) / (x1 - x0) * (W - L - R);
  let g = '';
  for (let s = x0; s <= x1; s += 3600) {
    const hh = (17 + s / 3600) % 24;
    if (hh % 2 === 0 || x1 - x0 <= 8 * 3600) g += `<line class="pl-grid" x1="${X(s)}" x2="${X(s)}" y1="${T - 4}" y2="${H}"/><text class="pl-ax" x="${X(s)}" y="12" text-anchor="middle">${String(Math.floor(hh)).padStart(2, '0')}</text>`;
  }
  ns.forEach((n, i) => {
    const y = T + i * rowH, b = base(n);
    g += `<text class="pl-day" x="0" y="${y + rowH / 2 + 2}">${esc(narrow ? dayName(n.night).replace(/\.,? .*$/, '') + ' ' + n.night.slice(8, 10) + '.' : dayName(n.night))}</text>`;
    n.dark.forEach(([a, e]) => { g += `<rect class="pl-dark" x="${X(a - b)}" y="${y + 2}" width="${Math.max(1, X(e - b) - X(a - b))}" height="${rowH - 8}" rx="4"/>`; });
    const w = n.weather === 'none' ? `<tspan class="pl-nowx">${narrow ? '–' : 'ohne Wetter'}</tspan><title>ohne Wettervorhersage</title>` : n.cloud != null ? (narrow ? `${n.cloud} %` : `${n.cloud} % ☁`) : '';
    g += `<text class="pl-side" x="${W - R + 6}" y="${y + rowH / 2 + 2}">${w}</text>`;
    if (!n.blocks.length) g += `<text class="pl-empty" x="${(X(x0) + X(x1)) / 2}" y="${y + rowH / 2 + 2}" text-anchor="middle">${n.cloud >= 60 ? 'bewölkt – frei gelassen' : 'nichts geplant'}</text>`;
    n.blocks.forEach((bl, k) => {
      const di = Math.max(0, devs.findIndex(d => d.id === bl.device)), by = y + 4 + di * lane, bx = X(bl.start - b), bw = Math.max(3, X(bl.end - b) - bx);
      const t = plan.targets.find(x => x.id === bl.target) || { name: bl.target };
      g += `<g class="pl-block" style="--n:${i}" tabindex="0" role="button" data-n="${i}" data-k="${k}" aria-label="${esc(t.name)}, ${hm(bl.start)} bis ${hm(bl.end)}">
        <rect x="${bx.toFixed(1)}" y="${by}" width="${bw.toFixed(1)}" height="${lane - 2}" rx="3" style="fill:${color(bl.target)}"/>
        ${bw > 34 ? `<text x="${(bx + 4).toFixed(1)}" y="${by + lane / 2 + 2}">${esc(short(t.name, bw))}</text>` : ''}<title>${esc(t.name)} · ${hm(bl.start)}–${hm(bl.end)} · ${hours(bl.hours)}</title></g>`;
    });
  });
  return `<svg class="pl-tl" viewBox="0 0 ${W} ${H}" role="img" aria-label="Plan je Nacht">${g}</svg>
    ${devs.length > 1 ? `<p class="mut sm pl-lanes">Je Nacht oben ${esc(devs[0].name)}, unten ${esc(devs[1].name)}.</p>` : ''}`;
}
const short = (name, px) => { const n = String(name).replace(/^(C \d+) \((NGC|IC) \d+\) /, '$1 '), max = Math.max(3, Math.floor(px / 6.2)); return n.length > max ? n.slice(0, max - 1) + '…' : n; };

// „~30 min“, „~5 h über 3 Nächte“, „~8 h über 2 Nächte · noch ~2 Nächte“ (nach dem Plan)
function needText(t) {
  const n = t.nights || 0, more = t.more_nights;
  return `~${hours(t.goal)}${t.have ? ` (bisher ${hours(t.have)})` : ''}${n > 1 ? ` über ${n} Nächte` : ''}${!t.done && more ? ` · noch ~${more} ${more === 1 ? 'Nacht' : 'Nächte'}` : ''}`;
}

function progress(plan) {
  if (!plan.targets.length) return '<p class="mut sm">Kein Ziel bekommt in diesem Zeitraum Zeit – mehr Nächte wählen oder Horizont/Wetter prüfen.</p>';
  const row = (t) => {
    const nights = plan.nights.filter(n => n.blocks.some(b => b.target === t.id)).map(n => dayName(n.night).split(',')[0]);
    const have = t.have || 0, tip = [t.why ? `Geschätzter Bedarf ~${hours(t.goal)}: ${t.why}` : '', ...(t.assumed || []), have ? `bisher ${hours(have)} belichtet` : '', `Nächte im Plan: ${nights.join(', ')}`].filter(Boolean).join(' · ');
    return `<li><i style="background:${color(t.id)}"></i><span class="pl-name">${t.href ? `<a href="${t.href}">${esc(t.name)}</a>` : esc(t.name)}${t.watch ? ' <span class="chip acc">Merkliste</span>' : ''}</span>
      <span class="pl-bar" title="${hours(have + t.hours)} von ~${hours(t.goal)}"><b style="width:${Math.min(100, (have + t.hours) / t.goal * 100).toFixed(0)}%;background:${color(t.id)}"></b></span>
      <span class="pl-h tnum">${hours(t.hours)}${t.done ? ' ✓' : ''}</span><span class="pl-when dim sm" title="${esc(tip)}">${esc(t.why ? needText(t) : nights.join(', '))}</span></li>`;
  };
  const first = plan.targets.slice(0, 10), rest = plan.targets.slice(10);    // lange Listen: Rest aufklappbar
  return `<ul class="pl-prog">${first.map(row).join('')}</ul>${rest.length ? `<details class="pl-more"><summary class="sm">Alle ${plan.targets.length} Ziele</summary><ul class="pl-prog">${rest.map(row).join('')}</ul></details>` : ''}`;
}

async function planView(el) {
  crumbs([['Übersicht', '#/'], ['Observatorium', '#/observatorium'], ['Planung']]);
  el.innerHTML = `<div class="pagehead"><div><h1>Observatorium</h1><p class="mut">Welche Objekte in den nächsten Nächten – berechnet aus Sichtbarkeit, Horizont, Mond und Wetter.</p></div>${tabs('plan')}</div>
    <div id="plBody"><div class="skel" style="height:14rem"></div></div>`;
  let d;
  try { d = await api('/stacking/plan'); } catch (e) { if (el.isConnected) $('#plBody', el).innerHTML = `<div class="panel">${esc(e.message)}</div>`; return; }
  if (!el.isConnected) return;
  let catalog = [];
  const w = { nights: 3, devices: [d.default_device], mode: 'mix', watch: [], eq: d.eq_default || [], ...(d.plan?.wishes || {}) };
  w.depth = depthOf(w); delete w.min_hours;
  w.devices = w.devices.filter(id => d.devices.some(o => o.id === id));
  if (!w.devices.length) w.devices = [d.devices[0].id];
  let wizard = !d.plan;

  const chips = (key, list, multi) => list.map(([v, l]) => {
    const on = multi ? w[key].includes(v) : w[key] === v;
    return `<button type="button" class="pl-opt${on ? ' on' : ''}" data-k="${key}" data-v="${esc(String(v))}" aria-pressed="${on}">${esc(l)}</button>`;
  }).join('');
  function wizardHtml() {
    return `<section class="panel pl-wiz"><div class="panel-h"><h2>${icon('sparkle')}Plan erstellen</h2><span class="grow"></span>${d.plan ? '<button type="button" class="linkbtn" data-act="close">Abbrechen</button>' : ''}</div>
      ${d.has_site ? '' : `<div class="notice">${icon('alert')}<div>Für die Planung fehlt ein Standort. <a href="#/settings/standorte">Standort eintragen</a></div></div>`}
      <ol class="pl-steps">
        <li><b>Wie viele Nächte?</b><div class="pl-opts">${chips('nights', NIGHTS.map(n => [n, String(n)]))}</div></li>
        <li><b>Welche Geräte?</b> <span class="dim sm">bis zu zwei gleichzeitig</span><div class="pl-opts">${chips('devices', d.devices.map(o => [o.id, o.name]), true)}</div><div class="pl-eq" id="plEq">${eqHtml()}</div></li>
        <li><b>Was willst du?</b><div class="pl-opts">${chips('mode', MODES)}</div></li>
        <li><b>Wie gründlich?</b> <span class="dim sm">Belichtung je Ziel geschätzt aus Objekt, Gerät und Himmel</span><div class="pl-opts">${chips('depth', DEPTHS)}</div></li>
        <li><b>Merkliste</b> <span class="dim sm">kommt bevorzugt in den Plan</span>
          <div class="pl-opts pl-watch">${w.watch.map(id => `<button type="button" class="pl-opt on" data-unwatch="${esc(id)}" title="Entfernen">${esc(nameOf(id))} ×</button>`).join('')}
            <label class="pl-add">${icon('plus')}<input id="plWatch" list="plCat" placeholder="Objekt hinzufügen" aria-label="Objekt zur Merkliste hinzufügen" autocomplete="off"></label>
            <datalist id="plCat">${catalog.map(o => `<option value="${esc(o.name)}">`).join('')}</datalist></div></li>
      </ol>
      <div class="row pl-go"><span class="mut sm">${d.weather_on ? 'Wetter: Open-Meteo, soweit die Vorhersage reicht (~8 Tage).' : 'Wettervorhersage ist aus – geplant wird nur nach Sichtbarkeit.'}</span><span class="grow"></span>
        <button type="button" class="btn" data-act="run" ${d.has_site ? '' : 'disabled'}>${icon('play')}Plan berechnen</button></div></section>`;
  }
  // EQ-Modus je gewähltem Gerät, das ihn kann (Seestar, Dwarf, eigene Kamera): dann keine Bildfelddrehung in der Rangfolge
  function eqHtml() {
    return d.devices.filter(o => o.eq_mode && w.devices.includes(o.id)).map(o => `<label class="modrow"><div><b>${esc(o.name)} im EQ-Modus</b>
      <div class="mut sm">${(d.eq_detected || []).includes(o.id) ? 'In deinen letzten Aufnahmen erkannt. ' : ''}Mit Polhöhenwiege/Keil dreht sich das Bildfeld nicht – hohe Ziele zählen dann voll.</div></div>
      <input type="checkbox" data-eq="${esc(o.id)}" ${w.eq.includes(o.id) ? 'checked' : ''}><span class="switch"></span></label>`).join('');
  }
  const nameOf = (id) => catalog.find(o => o.id === id)?.name || id;
  function planHtml(p) {
    const total = p.nights.reduce((s, n) => s + n.blocks.reduce((t, b) => t + b.hours, 0), 0);
    const fc = p.nights.filter(n => n.weather !== 'none').length;
    return `<section class="panel pl-plan"><div class="panel-h"><h2>${icon('list')}${p.nights.length} ${p.nights.length === 1 ? 'Nacht' : 'Nächte'} · ${p.devices.map(x => esc(x.name) + (x.eq ? ' (EQ)' : '')).join(' + ')}</h2><span class="grow"></span>
        <a class="btn ghost sm" href="#/observatorium?blick=hier" title="Bahnen der Ziele über deinem Horizont im Atlas">${icon('orbit')}Bahnen</a><button type="button" class="btn ghost sm" data-act="ics" title="Als Kalenderdatei (.ics) speichern">${icon('down')}Kalender</button><button type="button" class="btn sm" data-act="edit">${icon('refresh')}Neu planen</button><button type="button" class="btn ghost sm icon" data-act="del" title="Plan löschen" aria-label="Plan löschen">${icon('trash')}</button></div>
      <div class="pl-sum"><span><b class="tnum">${p.targets.length}</b> Ziele</span><span><b class="tnum">${hours(total)}</b> geplant</span>
        <span><b class="tnum">${p.targets.filter(t => t.done).length}</b> ${p.wishes.min_hours != null && !p.wishes.depth ? `erreichen ${hours(p.wishes.min_hours)}` : 'erreichen ihren Bedarf'}</span>
        ${p.wishes.depth ? `<span class="dim" title="${esc([p.sky ? '' : 'Ohne Messwert für den Himmel ist ein Vorortshimmel angenommen.', ...p.devices.map(x => x.calibration ? `${x.name}: ${x.calibration}` : '')].filter(Boolean).join(' · ') || 'Bedarf je Ziel aus Objektart, Helligkeit, Gerät und Himmel')}">${esc((DEPTHS.find(x => x[0] === p.wishes.depth) || ['', ''])[1])} · ${p.sky ? `Himmel SQM ${de(p.sky.sqm)}` : 'Himmel geschätzt'}</span>` : ''}
        <span class="dim">${p.weather_on ? (fc ? `Wetter für ${fc} ${fc === 1 ? 'Nacht' : 'Nächte'}` : 'keine Vorhersage') : 'ohne Wetter'}${p.site?.horizon ? ' · mit Horizont' : ''}</span></div>
      ${p.watch_missing?.length ? `<p class="sm warn">Nicht unterzubringen: ${p.watch_missing.map(esc).join(', ')}</p>` : ''}
      <div id="plTl">${timeline(p, $('#plBody', el)?.clientWidth - 32 || 760)}</div></section>
      <section class="panel pl-goals"><h2>Ziele</h2>${progress(p)}<p class="dim sm pl-foot">Berechnet ${new Date(p.created * 1000).toLocaleString('de-DE', { dateStyle: 'short', timeStyle: 'short' })} · Wetter und Fortschritt ändern sich – bei Bedarf „Neu planen“.</p></section>`;
  }
  // Neu zeichnen; `fresh` = Abschnitte, die neu erscheinen (Selektor) – nur die blenden ein, Zeitleiste wächst dann von links
  const still = () => matchMedia('(prefers-reduced-motion: reduce)').matches;
  function draw(fresh = null) {
    if (!el.isConnected) return;
    const b = $('#plBody', el);
    b.innerHTML = (wizard ? wizardHtml() : '') + (d.plan ? planHtml(d.plan) : '');
    if (!fresh || still()) return;
    $$(fresh, b).forEach((x, i) => { x.classList.add('pl-in'); x.style.setProperty('--i', i); });
    const tl = $('.pl-in #plTl', b);
    if (tl) { tl.classList.add('pl-grow'); setTimeout(() => tl.classList.remove('pl-grow'), 1200); }
  }
  // Abschnitt kurz ausblenden, dann umbauen
  const leave = (sel, then) => { const x = $(sel, el); if (!x || still()) return then(); x.classList.add('pl-out'); setTimeout(() => { if (el.isConnected) then(); }, 160); };
  draw('#plBody > section');
  // Zeitleiste bei geänderter Breite neu zeichnen (nur die Grafik)
  let lastW = 0;
  const ro = new ResizeObserver(() => { const tl = $('#plTl', el), wd = $('#plBody', el)?.clientWidth || 0;
    if (tl && d.plan && Math.abs(wd - lastW) > 8) { lastW = wd; tl.innerHTML = timeline(d.plan, tl.clientWidth || wd - 32); } });
  ro.observe($('#plBody', el));
  api('/stacking/atlas').then(a => { catalog = (a.catalog || []).filter(o => o.src !== 'a'); if (wizard) draw(); }).catch(() => {});

  const body = $('#plBody', el);
  body.addEventListener('click', async (e) => {
    const o = e.target.closest('.pl-opt[data-k]');
    if (o) {
      const str = k => k === 'mode' || k === 'devices' || k === 'depth';
      const k = o.dataset.k, v = str(k) ? o.dataset.v : +o.dataset.v;
      if (k === 'devices') w.devices = w.devices.includes(v) ? (w.devices.length > 1 ? w.devices.filter(x => x !== v) : w.devices) : [...w.devices, v].slice(-2);
      else w[k] = v;
      $$(`.pl-opt[data-k="${k}"]`, body).forEach(x => {
        const xv = str(k) ? x.dataset.v : +x.dataset.v, on = k === 'devices' ? w.devices.includes(xv) : w[k] === xv;
        x.classList.toggle('on', on); x.setAttribute('aria-pressed', on);
      });
      if (k === 'devices') { w.eq = w.eq.filter(x => w.devices.includes(x)); const q = $('#plEq', body); if (q) q.innerHTML = eqHtml(); }
      return;
    }
    const u = e.target.closest('[data-unwatch]');
    if (u) { w.watch = w.watch.filter(x => x !== u.dataset.unwatch); draw(); return; }
    const blk = e.target.closest('.pl-block');
    if (blk) { showBlock(+blk.dataset.n, +blk.dataset.k); return; }
    const a = e.target.closest('[data-act]')?.dataset.act;
    if (a === 'edit') { wizard = true; draw('.pl-wiz'); $('.pl-wiz', el)?.scrollIntoView({ behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth', block: 'start' }); }
    if (a === 'close') leave('.pl-wiz', () => { wizard = false; draw(); });
    if (a === 'ics' && d.plan) {
      const url = URL.createObjectURL(new Blob([ics(d.plan)], { type: 'text/calendar' })), link = document.createElement('a');
      link.href = url; link.download = `beobachtungsplan-${d.plan.nights[0]?.night || 'astroflow'}.ics`; link.click();
      setTimeout(() => URL.revokeObjectURL(url), 2000);
    }
    if (a === 'del' && await confirmDlg('Plan löschen?', 'Der gespeicherte Plan wird entfernt. Atlas und „Heute Nacht“ zeigen ihn dann nicht mehr.', 'Löschen', true)) {
      try { d.plan = (await send('/stacking/plan', null, 'DELETE')).plan; wizard = true; draw('#plBody > section'); } catch (err) { toast(err.message, 'err'); }
    }
    if (a === 'run') {
      const b = e.target.closest('button'); b.disabled = true; b.classList.add('pending');
      try { d.plan = (await send('/stacking/plan', w)).plan; wizard = false; draw('#plBody > section'); toast('Plan berechnet.', 'ok'); }
      catch (err) { toast(err.message, 'err'); b.disabled = false; b.classList.remove('pending'); }
    }
  });
  body.addEventListener('keydown', (e) => { const blk = e.target.closest?.('.pl-block'); if (blk && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); showBlock(+blk.dataset.n, +blk.dataset.k); } });
  body.addEventListener('change', (e) => {
    const eq = e.target.dataset?.eq;
    if (eq) { w.eq = e.target.checked ? [...new Set([...w.eq, eq])] : w.eq.filter(x => x !== eq); return; }
    if (e.target.id !== 'plWatch') return;
    const t = e.target.value.trim().toLowerCase(), o = catalog.find(x => x.name.toLowerCase() === t) || catalog.find(x => x.id.toLowerCase() === t.replace(/\s+/g, ''));
    if (!o) { toast('Objekt nicht im Katalog.', ''); return; }
    if (!w.watch.includes(o.id)) w.watch = [...w.watch, o.id].slice(0, 20);
    draw(); $('#plWatch', el)?.focus();
  });
  function showBlock(ni, k) {
    const p = d.plan, n = p.nights[ni], bl = n?.blocks[k];
    if (!bl) return;
    const t = p.targets.find(x => x.id === bl.target) || { name: bl.target }, dev = p.devices.find(x => x.id === bl.device);
    AF.ctx.open(el, { title: t.name, html: `<dl class="kv"><dt>Nacht</dt><dd>${esc(dayName(n.night))}</dd><dt>Zeit</dt><dd>${hm(bl.start)}–${hm(bl.end)}</dd>
        <dt>Nutzbar</dt><dd>${hours(bl.hours)}</dd><dt>Gerät</dt><dd>${esc(dev?.name || bl.device)}</dd>${bl.max_alt != null ? `<dt>Höchster Stand</dt><dd>${Math.round(bl.max_alt)}°</dd>` : ''}
        <dt>Gesamt im Plan</dt><dd>${hours(t.hours || 0)} von ~${hours(t.goal || p.wishes.min_hours || 0)}${t.have ? ` (bisher ${hours(t.have)})` : ''}</dd>${t.why ? `<dt>Bedarf</dt><dd>${esc(needText(t))}<div class="dim sm">${esc([t.why, ...(t.assumed || [])].join(' · '))}</div></dd>` : ''}</dl>
      <div class="row obs-ctx-acts"><a class="btn sm" href="#/observatorium?ra=${(+t.ra).toFixed(4)}&dec=${(+t.dec).toFixed(4)}&fov=6&name=${encodeURIComponent(t.name)}">${icon('globe')}Im Atlas</a>${t.href ? `<a class="btn sm ghost" href="${t.href}">${icon('image')}Projekt</a>` : ''}</div>` });
    AF.ctx.show();
  }
  return { destroy() { ro.disconnect(); } };
}

// Plan als Kalenderdatei (iCalendar, UTC) – wird im Browser erzeugt und gespeichert, nichts geht ins Netz
function ics(plan) {
  const z = (t) => new Date(t * 1000).toISOString().replace(/[-:]/g, '').replace(/\.\d+Z$/, 'Z');
  const txt = (v) => String(v).replace(/[\\;,]/g, (c) => '\\' + c).replace(/\n/g, '\\n');
  const dev = (id) => plan.devices.find(x => x.id === id)?.name || id;
  const ev = plan.nights.flatMap(n => n.blocks.flatMap((b, k) => {          // Zeilen ganz flach: je Feld eine Zeile
    const t = plan.targets.find(x => x.id === b.target) || { name: b.target };
    return ['BEGIN:VEVENT', `UID:astroflow-${n.night}-${b.device}-${k}@astroflow.local`, `DTSTAMP:${z(plan.created)}`,
      `DTSTART:${z(b.start)}`, `DTEND:${z(b.end)}`, `SUMMARY:${txt(`${t.name} · ${dev(b.device)}`)}`,
      `DESCRIPTION:${txt(`Nutzbar etwa ${hours(b.hours)}${b.max_alt != null ? `, höchster Stand ${Math.round(b.max_alt)}°` : ''} (AstroFlow-Plan)`)}`, 'END:VEVENT'];
  }));
  return ['BEGIN:VCALENDAR', 'VERSION:2.0', 'PRODID:-//AstroFlow//Beobachtungsplan//DE', 'CALSCALE:GREGORIAN', ...ev, 'END:VCALENDAR'].join('\r\n') + '\r\n';
}

// „Heute Nacht“: Abschnitt mit den Blöcken der gezeigten Nacht aus dem gespeicherten Plan
function nightSection(plan, night) {
  const n = plan?.nights?.find(x => x.night === night);
  if (!n) return '';
  const name = (id) => plan.targets.find(t => t.id === id)?.name || id, dev = (id) => plan.devices.find(x => x.id === id)?.name || id;
  return `<section class="panel pl-tonight"><div class="panel-h"><h2>${icon('list')}Dein Plan für diese Nacht</h2><span class="grow"></span><a class="linkbtn" href="#/observatorium?blick=hier&date=${encodeURIComponent(night)}">Bahnen im Atlas</a><a class="linkbtn" href="#/observatorium/plan">Plan öffnen ${icon('arrow')}</a></div>
    ${n.blocks.length ? `<ul class="pl-tn">${n.blocks.map(b => `<li><i style="background:${color(b.target)}"></i><span class="tnum">${hm(b.start)}–${hm(b.end)}</span><b>${esc(name(b.target))}</b>${plan.devices.length > 1 ? `<span class="dim sm">${esc(dev(b.device))}</span>` : ''}</li>`).join('')}</ul>`
      : `<p class="mut sm">${n.cloud >= 60 ? 'Laut Plan bewölkt – nichts vorgesehen.' : 'Für diese Nacht ist nichts geplant.'}</p>`}</section>`;
}

window.AFplan = { tabs, color, nightSection, ics };
const m = AF.modules.stacking;
m.routes.push([/^\/observatorium\/plan$/, 'observatory', planView]);
})();
