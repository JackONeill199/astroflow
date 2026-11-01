'use strict';
/* Modul „Bilder“ (Stacking): Galerie, Objektansicht, Import und Einstellungen. Helfer kommen aus der Shell (app.js). */

const BAND_COLOR = { L: '#d6dbe8', R: '#f87171', G: '#4ade80', B: '#60a5fa', Ha: '#fb7185', OIII: '#22d3ee', SII: '#f59e0b', RGB: '#a78bfa' };
const PALETTE_LABEL = { auto: 'Automatisch', RGB: 'RGB', LRGB: 'LRGB', SHO: 'SHO (Hubble)', HOO: 'HOO (Bicolor)', mono: 'Mono', OSC: 'Farbkamera' };
const TABS = [['bild', 'Bild'], ['aufnahmen', 'Aufnahmen'], ['versionen', 'Versionen'], ['optionen', 'Optionen']];
const GX_LABEL = { default: 'Wie in den Einstellungen', all: 'Hintergrund + Entrauschen', bge: 'Nur Hintergrund', denoise: 'Nur Entrauschen', off: 'Aus (eigene Verfahren)' };
const PROFILE_LABEL = { auto: 'Automatisch (nach Objekttyp)', generic: 'Allgemein', galaxy: 'Galaxie', emission: 'Emissionsnebel', reflection: 'Reflexionsnebel', planetary: 'Planetarischer Nebel', snr: 'Supernova-Überrest', globular: 'Kugelsternhaufen', open_cluster: 'Offener Sternhaufen' };
const sst = () => AF.status?.modules?.stacking || {};
const SOURCE_LABEL = { solve: 'Plate-Solving', catalog: 'Katalog (Objektname)', header: 'FITS-Header' };
const IMPORT_FILE = /\.(fits?|fts|jpe?g|png|tiff?)$/i;
// Aufnahmeordner (mehr als 10 TIFF/FITS) bringen oft je Frame eine JPG/PNG-Vorschau mit – die nicht importieren (Server prüft ebenso)
const PREVIEW_FILE = /\.(jpe?g|png)$/i, SOURCE_FILE = /\.(tiff?|fits?|fts)$/i, FITS_FILE = /\.(fits?|fts)$/i, SOURCE_LIMIT = 10;
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
  ['saturation', 'Farbigkeit', v => v.toFixed(2)], ['color_gradient', 'Farbverlauf', v => v.toFixed(3)]];

function qscoreHead(q, title, sub) {
  const cls = q.score >= 8.5 ? 'ok' : q.score >= 6.5 ? 'warn' : 'bad';
  return `<div class="panel-h" style="margin-bottom:12px"><span class="qscore ${cls}" style="--p:${q.score * 10}" title="${q.score.toFixed(1)} von 10"><b class="tnum">${q.score.toFixed(1)}</b></span>
      <div><h2>${title}</h2><div class="mut sm">${sub}</div></div></div>`;
}
const qlabel = (q) => q.score >= 8.5 ? 'Messwerte weitgehend im Zielbereich' : q.score >= 6.5 ? 'Einige Messwerte auffällig' : 'Mehrere Messwerte auffällig';

function qcDetails(s, q) {
  const auto = (s.qc || []).flatMap(h => h.round === 0 ? (h.adapt || [])
    : Object.keys(h.adjust || {}).length ? [`Nachgeregelt (Runde ${h.round}): ${Object.entries(h.adjust).map(([k, v]) => `${k} → ${v}`).join(', ')}`] : []);
  const m = q.metrics || {};
  return `<details class="qmore"><summary class="sm mut">Messwerte und automatische Anpassungen</summary>
      <dl class="kv" style="margin-top:8px">${QC_METRIC.filter(([k]) => m[k] != null).map(([k, l, f]) => `<dt>${l}</dt><dd class="tnum">${f(m[k])}</dd>`).join('')}</dl>
      ${q.rechecked ? '<p class="mut sm" style="margin:8px 0 0">Bewertung am gespeicherten Bild mit der aktuellen Prüfung nachgeholt.</p>' : ''}
      ${auto.length ? `<ul class="qlist mut" style="margin-top:8px">${auto.map(a => `<li>${esc(a)}</li>`).join('')}</ul>` : ''}</details>`;
}

function qualityPanel(s) {
  const q = s.quality;
  if (!q) return '';
  const snr = q.measure?.snr;
  return `<div class="panel">${qscoreHead(q, 'Technische Bildprüfung', `${qlabel(q)}${snr != null ? ` · Objekt-SNR ${Math.round(snr)}` : ''}`)}
    <p class="mut sm">Der Wert prüft technische Grenzwerte, nicht die gesamte Bildqualität. Fokus, Nachführung und großflächige Muster werden damit nicht vollständig bewertet.</p>
    ${snr != null && snr < 8 ? '<p class="warn sm">Wenig Nutzsignal: Entrauschen kann Flecken glätten, aber keine fehlenden Details zurückholen. Mehr gute Aufnahmen verbessern die Grundlage.</p>' : ''}
    ${q.issues?.length ? `<ul class="qlist">${q.issues.map(i => `<li class="warn">${esc(i.text)}</li>`).join('')}</ul>` : '<p class="ok sm" style="margin:0 0 10px">Keine messbaren Probleme gefunden.</p>'}
    ${q.hints?.length ? `<div class="qhints"><small class="mut">Verbesserungsmöglichkeiten</small><ul>${q.hints.map(h => `<li>${esc(h)}</li>`).join('')}</ul></div>` : ''}
    ${qcDetails(s, q)}</div>`;
}

// ------------------------------------------------------------- Bild-Tutor --- //
// Bericht vom Server (Bildprüfung + Aufnahmenacht + Gerät), je Ergebnis zwischengespeichert; bis er da ist, steht qualityPanel.
const tutorCache = new Map();
const utcTime = (iso) => new Date(iso).toLocaleTimeString('de-DE', { hour: '2-digit', minute: '2-digit', timeZone: 'UTC' });
const pct = (x) => Math.round(x * 100) + ' %';

function nightChips(n, dev = {}) {
  const c = [];
  if (n.sun) c.push(n.sun.dark >= 0.9 ? ['ok', 'astronomisch dunkel'] : [n.sun.nautical > 0.3 ? 'bad' : 'warn',
    `Dämmerung · Sonne nur ${Math.abs(n.sun.alt_min).toFixed(0)}° unter dem Horizont`]);
  const m = n.moon;
  c.push([m.illum < 0.3 || m.up === 0 ? 'ok' : m.illum > 0.7 && (m.up ?? 1) > 0.5 ? 'bad' : 'warn',
    `Mond ${pct(m.illum)}${m.up != null ? (m.up ? ` · ${pct(m.up)} der Zeit am Himmel` : ' · unter dem Horizont') : ''}${m.sep != null ? ` · ${m.sep.toFixed(0)}° entfernt` : ''}`]);
  if (n.target) c.push([n.target.alt_med < 25 ? 'warn' : '', `Objekthöhe ${n.target.alt_min.toFixed(0)}–${n.target.alt_max.toFixed(0)}°`]);
  const w = n.weather;
  if (w) c.push([w.cloud > 50 ? 'bad' : w.cloud > 20 ? 'warn' : 'ok', `Wolken ${w.cloud} %`],
    [w.dew_spread < 2 && !dev.dew_heater ? 'warn' : '', `Luftfeuchte bis ${w.humidity} % · Taupunktabstand ${String(w.dew_spread).replace('.', ',')} °C`],
    ['', `Wind ${w.wind} km/h, Böen ${w.gust} km/h`], [w.temp_min < 0 ? 'warn' : '', `Tiefst ${String(w.temp_min).replace('.', ',')} °C`]);
  const tr = n.frames_trend || {};
  if (tr.sensor_temp != null) c.push(['', `Sensor ${String(tr.sensor_temp).replace('.', ',')} °C`]);
  return c.map(([k, t]) => `<span class="${k && 'lvl-' + k}">${esc(t)}</span>`).join('');
}

function tutorHtml(t, s) {
  const q = t.quality, beginner = t.mode === 'einsteiger', snr = q.measure?.snr;
  const dev = t.device || {};
  const finds = t.findings.map(f => `<details class="tfind lvl-${f.level}" ${beginner && (f.level === 'bad' || f.level === 'warn') ? 'open' : ''}>
      <summary><b>${esc(f.title)}</b></summary><p>${esc(f.text)}</p>
      ${f.tips.length ? `${beginner ? '<small class="mut tips-h">So geht es beim nächsten Mal besser</small>' : ''}<ul>${f.tips.map(x => `<li>${esc(x)}</li>`).join('')}</ul>` : ''}</details>`).join('');
  const nights = t.nights.slice(-4).map(n => `<div class="tnight"><div class="tnight-h"><b>${fnight(n.night)}</b>
      <span class="mut sm">${utcTime(n.start)}–${utcTime(n.end)} UTC · ${n.frames} Aufnahmen${n.rejected ? `, ${n.rejected} aussortiert` : ''}</span></div>
      <div class="tchips">${nightChips(n, dev)}</div></div>`).join('');
  const ki = t.ki;
  return `<div class="panel">${qscoreHead(q, beginner ? 'Bild-Tutor' : 'Technische Bildprüfung',
      `${qlabel(q)}${snr != null ? ` · Objekt-SNR ${Math.round(snr)}` : ''}${dev.name ? ` · ${esc(dev.name)}${dev.guessed ? ' (am Bildformat erkannt)' : ''}` : ''}`)}
    ${beginner ? `<p class="mut sm tutor-intro">Ich habe dein Bild und die Aufnahme${t.nights.length > 1 ? 'nächte' : 'nacht'} geprüft – das ist mir aufgefallen. Klick auf einen Punkt für Erklärung und Tipps.</p>` : ''}
    <div class="tfinds">${finds}</div>
    ${t.nights.length ? `<small class="mut tsec">${t.nights.length > 1 ? 'Aufnahmenächte' : 'Aufnahmenacht'}</small><div class="tnights">${nights}</div>` : ''}
    ${!t.site ? `<p class="mut sm">Für Dämmerung, Mondhöhe und Wetter fehlt dein Standort – <a href="#/settings/stacking">in den Einstellungen eintragen</a>.</p>`
      : !t.weather ? `<p class="mut sm">Wetterdaten (Wolken, Tau, Wind, Frost) lassen sich <a href="#/settings/stacking">in den Einstellungen</a> einschalten.</p>` : ''}
    ${t.weather_error ? `<p class="warn sm">${esc(t.weather_error)}</p>` : ''}
    ${ki ? `<details class="tki"><summary><b class="sm">KI-Einschätzung</b>${ki.summary ? `<span class="sm"> · ${esc(ki.summary)}</span>` : ''}</summary>
        ${ki.seen?.length ? `<small class="mut tips-h">Gesehen</small><ul>${ki.seen.map(x => `<li>${esc(x)}</li>`).join('')}</ul>` : ''}
        ${ki.tips?.length ? `<small class="mut tips-h">Tipps</small><ul>${ki.tips.map(x => `<li>${esc(x)}</li>`).join('')}</ul>` : ''}
        <div class="dim sm" style="margin-top:6px">${esc(ki.model || '')}</div></details>` : ''}
    <div class="row" style="margin-bottom:10px">${t.llm ? `<button class="btn sm ghost" id="tutorKi">${icon('sparkle')}${ki ? 'KI erneut fragen' : 'KI-Tutor fragen'}</button>`
      : `<span class="mut sm">KI-Einschätzung: <a href="#/settings/stacking">Anbieter in den Einstellungen wählen</a></span>`}
      <span class="grow"></span><button class="linkbtn sm" id="tutorRefresh">Neu prüfen</button></div>
    ${qcDetails(s, q)}</div>`;
}

