'use strict';
/* Modul „SkyTrace“: Bildfolgen auf Lichtspuren und bewegte Objekte untersuchen. Portiert aus SkyTrace web/app.js;
   Messgrafiken, Overlays und Zoomdialoge kommen unverändert aus evidence.js (AstroEvidence). */
(() => {
const T = '/skytrace';
const EV = () => (typeof AstroEvidence !== 'undefined' ? AstroEvidence : null);
// Stellina-Ordner „<Datum>_observation_<Objekt>“; ältere automatische Läufe „<Objekt>_observation_<Nacht>-p<id>-<zeit>“
const shortName = (n) => { const a = /^(.+)_observation_\d{4}-\d\d-\d\d-p\d+-\d+$/.exec(n || ''); return (a ? a[1] : (n || 'Beobachtung').split('_observation_').pop()).replaceAll('_', ' '); };
const runDate = (id) => { const m = /^(\d{4})-(\d\d)-(\d\d)_(\d\d)-(\d\d)/.exec(id || ''); return m ? `${m[3]}.${m[2]}.${m[1]} ${m[4]}:${m[5]}` : ''; };
const media = (rel) => `/api/skytrace/media/${rel.split('/').map(encodeURIComponent).join('/')}`;
const kindNames = { trail: 'Einzelbild-Spur', meteor: 'Meteorkandidat', plane: 'Flugzeugkandidat', moving: 'Langsamer Beweger' };
const candidateName = (c) => EV() ? EV().candidateName(c) : kindNames[c.kind] || c.label;
const observedDate = (c) => c.timestamp ? new Date(c.timestamp * 1000).toLocaleString('de-DE', { timeZone: 'UTC', day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit' }) + ' UTC' : c.timestamp_text || 'Zeit unbekannt';
const providerNames = { meteors: 'Meteorflugbahnen · GMN', asteroids: 'Bekannte Asteroiden · JPL', satellites: 'Satelliten · historische Bahnen', aircraft: 'Flugzeuge · historische ADS-B-Daten', fireballs: 'Feuerkugeln · CNEOS' };
const statusNames = { service_unavailable: 'Dienst vorübergehend nicht erreichbar', needs_login: 'Anmeldung erforderlich', needs_access: 'Archivzugang freischalten', compatible: 'Geometrischer Hinweis', checked_no_match: 'Kein passender Eintrag', site_uncertain: 'Standort ungenau – Zuordnung unsicher', not_checkable: 'Nicht prüfbar', needs_archive: 'Archivdaten fehlen', temporal_hint: 'Zeitlicher Hinweis', error: 'Abfrage fehlgeschlagen', disabled: 'Ausgeschaltet', cancelled: 'Angehalten' };
const LOGTAG = { sequence: 'FRAME', rejected: 'FILTER', status: 'ENGINE', done: 'FERTIG', error: 'HINWEIS', fatal: 'FEHLER', artifact: 'EXPORT' };
const TABS = [['vorschau', 'Vorschau'], ['kandidaten', 'Kandidaten'], ['beobachtungen', 'Beobachtungen'], ['protokoll', 'Protokoll'], ['3d', '3D']];
const el = (tag, text, cls) => { const n = document.createElement(tag); if (text !== undefined) n.textContent = text; if (cls) n.className = cls; return n; };
const svgIcon = (name) => { const t = document.createElement('template'); t.innerHTML = icon(name); return t.content.firstChild; };

// JPL-Wartezeit verständlich anzeigen (die Engine wartet bis 45 s je Abfrage und ist dabei anhaltbar)
function engineStatus(e, now = Date.now()) {
  const text = e.status || 'Bereit';
  if (!e.running || e.stopping || !/^Katalogprüfung: (asteroids|JPL-Asteroiden)/.test(text)) return text;
  const logs = e.logs || [], last = logs[logs.length - 1];
  const m = [...logs].reverse().find(l => /K\d+ extrahiert/.test(l.text || ''))?.text.match(/K(\d+) extrahiert/);
  const elapsed = last?.time ? Math.max(0, Math.floor(now / 1000 - last.time)) : 0;
  return 'JPL-Asteroiden' + (m ? ' · Kandidat ' + Number(m[1]) + (e.stats?.candidates ? '/' + e.stats.candidates : '') : '') + ' · warte auf Antwort seit ' + elapsed + ' s · Anhalten möglich';
}
function engineNotice(eng) {
  if (!eng?.checked || eng.ok) return '';
  return `<div class="notice">${icon('alert')}<div><b>Analyse-Engine unvollständig</b><div class="mut sm">Der Python-Interpreter <code>${esc(eng.python || '')}</code>
    ${eng.error ? `antwortet nicht: ${esc(eng.error)}` : `hat nicht alle Pakete: ${esc((eng.missing || []).join(', '))}`}. Unter Einstellungen → SkyTrace einen Interpreter mit den
    SkyTrace-Paketen wählen (z. B. die <code>.venv</code> einer bestehenden SkyTrace-Installation) oder <code>pip install -r astroflow/modules/skytrace/requirements.txt</code>.</div></div>
    <span class="grow"></span><a class="btn sm ghost" href="#/settings/skytrace">Einrichten</a></div>`;
}

// ---------------------------------------------------------------- Übersicht --- //
// Analyse löschen: Ergebnisordner mit Videos, Ausschnitten und Bericht – die Originalaufnahmen bleiben unberührt.
async function deleteRun(id, title) {
  if (!await confirmDlg(`„${title}“ löschen?`, `Entfernt diese Analyse mit Videos, Kandidaten-Ausschnitten und Bericht. Das lässt sich nicht rückgängig machen.<br><br>
    <span class="sm">Deine Originalaufnahmen bleiben unberührt.</span>`, 'Endgültig löschen', true)) return false;
  try {
    const r = await send(`${T}/run?id=${encodeURIComponent(id)}`, null, 'DELETE');
    toast(`„${title}“ gelöscht${r.bytes ? ` – ${fmb(r.bytes)} frei` : ''}.`, 'ok');
    return true;
  } catch (e) { toast(e.message, 'err'); return false; }
}

// Gleichnamige Analysen (z. B. „Erneut analysieren“) unterscheidbar machen: Variante n von m, die neueste ist „aktuell“
function runVariants(all) {
  const groups = new Map();
  for (const r of all) { const k = shortName(r.title); if (!groups.has(k)) groups.set(k, []); groups.get(k).push(r); }
  for (const g of groups.values()) g.sort((a, b) => a.id < b.id ? -1 : 1);
  return (r) => { const g = groups.get(shortName(r.title)); if (!g || g.length < 2) return '';
    const i = g.indexOf(r) + 1; return ` <span class="chip ${i === g.length ? 'acc' : ''}" title="${g.length} Analysen derselben Beobachtung">${i === g.length ? 'aktuell · ' : ''}Variante ${i} von ${g.length}</span>`; };
}
async function runsView(el) {
  crumbs([['SkyTrace']]);
  let boot = await api(T + '/bootstrap'), sig = '', liveSig = '', mode = store.get('skytrace.view2', 'cards'), viz = null;
  el.innerHTML = `<div class="pagehead"><div><h1>SkyTrace</h1>
      <p class="mut">Bildfolgen sternregistrieren, Lichtspuren erkennen und mit Meteor-, Satelliten-, Asteroiden- und Flugkatalogen abgleichen.</p></div>
      <div class="page-actions"><a class="btn ghost" href="#/settings/skytrace">${icon('pin')}Standort &amp; Kataloge</a><a class="btn" href="#/skytrace/neu">${icon('play')}Neue Analyse</a></div></div>
    <div id="stEngine">${engineNotice(boot.engine)}</div><div id="stLive"></div><div id="stViz" style="margin:-8px 0 20px" hidden></div>
    ${window.SkyTrace3D && boot.runs?.length ? `<a class="st3d-entry" href="#/skytrace/3d"><span class="st3d-entry-ic">${icon('globe')}</span><span><b>3D-Ansicht</b>
      <small>Wohin das Teleskop über die Nacht geschaut hat und welche Satellitenbahnen das Bildfeld gekreuzt haben – auf der Erde zur Aufnahmezeit.</small></span>${icon('arrow')}</a>` : ''}
    <div class="toolbar"><input type="search" id="stq" placeholder="Analyse suchen …" title="Taste /"><span class="mut sm" id="stsum"></span><span class="grow"></span>
      <div class="seg" id="stview" aria-label="Ansicht"><button data-v="table" title="Tabelle">${icon('list')}</button><button data-v="cards" title="Karten">${icon('grid')}</button></div></div>
    <div id="stRuns"></div>`;
  let q = '';
  $('#stq').oninput = (e) => { q = e.target.value.toLowerCase(); sig = ''; draw(); };
  const setView = () => $$('#stview button').forEach(b => b.classList.toggle('on', b.dataset.v === mode));
  setView();
  $('#stview').onclick = (e) => { const b = e.target.closest('button'); if (b) { mode = b.dataset.v; store.set('skytrace.view2', mode); setView(); sig = ''; draw(); } };
  $('#stRuns').addEventListener('click', async (e) => {
    const b = e.target.closest('[data-del]'), tr = e.target.closest('tr[data-href]');
    if (!b && tr && !e.target.closest('a')) { location.hash = tr.dataset.href; return; }
    if (!b) return;
    e.preventDefault(); e.stopPropagation();
    const r = boot.runs.find(x => x.id === b.dataset.del);
    if (r && await deleteRun(r.id, shortName(r.title))) { boot.runs = boot.runs.filter(x => x.id !== r.id); sig = ''; draw(); }
  });
  function draw() {
    const runs = boot.runs.filter(r => (shortName(r.title) + ' ' + r.id).toLowerCase().includes(q));
    const variant = runVariants(boot.runs);
    const s = JSON.stringify([mode, runs.map(r => [r.id, r.frames, r.candidates, r.preview])]);
    $('#stsum').textContent = boot.runs.length ? `${boot.runs.length} Analyse${boot.runs.length === 1 ? '' : 'n'} · ${nf.format(boot.runs.reduce((a, r) => a + r.candidates, 0))} Kandidaten` : '';
    if (s === sig) return;
    sig = s;
    const live = (r) => boot.state.running && boot.state.run === r.id;
    if (runs.length && mode === 'table') {
      $('#stRuns').innerHTML = `<div class="tablewrap free"><table><thead><tr><th class="nosort">Analyse</th><th class="nosort">Datum</th><th class="nosort num">Beobachtungen</th><th class="nosort num">Aufnahmen</th><th class="nosort num">Unscharf</th><th class="nosort num">Kandidaten</th><th class="nosort"></th></tr></thead><tbody>
        ${runs.map(r => `<tr class="link" data-href="#/skytrace/${encodeURIComponent(r.id)}"><td class="name">${r.preview ? `<img class="tthumb" src="${media(r.preview)}" alt="" loading="lazy">` : '<span class="tthumb" style="display:inline-block"></span>'}<a href="#/skytrace/${encodeURIComponent(r.id)}">${esc(shortName(r.title))}</a>${variant(r)}${live(r) ? ' <span class="badge-dot run">läuft</span>' : ''}</td>
          <td>${esc(runDate(r.id) || ago(r.modified))}</td><td class="num">${r.sessions}</td><td class="num">${nf.format(r.frames)}</td><td class="num">${r.excluded ? nf.format(r.excluded) : '–'}</td>
          <td class="num">${r.candidates ? `<b class="acc">${r.candidates}</b>` : '0'}</td>
          <td>${live(r) ? '' : `<button type="button" class="linkbtn" data-del="${esc(r.id)}" title="Analyse löschen" aria-label="Analyse löschen">${icon('trash')}</button>`}</td></tr>`).join('')}</tbody></table></div>`;
      return;
    }
    $('#stRuns').innerHTML = runs.length ? `<div class="gallery stagger">${runs.map((r, i) => `<a class="card" href="#/skytrace/${encodeURIComponent(r.id)}" style="--i:${i}">
        <div class="thumb">${r.preview ? `<img src="${media(r.preview)}" alt="" loading="lazy" decoding="async" onload="this.classList.add('ld')">` : `<div class="ph">${icon('orbit')}Foto-/Videoanalyse</div>`}
          ${r.candidates ? `<span class="chip acc badge">${r.candidates} Kandidat${r.candidates === 1 ? '' : 'en'}</span>` : ''}</div>
        <div class="body"><h3>${esc(shortName(r.title))}</h3><div class="meta">${variant(r)}<span class="chip">${r.sessions} Beobachtung${r.sessions === 1 ? '' : 'en'}</span>${r.excluded ? `<span class="chip">${r.excluded} unscharf</span>` : ''}</div>
          <div class="foot"><span>${nf.format(r.frames)} Aufnahmen</span><span>${esc(runDate(r.id) || ago(r.modified))}</span></div></div>
        ${boot.state.running && boot.state.run === r.id ? '' : `<button type="button" class="card-del" data-del="${esc(r.id)}" title="Analyse löschen" aria-label="Analyse löschen">${icon('trash')}</button>`}</a>`).join('')}</div>`
      : boot.runs.length ? `<div class="empty">${icon('search')}<h2>Keine Treffer</h2></div>`
        : `<div class="empty">${icon('orbit')}<h2>Jede Spur hat eine Geschichte.</h2>Wähle einen Beobachtungsordner und starte deine erste Analyse.
          <div style="margin-top:18px"><a class="btn" href="#/skytrace/neu">${icon('play')}Neue Analyse</a></div></div>`;
  }
  function live(s) {
    const html = s.running ? `<a class="panel stlive" href="#/skytrace/${encodeURIComponent(s.run)}"><div class="panel-h"><span class="live"></span><h2>${s.operation === 'refresh' ? 'Kandidatenbericht wird aktualisiert' : 'Analyse läuft'}</h2>
        <span class="grow"></span><span class="mut sm tnum">${s.stats.current || 0} / ${s.stats.total || '—'} Aufnahmen · ${s.stats.candidates} Erkennungen</span>${icon('arrow')}</div>
        <div class="progress ${s.stats.total ? '' : 'indet'}"><i style="width:${s.stats.total ? 100 * (s.stats.current || 0) / s.stats.total : 0}%"></i></div>
        <div class="mut sm" style="margin-top:8px">${esc(engineStatus(s))}</div></a>` : '';
    if (html !== liveSig) { liveSig = html; $('#stLive').innerHTML = html; }
    // Schema bleibt über Fortschrittsmeldungen hinweg bestehen (Pause und Zeitachse bleiben erhalten)
    const want = s.running && s.operation !== 'refresh' && typeof vizProcess === 'function';
    if (want && !viz) { $('#stViz').hidden = false; viz = vizProcess($('#stViz'), { kind: 'trace' }); }
    else if (!want && viz) { viz.destroy(); viz = null; $('#stViz').hidden = true; $('#stViz').replaceChildren(); }
    viz?.update({ n: s.stats?.current || 0, total: s.stats?.total || 0, found: s.stats?.candidates });
  }
  draw();
  live(boot.state);
  const onKey = (e) => { if (e.key === '/' && !e.target.closest('input,select,textarea')) { e.preventDefault(); $('#stq').focus(); } };
  document.addEventListener('keydown', onKey);
  let wasRunning = boot.state.running;
  return {
    async update(st) {
      const s = st.modules.skytrace;
      if (!s) return;
      if (s.running || wasRunning) { const state = await api(T + '/state'); live(state); }
      if (wasRunning && !s.running) { boot.runs = await api(T + '/runs'); draw(); }
      wasRunning = s.running;
      if (!!s.engine?.ok !== !!boot.engine?.ok) { boot.engine = s.engine; $('#stEngine').innerHTML = engineNotice(s.engine); }
    },
    actions: () => [['play', 'Neue Analyse', 'Bildfolge untersuchen', () => { location.hash = '#/skytrace/neu'; }],
      ['pin', 'Standort & Kataloge', 'Einstellungen', () => { location.hash = '#/settings/skytrace'; }]],
    destroy: () => { viz?.destroy(); document.removeEventListener('keydown', onKey); },
  };
}

// --------------------------------------------------------- Neue Analyse --- //
async function newView(el) {
  crumbs([['SkyTrace', '#/skytrace'], ['Neue Analyse']]);
  const boot = await api(T + '/bootstrap');
  const p = { ...boot.preferences, ...store.get('skytrace.form', {}) };
  el.innerHTML = `<div class="pagehead"><div><h1>Neue Analyse</h1><p class="mut">Originale werden nur gelesen – Ergebnisse landen im SkyTrace-Datenordner.</p></div></div>
    ${engineNotice(boot.engine)}
    <div class="split"><form class="panel" id="stf">
      <label class="field"><span>Quellordner</span><div class="row" style="flex-wrap:nowrap"><input name="source" spellcheck="false" style="flex:1;min-width:0" value="${esc(p.source || '')}">
        <button type="button" class="btn ghost" id="browse">${icon('folder')}Durchsuchen</button></div><small>Ordner mit einer Beobachtung je Unterordner (z. B. Stellina-Export).</small></label>
      <label class="field"><span>Beobachtung</span><select name="observation"><option value="">Alle Beobachtungen</option></select></label>
      <label class="field"><span>Analysemodus</span><select name="mode"><option>Bildfolge → Ereignisvideo</option><option>Fotos</option><option>Videos</option></select></label>
      <div class="row" style="flex-wrap:nowrap;gap:14px"><label class="field" style="flex:1"><span>Bilder <span class="dim">0 = alle</span></span><input type="number" name="samples" min="0" max="100000" value="${esc(p.samples ?? 0)}"></label>
        <label class="field" style="flex:1"><span>Video-Bilder/s</span><input type="number" name="fps" min="1" max="60" step="0.5" value="${esc(p.fps ?? 8)}"></label></div>
      <label class="modrow" style="margin-bottom:16px">${icon('orbit')}<div><b>Plate Solving</b><div class="mut sm">Himmelsobjekte zuordnen – Voraussetzung für Katalogabgleiche</div></div>
        <input type="checkbox" name="solve" ${p.solve !== false ? 'checked' : ''}><span class="switch"></span></label>
      <div class="row"><button class="btn" type="submit" id="go">${icon('play')}Analyse starten</button><span class="mut sm">${icon('check')} Verwackelte Bilder werden automatisch ausgelassen</span></div>
    </form>
    <div class="panel"><h2 style="margin-bottom:10px">So funktioniert es</h2><ol class="mut sm steps">
      <li>Viele FITS-Aufnahmen einer Beobachtung werden chronologisch zu einem Zeitraffervideo.</li>
      <li>Die Teleskopbewegung wird über das ganze Sternfeld kompensiert; unscharfe Bilder fallen heraus.</li>
      <li>Einzelbildstreifen und wandernde Punkte werden als <b>Kandidaten</b> mit Vorher-/Nachher-Ausschnitten gespeichert.</li>
      <li>Kataloge (Satelliten, Meteore, Asteroiden, Flüge) werden zur echten UTC-Aufnahmezeit geprüft – nur Standort, Zeit und Himmelskoordinaten verlassen den Rechner.</li></ol>
      <p class="dim sm" style="margin:12px 0 0">Ein Kandidat ist kein bestätigtes Objekt; ein Katalogtreffer bleibt ein geometrischer Hinweis.</p></div></div>`;
  const f = $('#stf'), E = f.elements;
  E.mode.value = p.mode || 'Bildfolge → Ereignisvideo';
  const remember = () => store.set('skytrace.form', { source: E.source.value, observation: E.observation.value, mode: E.mode.value, samples: E.samples.value, fps: E.fps.value, solve: E.solve.checked });
  async function observations(pref) {
    const src = E.source.value.trim();
    try {
      const d = await api(T + '/observations?source=' + encodeURIComponent(src));
      if (src !== E.source.value.trim()) return;
      const sel = pref ?? E.observation.value;
      E.observation.innerHTML = '<option value="">Alle Beobachtungen</option>' + d.observations.map(n => {
        const s = d.history?.[n]?.status, mark = s === 'completed' ? '✓ Bereits analysiert · ' : s === 'partial' ? '◐ Teilweise · ' : '';
        return `<option value="${esc(n)}">${esc(mark + shortName(n) + ' · ' + n.split('_observation_')[0].replace('_', ' '))}</option>`;
      }).join('');
      if (sel && d.observations.includes(sel)) E.observation.value = sel;
    } catch (e) { E.observation.innerHTML = `<option value="">${esc(e.message)}</option>`; }
  }
  f.onchange = remember;
  E.source.onchange = async () => { await observations(); remember(); };
  $('#browse').onclick = () => folderDialog(E.source.value, async (path) => { E.source.value = path; await observations(); remember(); },
    { title: 'Beobachtungsordner wählen', hint: 'Originaldateien werden nur gelesen.' });
  f.onsubmit = async (e) => {
    e.preventDefault();
    $('#go').disabled = true;
    try {
      const s = await send(T + '/start', { source: E.source.value, observation: E.observation.value, mode: E.mode.value, samples: +E.samples.value, fps: +E.fps.value, solve: E.solve.checked });
      toast('Analyse gestartet – die Live-Vorschau aktualisiert sich automatisch.', 'ok');
      poll();
      location.hash = `#/skytrace/${encodeURIComponent(s.run)}`;
    } catch (err) { toast(err.message, 'err'); $('#go').disabled = false; }
  };
  await observations(p.observation);
}

// ------------------------------------------------------------- Laufdetail --- //
let focusNext = null;                                    // Kandidat, den die 3D-Ansicht öffnen will (nach dem Tabwechsel)
let focus3d = null;                                      // Kandidat, den der Reiter „3D“ in den Fokus nimmt
async function runView(root, runId, tab = 'vorschau') {
  runId = decodeURIComponent(runId);
  const S = { run: null, engine: await api(T + '/state'), session: 0, key: null, frame: null, overlay: store.get('skytrace.overlay', true),
    mode: 'image', timer: null, logId: 0, filter: 'all', ticket: 0, runStamp: 0, full: true, seek: null, detailCleanup: null };
  const isLive = () => S.engine.running && S.engine.run === runId;
  try { S.run = await api(T + '/run?id=' + encodeURIComponent(runId)); }
  catch (e) { if (!isLive()) throw e; S.run = { id: runId, sessions: [], candidates: [], report_url: null }; }
  crumbs([['SkyTrace', '#/skytrace'], [shortName(S.run.sessions.length === 1 ? S.run.sessions[0].session : runId)]]);
  if (S.run.candidates.length) S.key = S.run.candidates.some(c => c.key === focusNext) ? focusNext : S.run.candidates[0].key;
  focusNext = null;
  const session = () => S.run.sessions[S.session] || S.run.sessions[0];
  const cand = () => S.run.candidates.find(c => c.key === S.key);

  // Reiter „3D“ nur, wenn es Beobachtungen mit Aufnahmen gibt und gerade nicht analysiert wird
  const has3d = () => !!window.SkyTrace3D && !isLive() && S.run.sessions.some(s => s.video_url);
  const tabs = () => TABS.filter(([k]) => k !== '3d' || has3d());
  const runTitle = () => S.run.sessions.length === 1 ? shortName(S.run.sessions[0].session) : shortName(runId);
  function head() {
    const ss = S.run.sessions, frames = ss.reduce((a, s) => a + (s.frames || 0), 0), excl = ss.reduce((a, s) => a + (s.excluded || 0), 0);
    const live = isLive(), title = runTitle();
    return `<div class="pagehead"><div style="min-width:0"><h1>${esc(title)}</h1>
        <div class="row" style="margin-top:8px">${live ? '<span class="chip acc"><span class="live" style="width:7px;height:7px"></span>Live-Analyse</span>' : ''}
          <span class="chip">${esc(runDate(runId) || runId)}</span><span class="chip">${ss.length} Beobachtung${ss.length === 1 ? '' : 'en'}</span>
          <span class="chip">${nf.format(frames)} Aufnahmen</span>${excl ? `<span class="chip">${nf.format(excl)} unscharf ausgelassen</span>` : ''}
          <span class="chip ${S.run.candidates.length ? 'acc' : ''}">${S.run.candidates.length} Kandidaten</span></div></div>
      <span class="grow"></span>
      <div class="row">${live ? `<button class="btn danger" id="stop" ${S.engine.stopping ? 'disabled' : ''}>${icon('stop')}${S.engine.stopping ? 'Wird angehalten …' : 'Anhalten'}</button>`
        : `<div class="menu" id="analysisMenu"><button class="btn ghost" aria-haspopup="true" ${S.engine.running || !ss.some(s => s.video_url) ? 'disabled' : ''}>${icon('refresh')}Analyse${icon('chev')}</button><div class="pop">
            <a href="#" id="reanalyse"><span>Erneut analysieren<small>Erkennung mit aktuellem Code</small></span>${icon('play')}</a>
            <a href="#" id="refresh"><span>Zeitreihen aktualisieren<small>Berichte und Katalogabgleich</small></span>${icon('refresh')}</a>
            <div class="pop-note">Erneute Analyse erstellt einen neuen Lauf. Der bisherige bleibt erhalten.</div></div></div>`}
        ${S.run.report_url ? `<a class="btn ghost" href="${S.run.report_url}" target="_blank" rel="noopener">Bericht öffnen${icon('arrow')}</a>` : ''}
        ${live ? '' : `<button class="btn ghost icon" id="delRun" title="Analyse löschen" aria-label="Analyse löschen">${icon('trash')}</button>`}</div></div>
      <div id="stjob" style="margin:-8px 0 18px"></div>
      <nav class="tabs" id="tabs">${tabs().map(([k, l], i) => { const n = { kandidaten: S.run.candidates.length, beobachtungen: ss.length }[k];
        return `<a href="#/skytrace/${encodeURIComponent(runId)}/${k}" data-t="${k}" class="${tab === k ? 'on' : ''}" title="Taste ${i + 1}">${k === '3d' ? icon('globe') : ''}${l}${n != null ? `<span>${n}</span>` : ''}</a>`; }).join('')}<i class="tab-ind" id="tabind"></i></nav>
      <div id="tab"></div>`;
  }
  function jobState() {
    const e = S.engine, st = e.stats || {};
    setJobLine($('#stjob'), isLive() ? { state: 'running', stage: e.operation === 'refresh' ? 'Bericht' : `${st.current || 0} / ${st.total || '—'} Aufnahmen`,
      message: engineStatus(e), progress: st.total ? (st.current || 0) / st.total : null } : null);
  }
  function draw() {
    cleanupMedia();
    stopTimeline();
    root.innerHTML = head();
    $('#stop')?.addEventListener('click', async () => { try { S.engine = await send(T + '/stop'); draw(); } catch (e) { toast(e.message, 'err'); } });
    $('#delRun')?.addEventListener('click', async () => { if (await deleteRun(runId, runTitle())) location.hash = '#/skytrace'; });
    $('#analysisMenu .pop')?.addEventListener('click', () => $('#analysisMenu').classList.remove('open'));
    $('#reanalyse')?.addEventListener('click', async (e) => {
      e.preventDefault();
      try {
        const plan = await send(T + '/reanalyse', { run: runId, check: true });
        if (plan.missing) {
          folderDialog('', startAgain, { title: 'Originalaufnahmen für erneute Analyse wählen', ok: 'Erneut analysieren',
            hint: `${plan.missing} von ${plan.total} Originalaufnahmen fehlen am bisherigen Ort. Bitte den Ordner der passenden Nacht wählen. Aufnahmedatum und Dateinamen müssen zum bisherigen Lauf passen.` });
        } else await startAgain();
      } catch (e) { toast(e.message, 'err'); }
    });
    $('#refresh')?.addEventListener('click', async (e) => {
      e.preventDefault();
      try { S.engine = await send(T + '/refresh', { run: runId }); S.logId = 0; toast('Zeitreihen und Katalogabgleich werden aktualisiert.'); poll(); draw(); }
      catch (e) { toast(e.message, 'err'); }
    });
    jobState();
    drawTab(false);
  }
  async function startAgain(source) {
    try {
      const result = await send(T + '/reanalyse', { run: runId, ...(source ? { source } : {}) });
      toast('Neue Analyse mit aktuellem Code gestartet.', 'ok');
      location.hash = `#/skytrace/${encodeURIComponent(result.run)}/vorschau`;
    } catch (e) { toast(e.message, 'err'); }
  }
  function focusCandidate(key) {                       // aus der 3D-Ansicht: Kandidat zeigen
    if (tab === 'kandidaten' && $('#clist')) { S.key = key; S.frame = null; S.filter = 'all'; $('#kf') && ($('#kf').value = 'all'); drawList(); }
    else { focusNext = key; location.hash = `#/skytrace/${encodeURIComponent(runId)}/kandidaten`; }
  }
  function drawTab(animate = true) {
    cleanupMedia();
    stopTimeline();
    $$('#tabs a').forEach(a => a.classList.toggle('on', a.dataset.t === tab));
    requestAnimationFrame(() => slideInd($('#tabind'), $('#tabs a.on'), true));
    const t = $('#tab');
    t.classList.remove('enter');
    if (tab === '3d' && !has3d()) tab = 'vorschau';
    ({ vorschau: tabPreview, kandidaten: tabCandidates, beobachtungen: tabSessions, protokoll: tabLog, '3d': tab3d }[tab] || tabPreview)(t);
    if (animate) { void t.offsetWidth; t.classList.add('enter'); }
  }

  // ---- Vorschau: Livebild bzw. Ereignisvideo mit abschaltbaren Overlays ----
  function cleanupMedia() {
    const video = $('#skyVideo', root);
    video?._motionCleanup?.();
    video?.pause();
    S.detailCleanup?.(); S.detailCleanup = null;
    S.view3d?.dispose(); S.view3d = null;
  }
  function videoTarget(c, f) {
    const s = S.run.sessions.find(x => x.session === c.session);
    return s && (s.clean_url || s.video_url) && !f?.excluded && Number.isFinite(f?.video_seconds) ? s : null;
  }
  function jumpToVideo(c, f) {
    const s = videoTarget(c, f);
    if (!s) return;
    stopTimeline(); S.key = c.key; S.session = S.run.sessions.indexOf(s); S.mode = 'video';
    S.seek = { session: s.session, seconds: f.video_seconds };
    if (tab !== 'vorschau') { tab = 'vorschau'; location.hash = `#/skytrace/${encodeURIComponent(runId)}/vorschau`; drawTab(); }
    else renderPreview();
    requestAnimationFrame(() => $('#sky', root)?.scrollIntoView({ block: 'nearest', behavior: 'smooth' }));
  }
  function tabPreview(t) {
    const ss = S.run.sessions;
    t.innerHTML = `<div class="split st-split"><div>
        <div class="sky" id="sky"><div class="sky-empty" id="skyEmpty">${icon('orbit')}<b>Bereit für neue Perspektiven.</b><span class="mut sm">Noch keine Aufnahme verarbeitet.</span></div>
          <img id="skyImg" alt="Himmelsaufnahme" hidden><video id="skyVideo" controls preload="metadata" hidden></video>
          <svg id="skyOv" class="motion-video-overlay" aria-label="Spuren und Mindest-Winkelgeschwindigkeit" hidden></svg>
          <div class="sky-info"><span id="skyCoord">SKY FIELD</span><span id="skyFile"></span></div>
          <button class="sky-full" id="skyFull" title="Vollbild">${icon('expand')}</button></div>
        <div class="row" style="margin-top:10px"><label class="tgl"><input type="checkbox" id="ovl" ${S.overlay ? 'checked' : ''}><span class="switch"></span>Overlays</label>
          <div class="seg" id="vm"><button data-m="image">Vorschau</button><button data-m="video">Ereignisvideo</button></div>
          ${ss.length > 1 ? `<select id="sess">${ss.map((s, i) => `<option value="${i}">${esc(shortName(s.session))}</option>`).join('')}</select>` : ''}
          <span class="grow"></span><span class="mut sm" id="skyNote"></span></div><div class="sky-events" id="skyHits" aria-label="Zu Trefferbildern springen"></div></div>
      <div><div class="panel"><h2 style="margin-bottom:12px">Kennzahlen</h2><div class="stats st-stats" id="kpis"></div></div>
        <div class="panel"><div class="panel-h"><h2>Letzte Ereignisse</h2><span class="grow"></span><a class="linkbtn" href="#/skytrace/${encodeURIComponent(runId)}/protokoll">Protokoll ${icon('arrow')}</a></div><div class="loglist mini" id="miniLog"></div></div></div></div>`;
    if ($('#sess')) { $('#sess').value = S.session; $('#sess').onchange = (e) => { S.session = +e.target.value; renderPreview(); }; }
    $('#ovl').onchange = (e) => { S.overlay = e.target.checked; store.set('skytrace.overlay', S.overlay); renderPreview(); };
    $('#vm').onclick = (e) => { const b = e.target.closest('[data-m]'); if (b && !b.disabled) { S.mode = b.dataset.m; renderPreview(); } };
    $('#skyFull').onclick = () => document.fullscreenElement ? document.exitFullscreen() : $('#sky').requestFullscreen?.();
    renderPreview();
  }
  function renderPreview() {
    if (!root.isConnected || !$('#sky', root)) return;
    const e = S.engine, s = session(), live = isLive() && e.operation === 'analysis', row = live ? e.row : null, ev = EV();
    const img = $('#skyImg'), video = $('#skyVideo'), ov = $('#skyOv');
    let imgUrl = S.overlay ? (row?.preview_url || s?.preview_url) : (row?.clean_preview_url || s?.clean_preview_url);
    const interactive = !!(s?.overlay_timeline?.frames?.some(f => f.trails?.length || f.objects?.length) && s?.clean_url && ev);
    const videoUrl = interactive ? s.clean_url : S.overlay ? s?.video_url : s?.clean_url;
    if (interactive && !live && s.clean_preview_url) imgUrl = s.clean_preview_url;
    if (ev) ev.setOverlayVisible(ov, interactive && S.overlay && !live);
    if (!interactive && video._motionCleanup) { video._motionCleanup(); video._motionCleanup = null; ov.replaceChildren(); }
    if (interactive && ev) { video.setAttribute('controlsList', 'nofullscreen'); ev.videoOverlay(video, ov, s.overlay_timeline, S.run.candidates.filter(c => c.session === s.session), () => S.mode === 'video' ? video.currentTime : Infinity, () => cand()?.id); }
    let videoMode = S.mode === 'video' && !live;
    if (!S.overlay && !imgUrl && videoUrl && !live) videoMode = true;
    $$('#vm button').forEach(b => { b.classList.toggle('on', (b.dataset.m === 'video') === videoMode); if (b.dataset.m === 'video') b.disabled = live || !videoUrl; });
    if (videoMode && videoUrl) {
      img.hidden = true; $('#skyEmpty').hidden = true; video.hidden = false;
      if (video.dataset.url !== videoUrl) {
        const same = video.dataset.session === s.session, t0 = same ? video.currentTime : 0, playing = same && !video.paused;
        video.dataset.url = videoUrl; video.dataset.session = s.session;
        video.onloadedmetadata = () => { video.currentTime = Math.min(t0, video.duration || 0); if (!applySeek() && playing) video.play().catch(() => {}); };
        video.src = videoUrl;
      }
      if (video.readyState >= 1) applySeek();
    } else {
      video.pause(); video.hidden = true;
      if (imgUrl) {
        if (row?.preview_url) imgUrl += '?frame=' + e.revision;
        if (img.dataset.url !== imgUrl) { img.dataset.url = imgUrl; img.src = imgUrl; }
        img.hidden = false; $('#skyEmpty').hidden = true;
      } else { img.hidden = true; $('#skyEmpty').hidden = false; }
    }
    $('#skyFile').textContent = row?.filename || s?.session || '';
    const stars = row?.star_field?.inliers ?? row?.star_field?.matched;
    $('#skyCoord').textContent = stars !== undefined ? `${stars} Referenzsterne · ${row.star_field.accepted ? 'kompensiert' : 'unsicher'}` : live ? 'LIVE' : 'ARCHIV';
    $('#skyNote').textContent = row?.type === 'rejected' ? 'Unscharfe Aufnahme ausgeschlossen' : row?.timestamp_text || (s ? `${nf.format(s.frames || 0)} Aufnahmen${s.video_url ? ' · ' + (s.fps || 8) + ' Bilder/s' : ''}` : '');
    function applySeek() {
      if (!S.seek || S.seek.session !== s?.session || !Number.isFinite(video.duration)) return false;
      video.pause(); video.currentTime = Math.min(S.seek.seconds, Math.max(0, video.duration - .001)); S.seek = null;
      return true;
    }
    const hits = $('#skyHits', root);
    hits.replaceChildren();
    for (const c of S.run.candidates.filter(c => c.session === s?.session)) {
      const f = c.frames[c.initial_frame] || c.frames.find(x => x.hit);
      if (!videoTarget(c, f)) continue;
      const b = el('button', `${c.id} · Bild ${f.index}`, 'sky-event' + (c.key === S.key ? ' on' : ''));
      b.title = `${candidateName(c)} · ${f.timestamp_text || 'Aufnahmezeit unbekannt'}`;
      b.onclick = () => jumpToVideo(c, f); hits.append(b);
    }
    hits.hidden = !hits.childElementCount;
    const ss = S.run.sessions, st = e.stats || {};
    const frames = live ? st.processed - st.excluded : ss.reduce((a, x) => a + (x.frames || 0), 0), excl = live ? st.excluded : ss.reduce((a, x) => a + (x.excluded || 0), 0);
    const quality = frames + excl ? Math.round(100 * frames / (frames + excl)) : null;
    $('#kpis').innerHTML = `<div class="stat"><small>Aufnahmen verarbeitet</small><b class="tnum">${nf.format(Math.max(0, frames))}</b></div>
      <div class="stat"><small>${live ? 'Erkennungen' : 'Kandidaten'}</small><b class="tnum">${nf.format(live ? st.candidates : S.run.candidates.length)}</b></div>
      <div class="stat"><small>Scharf im Video</small><b class="tnum">${quality == null ? '—' : quality + ' %'}</b></div>
      <div class="stat"><small>Trefferbilder</small><b class="tnum">${nf.format(live ? st.hits : ss.reduce((a, x) => a + (x.hits || 0), 0))}</b></div>`;
    logs($('#miniLog'), 6);
  }

  // ---- Kandidaten: Liste + Zeitreihe + Messdetails + Katalogabgleich ----
  function tabCandidates(t) {
    t.innerHTML = `<div class="toolbar"><select id="kf"><option value="all">Alle Objekttypen</option><option value="trail">Einzelbild-Spuren</option><option value="satellite">Satelliten</option><option value="meteor">Meteorkandidaten</option>
      <option value="plane">Flugzeugkandidaten</option><option value="moving">Langsame Beweger</option></select><span class="grow"></span>
      <label class="tgl"><input type="checkbox" id="covl" ${S.overlay ? 'checked' : ''}><span class="switch"></span>Overlay</label></div>
      <div class="st-cands"><div class="st-clist" id="clist"></div><div class="panel st-cdetail" id="cdetail"></div></div>`;
    if (focusNext && S.run.candidates.some(c => c.key === focusNext)) { S.key = focusNext; S.frame = null; S.filter = 'all'; }
    focusNext = null;                                    // Wunsch aus der 3D-Ansicht (Tabwechsel innerhalb des Laufs)
    $('#kf').value = S.filter;
    $('#kf').onchange = (e) => { S.filter = e.target.value; drawList(); };
    $('#covl').onchange = (e) => { S.overlay = e.target.checked; store.set('skytrace.overlay', S.overlay); drawDetail(); };
    drawList();
  }
  function drawList() {
    const ev = EV(), list = S.run.candidates.filter(c => S.filter === 'all' || (ev ? ev.classification(c).kind : c.kind) === S.filter);
    if (!list.some(c => c.key === S.key)) { S.key = list[0]?.key || null; S.frame = null; }
    const host = $('#clist');
    host.innerHTML = list.length ? list.map((c, i) => { const hit = c.frames[c.initial_frame] || c.frames[0];
      return `<button class="ccard ${c.key === S.key ? 'on' : ''}" data-k="${esc(c.key)}" style="--i:${i}"><img src="${hit.url}" alt="" loading="lazy">
        <div><b>${esc(candidateName(c))}</b><span>${esc(c.id)} · ${esc(shortName(c.session))}</span><small class="mut">${c.timestamp ? new Date(c.timestamp * 1000).toLocaleTimeString('de-DE', { timeZone: 'UTC' }) + ' UTC' : 'Zeit unbekannt'}</small></div></button>`; }).join('')
      : `<div class="empty" style="padding:30px 16px">${S.run.candidates.length ? 'Keine Kandidaten dieses Typs.' : S.run.sessions.some(s => s.hits) ? 'Die Treffer sind gespeichert. Mit „Zeitreihen aktualisieren“ entstehen ihre Bildausschnitte.' : 'Noch keine Kandidaten. Hier erscheinen erkannte Spuren mit ihrem zeitlichen Kontext.'}</div>`;
    host.onclick = (e) => { const b = e.target.closest('[data-k]'); if (!b) return; S.key = b.dataset.k; S.frame = null; $$('.ccard', host).forEach(x => x.classList.toggle('on', x === b)); drawDetail(); };
    drawDetail();
  }
  function drawDetail() {
    stopTimeline();
    S.detailCleanup?.(); S.detailCleanup = null;
    const host = $('#cdetail');
    if (!host) return;
    host.replaceChildren();
    const c = cand(), ev = EV();
    if (!c) { host.innerHTML = `<div class="empty" style="border:0">${icon('orbit')}<h2>Jede Spur hat eine Geschichte.</h2>Wähle einen Kandidaten, um seine Zeitreihe und den Katalogabgleich anzusehen.</div>`; return; }
    S.frame = Math.min(c.frames.length - 1, Math.max(0, S.frame ?? c.initial_frame ?? 0));
    const top = el('div', undefined, 'panel-h');
    const hd = el('div'); hd.append(el('h2', candidateName(c)), el('div', c.id + ' · ' + observedDate(c) + ' · ' + (Number.isFinite(c.exposure_seconds) ? c.exposure_seconds + ' s Belichtung' : 'Belichtungszeit unbekannt'), 'mut sm'));
    top.append(hd);
    if (window.SkyTrace3D?.available(c)) {                // Bahn + Blickfeld räumlich (skytrace-3d.js)
      const b3 = el('button', undefined, 'btn ghost sm st3d-open'); b3.innerHTML = icon('globe') + '3D-Ansicht'; b3.title = 'Bahn und Teleskop-Blickfeld auf der Erde ansehen';
      b3.onclick = () => { focus3d = c.key; location.hash = SkyTrace3D.href(runId); }; top.append(el('span', undefined, 'grow'), b3);
    }
    host.append(top);
    const options = el('div', undefined, 'row frame-options'), views = el('div', undefined, 'seg');
    const full = el('button', 'Ganzes Bild'), crop = el('button', 'Ausschnitt'), hit = el('button', 'Zum Treffer', 'linkbtn'), inVideo = el('button', 'Im Video öffnen', 'linkbtn');
    full.dataset.view = 'full'; crop.dataset.view = 'crop'; views.append(full, crop); options.append(views, hit, el('span', undefined, 'grow'), inVideo); host.append(options);
    const observation = S.run.sessions.find(s => s.session === c.session);
    const hasFull = c.frames.some(f => f.full_image || (observation?.clean_url && Number.isFinite(f.video_seconds) && !f.excluded));
    full.disabled = !hasFull;
    if (!hasFull) S.full = false;
    const stage = el('div', undefined, 'cstage'), image = el('img'), video = el('video'), missing = el('div', 'Vollbild für diese Aufnahme nicht verfügbar. Der Ausschnitt bleibt verfügbar.', 'frame-missing mut sm'), svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    video.muted = true; video.playsInline = true; video.preload = 'metadata'; video.hidden = true; missing.hidden = true;
    image.alt = 'Unbeschrifteter Kandidatenausschnitt';
    const zoom = el('button', undefined, 'cstage-zoom'); zoom.title = 'Ausschnitt vergrößern'; zoom.append(svgIcon('expand'));
    stage.append(image, video, missing, svg, zoom); host.append(stage);
    const openZoom = () => { stopTimeline(); setPlay(false); if (S.full && !c.frames[S.frame].full_image) stage.requestFullscreen?.(); else ev?.openImage(c, S.frame, S.overlay, S.full); };
    stage.onclick = openZoom;
    const cap = el('div', undefined, 'row sm'), time = el('span', undefined, 'mut'), cls = el('b'); cap.append(time, el('span', undefined, 'grow'), cls); host.append(cap);
    const ctr = el('div', undefined, 'tl-ctrl'), prev = el('button', undefined, 'btn icon ghost sm'), play = el('button', undefined, 'btn icon ghost sm'), next = el('button', undefined, 'btn icon ghost sm'), slider = el('input'), counter = el('span', undefined, 'tnum mut sm');
    prev.append(svgIcon('back')); prev.title = 'Vorherige Aufnahme'; next.append(svgIcon('arrow')); next.title = 'Nächste Aufnahme';
    slider.type = 'range'; slider.min = 0; slider.max = c.frames.length - 1; slider.setAttribute('aria-label', 'Aufnahme der Zeitreihe');
    ctr.append(prev, play, next, slider, counter); host.append(ctr);
    const thumbs = el('div', undefined, 'tl-thumbs'), buttons = [];
    c.frames.forEach((f, i) => { const b = el('button', undefined, 'tl-th' + (f.excluded ? ' rej' : '') + (f.hit ? ' hit' : '')), im = el('img'); im.src = f.full_image?.url || f.url; im.loading = 'lazy'; im.alt = 'Bild ' + f.index;
      const utc = Number.isFinite(f.timestamp) ? new Date(f.timestamp * 1000).toLocaleTimeString('de-DE', { timeZone: 'UTC' }) : 'Zeit unbekannt';
      b.append(im, el('span', 'Bild ' + f.index + (f.hit ? ' · Treffer' : '')), el('small', utc)); b.title = f.timestamp_text || utc; b.onclick = () => show(i); buttons.push(b); thumbs.append(b); });
    host.append(thumbs);
    const shape = (tag, attrs) => { const n = document.createElementNS(svg.namespaceURI, tag); for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v); return n; };
    function show(i) {
      S.frame = (i + c.frames.length) % c.frames.length;
      const f = c.frames[S.frame];
      full.classList.toggle('on', S.full); crop.classList.toggle('on', !S.full);
      inVideo.disabled = !videoTarget(c, f);
      const display = ev?.frameImage(c, f, S.full) || (!S.full ? f : null);
      const useVideo = S.full && !display && observation?.clean_url && Number.isFinite(f.video_seconds) && !f.excluded;
      image.hidden = !display; video.hidden = !useVideo; missing.hidden = !!(display || useVideo);
      video.pause();
      if (display) { image.src = display.url; image.alt = S.full ? 'Vollständige Himmelsaufnahme' : 'Unbeschrifteter Kandidatenausschnitt'; }
      function paint(width, height, overlay) {
        svg.replaceChildren(); svg.setAttribute('viewBox', `0 0 ${width} ${height}`); svg.style.display = S.overlay ? '' : 'none';
        const o = overlay;
        if (o?.points?.length) {
          const pts = o.points;
          svg.append(pts.length > 1 ? shape('polyline', { points: pts.map(p => p.join(',')).join(' '), fill: 'none', stroke: '#f2d385', 'stroke-width': 2.5, 'stroke-linecap': 'round' })
            : shape('circle', { cx: pts[0][0], cy: pts[0][1], r: 10, fill: 'none', stroke: '#f2d385', 'stroke-width': 2.5 }));
          ev?.motionLabel(svg, c, pts, width, height);
        }
        if (o?.box) { const [x, y, w, h] = o.box; svg.append(shape('rect', { x, y, width: w, height: h, fill: 'none', stroke: '#f2d385', 'stroke-width': 2 })); }
      }
      if (display) paint(display.width, display.height, display.overlay); else svg.replaceChildren();
      if (useVideo) {
        const seek = () => { if (S.frame !== c.frames.indexOf(f)) return; video.currentTime = Math.min(f.video_seconds, Math.max(0, video.duration - .001));
          const frame = ev?.frameImage(c, { ...f, full_image: { width: video.videoWidth, height: video.videoHeight } }, true);
          paint(video.videoWidth, video.videoHeight, frame?.overlay); };
        video.onloadedmetadata = seek;
        video.onerror = () => { if (!video.hidden) { video.hidden = true; missing.hidden = false; } };
        if (video.dataset.url !== observation.clean_url) { video.dataset.url = observation.clean_url; video.src = observation.clean_url; }
        else if (video.readyState >= 1) seek();
      }
      time.textContent = 'Bild ' + f.index + ' · ' + (f.timestamp_text || 'Zeit unbekannt');
      cls.textContent = f.hit ? 'TREFFER' : f.excluded ? 'UNSCHARFER KONTEXT' : 'KONTEXT'; cls.className = f.hit ? 'acc' : 'mut';
      slider.value = S.frame; counter.textContent = (S.frame + 1) + ' / ' + c.frames.length;
      buttons.forEach((b, j) => b.classList.toggle('on', j === S.frame));
    }
    full.onclick = () => { S.full = true; show(S.frame); }; crop.onclick = () => { S.full = false; show(S.frame); };
    hit.onclick = () => show(c.initial_frame || 0); inVideo.onclick = () => jumpToVideo(c, c.frames[S.frame]);
    S.detailCleanup = () => { video.pause(); video.onloadedmetadata = video.onerror = null; };
    function setPlay(on) { play.replaceChildren(svgIcon(on ? 'stop' : 'play')); play.title = on ? 'Zeitreihe anhalten' : 'Zeitreihe abspielen'; }
    prev.onclick = () => show(S.frame - 1); next.onclick = () => show(S.frame + 1); slider.oninput = () => show(+slider.value);
    play.onclick = () => { if (S.timer) { stopTimeline(); setPlay(false); } else { S.timer = setInterval(() => show(S.frame + 1), 700); setPlay(true); } };
    setPlay(false);
    show(S.frame);
    const details = ev ? ev.analysisDetails(c, host) : host;
    const cat = el('div', undefined, 'st-catalogs'); cat.append(el('h3', 'Katalogabgleich zur echten Aufnahmezeit'));
    for (const r of c.catalogs || []) {
      const item = el('div', undefined, 'st-cat'), rw = el('div', undefined, 'row');
      rw.append(el('b', providerNames[r.provider] || r.provider), el('span', undefined, 'grow'),
        el('span', statusNames[r.status] || r.status, 'chip ' + (r.status === 'compatible' ? 'ok' : ['needs_archive', 'error', 'temporal_hint'].includes(r.status) ? 'warn' : '')));
      item.append(rw, el('p', r.detail, 'mut sm'));
      if (r.provider === 'asteroids' && r.objects_in_field !== undefined) item.append(el('p', 'Objekte im Feld nach JPL-Nachrechnung: ' + r.objects_in_field + ' · Positionsgrenze 30″ · Grenzgröße ' + (r.query?.['vmag-lim'] ?? 20) + '.', 'dim sm'));
      for (const m of r.matches || []) item.append(el('p', (m.name || 'Katalogobjekt') + ' · ' + m.separation_arcsec + '″ · ' + m.evidence, 'sm acc'));
      if (r.source) { const a = el('a', 'Datenquelle ↗', 'sm'); a.href = r.source; a.target = '_blank'; a.rel = 'noreferrer'; item.append(a); }
      cat.append(item);
    }
    const site = el('p', undefined, 'mut sm');
    if (c.site) site.textContent = 'Aufnahmestandort: ' + (c.site.name || 'Gespeicherte Position') + ' · ' + c.site.lat + '°, ' + c.site.lon + '°'
    + (c.site.accuracy_m ? ' · ± ' + (c.site.accuracy_m >= 1000 ? (c.site.accuracy_m / 1000).toFixed(1).replace('.', ',') + ' km' : Math.round(c.site.accuracy_m) + ' m') : '')
    + (c.site.source ? ' · ' + c.site.source : '');
    else { site.append('Für den räumlichen Abgleich fehlt der Aufnahmestandort. '); const a = el('a', 'Standort hinterlegen →'); a.href = '#/settings/skytrace'; site.append(a); }
    cat.append(site); details.append(cat);
  }
  function stopTimeline() { if (S.timer) clearInterval(S.timer); S.timer = null; }

  // ---- Beobachtungen ----
  function tabSessions(t) {
    t.innerHTML = S.run.sessions.length ? `<div class="gallery stagger">${S.run.sessions.map((s, i) => `<div class="card" style="--i:${i}">
        <div class="thumb">${s.preview_url ? `<img src="${s.preview_url}" alt="" loading="lazy" onload="this.classList.add('ld')">` : `<div class="ph">${icon('orbit')}</div>`}</div>
        <div class="body"><h3>${esc(shortName(s.session))}</h3><div class="meta"><span class="chip">${esc(s.state || 'Gespeichert')}</span></div>
          <div class="foot"><span>${nf.format(s.frames || 0)} Aufnahmen · ${s.excluded || 0} unscharf · ${s.hits || 0} Treffer</span>
          <button class="btn sm ghost" data-s="${i}">${icon('play')}Ansehen</button></div></div></div>`).join('')}</div>`
      : '<div class="empty">Für diesen Lauf sind noch keine Bildfolgen gespeichert.</div>';
    const fits = S.run.sessions.filter(s => s.folder && (s.sat_matches || 0) >= 3);
    if (fits.length) t.insertAdjacentHTML('beforeend', `<div class="panel st-sitefit"><div class="panel-h"><h2>${icon('pin')}Standort der Nacht prüfen</h2></div>
      <p class="mut sm" style="margin:-4px 0 10px">Aus mehreren zugeordneten Satellitenspuren lassen sich Standort und Uhrversatz der Kamera zurückrechnen
        (1 km Standortfehler ≈ 7′ quer bei 500 km). Nur ein Vorschlag – übernommen wird erst auf Knopfdruck.</p>
      ${fits.map(s => `<div class="st-fit" data-fit="${esc(s.folder)}" data-session="${esc(s.session)}"><div class="row"><b>${esc(shortName(s.session))}</b><span class="mut sm">${s.sat_matches} Satellitentreffer</span><span class="grow"></span>
        <button class="btn sm ghost" data-fitgo>Prüfen</button></div><div class="st-fit-res sm"></div></div>`).join('')}</div>`);
    t.onclick = async (e) => {
      const b = e.target.closest('[data-s]'); if (b) { S.session = +b.dataset.s; S.mode = 'video'; location.hash = `#/skytrace/${encodeURIComponent(runId)}/vorschau`; return; }
      const box = e.target.closest('[data-fit]'); if (!box) return;
      if (e.target.closest('[data-fitgo]')) {
        const go = e.target.closest('[data-fitgo]'), out = $('.st-fit-res', box);
        go.disabled = true; go.classList.add('busy'); out.innerHTML = '<span class="mut">Rechne …</span>';
        try { box._fit = await api(`${T}/sitefit?run=${encodeURIComponent(runId)}&folder=${encodeURIComponent(box.dataset.fit)}`); out.innerHTML = fitHtml(box._fit); }
        catch (err) { out.innerHTML = `<span class="bad">${esc(err.message)}</span>`; }
        go.disabled = false; go.classList.remove('busy');
      }
      if (e.target.closest('[data-fitapply]')) applyFit(box);
    };
  }
  const DIRS = ['Norden', 'Nordosten', 'Osten', 'Südosten', 'Süden', 'Südwesten', 'Westen', 'Nordwesten'];
  const km = (v) => (Math.round(v * 100) / 100).toLocaleString('de-DE');
  function fitHtml(r) {
    if (!r.ok) return `<p class="mut">${esc(r.reason)}</p>`;
    const dir = DIRS[Math.round(r.bearing_deg / 45) % 8];
    const facts = `${r.trails} Spuren (${r.timed} mit Zeitbezug${r.dropped.length ? `, ${r.dropped.length} als Ausreißer verworfen` : ''}) · Abweichung quer ${Math.round(r.rms_before_arcsec)}″ → ${Math.round(r.rms_after_arcsec)}″ · Uhr der Kamera ${r.clock_s > 0 ? 'geht ' + String(r.clock_s).replace('.', ',') + ' s nach' : r.clock_s < 0 ? 'geht ' + String(-r.clock_s).replace('.', ',') + ' s vor' : 'stimmt'} (± ${String(r.clock_sigma_s).replace('.', ',')} s)`;
    if (!r.worth) return `<p><span class="ok">Kein deutlich besserer Ort</span> <span class="mut">– Verschiebung ${km(r.shift_km)} km ± ${km(r.shift_sigma_km)} km liegt im Rahmen.</span></p><p class="dim">${facts}</p>`;
    return `<p>Diese Nacht passt besser zu einem Ort <b>${km(r.shift_km)} km weiter im ${dir}</b> (± ${km(r.ellipse_km.major)} km) als „${esc(r.site?.name || 'eingestellter Ort')}“.</p>
      <p class="dim">${facts}</p>
      <div class="row"><button class="btn sm" data-fitapply>Für diese Nacht übernehmen …</button>
        <a class="sm" href="https://www.openstreetmap.org/?mlat=${r.lat}&mlon=${r.lon}#map=15/${r.lat}/${r.lon}" target="_blank" rel="noreferrer">Auf der Karte ansehen ↗</a></div>`;
  }
  async function applyFit(box) {
    const r = box._fit;
    const night = r.night || (box.dataset.session.match(/(\d{4})-?(\d{2})-?(\d{2})/) || []).slice(1).join('-');
    if (!/^\d{4}-\d{2}-\d{2}$/.test(night)) { toast('Nacht-Datum unbekannt – bitte unter Einstellungen → Standorte zuordnen.', 'err'); return; }
    if (!await confirmDlg('Standort übernehmen?', `Ein neuer Ort „${esc((r.site?.name || 'Standort') + ' (Satelliten ' + night + ')')}“ (± ${km(r.accuracy_m / 1000)} km) wird angelegt und der Nacht ${esc(night)} zugeordnet. Der bisherige Ort bleibt erhalten. Danach „Zeitreihen aktualisieren“, um neu abzugleichen.`, 'Übernehmen')) return;
    try {
      const d = await api('/sites?nights=false');
      const id = 's' + Math.random().toString(16).slice(2, 10);
      await send('/sites', { ...d, list: [...d.list, { id, name: `${r.site?.name || 'Standort'} (Satelliten ${night})`, lat: r.lat, lon: r.lon, alt_m: r.alt_m, accuracy_m: r.accuracy_m,
        note: `aus ${r.trails} Satellitenspuren zurückgerechnet, Uhr ${r.clock_s} s` }] });
      await send('/sites/night', { night, site_id: id, nights: false });
      toast('Ort übernommen – Zeitreihen aktualisieren, um neu abzugleichen.', 'ok');
      $('.st-fit-res', box).insertAdjacentHTML('beforeend', '<p class="ok">Übernommen. <a href="#/settings/standorte">Standorte ansehen</a></p>');
    } catch (err) { toast(err.message, 'err'); }
  }

  // ---- Protokoll ----
  // ---- 3D: Erde, Teleskopweg, Blickfeld und zugeordnete Bahnen (skytrace-3d.js) als eigene Bühne ----
  function tab3d(t) {
    const focus = focus3d; focus3d = null;
    t.innerHTML = '<div id="st3dHost"></div>';
    S.view3d = SkyTrace3D.mount($('#st3dHost', t), runId, { focus, session: focus ? null : session()?.session, onCandidate: focusCandidate });
  }
  function tabLog(t) {
    t.innerHTML = `<div class="panel"><div class="panel-h"><h2>Aktivitätsprotokoll</h2><span class="grow"></span><span class="mut sm">${isLive() ? '<span class="live"></span> live' : S.engine.run === runId ? 'letzter Lauf dieser Sitzung' : 'Protokoll nur während und nach einem Lauf in dieser Sitzung'}</span></div>
      <div class="loglist" id="logList"></div></div>`;
    S.logId = 0;
    logs($('#logList'), 150);
  }
  function logs(list, max) {
    if (!list) return;
    const all = S.engine.run === runId ? (S.engine.logs || []) : [];
    if (!all.length) { list.innerHTML = '<div class="mut sm">Hier siehst du, was während der Analyse passiert.</div>'; return; }
    const atBottom = list.scrollHeight - list.scrollTop - list.clientHeight < 70;
    list.innerHTML = all.slice(-max).map(l => `<div class="logrow ${esc(l.type)}"><time>${new Date(l.time * 1000).toLocaleTimeString('de-DE')}</time><span class="ltag">${LOGTAG[l.type] || 'INFO'}</span><span>${esc(l.text)}</span></div>`).join('');
    if (atBottom || max < 20) list.scrollTop = list.scrollHeight;
  }

  // ---- Live-Aktualisierung ----
  async function refreshRun() {
    const ticket = ++S.ticket;
    try {
      const data = await api(T + '/run?id=' + encodeURIComponent(runId));
      if (ticket !== S.ticket) return;
      const changed = JSON.stringify(data.candidates.map(c => c.key)) !== JSON.stringify(S.run.candidates.map(c => c.key)) || data.sessions.length !== S.run.sessions.length;
      S.run = data;
      S.runStamp = Date.now();
      if (changed) draw(); else if (tab === 'vorschau') renderPreview();
    } catch { /* Lauf noch im Aufbau */ }
  }
  draw();
  const onKey = (e) => {
    if (e.ctrlKey || e.metaKey || e.altKey || e.target.closest('input,select,textarea') || $('dialog[open]')) return;
    if (/^[1-9]$/.test(e.key) && tabs()[+e.key - 1]) { location.hash = `#/skytrace/${encodeURIComponent(runId)}/${tabs()[+e.key - 1][0]}`; e.preventDefault(); }
    else if (tab === '3d' && (e.key === 'f' || e.key === 'F') && S.view3d) { S.view3d.fullscreen(); e.preventDefault(); }
    else if (tab === 'kandidaten' && (e.key === 'ArrowDown' || e.key === 'ArrowUp')) {
      const bs = $$('.ccard'), i = bs.findIndex(b => b.classList.contains('on')), n = bs[i + (e.key === 'ArrowDown' ? 1 : -1)];
      if (n) { n.click(); n.scrollIntoView({ block: 'nearest' }); e.preventDefault(); }
    }
  };
  document.addEventListener('keydown', onKey);
  return {
    async update(st) {
      const s = st.modules.skytrace;
      if (!s) return;
      const wasLive = isLive(), mine = s.run === runId;
      if (!s.running && !wasLive && !mine) return;
      const prevRev = S.engine.revision, prevStop = S.engine.stopping;
      S.engine = await api(T + '/state');
      const live = isLive();
      if (wasLive && !live) { toast(S.engine.status, S.engine.status.startsWith('Analyse unterbrochen') ? 'err' : 'ok'); await refreshRun(); draw(); return; }
      if (S.engine.revision === prevRev) { if (live) jobState(); return; }       // nur Wartezeit-Text (JPL) weiterzählen
      if (prevStop !== S.engine.stopping) draw();
      jobState();
      if (tab === 'vorschau') renderPreview();
      if (tab === 'protokoll') logs($('#logList'), 150);
      if (live && Date.now() - S.runStamp > 6500) refreshRun();
    },
    navigate(path) {
      const m = path.match(/^\/skytrace\/([^/]+)(?:\/(\w+))?$/);
      if (!m || decodeURIComponent(m[1]) !== runId || m[1] === 'neu' || m[1] === '3d') return false;
      const t2 = m[2] || 'vorschau';
      if (t2 !== tab) { tab = t2; drawTab(); }
      return true;
    },
    actions: () => [
      ...tabs().map(([k, l], i) => ['arrow', `${l} anzeigen`, `Taste ${i + 1}`, () => { location.hash = `#/skytrace/${encodeURIComponent(runId)}/${k}`; }]),
      ...(S.run.report_url ? [['arrow', 'Bericht öffnen', 'eigenständiges HTML', () => window.open(S.run.report_url, '_blank', 'noopener')]] : []),
      ...(isLive() ? [['stop', 'Analyse anhalten', 'Teilergebnisse bleiben', async () => { S.engine = await send(T + '/stop'); draw(); }]]
        : [['trash', 'Analyse löschen', 'mit Videos und Bericht', async () => { if (await deleteRun(runId, runTitle())) location.hash = '#/skytrace'; }]]),
    ],
    destroy: () => { ++S.ticket; cleanupMedia(); stopTimeline(); document.removeEventListener('keydown', onKey); if (document.fullscreenElement) document.exitFullscreen(); },
  };
}

// ------------------------------------------------------------ Einstellungen --- //
async function settings(el) {
  const [cfg, eng] = await Promise.all([api(T + '/settings'), api(T + '/engine')]);
  const runs = await api(T + '/runs');
  const scopes = new Set([...Object.keys(cfg.sites || {}), ...runs.map(r => r.title).filter(t => t.includes('_observation_'))]);
  const ov = EV()?.displaySettings() || { unit: 'arcsec' };
  el.innerHTML = `<div class="split"><div>
    <form class="panel" id="sitef"><div class="panel-h"><h2>${icon('pin')}Standort einer Beobachtung</h2></div>
      <p class="mut sm" style="margin:-4px 0 14px">Orte, Standard-Ort und Zeiträume gelten für ganz AstroFlow: <a href="#/settings/standorte">Einstellungen → Standorte</a>.
        Hier nur Ausnahmen für einzelne Beobachtungen. Für alte Aufnahmen zählt der <b>damalige</b> Teleskopstandort; Standortdaten aus FITS werden automatisch berücksichtigt.</p>
      <label class="field"><span>Beobachtung</span><select name="scope"><option value="">Beobachtung wählen …</option>
        ${[...scopes].map(n => `<option value="${esc(n)}">${esc(shortName(n) + ' · ' + n.split('_observation_')[0])}</option>`).join('')}</select></label>
      <label class="field"><span>Adresse oder Ort</span><div class="row" style="flex-wrap:nowrap"><input id="addr" placeholder="Ort oder Adresse eingeben" style="flex:1"><button type="button" class="btn ghost" id="addrGo">Suchen</button></div>
        <div class="addr" id="addrRes"></div><small>Adresssuche: Photon/komoot · © OpenStreetMap. <button type="button" class="linkbtn" id="locate">Aktuellen Gerätestandort abfragen</button></small></label>
      <label class="field"><span>Standortname</span><input name="name" placeholder="Mein Teleskopstandort"></label>
      <div class="row" style="flex-wrap:nowrap;gap:14px"><label class="field" style="flex:1"><span>Breitengrad</span><input name="lat" type="number" min="-90" max="90" step="any" placeholder="Nord positiv"></label>
        <label class="field" style="flex:1"><span>Längengrad</span><input name="lon" type="number" min="-180" max="180" step="any" placeholder="Ost positiv"></label>
        <label class="field" style="width:120px"><span>Höhe (m)</span><input name="alt" type="number" min="-500" max="10000" step="any"></label></div>
      <div class="row"><button class="btn" type="submit">Standort speichern</button><span class="mut sm" id="siteNote"></span></div></form>
    <form class="panel" id="catf"><h2 style="margin-bottom:12px">${icon('orbit')}Katalogabgleich</h2>
      ${[['enabled', 'Online-Abgleich aktivieren', 'Keine Fotos werden übertragen'], ['auto_solve', 'Plate Solving automatisch nachholen', 'Für Katalogkoordinaten, auch bei alten Läufen'],
        ['auto_satellites', 'Satellitendaten zur Aufnahmezeit abrufen', 'UTC aus Astrofotos · Cache, Standort und Bildfeld'], ['auto_meteors', 'Lokale Meteorflugbahnen prüfen', 'GMN · nur im Zeitfenster über deinem Horizont'],
        ['auto_aircraft', 'Historische Flugpositionen abrufen', 'Standorthorizont + Zeitfenster · OpenSky-Konto nötig']]
        .map(([k, l, s]) => `<label class="modrow"><div><b>${l}</b><div class="mut sm">${s}</div></div><input type="checkbox" name="${k}" ${cfg[k] !== false ? 'checked' : ''}><span class="switch"></span></label>`).join('')}
      <div class="srcs"><span><b>JPL</b> bekannte Asteroiden · Ephemeriden zur echten Aufnahmezeit</span><span><b>CNEOS</b> Feuerkugeln · nur vorhandener Zeitcache</span><span><b>GMN</b> vermessene Meteorflugbahnen</span></div>
      <p class="dim sm" id="archive">Archivstatus wird geladen …</p>
      <details><summary class="sm mut">Eigene Archivdateien / Download-URLs (optional)</summary>
        <label class="field" style="margin-top:10px"><span>Historische Satellitenbahnen · TLE</span><input name="tle_file" placeholder="Lokaler Pfad zur TLE-Datei" value="${esc(cfg.tle_file || '')}"><input name="tle_url" type="url" placeholder="oder https://… (direkter Download)" value="${esc(cfg.tle_url || '')}" style="margin-top:6px"></label>
        <label class="field"><span>Historische Flugpositionen · CSV</span><input name="flight_file" placeholder="Lokaler Pfad zur ADS-B-Datei" value="${esc(cfg.flight_file || '')}"><input name="flight_url" type="url" placeholder="oder https://…" value="${esc(cfg.flight_url || '')}" style="margin-top:6px">
          <small>CSV: timestamp (UTC/Unix), icao24, latitude, longitude, geo_altitude (m), optional callsign. Maximal 15 s Lücke.</small></label></details>
      <div class="row" style="margin-top:12px"><button class="btn" type="submit">Speichern</button><span class="mut sm">Bestehende Ergebnisse über „Zeitreihen aktualisieren“ erneuern.</span></div></form></div>
    <div>
    <form class="panel" id="engf"><div class="panel-h"><h2>Analyse-Engine</h2><span class="grow"></span>
        <span class="chip ${eng.ok ? 'ok' : eng.checked ? 'warn' : ''}">${eng.ok ? 'bereit' : eng.checked ? 'unvollständig' : 'wird geprüft …'}</span></div>
      <p class="mut sm" style="margin:-4px 0 12px">Die Analyse läuft als eigener Prozess und braucht OpenCV, PyTorch/YOLO, PyAV und Skyfield. Ein vorhandenes SkyTrace-Python kann direkt verwendet werden.</p>
      ${eng.checked && !eng.ok ? `<p class="warn sm">${eng.error ? esc(eng.error) : 'Fehlende Pakete: ' + esc((eng.missing || []).join(', '))}</p>` : ''}
      <label class="field"><span>Python-Interpreter</span><input name="python" value="${esc(eng.configured.python || '')}" placeholder="${esc(eng.python)}"><small>Leer = <code>.venv</code> im Datenordner, sonst der AstroFlow-Interpreter.</small></label>
      <label class="field"><span>Datenordner</span><input name="home" value="${esc(eng.configured.home || '')}" placeholder="${esc(eng.home)}"><small>Ergebnisse, Katalog-Cache und Standort. Ein bestehender SkyTrace-Ordner (mit <code>Ergebnisse/</code>) kann übernommen werden – wirkt nach einem Neustart.</small></label>
      <div class="row"><button class="btn" type="submit">Speichern &amp; prüfen</button></div></form>
    <div class="panel"><label class="modrow" style="border:0;padding:0"><div><b>Gestackte Aufnahmen automatisch analysieren</b>
        <div class="mut sm">Nach jedem Stacken sucht SkyTrace in den neuen Aufnahmen nach Meteoren und Satelliten – je Nacht ein Lauf, nacheinander im Hintergrund.</div></div>
        <input type="checkbox" id="autoSt" ${eng.configured.auto !== false ? 'checked' : ''}><span class="switch"></span></label></div>
    <form class="panel" id="oskf"><h2 style="margin-bottom:8px">OpenSky verbinden</h2>
      <p class="mut sm" style="margin:0 0 12px">Der Anbieter muss dein Konto für historische Trino-Daten freischalten. Das Passwort gibst du nur auf der offiziellen HTTPS-Anmeldeseite ein.</p>
      <div class="row" style="flex-wrap:nowrap"><input name="user" placeholder="OpenSky-Benutzername" value="${esc(cfg.opensky_user || '')}" autocomplete="username" style="flex:1"><button class="btn ghost" type="submit">Verbinden</button></div>
      <p class="sm mut" id="oskState" style="margin:10px 0 0"></p><a class="btn sm" id="oskLogin" target="_blank" rel="noreferrer" hidden>Bei OpenSky anmelden ↗</a>
      <p class="sm" style="margin:8px 0 0"><a href="https://opensky-network.org/data/trino" target="_blank" rel="noreferrer">Archivzugang beim Anbieter beantragen ↗</a></p></form>
    <div class="panel" id="satcatp" hidden><h2 style="margin-bottom:8px">Satellitennamen (CelesTrak)</h2>
      <p class="sm warn" id="satcatState" style="margin:0 0 12px"></p>
      <p class="mut sm" style="margin:0 0 12px">CelesTrak verlangt, nach einer Fehlerantwort nicht weiter abzufragen, bis ein Mensch die Ursache geprüft hat. Bei einer Sperre (HTTP 403) erst freigeben, wenn sie aufgehoben ist – sonst wird sie verlängert.</p>
      <div class="row"><button class="btn ghost" type="button" id="satcatRelease">Namensabfrage wieder freigeben</button>
        <a class="sm" href="https://celestrak.org/usage-policy.php" target="_blank" rel="noreferrer">Nutzungsregeln ↗</a></div></div>
    <form class="panel" id="ovf"><h2 style="margin-bottom:12px">Overlay-Anzeige</h2>
      <label class="field"><span>Geschwindigkeitseinheit</span><select name="unit"><option value="arcsec">Winkelgeschwindigkeit (″/s)</option><option value="kmh">Geschwindigkeitsschätzung (km/h*)</option></select></label>
      <label class="field"><span>Angenommene Entfernung (km)</span><input name="km" type="number" min="0.001" step="any" placeholder="z. B. 10" value="${esc(ov.distanceKm ?? '')}">
        <small>Die Entfernung ist ungemessen – das Overlay zeigt eine Mindest-Schätzung für deine Annahme. Gilt nur in diesem Browser.</small></label>
      <button class="btn ghost" type="submit">Übernehmen</button></form></div></div>`;
  const sf = $('#sitef'), E = sf.elements;
  const scopeSite = () => (E.scope.value ? cfg.sites?.[E.scope.value] : null) || {};
  const fill = (s) => { E.name.value = s.name || ''; E.lat.value = s.lat ?? ''; E.lon.value = s.lon ?? ''; E.alt.value = s.alt_m ?? 0; };
  const lock = () => { for (const n of ['name', 'lat', 'lon', 'alt']) E[n].disabled = !E.scope.value; };
  fill(scopeSite()); lock();
  E.scope.onchange = () => { fill(scopeSite()); lock(); };
  const search = async () => {
    try {
      const rows = await api(T + '/geocode?q=' + encodeURIComponent($('#addr').value));
      $('#addrRes').innerHTML = rows.map((r, i) => `<button type="button" class="chip" data-i="${i}">${esc(r.name)}</button>`).join('') || '<span class="mut sm">Kein Ergebnis gefunden.</span>';
      $('#addrRes').onclick = (e) => { const b = e.target.closest('[data-i]'); if (b) { fill(rows[+b.dataset.i]); $('#addrRes').innerHTML = ''; $('#siteNote').textContent = 'Übernommen – Höhe prüfen und speichern.'; } };
    } catch (e) { toast(e.message, 'err'); }
  };
  $('#addrGo').onclick = search;
  $('#addr').onkeydown = (e) => { if (e.key === 'Enter') { e.preventDefault(); search(); } };
  $('#locate').onclick = () => {
    if (!window.isSecureContext || !navigator.geolocation) { toast('Die Standortabfrage braucht HTTPS bzw. localhost – bitte die Adresssuche nutzen.', 'err'); return; }
    navigator.geolocation.getCurrentPosition(p => { fill({ name: 'Aktueller Gerätestandort', lat: p.coords.latitude, lon: p.coords.longitude, alt_m: p.coords.altitude ?? 0 });
      $('#siteNote').textContent = `Genauigkeit ca. ${Math.round(p.coords.accuracy)} m – nur für denselben damaligen Teleskopstandort speichern.`; },
    e => toast(e.message, 'err'), { enableHighAccuracy: true, timeout: 25000 });
  };
  sf.onsubmit = async (e) => {
    e.preventDefault();
    if (!E.scope.value) { toast('Erst eine Beobachtung wählen – allgemeine Orte unter Einstellungen → Standorte.', 'err'); return; }
    if ((E.lat.value === '') !== (E.lon.value === '')) { toast('Bitte Breiten- und Längengrad zusammen eingeben.', 'err'); return; }
    try {
      Object.assign(cfg, await send(T + '/settings', { session: E.scope.value || null, site: E.lat.value !== '' ? { name: E.name.value, lat: +E.lat.value, lon: +E.lon.value, alt_m: +(E.alt.value || 0), source: 'Web-Einstellung' } : null }));
      toast(E.lat.value !== '' ? 'Standort für diese Beobachtung gespeichert.' : 'Ausnahme entfernt – es gilt die Ortsliste.', 'ok');
    } catch (err) { toast(err.message, 'err'); }
  };
  $('#catf').onsubmit = async (e) => {
    e.preventDefault();
    const f = e.target.elements, body = {};
    for (const k of ['enabled', 'auto_solve', 'auto_satellites', 'auto_meteors', 'auto_aircraft']) body[k] = f[k].checked;
    for (const k of ['tle_file', 'tle_url', 'flight_file', 'flight_url']) body[k] = f[k].value;
    try { Object.assign(cfg, await send(T + '/settings', body)); toast('Katalogeinstellungen gespeichert.', 'ok'); } catch (err) { toast(err.message, 'err'); }
  };
  $('#autoSt').onchange = async (e) => {
    try { await send(T + '/engine', { auto: e.target.checked }); toast(e.target.checked ? 'Neue Stacks werden automatisch analysiert.' : 'Automatische Analyse aus.', 'ok'); }
    catch (err) { toast(err.message, 'err'); e.target.checked = !e.target.checked; }
  };
  $('#engf').onsubmit = async (e) => {
    e.preventDefault();
    try {
      const r = await send(T + '/engine', { python: e.target.elements.python.value, home: e.target.elements.home.value });
      toast(r.restart ? 'Gespeichert – der Datenordner wirkt nach einem Neustart.' : 'Gespeichert – Engine wird geprüft …', 'ok');
      setTimeout(() => settings(el), 2500);
    } catch (err) { toast(err.message, 'err'); }
  };
  const archive = async () => {
    try {
      const d = await api(T + '/catalog/status');
      if (!$('#archive') || !$('#oskState')) return null;   // inzwischen weiternavigiert
      $('#archive').textContent = `Satelliten: ${d.satellite_time_queries} gespeicherte Zeitabfragen · lokale Jahre ${d.satellite_years.join(', ') || 'keine'} · GMN-Monate ${d.meteor_months.join(', ') || 'keine'} · Satellitennamen ${d.satcat_file ? 'vom ' + new Date(d.satcat_file.updated * 1000).toLocaleString('de-DE', { dateStyle: 'short', timeStyle: 'short' }) : 'noch nicht geladen'} · ${Math.round(d.bytes / 1048576)} MB Cache`;
      $('#oskState').textContent = d.opensky.detail;
      $('#satcatp').hidden = !d.satcat?.reason;
      if (d.satcat?.reason) $('#satcatState').textContent = d.satcat.reason;
      $('#oskLogin').hidden = !d.opensky.auth_url;
      if (d.opensky.auth_url) $('#oskLogin').href = d.opensky.auth_url;
      return d.opensky.status;
    } catch { if ($('#archive')) $('#archive').textContent = 'Archivstatus vorübergehend nicht erreichbar.'; return null; }
  };
  archive();
  $('#satcatRelease').onclick = async () => {
    try { await send(T + '/catalog/satcat/release', {}); toast('Namensabfrage freigegeben – gilt für die nächste Analyse.', 'ok'); archive(); } catch (err) { toast(err.message, 'err'); }
  };
  $('#oskf').onsubmit = async (e) => {
    e.preventDefault();
    try {
      await send(T + '/catalog/connect', { user: e.target.elements.user.value });
      for (let i = 0; i < 20 && $('#oskState'); i++) { const s = await archive(); if (s !== 'connecting') break; await new Promise(r => setTimeout(r, 1500)); }
    } catch (err) { toast(err.message, 'err'); }
  };
  const of = $('#ovf');
  of.elements.unit.value = ov.unit || 'arcsec';
  const sync = () => { of.elements.km.disabled = of.elements.unit.value !== 'kmh'; };
  of.elements.unit.onchange = sync; sync();
  of.onsubmit = (e) => {
    e.preventDefault();
    if (!EV()) return;
    try { EV().setDisplaySettings({ unit: of.elements.unit.value, distanceKm: Number(of.elements.km.value) }); toast('Overlay-Anzeige gespeichert.', 'ok'); } catch (err) { toast(err.message, 'err'); }
  };
}

// --------------------------------------------------------------- Statistik --- //
// Einordnung wie in der Kandidatenliste (AstroEvidence.classification), damit Zahlen und Filter übereinstimmen.
const STAT_KINDS = [['satellite', 'Satelliten'], ['plane', 'Flugzeugkandidaten'], ['meteor', 'Meteorkandidaten'], ['moving', 'Langsame Beweger'], ['trail', 'Einzelbild-Spuren']];
const CAT_PARTS = [['hit', 'Treffer'], ['hint', 'Zeitlicher Hinweis'], ['none', 'Geprüft, kein Treffer'], ['open', 'Nicht prüfbar']];
const catPart = (s) => s === 'compatible' ? 'hit' : s === 'temporal_hint' ? 'hint' : s === 'checked_no_match' ? 'none' : 'open';

async function statsView(el) {
  crumbs([['SkyTrace', '#/skytrace'], ['Statistik']]);
  let year = '', data = null, sig = '', wasRunning = false;
  const month = s => new Date(s + '-01T12:00:00Z').toLocaleDateString('de-DE', { month: 'short', year: 'numeric', timeZone: 'UTC' });
  const date = s => s ? new Date(s + 'T12:00:00Z').toLocaleDateString('de-DE', { timeZone: 'UTC' }) : 'Nacht unbekannt';
  const pct = (a, b) => b ? (100 * a / b).toLocaleString('de-DE', { maximumFractionDigits: 1 }) + ' %' : '—';
  el.innerHTML = `<div class="pagehead"><div><h1>SkyTrace-Statistik</h1><p class="mut">Erkannte Spuren, Satellitenzuordnungen und Katalogabgleiche über alle Analysen.</p></div>
    <span class="grow"></span><select id="sts-year" aria-label="Beobachtungsjahr"><option value="">Alle Jahre</option></select></div><div id="sts-body"></div>`;
  const body = $('#sts-body', el), select = $('#sts-year', el);
  select.onchange = () => { year = select.value; draw(); };
  function bars(rows) {
    const max = Math.max(1, ...rows.map(r => r.n));
    return `<div class="sts-bars">${rows.map(r => `<div class="sts-bar" title="${esc(r.title || '')}"><span>${esc(r.label)}</span>
      <div class="sts-track"><div class="sts-fill" style="width:${100 * r.n / max}%;${r.n ? '' : 'min-width:0'}"></div></div><span class="tnum">${nf.format(r.n)}</span></div>`).join('')}</div>`;
  }
  function draw() {
    if (!data) return;
    const ev = EV(), sessions = data.sessions.filter(s => !year || s.night?.startsWith(year));
    const keep = new Set(sessions.map(s => s.run + '|' + s.session));
    const cands = data.candidates.filter(c => keep.has(c.run + '|' + c.session)).map(c => ({ ...c, cls: ev ? ev.classification(c) : { kind: c.kind || 'trail' } }));
    const frames = sessions.reduce((a, s) => a + s.frames, 0), excluded = sessions.reduce((a, s) => a + s.excluded, 0);
    const sats = cands.filter(c => c.cls.kind === 'satellite');
    // Satelliten nach Bahn (NORAD) und Objektart
    const byNorad = new Map(), types = {};
    for (const c of sats) {
      const m = (c.catalogs.find(r => r.provider === 'satellites' && r.status === 'compatible')?.matches || [])[0];
      if (!m) continue;
      const k = m.norad_id ?? m.name, t = m.object_type || 'Satellit';
      types[t] = (types[t] || 0) + 1;
      const e = byNorad.get(k) || { name: m.name || 'NORAD ' + m.norad_id, norad: m.norad_id, type: t, n: 0 };
      e.n++; byNorad.set(k, e);
    }
    const topSats = [...byNorad.values()].sort((a, b) => b.n - a.n || a.name.localeCompare(b.name)).slice(0, 10);
    // Katalogabgleich je Quelle
    const cat = Object.keys(providerNames).map(p => {
      const parts = { hit: 0, hint: 0, none: 0, open: 0 }, reasons = {};
      for (const c of cands) for (const r of c.catalogs) if (r.provider === p) {
        const k = catPart(r.status); parts[k]++;
        if (k === 'open') reasons[statusNames[r.status] || r.status] = (reasons[statusNames[r.status] || r.status] || 0) + 1;
      }
      return { p, parts, total: Object.values(parts).reduce((a, b) => a + b, 0), reasons };
    }).filter(r => r.total);
    const hours = Array.from({ length: 24 }, () => 0), months = {};
    for (const c of cands) if (Number.isFinite(c.timestamp)) hours[new Date(c.timestamp * 1000).getUTCHours()]++;
    for (const s of sessions) if (s.night) { const m = (months[s.night.slice(0, 7)] ||= { n: 0, frames: 0, obs: 0 }); m.n += s.candidate_count; m.frames += s.frames; m.obs++; }
    const span = Object.keys(months).sort(), [y0, m0] = (year ? `${year}-01` : span[0] || '').split('-').map(Number);
    for (let y = y0, m = m0, end = year ? `${year}-12` : span.at(-1); y && `${y}-${String(m).padStart(2, '0')}` <= end; m === 12 ? (y++, m = 1) : m++)
      months[`${y}-${String(m).padStart(2, '0')}`] ||= { n: 0, frames: 0, obs: 0 };      // Monate ohne Beobachtung als Lücke zeigen
    const maxHour = Math.max(1, ...hours), timed = hours.reduce((a, b) => a + b, 0);
    const top = sessions.filter(s => s.candidate_count).sort((a, b) => b.candidate_count - a.candidate_count || b.frames - a.frames).slice(0, 8);
    const s = JSON.stringify([year, sessions.length, cands.length, data.repeated, frames]);
    if (s === sig) return;
    sig = s;
    body.innerHTML = `<div class="hstats sts-summary"><div><b class="tnum">${nf.format(sessions.length)}</b><small>Beobachtungen</small></div>
        <div><b class="tnum">${nf.format(frames)}</b><small>Aufnahmen analysiert</small></div><div><b class="tnum">${nf.format(cands.length)}</b><small>Kandidaten</small></div>
        <div><b class="tnum">${nf.format(sats.length)}</b><small>Satelliten zugeordnet</small></div></div>
      <p class="mut sm">${frames ? `${pct(excluded, frames + excluded)} der Aufnahmen waren unscharf und wurden ausgelassen · ${(1000 * cands.length / frames).toLocaleString('de-DE', { maximumFractionDigits: 1 })} Kandidaten je 1 000 Aufnahmen · ` : ''}
        ${cands.length ? `${pct(sats.length, cands.length)} der Spuren passen zu einer bekannten Satellitenbahn. ` : ''}
        Mehrfach analysierte Beobachtungen zählen nur einmal (neueste vollständige Analyse)${data.repeated ? ` – ${nf.format(data.repeated)} ältere Auswertung${data.repeated === 1 ? '' : 'en'} nicht mitgezählt` : ''}.
        Ein Kandidat ist kein bestätigtes Objekt, eine Katalogzuordnung bleibt ein geometrischer Hinweis.</p>
      ${sessions.length ? `<div class="sts-grid">
        <section class="panel"><h2>Kandidaten nach Objektart</h2>${bars(STAT_KINDS.map(([k, l]) => ({ label: l, n: cands.filter(c => c.cls.kind === k).length })))}</section>
        <section class="panel"><h2>Häufigste Satelliten</h2>${Object.keys(types).length ? `<div class="row sts-types">${Object.entries(types).sort((a, b) => b[1] - a[1]).map(([t, n]) => `<span class="chip">${esc(t)} · ${nf.format(n)}</span>`).join('')}</div>` : ''}
          ${topSats.length ? topSats.map(e => `<div class="sts-row"><b>${esc(e.name)}</b><span class="mut sm">${e.norad != null && !e.name.includes(String(e.norad)) ? 'NORAD ' + esc(e.norad) : ''}</span><span class="grow"></span><span class="tnum">${nf.format(e.n)}×</span></div>`).join('')
            : '<p class="mut sm">Noch keine Spur einer Satellitenbahn zugeordnet. Mit Standort und Plate Solving prüft SkyTrace historische Bahnen zur Aufnahmezeit.</p>'}</section>
        <section class="panel sts-wide"><h2>Katalogabgleich je Quelle</h2><div class="row sts-legend">${CAT_PARTS.map(([k, l]) => `<span><i class="sts-${k}"></i>${l}</span>`).join('')}</div>
          ${cat.length ? cat.map(r => `<div class="sts-cat"><span>${esc(providerNames[r.p])}</span><div class="sts-stack" title="${esc(Object.entries(r.reasons).map(([k, n]) => `${k}: ${n}`).join(' · ') || 'alle Kandidaten geprüft')}">
            ${CAT_PARTS.map(([k, l]) => r.parts[k] ? `<i class="sts-${k}" style="flex:${r.parts[k]}" title="${l}: ${r.parts[k]}"></i>` : '').join('')}</div>
            <span class="tnum sm">${nf.format(r.parts.hit)} / ${nf.format(r.total)}</span></div>`).join('') : '<p class="mut sm">Noch keine Katalogabgleiche gespeichert.</p>'}
          <p class="dim sm" style="margin:12px 0 0">„Nicht prüfbar“ heißt: Archivdaten, Zugang oder Standort fehlten – nicht, dass dort kein Objekt war. Gründe im Tooltip.</p></section>
        <section class="panel sts-wide"><h2>Uhrzeit der Kandidaten (UTC)</h2>${timed ? `<div class="sts-hours">${hours.map((n, h) => `<div class="sts-hour" title="${String(h).padStart(2, '0')}:00–${String(h).padStart(2, '0')}:59 UTC · ${nf.format(n)} Kandidaten"><i style="height:${100 * n / maxHour}%"></i><small>${h}</small></div>`).join('')}</div>`
          : '<p class="mut sm">Keine Kandidaten mit Aufnahmezeit.</p>'}</section>
        <section class="panel"><h2>Kandidaten je Monat</h2>${Object.keys(months).length ? bars(Object.entries(months).sort().map(([k, m]) => ({ label: month(k), n: m.n, title: `${m.obs} Beobachtung${m.obs === 1 ? '' : 'en'} · ${nf.format(m.frames)} Aufnahmen` })))
          : '<p class="mut sm">Keine Beobachtungsnächte bekannt.</p>'}</section>
        <section class="panel"><h2>Ergiebigste Beobachtungen</h2>${top.length ? top.map(x => `<a class="sts-row" href="#/skytrace/${encodeURIComponent(x.run)}/kandidaten"><span class="sts-name"><b>${esc(shortName(x.session))}</b><small class="mut">${esc(date(x.night))} · ${nf.format(x.frames)} Aufnahmen</small></span>
            <span class="grow"></span><span class="tnum">${nf.format(x.candidate_count)}</span>${icon('arrow')}</a>`).join('') : '<p class="mut sm">Noch keine Kandidaten.</p>'}</section></div>`
        : `<div class="empty">${icon('orbit')}<h2>${data.sessions.length ? 'Keine Beobachtungen in diesem Jahr' : 'Noch keine Analysen'}</h2>${data.sessions.length ? '' : 'Sobald Bildfolgen analysiert sind, entsteht hier die Statistik.'}</div>`}`;
  }
  async function load() {
    try { data = await api(T + '/statistics'); }
    catch (e) { if (el.isConnected) body.innerHTML = `<div class="panel">${esc(e.message)}</div>`; return; }
    if (!el.isConnected) return;
    const years = [...new Set(data.sessions.map(s => s.night?.slice(0, 4)).filter(Boolean))].sort().reverse();
    select.innerHTML = '<option value="">Alle Jahre</option>' + years.map(y => `<option>${y}</option>`).join('');
    if (!years.includes(year)) year = '';
    select.value = year;
    sig = '';
    draw();
  }
  await load();
  return {
    async update(st) {            // nach einer fertigen Analyse neu zählen
      const r = !!st.modules.skytrace?.running;
      if (wasRunning && !r) await load();
      wasRunning = r;
    },
  };
}

// ------------------------------------------------------------ 3D-Ansicht --- //
// Zentrale Ansicht: Lauf wählen (zuletzt gewählter bleibt), darunter dieselbe Bühne wie im Reiter „3D“ eines Laufs.
// Eine gemeinsame Szene über alle Läufe gibt es nicht – jede Beobachtung hat eigene Zeit, eigenen Standort und Teleskopweg.
async function sky3dView(el) {
  crumbs([['SkyTrace', '#/skytrace'], ['3D-Ansicht']]);
  const runs = await api(T + '/runs');
  let runId = runs.some(r => r.id === store.get('skytrace.3drun')) ? store.get('skytrace.3drun') : runs[0]?.id, view = null;
  const label = (r) => ({ id: r.id, name: shortName(r.title), info: `${runDate(r.id) || ago(r.modified)} · ${nf.format(r.frames)} Aufnahmen · ${r.candidates} Kandidat${r.candidates === 1 ? '' : 'en'}` });
  el.innerHTML = `<div class="pagehead"><div style="min-width:0"><h1>3D-Ansicht</h1>
      <p class="mut">Erde zur Aufnahmezeit, Weg und Blickfeld des Teleskops über die Nacht und alle zugeordneten Bahnen einer Beobachtung.</p></div>
      ${runs.length ? '<div class="page-actions"><a class="btn ghost" id="st3dOpen" href="#">Zur Analyse</a></div>' : ''}</div>
    ${runs.length ? '<div id="st3dHost"></div>' : `<div class="empty"><h2>Noch keine Analysen</h2><p class="mut">Die 3D-Ansicht zeigt fertige SkyTrace-Läufe.</p><a class="btn" href="#/skytrace/neu">${icon('play')}Neue Analyse</a></div>`}`;
  if (!runs.length) return;
  // Analysenliste: bleibt beim Wechsel erhalten (wird nach jedem mount wieder oben in die Seitenleiste gesetzt)
  const variant = runVariants(runs);
  let onlyHits = store.get('skytrace.3dhits', runs.some(r => r.candidates)), q = '';
  const box = document.createElement('section');
  box.className = 'st3d-runs';
  box.innerHTML = `<h3 class="st3d-sec">Analysen</h3>
    <input type="search" class="st3d-rq" placeholder="Analyse suchen …" aria-label="Analyse suchen">
    <div class="seg st3d-rf" aria-label="Filter"><button data-v="hits">Mit Kandidaten</button><button data-v="all">Alle</button></div>
    <div class="st3d-rl" role="listbox" aria-label="Analysen"></div>`;
  const drawRuns = () => {
    $$('.st3d-rf button', box).forEach(b => b.classList.toggle('on', (b.dataset.v === 'hits') === onlyHits));
    const words = q.toLowerCase().split(/\s+/).filter(Boolean);
    const shown = runs.filter(r => (!onlyHits || r.candidates || r.id === runId) && words.every(w => (shortName(r.title) + ' ' + (runDate(r.id) || '')).toLowerCase().includes(w)));
    $('.st3d-rl', box).innerHTML = shown.map(r => `<button role="option" data-run="${esc(r.id)}" class="${r.id === runId ? 'on' : ''}" aria-selected="${r.id === runId}">
        <span class="st3d-rt">${r.preview ? `<img src="${media(r.preview)}" alt="" loading="lazy">` : icon('orbit')}</span>
        <span class="st3d-rn"><b>${esc(shortName(r.title))}</b><small>${esc(runDate(r.id) || ago(r.modified))}${variant(r).replace(/<[^>]+>/g, '').replace(/^ /, ' · ')}</small></span>
        ${r.candidates ? `<span class="chip acc">${r.candidates}</span>` : ''}</button>`).join('') || '<p class="mut sm">Keine Analyse passt.</p>';
  };
  $('.st3d-rq', box).oninput = (e) => { q = e.target.value; drawRuns(); };
  $('.st3d-rf', box).onclick = (e) => { const b = e.target.closest('button[data-v]'); if (!b) return; onlyHits = b.dataset.v === 'hits'; store.set('skytrace.3dhits', onlyHits); drawRuns(); };
  $('.st3d-rl', box).onclick = (e) => { const b = e.target.closest('[data-run]'); if (!b || b.dataset.run === runId) return; runId = b.dataset.run; store.set('skytrace.3drun', runId); show(); };
  const show = () => {
    view?.dispose();
    const r = runs.find(x => x.id === runId);
    crumbs([['SkyTrace', '#/skytrace'], ['3D-Ansicht', '#/skytrace/3d'], [shortName(r?.title || runId)]]);
    $('#st3dOpen', el).href = `#/skytrace/${encodeURIComponent(runId)}/kandidaten`;
    view = SkyTrace3D.mount($('#st3dHost', el), runId, { onCandidate: (key) => { focusNext = key; location.hash = `#/skytrace/${encodeURIComponent(runId)}/kandidaten`; } });
    $('#st3dHost .st3d-side', el).prepend(box);
    $('#st3dHost .st3d-side', el).classList.remove('empty');
    drawRuns();
    box.querySelector('.st3d-rl .on')?.scrollIntoView({ block: 'nearest' });
  };
  show();
  const onKey = (e) => {
    if (e.ctrlKey || e.metaKey || e.altKey || e.target.closest('input,select,textarea')) return;
    if (e.key === 'f' || e.key === 'F') { view?.fullscreen(); e.preventDefault(); }
  };
  document.addEventListener('keydown', onKey);
  return { destroy: () => { view?.dispose(); document.removeEventListener('keydown', onKey); } };
}

// ---------------------------------------------------------- Registrierung --- //
AF.register('skytrace', {
  nav: [{ href: '#/skytrace', label: 'SkyTrace', icon: 'orbit', key: 'skytrace', area: 'work' }, { href: '#/skytrace/neu', label: 'Neue Analyse', icon: 'play', key: 'skytrace-new' },
    { href: '#/skytrace/3d', label: '3D-Ansicht', icon: 'globe', key: 'skytrace-3d' }, { href: '#/skytrace/statistik', label: 'Statistik', icon: 'pulse', key: 'skytrace-stats' }],
  routes: [
    [/^\/skytrace$/, 'skytrace', runsView],
    [/^\/skytrace\/neu$/, 'skytrace-new', newView],
    [/^\/skytrace\/statistik$/, 'skytrace-stats', statsView],
    [/^\/skytrace\/3d$/, 'skytrace-3d', sky3dView],
    [/^\/skytrace\/([^/]+)(?:\/(\w+))?$/, 'skytrace', runView],
  ],
  settings,
  status: (st, badge) => badge('skytrace', st.modules.skytrace?.running ? '●' : 0, true),
  async summary(st) {
    const s = st.modules.skytrace || {}, runs = await api(T + '/runs');
    return { title: 'SkyTrace', icon: 'orbit', href: '#/skytrace', sub: runs[0] ? `Zuletzt: ${shortName(runs[0].title)} · ${runDate(runs[0].id) || ago(runs[0].modified)}` : 'Noch keine Analysen',
      stats: [[nf.format(runs.length), 'Analysen'], [nf.format(runs.reduce((a, r) => a + r.candidates, 0)), 'Kandidaten'], [nf.format(runs.reduce((a, r) => a + r.frames, 0)), 'Aufnahmen']],
      live: s.running ? `<span class="spinner"></span>Analyse läuft · ${esc(s.text || '')}` : !s.engine?.ok && s.engine?.checked ? '<span class="warn">Analyse-Engine unvollständig</span>' : '' };
  },
  async discoveries() {
    return (await api(T + '/runs')).filter(r => r.candidates).slice(0, 4).map(r => ({ img: r.preview ? media(r.preview) : null, icon: 'orbit', kicker: 'SkyTrace · Kandidaten',
      title: shortName(r.title), sub: `${r.candidates} Kandidat${r.candidates === 1 ? '' : 'en'} · ${runDate(r.id) || ago(r.modified)}`, href: `#/skytrace/${encodeURIComponent(r.id)}/kandidaten`, t: r.modified }));
  },
  async palette() {
    const runs = await api(T + '/runs');
    return [['play', 'Neue Spurenanalyse', 'SkyTrace', '#/skytrace/neu'], ['globe', 'SkyTrace 3D-Ansicht', 'Teleskopweg und Satellitenbahnen auf der Erde', '#/skytrace/3d'], ['pulse', 'SkyTrace-Statistik', 'Objektarten, Satelliten, Kataloge', '#/skytrace/statistik'],
      ...runs.map(r => ['orbit', shortName(r.title), `${r.candidates} Kandidaten · ${runDate(r.id)}`, `#/skytrace/${encodeURIComponent(r.id)}`, r.preview ? media(r.preview) : null])];
  },
});
})();
