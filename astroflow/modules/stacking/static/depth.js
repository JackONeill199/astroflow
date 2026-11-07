// „Wie tief schaust du?“ und „Veränderungen“ – Abschnitte der Objektseite (Reiter Bild, Detailspalte).
// Daten: /stacking/results/<id>/depth bzw. /compare (eigene Endpunkte, nicht in der Projektansicht – die wird gepollt).
// 3D-Tunnel: three.js aus der Shell (/vendor/), erst beim Öffnen geladen. Entfernungsachse logarithmisch.

const insightData = new Map();          // 'depth:<rid>' → Antwort des Servers
const DEPTH_MARKS = { on: store.get('bild.tiefe.marken', false) };
const de1 = (v, d = 1) => Number(v).toLocaleString('de-DE', { maximumFractionDigits: d, minimumFractionDigits: d });
// Lichtjahre lesbar: 189 Lj · 38 500 Lj · 21 Mio. Lj · 5,5 Mrd. Lj
const lyText = (ly, unit = 'Lj') => ly == null ? '–' : ly < 1000 ? `${nf.format(Math.round(ly))} ${unit}` : ly < 1e6 ? `${nf.format(Math.round(ly / 100) * 100)} ${unit}`
  : ly < 1e9 ? `${de1(ly / 1e6, ly < 1e7 ? 1 : 0)} Mio. ${unit}` : `${de1(ly / 1e9)} Mrd. ${unit}`;
const yrText = (y) => lyText(y, 'Jahre');

async function insightLoad(kind, rid, force = false) {
  const k = `${kind}:${rid}`;
  if (!force && insightData.has(k)) return insightData.get(k);
  const d = await api(`/stacking/results/${rid}/${kind}`);
  if (d.state === 'done') insightData.set(k, d); else insightData.delete(k);
  return d;
}

