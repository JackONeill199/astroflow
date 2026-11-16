'use strict';
// Lichtkurven veränderlicher Sterne – Abschnitt im Reiter Analyse (Wunsch 8).
// Daten: /stacking/results/<id>/lightcurves (Zusammenfassung), …/lightcurves/star/<i>?bin=n (Kurve eines Sterns), …/export (CSV, AAVSO).
// Eigene Messung in den Einzelbildern, geeicht an Gaia-Vergleichssternen; bekannte Veränderliche aus VSX, unbekannte Schwankungen nur
// als „möglicher Veränderlicher“. Klick auf „Im Bild“ zeigt den Stern im Betrachter (opts.onFocus).
// Hilfen aus depth.js: insightLoad, insightData, de1.

const LC_VIEW = { bin: store.get('analyse.lc.bin', 1), mode: store.get('analyse.lc.modus', 'zeit') };
const lcNum = (v, d = 2) => v == null ? '–' : de1(v, d);
const lcPeriod = (p) => p == null ? '–' : p < 0.1 ? `${de1(p * 24 * 60, 0)} min` : p < 2 ? `${de1(p * 24, 1)} h` : `${de1(p, p < 20 ? 2 : 0)} d`;

function lightcurvePanel(host, r, opts = {}) {
  if (!host) return;
  let timer = 0, sel = null, data = null;
  const head = (extra = '') => `<div class="panel-h"><h2>Lichtkurven veränderlicher Sterne</h2><span class="grow"></span>${extra}</div>`;
  const poll = async (force) => {
    clearTimeout(timer);
    if (!host.isConnected) return;
    let d;
    try { d = await insightLoad('lightcurves', r.id, force); } catch (e) { host.innerHTML = head() + `<p class="mut sm">${esc(e.message)}</p>`; return; }
    if (!host.isConnected) return;
    draw(d);
    if (d.state === 'queued' || d.state === 'running') timer = setTimeout(() => poll(true), 2000);
  };
  const run = async () => {
    try { await send(`/stacking/results/${r.id}/lightcurves`, {}); insightData.delete(`lightcurves:${r.id}`); poll(true); } catch (e) { toast(e.message, 'bad'); }
  };
  function draw(d) {
    data = d;
    if (d.state === 'queued' || d.state === 'running') {
      host.innerHTML = head() + `<div class="depth-wait"><span class="spinner"></span><span class="mut sm">${esc(d.message || 'wird gemessen')} …</span></div>`;
      return;
    }
    if (d.state !== 'done') {
      const few = (d.rohbilder || 0) < (d.min_rohbilder || 12);
      const why = !d.solved ? 'Dieses Bild ist nicht per Plate-Solving gelöst – ohne Himmelskoordinaten lassen sich die Sterne nicht Gaia zuordnen.'
        : few ? `Für Lichtkurven braucht es die Einzelbilder – von dieser Aufnahme liegen nur noch ${nf.format(d.rohbilder || 0)} Rohbilder vor (sie werden nach dem Stacken oft gelöscht, um Platz zu sparen). Bei der nächsten Nacht die Rohbilder behalten (Einstellungen → Speicher).`
        : d.state === 'error' ? esc(d.message || 'Messung fehlgeschlagen')
        : d.state === 'old' ? 'Die gespeicherte Messung stammt von einer älteren Version der Auswertung.'
        : `Helligkeit jedes Sterns in jedem der ${nf.format(d.rohbilder)} Einzelbilder – geeicht an Gaia-Vergleichssternen. Bekannte Veränderliche werden aus dem AAVSO-Katalog VSX erkannt, auffällige Schwankungen anderer Sterne gezeigt. Gesendet werden nur Himmelskoordinaten; die Messung dauert je nach Zahl der Bilder einige Minuten.`;
      host.innerHTML = head() + `<p class="mut sm">${why}</p>${d.solved && !few ? `<div class="row depth-empty-acts"><button class="btn sm" data-lc-run>${icon('pulse')}${d.state === 'none' ? 'Lichtkurven messen' : 'Neu messen'}</button></div>` : ''}`;
      $('[data-lc-run]', host)?.addEventListener('click', run);
      return;
    }
    const st = d.sterne || [], au = d.aufnahmen || {}, ens = d.ensemble || {};
    const known = st.filter(s => s.bekannt), maybe = st.filter(s => s.moeglich);
    const varKnown = known.filter(s => s.signifikant), crowd = known.filter(s => s.gedraenge), clean = known.filter(s => !s.gedraenge);
    const best = d.beste_genauigkeit;
    const list = [...clean, ...maybe, ...st.filter(s => s.pruefstern), ...crowd];
    if (sel == null || !list.some(s => s.i === sel)) sel = (varKnown[0] || maybe[0] || clean[0] || list[0])?.i ?? null;
    const flagged = Object.values(au.markiert || {}).reduce((a, b) => a + b, 0);
    host.innerHTML = head('<span class="chip">eigene Messung · Gaia DR3' + (d.katalog ? ` · ${esc(d.katalog)}` : '') + '</span>') + `
      <div class="depth-kpis lc-kpis">
        <div><small>Bekannte Veränderliche</small><b class="tnum">${nf.format(clean.length)}</b><span>gemessen · ${nf.format(varKnown.length)} schwanken messbar${crowd.length ? ` · ${nf.format(crowd.length)} im Gedränge` : ''} · ${nf.format(d.n_bekannt_feld || 0)} im Katalog</span></div>
        <div><small>Mögliche Veränderliche</small><b class="tnum">${nf.format(maybe.length)}</b><span>nicht im Katalog – keine Entdeckung, nur auffällig</span></div>
        <div><small>Aufnahmen</small><b class="tnum">${nf.format(au.gut || 0)}</b><span>von ${nf.format(au.n || 0)} · ${de1(au.spanne_h || 0, 1)} h${(au.naechte || []).length > 1 ? ` · ${au.naechte.length} Nächte` : ''}${flagged ? ` · ${flagged} ausgelassen` : ''}</span></div>
        <div><small>Genauigkeit</small><b class="tnum">${best ? `±${de1(best.rms, 3)} mag` : '–'}</b><span>${best ? `je Aufnahme bei V ${best.v} · ${nf.format(ens.n || 0)} Vergleichssterne` : 'zu wenige konstante Sterne'}</span></div>
      </div>
      <div class="lc-wrap">
        <div class="lc-list" role="listbox" aria-label="Sterne mit Lichtkurve">${list.length ? [...clean, ...maybe, ...st.filter(s => s.pruefstern)].map(s => row(s)).join('')
          + (crowd.length ? `<details class="lc-crowd"${!clean.length && !maybe.length ? ' open' : ''}><summary class="sm">${nf.format(crowd.length)} im Gedränge – nicht trennbar</summary>
              <p class="dim sm">Ein heller Nachbar liegt mit in der Messblende (z. B. im Kern eines Sternhaufens). Was schwankt, ist vor allem Rauschen und Nachbarlicht.</p>${crowd.map(s => row(s)).join('')}</details>` : '')
          : '<p class="mut sm">Keine Veränderlichen im Feld gemessen.</p>'}</div>
        <div class="lc-main">
          <div class="row lc-tools">
            <div class="seg" data-lc-mode><button data-m="zeit"${LC_VIEW.mode === 'zeit' ? ' class="on"' : ''}>Zeit</button><button data-m="phase"${LC_VIEW.mode === 'phase' ? ' class="on"' : ''}>Phase</button></div>
            <div class="seg" data-lc-bin title="Mehrere Aufnahmen mitteln – weniger Rauschen, weniger Zeitauflösung">${[1, 3, 5, 10].map(n => `<button data-b="${n}"${LC_VIEW.bin === n ? ' class="on"' : ''}>${n === 1 ? 'einzeln' : `${n}er`}</button>`).join('')}</div>
            <span class="grow"></span>
            <button class="linkbtn sm" data-lc-focus hidden>${icon('target')}Im Bild</button>
          </div>
          <div class="lc-plot" aria-live="polite"><div class="skel-line"></div></div>
          <div class="lc-info sm"></div>
        </div>
      </div>
      <div class="lc-export">
        <p class="lc-warn sm">${icon('alert')}<span><b>Nicht geprüft.</b> Die Messung ist automatisch. Vor dem Einreichen bei der AAVSO jede Zeile kontrollieren – Vergleichssterne stammen aus Gaia, nicht aus einer AAVSO-Sequenz; Beobachterkürzel eintragen.</span></p>
        <div class="row"><a class="btn sm" data-lc-x="csv1">CSV (dieser Stern)</a><a class="btn sm" data-lc-x="csv">CSV (alle)</a><a class="btn sm" data-lc-x="aavso">AAVSO-Format (bekannte)</a></div>
      </div>
      ${d.genauigkeit?.length ? `<p class="dim sm lc-acc">Genauigkeit je Aufnahme (Streuung konstanter Sterne): ${d.genauigkeit.map(a => `V ${a.v}: ±${de1(a.rms, 3)}`).join(' · ')} mag. Skala ${esc(d.skala || '')}, Filter ${esc(d.filter || '')}.</p>` : ''}
      ${(d.hinweise || []).map(h => `<p class="dim sm depth-note">${esc(h)}</p>`).join('')}
      <div class="hrd-foot"><details class="hrd-help"><summary>Wie wird gemessen?</summary><div class="readable sm">
        <p>In jedem Einzelbild wird das Licht jedes Sterns in einem kleinen Kreis gezählt und der Himmelshintergrund aus einem Ring darum abgezogen. Weil
          Dunst, Höhe über dem Horizont und Belichtung von Bild zu Bild schwanken, zählt nicht die absolute Helligkeit, sondern der Vergleich mit
          <b>Vergleichssternen</b> ähnlicher Helligkeit und Farbe in der Nähe, deren Helligkeit Gaia kennt. Vergleichssterne, die selbst schwanken, fliegen raus.</p>
        <p>Die <b>Genauigkeit</b> ist die Streuung konstanter Sterne gleicher Helligkeit. Ein Stern gilt als <b>möglicher Veränderlicher</b>, wenn er deutlich
          stärker und gleichmäßig (nicht sprunghaft) schwankt. Das ist ein Hinweis, keine Entdeckung – Doppelsterne, Bildfehler oder Wolken können
          ähnlich aussehen. Zeiten sind Belichtungsmitten im baryzentrischen Julianischen Datum (BJD).</p></div></details>
        <button class="linkbtn sm" data-lc-run>Neu messen</button></div>`;
    $('[data-lc-run]', host)?.addEventListener('click', run);
    $('.lc-list', host)?.addEventListener('click', (e) => {
      const b = e.target.closest('[data-i]'); if (!b) return;
      sel = +b.dataset.i;
      $$('.lc-row', host).forEach(x => x.setAttribute('aria-selected', String(+x.dataset.i === sel)));
      curve();
    });
    $('[data-lc-mode]', host).addEventListener('click', (e) => {
      const b = e.target.closest('[data-m]'); if (!b) return;
      LC_VIEW.mode = b.dataset.m; store.set('analyse.lc.modus', LC_VIEW.mode);
      $$('[data-lc-mode] button', host).forEach(x => x.classList.toggle('on', x === b));
      curve();
    });
    $('[data-lc-bin]', host).addEventListener('click', (e) => {
      const b = e.target.closest('[data-b]'); if (!b) return;
      LC_VIEW.bin = +b.dataset.b; store.set('analyse.lc.bin', LC_VIEW.bin);
      $$('[data-lc-bin] button', host).forEach(x => x.classList.toggle('on', x === b));
      curve(); links();
    });
    $('[data-lc-focus]', host)?.addEventListener('click', () => {
      const s = list.find(x => x.i === sel);
      if (s?.x != null && d.bild) opts.onFocus?.(s.x, s.y, d.bild.w);
    });
    links();
    curve();
  }
  function row(s) {
    const tag = s.pruefstern ? '<span class="chip">Prüfstern</span>' : s.gedraenge ? `<span class="chip">${esc(s.typ || 'VAR')}</span>` : s.moeglich ? '<span class="chip lc-maybe">möglicher Veränderlicher</span>'
      : `<span class="chip${s.signifikant ? ' acc' : ''}">${esc(s.typ || 'VAR')}</span>`;
    const name = s.name || s.gaia || `Stern ${s.i}`;
    const per = s.katalog_periode || s.eigene_periode?.periode;
    const amp = s.amp_fit ?? s.amp;
    return `<button class="lc-row" data-i="${s.i}" role="option" aria-selected="${s.i === sel}">
      <span class="lc-n"><b>${esc(name)}</b>${tag}</span>
      <span class="lc-m dim sm">V ${lcNum(s.mittel, 2)} · Δ ${lcNum(amp, 2)} mag · ${per ? `P ${lcPeriod(per)}` : 'ohne Periode'} · ±${lcNum(s.genauigkeit, 3)}${s.gedraenge ? ' · Gedränge' : ''}${s.gesaettigt ? ' · übersättigt' : ''}</span></button>`;
  }
  function links() {
    const base = `/api/stacking/results/${r.id}/lightcurves/export?bin=${LC_VIEW.bin}`;
    for (const a of $$('[data-lc-x]', host)) {
      const k = a.dataset.lcX;
      a.href = k === 'csv1' ? `${base}&fmt=csv&star=${sel ?? 'alle'}` : k === 'csv' ? `${base}&fmt=csv&star=alle` : `${base}&fmt=aavso&star=alle`;
      a.setAttribute('download', '');
    }
  }
  async function curve() {
    const plot = $('.lc-plot', host), info = $('.lc-info', host), fb = $('[data-lc-focus]', host);
    if (!plot || sel == null) { if (plot) plot.innerHTML = '<p class="mut sm">Kein Stern gewählt.</p>'; return; }
    links();
    const s = (data.sterne || []).find(x => x.i === sel);
    if (fb) fb.hidden = !(s?.x != null && data.bild && opts.onFocus);
    const want = sel;
    let k;
    try { k = await api(`/stacking/results/${r.id}/lightcurves/star/${sel}?bin=${LC_VIEW.bin}`); } catch (e) { plot.innerHTML = `<p class="mut sm">${esc(e.message)}</p>`; return; }
    if (!host.isConnected || want !== sel) return;
    const phase = LC_VIEW.mode === 'phase' && k.phase;
    plot.innerHTML = phase ? lcPhaseSvg(k) : lcTimeSvg(k);
    const per = s.katalog_periode ? `Periode ${lcPeriod(s.katalog_periode)} (VSX)` : s.eigene_periode ? `Periode ${lcPeriod(s.eigene_periode.periode)} (eigene Schätzung${s.eigene_periode.alias ? ', mehrdeutig' : ''})` : 'keine Periode bekannt';
    const cover = s.katalog_periode && s.zyklen != null ? (s.zyklen < 1 ? ` · nur ${Math.round(s.zyklen * 100)} % eines Zyklus erfasst` : ` · ${de1(s.zyklen, 1)} Zyklen erfasst`) : '';
    const why = LC_VIEW.mode === 'phase' && !k.phase ? '<br><span class="dim">Phase: ohne Periode nicht möglich.</span>' : '';
    info.innerHTML = `${esc(s.typ_de || (s.moeglich ? 'möglicher Veränderlicher' : s.pruefstern ? 'konstanter Vergleichsstern (Prüfstern)' : ''))}${s.gaia && s.name !== s.gaia ? ` · <span class="dim">${esc(s.gaia)}</span>` : ''}<br>
      ${per}${cover} · Streuung ${lcNum(s.rms, 3)} mag (erwartet ${lcNum(s.erwartet, 3)}) · ${nf.format(s.n)} Messungen${s.katalog_amp != null ? ` · Katalog-Amplitude ${lcNum(s.katalog_amp, 2)} mag (${esc(s.katalog_band || '')})` : ''}${why}`;
  }
  host.innerHTML = head() + '<div class="skel-line"></div>';
  poll(false);
}

