'use strict';
// HR-Diagramm (Farben-Helligkeits-Diagramm) von Sternhaufen – Abschnitt im Reiter Analyse (seit 0.41.0, Wunsch 9).
// Daten: /stacking/results/<id>/hrd (eigener Endpunkt, nicht in der gepollten Projektansicht). Eigene Messung = farbige Punkte
// (Haufenmitglieder) auf der Gaia-Skala, Gaias eigene Werte der Mitglieder als graue Wolke dahinter. Klick auf einen Stern zeigt
// ihn im Bild (opts.onFocus). Erscheint nur bei Sternhaufen bzw. wenn eine Messung genug Mitglieder gefunden hat.
// Hilfen aus depth.js: insightLoad, insightData, lyText, yrText, de1, starColor.

const HRD_LAYERS = { field: store.get('analyse.hrd.feld', false), gaia: store.get('analyse.hrd.gaia', true) };

// host: Abschnitt (wird ein-/ausgeblendet), opts: { onFocus(x, y, w), onShow(sichtbar) }
function hrdPanel(host, r, opts = {}) {
  if (!host) return;
  let timer = 0;
  const show = (on) => { host.hidden = !on; opts.onShow?.(on); };
  const head = (extra = '') => `<div class="panel-h"><h2>HR-Diagramm des Haufens</h2><span class="grow"></span>${extra}</div>`;
  const poll = async (force) => {
    clearTimeout(timer);
    if (!host.isConnected) return;
    let d;
    try { d = await insightLoad('hrd', r.id, force); } catch (e) { show(false); return; }
    if (!host.isConnected) return;
    // nur bei Sternhaufen – oder wenn eine Messung tatsächlich einen Haufen gefunden hat (z. B. M45, im Katalog als Reflexionsnebel)
    const found = d.state === 'done' && d.haufen?.erkannt;
    if (!d.haufen_art && !found) { show(false); return; }
    show(true);
    draw(d);
    if (d.state === 'queued' || d.state === 'running') timer = setTimeout(() => poll(true), 2000);
  };
  const run = async () => {
    try { await send(`/stacking/results/${r.id}/hrd`, {}); insightData.delete(`hrd:${r.id}`); poll(true); } catch (e) { toast(e.message, 'bad'); }
  };
  function draw(d) {
    if (d.state === 'queued' || d.state === 'running') {
      host.innerHTML = head() + `<div class="depth-wait"><span class="spinner"></span><span class="mut sm">${esc(d.message || 'wird gemessen')} …</span></div>`;
      return;
    }
    if (d.state !== 'done') {
      const why = !d.solved ? 'Dieses Bild ist nicht per Plate-Solving gelöst – ohne Himmelskoordinaten lassen sich die Sterne nicht Gaia zuordnen.'
        : d.state === 'error' ? esc(d.message || 'Messung fehlgeschlagen')
        : d.state === 'old' ? 'Die gespeicherte Messung stammt von einer älteren Version der Auswertung.'
        : 'Farbe und Helligkeit jedes Haufensterns aus deinem Bild – geeicht an Gaia, Mitglieder über Gaias Parallaxen und Eigenbewegungen. Daraus lassen sich Entfernung und grob das Alter des Haufens ablesen (es werden nur Himmelskoordinaten gesendet).';
      host.innerHTML = head() + `<p class="mut sm">${why}</p>${d.solved ? `<div class="row depth-empty-acts"><button class="btn sm" data-hrd-run>${icon('star')}${d.state === 'none' ? 'Jetzt messen' : 'Neu messen'}</button></div>` : ''}`;
      $('[data-hrd-run]', host)?.addEventListener('click', run);
      return;
    }
    const hf = d.haufen || {}, dist = d.entfernung, age = d.alter, red = d.roetung, to = d.abknick, cal = d.eichung || {};
    const n = (d.mitglieder || []).length;
    const ageTxt = age ? `~${yrText(age.jahre)}` : '–';
    const ageSub = age ? `${age.hoechstens ? 'höchstens; ' : ''}zwischen ${yrText(age.lo)} und ${yrText(age.hi)}` : hf.kugel ? 'Kugelhaufen: typisch 10–13 Mrd. Jahre' : to ? 'kein sicherer Abknickpunkt' : 'Abknickpunkt nicht erkennbar';
    host.innerHTML = head('<span class="chip">eigene Messung · Gaia DR3</span>') + (!hf.erkannt || n < 15 ? `
      <p class="mut sm">${hf.erkannt ? `Nur ${n} Haufensterne sauber gemessen – zu wenige für ein aussagekräftiges Diagramm. Mehr Belichtungszeit oder ein schärferes Bild helfen.`
        : 'In Gaias Eigenbewegungen zeigt sich hier keine Sterngruppe, die sich gemeinsam bewegt – entweder ist der Haufen sehr locker, größer als das Bildfeld, oder seine Sterne sind für das Bild zu schwach.'}</p>
      <div class="row depth-acts"><span class="grow"></span><button class="linkbtn sm" data-hrd-run>Neu messen</button></div>` : `
      <div class="depth-kpis">
        <div><small>Haufensterne</small><b class="tnum" data-count>${nf.format(n)}</b><span>gemessen · ${nf.format(hf.n_gaia || 0)} laut Gaia</span></div>
        <div><small>Entfernung</small><b class="tnum" data-count>${dist ? lyText(dist.ly) : '–'}</b><span>${dist ? `${nf.format(dist.pc)} pc · ${dist.pc_hi ? `${nf.format(dist.pc_lo)}–${nf.format(dist.pc_hi)} pc` : 'unsicher'}` : 'Parallaxe zu ungenau'}</span></div>
        <div><small>Alter (grob)</small><b class="tnum">${ageTxt}</b><span>${ageSub}</span></div>
        <div><small>Rötung durch Staub</small><b class="tnum" data-count>${red ? `${de1(red.E, 2)} mag` : '–'}</b><span>${red ? `E(BP−RP), Helligkeit −${de1(red.A_G, 1)} mag` : hf.kugel ? 'nicht geschätzt' : 'keine Hauptreihe angepasst'}</span></div>
      </div>
      <div class="hrd-wrap">
        <div class="hrd-plot">${hrdSvg(d)}</div>
        <div class="hrd-side">
          <div class="hrd-legend sm">
            <span><i class="lg own"></i>Haufenstern (deine Messung)</span>
            <label><input type="checkbox" data-layer="gaia"${HRD_LAYERS.gaia ? ' checked' : ''}><i class="lg gaia"></i>Gaia-Werte der Mitglieder</label>
            <label><input type="checkbox" data-layer="field"${HRD_LAYERS.field ? ' checked' : ''}><i class="lg field"></i>Feldsterne (nicht im Haufen)</label>
            ${d.hauptreihe?.length ? '<span><i class="lg ms"></i>Hauptreihe (genähert)</span>' : ''}
            ${to ? `<span><i class="lg to"></i>${to.erwartet ? 'erwarteter Abknickpunkt' : 'Abknickpunkt'}</span>` : ''}
          </div>
          <ul class="depth-facts hrd-facts">
            ${to && !to.erwartet ? `<li><span class="dot main"></span><div>Abknickpunkt bei <b>G ${de1(to.g, 1)}</b><div class="dim sm">absolut M<sub>G</sub> ${de1(to.MG, 1)} · Eigenfarbe ${de1(to.c0, 2)}${to.grenze === 'hell' ? ' · hellere Sterne sind im Bild übersättigt' : ''}</div></div></li>` : ''}
            ${to?.erwartet ? `<li><span class="dot main"></span><div>Abknickpunkt erwartet bei <b>G ${de1(to.g, 1)}</b><div class="dim sm">${to.sichtbar ? 'liegt im Bild – die Hauptreihe darunter ist kaum noch erfasst' : 'liegt unter der Grenzgröße deines Bildes – zu sehen sind die Riesen'}</div></div></li>` : ''}
            ${dist ? `<li><span class="dot near"></span><div>Entfernungsmodul <b>${de1(dist.mu, 2)} ± ${de1(dist.mu_err, 2)}</b><div class="dim sm">aus der mittleren Gaia-Parallaxe ${de1(dist.plx, 3)} ± ${de1(dist.plx_err, 3)} mas</div></div></li>` : ''}
            ${d.gaia_schaetzung?.alter && age ? `<li><span class="dot faint"></span><div>Gegenprobe mit Gaias Werten: <b>~${yrText(d.gaia_schaetzung.alter.jahre)}</b><div class="dim sm">gleiche Hauptreihe und Entfernung, Gaia statt deiner Messung</div></div></li>` : ''}
            <li><span class="dot gal"></span><div>Geeicht an ${nf.format(cal.n || 0)} Gaia-Sternen<div class="dim sm">Streuung Farbe ±${de1(cal.farbe_streuung, 2)} mag, Helligkeit ±${de1(cal.hell_streuung, 2)} mag${hf.verunreinigung != null ? ` · Feldsterne unter den Mitgliedern ~${Math.round(hf.verunreinigung * 100)} %` : ''}</div></div></li>
          </ul>
        </div>
      </div>
      <div class="hrd-foot"><details class="hrd-help"><summary>Was zeigt das Diagramm?</summary><div class="readable sm">
        <p>Jeder Punkt ist ein Stern des Haufens: <b>rechts</b> rötlicher (kühler), <b>links</b> bläulicher (heißer), <b>oben</b> heller. Weil alle Haufensterne
          etwa gleich weit entfernt und gleich alt sind, ordnen sie sich zu Linien statt zu einer Wolke.</p>
        <p>Das schräge Band ist die <b>Hauptreihe</b>: Sterne, die in ihrem Kern Wasserstoff verbrennen. Schwere Sterne sind heller und blauer – und verbrauchen
          ihren Vorrat viel schneller. Je älter ein Haufen, desto weiter unten „knickt“ die Hauptreihe nach rechts zu den Riesen ab. Dieser
          <b>Abknickpunkt</b> verrät grob das Alter.</p>
        <p>Die Entfernung stammt aus Gaias Parallaxen, die Haufenmitglieder aus Gaias Eigenbewegungen (sie ziehen gemeinsam über den Himmel). Staub zwischen uns
          und dem Haufen macht die Sterne röter und schwächer (<b>Rötung</b>). Die Hauptreihe ist eine Näherung für sonnenähnliche Zusammensetzung – das Alter
          ist deshalb nur auf etwa einen Faktor 2 genau, bei Kugelsternhaufen schätzen wir es gar nicht.</p></div></details>
      <button class="linkbtn sm" data-hrd-run>Neu messen</button></div>`)
      + (d.hinweise || []).map(h => `<p class="dim sm depth-note">${esc(h)}</p>`).join('');
    $('[data-hrd-run]', host)?.addEventListener('click', run);
    const plot = $('.hrd-plot', host);
    plot?.classList.toggle('no-gaia', !HRD_LAYERS.gaia);
    plot?.classList.toggle('no-field', !HRD_LAYERS.field);
    for (const cb of $$('[data-layer]', host)) cb.addEventListener('change', () => {
      HRD_LAYERS[cb.dataset.layer] = cb.checked;
      store.set(cb.dataset.layer === 'gaia' ? 'analyse.hrd.gaia' : 'analyse.hrd.feld', cb.checked);
      plot.classList.toggle(cb.dataset.layer === 'gaia' ? 'no-gaia' : 'no-field', !cb.checked);
    });
    if (opts.onFocus && d.bild) {
      const go = (el) => { const i = +el.dataset.i, m = d.mitglieder[i]; if (m?.x != null) opts.onFocus(m.x, m.y, d.bild.w); };
      plot?.addEventListener('click', (e) => { const c = e.target.closest('[data-i]'); if (c) go(c); });
      plot?.addEventListener('keydown', (e) => { const c = e.target.closest('[data-i]'); if (c && e.key === 'Enter') go(c); });
    }
  }
  host.innerHTML = head() + '<div class="skel-line"></div>';
  poll(false);
}

