'use strict';
/* Grundlage: Nachtgrafik aus dem pausierten UI-Umbau; Empfehlungen jetzt auch auf der Übersicht. */
(() => {
const COLORS = ['var(--acc)', 'var(--tn-orange)', 'var(--data-3)', 'var(--tn-pink)', 'var(--tn-violet)', 'var(--tn-lime)'];   // klar unterscheidbar
const hm = t => t == null ? '–' : new Date(t * 1000).toLocaleTimeString('de-DE', {hour: '2-digit', minute: '2-digit'});
const period = s => s ? `${hm(s[0])} – ${hm(s[1])}` : '–';
const targetLink = o => `href="${esc(o.href)}"${o.category === 'new' && AF.on('sage') ? ` data-night-catalog="${esc(o.id)}"` : ''}`;
const HID = 'af.tonight.hidden';        // „nicht interessiert“: nur lokal im Browser
const hiddenIds = () => { try { const a = JSON.parse(localStorage.getItem(HID) || '[]'); return Array.isArray(a) ? a.map(String) : []; } catch { return []; } };
const setHidden = a => { try { localStorage.setItem(HID, JSON.stringify([...new Set(a)])); } catch {} };
// Gewählte Nacht (0 = heute, bis tonight.NIGHTS-1); bleibt beim Neuladen der Ansicht, verlässt man „Heute Nacht“, gilt wieder heute
let nightIx = 0, nightReq = 0;
const query = (night = 0) => {
  const h = hiddenIds(), q = [h.length ? 'hide=' + encodeURIComponent(h.join(',')) : '', night ? 'night=' + night : ''].filter(Boolean);
  return q.length ? '?' + q.join('&') : '';
};
// Nachtname aus dem Datum, das der Server liefert (core.sites.planning_night): „Heute Nacht“, „Morgen Nacht“, „Fr 13.11.“
const nightDate = n => new Date(n.date + 'T12:00:00');
const shortDay = n => nightDate(n).toLocaleDateString('de-DE', {weekday: 'short'}).replace('.', '');
const dateDM = n => nightDate(n).toLocaleDateString('de-DE', {day: '2-digit', month: '2-digit'});
const nightName = (n, k) => k === 0 ? 'Heute Nacht' : k === 1 ? 'Morgen Nacht' : `${shortDay(n)} ${dateDM(n)}`;
const curName = d => d.nights?.[d.night_index || 0] ? nightName(d.nights[d.night_index || 0], d.night_index || 0) : 'Heute Nacht';
const num = v => String(v).replace('.', ',');
const noSite = () => `<p>Für die Nachtplanung fehlt dein Beobachtungsort.</p><a class="btn ghost sm" href="#/settings/standorte">Standort eintragen</a>`;
// „Heute bin ich in …“: Ort für die heutige Nacht wählen (gemeinsame Ortsliste, /api/sites/night)
function sitePicker(sites, d) {
  if (!sites?.list?.length) return '';
  const night = d.night || sites.today;
  const fixed = sites.periods.find(p => p.from === night && p.to === night);
  const cur = d.site_info?.id;
  const k = d.night_index || 0, when = k && d.nights?.[k] ? (k === 1 ? 'Morgen' : `Am ${dateDM(d.nights[k])}`) : 'Heute';
  return `<label class="tn-site"><span class="mut sm">${when} bin ich in</span><select id="tnSite" data-night="${esc(night)}" aria-label="Standort für ${esc(curName(d))}">
    ${fixed ? '' : `<option value="">${esc((sites.list.find(s => s.id === cur)?.name || 'Standard-Ort'))} (${esc(d.site_info?.detail || 'automatisch')})</option>`}
    ${sites.list.map(s => `<option value="${esc(s.id)}" ${fixed && fixed.site_id === s.id ? 'selected' : ''}>${esc(s.name)}</option>`).join('')}
    ${fixed ? '<option value="">Automatisch (Zeitraum bzw. Standard-Ort)</option>' : ''}</select></label>`;
}

// Geometrie des Nachtdiagramms: oben das Wolkenband (hohe/mittlere/tiefe Wolken, Wind), darunter Höhe über Zeit.
function geom(d, W, H) {
  const layers = !!(d.clouds && ['high', 'mid', 'low'].some(k => d.clouds[k]?.some(v => v != null)));
  const band = d.cloud ? (layers ? 3 * 7 : 12) + (d.wind?.some(v => v != null) ? 7 : 0) : 0;
  const L = 34, R = 12, B = 30, T = band ? band + 16 : 18;
  const t0 = d.times[0], t1 = d.times[d.times.length - 1];
  return {L, R, T, B, W, H, band, layers, PW: W - L - R, PH: H - T - B,
    X: t => L + (t - t0) / (t1 - t0) * (W - L - R), Y: a => T + (90 - Math.max(0, a)) / 90 * (H - T - B)};
}
const clamp = (v, a = 0, b = 1) => Math.max(a, Math.min(b, v));
// Mondphase als Pfad: beleuchteter Teil (k 0…1), zunehmend rechts hell (Südhalbkugel gespiegelt)
function moonShape(cx, cy, r, k, rightLit) {
  const rx = Math.abs(1 - 2 * k) * r, a = rightLit ? 1 : 0, b = (k < .5) === rightLit ? 0 : 1;
  return `M${cx.toFixed(1)} ${(cy - r).toFixed(1)}A${r} ${r} 0 0 ${a} ${cx.toFixed(1)} ${(cy + r).toFixed(1)}A${rx.toFixed(2)} ${r} 0 0 ${b} ${cx.toFixed(1)} ${(cy - r).toFixed(1)}Z`;
}
const moonIcon = (cx, cy, r, illum, waxing, south) => `<circle class="tn-moon-disc" cx="${cx}" cy="${cy}" r="${r}"/>${illum > 1 ? `<path class="tn-moon-lit" d="${moonShape(cx, cy, r, illum / 100, waxing !== !!south)}"/>` : ''}`;
// Horizontaler Verlauf über die Plotfläche: je Zeitschritt eine Haltestelle mit eigener Deckkraft
const ramp = (id, d, g, cls, op) => `<linearGradient id="${id}" x1="0" x2="1" y1="0" y2="0">${d.times.map((t, i) => {
  const o = op(i);
  return `<stop offset="${((g.X(t) - g.L) / g.PW).toFixed(4)}" class="${cls}" stop-opacity="${o == null || isNaN(o) ? 0 : clamp(o).toFixed(3)}"/>`;
}).join('')}</linearGradient>`;
let chartSeq = 0;

function chart(d, W, H, selected, hidden) {
  const g = geom(d, W, H), {L, T, PW, PH, X, Y} = g, id = `tn${++chartSeq}`;
  const path = a => a.map((v, i) => `${i ? 'L' : 'M'}${X(d.times[i]).toFixed(1)} ${Y(v).toFixed(1)}`).join('');
  const sun = d.sun;
  // Himmel: dunkler Grund, darüber weiche Dämmerung (astronomisch → nautisch → bürgerlich → Tag) und Horizontglühen
  let defs = ramp(`${id}tw`, d, g, 'tn-st-twi', i => (sun[i] + 18) / 12 * .75)
    + ramp(`${id}dy`, d, g, 'tn-st-day', i => (sun[i] + 4) / 6 * .8)
    + ramp(`${id}gl`, d, g, 'tn-st-glow', i => 1 - Math.abs(sun[i] + 5) / 8)
    + `<linearGradient id="${id}gv" x1="0" x2="0" y1="0" y2="1"><stop offset=".35" stop-color="#fff" stop-opacity="0"/><stop offset="1" stop-color="#fff"/></linearGradient>
       <mask id="${id}gm"><rect x="${L}" y="${T}" width="${PW}" height="${PH}" fill="url(#${id}gv)"/></mask>
       <clipPath id="${id}c"><rect x="${L}" y="${T}" width="${PW}" height="${PH}" rx="4"/></clipPath>
       <clipPath id="${id}u"><rect x="${L - 4}" y="${T - 8}" width="${PW + 8}" height="${Y(d.min_alt) - T + 8}"/></clipPath>
       <linearGradient id="${id}mv" x1="0" x2="0" y1="0" y2="1"><stop offset="0" class="tn-st-moon" stop-opacity=".9"/><stop offset="1" class="tn-st-moon" stop-opacity=".15"/></linearGradient>`;
  let sky = `<rect class="tn-night" x="${L}" y="${T}" width="${PW}" height="${PH}"/>
    <rect x="${L}" y="${T}" width="${PW}" height="${PH}" fill="url(#${id}tw)"/>
    <rect x="${L}" y="${T}" width="${PW}" height="${PH}" fill="url(#${id}dy)"/>
    <rect x="${L}" y="${T}" width="${PW}" height="${PH}" fill="url(#${id}gl)" mask="url(#${id}gm)"/>`;
  // Sterne nur, wo es astronomisch dunkel ist
  const spans = [];
  d.times.forEach((t, i) => { if (sun[i] <= -18) { const x = X(t); if (spans.length && spans[spans.length - 1][1] >= x - PW / d.times.length * 1.5) spans[spans.length - 1][1] = x; else spans.push([x, x]); } });
  sky += window.AFdeco?.draw('stars', {L, T, PW, PH, spans}) || '';
  // Wolken als Schleier im Diagramm (Gesamtbedeckung)
  if (d.cloud) {
    defs += ramp(`${id}cl`, d, g, 'tn-st-veil', i => d.cloud[i] == null ? 0 : d.cloud[i] / 100 * .38);
    sky += `<rect x="${L}" y="${T}" width="${PW}" height="${PH}" fill="url(#${id}cl)"/>`;
  }
  let out = `<g clip-path="url(#${id}c)">${sky}`;
  // unter der Mindesthöhe: leicht abgedunkelt
  out += `<rect class="tn-below" x="${L}" y="${Y(d.min_alt)}" width="${PW}" height="${Y(0) - Y(d.min_alt)}"/>`;
  // Mond: Fläche unter der Bahn, umso heller je voller
  const ma = d.moon.alt, mi = d.moon.illum;
  if (ma.some(v => v > 0)) {
    out += `<path class="tn-moon-area" d="${path(ma)}L${X(d.times[d.times.length - 1])} ${Y(0)}L${L} ${Y(0)}Z" fill="url(#${id}mv)" opacity="${(.06 + .34 * mi / 100).toFixed(2)}"/>
      <path class="tn-moon-line" d="${path(ma)}"><title>Mond (${mi} % beleuchtet)</title></path>`;
  }
  out += '</g>';
  for (const a of [0, 30, 60, 90]) {
    out += `<line class="${a === d.min_alt ? 'tn-minalt' : 'tn-grid'}" x1="${L}" x2="${L + PW}" y1="${Y(a)}" y2="${Y(a)}"/>
      <text class="tn-axis" x="${L - 5}" y="${Y(a) + 4}" text-anchor="end">${a}°</text>`;
  }
  // Mondsymbol am höchsten Punkt der Bahn
  const mk = ma.indexOf(Math.max(...ma));
  if (ma[mk] > 3) out += `<g class="tn-moon-icon">${moonIcon(+X(d.times[mk]).toFixed(1), +(Y(ma[mk]) - 11).toFixed(1), 6, mi, d.moon.waxing, d.site?.[0] < 0)}</g>`;
  // Zielkurven: blass unter der Mindesthöhe, kräftig darüber; die gewählte zuletzt (oben) mit Leuchten
  const order = d.recommendations.map((o, k) => k).filter(k => !hidden.has(k) && k !== selected);
  if (!hidden.has(selected) && d.recommendations[selected]) order.push(selected);
  for (const k of order) {
    const o = d.recommendations[k], p = path(o.alt), dash = o.weather_blocked ? ' stroke-dasharray="7 5"' : '';
    out += `<g class="tn-series${k === selected ? ' sel' : ''}${o.weather_blocked ? ' blocked' : ''}" data-target="${k}" style="--target:${COLORS[k]}"><path class="tn-low" d="${p}"${dash}/><path class="tn-high" d="${p}" clip-path="url(#${id}u)"${dash}/><title>${esc(o.name)}</title></g>`;
  }
  const so = d.recommendations[selected];
  if (so && !hidden.has(selected)) {
    const j = so.alt.indexOf(Math.max(...so.alt)), x = X(d.times[j]), y = Y(so.alt[j]);
    if (so.alt[j] > 0) out += `<text class="tn-peak" x="${clamp(x, L + 40, L + PW - 40).toFixed(1)}" y="${Math.max(T + 12, y - 11).toFixed(1)}" text-anchor="middle" style="--target:${COLORS[selected]}">${esc(so.name.length > 22 ? so.name.slice(0, 21) + '…' : so.name)}</text>`;
  }
  if (d.now >= d.times[0] && d.now <= d.times[d.times.length - 1])
    out += `<line class="tn-now" x1="${X(d.now)}" x2="${X(d.now)}" y1="${T}" y2="${T + PH}"><title>Jetzt</title></line>`;
  // Wolkenband oben: hohe, mittlere, tiefe Wolken (wie am Himmel übereinander), darunter Wind
  if (g.band) {
    const rows = g.layers ? [['high', 'Hohe Wolken'], ['mid', 'Mittelhohe Wolken'], ['low', 'Tiefe Wolken']] : [['all', 'Bewölkung']];
    let y = 6;
    rows.forEach(([k, label]) => {
      const vals = k === 'all' ? d.cloud : d.clouds[k], h = g.layers ? 6 : 11;
      defs += ramp(`${id}b${k}`, d, g, 'tn-st-cloud', i => vals[i] == null ? 0 : vals[i] / 100 * .95);
      out += `<rect class="tn-band-bg" x="${L}" y="${y}" width="${PW}" height="${h}" rx="2"/><rect x="${L}" y="${y}" width="${PW}" height="${h}" rx="2" fill="url(#${id}b${k})"><title>${label}</title></rect>`;
      y += h + 1;
    });
    out += `<g class="tn-band-ico" transform="translate(${L - 24} ${g.layers ? 4 : 2}) scale(.75)"><path d="M6 17h13a4 4 0 0 0 0-8 6 6 0 0 0-11.3 1.5A3.5 3.5 0 0 0 6 17z"/></g>`;
    if (d.wind?.some(v => v != null)) {
      defs += ramp(`${id}wd`, d, g, 'tn-st-wind', i => d.wind[i] == null ? 0 : (d.wind[i] - 10) / 35);
      out += `<rect class="tn-band-bg" x="${L}" y="${y + 1}" width="${PW}" height="4" rx="2"/><rect x="${L}" y="${y + 1}" width="${PW}" height="4" rx="2" fill="url(#${id}wd)"><title>Wind (kräftiger = stärker)</title></rect>
        <path class="tn-wind-ico" d="M${L - 20} ${y + 1}h9a2 2 0 1 0-2-2M${L - 22} ${y + 4}h12a2 2 0 1 1-2 2"/>`;
    }
  }
  const step = W < 500 ? 4 : 2;
  for (let t = Math.ceil(d.times[0] / 3600) * 3600; t < d.times[d.times.length - 1]; t += 3600) {
    if (new Date(t * 1000).getHours() % step === 0 && X(t) > L + 15 && X(t) < W - g.R - 15)
      out += `<text class="tn-axis" x="${X(t)}" y="${H - 8}" text-anchor="middle">${hm(t)}</text>`;
  }
  return `<svg class="tn-chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="Höhenverlauf empfohlener Ziele, Mond und Bewölkung in dieser Nacht"><defs>${defs}</defs>${out}<g class="tn-cursor"></g></svg>`;
}
const sunState = a => a > -0.833 ? 'Tag' : a > -6 ? 'Bürgerliche Dämmerung' : a > -12 ? 'Nautische Dämmerung' : a > -18 ? 'Astronomische Dämmerung' : 'Dunkel';

// Der eigene Lebenszyklus hält Auswahl und Zeitzeiger über Status-Polling und Größenänderungen hinweg.
customElements.define('af-tonight-chart', class extends HTMLElement {
  connectedCallback() {
    if (!this.plan) this.plan = JSON.parse(this.textContent);
    this.selected ??= Math.max(0, this.plan.recommendations.findIndex(o => o.id === this.plan.initial_target));
    this.hiddenTargets ??= new Set();
    this.index ??= Math.max(0, this.plan.times.findIndex(t => t >= this.plan.now));
    this.textContent = '';
    this.resizeChart = () => {
      const w = Math.round(this.clientWidth), h = Math.round(Math.min(480, Math.max(340, innerHeight * .38)));
      if (w >= 100 && (w !== this.width || h !== this.height)) { this.width = w; this.height = h; this.draw(); }
    };
    this.observer = new ResizeObserver(this.resizeChart);
    this.observer.observe(this);
    window.addEventListener('resize', this.resizeChart);
    this.onclick = e => {
      const choice = e.target.closest('[data-select]');
      if (choice) { this.selected = +choice.dataset.select; this.hiddenTargets.delete(this.selected); this.draw(); this.querySelector(`[data-select="${this.selected}"]`).focus({preventScroll: true}); }
      const svg = e.target.closest('svg.tn-chart');
      if (svg) {
        this.point(e, svg);
        const r = svg.getBoundingClientRect(), g = geom(this.plan, this.width, this.height), a = 90 * (1 - (e.clientY - r.top - g.T) / g.PH);
        const visible = this.plan.recommendations.map((o, k) => ({o, k})).filter(x => !this.hiddenTargets.has(x.k));
        visible.sort((x, y) => Math.abs(x.o.alt[this.index] - a) - Math.abs(y.o.alt[this.index] - a));
        if (visible.length) this.selected = visible[0].k;
        this.pinned = true; this.draw(); this.inspect();
      }
    };
    this.onchange = e => {
      if (!e.target.matches('[data-visible]')) return;
      const k = +e.target.dataset.visible;
      if (e.target.checked) this.hiddenTargets.delete(k); else this.hiddenTargets.add(k);
      this.draw(); this.querySelector(`[data-visible="${k}"]`).focus({preventScroll: true});
    };
    this.oninput = e => {
      if (!e.target.matches('.tn-time')) return;
      this.index = +e.target.value; this.pinned = true; this.inspect();
    };
    this.onpointermove = e => {
      const svg = e.target.closest('svg.tn-chart');
      if (svg && e.pointerType !== 'touch') { this.point(e, svg); this.inspect(); }
    };
    this.onpointerleave = () => { if (!this.pinned) { this.querySelector('.tn-tooltip')?.setAttribute('hidden', ''); this.querySelector('.tn-cursor')?.replaceChildren(); } };
  }
  point(e, svg) {
    const g = geom(this.plan, this.width, this.height), r = svg.getBoundingClientRect(), f = clamp((e.clientX - r.left - g.L) / g.PW);
    this.index = Math.round(f * (this.plan.times.length - 1));
  }
  draw() {
    const d = this.plan, o = d.recommendations[this.selected];
    const open = this.querySelector('.tn-target')?.open;
    this.innerHTML = `<div class="tn-stage">${chart(d, this.width, this.height, this.selected, this.hiddenTargets)}<div class="tn-tooltip" hidden></div></div>${legend(d)}
      <div class="tn-scrub"><output class="tn-clock">${hm(d.times[this.index])}</output><input class="tn-time" type="range" min="0" max="${d.times.length - 1}" value="${this.index}" aria-label="Uhrzeit im Höhenverlauf" aria-valuetext="${hm(d.times[this.index])}"><span class="mut sm">${Math.round(d.moon.illum)} % Mond</span></div>
      <div class="tn-choices">${d.recommendations.map((x, k) => `<div class="tn-choice" style="--target:${COLORS[k]}"><input type="checkbox" data-visible="${k}" ${this.hiddenTargets.has(k) ? '' : 'checked'} aria-label="Kurve ${esc(x.name)} anzeigen"><button type="button" data-select="${k}" aria-pressed="${this.selected === k}">${esc(x.name)}<small>${x.category === 'more' ? 'Mehr Zeit' : 'Neu'}${x.weather_blocked ? ' · bei klarem Himmel' : ''}${x.season?.kind === 'last' ? ' · letzte Chance' : ''}</small></button></div>`).join('')}</div>
      ${o ? `<div class="tn-selection"><a ${targetLink(o)}>Objekt öffnen ${icon('arrow')}</a><span class="mut sm">${o.good.map(period).join(', ')} · max. ${Math.round(o.max_alt)}°${o.exposure ? ' · bisher ' + dur(o.exposure) : ''}</span>
        <button type="button" class="tn-hide" data-tn-hide="${esc(o.id)}" title="Dieses Ziel nicht mehr vorschlagen">Nicht interessiert</button>
        ${o.weather_blocked ? '<p class="tn-tag soon">Laut Vorhersage zu bewölkt – dieses Ziel gilt bei klarem Himmel.</p>' : ''}${o.category === 'more' && o.gain ? `<p class="tn-gain">${esc(o.gain.text)}</p>` : ''}${o.season ? `<p class="tn-tag ${o.season.kind}">${esc(o.season.text)}</p>` : ''}
        <details class="tn-target" ${open ? 'open' : ''}><summary>${o.category === 'more' ? 'Warum mehr Belichtung?' : 'Warum dieses Ziel?'}</summary><ul class="tn-why">${o.reasons.filter(r => r !== o.season?.text).map(r => `<li>${esc(r)}</li>`).join('')}</ul>${o.category === 'new' ? `<p class="mut sm">${esc(o.benefit)}</p>` : ''}${o.moon_warning ? '<p class="warn sm">Mond zeitweise näher als 40°: schwache Details können leiden.</p>' : ''}</details></div>` : ''}`;
    if (this.pinned) this.inspect();
  }
  inspect() {
    const d = this.plan, i = this.index, W = this.width, H = this.height;
    const g = geom(d, W, H), x = g.X(d.times[i]), y = g.Y;
    let dots = `<line x1="${x}" x2="${x}" y1="${g.band ? 4 : g.T}" y2="${g.T + g.PH}" stroke="var(--fg)" stroke-opacity=".7"/>`;
    const rows = d.recommendations.map((o, k) => {
      if (this.hiddenTargets.has(k)) return '';
      if (o.alt[i] >= 0) dots += `<circle cx="${x}" cy="${y(o.alt[i])}" r="${k === this.selected ? 5 : 4}" fill="${COLORS[k]}" stroke="var(--surface)"/>`;
      return `<span style="--target:${COLORS[k]}"${o.alt[i] < d.min_alt ? ' class="low"' : ''}>${esc(o.name)}<b>${Math.round(o.alt[i])}°</b></span>`;
    }).join('');
    const sky = [d.cloud?.[i] != null ? `☁ ${d.cloud[i]} %` : '', d.wind?.[i] != null ? `Wind ${d.wind[i]} km/h` : ''].filter(Boolean).join(' · ');
    this.querySelector('.tn-cursor').innerHTML = dots;
    const tip = this.querySelector('.tn-tooltip');
    tip.hidden = false;
    tip.innerHTML = `<b>${hm(d.times[i])} <small>${sunState(d.sun[i])}</small></b>${rows}<span class="moon">Mond<b>${Math.round(d.moon.alt[i])}°</b></span>${sky ? `<small class="tn-tip-sky">${sky}</small>` : ''}`;
    tip.style.left = Math.max(4, Math.min(W - Math.min(tip.offsetWidth || 260, W - 8) - 4, x + 12)) + 'px';   // echte Breite (rem skaliert mit)
    const slider = this.querySelector('.tn-time');
    slider.value = i; slider.setAttribute('aria-valuetext', hm(d.times[i]));
    this.querySelector('.tn-clock').textContent = hm(d.times[i]);
  }
  disconnectedCallback() { this.observer?.disconnect(); window.removeEventListener('resize', this.resizeChart); }
});
const graph = (d, initial_target) => `<af-tonight-chart>${esc(JSON.stringify({...d, initial_target, now: Math.floor(d.now / 300) * 300}))}</af-tonight-chart>`;
const planList = (d, full) => d.plan?.length ? `<div class="tn-plan"><h3>Nachtplan</h3><ol>${d.plan.map(b => `<li><span class="tn-when">${hm(b.start)} – ${hm(b.end)}</span> <a href="${esc(b.href)}">${esc(b.name)}</a> <small class="mut">${b.category === 'more' ? 'Mehr Zeit' : 'Neu'}${b.cloud != null ? ' · ' + b.cloud + ' % Wolken' : ''}</small></li>`).join('')}</ol>${full ? '<p class="mut sm">Ein Smart-Teleskop nimmt nur ein Ziel gleichzeitig auf: Die nutzbare Nacht ist in Abschnitte geteilt, jeder bekommt das Ziel, das dann am besten steht (Höhe, Mond, Wolken, Zenitnähe).</p>' : ''}</div>` : '';
const day = (t, tonight) => (tonight ? 'heute Nacht' : 'in der folgenden Nacht') + ` ${hm(t.start)} – ${hm(t.end)}`;
// Meldung zur Lage – getrennt nach Ursache (bedeckt / teils bewölkt / Höhe / Nacht vorbei / keine Dunkelheit)
function statusNote(d) {
  const sky = d.sky, st = d.status?.code, nc = sky?.next_clear;
  const next = nc ? `Nächstes klares Fenster: ${day(nc, nc.tonight)}.` : 'In den nächsten 48 Stunden zeigt die Vorhersage kein klares Fenster.';
  if (st === 'no_dark') return '<p class="tn-note">In dieser Nacht gibt es hier keine astronomisch dunkle Zeit.</p>';
  if (st === 'over') return '<p class="tn-note">Die dunkle Zeit ist vorbei (Dämmerung bzw. Tag). Empfehlungen gelten wieder ab dem Abend.</p>';
  if (st === 'overcast') return `<p class="tn-note cloudy"><b>${esc(curName(d))} bedeckt</b> (${sky.mean} % Bewölkung laut Vorhersage). ${esc(next)} Die Ziele darunter gelten bei klarem Himmel.</p>`;
  if (sky?.state === 'partly') return `<p class="tn-note"><b>Teils bewölkt</b> (${sky.mean} % im Mittel, etwa ${String(sky.usable_hours).replace('.', ',')} h klar nutzbar). Ziele in klaren Stunden stehen vorn; gestrichelte gelten bei klarem Himmel.</p>`;
  if (st === 'low_alt') return `<p class="tn-note">Für den verbleibenden Teil der Nacht steht kein passendes Ziel höher als ${d.min_alt}° (Höhe, nicht Wetter).</p>`;
  return '';
}
// Blättern durch die Nächte: ‹ Name › und eine Leiste mit Mond und Bewölkung je Nacht
function nightNav(d) {
  const ns = d.nights || [], k = d.night_index || 0;
  if (!ns[k]) return '';
  const south = d.site?.[0] < 0;
  const tag = !d.weather_on ? '' : d.forecast?.covered ? (d.forecast.uncertain ? '<span class="tn-unsure" title="Wolkenvorhersagen über mehr als zwei Tage treffen oft nicht zu">Vorhersage unsicher</span>' : '')
    : k ? '<span class="tn-unsure off" title="So weit reicht die Vorhersage nicht – nur Dunkelheit, Mond und Ziele">ohne Wetter</span>' : '';
  const cell = n => {
    const cloud = n.cloud == null ? '' : ` · ${n.cloud} % Wolken${n.uncertain ? ' (unsicher)' : ''}`;
    return `<button type="button" role="tab" aria-selected="${n.index === k}" data-tn-night="${n.index}" title="${esc(nightName(n, n.index))}: ${num(n.dark_hours)} h dunkel · Mond ${n.moon} %${cloud}">
      <small>${n.index === 0 ? 'Heute' : esc(shortDay(n))}</small><span class="tn-d">${esc(dateDM(n).slice(0, 2))}</span>
      <svg viewBox="0 0 16 16" aria-hidden="true">${moonIcon(8, 8, 6, n.moon, n.waxing, south)}</svg>
      <i class="tn-cl${n.cloud == null ? ' none' : ''}${n.uncertain ? ' unsure' : ''}" style="--c:${n.cloud ?? 0}"></i></button>`;
  };
  return `<div class="tn-nav"><button type="button" class="tn-step prev" data-tn-night="${k - 1}" ${k ? '' : 'disabled'} aria-label="Vorige Nacht">${icon('chev')}</button>
    <b class="tn-nav-name">${esc(nightName(ns[k], k))}${k < 2 ? ` <small>${esc(shortDay(ns[k]))} ${esc(dateDM(ns[k]))}</small>` : ''}</b>
    <button type="button" class="tn-step next" data-tn-night="${k + 1}" ${k < ns.length - 1 ? '' : 'disabled'} aria-label="Nächste Nacht">${icon('chev')}</button>${tag}</div>
    <div class="tn-strip" role="tablist" aria-label="Nacht wählen (auch mit ← →)">${ns.map(cell).join('')}</div>`;
}
// Kompakte Legende aus Symbolen; Erklärungen im Tooltip
function legend(d) {
  const item = (svg, text, title) => `<li title="${esc(title)}">${svg.startsWith('<i') ? svg : `<svg viewBox="0 0 18 12" aria-hidden="true">${svg}</svg>`}${esc(text)}</li>`;
  return `<ul class="tn-legend">${[
    item('<i class="tn-lg-twi" aria-hidden="true"></i>', 'Dämmerung', 'Verlauf von hell nach dunkel: bürgerliche, nautische und astronomische Dämmerung'),
    item('<rect class="tn-night" width="18" height="12" rx="2"/><circle class="tn-lg-star" cx="5" cy="4" r="1"/><circle class="tn-lg-star" cx="13" cy="7" r=".8"/>', 'Dunkel', 'Sonne unter −18°'),
    item('<path class="tn-minalt" d="M0 6h18"/>', `${d.min_alt}°`, 'Mindesthöhe: darunter blass gezeichnet'),
    item(`<g transform="translate(3 0)">${moonIcon(6, 6, 5, Math.max(d.moon.illum, 30), d.moon.waxing, d.site?.[0] < 0)}</g>`, 'Mond', 'Mondbahn; die Fläche ist umso heller, je voller der Mond'),
    d.cloud ? item('<rect class="tn-lg-cloud" width="18" height="3.5" rx="1"/><rect class="tn-lg-cloud" y="4.25" width="18" height="3.5" rx="1" opacity=".6"/><rect class="tn-lg-cloud" y="8.5" width="18" height="3.5" rx="1" opacity=".3"/>', 'Wolken', 'Band oben: hohe, mittlere und tiefe Wolken; Schleier im Diagramm: Gesamtbewölkung') : '',
    d.wind?.some(v => v != null) ? item('<path class="tn-wind-ico" d="M1 4h9a2 2 0 1 0-2-2M1 8h12a2 2 0 1 1-2 2"/>', 'Wind', 'Streifen unter den Wolken: kräftiger = mehr Wind (bei Böen wackelt das Bild)') : '',
  ].join('')}</ul>`;
}
function content(d, initial_target, full) {
  if (!d.site) return `<div class="tn-empty">${noSite()}</div>`;
  const dev = d.device;
  return `${full ? nightNav(d) : ''}<div class="tn-overview"><div><small>Dunkle Zeit</small><b>${dur(d.dark_hours * 3600)}</b></div><div><small>Mond beleuchtet</small><b>${d.moon.illum} %</b></div><div><small>Dein Gerät</small><b>${esc(dev.name)}</b></div>${window.AFsky?.tonightTile(d) || ''}</div>
    ${statusNote(d)}
    ${graph(d, initial_target)}
    ${planList(d, full)}
    <details class="tn-notes mut sm"><summary>Berechnung &amp; Bildfeld</summary><p>${esc(dev.name)}${dev.fov ? ` · Bildfeld ca. ${dev.fov.map(v => v.toFixed(2).replace('.', ',') + '°').join(' × ')} (${esc(dev.source)})` : ' · Bildfeld unbekannt, Größenfilter aus'}</p><p>Neue Ziele: Messier- und Caldwell-Katalog (fest eingebaut)${d.catalog?.astap_found ? ' plus auffällige Objekte aus ASTAPs deep_sky.csv (ohne Helligkeitsangabe, nach Größe und Name gewählt)' : ' – ohne ASTAPs deep_sky.csv keine weiteren Objekte'}. Helligkeitsgrenze für dieses Gerät etwa ${num(d.catalog?.mag_limit ?? 11)} mag; Größe 3–90 % der kurzen Bildfeldseite. ${d.catalog?.candidates ?? 0} Kandidaten passen zu Gerät und Bildfeld.</p><p>Rangfolge: nutzbare Stunden in der dunklen Zeit, gewichtet mit Mond (Phase × Abstand), Wolken (falls die Vorhersage im Tutor an ist) und – bei Alt-Az-Geräten – Zenitnähe über 80° (Feldrotation); dazu Kulmination im Dunkeln und Saison. „Mehr Zeit“ rechnet das Rauschen nach der 1/√Zeit-Regel. Freier Horizont wird vorausgesetzt.</p></details>`;
}
// Kopf der Übersicht („Klarer Himmel heute Nacht?“) an die Vorhersage anpassen; ohne Wetter neutral
function heroText(d) {
  const h1 = document.querySelector('.dashboard-hero h1');
  if (!h1) return;
  h1.dataset.orig ??= h1.textContent;
  if (!/^(Klarer Himmel|Heute Nacht|Teils bewölkt|Bedeckt)/.test(h1.dataset.orig) && !h1.dataset.tn) return;   // nur die Nachtbegrüßung ersetzen
  const s = d?.sky?.state, c = d?.status?.code;
  h1.dataset.tn = '1';
  h1.textContent = !d?.site || !s ? 'Was lohnt sich heute Nacht?' : c === 'overcast' ? 'Heute Nacht bedeckt' : s === 'partly' ? 'Teils bewölkt heute Nacht' : 'Klarer Himmel heute Nacht';
}
// Kompakte Karte für die Übersicht: Lage (Dunkelheit, Mond, Wetter), bis zu drei Ziele, Verweis auf die volle Planung.
// Die interaktive Grafik zeigt die Übersicht nur in der Aufteilung „Nachtplanung“ (store dashboard.layout).
function compact(d) {
  if (!d.site) return `<div class="tn-empty">${noSite()}</div>`;
  const sky = d.status?.code === 'overcast' ? `bedeckt (${d.sky.mean} %)` : d.sky?.state === 'partly' ? `teils bewölkt (${d.sky.mean} %)` : d.sky?.state ? 'klar' : null;
  const recs = (d.recommendations || []).slice(0, 3);
  return `<div class="tn-overview"><div><small>Dunkle Zeit</small><b>${dur(d.dark_hours * 3600)}</b></div><div><small>Mond</small><b>${d.moon.illum} %</b></div>${sky ? `<div><small>Wetter</small><b>${esc(sky)}</b></div>` : ''}${window.AFsky?.tonightTile(d) || ''}</div>
    ${statusNote(d)}
    ${recs.length ? `<ol class="tn-top">${recs.map(x => `<li><a ${targetLink(x)}>${esc(x.name)}</a><small>${esc(x.kind_label || '')}${x.good?.length ? ' · ' + period(x.good[0]) : ''}${x.category === 'more' ? ' · mehr Zeit' : ''}${x.weather_blocked ? ' · bei klarem Himmel' : ''}</small></li>`).join('')}</ol>` : '<p class="mut sm">Heute keine passenden Ziele.</p>'}`;
}
async function featured() {
  let body;
  const full = store.get('dashboard.layout', 'balanced') === 'planning';
  try { const d = await api('/stacking/tonight' + query()); heroText(d); body = full ? content(d) : compact(d); }
  catch (e) { body = `<p class="mut">Nachtplanung gerade nicht verfügbar: ${esc(e.message)}</p>`; }
  return `<div class="tn-home"><div class="hhead">${icon('star')}<div><h2>Heute Nacht</h2></div><span class="grow"></span><a class="linkbtn" href="#/nacht">Volle Planung ${icon('arrow')}</a></div>${body}</div>`;
}
async function nightView(el, catalogId) {
  crumbs([['Übersicht', '#/'], ['Heute Nacht']]);
  const req = ++nightReq;
  el.querySelector('.tn-main')?.classList.add('tn-loading');
  const [d, sites] = await Promise.all([api('/stacking/tonight' + query(nightIx)), api('/sites?nights=false').catch(() => null)]);
  if (!el.isConnected || req !== nightReq) return;
  nightView.el = el; nightView.catalogId = catalogId;
  const selected = catalogId && [...(d.recommendations || []), ...(d.suggestions || []), ...(d.soon || [])].find(x => x.id === catalogId);
  const tw = d.twilight;
  el.innerHTML = `<div class="pagehead"><div><h1>Heute Nacht</h1><p class="mut">Empfehlungen aus deiner Bibliothek und dem zuletzt verwendeten Gerät.</p></div>${sitePicker(sites, d)}</div>
    ${selected ? `<section class="panel"><h2>${esc(selected.name)}</h2><p>${esc(selected.kind_label)} · Durchmesser ca. ${num(selected.size)}′${selected.mag != null ? ' · ' + num(selected.mag) + ' mag' : ''} ${selected.max_alt != null ? ' · max. ' + Math.round(selected.max_alt) + '°' : ''}</p>${selected.good?.length ? `<p>${selected.good.map(period).join(', ')} über ${d.min_alt}°</p>` : ''}<ul class="tn-why">${(selected.reasons || []).map(r => `<li>${esc(r)}</li>`).join('')}</ul>${AF.on('sage') ? `<a href="#/sage/wiki">In der Enzyklopädie recherchieren</a>` : ''}</section>` : ''}
    <section class="panel tn-main">${content(d, catalogId, true)}</section>
    ${d.soon?.length ? `<section class="panel"><h2>Bald gut sichtbar</h2><ul class="tn-why">${d.soon.map(x => `<li><a ${targetLink({...x, category: 'new'})}>${esc(x.name)}</a> <span class="mut sm">${esc(x.kind_label)}${x.mag != null ? ' · ' + num(x.mag) + ' mag' : ''} · ab etwa ${esc(x.season.from)}</span></li>`).join('')}</ul><p class="mut sm">Stehen heute zu kurz im Dunkeln, werden aber in den nächsten Wochen besser.</p></section>` : ''}
    ${hiddenIds().length ? `<p class="mut sm">${hiddenIds().length} Ziel(e) ausgeblendet. <button type="button" class="tn-hide" data-tn-reset>Alle wieder zeigen</button></p>` : ''}
    ${d.site ? `<section class="panel"><h2>Dämmerung und Mond</h2><div class="tn-facts"><p>Sonnenuntergang: ${hm(tw.sunset)}<br>Nautische Dämmerung: ${hm(tw.nautical)}<br>Astronomisch dunkel: ${period([tw.dark_start, tw.dark_end])}<br>Sonnenaufgang: ${hm(tw.sunrise)}</p>
      <p>Mondaufgang: ${hm(d.moon.rise)}<br>Monduntergang: ${hm(d.moon.set)}<br>Dunkel ohne Mond: ${d.moon.moonless.length ? d.moon.moonless.map(period).join(', ') : 'keine Zeit'}</p></div></section>
      <section class="panel"><h2>Wetter</h2>${d.weather?.hours?.length ? `<p class="mut sm">${esc(d.weather.source)}</p><div class="tn-weather">${d.weather.hours.map(h => `<span>${hm(h.t)}<br>${h.cloud ?? '–'} % Wolken</span>`).join('')}</div>` : `<p class="mut">${esc(d.weather_error || (d.weather_on ? 'Keine Vorhersage für diese Nacht.' : 'Wettervorhersage ist aus. Unter Einstellungen → Bilder → Bild-Tutor → Wetter einschalten.'))}</p>`}</section>` : ''}`;
}
document.addEventListener('change', async e => {
  if (e.target.id !== 'tnSite') return;
  const sel = e.target;
  try {
    await send('/sites/night', { night: sel.dataset.night, site_id: sel.value || null, nights: false });
    toast(sel.value ? `${sel.getAttribute('aria-label').replace('Standort für ', '')}: ${sel.selectedOptions[0].textContent}` : 'Ort für diese Nacht wieder automatisch.', 'ok');
    if (typeof route === 'function') route(); else dispatchEvent(new HashChangeEvent('hashchange'));
  } catch (err) { toast(err.message, 'err'); }
});
// Andere Nacht: Pfeile, Nachtleiste oder Tastatur ←/→ (nicht in Eingabefeldern)
function goNight(k, focus) {
  const el = nightView.el;
  if (!el?.isConnected || !Number.isFinite(k) || k < 0 || k >= 14 || k === nightIx) return;
  nightIx = k;
  nightView(el, nightView.catalogId).then(() => {
    const b = el.querySelector('.tn-strip [aria-selected="true"]');
    b?.scrollIntoView({block: 'nearest', inline: 'nearest'});
    if (focus) el.querySelector(focus)?.focus({preventScroll: true});
  }).catch(err => toast(err.message, 'err'));
}
document.addEventListener('click', e => {
  const b = e.target.closest('[data-tn-night]');
  if (b && !b.disabled) goNight(+b.dataset.tnNight, b.classList.contains('tn-step') ? `.tn-step.${b.classList.contains('prev') ? 'prev' : 'next'}:not(:disabled)` : '.tn-strip [aria-selected="true"]');
});
document.addEventListener('keydown', e => {
  if (!/^#\/nacht/.test(location.hash) || (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') || e.altKey || e.ctrlKey || e.metaKey || e.shiftKey) return;
  if (e.target.closest?.('input, select, textarea, [contenteditable], dialog[open]')) return;
  e.preventDefault();
  goNight(nightIx + (e.key === 'ArrowRight' ? 1 : -1), e.target.closest?.('.tn-strip') ? '.tn-strip [aria-selected="true"]' : null);
});
addEventListener('hashchange', () => { if (!/^#\/nacht/.test(location.hash)) nightIx = 0; });
document.addEventListener('click', e => {
  const hide = e.target.closest('[data-tn-hide]'), reset = e.target.closest('[data-tn-reset]');
  if (!hide && !reset) return;
  setHidden(hide ? [...hiddenIds(), hide.dataset.tnHide] : []);
  if (typeof route === 'function') route(); else dispatchEvent(new HashChangeEvent('hashchange'));
  if (hide) toast('Ausgeblendet – unter „Heute Nacht“ wieder einblendbar.');
});
document.addEventListener('click', async e => {
  const link = e.target.closest('[data-night-catalog]');
  if (!link || e.ctrlKey || e.metaKey || e.shiftKey || e.altKey) return;
  e.preventDefault();
  try {
    const card = await api('/sage/kb/card?name=' + encodeURIComponent(link.dataset.nightCatalog));
    if (!link.isConnected) return;
    location.hash = card.found ? '#/sage/o/' + card.id : '#/nacht/' + link.dataset.nightCatalog;
  } catch (err) { toast(err.message, 'err'); }
});
const m = AF.modules.stacking;
m.homeFeatured = featured;
m.nav.push({href: '#/nacht', label: 'Heute Nacht', icon: 'star', key: 'tonight', parent: 'home'});
m.routes.push([/^\/nacht(?:\/([A-Za-z0-9]+))?$/, 'tonight', nightView]);
})();
