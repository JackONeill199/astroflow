/* Echte Zwischenbilder: zwei Ebenen für weiche Wechsel, Status ohne Neuaufbau des Viewers. */
function createStackLive(host, pid) {
  host.innerHTML = `<section class="stack-live" aria-label="Live-Verarbeitung">
    <div class="sl-picture"><img class="sl-image" alt="Live-Vorschau des Astrofotos"><img class="sl-image" alt="" aria-hidden="true">
      <div class="sl-wait"><span class="sl-orbit" aria-hidden="true"></span><span>Noch keine Bildvorschau</span></div>
      <svg class="sl-pattern" aria-hidden="true" preserveAspectRatio="xMidYMid meet"></svg>
      <div class="sl-pinfo" aria-hidden="true"></div>
      <span class="sl-badge">LIVE-VORSCHAU</span><div class="sl-caption"><span></span><span></span></div></div>
    <div class="sl-status"><span class="sl-pulse" aria-hidden="true"></span><span class="sl-label" aria-live="polite"></span>
      <span class="sl-percent tnum"></span>
      <button type="button" class="sl-ptoggle" aria-pressed="true" hidden title="Bei jeder Aufnahme kurz zeigen, wie ihr Sternmuster auf die Referenz ausgerichtet wird (echte Registrierdaten)">Sternmuster</button><details class="sl-details"><summary>Details</summary>
        <div class="sl-pop"><b>Verarbeitungsverlauf</b><ol></ol><p class="mut sm">Zwischenbilder sind verkleinerte Vorschauen. Das fertige Bild erhält die vollständige Bearbeitung.</p></div></details></div>
    <div class="sl-track" role="progressbar" aria-label="Verarbeitungsfortschritt" aria-valuemin="0" aria-valuemax="100"><i></i></div></section>`;
  const root = host.firstElementChild, images = [...root.querySelectorAll('.sl-image')];
  const find = s => root.querySelector(s);
  let active = 0, wanted = '', shown = '', destroyed = false, stepSig = '', fallback = false;
  const pattern = createStarPattern(find('.sl-pattern'), find('.sl-pinfo'), find('.sl-ptoggle'), root, pid);
  function show(src, previous = false) {
    if (!src || src === shown || src === wanted) return;
    wanted = src;
    const next = images[1 - active];
    next.onload = () => {
      if (destroyed || wanted !== src) return;
      images[active].classList.remove('on');
      next.classList.add('on');
      next.setAttribute('aria-hidden', 'false');
      next.alt = previous ? 'Bisheriges Ergebnis – neue Verarbeitung läuft' : 'Echte Live-Vorschau des Astrofotos';
      images[active].setAttribute('aria-hidden', 'true');
      active = 1 - active; shown = src; fallback = previous;
      root.classList.add('has-picture');
      find('.sl-badge').textContent = previous ? 'BISHERIGES ERGEBNIS' : 'LIVE-VORSCHAU';
    };
    next.onerror = () => { if (wanted === src) wanted = ''; }; // nächster Poll versucht es erneut
    next.src = src;
  }
  return {
    update(p) {
      if (destroyed) return;
      const j = p.job || {}, live = p.live || {}, queued = j.state === 'queued';
      root.classList.toggle('sl-queued', queued);
      root.classList.toggle('sl-error', j.state === 'error');
      find('.sl-label').textContent = queued ? (j.message || 'In der Warteschlange') : j.message || j.stage || 'Verarbeitung startet';
      const percent = j.progress == null || queued ? null : Math.round(j.progress * 100);
      find('.sl-percent').textContent = percent == null ? '' : percent + ' %';
      const track = find('.sl-track');
      track.classList.toggle('indet', percent == null);
      if (percent == null) track.removeAttribute('aria-valuenow'); else track.setAttribute('aria-valuenow', percent);
      find('.sl-track i').style.width = (percent ?? 28) + '%';
      if (live.preview) show(`/api/stacking/projects/${pid}/live/${encodeURIComponent(live.preview)}`);
      else if (!shown && p.results?.[0]) show(`/api/stacking/results/${p.results[0].id}/jpg`, true);
      const caption = find('.sl-caption');
      caption.children[0].textContent = live.n != null ? `${live.n} Aufnahmen${live.band ? ' · Kanal ' + live.band : ''}` : '';
      caption.children[1].textContent = live.exposure != null ? dur(live.exposure) + ' integriert' : '';
      if (fallback && !live.preview) caption.children[0].textContent = 'Letzte fertige Version';
      pattern.update(j.stage === 'Stacking' && !queued ? live.pattern : null);
      const steps = live.steps?.length ? live.steps : [{ stage: j.stage || 'Vorbereitung', label: j.message || 'Verarbeitung wird vorbereitet', done: false }];
      const sig = JSON.stringify(steps);
      if (sig !== stepSig) {
        stepSig = sig;
        find('.sl-pop ol').innerHTML = steps.map(s => `<li class="${s.done ? 'done' : 'current'}"><span aria-hidden="true">${s.done ? '✓' : '·'}</span><div><small>${esc(s.stage)}</small><span>${esc(s.label)}</span></div></li>`).join('');
      }
    },
    destroy() { destroyed = true; pattern.destroy(); images.forEach(img => { img.onload = img.onerror = null; }); },
  };
}

