'use strict';
/* AstroFlow-Shell: lädt die Module (/api/app), baut Navigation, Übersicht, Aktivität, Einstellungen und Befehlspalette.
   Module registrieren sich mit AF.register(id, {...}) – siehe „Modul-Vertrag“ unten. Alle Helfer sind global. */

// ---------------------------------------------------------------- Helfer --- //
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const icon = (n) => `<svg><use href="#i-${n}"/></svg>`;
async function api(u, opt = {}) {
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
    d.innerHTML = `<h2 style="font-size:18px;margin-bottom:8px">${esc(title)}</h2><p class="mut">${text}</p>
      <div class="row" style="justify-content:flex-end;margin-top:18px"><button class="btn ghost" value="0">Abbrechen</button><button class="btn${danger ? ' danger' : ''}" value="1">${esc(ok)}</button></div>`;
    d.onclick = (e) => { const v = e.target.closest('button')?.value; if (v) { d.close(); res(v === '1'); } };
    d.onclose = () => res(false);
    d.showModal();
  });
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
    iw = main.naturalWidth; ih = main.naturalHeight;
    if (ov) { ov.setAttribute('viewBox', `0 0 ${iw} ${ih}`); ov.style.width = iw + 'px'; ov.style.height = ih + 'px'; opts.overlay(ov, iw, ih); }
    fit(); requestAnimationFrame(() => v.classList.add('ready'));
  };
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
  return { el: v, overlay: ov, destroy: () => { ro.disconnect(); document.removeEventListener('keydown', onKey); } };
}

// ----------------------------------------------------------- Modul-Vertrag --- //
/* AF.register(id, {
     nav: [{ href, label, icon, key }],          Einträge in der Seitenleiste (Gruppe = Modulname)
     routes: [[regex, navKey, async (el, ...m) => view]],   view = { update?(st), navigate?(path), actions?(), destroy?() }
     home: async (st) => html | '' ,             Karte auf der Übersicht
     settings: async (el) => void,               Abschnitt in den Einstellungen
     palette: async () => [[icon, label, sub, fn|href, img?]],  Einträge der Befehlspalette (z. B. Objekte)
     status: (st, nav) => void,                  Polling-Hook (Zähler in der Navigation)
     drop: (files) => bool                       global abgelegte Dateien übernehmen
   }) */
const AF = { app: null, modules: {}, order: [], status: null,
  register(id, def) { this.modules[id] = { id, ...def }; },
  on(id) { return !!this.modules[id]; },
  info(id) { return this.app?.modules.find(m => m.id === id); },
};

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

function buildNav() {
  const nav = $('#nav');
  let h = `<span class="nav-ind" id="navind"></span><a href="#/" data-r="home">${icon('home')}<span>Übersicht</span></a>`;
  for (const id of AF.order) {
    const m = AF.modules[id], info = AF.info(id);
    if (!m.nav?.length) continue;
    h += `<div class="nav-group">${esc(info.name)}</div>` + m.nav.map(n => `<a href="${n.href}" data-r="${n.key}"${n.sec ? ' class="sec"' : ''}>${icon(n.icon)}<span>${esc(n.label)}</span><b class="nb" data-nb="${n.key}" hidden></b></a>`).join('');
  }
  h += `<div class="nav-group">System</div><a href="#/activity" data-r="activity">${icon('pulse')}<span>Aktivität</span><b id="busy" hidden></b></a>
    <a href="#/settings" data-r="settings">${icon('gear')}<span>Einstellungen</span></a>`;
  nav.innerHTML = h;
}
const navBadge = (key, n, live = false) => { const b = $(`[data-nb="${key}"]`); if (!b) return; b.hidden = !n; b.textContent = n || ''; b.classList.toggle('live', !!live); };

