"""Eichung der Blinkspur-Erkennung: synthetische Blinkspuren in echte Aufnahmen setzen, Fehlalarme über die umgekehrte Differenz messen.

Methode nach JTransient (Petros Pissias, BSD-2-Clause; nur die Beschreibung in ALGORITHM.md nachgebaut, kein Code übernommen):
1. **Einsetzen:** in eine echte Aufnahme (Bildpaar mit Registrierung aus `frames.jsonl`, ohne Erkennung im Lauf) eine gestrichelte Spur mit
   Blitzzahl, Takt, Blitzlänge, Helligkeit (Vielfaches des Hintergrundrauschens), Breite (Gauß-σ) und Farbe der Positionslichter
   (Gewicht nach Bayer-2×2-Binning: weiß 1, grün ½, rot ¼) setzen und `detect_lines` laufen lassen → Wiederfindungsrate.
2. **Negativkontrolle:** dieselben Paare mit vertauschter Rolle (früheres Bild − späteres): echte Objekte des späteren Bilds werden
   negativ, jede gefundene Blinkspur ist ein Fehlalarm. Zusätzlich vorwärts ohne Einsetzen (neue Treffer = mögliche Fehlalarme, ansehen).
Erkannt wird einmal großzügig (3 Blitze, Score 4, Takt 25 %); die Schwellen-Kombinationen werden danach aus den gespeicherten
Blinkwerten bewertet. Die gewählte Einstellung danach direkt mit den Schwellen aus `line_detection` bestätigen (`--direct`).

Aufruf (Engine-Interpreter, `SKYTRACE_HOME` gesetzt; das Werkzeug liest nur und schreibt nichts in die Ordner). Kopierte Einzelpaare:
`frames.jsonl` mit je zwei Zeilen, die zweite trägt `_previous_file` = Pfad der ersten.
  python probe_blink_injection.py <Beobachtungsordner mit frames.jsonl> [...] --pairs 20 --trials 8 --out eichung.json [--direct]
"""
import argparse
import itertools
import json
import math
import time
from pathlib import Path

import cv2
import numpy as np

import line_detection
from analyse import load_image
from line_detection import detect_lines

COLORS = {'weiß': 1., 'grün': .5, 'rot': .25}          # Anteil am Mittel eines RGGB-Blocks (Bayer-Binning der Analyse)
PERMISSIVE = dict(BLINK_MIN_FLASHES=3, BLINK_SCORE=4., BLINK_SCORE_FEW=4., BLINK_REGULARITY=.25)
GRID = dict(min_flashes=(3, 4, 5), score_few=(8., 10., 12.), regularity=(.08, .12, .18))
SCORE = 8.                                              # ab 5 Blitzen (BLINK_SCORE)


def _set(values):
    old = {k: getattr(line_detection, k) for k in values}
    for k, v in values.items():
        setattr(line_detection, k, v)
    return old


def pairs(folder, count):
    rows = [json.loads(line) for line in (folder / 'frames.jsonl').read_text(encoding='utf-8').splitlines() if line.strip()]
    found = []
    for previous, current in zip(rows, rows[1:]):
        if current.get('previous_to_current') is None or previous.get('detections') or current.get('detections'):
            continue
        if current.get('_previous_file') not in (None, previous['file']):     # Kopien einzelner Bildpaare (siehe Kopfkommentar)
            continue
        if not (Path(previous['file']).is_file() and Path(current['file']).is_file()):
            continue
        found.append((previous, current))
    if len(found) > count:
        found = [found[int(i)] for i in np.linspace(0, len(found) - 1, count)]
    return found


def noise(data):
    sample = data[::2, ::2] - cv2.GaussianBlur(data[::2, ::2], (0, 0), 6)
    return max(1e-3, float(1.4826 * np.median(np.abs(sample - np.median(sample)))))


def inject(data, rng, flashes, period, dash, amplitude, sigma, color):
    """Gestrichelte Spur (Blitze im festen Takt) einsetzen; Rückgabe (Bild, Strecke, Parameter)."""
    h, w = data.shape
    span = (flashes - 1) * period + dash
    for _ in range(200):
        angle = rng.uniform(0, math.pi)
        if min(abs(math.cos(angle)), abs(math.sin(angle))) < .05:  # nicht entlang der Sensorachsen (Auslesezeilen werden verworfen)
            continue
        d = np.array([math.cos(angle), math.sin(angle)])
        start = rng.uniform([40, 40], [w - 40, h - 40])
        end = start + d * span
        if 40 <= end[0] <= w - 40 and 40 <= end[1] <= h - 40:
            break
    else:
        return None
    layer = np.zeros_like(data, dtype=np.float32)
    for k in range(flashes):
        a = start + d * k * period
        b = a + d * dash
        cv2.line(layer, tuple(int(round(v * 8)) for v in a), tuple(int(round(v * 8)) for v in b), 1., 1, cv2.LINE_AA, shift=3)
    layer = cv2.GaussianBlur(layer, (0, 0), sigma)
    peak = float(layer.max()) or 1.
    out = data + layer / peak * amplitude * COLORS[color]
    return out, (start, end), dict(flashes=flashes, period=round(period, 1), dash=round(dash, 1), amplitude_sigma=round(amplitude / noise(data), 2),
                                   sigma=sigma, color=color)


def matches(candidate, segment, scale_tolerance=8.):
    a, b = (np.asarray(p, dtype=float) for p in segment)
    d = (b - a) / max(np.linalg.norm(b - a), 1e-9)
    p, q = (np.asarray(v, dtype=float) for v in candidate['segment'])
    e = (q - p) / max(np.linalg.norm(q - p), 1e-9)
    if abs(float(d @ e)) < math.cos(math.radians(3)):
        return False
    normal = np.array([-d[1], d[0]])
    return abs(float(((p + q) / 2 - a) @ normal)) < scale_tolerance