// gemeinsames Achsen-/Rastergerüst; y = Helligkeit (hell oben)
function lcFrame(xs, ys, xlab, fx) {
  const W = 640, H = 300, L = 48, R = 12, T = 12, B = 34;
  const x0 = Math.min(...xs), x1 = Math.max(...xs), lo = Math.min(...ys), hi = Math.max(...ys);
  const pad = Math.max(0.03, (hi - lo) * 0.1);
  const y0 = lo - pad, y1 = hi + pad, dx = (x1 - x0) || 1;
  const X = (v) => L + (v - x0) / dx * (W - L - R), Y = (v) => T + (v - y0) / (y1 - y0) * (H - T - B);
  const step = (span) => { const raw = span / 5, p = 10 ** Math.floor(Math.log10(raw)); return [1, 2, 5, 10].map(m => m * p).find(m => m >= raw); };
  const sy = step(y1 - y0), sx = step(dx);
  let g = '';
  for (let v = Math.ceil(y0 / sy) * sy; v <= y1; v += sy) g += `<line class="hg" x1="${L}" x2="${W - R}" y1="${Y(v).toFixed(1)}" y2="${Y(v).toFixed(1)}"/><text class="hy" x="${L - 6}" y="${(Y(v) + 4).toFixed(1)}">${de1(v, sy < 0.1 ? 2 : 1)}</text>`;
  for (let v = Math.ceil(x0 / sx) * sx; v <= x1 + 1e-9; v += sx) g += `<text class="hx" x="${X(v).toFixed(1)}" y="${H - B + 16}">${fx(v, sx)}</text>`;
  g += `<text class="hl" x="${(L + W - R) / 2}" y="${H - 4}">${xlab}</text><text class="hl" transform="translate(12 ${(T + H - B) / 2}) rotate(-90)">Helligkeit (mag)</text>`;
  return { W, H, X, Y, g };
}

