'use strict';
/* Stilisierte Diagramm-Hintergründe: ein Baustein je Motiv, damit man ohne Text sieht, was oben/unten bedeutet.
   AFdeco.draw(motiv, f) liefert SVG-Elemente, die *vor* Gitter und Punkten ins Diagramm gehören. f = Plotfläche und Skala:
   { L, T, PW, PH, W, R, Y, lo, hi } – L/T linke obere Ecke, PW/PH Breite/Höhe, W Gesamtbreite, R rechter Rand. Die Randsymbole stehen
   im rechten Rand (R ≥ 24), damit sie keine Punkte verdecken – Diagramme mit Motiv nehmen dafür `AFdeco.R` als rechten Rand.
   Motive:
     'sky'     oben dunkler Himmel mit Sternen, unten Dunst/Dämmerung; Stern- und Mondsymbol; Orientierungsmarken, wenn sie in die Skala fallen
     'seeing'  aufgeblähter und scharfer Stern am Rand (f.sharpTop: scharf oben, sonst unten)
     'depth'   oben viele schwache, unten wenige helle Sterne (Grenzgröße: oben = tiefer)
     'stars'   Sternfeld nur über den Zeitspannen f.spans = [[x0, x1], …] (z. B. astronomisch dunkel), ohne Randsymbole
   AFdeco.legend([{ cls, r, text, title }]) – einzeilige Legende mit echten Diagrammpunkten (Kreis mit der Klasse des Diagramms).
   Farben nur über CSS-Klassen (chartdeco.css, Tokens aus :root); keine Animation, pointer-events aus. */