// ---------------------------------------------------------------- Router --- //
let view = null;      // { update?, navigate?, actions?, destroy? }
let routeTicket = 0;  // schützt vor langsamen Ansichten, die nach einem weiteren Klick fertig werden
const coreRoutes = [
  [/^\/?$/, 'home', homeView],
  [/^\/activity$/, 'activity', activityView],
  [/^\/settings(?:\/(\w+))?$/, 'settings', settingsView],
];
const allRoutes = () => [...coreRoutes, ...AF.order.flatMap(id => AF.modules[id].routes || [])];
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
    $$('#nav a').forEach(a => a.classList.toggle('on', a.dataset.r === nav));
    slideInd($('#navind'), $('#nav a.on'));
    main.classList.remove('enter');
    main.innerHTML = '<div class="skel" style="height:64px;max-width:420px;margin-bottom:26px"></div><div class="skel-grid">' + '<div class="skel"></div>'.repeat(3) + '</div>';
    const ticket = ++routeTicket;
    let v = null;
    try { v = await fn(main, ...m.slice(1)); } catch (e) {
      if (ticket === routeTicket) { console.error(e); main.innerHTML = `<div class="empty">${icon('alert')}<h2>Fehler</h2>${esc(e.message)}</div>`; }
    }
    if (ticket !== routeTicket) { v?.destroy?.(); return; }      // inzwischen weiternavigiert – diese Ansicht verwerfen
    view = v;
    void main.offsetWidth;  // Animation neu starten
    main.classList.add('enter');
    main.focus({ preventScroll: true });
    window.scrollTo({ top: 0 });
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
    if (st.busy || lastActive?.size) await pollActivity(); else { lastActive = new Map(); showJobPill(null); }
    await view?.update?.(st);
  } catch { /* Server kurz weg – nächster Versuch */ }
}
function sideFoot(st) {
  const s = st.modules.stacking || {}, d = s.disk;
  $('#sidefoot').innerHTML = `<div><span class="dot${s.auto ? '' : ' off'}"></span>${s.auto ? 'Automatik aktiv' : 'Automatik aus'}</div>
    <div>Letzter Scan: ${ago(s.last_scan)}</div>${d ? `<div>${fmb(d.free)} frei</div>` : ''}<div>AstroFlow ${esc(st.version)} · ${AF.order.length} Module</div>`;
}
async function pollActivity() {
  const items = await api('/activity?limit=25'), act = items.filter(running), now = new Map(act.map(a => [a.key, a]));
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
}
function showJobPill(a, n = 0) {
  const pill = $('#jobpill');
  const pc = a?.progress != null ? Math.round(a.progress * 100) : null;
  document.title = a?.state === 'running' ? `${pc != null ? pc + ' % · ' : ''}${a.title} – AstroFlow` : 'AstroFlow';
  if (!a) { pill.hidden = true; return; }
  const q = a.state === 'queued';
  pill.href = a.href || '#/activity';
  pill.title = 'Zum laufenden Auftrag';
  if (pill.hidden || !$('.t', pill)) { pill.innerHTML = '<span class="spinner"></span><span class="t"></span><i></i>'; pill.hidden = false; }
  $('.t', pill).innerHTML = `<b>${esc(a.title)}</b> · ${q ? 'wartet' : esc(a.stage || a.kind || 'läuft')}${!q && pc != null ? ` <span class="tnum">${pc} %</span>` : ''}${n > 1 ? ` <span class="mut">+${n - 1}</span>` : ''}`;
  requestAnimationFrame(() => { $('i', pill).style.width = (q || pc == null ? 0 : pc) + '%'; });
}

