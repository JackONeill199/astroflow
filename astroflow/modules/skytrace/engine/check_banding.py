"""Readout-noise regression, including the exact IC405 false detections."""
import json
from pathlib import Path
import cv2
import numpy as np
from analyse import load_image
from line_detection import detect_lines

rng=np.random.default_rng(93)
base=rng.normal(30,3,(480,640)).astype('float32')
for start,end in [((40,100),(580,390)),((40,200),(580,200)),((320,30),(320,450))]:
    image=base.copy();cv2.line(image,start,end,34.5,2,cv2.LINE_AA)
    assert detect_lines(image),(start,end,'single frame')
    comparison=rng.normal(30,3,image.shape).astype('float32')
    assert detect_lines(image,comparison,np.eye(2,3)),(start,end,'temporal')
print('OK: faint diagonal, horizontal and vertical finite trails preserved')
for axis in (0,1):
    previous=rng.normal(30,3,base.shape).astype('float32')
    current=rng.normal(30,3,base.shape).astype('float32')
    if axis==0:previous[160]-=12;current[160]+=5
    else:previous[:,320]-=12;current[:,320]+=5
    matrix=np.array([[1.,.0005,.8],[-.0005,1.,.4]])
    assert not detect_lines(current,previous,matrix),axis
print('OK: row/column noise with exposure changes and star-field registration rejected')
root=Path(__file__).resolve().parent.parent/'Ergebnisse/2026-10-27_17-50-17-579414'
folders=list(root.glob('*IC405*'))
if folders:
    rows=[json.loads(s) for s in (folders[0]/'frames.jsonl').read_text(encoding='utf-8').splitlines()]
    for index in (120,146,179,445,446):
        pos=next(i for i,row in enumerate(rows) if row['index']==index)
        current,_=load_image(Path(rows[pos]['file']));previous,_=load_image(Path(rows[pos-1]['file']))
        assert not detect_lines(current,previous,np.asarray(rows[pos]['previous_to_current'])),index+1
    print('OK: actual IC405 false trails in frames 121, 147, 180, 446 and 447 removed')
    pos=next(i for i,row in enumerate(rows) if row['index']==396)
    current,_=load_image(Path(rows[pos]['file']));previous,_=load_image(Path(rows[pos-1]['file']))
    assert detect_lines(current,previous,np.asarray(rows[pos]['previous_to_current']))
    print('OK: actual diagonal IC405 trail in frame 397 retained')
folders=list((Path(__file__).resolve().parent.parent/'Ergebnisse/M82-Sternfeld-Video').glob('*M82*'))
if folders:
    rows=[json.loads(s) for s in (folders[0]/'frames.jsonl').read_text(encoding='utf-8').splitlines()]
    pos=next(i for i,row in enumerate(rows) if row['index']==18)
    current,_=load_image(Path(rows[pos]['file']));previous,_=load_image(Path(rows[pos-1]['file']))
    assert detect_lines(current,previous,np.asarray(rows[pos]['previous_to_current']))
    print('OK: actual M82 single-exposure trail in frame 19 retained')
