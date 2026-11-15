'use strict';
/* Einblicke in die laufende Bearbeitung – passgenau über dem Live-Bild.
   Grundsatz: nichts erfinden. Jeder Einblick (live/<run>-i<n>.json, siehe live_insight.py) enthält echte Zwischenergebnisse des
   Schritts: Hotpixel-Positionen, Glühmodell, Hintergrundmodell mit Stützkacheln, Gaia-Sterne mit gemessener Farbe und
   Ausgleichsgeraden, Histogramme und die angewandte Übertragungskurve, die Sternebene. Koordinaten sind Bruchteile [u, v] des
   zugehörigen Bildes – gezeichnet wird im tatsächlich sichtbaren Bildrechteck (object-fit: contain). Erwartungswerte (1/√N)
   sind als solche beschriftet. Jede Animation läuft einmal (wenige Sekunden) und bleibt dann als Standbild stehen;
   prefers-reduced-motion zeigt sofort das Standbild derselben Daten.
   createInsight(host) → { play(data, src, done), clear(), noise(curve), active, destroy() } */
(() => {
const reduced = () => matchMedia('(prefers-reduced-motion: reduce)').matches;
const clamp = (v, a = 0, b = 1) => Math.max(a, Math.min(b, v));
const seg = (t, a, b) => clamp((t - a) / (b - a));
const ease = x => 1 - (1 - x) ** 3;
const nf = (v, d = 2) => Number(v).toLocaleString('de-DE', { minimumFractionDigits: d, maximumFractionDigits: d });
const DUR = { defects: 5200, hotfix: 4400, background: 6400, color: 6800, stretch: 6400, stars: 5200 };
const PICS = { defects: [], hotfix: ['base'], background: ['before', 'after'], color: ['base'], stretch: ['before', 'after'], stars: ['base', 'stars'] };
const METHOD = { graxpert: 'GraXpert-KI', polynom: 'Polynom', himmel: 'Himmels-Stützpunkte' };

function caption(d) {                              // [Titel, echte Kennzahlen]
  const k = d.kind;
  if (k === 'defects') return ['Sensorfehler', [`${d.n.toLocaleString('de-DE')} Hotpixel`, d.columns?.length ? `${d.columns.length} Spalten` : '',
    d.glow ? `Glühen ${nf(d.glow.min, 0)} … ${nf(d.glow.max, 0)} ADU` : '', d.night ? `Nacht ${d.night}` : ''].filter(Boolean).join(' · ')];
  if (k === 'hotfix') return ['Hotpixel im Stack', `${d.n.toLocaleString('de-DE')} Punkte · R ${d.channels[0]} · G ${d.channels[1]} · B ${d.channels[2]}`];
  if (k === 'background') return [`Hintergrund (${METHOD[d.method] || d.method})`, d.support
    ? `Polynom Grad ${d.support.degree} · ${d.support.used} von ${d.support.total} Stützkacheln` : 'Abgezogenes Modell = vorher − nachher'];
  if (k === 'color') return ['Farbkalibrierung (Gaia)', `${d.n} Sterne · R ×${nf(d.gains[0], 3)} · B ×${nf(d.gains[2], 3)}`];
  if (k === 'stretch') {
    const pass = d.params?.durchgaenge?.length;
    return [`Strecken (${d.method === 'ghs' ? `GHS${pass ? ', ' + pass + ' Durchgänge' : ''}` : 'asinh-Kurve'})`,
      `Median ${nf(d.median_in, 3)} → ${nf(d.median_out, 3)} · ${nf(d.dark_pct, 0)} % der Pixel unter 5 %`];
  }
  if (k === 'stars') return ['Sternentrennung', `${d.how === 'starnet' ? 'StarNet' : 'eigene Trennung'}${d.count ? ' · ' + d.count + ' Sterne vermessen' : ''} · Sternebene`];
  return [k, ''];
}

function createInsight(host) {
  const cv = document.createElement('canvas'), cap = document.createElement('div'), nz = document.createElement('div');
  cv.className = 'sl-ins'; cv.setAttribute('aria-hidden', 'true'); cv.hidden = true;
  cap.className = 'sl-ins-cap'; cap.hidden = true; cap.setAttribute('aria-live', 'polite');
  cap.innerHTML = '<b></b><span></span>';
  nz.className = 'sl-noise'; nz.hidden = true;
  host.append(cv, cap, nz);
  const ctx = cv.getContext('2d');
  const css = getComputedStyle(document.documentElement), col = (v, d) => css.getPropertyValue(v).trim() || d;
  const C = { fg: col('--fg', '#f0f2f5'), mut: col('--mut', '#aab2bd'), acc: col('--acc', '#4c9aff'), ok: col('--ok', '#42b883'),
    bad: col('--bad', '#e16b6b'), warn: col('--warn', '#e5aa52'), panel: col('--sidebar', '#14171c'), bg: col('--bg', '#0f1115'), line: col('--border', '#303640'), d2: col('--data-2', '#8fa6c8'), d3: col('--data-3', '#5fb8a8') };
  let W = 0, H = 0, dpr = 1, rem = 16, D = null, imgs = {}, t0 = 0, raf = 0, dead = false, done = null, heat = null;
  const size = () => {
    const r = host.getBoundingClientRect(); dpr = Math.min(2, devicePixelRatio || 1); W = r.width; H = r.height;
    cv.width = Math.round(W * dpr); cv.height = Math.round(H * dpr); rem = parseFloat(css.fontSize) || 16;
  };
  const ro = new ResizeObserver(() => { size(); if (D && !raf) draw(1e9); });
  ro.observe(host);
  // sichtbares Bildrechteck (wie object-fit: contain) für ein Seitenverhältnis
  function rect(aspect) {
    const s = Math.min(W / aspect, H);
    const w = s * aspect, h = s;
    return { x: (W - w) / 2, y: (H - h) / 2, w, h };
  }
  const at = (R, u, v) => [R.x + u * R.w, R.y + v * R.h];
  function aspect() {
    const i = imgs.base || imgs.before;
    if (i?.naturalWidth) return i.naturalWidth / i.naturalHeight;
    return D.frame ? D.frame.w / D.frame.h : 1.6;
  }
  // Wärmekarte aus einem 0…255-Gitter (Zeilen) als kleines Bild – weich hochskaliert
  function heatImage(grid) {
    const gh = grid.length, gw = grid[0]?.length || 0, c = document.createElement('canvas');
    c.width = gw; c.height = gh;
    const g = c.getContext('2d'), im = g.createImageData(gw, gh);
    const stops = [[18, 26, 70], [76, 154, 255], [229, 170, 82], [255, 240, 210]];
    grid.forEach((row, i) => row.forEach((v, j) => {
      const x = v / 255 * (stops.length - 1), k = Math.min(stops.length - 2, Math.floor(x)), f = x - k, o = (i * gw + j) * 4;
      for (let ch = 0; ch < 3; ch++) im.data[o + ch] = stops[k][ch] + (stops[k + 1][ch] - stops[k][ch]) * f;
      im.data[o + 3] = 255;
    }));
    g.putImageData(im, 0, 0);
    return c;
  }
  // Höhenlinien (Marching Squares) eines Gitters bei level → Strecken in Bruchteilen [u, v]
  function contour(grid, level) {
    const gh = grid.length, gw = grid[0].length, out = [];
    const P = (i, j) => [(j + .5) / gw, (i + .5) / gh];
    const lerp = (a, b, va, vb) => { const f = (level - va) / ((vb - va) || 1e-9); return [a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f]; };
    for (let i = 0; i < gh - 1; i++) for (let j = 0; j < gw - 1; j++) {
      const v = [grid[i][j], grid[i][j + 1], grid[i + 1][j + 1], grid[i + 1][j]], p = [P(i, j), P(i, j + 1), P(i + 1, j + 1), P(i + 1, j)];
      const e = [];
      for (let k = 0; k < 4; k++) { const a = v[k] >= level, b = v[(k + 1) % 4] >= level; if (a !== b) e.push(lerp(p[k], p[(k + 1) % 4], v[k], v[(k + 1) % 4])); }
      if (e.length >= 2) out.push([e[0], e[1]]);
      if (e.length === 4) out.push([e[2], e[3]]);
    }
    return out;
  }
  function picture(img, R, alpha = 1, clipX = null) {
    if (!img) return;
    ctx.save(); ctx.globalAlpha = alpha;
    if (clipX != null) { ctx.beginPath(); ctx.rect(R.x, R.y, clipX - R.x, R.h); ctx.clip(); }
    ctx.drawImage(img, R.x, R.y, R.w, R.h); ctx.restore();
  }
  const small = () => W < 520;                     // schmal (Handy): Diagramm ohne Überschrift, höchstens gut halb so hoch
  function panel(w, h) {                           // Diagrammfläche unten rechts, über der Bildunterschrift
    const pad = .75 * rem, bw = Math.min(w, W - 2 * pad), bh = Math.min(h, H - 5.5 * rem, small() ? H * .5 : 1e9);
    const x = W - bw - pad, y = H - bh - (small() ? 2.4 : 2.9) * rem;
    ctx.fillStyle = C.panel; ctx.strokeStyle = C.line; ctx.lineWidth = 1;   // Fläche/Rahmen aus den Tokens (--sidebar, --border)
    ctx.beginPath(); if (ctx.roundRect) ctx.roundRect(x, y, bw, bh, .375 * rem); else ctx.rect(x, y, bw, bh);
    ctx.save(); ctx.globalAlpha = .9; ctx.fill(); ctx.restore(); ctx.stroke();
    const head = small() ? .5 : 1.5;
    return { x: x + .625 * rem, y: y + head * rem, w: bw - 1.25 * rem, h: bh - (head + 1.1) * rem, top: y + .5 * rem, left: x + .625 * rem };
  }
  function label(text, x, y, color = C.mut, align = 'left', size = .6875) {
    ctx.font = `${size * rem}px ${css.getPropertyValue('--sans') || 'sans-serif'}`; ctx.fillStyle = color; ctx.textAlign = align; ctx.textBaseline = 'top';
    ctx.fillText(text, x, y);
  }
  function sides(R, wx, wipe, before, after) {     // Beschriftung beiderseits der Wischkante (oberes Drittel, frei vom Diagramm)
    if (wipe <= 0 || wipe >= 1) return;
    const y = R.y + R.h * .3;                       // über dem Diagramm unten rechts
    label(after, wx - .4 * rem, y, C.fg, 'right'); label(before, wx + .4 * rem, y, C.fg);
  }
  const panelW = () => Math.min(small() ? W * .5 : W * .38, 18 * rem);
  const bprpColor = c => {                          // Sternfarbe nach BP−RP: blauweiß → weiß → orange
    const x = clamp((c + .2) / 2.4), a = [155, 176, 255], m = [255, 244, 234], b = [255, 166, 81];
    const [p, q, f] = x < .42 ? [a, m, x / .42] : [m, b, (x - .42) / .58];
    return `rgb(${p.map((v, i) => Math.round(v + (q[i] - v) * f)).join(',')})`;
  };

  const S = {
    defects(t) {                                   // Sensorfeld, Glühen als Wärmekarte, Hotpixel blinken an echten Stellen auf
      const R = rect(aspect());
      ctx.fillStyle = C.bg; ctx.fillRect(R.x, R.y, R.w, R.h);
      if (D.glow) {
        heat ||= heatImage(D.glow.grid);
        ctx.save(); ctx.globalAlpha = .7 * ease(seg(t, 0, 1400)); ctx.imageSmoothingEnabled = true; ctx.drawImage(heat, R.x, R.y, R.w, R.h); ctx.restore();
      }
      ctx.strokeStyle = C.line; ctx.strokeRect(R.x + .5, R.y + .5, R.w - 1, R.h - 1);
      const pts = D.points, k = Math.round(pts.length * seg(t, 1000, 4200)), r = Math.max(1.5, .14 * rem);
      for (let i = 0; i < k; i++) {
        const [x, y] = at(R, pts[i][0], pts[i][1]), age = (t - 1000 - i / pts.length * 3200) / 600;
        const glow = age < 1 ? 1 - clamp(age) : 0;
        ctx.fillStyle = C.bad; ctx.globalAlpha = .85; ctx.beginPath(); ctx.arc(x, y, r, 0, 7); ctx.fill();
        if (glow > 0) { ctx.globalAlpha = glow * .8; ctx.strokeStyle = C.warn; ctx.beginPath(); ctx.arc(x, y, r + 7 * (1 - glow) + 2, 0, 7); ctx.stroke(); }
      }
      ctx.globalAlpha = .8; ctx.strokeStyle = C.bad;
      (D.columns || []).forEach(u => { const [x] = at(R, u, 0); ctx.beginPath(); ctx.moveTo(x, R.y); ctx.lineTo(x, R.y + R.h * seg(t, 1000, 3000)); ctx.stroke(); });
      ctx.globalAlpha = 1;
      label(`${Math.round(D.n * seg(t, 1000, 4200)).toLocaleString('de-DE')} / ${D.n.toLocaleString('de-DE')} Hotpixel`, R.x + .5 * rem, R.y + R.h - 1.5 * rem, C.fg, 'left', .75);
      if (D.glow) label(`Glühmodell: ${nf(D.glow.min, 0)} … ${nf(D.glow.max, 0)} ADU`, R.x + R.w - .5 * rem, R.y + .5 * rem, C.mut, 'right');
    },
    hotfix(t) {
      const R = rect(aspect()); picture(imgs.base, R);
      const pts = D.points, k = Math.round(pts.length * seg(t, 300, 3200)), ch = [C.bad, C.ok, C.acc];
      ctx.lineWidth = 1.5;
      for (let i = 0; i < k; i++) {
        const [x, y] = at(R, pts[i][0], pts[i][1]), f = clamp((t - 300 - i / pts.length * 2900) / 500);
        ctx.strokeStyle = ch[pts[i][2]] || C.warn; ctx.globalAlpha = .9;
        ctx.beginPath(); ctx.arc(x, y, .5 * rem + (1 - ease(f)) * 1.1 * rem, 0, 7); ctx.stroke();
      }
      ctx.globalAlpha = 1; ctx.lineWidth = 1;
    },
    background(t) {                                // Modell als Wärmefläche + Höhenlinien, Stützkacheln, dann Wischkante vorher → nachher
      const R = rect(aspect()), wipe = ease(seg(t, 3000, 5600)), wx = R.x + R.w * wipe;
      picture(imgs.before, R);
      if (wipe > 0) { ctx.save(); ctx.beginPath(); ctx.rect(R.x, R.y, wx - R.x, R.h); ctx.clip(); picture(imgs.after, R); ctx.restore(); }
      heat ||= heatImage(D.grid);
      const a = ease(seg(t, 200, 1400)) * (1 - .6 * wipe);
      ctx.save(); ctx.beginPath(); ctx.rect(wx, R.y, R.x + R.w - wx, R.h); ctx.clip();
      ctx.globalAlpha = .42 * a; ctx.imageSmoothingEnabled = true; ctx.drawImage(heat, R.x, R.y, R.w, R.h); ctx.restore();
      const lv = [32, 80, 128, 176, 224], lines = seg(t, 600, 2200);
      ctx.lineWidth = 1;
      lv.forEach((L, i) => {
        if (lines * lv.length < i + .2) return;
        ctx.strokeStyle = i > 2 ? C.warn : C.d2; ctx.globalAlpha = .85 * a;
        ctx.beginPath(); contour(D.grid, L).forEach(([p, q]) => { ctx.moveTo(...at(R, ...p)); ctx.lineTo(...at(R, ...q)); }); ctx.stroke();
      });
      if (D.support) {
        const sp = D.support.points, k = Math.round(sp.length * seg(t, 1200, 2800)), r = .25 * rem;
        for (let i = 0; i < k; i++) {
          const [x, y] = at(R, sp[i][0], sp[i][1]);
          ctx.globalAlpha = .9 * (1 - .7 * wipe); ctx.strokeStyle = sp[i][2] ? C.ok : C.bad; ctx.lineWidth = 1.5;
          ctx.beginPath();
          if (sp[i][2]) ctx.arc(x, y, r, 0, 7); else { ctx.moveTo(x - r, y - r); ctx.lineTo(x + r, y + r); ctx.moveTo(x + r, y - r); ctx.lineTo(x - r, y + r); }
          ctx.stroke();
        }
        ctx.lineWidth = 1;
      }
      ctx.globalAlpha = 1;
      if (wipe > 0 && wipe < 1) { ctx.strokeStyle = C.fg; ctx.globalAlpha = .8; ctx.beginPath(); ctx.moveTo(wx, R.y); ctx.lineTo(wx, R.y + R.h); ctx.stroke(); ctx.globalAlpha = 1; }
      sides(R, wx, wipe, 'vorher', 'nachher');
    },
    color(t) {                                     // Gaia-Sterne markieren, Streudiagramm Farbe vs. BP−RP mit den echten Geraden
      const R = rect(aspect()); picture(imgs.base, R);
      const st = D.stars, k = Math.round(st.length * seg(t, 200, 2400));
      ctx.lineWidth = 1.25;
      for (let i = 0; i < k; i++) {
        const [x, y] = at(R, st[i][0], st[i][1]);
        ctx.strokeStyle = bprpColor(st[i][2]); ctx.globalAlpha = st[i][5] ? .95 : .35;
        ctx.beginPath(); ctx.arc(x, y, .45 * rem, 0, 7); ctx.stroke();
      }
      ctx.globalAlpha = ease(seg(t, 1400, 2400)); ctx.lineWidth = 1;
      const P = panel(panelW(), panelW() * .78);
      if (!small()) label('Gemessene Farbe vs. Gaia BP−RP', P.left, P.top, C.fg);
      const ys = st.flatMap(s => [s[3], s[4]]), y0 = Math.min(-1, ...ys), y1 = Math.max(1, ...ys), x0 = -.3, x1 = 2.5;
      const X = v => P.x + (v - x0) / (x1 - x0) * P.w, Y = v => P.y + P.h - (v - y0) / (y1 - y0) * P.h;
      ctx.strokeStyle = C.line; ctx.beginPath(); ctx.moveTo(P.x, Y(0)); ctx.lineTo(P.x + P.w, Y(0)); ctx.stroke();
      ctx.setLineDash([3, 3]); ctx.strokeStyle = C.mut; ctx.beginPath(); ctx.moveTo(X(D.g2v), P.y); ctx.lineTo(X(D.g2v), P.y + P.h); ctx.stroke(); ctx.setLineDash([]);
      label('G2V', X(D.g2v) + 3, P.y, C.mut, 'left', .625);
      const shown = Math.round(st.length * seg(t, 1800, 3800));
      for (let i = 0; i < shown; i++) {
        const s = st[i];
        ctx.fillStyle = C.bad; ctx.globalAlpha = s[5] & 1 ? .85 : .25; ctx.fillRect(X(s[2]) - 1.5, Y(s[3]) - 1.5, 3, 3);
        ctx.fillStyle = C.acc; ctx.globalAlpha = s[5] & 2 ? .85 : .25; ctx.fillRect(X(s[2]) - 1.5, Y(s[4]) - 1.5, 3, 3);
      }
      const ln = ease(seg(t, 3800, 5200));
      ctx.globalAlpha = 1; ctx.lineWidth = 2;
      [['R', C.bad], ['B', C.acc]].forEach(([c, color]) => {
        const f = D.fit[c]; if (!f) return;
        ctx.strokeStyle = color; ctx.beginPath(); ctx.moveTo(X(x0), Y(f.a * x0 + f.b)); const xe = x0 + (x1 - x0) * ln; ctx.lineTo(X(xe), Y(f.a * xe + f.b)); ctx.stroke();
      });
      ctx.lineWidth = 1;
      label('ln(R/G)', P.left, P.y + P.h + .3 * rem, C.bad, 'left', .625);
      label('ln(B/G)', P.left + 3.4 * rem, P.y + P.h + .3 * rem, C.acc, 'left', .625);
      if (t > 5200 && !small()) label(`R ×${nf(D.gains[0], 3)}  B ×${nf(D.gains[2], 3)}`, P.x + P.w, P.y + P.h + .3 * rem, C.fg, 'right', .625);
      ctx.globalAlpha = 1;
    },
    stretch(t) {                                   // lineares Bild → Kurve zeichnet sich → Bild folgt per Wischkante; Histogramme vorher/nachher
      const R = rect(aspect()), wipe = ease(seg(t, 2600, 5200)), wx = R.x + R.w * wipe;
      picture(imgs.before, R);
      if (wipe > 0) picture(imgs.after, R, 1, wx);
      if (wipe > 0 && wipe < 1) { ctx.strokeStyle = C.fg; ctx.globalAlpha = .8; ctx.beginPath(); ctx.moveTo(wx, R.y); ctx.lineTo(wx, R.y + R.h); ctx.stroke(); ctx.globalAlpha = 1; }
      const P = panel(panelW(), panelW() * .78);
      ctx.globalAlpha = ease(seg(t, 0, 700));
      if (!small()) label('Übertragungskurve und Histogramm', P.left, P.top, C.fg);
      const X = v => P.x + v * P.w, Y = v => P.y + P.h - v * P.h, n = D.curve.length;
      const bars = (h, color, a) => { if (!h?.length) return; ctx.fillStyle = color; ctx.globalAlpha = a; const bw = P.w / h.length;
        h.forEach((v, i) => { const hh = Math.sqrt(v) * P.h * .9; ctx.fillRect(P.x + i * bw, P.y + P.h - hh, Math.max(bw, 1), hh); }); };
      bars(D.hist_in, C.mut, .45 * ease(seg(t, 0, 700)));
      bars(D.hist_out, C.d3, .55 * ease(seg(t, 4200, 5400)));
      ctx.globalAlpha = 1; ctx.strokeStyle = C.line; ctx.beginPath(); ctx.moveTo(X(0), Y(0)); ctx.lineTo(X(1), Y(1)); ctx.stroke();
      const k = Math.round(n * ease(seg(t, 600, 2400)));
      ctx.strokeStyle = C.acc; ctx.lineWidth = 2; ctx.beginPath();
      for (let i = 0; i < k; i++) ctx[i ? 'lineTo' : 'moveTo'](X(i / (n - 1)), Y(D.curve[i]));
      ctx.stroke(); ctx.lineWidth = 1;
      if (wipe > 0) {                              // wo die Wischkante gerade steht: der Bildpunkt folgt der Kurve
        const i = Math.round((n - 1) * wipe); ctx.fillStyle = C.fg; ctx.beginPath(); ctx.arc(X(i / (n - 1)), Y(D.curve[i]), 3, 0, 7); ctx.fill();
      }
      label('linear (vorher)', P.left, P.y + P.h + .3 * rem, C.mut, 'left', .625);
      label('gestreckt', P.x + P.w, P.y + P.h + .3 * rem, C.d3, 'right', .625);
      sides(R, wx, wipe, 'linear', 'gestreckt');
    },
    stars(t) {                                     // sternloses Bild, Sternebene blinkt zweimal ein und aus, bleibt dann eingeblendet
      const R = rect(aspect()); picture(imgs.base, R);
      const ph = seg(t, 400, 4400), a = ph >= 1 ? 1 : .5 - .5 * Math.cos(ph * Math.PI * 5);
      ctx.save(); ctx.globalCompositeOperation = 'screen'; picture(imgs.stars, R, a); ctx.restore();
      label(a > .5 ? 'mit Sternebene' : 'ohne Sterne', R.x + R.w - .5 * rem, R.y + .5 * rem, C.fg, 'right');
    },
  };
  function draw(t) {
    if (!D || !W || !H) return;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0); ctx.clearRect(0, 0, W, H);
    (S[D.kind] || (() => {}))(Math.min(t, DUR[D.kind] || 5000));
  }
  function loop(now) {
    if (dead || !D) return;
    if (!host.isConnected) { stop(); return; }
    const t = now - t0;
    if (!document.hidden) draw(t);
    if (t < (DUR[D.kind] || 5000) && !document.hidden) raf = requestAnimationFrame(loop);
    else { draw(1e9); raf = 0; const f = done; done = null; f?.(); }   // Ende: Standbild bleibt stehen
  }
  function stop() { cancelAnimationFrame(raf); raf = 0; }
  const load = src => new Promise((ok, bad) => { const i = new Image(); i.onload = () => ok(i); i.onerror = bad; i.src = src; });
  return {
    get active() { return !!D; },
    get playing() { return !!raf; },
    // data: Einblick-JSON, src(name) → URL eines Einblick-Bildes, fin(): Animation fertig (Standbild steht)
    async play(data, src, fin) {
      if (dead || !PICS[data?.kind]) { fin?.(); return; }
      let loaded = {};
      try {
        const roles = PICS[data.kind].filter(r => data.pictures?.[r]);
        if (roles.length < PICS[data.kind].length) throw new Error('Bild fehlt');
        loaded = Object.fromEntries(await Promise.all(roles.map(async r => [r, await load(src(data.pictures[r]))])));
      } catch { fin?.(); return; }                 // ohne seine echten Bilder kein Einblick
      if (dead || !host.isConnected) return;
      stop(); done?.();
      D = data; imgs = loaded; heat = null; done = fin;
      size();
      const [title, sub] = caption(D);
      cap.children[0].textContent = title; cap.children[1].textContent = sub;
      cap.hidden = false; cv.hidden = false; host.classList.add('sl-insight');
      requestAnimationFrame(() => cv.classList.add('on'));
      t0 = performance.now();
      if (reduced() || document.hidden) { draw(1e9); const f = done; done = null; f?.(); return; }
      raf = requestAnimationFrame(loop);
    },
    clear() {
      stop(); const f = done; done = null; f?.();
      D = null; imgs = {}; heat = null;
      cv.classList.remove('on'); cap.hidden = true; host.classList.remove('sl-insight');
      setTimeout(() => { if (!D) { cv.hidden = true; ctx.clearRect(0, 0, cv.width, cv.height); } }, 600);
    },
    // gemessene Rauschkurve des Stacks: [[N, Rauschen relativ zum ersten Stand, Kanal], …]; Erwartung 1/√N ab dem ersten Punkt
    noise(curve) {
      const band = curve?.length ? curve[curve.length - 1][2] : null;
      const pts = (curve || []).filter(p => p[2] === band && p[1] != null);
      nz.hidden = pts.length < 3 || !!D;
      if (nz.hidden) return;
      const sig = JSON.stringify(pts);
      if (nz.dataset.sig === sig) return;
      nz.dataset.sig = sig;
      const w = 160, h = 92, l = 26, b = 16, n0 = pts[0][0], r0 = pts[0][1], nMax = Math.max(n0 + 1, pts[pts.length - 1][0]);
      const lx = n => l + (Math.log(n) - Math.log(n0)) / (Math.log(nMax) - Math.log(n0)) * (w - l - 6);
      const ly = r => 6 + clamp(1 - r / Math.max(r0, 1e-9)) * (h - b - 10);   // senkrecht: 0 … Rauschen des ersten Stands
      const exp = Array.from({ length: 24 }, (_, i) => n0 * (nMax / n0) ** (i / 23)).map(n => `${lx(n).toFixed(1)},${ly(r0 * Math.sqrt(n0 / n)).toFixed(1)}`).join(' ');
      const meas = pts.map(p => `${lx(p[0]).toFixed(1)},${ly(p[1]).toFixed(1)}`).join(' ');
      const last = pts[pts.length - 1];
      nz.innerHTML = `<b>Rauschen je Aufnahmenzahl</b><svg viewBox="0 0 ${w} ${h}" role="img" aria-label="Gemessenes Rauschen ${Math.round(last[1] * 100)} % nach ${last[0]} Aufnahmen; gestrichelt die Erwartung 1/√N">
        <line x1="${l}" y1="${h - b}" x2="${w - 4}" y2="${h - b}" class="ax"/><line x1="${l}" y1="4" x2="${l}" y2="${h - b}" class="ax"/>
        <polyline points="${exp}" class="ex"/><polyline points="${meas}" class="me"/>
        ${pts.map(p => `<circle cx="${lx(p[0]).toFixed(1)}" cy="${ly(p[1]).toFixed(1)}" r="1.8" class="me"/>`).join('')}
        <text x="${l - 3}" y="10" text-anchor="end">${Math.round(r0 * 100)}%</text><text x="${l - 3}" y="${h - b}" text-anchor="end">0</text>
        <text x="${l}" y="${h - 3}">${n0}</text><text x="${w - 4}" y="${h - 3}" text-anchor="end">${last[0]} Aufn.</text></svg>
        <span><i class="me"></i>gemessen <i class="ex"></i>1/√N erwartet</span>`;
    },
    destroy() { dead = true; stop(); ro.disconnect(); },
  };
}
window.createInsight = createInsight;
})();
