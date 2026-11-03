'use strict';
/* SkyTrace 3D-Ansicht: Erde zur Aufnahmezeit, Teleskop-Blickfeld und zugeordnete Bahnen (Satelliten, Meteore, Flugzeuge).
   Die Bahnen rechnet der Server (`/api/skytrace/orbit3d`, orbit3d.py); hier wird nur gezeichnet. three.js liegt unter vendor/
   und wird erst beim Öffnen geladen. Szene in Erdradien; Server-System D (km) → three: (x, z, −y), Nordpol = +y.
   Die Erde dreht sich mit `earth_rate`·(t − t0), das nachgeführte Blickfeld bleibt raumfest, sein Ursprung wandert mit. */
(() => {
const R = 6378.137, BASE = '/m/skytrace/vendor/';
const reduced = () => matchMedia('(prefers-reduced-motion: reduce)').matches;
const TYPES = { PAY: 'Satellit', 'R/B': 'Raketenstufe', DEB: 'Trümmerteil', Satellit: 'Satellit', Raketenstufe: 'Raketenstufe', 'Trümmerteil': 'Trümmerteil' };
const fmt = (v, d = 0) => Number.isFinite(v) ? v.toLocaleString('de-DE', { minimumFractionDigits: d, maximumFractionDigits: d }) : '–';
const utc = (t) => new Date(t * 1000).toISOString().slice(11, 19) + ' UTC';
const rel = (s) => { const a = Math.abs(Math.round(s)); return (s < -.5 ? '−' : '+') + Math.floor(a / 60) + ':' + String(a % 60).padStart(2, '0'); };
const compass = (az) => ['N', 'NO', 'O', 'SO', 'S', 'SW', 'W', 'NW'][Math.round(az / 45) % 8];
let lib = null;

async function load() {
  if (!lib) lib = Promise.all([import(BASE + 'three.module.min.js'), import(BASE + 'OrbitControls.js')])
    .then(([THREE, c]) => ({ THREE, OrbitControls: c.OrbitControls })).catch(e => { lib = null; throw e; });
  return lib;
}

function available(c) { return !!c?.view3d; }

async function open(runId, c) {
  const d = document.createElement('dialog');
  d.className = 'st3d';
  d.innerHTML = `<header class="st3d-head"><div style="min-width:0"><h2>${icon('globe')}<span>3D-Ansicht</span></h2>
      <div class="mut sm st3d-sub">${esc(c.id)} · Bahn und Teleskop-Blickfeld zur Aufnahmezeit</div></div><span class="grow"></span>
      <div class="seg st3d-views" hidden><button data-v="earth" class="on">Erde</button><button data-v="fov">Blickfeld</button><button data-v="sat">Folgen</button></div>
      <button class="btn ghost icon" data-x title="Schließen (Esc)" aria-label="Schließen">${icon('x')}</button></header>
    <div class="st3d-stage"><canvas aria-label="Drehbare 3D-Erde mit Bahnen und Blickfeld"></canvas><div class="st3d-labels"></div>
      <div class="st3d-msg"><span class="spinner"></span>Bahnen werden berechnet …</div><div class="st3d-card" hidden></div>
      <div class="st3d-help mut">Ziehen dreht · Mausrad/zwei Finger zoomen · Antippen zeigt Details</div></div>
    <div class="st3d-bar" hidden><button class="btn icon ghost sm" data-play title="Abspielen" aria-label="Abspielen">${icon('play')}</button>
      <div class="st3d-range"><i class="st3d-exp"></i><input type="range" aria-label="Zeit um die Aufnahme"></div>
      <span class="st3d-time tnum"></span><select aria-label="Abspielgeschwindigkeit"><option value="1">1×</option><option value="10" selected>10×</option><option value="60">60×</option></select></div>
    <div class="st3d-foot"><div class="st3d-legend"></div><p class="st3d-note mut sm"></p></div>`;
  document.body.append(d);
  const $d = (s) => d.querySelector(s);
  let view = null;
  const close = () => { view?.dispose(); d.close(); d.remove(); };
  $d('[data-x]').onclick = close;
  d.addEventListener('cancel', (e) => { e.preventDefault(); close(); });
  d.showModal();
  const fail = (text) => { $d('.st3d-msg').innerHTML = `${icon('alert')}<span>${esc(text)}</span>`; $d('.st3d-msg').classList.add('hint'); };
  let data;
  try { data = await api(`/skytrace/orbit3d?run=${encodeURIComponent(runId)}&key=${encodeURIComponent(c.key)}`); }
  catch (e) { fail(e.message); return; }
  if (!d.isConnected) return;
  if (!data.ok) { fail(data.reason); return; }
  let mods;
  try { mods = await load(); } catch (e) { fail('3D-Bibliothek konnte nicht geladen werden: ' + e.message); return; }
  if (!d.isConnected) return;
  try { view = scene(d, data, mods); d._st3d = view; }
  catch (e) { fail('3D-Darstellung nicht möglich (WebGL nicht verfügbar?): ' + e.message); return; }
  $d('.st3d-msg').hidden = true;
}

function glowTexture(THREE) {
  const cv = document.createElement('canvas'); cv.width = cv.height = 64;
  const g = cv.getContext('2d'), r = g.createRadialGradient(32, 32, 0, 32, 32, 32);
  r.addColorStop(0, '#fff'); r.addColorStop(.25, '#ffffffd0'); r.addColorStop(.55, '#ffffff30'); r.addColorStop(1, '#fff0');
  g.fillStyle = r; g.fillRect(0, 0, 64, 64);
  const t = new THREE.CanvasTexture(cv); t.colorSpace = THREE.SRGBColorSpace; return t;
}

function scene(d, data, { THREE, OrbitControls }) {
  const $d = (s) => d.querySelector(s), stage = $d('.st3d-stage'), canvas = $d('canvas'), labelsHost = $d('.st3d-labels');
  const V = (p) => new THREE.Vector3(p[0] / R, p[2] / R, -p[1] / R);            // D (km) → Szene
  const U = (p) => new THREE.Vector3(p[0], p[2], -p[1]).normalize();            // Richtung
  const renderer = new THREE.WebGLRenderer({ canvas, antialias: true });
  renderer.setPixelRatio(Math.min(devicePixelRatio || 1, 2));
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  const sc = new THREE.Scene(); sc.background = new THREE.Color('#03050b');
  const camera = new THREE.PerspectiveCamera(40, 1, .001, 400);
  const controls = new OrbitControls(camera, canvas);
  controls.enableDamping = false; controls.rotateSpeed = .6; controls.zoomSpeed = .9; controls.minDistance = .002; controls.maxDistance = 60;
  const glow = glowTexture(THREE), disposables = [glow];
  const keep = (x) => { disposables.push(x); return x; };
  const t0 = data.t0, [tMin, tMax] = data.window;
  const S = { t: t0, playing: false, last: 0, raf: 0, dirty: true, sel: null, mode: 'earth' };

  // ---- Sterne, Licht, Erde ----
  { const n = 1600, pos = new Float32Array(n * 3); let s = 7;
    const rnd = () => (s = (s * 16807) % 2147483647) / 2147483647;
    for (let i = 0; i < n; i++) { const z = rnd() * 2 - 1, a = rnd() * Math.PI * 2, r = Math.sqrt(1 - z * z) * 150; pos.set([r * Math.cos(a), z * 150, r * Math.sin(a)], i * 3); }
    const g = keep(new THREE.BufferGeometry()); g.setAttribute('position', new THREE.BufferAttribute(pos, 3));
    sc.add(new THREE.Points(g, keep(new THREE.PointsMaterial({ color: '#cdd6ff', size: 1.3, sizeAttenuation: false, transparent: true, opacity: .75 })))); }
  const sun = U(data.sun);
  const light = new THREE.DirectionalLight('#fff6e8', 2.6); light.position.copy(sun).multiplyScalar(20); sc.add(light);
  sc.add(new THREE.AmbientLight('#4a5a8a', .12));
  const earth = new THREE.Group(); sc.add(earth);
  const tex = new THREE.TextureLoader().load(BASE + 'earth-2048.jpg', () => { S.dirty = true; });
  tex.colorSpace = THREE.SRGBColorSpace; tex.anisotropy = 4; keep(tex);
  // WGS84-Ellipsoid (Polradius b/a): ECEF-Positionen von Standort, Flugzeugen, Meteoren liegen sonst in hohen Breiten unter der Kugel
  const globe = new THREE.Mesh(keep(new THREE.SphereGeometry(1, 96, 64)), keep(new THREE.MeshPhongMaterial({ map: tex, color: '#ffffff', specular: '#223044', shininess: 8 })));
  globe.scale.set(1, 6356.752 / R, 1); earth.add(globe);
  // Atmosphärensaum (additiv, Rückseite)
  const atmo = { value: 1 };                          // aus der Nähe ausblenden (Saum wird sonst grell)
  sc.add(new THREE.Mesh(keep(new THREE.SphereGeometry(1.035, 64, 48)), keep(new THREE.ShaderMaterial({
    side: THREE.BackSide, blending: THREE.AdditiveBlending, transparent: true, depthWrite: false, uniforms: { k: atmo },
    vertexShader: 'varying vec3 n; void main(){ n = normalize(normalMatrix * normal); gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }',
    fragmentShader: 'uniform float k; varying vec3 n; void main(){ float i = pow(max(0.0, 0.72 - dot(n, vec3(0.0, 0.0, 1.0))), 3.0); gl_FragColor = vec4(0.42, 0.6, 1.0, 1.0) * i * 1.6 * k; }' }))));

  const sprite = (color, size, parent = sc) => { const m = keep(new THREE.SpriteMaterial({ map: glow, color, sizeAttenuation: false, depthWrite: false, transparent: true }));
    const s = new THREE.Sprite(m); s.scale.setScalar(size); parent.add(s); return s; };
  const line = (pts, color, opacity = 1, parent = sc) => { const g = keep(new THREE.BufferGeometry().setFromPoints(pts));
    const l = new THREE.Line(g, keep(new THREE.LineBasicMaterial({ color, transparent: opacity < 1, opacity }))); parent.add(l); return l; };
  const labels = [];
  const label = (text, color, obj, select) => { const b = document.createElement('button'); b.className = 'st3d-lbl'; b.textContent = text; b.style.setProperty('--c', color);
    b.onclick = () => select(); labelsHost.append(b); const L = { el: b, obj }; labels.push(L); return L; };

  // ---- Standort + Blickfeld ----
  const obsLocal = V(data.observer).multiplyScalar(1.0004);
  const observer = sprite('#f2d385', .028, earth); observer.position.copy(obsLocal);
  const items = [];
  const scope = { kind: 'scope', name: 'Teleskop', color: '#f2d385', obj: observer };
  items.push(scope);
  const sats = data.satellites.map((s) => ({ ...s, kind: 'sat' }));
  // Kegellänge: bis hinter den Satelliten, der das Blickfeld durchquert (sonst den besten Treffer)
  const primary = sats.find(s => s.in_fov) || sats[0];
  const reach = Math.max(600, ...(primary ? [primary.at_t0.range_km * 1.35] : []), ...(data.meteors.length ? [400] : []), ...(data.aircraft.length && !sats.length ? [80] : []));
  const fov = new THREE.Group(); sc.add(fov);
  const corners = data.fov.corners.map(U), bore = U(data.fov.boresight), L = reach / R;
  { const far = corners.map(c => c.clone().multiplyScalar(L / c.dot(bore)));
    const pos = [], edges = [];
    for (let i = 0; i < 4; i++) { const a = far[i], b = far[(i + 1) % 4]; pos.push(0, 0, 0, a.x, a.y, a.z, b.x, b.y, b.z); edges.push(0, 0, 0, a.x, a.y, a.z, a.x, a.y, a.z, b.x, b.y, b.z); }
    const g = keep(new THREE.BufferGeometry()); g.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3));
    fov.add(new THREE.Mesh(g, keep(new THREE.MeshBasicMaterial({ color: '#f2d385', transparent: true, opacity: .14, side: THREE.DoubleSide, depthWrite: false }))));
    const e = keep(new THREE.BufferGeometry()); e.setAttribute('position', new THREE.Float32BufferAttribute(edges, 3));
    fov.add(new THREE.LineSegments(e, keep(new THREE.LineBasicMaterial({ color: '#f2d385', transparent: true, opacity: .75 }))));
    line([new THREE.Vector3(), bore.clone().multiplyScalar(L * 1.08)], '#f2d385', .35, fov); }
  if (data.trail?.length > 1) {                     // gemessene Spur, auf Satellitenentfernung abgetragen
    const dist = (primary?.at_t0.range_km || reach / 1.35) / R;
    line(data.trail.map(p => U(p).multiplyScalar(dist)), '#ffffff', .9, fov);
  }

  // ---- Satelliten ----
  const idx = (t) => Math.min(Math.max((t - tMin) / data.step, 0), sats[0] ? sats[0].track.length - 1 : 0);
  const lerp = (arr, f) => { const i = Math.floor(f), k = f - i, a = arr[i], b = arr[Math.min(i + 1, arr.length - 1)];
    return Array.isArray(a) ? a.map((v, j) => v + (b[j] - v) * k) : a + (b - a) * k; };
  for (const s of sats) {
    line(s.ring.map(V), s.color, .38);
    line(s.track.map(V), s.color, .9);
    for (const seg of s.fov_segments) {
      const pts = seg.pos.map(V);
      if (pts.length > 1) { const tube = keep(new THREE.TubeGeometry(new THREE.CatmullRomCurve3(pts), Math.max(4, pts.length), .0003, 6));
        sc.add(new THREE.Mesh(tube, keep(new THREE.MeshBasicMaterial({ color: '#ffffff' })))); }
      const mark = sprite('#ffffff', .022); mark.position.copy(pts[Math.floor(pts.length / 2)]);
      s.pierce = s.pierce || pts[Math.floor(pts.length / 2)];
    }
    s.obj = sprite(s.color, .042);
    s.sight = line([new THREE.Vector3(), new THREE.Vector3(1, 0, 0)], s.color, .45);
    items.push(s);
  }
  // ---- Meteore und Flugzeuge (erdfest) ----
  for (const m of data.meteors) {
    const a = V(m.ecef[0]), b = V(m.ecef[1]);
    line([a, b], '#ff9f6e', 1, earth); const o = sprite('#ff9f6e', .03, earth); o.position.copy(b);
    items.push({ ...m, kind: 'meteor', color: '#ff9f6e', obj: o, name: m.name || 'Meteor' });
  }
  for (const a of data.aircraft) {
    const pts = a.ecef.map(V); line(pts, '#5eead4', 1, earth); const o = sprite('#5eead4', .026, earth); o.position.copy(pts[0]);
    items.push({ ...a, kind: 'plane', color: '#5eead4', obj: o, pts, name: a.name || 'Flugzeug' });
  }
  for (const it of items) it.label = label(it.name, it.color, it.obj, () => select(it));

  // ---- Zeit ----
  const range = $d('.st3d-range input'), timeEl = $d('.st3d-time'), playBtn = $d('[data-play]'), speedSel = $d('.st3d-bar select');
  range.min = tMin; range.max = tMax; range.step = .5; range.value = t0;
  { const ex = data.exposure, span = tMax - tMin, i = $d('.st3d-exp');
    if (ex) { i.style.left = ((ex.start - tMin) / span * 100) + '%'; i.style.width = Math.max(.6, ex.seconds / span * 100) + '%'; i.title = 'Belichtung'; } }
  $d('.st3d-bar').hidden = false;
  const tmp = new THREE.Vector3(), obsW = new THREE.Vector3();
  function setTime(t) {
    S.t = Math.min(Math.max(t, tMin), tMax); range.value = S.t;
    timeEl.innerHTML = `${utc(S.t)} <span class="mut">${rel(S.t - t0)}</span>`;
    earth.rotation.y = data.earth_rate * (S.t - t0);
    earth.updateMatrixWorld(true);
    obsW.copy(obsLocal); earth.localToWorld(obsW); fov.position.copy(obsW);
    const f = idx(S.t);
    for (const s of sats) {
      s.obj.position.copy(V(lerp(s.track, f)));
      const p = s.sight.geometry.attributes.position; p.setXYZ(0, obsW.x, obsW.y, obsW.z); p.setXYZ(1, s.obj.position.x, s.obj.position.y, s.obj.position.z); p.needsUpdate = true;
      s.sight.geometry.computeBoundingSphere();
    }
    for (const a of items.filter(x => x.kind === 'plane')) {
      const k = a.times.findIndex(x => x >= S.t); const j = k < 0 ? a.pts.length - 1 : Math.max(0, k);
      a.obj.position.copy(a.pts[j]); a.obj.visible = S.t >= a.times[0] - 30 && S.t <= a.times.at(-1) + 30;
    }
    if (S.mode === 'sat' && primary) follow();
    if (S.sel) card(S.sel);
    S.dirty = true;
  }
  range.oninput = () => { stop(); setTime(+range.value); };
  const stop = () => { S.playing = false; playBtn.innerHTML = icon('play'); playBtn.title = 'Abspielen'; };
  playBtn.onclick = () => { if (S.playing) return stop(); if (S.t >= tMax) setTime(tMin); S.playing = true; S.last = performance.now(); playBtn.innerHTML = icon('pause'); playBtn.title = 'Anhalten'; };

  // ---- Kamera ----
  const views = $d('.st3d-views'); views.hidden = false;
  if (!sats.length) views.querySelector('[data-v=sat]').hidden = true;
  const focus = () => sats.find(s => s.pierce)?.pierce || sats[0]?.obj.position.clone() || obsW.clone().multiplyScalar(1.02);
  const followed = () => (S.sel?.kind === 'sat' ? S.sel : primary);
  let lastTarget = null;
  function follow() { const p = followed().obj.position; if (lastTarget) { camera.position.add(tmp.copy(p).sub(lastTarget)); } controls.target.copy(p); lastTarget = p.clone(); }
  function setView(mode, animate = true) {
    S.mode = mode; lastTarget = null;
    views.querySelectorAll('button').forEach(b => b.classList.toggle('on', b.dataset.v === mode));
    let target, pos;
    if (mode === 'earth') {
      const dir = obsW.clone().normalize().add(primary ? primary.obj.position.clone().normalize() : new THREE.Vector3()).normalize();
      target = new THREE.Vector3(); pos = dir.multiplyScalar(stage.clientWidth < 520 ? 4.4 : 3.4).add(new THREE.Vector3(0, .5, 0));
      controls.minDistance = 1.05;
    } else {
      target = mode === 'sat' ? followed().obj.position.clone() : focus();
      const toObs = obsW.clone().sub(target), dist = toObs.length(), up = target.clone().normalize();
      const side = new THREE.Vector3().crossVectors(toObs, up).normalize();
      pos = target.clone().add(toObs.multiplyScalar(.45)).add(side.multiplyScalar(dist * .45)).add(up.multiplyScalar(dist * .12));
      controls.minDistance = .002;
    }
    const from = { p: camera.position.clone(), t: controls.target.clone() }, start = performance.now(), dur = animate && !reduced() ? 900 : 0;
    S.fly = (now) => { const k = dur ? Math.min(1, (now - start) / dur) : 1, e = 1 - Math.pow(1 - k, 3);
      camera.position.lerpVectors(from.p, pos, e); controls.target.lerpVectors(from.t, target, e); controls.update();
      if (k >= 1) { S.fly = null; if (mode === 'sat') lastTarget = target.clone(); } S.dirty = true; };
  }
  views.onclick = (e) => { const b = e.target.closest('[data-v]'); if (b) setView(b.dataset.v); };
  controls.addEventListener('change', () => { S.dirty = true; });
  controls.addEventListener('start', () => { S.fly = null; });

  // ---- Auswahl und Infokarte ----
  const cardEl = $d('.st3d-card');
  function card(it) {
    const f = idx(S.t), rows = [];
    let head;
    if (it.kind === 'sat') {
      const type = TYPES[it.object_type] || it.object_type;
      head = `<b style="--c:${it.color}">${esc(it.name)}</b><small>NORAD ${esc(it.norad_id)}${type ? ' · ' + esc(type) : ''}</small>`;
      rows.push(['Höhe', fmt(lerp(it.height_km, f)) + ' km'], ['Geschwindigkeit', fmt(lerp(it.speed_kms, f), 2) + ' km/s'], ['Entfernung', fmt(lerp(it.range_km, f)) + ' km'],
        ['Umlauf', fmt(it.period_min, 1) + ' min · ' + fmt(it.inclination_deg, 1) + '° Neigung'],
        ['Bahnelemente', (Number.isFinite(it.tle_age_days) ? fmt(it.tle_age_days, 1) + ' Tage alt · ' : '') + esc(it.tle_source || '')],
        ['Im Blickfeld', it.fov_segments.length ? `ja, ${fmt(it.fov_segments[0].t.at(-1) - it.fov_segments[0].t[0], 1)} s (${rel(it.fov_segments[0].t[0])} bis ${rel(it.fov_segments[0].t.at(-1))})` : 'nicht innerhalb ±2 min'],
        ['Abgleich', (Number.isFinite(it.separation_arcsec) ? fmt(it.separation_arcsec) + '″ neben der Spur' : '–') + (it.time_offset_s ? ` · ${fmt(it.time_offset_s, 1)} s außerhalb der Belichtung` : ' · während der Belichtung')]);
    } else if (it.kind === 'scope') {
      head = `<b style="--c:${it.color}">Teleskop-Blickfeld</b><small>Bildmitte RA ${fmt(data.fov.ra, 3)}° · Dec ${fmt(data.fov.dec, 3)}°</small>`;
      const up = obsW.clone().normalize(), north = new THREE.Vector3(0, 1, 0).projectOnPlane(up).normalize(), east = new THREE.Vector3().crossVectors(north, up);
      const alt = Math.asin(bore.dot(up)) * 180 / Math.PI, az = (Math.atan2(bore.dot(east), bore.dot(north)) * 180 / Math.PI + 360) % 360;
      rows.push(['Blickrichtung', `${fmt(alt, 1)}° hoch · Azimut ${fmt(az)}° (${compass(az)})`],
        ['Bildfeld', data.fov.size_deg ? `${fmt(data.fov.size_deg[0], 2)}° × ${fmt(data.fov.size_deg[1], 2)}°` : 'geschätzt (ohne Plate-Solving)'],
        ['Kegel', `bis ${fmt(reach)} km gezeichnet, nachgeführt`]);
    } else if (it.kind === 'meteor') {
      head = `<b style="--c:${it.color}">${esc(it.name)}</b><small>${esc(it.shower || 'GMN-Flugbahn')}</small>`;
      rows.push(['Höhe', `${fmt(it.height_km[0])} → ${fmt(it.height_km[1])} km`], ['Zeit', utc(it.times[0])]);
    } else {
      head = `<b style="--c:${it.color}">${esc(it.name)}</b><small>ADS-B ${esc(it.icao24 || '')}</small>`;
      rows.push(['Höhe', `${fmt(it.height_km[0], 1)} km`], ['Spur', `${utc(it.times[0])} – ${utc(it.times.at(-1))}`]);
    }
    cardEl.innerHTML = `<div class="st3d-card-h"><div>${head}</div><button class="btn ghost icon sm" aria-label="Schließen">${icon('x')}</button></div>
      <dl>${rows.map(([k, v]) => `<dt>${k}</dt><dd>${v}</dd>`).join('')}</dl>`;
    cardEl.querySelector('button').onclick = () => select(null);
    cardEl.hidden = false;
  }
  function select(it) {
    S.sel = it; legend.querySelectorAll('button').forEach(b => b.classList.toggle('on', it && b.dataset.k === String(items.indexOf(it))));
    if (it) card(it); else cardEl.hidden = true;
    if (S.mode === 'sat' && it?.kind === 'sat') setView('sat');       // „Folgen“ wechselt zum gewählten Satelliten
  }
  const legend = $d('.st3d-legend');
  legend.innerHTML = items.map((it, i) => `<button data-k="${i}" style="--c:${it.color}"><i></i>${esc(it.name)}</button>`).join('');
  legend.onclick = (e) => { const b = e.target.closest('[data-k]'); if (b) select(items[+b.dataset.k]); };
  const notes = [];
  if (data.missing.length) notes.push(...data.missing.map(m => `${m.name || 'NORAD ' + m.norad_id}: ${m.reason}`));
  if (data.fov.source !== 'wcs') notes.push('Blickfeld aus der Spur geschätzt (kein Plate-Solving).');
  notes.push('Bahn per SGP4 aus historischen Bahnelementen; Unsicherheit wenige km. Erde: NASA Blue Marble.');
  $d('.st3d-note').textContent = notes.join(' ');
  const ray = new THREE.Raycaster(), ndc = new THREE.Vector2();
  let down = null;
  canvas.addEventListener('pointerdown', (e) => { down = [e.clientX, e.clientY]; });
  canvas.addEventListener('pointerup', (e) => {
    if (!down || Math.hypot(e.clientX - down[0], e.clientY - down[1]) > 6) return;
    const b = canvas.getBoundingClientRect(); ndc.set((e.clientX - b.left) / b.width * 2 - 1, -(e.clientY - b.top) / b.height * 2 + 1);
    ray.setFromCamera(ndc, camera);
    const hit = ray.intersectObjects(items.map(x => x.obj), false)[0];
    select(hit ? items.find(x => x.obj === hit.object) : null);
  });

  // ---- Zeichnen (nur bei Änderung) ----
  const behind = (p) => { const o = camera.position, dir = tmp.copy(p).sub(o), len = dir.length(); dir.divideScalar(len);
    const b = o.dot(dir), c = o.lengthSq() - .995 * .995, disc = b * b - c; if (disc < 0) return false; const t = -b - Math.sqrt(disc); return t > 0 && t < len; };
  const wp = new THREE.Vector3();
  function draw() {
    const w = stage.clientWidth, h = stage.clientHeight;
    const dist = camera.position.distanceTo(controls.target);
    const ground = Math.max(camera.position.length() - 1, 1e-6);            // Nahebene an Abstand anpassen (Nahansicht ↔ Globus)
    camera.near = Math.min(Math.max(Math.min(dist, ground) * .3, 1e-5), .5); camera.far = 400;
    camera.updateProjectionMatrix(); camera.updateMatrixWorld();
    for (const L of labels) {
      L.obj.getWorldPosition(wp); const hidden = !L.obj.visible || behind(wp); wp.project(camera);
      L.el.hidden = hidden || Math.abs(wp.z) > 1;
      if (!L.el.hidden) L.el.style.transform = `translate(${(wp.x + 1) / 2 * w}px, ${(1 - wp.y) / 2 * h}px)`;
    }
    atmo.value = Math.min(1, Math.max(0, (camera.position.length() - 1.6) / 1.2));
    renderer.render(sc, camera);
  }
  function frame(now) {
    S.raf = requestAnimationFrame(frame);
    if (S.playing) { const dt = Math.min(.1, (now - S.last) / 1000); S.last = now; const t = S.t + dt * +speedSel.value;
      if (t >= tMax) { setTime(tMax); stop(); } else setTime(t); }
    S.fly?.(now);
    if (S.dirty) { S.dirty = false; draw(); }
  }
  const resize = () => { const w = stage.clientWidth, h = stage.clientHeight; if (!w || !h) return; renderer.setSize(w, h, false); camera.aspect = w / h; camera.updateProjectionMatrix(); S.dirty = true; };
  const ro = new ResizeObserver(resize); ro.observe(stage);
  resize();
  setTime(t0);
  camera.position.set(0, 1.5, 6); controls.update();
  setView('earth', true);
  S.raf = requestAnimationFrame(frame);
  return {
    state: () => ({ t: S.t, mode: S.mode, sats: sats.length }),
    dispose() { cancelAnimationFrame(S.raf); ro.disconnect(); controls.dispose(); for (const x of disposables) x.dispose?.(); renderer.dispose(); renderer.forceContextLoss?.(); },
  };
}

window.SkyTrace3D = { available, open };
})();
