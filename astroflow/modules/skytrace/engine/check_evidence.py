"""Independent measurements: preserve true streak, align context, handle RA wrap."""
from pathlib import Path
import numpy as np
from candidate_evidence import enrich


anchor=dict(index=1,file='hit',reference_to_current=[[1,0,0],[0,1,0]],star_field=dict(accepted=True,inliers=25,median_error=.2),quality=dict(excluded=False))
before=dict(index=0,file='before',reference_to_current=[[1,0,7],[0,1,0]],star_field=dict(accepted=True))
after=dict(index=2,file='after',reference_to_current=[[1,0,-5],[0,1,0]],star_field=dict(accepted=True))
data={name:np.full((200,200),100,dtype='float32') for name in ('hit','before','after')}
data['hit'][20:181,79:82]+=30
candidate=dict(id='K1',hit_indices=[1],world_path=[[359.9,0],[.1,0]],exposure_seconds=10,kind='trail',frames=[dict(index=i+1,timestamp=10*i,hit=i==1,excluded=False) for i in range(3)])
detection=dict(segment=[[80,20],[80,180]],line_snr=15,model_hints=[dict(kind='meteor',confidence=.7)])
evidence=enrich(candidate,anchor,detection,{0:before,1:anchor,2:after},lambda name:(data[name],{}),200,200)
assert evidence['detector_score']==15 and evidence['detector_threshold']==10.5
assert candidate['model_hints']==detection['model_hints'],'Model suggestions must remain available without changing the measured trail kind'
assert evidence['length_pixels']==160 and abs(evidence['length_arcmin']-12)<1e-4
assert abs(evidence['minimum_angular_speed_arcsec_s']-72)<1e-3
values=[r['contrast_adu'] for r in evidence['temporal']]
assert values[1]>29 and abs(values[0])<.01 and abs(values[2])<.01,values
assert evidence['profile']['contrast_adu'][12]>29
candidate['frames'][0]['excluded']=True
evidence=enrich(candidate,anchor,detection,{0:before,1:anchor,2:after},lambda name:(data[name],{}),200,200)
assert evidence['temporal'][0]['contrast_adu'] is None
print('OK: measured line contrast, registered temporal context, detector provenance, angular length across RA wrap and excluded context')
