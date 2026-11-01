"""Faint common star tails must be rejected without discarding independent streaks."""
from pathlib import Path
import os
SAMPLES=os.environ.get('SKYTRACE_SAMPLES','Stellina')  # Stammordner mit echten Stellina-Beobachtungen
import cv2
import numpy as np
from analyse import load_image
from frame_quality import measure_quality,assess_quality

rng=np.random.default_rng(81);yy,xx=np.mgrid[:480,:640]
sharp=np.full((480,640),100.,dtype='float32')
tails=np.zeros_like(sharp)
for y in range(50,440,65):
    for x in range(50,600,70):
        sharp+=6000*np.exp(-((xx-x)**2+(yy-y)**2)/(2*1.2**2))
        points=np.asarray([[x-28,y-3],[x-15,y-7],[x,y]],dtype='int32')
        cv2.polylines(tails,[points],False,200,2)
sharp+=rng.normal(0,1,sharp.shape).astype('float32')
assert not assess_quality(measure_quality(sharp))['excluded']
metrics=measure_quality(sharp+tails);print('faint common tails',metrics)
assert assess_quality(metrics)['excluded'],metrics
several=sharp.copy()
for start,end in [((30,90),(600,350)),((30,130),(600,390)),((30,170),(600,430))]:cv2.line(several,start,end,800,2)
assert not assess_quality(measure_quality(several))['excluded'],'Three real lines must not be mistaken for whole-field shake'
cases=[(SAMPLES+r'\2022-09-22_20-18-16_observation_IC59\01-images-initial\img-0114r.fits',True),
       (SAMPLES+r'\2022-09-22_20-18-16_observation_IC59\01-images-initial\img-0113r.fits',False),
       (SAMPLES+r'\2022-10-27_18-20-29_observation_NGC884_869\01-images-initial\img-0031r.fits',False),
       (SAMPLES+r'\2022-05-06_19-55-42_observation_M82\01-images-initial\img-0019.fits',False)]
for name,excluded in cases:
    path=Path(name)
    if not path.exists():continue
    data,_=load_image(path);quality=assess_quality(measure_quality(data))
    assert quality['excluded']==excluded,(path.name,quality)
print('OK: faint global tails rejected; sharp fields and multiple actual streaks retained; actual IC59 burst rejected, NGC884 and M82 preserved')
