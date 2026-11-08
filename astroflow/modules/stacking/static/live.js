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
      pattern.update(j.stage === 'Stacking' && !queued);
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

/* Sternmuster der Registrierung im Sternbild-Stil: je registrierter Aufnahme die hellsten zugeordneten Sterne, verbunden
   per minimalem Spannbaum (registration.pulse → Ringpuffer im Speicher, GET /pattern?seit=). Die seit dem letzten Abruf
   neuen Aufnahmen werden über das Abrufintervall verteilt abgespielt (Takt = tatsächliche Stack-Rate): das Muster gleitet in
   ~150 ms an seine Stelle auf der Stack-Vorschau, die Ringe rasten kurz ein, leuchten schwach nach und blenden aus.
   Nur transform/opacity, im Hintergrund-Tab pausiert. */
function createStarPattern(svg, info, toggle, root, pid) {
  const NS = 'http://www.w3.org/2000/svg', reduce = matchMedia('(prefers-reduced-motion: reduce)');
  const POLL = 2500, MIN_SLOT = 150, MAX_SLOT = 700, GLIDE = 150, MAX_PULSE = 520;
  const nf = (v, d = 1) => Number(v).toLocaleString('de-DE', { maximumFractionDigits: d, minimumFractionDigits: d });
  let run = '', seit = 0, wh = null, queue = [], slot = MAX_SLOT, timer = 0, fade = 0, raf = 0, busy = false, dead = false, live = false;
  let on = store.get('live.sternmuster', true);
  const flip = svg.appendChild(document.createElementNS(NS, 'g')), g = flip.appendChild(document.createElementNS(NS, 'g'));
  const el = (tag, attrs, parent) => { const e = document.createElementNS(NS, tag); for (const k in attrs) e.setAttribute(k, attrs[k]); parent.appendChild(e); return e; };
  const stop = () => { clearTimeout(timer); clearTimeout(fade); cancelAnimationFrame(raf); timer = fade = raf = 0; queue = []; root.classList.remove('pattern-on', 'pattern-play'); };
  toggle.onclick = () => { on = !on; store.set('live.sternmuster', on); toggle.setAttribute('aria-pressed', String(on)); if (!on) stop(); };
  toggle.setAttribute('aria-pressed', String(on));
  function draw(d) {
    g.textContent = '';
    const r = Math.max(...wh) / 180;                     // Ring etwas größer als ein Stern
    for (const [a, b] of d.e) el('line', { x1: d.p[a][0], y1: d.p[a][1], x2: d.p[b][0], y2: d.p[b][1] }, g);
    for (const [x, y] of d.p) el('circle', { cx: x, cy: y, r }, g);
    info.textContent = `${d.n && d.t ? `${d.n}/${d.t} · ` : ''}Drehung ${nf(d.r, 1)}°`;
  }
  function matrixAt(d, t) {                             // starr: Drehung, Maßstab, Versatz anteilig um den Schwerpunkt
    const [a, b, tx, c, e, ty] = d.m, n = Math.max(d.p.length, 1);
    const cx = d.p.reduce((s, p) => s + p[0], 0) / n, cy = d.p.reduce((s, p) => s + p[1], 0) / n;
    const ro = Math.atan2(c, a) * t, sc = 1 + (Math.hypot(a, c) - 1) * t, co = Math.cos(ro) * sc, si = Math.sin(ro) * sc;
    const ox = cx + (a * cx + b * cy + tx - cx) * t, oy = cy + (c * cx + e * cy + ty - cy) * t;
    return `matrix(${co} ${si} ${-si} ${co} ${ox - co * cx + si * cy} ${oy - si * cx - co * cy})`;
  }
  function play() {
    timer = 0;
    const d = queue.shift();
    if (!d || dead || !on || document.hidden) { root.classList.remove('pattern-on', 'pattern-play'); return; }
    clearTimeout(fade); cancelAnimationFrame(raf);
    draw(d);
    root.classList.add('pattern-play');
    if (reduce.matches) {                                // ohne Bewegung: nur die jüngste Aufnahme, ausgerichtet
      g.setAttribute('transform', matrixAt(d, 1)); svg.classList.add('aligned'); root.classList.add('pattern-on');
      timer = setTimeout(play, POLL); return;
    }
    const dur = Math.min(slot * 0.9, MAX_PULSE), glide = Math.min(GLIDE, dur * 0.4), t0 = performance.now();
    svg.classList.remove('aligned');
    g.setAttribute('transform', matrixAt(d, 0));
    root.classList.add('pattern-on');
    const step = (now) => {
      if (dead) return;
      const t = Math.min(1, (now - t0) / glide), k = 1 - (1 - t) ** 3;
      g.setAttribute('transform', matrixAt(d, k));
      if (t < 1) raf = requestAnimationFrame(step); else svg.classList.add('aligned');   // einrasten + nachleuchten (CSS)
    };
    raf = requestAnimationFrame(step);
    fade = setTimeout(() => { if (!dead) root.classList.remove('pattern-on'); }, Math.max(dur - 90, glide + 60));
    timer = setTimeout(play, slot);
  }
  function enqueue(items) {
    if (reduce.matches) items = items.slice(-1);
    const most = Math.floor(POLL / MIN_SLOT);           // sehr hohe Rate: gleichmäßig auslassen statt hinterherzuhinken
    if (items.length > most) items = Array.from({ length: most }, (_, i) => items[Math.round(i * (items.length - 1) / (most - 1))]);
    if (!items.length) return;
    queue = items;                                       // Neueres ersetzt, was noch wartet – nie Rückstand
    slot = Math.max(MIN_SLOT, Math.min(MAX_SLOT, POLL / items.length));
    if (!timer) play();
  }
  return {
    async update(stacking) {
      if (dead) return;
      toggle.hidden = !stacking;
      if (live && !stacking) stop();
      live = stacking;
      if (!stacking || !on || busy || document.hidden) return;
      busy = true;
      try {
        const res = await api(`/stacking/projects/${pid}/pattern?seit=${seit}`);
        if (dead || !live) return;
        let items = res.items || [];
        if (res.run !== run) { run = res.run; items = items.slice(-4); }   // neu geöffnet: nur das Jüngste, nichts nachholen
        seit = res.seq || 0;
        if (res.wh) {                                    // fertiges Bild ist vertikal gespiegelt (Norden oben)
          wh = res.wh;
          svg.setAttribute('viewBox', `0 0 ${wh[0]} ${wh[1]}`);
          flip.setAttribute('transform', `matrix(1 0 0 -1 0 ${wh[1] - 1})`);
        }
        if (wh) enqueue(items);
      } catch { /* nächster Poll versucht es erneut */ } finally { busy = false; }
    },
    destroy() { dead = true; stop(); },
  };
}