// --------------------------------------------------------------- Übersicht --- //
async function homeView(el) {
  crumbs([['Übersicht']]);
  const h = new Date().getHours();
  const hello = h < 5 || h >= 21 ? 'Klarer Himmel heute Nacht?' : h < 11 ? 'Guten Morgen' : h < 17 ? 'Hallo' : 'Guten Abend';
  el.innerHTML = `<section class="hero"><div><div class="eyebrow"><span></span>DEIN OBSERVATORIUM</div>
      <h1>${hello}${h < 5 || h >= 21 ? '' : '.'}</h1>
      <p class="mut">Rohbilder werden zu Astrofotos, Bildfolgen zu Entdeckungen, Objekte zu Geschichten – alles lokal auf diesem Rechner.</p></div>
      <div class="hero-act" id="heroAct"></div></section>
    <div class="home-grid stagger" id="homeGrid"></div>`;
  const acts = [];
  if (AF.on('stacking')) acts.push(`<a class="btn" href="#/import">${icon('upload')}Aufnahmen importieren</a>`);
  if (AF.on('skytrace')) acts.push(`<a class="btn ghost" href="#/skytrace/neu">${icon('orbit')}Bildfolge analysieren</a>`);
  if (AF.on('sage')) acts.push(`<a class="btn ghost" href="#/sage">${icon('book')}Bild erzählen lassen</a>`);
  $('#heroAct').innerHTML = acts.join('');
  let sig = '';
  async function load(st) {
    st = st || AF.status || await api('/status');
    const cards = await Promise.all(AF.order.map(async (id, i) => {
      try { const c = await AF.modules[id].home?.(st); return c ? `<section class="hcard" data-m="${id}" style="--i:${i}">${c}</section>` : ''; }
      catch (e) { return `<section class="hcard" style="--i:${i}"><div class="mut">${esc(AF.info(id).name)}: ${esc(e.message)}</div></section>`; }
    }));
    const html = cards.join('');
    if (html === sig || !$('#homeGrid')) return;
    const first = !sig;
    sig = html;
    $('#homeGrid').classList.toggle('stagger', first);
    $('#homeGrid').innerHTML = html;
  }
  await load();
  return { update: load };
}

// --------------------------------------------------------------- Aktivität --- //
async function activityView(el) {
  crumbs([['Aktivität']]);
  const mods = AF.order.filter(id => id !== 'x');
  let filter = 'all', sig = '';
  el.innerHTML = `<div class="pagehead"><div><h1>Aktivität</h1><p class="mut">Alle Aufträge aus allen Modulen – Verarbeitung, Spurenanalysen, Geschichten.</p></div></div>
    <div class="toolbar"><div class="seg" id="af">${[['all', 'Alle'], ...mods.map(id => [id, AF.info(id).name])].map(([k, l]) => `<button data-v="${k}" class="${k === filter ? 'on' : ''}">${esc(l)}</button>`).join('')}</div></div>
    <div id="jobs"></div>`;
  $('#af').onclick = (e) => { const b = e.target.closest('button'); if (!b) return; filter = b.dataset.v; $$('#af button').forEach(x => x.classList.toggle('on', x === b)); sig = ''; load(); };
  const label = { queued: ['', 'wartet'], running: ['acc', 'läuft'], done: ['ok', 'fertig'], error: ['bad', 'Fehler'], stopped: ['warn', 'angehalten'] };
  const ji = (j) => j.state === 'done' ? `<span class="ji ok">${icon('check')}</span>` : j.state === 'error' ? `<span class="ji bad">${icon('alert')}</span>`
    : j.state === 'running' ? '<span class="ji run"><span class="spinner"></span></span>' : `<span class="ji">${icon('pulse')}</span>`;
  const msg = (j) => j.state === 'error' ? `<span class="bad">${esc(j.detail)}</span>` : running(j) ? esc(j.detail || (j.state === 'queued' ? 'wartet' : 'läuft'))
    : `${esc(j.kind || '')}${j.detail ? ' · ' + esc(j.detail) : ''} · ${ago(j.finished || j.started)}${j.finished && j.started && j.finished - j.started > 1 ? ` · Dauer ${dur(j.finished - j.started)}` : ''}`;
  const row = (j, i) => `<a class="job ${running(j) ? 'running' : ''}" href="${j.href || '#/activity'}" data-j="${esc(j.key)}" style="--i:${i}">${ji(j)}
      <div style="min-width:0"><b style="color:var(--fg)">${esc(j.title)}</b> <span class="modtag">${esc(AF.info(j.module)?.name || j.module)}</span>
        <div class="mut sm jmsg">${msg(j)}</div></div>
      <span class="chip ${label[j.state]?.[0] || ''}">${j.state === 'running' && j.progress != null ? `${Math.round(j.progress * 100)} %` : label[j.state]?.[1] || esc(j.state)}</span>
      ${running(j) ? `<div class="progress ${j.state === 'queued' || j.progress == null ? 'indet' : ''}"><i style="width:${(j.progress || 0) * 100}%"></i></div>` : ''}</a>`;
  async function load() {
    const items = (await api('/activity?limit=60')).filter(j => filter === 'all' || j.module === filter);
    const s = JSON.stringify(items.map(j => [j.key, j.state]));
    const act = items.filter(running), past = items.filter(j => !running(j));
    if (s === sig) {
      act.forEach(j => {                          // nur Fortschritt: Balken und Texte vor Ort aktualisieren
        const r = $(`[data-j="${CSS.escape(j.key)}"]`);
        if (!r) return;
        $('.jmsg', r).innerHTML = msg(j);
        if (j.state === 'running' && j.progress != null) { $('.chip', r).textContent = Math.round(j.progress * 100) + ' %'; $('.progress i', r).style.width = j.progress * 100 + '%'; }
      });
      return;
    }
    sig = s;
    $('#jobs').innerHTML = items.length ? `${act.length ? `<h2 class="sect">Läuft</h2><div class="jobs stagger">${act.map(row).join('')}</div>` : ''}
      ${past.length ? `<h2 class="sect" ${act.length ? 'style="margin-top:28px"' : ''}>Verlauf</h2><div class="jobs stagger">${past.map(row).join('')}</div>` : ''}`
      : `<div class="empty">${icon('pulse')}<h2>Noch nichts passiert</h2>Sobald Aufnahmen verarbeitet, Bildfolgen analysiert oder Geschichten erzählt werden, erscheinen die Aufträge hier.</div>`;
  }
  await load();
  return { update: load };
}

