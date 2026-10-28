'use strict';
/* AstroFlow Web-App: Hash-Routing, Ansichten mit gezielten Live-Updates, Bildbetrachter mit Zoom/Vergleich. */

// ---------------------------------------------------------------- Helfer --- //
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const icon = (n) => `<svg><use href="#i-${n}"/></svg>`;
async function api(u, opt = {}) {
  const r = await fetch('/api' + u, opt);
  if (!r.ok) {
    let msg = r.statusText;
    try { msg = (await r.json()).detail || msg; } catch { /* kein JSON */ }
    throw new Error(msg || ('HTTP ' + r.status));
  }
  return r.json();
}
const send = (u, body, method = 'POST') => api(u, { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body ?? {}) });
const dur = (s) => {
  s = Math.round(s || 0);
  const h = Math.floor(s / 3600), m = Math.floor(s % 3600 / 60);
  return h ? `${h} h ${String(m).padStart(2, '0')} min` : m ? `${m} min` : `${s} s`;
};
const ago = (t) => {
  if (!t) return '–';
  const d = Date.now() / 1000 - t;
  if (d < 60) return 'gerade eben';
  if (d < 3600) return `vor ${Math.floor(d / 60)} min`;
  if (d < 86400) return `vor ${Math.floor(d / 3600)} h`;
  return new Date(t * 1000).toLocaleDateString('de-DE', { day: '2-digit', month: 'short', year: 'numeric' });
};
const fdate = (t) => new Date(t * 1000).toLocaleString('de-DE', { day: '2-digit', month: '2-digit', year: '2-digit', hour: '2-digit', minute: '2-digit' });
const fnight = (n) => n ? new Date(n + 'T12:00').toLocaleDateString('de-DE', { day: '2-digit', month: 'short', year: 'numeric' }) : '–';
const fmb = (b) => b > 1e9 ? (b / 1e9).toFixed(1) + ' GB' : (b / 1e6).toFixed(1) + ' MB';
const running = (j) => j && (j.state === 'queued' || j.state === 'running');
const BAND_COLOR = { L: '#d6dbe8', R: '#f87171', G: '#4ade80', B: '#60a5fa', Ha: '#fb7185', OIII: '#22d3ee', SII: '#f59e0b', RGB: '#a78bfa' };
const PALETTE_LABEL = { auto: 'Automatisch', RGB: 'RGB', LRGB: 'LRGB', SHO: 'SHO (Hubble)', HOO: 'HOO (Bicolor)', mono: 'Mono', OSC: 'Farbkamera' };
const TABS = [['bild', 'Bild'], ['aufnahmen', 'Aufnahmen'], ['versionen', 'Versionen'], ['optionen', 'Optionen']];
const PROFILE_LABEL = { auto: 'Automatisch (nach Objekttyp)', generic: 'Allgemein', galaxy: 'Galaxie', emission: 'Emissionsnebel', reflection: 'Reflexionsnebel', planetary: 'Planetarischer Nebel', snr: 'Supernova-Überrest', globular: 'Kugelsternhaufen', open_cluster: 'Offener Sternhaufen' };

const sexa = (v, hours) => {
  const sign = v < 0 ? '−' : hours ? '' : '+';
  let a = Math.abs(hours ? v / 15 : v), d = Math.floor(a), m = Math.floor((a - d) * 60), s = ((a - d) * 60 - m) * 60;
  if (s >= 59.95) { s = 0; m += 1; } if (m >= 60) { m = 0; d += 1; }
  return hours ? `${sign}${d}h ${String(m).padStart(2, '0')}m ${s.toFixed(1).padStart(4, '0')}s`
    : `${sign}${d}° ${String(m).padStart(2, '0')}′ ${String(Math.round(s)).padStart(2, '0')}″`;
};
const SOURCE_LABEL = { solve: 'Plate-Solving', catalog: 'Katalog (Objektname)', header: 'FITS-Header' };
const SOLVER_LABEL = { astap: 'ASTAP', astrometry: 'astrometry.net (lokal)', nova: 'nova.astrometry.net' };
function coordsPanel(c) {
  if (!c) return '';
  const s = c.solve;
  return `<div class="panel"><h2 style="margin-bottom:12px">Himmelsposition</h2><dl class="kv">
    <dt>Rektaszension</dt><dd class="tnum">${sexa(c.ra, true)}</dd>
    <dt>Deklination</dt><dd class="tnum">${sexa(c.dec, false)}</dd>
    <dt>Quelle</dt><dd>${esc(SOURCE_LABEL[c.source] || c.source)}${s ? ` · ${esc(SOLVER_LABEL[s.solver] || s.solver)}` : ''}</dd>
    ${s ? `<dt>Bildmaßstab</dt><dd class="tnum">${s.scale.toFixed(2)}″/px</dd>
    <dt>Bildfeld</dt><dd class="tnum">${(s.fov_w * 60).toFixed(0)}′ × ${(s.fov_h * 60).toFixed(0)}′</dd>
    <dt>Drehung</dt><dd class="tnum">${s.rotation.toFixed(1)}°</dd>` : ''}
  </dl></div>`;
}

const QC_METRIC = [['bg', 'Hintergrund', v => v.toFixed(3)], ['chroma_noise', 'Farbrauschen', v => v.toFixed(4)],
  ['gradient', 'Restgradient', v => v.toFixed(3)], ['white_clip', 'Ausgebrannt', v => (v * 100).toFixed(2) + ' %'],
  ['saturation', 'Farbigkeit', v => v.toFixed(2)]];

function qualityPanel(s) {
  const q = s.quality;
  if (!q) return '';
  const cls = q.score >= 8.5 ? 'ok' : q.score >= 6.5 ? 'warn' : 'bad';
  const label = q.score >= 8.5 ? 'sehr gut' : q.score >= 6.5 ? 'gut, mit Schwächen' : 'verbesserungswürdig';
  const auto = (s.qc || []).flatMap(h => h.round === 0 ? (h.adapt || [])
    : Object.keys(h.adjust || {}).length ? [`Nachgeregelt (Runde ${h.round}): ${Object.entries(h.adjust).map(([k, v]) => `${k} → ${v}`).join(', ')}`] : []);
  const m = q.metrics || {}, snr = q.measure?.snr;
  return `<div class="panel"><div class="panel-h" style="margin-bottom:12px"><span class="qscore ${cls}" style="--p:${q.score * 10}" title="${q.score.toFixed(1)} von 10"><b class="tnum">${q.score.toFixed(1)}</b></span>
      <div><h2>Bildqualität</h2><div class="mut sm">${label}${snr != null ? ` · Objekt-SNR ${Math.round(snr)}` : ''}</div></div></div>
    ${q.issues?.length ? `<ul class="qlist">${q.issues.map(i => `<li class="warn">${esc(i.text)}</li>`).join('')}</ul>` : '<p class="ok sm" style="margin:0 0 10px">Keine messbaren Probleme gefunden.</p>'}
    ${q.hints?.length ? `<div class="qhints"><small class="mut">Verbesserungsmöglichkeiten</small><ul>${q.hints.map(h => `<li>${esc(h)}</li>`).join('')}</ul></div>` : ''}
    <details class="qmore"><summary class="sm mut">Messwerte und automatische Anpassungen</summary>
      <dl class="kv" style="margin-top:8px">${QC_METRIC.filter(([k]) => m[k] != null).map(([k, l, f]) => `<dt>${l}</dt><dd class="tnum">${f(m[k])}</dd>`).join('')}</dl>
      ${auto.length ? `<ul class="qlist mut" style="margin-top:8px">${auto.map(a => `<li>${esc(a)}</li>`).join('')}</ul>` : ''}</details></div>`;
}

// Hinweis unten rechts; action = { label, href } oder { label, fn } für eine Schaltfläche (z. B. „Ansehen“, „Rückgängig“)
function toast(msg, kind = '', action = null) {
  const t = document.createElement('div'), ms = action ? 7000 : 4200;
  t.className = 'toast ' + kind;
  t.innerHTML = `<span></span>${action ? `<a class="btn ghost sm" href="${action.href || '#'}">${esc(action.label)}</a>` : ''}<i class="tbar" style="animation-duration:${ms}ms"></i>`;
  t.firstChild.textContent = msg;
  const close = () => { t.classList.add('out'); setTimeout(() => t.remove(), 300); };
  if (action) $('a', t).onclick = (e) => { if (action.fn) { e.preventDefault(); action.fn(); } close(); };
  $('#toasts').append(t);
  let timer = setTimeout(close, ms);
  t.onmouseenter = () => { clearTimeout(timer); $('.tbar', t).style.animationPlayState = 'paused'; };
  t.onmouseleave = () => { timer = setTimeout(close, 1800); };
}
// Gleitender Marker unter/hinter dem aktiven Element (Navigation, Tabs)
function slideInd(ind, el, line = false) {
  if (!ind) return;
  if (!el) { ind.style.opacity = '0'; return; }
  ind.style.opacity = '1';
  ind.style.transform = `translate(${el.offsetLeft}px,${line ? 0 : el.offsetTop}px)`;
  ind.style.width = el.offsetWidth + 'px';
  if (!line) ind.style.height = el.offsetHeight + 'px';
}
function jobLine(j) {
  if (!running(j)) return '';
  const q = j.state === 'queued';
  return `<div class="jobline"><div class="row"><span>${q ? '' : '<span class="live"></span>'}${q ? 'In Warteschlange' : esc(j.stage || 'Läuft')}${j.message ? ' · ' + esc(j.message) : ''}</span>
    <span class="tnum">${q ? '' : Math.round((j.progress || 0) * 100) + ' %'}</span></div>
    <div class="progress ${q ? 'indet' : ''}"><i style="width:${(j.progress || 0) * 100}%"></i></div></div>`;
}
// Jobzeile aktualisieren, ohne sie neu aufzubauen – so gleitet der Balken weich weiter
function setJobLine(host, j) {
  if (!host) return;
  const bar = $('.progress', host), q = j?.state === 'queued';
  if (running(j) && bar && bar.classList.contains('indet') === q) {
    const html = jobLine(j), tmp = document.createElement('div');
    tmp.innerHTML = html;
    $('.row', host).innerHTML = $('.row', tmp).innerHTML;
    $('i', bar).style.width = (j.progress || 0) * 100 + '%';
  } else host.innerHTML = jobLine(j);
}
async function deleteProject(p) {
  const what = [p.n_results ? `${p.n_results} Version${p.n_results > 1 ? 'en' : ''} des Bildes` : '', 'den Master-Stack',
    'hochgeladene Einzelbilder'].filter(Boolean).join(', ');
  if (!await confirmDlg(`„${p.name}“ löschen?`, `Entfernt ${esc(what)}. Das lässt sich nicht rückgängig machen.<br><br>
    <span class="sm">Aufnahmen aus eingebundenen Ordnern bleiben auf der Festplatte und werden künftig ignoriert.</span>`, 'Endgültig löschen', true)) return false;
  try {
    const r = await send(`/projects/${p.id}`, null, 'DELETE');
    toast(`„${r.name}“ gelöscht${r.bytes ? ` – ${fmb(r.bytes)} frei` : ''}.`, 'ok');
    return true;
  } catch (e) {
    toast(e.message, 'err');
    return false;
  }
}

function confirmDlg(title, text, ok = 'OK', danger = false) {
  return new Promise(res => {
    const d = $('#dlg');
    d.innerHTML = `<h2 style="font-size:18px;margin-bottom:8px">${esc(title)}</h2><p class="mut">${text}</p>
      <div class="row" style="justify-content:flex-end;margin-top:18px"><button class="btn ghost" value="0">Abbrechen</button><button class="btn${danger ? ' danger' : ''}" value="1">${esc(ok)}</button></div>`;
    d.onclick = (e) => { const v = e.target.closest('button')?.value; if (v) { d.close(); res(v === '1'); } };
    d.onclose = () => res(false);
    d.showModal();
  });
}

