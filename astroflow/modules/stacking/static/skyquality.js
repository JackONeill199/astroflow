'use strict';
/* Himmelsqualität (Wünsche 14–16): Himmelshelligkeit in mag/″², Bortle-Schätzung, Richtung der Lichtverschmutzung,
   Seeing – eigene Ansicht #/himmel, Kasten auf der Objektseite (AFsky.panel), Kurzwert in „Heute Nacht“. */
(() => {
const de = (v, nd = 1) => v == null ? '–' : Number(v).toFixed(nd).replace('.', ',');
const sqm = v => v == null ? '–' : `${de(v, 2)} mag/″²`;
const fday = s => new Date(s + 'T12:00:00Z').toLocaleDateString('de-DE', { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC' });
const FLAG = { twilight: 'Dämmerung', moon: 'Mond', low: 'tief', cloud: 'Dunst/Wolken', nosky: 'nicht messbar' };
const EXPLAIN = `<details class="sq-help"><summary>Was bedeuten die Werte?</summary>
  <p><b>Himmelshelligkeit (mag/″²)</b> – wie hell der Nachthimmel selbst leuchtet, gemessen an deinen Einzelbildern und geeicht an
  Sternen mit bekannter Helligkeit (Gaia-Katalog). Größere Zahl = dunklerer Himmel: Stadt um 18, Vorort 19–20, Land 21, beste Orte 21,8–22.
  Ein Handmessgerät („Sky Quality Meter“) zeigt dieselbe Einheit. Umgerechnet auf den Zenit; Genauigkeit etwa ±0,2–0,4 (je dunkler der Himmel, desto unsicherer – das Kamerarauschen überdeckt dann den Himmel).</p>
  <p><b>Bortle-Skala</b> – 1 (perfekt dunkel) bis 9 (Innenstadt). Die Spanne zeigt die Messunsicherheit.</p>
  <p><b>Seeing / effektive Bildschärfe</b> – Durchmesser der Sternscheibchen (FWHM) in Bogensekunden. Bei Smart-Teleskopen
  stecken Luftunruhe, Optik, Fokus und Nachführung zusammen drin; kleiner ist schärfer.</p>
  <p><b>Lichtrichtung</b> – aus welcher Himmelsrichtung Stadtlicht den Himmel aufhellt: aus dem Helligkeitsgefälle in vielen Bildern
  mit unterschiedlicher Blickrichtung. Je mehr Nächte und Richtungen, desto sicherer.</p>
  <p>Als Wert des Ortes zählen nur Aufnahmen in astronomischer Dunkelheit, ohne Mond, über 30° Höhe und ohne Dunst. Andere Nächte
  zeigen ihren Verlauf, aber keinen Ortswert.</p></details>`;

// Verlauf einer Nacht: Himmelshelligkeit (oben = dunkler) und gestört markierte Punkte
function curveSvg(pts, key = 'sqm', label = 'Himmelshelligkeit') {
  const P = pts.filter(p => p[key] != null);
  if (P.length < 2) return '';
  const D = window.AFdeco, W = 640, H = 150, L = 38, R = D ? D.R : 8, T = 10, B = 22;
  const t0 = P[0].ts, t1 = P[P.length - 1].ts || t0 + 1;
  const vs = P.map(p => p[key]);
  let lo = Math.min(...vs), hi = Math.max(...vs);
  if (hi - lo < 0.5) { const m = (hi + lo) / 2; lo = m - 0.25; hi = m + 0.25; }
  const inv = key === 'sqm';                          // Himmel: dunkler (größer) nach oben; Seeing: schärfer (kleiner) nach oben
  const X = t => L + (t - t0) / Math.max(1, t1 - t0) * (W - L - R);
  const Y = v => T + (inv ? (hi - v) : (v - lo)) / (hi - lo) * (H - T - B);
  // Hintergrund: Himmel (oben dunkel, unten Dunst/Mond) bzw. Schärfe (scharfer Stern oben)
  let g = D ? D.draw(inv ? 'sky' : 'seeing', { L, T, W, R, PW: W - L - R, PH: H - T - B, Y, lo, hi, sharpTop: true }) : '';
  for (const v of [lo, (lo + hi) / 2, hi]) g += `<line x1="${L}" x2="${W - R}" y1="${Y(v)}" y2="${Y(v)}" class="sq-grid"/><text x="${L - 5}" y="${Y(v) + 4}" text-anchor="end">${de(v, 1)}</text>`;
  const step = Math.max(1, Math.round((t1 - t0) / 3600 / 5));
  for (let t = Math.ceil(t0 / 3600) * 3600; t <= t1; t += 3600 * step)
    g += `<text x="${X(t)}" y="${H - 6}" text-anchor="middle">${new Date(t * 1000).toLocaleTimeString('de-DE', { hour: '2-digit', minute: '2-digit' })}</text>`;
  g += `<path class="sq-line" pathLength="1" d="${P.map((p, i) => `${i ? 'L' : 'M'}${X(p.ts).toFixed(1)} ${Y(p[key]).toFixed(1)}`).join('')}"/>`;
  g += P.map(p => `<circle cx="${X(p.ts).toFixed(1)}" cy="${Y(p[key]).toFixed(1)}" r="2.2" class="${p.flags.length ? 'sq-pt off' : 'sq-pt'}"><title>${p.t} UTC · ${de(p[key], 2)}${p.flags.length ? ' · ' + p.flags.map(f => FLAG[f] || f).join(', ') : ''}</title></circle>`).join('');
  return `<svg class="sq-chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(label)} im Verlauf der Nacht">${g}</svg>`;
}

// Windrose der Lichtverschmutzung: Sektoren = Restgefälle der Bilder, Pfeil = angepasste Richtung mit Unsicherheit
function roseSvg(l) {
  const S = 200, C = S / 2, Rr = 78;
  const pt = (az, r) => [C + r * Math.sin(az * Math.PI / 180), C - r * Math.cos(az * Math.PI / 180)];
  let g = `<circle cx="${C}" cy="${C}" r="${Rr}" class="sq-ring"/><circle cx="${C}" cy="${C}" r="${Rr / 2}" class="sq-ring"/>`;
  for (const [a, n] of [[0, 'N'], [90, 'O'], [180, 'S'], [270, 'W']]) { const [x, y] = pt(a, Rr + 12); g += `<text x="${x}" y="${y + 4}" text-anchor="middle">${n}</text>`; }
  l.rose.forEach((v, i) => {
    if (v <= 0) return;
    const a0 = i * 22.5 - 10, a1 = i * 22.5 + 10, r = 8 + v * (Rr - 8);
    const [x0, y0] = pt(a0, r), [x1, y1] = pt(a1, r);
    g += `<path class="sq-sector" d="M${C} ${C}L${x0.toFixed(1)} ${y0.toFixed(1)}A${r} ${r} 0 0 1 ${x1.toFixed(1)} ${y1.toFixed(1)}Z"/>`;
  });
  const sp = Math.min(l.spread_deg, 90);
  if (sp < 90) {
    const [x0, y0] = pt(l.az - sp, Rr), [x1, y1] = pt(l.az + sp, Rr);
    g += `<path class="sq-wedge" d="M${C} ${C}L${x0.toFixed(1)} ${y0.toFixed(1)}A${Rr} ${Rr} 0 ${2 * sp > 180 ? 1 : 0} 1 ${x1.toFixed(1)} ${y1.toFixed(1)}Z"/>`;
  }
  const [ax, ay] = pt(l.az, Rr - 4);
  g += `<line x1="${C}" y1="${C}" x2="${ax.toFixed(1)}" y2="${ay.toFixed(1)}" class="sq-arrow ${l.reliable ? '' : 'weak'}"/>`;
  return `<svg class="sq-rose" viewBox="0 0 ${S} ${S}" role="img" aria-label="Richtung der Lichtverschmutzung: ${esc(l.compass)}">${g}</svg>`;
}

const bortleChip = b => b ? `<span class="chip sq-b${b.class}" title="${esc(b.label)}">${esc(b.text)}</span>` : '';

function lightText(l) {
  if (!l) return '<p class="mut sm">Noch zu wenige Aufnahmen mit Hintergrundebene, verschiedenen Blickrichtungen und ohne Mond.</p>';
  return `<p>${l.reliable ? 'Hellster Horizont etwa' : 'Vermutlich'} aus <b>${esc(l.compass)}</b> (${l.az}° ± ${l.spread_deg}°)</p>
    <p class="mut sm">${l.samples} Aufnahmen aus ${l.groups} Nächten/Objekten, Blickrichtungen über ${l.az_coverage}° des Horizonts.
    ${l.reliable ? '' : 'Noch unsicher – mehr Nächte mit Zielen in verschiedenen Himmelsrichtungen verbessern die Schätzung.'}</p>`;
}


// ---- Saubere Nächte: Ranglisten, bester Ort, Monats-Heatmap ----------------------------------------------------
const WD = ['Mo', 'Di', 'Mi', 'Do', 'Fr', 'Sa', 'So'];
const MON = ['Jan', 'Feb', 'Mär', 'Apr', 'Mai', 'Jun', 'Jul', 'Aug', 'Sep', 'Okt', 'Nov', 'Dez'];
const MONTH_LONG = ['Januar', 'Februar', 'März', 'April', 'Mai', 'Juni', 'Juli', 'August', 'September', 'Oktober', 'November', 'Dezember'];
const hm = ts => new Date(ts * 1000).toLocaleTimeString('de-DE', { hour: '2-digit', minute: '2-digit' });
const CLEAN_TABS = [['score', 'Gesamt'], ['dark', 'Dunkelster Himmel'], ['seeing', 'Schärfstes Seeing'], ['clear', 'Beste Durchsicht']];
const CLEAN_HELP = `<details class="sq-help"><summary>Was heißt „sauber“?</summary>
  <p><b>Sauber = vor allem dunkel.</b> Die Hauptwertung ist die Himmelshelligkeit (mag/″², größer = dunkler) mit der Bortle-Klasse dazu.
  Die <b>Gesamtwertung</b> (0–100) mischt drei Teile: 60 % Dunkelheit, 20 % Seeing, 20 % Durchsicht – jeweils als Rang unter allen Nächten mit Ortswert.</p>
  <p><b>Ortswert:</b> Eine Nacht zählt für Dunkelheit und Gesamtwertung nur, wenn genug Aufnahmen ungestört waren (astronomische Dunkelheit, kein Mond, über 30°, kein Dunst).
  Nächte mit größerer Unsicherheit (ab ±0,35 mag, z. B. Eichung aus anderen Nächten) stehen getrennt darunter.
  <b>Seeing</b> (Sternscheibchen in ″, kleiner = schärfer) und <b>Durchsicht</b> (Verlust an Sternlicht in mag, kleiner = klarer) werden in ihren Ranglisten auch aus Nächten ohne Ortswert gewertet.</p>
  <p><b>Bester Abschnitt:</b> die dunkelste Zeitspanne der Nacht (Median aus fünf aufeinanderfolgenden Messpunkten), in der Zeit deines Browsers.
  Der Wochentag gehört zum Abend, an dem die Nacht begann.</p></details>`;

function cleanSection(c, state) {
  if (!c || !c.n_nights) return '';
  const objs = n => n.objects.map(o => `<a href="#/p/${o.id}">${esc(o.name || 'Objekt')}</a>`).join(', ');
  const day = n => `<b>${WD[n.weekday]}, ${fday(n.night)}</b>`;
  const win = n => n.window ? `${hm(n.window.from)}–${hm(n.window.to)}<small class="dim"> · ${de(n.window.sqm, 2)}</small>` : '<span class="dim">–</span>';
  const best = c.dark[0];
  const bs = c.best_site, bsSite = bs && c.sites.find(s => s.name === bs.name && s.id === bs.id);
  const stats = `<div class="stats sq-stats">
    <div class="stat"><small>Bester Ort</small><b>${bs ? esc(bs.name) : '–'}</b><small class="dim">${bs ? `${sqm(bs.sqm)} · ${bs.n} ${bs.n === 1 ? 'Nacht' : 'Nächte'}${bs.n < 3 ? ' (vorläufig)' : ''}` : 'noch keine Ortswerte'}</small></div>
    <div class="stat"><small>Dunkelste Nacht</small><b class="tnum">${best ? `${WD[best.weekday]}, ${fday(best.night)}` : '–'}</b><small class="dim">${best ? `${esc(best.site_name)} · ${sqm(best.sqm)}` : ''}</small></div>
    <div class="stat"><small>Bester Monat am besten Ort</small><b>${bsSite?.best_month ? MONTH_LONG[bsSite.best_month - 1] : '–'}</b><small class="dim">${bsSite?.best_month ? sqm(bsSite.months[bsSite.best_month - 1].sqm) + ' · Median' : ''}</small></div>
    <div class="stat"><small>Nächte mit Ortswert</small><b class="tnum">${c.n_site_nights} von ${c.n_nights}</b><small class="dim">${c.n_sure} davon sicher</small></div></div>`;
  const tab = state.tab;
  const list = c[tab] || [];
  const dk = tab === 'dark' || tab === 'score';
  const val = n => tab === 'seeing' ? (n.seeing != null ? de(n.seeing, 1) + '″' : '–') : tab === 'clear' ? (n.loss != null ? '−' + de(Math.max(0, n.loss), 2) + ' mag' : '–')
    : tab === 'score' ? `<b>${n.score}</b><small class="dim"> / 100</small>` : sqm(n.sqm);
  const tabs = `<div class="tabs sq-tabs" role="tablist">${CLEAN_TABS.map(([k, l]) => `<a href="#" class="${k === tab ? 'on' : ''}" role="tab" aria-selected="${k === tab}" data-ctab="${k}">${l}</a>`).join('')}</div>`;
  const table = list.length ? `<div class="sq-scroll"><table class="sq-table sq-top"><thead><tr><th>#</th><th>Nacht</th><th>Ort</th><th>${CLEAN_TABS.find(t => t[0] === tab)[1]}</th>
      <th>${dk ? 'Bortle' : 'Himmel'}</th><th>Seeing</th><th>Bester Abschnitt</th><th>Objekte</th></tr></thead><tbody>
      ${list.map((n, i) => `<tr><td class="tnum">${i + 1}</td><td>${day(n)}</td><td>${esc(n.site_name)}</td><td class="tnum">${val(n)}${n.err != null && tab === 'dark' ? `<small class="dim"> ±${de(n.err, 1)}</small>` : ''}</td>
        <td class="tnum">${dk ? (n.bortle ? esc(n.bortle.text.replace('Bortle ', 'B ')) : '–') : n.sqm != null ? sqm(n.sqm) : `<span class="dim" title="gestört – Median aller Aufnahmen">(${de(n.sqm_all, 2)})</span>`}</td>
        <td class="tnum">${n.seeing != null ? de(n.seeing, 1) + '″' : '–'}</td><td class="tnum">${win(n)}</td><td class="sq-objs">${objs(n)}</td></tr>`).join('')}
      </tbody></table></div>` : '<p class="mut sm">Für diese Wertung gibt es noch keine Nächte.</p>';
  // Heatmap Ort × Monat (Median der Nächte mit Ortswert, über alle Jahre)
  const vals = c.sites.flatMap(s => s.months.filter(m => m.sqm != null).map(m => m.sqm));
  const lo = Math.min(...vals), hi = Math.max(...vals);
  const heat = vals.length ? `<div class="sq-scroll"><table class="sq-heat"><thead><tr><th></th>${MON.map(m => `<th>${m}</th>`).join('')}</tr></thead><tbody>
      ${c.sites.filter(s => s.n).map(s => `<tr><th class="sq-hsite" title="${esc(s.name)}">${esc(s.name)}</th>${s.months.map((m, i) => {
        if (m.sqm == null) return '<td class="empty"></td>';
        const t = hi > lo ? (m.sqm - lo) / (hi - lo) : 1;
        return `<td class="${s.best_month === i + 1 ? 'best' : ''}" style="--t:${(t * 100).toFixed(0)}%" title="${esc(s.name)} · ${MONTH_LONG[i]}: ${de(m.sqm, 2)} mag/″² (${m.n} ${m.n === 1 ? 'Nacht' : 'Nächte'})">${de(m.sqm, 1)}<small>${m.n}</small></td>`;
      }).join('')}</tr>`).join('')}</tbody></table></div>
      <p class="mut sm">Median der Ortswerte je Monat, über alle Jahre. Kräftige Farbe = dunkler Himmel; kleine Zahl = Anzahl Nächte; Rahmen = bester Monat des Ortes (bevorzugt mit mindestens zwei Nächten).</p>` : '';
  const unsure = c.uncertain.length ? `<details class="sq-nights"><summary>${c.uncertain.length} unsichere ${c.uncertain.length === 1 ? 'Nacht' : 'Nächte'} (dunkel gemessen, aber ab ±0,35 mag unsicher)</summary>
      <div class="sq-scroll"><table class="sq-table"><thead><tr><th>Nacht</th><th>Ort</th><th>Himmel</th><th>Objekte</th></tr></thead><tbody>
      ${c.uncertain.map(n => `<tr><td>${day(n)}</td><td>${esc(n.site_name)}</td><td class="tnum">${sqm(n.sqm)}<small class="dim"> ±${de(n.err, 1)}</small></td><td class="sq-objs">${objs(n)}</td></tr>`).join('')}</tbody></table></div></details>` : '';
  return `<section class="panel sq-clean"><div class="panel-h"><h2>${icon('globe')}Saubere Nächte</h2><span class="grow"></span></div>
    <p class="mut sm">Wann und wo der Himmel am dunkelsten, ruhigsten und klarsten war – ausgewertet aus den bereits gemessenen Nächten.</p>
    ${stats}${tabs}${table}${unsure}${heat ? `<h3 class="sq-h3">Bester Monat je Ort</h3>${heat}` : ''}${CLEAN_HELP}</section>`;
}

// ---- eigene Ansicht ----------------------------------------------------------------------------------
async function skyView(el) {
  crumbs([['Übersicht', '#/'], ['Himmelsqualität']]);
  let sig = '', ticket = 0, clean = null;
  const cstate = { tab: 'score' };
  try { cstate.tab = localStorage.getItem('af.sky.cleantab') || 'score'; } catch {}
  const paintClean = () => {
    const h = $('#sqClean', el);
    if (!h) return;
    h.innerHTML = cleanSection(clean, cstate);
    h.querySelectorAll('[data-ctab]').forEach(b => b.onclick = (e) => {
      e.preventDefault();
      cstate.tab = b.dataset.ctab;
      try { localStorage.setItem('af.sky.cleantab', cstate.tab); } catch {}
      paintClean();
    });
  };
  el.innerHTML = `<div class="pagehead"><div><h1>Himmelsqualität</h1><p class="mut">Wie dunkel und ruhig der Himmel an deinen Standorten ist – gemessen an deinen eigenen Aufnahmen.</p></div>
    <span class="grow"></span><div class="row"><button class="btn ghost sm" id="sqMissing">Nur neue Objekte</button><button class="btn sm" id="sqAll">${icon('refresh')}Für alle Nächte berechnen</button></div></div><div id="sqJob"></div><div id="sqClean"></div><div id="sqBody"></div>`;
  const start = async (missing) => {
    try { await send('/stacking/sky/compute', { missing }); toast('Himmelsqualität wird berechnet – Fortschritt unter Aktivität.', 'ok'); load(); }
    catch (e) { toast(e.message, 'err'); }
  };
  $('#sqAll', el).onclick = () => start(false);
  $('#sqMissing', el).onclick = () => start(true);
  async function load() {
    const cur = ++ticket;
    let d;
    try { [d, clean] = await Promise.all([api('/stacking/sky'), api('/stacking/sky/clean').catch(() => null)]); } catch (e) { if (cur === ticket) $('#sqBody', el).innerHTML = `<div class="panel">${esc(e.message)}</div>`; return; }
    if (cur !== ticket || !el.isConnected) return;
    const s = JSON.stringify([d, clean]);
    if (s === sig) return;
    sig = s;
    paintClean();
    const j = d.job || {};
    $('#sqJob', el).innerHTML = j.state === 'running' ? `<div class="notice sq-job"><span class="spinner"></span><div><b>Berechnung läuft</b> · ${j.done}/${j.total} Objekte${j.current ? ' · ' + esc(j.current) : ''}
        <div class="progress"><i style="width:${(j.done / Math.max(1, j.total) * 100).toFixed(0)}%"></i></div><div class="mut sm">${esc(j.detail || '')}</div></div></div>`
      : j.state === 'done' && j.errors?.length ? `<div class="notice"><div><b>${j.nights} Nächte ausgewertet, ${j.errors.length} Objekte ohne Ergebnis</b><div class="mut sm">${j.errors.slice(0, 4).map(esc).join('<br>')}</div></div></div>` : '';
    $('#sqAll', el).disabled = $('#sqMissing', el).disabled = j.state === 'running';
    const hints = [!d.gaia && 'Die Eichung braucht den Gaia-Katalog – unter Einstellungen → Bilder die fotometrische Farbkalibrierung einschalten.',
      !d.solver && 'Ohne Plate-Solver (ASTAP) fehlen Bildmaßstab und Eichung für Objekte ohne gespeicherte Lösung.'].filter(Boolean);
    if (!d.sites.length) {
      $('#sqBody', el).innerHTML = `<div class="empty"><h2>Noch keine Messwerte</h2><p class="mut">${d.projects} Objekte mit Einzelbild-Analysen sind vorhanden. „Für alle Nächte berechnen“ wertet sie aus –
        auch ohne Rohbilder, aus den gespeicherten Messwerten.</p>${hints.map(h => `<p class="mut sm">${esc(h)}</p>`).join('')}</div>${EXPLAIN}`;
      return;
    }
    $('#sqBody', el).innerHTML = `<p class="mut sm">${d.computed} von ${d.projects} Objekten ausgewertet. ${hints.map(esc).join(' ')}</p>
      ${d.sites.map(site => {
        const own = site.nights.filter(n => n.sqm != null);
        return `<section class="panel sq-site"><div class="panel-h"><h2>${icon('pin')}${esc(site.name)}</h2><span class="grow"></span>${bortleChip(site.bortle)}</div>
        <div class="stats sq-stats"><div class="stat"><small>Himmelshelligkeit</small><b class="tnum">${site.sqm != null ? sqm(site.sqm) : '–'}</b><small class="dim">Median der Nächte${site.err ? ' · ±' + de(site.err, 1) : ''}</small></div>
          <div class="stat"><small>Dunkelste Nacht</small><b class="tnum">${site.sqm_best != null ? sqm(site.sqm_best) : '–'}</b></div>
          <div class="stat"><small>Bildschärfe</small><b class="tnum">${site.seeing != null ? de(site.seeing, 1) + '″' : '–'}</b><small class="dim">Median${site.seeing_best != null ? ', beste ' + de(site.seeing_best, 1) + '″' : ''}</small></div>
          <div class="stat"><small>Nächte mit Ortswert</small><b class="tnum">${site.n_site_nights} von ${site.n_nights}</b></div></div>
        ${site.bortle ? `<p class="mut sm">${esc(site.bortle.label)}${site.sqm_spread != null ? ` · Streuung zwischen den Nächten ±${de(site.sqm_spread, 2)} mag` : ''}${site.n_site_nights < 3 ? ' · erst wenige Nächte, Wert noch vorläufig' : ''}</p>` : '<p class="mut sm">Noch keine ungestörte Nacht (dunkel, ohne Mond, über 30°) – der Verlauf unten zeigt die gemessenen Werte.</p>'}
        <div class="sq-grid2"><div>${nightsChart(site.nights)}</div>
          <div class="sq-light"><h3>Lichtverschmutzung</h3>${site.light ? roseSvg(site.light) : ''}${lightText(site.light)}</div></div>
        <details class="sq-nights"><summary>${site.nights.length} Nächte</summary><div class="sq-scroll"><table class="sq-table"><thead><tr><th>Nacht</th><th>Himmel</th><th>Seeing</th><th>Objekte</th></tr></thead><tbody>
          ${site.nights.slice().reverse().map(n => `<tr><td>${fday(n.night)}</td><td class="tnum">${n.sqm != null ? sqm(n.sqm) : `<span class="dim" title="gestört – Median aller Aufnahmen">(${de(n.sqm_all, 2)})</span>`}</td>
            <td class="tnum">${n.seeing != null ? de(n.seeing, 1) + '″' : '–'}</td><td class="sq-objs">${n.objects.map(o => `<a href="#/p/${o.id}">${esc(o.name || 'Objekt')}</a>`).join(', ')}</td></tr>`).join('')}
        </tbody></table></div><p class="mut sm">Werte in Klammern: Nacht mit Mond, Dämmerung oder Dunst – kein Ortswert.</p></details></section>`;
      }).join('')}${EXPLAIN}`;
  }
  // Nächte eines Ortes: Ortswerte als Punkte, gestörte Nächte hell
  function nightsChart(nights) {
    const P = nights.map(n => ({ n, v: n.sqm ?? n.sqm_all, own: n.sqm != null })).filter(p => p.v != null);
    if (!P.length) return '';
    const D = window.AFdeco, W = 760, H = 210, L = 38, R = D ? D.R : 10, T = 10, B = 24;
    const ts = P.map(p => Date.parse(p.n.night + 'T12:00:00Z') / 1000), t0 = Math.min(...ts), t1 = Math.max(...ts, t0 + 86400);
    let lo = Math.min(...P.map(p => p.v)), hi = Math.max(...P.map(p => p.v));
    if (hi - lo < 1) { const m = (hi + lo) / 2; lo = m - 0.5; hi = m + 0.5; }
    const X = t => L + (t - t0) / (t1 - t0) * (W - L - R), Y = v => T + (hi - v) / (hi - lo) * (H - T - B);
    let g = D ? D.draw('sky', { L, T, W, R, PW: W - L - R, PH: H - T - B, Y, lo, hi }) : '';
    for (const v of [lo, (lo + hi) / 2, hi]) g += `<line x1="${L}" x2="${W - R}" y1="${Y(v)}" y2="${Y(v)}" class="sq-grid"/><text x="${L - 5}" y="${Y(v) + 4}" text-anchor="end">${de(v, 1)}</text>`;
    const y0 = new Date(t0 * 1000).getUTCFullYear(), y1 = new Date(t1 * 1000).getUTCFullYear();
    for (let y = y0; y <= y1 + 1; y++) { const t = Date.UTC(y, 0, 1) / 1000; if (t > t0 && t < t1) g += `<line x1="${X(t)}" x2="${X(t)}" y1="${T}" y2="${H - B}" class="sq-grid"/>`; }
    g += `<text x="${L}" y="${H - 6}">${fday(P[0].n.night)}</text><text x="${W - R}" y="${H - 6}" text-anchor="end">${fday(P[P.length - 1].n.night)}</text>`;
    g += P.map((p, i) => `<circle cx="${X(ts[i]).toFixed(1)}" cy="${Y(p.v).toFixed(1)}" r="${p.own ? 4 : 3}" class="${p.own ? 'sq-pt' : 'sq-pt off'}"><title>${fday(p.n.night)} · ${de(p.v, 2)} mag/″²${p.own ? '' : ' (gestört)'}</title></circle>`).join('');
    const leg = D ? D.legend([{ cls: 'sq-pt', r: 4, text: 'Ortswert', title: 'Ungestörte Nacht: dunkel, kein Mond, über 30°' },
      { cls: 'sq-pt off', r: 3, text: 'gestört', title: 'Mond, Dämmerung oder Dunst – Median aller Aufnahmen, kein Ortswert' }]) : '';
    return `<p class="mut sm sq-chart-h">Himmelshelligkeit je Nacht – oben = dunkler.</p>${leg}<svg class="sq-chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="Himmelshelligkeit je Nacht">${g}</svg>`;
  }
  await load();
  return { update: load, destroy: () => { ticket++; } };
}

// ---- Objektseite: Kennzahlen der Aufnahmenächte ------------------------------------------------------------
async function panel(host, pid) {
  if (!host) return;
  let d;
  try { d = await api(`/stacking/projects/${pid}/sky`); } catch { host.remove(); return; }
  if (!host.isConnected) return;
  const ns = d.nights.slice().reverse();
  const head = `<div class="panel-h"><h2>Himmel in der Aufnahmenacht</h2><span class="grow"></span><a class="linkbtn sm" href="#/himmel">Alle Standorte</a></div>`;
  if (!ns.length) {
    host.innerHTML = `${head}<p class="mut sm">Himmelshelligkeit, Bortle-Klasse und Seeing dieser Nächte sind noch nicht berechnet.</p>
      <button class="btn ghost sm" id="sqProj" ${d.busy ? 'disabled' : ''}>${icon('refresh')}Jetzt berechnen</button>`;
    $('#sqProj', host)?.addEventListener('click', async (e) => {
      e.target.disabled = true;
      try { await send('/stacking/sky/compute', { project_id: pid }); toast('Wird berechnet …', 'ok'); setTimeout(() => panel(host, pid), 4000); }
      catch (err) { toast(err.message, 'err'); e.target.disabled = false; }
    });
    return;
  }
  host.innerHTML = head + ns.map((n, i) => `<div class="sq-night">${ns.length > 1 ? `<h3>${fday(n.night)}${n.site_name ? ` · ${esc(n.site_name)}` : ''}</h3>` : n.site_name ? `<p class="mut sm">${fday(n.night)} · ${esc(n.site_name)}</p>` : ''}
    <div class="stats sq-kpis"><div class="stat"><small>Himmelshelligkeit</small><b class="tnum">${n.sqm != null ? de(n.sqm, 2) : n.sqm_all != null ? `(${de(n.sqm_all, 2)})` : '–'}</b><small class="dim">mag/″² · Zenit${n.sqm != null && n.err ? ' · ±' + de(n.err, 1) : ''}</small></div>
      <div class="stat"><small>Bortle</small><b>${n.bortle ? esc(n.bortle.text.replace('Bortle ', '')) : '–'}</b><small class="dim">${n.bortle ? esc(n.bortle.label) : 'kein Ortswert'}</small></div>
      <div class="stat"><small>Bildschärfe</small><b class="tnum">${n.seeing != null ? de(n.seeing, 1) + '″' : '–'}</b><small class="dim">${n.seeing_best != null ? 'beste ' + de(n.seeing_best, 1) + '″' : 'FWHM'}</small></div>
      <div class="stat"><small>Durchsicht</small><b class="tnum">${n.transparency != null ? (n.transparency <= 0.1 ? 'klar' : n.transparency <= CLOUD ? 'leicht dunstig' : 'dunstig') : '–'}</b><small class="dim">${n.transparency != null ? '−' + de(Math.max(0, n.transparency), 2) + ' mag Sternlicht' : ''}</small></div></div>
    ${n.note ? `<p class="sm warn">${esc(n.note)}${n.sqm_all != null ? ' – Wert in Klammern ist der Median aller Aufnahmen.' : ''}</p>` : ''}
    ${i < 2 ? curveSvg(n.curve) : ''}
    <p class="mut sm">${n.zp_source === 'device' ? 'Eichung aus anderen Nächten desselben Geräts (±0,1 mag zusätzlich).' : n.zp_fit ? `Geeicht an ${n.zp_fit.stars} Gaia-Sternen.` : ''}
      ${n.zp_error && !n.zp ? 'Keine Eichung möglich: ' + esc(n.zp_error) : ''} ${n.method === 'noise' ? 'Himmel aus dem Photonenrauschen der Einzelbilder (Gerät ohne Dark-Frames).' : n.method === 'level' ? 'Himmel aus dem Hintergrund der kalibrierten Einzelbilder.' : n.zp && !n.method ? 'Für diese Kamera ist das Rauschverhalten unbekannt – mit Dark-Frames kalibrieren, dann wird der Himmel messbar.' : ''}</p></div>`).join('') + EXPLAIN;
}
const CLOUD = 0.35;

// ---- Reiter Analyse: „Der Himmel in dieser Nacht“ (breit) ---------------------------------------------------
// Bortle-Skala 1–9 mit Zeiger (fährt beim Einblenden an seinen Platz, Spanne = Messunsicherheit), Himmelshelligkeit und Seeing als
// Verlauf über die Nacht (Linien zeichnen sich), Windrose der Lichtrichtung des Ortes (aus /stacking/sky, falls genug Daten).
const BORTLE_COL = ['#0b1030', '#16204e', '#1f3570', '#2f5a7c', '#4f7d62', '#8a8a45', '#b07a35', '#c25a2e', '#d23b2b'];
function bortleScale(b) {
  if (!b) return '';
  const pos = (c) => ((c - 0.5) / 9 * 100).toFixed(1);
  const [lo, hi] = b.range || [b.class, b.class];
  return `<div class="sq-bortle" style="--pos:${pos(b.class)}%;--lo:${((lo - 1) / 9 * 100).toFixed(1)}%;--hi:${(hi / 9 * 100).toFixed(1)}%" role="img" aria-label="${esc(b.text)}: ${esc(b.label)}">
    <div class="sq-bortle-bar">${BORTLE_COL.map((c, i) => `<span style="background:${c}">${i + 1}</span>`).join('')}<i class="sq-bortle-span"></i></div>
    <i class="sq-bortle-ptr"><b>${esc(b.text.replace('Bortle ', ''))}</b></i>
    <div class="sq-bortle-ends dim sm"><span>dunkel</span><span>Innenstadt</span></div></div>`;
}
async function analysis(host, pid) {
  if (!host) return;
  host.innerHTML = '<div class="panel-h"><h2>Der Himmel in dieser Nacht</h2></div><div class="skel-line"></div>';
  let d, all = null;
  try { [d, all] = await Promise.all([api(`/stacking/projects/${pid}/sky`), api('/stacking/sky').catch(() => null)]); }
  catch (e) { host.innerHTML = `<div class="panel-h"><h2>Der Himmel in dieser Nacht</h2></div><p class="mut sm">${esc(e.message)}</p>`; return; }
  if (!host.isConnected) return;
  const head = `<div class="panel-h"><h2>Der Himmel in ${d.nights.length > 1 ? 'diesen Nächten' : 'dieser Nacht'}</h2><span class="grow"></span><a class="linkbtn sm" href="#/himmel">Alle Standorte</a></div>`;
  const ns = d.nights.slice().reverse();
  if (!ns.length) {
    host.innerHTML = `${head}<div class="an-empty">${icon('globe')}<div><b>Noch nicht berechnet</b><p class="mut sm">Wie dunkel der Himmel war (Bortle-Klasse, mag/″²), wie ruhig die Luft (Seeing)
      und aus welcher Richtung Stadtlicht kommt – gemessen an deinen Einzelbildern, auch ohne Rohbilder.</p>
      <button class="btn sm" id="sqProj" ${d.busy ? 'disabled' : ''}>${icon('refresh')}Jetzt berechnen</button></div></div>`;
    $('#sqProj', host)?.addEventListener('click', async (e) => {
      e.currentTarget.disabled = true;
      try { await send('/stacking/sky/compute', { project_id: pid }); toast('Wird berechnet …', 'ok'); setTimeout(() => analysis(host, pid), 4000); }
      catch (err) { toast(err.message, 'err'); e.currentTarget.disabled = false; }
    });
    return;
  }
  const siteOf = (n) => all?.sites?.find(s => n.site_id ? s.id === n.site_id : s.name === n.site_name);
  host.innerHTML = head + ns.map((n, i) => {
    const site = siteOf(n), light = site?.light;
    return `<div class="sq-anight">${ns.length > 1 || n.site_name ? `<h3>${fday(n.night)}${n.site_name ? ` · ${esc(n.site_name)}` : ''}</h3>` : ''}
      <div class="sq-agrid">
        <div class="sq-amain">
          <div class="stats sq-kpis"><div class="stat"><small>Himmelshelligkeit</small><b class="tnum" data-count>${n.sqm != null ? de(n.sqm, 2) : n.sqm_all != null ? `(${de(n.sqm_all, 2)})` : '–'}</b><small class="dim">mag/″² · Zenit${n.sqm != null && n.err ? ' · ±' + de(n.err, 1) : ''}</small></div>
            <div class="stat"><small>Bildschärfe</small><b class="tnum" data-count>${n.seeing != null ? de(n.seeing, 1) + '″' : '–'}</b><small class="dim">${n.seeing_best != null ? 'beste ' + de(n.seeing_best, 1) + '″' : 'FWHM'}</small></div>
            <div class="stat"><small>Durchsicht</small><b class="tnum">${n.transparency != null ? (n.transparency <= 0.1 ? 'klar' : n.transparency <= CLOUD ? 'leicht dunstig' : 'dunstig') : '–'}</b><small class="dim">${n.transparency != null ? '−' + de(Math.max(0, n.transparency), 2) + ' mag Sternlicht' : ''}</small></div>
            <div class="stat"><small>Aufnahmen</small><b class="tnum" data-count>${n.good ?? 0} / ${n.frames ?? 0}</b><small class="dim">ungestört / gesamt</small></div></div>
          ${n.bortle ? bortleScale(n.bortle) + `<p class="mut sm">${esc(n.bortle.label)}</p>` : `<p class="mut sm">Keine Bortle-Klasse: ${esc(n.note || 'keine ungestörte Aufnahme (dunkel, ohne Mond, über 30°)')}.</p>`}
          ${i < 3 ? `<div class="sq-curves">${curveSvg(n.curve) ? `<div><small class="mut">Himmelshelligkeit (oben = dunkler)</small>${curveSvg(n.curve)}</div>` : ''}
            ${curveSvg(n.curve, 'seeing', 'Bildschärfe') ? `<div><small class="mut">Sternschärfe (FWHM) – oben = schärfer</small>${curveSvg(n.curve, 'seeing', 'Bildschärfe')}</div>` : ''}</div>` : ''}
        </div>
        <div class="sq-alight"><h3>Lichtrichtung${site ? ` · ${esc(site.name)}` : ''}</h3>${light ? roseSvg(light) : ''}${lightText(light)}</div>
      </div></div>`;
  }).join('') + EXPLAIN;
}

// „Heute Nacht“: Kurzwert für den gewählten Ort
function tonightTile(d) {
  const s = d?.sky_quality;                           // d.sky ist die Wettervorhersage (tonight.plan)
  if (!s?.sqm) return '';
  return `<div title="Gemessen an deinen Aufnahmen an diesem Ort (${s.n_site_nights} Nächte)"><small>Himmel hier</small><b>${de(s.sqm, 1)} mag/″²${s.bortle ? ' · ' + esc(s.bortle.text) : ''}</b></div>`;
}

window.AFsky = { panel, analysis, tonightTile };
const m = AF.modules.stacking;
m.nav.push({ href: '#/himmel', label: 'Himmelsqualität', icon: 'globe', key: 'sky', parent: 'home' });
m.routes.push([/^\/himmel$/, 'sky', skyView]);
})();
