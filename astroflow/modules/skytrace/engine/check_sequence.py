"""Detection regression checks: stationary stars, shifted fields, trails, time order, MP4 finalization."""
from pathlib import Path
import json
import tempfile
import numpy as np
import cv2
from astropy.io import fits
from sequence import register, streaks, transient_shape, MovieWriter, timestamp, discover, annotate, PersistentTrails

rng=np.random.default_rng(12)
image=rng.normal(20,1,(480,640)).clip(0,255).astype('uint8')
for _ in range(100):
    x,y=rng.integers(25,615),rng.integers(25,455)
    cv2.circle(image,(int(x),int(y)),2,190,-1)
image=cv2.GaussianBlur(image,(3,3),.6)
affine=np.array([[1.,0.,3.],[0.,1.,-2.]])
shifted=cv2.warpAffine(image,affine,(640,480),borderMode=cv2.BORDER_REFLECT)
matrix,inliers=register(image,shifted)
assert matrix is not None and inliers>20, inliers
assert np.max(np.abs(matrix-affine))<.3,matrix
assert not streaks(shifted,image,matrix)
assert not transient_shape(shifted,image,matrix,[.1,.1,.8,.8])
trailed=shifted.copy(); cv2.line(trailed,(150,250),(400,300),240,3)
assert streaks(trailed,image,matrix), 'Injected meteor-like trail lost'
assert transient_shape(trailed,image,matrix,[.2,.45,.68,.68])
print('Sternregistrierung / statisches Feld unterdrückt / transiente Spur erkannt OK')

history=PersistentTrails()
blank=np.zeros((240,320),dtype='uint8')
hit=dict(kind='meteor',label='Test',confidence=.9,box=[.2,.4,.6,.5],segment=[[65,105],[190,115]])
history.update(blank,[hit],None,0)
first=history.render(blank)
assert first[110,125].max()>100
history.update(blank,[],np.array([[1.,0.,10.],[0.,1.,5.]]),1)
for frame in range(2,20): history.update(blank,[],np.array([[1.,0.,0.],[0.,1.,0.]]),frame)
last=history.render(blank)
assert len(history.records)==1 and last[115,135].max()>100
assert history.records[0]['first_frame']==0
print('Einzelbild-Erkennung bleibt über 20 Frames als Leuchtspur sichtbar und folgt der Sternregistrierung OK')

with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as temp:
    root=Path(temp); session=root/'2022_observation_M82'; initial=session/'01-images-initial'; initial.mkdir(parents=True)
    for i,seconds in [(10,'02'),(2,'01')]:
        header=fits.Header(); header['DATE-OBS']=f'2022-05-06T20:00:{seconds}'
        fits.writeto(initial/f'img-{i}.fits',image,header)
    fits.writeto(session/'stack.fits',image)
    groups=discover(root)
    assert len(groups)==1 and len(groups[0][1])==2,groups
    ordered=sorted(groups[0][1],key=lambda p:timestamp(p)[0])
    assert ordered[0].name=='img-2.fits'
    path=root/'test.mp4'
    movie=MovieWriter(path,8,image.shape)
    for i in range(3): movie.write(annotate(image,[], 'Synthetic test', '2022-05-06T20:00:01 UTC',i,3,True,8))
    movie.release()
    cap=cv2.VideoCapture(str(path))
    assert int(cap.get(cv2.CAP_PROP_FRAME_COUNT))==3
    ok,frame=cap.read(); assert ok and frame.shape[:2]==image.shape
    cap.release()
    import av
    with av.open(str(path)) as container: assert container.streams.video[0].codec_context.name=='h264'
    print('Nur Originalserie / Aufnahmezeit-Sortierung / H.264 MP4 mit Overlay dekodierbar OK')
