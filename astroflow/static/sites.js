// Einstellungen → Standorte: gemeinsame Ortsliste aller Module (Server: core/sites.py, /api/sites).
// Orte (Name, Koordinaten, Höhe, Genauigkeit), Standard-Ort, Zeiträume als Zeitleiste, Nachtliste mit Ort je Nacht.
(() => {
'use strict';
const ACC = [[30, '± 30 m · GPS, Hausnummer'], [100, '± 100 m · Grundstück'], [500, '± 500 m · Ortsteil'], [2000, '± 2 km · Ort'], [10000, '± 10 km · Region']];
const COLORS = 6;
const fmtAcc = (m) => m >= 1000 ? `± ${String(Math.round(m / 100) / 10).replace('.', ',')} km` : `± ${Math.round(m)} m`;
const fmtDay = (d) => d ? `${d.slice(8, 10)}.${d.slice(5, 7)}.${d.slice(0, 4)}` : 'offen';
const deg = (v, pos, neg) => `${Math.abs(v).toFixed(4).replace('.', ',')}° ${v >= 0 ? pos : neg}`;
const coords = (s) => `${deg(s.lat, 'N', 'S')} · ${deg(s.lon, 'O', 'W')}${s.alt_m ? ` · ${Math.round(s.alt_m)} m` : ''}`;
const srcText = { override: 'Ausnahme', fits: 'aus FITS', period: 'Zeitraum', default: 'Standard' };
const fixedBy = { skytrace: 'SkyTrace' };     // automatisch gepflegte Einzelnächte (`src`); Umstellen hier macht sie zur eigenen
// Genauigkeit für SkyTrace einordnen: Satelliten brauchen ~200–500 m, Nachtplanung/Wetter nicht
const accHint = (m) => m <= 300 ? ['ok', 'genau genug für Satelliten'] : m <= 1000 ? ['warn', 'Satelliten: Toleranz wird erweitert'] : ['bad', 'zu ungenau für sichere Satelliten-Zuordnung'];

// Himmelsqualität des Ortes (Dienst site_sky des Bilder-Moduls; fehlt das Modul, bleibt die Zeile leer)
const skyLine = (q) => q && q.sqm != null ? `<div class="sm site-sky">Himmel <b>${String(q.sqm.toFixed(2)).replace('.', ',')} mag/″²</b>${q.bortle ? ` · ${esc(q.bortle.text)}` : ''}${q.seeing != null ? ` · Schärfe ${String(q.seeing.toFixed(1)).replace('.', ',')}″` : ''} <span class="dim">(${q.n_site_nights} ${q.n_site_nights === 1 ? 'Nacht' : 'Nächte'})</span> · <a href="#/himmel">Himmelsqualität</a></div>` : '';
async function sitesSettings(el) {
  let d = await api('/sites');
  let edit = null;                         // Ort in Bearbeitung (Kopie) oder null
  let assign = null;                       // Zeitraum für einen Ort anlegen: {id, from, to} oder null
  const color = (id) => `site-c${Math.max(0, d.list.findIndex(s => s.id === id)) % COLORS}`;
  const save = async (next, msg) => {
    try { d = await send('/sites', next); if (msg) toast(msg, 'ok'); draw(); return true; }
    catch (e) { toast(e.message, 'err'); return false; }
  };

  // Ort ohne Zeitraum, Einzelnacht und Standard-Rolle: gilt nirgends – Hinweis mit Zuweisung (Formular oder Knopf)
  function unusedNote(s) {
    if (!(d.unused || []).includes(s.id)) return '';
    if (assign?.id === s.id) return `<form class="site-assign" id="siteAssign" novalidate>
      <p class="sm">Ab wann gilt <b>${esc(s.name)}</b>? Ohne Zeitraum wird der Ort nicht verwendet.</p>
      <div class="site-assign-f"><label class="field"><span>von</span><input type="date" name="from" value="${esc(assign.from)}" required></label>
        <label class="field"><span>bis (leer = offen)</span><input type="date" name="to" value="${esc(assign.to)}"></label></div>
      <p class="sm dim">Gleiches Datum bei von und bis = nur diese Nacht. Mit Zeitraum gilt der Ort in diesen Nächten statt des Standard-Orts.</p>
      <div class="row"><button class="btn sm" type="submit">Zeitraum speichern</button><button class="btn ghost sm" type="button" id="assignCancel">Später</button></div></form>`;
    return `<div class="site-unused sm" role="note"><span>${icon('alert')}Wird noch nicht verwendet – Zeitraum oder Nacht zuweisen.</span>
      <button class="btn sm" data-act="assign">Zeitraum zuweisen</button></div>`;
  }

  function siteRows() {
    if (!d.list.length) return `<div class="empty sm"><p class="mut">Noch kein Standort. Lege deinen üblichen Beobachtungsort an – Heute Nacht, Bild-Tutor und SkyTrace nutzen ihn gemeinsam.</p></div>`;
    return `<ul class="site-list">${d.list.map(s => {
      const [lvl, txt] = accHint(s.accuracy_m);
      return `<li class="site-row" data-id="${esc(s.id)}"><span class="site-dot ${color(s.id)}" aria-hidden="true"></span>
        <div class="site-main"><b>${esc(s.name)}</b>${s.id === d.default ? ' <span class="chip acc">Standard</span>' : ''}
          <div class="mut sm site-coords">${coords(s)}</div>
          <div class="sm"><span class="${lvl}">${fmtAcc(s.accuracy_m)}</span> <span class="dim">· ${txt}</span>${s.note ? ` <span class="dim">· ${esc(s.note)}</span>` : ''}</div>${skyLine(d.sky?.[s.id])}${unusedNote(s)}</div>
        <div class="site-acts">${s.id !== d.default ? `<button class="btn ghost sm" data-act="default">Als Standard</button>` : ''}
          <button class="btn ghost sm icon" data-act="edit" title="Bearbeiten" aria-label="${esc(s.name)} bearbeiten">${icon('edit')}</button>
          <button class="btn ghost sm icon" data-act="del" title="Löschen" aria-label="${esc(s.name)} löschen">${icon('trash')}</button></div></li>`;
    }).join('')}</ul>`;
  }

  function editForm() {
    const s = edit;
    return `<form class="site-edit" id="siteEdit" novalidate>
      <h3>${s.id ? 'Standort bearbeiten' : 'Neuer Standort'}</h3>
      <label class="field"><span>Adresse oder Ort suchen</span><div class="row" style="flex-wrap:nowrap"><input id="siteQ" placeholder="z. B. Sternwarte, Straße, Ort" style="flex:1;min-width:0"><button type="button" class="btn ghost" id="siteGo">Suchen</button></div>
        <div class="addr" id="siteRes"></div><small>Adresssuche: Photon/komoot · © OpenStreetMap (nur der Suchtext wird gesendet). <button type="button" class="linkbtn" id="siteLocate">Gerätestandort abfragen</button></small></label>
      <label class="field"><span>Name</span><input name="name" value="${esc(s.name || '')}" placeholder="z. B. Garten, Ferienhaus, Sternwarte" maxlength="80" required></label>
      <div class="site-grid">
        <label class="field"><span>Breite (°, Nord +)</span><input name="lat" inputmode="decimal" value="${s.lat ?? ''}" placeholder="49,1234"></label>
        <label class="field"><span>Länge (°, Ost +)</span><input name="lon" inputmode="decimal" value="${s.lon ?? ''}" placeholder="8,1234"></label>
        <label class="field"><span>Höhe (m)</span><div class="row" style="flex-wrap:nowrap"><input name="alt_m" inputmode="decimal" value="${s.alt_m ?? 0}" style="flex:1;min-width:0"><button type="button" class="btn ghost sm" id="siteAlt" title="Geländehöhe abfragen (Open-Meteo)">${icon('target')}</button></div></label>
        <label class="field"><span>Genauigkeit</span><select name="accuracy_m">${ACC.map(([v, l]) => `<option value="${v}">${l}</option>`).join('')}${ACC.some(a => a[0] === s.accuracy_m) ? '' : `<option value="${s.accuracy_m}">${fmtAcc(s.accuracy_m)}</option>`}</select></label>
      </div>
      <p class="sm mut site-why">Für Satelliten zählt der Ort auf ~200–500 m: 1 km Fehler verschiebt eine Bahn in 500 km Höhe um ≈ 7′. Für Nachtplanung und Wetter reichen 10 km.
        ${s.lat != null && s.lat !== '' ? `<a href="https://www.openstreetmap.org/?mlat=${encodeURIComponent(s.lat)}&mlon=${encodeURIComponent(s.lon)}#map=16/${encodeURIComponent(s.lat)}/${encodeURIComponent(s.lon)}" target="_blank" rel="noreferrer">Auf der Karte prüfen ↗</a>` : ''}</p>
      <label class="field"><span>Notiz</span><input name="note" value="${esc(s.note || '')}" maxlength="300" placeholder="optional, z. B. Horizont nach Süden frei"></label>
      <div class="row"><button class="btn" type="submit">Speichern</button><button class="btn ghost" type="button" id="siteCancel">Abbrechen</button><span class="mut sm" id="siteNote"></span></div></form>`;
  }

  function timeline() {
    const ps = d.periods.filter(p => p.from !== p.to);
    const singles = d.periods.filter(p => p.from === p.to);
    const days = (a) => Date.parse(a + 'T00:00:00Z') / 864e5;
    const all = [...ps.flatMap(p => [p.from, p.to || d.today]), ...singles.map(p => p.from), d.today].filter(Boolean).sort();
    let bar = '';
    if (ps.length || singles.length) {
      const a = days(all[0]) - 3, b = days(all[all.length - 1]) + 3, w = (x) => ((x - a) / (b - a) * 100).toFixed(2);
      const lanes = d.list.filter(s => ps.some(p => p.site_id === s.id) || singles.some(p => p.site_id === s.id));
      const years = [];
      for (let y = +all[0].slice(0, 4) + 1; y <= +all[all.length - 1].slice(0, 4); y++) {
        const x = +w(days(y + '-01-01'));
        if (x > 16 && Math.abs(x - +w(days(d.today))) > 6) years.push(y);     // keine Beschriftungen übereinander
      }
      bar = `<div class="site-tl" role="img" aria-label="Zeitleiste der Standorte">
        ${lanes.map(s => `<div class="site-lane"><span class="site-lane-n sm">${esc(s.name)}</span><div class="site-lane-t">
          ${ps.filter(p => p.site_id === s.id).map(p => `<span class="site-bar ${color(s.id)}" style="left:${w(days(p.from))}%;width:${Math.max(0.6, w(days(p.to || d.today) + 1) - w(days(p.from)))}%" title="${esc(s.name)}: ${fmtDay(p.from)} – ${fmtDay(p.to)}"></span>`).join('')}
          ${singles.filter(p => p.site_id === s.id).map(p => `<span class="site-tick ${color(s.id)}" style="left:${w(days(p.from))}%" title="${esc(s.name)}: Nacht ${fmtDay(p.from)}"></span>`).join('')}</div></div>`).join('')}
        <div class="site-axis sm dim"><span style="left:0">${fmtDay(all[0])}</span>${years.map(y => `<span style="left:${w(days(y + '-01-01'))}%">${y}</span>`).join('')}<span class="site-now${+w(days(d.today)) > 88 ? ' end' : ''}" style="left:${w(days(d.today))}%">heute</span></div></div>`;
    }
    return `${bar}
      ${ps.length ? `<table class="site-periods"><thead><tr><th>Ort</th><th>von</th><th>bis</th><th></th></tr></thead><tbody>${ps.map(p => `<tr data-pid="${esc(p.id)}">
        <td><select data-f="site_id" aria-label="Ort">${d.list.map(s => `<option value="${esc(s.id)}" ${s.id === p.site_id ? 'selected' : ''}>${esc(s.name)}</option>`).join('')}</select>
          ${(d.shadowed || []).includes(p.id) ? `<div class="site-unused sm" role="note">${icon('alert')}Gilt in keiner Nacht – kürzere Zeiträume überdecken ihn ganz.</div>` : ''}</td>
        <td><input type="date" data-f="from" value="${esc(p.from)}" aria-label="von"></td><td><input type="date" data-f="to" value="${esc(p.to || '')}" aria-label="bis (leer = offen)"></td>
        <td><button class="btn ghost sm icon" data-act="pdel" title="Zeitraum löschen" aria-label="Zeitraum löschen">${icon('trash')}</button></td></tr>`).join('')}</tbody></table>`
        : `<p class="mut sm">Keine Zeiträume – es gilt überall der Standard-Ort${singles.length ? ' (außer den einzeln zugeordneten Nächten)' : ''}.</p>`}
      ${d.list.length ? `<button class="btn ghost sm" id="periodAdd">${icon('plus')}Zeitraum hinzufügen</button>` : ''}`;
  }

  function nightRows() {
    const ns = d.nights || [];
    if (!ns.length) return '<p class="mut sm">Noch keine Aufnahmenächte in Bildern oder SkyTrace.</p>';
    return `<div class="site-nights-filter row"><input id="nightQ" type="search" placeholder="Nacht oder Objekt suchen" aria-label="Nächte filtern" style="flex:1;min-width:0"></div>
      <ul class="site-nights">${ns.map(n => `<li data-night="${esc(n.night)}" data-text="${esc((n.night + ' ' + n.labels.join(' ')).toLowerCase())}">
      <div class="site-night-d"><b>${fmtDay(n.night)}</b><div class="mut sm site-night-l">${n.fixed_by ? `<b>Ort aus ${esc(fixedBy[n.fixed_by] || n.fixed_by)}</b> · ` : ''}${esc(n.labels.slice(0, 3).join(' · '))}${n.labels.length > 3 ? ` · +${n.labels.length - 3}` : ''}</div></div>
      <div class="site-night-s"><select data-night-site aria-label="Ort der Nacht ${fmtDay(n.night)}">
        <option value="">${n.site ? `Auto: ${esc(n.site.name)} (${srcText[n.site.source] || n.site.source})` : 'Automatisch: kein Ort'}</option>
        ${d.list.map(s => `<option value="${esc(s.id)}" ${n.fixed === s.id ? 'selected' : ''}>${esc(s.name)}</option>`).join('')}</select>
        ${n.site ? `<span class="site-dot ${color(n.site.id)}" aria-hidden="true"></span>` : ''}</div></li>`).join('')}</ul>`;
  }

  function draw() {
    const today = d.today_site;
    el.innerHTML = `<div class="panel"><div class="panel-h"><h2>${icon('pin')}Standorte</h2><span class="grow"></span>
        ${edit ? '' : `<button class="btn sm" id="siteAdd">${icon('plus')}Standort</button>`}</div>
      <p class="mut sm" style="margin:-4px 0 12px">Gilt für Heute Nacht, Bild-Tutor und SkyTrace. Für jede Aufnahmenacht wird der passende Ort gewählt:
        Ausnahme der Beobachtung (SkyTrace) → Standort in den FITS-Daten → Nacht/Zeitraum → Standard-Ort. Standorte bleiben auf diesem Rechner.</p>
      ${today ? `<p class="sm site-today">Heute Nacht: <b>${esc(today.name)}</b> <span class="dim">(${esc(today.detail)})</span></p>` : ''}
      ${edit ? editForm() : siteRows()}</div>
      <div class="panel"><h2>Zeiträume</h2><p class="mut sm" style="margin:4px 0 12px">„Von–bis war das Teleskop hier“, z. B. Urlaub oder Umzug. Überlappen sich Zeiträume, gilt der kürzere.</p>${timeline()}</div>
      <div class="panel"><h2>Aufnahmenächte</h2><p class="mut sm" style="margin:4px 0 12px">Ort je Nacht prüfen und bei Bedarf umstellen. Nach einer Änderung SkyTrace-Analysen unten neu abgleichen und im Bild-Tutor „Neu prüfen“ wählen.</p>${nightRows()}</div>
      <div id="siteModules"></div>`;
    bind();
    // Abschnitte der Module (AF.register(id, { sitesPanel(host) }), z. B. SkyTrace „neu abgleichen“)
    for (const id of AF.order) { const f = AF.modules[id].sitesPanel; if (f) { const host = document.createElement('div'); $('#siteModules', el).append(host); f(host); } }
  }

  function bind() {
    $('#siteAdd', el)?.addEventListener('click', () => { edit = { name: '', lat: '', lon: '', alt_m: 0, accuracy_m: 100, note: '' }; draw(); $('#siteQ', el)?.focus(); });
    $$('.site-row', el).forEach(row => row.addEventListener('click', async (e) => {
      const b = e.target.closest('[data-act]'); if (!b) return;
      const s = d.list.find(x => x.id === row.dataset.id);
      if (b.dataset.act === 'assign') { assign = { id: s.id, from: d.today, to: '' }; draw(); $('#siteAssign [name=from]', el)?.focus(); }
      if (b.dataset.act === 'edit') { edit = { ...s }; draw(); }
      if (b.dataset.act === 'default') save({ ...d, default: s.id }, `${s.name} ist jetzt der Standard-Ort.`);
      if (b.dataset.act === 'del') {
        const used = d.periods.filter(p => p.site_id === s.id).length;
        if (!await confirmDlg('Standort löschen?', `„${esc(s.name)}“ wird entfernt${used ? ` – mit ${used} Zeitraum/Nacht-Zuordnung(en)` : ''}. Bereits analysierte Läufe behalten den damals genutzten Ort.`, 'Löschen', true)) return;
        save({ list: d.list.filter(x => x.id !== s.id), default: d.default === s.id ? null : d.default, periods: d.periods.filter(p => p.site_id !== s.id) }, 'Standort gelöscht.');
      }
    }));
    const fa = $('#siteAssign', el);
    if (fa) {
      $('#assignCancel', el).onclick = () => { assign = null; draw(); };
      fa.onsubmit = async (e) => {
        e.preventDefault();
        const from = fa.elements.from.value, to = fa.elements.to.value;
        if (!from) { toast('Bitte einen Anfang angeben.', 'err'); return; }
        if (to && to < from) { toast('Das Ende liegt vor dem Anfang.', 'err'); return; }
        const id = assign.id;
        if (await save({ ...d, periods: [...d.periods, { site_id: id, from, to }] }, 'Zeitraum gespeichert.')) { assign = null; draw(); }
      };
    }
    const f = $('#siteEdit', el);
    if (f) {
      const E = f.elements;
      E.accuracy_m.value = String(edit.accuracy_m);
      const fill = (r) => { if (r.name && !E.name.value) E.name.value = r.name.split(',')[0]; E.lat.value = (+r.lat).toFixed(6); E.lon.value = (+r.lon).toFixed(6);
        if (r.alt_m != null) E.alt_m.value = Math.round(r.alt_m);
        if (r.accuracy_m) { const best = ACC.reduce((a, b) => Math.abs(b[0] - r.accuracy_m) < Math.abs(a[0] - r.accuracy_m) ? b : a); E.accuracy_m.value = String(best[0]); } };
      const search = async () => {
        const q = $('#siteQ', el).value.trim();
        if (q.length < 2) return;
        try {
          const rows = await api('/sites/geocode?q=' + encodeURIComponent(q));
          $('#siteRes', el).innerHTML = rows.map((r, i) => `<button type="button" class="chip" data-i="${i}">${esc(r.name)}</button>`).join('') || '<span class="mut sm">Kein Ergebnis gefunden.</span>';
          $('#siteRes', el).onclick = (e) => { const b = e.target.closest('[data-i]'); if (b) { fill(rows[+b.dataset.i]); $('#siteRes', el).innerHTML = ''; $('#siteNote', el).textContent = 'Übernommen – Lage auf der Karte und Höhe prüfen.'; } };
        } catch (e) { toast(e.message, 'err'); }
      };
      $('#siteGo', el).onclick = search;
      $('#siteQ', el).onkeydown = (e) => { if (e.key === 'Enter') { e.preventDefault(); search(); } };
      $('#siteLocate', el).onclick = () => {
        if (!window.isSecureContext || !navigator.geolocation) { toast('Die Standortabfrage braucht HTTPS bzw. localhost – bitte die Adresssuche nutzen.', 'err'); return; }
        navigator.geolocation.getCurrentPosition(p => { fill({ lat: p.coords.latitude, lon: p.coords.longitude, alt_m: p.coords.altitude, accuracy_m: p.coords.accuracy });
          $('#siteNote', el).textContent = `Gerätestandort (± ${Math.round(p.coords.accuracy)} m) – nur übernehmen, wenn das Teleskop hier steht.`; },
        e => toast(e.message, 'err'), { enableHighAccuracy: true, timeout: 25000 });
      };
      $('#siteAlt', el).onclick = async () => {
        const lat = +String(E.lat.value).replace(',', '.'), lon = +String(E.lon.value).replace(',', '.');
        if (!E.lat.value || !E.lon.value || !isFinite(lat) || !isFinite(lon)) { toast('Erst Breite und Länge eintragen.', 'err'); return; }
        try { E.alt_m.value = Math.round((await api(`/sites/elevation?lat=${lat}&lon=${lon}`)).alt_m); $('#siteNote', el).textContent = 'Geländehöhe übernommen (Open-Meteo, ~90-m-Raster).'; }
        catch (e) { toast(e.message, 'err'); }
      };
      $('#siteCancel', el).onclick = () => { edit = null; draw(); };
      f.onsubmit = async (e) => {
        e.preventDefault();
        const num = (v) => String(v).trim().replace(',', '.');
        if (!E.name.value.trim() || num(E.lat.value) === '' || num(E.lon.value) === '') { toast('Name, Breite und Länge angeben.', 'err'); return; }
        const s = { ...edit, name: E.name.value.trim(), lat: +num(E.lat.value), lon: +num(E.lon.value), alt_m: +(num(E.alt_m.value) || 0), accuracy_m: +E.accuracy_m.value, note: E.note.value.trim() };
        if (![s.lat, s.lon, s.alt_m].every(isFinite)) { toast('Koordinaten als Zahl in Grad angeben, z. B. 49,1234.', 'err'); return; }
        const list = s.id ? d.list.map(x => x.id === s.id ? s : x) : [...d.list, s];
        edit = null;
        const before = new Set(d.list.map(x => x.id));
        await save({ ...d, list }, 'Standort gespeichert.');
        // Neuer Ort, der noch nirgends gilt: gleich nach dem Zeitraum fragen
        const made = d.list.find(x => !before.has(x.id));
        if (made && (d.unused || []).includes(made.id)) { assign = { id: made.id, from: d.today, to: '' }; draw(); $('#siteAssign [name=from]', el)?.focus(); }
      };
    }
    $('#periodAdd', el)?.addEventListener('click', () => save({ ...d, periods: [...d.periods, { site_id: d.default || d.list[0].id, from: d.today, to: '' }] }));
    $$('.site-periods tr[data-pid]', el).forEach(tr => {
      tr.addEventListener('change', (e) => {
        const k = e.target.dataset.f; if (!k) return;
        save({ ...d, periods: d.periods.map(p => p.id === tr.dataset.pid ? { ...p, [k]: e.target.value } : p) }, 'Zeitraum gespeichert.');
      });
      $('[data-act=pdel]', tr).onclick = () => save({ ...d, periods: d.periods.filter(p => p.id !== tr.dataset.pid) }, 'Zeitraum gelöscht.');
    });
    $$('[data-night-site]', el).forEach(sel => sel.addEventListener('change', async () => {
      const night = sel.closest('li').dataset.night;
      try { d = await send('/sites/night', { night, site_id: sel.value || null }); toast(`Nacht ${fmtDay(night)}: ${sel.value ? sel.selectedOptions[0].textContent : 'automatisch'}`, 'ok'); draw(); }
      catch (e) { toast(e.message, 'err'); }
    }));
    $('#nightQ', el)?.addEventListener('input', (e) => {
      const q = e.target.value.trim().toLowerCase();
      $$('.site-nights li', el).forEach(li => { li.hidden = !!q && !li.dataset.text.includes(q); });
    });
  }
  draw();
}
window.sitesSettings = sitesSettings;
})();
