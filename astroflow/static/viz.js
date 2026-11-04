'use strict';
/* Prozessvisualisierung („Visualize what happens“): kleine, schematische Canvas-Szenen zu echten Verarbeitungsschritten.
   Sie zeigen vereinfacht, WAS gerade passiert – die Zahlen (Aufnahmen, Fortschritt, Kandidaten) kommen aus dem Backend.
   Immer als „Schema“ gekennzeichnet; echte Zwischenbilder zeigt die Live-Vorschau. Pausierbar, ruht bei verborgenem Tab
   und zeichnet bei prefers-reduced-motion nur ein Standbild.
   vizProcess(host, { kind, n, total, progress, label }) → { update(data), destroy() }
   kind: analyse | auswahl | stack | develop | save | solve | trace | research */
(() => {
const reduced = () => matchMedia('(prefers-reduced-motion: reduce)').matches;
function rng(seed) { let s = seed >>> 0 || 1; return () => (s = (s * 1664525 + 1013904223) >>> 0) / 4294967296; }
function stars(n, seed, w = 1, h = 1) { const r = rng(seed); return Array.from({ length: n }, () => ({ x: r() * w, y: r() * h, m: .25 + r() ** 3 * .9 })); }
const FIELD = stars(70, 7);
const TEXT = {
  analyse: ['Aufnahmen vermessen', 'Sterne finden, Schärfe (FWHM) und Hintergrund je Aufnahme messen'],
  auswahl: ['Aufnahmen auswählen', 'Ausreißer (Wolken, Unschärfe, Nachführfehler) aussortieren'],
  stack: ['Kalibrieren · Ausrichten · Stacken', 'Aufnahmen an gemeinsamen Sternen ausrichten und mitteln – das Rauschen sinkt mit 1/√N'],
  develop: ['Entwickeln', 'Hintergrund ebnen, Farben kalibrieren, Helligkeitskurve strecken'],
  save: ['Speichern', 'TIFF, PNG, JPG und linearen FITS schreiben'],
  solve: ['Plate-Solving', 'Sternmuster mit dem Katalog vergleichen'],
  trace: ['Spurenanalyse', 'Sternfeld ausrichten, Aufnahmen vergleichen, bewegte Lichtpunkte verfolgen'],
  research: ['Recherche', 'Kataloge und Quellen abfragen und zusammenführen'],
};
function vizProcess(host, data = {}) {
  host.innerHTML = `<figure class="viz"><canvas aria-hidden="true"></canvas><figcaption><span class="chip" title="Vereinfachte Darstellung, keine echten Bilddaten">Schema</span>
    <b class="viz-t"></b><span class="viz-d mut sm"></span><span class="grow"></span><span class="viz-n tnum sm"></span>
    <button class="tbtn viz-p" title="Animation anhalten" aria-label="Animation anhalten"><svg><use href="#i-pause"/></svg></button></figcaption></figure>`;
  const fig = host.firstElementChild, cv = $('canvas', fig), ctx = cv.getContext('2d');
  let D = { ...data }, t0 = performance.now(), paused = reduced(), raf = 0, last = 0, dead = false, W = 0, H = 0, dpr = 1;
  const css = getComputedStyle(document.documentElement), col = (v, d) => css.getPropertyValue(v).trim() || d;
  const C = { fg: col('--fg', '#f0f2f5'), mut: col('--mut', '#a0a8b4'), dim: col('--dim', '#717b89'), acc: col('--acc', '#4c9aff'), ok: col('--ok', '#42b883'), bad: col('--bad', '#e16b6b'), d3: col('--data-3', '#5fb8a8'), line: col('--border', '#303640') };
  const size = () => { const r = cv.getBoundingClientRect(); dpr = Math.min(2, devicePixelRatio || 1); W = r.width; H = r.height; cv.width = Math.round(W * dpr); cv.height = Math.round(H * dpr); };
  const ro = new ResizeObserver(() => { size(); if (paused) draw(t0 + 4000); });
  ro.observe(cv);
  const btn = $('.viz-p', fig);
  const setBtn = () => { btn.innerHTML = icon(paused ? 'play' : 'pause'); btn.title = paused ? 'Animation abspielen' : 'Animation anhalten'; btn.setAttribute('aria-label', btn.title); };
  btn.onclick = () => { paused = !paused; setBtn(); if (!paused) loop(); };
  setBtn();
  function caption() {
    const [t, d] = TEXT[D.kind] || [D.kind, ''];
    $('.viz-t', fig).textContent = D.title || t;
    $('.viz-d', fig).textContent = D.label || d;
    const n = D.kind === 'stack' && D.n ? `${D.n}${D.total ? ' / ' + D.total : ''} Aufnahmen · Rauschen ≈ ${Math.round(100 / Math.sqrt(D.n))} %`
      : D.kind === 'trace' && D.total ? `${D.n || 0} / ${D.total} Aufnahmen${D.found != null ? ' · ' + D.found + ' Erkennungen' : ''}`
      : D.progress != null ? Math.round(D.progress * 100) + ' %' : '';
    $('.viz-n', fig).textContent = n;
  }
  // ---- Szenen (t in Sekunden; alles in Einheitskoordinaten des Feldes) ----
  function field(x0, y0, w, h, opt = {}) {
    const { noise = 0, alpha = 1, dx = 0, dy = 0, rot = 0, rings = 0 } = opt;
    ctx.save(); ctx.beginPath(); ctx.rect(x0, y0, w, h); ctx.clip();
    ctx.fillStyle = '#07080a'; ctx.fillRect(x0, y0, w, h);
    if (noise > 0) {                                     // Rauschen als zufällige Punkte, Dichte ∝ noise
      const r = rng(Math.floor(performance.now() / 120)), k = Math.round(w * h / 90 * noise);
      ctx.fillStyle = `rgba(160,168,180,${.25 * noise + .05})`;
      for (let i = 0; i < k; i++) ctx.fillRect(x0 + r() * w, y0 + r() * h, 1, 1);
    }
    const cx = x0 + w / 2, cy = y0 + h / 2, cs = Math.cos(rot), sn = Math.sin(rot);
    for (const s of FIELD) {
      const px = (s.x - .5) * w, py = (s.y - .5) * h, x = cx + px * cs - py * sn + dx * w, y = cy + px * sn + py * cs + dy * h;
      ctx.globalAlpha = alpha * (.4 + s.m * .6);
      ctx.fillStyle = C.fg; ctx.beginPath(); ctx.arc(x, y, .6 + s.m * 1.6, 0, 7); ctx.fill();
      if (rings && s.m > .6) { ctx.globalAlpha = rings; ctx.strokeStyle = C.acc; ctx.lineWidth = 1; ctx.beginPath(); ctx.arc(x, y, 5 + s.m * 3, 0, 7); ctx.stroke(); }
    }
    ctx.restore(); ctx.globalAlpha = 1;
  }
  const frameBox = (x, y, w, h, c = C.line) => { ctx.strokeStyle = c; ctx.lineWidth = 1; ctx.strokeRect(x + .5, y + .5, w - 1, h - 1); };
  const S = {
    stack(t) {                                            // Frames gleiten versetzt herein, rasten am Sternmuster ein, Stapel wird klarer
      const n = Math.max(1, D.n || Math.round(1 + (D.progress || 0) * 40)), fh = H - 24, fw = Math.min(W * .42, fh * 1.5), x0 = W - fw - 16, y0 = 12;
      const cyc = 2.4, k = (t % cyc) / cyc, e = 1 - (1 - Math.min(1, k * 1.6)) ** 3;
      field(x0, y0, fw, fh, { noise: Math.min(1, 1.6 / Math.sqrt(n)) });         // Ergebnis: Rauschen ∝ 1/√N (echtes N)
      frameBox(x0, y0, fw, fh, C.acc);
      const ix = 16, iw = fw * .62, ih = fh * .62, iy = (H - ih) / 2;              // eingehender Frame: verschoben + gedreht, dann ausgerichtet
      const sx = ix + (x0 - ix + (fw - iw) / 2) * e, sy = iy + ((y0 + (fh - ih) / 2) - iy) * e;
      for (let j = 2; j >= 1; j--) { ctx.globalAlpha = .25 / j; frameBox(ix - j * 6, iy - j * 6, iw, ih); }
      ctx.globalAlpha = 1 - Math.max(0, k - .75) * 4;
      field(sx, sy, iw, ih, { noise: 1, alpha: .9, dx: .06 * (1 - e), dy: -.05 * (1 - e), rot: .12 * (1 - e) });
      frameBox(sx, sy, iw, ih, e > .98 ? C.ok : C.line);
      ctx.globalAlpha = 1;
    },
    analyse(t) {                                          // Aufnahmen laufen durch, Sterne werden umkreist (gemessen)
      const fh = H - 24, fw = Math.min(W - 32, fh * 1.6), x0 = (W - fw) / 2, y0 = 12, k = (t % 1.6) / 1.6;
      field(x0, y0, fw, fh, { noise: .8, rings: Math.min(1, k * 2) * .9, dx: Math.sin(Math.floor(t / 1.6)) * .01 });
      ctx.fillStyle = C.acc; ctx.globalAlpha = .18; ctx.fillRect(x0 + fw * k - 20, y0, 20, fh); ctx.globalAlpha = 1;
      frameBox(x0, y0, fw, fh);
    },
    auswahl(t) {                                          // Reihe von Aufnahmen, einzelne fallen heraus
      const n = 9, gw = (W - 32) / n, fh = Math.min(H - 30, gw * .8), y = (H - fh) / 2;
      for (let i = 0; i < n; i++) {
        const bad = i === 2 || i === 6, k = (t % 3) / 3, drop = bad ? Math.max(0, k - .4) * 1.6 : 0;
        ctx.globalAlpha = 1 - drop * .8;
        field(16 + i * gw + 2, y + drop * 30, gw - 4, fh, { noise: bad ? 1 : .6, alpha: bad && i === 2 ? .3 : 1 });
        frameBox(16 + i * gw + 2, y + drop * 30, gw - 4, fh, bad && drop ? C.bad : C.line);
      }
      ctx.globalAlpha = 1;
    },
    develop(t) {                                          // Histogramm + Kurve: linear → gestreckt (Fortschritt)
      const p = D.progress != null ? Math.min(1, Math.max(.15, D.progress)) : (Math.sin(t * .8) + 1) / 2, x0 = 16, y0 = 14, w = Math.min(W * .45, 260), h = H - 28;
      frameBox(x0, y0, w, h);
      ctx.strokeStyle = C.dim; ctx.beginPath(); ctx.moveTo(x0, y0 + h); ctx.lineTo(x0 + w, y0); ctx.stroke();
      const a = 1 + p * 60, curve = (x) => Math.asinh(a * x) / Math.asinh(a);
      ctx.strokeStyle = C.acc; ctx.lineWidth = 2; ctx.beginPath();
      for (let i = 0; i <= 60; i++) { const x = i / 60; ctx.lineTo(x0 + x * w, y0 + h - curve(x) * h); } ctx.stroke(); ctx.lineWidth = 1;
      for (let i = 0; i < 40; i++) {                      // Histogramm: Daten links gestaucht, nach der Kurve verteilt
        const x = i / 40, v = Math.exp(-((x - .04) ** 2) / .002) + .15 * Math.exp(-((x - .2) ** 2) / .01), xs = curve(x);
        ctx.fillStyle = C.d3; ctx.globalAlpha = .55; ctx.fillRect(x0 + xs * w, y0 + h - v * h * .8, w / 40, v * h * .8);
      }
      ctx.globalAlpha = 1;
      const fw = W - w - 48, fx = x0 + w + 16;
      if (fw > 60) { ctx.filter = `brightness(${.5 + p * 1.1})`; field(fx, y0, fw, h, { noise: .3 * (1 - p) }); ctx.filter = 'none'; frameBox(fx, y0, fw, h); }
    },
    save(t) { S.develop(t + 10); },
    solve(t) {                                            // Dreiecke im Sternfeld werden mit dem Katalog verglichen
      const fh = H - 24, fw = Math.min(W - 32, fh * 1.6), x0 = (W - fw) / 2, y0 = 12;
      field(x0, y0, fw, fh, { noise: .4 });
      const br = FIELD.filter(s => s.m > .7).slice(0, 6), k = Math.floor(t * 1.2) % Math.max(1, br.length - 2);
      ctx.strokeStyle = C.acc; ctx.beginPath();
      [br[k], br[k + 1], br[k + 2], br[k]].forEach((s, i) => s && ctx[i ? 'lineTo' : 'moveTo'](x0 + s.x * fw, y0 + s.y * fh)); ctx.stroke();
      frameBox(x0, y0, fw, fh);
    },
    trace(t) {                                            // registriertes Sternfeld steht, ein Punkt wandert und hinterlässt seine Spur
      const fh = H - 24, fw = Math.min(W - 32, fh * 1.6), x0 = (W - fw) / 2, y0 = 12, cyc = 4, k = (t % cyc) / cyc;
      field(x0, y0, fw, fh, { noise: .5 });
      const p = (u) => [x0 + fw * (.12 + .76 * u), y0 + fh * (.78 - .55 * u)];
      const steps = Math.floor(k * 12);
      ctx.strokeStyle = C.d3; ctx.setLineDash([3, 3]); ctx.beginPath(); ctx.moveTo(...p(0)); ctx.lineTo(...p(1)); ctx.globalAlpha = .35; ctx.stroke(); ctx.setLineDash([]);
      ctx.globalAlpha = 1; ctx.strokeStyle = C.acc; ctx.lineWidth = 2; ctx.beginPath(); ctx.moveTo(...p(0)); ctx.lineTo(...p(steps / 12)); ctx.stroke(); ctx.lineWidth = 1;
      ctx.fillStyle = C.acc; ctx.beginPath(); ctx.arc(...p(steps / 12), 3, 0, 7); ctx.fill();
      ctx.fillStyle = C.acc; ctx.globalAlpha = .15; ctx.fillRect(x0 + fw * k, y0, 3, fh); ctx.globalAlpha = 1;
      frameBox(x0, y0, fw, fh);
    },
    research(t) {                                         // Quellen (Kreise) verbinden sich mit dem Objekt in der Mitte
      const cx = W / 2, cy = H / 2, r = Math.min(W, H) * .36, n = 6;
      for (let i = 0; i < n; i++) {
        const a = i / n * 6.283 + .3, x = cx + Math.cos(a) * r * 1.4, y = cy + Math.sin(a) * r * .8, on = (t * 1.5 % n) > i;
        ctx.strokeStyle = on ? C.acc : C.line; ctx.globalAlpha = on ? .9 : .5; ctx.beginPath(); ctx.moveTo(cx, cy); ctx.lineTo(x, y); ctx.stroke();
        ctx.fillStyle = on ? C.acc : C.dim; ctx.beginPath(); ctx.arc(x, y, 4, 0, 7); ctx.fill();
      }
      ctx.globalAlpha = 1; ctx.fillStyle = C.fg; ctx.beginPath(); ctx.arc(cx, cy, 6, 0, 7); ctx.fill();
    },
  };
  function draw(now) {
    if (!W || !H) return;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0); ctx.clearRect(0, 0, W, H);
    (S[D.kind] || S.analyse)((now - t0) / 1000);
  }
  function loop(now = performance.now()) {
    if (dead || paused) return;
    if (!document.hidden && fig.isConnected && now - last > 33) { last = now; draw(now); }   // ≤ 30 Bilder/s
    raf = requestAnimationFrame(loop);
  }
  size(); caption(); draw(t0 + 4000);
  if (!paused) loop();
  return {
    el: fig,
    update(d) { const k = D.kind; D = { ...D, ...d }; caption(); if (paused || k !== D.kind) draw(performance.now()); },
    destroy() { dead = true; cancelAnimationFrame(raf); ro.disconnect(); },
  };
}
window.vizProcess = vizProcess;
})();