// Abschnitt „Tiefe“. viewer: createViewer-Objekt (Markierungen ein/aus über viewer.redraw()).
// opts.wide (Reiter Analyse): breites Raster, Fakten anklickbar (opts.onFocus(x, y, w) zoomt im Bild); opts.on3d(true) schaltet den
// Betrachter oben in die 3D-Ansicht, opts.onData(d) meldet die fertige Messung (für den 3D-Knopf in der Werkzeugleiste).
function depthPanel(host, r, viewer, opts = {}) {
  if (!host) return;
  let timer = 0;
  const poll = async (force) => {
    clearTimeout(timer);
    if (!host.isConnected) return;
    let d;
    try { d = await insightLoad('depth', r.id, force); } catch (e) { host.innerHTML = head() + `<p class="mut sm">${esc(e.message)}</p>`; return; }
    if (!host.isConnected) return;
    draw(d);
    if (d.state === 'queued' || d.state === 'running') timer = setTimeout(() => poll(true), 2000);
    else if (d.state === 'done' && DEPTH_MARKS.on) viewer?.redraw();
  };
  const head = (extra = '') => `<div class="panel-h"><h2>Wie tief schaust du?</h2><span class="grow"></span>${extra}</div>`;
  const run = async () => {
    try { await send(`/stacking/results/${r.id}/depth`, {}); insightData.delete(`depth:${r.id}`); poll(true); } catch (e) { toast(e.message, 'bad'); }
  };
  function draw(d) {
    if (d.state === 'queued' || d.state === 'running') {
      host.innerHTML = head() + `<div class="depth-wait"><span class="spinner"></span><span class="mut sm">${esc(d.message || 'wird gemessen')} …</span></div>`;
      return;
    }
    if (d.state !== 'done') {
      const why = !d.solved ? 'Dieses Bild ist nicht per Plate-Solving gelöst – ohne Himmelskoordinaten lässt sich nicht messen, wie tief es reicht.'
        : d.state === 'error' ? esc(d.message || 'Messung fehlgeschlagen')
        : d.state === 'old' ? 'Die gespeicherte Messung stammt von einer älteren Version der Auswertung.'
        : 'Grenzgröße, Entfernungen der Sterne und die fernsten Galaxien in deinem Bild – aus Gaia und SIMBAD (es werden nur Himmelskoordinaten gesendet).';
      host.innerHTML = head() + `<p class="mut sm">${why}</p><div class="row depth-empty-acts">${d.solved ? `<button class="btn ${opts.wide ? '' : 'ghost '}sm" data-depth-run>${icon('target')}${d.state === 'none' ? 'Jetzt messen' : 'Neu messen'}</button>`
        : opts.onSolve ? `<button class="btn sm" data-depth-solve>${icon('star')}Per Plate-Solving lösen</button>`
        : opts.wide ? `<p class="dim sm">Die Lösung entsteht beim Entwickeln, wenn ein Plate-Solver (z. B. ASTAP) eingerichtet und die fotometrische Farbkalibrierung eingeschaltet ist – danach „Neu entwickeln“.</p><a class="btn ghost sm" href="#/settings/stacking">${icon('star')}Plate-Solver einrichten</a>` : ''}</div>`;
      $('[data-depth-run]', host)?.addEventListener('click', run);
      $('[data-depth-solve]', host)?.addEventListener('click', opts.onSolve);
      return;
    }
    const g = d.grenze || {}, e = d.entfernung || {}, far = d.fernstes, main = d.hauptobjekt;
    const bins = (g.klassen || []).filter(b => b.mag >= 8);
    const bmax = Math.max(1, ...bins.map(b => b.katalog));
    const dist = (e.verteilung || []).filter(b => b.n);
    const dmax = Math.max(1, ...dist.map(b => b.n));
    const fieldFar = (d.galaxien || []).filter(o => !o.sichtbar).sort((a, b) => b.z - a.z)[0];
    const at = (o) => opts.onFocus && o?.x != null && d.bild ? ` class="focusable" tabindex="0" data-x="${o.x}" data-y="${o.y}" title="Im Bild zeigen"` : '';
    host.innerHTML = head('<span class="chip">Gaia DR3 · SIMBAD</span>') + `
      <div class="depth-kpis">
        <div><small>Grenzgröße</small><b class="tnum" data-count>${g.m5 != null ? de1(g.m5) + ' mag' : '–'}</b><span>${g.lim50 ? `Hälfte der Sterne bis G ${g.lim50}` : '5σ-Grenze'}</span></div>
        <div><small>Sterne erfasst</small><b class="tnum" data-count>${nf.format(g.erfasst || 0)}</b><span>von ${nf.format(g.katalog || 0)} Gaia-Sternen</span></div>
        <div><small>Fernster Stern</small><b class="tnum" data-count>${lyText(e.fernster?.ly)}</b><span>${e.fernster ? `G ${de1(e.fernster.g)} · mit Parallaxe` : 'keine sichere Parallaxe'}</span></div>
        <div><small>Fernstes Objekt</small><b class="tnum" data-count>${far ? lyText(far.ly) : main ? lyText(main.ly) : '–'}</b><span>${far ? `Licht unterwegs · z ${de1(far.z, 3)}` : main ? esc(main.name) : 'keine Galaxie erkannt'}</span></div>
      </div>
      ${bins.length ? `<div class="depth-chart" aria-label="Erfasste Sterne je Helligkeit"><div class="depth-chart-h"><span class="sm">Erfasst je Helligkeitsklasse</span><span class="grow"></span><span class="dim sm"><i class="lg cat"></i>Katalog <i class="lg det"></i>im Bild</span></div>
        <div class="depth-bars">${bins.map(b => `<div class="db" title="G ${b.mag}–${b.mag + 1}: ${b.erfasst} von ${b.katalog} erfasst"><i class="cat" style="height:${b.katalog / bmax * 100}%"></i><i class="det" style="height:${b.erfasst / bmax * 100}%"></i></div>`).join('')}</div>
        <div class="depth-axis tnum">${bins.map(b => `<span>${b.mag % 2 ? '' : b.mag}</span>`).join('')}</div></div>` : ''}
      ${dist.length ? `<div class="depth-chart"><div class="depth-chart-h"><span class="sm">Entfernungen der Sterne</span><span class="grow"></span><span class="dim sm">${nf.format(e.mit_parallaxe)} mit Parallaxe</span></div>
        <div class="depth-hist">${dist.map(b => `<div class="dh"><span class="dim sm tnum">bis ${lyText(b.bis)}</span><div class="progress"><i style="width:${b.n / dmax * 100}%"></i></div><span class="sm tnum">${b.n}</span></div>`).join('')}</div></div>` : ''}
      <ul class="depth-facts">
        ${e.naechster ? `<li${at(e.naechster)}><span class="dot near"></span><div>Nächster Stern <b>${lyText(e.naechster.ly)}</b><div class="dim sm">${esc(e.naechster.id)} · G ${de1(e.naechster.g)}</div></div></li>` : ''}
        ${g.schwaechster ? `<li${at(g.schwaechster)}><span class="dot faint"></span><div>Schwächster sicher erfasster Stern <b>G ${de1(g.schwaechster.g)}</b><div class="dim sm">${esc(g.schwaechster.id)} · SNR ${de1(g.schwaechster.snr)}</div></div></li>` : ''}
        ${main ? `<li><span class="dot main"></span><div>${esc(main.name)} <b>${lyText(main.ly)}</b><div class="dim sm">${esc(main.quelle)}</div></div></li>` : ''}
        ${far ? `<li${at(far)}><span class="dot gal"></span><div>Fernstes Objekt in deinem Bild: ${esc(far.name)} (${esc(far.art)})<div class="dim sm">Licht ${yrText(far.ly)} unterwegs · heute ${lyText(far.heute_ly)} entfernt</div></div></li>` : ''}
        <li><span class="dot gal"></span><div>${d.galaxien_sichtbar ? `${d.galaxien_sichtbar} ${d.galaxien_sichtbar === 1 ? 'Galaxie' : 'Galaxien'} mit gemessener Rotverschiebung sichtbar` : 'Keine Hintergrundgalaxie mit Rotverschiebung sicher erkennbar'}${d.teile ? `<div class="dim sm">dazu ${d.teile} Regionen im Hauptobjekt selbst (HII-Gebiete, Sternhaufen)</div>` : ''}
          ${d.galaxien_fehler ? `<div class="dim sm">${esc(d.galaxien_fehler)}</div>` : fieldFar ? `<div class="dim sm">Im Feld, aber zu schwach: ${esc(fieldFar.name)} (${esc(fieldFar.art)}, z ${de1(fieldFar.z, 2)}, ${yrText(fieldFar.ly)})</div>` : ''}</div></li>
      </ul>
      <div class="row depth-acts">
        ${d.bild ? `<button class="btn ghost sm${DEPTH_MARKS.on ? ' on' : ''}" data-depth-marks aria-pressed="${DEPTH_MARKS.on}">${icon('target')}Im Bild markieren</button>` : ''}
        ${d.punkte?.length ? `<button class="btn ghost sm" data-depth-3d>${icon('orbit')}${opts.on3d ? 'Im Bild in 3D zeigen' : '3D-Tunnel'}</button>` : ''}
        <span class="grow"></span><button class="linkbtn sm" data-depth-run>Neu messen</button></div>
      ${(d.hinweise || []).map(h => `<p class="dim sm depth-note">${esc(h)}</p>`).join('')}`;
    $('[data-depth-run]', host)?.addEventListener('click', run);
    $('[data-depth-3d]', host)?.addEventListener('click', () => opts.on3d ? opts.on3d(true) : depthTunnel(d, r));
    opts.onData?.(d);
    $('.depth-facts', host)?.addEventListener('click', (ev) => { const li = ev.target.closest('[data-x]'); if (li) opts.onFocus(+li.dataset.x, +li.dataset.y, d.bild.w); });
    $('.depth-facts', host)?.addEventListener('keydown', (ev) => { if (ev.key === 'Enter') ev.target.closest('[data-x]')?.click(); });
    $('[data-depth-marks]', host)?.addEventListener('click', (ev) => {
      DEPTH_MARKS.on = !DEPTH_MARKS.on; store.set('bild.tiefe.marken', DEPTH_MARKS.on);
      ev.currentTarget.classList.toggle('on', DEPTH_MARKS.on); ev.currentTarget.setAttribute('aria-pressed', DEPTH_MARKS.on);
      viewer?.el.classList.toggle('marks-on', DEPTH_MARKS.on); viewer?.redraw();
    });
    viewer?.el.classList.toggle('marks-on', DEPTH_MARKS.on && !!d.bild);
  }
  host.innerHTML = head() + '<div class="skel-line"></div>';
  poll(false);
}