// ---------------------------------------------------------------- Router --- //
let view = null;      // { update?, destroy? }
let pollTimer = null;
const routes = [
  [/^\/?$/, 'gallery', galleryView],
  [/^\/p\/(\d+)(?:\/(\w+))?$/, 'gallery', projectView],
  [/^\/activity$/, 'activity', activityView],
  [/^\/import$/, 'import', importView],
  [/^\/settings$/, 'settings', settingsView],
];
const SKELETON = { gallery: '<div class="skel" style="height:64px;max-width:420px;margin-bottom:26px"></div><div class="skel-grid">' + '<div class="skel"></div>'.repeat(6) + '</div>' };
async function route() {
  const path = location.hash.replace(/^#/, '') || '/';
  $('#pal').open && $('#pal').close();
  // Ansicht kann den Wechsel selbst übernehmen (z. B. Tabs eines Objekts – ohne Neuladen)
  if (view?.navigate?.(path)) return;
  view?.destroy?.();
  view = null;
  const main = $('#view');
  for (const [re, nav, fn] of routes) {
    const m = path.match(re);
    if (!m) continue;
    $$('#nav a').forEach(a => a.classList.toggle('on', a.dataset.r === nav));
    slideInd($('#navind'), $('#nav a.on'));
    main.classList.remove('enter');
    main.innerHTML = path === '/' ? SKELETON.gallery : '<div class="skel" style="height:320px"></div>';
    try { view = await fn(main, ...m.slice(1)); } catch (e) { main.innerHTML = `<div class="empty">${icon('alert')}<h2>Fehler</h2>${esc(e.message)}</div>`; }
    void main.offsetWidth;  // Animation neu starten
    main.classList.add('enter');
    main.focus({ preventScroll: true });
    window.scrollTo({ top: 0 });
    return;
  }
  location.hash = '#/';
}
function crumbs(parts) {
  $('#crumbs').innerHTML = parts.map((p, i) => i < parts.length - 1 ? `<a href="${p[1]}">${esc(p[0])}</a><span class="dim">/</span>` : `<b>${esc(p[0])}</b>`).join('');
}
// Globaler Status: Zähler in der Navigation, Job-Anzeige oben, Meldung wenn ein Durchlauf fertig ist
let runningJobs = null, lastStatus = null;   // id -> Job (null = erster Durchlauf, noch nichts melden)
async function poll() {
  try {
    const st = lastStatus = await api('/status');
    const b = $('#busy');
    b.hidden = !st.busy_jobs;
    b.textContent = st.busy_jobs;
    const lights = st.frames?.light || 0, cal = (st.frames?.dark || 0) + (st.frames?.flat || 0) + (st.frames?.bias || 0);
    $('#sidefoot').innerHTML = `<div><span class="dot${st.auto ? '' : ' off'}"></span>${st.auto ? 'Automatik aktiv' : 'Automatik aus'}</div>
      <div>${lights} Lights · ${cal} Kalibrierframes</div><div>Letzter Scan: ${ago(st.last_scan)}</div>${st.disk ? `<div>${fmb(st.disk.free)} frei</div>` : ''}<div>v${esc(st.version)}</div>`;
    if (st.busy_jobs || runningJobs?.size) await pollJobs(); else { runningJobs = new Map(); showJobPill(null); }
    await view?.update?.(st);
  } catch { /* Server kurz weg – nächster Versuch */ }
}
async function pollJobs() {
  const jobs = await api('/jobs'), now = new Map(jobs.filter(running).map(j => [j.id, j]));
  if (runningJobs) {
    for (const [jid, old] of runningJobs) {
      if (now.has(jid)) continue;
      const j = jobs.find(x => x.id === jid), name = j?.project || old.project || 'Objekt';
      if (location.hash.startsWith(`#/p/${old.project_id}`)) continue;   // die Objektansicht meldet selbst
      if (j?.state === 'error') toast(`„${name}“: Durchlauf fehlgeschlagen.`, 'err', { label: 'Details', href: `#/p/${old.project_id}` });
      else if (j?.state === 'done') toast(`„${name}“ ist fertig.`, 'ok', { label: 'Ansehen', href: `#/p/${old.project_id}` });
    }
  }
  runningJobs = now;
  showJobPill(jobs.find(j => j.state === 'running') || jobs.find(j => j.state === 'queued'), now.size);
}
function showJobPill(j, n = 0) {
  const pill = $('#jobpill');
  document.title = j?.state === 'running' ? `${Math.round((j.progress || 0) * 100)} % · ${j.project || ''} – AstroFlow` : 'AstroFlow';
  if (!j) { pill.hidden = true; return; }
  const pc = Math.round((j.progress || 0) * 100), q = j.state === 'queued';
  pill.href = `#/p/${j.project_id}`;
  pill.title = 'Zum laufenden Durchlauf';
  if (pill.hidden || !$('.t', pill)) { pill.innerHTML = '<span class="spinner"></span><span class="t"></span><i></i>'; pill.hidden = false; }
  $('.t', pill).innerHTML = `<b>${esc(j.project || 'Objekt')}</b> · ${q ? 'wartet' : esc(j.stage || 'läuft')}${q ? '' : ` <span class="tnum">${pc} %</span>`}${n > 1 ? ` <span class="mut">+${n - 1}</span>` : ''}`;
  requestAnimationFrame(() => { $('i', pill).style.width = (q ? 0 : pc) + '%'; });
}

// --------------------------------------------------------------- Galerie --- //
async function galleryView(el) {
  crumbs([['Galerie']]);
  const ui = JSON.parse(localStorage.getItem('af.gallery') || '{}');
  let q = ui.q || '', kind = ui.kind || 'all', sort = ui.sort || 'recent', projects = [], st = await api('/status'), sig = '', order = '';
  el.innerHTML = `<div class="pagehead"><div><h1>Deine Objekte</h1><p class="mut" id="gsum"></p></div><span class="grow"></span>
    <a class="btn" href="#/import">${icon('upload')}Aufnahmen hinzufügen</a></div>
    <div class="toolbar"><input type="search" id="gq" placeholder="Objekt suchen …" title="Taste /" value="${esc(q)}"><div class="seg" id="gkind"></div><span class="grow"></span>
      <select id="gsort"><option value="recent">Zuletzt bearbeitet</option><option value="name">Name</option><option value="exposure">Belichtungszeit</option><option value="frames">Aufnahmen</option></select></div>
    <div id="gallery"></div>`;
  $('#gsort').value = sort;
  const save = () => { try { localStorage.setItem('af.gallery', JSON.stringify({ q, kind, sort })); } catch { /* egal */ } };
  $('#gq').oninput = (e) => { q = e.target.value; save(); draw(); };
  $('#gq').onkeydown = (e) => {
    if (e.key === 'Enter') $('#gallery .card')?.click();
    if (e.key === 'Escape') { e.target.value = q = ''; save(); draw(); e.target.blur(); }
  };
  $('#gsort').onchange = (e) => { sort = e.target.value; save(); draw(); };
  $('#gkind').onclick = (e) => { const b = e.target.closest('button'); if (b) { kind = b.dataset.k; save(); draw(); } };
  const g = $('#gallery');
  // Lichtkegel folgt der Maus auf den Karten
  g.onpointermove = (e) => {
    const c = e.target.closest('.card');
    if (!c) return;
    const r = c.getBoundingClientRect();
    c.style.setProperty('--mx', e.clientX - r.left + 'px');
    c.style.setProperty('--my', e.clientY - r.top + 'px');
  };
  g.onclick = async (e) => {
    const run = e.target.closest('[data-run]'), b = e.target.closest('[data-del]');
    if (!run && !b) return;
    e.preventDefault();
    e.stopPropagation();
    if (run) {
      run.disabled = true;
      try { await send(`/projects/${run.dataset.run}/process`, { restack: false }); toast('Verarbeitung gestartet.'); } catch (err) { toast(err.message, 'err'); }
      await load();
      poll();
      return;
    }
    const p = projects.find(x => x.id === +b.dataset.del);
    if (p && await deleteProject(p)) { sig = ''; await load(); }
  };

  function draw() {
    const kinds = [...new Set(projects.map(p => p.kind || 'generic'))];
    $('#gkind').innerHTML = kinds.length > 1 ? [['all', 'Alle'], ...kinds.map(k => [k, projects.find(p => (p.kind || 'generic') === k).kind_label || 'Allgemein'])]
      .map(([k, l]) => `<button data-k="${k}" class="${kind === k ? 'on' : ''}">${esc(l)}</button>`).join('') : '';
    $('#gkind').style.display = kinds.length > 1 ? '' : 'none';
    const tot = projects.reduce((a, p) => a + (p.result?.exposure || 0), 0);
    $('#gsum').textContent = projects.length ? `${projects.length} Objekt${projects.length > 1 ? 'e' : ''} · ${dur(tot)} Gesamtbelichtung` : '';
    let list = projects.filter(p => (kind === 'all' || (p.kind || 'generic') === kind) && p.name.toLowerCase().includes(q.toLowerCase()));
    const key = { recent: p => -(p.result?.created || p.created || 0), name: p => p.name.toLowerCase(), exposure: p => -(p.result?.exposure || 0), frames: p => -p.total }[sort];
    list.sort((a, b) => key(a) < key(b) ? -1 : key(a) > key(b) ? 1 : 0);
    if (!projects.length) {
      order = '';
      g.innerHTML = `<div class="empty">${icon('star')}<h2>Noch keine Aufnahmen</h2>Lege FITS-Dateien in <code>${esc(st.input_dir)}</code> oder lade sie hoch.<br>AstroFlow erkennt Objekte, sortiert, stackt und bearbeitet alles automatisch.
        <div style="margin-top:18px"><a class="btn" href="#/import">${icon('upload')}Aufnahmen hinzufügen</a></div></div>`;
      return;
    }
    if (!list.length) { order = ''; g.innerHTML = `<div class="empty">${icon('search')}<h2>Keine Treffer</h2>Kein Objekt passt zu deiner Suche.</div>`; return; }
    // Gleiche Reihenfolge: nur geänderte Karten austauschen (keine Einblend-Animation, Bilder bleiben stehen)
    const o = list.map(p => p.id).join();
    if (o === order) {
      list.forEach(p => {
        const c = $(`.card[data-id="${p.id}"]`, g), h = cardInner(p);
        if (c && c.dataset.h !== String(hash(h))) {
          const src = p.result && `/api/results/${p.result.id}/thumb`, thumbSame = src && $('.thumb img', c)?.getAttribute('src') === src;
          c.dataset.h = hash(h);
          c.innerHTML = h;
          if (thumbSame) $('.thumb img', c)?.classList.add('ld');
        }
      });
      return;
    }
    order = o;
    g.innerHTML = `<div class="gallery stagger">${list.map((p, i) => { const h = cardInner(p); return `<a class="card" href="#/p/${p.id}" data-id="${p.id}" data-h="${hash(h)}" style="--i:${i}">${h}</a>`; }).join('')}</div>`;
  }
  function cardInner(p) {
    const r = p.result, pal = r?.stats?.palette, busy = running(p.job);
    return `<div class="thumb ${busy ? 'busy' : ''}">${r ? `<img src="/api/results/${r.id}/thumb" alt="" loading="lazy" decoding="async" onload="this.classList.add('ld')">`
        : `<div class="ph">${icon('star')}${busy ? 'wird verarbeitet …' : 'noch kein Ergebnis'}</div>`}
        ${busy ? '' : `<button class="cardx" data-del="${p.id}" title="Objekt löschen" aria-label="${esc(p.name)} löschen">${icon('trash')}</button>`}
        ${pal && pal !== 'OSC' ? `<span class="chip acc badge">${esc(PALETTE_LABEL[pal] || pal)}</span>` : ''}
        ${busy ? `<span class="state">${p.job.state === 'queued' ? 'wartet' : `<span class="spinner" style="width:12px;height:12px"></span>${Math.round((p.job.progress || 0) * 100)} %`}</span>` : ''}</div>
      <div class="body"><h3>${esc(p.name)}</h3>
        <div class="meta"><span class="chip">${esc(p.kind_label || 'Allgemein')}</span><span class="chip">${p.nights.length} ${p.nights.length === 1 ? 'Nacht' : 'Nächte'}</span>${p.n_results > 1 ? `<span class="chip">${p.n_results} Versionen</span>` : ''}</div>
        ${busy ? jobLine(p.job) : `<div class="foot"><span>${r ? `${r.n_frames} von ${p.total} Aufnahmen` : `${p.total} Aufnahmen`}</span>
          ${r ? `<span>${dur(r.exposure)}</span>` : `<button class="btn sm quick" data-run="${p.id}">${icon('play')}Verarbeiten</button>`}</div>`}
      </div>`;
  }
  async function load() {
    projects = await api('/projects');
    const s = JSON.stringify(projects);
    if (s !== sig) { sig = s; draw(); }
  }
  await load();
  const onKey = (e) => { if (e.key === '/' && !e.target.closest('input,select,textarea')) { e.preventDefault(); $('#gq').focus(); } };
  document.addEventListener('keydown', onKey);
  return { update: load, destroy: () => document.removeEventListener('keydown', onKey) };
}
const hash = (s) => { let h = 0; for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) | 0; return h; };

