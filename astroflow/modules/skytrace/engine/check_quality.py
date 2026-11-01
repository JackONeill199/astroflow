"""Reject exposure blur while preserving sharp translated fields and a single trail."""
from pathlib import Path
import os
SAMPLES=os.environ.get('SKYTRACE_SAMPLES','Stellina')  # Stammordner mit echten Stellina-Beobachtungen
from types import SimpleNamespace
from contextlib import redirect_stdout
import io
import json
import tempfile
import hashlib
import cv2
import numpy as np
from astropy.io import fits
from frame_quality import measure_quality,assess_quality,baseline_from_metrics

rng=np.random.default_rng(32)
height,width=480,640
yy,xx=np.mgrid[:height,:width]
sharp=np.full((height,width),100.,dtype='float32')
for y in range(40,450,65):
    for x in range(40,610,70):
        sharp+=600*np.exp(-((xx-x)**2+(yy-y)**2)/(2*1.3**2))
sharp+=rng.normal(0,2,sharp.shape).astype('float32')
metrics=measure_quality(sharp); baseline=baseline_from_metrics([metrics])
assert not assess_quality(metrics,baseline)['excluded']
translated=cv2.warpAffine(sharp,np.array([[1.,0.,8.],[0.,1.,-5.]]),(width,height),borderMode=cv2.BORDER_REFLECT)
assert not assess_quality(measure_quality(translated),baseline)['excluded']
double=.5*sharp+.5*cv2.warpAffine(sharp,np.array([[1.,0.,7.],[0.,1.,3.]]),(width,height),borderMode=cv2.BORDER_REFLECT)
assert assess_quality(measure_quality(double),baseline)['excluded']
smear=np.mean([cv2.warpAffine(sharp,np.array([[1.,0.,float(dx)],[0.,1.,0.]]),(width,height),borderMode=cv2.BORDER_REFLECT) for dx in range(-6,7)],axis=0)
assert assess_quality(measure_quality(smear),baseline)['excluded']
defocused=cv2.GaussianBlur(sharp,(0,0),2.2)
assert assess_quality(measure_quality(defocused),baseline)['excluded']
single=sharp.copy(); cv2.line(single,(30,90),(600,350),500,2)
assert not assess_quality(measure_quality(single),baseline)['excluded'],'Einzelner echter Streifen darf nicht als globale Verwacklung gelten'
print('Scharf/verschoben/eine Spur behalten; globale Doppelbilder, Schmierbewegung und Unschärfe ausgeschlossen OK')

root=Path(SAMPLES,'2022-08-07_20-32-32_observation_NGC6823/01-images-initial')
if root.is_dir():
    from analyse import load_image
    for index,excluded in [(1,False),(8,True),(9,True),(12,True),(13,False),(14,True)]:
        data,_=load_image(root/f'img-{index:04d}r.fits')
        assert assess_quality(measure_quality(data),{'fwhm':2.265})['excluded']==excluded,index
    data,_=load_image(Path(SAMPLES,'2022-05-06_19-55-42_observation_M82/01-images-initial/img-0019.fits'))
    assert not assess_quality(measure_quality(data),{'fwhm':2.5})['excluded'],'M82-Einzelbildspur muss erhalten bleiben'
    print('Echte Problemframes 8/9/12/14 verworfen; scharfe 1/13 und M82-Meteorspur behalten OK')

    # A sharp star can produce an elongated difference under wrong registration;
    # this must not be accepted as a physical streak in the current exposure.
    from sequence import transient_shape
    assert not transient_shape(sharp.astype('uint8'),translated.astype('uint8'),np.eye(2,3),[.05,.05,.95,.95])

with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as temp:
    root=Path(temp); source=root/'source'; source.mkdir()
    for index,image in enumerate([smear,sharp,double,translated]):
        header=fits.Header(); header['DATE-OBS']=f'2022-01-01T00:00:{index*10:02d}'
        fits.writeto(source/f'image-{index}.fits',image,header)
    before={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in source.glob('*.fits')}
    from sequence import run_sequences
    out=root/'output'
    with redirect_stdout(io.StringIO()): run_sequences(SimpleNamespace(source=str(source),output=str(out),limit=0,fps=8,solve=False))
    summary=json.loads((out/'videos.json').read_text(encoding='utf-8'))[0]
    folder=out/Path(summary['video']).parent
    rows=[json.loads(s) for s in (folder/'frames.jsonl').read_text(encoding='utf-8').splitlines()]
    rejected=[json.loads(s) for s in (folder/'ausgeschlossen.jsonl').read_text(encoding='utf-8').splitlines()]
    assert summary['frames']==2 and summary['excluded']==2,summary
    assert [r['index'] for r in rows]==[1,3],rows
    assert [r['index'] for r in rejected]==[0,2],rejected
    assert [r['video_frame'] for r in rows]==[0,1]
    assert {p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in source.glob('*.fits')}==before
    import av
    with av.open(str(folder/'ereignisse.mp4')) as movie: assert sum(1 for frame in movie.decode(video=0))==2
    print('Pipeline: unscharfer erster Frame und Doppelbild ausgeschlossen; Video enthält nur 2 scharfe Frames; Originale unverändert OK')