// Overlay für createViewer: Objektnamen (base, z. B. plateLabels) + Tiefenmarken, wenn eingeschaltet.
function depthOverlay(rid, base) {
  return (svg, width) => {
    base?.(svg, width);
    svg.querySelector('.depth-marks')?.remove();
    const d = insightData.get(`depth:${rid}`);
    if (!DEPTH_MARKS.on || !d?.bild) return;
    const sc = width / (d.bild.w || width), fs = Math.max(12, width / 60), e = d.entfernung || {}, g = d.grenze || {};
    const mark = (o, cls, label, r = fs * .9) => o?.x == null ? '' : `<g class="${cls}"><circle cx="${o.x * sc}" cy="${o.y * sc}" r="${r}"/>
      <text x="${o.x * sc + r + fs * .3}" y="${o.y * sc + fs * .35}" font-size="${fs}">${esc(label)}</text><title>${esc(label)}</title></g>`;
    const gals = (d.galaxien || []).filter(o => o.sichtbar && !o.teil).slice(0, 25);
    const html = gals.map(o => mark(o, 'dm-gal', lyText(o.ly), fs * .8)).join('')
      + mark(e.naechster, 'dm-near', `nächster Stern ${lyText(e.naechster?.ly)}`)
      + mark(e.fernster, 'dm-far', `fernster Stern ${lyText(e.fernster?.ly)}`)
      + mark(g.schwaechster, 'dm-faint', `G ${g.schwaechster ? de1(g.schwaechster.g) : ''}`);
    svg.insertAdjacentHTML('beforeend', `<g class="depth-marks">${html}</g>`);
  };
}

// ---------------------------------------------------------------- 3D-Tunnel --- //
// Blick vom Beobachter in das Bildfeld: Richtung = Bildposition (aufgeweitet), Abstand = log10(Lichtjahre).
// Von vorn sieht man wieder das Foto; dreht man die Ansicht, zieht sich das Feld zu einem Trichter in die Tiefe.
let threeLib = null;
const loadThree = () => threeLib || (threeLib = Promise.all([import('/vendor/three.module.min.js'), import('/vendor/OrbitControls.js')])
  .then(([T, O]) => ({ THREE: T, OrbitControls: O.OrbitControls })).catch((e) => { threeLib = null; throw e; }));

function starColor(bprp) {                       // BP−RP → Farbe (blau → weiß → orange)
  if (bprp == null) return [0.85, 0.88, 0.95];
  const t = Math.min(1, Math.max(0, (bprp + 0.2) / 2.4));
  const a = [[0.62, 0.74, 1], [1, 1, 1], [1, 0.82, 0.55], [1, 0.62, 0.38]];
  const f = t * 3, i = Math.min(2, Math.floor(f)), k = f - i;
  return a[i].map((v, j) => v + (a[i + 1][j] - v) * k);
}

