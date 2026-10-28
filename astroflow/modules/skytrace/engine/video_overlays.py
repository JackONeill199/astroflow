"""Timed browser overlays from saved measurements; never alter source images."""
import json
from functools import lru_cache
from pathlib import Path
from json_store import open_read,read_json


@lru_cache(maxsize=32)
def _objects(folder,width,height,wcs_stamp,info_stamp,catalog_stamp):
    from astropy.io import fits
    from astropy.wcs import WCS
    from solving import objects_for_frame
    folder=Path(folder)
    info=read_json(folder/'platesolve/ergebnis.json',{})
    header=fits.Header.fromfile(folder/'platesolve/loesung.wcs')
    header['NAXIS']=2;header['NAXIS1']=info['width'];header['NAXIS2']=info['height']
    solved=dict(info,wcs=WCS(header,naxis=2))
    if not solved['wcs'].has_celestial:return []
    return objects_for_frame(solved,(height,width))


def saved_catalog(folder,size):
    """Reuse the saved WCS and local catalogue; never solve or query a service."""
    wcs=folder/'platesolve/loesung.wcs';info_path=folder/'platesolve/ergebnis.json'
    if not wcs.is_file() or not info_path.is_file():return [],size
    try:
        info=read_json(info_path,{})
        if not size:
            scale=min(1,1536/info['width'])
            size=[max(2,int(info[key]*scale)//2*2) for key in ('width','height')]
        from solving import CATALOG
        stamp=CATALOG.stat().st_mtime_ns if CATALOG.is_file() else 0
        return _objects(str(folder),size[0],size[1],wcs.stat().st_mtime_ns,info_path.stat().st_mtime_ns,stamp),size
    except (OSError,ValueError,KeyError,TypeError,ZeroDivisionError):return [],size


def timeline(folder,candidates):
    size=next((c.get('plausibility',{}).get('analysis_size') for c in candidates if c.get('plausibility',{}).get('analysis_size')),None)
    objects,size=saved_catalog(folder,size)
    if not size:return None
    frames=[];active={}
    try:
        with open_read(folder/'frames.jsonl') as stream:
            for line in stream:
                try:row=json.loads(line)
                except ValueError:continue
                matrix=row.get('previous_to_current')
                if matrix:
                    for record in active.values():
                        record['points']=[[matrix[0][0]*x+matrix[0][1]*y+matrix[0][2],matrix[1][0]*x+matrix[1][1]*y+matrix[1][2]] for x,y in record['points']]
                for detection in row.get('detections',[]):
                    points=detection.get('segment') or detection.get('path')
                    if not points:continue
                    candidate=next((c for c in candidates if row['index'] in c.get('hit_indices',[]) and c.get('evidence',{}).get('segment')==points),None)
                    if not candidate:continue
                    active[candidate['id']]=dict(id=candidate['id'],points=points)
                reference=row.get('reference_to_current')
                if reference is None and not frames:reference=[[1,0,0],[0,1,0]]
                visible=[]
                if reference is not None:
                    for obj in objects:
                        x,y=obj['x'],obj['y']
                        px=reference[0][0]*x+reference[0][1]*y+reference[0][2]
                        py=reference[1][0]*x+reference[1][1]*y+reference[1][2]
                        if 0<=px<size[0] and 0<=py<size[1]:visible.append(dict(obj,x=px,y=py))
                satellites=[]
                if reference is not None:
                    from satellite_overlay import transform_segments,intersects
                    for c in candidates:
                        if not c.get('hit_indices') or row['index']<min(c['hit_indices']):continue
                        for projected in c.get('satellite_overlay',{}).get('paths',[]):
                            segments=transform_segments(projected['reference_segments'],reference)
                            satellites.append(dict(name=projected['name'],candidate_id=c['id'],frame=min(c['hit_indices'])+1,
                                segments=segments,in_frame=intersects(segments,*size)))
                frames.append(dict(seconds=row.get('video_seconds',0),objects=visible,satellites=satellites,trails=[dict(id=r['id'],points=[p[:] for p in r['points']]) for r in active.values()]))
    except OSError:return None
    return dict(width=size[0],height=size[1],frames=frames)