function lcTimeSvg(k) {
  const pts = k.punkte || [];
  if (pts.length < 2) return '<p class="mut sm">Zu wenige Messungen.</p>';
  const t0 = Math.floor(Math.min(...pts.map(p => p[0])) - 0.5) + 0.5;     // Mittag (BJD x,0) vor der ersten Aufnahme
  const span = Math.max(...pts.map(p => p[0])) - Math.min(...pts.map(p => p[0]));
  const inDays = span > 1.5;
  const tx = (t) => inDays ? t - t0 : (t - t0) * 24;
  const good = pts.filter(p => !p[4]);
  const ys = (good.length ? good : pts).map(p => p[1]);
  const xs = pts.map(p => tx(p[0]));
  const f = lcFrame(xs, ys, inDays ? `Tage seit BJD ${nf.format(t0)}` : `Stunden seit BJD ${nf.format(t0)} (Mittag)`, (v, s) => de1(v, s < 1 ? 1 : 0));
  const bars = good.map(p => `<line x1="${f.X(tx(p[0])).toFixed(1)}" x2="${f.X(tx(p[0])).toFixed(1)}" y1="${f.Y(p[1] - p[2]).toFixed(1)}" y2="${f.Y(p[1] + p[2]).toFixed(1)}"/>`).join('');
  const yMin = Math.min(...ys), yMax = Math.max(...ys);
  const why = { wolke: 'Lichtverlust (Wolken/Dunst)', ausreisser: 'Ausreißer im Ensemble', wenig: 'zu wenige Vergleichssterne' };
  // ausgelassene Aufnahmen grau und hohl, an den Rand geklemmt (sonst sprengt ein Wolkenbild die Achse)
  const dots = pts.map(p => p[4]
    ? `<circle class="lc-bad" cx="${f.X(tx(p[0])).toFixed(1)}" cy="${f.Y(Math.min(Math.max(p[1], yMin), yMax)).toFixed(1)}" r="2.6"><title>ausgelassen: ${why[p[4]] || p[4]}</title></circle>`
    : `<circle class="lc-p n${p[3] % 4}" cx="${f.X(tx(p[0])).toFixed(1)}" cy="${f.Y(p[1]).toFixed(1)}" r="2.4"/>`).join('');
  const bin = (k.gebinnt || []).map(p => `<circle class="lc-b" cx="${f.X(tx(p[0])).toFixed(1)}" cy="${f.Y(p[1]).toFixed(1)}" r="3.6"/>`).join('');
  const nights = (k.naechte || []).length > 1 ? `<g class="lc-leg">${k.naechte.map((n, i) => `<g transform="translate(${60 + i * 110} 18)"><circle class="lc-p n${i % 4}" r="4"/><text x="8" y="4">${esc(n)}</text></g>`).join('')}</g>` : '';
  return `<svg viewBox="0 0 ${f.W} ${f.H}" class="lc-svg${k.gebinnt ? ' has-bin' : ''}" role="img" aria-label="Lichtkurve über die Zeit, ${pts.length} Messungen">
    <g class="hrd-grid">${f.g}</g><g class="lc-err">${bars}</g><g>${dots}</g><g>${bin}</g>${nights}</svg>`;
}