// ---------------------------------------------------------------- Projekt --- //
async function projectView(el, id, tab = 'bild') {
  id = +id;
  let p = await api('/projects/' + id), frames = null, sel = null, cmp = null, viewer = null, sig = '', before = false;
  let fFilter = 'all', fBand = 'all', fSort = ['date', 1];
  crumbs([['Galerie', '#/'], [p.name]]);

  function head() {
    const r = p.result, busy = running(p.job);
    return `<div class="pagehead"><div style="min-width:0">
        <button class="namebtn" id="rename" title="Umbenennen"><h1 style="display:inline">${esc(p.name)}</h1>${icon('edit')}</button>
        <div class="row" style="margin-top:8px"><span class="chip">${esc(p.kind_label || 'Allgemein')}</span>
          ${r?.stats?.palette ? `<span class="chip acc">${esc(PALETTE_LABEL[r.stats.palette] || r.stats.palette)}</span>` : ''}
          <span class="chip">${p.total} Aufnahmen</span><span class="chip">${p.nights.length} ${p.nights.length === 1 ? 'Nacht' : 'Nächte'}</span>
          ${r ? `<span class="chip">${dur(r.exposure)} integriert</span>` : ''}
          ${p.coords?.source === 'solve' ? `<span class="chip acc" title="Koordinaten per Plate-Solving bestimmt">${icon('star')}Plate-Solved</span>` : ''}</div></div>
      <span class="grow"></span>
      <div class="row"><button class="btn ${busy ? 'busy' : ''}" id="run" ${busy ? 'disabled' : ''}>${icon(busy ? 'refresh' : 'play')}${busy ? 'Läuft …' : r ? 'Aktualisieren' : 'Verarbeiten'}</button>
        ${r ? `<div class="menu" id="dl"><button class="btn ghost">${icon('down')}Export${icon('chev')}</button><div class="pop">
          <a href="/api/results/${r.id}/tif">TIFF 16-bit <small>Druck/Bearbeitung</small></a><a href="/api/results/${r.id}/png">PNG <small>verlustfrei</small></a>
          <a href="/api/results/${r.id}/jpg" download>JPEG <small>zum Teilen</small></a><a href="/api/results/${r.id}/fits">FITS linear <small>für PixInsight & Co.</small></a></div></div>` : ''}
        <div class="menu" id="more"><button class="btn ghost">Mehr${icon('chev')}</button><div class="pop">
          ${r ? `<a href="#" data-act="develop">Neu entwickeln <small>ohne Stacking</small></a>` : ''}
          ${p.deleted?.can_restack === false ? `<span class="pop-note">Komplett neu stacken nicht möglich – Einzelbilder nach dem Stacking gelöscht</span>`
            : `<a href="#" data-act="restack">Komplett neu stacken <small>alle Aufnahmen</small></a>`}
          ${p.can_solve ? `<a href="#" data-act="solve">Objekt per Plate-Solving bestimmen <small>Sternmuster</small></a>` : ''}
          <a href="#" data-act="rename">Umbenennen</a>
          ${busy ? '' : `<hr><a href="#" data-act="delete" class="danger">Objekt löschen <small>mit allen Versionen</small></a>`}
        </div></div></div></div>
      ${p.can_solve && !p.coords && !busy ? `<div class="notice">${icon('star')}<div><b>Objekt unbekannt</b><div class="mut sm">Die Aufnahmen enthalten weder Objektnamen noch Koordinaten.
        Plate-Solving bestimmt die Himmelsposition aus dem Sternmuster und ordnet sie dem richtigen Objekt zu.</div></div><span class="grow"></span>
        <button class="btn sm" id="solveBtn">Jetzt bestimmen</button></div>` : ''}
      ${p.job?.state === 'error' && !busy ? `<div class="notice err"><div><b>Letzter Durchlauf fehlgeschlagen</b><div class="mut sm">${esc(p.job.message)}</div></div></div>` : ''}
      <div id="job" style="margin:-8px 0 18px">${jobLine(p.job)}</div>
      <nav class="tabs" id="tabs">${TABS.map(([k, l], i) => { const n = { aufnahmen: p.total, versionen: p.results.length }[k];
        return `<a href="#/p/${id}/${k}" data-t="${k}" class="${tab === k ? 'on' : ''}" title="Taste ${i + 1}">${l}${n != null ? `<span>${n}</span>` : ''}</a>`; }).join('')}<i class="tab-ind" id="tabind"></i></nav>
      <div id="tab"></div>`;
  }

  function draw() {
    el.innerHTML = head();
    $('#rename').onclick = rename;
    $('#run').onclick = () => run(false);
    $$('.menu', el).forEach(m => {
      $('button', m).onclick = (e) => { e.stopPropagation(); $$('.menu.open').forEach(o => o !== m && o.classList.remove('open')); m.classList.toggle('open'); };
    });
    $('#more .pop').onclick = (e) => {
      const a = e.target.closest('[data-act]');
      if (a) { e.preventDefault(); ({ solve, develop, rename, delete: remove }[a.dataset.act] || (() => run(true)))(); }
      $('#more').classList.remove('open');
    };
    $('#solveBtn')?.addEventListener('click', solve);
    drawTab(false);
  }
  // Nur den Tab-Inhalt neu zeichnen – Kopf, Jobzeile und Marker bleiben stehen
  function drawTab(animate = true) {
    viewer?.destroy();
    viewer = null;
    $$('#tabs a').forEach(a => a.classList.toggle('on', a.dataset.t === tab));
    requestAnimationFrame(() => slideInd($('#tabind'), $('#tabs a.on'), true));
    const t = $('#tab');
    t.classList.remove('enter');
    ({ bild: tabImage, aufnahmen: tabFrames, versionen: tabVersions, optionen: tabOptions }[tab] || tabImage)(t);
    if (animate) { void t.offsetWidth; t.classList.add('enter'); }
  }

  // ---- Tab: Bild ----
  function tabImage(t) {
    const r = p.results.find(x => x.id === sel) || p.results[0];
    const c = cmp && p.results.find(x => x.id === cmp);
    if (!r) {
      t.innerHTML = `<div class="viewer"><div class="none"><div>${running(p.job) ? '<div class="spinner"></div>Wird gerade verarbeitet – das Bild erscheint hier, sobald es fertig ist.'
        : `Noch kein Ergebnis.<br><br><button class="btn" id="run2">${icon('play')}Jetzt verarbeiten</button>`}</div></div></div>`;
      $('#run2')?.addEventListener('click', () => run(false));
      return;
    }
    const s = r.stats || {}, bands = s.bands || {}, bmax = Math.max(1, ...Object.values(bands).map(b => b.exposure));
    const rev = r.review || [];
    t.innerHTML = `<div class="split"><div>
        <div id="viewer"></div>
        ${r.has_before && !c ? `<div class="row" style="margin-top:10px"><div class="seg" id="ba"><button data-b="0" class="${before ? '' : 'on'}">Ergebnis</button>
          <button data-b="1" class="${before ? 'on' : ''}">Vorher / Nachher</button></div>
          ${before ? '<span class="mut sm">Links der unbearbeitete Master-Stack, rechts das entwickelte Bild – Trennlinie ziehen</span>' : ''}</div>` : ''}
        ${r.id !== p.results[0].id || c ? `<div class="row sm mut" style="margin-top:8px">${c ? `Vergleich: links Version vom ${fdate(c.created)}, rechts ${fdate(r.created)}` : `Ältere Version vom ${fdate(r.created)}`}
          <span class="grow"></span><button class="linkbtn" id="latest">Neueste Version zeigen</button></div>` : ''}
        <div class="stats">
          <div class="stat"><small>Integration</small><b>${dur(r.exposure)}</b></div>
          <div class="stat"><small>Gestackt</small><b class="tnum">${r.n_frames}</b></div>
          <div class="stat"><small>Sternschärfe (FWHM)</small><b class="tnum">${s.fwhm_px ?? '–'} px</b></div>
          <div class="stat"><small>Auflösung</small><b class="tnum">${s.shape ? s.shape[1] + '×' + s.shape[0] : '–'}</b></div>
        </div></div>
      <div>
        ${Object.keys(bands).length > 1 || (Object.keys(bands).length === 1 && !bands.RGB) ? `<div class="panel"><div class="panel-h"><h2>Kanäle</h2><span class="grow"></span><span class="chip acc">${esc(PALETTE_LABEL[s.palette] || s.palette || '')}</span></div>
          <div class="bands">${Object.entries(bands).map(([b, v]) => `<div class="band"><span><span class="chip"><i style="background:${BAND_COLOR[b] || '#999'}"></i>${esc(b)}</span></span>
            <div class="progress"><i style="width:${v.exposure / bmax * 100}%;background:${BAND_COLOR[b] || 'var(--acc)'}"></i></div><span class="mut sm tnum">${v.frames} · ${dur(v.exposure)}</span></div>`).join('')}</div></div>` : ''}
        ${qualityPanel(s)}
        <div class="panel"><h2 style="margin-bottom:12px">Details</h2><dl class="kv">
          <dt>Erstellt</dt><dd>${fdate(r.created)}</dd>
          <dt>Bearbeitungsprofil</dt><dd>${esc(PROFILE_LABEL[s.profile] || s.profile || '–')}</dd>
          <dt>Aufnahmetyp</dt><dd>${s.palette === 'OSC' || !s.palette ? 'Farbkamera' : 'Mono + Filter'}${s.narrowband ? ' · Schmalband' : ''}</dd>
          <dt>Sterne erkannt</dt><dd class="tnum">${s.stars ?? '–'}</dd>
          <dt>Sterne</dt><dd>${s.star_separation ? `getrennt bearbeitet (${s.star_separation === 'starnet' ? 'StarNet' : 'eigene Trennung'})` : 'mit dem Bild bearbeitet'}</dd>
          ${s.wb_gains ? `<dt>Weißabgleich (R/G/B)</dt><dd class="tnum">${s.wb_gains.join(' / ')}</dd>` : ''}
          ${s.color_cal ? `<dt>Farbkalibrierung</dt><dd>${s.color_cal.verfahren === 'gaia' ? `fotometrisch (Gaia, ${s.color_cal.sterne} Sterne)`
            : s.color_cal.verfahren === 'manuell' ? 'manuell' : `Sternfarben-Mittel${s.color_cal.grund ? `<div class="dim sm">${esc(s.color_cal.grund)}</div>` : ''}`}</dd>` : ''}
          <dt>Nächte</dt><dd>${p.nights.map(fnight).join(', ') || '–'}</dd>
          ${p.deleted?.frames ? `<dt>Einzelbilder</dt><dd>${p.deleted.frames} gelöscht · ${fmb(p.deleted.bytes)} frei</dd>` : ''}
        </dl></div>
        ${coordsPanel(p.coords)}
        ${rev.length ? `<div class="panel"><h2 style="margin-bottom:12px">KI-Bildprüfung</h2><div class="review">${rev.map(x => x.error
          ? `<div><b>Runde ${x.round}</b> <span class="warn">${esc(x.error)}</span></div>`
          : `<div><b>Runde ${x.round}</b> · <span class="${x.ok ? 'ok' : 'warn'}">${x.ok ? 'in Ordnung' : 'nachgebessert'}</span> <span class="mut">(${esc(x.score)}/10)</span>
            ${x.issues?.length ? `<ul>${x.issues.map(i => `<li>${esc(i)}</li>`).join('')}</ul>` : ''}
            ${Object.keys(x.adjust || {}).length ? `<div class="mut sm">${Object.entries(x.adjust).map(([k, v]) => `${esc(k)} → ${esc(v)}`).join(' · ')}</div>` : ''}</div>`).join('')}</div></div>` : ''}
      </div></div>`;
    const ph = `/api/results/${r.id}/thumb`;
    viewer = c ? createViewer($('#viewer'), `/api/results/${r.id}/jpg`, `/api/results/${c.id}/jpg`, undefined, ph)
      : before && r.has_before ? createViewer($('#viewer'), `/api/results/${r.id}/jpg`, `/api/results/${r.id}/before`, ['vorher', 'nachher'], ph)
      : createViewer($('#viewer'), `/api/results/${r.id}/jpg`, null, undefined, ph);
    $('#ba')?.addEventListener('click', (e) => { const b = e.target.closest('[data-b]'); if (b) { before = b.dataset.b === '1'; drawTab(false); } });
    $('#latest')?.addEventListener('click', () => { sel = null; cmp = null; drawTab(); });
  }

  // ---- Tab: Aufnahmen ----
  async function tabFrames(t) {
    if (!frames) { t.innerHTML = '<div class="skel" style="height:300px"></div>'; frames = await api(`/projects/${id}/frames`); }
    const bands = [...new Set(frames.map(f => f.band || 'RGB'))];
    const n = (fn) => frames.filter(fn).length;
    t.innerHTML = `<div class="panel"><div class="panel-h"><h2>Qualität je Aufnahme</h2><span class="grow"></span>
        <span class="mut sm">${n(f => f.stacked)} gestackt · ${n(f => f.status === 'rejected')} verworfen</span></div>
        ${chart(frames)}<div class="legend"><span><i style="background:var(--acc)"></i>Sternzahl</span><span><i style="background:#fb923c"></i>FWHM (Unschärfe)</span><span><i style="background:var(--bad);height:8px;width:3px"></i>verworfen</span></div></div>
      <div class="panel"><div class="toolbar" style="margin-bottom:12px">
        <div class="seg" id="ff">${[['all', 'Alle', frames.length], ['stacked', 'Gestackt', n(f => f.stacked)], ['selected', 'Ausgewählt', n(f => f.status === 'selected' && !f.stacked)], ['rejected', 'Verworfen', n(f => f.status === 'rejected')]]
          .filter(([k, , c]) => k === 'all' || c).map(([k, l, c]) => `<button data-v="${k}" class="${fFilter === k ? 'on' : ''}">${l} <span class="dim">${c}</span></button>`).join('')}</div>
        ${bands.length > 1 ? `<div class="seg" id="fb">${['all', ...bands].map(b => `<button data-v="${b}" class="${fBand === b ? 'on' : ''}">${b === 'all' ? 'Alle Filter' : esc(b)}</button>`).join('')}</div>` : ''}
        <span class="grow"></span><span class="mut sm">Klick auf „ausschließen“ entfernt eine Aufnahme dauerhaft</span></div>
        <div class="tablewrap" id="ftable"></div></div>`;
    $('#ff').onclick = (e) => { const b = e.target.closest('button'); if (b) { fFilter = b.dataset.v; tabFrames(t); } };
    $('#fb') && ($('#fb').onclick = (e) => { const b = e.target.closest('button'); if (b) { fBand = b.dataset.v; tabFrames(t); } });
    drawTable();
  }
  function drawTable() {
    const cols = [['name', 'Datei'], ['band', 'Filter'], ['night', 'Nacht'], ['n_stars', 'Sterne'], ['fwhm', 'FWHM'], ['ecc', 'Exzentr.'], ['score', 'Score'], ['status', 'Status']];
    let list = frames.filter(f => (fFilter === 'all' || (fFilter === 'stacked' ? f.stacked : fFilter === 'selected' ? f.status === 'selected' && !f.stacked : f.status === fFilter))
      && (fBand === 'all' || (f.band || 'RGB') === fBand));
    const [k, dir] = fSort;
    if (k !== 'date') list.sort((a, b) => ((a[k] ?? -1e9) < (b[k] ?? -1e9) ? -1 : (a[k] ?? -1e9) > (b[k] ?? -1e9) ? 1 : 0) * dir);
    const stat = (f) => f.stacked ? ['stacked', 'gestackt'] : f.status === 'selected' ? ['selected', 'ausgewählt'] : f.status === 'rejected' ? ['rejected', 'verworfen'] : f.status === 'failed' ? ['failed', 'Fehler'] : ['new', 'neu'];
    $('#ftable').innerHTML = `<table><thead><tr>${cols.map(([c, l]) => `<th data-k="${c}" class="${k === c ? 'sorted' + (dir > 0 ? ' asc' : '') : ''}">${l}</th>`).join('')}<th></th></tr></thead><tbody>
      ${list.map(f => { const [sc, sl] = stat(f); return `<tr data-id="${f.id}"><td class="name" title="${esc(f.name)}">${esc(f.name)}</td>
        <td>${f.band ? `<span class="chip"><i style="background:${BAND_COLOR[f.band] || '#999'}"></i>${esc(f.band)}</span>` : '<span class="dim">Farbe</span>'}</td>
        <td>${fnight(f.night)}</td><td class="tnum">${f.n_stars ?? ''}</td><td class="tnum">${f.fwhm ?? ''}</td><td class="tnum">${f.ecc ?? ''}</td><td class="tnum">${f.score ? f.score.toFixed(2) : ''}</td>
        <td><span class="status s-${sc}">${sl}${f.manual ? ' (manuell)' : ''}${f.deleted ? ' · Datei gelöscht' : ''}</span>${f.status === 'rejected' && f.reason ? `<div class="why">${esc(f.reason)}</div>` : ''}</td>
        <td>${f.deleted ? '' : f.status === 'rejected' ? `<button class="linkbtn" data-f="${f.id}" data-m="1">einschließen</button>` : f.status === 'selected' ? `<button class="linkbtn" data-f="${f.id}" data-m="-1">ausschließen</button>` : ''}</td></tr>`; }).join('')}
      </tbody></table>${list.length ? '' : '<div class="empty" style="border:0">Keine Aufnahmen in dieser Auswahl.</div>'}`;
    $('#ftable thead').onclick = (e) => { const th = e.target.closest('th[data-k]'); if (!th) return; const c = th.dataset.k; fSort = [c, fSort[0] === c ? -fSort[1] : (c === 'name' || c === 'night' ? 1 : -1)]; drawTable(); };
    $('#ftable tbody').onclick = async (e) => {
      const b = e.target.closest('button[data-f]'); if (!b) return;
      const f = frames.find(x => x.id === b.dataset.f), m = +b.dataset.m, prev = { manual: f.manual, status: f.status, reason: f.reason };
      let r;
      try { r = await send('/frames/' + f.id, { manual: m }, 'PATCH'); } catch (err) { toast(err.message, 'err'); return; }
      const apply = (v) => { Object.assign(f, v); drawTable(); $(`#ftable tr[data-id="${f.id}"]`)?.classList.add('flash'); };
      apply(m > 0 ? { manual: m, status: 'selected' } : { manual: m, status: 'rejected', reason: 'manuell ausgeschlossen' });
      // Rückgängig nur, wenn dadurch kein Neu-Stacken ausgelöst wurde
      const undo = r.restack_needed ? null : { label: 'Rückgängig', fn: async () => { await send('/frames/' + f.id, { manual: prev.manual || 0 }, 'PATCH'); apply(prev); } };
      toast(r.restack_needed ? 'Ausgeschlossen. Die Aufnahme steckt schon im Stack – beim nächsten Durchlauf wird neu gestackt.' : m > 0 ? 'Wird beim nächsten Durchlauf mitgestackt.' : 'Aufnahme ausgeschlossen.', '', undo);
    };
  }
  function chart(fr) {
    const d = fr.filter(f => f.n_stars != null);
    if (d.length < 2) return '<div class="mut sm">Noch nicht analysiert.</div>';
    const W = 1000, H = 190, P = 26;
    const mxN = Math.max(...d.map(f => f.n_stars), 1), fw = d.map(f => f.fwhm || 0), mxF = Math.max(...fw, 1);
    const x = (i) => P + i * (W - 2 * P) / (d.length - 1), yN = (v) => H - P - v / mxN * (H - 2 * P), yF = (v) => H - P - v / mxF * (H - 2 * P);
    const path = (fn) => d.map((f, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)},${fn(f).toFixed(1)}`).join('');
    let nightMarks = '';
    d.forEach((f, i) => { if (i && f.night !== d[i - 1].night) nightMarks += `<line x1="${x(i - .5)}" x2="${x(i - .5)}" y1="${P / 2}" y2="${H - P}" stroke="var(--line2)" stroke-dasharray="3 4"/><text x="${x(i - .5) + 4}" y="${P / 2 + 8}">${fnight(f.night)}</text>`; });
    return `<svg class="chart" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">
      <line x1="${P}" x2="${W - P}" y1="${H - P}" y2="${H - P}" stroke="var(--line)"/>${nightMarks}
      <path class="line" pathLength="1" d="${path(f => yN(f.n_stars))}" fill="none" stroke="var(--acc)" stroke-width="2" vector-effect="non-scaling-stroke"/>
      <path class="line" pathLength="1" d="${path(f => yF(f.fwhm || 0))}" fill="none" stroke="#fb923c" stroke-width="2" vector-effect="non-scaling-stroke"/>
      ${d.map((f, i) => f.status === 'rejected' ? `<rect x="${x(i) - 2}" y="${H - P + 4}" width="4" height="10" rx="1" fill="var(--bad)"><title>${esc(f.name)}: ${esc(f.reason)}</title></rect>` : '').join('')}
      ${d.map((f, i) => `<rect x="${x(i) - (W - 2 * P) / d.length / 2}" y="0" width="${(W - 2 * P) / d.length}" height="${H}" fill="transparent"><title>${esc(f.name)}\n${f.n_stars} Sterne · FWHM ${f.fwhm} px${f.band ? ' · ' + f.band : ''}</title></rect>`).join('')}
    </svg>`;
  }

  // ---- Tab: Versionen ----
  function tabVersions(t) {
    if (!p.results.length) { t.innerHTML = '<div class="empty"><h2>Noch keine Versionen</h2>Jeder Durchlauf – z. B. nach einer neuen Nacht – erzeugt eine neue Version.</div>'; return; }
    const cur = sel || p.results[0].id;
    t.innerHTML = `<div class="row mut sm" style="margin-bottom:14px">Klick zeigt eine Version. Mit ${icon('compare')} zwei Versionen nebeneinander vergleichen (Schieberegler).</div>
      <div class="versions">${p.results.map((r, i) => `<div class="ver ${r.id === cur ? 'on' : ''} ${r.id === cmp ? 'cmp' : ''}" data-id="${r.id}" role="button" tabindex="0">
        <img src="/api/results/${r.id}/thumb" alt="" loading="lazy"><div><b>${i === 0 ? 'Aktuell' : fdate(r.created)}</b>
        <span class="mut">${r.n_frames} Aufnahmen · ${dur(r.exposure)}</span>
        <span class="row" style="justify-content:space-between"><span class="dim">${esc(PROFILE_LABEL[r.stats?.profile] || r.stats?.profile || '')}</span>
        ${r.id !== cur ? `<button class="linkbtn" data-cmp="${r.id}" title="Mit angezeigter Version vergleichen">${icon('compare')}</button>` : ''}</span></div></div>`).join('')}</div>`;
    t.onclick = (e) => {
      const c = e.target.closest('[data-cmp]');
      if (c) { e.stopPropagation(); cmp = +c.dataset.cmp; sel = cur; location.hash = `#/p/${id}/bild`; return; }
      const v = e.target.closest('.ver');
      if (v) { sel = +v.dataset.id; cmp = null; location.hash = `#/p/${id}/bild`; }
    };
  }

  // ---- Tab: Optionen ----
  function tabOptions(t) {
    const o = p.options || {};
    const opt = (list, val, lbl) => list.map(v => `<option value="${esc(v)}" ${String(val) === String(v) ? 'selected' : ''}>${esc(lbl ? lbl(v) : v)}</option>`).join('');
    t.innerHTML = `<div class="split"><form class="panel" id="optf">
        <label class="field"><span>Bearbeitungsprofil</span><select name="profile">${opt(['auto', ...p.profiles], o.profile || 'auto', v => PROFILE_LABEL[v] || v)}</select>
          <small>Bestimmt Stretch, Entrauschen, Sternreduktion usw. „Automatisch“ wählt nach Objekttyp (${esc(p.kind_label || 'Allgemein')}).</small></label>
        <label class="field" ${p.result?.stats?.palette === 'OSC' ? 'hidden' : ''}><span>Farbpalette (Mono-Kamera mit Filtern)</span><select name="palette">${opt(p.palettes, o.palette || 'auto', v => PALETTE_LABEL[v] || v)}</select>
          <small>Nur relevant bei getrennten Filteraufnahmen (L, R, G, B, Hα, OIII, SII).</small></label>
        <label class="field"><span>Auswahl der Einzelbilder</span><select name="keep_percent" ${p.deleted?.can_restack === false ? 'disabled' : ''}>${opt(['', 90, 80, 60, 40], o.keep_percent ?? '', v => v === '' ? 'Alle brauchbaren (nur Ausreißer verwerfen)' : `Nur die besten ${v} %`)}</select></label>
        <label class="field"><span>Auflösung</span><select name="resolution_scale" ${p.deleted?.can_restack === false ? 'disabled' : ''}>${opt([1, 2], o.resolution_scale || 1, v => v === 2 ? '2× (Super-Resolution, viel RAM)' : '1× (Standard)')}</select></label>
        ${p.deleted?.can_restack === false ? '<p class="mut sm" style="margin:-6px 0 14px">Auswahl und Auflösung sind fest: die Einzelbilder wurden nach dem Stacking gelöscht.</p>' : ''}
        <label class="field"><span>Schmalband-Behandlung</span><select name="narrowband">${opt(['', 'true', 'false'], o.narrowband == null ? '' : String(o.narrowband), v => ({ '': 'Automatisch (nach Filter)', true: 'Ja – keine Sternfarben-Kalibrierung', false: 'Nein' }[v]))}</select></label>
        <label class="field"><span>Sterne getrennt bearbeiten</span><select name="star_separation">${opt(['', 'auto', 'intern'], o.star_separation || '', v => ({ '': 'Nein (Standard)', auto: 'Ja – StarNet, falls installiert, sonst eigene Trennung', intern: 'Ja – eigene Trennung' }[v]))}</select>
          <small>Nebel wird ohne Sterne gestreckt (mehr Nebel, Sterne kleiner). Kann bei flächigen Nebeln matter wirken – im Zweifel vergleichen.</small></label>
        <label class="field"><span>KI-Bildprüfung</span><select name="review">${opt(['true', 'false'], String(o.review ?? true), v => v === 'true' ? 'Verwenden, falls eingerichtet' : 'Für dieses Objekt aus')}</select></label>
        <div class="row" style="margin-top:6px"><button class="btn" type="submit" name="go" value="1" id="ogo"></button><button class="btn ghost" type="submit" id="osave">Nur speichern</button>
          <span class="grow"></span><span id="odirty"></span></div>
      </form>
      <div class="panel"><h2 style="margin-bottom:10px">Gut zu wissen</h2><p class="mut sm" style="margin:0 0 10px">Neue Aufnahmen werden automatisch <b>nachgestackt</b> – der bisherige Stack bleibt erhalten, nichts wird doppelt gerechnet.</p>
        <p class="mut sm" style="margin:0 0 10px">Profil-, Paletten- und Prüfänderungen werden aus dem gespeicherten Master-Stack <b>neu entwickelt</b> – ohne Stacking.</p>
        <p class="mut sm" style="margin:0">Auswahl und Auflösung beeinflussen den Stack: hierfür „Komplett neu stacken“ im Menü „Mehr“ verwenden.</p></div></div>`;
    const form = $('#optf');
    // direkt aus den Elementen lesen – gesperrte Felder fehlen sonst in FormData
    const v = (k) => form.elements[k].value;
    const read = () => ({ profile: v('profile'), palette: v('palette'), keep_percent: v('keep_percent') ? +v('keep_percent') : null,
      resolution_scale: +v('resolution_scale'), narrowband: v('narrowband') === '' ? null : v('narrowband') === 'true', review: v('review') === 'true', star_separation: v('star_separation') || null });
    const init = Object.fromEntries($$('select,input', form).filter(x => x.name).map(x => [x.name, x.value]));
    let o2, needRestack, dirty;
    // Erkennt Änderungen und beschriftet die Hauptaktion passend: neu entwickeln (schnell) oder neu stacken
    const check = () => {
      o2 = read();
      needRestack = (o2.keep_percent ?? null) !== (o.keep_percent ?? null) || o2.resolution_scale !== (o.resolution_scale || 1);
      const changed = $$('select,input', form).filter(x => x.name && String(init[x.name] ?? '') !== x.value);
      dirty = changed.length > 0;
      $$('.field', form).forEach(fl => fl.classList.toggle('changed', changed.some(x => fl.contains(x))));
      const busy = running(p.job), hasStack = !!p.result;
      $('#ogo').innerHTML = icon('play') + (!hasStack ? 'Speichern & verarbeiten' : needRestack ? 'Speichern & neu stacken' : dirty ? 'Speichern & neu entwickeln' : 'Neu entwickeln');
      $('#ogo').disabled = busy;
      $('#osave').disabled = !dirty;
      $('#odirty').innerHTML = dirty ? `<span class="dirty">${changed.length} Änderung${changed.length > 1 ? 'en' : ''}${needRestack ? ' · braucht Neu-Stacken' : ''}</span>` : '';
    };
    form.oninput = check;
    check();
    form.onsubmit = async (e) => {
      e.preventDefault();
      const go = e.submitter?.name === 'go';
      check();
      try {
        if (dirty) await send('/projects/' + id, { options: o2 }, 'PATCH');
        if (go) await (needRestack ? run(true, true) : p.result ? develop() : run(false));
        else toast('Optionen gespeichert.', 'ok');
      } catch (err) { toast(err.message, 'err'); return; }
      p = await api('/projects/' + id);
      if (tab === 'optionen') drawTab(false);
    };
  }

  // ---- Aktionen ----
  async function solve() {
    try {
      await send(`/projects/${id}/solve`);
      toast('Plate-Solving gestartet – die Aufnahmen werden danach dem passenden Objekt zugeordnet.');
      await refresh(true);
    } catch (e) {
      toast(e.message, 'err');
      if (/Plate-Solver/.test(e.message)) location.hash = '#/settings';
    }
  }
  async function run(restack, quiet) {
    if (restack && !quiet && !await confirmDlg('Komplett neu stacken?', 'Alle ausgewählten Aufnahmen werden erneut registriert und gestackt. Das kann je nach Menge dauern.', 'Neu stacken')) return;
    try {
      await send(`/projects/${id}/process`, { restack: !!restack });
    } catch (e) {
      toast(e.message, 'err');
      return;
    }
    toast(restack ? 'Neu-Stacken gestartet.' : 'Verarbeitung gestartet.');
    await refresh(true);
  }
  async function remove() {
    if (await deleteProject({ ...p, n_results: p.results.length })) location.hash = '#/';
  }
  async function develop() {
    try {
      await send(`/projects/${id}/process`, { mode: 'develop' });
    } catch (e) {
      toast(e.message, 'err');
      return;
    }
    toast('Wird aus dem Master-Stack neu entwickelt.');
    await refresh(true);
  }
  async function rename() {
    const d = $('#dlg');
    d.innerHTML = `<form method="dialog" id="rf"><h2 style="font-size:18px;margin-bottom:14px">Objekt umbenennen</h2>
      <input name="n" value="${esc(p.name)}" style="width:100%" maxlength="80" autofocus>
      <div class="row" style="justify-content:flex-end;margin-top:18px"><button class="btn ghost" value="">Abbrechen</button><button class="btn" value="ok">Speichern</button></div></form>`;
    d.showModal();
    d.onclose = async () => {
      const v = $('input', d)?.value.trim();
      if (d.returnValue === 'ok' && v && v !== p.name) { await send('/projects/' + id, { name: v }, 'PATCH'); toast('Umbenannt.', 'ok'); await refresh(true); crumbs([['Galerie', '#/'], [p.name]]); }
    };
  }
  async function refresh(force) {
    try {
      p = await api('/projects/' + id);
    } catch {
      // Projekt existiert nicht mehr: nach dem Plate-Solving wurden alle Aufnahmen anderen Objekten zugeordnet
      toast('Alle Aufnahmen wurden einem erkannten Objekt zugeordnet.', 'ok');
      location.hash = '#/';
      return;
    }
    const s = JSON.stringify([p.name, p.total, p.results.map(r => r.id), p.job?.state, p.frames, p.coords]);
    if (force || s !== sig) {
      const old = sig ? JSON.parse(sig) : null, cur = JSON.parse(s);
      const changedResults = old && old[2]?.[0] !== p.results[0]?.id;
      // nur die Frame-Zähler haben sich geändert (Analyse läuft) → Bild und Formular nicht neu aufbauen
      const framesOnly = old && !force && JSON.stringify([old[0], old[1], old[2], old[3], old[5]]) === JSON.stringify([cur[0], cur[1], cur[2], cur[3], cur[5]]);
      if (changedResults) {
        sel = null; cmp = null;
        const prevId = p.results[1]?.id;
        toast('Neue Version fertig.', 'ok', prevId ? { label: 'Mit vorheriger vergleichen', fn: () => { cmp = prevId; tab = 'bild'; location.hash = `#/p/${id}/bild`; drawTab(); } } : null);
      }
      if (old && JSON.stringify(old[4]) !== JSON.stringify(p.frames)) frames = null;
      sig = s;
      const y = window.scrollY;
      // im Optionen-Formular nicht neu zeichnen (Eingaben bleiben erhalten), nur Jobzeile
      if (framesOnly || (tab === 'optionen' && !force && !changedResults)) {
        setJobLine($('#job'), p.job);
        if (framesOnly && tab === 'aufnahmen') drawTab(false);
        return;
      }
      draw();
      window.scrollTo(0, y);
    } else {
      setJobLine($('#job'), p.job);
    }
  }
  sig = JSON.stringify([p.name, p.total, p.results.map(r => r.id), p.job?.state, p.frames, p.coords]);
  const prev = window.__afSel;
  if (prev && prev.id === id) { sel = prev.sel; cmp = prev.cmp; }
  draw();
  const closeMenu = (e) => { if (!e.target.closest('.menu')) $$('.menu.open').forEach(m => m.classList.remove('open')); };
  document.addEventListener('click', closeMenu);
  // Tastenkürzel: 1–4 Tabs, F Vollbild, B Vorher/Nachher, Esc schließt Menüs
  const onKey = (e) => {
    if (e.ctrlKey || e.metaKey || e.altKey || e.target.closest('input,select,textarea') || $('dialog[open]')) return;
    const k = e.key.toLowerCase();
    if (k === 'escape') $$('.menu.open').forEach(m => m.classList.remove('open'));
    else if (/^[1-4]$/.test(k)) location.hash = `#/p/${id}/${TABS[+k - 1][0]}`;
    else if (k === 'f' && tab === 'bild') $('.viewer [data-a=full]')?.click();
    else if (k === 'b' && tab === 'bild' && $('#ba')) { before = !before; drawTab(false); }
    else return;
    e.preventDefault();
  };
  document.addEventListener('keydown', onKey);
  return {
    update: () => refresh(false),
    // Tabwechsel innerhalb desselben Objekts ohne Neuladen
    navigate: (path) => {
      const m = path.match(/^\/p\/(\d+)(?:\/(\w+))?$/);
      if (!m || +m[1] !== id) return false;
      const t = m[2] || 'bild';
      if (t !== tab) { tab = t; drawTab(); }
      return true;
    },
    actions: () => [
      ...(running(p.job) ? [] : [['play', p.result ? 'Aktualisieren' : 'Verarbeiten', 'neue Aufnahmen nachstacken', () => run(false)]]),
      ...(p.result && !running(p.job) ? [['refresh', 'Neu entwickeln', 'ohne Stacking', develop]] : []),
      ...TABS.map(([k, l], i) => ['arrow', `${l} anzeigen`, `Taste ${i + 1}`, () => { location.hash = `#/p/${id}/${k}`; }]),
      ...(p.result ? [['expand', 'Vollbild', 'Taste F', () => { location.hash = `#/p/${id}/bild`; setTimeout(() => $('.viewer [data-a=full]')?.click(), 60); }],
        ['down', 'TIFF herunterladen', '16 bit', () => { location.href = `/api/results/${p.result.id}/tif`; }]] : []),
      ['edit', 'Umbenennen', p.name, rename],
    ],
    destroy: () => { window.__afSel = { id, sel, cmp }; viewer?.destroy(); document.removeEventListener('click', closeMenu); document.removeEventListener('keydown', onKey); },
  };
}

