import cv2
import numpy as np
from astro_motion import EnsembleMotion
from line_detection import detect_lines

rng=np.random.default_rng(93)
height,width=480,640
yy,xx=np.mgrid[:height,:width]
fixed=[]
for _ in range(55):
    x,y=rng.uniform(25,width-25),rng.uniform(25,height-25)
    if all(np.hypot(x-a,y-b)>15 for a,b in fixed): fixed.append((x,y))
def scene(index,independent=False):
    image=np.full((height,width),30.,dtype='float32')
    for x,y in fixed: image+=150*np.exp(-((xx-x)**2+(yy-y)**2)/5)
    if independent:
        image+=190*np.exp(-((xx-(290+index*.7))**2+(yy-220)**2)/5)
    angle=np.sin(index*.6)*.25
    matrix=cv2.getRotationMatrix2D((width/2,height/2),angle,1)
    matrix[:,2]+=np.array([np.sin(index*.7)*3.5,np.cos(index*.4)*2.5])
    moved=cv2.warpAffine(image,matrix,(width,height),borderMode=cv2.BORDER_REFLECT)
    moved+=rng.normal(0,1,moved.shape).astype('float32')
    return np.clip(moved,0,255).astype('uint8'),matrix

detector=EnsembleMotion(); first=None
for index in range(24):
    image,matrix=scene(index)
    if first is None: first=np.vstack([matrix,[0,0,1]])
    initial=(np.vstack([matrix,[0,0,1]])@np.linalg.inv(first))[:2]
    assert not detector.update(image,index,initial), 'Gemeinsames Sternwackeln darf keinen Beweger ergeben'
print('55 Sterne mit gemeinsamer Rotation und Verwacklung: keine Beweger OK')

detector=EnsembleMotion(); found=[]; first=None
for index in range(24):
    image,matrix=scene(index,True)
    if first is None: first=np.vstack([matrix,[0,0,1]])
    initial=(np.vstack([matrix,[0,0,1]])@np.linalg.inv(first))[:2]
    found.extend(detector.update(image,index,initial))
assert found,'Ein unabhängiger Beweger im verwackelten Sternfeld muss erhalten bleiben'
assert all(abs((r['box'][1]+r['box'][3])*height/2-220)<12 for r in found),found
print('Ein separat wandernder Stern zwischen gemeinsam wackelnden Sternen erkannt OK')

image=rng.normal(30,3,(height,width)).astype('float32')
reference=image.copy()
cv2.line(image,(40,100),(580,390),4.5+30,2,cv2.LINE_AA)
lines=detect_lines(image)
assert lines, 'Schwache Einzelbildlinie ohne wandernden Punkt muss erkannt werden'
assert not detect_lines(reference), 'Rauschen darf keine Spur werden'
print('Schwache Linie in nur einem Frame erkannt; reines Rauschen verworfen OK')

# A fixed extended galaxy must not become a transient streak when the telescope shifts.
fixed_image=np.full((height,width),30.,dtype='float32')
fixed_image+=50*np.exp(-((xx-320)**2/900+(yy-240)**2/36)/2)
transform=np.array([[1.,0.,3.],[0.,1.,-2.]])
shifted=cv2.warpAffine(fixed_image,transform,(width,height),borderMode=cv2.BORDER_REFLECT)
assert not detect_lines(shifted,fixed_image,transform)
print('Feste ausgedehnte Quelle nach Teleskopverschiebung nicht als Spur gemeldet OK')

sparse=np.array([[50,60],[560,65],[70,400],[550,410],[340,240]],dtype='float32')
detector=EnsembleMotion()
for index in range(24):
    matrix=cv2.getRotationMatrix2D((320,240),index*.015,1)
    matrix[:,2]+=np.array([23+np.sin(index*.4)*4,17+index*.2])
    points=cv2.transform(sparse[None],matrix)[0]
    points=np.vstack([points,[250,100]]) # detector-fixed defect
    results=detector.update(np.zeros((480,640),dtype='float32'),index,np.eye(2,3),points)
    assert detector.quality['accepted'], (index,detector.quality)
    assert not results,'Sparse Sterne plus ortsfester Hotpixel dürfen keine Beweger liefern'
print('Sparsames Sternfeld + falsche Startschätzung + Hotpixel: sicher kompensiert OK')

catalog=np.array([[40,40],[590,410],[60,420],[600,70],[200,100],[420,390],[220,350],[430,120],[70,230],[580,240],[320,40],[300,420]],dtype='float32')
detector=EnsembleMotion(); rolling=False
for index in range(24):
    start=min(6,index//8*2)
    points=catalog[start:start+6]+np.array([index*.2,index*.1])
    detector.update(np.zeros((480,640),dtype='float32'),index,np.eye(2,3),points)
    assert detector.quality['accepted'],(index,detector.quality)
    rolling |= detector.quality.get('reference')=='last_valid_frame'
assert rolling,'Neue Referenzsterne müssen ältere verschwundene Sterne ersetzen können'
print('Wechselnde Referenzsterne ohne Lockerung der Anpassungsfehler übernommen OK')