async function tutorPanel(box, r) {
  const s = r.stats || {};
  const draw = (t) => {
    if (!box.isConnected || !t.available) return;
    box.innerHTML = tutorHtml(t, s);
    $('#tutorKi', box)?.addEventListener('click', async (e) => {
      const b = e.currentTarget;
      b.disabled = true;
      b.innerHTML = '<span class="spinner"></span>KI sieht sich das Bild an …';
      try { t.ki = await send(`/stacking/results/${r.id}/tutor/ki`, {}); draw(t); }
      catch (err) { toast(err.message, 'err'); draw(t); }
    });
    $('#tutorRefresh', box).addEventListener('click', () => load(true));
  };
  const load = async (refresh = false) => {
    try {
      const t = await api(`/stacking/results/${r.id}/tutor${refresh ? '?refresh=true' : ''}`);
      tutorCache.set(r.id, t);
      draw(t);
    } catch { /* ohne Bericht bleibt die einfache Bildprüfung stehen */ }
  };
  if (tutorCache.has(r.id)) draw(tutorCache.get(r.id));
  else await load();
}

async function deleteProject(p) {
  const what = [p.n_results ? `${p.n_results} Version${p.n_results > 1 ? 'en' : ''} des Bildes` : '', 'den Master-Stack',
    'hochgeladene Einzelbilder'].filter(Boolean).join(', ');
  if (!await confirmDlg(`„${p.name}“ löschen?`, `Entfernt ${esc(what)}. Das lässt sich nicht rückgängig machen.${(p.companions ?? p.options?.companion_ids?.length ?? 0) ? '<br>Die zugeordneten Teleskopbilder bleiben erhalten und erscheinen danach einzeln in der Galerie.' : ''}<br><br>
    <span class="sm">Aufnahmen aus eingebundenen Ordnern bleiben auf der Festplatte und werden künftig ignoriert.</span>`, 'Endgültig löschen', true)) return false;
  try {
    const r = await send(`/stacking/projects/${p.id}`, null, 'DELETE');
    toast(`„${r.name}“ gelöscht${r.bytes ? ` – ${fmb(r.bytes)} frei` : ''}.`, 'ok');
    return true;
  } catch (e) {
    toast(e.message, 'err');
    return false;
  }
}

// --------------------------------------------------------------- Galerie --- //
async function scan(b) {
  if (b) { b.disabled = true; b.classList.add('busy'); }
  try {
    const r = await send('/stacking/scan');
    toast(r.new_frames ? `${r.new_frames} neue Dateien gefunden${r.solved ? ` – ${r.solved} per Plate-Solving zugeordnet` : ''}.` : 'Keine neuen Dateien.', r.new_frames ? 'ok' : '');
    (r.errors || []).slice(0, 3).forEach(m => toast(m, 'err'));
    poll();
  } catch (e) { toast('Scan fehlgeschlagen: ' + e.message, 'err'); }
  if (b) { b.disabled = false; b.classList.remove('busy'); }
}
async function galleryView(el) {
  crumbs([['Bilder'], ['Galerie']]);
  const ui = JSON.parse(localStorage.getItem('af.gallery') || '{}');
  let q = ui.q || '', kind = ui.kind || 'all', sort = ui.sort || 'recent', projects = [], st = sst(), sig = '', order = '';
  el.innerHTML = `<div class="pagehead"><div><h1>Deine Objekte</h1><p class="mut" id="gsum"></p></div><span class="grow"></span>
    <a class="btn ghost" href="#/bilder/statistik">${icon('pulse')}Statistik</a>
    <button class="btn ghost" id="gscan" title="Eingangsordner jetzt nach neuen Aufnahmen durchsuchen">${icon('refresh')}Scannen</button>
    <button class="btn ghost" id="gdev" title="Alle Objekte aus ihrem Master-Stack neu entwickeln – ohne Stacking, z. B. nach neuen Bearbeitungsfunktionen">${icon('play')}Alle neu entwickeln</button>
    <a class="btn" href="#/import">${icon('upload')}Aufnahmen hinzufügen</a></div>
    <div class="toolbar"><input type="search" id="gq" placeholder="Objekt suchen …" title="Taste /" value="${esc(q)}"><div class="seg" id="gkind"></div><span class="grow"></span>
      <select id="gsort"><option value="recent">Zuletzt bearbeitet</option><option value="name">Name</option><option value="exposure">Belichtungszeit</option><option value="frames">Aufnahmen</option></select></div>
    <div id="gallery"></div>`;
  $('#gsort').value = sort;
  $('#gscan').onclick = async (e) => { await scan(e.currentTarget); await load(); };
  $('#gdev').onclick = async () => {
    if (!await confirmDlg('Alle Objekte neu entwickeln?', 'Jedes Objekt wird aus seinem gespeicherten Master-Stack neu bearbeitet (ohne Stacking). Die bisherigen Bilder bleiben als ältere Versionen erhalten. Das kann je nach Anzahl eine Weile dauern.', 'Neu entwickeln')) return;
    try { const r = await send('/stacking/develop-all', {}); toast(`${r.queued} Objekte werden neu entwickelt.`, 'ok'); } catch (err) { toast(err.message, 'err'); }
    await load();
    poll();
  };
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
      try { await send(`/stacking/projects/${run.dataset.run}/process`, { restack: false }); toast('Verarbeitung gestartet.'); } catch (err) { toast(err.message, 'err'); }
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
          const src = p.result && `/api/stacking/results/${p.result.id}/thumb`, thumbSame = src && $('.thumb img', c)?.getAttribute('src') === src;
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
    return `<div class="thumb ${busy ? 'busy' : ''}">${r ? `<img src="/api/stacking/results/${r.id}/thumb" alt="" loading="lazy" decoding="async" onload="this.classList.add('ld')">`
        : `<div class="ph">${icon('star')}${busy ? 'wird verarbeitet …' : 'noch kein Ergebnis'}</div>`}
        ${busy ? '' : `<button class="cardx" data-del="${p.id}" title="Objekt löschen" aria-label="${esc(p.name)} löschen">${icon('trash')}</button>`}
        ${pal && pal !== 'OSC' ? `<span class="chip acc badge">${esc(PALETTE_LABEL[pal] || pal)}</span>` : ''}
        ${busy ? `<span class="state">${p.job.state === 'queued' ? 'wartet' : `<span class="spinner" style="width:12px;height:12px"></span>${Math.round((p.job.progress || 0) * 100)} %`}</span>` : ''}</div>
      <div class="body"><h3>${esc(p.name)}</h3>
        <div class="meta"><span class="chip">${esc(p.kind_label || 'Allgemein')}</span>${p.imported ? '<span class="chip acc">Fertiges Bild</span>' : `<span class="chip">${p.nights.length} ${p.nights.length === 1 ? 'Nacht' : 'Nächte'}</span>`}${p.n_results > 1 ? `<span class="chip">${p.n_results} Versionen</span>` : ''}</div>
        ${busy ? jobLine(p.job) : `<div class="foot"><span>${p.imported ? 'Original erhalten' : r ? `${r.n_frames} von ${p.total} Aufnahmen` : `${p.total} Aufnahmen`}</span>
          ${r ? `<span>${p.imported ? esc(r.stats?.imported?.format || '') : dur(r.exposure)}</span>` : `<button class="btn sm quick" data-run="${p.id}">${icon('play')}Verarbeiten</button>`}</div>`}
      </div>`;
  }
  async function load() {
    projects = (await api('/stacking/projects')).filter(p => !p.options?.gallery_parent);
    const s = JSON.stringify(projects);
    if (s !== sig) { sig = s; draw(); }
  }
  await load();
  const onKey = (e) => { if (e.key === '/' && !e.target.closest('input,select,textarea')) { e.preventDefault(); $('#gq').focus(); } };
  document.addEventListener('keydown', onKey);
  return { update: load, destroy: () => document.removeEventListener('keydown', onKey) };
}

