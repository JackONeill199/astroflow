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

/* Sternmuster der Registrierung – „Sterne rasten ein“: je registrierter Aufnahme erscheinen ihre hellsten zugeordneten
   Sterne als kleine weiche Punkte dort, wo sie in der Aufnahme lagen, gleiten in ~150 ms auf ihre Stelle im Stack
   (Transformation der Registrierung), rasten leise ein und blenden aus. Daten: registration.pulse → Ringpuffer im
   Speicher, GET /pattern?seit=. Die seit dem letzten Abruf neuen Aufnahmen werden über das Abrufintervall verteilt
   abgespielt (Takt = tatsächliche Stack-Rate). Bewegung nur per CSS-transform, im Hintergrund-Tab pausiert. */
function createStarPattern(svg, info, toggle, root, pid) {
  const NS = 'http://www.w3.org/2000/svg', reduce = matchMedia('(prefers-reduced-motion: reduce)');
  const POLL = 2500, MIN_SLOT = 150, MAX_SLOT = 700, MAX_PULSE = 480;
  const nf = (v, d = 1) => Number(v).toLocaleString('de-DE', { maximumFractionDigits: d, minimumFractionDigits: d });
  let run = '', seit = 0, wh = null, queue = [], slot = MAX_SLOT, timer = 0, fade = 0, snap = 0, busy = false, dead = false, live = false;
  let on = store.get('live.sternmuster', true);
  const flip = svg.appendChild(document.createElementNS(NS, 'g'));
  const stop = () => {
    clearTimeout(timer); clearTimeout(fade); clearTimeout(snap);
    timer = fade = snap = 0; queue = []; root.classList.remove('pattern-on', 'pattern-play');
  };
  toggle.onclick = () => { on = !on; store.set('live.sternmuster', on); toggle.setAttribute('aria-pressed', String(on)); if (!on) stop(); };
  toggle.setAttribute('aria-pressed', String(on));
  function draw(d, moving) {
    flip.textContent = '';
    svg.classList.remove('go', 'snap');
    const box = svg.getBoundingClientRect();              // Radius ≈ 3,5 Bildschirmpixel, unabhängig von der Bildgröße
    const r = 3.5 * Math.max(wh[0] / Math.max(box.width, 1), wh[1] / Math.max(box.height, 1));
    svg.style.setProperty('--r', String(r));
    for (const [fx, fy, sx, sy] of d.s) {
      const c = document.createElementNS(NS, 'circle');
      c.setAttribute('cx', sx); c.setAttribute('cy', sy); c.setAttribute('r', r);
      if (moving) c.style.transform = `translate(${fx - sx}px, ${fy - sy}px)`;   // Start: Lage in der Aufnahme
      flip.appendChild(c);
    }
    info.textContent = `${d.n && d.t ? `${d.n}/${d.t} · ` : ''}Drehung ${nf(d.r, 1)}°`;
  }
  function play() {
    timer = 0;
    const d = queue.shift();
    if (!d || dead || !on || document.hidden) { root.classList.remove('pattern-on', 'pattern-play'); return; }
    clearTimeout(fade); clearTimeout(snap);
    const moving = !reduce.matches;
    draw(d, moving);
    root.classList.add('pattern-play', 'pattern-on');
    if (!moving) {                                        // ohne Bewegung: nur kurz am Ziel aufleuchten
      svg.classList.add('snap');
      fade = setTimeout(() => { if (!dead) root.classList.remove('pattern-on'); }, 700);
      timer = setTimeout(play, POLL); return;
    }
    const dur = Math.min(slot * 0.9, MAX_PULSE);
    if (flip.firstChild) getComputedStyle(flip.firstChild).transform;   // Startlage festschreiben, dann gleiten (CSS 150 ms)
    svg.classList.add('go');
    snap = setTimeout(() => { if (!dead) svg.classList.add('snap'); }, Math.min(170, dur * 0.45));       // einrasten
    fade = setTimeout(() => { if (!dead) root.classList.remove('pattern-on'); }, Math.max(dur - 90, 260));
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
