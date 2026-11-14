'use strict';
/* Teleskop (modules/telescope): Live-Ansicht #/teleskop – animiertes Teleskop vor dem Himmel, Kuppel von oben, aktuelles Ziel,
   Bedingungen, Zeitleiste der Nacht, Hindernis-Vorschläge, Ereignisprotokoll und große Schalter (Start/Pause/Sofort einklappen).
   Der Server schickt keine Koordinaten des Ortes – nur Azimut/Höhe der Ziele und die Horizontmaske. */
(() => {
  const T = '/telescope';
  const MODE = { aus: 'Autopilot aus', laeuft: 'Autopilot läuft', pausiert: 'Pausiert', wartet: 'Wartet', fertig: 'Nacht beendet' };
  const ITEM = { geplant: 'geplant', aktiv: 'läuft', fertig: 'fertig', uebersprungen: 'übersprungen', verdeckt: 'verdeckt', abgebrochen: 'abgebrochen' };
  const STEP = { idle: 'Wartet', opening: 'Öffnet', parking: 'Klappt ein', init: 'Richtet sich aus', goto: 'Schwenkt zum Ziel', astrometry: 'Bestimmt die Lage', capture: 'Nimmt auf' };
  const tf = (t) => t ? new Date(t * 1000).toLocaleTimeString('de-DE', { hour: '2-digit', minute: '2-digit' }) : '–';
  const wrap = (d) => ((d % 360) + 540) % 360 - 180;
  const reduced = () => matchMedia('(prefers-reduced-motion: reduce)').matches;
  const cssVar = (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim() || '#888';

  // ------------------------------------------------------------ Alarm --- //
  // Einklappen nicht bestätigt: Ton (alle 6 s) + Browser-/Handy-Benachrichtigung, solange die Seite offen ist. Kein Push-Dienst.
  let alarmTimer = null, alarmText = null;
  function beep() {
    try {
      const ctx = new (window.AudioContext || window.webkitAudioContext)();
      [0, 0.35, 0.7].forEach((d) => {
        const o = ctx.createOscillator(), g = ctx.createGain();
        o.frequency.value = 880; o.connect(g); g.connect(ctx.destination);
        g.gain.setValueAtTime(0.0001, ctx.currentTime + d); g.gain.exponentialRampToValueAtTime(0.25, ctx.currentTime + d + 0.02);
        g.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + d + 0.28);
        o.start(ctx.currentTime + d); o.stop(ctx.currentTime + d + 0.3);
      });
      setTimeout(() => ctx.close(), 1500);
    } catch { /* kein Ton möglich */ }
  }
  function alarm(text) {
    if (text === alarmText) return;
    alarmText = text;
    clearInterval(alarmTimer); alarmTimer = null;
    if (!text) return;
    beep(); alarmTimer = setInterval(beep, 6000);
    try { if ('Notification' in window && Notification.permission === 'granted') new Notification('AstroFlow – Teleskop', { body: text, requireInteraction: true }); } catch { /* egal */ }
  }
  const askNotify = () => { try { if ('Notification' in window && Notification.permission === 'default') Notification.requestPermission(); } catch { /* egal */ } };

  // ------------------------------------------------------- Höhenanzeige --- //
  // Bewusst abstrakt (Nutzerwunsch 14.11.2026): kein Gerätemodell, sondern ein Viertelkreis von Horizont bis Zenit mit Zeiger
  // = gemeldete Höhe. Gilt für jedes Teleskop. Eingeklappt: Zeiger eingefahren, Knoten gedimmt. Öffnen/Einklappen = Ein-/Ausfahren.
  const altOf = (d) => Math.max(0, Math.min(90, d.state?.alt ?? 0));
  const openOf = (d) => d.state?.parked && d.state?.step !== 'opening' ? 0 : 1;
  const DIRS = ['N', 'NO', 'O', 'SO', 'S', 'SW', 'W', 'NW'];
  const dirOf = (az) => DIRS[Math.round(((az % 360) + 360) % 360 / 45) % 8];
  // Höhe: Viertelkreis (Glas), Striche alle 10°, grüner Bogen „frei ab“, Zeiger mit Keil und Wert an der Spitze.
  // Azimut: kleine Kompassrose daneben. Darunter die Phase. Rückgabe: Zeigerspitze (für den Lichtstrom).
  function drawGauge(g, x, y, R, alt, az, open, minAlt, capture, label, t, col) {
    const ang = (a) => -a * Math.PI / 180, on = open > 0.5, pulse = reduced() ? 0 : Math.sin(t / 320);
    g.save(); g.lineCap = 'round'; g.textBaseline = 'middle';
    // Glasfläche
    const bg = g.createRadialGradient(x, y, R * 0.1, x, y, R + 8);
    bg.addColorStop(0, 'rgba(30,38,54,.15)'); bg.addColorStop(1, 'rgba(30,38,54,.55)');
    g.fillStyle = bg; g.beginPath(); g.moveTo(x, y); g.arc(x, y, R + 8, ang(90), 0); g.closePath(); g.fill();
    g.strokeStyle = 'rgba(170,185,210,.18)'; g.lineWidth = 1; g.stroke();
    // Striche
    for (let a = 0; a <= 90; a += 10) {
      const c = Math.cos(ang(a)), sn = Math.sin(ang(a)), big = a % 30 === 0;
      g.strokeStyle = big ? 'rgba(190,200,220,.55)' : 'rgba(190,200,220,.22)'; g.lineWidth = big ? 1.5 : 1;
      g.beginPath(); g.moveTo(x + c * (R - (big ? 9 : 5)), y + sn * (R - (big ? 9 : 5))); g.lineTo(x + c * R, y + sn * R); g.stroke();
      if (big && a) { g.fillStyle = col.dim; g.font = '500 10px system-ui, sans-serif'; g.textAlign = 'center'; g.fillText(a + '°', x + c * (R - 20), y + sn * (R - 20)); }
    }
    // frei ab Mindesthöhe
    g.shadowColor = col.ok; g.shadowBlur = 6;
    g.strokeStyle = col.ok + 'cc'; g.lineWidth = 3;
    g.beginPath(); g.arc(x, y, R + 4, ang(90), ang(minAlt)); g.stroke();
    g.shadowBlur = 0;
    // Keil + Zeiger
    const len = R * (0.16 + 0.84 * open), c = Math.cos(ang(alt)), sn = Math.sin(ang(alt)), tx = x + c * len, ty = y + sn * len;
    if (open > 0.05) {
      const kg = g.createRadialGradient(x, y, 0, x, y, len);
      kg.addColorStop(0, col.acc + '00'); kg.addColorStop(1, col.acc + (capture ? '66' : '38'));
      g.fillStyle = kg; g.beginPath(); g.moveTo(x, y); g.arc(x, y, len, ang(alt) - 0.09, ang(alt) + 0.09); g.closePath(); g.fill();
    }
    const ng = g.createLinearGradient(x, y, tx, ty);
    ng.addColorStop(0, on ? col.acc + '55' : '#3a4250'); ng.addColorStop(1, on ? col.acc : '#5b6676');
    g.strokeStyle = ng; g.lineWidth = 3.5;
    g.beginPath(); g.moveTo(x, y); g.lineTo(tx, ty); g.stroke();
    if (on) { g.shadowColor = col.acc; g.shadowBlur = capture ? 10 + 4 * pulse : 6; }
    g.fillStyle = on ? col.acc : '#5b6676'; g.beginPath(); g.arc(tx, ty, 4, 0, 7); g.fill();
    g.shadowBlur = 0;
    if (on) {
      g.fillStyle = col.fg; g.font = '650 13px system-ui, sans-serif'; g.textAlign = 'left';
      g.fillText(`${Math.round(alt)}°`, tx + 9 * Math.max(0.3, c), ty - 10 * Math.max(0.4, -sn) - 2);
    }
    // Knoten (= das Teleskop)
    g.fillStyle = '#151a22'; g.strokeStyle = on ? col.acc : '#4a5464'; g.lineWidth = 2;
    g.beginPath(); g.arc(x, y, 8, 0, 7); g.fill(); g.stroke();
    if (capture) { g.strokeStyle = col.acc + '66'; g.lineWidth = 1.5; g.beginPath(); g.arc(x, y, 12 + 3 * pulse, 0, 7); g.stroke(); }
    // Kompass (Azimut)
    const cx = x + R + 46, cy = y - 26, cr = 17;
    g.fillStyle = 'rgba(30,38,54,.55)'; g.strokeStyle = 'rgba(170,185,210,.3)'; g.lineWidth = 1;
    g.beginPath(); g.arc(cx, cy, cr, 0, 7); g.fill(); g.stroke();
    g.fillStyle = col.dim; g.font = '600 8px system-ui, sans-serif'; g.textAlign = 'center';
    g.fillText('N', cx, cy - cr + 6);
    const za = (az - 90) * Math.PI / 180;
    g.fillStyle = on ? col.acc : '#5b6676';
    g.beginPath(); g.moveTo(cx + Math.cos(za) * (cr - 3), cy + Math.sin(za) * (cr - 3));
    g.lineTo(cx + Math.cos(za + 2.6) * 6, cy + Math.sin(za + 2.6) * 6); g.lineTo(cx + Math.cos(za - 2.6) * 6, cy + Math.sin(za - 2.6) * 6); g.closePath(); g.fill();
    g.fillStyle = col.mut; g.font = '500 10px system-ui, sans-serif'; g.textAlign = 'left';
    g.fillText(on ? `${Math.round(az)}° ${dirOf(az)}` : '–', cx + cr + 7, cy);
    // Phase
    g.fillStyle = on ? col.fg : col.dim; g.font = '600 11px system-ui, sans-serif'; g.textAlign = 'left';
    g.fillText(label, x - 6, y + 32);
    g.restore();
    return [tx, ty, c, sn];
  }

  // ------------------------------------------------- Bühne (Seitenansicht) --- //
  // Panorama 140° um die Blickrichtung des Teleskops: Himmel nach Sonnenhöhe, Sterne, Horizontmaske, verdeckte Richtungen,
  // Zielmarke; davor das Teleskop, dessen Tubus auf die Zielmarke zeigt (eingeklappt waagerecht).
  function stage(canvas) {
    const g = canvas.getContext('2d');
    const stars = [];
    let seed = 7;
    const rnd = () => (seed = (seed * 16807) % 2147483647) / 2147483647;
    for (let i = 0; i < 320; i++) stars.push({ az: rnd() * 360, alt: Math.asin(rnd()) * 180 / Math.PI, m: rnd() ** 3, tw: rnd() * 6.28 });
    let data = null, shown = { az: 0, alt: 0, open: 0 }, raf = 0, last = performance.now(), W = 0, H = 0;
    const col = { acc: cssVar('--acc'), ok: cssVar('--ok'), warn: cssVar('--warn'), bad: cssVar('--bad'), fg: cssVar('--fg'), dim: cssVar('--dim'), mut: cssVar('--mut') };
    const resize = () => {
      const r = canvas.getBoundingClientRect(), dpr = Math.min(2, devicePixelRatio || 1);
      W = r.width; H = r.height;
      canvas.width = Math.round(W * dpr); canvas.height = Math.round(H * dpr);
      g.setTransform(dpr, 0, 0, dpr, 0, 0);
    };
    const ro = new ResizeObserver(resize); ro.observe(canvas);
    resize();
    function sky(sun) {
      const s = sun ?? -30;
      const k = Math.max(0, Math.min(1, (s + 18) / 18));           // 0 = Nacht, 1 = Sonnenuntergang
      const top = `rgb(${Math.round(4 + 30 * k)},${Math.round(7 + 50 * k)},${Math.round(16 + 90 * k)})`;
      const low = `rgb(${Math.round(10 + 160 * k)},${Math.round(14 + 90 * k)},${Math.round(30 + 80 * k)})`;
      return [top, low, k];
    }
    function frame(t) {
      raf = requestAnimationFrame(frame);
      const dt = Math.min(0.2, (t - last) / 1000); last = t;
      if (!data || !W) return;
      const s = data.state, tgt = { az: s.az ?? 0, alt: altOf(data), open: openOf(data) };
      const k = reduced() ? 1 : 1 - Math.exp(-dt * 2.5);
      shown.az = (shown.az + wrap(tgt.az - shown.az) * k + 360) % 360;
      shown.alt += (tgt.alt - shown.alt) * k;
      shown.open += (tgt.open - shown.open) * k;
      const hy = H * 0.74, fov = 140;
      const X = (az) => W / 2 + wrap(az - shown.az) / fov * W;
      const Y = (alt) => hy - alt / 90 * (hy - 6) * 1.25;
      const [top, low, tw] = sky(data.conditions?.sun);
      const gr = g.createLinearGradient(0, 0, 0, hy);
      gr.addColorStop(0, top); gr.addColorStop(1, low);
      g.fillStyle = gr; g.fillRect(0, 0, W, hy);
      // Sterne (driften langsam mit der Erddrehung)
      const drift = (data.now * 0.004178) % 360;
      for (const st of stars) {
        const az = st.az + drift, x = X(az), y = Y(st.alt);
        if (x < -2 || x > W + 2 || y > hy) continue;
        const a = (1 - tw) * (0.25 + 0.75 * st.m) * (reduced() ? 1 : 0.75 + 0.25 * Math.sin(t / 700 + st.tw));
        if (a <= 0.02) continue;
        g.fillStyle = `rgba(235,240,255,${a})`;
        g.fillRect(x, y, st.m > 0.6 ? 2 : 1.2, st.m > 0.6 ? 2 : 1.2);
      }
      // Wolken (Vorhersage oder im Bild gemessen)
      const cloudy = data.hold && ['clouds', 'weather'].includes(data.hold.kind) || (data.conditions?.cloud ?? 0) > 60;
      if (cloudy) {
        for (let i = 0; i < 6; i++) {
          const x = ((t / 90 + i * 260) % (W + 300)) - 150, y = hy * (0.25 + 0.1 * (i % 3));
          const cg = g.createRadialGradient(x, y, 4, x, y, 120);
          cg.addColorStop(0, 'rgba(120,128,145,.45)'); cg.addColorStop(1, 'rgba(120,128,145,0)');
          g.fillStyle = cg; g.beginPath(); g.ellipse(x, y, 150, 38, 0, 0, 7); g.fill();
        }
      }
      // Mindesthöhe (frei ab)
      g.setLineDash([4, 5]); g.strokeStyle = col.ok + '88'; g.lineWidth = 1;
      g.beginPath(); g.moveTo(0, Y(data.min_alt || 30)); g.lineTo(W, Y(data.min_alt || 30)); g.stroke(); g.setLineDash([]);
      // Horizontmaske + Boden
      const hz = data.site?.horizon;
      g.fillStyle = '#07080b';
      g.beginPath(); g.moveTo(0, H);
      for (let x = 0; x <= W; x += 4) {
        const az = shown.az + (x - W / 2) / W * fov;
        let h = 0;
        if (hz) { const f = (((az % 360) + 360) % 360) / 10, i = Math.floor(f) % 36; h = hz[i] * (1 - (f - Math.floor(f))) + hz[(i + 1) % 36] * (f - Math.floor(f)); }
        g.lineTo(x, Math.min(hy, Y(h)));
      }
      g.lineTo(W, H); g.closePath(); g.fill();
      g.fillStyle = '#0b0d11'; g.fillRect(0, hy, W, H - hy);
      g.fillStyle = `rgba(140,160,200,${0.08 + 0.2 * tw})`; g.fillRect(0, hy - 1, W, 1);
      // Himmelsrichtungen am Horizont
      g.font = '600 11px system-ui, sans-serif'; g.textAlign = 'center'; g.fillStyle = col.dim;
      for (const [a, n] of [[0, 'N'], [45, 'NO'], [90, 'O'], [135, 'SO'], [180, 'S'], [225, 'SW'], [270, 'W'], [315, 'NW']]) {
        const x = X(a); if (x > 10 && x < W - 10) g.fillText(n, x, hy + 14);
      }
      // verdeckte Richtungen
      for (const b of data.blocked || []) {
        const x = X(b.az), y = Y(b.alt);
        if (x < -20 || x > W + 20) continue;
        g.strokeStyle = col.bad; g.lineWidth = 2;
        g.beginPath(); g.moveTo(x - 5, y - 5); g.lineTo(x + 5, y + 5); g.moveTo(x + 5, y - 5); g.lineTo(x - 5, y + 5); g.stroke();
      }
      // Zielmarke
      const tp = data.target_pointing, capture = s.step === 'capture';
      let mx = W / 2, my = Y(shown.alt);
      if (tp && !s.parked) {
        mx = X(tp.az); my = Y(tp.alt);
        const pulse = reduced() ? 0 : (Math.sin(t / 400) + 1) * 2;
        g.strokeStyle = col.acc; g.lineWidth = 1.5;
        g.beginPath(); g.arc(mx, my, 11 + pulse, 0, 7); g.stroke();
        g.beginPath(); g.moveTo(mx - 18, my); g.lineTo(mx - 8, my); g.moveTo(mx + 8, my); g.lineTo(mx + 18, my);
        g.moveTo(mx, my - 18); g.lineTo(mx, my - 8); g.moveTo(mx, my + 8); g.lineTo(mx, my + 18); g.stroke();
        g.fillStyle = col.fg; g.font = '600 12px system-ui, sans-serif'; g.textAlign = 'left';
        g.fillText(data.current?.name || '', mx + 16, my - 14);
      }
      // Teleskop (Seitenansicht, Tubuswinkel = gemeldete Höhe)
      const front = drawGauge(g, Math.max(30, W * 0.06), hy, Math.min(120, hy * 0.5), shown.alt, shown.az, shown.open, data.min_alt || 30, capture, data.phase_label || '', t, col);
      if (capture && !reduced()) {                 // Licht fällt genau entlang der Blickrichtung in die Öffnung
        const [fx, fy, ux, uy] = front, D = Math.min(170, W * 0.3);
        for (let i = 0; i < 12; i++) {
          const f = (t / 1500 + i / 12) % 1, d = D * (1 - f) + 6;
          g.fillStyle = `rgba(170,205,255,${0.6 * f})`;
          g.beginPath(); g.arc(fx + ux * d, fy + uy * d, 1.4, 0, 7); g.fill();
        }
      }
    }
    raf = requestAnimationFrame(frame);
    return { set(d) { if (!data && d.state) { shown.az = d.state.az ?? 0; shown.alt = altOf(d); shown.open = openOf(d); } data = d; },
      stop() { cancelAnimationFrame(raf); ro.disconnect(); } };
  }

  // ------------------------------------------------------ Kuppel (von oben) --- //
  // Zenit in der Mitte, Norden oben, Osten links (Blick nach oben wie im Observatorium). Maske schraffiert, Mindesthöhe
  // gestrichelt, Bahnen der Ziele dieser Nacht, verdeckte Richtungen, Fadenkreuz = wohin das Teleskop gerade zeigt (mit Spur).
  function dome(canvas) {
    const g = canvas.getContext('2d');
    let data = null, raf = 0, last = performance.now(), shown = null, S = 0;
    const trail = [];
    const col = { acc: cssVar('--acc'), ok: cssVar('--ok'), bad: cssVar('--bad'), fg: cssVar('--fg'), dim: cssVar('--dim'), mut: cssVar('--mut'), b: cssVar('--border'), d2: cssVar('--data-2') };
    const resize = () => {
      const r = canvas.getBoundingClientRect(), dpr = Math.min(2, devicePixelRatio || 1);
      S = Math.min(r.width, r.height);
      canvas.width = Math.round(r.width * dpr); canvas.height = Math.round(r.height * dpr);
      g.setTransform(dpr, 0, 0, dpr, 0, 0);
    };
    const ro = new ResizeObserver(resize); ro.observe(canvas);
    resize();
    function frame(t) {
      raf = requestAnimationFrame(frame);
      const dt = Math.min(0.2, (t - last) / 1000); last = t;
      if (!data || !S) return;
      const r = canvas.getBoundingClientRect(), cx = r.width / 2, cy = r.height / 2, R = S / 2 - 16;
      const P = (az, alt) => { const rr = R * (90 - Math.max(-5, alt)) / 90, a = az * Math.PI / 180; return [cx - rr * Math.sin(a), cy - rr * Math.cos(a)]; };
      g.clearRect(0, 0, r.width, r.height);
      const bg = g.createRadialGradient(cx, cy, 0, cx, cy, R);
      bg.addColorStop(0, '#0d1424'); bg.addColorStop(1, '#090c14');
      g.fillStyle = bg; g.beginPath(); g.arc(cx, cy, R, 0, 7); g.fill();
      // Horizontmaske
      const hz = data.site?.horizon;
      if (hz) {
        g.fillStyle = 'rgba(225,107,107,.13)';
        g.beginPath();
        for (let a = 0; a <= 360; a += 5) { const f = a / 10, i = Math.floor(f) % 36, h = hz[i] * (1 - (f - Math.floor(f))) + hz[(i + 1) % 36] * (f - Math.floor(f)); const [x, y] = P(a, h); a ? g.lineTo(x, y) : g.moveTo(x, y); }
        for (let a = 360; a >= 0; a -= 5) { const [x, y] = P(a, 0); g.lineTo(x, y); }
        g.fill();
      }
      g.strokeStyle = col.b; g.lineWidth = 1;
      for (const a of [30, 60]) { g.beginPath(); g.arc(cx, cy, R * (90 - a) / 90, 0, 7); g.stroke(); }
      g.beginPath(); g.arc(cx, cy, R, 0, 7); g.stroke();
      g.setLineDash([4, 5]); g.strokeStyle = col.ok + '99';
      g.beginPath(); g.arc(cx, cy, R * (90 - (data.min_alt || 30)) / 90, 0, 7); g.stroke(); g.setLineDash([]);
      g.fillStyle = col.dim; g.font = '600 11px system-ui, sans-serif'; g.textAlign = 'center'; g.textBaseline = 'middle';
      for (const [a, n] of [[0, 'N'], [90, 'O'], [180, 'S'], [270, 'W']]) { const [x, y] = P(a, -9); g.fillText(n, x, y); }
      // Bahnen (beschriftet nur das laufende und die nächsten zwei Ziele – sonst wird es in der Mitte zu voll)
      const labeled = new Set((data.items || []).filter(i => i.status === 'aktiv' || (i.status === 'geplant' && i.end > data.now)).slice(0, 3).map(i => i));
      for (const it of data.items || []) {
        if (!it.track?.length || it.status === 'abgebrochen') continue;
        const act = it.status === 'aktiv';
        g.strokeStyle = act ? col.acc : it.status === 'fertig' ? col.ok + '88' : it.status === 'verdeckt' ? col.bad + 'aa' : col.d2 + '99';
        g.lineWidth = act ? 2.5 : 1.5;
        g.setLineDash(it.status === 'geplant' ? [] : it.status === 'fertig' ? [] : [3, 4]);
        g.beginPath(); it.track.forEach(([az, alt], i) => { const [x, y] = P(az, alt); i ? g.lineTo(x, y) : g.moveTo(x, y); }); g.stroke();
        g.setLineDash([]);
        if (!labeled.has(it)) continue;
        const [lx, ly] = P(...it.track[0]);
        g.fillStyle = act ? col.fg : col.mut; g.font = (act ? '600 ' : '500 ') + '10px system-ui, sans-serif'; g.textAlign = 'left';
        g.fillText(it.name, lx + 4, ly - 6);
      }
      for (const b of data.blocked || []) {
        const [x, y] = P(b.az, b.alt);
        g.strokeStyle = col.bad; g.lineWidth = 2;
        g.beginPath(); g.moveTo(x - 4, y - 4); g.lineTo(x + 4, y + 4); g.moveTo(x + 4, y - 4); g.lineTo(x - 4, y + 4); g.stroke();
      }
      // Teleskop
      const s = data.state;
      if (s.az != null && !s.parked) {
        const tgt = { az: s.az, alt: s.alt ?? 0 };
        if (!shown) shown = { ...tgt };
        const k = reduced() ? 1 : 1 - Math.exp(-dt * 2.5);
        shown.az = (shown.az + wrap(tgt.az - shown.az) * k + 360) % 360; shown.alt += (tgt.alt - shown.alt) * k;
        const [x, y] = P(shown.az, shown.alt);
        if (!trail.length || Math.hypot(trail.at(-1)[0] - x, trail.at(-1)[1] - y) > 2) { trail.push([x, y]); if (trail.length > 60) trail.shift(); }
        g.strokeStyle = col.acc + '55'; g.lineWidth = 1.5;
        g.beginPath(); trail.forEach(([a, b], i) => i ? g.lineTo(a, b) : g.moveTo(a, b)); g.stroke();
        g.strokeStyle = col.acc; g.lineWidth = 1.5;
        g.beginPath(); g.arc(x, y, 7, 0, 7); g.moveTo(x - 12, y); g.lineTo(x - 4, y); g.moveTo(x + 4, y); g.lineTo(x + 12, y);
        g.moveTo(x, y - 12); g.lineTo(x, y - 4); g.moveTo(x, y + 4); g.lineTo(x, y + 12); g.stroke();
      } else { shown = null; trail.length = 0; }
    }
    raf = requestAnimationFrame(frame);
    return { set(d) { data = d; }, stop() { cancelAnimationFrame(raf); ro.disconnect(); } };
  }

  // ------------------------------------------------------------ Abschnitte --- //
  function conditionsHtml(d) {
    const c = d.conditions || {}, s = d.state || {};
    const chip = (cls, label, val) => `<span class="chip ${cls}"><i style="background:currentColor"></i>${esc(label)} <b>${esc(val)}</b></span>`;
    const lvl = (v, lim, lower = false) => v == null ? '' : (lower ? v < lim : v > lim) ? 'bad' : (lower ? v < lim + 1 : v > lim * 0.8) ? 'warn' : 'ok';
    const out = [];
    if (c.sun != null) out.push(chip(c.sun > c.sun_limit ? 'warn' : 'ok', 'Sonne', `${Math.round(c.sun)}°`));
    out.push(c.cloud != null ? chip(lvl(c.cloud, c.cloud_max), 'Wolken', `${Math.round(c.cloud)} %`) : chip('', 'Wolken', 'keine Vorhersage'));
    if (c.wind != null) out.push(chip(lvl(c.wind, c.wind_max), 'Wind', `${Math.round(c.wind)} km/h`));
    if (c.dew_spread != null) out.push(chip(lvl(c.dew_spread, c.dew_limit, true), 'Taupunktabstand', `${c.dew_spread.toFixed(1)} °C`));
    if (s.battery != null || s.charging) out.push(chip(s.charging ? 'ok' : lvl(s.battery, c.battery_min, true), 'Akku', s.charging ? (s.battery != null ? `${s.battery} % · lädt` : 'lädt') : `${s.battery} %`));
    if (s.stars != null && s.step === 'capture') out.push(chip(s.stars > 30 ? 'ok' : s.stars > 8 ? 'warn' : 'bad', 'Sterne im Bild', s.stars));
    const w = c.weather_offline ? `<span class="sm warn">Kein Internet – Vorhersage von ${tf(c.weather_at)}</span>` : c.weather_at ? `<span class="sm dim">Vorhersage von ${tf(c.weather_at)} (Open-Meteo)</span>` : '';
    return `<div class="tel-conds">${out.join('')}</div>${w ? `<div class="tel-wnote">${w}</div>` : ''}`;
  }

  function currentHtml(d) {
    const c = d.current, s = d.state || {};
    if (!c) {
      const next = (d.items || []).find(i => ['geplant', 'verdeckt', 'uebersprungen'].includes(i.status) && i.end > d.now);
      return `<div class="tel-cur empty-cur"><div class="mut sm">Kein Ziel aktiv</div><div class="tel-cur-name">${next ? 'Als Nächstes: ' + esc(next.name) : '–'}</div>
        <div class="mut sm">${next ? `${tf(next.start)}–${tf(next.end)}` : esc(d.phase_label || '')}</div></div>`;
    }
    const p = Math.max(0, Math.min(1, (d.now - (c.real_start || c.start)) / Math.max(60, c.end - (c.real_start || c.start))));
    return `<div class="tel-cur"><div class="mut sm">${esc(STEP[s.step] || d.phase_label)}${c.substitute_for ? ` · Ersatz für ${esc(c.substitute_for)}` : ''}</div>
      <div class="tel-cur-name">${esc(c.name)}</div>
      <div class="progress"><i style="width:${(p * 100).toFixed(1)}%"></i></div>
      <div class="row sm mut tel-cur-nums"><span>${tf(c.real_start || c.start)}–${tf(c.end)}</span><span class="grow"></span>
        <span><b class="tnum">${s.stacked || 0}</b> gestapelt</span>${d.unit?.live_frames ? `<span><b class="tnum">${d.unit.frames_fetched}</b> geholt</span>` : ''}${s.rejected ? `<span><b class="tnum">${s.rejected}</b> verworfen</span>` : ''}</div></div>`;
  }

  function timelineHtml(d) {
    const items = (d.items || []).filter(i => i.status !== 'abgebrochen' || i.real_start);
    const t0 = Math.min(d.dark?.start ?? Infinity, ...items.map(i => i.start), d.now) - 1800;
    const t1 = Math.max(d.dark?.end ?? -Infinity, ...items.map(i => i.end), d.now + 3600) + 1800;
    if (!isFinite(t0) || !isFinite(t1) || t1 <= t0) return '<p class="mut sm">Noch keine Nacht geladen.</p>';
    const x = (t) => ((t - t0) / (t1 - t0) * 100).toFixed(2) + '%';
    const w = (a, b) => ((Math.max(0, b - a)) / (t1 - t0) * 100).toFixed(2) + '%';
    const ticks = [];
    for (let h = Math.ceil(t0 / 3600) * 3600; h < t1; h += 3600) ticks.push(`<i class="tel-tick" style="left:${x(h)}"><span>${tf(h).slice(0, 2)}</span></i>`);
    const dark = d.dark ? `<i class="tel-dark" style="left:${x(d.dark.start)};width:${w(d.dark.start, d.dark.end)}"></i>` : '';
    const clouds = (d.weather || []).filter(h => h.t >= t0 && h.t <= t1 && h.cloud != null)
      .map(h => `<i class="tel-cloud" style="left:${x(h.t - 1800)};width:${w(h.t - 1800, h.t + 1800)};opacity:${(h.cloud / 100 * 0.9).toFixed(2)}" title="${Math.round(h.cloud)} % Wolken"></i>`).join('');
    const rows = items.map(i => `<div class="tel-bar s-${i.status}${i.substitute_for ? ' sub' : ''}" style="left:${x(i.start)};width:${w(i.start, i.end)}" title="${esc(i.name)} · ${tf(i.start)}–${tf(i.end)} · ${ITEM[i.status]}${i.why ? ' (' + esc(i.why) + ')' : ''}">
        <span>${esc(i.name)}</span></div>`).join('');
    return `<div class="tel-tl"><div class="tel-tl-sky">${dark}${clouds}</div><div class="tel-tl-bars">${rows}</div><div class="tel-tl-axis">${ticks.join('')}</div>
      <i class="tel-now" style="left:${x(d.now)}"></i></div>
      <div class="row sm mut tel-legend"><span><i class="lg s-geplant"></i>geplant</span><span><i class="lg s-aktiv"></i>läuft</span><span><i class="lg s-fertig"></i>fertig</span>
        <span><i class="lg s-verdeckt"></i>verdeckt</span><span><i class="lg s-uebersprungen"></i>übersprungen</span><span><i class="lg dark"></i>dunkel</span></div>`;
  }

  function proposalsHtml(d) {
    if (!d.proposals?.length) return '';
    return `<section class="panel tel-prop"><div class="panel-h"><h2>${icon('alert')}Hindernisse erkannt</h2></div>
      <p class="mut sm">In diesen Richtungen fehlten heute die Sterne, während andere Richtungen klar waren. Soll die Horizontmaske des Ortes dort angehoben werden?
        Planer, Heute Nacht und Autopilot meiden die Stellen dann.</p>
      <ul class="tel-plist">${d.proposals.map(p => `<li><b>${esc(p.target)}</b> · Azimut ${Math.round(p.az)}°, bis ${Math.round(p.alt)}° Höhe${p.sure ? '' : ' <span class="chip warn">unsicher</span>'}</li>`).join('')}</ul>
      <div class="row"><button class="btn" data-act="accept">In Horizontmaske übernehmen</button><button class="btn ghost" data-act="dismiss">Verwerfen</button></div></section>`;
  }

  const evRow = (e) => `<li class="ev-${e.kind}"><time>${tf(e.t)}</time><i></i><span>${esc(e.text)}</span></li>`;

  // ---------------------------------------------------------------- Ansicht --- //
  async function telescopeView(el) {
    let d = await api(T + '/state');
    if (!el.isConnected) return;
    el.innerHTML = `<div class="tel">
      <div class="tel-head"><div class="tel-title"><h1>Teleskop</h1><div class="row tel-tags"></div></div>
        <div class="tel-btns"><button class="btn tel-go" data-act="go">${icon('play')}<span>Start</span></button>
          <button class="btn ghost tel-pause" data-act="pause">${icon('pause')}<span>Pause</span></button>
          <button class="btn danger tel-stop" data-act="park" title="Stoppt alles und klappt das Teleskop sofort ein">${icon('stop')}<span>Sofort einklappen</span></button></div></div>
      <div class="tel-alarm" hidden></div>
      <div class="tel-notes"></div>
      <div class="tel-grid">
        <section class="panel tel-stagebox"><canvas class="tel-stage" aria-label="Animiertes Teleskop vor dem Himmel"></canvas></section>
        <section class="panel tel-domebox"><canvas class="tel-dome" aria-label="Himmel von oben mit Bahnen der Ziele"></canvas></section>
      </div>
      <div class="tel-grid2">
        <section class="panel"><div class="panel-h"><h2>Aktuelles Ziel</h2></div><div class="tel-current"></div></section>
        <section class="panel"><div class="panel-h"><h2>Bedingungen</h2></div><div class="tel-conditions"></div></section>
      </div>
      <section class="panel"><div class="panel-h"><h2>Nacht</h2><span class="grow"></span><span class="mut sm tel-night"></span>
          <button class="btn ghost sm" data-act="reload" title="Plan der Nacht neu aus dem Planer laden">${icon('refresh')}Plan neu laden</button></div>
        <div class="tel-timeline"></div>
        <form class="row tel-add"><input name="name" placeholder="Ziel hinzufügen, z. B. M 81" aria-label="Ziel hinzufügen"><select name="hours" aria-label="Dauer">
          <option value="0.5">30 min</option><option value="1" selected>1 h</option><option value="2">2 h</option><option value="3">3 h</option></select>
          <button class="btn ghost" type="submit">${icon('plus')}Hinzufügen</button><a class="sm" href="#/observatorium/plan">Zum Planer</a></form></section>
      <div class="tel-props"></div>
      <section class="panel"><div class="panel-h"><h2>Ereignisse</h2></div><ol class="tel-log"></ol></section></div>`;
    const q = (s) => el.querySelector(s);
    const sv = stage(q('.tel-stage')), dv = dome(q('.tel-dome'));
    let lastEv = 0, timer = 0, sig = {};
    const put = (key, html, sel) => { if (sig[key] !== html) { sig[key] = html; q(sel).innerHTML = html; } };

    function render() {
      const u = d.unit || {}, s = d.state || {};
      sv.set(d); dv.set(d);
      const tags = [`<span class="chip ${u.simulated ? 'acc' : ''}">${u.simulated ? 'Simulation' : esc(u.label)}</span>`, `<span class="chip">${esc(u.model_name || '')}</span>`,
        u.simulated && u.speed > 1 ? `<span class="chip">Zeitraffer ×${Math.round(u.speed)}</span>` : '',
        `<span class="chip ${s.connected ? 'ok' : 'warn'}"><i style="background:currentColor"></i>${s.connected ? (s.master === 'us' ? 'verbunden' : s.master === 'other' ? 'App steuert' : 'verbunden, ohne Kontrolle') : 'nicht verbunden'}</span>`,
        `<span class="chip ${d.mode === 'laeuft' ? 'acc' : d.mode === 'wartet' ? 'warn' : ''}">${esc(MODE[d.mode] || d.mode)} · ${esc(d.phase_label)}</span>`,
        u.simulated ? `<span class="chip">Simulierte Zeit ${tf(d.now)}</span>` : ''].join('');
      put('tags', tags, '.tel-tags');
      const go = q('.tel-go'), pause = q('.tel-pause');
      const running = ['laeuft', 'wartet'].includes(d.mode);
      go.hidden = running; pause.hidden = !running;
      go.querySelector('span').textContent = d.mode === 'pausiert' ? 'Weiter' : 'Start';
      const notes = [];
      if (u.error) notes.push(`<div class="notice err">${icon('alert')}<div><b>Teleskop-Treiber nicht bereit</b><div class="mut sm">${esc(u.error)} – solange läuft der Simulator.</div></div><span class="grow"></span><a class="btn sm ghost" href="#/settings/telescope">Einstellungen</a></div>`);
      if (d.hold) notes.push(`<div class="notice ${['weather', 'clouds', 'dew', 'battery'].includes(d.hold.kind) ? 'err' : ''}">${icon(d.hold.kind === 'dawn' || d.hold.kind === 'twilight' ? 'star' : 'alert')}<div><b>${d.mode === 'fertig' ? 'Nacht beendet' : 'Wartet'}</b><div class="mut sm">${esc(d.hold.text)}</div></div></div>`);
      if (s.raw_enabled === false) notes.push(`<div class="notice">${icon('camera')}<div><b>Rohbilder am Teleskop aus</b><div class="mut sm">Für die AstroFlow-Pipeline in der Vaonis-App „Rohbilder speichern“ einschalten.</div></div></div>`);
      if (!d.items?.length) notes.push(`<div class="notice">${icon('dome')}<div><b>Noch kein Plan für diese Nacht</b><div class="mut sm">Im Planer eine Nacht planen oder unten Ziele hinzufügen.</div></div><span class="grow"></span><a class="btn sm ghost" href="#/observatorium/plan">Zum Planer</a></div>`);
      put('notes', notes.join(''), '.tel-notes');
      const al = q('.tel-alarm');
      al.hidden = !d.alarm; if (d.alarm) al.innerHTML = `${icon('alert')}<b>${esc(d.alarm)}</b>`;
      alarm(d.alarm || null);
      put('cur', currentHtml(d), '.tel-current');
      put('cond', conditionsHtml(d), '.tel-conditions');
      put('tl', timelineHtml(d), '.tel-timeline');
      put('props', proposalsHtml(d), '.tel-props');
      put('night', d.night ? `Nacht ${esc(d.night)}${d.site?.name ? ' · ' + esc(d.site.name) : ''}${d.dark ? ` · dunkel ${tf(d.dark.start)}–${tf(d.dark.end)}` : ''}` : '', '.tel-night');
    }
    async function poll() {
      if (!el.isConnected) return;
      try {
        const [nd, ev] = await Promise.all([api(T + '/state'), api(T + '/events?since=' + lastEv)]);
        if (!el.isConnected) return;
        d = nd;
        if (ev.events.length) {
          const log = q('.tel-log');
          log.insertAdjacentHTML('afterbegin', ev.events.slice().reverse().map(evRow).join(''));
          lastEv = ev.events.at(-1).id;
          while (log.children.length > 80) log.lastElementChild.remove();
        }
        render();
      } catch { /* nächster Versuch */ }
      timer = setTimeout(poll, 1000);
    }
    el.addEventListener('click', async (e) => {
      const b = e.target.closest('[data-act]');
      if (!b) return;
      const act = b.dataset.act;
      try {
        if (act === 'go') {
          askNotify();
          if (d.mode === 'pausiert') await send(T + '/resume');
          else {
            let confirm = false;
            if (!d.unit?.simulated && !d.confirmed) {
              confirm = await confirmDlg('Teleskop bewegen?', 'Der Autopilot öffnet das Teleskop, richtet es aus und schwenkt zu den Zielen. Steht es frei, sodass sich der Arm ungehindert bewegen kann?', 'Ja, starten');
              if (!confirm) return;
            }
            await send(T + '/start', { confirm });
          }
        } else if (act === 'pause') await send(T + '/pause');
        else if (act === 'park') { await send(T + '/park'); toast('Teleskop wird eingeklappt.'); }
        else if (act === 'reload') { const r = await send(T + '/reload'); toast(`${r.items} Ziele für diese Nacht geladen.`); }
        else if (act === 'accept') {
          if (!await confirmDlg('Horizontmaske anpassen?', 'Die Maske des Ortes wird an den erkannten Stellen auf knapp über die gescheiterte Höhe angehoben. Das lässt sich unter Einstellungen → Standorte wieder ändern.', 'Übernehmen')) return;
          await send(T + '/proposals/accept', {}); toast('Horizontmaske angepasst.');
        } else if (act === 'dismiss') await send(T + '/proposals/dismiss');
        clearTimeout(timer); poll();
      } catch (err) { toast(err.message, 'err'); }
    });
    q('.tel-add').addEventListener('submit', async (e) => {
      e.preventDefault();
      const f = e.target;
      try { await send(T + '/targets', { name: f.name.value, hours: +f.hours.value }); f.name.value = ''; clearTimeout(timer); poll(); } catch (err) { toast(err.message, 'err'); }
    });
    render();
    poll();
    return { destroy() { clearTimeout(timer); sv.stop(); dv.stop(); alarm(null); } };
  }

  // ----------------------------------------------------------- Einstellungen --- //
  async function settings(el) {
    const c = await api(T + '/settings');
    if (!el.isConnected) return;
    const num = (k, label, unit, step = 1, hint = '') => `<label class="field"><span>${label}</span><div class="row" style="flex-wrap:nowrap"><input name="${k}" type="number" step="${step}" value="${esc(c[k])}" style="flex:1"><span class="mut sm">${unit}</span></div>${hint ? `<small>${hint}</small>` : ''}</label>`;
    const sw = (k, label, sub) => `<label class="modrow"><div><b>${label}</b><div class="mut sm">${sub}</div></div><input type="checkbox" name="${k}" ${c[k] ? 'checked' : ''}><span class="switch"></span></label>`;
    const opt = (pl, o) => {
      const v = (c.plugin_options?.[pl.id] || {})[o.key];
      return o.type === 'bool'
        ? `<label class="modrow"><div><b>${esc(o.label || o.key)}</b>${o.hint ? `<div class="mut sm">${esc(o.hint)}</div>` : ''}</div><input type="checkbox" data-po="${esc(pl.id)}" data-k="${esc(o.key)}" ${v ? 'checked' : ''}><span class="switch"></span></label>`
        : `<label class="field"><span>${esc(o.label || o.key)}</span><input data-po="${esc(pl.id)}" data-k="${esc(o.key)}" value="${esc(v ?? '')}">${o.hint ? `<small>${esc(o.hint)}</small>` : ''}</label>`;
    };
    const plugin = (pl) => `<div class="tel-plug"><div class="row"><div class="grow"><b>${esc(pl.name)}</b>
        <div class="mut sm">${esc(pl.family || 'Teleskop')} · Vertrag ${esc(pl.contract ?? '?')} · <span class="tel-path">${esc(pl.path)}</span></div></div>
        ${pl.error ? `<span class="chip bad">${esc(pl.error)}</span>` : pl.enabled ? '<span class="chip ok">aktiviert</span>' : ''}
        ${pl.error && !pl.enabled ? '' : `<button type="button" class="btn ghost sm" data-plug="${esc(pl.id)}" data-on="${pl.enabled ? 0 : 1}" data-local-only>${pl.enabled ? 'Deaktivieren' : 'Aktivieren …'}</button>`}</div>
        ${pl.enabled && pl.options?.length ? `<div class="tel-plug-opts" data-local-only>${pl.options.map(o => opt(pl, o)).join('')}</div>` : ''}</div>`;
    el.innerHTML = `<form class="tel-set"><section class="panel"><div class="panel-h"><h2>${icon('target')}Teleskop</h2></div>
        <label class="field"><span>Steuerung</span><select name="driver">${c.drivers.map(d => `<option value="${esc(d.id)}" ${d.usable ? '' : 'disabled'}>${esc(d.label)}${d.usable ? '' : ' – nicht aktiviert'}</option>`).join('')}</select>
          <small>Ohne Plugin: Simulator oder „nur Status und Bilder“ (Import vom Teleskop, Live-Rohbilder). Steuern braucht ein Treiber-Plugin.</small></label>
        <label class="field"><span>Gerät</span><select name="model">${Object.entries(c.models).map(([k, v]) => `<option value="${k}">${esc(v)}</option>`).join('')}</select></label>
        <label class="field" data-local-only><span>Adresse des Teleskops</span><input name="ip" value="${esc(c.ip)}" placeholder="Standard: Teleskop-WLAN (10.0.0.1)"></label>
        ${sw('fallback', 'Plan im Gerät als Rückfall', 'Fällt die Verbindung weg, arbeitet das Teleskop den Rest der Nacht selbst ab und klappt am Ende ein (wenn das Plugin es kann).')}
        ${sw('live_frames', 'Rohbilder live abholen', 'Während der Aufnahme neue Einzelbilder per FTP aus dem Teleskop holen (nur lesen) und in den Eingangsordner legen – die Bilder-Pipeline stackt mit.')}
        ${sw('weather', 'Wettervorhersage nutzen', 'Open-Meteo, Ort auf 0,1° gerundet wie bei Heute Nacht; wird während der Nacht alle 15 min aktualisiert.')}</section>
      <section class="panel"><div class="panel-h"><h2>Treiber-Plugins</h2></div>
        <p class="mut sm">Plugins steuern Teleskope, die AstroFlow nicht selbst mitbringt. Sie sind <b>fremder Code</b> mit allen Rechten von AstroFlow –
          nur aus vertrauenswürdiger Quelle aktivieren. Gesucht wird im Datenordner unter <code>plugins/</code> und in diesen Ordnern:</p>
        <div data-local-only><label class="field"><span>Weitere Plugin-Ordner (einer je Zeile)</span><textarea name="plugin_paths" rows="2">${esc((c.plugin_paths || []).join('\n'))}</textarea></label>
          <button type="button" class="btn ghost sm" data-paths>${icon('refresh')}Ordner übernehmen und suchen</button></div>
        <div class="tel-plugs">${c.plugins.length ? c.plugins.map(plugin).join('') : '<p class="mut sm">Keine Plugins gefunden.</p>'}</div></section>
      <section class="panel"><div class="panel-h"><h2>Einklappen und wieder öffnen</h2></div>
        <div class="tel-set-grid">${num('cloud_max', 'Wolken über', '%')}${num('wind_max', 'Wind über', 'km/h')}${num('dew_min', 'Taupunktabstand unter', '°C', 0.5)}
        ${num('dew_min_stellina', 'Stellina: Taupunktabstand unter', '°C', 0.5)}${num('reopen_min', 'Wieder öffnen nach stabil guten', 'min')}${num('battery_min', 'Akku unter', '%')}
        ${num('min_alt', 'Mindesthöhe der Ziele', '°')}${num('exposure', 'Einzelbelichtung', 's', 1, 'Gerätestandard: Vespera/Stellina 10 s')}</div>
        <label class="field"><span>Nacht beginnt und endet bei</span><select name="sun_limit"><option value="-12">Sonne −12° (nautisch)</option><option value="-18">Sonne −18° (nur astronomisch dunkel)</option></select></label></section>
      <section class="panel"><div class="panel-h"><h2>Simulator</h2></div>
        <div class="tel-set-grid"><label class="field"><span>Zeitraffer</span><select name="sim_speed"><option value="1">Echtzeit</option><option value="10">×10</option><option value="30">×30</option><option value="120">×120</option></select></label>
        <label class="field"><span>Start der Uhr</span><select name="sim_start"><option value="night">Beginn der nächsten Dunkelheit</option><option value="now">Jetzt</option></select></label>
        <label class="field"><span>Szenario</span><select name="sim_scenario"><option value="demo">Vorführung (Hauswand im Osten, Wolkenband, Tau)</option><option value="clear">Klare Nacht</option></select></label></div></section>
      <div class="row"><button class="btn" type="submit">Speichern</button><span class="mut sm">Wirkt sofort, solange der Autopilot nicht läuft.</span></div></form>`;
    const f = el.querySelector('.tel-set');
    for (const k of ['driver', 'model', 'sun_limit', 'sim_speed', 'sim_start', 'sim_scenario']) f.elements[k].value = String(c[k]).replace(/\.0$/, '');
    const reload = () => settings(el);
    f.onclick = async (e) => {
      const b = e.target.closest('[data-plug]');
      if (b) {
        const on = b.dataset.on === '1';
        if (on && !await confirmDlg('Fremden Code aktivieren?', 'Ein Treiber-Plugin läuft mit allen Rechten von AstroFlow auf diesem Rechner und kann das Teleskop bewegen. Nur aktivieren, wenn du der Quelle vertraust.', 'Aktivieren')) return;
        try { await send(T + '/plugins/enable', { id: b.dataset.plug, enable: on }); toast(on ? 'Plugin aktiviert.' : 'Plugin deaktiviert.'); reload(); } catch (err) { toast(err.message, 'err'); }
      }
      if (e.target.closest('[data-paths]')) {
        try { await send(T + '/plugins/paths', { paths: f.elements.plugin_paths.value.split('\n') }); reload(); } catch (err) { toast(err.message, 'err'); }
      }
    };
    f.onsubmit = async (e) => {
      e.preventDefault();
      const body = {}, po = {};
      for (const x of f.elements) {
        if (x.dataset.po) { (po[x.dataset.po] ||= {})[x.dataset.k] = x.type === 'checkbox' ? x.checked : x.value; continue; }
        if (!x.name || x.name === 'plugin_paths') continue;
        body[x.name] = x.type === 'checkbox' ? x.checked : x.type === 'number' ? +x.value : x.name === 'sun_limit' || x.name === 'sim_speed' ? +x.value : x.value;
      }
      if (AF.app?.local === false) delete body.ip; else body.plugin_options = po;
      try { await send(T + '/settings', body); toast('Gespeichert.'); reload(); } catch (err) { toast(err.message, 'err'); }
    };
  }

  // ------------------------------------------------- Import: vom Teleskop --- //
  // Abschnitt oben in der Importansicht (stacking.js ruft importPanel auf). Erscheint nur, wenn ein Teleskop erreichbar ist;
  // bei eingerichtetem echten Teleskop sonst eine leise Zeile mit „Erneut suchen“.
  const mb = (b) => b >= 1e9 ? (b / 1e9).toFixed(1).replace('.', ',') + ' GB' : Math.max(1, Math.round(b / 1e6)) + ' MB';
  const dday = (t) => t ? new Date(t * 1000).toLocaleDateString('de-DE', { day: '2-digit', month: '2-digit', year: 'numeric' }) : '';
  async function importPanel(host) {
    let timer = 0, sel = null;
    const load = async (fresh = false) => {
      if (!host.isConnected) return;
      let d;
      try { d = await api(T + '/captures' + (fresh ? '?fresh=1' : '')); } catch { host.innerHTML = ''; return; }
      if (!host.isConnected) return;
      const j = d.job, running = j?.state === 'running';
      if (!d.available) {
        host.innerHTML = d.simulated ? '' : `<div class="tel-imp-off">${icon('target')}<span class="mut sm">${esc(d.reason || 'Teleskop nicht erreichbar.')}</span>
          <button class="btn ghost sm" data-act="fresh">${icon('refresh')}Erneut suchen</button></div>`;
      } else {
        if (!sel) sel = new Set(d.items.filter(e => !e.imported).map(e => e.key));
        const n = d.items.filter(e => sel.has(e.key)).reduce((a, e) => a + e.frames, 0);
        host.innerHTML = `<section class="panel tel-imp"><div class="panel-h"><h2>${icon('target')}Aufnahmen auf dem Teleskop</h2>
            <span class="chip">${esc(d.model_name || d.model)}</span>${d.simulated ? '<span class="chip acc">Simulation</span>' : ''}<span class="grow"></span>
            <button class="btn ghost sm" data-act="fresh" ${running ? 'disabled' : ''}>${icon('refresh')}Aktualisieren</button></div>
          <p class="mut sm">Das Teleskop ist erreichbar. Rohbilder vorhandener Beobachtungen direkt übernehmen – sie landen im Eingangsordner und werden wie gewohnt gestackt.</p>
          ${d.items.length ? `<ul class="tel-imp-list">${d.items.map(e => `<li><label><input type="checkbox" data-k="${esc(e.key)}" ${sel.has(e.key) ? 'checked' : ''} ${running ? 'disabled' : ''}>
              <span class="tel-imp-name"><b>${esc(e.name || e.observation)}</b><span class="mut sm">${esc(e.sub || e.plan)}${e.sub ? '' : e.modified ? ' · ' + dday(e.modified) : ''}</span></span>
              <span class="sm mut tnum">${e.frames} Bilder · ${mb(e.bytes)}</span>${e.imported ? '<span class="chip ok">schon importiert</span>' : ''}</label></li>`).join('')}</ul>`
            : '<p class="mut sm">Keine Rohbilder auf dem Teleskop gefunden.</p>'}
          ${running ? `<div class="tel-imp-job"><div class="row sm"><span class="live"></span><span>${esc(j.current)}</span><span class="grow"></span><span class="tnum">${j.done}/${j.total}</span></div>
              <div class="progress"><i style="width:${j.total ? (j.done / j.total * 100).toFixed(1) : 0}%"></i></div></div>`
            : j?.text ? `<p class="sm ${j.state === 'error' ? 'bad' : 'mut'}">${esc(j.text)}</p>` : ''}
          ${d.items.length ? `<div class="row"><button class="btn" data-act="import" ${running || !n ? 'disabled' : ''}>${icon('down')}${n ? `${n} Rohbilder importieren` : 'Nichts gewählt'}</button></div>` : ''}</section>`;
      }
      clearTimeout(timer);
      if (running) timer = setTimeout(() => load(), 1500);
      const finished = !running && host.dataset.wasRunning;
      host.dataset.wasRunning = running ? '1' : '';
      if (finished) {                                   // fertig: Hinweis, Auswahl neu (Importiertes nicht mehr vorgewählt)
        if (j?.state === 'done') toast(j.text);
        sel = null; load();
      }
    };
    host.onclick = async (e) => {
      const b = e.target.closest('[data-act]');
      if (!b) return;
      if (b.dataset.act === 'fresh') { sel = null; return load(true); }
      if (b.dataset.act === 'import') {
        try { await send(T + '/captures/import', { keys: [...sel] }); host.dataset.wasRunning = '1'; load(); } catch (err) { toast(err.message, 'err'); }
      }
    };
    host.onchange = (e) => {
      const c = e.target.closest('[data-k]');
      if (!c) return;
      if (c.checked) sel.add(c.dataset.k); else sel.delete(c.dataset.k);
      load();
    };
    load();
  }

  AF.register('telescope', {
    importPanel,
    nav: [{ href: '#/teleskop', label: 'Teleskop', icon: 'target', key: 'telescope', area: 'work' }],
    routes: [[/^\/teleskop$/, 'telescope', telescopeView]],
    settings,
    status: (st, badge) => badge('telescope', st.modules.telescope?.busy_jobs, true),
    async summary(st) {
      const s = st.modules.telescope || {};
      return { title: 'Teleskop', icon: 'target', href: '#/teleskop', sub: `${MODE[s.mode] || 'Autopilot aus'}${s.simulated ? ' · Simulation' : ''}`,
        stats: [[s.target || '–', 'Ziel'], [s.phase_label || '–', 'Zustand']], live: s.alarm ? `<b class="bad">${esc(s.alarm)}</b>` : '' };
    },
  });
})();
