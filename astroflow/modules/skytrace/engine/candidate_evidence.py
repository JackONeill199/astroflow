"""Auditable candidate measurements; never interpret detector scores as probabilities."""
import math
import cv2
import numpy as np


def enrich(item,anchor,detection,rows,loader,width,height):
    from line_detection import LINE_SCORE_THRESHOLD
    from motion_plausibility import assess_motion
    _,meta=loader(anchor['file'])
    # Legacy callers may supply a previously normalized exposure with no raw header.
    if not any(k in meta for k in ('EXPTIME','EXPOSURE','exposure_seconds')):
        meta=dict(meta,exposure_seconds=item.get('exposure_seconds'))
    item['plausibility']=assess_motion(detection,meta,(height,width),item.get('world_path'))
    item['exposure_seconds']=item['plausibility']['exposure_seconds']
    item['model_hints']=detection.get('model_hints',[])
    points=np.asarray(detection.get('segment') or detection.get('path') or [],dtype=float)
    quality=anchor.get('quality',{});stars=anchor.get('star_field',{})
    evidence=dict(version=1,detector_score=detection.get('line_snr'),detector_threshold=LINE_SCORE_THRESHOLD if detection.get('line_snr') is not None else None,
                  quality=quality,registration=stars,hit_frames=len(item['hit_indices']),analysis_pixels='Pixel der Analyse-/Videoauflösung; Bayer-Bilder können gebinnt sein',
                  source_frame=anchor['index']+1,segment=points.tolist(),temporal=[],profile=None)
    evidence['shape_validation']=detection.get('shape_validation')
    evidence['frame_validation']=anchor.get('frame_validation')
    if len(points)>1:
        delta=points[-1]-points[0];length=float(np.linalg.norm(np.diff(points,axis=0),axis=1).sum())
        evidence.update(length_pixels=round(length,2),angle_image_degrees=round(math.degrees(math.atan2(delta[1],delta[0]))%180,2))
        world=np.asarray(item.get('world_path') or [])
        if len(world)>1:
            from astropy.coordinates import SkyCoord
            from astropy import units as u
            coords=SkyCoord(world[:,0]*u.deg,world[:,1]*u.deg)
            angular=float(coords[:-1].separation(coords[1:]).arcmin.sum())
            evidence['length_arcmin']=round(angular,4)
            seconds=item['plausibility']['exposure_seconds']
            if seconds: evidence['minimum_angular_speed_arcsec_s']=round(angular*60/seconds,3)
        if length>0 and len(points)==2 and detection.get('segment') is not None:
            from line_detection import correct_readout_banding
            normal=np.array([-delta[1],delta[0]])/max(float(np.linalg.norm(delta)),1e-9)
            along=np.linspace(points[0],points[-1],240)
            offsets=np.arange(-12,13,dtype=float)
            positions=along[None,:,:]+offsets[:,None,None]*normal
            matrix=anchor.get('reference_to_current')
            if anchor['index']==0:matrix=np.eye(2,3)
            for frame in item['frames']:
                row=rows.get(frame['index']-1)
                record=dict(frame=frame['index'],timestamp=frame.get('timestamp'),hit=frame['hit'],excluded=frame['excluded'],contrast_adu=None,reason=None)
                evidence['temporal'].append(record)
                if not row or frame['excluded']:
                    record['reason']='Unscharfer Kontext, nicht vermessen';continue
                current=row.get('reference_to_current')
                if row['index']==anchor['index']:transform=np.eye(2,3)
                elif matrix is not None and current is not None and row.get('star_field',{}).get('accepted'):
                    a=np.vstack([matrix,[0,0,1]]);b=np.vstack([current,[0,0,1]])
                    transform=(b@np.linalg.inv(a))[:2]
                else:record['reason']='Keine zuverlässige Registrierung zum Trefferbild';continue
                data,_=loader(row['file'])
                linear=correct_readout_banding(cv2.resize(data,(width,height),interpolation=cv2.INTER_AREA))
                xy=cv2.transform(positions.reshape(1,-1,2),transform)[0].reshape(positions.shape)
                valid=(xy[:,:,0]>=0)&(xy[:,:,0]<width-1)&(xy[:,:,1]>=0)&(xy[:,:,1]<height-1)
                values=cv2.remap(linear,xy[:,:,0].astype('float32'),xy[:,:,1].astype('float32'),cv2.INTER_LINEAR,borderMode=cv2.BORDER_CONSTANT,borderValue=float('nan'))
                values[~valid]=np.nan
                valid_columns=np.isfinite(values).all(axis=0)
                if valid_columns.sum()<120:record['reason']='Zu wenig gemeinsame Bildfläche';continue
                values=values[:,valid_columns]
                side=np.median(values[[0,2,4,20,22,24]],axis=0)
                # Median along the line reduces contamination from bright stars.
                profile=np.median(values-side[None,:],axis=1)
                record['contrast_adu']=round(float(np.median(np.mean(values[11:14],axis=0)-side)),4)
                if row['index']==anchor['index']:
                    evidence['profile']=dict(offset_pixels=offsets.tolist(),contrast_adu=np.round(profile,4).tolist(),samples=int(valid_columns.sum()),unit='ADU nach Bayer-Binning/Verkleinerung; lokaler Seitenhintergrund abgezogen')
    item['evidence']=evidence
    return evidence
