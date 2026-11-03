'use strict';
/* Grundlage: Nachtgrafik aus dem pausierten UI-Umbau; Empfehlungen jetzt auch auf der Übersicht. */
(() => {
const COLORS = ['var(--acc)', 'var(--acc3)', 'var(--acc2)', 'var(--tn-orange)', 'var(--tn-pink)', 'var(--tn-blue)'];
const hm = t => t == null ? '–' : new Date(t * 1000).toLocaleTimeString('de-DE', {hour: '2-digit', minute: '2-digit'});
const period = s => s ? `${hm(s[0])} – ${hm(s[1])}` : '–';
const targetLink = o => `href="${esc(o.href)}"${o.category === 'new' && AF.on('sage') ? ` data-night-catalog="${o.id}"` : ''}`;
const noSite = () => `<p>Für die Nachtplanung fehlt dein Beobachtungsort.</p><a class="btn ghost sm" href="#/settings/stacking">Standort im Bild-Tutor eintragen</a>`;

function chart(d, W) {
  const H = 250, L = 32, R = 12, T = 18, B = 30;
  const t0 = d.times[0], t1 = d.times[d.times.length - 1];
  const X = t => L + (t - t0) / (t1 - t0) * (W - L - R);
  const Y = a => T + (90 - Math.max(0, a)) / 90 * (H - T - B);
  const path = a => a.map((v, i) => `${i ? 'L' : 'M'}${X(d.times[i]).toFixed(1)} ${Y(v).toFixed(1)}`).join('');
  let g = '';
  // Die gemessene Sonnenhöhe bildet auch Polartag/-nacht und fehlende Dämmerungskreuzungen ab.
  d.times.slice(0, -1).forEach((t, i) => {
    const a = d.sun[i], fill = a <= -18 ? 'var(--bg)' : a <= -12 ? 'var(--panel2)' : 'var(--line2)';
    g += `<rect x="${X(t)}" y="${T}" width="${X(d.times[i + 1]) - X(t) + .2}" height="${H - T - B}" fill="${fill}"/>`;
  });
  for (const a of [0, 30, 60, 90]) {
    g += `<line x1="${L}" x2="${W - R}" y1="${Y(a)}" y2="${Y(a)}" stroke="var(--mut)" stroke-opacity=".3" ${a === d.min_alt ? 'stroke-dasharray="3 4"' : ''}/>
      <text x="${L - 5}" y="${Y(a) + 4}" text-anchor="end" fill="var(--mut)">${a}°</text>`;
  }
  g += `<path d="${path(d.moon.alt)}" fill="none" stroke="var(--mut)" stroke-width="1.5" stroke-dasharray="2 5"><title>Mondhöhe</title></path>`;
  d.recommendations.forEach((o, k) => {
    g += `<a ${targetLink(o)}><path d="${path(o.alt)}" fill="none" stroke="${COLORS[k]}" stroke-width="2.5"><title>${esc(o.name)}: ${esc(o.reasons.join(' '))}</title></path></a>`;
  });
  if (d.now >= t0 && d.now <= t1) g += `<line x1="${X(d.now)}" x2="${X(d.now)}" y1="${T}" y2="${H - B}" stroke="var(--fg)" stroke-dasharray="4 4"><title>Jetzt</title></line>`;
  const step = W < 500 ? 4 : 2;
  for (let t = Math.ceil(t0 / 3600) * 3600; t < t1; t += 3600) {
    if (new Date(t * 1000).getHours() % step === 0 && X(t) > L + 15 && X(t) < W - R - 15)
      g += `<text x="${X(t)}" y="${H - 8}" text-anchor="middle" fill="var(--mut)">${hm(t)}</text>`;
  }
  return `<svg class="tn-chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="Höhenverlauf empfohlener Ziele und des Mondes heute Nacht">${g}</svg>`;
}

// Eigener Lebenszyklus: Größenänderungen zeichnen neu; entfernte Karten geben den Observer frei.
customElements.define('af-tonight-chart', class extends HTMLElement {
  connectedCallback() {
    this.plan = JSON.parse(this.textContent);
    this.textContent = '';
    this.observer = new ResizeObserver(() => {
      const w = Math.round(this.clientWidth);
      if (w >= 100 && w !== this.width) { this.width = w; this.innerHTML = chart(this.plan, w); }
    });
    this.observer.observe(this);
  }
  disconnectedCallback() { this.observer?.disconnect(); }
});
const graph = d => `<af-tonight-chart>${esc(JSON.stringify({...d, now: Math.floor(d.now / 300) * 300}))}</af-tonight-chart>`;
const cards = d => `<div class="tn-recommendations">${d.recommendations.map((o, k) => `<article class="tn-target" style="--target:${COLORS[k]}">
  <small>${o.category === 'more' ? 'Mehr Belichtung sammeln' : 'Noch nicht aufgenommen'}</small>
  <h3><a ${targetLink(o)}>${esc(o.name)}</a></h3><p>${esc(o.reasons.join(' '))}</p>
  <p class="mut sm">${o.good.map(period).join(', ')} · ${dur(o.good_hours * 3600)} über ${d.min_alt}° · max. ${Math.round(o.max_alt)}°</p>
  ${o.exposure ? `<p class="mut sm">Bisher belichtet: ${dur(o.exposure)}</p>` : ''}
  <p class="mut sm">${esc(o.benefit)}</p>${o.moon_warning ? '<p class="warn sm">Mond zeitweise näher als 40°: schwache Details können leiden.</p>' : ''}</article>`).join('')}</div>`;
function content(d) {
  if (!d.site) return `<div class="tn-empty">${noSite()}</div>`;
  const dev = d.device;
  return `<p class="mut sm">Dunkle Zeit: ${dur(d.dark_hours * 3600)} · Mond ${d.moon.illum} % beleuchtet · ${esc(dev.name)}${dev.fov ? ` · Bildfeld ca. ${dev.fov.map(v => v.toFixed(2).replace('.', ',') + '°').join(' × ')} (${esc(dev.source)})` : ' · Bildfeld unbekannt, Größenfilter aus'}</p>
    ${graph(d)}<p class="mut sm tn-legend">Dunkler Hintergrund: Sonne unter −18° · gestrichelte Linie: Mindesthöhe ${d.min_alt}° · gepunktet: Mond · Zeiten auf diesem Gerät</p>
    ${d.recommendations.length ? cards(d) : `<p class="mut">${d.dark_hours === 0 ? 'Heute gibt es hier keine astronomisch dunkle Zeit.' : 'Für den verbleibenden Teil der Nacht gibt es keine passenden Empfehlungen über 30°.'}</p>`}
    <p class="mut sm">Auswahl aus dem eingebauten Katalog; die Liste ist kein vollständiger Messier-Katalog. Freier Horizont und klares Wetter werden vorausgesetzt.</p>`;
}
async function featured() {
  let body;
  try { body = content(await api('/stacking/tonight')); }
  catch (e) { body = `<p class="mut">Nachtplanung gerade nicht verfügbar: ${esc(e.message)}</p>`; }
  return `<div class="tn-home"><div class="hhead">${icon('star')}<div><h2>Empfohlene Ziele für heute Nacht</h2></div><span class="grow"></span><a class="linkbtn" href="#/nacht">Planung öffnen ${icon('arrow')}</a></div>${body}</div>`;
}
async function nightView(el, catalogId) {
  crumbs([['Übersicht', '#/'], ['Heute Nacht']]);
  const d = await api('/stacking/tonight');
  if (!el.isConnected) return;
  const selected = catalogId && [...(d.recommendations || []), ...(d.suggestions || [])].find(x => x.id === catalogId);
  const tw = d.twilight;
  el.innerHTML = `<div class="pagehead"><div><h1>Heute Nacht</h1><p class="mut">Empfehlungen aus deiner Bibliothek und dem zuletzt verwendeten Gerät.</p></div></div>
    ${selected ? `<section class="panel"><h2>${esc(selected.name)}</h2><p>${esc(selected.kind_label)} · Durchmesser ca. ${selected.size}′ · max. ${Math.round(selected.max_alt)}°</p><p>${selected.good.map(period).join(', ')} über ${d.min_alt}°</p><p class="mut">Katalogdaten aus dem eingebauten AstroFlow-Katalog.</p>${AF.on('sage') ? `<a href="#/sage/wiki">In der Enzyklopädie recherchieren</a>` : ''}</section>` : ''}
    <section class="panel">${content(d)}</section>
    ${d.site ? `<section class="panel"><h2>Dämmerung und Mond</h2><div class="tn-facts"><p>Sonnenuntergang: ${hm(tw.sunset)}<br>Nautische Dämmerung: ${hm(tw.nautical)}<br>Astronomisch dunkel: ${period([tw.dark_start, tw.dark_end])}<br>Sonnenaufgang: ${hm(tw.sunrise)}</p>
      <p>Mondaufgang: ${hm(d.moon.rise)}<br>Monduntergang: ${hm(d.moon.set)}<br>Dunkel ohne Mond: ${d.moon.moonless.length ? d.moon.moonless.map(period).join(', ') : 'keine Zeit'}</p></div></section>
      <section class="panel"><h2>Wetter</h2>${d.weather?.hours?.length ? `<p class="mut sm">${esc(d.weather.source)}</p><div class="tn-weather">${d.weather.hours.map(h => `<span>${hm(h.t)}<br>${h.cloud ?? '–'} % Wolken</span>`).join('')}</div>` : `<p class="mut">${esc(d.weather_error || (d.weather_on ? 'Keine Vorhersage für diese Nacht.' : 'Wettervorhersage ist aus. Unter Einstellungen → Bilder → Bild-Tutor → Wetter einschalten.'))}</p>`}</section>` : ''}`;
}
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
m.nav.push({href: '#/nacht', label: 'Heute Nacht', icon: 'star', key: 'tonight', sec: true});
m.routes.push([/^\/nacht(?:\/([A-Za-z0-9]+))?$/, 'tonight', nightView]);
})();
