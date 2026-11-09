'use strict';
// Reiter „Analyse“ der Objektseite (seit 0.27.0, Nutzerwunsch: Tiefe, Veränderungen und Himmel nicht mehr gequetscht in der Detailspalte).
// Oben das Bild (kompakter Betrachter mit denselben Overlays; Knopf „3D“ in dessen Werkzeugleiste lässt die Sterne aus dem Bild heraus
// nach Entfernung in die Tiefe ziehen – kein zweiter Betrachter, seit 0.27.1), darunter breite Abschnitte: Wie tief schaust du?, Veränderungen (Blink groß, Kandidaten daneben), Asteroiden (asteroids.js), Der Himmel in dieser Nacht (Bortle-Zeiger, Verlauf, Lichtrichtung), Tutor.
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
  const tile = (k, label, v, sub = '', tip = '') => `<a class="stat an-kpi" href="#/p/${pid}/analyse" data-k="${k}"${tip ? ` title="${esc(tip)}"` : ''}><small>${label}</small><b class="tnum">${v}</b><small class="dim">${sub}</small></a>`;
  let grade = null;
  const draw = (depth, night) => {
    if (!host.isConnected) return;
    const gq = grade?.grade;
    const g = depth?.state === 'done' ? depth.grenze : null;
    host.innerHTML = `<div class="stats an-sum">
        ${gq ? tile('q', 'Bildqualität', `${String(gq.score).replace('.', ',')}<span class="dim">/10</span>`, esc(gq.parts.map(p => p.label).join(' · ')), gradeTip(gq))
          : q ? tile('q', 'Bearbeitung', `${String(q.score).replace('.', ',')}<span class="dim">/10</span>`, esc(qlabel(q))) : ''}
        ${tile('depth', 'Grenzgröße', g?.m5 != null ? `${String(g.m5.toFixed(1)).replace('.', ',')} mag` : '–', g ? '5σ, Gaia' : depth?.solved === false ? 'nicht gelöst' : 'noch nicht gemessen')}
        ${tile('sky', 'Bortle', night?.bortle ? esc(night.bortle.text.replace('Bortle ', '')) : '–', night?.bortle ? esc(night.bortle.label) : night ? 'kein Ortswert' : 'noch nicht berechnet')}
        ${tile('seeing', 'Seeing', night?.seeing != null ? `${String(night.seeing.toFixed(1)).replace('.', ',')}″` : '–', night?.seeing != null ? 'FWHM, Median' : '')}
      </div>
      <a class="an-more" href="#/p/${pid}/analyse">Mehr in der Analyse ${icon('arrow')}</a>`;
  };
  draw(null, null);
  const [depth, sky, gr] = await Promise.all([
    typeof insightLoad === 'function' ? insightLoad('depth', r.id).catch(() => null) : null,
    api(`/stacking/projects/${pid}/sky`).catch(() => null), api(`/stacking/results/${r.id}/grade`).catch(() => null)]);
  grade = gr;
  const ns = sky?.nights || [];
  draw(depth, ns.find(n => n.bortle) || ns.at(-1) || null);
}

