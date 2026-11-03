"""Blinkende Flugzeugspuren (synthetisch): gestrichelt und mit Knoten erkennen; Meteor, Rauschen, Sternketten nicht."""
import cv2
import numpy as np

from line_detection import detect_lines


def frame(seed, h=1040, w=1536):
    rng = np.random.default_rng(seed)
    img = rng.normal(1000, 40, (h, w)).astype(np.float32)
    for y, x in rng.integers(20, [h - 20, w - 20], (160, 2)):          # Sterne
        img[y - 2:y + 3, x - 2:x + 3] += rng.uniform(300, 4000)
    return cv2.GaussianBlur(img, (0, 0), .9)


def draw(img, p1, p2, amp, pattern=None):
    p1, p2 = np.array(p1, float), np.array(p2, float)
    n = int(np.linalg.norm(p2 - p1))
    layer = np.zeros_like(img)
    for i in range(n):
        if pattern and not pattern(i):
            continue
        x, y = p1 + (p2 - p1) * i / n
        cv2.circle(layer, (int(x), int(y)), 1, 1.0, -1)
    return img + cv2.GaussianBlur(layer, (0, 0), 1.0) * amp


def run(img, seed):
    return detect_lines(img, frame(seed), np.array([[1., 0, 0], [0, 1., 0]]))


results = {}
dashed = draw(frame(1), (200, 150), (900, 700), 600, lambda i: i % 70 < 26)
results['gestrichelt'] = run(dashed, 101)
knots = draw(draw(frame(2), (300, 800), (1300, 300), 250), (300, 800), (1300, 300), 1500, lambda i: i % 90 < 10)
results['Knoten'] = run(knots, 102)
meteor = draw(frame(3), (400, 200), (1100, 600), 500)
results['durchgehend'] = run(meteor, 103)
irregular = draw(frame(4), (200, 900), (1200, 500), 600, lambda i: i % 300 < 26 or 120 < i % 300 < 146 or 150 < i % 300 < 170)
results['unregelmaessig'] = run(irregular, 104)
noise = [run(frame(10 + k), 200 + k) for k in range(6)]

planes = {k: [c for c in v if c.get('blink')] for k, v in results.items()}
for k, v in results.items():
    print(k, [(c['kind'], c['line_snr'], c.get('blink')) for c in v])
print('Rauschen', [len(r) for r in noise])
assert planes['gestrichelt'] and planes['gestrichelt'][0]['blink']['form'] == 'gestrichelt' and planes['gestrichelt'][0]['blink']['flashes'] >= 5
assert len(results['gestrichelt']) == 1, 'Randblitz gehört zur selben Flugzeugspur'
assert len(planes['Knoten']) == 1 and planes['Knoten'][0]['blink']['form'] == 'Knoten', 'keine zweite Linie quer durch die Knoten'
assert results['durchgehend'] and not planes['durchgehend'], 'durchgehende Spur bleibt Meteor/Satellit-Kandidat'
assert not planes['unregelmaessig'], 'unregelmäßige Lücken sind kein Blinktakt'
assert not any(noise), 'Rauschen und Sterne erzeugen keine Spuren'
print('OK: gestrichelte und geknotete Blinkspuren erkannt; durchgehende, unregelmäßige Spuren und Rauschen nicht als Flugzeug')
