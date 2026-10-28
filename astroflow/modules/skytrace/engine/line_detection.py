"""Faint single-exposure streaks via line integration, independent of moving points."""
import cv2
import numpy as np
from scipy.ndimage import gaussian_filter, gaussian_filter1d, maximum_filter

LINE_SCORE_THRESHOLD=10.5


def merge_streak_detections(model_detections, lines, shape):
    """Replace model fragments intersecting a measured streak with its geometry.

    Box IoU is unsuitable: a long streak and each short fragment barely overlap
    by area. Clip the measured segment to the box instead. Parallel streaks remain
    separate; unmatched model candidates are retained for independent review.
    """
    height,width=shape[:2]
    remaining=[]
    for detection in model_detections:
        if detection.get('track_id') is not None:
            remaining.append(detection);continue
        x1,y1,x2,y2=np.asarray(detection['box'])*[width,height,width,height]
        rect=(int(x1)-3,int(y1)-3,max(1,int(x2-x1)+6),max(1,int(y2-y1)+6))
        matches=[]
        for candidate in lines:
            segment=np.rint(candidate['segment']).astype('int32')
            intersects,a,b=cv2.clipLine(rect,tuple(segment[0]),tuple(segment[-1]))
            if intersects and np.linalg.norm(np.asarray(b)-a)>min(25,.25*np.hypot(x2-x1,y2-y1)):
                matches.append(candidate)
        if matches:
            def distance(candidate):
                a,b=np.asarray(candidate['segment'])
                direction=b-a
                normal=np.array([-direction[1],direction[0]])/max(1,np.linalg.norm(direction))
                return abs(np.dot(np.array([(x1+x2)/2,(y1+y2)/2])-a,normal))
            closest=min(matches,key=distance)
            closest.setdefault('model_hints',[]).append(dict(kind=detection['kind'],confidence=detection.get('confidence')))
        else: remaining.append(detection)
    return remaining+lines