// Diagramm als SVG: x = Farbe BP−RP, y = G (hell oben); rechte Achse absolute Helligkeit M_G, wenn Entfernung und Rötung bekannt
function hrdSvg(d) {
  const W = 680, H = 340, L = 46, R = d.entfernung ? 46 : 14, T = 12, B = 38;
  const mem = d.mitglieder || [], gaia = d.gaia || [], field = d.feld || [];
  const cs = mem.map(m => m.c), gs = mem.map(m => m.g);
  const q = (a, p) => { const s = [...a].sort((x, y) => x - y); return s[Math.max(0, Math.min(s.length - 1, Math.round(p * (s.length - 1))))]; };
  const x0 = Math.min(-0.2, q(cs, 0.01) - 0.15), x1 = Math.max(1.6, Math.min(3.6, q(cs, 0.99) + 0.25));
  const gAll = gs.concat(d.abknick?.g != null ? [d.abknick.g] : []);
  const y0 = Math.floor(Math.min(...gAll) - 0.5), y1 = Math.ceil(Math.max(...gAll) + 0.5);
  const X = (c) => L + (c - x0) / (x1 - x0) * (W - L - R), Y = (g) => T + (g - y0) / (y1 - y0) * (H - T - B);
  const inb = (c, g) => c >= x0 && c <= x1 && g >= y0 && g <= y1;
  const f2 = (v) => v.toFixed(1);
  const ticks = (a, b, step) => { const o = []; for (let v = Math.ceil(a / step) * step; v <= b + 1e-9; v += step) o.push(+v.toFixed(2)); return o; };
  const xs = ticks(x0, x1, x1 - x0 > 2.5 ? 1 : 0.5), ys = ticks(y0, y1, y1 - y0 > 8 ? 2 : 1);
  const grid = xs.map(v => `<line class="hg" x1="${f2(X(v))}" x2="${f2(X(v))}" y1="${T}" y2="${H - B}"/><text class="hx" x="${f2(X(v))}" y="${H - B + 15}">${de1(v, Math.abs(v % 1) > 0 ? 1 : 0)}</text>`).join('')
    + ys.map(v => `<line class="hg" x1="${L}" x2="${W - R}" y1="${f2(Y(v))}" y2="${f2(Y(v))}"/><text class="hy" x="${L - 6}" y="${f2(Y(v) + 4)}">${v}</text>`).join('');
  // rechte Achse: M_G = G − μ − A_G
  let right = '';
  if (d.entfernung) {
    const off = d.entfernung.mu + (d.roetung?.A_G || 0);
    right = ticks(y0 - off, y1 - off, y1 - y0 > 8 ? 2 : 1).map(m => `<text class="hy r" x="${W - R + 6}" y="${f2(Y(m + off) + 4)}">${m}</text>`).join('')
      + `<text class="hl" transform="translate(${W - 8} ${(T + H - B) / 2}) rotate(90)">absolute Helligkeit M<tspan baseline-shift="sub" font-size="9">G</tspan></text>`;
  }
  const rgb = (c) => { const [r, g, b] = starColor(c); return `rgb(${Math.round(r * 255)},${Math.round(g * 255)},${Math.round(b * 255)})`; };
  // Hintergrund: Sternfarbe entlang der x-Achse (gleiche Umrechnung wie die Punkte) – zart über die Fläche, kräftig als Band unten
  const stops = Array.from({ length: 9 }, (_, k) => `<stop offset="${k / 8}" stop-color="${rgb(x0 + (x1 - x0) * k / 8)}"/>`).join('');
  const band = `<defs><linearGradient id="hrdBand" x1="0" x2="1" y1="0" y2="0">${stops}</linearGradient></defs>
    <rect class="hrd-tint" x="${L}" y="${T}" width="${W - L - R}" height="${H - T - B}" fill="url(#hrdBand)"/>
    <rect class="hrd-band" x="${L}" y="${H - B - 4}" width="${W - L - R}" height="4" fill="url(#hrdBand)"/>`;
  const gaiaPts = gaia.filter(([c, g]) => inb(c, g)).map(([c, g]) => `<circle cx="${f2(X(c))}" cy="${f2(Y(g))}" r="1.6"/>`).join('');
  const fieldPts = field.filter(([c, g]) => inb(c, g)).map(([c, g]) => `<circle cx="${f2(X(c))}" cy="${f2(Y(g))}" r="1.5"/>`).join('');
  const ms = (d.hauptreihe || []).filter(([c, g]) => inb(c, g));
  const msPath = ms.length > 1 ? `<path class="hrd-ms" d="M${ms.map(([c, g]) => `${f2(X(c))},${f2(Y(g))}`).join('L')}"/>` : '';
  const to = d.abknick;
  const toMark = !to ? '' : to.erwartet ? (to.g >= y0 && to.g <= y1 ? `<g class="hrd-to exp"><line x1="${L}" x2="${W - R}" y1="${f2(Y(to.g))}" y2="${f2(Y(to.g))}"/><text x="${L + 6}" y="${f2(Y(to.g) - 5)}">erwarteter Abknickpunkt</text></g>` : '')
    : `<g class="hrd-to"><circle cx="${f2(X(to.c))}" cy="${f2(Y(to.g))}" r="9"/><text x="${f2(X(to.c) + 13)}" y="${f2(Y(to.g) + 4)}">Abknickpunkt</text></g>`;
  const own = mem.map((m, i) => !inb(m.c, m.g) ? '' : `<circle class="hp" data-i="${i}" tabindex="-1" cx="${f2(X(m.c))}" cy="${f2(Y(m.g))}" r="2.8" fill="${rgb(m.c)}" style="--k:${(i % 40) * 12}ms"><title>${esc(m.id)}
G ${de1(m.g, 2)} (Gaia ${de1(m.gg, 2)}) · BP−RP ${de1(m.c, 2)} ± ${de1(m.ce, 2)}${m.gc != null ? ` (Gaia ${de1(m.gc, 2)})` : ''}${m.x != null ? '\nKlick zeigt ihn im Bild' : ''}</title></circle>`).join('');
  return `<svg viewBox="0 0 ${W} ${H}" class="hrd-svg" role="img" aria-label="Farben-Helligkeits-Diagramm: ${mem.length} Haufensterne">
    ${band}<g class="hrd-grid">${grid}${right}
      <text class="hl" x="${(L + W - R) / 2}" y="${H - 6}">Farbe BP−RP (heiß ← → kühl)</text>
      <text class="hl" transform="translate(12 ${(T + H - B) / 2}) rotate(-90)">Helligkeit G – oben heller</text></g>
    <g class="hrd-gaia">${gaiaPts}</g><g class="hrd-field">${fieldPts}</g>${msPath}<g class="hrd-own">${own}</g>${toMark}</svg>`;
}
