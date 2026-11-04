'use strict';
/* Grundlage: Nachtgrafik aus dem pausierten UI-Umbau; Empfehlungen jetzt auch auf der Übersicht. */
(() => {
const COLORS = ['var(--acc)', 'var(--acc3)', 'var(--acc2)', 'var(--tn-orange)', 'var(--tn-pink)', 'var(--tn-blue)'];
const hm = t => t == null ? '–' : new Date(t * 1000).toLocaleTimeString('de-DE', {hour: '2-digit', minute: '2-digit'});
const period = s => s ? `${hm(s[0])} – ${hm(s[1])}` : '–';
const targetLink = o => `href="${esc(o.href)}"${o.category === 'new' && AF.on('sage') ? ` data-night-catalog="${o.id}"` : ''}`;
const HID = 'af.tonight.hidden';        // „nicht interessiert“: nur lokal im Browser
const hiddenIds = () => { try { const a = JSON.parse(localStorage.getItem(HID) || '[]'); return Array.isArray(a) ? a.map(String) : []; } catch { return []; } };
const setHidden = a => { try { localStorage.setItem(HID, JSON.stringify([...new Set(a)])); } catch {} };
const query = () => { const h = hiddenIds(); return h.length ? '?hide=' + encodeURIComponent(h.join(',')) : ''; };
const num = v => String(v).replace('.', ',');
const noSite = () => `<p>Für die Nachtplanung fehlt dein Beobachtungsort.</p><a class="btn ghost sm" href="#/settings/stacking">Standort im Bild-Tutor eintragen</a>`;

function chart(d, W, H, selected, hidden) {
  const L = 34, R = 12, T = 18, B = 30;
  const t0 = d.times[0], t1 = d.times[d.times.length - 1];
  const X = t => L + (t - t0) / (t1 - t0) * (W - L - R);
  const Y = a => T + (90 - Math.max(0, a)) / 90 * (H - T - B);
  const path = a => a.map((v, i) => `${i ? 'L' : 'M'}${X(d.times[i]).toFixed(1)} ${Y(v).toFixed(1)}`).join('');
  let g = '';
  d.times.slice(0, -1).forEach((t, i) => {
    const a = d.sun[i], fill = a <= -18 ? 'var(--bg)' : a <= -12 ? 'var(--panel2)' : 'var(--line2)';
    g += `<rect x="${X(t)}" y="${T}" width="${X(d.times[i + 1]) - X(t) + .2}" height="${H - T - B}" fill="${fill}"/>`;
  });
  for (const a of [0, 30, 60, 90]) {
    g += `<line x1="${L}" x2="${W - R}" y1="${Y(a)}" y2="${Y(a)}" stroke="var(--mut)" stroke-opacity=".3" ${a === d.min_alt ? 'stroke-dasharray="3 4"' : ''}/>
      <text x="${L - 5}" y="${Y(a) + 4}" text-anchor="end" fill="var(--mut)">${a}°</text>`;
  }
  if (d.cloud) d.cloud.slice(0, -1).forEach((c, i) => {
    if (c != null) g += `<rect x="${X(d.times[i])}" y="${H - B - 8}" width="${X(d.times[i + 1]) - X(d.times[i]) + .2}" height="8" fill="var(--tn-blue)" opacity="${(c / 100 * .75).toFixed(2)}"><title>${c} % Wolken</title></rect>`;
  });
  g += `<path d="${path(d.moon.alt)}" fill="none" stroke="var(--mut)" stroke-width="1.5" stroke-dasharray="2 5"><title>Mondhöhe</title></path>`;
  d.recommendations.forEach((o, k) => {
    if (!hidden.has(k)) g += `<path class="tn-series" data-target="${k}" d="${path(o.alt)}" fill="none" stroke="${COLORS[k]}" stroke-width="${selected === k ? 3.5 : 2}" opacity="${selected === k ? 1 : .65}"><title>${esc(o.name)}</title></path>`;
  });
  if (d.now >= t0 && d.now <= t1) g += `<line x1="${X(d.now)}" x2="${X(d.now)}" y1="${T}" y2="${H - B}" stroke="var(--fg)" stroke-opacity=".5" stroke-dasharray="4 4"><title>Jetzt</title></line>`;
  const step = W < 500 ? 4 : 2;
  for (let t = Math.ceil(t0 / 3600) * 3600; t < t1; t += 3600) {
    if (new Date(t * 1000).getHours() % step === 0 && X(t) > L + 15 && X(t) < W - R - 15)
      g += `<text x="${X(t)}" y="${H - 8}" text-anchor="middle" fill="var(--mut)">${hm(t)}</text>`;
  }
  return `<svg class="tn-chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="Höhenverlauf empfohlener Ziele und des Mondes heute Nacht">${g}<g class="tn-cursor"></g></svg>`;
}

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
        const r = svg.getBoundingClientRect(), a = 90 * (1 - (e.clientY - r.top - 18) / (this.height - 48));
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
    const r = svg.getBoundingClientRect(), f = Math.max(0, Math.min(1, (e.clientX - r.left - 34) / (r.width - 46)));
    this.index = Math.round(f * (this.plan.times.length - 1));
  }
  draw() {
    const d = this.plan, o = d.recommendations[this.selected];
    const open = this.querySelector('.tn-target')?.open;
    this.innerHTML = `<div class="tn-stage">${chart(d, this.width, this.height, this.selected, this.hiddenTargets)}<div class="tn-tooltip" hidden></div></div>
      <div class="tn-scrub"><output class="tn-clock">${hm(d.times[this.index])}</output><input class="tn-time" type="range" min="0" max="${d.times.length - 1}" value="${this.index}" aria-label="Uhrzeit im Höhenverlauf" aria-valuetext="${hm(d.times[this.index])}"><span class="mut sm">${Math.round(d.moon.illum)} % Mond</span></div>
      <div class="tn-choices">${d.recommendations.map((x, k) => `<div class="tn-choice" style="--target:${COLORS[k]}"><input type="checkbox" data-visible="${k}" ${this.hiddenTargets.has(k) ? '' : 'checked'} aria-label="Kurve ${esc(x.name)} anzeigen"><button type="button" data-select="${k}" aria-pressed="${this.selected === k}">${esc(x.name)}<small>${x.category === 'more' ? 'Mehr Zeit' : 'Neu'}${x.season?.kind === 'last' ? ' · letzte Chance' : ''}</small></button></div>`).join('')}</div>
      ${o ? `<div class="tn-selection"><a ${targetLink(o)}>Objekt öffnen ${icon('arrow')}</a><span class="mut sm">${o.good.map(period).join(', ')} · max. ${Math.round(o.max_alt)}°${o.exposure ? ' · bisher ' + dur(o.exposure) : ''}</span>
        <button type="button" class="tn-hide" data-tn-hide="${esc(o.id)}" title="Dieses Ziel nicht mehr vorschlagen">Nicht interessiert</button>
        ${o.category === 'more' && o.gain ? `<p class="tn-gain">${esc(o.gain.text)}</p>` : ''}${o.season ? `<p class="tn-tag ${o.season.kind}">${esc(o.season.text)}</p>` : ''}
        <details class="tn-target" ${open ? 'open' : ''}><summary>${o.category === 'more' ? 'Warum mehr Belichtung?' : 'Warum dieses Ziel?'}</summary><ul class="tn-why">${o.reasons.filter(r => r !== o.season?.text).map(r => `<li>${esc(r)}</li>`).join('')}</ul>${o.category === 'new' ? `<p class="mut sm">${esc(o.benefit)}</p>` : ''}${o.moon_warning ? '<p class="warn sm">Mond zeitweise näher als 40°: schwache Details können leiden.</p>' : ''}</details></div>` : ''}`;
    if (this.pinned) this.inspect();
  }
  inspect() {
    const d = this.plan, i = this.index, W = this.width, H = this.height;
    const x = 34 + i / (d.times.length - 1) * (W - 46), y = a => 18 + (90 - Math.max(0, a)) / 90 * (H - 48);
    let dots = `<line x1="${x}" x2="${x}" y1="18" y2="${H - 30}" stroke="var(--fg)" stroke-opacity=".7"/>`;
    const rows = d.recommendations.map((o, k) => {
      if (this.hiddenTargets.has(k)) return '';
      if (o.alt[i] >= 0) dots += `<circle cx="${x}" cy="${y(o.alt[i])}" r="4" fill="${COLORS[k]}" stroke="var(--panel-s)"/>`;
      return `<span style="--target:${COLORS[k]}">${esc(o.name)}<b>${Math.round(o.alt[i])}°</b></span>`;
    }).join('');
    this.querySelector('.tn-cursor').innerHTML = dots;
    const tip = this.querySelector('.tn-tooltip');
    tip.hidden = false;
    tip.innerHTML = `<b>${hm(d.times[i])}</b>${rows}<span>Mond<b>${Math.round(d.moon.alt[i])}°</b></span>`;
    tip.style.left = Math.max(4, Math.min(W - Math.min(260, W - 8) - 4, x + 12)) + 'px';
    const slider = this.querySelector('.tn-time');
    slider.value = i; slider.setAttribute('aria-valuetext', hm(d.times[i]));
    this.querySelector('.tn-clock').textContent = hm(d.times[i]);
  }
  disconnectedCallback() { this.observer?.disconnect(); window.removeEventListener('resize', this.resizeChart); }
});
const graph = (d, initial_target) => `<af-tonight-chart>${esc(JSON.stringify({...d, initial_target, now: Math.floor(d.now / 300) * 300}))}</af-tonight-chart>`;
const planList = (d, full) => d.plan?.length ? `<div class="tn-plan"><h3>Nachtplan</h3><ol>${d.plan.map(b => `<li><span class="tn-when">${hm(b.start)} – ${hm(b.end)}</span> <a href="${esc(b.href)}">${esc(b.name)}</a> <small class="mut">${b.category === 'more' ? 'Mehr Zeit' : 'Neu'}${b.cloud != null ? ' · ' + b.cloud + ' % Wolken' : ''}</small></li>`).join('')}</ol>${full ? '<p class="mut sm">Ein Smart-Teleskop nimmt nur ein Ziel gleichzeitig auf: Die nutzbare Nacht ist in Abschnitte geteilt, jeder bekommt das Ziel, das dann am besten steht (Höhe, Mond, Wolken, Zenitnähe).</p>' : ''}</div>` : '';
function content(d, initial_target, full) {
  if (!d.site) return `<div class="tn-empty">${noSite()}</div>`;
  const dev = d.device;
  return `<div class="tn-overview"><div><small>Dunkle Zeit</small><b>${dur(d.dark_hours * 3600)}</b></div><div><small>Mond beleuchtet</small><b>${d.moon.illum} %</b></div><div><small>Dein Gerät</small><b>${esc(dev.name)}</b></div></div>
    ${graph(d, initial_target)}<p class="mut sm tn-legend">Dunkel: Sonne unter −18° · Mindesthöhe ${d.min_alt}° · gepunktet: Mond${d.cloud ? ' · blauer Streifen unten: Bewölkung' : ''} · Zeiten in deiner Ortszeit</p>
    ${planList(d, full)}
    ${d.recommendations.length ? '' : `<p class="mut">${d.dark_hours === 0 ? 'Heute gibt es hier keine astronomisch dunkle Zeit.' : 'Für den verbleibenden Teil der Nacht gibt es keine passenden Empfehlungen über 30°.'}</p>`}
    <details class="tn-notes mut sm"><summary>Berechnung &amp; Bildfeld</summary><p>${esc(dev.name)}${dev.fov ? ` · Bildfeld ca. ${dev.fov.map(v => v.toFixed(2).replace('.', ',') + '°').join(' × ')} (${esc(dev.source)})` : ' · Bildfeld unbekannt, Größenfilter aus'}</p><p>Neue Ziele: Messier- und Caldwell-Katalog (fest eingebaut)${d.catalog?.astap_found ? ' plus auffällige Objekte aus ASTAPs deep_sky.csv (ohne Helligkeitsangabe, nach Größe und Name gewählt)' : ' – ohne ASTAPs deep_sky.csv keine weiteren Objekte'}. Helligkeitsgrenze für dieses Gerät etwa ${num(d.catalog?.mag_limit ?? 11)} mag; Größe 3–90 % der kurzen Bildfeldseite. ${d.catalog?.candidates ?? 0} Kandidaten passen zu Gerät und Bildfeld.</p><p>Rangfolge: nutzbare Stunden in der dunklen Zeit, gewichtet mit Mond (Phase × Abstand), Wolken (falls die Vorhersage im Tutor an ist) und – bei Alt-Az-Geräten – Zenitnähe über 80° (Feldrotation); dazu Kulmination im Dunkeln und Saison. „Mehr Zeit“ rechnet das Rauschen nach der 1/√Zeit-Regel. Freier Horizont wird vorausgesetzt.</p></details>`;
}
async function featured() {
  let body;
  try { body = content(await api('/stacking/tonight' + query())); }
  catch (e) { body = `<p class="mut">Nachtplanung gerade nicht verfügbar: ${esc(e.message)}</p>`; }
  return `<div class="tn-home"><div class="hhead">${icon('star')}<div><h2>Empfohlene Ziele für heute Nacht</h2></div><span class="grow"></span><a class="linkbtn" href="#/nacht">Planung öffnen ${icon('arrow')}</a></div>${body}</div>`;
}
async function nightView(el, catalogId) {
  crumbs([['Übersicht', '#/'], ['Heute Nacht']]);
  const d = await api('/stacking/tonight' + query());
  if (!el.isConnected) return;
  const selected = catalogId && [...(d.recommendations || []), ...(d.suggestions || []), ...(d.soon || [])].find(x => x.id === catalogId);
  const tw = d.twilight;
  el.innerHTML = `<div class="pagehead"><div><h1>Heute Nacht</h1><p class="mut">Empfehlungen aus deiner Bibliothek und dem zuletzt verwendeten Gerät.</p></div></div>
    ${selected ? `<section class="panel"><h2>${esc(selected.name)}</h2><p>${esc(selected.kind_label)} · Durchmesser ca. ${num(selected.size)}′${selected.mag != null ? ' · ' + num(selected.mag) + ' mag' : ''} ${selected.max_alt != null ? ' · max. ' + Math.round(selected.max_alt) + '°' : ''}</p>${selected.good?.length ? `<p>${selected.good.map(period).join(', ')} über ${d.min_alt}°</p>` : ''}<ul class="tn-why">${(selected.reasons || []).map(r => `<li>${esc(r)}</li>`).join('')}</ul>${AF.on('sage') ? `<a href="#/sage/wiki">In der Enzyklopädie recherchieren</a>` : ''}</section>` : ''}
    <section class="panel">${content(d, catalogId, true)}</section>
    ${d.soon?.length ? `<section class="panel"><h2>Bald gut sichtbar</h2><ul class="tn-why">${d.soon.map(x => `<li><a ${targetLink({...x, category: 'new'})}>${esc(x.name)}</a> <span class="mut sm">${esc(x.kind_label)}${x.mag != null ? ' · ' + num(x.mag) + ' mag' : ''} · ab etwa ${esc(x.season.from)}</span></li>`).join('')}</ul><p class="mut sm">Stehen heute zu kurz im Dunkeln, werden aber in den nächsten Wochen besser.</p></section>` : ''}
    ${hiddenIds().length ? `<p class="mut sm">${hiddenIds().length} Ziel(e) ausgeblendet. <button type="button" class="tn-hide" data-tn-reset>Alle wieder zeigen</button></p>` : ''}
    ${d.site ? `<section class="panel"><h2>Dämmerung und Mond</h2><div class="tn-facts"><p>Sonnenuntergang: ${hm(tw.sunset)}<br>Nautische Dämmerung: ${hm(tw.nautical)}<br>Astronomisch dunkel: ${period([tw.dark_start, tw.dark_end])}<br>Sonnenaufgang: ${hm(tw.sunrise)}</p>
      <p>Mondaufgang: ${hm(d.moon.rise)}<br>Monduntergang: ${hm(d.moon.set)}<br>Dunkel ohne Mond: ${d.moon.moonless.length ? d.moon.moonless.map(period).join(', ') : 'keine Zeit'}</p></div></section>
      <section class="panel"><h2>Wetter</h2>${d.weather?.hours?.length ? `<p class="mut sm">${esc(d.weather.source)}</p><div class="tn-weather">${d.weather.hours.map(h => `<span>${hm(h.t)}<br>${h.cloud ?? '–'} % Wolken</span>`).join('')}</div>` : `<p class="mut">${esc(d.weather_error || (d.weather_on ? 'Keine Vorhersage für diese Nacht.' : 'Wettervorhersage ist aus. Unter Einstellungen → Bilder → Bild-Tutor → Wetter einschalten.'))}</p>`}</section>` : ''}`;
}
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
