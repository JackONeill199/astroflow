/* Echte Zwischenbilder: zwei Ebenen für weiche Wechsel, Status ohne Neuaufbau des Viewers.
   Während des Stackens optional als 3D-Raum (createFrameSpace): Aufnahmen als Ebenen entlang einer Zeitachse, die auf
   die Stack-Ebene zufliegen; darunter eine Zeitachse mit den gespeicherten Stacking-Stufen (1, 3, 10, 30, 50, 100 …). */
const LIVE_2D3D_MIN = 768;                          // schmaler: immer 2D
function createStackLive(host, pid) {
  host.innerHTML = `<section class="stack-live" aria-label="Live-Verarbeitung">
    <div class="sl-picture"><img class="sl-image" alt="Live-Vorschau des Astrofotos"><img class="sl-image" alt="" aria-hidden="true">
      <div class="sl-wait"><span class="sl-orbit" aria-hidden="true"></span><span>Noch keine Bildvorschau</span></div>
      <div class="sl-space" aria-hidden="true"></div>
      <div class="sl-pinfo" aria-hidden="true"></div>
      <span class="sl-badge">LIVE-VORSCHAU</span><div class="sl-caption"><span></span><span></span></div></div>
    <div class="sl-time" hidden><span class="sl-time-h">Zeitachse</span><div class="sl-chips" role="group" aria-label="Stack nach Anzahl Aufnahmen"></div></div>
    <div class="sl-status"><span class="sl-pulse" aria-hidden="true"></span><span class="sl-label" aria-live="polite"></span>
      <span class="sl-percent tnum"></span>
      <button type="button" class="sl-mode" aria-pressed="false" hidden title="Aufnahmen als Ebenen im Raum zeigen (echte Registrierdaten)">3D</button><details class="sl-details"><summary>Details</summary>
        <div class="sl-pop"><b>Verarbeitungsverlauf</b><ol></ol><p class="mut sm">Zwischenbilder sind verkleinerte Vorschauen. Das fertige Bild erhält die vollständige Bearbeitung.</p></div></details></div>
    <div class="sl-track" role="progressbar" aria-label="Verarbeitungsfortschritt" aria-valuemin="0" aria-valuemax="100"><i></i></div></section>`;
  const root = host.firstElementChild, images = [...root.querySelectorAll('.sl-image')];
  const find = s => root.querySelector(s);
  let active = 0, wanted = '', shown = '', destroyed = false, stepSig = '', fallback = false;
  let liveSrc = '', pinned = null, stageSig = '', stageAt = 0, stageN = null, stacking = false;
  const space = createFrameSpace(find('.sl-space'), find('.sl-pinfo'), root, pid);
  const modeBtn = find('.sl-mode');
  const reduce = matchMedia('(prefers-reduced-motion: reduce)');
  const wide = () => root.clientWidth >= LIVE_2D3D_MIN;
  let mode = store.get('live.ansicht', '');             // '' = automatisch (3D ab 768 px, sonst 2D)
  const want3d = () => stacking && wide() && !space.failed && (mode === '3d' || (!mode && !reduce.matches));
  function applyMode() {
    const on = want3d();
    modeBtn.hidden = !stacking || !wide() || space.failed;
    modeBtn.setAttribute('aria-pressed', String(on));
    root.classList.toggle('mode-3d', on);
    space.active(on);
  }
  modeBtn.onclick = () => { mode = want3d() ? '2d' : '3d'; store.set('live.ansicht', mode); applyMode(); };
  function show(src, previous = false) {
    if (!src) return;
    if (src === shown) { wanted = src; return; }        // zurück zum schon gezeigten Bild: ausstehenden Wechsel verwerfen
    if (src === wanted) return;
    wanted = src;
    const next = images[1 - active];
    next.onload = () => {
      if (destroyed || wanted !== src) return;
      images[active].classList.remove('on');
      next.classList.add('on');
      next.setAttribute('aria-hidden', 'false');
      next.alt = previous ? 'Bisheriges Ergebnis – neue Verarbeitung läuft' : pinned ? `Stack nach ${pinned.n} Aufnahmen` : 'Echte Live-Vorschau des Astrofotos';
      images[active].setAttribute('aria-hidden', 'true');
      active = 1 - active; shown = src; fallback = previous;
      root.classList.add('has-picture');
      find('.sl-badge').textContent = previous ? 'BISHERIGES ERGEBNIS' : pinned ? `NACH ${pinned.n} AUFNAHMEN` : 'LIVE-VORSCHAU';
    };
    next.onerror = () => { if (wanted === src) wanted = ''; }; // nächster Poll versucht es erneut
    next.src = src;
  }
  const stageSrc = st => `/api/stacking/projects/${pid}/stages/${st.file}${st.band ? '?band=' + encodeURIComponent(st.band) : ''}`;
  function picture(src, previous) { show(src, previous); space.stack(src); }
  function pin(st) {
    pinned = st;
    find('.sl-chips').querySelectorAll('button').forEach(b => b.setAttribute('aria-pressed', String(st ? b.dataset.k === `${st.band}|${st.n}` : b.dataset.k === 'live')));
    picture(st ? stageSrc(st) : liveSrc);
  }
  find('.sl-chips').addEventListener('click', (e) => {
    const b = e.target.closest('button');
    if (!b) return;
    const st = b.dataset.k === 'live' ? null : JSON.parse(b.dataset.st);
    pin(st);
  });
  async function loadStages(n) {                         // nur wenn sich die Aufnahmenzahl ändert, höchstens alle 5 s
    if (n === stageN && Date.now() - stageAt < 30000) return;
    if (Date.now() - stageAt < 5000) return;
    stageAt = Date.now(); stageN = n;
    try {
      const { stages } = await api(`/stacking/projects/${pid}/stages`);
      if (destroyed) return;
      const sig = JSON.stringify(stages);
      if (sig === stageSig) return;
      stageSig = sig;
      const bands = new Set(stages.map(s => s.band)).size > 1;
      find('.sl-time').hidden = !stages.length;
      find('.sl-chips').innerHTML = `<button type="button" data-k="live" aria-pressed="${!pinned}">Live</button>` + stages.map(s =>
        `<button type="button" class="tnum" data-k="${esc(s.band)}|${s.n}" data-st='${esc(JSON.stringify(s))}' aria-pressed="${pinned?.n === s.n && pinned?.band === s.band}"
          title="Stack nach ${s.n} Aufnahmen${s.exposure ? ' · ' + esc(dur(s.exposure)) : ''}">${bands && s.band ? esc(s.band) + ' ' : ''}${s.n}</button>`).join('');
    } catch { /* nächster Poll */ }
  }
  addEventListener('resize', applyMode);
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
      stacking = j.stage === 'Stacking' && !queued;
      applyMode();
      if (live.preview) {
        liveSrc = `/api/stacking/projects/${pid}/live/${encodeURIComponent(live.preview)}`;
        if (!pinned) picture(liveSrc);
      } else if (!shown && p.results?.[0]) show(`/api/stacking/results/${p.results[0].id}/jpg`, true);
      const caption = find('.sl-caption');
      caption.children[0].textContent = live.n != null ? `${live.n} Aufnahmen${live.band ? ' · Kanal ' + live.band : ''}` : '';
      caption.children[1].textContent = live.exposure != null ? dur(live.exposure) + ' integriert' : '';
      if (fallback && !live.preview) caption.children[0].textContent = 'Letzte fertige Version';
      if (live.n != null || !find('.sl-time').hidden) loadStages(live.n);
      space.update(stacking);
      const steps = live.steps?.length ? live.steps : [{ stage: j.stage || 'Vorbereitung', label: j.message || 'Verarbeitung wird vorbereitet', done: false }];
      const sig = JSON.stringify(steps);
      if (sig !== stepSig) {
        stepSig = sig;
        find('.sl-pop ol').innerHTML = steps.map(s => `<li class="${s.done ? 'done' : 'current'}"><span aria-hidden="true">${s.done ? '✓' : '·'}</span><div><small>${esc(s.stage)}</small><span>${esc(s.label)}</span></div></li>`).join('');
      }
    },
    destroy() { destroyed = true; removeEventListener('resize', applyMode); space.destroy(); images.forEach(img => { img.onload = img.onerror = null; }); },
  };
}