/* Sternmuster der Registrierung: echte Daten aus registration.pattern (≤ alle 3 s eine Aufnahme, eigener Endpunkt).
   Je Aufnahme ein kurzer Puls: die übereinstimmenden Linien der Aufnahme erscheinen an ihrer Rohlage, drehen/verschieben
   sich mit der gefundenen Transformation auf die Sterne der Referenz (= Stack-Vorschau), leuchten kurz auf und verschwinden. */
function createStarPattern(svg, info, toggle, root, pid) {
  const NS = 'http://www.w3.org/2000/svg', reduce = matchMedia('(prefers-reduced-motion: reduce)');
  const nf = (v, d = 1) => Number(v).toLocaleString('de-DE', { maximumFractionDigits: d, minimumFractionDigits: d });
  const MOVE = 900, HOLD = 650, FADE = 700;
  let key = '', loading = '', raf = 0, timers = [], dead = false, active = false, on = store.get('live.sternmuster', true);
  const el = (tag, attrs, parent) => { const e = document.createElementNS(NS, tag); for (const k in attrs) e.setAttribute(k, attrs[k]); parent?.appendChild(e); return e; };
  const later = (fn, ms) => timers.push(setTimeout(() => { if (!dead) fn(); }, ms));
  const stop = () => { cancelAnimationFrame(raf); timers.forEach(clearTimeout); timers = []; };
  function apply() {
    root.classList.toggle('pattern-on', active && on);
    toggle.setAttribute('aria-pressed', String(on));
  }
  toggle.onclick = () => { on = !on; store.set('live.sternmuster', on); apply(); };
  function pulse(d) {
    stop();
    const fy = y => d.h - 1 - y, r = Math.max(d.w, d.h) / 320;   // fertiges Bild ist vertikal gespiegelt (Norden oben)
    svg.setAttribute('viewBox', `0 0 ${d.w} ${d.h}`);
    svg.textContent = '';
    const g = el('g', {}, svg);
    // nur deckungsgleiche Linien und ihre Sterne – das Muster, an dem die Aufnahme ausgerichtet wurde
    const edges = d.tgt_edges.filter(e => e[2]);
    const used = [...new Set(edges.flatMap(([a, b]) => [a, b]))];
    const lines = edges.map(([a, b]) => [a, b, el('line', {}, g)]);
    const dots = used.map(i => [i, el('circle', { r }, g)]);
    const seed = d.seed_tgt.length === 3 ? el('polygon', { class: 'seed' }, g) : null;
    const [a, b, tx, c, e, ty] = d.m, rot = Math.atan2(c, a), sc = Math.hypot(a, c);
    const pts = used.map(i => d.tgt[i]), n = Math.max(pts.length, 1);
    const cx = pts.reduce((s, p) => s + p[0], 0) / n, cy = pts.reduce((s, p) => s + p[1], 0) / n;
    const gx = a * cx + b * cy + tx, gy = c * cx + e * cy + ty;
    const at = (t) => {                                          // starr: Drehung, Maßstab, Versatz anteilig
      const ro = rot * t, s = 1 + (sc - 1) * t, co = Math.cos(ro) * s, si = Math.sin(ro) * s;
      const ox = cx + (gx - cx) * t, oy = cy + (gy - cy) * t;
      return d.tgt.map(([x, y]) => [co * (x - cx) - si * (y - cy) + ox, fy(si * (x - cx) + co * (y - cy) + oy)]);
    };
    const set = (t) => {
      const p = at(t);
      lines.forEach(([i, j, l]) => { l.setAttribute('x1', p[i][0]); l.setAttribute('y1', p[i][1]); l.setAttribute('x2', p[j][0]); l.setAttribute('y2', p[j][1]); });
      dots.forEach(([i, c]) => { c.setAttribute('cx', p[i][0]); c.setAttribute('cy', p[i][1]); });
      if (seed) seed.setAttribute('points', d.seed_tgt.map(i => p[i].join(',')).join(' '));
    };
    info.textContent = `Ausrichtung${d.n && d.total ? ` ${d.n}/${d.total}` : ''} · Drehung ${nf(d.rot, 2)}° · Versatz ${nf(Math.hypot(...d.shift))} px`;
    svg.classList.remove('aligned');
    active = true; apply();
    const end = () => { svg.classList.add('aligned'); later(() => { active = false; apply(); }, reduce.matches ? 1200 : HOLD); };
    if (reduce.matches) { set(1); return end(); }
    set(0);
    const t0 = performance.now() + 250;
    const step = (now) => {
      if (dead) return;
      const t = Math.min(1, Math.max(0, (now - t0) / MOVE)), k = t < .5 ? 2 * t * t : 1 - (-2 * t + 2) ** 2 / 2;
      set(k);
      if (t < 1) raf = requestAnimationFrame(step); else end();
    };
    raf = requestAnimationFrame(step);
  }
  return {
    async update(k) {
      if (dead) return;
      if (!k) { stop(); active = false; apply(); toggle.hidden = true; return; }
      toggle.hidden = false;
      if (k === key || k === loading) return;
      loading = k;
      try {
        const d = await api(`/stacking/projects/${pid}/pattern`);
        if (dead || loading !== k) return;
        key = d.key;
        if (on) pulse(d);
      } catch { /* nächster Poll versucht es erneut */ } finally { if (loading === k) loading = ''; }
    },
    destroy() { dead = true; stop(); },
  };
}
