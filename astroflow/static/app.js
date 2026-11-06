'use strict';
/* AstroFlow-Shell: lädt die Module (/api/app), baut Navigation, Übersicht, Aktivität, Einstellungen und Befehlspalette.
   Module registrieren sich mit AF.register(id, {...}) – siehe „Modul-Vertrag“ unten. Alle Helfer sind global. */

// ---------------------------------------------------------------- Helfer --- //
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const icon = (n) => `<svg><use href="#i-${n}"/></svg>`;
// Kleine Bildfolge: vergleichen → Spur finden → Bahn darüberlegen (symbolisch, kein Fortschrittsmaß).
const sequenceLoader = () => '<span class="sequence-loader" aria-hidden="true"><em class="seq-back seq-back-two"></em><em class="seq-back seq-back-one"></em><em class="seq-image"><em class="seq-scan"></em><em class="seq-trail"></em><em class="seq-path"></em><em class="seq-object"></em></em></span>';
// Ladezustand am Knopf: der zuletzt gedrückte Knopf (≤ 0,6 s) bekommt während einer schreibenden Anfrage .pending (style.css)
let lastPress = { el: null, t: 0 };
addEventListener('pointerdown', (e) => { const b = e.target.closest?.('button, .btn'); if (b) lastPress = { el: b, t: performance.now() }; }, true);
addEventListener('keydown', (e) => { if ((e.key === 'Enter' || e.key === ' ') && e.target.closest?.('button, .btn')) lastPress = { el: e.target.closest('button, .btn'), t: performance.now() }; }, true);
// Laufende Aktion: erneutes Auslösen sperren – auch per Enter/Leertaste (die lösen ebenfalls click aus; pointer-events wirkt nur für die Maus)
addEventListener('click', (e) => { if (e.target.closest?.('.pending')) { e.preventDefault(); e.stopImmediatePropagation(); } }, true);
async function api(u, opt = {}) {
  const b = opt.method && opt.method !== 'GET' && performance.now() - lastPress.t < 600 && lastPress.el?.isConnected ? lastPress.el : null;
  if (b) { b.classList.add('pending'); b.setAttribute('aria-busy', 'true'); }
  try { return await apiFetch(u, opt); } finally { if (b) { b.classList.remove('pending'); b.removeAttribute('aria-busy'); } }
}
async function apiFetch(u, opt) {
  const r = await fetch('/api' + u, opt);
  if (!r.ok) {
    let msg = r.statusText;
    try { const j = await r.json(); msg = j.detail || j.error || msg; } catch { /* kein JSON */ }
    throw new Error(typeof msg === 'string' ? msg : JSON.stringify(msg) || ('HTTP ' + r.status));
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
const nf = new Intl.NumberFormat('de-DE');
const running = (j) => j && (j.state === 'queued' || j.state === 'running');
const hash = (s) => { let h = 0; for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) | 0; return h; };
const sexa = (v, hours) => {
  const sign = v < 0 ? '−' : hours ? '' : '+';
  let a = Math.abs(hours ? v / 15 : v), d = Math.floor(a), m = Math.floor((a - d) * 60), s = ((a - d) * 60 - m) * 60;
  if (s >= 59.95) { s = 0; m += 1; } if (m >= 60) { m = 0; d += 1; }
  return hours ? `${sign}${d}h ${String(m).padStart(2, '0')}m ${s.toFixed(1).padStart(4, '0')}s`
    : `${sign}${d}° ${String(m).padStart(2, '0')}′ ${String(Math.round(s)).padStart(2, '0')}″`;
};
const store = {
  get: (k, d) => { try { return JSON.parse(localStorage.getItem('af.' + k)) ?? d; } catch { return d; } },
  set: (k, v) => { try { localStorage.setItem('af.' + k, JSON.stringify(v)); } catch { /* privat/gesperrt */ } },
};
// Oberflächengröße je Browser: 0 = automatisch nach Bildschirmbreite (CSS), sonst Faktor auf 16px
const UI_SCALES = [[0, 'Automatisch'], [0.9, '90 %'], [1, '100 %'], [1.125, '113 %'], [1.25, '125 %'], [1.4, '140 %']];
const applyUiScale = (s = store.get('ui.scale', 0)) => { document.documentElement.style.fontSize = s > 0 ? `${16 * s}px` : ''; };
applyUiScale();
// Atmosphäre (look.css): 'schlicht' = neutrales Anthrazit von 0.21, 'dezent' (Standard) und 'nebula' mit Verlauf und Glas.
// index.html setzt das Attribut schon im <head> (kein Aufblitzen), hier nur für den Wechsel in den Einstellungen.
const UI_LOOKS = [['schlicht', 'Schlicht'], ['dezent', 'Dezent'], ['nebula', 'Nebula']];
const applyLook = (v = store.get('ui.look', 'dezent')) => { if (v === 'schlicht') delete document.documentElement.dataset.look; else document.documentElement.dataset.look = v; };
applyLook();
// Sternschnuppen: static/meteors.js (window.meteors) – Rate nach echtem Himmel

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
  if (!el || !el.offsetParent) { ind.style.opacity = '0'; return; }
  ind.style.opacity = '1';
  ind.style.transform = `translate(${el.offsetLeft}px,${line ? 0 : el.offsetTop}px)`;
  ind.style.width = el.offsetWidth + 'px';
  if (!line) ind.style.height = el.offsetHeight + 'px';
}
function jobLine(j) {
  if (!running(j)) return '';
  const q = j.state === 'queued';
  return `<div class="jobline"><div class="row"><span>${q ? '' : '<span class="live"></span>'}${q ? 'In Warteschlange' : esc(j.stage || 'Läuft')}${j.message ? ' · ' + esc(j.message) : ''}</span>
    <span class="tnum">${q || j.progress == null ? '' : Math.round((j.progress || 0) * 100) + ' %'}</span></div>
    <div class="progress ${q || j.progress == null ? 'indet' : ''}"><i style="width:${(j.progress || 0) * 100}%"></i></div></div>`;
}
// Jobzeile aktualisieren, ohne sie neu aufzubauen – so gleitet der Balken weich weiter
function setJobLine(host, j) {
  if (!host) return;
  const bar = $('.progress', host), q = j?.state === 'queued' || j?.progress == null;
  if (running(j) && bar && bar.classList.contains('indet') === q) {
    const tmp = document.createElement('div');
    tmp.innerHTML = jobLine(j);
    $('.row', host).innerHTML = $('.row', tmp).innerHTML;
    $('i', bar).style.width = (j.progress || 0) * 100 + '%';
  } else host.innerHTML = jobLine(j);
}
function confirmDlg(title, text, ok = 'OK', danger = false) {
  return new Promise(res => {
    const d = $('#dlg');
    d.innerHTML = `<h2 style="font-size:16px;margin-bottom:8px">${esc(title)}</h2><p class="mut">${text}</p>
      <div class="row" style="justify-content:flex-end;margin-top:18px"><button class="btn ghost" value="0">Abbrechen</button><button class="btn${danger ? ' danger' : ''}" value="1">${esc(ok)}</button></div>`;
    d.onclick = (e) => { const v = e.target.closest('button')?.value; if (v) { d.close(); res(v === '1'); } };
    d.onclose = () => res(false);
    d.showModal();
  });
}
// Ordner auf dem Server-Rechner wählen (Browser geben keine echten Pfade heraus). pick(path) bekommt den gewählten Ordner.
// opts: title, ok (Beschriftung), hint (Satz unten links). Suchfeld: filtert sofort den aktuellen Ordner und sucht entprellt
// in Unterordnern (/api/folders/search); Klick auf einen Treffer öffnet ihn, gewählt wird weiter nur der aktuelle Ordner.
// Keine Mehrfachauswahl: Hauptordner-Suche und SkyTrace arbeiten je mit genau einem Ordner.
const folderKey = (s) => String(s || '').toLowerCase().replace(/[\s_-]+/g, '');
function folderDialog(start, pick, { title = 'Ordner wählen', ok = 'Diesen Ordner verwenden', hint = 'Dateien werden nur gelesen.' } = {}) {
  const d = $('#dlg');
  d.innerHTML = `<h2 style="font-size:16px;margin-bottom:12px">${esc(title)}</h2>
    <div class="fdrives" id="fdr"></div>
    <div class="fnav"><button class="btn ghost sm" id="fup" title="Übergeordneter Ordner">↑</button><div class="fpath" id="fcr"></div></div>
    <div class="fsearch">${icon('search')}<input id="fq" type="text" autocomplete="off" spellcheck="false" aria-label="Ordner suchen"><button class="linkbtn sm" id="fqx" hidden title="Suche leeren (Esc)">Leeren</button></div>
    <div class="folders" id="fl"></div><div class="fcount mut sm" id="fct"></div>
    <details class="fmanual"><summary class="mut sm">Pfad eingeben</summary><div class="row" style="flex-wrap:nowrap;margin-top:6px">
      <input id="fp" style="flex:1" spellcheck="false" autocomplete="off"><button class="btn ghost sm" id="fgo">Öffnen</button></div></details>
    <div class="row" style="justify-content:flex-end;margin-top:14px"><span class="mut sm grow">${esc(hint)}</span><button class="btn ghost" id="fx">Abbrechen</button><button class="btn" id="fok" disabled>${esc(ok)}</button></div>`;
  let cur = '', list = null, deep = null, timer = 0, ctrl = null;
  const fq = $('#fq', d), fl = $('#fl', d), fct = $('#fct', d);
  const row = (x, rel) => `<button class="frow" data-p="${esc(x.path)}">${icon('folder')}<span class="fname">${esc(x.name)}${rel ? `<small class="frel">${esc(rel)}</small>` : ''}</span></button>`;
  const counts = () => list && (list.fits || list.pictures) ? `In diesem Ordner: ${[list.fits && `${list.fits} FITS`, list.pictures && `${list.pictures} Bilder`].filter(Boolean).join(', ')}` : '';
  // Liste zeichnen: ohne Suche alle Unterordner; mit Suche sofort die passenden hier, darunter Treffer tiefer (relativer Pfad)
  const draw = () => {
    if (!list) return;
    const key = folderKey(fq.value);
    $('#fqx', d).hidden = !fq.value;
    if (!key) {
      fl.innerHTML = list.folders.map(x => row(x)).join('') + (list.more ? `<p class="mut sm">… und ${list.more} weitere</p>` : '')
        || '<p class="mut sm" style="padding:8px">Keine Unterordner.</p>';
      fct.textContent = counts();
      return;
    }
    const done = deep?.key === key;
    const here = list.folders.filter(x => folderKey(x.name).includes(key));
    const seen = new Set(here.map(x => x.path));
    const sub = (done ? deep.hits : []).filter(x => !seen.has(x.path));
    fl.innerHTML = here.map(x => row(x)).join('')
      + (sub.length ? '<p class="fsec mut sm">In Unterordnern</p>' + sub.map(x => row(x, x.rel)).join('') : '')
      + (cur && !done ? '<p class="fsec mut sm"><span class="spinner"></span>Suche in Unterordnern …</p>' : '')
      + (!here.length && !sub.length && (done || !cur) ? '<p class="mut sm" style="padding:8px">Kein Ordner gefunden.</p>' : '');
    const n = here.length + sub.length;
    fct.textContent = done ? `${n} Treffer${deep.truncated ? ' – Suche begrenzt, es kann weitere geben' : ''} (bis ${deep.depth} Ebenen tief)`
      : cur ? `${here.length} Treffer in diesem Ordner` : '';
  };
  // Entprellt in Unterordnern suchen; eine ältere, noch laufende Anfrage wird abgebrochen
  const stop = () => { clearTimeout(timer); ctrl?.abort(); ctrl = null; };
  const searchDeep = () => {
    stop();
    const key = folderKey(fq.value);
    if (!key || !cur) { deep = null; draw(); return; }
    timer = setTimeout(async () => {
      const c = ctrl = new AbortController(), q = fq.value, path = cur;
      try {
        const r = await apiFetch(`/folders/search?path=${encodeURIComponent(path)}&q=${encodeURIComponent(q)}`, { signal: c.signal });
        if (c !== ctrl || path !== cur) return;
        deep = { key: folderKey(q), hits: r.hits, truncated: r.truncated, depth: r.depth };
        draw();
      } catch (e) {
        if (e.name === 'AbortError' || c !== ctrl) return;
        deep = { key, hits: [], depth: 0 }; draw(); toast(e.message, 'err');
      }
    }, 300);
  };
  const browse = async (path) => {
    try {
      const r = await api('/folders?path=' + encodeURIComponent(path || ''));
      stop();
      cur = r.path; list = r; deep = null; fq.value = '';
      $('#fp', d).value = r.path;
      $('#fok', d).disabled = !r.path;
      $('#fup', d).disabled = !r.path;
      $('#fup', d).dataset.p = r.parent || '';
      $('#fdr', d).innerHTML = r.drives.map(x => `<button class="chip${r.path.toLowerCase().startsWith(x.toLowerCase()) ? ' acc' : ''}" data-p="${esc(x)}">${esc(x)}</button>`).join('');
      // Pfad als klickbare Brotkrumen; Trenner je nach System
      const sep = r.path.includes('\\') ? '\\' : '/', parts = r.path.split(sep).filter(Boolean);
      $('#fcr', d).innerHTML = r.path ? parts.map((x, i) => {
        const p = (r.path.startsWith(sep) ? sep : '') + parts.slice(0, i + 1).join(sep) + (i === 0 && sep === '\\' ? sep : '');
        return `<button class="linkbtn" data-p="${esc(p)}">${esc(x)}</button>`;
      }).join(`<span class="dim">${esc(sep)}</span>`) || esc(sep) : '<span class="mut">Laufwerke</span>';
      fq.placeholder = r.path ? 'Ordner suchen, auch in Unterordnern – z. B. NGC 4395' : 'Laufwerk suchen …';
      draw();
      fl.scrollTop = 0;
    } catch (e) { toast(e.message, 'err'); }
  };
  d.onclick = (e) => { const r = e.target.closest('[data-p]'); if (r && r.id !== 'fup') browse(r.dataset.p); };
  $('#fup', d).onclick = (e) => { e.stopPropagation(); browse(e.currentTarget.dataset.p); };
  $('#fgo', d).onclick = () => browse($('#fp', d).value);
  $('#fp', d).onkeydown = (e) => { if (e.key === 'Enter') { e.preventDefault(); browse($('#fp', d).value); } };
  fq.oninput = () => { draw(); searchDeep(); };
  // Esc leert die Suche (erst bei leerem Feld schließt Esc den Dialog), Enter öffnet den ersten Treffer, ↓ springt in die Liste
  fq.onkeydown = (e) => {
    if (e.key === 'Escape' && fq.value) { e.preventDefault(); e.stopPropagation(); fq.value = ''; fq.oninput(); }
    else if (e.key === 'Enter') { e.preventDefault(); const f = $('.frow', fl); if (f && fq.value) browse(f.dataset.p); }
    else if (e.key === 'ArrowDown') { e.preventDefault(); $('.frow', fl)?.focus(); }
  };
  fl.onkeydown = (e) => {
    const rows = $$('.frow', fl), i = rows.indexOf(document.activeElement);
    if (i < 0) return;
    if (e.key === 'ArrowDown') { e.preventDefault(); rows[Math.min(i + 1, rows.length - 1)].focus(); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); (i ? rows[i - 1] : fq).focus(); }
  };
  $('#fqx', d).onclick = (e) => { e.stopPropagation(); fq.value = ''; fq.oninput(); fq.focus(); };
  $('#fx', d).onclick = () => d.close();
  $('#fok', d).onclick = () => { if (cur) { d.close(); pick(cur); } };
  d.onclose = stop;
  d.showModal();
  fq.focus();
  browse(start || '');
}
function crumbs(parts) {
  $('#crumbs').innerHTML = parts.map((p, i) => i < parts.length - 1 ? `<a href="${p[1]}">${esc(p[0])}</a><span class="dim">/</span>` : `<b>${esc(p[0])}</b>`).join('');
}
// Menüs (.menu > button + .pop) öffnen/schließen – ein Klick außerhalb schließt alle
document.addEventListener('click', (e) => {
  const b = e.target.closest('.menu > button');
  if (b) { e.stopPropagation(); const m = b.parentElement; $$('.menu.open').forEach(o => o !== m && o.classList.remove('open')); m.classList.toggle('open'); return; }
  if (!e.target.closest('.menu')) $$('.menu.open').forEach(m => m.classList.remove('open'));
});
// Auswahlfeld mit Suche für lange Listen (Modelle, Stimmen). Der Wert steht in einem versteckten <input name>, damit
// Formulare ihn wie ein normales Feld lesen. load() liefert [{id, name, info}] und wird beim ersten Öffnen bzw. nach reload() geholt.
// custom: eigene Eingabe erlauben (z. B. Modell, das der Server nicht auflistet). Gibt { set(v), reload(), value } zurück.
function picker(host, { name, value = '', placeholder = 'Bitte wählen …', load, custom = true, empty = '' }) {
  host.classList.add('pick');
  host.innerHTML = `<input type="hidden" name="${esc(name)}" value="${esc(value)}"><button type="button" class="pick-btn"><span class="pick-v"></span>${icon('chev')}</button>
    <div class="pick-pop" hidden><div class="pick-q">${icon('search')}<input placeholder="Suchen …" autocomplete="off" spellcheck="false"></div><ul></ul></div>`;
  const hid = $('input[type=hidden]', host), btn = $('.pick-btn', host), pop = $('.pick-pop', host), q = $('.pick-q input', host), ul = $('ul', host);
  let items = null, err = '', loading = null, sel = 0, shown = [];
  const label = () => {
    const it = items?.find(i => i.id === hid.value);
    $('.pick-v', host).innerHTML = hid.value ? `${esc(it?.name || hid.value)}${it?.info ? `<small>${esc(it.info)}</small>` : ''}` : `<span class="dim">${esc(empty || placeholder)}</span>`;
  };
  const set = (v, fire = true) => {
    hid.value = v; label();
    if (fire) { hid.dispatchEvent(new Event('input', { bubbles: true })); hid.dispatchEvent(new Event('change', { bubbles: true })); }
  };
  const fetchItems = async () => {
    if (items || loading) return loading;
    loading = (async () => { try { items = await load(); err = ''; } catch (e) { items = []; err = e.message; } loading = null; label(); draw(); })();
    return loading;
  };
  function draw() {
    const words = q.value.toLowerCase().split(/\s+/).filter(Boolean);
    shown = (items || []).filter(i => words.every(w => (i.id + ' ' + i.name + ' ' + (i.info || '')).toLowerCase().includes(w))).slice(0, 300);
    const own = custom && q.value.trim() && !shown.some(i => i.id === q.value.trim());
    if (own) shown = [{ id: q.value.trim(), name: q.value.trim(), info: 'eigene Eingabe übernehmen', own: true }, ...shown];
    if (empty && !words.length) shown = [{ id: '', name: empty, info: '' }, ...shown];
    sel = Math.min(sel, Math.max(0, shown.length - 1));
    ul.innerHTML = (items === null ? '<li class="pick-msg"><span class="spinner"></span>Lädt …</li>' : '')
      + (err ? `<li class="pick-msg bad">${esc(err)}</li>` : '')
      + shown.map((i, k) => `<li data-k="${k}" class="${k === sel ? 'on' : ''} ${i.id === hid.value ? 'cur' : ''} ${i.own ? 'own' : ''}"><b>${esc(i.name)}</b>${i.name !== i.id && i.id ? `<code>${esc(i.id)}</code>` : ''}${i.info ? `<small>${esc(i.info)}</small>` : ''}</li>`).join('')
      + (items && !shown.length && !err ? '<li class="pick-msg">Nichts gefunden</li>' : '');
    $('li.on', ul)?.scrollIntoView({ block: 'nearest' });
  }
  const close = () => { pop.hidden = true; host.classList.remove('open'); document.removeEventListener('pointerdown', outside, true); };
  const outside = (e) => { if (!host.contains(e.target)) close(); };
  const open = () => {
    pop.hidden = false; host.classList.add('open'); q.value = ''; sel = Math.max(0, (items || []).findIndex(i => i.id === hid.value));
    draw(); fetchItems(); q.focus();
    document.addEventListener('pointerdown', outside, true);
  };
  btn.onclick = () => pop.hidden ? open() : close();
  q.oninput = () => { sel = 0; draw(); };
  q.onkeydown = (e) => {
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') { e.preventDefault(); sel = (sel + (e.key === 'ArrowDown' ? 1 : -1) + shown.length) % Math.max(1, shown.length); draw(); }
    else if (e.key === 'Enter') { e.preventDefault(); if (shown[sel]) { set(shown[sel].id); close(); btn.focus(); } }
    else if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); close(); btn.focus(); }
  };
  ul.onclick = (e) => { const li = e.target.closest('li[data-k]'); if (li) { set(shown[+li.dataset.k].id); close(); } };
  label();
  return { get value() { return hid.value; }, set, reload() { items = null; err = ''; label(); if (!pop.hidden) { draw(); fetchItems(); } }, preload: fetchItems };
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

