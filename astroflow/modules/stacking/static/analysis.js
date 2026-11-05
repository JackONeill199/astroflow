'use strict';
// Reiter „Analyse“ der Objektseite (seit 0.27.0, Nutzerwunsch: Tiefe, Veränderungen und Himmel nicht mehr gequetscht in der Detailspalte).
// Oben das Bild (kompakter Betrachter mit denselben Overlays), darunter breite Abschnitte: Wie tief schaust du? (mit 3D-Tunnel und
// Kameraflug), Veränderungen (Blink groß, Kandidaten daneben), Der Himmel in dieser Nacht (Bortle-Zeiger, Verlauf, Lichtrichtung), Tutor.
// Animationen kurz und funktional: Zahlen zählen hoch, Abschnitte blenden beim Erreichen ein, Kurven zeichnen sich – alles aus bei
// prefers-reduced-motion. Die Abschnitte selbst kommen aus depth.js (depthPanel/comparePanel), skyquality.js (AFsky.analysis) und tutorPanel.

const anReduced = () => matchMedia('(prefers-reduced-motion: reduce)').matches;

// Erste Zahl in einem Text hochzählen (deutsches Format, Nachkommastellen und Tausenderpunkte bleiben): „17,3 mag“, „1.234“, „5,5 Mrd. Lj“
function anCountUp(root, dur = 900) {
  if (anReduced()) return;
  for (const b of root.querySelectorAll('[data-count]')) {
    if (b.dataset.counted) continue;
    b.dataset.counted = '1';
    const text = b.textContent, m = text.match(/\d{1,3}(?:\.\d{3})+(?:,\d+)?|\d+(?:,\d+)?/);
    if (!m) continue;
    const dec = (m[0].split(',')[1] || '').length, val = parseFloat(m[0].replace(/\./g, '').replace(',', '.'));
    if (!isFinite(val) || val === 0) continue;
    const pre = text.slice(0, m.index), post = text.slice(m.index + m[0].length), t0 = performance.now();
    const fmt = (v) => v.toLocaleString('de-DE', { minimumFractionDigits: dec, maximumFractionDigits: dec });
    const step = (now) => {
      if (!b.isConnected) return;
      const k = Math.min(1, (now - t0) / dur), e = 1 - (1 - k) ** 3;
      b.textContent = pre + fmt(val * e) + post;
      if (k < 1) requestAnimationFrame(step); else b.textContent = text;
    };
    requestAnimationFrame(step);
  }
}

// Abschnitte blenden ein, sobald sie ins Bild kommen; danach zählen ihre Zahlen hoch und Kurven zeichnen sich
function anReveal(sec) {
  if (anReduced() || !('IntersectionObserver' in window)) { sec.classList.add('in'); return; }
  const io = new IntersectionObserver((es) => {
    if (!es.some(e => e.isIntersecting)) return;
    io.disconnect(); sec.classList.add('in'); anCountUp(sec);
  }, { threshold: 0.15 });
  io.observe(sec);
  // Inhalte kommen später (Server) – neue Zahlen in einem schon sichtbaren Abschnitt ebenfalls hochzählen
  new MutationObserver(() => { if (sec.classList.contains('in')) anCountUp(sec); }).observe(sec, { childList: true, subtree: true });
}

// Detailspalte im Reiter Bild: 3–4 Kernzahlen + Verweis auf den Reiter
async function analysisSummary(host, r, pid) {
  if (!host) return;
  const q = r.stats?.quality;
  const tile = (k, label, v, sub = '') => `<a class="stat an-kpi" href="#/p/${pid}/analyse" data-k="${k}"><small>${label}</small><b class="tnum">${v}</b><small class="dim">${sub}</small></a>`;
  const draw = (depth, night) => {
    if (!host.isConnected) return;
    const g = depth?.state === 'done' ? depth.grenze : null;
    host.innerHTML = `<div class="stats an-sum">
        ${q ? tile('q', 'Bildqualität', `${String(q.score).replace('.', ',')}<span class="dim">/10</span>`, esc(qlabel(q))) : ''}
        ${tile('depth', 'Grenzgröße', g?.m5 != null ? `${String(g.m5.toFixed(1)).replace('.', ',')} mag` : '–', g ? '5σ, Gaia' : depth?.solved === false ? 'nicht gelöst' : 'noch nicht gemessen')}
        ${tile('sky', 'Bortle', night?.bortle ? esc(night.bortle.text.replace('Bortle ', '')) : '–', night?.bortle ? esc(night.bortle.label) : night ? 'kein Ortswert' : 'noch nicht berechnet')}
        ${tile('seeing', 'Seeing', night?.seeing != null ? `${String(night.seeing.toFixed(1)).replace('.', ',')}″` : '–', night?.seeing != null ? 'FWHM, Median' : '')}
      </div>
      <a class="an-more" href="#/p/${pid}/analyse">Mehr in der Analyse ${icon('arrow')}</a>`;
  };
  draw(null, null);
  const [depth, sky] = await Promise.all([
    typeof insightLoad === 'function' ? insightLoad('depth', r.id).catch(() => null) : null,
    api(`/stacking/projects/${pid}/sky`).catch(() => null)]);
  const ns = sky?.nights || [];
  draw(depth, ns.find(n => n.bortle) || ns.at(-1) || null);
}

