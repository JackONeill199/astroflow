'use strict';
/* Observatorium, Himmelskuppel #/observatorium/kuppel[?night=k|date=JJJJ-MM-TT]: der Himmel von oben (Zenit in der Mitte,
   Horizont am Rand, Norden oben, Osten links – wie eine drehbare Sternkarte über dem Kopf). Freier Ausschnitt laut
   Horizontmaske hell, verdeckter Bereich als Silhouette; je Ziel die Bahn durch die Nacht, im freien Fenster kräftig, geplante
   Blöcke in der Planfarbe; Mond; Zeitregler; Listen „Jetzt frei“ / „Später frei“ / „In dieser Nacht nie frei“.
   Rechnung auf dem Server (/api/stacking/dome, dome.py – lokal, ohne Netz), hier nur Darstellung. */
(() => {
const VB = 640, C = VB / 2, R = 272;                        // SVG-Raster: Kuppelradius R, Rand für Himmelsrichtungen
const DIRS = [['N', 0], ['NO', 45], ['O', 90], ['SO', 135], ['S', 180], ['SW', 225], ['W', 270], ['NW', 315]];
const hm = (t) => new Date(t * 1000).toLocaleTimeString('de-DE', { hour: '2-digit', minute: '2-digit' });
const dayName = (n) => new Date(n + 'T12:00:00Z').toLocaleDateString('de-DE', { weekday: 'long', day: 'numeric', month: 'long', timeZone: 'UTC' });
const span = (s) => `${hm(s[0])}–${hm(s[1])}`;
// Azimut/Höhe → Punkt (von oben gesehen: Norden oben, Osten links)
const xy = (az, alt) => { const r = (90 - alt) / 90 * R, a = az * Math.PI / 180; return [C - r * Math.sin(a), C - r * Math.cos(a)]; };
const f1 = (v) => v.toFixed(1);
const hzAt = (h, az) => { if (!h) return 0; const x = ((az % 360) + 360) % 360 / 10, i = Math.floor(x) % 36, f = x - Math.floor(x); return h[i] * (1 - f) + h[(i + 1) % 36] * f; };
// Linienzug durch Index-Bereiche (nur über dem Horizont); Lücken bei Sprung über den Zenit vermeiden: Punkte sind dicht genug
function path(az, alt, ok) {
  let d = '', on = false;
  for (let i = 0; i < az.length; i++) {
    if (!ok(i) || alt[i] < 0) { on = false; continue; }
    const [x, y] = xy(az[i], alt[i]);
    d += `${on ? 'L' : 'M'}${f1(x)} ${f1(y)}`; on = true;
  }
  return d;
}

async function domeView(el, query) {
  crumbs([['Übersicht', '#/'], ['Observatorium', '#/observatorium'], ['Himmelskuppel']]);
  const q = new URLSearchParams(query || '');
  let k = Math.max(0, parseInt(q.get('night') || '0', 10) || 0), date = (q.get('date') || '').slice(0, 10);
  el.innerHTML = `<div class="pagehead"><div><h1>Observatorium</h1><p class="mut">Was du heute von deinem Platz aus siehst – Himmel von oben, Horizont am Rand.</p></div>${window.AFplan?.tabs('dome') || ''}</div>
    <div class="dm-wrap"><div class="dm-grid" id="dmBody"><div class="skel" style="height:24rem"></div></div></div>`;
  let d = null, sel = null, ti = 0, show = { plan: true, own: true };
  const body = $('#dmBody', el);

  async function load() {
    body.classList.add('dm-busy');
    let r;
    try { r = await api(`/stacking/dome?${date ? 'date=' + encodeURIComponent(date) : 'night=' + k}`); }
    catch (e) { if (el.isConnected) body.innerHTML = `<section class="panel">${esc(e.message)}</section>`; return; }
    if (!el.isConnected) return;
    body.classList.remove('dm-busy');
    if (r.error && !r.targets) { body.innerHTML = `<section class="panel"><div class="notice">${icon('alert')}<div>${esc(r.error)}${/Standort/.test(r.error) ? ' <a href="#/settings/standorte">Standort eintragen</a>' : ''}</div></div></section>`; return; }
    const first = !d;
    d = r; k = r.k; date = '';
    const hasPlan = d.targets.some(t => t.source === 'plan');
    if (first) show = { plan: true, own: !hasPlan };
    // Zeitpunkt: jetzt, falls in dieser Nacht, sonst Beginn der Dunkelheit
    const at = (t) => Math.max(0, Math.min(d.n - 1, Math.round((t - d.t0) / d.step)));
    ti = d.now >= d.t0 && d.now <= d.t0 + (d.n - 1) * d.step ? at(d.now) : d.dark.length ? at(d.dark[0][0]) : 0;
    if (sel && !d.targets.some(t => t.id === sel)) sel = null;
    render();
  }

  const T = (i) => d.t0 + i * d.step;
  const visible = () => d.targets.filter(t => show[t.source]);
  const color = (t) => t.source === 'plan' && window.AFplan ? window.AFplan.color(t.id) : null;

  function sky() {
    const h = d.site.horizon, low = [], free = [];
    for (let az = 0; az < 360; az += 2) { low.push(xy(az, hzAt(h, az))); free.push(xy(az, Math.max(d.min_alt, hzAt(h, az)))); }
    const poly = (pts) => 'M' + pts.map(p => p.map(f1).join(' ')).join('L') + 'Z';
    let g = `<defs><pattern id="dmHatch" width="7" height="7" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><line class="dm-hatch" x1="0" y1="0" x2="0" y2="7"/></pattern></defs>
      <circle class="dm-ground" cx="${C}" cy="${C}" r="${R}"/><circle cx="${C}" cy="${C}" r="${R}" fill="url(#dmHatch)"/><path class="dm-low" d="${poly(low)}"/><path class="dm-free" d="${poly(free)}"/>`;
    for (const a of [30, 60]) g += `<circle class="dm-ring${a === d.min_alt ? ' min' : ''}" cx="${C}" cy="${C}" r="${f1((90 - a) / 90 * R)}"/><text class="dm-ax" x="${C + 4}" y="${f1(C - (90 - a) / 90 * R - 4)}">${a}°</text>`;
    for (const [n, az] of DIRS) {
      const [x1, y1] = xy(az, 0), [x2, y2] = xy(az, 90), [lx, ly] = xy(az, -7);
      g += `<line class="dm-spoke" x1="${f1(x1)}" y1="${f1(y1)}" x2="${f1(x2)}" y2="${f1(y2)}"/><text class="dm-dir${n.length === 1 ? ' main' : ''}" x="${f1(lx)}" y="${f1(ly + 4)}" text-anchor="middle">${n}</text>`;
    }
    return g + `<circle class="dm-rim" cx="${C}" cy="${C}" r="${R}"/>`;
  }

  function tracks() {
    const dark = (i) => d.dark.some(([a, b]) => T(i) >= a && T(i) <= b);
    const inSpans = (spans) => (i) => spans.some(([a, b]) => T(i) >= a && T(i) <= b);
    let g = '';
    const m = d.moon;
    g += `<path class="dm-moontrack" d="${path(m.az, m.alt, dark)}"/>`;
    for (const t of visible()) {
      const c = color(t), me = t.id === sel;
      const base = path(t.az, t.alt, dark), fr = path(t.az, t.alt, inSpans(t.free));
      const blocks = t.blocks.map(b => path(t.az, t.alt, (i) => T(i) >= b.start && T(i) <= b.end)).join('');
      g += `<g class="dm-t${me ? ' sel' : ''}${t.free.length ? '' : ' never'}" data-id="${esc(t.id)}">
        <path class="dm-track" d="${base}"/>${fr ? `<path class="dm-tfree" d="${fr}"${c ? ` style="stroke:${c}"` : ''}/>` : ''}
        ${blocks ? `<path class="dm-tblock" d="${blocks}" style="stroke:${c || 'var(--acc)'}"/>` : ''}
        <path class="dm-hit" d="${base}"><title>${esc(t.name)}${t.free.length ? ' · frei ' + t.free.map(span).join(', ') : ''}</title></path></g>`;
    }
    // Uhrzeit-Marken entlang der gewählten Bahn (volle Stunden)
    const st = sel && visible().find(t => t.id === sel);
    if (st) for (let i = 0; i < d.n; i++) {
      const t = T(i);
      if (Math.round(t) % 3600 !== 0 || st.alt[i] < 0 || !dark(i)) continue;
      const [x, y] = xy(st.az[i], st.alt[i]);
      g += `<g class="dm-tick"><circle cx="${f1(x)}" cy="${f1(y)}" r="3"/><text x="${f1(x + 6)}" y="${f1(y - 5)}">${hm(t).slice(0, 2)}</text></g>`;
    }
    return g;
  }

  // Positionen zum gewählten Zeitpunkt (Marken gleiten per CSS-Übergang)
  function marks() {
    let g = '';
    const m = d.moon;
    if (m.alt[ti] > 0) {
      const [x, y] = xy(m.az[ti], m.alt[ti]);
      g += `<g class="dm-moon" style="transform:translate(${f1(x)}px,${f1(y)}px)"><circle r="9"/><text x="13" y="4">Mond ${m.illum} %</text></g>`;
    }
    const t = T(ti), labels = [];
    for (const x of visible()) {
      if (x.alt[ti] < 0) continue;
      const [px, py] = xy(x.az[ti], x.alt[ti]), free = x.free.some(([a, b]) => t >= a && t <= b), me = x.id === sel;
      const c = color(x);
      const lab = (free || me) && !labels.some(([lx, ly]) => Math.abs(lx - px) < 70 && Math.abs(ly - py) < 14);
      if (lab) labels.push([px, py]);
      g += `<g class="dm-pos${free ? ' free' : ''}${me ? ' sel' : ''}" data-id="${esc(x.id)}" style="transform:translate(${f1(px)}px,${f1(py)}px)"><circle r="${me ? 6 : 4.5}"${c && free ? ` style="fill:${c}"` : ''}/>${lab ? `<text x="9" y="4">${esc(x.name)}</text>` : ''}</g>`;
    }
    return g;
  }

  function lists() {
    const t = T(ti), now = [], later = [], past = [], never = [];
    for (const x of visible()) {
      const cur = x.free.find(([a, b]) => t >= a && t <= b), next = x.free.find(([a]) => a > t);
      if (cur) now.push([cur[1], x, `noch bis ${hm(cur[1])}`]);
      else if (next) later.push([next[0], x, `frei ${x.free.filter(([a]) => a > t).map(span).join(', ')}`]);
      else if (x.free.length) past.push([0, x, `war frei ${x.free.map(span).join(', ')}`]);
      else never.push([0, x, x.why || 'nie frei']);
    }
    const item = ([, x, txt]) => { const c = color(x); return `<li><button type="button" class="dm-item${x.id === sel ? ' on' : ''}" data-id="${esc(x.id)}">
      <i style="background:${c || 'var(--acc)'}"></i><span class="dm-name">${esc(x.name)}</span><span class="dim sm">${esc(txt)}</span></button></li>`; };
    const grp = (title, rows, empty, more = '') => `<div class="dm-grp"><h3>${title} <span class="chip">${rows.length}</span></h3>${rows.length
      ? `<ul class="dm-list">${rows.slice(0, 12).map(item).join('')}</ul>${rows.length > 12 ? `<details class="dm-more"><summary class="sm">Alle ${rows.length}</summary><ul class="dm-list">${rows.slice(12).map(item).join('')}</ul></details>` : ''}`
      : `<p class="mut sm">${empty}</p>`}${more}</div>`;
    now.sort((a, b) => a[0] - b[0]); later.sort((a, b) => a[0] - b[0]);
    return grp(`Jetzt frei <span class="dim sm">(${hm(t)})</span>`, now, 'Gerade steht kein Ziel im freien Ausschnitt.')
      + grp('Später frei', later, 'Für den Rest der Nacht kommt nichts mehr dazu.')
      + (past.length ? grp('Schon vorbei', past, '') : '')
      + grp('In dieser Nacht nie frei', never, 'Alle Ziele kommen irgendwann ins freie Fenster.');
  }

  function render() {
    const hasPlan = d.targets.some(t => t.source === 'plan'), hasOwn = d.targets.some(t => t.source === 'own');
    const freeH = d.dark.reduce((s, [a, b]) => s + (b - a), 0) / 3600;
    body.innerHTML = `<section class="panel dm-main">
      <div class="dm-head"><div class="seg dm-night" role="group" aria-label="Nacht"><button type="button" data-nav="-1" ${d.k <= 0 ? 'disabled' : ''} aria-label="Nacht davor">${icon('back')}</button>
          <b>${esc(dayName(d.night))}</b><button type="button" data-nav="1" ${d.k >= d.nights - 1 ? 'disabled' : ''} aria-label="Nächste Nacht">${icon('arrow')}</button></div>
        <span class="grow"></span>
        ${hasPlan && hasOwn ? `<div class="seg dm-src" role="group" aria-label="Ziele">${[['plan', 'Plan'], ['own', 'Eigene Objekte']].map(([s, l]) => `<button type="button" data-src="${s}" class="${show[s] ? 'on' : ''}" aria-pressed="${show[s]}">${l}</button>`).join('')}</div>` : ''}</div>
      <p class="mut sm dm-sub">${esc(d.site.name)} · ${d.dark.length ? `dunkel ${d.dark.map(span).join(', ')} (${freeH.toFixed(1).replace('.', ',')} h)` : 'keine astronomische Dunkelheit'} · Mond ${d.moon.illum} % ·
        ${d.site.horizon ? 'mit Horizontmaske' : `ohne Horizontmaske – <a href="#/settings/standorte">einzeichnen</a>`}</p>
      <div class="dm-stage"><svg class="dm-svg" id="dmSvg" viewBox="0 0 ${VB} ${VB}" role="img" aria-label="Himmelskuppel von oben: Zenit in der Mitte, Horizont am Rand, Norden oben">
        <g id="dmSky">${sky()}</g><g id="dmTracks">${tracks()}</g><g id="dmMarks">${marks()}</g></svg></div>
      <div class="dm-time"><input type="range" id="dmT" min="0" max="${d.n - 1}" step="1" value="${ti}" aria-label="Uhrzeit"><b class="tnum" id="dmTL">${hm(T(ti))}</b><button type="button" class="btn ghost sm" id="dmNow">Jetzt</button></div>
      <div class="dm-legend sm"><span><i class="lg-free"></i>frei (dunkel, über ${d.min_alt}° und Maske)</span><span><i class="lg-low"></i>über dem Horizont, aber tief</span><span><i class="lg-ground"></i>verdeckt</span>${hasPlan ? '<span><i class="lg-plan"></i>geplanter Block</span>' : ''}</div>
    </section>
    <aside class="panel dm-side" id="dmLists">${d.targets.length ? lists() : `<p class="mut">Noch keine Ziele: <a href="#/observatorium/plan">Plan erstellen</a> oder Bilder mit Himmelsposition stacken.</p>`}</aside>`;
    $('#dmNow', el).disabled = !(d.now >= d.t0 && d.now <= T(d.n - 1));
  }

  // nur Marken, Uhrzeit und Listen neu (Zeitregler)
  function retime() {
    $('#dmMarks', el).innerHTML = marks();
    $('#dmTL', el).textContent = hm(T(ti));
    if (d.targets.length) $('#dmLists', el).innerHTML = lists();
  }
  function select(id, open = true) {
    sel = sel === id && !open ? null : id;
    $('#dmTracks', el).innerHTML = tracks();
    retime();
    const t = d.targets.find(x => x.id === sel);
    if (!t) { AF.ctx.clear(); return; }
    if (!open) return;
    AF.ctx.open(el, { title: t.name, html: `<dl class="kv"><dt>Frei</dt><dd>${t.free.length ? t.free.map(span).join(', ') + ` (${t.free_hours.toFixed(1).replace('.', ',')} h)` : esc(t.why || 'nie')}</dd>
        <dt>Höchster Stand</dt><dd>${Math.round(t.max_alt)}°</dd>${t.blocks.length ? `<dt>Geplant</dt><dd>${t.blocks.map(b => span([b.start, b.end])).join(', ')}</dd>` : ''}
        <dt>Herkunft</dt><dd>${t.source === 'plan' ? 'Beobachtungsplan' : 'eigenes Objekt'}</dd></dl>
      <div class="row obs-ctx-acts"><a class="btn sm" href="#/observatorium?ra=${(+t.ra).toFixed(4)}&dec=${(+t.dec).toFixed(4)}&fov=6&name=${encodeURIComponent(t.name)}">${icon('globe')}Im Atlas</a>${t.href ? `<a class="btn sm ghost" href="${t.href}">${icon('image')}Projekt</a>` : ''}</div>` });
    AF.ctx.show();
  }

  body.addEventListener('click', (e) => {
    const nav = e.target.closest('[data-nav]');
    if (nav && !nav.disabled) { k = Math.max(0, Math.min(d.nights - 1, d.k + +nav.dataset.nav)); load(); return; }
    const src = e.target.closest('[data-src]');
    if (src) { show[src.dataset.src] = !show[src.dataset.src]; if (!show.plan && !show.own) show[src.dataset.src === 'plan' ? 'own' : 'plan'] = true; render(); return; }
    if (e.target.id === 'dmNow') { ti = Math.max(0, Math.min(d.n - 1, Math.round((d.now - d.t0) / d.step))); $('#dmT', el).value = ti; retime(); return; }
    const it = e.target.closest('[data-id]');
    if (it) select(it.dataset.id);
  });
  body.addEventListener('input', (e) => { if (e.target.id === 'dmT') { ti = +e.target.value; retime(); } });
  await load();
  return { destroy() {} };
}

// Kleiner Verweis für „Heute Nacht“, Planer und „Von hier“
const link = (night) => `#/observatorium/kuppel${night ? '?date=' + encodeURIComponent(night) : ''}`;
window.AFdome = { link, xy };
const m = AF.modules.stacking;
m.routes.push([/^\/observatorium\/kuppel(?:\?(.*))?$/, 'observatory', domeView]);
})();