// ------------------------------------------------------- Bildbetrachter --- //
// ph = kleines Vorschaubild, das unscharf angezeigt wird, bis das große Bild geladen ist
function createViewer(host, src, cmpSrc, labels = ['älter', 'neuer'], ph = null) {
  host.innerHTML = `<div class="viewer">${ph ? `<img class="phimg" src="${ph}" alt="">` : ''}<span class="vload"><span class="spinner"></span>Lädt Bild in voller Auflösung …</span>
    ${cmpSrc ? `<img class="a" src="${cmpSrc}" alt=""><div class="cmp"><img class="b" src="${src}" alt=""></div><div class="cmpline"></div>
      <span class="lbl" style="left:12px">${esc(labels[0])}</span><span class="lbl" style="right:12px">${esc(labels[1])}</span>` : `<img class="b" src="${src}" alt="Astrofoto">`}
    <div class="vbar"><button data-a="out" title="Verkleinern">${icon('minus')}</button><span class="z tnum">–</span><button data-a="in" title="Vergrößern">${icon('plus')}</button>
      <button data-a="fit" title="Einpassen (Doppelklick: 100 %)">${icon('fit')}</button><button data-a="full" title="Vollbild (Esc beendet)">${icon('expand')}</button>
      <a href="${src}" download title="Herunterladen" style="display:grid;place-items:center;width:32px;height:32px;color:#e8ebf5">${icon('down')}</a></div></div>`;
  const v = $('.viewer', host), imgs = $$('img.a, img.b', v), main = $('img.b', v), clip = $('.cmp', v), line = $('.cmpline', v), zl = $('.z', v);
  let s = 1, x = 0, y = 0, fitS = 1, iw = 0, ih = 0, split = 0.5, ptrs = new Map(), last = null, pinch = null;
  const apply = () => {
    imgs.forEach(i => { i.style.transform = `translate(${x}px,${y}px) scale(${s})`; });
    zl.textContent = Math.round(s * 100) + ' %';
    if (clip) { const w = v.clientWidth * split; clip.style.clipPath = `inset(0 0 0 ${w}px)`; line.style.left = (w - 1) + 'px'; }
  };
  const clamp = () => {
    const W = v.clientWidth, H = v.clientHeight, w = iw * s, h = ih * s;
    x = w <= W ? (W - w) / 2 : Math.min(0, Math.max(W - w, x));
    y = h <= H ? (H - h) / 2 : Math.min(0, Math.max(H - h, y));
  };
  const fit = () => { if (!iw) return; fitS = Math.min(v.clientWidth / iw, v.clientHeight / ih); s = fitS; clamp(); apply(); };
  const zoomAt = (f, cx, cy) => {
    const ns = Math.min(8, Math.max(fitS * 0.5, s * f));
    x = cx - (cx - x) * ns / s; y = cy - (cy - y) * ns / s; s = ns; clamp(); apply();
  };
  // weiche Übergänge nur für Knöpfe/Doppelklick – Ziehen und Mausrad folgen direkt
  let animT = 0;
  const smooth = (fn) => { v.classList.add('anim'); fn(); clearTimeout(animT); animT = setTimeout(() => v.classList.remove('anim'), 400); };
  const onload = () => { iw = main.naturalWidth; ih = main.naturalHeight; fit(); requestAnimationFrame(() => v.classList.add('ready')); };
  main.complete && main.naturalWidth ? onload() : main.addEventListener('load', onload);
  main.addEventListener('error', () => { $('.vload', v).innerHTML = 'Bild konnte nicht geladen werden.'; });
  const ro = new ResizeObserver(() => { const atFit = Math.abs(s - fitS) < 1e-3; if (atFit) fit(); else { clamp(); apply(); } });
  ro.observe(v);
  v.addEventListener('wheel', (e) => { e.preventDefault(); const r = v.getBoundingClientRect(); zoomAt(Math.exp(-e.deltaY * 0.0018), e.clientX - r.left, e.clientY - r.top); }, { passive: false });
  v.addEventListener('dblclick', (e) => { const r = v.getBoundingClientRect(); smooth(() => { if (s > fitS * 1.05) fit(); else zoomAt(1 / s, e.clientX - r.left, e.clientY - r.top); }); });
  v.addEventListener('pointerdown', (e) => {
    if (e.target.closest('.vbar')) return;
    if (e.target === line) { const mv = (ev) => { const r = v.getBoundingClientRect(); split = Math.min(1, Math.max(0, (ev.clientX - r.left) / r.width)); apply(); };
      const up = () => { removeEventListener('pointermove', mv); removeEventListener('pointerup', up); }; addEventListener('pointermove', mv); addEventListener('pointerup', up); return; }
    v.setPointerCapture(e.pointerId); ptrs.set(e.pointerId, [e.clientX, e.clientY]); last = [e.clientX, e.clientY]; v.classList.add('drag');
    if (ptrs.size === 2) { const [a, b] = [...ptrs.values()]; pinch = Math.hypot(a[0] - b[0], a[1] - b[1]); }
  });
  v.addEventListener('pointermove', (e) => {
    if (!ptrs.has(e.pointerId)) return;
    ptrs.set(e.pointerId, [e.clientX, e.clientY]);
    if (ptrs.size === 2 && pinch) {
      const [a, b] = [...ptrs.values()], d = Math.hypot(a[0] - b[0], a[1] - b[1]), r = v.getBoundingClientRect();
      zoomAt(d / pinch, (a[0] + b[0]) / 2 - r.left, (a[1] + b[1]) / 2 - r.top); pinch = d; return;
    }
    x += e.clientX - last[0]; y += e.clientY - last[1]; last = [e.clientX, e.clientY]; clamp(); apply();
  });
  const end = (e) => { ptrs.delete(e.pointerId); if (ptrs.size < 2) pinch = null; if (!ptrs.size) v.classList.remove('drag'); else last = [...ptrs.values()][0]; };
  v.addEventListener('pointerup', end); v.addEventListener('pointercancel', end);
  const onKey = (e) => { if (e.key === 'Escape' && v.classList.contains('full')) { v.classList.remove('full'); requestAnimationFrame(fit); } };
  document.addEventListener('keydown', onKey);
  $('.vbar', v).addEventListener('click', (e) => {
    const a = e.target.closest('button')?.dataset.a; if (!a) return;
    const cx = v.clientWidth / 2, cy = v.clientHeight / 2;
    if (a === 'in') smooth(() => zoomAt(1.5, cx, cy)); else if (a === 'out') smooth(() => zoomAt(1 / 1.5, cx, cy)); else if (a === 'fit') smooth(fit);
    else if (a === 'full') { v.classList.toggle('full'); requestAnimationFrame(fit); }
  });
  return { destroy: () => { ro.disconnect(); document.removeEventListener('keydown', onKey); } };
}

