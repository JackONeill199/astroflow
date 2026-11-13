/* Teilen-Karte für Foren & Co. (share.py): Vorschau, Schalter, Download und Forentext. Eine Bilddatei, kein Link – nichts wird hochgeladen.
   Aufruf: shareDialog(resultId) aus Objektkopf (Export-Menü), Bild-Details und Galerie-Betrachter. */
const SHARE_MAX = 4;
async function shareDialog(rid) {
  const d = $('#dlg');
  d.innerHTML = `<div class="share"><div class="share-h"><h2>Teilen-Karte</h2><span class="grow"></span>
    <button class="btn ghost sm" data-close title="Schließen" aria-label="Schließen">${icon('x')}</button></div>
    <div class="skel" style="height:18rem"></div></div>`;
  d.onclick = (e) => { if (e.target === d || e.target.closest('[data-close]')) d.close(); };
  d.showModal();
  let info;
  try { info = await api(`/stacking/results/${rid}/share`); } catch (e) {
    if (d.open && $('.share', d)) $('.share', d).insertAdjacentHTML('beforeend', `<p class="bad sm">${esc(e.message)}</p>`), $('.share .skel', d)?.remove();
    return;
  }
  if (!d.open || !$('.share', d)) return;
  const saved = store.get('stacking.share', {});
  const st = {
    layout: ['quer', 'hoch'].includes(saved.layout) ? saved.layout : info.defaults.layout,
    size: info.sizes.includes(saved.size) ? saved.size : info.defaults.size,
    parts: new Set(info.defaults.parts.filter(p => !(saved.off || []).includes(p))),
    finds: new Set(info.defaults.finds), fmt: saved.fmt === 'markdown' ? 'markdown' : 'bbcode', own: false, photographer: '',
  };
  const PART = [['object', 'Objekt', 'Typ, Sternbild, Entfernung'], ['shot', 'Aufnahme', 'Gerät, Belichtung, Aufnahmen, Datum'],
    ...(info.has_labels ? [['labels', 'Objektnamen im Bild', 'dezent, aus dem Plate-Solving']] : []),
    ...(info.has_map ? [['map', 'Himmelskarte', 'Sternbild mit Position']] : [])].filter(([k]) => k !== 'shot' || info.has_shot);
  const sw = (id, on, label, sub = '', dis = false) => `<label class="modrow"><input type="checkbox" data-k="${id}"${on ? ' checked' : ''}${dis ? ' disabled' : ''}><span class="switch"></span>
    <div><b>${esc(label)}</b>${sub ? `<div class="mut sm">${esc(sub)}</div>` : ''}</div></label>`;
  $('.share', d).innerHTML = `<div class="share-h"><h2>Teilen-Karte</h2><span class="mut sm share-name">${esc(info.name)}</span><span class="grow"></span>
      <button class="btn ghost sm" data-close title="Schließen" aria-label="Schließen">${icon('x')}</button></div>
    <div class="share-body">
      <div class="share-prev"><div class="share-img" id="shPrev"><img alt="Vorschau der Teilen-Karte" hidden><span class="skel"></span></div>
        <p class="dim sm">Ohne Standort, Uhrzeiten, Dateinamen und Seriennummern. Die Datei entsteht nur auf diesem Rechner – hochgeladen wird nichts.</p></div>
      <div class="share-opts">
        <div class="share-row"><span class="mut sm">Format</span><div class="seg" id="shLayout"><button data-v="quer">Querformat</button><button data-v="hoch">Hochformat 4:5</button></div></div>
        <div class="share-row"><span class="mut sm">Größe</span><div class="seg" id="shSize">${info.sizes.map(s => `<button data-v="${s}">${s === 1600 ? 'Forum' : 'Groß'} <span class="dim">${s} px</span></button>`).join('')}</div></div>
        <h3>Inhalt</h3>${PART.map(([k, l, s]) => sw('p:' + k, st.parts.has(k), l, s)).join('')}
        ${info.finds.length ? `<h3>Was AstroFlow herausgefunden hat <span class="dim">· bis ${SHARE_MAX}</span></h3><div id="shFinds">${info.finds.map(f => sw('f:' + f.key, st.finds.has(f.key), f.value, f.label)).join('')}</div>`
          : '<p class="mut sm">Noch keine Auswertungen (Tiefe, Asteroiden …) – die Karte zeigt Foto, Objekt und Aufnahme.</p>'}
        ${info.imported ? `<h3>Urheber</h3><div class="notice share-own"><div class="mut sm">Importiertes Bild: Teile es nur, wenn es dein eigenes ist – oder nenne den Fotografen.</div></div>
          ${sw('own', false, 'Das ist mein eigenes Foto')}` : '<h3>Fotograf <span class="dim">· optional</span></h3>'}
        <input type="text" id="shBy" maxlength="60" placeholder="Foto von … (Name oder Forenname)" autocomplete="off" aria-label="Fotograf">
      </div></div>
    <div class="share-f"><div class="seg" id="shFmt"><button data-v="bbcode">BBCode</button><button data-v="markdown">Markdown</button></div>
      <button class="btn ghost" id="shCopy">${icon('list')}Text kopieren</button><span class="grow"></span>
      <a class="btn" id="shDl" download>${icon('down')}Herunterladen</a></div>`;
  const img = $('#shPrev img', d), prev = $('#shPrev', d);
  const query = (dl) => {
    const q = new URLSearchParams({ layout: st.layout, size: st.size, parts: [...st.parts].join(',') || 'none', finds: [...st.finds].join(',') || 'none' });
    if (st.photographer) q.set('photographer', st.photographer);
    if (st.own) q.set('own', '1');
    if (dl) q.set('download', '1');
    return q.toString();
  };
  const blocked = () => info.imported && !st.own && !st.photographer;
  let text = info.text, seq = 0, timer = 0;
  const refresh = () => {
    clearTimeout(timer);
    timer = setTimeout(async () => {
      const mine = ++seq;
      store.set('stacking.share', { layout: st.layout, size: st.size, fmt: st.fmt, off: info.defaults.parts.filter(p => !st.parts.has(p)) });
      const dl = $('#shDl', d);
      dl.classList.toggle('disabled', blocked());
      dl.setAttribute('aria-disabled', blocked() ? 'true' : 'false');
      dl.href = blocked() ? '#' : `/api/stacking/results/${rid}/share/card.jpg?${query(true)}`;
      prev.classList.toggle('hoch', st.layout === 'hoch');
      if (blocked()) { img.hidden = true; prev.dataset.msg = 'Bitte bestätige, dass es dein eigenes Foto ist, oder nenne den Fotografen.'; }
      else {
        delete prev.dataset.msg;
        prev.classList.add('busy');
        const src = `/api/stacking/results/${rid}/share/card.jpg?${query(false)}`;
        const pre = new Image();
        pre.onload = () => { if (mine === seq && d.open) { img.src = src; img.hidden = false; prev.classList.remove('busy'); $('.skel', prev)?.remove(); } };
        pre.onerror = () => { if (mine === seq && d.open) { prev.classList.remove('busy'); prev.dataset.msg = 'Die Vorschau ließ sich nicht erzeugen.'; } };
        pre.src = src;
      }
      try { const r = await api(`/stacking/results/${rid}/share?${query(false)}`); if (mine === seq) text = r.text; } catch { /* alter Text bleibt */ }
    }, 180);
  };
  const seg = (id, key, cast = String) => {
    const el = $('#' + id, d);
    const mark = () => $$('button', el).forEach(b => b.classList.toggle('on', b.dataset.v === String(st[key])));
    el.onclick = (e) => { const b = e.target.closest('button'); if (!b) return; st[key] = cast(b.dataset.v); mark(); refresh(); };
    mark();
  };
  seg('shLayout', 'layout'); seg('shSize', 'size', Number); seg('shFmt', 'fmt');
  const limit = () => $$('#shFinds input', d).forEach(i => { i.disabled = !i.checked && st.finds.size >= SHARE_MAX; });
  limit();
  d.onchange = (e) => {
    const k = e.target.dataset?.k;
    if (!k) return;
    const [kind, key] = k.includes(':') ? k.split(':') : [k, ''];
    if (kind === 'p') e.target.checked ? st.parts.add(key) : st.parts.delete(key);
    else if (kind === 'f') { e.target.checked ? st.finds.add(key) : st.finds.delete(key); limit(); }
    else if (kind === 'own') st.own = e.target.checked;
    refresh();
  };
  $('#shBy', d).oninput = (e) => { st.photographer = e.target.value.trim(); refresh(); };
  $('#shDl', d).onclick = (e) => { if (blocked()) { e.preventDefault(); toast('Bitte bestätige, dass es dein eigenes Foto ist, oder nenne den Fotografen.', 'err'); } };
  $('#shCopy', d).onclick = async () => {
    const t = text[st.fmt];
    let ok = false;
    try { await navigator.clipboard.writeText(t); ok = true; } catch {
      const ta = document.createElement('textarea');          // ohne sichere Verbindung (Heimnetz per IP) gibt es keine Clipboard-API
      ta.value = t; ta.setAttribute('readonly', ''); ta.style.cssText = 'position:fixed;opacity:0';
      d.append(ta); ta.select();
      try { ok = document.execCommand('copy'); } catch { ok = false; }
      ta.remove();
    }
    toast(ok ? `${st.fmt === 'bbcode' ? 'BBCode' : 'Markdown'} kopiert – im Forum einfügen und die Karte als Bild anhängen.` : 'Kopieren nicht möglich.', ok ? 'ok' : 'err');
  };
  d.onclose = () => { clearTimeout(timer); seq++; d.onchange = null; };
  refresh();
}
