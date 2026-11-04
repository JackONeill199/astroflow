'use strict';
/* Sage 2.0 – lebende Enzyklopädie: Suche, Objektseiten mit 7 Tabs, Wissenskarte beim Klick ins Bild, Wissensnetz,
   „Meine Entdeckungen“, „Frag Sage“ (Chat mit Werkzeugen) und Erzählformate samt Audioführung.
   Erweitert das in sage.js registrierte Modul (Navigation steht dort). API: /api/sage/kb/…; global: window.SageWiki. */
(() => {
const K = '/sage/kb';
const TABS = [['uebersicht', 'Übersicht', 'home'], ['wissen', 'Details', 'book'], ['bilder', 'Bilder & Aufnahmen', 'camera'], ['forschung', 'Forschung & Quellen', 'search'],
  ['beobachtungen', 'Persönliche Daten', 'pin'], ['netz', 'Zusammenhänge', 'orbit'], ['geschichten', 'Geschichten & Audio', 'volume']];
const STATUS = { stub: ['Grundeintrag', ''], catalog: ['Katalogdaten', 'acc'], researched: ['KI-Entwurf', 'warn'], reviewed: ['geprüft', 'ok'] };
const KIND_ICON = { galaxy: 'orbit', emission: 'sparkle', reflection: 'sparkle', planetary: 'target', snr: 'sparkle', globular: 'star',
  open_cluster: 'star', star: 'star', dark: 'globe', concept: 'book', personal: 'camera' };
const ORIGIN = { catalog: 'Fachkatalog', derived: 'berechnet', curated: 'Grundwissen', ai: 'KI-Vorschlag', 'ai+user': 'KI · von dir bestätigt',
  user: 'von dir', personal: 'deine Daten' };
const SRC_KIND = { catalog: 'Katalog', paper: 'Fachartikel', preprint: 'Preprint', curated: 'Grundwissen', personal: 'Deine Daten' };
const NOTE_KIND = { note: 'Notiz', plan: 'Geplante Beobachtung', observation: 'Beobachtung', interest: 'Interesse' };
const INS_ICON = { research: 'book', field: 'search', region: 'globe', targets: 'target' };
const QUESTIONS = ['Was genau sehe ich auf diesem Bild?', 'Warum leuchtet der Nebel blau?', 'Was weiß die Wissenschaft aktuell über dieses Objekt?',
  'Welche Strukturen sind in meiner Aufnahme besonders interessant?', 'Welche meiner Bilder zeigen ähnliche astronomische Prozesse?',
  'Erkläre mir die Entstehung auf wissenschaftlichem Niveau.', 'Was hat sich in der Forschung zu diesem Objekt verändert?'];
// Aufnahmedatum: letzte Nacht der Aufnahme (Versionen derselben Frames sind zusammengefasst), sonst Entwicklungsdatum
const shotDate = (g) => g.nights?.length ? fnight(g.nights[g.nights.length - 1]) : fdate(g.created).slice(0, 8);
const noCite = (s) => (s || '').replace(/\s*\[S\d+\]/g, '');
// Messwert anzeigen: Koordinaten sexagesimal, Dezimalzahlen mit Komma
const fv = (f) => f.key === 'ra' && f.num != null ? sexa(f.num, true) : f.key === 'dec' && f.num != null ? sexa(f.num, false)
  : String(f.value).replace(/(\d)\.(\d)/g, '$1,$2');

// ------------------------------------------------------------ Markdown + Belege --- //
// Belege [S12] werden zu nummerierten Hochzahlen; `cites` nummeriert je Seite in Reihenfolge des ersten Auftretens
function cites() { const m = new Map(); return { n: (id) => { if (!m.has(id)) m.set(id, m.size + 1); return m.get(id); }, map: m }; }
function md(text, C, srcs = {}) {
  const inline = (s) => esc(s).replace(/\*\*(.+?)\*\*/g, '<b>$1</b>').replace(/(^|[^*])\*([^*\n]+?)\*/g, '$1<i>$2</i>')
    .replace(/\s*\[S(\d+)\]/g, (_, id) => C ? `<sup><button class="cite" data-s="${id}" title="${esc(srcs[id]?.title || 'Quelle')}">${C.n(+id)}</button></sup>` : '')
    .replace(/\[(\d+)\]/g, '<sup class="dim">$1</sup>');
  return (text || '').trim().split(/\n{2,}/).map(b => {
    const h = b.match(/^(#{1,4})\s+(.+)$/);
    if (h) return `<h${Math.min(4, h[1].length + 1)}>${inline(h[2])}</h${Math.min(4, h[1].length + 1)}>`;
    if (/^\s*[-*] /.test(b)) return `<ul>${b.split('\n').map(l => `<li>${inline(l.replace(/^\s*[-*] /, ''))}</li>`).join('')}</ul>`;
    return `<p>${inline(b).replace(/\n/g, '<br>')}</p>`;
  }).join('');
}

// ---------------------------------------------------------------- Schwebekarten --- //
let floatEl = null;
function closeFloat() { floatEl?.remove(); floatEl = null; }
document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeFloat(); });
addEventListener('hashchange', closeFloat);
document.addEventListener('pointerdown', (e) => { if (floatEl && !floatEl.contains(e.target)) closeFloat(); }, true);
function floatCard(html, ev, cls = '') {
  closeFloat();
  const el = floatEl = document.createElement('div');
  el.className = 'sw-float ' + cls;
  el.innerHTML = html;
  document.body.append(el);
  el._at = [ev?.clientX ?? innerWidth / 2, ev?.clientY ?? innerHeight / 3];
  place(el);
  return el;
}
// im Fenster halten – auch nachdem sich der Inhalt (nachgeladene Karte) vergrößert hat
function place(el) {
  const r = el.getBoundingClientRect(), [x, y] = el._at;
  el.style.left = Math.max(12, Math.min(innerWidth - r.width - 12, x + 14)) + 'px';
  el.style.top = Math.max(12, Math.min(innerHeight - r.height - 12, y + 10)) + 'px';
}
// Klick auf einen Beleg → Quelle mit Herkunft
document.addEventListener('click', (e) => {
  const b = e.target.closest('.cite');
  if (!b) return;
  e.preventDefault(); e.stopPropagation();
  const s = srcCache[b.dataset.s];
  floatCard(s ? srcHtml(s) : '<div class="mut sm">Quelle nicht geladen.</div>', e, 'sw-srcpop');
});
const srcCache = {};
const srcHtml = (s) => `<div class="row" style="gap:6px"><span class="chip ${s.kind === 'catalog' ? 'acc' : s.kind === 'preprint' ? 'warn' : ''}">${SRC_KIND[s.kind] || s.kind}</span>
    ${s.year ? `<span class="chip">${s.year}</span>` : ''}<span class="grow"></span></div><b class="sw-srct">${esc(s.title)}</b>
  ${s.authors ? `<div class="mut sm">${esc(s.authors)}</div>` : ''}${s.publisher ? `<div class="dim sm">${esc(s.publisher)}</div>` : ''}
  ${s.abstract ? `<p class="sm sw-abs">${esc(s.abstract.slice(0, 420))}${s.abstract.length > 420 ? '…' : ''}</p>` : ''}
  <div class="row sm" style="margin-top:8px">${s.url ? `<a href="${esc(s.url)}" target="_blank" rel="noopener">Quelle öffnen ${icon('arrow')}</a>` : ''}<span class="grow"></span>
    ${s.retrieved ? `<span class="dim">abgerufen ${ago(s.retrieved)}</span>` : ''}</div>`;

// Kompakte Wissenskarte (Klick auf ein Objekt im Bild)
async function card(name, ev, ctx = {}) {
  const el = floatCard(`<div class="row"><span class="spinner"></span><span class="mut sm">${esc(name)} wird nachgeschlagen …</span></div>`, ev, 'sw-kcard');
  let c;
  try { c = await api(`${K}/card?name=${encodeURIComponent(name)}`); } catch (err) { el.innerHTML = `<div class="bad sm">${esc(err.message)}</div>`; return; }
  if (floatEl !== el) return;
  if (!c.found) { el.innerHTML = `<b>${esc(name)}</b><p class="mut sm">Nicht im Katalog. <button class="linkbtn" data-r>In Fachkatalogen suchen</button></p>`; }
  else {
    el.innerHTML = `<div class="sw-kc-h"><span class="sw-kc-i k-${c.af_kind || 'x'}">${icon(KIND_ICON[c.af_kind] || 'star')}</span><div><b>${esc(c.name)}</b>
        <div class="mut sm">${esc(c.otype_label || '')}${c.constellation ? ' · ' + esc(c.constellation) : ''}</div></div></div>
      ${c.facts.length ? `<dl class="sw-kc-f">${c.facts.filter(f => !['Objekttyp', 'Sternbild'].includes(f.label)).map(f => `<dt>${esc(f.label)}</dt><dd>${esc(fv(f))}${f.status === 'conflict' ? ` <span class="warn" title="Messungen widersprechen sich">${icon('alert')}</span>` : ''}</dd>`).join('')}</dl>` : ''}
      ${c.summary && c.status !== 'stub' ? `<p class="sm">${esc(noCite(c.summary)).slice(0, 260)}</p>` : `<p class="sm mut">${c.researching ? '<span class="spinner"></span> Sage ergänzt gerade Katalogdaten …' : 'Noch ein Grundeintrag aus dem lokalen Katalog.'}</p>`}
      ${c.mine.length ? `<div class="sw-kc-mine">${icon('camera')}<span>${esc(c.mine[0].text)}</span></div>` : ''}
      <div class="row" style="margin-top:10px"><a class="btn sm" href="#/sage/o/${c.id}">${icon('book')}Enzyklopädie</a>
        <button class="btn sm ghost" data-ask>${icon('sparkle')}Frag Sage</button>${c.status === 'stub' ? '<button class="btn sm ghost" data-r>Recherchieren</button>' : ''}</div>`;
  }
  place(el);
  el.querySelector('[data-ask]')?.addEventListener('click', () => { closeFloat(); chat({ scope: ctx.image ? 'image:' + ctx.image : 'entity:' + c.id, entity: c.id, image: ctx.image, title: c.name }); });
  el.querySelector('[data-r]')?.addEventListener('click', async () => { closeFloat(); await startResearch(c.found ? c.id : name, true); });
}

// ---------------------------------------------------------------- Aufträge --- //
async function startResearch(name, deep = true) {
  try {
    const t = await send(K + '/research', { name, deep });
    toast(`Sage recherchiert „${name}“ …`, '', { label: 'Aktivität', href: '#/activity' });
    watch(t.id, (r) => {
      if (r.state === 'done') toast(r.message, 'ok', { label: 'Öffnen', href: `#/sage/o/${r.entity}` });
      else toast(r.error || 'Recherche fehlgeschlagen', 'err');
    });
    return t;
  } catch (err) { toast(err.message, 'err'); }
}
const watchers = new Map();
function watch(id, done, step) {
  if (watchers.has(id)) return;
  const tick = async () => {
    let t;
    try { t = await api(`${K}/tasks/${id}`); } catch { watchers.delete(id); return; }
    step?.(t);
    if (t.state === 'running') { watchers.set(id, setTimeout(tick, 1200)); return; }
    watchers.delete(id); done?.(t);
  };
  watchers.set(id, setTimeout(tick, 600));
}
function setTask(host, t) {
  const box = host?.querySelector('.sw-task');
  if (!box) { if (host) host.innerHTML = taskLine(t); return; }
  box.querySelector('.sw-tm').textContent = t.message || '';
  box.querySelector('.progress i').style.width = `${Math.max(5, (t.progress || 0) * 100)}%`;
}
const taskLine = (t) => `<div class="panel sw-task"><div class="row"><span class="spinner"></span><b>${esc(t.title)}</b><span class="grow"></span><span class="mut sm sw-tm">${esc(t.message || '')}</span></div>
  <div class="progress" style="margin-top:10px"><i style="width:${Math.max(5, (t.progress || 0) * 100)}%"></i></div></div>`;

// ---------------------------------------------------------------- Frag Sage --- //
let chatCtx = null, chatLoad = null;
function chat(ctx) {
  chatCtx = { scope: 'global', ...ctx };
  let d = $('#swChat');
  if (!d) {
    d = document.createElement('aside');
    d.id = 'swChat'; d.className = 'sw-chat'; d.setAttribute('aria-label', 'Frag Sage');
    d.innerHTML = `<div class="sw-chat-h">${icon('sparkle')}<div><b>Frag Sage</b><div class="mut sm" id="swChatCtx"></div></div><span class="grow"></span>
        <button class="linkbtn" id="swChatClr" title="Gespräch leeren">Neu</button><button class="sw-x" id="swChatX" aria-label="Schließen">×</button></div>
      <div class="sw-chat-b" id="swChatB"></div>
      <div class="sw-chat-q" id="swChatQ"></div>
      <form class="sw-chat-f" id="swChatF"><textarea id="swChatI" rows="2" placeholder="Frage zu Objekt, Bild oder Forschung …"></textarea><button class="btn" type="submit">${icon('arrow')}</button></form>`;
    document.body.append(d);
    $('#swChatX').onclick = () => d.classList.remove('open');
    $('#swChatClr').onclick = async () => { await send(`${K}/chat?scope=${encodeURIComponent(chatCtx.scope)}`, null, 'DELETE'); chatLoad = loadChat(); };
    $('#swChatI').onkeydown = (e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); $('#swChatF').requestSubmit(); } };
    $('#swChatF').onsubmit = async (e) => {
      e.preventDefault();
      const q = $('#swChatI').value.trim(); if (!q) return;
      $('#swChatI').value = '';
      ask(q);
    };
  }
  $('#swChatCtx').textContent = chatCtx.title ? `zu „${chatCtx.title}“` : 'Enzyklopädie, deine Aufnahmen, Fachkataloge';
  $('#swChatQ').innerHTML = QUESTIONS.slice(0, ctx.image ? 4 : 7).filter((q, i) => ctx.image || i !== 0 && i !== 3).slice(0, 4).map(q => `<button type="button">${esc(q)}</button>`).join('');
  $$('#swChatQ button').forEach(b => { b.onclick = () => ask(b.textContent); });
  d.classList.add('open');
  chatLoad = loadChat();
  setTimeout(() => $('#swChatI').focus(), 250);
}
const chatMsg = (m) => {
  const data = m.data || {};
  data.sources?.forEach(s => { srcCache[s.id] = s; });
  const C = cites(), srcs = Object.fromEntries((data.sources || []).map(s => [s.id, s]));
  return m.role === 'user' ? `<div class="sw-m u">${esc(m.content)}</div>`
    : `<div class="sw-m a">${data.steps?.length ? `<details class="sw-steps"><summary>${icon('search')}Sage hat ${data.steps.length} Werkzeug${data.steps.length > 1 ? 'e' : ''} genutzt</summary>
        ${data.steps.map(s => `<div class="sm"><b>${esc(s.tool)}</b> <span class="mut">${esc(JSON.stringify(s.args || {}).slice(0, 80))}</span> → ${esc(s.summary || '')}</div>`).join('')}</details>` : ''}
      ${md(m.content, C, srcs)}${C.map.size ? `<div class="sw-m-src">${[...C.map].map(([id, n]) => `<button class="cite" data-s="${id}">${n}</button> <span class="sm">${esc(srcs[id]?.title || '')}</span>`).join('<br>')}</div>` : ''}</div>`;
};
async function loadChat() {
  const b = $('#swChatB');
  const hist = await api(`${K}/chat?scope=${encodeURIComponent(chatCtx.scope)}`).catch(() => []);
  b.innerHTML = hist.length ? hist.map(chatMsg).join('') : `<div class="sw-chat-e">${icon('sparkle')}<p>Sage nutzt die Enzyklopädie, deine Aufnahmen, SIMBAD und arXiv.
    Antworten trennen <b>Fakten mit Quelle</b>, <b>deine Daten</b> und <b>Interpretation</b>.</p></div>`;
  b.scrollTop = b.scrollHeight;
}
async function ask(q) {
  await chatLoad;
  const b = $('#swChatB');
  $('.sw-chat-e', b)?.remove();
  b.insertAdjacentHTML('beforeend', chatMsg({ role: 'user', content: q }) + `<div class="sw-m a" id="swThink"><span class="spinner"></span> <span class="mut sm">Sage denkt nach und schlägt nach …</span></div>`);
  b.scrollTop = b.scrollHeight;
  try {
    const r = await send(K + '/chat', { question: q, scope: chatCtx.scope, entity: chatCtx.entity, image: chatCtx.image });
    $('#swThink')?.remove();
    b.insertAdjacentHTML('beforeend', chatMsg({ role: 'assistant', content: r.answer, data: r }));
  } catch (err) {
    $('#swThink')?.remove();
    b.insertAdjacentHTML('beforeend', `<div class="sw-m a bad sm">${esc(err.message)}${/KI eingerichtet/.test(err.message) ? ' <a href="#/settings/sage">Einrichten</a>' : ''}</div>`);
  }
  b.scrollTop = b.scrollHeight;
}

