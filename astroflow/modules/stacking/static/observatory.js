'use strict';
/* Observatorium, Etappe 1: Himmelsatlas #/observatorium[?ra=&dec=&fov=&name=] – ganzer Himmel, zoombar (stereografische
   Projektion auf Canvas): Sterne bis 6,5 mag (Yale BSC), Sternbildlinien (d3-celestial), Deep-Sky-Katalog wie „Heute Nacht“
   und die eigenen Bilder an ihrer echten Lage (vier Ecken aus der Plate-Solving-Lösung, /api/stacking/atlas).
   Klick → Kontextpanel mit Projekt bzw. Sage. Sage-Objektseiten springen mit ?ra=&dec=&name= hierher. */
(() => {
const D2R = Math.PI / 180, FOV_MIN = 0.3, FOV_MAX = 220, FOV_HOME = 120;
const KEY = 'obs.view', LKEY = 'obs.layers';
const LAYERS = [['photos', 'Meine Fotos', 'image'], ['catalog', 'Katalog', 'target'], ['cons', 'Sternbilder', 'star'], ['grid', 'Gitter', 'grid'], ['plan', 'Plan', 'list'], ['tracks', 'Bahnen', 'orbit']];
const ZENITH = { ra: 180, dec: 90, fov: 190 };                   // „Von hier“: ganzer Himmel von oben (Norden oben, Osten links)
const hmT = (t) => new Date(t * 1000).toLocaleTimeString('de-DE', { hour: '2-digit', minute: '2-digit' });
const spanT = (s) => `${hmT(s[0])}–${hmT(s[1])}`;
const de = (v, n = 1) => Number(v).toFixed(n).replace('.', ',');
const reduced = () => matchMedia('(prefers-reduced-motion: reduce)').matches;
const norm = (ra) => ((ra % 360) + 360) % 360;

let skyP = null;
const skyData = () => skyP || (skyP = Promise.all(['sterne', 'sternbilder'].map(n =>
  fetch(`/m/stacking/himmel/${n}.json?v=${AF.app?.build || AF.app?.version || ''}`).then(r => { if (!r.ok) throw new Error('Himmelsdaten fehlen'); return r.json(); })))
  .then(([s, c]) => ({ stars: s.stars, cons: c.items })).catch(e => { skyP = null; throw e; }));

// Stereografische Projektion um (ra0, dec0); Osten links, Norden oben. S = Pixel je Bogenmaß in der Mitte.
function projection(v, W, H, cut = -0.6) {
  const d0 = v.dec * D2R, sd0 = Math.sin(d0), cd0 = Math.cos(d0);
  const S = Math.min(W, H) / (4 * Math.tan(v.fov * D2R / 4));
  const fwd = (ra, dec) => {
    const d = dec * D2R, a = (ra - v.ra) * D2R, sd = Math.sin(d), cd = Math.cos(d), ca = Math.cos(a);
    const cc = sd0 * sd + cd0 * cd * ca;
    if (cc < cut) return null;
    const k = 2 / (1 + cc);
    return [W / 2 - k * cd * Math.sin(a) * S, H / 2 - k * (cd0 * sd - sd0 * cd * ca) * S];
  };
  const inv = (X, Y) => {
    const x = (W / 2 - X) / S, y = (H / 2 - Y) / S, rho = Math.hypot(x, y);
    if (rho < 1e-12) return [v.ra, v.dec];
    const c = 2 * Math.atan(rho / 2), sc = Math.sin(c), cc = Math.cos(c);
    const dec = Math.asin(Math.max(-1, Math.min(1, cc * sd0 + y * sc * cd0 / rho)));
    const ra = v.ra + Math.atan2(x * sc, rho * cd0 * cc - y * sd0 * sc) / D2R;
    return [norm(ra), dec / D2R];
  };
  // halber sichtbarer Winkel (Diagonale) in Grad – für Gitter und Katalog-Vorauswahl
  const reach = Math.min(180, 2 * Math.atan(Math.hypot(W, H) / 2 / S / 2) / D2R * 1.05);
  return { fwd, inv, S, W, H, reach };
}

const angSep = (ra1, de1, ra2, de2) => {
  const a = Math.sin(de1 * D2R) * Math.sin(de2 * D2R) + Math.cos(de1 * D2R) * Math.cos(de2 * D2R) * Math.cos((ra1 - ra2) * D2R);
  return Math.acos(Math.max(-1, Math.min(1, a))) / D2R;
};
const pickStep = (want, steps) => steps.find(s => s <= want) || steps[steps.length - 1];
const starColor = (bv) => bv < 0 ? '#b5ccff' : bv < 0.4 ? '#e6edff' : bv < 0.9 ? '#fff3df' : bv < 1.4 ? '#ffdcb0' : '#ffc68f';
const fmtRA = (ra) => { const h = norm(ra) / 15, m = Math.round((h % 1) * 60); return `${Math.floor(h) + (m === 60 ? 1 : 0)}h ${String(m % 60).padStart(2, '0')}m`; };
const fmtDec = (d) => `${d < 0 ? '−' : '+'}${Math.abs(Math.round(d))}°`;
const fmtFov = (f) => f >= 2 ? `${Math.round(f)}°` : `${Math.round(f * 60)}′`;
const fmtExp = (s) => !s ? '' : s >= 3600 ? `${de(s / 3600, 1)} h` : `${Math.round(s / 60)} min`;
const fmtDate = (ts) => ts ? new Date(ts * 1000).toLocaleDateString('de-DE', { day: 'numeric', month: 'short', year: 'numeric' }) : '';

// „Von hier“: Ortssternzeit, Azimut/Höhe (Nord 0°, Ost 90°) und genäherte Sonnenposition (~0,01°) – alles lokal
const gmst = (ts) => ((280.46061837 + 360.98564736629 * (ts / 86400 + 2440587.5 - 2451545)) % 360 + 360) % 360;
function horizontal(ra, dec, lst, lat) {
  const h = (lst - ra) * D2R, d = dec * D2R, p = lat * D2R;
  const alt = Math.asin(Math.max(-1, Math.min(1, Math.sin(p) * Math.sin(d) + Math.cos(p) * Math.cos(d) * Math.cos(h))));
  const az = Math.atan2(-Math.cos(d) * Math.sin(h), Math.sin(d) * Math.cos(p) - Math.cos(d) * Math.cos(h) * Math.sin(p));
  return [norm(az / D2R), alt / D2R];
}
function sunRaDec(ts) {
  const n = ts / 86400 + 2440587.5 - 2451545, L = (280.46 + 0.9856474 * n) % 360, gA = (357.528 + 0.9856003 * n) * D2R;
  const lam = (L + 1.915 * Math.sin(gA) + 0.02 * Math.sin(2 * gA)) * D2R, eps = (23.439 - 4e-7 * n) * D2R;
  return [norm(Math.atan2(Math.cos(eps) * Math.sin(lam), Math.cos(lam)) / D2R), Math.asin(Math.sin(eps) * Math.sin(lam)) / D2R];
}
const hzAt = (h, az) => { if (!h) return 0; const x = norm(az) / 10, i = Math.floor(x) % 36, f = x - Math.floor(x); return h[i] * (1 - f) + h[(i + 1) % 36] * f; };
const DIRS8 = ['N', 'NO', 'O', 'SO', 'S', 'SW', 'W', 'NW'];

// Zeichenreihenfolge der eigenen Fotos (hinten → vorne): großes Bildfeld hinten, kleines vorne; das überfahrene und
// das ausgewählte Foto ganz nach vorne (überfahren vor ausgewählt, damit man ein verdecktes Nachbarbild anschauen kann)
function photoOrder(list, selPid = null, hoverPid = null) {
  const area = (p) => (p.fov?.[0] || 0) * (p.fov?.[1] || 0);
  const rank = (p) => (p.pid === hoverPid ? 2 : p.pid === selPid ? 1 : 0);
  return list.slice().sort((a, b) => rank(a) - rank(b) || area(b) - area(a) || a.pid - b.pid);
}

function pointInPoly(x, y, pts) {
  let inside = false;
  for (let i = 0, j = pts.length - 1; i < pts.length; j = i++) {
    const [xi, yi] = pts[i], [xj, yj] = pts[j];
    if ((yi > y) !== (yj > y) && x < (xj - xi) * (y - yi) / (yj - yi) + xi) inside = !inside;
  }
  return inside;
}

// Bild auf ein Dreieck abbilden (affin) – zwei Dreiecke ergeben das verzerrte Viereck
function drawTri(g, img, s, d) {
  const [[u0, v0], [u1, v1], [u2, v2]] = s, [[x0, y0], [x1, y1], [x2, y2]] = d;
  const den = (u1 - u0) * (v2 - v0) - (u2 - u0) * (v1 - v0);
  if (!den) return;
  const a = ((x1 - x0) * (v2 - v0) - (x2 - x0) * (v1 - v0)) / den, b = ((x2 - x0) * (u1 - u0) - (x1 - x0) * (u2 - u0)) / den;
  const c = ((y1 - y0) * (v2 - v0) - (y2 - y0) * (v1 - v0)) / den, e = ((y2 - y0) * (u1 - u0) - (y1 - y0) * (u2 - u0)) / den;
  const cx = (x0 + x1 + x2) / 3, cy = (y0 + y1 + y2) / 3, grow = (x, y) => [x + (x - cx) * 0.02, y + (y - cy) * 0.02];   // Naht vermeiden
  g.save();
  g.beginPath(); g.moveTo(...grow(x0, y0)); g.lineTo(...grow(x1, y1)); g.lineTo(...grow(x2, y2)); g.closePath(); g.clip();
  g.setTransform(g.__dpr * a, g.__dpr * c, g.__dpr * b, g.__dpr * e, g.__dpr * (x0 - a * u0 - b * v0), g.__dpr * (y0 - c * u0 - e * v0));
  g.drawImage(img, 0, 0);
  g.restore();
}

async function atlasView(el, query) {
  crumbs([['Übersicht', '#/'], ['Observatorium']]);
  const q = new URLSearchParams(query || '');
  let saved = store.get(KEY, null);
  const v = { ra: 83.8, dec: 0, fov: FOV_HOME, ...(saved && typeof saved.ra === 'number' ? saved : {}) };
  let target = null;                        // Marke aus Sage (?ra=&dec=&name=)
  const fromQuery = (qs) => {
    const ra = parseFloat(qs.get('ra')), dec = parseFloat(qs.get('dec'));
    if (!isFinite(ra) || !isFinite(dec)) return false;
    const name = (qs.get('name') || '').slice(0, 80);
    target = name ? { ra: norm(ra), dec: Math.max(-90, Math.min(90, dec)), name } : null;          // Marke nur mit Namen (Einstieg aus Sage)
    const f = parseFloat(qs.get('fov'));
    return { ra: norm(ra), dec: Math.max(-90, Math.min(90, dec)), fov: isFinite(f) ? Math.max(FOV_MIN, Math.min(FOV_MAX, f)) : 8 };
  };
  const start = fromQuery(q);
  if (start) Object.assign(v, reduced() ? start : { ...start, fov: Math.min(FOV_MAX, start.fov * 5) });   // danach hineinfliegen
  const on = { photos: true, catalog: true, cons: true, grid: true, plan: true, tracks: true, ...store.get(LKEY, {}) };
  const wantHere = q.get('blick') === 'hier';
  let pathDate = (q.get('date') || '').slice(0, 10);
  if (wantHere) on.tracks = true;
  // Bahnen der Ziele durch die Nacht (/api/stacking/dome, dome.py): nur „Von hier“; trackSel = angeklicktes Ziel
  let paths = null, pathsP = null;
  let planned = [];                         // Ziele des gespeicherten Plans (Planer): {ra, dec, name, when}
  // Blickart: 'sky' (äquatorial) oder 'here' (Azimut/Höhe vom Standort zur Zeit tsel); je Blickart eigene Ansicht
  let mode = 'sky', site = null, tsel = Date.now() / 1000;
  const views = { sky: { ...v }, here: { ...ZENITH, ...(wantHere ? {} : store.get(KEY + '.here', {})) } };
  const toHor = (ra, dec) => horizontal(ra, dec, gmst(tsel) + site.lon, site.lat);
  const sage = AF.on('sage');

  el.innerHTML = `<div class="pagehead"><div><h1>Observatorium</h1><p class="mut">Dein Himmel – mit allen eigenen Aufnahmen an ihrem Platz.</p></div>${window.AFplan?.tabs('atlas') || ''}</div>
    <div class="obs-stage" id="obsStage">
      <canvas id="obsCanvas" aria-label="Himmelskarte" role="img" tabindex="0"></canvas><canvas class="obs-fade" id="obsFade" aria-hidden="true" hidden></canvas>
      <div class="obs-top">
        <label class="obs-search">${icon('search')}<input id="obsFind" type="search" list="obsList" placeholder="Objekt suchen" autocomplete="off" aria-label="Objekt suchen"><datalist id="obsList"></datalist></label>
        <div class="seg obs-mode" role="group" aria-label="Blickart" hidden><button type="button" data-mode="sky" class="on" aria-pressed="true">${icon('globe')}<span>Himmel</span></button><button type="button" data-mode="here" aria-pressed="false">${icon('pin')}<span>Von hier</span></button></div>
        <div class="seg obs-layers" role="group" aria-label="Ebenen">${LAYERS.map(([k, l, i]) => `<button type="button" data-layer="${k}" aria-pressed="${on[k]}" class="${on[k] ? 'on' : ''}" title="${l}"${k === 'plan' || k === 'tracks' ? ' hidden' : ''}>${icon(i)}<span>${l}</span></button>`).join('')}</div>
      </div>
      <div class="obs-zoom" role="group" aria-label="Zoom"><button type="button" data-z="in" title="Vergrößern" aria-label="Vergrößern">${icon('plus')}</button><button type="button" data-z="out" title="Verkleinern" aria-label="Verkleinern">${icon('minus')}</button><button type="button" data-z="home" title="Ganzer Himmel" aria-label="Ganzer Himmel">${icon('globe')}</button></div>
      <div class="obs-read tnum" id="obsRead" aria-live="off"></div>
      <div class="obs-time" id="obsTime" hidden><input type="range" id="obsT" min="0" max="${16 * 60}" step="10" value="0" aria-label="Uhrzeit"><b class="tnum" id="obsTL"></b><button type="button" class="btn ghost sm" id="obsNow">Jetzt</button></div>
      <div class="obs-msg" id="obsMsg" hidden></div>
    </div>
    <section class="obs-free" id="obsFree" hidden></section>
    <section class="obs-strip-wrap"><div class="panel-h"><h2>${icon('image')}Meine Fotos am Himmel</h2><span class="grow"></span><span class="chip" id="obsCount">…</span></div><div class="obs-strip" id="obsStrip"></div></section>`;

  const stage = $('#obsStage', el), cv = $('#obsCanvas', el), g = cv.getContext('2d');
  let W = 0, H = 0, P = null, sky = null, atlas = { photos: [], catalog: [] }, sel = null, hover = null, hits = [], raf = 0, anim = 0;
  const imgs = new Map();
  let col = {};
  const readColors = () => {
    const cs = getComputedStyle(document.documentElement), c = (n) => cs.getPropertyValue(n).trim();
    col = { ok: c('--ok'), bg: c('--bg'), surface: c('--surface'), border: c('--border'), fg: c('--fg'), mut: c('--mut'), dim: c('--dim'), acc: c('--acc'), cat: c('--data-2'), warn: c('--warn'), font: getComputedStyle(document.body).fontFamily };
  };
  readColors();

  const draw = () => { if (!raf) raf = requestAnimationFrame(paint); };
  const now = () => { cancelAnimationFrame(raf); paint(); };
  // Überblenden statt Springen (Ebenen, Blickart, eintreffende Daten): altes Bild als Standbild darüber, neu zeichnen, Standbild
  // ausblenden. Während einer Kamerafahrt nicht (das Standbild stünde dann neben dem bewegten Bild).
  let lastMove = 0;
  const fadeCv = $('#obsFade', el);
  fadeCv.addEventListener('transitionend', () => { if (fadeCv.classList.contains('out')) fadeCv.hidden = true; });
  function crossfade(change) {
    if (reduced() || !W || !P || performance.now() - lastMove < 80) { change(); draw(); return; }
    fadeCv.width = cv.width; fadeCv.height = cv.height; fadeCv.style.width = W + 'px'; fadeCv.style.height = H + 'px';
    fadeCv.getContext('2d').drawImage(cv, 0, 0);
    fadeCv.classList.remove('out'); fadeCv.hidden = false;
    change(); now();
    void fadeCv.offsetWidth; fadeCv.classList.add('out');
  }
  const save = () => store.set(mode === 'here' ? KEY + '.here' : KEY, { ra: +v.ra.toFixed(3), dec: +v.dec.toFixed(3), fov: +v.fov.toFixed(3) });

  function size() {
    const r = stage.getBoundingClientRect(), dpr = Math.min(2, window.devicePixelRatio || 1);
    W = Math.max(1, Math.round(r.width)); H = Math.max(1, Math.round(r.height));
    cv.width = W * dpr; cv.height = H * dpr; cv.style.width = W + 'px'; cv.style.height = H + 'px';
    g.__dpr = dpr;
    draw();
  }

  function image(p, big) {
    const key = big ? p.image : p.thumb;
    let im = imgs.get(key);
    if (!im) {
      im = new Image(); im.decoding = 'async';
      im.onload = () => { im.ok = true; if (el.isConnected) draw(); };
      im.src = key; imgs.set(key, im);
    }
    return im.ok ? im : (big ? image(p, false) : null);
  }

  function paint() {
    raf = 0;
    if (!el.isConnected) return;
    const dpr = g.__dpr;
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    const base = projection(v, W, H), here = mode === 'here' && site;
    // „Von hier“: Himmelskoordinaten → Azimut/Höhe; Azimut wächst nach rechts (Blick nach Süden: Osten links). Keine Vorauswahl
    // nach Himmelsabstand (reach 180), die Bildschirmprüfung reicht.
    P = here ? { ...base, fwd: (ra, dec) => { const [az, alt] = toHor(ra, dec); return base.fwd(360 - az, alt); }, reach: 180 } : base;
    const { fwd, S, reach } = P;
    hits = [];
    // Himmel (von hier: in der Dämmerung heller)
    const grd = g.createRadialGradient(W / 2, H / 2, 0, W / 2, H / 2, Math.hypot(W, H) / 2);
    grd.addColorStop(0, col.surface); grd.addColorStop(1, col.bg);
    g.fillStyle = grd; g.fillRect(0, 0, W, H);
    const sunAlt = here ? toHor(...sunRaDec(tsel))[1] : -90;
    if (sunAlt > -18) { g.fillStyle = `rgba(70, 110, 170, ${Math.min(0.75, (sunAlt + 18) / 18 * 0.75)})`; g.fillRect(0, 0, W, H); }
    g.font = '500 11px ' + col.font;
    if (on.grid) grid(base);
    if (sky && on.cons) constellations(P);
    if (sky) stars(P);
    const shown = on.photos ? photoQuads(P) : [];
    if (on.catalog) catalog(P, shown.filter(x => x.wpx > 36).map(x => x.q));
    photos(shown);
    if (on.plan && planned.length) {                            // geplante Ziele: gestrichelte Ringe mit erster Nacht
      g.setLineDash([5, 4]); g.lineWidth = 1.6;
      const labels = [];                                          // Beschriftungen nicht übereinander (M31/M32/M110)
      for (const t of planned) {
        const q = fwd(t.ra, t.dec);
        if (!q || q[0] < -30 || q[1] < -30 || q[0] > W + 30 || q[1] > H + 30) continue;
        g.strokeStyle = t.color; g.beginPath(); g.arc(q[0], q[1], 13, 0, 2 * Math.PI); g.stroke();
        if (v.fov < 90 && !labels.some(([x, y]) => Math.abs(x - q[0]) < 90 && Math.abs(y - q[1]) < 16)) {
          g.fillStyle = t.color; g.fillText(`geplant · ${t.when}`, q[0] + 16, q[1] + 16); labels.push(q);
        }
      }
      g.setLineDash([]);
    }
    if (here) ground();
    if (here && on.tracks && paths) trackLayer(base);
    if (target) {
      const t = fwd(target.ra, target.dec);
      if (t) {
        g.strokeStyle = col.warn; g.lineWidth = 1.5;
        const r = 14;
        g.beginPath(); g.arc(t[0], t[1], r, 0, 2 * Math.PI); g.stroke();
        for (const [dx, dy] of [[1, 0], [-1, 0], [0, 1], [0, -1]]) { g.beginPath(); g.moveTo(t[0] + dx * (r + 3), t[1] + dy * (r + 3)); g.lineTo(t[0] + dx * (r + 9), t[1] + dy * (r + 9)); g.stroke(); }
        if (target.name && !target.matched) { g.fillStyle = col.warn; g.fillText(target.name, t[0] + r + 12, t[1] - r + 2); }
      }
    }
    $('#obsRead', el).textContent = here ? `${DIRS8[Math.round(norm(360 - v.ra) / 45) % 8]} · Höhe ${Math.round(v.dec)}° · Feld ${fmtFov(v.fov)} · Sonne ${Math.round(sunAlt)}°`
      : `${fmtRA(v.ra)} · ${fmtDec(v.dec)} · Feld ${fmtFov(v.fov)}`;
    void S; void reach;
  }

  // Boden unter der Horizontmaske des Ortes (bzw. 0°), Himmelsrichtungen auf der Horizontlinie
  function ground() {
    // Horizontlinie als geschlossener Linienzug (stereografisch: ein Kreis bzw. eine Kurve); der Boden ist alles außerhalb
    // (Blick über den Horizont) bzw. innerhalb (Blick nach unten). Fast bis zum Gegenpunkt projizieren, sonst fehlen Stücke.
    const { fwd } = projection(v, W, H, -0.9995), hz = site.horizon, pts = [];
    for (let az = 0; az <= 360; az += 1) { const q = fwd(360 - az, hzAt(hz, az)); if (q) pts.push(q); }
    if (pts.length < 3) return;
    g.beginPath();
    if (v.dec >= 0) g.rect(-10, -10, W + 20, H + 20);
    pts.forEach((c, i) => i ? g.lineTo(...c) : g.moveTo(...c)); g.closePath();
    g.fillStyle = col.bg; g.globalAlpha = 0.93; g.fill('evenodd'); g.globalAlpha = 1;
    g.strokeStyle = col.ok; g.lineWidth = 1.5; g.beginPath();
    pts.forEach((c, i) => (i && Math.hypot(c[0] - pts[i - 1][0], c[1] - pts[i - 1][1]) < W) ? g.lineTo(...c) : g.moveTo(...c));
    g.stroke();
    // ab hier frei: Mindesthöhe (30°) bzw. höher, wo die Maske höher ist – gestrichelt
    const lim = paths?.min_alt ?? 30, fr = [];
    for (let az = 0; az <= 360; az += 2) { const q = fwd(360 - az, Math.max(lim, hzAt(hz, az))); if (q) fr.push(q); }
    g.setLineDash([6, 5]); g.globalAlpha = 0.75; g.beginPath();
    fr.forEach((c, i) => (i && Math.hypot(c[0] - fr[i - 1][0], c[1] - fr[i - 1][1]) < W) ? g.lineTo(...c) : g.moveTo(...c));
    g.stroke(); g.setLineDash([]); g.globalAlpha = 1;
    const { fwd: f2 } = projection(v, W, H);
    g.font = '700 12px ' + col.font; g.textAlign = 'center'; g.fillStyle = col.mut;
    DIRS8.forEach((n, k) => { const p = f2(360 - k * 45, 2); if (p && p[0] > 8 && p[0] < W - 8 && p[1] > 8 && p[1] < H - 8) g.fillText(n, p[0], p[1] - 4); });
    g.textAlign = 'start'; g.font = '500 11px ' + col.font;
  }

  // Bahnen durch die Nacht (Azimut/Höhe vom Server): gestrichelt = dunkel, aber tief oder verdeckt; kräftig = frei;
  // dick = geplanter Block; Marke zur gewählten Uhrzeit. Angeklicktes Ziel mit Stundenmarken, andere blasser.
  const pathTargets = () => { const pl = paths.targets.filter(t => t.source === 'plan'); return pl.length ? pl : paths.targets; };
  const pathColor = (t) => (t.source === 'plan' && window.AFplan ? window.AFplan.color(t.id) : col.acc);
  const pathAt = (t, ts) => {                                     // Position zur Zeit ts (zwischen den 10-min-Punkten)
    const fi = (ts - paths.t0) / paths.step, i = Math.floor(fi), f = fi - i;
    if (i < 0 || i + 1 >= paths.n) return null;
    const da = ((t.az[i + 1] - t.az[i] + 540) % 360) - 180;
    return [t.az[i] + da * f, t.alt[i] + (t.alt[i + 1] - t.alt[i]) * f];
  };
  const freeAt = (t, ts) => t.free.some(([a, b]) => ts >= a && ts < b);
  // Ablauf je Teleskop: Blöcke des Plans in dieser Nacht nach Zeit, mit Ziel
  const runs = () => (paths.devices || []).map(dv => ({ ...dv, seq: paths.targets.flatMap(t => t.blocks.filter(b => b.device === dv.id).map(b => ({ ...b, t })))
    .sort((a, b) => a.start - b.start) })).filter(r => r.seq.length);
  function trackLayer(base) {
    const d = paths, T = (i) => d.t0 + i * d.step;
    const dark = (i) => d.dark.some(([a, b]) => T(i) >= a && T(i) < b);
    const pick = sel?.type === 'track' ? sel.o.id : null;
    const line = (t, ok) => {
      g.beginPath(); let on2 = false;
      for (let i = 0; i < d.n; i++) {
        const q = ok(i) && t.alt[i] >= 0 ? base.fwd(360 - t.az[i], t.alt[i]) : null;
        if (!q) { on2 = false; continue; }
        if (on2) g.lineTo(...q); else g.moveTo(...q);
        on2 = true;
      }
      g.stroke();
    };
    g.lineCap = 'round';
    for (const t of pathTargets()) {
      const c = pathColor(t), me = t.id === pick;
      g.globalAlpha = pick && !me ? 0.3 : 1;
      g.strokeStyle = me ? col.fg : col.dim; g.lineWidth = 1; g.setLineDash([2, 4]); line(t, dark); g.setLineDash([]);
      g.strokeStyle = c; g.lineWidth = me ? 3 : 2; line(t, (i) => t.free.some(([a, b]) => T(i) >= a && T(i) < b));
      if (t.blocks.length) { g.lineWidth = me ? 6 : 5; g.globalAlpha *= 0.85; line(t, (i) => t.blocks.some(b => T(i) >= b.start && T(i) < b.end)); }
    }
    g.globalAlpha = 1; g.lineCap = 'butt';
    // Stundenmarken auf der gewählten Bahn (volle Stunden der Ortszeit des Browsers)
    const st = pick && pathTargets().find(t => t.id === pick);
    if (st) {
      const h = new Date(d.t0 * 1000); h.setMinutes(60, 0, 0);
      g.font = '600 11px ' + col.font;
      for (let ts = h.getTime() / 1000; ts <= T(d.n - 1); ts += 3600) {
        const p = pathAt(st, ts), fi = (ts - d.t0) / d.step;
        if (!p || p[1] < 0 || !dark(Math.floor(fi)) || !dark(Math.ceil(fi))) continue;
        const q = base.fwd(360 - p[0], p[1]);
        if (!q) continue;
        g.fillStyle = col.fg; g.beginPath(); g.arc(q[0], q[1], 2.5, 0, 2 * Math.PI); g.fill();
        g.fillText(hmT(ts).slice(0, 2), q[0] + 6, q[1] - 5);
      }
      g.font = '500 11px ' + col.font;
    }
    // Mond und Ziele zur gewählten Uhrzeit
    const mp = pathAt(d.moon, tsel);
    if (mp && mp[1] > 0) {
      const q = base.fwd(360 - mp[0], mp[1]);
      if (q) { g.fillStyle = col.warn; g.beginPath(); g.arc(q[0], q[1], 7, 0, 2 * Math.PI); g.fill(); g.fillText(`Mond ${d.moon.illum} %`, q[0] + 11, q[1] + 4); }
    }
    const labels = [];
    g.font = '600 11px ' + col.font;
    for (const t of pathTargets()) {
      const p = pathAt(t, tsel);
      if (!p || p[1] < 0) continue;
      const q = base.fwd(360 - p[0], p[1]);
      if (!q) continue;
      const free = freeAt(t, tsel), me = t.id === pick;
      g.globalAlpha = pick && !me ? 0.4 : 1;
      g.beginPath(); g.arc(q[0], q[1], me ? 6 : 4.5, 0, 2 * Math.PI);
      if (free) { g.fillStyle = pathColor(t); g.fill(); } else { g.strokeStyle = col.mut; g.lineWidth = 1.3; g.stroke(); }
      if ((free || me) && !labels.some(([x, y]) => Math.abs(x - q[0]) < 80 && Math.abs(y - q[1]) < 14)) {
        g.fillStyle = col.fg; g.fillText(t.name, q[0] + 9, q[1] + 4); labels.push(q);
      }
      hits.push({ type: 'track', o: t, x: q[0], y: q[1], r: 11 });
    }
    g.globalAlpha = 1;
    // Teleskope: Schwenkweg zwischen aufeinanderfolgenden Blöcken (Pfeil mit Uhrzeit) und Fadenkreuz am gerade angepeilten Ziel
    const at = (t, ts) => { const p = pathAt(t, ts); return p && p[1] > -5 ? base.fwd(360 - p[0], p[1]) : null; };
    runs().forEach((r, ri) => {
      g.strokeStyle = col.fg; g.fillStyle = col.fg; g.lineWidth = 1.4; g.setLineDash([5, 4]); g.globalAlpha = 0.75;
      for (let k = 1; k < r.seq.length; k++) {
        const a = at(r.seq[k - 1].t, r.seq[k - 1].end), b = at(r.seq[k].t, r.seq[k].start);
        if (!a || !b || Math.hypot(b[0] - a[0], b[1] - a[1]) < 12) continue;
        g.beginPath(); g.moveTo(...a); g.lineTo(...b); g.stroke();
        const ang = Math.atan2(b[1] - a[1], b[0] - a[0]);
        g.setLineDash([]); g.beginPath(); g.moveTo(...b);
        g.lineTo(b[0] - 9 * Math.cos(ang - 0.4), b[1] - 9 * Math.sin(ang - 0.4)); g.lineTo(b[0] - 9 * Math.cos(ang + 0.4), b[1] - 9 * Math.sin(ang + 0.4));
        g.closePath(); g.fill(); g.setLineDash([5, 4]);
        g.fillText(hmT(r.seq[k].start), (a[0] + b[0]) / 2 + 5, (a[1] + b[1]) / 2 - 5);
      }
      g.setLineDash([]); g.globalAlpha = 1;
      const cur = r.seq.find(b => tsel >= b.start && tsel < b.end), q = cur && at(cur.t, tsel);
      if (!q) return;
      const R = 17;
      g.strokeStyle = col.fg; g.lineWidth = 2;
      g.beginPath(); g.arc(q[0], q[1], R, 0, 2 * Math.PI); g.stroke();
      for (const [dx, dy] of [[1, 0], [-1, 0], [0, 1], [0, -1]]) { g.beginPath(); g.moveTo(q[0] + dx * (R - 6), q[1] + dy * (R - 6)); g.lineTo(q[0] + dx * (R + 7), q[1] + dy * (R + 7)); g.stroke(); }
      g.font = '700 11px ' + col.font; g.fillStyle = col.fg;
      g.fillText(`${r.name} · bis ${hmT(cur.end)}`, q[0] + R + 9, q[1] - R + 2 + ri * 14);
    });
    g.font = '500 11px ' + col.font;
  }

  // Listen unter der Karte: jetzt frei / später frei / schon vorbei / nie frei (zur gewählten Uhrzeit)
  let freeSig = '';
  function freeList(ts = tShow) {                              // tShow: Zielzeit des Reglers (auch während der Animation)
    const box = $('#obsFree', el);
    const showIt = mode === 'here' && on.tracks && paths && paths.targets.length;
    box.hidden = !showIt;
    if (!showIt) { freeSig = ''; return; }
    const now = [], later = [], past = [], never = [];
    for (const t of pathTargets()) {
      const cur = t.free.find(([a, b]) => ts >= a && ts < b), next = t.free.filter(([a]) => a > ts);
      if (cur) now.push([cur[1], t, `noch bis ${hmT(cur[1])}`]);
      else if (next.length) later.push([next[0][0], t, `frei ${next.map(spanT).join(', ')}`]);
      else if (t.free.length) past.push([0, t, `war frei ${t.free.map(spanT).join(', ')}`]);
      else never.push([0, t, t.why || 'nie frei']);
    }
    now.sort((a, b) => a[0] - b[0]); later.sort((a, b) => a[0] - b[0]);
    const pick = sel?.type === 'track' ? sel.o.id : '';
    const rr = runs(), active = rr.map(r => r.seq.find(b => ts >= b.start && ts < b.end)?.start || 0);
    const sig = [ts > 0 ? Math.round(ts / 600) : 0, pick, now.length, later.length, past.length, active.join(',')].join('|');
    if (sig === freeSig) return;
    const first = !freeSig;
    freeSig = sig;
    const item = ([, t, txt]) => `<li><button type="button" class="obs-fi${t.id === pick ? ' on' : ''}" data-track="${esc(t.id)}"><i style="background:${pathColor(t)}"></i><span class="obs-fn">${esc(t.name)}</span><span class="dim sm">${esc(txt)}</span></button></li>`;
    const grp = (title, rows, empty) => `<div class="obs-fg"><h3>${title} <span class="chip">${rows.length}</span></h3>${rows.length
      ? `<ul>${rows.slice(0, 8).map(item).join('')}</ul>${rows.length > 8 ? `<details><summary class="sm">Alle ${rows.length}</summary><ul>${rows.slice(8).map(item).join('')}</ul></details>` : ''}`
      : `<p class="mut sm">${empty}</p>`}</div>`;
    const d = paths;
    box.innerHTML = `<div class="panel-h"><h2>${icon('orbit')}Bahnen ${d.k ? 'in der Nacht ' + new Date(d.night + 'T12:00:00Z').toLocaleDateString('de-DE', { weekday: 'short', day: 'numeric', month: 'numeric', timeZone: 'UTC' }) : 'heute Nacht'}</h2><span class="grow"></span>
        <span class="mut sm">${d.dark.length ? 'dunkel ' + d.dark.map(spanT).join(', ') : 'keine astronomische Dunkelheit'} · Mond ${d.moon.illum} %</span></div>
      <p class="mut sm obs-fhint">Kräftige Linie = frei (dunkel, über ${d.min_alt}° und über deinem Horizont, gestrichelte Grenze) · dick = geplant · gestrichelt = tief oder verdeckt. Uhrzeit mit dem Regler wählen.</p>
      ${rr.length ? `<div class="obs-run"><h3>${icon('target')}Ablauf deiner Teleskope</h3>${rr.map((r, ri) => `<div class="obs-runrow"><b>${esc(r.name)}</b><ol>${r.seq.map(b =>
        `<li><button type="button" class="obs-step${b.start === active[ri] ? ' on' : ''}" data-at="${b.start}" data-track="${esc(b.t.id)}" title="${esc(b.t.name)} ${spanT([b.start, b.end])} – Zeit dorthin stellen"><i style="background:${pathColor(b.t)}"></i><span class="tnum">${hmT(b.start)}</span> ${esc(b.t.name)}</button></li>`).join('')}</ol></div>`).join('')}</div>` : ''}
      <div class="obs-fgrid">${grp(`Jetzt frei <span class="dim sm">(${hmT(ts)})</span>`, now, 'Gerade steht kein Ziel im freien Ausschnitt.')}${grp('Später frei', later, 'Bis zum Morgen kommt nichts mehr dazu.')}
        ${past.length ? grp('Schon vorbei', past, '') : ''}${grp('In dieser Nacht nie frei', never, 'Alle Ziele kommen irgendwann ins freie Fenster.')}</div>`;
    if (first) box.classList.add('obs-in');
  }
  function loadPaths() {
    if (pathsP || !site) return;
    const req = pathsP = api('/stacking/dome' + (pathDate ? '?date=' + encodeURIComponent(pathDate) : '')).then(async r => {
      if (!el.isConnected || req !== pathsP || !r || r.error || !r.targets) return;
      // Ort dieser Nacht (Ortsliste: kann je Nacht wechseln) – Himmel, Boden und Maske passend zu den Bahnen
      const ns = (await api('/sites/resolve?night=' + encodeURIComponent(r.night)).catch(() => null))?.site;
      if (!el.isConnected || req !== pathsP) return;
      if (ns && ns.lat != null) site = ns;
      paths = r;
      const b = $('[data-layer="tracks"]', el); if (b) b.hidden = mode !== 'here';
      setRange();
      crossfade(() => {}); freeList();
    }).catch(() => {});
  }

  function grid({ fwd, reach }) {
    const raStep = pickStep(v.fov / 5, [30, 15, 7.5, 3.75, 1.25, 0.5, 0.25, 0.125]);
    const deStep = pickStep(v.fov / 5, [30, 15, 10, 5, 2, 1, 0.5, 0.25, 0.1]);
    const poleIn = 90 - Math.abs(v.dec) < reach;
    const span = poleIn ? 180 : Math.min(180, reach / Math.max(0.05, Math.cos(Math.min(89, Math.abs(v.dec) + reach) * D2R)));
    const d1 = Math.max(-90, v.dec - reach), d2 = Math.min(90, v.dec + reach);
    g.strokeStyle = col.border; g.lineWidth = 1; g.globalAlpha = 0.55;
    const line = (pts) => {
      g.beginPath(); let pen = false, last = null;
      for (const [ra, dec] of pts) {
        const p = fwd(ra, dec);
        if (!p || (last && Math.hypot(p[0] - last[0], p[1] - last[1]) > Math.max(W, H))) { pen = false; last = p; continue; }
        if (pen) g.lineTo(p[0], p[1]); else { g.moveTo(p[0], p[1]); pen = true; }
        last = p;
      }
      g.stroke();
    };
    for (let dec = Math.ceil(d1 / deStep) * deStep; dec <= d2; dec += deStep) {
      if (Math.abs(dec) >= 89.999) continue;
      const pts = [];
      for (let i = 0; i <= 120; i++) pts.push([v.ra - span + i * span / 60, dec]);
      line(pts);
    }
    const r0 = Math.floor((v.ra - span) / raStep) * raStep;
    for (let ra = r0; ra <= v.ra + span + 1e-9 && ra < r0 + 360; ra += raStep) {
      const pts = [];
      for (let i = 0; i <= 60; i++) pts.push([ra, d1 + i * (d2 - d1) / 60]);
      line(pts);
    }
    g.globalAlpha = 1;
  }

  function constellations({ fwd }) {
    g.strokeStyle = col.cat; g.globalAlpha = 0.42; g.lineWidth = 1;
    g.beginPath();
    for (const c of sky.cons) {
      if (angSep(c.ra, c.dec, v.ra, v.dec) > P.reach + 40) continue;
      for (const seg of c.lines) {
        let last = null;
        for (let i = 0; i < seg.length; i += 2) {
          const p = fwd(seg[i], seg[i + 1]);
          if (p && last && Math.hypot(p[0] - last[0], p[1] - last[1]) < Math.max(W, H)) { g.moveTo(last[0], last[1]); g.lineTo(p[0], p[1]); }
          last = p;
        }
      }
    }
    g.stroke();
    g.globalAlpha = 1;
    if (v.fov > 6 && v.fov < 200) {
      g.fillStyle = col.cat; g.globalAlpha = 0.75; g.textAlign = 'center';
      g.font = '600 10px ' + col.font;
      for (const c of sky.cons) {
        if (v.fov > 150 && c.rank > 1) continue;
        const p = fwd(c.ra, c.dec);
        if (p && p[0] > 0 && p[0] < W && p[1] > 0 && p[1] < H) g.fillText(c.name.toUpperCase(), p[0], p[1]);
      }
      g.textAlign = 'start'; g.globalAlpha = 1;
      g.font = '500 11px ' + col.font;
    }
  }

  function stars({ fwd }) {
    const st = sky.stars, lim = v.fov > 150 ? 4.8 : v.fov > 80 ? 5.4 : v.fov > 30 ? 6 : 6.5;
    const boost = v.fov < 3 ? 2.2 : v.fov < 20 ? 1.5 : v.fov < 60 ? 1.15 : 1;
    for (let i = 0; i < st.length; i += 4) {
      const mag = st[i + 2];
      if (mag > lim) break;                                     // nach Helligkeit sortiert
      const p = fwd(st[i], st[i + 1]);
      if (!p || p[0] < -4 || p[1] < -4 || p[0] > W + 4 || p[1] > H + 4) continue;
      const r = Math.max(0.55, (lim + 1.2 - mag) * 0.42 * boost);
      g.fillStyle = starColor(st[i + 3]);
      g.globalAlpha = Math.min(1, 0.35 + (lim - mag) * 0.22);
      g.beginPath(); g.arc(p[0], p[1], r, 0, 2 * Math.PI); g.fill();
    }
    g.globalAlpha = 1;
  }

  function catalog({ fwd, S, reach }, named) {
    g.lineWidth = 1.2;
    for (const o of atlas.catalog) {
      if (o.src === 'a' && v.fov > 40) continue;                 // ASTAP-Zusatzobjekte erst beim Hineinzoomen
      if (Math.abs(o.dec - v.dec) > reach + 1 || angSep(o.ra, o.dec, v.ra, v.dec) > reach) continue;
      const p = fwd(o.ra, o.dec);
      const r = Math.max(3, (o.size || 1) / 120 * D2R * S), me = sel?.type === 'cat' && sel.o.id === o.id;
      if (!p || p[0] < -r - 40 || p[1] < -r - 40 || p[0] > W + r + 40 || p[1] > H + r + 40) continue;
      // Formen bewusst rund (Galaxie: Ellipse) – eckige Rahmen gehören den eigenen Fotos
      const big = r > 0.3 * Math.min(W, H);
      g.strokeStyle = me ? col.fg : col.cat; g.lineWidth = me ? 2 : 1.2; g.globalAlpha = big && !me ? 0.4 : 0.9;
      g.setLineDash(o.kind === 'open_cluster' || o.kind === 'globular' ? [3, 3] : ['emission', 'reflection', 'snr', 'other'].includes(o.kind) ? [8, 4] : []);
      g.beginPath();
      if (o.kind === 'galaxy' && r < 14) g.ellipse(p[0], p[1], r, Math.max(2, r * 0.55), -0.5, 0, 2 * Math.PI);   // nur als Symbol: Lagewinkel fehlt im Katalog
      else g.arc(p[0], p[1], r, 0, 2 * Math.PI);
      g.stroke(); g.setLineDash([]); g.globalAlpha = 1;
      if (o.kind === 'planetary' || o.kind === 'globular') { g.fillStyle = col.cat; g.beginPath(); g.arc(p[0], p[1], 1.4, 0, 2 * Math.PI); g.fill(); }
      // Name entfällt, wenn ein beschriftetes eigenes Foto das Objekt zeigt (sonst doppelte Beschriftung)
      const label = me || ((r > 16 || (o.src === 'm' && v.fov < 70) || (o.src === 'c' && v.fov < 40) || v.fov < 10)
        && !named.some(q => pointInPoly(p[0], p[1], q)));
      if (label) {
        g.fillStyle = me ? col.fg : col.mut;
        if (big || me) { g.textAlign = 'center'; g.fillText(o.name, p[0], Math.max(16, p[1] - r - 6)); g.textAlign = 'start'; }
        else g.fillText(o.name, p[0] + r + 4, p[1] + 4);
      }
      hits.push({ type: 'cat', o, x: p[0], y: p[1], r: Math.min(40, Math.max(r, 9)) });
    }
  }

  // sichtbare Fotos mit ihren Bildschirm-Ecken (einmal je Bild berechnet: Katalog-Beschriftung und Zeichnen)
  function photoQuads({ fwd, reach }) {
    const out = [];
    for (const p of atlas.photos) {
      if (angSep(p.ra, p.dec, v.ra, v.dec) > reach + Math.max(...p.fov)) continue;
      const q = p.corners.map(([ra, dec]) => fwd(ra, dec));
      if (q.some(x => !x)) continue;
      const xs = q.map(c => c[0]), ys = q.map(c => c[1]);
      const x0 = Math.min(...xs), x1 = Math.max(...xs), y0 = Math.min(...ys), y1 = Math.max(...ys);
      if (x1 < -20 || y1 < -20 || x0 > W + 20 || y0 > H + 20) continue;
      out.push({ p, q, x0, x1, y0, y1, wpx: Math.max(x1 - x0, y1 - y0) });
    }
    const order = photoOrder(out.map(x => x.p), sel?.type === 'photo' ? sel.o.pid : null, hover);
    const at = new Map(order.map((p, i) => [p.pid, i]));
    return out.sort((a, b) => at.get(a.p.pid) - at.get(b.p.pid));
  }

  function photos(list) {
    for (const { p, q, x0, x1, y0, y1, wpx } of list) {
      const me = sel?.type === 'photo' && sel.o.pid === p.pid, hot = me || hover === p.pid;
      if (wpx < 12) {                                             // im Weitwinkel nur als Punkt sichtbar
        const cx = (x0 + x1) / 2, cy = (y0 + y1) / 2;
        g.fillStyle = col.acc; g.globalAlpha = 0.9;
        g.beginPath(); g.arc(cx, cy, me ? 5 : 3.5, 0, 2 * Math.PI); g.fill();
        g.globalAlpha = 0.35; g.beginPath(); g.arc(cx, cy, me ? 10 : 7, 0, 2 * Math.PI); g.fill(); g.globalAlpha = 1;
        hits.push({ type: 'photo', o: p, x: cx, y: cy, r: 10 });
        continue;
      }
      const im = image(p, wpx > 360);
      if (im) {
        const w = im.naturalWidth, h = im.naturalHeight;
        drawTri(g, im, [[0, 0], [w, 0], [w, h]], [q[0], q[1], q[2]]);
        drawTri(g, im, [[0, 0], [w, h], [0, h]], [q[0], q[2], q[3]]);
      } else { g.fillStyle = col.surface; g.globalAlpha = 0.8; g.beginPath(); q.forEach((c, i) => i ? g.lineTo(...c) : g.moveTo(...c)); g.closePath(); g.fill(); g.globalAlpha = 1; }
      g.strokeStyle = me ? col.fg : col.acc; g.lineWidth = hot ? 2.5 : 1.3; g.globalAlpha = hot ? 1 : 0.85;
      g.beginPath(); q.forEach((c, i) => i ? g.lineTo(...c) : g.moveTo(...c)); g.closePath(); g.stroke(); g.globalAlpha = 1;
      hits.push({ type: 'photo', o: p, poly: q });
    }
    // Namen erst nach allen Bildern, damit kein Nachbarbild sie verdeckt
    g.font = '600 11px ' + col.font;
    list.forEach(({ p, q, wpx }, i) => {
      if (wpx <= 36) return;
      const top = q.reduce((a, c) => (c[1] < a[1] ? c : a));
      if (list.slice(i + 1).some(o => o.wpx >= 12 && pointInPoly(top[0] + 4, top[1] - 8, o.q))) return;   // liegt unter einem vorderen Foto
      const hot = (sel?.type === 'photo' && sel.o.pid === p.pid) || hover === p.pid;
      g.fillStyle = hot ? col.fg : col.acc;
      g.fillText(p.name, top[0] + 4, top[1] - 5);
    });
    g.font = '500 11px ' + col.font;
  }

  // ---- Auswahl → Kontextpanel ----
  function select(h, show = true) {
    const wasTrack = sel?.type === 'track';
    sel = h ? { type: h.type, o: h.o } : null;
    draw();
    if (wasTrack && sel?.type !== 'track') freeList();
    if (!sel) { AF.ctx.clear(); return; }
    const o = sel.o;
    if (sel.type === 'track') {
      freeList();
      AF.ctx.open(el, { title: o.name, html: `<dl class="kv"><dt>Frei</dt><dd>${o.free.length ? o.free.map(spanT).join(', ') + ` (${de(o.free_hours, 1)} h)` : esc(o.why || 'nie')}</dd>
          <dt>Höchster Stand</dt><dd>${Math.round(o.max_alt)}°</dd>${o.blocks.length ? `<dt>Geplant</dt><dd>${o.blocks.map(b => spanT([b.start, b.end])).join(', ')}</dd>` : ''}
          <dt>Herkunft</dt><dd>${o.source === 'plan' ? 'Beobachtungsplan' : 'eigenes Objekt'}</dd></dl>
        ${o.href ? `<div class="row obs-ctx-acts"><a class="btn sm" href="${o.href}">${icon('image')}Projekt öffnen</a></div>` : ''}` });
    } else if (sel.type === 'photo') {
      AF.ctx.open(el, { title: o.name, html: `<a class="obs-ctx-img" href="${o.href}"><img src="${esc(o.thumb)}" alt=""></a>
        <dl class="kv">${o.kind_label ? `<dt>Art</dt><dd>${esc(o.kind_label)}</dd>` : ''}${o.exposure ? `<dt>Belichtung</dt><dd>${fmtExp(o.exposure)}${o.frames ? ` · ${o.frames} Bilder` : ''}</dd>` : ''}
          <dt>Bildfeld</dt><dd>${fmtFov(o.fov[0])} × ${fmtFov(o.fov[1])}</dd><dt>Position</dt><dd>${fmtRA(o.ra)} · ${fmtDec(o.dec)}</dd>${o.created ? `<dt>Stand</dt><dd>${fmtDate(o.created)}</dd>` : ''}</dl>
        <div class="row obs-ctx-acts"><a class="btn sm" href="${o.href}">${icon('image')}Projekt öffnen</a>${sage ? `<button class="btn sm ghost" data-sage="${esc(o.name)}">${icon('book')}In Sage</button>` : ''}</div>` });
    } else {
      const mine = atlas.photos.filter(p => angSep(p.ra, p.dec, o.ra, o.dec) < (o.size || 0) / 120 + Math.max(...p.fov) / 2);   // Bild überlappt das Objekt
      AF.ctx.open(el, { title: o.name, html: `<dl class="kv">${o.kind_label ? `<dt>Art</dt><dd>${esc(o.kind_label)}</dd>` : ''}${o.size ? `<dt>Größe</dt><dd>${de(o.size, o.size < 10 ? 1 : 0)}′</dd>` : ''}
          ${o.mag != null ? `<dt>Helligkeit</dt><dd>${de(o.mag, 1)} mag</dd>` : ''}<dt>Position</dt><dd>${fmtRA(o.ra)} · ${fmtDec(o.dec)}</dd></dl>
        ${mine.length ? `<div class="obs-ctx-mine">${mine.slice(0, 4).map(p => `<a href="${p.href}" title="${esc(p.name)}"><img src="${esc(p.thumb)}" alt="${esc(p.name)}" loading="lazy"></a>`).join('')}</div>` : '<p class="mut sm">Noch nicht fotografiert.</p>'}
        ${sage ? `<div class="row obs-ctx-acts"><button class="btn sm ghost" data-sage="${esc(o.id)}">${icon('book')}In Sage</button></div>` : ''}` });
    }
    if (show) AF.ctx.show();
  }
  const onCtxClick = async (e) => {
    const b = e.target.closest('[data-sage]');
    if (!b || !el.isConnected) return;
    b.disabled = true;
    try {
      const card = await api('/sage/kb/card?name=' + encodeURIComponent(b.dataset.sage));
      if (card.found) location.hash = '#/sage/o/' + card.id; else toast('Sage kennt dieses Objekt noch nicht.', '');
    } catch (err) { toast(err.message, 'err'); }
    b.disabled = false;
  };
  AF.ctx.body()?.addEventListener('click', onCtxClick);

  function hitAt(x, y) {
    for (let i = hits.length - 1; i >= 0; i--) {                 // Fotos liegen oben
      const h = hits[i];
      if (h.type === 'photo' && h.poly && pointInPoly(x, y, h.poly)) return h;
    }
    let best = null, bd = Infinity;
    for (const h of hits) {
      if (h.poly) continue;
      const d = Math.hypot(h.x - x, h.y - y);
      if (d <= h.r && d < bd + (h.type === 'photo' ? 4 : 0)) { best = h; bd = d; }
    }
    return best;
  }

  // ---- Bewegung ----
  const clampFov = (f) => Math.max(FOV_MIN, Math.min(FOV_MAX, f));
  function zoomAt(f, X = W / 2, Y = H / 2, commit = true) {
    const before = P.inv(X, Y);
    v.fov = clampFov(v.fov * f);
    const P2 = projection(v, W, H), after = P2.inv(X, Y);
    v.ra = norm(v.ra + (before[0] - after[0] + 540) % 360 - 180);
    v.dec = Math.max(-90, Math.min(90, v.dec + before[1] - after[1]));
    if (commit) { save(); draw(); }
  }
  function zoomTo(f, X = W / 2, Y = H / 2) {                      // Knöpfe, Tasten, Doppelklick: kurz animiert (Rad/Pinch direkt)
    cancelAnimationFrame(anim);
    if (reduced()) return zoomAt(f, X, Y);
    const t0 = performance.now(), lf = Math.log(f);
    let done = 0;
    const step = (t) => {
      const k = Math.min(1, (t - t0) / 240), e = 1 - (1 - k) ** 3;
      zoomAt(Math.exp(lf * (e - done)), X, Y, false); done = e; lastMove = t; now();
      if (k < 1 && el.isConnected) anim = requestAnimationFrame(step); else save();
    };
    anim = requestAnimationFrame(step);
  }
  function fly(to, T = null) {
    cancelAnimationFrame(anim);
    if (mode === 'here' && site && !to.raw) { const [az, alt] = toHor(to.ra, to.dec); to = { ra: 360 - az, dec: alt, fov: to.fov }; }
    const from = { ...v }, dra = ((to.ra - from.ra + 540) % 360) - 180;
    if (reduced()) { Object.assign(v, { ra: norm(to.ra), dec: to.dec, fov: clampFov(to.fov) }); save(); draw(); return; }
    const t0 = performance.now(), lf0 = Math.log(from.fov), lf1 = Math.log(clampFov(to.fov));
    T = T ?? Math.min(750, 320 + angSep(from.ra, from.dec, to.ra, to.dec) * 2.5 + Math.abs(lf1 - lf0) * 110);
    const step = (t) => {
      const k = Math.min(1, (t - t0) / T), e = k < 0.5 ? 2 * k * k : 1 - (-2 * k + 2) ** 2 / 2;
      v.ra = norm(from.ra + dra * e); v.dec = from.dec + (to.dec - from.dec) * e; v.fov = Math.exp(lf0 + (lf1 - lf0) * e);
      lastMove = t; now();
      if (k < 1 && el.isConnected) anim = requestAnimationFrame(step); else save();
    };
    anim = requestAnimationFrame(step);
  }
  const flyPhoto = (p) => { fly({ ra: p.ra, dec: p.dec, fov: Math.max(...p.fov) * 2.4 }); select({ type: 'photo', o: p }); };

  const ptrs = new Map();
  let drag = null, pinch = 0, moved = false;
  const dragFrom = (x, y) => ({ x, y, P: projection(v, W, H), ra: v.ra, dec: v.dec });
  cv.addEventListener('pointerdown', (e) => {
    cv.setPointerCapture(e.pointerId); ptrs.set(e.pointerId, [e.offsetX, e.offsetY]);
    cancelAnimationFrame(anim);
    if (ptrs.size === 1) { drag = dragFrom(e.offsetX, e.offsetY); moved = false; }
    if (ptrs.size === 2) { const [a, b] = [...ptrs.values()]; pinch = Math.hypot(a[0] - b[0], a[1] - b[1]); moved = true; }
  });
  cv.addEventListener('pointermove', (e) => {
    if (!ptrs.has(e.pointerId)) {                                // nur Zeiger über der Karte: Mauszeiger
      const h = hitAt(e.offsetX, e.offsetY), hp = h?.type === 'photo' ? h.o.pid : null;
      cv.style.cursor = h ? 'pointer' : 'grab';
      if (hp !== hover) { hover = hp; draw(); }                 // überfahrenes Foto nach vorne
      return;
    }
    ptrs.set(e.pointerId, [e.offsetX, e.offsetY]);
    if (ptrs.size === 2 && pinch) {
      const [a, b] = [...ptrs.values()], d = Math.hypot(a[0] - b[0], a[1] - b[1]);
      if (d > 0) zoomAt(pinch / d, (a[0] + b[0]) / 2, (a[1] + b[1]) / 2);
      pinch = d; return;
    }
    if (!drag) return;
    if (Math.hypot(e.offsetX - drag.x, e.offsetY - drag.y) > 4) moved = true;
    if (!moved) return;
    // Punkt unter dem Zeiger festhalten: Mitte so verschieben, dass der Startpunkt dem Zeiger folgt
    const [ra1, de1] = drag.P.inv(drag.x, drag.y), [ra2, de2] = drag.P.inv(e.offsetX, e.offsetY);
    v.dec = Math.max(-90, Math.min(90, drag.dec + (de1 - de2)));
    v.ra = norm(drag.ra + ((ra1 - ra2 + 540) % 360 - 180));
    draw();
  });
  const up = (e) => {
    const was = ptrs.has(e.pointerId);
    ptrs.delete(e.pointerId);
    if (ptrs.size < 2) pinch = 0;
    // nach dem Pinch zieht der verbleibende Finger weiter: Zieh-Basis auf seine Position und die neue Ansicht setzen
    if (ptrs.size === 1 && was) { const [x, y] = [...ptrs.values()][0]; drag = dragFrom(x, y); }
    if (!ptrs.size) {
      if (was && !moved && e.type === 'pointerup') select(hitAt(e.offsetX, e.offsetY));
      if (moved) save();
      drag = null;
    }
  };
  cv.addEventListener('pointerup', up);
  cv.addEventListener('pointercancel', up);
  cv.addEventListener('pointerleave', () => { if (hover !== null && !ptrs.size) { hover = null; draw(); } });
  cv.addEventListener('wheel', (e) => { e.preventDefault(); zoomAt(Math.exp(e.deltaY * 0.0015), e.offsetX, e.offsetY); }, { passive: false });
  cv.addEventListener('dblclick', (e) => zoomTo(1 / 2, e.offsetX, e.offsetY));
  const nudge = (dra, ddec) => fly({ ra: v.ra + dra, dec: Math.max(-90, Math.min(90, v.dec + ddec)), fov: v.fov, raw: true }, 180);
  cv.addEventListener('keydown', (e) => {
    const s = v.fov / 10, k = e.key;
    if (k === '+' || k === '=') zoomTo(1 / 1.5);
    else if (k === '-') zoomTo(1.5);
    else if (k === 'ArrowLeft') nudge(s / Math.max(0.1, Math.cos(v.dec * D2R)), 0);
    else if (k === 'ArrowRight') nudge(-s / Math.max(0.1, Math.cos(v.dec * D2R)), 0);
    else if (k === 'ArrowUp') nudge(0, s);
    else if (k === 'ArrowDown') nudge(0, -s);
    else if (k === 'Escape') select(null);
    else return;
    e.preventDefault();
  });
  $('.obs-zoom', el).onclick = (e) => {
    const z = e.target.closest('[data-z]')?.dataset.z;
    if (z === 'in') zoomTo(1 / 1.6); else if (z === 'out') zoomTo(1.6); else if (z === 'home') fly(mode === 'here' ? { ...ZENITH, raw: true } : { ra: v.ra, dec: v.dec, fov: FOV_HOME, raw: true });
  };
  $('.obs-layers', el).onclick = (e) => {
    const b = e.target.closest('[data-layer]');
    if (!b) return;
    const k = b.dataset.layer;
    on[k] = !on[k]; b.classList.toggle('on', on[k]); b.setAttribute('aria-pressed', on[k]);
    store.set(LKEY, on); crossfade(() => {});
    if (k === 'tracks') { if (!on.tracks && sel?.type === 'track') select(null); freeList(); }
  };
  // Blickart umschalten (je Blickart eigene Mitte/Zoom), Zeitregler ab jetzt bis +16 h
  const setMode = (m, instant = false) => {
    if (m === mode || (m === 'here' && !site)) return;
    cancelAnimationFrame(anim);
    views[mode] = { ra: v.ra, dec: v.dec, fov: v.fov };
    store.set('obs.mode', m);
    $$('.obs-mode [data-mode]', el).forEach(b => { b.classList.toggle('on', b.dataset.mode === m); b.setAttribute('aria-pressed', b.dataset.mode === m); });
    $('#obsTime', el).hidden = m !== 'here';
    const tb = $('[data-layer="tracks"]', el); if (tb) tb.hidden = m !== 'here' || !paths;
    if (m === 'here') loadPaths();
    if (m !== 'here' && sel?.type === 'track') { sel = null; AF.ctx.clear(); }
    cv.setAttribute('aria-label', m === 'here' ? 'Himmel von deinem Standort aus' : 'Himmelskarte');
    if (instant || reduced()) { mode = m; Object.assign(v, views[m]); draw(); freeList(); return; }
    crossfade(() => { mode = m; Object.assign(v, views[m], { fov: clampFov(views[m].fov * 1.3) }); });
    freeList();
    fly({ ...views[m], raw: true }, 420);
  };
  $('.obs-mode', el).onclick = (e) => { const b = e.target.closest('[data-mode]'); if (b) setMode(b.dataset.mode); };
  // Zeitregler: ab jetzt bis +16 h; mit Bahnen über die ganze Nacht (ab jetzt bzw. ab Abend, falls die Nacht noch kommt)
  let t0 = Math.floor(Date.now() / 600000) * 600;
  function setRange() {
    const d = paths, end = d.t0 + (d.n - 1) * d.step, nowT = Date.now() / 1000;
    t0 = Math.floor(Math.min(nowT, d.t0) / 600) * 600;
    const r = $('#obsT', el);
    r.max = String(Math.max(60, Math.round((end - t0) / 60)));
    if (pathDate && d.k > 0) tsel = tShow = d.dark[0]?.[0] ?? d.t0;         // spätere Nacht aus Heute Nacht/Planer: ab Beginn der Dunkelheit
    r.value = String(Math.max(0, Math.round((tsel - t0) / 60)));
    showT();
  }
  const showT = (ts = tsel) => { $('#obsTL', el).textContent = new Date(ts * 1000).toLocaleString('de-DE', { weekday: 'short', hour: '2-digit', minute: '2-digit' }); };
  let tAnim = 0, tShow = tsel;
  const setT = (to) => {                                          // Himmel dreht sich kurz zum neuen Zeitpunkt statt zu springen
    tShow = to; showT(to); cancelAnimationFrame(tAnim);
    if (reduced() || mode !== 'here') { tsel = to; draw(); freeList(to); return; }
    const from = tsel, t1 = performance.now(), T = 180 + Math.min(220, Math.abs(to - from) / 3600 * 30);
    const step = (t) => {
      const k = Math.min(1, (t - t1) / T), e = 1 - (1 - k) ** 3;
      tsel = from + (to - from) * e; now();
      if (k < 1 && el.isConnected) tAnim = requestAnimationFrame(step);
    };
    tAnim = requestAnimationFrame(step);
    freeList(to);
  };
  $('#obsT', el).oninput = (e) => setT(t0 + +e.target.value * 60);
  $('#obsNow', el).onclick = () => { const nowT = Date.now() / 1000; $('#obsT', el).value = String(Math.max(0, Math.round((nowT - t0) / 60))); setT(nowT); };
  showT();
  api('/sites/resolve').then(r => {
    if (!el.isConnected || !r?.site || r.site.lat == null) return;
    site = r.site;
    $('.obs-mode', el).hidden = false;
    if (wantHere || store.get('obs.mode', 'sky') === 'here') { setMode('here', true); if (start) fly(start); }   // Ziel aus Sage/Planer auch „von hier“ anfliegen
  }).catch(() => {});
  $('#obsFree', el).addEventListener('click', (e) => {
    const b = e.target.closest('[data-track]'), t = b && paths?.targets.find(x => x.id === b.dataset.track);
    if (!t) return;
    if (b.dataset.at) {                                           // Ablauf: Zeitregler auf den Beginn des Blocks
      const to = +b.dataset.at + 60;
      $('#obsT', el).value = String(Math.max(0, Math.round((to - t0) / 60)));
      setT(to);
    }
    const p = pathAt(t, b.dataset.at ? +b.dataset.at + 60 : tsel);
    if (p && p[1] > 0) fly({ ra: 360 - p[0], dec: Math.min(85, p[1]), fov: Math.min(v.fov, 100), raw: true });
    select({ type: 'track', o: t });
  });
  const find = $('#obsFind', el);
  find.addEventListener('change', () => {
    const t = find.value.trim().toLowerCase();
    if (!t) return;
    const p = atlas.photos.find(x => x.name.toLowerCase() === t) || atlas.photos.find(x => x.name.toLowerCase().includes(t));
    const o = atlas.catalog.find(x => x.name.toLowerCase() === t || x.id.toLowerCase() === t.replace(/\s+/g, ''))
      || atlas.catalog.find(x => x.name.toLowerCase().includes(t));
    if (p && (!o || p.name.toLowerCase() === t)) flyPhoto(p);
    else if (o) { fly({ ra: o.ra, dec: o.dec, fov: Math.max(1.5, (o.size || 10) / 60 * 4) }); select({ type: 'cat', o }); }
    else toast('Nicht gefunden.', '');
    find.blur();
  });

  const ro = new ResizeObserver(size);
  ro.observe(stage);
  const onTheme = new MutationObserver(() => { readColors(); draw(); });
  onTheme.observe(document.documentElement, { attributes: true, attributeFilter: ['data-look', 'data-theme', 'style'] });
  size();
  if (start) fly(start);

  // ---- Daten ----
  const msg = (t) => { const m = $('#obsMsg', el); if (m) { m.hidden = !t; m.textContent = t || ''; } };
  skyData().then(s => { if (el.isConnected) crossfade(() => { sky = s; }); }).catch(() => msg('Sterndaten konnten nicht geladen werden.'));
  // Einstieg aus Sage: passendes Katalogobjekt an der Marke gleich auswählen (sonst Auswahl leeren)
  const focusTarget = () => {
    let best = null, bd = Infinity;
    for (const o of target ? atlas.catalog : []) {
      const lim = Math.max(0.3, (o.size || 0) / 120);              // Mittelpunkte weichen je nach Katalog ab (M45: SIMBAD ↔ OpenNGC)
      if (Math.abs(o.dec - target.dec) > lim) continue;
      const d = angSep(o.ra, o.dec, target.ra, target.dec);
      if (d < lim && d / lim < bd) { best = o; bd = d / lim; }
    }
    if (target) target.matched = !!best;
    // schmale Fenster: die Schublade nicht gleich über die Karte legen (Details über den Panel-Knopf)
    if (best) select({ type: 'cat', o: best }, matchMedia('(min-width: 1280px)').matches); else if (sel) { sel = null; AF.ctx.clear(); }
  };
  // Atlasdaten getrennt laden: die Ansicht (mit destroy) kommt sofort zurück, auch wenn man vor der Antwort weiternavigiert
  (async () => {
    let a = atlas;
    try {
      a = await api('/stacking/atlas');
      const pl = (await api('/stacking/plan').catch(() => null))?.plan;
      if (pl?.targets?.length) {
        const first = (id) => pl.nights.find(n => n.blocks.some(b => b.target === id))?.night;
        planned = pl.targets.map(t => ({ ra: t.ra, dec: t.dec, name: t.name, color: window.AFplan?.color(t.id) || col.acc,
          when: new Date(first(t.id) + 'T12:00:00Z').toLocaleDateString('de-DE', { weekday: 'short', timeZone: 'UTC' }) }));
        const b = $('[data-layer="plan"]', el); if (b) b.hidden = false;
      }
    } catch (e) { msg(e.message); }
    if (!el.isConnected) return;
    crossfade(() => { atlas = a; });
    const strip = $('#obsStrip', el);
    $('#obsCount', el).textContent = String(atlas.photos.length);
    strip.classList.add('stagger');
    strip.innerHTML = atlas.photos.length
      ? atlas.photos.slice().sort((a, b) => (b.created || 0) - (a.created || 0)).map((p, i) => `<button type="button" class="obs-shot" style="--i:${i}" data-pid="${p.pid}" title="${esc(p.name)} am Himmel zeigen"><img src="${esc(p.thumb)}" alt="" loading="lazy"><span>${esc(p.name)}</span></button>`).join('')
      : `<p class="mut sm">Noch keine Bilder mit bekannter Himmelsposition. Sobald ein Stack oder importiertes Bild per Plate-Solving gelöst ist, erscheint es hier.</p>`;
    strip.onclick = (e) => { const b = e.target.closest('[data-pid]'); const p = b && atlas.photos.find(x => x.pid === +b.dataset.pid); if (p) flyPhoto(p); };
    $('#obsList', el).innerHTML = [...new Set([...atlas.photos.map(p => p.name), ...atlas.catalog.filter(o => o.src !== 'a').map(o => o.name)])]
      .map(n => `<option value="${esc(n)}">`).join('');
    if (target) focusTarget();
    draw();
  })();

  return {
    navigate(path) {
      const m = path.match(/^\/observatorium(?:\?(.*))?$/);
      if (!m) return false;
      const qs = new URLSearchParams(m[1] || '');
      const nd = (qs.get('date') || '').slice(0, 10);
      if (nd !== pathDate) {                                        // andere Nacht (oder wieder heute): Bahnen neu
        pathDate = nd; pathsP = null; paths = null; freeSig = '';
        tsel = tShow = Date.now() / 1000;
        if (sel?.type === 'track') { sel = null; AF.ctx.clear(); }
        freeList();
        if (mode === 'here') loadPaths(); else { const b = $('[data-layer="tracks"]', el); if (b) b.hidden = true; }
      }
      if (qs.get('blick') === 'hier' && site) {                     // „Bahnen im Atlas“ bei schon offenem Atlas
        if (!on.tracks) { on.tracks = true; $('[data-layer="tracks"]', el)?.classList.add('on'); }
        setMode('here');
      }
      const to = fromQuery(qs);
      if (to) fly(to); else target = null;
      focusTarget(); draw();
      return true;
    },
    destroy() {
      ro.disconnect(); onTheme.disconnect(); cancelAnimationFrame(anim); cancelAnimationFrame(raf); cancelAnimationFrame(tAnim);
      AF.ctx.body()?.removeEventListener('click', onCtxClick);
    },
  };
}

const m = AF.modules.stacking;
m.nav.push({ href: '#/observatorium', label: 'Observatorium', icon: 'dome', key: 'observatory', parent: 'home' });
m.routes.push([/^\/observatorium(?:\?(.*))?$/, 'observatory', atlasView]);
window.AFobs = { projection, angSep, photoOrder };
})();
