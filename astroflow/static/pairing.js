/* Einstellungen → Heimnetz: Heimnetz ein/aus, Geräte per QR-Code koppeln, gekoppelte Geräte verwalten.
   Backend: core/security.py (Kopplung, Prüfungen), server.py (/api/pair, /api/pairing…), core/setup.py (lan).
   Kopplung verwalten geht nur am Rechner selbst; gekoppelte Geräte sehen hier nur einen Hinweis. */
(() => {
  const mins = (t) => Math.max(0, Math.round((t - Date.now() / 1000) / 60));

  async function pairingSettings(el) {
    if (AF.app?.local === false) {
      el.innerHTML = `<div class="panel"><h2>Heimnetz</h2><p class="mut sm" style="margin:0.5rem 0 0">Dieses Gerät ist mit AstroFlow gekoppelt.
        Weitere Geräte koppeln oder gekoppelte entfernen geht nur am Rechner selbst, auf dem AstroFlow läuft (Einstellungen → Heimnetz).</p></div>`;
      return;
    }
    let d = await api('/pairing'), s = await api('/setup'), timer = null, sig = '';
    if (!el.isConnected) return;
    const root = document.createElement('div');      // eigener Behälter: #sbody wird von allen Abschnitten geteilt
    el.replaceChildren(root);
    const sigOf = () => JSON.stringify([d.devices, d.links, d.lan, d.lan_active, mins(d.expires)]);
    const paint = () => {
      const active = d.lan_active, devs = d.devices || [];
      const state = active ? `<span class="chip ok">aktiv</span>`
        : d.lan ? `<span class="chip warn">wirkt nach dem Neustart</span>` : `<span class="chip">aus</span>`;
      const restartHint = d.lan && !active ? `<p class="warn sm pa-gap">${s.launched ? 'AstroFlow startet gleich neu …'
        : 'Eingeschaltet – wirkt nach dem nächsten Start: AstroFlow mit Stop.bat beenden und mit Start.bat neu starten.'}</p>`
        : !d.lan && active ? `<p class="mut sm pa-gap">Ausgeschaltet – gilt nach dem nächsten Start. Bis dahin bleibt AstroFlow im Heimnetz erreichbar (nur für gekoppelte Geräte).</p>` : '';
      const migrated = d.lan_migrated ? `<div class="notice"><div><b>Neu seit dem Update: Geräte koppeln</b><div class="mut sm">Das Heimnetz bleibt wie bisher eingeschaltet.
        Handy und Tablet müssen jetzt aber einmal gekoppelt werden: QR-Code unten mit dem Gerät scannen. Danach geht alles wie gewohnt.</div></div></div>` : '';
      root.innerHTML = `<div class="split">
        <div class="panel"><div class="panel-h"><h2>Heimnetz</h2><span class="grow"></span>${state}</div>
          ${migrated}
          <label class="modrow"><div><b>Im Heimnetz erreichbar</b><div class="mut sm">Handy, Tablet oder ein anderer PC im eigenen WLAN können AstroFlow öffnen –
            aber nur Geräte, die du hier einmal per QR-Code gekoppelt hast. Ausgeschaltet ist AstroFlow nur auf diesem Rechner erreichbar.</div></div>
            <input type="checkbox" id="paLan" ${d.lan ? 'checked' : ''}><span class="switch"></span></label>
          ${restartHint}
          ${active && s.platform === 'Windows' ? `<div class="su-acts pa-gap"><button class="btn ghost sm" id="paFw">Windows-Firewall freigeben</button>
            <span class="dim sm">Falls Geräte die Seite nicht erreichen (fragt nach Administratorrechten).</span></div>` : ''}
          ${active && s.platform === 'Linux' ? `<div class="su-acts pa-gap"><button class="btn ghost sm" id="paFw" data-local-only>Firewall freigeben</button>
            <button class="btn ghost sm" id="paFwRm" data-local-only>Freigabe entfernen</button>
            <span class="dim sm">ufw oder firewalld, nur für das eigene Heimnetz (das System fragt nach dem Passwort).</span></div>` : ''}
          <p class="dim sm pa-gap">Einstellungen, die Programme starten (Komponenten, Programmpfade, StarNet), Heimnetz, Datenordner und Beenden gehen nur am Rechner selbst.</p>
        </div>
        <div class="panel"><h2>Gerät koppeln</h2>
          ${!active ? `<p class="mut sm pa-gap">Erst das Heimnetz einschalten${d.lan ? ' und AstroFlow neu starten' : ''}. Danach erscheint hier der QR-Code.</p>`
          : !d.qr ? `<p class="warn sm pa-gap">Keine Netzwerkadresse dieses Rechners gefunden – ist er mit dem WLAN/LAN verbunden?</p>`
          : `<div class="pa-pair"><div class="pa-qr" aria-label="QR-Code zum Koppeln">${d.qr}</div>
            <div class="pa-how"><ol class="mut sm"><li>Mit der Kamera des Handys oder Tablets den QR-Code scannen und den Link öffnen.</li>
              <li>Das Gerät ist danach dauerhaft gekoppelt – am besten gleich ein Lesezeichen bzw. „Zum Startbildschirm“ anlegen.</li></ol>
              <p class="sm pa-gap">Oder den Link am Gerät eingeben:</p>
              ${d.links.map(l => `<code class="pa-link">${esc(l)}</code>`).join('')}
              <p class="dim sm pa-gap">Gilt noch ${mins(d.expires)} min und für ein Gerät. Danach erscheint hier ein neuer Code.</p>
              <div class="su-acts"><button class="btn ghost sm" id="paNew">${icon('refresh')}Neuen Kopplungscode erzeugen</button></div></div></div>`}
        </div>
        <div class="panel"><div class="panel-h"><h2>Gekoppelte Geräte</h2><span class="grow"></span><span class="chip">${devs.length}</span></div>
          ${devs.length ? `<div class="pa-devs">${devs.map(x => `<div class="pa-dev"><div><b>${esc(x.name)}</b>
              <div class="mut sm">gekoppelt ${ago(x.created)} · zuletzt ${ago(x.seen)}</div></div>
              <button class="btn ghost sm danger" data-pa-rm="${esc(x.id)}">Entfernen</button></div>`).join('')}</div>`
          : `<p class="mut sm pa-gap">Noch kein Gerät gekoppelt.</p>`}
        </div></div>`;
    };
    const load = async () => {
      if (!root.isConnected) return stop();
      try { d = await api('/pairing'); } catch { return; }
      const n = sigOf();
      if (n !== sig) { sig = n; paint(); }
    };
    const stop = () => { clearInterval(timer); timer = null; };
    root.onclick = async (e) => {
      const b = e.target.closest('button');
      if (!b) return;
      try {
        if (b.id === 'paNew') { d = await send('/pairing/code'); sig = ''; paint(); toast('Neuer Kopplungscode – der alte gilt nicht mehr.', 'ok'); }
        else if (b.id === 'paFw' || b.id === 'paFwRm') { const r = await send('/setup/firewall', { remove: b.id === 'paFwRm' }); toast(r.detail, r.ok ? 'ok' : 'err'); }
        else if (b.dataset.paRm) {
          if (!await confirmDlg('Gerät entfernen?', 'Das Gerät muss danach neu gekoppelt werden, um AstroFlow zu öffnen.', 'Entfernen', true)) return;
          d = await send('/pairing/remove', { id: b.dataset.paRm }); sig = ''; paint();
        }
      } catch (err) { toast(err.message, 'err'); }
    };
    root.onchange = async (e) => {
      if (e.target.id !== 'paLan') return;
      try {
        const r = await send('/setup', { lan: e.target.checked });
        s = r; d = await api('/pairing'); sig = ''; paint();
        if (r.restart) window.restartOverlay?.(e.target.checked ? 'AstroFlow wird im Heimnetz erreichbar …' : 'AstroFlow ist danach nur noch auf diesem Rechner erreichbar …');
        else toast(e.target.checked ? 'Heimnetz eingeschaltet – wirkt nach dem nächsten Start.' : 'Heimnetz ausgeschaltet – wirkt nach dem nächsten Start.', 'ok');
      } catch (err) { toast(err.message, 'err'); e.target.checked = !e.target.checked; }
    };
    sig = sigOf();
    paint();
    timer = setInterval(load, 4000);                 // neue Geräte und der nächste Code erscheinen von selbst
  }

  window.pairingSettings = pairingSettings;
})();