// Reiter Analyse. ctx: { p, r, viewerFor(host) → createViewer-Objekt } – gibt { viewer, destroy } zurück
function analysisTab(t, ctx) {
  const { p, r } = ctx;
  const secs = [['anDepth', 'Wie tief schaust du?', 'target'], ['anHrd', 'HR-Diagramm', 'star'], ['anCmp', 'Veränderungen', 'compare'], ['anAst', 'Asteroiden', 'orbit'], ['anSky', 'Der Himmel in dieser Nacht', 'globe'], ['anDiag', 'Dein Gerät', 'camera'], ['anTutor', 'Tutor', 'sparkle']];
  t.innerHTML = `<div class="an">
      <div class="an-top"><div id="anViewer"></div>
        <nav class="an-toc" aria-label="Abschnitte">${secs.map(([id, l, ic], i) => `<a href="#${id}" data-sec="${id}" style="--i:${i}"${id === 'anHrd' ? ' hidden' : ''}>${icon(ic)}<span>${l}</span></a>`).join('')}
          <p class="dim sm">Klick auf Sterne, Galaxien oder Kandidaten zeigt sie im Bild.</p></nav></div>
      <section class="panel an-sec depth-panel" id="anDepth"></section>
      <section class="panel an-sec depth-panel hrd-panel" id="anHrd" hidden></section>
      <section class="panel an-sec" id="anCmpWrap"><div class="an-cmp"><div class="an-blink" id="anBlink"></div><div class="depth-panel" id="anCmp"></div></div></section>
      <section class="panel an-sec depth-panel" id="anAst"></section>
      <section class="panel an-sec" id="anSky"></section>
      <section class="panel an-sec" id="anDiag"></section>
      <section class="an-sec an-tutor" id="anTutor"><div id="anTutorBox">${qualityPanel(r.stats || {})}</div></section>
    </div>`;
  const viewer = ctx.viewerFor($('#anViewer', t));
  viewer.el.classList.add('an-viewer', 'hover-labels');
  // Bildpixel der Auswertung (Raster d.bild) → Betrachter zoomen
  const focus = (x, y, w) => {
    set3d(false, false);
    const img = $('img.b', viewer.el), sc = img?.naturalWidth && w ? img.naturalWidth / w : 1;
    viewer.zoomTo(x * sc, y * sc, 5);
    $('#anViewer', t).scrollIntoView({ behavior: anReduced() ? 'auto' : 'smooth', block: 'nearest' });
  };
  $('.an-toc', t).addEventListener('click', (e) => {
    const a = e.target.closest('[data-sec]'); if (!a) return;
    e.preventDefault(); $('#' + (a.dataset.sec === 'anCmp' ? 'anCmpWrap' : a.dataset.sec), t)?.scrollIntoView({ behavior: anReduced() ? 'auto' : 'smooth', block: 'start' });
  });
  // 3D im Betrachter: Umschalter „3D“ in der Werkzeugleiste, sobald die Tiefenmessung Punkte hat
  let depth = null, t3 = null;
  const b3d = document.createElement('button');
  b3d.className = 'v3d-btn'; b3d.dataset.v3d = ''; b3d.hidden = true; b3d.title = '3D: Sterne nach Entfernung in die Tiefe'; b3d.setAttribute('aria-pressed', 'false');
  b3d.textContent = '3D';
  $('.vbar', viewer.el).prepend(b3d);
  const set3d = (on, animated = true) => {
    if (!on === !t3 || (on && !depth)) return;
    b3d.classList.toggle('on', on); b3d.setAttribute('aria-pressed', String(on));
    if (on) {
      $('.vbar [data-a="fit"]', viewer.el)?.click();             // gleicher Ausschnitt wie das eingepasste Bild
      t3 = depthTunnel(depth, r, viewer);
      $('#anViewer', t).scrollIntoView({ behavior: anReduced() ? 'auto' : 'smooth', block: 'nearest' });
    } else { t3?.close(animated); t3 = null; }
  };
  b3d.addEventListener('click', (e) => { e.stopPropagation(); set3d(!t3); });
  const onData = (d) => { depth = d; b3d.hidden = !d.punkte?.length || !d.bild; if (b3d.hidden) set3d(false, false); };
  depthPanel($('#anDepth', t), r, viewer, { wide: true, onFocus: focus, onSolve: ctx.solve, on3d: set3d, onData });
  // HR-Diagramm: nur bei Sternhaufen (bzw. wenn eine Messung einen Haufen gefunden hat) – Abschnitt und Eintrag links erst dann zeigen
  if (typeof hrdPanel === 'function') hrdPanel($('#anHrd', t), r, { onFocus: focus, onShow: (on) => { const a = $('.an-toc [data-sec="anHrd"]', t); if (a) a.hidden = !on; } });
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
  if (typeof asteroidPanel === 'function') asteroidPanel($('#anAst', t), r, viewer, { onFocus: focus });
  window.AFsky?.analysis?.($('#anSky', t), p.id);
  window.diagPanel?.($('#anDiag', t), r);                // Gerätediagnose (diagnose.js)
  if (r.stats?.quality) tutorPanel($('#anTutorBox', t), r);
  $$('.an-sec', t).forEach(anReveal);
  // Sprung aus dem Dashboard (#/analyse): Stelle im Bild anfahren, sobald das Foto geladen ist
  let pf = null;
  try { pf = JSON.parse(sessionStorage.getItem('af.an.focus') || 'null'); } catch { pf = null; }
  if (pf && pf.rid === r.id) {
    try { sessionStorage.removeItem('af.an.focus'); } catch { /* egal */ }
    const img = $('img.b', viewer.el), go = () => { if (t.isConnected) focus(pf.x, pf.y, pf.w); };
    if (img?.complete && img.naturalWidth) setTimeout(go, 250); else img?.addEventListener('load', () => setTimeout(go, 100), { once: true });
  }
  return { viewer, destroy: () => { blink?.destroy(); blink = null; set3d(false, false); } };
}
