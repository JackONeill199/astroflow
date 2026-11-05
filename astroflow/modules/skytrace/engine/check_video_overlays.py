"""Timed overlays preserve measured geometry, star transforms and original data."""
import json
from pathlib import Path
import tempfile
from unittest.mock import patch
from video_overlays import timeline,satellites_at

with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as temp:
    folder=Path(temp)
    candidates=[dict(id='K1',hit_indices=[1],evidence=dict(segment=[[10,10],[30,30]]),plausibility=dict(analysis_size=[320,240]))]
    candidates[0]['satellite_overlay']=dict(paths=[dict(name='SAT TEST',reference_segments=[[[10,20],[40,50]]])])
    rows=[dict(index=0,video_seconds=0,detections=[]),dict(index=1,video_seconds=.125,detections=[dict(segment=[[10,10],[30,30]])]),dict(index=3,video_seconds=.25,previous_to_current=[[1,0,5],[0,1,-2]],detections=[])]
    path=folder/'frames.jsonl';original='\n'.join(json.dumps(r) for r in rows);path.write_text(original)
    data=timeline(folder,candidates)
    assert data['width']==320 and data['height']==240
    assert data['frames'][0]['trails']==[]
    assert data['frames'][1]['trails'][0]['points']==[[10,10],[30,30]]
    assert data['frames'][2]['trails'][0]['points']==[[15,8],[35,28]]
    assert data['frames'][2]['seconds']==.25,'Excluded frames must not change playback timing'
    assert path.read_text()==original
    assert timeline(folder,[]) is None
    objects=[dict(name='NGC884/Chi Persei Cluster',x=50,y=60,ra=35.,dec=57.)]
    rows[2]['reference_to_current']=[[1,0,9],[0,1,-4]]
    path.write_text('\n'.join(json.dumps(r) for r in rows))
    with patch('video_overlays.saved_catalog',return_value=(objects,[320,240])):
        calibrated=timeline(folder,candidates)
        assert calibrated['frames'][0]['objects'][0]['x']==50
        assert calibrated['frames'][1]['objects']==[],'Unregistered frames must not invent catalogue positions'
        assert calibrated['frames'][2]['objects'][0]['x']==59
        assert calibrated['frames'][2]['objects'][0]['y']==56
        assert objects[0]['x']==50,'Catalogue reference positions must remain unchanged'
        assert satellites_at(calibrated,calibrated['frames'][0])==[],'Do not show a future exposure as a current prediction'
        assert satellites_at(calibrated,calibrated['frames'][2])[0]['segments']==[[[19,16],[49,46]]]
        assert timeline(folder,[])['frames'][0]['objects'][0]['name'].startswith('NGC884'),'Plate-solving labels must work without event candidates'
print('OK: timed overlays appear at detection, persist with star transforms, respect excluded frames and leave saved data untouched')