// vw: Bildbetrachter (createViewer) im Reiter Analyse – dann entsteht die 3D-Ansicht *im* Betrachter: Startbild = das Foto, exakt
// wie im flachen Betrachter (eingepasst, volle Auflösung, volle Helligkeit). Erst beim Drehen/Fahren lösen sich die Sterne nach ihrer
// Entfernung in die Tiefe (sie liegen auf den Sichtstrahlen ihrer Bildposition, von vorn decken sie sich also mit dem Foto).
// Rückgabe dort: { close(animiert) }. Ohne vw: Vollbild-Dialog wie bisher.
function depthTunnel(d, r, vw = null) {
  const ov = document.createElement('div');
  ov.className = vw ? 'tunnel in-viewer' : 'tunnel';
  if (!vw) { ov.setAttribute('role', 'dialog'); ov.setAttribute('aria-label', '3D-Tunnel nach Entfernung'); }
  const seg = `<div class="seg"><button data-v="front"${vw ? ' class="on"' : ''}>Von vorn</button><button data-v="oblique"${vw ? '' : ' class="on"'}>Schräg</button><button data-v="side">Seitlich</button></div>`;
  ov.innerHTML = vw ? `<div class="tunnel-stage"></div>
    <div class="t3-bar">${seg}<button class="btn ghost sm" data-replay title="Kameraflug wiederholen">${icon('play')}Flug</button></div>
    <div class="tunnel-info" hidden></div>
    <div class="t3-legend sm"><span><i class="lg star"></i>Stern</span><span><i class="lg gal"></i>Galaxie</span>${d.hauptobjekt ? '<span><i class="lg main"></i>Hauptobjekt</span>' : ''}<span class="t3-hint">ziehen dreht · Mausrad fährt · Abstand logarithmisch</span></div>`
    : `<div class="tunnel-bar"><b>Tiefe des Bildfelds</b><span class="mut sm tunnel-sub">Abstand logarithmisch · ziehen dreht, Mausrad zoomt, Klick zeigt Namen</span><span class="grow"></span>
      ${seg}<button class="btn ghost sm" data-x aria-label="Schließen">${icon('x')}</button></div>
    <div class="tunnel-stage"><div class="tunnel-load"><span class="spinner"></span>3D-Ansicht wird geladen …</div></div>
    <div class="tunnel-info" hidden></div>
    <div class="tunnel-legend sm"><span><i class="lg star"></i>Stern (Gaia-Parallaxe)</span><span><i class="lg gal"></i>Galaxie (Rotverschiebung, Lichtlaufzeit)</span>${d.hauptobjekt ? '<span><i class="lg main"></i>Hauptobjekt</span>' : ''}</div>`;
  if (vw) {
    vw.el.insertBefore(ov, $('.vbar', vw.el));                     // Werkzeugleiste bleibt darüber
    // Ziehen/Mausrad gehören der 3D-Ansicht, nicht dem flachen Betrachter darunter
    for (const t of ['pointerdown', 'wheel', 'dblclick']) ov.addEventListener(t, (e) => e.stopPropagation());
  } else document.body.append(ov);
  let alive = true, raf = 0;
  const api3 = { close: () => {} };
  const close = () => { alive = false; cancelAnimationFrame(raf); ro?.disconnect(); renderer?.dispose(); document.removeEventListener('keydown', key); ov.remove(); vw?.el.classList.remove('v3d-on'); };
  api3.close = close;
  const key = (e) => { if (e.key === 'Escape') close(); };
  if (!vw) document.addEventListener('keydown', key);
  $('[data-x]', ov)?.addEventListener('click', close);
  let THREE, OrbitControls, renderer, ro;
  const reduced = () => matchMedia('(prefers-reduced-motion: reduce)').matches;
  const run = (async () => {
  try { ({ THREE, OrbitControls } = await loadThree()); } catch (e) {
    if (vw) { close(); toast('3D-Bibliothek konnte nicht geladen werden.', 'bad'); } else $('.tunnel-load', ov).textContent = '3D-Bibliothek konnte nicht geladen werden.';
    return;
  }
  if (!alive || !ov.isConnected) return;
  const stage = $('.tunnel-stage', ov);
  stage.innerHTML = '';
  renderer = new THREE.WebGLRenderer({ antialias: true });
  renderer.setPixelRatio(Math.min(2, devicePixelRatio));
  stage.append(renderer.domElement);
  const scene = new THREE.Scene();
  scene.background = new THREE.Color(vw ? 0x050607 : 0x07080b);   // im Betrachter: dessen Hintergrund
  const cam = new THREE.PerspectiveCamera(50, 1, 0.01, 500);
  const img = vw ? $('img.b', vw.el) : null;
  const asp = img?.naturalWidth ? img.naturalWidth / img.naturalHeight : (d.bild?.w || 3) / (d.bild?.h || 2);
  const S = 0.28;                                  // Aufweitung: halbe Bildhöhe = 0,28 je Einheit Abstand (≈ 31° Öffnung)
  const R0 = 0.5;                                  // log10(Lj) des Beobachter-Nullpunkts (~3 Lj)
  const dirOf = (x, y) => [(x / d.bild.w - 0.5) * asp * 2 * S, (0.5 - y / d.bild.h) * 2 * S, -1];
  const tOf = (ly) => Math.max(0.05, Math.log10(Math.max(1, ly)) - R0);
  const place = (x, y, ly) => dirOf(x, y).map(v => v * tOf(ly));
  const pts = (d.punkte || []).filter(p => p.x != null && p.ly > 0);
  const maxT = Math.max(3, ...pts.map(p => Math.log10(p.ly) - R0), d.hauptobjekt ? Math.log10(d.hauptobjekt.ly) - R0 : 0);
  // Abstand der Bildebene. Im Betrachter mitten im Trichter: von vorn füllt sie das Bild (der Blickwinkel hängt nicht davon ab),
  // schräg bleibt das Foto groß sichtbar – nahe Sterne treten davor heraus, ferne ziehen dahinter in die Tiefe
  const tF = vw ? Math.min(4, Math.max(1, maxT * 0.3)) : 0.6;
  // Tiefe k: 0 = alle Punkte in der Bildebene (auf ihrem Bildpixel), 1 = in ihrer Entfernung; fade: 0 = von vorn (nur Foto), 1 = Tiefe sichtbar
  let k = vw ? 0 : 1, fade = vw ? 0 : 1, dirty = true;
  const fading = [];                               // Materialien, die mit der Tiefe einblenden: [Material, volle Deckkraft]
  const fadeMat = (m, o) => { m.transparent = true; fading.push([m, o]); return m; };
  // Foto: im Betrachter deckend und in voller Auflösung (gleiche Datei wie oben), sonst halbtransparentes Fenster
  let plane = null;
  new THREE.TextureLoader().load(img?.currentSrc || img?.src || `/api/stacking/results/${r.id}/thumb`, (tex) => {
    if (!alive) return;
    tex.colorSpace = THREE.SRGBColorSpace;
    tex.anisotropy = renderer.capabilities.getMaxAnisotropy();
    plane = new THREE.Mesh(new THREE.PlaneGeometry(asp * 2 * S * tF, 2 * S * tF),
      new THREE.MeshBasicMaterial({ map: tex, transparent: true, opacity: vw ? 1 : 0.32, depthWrite: false, side: THREE.DoubleSide, toneMapped: false }));
    plane.position.z = -tF; plane.renderOrder = -1; scene.add(plane); dirty = true;
    if (vw) requestAnimationFrame(() => { ov.classList.add('ready'); start(); });
  }, undefined, () => { if (vw) { close(); toast('Bild für die 3D-Ansicht konnte nicht geladen werden.', 'bad'); } });
  // Rahmen je Zehnerpotenz mit Beschriftung
  const label = (text, pos, color = '#9aa3b2', size = 0.16) => {
    const c = document.createElement('canvas'), g = c.getContext('2d'), font = '500 44px system-ui, sans-serif';
    g.font = font;
    c.width = Math.ceil(g.measureText(text).width + 16); c.height = 96;
    g.font = font; g.fillStyle = color; g.textBaseline = 'middle'; g.fillText(text, 8, 48);
    const sp = new THREE.Sprite(fadeMat(new THREE.SpriteMaterial({ map: new THREE.CanvasTexture(c), depthWrite: false }), 1));
    sp.scale.set(size * c.width / 96, size, 1); sp.center.set(0, 0.5); sp.position.set(...pos); scene.add(sp); return sp;
  };
  for (let e = 1; e <= 10.5; e++) {
    const t = e - R0;
    if (t > maxT + 0.6) break;
    const w = asp * S * t, h = S * t;
    const geo = new THREE.BufferGeometry().setFromPoints([[-w, -h], [w, -h], [w, h], [-w, h], [-w, -h]].map(([x, y]) => new THREE.Vector3(x, y, -t)));
    scene.add(new THREE.Line(geo, fadeMat(new THREE.LineBasicMaterial({ color: 0x2a3140 }), 0.8)));
    label(lyText(10 ** e), [w + 0.05, h, -t], '#7d8696', 0.1 + 0.05 * t);
  }
  // Kanten des Trichters
  const ex = asp * S * (maxT + 0.6), ey = S * (maxT + 0.6), ez = -(maxT + 0.6);
  for (const [sx, sy] of [[-1, -1], [1, -1], [1, 1], [-1, 1]]) {
    const geo = new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(0, 0, 0), new THREE.Vector3(sx * ex, sy * ey, ez)]);
    scene.add(new THREE.Line(geo, fadeMat(new THREE.LineBasicMaterial({ color: 0x1c212b }), 1)));
  }
  // Punkte: drei Größen nach Helligkeit, Galaxien eigene Wolke; Lage = Richtung (Bildposition) × Abstand
  const groups = [];
  const dot = (() => { const c = document.createElement('canvas'); c.width = c.height = 64; const g = c.getContext('2d');
    const gr = g.createRadialGradient(32, 32, 0, 32, 32, 32); gr.addColorStop(0, '#fff'); gr.addColorStop(0.45, '#fffffff0'); gr.addColorStop(1, '#fff0');
    g.fillStyle = gr; g.fillRect(0, 0, 64, 64); return new THREE.CanvasTexture(c); })();
  const cloud = (list, size, colorOf) => {
    if (!list.length) return;
    const pos = new Float32Array(list.length * 3), col = new Float32Array(list.length * 3);
    const dir = new Float32Array(list.length * 3), dist = new Float32Array(list.length);
    list.forEach((p, i) => { dir.set(dirOf(p.x, p.y), i * 3); dist[i] = tOf(p.ly); col.set(colorOf(p), i * 3); });
    const geo = new THREE.BufferGeometry();
    geo.setAttribute('position', new THREE.BufferAttribute(pos, 3)); geo.setAttribute('color', new THREE.BufferAttribute(col, 3));
    const pts3 = new THREE.Points(geo, fadeMat(new THREE.PointsMaterial({ size: size * 1.35, map: dot, vertexColors: true, sizeAttenuation: false, depthWrite: false }), 0.95));
    pts3.userData = { list, dir, dist }; scene.add(pts3); groups.push(pts3);
  };
  const setDepth = () => {                         // Punkte entlang ihrer Sichtstrahlen von der Bildebene in die Tiefe schieben
    for (const g of groups) {
      const { dir, dist } = g.userData, pos = g.geometry.attributes.position;
      for (let i = 0; i < dist.length; i++) {
        const t = tF + (dist[i] - tF) * k;
        pos.array[i * 3] = dir[i * 3] * t; pos.array[i * 3 + 1] = dir[i * 3 + 1] * t; pos.array[i * 3 + 2] = -t;
      }
      pos.needsUpdate = true; g.geometry.computeBoundingSphere();
    }
  };
  const stars = pts.filter(p => p.t === 's');
  cloud(stars.filter(p => p.g < 12), 7, p => starColor(p.bp_rp));
  cloud(stars.filter(p => p.g >= 12 && p.g < 15), 4.5, p => starColor(p.bp_rp));
  cloud(stars.filter(p => p.g >= 15), 3, p => starColor(p.bp_rp));
  cloud(pts.filter(p => p.t === 'g'), 8, () => [0.78, 0.6, 1]);
  if (d.hauptobjekt) {
    const m = { t: 'm', x: d.bild.w / 2, y: d.bild.h / 2, ly: d.hauptobjekt.ly, n: d.hauptobjekt.name };
    cloud([m], 13, () => [1, 0.78, 0.35]);
    label(d.hauptobjekt.name, place(m.x, m.y, m.ly).map((v, i) => i === 0 ? v + 0.12 : v), '#ffd08a', 0.12 + 0.07 * (Math.log10(m.ly) - R0));
  }
  if (d.fernstes?.x != null) label(d.fernstes.name, place(d.fernstes.x, d.fernstes.y, d.fernstes.ly).map((v, i) => i === 0 ? v + 0.1 : v), '#c8a6ff', 0.12 + 0.07 * (Math.log10(d.fernstes.ly) - R0));
  label('Du', [0.05, 0.05, 0], '#e8ebf5', 0.16);
  setDepth();

  const ctl = new OrbitControls(cam, renderer.domElement);
  ctl.enableDamping = true; ctl.dampingFactor = 0.12;
  const mid = -maxT / 2;
  // Blickwinkel von vorn: Bildebene genau so groß wie das eingepasste Foto im Betrachter (contain)
  let fitFov = 50;
  const views = { front: [[0, 0, 0], [0, 0, -tF]], oblique: [[-maxT * 0.95, maxT * 0.45, maxT * 0.15], [0, 0, mid]], side: [[-maxT * 1.25, 0.15, mid], [0, 0, mid]] };
  if (!vw) views.front = [[0, 0, 0.8], [0, 0, -3]];
  else {
    // im Betrachter: Dreiviertelansicht auf das Foto (≈ 38° bzw. 68° seitlich), damit Foto und Tiefe zugleich gut zu sehen sind
    const tz = -(tF + (maxT - tF) * 0.12), R = tF * 1.8 + 1.5;
    const from = (a, e) => { a *= Math.PI / 180; e *= Math.PI / 180; return [[-R * Math.sin(a) * Math.cos(e), R * Math.sin(e), tz + R * Math.cos(a) * Math.cos(e)], [0, 0, tz]]; };
    views.oblique = from(34, 12); views.side = from(72, 6);
  }
  const fovFor = (v) => vw ? (v === 'front' ? fitFov : 34) : 50;
  let fly = null;
  const go = (v, instant = false, dur = 700) => {
    const [p, t] = views[v];
    if (instant || reduced()) { fly = null; cam.position.set(...p); ctl.target.set(...t); cam.fov = fovFor(v); cam.updateProjectionMatrix(); ctl.update(); dirty = true; return; }
    fly = { t0: performance.now(), dur, p0: cam.position.clone(), q0: ctl.target.clone(), p1: new THREE.Vector3(...p), q1: new THREE.Vector3(...t), f0: cam.fov, f1: fovFor(v) };
  };
  // Übergang aus dem flachen Bild: Kamera fährt vom Foto schräg hinaus, die Sterne ziehen dabei in ihre Entfernung (≈ 2,4 s)
  const replayB = $('[data-replay]', ov);
  const mark = (v) => $$('.seg button', ov).forEach(x => x.classList.toggle('on', x.dataset.v === v));
  const start = () => {
    go('front', true);
    if (reduced()) { k = 1; setDepth(); dirty = true; return; }  // reduziert: Foto bleibt stehen, Tiefe durch eigenes Drehen
    k = 0; setDepth();
    const k0 = performance.now() + 250;
    fly = { t0: k0, dur: 2400, p0: cam.position.clone(), q0: ctl.target.clone(), p1: new THREE.Vector3(...views.oblique[0]), q1: new THREE.Vector3(...views.oblique[1]),
      f0: cam.fov, f1: fovFor('oblique'), depth: true };
    mark('oblique');
  };
  const intro = () => { if (vw) { go('front', true); k = 0; start(); return; } };
  let closing = false;                             // Rückflug zum flachen Bild läuft: nicht mehr unterbrechbar umzulenken
  replayB?.addEventListener('click', () => { if (!closing) intro(); });
  // Abbrechen eines Flugs springt ans Ziel – auch beim Rückflug, dessen Abschluss (then: close) dann sofort ausgeführt wird
  const skip = () => { if (!fly) return; const { depth: dep, then } = fly; fly = null; if (dep) { k = 1; setDepth(); } dirty = true; then?.(); };
  renderer.domElement.addEventListener('pointerdown', skip);
  ctl.addEventListener('change', () => { dirty = true; });
  $('.seg', ov).addEventListener('click', (e) => {
    const b = e.target.closest('[data-v]'); if (!b) return;
    if (closing) return;
    mark(b.dataset.v); if (k < 1) { k = 1; setDepth(); } go(b.dataset.v);
  });
  // Hochformat (Telefon): größerer Blickwinkel, damit der Trichter nicht seitlich abgeschnitten wird
  const size = () => {
    const w = stage.clientWidth, h = stage.clientHeight; renderer.setSize(w, h, false); cam.aspect = w / Math.max(1, h);
    const a = cam.aspect;
    fitFov = 2 * Math.atan(S / Math.min(1, a / asp)) * 180 / Math.PI;   // halbe Bildhöhe S·tF bei Abstand tF füllt die Höhe bzw. Breite
    const atFront = vw && !fly && cam.position.lengthSq() < 1e-6;
    if (atFront) cam.fov = fitFov;
    else if (!vw) cam.fov = a < 1 ? Math.min(85, 50 / Math.max(0.55, a)) : 50;
    cam.updateProjectionMatrix(); dirty = true;
  };
  ro = new ResizeObserver(size); ro.observe(stage); size();
  go(vw ? 'front' : 'oblique', true);
  // Klick: nächster Punkt in Bildschirmnähe (erst wenn die Tiefe sichtbar ist)
  const info = $('.tunnel-info', ov), ray = new THREE.Raycaster();
  renderer.domElement.addEventListener('click', (e) => {
    if (fade < 0.3) { info.hidden = true; return; }
    const b = renderer.domElement.getBoundingClientRect();
    const m = new THREE.Vector2(((e.clientX - b.left) / b.width) * 2 - 1, -((e.clientY - b.top) / b.height) * 2 + 1);
    ray.setFromCamera(m, cam);
    ray.params.Points.threshold = 0.04 * cam.position.distanceTo(ctl.target) / 4;
    const hit = ray.intersectObjects(groups).sort((a, b2) => a.distanceToRay - b2.distanceToRay)[0];
    if (!hit) { info.hidden = true; return; }
    const p = hit.object.userData.list[hit.index];
    info.hidden = false;
    info.innerHTML = p.t === 'g' ? `<b>${esc(p.n)}</b><div class="sm">${esc(p.art || 'Galaxie')} · Rotverschiebung z ${de1(p.z, 4)}</div><div class="sm">Licht ${yrText(p.ly)} unterwegs</div>`
      : p.t === 'm' ? `<b>${esc(p.n)}</b><div class="sm">${lyText(p.ly)} entfernt</div>`
      : `<b>${esc(p.n)}</b><div class="sm">${lyText(p.ly)} entfernt · G ${de1(p.g)}</div><div class="dim sm">Licht ${yrText(p.ly)} unterwegs</div>`;
  });
  // Von vorn (Kamera auf der Achse vor dem Foto) nur das Foto; mit dem Blickwinkel blendet die Tiefe ein, das Foto wird zum Fenster
  const pc = new THREE.Vector3(0, 0, -tF), dv = new THREE.Vector3();
  const updFade = () => {
    if (!vw) return;
    dv.subVectors(cam.position, pc);
    const ang = dv.z > 0 ? Math.atan2(Math.hypot(dv.x, dv.y), dv.z) * 180 / Math.PI : 180;
    const f = Math.min(1, Math.max(0, (ang - 3) / 22)) * Math.min(1, k * 1.5);
    fade = f;
    for (const [m, o] of fading) m.opacity = o * f;
    if (plane) plane.material.opacity = 1 - 0.25 * f;                // schräg bleibt das Foto gut sichtbar
    ov.classList.toggle('deep', f > 0.3);
  };
  const loop = (now) => {
    if (!alive) return;
    if (!ov.isConnected) { close(); return; }            // eingebettet: Reiter gewechselt
    if (fly && now >= fly.t0) {
      const q = Math.min(1, (now - fly.t0) / fly.dur), e = q < .5 ? 2 * q * q : 1 - (-2 * q + 2) ** 2 / 2;
      cam.position.lerpVectors(fly.p0, fly.p1, e); ctl.target.lerpVectors(fly.q0, fly.q1, e);
      cam.fov = fly.f0 + (fly.f1 - fly.f0) * e; cam.updateProjectionMatrix();
      if (fly.depth) { k = Math.min(1, q * 1.15); setDepth(); }
      dirty = true;
      if (q >= 1) { const then = fly.then; fly = null; then?.(); }
    }
    if (ctl.update()) dirty = true;
    if (dirty) { updFade(); renderer.render(scene, cam); dirty = false; }
    raf = requestAnimationFrame(loop);
  };
  raf = requestAnimationFrame(loop);
  // Zurück zum flachen Bild: erst nach vorn fahren und die Tiefe einziehen, dann ausblenden
  api3.close = (animated = true) => {
    if (!alive) return;
    closing = true;
    if (!animated || reduced() || !plane) { close(); return; }
    fly = { t0: performance.now(), dur: 650, p0: cam.position.clone(), q0: ctl.target.clone(), p1: new THREE.Vector3(...views.front[0]), q1: new THREE.Vector3(...views.front[1]),
      f0: cam.fov, f1: fitFov, then: close };
  };
  })();
  if (vw) { vw.el.classList.add('v3d-on'); return api3; }
  return run;
}

