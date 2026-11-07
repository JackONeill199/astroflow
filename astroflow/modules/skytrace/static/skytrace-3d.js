'use strict';
/* SkyTrace Lauf-Ebene der 3D-Erde (seit 0.35.0 keine eigene Szene mehr): Teleskopweg über die Nacht (Himmelskuppel über dem Standort),
   Blickfeld mit Strahl zum Zielobjekt und alle zugeordneten Treffer (Satelliten, Meteore, Flugzeuge) eines Laufs – gezeichnet auf dem
   gemeinsamen Globus aus skytrace-erde.js (`G`). Gerechnet wird im Server (`/api/skytrace/run3d`, run3d.py/orbit3d.py).
   Server-System D (km) → three: (x, z, −y), Nordpol = +y. D = erdfest (ECEF) zur Ankerzeit, danach raumfest:
   die Erdgruppe `G.earth` dreht sich mit `earth_rate`·(t − anchor), das nachgeführte Blickfeld bleibt raumfest, sein Ursprung wandert mit.
   interactive: Zeitleiste, Kamera-Ansichten Erde/Teleskop/Durchgang/Folgen, Objektliste, Infokarte (Kontext „Analysen“, Lauf-Reiter „3D“).
   statisch: Schnappschuss zur Ankerzeit ohne Erddrehung (Ebene „Satellitenbahnen“ in „Nächte & Standorte“) – bei Erddrehung 0 ist D erdfest. */
