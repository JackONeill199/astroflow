"""Angular screening must never turn an exposure into a measured transit time."""
import json
import math
from pathlib import Path
import tempfile
from unittest.mock import patch
import numpy as np
from astropy.io import fits
from analyse import load_image
from motion_plausibility import assess_motion,exposure_info

assert exposure_info(dict(INSTRUME='stellina-test',EXPOSURE=10000))['seconds']==10
assert exposure_info(dict(EXPTIME=10))['seconds']==10
for bad in (None,0,-1,'NaN','inf','bad'):
    assert exposure_info(dict(EXPTIME=bad))['seconds'] is None
assert exposure_info(dict(EXPOSURE=10000))['seconds'] is None,'Unknown EXPOSURE units must not be guessed'
assert exposure_info(dict(INSTRUME='stellina',EXPOSURE=10000,EXPTIME=2))['status']=='conflicting'
meta=dict(INSTRUME='stellina',EXPOSURE=10000,FOCAL=400,PIXSZ=2.4,NAXIS1=3072,NAXIS2=2080)
segment=dict(segment=[[300,500],[1200,500]])
result=assess_motion(segment,meta,(1040,1536))
assert result['status']=='assessed' and 1.05<result['field_of_view_deg'][0]<1.06
assert .61<result['angular_length_deg']<.63
resized=assess_motion(dict(segment=[[150,250],[600,250]]),meta,(520,768))
assert abs(result['angular_length_deg']-resized['angular_length_deg'])<1e-9,'Binning/resize changed sky length'
assert abs(result['minimum_angular_speed_deg_s']-result['angular_length_deg']/10)<1e-10
assert all(s['compatible_with_exposure'] for s in result['scenarios'])
assert result['excluded_classes']==[],'Exposure alone cannot exclude entire classes'
fast=assess_motion(segment,dict(meta,EXPOSURE=1),(1040,1536))
assert not any(s['compatible_with_exposure'] for s in fast['scenarios'])
assert fast['excluded_classes']==[],'Rejected reference scenarios are not universal class bounds'
assert assess_motion(segment,{},(1040,1536))['status']=='not_checkable'
assert assess_motion(dict(segment=[[0,2],[2000,2]]),meta,(1040,1536))['status']=='invalid_geometry'
wrapped=assess_motion(segment,meta,(1040,1536),[[359.9,0],[.1,0]])
assert abs(wrapped['angular_length_deg']-.2)<1e-8
assert wrapped['angular_source'].startswith('Plate Solving')
assert assess_motion(dict(segment=[[1,1],[100,1]]),meta,(1040,1536))['touches_border']
with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as temp:
    path=Path(temp)/'test.fits';header=fits.Header()
    for k,v in meta.items():
        if not k.startswith('NAXIS'):header[k]=v
    header['BAYERPAT']='RGGB'
    fits.writeto(path,np.zeros((20,30),dtype='float32'),header)
    image,loaded=load_image(path)
    assert image.shape==(10,15) and loaded['NAXIS1']=='30' and loaded['exposure_seconds']==10
import catalog_matching as cm
with patch.object(cm,'asteroid_check',side_effect=AssertionError('query must not run')):
    checks=cm.compare(dict(timestamp=1,world_path=[[1,2]],exposure_seconds=None),{},dict(enabled=True))
    assert all(c['status']=='not_checkable' for c in checks)
print('OK: header units/conflicts, native size, Bayer/resize, WCS wrap, lower speed bound, scenario-only exclusions and no catalogue query without exposure')
root=Path(__file__).resolve().parent.parent/'Ergebnisse/NGC884-869-Geprueft'
if root.exists():
    summary=json.loads((root/'videos.json').read_text(encoding='utf-8'))[0]
    rows=[json.loads(v) for v in (root/Path(summary['video']).parent/'frames.jsonl').read_text(encoding='utf-8').splitlines()]
    row=next(r for r in rows if r['index']==30);_,meta=load_image(Path(row['file']))
    for detection in row['detections']:
        result=assess_motion(detection,meta,(1040,1536))
        assert result['exposure_seconds']==10 and all(s['compatible_with_exposure'] for s in result['scenarios'])
        print('NGC884/869:',round(result['angular_length_deg'],4),'deg;',round(result['minimum_angular_speed_deg_s'],4),'deg/s minimum;',[(s['kind'],round(s['required_visibility_seconds'],4)) for s in result['scenarios']])
