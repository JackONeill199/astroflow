'use strict';
/* SkyTrace „Erde“ (#/skytrace/erde[/<Nacht>], 0.35.0): zentrale Erd-Ansicht für Standorte, Standortsuche und Bahnen.
   Globus (three.js aus /vendor/) als Bühne, Ebenen einzeln weich ein-/ausblendbar:
   Orte der Liste · Ort je Nacht · Heatmap der Standortsuche (standortsuche-karten/<Nacht>.json, engine/global_site.heat_map) ·
   Vorschläge mit Unsicherheitsellipse · Satellitenbahnen und Teleskop-Blickrichtung (run3d der Beobachtungen der Nacht) ·
   Grenzen/Küsten (Natural Earth, offline) · Tag/Nacht-Grenze zur Aufnahmezeit.
   „Nachtsicht“: liegt, was gezeigt wird, auf der Nachtseite, hellt der Erd-Shader die Nachtseite weich in ein gedämpftes Blaugrün auf.
   Kartenkacheln (OSM) nur auf Knopfdruck und erst bei starkem Zoom – sonst verlässt kein Ort den Rechner.
   Szene in Erdradien, Achsen wie skytrace-3d.js: ECEF (x, y, z) → three (x, z, −y), Nordpol = +y. Gezeichnet wird nur bei Änderung. */
