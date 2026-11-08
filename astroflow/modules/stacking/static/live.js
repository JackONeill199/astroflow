/* Echte Zwischenbilder: zwei Ebenen für weiche Wechsel, Status ohne Neuaufbau des Viewers. */
function createStackLive(host, pid) {
  host.innerHTML = `<section class="stack-live" aria-label="Live-Verarbeitung">
    <div class="sl-picture"><img class="sl-image" alt="Live-Vorschau des Astrofotos"><img class="sl-image" alt="" aria-hidden="true">
      <div class="sl-wait"><span class="sl-orbit" aria-hidden="true"></span><span>Noch keine Bildvorschau</span></div>
      <svg class="sl-pattern" aria-hidden="true" preserveAspectRatio="xMidYMid meet"></svg>
      <div class="sl-pinfo" hidden><b>Sternmuster · Visualisierung</b><span></span></div>
      <span class="sl-badge">LIVE-VORSCHAU</span><div class="sl-caption"><span></span><span></span></div></div>
    <div class="sl-status"><span class="sl-pulse" aria-hidden="true"></span><span class="sl-label" aria-live="polite"></span>
      <span class="sl-percent tnum"></span>
      <button type="button" class="sl-ptoggle" aria-pressed="true" hidden title="Sternmuster der Registrierung ein-/ausblenden">Sternmuster</button><details class="sl-details"><summary>Details</summary>
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
   Das Netz der Referenz liegt fest über dem Stack, das Netz der neuen Aufnahme dreht/verschiebt sich mit der gefundenen
   Transformation auf die Referenz; Kanten, die danach auf einer Referenzkante liegen, leuchten auf. */
function createStarPattern(svg, info, toggle, root, pid) {
  const NS = 'http://www.w3.org/2000/svg', reduce = matchMedia('(prefers-reduced-motion: reduce)');
  const nf = (v, d = 1) => Number(v).toLocaleString('de-DE', { maximumFractionDigits: d, minimumFractionDigits: d });
  let key = '', loading = '', data = null, raf = 0, dead = false, on = store.get('live.sternmuster', true);
  const el = (tag, attrs, parent) => { const e = document.createElementNS(NS, tag); for (const k in attrs) e.setAttribute(k, attrs[k]); parent?.appendChild(e); return e; };
  function apply() {
    root.classList.toggle('pattern-on', !!data && on);
    toggle.setAttribute('aria-pressed', String(on));
    info.hidden = !data || !on;
  }
  toggle.onclick = () => { on = !on; store.set('live.sternmuster', on); apply(); };
  function draw(d) {
    cancelAnimationFrame(raf);
    const H = d.h, fy = y => H - 1 - y, r = Math.max(d.w, d.h) / 260;   // fertiges Bild ist vertikal gespiegelt (Norden oben)
    svg.setAttribute('viewBox', `0 0 ${d.w} ${d.h}`);
    svg.textContent = '';
    const gr = el('g', { class: 'ref' }, svg), gt = el('g', { class: 'tgt' }, svg);
    for (const [a, b] of d.ref_edges) el('line', { x1: d.ref[a][0], y1: fy(d.ref[a][1]), x2: d.ref[b][0], y2: fy(d.ref[b][1]) }, gr);
    for (const [x, y] of d.ref) el('circle', { cx: x, cy: fy(y), r }, gr);
    if (d.seed_ref.length === 3) el('polygon', { class: 'seed', points: d.seed_ref.map(i => `${d.ref[i][0]},${fy(d.ref[i][1])}`).join(' ') }, gr);
    const lines = d.tgt_edges.map(([a, b, m]) => [a, b, el('line', { class: m ? 'm' : '' }, gt)]);
    const dots = d.tgt.map(() => el('circle', { r: r * 0.8 }, gt));
    const seed = d.seed_tgt.length === 3 ? el('polygon', { class: 'seed' }, gt) : null;
    // Transformation zerlegen und von „ungedreht, an der Rohposition“ bis zur gefundenen Lage überblenden (starr)
    const [a, b, tx, c, e, ty] = d.m, rot = Math.atan2(c, a), sc = Math.hypot(a, c);
    const cx = d.tgt.reduce((s, p) => s + p[0], 0) / d.tgt.length, cy = d.tgt.reduce((s, p) => s + p[1], 0) / d.tgt.length;
    const gx = a * cx + b * cy + tx, gy = c * cx + e * cy + ty;
    const at = (t) => {
      const ro = rot * t, s = 1 + (sc - 1) * t, co = Math.cos(ro) * s, si = Math.sin(ro) * s;
      const ox = cx + (gx - cx) * t, oy = cy + (gy - cy) * t;
      return d.tgt.map(([x, y]) => [co * (x - cx) - si * (y - cy) + ox, fy(si * (x - cx) + co * (y - cy) + oy)]);
    };
    const set = (t) => {
      const p = at(t);
      lines.forEach(([i, j, l]) => { l.setAttribute('x1', p[i][0]); l.setAttribute('y1', p[i][1]); l.setAttribute('x2', p[j][0]); l.setAttribute('y2', p[j][1]); });
      dots.forEach((c, i) => { c.setAttribute('cx', p[i][0]); c.setAttribute('cy', p[i][1]); });
      if (seed) seed.setAttribute('points', d.seed_tgt.map(i => p[i].join(',')).join(' '));
    };
    const done = () => { set(1); svg.classList.add('aligned'); };
    svg.classList.remove('aligned', 'enter');
    if (reduce.matches) return done();
    set(0);
    void svg.getBoundingClientRect();
    svg.classList.add('enter');
    const t0 = performance.now() + 450, dur = 1500;
    const step = (now) => {
      if (dead) return;
      const t = Math.min(1, Math.max(0, (now - t0) / dur)), k = t < .5 ? 2 * t * t : 1 - (-2 * t + 2) ** 2 / 2;
      set(k);
      if (t < 1) raf = requestAnimationFrame(step); else done();
    };
    raf = requestAnimationFrame(step);
  }
  function text(d) {
    const m = d.tgt_edges.filter(x => x[2]).length;
    info.lastElementChild.textContent = [d.n && d.total ? `Aufnahme ${d.n}/${d.total}` : '', `${d.n_pairs} Sternpaare`,
      `${m}/${d.tgt_edges.length} Kanten deckungsgleich`, `Drehung ${nf(d.rot, 2)}°`,
      `Versatz ${nf(Math.hypot(...d.shift))} px`, d.rms != null ? `Rest ${nf(d.rms, 2)} px` : ''].filter(Boolean).join(' · ');
  }
  return {
    async update(k) {
      if (dead) return;
      if (!k) { if (data) { data = null; key = ''; svg.textContent = ''; apply(); } toggle.hidden = true; return; }
      toggle.hidden = false;
      if (k === key || k === loading) return;
      loading = k;
      try {
        const d = await api(`/stacking/projects/${pid}/pattern`);
        if (dead || loading !== k) return;
        key = d.key; data = d;
        text(d); draw(d); apply();
      } catch { /* nächster Poll versucht es erneut */ } finally { if (loading === k) loading = ''; }
    },
    destroy() { dead = true; cancelAnimationFrame(raf); },
  };
}