// ------------------------------------------------------------ Einstellungen --- //
async function settingsView(el, section) {
  crumbs([['Einstellungen']]);
  const secs = [...AF.order.filter(id => AF.modules[id].settings).map(id => [id, AF.info(id).name, AF.info(id).icon]), ['module', 'Module & System', 'gear']];
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
    try { await (k === 'module' ? moduleSettings(body) : AF.modules[k].settings(body)); }
    catch (e) { body.innerHTML = `<div class="notice err"><div><b>Fehler</b><div class="mut sm">${esc(e.message)}</div></div></div>`; }
    void body.offsetWidth; body.classList.add('enter');
  };
  await show(section);
  return { navigate: (path) => { const m = path.match(/^\/settings(?:\/(\w+))?$/); if (!m) return false; if ((m[1] || secs[0][0]) !== section) show(m[1] || secs[0][0]); return true; } };
}
async function moduleSettings(el) {
  const info = await api('/app'), st = AF.status || await api('/status');
  el.innerHTML = `<div class="panel"><h2>Module</h2><p class="mut sm" style="margin:6px 0 16px">AstroFlow besteht aus Modulen. Abgeschaltete Module werden nicht geladen –
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
  const base = [['home', 'Übersicht', 'Start', go('#/')],
    ...AF.order.flatMap(id => (AF.modules[id].nav || []).map(n => [n.icon, n.label, AF.info(id).name, go(n.href)])),
    ['pulse', 'Aktivität', 'alle Aufträge', go('#/activity')], ['gear', 'Einstellungen', 'Module, Solver, KI, Standort', go('#/settings')]];
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
  extra = lists.filter(([, l]) => l.length);
  if (d.open) draw();
}

// ------------------------------------------------------------------ Start --- //
async function boot() {
  try { await loadModules(); }
  catch (e) { $('#view').innerHTML = `<div class="empty">${icon('alert')}<h2>Server nicht erreichbar</h2>${esc(e.message)}</div>`; return; }
  buildNav();
  $('#palBtn').onclick = openPalette;
  addEventListener('keydown', (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); openPalette(); }
  });
  addEventListener('resize', () => { slideInd($('#navind'), $('#nav a.on')); slideInd($('#tabind'), $('#tabs a.on'), true); });
  // Dateien überall auf die Seite ziehen: das erste Modul, das sie annimmt, bekommt sie (FITS → Bilder, Fotos → Sage)
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
  await route();
  poll();
  setInterval(() => { if (!document.hidden) poll(); }, 2500);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) poll(); });
}
boot();