// ---------------------------------------------------------- Veränderungen --- //
// Abschnitt „Veränderungen“: Vergleich mit eigener früherer Aufnahme oder DSS2; onBlink(true) schaltet den Betrachter um.
function comparePanel(host, r, onBlink, blinkOn) {
  if (!host) return;
  let timer = 0;
  const head = () => `<div class="panel-h"><h2>Veränderungen</h2><span class="grow"></span><span class="chip">Hinweise</span></div>`;
  const poll = async (force) => {
    clearTimeout(timer);
    if (!host.isConnected) return;
    let d;
    try { d = await insightLoad('compare', r.id, force); } catch (e) { host.innerHTML = head() + `<p class="mut sm">${esc(e.message)}</p>`; return; }
    if (!host.isConnected) return;
    draw(d);
    if (d.state === 'queued' || d.state === 'running') timer = setTimeout(() => poll(true), 2000);
  };
  const refOpts = (d) => `<option value="auto">Automatisch wählen</option>${(d.refs || []).map(x => `<option value="${x.id}">${x.same ? 'Version' : esc(x.name)} vom ${fdate(x.created)} · ${x.n_frames} Aufn.</option>`).join('')}
    ${d.survey ? '<option value="survey">Himmelsdurchmusterung DSS2 (online)</option>' : ''}`;
  const run = async () => {
    const v = $('#cmpRef', host)?.value || 'auto';
    try { await send(`/stacking/results/${r.id}/compare`, { ref: /^\d+$/.test(v) ? +v : v }); insightData.delete(`compare:${r.id}`); poll(true); }
    catch (e) { toast(e.message, 'bad'); }
  };
  function draw(d) {
    const picker = (label) => d.solved && (d.refs?.length || d.survey) ? `<div class="row cmp-run"><select id="cmpRef" aria-label="Referenz">${refOpts(d)}</select>
      <button class="btn ghost sm" data-cmp-run>${icon('compare')}${label}</button></div>` : '';
    if (d.state === 'queued' || d.state === 'running') {
      host.innerHTML = head() + `<div class="depth-wait"><span class="spinner"></span><span class="mut sm">${esc(d.message || 'wird verglichen')} …</span></div>`;
      return;
    }
    if (d.state !== 'done') {
      const why = !d.solved ? 'Dieses Bild ist nicht per Plate-Solving gelöst – ohne Himmelskoordinaten kein Vergleich möglich.'
        : !(d.refs?.length || d.survey) ? 'Keine frühere eigene Aufnahme dieses Himmelsausschnitts, und der Online-Vergleich ist ausgeschaltet.'
        : d.state === 'error' ? esc(d.message || 'Vergleich fehlgeschlagen')
        : 'Vergleicht mit einer früheren eigenen Aufnahme oder der Himmelsdurchmusterung und zeigt neue oder veränderte Lichtquellen – als Hinweis, nicht als Entdeckung.';
      host.innerHTML = head() + `<p class="mut sm">${why}</p>${picker('Vergleichen')}`;
      $('[data-cmp-run]', host)?.addEventListener('click', run);
      return;
    }
    const ref = d.ref || {}, k = d.kandidaten || [], vw = d.verworfen || {};
    const nrej = Object.values(vw).reduce((a, b) => a + b, 0);
    const kindTxt = { neu: 'neu', heller: 'heller', 'verschwunden': 'verschwunden', 'schwächer': 'schwächer' };
    // Grenzgröße beider Bilder (seit 0.43.1): „neu“/„verschwunden“ nur, wenn das andere Bild die Quelle zeigen konnte
    const gz = d.grenze || {}, srcTxt = { gaia: 'Gaia-Sterne im Bild', tiefe: 'gespeicherte Tiefe', nullpunkt: 'Nullpunkt + Rauschen',
      vollstaendigkeit: '50-%-Vollständigkeit', geraet: 'geschätzt aus anderen Bildern der Kamera', durchmusterung: 'Nennwert der Durchmusterung' };
    const gzSide = (s, label) => s && s.m5 != null ? `<span title="5σ-Grenzgröße – Quelle: ${esc(srcTxt[s.quelle] || s.quelle || '')}">${label} <b class="tnum">≈ ${de1(s.m5)} mag</b>${s.belichtung_s ? ` <span class="dim">(${dur(s.belichtung_s)}${s.aufnahmen ? `, ${s.aufnahmen} Aufn.` : ''})</span>` : ''}</span>` : `<span class="dim">${label} unbekannt</span>`;
    // Verwerfgründe, die eine Aussage über die Referenz bzw. das Bild treffen (seit 0.43.4: Halo/Sättigung, bekannte Sterne)
    const rejTxt = [['unter_grenze', 'unter der Grenze'], ['heller_nachbar', 'im Licht heller Sterne'], ['katalogstern', 'bekannte Sterne']]
      .filter(([key]) => vw[key]).map(([key, t]) => `${vw[key]} ${key === 'katalogstern' && vw[key] === 1 ? 'bekannter Stern' : t}`);
    const rejAll = { hotpixel: 'Hotpixel', rand: 'Rand', rauschen: 'Rauschen', form: 'Form', einzelpixel: 'Einzelpixel', heller_stern: 'Rest heller Sterne',
      helligkeit: 'Helligkeit gleich', zu_schwach: 'zu schwach', unter_grenze: 'unter der Grenze', heller_nachbar: 'im Licht heller Sterne', katalogstern: 'bekannter Stern' };
    const rejTitle = Object.entries(vw).filter(([, n]) => n).map(([key, n]) => `${rejAll[key] || key}: ${n}`).join(' · ');
    host.innerHTML = head() + `
      <p class="sm cmp-ref">Referenz: <b>${esc(ref.art === 'survey' ? ref.label : ref.label || 'eigene Aufnahme')}</b>${ref.datum ? ` · ${esc(ref.datum)}` : ''}${ref.n_frames ? ` · ${ref.n_frames} Aufnahmen` : ''}</p>
      ${d.grenze ? `<p class="sm cmp-lim">Grenzgröße: ${gzSide(gz.aktuell, 'Bild')} · ${gzSide(gz.referenz, 'Referenz')}${rejTxt.length ? ` · <span class="mut">${rejTxt.join(', ')} verworfen</span>` : ''}</p>` : ''}
      <div class="row cmp-acts"><div class="seg" id="cmpBlink"><button data-b="0" class="${blinkOn ? '' : 'on'}">Bild</button><button data-b="1" class="${blinkOn ? 'on' : ''}">Blinken</button></div>
        <span class="mut sm"${rejTitle ? ` title="${esc(rejTitle)}"` : ''}>${k.length ? `${k.length} ${k.length === 1 ? 'Stelle' : 'Stellen'} auffällig` : 'Nichts Auffälliges'}${nrej ? ` · ${nrej} geprüft und verworfen` : ''}</span></div>
      ${k.length ? `<ul class="cmp-list">${k.map((c, i) => `<li data-i="${i}" tabindex="0" title="Im Blinkbild anzeigen">
          <img src="/api/stacking/results/${r.id}/compare/${esc(c.bild)}?t=${Math.round(d.erstellt || 0)}" alt="Referenz, aktuell, Differenz" loading="lazy" width="147" height="49">
          <div><b class="cmp-kind ${c.art === 'neu' || c.art === 'heller' ? 'up' : 'down'}">${kindTxt[c.art] || esc(c.art)}</b> <span class="mut sm tnum">${de1(c.sigma)} σ${c.dmag != null ? ` · ${c.dmag > 0 ? '+' : ''}${de1(c.dmag, 2)} mag` : ''}${c.art === 'verschwunden' ? (c.mag_ref ? ` · war ≈ ${de1(c.mag_ref)} mag` : '') : c.mag_neu ? ` · ≈ ${de1(c.mag_neu)} mag` : ''}${c.grenze != null ? ` <span class="dim">(Grenze ${c.art === 'verschwunden' ? 'hier' : 'Referenz'} ${de1(c.grenze)})</span>` : ''}</span>
          ${c.kleinkoerper ? `<div class="sm">Kleinkörper: <b>${esc(c.kleinkoerper.name)}</b> <span class="dim">(${de1(c.kleinkoerper.abstand, 0)}″)</span></div>` : ''}
          ${c.katalog ? `<div class="sm${c.katalog.veraenderlich ? '' : ' dim'}">${c.katalog.veraenderlich ? 'bekannt veränderlich: ' : 'SIMBAD: '}${esc(c.katalog.name)} <span class="dim">(${esc(c.katalog.art)}, ${de1(c.katalog.abstand)}″)</span></div>`
            : c.kleinkoerper ? '' : '<div class="dim sm">kein Katalogeintrag gefunden</div>'}</div></li>`).join('')}</ul>
        <p class="dim sm">Ausschnitte: Referenz · aktuell · Differenz. Unterschiede in Kamera, Filter, Schärfe und Bearbeitung erzeugen Fehlalarme – bitte selbst prüfen.</p>` : ''}
      ${(d.hinweise || []).map(h => `<p class="dim sm depth-note">${esc(h)}</p>`).join('')}
      ${picker('Erneut vergleichen')}`;
    $('[data-cmp-run]', host)?.addEventListener('click', run);
    $('#cmpBlink', host).addEventListener('click', (e) => { const b = e.target.closest('[data-b]'); if (b && (b.dataset.b === '1') !== blinkOn) onBlink(b.dataset.b === '1'); });
    $('.cmp-list', host)?.addEventListener('click', (e) => { const li = e.target.closest('[data-i]'); if (li) onBlink(true, k[+li.dataset.i]); });
  }
  host.innerHTML = head() + '<div class="skel-line"></div>';
  poll(false);
}