// ---------------------------------------------------------------- Objekt --- //
async function projectView(el, id, tab = 'bild') {
  id = +id;
  let p = await api('/stacking/projects/' + id), frames = null, sel = null, cmp = null, viewer = null, sig = '', before = false, stage = null;
  let fFilter = 'all', fBand = 'all', fSort = ['date', 1];
  const tabs = () => [...TABS.filter(([k]) => !p.imported || k === 'bild' || (k === 'versionen' && p.results.length > 1)),
    ...((p.companions ?? p.options?.companion_ids?.length ?? 0) ? [['teleskopbilder', 'Teleskopbilder']] : [])];
  crumbs([['Galerie', '#/bilder'], [p.name]]);

  function head() {
    const r = p.result, busy = running(p.job);
    return `<div class="pagehead"><div style="min-width:0">
        <button class="namebtn" id="rename" title="Umbenennen"><h1 style="display:inline">${esc(p.name)}</h1>${icon('edit')}</button>
        <div class="row" style="margin-top:8px"><span class="chip">${esc(p.kind_label || 'Allgemein')}</span>
          ${r?.stats?.palette ? `<span class="chip acc">${esc(PALETTE_LABEL[r.stats.palette] || r.stats.palette)}</span>` : ''}
          ${p.imported ? '<span class="chip acc">Fertiges Bild · importiert</span>' : `<span class="chip">${p.total} Aufnahmen</span><span class="chip">${p.nights.length} ${p.nights.length === 1 ? 'Nacht' : 'Nächte'}</span>`}
          ${r && !p.imported ? `<span class="chip">${dur(r.exposure)} integriert</span>` : ''}
          ${p.coords?.source === 'solve' ? `<span class="chip acc" title="Koordinaten per Plate-Solving bestimmt">${icon('star')}Plate-Solved</span>` : ''}</div></div>
      <span class="grow"></span>
      <div class="row">${p.imported ? '' : `<button class="btn ${busy ? 'busy' : ''}" id="run" ${busy ? 'disabled' : ''}>${icon(busy ? 'refresh' : 'play')}${busy ? 'Läuft …' : r ? 'Aktualisieren' : 'Verarbeiten'}</button>`}
        ${r ? `<div class="menu" id="dl"><button class="btn ghost">${icon('down')}Export${icon('chev')}</button><div class="pop">
          ${p.imported ? `<a href="/api/stacking/results/${r.id}/original">Originaldatei <small>unverändert · ${esc(r.stats.imported.format)}</small></a>` : `<a href="/api/stacking/results/${r.id}/tif">TIFF 16-bit <small>Druck/Bearbeitung</small></a>`}<a href="/api/stacking/results/${r.id}/png">PNG <small>${p.imported ? 'Anzeigeversion' : 'verlustfrei'}</small></a>
          <a href="/api/stacking/results/${r.id}/jpg" download>JPEG <small>zum Teilen</small></a>${p.imported ? '' : `<a href="/api/stacking/results/${r.id}/fits">FITS linear <small>für PixInsight & Co.</small></a>`}</div></div>` : ''}
        <div class="menu" id="more"><button class="btn ghost">Mehr${icon('chev')}</button><div class="pop">
          ${r && !p.imported ? `<a href="#" data-act="develop">Neu entwickeln <small>ohne Stacking</small></a>` : ''}
          ${p.imported ? '<span class="pop-note">Fertig importiert · keine Rohaufnahmen vorhanden</span>' : p.deleted?.can_restack === false ? `<span class="pop-note">Komplett neu stacken nicht möglich – Einzelbilder nach dem Stacking gelöscht</span>`
            : `<a href="#" data-act="restack">Komplett neu stacken <small>alle Aufnahmen</small></a>`}
          ${p.can_solve ? `<a href="#" data-act="solve">Objekt per Plate-Solving bestimmen <small>Sternmuster</small></a>` : ''}
          ${r && AF.on('sage') ? `<a href="#" data-act="story">Geschichte erzählen <small>Sage · Objekte im Bild + KI</small></a>` : ''}
          <a href="#" data-act="rename">Umbenennen</a>
          ${busy ? '' : `<hr><a href="#" data-act="delete" class="danger">Objekt löschen <small>mit allen Versionen</small></a>`}
        </div></div></div></div>
      ${p.can_solve && !p.coords && !busy ? `<div class="notice">${icon('star')}<div><b>Objekt unbekannt</b><div class="mut sm">Die Aufnahmen enthalten weder Objektnamen noch Koordinaten.
        Plate-Solving bestimmt die Himmelsposition aus dem Sternmuster und ordnet sie dem richtigen Objekt zu.</div></div><span class="grow"></span>
        <button class="btn sm" id="solveBtn">Jetzt bestimmen</button></div>` : ''}
      ${p.job?.state === 'error' && !busy ? `<div class="notice err"><div><b>Letzter Durchlauf fehlgeschlagen</b><div class="mut sm">${esc(p.job.message)}</div></div></div>` : ''}
      <div id="job" style="margin:-8px 0 18px">${jobLine(p.job)}</div>
      <nav class="tabs" id="tabs">${tabs().map(([k, l], i) => { const n = { aufnahmen: p.total, versionen: p.results.length, teleskopbilder: p.companions ?? p.options?.companion_ids?.length }[k];
        return `<a href="#/p/${id}/${k}" data-t="${k}" class="${tab === k ? 'on' : ''}" title="Taste ${i + 1}">${l}${n != null ? `<span>${n}</span>` : ''}</a>`; }).join('')}<i class="tab-ind" id="tabind"></i></nav>
      <div id="tab"></div>`;
  }

  function draw() {
    el.innerHTML = head();
    $('#rename').onclick = rename;
    $('#run')?.addEventListener('click', () => run(false));
    $$('.menu', el).forEach(m => {
      $('button', m).onclick = (e) => { e.stopPropagation(); $$('.menu.open').forEach(o => o !== m && o.classList.remove('open')); m.classList.toggle('open'); };
    });
    $('#more .pop').onclick = (e) => {
      const a = e.target.closest('[data-act]');
      if (a) { e.preventDefault(); ({ solve, develop, rename, story, delete: remove }[a.dataset.act] || (() => run(true)))(); }
      $('#more').classList.remove('open');
    };
    $('#solveBtn')?.addEventListener('click', solve);
    drawTab(false);
  }
  // Nur den Tab-Inhalt neu zeichnen – Kopf, Jobzeile und Marker bleiben stehen
  function drawTab(animate = true) {
    if (p.imported && !['bild', 'versionen'].includes(tab)) tab = 'bild';
    viewer?.destroy();
    viewer = null;
    $$('#tabs a').forEach(a => a.classList.toggle('on', a.dataset.t === tab));
    requestAnimationFrame(() => slideInd($('#tabind'), $('#tabs a.on'), true));
    const t = $('#tab');
    if (!t) return;
    t.classList.remove('enter');
    ({ bild: tabImage, aufnahmen: tabFrames, versionen: tabVersions, optionen: tabOptions, teleskopbilder: tabCompanions }[tab] || tabImage)(t);
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
    // Stacking-Verlauf: Stufen mit weniger Aufnahmen, letzte Position = fertiges Bild (Standard)
    const stages = !c && !before ? [...(s.stages || []), { n: r.n_frames, exposure: r.exposure, final: true }] : [];
    if (stage?.rid !== r.id || !(stage.i >= 0 && stage.i < stages.length)) stage = { rid: r.id, i: stages.length - 1 };
    const stageLabel = (x) => x.final ? `Alle ${x.n} Aufnahmen · ${dur(x.exposure)} – fertiges Bild` : `${x.n} ${x.n === 1 ? 'Aufnahme' : 'Aufnahmen'} · ${dur(x.exposure)}`;
    const stageSrc = (x) => x.final ? `/api/stacking/results/${r.id}/jpg` : `/api/stacking/results/${r.id}/s${x.n}`;
    t.innerHTML = `<div class="split"><div>
        <div id="viewer"></div>
        ${stages.length > 1 ? `<div class="stages"><div class="stages-h"><b>Stacking-Verlauf</b><span class="grow"></span><span class="sm tnum" id="stlbl">${stageLabel(stages[stage.i])}</span></div>
          <input type="range" id="strange" min="0" max="${stages.length - 1}" step="1" value="${stage.i}" aria-label="Stacking-Stufe" style="--fill:${stage.i / (stages.length - 1) * 100}%">
          <div class="stages-ticks">${stages.map((x, i) => `<span data-i="${i}" class="tnum${i === stage.i ? ' on' : ''}">${x.final ? 'Alle' : x.n}</span>`).join('')}</div>
          <p class="mut sm">Dasselbe Objekt mit weniger Aufnahmen, gleich entwickelt – so sieht man, was mehr Belichtungszeit bringt.${s.stages_simplified ? ` Zwischenstufen ohne ${s.stages_simplified.join('/')} entwickelt.` : ''}</p></div>` : ''}
        ${r.has_before && !c ? `<div class="row" style="margin-top:10px"><div class="seg" id="ba"><button data-b="0" class="${before ? '' : 'on'}">Ergebnis</button>
          <button data-b="1" class="${before ? 'on' : ''}">Vorher / Nachher</button></div>
          ${before ? '<span class="mut sm">Links der unbearbeitete Master-Stack, rechts das entwickelte Bild – Trennlinie ziehen</span>' : ''}</div>` : ''}
        ${r.id !== p.results[0].id || c ? `<div class="row sm mut" style="margin-top:8px">${c ? `Vergleich: links Version vom ${fdate(c.created)}, rechts ${fdate(r.created)}` : `Ältere Version vom ${fdate(r.created)}`}
          <span class="grow"></span><button class="linkbtn" id="latest">Neueste Version zeigen</button></div>` : ''}
        ${p.imported ? `<div class="notice" style="margin-top:14px"><div><b>Fertig importiertes Bild</b><div class="mut sm">Das Original bleibt unverändert. AstroFlow führt kein erneutes Stacking und keine automatische Entwicklung durch.</div>
          ${AF.on('sage') ? '<button class="btn ghost sm" id="importedSage" style="margin-top:8px">Mit Sage bestimmen und erzählen</button>' : ''}</div></div>` : `<div class="stats">
          <div class="stat"><small>Integration</small><b>${dur(r.exposure)}</b></div>
          <div class="stat"><small>Gestackt</small><b class="tnum">${r.n_frames}</b></div>
          <div class="stat"><small>Sternschärfe (FWHM)</small><b class="tnum">${s.fwhm_px ?? '–'} px</b></div>
          <div class="stat"><small>Auflösung</small><b class="tnum">${s.shape ? s.shape[1] + '×' + s.shape[0] : '–'}</b></div>
        </div>`}</div>
      <div>
        ${Object.keys(bands).length > 1 || (Object.keys(bands).length === 1 && !bands.RGB) ? `<div class="panel"><div class="panel-h"><h2>Kanäle</h2><span class="grow"></span><span class="chip acc">${esc(PALETTE_LABEL[s.palette] || s.palette || '')}</span></div>
          <div class="bands">${Object.entries(bands).map(([b, v]) => `<div class="band"><span><span class="chip"><i style="background:${BAND_COLOR[b] || '#999'}"></i>${esc(b)}</span></span>
            <div class="progress"><i style="width:${v.exposure / bmax * 100}%;background:${BAND_COLOR[b] || 'var(--acc)'}"></i></div><span class="mut sm tnum">${v.frames} · ${dur(v.exposure)}</span></div>`).join('')}</div></div>` : ''}
        <div id="tutorBox">${qualityPanel(s)}</div>
        <div class="panel"><h2 style="margin-bottom:12px">Details</h2><dl class="kv">
          <dt>Erstellt</dt><dd>${fdate(r.created)}</dd>
          ${p.imported ? `<dt>Dateiname</dt><dd>${esc(s.imported.filename)}</dd><dt>Format</dt><dd>${esc(s.imported.format)} · ${s.imported.bit_depth} Bit</dd>
          <dt>Auflösung</dt><dd>${s.shape[1]} × ${s.shape[0]} px</dd>${s.imported.date_obs ? `<dt>Aufnahmezeit (Dateimetadaten)</dt><dd>${esc(s.imported.date_obs)}</dd>` : ''}` : `<dt>Bearbeitungsprofil</dt><dd>${esc(PROFILE_LABEL[s.profile] || s.profile || '–')}</dd>
          <dt>Aufnahmetyp</dt><dd>${s.palette === 'OSC' || !s.palette ? 'Farbkamera' : 'Mono + Filter'}${s.narrowband ? ' · Schmalband' : ''}</dd>
          <dt>Sterne erkannt</dt><dd class="tnum">${s.stars ?? '–'}</dd>
          ${s.graxpert ? `<dt>GraXpert-KI</dt><dd>${[s.graxpert.bge && 'Hintergrund', s.graxpert.denoise && `Entrauschen ${s.graxpert.denoise}`].filter(Boolean).join(' · ') || 'nicht angewendet'}${s.graxpert.error ? `<div class="dim sm">${esc(s.graxpert.error)}</div>` : ''}</dd>` : ''}
          <dt>Sterne</dt><dd>${s.star_separation ? `getrennt bearbeitet (${s.star_separation === 'starnet' ? 'StarNet' : 'eigene Trennung'})` : 'mit dem Bild bearbeitet'}</dd>
          ${s.wb_gains ? `<dt>Weißabgleich (R/G/B)</dt><dd class="tnum">${s.wb_gains.join(' / ')}</dd>` : ''}
          ${s.color_cal ? `<dt>Farbkalibrierung</dt><dd>${s.color_cal.verfahren === 'gaia' ? `fotometrisch (Gaia, ${s.color_cal.sterne} Sterne)`
            : s.color_cal.verfahren === 'manuell' ? 'manuell' : `Sternfarben-Mittel${s.color_cal.grund ? `<div class="dim sm">${esc(s.color_cal.grund)}</div>` : ''}`}</dd>` : ''}
          <dt>Nächte</dt><dd>${p.nights.map(fnight).join(', ') || '–'}</dd>`}
          ${p.deleted?.frames ? `<dt>Einzelbilder</dt><dd>${p.deleted.frames} gelöscht · ${fmb(p.deleted.bytes)} frei</dd>` : ''}
        </dl></div>
        ${coordsPanel(p.coords)}
        ${rev.length ? `<div class="panel"><h2 style="margin-bottom:12px">KI-Bildprüfung</h2><div class="review">${rev.map(x => x.error
          ? `<div><b>Runde ${x.round}</b> <span class="warn">${esc(x.error)}</span></div>`
          : `<div><b>Runde ${x.round}</b> · <span class="${x.ok ? 'ok' : 'warn'}">${x.ok ? 'in Ordnung' : 'nachgebessert'}</span> <span class="mut">(${esc(x.score)}/10)</span>
            ${x.issues?.length ? `<ul>${x.issues.map(i => `<li>${esc(i)}</li>`).join('')}</ul>` : ''}
            ${Object.keys(x.adjust || {}).length ? `<div class="mut sm">${Object.entries(x.adjust).map(([k, v]) => `${esc(k)} → ${esc(v)}`).join(' · ')}</div>` : ''}</div>`).join('')}</div></div>` : ''}
      </div></div>`;
    if (s.quality) tutorPanel($('#tutorBox'), r);
    const ph = `/api/stacking/results/${r.id}/thumb`;
    viewer = c ? createViewer($('#viewer'), `/api/stacking/results/${r.id}/jpg`, `/api/stacking/results/${c.id}/jpg`, undefined, ph)
      : before && r.has_before ? createViewer($('#viewer'), `/api/stacking/results/${r.id}/jpg`, `/api/stacking/results/${r.id}/before`, ['vorher', 'nachher'], ph)
      : createViewer($('#viewer'), `/api/stacking/results/${r.id}/jpg`, null, undefined, ph);
    $('#ba')?.addEventListener('click', (e) => { const b = e.target.closest('[data-b]'); if (b) { before = b.dataset.b === '1'; drawTab(false); } });
    if (stages.length > 1) {
      let pre = null;                                    // alle Stufen vorladen, sobald der Regler benutzt wird
      const show = (i) => {
        if (!pre) pre = stages.map(x => Object.assign(new Image(), { src: stageSrc(x) }));
        stage.i = i; $('#strange').value = i;
        $('#strange').style.setProperty('--fill', i / (stages.length - 1) * 100 + '%');
        $('#stlbl').textContent = stageLabel(stages[i]);
        $$('.stages-ticks span').forEach(sp => sp.classList.toggle('on', +sp.dataset.i === i));
        viewer.setSrc(stageSrc(stages[i]));
      };
      $('#strange').addEventListener('input', (e) => show(+e.target.value));
      $('.stages-ticks').addEventListener('click', (e) => { const sp = e.target.closest('[data-i]'); if (sp) show(+sp.dataset.i); });
      if (stage.i !== stages.length - 1) viewer.setSrc(stageSrc(stages[stage.i]));
    }
    $('#latest')?.addEventListener('click', () => { sel = null; cmp = null; drawTab(); });
    $('#importedSage')?.addEventListener('click', story);
  }

  // ---- Tab: Aufnahmen ----
  async function tabFrames(t) {
    if (!frames) { t.innerHTML = '<div class="skel" style="height:300px"></div>'; frames = await api(`/stacking/projects/${id}/frames`); }
    if (!t.isConnected) return;
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
      try { r = await send('/stacking/frames/' + f.id, { manual: m }, 'PATCH'); } catch (err) { toast(err.message, 'err'); return; }
      const apply = (v) => { Object.assign(f, v); drawTable(); $(`#ftable tr[data-id="${f.id}"]`)?.classList.add('flash'); };
      apply(m > 0 ? { manual: m, status: 'selected' } : { manual: m, status: 'rejected', reason: 'manuell ausgeschlossen' });
      // Rückgängig nur, wenn dadurch kein Neu-Stacken ausgelöst wurde
      const undo = r.restack_needed ? null : { label: 'Rückgängig', fn: async () => { await send('/stacking/frames/' + f.id, { manual: prev.manual || 0 }, 'PATCH'); apply(prev); } };
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
        <img src="/api/stacking/results/${r.id}/thumb" alt="" loading="lazy"><div><b>${i === 0 ? 'Aktuell' : fdate(r.created)}</b>
        <span class="mut">${r.stats?.imported ? esc(r.stats.imported.filename) : `${r.n_frames} Aufnahmen · ${dur(r.exposure)}`}</span>
        <span class="row" style="justify-content:space-between"><span class="dim">${esc(PROFILE_LABEL[r.stats?.profile] || r.stats?.profile || '')}</span>
        <span class="ver-act">${r.id !== cur ? `<button class="linkbtn" data-cmp="${r.id}" title="Mit angezeigter Version vergleichen">${icon('compare')}</button>` : ''}
        ${p.results.length > 1 ? `<button class="linkbtn danger" data-del="${r.id}" title="Diese Version löschen">${icon('trash')}</button>` : ''}</span></span></div></div>`).join('')}</div>`;
    t.onclick = async (e) => {
      const d = e.target.closest('[data-del]');
      if (d) {
        e.stopPropagation();
        const rid = +d.dataset.del, i = p.results.findIndex(x => x.id === rid);
        if (!await confirmDlg('Version löschen?', `Entfernt ${i === 0 ? 'die aktuelle Version' : `die Version vom ${esc(fdate(p.results[i].created))}`} samt Bilddateien. Das lässt sich nicht rückgängig machen.${i === 0 ? '<br>Danach gilt die nächstältere Version als aktuell.' : ''}`, 'Endgültig löschen', true)) return;
        try {
          const res = await send(`/stacking/results/${rid}`, null, 'DELETE');
          if (sel === rid) sel = null;
          if (cmp === rid) cmp = null;
          toast(`Version gelöscht${res.freed ? ` – ${fmb(res.freed)} frei` : ''}.`, 'ok');
          await refresh(true);
        } catch (err) { toast(err.message, 'err'); }
        return;
      }
      const c = e.target.closest('[data-cmp]');
      if (c) { e.stopPropagation(); cmp = +c.dataset.cmp; sel = cur; location.hash = `#/p/${id}/bild`; return; }
      const v = e.target.closest('.ver');
      if (v) { sel = +v.dataset.id; cmp = null; location.hash = `#/p/${id}/bild`; }
    };
  }

  async function tabCompanions(t) {
    t.innerHTML = '<div class="panel"><span class="spinner"></span> Teleskopbilder laden …</div>';
    try {
      const pictures = (await api('/stacking/projects?parent_id=' + id)).filter(x => x.options?.gallery_parent === id && x.result)
        .sort((a, b) => String(b.options.imported?.date_obs || '').localeCompare(String(a.options.imported?.date_obs || '')) || b.id - a.id);
      if (!t.isConnected || tab !== 'teleskopbilder') return;
      let count = 48;
      function drawPictures() {
        t.innerHTML = `<p class="mut">${pictures.length} vom Teleskop erzeugte Begleitbilder. Sie gehören zu dieser Aufnahme und bleiben mit ihren Originaldateien erhalten.</p>
          <div class="gallery">${pictures.slice(0, count).map(x => `<a class="card" href="#/p/${x.id}"><div class="thumb"><img src="/api/stacking/results/${x.result.id}/thumb" alt="" loading="lazy"></div>
            <div class="body"><h3>${esc(x.options.imported?.filename || x.name)}</h3><p class="mut sm">${x.n_results > 1 ? `${x.n_results} Versionen` : 'Teleskop-Vorschau'}</p></div></a>`).join('')}</div>
          ${count < pictures.length ? '<button class="btn ghost" id="moreCompanions" style="margin-top:16px">Weitere anzeigen</button>' : ''}`;
        t.querySelector('#moreCompanions')?.addEventListener('click', () => { count += 48; drawPictures(); });
      }
      drawPictures();
    } catch (e) { if (t.isConnected) t.innerHTML = `<div class="panel">${esc(e.message)}</div>`; }
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
        <label class="field"><span>Sterne getrennt bearbeiten</span><select name="star_separation">${opt(['', 'starnet', 'auto', 'intern', 'off'], o.star_separation || '', v => ({ '': 'Wie Profil (Nebel: StarNet, sonst nein)', starnet: 'Ja – nur mit StarNet', auto: 'Ja – StarNet, falls installiert, sonst eigene Trennung', intern: 'Ja – eigene Trennung', off: 'Nein' }[v]))}</select>
          <small>Nebel wird ohne Sterne gestreckt (mehr Nebel, Sterne kleiner). Bei Sternhaufen besser aus (Kern brennt aus).</small></label>
        <label class="field"><span>GraXpert (KI-Hintergrund und -Entrauschen)</span><select name="graxpert">${opt(['', 'all', 'bge', 'denoise', 'off'], o.graxpert || '', v => GX_LABEL[v || 'default'])}</select>
          <small>Nur wirksam, wenn GraXpert installiert ist (Einstellungen → Bilder).</small></label>
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
      resolution_scale: +v('resolution_scale'), narrowband: v('narrowband') === '' ? null : v('narrowband') === 'true', review: v('review') === 'true', star_separation: v('star_separation') || null, graxpert: v('graxpert') || null });
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
        if (dirty) await send('/stacking/projects/' + id, { options: o2 }, 'PATCH');
        if (go) await (needRestack ? run(true, true) : p.result ? develop() : run(false));
        else toast('Optionen gespeichert.', 'ok');
      } catch (err) { toast(err.message, 'err'); return; }
      p = await api('/stacking/projects/' + id);
      if (tab === 'optionen') drawTab(false);
    };
  }

  // ---- Aktionen ----
  async function solve() {
    try {
      await send(`/stacking/projects/${id}/solve`);
      toast('Plate-Solving gestartet – die Aufnahmen werden danach dem passenden Objekt zugeordnet.');
      await refresh(true);
    } catch (e) {
      toast(e.message, 'err');
      if (/Plate-Solver/.test(e.message)) location.hash = '#/settings/stacking';
    }
  }
  async function run(restack, quiet) {
    if (restack && !quiet && !await confirmDlg('Komplett neu stacken?', 'Alle ausgewählten Aufnahmen werden erneut registriert und gestackt. Das kann je nach Menge dauern.', 'Neu stacken')) return;
    try {
      await send(`/stacking/projects/${id}/process`, { restack: !!restack });
    } catch (e) {
      toast(e.message, 'err');
      return;
    }
    toast(restack ? 'Neu-Stacken gestartet.' : 'Verarbeitung gestartet.');
    await refresh(true);
  }
  async function remove() {
    if (await deleteProject({ ...p, n_results: p.results.length })) location.hash = '#/bilder';
  }
  async function develop() {
    try {
      await send(`/stacking/projects/${id}/process`, { mode: 'develop' });
    } catch (e) {
      toast(e.message, 'err');
      return;
    }
    toast('Wird aus dem Master-Stack neu entwickelt.');
    await refresh(true);
  }
  async function story() {
    try {
      await send(`/sage/analyze/project/${id}`);
      toast('Sage bestimmt die Objekte im Bild und schreibt die Geschichte …', '', { label: 'Zu Sage', href: '#/sage' });
      poll();
    } catch (e) { toast(e.message, 'err'); }
  }
  async function rename() {
    const d = $('#dlg');
    d.innerHTML = `<form method="dialog" id="rf"><h2 style="font-size:18px;margin-bottom:14px">Objekt umbenennen</h2>
      <input name="n" value="${esc(p.name)}" style="width:100%" maxlength="80" autofocus>
      <div class="row" style="justify-content:flex-end;margin-top:18px"><button class="btn ghost" value="">Abbrechen</button><button class="btn" value="ok">Speichern</button></div></form>`;
    d.showModal();
    d.onclose = async () => {
      const v = $('input', d)?.value.trim();
      if (d.returnValue === 'ok' && v && v !== p.name) { await send('/stacking/projects/' + id, { name: v }, 'PATCH'); toast('Umbenannt.', 'ok'); await refresh(true); crumbs([['Galerie', '#/bilder'], [p.name]]); }
    };
  }
  async function refresh(force) {
    try {
      p = await api('/stacking/projects/' + id);
    } catch {
      // Projekt existiert nicht mehr: nach dem Plate-Solving wurden alle Aufnahmen anderen Objekten zugeordnet
      toast('Alle Aufnahmen wurden einem erkannten Objekt zugeordnet.', 'ok');
      location.hash = '#/bilder';
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
    else if (/^[1-9]$/.test(k) && tabs()[+k - 1]) location.hash = `#/p/${id}/${tabs()[+k - 1][0]}`;
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
      ...(running(p.job) || p.imported ? [] : [['play', p.result ? 'Aktualisieren' : 'Verarbeiten', 'neue Aufnahmen nachstacken', () => run(false)]]),
      ...(p.result && !running(p.job) && !p.imported ? [['refresh', 'Neu entwickeln', 'ohne Stacking', develop]] : []),
      ...tabs().map(([k, l], i) => ['arrow', `${l} anzeigen`, `Taste ${i + 1}`, () => { location.hash = `#/p/${id}/${k}`; }]),
      ...(p.result ? [['expand', 'Vollbild', 'Taste F', () => { location.hash = `#/p/${id}/bild`; setTimeout(() => $('.viewer [data-a=full]')?.click(), 60); }],
        ['down', p.imported ? 'Original herunterladen' : 'TIFF herunterladen', p.imported ? 'unverändert' : '16 bit', () => { location.href = `/api/stacking/results/${p.result.id}/${p.imported ? 'original' : 'tif'}`; }]] : []),
      ...(p.result && AF.on('sage') ? [['book', 'Geschichte erzählen', 'Sage', story]] : []),
      ['edit', 'Umbenennen', p.name, rename],
    ],
    destroy: () => { window.__afSel = { id, sel, cmp }; viewer?.destroy(); document.removeEventListener('click', closeMenu); document.removeEventListener('keydown', onKey); },
  };
}

