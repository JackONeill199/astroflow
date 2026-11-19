/* Einrichtung (#/willkommen): Datenordner, Teleskop, Standort, ASTAP auf Knopfdruck, Demo fürs erste Bild, Extras.
   Erscheint beim ersten Start der installierten Version von selbst (AF.app.setup.pending), sonst unter Einstellungen → Module & System.
   Backend: core/setup.py (/api/setup…, /api/system/…). */
(() => {
  const TELESCOPES = [['stellina', 'Stellina'], ['vespera', 'Vespera'], ['seestar', 'Seestar'], ['dwarf', 'Dwarf'], ['andere', 'Andere Kamera']];
  const dbFor = (list) => list.includes('andere') ? 'd50' : 'd05';   // Gesamtempfehlung (wie core/setup.py → recommended_db)
  const sizeOf = (b) => b == null ? '–' : b >= 1e12 ? (b / 1e12).toFixed(1).replace('.', ',') + ' TB' : Math.round(b / 1e9) + ' GB';

  async function waitForServer(msgEl) {
    const t0 = Date.now();
    await new Promise(r => setTimeout(r, 2500));
    while (Date.now() - t0 < 90000) {
      try { const r = await fetch('/api/status', { cache: 'no-store' }); if (r.ok) { location.reload(); return; } } catch { /* startet noch */ }
      if (msgEl) msgEl.textContent = `AstroFlow startet neu … ${Math.round((Date.now() - t0) / 1000)} s`;
      await new Promise(r => setTimeout(r, 1000));
    }
    if (msgEl) msgEl.textContent = 'AstroFlow antwortet nicht. Bitte über das Startmenü neu starten.';
  }

  function restartOverlay(text) {
    AF.restarting = true;                                   // kein „AstroFlow ist beendet“ während des Neustarts
    const d = $('#dlg');
    d.innerHTML = `<h2 style="font-size:1rem;margin-bottom:0.75rem">${esc(text)}</h2><div class="progress indet"><i></i></div><p class="mut sm" id="suWait" style="margin-top:0.75rem">Einen Moment …</p>`;
    d.showModal();
    waitForServer($('#suWait', d));
  }

  function taskLine(t) {
    if (!t) return '';
    if (t.state === 'running') return `<div class="su-task"><div class="progress"><i style="width:${Math.round((t.progress || 0) * 100)}%"></i></div><span class="mut sm">${esc(t.detail || '')}</span></div>`;
    if (t.state === 'error') return `<p class="bad sm su-task">${esc(t.detail || 'Fehlgeschlagen.')}</p>`;
    return `<p class="ok sm su-task">${icon('check')} ${esc(t.detail || 'Fertig.')}</p>`;
  }


  // Optionale Komponenten (core/components.py): Zustand, Installieren, Reparieren, Entfernen – Assistent und Einstellungen
  const STATE = { installed: ['ok', 'installiert'], partial: ['warn', 'unvollständig'], missing: ['', 'fehlt'], unsupported: ['', 'nicht verfügbar'], error: ['warn', 'Fehler'] };
  const fsize = (mb) => mb >= 1000 ? (mb / 1000).toFixed(1).replace('.', ',') + ' GB' : mb + ' MB';
  function componentsPanel(host, { skip = [] } = {}) {
    let timer = null, data = null;
    host.className = 'cmp-list';
    host.innerHTML = '<div class="skel" style="height:8rem"></div>';
    const row = (c) => {
      const t = c.task, busy = t?.state === 'running';
      const queued = c.pending && c.state !== 'installed' && !busy && !(t?.state === 'error') && data.queue_active;
      const failed = c.pending && c.state !== 'installed' && t?.state === 'error';
      let [kind, label] = STATE[c.state] || ['', c.state];
      if (busy) [kind, label] = ['acc', 'wird installiert'];
      else if (queued) [kind, label] = ['acc', c.queue_pos === 1 ? 'wartet – als Nächstes' : `wartet – Platz ${c.queue_pos}`];
      const btns = c.id === 'starnet'
        ? `<a class="btn ghost sm" href="#/settings/stacking" data-local-only>ZIP einspielen</a>`
        : c.state === 'unsupported' || c.can_install === false || queued ? ''
        : `${c.state === 'installed' ? `<button class="btn ghost sm" data-cmp="${c.id}" data-force="1" data-local-only ${busy ? 'disabled' : ''}>Reparieren</button>`
            : `<button class="btn sm" data-cmp="${c.id}" data-local-only ${busy ? 'disabled' : ''}>${icon('down')}${failed ? 'Erneut versuchen' : 'Installieren'}</button>`}
           ${c.can_remove ? `<button class="btn ghost sm" data-rm="${c.id}" data-local-only ${busy ? 'disabled' : ''}>Entfernen</button>` : ''}`;
      return `<div class="cmp-row" data-id="${c.id}"><div class="cmp-main"><b>${esc(c.title)}</b> <span class="chip ${kind}">${label}</span>
          ${c.version ? `<span class="mut sm">${esc(c.version)}</span>` : ''}${c.pending && c.state !== 'installed' ? ' <span class="chip acc">vom Installer vorgemerkt</span>' : ''}
          <div class="mut sm">${esc(c.what)}${c.size_mb ? ` · ~${fsize(c.size_mb)}` : ''}</div>
          <div class="dim sm">${esc(c.license)} · <a href="${esc(c.source)}" target="_blank" rel="noopener">${c.id === 'graxpert' ? 'Quellcode' : 'Quelle'}</a></div>
          ${queued ? `<div class="mut sm cmp-detail">Wird automatisch installiert, sobald die Komponenten davor fertig sind – du musst nichts anklicken.</div>` : ''}
          ${c.detail && !queued ? `<div class="dim sm cmp-detail">${esc(c.detail)}</div>` : ''}${taskLine(t)}</div>
        <div class="cmp-acts">${btns}</div></div>`;
    };
    const paint = () => { host.innerHTML = data.items.filter(c => !skip.includes(c.id)).map(row).join(''); };
    const stop = () => { clearInterval(timer); timer = null; };
    const watch = () => { if (!timer) timer = setInterval(load, 1500); };
    const busy = () => data && (data.items.some(c => c.task?.state === 'running') || data.pending.length);
    async function load() {
      if (data && !host.isConnected) return stop();
      try { data = await api('/components'); } catch (e) { host.innerHTML = `<p class="bad sm">${esc(e.message)}</p>`; return stop(); }
      paint();
      if (busy()) watch(); else stop();
    }
    host.onclick = async (e) => {
      const b = e.target.closest('button[data-cmp], button[data-rm]');
      if (!b) return;
      try {
        if (b.dataset.rm) {
          if (!await confirmDlg('Komponente entfernen?', `${esc(data.items.find(c => c.id === b.dataset.rm)?.title || '')} wird gelöscht. Deine Bilder und Einstellungen bleiben.`, 'Entfernen', true)) return;
          const r = await send('/components/remove', { id: b.dataset.rm });
          toast(r.detail, 'ok');
        } else {
          await send('/components/install', { id: b.dataset.cmp, option: b.dataset.cmp === 'astap' ? data.telescope_db : null, force: !!b.dataset.force });
        }
        await load();
      } catch (err) { toast(err.message, 'err'); }
    };
    host._stop = stop;
    load();
    return { reload: load, stop };
  }

  async function setupView(el) {
    crumbs([['Einrichtung']]);
    try { sessionStorage.removeItem('af.fromSetup'); } catch { /* nur Komfort */ }
    let s = await api('/setup'), sites = await api('/sites?nights=false').catch(() => ({ list: [] }));
    let timer = null, db = null, saveRev = 0, telSave = Promise.resolve(), pendingSaves = 0;
    const busyNow = () => Object.values(s.tasks || {}).some(t => t.state === 'running') || !!s.components?.queue_active;
    const paint = () => {
      const tel = s.telescopes || [], a = s.astap, t = s.tasks || {};
      db = db || dbFor(tel);
      const astapOk = a.path && a.databases.length;
      const bg = busyNow();
      const cloud = s.data.cloud ? `<div class="notice"><div><b>Liegt in ${esc(s.data.cloud)}</b><div class="mut sm">Stacks und Zwischenstände sind schnell viele GB und würden ständig synchronisiert. Besser einen Ordner außerhalb wählen.</div></div></div>` : '';
      // Erledigt-Stand je Schritt (Server merkt ihn sich: Teleskop, Orte, ASTAP, fertige Bilder)
      const doneSteps = { tel: tel.length > 0, site: !!sites.list?.length, astap: !!astapOk, pic: (s.pictures || 0) > 0 };
      const nDone = 1 + Object.values(doneSteps).filter(Boolean).length;
      const mark = (ok) => ok ? ` <span class="chip ok su-ok">${icon('check')}erledigt</span>` : '';
      const otherDrives = (s.drives || []).filter(d => !String(s.data.path).toLowerCase().startsWith(String(d.path).toLowerCase()));
      const low = s.data.free != null && s.data.free < 20e9 ? `<p class="warn sm">Auf diesem Laufwerk sind nur ${sizeOf(s.data.free)} frei – für Stacks besser 20 GB oder mehr.</p>` : '';
      el.innerHTML = `<div class="pagehead"><div><h1>Einrichtung</h1><p class="mut">Ein paar Angaben, dann verarbeitet AstroFlow deine Aufnahmen automatisch. Alles lässt sich später unter Einstellungen ändern.</p></div></div>
      <p class="su-progress sm" aria-live="polite"><b>${nDone} von 5</b> Schritten erledigt${nDone === 5 ? ' – alles bereit.' : ' · was erledigt ist, merkt sich AstroFlow.'}</p>
      <ol class="su-steps">
        <li class="panel su-step done"><h2>Datenordner${mark(true)}</h2>
          <p class="mut sm">Hier liegen Datenbank, Stacks und fertige Bilder. Deine Rohaufnahmen bleiben, wo sie sind.</p>
          <div class="su-path"><code>${esc(s.data.path)}</code><span class="chip">${sizeOf(s.data.free)} frei</span></div>
          ${cloud}${low}
          ${s.launched ? `${otherDrives.length ? `<div class="su-drives"><span class="mut sm">Weitere Laufwerke:</span>${otherDrives.map(d => `<span class="chip">${esc(d.path)} ${sizeOf(d.free)} frei</span>`).join('')}</div>` : ''}
            <div class="su-acts"><button class="btn ghost sm" id="suDir" data-local-only>${icon('folder')}Anderen Ordner wählen …</button>
            ${s.data.path !== s.default_data ? `<button class="linkbtn sm" id="suDirDef" data-local-only>Standard (${esc(s.default_data)})</button>` : ''}</div>`
          : `<p class="mut sm">Bei dieser Instanz fest vorgegeben (<code>--data</code> bzw. <code>ASTROFLOW_DATA</code>).</p>`}
        </li>
        <li class="panel su-step${doneSteps.tel ? ' done' : ''}"><h2>Teleskop${mark(doneSteps.tel)}</h2>
          <p class="mut sm">Bestimmt, welche Sterndatenbank fürs Plate-Solving passt. Mehrere Geräte sind möglich. Die Aufnahmen selbst erkennt AstroFlow am Dateikopf.</p>
          <div class="seg" id="suTel">${TELESCOPES.map(([v, l]) => `<button data-v="${v}" aria-pressed="${tel.includes(v)}" class="${tel.includes(v) ? 'on' : ''}">${l}</button>`).join('')}</div>
          ${tel.length ? `<p class="mut sm su-why">Empfehlung: Sterndatenbank ${dbFor(tel).toUpperCase()}${tel.length > 1 ? ' (für alle gewählten Geräte zusammen)' : ''}.</p>` : ''}
        </li>
        <li class="panel su-step${doneSteps.site ? ' done' : ''}"><h2>Standort${mark(doneSteps.site)}</h2>
          ${sites.list?.length ? `<p class="sm">${sites.list.length === 1 ? `Ort „${esc(sites.list[0].name)}“ eingetragen.` : `${sites.list.length} Orte eingetragen.`}</p>
            <div class="su-acts"><a class="btn ghost sm" href="#/settings/standorte" data-from-setup>${icon('pin')}Orte bearbeiten</a></div>`
          : `<p class="mut sm">Für „Heute Nacht“, die Himmelsqualität und den Bild-Tutor. Der Ort genügt (z. B. „Sonneberg“), er bleibt auf diesem Rechner.</p>
            <form class="su-site" id="suSite" novalidate><input id="suSiteQ" placeholder="Ort oder Adresse, z. B. Sonneberg" aria-label="Ort oder Adresse suchen" maxlength="200">
              <button class="btn sm" type="submit">Suchen</button></form>
            <div class="addr su-site-res" id="suSiteRes"></div>
            <p class="dim sm su-lic">Adresssuche: Photon/komoot · © OpenStreetMap (nur der Suchtext wird gesendet), Höhe: Open-Meteo.
              Genaue Koordinaten oder mehrere Orte: <a href="#/settings/standorte" data-from-setup>Einstellungen → Standorte</a>.</p>`}
        </li>
        <li class="panel su-step${doneSteps.astap ? ' done' : ''}"><h2>Plate-Solving (ASTAP)${mark(doneSteps.astap)}</h2>
          <p class="mut sm">Bestimmt, welcher Himmelsausschnitt auf dem Bild ist – für Objektnamen, Farbkalibrierung an Gaia-Sternen und die Tiefenanalyse. Ohne ASTAP läuft alles andere trotzdem.</p>
          ${astapOk ? `<p class="sm">${icon('check')} Gefunden: <code>${esc(a.path)}</code> · Sterndatenbank ${esc(a.databases.join(', '))}</p>`
          : !a.supported ? `<p class="sm">Für dieses System bitte ASTAP selbst installieren: <a href="https://www.hnsky.org/astap.htm" target="_blank" rel="noopener">hnsky.org/astap.htm</a></p>`
          : `<div class="seg" id="suDb">${Object.entries(s.dbs).map(([k, mb]) => `<button data-v="${k}" class="${k === db ? 'on' : ''}">${k.toUpperCase()} · ~${mb} MB</button>`).join('')}</div>
            <p class="mut sm su-why">D05 reicht für Smart-Teleskope (Bildfeld ab 0,6°), D50 für kleinere Bildfelder klassischer Teleskope.</p>
            <div class="su-acts"><button class="btn sm" id="suAstap" data-local-only ${t.astap?.state === 'running' ? 'disabled' : ''}>${icon('down')}Herunterladen und einrichten</button></div>
            <p class="mut sm su-lic">Quelle: ASTAP von Han Kleijn (<a href="https://www.hnsky.org/astap.htm" target="_blank" rel="noopener">hnsky.org</a>), geladen direkt von SourceForge nach <code>${esc(a.target)}</code>.
              Der Objektkatalog <code>deep_sky.csv</code> ist nur für nicht-kommerzielle Nutzung frei.</p>`}
          ${taskLine(t.astap)}
        </li>
        <li class="panel su-step${doneSteps.pic ? ' done' : ''}"><h2>Erstes Bild${mark(doneSteps.pic)}</h2>
          ${doneSteps.pic ? `<p class="sm">${s.pictures === 1 ? 'Dein erstes Bild ist fertig.' : `${s.pictures} Bilder sind fertig.`} Weitere Aufnahmen ziehst du einfach auf die Seite oder importierst sie unter Import.</p>
          <div class="su-acts"><a class="btn sm" href="#/bilder">${icon('arrow')}Zu den Bildern</a><a class="btn ghost sm" href="#/import">${icon('folder')}Weitere Aufnahmen importieren</a></div>` : `
          <p class="mut sm">Noch keine eigenen Aufnahmen zur Hand? Die Demo erzeugt eine künstliche Aufnahmeserie (30 Bilder, Darks, Flats) und verarbeitet sie wie echte.
            Eigene Aufnahmen ziehst du einfach auf die Seite oder wählst unter Import → „Hauptordner durchsuchen“ die Teleskop-Festplatte.</p>
          <div class="su-acts">${s.demo.exists ? `<a class="btn ghost sm" href="#/bilder">${icon('arrow')}Zu den Bildern</a>`
            : `<button class="btn sm" id="suDemo" ${t.demo?.state === 'running' ? 'disabled' : ''}>${icon('play')}Demo verarbeiten</button>`}
            <a class="btn ghost sm" href="#/import">${icon('folder')}Eigene Aufnahmen importieren</a></div>`}
          ${taskLine(t.demo)}
        </li>
        <li class="panel su-step"><h2>Extras <span class="mut sm">(optional)</span></h2>
          <div id="suCompSlot"></div>
          <dl class="su-extras">
            ${s.launched ? `<dt>Heimnetz</dt><dd><label class="modrow su-row" data-local-only><div>Vom Handy oder Tablet im eigenen WLAN erreichbar. Jedes Gerät wird einmal per QR-Code gekoppelt (Einstellungen → Heimnetz).</div>
              <input type="checkbox" id="suLan" ${s.lan ? 'checked' : ''}><span class="switch"></span></label>
              ${s.lan && (s.platform === 'Windows' || s.platform === 'Linux') ? `<button class="btn ghost sm" id="suFw" data-local-only>${s.platform === 'Windows' ? 'Windows-Firewall' : 'Firewall'} freigeben</button>` : ''}
              ${s.lan ? `<a class="btn ghost sm" href="#/settings/heimnetz">Gerät koppeln</a>` : ''}</dd>` : ''}
            ${s.launched && s.platform === 'Windows' ? `<dt>Im Hintergrund</dt><dd class="sm su-tray">AstroFlow läuft weiter, auch wenn du den Browser schließt. Das AstroFlow-Symbol unten rechts in der Taskleiste
              öffnet es wieder und beendet es. Windows versteckt neue Symbole oft hinter dem kleinen Pfeil <b>^</b> – dort nachsehen und das Symbol bei Bedarf in die Taskleiste ziehen.</dd>` : ''}
            <dt>Updates</dt><dd><label class="modrow su-row"><div>Einmal am Tag nach einer neuen Version fragen (eine anonyme Anfrage an GitHub).</div>
              <input type="checkbox" id="suUpd" ${s.update_check ? 'checked' : ''}><span class="switch"></span></label></dd>
          </dl>
        </li>
      </ol>
      <div class="su-foot"><p class="mut sm su-bg">${bg ? `${icon('down')} Downloads laufen im Hintergrund weiter – du kannst trotzdem abschließen. Fortschritt unter <a href="#/activity">Aufträge</a>.`
        : nDone === 5 ? 'Alles erledigt – neue Aufnahmen verarbeitet AstroFlow ab jetzt von selbst.' : `${nDone} von 5 erledigt. Fehlendes kannst du jederzeit unter Einstellungen nachholen.`}</p><button class="btn" id="suDone">${icon('check')}${s.done ? 'Fertig' : 'Einrichtung abschließen'}</button></div>`;
    };
    const comps = document.createElement('div');
    componentsPanel(comps, { skip: ['astap'] });
    // Neu zeichnen (alle 1,2 s, solange Downloads laufen): Suchtext, Treffer und Fokus der Ortssuche bleiben erhalten
    const repaint = () => {
      const inp = $('#suSiteQ', el), res = $('#suSiteRes', el);
      const q = inp?.value, focus = inp && document.activeElement === inp, sel = focus ? [inp.selectionStart, inp.selectionEnd] : null, html = res?.innerHTML;
      paint(); $('#suCompSlot', el)?.replaceWith(comps);
      const ni = $('#suSiteQ', el);
      if (ni && q != null) { ni.value = q; if (html) $('#suSiteRes', el).innerHTML = html; if (focus) { ni.focus(); ni.setSelectionRange(...sel); } }
    };
    const refresh = async () => {
      if (!el.isConnected) return stop();
      const rev = saveRev, fresh = await api('/setup').catch(() => s);
      if (rev !== saveRev || pendingSaves) return;                    // Wahl wurde währenddessen gespeichert: veraltete Antwort verwerfen
      s = fresh;
      repaint();
      if (!busyNow()) stop();
    };
    const stop = () => { clearInterval(timer); timer = null; };
    const watch = () => { if (!timer) timer = setInterval(refresh, 1200); };
    const changeDir = async (path) => {
      if (!await confirmDlg('Datenordner wechseln?', `AstroFlow startet dann mit dem Ordner <code>${esc(path)}</code> neu. Vorhandene Daten im alten Ordner bleiben dort.`, 'Wechseln')) return;
      try { const r = await send('/setup', { data_dir: path }); if (r.restart) restartOverlay('AstroFlow startet mit dem neuen Datenordner …'); }
      catch (e) { toast(e.message, 'err'); }
    };
    // Standort direkt hier: Suche → Treffer anklicken → als Standard-Ort speichern (Höhe von Open-Meteo, sonst 0 m)
    let siteRows = [];                                          // Treffer der letzten Suche (Knöpfe tragen den Index)
    el.onsubmit = async (e) => {
      if (e.target.id !== 'suSite') return;
      e.preventDefault();
      const q = $('#suSiteQ', el).value.trim(), res = $('#suSiteRes', el);
      if (q.length < 2) { toast('Bitte einen Ort oder eine Adresse eingeben.', 'err'); return; }
      res.innerHTML = '<span class="mut sm">Suche …</span>';
      try {
        siteRows = await api('/sites/geocode?q=' + encodeURIComponent(q));
        if (res.isConnected) res.innerHTML = siteRows.length ? `<span class="mut sm">Treffer anklicken:</span>${siteRows.map((r, i) => `<button type="button" class="chip" data-site="${i}">${esc(r.name)}</button>`).join('')}`
          : '<span class="mut sm">Kein Ergebnis gefunden – anders schreiben oder unter Einstellungen → Standorte Koordinaten eintragen.</span>';
      } catch (err) { if (res.isConnected) res.innerHTML = ''; toast(err.message, 'err'); }
    };
    const addSite = async (r) => {
      const res = $('#suSiteRes', el);
      if (res) res.innerHTML = '<span class="mut sm">Speichere …</span>';
      let alt = 0;
      try { alt = Math.round((await api(`/sites/elevation?lat=${+r.lat}&lon=${+r.lon}`)).alt_m) || 0; } catch { /* Höhe später nachtragen */ }
      const cur = await api('/sites?nights=false');
      const site = { name: String(r.name).split(',')[0].trim().slice(0, 80) || 'Standort', lat: +r.lat, lon: +r.lon, alt_m: alt, accuracy_m: r.accuracy_m || 2000, note: '' };
      sites = await send('/sites', { list: [...cur.list, site], default: cur.default, periods: cur.periods });
      toast(`Standort „${site.name}“ gespeichert${alt ? ` (Höhe ${alt} m)` : ''}.`, 'ok');
      repaint();
    };
    el.onclick = async (e) => {
      if (e.target.closest('a[data-from-setup]')) { try { sessionStorage.setItem('af.fromSetup', '1'); } catch { /* nur Komfort */ } return; }
      const b = e.target.closest('button');
      if (!b || b.closest('.cmp-list')) return;
      if (b.dataset.site != null) { try { await addSite(siteRows[+b.dataset.site]); } catch (err) { toast(err.message, 'err'); repaint(); } return; }
      try {
        if (b.id === 'suDir') folderDialog(s.data.path, (p) => changeDir(p), { title: 'Datenordner wählen', ok: 'Diesen Ordner verwenden', hint: 'Ein leerer oder neuer Ordner auf einem Laufwerk mit viel Platz – nicht in OneDrive/Dropbox.' });
        else if (b.id === 'suDirDef') changeDir(s.default_data);
        else if (b.closest('#suTel')) {
          // Auswahl sofort lokal ändern (mehrere schnelle Klicks), Speichern nacheinander
          const cur = s.telescopes || [], v = b.dataset.v;
          s = { ...s, telescopes: cur.includes(v) ? cur.filter(x => x !== v) : [...cur, v] };
          db = dbFor(s.telescopes); repaint();
          saveRev++;
          telSave = telSave.catch(() => {}).then(async () => {
            let saved = null;
            try { saved = await send('/setup', { telescopes: s.telescopes }); }
            finally {
              if (--pendingSaves === 0) {
                saveRev++;
                s = saved || await api('/setup').catch(() => s);       // bei Fehler: Serverstand übernehmen
                db = dbFor(s.telescopes || []); repaint();
              }
            }
          });
          pendingSaves++;
          await telSave;
        }
        else if (b.closest('#suDb')) { db = b.dataset.v; repaint(); }
        else if (b.id === 'suAstap') { await send('/components/install', { id: 'astap', option: db }); await refresh(); watch(); }
        else if (b.id === 'suDemo') { await send('/setup/demo'); await refresh(); watch(); }
        else if (b.id === 'suFw') { const r = await send('/setup/firewall'); toast(r.detail, r.ok ? 'ok' : 'err'); }
        else if (b.id === 'suDone') {
          const bg = busyNow();
          await send('/setup', { done: true });
          if (bg) toast('Downloads laufen im Hintergrund weiter – Fortschritt unter Aufträge.', 'ok', { label: 'Aufträge', href: '#/activity' }); location.hash = s.demo.exists || s.pictures ? '#/bilder' : '#/'; }
      } catch (err) { toast(err.message, 'err'); }
    };
    el.onchange = async (e) => {
      try {
        if (e.target.id === 'suUpd') { s = await send('/setup', { update_check: e.target.checked }); }
        else if (e.target.id === 'suLan') {
          const r = await send('/setup', { lan: e.target.checked });
          if (r.restart) restartOverlay(e.target.checked ? 'AstroFlow wird im Heimnetz erreichbar …' : 'AstroFlow ist danach nur noch auf diesem PC erreichbar …');
        }
      } catch (err) { toast(err.message, 'err'); }
    };
    repaint();
    if (busyNow()) watch();
    return { destroy: () => { stop(); comps._stop?.(); } };
  }

  // Stand der Update-Prüfung als kurze Zeile: neuere Version (Klick öffnet Details) · aktuell · nicht erreichbar · noch nie geprüft
  function updState(upd, info) {
    if (upd?.latest) return `<a href="#" data-upd>Version ${esc(upd.latest)} verfügbar – Details</a>`;
    if (info?.latest) return `<span class="mut">aktuell (geprüft ${ago(info.checked)})</span>`;
    if (info?.checked) return `<span class="mut">GitHub nicht erreichbar (versucht ${ago(info.checked)})</span>`;
    return '<span class="mut">noch nicht geprüft</span>';
  }

  // Einstellungen → Module & System: Einrichtung, Updates, Beenden
  async function setupSystemPanel(el) {
    const s = await api('/setup').catch(() => null);
    if (!s || !el.isConnected) return;
    const u = s.update || {};
    let offer = s.update_offer;                                // aktuelles Angebot – auch bei abgeschalteter Prüfung für „Details“
    if (s.update_check) AF.updateHint?.(offer);                // frischer Serverstand → Chip mitziehen
    const p = document.createElement('div');
    p.className = 'panel';
    p.innerHTML = `<h2>Einrichtung und Updates</h2>
      <dl class="kv" style="margin:0.75rem 0">
        <dt>Datenordner</dt><dd><code>${esc(s.data.path)}</code></dd>
        <dt>Version</dt><dd>${esc(s.version)} · <span id="sysUpdState">${updState(s.update_offer, u)}</span></dd>
      </dl>
      <label class="modrow"><div><b>Nach Updates suchen</b><div class="mut sm">Einmal am Tag eine anonyme Anfrage an GitHub, ob es eine neue Version gibt.</div></div>
        <input type="checkbox" id="sysUpd" ${s.update_check ? 'checked' : ''}><span class="switch"></span></label>
      <label class="modrow"><div><b>„Fehler melden“ anzeigen</b><div class="mut sm">${s.report_env_off ? 'Per <code>ASTROFLOW_REPORT=0</code> ausgeschaltet.' : 'Knopf oben rechts (Strg+Umschalt+F): stellt einen bereinigten Bericht zusammen und öffnet das GitHub-Formular. AstroFlow sendet selbst nichts.'}</div></div>
        <input type="checkbox" id="sysReport" ${s.report && !s.report_env_off ? 'checked' : ''} ${s.report_env_off ? 'disabled' : ''}><span class="switch"></span></label>
      <div class="su-acts" style="margin-top:0.875rem"><a class="btn ghost sm" href="#/willkommen">${icon('gear')}Einrichtungsassistent</a>
        <button class="btn ghost sm" id="sysCheck">Jetzt prüfen</button>
        ${s.launched ? `<button class="btn ghost sm" id="sysRestart" data-local-only>Neu starten</button>` : ''}
        <button class="btn danger sm" id="sysQuit" data-local-only>AstroFlow beenden</button></div>
      <p class="mut sm su-support" style="margin:0.875rem 0 0">AstroFlow ist für private Nutzung kostenlos, der Quellcode ist offen (PolyForm Noncommercial 1.0.0). Du möchtest die Entwicklung unterstützen? <a href="https://github.com/sponsors/JackONeill199" target="_blank" rel="noopener">GitHub Sponsors</a> · <a href="https://ko-fi.com/jackoneill199" target="_blank" rel="noopener">Ko-fi</a></p>`;
    el.append(p);
    const cp = document.createElement('div');
    cp.className = 'panel';
    cp.innerHTML = '<h2>Komponenten</h2><p class="mut sm" style="margin:0.375rem 0 0.75rem">Werkzeuge, die AstroFlow auf Wunsch lädt. Fehlt etwas, läuft AstroFlow trotzdem – nur die betroffene Funktion entfällt.</p><div></div>';
    el.append(cp);
    componentsPanel(cp.lastElementChild);
    p.onchange = async (e) => {
      if (e.target.id === 'sysUpd') { try { await send('/setup', { update_check: e.target.checked });
        if (e.target.checked) AF.app.updateManual = false;      // Hintergrundprüfung wieder an: neue Angebote zählen wieder (Review PR #112)
        toast('Gespeichert.', 'ok'); } catch (err) { toast(err.message, 'err'); } }
      if (e.target.id === 'sysReport') {
        try { await send('/setup', { report: e.target.checked }); AF.app.report = e.target.checked; $('#reportBtn').hidden = !e.target.checked; toast('Gespeichert.', 'ok'); }
        catch (err) { toast(err.message, 'err'); e.target.checked = !e.target.checked; }
      }
    };
    p.onclick = async (e) => {
      if (e.target.closest('[data-upd]')) { e.preventDefault(); AF.updatePanel?.(offer); return; }
      const b = e.target.closest('button');
      if (!b) return;
      try {
        if (b.id === 'sysCheck') {
          b.disabled = true; b.classList.add('busy');
          const checkOn = !!$('#sysUpd')?.checked;                    // vor dem await erfassen (Ansicht kann inzwischen weg sein)
          const st = $('#sysUpdState');
          if (st) st.innerHTML = '<span class="mut">wird geprüft …</span>';
          const r = await api('/system/update/check', { method: 'POST' }).finally(() => { b.disabled = false; b.classList.remove('busy'); });
          if (r.update) store.set('update.dismissed', '');          // bewusst geprüft → Hinweis wieder zeigen
          offer = r.update;
          AF.updateHint?.(r.update, !checkOn);          // Prüfung aus: Stand bleibt trotzdem beim Status-Poll erhalten
          if (st?.isConnected) st.innerHTML = updState(r.update, r);
        } else if (b.id === 'sysRestart') { await send('/system/restart'); restartOverlay('AstroFlow startet neu …'); }
        else if (b.id === 'sysQuit') {
          if (!await confirmDlg('AstroFlow beenden?', 'Laufende Aufträge setzen beim nächsten Start am letzten Zwischenstand fort. Neu starten über das Startmenü bzw. den Starter.', 'Beenden', true)) return;
          await send('/system/shutdown');
          AF.serverGone?.();
        }
      } catch (err) { toast(err.message, 'err'); }
    };
  }

  // Hilfe & erste Schritte (#/hilfe): Kurzanleitung, häufige Fragen, Doku-Links, Fehler melden – auch über die Suche (Strg+K) erreichbar
  const DOC = 'https://github.com/JackONeill199/astroflow/blob/main/';
  async function helpView(el) {
    crumbs([['Hilfe']]);
    // sofort zeichnen (kein Warten auf /api/setup – das ist unter Last langsam); den Datenordner danach einsetzen
    const win = /Windows/.test(navigator.userAgent), launched = !!AF.app.setup?.launched;
    const faq = [
      ['Wo ist AstroFlow, wenn ich den Browser schließe?', `AstroFlow läuft im Hintergrund weiter und verarbeitet neue Aufnahmen.${launched ? ` Zurück kommst du über das Startmenü, das Desktop-Symbol oder das AstroFlow-Symbol im Infobereich unten rechts${win ? ' – Windows versteckt es oft hinter dem kleinen Pfeil <b>^</b>' : ''}.` : ' Die Adresse dieser Seite bleibt gleich.'}`],
      ['Wie beende ich AstroFlow?', `${launched ? 'Rechtsklick auf das AstroFlow-Symbol im Infobereich → <b>Beenden</b>, oder ' : ''}<a href="#/settings/module">Einstellungen → Module &amp; System</a> → „AstroFlow beenden“. Laufende Aufträge und Downloads setzen beim nächsten Start fort.`],
      ['Werden meine Rohaufnahmen verändert?', 'Nein. AstroFlow liest sie nur. Hochgeladene Kopien (Ziehen auf die Seite) entfernt es nach dem Stacken, wenn das unter Einstellungen → Bilder eingeschaltet ist – die Originale auf dem Teleskop oder der Festplatte bleiben.'],
      ['Warum dauern die Extras nach der Installation so lange?', 'GraXpert (KI-Hintergrund und -Entrauschen, ~160 MB) und die SkyTrace-Analyse-Engine (~1,4 GB) laden im Hintergrund – je nach Leitung 10–20 Minuten. AstroFlow ist in der Zeit voll nutzbar; Bilder, die vor GraXpert fertig wurden, entwickelt es danach neu. Fortschritt unter <a href="#/activity">Aufträge</a>.'],
      ['Mein Bild hat keinen Objektnamen.', 'Dann fehlen Name und Koordinaten im Dateikopf. Mit ASTAP (Plate-Solving) bestimmt AstroFlow das Objekt aus dem Sternmuster – Objektseite → „Mehr“ → „Objekt per Plate-Solving bestimmen“.'],
      ['Wo liegen meine Daten?', `Im Datenordner <span id="hlData">(siehe Einstellungen → Module &amp; System)</span>: Datenbank, Stacks und fertige Bilder. Ändern lässt er sich in der <a href="#/willkommen">Einrichtung</a>.`],
    ];
    el.innerHTML = `<div class="pagehead"><div><h1>Hilfe</h1><p class="mut">Erste Schritte, häufige Fragen und die ausführliche Anleitung.</p></div></div>
      <div class="panel hl-panel"><h2>Erste Schritte</h2>
        <ol class="hl-steps">
          <li><b>Einrichtung</b> – Teleskop und Standort angeben. <a href="#/willkommen">Einrichtung öffnen</a></li>
          <li><b>Aufnahmen holen</b> – FITS-Dateien auf die Seite ziehen oder unter <a href="#/import">Import</a> den Ordner des Teleskops wählen.</li>
          <li><b>Warten</b> – AstroFlow sortiert, prüft, stackt und entwickelt von selbst. Den Fortschritt zeigt die Leiste oben rechts.</li>
          <li><b>Ansehen und teilen</b> – fertige Bilder unter <a href="#/bilder">Bilder</a>, als JPEG oder TIFF über den Knopf „Export“.</li>
        </ol>
        <p class="sm mut hl-keys">Suchen &amp; Befehle: <kbd>Strg</kbd> + <kbd>K</kbd> – findet Objekte, Ansichten und diese Hilfe.</p></div>
      <div class="panel hl-panel"><h2>Häufige Fragen</h2>
        <div class="hl-faq">${faq.map(([q, a]) => `<details><summary>${esc(q)}</summary><p class="sm">${a}</p></details>`).join('')}</div></div>
      <div class="panel hl-panel"><h2>Anleitung und Kontakt</h2>
        <div class="su-acts"><a class="btn ghost sm" href="${DOC}README.md" target="_blank" rel="noopener">${icon('book')}Anleitung (README)</a>
          <a class="btn ghost sm" href="${DOC}docs/module/bilder.md" target="_blank" rel="noopener">Bilder</a>
          <a class="btn ghost sm" href="${DOC}docs/module/skytrace.md" target="_blank" rel="noopener">SkyTrace</a>
          <a class="btn ghost sm" href="${DOC}docs/module/sage.md" target="_blank" rel="noopener">Sage</a>
          ${AF.app.report ? `<button class="btn ghost sm" data-report>${icon('bug')}Fehler melden</button>` : ''}</div>
        <p class="dim sm hl-ver">AstroFlow ${esc(AF.app.version || '')} · Die Links öffnen GitHub im Browser.</p></div>`;
    api('/setup').then(s => { const d = $('#hlData', el); if (d?.isConnected) d.innerHTML = `<code>${esc(s.data.path)}</code>`; }).catch(() => {});
  }

  window.setupView = setupView;
  window.helpView = helpView;
  window.restartOverlay = restartOverlay;
  window.setupSystemPanel = setupSystemPanel;
  window.componentsPanel = componentsPanel;
})();