// Betrachter für den Blink-Vergleich: links Referenz, rechts aktuell (gleiche Streckung, Raster des Stacks); Kandidaten markiert.
function compareViewer(host, r, focus) {
  const d = insightData.get(`compare:${r.id}`);
  if (!d) return null;
  const q = `?t=${Math.round(d.erstellt || 0)}`;
  const marks = (svg, width) => {
    const sc = width / (d.bild?.w || width), fs = Math.max(12, width / 60);
    svg.innerHTML = (d.kandidaten || []).map((c, i) => `<g class="cm-${c.art === 'neu' || c.art === 'heller' ? 'up' : 'down'}"><circle cx="${c.x * sc}" cy="${c.y * sc}" r="${fs * 1.2}"/>
      <text x="${c.x * sc + fs * 1.5}" y="${c.y * sc + fs * .35}" font-size="${fs}">${i + 1}</text></g>`).join('');
  };
  const v = createViewer(host, `/api/stacking/results/${r.id}/compare/aktuell.jpg${q}`, `/api/stacking/results/${r.id}/compare/referenz.jpg${q}`,
    ['Referenz', 'aktuell'], null, { overlay: marks });
  v.el.classList.add('tall', 'cmp-view');
  // automatisches Blinken (abschaltbar), reduzierte Bewegung: aus
  const bar = document.createElement('div');
  bar.className = 'row cmp-blinkbar';
  bar.innerHTML = `<button class="btn ghost sm" data-auto>${icon('play')}Automatisch blinken</button><span class="mut sm">Trennlinie ziehen oder automatisch wechseln lassen</span>`;
  host.after(bar);
  let iv = 0, side = 0;
  const stop = () => { clearInterval(iv); iv = 0; $('[data-auto]', bar).innerHTML = `${icon('play')}Automatisch blinken`; v.setSplit(0.5); };
  $('[data-auto]', bar).addEventListener('click', () => {
    if (iv) return stop();
    $('[data-auto]', bar).innerHTML = `${icon('pause')}Anhalten`;
    iv = setInterval(() => { side = 1 - side; v.setSplit(side); }, 650);
  });
  if (focus) setTimeout(() => v.zoomTo(focus.x, focus.y, 5), 350);
  const destroy = v.destroy;
  v.destroy = () => { clearInterval(iv); bar.remove(); destroy(); };
  return v;
}