// ------------------------------------------------------- Bildbetrachter --- //
// ph = kleines Vorschaubild, das unscharf angezeigt wird, bis das große Bild geladen ist;
// opts.overlay(svg, w, h) zeichnet Markierungen in Bildkoordinaten (wandern mit Zoom/Verschieben);
// opts.onTap(target, event) meldet ein kurzes Antippen ohne Ziehen (z. B. Klick auf eine Objektmarke)
function createViewer(host, src, cmpSrc, labels = ['älter', 'neuer'], ph = null, opts = {}) {
  host.innerHTML = `<div class="viewer">${ph ? `<img class="phimg" src="${ph}" alt="">` : ''}<span class="vload"><span class="spinner"></span>Lädt Bild in voller Auflösung …</span>
    ${cmpSrc ? `<img class="a" src="${cmpSrc}" alt=""><div class="cmp"><img class="b" src="${src}" alt=""></div><div class="cmpline"></div>
      <span class="lbl" style="left:12px">${esc(labels[0])}</span><span class="lbl" style="right:12px">${esc(labels[1])}</span>` : `<img class="b" src="${src}" alt="Astrofoto">`}
    ${opts.overlay ? '<svg class="vov" aria-hidden="true"></svg>' : ''}
    <div class="vbar"><button data-a="out" title="Verkleinern">${icon('minus')}</button><span class="z tnum">–</span><button data-a="in" title="Vergrößern">${icon('plus')}</button>
      <button data-a="fit" title="Einpassen (Doppelklick: 100 %)">${icon('fit')}</button><button data-a="full" title="Vollbild (Esc beendet)">${icon('expand')}</button>
      <a href="${src}" download title="Herunterladen" style="display:grid;place-items:center;width:32px;height:32px;color:#e8ebf5">${icon('down')}</a></div></div>`;
  const v = $('.viewer', host), main = $('img.b', v), clip = $('.cmp', v), line = $('.cmpline', v), zl = $('.z', v), ov = $('.vov', v);
  const imgs = [...$$('img.a, img.b', v), ...(ov ? [ov] : [])];
  let s = 1, x = 0, y = 0, fitS = 1, iw = 0, ih = 0, split = 0.5, ptrs = new Map(), last = null, pinch = null, tap = null;
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
    const ns = Math.min(8, Math.max(fitS, s * f));                 // nie kleiner als eingepasst (sonst nur schwarzer Rand)
    x = cx - (cx - x) * ns / s; y = cy - (cy - y) * ns / s; s = ns; clamp(); apply();
  };
  // weiche Übergänge nur für Knöpfe/Doppelklick – Ziehen und Mausrad folgen direkt
  let animT = 0;
  const smooth = (fn) => { v.classList.add('anim'); fn(); clearTimeout(animT); animT = setTimeout(() => v.classList.remove('anim'), 400); };
  const onload = () => {
    // Auflösung kann zwischen Live-Stufe und Endbild wechseln: relativen Zoom und Bildmittelpunkt erhalten.
    const relative = iw ? s / fitS : 1;
    const cx = iw ? (v.clientWidth / 2 - x) / (iw * s) : .5;
    const cy = ih ? (v.clientHeight / 2 - y) / (ih * s) : .5;
    iw = main.naturalWidth; ih = main.naturalHeight;
    if (ov) { ov.setAttribute('viewBox', `0 0 ${iw} ${ih}`); ov.style.width = iw + 'px'; ov.style.height = ih + 'px'; opts.overlay(ov, iw, ih); }
    fitS = Math.min(v.clientWidth / iw, v.clientHeight / ih);
    s = fitS * relative;
    x = v.clientWidth / 2 - cx * iw * s; y = v.clientHeight / 2 - cy * ih * s;
    clamp(); apply(); requestAnimationFrame(() => v.classList.add('ready'));
  };
  // Dauert es lange (Handy im Heimnetz, Werbeblocker) oder schlägt es fehl: sagen und „Erneut versuchen“ anbieten
  const vload = $('.vload', v), slowAfter = opts.slowAfter ?? 20000;
  let slowT = 0;
  const hint = (msg) => { vload.innerHTML = `<span>${msg}</span><button class="btn sm ghost" data-retry>Erneut versuchen</button>`; };
  const watch = () => { clearTimeout(slowT); slowT = setTimeout(() => { if (!v.classList.contains('ready')) hint('Lädt noch … (langsames Netz?)'); }, slowAfter); };
  main.addEventListener('load', () => clearTimeout(slowT));
  main.addEventListener('load', onload);
  if (main.complete && main.naturalWidth) onload(); else watch();
  main.addEventListener('error', () => { clearTimeout(slowT); hint('Bild konnte nicht geladen werden.'); });
  vload.addEventListener('click', (e) => {
    if (!e.target.closest('[data-retry]')) return;
    vload.innerHTML = '<span class="spinner"></span>Lädt Bild in voller Auflösung …';
    const u = new URL(main.getAttribute('src'), location.href); u.searchParams.set('neu', Date.now());
    main.src = u.pathname + u.search; watch();
  });
  const ro = new ResizeObserver(() => { const atFit = Math.abs(s - fitS) < 1e-3; if (atFit) fit(); else { clamp(); apply(); } });
  ro.observe(v);
  v.addEventListener('wheel', (e) => { e.preventDefault(); const r = v.getBoundingClientRect(); zoomAt(Math.exp(-e.deltaY * 0.0018), e.clientX - r.left, e.clientY - r.top); }, { passive: false });
  v.addEventListener('dblclick', (e) => { const r = v.getBoundingClientRect(); smooth(() => { if (s > fitS * 1.05) fit(); else zoomAt(1 / s, e.clientX - r.left, e.clientY - r.top); }); });
  v.addEventListener('pointerdown', (e) => {
    if (e.target.closest('.vbar, [data-retry]')) return;
    if (e.target === line) { const mv = (ev) => { const r = v.getBoundingClientRect(); split = Math.min(1, Math.max(0, (ev.clientX - r.left) / r.width)); apply(); };
      const up = () => { removeEventListener('pointermove', mv); removeEventListener('pointerup', up); }; addEventListener('pointermove', mv); addEventListener('pointerup', up); return; }
    tap = ptrs.size ? null : { t: e.target, x: e.clientX, y: e.clientY };
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
  const end = (e) => {
    if (tap && opts.onTap && e.type === 'pointerup' && Math.hypot(e.clientX - tap.x, e.clientY - tap.y) < 5) opts.onTap(tap.t, e);
    tap = null;
    ptrs.delete(e.pointerId); if (ptrs.size < 2) pinch = null; if (!ptrs.size) v.classList.remove('drag'); else last = [...ptrs.values()][0]; };
  v.addEventListener('pointerup', end); v.addEventListener('pointercancel', end);
  const onKey = (e) => { if (e.key === 'Escape' && v.classList.contains('full')) { v.classList.remove('full'); requestAnimationFrame(fit); } };
  document.addEventListener('keydown', onKey);
  $('.vbar', v).addEventListener('click', (e) => {
    const a = e.target.closest('button')?.dataset.a; if (!a) return;
    const cx = v.clientWidth / 2, cy = v.clientHeight / 2;
    if (a === 'in') smooth(() => zoomAt(1.5, cx, cy)); else if (a === 'out') smooth(() => zoomAt(1 / 1.5, cx, cy)); else if (a === 'fit') smooth(fit);
    else if (a === 'full') { v.classList.toggle('full'); requestAnimationFrame(fit); }
  });
  // setSrc: Bild wechseln; auch bei anderer Pixelgröße bleiben relativer Zoom und Ausschnitt erhalten.
  // setSplit: Vergleichslinie setzen (0 = nur „neu“, 1 = nur „alt“; Blinken); zoomTo: auf Bildpixel (px, py) zoomen; redraw: Overlay neu zeichnen
  return { el: v, overlay: ov, setSrc: (u) => { if (main.getAttribute('src') !== u) main.src = u; },
    setSplit: (f) => { split = Math.min(1, Math.max(0, f)); apply(); },
    zoomTo: (px, py, f = 4) => { if (!iw) return; smooth(() => { s = Math.min(8, fitS * f); x = v.clientWidth / 2 - px * s; y = v.clientHeight / 2 - py * s; clamp(); apply(); }); },
    redraw: () => { if (ov && iw) opts.overlay(ov, iw, ih); }, destroy:() => { clearTimeout(slowT); ro.disconnect(); main.removeEventListener('load', onload); document.removeEventListener('keydown', onKey); } };
}

// ----------------------------------------------------------- Modul-Vertrag --- //
/* AF.register(id, {
     nav: [{ href, label, icon, key, area?, parent? }],  Sidebar: area 'work' = Arbeitsbereich (Hauptpunkt des Moduls),
                                                 'library' = Bibliothek; ohne area = Unterpunkt unter dem Hauptpunkt des Moduls
                                                 (bzw. unter parent = Schlüssel eines anderen Eintrags, 'home' = Übersicht)
     routes: [[regex, navKey, async (el, ...m) => view]],   view = { update?(st), navigate?(path), actions?(), destroy?() }
     summary: async (st) => { title, sub, icon, href, stats: [[wert, label]], live?: html },   Modulkarte der Übersicht
     recent: async (st) => [{ img, title, sub, href }],     „Zuletzt bearbeitet“ (fertige Bilder)
     discoveries: async (st) => [{ img?, icon?, kicker, title, sub, href, t }],   „Neueste Entdeckungen“ (t = Unix-Zeit)
     homeFeatured: async (st) => html | '',      breite Karte neben der Übersicht (Heute Nacht)
     settings: async (el) => void,               Abschnitt in den Einstellungen
     palette: async () => [[icon, label, sub, fn|href, img?]],  Einträge der Befehlspalette (z. B. Objekte)
     status: (st, nav) => void,                  Polling-Hook (Zähler in der Navigation)
     sitesPanel: (host) => void,                 eigener Abschnitt unter Einstellungen → Standorte (z. B. SkyTrace neu abgleichen)
     drop: (files) => bool                       global abgelegte Dateien übernehmen
   })
   Kontextpanel rechts: AF.ctx.open(el, { title, html | render(body) }) – el = Behälter der Ansicht (verworfene Ansichten
   schreiben so nicht ins Panel); wird beim Ansichtswechsel automatisch geleert. AF.ctx.show() öffnet es (Drawer auf schmalen Fenstern). */
const AF = { app: null, modules: {}, order: [], status: null,
  register(id, def) { this.modules[id] = { id, ...def }; },
  on(id) { return !!this.modules[id]; },
  info(id) { return this.app?.modules.find(m => m.id === id); },
};

// ------------------------------------------------------------- Kontextpanel --- //
const wideCtx = () => matchMedia('(min-width: 1280px)').matches;
AF.ctx = {
  has: false,
  open(owner, { title = 'Details', html = '', render = null } = {}) {
    if (owner && !owner.isConnected) return null;
    const body = $('#ctxBody');
    $('#ctxTitle').textContent = title;
    if (render) { body.replaceChildren(); render(body); } else body.innerHTML = html;
    this.has = true; ctxSync();
    return body;
  },
  body() { return $('#ctxBody'); },
  shown: false,                                   // Details sind standardmäßig eingeklappt – je Ansicht neu, nicht gespeichert
  show() { this.shown = true; if (!wideCtx()) $('#shell').classList.add('ctx-shown'); ctxSync(); },
  hide() { this.shown = false; $('#shell').classList.remove('ctx-shown'); ctxSync(); },
  toggle() { (wideCtx() ? $('#ctx').hidden : !$('#shell').classList.contains('ctx-shown')) ? this.show() : this.hide(); },
  clear() { this.has = false; this.shown = false; $('#ctxBody').replaceChildren(); $('#shell').classList.remove('ctx-shown'); ctxSync(); },
};
function ctxSync() {
  const has = AF.ctx.has, wide = wideCtx(), shell = $('#shell');
  $('#ctxBtn').hidden = !has;
  $('#ctx').hidden = !has || (wide && !AF.ctx.shown);
  if (wide) shell.classList.remove('ctx-shown');
  const shown = has && (wide ? !$('#ctx').hidden : shell.classList.contains('ctx-shown'));
  $('#ctxBtn').classList.toggle('on', shown);
  $('#ctxBtn').setAttribute('aria-expanded', shown);
  $$('[data-ctx-toggle]:not([data-side])').forEach(b => { b.classList.toggle('on', shown); b.setAttribute('aria-pressed', shown); });
}

async function loadModules() {
  AF.app = await api('/app');
  const v = AF.app.build || AF.app.version;
  for (const m of AF.app.modules.filter(m => m.available)) {
    for (const href of m.styles) document.head.insertAdjacentHTML('beforeend', `<link rel="stylesheet" href="${href}?v=${v}">`);
    for (const src of m.scripts) {
      await new Promise((res) => { const s = document.createElement('script'); s.src = `${src}?v=${v}`; s.onload = res; s.onerror = res; document.body.append(s); });
    }
    if (AF.modules[m.id]) AF.order.push(m.id);
  }
}

// Sidebar: Arbeitsbereiche (je Modul ein Hauptpunkt, Unterpunkte klappen beim aktiven Bereich auf), Bibliothek, System.
// Wird einmal gebaut; Routenwechsel setzen nur Klassen (navState).
const NAV = { items: [], parent: {} };
function buildNav() {
  const all = [{ href: '#/', label: 'Übersicht', icon: 'home', key: 'home', area: 'work' }];
  for (const id of AF.order) {
    const nav = AF.modules[id].nav || [], main = nav.find(n => n.area === 'work') || nav[0];
    for (const n of nav) all.push({ ...n, module: id, area: n === main ? 'work' : n.area, parent: n.area === 'library' || n === main ? null : n.parent || main?.key });
  }
  all.push({ href: '#/activity', label: 'Aufträge', icon: 'pulse', key: 'activity', area: 'system' },
    { href: '#/settings', label: 'Einstellungen', icon: 'gear', key: 'settings', area: 'system', cls: 'lib-only' });
  NAV.items = all;
  NAV.parent = Object.fromEntries(all.filter(n => n.parent).map(n => [n.key, n.parent]));
  const link = (n, sub = false) => `<a href="${n.href}" data-r="${n.key}" class="${sub ? 'sub' : ''} ${n.cls || ''}" title="${esc(n.label)}">${sub ? '' : icon(n.icon)}<span>${esc(n.label)}</span>${n.key === 'activity' ? '<b id="busy" hidden></b>' : `<b class="nb" data-nb="${n.key}" hidden></b>`}</a>`;
  const withSubs = (n) => {
    const subs = all.filter(s => s.parent === n.key);
    return link(n) + (subs.length ? `<div class="subs" data-for="${n.key}"><div>${subs.map(s => link(s, true)).join('')}</div></div>` : '');
  };
  const lib = all.filter(n => n.area === 'library');
  $('#nav').innerHTML = `<div class="nav-group">Arbeitsbereiche</div>${all.filter(n => n.area === 'work').map(withSubs).join('')}
    ${lib.length ? `<div class="nav-group">Bibliothek</div>${lib.map(withSubs).join('')}` : ''}
    <div class="nav-group">System</div>${all.filter(n => n.area === 'system').map(withSubs).join('')}`;
}
function navState(key) {
  const top = NAV.parent[key] || key;
  $$('#nav a').forEach(a => { a.classList.toggle('on', a.dataset.r === key); a.classList.toggle('parent-on', a.dataset.r === top && top !== key); });
  $$('#nav .subs').forEach(s => s.classList.toggle('open', s.dataset.for === top));
  $('#nav a.on, #nav a.parent-on')?.scrollIntoView({ block: 'nearest', inline: 'nearest' });
  // schmale Fenster: Unterpunkte des aktiven Bereichs als Leiste über der Ansicht
  const fam = NAV.items.filter(n => n.key === top || n.parent === top);
  $('#subnav').innerHTML = fam.length > 1 ? fam.map(n => `<a href="${n.href}" class="${n.key === key ? 'on' : ''}">${esc(n.label)}</a>`).join('') : '';
}
const navBadge = (key, n, live = false) => {
  const b = $(`[data-nb="${key}"]`); if (!b) return;
  const sequence = key === 'skytrace' && !!n && live;
  b.hidden = !n;
  if (sequence) {
    if (!b.classList.contains('sequence-badge')) b.innerHTML = sequenceLoader();
    b.setAttribute('role', 'img'); b.setAttribute('aria-label', 'Spurenanalyse läuft');
  } else { b.textContent = n || ''; b.removeAttribute('role'); b.removeAttribute('aria-label'); }
  b.classList.toggle('live', !!live && !sequence);
  b.classList.toggle('sequence-badge', sequence);
  b.parentElement.classList.toggle('sequence-running', sequence);
};

// ---------------------------------------------------------------- Router --- //
let view = null;      // { update?, navigate?, actions?, destroy? }
let routeTicket = 0;  // schützt vor langsamen Ansichten, die nach einem weiteren Klick fertig werden
const coreRoutes = [
  [/^\/?$/, 'home', homeView],
  [/^\/activity$/, 'activity', activityView],
  [/^\/settings(?:\/(\w+))?$/, 'settings', settingsView],
  [/^\/willkommen$/, 'settings', (el) => window.setupView(el)],          // Einrichtung (setup.js)
];
const allRoutes = () => [...coreRoutes, ...AF.order.flatMap(id => AF.modules[id].routes || [])];
// Inhaltsbreite je Ansicht: Arbeitsflächen (Bild bearbeiten, Spuren prüfen, 3D) nutzen die Breite, Formulare/Import bleiben schmal,
// alles andere ist zentriert auf --page-max begrenzt (style.css → .page)
const PAGE_WIDE = [/^\/galerie\/\d+$/, /^\/analyse$/, /^\/skytrace\/(?!neu$|statistik$|erfolge$)[^/]+/];
const PAGE_NARROW = [/^\/settings/, /^\/willkommen$/];
// Weicher Seitenwechsel: Die bisherige Ansicht bleibt als stilles Abbild („Geist“, ohne IDs, nicht klickbar) stehen, bis die neue
// fertig ist; dann blenden beide kurz ineinander über. Dauert das Laden länger als SKEL_AFTER, erscheint das Skelett.
// So gibt es kein falsches Zwischen-Skelett und keinen leeren Moment. Bei „Bewegung reduzieren“ ohne Überblendung.
const SKEL_AFTER = 350, LOADBAR_AFTER = 150;
// Schmaler Ladebalken unter der Werkzeugleiste – sofortige Rückmeldung, solange eine Ansicht lädt (Zähler für Überlappungen)
const loadbar = (() => {
  let n = 0, timer = null;
  const bar = () => $('#loadbar') || document.body.appendChild(Object.assign(document.createElement('div'), { id: 'loadbar', className: 'loadbar', role: 'progressbar', ariaLabel: 'Lädt' }));
  return {
    start() { n++; if (n === 1) { clearTimeout(timer); timer = setTimeout(() => bar().classList.add('on'), LOADBAR_AFTER); } },
    stop() { n = Math.max(0, n - 1); if (!n) { clearTimeout(timer); $('#loadbar')?.classList.remove('on'); } },
  };
})();
let ghostEl = null;
const reducedMotion = () => matchMedia('(prefers-reduced-motion: reduce)').matches;
function makeGhost(prev) {
  const r = prev.getBoundingClientRect();
  if (!r.width || reducedMotion()) return null;
  const g = document.createElement('div');
  g.className = 'page-ghost';
  g.setAttribute('aria-hidden', 'true');
  Object.assign(g.style, { left: r.left + 'px', top: r.top + 'px', width: r.width + 'px', height: Math.max(0, innerHeight - r.top) + 'px' });
  g.append(...prev.childNodes);
  g.querySelectorAll('[id]').forEach(n => n.removeAttribute('id'));       // globale $('#…') treffen nur die neue Ansicht
  // Zeichenflächen der beendeten Ansicht (WebGL der 3D-Szene, Diagramme) sind schon freigegeben und würden weiß aufblitzen
  g.querySelectorAll('canvas, video').forEach(n => { n.style.visibility = 'hidden'; });
  document.body.append(g);
  return g;
}
function dropGhost(fade = false) {          // fertige Ansicht: sofort weg (keine Doppelbelichtung von Text); nur vor dem Skelett ausblenden
  const g = ghostEl; ghostEl = null;
  if (!g) return;
  if (!fade) return g.remove();
  g.classList.add('out');
  setTimeout(() => g.remove(), 140);
}
async function route() {
  const path = location.hash.replace(/^#/, '') || '/';
  $('#pal').open && $('#pal').close();
  // Ansicht kann den Wechsel selbst übernehmen (z. B. Tabs eines Objekts – ohne Neuladen)
  if (view?.navigate?.(path)) return;
  view?.destroy?.();
  view = null;
  const main = $('#view');
  for (const [re, nav, fn] of allRoutes()) {
    const m = path.match(re);
    if (!m) continue;
    AF.ctx.clear();
    navState(nav);
    $$('.popover.open').forEach(p => p.classList.remove('open'));
    // eigener Behälter je Aufruf: eine verworfene, noch ladende Ansicht schreibt danach nur in ihren abgehängten Behälter,
    // nicht über die aktuelle (Layout-Test fand das beim schnellen Wechseln)
    const host = document.createElement('div');
    host.className = 'page pending' + (PAGE_WIDE.some(r => r.test(path)) ? ' wide' : PAGE_NARROW.some(r => r.test(path)) ? ' narrow' : '');
    host.innerHTML = '<div class="skel" style="height:40px;max-width:360px;margin-bottom:20px"></div><div class="skel-grid">' + '<div class="skel"></div>'.repeat(3) + '</div>';
    const prev = [...main.children].find(c => c.classList.contains('page'));
    if (prev && !ghostEl && !prev.classList.contains('pending')) ghostEl = makeGhost(prev);   // noch ladende Vorgängerin: alten Geist behalten
    main.replaceChildren($('#subnav'), host);
    window.scrollTo({ top: 0 });
    const ticket = ++routeTicket;
    loadbar.start();
    const skel = setTimeout(() => { if (ticket === routeTicket) { dropGhost(true); host.classList.remove('pending'); } }, ghostEl ? SKEL_AFTER : 0);
    let v = null;
    try { v = await fn(host, ...m.slice(1)); } catch (e) {
      if (ticket === routeTicket) { console.error(e); host.innerHTML = `<div class="empty">${icon('alert')}<h2>Fehler</h2>${esc(e.message)}</div>`; }
    }
    clearTimeout(skel);
    loadbar.stop();
    if (ticket !== routeTicket) { v?.destroy?.(); return; }      // inzwischen weiternavigiert – diese Ansicht verwerfen
    view = v;
    const wasPending = host.classList.contains('pending');
    host.classList.remove('pending');
    host.classList.add(wasPending ? 'swap' : 'enter');           // direkt vom Geist zur fertigen Seite: nur ein Hauch Einblendung
    dropGhost();
    main.focus({ preventScroll: true });
    return;
  }
  location.hash = '#/';
}

// ----------------------------------------------------------------- Polling --- //
// Gemeinsamer Status aller Module; laufende Aufträge (alle Module) als Anzeige oben + Meldung beim Fertigwerden
let lastActive = null;   // key -> Auftrag (null = erster Durchlauf, noch nichts melden)
async function poll() {
  try {
    const st = AF.status = await api('/status');
    const b = $('#busy');
    if (b) { b.hidden = !st.busy; b.textContent = st.busy; }
    for (const id of AF.order) AF.modules[id].status?.(st, navBadge);
    sideFoot(st);
    if (st.busy || lastActive?.size || $('#jobpop').classList.contains('open')) await pollActivity(); else { lastActive = new Map(); showJobPill(null); }
    await view?.update?.(st);
  } catch { /* Server kurz weg – nächster Versuch */ }
}
// Systemstatus: Fuß der Sidebar + Punkt in der Toolbar (grün = Automatik an, gelb = Aufträge warten, grau = Automatik aus)
function sideFoot(st) {
  const s = st.modules.stacking || {}, d = s.disk, used = d?.total ? 1 - d.free / d.total : null;
  $('#sidefoot').innerHTML = `<div><span class="dot${s.auto ? '' : ' off'}"></span>${s.auto ? 'Automatik aktiv' : 'Automatik aus'}</div>
    <div>Letzter Scan: ${ago(s.last_scan)}</div>
    ${d ? `<div class="mini-bar${used > .9 ? ' warn' : ''}" title="${Math.round(used * 100)} % belegt"><i style="width:${Math.round(used * 100)}%"></i></div><div class="${s.hold && /frei/.test(s.hold) ? 'bad' : ''}">${fmb(d.free)} frei</div>` : ''}
    ${s.hold ? `<a class="warn" href="#/activity">${s.paused ? 'Aufträge angehalten' : 'Aufträge warten (Speicher)'}</a>` : ''}<div>AstroFlow ${esc(st.version)}</div>`;
  const dot = $('#sysdot');
  dot.className = 'sysdot' + (s.hold ? ' warn' : s.auto ? '' : ' off');
  $('#sysBtn').title = `System: ${s.auto ? 'Automatik aktiv' : 'Automatik aus'}${d ? ` · ${fmb(d.free)} frei` : ''}${s.hold ? ' · ' + s.hold : ''} · AstroFlow ${st.version}`;
}
let lastItems = [];
async function pollActivity() {
  const items = lastItems = await api('/activity?limit=25'), act = items.filter(running), now = new Map(act.map(a => [a.key, a]));
  if (lastActive) {
    for (const [key, old] of lastActive) {
      if (now.has(key)) continue;
      const a = items.find(x => x.key === key);
      if (!a || location.hash.startsWith(old.href || '##')) continue;      // die offene Ansicht meldet selbst
      if (a.state === 'error') toast(`„${a.title}“: fehlgeschlagen.`, 'err', { label: 'Details', href: a.href || '#/activity' });
      else if (a.state === 'done') toast(`„${a.title}“ ist fertig.`, 'ok', { label: 'Ansehen', href: a.href || '#/activity' });
    }
  }
  lastActive = now;
  showJobPill(act.find(a => a.state === 'running') || act[0], act.length);
  drawJobPop(items);
}
// Auftragsanzeige in der Toolbar: Zähler + aktueller Auftrag; Klick öffnet die Liste (Popover)
function showJobPill(a, n = 0) {
  const pill = $('#jobpill');
  const pc = a?.progress != null ? Math.round(a.progress * 100) : null;
  document.title = a?.state === 'running' ? `${pc != null ? pc + ' % · ' : ''}${a.title} – AstroFlow` : 'AstroFlow';
  pill.classList.toggle('idle', !a);
  $('.count', pill).textContent = n || 0;
  $('.count', pill).hidden = !n;
  pill.title = a ? `${n} laufende${n === 1 ? 'r' : ''} Auftr${n === 1 ? 'ag' : 'äge'}` : 'Keine laufenden Aufträge';
  const symbol = $('.job-symbol', pill), q = a?.state === 'queued';
  const loader = !a ? 'idle' : !q && a.key?.startsWith('skytrace:') ? 'sequence' : 'spinner';
  if (symbol.dataset.loader !== loader) {
    symbol.innerHTML = loader === 'sequence' ? sequenceLoader() : loader === 'spinner' ? '<span class="spinner" aria-hidden="true"></span>' : icon('pulse');
    symbol.dataset.loader = loader;
  }
  $('.t', pill).innerHTML = a ? `<b>${esc(a.title)}</b> <span class="mut">${q ? 'wartet' : pc != null ? pc + ' %' : esc(a.stage || 'läuft')}</span>` : '';
  requestAnimationFrame(() => { $('i', pill).style.width = (!a || q || pc == null ? 0 : pc) + '%'; });
}
function drawJobPop(items = lastItems) {
  const pop = $('#jobpop');
  if (!pop.classList.contains('open')) return;
  const act = items.filter(running), past = items.filter(j => !running(j)).slice(0, 3);
  const row = (j) => `<a class="pj" href="${j.href || '#/activity'}"><b>${esc(j.title)}</b><span class="mut sm tnum">${j.state === 'queued' ? 'wartet' : j.state === 'running' ? (j.progress != null ? Math.round(j.progress * 100) + ' %' : 'läuft') : j.state === 'error' ? '<span class="bad">Fehler</span>' : ago(j.finished || j.started)}</span>
    <small>${esc(AF.info(j.module)?.name || j.module)} · ${esc(j.detail || j.kind || '')}</small>${running(j) ? `<div class="progress ${j.state === 'queued' || j.progress == null ? 'indet' : ''}"><i style="width:${(j.progress || 0) * 100}%"></i></div>` : ''}</a>`;
  pop.innerHTML = `<div class="ph">${act.length ? `${act.length} laufend` : 'Keine laufenden Aufträge'}</div>${act.map(row).join('')}
    ${past.length ? `<div class="ph">Zuletzt</div>${past.map(row).join('')}` : act.length ? '' : '<div class="pnone">Verarbeitung, Spurenanalysen und Recherchen erscheinen hier.</div>'}
    <div class="pfoot"><a class="linkbtn" href="#/activity">Alle Aufträge ${icon('arrow')}</a></div>`;
}
async function toggleJobPop(e) {
  e.stopPropagation();
  const pop = $('#jobpop'), open = !pop.classList.contains('open');
  pop.classList.toggle('open', open);
  $('#jobpill').setAttribute('aria-expanded', open);
  if (open) { drawJobPop(); try { await pollActivity(); } catch { /* egal */ } }
}

// --------------------------------------------------------------- Übersicht --- //
// Dashboard (zentriert, eine klare Reihenfolge): laufende Aufträge (leer = eine Zeile), zuletzt bearbeitete Bilder (recent),
// Entdeckungen (discoveries), kompakte Modulzeile (summary); daneben „Heute Nacht“ (homeFeatured, kompakt; volle Grafik in der
// Aufteilung „Nachtplanung“). Jeder Abschnitt wird nur bei geänderter Ausgabe neu geschrieben.
async function homeView(el) {
  crumbs([['Übersicht']]);
  const h = new Date().getHours();
  const hello = h < 5 || h >= 21 ? 'Gute Nacht' : h < 11 ? 'Guten Morgen' : h < 17 ? 'Willkommen zurück' : 'Guten Abend';
  el.innerHTML = `<div class="pagehead"><div><h1>Übersicht</h1><p class="welcome">${hello} – deine Aufnahmen, Analysen und Entdeckungen.</p></div>
      <div class="page-actions"><label class="dashboard-mode"><span>Aufteilung</span><select aria-label="Dashboard-Aufteilung"><option value="balanced">Ausgewogen</option><option value="planning">Nachtplanung</option><option value="library">Bilder &amp; Module</option></select></label></div></div>
    <div id="hJobs"><p class="hnone hph"><span class="skel-line"></span></p></div>
    <div class="home-grid" id="homeGrid"><div class="home-main">
      <section id="hRecentS"><h2 class="sect">Zuletzt bearbeitet <a href="${AF.on('stacking') ? '#/galerie' : '#/'}">Galerie öffnen</a></h2><div id="hRecent">
        <div class="hthumbs hph">${'<span class="skel skel-thumb"></span>'.repeat(4)}</div></div></section>
      <section id="hDiscS"><h2 class="sect">Neueste Entdeckungen</h2><div id="hDisc"><div class="hdisc hph">${'<span class="skel skel-row"></span>'.repeat(3)}</div></div></section>
      <section><h2 class="sect">Module</h2><div class="home-modules">${AF.order.filter(id => AF.modules[id].summary).map(id => `<span class="skel skel-card" data-m="${id}"></span>`).join('')}</div></section>
    </div><div class="home-featured">${AF.order.filter(id => AF.modules[id].homeFeatured).map(id => `<section class="hcard featured" data-m="${id}"><div class="skel skel-feat"></div></section>`).join('')}</div></div>`;
  const layout = $('.dashboard-mode select', el), grid = $('.home-grid', el);
  const saved = store.get('dashboard.layout', 'balanced');
  layout.value = ['balanced', 'planning', 'library'].includes(saved) ? saved : 'balanced';
  grid.dataset.layout = layout.value;
  layout.onchange = () => { grid.dataset.layout = layout.value; store.set('dashboard.layout', layout.value); load(); };
  // put: nur bei geänderter Ausgabe schreiben; der erste Inhalt eines Abschnitts blendet sanft ein (ersetzt den Platzhalter)
  const put = (node, html) => { if (node && node.homeHTML !== html) { const first = node.homeHTML === undefined; node.homeHTML = html; node.innerHTML = html;
    if (first) { node.classList.remove('fill'); void node.offsetWidth; node.classList.add('fill'); } } };
  const safe = async (fn, d) => { try { return (await fn()) ?? d; } catch { return d; } };
  const failed = (what) => `<p class="mut sm home-err">${icon('alert')}${what} konnte gerade nicht geladen werden.</p>`;
  // Jeder Abschnitt lädt für sich: schnelle Kacheln stehen sofort, langsame (Heute Nacht, Sage, SkyTrace) folgen ohne die anderen aufzuhalten
  function load(st) {
    const statusP = st ? Promise.resolve(st) : AF.status ? Promise.resolve(AF.status) : api('/status');
    const part = (fn) => statusP.then(fn).catch(() => {});
    for (const id of AF.order.filter(i => AF.modules[i].summary)) part(async (s) => paintCard(id, await safe(() => AF.modules[id].summary(s), null)));
    part(async () => paintJobs(await safe(() => api('/activity?limit=12'), [])));
    part(async (s) => paintRecent(await Promise.all(AF.order.map(id => safe(() => AF.modules[id].recent?.(s), [])))));
    part(async (s) => paintDisc(await Promise.all(AF.order.map(id => safe(() => AF.modules[id].discoveries?.(s), [])))));
    for (const id of AF.order.filter(i => AF.modules[i].homeFeatured))
      part(async (s) => { let html; try { html = await AF.modules[id].homeFeatured(s); } catch { html = failed(AF.info(id)?.name || 'Dieser Bereich'); } paintFeatured(id, html); });
  }
  function paintCard(id, c) {
    const slot = el.isConnected && $(`.home-modules [data-m="${id}"]`, el);
    if (!slot) return;
    // Fehlschlag: Platz behalten (Hinweis statt Platzhalter), damit die nächste erfolgreiche Aktualisierung die Karte wieder einsetzt
    if (!c) { if (slot.classList.contains('skel')) { const e = document.createElement('div'); e.className = 'modcard modcard-err fill'; e.dataset.m = id;
      e.innerHTML = failed(AF.info(id)?.name || 'Dieses Modul'); slot.replaceWith(e); } return; }
    const html = `<a class="modcard" data-m="${id}" href="${c.href}">
      <div class="mh">${icon(c.icon || AF.info(id)?.icon || 'grid')}<b>${esc(c.title || AF.info(id)?.name)}</b><span>Öffnen ${icon('arrow')}</span></div>
      <div class="ms">${(c.stats || []).map(([v, l]) => `<div><b class="tnum">${esc(v)}</b><small>${esc(l)}</small></div>`).join('')}</div>
      <div class="ml">${c.live || esc(c.sub || '')}</div></a>`;
    if (slot.homeHTML === html) return;
    const tmp = document.createElement('div'); tmp.innerHTML = html;
    const card = tmp.firstElementChild; card.homeHTML = html;
    if (slot.classList.contains('skel')) card.classList.add('fill');
    slot.replaceWith(card);
  }
  function paintJobs(acts) {
    if (!el.isConnected) return;
    const act = acts.filter(running);
    put($('#hJobs', el), act.length ? `<section class="home-jobs"><h2 class="sect">Laufende Aufträge <a href="#/activity">Alle anzeigen</a></h2><div class="hjobs">${act.slice(0, 5).map(j => `<a class="hjob" href="${j.href || '#/activity'}"><b>${esc(j.title)}</b>
        <span class="mut">${esc(AF.info(j.module)?.name || j.module)} · ${esc(j.state === 'queued' ? 'wartet' : j.stage || j.detail || 'läuft')}</span>
        <div class="progress ${j.state === 'queued' || j.progress == null ? 'indet' : ''}"><i style="width:${(j.progress || 0) * 100}%"></i></div>
        <span class="pc">${j.state === 'running' && j.progress != null ? Math.round(j.progress * 100) + ' %' : ''}</span></a>`).join('')}</div></section>`
      : `<p class="hnone">${icon('check')}Keine laufenden Aufträge${acts[0] ? ` · zuletzt <a href="${acts[0].href || '#/activity'}">${esc(acts[0].title)}</a>, ${ago(acts[0].finished || acts[0].started)}` : ''} · <a href="#/activity">Aufträge</a></p>`);
  }
  function paintRecent(recent) {
    if (!el.isConnected) return;
    const rec = recent.flat().slice(0, 8);
    $('#hRecentS', el).hidden = !rec.length;
    put($('#hRecent', el), `<div class="hthumbs">${rec.map(r => `<a href="${r.href}" title="${esc(r.title)}"><span class="ii"><img src="${r.img}" alt="" loading="lazy"></span><span>${esc(r.title)}</span><small>${esc(r.sub || '')}</small></a>`).join('')}</div>`);
  }
  function paintDisc(disc) {
    if (!el.isConnected) return;
    const ds = disc.flat().sort((a, b) => (b.t || 0) - (a.t || 0)).slice(0, 6);
    $('#hDiscS', el).hidden = !ds.length;
    put($('#hDisc', el), `<div class="hdisc">${ds.map(d => `<a class="hd" href="${d.href}">${d.img ? `<img src="${d.img}" alt="" loading="lazy">` : `<span class="hdi">${icon(d.icon || 'sparkle')}</span>`}
      <div><small>${esc(d.kicker || '')}</small><b>${esc(d.title)}</b><span>${esc(d.sub || '')}</span></div></a>`).join('')}</div>`);
  }
  function paintFeatured(id, html) {
    if (!el.isConnected) return;
    const fgroup = $('.home-featured', el);
    let node = fgroup.querySelector(`[data-m="${id}"]`);
    if (!html) node?.remove();
    else { if (!node) { node = document.createElement('section'); node.className = 'hcard featured'; node.dataset.m = id; fgroup.append(node); } put(node, html); }
    grid.classList.toggle('has-featured', !!fgroup.children.length);
  }
  grid.classList.toggle('has-featured', !!$('.home-featured', el).children.length);
  load();                                     // nicht abwarten: das Gerüst steht sofort, die Abschnitte füllen sich einzeln
  return { update: load };
}

// --------------------------------------------------------------- Aufträge --- //
async function activityView(el) {
  crumbs([['Aufträge']]);
  const mods = AF.order;
  let filter = 'all', sig = '';
  el.innerHTML = `<div class="pagehead"><div><h1>Aufträge</h1><p class="mut">Alle Hintergrundprozesse aus allen Modulen – Verarbeitung, Spurenanalysen, Recherchen und Geschichten.</p></div>
      <div class="page-actions" id="qctl"></div></div>
    <div id="qhold"></div>
    <div class="toolbar"><div class="seg" id="af">${[['all', 'Alle'], ...mods.map(id => [id, AF.info(id).name])].map(([k, l]) => `<button data-v="${k}" class="${k === filter ? 'on' : ''}">${esc(l)}</button>`).join('')}</div></div>
    <div id="jobs"></div>`;
  $('#af').onclick = (e) => { const b = e.target.closest('button'); if (!b) return; filter = b.dataset.v; $$('#af button').forEach(x => x.classList.toggle('on', x === b)); sig = ''; load(); };
  const label = { queued: ['', 'wartet'], running: ['acc', 'läuft'], done: ['ok', 'fertig'], error: ['bad', 'Fehler'], stopped: ['warn', 'angehalten'] };
  const ji = (j) => j.state === 'done' ? `<span class="ji ok">${icon('check')}</span>` : j.state === 'error' ? `<span class="ji bad">${icon('alert')}</span>`
    : j.state === 'running' ? '<span class="ji run"><span class="spinner"></span></span>' : `<span class="ji">${icon('pulse')}</span>`;
  const msg = (j) => j.state === 'error' ? `<span class="bad">${esc(j.detail)}</span>` : running(j) ? esc(j.detail || (j.state === 'queued' ? 'wartet' : 'läuft'))
    : `${esc(j.kind || '')}${j.detail ? ' · ' + esc(j.detail) : ''}`;
  const side = (j) => `${j.started ? `<span title="Start">${fdate(j.started)}</span>` : ''}${j.finished && j.started && j.finished - j.started > 1 ? `<span title="Dauer">${dur(j.finished - j.started)}</span>`
    : j.state === 'running' && j.started ? `<span title="Läuft seit">seit ${dur(Date.now() / 1000 - j.started)}</span>` : ''}`;
  const row = (j, i) => `<a class="job ${running(j) ? 'running' : ''}" href="${j.href || '#/activity'}" data-j="${esc(j.key)}" style="--i:${i}">${ji(j)}
      <div style="min-width:0"><b style="color:var(--fg);font-weight:550">${esc(j.title)}</b> <span class="modtag">${esc(AF.info(j.module)?.name || j.module)}${j.stage && running(j) ? ' · ' + esc(j.stage) : ''}</span>
        <div class="mut sm jmsg">${msg(j)}</div></div>
      <div class="jside"><span class="jtime">${side(j)}</span><span class="chip ${label[j.state]?.[0] || ''}">${j.state === 'running' && j.progress != null ? `${Math.round(j.progress * 100)} %` : label[j.state]?.[1] || esc(j.state)}</span></div>
      ${running(j) ? `<div class="progress ${j.state === 'queued' || j.progress == null ? 'indet' : ''}"><i style="width:${(j.progress || 0) * 100}%"></i></div>` : ''}</a>`;
  // Bilder-Warteschlange anhalten/fortsetzen (laufender Auftrag setzt später am Checkpoint fort) – die einzige echte Steuerung
  let qsig = null;
  const queueCtl = () => {
    const s = AF.status?.modules?.stacking;
    if (!AF.on('stacking') || !s || !$('#qctl')) return;
    const k = JSON.stringify([s.paused, s.hold]);
    if (k === qsig) return;
    qsig = k;
    $('#qctl').innerHTML = s.paused ? `<button class="btn" id="qbtn">${icon('play')}Aufträge fortsetzen</button>`
      : `<button class="btn ghost" id="qbtn" title="Laufende Bildverarbeitung abbrechen und Warteschlange anhalten">${icon('pause')}Bilder-Aufträge anhalten</button>`;
    $('#qhold').innerHTML = s.hold ? `<div class="notice ${s.paused && !/voll/.test(s.hold) ? '' : 'err'}">${icon('alert')}<div>${esc(s.hold)}</div></div>` : '';
    $('#qbtn').onclick = async () => {
      const r = await send('/stacking/queue', { paused: !s.paused });
      Object.assign(s, r); qsig = null; queueCtl();
      toast(r.paused ? 'Warteschlange angehalten – der laufende Auftrag stoppt gleich.' : 'Aufträge laufen weiter.', 'ok');
    };
  };
  async function load() {
    queueCtl();
    const items = (await api('/activity?limit=60')).filter(j => filter === 'all' || j.module === filter);
    if (!el.isConnected) return;
    const s = JSON.stringify(items.map(j => [j.key, j.state]));
    const act = items.filter(running), past = items.filter(j => !running(j));
    if (s === sig) {
      act.forEach(j => {                          // nur Fortschritt: Balken und Texte vor Ort aktualisieren
        const r = $(`[data-j="${CSS.escape(j.key)}"]`);
        if (!r) return;
        $('.jmsg', r).innerHTML = msg(j);
        $('.jtime', r).innerHTML = side(j);
        if (j.state === 'running' && j.progress != null) { $('.chip', r).textContent = Math.round(j.progress * 100) + ' %'; $('.progress i', r).style.width = j.progress * 100 + '%'; }
      });
      return;
    }
    sig = s;
    $('#jobs').innerHTML = items.length ? `${act.length ? `<h2 class="sect">Läuft · ${act.length}</h2><div class="jobs stagger">${act.map(row).join('')}</div>` : ''}
      ${past.length ? `<h2 class="sect" ${act.length ? 'style="margin-top:24px"' : ''}>Verlauf</h2><div class="jobs stagger">${past.map(row).join('')}</div>` : ''}`
      : `<div class="empty">${icon('pulse')}<h2>Noch nichts passiert</h2>Sobald Aufnahmen verarbeitet, Bildfolgen analysiert oder Geschichten erzählt werden, erscheinen die Aufträge hier.</div>`;
  }
  await load();
  return { update: load };
}

// ------------------------------------------------------------ Einstellungen --- //
async function settingsView(el, section) {
  crumbs([['Einstellungen']]);
  const secs = [...AF.order.filter(id => AF.modules[id].settings).map(id => [id, AF.info(id).name, AF.info(id).icon]), ['standorte', 'Standorte', 'pin'], ['module', 'Module & System', 'gear']];
  section = secs.some(s => s[0] === section) ? section : secs[0][0];
  el.innerHTML = `<div class="pagehead"><div><h1>Einstellungen</h1><p class="mut">Alles wird im Datenordner gespeichert und gilt auch nach einem Neustart.</p></div></div>
    <div class="settings"><nav class="snav" id="snav">${secs.map(([k, l, ic]) => `<a href="#/settings/${k}" data-s="${k}" class="${k === section ? 'on' : ''}">${icon(ic)}${esc(l)}</a>`).join('')}</nav>
    <div id="sbody"></div></div>`;
  const body = $('#sbody');
  const show = async (k) => {
    section = k;
    $$('#snav a').forEach(a => a.classList.toggle('on', a.dataset.s === k));
    body.classList.remove('enter');
    body.innerHTML = '<div class="skel" style="height:300px"></div>';
    try { await (k === 'module' ? moduleSettings(body) : k === 'standorte' ? window.sitesSettings(body) : AF.modules[k].settings(body)); }
    catch (e) { body.innerHTML = `<div class="notice err"><div><b>Fehler</b><div class="mut sm">${esc(e.message)}</div></div></div>`; }
    void body.offsetWidth; body.classList.add('enter');
  };
  await show(section);
  return { navigate: (path) => { const m = path.match(/^\/settings(?:\/(\w+))?$/); if (!m) return false; if ((m[1] || secs[0][0]) !== section) show(m[1] || secs[0][0]); return true; } };
}
async function moduleSettings(el) {
  const info = await api('/app'), st = AF.status || await api('/status');
  const scale = store.get('ui.scale', 0), look = store.get('ui.look', 'dezent');
  el.innerHTML = `<div class="panel"><h2>Darstellung</h2><p class="mut sm" style="margin:6px 0 12px">Größe von Schrift und Bedienelementen. „Automatisch“ vergrößert
      auf großen Monitoren (z. B. WQHD, 4K). Gilt nur für diesen Browser.</p>
    <div class="seg" id="uiScale" aria-label="Oberflächengröße">${UI_SCALES.map(([v, l]) => `<button data-v="${v}" class="${v === scale ? 'on' : ''}">${l}</button>`).join('')}</div>
    <p class="mut sm" style="margin:16px 0 12px">Stil: „Schlicht“ ist neutrales Anthrazit, „Dezent“ und „Nebula“ bringen Farbverlauf und Glasflächen.</p>
    <div class="seg" id="uiLook" aria-label="Stil">${UI_LOOKS.map(([v, l]) => `<button data-v="${v}" class="${v === look ? 'on' : ''}">${l}</button>`).join('')}</div>
    <label class="modrow" style="margin-top:14px"><div><b>Sternschnuppen</b><div class="mut sm">Selten eine Sternschnuppe im Hintergrund – so oft, wie es der echte Himmel gerade hergibt (Meteorströme, Uhrzeit, Standort, Mond; tagsüber keine). Nur Dezent/Nebula, aus bei „Bewegung reduzieren“.</div></div>
      <input type="checkbox" id="uiMeteors" ${store.get('ui.meteors', true) ? 'checked' : ''}><span class="switch"></span></label>
    <p class="mut sm" id="meteorInfo" style="margin:8px 0 0"></p></div>
    <div class="panel"><h2>Module</h2><p class="mut sm" style="margin:6px 0 16px">AstroFlow besteht aus Modulen. Abgeschaltete Module werden nicht geladen –
      Änderungen wirken nach einem Neustart des Servers. „Bilder“ ist das Kernmodul.</p>
    <div class="modlist">${info.modules.map(m => `<label class="modrow">${icon(m.icon)}<div><b>${esc(m.name)}</b>${m.error ? ` <span class="chip bad">Fehler</span>` : ''}
        <div class="mut sm">${esc(m.description)}</div>${m.error ? `<div class="bad sm">${esc(m.error)}</div>` : ''}</div>
        <input type="checkbox" data-mod="${m.id}" ${m.enabled ? 'checked' : ''} ${m.id === 'stacking' ? 'disabled' : ''}><span class="switch"></span></label>`).join('')}</div>
    <p class="sm" id="modnote" hidden><span class="warn">Neustart nötig:</span> <span class="mut">AstroFlow beenden und neu starten, damit die Änderung wirkt.</span></p></div>
    <div class="panel"><h2 style="margin-bottom:12px">System</h2><dl class="kv">
      <dt>Version</dt><dd>AstroFlow ${esc(info.version)}</dd>
      <dt>Aktive Module</dt><dd>${info.modules.filter(m => m.available).map(m => esc(m.name)).join(', ')}</dd>
      ${st.modules.stacking?.disk ? `<dt>Speicher frei</dt><dd>${fmb(st.modules.stacking.disk.free)} von ${fmb(st.modules.stacking.disk.total)}</dd>` : ''}
    </dl><p class="mut sm" style="margin:14px 0 0">Tipp: Im Browser-Menü „App installieren“ wählen – AstroFlow läuft dann wie eine eigene App.</p></div>`;
  window.setupSystemPanel?.(el);
  $('#uiScale', el).onclick = (e) => {
    const b = e.target.closest('button[data-v]');
    if (!b) return;
    const v = Number(b.dataset.v);
    store.set('ui.scale', v); applyUiScale(v);
    $$('#uiScale button', el).forEach(x => x.classList.toggle('on', x === b));
  };
  $('#uiLook', el).onclick = (e) => {
    const b = e.target.closest('button[data-v]');
    if (!b) return;
    store.set('ui.look', b.dataset.v); applyLook(b.dataset.v);
    $$('#uiLook button', el).forEach(x => x.classList.toggle('on', x === b));
    meteors.start();
  };
  window.meteors?.info().then(t => { const i = $('#meteorInfo', el); if (i) i.textContent = t; }).catch(() => {});
  $('#uiMeteors', el).onchange = (e) => { e.stopPropagation(); store.set('ui.meteors', e.target.checked); if (e.target.checked) meteors.one(); else meteors.stop(); };
  el.onchange = async (e) => {
    const c = e.target.closest('[data-mod]');
    if (!c) return;
    try { await send('/modules', { [c.dataset.mod]: c.checked }); $('#modnote').hidden = false; toast('Gespeichert – wirkt nach einem Neustart.', 'ok'); }
    catch (err) { toast(err.message, 'err'); c.checked = !c.checked; }
  };
}

// ------------------------------------------------------- Befehlspalette --- //
// Strg+K: Objekte/Läufe/Geschichten öffnen, Ansichten wechseln, Aktionen der aktuellen Ansicht ausführen
async function openPalette() {
  const d = $('#pal');
  if (d.open) return;
  d.innerHTML = `<div class="pin">${icon('search')}<input placeholder="Objekt, Analyse oder Befehl suchen …" autocomplete="off" spellcheck="false"><kbd>Esc</kbd></div><ul></ul>
    <div class="pfoot"><span><kbd>↑</kbd><kbd>↓</kbd> auswählen</span><span><kbd>↵</kbd> öffnen</span><span><kbd>/</kbd> Suche in Listen</span></div>`;
  d.showModal();
  const inp = $('input', d), ul = $('ul', d);
  const go = (h) => () => { location.hash = h; };
  const base = [...NAV.items.map(n => [n.icon || 'arrow', n.label, n.module ? AF.info(n.module).name : n.key === 'home' ? 'Start' : 'System', go(n.href)]),
    ...(AF.on('stacking') ? [['upload', 'Dateien importieren', 'FITS, JPG, PNG, TIFF', go('#/import')]] : []),
    ['pulse', 'Aufträge anzeigen', 'alle laufenden und fertigen Aufträge', go('#/activity')], ['gear', 'Einstellungen', 'Module, Solver, KI', go('#/settings')], ['pin', 'Standorte', 'Beobachtungsorte und Zeiträume', go('#/settings/standorte')]];
  let extra = [], sel = 0, items = [];
  const draw = () => {
    const words = inp.value.toLowerCase().split(/\s+/).filter(Boolean);
    const hit = (t) => words.every(w => t.toLowerCase().includes(w));
    const ctx = (view?.actions?.() || []).filter(a => hit(a[1] + ' ' + a[2]));
    const groups = [['Diese Ansicht', ctx], ...extra.map(([g, list]) => [g, list.filter(a => hit(a[1] + ' ' + (a[2] || ''))).slice(0, 7)]),
      ['Navigation', base.filter(a => hit(a[1] + ' ' + a[2]))]];
    items = groups.flatMap(([, l]) => l.map(a => typeof a[3] === 'string' ? go(a[3]) : a[3]));
    sel = Math.min(sel, Math.max(0, items.length - 1));
    let i = 0;
    const li = (a) => `<li class="it ${i === sel ? 'on' : ''}" data-i="${i++}">${a[4] ? `<img src="${a[4]}" alt="">` : `<span class="ic">${icon(a[0])}</span>`}<span>${esc(a[1])}</span><small>${esc(a[2] || '')}</small></li>`;
    ul.innerHTML = groups.filter(([, l]) => l.length).map(([g, l]) => `<li class="h">${esc(g)}</li>` + l.map(li).join('')).join('') || '<li class="pnone">Nichts gefunden</li>';
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
  // Module liefern ihre Einträge (Objekte, Läufe, Geschichten) nach
  const lists = await Promise.all(AF.order.map(async id => { try { return [AF.info(id).name, await AF.modules[id].palette?.() || []]; } catch { return [id, []]; } }));
  const jobs = (await api('/activity?limit=25').catch(() => [])).filter(running).map(j => ['pulse', j.title, `${AF.info(j.module)?.name || j.module} · ${j.state === 'queued' ? 'wartet' : j.stage || 'läuft'}`, j.href || '#/activity']);
  extra = [['Laufende Aufträge', jobs], ...lists].filter(([, l]) => l.length);
  if (d.open) draw();
}

// ------------------------------------------------------------------ Start --- //
async function boot() {
  try { await loadModules(); }
  catch (e) { $('#view').innerHTML = `<div class="empty">${icon('alert')}<h2>Server nicht erreichbar</h2>${esc(e.message)}</div>`; return; }
  buildNav();
  $('#palBtn').onclick = openPalette;
  // Sidebar ein-/ausklappen (gemerkt je Browser); Kontextpanel; Auftrags-Popover
  const shell = $('#shell');
  shell.classList.toggle('mini', store.get('side.mini', false));
  $('#sideToggle').onclick = () => {
    const narrow = matchMedia('(max-width: 1100px)').matches;
    if (narrow) shell.classList.toggle('wide'); else { shell.classList.toggle('mini'); store.set('side.mini', shell.classList.contains('mini')); }
  };
  $('#ctxBtn').onclick = () => AF.ctx.toggle();
  $('#ctxClose').onclick = () => AF.ctx.hide();
  $('#ctxScrim').onclick = () => AF.ctx.hide();
  matchMedia('(min-width: 1280px)').addEventListener('change', ctxSync);
  $('#jobpill').onclick = toggleJobPop;
  document.addEventListener('click', (e) => { if (e.target.closest('[data-ctx-open]')) AF.ctx.show(); else if (e.target.closest('[data-ctx-toggle]')) AF.ctx.toggle(); });
  document.addEventListener('click', (e) => { if (!e.target.closest('.jobs-ind')) { $('#jobpop').classList.remove('open'); $('#jobpill').setAttribute('aria-expanded', false); } });
  addEventListener('keydown', (e) => { if (e.key === 'Escape') { $('#jobpop').classList.remove('open'); if ($('#shell').classList.contains('ctx-shown')) AF.ctx.hide(); } });
  addEventListener('keydown', (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); openPalette(); }
  });
  addEventListener('resize', () => slideInd($('#tabind'), $('#tabs a.on'), true));
  // Dateien überall auf die Seite ziehen: das erste Modul, das sie annimmt, bekommt sie (FITS → Bilder-Pipeline, JPG/PNG/TIFF → Galerie)
  let dragDepth = 0;
  const own = (e) => e.target.closest?.('.dropzone');
  addEventListener('dragenter', (e) => { if (e.dataTransfer?.types.includes('Files') && !own(e)) { dragDepth++; $('#drop').hidden = false; } });
  addEventListener('dragleave', () => { if (--dragDepth <= 0) { dragDepth = 0; $('#drop').hidden = true; } });
  addEventListener('dragover', (e) => e.preventDefault());
  addEventListener('drop', (e) => {
    e.preventDefault(); dragDepth = 0; $('#drop').hidden = true;
    if (own(e)) return;
    droppedFiles(e.dataTransfer).then(fl => {
      if (!fl.length) return;
      if (!AF.order.some(id => AF.modules[id].drop?.(fl))) toast('Dieses Dateiformat kann AstroFlow nicht übernehmen.', 'err');
    });
  });
  addEventListener('hashchange', route);
  // Erster Start der installierten Version → Einrichtung; neue Version → einmaliger Hinweis je Version
  if (AF.app.setup?.pending && /^#?\/?$/.test(location.hash)) location.hash = '#/willkommen';
  const upd = AF.app.update;
  if (upd?.latest && store.get('update.seen', '') !== upd.latest) {
    store.set('update.seen', upd.latest);
    toast(`AstroFlow ${upd.latest} ist verfügbar.`, 'ok', { label: 'Herunterladen', href: upd.url });
  }
  await route();
  poll();
  setInterval(() => { if (!document.hidden) poll(); }, 2500);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) poll(); });
}
boot();
