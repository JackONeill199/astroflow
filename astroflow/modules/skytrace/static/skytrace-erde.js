'use strict';
/* SkyTrace „3D-Erde“ (0.35.0): EINE Erd-Ansicht für Standorte, Standortsuche und Analysen.
   Zentrale Seite #/skytrace/erde[/<Nacht>] (Kontext „Nächte & Standorte“) bzw. #/skytrace/3d (Kontext „Analysen“) und eingebettet im
   Lauf-Reiter „3D“ (embed, Lauf fest). Ein Globus (three.js aus /vendor/), Ebenen einzeln weich ein-/ausblendbar:
   Orte der Liste · Ort je Nacht · Heatmap der Standortsuche (standortsuche-karten/<Nacht>.json) · Vorschläge mit Unsicherheitsellipse ·
   Satellitenbahnen und Teleskop (Lauf-Ebene aus skytrace-3d.js, gleiche Darstellung in beiden Kontexten) · Grenzen/Küsten · Tag/Nacht-Grenze.
   Die Erde ist eine Gruppe (`earth`): im Analysen-Kontext dreht sie sich mit der Zeit des Laufs, alles Erdfeste dreht mit.
   „Nachtsicht“: liegt, was gezeigt wird, auf der Nachtseite, hellt der Erd-Shader die Nachtseite weich in ein gedämpftes Blaugrün auf.
   Kartenkacheln (OSM) nur auf Knopfdruck und erst bei starkem Zoom. Achsen: ECEF (x, y, z) → three (x, z, −y)/a, Nordpol = +y, WGS84. */
