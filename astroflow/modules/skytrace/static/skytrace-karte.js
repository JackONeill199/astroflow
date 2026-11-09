'use strict';
/* SkyTrace 3D-Erde – flache Karte (0.37.0, Nutzerwunsch „wie bei Google Maps: weit genug hineingezoomt eine normale Karte, herausgezoomt die Kugel“).
   Canvas 2D in Web-Mercator über der WebGL-Bühne. Verschieben 1:1 (der Punkt unter dem Zeiger bleibt unter dem Zeiger), Mausrad/Pinch zoomt um den
   Zeiger, keine Trägheit. Gezeichnet werden dieselben Daten wie auf dem Globus: Erdbild (Tag/Nacht, Nachtsicht), Grenzen, Heatmap (grob + fein, scharf
   als Zellen), Orte, Ort je Nacht, Vorschläge mit Ellipse, Teleskopstandorte. Satellitenbahnen nicht: das sind Bahnen im Raum (Hunderte km hoch,
   ein Umlauf umrundet die Erde) – auf einer ebenen Karte ergäben sie nur verzerrte Linien ohne Bezug zum Bodenort; die Karte zeigt deshalb nur, wo
   das Teleskop stand. Kartenkacheln (OSM/OpenTopoMap) nur auf Knopfdruck. Welt-Koordinaten x, y ∈ [0, 1] (Mercator), Maßstab Z = Pixel je Welteinheit. */
