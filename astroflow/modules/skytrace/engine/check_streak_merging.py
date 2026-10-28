import json
from pathlib import Path
import cv2
import numpy as np
from analyse import load_image
from sequence import stretch
from line_detection import detect_lines, merge_streak_detections

def line(a,b):
    return dict(kind='trail',segment=[a,b],box=[0,0,1,1],confidence=None)

lines=[line([40,40],[440,440]),line([40,140],[340,440])]
fragments=[dict(kind='meteor',box=[.1,.1,.3,.3],confidence=.8),dict(kind='meteor',box=[.6,.6,.85,.85],confidence=.6),dict(kind='plane',box=[.1,.3,.3,.5],confidence=.7)]
result=merge_streak_detections(fragments,lines,(480,480))
assert len(result)==2 and [len(c.get('model_hints',[])) for c in result]==[2,1]
assert all(c['kind']=='trail' and c['confidence'] is None for c in result)
unrelated=dict(kind='meteor',box=[.7,.05,.9,.2],confidence=.9)
assert unrelated in merge_streak_detections([unrelated],lines,(480,480))
assert len(merge_streak_detections([],[line([40,40],[440,440]),line([40,440],[440,40])],(480,480)))==2
rng=np.random.default_rng(481)
base=rng.normal(30,3,(480,640)).astype('float32');bright=base.copy()
cv2.line(bright,(100,40),(520,440),400,2);cv2.line(bright,(220,40),(640,440),300,2)
assert len(detect_lines(bright))==2
assert not detect_lines(base,bright,np.eye(2,3)),'Disappearing bright trails created ghosts'
print('OK: fragments merged; distinct parallel/crossing trails and unrelated candidates retained; bright trails detected; disappearing-trail ghosts rejected')

root=Path(__file__).resolve().parent.parent/'Ergebnisse/2026-10-27_20-59-42-652459'
summary=json.loads((root/'videos.json').read_text(encoding='utf-8'))[0]
rows=[json.loads(s) for s in (root/Path(summary['video']).parent/'frames.jsonl').read_text(encoding='utf-8').splitlines()]
for index in (30,31):
    pos=next(i for i,r in enumerate(rows) if r['index']==index)
    row=rows[pos]
    data,_=load_image(Path(row['file'])); prev,_=load_image(Path(rows[pos-1]['file']))
    gray=stretch(data,summary['display_limits'])
    linear=cv2.resize(data,(gray.shape[1],gray.shape[0]),interpolation=cv2.INTER_AREA)
    prev=cv2.resize(prev,(gray.shape[1],gray.shape[0]),interpolation=cv2.INTER_AREA)
    lines=detect_lines(linear,prev,np.asarray(row['previous_to_current']))
    merged=merge_streak_detections([d for d in row['detections'] if d.get('line_snr') is None],lines,gray.shape)
    print(index,'lines',len(lines),'merged',len(merged),'hints',[len(d.get('model_hints',[])) for d in merged],flush=True)
    assert len(merged)==(2 if index==30 else 0)
    if index==30: assert sorted(len(d['model_hints']) for d in merged)==[4,4]