// ------------------------------------------------------------------ Import --- //
async function importView(el) {
  crumbs([['Bilder', '#/bilder'], ['Import']]);
  const st = await api('/stacking/status');
  el.innerHTML = `<div class="pagehead"><div><h1>Bilder und Aufnahmen importieren</h1><p class="mut">Ein Eingang für Rohaufnahmen und fertige Astrofotos – AstroFlow ordnet sie automatisch zu.</p></div></div>
    <label class="dropzone" id="dz">${icon('upload')}<h2>Bilder oder ganze Ordner hierher ziehen</h2>
      <div class="mut">oder klicken · FITS, JPG, PNG und TIFF dürfen gemischt sein</div>
      <input type="file" id="fi" multiple accept=".fit,.fits,.fts,.jpg,.jpeg,.png,.tif,.tiff" hidden></label>
    <div class="row" style="justify-content:center;margin-top:12px"><label class="btn ghost sm">Ordner auswählen…<input type="file" id="fd" webkitdirectory multiple hidden></label>
      <span class="mut sm">Ordnernamen bleiben erhalten – z. B. verrät „…_observation_M57“ bei Stellina das Objekt.</span></div>
    <div class="uploads" id="ups"></div>
    <section class="panel" id="lib" style="margin-top:20px"><div class="panel-h"><h2 style="margin:0">Hauptordner durchsuchen</h2></div>
      <p class="mut sm" style="margin-top:0">Ordner mit allen Beobachtungen wählen (z. B. die Stellina-Platte). AstroFlow findet die Objekte darin und zeigt, was noch nicht oder nicht fertig gestackt in AstroFlow ist. Die Dateien bleiben unverändert.</p>
      <div class="lib-form"><button class="btn" id="libPick">${icon('folder')}Ordner wählen …</button><span class="lib-root" id="libRoot"></span><button class="btn ghost" id="libGo" hidden>${icon('refresh')}Erneut durchsuchen</button></div>
      <div id="libBody"></div></section>
    <div class="infogrid">
      <div><b>Oder: Rohdaten-Ordner einbinden</b>Lege FITS-Dateien direkt in <code>${esc(st.input_dir)}</code> – AstroFlow schaut alle ${st.scan_interval} s nach.</div>
      <div><b>FITS → Rohaufnahmen</b>Objekt, Kamera und Nacht werden aus den Headern erkannt. Lights, Darks, Flats und Bias sind gemischt möglich; neue Nächte werden nachgestackt.</div>
      <div><b>JPG, PNG, TIFF → fertige Bilder</b>Direkt in die Galerie, ohne erneutes Stacking oder Stretch. Das Original bleibt erhalten. Sage bestimmt die Bilder automatisch, wenn „Automatisch entdecken“ aktiv ist.</div>
    </div>`;
  const dz = $('#dz');
  $('#fi').onchange = (e) => { upload([...e.target.files]); e.target.value = ''; };
  $('#fd').onchange = (e) => { upload([...e.target.files]); e.target.value = ''; };
  dz.ondragover = (e) => { e.preventDefault(); dz.classList.add('over'); };
  dz.ondragleave = () => dz.classList.remove('over');
  dz.ondrop = async (e) => { e.preventDefault(); e.stopPropagation(); dz.classList.remove('over'); upload(await droppedFiles(e.dataTransfer)); };
  libraryPanel(el);
}

