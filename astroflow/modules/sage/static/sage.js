'use strict';
/* Modul „Sage“: Bild bestimmen lassen → KI-Geschichte → anhören; Verlauf und Entdeckungen. Portiert aus starlight-sage. */
(() => {
const G = '/sage';
const STEPS = [['upload', 'Bild vorbereiten'], ['solve', 'Sternmuster bestimmen'], ['identify', 'Objekte erkennen'], ['story', 'Geschichte schreiben'], ['complete', 'Fertig']];
const CAT = { messier: 'Messier', ngc: 'NGC', ic: 'IC', other: 'Weitere' };
const tasks = new Map();          // laufende Aufträge dieser Sitzung (id -> Auftrag)

// Kleines, sicheres Markdown: erst escapen, dann Überschriften, Fett/Kursiv und Absätze
function md(text) {
  const inline = (s) => esc(s).replace(/\*\*(.+?)\*\*/g, '<b>$1</b>').replace(/(^|[^*])\*([^*\n]+?)\*/g, '$1<i>$2</i>');
  return (text || '').trim().split(/\n{2,}/).map(b => {
    const h = b.match(/^(#{1,4})\s+(.+)$/);
    if (h) return `<h${Math.min(4, h[1].length + 1)}>${inline(h[2])}</h${Math.min(4, h[1].length + 1)}>`;
    if (/^\s*[-*] /.test(b)) return `<ul>${b.split('\n').map(l => `<li>${inline(l.replace(/^\s*[-*] /, ''))}</li>`).join('')}</ul>`;
    return `<p>${inline(b).replace(/\n/g, '<br>')}</p>`;
  }).join('');
}
const when = (iso) => new Date(iso).toLocaleDateString('de-DE', { day: '2-digit', month: 'short', year: 'numeric' });

async function startUpload(file) {
  const fd = new FormData();
  fd.append('file', file, file.name);
  try {
    const t = await api(G + '/analyze', { method: 'POST', body: fd });
    tasks.set(t.id, t);
    toast(`„${file.name}“ wird bestimmt …`);
    poll();
    if (!location.hash.startsWith('#/sage')) location.hash = '#/sage';
    return t;
  } catch (e) { toast(e.message, 'err'); }
}
function stepper(t) {
  const i = Math.max(0, STEPS.findIndex(s => s[0] === t.step));
  return `<ol class="sg-steps">${STEPS.map(([k, l], j) => `<li class="${j < i || t.state === 'done' ? 'done' : j === i ? (t.state === 'error' ? 'err' : 'now') : ''}">${j < i || t.state === 'done' ? icon('check') : `<span>${j + 1}</span>`}${l}</li>`).join('')}</ol>`;
}

// ---------------------------------------------------------------- Erzählen --- //
async function mainView(el) {
  crumbs([['Sage'], ['Erzählen']]);
  const cfg = await api(G + '/config');
  const projects = AF.on('stacking') ? (await api('/stacking/projects')).filter(p => p.result) : [];
  let hist = await api(G + '/history'), sig = '';
  const ready = cfg.local_solver || (cfg.online_solve && cfg.has_nova_key);
  el.innerHTML = `<div class="pagehead"><div><h1>Was ist auf deinem Bild?</h1><p class="mut">Sage bestimmt die Himmelsobjekte und erzählt dir ihre Geschichte – zum Lesen und Anhören.</p></div>
      <span class="grow"></span><a class="btn ghost" href="#/sage/entdeckungen">${icon('medal')}Entdeckungen</a></div>
    ${!ready ? `<div class="notice">${icon('alert')}<div><b>Kein Plate-Solver bereit</b><div class="mut sm">Richte ASTAP unter Einstellungen → Bilder ein (lokal, empfohlen) oder erlaube unter Einstellungen → Sage das Online-Lösen über nova.astrometry.net.</div></div><span class="grow"></span><a class="btn sm ghost" href="#/settings/sage">Einrichten</a></div>` : ''}
    ${!cfg.has_story_api_key && cfg.story_provider !== 'lmstudio' && cfg.story_provider !== 'custom' ? `<div class="notice">${icon('book')}<div><b>Erzähl-KI noch nicht eingerichtet</b><div class="mut sm">Objekte werden trotzdem bestimmt; für die Geschichte einen OpenAI-Schlüssel eintragen oder LM Studio wählen.</div></div><span class="grow"></span><a class="btn sm ghost" href="#/settings/sage">Einrichten</a></div>` : ''}
    <div class="split sg-top"><label class="dropzone" id="sgdz">${icon('upload')}<h2>Astrofoto hierher ziehen</h2>
        <div class="mut">oder klicken · JPG, PNG, TIFF oder FITS</div><input type="file" id="sgfi" accept=".jpg,.jpeg,.png,.tif,.tiff,.gif,.fit,.fits,.fts" hidden></label>
      <div class="panel"><h2 style="margin-bottom:10px">Aus deinen Bildern</h2>
        ${projects.length ? `<div class="sg-pick">${projects.slice(0, 8).map(p => `<button data-p="${p.id}" title="Geschichte zu ${esc(p.name)} erzählen"><img src="/api/stacking/results/${p.result.id}/thumb" alt="" loading="lazy"><span>${esc(p.name)}</span></button>`).join('')}</div>`
          : '<p class="mut sm" style="margin:0">Fertige Bilder aus der Galerie erscheinen hier – ein Klick, und Sage erzählt ihre Geschichte.</p>'}</div></div>
    <div id="sgTasks"></div>
    <div class="toolbar" style="margin-top:26px"><h2 class="sect" style="margin:0">Deine Geschichten</h2><span class="grow"></span><input type="search" id="sgq" placeholder="Geschichte oder Objekt suchen …"></div>
    <div id="sgHist"></div>`;
  $('#sgfi').onchange = (e) => { const f = e.target.files[0]; if (f) startUpload(f); e.target.value = ''; };
  const dz = $('#sgdz');
  dz.ondragover = (e) => { e.preventDefault(); dz.classList.add('over'); };
  dz.ondragleave = () => dz.classList.remove('over');
  dz.ondrop = async (e) => { e.preventDefault(); e.stopPropagation(); dz.classList.remove('over'); const f = (await droppedFiles(e.dataTransfer))[0]; if (f) startUpload(f); };
  el.querySelector('.sg-pick')?.addEventListener('click', async (e) => {
    const b = e.target.closest('[data-p]');
    if (!b) return;
    b.disabled = true;
    try { const t = await send(`${G}/analyze/project/${b.dataset.p}`); tasks.set(t.id, t); poll(); drawTasks(); } catch (err) { toast(err.message, 'err'); }
    b.disabled = false;
  });
  let q = '';
  $('#sgq').oninput = (e) => { q = e.target.value.toLowerCase(); sig = ''; drawHist(); };
  function drawHist() {
    const list = hist.filter(h => (h.title + ' ' + h.names.join(' ')).toLowerCase().includes(q));
    const s = JSON.stringify(list.map(h => h.id));
    if (s === sig) return;
    sig = s;
    $('#sgHist').innerHTML = list.length ? `<div class="gallery stagger">${list.map((h, i) => `<a class="card" href="#/sage/${h.id}" style="--i:${i}">
        <div class="thumb">${h.image ? `<img src="${h.image}" alt="" loading="lazy" onload="this.classList.add('ld')">` : `<div class="ph">${icon('book')}</div>`}
          ${h.source?.type === 'project' ? `<span class="chip acc badge">${icon('grid')}Eigenes Bild</span>` : ''}</div>
        <div class="body"><h3>${esc(h.title)}</h3><div class="meta">${h.names.slice(0, 3).map(n => `<span class="chip">${esc(n)}</span>`).join('')}${h.objects > 3 ? `<span class="chip">+${h.objects - 3}</span>` : ''}</div>
          <p class="mut sm sg-prev">${esc(h.preview || (h.has_story ? '' : 'Ohne Geschichte – Objekte bestimmt.'))}</p>
          <div class="foot"><span>${h.objects} Objekt${h.objects === 1 ? '' : 'e'}</span><span>${when(h.created)}</span></div></div></a>`).join('')}</div>`
      : hist.length ? `<div class="empty">${icon('search')}<h2>Keine Treffer</h2></div>`
        : `<div class="empty">${icon('book')}<h2>Noch keine Geschichten</h2>Lade ein Astrofoto hoch oder wähle eines deiner Bilder.</div>`;
  }
  function drawTasks() {
    const list = [...tasks.values()].filter(t => t.state === 'running' || (t.finished && Date.now() / 1000 - t.finished < 120));
    $('#sgTasks').innerHTML = list.map(t => `<div class="panel sg-task ${t.state}"><div class="panel-h">${t.state === 'running' ? '<span class="spinner"></span>' : t.state === 'done' ? `<span class="ok">${icon('check')}</span>` : `<span class="bad">${icon('alert')}</span>`}
        <h2>${esc(t.title)}</h2><span class="grow"></span>${t.state === 'running' ? `<button class="linkbtn" data-x="${t.id}">Abbrechen</button>` : t.entry ? `<a class="btn sm" href="#/sage/${t.entry}">Geschichte lesen${icon('arrow')}</a>` : ''}</div>
      ${stepper(t)}<div class="progress ${t.state === 'running' ? '' : 'static'}"><i style="width:${(t.progress || 0) * 100}%"></i></div>
      <div class="sm ${t.state === 'error' ? 'bad' : 'mut'}" style="margin-top:8px">${esc(t.error || t.message)}</div>
      ${t.discovery?.new?.length ? `<div class="sg-new">${icon('sparkle')}Neu entdeckt: ${t.discovery.new.map(esc).join(', ')}</div>` : ''}</div>`).join('');
    $$('[data-x]', $('#sgTasks')).forEach(b => { b.onclick = async () => { try { tasks.set(b.dataset.x, await send(`${G}/tasks/${b.dataset.x}/cancel`)); drawTasks(); } catch { /* egal */ } }; });
  }
  // Aufträge kommen vom Server – auch die aus der Galerie gestarteten („Geschichte erzählen“)
  async function loadTasks() {
    const before = new Map([...tasks].map(([k, t]) => [k, t.state]));
    for (const n of await api(G + '/tasks')) {
      tasks.set(n.id, n);
      if (before.get(n.id) === 'running' && n.state === 'done') {
        hist = await api(G + '/history'); sig = ''; drawHist();
      }
    }
    drawTasks();
  }
  drawHist();
  await loadTasks();
  return {
    async update(st) {
      if (st.modules.sage?.busy_jobs || [...tasks.values()].some(t => t.state === 'running')) await loadTasks();
    },
    actions: () => [['upload', 'Bild hochladen', 'Sage bestimmt und erzählt', () => $('#sgfi').click()], ['medal', 'Entdeckungen', 'Sammlung & Erfolge', () => { location.hash = '#/sage/entdeckungen'; }]],
  };
}

// ---------------------------------------------------------------- Geschichte --- //
async function entryView(el, id) {
  let e = await api(`${G}/history/${id}`), viewer = null, audio = null, tts = null;
  // Sprachdienste im Hintergrund prüfen (der lokale TTS-Server antwortet evtl. erst nach einem Zeitlimit)
  api(G + '/tts/config').then(t => {
    tts = t;
    const sel = $('#ttsp');
    if (!sel) return;
    for (const o of sel.options) { const ok = t[o.value]?.available; o.disabled = !ok; o.textContent = o.textContent.replace(/ – .*$/, '') + (ok ? '' : ' – nicht verfügbar'); }
    const f = [...sel.options].find(o => !o.disabled);
    if (sel.options[sel.selectedIndex]?.disabled && f) sel.value = f.value;
    sel.dispatchEvent(new Event('change'));
  }).catch(() => {});
  crumbs([['Sage', '#/sage'], [e.title]]);
  const fresh = new Set(e.discovery?.new || []);
  function draw() {
    const objs = e.objects || [], sol = e.solution || {};
    el.innerHTML = `<div class="pagehead"><div style="min-width:0"><h1>${esc(e.title)}</h1>
        <div class="row" style="margin-top:8px"><span class="chip">${when(e.created)}</span><span class="chip">${objs.length} Objekt${objs.length === 1 ? '' : 'e'}</span>
          ${sol.solver ? `<span class="chip acc">${icon('star')}${sol.solver === 'nova' ? 'nova.astrometry.net' : 'lokal gelöst (' + esc(sol.solver.toUpperCase()) + ')'}</span>` : ''}
          ${e.source?.type === 'project' ? `<a class="chip" href="#/p/${e.source.project}">${icon('grid')}${esc(e.source.name)}</a>` : e.source?.filename ? `<span class="chip">${esc(e.source.filename)}</span>` : ''}</div></div>
      <span class="grow"></span><div class="row"><button class="btn ghost" id="retell">${icon('refresh')}${e.story ? 'Neu erzählen' : 'Geschichte schreiben'}</button>
        <div class="menu"><button class="btn ghost">Mehr${icon('chev')}</button><div class="pop"><a href="${e.image}" download>Bild herunterladen</a><hr><a href="#" class="danger" id="del">Löschen <small>mit Bild und Audio</small></a></div></div></div></div>
      <div class="split sg-entry"><div>
        <div id="sgv"></div>
        <div class="row" style="margin-top:10px"><label class="tgl"><input type="checkbox" id="labels" checked><span class="switch"></span>Beschriftung</label>
          <span class="mut sm">${sol.ra != null ? `Bildmitte ${sexa(sol.ra, true)} · ${sexa(sol.dec, false)}${sol.fov_w ? ` · Feld ${(sol.fov_w * 60).toFixed(0)}′ × ${(sol.fov_h * 60).toFixed(0)}′` : ''}` : ''}</span></div>
        <article class="panel sg-story">${e.story ? md(e.story.replace(/^\s*#{1,4}\s+.+\n+/, '')) : `<div class="empty" style="border:0">${icon('book')}<h2>Noch keine Geschichte</h2>${esc(e.story_error || 'Die Objekte sind bestimmt – die Erzähl-KI war nicht verfügbar.')}</div>`}</article></div>
      <div>${e.story ? `<div class="panel sg-player"><div class="panel-h"><h2>${icon('volume')}Vorlesen</h2><span class="grow"></span>
          <select id="ttsp">${[['openrouter', 'OpenRouter'], ['qwen', 'Qwen3 (lokal)'], ['elevenlabs', 'ElevenLabs']].map(([k, l]) => `<option value="${k}" ${(tts?.default || store.get('sage.tts', 'qwen')) === k ? 'selected' : ''} ${tts && !tts[k]?.available ? 'disabled' : ''}>${l}${tts && !tts[k]?.available ? ' – nicht verfügbar' : ''}</option>`).join('')}</select></div>
          <div id="ttsBody"></div></div>` : ''}
        <div class="panel"><div class="panel-h"><h2>Im Bild</h2><span class="grow"></span><span class="mut sm">${objs.length}</span></div>
          ${objs.length ? `<ul class="sg-objs">${objs.map((o, i) => `<li data-i="${i}">${fresh.has(o.name) ? `<span class="chip acc">${icon('sparkle')}neu</span>` : ''}<b>${esc(o.name)}</b>
            <span class="mut sm">${esc(o.kind_label || '')}${o.size ? ` · ${o.size >= 60 ? (o.size / 60).toFixed(1) + '°' : Math.round(o.size) + '′'}` : ''}</span></li>`).join('')}</ul>`
            : '<p class="mut sm" style="margin:0">Keine Katalogobjekte im Bildfeld gefunden.</p>'}</div>
        <a class="panel sg-disc" href="#/sage/entdeckungen">${icon('medal')}<div><b>Deine Entdeckungen</b><div class="mut sm">Sammlung, Fortschritt und Erfolge</div></div>${icon('arrow')}</a></div></div>`;
    const marked = objs.filter(o => o.x != null);
    viewer = createViewer($('#sgv'), e.image, null, undefined, null, marked.length ? { overlay: (svg, w, h) => drawLabels(svg, w, h, marked) } : {});
    $('#labels').onchange = (ev) => { if (viewer.overlay) viewer.overlay.style.display = ev.target.checked ? '' : 'none'; };
    $('#retell').onclick = retell;
    $('#del').onclick = async (ev) => { ev.preventDefault(); if (await confirmDlg('Geschichte löschen?', 'Bild, Geschichte und Vertonung werden entfernt; die Entdeckungen werden neu berechnet.', 'Löschen', true)) { await send(`${G}/history/${e.id}`, null, 'DELETE'); toast('Gelöscht.', 'ok'); location.hash = '#/sage'; } };
    $$('.sg-objs li').forEach(li => { li.onmouseenter = () => $$(`.vov [data-i="${li.dataset.i}"]`).forEach(n => n.classList.add('hi')); li.onmouseleave = () => $$('.vov .hi').forEach(n => n.classList.remove('hi')); });
    if (e.story) player();
  }
  // Objektmarken in Bildpixeln: Kreis in Objektgröße, Name daneben (skaliert mit dem Zoom des Betrachters)
  function drawLabels(svg, w, h, objs) {
    const W = e.width || w, sc = w / W, fs = Math.max(12, w / 70);
    const objIndex = (o) => (e.objects || []).indexOf(o);
    svg.innerHTML = objs.map(o => { const x = o.x * sc, y = o.y * sc, r = Math.max(fs * .6, (o.radius || 8) * sc);
      return `<g data-i="${objIndex(o)}" class="${fresh.has(o.name) ? 'new' : ''}"><circle cx="${x}" cy="${y}" r="${r}"/><text x="${x + r * .75 + fs * .3}" y="${y - r * .75}" font-size="${fs}">${esc(o.name)}</text></g>`; }).join('');
  }
  function player() {
    const body = $('#ttsBody'), sel = $('#ttsp');
    const show = () => {
      const p = sel.value, have = e.audio?.[p];
      if (!have && tts && !tts[p]?.available) {
        body.innerHTML = `<p class="mut sm" style="margin:0">Dieser Sprachdienst ist noch nicht eingerichtet (Schlüssel bzw. lokaler Server) – <a href="#/settings/sage">Einstellungen → Sage</a>.</p>`;
        return;
      }
      const cur = tts?.[p]?.model ? `${tts[p].model}${tts[p].voice ? ' · ' + tts[p].voice : ''}` : '';
      const stale = have && cur && p === 'openrouter' && e.audio_variant?.[p] && e.audio_variant[p] !== `${tts[p].model}|${tts[p].voice}`;
      body.innerHTML = have ? `<audio controls src="${have}" preload="none"></audio>
          <div class="row sm" style="margin-top:6px"><span class="mut">${esc((e.audio_variant?.[p] || '').replace('|', ' · '))}</span><span class="grow"></span>
          <button class="linkbtn" id="redo">${stale ? 'Mit neuer Stimme vertonen' : 'Neu vertonen'}</button></div>`
        : `<button class="btn" id="speak">${icon('play')}Vorlesen lassen</button><span class="mut sm" style="margin-left:8px">${p === 'qwen' ? 'lokal, ohne Upload'
          : p === 'openrouter' ? 'über OpenRouter' + (cur ? ' · ' + esc(cur) : '') : 'Text wird an ElevenLabs gesendet'}</span>`;
      $('#speak')?.addEventListener('click', () => speak(false));
      $('#redo')?.addEventListener('click', () => { body.innerHTML = `<button class="btn" id="speak"></button>`; speak(true); });
    };
    async function speak(force) {
      const b = $('#speak');
      b.disabled = true; b.classList.add('busy'); b.innerHTML = `<span class="spinner"></span>Stimme wird erzeugt …`;
      try {
        const r = await send(G + '/tts/speak', { history_id: e.id, provider: sel.value, force });
        e.audio = { ...(e.audio || {}), [sel.value]: r.url };
        e.audio_variant = { ...(e.audio_variant || {}), [sel.value]: r.variant };
        show();
        audio = $('audio', body); audio?.play().catch(() => {});
      } catch (err) { toast(err.message, 'err'); show(); }
    }
    sel.onchange = () => { store.set('sage.tts', sel.value); show(); };
    show();
  }
  async function retell() {
    const b = $('#retell');
    b.disabled = true; b.classList.add('busy');
    try { e = await send(`${G}/history/${e.id}/story`); toast('Neue Geschichte geschrieben.', 'ok'); viewer?.destroy(); draw(); crumbs([['Sage', '#/sage'], [e.title]]); }
    catch (err) { toast(err.message, 'err'); b.disabled = false; b.classList.remove('busy'); }
  }
  draw();
  if (!e.seen) send(`${G}/history/${e.id}`, { seen: true }, 'PATCH').catch(() => {});
  return {
    actions: () => [['refresh', 'Neu erzählen', 'KI schreibt die Geschichte neu', retell], ...(e.story ? [['volume', 'Vorlesen', 'Geschichte anhören', () => $('#speak')?.click()]] : [])],
    destroy: () => { viewer?.destroy(); audio?.pause(); },
  };
}

// ------------------------------------------------------------ Entdeckungen --- //
async function discView(el) {
  crumbs([['Sage', '#/sage'], ['Entdeckungen']]);
  const d = await api(G + '/discoveries'), s = d.stats;
  let cat = 'all';
  const unlocked = d.achievements.filter(a => a.unlocked).length;
  el.innerHTML = `<div class="pagehead"><div><h1>Deine Entdeckungen</h1><p class="mut">Jedes bestimmte Objekt wandert in deine Sammlung – aus hochgeladenen Fotos und deinen eigenen Bildern.</p></div></div>
    <div class="stats sg-kpi">
      <div class="stat"><small>Objekte</small><b class="tnum">${s.total_objects}</b></div><div class="stat"><small>Analysen</small><b class="tnum">${s.analyses}</b></div>
      <div class="stat"><small>Messier</small><b class="tnum">${s.messier} <span class="dim sm">/ 110</span></b></div><div class="stat"><small>Erfolge</small><b class="tnum">${unlocked} <span class="dim sm">/ ${d.achievements.length}</span></b></div></div>
    <div class="panel sg-mile"><div class="row"><b>${d.next_milestone ? `Nächstes Ziel: ${d.next_milestone} Objekte` : 'Alle Meilensteine erreicht'}</b><span class="grow"></span><span class="mut sm tnum">${s.total_objects}${d.next_milestone ? ' / ' + d.next_milestone : ''}</span></div>
      <div class="progress static" style="margin-top:8px"><i style="width:${d.milestone_progress}%"></i></div>
      <div class="sg-messier" title="Messier-Katalog: ${s.messier} von 110">${Array.from({ length: 110 }, (_, i) => `<i class="${d.objects.some(o => o.category === 'messier' && new RegExp(`^m ?${i + 1}\\b`, 'i').test(o.name)) ? 'on' : ''}" title="M${i + 1}"></i>`).join('')}</div></div>
    <h2 class="sect">Erfolge</h2>
    <div class="sg-ach stagger">${d.achievements.map((a, i) => `<div class="ach ${a.unlocked ? 'on' : ''} ${a.unlocked && Date.now() - Date.parse(a.unlocked) < 3 * 864e5 ? 'fresh' : ''}" style="--i:${i}"><span>${icon(a.icon)}</span><div><b>${esc(a.title)}</b><small class="mut">${esc(a.description)}</small>
      ${a.unlocked ? `<small class="ok">${when(a.unlocked)}</small>` : ''}</div></div>`).join('')}</div>
    <div class="toolbar" style="margin-top:26px"><h2 class="sect" style="margin:0">Sammlung</h2><span class="grow"></span><div class="seg" id="dc"></div></div>
    <div id="dgrid"></div>`;
  const cats = ['all', ...Object.keys(CAT).filter(k => d.objects.some(o => o.category === k))];
  const draw = () => {
    $('#dc').innerHTML = cats.map(k => `<button data-k="${k}" class="${k === cat ? 'on' : ''}">${k === 'all' ? 'Alle' : CAT[k]} <span class="dim">${k === 'all' ? d.objects.length : d.objects.filter(o => o.category === k).length}</span></button>`).join('');
    const list = d.objects.filter(o => cat === 'all' || o.category === cat);
    $('#dgrid').innerHTML = list.length ? `<div class="sg-coll stagger">${list.map((o, i) => `<a href="#/sage/${o.first_entry}" style="--i:${i}">${o.image ? `<img src="${o.image}" alt="" loading="lazy">` : `<span class="ph">${icon('star')}</span>`}
      <b>${esc(o.name)}</b><small class="mut">${esc(o.kind_label || CAT[o.category])}${o.seen > 1 ? ` · ${o.seen}× gesehen` : ''}</small><small class="dim">seit ${when(o.first_seen)}</small></a>`).join('')}</div>`
      : `<div class="empty">${icon('star')}<h2>Noch leer</h2>Deine erste Analyse füllt die Sammlung.</div>`;
  };
  $('#dc').onclick = (e) => { const b = e.target.closest('[data-k]'); if (b) { cat = b.dataset.k; draw(); } };
  draw();
}

// ------------------------------------------------------------ Einstellungen --- //
async function settings(el) {
  const c = await api(G + '/config');
  const sec = (k) => c['has_' + k] ? '•••••• (gesetzt – leer lassen zum Behalten)' : '';
  el.innerHTML = `<div class="split"><div>
    <form class="panel" id="sgs1"><h2>Erzähl-KI</h2><p class="mut sm" style="margin:6px 0 14px">Schreibt aus den bestimmten Objekten eine Geschichte. Mit <b>LM Studio</b> bleibt alles lokal;
        bei OpenAI/OpenRouter gehen nur Objektnamen und Koordinaten an den Dienst, nie das Bild.</p>
      <label class="field"><span>Anbieter</span><select name="story_provider"><option value="openai">OpenAI</option><option value="lmstudio">LM Studio (lokal)</option><option value="openrouter">OpenRouter</option><option value="custom">Eigener OpenAI-kompatibler Server</option></select></label>
      <label class="field"><span>Server-URL</span><input name="story_base_url" value="${esc(c.story_base_url)}" placeholder="leer = Standard des Anbieters"></label>
      <label class="field"><span>API-Schlüssel</span><input name="story_api_key" type="password" autocomplete="off" placeholder="${sec('story_api_key') || 'für OpenAI/OpenRouter'}"></label>
      <div class="field"><span>Modell</span><div id="pStory"></div><small>Liste vom gewählten Anbieter – bei OpenAI erst nach dem Speichern des Schlüssels.</small></div>
      <div class="row"><button class="btn" type="submit">Speichern</button></div></form>
    <form class="panel" id="sgs2"><h2>Bildbestimmung</h2><p class="mut sm" style="margin:6px 0 14px">Zuerst wird lokal gelöst (ASTAP aus Einstellungen → Bilder):
        <b class="${c.local_solver ? 'ok' : 'warn'}">${c.local_solver ? 'bereit' : 'nicht eingerichtet'}</b>. Optional springt nova.astrometry.net ein – dabei wird das Bild hochgeladen (nicht öffentlich).</p>
      <label class="modrow"><div><b>Online-Lösen erlauben</b><div class="mut sm">nur wenn lokal nicht möglich</div></div><input type="checkbox" name="online_solve" ${c.online_solve ? 'checked' : ''}><span class="switch"></span></label>
      <label class="field"><span>nova.astrometry.net API-Key</span><input name="nova_key" type="password" autocomplete="off" placeholder="${sec('nova_key') || 'nova.astrometry.net → Profil → API-Key'}"></label>
      <div class="row"><button class="btn" type="submit">Speichern</button></div></form>
    <div class="panel" id="sgAuto"><h2 style="margin-bottom:10px">Nach dem Stacken</h2>
      <label class="modrow"><div><b>Fertige Bilder automatisch eintragen</b><div class="mut sm">Jedes neu gestackte Bild wird bestimmt und landet mit seinen Objekten in den Entdeckungen</div></div><input type="checkbox" name="auto_discover" ${c.auto_discover !== false ? 'checked' : ''}><span class="switch"></span></label>
      <label class="modrow"><div><b>Dabei eine Geschichte schreiben</b><div class="mut sm">nutzt die Erzähl-KI (bei Cloud-Anbietern kleine Kosten je Bild)</div></div><input type="checkbox" name="auto_story" ${c.auto_story !== false ? 'checked' : ''}><span class="switch"></span></label></div></div>
    <div><form class="panel" id="sgs3"><div class="panel-h"><h2>Vorlesen</h2><span class="grow"></span><span class="mut sm" id="ttsState">prüfe …</span></div>
      <label class="field"><span>Standard-Sprachdienst</span><select name="tts_provider"><option value="openrouter">OpenRouter (ElevenLabs v4 u. a.)</option>
        <option value="qwen">Qwen3-TTS (lokal)</option><option value="elevenlabs">ElevenLabs (direkt)</option></select></label>
      <fieldset class="sg-fs" data-p="openrouter"><legend>OpenRouter · Sprachmodelle vieler Anbieter</legend>
        <label class="field"><span>API-Schlüssel</span><input name="openrouter_key" type="password" autocomplete="off"
          placeholder="${c.has_openrouter_key ? sec('openrouter_key') : c.openrouter_speech_key ? 'leer = Schlüssel der Erzähl-KI (OpenRouter)' : 'openrouter.ai → Keys'}"></label>
        <div class="field"><span>Modell</span><div id="pOrModel"></div></div>
        <div class="field"><span>Stimme</span><div id="pOrVoice"></div><small>Der Text wird an OpenRouter und den Modellanbieter gesendet; abgerechnet wird je Zeichen.</small></div></fieldset>
      <fieldset class="sg-fs" data-p="qwen"><legend>Qwen3-TTS · lokaler OpenAI-kompatibler Sprachserver</legend>
        <label class="field"><span>Server-URL</span><input name="qwen_base_url" value="${esc(c.qwen_base_url)}"></label>
        <div class="row" style="flex-wrap:nowrap;gap:12px;align-items:start"><div class="field" style="flex:1"><span>Stimme</span><div id="pQwenVoice"></div></div>
          <label class="field" style="flex:1"><span>Sprache</span><input name="qwen_language" value="${esc(c.qwen_language)}"></label></div>
        <label class="field"><span>Modell</span><input name="qwen_model" value="${esc(c.qwen_model)}"></label></fieldset>
      <fieldset class="sg-fs" data-p="elevenlabs"><legend>ElevenLabs · direkt mit eigenem Konto</legend>
        <label class="field"><span>API-Schlüssel</span><input name="elevenlabs_key" type="password" autocomplete="off" placeholder="${sec('elevenlabs_key') || 'elevenlabs.io → Profil'}"></label>
        <div class="row" style="flex-wrap:nowrap;gap:12px;align-items:start"><div class="field" style="flex:1"><span>Stimme</span><div id="pElVoice"></div></div>
          <div class="field" style="flex:1"><span>Modell</span><div id="pElModel"></div></div></div></fieldset>
      <div class="row"><button class="btn" type="submit">Speichern</button><button class="btn ghost" type="button" id="probe">${icon('volume')}Speichern &amp; Probe hören</button>
        <audio id="probeAudio" hidden></audio></div></form></div></div>`;
  const f1 = $('#sgs1'), f3 = $('#sgs3');
  f1.elements.story_provider.value = c.story_provider;
  f3.elements.tts_provider.value = c.tts_provider;
  const fail = (e) => { throw e; };

  // Auswahlfelder: Modelle und Stimmen kommen live vom jeweiligen Dienst
  const story = picker($('#pStory'), { name: 'story_model', value: c.story_model, placeholder: 'Modell wählen …',
    load: () => api(`${G}/models?kind=story&provider=${encodeURIComponent(f1.elements.story_provider.value)}&base_url=${encodeURIComponent(f1.elements.story_base_url.value)}`) });
  f1.elements.story_provider.addEventListener('change', () => story.reload());
  f1.elements.story_base_url.addEventListener('change', () => story.reload());

  let speech = null;                                     // OpenRouter-Sprachmodelle samt Stimmen
  const speechModels = async () => speech || (speech = await api(`${G}/models?kind=speech`).catch(fail));
  const orModel = picker($('#pOrModel'), { name: 'openrouter_tts_model', value: c.openrouter_tts_model, load: speechModels });
  const orVoice = picker($('#pOrVoice'), { name: 'openrouter_tts_voice', value: c.openrouter_tts_voice, empty: 'Standardstimme des Modells',
    load: async () => ((await speechModels()).find(m => m.id === f3.elements.openrouter_tts_model.value)?.voices || []).map(v => ({ id: v, name: v })) });
  f3.elements.openrouter_tts_model.addEventListener('change', async () => {
    orVoice.reload();
    const voices = (await speechModels()).find(m => m.id === f3.elements.openrouter_tts_model.value)?.voices || [];
    if (!voices.includes(f3.elements.openrouter_tts_voice.value)) orVoice.set(voices[0] || '', false);
  });
  picker($('#pQwenVoice'), { name: 'qwen_voice', value: c.qwen_voice, load: () => api(`${G}/tts/voices?provider=qwen`) });
  picker($('#pElVoice'), { name: 'elevenlabs_voice', value: c.elevenlabs_voice, empty: 'Automatisch (erste eigene Stimme)',
    load: async () => (await api(`${G}/tts/voices?provider=elevenlabs`)).map(v => ({ id: v.id, name: v.name, info: (v.recommended ? '★ ' : '') + (v.category || '') })) });
  picker($('#pElModel'), { name: 'elevenlabs_model', value: c.elevenlabs_model, load: () => api(`${G}/models?kind=elevenlabs`) });
  orModel.preload();
  const markProvider = () => $$('.sg-fs', f3).forEach(fs => fs.classList.toggle('active', fs.dataset.p === f3.elements.tts_provider.value));
  f3.elements.tts_provider.addEventListener('change', markProvider);
  markProvider();

  const save = async (form, quiet) => {
    const body = {};
    for (const x of form.elements) if (x.name) body[x.name] = x.type === 'checkbox' ? x.checked : x.value;
    await send(G + '/config', body);
    for (const x of form.elements) if (x.type === 'password' && x.value) { x.value = ''; x.placeholder = '•••••• (gesetzt – leer lassen zum Behalten)'; }
    if (!quiet) toast('Gespeichert.', 'ok');
  };
  $('#sgAuto').onchange = async (e) => {
    try { await send(G + '/config', { [e.target.name]: e.target.checked }); toast('Gespeichert.', 'ok'); }
    catch (err) { toast(err.message, 'err'); e.target.checked = !e.target.checked; }
  };
  for (const s of ['#sgs1', '#sgs2', '#sgs3']) $(s).onsubmit = async (e) => { e.preventDefault(); try { await save(e.target); } catch (err) { toast(err.message, 'err'); } };
  $('#probe').onclick = async () => {
    const b = $('#probe');
    b.disabled = true; b.classList.add('busy');
    try {
      await save(f3, true);
      const r = await fetch('/api' + G + '/tts/speak', { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ provider: f3.elements.tts_provider.value, text: 'Hallo! So klingt die Stimme für deine Himmelsgeschichten. Der Orionnebel ist rund 1350 Lichtjahre entfernt.' }) });
      if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || 'Probe fehlgeschlagen.');
      const a = $('#probeAudio');
      a.src = URL.createObjectURL(await r.blob());
      a.hidden = false; a.controls = true;
      a.play().catch(() => {});
    } catch (err) { toast(err.message, 'err'); }
    b.disabled = false; b.classList.remove('busy');
  };
  api(G + '/tts/config').then(t => {
    const s = (ok, a, b) => `<b class="${ok ? 'ok' : 'dim'}">${ok ? a : b}</b>`;
    $('#ttsState').innerHTML = `OpenRouter ${s(t.openrouter.available, 'bereit', 'ohne Schlüssel')} · Qwen3 ${s(t.qwen.available, 'erreichbar', 'nicht erreichbar')} · ElevenLabs ${s(t.elevenlabs.available, 'bereit', 'ohne Schlüssel')}`;
  }).catch(() => {});
}

// --------------------------------------------------------- Erfolge feiern --- //
// Neue Objekte und Erfolge – auch aus automatischen Analysen nach dem Stacken – kommen mit dem Status-Polling
// (`celebrate`) und gleiten wie Steam-Erfolge unten rechts herein. Nach einigen Sekunden verschwinden sie und gelten
// als gesehen; mit der Maus darüber bleiben sie stehen, ein Klick öffnet die Entdeckungen bzw. die Geschichte.
const celShown = new Set();
function celebrate(list) {
  const fresh = (list || []).filter(c => !celShown.has(c.id));
  if (!fresh.length) return;
  let host = document.getElementById('sgCel');
  if (!host) { host = document.createElement('div'); host.id = 'sgCel'; host.className = 'sg-cel'; host.setAttribute('aria-live', 'polite');
    const toasts = document.getElementById('toasts');                     // gleiche Ecke wie die Hinweise, ohne Überlappung
    if (toasts) toasts.prepend(host); else document.body.append(host); }
  fresh.forEach((c, n) => {
    celShown.add(c.id);
    const ach = c.achievements || [], gold = ach.length > 0, ms = 7000;
    const card = document.createElement('a');
    card.className = 'sg-cel-card' + (gold ? ' gold' : '');
    card.href = `#/sage/${gold ? 'entdeckungen' : c.entry}`;
    card.style.cssText = `--t:${ms}ms;animation-delay:${n * 350}ms`;
    card.innerHTML = `<div class="burst">${Array.from({ length: 14 }, (_, i) => `<i style="--a:${i * 360 / 14}deg;--d:${i % 4}"></i>`).join('')}</div>
      <div class="medal">${icon(ach[0]?.icon || 'sparkle')}</div>
      <div class="txt"><small>${gold ? 'Erfolg freigeschaltet!' : 'Neue Entdeckung!'}${c.auto ? ' · nach dem Stacken' : ''}</small>
        <b>${esc(gold ? ach.map(a => a.title).join(' · ') : c.new.join(', '))}</b>
        <span class="mut sm">${esc(gold ? ach[0].description : c.title)}</span></div>
      <button type="button" class="x" title="Schließen" aria-label="Schließen">×</button><i class="bar"></i>`;
    let done = false;
    const close = () => {
      if (done) return; done = true;
      card.classList.add('out'); setTimeout(() => card.remove(), 420);
      send(G + '/celebrations/ack', { ids: [c.id] }).catch(() => {});
    };
    card.addEventListener('click', (e) => { if (e.target.closest('.x')) e.preventDefault(); close(); });
    card.querySelector('.bar').addEventListener('animationend', close);     // Zeitbalken abgelaufen (pausiert beim Überfahren)
    host.append(card);
    while (host.children.length > 4) host.firstElementChild.remove();
  });
}

// ---------------------------------------------------------- Registrierung --- //
AF.register('sage', {
  nav: [{ href: '#/sage', label: 'Erzählen', icon: 'book', key: 'sage' }, { href: '#/sage/entdeckungen', label: 'Entdeckungen', icon: 'medal', key: 'sage-disc', sec: true }],
  routes: [
    [/^\/sage$/, 'sage', mainView],
    [/^\/sage\/entdeckungen$/, 'sage-disc', discView],
    [/^\/sage\/([\w-]+)$/, 'sage', entryView],
  ],
  settings,
  status: (st, badge) => { badge('sage', st.modules.sage?.busy_jobs, true); celebrate(st.modules.sage?.celebrate); },
  async home(st) {
    const s = st.modules.sage || {}, [hist, d] = await Promise.all([api(G + '/history'), api(G + '/discoveries')]);
    const last = hist[0], next = d.achievements.find(a => !a.unlocked);
    return `<div class="hhead">${icon('book')}<div><h2>Sage</h2><div class="mut sm">Objekte bestimmen · Geschichten hören</div></div><span class="grow"></span><a class="linkbtn" href="#/sage">Erzählen ${icon('arrow')}</a></div>
      <div class="hstats"><div><b class="tnum">${d.stats.total_objects}</b><small>Objekte entdeckt</small></div><div><b class="tnum">${hist.length}</b><small>Geschichten</small></div>
        <div><b class="tnum">${d.achievements.filter(a => a.unlocked).length}/${d.achievements.length}</b><small>Erfolge</small></div></div>
      ${s.busy_jobs ? `<div class="hlist"><a href="#/sage"><span class="spinner"></span><b>Sage arbeitet</b><span class="mut sm">${s.busy_jobs} Auftrag${s.busy_jobs > 1 ? 'e' : ''}</span></a></div>` : ''}
      ${last ? `<a class="hfeature" href="#/sage/${last.id}">${last.image ? `<img src="${last.image}" alt="" loading="lazy">` : ''}<div><small class="mut">Zuletzt erzählt</small><b>${esc(last.title)}</b>
          <span class="mut sm">${esc(last.preview.slice(0, 90))}${last.preview.length > 90 ? '…' : ''}</span></div></a>`
        : `<p class="mut sm">Lade ein Astrofoto hoch – Sage erzählt dir, was darauf zu sehen ist.</p>`}
      ${next ? `<div class="hnext">${icon(next.icon)}<span><b>Nächster Erfolg:</b> ${esc(next.title)} <span class="mut">– ${esc(next.description)}</span></span></div>` : ''}`;
  },
  async palette() {
    const hist = await api(G + '/history');
    return [['upload', 'Bild erzählen lassen', 'Sage', '#/sage'], ...hist.map(h => ['book', h.title, `${h.objects} Objekte · ${when(h.created)}`, `#/sage/${h.id}`, h.image])];
  },
  drop(files) {
    const pic = files.find(f => /\.(jpe?g|png|tiff?|gif)$/i.test(f.name));
    if (!pic) return false;
    startUpload(pic);
    return true;
  },
});
})();
