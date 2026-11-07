'use strict';
/* SkyTrace-Statistik: „Satelliten am Himmel“ – Satelliten je Belichtungsstunde über die Jahre (Starlink/OneWeb getrennt),
   Verteilung der Helligkeiten und hellste Spur. Daten: GET /api/skytrace/statistics/satellites (satellites.py, klein). */
(() => {
const T = '/skytrace';
const nf = new Intl.NumberFormat('de-DE');
const f1 = new Intl.NumberFormat('de-DE', { minimumFractionDigits: 1, maximumFractionDigits: 1 });
const MONTHS = ['Jan', 'Feb', 'Mär', 'Apr', 'Mai', 'Jun', 'Jul', 'Aug', 'Sep', 'Okt', 'Nov', 'Dez'];
const median = (a) => { const s = [...a].sort((x, y) => x - y), n = s.length; return n ? (n % 2 ? s[(n - 1) / 2] : (s[n / 2 - 1] + s[n / 2]) / 2) : null; };

window.stSatellites = async function (host) {
  let d;
  try { d = await api(T + '/statistics/satellites'); } catch { host.innerHTML = ''; return; }
  if (!host.isConnected) return;
  if (!d.cells.some(c => c.n || c.h > 0)) { host.innerHTML = ''; return; }
  let site = d.sites[0]?.key ?? '', unit = 'year';
  host.innerHTML = `<section class="panel sts-sat"><div class="row"><h2>Satelliten am Himmel</h2><span class="grow"></span>
      <select id="ss-site" aria-label="Standort" ${d.sites.length > 1 ? '' : 'hidden'}>${d.sites.map(s => `<option value="${esc(s.key)}">${esc(s.name)}${d.sites.filter(o => o.name === s.name).length > 1 ? ' · ' + esc(s.device) : ''}</option>`).join('')}</select>
      <div class="seg" id="ss-unit"><button type="button" data-u="year" class="on">Jahre</button><button type="button" data-u="month">Monate</button></div></div>
    <div id="ss-body"></div></section>`;
  const body = $('#ss-body', host), sel = $('#ss-site', host), seg = $('#ss-unit', host);
  sel.onchange = () => { site = sel.value; draw(); };
  seg.onclick = (e) => {
    const b = e.target.closest('[data-u]');
    if (!b) return;
    unit = b.dataset.u;
    seg.querySelectorAll('button').forEach(x => x.classList.toggle('on', x === b));
    draw();
  };
  function draw() {
    const cells = d.cells.filter(c => c.site === site);
    const groups = new Map();
    for (const c of cells) {
      const k = unit === 'year' ? String(c.y) : `${c.y}-${String(c.m).padStart(2, '0')}`;
      const g = groups.get(k) || { k, label: unit === 'year' ? String(c.y) : `${MONTHS[c.m - 1]} ${String(c.y).slice(2)}`, h: 0, n: 0, sl: 0, ow: 0 };
      g.h += c.h; g.n += c.n; g.sl += c.sl; g.ow += c.ow;
      groups.set(k, g);
    }
    const rows = [...groups.values()].sort((a, b) => a.k.localeCompare(b.k)).filter(g => g.h >= 0.5);   // unter 30 min Belichtung ist keine Rate sinnvoll
    const rate = (g) => g.n / g.h, max = Math.max(0.1, ...rows.map(rate));
    const mags = cells.flatMap(c => c.mags), allN = cells.reduce((a, c) => a + c.n, 0), allH = cells.reduce((a, c) => a + c.h, 0);
    const lo = mags.length ? Math.floor(Math.min(...mags)) : 3, hi = mags.length ? Math.ceil(Math.max(...mags)) : 12;
    const bins = Array.from({ length: Math.max(1, hi - lo) }, () => 0);
    for (const m of mags) bins[Math.min(bins.length - 1, Math.floor(m - lo))]++;
    const bmax = Math.max(1, ...bins), med = median(mags);
    const best = d.brightest.find(b => b.group === site);
    const sl = cells.reduce((a, c) => a + c.sl, 0), ow = cells.reduce((a, c) => a + c.ow, 0);
    const est = d.zp.estimate;
    const parts = (g) => [['sl', g.sl], ['ow', g.ow], ['ot', g.n - g.sl - g.ow]].filter(x => x[1] > 0).map(([k, n]) => `<i class="ss-${k}" style="flex:${n}"></i>`).join('');
    const err = d.method.errors;
    body.innerHTML = `${rows.length ? `<div class="ss-cols"><div><div class="row sts-legend"><span><i class="ss-sl"></i>Starlink</span><span><i class="ss-ow"></i>OneWeb</span><span><i class="ss-ot"></i>übrige</span></div>
        <div class="ss-bars" style="--n:${rows.length}">${rows.map(g => `<div class="ss-col" title="${esc(`${g.label}: ${nf.format(g.n)} Satelliten in ${f1.format(g.h)} h Belichtung · Starlink ${g.n ? Math.round(100 * g.sl / g.n) : 0} %`)}">
          <span class="tnum sm">${f1.format(rate(g))}</span><div class="ss-bar" style="height:${100 * rate(g) / max}%">${parts(g)}</div><small>${esc(g.label)}</small></div>`).join('')}</div>
        <p class="mut sm ss-cap">Satelliten je Stunde Belichtung (nur Aufnahmen bei Sonne unter −${Math.abs(d.method.night_deg)}°). Insgesamt ${nf.format(allN)} Spuren in ${f1.format(allH)} h${allN ? ` · ${Math.round(100 * sl / allN)} % Starlink${ow ? `, ${Math.round(100 * ow / allN)} % OneWeb` : ''}` : ''}.</p></div>
        <div><b class="sm">Helligkeit</b>${mags.length ? `<div class="ss-hist">${bins.map((n, i) => `<div title="${lo + i}–${lo + i + 1} mag: ${nf.format(n)}"><i style="height:${100 * n / bmax}%"></i><small>${lo + i}</small></div>`).join('')}</div>
          <p class="mut sm ss-cap">Median ${f1.format(med)} mag (${nf.format(mags.length)} Spuren; kleinere Zahl = heller)</p>` : '<p class="mut sm">Keine Helligkeit messbar.</p>'}
          ${best ? `<a class="sts-row ss-best" href="#/skytrace/${encodeURIComponent(best.run)}/kandidaten"><span class="sts-name"><b>Hellste Spur: ${f1.format(best.mag)} mag</b><small class="mut">${esc(best.name || 'NORAD ' + best.norad)} · ${esc(best.night || '')}</small></span><span class="grow"></span>${icon('arrow')}</a>` : ''}</div></div>`
      : '<p class="mut sm">Für diesen Standort liegen noch keine Belichtungszeiten vor.</p>'}
      <p class="dim sm ss-note">Helligkeit = scheinbare Helligkeit im Luminanzband aus Spurfluss, Durchgangszeit der zugeordneten Bahn und Nullpunkt ${d.zp.night || d.zp.device ? 'der Gaia-geeichten Stacks' : 'nach Richtwert'}; Unsicherheit etwa ${String(err.night).replace('.', ',')}–${String(err.estimate).replace('.', ',')} mag${est ? `, ${nf.format(est)} Werte nur nach Richtwert (Schätzung)` : ''}.
        Die Rate hängt vom Bildfeld ab; Geräte werden getrennt gezählt.</p>`;
  }
  draw();
};
})();