// --------------------------------------------------------------- Aktivität --- //
async function activityView(el) {
  crumbs([['Aktivität']]);
  el.innerHTML = `<div class="pagehead"><div><h1>Aktivität</h1><p class="mut">Verarbeitungsaufträge – neue Aufnahmen werden automatisch eingeplant.</p></div></div><div id="jobs"></div>`;
  let sig = '';
  const label = { queued: ['', 'wartet'], running: ['acc', 'läuft'], done: ['ok', 'fertig'], error: ['bad', 'Fehler'] };
  const ji = (j) => j.state === 'done' ? `<span class="ji ok">${icon('check')}</span>` : j.state === 'error' ? `<span class="ji bad">${icon('alert')}</span>`
    : j.state === 'running' ? '<span class="ji run"><span class="spinner"></span></span>' : `<span class="ji">${icon('pulse')}</span>`;
  const row = (j) => `<a class="job ${running(j) ? 'running' : ''}" href="#/p/${j.project_id}" data-j="${j.id}">${ji(j)}
      <div style="min-width:0"><b style="color:var(--fg)">${esc(j.project || 'Objekt ' + j.project_id)}</b>
        <div class="mut sm jmsg">${j.state === 'error' ? `<span class="bad">${esc(j.message)}</span>` : running(j) ? esc((j.stage || 'wartet') + (j.message ? ' · ' + j.message : '')) : `gestartet ${ago(j.started || j.created)}${j.finished && j.started ? ` · Dauer ${dur(j.finished - j.started)}` : ''}`}</div></div>
      <span class="chip ${label[j.state]?.[0] || ''}">${running(j) && j.state === 'running' ? `${Math.round((j.progress || 0) * 100)} %` : label[j.state]?.[1] || esc(j.state)}</span>
      ${running(j) ? `<div class="progress ${j.state === 'queued' ? 'indet' : ''}"><i style="width:${(j.progress || 0) * 100}%"></i></div>` : ''}</a>`;
  async function load() {
    const jobs = await api('/jobs');
    const s = JSON.stringify(jobs.map(j => [j.id, j.state, j.stage]));
    const act = jobs.filter(running), past = jobs.filter(j => !running(j));
    if (s === sig) {
      // nur Fortschritt: Balken und Texte vor Ort aktualisieren
      act.forEach(j => {
        const r = $(`[data-j="${j.id}"]`);
        if (!r) return;
        $('.jmsg', r).textContent = (j.stage || 'wartet') + (j.message ? ' · ' + j.message : '');
        if (j.state === 'running') { $('.chip', r).textContent = Math.round((j.progress || 0) * 100) + ' %'; $('.progress i', r).style.width = (j.progress || 0) * 100 + '%'; }
      });
      return;
    }
    sig = s;
    $('#jobs').innerHTML = jobs.length ? `${act.length ? `<h2 class="sect">Läuft</h2><div class="jobs stagger">${act.map((j, i) => row(j).replace('<a ', `<a style="--i:${i}" `)).join('')}</div>` : ''}
      ${past.length ? `<h2 class="sect" ${act.length ? 'style="margin-top:28px"' : ''}>Verlauf</h2><div class="jobs stagger">${past.map((j, i) => row(j).replace('<a ', `<a style="--i:${i}" `)).join('')}</div>` : ''}`
      : `<div class="empty">${icon('pulse')}<h2>Noch nichts passiert</h2>Sobald Aufnahmen gefunden werden, erscheinen die Aufträge hier.</div>`;
  }
  await load();
  return { update: load };
}