(() => {
const LIB = '/vendor/', TEX = '/m/skytrace/vendor/earth-2048.jpg', T = '/skytrace';
const R_KM = 6378.137, F_WGS = 1 / 298.257223563, E2 = F_WGS * (2 - F_WGS), D2R = Math.PI / 180;
const reduced = () => matchMedia('(prefers-reduced-motion: reduce)').matches;
const LAYERS = [
  ['sites', 'Orte der Liste', '#c9d2e3', 'Alle Standorte aus Einstellungen → Standorte'],
  ['current', 'Ort je Nacht', '#f2d385', 'Ort, der der Nacht bisher zugeordnet ist (Ring)'],
  ['heat', 'Heatmap der Standortsuche', '#f06e32', 'Bodenpunkte, von denen aus die Spuren der Nacht zu sehen waren'],
  ['proposals', 'Vorschläge mit Unsicherheit', '#6b86ff', 'Vorgeschlagener Ort mit 1σ-Ellipse'],
  ['runs', 'Satellitenbahnen und Teleskop', '#5eead4', 'Teleskopweg, Blickfeld und zugeordnete Bahnen der Analysen'],
  ['borders', 'Grenzen und Küsten', '#9fb0d9', 'Natural Earth, ohne Netz'],
  ['terminator', 'Tag/Nacht-Grenze', '#fbbf24', 'Beleuchtung zur Aufnahmezeit'],
];
const DEFAULT_ON = { sites: true, current: true, heat: true, proposals: true, runs: false, borders: true, terminator: true };
const ease = (k) => k < .5 ? 4 * k * k * k : 1 - Math.pow(-2 * k + 2, 3) / 2;
const clamp = (v, a, b) => Math.min(b, Math.max(a, v));
let lib = null;
function load() {
  return lib ||= Promise.all([import(LIB + 'three.module.min.js'), import(LIB + 'OrbitControls.js')])
    .then(([THREE, c]) => ({ THREE, OrbitControls: c.OrbitControls })).catch(e => { lib = null; throw e; });
}

// Sonnenstand (vereinfachte Formel, ~0,01°): Unterpunkt der Sonne
function sunLatLon(t) {
  const d = t / 86400 + 2440587.5 - 2451545.0, g = (357.529 + .98560028 * d) * D2R, q = 280.459 + .98564736 * d;
  const L = (q + 1.915 * Math.sin(g) + .02 * Math.sin(2 * g)) * D2R, e = (23.439 - 3.6e-7 * d) * D2R;
  const ra = Math.atan2(Math.cos(e) * Math.sin(L), Math.cos(L)), dec = Math.asin(Math.sin(e) * Math.sin(L));
  const gmst = ((280.46061837 + 360.98564736629 * d) % 360) * D2R;
  return [dec / D2R, (((ra - gmst) / D2R) % 360 + 540) % 360 - 180];
}
// Zeitpunkt einer Nacht ohne genauere Angabe: Mitternacht Ortszeit an der Länge der Region
const nightTime = (night, lon = 0) => Date.parse(night + 'T00:00:00Z') / 1000 + (24 - lon / 15) * 3600;
const nightOfTime = (t) => new Date((t - 43200) * 1000).toISOString().slice(0, 10);          // wie core/sites.night_of: UTC − 12 h
const fmtNight = (night, long = false) => new Date(night + 'T12:00:00Z').toLocaleDateString('de-DE', long ? { weekday: 'short', day: '2-digit', month: '2-digit', year: 'numeric', timeZone: 'UTC' } : { day: '2-digit', month: '2-digit', year: 'numeric', timeZone: 'UTC' });
const helpers = () => window.SkyTraceHelpers;
const HELP = 'Ziehen dreht · Mausrad/zwei Finger zoomen · nah heran = flache Karte · Antippen zeigt Details';          // aus skytrace.js (Heatmap-Zellen, Ortsmarken, Übernehmen, Texte, Läufe)

const MARKUP = (page) => `<div class="ste-main">
    <div class="ste-stage"><canvas aria-label="Drehbarer Globus mit Standorten, Heatmap der Standortsuche, Teleskopweg und Bahnen"></canvas><div class="ste-labels"></div>
      <div class="ste-tools"><div class="seg st3d-views" hidden aria-label="Kamera"><button data-v="earth" class="on">Erde</button><button data-v="scope">Teleskop</button><button data-v="pass">Durchgang</button><button data-v="sat">Folgen</button></div>
        <span class="ste-pill ste-nvpill" hidden>${icon('target')}Nachtsicht</span><span class="grow"></span>
        <span class="st3d-schema" hidden title="Kuppel, Himmel und Ziel sind nicht maßstäblich">Schema</span>
        <button class="btn ghost sm ste-mode" title="Zwischen Globus und flacher Karte wechseln (Hineinzoomen wechselt von selbst)">${icon('layers')}<span>Karte</span></button>
        <button class="btn ghost sm ste-home" title="Ganze Region zeigen">${icon('fit')}<span>Übersicht</span></button>
        <button class="btn ghost icon sm ste-fs" title="Vollbild (F)" aria-label="Vollbild">${icon('expand')}</button>
        <button class="btn ghost icon sm ste-paneltg" title="Ebenen und Nächte ein-/ausblenden" aria-label="Seitenleiste" aria-expanded="true">${icon('sidebar')}</button></div>
      <div class="ste-msg"><span class="spinner"></span>Globus wird geladen …</div>
      <div class="ste-legend" hidden><span class="ramp"></span><span class="ste-legend-t"></span><button type="button" class="btn ghost sm ste-legend-off" title="Heatmap ausblenden – wieder einblenden unter Ebenen oder in der Karte der Nacht (Deckkraft: Regler unter Ebenen)">Ausblenden</button></div>
      <div class="ste-tiles" hidden><div class="seg"><button data-tiles="" class="on" title="Erdbild, ohne Netz">Erdbild</button><button data-tiles="osm" title="Kacheln von OpenStreetMap laden – der Kartendienst sieht den Ausschnitt">Straßenkarte</button><button data-tiles="topo" title="Kacheln von OpenTopoMap laden – der Kartendienst sieht den Ausschnitt">Gelände</button></div><span class="ste-attr sm"></span></div>
      <div class="ste-readout" hidden><span class="ste-scale"><i></i><b></b></span><span class="ste-coord tnum"></span></div>
      <div class="ste-card" hidden></div>
      <div class="ste-pick" role="menu" aria-label="Mehrere Objekte an dieser Stelle" hidden></div><div class="ste-tip" hidden></div>
      <div class="ste-help">${HELP}</div></div>
    <div class="st3d-bar" hidden><button class="btn icon ghost sm" data-play title="Abspielen" aria-label="Abspielen">${icon('play')}</button>
      <div class="st3d-range"><canvas class="st3d-strip" aria-hidden="true"></canvas><input type="range" aria-label="Zeit der Beobachtung"></div>
      <span class="st3d-time tnum"></span><select aria-label="Abspielgeschwindigkeit"><option value="1">1×</option><option value="10">10×</option>
        <option value="60">60×</option><option value="300" selected>300×</option><option value="1200">1200×</option></select></div></div>
  <aside class="ste-side">
    ${page ? `<div class="seg ste-ctx" role="tablist" aria-label="Inhalt"><button data-ctx="nights" role="tab">Nächte &amp; Standorte</button><button data-ctx="runs" role="tab">Analysen</button></div>` : ''}
    <section class="ste-sec-nights" hidden><h3 class="ste-sec">Nächte</h3><div class="ste-nightbar"><button class="btn ghost icon sm" data-step="-1" aria-label="Vorige Nacht">${icon('back')}</button>
      <input type="range" class="ste-nightrange" min="0" max="0" value="0" aria-label="Nacht wählen"><button class="btn ghost icon sm" data-step="1" aria-label="Nächste Nacht">${icon('arrow')}</button></div>
      <div class="ste-nights" role="listbox" aria-label="Nächte"></div></section>
    <section class="st3d-runs" hidden><h3 class="ste-sec">Analysen</h3>
      <input type="search" class="st3d-rq" placeholder="Analyse suchen …" aria-label="Analyse suchen">
      <div class="seg st3d-rf" aria-label="Filter"><button data-v="hits">Mit Kandidaten</button><button data-v="all">Alle</button></div>
      <div class="st3d-rl" role="listbox" aria-label="Analysen"></div>${page ? '<a class="btn ghost sm ste-runopen" href="#">Zur Analyse</a>' : ''}</section>
    <section class="ste-sec-objects" hidden><h3 class="ste-sec">Objekte</h3><div class="st3d-legend"></div></section>
    <section><h3 class="ste-sec">Ebenen</h3><div class="ste-layers"></div></section>
    <section><h3 class="ste-sec">Nachtsicht</h3><div class="seg ste-nv" aria-label="Nachtsicht"><button data-nv="auto">Auto</button><button data-nv="on">An</button><button data-nv="off">Aus</button></div></section>
    <p class="st3d-note mut sm"></p><p class="ste-note mut sm"></p></aside>`;

// Zentrale Seite. route: { ctx: 'nights'|'runs'|null, night }
async function view(el, route = {}) {
  const H = helpers();
  crumbs([['SkyTrace', '#/skytrace'], ['3D-Erde']]);
  // Kopf ohne Erklärtext (Nutzerwunsch: mehr Platz für die Karte) – die Erklärung steht hinter dem Info-Knopf
  el.innerHTML = `<div class="pagehead ste-head"><div class="ste-title"><h1>3D-Erde</h1>
        <button class="btn ghost icon sm ste-info" aria-expanded="false" aria-controls="steInfo" title="Was zeigt die 3D-Erde und wie bedient man sie?" aria-label="Hilfe zur 3D-Erde">?</button></div>
      <div class="page-actions"><a class="btn ghost" href="#/skytrace/standorte">${icon('list')}Standorte aller Nächte</a></div></div>
    <div class="ste-infopop panel" id="steInfo" hidden><button class="btn ghost icon sm ste-info-x" aria-label="Schließen">${icon('x')}</button>
      <h3>Was die 3D-Erde zeigt</h3>
      <p class="readable">Standorte, die Heatmap der Standortsuche, Teleskopweg und Satellitenbahnen auf einem Globus. <b>Nächte &amp; Standorte</b> zeigt je Nacht den bisherigen Ort,
        Vorschläge mit Unsicherheit und die Heatmap der zurückgerechneten Bodenpunkte (hell = viele verschiedene Spuren treffen sich). <b>Analysen</b> zeigt einen Lauf mit
        Zeitleiste: wohin das Teleskop geschaut hat und welche Satelliten das Bildfeld gekreuzt haben.</p>
      <h3>Bedienung</h3>
      <ul class="readable"><li><b>Ziehen</b> dreht den Globus bzw. verschiebt die Karte – der angefasste Punkt bleibt unter dem Zeiger.</li>
        <li><b>Mausrad / zwei Finger</b> zoomen zum Zeiger. Nah heran wird der Globus zur <b>flachen Karte</b>, weit heraus wieder zur Kugel; „Karte/Globus“ wechselt direkt.</li>
        <li><b>Antippen</b> eines Orts oder Vorschlags öffnet seine Karte mit „Übernehmen“. ←/→ wechseln die Nacht, F schaltet Vollbild.</li>
        <li>Rechts lassen sich <b>Ebenen</b> einzeln ein- und ausschalten; der Knopf oben rechts klappt das Panel ein.</li>
        <li><b>Nachtsicht</b>: liegt die Region auf der Nachtseite, wird sie aufgehellt (Auto/An/Aus).</li>
        <li><b>Straßenkarte/Gelände</b> laden Kartenkacheln von OpenStreetMap bzw. OpenTopoMap – nur auf Knopfdruck, der Kartendienst sieht dann den Ausschnitt. Sonst bleibt alles auf dem Rechner.</li></ul></div>
    <div class="ste" id="steHost">${MARKUP(true)}</div>`;
  const infoBtn = $('.ste-info', el), infoPop = $('#steInfo', el);
  $('.ste-head', el).append(infoPop);                    // als Popover am Kopf: die Karte springt beim Öffnen nicht
  const outside = (e) => { if (!infoPop.hidden && !e.target.closest('#steInfo,.ste-info')) info(false); };
  document.addEventListener('pointerdown', outside);
  const info = (open) => { infoPop.hidden = !open; infoBtn.setAttribute('aria-expanded', String(open)); };
  infoBtn.onclick = () => info(infoPop.hidden);
  $('.ste-info-x', el).onclick = () => { info(false); infoBtn.focus(); };
  infoPop.addEventListener('keydown', (e) => { if (e.key === 'Escape') { info(false); infoBtn.focus(); } });
  const ctx = route.ctx || 'nights';                     // #/skytrace/erde = Nächte & Standorte, #/skytrace/3d = Analysen
  const C = await component($('#steHost', el), el, { page: true, ctx, night: route.night || null, H });
  if (!C) { document.removeEventListener('pointerdown', outside); return; }
  const onKey = (e) => {
    if (e.ctrlKey || e.metaKey || e.altKey || e.target.closest('input,select,textarea')) return;
    if (e.key === 'f' || e.key === 'F') { C.fullscreen(); e.preventDefault(); }
    else if ((e.key === 'ArrowLeft' || e.key === 'ArrowRight') && C.ctx() === 'nights') { C.step(e.key === 'ArrowRight' ? 1 : -1); e.preventDefault(); }
  };
  document.addEventListener('keydown', onKey);
  return {
    destroy() { C.dispose(); document.removeEventListener('keydown', onKey); document.removeEventListener('pointerdown', outside); },
    actions: () => [['globe', '3D-Erde: Übersicht', 'Ganze Region zeigen', () => C.home()]],
  };
}

// Lauf-Reiter „3D“: dieselbe Komponente mit festem Lauf (ohne Lauf-/Nachtliste). → { dispose, fullscreen }; host._st3d.state() = Lauf, host._ste.state() = Globus
function embed(host, { run, focus = null, session = null, onCandidate = null } = {}) {
  host.classList.add('ste', 'ste-embed');
  host.innerHTML = MARKUP(false);
  let C = null, dead = false;
  component(host, host, { page: false, ctx: 'runs', run, focus, session, onCandidate, H: helpers() }).then(c => { if (dead) c?.dispose(); else C = c; });
  return {
    fullscreen: () => { if (document.fullscreenElement) document.exitFullscreen(); else host.requestFullscreen?.().catch(() => {}); },
    dispose() { dead = true; C?.dispose(); host._st3d = host._ste = null; if (document.fullscreenElement === host) document.exitFullscreen(); },
  };
}

// Gemeinsamer Aufbau: Daten laden, Globus anlegen, Kontexte. opts: { page, ctx, night, run, focus, session, onCandidate, H }
async function component(host, el, opts) {
  const { page, H } = opts, $d = (s) => host.querySelector(s);
  let data = null, sites = [], maps = [], runs = [], scene = null, dead = false;
  const heats = {};
  const msg = (html) => { const m = $d('.ste-msg'); m.innerHTML = html; m.hidden = false; m.classList.add('hint'); };
  try {
    const [d, s, r] = await Promise.all([api(T + '/sitesearch/nights'), api('/sites?nights=false').catch(() => ({})), page ? api(T + '/runs').catch(() => []) : Promise.resolve([])]);
    data = d; sites = (s.list || []).filter(x => x.lat != null && x.lon != null); maps = d.maps || []; runs = r || [];
  } catch (e) { msg(`${icon('alert')}<span>${esc(e.message)}</span>`); return null; }
  if (!host.isConnected) return null;
  const nights = (data.result?.nights || []).slice().sort((a, b) => a.night < b.night ? -1 : 1);
  const S = {
    ctx: opts.ctx, night: nights.some(n => n.night === opts.night) ? opts.night : null,
    on: { ...DEFAULT_ON, ...store.get('skytrace.erde.layers', {}) }, nv: store.get('skytrace.erde.nv', 'auto'),
    heatOp: Math.min(1, Math.max(.1, +store.get('skytrace.erde.heatop', .4) || .4)),
    runId: opts.run || (runs.some(r => r.id === store.get('skytrace.3drun')) ? store.get('skytrace.3drun') : runs[0]?.id) || null,
    onlyHits: store.get('skytrace.3dhits', runs.some(r => r.candidates)), q: '',
  };
  if (S.ctx === 'runs') S.on.runs = true;                // im Analysen-Kontext ist der Lauf das Thema
  if (!S.night && !opts.night) S.night = (nights.filter(n => maps.includes(n.night) || n.proposal?.kind === 'global').at(-1) || nights.filter(n => n.proposal && !n.applied).at(-1))?.night || null;
  const nightOf = (night) => nights.find(n => n.night === night);

  // ---- Seitenleiste: Nächte ----
  const range = $d('.ste-nightrange'), listEl = $d('.ste-nights');
  range.max = String(Math.max(0, nights.length));        // 0 = alle Nächte, 1… = Nacht
  const edges = () => { listEl.classList.toggle('fade-t', listEl.scrollTop > 2); listEl.classList.toggle('fade-b', listEl.scrollTop + listEl.clientHeight < listEl.scrollHeight - 2); };
  listEl.addEventListener('scroll', edges, { passive: true });
  function drawNights() {
    const idx = S.night ? nights.findIndex(n => n.night === S.night) + 1 : 0;
    range.value = String(idx);
    listEl.innerHTML = `<button role="option" data-n="" class="${S.night ? '' : 'on'}" aria-selected="${!S.night}"><span><b>Alle Nächte</b><small>${nights.length} Nächte · ${maps.length} mit Heatmap</small></span></button>`
      + nights.slice().reverse().map(n => {
        const [label, cls] = n.applied ? ['Übernommen', 'ok'] : n.proposal?.kind === 'global' ? ['Unbekannter Ort', 'acc'] : (H.NIGHT_STATUS[n.status] || [n.status || '–', '']);
        return `<button role="option" data-n="${esc(n.night)}" class="${n.night === S.night ? 'on' : ''}" aria-selected="${n.night === S.night}">
          <span><b>${esc(fmtNight(n.night, true))}</b><small>${esc((n.observations || []).map(o => H.shortName(o.session)).filter((v, i, a) => a.indexOf(v) === i).join(' · '))}</small></span>
          ${maps.includes(n.night) ? `<i class="ste-heaticon" title="Heatmap vorhanden"></i>` : ''}<span class="chip ${cls}">${esc(label)}</span></button>`;
      }).join('');
    const on = listEl.querySelector('.on');                // nur die Liste scrollen, nie die Seite (Handy)
    if (on && on.offsetTop < listEl.scrollTop) listEl.scrollTop = on.offsetTop;
    else if (on && on.offsetTop + on.offsetHeight > listEl.scrollTop + listEl.clientHeight) listEl.scrollTop = on.offsetTop + on.offsetHeight - listEl.clientHeight;
    edges();
  }
  // ---- Seitenleiste: Analysen (wie bisher #/skytrace/3d) ----
  const runsBox = $d('.st3d-runs'), variant = page ? H.runVariants(runs) : () => '';
  function drawRuns() {
    if (!page) return;
    $$('.st3d-rf button', runsBox).forEach(b => b.classList.toggle('on', (b.dataset.v === 'hits') === S.onlyHits));
    const words = S.q.toLowerCase().split(/\s+/).filter(Boolean);
    const shown = runs.filter(r => (!S.onlyHits || r.candidates || r.id === S.runId) && words.every(w => (H.shortName(r.title) + ' ' + (H.runDate(r.id) || '')).toLowerCase().includes(w)));
    $('.st3d-rl', runsBox).innerHTML = shown.map(r => `<button role="option" data-run="${esc(r.id)}" class="${r.id === S.runId ? 'on' : ''}" aria-selected="${r.id === S.runId}">
        <span class="st3d-rt">${r.preview ? `<img src="${H.media(r.preview)}" alt="" loading="lazy">` : icon('orbit')}</span>
        <span class="st3d-rn"><b>${esc(H.shortName(r.title))}</b><small>${esc(H.runDate(r.id) || ago(r.modified))}${variant(r).replace(/<[^>]+>/g, '').replace(/^ /, ' · ')}</small></span>
        ${r.candidates ? `<span class="chip acc">${r.candidates}</span>` : ''}</button>`).join('') || (runs.length ? '<p class="mut sm">Keine Analyse passt.</p>' : `<p class="mut sm">Noch keine Analysen. <a href="#/skytrace/neu">Neue Analyse</a></p>`);
    const open = $d('.ste-runopen'); if (open) { open.hidden = !S.runId; open.href = `#/skytrace/${encodeURIComponent(S.runId || '')}/kandidaten`; }
    const on = $('.st3d-rl .on', runsBox), rl = $('.st3d-rl', runsBox);
    if (on && (on.offsetTop < rl.scrollTop || on.offsetTop + on.offsetHeight > rl.scrollTop + rl.clientHeight)) rl.scrollTop = on.offsetTop - 4;
  }
  if (page) {
    $('.st3d-rq', runsBox).oninput = (e) => { S.q = e.target.value; drawRuns(); };
    $('.st3d-rf', runsBox).onclick = (e) => { const b = e.target.closest('button[data-v]'); if (!b) return; S.onlyHits = b.dataset.v === 'hits'; store.set('skytrace.3dhits', S.onlyHits); drawRuns(); };
    $('.st3d-rl', runsBox).onclick = (e) => { const b = e.target.closest('[data-run]'); if (!b || b.dataset.run === S.runId) return; S.runId = b.dataset.run; store.set('skytrace.3drun', S.runId); drawRuns(); scene?.openRun(S.runId); };
  }
  const layersEl = $d('.ste-layers');
  const drawLayers = () => { layersEl.innerHTML = LAYERS.map(([k, name, c, tip]) => `<label class="ste-layer" style="--c:${c}" title="${esc(tip)}"><input type="checkbox" data-layer="${k}"${S.on[k] ? ' checked' : ''}>
    <i></i><span>${esc(name)}</span></label>${k === 'heat' ? `<label class="ste-layer-op${S.on.heat ? '' : ' off'}" title="Deckkraft des heißesten Bereichs der Heatmap"><span>Deckkraft</span><input type="range" class="ste-heatop" min="10" max="100" step="5" value="${Math.round(S.heatOp * 100)}" aria-label="Deckkraft der Heatmap"><b class="tnum">${Math.round(S.heatOp * 100)} %</b></label>` : ''}`).join(''); };
  // Ebene schalten bzw. Deckkraft setzen – gemeinsam für Ebenenliste, Legende und Karte der Nacht
  const setLayer = (k, on) => { S.on[k] = on; store.set('skytrace.erde.layers', S.on); drawLayers(); scene?.layersChanged(k); };
  const setHeatOp = (v) => {
    S.heatOp = Math.min(1, Math.max(.1, v)); store.set('skytrace.erde.heatop', S.heatOp);
    const t = Math.round(S.heatOp * 100) + ' %';
    $$('.ste-heatop', host).forEach(i => { if (+i.value !== Math.round(S.heatOp * 100)) i.value = Math.round(S.heatOp * 100); });
    $$('.ste-layer-op b', host).forEach(b => { b.textContent = t; });
    scene?.heatOpacity();
  };
  const drawNv = () => $$('.ste-nv button', host).forEach(b => b.classList.toggle('on', b.dataset.nv === S.nv));
  const drawCtx = () => {
    $$('.ste-ctx button', host).forEach(b => { b.classList.toggle('on', b.dataset.ctx === S.ctx); b.setAttribute('aria-selected', b.dataset.ctx === S.ctx); });
    $d('.ste-sec-nights').hidden = S.ctx !== 'nights';
    runsBox.hidden = !page || S.ctx !== 'runs';
    $d('.ste-sec-objects').hidden = S.ctx !== 'runs';
    $d('.st3d-note').hidden = S.ctx !== 'runs';
  };
  drawNights(); drawRuns(); drawLayers(); drawNv(); drawCtx();
  async function loadHeats(list) {
    const want = list.filter(n => maps.includes(n) && !(n in heats));
    for (let i = 0; i < want.length; i += 100) {             // Endpunkt nimmt höchstens 120 Nächte je Anfrage
      try { Object.assign(heats, (await api(T + '/sitesearch/nights/heatmaps?nights=' + encodeURIComponent(want.slice(i, i + 100).join(',')))).heat || {}); }
      catch { /* ohne Heatmap – beim Wählen der Nacht erneut versucht */ }
    }
  }
  await loadHeats(maps);
  if (!host.isConnected) return null;
  let mods;
  try { mods = await load(); } catch (e) { msg(`${icon('alert')}<span>3D-Bibliothek konnte nicht geladen werden: ${esc(e.message)}</span>`); return null; }
  if (!host.isConnected) return null;
  const onCandidate = opts.onCandidate || (page ? (key) => H.openCandidate(S.runId, key) : null);
  try { scene = globe(host, mods, { nights, sites, heats, S, H, nightOf, onPick: (night) => pickNight(night), onCompute: computeHeat, setLayer, setHeatOp, onCandidate, focus: opts.focus, session: opts.session, page }); host._ste = scene; }
  catch (e) { msg(`${icon('alert')}<span>3D-Darstellung nicht möglich (WebGL nicht verfügbar?): ${esc(e.message)}</span>`); return null; }
  $d('.ste-msg').hidden = true;

  function setHash() {
    if (!page) return;
    const url = S.ctx === 'runs' ? '#/skytrace/3d' : '#/skytrace/erde' + (S.night ? '/' + S.night : '');
    if (location.hash !== url) history.replaceState(null, '', url);
    const r = runs.find(x => x.id === S.runId);
    crumbs([['SkyTrace', '#/skytrace'], ['3D-Erde', '#/skytrace/erde'], ...(S.ctx === 'runs' ? (r ? [[H.shortName(r.title)]] : []) : S.night ? [[fmtNight(S.night)]] : [])]);
  }
  function pickNight(night, { card = true } = {}) {
    if (S.ctx !== 'nights') { scene.nightCard(night); return; }
    S.night = night || null;
    drawNights(); setHash();
    scene.setNight(S.night, { card: card && !!S.night, fly: true });
    if (S.night && maps.includes(S.night) && !heats[S.night]) {         // fehlte beim Laden (Netz, Fehler): einzeln nachholen
      const want = S.night; delete heats[want];
      loadHeats([want]).then(() => { if (heats[want] && scene) scene.heatChanged(want); });
    }
  }
  function setCtx(c) {
    if (c === S.ctx) return;
    S.ctx = c; drawCtx();
    if (c === 'runs') { S.on.runs = true; drawLayers(); scene.openRun(S.runId); }
    else { scene.closeRun(); pickNight(S.night, { card: false }); }
    setHash();
  }
  listEl.onclick = (e) => { const b = e.target.closest('[data-n]'); if (b) pickNight(b.dataset.n); };
  range.oninput = () => pickNight(+range.value ? nights[+range.value - 1].night : null);
  const step = (d) => { const i = clamp(+range.value + d, 0, nights.length); pickNight(i ? nights[i - 1].night : null); };
  $$('[data-step]', host).forEach(b => b.onclick = () => step(+b.dataset.step));
  layersEl.oninput = (e) => { if (e.target.classList.contains('ste-heatop')) setHeatOp(+e.target.value / 100); };
  layersEl.onchange = (e) => { const k = e.target.dataset.layer; if (!k) return; S.on[k] = e.target.checked; store.set('skytrace.erde.layers', S.on); scene.layersChanged(k); };
  $d('.ste-nv').onclick = (e) => { const b = e.target.closest('[data-nv]'); if (!b) return; S.nv = b.dataset.nv; store.set('skytrace.erde.nv', S.nv); drawNv(); scene.layersChanged('nv'); };
  $d('.ste-ctx')?.addEventListener('click', (e) => { const b = e.target.closest('[data-ctx]'); if (b) setCtx(b.dataset.ctx); });
  const fullscreen = () => { if (document.fullscreenElement) document.exitFullscreen(); else host.requestFullscreen?.().catch(() => {}); };
  $d('.ste-fs').onclick = fullscreen;
  $d('.ste-home').onclick = () => scene.home();
  // Seitenleiste als Panel in der Karte (ab 861 px; Nutzerwunsch „Optionen in die Karte, damit sie mehr Platz nutzt“), einklappbar; auf dem Handy darunter
  const side = $d('.ste-side'), stageEl = $d('.ste-stage'), wide = matchMedia('(min-width: 861px)'), tg = $d('.ste-paneltg');
  const place = () => {
    const over = wide.matches;
    if (over && side.parentElement !== stageEl) stageEl.append(side); else if (!over && side.parentElement !== host) host.append(side);
    host.classList.toggle('ste-over', over); tg.hidden = !over;
    const open = !over || store.get('skytrace.erde.panel', true);
    stageEl.classList.toggle('panel-open', over && open); side.classList.toggle('closed', over && !open); tg.setAttribute('aria-expanded', String(open));
  };
  tg.onclick = () => { store.set('skytrace.erde.panel', !store.get('skytrace.erde.panel', true)); place(); };
  wide.addEventListener?.('change', place); place();

  // Heatmap für eine Nacht berechnen (Hintergrundauftrag wie in „Standorte aller Nächte“), danach neu aufbauen
  async function computeHeat(night, btn) {
    if (btn) btn.disabled = true;
    try { await send(T + '/sitesearch/nights/heatmap', { night, global_km: store.get('skytrace.nightsGlobal', 1500) || 1500, clock: store.get('skytrace.nightsClock', true) }); }
    catch (e) { toast(e.message, 'err'); if (btn) btn.disabled = false; return; }
    for (;;) {
      await new Promise(r => setTimeout(r, 1500));
      if (dead || !host.isConnected) return;
      let d; try { d = await api(T + '/sitesearch/nights/heatmap?night=' + encodeURIComponent(night)); } catch { continue; }
      const j = d.job;
      if (j && j.state === 'running') { if (btn?.isConnected) btn.textContent = j.text ? j.text.replace(night + ': ', '').slice(0, 50) : 'Rechnet …'; continue; }
      if (j?.state === 'error') toast(j.error || 'Heatmap fehlgeschlagen', 'err');
      else toast(d.heat ? `Heatmap für ${night} berechnet.` : (j?.result?.text || 'Keine Heatmap.'));
      if (d.heat) { heats[night] = d.heat; if (!maps.includes(night)) maps.push(night); drawNights(); scene.heatChanged(night); }
      return;
    }
  }
  if (S.ctx === 'runs') { if (opts.run || S.runId) scene.openRun(S.runId, { focus: opts.focus, session: opts.session }); else scene.setNight(null, { fly: true, first: true }); }
  else scene.setNight(S.night, { card: false, fly: true, first: true });
  if (!nights.length && S.ctx === 'nights') $d('.ste-note').innerHTML = 'Noch keine Standortsuche: <a href="#/skytrace/standorte">Standorte aller Nächte</a> rechnet je Nacht Vorschläge und Heatmaps. Bis dahin zeigt der Globus die Orte der Liste.';
  setHash();
  return {
    ctx: () => S.ctx, step, home: () => scene.home(), fullscreen,
    dispose() { dead = true; scene?.dispose(); host._ste = host._st3d = null; if (document.fullscreenElement === host) document.exitFullscreen(); },
  };
}

function glowTexture(THREE, ring = false) {
  const cv = document.createElement('canvas'); cv.width = cv.height = 64;
  const g = cv.getContext('2d');
  if (ring) { g.strokeStyle = '#fff'; g.lineWidth = 7; g.beginPath(); g.arc(32, 32, 24, 0, Math.PI * 2); g.stroke(); }
  else {
    const r = g.createRadialGradient(32, 32, 0, 32, 32, 32);
    r.addColorStop(0, '#fff'); r.addColorStop(.3, '#ffffffe0'); r.addColorStop(.6, '#ffffff38'); r.addColorStop(1, '#fff0');
    g.fillStyle = r; g.fillRect(0, 0, 64, 64);
  }
  return new THREE.CanvasTexture(cv);
}

// Erd-Shader: Tag/Nacht nach Sonnenrichtung (shade = Ebene „Tag/Nacht-Grenze“), Nachtsicht nv hellt die Nachtseite in Blaugrün auf.
// Textur roh (sRGB-Werte, keine Umrechnung) – Ausgabe ebenso, damit Farben wie im Bild bleiben.
const EARTH_VS = `varying vec2 vUv; varying vec3 vN; void main(){ vUv = uv; vN = normalize((modelMatrix * vec4(normal, 0.0)).xyz); gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }`;
const EARTH_FS = `uniform sampler2D map; uniform vec3 sun; uniform float shade; uniform float nv; varying vec2 vUv; varying vec3 vN;
void main(){
  vec3 c = texture2D(map, vUv).rgb;
  float d = dot(normalize(vN), sun);
  float day = mix(1.0, smoothstep(-0.06, 0.1, d), shade);
  float lum = dot(c, vec3(0.3, 0.59, 0.11));
  vec3 lit = c * mix(1.0, 0.55 + 0.6 * clamp(d + 0.25, 0.0, 1.0), shade);
  vec3 dark = c * 0.07 + vec3(0.004, 0.006, 0.012);
  vec3 sight = mix(vec3(0.03, 0.06, 0.075), vec3(0.34, 0.62, 0.58), clamp(pow(lum, 0.8) * 1.35, 0.0, 1.0));
  vec3 night = mix(dark, sight, nv);
  vec3 col = mix(night, mix(lit, mix(lit, vec3(lum) * vec3(0.8, 0.95, 0.95), 0.45), nv), day);
  col += vec3(1.0, 0.72, 0.3) * (1.0 - smoothstep(0.0, 0.012, abs(d))) * shade * 0.55;
  gl_FragColor = vec4(col, 1.0);
}`;
// Heatmap: Wert-Textur (r = Spuren / Höchstwert), Farbe aus der gleichen Skala wie heatColor in skytrace.js; reveal baut von den hellsten Zellen her auf
const HEAT_FS = `uniform sampler2D tex; uniform float reveal; uniform float opacity; varying vec2 vUv;
vec3 ramp(float t){ vec3 a = vec3(40.,20.,90.)/255., b = vec3(150.,40.,130.)/255., c = vec3(240.,110.,50.)/255., e = vec3(255.,235.,120.)/255.;
  float x = clamp(t, 0.0, 0.999) * 3.0; return x < 1.0 ? mix(a, b, x) : x < 2.0 ? mix(b, c, x - 1.0) : mix(c, e, x - 2.0); }
void main(){
  float v = texture2D(tex, vUv).r;
  if (v < 0.004) discard;
  float th = 1.0 - reveal * 1.08;
  // Zellen mit nur 1–2 Stimmen (zufällige Bodenpunkte einzelner Spuren) fast durchsichtig, Häufungen am deutlichsten;
  // opacity = Deckkraft des heißesten Bereichs (Regler, Standard 40 %) – die Karte darunter bleibt lesbar
  float a = smoothstep(th, th + 0.08, v) * smoothstep(0.04, 0.3, v) * (0.08 + 0.92 * pow(v, 1.3)) * opacity;
  if (a < 0.004) discard;
  gl_FragColor = vec4(ramp(v), a);
}`;

function globe(host, { THREE, OrbitControls }, ctx) {
  const { nights, sites, heats, S, H, nightOf, onPick, onCompute, onCandidate, page, setLayer, setHeatOp } = ctx;
  const $d = (s) => host.querySelector(s), stage = $d('.ste-stage'), canvas = $d('.ste-stage canvas'), labelsHost = $d('.ste-labels');
  // geodätisch (WGS84) → Szene; lift in Erdradien über dem Ellipsoid
  const llv = (lat, lon, lift = 0) => { const a = lat * D2R, b = lon * D2R, s = Math.sin(a), n = 1 / Math.sqrt(1 - E2 * s * s);
    return new THREE.Vector3((n + lift) * Math.cos(a) * Math.cos(b), (n * (1 - E2) + lift) * s, -(n + lift) * Math.cos(a) * Math.sin(b)); };
  const dirLL = (lat, lon) => { const a = lat * D2R, b = lon * D2R; return new THREE.Vector3(Math.cos(a) * Math.cos(b), Math.sin(a), -Math.cos(a) * Math.sin(b)); };
  const vll = (v) => { const n = v.clone().normalize(); return [Math.atan2(n.y, (1 - E2) * Math.hypot(n.x, n.z)) / D2R, Math.atan2(-n.z, n.x) / D2R]; };
  // Kugelkappe/Kugel → Ellipsoid: Breite der Kugelgeometrie als geodätische Breite deuten (Textur, Heatmap, Kacheln liegen damit lagerichtig)
  const toEllipsoid = (g, lift = 0) => { const p = g.attributes.position, v = new THREE.Vector3();
    for (let i = 0; i < p.count; i++) { v.fromBufferAttribute(p, i); const [la, lo] = [Math.asin(clamp(v.y / v.length(), -1, 1)) / D2R, Math.atan2(-v.z, v.x) / D2R]; p.setXYZ(i, ...llv(la, lo, lift).toArray()); }
    p.needsUpdate = true; g.computeVertexNormals(); g.computeBoundingSphere(); return g; };
  const renderer = new THREE.WebGLRenderer({ canvas, antialias: true });
  renderer.setPixelRatio(Math.min(devicePixelRatio || 1, 2));
  const sc = new THREE.Scene(); sc.background = new THREE.Color('#03050b');
  const camera = new THREE.PerspectiveCamera(38, 1, .001, 400);
  camera.position.set(0, 1.2, 4.2);
  const M = { dirty: true, raf: 0, fly: null, frames: 0, frameMs: 0, flyFrames: [], anim: new Set(), zoom: 'globe', hooks: { draw: [], frame: [], resize: [], pick: [], near: [] } };
  let controls = null;
  function makeControls(up) {
    const tgt = controls ? controls.target.clone() : new THREE.Vector3();
    controls?.dispose(); camera.up.copy(up);
    controls = new OrbitControls(camera, canvas); controls.target.copy(tgt);
    controls.enablePan = false; controls.enableDamping = false; controls.rotateSpeed = .5; controls.zoomSpeed = .9; controls.maxDistance = 90;
    controls.enableZoom = controls.enableRotate = M.zoom === 'orbit';
    controls.addEventListener('change', () => { M.dirty = true; });
    controls.addEventListener('start', () => { M.fly = null; });
  }
  makeControls(new THREE.Vector3(0, 1, 0));
  const disposables = [], keep = (x) => { disposables.push(x); return x; };
  const glow = keep(glowTexture(THREE)), ring = keep(glowTexture(THREE, true));

  // ---- Sterne, Erde, Saum ----
  { const n = 1500, pos = new Float32Array(n * 3); let s = 11;
    const rnd = () => (s = (s * 16807) % 2147483647) / 2147483647;
    for (let i = 0; i < n; i++) { const z = rnd() * 2 - 1, a = rnd() * Math.PI * 2, r = Math.sqrt(1 - z * z) * 150; pos.set([r * Math.cos(a), z * 150, r * Math.sin(a)], i * 3); }
    const g = keep(new THREE.BufferGeometry()); g.setAttribute('position', new THREE.BufferAttribute(pos, 3));
    sc.add(new THREE.Points(g, keep(new THREE.PointsMaterial({ color: '#cdd6ff', size: 1.2, sizeAttenuation: false, transparent: true, opacity: .7 })))); }
  const earth = new THREE.Group(); sc.add(earth);          // dreht sich im Analysen-Kontext mit der Zeit; alles Erdfeste hängt daran
  const tex = keep(new THREE.TextureLoader().load(TEX, () => { M.dirty = true; }));
  const texImg = new Image(); texImg.src = TEX;          // dieselbe Datei für die flache Karte (aus dem Browser-Cache)
  tex.anisotropy = renderer.capabilities.getMaxAnisotropy();      // schräg betrachtete Erde scharf (Mipmaps: Standard, trilinear)
  tex.minFilter = THREE.LinearMipmapLinearFilter;
  const U_E = { map: { value: tex }, sun: { value: new THREE.Vector3(1, 0, 0) }, shade: { value: S.on.terminator ? 1 : 0 }, nv: { value: 0 } };
  earth.add(new THREE.Mesh(keep(toEllipsoid(new THREE.SphereGeometry(1, 128, 96))), keep(new THREE.ShaderMaterial({ uniforms: U_E, vertexShader: EARTH_VS, fragmentShader: EARTH_FS }))));
  const atmo = { value: 1 };
  sc.add(new THREE.Mesh(keep(new THREE.SphereGeometry(1.03, 64, 48)), keep(new THREE.ShaderMaterial({
    side: THREE.BackSide, blending: THREE.AdditiveBlending, transparent: true, depthWrite: false, uniforms: { k: atmo },
    vertexShader: 'varying vec3 n; void main(){ n = normalize(normalMatrix * normal); gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }',
    fragmentShader: 'uniform float k; varying vec3 n; void main(){ float i = pow(max(0.0, 0.72 - dot(n, vec3(0.0, 0.0, 1.0))), 3.0); gl_FragColor = vec4(0.42, 0.6, 1.0, 1.0) * i * 1.4 * k; }' }))));

  // ---- Ebenen: Gruppe + Deckkraft k (weich zum Ziel) ----
  const layer = {};
  for (const [k] of LAYERS) { layer[k] = { k: 0, group: new THREE.Group(), mats: [] }; (k === 'runs' ? sc : earth).add(layer[k].group); }
  const addMat = (k, m, base = 1) => { m.transparent = true; m.userData.base = base; m.opacity = base * ease(layer[k].k); layer[k].mats.push(m); return keep(m); };
  const dropMats = (k, list) => { const set = new Set(list); layer[k].mats = layer[k].mats.filter(m => !set.has(m)); };
  const sprite = (k, color, size, base = 1, map = glow) => { const m = addMat(k, new THREE.SpriteMaterial({ map, color, sizeAttenuation: false, depthWrite: false, depthTest: true }), base);
    const s = new THREE.Sprite(m); s.scale.setScalar(size); return s; };
  const lineOn = (k, pts, color, base = 1, loop = false) => { const g = keep(new THREE.BufferGeometry().setFromPoints(pts));
    const m = addMat(k, new THREE.LineBasicMaterial({ color, depthWrite: false }), base); return new (loop ? THREE.LineLoop : THREE.Line)(g, m); };
  const labels = [];
  function label(text, sub, color, pos, opts = {}) {
    const b = document.createElement('button'); b.className = 'ste-lbl' + (opts.cls ? ' ' + opts.cls : ''); b.style.setProperty('--c', color); b.textContent = text;
    if (sub) { const s = document.createElement('small'); s.textContent = sub; b.append(s); }
    if (opts.onClick) b.onclick = opts.onClick; else b.tabIndex = -1;
    labelsHost.append(b); const L = { el: b, pos, ...opts }; labels.push(L); return L;
  }
  const dropLabels = (pred) => { for (let i = labels.length - 1; i >= 0; i--) if (pred(labels[i])) { labels[i].el.remove(); labels.splice(i, 1); } };

  // Grenzen/Küsten: ein LineSegments-Objekt aus den Umrissen (189 000 Punkte)
  const bordersMat = addMat('borders', new THREE.LineBasicMaterial({ color: '#c5d3ef', depthWrite: false }), .38);
  H.outlines().then(rings => {
    if (!rings || M.dead) return;
    let n = 0; for (const r of rings) n += r.lon.length;
    const pos = new Float32Array(n * 6); let o = 0;
    for (const r of rings) {
      const m = r.lon.length;
      for (let i = 0; i < m; i++) {
        const j = (i + 1) % m, a = llv(r.lat[i], r.lon[i], .0006), b = llv(r.lat[j], r.lon[j], .0006);
        pos[o++] = a.x; pos[o++] = a.y; pos[o++] = a.z; pos[o++] = b.x; pos[o++] = b.y; pos[o++] = b.z;
      }
    }
    const g = keep(new THREE.BufferGeometry()); g.setAttribute('position', new THREE.BufferAttribute(pos, 3));
    layer.borders.group.add(new THREE.LineSegments(g, bordersMat)); M.borders = n; M.dirty = true;
  });

  // Orte der Liste
  const picks = [];                                    // anklickbare Sprites: {obj, kind, night|site}
  for (const s of sites) {
    const p = llv(s.lat, s.lon, .0012), o = sprite('sites', '#dfe6f5', .016, .95); o.position.copy(p); layer.sites.group.add(o);
    const lab = label(s.name || 'Ort', null, '#c9d2e3', p, { layer: 'sites', near: 1.9, cls: 'site' });
    picks.push({ obj: o, kind: 'site', site: s, layer: 'sites', lab });
  }
  // Ort je Nacht (bisher zugeordnet) und Vorschläge: werden je Auswahl neu gebaut
  const coords = (n) => H.nightMarks(n, sites);
  function rebuildMarks() {
    for (const k of ['current', 'proposals']) { for (const c of layer[k].group.children) c.geometry?.dispose?.(); layer[k].group.clear(); layer[k].mats = []; }
    dropLabels(L => L.layer === 'current' || L.layer === 'proposals');
    for (let i = picks.length - 1; i >= 0; i--) if (picks[i].layer !== 'sites') picks.splice(i, 1);
    const shown = S.night ? [nightOf(S.night)].filter(Boolean) : S.ctx === 'runs' ? [] : nights;
    const curSeen = new Set();
    M.curPos = null; M.flatMarks = { current: [], proposals: [], alts: [] };
    for (const n of shown) {
      const m = coords(n), sel = n.night === S.night;
      if (m.cur && !curSeen.has(m.cur.name + m.cur.lat)) {
        curSeen.add(m.cur.name + m.cur.lat);
        const o = sprite('current', '#f2d385', sel ? .034 : .024, 1, ring); o.position.copy(llv(m.cur.lat, m.cur.lon, .0016)); layer.current.group.add(o);
        picks.push({ obj: o, kind: 'night', night: n.night, layer: 'current' });
        if (sel) { label('bisher: ' + (m.cur.name || 'Ort'), null, '#f2d385', o.position, { layer: 'current', cls: 'cur' }); M.curPos = llv(m.cur.lat, m.cur.lon, .0012); }
        M.flatMarks.current.push({ lat: m.cur.lat, lon: m.cur.lon, name: m.cur.name, night: n.night, sel });
      }
      if (m.prop && !n.applied) {
        const p = llv(m.prop.lat, m.prop.lon, .002), o = sprite('proposals', '#7f98ff', sel ? .03 : .02, 1); o.position.copy(p); layer.proposals.group.add(o);
        picks.push({ obj: o, kind: 'night', night: n.night, layer: 'proposals' });
        M.flatMarks.proposals.push({ lat: m.prop.lat, lon: m.prop.lon, night: n.night, sel, mark: m.prop, name: m.prop.kind === 'list' ? m.prop.name : m.prop.kind === 'global' ? 'Unbekannter Ort' : 'Vorschlag',
          sub: fmtNight(n.night) + ' · ± ' + H.kmText((m.prop.ellipse?.major ?? m.prop.acc / 1000)) });
        layer.proposals.group.add(lineOn('proposals', H.ellipsePts(m.prop, 72).map(([la, lo]) => llv(la, lo, .0018)), '#8ea4ff', sel ? .95 : .6, true));
        if (sel || !S.night) label(m.prop.kind === 'list' ? m.prop.name : m.prop.kind === 'global' ? 'Unbekannter Ort' : 'Vorschlag', sel ? fmtNight(n.night) + ' · ± ' + H.kmText((m.prop.ellipse?.major ?? m.prop.acc / 1000)) : fmtNight(n.night),
          '#7f98ff', p, { layer: 'proposals', onClick: () => onPick(n.night), near: sel ? 99 : 2.6 });
        // Alternative (0.38.0): gestrichelte Verbindung Vorschlag ↔ Listenort mit Abstand
        for (const a of sel ? m.alts || [] : []) {
          const pts = Array.from({ length: 17 }, (_, i) => llv(m.prop.lat + (a.lat - m.prop.lat) * i / 16, m.prop.lon + (a.lon - m.prop.lon) * i / 16, .0019));
          const g = keep(new THREE.BufferGeometry().setFromPoints(pts)), len = pts[0].distanceTo(pts[16]);
          const ln = new THREE.Line(g, addMat('proposals', new THREE.LineDashedMaterial({ color: '#f2d385', dashSize: len / 9, gapSize: len / 14, depthWrite: false }), 1));
          ln.computeLineDistances(); layer.proposals.group.add(ln);
          label('Alternative: ' + (a.name || 'Ort'), H.mText(a.distance_m) + ' entfernt', '#f2d385', pts[8].clone(), { layer: 'proposals', cls: 'alt', near: 1.3 });
          M.flatMarks.alts.push({ lat1: m.prop.lat, lon1: m.prop.lon, lat2: a.lat, lon2: a.lon, text: H.mText(a.distance_m), name: a.name });
        }
      }
    }
    for (const k of ['current', 'proposals']) applyK(k);
    M.dirty = true;
  }

  // ---- Heatmap je Nacht: Kugelkappe über dem belegten Ausschnitt, grob (20 km) + fein (1 km, beim Heranzoomen) ----
  const patches = {};
  function patch(cells, km, lift) {
    if (!cells.length) return null;
    const max = cells.reduce((a, c) => Math.max(a, c.v), 0);
    let la0 = 90, la1 = -90, lo0 = 180, lo1 = -180;
    for (const c of cells) { const dl = c.km / 2 / 111.195, dn = c.km / 2 / (111.195 * Math.cos(c.lat * D2R));
      la0 = Math.min(la0, c.lat - dl); la1 = Math.max(la1, c.lat + dl); lo0 = Math.min(lo0, c.lon - dn); lo1 = Math.max(lo1, c.lon + dn); }
    const cellDeg = km / 111.195, rows = clamp(Math.ceil((la1 - la0) / cellDeg * 3), 8, 1024), cols = clamp(Math.ceil((lo1 - lo0) / cellDeg * Math.cos((la0 + la1) / 2 * D2R) * 3), 8, 1024);
    const buf = new Uint8Array(rows * cols * 4);
    for (const c of cells) {
      const dl = c.km / 2 / 111.195, dn = c.km / 2 / (111.195 * Math.cos(c.lat * D2R)), v = Math.round(c.v / max * 255);
      const r0 = Math.floor((c.lat - dl - la0) / (la1 - la0) * rows), r1 = Math.ceil((c.lat + dl - la0) / (la1 - la0) * rows);
      const c0 = Math.floor((c.lon - dn - lo0) / (lo1 - lo0) * cols), c1 = Math.ceil((c.lon + dn - lo0) / (lo1 - lo0) * cols);
      for (let y = Math.max(0, r0); y < Math.min(rows, r1); y++) for (let x = Math.max(0, c0); x < Math.min(cols, c1); x++) {
        const i = (y * cols + x) * 4; if (v > buf[i]) { buf[i] = v; buf[i + 3] = 255; }
      }
    }
    const t = keep(new THREE.DataTexture(buf, cols, rows, THREE.RGBAFormat)); t.magFilter = t.minFilter = THREE.LinearFilter; t.needsUpdate = true;
    const u = { tex: { value: t }, reveal: { value: 0 }, opacity: { value: 0 } };
    const g = keep(toEllipsoid(new THREE.SphereGeometry(1, clamp(cols >> 2, 8, 96), clamp(rows >> 2, 8, 96), (lo0 + 180) * D2R, (lo1 - lo0) * D2R, (90 - la1) * D2R, (la1 - la0) * D2R), lift));
    const m = keep(new THREE.ShaderMaterial({ uniforms: u, transparent: true, depthWrite: false, vertexShader: 'varying vec2 vUv; void main(){ vUv = uv; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }', fragmentShader: HEAT_FS }));
    const mesh = new THREE.Mesh(g, m); mesh.renderOrder = 2;
    return { mesh, u, max, box: [la0, la1, lo0, lo1] };
  }
  function buildPatch(night) {
    const h = heats[night]; if (!h) return null;
    const old = patches[night]; if (old) { layer.heat.group.remove(old.group); }
    const coarse = patch(H.heatCells(h.coarse), h.coarse?.cell_km || 20, .0009), fine = patch(H.heatCells(h.fine), h.fine?.km || 1, .0013);
    if (!coarse && !fine) return null;
    const group = new THREE.Group(); if (coarse) group.add(coarse.mesh); if (fine) group.add(fine.mesh);
    layer.heat.group.add(group);
    return patches[night] = { night, group, coarse, fine, reveal: 0, revealStart: 0, h };
  }
  function revealHeat(list) {
    const now = performance.now();
    for (const P of list) { P.revealStart = now; P.reveal = reduced() ? 1 : 0; }
    M.anim.add('reveal'); M.dirty = true;
  }

  // ---- Gemeinsame Schnittstelle für die Lauf-Ebene (skytrace-3d.js runLayer) ----
  const G = {
    THREE, sc, earth, camera, stage, labelsHost, $d, glow, cardEl: $d('.ste-card'),
    controls: () => controls, makeControls, addMat, dropMats,
    zoomMode(m) { M.zoom = m; controls.enableZoom = controls.enableRotate = m === 'orbit'; if (m === 'orbit' && flat?.active) leaveFlat(null, true); },
    setFly(fn) { M.fly = fn; M.dirty = true; },
    dirty() { M.dirty = true; },
    layerK: (k) => layer[k].k,
    hook(kind, fn) { M.hooks[kind].push(fn); return () => { M.hooks[kind] = M.hooks[kind].filter(f => f !== fn); }; },
    setSun(v) { sunNow.copy(v); U_E.sun.value.copy(v); M.dirty = true; },
    refresh(k) { applyK(k); flat?.dirty(); },
    onRunPick: null,
  };
  // ---- Analysen-Kontext: ein Lauf interaktiv (Zeitleiste dreht die Erde) ----
  const R = { run: null, id: null, ticket: 0 };
  const runMsg = (text, link) => { const m = $d('.ste-msg'); m.innerHTML = `${icon('alert')}<span>${esc(text)}${link ? ` <a href="${link[1]}">${esc(link[0])}</a>` : ''}</span>`; m.classList.add('hint'); m.hidden = false; };
  async function openRun(id, { focus = null, session = null } = {}) {
    closeRun(false);
    S.ctx = 'runs'; R.id = id;
    for (const P of Object.values(nightRuns)) P.layers.forEach(l => l.setVisible(false));
    if (!id) { setNight(null, { fly: true }); return; }
    const ticket = ++R.ticket, m = $d('.ste-msg');
    m.classList.remove('hint'); m.innerHTML = '<span class="spinner"></span>Teleskopweg und Bahnen werden berechnet …'; m.hidden = false;
    let data;
    const q = `run=${encodeURIComponent(id)}` + (focus ? `&focus=${encodeURIComponent(focus)}` : '') + (session ? `&session=${encodeURIComponent(session)}` : '');
    try { data = await api(T + '/run3d?' + q); } catch (e) { if (ticket === R.ticket) runMsg(e.message); return; }
    if (ticket !== R.ticket || M.dead) return;
    if (!data.ok) { runMsg(data.reason, data.need_site ? ['Standort eintragen →', '#/settings/skytrace'] : null); notes(); return; }
    m.hidden = true;
    $d('.st3d-schema').hidden = false;
    R.run = SkyTrace3D.runLayer(G, data, { interactive: true, focus, onCandidate, legendEl: $d('.st3d-legend'), noteEl: $d('.st3d-note') });
    host._st3d = { state: () => R.run?.state(), screen: () => R.run?.screen() || [], sel: () => R.run?.sel() };
    // Nacht des Laufs: deren Orte, Vorschlag und Heatmap gehören dazu (gemeinsame Ebenen)
    S.night = nightOfTime(data.anchor);
    rebuildMarks(); for (const P of Object.values(patches)) setPatchK(P);
    if (S.on.heat && patches[S.night]) revealHeat([patches[S.night]]);
    M.sunTime = data.anchor;
    M.anim.add('layers'); notes();
    R.run.begin(true);
  }
  function closeRun(animate = true) {
    R.ticket++;
    if (R.run) { R.run.dispose(); R.run = null; }
    host._st3d = null;
    $d('.st3d-schema').hidden = true; $d('.st3d-views').hidden = true; $d('.st3d-bar').hidden = true;
    $d('.ste-msg').hidden = true;
    G.zoomMode('globe');
    if (camera.up.y < .999) makeControls(new THREE.Vector3(0, 1, 0));
    // Erde weich zurück in die erdfeste Lage (Nächte-Kontext), Blick wieder auf den Mittelpunkt
    const r0 = earth.rotation.y, t0 = controls.target.clone(), start = performance.now(), ms = animate && !reduced() ? 900 : 0;
    if (Math.abs(r0) > 1e-6 || t0.lengthSq() > 1e-9) {
      const step = (now) => { const k = ms ? Math.min(1, (now - start) / ms) : 1, e = ease(k);
        earth.rotation.y = r0 * (1 - e); controls.target.copy(t0).multiplyScalar(1 - e); controls.update();
        if (k >= 1) M.spin = null; M.dirty = true; };
      if (ms) M.spin = step; else step(start);
    }
  }

  // ---- „Nächte & Standorte“: Satellitenbahnen = dieselbe Lauf-Ebene, statisch zur Ankerzeit (bei Erddrehung 0 ist System D erdfest) ----
  const nightRuns = {};
  async function loadNightRuns(night) {
    if (nightRuns[night]) return nightRuns[night];
    const n = nightOf(night), obs = [];
    for (const o of n?.observations || []) if (!obs.some(x => x.run === o.run && x.session === o.session)) obs.push(o);
    const P = nightRuns[night] = { layers: [], notes: [], hits: 0, obs: obs.length, ready: false, time: null, points: [] };
    for (const o of obs.slice(0, 6)) {
      let d;
      try { d = await api(`${T}/run3d?run=${encodeURIComponent(o.run)}&session=${encodeURIComponent(o.session)}`); } catch (e) { P.notes.push(`${H.shortName(o.session)}: ${e.message}`); continue; }
      if (M.dead) return P;
      if (!d.ok) { P.notes.push(`${H.shortName(o.session)}: ${d.reason || 'keine 3D-Daten'}`); continue; }
      const L = SkyTrace3D.runLayer(G, d, { interactive: false, label: H.shortName(o.session) });
      L.setVisible(S.ctx === 'nights' && S.night === night);
      P.layers.push(L); P.time ??= d.anchor; P.hits += L.hits; P.notes.push(...L.notes); P.points.push(...L.points);
    }
    P.ready = true;
    applyK('runs'); M.dirty = true;
    return P;
  }
  function showNightRuns() { for (const [night, P] of Object.entries(nightRuns)) P.layers.forEach(l => l.setVisible(S.ctx === 'nights' && night === S.night)); }
  G.onRunPick = () => { if (S.ctx === 'nights' && S.night) card(nightOf(S.night)); };      // statische Lauf-Ebene: Karte der Nacht

  // ---- Deckkraft der Ebenen (weich, 450 ms) ----
  function applyK(k) {
    const L = layer[k], f = ease(L.k);
    for (const m of L.mats) m.opacity = (m.userData.base ?? 1) * f;
    L.group.visible = L.k > .002;
    if (k === 'terminator') U_E.shade.value = f;
    if (k === 'heat') for (const P of Object.values(patches)) setPatchK(P);
    M.dirty = true;
  }
  function setPatchK(P) {
    const f = ease(layer.heat.k), sel = S.night ? P.night === S.night : S.ctx === 'nights';
    const dist = camera.position.length(), fineK = P.fine ? clamp((1.55 - dist) / .35, 0, 1) : 0;
    P.group.visible = sel && f > .002;
    if (P.coarse) { P.coarse.u.opacity.value = f * S.heatOp * (1 - .55 * fineK); P.coarse.u.reveal.value = ease(P.reveal); }
    if (P.fine) { P.fine.u.opacity.value = f * S.heatOp * fineK; P.fine.u.reveal.value = ease(P.reveal); }
  }
  const target = (k) => {
    if (k === 'runs') return S.on.runs && (S.ctx === 'runs' ? !!R.run : S.night && nightRuns[S.night]?.ready) ? 1 : 0;
    return S.on[k] ? 1 : 0;
  };
  // Nachtsicht: Auto = an, sobald Daten gezeigt werden und die betrachtete Region auf der Nachtseite liegt
  const sunNow = new THREE.Vector3(1, 0, 0);
  function nvTarget() {
    if (S.nv === 'on') return 1;
    if (S.nv === 'off' || !S.on.terminator) return 0;
    const any = ['sites', 'current', 'heat', 'proposals', 'runs'].some(k => S.on[k]);
    const look = S.ctx === 'runs' ? (controls.target.lengthSq() > 1e-6 ? controls.target.clone() : camera.position.clone()).normalize() : (M.focus || camera.position.clone().normalize());
    return any && look.dot(sunNow) < .12 ? 1 : 0;
  }
  function layersChanged(k) {
    if (k === 'heat' && S.on.heat) revealHeat(Object.values(patches).filter(P => !S.night || P.night === S.night));
    if (k === 'runs' && S.on.runs && S.night && S.ctx === 'nights') loadNightRuns(S.night).then((P) => { if (!M.dead) { showNightRuns(); applyK('runs'); M.anim.add('layers'); notes(); if (P?.points.length && S.on.runs) home(); } });
    M.anim.add('layers'); M.dirty = true;
    notes();
  }

  // ---- Kamera: Fahrt (Kugel-Slerp mit Bogen nach außen) zu Richtung und Abstand ----
  function flyTo(dir, dist, ms = 1500) {
    if (flat?.active) leaveFlat(null, true);
    const from = camera.position.clone(), d0 = from.length(), d1 = clamp(dist, 1.015, 8);
    const a = from.clone().normalize(), b = dir.clone().normalize(), ang = a.angleTo(b);
    const q0 = new THREE.Quaternion(), q1 = new THREE.Quaternion().setFromUnitVectors(a, b);
    if (reduced() || ms === 0) { camera.position.copy(b.multiplyScalar(d1)); controls.update(); M.dirty = true; return; }
    const lift = Math.max(0, Math.min(1.8, ang * 1.1) - Math.max(0, Math.max(d0, d1) - 2));
    const start = performance.now(), q = new THREE.Quaternion();
    M.flyFrames = [];
    M.fly = (now) => {
      const k = Math.min(1, (now - start) / ms), e = ease(k);
      q.slerpQuaternions(q0, q1, e);
      const r = d0 + (d1 - d0) * e + lift * Math.sin(Math.PI * e) * Math.min(1, 1.2 / Math.max(d0, d1));
      camera.position.copy(a).applyQuaternion(q).multiplyScalar(r);
      controls.update();
      if (k >= 1) M.fly = null;
      M.dirty = true;
    };
  }
  // Region einer Nacht: Heatmap-Ausschnitt, Vorschlag, bisheriger Ort (+ Bahnen); ohne Nacht alle Orte
  function region(night) {
    const pts = [];
    const add = (la, lo) => pts.push(dirLL(la, lo));
    const ns = night ? [nightOf(night)].filter(Boolean) : nights;
    for (const n of ns) {
      const m = coords(n);
      if (m.prop) add(m.prop.lat, m.prop.lon);
      if (m.cur) add(m.cur.lat, m.cur.lon);
      const P = patches[n.night];
      if (P?.coarse && (night || S.on.heat)) { const [a, b, c, d] = P.coarse.box; add(a, c); add(b, d); add(a, d); add(b, c); }
    }
    const rp = night && S.on.runs && nightRuns[night]?.ready ? nightRuns[night] : null;
    if (rp) pts.push(...rp.points.map(p => p.clone().normalize()));
    if (!pts.length) for (const s of sites) add(s.lat, s.lon);
    if (!pts.length) return { dir: new THREE.Vector3(.6, .55, -.3).normalize(), dist: 3.6 };
    const c = pts.reduce((a, p) => a.add(p), new THREE.Vector3()).normalize();
    const spread = Math.max(...pts.map(p => p.angleTo(c)), .004);
    const narrow = stage.clientWidth / Math.max(1, stage.clientHeight) < 1 ? 1.6 : 1;
    return { dir: c, dist: clamp(1 + spread * 2.6 * narrow + .03, 1.09, 3.6) };      // mindestens ~570 km Höhe: Umgebung bleibt erkennbar
  }
  function home() {
    if (S.ctx === 'runs' && R.run) { R.run.setView('earth'); return; }
    const r = region(S.night); M.focus = r.dir; flyTo(r.dir, r.dist); M.anim.add('layers');
  }

  // ---- Auswahl einer Nacht (Kontext „Nächte & Standorte“) ----
  function setNight(night, { card: showCard = false, fly = true, first = false } = {}) {
    if (flat?.active && night === M.lastNight) { if (showCard && night) card(nightOf(night)); return; }   // in der Karte: dieselbe Nacht nur zeigen, nicht wegfliegen
    S.night = night; M.lastNight = night;
    // Sonne zur Aufnahmezeit: run3d-Anker, sonst Mitternacht Ortszeit an der Region; ohne Nacht: jetzt
    const r = region(night), [, lon] = vll(r.dir);
    const t = night ? (nightRuns[night]?.time || nightTime(night, lon)) : Date.now() / 1000;
    G.setSun(dirLL(...sunLatLon(t)));
    M.sunTime = t;
    rebuildMarks();
    for (const P of Object.values(patches)) setPatchK(P);
    if (S.on.heat) revealHeat(Object.values(patches).filter(P => !night || P.night === night));
    showNightRuns();
    if (S.on.runs && night) {
      const had = nightRuns[night]?.ready;
      loadNightRuns(night).then((P) => { if (!M.dead && S.night === night && S.ctx === 'nights') { showNightRuns(); applyK('runs'); M.anim.add('layers'); notes(); if (!had && P?.points.length) home(); } });
    }
    M.focus = r.dir;
    if (fly) flyTo(r.dir, r.dist, first ? 1800 : 1500);
    if (showCard && night) card(nightOf(night)); else if (!night) cardEl.hidden = true;
    M.anim.add('layers'); notes(); M.dirty = true;
  }
  function heatChanged(night) { const P = buildPatch(night); if (P) { setPatchK(P); if (S.on.heat) revealHeat([P]); } if (S.night === night && !cardEl.hidden) card(nightOf(night)); M.dirty = true; }

  // ---- Detailkarte einer Nacht ----
  const cardEl = G.cardEl;
  function card(n) {
    if (!n) { cardEl.hidden = true; return; }
    const p = n.proposal, c = n.current || {}, hh = heats[n.night];
    const [lab, cls] = n.applied ? ['Übernommen', 'ok'] : p?.kind === 'global' ? ['Unbekannter Ort', 'acc'] : (H.NIGHT_STATUS[n.status] || [n.status || '–', '']);
    const why = H.nearText(n);
    const prop = !p ? '' : p.kind === 'list' ? `<b>${esc(p.name || 'Ort der Liste')}</b><span class="mut sm">bestehender Ort · ${p.sure} von ${n.tracks} Spuren · quer ${H.arcText(p.rms)}</span>`
      : p.kind === 'global' ? H.globalHtml(p, n, sites)
      : `<b>Neuer Ort ${p.shift_from_current_km != null ? H.kmText(p.shift_from_current_km) + ' neben „' + esc(c.name || 'bisher') + '“' : ''}</b><span class="mut sm">± ${H.kmText(p.accuracy_m / 1000)} · ${p.sure} von ${n.tracks} Spuren · quer ${H.arcText(p.rms)}${H.clockText(p) ? ' · ' + H.clockText(p) : ''}</span>`;
    const runsOf = [...new Map((n.observations || []).map(o => [o.run, o])).values()];
    const rp = nightRuns[n.night];
    cardEl.innerHTML = `<div class="ste-card-h"><div><b>${esc(fmtNight(n.night, true))}</b><small>${esc([...new Set((n.observations || []).map(o => H.shortName(o.session)))].join(' · '))}</small></div>
        <span class="chip ${cls}">${esc(lab)}</span><button class="btn ghost icon sm" data-a="x" aria-label="Schließen">${icon('x')}</button></div>
      <div class="ste-card-g"><div><small class="dim">Bisher</small><b>${esc(c.name || 'kein Ort')}</b><span class="mut sm">${c.sure ?? n.before ?? 0} von ${n.tracks} Spuren eindeutig · quer ${H.arcText(c.rms)}</span></div>
        ${p ? `<div><small class="dim">Vorschlag</small>${prop}</div>` : ''}
        ${hh ? `<div><small class="dim">Heatmap</small><span class="mut sm">Suchgebiet ± ${hh.region_km} km · Uhr ± ${Math.round(hh.clock_s)} s · bis ${patches[n.night]?.coarse?.max ?? hh.coarse?.max ?? '–'} verschiedene Spuren je ${hh.coarse?.cell_km || 20}-km-Zelle${hh.fine ? ' · fein 1 km beim Heranzoomen' : ''}</span></div>` : ''}
        ${rp?.ready ? `<div><small class="dim">Bahnen</small><span class="mut sm">${rp.hits} zugeordnete Objekte aus ${rp.obs} Beobachtung${rp.obs === 1 ? '' : 'en'}</span></div>` : ''}</div>
      ${H.altHtml(n)}
      ${n.note || why ? `<p class="dim sm">${esc([n.note, why].filter(Boolean).join(' '))}</p>` : ''}
      <div class="row ste-card-a">${p && !n.applied ? '<button class="btn sm" data-a="apply">Für diese Nacht übernehmen …</button>' : ''}
        <button class="btn ghost sm" data-a="heat">${hh ? 'Heatmap neu berechnen' : 'Heatmap berechnen'}</button>
        ${hh ? `<button class="btn ghost sm" data-a="heatvis" aria-pressed="${S.on.heat}" title="Heatmap auf Globus und Karte ${S.on.heat ? 'ausblenden' : 'einblenden'}">${S.on.heat ? 'Heatmap ausblenden' : 'Heatmap einblenden'}</button>` : ''}
        ${runsOf.slice(0, 3).map(o => `<a class="btn ghost sm" href="#/skytrace/${encodeURIComponent(o.run)}/beobachtungen">${esc(H.shortName(o.session))}</a>`).join('')}
        ${runsOf[0] && page ? `<button class="btn ghost sm" data-a="run" title="Teleskopweg und Durchgänge über die Nacht">${icon('globe')}Als Analyse zeigen</button>` : ''}</div>`;
    cardEl.onclick = async (e) => {
      const a = e.target.closest('[data-a]')?.dataset.a;
      if (a === 'x') cardEl.hidden = true;
      else if (a === 'apply') { if (await H.applyNights([n])) { n.applied = Math.floor(Date.now() / 1000); rebuildMarks(); card(n); } }
      else if (a === 'heat') onCompute(n.night, e.target.closest('button'));
      else if (a === 'heatvis') { setLayer('heat', !S.on.heat); card(n); }
      const al = e.target.closest('[data-alt-apply],[data-alt-move]');
      if (al?.dataset.altApply && await H.applyAlt(n, al.dataset.altApply)) { n.applied = Math.floor(Date.now() / 1000); rebuildMarks(); card(n); }
      else if (al?.dataset.altMove && await H.moveSite(n, al.dataset.altMove, nights)) {
        // Listenort auf der Erde mitverschieben (die Ortsliste ist schon gespeichert)
        const pk = picks.find(k => k.kind === 'site' && k.site.id === al.dataset.altMove);
        if (pk) { pk.site.lat = p.lat; pk.site.lon = p.lon; const v = llv(p.lat, p.lon, .0012); pk.obj.position.copy(v); pk.lab?.pos.copy(v); }
        n.applied = Math.floor(Date.now() / 1000); rebuildMarks(); card(n);
      }
      else if (a === 'run') { cardEl.hidden = true; host.querySelector('.ste-ctx [data-ctx=runs]') && (S.runId = runsOf[0].run, store.set('skytrace.3drun', S.runId), host.querySelector('.ste-ctx [data-ctx=runs]').click()); }
    };
    cardEl.hidden = false;
  }
  function siteCard(s) {
    cardEl.innerHTML = `<div class="ste-card-h"><div><b>${esc(s.name || 'Ort')}</b><small>Ort der Liste${s.accuracy_m ? ' · ± ' + H.kmText(s.accuracy_m / 1000) : ''}</small></div>
      <button class="btn ghost icon sm" data-a="x" aria-label="Schließen">${icon('x')}</button></div>
      ${s.note ? `<p class="mut sm">${esc(s.note)}</p>` : ''}<div class="row ste-card-a"><a class="btn ghost sm" href="#/settings/standorte">Standorte bearbeiten</a></div>`;
    cardEl.onclick = (e) => { if (e.target.closest('[data-a=x]')) cardEl.hidden = true; };
    cardEl.hidden = false;
  }

  // ---- Hinweise, Legende ----
  function notes() {
    const out = [];
    if (S.ctx === 'nights') {
      if (S.on.runs && !S.night) out.push('Satellitenbahnen: eine Nacht wählen.');
      const rp = S.night && nightRuns[S.night];
      if (S.on.runs && rp && !rp.ready) out.push('Bahnen werden berechnet …');
      if (S.on.runs && rp?.ready) { out.push(rp.hits ? `${rp.hits} Bahnen/Treffer mit Teleskop-Blickrichtung, je Beobachtung zu ihrem Beginn.` : 'In dieser Nacht keine Katalogtreffer – gezeigt wird die Blickrichtung des Teleskops.'); out.push(...rp.notes); }
      if (S.night && !heats[S.night] && S.on.heat) out.push('Für diese Nacht liegt keine Heatmap vor – „Heatmap berechnen“ in der Karte der Nacht holt sie nach.');
    }
    if (S.on.terminator && M.sunTime) out.push(`Beleuchtung ${S.ctx === 'runs' ? 'zur Aufnahmezeit' : S.night ? (nightRuns[S.night]?.time ? 'zur Aufnahmezeit' : '≈ Mitternacht Ortszeit') : 'jetzt'} (${new Date(M.sunTime * 1000).toISOString().slice(0, 16).replace('T', ' ')} UTC).`);
    out.push('Erde: NASA Blue Marble · Umrisse: Natural Earth.');
    $d('.ste-note').textContent = out.join(' ');
    const P = S.night ? patches[S.night] : null, any = Object.keys(patches).length;
    const lg = $d('.ste-legend');
    lg.hidden = !S.on.heat || !(P || (!S.night && S.ctx === 'nights' && any));
    $d('.ste-legend-t').textContent = P ? `verschiedene Spuren: 1 … ${P.coarse?.max ?? P.fine?.max}` : 'verschiedene Spuren (je Nacht relativ)';
  }

  // ---- Flache Karte (skytrace-karte.js): ab FLAT_IN Erdradien Abstand beim Hineinzoomen, zurück zum Globus unter dem Maßstab von FLAT_OUT ----
  const FLAT_IN = 1.07, FLAT_OUT = 1.085;
  const tilesBox = $d('.ste-tiles'), readout = $d('.ste-readout');
  const ppkAt = (distKm) => (stage.clientHeight / 2) / (Math.tan(camera.fov / 2 * D2R) * Math.max(distKm, .01));      // Pixel je km in Blickrichtung
  const toLocalDir = (v) => v.clone().applyQuaternion(earth.quaternion.clone().invert());
  const rayGlobe = (sx, sy) => {                     // Schnitt des Sehstrahls mit der Erde (Kugel, für Bedienung genau genug) → Weltpunkt
    const b = canvas.getBoundingClientRect(); ndc.set(sx / b.width * 2 - 1, -sy / b.height * 2 + 1); rc.setFromCamera(ndc, camera);
    const o = rc.ray.origin, d = rc.ray.direction, bb = o.dot(d), c = o.lengthSq() - 1, disc = bb * bb - c;
    return disc < 0 ? null : o.clone().add(d.clone().multiplyScalar(-bb - Math.sqrt(disc)));
  };
  const flatAllowed = () => M.zoom === 'globe' && !!window.SkyTraceKarte;
  const flat = window.SkyTraceKarte ? SkyTraceKarte.FlatMap(stage, {
    H, sites, texture: texImg,
    layerK: (k) => ease(layer[k].k), heatOp: () => S.heatOp, nv: () => U_E.nv.value, shade: () => U_E.shade.value,
    sunLocal: () => { const v = toLocalDir(sunNow); return [v.x, -v.z, v.y]; },
    patches: () => Object.values(patches).filter(P => S.night ? P.night === S.night : S.ctx === 'nights'),
    marks: () => M.flatMarks || { current: [], proposals: [] },
    observers: () => {                               // Teleskopstandorte der gezeigten Läufe (Bahnen im Raum zeigt die Karte nicht)
      const out = [], add = (d, name) => { const [la, lo] = vll(new THREE.Vector3(d.observer[0], d.observer[2], -d.observer[1])); out.push({ lat: la, lon: lo, name }); };
      if (S.ctx === 'runs' && R.run) add(R.run.data, 'Teleskop');
      else for (const L of nightRuns[S.night]?.layers || []) add(L.data, L.name);
      return out;
    },
    satellites: () => S.ctx === 'runs' ? (R.run?.ground() || []) : (nightRuns[S.night]?.layers || []).flatMap(L => L.ground()),
    onPick: (t) => {
      if (t.kind === 'sat') { setHover(null); t.ref.pick(); } else if (t.kind === 'multi') { setHover(null); chooser(t.list, t.sx, t.sy); }
      else if (t.kind === 'site') siteCard(t.site); else onPick(t.night);
    },
    onHover: (la, lo) => showCoord(la, lo),
    onHoverHit: (x, sx, sy) => { setHover(x); if (x) { tipEl.style.left = Math.min(sx + 14, stage.clientWidth - tipEl.offsetWidth - 6) + 'px'; tipEl.style.top = Math.max(6, sy - tipEl.offsetHeight - 10) + 'px'; } },
    leaveAt: () => ppkAt((FLAT_OUT - 1) * R_KM),
    onLeave: (clat, clon, ppk, sx, sy, la, lo) => leaveFlat({ clat, clon, ppk, sx, sy, la, lo }),
  }) : null;
  function enterFlat(sx, sy, minPpk = 0) {
    if (!flat || flat.active || !flatAllowed()) return;
    sx ??= stage.clientWidth / 2; sy ??= stage.clientHeight / 2;
    const hit = rayGlobe(sx, sy) || rayGlobe(stage.clientWidth / 2, stage.clientHeight / 2); if (!hit) return;
    const [la, lo] = vll(toLocalDir(hit)), ppk = Math.max(ppkAt(camera.position.distanceTo(hit) * R_KM), minPpk);
    M.fly = null; flat.enter(la, lo, ppk, sx, sy); cardEl.hidden = cardEl.hidden; labelsHost.hidden = true;
    tilesBox.hidden = false; $d('.ste-mode span').textContent = 'Globus'; $d('.ste-help').textContent = 'Ziehen verschiebt · Mausrad/zwei Finger zoomen · weit herauszoomen = Globus'; M.dirty = true; M.anim.add('flat');
  }
  // zurück auf den Globus: Mitte und Maßstab übernehmen, dann den Punkt unter dem Zeiger nachführen
  function leaveFlat(o, instant = false) {
    if (!flat?.active) return;
    const [clat, clon] = o ? [o.clat, o.clon] : flat.center(), ppk = o ? o.ppk : flat.pxPerKm();
    const altKm = (stage.clientHeight / 2) / (Math.tan(camera.fov / 2 * D2R) * ppk);
    earth.updateMatrixWorld(true);
    const dir = earth.localToWorld(dirLL(clat, clon)).normalize();
    camera.position.copy(dir.multiplyScalar(Math.max(1 + altKm / R_KM, FLAT_OUT + .002))); controls.update(); camera.updateMatrixWorld();
    if (o?.la != null) for (let it = 0; it < 2; it++) {
      const p = rayGlobe(o.sx, o.sy), q = earth.localToWorld(dirLL(o.la, o.lo)).normalize(); if (!p) break;
      camera.position.applyQuaternion(new THREE.Quaternion().setFromUnitVectors(p.normalize(), q)); controls.update(); camera.updateMatrixWorld();
    }
    flat.leave(); if (instant) flat.tick(0, false, true);
    labelsHost.hidden = false; tilesBox.hidden = true; $d('.ste-mode span').textContent = 'Karte'; $d('.ste-help').textContent = HELP; M.dirty = true; M.anim.add('flat');
  }
  $d('.ste-mode').onclick = () => {
    if (flat?.active) leaveFlat(null);
    else {
      if (M.zoom !== 'globe' && R.run) {               // aus Teleskop/Durchgang/Folgen: Erdansicht sofort übernehmen (setView plant sie sonst erst fürs nächste Bild)
        R.run.setView('earth', false); M.fly?.(performance.now()); M.fly = null; controls.update(); camera.updateMatrixWorld();
      }
      enterFlat(null, null, ppkAt((FLAT_IN - 1) * R_KM) * 1.15);
    }
  };
  tilesBox.querySelector('.seg').onclick = (e) => {
    const b = e.target.closest('[data-tiles]'); if (!b || !flat) return;
    $$('[data-tiles]', tilesBox).forEach(x => x.classList.toggle('on', x === b));
    $d('.ste-attr').innerHTML = flat.setTiles(b.dataset.tiles);
  };
  // Koordinaten unter dem Zeiger (0,01°) und Maßstabsleiste
  const NICE = [.1, .2, .5, 1, 2, 5, 10, 20, 50, 100, 200, 500, 1000, 2000, 5000];
  function showCoord(la, lo) {
    const ppk = flat?.active ? flat.pxPerKm() : ppkAt((camera.position.length() - 1) * R_KM);
    const km = NICE.find(k => k * ppk >= 60) || 5000, show = flat?.active || camera.position.length() < 1.6;
    readout.hidden = !show && la == null;
    readout.querySelector('.ste-scale').hidden = !show;
    readout.querySelector('.ste-scale i').style.width = (km * ppk).toFixed(0) + 'px';
    readout.querySelector('.ste-scale b').textContent = km < 1 ? Math.round(km * 1000) + ' m' : String(km).replace('.', ',') + ' km';
    readout.querySelector('.ste-coord').textContent = la == null ? '' : `${Math.abs(la).toFixed(2).replace('.', ',')}° ${la >= 0 ? 'N' : 'S'} · ${Math.abs(lo).toFixed(2).replace('.', ',')}° ${lo >= 0 ? 'O' : 'W'}`;
    M.coord = la == null ? null : [+la.toFixed(2), +lo.toFixed(2)];
  }

  // ---- Zoom: im Globus-Modus eigenes Mausrad/Zwei-Finger-Zoomen nach Höhe über dem Boden (OrbitControls zoomt nur multiplikativ zum Mittelpunkt);
  //      in den Lauf-Ansichten Teleskop/Durchgang/Folgen zoomt OrbitControls zum jeweiligen Blickpunkt ----
  // Zoomen um den Zeiger: Höhe über dem Boden ändern und den Punkt unter dem Zeiger dort halten; unter FLAT_IN wird die Kugel zur Karte
  const zoomBy = (f, sx, sy) => {
    const before = sx != null ? rayGlobe(sx, sy) : null;
    const d = camera.position.length(), alt = clamp((d - 1) * f, .012, 7); camera.position.multiplyScalar((1 + alt) / d); M.fly = null; controls.update(); camera.updateMatrixWorld();
    const after = before && rayGlobe(sx, sy);
    if (before && after) { camera.position.applyQuaternion(new THREE.Quaternion().setFromUnitVectors(after.normalize(), before.normalize())); controls.update(); }
    M.dirty = true; M.anim.add('layers');
    if (f < 1 && camera.position.length() < FLAT_IN) enterFlat(sx, sy);
  };
  const localXY = (e) => { const b = canvas.getBoundingClientRect(); return [e.clientX - b.left, e.clientY - b.top]; };
  // auf der ganzen Bühne (auch über Schildern), nicht über Karte/Werkzeugen; die flache Karte hat ihren eigenen Zoom
  stage.addEventListener('wheel', (e) => {
    if (M.zoom !== 'globe' || flat?.active || e.target.closest('.ste-card,.ste-tools,.ste-tiles,.ste-legend,.ste-side') || e.target.classList.contains('ste-flat')) return;
    e.preventDefault(); zoomBy(Math.exp(clamp(e.deltaY, -300, 300) * .0018), ...localXY(e));
  }, { passive: false });
  const touches = new Map(); let pinch = 0, down = null, grab = null;
  canvas.addEventListener('pointerdown', (e) => {
    touches.set(e.pointerId, localXY(e)); down = [e.clientX, e.clientY];
    if (M.zoom !== 'globe') return;
    canvas.setPointerCapture?.(e.pointerId);
    const h = touches.size === 1 ? rayGlobe(...localXY(e)) : null;
    grab = touches.size === 1 ? { p: h?.normalize() || null, last: localXY(e) } : null; M.fly = null;
  });
  canvas.addEventListener('pointermove', (e) => {
    const xy = localXY(e);
    if (!touches.has(e.pointerId)) {                   // nur zeigen: Koordinaten unter dem Zeiger, Treffer hervorheben
      const h = M.zoom === 'globe' ? rayGlobe(...xy) : null;
      if (h) { const [la, lo] = vll(toLocalDir(h)); showCoord(la, lo); } else showCoord(null);
      if (e.pointerType === 'mouse') hoverAt(...xy);
      return;
    }
    touches.set(e.pointerId, xy);
    if (M.zoom !== 'globe') return;
    if (touches.size === 2) { const [a, b] = [...touches.values()], d = Math.hypot(a[0] - b[0], a[1] - b[1]); if (pinch) zoomBy(pinch / d, (a[0] + b[0]) / 2, (a[1] + b[1]) / 2); pinch = d; grab = null; return; }
    if (!grab) return;
    // Greif-Drehen: der angefasste Punkt bleibt unter dem Zeiger (ohne Nachlauf); daneben (im All) langsam nach Höhe
    const now = rayGlobe(...xy);
    if (grab.p && now) camera.position.applyQuaternion(new THREE.Quaternion().setFromUnitVectors(now.normalize(), grab.p));
    else {
      const k = clamp((camera.position.length() - 1) * .004, .0004, .006), dx = xy[0] - grab.last[0], dy = xy[1] - grab.last[1];
      camera.position.applyAxisAngle(new THREE.Vector3(0, 1, 0), -dx * k);
      camera.position.applyAxisAngle(new THREE.Vector3().crossVectors(camera.up, camera.position).normalize(), -dy * k);
    }
    const c = camera.position, lim = .995 * c.length();                 // nicht über die Pole kippen
    if (Math.abs(c.y) > lim) { const r = Math.hypot(c.x, c.z) || 1e-6, s2 = Math.sqrt(c.lengthSq() - lim * lim) / r; c.set(c.x * s2, Math.sign(c.y) * lim, c.z * s2); }
    grab.last = xy; controls.update(); M.dirty = true;
  });
  const up = (e) => { touches.delete(e.pointerId); if (touches.size < 2) pinch = 0; if (!touches.size) grab = null; };
  canvas.addEventListener('pointercancel', up);
  canvas.addEventListener('pointerleave', () => { if (!touches.size) showCoord(null); setHover(null); });
  // Antippen: Objekte des Laufs, sonst Ort / Nacht
  const rc = new THREE.Raycaster(), ndc = new THREE.Vector2(), wp = new THREE.Vector3(), camN = new THREE.Vector3();
  canvas.addEventListener('pointerup', (e) => {
    up(e);
    if (!down || Math.hypot(e.clientX - down[0], e.clientY - down[1]) > 6) return;
    const b = canvas.getBoundingClientRect(); ndc.set((e.clientX - b.left) / b.width * 2 - 1, -(e.clientY - b.top) / b.height * 2 + 1);
    rc.setFromCamera(ndc, camera);
    // Treffer (Satelliten, Meteore, Flugzeuge) im Bildschirmraum: großzügig, fürs Antippen mit dem Finger noch mehr; mehrere → Auswahl
    const [lx, ly] = localXY(e), list = nearAt(lx, ly, e.pointerType === 'touch' ? 26 : e.pointerType === 'pen' ? 18 : 14);
    if (list.length) { setHover(null); if (list.length === 1) list[0].pick(); else chooser(list, lx, ly); return; }
    for (const f of M.hooks.pick) if (f(rc)) return;
    camN.copy(camera.position).normalize();
    const objs = picks.filter(p => layer[p.layer].group.visible && p.obj.getWorldPosition(wp).normalize().dot(camN) > .05);
    const hit = rc.intersectObjects(objs.map(p => p.obj), false)[0], p = hit && objs.find(x => x.obj === hit.object);
    if (!p) return;
    if (p.kind === 'site') siteCard(p.site); else onPick(p.night);
  });

  // ---- Treffer anklicken: Auswahl bei mehreren, Hervorheben beim Überfahren (Maus) ----
  const pickEl = $d('.ste-pick'), tipEl = $d('.ste-tip');
  function nearAt(sx, sy, r) {
    const all = M.hooks.near.flatMap(f => f(sx, sy, r, stage.clientWidth, stage.clientHeight)), seen = new Set();
    return all.sort((a, b) => a.d - b.d).filter(x => !seen.has(x.id) && seen.add(x.id));
  }
  function chooser(list, sx, sy) {
    M.choices = list.slice(0, 8);
    pickEl.innerHTML = `<p class="ste-pick-h">${list.length} Objekte an dieser Stelle</p>` + M.choices.map((x, i) => `<button type="button" role="menuitem" data-i="${i}" style="--c:${x.color}" aria-label="${esc(x.name + ' – ' + x.sub)}">
      <i></i><span><b>${esc(x.name)}</b><small>${esc(x.sub)}</small></span></button>`).join('');
    pickEl.hidden = false;
    const w = pickEl.offsetWidth, h = pickEl.offsetHeight;
    pickEl.style.left = clamp(sx + 10, 6, stage.clientWidth - w - 6) + 'px'; pickEl.style.top = clamp(sy + 10, 6, stage.clientHeight - h - 6) + 'px';
    setTimeout(() => pickEl.hidden || pickEl.querySelector('button')?.focus({ preventScroll: true }));      // nach dem Klick (sonst holt die Karte den Fokus zurück)
  }
  const closeChooser = () => { if (!pickEl.hidden) { pickEl.hidden = true; M.choices = null; } };
  pickEl.onclick = (e) => { const b = e.target.closest('[data-i]'); if (!b) return; const x = M.choices?.[+b.dataset.i]; closeChooser(); x?.pick(); };
  pickEl.onpointerover = (e) => { const b = e.target.closest('[data-i]'); setHover(b ? M.choices?.[+b.dataset.i] : null, false); };
  pickEl.onkeydown = (e) => {
    const bs = [...pickEl.querySelectorAll('button')], i = bs.indexOf(document.activeElement);
    if (e.key === 'Escape') { closeChooser(); canvas.focus?.(); }
    else if (e.key === 'ArrowDown' || e.key === 'ArrowUp') { e.preventDefault(); bs[(i + (e.key === 'ArrowDown' ? 1 : bs.length - 1)) % bs.length]?.focus(); }
  };
  stage.addEventListener('pointerdown', (e) => { if (!e.target.closest('.ste-pick')) closeChooser(); }, true);
  // Bühne nie scrollen lassen (Fokus/scrollIntoView eines Menüpunkts würde sie sonst samt Karte verschieben)
  stage.addEventListener('scroll', () => { if (stage.scrollTop || stage.scrollLeft) stage.scrollTop = stage.scrollLeft = 0; });
  function setHover(x, tip = true) {
    if (M.hover?.id === x?.id) return;
    M.hover?.hover(false); M.hover = x || null; x?.hover(true);
    canvas.classList.toggle('ste-hot', !!x); flat?.hot(!!x);
    tipEl.hidden = !x || !tip;
    if (x && tip) { tipEl.innerHTML = `<b style="--c:${x.color}">${esc(x.name)}</b><small>${esc(x.sub)}</small>`; }
    M.dirty = true;
  }
  let hoverRaf = 0;
  function hoverAt(sx, sy) {                           // höchstens einmal je Bild
    M.hoverXY = [sx, sy]; if (hoverRaf) return;
    hoverRaf = requestAnimationFrame(() => {
      hoverRaf = 0; if (M.dead || touches.size || !M.hoverXY) return;
      const [x, y] = M.hoverXY, hit = nearAt(x, y, 12)[0] || null;
      setHover(hit);
      if (hit) { tipEl.style.left = Math.min(x + 14, stage.clientWidth - tipEl.offsetWidth - 6) + 'px'; tipEl.style.top = Math.max(6, y - tipEl.offsetHeight - 10) + 'px'; }
    });
  }

  // ---- Zeichnen ----
  function draw() {
    const w = stage.clientWidth, h = stage.clientHeight, dist = camera.position.length();
    const toTarget = camera.position.distanceTo(controls.target), ground = Math.max(dist - 1, 1e-6);     // Nahebene an Abstand anpassen (Nahansicht ↔ Globus)
    camera.near = clamp(Math.min(toTarget, ground) * .3, 1e-5, .5); camera.updateProjectionMatrix(); camera.updateMatrixWorld();
    controls.rotateSpeed = controls.target.lengthSq() > 1e-6 ? .6 : clamp((dist - 1) * .45, .012, .55);
    atmo.value = clamp((dist - 1.4) / 1.2, 0, 1);
    camN.copy(camera.position).normalize();
    earth.updateMatrixWorld(true);
    for (const L of labels) {
      const on = layer[L.layer].group.visible && layer[L.layer].k > .4 && (!L.night || L.night === S.night) && dist < (L.near ?? 99)
        && !(L.layer === 'sites' && M.curPos && layer.current.k > .4 && L.pos.distanceTo(M.curPos) < 2e-5);     // Listenort = bisheriger Ort: nur ein Schild
      wp.copy(L.pos).applyMatrix4(earth.matrixWorld);
      let hide = !on || wp.clone().normalize().dot(camN) < .08;
      if (!hide) { wp.project(camera); hide = Math.abs(wp.z) > 1 || Math.abs(wp.x) > 1.05 || Math.abs(wp.y) > 1.05; }
      L.el.hidden = hide;
      if (!hide) L.el.style.transform = `translate(${((wp.x + 1) / 2 * w).toFixed(1)}px, ${((1 - wp.y) / 2 * h).toFixed(1)}px)`;
    }
    for (const f of M.hooks.draw) f(w, h);
    for (const P of Object.values(patches)) setPatchK(P);
    bordersMat.color.set(U_E.nv.value > .5 ? '#9ff3e4' : '#c5d3ef'); bordersMat.userData.base = .38 + .3 * U_E.nv.value; bordersMat.opacity = bordersMat.userData.base * ease(layer.borders.k);
    $d('.ste-nvpill').hidden = U_E.nv.value < .5;
    const t0 = performance.now(); renderer.render(sc, camera); M.frameMs = performance.now() - t0; M.frames++;
  }
  function frame(now) {
    M.raf = requestAnimationFrame(frame);
    const dt = Math.min(.1, (now - (M.last || now)) / 1000); M.last = now;
    if (M.fly) { M.flyFrames.push(now); M.fly(now); }
    M.spin?.(now);
    for (const f of M.hooks.frame) f(now, dt);
    if (M.anim.size) {
      let busy = false;
      const step = reduced() ? 1 : dt / .45;
      for (const [k] of LAYERS) { const L = layer[k], tg = target(k); if (L.k !== tg) { L.k = tg > L.k ? Math.min(tg, L.k + step) : Math.max(tg, L.k - step); applyK(k); busy = true; } }
      const nt = nvTarget(), nvStep = reduced() ? 1 : dt / .6;
      if (U_E.nv.value !== nt) { U_E.nv.value = nt > U_E.nv.value ? Math.min(nt, U_E.nv.value + nvStep) : Math.max(nt, U_E.nv.value - nvStep); busy = true; }
      if (M.anim.has('reveal')) {
        let r = false;
        for (const P of Object.values(patches)) if (P.reveal < 1) { P.reveal = reduced() ? 1 : Math.min(1, (now - P.revealStart) / 1400); r = true; }
        if (!r) M.anim.delete('reveal'); busy = true;
      }
      if (!busy && !M.fly && !M.spin && !M.anim.has('flat')) M.anim.delete('layers');
      M.dirty = true;
    }
    const flatFull = flat ? flat.tick(dt, M.dirty || M.anim.size > 0, reduced()) : false;
    if (flat && !flat.fading) M.anim.delete('flat');
    if (M.dirty) { M.dirty = false; if (!flatFull) draw(); }           // ganz flach: Globus nicht zeichnen
  }
  const resize = () => { const w = stage.clientWidth, h = stage.clientHeight; if (!w || !h) return; renderer.setSize(w, h, false); camera.aspect = w / h; camera.updateProjectionMatrix(); for (const f of M.hooks.resize) f(); M.dirty = true; };
  const ro = new ResizeObserver(resize); ro.observe(stage); resize();
  for (const night of Object.keys(heats)) if (heats[night]) buildPatch(night);
  { const lg = $d('.ste-legend');
    lg.querySelector('.ste-legend-off').onclick = () => { setLayer('heat', false); if (S.night && !cardEl.hidden) card(nightOf(S.night)); }; }
  function heatOpacity() { for (const P of Object.values(patches)) setPatchK(P); M.anim.add('layers'); M.dirty = true; }
  controls.update();
  M.anim.add('layers');
  M.raf = requestAnimationFrame(frame);
  return {
    setNight, layersChanged, heatChanged, heatOpacity, home, openRun, closeRun: () => closeRun(true),
    nightCard: (night) => card(nightOf(night)),
    state: () => {
      const f = M.flyFrames, gaps = f.slice(1).map((t, i) => t - f[i]);
      return { ctx: S.ctx, night: S.night, run: R.id, runReady: !!R.run, nv: +U_E.nv.value.toFixed(3), shade: +U_E.shade.value.toFixed(3), dist: +camera.position.length().toFixed(4),
        earthRot: +earth.rotation.y.toFixed(5),
        layers: Object.fromEntries(LAYERS.map(([k]) => [k, +layer[k].k.toFixed(3)])), on: { ...S.on }, patches: Object.keys(patches).length,
        heatVisible: Object.values(patches).filter(P => P.group.visible).map(P => P.night), reveal: Object.fromEntries(Object.values(patches).map(P => [P.night, +P.reveal.toFixed(3)])),
        marks: { current: layer.current.group.children.length, proposals: layer.proposals.group.children.length, sites: layer.sites.group.children.length, alts: (M.flatMarks?.alts || []).length },
        borders: M.borders || 0, runs: Object.fromEntries(Object.entries(nightRuns).map(([k, v]) => [k, { ready: v.ready, hits: v.hits, layers: v.layers.length }])),
        flying: !!M.fly || !!M.spin, animating: M.anim.size > 0, frameMs: +M.frameMs.toFixed(2), flyFrameMs: gaps.length ? +(gaps.reduce((a, b) => a + b, 0) / gaps.length).toFixed(2) : null,
        flyMaxMs: gaps.length ? +Math.max(...gaps).toFixed(2) : null, sun: vll(sunNow).map(v => +v.toFixed(2)), card: !cardEl.hidden,
        flat: flat?.state() || null, coord: M.coord || null };
    },
    focus: (lat, lon, dist) => flyTo(dirLL(lat, lon), dist, 0),
    satScreen: () => (S.ctx === 'runs' ? [R.run] : nightRuns[S.night]?.layers || []).filter(Boolean).flatMap(L => L.screen()),
    satSel: () => (S.ctx === 'runs' ? [R.run] : nightRuns[S.night]?.layers || []).filter(Boolean).map(L => L.sel()).find(Boolean) || null,
    chooser: () => pickEl.hidden ? null : [...pickEl.querySelectorAll('b')].map(b => b.textContent),
    flat: flat && { at: flat.at, screenOf: flat.screenOf, flyTo: flat.flyTo, enter: (sx, sy) => enterFlat(sx, sy, ppkAt((FLAT_IN - 1) * R_KM) * 1.15), leave: () => leaveFlat(null) },
    dispose() {
      M.dead = true; cancelAnimationFrame(M.raf); ro.disconnect(); R.run?.dispose();
      for (const P of Object.values(nightRuns)) P.layers.forEach(l => l.dispose());
      controls.dispose(); flat?.dispose(); for (const x of disposables) x.dispose?.(); renderer.dispose(); renderer.forceContextLoss?.();
    },
  };
}

window.SkyTraceErde = { view, embed, href: (night) => '#/skytrace/erde' + (night ? '/' + night : '') };
})();