// ---------------------------------------------------------------- Bausteine --- //
const statusChip = (s) => `<span class="chip ${STATUS[s]?.[1] || ''}">${STATUS[s]?.[0] || s}</span>`;
const ph = (k) => `<div class="sw-ph k-${k || 'x'}">${icon(KIND_ICON[k] || 'star')}</div>`;
const entCard = (c, i) => `<a class="sw-card" href="#/sage/o/${c.id}" style="--i:${i}"><div class="sw-thumb">${c.image ? `<img src="${c.image}" alt="" loading="lazy" onload="this.classList.add('ld')">` : ph(c.kind === 'concept' ? 'concept' : c.af_kind)}
    ${c.kind === 'concept' ? '<span class="chip">Grundbegriff</span>' : statusChip(c.status)}${c.observed ? `<span class="sw-mine">${icon('camera')}fotografiert</span>` : ''}</div>
  <div class="body"><h3>${esc(c.name)}</h3><div class="mut sm">${esc(c.otype_label || '')}${c.constellation ? ' · ' + esc(c.constellation) : ''}</div>
    <p class="sm sw-sum">${esc(noCite(c.summary))}</p>${c.distance ? `<div class="foot"><span>${icon('orbit')}${esc(c.distance)}</span>${c.distance_conflict ? '<span class="warn">Messungen streuen</span>' : ''}</div>` : ''}</div></a>`;

// --------------------------------------------------------------- Enzyklopädie --- //
// Startseite als Portal (Vorbild: Hauptseiten von Wikipedia, Telescopius, Stellarium): Suche, Objekt im Fokus
// (zuletzt fotografiert, umschaltbar), „Heute Nacht am Himmel“, neueste Entdeckungen, Stöbern nach Objektart, darunter alle Einträge
const KIND_PL = { galaxy: 'Galaxien', emission: 'Emissionsnebel', reflection: 'Reflexionsnebel', planetary: 'Planetarische Nebel', snr: 'Supernova-Überreste',
  globular: 'Kugelsternhaufen', open_cluster: 'Offene Sternhaufen', star: 'Sterne', dark: 'Dunkelnebel' };
