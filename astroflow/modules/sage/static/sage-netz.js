'use strict';
/* Sage – Wissensnetz zeichnen (window.SageNetz.draw). Ohne Kräftesimulation: Überblick = Gruppen (Objektart oder Sternbild)
   als Sonnenblumen-Spiralen, Fokus = Ringe um ein Objekt (Ebene 1, 2, 3 …). Beides O(n), sofort fertig, blockiert nichts.
   Gezeichnet wird in Bildschirmkoordinaten: Beschriftungen bleiben gleich groß und werden nach Wichtigkeit gesetzt,
   überlappende fallen weg (beim Hineinzoomen erscheinen mehr). Ziehen/Mausrad/zwei Finger, Tippen = Infokarte.
   Vorher (bis 0.64): Kräftelayout über alle Paare → Ring aus losen Dreiergruppen + Klumpen, Text winzig. */
(() => {
const K = '/sage/kb';
const GROUP = { galaxy: 'Galaxien', emission: 'Emissionsnebel', reflection: 'Reflexionsnebel', planetary: 'Planetarische Nebel',
  snr: 'Supernova-Überreste', globular: 'Kugelsternhaufen', open_cluster: 'Offene Sternhaufen', star: 'Sterne', dark: 'Dunkelnebel',
  concept: 'Begriffe', object: 'Weitere Objekte', personal: 'Deine Daten' };
const ORIGINS = [['cat', 'Fachkatalog'], ['cur', 'Grundwissen'], ['ai', 'KI-Vorschlag'], ['per', 'deine Daten']];
const lk = (l) => l.status === 'suggested' ? 'ai' : ['personal', 'user', 'ai+user'].includes(l.origin) ? 'per' : l.origin === 'curated' ? 'cur'
  : l.origin === 'ai' ? 'ai' : 'cat';
const reduced = () => matchMedia('(prefers-reduced-motion: reduce)').matches;
const hrs = (h) => h >= 10 ? Math.round(h) + ' h' : String(Math.round(h * 10) / 10).replace('.', ',') + ' h';
let uid = 0;

// ---- Layouts (Weltkoordinaten) ------------------------------------------------------------------------
// Überblick: je Gruppe eine Spirale (wichtigste innen), Gruppen gierig um die größte gepackt (breites Seitenverhältnis bevorzugt)
function clusters(N, keyOf, aspect) {
  const m = new Map();
  for (const n of N) { const k = keyOf(n); if (!m.has(k)) m.set(k, []); m.get(k).push(n); }
  const S = 30;
  const G = [...m].map(([k, ns]) => {
    ns.sort((a, b) => (b.hub - a.hub) || (b.observed - a.observed) || (b.hours - a.hours) || (b.deg - a.deg) || a.label.localeCompare(b.label));
    ns.forEach((n, i) => { const r = S * Math.sqrt(i + (i ? .5 : 0)), a = i * 2.39996; n.lx = Math.cos(a) * r; n.ly = Math.sin(a) * r; });
    return { k, ns, R: S * Math.sqrt(ns.length + .5) + 22, x: 0, y: 0 };
  }).sort((a, b) => b.R - a.R || a.k.localeCompare(b.k));
  const done = [];
  for (const g of G) {
    if (done.length) {
      let best = null;
      for (const p of done) for (let t = 0; t < 48; t++) {
        const a = t / 48 * 2 * Math.PI, d = p.R + g.R + 26, x = p.x + Math.cos(a) * d, y = p.y + Math.sin(a) * d;
        if (done.some(q => Math.hypot(x - q.x, y - q.y) < q.R + g.R + 24)) continue;
        const c = (x / aspect) ** 2 + y * y;
        if (!best || c < best.c) best = { x, y, c };
      }
      g.x = best.x; g.y = best.y;
    }
    done.push(g);
    for (const n of g.ns) { n.x = g.x + n.lx; n.y = g.y + n.ly; n.grp = g; }
  }
  return G;
}

// Fokus: Breitensuche vom Zentrum, jeder Knoten bekommt einen Winkelbereich nach der Zahl seiner Nachfahren
function rings(N, L, center) {
  const by = new Map(N.map(n => [n.id, n])), adj = new Map(N.map(n => [n, []]));
  for (const l of L) { adj.get(l.s).push(l.t); adj.get(l.t).push(l.s); }
  const root = by.get(center) || N[0], seen = new Set([root]), order = [root];
  root.depth = 0; root.kids = [];
  // ebenenweise; Begriffe je Ebene zuletzt aufklappen, damit Objekte an echten Nachbarn hängen statt an „Galaxie“
  for (let lvl = [root]; lvl.length;) {
    const nxt = [];
    for (const p of [...lvl].sort((a, b) => (a.kind === 'concept') - (b.kind === 'concept'))) {
      const kids = adj.get(p).filter(q => !seen.has(q)).sort((a, b) => (a.group || '').localeCompare(b.group || '') || a.label.localeCompare(b.label));
      for (const q of kids) { seen.add(q); q.depth = p.depth + 1; q.kids = []; q.par = p; p.kids.push(q); nxt.push(q); }
    }
    order.push(...nxt); lvl = nxt;
  }
  for (const n of [...order].reverse()) n.w = n.kids.length ? n.kids.reduce((s, k) => s + k.w, 0) : 1;
  const per = []; order.forEach(n => { per[n.depth] = (per[n.depth] || 0) + 1; });
  const R = [0];
  for (let d = 1; d < per.length; d++) R[d] = Math.max(R[d - 1] + (d === 1 ? 150 : 115), per[d] * 30 / (2 * Math.PI));
  const put = (n, a0, a1) => {
    const a = (a0 + a1) / 2; n.x = Math.cos(a) * R[n.depth]; n.y = Math.sin(a) * R[n.depth];
    let s = a0; for (const k of n.kids) { const e = s + (a1 - a0) * k.w / n.w; put(k, s, e); s = e; }
  };
  put(root, -Math.PI / 2, 1.5 * Math.PI);
  const lone = N.filter(n => !seen.has(n));                  // ohne Weg zum Zentrum: gesammelt rechts daneben statt verstreut
  const x0 = (R[R.length - 1] || 0) + 140;
  lone.forEach((n, i) => { const r = 30 * Math.sqrt(i + .5), a = i * 2.39996; n.x = x0 + Math.cos(a) * r; n.y = Math.sin(a) * r; });
  return lone.length ? [{ k: 'lone', label: 'ohne Verbindung', ns: lone, R: 30 * Math.sqrt(lone.length + .5) + 22, x: x0, y: 0 }] : [];
}

// ---- Zeichnen ------------------------------------------------------------------------------------------
function draw(host, g, opts = {}) {
  const center = opts.center || null;
  let mode = opts.mode || 'art';
  const ALL = g.nodes.map(n => ({ ...n, hours: n.hours || 0, nights: n.nights || 0, deg: 0 }));
  const by = new Map(ALL.map(n => [n.id, n]));
  let LA = g.links.filter(l => by.has(l.source) && by.has(l.target) && l.source !== l.target).map(l => ({ ...l, s: by.get(l.source), t: by.get(l.target), c: lk(l) }));
  LA.forEach(l => { l.s.deg++; l.t.deg++; });
  let N = ALL, L = LA;                                       // gerade gezeichnet (nach Sternbild ohne Begriffe)
  for (const n of ALL) n.r = n.id === center ? 15 : n.kind === 'object' ? (n.observed ? Math.min(17, 7 + 2.6 * Math.sqrt(n.hours)) : 5.5)
    : n.kind === 'concept' ? 8 : 6;
  const hidden = new Set();
  const cid = 'nzc' + (++uid);
  host.innerHTML = `<div class="nz"><svg class="nz-svg" role="img" aria-label="Wissensnetz"><defs><clipPath id="${cid}" clipPathUnits="objectBoundingBox"><circle cx=".5" cy=".5" r=".5"/></clipPath></defs>
      <g class="nz-hulls"></g><g class="nz-edges"></g><g class="nz-nodes"></g><g class="nz-labels"></g></svg>
    <div class="nz-legend"></div>
    <div class="nz-zoom"><button type="button" class="btn ghost icon" data-z="in" title="Vergrößern" aria-label="Vergrößern">${icon('plus')}</button>
      <button type="button" class="btn ghost icon" data-z="out" title="Verkleinern" aria-label="Verkleinern">${icon('minus')}</button>
      <button type="button" class="btn ghost icon" data-z="fit" title="Alles zeigen" aria-label="Alles zeigen">${icon('fit')}</button></div></div>`;
  const svg = host.querySelector('svg'), [gH, gE, gN, gT] = ['.nz-hulls', '.nz-edges', '.nz-nodes', '.nz-labels'].map(s => svg.querySelector(s));
  let W = host.clientWidth || 800, H = host.clientHeight || 500, groups = [];
  // Legende = Filter: Linienart antippen blendet sie aus; Zahl = Linien dieser Herkunft
  const legend = () => { host.querySelector('.nz-legend').innerHTML = ORIGINS.map(([k, t]) => { const c = L.filter(l => l.c === k).length;
    return c ? `<button type="button" class="nz-lg" data-o="${k}" aria-pressed="${!hidden.has(k)}"><i class="l-${k}"></i>${t}<span class="dim">${c}</span></button>` : ''; }).join(''); };
  legend();

  const build = () => {
    const bild = mode === 'bild' && !center;
    // nach Sternbild ohne Begriffe: „ist eine Galaxie“ verbände sonst jedes Sternbild mit der Mitte
    N = bild ? ALL.filter(n => n.kind !== 'concept') : ALL;
    const on = new Set(N); L = LA.filter(l => on.has(l.s) && on.has(l.t));
    legend();
    const base = bild ? (n => n.constellation || '~')
      : (n => n.kind === 'concept' ? 'concept' : GROUP[n.group] && n.group !== 'personal' ? n.group : 'object');
    // Knoten ohne eigene Gruppe in die Gruppe, zu der sie überwiegend gehören: Begriffe („Galaxie“ zu den Galaxien), nach Sternbild
    // Katalog-Gruppen ohne Sternbild (z. B. M81-Gruppe zu Ursa Major) – sonst ein Linienfächer übers ganze Bild
    const home = new Map();
    for (const n of N) if (bild ? !n.constellation : n.kind === 'concept') {
      const cnt = {}; let tot = 0;
      for (const l of L) { const o = l.s === n ? l.t : l.t === n ? l.s : null; if (o && o.kind !== 'concept' && base(o) !== '~') { cnt[base(o)] = (cnt[base(o)] || 0) + 1; tot++; } }
      const [k, c] = Object.entries(cnt).sort((a, b) => b[1] - a[1])[0] || [];
      if (k && c >= 2 && c / tot >= .5) home.set(n, k);
    }
    N.forEach(n => { n.hub = home.has(n); });
    groups = center ? rings(N, L, center) : clusters(N, n => home.get(n) || base(n), W / H);
    gH.innerHTML = groups.map((gr, i) => { const k = center || bild ? 'x' : gr.k;
      const t = gr.label || (bild ? (gr.k === '~' ? 'ohne Sternbild' : gr.k) : GROUP[gr.k] || gr.k);
      return `<g class="nz-hull k-${esc(k)}" data-g="${i}"><circle/><text>${esc(t)}<tspan class="nz-n"> ${gr.ns.length}</tspan></text></g>`; }).join('');
    // im Fokus: Linien entlang der Ringe kräftig, Querverbindungen zwischen Ästen nur angedeutet
    gE.innerHTML = L.map((l, i) => `<path class="l-${l.c}${center && l.s.par !== l.t && l.t.par !== l.s ? ' x' : ''}" data-l="${i}"/>`).join('');
    gN.innerHTML = N.map((n, i) => `<g class="nn k-${esc(n.kind === 'object' ? n.group : n.kind === 'concept' ? 'concept' : 'personal')} ${n.observed ? 'obs' : ''} ${n.kind} ${n.id === center ? 'ctr' : ''}"
      data-i="${i}" style="--d:${Math.min(i * 6, 500)}ms"><circle/>${n.image ? `<image clip-path="url(#${cid})" preserveAspectRatio="xMidYMid slice"/>` : ''}</g>`).join('');
    gT.innerHTML = N.map(n => `<text>${esc(n.label.length > 26 ? n.label.slice(0, 25) + '…' : n.label)}</text>`).join('');
    N.forEach((n, i) => { n.el = gN.children[i]; n.c = n.el.firstElementChild; n.img = n.el.querySelector('image'); n.tx = gT.children[i]; n.pr = -1; n.tw = 0; });
    L.forEach((l, i) => { l.el = gE.children[i]; });
    groups.forEach((gr, i) => { gr.el = gH.children[i]; gr.c = gr.el.firstElementChild; gr.tx = gr.el.lastElementChild; });
  };

  // Ansicht: Bildschirm = Welt × sc + (tx, ty)
  let sc = 1, tx = W / 2, ty = H / 2, minSc = .1, sel = null, hov = null, raf = 0, anim = 0;
  const fitView = () => {
    const xs = [], ys = [];
    for (const n of N) { xs.push(n.x - 40, n.x + 40); ys.push(n.y - 30, n.y + 40); }
    for (const gr of groups) { xs.push(gr.x - gr.R, gr.x + gr.R); ys.push(gr.y - gr.R - 24, gr.y + gr.R); }
    const x0 = Math.min(...xs), x1 = Math.max(...xs), y0 = Math.min(...ys), y1 = Math.max(...ys);
    const s = Math.min(1.6, (W - 24) / (x1 - x0 || 1), (H - 56) / (y1 - y0 || 1));
    minSc = Math.min(.5, s * .6);
    return { sc: s, tx: W / 2 - (x0 + x1) / 2 * s, ty: (H - 36) / 2 - (y0 + y1) / 2 * s };
  };
  const go = (v, ms = 320) => {
    cancelAnimationFrame(anim);
    if (reduced() || !ms) { ({ sc, tx, ty } = v); paint(); return; }
    const a = { sc, tx, ty }, t0 = performance.now();
    const step = (t) => { const p = Math.min(1, (t - t0) / ms), e = 1 - (1 - p) ** 3;
      sc = a.sc + (v.sc - a.sc) * e; tx = a.tx + (v.tx - a.tx) * e; ty = a.ty + (v.ty - a.ty) * e; paint(); if (p < 1) anim = requestAnimationFrame(step); };
    anim = requestAnimationFrame(step);
  };
  const later = () => { if (!raf) raf = requestAnimationFrame(() => { raf = 0; paint(); }); };
  const fs = parseFloat(getComputedStyle(gT).fontSize) || 12;

  function paint() {
    const k = Math.sqrt(sc), cx = tx, cy = ty;          // Weltursprung = Mitte der größten Gruppe
    const nb = new Set(), on = new Set(), foc = sel || hov;
    if (foc) { nb.add(foc); L.forEach(l => { if (!hidden.has(l.c) && (l.s === foc || l.t === foc)) { nb.add(l.s); nb.add(l.t); on.add(l); } }); }
    svg.classList.toggle('dim', !!foc);
    for (const n of N) {
      n.sx = n.x * sc + tx; n.sy = n.y * sc + ty;
      const r = Math.max(3, Math.min(30, n.r * k));
      n.el.setAttribute('transform', `translate(${n.sx.toFixed(1)},${n.sy.toFixed(1)})`);
      if (r !== n.pr) {
        n.pr = r; n.c.setAttribute('r', r.toFixed(1));
        if (n.img) { const big = r >= 11; n.img.style.display = big ? '' : 'none';
          if (big) { if (!n.img.getAttribute('href')) n.img.setAttribute('href', n.image); const q = (r - 1.5).toFixed(1); n.img.setAttribute('x', -q); n.img.setAttribute('y', -q); n.img.setAttribute('width', q * 2); n.img.setAttribute('height', q * 2); } }
      }
      n.el.classList.toggle('on', nb.has(n)); n.el.classList.toggle('sel', n === sel);
    }
    for (const l of L) {
      const off = hidden.has(l.c); l.el.style.display = off ? 'none' : ''; if (off) continue;
      const a = l.s, b = l.t;
      if (!center && a.grp !== b.grp) {          // zwischen Gruppen: leicht zur Mitte gebogen, bündelt sich statt zu kreuzen
        const mx = (a.sx + b.sx) / 2, my = (a.sy + b.sy) / 2, qx = mx + (cx - mx) * .3, qy = my + (cy - my) * .3;
        l.el.setAttribute('d', `M${a.sx.toFixed(1)},${a.sy.toFixed(1)}Q${qx.toFixed(1)},${qy.toFixed(1)} ${b.sx.toFixed(1)},${b.sy.toFixed(1)}`);
      } else l.el.setAttribute('d', `M${a.sx.toFixed(1)},${a.sy.toFixed(1)}L${b.sx.toFixed(1)},${b.sy.toFixed(1)}`);
      l.el.classList.toggle('on', on.has(l));
    }
    const boxes = [];
    for (const gr of groups) {
      const x = gr.x * sc + tx, y = gr.y * sc + ty, R = gr.R * sc;
      gr.c.setAttribute('cx', x); gr.c.setAttribute('cy', y); gr.c.setAttribute('r', Math.max(0, R));
      // Gruppennamen ebenfalls ohne Überlappung (größte Gruppen zuerst) – auf dem Handy liegen sonst mehrere übereinander;
      // am Rand ins Bild geschoben statt abgeschnitten
      gr.tw ||= gr.tx.textContent.length * fs * .62;
      const lx = Math.min(Math.max(x, gr.tw / 2 + 4), W - gr.tw / 2 - 4);
      gr.tx.setAttribute('x', lx); gr.tx.setAttribute('y', y - R - 8);
      const b = [lx - gr.tw / 2 - 3, y - R - 8 - fs * 1.1, lx + gr.tw / 2 + 3, y - R - 4];
      const ok = !boxes.some(q => b[0] < q[2] && b[2] > q[0] && b[1] < q[3] && b[3] > q[1]);
      gr.tx.style.display = ok ? '' : 'none';
      if (ok) boxes.push(b);
    }
    // Beschriftungen: wichtigste zuerst, nur ohne Überlappung und im Bild
    const pri = (n) => (n === sel ? 1e9 : 0) + (n === hov ? 1e8 : 0) + (nb.has(n) ? 1e7 : 0) + (n.id === center ? 1e6 : 0)
      + (n.observed ? 1e4 : 0) + n.hours * 50 + n.deg * 20 + n.pr;
    const order = [...N].sort((a, b) => pri(b) - pri(a));
    for (const n of order) {
      const must = n === sel || n === hov || n.id === center;
      if (foc && !nb.has(n) && !must) { n.tx.style.display = 'none'; continue; }
      if (!n.tw) n.tw = n.tx.textContent.length * fs * .56;
      const x = n.sx, y = n.sy + n.pr + fs + 1, b = [x - n.tw / 2 - 2, y - fs, x + n.tw / 2 + 2, y + 3];
      const ok = must || (n.pr >= 4.5 && b[0] >= 0 && b[2] <= W && b[1] >= 0 && b[3] <= H && !boxes.some(q => b[0] < q[2] && b[2] > q[0] && b[1] < q[3] && b[3] > q[1]));
      n.tx.style.display = ok ? '' : 'none';
      if (ok) { n.tx.setAttribute('x', x.toFixed(1)); n.tx.setAttribute('y', y.toFixed(1)); n.tx.classList.toggle('sel', n === sel); boxes.push(b); }
    }
  }

  // ---- Infokarte -------------------------------------------------------------------------------------
  const float = (html, ev) => window.SageWiki?.float(html, ev, 'nz-card');
  function cardHtml(n) {
    const meta = [n.type || GROUP[n.group], n.constellation, n.nights ? `${n.nights} ${n.nights === 1 ? 'Nacht' : 'Nächte'} · ${hrs(n.hours)}` : n.images ? `${n.images} ${n.images === 1 ? 'Bild' : 'Bilder'}` : ''].filter(Boolean);
    const ls = L.filter(l => l.s === n || l.t === n).sort((a, b) => (b.c === 'ai') - (a.c === 'ai'));
    const row = (l) => { const o = l.s === n ? l.t : l.s;
      return `<div class="nz-row" data-l="${L.indexOf(l)}"><i class="l-${l.c}"></i><button type="button" class="nz-go" data-go="${N.indexOf(o)}">${esc(o.label)} <span class="mut sm">${esc(l.rel)}</span></button>
        ${l.status === 'suggested' && l.id ? `<button type="button" class="btn ghost icon nz-ok" data-a="verified" title="Bestätigen" aria-label="Bestätigen">${icon('check')}</button><button type="button" class="btn ghost icon" data-a="rejected" title="Verwerfen" aria-label="Verwerfen">${icon('x')}</button>` : ''}</div>`; };
    const ent = n.kind === 'object' || n.kind === 'concept';
    return `<div class="nz-ch">${n.image ? `<img src="${esc(n.image)}" alt="">` : `<span class="nz-dot k-${esc(n.kind === 'object' ? n.group : n.kind === 'concept' ? 'concept' : 'personal')}"></span>`}
        <div style="min-width:0"><b>${esc(n.label)}</b>${meta.length ? `<div class="mut sm">${esc(meta.join(' · '))}</div>` : ''}</div></div>
      ${ls.length ? `<div class="nz-rows">${ls.slice(0, 12).map(row).join('')}${ls.length > 12 ? `<div class="dim sm">+ ${ls.length - 12} weitere</div>` : ''}</div>` : ''}
      <div class="row nz-act">${ent ? `<a class="btn sm" href="#/sage/o/${encodeURIComponent(n.id)}">${icon('book')}Objektseite</a>` : n.href ? `<a class="btn sm" href="${esc(n.href)}">${icon('arrow')}Öffnen</a>` : ''}
        ${ent && n.id !== center ? `<a class="btn ghost sm" href="#/sage/netz/${encodeURIComponent(n.id)}">${icon('orbit')}Netz darum</a>` : ''}</div>`;
  }
  function select(n, ev, move = false) {
    sel = n; paint();
    if (!n) { window.SageWiki?.closeFloat(); return; }
    const narrow = W < 600, fy = narrow ? .22 : .42;          // schmal: Punkt nach oben, Karte darunter (verdeckt ihn sonst)
    if (move || narrow) { const s = Math.max(sc, 1); go({ sc: s, tx: W / 2 - n.x * s, ty: H * fy - n.y * s }); }
    const r = svg.getBoundingClientRect();
    const at = narrow ? { clientX: 0, clientY: r.top + H * fy + 40 } : ev?.clientX != null ? ev : { clientX: r.left + (move ? W / 2 : n.sx), clientY: r.top + (move ? H * fy : n.sy) };
    const el = float(cardHtml(n), at);
    if (!el) return;
    el.addEventListener('click', async (e) => {
      const go2 = e.target.closest('[data-go]');
      if (go2) { select(N[+go2.dataset.go], null, true); return; }
      const b = e.target.closest('[data-a]'); if (!b) return;
      const l = L[+b.closest('[data-l]').dataset.l];
      try {
        await send(`${K}/edges/${l.id}`, { status: b.dataset.a }, 'PATCH');
        if (b.dataset.a === 'rejected') { l.el.remove(); L = L.filter(x => x !== l); LA = LA.filter(x => x !== l); }
        else { l.status = 'verified'; l.origin = 'ai+user'; l.c = 'per'; l.el.setAttribute('class', 'l-per'); }
        toast(b.dataset.a === 'rejected' ? 'Vorschlag verworfen.' : 'Verbindung bestätigt.', 'ok');
        legend(); select(n, at); opts.onChange?.();
      } catch (err) { toast(err.message, 'err'); }
    });
  }

  // ---- Bedienung: Ziehen, Mausrad, zwei Finger; Tippen wählt (auch knapp daneben – Finger) ----------------
  const pt = (ev) => { const r = svg.getBoundingClientRect(); return [ev.clientX - r.left, ev.clientY - r.top]; };
  const nodeAt = (ev) => {
    const hit = ev.target.closest?.('.nn'); if (hit) return N[+hit.dataset.i];
    const [x, y] = pt(ev), tol = ev.pointerType === 'touch' ? 22 : 8; let best = null, bd = Infinity;
    for (const n of N) { const d = Math.hypot(n.sx - x, n.sy - y) - n.pr; if (d < tol && d < bd) { bd = d; best = n; } }
    return best;
  };
  const zoomAt = (x, y, f) => { const ns = Math.min(5, Math.max(minSc, sc * f)); tx = x - (x - tx) * ns / sc; ty = y - (y - ty) * ns / sc; sc = ns; later(); };
  const ptr = new Map(); let gst = null;
  const onDown = (ev) => {
    if (ev.button > 0) return;
    cancelAnimationFrame(anim); svg.setPointerCapture?.(ev.pointerId); ptr.set(ev.pointerId, pt(ev));
    if (ptr.size === 1) gst = { p: pt(ev), tx, ty, moved: false, node: nodeAt(ev) };
    else if (ptr.size === 2) { const [a, b] = [...ptr.values()]; gst = { pinch: Math.hypot(a[0] - b[0], a[1] - b[1]) || 1, sc, moved: true }; }
  };
  const onMove = (ev) => {
    if (!ptr.has(ev.pointerId)) {                  // Maus ohne Taste: Nachbarn hervorheben
      if (ev.pointerType === 'mouse') { const n = nodeAt(ev); if (n !== hov) { hov = n; svg.style.cursor = n ? 'pointer' : ''; later(); } }
      return;
    }
    ptr.set(ev.pointerId, pt(ev));
    if (gst?.pinch && ptr.size === 2) {
      const [a, b] = [...ptr.values()], d = Math.hypot(a[0] - b[0], a[1] - b[1]);
      zoomAt((a[0] + b[0]) / 2, (a[1] + b[1]) / 2, gst.sc * d / gst.pinch / sc);
    } else if (gst && !gst.pinch) {
      const [x, y] = pt(ev);
      if (!gst.moved && Math.hypot(x - gst.p[0], y - gst.p[1]) > 5) gst.moved = true;
      if (gst.moved) { tx = gst.tx + x - gst.p[0]; ty = gst.ty + y - gst.p[1]; later(); }
    }
  };
  const onUp = (ev) => {
    if (!ptr.has(ev.pointerId)) return;
    ptr.delete(ev.pointerId);
    if (gst && !gst.moved && ev.type === 'pointerup') select(gst.node, ev);
    gst = ptr.size === 1 ? { p: [...ptr.values()][0], tx, ty, moved: true } : null;
  };
  const onWheel = (ev) => { ev.preventDefault(); const [x, y] = pt(ev); zoomAt(x, y, Math.exp(-ev.deltaY * (ev.deltaMode ? .05 : .0015))); };
  const onLeave = () => { if (hov) { hov = null; later(); } };
  svg.addEventListener('pointerdown', onDown); svg.addEventListener('pointermove', onMove);
  svg.addEventListener('pointerup', onUp); svg.addEventListener('pointercancel', onUp); svg.addEventListener('pointerleave', onLeave);
  svg.addEventListener('wheel', onWheel, { passive: false });
  host.querySelector('.nz-zoom').onclick = (ev) => { const b = ev.target.closest('[data-z]'); if (!b) return;
    if (b.dataset.z === 'fit') go(fitView()); else { const f = b.dataset.z === 'in' ? 1.5 : 1 / 1.5, ns = Math.min(5, Math.max(minSc, sc * f));
      go({ sc: ns, tx: W / 2 - (W / 2 - tx) * ns / sc, ty: H / 2 - (H / 2 - ty) * ns / sc }, 200); } };
  host.querySelector('.nz-legend').onclick = (ev) => { const b = ev.target.closest('[data-o]'); if (!b) return;
    const o = b.dataset.o; hidden.has(o) ? hidden.delete(o) : hidden.add(o); b.setAttribute('aria-pressed', String(!hidden.has(o))); paint(); };
  const ro = new ResizeObserver(() => { const w = host.clientWidth, h = host.clientHeight; if (!w || !h || (w === W && h === H)) return;
    tx += (w - W) / 2; ty += (h - H) / 2; W = w; H = h; svg.setAttribute('viewBox', `0 0 ${W} ${H}`); paint(); });
  ro.observe(host);

  svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
  build();
  ({ sc, tx, ty } = fitView());
  paint();
  return {
    destroy() { ro.disconnect(); cancelAnimationFrame(anim); cancelAnimationFrame(raf); svg.removeEventListener('wheel', onWheel); if (sel) window.SageWiki?.closeFloat(); },
    find(q) { q = String(q || '').trim().toLowerCase(); if (!q) return null; const c = q.replace(/\s+/g, '');
      return N.find(n => n.label.toLowerCase() === q) || N.find(n => n.label.toLowerCase().replace(/\s+/g, '').startsWith(c)) || N.find(n => n.label.toLowerCase().includes(q)) || null; },
    focus(n) { select(n, null, true); },
    regroup(m) { if (center || m === mode) return; mode = m; sel = null; window.SageWiki?.closeFloat(); build(); go(fitView(), 0); },
    labels: () => N.map(n => n.label),
  };
}

window.SageNetz = { draw };
})();