// ------------------------------------------------------------------ Import --- //
async function importView(el) {
  crumbs([['Import']]);
  const st = await api('/status');
  el.innerHTML = `<div class="pagehead"><div><h1>Aufnahmen importieren</h1><p class="mut">Rohbilder (FITS) von Seestar, Vespera, Stellina oder deiner Astrokamera.</p></div></div>
    <label class="dropzone" id="dz">${icon('upload')}<h2>FITS-Dateien oder ganze Ordner hierher ziehen</h2>
      <div class="mut">oder klicken, um Dateien auszuwählen · Lights, Darks, Flats und Bias gemischt sind okay</div>
      <input type="file" id="fi" multiple accept=".fit,.fits,.fts" hidden></label>
    <div class="row" style="justify-content:center;margin-top:12px"><label class="btn ghost sm">Ordner auswählen…<input type="file" id="fd" webkitdirectory multiple hidden></label>
      <span class="mut sm">Ordnernamen bleiben erhalten – z. B. verrät „…_observation_M57“ bei Stellina das Objekt.</span></div>
    <div class="uploads" id="ups"></div>
    <div class="infogrid">
      <div><b>Oder: Ordner einbinden</b>Lege Dateien direkt in <code>${esc(st.input_dir)}</code> – AstroFlow schaut alle ${st.scan_interval} s nach.</div>
      <div><b>Automatische Sortierung</b>Objekt, Kamera und Nacht werden aus den Headern erkannt. Kalibrierframes über IMAGETYP oder Ordnernamen.</div>
      <div><b>Mehrere Nächte</b>Neue Sessions eines Objekts werden auf den bestehenden Stack nachgestackt.</div>
    </div>`;
  const dz = $('#dz');
  $('#fi').onchange = (e) => { upload([...e.target.files]); e.target.value = ''; };
  $('#fd').onchange = (e) => { upload([...e.target.files]); e.target.value = ''; };
  dz.ondragover = (e) => { e.preventDefault(); dz.classList.add('over'); };
  dz.ondragleave = () => dz.classList.remove('over');
  dz.ondrop = async (e) => { e.preventDefault(); e.stopPropagation(); dz.classList.remove('over'); upload(await droppedFiles(e.dataTransfer)); };
}

