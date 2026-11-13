'use strict';
/* Fehler melden (core/report.py): Dialog → Server bereinigt (/api/report/compose) → Vorschau → vorbefülltes Issue-Formular
   auf GitHub (Link, öffnet der Nutzer selbst). Der Server sendet nichts nach außen. Screenshot: Bildschirmaufnahme des Browsers
   (getDisplayMedia) oder eigenes Bild, vorher Ortsnamen/Pfade/Karten maskiert, danach schwärzen – nur zum Speichern, nie hochgeladen.
   Wird vor app.js geladen, damit der Fehlerpuffer ab Seitenstart mitläuft; alle Helfer ($, esc, api, send, toast …) kommen aus app.js. */
const report = (() => {
  // ---- Puffer der letzten Browser-Fehler (nur im Speicher, geht nur über die Vorschau raus) ----
  const ERR = [];
  const push = (m) => { const s = String(m ?? '').slice(0, 400).trim(); if (s && ERR[ERR.length - 1] !== s) { ERR.push(s); if (ERR.length > 20) ERR.shift(); } };
  const where = (f, l) => f ? ` (${String(f).split('?')[0].split('/').pop()}:${l || 0})` : '';
  addEventListener('error', (e) => { if (e.message) push(e.message + where(e.filename, e.lineno)); });
  addEventListener('unhandledrejection', (e) => push('Unbehandelt: ' + (e.reason?.message || e.reason)));
  const ce = console.error.bind(console);
  console.error = (...a) => {
    try { push(a.map(x => x instanceof Error ? x.message : typeof x === 'string' ? x : JSON.stringify(x)).join(' ')); } catch { /* egal */ }
    ce(...a);
  };

  let dlg = null, info = null, shot = null, rects = [];
  const browser = () => {
    const ua = navigator.userAgent;
    for (const [n, rx] of [['Edge', /Edg\/(\d+)/], ['Opera', /OPR\/(\d+)/], ['Firefox', /Firefox\/(\d+)/], ['Chrome', /Chrome\/(\d+)/], ['Safari', /Version\/(\d+).*Safari/]]) {
      const m = ua.match(rx);
      if (m) return `${n} ${m[1]}`;
    }
    return 'unbekannt';
  };
  const areaOf = () => {
    const h = location.hash.replace(/^#\/?/, '');
    if (/^(settings|willkommen|einrichtung)/.test(h)) return 'setup';
    const mod = (AF.order || []).find(id => h.startsWith(id) || (AF.modules[id]?.nav || []).some(n => n.href && h && n.href.replace(/^#\/?/, '').split('/')[0] === h.split('/')[0]));
    return mod && ['stacking', 'skytrace', 'sage'].includes(mod) ? mod : 'ui';
  };

  // ---- Maskieren vor der Bildschirmaufnahme: Text mit Pfaden/Orten/Koordinaten → Balken, Karten/Globus abgedeckt ----
  function maskPage(words) {
    const parts = [String.raw`[A-Za-z]:[\\/][^\s"'<>|]*`, String.raw`(?:~|/home|/Users|/root|/mnt|/media|/Volumes)/[^\s"'<>]*`,
      String.raw`\b(?:\d{1,3}\.){3}\d{1,3}\b`, String.raw`[-+]?\d{1,3}[.,]\d{3,}\s*[,;/ ]\s*[-+]?\d{1,3}[.,]\d{3,}`,
      String.raw`\d{1,3}\s?°\s?\d{1,2}\s?['′]`, String.raw`[\w.+-]+@[\w-]+(?:\.[\w-]+)+`,
      ...words.map(w => w.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'))];
    const rx = new RegExp(parts.join('|'), 'gi');
    const undo = [];
    const bar = (v) => v.replace(rx, (m) => '█'.repeat(Math.min(m.length, 24)));
    const textNode = (n) => {
      if (n.parentElement?.closest('#reportDlg, script, style')) return;
      const v = n.nodeValue;
      rx.lastIndex = 0;
      if (v && rx.test(v)) { undo.push(() => { n.nodeValue = v; }); rx.lastIndex = 0; n.nodeValue = bar(v); }
    };
    const walk = (root) => {
      if (root.nodeType === Node.TEXT_NODE) return textNode(root);
      const tw = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
      const nodes = [];
      for (let n = tw.nextNode(); n; n = tw.nextNode()) nodes.push(n);
      nodes.forEach(textNode);
    };
    walk(document.body);
    // Ansichten zeichnen sich während der Browser-Auswahl evtl. neu (Polling) → Neues ebenfalls maskieren
    const mo = new MutationObserver((list) => { for (const m of list) m.type === 'characterData' ? (m.target.nodeValue.includes('█') || textNode(m.target)) : m.addedNodes.forEach(walk); });
    mo.observe(document.body, { childList: true, characterData: true, subtree: true });
    for (const el of $$('input:not([type=checkbox]):not([type=radio]):not([type=file]), textarea')) {
      if (el.closest('#reportDlg')) continue;
      const v = el.value;
      rx.lastIndex = 0;
      if (v && rx.test(v)) { undo.push(() => { el.value = v; }); rx.lastIndex = 0; el.value = bar(v); }
    }
    document.documentElement.classList.add('af-masking');
    return () => { mo.disconnect(); document.documentElement.classList.remove('af-masking'); undo.reverse().forEach(f => f()); };
  }

  const frame = () => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)));
  const canCapture = () => !!(window.isSecureContext && navigator.mediaDevices?.getDisplayMedia);

  async function capture() {
    let restore = null, stream = null;
    dlg.close();
    try {
      restore = maskPage(info?.mask || []);
      await frame();
      stream = await navigator.mediaDevices.getDisplayMedia({ video: { displaySurface: 'browser' }, audio: false,
        preferCurrentTab: true, selfBrowserSurface: 'include', surfaceSwitching: 'exclude' });
      const v = document.createElement('video');
      v.muted = true; v.playsInline = true; v.srcObject = stream;
      await v.play();
      await new Promise(r => setTimeout(r, 400));          // Hinweisleiste des Browsers verschwindet, Seite neu gezeichnet
      const c = document.createElement('canvas');
      c.width = v.videoWidth; c.height = v.videoHeight;
      c.getContext('2d').drawImage(v, 0, 0);
      setShot(c);
    } catch (e) {
      if (e?.name !== 'NotAllowedError' && e?.name !== 'AbortError') toast('Bildschirmaufnahme nicht möglich – du kannst stattdessen ein Bild auswählen.', 'err');
    } finally {
      stream?.getTracks().forEach(t => t.stop());
      restore?.();
      dlg.showModal();
    }
  }

  async function pickFile(file) {
    if (!file || !file.type.startsWith('image/')) return toast('Bitte eine Bilddatei wählen.', 'err');
    try {
      const bmp = await createImageBitmap(file);
      const c = document.createElement('canvas');
      c.width = bmp.width; c.height = bmp.height;
      c.getContext('2d').drawImage(bmp, 0, 0);
      setShot(c);
    } catch { toast('Das Bild ließ sich nicht öffnen.', 'err'); }
  }

  function setShot(c) { shot = c; rects = []; drawShot(); }

  // ---- Schwärzen: Rechtecke ziehen; gespeichert wird das neu gezeichnete Bild (ohne Metadaten) ----
  function drawShot() {
    const box = $('#repShot', dlg);
    if (!box) return;
    box.hidden = !shot;
    if (!shot) return;
    const cv = $('#repCanvas', dlg);
    cv.width = shot.width; cv.height = shot.height;
    const g = cv.getContext('2d');
    g.drawImage(shot, 0, 0);
    g.fillStyle = '#000';
    for (const r of rects) g.fillRect(r.x, r.y, r.w, r.h);
    $('#repUndo', dlg).disabled = !rects.length;
  }
  function bindCanvas() {
    const cv = $('#repCanvas', dlg);
    let start = null;
    const pt = (e) => { const b = cv.getBoundingClientRect(); return { x: (e.clientX - b.left) * cv.width / b.width, y: (e.clientY - b.top) * cv.height / b.height }; };
    const rectOf = (a, b) => ({ x: Math.min(a.x, b.x), y: Math.min(a.y, b.y), w: Math.abs(a.x - b.x), h: Math.abs(a.y - b.y) });
    cv.onpointerdown = (e) => { start = pt(e); cv.setPointerCapture(e.pointerId); e.preventDefault(); };
    cv.onpointermove = (e) => {
      if (!start) return;
      drawShot();
      const r = rectOf(start, pt(e)), g = cv.getContext('2d');
      g.fillStyle = 'rgba(0,0,0,.75)'; g.fillRect(r.x, r.y, r.w, r.h);
    };
    cv.onpointerup = (e) => { if (!start) return; const r = rectOf(start, pt(e)); start = null; if (r.w > 3 && r.h > 3) rects.push(r); drawShot(); };
    cv.onpointercancel = () => { start = null; drawShot(); };
  }
  function saveShot() {
    $('#repCanvas', dlg).toBlob((b) => {
      if (!b) return toast('Speichern nicht möglich.', 'err');
      const a = document.createElement('a');
      a.href = URL.createObjectURL(b);
      a.download = `astroflow-fehler-${new Date().toISOString().slice(0, 10)}.png`;
      document.body.append(a); a.click(); a.remove();
      setTimeout(() => URL.revokeObjectURL(a.href), 4000);
      toast('Screenshot gespeichert – bei Bedarf selbst ins GitHub-Formular ziehen.', 'ok');
    }, 'image/png');
  }

  async function copy(text) {
    try { await navigator.clipboard.writeText(text); return true; } catch { /* http im Heimnetz: kein Clipboard-API */ }
    const t = document.createElement('textarea');
    t.value = text; t.setAttribute('readonly', ''); t.style.position = 'fixed'; t.style.opacity = '0';
    dlg.append(t); t.select();
    let ok = false;
    try { ok = document.execCommand('copy'); } catch { ok = false; }
    t.remove();
    return ok;
  }

  // ---- Dialog ----
  function formHtml() {
    const areas = info?.areas || {};
    const cap = canCapture();
    return `<div class="rep-step" id="repForm">
      <div class="rep-h"><h2>Fehler melden</h2><button class="tbtn" type="button" data-close title="Schließen" aria-label="Schließen">${icon('x')}</button></div>
      <p class="mut sm rep-note">Öffnet ein vorausgefülltes Formular auf <b>GitHub</b>. Das Issue ist dort <b>öffentlich</b> und braucht ein
        (kostenloses) GitHub-Konto. AstroFlow sendet selbst nichts – du siehst vorher genau, was übernommen wird.</p>
      <label class="field"><span>Titel</span><input name="title" maxlength="150" autocomplete="off" placeholder="z. B. Stacken bricht bei Flats ab"></label>
      <label class="field"><span>Was ist passiert?</span><textarea name="actual" rows="4" maxlength="4000" placeholder="Fehlermeldung möglichst wörtlich"></textarea></label>
      <label class="field"><span>Was hast du erwartet?</span><textarea name="expected" rows="2" maxlength="2000"></textarea></label>
      <label class="field"><span>Schritte zum Nachstellen <small>(optional)</small></span><textarea name="steps" rows="3" maxlength="3000" placeholder="1. Bilder → Import → …"></textarea></label>
      <label class="field"><span>Bereich</span><select name="area">${Object.entries(areas).map(([k, v]) => `<option value="${esc(k)}"${k === areaOf() ? ' selected' : ''}>${esc(v)}</option>`).join('')}</select></label>
      <label class="modrow"><div><b>Auszug aus dem Protokoll anhängen</b><div class="mut sm">Letzte Zeilen von <code>server.log.err</code>, bereinigt – in der Vorschau sichtbar.</div></div>
        <input type="checkbox" name="log"><span class="switch"></span></label>
      <div class="rep-shotbar"><div><b>Screenshot</b> <span class="mut sm">optional · bleibt auf deinem Rechner</span></div>
        <div class="row">${cap ? `<button class="btn ghost sm" type="button" id="repCap">${icon('camera')}Bildschirm aufnehmen</button>` : ''}
          <label class="btn ghost sm" id="repPick">${icon('image')}Bild wählen<input type="file" accept="image/*" hidden></label></div></div>
      <div class="rep-shot" id="repShot" hidden>
        <p class="mut sm">Ortsnamen, Pfade und Karten sind vor der Aufnahme abgedeckt. Weitere Stellen mit der Maus/dem Finger <b>schwärzen</b> (Rechteck ziehen).</p>
        <canvas id="repCanvas" aria-label="Screenshot – Rechtecke ziehen zum Schwärzen"></canvas>
        <div class="row"><button class="btn ghost sm" type="button" id="repUndo">Rückgängig</button><button class="btn ghost sm" type="button" id="repDrop">Entfernen</button>
          <span class="grow"></span><button class="btn sm" type="button" id="repSave">${icon('down')}Screenshot speichern</button></div>
        <p class="mut sm">Der Screenshot wird <b>nicht</b> mitgeschickt. Gespeichert kannst du ihn selbst ins GitHub-Formular ziehen.</p>
      </div>
      <div class="row rep-acts"><button class="btn ghost" type="button" data-close>Abbrechen</button><button class="btn" type="button" id="repNext">Vorschau${icon('arrow')}</button></div>
    </div><div class="rep-step" id="repPrev" hidden></div>`;
  }

  function previewHtml(r) {
    const moved = r.preview.filter(p => !p.in_link).length;
    return `<div class="rep-h"><h2>Vorschau</h2><button class="tbtn" type="button" data-close title="Schließen" aria-label="Schließen">${icon('x')}</button></div>
      <p class="mut sm rep-note">Genau das landet im <b>öffentlichen</b> GitHub-Formular. Platzhalter wie <code>&lt;privat&gt;</code> oder <code>&lt;Pfad&gt;</code>
        hat AstroFlow eingesetzt. Auf GitHub kannst du alles noch ändern, bevor du absendest.</p>
      <div class="rep-prev">${r.preview.map(p => `<div><div class="rep-pl">${esc(p.label)}${p.in_link ? '' : ' <span class="chip warn">über Zwischenablage</span>'}</div><pre>${esc(p.value)}</pre></div>`).join('')}</div>
      ${moved ? '<p class="mut sm">Zu lang für den Link: markierte Teile kopiert AstroFlow beim Öffnen in die Zwischenablage – auf GitHub ins Feld einfügen.</p>' : ''}
      ${shot ? '<p class="mut sm">Den Screenshot hast du lokal – nicht vergessen, ihn ggf. zu speichern und selbst ins Formular zu ziehen.</p>' : ''}
      <div class="row rep-acts"><button class="btn ghost" type="button" id="repBack">${icon('back')}Zurück</button><span class="grow"></span>
        <button class="btn ghost" type="button" id="repCopy">Bericht kopieren</button>
        <a class="btn" id="repOpen" href="${esc(r.url)}" target="_blank" rel="noopener noreferrer">Auf GitHub öffnen${icon('arrow')}</a></div>`;
  }

  async function open() {
    if (!AF.app?.report) return;
    if (!dlg) {
      dlg = document.createElement('dialog');
      dlg.id = 'reportDlg';
      dlg.setAttribute('aria-label', 'Fehler melden');
      document.body.append(dlg);
    }
    if (dlg.open) return;
    try { info = await api('/report'); } catch (e) { return toast(e.message, 'err'); }
    if (!dlg.innerHTML) build();
    dlg.showModal();
    $('[name=title]', dlg).focus();
  }
  function reset() { if (dlg) dlg.innerHTML = ''; shot = null; rects = []; }

  function build() {
    dlg.innerHTML = formHtml();
    bindCanvas();
    let last = null;
    dlg.onclick = async (e) => {
      const b = e.target.closest('button, a');
      if (!b) return;
      if (b.hasAttribute('data-close')) dlg.close();
      else if (b.id === 'repCap') capture();
      else if (b.id === 'repUndo') { rects.pop(); drawShot(); }
      else if (b.id === 'repDrop') { shot = null; rects = []; drawShot(); }
      else if (b.id === 'repSave') saveShot();
      else if (b.id === 'repBack') { $('#repPrev', dlg).hidden = true; $('#repForm', dlg).hidden = false; }
      else if (b.id === 'repCopy') toast(await copy(last.clipboard) ? 'Bericht kopiert.' : 'Kopieren nicht möglich.', 'ok');
      else if (b.id === 'repOpen') {
        if (!String(b.href).startsWith((info?.repo || '') + '/issues/new?')) { e.preventDefault(); return; }   // nur das feste Repo
        if (last.moved.length) copy(last.clipboard);
        setTimeout(() => { dlg.close(); reset(); }, 300);       // sonst bleibt der Entwurf (Schließen/Esc verwirft nichts)
      } else if (b.id === 'repNext') {
        const f = (n) => $(`[name=${n}]`, dlg);
        if (!f('title').value.trim() || !f('actual').value.trim()) {
          (f('title').value.trim() ? f('actual') : f('title')).focus();
          return toast('Bitte Titel und „Was ist passiert?“ ausfüllen.', 'err');
        }
        try {
          last = await send('/report/compose', { title: f('title').value, actual: f('actual').value, expected: f('expected').value,
            steps: f('steps').value, area: f('area').value, log: f('log').checked, route: location.hash, errors: ERR.slice(),
            browser: browser(), width: innerWidth });
        } catch (err) { return toast(err.message, 'err'); }
        if (!dlg.open) return;
        const p = $('#repPrev', dlg);
        p.innerHTML = previewHtml(last);
        $('#repForm', dlg).hidden = true; p.hidden = false;
        dlg.scrollTop = 0;
      }
    };
    $('#repPick input', dlg).onchange = (e) => { pickFile(e.target.files[0]); e.target.value = ''; };
  }

  addEventListener('keydown', (e) => {
    if ((e.ctrlKey || e.metaKey) && e.shiftKey && e.key.toLowerCase() === 'f' && AF.app?.report) { e.preventDefault(); open(); }
  });
  document.addEventListener('click', (e) => { if (e.target.closest('[data-report]')) { e.preventDefault(); open(); } });
  return { open, errors: () => ERR.slice(), maskPage };
})();