// Hauptordner durchsuchen: Gruppen (Objekte) finden, Stand in AstroFlow anzeigen, Auswahl importieren
const LIB_STATE = { new: ['neu', 'acc'], partial: ['teilweise vorhanden', 'warn'], unstacked: ['nicht gestackt', 'warn'], done: ['fertig', 'ok'] };
const LIB_KIND = { lights: 'Aufnahmen', calibration: 'Kalibrierung', pictures: 'Fertige Bilder' };
async function libraryPanel(view) {
  const body = $('#libBody', view), shown = $('#libRoot', view), go = $('#libGo', view);
  let root = '';
  const setRoot = (r) => { root = r || ''; shown.textContent = root; shown.title = root; go.hidden = !root; };
  const picked = new Set();
  let timer = null, seen = false, showKnown = false;
  const done = (g) => g.state === 'done';
  const draw = (st) => {
    if (!body.isConnected) return clearTimeout(timer);
    const busy = st.state === 'scanning' || st.state === 'importing';
    go.disabled = busy;
    $('#libPick', view).disabled = busy;
    if (busy) {
      body.innerHTML = `<div class="lib-bar"><span class="spinner"></span><span>${esc(st.message || '')}</span><span class="grow"></span><span class="tnum">${Math.round((st.progress || 0) * 100)} %</span></div><div class="progress"><i style="width:${(st.progress || 0) * 100}%"></i></div>`;
      return;
    }
    if (st.state === 'error') { body.innerHTML = `<p class="bad sm">${esc(st.message)}</p>`; return; }
    const groups = st.groups || [];
    if (!seen) { seen = true; groups.forEach(g => { if (!done(g) && g.n_new) picked.add(g.id); }); }
    [...picked].forEach(id => { const g = groups.find(x => x.id === id); if (!g || !g.n_new) picked.delete(id); });
    if (!groups.length) { body.innerHTML = st.message ? `<p class="mut sm">${esc(st.message)}</p>` : ''; return; }
    const sel = groups.filter(g => picked.has(g.id));
    const imp = st.imported;
    // Bereits vollständig in AstroFlow vorhandene Gruppen gibt es nicht zu importieren -> ausgeblendet
    const known = groups.filter(g => !g.n_new), list = showKnown ? groups : groups.filter(g => g.n_new);
    body.innerHTML = `${imp ? `<div class="notice lib-done"><div><b>${icon('check')} Import abgeschlossen</b><div class="mut sm">${esc(st.message)}${imp.errors?.length ? `<br><span class="bad">${imp.errors.map(esc).join('<br>')}</span>` : ''}</div>
        ${Object.keys(imp.projects || {}).length ? '<div style="margin-top:8px"><a class="btn ghost sm" href="#/bilder">Zur Galerie</a></div>' : ''}</div></div>` : ''}
      <div class="lib-bar"><span class="mut sm">${imp ? '' : esc(st.message)}</span><span class="grow"></span>
        <button class="btn ghost sm" id="libNew">Nur Neues wählen</button><button class="btn ghost sm" id="libNone">Keine</button>
        <button class="btn" id="libImp" ${sel.length ? '' : 'disabled'}>${icon('upload')}${sel.length ? `${sel.length} ${sel.length === 1 ? 'Gruppe' : 'Gruppen'} importieren` : 'Importieren'}</button></div>
      ${list.length ? '' : '<p class="mut sm">Alles bereits in AstroFlow – nichts Neues zu importieren.</p>'}
      <div class="lib-list">${list.map(g => {
        const [lab, cls] = LIB_STATE[g.state] || [g.state, ''], off = !g.n_new;
        return `<label class="lib-row ${off ? 'off' : ''}"><input type="checkbox" data-id="${g.id}" ${picked.has(g.id) ? 'checked' : ''} ${off ? 'disabled' : ''}>
          <div class="lib-main"><div class="lib-title"><b>${esc(g.name)}</b><span class="chip ${cls}">${lab}</span><span class="chip">${LIB_KIND[g.kind] || g.kind}</span>${g.project?.name ? `<a class="chip acc" href="#/p/${g.project.id}">in AstroFlow: ${esc(g.project.name)}</a>` : ''}</div>
            <div class="lib-meta"><span class="chip">${g.n_files} Dateien · ${fmb(g.bytes)}</span>${g.n_known && g.n_new ? `<span class="chip">${g.n_new} davon neu</span>` : ''}${g.night ? `<span class="chip">${esc(g.night)}</span>` : ''}${g.camera ? `<span class="chip">${esc(g.camera)}</span>` : ''}</div>
            <div class="lib-path">${esc(g.rel)}</div></div></label>`;
      }).join('')}</div>
      ${known.length ? `<button class="linkbtn lib-hidden" id="libKnown">${showKnown ? 'Bereits vorhandene ausblenden' : `${known.length} bereits vorhandene ${known.length === 1 ? 'Gruppe' : 'Gruppen'} anzeigen`}</button>` : ''}`;
    $('#libKnown', body)?.addEventListener('click', () => { showKnown = !showKnown; draw(st); });
    body.onchange = (e) => { const id = e.target.dataset?.id; if (!id) return; e.target.checked ? picked.add(id) : picked.delete(id); draw(st); };
    $('#libNew', body).onclick = () => { picked.clear(); groups.forEach(g => { if (g.n_new && !done(g)) picked.add(g.id); }); draw(st); };
    $('#libNone', body).onclick = () => { picked.clear(); draw(st); };
    $('#libImp', body).onclick = async () => {
      try { draw(await send('/stacking/library/import', { ids: [...picked] })); follow(); }
      catch (e) { toast(e.message, 'err'); }
    };
  };
  const follow = async () => {
    clearTimeout(timer);
    try {
      const st = await api('/stacking/library');
      draw(st);
      if (st.state === 'scanning' || st.state === 'importing') timer = setTimeout(follow, 800);
      else if (st.imported && body.isConnected) poll();
    } catch (e) { if (body.isConnected) timer = setTimeout(follow, 2000); }
  };
  const scan = async () => {
    if (!root) return;
    picked.clear(); seen = false;
    try { draw(await send('/stacking/library/scan', { root })); follow(); } catch (e) { toast(e.message, 'err'); }
  };
  go.onclick = scan;
  $('#libPick', view).onclick = () => folderDialog(root, (p) => { setRoot(p); scan(); },
    { title: 'Hauptordner wählen', ok: 'Diesen Ordner durchsuchen', hint: 'Unterordner werden mit durchsucht, Dateien nur gelesen.' });
  const st = await api('/stacking/library');
  setRoot(st.root);
  if (st.state !== 'idle') { seen = st.state === 'done' && !st.imported ? false : true; draw(st); if (st.state === 'scanning' || st.state === 'importing') follow(); }
}
function upload(files) {
  files = files.filter(f => IMPORT_FILE.test(f.name));
  // Teleskop-Ordner enthalten oft hunderte kumulative JPEG-Zwischenstände neben den eigentlichen FITS.
  // Bei Rohdaten sind diese kein zusätzlicher Import; ohne Rohdaten genügt die letzte Vorschau je Ordner.
  const observation = f => {
    const parts = (f.relPath || f.webkitRelativePath || '').split(/[\\/]/);
    const i = parts.findIndex(p => /_observation_/i.test(p));
    return i < 0 ? null : parts.slice(0, i + 1).join('/');
  };
  const rawFolders = new Set(files.filter(f => /\.(fits?|fts)$/i.test(f.name)).map(observation).filter(Boolean));
  const previews = new Map(), latest = new Map();
  for (const f of files) {
    const folder = observation(f), m = f.name.match(/^img-(\d+)-output\.(jpe?g|png|tiff?)$/i);
    if (!folder || !m) continue;
    previews.set(f, folder);
    if (!latest.has(folder) || +m[1] > latest.get(folder).sequence) latest.set(folder, { file: f, sequence: +m[1] });
  }
  const before = files.length;
  files = files.filter(f => !previews.has(f) || (!rawFolders.has(previews.get(f)) && latest.get(previews.get(f)).file === f));
  // allgemein (wie picture_import.skipped_pictures): > SOURCE_LIMIT FITS → nur FITS; sonst > SOURCE_LIMIT TIFF/FITS → keine JPG/PNG
  if (files.filter(f => FITS_FILE.test(f.name)).length > SOURCE_LIMIT) files = files.filter(f => FITS_FILE.test(f.name));
  else if (files.filter(f => SOURCE_FILE.test(f.name)).length > SOURCE_LIMIT) files = files.filter(f => !PREVIEW_FILE.test(f.name));
  const skipped = before - files.length;
  if (skipped) toast(`${skipped} Vorschau-/Zwischenbilder übersprungen – mit vielen FITS werden nur die Rohdaten importiert. Quelldateien bleiben unverändert.`);
  if (!files.length) { toast('Keine unterstützten Dateien dabei (FITS, JPG, PNG, TIFF).', 'err'); return; }
  const total = files.reduce((a, f) => a + f.size, 0);
  const box = document.createElement('div');
  box.className = 'upl';
  box.innerHTML = `<div class="row"><b>${files.length} Datei${files.length > 1 ? 'en' : ''}</b><span class="mut">${fmb(total)}</span><span class="grow"></span><span class="tnum pct">0 %</span></div><div class="progress"><i style="width:0"></i></div>`;
  if (skipped) box.insertAdjacentHTML('beforeend', `<p class="mut sm">${skipped} Vorschau-/Zwischenbilder (JPG/PNG/TIFF) nicht mit hochgeladen. Die Quelldateien bleiben unverändert.</p>`);
  ($('#ups') || document.body).prepend(box);
  if (!$('#ups')) { location.hash = '#/import'; }
  const fd = new FormData();
  files.forEach(f => fd.append('files', f, f.relPath || f.webkitRelativePath || f.name));
  const xhr = new XMLHttpRequest();
  xhr.open('POST', '/api/stacking/upload');
  xhr.upload.onprogress = (e) => { if (e.lengthComputable) { const pc = e.loaded / e.total * 100; $('i', box).style.width = pc + '%'; $('.pct', box).textContent = Math.round(pc) + ' %'; } };
  xhr.onload = async () => {
    if (xhr.status !== 200) { $('.pct', box).innerHTML = '<span class="bad">Fehler</span>'; toast('Upload fehlgeschlagen.', 'err'); return; }
    const r = JSON.parse(xhr.responseText);
    $('.pct', box).innerHTML = `<span class="row" style="gap:6px"><span class="spinner"></span>wird einsortiert …</span>`;
    try {
      const s = r.raw ? await send('/stacking/scan') : { new_by_project: {} };
      const dupBy = s.duplicates_by_project || {}, rawDup = s.duplicates || 0;   // Rohdaten, die schon einmal importiert wurden
      const pictures = r.pictures || [], ids = [...new Set([...Object.keys(s.new_by_project || {}).map(Number), ...Object.keys(dupBy).map(Number), ...pictures.map(p => p.parent_id || p.id)])];
      const projects = ids.length ? await api('/stacking/projects') : [];
      box.classList.add('done');
      const fresh = pictures.filter(p => !p.duplicate && !p.series).length, duplicates = pictures.filter(p => p.duplicate).length + rawDup;
      const series = pictures.filter(p => !p.duplicate && p.series).length;
      $('.pct', box).innerHTML = `<span class="ok row" style="gap:6px">${icon('check')}${(r.raw || 0) - rawDup} neue Rohdateien · ${fresh} fertige Bilder${series ? ` · ${series} Teleskop-Verlaufsbilder` : ''}${duplicates ? ` · ${duplicates} bereits vorhanden` : ''}</span>`;
      (r.errors || []).forEach(message => toast(message, 'err'));
      if (r.errors?.length) box.insertAdjacentHTML('beforeend', `<p class="bad sm">${r.errors.map(esc).join('<br>')}</p>`);
      // direkt zu den erkannten Objekten springen
      if (ids.length) box.insertAdjacentHTML('beforeend', `<div class="found"><span class="mut sm" style="align-self:center">Erkannt:</span>${projects.filter(p => ids.includes(p.id))
        .map((p, i) => `<a class="chip acc" href="#/p/${p.id}" style="animation-delay:${i * 60}ms">${esc(p.name)} · ${p.imported ? 'fertiges Bild' : [s.new_by_project[p.id] && `${s.new_by_project[p.id]} neue Aufnahmen`, dupBy[p.id] && `${dupBy[p.id]} bereits vorhanden`].filter(Boolean).join(' · ')} ${icon('arrow')}</a>`).join('')}</div>`);
      const one = ids.length === 1 && projects.find(p => p.id === ids[0]);
      toast(rawDup && rawDup === r.raw && !fresh && !series ? `Nichts Neues: alle ${rawDup} Rohdateien waren schon importiert.` : `${r.saved - rawDup} Dateien übernommen${fresh ? ` · ${fresh} fertige Bilder in der Galerie` : ''}${series ? ` · ${series} Teleskop-Verlaufsbilder zusammengefasst` : ''}${duplicates ? ` · ${duplicates} bereits vorhanden` : ''}.`, r.saved ? 'ok' : 'err', one ? { label: 'Öffnen', href: `#/p/${one.id}` } : null);
      poll();
    } catch (e) { $('.pct', box).innerHTML = '<span class="bad">Einsortieren fehlgeschlagen</span>'; toast(e.message, 'err'); }
  };
  xhr.onerror = () => toast('Upload fehlgeschlagen.', 'err');
  xhr.send(fd);
}