(() => {
const LIB = '/vendor/', TEX = '/m/skytrace/vendor/earth-2048.jpg', T = '/skytrace';
const R_KM = 6378.137, D2R = Math.PI / 180;
const reduced = () => matchMedia('(prefers-reduced-motion: reduce)').matches;
const LAYERS = [
  ['sites', 'Orte der Liste', '#c9d2e3', 'Alle Standorte aus Einstellungen → Standorte'],
  ['current', 'Ort je Nacht', '#f2d385', 'Ort, der der Nacht bisher zugeordnet ist (Ring)'],
  ['heat', 'Heatmap der Standortsuche', '#f06e32', 'Bodenpunkte, von denen aus die Spuren der Nacht zu sehen waren'],
  ['proposals', 'Vorschläge mit Unsicherheit', '#6b86ff', 'Vorgeschlagener Ort mit 1σ-Ellipse'],
  ['runs', 'Satellitenbahnen und Teleskop', '#5eead4', 'Bahnen der zugeordneten Satelliten und Blickrichtung des Teleskops über die Nacht'],
  ['borders', 'Grenzen und Küsten', '#9fb0d9', 'Natural Earth, ohne Netz'],
  ['terminator', 'Tag/Nacht-Grenze', '#fbbf24', 'Beleuchtung zur Aufnahmezeit'],
];
const DEFAULT_ON = { sites: true, current: true, heat: true, proposals: true, runs: false, borders: true, terminator: true };
const PALETTE = ['#5eead4', '#7c9cff', '#f2a6ff', '#fbbf24', '#4ade80', '#f87171', '#60a5fa', '#fb923c'];
const nf1 = (v, d = 0) => Number.isFinite(v) ? v.toLocaleString('de-DE', { minimumFractionDigits: d, maximumFractionDigits: d }) : '–';
const ease = (k) => k < .5 ? 4 * k * k * k : 1 - Math.pow(-2 * k + 2, 3) / 2;
const clamp = (v, a, b) => Math.min(b, Math.max(a, v));
let lib = null;
function load() {
  return lib ||= Promise.all([import(LIB + 'three.module.min.js'), import(LIB + 'OrbitControls.js')])
    .then(([THREE, c]) => ({ THREE, OrbitControls: c.OrbitControls })).catch(e => { lib = null; throw e; });
}

// Sonnenstand (vereinfachte Formel, ~0,01°): Unterpunkt der Sonne → Richtung in Szenenachsen
function sunLatLon(t) {
  const d = t / 86400 + 2440587.5 - 2451545.0, g = (357.529 + .98560028 * d) * D2R, q = 280.459 + .98564736 * d;
  const L = (q + 1.915 * Math.sin(g) + .02 * Math.sin(2 * g)) * D2R, e = (23.439 - 3.6e-7 * d) * D2R;
  const ra = Math.atan2(Math.cos(e) * Math.sin(L), Math.cos(L)), dec = Math.asin(Math.sin(e) * Math.sin(L));
  const gmst = ((280.46061837 + 360.98564736629 * d) % 360) * D2R;
  return [dec / D2R, (((ra - gmst) / D2R) % 360 + 540) % 360 - 180];
}
// Zeitpunkt einer Nacht ohne genauere Angabe: Mitternacht Ortszeit an der Länge der Region
const nightTime = (night, lon = 0) => Date.parse(night + 'T00:00:00Z') / 1000 + (24 - lon / 15) * 3600;
const fmtNight = (night, long = false) => new Date(night + 'T12:00:00Z').toLocaleDateString('de-DE', long ? { weekday: 'short', day: '2-digit', month: '2-digit', year: 'numeric', timeZone: 'UTC' } : { day: '2-digit', month: '2-digit', year: 'numeric', timeZone: 'UTC' });

const MARKUP = `<div class="ste-main">
    <div class="ste-stage"><canvas aria-label="Drehbarer Globus mit Standorten, Heatmap der Standortsuche und Bahnen"></canvas><div class="ste-labels"></div>
      <div class="ste-tools"><span class="ste-pill ste-nvpill" hidden>${icon('target')}Nachtsicht</span><span class="grow"></span>
        <button class="btn ghost sm ste-home" title="Ganze Region zeigen">${icon('fit')}<span>Übersicht</span></button>
        <button class="btn ghost icon sm ste-fs" title="Vollbild (F)" aria-label="Vollbild">${icon('expand')}</button></div>
      <div class="ste-msg"><span class="spinner"></span>Globus wird geladen …</div>
      <div class="ste-legend" hidden><span class="ramp"></span><span class="ste-legend-t"></span></div>
      <div class="ste-tiles" hidden><button class="btn sm ste-tiles-b">${icon('globe')}Karte laden</button><span class="ste-attr sm"></span></div>
      <div class="ste-card" hidden></div>
      <div class="ste-help">Ziehen dreht · Mausrad/zwei Finger zoomen · Antippen zeigt Details</div></div></div>
  <aside class="ste-side">
    <section><h3 class="ste-sec">Nächte</h3><div class="ste-nightbar"><button class="btn ghost icon sm" data-step="-1" aria-label="Vorige Nacht">${icon('back')}</button>
      <input type="range" class="ste-nightrange" min="0" max="0" value="0" aria-label="Nacht wählen"><button class="btn ghost icon sm" data-step="1" aria-label="Nächste Nacht">${icon('arrow')}</button></div>
      <div class="ste-nights" role="listbox" aria-label="Nächte"></div></section>
    <section><h3 class="ste-sec">Ebenen</h3><div class="ste-layers"></div></section>
    <section><h3 class="ste-sec">Nachtsicht</h3><div class="seg ste-nv" aria-label="Nachtsicht"><button data-nv="auto">Auto</button><button data-nv="on">An</button><button data-nv="off">Aus</button></div>
      <p class="mut sm ste-nvnote"></p></section>
    <p class="ste-note mut sm"></p></aside>`;

/* Ansicht. helpers kommen aus skytrace.js (gemeinsam mit „Standorte aller Nächte“):
   { heatCells, heatColor, nightMarks, ellipsePts, outlines, applyNights, NIGHT_STATUS, globalHtml, kmText, arcText, clockText, shortName } */
async function view(el, nightArg, H) {
  crumbs([['SkyTrace', '#/skytrace'], ['Erde']]);
  el.innerHTML = `<div class="pagehead"><div style="min-width:0"><h1>Erde</h1>
      <p class="mut readable">Standorte, Heatmap der Standortsuche und Satellitenbahnen auf einem Globus. Jede Ebene lässt sich einzeln ein- und ausschalten; liegt etwas auf der Nachtseite, hellt die Nachtsicht sie auf.</p></div>
      <div class="page-actions"><a class="btn ghost" href="#/skytrace/standorte">${icon('list')}Standorte aller Nächte</a></div></div>
    <div class="ste" id="steHost">${MARKUP}</div>`;
  const host = $('#steHost', el), $d = (s) => host.querySelector(s);
  let data = null, sites = [], maps = [], heats = {}, scene = null, dead = false;
  try {
    const [d, s] = await Promise.all([api(T + '/sitesearch/nights'), api('/sites?nights=false').catch(() => ({}))]);
    data = d; sites = (s.list || []).filter(x => x.lat != null && x.lon != null); maps = d.maps || [];
  } catch (e) { $d('.ste-msg').innerHTML = `${icon('alert')}<span>${esc(e.message)}</span>`; return; }
  if (!el.isConnected) return;
  const nights = (data.result?.nights || []).slice().sort((a, b) => a.night < b.night ? -1 : 1);
  const S = {
    night: nights.some(n => n.night === nightArg) ? nightArg : null,
    on: { ...DEFAULT_ON, ...store.get('skytrace.erde.layers', {}) }, nv: store.get('skytrace.erde.nv', 'auto'),
  };
  if (!S.night && !nightArg) S.night = (nights.filter(n => maps.includes(n.night) || n.proposal?.kind === 'global').at(-1) || nights.filter(n => n.proposal && !n.applied).at(-1))?.night || null;
  const range = $d('.ste-nightrange');
  range.max = String(Math.max(0, nights.length));        // 0 = alle Nächte, 1… = Nacht
  const listEl = $d('.ste-nights');
  // Ränder der Liste weich ausblenden, wo weitergescrollt werden kann (angeschnittene Einträge wirken sonst abgeschnitten)
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
  const layersEl = $d('.ste-layers');
  layersEl.innerHTML = LAYERS.map(([k, name, c, tip]) => `<label class="ste-layer" style="--c:${c}" title="${esc(tip)}"><input type="checkbox" data-layer="${k}"${S.on[k] ? ' checked' : ''}>
    <i></i><span>${esc(name)}</span></label>`).join('');
  const drawNv = () => $$('.ste-nv button', host).forEach(b => b.classList.toggle('on', b.dataset.nv === S.nv));
  drawNights(); drawNv();
  const nightOf = (night) => nights.find(n => n.night === night);
  async function loadHeats(list) {
    const want = list.filter(n => maps.includes(n) && !(n in heats));
    if (!want.length) return;
    for (let i = 0; i < want.length; i += 100) {             // Endpunkt nimmt höchstens 120 Nächte je Anfrage
      try { Object.assign(heats, (await api(T + '/sitesearch/nights/heatmaps?nights=' + encodeURIComponent(want.slice(i, i + 100).join(',')))).heat || {}); }
      catch { /* ohne Heatmap – beim Wählen der Nacht erneut versucht */ }
    }
  }
  await loadHeats(maps);
  if (!el.isConnected) return;
  let mods;
  try { mods = await load(); } catch (e) { $d('.ste-msg').innerHTML = `${icon('alert')}<span>3D-Bibliothek konnte nicht geladen werden: ${esc(e.message)}</span>`; return; }
  if (!el.isConnected) return;
  try { scene = globe(host, mods, { nights, sites, heats, S, H, nightOf, onPick: (night) => pick(night, true), onCompute: computeHeat }); host._ste = scene; }
  catch (e) { $d('.ste-msg').innerHTML = `${icon('alert')}<span>3D-Darstellung nicht möglich (WebGL nicht verfügbar?): ${esc(e.message)}</span>`; return; }
  $d('.ste-msg').hidden = true;
  if (!nights.length) $d('.ste-note').innerHTML = 'Noch keine Standortsuche: <a href="#/skytrace/standorte">Standorte aller Nächte</a> rechnet je Nacht Vorschläge und Heatmaps. Bis dahin zeigt der Globus die Orte der Liste.';

  function pick(night, fromScene = false) {
    S.night = night || null;
    drawNights();
    const url = '#/skytrace/erde' + (S.night ? '/' + S.night : '');
    if (location.hash !== url) history.replaceState(null, '', url);
    crumbs([['SkyTrace', '#/skytrace'], ['Erde', '#/skytrace/erde'], ...(S.night ? [[fmtNight(S.night)]] : [])]);
    scene.setNight(S.night, { card: !!S.night, fly: true, fromScene });
    if (S.night && maps.includes(S.night) && !heats[S.night]) {         // fehlte beim Laden (Netz, Fehler): einzeln nachholen
      const want = S.night; delete heats[want];
      loadHeats([want]).then(() => { if (heats[want] && scene) scene.heatChanged(want); });
    }
  }
  listEl.onclick = (e) => { const b = e.target.closest('[data-n]'); if (b) pick(b.dataset.n); };
  range.oninput = () => pick(+range.value ? nights[+range.value - 1].night : null);
  $$('[data-step]', host).forEach(b => b.onclick = () => { const i = clamp(+range.value + +b.dataset.step, 0, nights.length); pick(i ? nights[i - 1].night : null); });
  layersEl.onchange = (e) => {
    const k = e.target.dataset.layer; if (!k) return;
    S.on[k] = e.target.checked; store.set('skytrace.erde.layers', S.on); scene.layersChanged(k);
  };
  $d('.ste-nv').onclick = (e) => { const b = e.target.closest('[data-nv]'); if (!b) return; S.nv = b.dataset.nv; store.set('skytrace.erde.nv', S.nv); drawNv(); scene.layersChanged('nv'); };
  const fullscreen = () => { if (document.fullscreenElement) document.exitFullscreen(); else host.requestFullscreen?.().catch(() => {}); };
  $d('.ste-fs').onclick = fullscreen;
  $d('.ste-home').onclick = () => scene.home();

  // Heatmap für eine Nacht berechnen (Hintergrundauftrag wie in „Standorte aller Nächte“), danach neu aufbauen
  async function computeHeat(night, btn) {
    if (btn) btn.disabled = true;
    try { await send(T + '/sitesearch/nights/heatmap', { night, global_km: store.get('skytrace.nightsGlobal', 1500) || 1500, clock: store.get('skytrace.nightsClock', true) }); }
    catch (e) { toast(e.message, 'err'); if (btn) btn.disabled = false; return; }
    for (;;) {
      await new Promise(r => setTimeout(r, 1500));
      if (dead || !el.isConnected) return;
      let d; try { d = await api(T + '/sitesearch/nights/heatmap?night=' + encodeURIComponent(night)); } catch { continue; }
      const j = d.job;
      if (j && j.state === 'running') { if (btn?.isConnected) btn.textContent = j.text ? j.text.replace(night + ': ', '').slice(0, 50) : 'Rechnet …'; continue; }
      if (j?.state === 'error') toast(j.error || 'Heatmap fehlgeschlagen', 'err');
      else toast(d.heat ? `Heatmap für ${night} berechnet.` : (j?.result?.text || 'Keine Heatmap.'));
      if (d.heat) { heats[night] = d.heat; if (!maps.includes(night)) maps.push(night); drawNights(); scene.heatChanged(night); }
      return;
    }
  }
  scene.setNight(S.night, { card: false, fly: true, first: true });
  const onKey = (e) => {
    if (e.ctrlKey || e.metaKey || e.altKey || e.target.closest('input,select,textarea')) return;
    if (e.key === 'f' || e.key === 'F') { fullscreen(); e.preventDefault(); }
    else if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') { const i = clamp(+range.value + (e.key === 'ArrowRight' ? 1 : -1), 0, nights.length); pick(i ? nights[i - 1].night : null); e.preventDefault(); }
  };
  document.addEventListener('keydown', onKey);
  return {
    destroy() { dead = true; scene?.dispose(); host._ste = null; document.removeEventListener('keydown', onKey); if (document.fullscreenElement === host) document.exitFullscreen(); },
    actions: () => [['globe', 'Erde: Übersicht', 'Ganze Region zeigen', () => scene?.home()]],
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
  // Zellen mit nur 1–2 Stimmen (zufällige Bodenpunkte einzelner Spuren) fast durchsichtig, Häufungen deckend
  float a = smoothstep(th, th + 0.08, v) * smoothstep(0.04, 0.3, v) * (0.3 + 0.65 * v) * opacity;
  if (a < 0.004) discard;
  gl_FragColor = vec4(ramp(v), a);
}`;

function globe(host, { THREE, OrbitControls }, ctx) {
  const { nights, sites, heats, S, H, nightOf, onPick, onCompute } = ctx;
  const $d = (s) => host.querySelector(s), stage = $d('.ste-stage'), canvas = $d('.ste-stage canvas'), labelsHost = $d('.ste-labels');
  const llv = (lat, lon, r = 1) => { const a = lat * D2R, b = lon * D2R; return new THREE.Vector3(r * Math.cos(a) * Math.cos(b), r * Math.sin(a), -r * Math.cos(a) * Math.sin(b)); };
  const vll = (v) => { const n = v.clone().normalize(); return [Math.asin(n.y) / D2R, Math.atan2(-n.z, n.x) / D2R]; };
  const V = (p) => new THREE.Vector3(p[0] / R_KM, p[2] / R_KM, -p[1] / R_KM);           // ECEF bzw. System D (km) → Szene
  const U = (p) => new THREE.Vector3(p[0], p[2], -p[1]).normalize();
  const renderer = new THREE.WebGLRenderer({ canvas, antialias: true });
  renderer.setPixelRatio(Math.min(devicePixelRatio || 1, 2));
  const sc = new THREE.Scene(); sc.background = new THREE.Color('#03050b');
  const camera = new THREE.PerspectiveCamera(38, 1, .001, 400);
  camera.position.set(0, 1.2, 4.2);
  const controls = new OrbitControls(camera, canvas);
  controls.enablePan = false; controls.enableZoom = false; controls.enableDamping = false; controls.rotateSpeed = .5;
  const disposables = [], keep = (x) => { disposables.push(x); return x; };
  const glow = keep(glowTexture(THREE)), ring = keep(glowTexture(THREE, true));
  const M = { dirty: true, raf: 0, fly: null, frames: 0, frameMs: 0, flyFrames: [], anim: new Set() };
  controls.addEventListener('change', () => { M.dirty = true; });
  controls.addEventListener('start', () => { M.fly = null; });

  // ---- Sterne, Erde, Saum ----
  { const n = 1500, pos = new Float32Array(n * 3); let s = 11;
    const rnd = () => (s = (s * 16807) % 2147483647) / 2147483647;
    for (let i = 0; i < n; i++) { const z = rnd() * 2 - 1, a = rnd() * Math.PI * 2, r = Math.sqrt(1 - z * z) * 150; pos.set([r * Math.cos(a), z * 150, r * Math.sin(a)], i * 3); }
    const g = keep(new THREE.BufferGeometry()); g.setAttribute('position', new THREE.BufferAttribute(pos, 3));
    sc.add(new THREE.Points(g, keep(new THREE.PointsMaterial({ color: '#cdd6ff', size: 1.2, sizeAttenuation: false, transparent: true, opacity: .7 })))); }
  const tex = keep(new THREE.TextureLoader().load(TEX, () => { M.dirty = true; }));
  tex.anisotropy = 8;
  const U_E = { map: { value: tex }, sun: { value: new THREE.Vector3(1, 0, 0) }, shade: { value: S.on.terminator ? 1 : 0 }, nv: { value: 0 } };
  const earth = new THREE.Mesh(keep(new THREE.SphereGeometry(1, 128, 96)), keep(new THREE.ShaderMaterial({ uniforms: U_E, vertexShader: EARTH_VS, fragmentShader: EARTH_FS })));
  sc.add(earth);
  const atmo = { value: 1 };
  sc.add(new THREE.Mesh(keep(new THREE.SphereGeometry(1.03, 64, 48)), keep(new THREE.ShaderMaterial({
    side: THREE.BackSide, blending: THREE.AdditiveBlending, transparent: true, depthWrite: false, uniforms: { k: atmo },
    vertexShader: 'varying vec3 n; void main(){ n = normalize(normalMatrix * normal); gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }',
    fragmentShader: 'uniform float k; varying vec3 n; void main(){ float i = pow(max(0.0, 0.72 - dot(n, vec3(0.0, 0.0, 1.0))), 3.0); gl_FragColor = vec4(0.42, 0.6, 1.0, 1.0) * i * 1.4 * k; }' }))));

  // ---- Ebenen: Gruppe + Deckkraft k (weich zum Ziel) ----
  const layer = {}, mats = {};
  for (const [k] of LAYERS) { layer[k] = { k: 0, group: new THREE.Group(), mats: [] }; sc.add(layer[k].group); }
  const addMat = (k, m, base = 1) => { m.transparent = true; m.userData.base = base; layer[k].mats.push(m); return keep(m); };
  const sprite = (k, color, size, base = 1, map = glow) => { const m = addMat(k, new THREE.SpriteMaterial({ map, color, sizeAttenuation: false, depthWrite: false, depthTest: true }), base);
    const s = new THREE.Sprite(m); s.scale.setScalar(size); s.userData.size = size; return s; };
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
        const j = (i + 1) % m, a = r.lat[i] * D2R, b = r.lon[i] * D2R, c = r.lat[j] * D2R, d = r.lon[j] * D2R, q = 1.0006;
        pos[o++] = q * Math.cos(a) * Math.cos(b); pos[o++] = q * Math.sin(a); pos[o++] = -q * Math.cos(a) * Math.sin(b);
        pos[o++] = q * Math.cos(c) * Math.cos(d); pos[o++] = q * Math.sin(c); pos[o++] = -q * Math.cos(c) * Math.sin(d);
      }
    }
    const g = keep(new THREE.BufferGeometry()); g.setAttribute('position', new THREE.BufferAttribute(pos, 3));
    layer.borders.group.add(new THREE.LineSegments(g, bordersMat)); M.borders = n; M.dirty = true;
  });

  // Orte der Liste
  const picks = [];                                    // anklickbare Sprites: {obj, kind, night|site}
  for (const s of sites) {
    const p = llv(s.lat, s.lon, 1.0012), o = sprite('sites', '#dfe6f5', .016, .95); o.position.copy(p); layer.sites.group.add(o);
    picks.push({ obj: o, kind: 'site', site: s, layer: 'sites' });
    label(s.name || 'Ort', null, '#c9d2e3', p, { layer: 'sites', near: 1.9, cls: 'site' });
  }
  // Ort je Nacht (bisher zugeordnet) und Vorschläge: werden je Auswahl neu gebaut
  const coords = (n) => H.nightMarks(n, sites);
  function rebuildMarks() {
    for (const k of ['current', 'proposals']) { layer[k].group.clear(); layer[k].mats = layer[k].mats.filter(m => m.userData.keep); }
    dropLabels(L => L.layer === 'current' || L.layer === 'proposals');
    for (let i = picks.length - 1; i >= 0; i--) if (picks[i].layer !== 'sites') picks.splice(i, 1);
    const shown = S.night ? [nightOf(S.night)].filter(Boolean) : nights;
    const curSeen = new Set();
    M.curPos = null;
    for (const n of shown) {
      const m = coords(n), sel = n.night === S.night;
      if (m.cur && !curSeen.has(m.cur.name + m.cur.lat)) {
        curSeen.add(m.cur.name + m.cur.lat);
        const o = sprite('current', '#f2d385', sel ? .034 : .024, 1, ring); o.position.copy(llv(m.cur.lat, m.cur.lon, 1.0016)); layer.current.group.add(o);
        picks.push({ obj: o, kind: 'night', night: n.night, layer: 'current' });
        if (sel) { label('bisher: ' + (m.cur.name || 'Ort'), null, '#f2d385', o.position, { layer: 'current', cls: 'cur' }); M.curPos = llv(m.cur.lat, m.cur.lon, 1.0012); }
      }
      if (m.prop && !n.applied) {
        const p = llv(m.prop.lat, m.prop.lon, 1.002), o = sprite('proposals', '#7f98ff', sel ? .03 : .02, 1); o.position.copy(p); layer.proposals.group.add(o);
        picks.push({ obj: o, kind: 'night', night: n.night, layer: 'proposals' });
        layer.proposals.group.add(lineOn('proposals', H.ellipsePts(m.prop, 72).map(([la, lo]) => llv(la, lo, 1.0018)), '#8ea4ff', sel ? .95 : .6, true));
        if (sel || !S.night) label(m.prop.kind === 'list' ? m.prop.name : m.prop.kind === 'global' ? 'Unbekannter Ort' : 'Vorschlag', sel ? fmtNight(n.night) + ' · ± ' + H.kmText((m.prop.ellipse?.major ?? m.prop.acc / 1000)) : fmtNight(n.night),
          '#7f98ff', p, { layer: 'proposals', onClick: () => onPick(n.night), near: sel ? 99 : 2.6 });
      }
    }
    for (const k of ['current', 'proposals']) applyK(k);
    M.dirty = true;
  }

  // ---- Heatmap je Nacht: Kugelkappe über dem belegten Ausschnitt, grob (20 km) + fein (1 km, beim Heranzoomen) ----
  const patches = {};
  function patch(cells, km, r) {
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
    const g = keep(new THREE.SphereGeometry(r, clamp(cols >> 2, 8, 96), clamp(rows >> 2, 8, 96), (lo0 + 180) * D2R, (lo1 - lo0) * D2R, (90 - la1) * D2R, (la1 - la0) * D2R));
    const m = keep(new THREE.ShaderMaterial({ uniforms: u, transparent: true, depthWrite: false, vertexShader: 'varying vec2 vUv; void main(){ vUv = uv; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }', fragmentShader: HEAT_FS }));
    const mesh = new THREE.Mesh(g, m); mesh.renderOrder = 2;
    return { mesh, u, max, box: [la0, la1, lo0, lo1] };
  }
  function buildPatch(night) {
    const h = heats[night]; if (!h) return null;
    const old = patches[night]; if (old) { layer.heat.group.remove(old.group); }
    const coarse = patch(H.heatCells(h.coarse), h.coarse?.cell_km || 20, 1.0009), fine = patch(H.heatCells(h.fine), h.fine?.km || 1, 1.0013);
    if (!coarse && !fine) return null;
    const group = new THREE.Group(); if (coarse) group.add(coarse.mesh); if (fine) group.add(fine.mesh);
    layer.heat.group.add(group);
    const P = patches[night] = { night, group, coarse, fine, reveal: 0, revealStart: 0, h };
    return P;
  }
  function revealHeat(list) {
    const now = performance.now();
    for (const P of list) { P.revealStart = now; P.reveal = reduced() ? 1 : 0; }
    M.anim.add('reveal'); M.dirty = true;
  }

  // ---- Satellitenbahnen und Teleskop (run3d je Beobachtung der Nacht, System D → erdfest) ----
  const runs = {};
  const Y = new THREE.Vector3(0, 1, 0);
  async function loadRuns(night) {
    if (runs[night]) return runs[night];
    const n = nightOf(night), obs = [];
    for (const o of n?.observations || []) if (!obs.some(x => x.run === o.run && x.session === o.session)) obs.push(o);
    const P = runs[night] = { group: new THREE.Group(), notes: [], hits: 0, obs: obs.length, ready: false, time: null, points: [] };
    layer.runs.group.add(P.group); P.group.visible = false;
    let ci = 0;
    for (const o of obs.slice(0, 6)) {
      let d;
      try { d = await api(`${T}/run3d?run=${encodeURIComponent(o.run)}&session=${encodeURIComponent(o.session)}`); } catch (e) { P.notes.push(`${H.shortName(o.session)}: ${e.message}`); continue; }
      if (M.dead) return P;
      if (!d.ok) { P.notes.push(`${H.shortName(o.session)}: ${d.reason || 'keine 3D-Daten'}`); continue; }
      const toE = (p, t) => V(p).applyAxisAngle(Y, -d.earth_rate * (t - d.anchor));
      const dirE = (p, t) => U(p).applyAxisAngle(Y, -d.earth_rate * (t - d.anchor));
      const obsP = V(d.observer), Pt = d.pointing;
      P.points.push(obsP.clone());
      P.time ??= d.anchor;
      // Blickrichtung über die Nacht: Fächer aus höchstens 48 Strahlen (~600 km), mittlerer kräftiger
      const step = Math.max(1, Math.floor(Pt.t.length / 48)), fan = [];
      for (let i = 0; i < Pt.t.length; i += step) fan.push(obsP, obsP.clone().add(dirE(Pt.center[i], Pt.t[i]).multiplyScalar(.095)));
      { const g = keep(new THREE.BufferGeometry().setFromPoints(fan)); P.group.add(new THREE.LineSegments(g, addMat('runs', new THREE.LineBasicMaterial({ color: '#f2d385', depthWrite: false }), .32))); }
      const mid = Math.floor(Pt.t.length / 2);
      P.group.add(lineOn('runs', [obsP, obsP.clone().add(dirE(Pt.center[mid], Pt.t[mid]).multiplyScalar(.16))], '#ffe8a8', .95));
      const so = sprite('runs', '#f2d385', .02); so.position.copy(obsP); P.group.add(so);
      label(H.shortName(o.session), 'Teleskop · ' + (d.target?.name || ''), '#f2d385', obsP, { layer: 'runs', night, near: 2.2 });
      for (const h of d.hits || []) {
        const color = PALETTE[ci++ % PALETTE.length];
        if (h.kind === 'sat') {
          const tr = h.track.map((p, i) => toE(p, h.window[0] + 2 * i));
          P.group.add(lineOn('runs', tr, color, .95));
          if (h.ring?.length) P.group.add(lineOn('runs', h.ring.map(p => toE(p, h.t_pass)), color, h.alternative ? .14 : .26));
          const at = tr[clamp(Math.round((h.t_pass - h.window[0]) / 2), 0, tr.length - 1)];
          P.points.push(at.clone());
          const s = sprite('runs', color, .022); s.position.copy(at); P.group.add(s);
          label(h.name || 'Satellit', `${h.candidate || ''} · ${nf1(h.at_t0?.height_km)} km`, color, at, { layer: 'runs', night, near: 3.5 });
          for (const seg of h.fov_segments || []) { const pts = seg.pos.map((p, i) => toE(p, h.t_pass + seg.t[i])); /* seg.t relativ zum Durchgang (orbit3d: fine − t0) */ if (pts.length > 1) P.group.add(lineOn('runs', pts, '#ffffff', 1)); }
        } else if (h.ecef?.length) {
          const pts = h.ecef.map(V); P.group.add(lineOn('runs', pts, color, 1));
          label(h.name || (h.kind === 'meteor' ? 'Meteor' : 'Flugzeug'), h.candidate || '', color, pts.at(-1), { layer: 'runs', night, near: 2.2 });
        }
        P.hits++;
      }
      for (const m of d.missing || []) P.notes.push(`${m.candidate}: ${m.name || 'NORAD ' + m.norad_id} – ${m.reason}`);
    }
    P.ready = true;
    applyK('runs'); M.dirty = true;
    return P;
  }

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
    const f = ease(layer.heat.k), sel = !S.night || P.night === S.night;
    const dist = camera.position.length(), fineK = P.fine ? clamp((1.55 - dist) / .35, 0, 1) : 0;
    P.group.visible = sel && f > .002;
    if (P.coarse) { P.coarse.u.opacity.value = f * (1 - .55 * fineK); P.coarse.u.reveal.value = ease(P.reveal); }
    if (P.fine) { P.fine.u.opacity.value = f * fineK; P.fine.u.reveal.value = ease(P.reveal); }
  }
  const target = (k) => {
    if (k === 'runs') return S.on.runs && S.night && runs[S.night]?.ready ? 1 : 0;
    if (k === 'heat') return S.on.heat ? 1 : 0;
    return S.on[k] ? 1 : 0;
  };
  // Nachtsicht: Auto = an, sobald Orte/Heatmap/Vorschläge gezeigt werden und die betrachtete Region auf der Nachtseite liegt
  const sunNow = new THREE.Vector3(1, 0, 0);
  function nvTarget() {
    if (S.nv === 'on') return 1;
    if (S.nv === 'off' || !S.on.terminator) return 0;
    const any = ['sites', 'current', 'heat', 'proposals', 'runs'].some(k => S.on[k]);
    const look = M.focus || camera.position.clone().normalize();
    return any && look.dot(sunNow) < .12 ? 1 : 0;
  }
  function layersChanged(k) {
    if (k === 'heat' && S.on.heat) revealHeat(Object.values(patches).filter(P => !S.night || P.night === S.night));
    if (k === 'runs' && S.on.runs && S.night) loadRuns(S.night).then((P) => { if (!M.dead) { showRuns(); applyK('runs'); M.anim.add('layers'); notes(); if (S.night && !cardEl.hidden) card(nightOf(S.night)); if (P?.points.length && S.on.runs) home(); } });
    M.anim.add('layers'); M.dirty = true;
    notes();
  }
  function showRuns() { for (const [night, P] of Object.entries(runs)) P.group.visible = night === S.night; }

  // ---- Kamera: Fahrt (Kugel-Slerp mit Bogen nach außen) zu Richtung und Abstand ----
  function flyTo(dir, dist, ms = 1500) {
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
      M.flyFrames.push(now);
      if (k >= 1) M.fly = null;
      M.dirty = true;
    };
  }
  // Region einer Nacht: Heatmap-Ausschnitt, Vorschlag, bisheriger Ort; ohne Nacht alle Orte
  function region(night) {
    const pts = [];
    const add = (la, lo) => pts.push(llv(la, lo));
    const ns = night ? [nightOf(night)].filter(Boolean) : nights;
    for (const n of ns) {
      const m = coords(n);
      if (m.prop) add(m.prop.lat, m.prop.lon);
      if (m.cur) add(m.cur.lat, m.cur.lon);
      const P = patches[n.night];
      if (P?.coarse && (night || S.on.heat)) { const [a, b, c, d] = P.coarse.box; add(a, c); add(b, d); add(a, d); add(b, c); }
    }
    const rp = night && S.on.runs && runs[night]?.ready ? runs[night] : null;
    if (rp) pts.push(...rp.points.map(p => p.clone().normalize()));
    if (!pts.length) for (const s of sites) add(s.lat, s.lon);
    if (!pts.length) return { dir: new THREE.Vector3(.6, .55, -.3).normalize(), dist: 3.6 };
    const c = pts.reduce((a, p) => a.add(p), new THREE.Vector3()).normalize();
    const spread = Math.max(...pts.map(p => p.angleTo(c)), .004);
    const narrow = stage.clientWidth / Math.max(1, stage.clientHeight) < 1 ? 1.6 : 1;
    return { dir: c, dist: clamp(1 + spread * 2.6 * narrow + .03, 1.09, 3.6) };      // mindestens ~570 km Höhe: Umgebung bleibt erkennbar
  }
  function home() { const r = region(S.night); M.focus = r.dir; flyTo(r.dir, r.dist); M.anim.add('layers'); }

  // ---- Auswahl einer Nacht ----
  function setNight(night, { card: showCard = false, fly = true, first = false } = {}) {
    S.night = night;
    // Sonne zur Aufnahmezeit: run3d-Anker, sonst Mitternacht Ortszeit an der Region; ohne Nacht: jetzt
    const r = region(night), [, lon] = vll(r.dir);
    const t = night ? (runs[night]?.time || nightTime(night, lon)) : Date.now() / 1000;
    const [sla, slo] = sunLatLon(t); sunNow.copy(llv(sla, slo)); U_E.sun.value.copy(sunNow);
    M.sunTime = t;
    rebuildMarks();
    for (const P of Object.values(patches)) setPatchK(P);
    if (S.on.heat) revealHeat(Object.values(patches).filter(P => !night || P.night === night));
    showRuns();
    if (S.on.runs && night) {
      const had = runs[night]?.ready;
      loadRuns(night).then((P) => { if (!M.dead && S.night === night) { showRuns(); applyK('runs'); M.anim.add('layers'); notes(); if (!had && P?.points.length) home(); } });
    }
    M.focus = r.dir;
    if (fly) flyTo(r.dir, r.dist, first ? 1800 : 1500);
    if (showCard && night) card(nightOf(night)); else if (!night) cardEl.hidden = true;
    M.anim.add('layers'); notes(); M.dirty = true;
  }
  function heatChanged(night) { const P = buildPatch(night); if (P) { setPatchK(P); if (S.on.heat) revealHeat([P]); } if (S.night === night) card(nightOf(night)); M.dirty = true; }

  // ---- Detailkarte ----
  const cardEl = $d('.ste-card');
  function card(n) {
    if (!n) { cardEl.hidden = true; return; }
    const p = n.proposal, c = n.current || {}, hh = heats[n.night];
    const [label, cls] = n.applied ? ['Übernommen', 'ok'] : p?.kind === 'global' ? ['Unbekannter Ort', 'acc'] : (H.NIGHT_STATUS[n.status] || [n.status || '–', '']);
    const prop = !p ? '' : p.kind === 'list' ? `<b>${esc(p.name || 'Ort der Liste')}</b><span class="mut sm">bestehender Ort · ${p.sure} von ${n.tracks} Spuren · quer ${H.arcText(p.rms)}</span>`
      : p.kind === 'global' ? H.globalHtml(p, n, sites)
      : `<b>Neuer Ort ${p.shift_from_current_km != null ? H.kmText(p.shift_from_current_km) + ' neben „' + esc(c.name || 'bisher') + '“' : ''}</b><span class="mut sm">± ${H.kmText(p.accuracy_m / 1000)} · ${p.sure} von ${n.tracks} Spuren · quer ${H.arcText(p.rms)}${H.clockText(p) ? ' · ' + H.clockText(p) : ''}</span>`;
    const runsOf = [...new Map((n.observations || []).map(o => [o.run, o])).values()];
    const rp = runs[n.night];
    cardEl.innerHTML = `<div class="ste-card-h"><div><b>${esc(fmtNight(n.night, true))}</b><small>${esc([...new Set((n.observations || []).map(o => H.shortName(o.session)))].join(' · '))}</small></div>
        <span class="chip ${cls}">${esc(label)}</span><button class="btn ghost icon sm" data-a="x" aria-label="Schließen">${icon('x')}</button></div>
      <div class="ste-card-g"><div><small class="dim">Bisher</small><b>${esc(c.name || 'kein Ort')}</b><span class="mut sm">${c.sure ?? n.before ?? 0} von ${n.tracks} Spuren eindeutig · quer ${H.arcText(c.rms)}</span></div>
        ${p ? `<div><small class="dim">Vorschlag</small>${prop}</div>` : ''}
        ${hh ? `<div><small class="dim">Heatmap</small><span class="mut sm">Suchgebiet ± ${hh.region_km} km · Uhr ± ${Math.round(hh.clock_s)} s · bis ${patches[n.night]?.coarse?.max ?? hh.coarse?.max ?? '–'} verschiedene Spuren je ${hh.coarse?.cell_km || 20}-km-Zelle${hh.fine ? ' · fein 1 km beim Heranzoomen' : ''}</span></div>` : ''}
        ${rp?.ready ? `<div><small class="dim">Bahnen</small><span class="mut sm">${rp.hits} zugeordnete Objekte aus ${rp.obs} Beobachtung${rp.obs === 1 ? '' : 'en'}</span></div>` : ''}</div>
      ${n.note ? `<p class="dim sm">${esc(n.note)}</p>` : ''}
      <div class="row ste-card-a">${p && !n.applied ? '<button class="btn sm" data-a="apply">Für diese Nacht übernehmen …</button>' : ''}
        <button class="btn ghost sm" data-a="heat">${hh ? 'Heatmap neu berechnen' : 'Heatmap berechnen'}</button>
        ${runsOf.slice(0, 3).map(o => `<a class="btn ghost sm" href="#/skytrace/${encodeURIComponent(o.run)}/beobachtungen">${esc(H.shortName(o.session))}</a>`).join('')}
        ${runsOf[0] ? `<a class="btn ghost sm" href="#/skytrace/${encodeURIComponent(runsOf[0].run)}/3d" title="Teleskopweg und Durchgänge über die Nacht">${icon('globe')}3D-Lauf</a>` : ''}</div>`;
    cardEl.onclick = async (e) => {
      const a = e.target.closest('[data-a]')?.dataset.a;
      if (a === 'x') cardEl.hidden = true;
      else if (a === 'apply') { if (await H.applyNights([n])) { n.applied = Math.floor(Date.now() / 1000); rebuildMarks(); card(n); } }
      else if (a === 'heat') onCompute(n.night, e.target.closest('button'));
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
    if (S.on.runs && !S.night) out.push('Satellitenbahnen: eine Nacht wählen.');
    const rp = S.night && runs[S.night];
    if (S.on.runs && rp && !rp.ready) out.push('Bahnen werden berechnet …');
    if (S.on.runs && rp?.ready) { out.push(rp.hits ? `${rp.hits} Bahnen/Treffer, Teleskop-Blickrichtung als Fächer über die Nacht.` : 'In dieser Nacht keine Katalogtreffer – gezeigt wird die Blickrichtung des Teleskops.'); out.push(...rp.notes); }
    if (S.night && !heats[S.night] && S.on.heat) out.push('Für diese Nacht liegt keine Heatmap vor – „Heatmap berechnen“ in der Karte der Nacht holt sie nach.');
    if (S.on.terminator) out.push(`Beleuchtung ${S.night ? (runs[S.night]?.time ? 'zur Aufnahmezeit' : '≈ Mitternacht Ortszeit') : 'jetzt'} (${new Date(M.sunTime * 1000).toISOString().slice(0, 16).replace('T', ' ')} UTC).`);
    out.push('Erde: NASA Blue Marble · Umrisse: Natural Earth.');
    $d('.ste-note').textContent = out.join(' ');
    const P = S.night ? patches[S.night] : null, any = Object.keys(patches).length;
    const lg = $d('.ste-legend');
    lg.hidden = !S.on.heat || !(P || (!S.night && any));
    $d('.ste-legend-t').textContent = P ? `verschiedene Spuren: 1 … ${P.coarse?.max ?? P.fine?.max}` : 'verschiedene Spuren (je Nacht relativ)';
  }

  // ---- Kartenkacheln (OSM) nur auf Knopfdruck, erst bei starkem Zoom ----
  const tilesBox = $d('.ste-tiles'), tilesBtn = $d('.ste-tiles-b');
  let tilesMesh = null;
  const clearTiles = () => { if (!tilesMesh) return; sc.remove(tilesMesh); tilesMesh.geometry.dispose(); tilesMesh.material.map?.dispose(); tilesMesh.material.dispose(); tilesMesh = null; $d('.ste-attr').textContent = ''; tilesBtn.innerHTML = `${icon('globe')}Karte laden`; M.dirty = true; };
  tilesBtn.onclick = async () => {
    if (tilesMesh) { clearTiles(); return; }
    const [lat, lon] = vll(camera.position), alt = (camera.position.length() - 1) * R_KM;
    const z = clamp(Math.round(Math.log2(40000 / Math.max(alt * 1.2, 2))) + 1, 5, 14), n = 2 ** z, N = 4;
    const tx = Math.floor((lon + 180) / 360 * n), ty = Math.floor((1 - Math.log(Math.tan(lat * D2R) + 1 / Math.cos(lat * D2R)) / Math.PI) / 2 * n);
    const x0 = tx - N / 2 + 1, y0 = clamp(ty - N / 2 + 1, 0, n - N);
    const tLat = (y) => Math.atan(Math.sinh(Math.PI * (1 - 2 * y / n))) / D2R;
    const la1 = tLat(y0), la0 = tLat(y0 + N), lo0 = x0 / n * 360 - 180, lo1 = (x0 + N) / n * 360 - 180;
    const cv = document.createElement('canvas'); cv.width = cv.height = 256 * N; const g = cv.getContext('2d');
    tilesBtn.disabled = true; tilesBtn.textContent = 'Lädt …';
    const get = (x, y) => new Promise((ok, fail) => { const im = new Image(); im.crossOrigin = 'anonymous'; im.onload = () => ok(im); im.onerror = () => fail(new Error('Kachel nicht geladen'));
      im.src = `https://tile.openstreetmap.org/${z}/${((x % n) + n) % n}/${y}.png`; });
    try {
      await Promise.all(Array.from({ length: N * N }, (_, i) => { const x = x0 + i % N, y = y0 + Math.floor(i / N);
        return get(x, y).then(im => { const a = (la1 - tLat(y)) / (la1 - la0) * cv.height, b = (la1 - tLat(y + 1)) / (la1 - la0) * cv.height; g.drawImage(im, (i % N) * 256, a, 256, b - a); }); }));
      if (M.dead) return;
      const t = new THREE.CanvasTexture(cv); t.anisotropy = 8;
      tilesMesh = new THREE.Mesh(new THREE.SphereGeometry(1.0004, 64, 64, (lo0 + 180) * D2R, (lo1 - lo0) * D2R, (90 - la1) * D2R, (la1 - la0) * D2R), new THREE.MeshBasicMaterial({ map: t, transparent: true, opacity: .92, depthWrite: false }));
      tilesMesh.renderOrder = 1; sc.add(tilesMesh);
      $d('.ste-attr').innerHTML = '© <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noreferrer">OpenStreetMap</a>-Mitwirkende';
      tilesBtn.innerHTML = `${icon('x')}Karte entfernen`;
    } catch (e) { toast((e.message || 'Kacheln nicht geladen') + ' – Karte braucht Internet.', 'err'); tilesBtn.innerHTML = `${icon('globe')}Karte laden`; }
    tilesBtn.disabled = false; M.dirty = true;
  };

  // ---- Zoom: eigenes Mausrad/Zwei-Finger-Zoomen nach Höhe über dem Boden (OrbitControls zoomt nur multiplikativ zum Mittelpunkt) ----
  const zoomBy = (f) => { const d = camera.position.length(), alt = clamp((d - 1) * f, .012, 7); camera.position.multiplyScalar((1 + alt) / d); M.fly = null; controls.update(); M.dirty = true; M.anim.add('layers'); };
  canvas.addEventListener('wheel', (e) => { e.preventDefault(); zoomBy(Math.exp(clamp(e.deltaY, -300, 300) * .0018)); }, { passive: false });
  const touches = new Map(); let pinch = 0;
  canvas.addEventListener('pointerdown', (e) => { touches.set(e.pointerId, [e.clientX, e.clientY]); down = [e.clientX, e.clientY]; });
  canvas.addEventListener('pointermove', (e) => {
    if (!touches.has(e.pointerId)) return; touches.set(e.pointerId, [e.clientX, e.clientY]);
    if (touches.size === 2) { const [a, b] = [...touches.values()], d = Math.hypot(a[0] - b[0], a[1] - b[1]); if (pinch) zoomBy(pinch / d); pinch = d; }
  });
  const up = (e) => { touches.delete(e.pointerId); if (touches.size < 2) pinch = 0; };
  canvas.addEventListener('pointercancel', up);
  // Antippen: Ort / Nacht wählen
  const rc = new THREE.Raycaster(), ndc = new THREE.Vector2(); let down = null;
  canvas.addEventListener('pointerup', (e) => {
    up(e);
    if (!down || Math.hypot(e.clientX - down[0], e.clientY - down[1]) > 6) return;
    const b = canvas.getBoundingClientRect(); ndc.set((e.clientX - b.left) / b.width * 2 - 1, -(e.clientY - b.top) / b.height * 2 + 1);
    rc.setFromCamera(ndc, camera);
    const objs = picks.filter(p => layer[p.layer].group.visible && p.obj.position.clone().normalize().dot(camera.position.clone().normalize()) > .05);
    const hit = rc.intersectObjects(objs.map(p => p.obj), false)[0], p = hit && objs.find(x => x.obj === hit.object);
    if (!p) return;
    if (p.kind === 'site') siteCard(p.site); else onPick(p.night);
  });

  // ---- Zeichnen ----
  const wp = new THREE.Vector3(), camN = new THREE.Vector3();
  function draw() {
    const w = stage.clientWidth, h = stage.clientHeight, dist = camera.position.length();
    camera.near = clamp((dist - 1) * .3, 1e-4, .5); camera.updateProjectionMatrix(); camera.updateMatrixWorld();
    controls.rotateSpeed = clamp((dist - 1) * .45, .012, .55);
    atmo.value = clamp((dist - 1.4) / 1.2, 0, 1);
    camN.copy(camera.position).normalize();
    for (const L of labels) {
      const on = layer[L.layer].group.visible && layer[L.layer].k > .4 && (!L.night || L.night === S.night) && dist < (L.near ?? 99)
        && !(L.layer === 'sites' && M.curPos && layer.current.k > .4 && L.pos.distanceTo(M.curPos) < 2e-5);     // Listenort = bisheriger Ort: nur ein Schild
      let hide = !on || wp.copy(L.pos).normalize().dot(camN) < .08;
      if (!hide) { wp.copy(L.pos).project(camera); hide = Math.abs(wp.z) > 1 || Math.abs(wp.x) > 1.05 || Math.abs(wp.y) > 1.05; }
      L.el.hidden = hide;
      if (!hide) L.el.style.transform = `translate(${((wp.x + 1) / 2 * w).toFixed(1)}px, ${((1 - wp.y) / 2 * h).toFixed(1)}px)`;
    }
    for (const P of Object.values(patches)) setPatchK(P);
    bordersMat.color.set(U_E.nv.value > .5 ? '#9ff3e4' : '#c5d3ef'); bordersMat.userData.base = .38 + .3 * U_E.nv.value; bordersMat.opacity = bordersMat.userData.base * ease(layer.borders.k);
    tilesBox.hidden = dist > 1.08 && !tilesMesh;
    if (tilesMesh && dist > 1.5) clearTiles();
    $d('.ste-nvpill').hidden = U_E.nv.value < .5;
    const t0 = performance.now(); renderer.render(sc, camera); M.frameMs = performance.now() - t0; M.frames++;
  }
  function frame(now) {
    M.raf = requestAnimationFrame(frame);
    const dt = Math.min(.1, (now - (M.last || now)) / 1000); M.last = now;
    M.fly?.(now);
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
      if (!busy && !M.fly) M.anim.delete('layers');
      M.dirty = true;
    }
    if (M.dirty) { M.dirty = false; draw(); }
  }
  const resize = () => { const w = stage.clientWidth, h = stage.clientHeight; if (!w || !h) return; renderer.setSize(w, h, false); camera.aspect = w / h; camera.updateProjectionMatrix(); M.dirty = true; };
  const ro = new ResizeObserver(resize); ro.observe(stage); resize();
  for (const night of Object.keys(heats)) if (heats[night]) buildPatch(night);
  controls.update();
  M.anim.add('layers');
  M.raf = requestAnimationFrame(frame);
  return {
    setNight, layersChanged, heatChanged, home,
    state: () => {
      const f = M.flyFrames, gaps = f.slice(1).map((t, i) => t - f[i]);
      return { night: S.night, nv: +U_E.nv.value.toFixed(3), shade: +U_E.shade.value.toFixed(3), dist: +camera.position.length().toFixed(4),
        layers: Object.fromEntries(LAYERS.map(([k]) => [k, +layer[k].k.toFixed(3)])), on: { ...S.on }, patches: Object.keys(patches).length,
        heatVisible: Object.values(patches).filter(P => P.group.visible).map(P => P.night), reveal: Object.fromEntries(Object.values(patches).map(P => [P.night, +P.reveal.toFixed(3)])),
        marks: { current: layer.current.group.children.length, proposals: layer.proposals.group.children.length, sites: layer.sites.group.children.length },
        borders: M.borders || 0, runs: Object.fromEntries(Object.entries(runs).map(([k, v]) => [k, { ready: v.ready, hits: v.hits }])),
        flying: !!M.fly, animating: M.anim.size > 0, frameMs: +M.frameMs.toFixed(2), flyFrameMs: gaps.length ? +(gaps.reduce((a, b) => a + b, 0) / gaps.length).toFixed(2) : null,
        flyMaxMs: gaps.length ? +Math.max(...gaps).toFixed(2) : null, sun: vll(sunNow).map(v => +v.toFixed(2)), card: !cardEl.hidden };
    },
    focus: (lat, lon, dist) => flyTo(llv(lat, lon), dist, 0),
    dispose() { M.dead = true; cancelAnimationFrame(M.raf); ro.disconnect(); controls.dispose(); clearTiles(); for (const x of disposables) x.dispose?.(); renderer.dispose(); renderer.forceContextLoss?.(); },
  };
}

window.SkyTraceErde = { view, href: (night) => '#/skytrace/erde' + (night ? '/' + night : '') };
})();