const hhmm = (h) => `${String(Math.floor(h)).padStart(2, '0')}:${String(Math.round((h % 1) * 60) % 60).padStart(2, '0')}`;
async function wikiView(el) {
  crumbs([['Sage', '#/sage/wiki'], ['Enzyklopädie']]);
  let filter = store.get('sage.wf', 'all'), kindF = null, q = '', seq = 0;
  const [all, home, ins] = await Promise.all([api(`${K}/entities?filter=all`), api(`${K}/home`).catch(() => ({ featured: [], tonight: { items: [] } })),
    api(`${K}/insights`).catch(() => ({ items: [] }))]);
  if (!el.isConnected) return;
  let d = filter === 'all' ? all : await api(`${K}/entities?filter=${filter}`);
  const s = all.stats, feat = home.featured;
  let focus = feat.some(f => f.id === store.get('sage.focus')) ? store.get('sage.focus') : feat[0]?.id;
  // Objektarten zum Stöbern: Anzahl + erstes eigenes Bild
  const kinds = {};
  all.items.forEach(i => { if (!KIND_PL[i.af_kind]) return; const k = kinds[i.af_kind] ||= { n: 0, img: null }; k.n++; k.img ||= i.image; });
  const kindList = Object.entries(kinds).sort((a, b) => b[1].n - a[1].n);
  // Neueste Entdeckungen: je Art zuerst die jüngste, damit nicht vier gleichartige Hinweise den Platz füllen
  const news = [], used = new Set();
  ins.items.forEach(x => { if (!used.has(x.kind) && news.length < 6) { used.add(x.kind); news.push(x); } });
  ins.items.forEach(x => { if (news.length < 6 && !news.includes(x)) news.push(x); });
  const tn = home.tonight;
  el.innerHTML = `<div class="sw-home">
    <div class="sw-hhead"><div><div class="eyebrow"><span></span>SAGE · LEBENDE ENZYKLOPÄDIE</div><h1>Enzyklopädie</h1>
        <div class="sw-hstats">${[[s.objects, 'Objekte'], [s.observed, 'fotografiert'], [s.researched + s.reviewed, 'recherchiert'], [s.sources, 'Quellen']]
          .map(([n, l]) => `<span><b>${n.toLocaleString('de-DE')}</b> ${l}</span>`).join('')}</div></div>
      <a class="linkbtn" href="#/sage/netz">${icon('orbit')}Wissensnetz</a></div>
    <div class="sw-wtop"><div class="sw-search"><input type="search" id="swQ" placeholder="Suche in deiner Enzyklopädie – z. B. M45, Plejaden, NGC 7000, Reflexionsnebel" autocomplete="off"></div>
      <button class="btn ghost" id="swAskAll">${icon('sparkle')}Frag Sage</button></div>
    <div id="swSearchRes"></div><div id="swTasks"></div>
    <div id="swPortal">
      ${feat.length ? `<section class="sw-feat" id="swFeat"></section>
        <div class="sw-fstrip">${feat.map(f => `<button data-f="${f.id}" title="${esc(f.name)}">${f.image ? `<img src="${f.image}" alt="" loading="lazy">` : ph(f.af_kind)}<span>${esc(shortName(f.name))}</span></button>`).join('')}</div>`
      : `<section class="sw-hero"><div class="eyebrow"><span></span>DEINE ENZYKLOPÄDIE</div>
        <h1>Der Himmel, deine Bilder, die Forschung – <span class="sw-grad">verbunden</span>.</h1>
        <p class="mut">Fachkataloge liefern die Messwerte, ein recherchierender Agent liest die Literatur, und alles verknüpft sich mit deinen eigenen Aufnahmen. Bestimme ein Bild oder suche oben nach einem Objekt.</p></section>`}
      <div class="sw-hgrid">
        <section class="panel sw-tonight"><div class="sw-ph2">${icon('globe')}<div><h3>Heute Nacht am Himmel</h3>
            <div class="mut sm">Hoch um Mitternacht · ${tn.assumed ? `für ${tn.lat}° N angenommen` : `${String(tn.lat).replace('.', ',')}° Breite`}</div></div></div>
          ${tn.items.length ? `<div class="sw-tn">${tn.items.map((t, i) => `<button class="sw-tnr" data-i="${i}">
              <span class="sw-tni">${t.image ? `<img src="${t.image}" alt="" loading="lazy">` : ph(t.kind)}</span>
              <span class="sw-tnn"><b>${esc(t.name)}</b><span class="mut sm">${esc(t.kind_label || '')}${t.mine ? ` · <span class="sw-tnm">${icon('camera')}fotografiert</span>` : ''}</span></span>
              <span class="sw-tna" title="Größte Höhe über dem Horizont"><span class="sw-alt"><i style="width:${Math.round(t.alt / 90 * 100)}%"></i></span><span class="sm">${t.alt}°</span></span>
              <span class="sw-tnt mut sm" title="Kulmination (mittlere Ortszeit, ungefähr)">${hhmm(t.transit)}</span></button>`).join('')}</div>`
            : '<p class="mut sm">Gerade steht keines der Katalogobjekte günstig.</p>'}
          ${tn.assumed ? `<p class="dim sm sw-pfoot">Genauer mit deinem Standort: <a href="#/settings/sage">Breitengrad eintragen</a></p>` : ''}</section>
        <section class="panel sw-news"><div class="sw-ph2">${icon('sparkle')}<div><h3>Neu entdeckt</h3><div class="mut sm">Sage forscht im Hintergrund weiter</div></div>
            <span class="grow"></span><a class="linkbtn" href="#/sage/entdeckungen">Alle ${icon('arrow')}</a></div>
          ${news.length ? news.map(x => `<a class="sw-nw" href="#/sage/entdeckungen"><span class="sw-nwi">${icon(INS_ICON[x.kind] || 'sparkle')}</span>
              <span><b>${esc(x.title)}</b><span class="mut sm">${ago(x.created)}${x.entities.length ? ' · ' + x.entities.slice(0, 3).map(e => esc(shortName(x.entity_names[e] || e))).join(', ') : ''}</span></span></a>`).join('')
            : `<p class="mut sm">Noch nichts – Sage sucht regelmäßig nach neuen Arbeiten zu deinen Objekten, fehlenden Einträgen und Zusammenhängen.</p>`}</section>
      </div>
      ${kindList.length > 1 ? `<h2 class="sect sw-hsect">Nach Objektart stöbern</h2><div class="sw-kinds stagger">${kindList.map(([k, v], i) => `<button class="sw-kind k-${k}" data-kind="${k}" style="--i:${i}">
          ${v.img ? `<img src="${v.img}" alt="" loading="lazy">` : ph(k)}<span><b>${KIND_PL[k]}</b><span class="sm">${v.n} ${v.n === 1 ? 'Eintrag' : 'Einträge'}</span></span></button>`).join('')}</div>` : ''}
    </div>
    <div class="sw-encl" id="swEncl"><div class="toolbar"><h2 class="sect" style="margin:0">Alle Einträge</h2><span id="swKF"></span><span class="grow"></span>
        <div class="seg" id="swF">${[['all', 'Alle'], ['observed', 'Meine Objekte'], ['ai', 'KI-recherchiert'], ['concepts', 'Grundbegriffe']].map(([k, l]) => `<button data-k="${k}" class="${k === filter ? 'on' : ''}">${l}</button>`).join('')}</div></div>
      <div id="swGrid"></div></div></div>`;
  const showFocus = (fid) => {
    const f = feat.find(x => x.id === fid), host = $('#swFeat');
    if (!f || !host) return;
    focus = fid; store.set('sage.focus', fid);
    $$('.sw-fstrip button').forEach(b => b.classList.toggle('on', b.dataset.f === fid));
    const facts = [[icon('orbit'), f.distance, 'Entfernung'], [icon('camera'), f.nights ? `${f.nights} ${f.nights === 1 ? 'Nacht' : 'Nächte'}` : null, f.hours ? `${String(f.hours).replace('.', ',')} h belichtet` : 'Aufnahmen'],
      [icon('pin'), f.last ? fnight(f.last) : null, 'zuletzt fotografiert'], [icon('globe'), f.constellation, 'Sternbild']].filter(x => x[1]);
    host.innerHTML = `<div class="sw-fbg" style="${f.image ? `background-image:url('${f.image}')` : ''}"></div>
      <a class="sw-fimg" href="#/sage/o/${f.id}">${f.picture ? `<img src="${f.picture}" alt="${esc(f.name)}" onload="this.classList.add('ld')">` : ph(f.af_kind)}</a>
      <div class="sw-ftxt"><div class="eyebrow"><span></span>IM FOKUS · ZULETZT FOTOGRAFIERT</div>
        <h2>${esc(f.name)}</h2><div class="mut">${esc(f.otype_label || '')}${f.constellation ? ' im Sternbild ' + esc(f.constellation) : ''} ${statusChip(f.status)}</div>
        <p class="sw-fsum">${esc(noCite(f.summary)) || '<span class="mut">Noch ohne Beschreibung – Sage kann recherchieren.</span>'}</p>
        <div class="sw-ffacts">${facts.map(([ic, v, l]) => `<div>${ic}<b>${esc(v)}</b><span>${l}</span></div>`).join('')}</div>
        <div class="row sw-fbtn"><a class="btn" href="#/sage/o/${f.id}">${icon('book')}Artikel lesen</a>
          <button class="btn ghost" data-ask>${icon('sparkle')}Frag Sage</button><a class="btn ghost" href="#/sage/netz/${f.id}">${icon('orbit')}Zusammenhänge</a></div></div>`;
    host.querySelector('[data-ask]').onclick = () => chat({ scope: 'entity:' + f.id, entity: f.id, title: f.name });
  };
  const draw = () => {
    const items = d.items, exact = q && items.some(i => i.name.toLowerCase().includes(q.toLowerCase()) && i.kind === 'object');
    const cta = q && !exact ? `<div class="panel sw-cta">${icon('search')}<div><b>„${esc(q)}“ recherchieren</b><div class="mut sm">Sage löst die Kennung in SIMBAD auf, sammelt Messwerte mit Belegen und aktuelle Literatur – mit KI zusätzlich ein belegter Artikel.</div></div>
        <span class="grow"></span><button class="btn" id="swDo">${icon('sparkle')}Recherchieren</button></div>` : '';
    $('#swSearchRes').innerHTML = q ? cta + (items.length ? `<div class="sw-grid stagger" style="margin-bottom:22px">${items.slice(0, 8).map(entCard).join('')}</div>` : '') : '';
    $('#swPortal').hidden = $('#swEncl').hidden = !!q;
    $('#swDo')?.addEventListener('click', async () => { const t = await startResearch(q, true); if (t) showTask(t); });
    if (q) return;
    const shown = kindF ? items.filter(i => i.af_kind === kindF) : items;
    $('#swKF').innerHTML = kindF ? `<button class="chip acc sw-kfx" title="Filter entfernen">${KIND_PL[kindF]} ×</button>` : '';
    const hid = d.stats.hidden_stubs && !kindF ? `<p class="dim sm" style="margin-top:14px">${d.stats.hidden_stubs} Grundeinträge ohne eigenes Bild (z. B. Galaxiengruppen aus SIMBAD) sind ausgeblendet – über die Suche und das Wissensnetz erreichbar.</p>` : '';
    $('#swGrid').innerHTML = shown.length ? `<div class="sw-grid stagger">${shown.map(entCard).join('')}</div>${hid}`
      : items.length ? `<p class="mut">Keine ${KIND_PL[kindF] || 'Einträge'} in dieser Auswahl.</p>`
      : `<div class="empty">${icon('book')}<h2>Die Enzyklopädie ist noch leer</h2>Bestimme ein Bild oder suche oben nach einem Objekt – Sage legt die Einträge an.</div>`;
  };
  const showTask = (t) => {
    const host = document.createElement('div'); $('#swTasks').append(host); host.innerHTML = taskLine(t);
    watch(t.id, async (r) => { host.remove(); if (r.state === 'done') location.hash = `#/sage/o/${r.entity}`; }, (r) => setTask(host, r));
  };
  let timer = 0;
  $('#swQ').oninput = (ev) => { clearTimeout(timer); timer = setTimeout(async () => {
    q = ev.target.value.trim(); const my = ++seq;
    const r = await api(`${K}/entities?filter=${q ? 'all' : filter}&q=${encodeURIComponent(q)}`);
    if (my === seq) { d = r; draw(); } }, 220); };
  $('#swQ').onkeydown = (ev) => { if (ev.key === 'Enter' && $('#swDo')) $('#swDo').click(); else if (ev.key === 'Enter' && q && d.items[0]) location.hash = `#/sage/o/${d.items[0].id}`; };
  $('#swF').onclick = async (ev) => { const b = ev.target.closest('[data-k]'); if (!b) return; filter = b.dataset.k; store.set('sage.wf', filter);
    $$('#swF button').forEach(x => x.classList.toggle('on', x === b)); d = await api(`${K}/entities?filter=${filter}`); q = ''; $('#swQ').value = ''; draw(); };
  $('.sw-fstrip')?.addEventListener('click', (ev) => { const b = ev.target.closest('[data-f]'); if (b && b.dataset.f !== focus) showFocus(b.dataset.f); });
  // Objekt ohne Enzyklopädie-Eintrag → Wissenskarte (legt bei Bedarf eine Recherche an)
  $('.sw-tn')?.addEventListener('click', (ev) => { const b = ev.target.closest('[data-i]'); if (!b) return; const t = tn.items[b.dataset.i];
    if (t.id) location.hash = `#/sage/o/${t.id}`; else card(t.name, ev); });
  $('.sw-kinds')?.addEventListener('click', (ev) => { const b = ev.target.closest('[data-kind]'); if (!b) return; kindF = b.dataset.kind;
    if (filter === 'concepts') $('#swF [data-k="all"]').click(); else draw();
    $('#swEncl').scrollIntoView({ behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' }); });
  $('#swKF').onclick = () => { kindF = null; draw(); };
  $('#swAskAll').onclick = () => chat({ scope: 'global' });
  draw();
  showFocus(focus);
  return { actions: () => [['search', 'Objekt recherchieren', 'Fachkataloge + KI', () => $('#swQ').focus()], ['sparkle', 'Frag Sage', 'Assistent', () => chat({ scope: 'global' })]] };
}

// Himmelskarte: Rektaszension nach links wachsend (Blick nach Süden), Gitter, helle Katalogobjekte, eigene hervorgehoben.
// Mausrad/Pinch zoomt, Ziehen verschiebt, Doppelklick zentriert neu; ⤢ öffnet die große Ansicht.
const mapData = new Map();
async function skyMap(host, id, big = false) {
  const m = mapData.get(id) || await api(`${K}/skymap/${id}?radius=75`).catch(() => null);
  if (!m?.center || !host.isConnected) return;
  mapData.set(id, m);
  let W = 960, H = 600;
  let cra = m.center.ra, cdec = m.center.dec, R = big ? 30 : 18;          // R = halbe Kartenhöhe in Grad
  const draw = () => {
    if (!big) { W = Math.max(240, Math.round((host.clientWidth || 240) / 1.4)); H = Math.round(W * 0.625); }   // Schrift bleibt bei breiter Karte gleich groß
    const k = H / (2 * R), cosd = Math.cos(cdec * Math.PI / 180);
    const pos = (ra, dec) => { const dra = ((ra - cra + 540) % 360) - 180; return [W / 2 - dra * Math.cos(dec * Math.PI / 180) * k, H / 2 - (dec - cdec) * k]; };
    const step = R > 25 ? 15 : R > 8 ? 5 : 1, grid = [];
    let lastY = -1e9, lastX = -1e9;
    for (let dec = Math.ceil((cdec - R) / step) * step; dec <= Math.min(90, cdec + R); dec += step) {
      const y = H / 2 - (dec - cdec) * k, lbl = Math.abs(y - lastY) > 16 && y < H - 16 && y > 12;
      if (lbl) lastY = y;
      grid.push(`<line x1="0" x2="${W}" y1="${y}" y2="${y}"/>${lbl ? `<text x="3" y="${y - 2}">${dec}°</text>` : ''}`); }
    const rs = R > 25 ? 30 : R > 8 ? 15 : 3, span = W / (2 * k * Math.max(cosd, 0.2));
    for (let ra = Math.ceil((cra - span) / rs) * rs; ra <= cra + span; ra += rs) {
      const [x] = pos(ra, cdec); if (!(x > 0 && x < W)) continue;
      const lbl = Math.abs(x - lastX) > 40 && x > 34 && x < W - 26; if (lbl) lastX = x;
      grid.push(`<line y1="0" y2="${H}" x1="${x}" x2="${x}"/>${lbl ? `<text x="${x + 2}" y="${H - 3}">${(((ra % 360) + 360) % 360 / 15).toFixed(rs < 15 ? 1 : 0).replace('.', ',')}h</text>` : ''}`); }
    const named = R < (big ? 40 : 10);
    const pts = m.objects.map(o => { const [x, y] = pos(o.ra, o.dec), me = o.id === m.center.id;
      if (x < -5 || x > W + 5 || y < -5 || y > H + 5) return '';
      return `<g class="${me ? 'me' : o.mine ? 'mine' : ''}"${o.id && !me ? ` data-id="${o.id}"` : ''}><circle cx="${x}" cy="${y}" r="${(me ? 4 : o.mine ? 3 : 1.8) * (big ? 1.6 : 1)}"/>${me || o.mine || named ? `<text x="${x + 6}" y="${y - 5}">${esc(shortName(o.name))}</text>` : ''}<title>${esc(o.name)}</title></g>`; }).join('');
    host.innerHTML = `<svg class="sw-map ${big ? 'big' : ''}" viewBox="0 0 ${W} ${H}"><g class="grid">${grid.join('')}</g>${pts}
        ${m.constellation ? `<text class="con" x="${W - 8}" y="${big ? 24 : 14}" text-anchor="end">${esc(m.constellation)}</text>` : ''}</svg>
      <div class="sw-map-bar"><button data-z="in" title="Vergrößern">+</button><button data-z="out" title="Verkleinern">−</button><button data-z="home" title="Auf Objekt zentrieren">◎</button>
        ${big ? '' : '<button data-z="big" title="Große Karte">⤢</button>'}</div>`;
  };
  const zoom = (f) => { R = Math.min(75, Math.max(1.5, R * f)); draw(); };
  host.classList.add('sw-maphost');
  host.onwheel = (ev) => { ev.preventDefault(); zoom(Math.exp(ev.deltaY * 0.0015)); };
  let drag = null, moved = false, pinch = null;
  const ptrs = new Map();
  host.onpointerdown = (ev) => { if (ev.target.closest('.sw-map-bar')) return; host.setPointerCapture(ev.pointerId); ptrs.set(ev.pointerId, [ev.clientX, ev.clientY]);
    drag = { x: ev.clientX, y: ev.clientY, ra: cra, dec: cdec }; moved = false; if (ptrs.size === 2) { const [a, b] = [...ptrs.values()]; pinch = Math.hypot(a[0] - b[0], a[1] - b[1]); } };
  host.onpointermove = (ev) => {
    if (!drag) return;
    ptrs.set(ev.pointerId, [ev.clientX, ev.clientY]);
    if (ptrs.size === 2 && pinch) { const [a, b] = [...ptrs.values()], d = Math.hypot(a[0] - b[0], a[1] - b[1]); zoom(pinch / d); pinch = d; return; }
    const r = host.querySelector('svg').getBoundingClientRect(), k = H / (2 * R) * r.height / H;
    const dx = ev.clientX - drag.x, dy = ev.clientY - drag.y;
    if (Math.hypot(dx, dy) > 3) moved = true;
    cdec = Math.max(-89, Math.min(89, drag.dec + dy / k));
    cra = drag.ra + dx / k / Math.max(Math.cos(cdec * Math.PI / 180), 0.2);
    draw();
  };
  const end = (ev) => { ptrs.delete(ev.pointerId); if (ptrs.size < 2) pinch = null; if (!ptrs.size) drag = null; };
  host.onpointerup = (ev) => {
    if (!moved) { const g = ev.target.closest?.('[data-id]'); if (g) location.hash = `#/sage/o/${g.dataset.id}`; }
    end(ev);
  };
  host.onpointercancel = end;
  host.onclick = (ev) => {
    const z = ev.target.closest('[data-z]')?.dataset.z; if (!z) return;
    if (z === 'in') zoom(1 / 1.5); else if (z === 'out') zoom(1.5);
    else if (z === 'home') { cra = m.center.ra; cdec = m.center.dec; R = big ? 30 : 18; draw(); }
    else if (z === 'big') mapDialog(id);
  };
  draw();
  if (!big) { let lw = host.clientWidth; new ResizeObserver(() => { if (host.clientWidth !== lw) { lw = host.clientWidth; draw(); } }).observe(host); }
}
function mapDialog(id) {
  let dl = $('#swMapDlg');
  if (!dl) {
    dl = document.createElement('dialog'); dl.id = 'swMapDlg'; dl.className = 'sw-mapdlg';
    dl.innerHTML = `<div class="row"><b>Position am Himmel</b><span class="mut sm">Mausrad zoomt · Ziehen verschiebt · Klick auf ein Objekt öffnet es</span><span class="grow"></span><button class="sw-x" aria-label="Schließen">×</button></div><div id="swMapBig"></div>`;
    document.body.append(dl);
    dl.querySelector('.sw-x').onclick = () => dl.close();
    dl.addEventListener('click', (e) => { if (e.target === dl) dl.close(); });
    addEventListener('hashchange', () => dl.open && dl.close());
  }
  dl.showModal();
  skyMap($('#swMapBig', dl), id, true);
}
// Himmelsansicht: DSS2-Aufnahme des Feldes (NASA SkyView) im Bildbetrachter – zum Vergleich mit der eigenen Aufnahme
function skyDialog(id, name, size) {
  let dl = $('#swSkyDlg');
  if (!dl) {
    dl = document.createElement('dialog'); dl.id = 'swSkyDlg'; dl.className = 'sw-mapdlg';
    dl.innerHTML = `<div class="row"><b>Himmelsansicht</b><span class="mut sm" id="swSkyName"></span><span class="grow"></span><button class="sw-x" aria-label="Schließen">×</button></div>
      <div id="swSkyBig" class="sw-skybig"></div><p class="dim sm" id="swSkyCap" style="margin:10px 0 0"></p>`;
    document.body.append(dl);
    dl.querySelector('.sw-x').onclick = () => dl.close();
    dl.addEventListener('click', (e) => { if (e.target === dl) dl.close(); });
    dl.addEventListener('close', () => { dl._v?.destroy(); dl._v = null; $('#swSkyBig', dl).innerHTML = ''; });
    addEventListener('hashchange', () => dl.open && dl.close());
  }
  $('#swSkyName', dl).textContent = name;
  $('#swSkyCap', dl).textContent = `Digitized Sky Survey (DSS2, Farbe aus IR/Rot/Blau) über NASA SkyView · Feld ${((size || 20) * 1.8 / 60).toFixed(1).replace('.', ',')}°`;
  dl.showModal();
  const host = $('#swSkyBig', dl);
  host.innerHTML = '<div class="sw-noimg view"><span class="mut sm">Himmelsaufnahme wird geladen …</span></div>';
  const img = new Image();
  img.onload = () => { if (!dl.open) return; host.innerHTML = ''; dl._v = createViewer(host, img.src); };
  img.onerror = () => { host.innerHTML = '<div class="sw-noimg view"><b>Keine Bilddaten</b><span class="mut sm">Die Himmelsdurchmusterung ist gerade nicht erreichbar.</span></div>'; };
  img.src = `/api${K}/sky/${id}.jpg?v=2`;
}

// --------------------------------------------------------------- Objektseite --- //
// Kurzname für die Überschrift („M45 Plejaden“ → „Plejaden“), Bezeichnungen darunter
const shortName = (n) => (n || '').replace(/^(M|NGC|IC|Sh2-?|Mel|C)\s?\d+\s+/i, '') || n;
// Monat, in dem ein Objekt um Mitternacht im Süden steht (Sonne gegenüber): Tag ≈ 79 + (RA − 180°) / 0,9856
const MONTHS = ['Januar', 'Februar', 'März', 'April', 'Mai', 'Juni', 'Juli', 'August', 'September', 'Oktober', 'November', 'Dezember'];
function season(ra) {
  if (ra == null) return null;
  const doy = (79 + ((ra + 180) % 360) / 0.9856) % 365, m = Math.floor(new Date(2026, 0, 1 + doy).getMonth());
  const at = (k) => MONTHS[(m + k + 12) % 12];
  return { best: at(0), from: at(-2), to: at(2), text: `${at(-2).slice(0, 3)} – ${at(2).slice(0, 3)}, am besten im ${at(0)}` };
}

async function objectView(el, id, tab = 'uebersicht', opts = {}) {
  let d = await api(`${K}/entities/${encodeURIComponent(id)}`), cleanup = [], C = cites();
  let e = d.entity;
  id = e.id;
  if (!TABS.some(t => t[0] === tab)) tab = 'uebersicht';
  const reload = async () => { d = await api(`${K}/entities/${id}`); e = d.entity; Object.values(d.sources).forEach(s => { srcCache[s.id] = s; }); };
  Object.values(d.sources).forEach(s => { srcCache[s.id] = s; });
  if (!opts.embedded) crumbs([['Sage', '#/sage/wiki'], ['Enzyklopädie', '#/sage/wiki'], [e.name]]);
  const fact = (k) => d.facts.find(f => f.key === k && ['current', 'conflict'].includes(f.status));
  const sky = () => e.ra != null ? `/api${K}/sky/${id}.jpg?v=2` : null;

  // ---- Kopf: Titelbild, Name, Kurzbeschreibung, Stammdaten, Position am Himmel
  const head = () => {
    const g = d.gallery[0], img = g?.thumb, title = shortName(e.name), des = d.designations.filter(x => x.toLowerCase() !== title.toLowerCase());
    const mag = fact('mag'), con = fact('constellation');
    const tags = [e.otype_label, ...d.edges.filter(x => x.dir === 'out' && x.other.kind === 'concept' && x.other.name !== e.otype_label).map(x => x.other.name),
      con && `Sternbild ${con.value}`, d.designations.some(x => /^M\d/.test(x)) && 'Messier-Objekt', mag && mag.num < 6 && 'mit bloßem Auge sichtbar'].filter(Boolean);
    const rows = [['orbit', 'Entfernung', fact('distance_ly')], ['target', 'Durchmesser', fact('diameter')], ['star', 'Helligkeit', mag],
      ['globe', 'Sternbild', con], ['sparkle', 'Spektraltyp', fact('sp_type')], ['book', 'Objekttyp', fact('otype') && { value: e.otype_label }]].filter(r => r[2]).slice(0, 6);
    return `<div class="sw-dhead">
      <div class="sw-dh-img">${img ? `<a href="#/sage/o/${id}/bilder" title="Alle Bilder"><img src="${img}" alt="" onload="this.classList.add('ld')"></a>` : `<div class="sw-noimg big">${icon('camera')}<span>Keine Bilddaten</span><small>noch keine eigene Aufnahme</small></div>`}
        ${d.gallery.length > 1 ? `<button class="sw-dh-chg" id="swHero">${icon('edit')}Bild ändern</button>` : ''}</div>
      <div class="sw-dh-main"><div class="row" style="gap:6px">${e.kind === 'concept' ? '<span class="chip">Grundbegriff</span>' : statusChip(e.status)}${e.stale ? '<span class="chip warn">veraltet</span>' : ''}</div>
        <h1>${esc(title)}</h1>${des.length ? `<div class="sw-des">${des.map(esc).join('<i>·</i>')}</div>` : ''}
        <p class="sw-dh-sum" id="swSum">${esc(noCite(e.summary) || 'Noch keine Beschreibung.')}</p>
        ${rows.length ? `<dl class="sw-kv">${rows.slice(0, 4).map(([, l, f]) => `<div><dt>${l}</dt><dd>${esc(fv(f))}${f.status === 'conflict' ? ` <span class="warn" title="${esc(f.note)}">${icon('alert')}</span>` : ''}</dd></div>`).join('')}</dl>` : ''}
        <div class="sw-tags">${tags.map((t, i) => `<span class="chip ${i ? '' : 'acc'}">${esc(t)}</span>`).join('')}${e.observed ? `<span class="chip ok">${icon('camera')}von dir fotografiert</span>` : ''}</div></div>
    </div>`;
  };
  const page = () => `<div class="sw-dtop"><span class="grow"></span>
      <button class="btn sm ghost" id="swAsk">${icon('sparkle')}Frag Sage</button>
      ${e.kind !== 'concept' ? `<button class="btn sm ghost" id="swRes">${icon('refresh')}${e.status === 'researched' || e.status === 'reviewed' ? 'Aktualisieren' : d.llm ? 'Mit KI recherchieren' : 'Katalogdaten holen'}</button>` : ''}
      <a class="btn sm ghost" href="/api${K}/entities/${id}/export.md" download>${icon('down')}Exportieren</a>
      <button class="btn sm" id="swEdit">${icon('edit')}Bearbeiten</button>
      <div class="menu"><button class="btn sm ghost" aria-label="Mehr">⋯</button><div class="pop">
        ${e.kind !== 'concept' ? `<a href="#" id="swCat">Nur Katalogdaten auffrischen <small>ohne KI</small></a>` : ''}
        ${e.status === 'researched' ? '<a href="#" id="swOk">Alles als geprüft markieren</a>' : ''}
        <a href="#" id="swGraph">Im Wissensnetz zeigen</a>${e.kind !== 'concept' ? '<hr><a href="#" class="danger" id="swDel">Eintrag löschen <small>Notizen bleiben</small></a>' : ''}</div></div></div>
    ${head()}<div id="swTaskHost"></div>
    <nav class="tabs sw-dtabs" id="swTabs">${TABS.map(([k, l, ic], i) => { const n = { wissen: d.sections.length || null, beobachtungen: d.mine.projects.length + d.mine.images.length || null,
      forschung: d.papers.length + d.preprints.length || null, netz: d.edges.length || null, geschichten: d.stories.length || null, bilder: d.gallery.length || null }[k];
      return `<a href="#/sage/o/${id}/${k}" data-t="${k}" class="${tab === k ? 'on' : ''}" title="Taste ${i + 1}">${icon(ic)}${l}${n ? `<span>${n}</span>` : ''}</a>`; }).join('')}<i class="tab-ind" id="swTabInd"></i></nav>
    <div id="swTab"></div>`;
  const bind = () => {
    $('#swAsk').onclick = () => chat({ scope: 'entity:' + id, entity: id, title: e.name });
    $('#swRes')?.addEventListener('click', () => research(true));
    $('#swCat')?.addEventListener('click', (ev) => { ev.preventDefault(); research(false); });
    $('#swOk')?.addEventListener('click', async (ev) => { ev.preventDefault(); await send(`${K}/entities/${id}`, { reviewed: true }, 'PATCH'); toast('Als geprüft markiert.', 'ok'); await reload(); full(); });
    $('#swGraph').onclick = (ev) => { ev.preventDefault(); location.hash = `#/sage/netz/${id}`; };
    $('#swDel')?.addEventListener('click', async (ev) => { ev.preventDefault(); if (await confirmDlg('Eintrag löschen?', 'Fakten, Texte, Beziehungen und Geschichten dieses Objekts werden entfernt. Deine Notizen bleiben erhalten.', 'Löschen', true)) { await send(`${K}/entities/${id}`, null, 'DELETE'); toast('Gelöscht.', 'ok'); location.hash = '#/sage/wiki'; } });
    $('#swEdit').onclick = () => {
      const p = $('#swSum');
      p.outerHTML = `<div id="swSumEd"><textarea rows="4" class="sw-edit">${esc(e.summary || '')}</textarea><div class="row" style="margin-top:6px"><button class="btn sm" data-s>Speichern</button><button class="linkbtn" data-c>Abbrechen</button><span class="mut sm">Deine Fassung gilt als geprüft.</span></div></div>`;
      $('#swSumEd [data-c]').onclick = full;
      $('#swSumEd [data-s]').onclick = async () => { await send(`${K}/entities/${id}`, { summary: $('#swSumEd textarea').value }, 'PATCH'); toast('Gespeichert.', 'ok'); await reload(); full(); };
    };
    $('#swHero')?.addEventListener('click', (ev) => {
      const fc = floatCard(`<b>Titelbild wählen</b><div class="sw-imgs" style="margin-top:10px">${d.gallery.map(g => `<a href="#" data-k="${g.key}"><img src="${g.thumb}" alt=""><span>${shotDate(g)}</span></a>`).join('')}</div>`, ev);
      fc.onclick = async (x) => { const a = x.target.closest('[data-k]'); if (!a) return; x.preventDefault(); closeFloat(); await send(`${K}/entities/${id}`, { hero: a.dataset.k }, 'PATCH'); await reload(); full(); };
    });
  };
  async function research(deep) {
    const t = await send(K + '/research', { name: id, deep }).catch(err => { toast(err.message, 'err'); });
    if (!t) return;
    $('#swTaskHost').innerHTML = taskLine(t);
    watch(t.id, async (r) => {
      if (r.state === 'done') { toast(r.message, 'ok'); await reload(); full(); }
      else { $('#swTaskHost').innerHTML = `<div class="notice">${icon('alert')}<div><b>Recherche fehlgeschlagen</b><div class="mut sm">${esc(r.error || '')}</div></div></div>`; }
    }, (r) => setTask($('#swTaskHost'), r));
  }
  const full = () => { cleanup.forEach(f => f()); cleanup = []; el.innerHTML = page(); bind(); show(tab); };
  function show(k) {
    tab = k;
    cleanup.forEach(f => f()); cleanup = [];
    $$('#swTabs a').forEach(a => a.classList.toggle('on', a.dataset.t === k));
    requestAnimationFrame(() => slideInd($('#swTabInd'), $('#swTabs a.on'), true));
    const host = $('#swTab');
    host.classList.remove('sw-enter'); void host.offsetWidth; host.classList.add('sw-enter');
    C = cites();
    ({ uebersicht: tabOverview, wissen: tabKnowledge, beobachtungen: tabMine, forschung: tabResearch, bilder: tabImages, netz: tabLinks, geschichten: tabStories })[k](host);
  }

  // ---- Übersicht: Bild mit Galerie · Beschreibung + Schnellfakten · Verwandtes, Notizen, Aufnahmen
  const ORIGIN_LABEL = { catalog: 'aus Katalogwerten', curated: 'Grundwissen', user: 'von dir', ai: 'KI-Entwurf · ungeprüft' };
  function tabOverview(host) {
    const overview = d.sections.find(s => s.key === 'overview'), orig = e.data.summary_origin;
    const ss = season(e.ra), size = fact('size'), plans = d.mine.notes.filter(n => n.kind === 'plan');
    const members = d.edges.filter(x => x.dir === 'in' && x.rel === 'Mitglied von');
    const quick = [['Kataloge', d.designations.join(', ')], ['Objekttyp', e.otype_label], ['Entfernung', fact('distance_ly') && fv(fact('distance_ly'))],
      ['Durchmesser', fact('diameter') && fv(fact('diameter'))], ['Scheinbare Größe', size && `${fv(size)} (${(size.num / 60).toFixed(1).replace('.', ',')}°)`],
      ['Helligkeit', fact('mag') && fv(fact('mag'))], ['Spektraltyp', fact('sp_type')?.value], ['Sternbild', fact('constellation')?.value],
      ['Beste Sichtbarkeit', ss?.text], ['Benannte Mitglieder', members.length ? members.slice(0, 5).map(x => x.other.name).join(', ') + (members.length > 5 ? ' …' : '') : null],
      ['Fachartikel', fact('nbref') && nf.format(fact('nbref').num)]].filter(r => r[1]);
    const related = d.edges.filter(x => x.other.kind !== 'concept' && x.status !== 'rejected' && !(x.dir === 'in' && x.rel === 'Mitglied von'));
    const relList = [...related, ...members].slice(0, 4);
    const fresh = d.preprints.slice(0, 3), isNew = (p) => p.retrieved && Date.now() / 1000 - p.retrieved < 30 * 86400 && p.year >= new Date().getFullYear() - 1;
    const last = d.gallery[0];
    // Werte, die schon im Kopf stehen (Kernwerte), hier nicht wiederholen; der Kurztext steht ebenfalls schon oben
    const inHead = new Set(['Entfernung', 'Durchmesser', 'Helligkeit', 'Sternbild', 'Spektraltyp', 'Objekttyp']);
    const facts = quick.filter(([l]) => !inHead.has(l));
    const longText = overview ? md(overview.body, C, d.sources) : null;
    host.innerHTML = `<div class="sw-dash">
      <div class="sw-d-main">
        <section class="sw-sec"><div class="panel-h"><h2>Beschreibung</h2><span class="grow"></span>${overview ? `<span class="chip ${overview.status === 'draft' ? 'warn' : 'ok'}">${overview.status === 'draft' ? 'KI-Entwurf' : 'geprüft'}</span>` : ORIGIN_LABEL[orig] ? `<span class="chip">${ORIGIN_LABEL[orig]}</span>` : ''}</div>
          ${longText ? `<div class="sw-body readable">${longText}</div>` : `<p class="mut readable">${e.summary ? 'Bisher gibt es nur den Kurztext oben.' : 'Noch keine Beschreibung.'} Der Recherche-Agent liest die Fachliteratur und belegt jede Aussage.</p>`}
          ${e.kind !== 'concept' ? `<button class="btn sm ghost" data-go="deep" style="margin-top:12px" ${d.llm ? '' : 'disabled title="KI einrichten"'}>${icon('sparkle')}${longText ? 'Mit KI vertiefen' : 'Mit KI recherchieren'}</button>` : ''}</section>
        ${facts.length ? `<section class="sw-sec"><h2>Schnellfakten</h2><dl class="sw-quick">${facts.map(([l, v]) => `<dt>${l}</dt><dd>${esc(v)}</dd>`).join('')}</dl>
          <a class="linkbtn" href="#/sage/o/${id}/wissen" style="margin-top:8px;display:inline-flex">Alle Messwerte mit Quellen ${icon('arrow')}</a></section>` : ''}
        <section class="sw-sec"><div class="panel-h"><h2>Aktuelle Forschung${fresh.some(isNew) ? ' <span class="chip acc">Neu</span>' : ''}</h2><span class="grow"></span><button class="btn sm ghost" data-go="papers">${icon('refresh')}Neue Arbeiten suchen</button></div>
          ${fresh.length ? `<ul class="sw-d-list readable">${fresh.map(p => `<li><a href="${esc(p.url || '#')}" target="_blank" rel="noopener">${esc(p.title)}</a><small class="dim">${p.year || ''}${p.kind === 'preprint' ? ' · arXiv' : ''}</small></li>`).join('')}</ul>`
            : '<p class="mut sm">Noch keine Preprints gesammelt.</p>'}</section>
      </div>
      <div class="sw-d-side">
        ${e.ra != null ? `<div class="panel sw-dh-sky"><h2>Position am Himmel</h2><div id="swMap"></div>
          <dl><dt>Rektaszension</dt><dd>${sexa(e.ra, true).replace(/\.\d+s$/, 's')}</dd><dt>Deklination</dt><dd>${sexa(e.dec, false)}</dd></dl>
          <button class="btn sm ghost" id="swSkyBtn">${icon('globe')}Himmelsansicht</button></div>` : ''}
        <div class="panel"><div class="panel-h"><h2>${icon('camera')}Meine Aufnahmen</h2><span class="grow"></span>${d.gallery.length ? `<a class="linkbtn" href="#/sage/o/${id}/bilder">Alle ${d.gallery.length}</a>` : ''}</div>
          ${d.gallery.length ? `<div class="sw-shots">${d.gallery.slice(0, 3).map(g => `<a href="${g.href}"><img src="${g.thumb}" alt="" loading="lazy"><small>${shotDate(g)}</small>${g.minutes ? `<small class="dim">${g.minutes} min</small>` : ''}</a>`).join('')}</div>`
            : `<p class="mut sm" style="margin:0">Noch nicht fotografiert${ss ? ` – gute Zeit: ${ss.from} bis ${ss.to}` : ''}.</p>`}
          <ul class="sw-ctx">
            <li>${icon('target')}<span><b>Beobachtungen (${d.notes_n.observation})</b><small>${d.notes_n.note} Notiz${d.notes_n.note === 1 ? '' : 'en'}</small></span></li>
            <li>${icon('pin')}<span><b>Geplant (${plans.length})</b><small>${plans[0]?.date ? 'Nächste: ' + fnight(plans[0].date) : ss ? 'Saison: ' + ss.from + ' – ' + ss.to : '–'}</small></span></li>
          </ul></div>
        <div class="panel"><div class="panel-h"><h2>${icon('orbit')}Verwandte Objekte</h2><span class="grow"></span><a class="linkbtn" href="#/sage/o/${id}/netz">Alle anzeigen</a></div>
          ${relList.length ? relList.map(x => `<a class="sw-relrow" href="#/sage/o/${x.other.id}"><span class="sw-rr-img">${x.other.image ? `<img src="${x.other.image}" alt="" loading="lazy">` : '<i class="sw-noimg">keine Bilddaten</i>'}</span>
            <span><b>${esc(x.other.name)}</b><small>${esc(x.other.otype_label || '')}${x.other.distance ? ' · ' + esc(x.other.distance) : ''}</small><small class="dim">${esc(x.dir === 'out' ? x.rel : '← ' + x.rel)}${x.status === 'suggested' ? ' · KI-Vorschlag' : ''}</small></span></a>`).join('')
            : '<p class="mut sm" style="margin:0">Noch keine Beziehungen – „Katalogdaten holen“.</p>'}</div>
        <div class="panel"><div class="panel-h"><h2>${icon('edit')}Persönliche Notizen</h2><span class="grow"></span><button class="btn sm ghost" id="swNAdd">${icon('plus')}Hinzufügen</button></div>
          <div id="swNForm"></div>
          ${d.mine.notes.length ? `<ul class="sw-nlist">${d.mine.notes.slice(0, 4).map(n => `<li class="k-${n.kind}"><i></i><div><small class="dim">${n.date ? fnight(n.date) : ago(n.created)} · ${NOTE_KIND[n.kind]}</small><b>${esc(n.title)}</b>${n.body ? `<span class="sm mut">${esc(n.body.slice(0, 120))}</span>` : ''}</div></li>`).join('')}</ul>`
            : '<p class="mut sm" style="margin:0">Bedingungen, Ideen, Ziele – alles bleibt lokal.</p>'}
          ${d.mine.notes.length > 4 ? `<a class="linkbtn" href="#/sage/o/${id}/beobachtungen">Alle ${d.mine.notes.length} Notizen</a>` : ''}</div>
      </div></div>`;
    if ($('#swMap', host)) skyMap($('#swMap', host), id);
    $('#swSkyBtn', host)?.addEventListener('click', () => skyDialog(id, shortName(e.name), e.size));
    host.querySelectorAll('[data-go=deep]').forEach(b => { b.onclick = () => research(true); });
    host.querySelector('[data-go=papers]').onclick = async (ev) => {
      const b = ev.currentTarget; b.disabled = true; b.innerHTML = '<span class="spinner"></span>Sucht …';
      try { const t = await send(`${K}/insights/run`, { only: ['papers'] }); watch(t.id, async () => { await reload(); show('uebersicht'); toast('Forschung aktualisiert.', 'ok'); }); }
      catch (err) { toast(err.message, 'err'); b.disabled = false; }
    };
    $('#swNAdd').onclick = () => {
      $('#swNForm').innerHTML = `<form class="sw-nf"><select name="kind">${Object.entries(NOTE_KIND).map(([k, l]) => `<option value="${k}">${l}</option>`).join('')}</select>
        <input name="title" placeholder="Titel" required><textarea name="body" rows="2" placeholder="Text (optional)"></textarea><input type="date" name="date">
        <div class="row"><button class="btn sm" type="submit">Speichern</button><button class="linkbtn" type="button" data-c>Abbrechen</button></div></form>`;
      const f = $('#swNForm form');
      f.title.focus();
      $('[data-c]', f).onclick = () => { $('#swNForm').innerHTML = ''; };
      f.onsubmit = async (ev) => { ev.preventDefault(); await send(`${K}/notes`, { entity_id: id, kind: f.kind.value, title: f.title.value, body: f.body.value, date: f.date.value }); await reload(); show('uebersicht'); };
    };
  }
  // Bild: Galerie-Eintrag (bestimmtes Bild mit Marken bzw. entwickeltes Ergebnis) oder DSS2-Himmelsbild
  async function heroViewer(host, cap, g) {
    if (g?.kind === 'image') {
      const h = await api(`/sage/history/${g.entry}`).catch(() => null);
      if (h) {
        const objs = (h.objects || []).filter(o => o.x != null);
        let v = null;
        v = createViewer(host, h.image, null, undefined, null, objs.length ? { overlay: (svg, w) => labels(svg, w, h, objs, e.name), onTap: (t, ev) => { const n = tapName(v, ev); if (n) card(n, ev, { image: h.id }); } } : {});
        cleanup.push(() => v.destroy());
        cap.innerHTML = `<b>${esc(h.title)}</b> – eigene Aufnahme (${shotDate(g)}${g.minutes ? `, ${g.minutes} min` : ''}) · ${objs.length} Objekte markiert, antippen für Wissen <a href="${g.href}">${icon('arrow')}</a>`;
        return;
      }
    }
    if (g) {
      const v = createViewer(host, g.src, null, undefined, g.thumb);
      cleanup.push(() => v.destroy());
      cap.innerHTML = `<b>${esc(g.title)}</b> – eigene Aufnahme (${shotDate(g)}${g.minutes ? `, ${g.minutes} min` : ''}${g.instrument ? ', ' + esc(g.instrument) : ''}) <a href="${g.href}">${icon('arrow')}</a>`;
      return;
    }
    host.innerHTML = `<div class="sw-noimg view">${icon('camera')}<b>Keine Bilddaten</b><span class="mut sm">Von ${esc(e.name)} gibt es noch keine eigene Aufnahme.</span>
      ${e.ra != null ? '<button class="btn sm ghost" data-sky>Himmelsdurchmusterung (DSS2) zeigen</button>' : ''}</div>`;
    cap.innerHTML = '';
    host.querySelector('[data-sky]')?.addEventListener('click', () => {
      const v = createViewer(host, sky());
      cleanup.push(() => v.destroy());
      cap.innerHTML = 'Himmelsansicht: Digitized Sky Survey (DSS2, Farbe aus IR/Rot/Blau) über NASA SkyView – keine eigene Aufnahme.';
    });
  }

  // ---- Wissen & Details
  function sectionHtml(s) {
    const draft = s.status === 'draft';
    return `<article class="panel sw-sec ${draft ? 'draft' : ''}" data-sid="${s.id}"><div class="panel-h"><h2>${esc(s.title)}</h2><span class="grow"></span>
        <span class="chip ${draft ? 'warn' : 'ok'}">${draft ? 'KI-Entwurf · ungeprüft' : s.origin === 'user' ? 'von dir' : 'geprüft'}</span>${s.version > 1 ? `<span class="chip">v${s.version}</span>` : ''}</div>
      ${s.flags?.length ? `<details class="sw-flags" open><summary>${icon('alert')}${s.flags.length} Prüfhinweis${s.flags.length > 1 ? 'e' : ''}</summary>${s.flags.map(f => `<div>${esc(f.text)}</div>`).join('')}</details>` : ''}
      <div class="sw-body">${md(s.body, C, d.sources)}</div>
      <div class="row sm sw-sec-f"><span class="dim">${s.model ? esc(s.model) + ' · ' : ''}${ago(s.created)}</span><span class="grow"></span>
        ${draft ? '<button class="linkbtn" data-a="ok">Als geprüft markieren</button>' : ''}<button class="linkbtn" data-a="edit">Bearbeiten</button>
        <button class="linkbtn" data-a="hist">Versionen</button><button class="linkbtn" data-a="del">Entfernen</button></div></article>`;
  }
  function bindSections(host) {
    host.querySelectorAll('.sw-sec').forEach(art => art.addEventListener('click', async (ev) => {
      const a = ev.target.closest('[data-a]')?.dataset.a; if (!a) return;
      const sid = +art.dataset.sid, s = d.sections.find(x => x.id === sid);
      if (a === 'ok') { await send(`${K}/sections/${sid}`, { status: 'reviewed' }, 'PATCH'); await reload(); show(tab); }
      if (a === 'del' && await confirmDlg('Abschnitt entfernen?', 'Die Version bleibt im Verlauf erhalten.', 'Entfernen', true)) { await send(`${K}/sections/${sid}`, null, 'DELETE'); await reload(); show(tab); }
      if (a === 'edit') {
        $('.sw-body', art).innerHTML = `<textarea class="sw-edit" rows="12">${esc(s.body)}</textarea><div class="row" style="margin-top:8px"><button class="btn sm" data-save>Speichern</button><span class="mut sm">Belege als [S&lt;Nummer&gt;] bleiben erhalten · deine Fassung gilt als geprüft</span></div>`;
        $('[data-save]', art).onclick = async () => { await send(`${K}/sections/${sid}`, { body: $('.sw-edit', art).value }, 'PATCH'); toast('Neue Version gespeichert.', 'ok'); await reload(); show(tab); };
      }
      if (a === 'hist') {
        const h = await api(`${K}/sections/${sid}/history`);
        floatCard(`<b>Versionen: ${esc(s.title)}</b>${h.map(v => `<div class="sw-ver"><span class="chip">v${v.version}</span> <span class="sm">${esc(ORIGIN[v.origin] || v.origin)}${v.model ? ' · ' + esc(v.model) : ''}</span> <span class="dim sm">${ago(v.created)} · ${v.status === 'superseded' ? 'ersetzt' : v.status}</span></div>`).join('')}`, ev);
      }
    }));
  }
  function tabKnowledge(host) {
    const cur = d.facts.filter(f => f.status !== 'alternative' && f.key !== 'distance'), alt = d.facts.filter(f => f.key === 'distance');
    const secs = d.sections.filter(s => !['research', 'open_questions'].includes(s.key));
    host.innerHTML = `<div class="split"><div>${secs.length ? secs.map(sectionHtml).join('') : `<div class="empty">${icon('book')}<h2>Noch keine ausführlichen Texte</h2>${d.llm ? '<button class="btn" id="swRes3">Mit KI recherchieren</button>' : 'Für belegte Artikel eine KI einrichten (Einstellungen → Sage).'}</div>`}
        ${e.data.uncertainties?.length ? `<div class="panel"><h2>${icon('alert')}Unsicherheiten laut Recherche</h2><ul class="sw-ul">${e.data.uncertainties.map(u => `<li>${esc(u)}</li>`).join('')}</ul></div>` : ''}</div>
      <div><div class="panel"><h2 style="margin-bottom:10px">Alle Messwerte</h2><table class="sw-tab"><tbody>${cur.map(f => `<tr><th>${esc(f.label)}</th><td>${esc(fv(f))}${f.err ? ` <span class="dim">± ${esc(String(+f.err.toPrecision(2)).replace('.', ','))}</span>` : ''}
            <div class="dim sm">${esc(ORIGIN[f.origin] || f.origin)}${f.note ? ' · ' + esc(f.note) : ''}</div></td><td>${f.source_id ? `<button class="cite" data-s="${f.source_id}">${C.n(f.source_id)}</button>` : ''}</td></tr>`).join('')}</tbody></table>
          ${alt.length ? `<details class="sw-alt"><summary>${alt.length} Entfernungsmessungen in der Literatur</summary>${alt.map(f => `<div class="row sm"><b class="tnum">${esc(fv(f))}</b><span class="mut">${esc(f.note || '')}</span><span class="grow"></span><span class="dim">${esc(f.source_title || '').replace(/^Entfernungsmessung /, '')}</span><button class="cite" data-s="${f.source_id}">${C.n(f.source_id)}</button></div>`).join('')}</details>` : ''}</div>
        <div class="panel"><h2 style="margin-bottom:10px">Quellen dieser Seite</h2><ol class="sw-srcs" id="swSrcList"></ol></div></div></div>`;
    $('#swRes3')?.addEventListener('click', () => research(true));
    bindSections(host);
    $('#swSrcList').innerHTML = [...C.map].map(([sid, n]) => { const s = d.sources[sid]; return s ? `<li value="${n}"><a href="#" class="cite-l" data-s="${sid}">${esc(s.title)}</a> <span class="dim sm">${SRC_KIND[s.kind] || s.kind}${s.year ? ' · ' + s.year : ''}</span></li>` : ''; }).join('') || '<li class="mut sm">–</li>';
    $$('.cite-l', host).forEach(a => { a.onclick = (ev) => { ev.preventDefault(); floatCard(srcHtml(d.sources[a.dataset.s]), ev, 'sw-srcpop'); }; });
  }

  // ---- Meine Beobachtungen
  function tabMine(host) {
    const m = d.mine, ses = m.projects.flatMap(p => p.sessions.map(s => ({ ...s, project: p })));
    const res = m.projects.flatMap(p => p.results.map(r => ({ ...r, project: p })));
    const maxSnr = Math.max(1, ...res.map(r => r.snr || 0));
    host.innerHTML = `<div class="split"><div>
        ${m.derived.length ? `<div class="panel sw-you"><div class="panel-h"><h2>Aus deinen Daten</h2><span class="grow"></span><span class="chip">berechnet, nicht geschätzt</span></div>
          <ul class="sw-der">${m.derived.map(x => `<li>${icon(x.kind === 'interpretation' ? 'sparkle' : 'check')}<div>${esc(x.text)}<div class="dim sm">${x.kind === 'interpretation' ? 'Interpretation · ' : ''}Grundlage: ${esc(x.basis)}</div></div></li>`).join('')}</ul></div>`
          : `<div class="empty" style="padding:44px 20px">${icon('camera')}<h2>Noch keine eigenen Aufnahmen</h2>Sobald du ${esc(e.name)} fotografierst oder ein Bild bestimmen lässt, erscheint hier deine Geschichte mit diesem Objekt.</div>`}
        ${ses.length ? `<div class="panel"><h2 style="margin-bottom:10px">Sessions</h2><table class="sw-tab sw-ses"><thead><tr><th>Nacht</th><th>Bilder</th><th>Belichtung</th><th>FWHM</th><th>Gerät</th></tr></thead><tbody>
          ${ses.map(s => `<tr><td><a href="#/p/${s.project.id}/aufnahmen">${fnight(s.night)}</a></td><td class="tnum">${s.used} / ${s.frames}</td><td class="tnum">${dur(s.used_s || s.exposure_s)}</td><td class="tnum">${s.fwhm_px ? s.fwhm_px.toFixed(1).replace('.', ',') + ' px' : '–'}</td><td class="sm">${esc(s.instruments.join(', ') || '–')}${s.filters.length ? ' · ' + esc(s.filters.join(', ')) : ''}</td></tr>`).join('')}</tbody></table></div>` : ''}
        ${res.length ? `<div class="panel"><h2 style="margin-bottom:10px">Bearbeitungsverlauf</h2>${res.map(r => `<a class="sw-verrow" href="#/p/${r.project.id}/versionen"><img src="/api/stacking/results/${r.id}/thumb" alt="" loading="lazy"><div><b>${fdate(r.created)}</b><div class="mut sm">${r.n_frames} Bilder · ${dur(r.exposure_s)}${r.score != null ? ' · Qualität ' + r.score : ''}</div>
            ${r.snr ? `<div class="sw-bar"><i style="width:${r.snr / maxSnr * 100}%"></i></div><small class="dim">Objekt-SNR ${r.snr.toFixed(1).replace('.', ',')}</small>` : ''}</div></a>`).join('')}</div>` : ''}
        ${m.images.length ? `<div class="panel"><h2 style="margin-bottom:10px">Bestimmte Bilder</h2><div class="sw-imgs">${m.images.map(i => `<a href="#/sage/${i.id}">${i.image ? `<img src="${i.image}" alt="" loading="lazy">` : ph()}<span>${esc(i.title || '')}</span></a>`).join('')}</div></div>` : ''}</div>
      <div><form class="panel" id="swNoteF"><h2 style="margin-bottom:12px">Notiz, Plan oder Beobachtung</h2>
          <div class="row" style="flex-wrap:nowrap;gap:10px"><label class="field" style="flex:1"><span>Art</span><select name="kind">${Object.entries(NOTE_KIND).map(([k, l]) => `<option value="${k}">${l}</option>`).join('')}</select></label>
            <label class="field" style="width:150px"><span>Datum</span><input type="date" name="date"></label></div>
          <label class="field"><span>Titel</span><input name="title" placeholder="z. B. Mehr Belichtung für den Merope-Nebel" required></label>
          <label class="field"><span>Text</span><textarea name="body" rows="4" placeholder="Bedingungen, Ideen, Entdeckungen …"></textarea></label>
          <button class="btn" type="submit">${icon('plus')}Speichern</button></form>
        <div id="swNotes"></div></div></div>`;
    const drawNotes = () => {
      $('#swNotes').innerHTML = m.notes.map(n => `<div class="panel sw-note" data-n="${n.id}"><div class="row"><span class="chip ${n.kind === 'plan' ? 'acc' : ''}">${NOTE_KIND[n.kind]}</span>${n.date ? `<span class="chip">${fnight(n.date)}</span>` : ''}<span class="grow"></span>
          <button class="linkbtn" data-a="edit">Bearbeiten</button><button class="linkbtn" data-a="del">Löschen</button></div><b>${esc(n.title)}</b>${n.body ? `<p class="sm">${esc(n.body).replace(/\n/g, '<br>')}</p>` : ''}</div>`).join('');
    };
    drawNotes();
    $('#swNoteF').onsubmit = async (ev) => {
      ev.preventDefault();
      const f = ev.target, n = await send(`${K}/notes`, { entity_id: id, kind: f.kind.value, title: f.title.value, body: f.body.value, date: f.date.value });
      m.notes.unshift(n); f.reset(); drawNotes(); toast('Gespeichert.', 'ok');
    };
    $('#swNotes').onclick = async (ev) => {
      const a = ev.target.closest('[data-a]')?.dataset.a, box = ev.target.closest('[data-n]'); if (!a || !box) return;
      const n = m.notes.find(x => x.id === +box.dataset.n);
      if (a === 'del' && await confirmDlg('Notiz löschen?', esc(n.title), 'Löschen', true)) { await send(`${K}/notes/${n.id}`, null, 'DELETE'); m.notes = m.notes.filter(x => x !== n); drawNotes(); }
      if (a === 'edit') {
        box.innerHTML = `<label class="field"><span>Titel</span><input value="${esc(n.title)}" data-f="title"></label><label class="field"><span>Text</span><textarea rows="4" data-f="body">${esc(n.body)}</textarea></label><button class="btn sm" data-save>Speichern</button>`;
        $('[data-save]', box).onclick = async () => { Object.assign(n, await send(`${K}/notes/${n.id}`, { title: $('[data-f=title]', box).value, body: $('[data-f=body]', box).value }, 'PATCH')); drawNotes(); };
      }
    };
  }

  // ---- Forschung & Entdeckungen
  async function tabResearch(host) {
    const secs = d.sections.filter(s => ['research', 'open_questions'].includes(s.key));
    const paper = (p) => `<details class="sw-paper"><summary><span class="chip ${p.kind === 'preprint' ? 'warn' : ''}">${p.kind === 'preprint' ? 'arXiv' : 'Artikel'}</span><b>${esc(p.title)}</b><span class="dim sm">${p.year || ''}</span></summary>
      ${p.authors ? `<div class="mut sm">${esc(p.authors)}</div>` : ''}${p.abstract ? `<p class="sm">${esc(p.abstract)}</p>` : '<p class="dim sm">Kein Abstract gespeichert.</p>'}${p.url ? `<a class="sm" href="${esc(p.url)}" target="_blank" rel="noopener">Öffnen ${icon('arrow')}</a>` : ''}</details>`;
    host.innerHTML = `<div class="split"><div>${secs.map(sectionHtml).join('') || `<div class="notice">${icon('book')}<div><b>Noch keine Forschungszusammenfassung</b><div class="mut sm">Der Recherche-Agent fasst neue Arbeiten zusammen und benennt offene Fragen – mit Beleg je Aussage.</div></div>${d.llm ? '<span class="grow"></span><button class="btn sm" id="swRes4">Recherchieren</button>' : ''}</div>`}
        <div id="swIns"></div>
        <div class="panel"><div class="panel-h"><h2>Neueste Preprints</h2><span class="grow"></span><span class="mut sm">arXiv · noch nicht begutachtet</span></div>${d.preprints.map(paper).join('') || '<p class="mut sm" style="margin:0">Keine gefunden.</p>'}</div>
        <div class="panel"><div class="panel-h"><h2>Fachartikel mit Schwerpunkt auf dem Objekt</h2><span class="grow"></span><span class="mut sm">SIMBAD · NASA ADS</span></div>${d.papers.map(paper).join('') || '<p class="mut sm" style="margin:0">Keine gefunden.</p>'}</div></div>
      <div><div class="panel"><h2 style="margin-bottom:10px">Rechercheprotokoll</h2>${d.runs.length ? d.runs.map(r => `<details class="sw-run"><summary><span class="chip ${r.state === 'done' ? 'ok' : 'bad'}">${r.state === 'done' ? 'fertig' : 'Fehler'}</span><span class="sm">${fdate(r.started)}</span><span class="grow"></span><span class="dim sm">${r.cost ? r.cost.toFixed(3).replace('.', ',') + ' $' : ''}</span></summary>
          <div class="dim sm">${esc(r.model || '')} · ${nf.format(r.tokens || 0)} Tokens</div>${r.error ? `<div class="bad sm">${esc(r.error)}</div>` : ''}
          <ol class="sw-stepl">${(r.steps || []).map(s => `<li class="${s.type}">${esc(s.text || s.tool || '')}</li>`).join('')}</ol></details>`).join('')
          : '<p class="mut sm" style="margin:0">Noch keine KI-Recherche. Katalogabfragen laufen ohne Protokolleintrag.</p>'}
          <p class="dim sm" style="margin:10px 0 0">Zuletzt aufgefrischt: ${e.refreshed ? ago(e.refreshed) : 'nie'}</p></div></div></div>`;
    $('#swRes4')?.addEventListener('click', () => research(true));
    bindSections(host);
    const ins = (await api(`${K}/insights`).catch(() => ({ items: [] }))).items.filter(i => i.entities.includes(id));
    if (ins.length && $('#swIns')) $('#swIns').innerHTML = `<h2 class="sect">Entdeckungen zu ${esc(e.name)}</h2>${ins.map(insCard).join('')}`;
  }

  // ---- Bilder & Vergleiche
  function tabImages(host) {
    const m = d.mine, proj = m.projects.find(p => p.results.length >= 2);
    host.innerHTML = `${d.gallery.length ? `<div class="panel sw-d-view"><div id="swImg"></div><div class="sw-cap2" id="swCap"></div>
        ${d.gallery.length > 1 ? `<div class="sw-strip">${d.gallery.slice(0, 8).map((g, i) => `<button data-g="${i}" class="${i ? '' : 'on'}"><img src="${g.thumb}" alt="" loading="lazy"></button>`).join('')}</div>` : ''}</div>` : ''}
      ${proj ? `<div class="panel"><div class="panel-h"><h2>Erste gegen neueste Version</h2><span class="grow"></span><span class="mut sm">Regler ziehen · ${esc(proj.name)}</span></div><div id="swCmp"></div>
        <div class="row sm mut" style="margin-top:8px">${(() => { const a = proj.results[0], b = proj.results[proj.results.length - 1]; return `<span>${fdate(a.created)} · ${dur(a.exposure_s)}${a.snr ? ' · SNR ' + a.snr.toFixed(0) : ''}</span><span class="grow"></span><span>${fdate(b.created)} · ${dur(b.exposure_s)}${b.snr ? ' · SNR ' + b.snr.toFixed(0) : ''}</span>`; })()}</div></div>` : ''}
      <div class="split" style="margin-top:${proj ? 16 : 0}px"><div><div class="panel"><h2 style="margin-bottom:10px">Deine Bilder</h2><div class="sw-imgs" id="swOwn">
        ${m.projects.flatMap(p => p.results.map(r => `<a href="#/p/${p.id}/bild" data-v="/api/stacking/results/${r.id}/jpg"><img src="/api/stacking/results/${r.id}/thumb" alt="" loading="lazy"><span>${fdate(r.created)}</span></a>`)).join('')}
        ${m.images.filter(i => i.image).map(i => `<a href="#/sage/${i.id}"><img src="${i.image}" alt="" loading="lazy"><span>${esc(i.title || '')}</span></a>`).join('')}</div>
        ${!m.projects.length && !m.images.length ? '<p class="mut sm" style="margin:0">Noch keine eigenen Bilder dieses Objekts.</p>' : ''}</div></div>
      <div>${e.ra != null ? `<div class="panel"><h2 style="margin-bottom:10px">Himmelsdurchmusterung</h2><div id="swSky"></div><p class="dim sm" style="margin:8px 0 0">DSS2 (Palomar/UK-Schmidt, digitalisiert) über NASA SkyView – zum Vergleich mit deiner Aufnahme. Feld ${((e.size || 20) * 1.8 / 60).toFixed(1).replace('.', ',')}°.</p></div>` : ''}</div></div>`;
    if (d.gallery.length) {
      const view = (i) => { heroViewer($('#swImg'), $('#swCap'), d.gallery[i]); $$('.sw-strip button', host).forEach(b => b.classList.toggle('on', +b.dataset.g === i)); };
      $('.sw-strip', host)?.addEventListener('click', (ev) => { const b = ev.target.closest('[data-g]'); if (b) view(+b.dataset.g); });
      view(0);
    }
    if (proj) { const a = proj.results[0], b = proj.results[proj.results.length - 1]; const v = createViewer($('#swCmp'), `/api/stacking/results/${b.id}/jpg`, `/api/stacking/results/${a.id}/jpg`, ['erste Version', 'neueste']); cleanup.push(() => v.destroy()); }
    if ($('#swSky')) { const v = createViewer($('#swSky'), `/api${K}/sky/${id}.jpg?v=2`); cleanup.push(() => v.destroy());
      v.el.querySelector('img.b')?.addEventListener('error', () => { $('#swSky').innerHTML = '<div class="sw-noimg view"><b>Keine Bilddaten</b><span class="mut sm">Die Himmelsdurchmusterung ist gerade nicht erreichbar.</span></div>'; }); }
  }

  // ---- Zusammenhänge
  async function tabLinks(host) {
    const groups = {};
    d.edges.forEach(x => { (groups[x.status === 'suggested' ? 'suggested' : x.origin] ||= []).push(x); });
    const label = { suggested: 'KI-Vorschläge – bitte prüfen', catalog: 'Aus Fachkatalogen', curated: 'Grundwissen', user: 'Von dir', 'ai+user': 'KI-Vorschläge, von dir bestätigt' };
    host.innerHTML = `<div class="split"><div><div class="panel sw-gpanel"><div class="panel-h"><h2>Wissensnetz um ${esc(e.name)}</h2><span class="grow"></span><span class="mut sm">Ziehen · Mausrad · Doppelklick öffnet</span></div><div class="sw-graph" id="swG"></div>${legend()}</div></div>
      <div>${Object.entries(groups).map(([g, xs]) => `<div class="panel"><h2 style="margin-bottom:8px">${label[g] || g}</h2>${xs.map(x => `<div class="sw-edge" data-x="${x.id}"><a href="#/sage/o/${x.other.id}">${x.dir === 'out' ? '' : esc(x.other.name) + ' '}<span class="mut">${esc(x.rel)}</span>${x.dir === 'out' ? ' ' + esc(x.other.name) : ' ' + esc(e.name)}</a>
          ${x.note ? `<div class="dim sm">${esc(x.note)}</div>` : ''}${x.source_id ? `<button class="cite" data-s="${x.source_id}">Q</button>` : ''}
          ${x.status === 'suggested' ? `<div class="row sm" style="margin-top:4px"><button class="linkbtn" data-a="verified">${icon('check')}Bestätigen</button><button class="linkbtn" data-a="rejected">Verwerfen</button></div>` : ''}</div>`).join('')}</div>`).join('')}
        <form class="panel" id="swEdgeF"><h2 style="margin-bottom:10px">Verbindung hinzufügen</h2><label class="field"><span>Mit Objekt oder Begriff</span><input name="dst" placeholder="z. B. NGC 1435 oder Reflexionsnebel" required></label>
          <label class="field"><span>Beziehung</span><input name="rel" placeholder="z. B. beleuchtet, liegt vor, gleiche Region" required></label><button class="btn sm" type="submit">${icon('plus')}Verbinden</button></form></div></div>`;
    host.querySelectorAll('.sw-edge [data-a]').forEach(b => { b.onclick = async () => { await send(`${K}/edges/${b.closest('[data-x]').dataset.x}`, { status: b.dataset.a }, 'PATCH'); await reload(); show('netz'); }; });
    $('#swEdgeF').onsubmit = async (ev) => { ev.preventDefault(); try { await send(`${K}/edges`, { src: id, dst: ev.target.dst.value, rel: ev.target.rel.value }); await reload(); show('netz'); } catch (err) { toast(err.message, 'err'); } };
    const g = await api(`${K}/graph?center=${id}&depth=2`);
    if ($('#swG')) cleanup.push(drawGraph($('#swG'), g, { center: id }));
  }

  // ---- Geschichten & Audio
  function tabStories(host) {
    let cur = d.stories[0] || null;
    const legacy = d.mine.images.filter(i => i.has_story);
    host.innerHTML = `<div class="sw-formats">${Object.entries(d.formats).map(([k, l]) => `<button class="sw-fmt" data-f="${k}" ${!d.llm && k !== 'tour' ? 'disabled title="KI einrichten"' : ''}>${icon({ doku: 'book', story: 'sparkle', report: 'camera', beginner: 'star', deep: 'search', tour: 'volume' }[k])}<b>${esc(l)}</b></button>`).join('')}</div>
      <div class="split"><div id="swStory"></div><div><div class="panel"><h2 style="margin-bottom:8px">Deine Erzählungen</h2><div id="swSList"></div></div>
        ${legacy.length ? `<div class="panel"><h2 style="margin-bottom:8px">Geschichten zu deinen Bildern</h2>${legacy.map(i => `<a class="sw-srow" href="#/sage/${i.id}">${icon('book')}<span>${esc(i.title)}</span></a>`).join('')}</div>` : ''}</div></div>`;
    const list = () => { $('#swSList').innerHTML = d.stories.length ? d.stories.map(s => `<a href="#" class="sw-srow ${cur?.id === s.id ? 'on' : ''}" data-s="${s.id}">${icon(s.format === 'tour' ? 'volume' : 'book')}<span>${esc(s.title)}<small class="dim">${esc(d.formats[s.format] || s.format)} · ${ago(s.created)}</small></span></a>`).join('') : '<p class="mut sm" style="margin:0">Wähle oben ein Format – Sage erzählt aus der Enzyklopädie und deinen Daten.</p>'; };
    const open = (s) => { cur = s; list(); cleanup.forEach(f => f()); cleanup = []; s ? (s.format === 'tour' ? tour($('#swStory'), s) : reader($('#swStory'), s)) : ($('#swStory').innerHTML = ''); };
    host.querySelector('.sw-formats').onclick = async (ev) => {
      const b = ev.target.closest('[data-f]'); if (!b || b.disabled) return;
      $$('.sw-fmt', host).forEach(x => { x.disabled = true; }); b.classList.add('busy'); b.insertAdjacentHTML('beforeend', '<span class="spinner"></span>');
      try { const s = await send(`${K}/entities/${id}/stories`, { format: b.dataset.f }); d.stories.unshift(s); open(s); toast('Fertig erzählt.', 'ok'); }
      catch (err) { toast(err.message, 'err'); }
      $$('.sw-fmt', host).forEach(x => { x.disabled = !d.llm && x.dataset.f !== 'tour'; }); b.classList.remove('busy'); $('.spinner', b)?.remove();
    };
    $('#swSList').onclick = (ev) => { const a = ev.target.closest('[data-s]'); if (!a) return; ev.preventDefault(); open(d.stories.find(s => s.id === +a.dataset.s)); };
    open(cur);
  }
  const ttsSel = () => `<select class="sw-tts">${[['openrouter', 'OpenRouter'], ['qwen', 'Qwen3 (lokal)'], ['elevenlabs', 'ElevenLabs']].map(([k, l]) => `<option value="${k}" ${store.get('sage.tts', 'openrouter') === k ? 'selected' : ''}>${l}</option>`).join('')}</select>`;
  function reader(host, s) {
    host.innerHTML = `<article class="panel sg-story sw-reader"><div class="row sw-rd-h"><span class="chip acc">${esc(d.formats[s.format] || s.format)}</span><span class="dim sm">${esc(s.model)}</span><span class="grow"></span>
        ${ttsSel()}<button class="btn sm" id="swSpeak">${icon('play')}Vorlesen</button><button class="linkbtn" id="swSDel">Löschen</button></div><div id="swAud"></div>${md(s.body, C, d.sources)}</article>`;
    const sel = $('.sw-tts', host);
    sel.onchange = () => store.set('sage.tts', sel.value);
    $('#swSpeak').onclick = async () => {
      const b = $('#swSpeak'); b.disabled = true; b.innerHTML = '<span class="spinner"></span>Stimme wird erzeugt …';
      try { const r = await send(`${K}/stories/${s.id}/speak`, { provider: sel.value }); $('#swAud').innerHTML = `<audio controls autoplay src="${r.url}"></audio>`; const a = $('#swAud audio'); cleanup.push(() => a.pause()); }
      catch (err) { toast(err.message, 'err'); }
      b.disabled = false; b.innerHTML = `${icon('play')}Vorlesen`;
    };
    $('#swSDel').onclick = async () => { if (await confirmDlg('Erzählung löschen?', 'Text und Vertonung werden entfernt.', 'Löschen', true)) { await send(`${K}/stories/${s.id}`, null, 'DELETE'); d.stories = d.stories.filter(x => x.id !== s.id); show('geschichten'); } };
  }
  // Audioführung: Abschnitt für Abschnitt vertonen; das besprochene Objekt leuchtet im Bild auf
  async function tour(host, s) {
    host.innerHTML = `<div class="panel sw-tour"><div class="row"><h2>${esc(s.title)}</h2><span class="grow"></span>${ttsSel()}<button class="btn sm" id="swPlay">${icon('play')}Führung starten</button></div>
        <div id="swTV" style="margin-top:12px"></div><ol class="sw-segs">${s.segments.map((g, i) => `<li data-i="${i}">${g.object ? `<b>${esc(g.object)}</b> ` : ''}${esc(g.text)}</li>`).join('')}</ol>
        <div class="dim sm">${esc(s.model)}</div></div>`;
    const h = s.image?.entry ? await api(`/sage/history/${s.image.entry}`).catch(() => null) : null;
    let ov = null;
    if (h) {
      const objs = (h.objects || []).filter(o => o.x != null);
      let v = null;
      v = createViewer($('#swTV'), h.image, null, undefined, null, { overlay: (svg, w) => { labels(svg, w, h, objs, null); ov = svg; }, onTap: (t, ev) => { const n = tapName(v, ev); if (n) card(n, ev, { image: h.id }); } });
      cleanup.push(() => v.destroy());
    }
    let playing = false, audio = null;
    const focus = (i) => {
      $$('.sw-segs li', host).forEach(li => li.classList.toggle('on', +li.dataset.i === i));
      $('.sw-segs li.on', host)?.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
      ov && $$('g', ov).forEach(g => g.classList.toggle('focus', i >= 0 && g.dataset.n === s.segments[i]?.object));
    };
    const stop = () => { playing = false; audio?.pause(); focus(-1); $('#swPlay').innerHTML = `${icon('play')}Führung starten`; };
    cleanup.push(stop);
    $('#swPlay').onclick = async () => {
      if (playing) { stop(); return; }
      playing = true; $('#swPlay').innerHTML = `${icon('stop')}Anhalten`;
      const prov = $('.sw-tts', host).value; store.set('sage.tts', prov);
      for (let i = 0; i < s.segments.length && playing; i++) {
        focus(i);
        let r;
        try { r = await send(`${K}/stories/${s.id}/speak`, { provider: prov, segment: i }); } catch (err) { toast(err.message, 'err'); stop(); return; }
        if (!playing) return;
        audio = new Audio(r.url);
        await new Promise(res => { audio.onended = res; audio.onerror = res; audio.play().catch(res); });
      }
      stop();
    };
  }

  full();
  return {
    navigate(path) { const m = path.match(/^\/sage\/o\/([^/]+)(?:\/(\w+))?$/); if (!m || m[1] !== id) return false; show(TABS.some(t => t[0] === m[2]) ? m[2] : 'uebersicht'); return true; },
    actions: () => [['sparkle', 'Frag Sage', e.name, () => chat({ scope: 'entity:' + id, entity: id, title: e.name })], ...(e.kind !== 'concept' ? [['refresh', 'Recherchieren', 'Fachkataloge + KI', () => research(true)]] : []),
      ...TABS.map(([k, l]) => ['arrow', l, e.name, `#/sage/o/${id}/${k}`])],
    destroy: () => { cleanup.forEach(f => f()); closeFloat(); },
  };
}

// Welche Objektmarke liegt unter dem Klick? Beschriftung zuerst, sonst der kleinste Kreis, der den Punkt enthält
// (Kreise sind ungefüllt – direktes Treffen wäre nur auf der Linie möglich; große Objekte überdecken oft das ganze Bild)
function pick(svg, ev) {
  if (!svg?.getScreenCTM) return null;
  const p = new DOMPoint(ev.clientX, ev.clientY).matrixTransform(svg.getScreenCTM().inverse());
  let best = null;
  for (const g of svg.querySelectorAll('g')) {
    const c = g.querySelector('circle'), t = g.querySelector('text');
    const b = t?.getBBox();
    if (b && p.x >= b.x - 4 && p.x <= b.x + b.width + 4 && p.y >= b.y - 4 && p.y <= b.y + b.height + 4) return g;
    if (!c) continue;
    const r = +c.getAttribute('r'), d = Math.hypot(p.x - c.getAttribute('cx'), p.y - c.getAttribute('cy'));
    if (d <= r && (!best || r < best.r)) best = { g, r };
  }
  return best?.g || null;
}
const tapName = (viewer, ev) => pick(viewer?.overlay, ev)?.dataset.n;

// Objektmarken im Bild (Anzeige-Pixel des Vorschaubilds); das Hauptobjekt wird hervorgehoben
function labels(svg, w, h, objs, self) {
  const W = h.width || w, sc = w / W, fs = Math.max(12, w / 70);
  svg.innerHTML = objs.map(o => { const x = o.x * sc, y = o.y * sc, r = Math.max(fs * .6, (o.radius || 8) * sc);
    return `<g data-n="${esc(o.name)}" class="tap ${self && o.name === self ? 'self' : ''}"><circle cx="${x}" cy="${y}" r="${r}"/><text x="${x + r * .75 + fs * .3}" y="${y - r * .75}" font-size="${fs}">${esc(o.name)}</text></g>`; }).join('');
}

// ---------------------------------------------------------------- Wissensnetz --- //
const legend = () => `<div class="sw-legend"><span><i class="l-cat"></i>Fachkatalog</span><span><i class="l-cur"></i>Grundwissen</span><span><i class="l-ai"></i>KI-Vorschlag</span><span><i class="l-per"></i>deine Daten</span></div>`;
// Kräftebasiertes Layout in SVG (bis ~100 Knoten); Ziehen verschiebt, Mausrad zoomt, Klick zeigt die Karte, Doppelklick öffnet
function drawGraph(host, g, { center = null, onPick = null } = {}) {
  const W = host.clientWidth || 800, H = host.clientHeight || 520;
  const N = g.nodes.map((n, i) => ({ ...n, x: W / 2 + Math.cos(i * 2.4) * (40 + (n.level || 1) * 90), y: H / 2 + Math.sin(i * 2.4) * (40 + (n.level || 1) * 70), vx: 0, vy: 0 }));
  const by = Object.fromEntries(N.map(n => [n.id, n]));
  const L = g.links.filter(l => by[l.source] && by[l.target]).map(l => ({ ...l, s: by[l.source], t: by[l.target] }));
  if (center && by[center]) { by[center].x = W / 2; by[center].y = H / 2; by[center].fixed = true; }
  for (let it = 0; it < 320; it++) {
    const a = 1 - it / 320;
    for (let i = 0; i < N.length; i++) for (let j = i + 1; j < N.length; j++) {
      const p = N[i], q = N[j]; let dx = q.x - p.x, dy = q.y - p.y, d2 = dx * dx + dy * dy + .01;
      const f = 9000 / d2 * a; const d = Math.sqrt(d2); dx /= d; dy /= d;
      p.vx -= dx * f; p.vy -= dy * f; q.vx += dx * f; q.vy += dy * f;
    }
    for (const l of L) { const dx = l.t.x - l.s.x, dy = l.t.y - l.s.y, d = Math.hypot(dx, dy) || 1, f = (d - (l.origin === 'personal' ? 85 : 125)) * .04 * a;
      l.s.vx += dx / d * f; l.s.vy += dy / d * f; l.t.vx -= dx / d * f; l.t.vy -= dy / d * f; }
    for (const n of N) { if (n.fixed) { n.vx = n.vy = 0; continue; } n.vx += (W / 2 - n.x) * .004 * a; n.vy += (H / 2 - n.y) * .004 * a; n.x += n.vx; n.y += n.vy; n.vx *= .55; n.vy *= .55; }
  }
  const cls = (l) => l.origin === 'personal' ? 'l-per' : l.status === 'suggested' || l.origin === 'ai' ? 'l-ai' : l.origin === 'curated' ? 'l-cur' : 'l-cat';
  host.innerHTML = `<svg class="sw-gsvg" viewBox="0 0 ${W} ${H}"><g class="vp">${L.map((l, i) => `<line data-l="${i}" class="${cls(l)}" x1="${l.s.x}" y1="${l.s.y}" x2="${l.t.x}" y2="${l.t.y}"><title>${esc(l.s.label)} – ${esc(l.rel)} → ${esc(l.t.label)}</title></line>`).join('')}
    ${N.map((n, i) => `<g class="gn ${n.kind} g-${n.group} ${n.observed ? 'obs' : ''} ${n.id === center ? 'ctr' : ''}" data-i="${i}" transform="translate(${n.x},${n.y})"><circle r="${n.id === center ? 13 : n.kind === 'concept' ? 9 : n.kind === 'object' ? 8 : 6}"/><text y="${n.id === center ? 27 : 21}">${esc(n.label.length > 28 ? n.label.slice(0, 27) + '…' : n.label)}</text></g>`).join('')}</g></svg>`;
  const svg = $('svg', host), vp = $('.vp', svg);
  // Ergebnis ins Bild einpassen
  const xs = N.map(n => n.x), ys = N.map(n => n.y), bw = Math.max(...xs) - Math.min(...xs) + 160, bh = Math.max(...ys) - Math.min(...ys) + 70;
  let sc = Math.min(1.7, W / bw, H / bh), tx = W / 2 - (Math.min(...xs) + Math.max(...xs)) / 2 * sc, ty = H / 2 - (Math.min(...ys) + Math.max(...ys)) / 2 * sc, drag = null;
  const apply = () => vp.setAttribute('transform', `translate(${tx},${ty}) scale(${sc})`);
  apply();
  const pt = (ev) => { const r = svg.getBoundingClientRect(); return [(ev.clientX - r.left) * W / r.width, (ev.clientY - r.top) * H / r.height]; };
  const onWheel = (ev) => { ev.preventDefault(); const [px, py] = pt(ev), f = Math.exp(-ev.deltaY * .0015), ns = Math.min(3, Math.max(.35, sc * f)); tx = px - (px - tx) * ns / sc; ty = py - (py - ty) * ns / sc; sc = ns; apply(); };
  svg.addEventListener('wheel', onWheel, { passive: false });
  svg.addEventListener('pointerdown', (ev) => { const gn = ev.target.closest('.gn'); const [px, py] = pt(ev); drag = { n: gn ? N[+gn.dataset.i] : null, el: gn, px, py, tx, ty, moved: false }; svg.setPointerCapture(ev.pointerId); });
  svg.addEventListener('pointermove', (ev) => {
    if (!drag) return; const [px, py] = pt(ev);
    if (Math.hypot(px - drag.px, py - drag.py) > 3) drag.moved = true;
    if (drag.n) { drag.n.x = (px - tx) / sc; drag.n.y = (py - ty) / sc; drag.el.setAttribute('transform', `translate(${drag.n.x},${drag.n.y})`);
      L.forEach((l, i) => { if (l.s === drag.n || l.t === drag.n) { const e = svg.querySelector(`[data-l="${i}"]`); e.setAttribute('x1', l.s.x); e.setAttribute('y1', l.s.y); e.setAttribute('x2', l.t.x); e.setAttribute('y2', l.t.y); } }); }
    else { tx = drag.tx + px - drag.px; ty = drag.ty + py - drag.py; apply(); }
  });
  svg.addEventListener('pointerup', (ev) => {
    if (drag?.n && !drag.moved) {
      const n = drag.n;
      $$('.gn', svg).forEach(x => x.classList.toggle('hi', x === drag.el));
      if (onPick) onPick(n, ev);
      else if (n.href) floatCard(`<b>${esc(n.label)}</b><div class="row" style="margin-top:8px"><a class="btn sm" href="${n.href}">Öffnen</a></div>`, ev);
      else if (n.kind === 'object' || n.kind === 'concept') card(n.label, ev);
      else floatCard(`<b>${esc(n.label)}</b><div class="mut sm">Aus deinen Aufnahmedaten</div>`, ev);
    }
    drag = null;
  });
  svg.addEventListener('dblclick', (ev) => { const gn = ev.target.closest('.gn'); const n = gn && N[+gn.dataset.i]; if (n && (n.kind === 'object' || n.kind === 'concept')) location.hash = `#/sage/o/${n.id}`; else if (n?.href) location.hash = n.href; });
  return () => svg.removeEventListener('wheel', onWheel);
}

async function graphView(el, center) {
  crumbs([['Sage', '#/sage/wiki'], ['Wissensnetz']]);
  el.innerHTML = `<div class="pagehead"><div><h1>Wissensnetz</h1><p class="mut">Objekte, Begriffe und deine Aufnahmen – jede Linie mit Herkunft. Gestrichelt: Vorschläge der KI, noch nicht bestätigt.</p></div>
      <span class="grow"></span><input type="search" id="swGQ" placeholder="Zentrieren auf … (z. B. M45)" value="${esc(center || '')}"></div>
    <div class="panel sw-gpanel big"><div class="sw-graph" id="swG"></div>${legend()}</div>`;
  let off = null;
  const load = async (c) => {
    off?.();
    const g = await api(`${K}/graph${c ? `?center=${encodeURIComponent(c)}&depth=2` : ''}`).catch(err => { toast(err.message, 'err'); return null; });
    if (!g) return;
    if (!g.nodes.length) { $('#swG').innerHTML = `<div class="empty" style="border:0">${icon('orbit')}<h2>Noch leer</h2>Bestimme Bilder oder recherchiere Objekte – das Netz wächst mit.</div>`; return; }
    off = drawGraph($('#swG'), g, { center: g.center });
  };
  $('#swGQ').onkeydown = async (e) => { if (e.key !== 'Enter') return; const q = e.target.value.trim(); if (!q) { load(null); return; }
    const r = await api(`${K}/entities?q=${encodeURIComponent(q)}&limit=1`); if (r.items[0]) location.hash = `#/sage/netz/${r.items[0].id}`; else toast('Nicht gefunden – erst in der Enzyklopädie recherchieren.', 'err'); };
  await load(center);
  return { destroy: () => off?.() };
}

// ---------------------------------------------------------- Meine Entdeckungen --- //
const insCard = (x, i = 0) => `<article class="panel sw-ins ${x.seen ? '' : 'new'}" data-id="${x.id}" style="--i:${i}"><div class="sw-ins-h"><span class="sw-ins-i k-${x.kind}">${icon(INS_ICON[x.kind] || 'sparkle')}</span>
    <div style="min-width:0"><b>${esc(x.title)}</b><div class="dim sm">${ago(x.created)}${x.origin === 'ai' ? ' · mit KI zusammengefasst' : ''}${x.data?.kind === 'interpretation' ? ' · Interpretation' : ''}</div></div><span class="grow"></span>
    <button class="sw-x" data-a="dismiss" title="Ausblenden" aria-label="Ausblenden">×</button></div>
  <div class="sw-body">${md(x.body)}</div>
  ${x.reason ? `<div class="sw-why">${icon('alert')}<span><b>Warum?</b> ${esc(x.reason)}</span></div>` : ''}
  <div class="row" style="margin-top:10px">${Object.entries(x.entity_names || {}).map(([eid, n]) => `<a class="chip acc" href="#/sage/o/${eid}">${esc(n)}</a>`).join('')}<span class="grow"></span>
    ${x.sources.map(s => `<a class="chip" href="${esc(s.url || '#')}" target="_blank" rel="noopener" title="${esc(s.title)}">${esc(SRC_KIND[s.kind] || s.kind)}${s.year ? ' ' + s.year : ''}</a>`).join('')}</div></article>`;

async function insightsView(el) {
  crumbs([['Sage', '#/sage/wiki'], ['Meine Entdeckungen']]);
  let d = await api(`${K}/insights`);
  const draw = () => {
    const r = d.report?.tasks || {};
    el.innerHTML = `<div class="pagehead"><div><h1>Meine Entdeckungen</h1><p class="mut">Sage forscht im Hintergrund weiter: neue Arbeiten zu deinen Objekten, Verborgenes in deinen Bildern, Zusammenhänge zwischen deinen Aufnahmen.</p></div>
        <span class="grow"></span><button class="btn" id="swRun" ${d.running ? 'disabled' : ''}>${d.running ? '<span class="spinner"></span>Sucht …' : icon('refresh') + 'Jetzt suchen'}</button></div>
      <div class="split sw-insl"><div id="swFeed">${d.items.length ? `<div class="stagger">${d.items.map(insCard).join('')}</div>`
        : `<div class="empty">${icon('sparkle')}<h2>Noch keine Entdeckungen</h2>Sage braucht eigene Aufnahmen oder bestimmte Bilder – dann sucht es automatisch ${d.proactive ? `alle ${d.hours} h` : '(Hintergrundsuche ist aus)'}.</div>`}</div>
        <div><div class="panel"><div class="panel-h"><h2>Hintergrundaufgaben</h2><span class="grow"></span><a class="linkbtn" href="#/settings/sage">Einstellen</a></div>
          <p class="mut sm" style="margin:0 0 10px">${d.proactive ? `Automatisch alle ${d.hours} h` : 'Automatik aus'} · zuletzt ${d.last ? ago(d.last) : 'nie'}</p>
          ${d.tasks.map(t => `<div class="sw-taskrow ${t.enabled ? '' : 'off'}"><b>${esc(t.title)}</b><span class="grow"></span>${r[t.id] ? (r[t.id].ok ? `<span class="ok sm">${r[t.id].new ? r[t.id].new + ' neu' : 'ok'}</span>` : `<span class="bad sm" title="${esc(r[t.id].error)}">Fehler</span>`) : t.enabled ? '' : '<span class="dim sm">aus</span>'}
            <div class="dim sm">${esc(t.description)}</div></div>`).join('')}
          ${!d.observer_lat ? '<p class="dim sm" style="margin:10px 0 0">Für Beobachtungsvorschläge den Standort (Breitengrad) in den Einstellungen eintragen.</p>' : ''}</div></div></div>`;
    $('#swRun').onclick = async () => {
      try { const t = await send(`${K}/insights/run`, {}); d.running = true; draw();
        watch(t.id, async (res) => { d = await api(`${K}/insights`); draw(); toast(res.message || 'Fertig', res.state === 'done' ? 'ok' : 'err'); }); }
      catch (err) { toast(err.message, 'err'); }
    };
    $('#swFeed').onclick = async (ev) => {
      const b = ev.target.closest('[data-a=dismiss]'); if (!b) return;
      const art = b.closest('[data-id]'); art.classList.add('out');
      await send(`${K}/insights/${art.dataset.id}`, { dismissed: true }, 'PATCH');
      setTimeout(() => art.remove(), 300);
    };
  };
  draw();
  if (d.items.some(x => !x.seen)) setTimeout(() => send(`${K}/insights/seen`).catch(() => {}), 2500);
  return { actions: () => [['refresh', 'Nach Entdeckungen suchen', 'Hintergrundaufgaben jetzt starten', () => $('#swRun')?.click()]] };
}

// --------------------------------------------------------- Einstellungen --- //
async function settingsExtra(el) {
  const [c, u] = await Promise.all([api('/sage/config'), api(`${K}/usage`).catch(() => null)]);
  const box = document.createElement('div');
  box.className = 'split'; box.style.marginTop = '16px';
  const chk = (n, t, s) => `<label class="modrow"><div><b>${t}</b>${s ? `<div class="mut sm">${s}</div>` : ''}</div><input type="checkbox" name="${n}" ${c[n] !== false ? 'checked' : ''}><span class="switch"></span></label>`;
  box.innerHTML = `<div><form class="panel" id="swS1"><h2>Enzyklopädie & Recherche</h2><p class="mut sm" style="margin:6px 0 14px">Messwerte kommen aus SIMBAD (Spiegel Harvard), Literatur aus SIMBAD, arXiv und optional NASA ADS.
        Gesendet werden nur Objektnamen und Koordinaten. Die KI (Anbieter wie bei der Erzähl-KI) schreibt belegte Entwürfe.</p>
      ${chk('catalog_online', 'Fachkataloge online abfragen', 'SIMBAD, arXiv, ADS, SkyView – aus = nur lokaler Katalog und Cache')}
      ${chk('auto_research', 'Erkannte Objekte automatisch ergänzen', 'Nach jeder Bildbestimmung fehlende Katalogdaten holen (ohne KI)')}
      <div class="field"><span>Modell für Recherche und Chat</span><div id="pRes"></div><small>leer = Modell der Erzähl-KI. Für Werkzeugaufrufe eignen sich z. B. GPT-4o-mini, Claude, Qwen3.</small></div>
      <label class="field"><span>NASA-ADS-Token (optional)</span><input name="ads_token" type="password" autocomplete="off" placeholder="${c.has_ads_token ? '•••••• (gesetzt – leer lassen zum Behalten)' : 'ui.adsabs.harvard.edu → Settings → API Token'}"></label>
      <div class="row" style="flex-wrap:nowrap;gap:12px;align-items:start"><label class="field" style="flex:1"><span>Monatsbudget KI ($)</span><input name="budget_month_usd" inputmode="decimal" value="${c.budget_month_usd}"></label>
        <label class="field" style="flex:1"><span>Budget je Recherche ($)</span><input name="budget_run_usd" inputmode="decimal" value="${c.budget_run_usd}"></label>
        <label class="field" style="width:110px"><span>Max. Schritte</span><input name="max_steps" inputmode="numeric" value="${c.max_steps}"></label></div>
      ${u ? `<div class="sw-usage"><div class="row"><b>Diesen Monat</b><span class="grow"></span><span class="tnum">${u.cost.toFixed(3).replace('.', ',')} $ von ${(+u.budget_month || 0).toFixed(2).replace('.', ',')} $${u.estimated ? ' <span class="dim">(teils geschätzt)</span>' : ''}</span></div>
        <div class="progress static" style="margin-top:6px"><i style="width:${Math.min(100, u.cost / Math.max(1e-6, +u.budget_month || 1) * 100)}%"></i></div><div class="dim sm" style="margin-top:4px">${u.calls} KI-Aufrufe · ${nf.format(u.tokens)} Tokens${u.by_purpose.map(p => ` · ${({ research: 'Recherche', chat: 'Chat', story: 'Erzählungen', proactive: 'Hintergrund' })[p.purpose] || p.purpose} ${(p.cost || 0).toFixed(3).replace('.', ',')} $`).join('')}</div></div>` : ''}
      <div class="row"><button class="btn" type="submit">Speichern</button></div></form></div>
    <div><form class="panel" id="swS2"><h2>Hintergrundaufgaben</h2><p class="mut sm" style="margin:6px 0 14px">Füllen „Meine Entdeckungen“. Nutzen Fachkataloge; die KI nur, wenn unten freigegeben (im Budget).</p>
      ${chk('proactive', 'Automatisch suchen')}
      <div class="row" style="flex-wrap:nowrap;gap:12px;align-items:start"><label class="field" style="flex:1"><span>Intervall (Stunden)</span><input name="proactive_hours" inputmode="numeric" value="${c.proactive_hours}"></label>
        <label class="field" style="flex:1"><span>Veraltet nach (Tagen)</span><input name="stale_days" inputmode="numeric" value="${c.stale_days}"></label>
        <label class="field" style="flex:1"><span>Breitengrad</span><input name="observer_lat" inputmode="decimal" value="${esc(c.observer_lat)}" placeholder="z. B. 51,2"></label></div>
      ${chk('proactive_llm', 'KI im Hintergrund erlauben', 'Neue Forschung zusammenfassen, fehlende Artikel schreiben – kostet Budget')}
      <div class="sw-tasks">${[['task_fill', 'Fehlende Einträge ergänzen'], ['task_papers', 'Neue Forschung'], ['task_field', 'Mehr in deinen Bildern'], ['task_region', 'Gemeinsame Regionen'], ['task_stale', 'Veraltetes erkennen'], ['task_targets', 'Beobachtungsvorschläge']].map(([n, t]) => chk(n, t)).join('')}</div>
      <div class="row"><button class="btn" type="submit">Speichern</button></div></form></div>`;
  el.append(box);
  $('[name=proactive_llm]', box).checked = !!c.proactive_llm;
  picker($('#pRes', box), { name: 'research_model', value: c.research_model, placeholder: 'wie Erzähl-KI', empty: 'wie Erzähl-KI', load: () => api('/sage/models?kind=story') });
  for (const f of [$('#swS1', box), $('#swS2', box)]) f.onsubmit = async (ev) => {
    ev.preventDefault();
    const body = {};
    for (const x of f.elements) if (x.name) body[x.name] = x.type === 'checkbox' ? x.checked : x.value;
    if (!body.ads_token) delete body.ads_token;
    try { await send('/sage/config', body); toast('Gespeichert.', 'ok'); } catch (err) { toast(err.message, 'err'); }
  };
}

// ---------------------------------------------------------- Registrierung --- //
const M = AF.modules.sage;
if (M) {
  M.routes.unshift(
    [/^\/sage\/wiki$/, 'sage-wiki', wikiView],
    [/^\/sage\/o\/([^/]+)(?:\/(\w+))?$/, 'sage-wiki', objectView],
    [/^\/sage\/entdeckungen$/, 'sage-ins', insightsView],
    [/^\/sage\/netz(?:\/([^/]+))?$/, 'sage-net', graphView],
  );
  const baseSettings = M.settings, basePalette = M.palette;
  M.settings = async (el) => { await baseSettings(el); await settingsExtra(el); };
  M.palette = async () => {
    const [base, ents] = await Promise.all([basePalette(), api(`${K}/entities?limit=300`).catch(() => ({ items: [] }))]);
    return [['book', 'Enzyklopädie', 'Sage', '#/sage/wiki'], ['sparkle', 'Meine Entdeckungen', 'Sage', '#/sage/entdeckungen'], ['orbit', 'Wissensnetz', 'Sage', '#/sage/netz'],
      ['sparkle', 'Frag Sage', 'Assistent mit Werkzeugen', () => chat({ scope: 'global' })], ...ents.items.map(c => ['star', c.name, `${c.otype_label || ''} · Enzyklopädie`, `#/sage/o/${c.id}`, c.image]), ...base];
  };
}
window.SageWiki = { card, chat, pick, research: startResearch };
})();
