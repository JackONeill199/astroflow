"""Vollbilder und Zeitbezug: Originalnummern enthalten ausgelassene Aufnahmen, Videozeiten nicht."""
import json
from pathlib import Path
from fastapi.testclient import TestClient

from astroflow.core.config import load_settings
from astroflow.core.store import SettingsStore
from astroflow.server import create_app


def test_full_frames_and_exact_video_time(tmp_path, monkeypatch):
    monkeypatch.setenv('ASTROFLOW_MODULES','stacking,skytrace')
    home=tmp_path/'skytrace';run=home/'Ergebnisse'/'testlauf';folder=run/'obs';folder.mkdir(parents=True)
    for file in ('crop.png','full.jpg','context.jpg'):(folder/file).write_bytes(b'bild')
    frames=[dict(index=i,image='obs/crop.png',width=20,height=30,hit=i==3,excluded=i==2,video_seconds=999) for i in (1,2,3)]
    frames[0]['full_image']=dict(image='obs/full.jpg',width=160,height=120)
    candidate=dict(id='K0001',session='Test',frames=frames,crop=[30,40,50,70],context_image=dict(image='obs/context.jpg',frame=3,width=160,height=120))
    (folder/'kandidaten.json').write_text(json.dumps([candidate]),encoding='utf-8')
    manifest=folder/'frames.jsonl'
    manifest.write_text('\n'.join(json.dumps(r) for r in [dict(index=0,video_seconds=0),dict(index=2,video_seconds=.125)]),encoding='utf-8')
    (run/'videos.json').write_text(json.dumps([dict(session='Test',video='obs/ereignisse.mp4',frames=2,candidates_file='obs/kandidaten.json')]),encoding='utf-8')
    s=load_settings(tmp_path/'data');SettingsStore(s.data_dir/'settings.json').update('skytrace',{'home':str(home)})
    c=TestClient(create_app(s))
    data=c.get('/api/skytrace/run',params={'id':run.name}).json()['candidates'][0]['frames']
    assert data[0]['video_seconds']==0 and data[2]['video_seconds']==.125
    assert 'video_seconds' not in data[1]                 # ausgeschlossen: nie zu einem falschen Videobild springen
    assert data[0]['full_image']['width']==160
    assert c.get(data[0]['full_image']['url']).content==b'bild'
    assert data[2]['full_image']['url'].endswith('/obs/context.jpg')  # alte Trefferbilder bleiben nutzbar
    assert 'full_image' not in data[1]
    manifest.write_text(json.dumps(dict(index=2,video_seconds=.375))+'\n',encoding='utf-8')
    fresh=c.get('/api/skytrace/run',params={'id':run.name}).json()['candidates'][0]['frames']
    assert fresh[2]['video_seconds']==.375               # aktualisierte Aufnahmen invalidieren den Cache
    c.close()