def correct_readout_banding(image):
    """Remove sensor-wide row/column offsets before aligning the star field.

    Require agreement in seven of eight independent strips and at both sensor
    edges. Finite horizontal/vertical trails with real endpoints are retained;
    orientation alone never rejects a candidate. Only detection data is changed.
    """
    data=np.asarray(image,dtype='float32').copy()
    for axis in (1,0):
        if data.shape[axis]<64: continue
        profiles=np.asarray([np.median(part,axis=axis) for part in np.array_split(data,8,axis=axis)])
        high=profiles-gaussian_filter1d(profiles,8,axis=1)
        common=np.median(high,axis=0);sign=np.sign(common)
        noise=max(.01,float(1.4826*np.median(np.abs(common-np.median(common)))))
        coherent=(high*sign>0).mean(axis=0)>=.875
        rim=max(8,data.shape[axis]//32)
        edges=np.asarray([np.median(np.take(data,range(rim),axis=axis),axis=axis),np.median(np.take(data,range(data.shape[axis]-rim,data.shape[axis]),axis=axis),axis=axis)])
        edges-=gaussian_filter1d(edges,8,axis=1)
        supported=(edges[0]*sign>.25*np.abs(common))&(edges[1]*sign>.25*np.abs(common))
        bias=np.where(coherent&supported&(np.abs(common)>1.5*noise),common,0)
        data-=bias[:,None] if axis==1 else bias[None,:]
    return data


def detect_lines(image, comparison=None, transform=None):
    image=correct_readout_banding(image)
    original=image.copy()
    if comparison is not None and transform is not None and comparison.shape==image.shape:
        previous=cv2.warpAffine(correct_readout_banding(comparison),transform,(image.shape[1],image.shape[0]),borderMode=cv2.BORDER_REFLECT)
        image=(image.astype('float32')-np.median(image))-(previous-np.median(previous))
    scale=min(1.,768/image.shape[1])
    small=cv2.resize(image.astype('float32'),None,fx=scale,fy=scale,interpolation=cv2.INTER_AREA)
    high=small-gaussian_filter(small,12)
    smooth=gaussian_filter(high,.8)
    noise=max(.25,float(1.4826*np.median(np.abs(smooth-np.median(smooth)))))
    z=smooth/noise
    source=cv2.resize(original,None,fx=scale,fy=scale,interpolation=cv2.INTER_AREA)
    source=gaussian_filter(source-gaussian_filter(source,12),.8)
    source/=max(.25,float(1.4826*np.median(np.abs(source-np.median(source)))))
    # Mask compact bright stars instead of requiring every trail pixel to exceed a high threshold.
    peaks=(smooth==maximum_filter(smooth,7))&(z>7)
    stars=maximum_filter(peaks.astype('uint8'),9)>0
    # A bright continuous streak also has local maxima. Do not erase it as a chain
    # of stars: preserve elongated connected ridges before the Hough vote.
    count,components,stats,_=cv2.connectedComponentsWithStats((z>7).astype('uint8'),8)
    for component in range(1,count):
        if stats[component,cv2.CC_STAT_AREA]<25: continue
        yy,xx=np.where(components==component)
        coords=np.column_stack([xx,yy]).astype('float32')
        _,_,values=cv2.PCACompute2(coords,mean=None)
        if values[0,0]>100 and values[0,0]>16*max(values[1,0],.1):
            ridge=cv2.dilate((components==component).astype('uint8'),np.ones((5,5),dtype='uint8'))>0
            stars[ridge]=False
    votes=((z>1.35)&~stars).astype('uint8')*255
    votes[:12]=votes[-12:]=0; votes[:,:12]=votes[:,-12:]=0
    lines=cv2.HoughLines(votes,1,np.pi/720,max(35,int(min(small.shape)*.075)))
    if lines is None: return []
    h,w=small.shape; candidates=[]
    for rho,theta in lines.reshape(-1,2)[:160]:
        normal=np.array([np.cos(theta),np.sin(theta)]); direction=np.array([-normal[1],normal[0]])
        center=normal*rho
        t=np.arange(-max(h,w)*2,max(h,w)*2,dtype='float32')
        xy=center[None]+t[:,None]*direction
        inside=(xy[:,0]>14)&(xy[:,0]<w-14)&(xy[:,1]>14)&(xy[:,1]<h-14)
        xy=xy[inside]
        if len(xy)<80: continue
        def sample(offset,field=z):
            points=xy+normal*offset
            return cv2.remap(field,points[:,0].astype('float32')[None],points[:,1].astype('float32')[None],cv2.INTER_LINEAR,borderMode=cv2.BORDER_REFLECT)[0]
        signal=(sample(-1)+sample(0)+sample(1))/3
        sides=np.asarray([sample(k) for k in (-9,-7,7,9)])
        contrast=signal-np.mean(sides,axis=0)
        narrow=signal-(sample(-4)+sample(4))/2
        # Extended galaxies/nebulae are not narrow ridges. Winsorization prevents one
        # bright stellar residual from giving an otherwise empty line a large score.
        contrast=np.clip(np.minimum(contrast,narrow*1.4),-4,4)
        valid=cv2.remap((~stars).astype('uint8'),xy[:,0].astype('float32')[None],xy[:,1].astype('float32')[None],cv2.INTER_NEAREST)[0]>0
        # Score contiguous 24px bins; a chance chain of stars cannot support an entire line.
        bins=[]
        for start in range(0,len(xy)-23,24):
            keep=valid[start:start+24]
            bins.append(float(np.mean(contrast[start:start+24][keep])) if keep.sum()>=12 else 0.)
        bins=np.asarray(bins)
        good=bins>.32
        best=None
        for start in np.where(good)[0]:
            for end in range(start+3,len(bins)+1):
                section=good[start:end]
                if section.mean()<.65 or not section[-1]: continue
                strength=float(np.mean(bins[start:end])*np.sqrt((end-start)*24/4))
                if best is None or strength>best[0]: best=(strength,start,end)
        if best is None or best[0]<LINE_SCORE_THRESHOLD: continue
        snr,start,end=best
        # Subtracting a bright trail in the previous exposure can create positive
        # side lobes. A candidate also needs a narrow ridge in this exposure itself.
        source_signal=sum(sample(k,source) for k in (-1,0,1))/3
        source_sides=sum(sample(k,source) for k in (-9,-7,7,9))/4
        source_narrow=source_signal-(sample(-4,source)+sample(4,source))/2
        source_contrast=np.clip(np.minimum(source_signal-source_sides,source_narrow*1.4),-4,4)
        source_bins=[]
        for bin_index in range(start,end):
            keep=valid[bin_index*24:bin_index*24+24]
            values=source_contrast[bin_index*24:bin_index*24+24][keep]
            source_bins.append(float(np.mean(values)) if len(values)>=12 else 0.)
        if np.mean(source_bins)*np.sqrt((end-start)*24/4)<5 or np.mean(np.asarray(source_bins)>.2)<.5: continue
        p1=xy[start*24]/scale; p2=xy[min(len(xy)-1,end*24-1)]/scale
        if np.linalg.norm(p2-p1)<160: continue
        # Nearby parallel Hough solutions describe the same measured streak.
        midpoint=(p1+p2)/2
        if any(abs(np.dot(midpoint-np.mean(c['segment'],axis=0),normal))<12 and abs(np.dot(direction,c['_direction']))>.995 for c in candidates): continue
        height,width=image.shape
        candidates.append(dict(kind='trail',label='Einzelbild-Spur (Meteor/Satellit/Flugzeug?)',confidence=None,box=[float(max(0,min(p1[0],p2[0])-8)/width),float(max(0,min(p1[1],p2[1])-8)/height),float(min(width,max(p1[0],p2[0])+8)/width),float(min(height,max(p1[1],p2[1])+8)/height)],segment=[p1.tolist(),p2.tolist()],line_snr=round(snr,2),method='Einzelbild: Sensorbanding-Korrektur + Hintergrundabzug + Hough + integrierter Linienkontrast',_direction=direction))
        if len(candidates)>=8: break
    merged=[]
    for candidate in sorted(candidates,key=lambda c:c['line_snr'],reverse=True):
        segment=np.asarray(candidate['segment']); midpoint=segment.mean(axis=0)
        duplicate=False
        for kept in merged:
            other=np.asarray(kept['segment']); direction=kept['_direction']; normal=np.array([-direction[1],direction[0]])
            if abs(np.dot(candidate['_direction'],direction))>.99 and abs(np.dot(midpoint-other.mean(axis=0),normal))<14:
                a=segment@direction; b=other@direction
                if min(a.max(),b.max())>max(a.min(),b.min()): duplicate=True; break
        if not duplicate: merged.append(candidate)
    for candidate in merged: candidate.pop('_direction')
    return merged