// Reiter Analyse. ctx: { p, r, viewerFor(host) → createViewer-Objekt } – gibt { viewer, destroy } zurück
function analysisTab(t, ctx) {
  const { p, r } = ctx;
  const secs = [['anDepth', 'Wie tief schaust du?', 'target'], ['anCmp', 'Veränderungen', 'compare'], ['anSky', 'Der Himmel in dieser Nacht', 'globe'], ['anTutor', 'Tutor', 'sparkle']];
  t.innerHTML = `<div class="an">
      <div class="an-top"><div id="anViewer"></div>
        <nav class="an-toc" aria-label="Abschnitte">${secs.map(([id, l, ic], i) => `<a href="#${id}" data-sec="${id}" style="--i:${i}">${icon(ic)}<span>${l}</span></a>`).join('')}
          <p class="dim sm">Klick auf Sterne, Galaxien oder Kandidaten zeigt sie im Bild.</p></nav></div>
      <section class="panel an-sec depth-panel" id="anDepth"></section>
      <section class="panel an-sec" id="anCmpWrap"><div class="an-cmp"><div class="an-blink" id="anBlink"></div><div class="depth-panel" id="anCmp"></div></div></section>
      <section class="panel an-sec" id="anSky"></section>
      <section class="an-sec an-tutor" id="anTutor"><div id="anTutorBox">${qualityPanel(r.stats || {})}</div></section>
    </div>`;
  const viewer = ctx.viewerFor($('#anViewer', t));
  viewer.el.classList.add('an-viewer', 'hover-labels');
  // Bildpixel der Auswertung (Raster d.bild) → Betrachter zoomen
  const focus = (x, y, w) => {
    const img = $('img.b', viewer.el), sc = img?.naturalWidth && w ? img.naturalWidth / w : 1;
    viewer.zoomTo(x * sc, y * sc, 5);
    $('#anViewer', t).scrollIntoView({ behavior: anReduced() ? 'auto' : 'smooth', block: 'nearest' });
  };
  $('.an-toc', t).addEventListener('click', (e) => {
    const a = e.target.closest('[data-sec]'); if (!a) return;
    e.preventDefault(); $('#' + (a.dataset.sec === 'anCmp' ? 'anCmpWrap' : a.dataset.sec), t)?.scrollIntoView({ behavior: anReduced() ? 'auto' : 'smooth', block: 'start' });
  });
  depthPanel($('#anDepth', t), r, viewer, { wide: true, onFocus: focus, onSolve: ctx.solve });
  // Veränderungen: Blink-Betrachter groß links, Kandidaten rechts
  let blink = null;
  const showBlink = (on, f) => {
    blink?.destroy(); blink = null;
    const host = $('#anBlink', t);
    if (!host?.isConnected) return;
    host.innerHTML = '<div></div>';                  // compareViewer hängt seine Blinkleiste hinter den Behälter – bleibt so in der linken Spalte
    if (on) blink = compareViewer(host.firstChild, r, f);
    $('.an-cmp', t)?.classList.toggle('has-blink', !!blink);
    comparePanel($('#anCmp', t), r, showBlink, !!blink);
  };
  comparePanel($('#anCmp', t), r, showBlink, false);
  insightLoad('compare', r.id).then(d => { if (d?.state === 'done' && t.isConnected && !blink) showBlink(true); }).catch(() => {});
  window.AFsky?.analysis?.($('#anSky', t), p.id);
  if (r.stats?.quality) tutorPanel($('#anTutorBox', t), r);
  $$('.an-sec', t).forEach(anReveal);
  return { viewer, destroy: () => { blink?.destroy(); blink = null; } };
}
