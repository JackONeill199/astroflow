'use strict';
/* SkyTrace 3D-Ansicht eines Laufs (eigene Ansicht: Reiter „3D“ im Lauf und #/skytrace/3d, kein Dialog mehr): Erde zur Aufnahmezeit, Teleskopweg über die Nacht (Himmelskuppel über dem Standort),
   Blickfeld mit Strahl zum Zielobjekt und alle zugeordneten Treffer (Satelliten, Meteore, Flugzeuge).
   Gerechnet wird im Server (`/api/skytrace/run3d`, run3d.py/orbit3d.py); hier wird nur gezeichnet. three.js liegt unter
   vendor/ und wird erst beim Öffnen geladen. Szene in Erdradien; Server-System D (km) → three: (x, z, −y), Nordpol = +y.
   Die Erde dreht sich mit `earth_rate`·(t − anchor), das nachgeführte Blickfeld bleibt raumfest, sein Ursprung wandert mit. */
(() => {
const R = 6378.137, BASE = '/m/skytrace/vendor/', LIB = '/vendor/';   // three.js liegt in der Shell (gemeinsam mit „Bilder → Tiefe“)
const DOME = .045, SKY = 60;                    // Himmelskuppel und Sternkugel – beide nicht maßstäblich
const PALETTE = ['#7c9cff', '#5eead4', '#f2a6ff', '#b18cff', '#fbbf24', '#f87171', '#4ade80', '#60a5fa', '#fb923c', '#e879f9', '#a3e635', '#38bdf8'];
const reduced = () => matchMedia('(prefers-reduced-motion: reduce)').matches;
const TYPES = { PAY: 'Satellit', 'R/B': 'Raketenstufe', DEB: 'Trümmerteil', Satellit: 'Satellit', Raketenstufe: 'Raketenstufe', 'Trümmerteil': 'Trümmerteil' };
const fmt = (v, d = 0) => Number.isFinite(v) ? v.toLocaleString('de-DE', { minimumFractionDigits: d, maximumFractionDigits: d }) : '–';
const utc = (t) => new Date(t * 1000).toISOString().slice(11, 19) + ' UTC';
const rel = (s) => { const a = Math.abs(Math.round(s)); return (s < -.5 ? '−' : '+') + Math.floor(a / 60) + ':' + String(a % 60).padStart(2, '0'); };
const dur = (s) => s >= 3600 ? `${Math.floor(s / 3600)} h ${Math.round(s % 3600 / 60)} min` : `${Math.round(s / 60)} min`;
const compass = (az) => ['N', 'NO', 'O', 'SO', 'S', 'SW', 'W', 'NW'][Math.round(az / 45) % 8];
const angleDiff = (a, b) => ((b - a + 540) % 360) - 180;
let lib = null;

async function load() {
  if (!lib) lib = Promise.all([import(LIB + 'three.module.min.js'), import(LIB + 'OrbitControls.js')])
    .then(([THREE, c]) => ({ THREE, OrbitControls: c.OrbitControls })).catch(e => { lib = null; throw e; });
  return lib;
}

function available(c) { return !!c?.view3d; }

// Adresse der 3D-Ansicht eines Laufs (Reiter im Lauf); den Fokus-Kandidaten übergibt skytrace.js selbst
const href = (runId) => `#/skytrace/${encodeURIComponent(runId)}/3d`;

const MARKUP = `<div class="st3d-main">
    <div class="st3d-stage"><canvas aria-label="Drehbare 3D-Erde mit Teleskopweg, Blickfeld und Bahnen"></canvas><div class="st3d-labels"></div>
      <div class="st3d-tools"><div class="seg st3d-views" hidden aria-label="Kamera"><button data-v="earth" class="on">Erde</button><button data-v="scope">Teleskop</button><button data-v="pass">Durchgang</button><button data-v="sat">Folgen</button></div>
        <span class="grow"></span><span class="st3d-schema" title="Kuppel, Himmel und Ziel sind nicht maßstäblich">Schema</span>
        <button class="btn ghost icon sm st3d-fs" title="Vollbild (F)" aria-label="Vollbild">${icon('expand')}</button></div>
      <div class="st3d-msg"><span class="spinner"></span>Teleskopweg und Bahnen werden berechnet …</div><div class="st3d-card" hidden></div>
      <div class="st3d-help">Ziehen dreht · Mausrad/zwei Finger zoomen · Antippen zeigt Details</div></div>
    <div class="st3d-bar" hidden><button class="btn icon ghost sm" data-play title="Abspielen" aria-label="Abspielen">${icon('play')}</button>
      <div class="st3d-range"><canvas class="st3d-strip" aria-hidden="true"></canvas><input type="range" aria-label="Zeit der Beobachtung"></div>
      <span class="st3d-time tnum"></span><select aria-label="Abspielgeschwindigkeit"><option value="1">1×</option><option value="10">10×</option>
        <option value="60">60×</option><option value="300" selected>300×</option><option value="1200">1200×</option></select></div></div>
  <aside class="st3d-side"><h3 class="st3d-sec">Objekte</h3><div class="st3d-legend"><p class="mut sm">Wird geladen …</p></div><p class="st3d-note mut sm"></p></aside>`;

// Zeichnet die 3D-Ansicht eines Laufs in host (Bühne, Zeitleiste, Objektliste). Gibt { dispose, fullscreen } zurück;
// host._st3d = Szene (state() für Tests) sobald gezeichnet.
function mount(host, runId, { focus = null, session = null, onCandidate = null } = {}) {
  host.classList.add('st3d');
  host.innerHTML = MARKUP;
  const $d = (s) => host.querySelector(s);
  let view = null, dead = false;
  const fullscreen = () => { if (document.fullscreenElement) document.exitFullscreen(); else host.requestFullscreen?.().catch(() => {}); };
  $d('.st3d-fs').onclick = fullscreen;
  const fail = (text, link) => {
    $d('.st3d-msg').innerHTML = `${icon('alert')}<span>${esc(text)}${link ? ` <a href="${link[1]}">${esc(link[0])}</a>` : ''}</span>`;
    $d('.st3d-msg').classList.add('hint'); $d('.st3d-legend').innerHTML = '<p class="mut sm">Keine Objekte zu zeigen.</p>';
    if (!$d('.st3d-runs')) $d('.st3d-side').classList.add('empty');
  };
  const gone = () => dead || !host.isConnected;
  (async () => {
    let data;
    const q = `run=${encodeURIComponent(runId)}` + (focus ? `&focus=${encodeURIComponent(focus)}` : '') + (session ? `&session=${encodeURIComponent(session)}` : '');
    try { data = await api('/skytrace/run3d?' + q); }
    catch (e) { if (!gone()) fail(e.message); return; }
    if (gone()) return;
    if (!data.ok) { fail(data.reason, data.need_site ? ['Standort eintragen →', '#/settings/skytrace'] : null); return; }
    let mods;
    try { mods = await load(); } catch (e) { if (!gone()) fail('3D-Bibliothek konnte nicht geladen werden: ' + e.message); return; }
    if (gone()) return;
    try { view = scene(host, data, mods, { focus, onCandidate }); host._st3d = view; }
    catch (e) { fail('3D-Darstellung nicht möglich (WebGL nicht verfügbar?): ' + e.message); return; }
    $d('.st3d-msg').hidden = true;
  })();
  return {
    fullscreen,
    dispose() { dead = true; view?.dispose(); host._st3d = null; if (document.fullscreenElement === host) document.exitFullscreen(); },
  };
}

function glowTexture(THREE) {
  const cv = document.createElement('canvas'); cv.width = cv.height = 64;
  const g = cv.getContext('2d'), r = g.createRadialGradient(32, 32, 0, 32, 32, 32);
  r.addColorStop(0, '#fff'); r.addColorStop(.25, '#ffffffd0'); r.addColorStop(.55, '#ffffff30'); r.addColorStop(1, '#fff0');
  g.fillStyle = r; g.fillRect(0, 0, 64, 64);
  const t = new THREE.CanvasTexture(cv); t.colorSpace = THREE.SRGBColorSpace; return t;
}

function scene(d, data, { THREE, OrbitControls }, { focus, onCandidate }) {
  const $d = (s) => d.querySelector(s), stage = $d('.st3d-stage'), canvas = $d('.st3d-stage canvas'), labelsHost = $d('.st3d-labels');
  const V = (p) => new THREE.Vector3(p[0] / R, p[2] / R, -p[1] / R);            // D (km) → Szene
  const U = (p) => new THREE.Vector3(p[0], p[2], -p[1]).normalize();            // Richtung
  const renderer = new THREE.WebGLRenderer({ canvas, antialias: true });
  renderer.setPixelRatio(Math.min(devicePixelRatio || 1, 2));
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  const sc = new THREE.Scene(); sc.background = new THREE.Color('#03050b');
  const camera = new THREE.PerspectiveCamera(40, 1, .001, 400);
  // OrbitControls übernimmt camera.up nur beim Anlegen → für die Teleskop-Ansicht (lokale Senkrechte oben) neu anlegen
  let controls = null;
  function makeControls(up) {
    const target = controls ? controls.target.clone() : new THREE.Vector3();
    controls?.dispose(); camera.up.copy(up);
    controls = new OrbitControls(camera, canvas); controls.target.copy(target);
    controls.enableDamping = false; controls.rotateSpeed = .6; controls.zoomSpeed = .9; controls.minDistance = .002; controls.maxDistance = 90;
    controls.addEventListener('change', () => { S.dirty = true; });
    controls.addEventListener('start', () => { S.fly = null; });
  }
  makeControls(new THREE.Vector3(0, 1, 0));
  const glow = glowTexture(THREE), disposables = [glow];
  const keep = (x) => { disposables.push(x); return x; };
  const anchor = data.anchor, [tMin, tMax] = data.window, P = data.pointing;
  const S = { t: anchor, playing: false, last: 0, raf: 0, dirty: true, sel: null, mode: 'earth', k: 0 };

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
  const label = (text, color, obj, select, sub) => { const b = document.createElement('button'); b.className = 'st3d-lbl'; b.textContent = text; b.style.setProperty('--c', color);
    if (sub) { const s = document.createElement('small'); s.textContent = sub; b.append(s); }
    b.onclick = () => select(); labelsHost.append(b); const L = { el: b, obj }; labels.push(L); return L; };

  // ---- Standort und Himmelskuppel (erdfest, lokales Horizontsystem) ----
  const obsLocal = V(data.observer).multiplyScalar(1.0004);
  const observer = sprite('#f2d385', .026, earth); observer.position.copy(obsLocal);
  const E = U(data.enu.east), N = U(data.enu.north), Up = U(data.enu.up);
  const dome = new THREE.Group(); dome.position.copy(obsLocal); earth.add(dome);
  const onDome = (az, alt, r = DOME) => { const a = az * Math.PI / 180, h = alt * Math.PI / 180;
    return N.clone().multiplyScalar(Math.cos(h) * Math.cos(a)).add(E.clone().multiplyScalar(Math.cos(h) * Math.sin(a))).add(Up.clone().multiplyScalar(Math.sin(h))).multiplyScalar(r); };
  for (const alt of [0, 30, 60]) line(Array.from({ length: 73 }, (_, i) => onDome(i * 5, alt)), '#9fb0d9', alt ? .18 : .45, dome);
  for (const az of [0, 90, 180, 270]) line(Array.from({ length: 19 }, (_, i) => onDome(az, i * 5)), az ? '#9fb0d9' : '#f87171', az ? .16 : .6, dome);
  const domeN = new THREE.Object3D(); domeN.position.copy(onDome(0, 0, DOME * 1.12)); dome.add(domeN);
  // Weg der Blickrichtung über die Nacht, an Lücken unterbrochen
  const gapAt = (a, b) => data.gaps.some(([g1, g2]) => a <= g1 && b >= g2);
  { const pathMat = keep(new THREE.MeshBasicMaterial({ color: '#f2d385' }));
    const tube = (pts) => { if (pts.length < 2) return;
      dome.add(new THREE.Mesh(keep(new THREE.TubeGeometry(new THREE.CatmullRomCurve3(pts), Math.min(400, pts.length * 2), DOME * .012, 6)), pathMat)); };
    let part = [];
    for (let i = 0; i < P.t.length; i++) {
      if (i && gapAt(P.t[i - 1], P.t[i])) { tube(part); part = []; }
      part.push(onDome(P.az[i], P.alt[i]));
    }
    tube(part); }
  const domeMark = sprite('#ffffff', .028, dome);
  const domeRay = line([new THREE.Vector3(), new THREE.Vector3(0, 0, 1)], '#f2d385', .8, dome);

  // ---- Blickfeld, Strahl zum Ziel ----
  const hits = data.hits.map((h, i) => ({ ...h, color: PALETTE[i % PALETTE.length], on: true }));
  const sats = hits.filter(h => h.kind === 'sat');
  const fov = new THREE.Group(); sc.add(fov);
  const reachKm = Math.max(800, ...sats.filter(s => s.in_fov).map(s => s.at_t0.range_km * 1.35));
  const L = Math.min(reachKm, 4000) / R;
  const facePos = new THREE.Float32BufferAttribute(new Float32Array(36), 3), edgePos = new THREE.Float32BufferAttribute(new Float32Array(48), 3);
  { const g = keep(new THREE.BufferGeometry()); g.setAttribute('position', facePos);
    fov.add(new THREE.Mesh(g, keep(new THREE.MeshBasicMaterial({ color: '#f2d385', transparent: true, opacity: .14, side: THREE.DoubleSide, depthWrite: false }))));
    const e = keep(new THREE.BufferGeometry()); e.setAttribute('position', edgePos);
    fov.add(new THREE.LineSegments(e, keep(new THREE.LineBasicMaterial({ color: '#f2d385', transparent: true, opacity: .75 })))); }
  const ray = new THREE.Line(keep(new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(), new THREE.Vector3(1, 0, 0)])),
    keep(new THREE.LineDashedMaterial({ color: '#f2d385', dashSize: .6, gapSize: .45, transparent: true, opacity: .55 })));
  sc.add(ray);
  // Himmelsäquator auf der Sternkugel als Orientierung, Ziel als Punkt am Himmel
  line(Array.from({ length: 121 }, (_, i) => new THREE.Vector3(Math.cos(i / 120 * Math.PI * 2) * SKY, 0, -Math.sin(i / 120 * Math.PI * 2) * SKY)), '#7c9cff', .22);
  const target = sprite('#f2d385', .034);
  const bore0 = U(P.center[0]); target.position.copy(bore0.clone().multiplyScalar(SKY));
  const tg = data.target || {};
  const targetSub = [tg.kind, tg.distance].filter(Boolean).join(' · ') || 'Zielrichtung';

  // ---- Treffer ----
  for (const h of hits) {
    h.group = new THREE.Group(); sc.add(h.group);
    if (h.kind === 'sat') {
      line(h.ring.map(V), h.color, h.alternative ? .18 : .3, h.group);
      line(h.track.map(V), h.color, .9, h.group);
      for (const seg of h.fov_segments) {
        const pts = seg.pos.map(V);
        if (pts.length > 1) h.group.add(new THREE.Mesh(keep(new THREE.TubeGeometry(new THREE.CatmullRomCurve3(pts), Math.max(4, pts.length), .0003, 6)), keep(new THREE.MeshBasicMaterial({ color: '#ffffff' }))));
        h.pierce = h.pierce || pts[Math.floor(pts.length / 2)];
        sprite('#ffffff', .02, h.group).position.copy(pts[Math.floor(pts.length / 2)]);
      }
      h.obj = sprite(h.color, .04);
    } else {
      const pts = h.ecef.map(V);
      line(pts, h.color, 1, earth); h.obj = sprite(h.color, .03, earth); h.obj.position.copy(pts.at(-1)); h.pts = pts;
    }
    h.name = h.name || (h.kind === 'meteor' ? 'Meteor' : 'Flugzeug');
  }
  const items = [{ kind: 'scope', name: 'Teleskop', color: '#f2d385', obj: observer }, { kind: 'target', name: tg.name || 'Ziel', color: '#ffe8a8', obj: target }, ...hits];
  for (const it of items) it.label = label(it.name, it.color, it.obj, () => select(it), it.kind === 'target' ? targetSub + ' · nicht maßstäblich' : null);
  label('N', '#f87171', domeN, () => select(items[0])).el.classList.add('st3d-lbl-n');

  // ---- Zeit ----
  const range = $d('.st3d-range input'), timeEl = $d('.st3d-time'), playBtn = $d('[data-play]'), speedSel = $d('.st3d-bar select');
  range.min = tMin; range.max = tMax; range.step = 1; range.value = anchor;
  $d('.st3d-bar').hidden = false;
  const strip = $d('.st3d-strip');
  function drawStrip() {
    const w = strip.clientWidth, h = strip.clientHeight, dpr = Math.min(devicePixelRatio || 1, 2); if (!w) return;
    strip.width = w * dpr; strip.height = h * dpr; const g = strip.getContext('2d'); g.scale(dpr, dpr); g.clearRect(0, 0, w, h);
    const x = (t) => (t - tMin) / (tMax - tMin) * w;
    g.fillStyle = '#f2d385aa'; for (const t of P.t) g.fillRect(x(t), h * .3, 1.2, h * .4);
    g.fillStyle = '#f8717188'; for (const t of data.frames.excluded_times) g.fillRect(x(t), h * .3, 1, h * .4);
    g.fillStyle = '#ffffff18'; for (const [a, b] of data.gaps) g.fillRect(x(a), 0, x(b) - x(a), h);
    for (const hh of hits) { g.fillStyle = hh.color; g.fillRect(x(hh.t_pass) - 1.5, 0, 3, h); }
  }
  const tmp = new THREE.Vector3(), obsW = new THREE.Vector3(), bore = new THREE.Vector3();
  const sampleAt = (t) => { let lo = 0, hi = P.t.length - 1; if (t <= P.t[0]) return 0; while (lo < hi) { const m = (lo + hi + 1) >> 1; if (P.t[m] <= t) lo = m; else hi = m - 1; } return lo; };
  const inPause = (t) => t < P.t[0] - 1 || t > P.t.at(-1) + data.exposure + 1 || data.gaps.some(([a, b]) => t > a + data.exposure && t < b);
  function setTime(t) {
    S.t = Math.min(Math.max(t, tMin), tMax); range.value = S.t;
    const k = S.k = sampleAt(S.t), pause = inPause(S.t);
    timeEl.innerHTML = `${utc(S.t)} <span class="mut">${pause ? 'Pause' : 'Aufnahme ' + ((P.index[k] ?? k) + 1)}</span>`;
    earth.rotation.y = data.earth_rate * (S.t - anchor);
    earth.updateMatrixWorld(true);
    obsW.copy(obsLocal); earth.localToWorld(obsW); fov.position.copy(obsW);
    bore.copy(U(P.center[k]));
    const c = P.corners[k].map(U).map(v => v.multiplyScalar(L / v.dot(bore))), f = facePos.array, e = edgePos.array;
    for (let i = 0; i < 4; i++) { const a = c[i], b = c[(i + 1) % 4]; f.set([0, 0, 0, a.x, a.y, a.z, b.x, b.y, b.z], i * 9); e.set([0, 0, 0, a.x, a.y, a.z, a.x, a.y, a.z, b.x, b.y, b.z], i * 12); }
    facePos.needsUpdate = edgePos.needsUpdate = true; fov.children.forEach(m => m.geometry.computeBoundingSphere());
    fov.visible = !pause;
    const rp = ray.geometry.attributes.position; tmp.copy(bore).multiplyScalar(L).add(obsW);
    rp.setXYZ(0, tmp.x, tmp.y, tmp.z); rp.setXYZ(1, bore.x * SKY, bore.y * SKY, bore.z * SKY); rp.needsUpdate = true; ray.computeLineDistances(); ray.geometry.computeBoundingSphere();
    target.position.copy(bore).multiplyScalar(SKY);
    domeMark.position.copy(onDome(P.az[k], P.alt[k])); domeMark.visible = !pause;
    const dr = domeRay.geometry.attributes.position; dr.setXYZ(1, domeMark.position.x, domeMark.position.y, domeMark.position.z); dr.needsUpdate = true; domeRay.geometry.computeBoundingSphere();
    for (const h of hits) {
      const inside = S.t >= h.window[0] && S.t <= h.window[1];
      if (h.kind === 'sat') { h.obj.visible = h.on && inside; if (inside) h.obj.position.copy(V(lerp(h.track, (S.t - h.window[0]) / 2))); }
      else { h.obj.visible = h.on && S.t >= h.times[0] - 60 && S.t <= h.times.at(-1) + 60;
        if (h.kind === 'plane') { const j = h.times.findIndex(x => x >= S.t); h.obj.position.copy(h.pts[j < 0 ? h.pts.length - 1 : j]); } }
    }
    if (S.mode === 'sat' || S.mode === 'scope' || S.mode === 'aim') follow();
    if (S.sel) card(S.sel);
    S.dirty = true;
  }
  const lerp = (arr, f) => { f = Math.min(Math.max(f, 0), arr.length - 1); const i = Math.floor(f), k = f - i, a = arr[i], b = arr[Math.min(i + 1, arr.length - 1)];
    return Array.isArray(a) ? a.map((v, j) => v + (b[j] - v) * k) : a + (b - a) * k; };
  range.oninput = () => { stop(); setTime(+range.value); };
  const stop = () => { S.playing = false; playBtn.innerHTML = icon('play'); playBtn.title = 'Abspielen'; };
  playBtn.onclick = () => { if (S.playing) return stop(); if (S.t >= tMax) setTime(tMin); S.playing = true; S.last = performance.now(); playBtn.innerHTML = icon('pause'); playBtn.title = 'Anhalten'; };

  // ---- Kamera ----
  const views = $d('.st3d-views'); views.hidden = false;
  if (!sats.length) { views.querySelector('[data-v=sat]').hidden = true; }
  if (!hits.length) views.querySelector('[data-v=pass]').hidden = true;
  const chosen = () => (S.sel && S.sel.kind !== 'scope' && S.sel.kind !== 'target' ? S.sel : hits.find(h => h.focus) || hits[0]);
  const followed = () => (S.sel?.kind === 'sat' ? S.sel : sats.find(h => h.focus) || sats[0]);
  let lastTarget = null;
  // Kamera wandert mit (Satellit bzw. Standort, der sich mit der Erde dreht)
  function follow() { const p = S.mode === 'scope' || S.mode === 'aim' ? obsW : followed()?.obj.position; if (!p) return;
    if (lastTarget) { const dl = tmp.copy(p).sub(lastTarget); camera.position.add(dl); controls.target.add(dl); } lastTarget = p.clone(); }
  function setView(mode, animate = true) {
    S.mode = mode; lastTarget = null;
    views.querySelectorAll('button').forEach(b => b.classList.toggle('on', b.dataset.v === mode));
    let target, pos;
    const up = obsW.clone().normalize(), wantUp = mode === 'scope' || mode === 'aim' ? up : new THREE.Vector3(0, 1, 0);
    if (camera.up.distanceTo(wantUp) > 1e-6) makeControls(wantUp);
    const wide = stage.clientWidth < 520 ? 1.6 : 1;      // Handy: etwas mehr Abstand
    if (mode === 'earth') {
      const h = chosen(), dir = up.clone().add(h?.kind === 'sat' && h.obj.visible ? h.obj.position.clone().normalize() : new THREE.Vector3()).normalize();
      target = new THREE.Vector3(); pos = dir.multiplyScalar(stage.clientWidth < 520 ? 4.4 : 3.4).add(new THREE.Vector3(0, .5, 0));
      controls.minDistance = 1.05;
    } else if (mode === 'aim') {                         // vom Standort entlang des Strahls zum Zielobjekt
      target = obsW.clone().add(bore.clone().multiplyScalar(3));
      pos = obsW.clone().sub(bore.clone().multiplyScalar(DOME * 3 * wide)).add(up.clone().multiplyScalar(DOME * 1.2 * wide));
      controls.minDistance = .002;
    } else if (mode === 'scope') {                       // hinter und über dem Standort, Blick in Zeigerichtung
      const horiz = bore.clone().projectOnPlane(up).normalize(), side = new THREE.Vector3().crossVectors(horiz, up).normalize();
      target = obsW.clone().add(up.clone().multiplyScalar(DOME * .4)).add(horiz.clone().multiplyScalar(DOME * .4));
      pos = obsW.clone().sub(horiz.multiplyScalar(DOME * 2.6 * wide)).add(up.multiplyScalar(DOME * 1.9 * wide)).add(side.multiplyScalar(DOME * .9 * wide));
      controls.minDistance = .002;
    } else {
      const h = mode === 'sat' ? followed() : chosen();
      target = (mode === 'pass' && h?.pierce) ? h.pierce.clone() : (h?.obj.getWorldPosition(new THREE.Vector3()) || obsW.clone());
      const toObs = obsW.clone().sub(target), dist = Math.max(toObs.length(), .02);
      const side = new THREE.Vector3().crossVectors(toObs, target.clone().normalize()).normalize();
      pos = target.clone().add(toObs.multiplyScalar(.45)).add(side.multiplyScalar(dist * .45)).add(target.clone().normalize().multiplyScalar(dist * .12));
      controls.minDistance = .002;
    }
    const from = { p: camera.position.clone(), t: controls.target.clone() }, start = performance.now(), ms = animate && !reduced() ? 900 : 0;
    S.fly = (now) => { const k = ms ? Math.min(1, (now - start) / ms) : 1, e = 1 - Math.pow(1 - k, 3);
      camera.position.lerpVectors(from.p, pos, e); controls.target.lerpVectors(from.t, target, e); controls.update();
      if (k >= 1) { S.fly = null; if (mode === 'sat') lastTarget = target.clone(); if (mode === 'scope' || mode === 'aim') lastTarget = obsW.clone(); } S.dirty = true; };
  }
  views.onclick = (e) => { const b = e.target.closest('[data-v]'); if (b) setView(b.dataset.v); };

  // ---- Auswahl und Infokarte ----
  const cardEl = $d('.st3d-card');
  function card(it) {
    const rows = [], k = S.k;
    let head, actions = '';
    if (it.kind === 'sat') {
      const type = TYPES[it.object_type] || it.object_type, inside = S.t >= it.window[0] && S.t <= it.window[1], f = (S.t - it.window[0]) / 2;
      head = `<b style="--c:${it.color}">${esc(it.name)}</b><small>NORAD ${esc(it.norad_id)}${type ? ' · ' + esc(type) : ''} · ${esc(it.candidate)}${it.alternative ? ' · Alternative' : ''}</small>`;
      rows.push(['Durchgang', `${utc(it.t_pass)} (${rel(S.t - it.t_pass)})`]);
      if (inside) rows.push(['Höhe', fmt(lerp(it.height_km, f)) + ' km'], ['Geschwindigkeit', fmt(lerp(it.speed_kms, f), 2) + ' km/s'], ['Entfernung', fmt(lerp(it.range_km, f)) + ' km']);
      else rows.push(['Höhe', fmt(it.at_t0.height_km) + ' km beim Durchgang']);
      rows.push(['Umlauf', fmt(it.period_min, 1) + ' min · ' + fmt(it.inclination_deg, 1) + '° Neigung'],
        ['Bahnelemente', (Number.isFinite(it.tle_age_days) ? fmt(it.tle_age_days, 1) + ' Tage alt · ' : '') + esc(it.tle_source || '')],
        ['Im Blickfeld', it.fov_segments.length ? `ja, ${fmt(it.fov_segments[0].t.at(-1) - it.fov_segments[0].t[0], 1)} s` : 'nicht innerhalb ±2 min'],
        ['Abgleich', (Number.isFinite(it.separation_arcsec) ? fmt(it.separation_arcsec) + '″ neben der Spur' : '–') + (it.time_offset_s ? ` · ${fmt(it.time_offset_s, 1)} s außerhalb` : ' · während der Belichtung')]);
    } else if (it.kind === 'meteor' || it.kind === 'plane') {
      head = `<b style="--c:${it.color}">${esc(it.name)}</b><small>${it.kind === 'meteor' ? esc(it.shower || 'GMN-Flugbahn') : 'ADS-B ' + esc(it.icao24 || '')} · ${esc(it.candidate)}</small>`;
      rows.push(['Zeit', utc(it.times[0])], ['Höhe', it.kind === 'meteor' ? `${fmt(it.height_km[0])} → ${fmt(it.height_km[1])} km` : `${fmt(it.height_km[0], 1)} km`]);
    } else if (it.kind === 'target') {
      head = `<b style="--c:${it.color}">${esc(tg.name || 'Zielobjekt')}</b><small>${esc(targetSub)}</small>`;
      rows.push(['Richtung', `RA ${fmt(P.ra, 3)}° · Dec ${fmt(P.dec, 3)}°`], ['Darstellung', 'Strahl ins Unendliche – nicht maßstäblich']);
      if (tg.distance) rows.push(['Entfernung', esc(tg.distance)]);
    } else {
      const pause = inPause(S.t);
      head = `<b style="--c:${it.color}">Teleskop</b><small>${pause ? 'Pause zwischen Aufnahmen' : 'Aufnahme ' + ((P.index[k] ?? k) + 1) + ' · ' + utc(P.t[k])}</small>`;
      rows.push(['Blickrichtung', `${fmt(P.alt[k], 1)}° hoch · Azimut ${fmt(P.az[k])}° (${compass(P.az[k])})`],
        ['Weg', `Höhe ${fmt(Math.min(...P.alt), 1)}–${fmt(Math.max(...P.alt), 1)}° · Azimut ${fmt(P.az[0])}° → ${fmt(P.az.at(-1))}°`],
        ['Bildlage', `Positionswinkel ${fmt(P.pa[k], 1)}° (${angleDiff(P.pa[0], P.pa[k]) >= 0 ? '+' : ''}${fmt(angleDiff(P.pa[0], P.pa[k]), 1)}° seit Beginn)`],
        ['Feldrotation', `Alt-Az: ${angleDiff(P.parallactic[0], P.parallactic[k]) >= 0 ? '+' : ''}${fmt(angleDiff(P.parallactic[0], P.parallactic[k]), 1)}° seit Beginn`],
        ['Bildfeld', `${fmt(P.size_deg[0], 2)}° × ${fmt(P.size_deg[1], 2)}°` + (P.source === 'wcs' ? '' : ' (geschätzt)')],
        ['Quelle', P.source === 'wcs' ? 'Plate-Solving je Aufnahme' : 'Katalogkoordinate des Ziels – ohne Plate-Solving geschätzt'],
        ['Aufnahmen', `${data.frames.accepted} genutzt · ${data.frames.excluded} ausgelassen · ${dur(P.t.at(-1) - P.t[0] + data.exposure)}` + (data.gaps.length ? ` · ${data.gaps.length} Pause${data.gaps.length > 1 ? 'n' : ''}` : '')]);
    }
    if (it.t_pass) actions += `<button class="btn ghost sm" data-a="pass">Zum Durchgang</button>`;
    if (it.key && onCandidate) actions += `<button class="btn ghost sm" data-a="cand">Kandidat ${esc(it.candidate)} öffnen</button>`;
    cardEl.innerHTML = `<div class="st3d-card-h"><div>${head}</div><button class="btn ghost icon sm" data-a="x" aria-label="Schließen">${icon('x')}</button></div>
      <dl>${rows.map(([a, b]) => `<dt>${a}</dt><dd>${b}</dd>`).join('')}</dl>${actions ? `<div class="row st3d-card-a">${actions}</div>` : ''}`;
    cardEl.onclick = (e) => { const a = e.target.closest('[data-a]')?.dataset.a;
      if (a === 'x') select(null); else if (a === 'pass') { stop(); setTime(it.t_pass); setView('pass'); } else if (a === 'cand') onCandidate(it.key); };
    cardEl.hidden = false;
  }
  function select(it) {
    S.sel = it; legend.querySelectorAll('[data-k]').forEach(b => b.classList.toggle('on', !!it && b.dataset.k === String(items.indexOf(it))));
    if (it) card(it); else cardEl.hidden = true;
    if (S.mode === 'sat' && it?.kind === 'sat') setView('sat');
    if (it?.kind === 'target') setView('aim');
  }
  const legend = $d('.st3d-legend');
  legend.innerHTML = items.map((it, i) => `<span class="st3d-chip" style="--c:${it.color}">${it.t_pass ? `<input type="checkbox" checked data-v="${i}" aria-label="${esc(it.name)} ein-/ausblenden">` : ''}<button data-k="${i}"><i></i>${esc(it.name)}${it.t_pass ? `<small>${utc(it.t_pass).slice(0, 8)}</small>` : ''}</button></span>`).join('');
  legend.onclick = (e) => { const b = e.target.closest('[data-k]'); if (!b) return; const it = items[+b.dataset.k]; select(it); if (it.t_pass) { stop(); setTime(it.t_pass); } };
  legend.onchange = (e) => { const it = items[+e.target.dataset.v]; if (!it) return; it.on = e.target.checked; it.group && (it.group.visible = it.on); it.label.el.dataset.off = it.on ? '' : '1'; setTime(S.t); };
  const notes = [];
  if (data.missing.length) notes.push(...data.missing.map(m => `${m.candidate}: ${m.name || 'NORAD ' + m.norad_id} – ${m.reason}`));
  if (P.source !== 'wcs') notes.push('Blickrichtung aus der Katalogkoordinate des Ziels geschätzt (kein Plate-Solving).');
  if (data.clock_offset_s) {
    const a = Math.abs(data.clock_offset_s), m = Math.floor(a / 60), r = Math.round(a - 60 * m);
    notes.push(`Uhr korrigiert um ${data.clock_offset_s < 0 ? '−' : '+'}${m ? m + ' min ' + r + ' s' : r + ' s'} (übernommener Uhrversatz der Nacht; Zeiten in wahrer UTC).`);
  }
  if (data.clock_pending) notes.push('Die Satellitentreffer stammen aus einem Abgleich mit anderer Uhrkorrektur – „Satelliten neu abgleichen“, damit sie zur korrigierten Uhr passen.');
  notes.push(hits.length ? `${hits.length} zugeordnete${hits.length === 1 ? 's Objekt' : ' Objekte'}; Bahnen per SGP4, Unsicherheit wenige km.` : 'Keine Katalogtreffer in diesem Lauf – gezeigt wird der Weg des Teleskops.');
  notes.push('Kuppel und Himmel nicht maßstäblich. Erde: NASA Blue Marble.');
  $d('.st3d-note').textContent = notes.join(' ');
  const rc = new THREE.Raycaster(), ndc = new THREE.Vector2();
  let down = null;
  canvas.addEventListener('pointerdown', (e) => { down = [e.clientX, e.clientY]; });
  canvas.addEventListener('pointerup', (e) => {
    if (!down || Math.hypot(e.clientX - down[0], e.clientY - down[1]) > 6) return;
    const b = canvas.getBoundingClientRect(); ndc.set((e.clientX - b.left) / b.width * 2 - 1, -(e.clientY - b.top) / b.height * 2 + 1);
    rc.setFromCamera(ndc, camera);
    const hit = rc.intersectObjects(items.map(x => x.obj).filter(o => o.visible), false)[0];
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
    for (const Lb of labels) {
      Lb.obj.getWorldPosition(wp); const hidden = !Lb.obj.visible || Lb.el.dataset.off === '1' || behind(wp); wp.project(camera);
      Lb.el.hidden = hidden || Math.abs(wp.z) > 1;
      if (!Lb.el.hidden) Lb.el.style.transform = `translate(${(wp.x + 1) / 2 * w}px, ${(1 - wp.y) / 2 * h}px)`;
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
  const resize = () => { const w = stage.clientWidth, h = stage.clientHeight; if (!w || !h) return; renderer.setSize(w, h, false); camera.aspect = w / h; camera.updateProjectionMatrix(); drawStrip(); S.dirty = true; };
  const ro = new ResizeObserver(resize); ro.observe(stage);
  resize();
  const start = hits.find(h => h.focus);
  if (start) { speedSel.value = '10'; setTime(start.t_pass); } else setTime(P.t[0]);
  camera.position.set(0, 1.5, 6); controls.update();
  if (start) { select(start); setView('pass', true); } else setView('earth', true);
  S.raf = requestAnimationFrame(frame);
  return {
    state: () => ({ t: S.t, mode: S.mode, hits: hits.length, sample: S.k, visible: hits.filter(h => h.obj.visible).length,
      // Prüfwerte: Höhe der Blickrichtung über dem lokalen Horizont, Kamera hinter dem Standort (< 0)
      alt: Math.asin(bore.dot(obsW.clone().normalize())) * 180 / Math.PI, camAlong: camera.position.clone().sub(obsW).dot(bore) }),
    dispose() { cancelAnimationFrame(S.raf); ro.disconnect(); controls?.dispose(); for (const x of disposables) x.dispose?.(); renderer.dispose(); renderer.forceContextLoss?.(); },
  };
}

window.SkyTrace3D = { available, mount, href };
})();