(() => {
const D2R = Math.PI / 180, EQ_KM = 40075.017;
const clamp = (v, a, b) => Math.min(b, Math.max(a, v));
const mx = (lon) => (lon + 180) / 360;
const my = (lat) => { const s = Math.sin(clamp(lat, -85.05, 85.05) * D2R); return .5 - Math.log((1 + s) / (1 - s)) / (4 * Math.PI); };
const lonOf = (x) => x * 360 - 180;
const latOf = (y) => Math.atan(Math.sinh(Math.PI * (1 - 2 * y))) / D2R;
const smooth = (a, b, x) => { const t = clamp((x - a) / (b - a), 0, 1); return t * t * (3 - 2 * t); };
const TILES = {
  osm: ['https://tile.openstreetmap.org/{z}/{x}/{y}.png', '© <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noreferrer">OpenStreetMap</a>-Mitwirkende', 19],
  topo: ['https://a.tile.opentopomap.org/{z}/{x}/{y}.png', 'Karte © <a href="https://opentopomap.org" target="_blank" rel="noreferrer">OpenTopoMap</a> (CC-BY-SA), Daten © OpenStreetMap-Mitwirkende', 17],
};

// Erdbild in drei Fassungen (Tag, dunkle Nacht, Nachtsicht) – gleiche Formeln wie EARTH_FS im Globus, einmal je Seite berechnet
function earthVariants(img) {
  const w = img.naturalWidth, h = img.naturalHeight, mk = () => { const c = document.createElement('canvas'); c.width = w; c.height = h; return c; };
  const day = mk(); day.getContext('2d').drawImage(img, 0, 0);
  const src = day.getContext('2d').getImageData(0, 0, w, h), a = src.data;
  const night = new ImageData(w, h), sight = new ImageData(w, h), dayNv = new ImageData(w, h);
  for (let i = 0; i < a.length; i += 4) {
    const r = a[i] / 255, g = a[i + 1] / 255, b = a[i + 2] / 255, lum = .3 * r + .59 * g + .11 * b, k = clamp(Math.pow(lum, .8) * 1.35, 0, 1);
    night.data[i] = (r * .07 + .004) * 255; night.data[i + 1] = (g * .07 + .006) * 255; night.data[i + 2] = (b * .07 + .012) * 255; night.data[i + 3] = 255;
    sight.data[i] = (.03 + (.34 - .03) * k) * 255; sight.data[i + 1] = (.06 + (.62 - .06) * k) * 255; sight.data[i + 2] = (.075 + (.58 - .075) * k) * 255; sight.data[i + 3] = 255;
    dayNv.data[i] = (r * .55 + lum * .8 * .45) * 255; dayNv.data[i + 1] = (g * .55 + lum * .95 * .45) * 255; dayNv.data[i + 2] = (b * .55 + lum * .95 * .45) * 255; dayNv.data[i + 3] = 255;
  }
  const put = (d) => { const c = mk(); c.getContext('2d').putImageData(d, 0, 0); return c; };
  return { day, night: put(night), sight: put(sight), dayNv: put(dayNv), w, h };
}

/* host = .ste-stage. deps (Getter, vom Globus): { H, sites, layerK(k), nv(), shade(), sunLocal() → [x,y,z] ECEF-Einheitsvektor,
   patches() → [{night, coarse, fine, reveal, h}], marks() → {current:[{lat,lon,name,night,sel}], proposals:[{lat,lon,name,night,sel,mark}], alts:[{lat1,lon1,lat2,lon2,text,name}]},
   observers() → [{lat, lon, name}], satellites() → [{id, name, sub, color, pts:[[lat,lon]…], at, on}] (Bodenspur, seit 0.45.0),
   onPick(p) (p.kind 'site'|'night'|'sat'|'multi'), onHoverHit(sat|null, px, py), onLeave(lat, lon, pxPerKm, px, py), texture: Image } */
function FlatMap(host, deps) {
  const cv = document.createElement('canvas'); cv.className = 'ste-flat'; cv.setAttribute('aria-label', 'Flache Karte: Ziehen verschiebt, Mausrad oder zwei Finger zoomen');
  cv.hidden = true; host.insertBefore(cv, host.querySelector('.ste-labels'));
  const g = cv.getContext('2d');
  const scratch = document.createElement('canvas'), sg = scratch.getContext('2d');
  const mask = document.createElement('canvas'); mask.width = 72; mask.height = 48; const mg = mask.getContext('2d');
  const V = { cx: .5, cy: .5, Z: 4096, W: 1, H: 1, dpr: 1 };
  const S = { on: false, fade: 0, fadeTo: 0, dirty: true, tiles: null, tileCache: new Map(), tileReq: 0, tileZ: null, tileWant: 0, earth: null, frameMs: 0, hover: null, outlines: null, cellCache: new WeakMap() };
  deps.H.outlines().then(r => { S.outlines = r; S.dirty = true; });
  const tex = deps.texture;
  const prep = () => { if (!S.earth && tex.complete && tex.naturalWidth) { try { S.earth = earthVariants(tex); } catch { S.earth = null; } S.dirty = true; } };
  tex.addEventListener('load', prep); prep();

  const toS = (x, y) => [(x - V.cx) * V.Z + V.W / 2, (y - V.cy) * V.Z + V.H / 2];
  const toW = (sx, sy) => [V.cx + (sx - V.W / 2) / V.Z, V.cy + (sy - V.H / 2) / V.Z];
  // Weltkopie wie das Kartenbild: Länge relativ zur Kartenmitte umbrechen (Datumsgrenze, nach dem Verschieben in eine Nachbarkopie)
  const wrapX = (x) => x + Math.round(V.cx - x);
  const llS = (lat, lon) => toS(wrapX(mx(lon)), my(lat));
  const pxPerKm = (lat = latOf(V.cy)) => V.Z / (EQ_KM * Math.cos(lat * D2R));
  function resize() {
    const r = host.getBoundingClientRect(), dpr = Math.min(devicePixelRatio || 1, 2);
    V.W = r.width; V.H = r.height; V.dpr = dpr;
    for (const c of [cv, scratch]) { c.width = Math.max(1, Math.round(r.width * dpr)); c.height = Math.max(1, Math.round(r.height * dpr)); }
    S.dirty = true;
  }

  // Erdbild: in waagerechten Streifen von der Plattkarte (Breite linear) in Mercator umgesetzt, Längen über den Rand wiederholt
  function drawEarth(ctx, img, alpha = 1) {
    if (!img || alpha <= 0) return;
    const w = S.earth.w, h = S.earth.h, strips = 48, [x0] = toW(0, 0), [x1] = toW(V.W, 0);
    ctx.globalAlpha = alpha;
    for (let s = 0; s < strips; s++) {
      const sy0 = V.H * s / strips, sy1 = V.H * (s + 1) / strips;
      const la0 = latOf(toW(0, sy0)[1]), la1 = latOf(toW(0, sy1)[1]);
      const v0 = (90 - la0) / 180 * h, v1 = (90 - la1) / 180 * h;
      if (v1 <= 0 || v0 >= h) continue;
      for (let k = Math.floor(x0); k <= Math.floor(x1); k++) {
        const a = Math.max(x0, k), b = Math.min(x1, k + 1); if (b <= a) continue;
        const sxa = toS(a, 0)[0], sxb = toS(b, 0)[0];
        ctx.drawImage(img, (a - k) * w, clamp(v0, 0, h), (b - a) * w, Math.max(.5, clamp(v1, 0, h) - clamp(v0, 0, h)), sxa, sy0, sxb - sxa, sy1 - sy0 + .6);
      }
    }
    ctx.globalAlpha = 1;
  }
  // Nachtmaske: Anteil Nacht je Zelle aus der Sonnenrichtung (gleiche Kante wie der Erd-Shader)
  function nightMask(shade) {
    const sun = deps.sunLocal(), id = mg.createImageData(mask.width, mask.height);
    for (let j = 0; j < mask.height; j++) for (let i = 0; i < mask.width; i++) {
      const [x, y] = toW((i + .5) / mask.width * V.W, (j + .5) / mask.height * V.H), la = latOf(y) * D2R, lo = lonOf(x) * D2R;
      const d = Math.cos(la) * Math.cos(lo) * sun[0] + Math.cos(la) * Math.sin(lo) * sun[1] + Math.sin(la) * sun[2];
      id.data[(j * mask.width + i) * 4 + 3] = 255 * (1 - smooth(-.06, .1, d)) * shade;
    }
    mg.putImageData(id, 0, 0);
  }
  // Kartenkacheln (nur nach Knopfdruck; Bilder ohne Pixelzugriff, daher kein CORS nötig). Nutzungsregeln von OSM/OpenTopoMap: nur sichtbare Kacheln,
  // keine Vorab-Downloads, höchstens MAX_TILES (96) je Ansicht, Speicher-Cache (LRU) statt erneuter Abrufe, Ladevorgänge außer Sicht abbrechen, Quellenangabe sichtbar.
  // Zoomstufe so, dass 1 Kachelpixel ≈ 1 Gerätepixel (Z · devicePixelRatio / 256); bis die feinen Kacheln da sind, stehen gröbere Vorfahren als Platzhalter, dann 200 ms Überblendung.
  const MAX_TILES = 96, CACHE_TILES = 400;                 // 96: große HiDPI-Bühne (dpr 2) braucht ~60 Kacheln je Ansicht
  function tileImg(kind, z, x, y) {
    const key = `${kind}/${z}/${x}/${y}`;
    let im = S.tileCache.get(key);
    if (im) { S.tileCache.delete(key); S.tileCache.set(key, im); return im; }          // zuletzt genutzt nach hinten
    im = new Image(); im.decoding = 'async'; im.dataset.key = key;
    im.onload = () => { im._t = performance.now(); S.dirty = true; };
    im.onerror = () => { im._bad = true; };
    im.src = TILES[kind][0].replace('{z}', z).replace('{x}', x).replace('{y}', y); S.tileReq++;
    S.tileCache.set(key, im);
    return im;
  }
  const cached = (kind, z, x, y) => { const im = S.tileCache.get(`${kind}/${z}/${x}/${y}`); return im?._t ? im : null; };
  function drawTiles() {
    const t = TILES[S.tiles]; if (!t) return;
    const [x0, y0] = toW(0, 0), [x1, y1] = toW(V.W, V.H);
    const span = (z) => { const n = 2 ** z; return (Math.floor(x1 * n) - Math.floor(x0 * n) + 1) * (Math.min(n - 1, Math.floor(y1 * n)) - Math.max(0, Math.floor(y0 * n)) + 1); };
    let z = clamp(Math.round(Math.log2(V.Z * V.dpr / 256)), 1, t[2]);
    while (z > 1 && span(z) > MAX_TILES) z--;
    S.tileZ = z;
    const n = 2 ** z, d = V.dpr, now = performance.now(), want = new Set();
    let fading = false;
    g.save(); g.setTransform(1, 0, 0, 1, 0, 0); g.imageSmoothingEnabled = true; g.imageSmoothingQuality = 'high';
    for (let ty = Math.max(0, Math.floor(y0 * n)); ty <= Math.min(n - 1, Math.floor(y1 * n)); ty++)
      for (let tx = Math.floor(x0 * n); tx <= Math.floor(x1 * n); tx++) {
        const wx = ((tx % n) + n) % n, im = tileImg(S.tiles, z, wx, ty); want.add(im.dataset.key);
        // auf ganze Gerätepixel gerundet: keine Fugen, kein zusätzliches Weichzeichnen
        const [ax, ay] = toS(tx / n, ty / n), [bx, by] = toS((tx + 1) / n, (ty + 1) / n);
        const X0 = Math.round(ax * d), Y0 = Math.round(ay * d), W = Math.round(bx * d) - X0, Hh = Math.round(by * d) - Y0;
        const a = im._t ? Math.min(1, (now - im._t) / 200) : 0;
        if (a < 1) {                                           // Platzhalter: nächster geladener Vorfahr (bis 4 Stufen gröber)
          for (let k = 1; k <= 4 && z - k >= 0; k++) {
            const p = cached(S.tiles, z - k, wx >> k, ty >> k); if (!p) continue;
            const m = 2 ** k, sz = 256 / m;
            g.globalAlpha = 1; g.drawImage(p, (wx % m) * sz, (ty % m) * sz, sz, sz, X0, Y0, W, Hh); break;
          }
          if (im._t) fading = true;
        }
        if (a > 0) { g.globalAlpha = a; g.drawImage(im, X0, Y0, W, Hh); }
      }
    g.restore();
    for (const [key, im] of S.tileCache) if (!im._t && !im._bad && !want.has(key)) { im.src = ''; S.tileCache.delete(key); }     // außer Sicht: nicht weiterladen
    while (S.tileCache.size > CACHE_TILES) S.tileCache.delete(S.tileCache.keys().next().value);
    S.tileWant = want.size;
    S.again ||= fading;                                     // Überblendung läuft: nächstes Bild wieder zeichnen
  }
  // Heatmap als scharfe Zellen (Farbe und Durchsicht wie im Globus-Shader)
  function cells(h) { let c = S.cellCache.get(h); if (!c) { c = deps.H.heatCells(h); c.max = c.reduce((a, x) => Math.max(a, x.v), 0); S.cellCache.set(h, c); } return c; }
  function drawHeat(k) {
    const ppk = pxPerKm(), fineK = smooth(1.5, 6, ppk);          // fein (1 km) erst, wenn Zellen ≥ 1,5 px groß sind
    for (const P of deps.patches()) {
      const rv = P.reveal ?? 1, rev = rv < .5 ? 4 * rv ** 3 : 1 - Math.pow(-2 * rv + 2, 3) / 2, th = 1 - rev * 1.08;
      for (const [hh, f] of [[P.h.coarse, 1 - .6 * fineK], [P.h.fine, Math.max(fineK, .35)]]) {
        if (!hh || f <= .01) continue;
        const cs = cells(hh); if (!cs.max) continue;
        for (const c of cs) {
          const t = c.v / cs.max, a = smooth(th, th + .08, t) * smooth(.04, .3, t) * (.08 + .92 * Math.pow(t, 1.3)) * k * f * deps.heatOp(); if (a < .004) continue;
          const dl = c.km / 2 / 111.195, dn = c.km / 2 / (111.195 * Math.cos(c.lat * D2R));
          const [sx0, sy0] = llS(c.lat + dl, c.lon - dn), [sx1, sy1] = llS(c.lat - dl, c.lon + dn);
          if (sx1 < 0 || sy1 < 0 || sx0 > V.W || sy0 > V.H) continue;
          const [r, gg, b] = deps.H.heatColor(t); g.fillStyle = `rgba(${r},${gg},${b},${a.toFixed(3)})`; g.fillRect(sx0, sy0, sx1 - sx0 + .4, sy1 - sy0 + .4);
        }
      }
    }
  }
  function drawBorders(k, nv) {
    if (!S.outlines || k <= .01) return;
    const [x0, y0] = toW(0, 0), [x1, y1] = toW(V.W, V.H), la1 = latOf(y0), la0 = latOf(y1);
    g.beginPath();
    for (const r of S.outlines) {
      // Ring als Ganzes in die Weltkopie der Kartenmitte (Natural Earth schneidet Ringe an ±180°, also kein Ring über die Naht)
      const k = Math.round(V.cx - (mx(r.box[0]) + mx(r.box[1])) / 2), a = mx(r.box[0]) + k, b = mx(r.box[1]) + k;
      if (b < x0 || a > x1 || r.box[3] < la0 || r.box[2] > la1) continue;
      for (let i = 0; i < r.lon.length; i++) { const [sx, sy] = toS(mx(r.lon[i]) + k, my(r.lat[i])); if (i) g.lineTo(sx, sy); else g.moveTo(sx, sy); }
      g.closePath();
    }
    g.strokeStyle = nv > .5 ? `rgba(159,243,228,${(.55 * k).toFixed(2)})` : `rgba(210,222,245,${(.5 * k).toFixed(2)})`; g.lineWidth = 1; g.stroke();
  }
  const pill = (text, sx, sy, color, sub) => {
    g.font = '600 11px system-ui, sans-serif'; const w = Math.max(g.measureText(text).width, sub ? (g.font = '500 10px system-ui, sans-serif', g.measureText(sub).width) : 0) + 22, hh = sub ? 30 : 18;
    const x = sx + 9, y = sy - hh / 2;
    g.fillStyle = 'rgba(11,13,16,.85)'; g.strokeStyle = color + '88'; g.lineWidth = 1; g.beginPath(); g.roundRect(x, y, w, hh, 9); g.fill(); g.stroke();
    g.fillStyle = color; g.beginPath(); g.arc(x + 9, y + 9, 3, 0, Math.PI * 2); g.fill();
    g.fillStyle = '#e6e9ef'; g.font = '600 11px system-ui, sans-serif'; g.fillText(text, x + 16, y + 13);
    if (sub) { g.fillStyle = '#9aa3b2'; g.font = '500 10px system-ui, sans-serif'; g.fillText(sub, x + 16, y + 25); }
  };
  const targets = [];                                  // anklickbare Marken in Bildschirmpixeln
  function drawMarks() {
    targets.length = 0;
    const ppk = pxPerKm(), m = deps.marks();
    const kS = deps.layerK('sites'), kC = deps.layerK('current'), kP = deps.layerK('proposals'), kR = deps.layerK('runs');
    if (kP > .01) for (const p of m.proposals) {
      g.globalAlpha = kP; g.beginPath();
      deps.H.ellipsePts(p.mark, 96).forEach(([la, lo], i) => { const [sx, sy] = llS(la, lo); if (i) g.lineTo(sx, sy); else g.moveTo(sx, sy); });
      g.closePath(); g.fillStyle = 'rgba(107,134,255,.16)'; g.fill(); g.strokeStyle = p.sel ? '#8ea4ff' : '#8ea4ffaa'; g.lineWidth = 1.5; g.stroke(); g.globalAlpha = 1;
    }
    if (kP > .01) for (const l of m.alts || []) {              // Alternative: gestrichelte Verbindung Vorschlag ↔ Listenort mit Abstand
      const [x1, y1] = llS(l.lat1, l.lon1), [x2, y2] = llS(l.lat2, l.lon2);
      g.globalAlpha = kP; g.setLineDash([6, 5]); g.strokeStyle = '#f2d385'; g.lineWidth = 2; g.beginPath(); g.moveTo(x1, y1); g.lineTo(x2, y2); g.stroke(); g.setLineDash([]);
      if (Math.hypot(x2 - x1, y2 - y1) > 24) pill(l.text, (x1 + x2) / 2, (y1 + y2) / 2, '#f2d385', 'Alternative ' + (l.name || ''));
      g.globalAlpha = 1;
    }
    if (kS > .01) for (const s of deps.sites) {
      const [sx, sy] = llS(s.lat, s.lon); if (sx < -40 || sy < -40 || sx > V.W + 40 || sy > V.H + 40) continue;
      g.globalAlpha = kS; g.fillStyle = '#dfe6f5'; g.strokeStyle = '#0b0d10'; g.lineWidth = 2; g.beginPath(); g.arc(sx, sy, 4.5, 0, Math.PI * 2); g.stroke(); g.fill();
      if (ppk > .25 && !m.current.some(c => Math.abs(c.lat - s.lat) < 1e-6 && Math.abs(c.lon - s.lon) < 1e-6)) pill(s.name || 'Ort', sx, sy, '#c9d2e3');
      g.globalAlpha = 1; targets.push({ sx, sy, kind: 'site', site: s });
    }
    if (kC > .01) for (const c of m.current) {
      const [sx, sy] = llS(c.lat, c.lon);
      g.globalAlpha = kC; g.strokeStyle = '#f2d385'; g.lineWidth = c.sel ? 3 : 2; g.beginPath(); g.arc(sx, sy, c.sel ? 9 : 7, 0, Math.PI * 2); g.stroke();
      if (c.sel) pill('bisher: ' + (c.name || 'Ort'), sx + 4, sy + 14, '#f2d385');
      g.globalAlpha = 1; targets.push({ sx, sy, kind: 'night', night: c.night });
    }
    if (kP > .01) for (const p of m.proposals) {
      const [sx, sy] = llS(p.lat, p.lon);
      g.globalAlpha = kP; g.fillStyle = '#7f98ff'; g.strokeStyle = '#10162e'; g.lineWidth = 2.5; g.beginPath(); g.arc(sx, sy, p.sel ? 7 : 5, 0, Math.PI * 2); g.stroke(); g.fill();
      if (p.sel || ppk > .05) pill(p.name, sx, sy, '#7f98ff', p.sub);
      g.globalAlpha = 1; targets.push({ sx, sy, kind: 'night', night: p.night });
    }
    if (kR > .01) for (const o of deps.observers()) {
      const [sx, sy] = llS(o.lat, o.lon);
      g.globalAlpha = kR; g.fillStyle = '#f2d385'; g.strokeStyle = '#0b0d10'; g.lineWidth = 2; g.beginPath(); g.rect(sx - 5, sy - 5, 10, 10); g.stroke(); g.fill();
      pill(o.name || 'Teleskop', sx, sy, '#f2d385', 'Teleskop'); g.globalAlpha = 1;
    }
    // Satelliten: Bodenspur ±5 min (gestrichelt) und Punkt unter dem Satelliten beim Durchgang – anklickbar (Bahn im Raum zeigt nur der Globus)
    const sats = kR > .01 && deps.satellites ? deps.satellites() : [];
    for (const s of sats) {
      // Länge stetig fortsetzen (Datumsgrenze) und am Punkt beim Durchgang verankern: er liegt wie alle übrigen Marken in der Weltkopie
      // nächst der Kartenmitte. Verankert an pts[0] landete er um genau eine Weltbreite daneben, sobald pts[0] und er auf verschiedenen
      // Seiten des Gegenpunkts der Mitte lagen (CI-Befund 0.45.0).
      let prev = s.pts[0][1], lu = prev;
      const lus = s.pts.map(([, lo], i) => { if (i) { let d = lo - prev; d -= 360 * Math.round(d / 360); lu += d; prev = lo; } return lu; });
      const ia = Math.max(0, s.pts.indexOf(s.at)), [xa] = llS(s.pts[ia][0], s.pts[ia][1]);
      const poly = s.pts.map(([la], i) => [xa + (lus[i] - lus[ia]) / 360 * V.Z, toS(0, my(la))[1]]);
      const [ax, ay] = poly[ia];
      g.globalAlpha = kR * (s.on ? 1 : .8); g.strokeStyle = s.color; g.lineWidth = s.on ? 2.5 : 1.5; g.setLineDash([6, 4]);
      g.beginPath(); poly.forEach(([x, y], i) => i ? g.lineTo(x, y) : g.moveTo(x, y)); g.stroke(); g.setLineDash([]);
      const r = s.on ? 8 : 6;
      g.fillStyle = s.color; g.strokeStyle = '#0b0d10'; g.lineWidth = 2; g.beginPath(); g.moveTo(ax, ay - r); g.lineTo(ax + r, ay); g.lineTo(ax, ay + r); g.lineTo(ax - r, ay); g.closePath(); g.stroke(); g.fill();
      if (s.on || sats.length <= 3) pill(s.name, ax, ay, s.color, s.on ? s.sub : 'unter dem Satelliten beim Durchgang');
      g.globalAlpha = 1; targets.push({ sx: ax, sy: ay, kind: 'sat', ref: s, poly });
    }
  }
  const segD = (x, y, a, b) => { const dx = b[0] - a[0], dy = b[1] - a[1], l = dx * dx + dy * dy, t = l ? clamp(((x - a[0]) * dx + (y - a[1]) * dy) / l, 0, 1) : 0;
    return Math.hypot(a[0] + t * dx - x, a[1] + t * dy - y); };
  const distTo = (t, x, y) => t.poly ? Math.min(Math.hypot(t.sx - x, t.sy - y), ...t.poly.slice(1).map((b, i) => segD(x, y, t.poly[i], b))) : Math.hypot(t.sx - x, t.sy - y);
  // Satelliten unter dem Zeiger (großzügig; mehrere → Auswahl im Globus)
  const satsAt = (x, y, r) => targets.filter(t => t.kind === 'sat').map(t => ({ t, d: distTo(t, x, y) })).filter(o => o.d <= r).sort((a, b) => a.d - b.d);
  function draw() {
    if (!S.on && S.fade <= 0) return;
    const t0 = performance.now(), ctx = g;
    ctx.setTransform(V.dpr, 0, 0, V.dpr, 0, 0); ctx.clearRect(0, 0, V.W, V.H);
    ctx.fillStyle = '#03050b'; ctx.fillRect(0, 0, V.W, V.H);
    const nv = deps.nv(), shade = deps.shade();
    if (S.earth) {
      drawEarth(ctx, S.earth.day, 1); if (nv > .01) drawEarth(ctx, S.earth.dayNv, nv);
      if (shade > .01) {                                   // Nachtseite: dunkle bzw. Nachtsicht-Fassung, maskiert
        nightMask(shade);
        sg.setTransform(V.dpr, 0, 0, V.dpr, 0, 0); sg.globalCompositeOperation = 'source-over'; sg.clearRect(0, 0, V.W, V.H);
        drawEarth(sg, S.earth.night, 1); if (nv > .01) drawEarth(sg, S.earth.sight, nv);
        sg.globalCompositeOperation = 'destination-in'; sg.imageSmoothingEnabled = true; sg.drawImage(mask, 0, 0, V.W, V.H); sg.globalCompositeOperation = 'source-over';
        ctx.setTransform(1, 0, 0, 1, 0, 0); ctx.drawImage(scratch, 0, 0); ctx.setTransform(V.dpr, 0, 0, V.dpr, 0, 0);
      }
    }
    if (S.tiles) drawTiles();
    drawBorders(deps.layerK('borders'), nv);
    const kH = deps.layerK('heat'); if (kH > .01) drawHeat(kH);
    drawMarks();
    cv.style.opacity = String(S.fade);
    S.frameMs = performance.now() - t0; S.dirty = !!S.again; S.again = false;
  }
  // ---- Bedienung: Verschieben 1:1, Zoom um den Zeiger, Pinch – ohne Trägheit ----
  const pts = new Map(); let drag = null, pinch = null, moved = 0;
  const view = () => { const h = S.hover || toW(V.W / 2, V.H / 2); deps.onHover?.(latOf(h[1]), lonOf(h[0])); };      // Koordinaten/Maßstab nachführen
  const local = (e) => { const r = cv.getBoundingClientRect(); return [e.clientX - r.left, e.clientY - r.top]; };
  function zoomAt(f, sx, sy) {
    const [wx, wy] = toW(sx, sy);
    V.Z = clamp(V.Z * f, 64, 400 * EQ_KM);                 // bis etwa 2,5 m je Pixel
    V.cx = wx - (sx - V.W / 2) / V.Z; V.cy = clamp(wy - (sy - V.H / 2) / V.Z, 0, 1);
    S.dirty = true; view();
    if (pxPerKm() < deps.leaveAt()) { const [la, lo] = [latOf(wy), lonOf(wx)]; deps.onLeave(latOf(V.cy), lonOf(V.cx), pxPerKm(), sx, sy, la, lo); }
  }
  const onWheel = (e) => {                                  // auch über Schildern/Legende der Bühne
    if (!S.on || e.target.closest('.ste-card,.ste-tools,.ste-tiles,.ste-side')) return;   // Seitenleiste scrollt selbst
    e.preventDefault(); const [sx, sy] = local(e); zoomAt(Math.exp(-clamp(e.deltaY, -300, 300) * .0022), sx, sy);
  };
  host.addEventListener('wheel', onWheel, { passive: false });
  cv.addEventListener('pointerdown', (e) => {
    cv.setPointerCapture?.(e.pointerId); const p = local(e); pts.set(e.pointerId, p); moved = 0;
    if (pts.size === 1) drag = { w: toW(...p) };
    if (pts.size === 2) { const [a, b] = [...pts.values()]; pinch = { d: Math.hypot(a[0] - b[0], a[1] - b[1]) }; drag = null; }
  });
  cv.addEventListener('pointermove', (e) => {
    const p = local(e);
    if (!pts.has(e.pointerId)) {
      S.hover = toW(...p); deps.onHover?.(latOf(S.hover[1]), lonOf(S.hover[0]));
      if (e.pointerType === 'mouse') deps.onHoverHit?.(satsAt(p[0], p[1], 12)[0]?.t.ref || null, p[0], p[1]);
      return;
    }
    const old = pts.get(e.pointerId); moved += Math.hypot(p[0] - old[0], p[1] - old[1]); pts.set(e.pointerId, p);
    if (pts.size === 2 && pinch) {
      const [a, b] = [...pts.values()], d = Math.hypot(a[0] - b[0], a[1] - b[1]);
      zoomAt(d / pinch.d, (a[0] + b[0]) / 2, (a[1] + b[1]) / 2); pinch.d = d; return;
    }
    if (drag) {                                            // Weltpunkt unter dem Zeiger festhalten
      V.cx = drag.w[0] - (p[0] - V.W / 2) / V.Z; V.cy = clamp(drag.w[1] - (p[1] - V.H / 2) / V.Z, 0, 1); S.dirty = true;
      S.hover = drag.w; view();
    }
  });
  const up = (e) => {
    const p = local(e); pts.delete(e.pointerId);
    if (pts.size < 2) pinch = null;
    if (!pts.size) {
      if (drag && moved < 5) {                            // Antippen: Satelliten (Spur oder Punkt) zuerst, sonst nächste Marke ≤ 12 px
        const sats = satsAt(p[0], p[1], e.pointerType === 'touch' ? 24 : 14);
        const hit = targets.filter(t => t.kind !== 'sat').map(t => ({ t, d: Math.hypot(t.sx - p[0], t.sy - p[1]) })).filter(x => x.d < 12).sort((a, b) => a.d - b.d)[0];
        if (sats.length > 1) deps.onPick({ kind: 'multi', list: sats.map(o => o.t.ref), sx: p[0], sy: p[1] });
        else if (sats.length) deps.onPick(sats[0].t);
        else if (hit) deps.onPick(hit.t);
      }
      drag = null;
    } else if (pts.size === 1) drag = { w: toW(...pts.values().next().value) };
  };
  cv.addEventListener('pointerup', up); cv.addEventListener('pointercancel', up);
  cv.addEventListener('pointerleave', () => { deps.onHover?.(null); deps.onHoverHit?.(null); });
  const ro = new ResizeObserver(resize); ro.observe(host); resize();

  return {
    get active() { return S.on; },
    get fading() { return S.fade !== S.fadeTo; },
    // Übernahme vom Globus: (lat, lon) liegt an (sx, sy), Maßstab ppk Pixel/km
    enter(lat, lon, ppk, sx, sy) {
      V.Z = ppk * EQ_KM * Math.cos(lat * D2R);
      V.cx = mx(lon) - (sx - V.W / 2) / V.Z; V.cy = my(lat) - (sy - V.H / 2) / V.Z;
      S.on = true; S.fadeTo = 1; cv.hidden = false; S.dirty = true; S.hover = null; view();
    },
    leave() { S.on = false; S.fadeTo = 0; },
    // je Bild: Überblendung (220 ms, reduzierte Bewegung sofort), Zeichnen bei Bedarf
    tick(dt, force, instant) {
      if (S.fade !== S.fadeTo) { S.fade = instant ? S.fadeTo : S.fadeTo > S.fade ? Math.min(1, S.fade + dt / .22) : Math.max(0, S.fade - dt / .22); S.dirty = true; }
      if (S.fade <= 0 && !S.on) { cv.hidden = true; return false; }
      if (S.dirty || force) draw();
      return S.fade >= 1;
    },
    dirty() { S.dirty = true; },
    hot(on) { cv.classList.toggle('ste-hot', !!on); },
    setTiles(kind) { S.tiles = kind || null; S.dirty = true; return TILES[kind]?.[1] || ''; },
    get tiles() { return S.tiles; },
    flyTo(lat, lon, ppk) { V.cx = mx(lon); V.cy = my(lat); if (ppk) V.Z = ppk * EQ_KM * Math.cos(lat * D2R); S.dirty = true; },
    center: () => [latOf(V.cy), lonOf(V.cx)],
    pxPerKm: () => pxPerKm(),
    // Prüfwerte: Weltpunkt unter einem Bildschirmpunkt (Tests: Punkt unter dem Zeiger bleibt beim Verschieben)
    at: (sx, sy) => { const [x, y] = toW(sx, sy); return [latOf(y), lonOf(x)]; },
    screenOf: (lat, lon) => llS(lat, lon),
    state: () => ({ on: S.on, fade: +S.fade.toFixed(3), center: [latOf(V.cy), lonOf(V.cx)].map(v => +v.toFixed(5)), pxPerKm: +pxPerKm().toFixed(4), frameMs: +S.frameMs.toFixed(2), tiles: S.tiles, tileZ: S.tileZ, tileWant: S.tileWant, tileReq: S.tileReq, Z: V.Z, dpr: V.dpr, earth: !!S.earth, targets: targets.length,
      sats: targets.filter(t => t.kind === 'sat').map(t => ({ id: t.ref.id, name: t.ref.name, x: +t.sx.toFixed(1), y: +t.sy.toFixed(1), on: !!t.ref.on })) }),
    dispose() { host.removeEventListener('wheel', onWheel); ro.disconnect(); cv.remove(); S.tileCache.clear(); },
  };
}

window.SkyTraceKarte = { FlatMap, mx, my, latOf, lonOf };
})();