// ------------------------------------------------------------ Speicherbelegung --- //
const HK_COLORS = ['var(--acc)', 'var(--acc2)', 'var(--acc3)', 'var(--warn)', 'var(--ok)', 'var(--bad)', '#f0abfc', '#93c5fd', 'var(--dim)'];
async function housekeeping(panel) {
  const body = $('#hkBody', panel), keepOf = () => +($('#hkKeep', panel)?.value || 2);
  const load = async (keep = 2) => {
    body.innerHTML = '<div class="skel" style="height:120px"></div>';
    let d;
    try { d = await api(`/stacking/housekeeping?keep=${keep}`); } catch (e) { body.innerHTML = `<p class="bad sm">${esc(e.message)}</p>`; return; }
    const u = d.usage, cats = u.categories;
    $('#hkTotal', panel).textContent = `${fmb(u.total)} belegt`;
    const big = u.projects.filter(p => p.output + p.stack > 0);
    body.innerHTML = `<div class="hk-bar">${cats.map((c, i) => `<i style="flex:${c.bytes};background:${HK_COLORS[i % HK_COLORS.length]}" title="${esc(c.label)}: ${fmb(c.bytes)}"></i>`).join('')}</div>
      <ul class="hk-legend">${cats.map((c, i) => `<li><i style="background:${HK_COLORS[i % HK_COLORS.length]}"></i><span><b>${esc(c.label)}</b>${c.detail ? `<small>${esc(c.detail)}</small>` : ''}</span><span class="tnum">${fmb(c.bytes)}</span></li>`).join('')}</ul>
      ${u.disk ? `<p class="mut sm" style="margin:4px 0 0">Frei auf dem Laufwerk: <b>${fmb(u.disk.free)}</b> von ${fmb(u.disk.total)}</p>` : ''}
      ${big.length ? `<h3 class="hk-h">Größte Objekte</h3><table class="hk-tab"><thead><tr><th>Objekt</th><th>Versionen</th><th>Bilder</th><th>Master-Stack</th></tr></thead><tbody>
        ${big.map(p => `<tr><td><a href="#/p/${p.id}/versionen">${esc(p.name)}</a></td><td class="tnum">${p.versions}</td><td class="tnum">${fmb(p.output)}</td><td class="tnum">${p.stack ? fmb(p.stack) : '–'}</td></tr>`).join('')}</tbody></table>` : ''}
      <h3 class="hk-h">Ungenutzte Daten</h3>
      ${d.unused.length ? `<form id="hkForm"><div class="hk-items">${d.unused.map(i => `<label class="modrow hk-item" for="hk_${i.key}"><div><b>${esc(i.label)}</b><div class="mut sm">${esc(i.detail)}${i.count > 1 ? ` · ${i.count} Einträge` : ''}</div>
          ${i.key === 'old_versions' ? `<div class="row sm" style="margin-top:6px;gap:6px"><span class="mut">Neueste behalten:</span><select id="hkKeep" style="width:auto;height:30px">${[1, 2, 3, 5].map(n => `<option ${n === i.keep ? 'selected' : ''}>${n}</option>`).join('')}</select></div>` : ''}</div>
          <span class="tnum hk-size">${i.bytes ? fmb(i.bytes) : '–'}</span><input type="checkbox" id="hk_${i.key}" name="${i.key}" data-b="${i.bytes}" ${i.default ? 'checked' : ''}><span class="switch"></span></label>`).join('')}</div>
        <div class="row" style="margin-top:12px"><button class="btn" type="submit" id="hkGo"></button><span class="mut sm">Rohdaten, Master-Stacks bestehender Objekte und die neuesten Ergebnisse bleiben immer erhalten.</span></div></form>`
      : `<p class="mut sm">${icon('check')} Nichts Ungenutztes gefunden.</p>`}`;
    const form = $('#hkForm', body);
    if (!form) return;
    const sum = () => { const sel = $$('input[type=checkbox]:checked', form); $('#hkGo', form).disabled = !sel.length;
      $('#hkGo', form).innerHTML = `${icon('trash')}Aufräumen${sel.length ? ` · ${fmb(sel.reduce((a, c) => a + +c.dataset.b, 0))}` : ''}`; };
    form.onchange = (e) => { if (e.target.id === 'hkKeep') load(keepOf()); else sum(); };
    sum();
    form.onsubmit = async (e) => {
      e.preventDefault();
      const sel = $$('input[type=checkbox]:checked', form).map(c => c.name);
      const items = d.unused.filter(i => sel.includes(i.key));
      const risky = items.filter(i => !i.default);
      if (!await confirmDlg('Ungenutzte Daten aufräumen?', `${items.map(i => `• ${esc(i.label)}${i.bytes ? ` (${fmb(i.bytes)})` : ''}`).join('<br>')}
          ${risky.length ? `<br><br><b class="bad">${risky.map(i => esc(i.label)).join(', ')} lassen sich nicht wiederherstellen.</b>` : ''}`, 'Aufräumen', !!risky.length)) return;
      $('#hkGo', form).disabled = true;
      $('#hkGo', form).innerHTML = '<span class="spinner"></span>wird aufgeräumt …';
      try {
        const r = await send('/stacking/housekeeping/cleanup', { keys: sel, keep: keepOf() });
        toast(`Aufgeräumt – ${fmb(r.freed)} freigegeben.`, 'ok');
      } catch (err) { toast(err.message, 'err'); }
      load(keepOf());
    };
  };
  $('#hkScan', panel).onclick = () => load(keepOf());
  load();
}

