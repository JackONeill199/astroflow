'use strict';
/* Gerätediagnose (Wunsch 17, seit 0.43.0): Nachführung, Fokus und Bildfeld aus gespeicherten Daten (diagnose.py).
   - window.diagPanel(host, r)   Abschnitt „Dein Gerät“ im Reiter Analyse (GET /stacking/results/{rid}/diagnose, rechnet bei Bedarf im
                                 Hintergrund und wird bis dahin abgefragt)
   - window.diagDevices(list)    HTML „Gerät über alle Nächte“ für das Analyse-Dashboard (Daten aus /stacking/analysis → diag_devices)
   Diagramme als kleines SVG; Animationen hängen an `.an-sec.in` (aus bei prefers-reduced-motion). Präfix dg-. */
(() => {
const de = (v, nd = 1) => v == null || !isFinite(v) ? '–' : Number(v).toLocaleString('de-DE', { minimumFractionDigits: nd, maximumFractionDigits: nd });
const fday = s => s ? new Date(s + 'T12:00:00Z').toLocaleDateString('de-DE', { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC' }) : '–';
const hm = iso => iso ? new Date(iso).toLocaleTimeString('de-DE', { hour: '2-digit', minute: '2-digit', timeZone: 'UTC' }) : '';
const sign = (v, nd = 1) => v == null ? '–' : (v > 0 ? '+' : '') + de(v, nd);
const LEVEL = (v, warn, bad) => v == null ? '' : v >= bad ? 'bad' : v >= warn ? 'warn' : 'ok';

// Liniendiagramm über die Nacht: x = Stunden, mehrere Reihen [{pts:[[x,y]], cls, label}], Marken [{x, label}].
// Feste viewBox 400 breit wie die Dashboard-Diagramme (0.38.3): in Dreierreihen etwa 1:1, Schrift ~11 px.
function chart(series, { unit = '', nd = 1, marks = [], label = '', W = 400, H = 150, zero = false, xticks = null, minSpan = 0 } = {}) {
  const all = series.flatMap(s => s.pts).filter(p => p[1] != null && isFinite(p[1]));
  if (all.length < 2) return '<p class="mut sm dg-empty">Zu wenige Messwerte.</p>';
  const L = 40, R = 8, T = 8, B = 20;
  const x0 = Math.min(...all.map(p => p[0])), x1 = Math.max(...all.map(p => p[0]), x0 + 1e-3);
  let lo = Math.min(...all.map(p => p[1])), hi = Math.max(...all.map(p => p[1]));
  if (zero) { lo = Math.min(lo, 0); hi = Math.max(hi, 0); }
  if (hi - lo < Math.max(minSpan, 1e-6)) { const m = (hi + lo) / 2, h = (minSpan || 2) / 2; lo = m - h; hi = m + h; }
  const pad = (hi - lo) * 0.08; lo -= pad; hi += pad;
  const X = x => L + (x - x0) / (x1 - x0) * (W - L - R), Y = y => T + (hi - y) / (hi - lo) * (H - T - B);
  let g = '';
  for (const v of [lo + pad, (lo + hi) / 2, hi - pad]) g += `<line x1="${L}" x2="${W - R}" y1="${Y(v).toFixed(1)}" y2="${Y(v).toFixed(1)}" class="dg-gl"/><text x="${L - 5}" y="${(Y(v) + 4).toFixed(1)}" text-anchor="end">${de(v, nd)}</text>`;
  // Zeitachse: Stunden, kurze Sitzungen in Minuten
  const hrs = x1 - x0, ticks = xticks || [];
  const step = hrs > 3 ? 1 : hrs > 1.2 ? 0.5 : hrs > 0.5 ? 1 / 6 : hrs > 0.2 ? 1 / 12 : 1 / 30;
  if (!xticks) for (let k = Math.ceil(x0 / step - 1e-9); k * step <= x1 + 1e-9; k++) {
    const t = k * step;
    ticks.push([t, step >= 0.5 ? `${String(t).replace('.', ',')} h` : `${Math.round(t * 60)} min`]);
  }
  ticks.forEach(([t, l], i) => { g += `<text x="${X(t).toFixed(1)}" y="${H - 4}" text-anchor="${xticks ? (i ? 'end' : 'start') : 'middle'}">${esc(l)}</text>`; });
  for (const m of marks) g += `<line x1="${X(m.x).toFixed(1)}" x2="${X(m.x).toFixed(1)}" y1="${T}" y2="${H - B}" class="dg-mark"><title>${esc(m.label)}</title></line>`;
  for (const s of series) {
    const P = s.pts.filter(p => p[1] != null && isFinite(p[1]));
    if (s.dots) g += P.map(p => `<circle cx="${X(p[0]).toFixed(1)}" cy="${Y(p[1]).toFixed(1)}" r="${s.r || 1.8}" class="dg-dot ${s.cls || ''}"/>`).join('');
    else if (P.length > 1) g += `<path pathLength="1" class="dg-line ${s.cls || ''}" d="${P.map((p, i) => `${i && !p[2] ? 'L' : 'M'}${X(p[0]).toFixed(1)} ${Y(p[1]).toFixed(1)}`).join('')}"/>`;
  }
  return `<svg class="dg-chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(label)}">${g}</svg>
    ${series.length > 1 || unit ? `<div class="dg-leg">${series.length > 1 ? series.map(s => `<span><i class="dg-sw ${s.cls || ''}"></i>${esc(s.label)}</span>`).join('') : ''}<span class="mut">${esc(unit)}</span></div>` : ''}`;
}

// Kennzahlenleiste wie im Dashboard: Zellen mit Trennstrichen, je Zelle Bezeichnung, Wert, Zusatz; lv = ok|warn|bad
const cell = (label, v, sub = '', lv = '') => `<div class="dg-k ${lv}"><small>${label}</small><b class="tnum">${v}</b><small class="dim">${sub}</small></div>`;
const strip = (cells) => `<div class="dg-strip">${cells.filter(Boolean).join('')}</div>`;

// Schärfekarte des Bildfelds: Kacheln relativ zum Median (grün = schärfer, rot = größere Sterne), Pfeil zur unschärferen Seite
function fieldMap(f) {
  const M = f.map || [], vals = M.flat().filter(v => v != null);
  if (!vals.length) return '<p class="mut sm dg-empty">Keine Schärfekarte.</p>';
  const med = vals.slice().sort((a, b) => a - b)[Math.floor(vals.length / 2)];
  const rows = M.length, cols = M[0].length;
  const tile = (v) => {
    if (v == null) return '<i class="dg-cell na"></i>';
    const d = (v / med - 1) * 100, k = Math.max(-1, Math.min(1, d / 15));
    return `<i class="dg-cell" style="--k:${k.toFixed(2)}" title="${sign(d, 0)} % gegenüber dem Median">${Math.abs(d) >= 3 ? sign(d, 0) : ''}</i>`;
  };
  const ang = f.tilt_dir != null ? -f.tilt_dir : null;       // im Bild: Norden oben = Stack gespiegelt
  return `<div class="dg-map" style="--cols:${cols};--rows:${rows}">${M.map(r => r.map(tile).join('')).join('')}
      ${ang != null && f.tilt_pct >= 3 && !f.smeared ? `<svg class="dg-arrow${f.tilt_sig ? ' sig' : ''}" viewBox="-50 -50 100 100" aria-hidden="true"><g transform="rotate(${ang.toFixed(0)})"><line x1="-22" y1="0" x2="22" y2="0"/><path d="M22 0 L12 -7 L12 7 Z"/></g></svg>` : ''}</div>
    <div class="dg-leg"><span class="mut">Sterngröße je Bildbereich, Norden oben</span></div>`;
}

function trackChart(t) {
  if (!t) return '<p class="mut sm dg-empty">Keine Zuordnung der Aufnahmen zum Referenzbild.</p>';
  if (!t.measured) return `<p class="mut sm dg-empty">${esc(t.reason || 'Keine messbaren Abschnitte.')}</p>`;
  const S = t.series || [];
  const brk = (i) => i > 0 && S[i - 1] && Math.hypot(S[i][1] - S[i - 1][1], S[i][2] - S[i - 1][2]) > 15;
  const pts = (k) => S.map((p, i) => [p[0], p[k], brk(i)]);
  return chart([{ pts: pts(1), cls: 'a', label: 'x' }, { pts: pts(2), cls: 'b', label: 'y' }], { unit: 'Versatz in px', nd: 0, label: 'Lage der Aufnahmen über die Nacht', zero: true });
}

function focusChart(f) {
  if (!f) return '<p class="mut sm dg-empty">Zu wenige Aufnahmen für eine Fokusmessung.</p>';
  const S = f.series || [];
  const marks = (f.marks || []).map(m => ({ x: m.h, label: `${m.kind === 'restart' ? 'Neue Beobachtung' : 'Automatisch nachfokussiert'} (${hm(m.t)} UTC)${m.change != null ? `: ${sign(m.change)} %` : ''}` }));
  return chart([{ pts: S.map(p => [p[0], p[1]]), cls: 'a', label: 'gemessen', dots: true }, ...(f.airmass ? [{ pts: S.map(p => [p[0], p[2]]), cls: 'b', label: 'ohne Luftmasse', dots: true }] : [])],
    { unit: f.unit === 'as' ? 'FWHM in ″' : 'FWHM in px', label: 'Sterngröße über die Nacht', marks });
}

function eventsLine(e) {
  const E = e?.events || [];
  if (!E.length) return `<span class="mut">Keine Stöße oder Böen${e?.frames ? ` (${e.frames} einzelne Ausreißer)` : ''}</span>`;
  return `<span>Stöße/Böen:</span> ${E.map(x => `<span class="chip ${x.n >= 6 ? 'warn' : ''}" title="Sterngröße ×${de(x.fwhm, 1)}${x.ecc != null ? ` · Länglichkeit ${de(x.ecc, 2)}` : ''}">${hm(x.start)}${x.end !== x.start ? '–' + hm(x.end) : ''} · ${x.n}× ${esc(x.kind)}</span>`).join(' ')}`;
}

// Kennzahlen einer Nacht + Bildfeld in einer Leiste
function nightStrip(n, f) {
  const t = n?.tracking?.measured ? n.tracking : null, fo = n?.focus, tm = fo?.time, u = fo?.unit === 'as' ? '″' : ' px';
  const verdict = !tm ? 'zu kurz' : tm.sig ? `${sign(tm.pct_h)} %/h` : tm.stable ? 'stabil' : 'unklar';
  return strip([
    cell('Drift', t ? (t.speed_as_min != null ? de(t.speed_as_min, 0) + '″/min' : de(t.speed_px_s * 60, 1) + ' px/min') : '–', t ? `${t.recenter}× nachzentriert` : ''),
    cell('Verschmieren', t ? `${de(t.smear_px, 1)} px` : '–', t?.smear_ratio != null ? `${de(t.smear_ratio * 100, 0)} % der Sterngröße` : 'je Belichtung', LEVEL(t?.smear_ratio, 0.5, 1)),
    cell('Unruhe · Drehung', t ? `${t.jitter_as != null ? de(t.jitter_as, 1) + '″' : de(t.jitter_px, 1) + ' px'} · ${de(t.rot_deg_min * 60, 1)}°/h` : '–', 'um die Driftgerade'),
    cell('Sterngröße', fo ? `${de(fo.fwhm, 1)}${u}` : '–', fo?.airmass ? `${de(fo.fwhm_corr, 1)}${u} ohne Luftmasse` : 'FWHM, Median'),
    cell('Fokusdrift', verdict, fo?.temp_range ? `Sensor ${de(fo.temp_range[0], 0)}–${de(fo.temp_range[1], 0)} °C` : tm ? `${sign(tm.lo)} … ${sign(tm.hi)} %/h` : '',
      tm?.sig && tm.pct_h > 0 ? (tm.pct_h >= 8 ? 'warn' : '') : tm?.stable ? 'ok' : ''),
    f && !f.error ? cell('Schärfeabfall', f.smeared ? 'unsicher' : `${de(f.tilt_pct, 0)} %`, f.smeared ? 'Bildfeld drehte sich' : `± ${de(f.tilt_err, 0)} %, größer ${esc(f.soft_side || '')}`,
      f.smeared ? '' : f.tilt_sig ? (f.tilt_pct >= 20 ? 'bad' : 'warn') : 'ok') : '',
    f && !f.error ? cell('Rand gegen Mitte', `${sign(f.curv_pct, 0)} %`, `± ${de(f.curv_err, 0)} % · ${nf.format(f.stars)} Sterne`, f.curv_sig && f.curv_pct > 0 ? 'warn' : 'ok') : '',
  ]);
}

const HELP = `<details class="dg-help"><summary>Was bedeuten die Werte?</summary>
  <p><b>Nachführung:</b> Jede Gerade im Diagramm ist ein Abschnitt zwischen zwei Nachzentrierungen (Sprünge); ihre Steigung ist die Drift während der Aufnahmen.
    Verschmieren = Drift × Belichtungszeit – ab der halben Sterngröße werden Sterne sichtbar länglich.</p>
  <p><b>Fokus:</b> Ob Seeing oder Fokus, lässt sich bei auf- oder untergehenden Objekten nicht ganz trennen – gemeldet wird eine Drift nur, wenn sie mit und ohne
    Luftmassen-Korrektur gleich ausfällt. Grüne senkrechte Linien: automatisch nachfokussiert bzw. neue Beobachtung.</p>
  <p><b>Bildfeld:</b> Verkippung = Sterne werden zu einer Seite größer (Ebene durch die Sterngrößen), Wölbung = größer zum Rand hin.
    Gemeldet wird erst ab 10 % Unterschied und dreifacher Messunsicherheit.</p></details>`;

// Gerätetrend: Kennzahlenleiste + drei Verläufe über alle Nächte in einer Reihe
function deviceTrend(dev) {
  const N = dev.nights || [];
  const med = (k) => { const v = N.map(n => n[k]).filter(x => x != null).sort((a, b) => a - b); return v.length ? v[Math.floor(v.length / 2)] : null; };
  const TR = dev.trends || {};
  const badge = (k) => TR[k] ? `<span class="chip ${TR[k].worse ? 'warn' : TR[k].better ? 'ok' : ''}">${TR[k].worse ? 'wird schlechter' : TR[k].better ? 'wird besser' : 'gleichbleibend'}</span>` : '';
  const head = strip([
    cell('Nächte', nf.format(N.length), N.length ? `${fday(N[0].night)} – ${fday(N.at(-1).night)}` : ''),
    cell('Drift', med('speed') != null ? de(med('speed'), 0) + '″/min' : '–', 'Median'),
    cell('Verschmieren', med('smear') != null ? `${de(med('smear') * 100, 0)} %` : '–', 'der Sterngröße, Median', LEVEL(med('smear'), 0.5, 1)),
    cell('Sterngröße', med('fwhm') != null ? de(med('fwhm'), 1) + '″' : '–', 'ohne Luftmasse, Median'),
    cell('Fokusdrift', nf.format(N.filter(n => n.focus != null && n.focus >= 4).length), 'Nächte mit Drift'),
    cell('Schärfeabfall', med('tilt') != null ? `${de(med('tilt'), 0)} %` : '–', 'Median der Nächte'),
  ]);
  if (N.length < 2) return head + '<p class="mut sm">Für einen Verlauf braucht es Diagnosen aus mehreren Nächten – „Alles analysieren“ mit „Gerät“.</p>';
  const ts = N.map(n => new Date(n.night + 'T12:00:00Z').getTime() / 864e5);
  const t0 = Math.min(...ts), xt = [[0, fday(N[0].night)], [Math.max(...ts) - t0, fday(N.at(-1).night)]];
  const series = (k) => N.map((n, i) => [ts[i] - t0, n[k]]);
  const SPAN = { fwhm: 0.6, smear: 0.2, tilt: 6 };
  const box = (k, title, unit, nd) => `<div class="dg-c"><h4>${title} ${badge(k)}</h4>${N.some(n => n[k] != null)
    ? chart([{ pts: series(k), cls: 'a', label: title, dots: true, r: 2.6 }], { nd, H: 120, label: title, xticks: xt, minSpan: SPAN[k], unit }) : '<p class="mut sm dg-empty">Keine Messwerte.</p>'}</div>`;
  return head + `<div class="dg-c3">${box('fwhm', 'Sterngröße', 'FWHM in ″, ohne Luftmasse', 1)}${box('smear', 'Verschmieren', 'Anteil der Sterngröße', 2)}${box('tilt', 'Schärfeabfall', '% Ecke zu Ecke, nur je Nacht gemessen', 0)}</div>`;
}

function render(host, d, devs, night) {
  const nights = d.nights || [];
  const n = nights.find(x => x.night === night) || nights.at(-1);
  const dev = (devs || []).find(x => x.key === d.device_key);
  const f = d.field, ff = d.field_frames, tr = n?.tracking;
  const extra = [eventsLine(n?.events),
    tr?.elong_vs_drift != null ? `Längliche Sterne ${tr.elong_vs_drift <= 25 && tr.elong_coherence >= 0.4 ? '<b>in Driftrichtung</b>' : `${de(tr.elong_vs_drift, 0)}° neben der Driftrichtung`}` : '',
    tr?.periodic ? `Periodisch ~${de(tr.periodic.period_s / 60, 1)} min, ±${de(tr.periodic.amp_px, 1)} px` : '',
    ff ? `Einzelaufnahmen (3×3): Abfall ${de(ff.tilt_pct, 0)} % ± ${de(ff.tilt_err, 0)} % ${esc(ff.soft_side)}` : ''].filter(Boolean).join(' · ');
  host.innerHTML = `<div class="panel-h"><div><h2>Dein Gerät</h2><p class="mut sm">${esc(d.device?.name || '')} · aus Lage und Schärfe der ${nf.format(d.total)} Aufnahmen und dem Master-Stack – ohne Rohbilder.</p></div>
      <span class="grow"></span>${nights.length > 1 ? `<div class="seg dg-nights" role="group" aria-label="Nacht">${nights.map(x => `<button data-night="${x.night}"${x === n ? ' class="on"' : ''}>${fday(x.night)}</button>`).join('')}</div>` : ''}
      <button class="btn ghost sm" data-dg-redo>${icon('refresh')}Neu berechnen</button></div>
    ${nightStrip(n, f)}
    <div class="dg-c3">
      <div class="dg-c"><h4>${icon('target')}Nachführung</h4>${trackChart(tr)}</div>
      <div class="dg-c"><h4>${icon('sparkle')}Fokus</h4>${focusChart(n?.focus)}</div>
      <div class="dg-c"><h4>${icon('grid')}Bildfeld</h4>${f && !f.error ? fieldMap(f) : `<p class="mut sm dg-empty">${f?.error ? 'Nicht messbar: ' + esc(f.error) : 'Kein Master-Stack mit genug Sternen.'}</p>`}</div>
    </div>
    <p class="dg-extra sm">${extra}</p>
    ${dev ? `<div class="dg-dev"><h3>${icon('camera')}${esc(dev.name || 'Gerät')} über alle Nächte</h3>${deviceTrend(dev)}</div>` : ''}
    ${HELP}`;
  $$('[data-night]', host).forEach(b => b.onclick = () => render(host, d, devs, b.dataset.night));
}

function diagPanel(host, r) {
  if (!host) return;
  let timer = 0, waited = false;
  const wait = (msg) => { host.innerHTML = `<div class="panel-h"><h2>Dein Gerät</h2></div><p class="mut sm"><span class="spinner"></span> ${esc(msg || 'Gerätediagnose wird berechnet …')}</p>`; };
  const load = async (force) => {
    clearTimeout(timer);
    if (!host.isConnected) return;
    let d;
    try { d = force ? await send(`/stacking/results/${r.id}/diagnose`, {}) : await api(`/stacking/results/${r.id}/diagnose`); }
    catch (e) { host.innerHTML = `<div class="panel-h"><h2>Dein Gerät</h2></div><p class="mut sm">${esc(e.message)}</p>`; return; }
    if (!host.isConnected) return;
    if (force || d.state === 'queued' || d.state === 'running') { waited = true; wait(d.message === 'wartet' ? 'Gerätediagnose wartet auf andere Auswertungen …' : d.message); timer = setTimeout(() => load(false), 1500); return; }
    if (d.state === 'error') {
      host.innerHTML = `<div class="panel-h"><h2>Dein Gerät</h2><span class="grow"></span><button class="btn ghost sm" data-dg-redo>${icon('refresh')}Erneut versuchen</button></div><p class="mut sm">${esc(d.message || 'Nicht berechenbar.')}</p>`;
    } else {
      const devs = await api('/stacking/diagnose/devices').then(x => x.devices).catch(() => []);
      if (!host.isConnected) return;
      render(host, d, devs);
      // Diagnose eben erst fertig: Tutor-Befunde (Nachführung, Fokus, Bildfeld) neu laden
      const tb = host.parentElement?.querySelector('#anTutorBox');
      if (waited && tb && typeof tutorPanel === 'function' && typeof tutorCache !== 'undefined') { tutorCache.delete(r.id); tutorPanel(tb, r); }
      waited = false;
    }
  };
  // Delegiert am bleibenden Behälter: der Knopf wird bei jedem Nachtwechsel (render) neu gezeichnet
  host.addEventListener('click', (e) => { if (e.target.closest('[data-dg-redo]')) load(true); });
  wait();
  load(false);
}

// Analyse-Dashboard: je Gerät Kennzahlenleiste + drei Verläufe in einer Reihe (Formensprache 0.38.3)
function diagDevices(list) {
  const head = '<div class="panel-h"><div><h2>Gerätediagnose</h2><p class="mut sm">Nachführung, Fokus und Bildfeld je Gerät über alle Nächte – wird dein Gerät schlechter?</p></div></div>';
  if (!list?.length) return head + '<p class="mut sm">Noch keine Gerätediagnosen – „Alles analysieren“ mit „Gerät“ (lokal, ohne Netz).</p>';
  return head + list.map(dv => `<div class="dg-dev"><h3>${icon('camera')}${esc(dv.name || dv.key || 'Gerät')}</h3>${deviceTrend(dv)}</div>`).join('');
}

window.diagPanel = diagPanel;
window.diagDevices = diagDevices;
})();
