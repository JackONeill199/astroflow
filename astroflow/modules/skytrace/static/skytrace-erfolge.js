'use strict';
/* SkyTrace „Erfolge“: eigene Datei, hängt sich in die Registrierung von skytrace.js (Route, Navigation, Palette, Status-Hinweis).
   Daten und Freischaltung liefert das Backend (achievements.py) – hier nur Anzeige und die kleine Feier für neue Erfolge. */
(() => {
const T = '/skytrace', mod = AF.modules.skytrace;
if (!mod) return;
const nf = new Intl.NumberFormat('de-DE');
const when = (iso) => new Date(iso).toLocaleDateString('de-DE', { day: '2-digit', month: 'short', year: 'numeric' });
const toasted = (() => { try { return new Set(JSON.parse(localStorage.getItem('stx-ach-toasted') || '[]')); } catch { return new Set(); } })();
const keep = () => { try { localStorage.setItem('stx-ach-toasted', JSON.stringify([...toasted].slice(-200))); } catch { /* ohne Speicher egal */ } };

async function achView(el) {
  crumbs([['SkyTrace', '#/skytrace'], ['Erfolge']]);
  let d = null, filter = 'all', sig = '', wasRunning = false, timer = 0;
  el.innerHTML = `<div class="pagehead"><div><h1>SkyTrace-Erfolge</h1>
      <p class="mut">Aus deinen Analysen freigeschaltet – von der ersten Satellitenspur bis zum kosmischen Verkehrsstau.</p></div><span class="grow"></span><div class="seg" id="stx-f"></div></div>
    <div id="stx-body"></div>`;
  const body = $('#stx-body', el), seg = $('#stx-f', el);
  seg.onclick = (e) => { const b = e.target.closest('[data-k]'); if (b) { filter = b.dataset.k; sig = ''; draw(); } };
  function card(a, i) {
    const p = a.progress, pct = Math.round(100 * p.cur / p.goal);
    return `<div class="stx-card ${a.unlocked ? 'on' : ''} ${a.new ? 'fresh' : ''}" style="--i:${i}"><span class="stx-medal">${icon(a.icon)}</span>
      <div><b>${esc(a.title)}</b><small class="mut">${esc(a.description)}</small>
      ${a.joke ? `<small class="stx-joke">„${esc(a.joke)}“</small>` : ''}
      ${a.unlocked ? `<small class="ok">${a.new ? 'Neu! ' : ''}${when(a.unlocked)}</small>`
        : p.goal > 1 ? `<small class="tnum">${nf.format(p.cur)} / ${nf.format(p.goal)}</small><div class="progress static sm"><i style="width:${pct}%"></i></div>` : ''}</div></div>`;
  }
  function draw() {
    if (!d || !el.isConnected) return;
    const s = JSON.stringify([filter, d.unlocked, d.total, d.metrics, d.new]);
    if (s === sig) return;
    sig = s;
    const all = d.achievements, m = d.metrics;
    seg.innerHTML = [['all', 'Alle', all.length], ['on', 'Freigeschaltet', d.unlocked], ['off', 'Offen', all.length - d.unlocked]]
      .map(([k, l, n]) => `<button data-k="${k}" class="${k === filter ? 'on' : ''}">${l} <span class="dim">${n}</span></button>`).join('');
    const shown = all.filter(a => filter === 'all' || (filter === 'on') === !!a.unlocked);
    const next = all.filter(a => !a.unlocked && a.progress.goal > 1).sort((a, b) => b.progress.cur / b.progress.goal - a.progress.cur / a.progress.goal)[0];
    body.innerHTML = `<div class="hstats stx-kpi"><div><b class="tnum">${d.unlocked} <span class="dim sm">/ ${d.total}</span></b><small>Erfolge</small></div>
        <div><b class="tnum">${nf.format(m.sats)}</b><small>Satelliten zugeordnet</small></div><div><b class="tnum">${nf.format(m.starlink)}</b><small>davon Starlink</small></div>
        <div><b class="tnum">${nf.format(m.frames)}</b><small>Aufnahmen analysiert</small></div></div>
      <div class="progress static stx-all" title="${d.unlocked} von ${d.total}"><i style="width:${Math.round(100 * d.unlocked / Math.max(1, d.total))}%"></i></div>
      ${next ? `<p class="mut sm stx-next">${icon(next.icon)}<span><b>Nächster Anlauf:</b> ${esc(next.title)} – ${nf.format(next.progress.cur)} von ${nf.format(next.progress.goal)}</span></p>` : ''}
      ${d.groups.map(g => { const list = shown.filter(a => a.group === g); if (!list.length) return '';
        return `<h3 class="stx-grp">${esc(g)} <span class="dim tnum">${all.filter(a => a.group === g && a.unlocked).length} / ${all.filter(a => a.group === g).length}</span></h3>
          <div class="stx-grid stagger">${list.map(card).join('')}</div>`; }).join('') || '<p class="mut">Nichts in dieser Ansicht.</p>'}
      <p class="dim sm stx-note">Gezählt wird je Beobachtung die neueste vollständige Analyse. Eine Zuordnung ist ein geometrischer Kataloghinweis, keine bestätigte Identität;
        „Starlink“, „R/B“ und „DEB“ stehen so im Satellitenkatalog. Freigeschaltete Erfolge bleiben auch, wenn eine Analyse später gelöscht wird.</p>`;
  }
  async function load() {
    try { d = await api(T + '/achievements'); }
    catch (e) { if (el.isConnected) body.innerHTML = `<div class="panel">${esc(e.message)}</div>`; return; }
    if (!el.isConnected) return;
    sig = '';
    draw();
    if (d.new.length) {            // neue Erfolge: einige Sekunden hervorgehoben, dann als gesehen markieren
      d.new.forEach(id => toasted.add(id)); keep();
      timer = setTimeout(() => { send(T + '/achievements/seen', {}).catch(() => {}); }, 6000);
    }
  }
  await load();
  return {
    async update(st) {
      const r = !!st.modules.skytrace?.running;
      if (wasRunning && !r) { clearTimeout(timer); await load(); }
      wasRunning = r;
    },
    destroy() { clearTimeout(timer); if (d?.new.length) send(T + '/achievements/seen', {}).catch(() => {}); },
  };
}

mod.nav.push({ href: '#/skytrace/erfolge', label: 'Erfolge', icon: 'medal', key: 'skytrace-ach' });
mod.routes.unshift([/^\/skytrace\/erfolge$/, 'skytrace-ach', achView]);
const palette = mod.palette;
mod.palette = async () => [...(palette ? await palette() : []), ['medal', 'SkyTrace-Erfolge', 'Meilensteine und Kurioses aus deinen Analysen', '#/skytrace/erfolge']];
const status = mod.status;
mod.status = (st, badge) => {
  status?.(st, badge);
  const fresh = (st.modules.skytrace?.achievements_new || []).filter(a => !toasted.has(a.id));
  if (!fresh.length || /^#\/skytrace\/erfolge/.test(location.hash)) return;
  fresh.forEach(a => toasted.add(a.id)); keep();
  toast(fresh.length > 2 ? `${fresh.length} neue SkyTrace-Erfolge: ${fresh.slice(0, 2).map(a => a.title).join(', ')} …` : `Neuer SkyTrace-Erfolg: ${fresh.map(a => a.title).join(', ')}`, 'ok');
};
})();
