"""Independent real multi-trails must survive burst and curvature safeguards."""
import cv2
import numpy as np
from trail_validation import straightness,validate_detections

rng=np.random.default_rng(402)
base=rng.normal(100,1,(300,400)).astype('float32')
line=base.copy();cv2.line(line,(40,100),(350,100),140,2)
proposal=dict(kind='meteor',box=[.05,.18,.95,.6],confidence=.9)
print('straight',straightness(line,proposal))
assert not straightness(line,proposal)['curved']
curved=base.copy();points=np.asarray([[x,90+.0008*(x-195)**2] for x in range(40,351)],dtype='int32')
cv2.polylines(curved,[points],False,140,2)
measurement=straightness(curved,proposal);print('curved',measurement)
assert measurement['status']=='measured' and measurement['curved'],measurement
kept,rejected,context=validate_detections(curved,[proposal],{})
assert not kept and len(rejected)==1
multi=[];image=base.copy()
for x,y in [(40,40),(220,40),(40,190),(220,190)]:
    cv2.line(image,(x,y),(x+110,y),140,2)
    multi.append(dict(kind='meteor',box=[(x-10)/400,(y-15)/300,(x+120)/400,(y+15)/300],confidence=.8))
kept,rejected,context=validate_detections(image,multi,{})
assert len(kept)==4 and not rejected,'Simultaneous independent straight trails must survive'
assert context['simultaneous_detections']==4 and not context['star_supported_burst']
quality=dict(tail_stars=15,tail_fraction=.3,tail_direction_coherence=.9,tail_quadrants=4)
kept,rejected,context=validate_detections(image,multi,quality)
assert not kept and len(rejected)==4 and context['star_supported_burst']
protected=dict(multi[0],line_snr=20,segment=[[40,40],[150,40]])
kept,rejected,_=validate_detections(image,multi+[protected],quality)
assert protected in kept,'A separately measured real line must not be removed by proposal count'
print('OK: curved ridge rejected; simultaneous straight trails retained without independent stellar evidence; verified lines protected')