(() => {
const R = 6378.137;
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

function available(c) { return !!c?.view3d; }

/* Satellit angeklickt (0.45.0): Details erst jetzt laden (Handy: run3d bleibt schlank), je Satellit einmal je Seite.
   Nur Gespeichertes – fehlt ein Wert, fehlt die Zeile. */
const DETAILS = new Map();
function loadDetails(run, key, norad) {
  const id = run + '|' + key + '|' + norad;
  if (!DETAILS.has(id)) DETAILS.set(id, api(`/skytrace/satellite?run=${encodeURIComponent(run)}&key=${encodeURIComponent(key)}&norad=${encodeURIComponent(norad)}`)
    .catch((e) => { DETAILS.delete(id); throw e; }));
  return DETAILS.get(id);
}
const LEVEL = { eindeutig: ['Eindeutig', 'ok'], wahrscheinlich: ['Wahrscheinlich', 'acc'], mehrdeutig: ['Mehrdeutig', 'warn'], unsicher: ['Unsicher', 'warn'],
  alternative: ['Alternative', ''], keine: ['Kein Treffer', 'bad'] };
const deDate = (s) => /^\d{4}-\d\d-\d\d/.test(s || '') ? new Date(s.slice(0, 10) + 'T12:00:00Z').toLocaleDateString('de-DE', { day: '2-digit', month: '2-digit', year: 'numeric', timeZone: 'UTC' }) : s;
function cropHtml(c, it) {
  if (!c) return '';
  const [[x1, y1], [x2, y2]] = c.line, L = Math.hypot(x2 - x1, y2 - y1) || 1, nx = -(y2 - y1) / L * 9, ny = (x2 - x1) / L * 9;
  const guide = (s) => `<line x1="${(x1 + s * nx).toFixed(1)}" y1="${(y1 + s * ny).toFixed(1)}" x2="${(x2 + s * nx).toFixed(1)}" y2="${(y2 + s * ny).toFixed(1)}"/>`;
  return `<figure class="st3d-crop"><button type="button" data-a="crop" title="Aufnahme im SkyTrace-Lauf öffnen (Kandidat ${esc(it.candidate)})" style="aspect-ratio:${c.width} / ${c.height}">
      <img src="${esc(c.url)}" width="${c.width}" height="${c.height}" alt="Ausschnitt der eigenen Aufnahme mit der Spur von ${esc(it.name)}" decoding="async">
      <svg viewBox="0 0 ${c.width} ${c.height}" preserveAspectRatio="none" aria-hidden="true" style="--c:${it.color}">${guide(1)}${guide(-1)}</svg></button>
    <figcaption>${c.kind === 'kandidat' ? 'Trefferbild' : 'Vollbild der Trefferaufnahme'}${c.time ? ' · ' + esc(String(c.time).replace('T', ' ')) : ''} · Spur zwischen den Linien</figcaption></figure>`;
}
async function satDetails(el, more, run, it) {
  let d;
  try { d = await loadDetails(run, it.key, it.norad_id); }
  catch (e) { if (el.isConnected) el.innerHTML = `<p class="mut sm">${icon('alert')} Details nicht verfügbar: ${esc(e.message)}</p>`; return; }
  if (!el.isConnected) return;
  const rows = [];
  if (d.intl_id) rows.push(['Kennung', `${esc(d.intl_id)} <span class="dim">(international)</span>`]);
  if (d.kind) rows.push(['Art', esc(d.kind)]);
  if (d.launch_date || d.launch_year) rows.push(['Start', esc(d.launch_date ? deDate(d.launch_date) : String(d.launch_year)) + (d.owner ? ' · ' + esc(d.owner) : '')]);
  else if (d.owner) rows.push(['Herkunft', esc(d.owner)]);
  if (d.decay_date) rows.push(['Verglüht', esc(deDate(d.decay_date))]);
  const oc = d.orbit_catalog;
  if (oc?.perigee_km != null && oc?.apogee_km != null) rows.push(['Bahnhöhe', `${fmt(oc.perigee_km)}–${fmt(oc.apogee_km)} km <span class="dim">(Katalog, heute)</span>`]);
  if (d.speed) rows.push(['Winkelgeschw.', `${d.speed.kind === 'mindestens' ? '≥ ' : ''}${fmt(d.speed.arcsec_s, d.speed.arcsec_s < 100 ? 1 : 0)}″/s <span class="dim">(${esc(d.speed.text)})</span>`]);
  const b = d.brightness;
  if (b?.mag != null) rows.push(['Helligkeit', `≈ ${fmt(b.mag, 1)} mag ± ${fmt(b.err, 1)} <span class="dim">(gemessen; ${esc(b.note)})</span>`]);
  else if (b?.note) rows.push(['Helligkeit', `<span class="dim">${esc(b.note)}</span>`]);
  else rows.push(['Helligkeit', '<span class="dim">nicht messbar (kein Querprofil oder keine Durchgangszeit gespeichert)</span>']);
  const c = d.certainty || {}, [lab, cls] = LEVEL[c.level] || [c.level || '–', ''];
  el.innerHTML = `${cropHtml(d.crop, it)}${d.crop ? '' : `<p class="mut sm st3d-nocrop">${icon('image')} ${esc(d.crop_note || 'Kein Bildausschnitt gespeichert.')}</p>`}
    <div class="st3d-sure"><span class="chip ${cls}">${esc(lab)}</span><span>${esc(c.text || '')}</span>${c.facts?.length ? `<small>${esc(c.facts.join(' · '))}${c.others?.length ? ' · ebenfalls: ' + esc(c.others.join(', ')) : ''}</small>` : ''}</div>`;
  more.innerHTML = `<dl>${rows.map(([a, v]) => `<dt>${a}</dt><dd>${v}</dd>`).join('')}</dl>
    <p class="st3d-ext sm"><span class="dim">Extern:</span> <a href="${esc(d.links.celestrak)}" target="_blank" rel="noopener noreferrer">CelesTrak</a> · <a href="${esc(d.links.n2yo)}" target="_blank" rel="noopener noreferrer">N2YO</a>
      ${d.catalog_source ? `<span class="dim"> · Katalog: ${esc(d.catalog_source)}</span>` : ''}</p>`;
}
// Adresse der 3D-Ansicht eines Laufs (Reiter im Lauf); den Fokus-Kandidaten übergibt skytrace.js selbst
const href = (runId) => `#/skytrace/${encodeURIComponent(runId)}/3d`;

/* Lauf-Ebene auf dem gemeinsamen Globus. G: { THREE, sc, earth, camera, controls(), makeControls(up), zoomMode(m), setFly(fn), dirty(), glow,
   addMat(layer, m, base), layerK(layer), hook(kind, fn) → ab, cardEl, labelsHost, stage, $d, setSun(v) }.
   opts: { interactive, focus, onCandidate, legendEl, noteEl, label } → { state, setView, dispose, points, hits, notes, time, items } */
function runLayer(G, data, { interactive = false, focus = null, onCandidate = null, legendEl = null, noteEl = null, label: runLabel = '' } = {}) {
  const { THREE } = G, $d = G.$d, stage = G.stage, labelsHost = G.labelsHost;
  const camera = G.camera;
  const V = (p) => new THREE.Vector3(p[0] / R, p[2] / R, -p[1] / R);            // D (km) → Szene
  const U = (p) => new THREE.Vector3(p[0], p[2], -p[1]).normalize();            // Richtung
  const own = [], keep = (x) => { own.push(x); return x; };
  const mat = (m, base = 1) => keep(G.addMat('runs', m, base));
  const world = new THREE.Group(), fixed = new THREE.Group();                    // raumfest bzw. erdfest (in der Erdgruppe)
  G.sc.add(world); G.earth.add(fixed);
  const anchor = data.anchor, [tMin, tMax] = data.window, P = data.pointing;
  const S = { t: anchor, playing: false, sel: null, mode: 'earth', k: 0 };

  const sprite = (color, size, parent = world) => { const s = new THREE.Sprite(mat(new THREE.SpriteMaterial({ map: G.glow, color, sizeAttenuation: false, depthWrite: false })));
    s.scale.setScalar(size); parent.add(s); return s; };
  const line = (pts, color, opacity = 1, parent = world) => { const g = keep(new THREE.BufferGeometry().setFromPoints(pts));
    const l = new THREE.Line(g, mat(new THREE.LineBasicMaterial({ color, depthWrite: false }), opacity)); parent.add(l); return l; };
  const labels = [];
  const label = (text, color, obj, select, sub) => { const b = document.createElement('button'); b.className = 'st3d-lbl'; b.textContent = text; b.style.setProperty('--c', color);
    if (sub) { const s = document.createElement('small'); s.textContent = sub; b.append(s); }
    b.onclick = () => select?.(); labelsHost.append(b); const L = { el: b, obj }; labels.push(L); return L; };

  // ---- Standort und Himmelskuppel (erdfest, lokales Horizontsystem) ----
  const obsLocal = V(data.observer).multiplyScalar(1.0004);
  const observer = sprite('#f2d385', .026, fixed); observer.position.copy(obsLocal);
  const E = U(data.enu.east), N = U(data.enu.north), Up = U(data.enu.up);
  const dome = new THREE.Group(); dome.position.copy(obsLocal); fixed.add(dome);
  const onDome = (az, alt, r = DOME) => { const a = az * Math.PI / 180, h = alt * Math.PI / 180;
    return N.clone().multiplyScalar(Math.cos(h) * Math.cos(a)).add(E.clone().multiplyScalar(Math.cos(h) * Math.sin(a))).add(Up.clone().multiplyScalar(Math.sin(h))).multiplyScalar(r); };
  for (const alt of [0, 30, 60]) line(Array.from({ length: 73 }, (_, i) => onDome(i * 5, alt)), '#9fb0d9', alt ? .18 : .45, dome);
  for (const az of [0, 90, 180, 270]) line(Array.from({ length: 19 }, (_, i) => onDome(az, i * 5)), az ? '#9fb0d9' : '#f87171', az ? .16 : .6, dome);
  const domeN = new THREE.Object3D(); domeN.position.copy(onDome(0, 0, DOME * 1.12)); dome.add(domeN);
  // Weg der Blickrichtung über die Nacht, an Lücken unterbrochen
  const gapAt = (a, b) => data.gaps.some(([g1, g2]) => a <= g1 && b >= g2);
  { const pathMat = mat(new THREE.MeshBasicMaterial({ color: '#f2d385', depthWrite: false }));
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
  const fov = new THREE.Group(); world.add(fov);
  const reachKm = Math.max(800, ...sats.filter(s => s.in_fov).map(s => s.at_t0.range_km * 1.35));
  const L = Math.min(reachKm, 4000) / R;
  const facePos = new THREE.Float32BufferAttribute(new Float32Array(36), 3), edgePos = new THREE.Float32BufferAttribute(new Float32Array(48), 3);
  { const g = keep(new THREE.BufferGeometry()); g.setAttribute('position', facePos);
    fov.add(new THREE.Mesh(g, mat(new THREE.MeshBasicMaterial({ color: '#f2d385', side: THREE.DoubleSide, depthWrite: false }), .14)));
    const e = keep(new THREE.BufferGeometry()); e.setAttribute('position', edgePos);
    fov.add(new THREE.LineSegments(e, mat(new THREE.LineBasicMaterial({ color: '#f2d385', depthWrite: false }), .75))); }
  const ray = new THREE.Line(keep(new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(), new THREE.Vector3(1, 0, 0)])),
    mat(new THREE.LineDashedMaterial({ color: '#f2d385', dashSize: .6, gapSize: .45, depthWrite: false }), .55));
  world.add(ray);
  // Himmelsäquator auf der Sternkugel als Orientierung, Ziel als Punkt am Himmel
  line(Array.from({ length: 121 }, (_, i) => new THREE.Vector3(Math.cos(i / 120 * Math.PI * 2) * SKY, 0, -Math.sin(i / 120 * Math.PI * 2) * SKY)), '#7c9cff', .22);
  const target = sprite('#f2d385', .034);
  const bore0 = U(P.center[0]); target.position.copy(bore0.clone().multiplyScalar(SKY));
  const tg = data.target || {};
  const targetSub = [tg.kind, tg.distance].filter(Boolean).join(' · ') || 'Zielrichtung';
  if (!interactive) { ray.visible = false; target.visible = false; }

  // ---- Treffer ----
  // Je Treffer: Linien-Materialien (Hervorheben) und Stützpunkte für das Antippen im Bildschirmraum (`polys`, großzügige Trefferfläche)
  const thin = (arr, k) => arr.filter((_, i) => i % k === 0 || i === arr.length - 1);
  for (const h of hits) {
    h.group = new THREE.Group(); world.add(h.group); h.mats = [];
    if (h.kind === 'sat') {
      h.mats.push(line(h.ring.map(V), h.color, h.alternative ? .18 : .3, h.group).material, line(h.track.map(V), h.color, .9, h.group).material);
      h.polys = [thin(h.track, 3).map(V), thin(h.ring, 2).map(V)];
      for (const seg of h.fov_segments) {
        const pts = seg.pos.map(V);
        if (pts.length > 1) h.group.add(new THREE.Mesh(keep(new THREE.TubeGeometry(new THREE.CatmullRomCurve3(pts), Math.max(4, pts.length), .0003, 6)), mat(new THREE.MeshBasicMaterial({ color: '#ffffff', depthWrite: false }))));
        h.pierce = h.pierce || pts[Math.floor(pts.length / 2)];
        sprite('#ffffff', .02, h.group).position.copy(pts[Math.floor(pts.length / 2)]);
        h.polys.push(thin(pts, Math.max(1, Math.floor(pts.length / 12))));
      }
      h.obj = sprite(h.color, .04);
    } else {
      const pts = h.ecef.map(V);
      h.mats.push(line(pts, h.color, 1, fixed).material); h.obj = sprite(h.color, .03, fixed); h.obj.position.copy(pts.at(-1)); h.pts = pts; h.polys = [pts];
    }
    h.size = h.obj.scale.x;
    h.name = h.name || (h.kind === 'meteor' ? 'Meteor' : 'Flugzeug');
  }
  const items = [{ kind: 'scope', name: interactive ? 'Teleskop' : (runLabel || 'Teleskop'), color: '#f2d385', obj: observer }, { kind: 'target', name: tg.name || 'Ziel', color: '#ffe8a8', obj: target }, ...hits];
  for (const it of items) if (interactive || it.kind !== 'target')
    it.label = label(it.name, it.color, it.obj, () => select(it), it.kind === 'target' ? targetSub + ' · nicht maßstäblich' : !interactive && it.kind === 'scope' ? 'Teleskop · ' + (tg.name || '') : null);
  if (interactive) label('N', '#f87171', domeN, () => select(items[0])).el.classList.add('st3d-lbl-n');

  // ---- Zeit ----
  const bar = interactive ? $d('.st3d-bar') : null;
  const range = bar?.querySelector('.st3d-range input'), timeEl = bar?.querySelector('.st3d-time'), playBtn = bar?.querySelector('[data-play]'), speedSel = bar?.querySelector('select');
  const strip = bar?.querySelector('.st3d-strip');
  if (bar) { range.min = tMin; range.max = tMax; range.step = 1; range.value = anchor; bar.hidden = false; }
  function drawStrip() {
    if (!strip) return;
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
  const lerp = (arr, f) => { f = Math.min(Math.max(f, 0), arr.length - 1); const i = Math.floor(f), k = f - i, a = arr[i], b = arr[Math.min(i + 1, arr.length - 1)];
    return Array.isArray(a) ? a.map((v, j) => v + (b[j] - v) * k) : a + (b - a) * k; };
  function setTime(t) {
    S.t = Math.min(Math.max(t, tMin), tMax); if (range) range.value = S.t;
    const k = S.k = sampleAt(S.t), pause = inPause(S.t);
    if (timeEl) timeEl.innerHTML = `${utc(S.t)} <span class="mut">${pause ? 'Pause' : 'Aufnahme ' + ((P.index[k] ?? k) + 1)}</span>`;
    if (interactive) G.earth.rotation.y = data.earth_rate * (S.t - anchor);
    G.earth.updateMatrixWorld(true);
    obsW.copy(obsLocal); if (interactive) G.earth.localToWorld(obsW); fov.position.copy(obsW);      // statisch: Erde in erdfester Lage (Drehung 0)
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
      // statisch: jeder Satellit an seinem Durchgang (Schnappschuss), sonst zur eingestellten Zeit
      const tt = interactive ? S.t : h.t_pass ?? S.t, inside = tt >= h.window[0] && tt <= h.window[1];
      if (h.kind === 'sat') { h.obj.visible = h.on && inside; if (inside) h.obj.position.copy(V(lerp(h.track, (tt - h.window[0]) / 2))); }
      else { h.obj.visible = h.on && (!interactive || S.t >= h.times[0] - 60 && S.t <= h.times.at(-1) + 60);
        if (h.kind === 'plane') { const j = h.times.findIndex(x => x >= tt); h.obj.position.copy(h.pts[j < 0 ? h.pts.length - 1 : j]); } }
    }
    if (S.mode === 'sat' || S.mode === 'scope' || S.mode === 'aim') follow();
    if (S.sel) card(S.sel);
    G.dirty();
  }
  if (range) range.oninput = () => { stop(); setTime(+range.value); };
  const stop = () => { S.playing = false; if (playBtn) { playBtn.innerHTML = icon('play'); playBtn.title = 'Abspielen'; } };
  if (playBtn) playBtn.onclick = () => { if (S.playing) return stop(); if (S.t >= tMax) setTime(tMin); S.playing = true; playBtn.innerHTML = icon('pause'); playBtn.title = 'Anhalten'; };

  // ---- Kamera (nur interaktiv) ----
  const views = interactive ? $d('.st3d-views') : null;
  if (views) {
    views.hidden = false;
    views.querySelector('[data-v=sat]').hidden = !sats.length;
    views.querySelector('[data-v=pass]').hidden = !hits.length;
  }
  const chosen = () => (S.sel && S.sel.kind !== 'scope' && S.sel.kind !== 'target' ? S.sel : hits.find(h => h.focus) || hits[0]);
  const followed = () => (S.sel?.kind === 'sat' ? S.sel : sats.find(h => h.focus) || sats[0]);
  let lastTarget = null;
  // Kamera wandert mit (Satellit bzw. Standort, der sich mit der Erde dreht)
  function follow() { const p = S.mode === 'scope' || S.mode === 'aim' ? obsW : followed()?.obj.position; if (!p) return;
    const controls = G.controls();
    if (lastTarget) { const dl = tmp.copy(p).sub(lastTarget); camera.position.add(dl); controls.target.add(dl); } lastTarget = p.clone(); }
  function setView(mode, animate = true) {
    if (!interactive) return;
    S.mode = mode; lastTarget = null;
    views.querySelectorAll('button').forEach(b => b.classList.toggle('on', b.dataset.v === mode));
    let tgt, pos;
    const up = obsW.clone().normalize(), wantUp = mode === 'scope' || mode === 'aim' ? up : new THREE.Vector3(0, 1, 0);
    if (camera.up.distanceTo(wantUp) > 1e-6) G.makeControls(wantUp);
    const controls = G.controls();
    const wide = stage.clientWidth < 520 ? 1.6 : 1;      // Handy: etwas mehr Abstand
    G.zoomMode(mode === 'earth' ? 'globe' : 'orbit');
    if (mode === 'earth') {
      const h = chosen(), dir = up.clone().add(h?.kind === 'sat' && h.obj.visible ? h.obj.position.clone().normalize() : new THREE.Vector3()).normalize();
      tgt = new THREE.Vector3(); pos = dir.multiplyScalar(stage.clientWidth < 520 ? 4.4 : 3.4).add(new THREE.Vector3(0, .5, 0));
    } else if (mode === 'aim') {                         // vom Standort entlang des Strahls zum Zielobjekt
      tgt = obsW.clone().add(bore.clone().multiplyScalar(3));
      pos = obsW.clone().sub(bore.clone().multiplyScalar(DOME * 3 * wide)).add(up.clone().multiplyScalar(DOME * 1.2 * wide));
    } else if (mode === 'scope') {                       // hinter und über dem Standort, Blick in Zeigerichtung
      const horiz = bore.clone().projectOnPlane(up).normalize(), side = new THREE.Vector3().crossVectors(horiz, up).normalize();
      tgt = obsW.clone().add(up.clone().multiplyScalar(DOME * .4)).add(horiz.clone().multiplyScalar(DOME * .4));
      pos = obsW.clone().sub(horiz.multiplyScalar(DOME * 2.6 * wide)).add(up.multiplyScalar(DOME * 1.9 * wide)).add(side.multiplyScalar(DOME * .9 * wide));
    } else {
      const h = mode === 'sat' ? followed() : chosen();
      tgt = (mode === 'pass' && h?.pierce) ? h.pierce.clone() : (h?.obj.getWorldPosition(new THREE.Vector3()) || obsW.clone());
      const toObs = obsW.clone().sub(tgt), dist = Math.max(toObs.length(), .02);
      const side = new THREE.Vector3().crossVectors(toObs, tgt.clone().normalize()).normalize();
      pos = tgt.clone().add(toObs.multiplyScalar(.45)).add(side.multiplyScalar(dist * .45)).add(tgt.clone().normalize().multiplyScalar(dist * .12));
    }
    controls.minDistance = mode === 'earth' ? 1.05 : .002;
    const from = { p: camera.position.clone(), t: controls.target.clone() }, start = performance.now(), ms = animate && !reduced() ? 1100 : 0;
    G.setFly((now) => { const k = ms ? Math.min(1, (now - start) / ms) : 1, e = 1 - Math.pow(1 - k, 3);
      camera.position.lerpVectors(from.p, pos, e); G.controls().target.lerpVectors(from.t, tgt, e); G.controls().update();
      if (k >= 1) { G.setFly(null); if (mode === 'sat') lastTarget = tgt.clone(); if (mode === 'scope' || mode === 'aim') lastTarget = obsW.clone(); } G.dirty(); });
  }
  if (views) views.onclick = (e) => { const b = e.target.closest('[data-v]'); if (b) setView(b.dataset.v); };

  // ---- Auswahl und Infokarte (nur interaktiv) ----
  const cardEl = G.cardEl;
  const openCand = (key) => onCandidate ? onCandidate(key) : window.SkyTraceHelpers?.openCandidate(data.run, key);
  function card(it) {
    const rows = [], k = S.k;
    let head, actions = '';
    if (it.kind === 'sat') {
      const type = TYPES[it.object_type] || it.object_type, inside = interactive && S.t >= it.window[0] && S.t <= it.window[1], f = (S.t - it.window[0]) / 2;
      head = `<b style="--c:${it.color}">${esc(it.name)}</b><small>NORAD ${esc(it.norad_id)}${type ? ' · ' + esc(type) : ''} · ${esc(it.candidate)}${it.alternative ? ' · Alternative' : ''}${!interactive && runLabel ? ' · ' + esc(runLabel) : ''}</small>`;
      rows.push(['Durchgang', utc(it.t_pass) + (interactive ? ` (${rel(S.t - it.t_pass)})` : '')]);
      if (inside) rows.push(['Höhe', fmt(lerp(it.height_km, f)) + ' km'], ['Geschwindigkeit', fmt(lerp(it.speed_kms, f), 2) + ' km/s'], ['Entfernung', fmt(lerp(it.range_km, f)) + ' km']);
      else rows.push(['Höhe', fmt(it.at_t0.height_km) + ' km beim Durchgang'], ['Entfernung', fmt(it.at_t0.range_km) + ' km zum Teleskop · ' + fmt(it.at_t0.speed_kms, 2) + ' km/s']);
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
    if (it.t_pass && interactive) actions += `<button class="btn ghost sm" data-a="pass">Zum Durchgang</button>`;
    if (it.key && (onCandidate || (data.run && window.SkyTraceHelpers))) actions += `<button class="btn ghost sm" data-a="cand">Kandidat ${esc(it.candidate)} öffnen</button>`;
    const dl = rows.map(([a, b]) => `<dt>${a}</dt><dd>${b}</dd>`).join('');
    // gleiche Auswahl (Zeitleiste läuft): nur die Zeilen erneuern – Details und Bildausschnitt bleiben stehen
    if (cardEl._st3d === it && cardEl.querySelector('.st3d-rows')) { cardEl.querySelector('.st3d-rows').innerHTML = dl; cardEl.hidden = false; return; }
    cardEl._st3d = it;
    const sat = it.kind === 'sat' && data.run && it.key;
    cardEl.innerHTML = `<div class="st3d-card-h"><div>${head}</div><button class="btn ghost icon sm" data-a="x" aria-label="Schließen">${icon('x')}</button></div>
      ${sat ? `<div class="st3d-sx" aria-live="polite"><p class="mut sm"><span class="spinner"></span>Details und Bildausschnitt werden geladen …</p></div>` : ''}
      <dl class="st3d-rows">${dl}</dl>${sat ? '<div class="st3d-sx st3d-sxd"></div>' : ''}
      ${actions ? `<div class="row st3d-card-a">${actions}</div>` : ''}`;
    cardEl.onclick = (e) => { const a = e.target.closest('[data-a]')?.dataset.a;
      if (a === 'x') select(null); else if (a === 'pass') { stop(); setTime(it.t_pass); setView('pass'); } else if (a === 'cand' || a === 'crop') openCand(it.key); };
    cardEl.hidden = false;
    if (sat) satDetails(cardEl.querySelector('.st3d-sx'), cardEl.querySelector('.st3d-sxd'), data.run, it);
  }
  // Hervorheben (Antippen, Überfahren, Auswahl): Linien kräftiger, Punkt größer
  let hov = null, picked = null;
  function emph(it) {
    if (!it?.mats) return;
    const on = it === hov || it === S.sel || it === picked;
    for (const m of it.mats) { m.userData.base0 ??= m.userData.base; m.userData.base = on ? Math.min(1, m.userData.base0 * 2.4 + .12) : m.userData.base0; }
    it.obj.scale.setScalar(on ? it.size * 1.6 : it.size);
    G.refresh?.('runs');
  }
  function select(it) {
    const was = picked; picked = it && it.kind !== 'scope' && it.kind !== 'target' ? it : null; emph(was); emph(picked);
    if (!interactive) {
      if (it?.kind === 'sat' || it?.kind === 'meteor' || it?.kind === 'plane') card(it);
      else if (it) G.onRunPick?.(data); else cardEl.hidden = true;
      return;
    }
    const old = S.sel; S.sel = it; emph(old); emph(it);
    legendEl?.querySelectorAll('[data-k]').forEach(b => b.classList.toggle('on', !!it && b.dataset.k === String(items.indexOf(it))));
    if (it) card(it); else cardEl.hidden = true;
    if (S.mode === 'sat' && it?.kind === 'sat') setView('sat');
    if (it?.kind === 'target') setView('aim');
  }
  // Treffer nahe einem Bildschirmpunkt (Abstand zu Punkt, Bahn, Umlauf, Durchstoß) – Antippen, Überfahren, Auswahl bei mehreren
  const pv = new THREE.Vector3(), cp = new THREE.Vector3();
  const scr = (p, w, h) => { if (behind(p)) return null; pv.copy(p).project(camera); return Math.abs(pv.z) > 1 ? null : [(pv.x + 1) / 2 * w, (1 - pv.y) / 2 * h]; };
  const segD = (x, y, a, b) => { const dx = b[0] - a[0], dy = b[1] - a[1], l = dx * dx + dy * dy, t = l ? Math.min(1, Math.max(0, ((x - a[0]) * dx + (y - a[1]) * dy) / l)) : 0;
    return Math.hypot(a[0] + t * dx - x, a[1] + t * dy - y); };
  function near(sx, sy, r, w, h) {
    if (!world.visible || G.layerK('runs') < .4) return [];
    const out = [];
    for (const it of hits) {
      if (!it.on) continue;
      let best = Infinity;
      const polys = it.obj.visible ? [[it.obj.getWorldPosition(new THREE.Vector3())], ...it.polys] : it.polys;
      for (const poly of polys) {
        let prev = null;
        for (const p0 of poly) {
          const s = scr(it.kind === 'sat' || poly.length === 1 ? p0 : fixed.localToWorld(cp.copy(p0)), w, h);
          if (s) best = Math.min(best, prev ? segD(sx, sy, prev, s) : Math.hypot(s[0] - sx, s[1] - sy));
          prev = s;
        }
      }
      if (best <= r) out.push(entry(it, best));
    }
    return out;
  }
  const entry = (it, d) => ({ d, it, id: (data.run || '') + '|' + it.key + '|' + (it.norad_id ?? it.name), name: it.name, color: it.color, kind: it.kind,
    sub: [utc(it.t_pass ?? it.times?.[0]).slice(0, 8) + ' UTC', TYPES[it.object_type] || (it.kind === 'meteor' ? 'Meteor' : it.kind === 'plane' ? 'Flugzeug' : ''), it.candidate, !interactive && runLabel].filter(Boolean).join(' · '),
    pick: () => { select(it); if (interactive && it.t_pass) { stop(); setTime(it.t_pass); } }, hover: (on) => { hov = on ? it : hov === it ? null : hov; emph(it); } });
  const offNear = G.hook('near', near);
  // Bodenspur je Satellit für die flache Karte (Punkt unter dem Satelliten beim Durchgang + ±5 min)
  const ground = () => (world.visible && G.layerK('runs') > .01 ? hits : []).filter(h => h.on && h.ground?.length).map(h => {
    const i = Math.round((h.t_pass - h.window[0]) / (h.ground_step || 10));
    return { ...entry(h, 0), pts: h.ground, at: h.ground[Math.min(Math.max(i, 0), h.ground.length - 1)], on: h === hov || h === S.sel || h === picked };
  });
  if (legendEl) {
    legendEl.innerHTML = items.map((it, i) => `<span class="st3d-chip" style="--c:${it.color}">${it.t_pass ? `<input type="checkbox" checked data-v="${i}" aria-label="${esc(it.name)} ein-/ausblenden">` : ''}<button data-k="${i}"><i></i>${esc(it.name)}${it.t_pass ? `<small>${utc(it.t_pass).slice(0, 8)}</small>` : ''}</button></span>`).join('');
    legendEl.onclick = (e) => { const b = e.target.closest('[data-k]'); if (!b) return; const it = items[+b.dataset.k]; select(it); if (it.t_pass) { stop(); setTime(it.t_pass); } };
    legendEl.onchange = (e) => { const it = items[+e.target.dataset.v]; if (!it) return; it.on = e.target.checked; it.group && (it.group.visible = it.on); if (it.label) it.label.el.dataset.off = it.on ? '' : '1'; setTime(S.t); };
  }
  const notes = [];
  if (data.missing.length) notes.push(...data.missing.map(m => `${m.candidate}: ${m.name || 'NORAD ' + m.norad_id} – ${m.reason}`));
  if (P.source !== 'wcs') notes.push('Blickrichtung aus der Katalogkoordinate des Ziels geschätzt (kein Plate-Solving).');
  if (data.clock_offset_s) {
    const a = Math.abs(data.clock_offset_s), m = Math.floor(a / 60), r = Math.round(a - 60 * m);
    notes.push(`Uhr korrigiert um ${data.clock_offset_s < 0 ? '−' : '+'}${m ? m + ' min ' + r + ' s' : r + ' s'} (übernommener Uhrversatz der Nacht; Zeiten in wahrer UTC).`);
  }
  if (data.clock_pending) notes.push('Die Satellitentreffer stammen aus einem Abgleich mit anderer Uhrkorrektur – „Satelliten neu abgleichen“, damit sie zur korrigierten Uhr passen.');
  if (interactive) {
    notes.push(hits.length ? `${hits.length} zugeordnete${hits.length === 1 ? 's Objekt' : ' Objekte'}; Bahnen per SGP4, Unsicherheit wenige km.` : 'Keine Katalogtreffer in diesem Lauf – gezeigt wird der Weg des Teleskops.');
    notes.push('Kuppel und Himmel nicht maßstäblich.');
    if (noteEl) noteEl.textContent = notes.join(' ');
  }
  // Antippen: Objekte dieses Laufs (vor den Orten/Nächten des Globus)
  const offPick = G.hook('pick', (rc) => {
    if (!world.visible || G.layerK('runs') < .4) return false;
    const objs = items.map(x => x.obj).filter(o => o.visible);
    const hit = rc.intersectObjects(objs, false)[0];
    if (!hit) return false;
    select(items.find(x => x.obj === hit.object)); return true;
  });

  // ---- Zeichnen: Schilder (hinter der Erde ausblenden) ----
  const behind = (p) => { const o = camera.position, dir = tmp.copy(p).sub(o), len = dir.length(); dir.divideScalar(len);
    const b = o.dot(dir), c = o.lengthSq() - .995 * .995, disc = b * b - c; if (disc < 0) return false; const t = -b - Math.sqrt(disc); return t > 0 && t < len; };
  const wp = new THREE.Vector3();
  const offDraw = G.hook('draw', (w, h) => {
    const k = G.layerK('runs'), dist = camera.position.length();
    for (const Lb of labels) {
      Lb.obj.getWorldPosition(wp); const hidden = k < .4 || !world.visible || !Lb.obj.visible || Lb.el.dataset.off === '1' || (!interactive && dist > 2.4) || behind(wp); wp.project(camera);
      Lb.el.hidden = hidden || Math.abs(wp.z) > 1;
      if (!Lb.el.hidden) Lb.el.style.transform = `translate(${(wp.x + 1) / 2 * w}px, ${(1 - wp.y) / 2 * h}px)`;
    }
  });
  const offFrame = interactive ? G.hook('frame', (now, dt) => {
    if (!S.playing) return;
    const t = S.t + dt * +speedSel.value;
    if (t >= tMax) { setTime(tMax); stop(); } else setTime(t);
  }) : null;
  const offResize = interactive ? G.hook('resize', drawStrip) : null;
  if (interactive) G.setSun(U(data.sun));
  drawStrip();
  const start = hits.find(h => h.focus);
  if (interactive && start && speedSel) { speedSel.value = '10'; setTime(start.t_pass); } else setTime(P.t[0]);
  // Stützpunkte für den Bildausschnitt (statisch: Standort und Satelliten beim Durchgang, Szenenkoordinaten)
  const points = [obsLocal.clone(), ...hits.filter(h => h.obj).map(h => h.obj.position.clone())];
  return {
    data, items, hits: hits.length, notes, time: anchor, points, world, fixed, name: runLabel, ground, near,
    // Prüfwert (Tests): je Treffer ein sichtbarer Bildschirmpunkt seiner Bahn (Punkt, Bahnstück oder Umlauf)
    screen: () => { const w = stage.clientWidth, h = stage.clientHeight;
      return hits.map(it => { for (const poly of [[it.obj.visible ? it.obj.getWorldPosition(new THREE.Vector3()) : null], ...it.polys]) for (const p of poly) {
        if (!p) continue; const s = scr(it.kind === 'sat' || poly.length === 1 ? p : fixed.localToWorld(cp.copy(p)), w, h);
        if (s && s[0] > 20 && s[1] > 20 && s[0] < w - 20 && s[1] < h - 20) return { name: it.name, norad: it.norad_id ?? null, key: it.key, x: +s[0].toFixed(1), y: +s[1].toFixed(1) }; }
        return { name: it.name, norad: it.norad_id ?? null, key: it.key, x: null, y: null }; }); },
    sel: () => picked && { name: picked.name, key: picked.key, norad: picked.norad_id ?? null },
    begin(animate = true) { if (!interactive) return; if (start) { select(start); setView('pass', animate); } else setView('earth', animate); },
    setView, setVisible(on) { world.visible = fixed.visible = on; for (const Lb of labels) Lb.el.hidden = true; G.dirty(); },
    state: () => ({ t: S.t, mode: S.mode, hits: hits.length, sample: S.k, visible: hits.filter(h => h.obj.visible).length,
      // Prüfwerte: Höhe der Blickrichtung über dem lokalen Horizont, Kamera hinter dem Standort (< 0)
      alt: Math.asin(bore.dot(obsW.clone().normalize())) * 180 / Math.PI, camAlong: camera.position.clone().sub(obsW).dot(bore) }),
    dispose() {
      stop(); offPick(); offNear(); offDraw(); offFrame?.(); offResize?.();
      G.sc.remove(world); G.earth.remove(fixed); for (const Lb of labels) Lb.el.remove();
      G.dropMats('runs', own); for (const x of own) x.dispose?.();
      if (bar) bar.hidden = true; if (views) views.hidden = true;
      if (legendEl) legendEl.innerHTML = '';
      G.dirty();
    },
  };
}

// Lauf-Reiter „3D“ (skytrace.js tab3d): eingebettete 3D-Erde mit festem Lauf – siehe SkyTraceErde.embed
function mount(host, runId, opts = {}) { return SkyTraceErde.embed(host, { run: runId, ...opts }); }

window.SkyTrace3D = { available, mount, href, runLayer };
})();
