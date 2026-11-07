'use strict';
/* Gerätediagnose (Wunsch 17, seit 0.39.0): Nachführung, Fokus und Bildfeld aus gespeicherten Daten (diagnose.py).
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

// Liniendiagramm über die Nacht: x = Stunden, mehrere Reihen [{pts:[[x,y]], cls, label}], Marken [{x, label}]
// Breite der Diagramme in CSS-Pixeln (render setzt sie aus der Spaltenbreite) – so bleibt die Schrift überall 11 px groß
let CW = 520;
function chart(series, { unit = '', nd = 1, marks = [], label = '', W = CW, H = 170, zero = false, xticks = null, minSpan = 0 } = {}) {
  const all = series.flatMap(s => s.pts).filter(p => p[1] != null && isFinite(p[1]));
  if (all.length < 2) return '<p class="mut sm">Zu wenige Messwerte.</p>';
  const L = 44, R = 10, T = 10, B = 22;
  const x0 = Math.min(...all.map(p => p[0])), x1 = Math.max(...all.map(p => p[0]), x0 + 1e-3);
  let lo = Math.min(...all.map(p => p[1])), hi = Math.max(...all.map(p => p[1]));
  if (zero) { lo = Math.min(lo, 0); hi = Math.max(hi, 0); }
  if (hi - lo < Math.max(minSpan, 1e-6)) { const m = (hi + lo) / 2, h = (minSpan || 2) / 2; lo = m - h; hi = m + h; }
  const pad = (hi - lo) * 0.08; lo -= pad; hi += pad;
  const X = x => L + (x - x0) / (x1 - x0) * (W - L - R), Y = y => T + (hi - y) / (hi - lo) * (H - T - B);
  let g = '';
  for (const v of [lo + pad, (lo + hi) / 2, hi - pad]) g += `<line x1="${L}" x2="${W - R}" y1="${Y(v).toFixed(1)}" y2="${Y(v).toFixed(1)}" class="dg-gl"/><text x="${L - 6}" y="${(Y(v) + 4).toFixed(1)}" text-anchor="end">${de(v, nd)}</text>`;
  // Zeitachse: Stunden, kurze Sitzungen in Minuten
  const hrs = x1 - x0, ticks = xticks || [];
  const step = hrs > 3 ? 1 : hrs > 1.2 ? 0.5 : hrs > 0.5 ? 1 / 6 : hrs > 0.2 ? 1 / 12 : 1 / 30;
  if (!xticks) for (let k = Math.ceil(x0 / step - 1e-9); k * step <= x1 + 1e-9; k++) {
    const t = k * step;
    ticks.push([t, step >= 0.5 ? `${String(t).replace('.', ',')} h` : `${Math.round(t * 60)} min`]);
  }
  ticks.forEach(([t, l], i) => { g += `<text x="${X(t).toFixed(1)}" y="${H - 5}" text-anchor="${xticks ? (i ? 'end' : 'start') : 'middle'}">${esc(l)}</text>`; });
  for (const m of marks) g += `<line x1="${X(m.x).toFixed(1)}" x2="${X(m.x).toFixed(1)}" y1="${T}" y2="${H - B}" class="dg-mark"><title>${esc(m.label)}</title></line>`;
  for (const s of series) {
    const P = s.pts.filter(p => p[1] != null && isFinite(p[1]));
    if (s.dots) g += P.map(p => `<circle cx="${X(p[0]).toFixed(1)}" cy="${Y(p[1]).toFixed(1)}" r="1.8" class="dg-dot ${s.cls || ''}"/>`).join('');
    else if (P.length > 1) g += `<path pathLength="1" class="dg-line ${s.cls || ''}" d="${P.map((p, i) => `${i && !p[2] ? 'L' : 'M'}${X(p[0]).toFixed(1)} ${Y(p[1]).toFixed(1)}`).join('')}"/>`;
  }
  return `<svg class="dg-chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(label)}">${g}</svg>
    ${series.length > 1 || unit ? `<div class="dg-leg sm">${series.map(s => `<span><i class="dg-sw ${s.cls || ''}"></i>${esc(s.label)}</span>`).join('')}<span class="mut">${esc(unit)}</span></div>` : ''}`;
}

// Schärfekarte des Bildfelds: Kacheln relativ zum Median (grün = schärfer, rot = größere Sterne), Pfeil zur unschärferen Seite
function fieldMap(f) {
  const M = f.map || [], vals = M.flat().filter(v => v != null);
  if (!vals.length) return '';
  const med = vals.slice().sort((a, b) => a - b)[Math.floor(vals.length / 2)];
  const rows = M.length, cols = M[0].length;
  const cell = (v) => {
    if (v == null) return '<i class="dg-cell na"></i>';
    const d = (v / med - 1) * 100, k = Math.max(-1, Math.min(1, d / 15));
    return `<i class="dg-cell" style="--k:${k.toFixed(2)}" title="${sign(d, 0)} % gegenüber dem Median">${Math.abs(d) >= 3 ? sign(d, 0) : ''}</i>`;
  };
  const ang = f.tilt_dir != null ? -f.tilt_dir : null;       // im Bild: Norden oben = Stack gespiegelt
  return `<div class="dg-field"><div class="dg-map" style="--cols:${cols};--rows:${rows}">${M.map(r => r.map(cell).join('')).join('')}
      ${ang != null && f.tilt_pct >= 3 ? `<svg class="dg-arrow${f.tilt_sig ? ' sig' : ''}" viewBox="-50 -50 100 100" aria-hidden="true"><g transform="rotate(${ang.toFixed(0)})"><line x1="-22" y1="0" x2="22" y2="0"/><path d="M22 0 L12 -7 L12 7 Z"/></g></svg>` : ''}</div>
    <p class="dim sm">Sterngröße je Bildbereich gegenüber dem Median (Norden oben wie im fertigen Bild).${f.arrow_note || ''}</p></div>`;
}

function trackingHtml(t) {
  if (!t) return '<p class="mut sm">Keine Zuordnung der Aufnahmen zum Referenzbild (kein Master-Stack oder zu wenige Sterne).</p>';
  if (!t.measured) return `<p class="mut sm">${esc(t.reason || 'Keine messbaren Abschnitte.')}</p>`;
  const S = t.series || [];
  const brk = (i) => i > 0 && S[i - 1] && Math.hypot(S[i][1] - S[i - 1][1], S[i][2] - S[i - 1][2]) > 15;
  const pts = (k) => S.map((p, i) => [p[0], p[k], brk(i)]);
  return `<div class="stats dg-kpis">
      <div class="stat"><small>Drift</small><b class="tnum">${t.speed_as_min != null ? de(t.speed_as_min, 0) + '″/min' : de(t.speed_px_s * 60, 1) + ' px/min'}</b><small class="dim">zwischen ${t.recenter} Nachzentrierungen</small></div>
      <div class="stat ${LEVEL(t.smear_ratio, 0.5, 1)}"><small>Verschmieren je Belichtung</small><b class="tnum">${de(t.smear_px, 1)} px</b><small class="dim">${t.smear_ratio != null ? `${de(t.smear_ratio * 100, 0)} % der Sterngröße` : ''}</small></div>
      <div class="stat"><small>Unruhe</small><b class="tnum">${t.jitter_as != null ? de(t.jitter_as, 1) + '″' : de(t.jitter_px, 1) + ' px'}</b><small class="dim">um die Driftgerade</small></div>
      <div class="stat"><small>Bilddrehung</small><b class="tnum">${de(t.rot_deg_min * 60, 1)}°/h</b><small class="dim">${t.rot_smear_px != null ? `${de(t.rot_smear_px, 1)} px am Rand je Belichtung` : ''}</small></div>
    </div>
    ${chart([{ pts: pts(1), cls: 'a', label: 'Versatz x' }, { pts: pts(2), cls: 'b', label: 'Versatz y' }], { unit: 'Pixel gegenüber der ersten Aufnahme', nd: 0, label: 'Lage der Aufnahmen über die Nacht', zero: true })}
    <p class="dim sm">Jede Gerade ist ein Abschnitt zwischen zwei Nachzentrierungen (Sprünge). Ihre Steigung ist die Drift während der Aufnahmen.
      ${t.elong_vs_drift != null ? `Längliche Sterne zeigen ${t.elong_vs_drift <= 25 && t.elong_coherence >= 0.4 ? '<b>in Driftrichtung</b>' : `${de(t.elong_vs_drift, 0)}° neben die Driftrichtung`}.` : ''}
      ${t.periodic ? `Periodischer Anteil: ~${de(t.periodic.period_s / 60, 1)} min, ±${de(t.periodic.amp_px, 1)} px.` : ''}</p>`;
}

function focusHtml(f) {
  if (!f) return '<p class="mut sm">Zu wenige Aufnahmen für eine Fokusmessung.</p>';
  const u = f.unit === 'as' ? '″' : ' px', tm = f.time, S = f.series || [];
  const marks = (f.marks || []).map(m => ({ x: m.h, label: `${m.kind === 'restart' ? 'Neue Beobachtung' : 'Automatisch nachfokussiert'} (${hm(m.t)} UTC)${m.change != null ? `: ${sign(m.change)} %` : ''}` }));
  const verdict = !tm ? 'zu kurz für einen Trend' : tm.sig ? `${sign(tm.pct_h)} % je Stunde` : tm.stable ? 'stabil' : 'nicht eindeutig';
  return `<div class="stats dg-kpis">
      <div class="stat"><small>Sterngröße</small><b class="tnum">${de(f.fwhm, 1)}${u}</b><small class="dim">FWHM, Median</small></div>
      <div class="stat"><small>ohne Luftmasse</small><b class="tnum">${f.airmass ? de(f.fwhm_corr, 1) + u : '–'}</b><small class="dim">${f.airmass ? 'auf Zenit umgerechnet' : 'Standort fehlt'}</small></div>
      <div class="stat ${tm?.sig && tm.pct_h > 0 ? (tm.pct_h >= 8 ? 'warn' : '') : tm?.stable ? 'ok' : ''}"><small>Fokusdrift</small><b class="tnum">${verdict}</b><small class="dim">${tm ? `Bereich ${sign(tm.lo)} … ${sign(tm.hi)} %/h` : ''}</small></div>
      ${f.temp_range ? `<div class="stat"><small>Sensortemperatur</small><b class="tnum">${de(f.temp_range[0], 1)}–${de(f.temp_range[1], 1)} °C</b><small class="dim">${f.temp ? `${sign(f.temp.pct_c)} % je °C${f.temp.sig ? '' : ' (unsicher)'}` : ''}</small></div>` : ''}
    </div>
    ${chart([{ pts: S.map(p => [p[0], p[1]]), cls: 'a', label: 'gemessen', dots: true }, ...(f.airmass ? [{ pts: S.map(p => [p[0], p[2]]), cls: 'b', label: 'ohne Luftmasse', dots: true }] : [])],
      { unit: f.unit === 'as' ? 'FWHM in Bogensekunden' : 'FWHM in Pixeln', label: 'Sterngröße über die Nacht', marks })}
    <p class="dim sm">Ob es Seeing oder Fokus ist, lässt sich bei auf- oder untergehenden Objekten nicht ganz trennen – gemeldet wird eine Drift nur, wenn sie mit und ohne Luftmassen-Korrektur gleich ausfällt.${marks.length ? ' Senkrechte Linien: Nachfokussieren bzw. neue Beobachtung.' : ''}</p>`;
}

function eventsHtml(e) {
  const E = e?.events || [];
  if (!E.length) return `<p class="mut sm">Keine Stöße oder Böen erkannt${e?.frames ? ` – nur ${e.frames} einzelne Ausreißer` : ''}.</p>`;
  return `<ul class="dg-events">${E.map(x => `<li><b class="tnum">${hm(x.start)}${x.end !== x.start ? '–' + hm(x.end) : ''}</b> UTC <span class="chip ${x.n >= 6 ? 'warn' : ''}">${x.n} Aufnahmen ${esc(x.kind)}</span>
      <small class="mut">Sterngröße ×${de(x.fwhm, 1)}${x.ecc != null ? ` · Länglichkeit ${de(x.ecc, 2)}` : ''}</small></li>`).join('')}</ul>`;
}

function fieldHtml(f, ff) {
  if (!f || f.error) return `<p class="mut sm">${f?.error ? 'Bildfeld nicht messbar: ' + esc(f.error) : 'Kein Master-Stack mit genug Sternen.'}</p>`;
  const tilt = f.smeared ? 'unsicher' : `${de(f.tilt_pct, 0)} %`;
  return `<div class="dg-fieldwrap">${fieldMap(f)}<div>
      <div class="stats dg-kpis dg-kpis2">
        <div class="stat ${f.tilt_sig ? (f.tilt_pct >= 20 ? 'bad' : 'warn') : 'ok'}"><small>Schärfeabfall (Verkippung)</small><b class="tnum">${tilt}</b><small class="dim">${f.smeared ? 'Bildfeld drehte sich zwischen den Aufnahmen' : `± ${de(f.tilt_err, 0)} %, größer ${esc(f.soft_side || '')}`}</small></div>
        <div class="stat ${f.curv_sig && f.curv_pct > 0 ? 'warn' : 'ok'}"><small>Rand gegen Mitte</small><b class="tnum">${sign(f.curv_pct, 0)} %</b><small class="dim">± ${de(f.curv_err, 0)} % (Bildfeldwölbung)</small></div>
        <div class="stat"><small>gemessen an</small><b class="tnum">${nf.format(f.stars)}</b><small class="dim">Sternen im Master-Stack</small></div>
      </div>
      ${ff ? `<p class="sm">Einzelaufnahmen (${nf.format(ff.frames)}, je 3×3 Felder): Abfall ${de(ff.tilt_pct, 0)} % ± ${de(ff.tilt_err, 0)} % ${esc(ff.soft_side)} – ${ff.tilt_sig === f.tilt_sig ? 'passt zum Stack' : 'unabhängige Prüfung'}.</p>` : ''}
      <p class="dim sm">Verkippung: Sterne werden zu einer Seite größer (Ebene durch die Sterngrößen). Wölbung: größer zum Rand hin. Gemeldet wird erst ab ${TILT} % Unterschied und dreifacher Messunsicherheit.</p>
    </div></div>`;
}
const TILT = 10;

// Gerätetrend: je Kennzahl eine kleine Kurve über alle Nächte
function deviceTrend(dev) {
  const N = dev.nights || [];
  if (N.length < 2) return '<p class="mut sm">Für einen Verlauf braucht es Diagnosen aus mehreren Nächten – „Alles analysieren“ im Analyse-Dashboard mit „Gerät“.</p>';
  const ts = N.map(n => new Date(n.night + 'T12:00:00Z').getTime() / 864e5);
  const t0 = Math.min(...ts), xt = [[0, fday(N[0].night)], [Math.max(...ts) - t0, fday(N.at(-1).night)]];
  const series = (k) => N.map((n, i) => [ts[i] - t0, n[k]]);
  const TR = dev.trends || {};
  const badge = (k) => TR[k] ? `<span class="chip ${TR[k].worse ? 'warn' : TR[k].better ? 'ok' : ''}">${TR[k].worse ? 'wird schlechter' : TR[k].better ? 'wird besser' : 'gleichbleibend'}</span>` : '';
  const SPAN = { fwhm: 0.6, smear: 0.2, tilt: 6, speed: 6 };
  const box = (k, title, unit, nd) => N.some(n => n[k] != null) ? `<div class="dg-trend"><h4>${title} ${badge(k)}</h4>${chart([{ pts: series(k), cls: 'a', label: title, dots: true }], { nd, W: CW, H: 130, label: title, xticks: xt, minSpan: SPAN[k] })}<small class="mut">${unit}</small></div>` : '';
  return `<div class="dg-trends">${box('fwhm', 'Sterngröße', 'FWHM in ″, Luftmasse herausgerechnet', 1)}${box('smear', 'Verschmieren', 'Anteil der Sterngröße je Belichtung', 2)}
      ${box('tilt', 'Schärfeabfall', '% von Ecke zu Ecke', 0)}${box('speed', 'Drift', '″ je Minute', 0)}</div>
    <p class="dim sm">${N.length} Nächte von ${fday(N[0].night)} bis ${fday(N.at(-1).night)}. Trend = robuste Gerade über die Zeit; „wird schlechter“ erst, wenn sie sicher steigt.</p>`;
}

function setWidth(host, cols) {
  const w = host.clientWidth || 800, two = cols === 2 && !matchMedia('(max-width: 960px)').matches;
  CW = Math.round(Math.max(260, Math.min(760, two ? (w - 64) / 2 : w - 40)));
}

function render(host, d, devs, night) {
  setWidth(host, 2);
  const nights = d.nights || [];
  const n = nights.find(x => x.night === night) || nights.at(-1);
  const dev = (devs || []).find(x => x.key === d.device_key);
  host.innerHTML = `<div class="panel-h"><div><h2>Dein Gerät</h2><p class="mut sm">${esc(d.device?.name || '')} · Nachführung, Fokus und Bildfeld aus der Lage und Schärfe der ${nf.format(d.total)} Aufnahmen und dem Master-Stack – ohne Rohbilder.</p></div>
      <span class="grow"></span><button class="btn ghost sm" data-dg-redo>${icon('refresh')}Neu berechnen</button></div>
    ${nights.length > 1 ? `<div class="seg dg-nights" role="group" aria-label="Nacht">${nights.map(x => `<button data-night="${x.night}"${x === n ? ' class="on"' : ''}>${fday(x.night)}</button>`).join('')}</div>` : ''}
    <div class="dg-grid">
      <div class="dg-block"><h3>${icon('target')}Nachführung</h3>${trackingHtml(n?.tracking)}<h4>Stöße und Böen</h4>${eventsHtml(n?.events)}</div>
      <div class="dg-block"><h3>${icon('sparkle')}Fokus</h3>${focusHtml(n?.focus)}</div>
    </div>
    <div class="dg-block"><h3>${icon('grid')}Bildfeld</h3>${fieldHtml(d.field, d.field_frames)}</div>
    ${dev ? `<div class="dg-block"><h3>${icon('camera')}${esc(dev.name || 'Gerät')} über alle Nächte</h3>${deviceTrend(dev)}</div>` : ''}`;
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

// Analyse-Dashboard: Gerätetrend aller Geräte
function diagDevices(list, host) {
  if (host) setWidth(host, 2);                         // Trends stehen zu zweit nebeneinander
  if (!list?.length) return '<div class="panel-h"><div><h2>Gerätediagnose</h2><p class="mut sm">Nachführung, Fokus und Bildfeld je Gerät über alle Nächte.</p></div></div><p class="mut">Noch keine Gerätediagnosen – „Alles analysieren“ mit „Gerät“ (lokal, ohne Netz).</p>';
  return `<div class="panel-h"><div><h2>Gerätediagnose</h2><p class="mut sm">Nachführung, Fokus und Bildfeld je Gerät über alle Nächte – wird dein Gerät schlechter?</p></div></div>
    ${list.map(dv => `<div class="dg-block"><h3>${icon('camera')}${esc(dv.name || dv.key || 'Gerät')} <small class="mut">${dv.nights.length} Nächte</small></h3>${deviceTrend(dv)}</div>`).join('')}`;
}

window.diagPanel = diagPanel;
window.diagDevices = diagDevices;
})();
