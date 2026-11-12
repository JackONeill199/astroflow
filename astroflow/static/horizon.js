// Horizontmaske je Standort (core/sites.py: `horizon` = 36 Höhen alle 10° Azimut ab Norden über Osten).
// Panorama-Editor im gemeinsamen Dialog #dlg: Linie ziehen bzw. darübermalen, Vorlagen, Tastatur (← → wählen, ↑ ↓ ±5°).
// Global: horizonEditor(site, save) – save(horizon | null) speichert; horizonMini(horizon) – kleine Silhouette für Listen.
(() => {
'use strict';
const N = 36, STEP = 10, MIN_ALT = 30;
const DIRS = ['N', 'NO', 'O', 'SO', 'S', 'SW', 'W', 'NW', 'N'];
const open = (from, to, low, high) => Array.from({ length: N }, (_, i) => {   // frei von `from` bis `to` (Azimut, über 0° hinweg)
  const a = i * STEP, inside = from <= to ? a >= from && a <= to : a >= from || a <= to;
  return inside ? low : high;
});
const PRESETS = [['Frei', Array(N).fill(0)], ['Bäume ringsum', Array(N).fill(25)], ['Balkon Süd', open(110, 250, 10, 90)],
  ['Balkon Ost', open(20, 160, 10, 90)], ['Balkon West', open(200, 340, 10, 90)]];
const hasMask = (h) => Array.isArray(h) && h.length === N && h.some(v => v > 0);

// kleine Silhouette (Gelände unten) für Listen; ohne Maske nur die Grundlinie
function horizonMini(h, w = 108, hgt = 22) {
  const X = (i) => (i / N * w).toFixed(1), Y = (v) => (hgt - v / 90 * hgt).toFixed(1);
  const pts = hasMask(h) ? [...h, h[0]].map((v, i) => `${X(i)},${Y(v)}`).join(' ') : `0,${hgt} ${w},${hgt}`;
  return `<svg class="hz-mini" viewBox="0 0 ${w} ${hgt}" aria-hidden="true"><line class="hz-mini-min" x1="0" x2="${w}" y1="${Y(MIN_ALT)}" y2="${Y(MIN_ALT)}"/>
    <polygon class="hz-mini-land" points="0,${hgt} ${pts} ${w},${hgt}"/></svg>`;
}

function horizonEditor(site, save) {
  const d = $('#dlg');
  let h = hasMask(site.horizon) ? site.horizon.slice() : Array(N).fill(0), cur = 18;
  // Zeichenfläche = echte Breite (Schrift bleibt auf schmalen Fenstern lesbar), Höhe ~ 0,37 × Breite, mindestens 200 px
  let W = 720, H = 270, PW = 0, PH = 0;
  const L = 34, R = 14, T = 14, B = 34;
  const X = (az) => L + az / 360 * PW, Y = (alt) => T + (90 - alt) / 90 * PH;
  const fit = () => {
    W = Math.max(300, Math.round(svg.getBoundingClientRect().width) || 720); H = Math.max(200, Math.round(W * 0.37));
    PW = W - L - R; PH = H - T - B;
    svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
  };
  d.classList.add('hz-dlg');
  d.innerHTML = `<div class="row hz-head"><h2>Horizont · ${esc(site.name)}</h2><span class="grow"></span><button class="btn ghost sm icon" value="x" aria-label="Schließen">${icon('x')}</button></div>
    <p class="mut sm hz-hint">Linie auf die Höhe von Bäumen, Häusern oder Balkonbrüstung ziehen – darunter sieht das Teleskop nicht.</p>
    <svg class="hz-edit" id="hzSvg" viewBox="0 0 ${W} ${H}" tabindex="0" role="slider" aria-valuemin="0" aria-valuemax="90"></svg>
    <div class="hz-presets" role="group" aria-label="Vorlagen">${PRESETS.map(([n], i) => `<button type="button" class="chip" data-p="${i}">${esc(n)}</button>`).join('')}</div>
    <div class="row hz-foot"><span class="mut sm" id="hzInfo"></span><span class="grow"></span><button class="btn ghost" value="0">Abbrechen</button><button class="btn" value="1">Speichern</button></div>`;
  const svg = $('#hzSvg', d);
  const paint = () => {
    const pts = [...h, h[0]].map((v, i) => `${X(i * STEP).toFixed(1)},${Y(v).toFixed(1)}`).join(' ');
    let g = '';
    for (const a of [0, 30, 60, 90]) g += `<line class="${a === MIN_ALT ? 'hz-min' : 'hz-grid'}" x1="${L}" x2="${L + PW}" y1="${Y(a)}" y2="${Y(a)}"/><text class="hz-ax" x="${L - 6}" y="${Y(a) + 4}" text-anchor="end">${a}°</text>`;
    DIRS.forEach((n, k) => { g += `<line class="hz-grid" x1="${X(k * 45)}" x2="${X(k * 45)}" y1="${T}" y2="${T + PH}"/><text class="hz-ax${n.length === 1 ? ' main' : ''}" x="${X(k * 45)}" y="${H - 12}" text-anchor="middle">${n}</text>`; });
    g += `<polygon class="hz-land" points="${L},${T + PH} ${pts} ${L + PW},${T + PH}"/><polyline class="hz-line" points="${pts}"/>`;
    g += h.map((v, i) => `<circle class="hz-pt${i === cur ? ' cur' : ''}" cx="${X(i * STEP).toFixed(1)}" cy="${Y(v).toFixed(1)}" r="${i === cur ? 5 : 3}"/>`).join('');
    svg.innerHTML = g;
    svg.setAttribute('aria-valuenow', String(Math.round(h[cur])));
    svg.setAttribute('aria-valuetext', `${cur * STEP}° (${DIRS[Math.round(cur * STEP / 45) % 8]}): frei ab ${Math.round(h[cur])}°`);
    svg.setAttribute('aria-label', 'Horizonthöhe je Himmelsrichtung');
    const free = h.filter(v => v <= MIN_ALT).length;
    $('#hzInfo', d).textContent = `${cur * STEP}° ${DIRS[Math.round(cur * STEP / 45) % 8]}: ${Math.round(h[cur])}° · frei über ${MIN_ALT}°: ${Math.round(free / N * 100)} % der Richtungen`;
  };
  // Malen: Zeiger ziehen setzt die Höhe der überstrichenen Richtungen (Lücken dazwischen werden aufgefüllt)
  let last = null;
  const at = (e) => {
    const m = svg.getScreenCTM().inverse(), p = new DOMPoint(e.clientX, e.clientY).matrixTransform(m);
    const az = Math.max(0, Math.min(359.9, (p.x - L) / PW * 360));
    return [Math.round(az / STEP) % N, Math.round(Math.max(0, Math.min(90, 90 - (p.y - T) / PH * 90)))];
  };
  const set = ([i, v]) => {
    if (last && last[0] !== i && Math.abs(last[0] - i) < N / 2) {
      const [a, va] = last, s = Math.sign(i - a);
      for (let k = a + s; k !== i; k += s) h[k] = Math.round(va + (v - va) * (k - a) / (i - a));
    }
    h[i] = v; cur = i; last = [i, v]; paint();
  };
  svg.onpointerdown = (e) => { svg.setPointerCapture(e.pointerId); last = null; set(at(e)); };
  svg.onpointermove = (e) => { if (svg.hasPointerCapture(e.pointerId)) set(at(e)); };
  svg.onpointerup = svg.onpointercancel = () => { last = null; };
  svg.onkeydown = (e) => {
    const k = e.key, step = e.shiftKey ? 15 : 5;
    if (k === 'ArrowLeft') cur = (cur + N - 1) % N; else if (k === 'ArrowRight') cur = (cur + 1) % N;
    else if (k === 'ArrowUp') h[cur] = Math.min(90, h[cur] + step); else if (k === 'ArrowDown') h[cur] = Math.max(0, h[cur] - step);
    else return;
    e.preventDefault(); paint();
  };
  d.onclick = async (e) => {
    const p = e.target.closest('[data-p]');
    if (p) { h = PRESETS[+p.dataset.p][1].slice(); paint(); return; }
    const v = e.target.closest('button')?.value;
    if (v === 'x' || v === '0') d.close();
    if (v === '1') { const b = e.target.closest('button'); b.disabled = true; if (await save(hasMask(h) ? h : null)) d.close(); else b.disabled = false; }
  };
  d.onclose = () => { d.classList.remove('hz-dlg'); d.onclick = d.onclose = null; };
  d.showModal();
  fit(); paint();
  svg.focus();
}
window.horizonEditor = horizonEditor;
window.horizonMini = horizonMini;
})();