def blink_values(candidates):
    return [dict(c['blink'], line_snr=c.get('line_snr')) for c in candidates if c.get('blink')]


def passes(blink, min_flashes, score_few, regularity):
    """Regel wie `line_detection.blink_pattern`: ab 5 Blitzen Score ≥ SCORE, darunter ≥ score_few und jeder Abstand im Takt."""
    if blink['flashes'] < min_flashes or blink['regularity'] > regularity:
        return False
    if blink['flashes'] >= 5:
        return blink['score'] >= SCORE
    return blink['score'] >= score_few and blink.get('max_deviation', 0) <= regularity


def run(folders, pair_count, trials, seed, direct=False):
    rng = np.random.default_rng(seed)
    injected, negatives, forwards = [], [], []
    old = _set({} if direct else PERMISSIVE)
    started = time.monotonic()
    try:
        for folder in folders:
            for previous, current in pairs(folder, pair_count):
                prev, _ = load_image(Path(previous['file']))
                cur, meta = load_image(Path(current['file']))
                transform = np.asarray(current['previous_to_current'], dtype=float)
                inverse = cv2.invertAffineTransform(transform)
                where = dict(folder=folder.name, frame=current['index'] + 1)
                negatives.append(dict(where, blinks=blink_values(detect_lines(prev, cur, inverse)),
                                      exposure=float(meta.get('exposure_seconds') or 10.)))
                forwards.append(dict(where, blinks=blink_values(detect_lines(cur, prev, transform))))
                sigma_noise = noise(cur)
                scale = min(1., 768 / cur.shape[1])
                for _ in range(trials):
                    flashes = int(rng.integers(2, 7))
                    period = float(rng.uniform(60, 220)) / scale              # 60–220 Abtastpunkte im 768-px-Profil
                    dash = float(rng.uniform(.06, .25)) * period
                    amplitude = float(rng.choice([3, 5, 8, 12, 20, 35])) * sigma_noise
                    color = str(rng.choice(list(COLORS)))
                    made = inject(cur, rng, flashes, period, dash, amplitude, float(rng.uniform(.8, 1.6)), color)
                    if made is None:
                        continue
                    image, segment, params = made
                    found = [c for c in detect_lines(image, prev, transform) if matches(c, segment)]
                    injected.append(dict(where, params=params, blinks=blink_values(found), line=bool(found)))
                print(f'{folder.name} Bild {current["index"] + 1}: {time.monotonic() - started:.0f} s', flush=True)
    finally:
        _set(old)
    return dict(injected=injected, negatives=negatives, forwards=forwards)


def evaluate(data):
    """Wiederfindung je Blitzzahl/Helligkeit und Fehlalarme je Schwellen-Kombination."""
    rows = []
    hours = sum(n['exposure'] for n in data['negatives']) / 3600 or 1e-9
    for min_flashes, score, regularity in itertools.product(*GRID.values()):
        ok = lambda b: passes(b, min_flashes, score, regularity)      # score = Score für 3–4 Blitze
        hit = {}
        for item in data['injected']:
            key = (item['params']['flashes'], item['params']['amplitude_sigma'] >= 8)
            hit.setdefault(key, []).append(any(ok(b) for b in item['blinks']))
        false = sum(any(ok(b) for b in n['blinks']) for n in data['negatives'])
        new = sum(any(ok(b) for b in f['blinks']) for f in data['forwards'])
        rows.append(dict(min_flashes=min_flashes, score_few=score, regularity=regularity,
                         recovery={f'{k[0]} Blitze, {"hell" if k[1] else "schwach"}': round(float(np.mean(v)), 3) for k, v in sorted(hit.items())},
                         recovery_3plus_bright=round(float(np.mean([any(ok(b) for b in i['blinks']) for i in data['injected']
                                                                     if i['params']['flashes'] >= 3 and i['params']['amplitude_sigma'] >= 8] or [0])), 3),
                         false_alarm_pairs=false, false_alarms_per_hour=round(false / hours, 3), new_forward=new, pairs=len(data['negatives'])))
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('folders', nargs='+', type=Path)
    parser.add_argument('--pairs', type=int, default=20)
    parser.add_argument('--trials', type=int, default=8)
    parser.add_argument('--seed', type=int, default=1)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--direct', action='store_true', help='Schwellen aus line_detection statt großzügig erkennen (Bestätigung)')
    args = parser.parse_args()
    data = run([f.resolve() for f in args.folders], args.pairs, args.trials, args.seed, args.direct)
    data['grid'] = evaluate(data)
    data['current'] = dict(BLINK_MIN_FLASHES=line_detection.BLINK_MIN_FLASHES, BLINK_SCORE=line_detection.BLINK_SCORE,
                           BLINK_SCORE_FEW=line_detection.BLINK_SCORE_FEW,
                           BLINK_REGULARITY=line_detection.BLINK_REGULARITY)
    args.out.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding='utf-8')
    for row in data['grid']:
        print(row['min_flashes'], row['score_few'], row['regularity'], 'Wiederfindung ≥3 Blitze hell', row['recovery_3plus_bright'],
              'Fehlalarme', row['false_alarm_pairs'], '/', row['pairs'], f"({row['false_alarms_per_hour']}/h)", 'neu vorwärts', row['new_forward'])


if __name__ == '__main__':
    main()
