"""Gemeinsame lange Sternschweife erkennen; echte Spuren und symmetrische Optik erhalten."""
from pathlib import Path
import sys

import numpy as np
from scipy.ndimage import gaussian_filter

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'astroflow/modules/skytrace/engine'))
from frame_quality import measure_quality, assess_quality


def field(tails=False,symmetric=False,local=False,random_angles=False):
    rng=np.random.default_rng(471)
    shape=(700,900);cores=np.zeros(shape,dtype='float32');streaks=np.zeros(shape,dtype='float32')
    for y in range(90,650,80):
        for x in range(90,850,80):
            peak=rng.uniform(4000,16000)
            cores[y,x]=peak*2*np.pi*1.2**2
            if not tails or (local and not (x>450 and y<350)):continue
            angle=rng.uniform(0,2*np.pi) if random_angles else np.radians(220)
            for sign in ((-1,1) if symmetric else (1,)):
                # Unterbrochen und vom Kern getrennt: eine Zusammenhangssuche kann diesen Schweif nicht erfassen.
                for d in range(10,57):
                    if d%10>6:continue
                    bend=1.5*np.sin(np.pi*d/57)
                    dx=d*np.cos(angle)-bend*np.sin(angle);dy=d*np.sin(angle)+bend*np.cos(angle)
                    streaks[int(round(y+sign*dy)),int(round(x+sign*dx))]+=.01*peak
    image=100+gaussian_filter(cores,1.2)+gaussian_filter(streaks,.7)
    return image+rng.normal(0,1,shape).astype('float32')


def test_broken_tails_are_shared_telescope_motion():
    quality=assess_quality(measure_quality(field(tails=True)))
    assert quality['ensemble_motion'] and quality['excluded'],quality
    assert quality['ensemble_tail_stars']>=5 and quality['ensemble_tail_quadrants']>=3
    assert quality['field_motion_evidence']=='ensemble_star_tails'


def test_sharp_field_and_independent_trails_are_kept():
    image=field();lines=np.zeros_like(image)
    for y in (80,300,550):
        for t in np.linspace(0,1,700):
            x=40+800*t;yy=y+80*t
            lines[int(yy),int(x)]=300
    for data in (image,image+gaussian_filter(lines,.7)):
        assert not assess_quality(measure_quality(data))['excluded']


def test_symmetric_diffraction_local_and_incoherent_shapes_are_kept():
    for options in (dict(symmetric=True),dict(local=True),dict(random_angles=True)):
        quality=measure_quality(field(tails=True,**options))
        assert not quality['ensemble_motion'],quality
