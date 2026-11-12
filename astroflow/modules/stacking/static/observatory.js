'use strict';
/* Observatorium, Etappe 1: Himmelsatlas #/observatorium[?ra=&dec=&fov=&name=] – ganzer Himmel, zoombar (stereografische
   Projektion auf Canvas): Sterne bis 6,5 mag (Yale BSC), Sternbildlinien (d3-celestial), Deep-Sky-Katalog wie „Heute Nacht“
   und die eigenen Bilder an ihrer echten Lage (vier Ecken aus der Plate-Solving-Lösung, /api/stacking/atlas).
   Klick → Kontextpanel mit Projekt bzw. Sage. Sage-Objektseiten springen mit ?ra=&dec=&name= hierher. */
(() => {
const D2R = Math.PI / 180, FOV_MIN = 0.3, FOV_MAX = 220, FOV_HOME = 120;
const KEY = 'obs.view', LKEY = 'obs.layers';
const LAYERS = [['photos', 'Meine Fotos', 'image'], ['catalog', 'Katalog', 'target'], ['cons', 'Sternbilder', 'star'], ['grid', 'Gitter', 'grid'], ['plan', 'Plan', 'list']];
const de = (v, n = 1) => Number(v).toFixed(n).replace('.', ',');
const reduced = () => matchMedia('(prefers-reduced-motion: reduce)').matches;
const norm = (ra) => ((ra % 360) + 360) % 360;

let skyP = null;
const skyData = () => skyP || (skyP = Promise.all(['sterne', 'sternbilder'].map(n =>
  fetch(`/m/stacking/himmel/${n}.json?v=${AF.app?.build || AF.app?.version || ''}`).then(r => { if (!r.ok) throw new Error('Himmelsdaten fehlen'); return r.json(); })))
  .then(([s, c]) => ({ stars: s.stars, cons: c.items })).catch(e => { skyP = null; throw e; }));

// Stereografische Projektion um (ra0, dec0); Osten links, Norden oben. S = Pixel je Bogenmaß in der Mitte.
function projection(v, W, H) {
  const d0 = v.dec * D2R, sd0 = Math.sin(d0), cd0 = Math.cos(d0);
  const S = Math.min(W, H) / (4 * Math.tan(v.fov * D2R / 4));
  const fwd = (ra, dec) => {
    const d = dec * D2R, a = (ra - v.ra) * D2R, sd = Math.sin(d), cd = Math.cos(d), ca = Math.cos(a);
    const cc = sd0 * sd + cd0 * cd * ca;
    if (cc < -0.6) return null;
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
  if (start) Object.assign(v, start);
  const on = { photos: true, catalog: true, cons: true, grid: true, plan: true, ...store.get(LKEY, {}) };
  let planned = [];                         // Ziele des gespeicherten Plans (Planer): {ra, dec, name, when}
  const sage = AF.on('sage');

  el.innerHTML = `<div class="pagehead"><div><h1>Observatorium</h1><p class="mut">Dein Himmel – mit allen eigenen Aufnahmen an ihrem Platz.</p></div>${window.AFplan?.tabs('atlas') || ''}</div>
    <div class="obs-stage" id="obsStage">
      <canvas id="obsCanvas" aria-label="Himmelskarte" role="img" tabindex="0"></canvas>
      <div class="obs-top">
        <label class="obs-search">${icon('search')}<input id="obsFind" type="search" list="obsList" placeholder="Objekt suchen" autocomplete="off" aria-label="Objekt suchen"><datalist id="obsList"></datalist></label>
        <div class="seg obs-layers" role="group" aria-label="Ebenen">${LAYERS.map(([k, l, i]) => `<button type="button" data-layer="${k}" aria-pressed="${on[k]}" class="${on[k] ? 'on' : ''}" title="${l}"${k === 'plan' ? ' hidden' : ''}>${icon(i)}<span>${l}</span></button>`).join('')}</div>
      </div>
      <div class="obs-zoom" role="group" aria-label="Zoom"><button type="button" data-z="in" title="Vergrößern" aria-label="Vergrößern">${icon('plus')}</button><button type="button" data-z="out" title="Verkleinern" aria-label="Verkleinern">${icon('minus')}</button><button type="button" data-z="home" title="Ganzer Himmel" aria-label="Ganzer Himmel">${icon('globe')}</button></div>
      <div class="obs-read tnum" id="obsRead" aria-live="off"></div>
      <div class="obs-msg" id="obsMsg" hidden></div>
    </div>
    <section class="obs-strip-wrap"><div class="panel-h"><h2>${icon('image')}Meine Fotos am Himmel</h2><span class="grow"></span><span class="chip" id="obsCount">…</span></div><div class="obs-strip" id="obsStrip"></div></section>`;

  const stage = $('#obsStage', el), cv = $('#obsCanvas', el), g = cv.getContext('2d');
  let W = 0, H = 0, P = null, sky = null, atlas = { photos: [], catalog: [] }, sel = null, hover = null, hits = [], raf = 0, anim = 0;
  const imgs = new Map();
  let col = {};
  const readColors = () => {
    const cs = getComputedStyle(document.documentElement), c = (n) => cs.getPropertyValue(n).trim();
    col = { bg: c('--bg'), surface: c('--surface'), border: c('--border'), fg: c('--fg'), mut: c('--mut'), dim: c('--dim'), acc: c('--acc'), cat: c('--data-2'), warn: c('--warn'), font: getComputedStyle(document.body).fontFamily };
  };
  readColors();

  const draw = () => { if (!raf) raf = requestAnimationFrame(paint); };
  const save = () => store.set(KEY, { ra: +v.ra.toFixed(3), dec: +v.dec.toFixed(3), fov: +v.fov.toFixed(3) });

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
    P = projection(v, W, H);
    const { fwd, S, reach } = P;
    hits = [];
    // Himmel
    const grd = g.createRadialGradient(W / 2, H / 2, 0, W / 2, H / 2, Math.hypot(W, H) / 2);
    grd.addColorStop(0, col.surface); grd.addColorStop(1, col.bg);
    g.fillStyle = grd; g.fillRect(0, 0, W, H);
    g.font = '500 11px ' + col.font;
    if (on.grid) grid(P);
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
    $('#obsRead', el).textContent = `${fmtRA(v.ra)} · ${fmtDec(v.dec)} · Feld ${fmtFov(v.fov)}`;
    void S; void reach;
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
    sel = h ? { type: h.type, o: h.o } : null;
    draw();
    if (!sel) { AF.ctx.clear(); return; }
    const o = sel.o;
    if (sel.type === 'photo') {
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
  function zoomAt(f, X = W / 2, Y = H / 2) {
    const before = P.inv(X, Y);
    v.fov = clampFov(v.fov * f);
    const P2 = projection(v, W, H), after = P2.inv(X, Y);
    v.ra = norm(v.ra + (before[0] - after[0] + 540) % 360 - 180);
    v.dec = Math.max(-90, Math.min(90, v.dec + before[1] - after[1]));
    save(); draw();
  }
  function fly(to) {
    cancelAnimationFrame(anim);
    const from = { ...v }, dra = ((to.ra - from.ra + 540) % 360) - 180;
    if (reduced()) { Object.assign(v, { ra: norm(to.ra), dec: to.dec, fov: clampFov(to.fov) }); save(); draw(); return; }
    const t0 = performance.now(), T = 700, lf0 = Math.log(from.fov), lf1 = Math.log(clampFov(to.fov));
    const step = (t) => {
      const k = Math.min(1, (t - t0) / T), e = k < 0.5 ? 2 * k * k : 1 - (-2 * k + 2) ** 2 / 2;
      v.ra = norm(from.ra + dra * e); v.dec = from.dec + (to.dec - from.dec) * e; v.fov = Math.exp(lf0 + (lf1 - lf0) * e);
      paint();
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
  cv.addEventListener('dblclick', (e) => zoomAt(1 / 2, e.offsetX, e.offsetY));
  cv.addEventListener('keydown', (e) => {
    const s = v.fov / 10, k = e.key;
    if (k === '+' || k === '=') zoomAt(1 / 1.5);
    else if (k === '-') zoomAt(1.5);
    else if (k === 'ArrowLeft') { v.ra = norm(v.ra + s / Math.max(0.1, Math.cos(v.dec * D2R))); save(); draw(); }
    else if (k === 'ArrowRight') { v.ra = norm(v.ra - s / Math.max(0.1, Math.cos(v.dec * D2R))); save(); draw(); }
    else if (k === 'ArrowUp') { v.dec = Math.min(90, v.dec + s); save(); draw(); }
    else if (k === 'ArrowDown') { v.dec = Math.max(-90, v.dec - s); save(); draw(); }
    else if (k === 'Escape') select(null);
    else return;
    e.preventDefault();
  });
  $('.obs-zoom', el).onclick = (e) => {
    const z = e.target.closest('[data-z]')?.dataset.z;
    if (z === 'in') zoomAt(1 / 1.6); else if (z === 'out') zoomAt(1.6); else if (z === 'home') fly({ ra: v.ra, dec: v.dec, fov: FOV_HOME });
  };
  $('.obs-layers', el).onclick = (e) => {
    const b = e.target.closest('[data-layer]');
    if (!b) return;
    const k = b.dataset.layer;
    on[k] = !on[k]; b.classList.toggle('on', on[k]); b.setAttribute('aria-pressed', on[k]);
    store.set(LKEY, on); draw();
  };
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

  // ---- Daten ----
  const msg = (t) => { const m = $('#obsMsg', el); if (m) { m.hidden = !t; m.textContent = t || ''; } };
  skyData().then(s => { if (el.isConnected) { sky = s; draw(); } }).catch(() => msg('Sterndaten konnten nicht geladen werden.'));
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
    try {
      atlas = await api('/stacking/atlas');
      const pl = (await api('/stacking/plan').catch(() => null))?.plan;
      if (pl?.targets?.length) {
        const first = (id) => pl.nights.find(n => n.blocks.some(b => b.target === id))?.night;
        planned = pl.targets.map(t => ({ ra: t.ra, dec: t.dec, name: t.name, color: window.AFplan?.color(t.id) || col.acc,
          when: new Date(first(t.id) + 'T12:00:00Z').toLocaleDateString('de-DE', { weekday: 'short', timeZone: 'UTC' }) }));
        const b = $('[data-layer="plan"]', el); if (b) b.hidden = false;
      }
    } catch (e) { msg(e.message); }
    if (!el.isConnected) return;
    const strip = $('#obsStrip', el);
    $('#obsCount', el).textContent = String(atlas.photos.length);
    strip.innerHTML = atlas.photos.length
      ? atlas.photos.slice().sort((a, b) => (b.created || 0) - (a.created || 0)).map(p => `<button type="button" class="obs-shot" data-pid="${p.pid}" title="${esc(p.name)} am Himmel zeigen"><img src="${esc(p.thumb)}" alt="" loading="lazy"><span>${esc(p.name)}</span></button>`).join('')
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
      const to = fromQuery(new URLSearchParams(m[1] || ''));
      if (to) fly(to); else target = null;
      focusTarget(); draw();
      return true;
    },
    destroy() {
      ro.disconnect(); onTheme.disconnect(); cancelAnimationFrame(anim); cancelAnimationFrame(raf);
      AF.ctx.body()?.removeEventListener('click', onCtxClick);
    },
  };
}

const m = AF.modules.stacking;
m.nav.push({ href: '#/observatorium', label: 'Observatorium', icon: 'dome', key: 'observatory', parent: 'home' });
m.routes.push([/^\/observatorium(?:\?(.*))?$/, 'observatory', atlasView]);
window.AFobs = { projection, angSep, photoOrder };
})();
