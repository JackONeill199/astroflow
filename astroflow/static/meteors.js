// AstroFlow – Sternschnuppen im Hintergrund nach dem echten Himmel (seit 0.24.0, Nutzerwunsch „selten, ein interessanter Algorithmus
// anhand echter Daten“). Rechnet für Ort (Standorte, sonst grob aus der Zeitzone) und Uhrzeit, wie viele Meteore ein Beobachter
// gerade sähe, und zeigt einen kleinen Bruchteil davon:
//  • Ströme (IAU-MDC-Liste der großen Ströme): Aktivität nach Sonnenlänge λ☉ mit Jenniskens' Profil ZHR(λ) = ZHRmax · 10^(−B·|λ − λmax|),
//    sichtbare Rate HR = ZHR · sin(Radiantenhöhe) · r^(Grenzgröße − 6,5) – unter dem Horizont keine.
//  • Sporadische: ~2/h Grundrate plus bis zu ~10/h je nach Höhe des Apex (Richtung der Erdbewegung) → morgens und im Herbst mehr.
//  • Himmel: Sonne über −6° → keine (wie draußen), nautische Dämmerung dämpft, Mondlicht senkt die Grenzgröße.
//  • Anzeige: Poisson-Prozess mit HR / SCALE; Strom-Meteore fliegen vom Radianten weg (Blick nach Süden, auf der Südhalbkugel nach Norden),
//    Tempo und Länge aus der Eintrittsgeschwindigkeit, schnelle leuchten grünlich (Mg/O), langsame gelblich (Na/Fe).
(() => {
  const RAD = Math.PI / 180, SCALE = 5, TICK = 15;
  // name, λmax (°, J2000), ZHR, B (Anstieg, Abfall: 1/°), RA, Dec (°), v (km/s), r (Populationsindex)
  const SHOWERS = [
    ['Quadrantiden', 283.15, 110, 2.5, 2.5, 230, 49, 41, 2.1], ['Lyriden', 32.32, 18, 0.9, 0.9, 271, 34, 49, 2.1],
    ['Eta-Aquariiden', 45.5, 50, 0.08, 0.08, 338, -1, 66, 2.4], ['Südliche Delta-Aquariiden', 127, 25, 0.091, 0.091, 340, -16, 41, 2.5],
    ['Alpha-Capricorniden', 127, 5, 0.07, 0.07, 307, -10, 23, 2.5], ['Perseiden', 140.0, 100, 0.20, 0.40, 48, 58, 59, 2.2],
    ['Draconiden', 195.4, 10, 2.0, 2.0, 262, 54, 20, 2.6], ['Südliche Tauriden', 197, 5, 0.026, 0.026, 32, 9, 27, 2.3],
    ['Orioniden', 208, 20, 0.12, 0.12, 95, 16, 66, 2.5], ['Nördliche Tauriden', 230, 5, 0.026, 0.026, 58, 22, 29, 2.3],
    ['Leoniden', 235.27, 15, 0.5, 0.5, 152, 22, 71, 2.5], ['Geminiden', 262.2, 150, 0.39, 0.81, 112, 33, 35, 2.6],
    ['Ursiden', 270.7, 10, 0.9, 0.9, 217, 76, 33, 3.0],
  ].map(([name, lmax, zhr, bUp, bDown, ra, dec, v, r]) => ({ name, lmax, zhr, bUp, bDown, ra, dec, v, r }));

  const sin = (d) => Math.sin(d * RAD), cos = (d) => Math.cos(d * RAD), norm = (d) => ((d % 360) + 360) % 360;
  function altaz(ra, dec, lst, lat) {
    const H = lst - ra, sa = sin(lat) * sin(dec) + cos(lat) * cos(dec) * cos(H);
    const alt = Math.asin(Math.max(-1, Math.min(1, sa))) / RAD;
    const az = norm(Math.atan2(-sin(H) * cos(dec), sin(dec) * cos(lat) - cos(dec) * sin(lat) * cos(H)) / RAD);
    return { alt, az };
  }
  const eclToEq = (lam, eps) => ({ ra: norm(Math.atan2(cos(eps) * sin(lam), cos(lam)) / RAD), dec: Math.asin(sin(eps) * sin(lam)) / RAD });

  // Himmelszustand für Zeitpunkt + Ort: Sonnenlänge, Sonnenhöhe, Mondphase, Raten je Quelle (sichtbar pro Stunde)
  function sky(date = new Date(), lat = 50, lon = 10) {
    const d = date.getTime() / 864e5 + 2440587.5 - 2451545.0;
    const lst = norm(280.46061837 + 360.98564736629 * d + lon), eps = 23.439 - 4e-7 * d;
    const g = norm(357.528 + 0.9856003 * d), lam = norm(280.460 + 0.9856474 * d + 1.915 * sin(g) + 0.020 * sin(2 * g));
    const sun = altaz(...Object.values(eclToEq(lam, eps)), lst, lat);
    // Mond: Phase aus der synodischen Periode (Neumond 06.01.2000 18:14 UT), Beleuchtung 0…1; Höhe vereinfacht nicht gerechnet
    const age = ((d - 5.26) % 29.530589 + 29.530589) % 29.530589, illum = (1 - Math.cos(2 * Math.PI * age / 29.530589)) / 2;
    const dark = sun.alt <= -12 ? 1 : sun.alt >= -6 ? 0 : (-6 - sun.alt) / 6;          // nautische Dämmerung blendet ein
    const lm = 6.5 - 1.5 * illum - 1.5 * (1 - dark);                                    // Grenzgröße
    const sources = [];
    for (const s of SHOWERS) {
      const dl = ((lam - s.lmax + 540) % 360) - 180;
      const zhr = s.zhr * Math.pow(10, -(dl < 0 ? s.bUp : s.bDown) * Math.abs(dl));
      if (zhr < 0.5) continue;
      const p = altaz(s.ra, s.dec, lst, lat);
      const hr = p.alt > 0 ? zhr * sin(p.alt) * Math.pow(s.r, lm - 6.5) * dark : 0;
      sources.push({ ...s, zhrNow: zhr, alt: p.alt, az: p.az, hr });
    }
    const apex = altaz(...Object.values(eclToEq(norm(lam - 90), eps)), lst, lat);
    const spor = (2 + 10 * Math.max(0, sin(apex.alt))) * Math.pow(2.8, lm - 6.5) * dark;
    sources.push({ name: 'Sporadisch', hr: spor, v: 30 + 40 * Math.max(0, sin(apex.alt)), r: 2.8, sporadic: true });
    const total = sources.reduce((a, s) => a + s.hr, 0);
    return { lam, sunAlt: sun.alt, moon: illum, dark, lm, sources, total, shown: total / SCALE, lat, lon };
  }

  // Ort: Standort für heute aus den Standorten (Server), sonst Breite 50° und Länge aus der Zeitzone
  let site = null, siteAt = 0;
  async function where() {
    if (site && Date.now() - siteAt < 36e5) return site;
    site = { lat: 50, lon: -new Date().getTimezoneOffset() / 4, guess: true }; siteAt = Date.now();
    try {
      const r = await fetch('/api/sites/resolve?when=' + Date.now() / 1000);
      const s = r.ok && (await r.json()).site;
      if (s && isFinite(s.lat) && isFinite(s.lon)) site = { lat: +s.lat, lon: +s.lon, name: s.name };
    } catch { /* offline: Schätzung bleibt */ }
    return site;
  }

  let layer = null, timer = 0, state = null;
  const look = () => document.documentElement.dataset.look;
  const allowed = () => look() && store.get('ui.meteors', true) && !matchMedia('(prefers-reduced-motion: reduce)').matches;

  function fly(src) {
    if (!layer) return;
    const w = innerWidth, h = innerHeight, m = document.createElement('i');
    const v = src.v || 40, fast = Math.max(0, Math.min(1, (v - 25) / 45));
    const len = 60 + v * 2.6 * (0.7 + Math.random() * 0.6), dist = 160 + v * 5 * (0.7 + Math.random() * 0.6);
    const dur = Math.max(350, Math.min(1400, 950 * Math.pow(35 / v, 0.7) * (0.85 + Math.random() * 0.3)));
    let x = w * (0.1 + Math.random() * 0.85), y = h * (0.05 + Math.random() * 0.55), ang;
    if (!src.sporadic && src.alt > 0) {
      // Blickrichtung Süden (Südhalbkugel Norden), Azimut ±100° über die Breite, Höhe 0…75° über die Höhe → vom Radianten weg
      const face = (state?.lat ?? 50) >= 0 ? 180 : 0, daz = ((src.az - face + 540) % 360) - 180;
      const rx = w * (0.5 + daz / 200), ry = h * (0.95 - Math.min(src.alt, 75) / 75 * 0.95);
      ang = Math.atan2(y - ry, x - rx) / RAD;
      if (Math.hypot(x - rx, y - ry) < 40) ang = Math.random() * 360;
    } else {
      ang = 20 + Math.random() * 140;                     // sporadisch: irgendwohin, meist nach unten
    }
    // Element: Kopf am rechten Ende, gedreht um den Kopf → links/oben so setzen, dass der Kopf bei (x, y) startet
    m.className = 'meteor';
    m.style.cssText = `left:${x - len}px;top:${y}px;--ang:${ang}deg;--len:${len}px;--dist:${dist}px;--dur:${dur}ms;`
      + `--peak:${0.5 + Math.random() * 0.5};--head:${fast > 0.6 ? '#d9ffef' : fast < 0.3 ? '#fff1c9' : '#ffffff'};`
      + `--halo:${fast > 0.6 ? '#7dffc466' : fast < 0.3 ? '#ffc96b55' : '#8fa8ff55'}`;
    m.title = src.name;
    m.addEventListener('animationend', () => m.remove());
    layer.append(m);
  }

  async function tick() {
    timer = 0;
    if (!allowed()) return stop();
    const s = await where();
    state = sky(new Date(), s.lat, s.lon);
    if (!document.hidden && state.shown > 0 && Math.random() < 1 - Math.exp(-state.shown / 3600 * TICK)) {
      let pick = Math.random() * state.total;
      fly(state.sources.find(x => (pick -= x.hr) <= 0) || state.sources.at(-1));
    }
    if (layer) timer = setTimeout(tick, TICK * 1000);
  }
  function stop() { clearTimeout(timer); timer = 0; layer?.remove(); layer = null; }
  function start() {
    if (!allowed()) return stop();
    if (!layer) { layer = document.createElement('div'); layer.id = 'meteors'; layer.setAttribute('aria-hidden', 'true'); document.body.prepend(layer); }
    if (!timer) timer = setTimeout(tick, 2000);
  }
  // Vorführen (Einstellungen): eine Sternschnuppe der gerade stärksten Quelle, unabhängig von der Rate
  async function one() {
    start(); if (!layer) return;
    const s = await where(); state = sky(new Date(), s.lat, s.lon);
    fly([...state.sources].sort((a, b) => b.hr - a.hr)[0] || { name: 'Sporadisch', v: 40, sporadic: true });
  }
  // Kurzer Text für die Einstellungen: was der Himmel gerade hergibt
  async function info() {
    const s = await where(), k = sky(new Date(), s.lat, s.lon), f = (n) => n < 10 ? n.toFixed(1).replace('.', ',') : Math.round(n);
    const active = k.sources.filter(x => !x.sporadic && x.zhrNow >= 2).sort((a, b) => b.zhrNow - a.zhrNow);
    const where_ = s.guess ? 'deinem ungefähren Ort (kein Standort eingetragen, Breite 50° angenommen)' : (s.name || 'deinem Standort');
    if (k.dark === 0) return `Gerade ist es bei ${where_} hell (Sonne ${Math.round(k.sunAlt)}°) – wie draußen keine Sternschnuppen.${active.length ? ` Aktiv: ${active.map(a => a.name).join(', ')}.` : ''}`;
    return `Am Himmel über ${where_}: etwa ${f(k.total)} Meteore pro Stunde${active.length ? ` – ${active.map(a => `${a.name} (ZHR ${f(a.zhrNow)}, Radiant ${a.alt > 0 ? Math.round(a.alt) + '° hoch' : 'unter dem Horizont'})`).join(', ')}` : ', nur sporadische'}`
      + `, Mond ${Math.round(k.moon * 100)} %. Angezeigt wird etwa jede ${SCALE}. → ~${f(k.shown)} pro Stunde.`;
  }
  window.meteors = { start, stop, one, info, sky, SHOWERS };
  if (document.body) start(); else addEventListener('DOMContentLoaded', start);
})();
