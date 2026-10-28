"""Meaningful integration checks with synthetic data; originals stay read-only."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import numpy as np
from astropy.io import fits
import cv2
from analyse import measure, session

base = Path(__file__).resolve().parent
y, x = np.mgrid[:128, :128]
rng = np.random.default_rng(42)
data = 100 + rng.normal(0, 1, x.shape)
for cx, cy in [(25,25), (60,65), (100,100)]:
    data += 500*np.exp(-((x-cx)**2+(y-cy)**2)/8)
m = measure(data.astype('float32'))
assert m['stars'] == 3, m
assert abs(m['fwhm'] - 4.71) < 1, m
assert session(Path('C:/test/2022_observation_M82/sub/a.fits'), Path('C:/test/2022_observation_M82')) == '2022_observation_M82'
print('Synthetische Sternzahl / FWHM / Beobachtungsgruppe OK')

with tempfile.TemporaryDirectory(dir=base) as temp:
    temp = Path(temp)
    source = temp/'source'; source.mkdir()
    for i in range(4):
        fits.writeto(source/f'image-{i}.fits', data.astype('float32'))
    output = temp/'stopped'
    cmd = [sys.executable, '-u', str(base/'analyse.py'), '--source', str(source), '--output', str(output), '--samples', '0']
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, text=True, encoding='utf-8')
    for line in proc.stdout:
        event = json.loads(line)
        if event['type'] == 'result':
            (output/'STOP').touch()
    assert proc.wait() == 0
    rows = (output/'messwerte.jsonl').read_text().splitlines()
    assert 1 <= len(rows) < 4, len(rows)
    assert (output/'bericht.html').is_file()
    print('Kooperativer Stopp / gespeicherter Teilbericht OK:', len(rows))

    video = source/'blank.avi'
    writer = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*'MJPG'), 2, (320, 240))
    assert writer.isOpened()
    for _ in range(2): writer.write(np.zeros((240,320,3),dtype='uint8'))
    writer.release()
    completed = subprocess.run([sys.executable, '-u', str(base/'analyse.py'), '--source', str(source), '--output', str(temp/'video'), '--mode', 'Videos'], capture_output=True, text=True, encoding='utf-8')
    assert completed.returncode == 0, completed.stderr + completed.stdout
    events = []
    for line in completed.stdout.splitlines():
        try: events.append(json.loads(line))
        except ValueError: pass
    assert any(e.get('type') == 'video' for e in events), events
    assert events[-1]['type'] == 'done', events
    assert (temp/'video/messwerte.jsonl').read_text() == ''
    print('Video-Decoding / echtes OpenSkyCam-Modell / Live-Ereignisse OK')
