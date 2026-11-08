/* Echte Zwischenbilder: zwei Ebenen für weiche Wechsel, Status ohne Neuaufbau des Viewers.
   Beim Stacken „entsteht das Bild aus dem Rauschen“: live.frames sind echte Zwischenstände des Stacks (1, 2, 3, 4, 5, 6, 8 …
   Aufnahmen, später seltener) mit fester Streckung über den ganzen Lauf; die seit dem letzten Abruf neuen werden über das
   Abrufintervall verteilt nacheinander überblendet – so sinkt das Rauschen sichtbar. Keine erfundenen Zwischenbilder. */
const LIVE_POLL = 2500;
function createStackLive(host, pid) {
  host.innerHTML = `<section class="stack-live" aria-label="Live-Verarbeitung">
    <div class="sl-picture"><img class="sl-image" alt="Live-Vorschau des Astrofotos"><img class="sl-image" alt="" aria-hidden="true">
      <div class="sl-wait"><span class="sl-orbit" aria-hidden="true"></span><span>Noch keine Bildvorschau</span></div>
      <span class="sl-badge">LIVE-VORSCHAU</span><div class="sl-caption"><span></span><span></span></div></div>
    <div class="sl-status"><span class="sl-pulse" aria-hidden="true"></span><span class="sl-label" aria-live="polite"></span>
      <span class="sl-percent tnum"></span><details class="sl-details"><summary>Details</summary>
        <div class="sl-pop"><b>Verarbeitungsverlauf</b><ol></ol><p class="mut sm">Zwischenbilder sind verkleinerte Vorschauen. Beim Stacken zeigen sie echte Zwischenstände mit gleichbleibender Helligkeitskurve – so sieht man das Rauschen sinken. Das fertige Bild erhält die vollständige Bearbeitung.</p></div></details></div>
    <div class="sl-track" role="progressbar" aria-label="Verarbeitungsfortschritt" aria-valuemin="0" aria-valuemax="100"><i></i></div></section>`;
  const root = host.firstElementChild, images = [...root.querySelectorAll('.sl-image')];
  const find = s => root.querySelector(s);
  const fmt = (v) => Number(v).toLocaleString('de-DE');
  let active = 0, wanted = '', shown = '', destroyed = false, stepSig = '', fallback = false;
  let preview = '', played = '', run = '', queue = [], timer = 0, current = null, lastLive = {};
  function show(src, previous = false, onShown = null) {
    if (!src) return;
    if (src === shown) { wanted = src; return; }          // schon sichtbar: ausstehenden Wechsel verwerfen
    if (src === wanted) return;
    wanted = src;
    const next = images[1 - active];
    next.onload = () => {
      if (destroyed || wanted !== src) return;
      images[active].classList.remove('on');
      next.classList.add('on');
      next.setAttribute('aria-hidden', 'false');
      next.alt = previous ? 'Bisheriges Ergebnis – neue Verarbeitung läuft' : 'Echte Live-Vorschau des Astrofotos';
      images[active].setAttribute('aria-hidden', 'true');
      active = 1 - active; shown = src; fallback = previous;
      root.classList.add('has-picture');
      find('.sl-badge').textContent = previous ? 'BISHERIGES ERGEBNIS' : 'LIVE-VORSCHAU';
      onShown?.();
    };
    next.onerror = () => { if (wanted === src) wanted = ''; }; // nächster Poll versucht es erneut
    next.src = src;
  }
  const frameSrc = f => `/api/stacking/projects/${pid}/live/${encodeURIComponent(f.name)}`;
  function caption(f) {
    const live = lastLive, c = find('.sl-caption'), n = f?.n ?? live.n, band = f ? f.band : live.band, exp = f?.exposure ?? live.exposure;
    c.children[0].textContent = n != null ? `${fmt(n)} Aufnahmen${band ? ' · Kanal ' + band : ''}` : '';
    const noise = f?.noise != null && f.n > 1 ? `Rauschen ${f.noise <= 1 ? '−' : '+'}${Math.round(Math.abs(1 - f.noise) * 100)} %` : '';
    c.children[1].textContent = [noise, exp != null ? dur(exp) + ' integriert' : ''].filter(Boolean).join(' · ');
    if (fallback && !f && !live.preview) c.children[0].textContent = 'Letzte fertige Version';
  }
  function step() {                                       // nächsten echten Zwischenstand überblenden
    timer = 0;
    const f = queue.shift();
    if (!f || destroyed) return;
    const slot = Math.max(350, Math.min(1500, LIVE_POLL / (queue.length + 1)));
    root.style.setProperty('--sl-fade', Math.round(Math.min(slot * 0.85, 800)) + 'ms');
    current = f;
    show(frameSrc(f), false, () => { if (current === f) caption(f); });
    if (queue.length) timer = setTimeout(step, slot);
  }
  function frames(list) {
    if (!list?.length) return;
    const names = list.map(f => f.name), i = names.indexOf(played);
    let fresh = i >= 0 ? list.slice(i + 1) : list.slice(-1);        // neu geöffnet: nur der jüngste Stand
    if (!fresh.length) return;
    if (document.hidden) fresh = fresh.slice(-1);                  // Hintergrund-Tab: nicht nachholen
    played = fresh[fresh.length - 1].name;
    queue = queue.concat(fresh).slice(-8);                         // nie weit hinterherhinken
    if (!timer) step();
  }
  return {
    update(p) {
      if (destroyed) return;
      const j = p.job || {}, live = p.live || {}, queued = j.state === 'queued';
      lastLive = live;
      root.classList.toggle('sl-queued', queued);
      root.classList.toggle('sl-error', j.state === 'error');
      find('.sl-label').textContent = queued ? (j.message || 'In der Warteschlange') : j.message || j.stage || 'Verarbeitung startet';
      const percent = j.progress == null || queued ? null : Math.round(j.progress * 100);
      find('.sl-percent').textContent = percent == null ? '' : percent + ' %';
      const track = find('.sl-track');
      track.classList.toggle('indet', percent == null);
      if (percent == null) track.removeAttribute('aria-valuenow'); else track.setAttribute('aria-valuenow', percent);
      find('.sl-track i').style.width = (percent ?? 28) + '%';
      if (live.run !== run) { run = live.run; played = ''; queue = []; }
      if (live.preview) {                                          // Endbearbeitung: deren Vorschau hat Vorrang
        if (live.preview !== preview) {
          preview = live.preview; queue = []; clearTimeout(timer); timer = 0; current = null;
          root.style.removeProperty('--sl-fade');
        }
        // bei jedem Poll: show() übergeht gezeigte/ladende Bilder, ein fehlgeschlagener Abruf wird so erneut versucht
        show(`/api/stacking/projects/${pid}/live/${encodeURIComponent(live.preview)}`);
      } else frames(live.frames);
      if (!shown && !live.preview && !live.frames?.length && p.results?.[0]) show(`/api/stacking/results/${p.results[0].id}/jpg`, true);
      caption(live.preview ? null : current);
      const steps = live.steps?.length ? live.steps : [{ stage: j.stage || 'Vorbereitung', label: j.message || 'Verarbeitung wird vorbereitet', done: false }];
      const sig = JSON.stringify(steps);
      if (sig !== stepSig) {
        stepSig = sig;
        find('.sl-pop ol').innerHTML = steps.map(s => `<li class="${s.done ? 'done' : 'current'}"><span aria-hidden="true">${s.done ? '✓' : '·'}</span><div><small>${esc(s.stage)}</small><span>${esc(s.label)}</span></div></li>`).join('');
      }
    },
    destroy() { destroyed = true; clearTimeout(timer); images.forEach(img => { img.onload = img.onerror = null; }); },
  };
}