/* Aufnahmen als Ebenen im Raum (three.js aus /vendor, erst geladen, wenn 3D sichtbar wird). Vorne die Stack-Ebene
   (= Live-Vorschau bzw. gewählte Stufe), dahinter entlang der Zeitachse die nächsten registrierten Aufnahmen
   (registration.pulse + Mini-Vorschau live.thumb, Ringpuffer im Speicher, GET /pattern?seit=). Im Stack-Takt fliegt die
   vorderste nach vorn, dreht/verschiebt sich dabei mit der echten Transformation der Registrierung auf die Referenz, ihre
   hellsten Sterne rasten als Punkte ein, dann verschmilzt sie mit dem Stack. Ruhig: keine Partikel, kein Autodreh. */
function createFrameSpace(host, info, root, pid) {
  const reduce = matchMedia('(prefers-reduced-motion: reduce)');
  const POLL = 2500, MIN_SLOT = 260, MAX_SLOT = 1100, GAP = 0.13, BEHIND = 9, WITH_IMAGE = 5;
  const nf = (v, d = 1) => Number(v).toLocaleString('de-DE', { maximumFractionDigits: d, minimumFractionDigits: d });
  let T = null, renderer = null, scene, camera, controls, loader, stackA, stackB, front = 0, stackSrc = '', wanted = '';
  let on = false, loading = null, dead = false, live = false, busy = false, run = '', seit = 0, wh = null;
  let queue = [], slot = MAX_SLOT, timer = 0, raf = 0, anims = [], flown = 0;
  const api3 = { failed: false };
  let dotTex = null;
  function frameMesh(d) {                                // eine Aufnahme = dünne Ebene (Bild oder getönte Platte)
    const a = wh[1] / wh[0];
    const geo = new T.PlaneGeometry(1, a);
    const mat = new T.MeshBasicMaterial({ color: 0x9fb4ff, transparent: true, opacity: 0, depthWrite: false, side: T.DoubleSide });
    const mesh = new T.Mesh(geo, mat);
    const edge = new T.LineSegments(new T.EdgesGeometry(geo), new T.LineBasicMaterial({ color: 0x8ea2ff, transparent: true, opacity: 0 }));
    mesh.add(edge);
    mesh.userData = { d, edge, target: 0.16, plate: true };
    return mesh;
  }
  function giveImage(mesh) {                             // nur die vordersten Ebenen bekommen ihr echtes Bild
    const u = mesh.userData;
    if (!u.plate || u.loading || !u.d.img) return;
    u.loading = true;
    loader.load(`/api/stacking/projects/${pid}/pattern/${u.d.i}.jpg`, (tex) => {
      if (dead || !mesh.parent) { tex.dispose(); return; }
      tex.colorSpace = T.SRGBColorSpace;
      mesh.material.map = tex; mesh.material.color.set(0xffffff); mesh.material.needsUpdate = true;
      u.plate = false; u.target = 0.78; render();
    }, undefined, () => { u.loading = false; });
  }
  function layout(instant) {                             // wartende Aufnahmen hintereinander auf der Zeitachse
    queue.forEach((m, k) => {
      const z = -GAP * (k + 1), u = m.userData;
      if (k < WITH_IMAGE) giveImage(m);
      const op = k < BEHIND ? (u.plate ? 0.16 : 0.78) * (1 - k / (BEHIND + 2)) : 0;
      if (instant) { m.position.z = z; m.material.opacity = op; u.edge.material.opacity = op * 0.35; }
      else tween(m, { z, op }, Math.min(slot * 0.55, 420));
    });
  }
  function tween(m, to, ms) {
    const from = { z: m.position.z, op: m.material.opacity };
    anims = anims.filter(a => a.m !== m || a.kind !== 'slide');
    anims.push({ m, kind: 'slide', t0: performance.now(), ms, step(k) {
      m.position.z = from.z + (to.z - from.z) * k;
      m.material.opacity = from.op + (to.op - from.op) * k;
      m.userData.edge.material.opacity = m.material.opacity * 0.35;
    } });
    loop();
  }
  function place(m, t) {                                 // Transformation Aufnahme → Stack anteilig (Drehung, Maßstab, Versatz)
    const [a, b, tx, c, e, ty] = m.userData.d.m, W = wh[0], H = wh[1];
    const rot = Math.atan2(c, a) * t, sc = 1 + (Math.hypot(a, c) - 1) * t, co = Math.cos(rot) * sc, si = Math.sin(rot) * sc;
    const cx = W / 2, cy = H / 2;
    m.rotation.z = rot; m.scale.set(sc, sc, 1);
    m.position.x = (co * cx - si * cy + tx * t - cx) / W;
    m.position.y = (si * cx + co * cy + ty * t - cy) / W;
  }
  function arrive(m) {                                   // vorderste Ebene: nach vorn, ausrichten, Sterne rasten ein, verschmelzen
    const d = m.userData.d, W = wh[0], H = wh[1], dur = Math.min(slot * 0.92, 1000), move = dur * 0.55;
    const pts = new Float32Array(d.s.length * 3);
    d.s.forEach(([fx, fy], k) => { pts[k * 3] = (fx - W / 2) / W; pts[k * 3 + 1] = (fy - H / 2) / W; pts[k * 3 + 2] = 0.002; });
    const g = new T.BufferGeometry(); g.setAttribute('position', new T.BufferAttribute(pts, 3));
    const dots = new T.Points(g, new T.PointsMaterial({ color: 0x9db8ff, map: softDot(), size: 9, sizeAttenuation: false, transparent: true, opacity: 0, depthWrite: false }));
    m.add(dots);
    const z0 = m.position.z, op0 = Math.max(m.material.opacity, 0.5);
    info.textContent = `${d.n && d.t ? `Aufnahme ${d.n}/${d.t} · ` : ''}Drehung ${nf(d.r, 1)}° · ${d.q} Sternpaare`;
    root.classList.add('space-info');
    anims.push({ m, kind: 'arrive', t0: performance.now(), ms: dur, step(k) {
      const t = Math.min(1, k * dur / move), ease = 1 - (1 - t) ** 3;
      m.position.z = z0 + (0.003 - z0) * ease;
      place(m, ease);
      const fade = k * dur < move ? 1 : 1 - (k * dur - move) / (dur - move);
      m.material.opacity = op0 * fade;
      m.userData.edge.material.opacity = 0.3 * fade;
      dots.material.opacity = t < 0.6 ? 0 : Math.min(1, (t - 0.6) / 0.3) * fade * 0.9;
      dots.material.size = t >= 1 && k * dur < move + 120 ? 7 : 9;      // kurzes Einrasten: kleiner und heller
    }, done() { scene.remove(m); dispose(m); flown++; root.dataset.flown = flown; } });
    loop();
  }
  function softDot() {                                  // weicher runder Punkt statt Quadrat
    if (dotTex) return dotTex;
    const c = Object.assign(document.createElement('canvas'), { width: 32, height: 32 }), x = c.getContext('2d');
    const g = x.createRadialGradient(16, 16, 0, 16, 16, 16);
    g.addColorStop(0, 'rgba(255,255,255,1)'); g.addColorStop(0.35, 'rgba(255,255,255,.75)'); g.addColorStop(1, 'rgba(255,255,255,0)');
    x.fillStyle = g; x.fillRect(0, 0, 32, 32);
    return (dotTex = new T.CanvasTexture(c));
  }
  function dispose(m) {
    m.traverse(o => { o.geometry?.dispose(); if (o.material) { if (o.material.map !== dotTex) o.material.map?.dispose(); o.material.dispose(); } });
  }
  function play() {
    timer = 0;
    if (dead || !on || document.hidden || !queue.length) { root.classList.remove('space-info'); return; }
    const m = queue.shift();
    arrive(m);
    layout(false);
    timer = setTimeout(play, slot);
  }
  function enqueue(items) {
    const most = Math.floor(POLL / MIN_SLOT);            // sehr hohe Rate: gleichmäßig auslassen statt hinterherzuhinken
    if (items.length > most) items = Array.from({ length: most }, (_, i) => items[Math.round(i * (items.length - 1) / (most - 1))]);
    if (!items.length) return;
    queue.forEach(m => { scene.remove(m); dispose(m); });   // Neueres ersetzt, was noch wartet – nie Rückstand
    queue = items.map(d => { const m = frameMesh(d); m.position.z = -GAP * (BEHIND + 1); scene.add(m); return m; });
    slot = Math.max(MIN_SLOT, Math.min(MAX_SLOT, POLL / items.length));
    if (reduce.matches) { layout(true); render(); return; }   // ruhiges Standbild: Ebenen stehen, nichts fliegt
    layout(false);
    if (!timer) timer = setTimeout(play, 200);
  }
  function loop() {
    if (raf || dead) return;
    const tick = (now) => {
      raf = 0;
      anims = anims.filter(a => {
        const k = Math.min(1, (now - a.t0) / a.ms);
        a.step(k);
        if (k >= 1) { a.done?.(); return false; }
        return true;
      });
      controls?.update();
      renderer.render(scene, camera);
      if (anims.length || controls?.moving) raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
  }
  function render() { if (renderer) loop(); }
  function setStack(src) {
    if (!renderer || !src) return;
    if (src === stackSrc) { wanted = ''; return; }      // zurück zum schon gezeigten Bild: ausstehenden Wechsel verwerfen
    if (src === wanted) return;
    wanted = src;
    loader.load(src, (tex) => {
      if (dead || wanted !== src) { tex.dispose(); return; }
      tex.colorSpace = T.SRGBColorSpace;
      const next = front ? stackA : stackB, prev = front ? stackB : stackA;
      next.material.map?.dispose(); next.material.map = tex; next.material.needsUpdate = true;
      const a = tex.image.height / tex.image.width;
      next.scale.set(1, a / (wh ? wh[1] / wh[0] : a), 1);
      next.renderOrder = 2; prev.renderOrder = 1;
      anims.push({ m: next, kind: 'stack', t0: performance.now(), ms: reduce.matches ? 1 : 500, step(k) { next.material.opacity = k; },
        done() { prev.material.opacity = 0; } });
      front = 1 - front; stackSrc = src; loop();
    }, undefined, () => { if (wanted === src) wanted = ''; });
  }
  async function init() {
    let lib;
    try {
      lib = await (typeof loadThree === 'function' ? loadThree()
        : Promise.all([import('/vendor/three.module.min.js'), import('/vendor/OrbitControls.js')]).then(([t, o]) => ({ THREE: t, OrbitControls: o.OrbitControls })));
      if (dead) return;
      T = lib.THREE;
      renderer = new T.WebGLRenderer({ antialias: true, alpha: true, powerPreference: 'low-power' });
    } catch {
      api3.failed = true; root.dataset.space = 'failed'; root.classList.remove('mode-3d'); return;
    }
    renderer.setPixelRatio(Math.min(devicePixelRatio || 1, 2));
    renderer.outputColorSpace = T.SRGBColorSpace;
    host.appendChild(renderer.domElement);
    scene = new T.Scene();
    camera = new T.PerspectiveCamera(30, 1, 0.01, 50);
    camera.position.set(1.05, 0.32, 1.32);
    loader = new T.TextureLoader();
    const plane = () => new T.Mesh(new T.PlaneGeometry(1, wh ? wh[1] / wh[0] : 0.68),
      new T.MeshBasicMaterial({ color: 0xffffff, transparent: true, opacity: 0, depthWrite: false, side: T.DoubleSide }));
    stackA = plane(); stackB = plane(); scene.add(stackA, stackB);
    const rim = new T.LineSegments(new T.EdgesGeometry(stackA.geometry), new T.LineBasicMaterial({ color: 0x8ea2ff, transparent: true, opacity: 0.22 }));
    scene.add(rim);
    controls = new lib.OrbitControls(camera, renderer.domElement);
    controls.target.set(-0.06, 0, -0.55);
    controls.enableDamping = true; controls.dampingFactor = 0.12; controls.enablePan = false;
    controls.minDistance = 1.1; controls.maxDistance = 3.2;
    controls.addEventListener('start', () => { controls.moving = true; loop(); });
    controls.addEventListener('end', () => { setTimeout(() => { controls.moving = false; }, 400); });
    controls.update();
    new ResizeObserver(() => resize()).observe(host);
    resize();
    root.dataset.space = '3d';
    if (stackSrc || wanted) { const s = wanted || stackSrc; stackSrc = wanted = ''; setStack(s); }
    render();
  }
  function resize() {
    if (!renderer) return;
    const w = host.clientWidth || 1, h = host.clientHeight || 1;
    renderer.setSize(w, h, false);
    camera.aspect = w / h; camera.updateProjectionMatrix();
    render();
  }
  function stopFlight() {
    clearTimeout(timer); timer = 0;
    if (scene) { queue.forEach(m => { scene.remove(m); dispose(m); }); }
    queue = []; root.classList.remove('space-info');
  }
  return Object.assign(api3, {
    active(v) {
      if (v === on) return;
      on = v;
      if (!v) { stopFlight(); return; }
      if (!loading) loading = init();
      render();
    },
    stack(src) { if (!renderer) { wanted = src; return; } setStack(src); },
    async update(stacking) {
      if (dead) return;
      if (live && !stacking) stopFlight();
      live = stacking;
      if (!on || !stacking || busy || document.hidden || !renderer) return;
      busy = true;
      try {
        const res = await api(`/stacking/projects/${pid}/pattern?seit=${seit}`);
        if (dead || !live || !on) return;
        let items = res.items || [];
        if (res.run !== run) { run = res.run; items = items.slice(-4); }   // neu geöffnet: nur das Jüngste
        seit = res.seq || 0;
        if (res.wh && (!wh || res.wh[0] !== wh[0] || res.wh[1] !== wh[1])) {
          wh = res.wh;
          [stackA, stackB].forEach(s => { s.geometry.dispose(); s.geometry = new T.PlaneGeometry(1, wh[1] / wh[0]); });
        }
        if (wh) enqueue(items);
      } catch { /* nächster Poll versucht es erneut */ } finally { busy = false; }
    },
    destroy() {
      dead = true; stopFlight(); cancelAnimationFrame(raf);
      if (renderer) { scene.traverse(o => { o.geometry?.dispose(); o.material?.map?.dispose(); o.material?.dispose?.(); }); controls.dispose(); renderer.dispose(); }
    },
  });
}