// Abgelegte Dateien inkl. Unterordnern einsammeln; relativer Pfad landet in f.relPath
async function droppedFiles(dt) {
  const entries = [...(dt?.items || [])].map(i => i.webkitGetAsEntry?.()).filter(Boolean);
  if (!entries.length) return [...(dt?.files || [])];
  const out = [];
  const walk = async (en) => {
    if (en.isFile) {
      const f = await new Promise((res, rej) => en.file(res, rej));
      f.relPath = en.fullPath.replace(/^\//, '');
      out.push(f);
    } else if (en.isDirectory) {
      const rd = en.createReader();
      for (;;) {
        const batch = await new Promise((res, rej) => rd.readEntries(res, rej));
        if (!batch.length) break;
        for (const c of batch) await walk(c);
      }
    }
  };
  for (const en of entries) await walk(en);
  return out;
}
function upload(files) {
  files = files.filter(f => /\.(fits?|fts)$/i.test(f.name));
  if (!files.length) { toast('Keine FITS-Dateien dabei (.fit, .fits, .fts).', 'err'); return; }
  const total = files.reduce((a, f) => a + f.size, 0);
  const box = document.createElement('div');
  box.className = 'upl';
  box.innerHTML = `<div class="row"><b>${files.length} Datei${files.length > 1 ? 'en' : ''}</b><span class="mut">${fmb(total)}</span><span class="grow"></span><span class="tnum pct">0 %</span></div><div class="progress"><i style="width:0"></i></div>`;
  ($('#ups') || document.body).prepend(box);
  if (!$('#ups')) { location.hash = '#/import'; }
  const fd = new FormData();
  files.forEach(f => fd.append('files', f, f.relPath || f.webkitRelativePath || f.name));
  const xhr = new XMLHttpRequest();
  xhr.open('POST', '/api/upload');
  xhr.upload.onprogress = (e) => { if (e.lengthComputable) { const pc = e.loaded / e.total * 100; $('i', box).style.width = pc + '%'; $('.pct', box).textContent = Math.round(pc) + ' %'; } };
  xhr.onload = async () => {
    if (xhr.status !== 200) { $('.pct', box).innerHTML = '<span class="bad">Fehler</span>'; toast('Upload fehlgeschlagen.', 'err'); return; }
    const r = JSON.parse(xhr.responseText);
    $('.pct', box).innerHTML = `<span class="row" style="gap:6px"><span class="spinner"></span>wird einsortiert …</span>`;
    try {
      const s = await send('/scan'), ids = Object.keys(s.new_by_project || {}).map(Number);
      const projects = ids.length ? await api('/projects') : [];
      box.classList.add('done');
      $('.pct', box).innerHTML = `<span class="ok row" style="gap:6px">${icon('check')}${r.saved} gespeichert</span>`;
      // direkt zu den erkannten Objekten springen
      if (ids.length) box.insertAdjacentHTML('beforeend', `<div class="found"><span class="mut sm" style="align-self:center">Erkannt:</span>${projects.filter(p => ids.includes(p.id))
        .map((p, i) => `<a class="chip acc" href="#/p/${p.id}" style="animation-delay:${i * 60}ms">${esc(p.name)} · ${s.new_by_project[p.id]} neu ${icon('arrow')}</a>`).join('')}</div>`);
      const one = ids.length === 1 && projects.find(p => p.id === ids[0]);
      toast(`${r.saved} Dateien hochgeladen – ${ids.length} Objekt${ids.length === 1 ? '' : 'e'} ${lastStatus?.auto ? 'werden verarbeitet' : 'erkannt'}.`, 'ok', one ? { label: 'Öffnen', href: `#/p/${one.id}` } : null);
      poll();
    } catch (e) { $('.pct', box).innerHTML = '<span class="bad">Einsortieren fehlgeschlagen</span>'; toast(e.message, 'err'); }
  };
  xhr.onerror = () => toast('Upload fehlgeschlagen.', 'err');
  xhr.send(fd);
}

// ------------------------------------------------------------ Einstellungen --- //
async function settingsView(el) {
  crumbs([['Einstellungen']]);
  const [c, st, sv, sn] = await Promise.all([api('/reviewer'), api('/status'), api('/solver'), api('/starnet')]);
  const astapOk = sv.astap && sv.astap_databases.length;
  el.innerHTML = `<div class="pagehead"><div><h1>Einstellungen</h1></div></div>
    <div class="split"><div><form class="panel" id="sf">
      <div class="panel-h"><h2>Plate-Solving</h2><span class="grow"></span>
        <span class="chip ${sv.active.length ? 'ok' : 'warn'}">${sv.active.length ? 'aktiv: ' + sv.active.map(b => SOLVER_LABEL[b] || b).join(', ') : 'nicht aktiv'}</span></div>
      <p class="mut sm" style="margin:-6px 0 14px">Erkennt Objekte aus dem Sternmuster, wenn die Aufnahmen keine Koordinaten oder keinen Objektnamen enthalten
        (z. B. Astrokamera ohne Montierungsanbindung). Je Ordner und Nacht wird nur eine Aufnahme gelöst.</p>
      <dl class="kv" style="margin-bottom:16px">
        <dt>ASTAP</dt><dd>${sv.astap ? `<span class="ok">gefunden</span>` : '<span class="warn">nicht gefunden</span>'}</dd>
        <dt>Sterndatenbank</dt><dd>${sv.astap_databases.length ? `<span class="ok">${esc(sv.astap_databases.join(', '))}</span>` : '<span class="warn">keine gefunden</span>'}</dd>
        <dt>astrometry.net lokal</dt><dd>${sv.solve_field ? '<span class="ok">gefunden</span>' : '<span class="dim">nicht installiert</span>'}</dd>
      </dl>
      ${astapOk ? '' : `<div class="notice" style="margin-bottom:16px"><div class="sm"><b>So richtest du ASTAP ein</b><div class="mut">
        ${sv.astap ? '' : 'ASTAP von <a href="https://www.hnsky.org/astap.htm" target="_blank" rel="noopener">hnsky.org/astap.htm</a> installieren. '}
        Dazu eine Sterndatenbank in den ASTAP-Ordner legen: <b>D05</b> (klein, Bildfeld ≥ 0,6°, ideal für Seestar/Vespera) oder <b>D50</b> (für kleinere Bildfelder).
        Im Docker-Image ist beides bereits enthalten.</div></div></div>`}
      <label class="field"><span>Solver</span><select name="backend">
        <option value="auto">Automatisch (lokale Solver)</option><option value="astap">ASTAP</option><option value="astrometry">astrometry.net (lokal)</option>
        <option value="nova">nova.astrometry.net (Online)</option><option value="off">Aus</option></select>
        <small>Beim Online-Dienst wird ein verkleinertes Graustufenbild hochgeladen (nicht öffentlich). „Automatisch“ nutzt ihn nie.</small></label>
      <label class="field"><span>ASTAP-Programm</span><input name="astap" value="${esc(sv.astap || '')}" placeholder="leer = automatisch suchen"></label>
      <label class="field"><span>Ordner der Sterndatenbank</span><input name="astap_db" value="${esc(sv.astap_db)}" placeholder="leer = ASTAP-Ordner"></label>
      <label class="field"><span>nova.astrometry.net API-Key</span><input name="nova_key" type="password" autocomplete="off" placeholder="${sv.has_nova_key ? '•••••• (gesetzt)' : 'nur für den Online-Dienst'}"></label>
      <label class="field"><span>Zeitlimit je Bild (s)</span><input name="timeout" type="number" min="20" max="900" value="${sv.timeout}" style="width:110px"></label>
      <div class="row"><button class="btn" type="submit">Speichern</button><span class="mut sm">Danach werden fehlgeschlagene Versuche beim nächsten Scan wiederholt.</span></div>
    </form>
    <form class="panel" id="rf">
      <h2>KI-Bildprüfer</h2><p class="mut sm" style="margin:6px 0 16px">Ein Vision-Modell begutachtet jedes fertige Bild („sieht das aus, wie es soll?“) und korrigiert die Bearbeitungsparameter, bis es passt.
        Bei <b>LM Studio</b> bleibt alles lokal; bei <b>OpenRouter</b> wird eine verkleinerte Vorschau an den Dienst gesendet.</p>
      <label class="field"><span>Anbieter</span><select name="provider"><option value="off">Aus</option><option value="lmstudio">LM Studio (lokal)</option><option value="openrouter">OpenRouter (Cloud)</option></select></label>
      <label class="field"><span>Server-URL</span><input name="base_url" value="${esc(c.base_url)}" placeholder="leer = Standard des Anbieters"></label>
      <label class="field"><span>Modell</span><input name="model" value="${esc(c.model)}" placeholder="z. B. qwen2.5-vl-7b-instruct"><small>Muss Bildeingabe unterstützen.</small></label>
      <label class="field"><span>API-Key</span><input name="api_key" type="password" autocomplete="off" placeholder="${c.has_key ? '•••••• (gesetzt – leer lassen zum Behalten)' : 'nur für OpenRouter'}"></label>
      <label class="field"><span>Maximale Prüfrunden</span><input name="max_rounds" type="number" min="1" max="6" value="${c.max_rounds}" style="width:100px"></label>
      <div class="row"><button class="btn" type="submit">Speichern</button><span class="mut sm">Wird gespeichert und gilt auch nach einem Neustart.</span></div>
    </form></div>
    <div><div class="panel" id="snp">
      <div class="panel-h"><h2>Sternentrennung</h2><span class="grow"></span>
        <span class="chip ${sn.found ? 'ok' : ''}">${sn.found ? 'StarNet aktiv' : 'eigene Trennung'}</span></div>
      <p class="mut sm" style="margin:-6px 0 12px">AstroFlow trennt Sterne und Nebel und bearbeitet beides getrennt. Ohne Zusatzsoftware nutzt es ein eigenes Verfahren;
        mit <b>StarNet2</b> (optional) werden auch helle Sterne sauberer entfernt.</p>
      ${sn.found ? `<dl class="kv" style="margin-bottom:12px"><dt>Programm</dt><dd style="word-break:break-all">${esc(sn.path)}</dd></dl>
        <div class="row">${sn.installed_here || sn.custom ? '<button class="btn ghost danger" id="snrm">Entfernen</button>' : ''}</div>`
      : `<ol class="mut sm steps"><li>Kommandozeilen-Version (CLI) für dein System von der offiziellen Seite laden:
          <a class="btn sm ghost" href="${esc(sn.page)}" target="_blank" rel="noopener">${icon('down')}starnetastro.com öffnen</a></li>
        <li>Die heruntergeladene ZIP hier auswählen – AstroFlow entpackt und testet sie:
          <label class="btn sm" style="margin-top:6px">${icon('upload')}ZIP installieren<input type="file" id="snzip" accept=".zip" hidden></label>
          <span id="snbusy" class="mut sm"></span></li></ol>
        <p class="dim sm" style="margin:10px 0 0">StarNet2 ist kostenlose Software von Mikita Misiura (alle Rechte beim Autor). AstroFlow liefert es nicht mit,
          sondern ruft es nur auf – mit dem Herunterladen akzeptierst du die Bedingungen des Autors.</p>
        <details style="margin-top:10px"><summary class="sm mut" style="cursor:pointer">Schon installiert? Pfad angeben</summary>
          <form id="snpath" class="row" style="margin-top:8px"><input name="path" placeholder="z. B. C:\\starnet\\starnet2.exe" style="flex:1"><button class="btn sm">Übernehmen</button></form></details>`}
    </div>
    <form class="panel" id="stf"><h2 style="margin-bottom:10px">Speicher &amp; Daten</h2>
      ${st.disk ? `<div class="row sm" style="justify-content:space-between"><span class="mut">Frei auf dem Datenlaufwerk</span><b>${fmb(st.disk.free)} von ${fmb(st.disk.total)}</b></div>
        <div class="progress" style="margin:6px 0 14px"><i style="width:${(1 - st.disk.free / st.disk.total) * 100}%"></i></div>` : ''}
      <label class="row" style="align-items:flex-start;gap:10px;cursor:pointer;flex-wrap:nowrap"><input type="checkbox" name="delete_uploads" ${st.delete_uploads ? 'checked' : ''} style="height:auto;margin-top:4px">
        <span><b>Hochgeladene Einzelbilder nach dem Stacking löschen</b><br><span class="mut sm">Der Stack bleibt erhalten: neue Nächte werden weiter ergänzt, die Bearbeitung funktioniert wie gewohnt.
        Danach ist aber kein komplettes Neu-Stacken mehr möglich (z. B. andere Bildauswahl). Dateien in eingebundenen Ordnern und Kalibrierbilder werden nie gelöscht.</span></span></label>
      <label class="row" style="align-items:flex-start;gap:10px;cursor:pointer;flex-wrap:nowrap;margin-top:14px"><input type="checkbox" name="gaia" ${st.gaia ? 'checked' : ''} style="height:auto;margin-top:4px">
        <span><b>Farbkalibrierung über Gaia-Sternfarben</b><br><span class="mut sm">Wie PCC in PixInsight/Siril: der Stack wird plate-gesolvt und der Weißabgleich an den
        Katalogfarben der Sterne ausgerichtet. Dazu werden nur die Feldkoordinaten an das ESA-Gaia-Archiv geschickt; das Ergebnis wird lokal gespeichert.
        Aus: Weißabgleich über das Mittel der Sternfarben.</span></span></label>
    </form>
    <div class="panel"><h2 style="margin-bottom:12px">System</h2><dl class="kv">
      <dt>Version</dt><dd>${esc(st.version)}</dd>
      <dt>Eingangsordner</dt><dd style="word-break:break-all">${esc(st.input_dir)}</dd>
      <dt>Ausgabeordner</dt><dd style="word-break:break-all">${esc(st.output_dir)}</dd>
      <dt>Automatik</dt><dd>${st.auto ? `an · alle ${st.scan_interval} s` : 'aus'}</dd>
      <dt>Rechen-Threads</dt><dd>${st.threads}</dd>
      <dt>Lights</dt><dd>${st.frames?.light || 0}</dd>
      <dt>Darks / Flats / Bias</dt><dd>${st.frames?.dark || 0} / ${st.frames?.flat || 0} / ${st.frames?.bias || 0}</dd>
    </dl><p class="mut sm" style="margin:14px 0 0">Tipp: Im Browser-Menü „App installieren“ wählen – AstroFlow läuft dann wie eine eigene App.</p></div></div></div>`;
  $('#stf [name=gaia]').onchange = async (e) => {
    try {
      const r = await send('/storage', { gaia: e.target.checked });
      toast(r.gaia ? 'Farbkalibrierung über Gaia an.' : 'Farbkalibrierung über Sternfarben-Mittel.', 'ok');
    } catch (err) { toast('Fehler: ' + err.message, 'err'); }
  };
  $('#snzip')?.addEventListener('change', async (e) => {
    const f = e.target.files[0];
    if (!f) return;
    $('#snbusy').textContent = ' wird installiert und getestet …';
    const fd = new FormData();
    fd.append('file', f);
    try {
      await api('/starnet/install', { method: 'POST', body: fd });
      toast('StarNet installiert und getestet.', 'ok');
      settingsView(el);
    } catch (err) { $('#snbusy').textContent = ''; toast(err.message, 'err'); }
  });
  $('#snpath')?.addEventListener('submit', async (e) => {
    e.preventDefault();
    try { await send('/starnet', { path: new FormData(e.target).get('path') }); toast('StarNet-Pfad übernommen.', 'ok'); settingsView(el); }
    catch (err) { toast(err.message, 'err'); }
  });
  $('#snrm')?.addEventListener('click', async () => {
    if (!await confirmDlg('StarNet entfernen?', 'AstroFlow nutzt dann wieder die eigene Sternentrennung.', 'Entfernen', true)) return;
    try { await send('/starnet', null, 'DELETE'); toast('StarNet entfernt.', 'ok'); settingsView(el); } catch (err) { toast(err.message, 'err'); }
  });
  $('#stf [name=delete_uploads]').onchange = async (e) => {
    try {
      const r = await send('/storage', { delete_uploads: e.target.checked });
      toast(r.delete_uploads ? 'Einzelbilder werden nach dem Stacking gelöscht.' : 'Einzelbilder bleiben erhalten.', 'ok');
    } catch (err) { toast('Fehler: ' + err.message, 'err'); }
  };
  $('[name=provider]').value = c.provider;
  $('[name=backend]').value = sv.backend;
  $('#sf').onsubmit = async (e) => {
    e.preventDefault();
    const f = Object.fromEntries(new FormData(e.target));
    f.timeout = +f.timeout;
    try {
      const r = await send('/solver', f);
      toast(r.active.length ? `Plate-Solving aktiv (${r.active.map(b => SOLVER_LABEL[b] || b).join(', ')}).` : 'Gespeichert – noch kein Solver einsatzbereit.', r.active.length ? 'ok' : 'err');
      settingsView(el);
    } catch (err) { toast('Fehler: ' + err.message, 'err'); }
  };
  $('#rf').onsubmit = async (e) => {
    e.preventDefault();
    const f = Object.fromEntries(new FormData(e.target));
    f.max_rounds = +f.max_rounds;
    try { await send('/reviewer', f); toast('KI-Prüfer gespeichert.', 'ok'); } catch (err) { toast('Fehler: ' + err.message, 'err'); }
  };
}

// ------------------------------------------------------- Befehlspalette --- //
// Strg+K: Objekte öffnen, Ansichten wechseln, Aktionen der aktuellen Ansicht ausführen
async function scan() {
  const b = $('#scanBtn');
  b.disabled = true;
  b.classList.add('busy');
  try {
    const r = await send('/scan');
    toast(r.new_frames ? `${r.new_frames} neue Dateien gefunden${r.solved ? ` – ${r.solved} per Plate-Solving zugeordnet` : ''}.` : 'Keine neuen Dateien.', r.new_frames ? 'ok' : '');
    (r.errors || []).slice(0, 3).forEach(m => toast(m, 'err'));
    poll();
  } catch (e) { toast('Scan fehlgeschlagen: ' + e.message, 'err'); }
  b.disabled = false;
  b.classList.remove('busy');
}
async function openPalette() {
  const d = $('#pal');
  if (d.open) return;
  d.innerHTML = `<div class="pin">${icon('search')}<input placeholder="Objekt oder Befehl suchen …" autocomplete="off" spellcheck="false"><kbd>Esc</kbd></div><ul></ul>
    <div class="pfoot"><span><kbd>↑</kbd><kbd>↓</kbd> auswählen</span><span><kbd>↵</kbd> öffnen</span><span><kbd>/</kbd> Galerie-Suche</span><span><kbd>1</kbd>–<kbd>4</kbd> Tabs im Objekt</span></div>`;
  d.showModal();
  const inp = $('input', d), ul = $('ul', d);
  const go = (h) => () => { location.hash = h; };
  const base = [
    ['grid', 'Galerie', 'alle Objekte', go('#/')], ['upload', 'Aufnahmen importieren', 'FITS hochladen', go('#/import')],
    ['pulse', 'Aktivität', 'laufende Aufträge', go('#/activity')], ['gear', 'Einstellungen', 'Plate-Solving, KI-Prüfer, Speicher', go('#/settings')],
    ['refresh', 'Eingangsordner scannen', 'nach neuen Aufnahmen suchen', scan],
  ];
  let projects = [], sel = 0, items = [];
  const draw = () => {
    const words = inp.value.toLowerCase().split(/\s+/).filter(Boolean);
    const hit = (t) => words.every(w => t.toLowerCase().includes(w));
    const ctx = (view?.actions?.() || []).filter(a => hit(a[1] + ' ' + a[2]));
    const pr = projects.filter(p => hit(p.name + ' ' + (p.kind_label || ''))).slice(0, 8);
    const nav = base.filter(a => hit(a[1] + ' ' + a[2]));
    items = [...ctx.map(a => a[3]), ...pr.map(p => go(`#/p/${p.id}`)), ...nav.map(a => a[3])];
    sel = Math.min(sel, Math.max(0, items.length - 1));
    let i = 0;
    const li = (ic, label, sub) => `<li class="it ${i === sel ? 'on' : ''}" data-i="${i++}">${ic}<span>${esc(label)}</span><small>${esc(sub || '')}</small></li>`;
    ul.innerHTML = (ctx.length ? '<li class="h">Dieses Objekt</li>' + ctx.map(a => li(`<span class="ic">${icon(a[0])}</span>`, a[1], a[2])).join('') : '')
      + (pr.length ? '<li class="h">Objekte</li>' + pr.map(p => li(p.result ? `<img src="/api/results/${p.result.id}/thumb" alt="">` : `<span class="ic">${icon('star')}</span>`,
        p.name, running(p.job) ? 'wird verarbeitet' : `${p.total} Aufnahmen${p.result ? ' · ' + dur(p.result.exposure) : ''}`)).join('') : '')
      + (nav.length ? '<li class="h">Navigation</li>' + nav.map(a => li(`<span class="ic">${icon(a[0])}</span>`, a[1], a[2])).join('') : '')
      || '<li class="pnone">Nichts gefunden</li>';
    $('li.on', ul)?.scrollIntoView({ block: 'nearest' });
  };
  const pick = (k) => { const fn = items[k]; if (!fn) return; d.close(); fn(); };
  inp.oninput = () => { sel = 0; draw(); };
  inp.onkeydown = (e) => {
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') { e.preventDefault(); sel = (sel + (e.key === 'ArrowDown' ? 1 : -1) + items.length) % Math.max(1, items.length); draw(); }
    else if (e.key === 'Enter') { e.preventDefault(); pick(sel); }
  };
  ul.onmousemove = (e) => { const l = e.target.closest('li.it'); if (l && +l.dataset.i !== sel) { sel = +l.dataset.i; $$('li.it', ul).forEach(x => x.classList.toggle('on', x === l)); } };
  ul.onclick = (e) => { const l = e.target.closest('li.it'); if (l) pick(+l.dataset.i); };
  d.onclick = (e) => { if (e.target === d) d.close(); };   // Klick auf den Hintergrund
  draw();
  inp.focus();
  try { projects = await api('/projects'); if (d.open) draw(); } catch { /* ohne Objektliste */ }
}

// ------------------------------------------------------------------ Start --- //
$('#scanBtn').onclick = scan;
$('#palBtn').onclick = openPalette;
addEventListener('keydown', (e) => {
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); openPalette(); }
});
addEventListener('resize', () => { slideInd($('#navind'), $('#nav a.on')); slideInd($('#tabind'), $('#tabs a.on'), true); });
// Dateien überall auf die Seite ziehen
let dragDepth = 0;
addEventListener('dragenter', (e) => { if (e.dataTransfer?.types.includes('Files') && !location.hash.startsWith('#/import')) { dragDepth++; $('#drop').hidden = false; } });
addEventListener('dragleave', () => { if (--dragDepth <= 0) { dragDepth = 0; $('#drop').hidden = true; } });
addEventListener('dragover', (e) => e.preventDefault());
addEventListener('drop', (e) => {
  e.preventDefault(); dragDepth = 0; $('#drop').hidden = true;
  if (e.target.closest?.('#dz')) return;
  droppedFiles(e.dataTransfer).then(fl => { if (fl.length) { location.hash = '#/import'; setTimeout(() => upload(fl), 150); } });
});
addEventListener('hashchange', route);
route().then(poll);
pollTimer = setInterval(() => { if (!document.hidden) poll(); }, 2500);
document.addEventListener('visibilitychange', () => { if (!document.hidden) poll(); });