function lcPhaseSvg(k) {
  const ph = k.phase;
  const pts = ph.punkte || [];
  if (pts.length < 2) return '<p class="mut sm">Zu wenige Messungen.</p>';
  const ys = pts.map(p => p[1]);
  const f = lcFrame([0, 1], ys, `Phase (Periode ${lcPeriod(ph.periode)}, ${ph.quelle})`, (v) => de1(v, 1));
  const bars = pts.map(p => `<line x1="${f.X(p[0]).toFixed(1)}" x2="${f.X(p[0]).toFixed(1)}" y1="${f.Y(p[1] - p[2]).toFixed(1)}" y2="${f.Y(p[1] + p[2]).toFixed(1)}"/>`).join('');
  const dots = pts.map(p => `<circle class="lc-p n0" cx="${f.X(p[0]).toFixed(1)}" cy="${f.Y(p[1]).toFixed(1)}" r="2.6"/>`).join('');
  const model = ph.modell?.length ? `<path class="lc-model" d="M${ph.modell.map(([a, b]) => `${f.X(a).toFixed(1)},${f.Y(b).toFixed(1)}`).join('L')}"/>` : '';
  return `<svg viewBox="0 0 ${f.W} ${f.H}" class="lc-svg" role="img" aria-label="Phasengefaltete Lichtkurve">
    <g class="hrd-grid">${f.g}</g><g class="lc-err">${bars}</g>${model}<g>${dots}</g></svg>`;
}