// ------------------------------------------------------------ Einstellungen --- //
async function stackingSettings(el) {
  const [c, st, sv, sn, gx, gp, tu] = await Promise.all([api('/stacking/reviewer'), api('/stacking/status'), api('/stacking/solver'), api('/stacking/starnet'),
    api('/stacking/graxpert'), api('/stacking/gpu'), api('/stacking/tutor')]);
  const astapOk = sv.astap && sv.astap_databases.length;
  el.innerHTML = `<div class="split"><div><form class="panel" id="tf">
      <div class="panel-h"><h2>Bild-Tutor</h2><span class="grow"></span><span class="chip ${tu.mode === 'einsteiger' ? 'acc' : ''}">${tu.mode === 'einsteiger' ? 'Einsteiger' : 'Erfahren'}</span></div>
      <p class="mut sm" style="margin:-6px 0 14px">Prüft jedes fertige Bild, erklärt Auffälligkeiten und bewertet die Aufnahmenacht (Dämmerung, Mond, Objekthöhe, Wolken, Tau, Wind, Frost) –
        mit Tipps passend zu deinem Gerät (Stellina, Vespera, Seestar … werden automatisch erkannt).</p>
      <label class="field"><span>Darstellung</span><select name="mode"><option value="einsteiger">Einsteiger – mit Erklärungen und Tipps</option>
        <option value="experte">Erfahren – kompakt, Details auf Klick</option></select></label>
      <div class="field"><span>Beobachtungsort (Breite, Länge in Grad)</span><div class="row" style="flex-wrap:nowrap">
        <input name="lat" inputmode="decimal" value="${tu.lat ?? ''}" placeholder="z. B. 49,1" style="width:120px"><input name="lon" inputmode="decimal" value="${tu.lon ?? ''}" placeholder="z. B. 8,5" style="width:120px"></div>
        <small>Für Dämmerung, Mondhöhe und Objekthöhe. Seestar &amp; Co. schreiben den Ort in die Aufnahmen, Stellina nicht. Eine Stelle hinter dem Komma reicht.</small></div>
      <label class="row" style="align-items:flex-start;gap:10px;cursor:pointer;flex-wrap:nowrap;margin-bottom:14px"><input type="checkbox" name="weather" ${tu.weather ? 'checked' : ''} style="height:auto;margin-top:4px">
        <span><b>Wetter der Aufnahmenacht abrufen</b><br><span class="mut sm">Wolken, Luftfeuchte/Taupunkt, Temperatur und Wind aus dem Open-Meteo-Archiv (kostenlos, ohne Konto).
        Dazu werden nur der auf ~10 km gerundete Ort und das Datum gesendet; Ergebnisse werden lokal gespeichert.</span></span></label>
      <label class="field"><span>KI-Einschätzung</span><select name="llm_provider"><option value="off">Aus</option><option value="lmstudio">LM Studio (lokal)</option><option value="openrouter">OpenRouter (Cloud)</option></select>
        <small>Auf Knopfdruck sieht sich ein Vision-Modell das Bild mit allen Messwerten an und ergänzt, was es sieht. Server-URL und API-Key kommen vom KI-Bildprüfer unten.</small></label>
      <div class="field"><span>Modell</span><div id="tmodel"></div></div>
      <div class="row"><button class="btn" type="submit">Speichern</button>${tu.llm_provider !== 'off' && !tu.llm_ready ? '<span class="warn sm">KI noch nicht bereit – Modell bzw. API-Key fehlt.</span>' : ''}</div>
    </form>
    <form class="panel" id="sf">
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
      <div class="field"><span>Modell</span><div id="rmodel"></div><small>Nur Modelle mit Bildeingabe; Liste kommt vom gewählten Anbieter.</small></div>
      <label class="field"><span>API-Key</span><input name="api_key" type="password" autocomplete="off" placeholder="${c.has_key ? '•••••• (gesetzt – leer lassen zum Behalten)' : 'nur für OpenRouter'}"></label>
      <label class="field"><span>Maximale Prüfrunden</span><input name="max_rounds" type="number" min="1" max="6" value="${c.max_rounds}" style="width:100px"></label>
      <div class="row"><button class="btn" type="submit">Speichern</button><span class="mut sm">Wird gespeichert und gilt auch nach einem Neustart.</span></div>
    </form>
    <div class="panel" id="hk"><div class="panel-h"><h2>Speicherbelegung</h2><span class="grow"></span><span class="chip" id="hkTotal">wird gemessen …</span>
      <button class="btn sm ghost" type="button" id="hkScan">${icon('refresh')}Neu scannen</button></div><div id="hkBody"><div class="skel" style="height:120px"></div></div></div></div>
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
    <form class="panel" id="gxp">
      <div class="panel-h"><h2>GraXpert (KI)</h2><span class="grow"></span>
        <span class="chip ${gx.found && gx.mode !== 'off' ? 'ok' : ''}">${!gx.found ? 'nicht installiert' : gx.mode === 'off' ? 'aus' : 'aktiv'}</span></div>
      <p class="mut sm" style="margin:-6px 0 12px">Entfernt Lichtverschmutzungs-Verläufe per KI, ohne großflächige Nebel abzutragen, und entrauscht den linearen Stack –
        wie im Workflow vieler Smart-Teleskop-Fotografen. Läuft einmal je Entwicklung; ohne GraXpert nutzt AstroFlow die eigenen Verfahren.</p>
      ${gx.found ? `<dl class="kv" style="margin-bottom:12px"><dt>Programm</dt><dd style="word-break:break-all">${esc(gx.path)}</dd></dl>`
      : `<ol class="mut sm steps"><li>GraXpert (kostenlos, Open Source, GPL-3) für dein System laden und installieren:
          <a class="btn sm ghost" href="${esc(gx.page)}" target="_blank" rel="noopener">${icon('down')}GitHub-Seite öffnen</a></li>
        <li>AstroFlow findet es danach im üblichen Installationsordner automatisch – sonst unten den Pfad zur GraXpert.exe angeben.</li></ol>`}
      <label class="field"><span>Standard für alle Objekte</span><select name="mode">${['all', 'bge', 'denoise', 'off'].map(v => `<option value="${v}" ${gx.mode === v ? 'selected' : ''}>${GX_LABEL[v]}</option>`).join('')}</select>
        <small>Je Objekt unter „Optionen“ änderbar. Beim ersten Lauf lädt GraXpert seine KI-Modelle selbst herunter.</small></label>
      <label class="row" style="gap:10px;cursor:pointer;flex-wrap:nowrap;margin-bottom:12px"><input type="checkbox" name="gpu" ${gx.gpu ? 'checked' : ''} style="height:auto">
        <span>Grafikkarte nutzen (deutlich schneller beim Entrauschen)</span></label>
      <label class="field"><span>GraXpert-Programm</span><input name="path" value="${esc(gx.custom || '')}" placeholder="leer = automatisch suchen"></label>
      <div class="row"><button class="btn" type="submit">Speichern</button></div>
    </form>
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
            <dt>Eingangsordner</dt><dd style="word-break:break-all">${esc(st.input_dir)}</dd>
      <dt>Ausgabeordner</dt><dd style="word-break:break-all">${esc(st.output_dir)}</dd>
      <dt>Automatik</dt><dd>${st.auto ? `an · alle ${st.scan_interval} s` : 'aus'}</dd>
      <dt>Rechen-Threads</dt><dd>${st.threads}</dd>
      <dt>Grafikkarte</dt><dd>${gp.active ? `<span class="ok">${esc(gp.device)}</span>` : gp.error ? `<span class="warn">Fehler – Stacking auf der CPU</span><div class="dim sm">${esc(gp.error)}</div>`
        : gp.mode === 'off' ? '<span class="dim">aus</span>' : !gp.pyopencl ? '<span class="dim">nicht eingerichtet (pyopencl fehlt)</span>' : '<span class="dim">keine geeignete gefunden</span>'}</dd>
      <dt>Lights</dt><dd>${st.frames?.light || 0}</dd>
      <dt>Darks / Flats / Bias</dt><dd>${st.frames?.dark || 0} / ${st.frames?.flat || 0} / ${st.frames?.bias || 0}</dd>
    </dl>
      <label class="row" style="align-items:flex-start;gap:10px;cursor:pointer;flex-wrap:nowrap;margin-top:14px"><input type="checkbox" id="gpuOn" ${gp.mode !== 'off' ? 'checked' : ''} style="height:auto;margin-top:4px">
        <span><b>Grafikkarte fürs Stacking nutzen</b><br><span class="mut sm">Ausrichten und Ausreißer-Erkennung laufen dann auf der GPU (OpenCL: AMD, NVIDIA, Intel) –
        deutlich schneller. Ohne geeignete Karte oder bei Fehlern rechnet automatisch der Prozessor.</span></span></label>
      <p class="mut sm" style="margin:14px 0 0">Tipp: Im Browser-Menü „App installieren“ wählen – AstroFlow läuft dann wie eine eigene App.</p></div></div></div>`;
  if (!el.isConnected) return;      // inzwischen weiternavigiert – die Abfragen unten gingen ins Leere
  housekeeping($('#hk'));
  $('#stf [name=gaia]').onchange = async (e) => {
    try {
      const r = await send('/stacking/storage', { gaia: e.target.checked });
      toast(r.gaia ? 'Farbkalibrierung über Gaia an.' : 'Farbkalibrierung über Sternfarben-Mittel.', 'ok');
    } catch (err) { toast('Fehler: ' + err.message, 'err'); }
  };
  $('#gpuOn').onchange = async (e) => {
    try {
      const r = await send('/stacking/gpu', { mode: e.target.checked ? 'auto' : 'off' });
      toast(r.active ? `Stacking auf der Grafikkarte (${r.device}).` : 'Stacking auf dem Prozessor.', 'ok');
      stackingSettings(el);
    } catch (err) { toast('Fehler: ' + err.message, 'err'); }
  };
  $('#gxp').addEventListener('submit', async (e) => {
    e.preventDefault();
    const f = e.target.elements;
    try {
      await send('/stacking/graxpert', { mode: f.mode.value, gpu: f.gpu.checked, path: f.path.value });
      toast('GraXpert-Einstellungen gespeichert.', 'ok');
      stackingSettings(el);
    } catch (err) { toast(err.message, 'err'); }
  });
  $('#snzip')?.addEventListener('change', async (e) => {
    const f = e.target.files[0];
    if (!f) return;
    $('#snbusy').textContent = ' wird installiert und getestet …';
    const fd = new FormData();
    fd.append('file', f);
    try {
      await api('/stacking/starnet/install', { method: 'POST', body: fd });
      toast('StarNet installiert und getestet.', 'ok');
      stackingSettings(el);
    } catch (err) { $('#snbusy').textContent = ''; toast(err.message, 'err'); }
  });
  $('#snpath')?.addEventListener('submit', async (e) => {
    e.preventDefault();
    try { await send('/stacking/starnet', { path: new FormData(e.target).get('path') }); toast('StarNet-Pfad übernommen.', 'ok'); stackingSettings(el); }
    catch (err) { toast(err.message, 'err'); }
  });
  $('#snrm')?.addEventListener('click', async () => {
    if (!await confirmDlg('StarNet entfernen?', 'AstroFlow nutzt dann wieder die eigene Sternentrennung.', 'Entfernen', true)) return;
    try { await send('/stacking/starnet', null, 'DELETE'); toast('StarNet entfernt.', 'ok'); stackingSettings(el); } catch (err) { toast(err.message, 'err'); }
  });
  $('#stf [name=delete_uploads]').onchange = async (e) => {
    try {
      const r = await send('/stacking/storage', { delete_uploads: e.target.checked });
      toast(r.delete_uploads ? 'Einzelbilder werden nach dem Stacking gelöscht.' : 'Einzelbilder bleiben erhalten.', 'ok');
    } catch (err) { toast('Fehler: ' + err.message, 'err'); }
  };
  $('[name=provider]').value = c.provider;
  // Modell-Auswahl: Liste vom gewählten (auch noch nicht gespeicherten) Anbieter
  const rf = $('#rf');
  const mp = picker($('#rmodel'), { name: 'model', value: c.model, placeholder: 'Modell wählen …',
    load: () => api(`/stacking/reviewer/models?provider=${encodeURIComponent(rf.elements.provider.value)}&base_url=${encodeURIComponent(rf.elements.base_url.value)}`) });
  rf.elements.provider.addEventListener('change', () => mp.reload());
  rf.elements.base_url.addEventListener('change', () => mp.reload());
  const tf = $('#tf');
  tf.elements.mode.value = tu.mode;
  tf.elements.llm_provider.value = tu.llm_provider;
  const tp = picker($('#tmodel'), { name: 'llm_model', value: tu.llm_model, placeholder: 'wie beim KI-Bildprüfer',
    load: () => {             // gleiche Server-URL wie tutor.llm_config: die des KI-Prüfers, außer er nutzt einen anderen Anbieter
      const prov = tf.elements.llm_provider.value, url = c.provider === prov || c.provider === 'off' ? c.base_url : '';
      return api(`/stacking/reviewer/models?provider=${encodeURIComponent(prov)}&base_url=${encodeURIComponent(url)}`);
    } });
  tf.elements.llm_provider.addEventListener('change', () => tp.reload());
  tf.onsubmit = async (e) => {
    e.preventDefault();
    const f = Object.fromEntries(new FormData(tf));
    f.weather = tf.elements.weather.checked;
    try {
      await send('/stacking/tutor', f);
      tutorCache.clear();
      toast('Bild-Tutor gespeichert.', 'ok');
      stackingSettings(el);
    } catch (err) { toast(err.message, 'err'); }
  };
  $('[name=backend]').value = sv.backend;
  $('#sf').onsubmit = async (e) => {
    e.preventDefault();
    const f = Object.fromEntries(new FormData(e.target));
    f.timeout = +f.timeout;
    try {
      const r = await send('/stacking/solver', f);
      toast(r.active.length ? `Plate-Solving aktiv (${r.active.map(b => SOLVER_LABEL[b] || b).join(', ')}).` : 'Gespeichert – noch kein Solver einsatzbereit.', r.active.length ? 'ok' : 'err');
      stackingSettings(el);
    } catch (err) { toast('Fehler: ' + err.message, 'err'); }
  };
  $('#rf').onsubmit = async (e) => {
    e.preventDefault();
    const f = Object.fromEntries(new FormData(e.target));
    f.max_rounds = +f.max_rounds;
    try { await send('/stacking/reviewer', f); toast('KI-Prüfer gespeichert.', 'ok'); } catch (err) { toast('Fehler: ' + err.message, 'err'); }
  };
}