(() => {
let seq = 0;
const f1 = v => (+v).toFixed(1);
// grobe Richtwerte in mag/″² (Bortle ~3 / ~5 / ~8) – erscheinen nur innerhalb der Skala
const SKY_REF = [[21.5, 'Landhimmel'], [20, 'Vorstadt'], [18, 'Stadt']];
const de1 = v => Number(v).toLocaleString('de-DE', { minimumFractionDigits: 1, maximumFractionDigits: 1 });

// feste Sternlagen (Anteile der Fläche, Radius) – kein Zufall, damit sich das Bild beim Neuzeichnen nicht ändert
const STARS = [[.06, .1, .8], [.17, .28, .55], [.29, .08, .6], [.41, .2, .9], [.53, .06, .5], [.64, .26, .65], [.76, .12, .8], [.88, .3, .5],
  [.95, .07, .6], [.23, .42, .45], [.71, .4, .45]];
const FIELD = [[.04, .06], [.11, .19], [.19, .04], [.26, .14], [.33, .27], [.38, .07], [.46, .17], [.52, .33], [.58, .05], [.63, .21], [.69, .12],
  [.75, .3], [.81, .04], [.86, .18], [.93, .1], [.97, .26], [.08, .38], [.15, .5], [.31, .45], [.44, .58], [.57, .48], [.67, .62], [.79, .52],
  [.9, .44], [.22, .7], [.49, .8], [.84, .76]];

function sky(f, id) {
  const { L, T, PW, PH, W, R, Y, lo, hi } = f, xs = W - R / 2, x2 = L + PW, my = T + PH - 10;
  const stars = STARS.map(([fx, fy, r]) => `<circle cx="${f1(L + fx * PW)}" cy="${f1(T + fy * PH)}" r="${r}"/>`).join('');
  const refs = SKY_REF.filter(([v]) => v > lo + (hi - lo) * 0.06 && v < hi - (hi - lo) * 0.06)
    .map(([v, t]) => `<line class="cd-ref" x1="${L}" x2="${x2}" y1="${f1(Y(v))}" y2="${f1(Y(v))}"/><text class="cd-reft" x="${x2 - 3}" y="${f1(Y(v) - 3)}" text-anchor="end">${t} ~${de1(v)}</text>`).join('');
  return `<g class="cd" aria-hidden="true"><defs>
      <linearGradient id="${id}g" x1="0" y1="0" x2="0" y2="1"><stop offset="0" class="cd-sky0"/><stop offset=".5" class="cd-sky1"/><stop offset="1" class="cd-sky2"/></linearGradient>
      <radialGradient id="${id}h" cx=".5" cy=".5" r=".5"><stop offset="0" class="cd-haze0"/><stop offset="1" class="cd-clear"/></radialGradient>
      <clipPath id="${id}c"><rect x="${L}" y="${T}" width="${PW}" height="${PH}" rx="4"/></clipPath>
      <mask id="${id}m"><rect x="${xs - 9}" y="${my - 9}" width="18" height="18" fill="#fff"/><circle cx="${xs + 3}" cy="${my - 2}" r="5.5" fill="#000"/></mask></defs>
    <g clip-path="url(#${id}c)"><rect x="${L}" y="${T}" width="${PW}" height="${PH}" fill="url(#${id}g)"/>
      <ellipse cx="${f1(L + PW * .25)}" cy="${T + PH}" rx="${f1(PW * .4)}" ry="${f1(PH * .32)}" fill="url(#${id}h)"/>
      <ellipse cx="${f1(L + PW * .72)}" cy="${f1(T + PH * 1.02)}" rx="${f1(PW * .45)}" ry="${f1(PH * .26)}" fill="url(#${id}h)"/>
      <path class="cd-band" d="M${L} ${f1(T + PH * .78)}q${f1(PW * .25)} ${f1(-PH * .07)} ${f1(PW * .5)} 0t${f1(PW * .5)} 0"/>
      <path class="cd-band" d="M${L} ${f1(T + PH * .9)}q${f1(PW * .25)} ${f1(PH * .06)} ${f1(PW * .5)} 0t${f1(PW * .5)} 0"/>
      <g class="cd-star">${stars}</g></g>${refs}
    <path class="cd-spark" d="M${xs} ${T + 3}l1.4 4.2 4.2 1.4-4.2 1.4-1.4 4.2-1.4-4.2-4.2-1.4 4.2-1.4z"/>
    <circle class="cd-moon" cx="${xs}" cy="${my}" r="6.5" mask="url(#${id}m)"/></g>`;
}

// aufgeblähter (unscharfer) und scharfer Stern
const blurStar = (x, y, id) => `<circle cx="${x}" cy="${y}" r="8" fill="url(#${id}b)"/>`;
const sharpStar = (x, y) => `<path class="cd-sharp" d="M${x} ${y - 5}v10M${x - 5} ${y}h10"/><circle class="cd-dot" cx="${x}" cy="${y}" r="1.6"/>`;
function seeing(f, id) {
  const { T, PH, W, R } = f, xs = W - R / 2, top = T + 9, bot = T + PH - 8;
  return `<g class="cd" aria-hidden="true"><defs><radialGradient id="${id}b"><stop offset="0" class="cd-blur0"/><stop offset="1" class="cd-clear"/></radialGradient></defs>
    ${f.sharpTop ? sharpStar(xs, top) + blurStar(xs, bot, id) : blurStar(xs, top, id) + sharpStar(xs, bot)}</g>`;
}

// Grenzgröße: oben (tiefer) ein dichtes Feld schwacher Sterne, unten wenige helle; Randsymbole: Sternhaufen oben, heller Stern unten
function depth(f) {
  const { L, T, PW, PH, W, R } = f, xs = W - R / 2;
  const field = FIELD.map(([fx, fy]) => `<circle cx="${f1(L + fx * PW)}" cy="${f1(T + fy * PH)}" r="${fy < .35 ? .5 : .7}"/>`).join('');
  const faint = [[-6, 1, .6], [-2, 0, .5], [3, 2, .8], [6, 0, .5], [-4, 5, .7], [1, 6, .5], [5, 7, .6], [-6, 10, .5], [-1, 11, .8], [4, 12, .5], [1, 15, .6], [-4, 15, .5]]
    .map(([dx, dy, r]) => `<circle cx="${xs + dx}" cy="${T + 1 + dy}" r="${r}"/>`).join('');
  const by = T + PH - 8;
  return `<g class="cd" aria-hidden="true"><g class="cd-field">${field}</g><g class="cd-faint">${faint}</g>
    <path class="cd-bright" d="M${xs} ${by - 7}l1.8 5.2 5.2 1.8-5.2 1.8-1.8 5.2-1.8-5.2-5.2-1.8 5.2-1.8z"/></g>`;
}

// Sternfeld nur über bestimmten Spannen der x-Achse (Nachtdiagramm: dort, wo es dunkel ist); oben dichter als unten
function stars(f, id) {
  const { L, T, PW, PH, spans = [] } = f;
  if (!spans.length) return '';
  const clip = spans.map(([a, b]) => `<rect x="${f1(a)}" y="${T}" width="${f1(Math.max(0, b - a))}" height="${PH}"/>`).join('');
  const field = FIELD.map(([fx, fy]) => `<circle cx="${f1(L + fx * PW)}" cy="${f1(T + fy * PH * .8)}" r=".6"/>`).join('');
  const big = STARS.map(([fx, fy, r]) => `<circle cx="${f1(L + ((fx + .37) % 1) * PW)}" cy="${f1(T + fy * PH * .9)}" r="${r}"/>`).join('');
  return `<g class="cd" aria-hidden="true"><defs><clipPath id="${id}s">${clip}</clipPath></defs>
    <g clip-path="url(#${id}s)"><g class="cd-field">${field}</g><g class="cd-star">${big}</g></g></g>`;
}

const MOTIFS = { sky, seeing, depth, stars };
window.AFdeco = {
  R: 24,
  draw(motif, f) { const fn = MOTIFS[motif]; return fn ? fn(f, `cd${++seq}`) : ''; },
  legend: (items) => `<ul class="cd-leg sm">${items.map(i => `<li${i.title ? ` title="${esc(i.title)}"` : ''}><svg viewBox="0 0 12 12" aria-hidden="true"><circle class="${i.cls}" cx="6" cy="6" r="${i.r || 4}"/></svg>${esc(i.text)}</li>`).join('')}</ul>`,
};
})();
