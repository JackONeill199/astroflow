'use strict';
// Reiter „Analyse“ für fertig importierte Bilder (bearbeitet, nicht linear): Objekte im Bild, Bildfeld und Maßstab, grobe Bildqualität,
// eigene Aufnahmen desselben Objekts. Daten: GET /api/stacking/results/<id>/picture-analysis (picture_analysis.py).
// Bewusst ohne Grenzgröße, HR-Diagramm, Farbkalibrierung und Asteroiden – die brauchen lineare Daten bzw. Einzelaufnahmen.

function pictureAnalysisTab(t, ctx) {
  const { p, r } = ctx;
  const de = (v, d = 1) => (+v).toLocaleString('de-DE', { minimumFractionDigits: d, maximumFractionDigits: d });
  const ps = p.options?.picture_solve || {};
  const solving = ['queued', 'running'].includes(ps.state);
  const secs = [['paObjects', 'Objekte im Bild', 'star'], ['paField', 'Bildfeld und Maßstab', 'globe'], ['paQual', 'Grobe Bildqualität', 'sparkle'], ['paOwn', 'Eigene Aufnahmen', 'compare']];
  t.innerHTML = `<div class="an pa">
    <div class="an-top"><div id="paViewer"></div>
      <nav class="an-toc" aria-label="Abschnitte">${secs.map(([id, l, ic], i) => `<a href="#${id}" data-sec="${id}" style="--i:${i}">${icon(ic)}<span>${l}</span></a>`).join('')}
        <p class="dim sm">Mit „Objekte im Bild einblenden“ erscheinen Kreise und Namen im Bild; aus, bleibt das Bild frei.</p></nav></div>
    <section class="panel an-sec pa-sec" id="paObjects"><div class="skel" style="height:6rem"></div></section>
    <section class="panel an-sec pa-sec" id="paField"></section>
    <section class="panel an-sec pa-sec" id="paQual"></section>
    <section class="panel an-sec pa-sec" id="paOwn"></section>
    <p class="mut sm pa-note">${icon('alert')}<span>Für fertige Bilder gibt es bewusst keine Grenzgröße, kein HR-Diagramm, keine Farbkalibrierung und keine Asteroiden – das wäre an gestreckten Daten falsch.</span></p>
  </div>`;
  const viewer = ctx.viewerFor($('#paViewer', t));
  viewer.el.classList.add('an-viewer', 'pa-viewer');      // Überlagerung nur über den Schalter (kein Überfahren, kein Touch-Sonderfall)
  const root = t.firstElementChild;                       // `t` (#tab) bleibt beim Reiterwechsel stehen, nur sein Inhalt wird ersetzt
  const alive = () => !!root && root.isConnected;
  const put = (id, html) => { const e = alive() ? root.querySelector('#' + id) : null; if (e) e.innerHTML = html; };       // nie in einen schon ersetzten Reiter schreiben
  const head = (h, sub = '') => `<div class="panel-h"><h2>${h}</h2>${sub ? `<span class="grow"></span><span class="mut sm">${sub}</span>` : ''}</div>`;
  const kpi = (label, v, sub = '') => `<div class="stat an-kpi"><small>${label}</small><b class="tnum">${v}</b><small class="dim">${sub}</small></div>`;
  $('.an-toc', t).addEventListener('click', (e) => {
    const a = e.target.closest('[data-sec]'); if (!a) return;
    e.preventDefault(); $('#' + a.dataset.sec, t)?.scrollIntoView({ behavior: anReduced() ? 'auto' : 'smooth', block: 'start' });
  });

  const needSolve = (what, btn = true) => `<p class="mut">${solving ? `Das Plate-Solving läuft noch – ${what} erscheint danach hier.`
    : `Ohne Plate-Solving gibt es ${what} nicht. ${esc(ps.state === 'failed' && ps.message ? ps.message : 'Das Sternmuster wurde noch nicht erkannt.')}`}</p>
    ${!btn ? '' : solving ? '<span class="badge-dot run">läuft</span>' : '<button class="btn sm" data-solve>Objekt bestimmen</button>'}`;

  api(`/stacking/results/${r.id}/picture-analysis`).then(d => {
    if (!alive()) return;
    const f = d.field, q = d.quality;
    // ---- Objekte ----
    const objs = (d.objects || []).filter(o => o.x != null);
    put('paObjects', head('Objekte im Bild', f ? `${objs.length} ${objs.length === 1 ? "Katalogobjekt" : "Katalogobjekte"}` : '') + (!d.solved ? needSolve('die Objektliste')
      : !objs.length ? '<p class="mut">Im Bildfeld liegt kein Objekt aus dem eingebauten Katalog.</p>'
      : `<div class="pa-bar"><label class="modrow"><input type="checkbox" id="paMarks"${store.get('pa.marks', false) ? ' checked' : ''}><span class="switch"></span><span>Objekte im Bild einblenden</span></label></div>
        <ul class="pa-objs">${objs.map((o, i) => `<li><button class="pa-obj" data-i="${i}" title="Im Bild zeigen"><b>${esc(o.name)}</b><span class="mut sm">${esc(o.kind_label || '')}${o.size ? ` · ${de(o.size, o.size < 10 ? 1 : 0)}′` : ''}</span></button></li>`).join('')}</ul>`));
    const marks = $('#paMarks', t);
    viewer.el.classList.toggle('marks-on', !!marks?.checked);                       // Anfangszustand = Schalter (auch nach Reiterwechsel/Neuladen)
    marks?.addEventListener('change', (e) => { viewer.el.classList.toggle('marks-on', e.target.checked); store.set('pa.marks', e.target.checked); });
    $('.pa-objs', t)?.addEventListener('click', (e) => {
      const b = e.target.closest('[data-i]'); if (!b) return;
      const o = objs[+b.dataset.i], img = $('img.b', viewer.el), sc = img?.naturalWidth && f?.width ? img.naturalWidth / f.width : 1;
      viewer.zoomTo(o.x * sc, o.y * sc, 2.5);
      $('#paViewer', t).scrollIntoView({ behavior: anReduced() ? 'auto' : 'smooth', block: 'nearest' });
    });
    // ---- Bildfeld ----
    put('paField', head('Bildfeld und Maßstab') + (!f ? needSolve('Bildfeld und Maßstab', false) : `<div class="depth-kpis pa-kpis">
        ${kpi('Bildfeld', `${de(f.fov_w, 2)}° × ${de(f.fov_h, 2)}°`, `${de(f.fov_w * 60, 0)}′ × ${de(f.fov_h * 60, 0)}′`)}
        ${kpi('Maßstab', `${de(f.scale, 2)}″/px`, `${f.width} × ${f.height} px`)}
        ${kpi('Norden', f.north === 0 ? 'oben' : `${de(Math.abs(f.north), 0)}° ${f.north > 0 ? 'rechts' : 'links'}`, f.north === 0 ? 'Bild nicht gedreht' : 'von oben gedreht')}</div>
      <p class="pa-hint"><b>Einordnung:</b> ${esc(f.device_hint)} <span class="mut">${esc(f.caveat)}</span></p>`));
    // ---- Qualität ----
    let qh = '';
    if (!q) qh = `<p class="mut">${esc(d.quality_error || 'Das Bild konnte nicht gelesen werden.')}</p>`;
    else {
      const s = q.stars, b = q.background;
      const noise = b.noise < 0.006 ? ['ruhig', 'ok'] : b.noise < 0.012 ? ['leicht sichtbar', 'warn'] : ['deutlich sichtbar', 'bad'];
      const cast = b.mono ? ['Schwarzweiß', 'ok'] : b.cast < 0.008 ? ['neutral', 'ok'] : [b.tone || 'leichter Stich', b.cast < 0.02 ? 'warn' : 'bad'];
      qh = `<div class="depth-kpis pa-kpis">
        ${s ? kpi('Sternschärfe', s.fwhm_arcsec != null ? `${de(s.fwhm_arcsec, 1)}″` : `${de(s.fwhm_px, 1)} px`, s.fwhm_arcsec != null ? `FWHM · ${de(s.fwhm_px, 1)} px` : 'FWHM (ohne Lösung in Pixeln)') : kpi('Sternschärfe', '–', 'zu wenige Sterne')}
        ${s ? kpi('Sternform', s.round ? 'rund' : 'länglich', `Exzentrizität ${de(s.ecc, 2)}`) : kpi('Sternform', '–', 'zu wenige Sterne')}
        ${kpi('Hintergrundrauschen', `<span class="lvl-${noise[1]}">${noise[0]}</span>`, `${de(b.noise * 100, 2)} % der Helligkeit`)}
        ${kpi('Farbstich', `<span class="lvl-${cast[1]}">${esc(cast[0])}</span>`, b.mono ? '' : `${de(b.cast * 100, 2)} % Abweichung`)}</div>
        ${b.notes?.length ? `<ul class="pa-notes">${b.notes.map(n => `<li>${esc(n)}</li>`).join('')}</ul>` : ''}
        ${s && s.pa && s.pa[1] > 0.5 && !s.round ? `<p class="mut sm">Die länglichen Sterne zeigen überwiegend in dieselbe Richtung (${de(s.pa[0], 0)}°) – typisch für Nachführfehler oder Wind.</p>` : ''}
        <p class="mut sm pa-caveat">${icon('alert')}<span>Am fertigen Bild gemessen, keine exakten Werte: Das Strecken bläht Sterne auf, Rauschminderung und Verkleinern verändern Schärfe und Rauschen. ${q.n_stars ? `Grundlage: ${q.n_stars} erkannte Sterne.` : ''}</span></p>`;
    }
    put('paQual', head('Grobe Bildqualität', 'Einordnung') + qh);
    // ---- Eigene Aufnahmen ----
    const own = d.own || [];
    put('paOwn', head('Eigene Aufnahmen desselben Objekts') + (!own.length
      ? `<p class="mut">${p.coords || d.solved ? 'Von diesem Objekt gibt es in AstroFlow noch keine eigene Aufnahme.' : 'Ohne bekanntes Objekt lassen sich keine eigenen Aufnahmen zuordnen.'}</p>`
      : `<div class="pa-cmp"><a class="pa-thumb pa-self" href="#/p/${p.id}/bild"><img src="/api/stacking/results/${r.id}/thumb" alt=""><span><b>Dieses Bild</b><small class="mut">Importiert</small></span></a>
        ${own.map(o => `<a class="pa-thumb" href="#/p/${o.project}/bild"><img src="/api/stacking/results/${o.result}/thumb" alt="" loading="lazy"><span><b>${esc(o.name)}</b>
          <small class="mut">${o.imported ? 'Fertiges Bild' : `${o.n_frames} Aufnahmen${o.exposure && typeof dur === 'function' ? ' · ' + dur(o.exposure) : ''}`}</small></span></a>`).join('')}</div>`));
    t.querySelectorAll('[data-solve]').forEach(b => b.addEventListener('click', () => ctx.solve?.()));
    $$('.pa-sec', t).forEach(anReveal);
  }).catch(e => {
    if (alive()) put('paObjects', `<p class="warn">${esc(e.message || 'Analyse nicht verfügbar')}</p>`);
  });
  return { viewer, destroy() {} };
}