// ------------------------------------------------ Beobachtungsstatistik --- //
async function statisticsView(el) {
  crumbs([['Bilder', '#/bilder'], ['Statistik']]);
  let year = '', ticket = 0, signature = '';
  const date = s => new Date(s + 'T12:00:00Z').toLocaleDateString('de-DE', { timeZone: 'UTC' });
  const month = s => new Date(s + '-01T12:00:00Z').toLocaleDateString('de-DE', { month: 'short', year: 'numeric', timeZone: 'UTC' });
  const weekdays = ['Montag', 'Dienstag', 'Mittwoch', 'Donnerstag', 'Freitag', 'Samstag', 'Sonntag'];
  el.innerHTML = `<div class="pagehead"><div><h1>Deine Beobachtungsaktivität</h1><p class="mut">Wann du den Himmel am häufigsten aufgenommen hast.</p></div><span class="grow"></span>
    <select id="obs-year" aria-label="Beobachtungsjahr"><option value="">Alle Jahre</option></select></div><div id="obs-body"></div>`;
  const body = el.querySelector('#obs-body'), select = el.querySelector('#obs-year');
  select.onchange = () => { year = select.value; signature = ''; load(); };
  function bars(rows, label) {
    const max = Math.max(1, ...rows.map(b => b.frames));
    return `<div class="obs-bars">${rows.map(b => `<div class="obs-bar" title="${nf.format(b.frames)} Aufnahmen · ${dur(b.exposure)} Belichtung">
      <span>${esc(label(b.key))}</span><div class="obs-track"><div class="obs-fill" style="width:${100 * b.frames / max}%;${b.frames ? '' : 'min-width:0'}"></div></div><span class="tnum">${nf.format(b.frames)}</span></div>`).join('')}</div>`;
  }
  async function load() {
    const current = ++ticket;
    try {
      const d = await api('/stacking/statistics' + (year ? '?year=' + year : ''));
      if (current !== ticket) return;
      const sig = JSON.stringify(d);
      if (sig === signature) return;
      signature = sig;
      select.innerHTML = '<option value="">Alle Jahre</option>' + d.years.map(y => `<option value="${y}">${y}</option>`).join('');
      select.value = year;
      const maxHour = Math.max(1, ...d.hours.map(b => b.frames));
      body.innerHTML = `<div class="hstats obs-summary"><div><b class="tnum">${nf.format(d.frames)}</b><small>Aufnahmen</small></div><div><b class="tnum">${d.nights}</b><small>Beobachtungsnächte</small></div>
        <div><b class="tnum">${dur(d.exposure)}</b><small>Belichtung</small></div><div><b class="tnum">${d.objects}</b><small>Objekte</small></div></div>
        <p class="mut sm">Aufnahmezeit aus FITS (UTC), nicht der Uploadzeitpunkt. Nächte zählen von 12 bis 12 Uhr UTC; Monate und Wochentage gehören zum Nachtbeginn.
        Gezählt werden alle importierten Light-Aufnahmen, einschließlich verworfener und inzwischen gelöschter Originale. Duplikate und Bildversionen zählen nicht.
        ${d.missing_dates ? `${nf.format(d.missing_dates)} Aufnahmen ohne gültigen Aufnahmezeitpunkt sind ausgeschlossen (über alle Jahre).` : ''}</p>
        ${d.frames ? `<p class="mut">${date(d.first)} – ${date(d.last)} · Aktivster Monat: <b>${esc(month(d.top_month.key))}</b> (${nf.format(d.top_month.frames)} Aufnahmen)</p>
        <div class="obs-stats"><section class="panel"><h2>Aufnahmen je Monat</h2>${bars(d.months, month)}</section>
        <section class="panel"><h2>Wochentage der Beobachtungsnächte</h2>${bars(d.weekdays, k => weekdays[k])}</section>
        <section class="panel obs-wide"><h2>Aufnahmezeiten (UTC)</h2><div class="obs-hours">${d.hours.map(b => `<div class="obs-hour" title="${String(b.key).padStart(2, '0')}:00–${String(b.key).padStart(2, '0')}:59 UTC · ${nf.format(b.frames)} Aufnahmen · ${dur(b.exposure)}"><i style="height:${100 * b.frames / maxHour}%"></i><small>${b.key}</small></div>`).join('')}</div></section>
        <section class="panel obs-wide"><h2>Deine aktivsten Nächte</h2><p class="mut sm">Nach Anzahl der Aufnahmen · Belichtung rechts</p>${d.top_nights.map(b => `<div class="obs-night"><b>${date(b.key)}</b><span class="tnum">${nf.format(b.frames)} Aufnahmen · ${dur(b.exposure)}</span></div>`).join('')}</section></div>`
          : '<div class="empty"><h2>Noch keine Aufnahmezeitpunkte</h2>Für diesen Zeitraum liegen keine passenden FITS-Metadaten vor.</div>'}`;
    } catch (e) { if (current === ticket) body.innerHTML = `<div class="panel">${esc(e.message)}</div>`; }
  }
  await load();
  return { update: load, destroy: () => { ticket++; } };
}

// ---------------------------------------------------------- Registrierung --- //
AF.register('stacking', {
  nav: [{ href: '#/bilder', label: 'Galerie', icon: 'grid', key: 'gallery' }, { href: '#/import', label: 'Import', icon: 'upload', key: 'import', sec: true },
    { href: '#/bilder/statistik', label: 'Statistik', icon: 'pulse', key: 'statistics', sec: true }],
  routes: [
    [/^\/bilder$/, 'gallery', galleryView],
    [/^\/bilder\/statistik$/, 'statistics', statisticsView],
    [/^\/p\/(\d+)(?:\/(\w+))?$/, 'gallery', projectView],
    [/^\/import$/, 'import', importView],
  ],
  settings: stackingSettings,
  status: (st, badge) => badge('gallery', st.modules.stacking?.busy_jobs, true),
  async home(st) {
    const s = st.modules.stacking || {}, projects = (await api('/stacking/projects')).filter(p => !p.options?.gallery_parent);
    const done = projects.filter(p => p.result).sort((a, b) => b.result.created - a.result.created);
    const tot = projects.reduce((a, p) => a + (p.result?.exposure || 0), 0), busy = projects.filter(p => running(p.job));
    return `<div class="hhead">${icon('grid')}<div><h2>Bilder</h2><div class="mut sm">Rohbilder → fertiges Astrofoto</div></div><span class="grow"></span><a class="linkbtn" href="#/bilder">Galerie ${icon('arrow')}</a></div>
      <div class="hstats"><div><b class="tnum">${projects.length}</b><small>Objekte</small></div><div><b class="tnum">${dur(tot)}</b><small>Belichtung</small></div><div><b class="tnum">${nf.format(s.frames?.light || 0)}</b><small>Aufnahmen</small></div></div>
      ${busy.length ? `<div class="hlist">${busy.slice(0, 2).map(p => `<a href="#/p/${p.id}"><span class="spinner"></span><b>${esc(p.name)}</b><span class="mut sm">${esc(p.job.stage || 'wartet')} ${p.job.progress ? Math.round(p.job.progress * 100) + ' %' : ''}</span></a>`).join('')}</div>` : ''}
      ${done.length ? `<div class="hthumbs">${done.slice(0, 6).map(p => `<a href="#/p/${p.id}" title="${esc(p.name)}"><img src="/api/stacking/results/${p.result.id}/thumb" alt="" loading="lazy"><span>${esc(p.name)}</span></a>`).join('')}</div>`
        : `<p class="mut sm">Noch keine fertigen Bilder. Lege FITS-Dateien in den Eingangsordner oder <a href="#/import">importiere sie</a>.</p>`}`;
  },
  async palette() {
    const projects = (await api('/stacking/projects')).filter(p => !p.options?.gallery_parent);
    return [['refresh', 'Eingangsordner scannen', 'nach neuen Aufnahmen suchen', () => scan()], ...projects.map(p => ['star', p.name, running(p.job) ? 'wird verarbeitet' : `${p.total} Aufnahmen${p.result ? ' · ' + dur(p.result.exposure) : ''}`,
      `#/p/${p.id}`, p.result ? `/api/stacking/results/${p.result.id}/thumb` : null])];
  },
  drop(files) {
    const supported = files.filter(f => IMPORT_FILE.test(f.name));
    if (!supported.length) return false;
    location.hash = '#/import';
    setTimeout(() => upload(supported), 150);
    return true;
  },
});
