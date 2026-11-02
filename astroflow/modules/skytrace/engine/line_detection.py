"""Faint single-exposure streaks via line integration, independent of moving points."""
import cv2
import numpy as np
from functools import lru_cache
from scipy.ndimage import gaussian_filter, gaussian_filter1d, maximum_filter

LINE_SCORE_THRESHOLD=10.5
SHORT_BINS=7          # Abschnitte unter 7×24 px (Analyse-Maßstab ≤768 px) gelten als kurze Spur
CALM_LIMIT=1.5        # kurze Spuren nur in ruhigem Hintergrund (lokales/globales Rauschen)


def required_score(bins):
    """Integrated contrast required for a section of `bins` 24px bins.

    Long streaks keep the calibrated 10.5. Telescope fields are narrow, so a faint
    meteor may cross only ~100 px: from two bins 5.2, +0.9 per bin. Short sections
    must additionally lie in a calm background (`local_structure`). Calibrated
    28.10.2026: M74 img-0041 meteor 5.5 found; no new short false alarm in IC59,
    IC405, M103, NGC884 (all short noise there lies at bright stars/nebulae, ≥2.0).
    """
    return min(LINE_SCORE_THRESHOLD,5.2+.9*(bins-2))


@lru_cache(maxsize=32)
def _section_geometry(n):
    start,last=np.triu_indices(n,1)
    end=last+1; length=end-start
    return start,end,length,np.sqrt(length*24/4),np.minimum(LINE_SCORE_THRESHOLD,5.2+.9*(length-2))


def _best_section(bins):
    """Abschnitte gemeinsam vorfiltern, mögliche Gewinner mit der bisherigen Rechnung nachmessen."""
    good=bins>.32
    best=None
    if len(bins)>128:                         # extrem hohe Bilder: keinen quadratischen Hilfsspeicher anlegen
        for start in np.flatnonzero(good):
            for end in range(start+2,len(bins)+1):
                section=good[start:end]
                if section.mean()<.65 or not section[-1]: continue
                strength=float(np.mean(bins[start:end])*np.sqrt((end-start)*24/4))
                if strength<required_score(end-start): continue
                if best is None or strength>best[0]: best=(strength,start,end)
        return best
    if len(bins)<2: return None
    start,end,length,factor,threshold=_section_geometry(len(bins))
    counts=np.r_[0,np.cumsum(good)]
    sums=np.r_[0.,np.cumsum(bins)]
    score=(sums[end]-sums[start])/length*factor
    # Präfixsummen runden anders als np.mean. Großzügiger Fehlerabstand hält auch Grenzfälle im exakten Nachtest.
    epsilon=64*np.finfo(float).eps*max(1.,np.abs(bins).sum())*len(bins)
    eligible=good[start]&good[end-1]&((counts[end]-counts[start])/length>=.65)&(score+epsilon>=threshold)
    for i in np.flatnonzero(eligible):          # gleiche Reihenfolge und Gleichstandsentscheidung wie zuvor
        if best is not None and score[i]+epsilon<best[0]: continue
        a,b=int(start[i]),int(end[i])
        strength=float(np.mean(bins[a:b])*np.sqrt((b-a)*24/4))
        if strength<required_score(b-a): continue
        if best is None or strength>best[0]: best=(strength,a,b)
    return best


BLINK_SCORE=8.          # integrierter Kontrast der Blitze (wie line_snr, nur über die hellen Abschnitte)


def blink_pattern(contrast, valid):
    """Regelmäßig unterbrochene Spur auf einer Geraden: Blitz-/Positionslichter eines Flugzeugs (Nutzerhinweis 29.10.2026).

    Zwei Formen: gestrichelt (helle Abschnitte mit dunklen Lücken) oder eine schwache Linie mit regelmäßigen hellen
    Knoten. Verlangt ≥ 5 Blitze, echte Lücken, konstanten Takt (Median-Abweichung ≤ 12 % der Periode, ¾ der Abstände ≤ 25 %) und einen integrierten
    Kontrast ≥ BLINK_SCORE. Einzelne verdeckte Blitze (Stern darüber) sind erlaubt, Abstände = Vielfache des Takts. Ohne echtes Beispiel eingestellt – deshalb streng; ein taumelnder Satellit kann ähnlich
    aussehen. `contrast` ungekappt, je Abtastpunkt (Analyse-Maßstab ≤ 768 px), `valid` = kein Stern."""
    c=np.convolve(np.where(valid,contrast,0.),np.ones(3)/3,'same')
    for floor in (1.0,None):                       # None: Knoten relativ zur Grundhelligkeit der Linie
        if floor is None:
            lit=c[c>.8]
            if len(lit)<30: return None
            floor=max(1.,2.*float(np.median(lit)))
        on=np.r_[False,c>floor,False]
        edges=np.flatnonzero(np.diff(on.astype(int)))
        runs=[]
        for a,b in zip(edges[::2],edges[1::2]):
            if runs and a-runs[-1][1]<3: runs[-1]=(runs[-1][0],b); continue     # Rauschlöcher in einem Blitz schließen
            runs.append((a,b))
        runs=[(a,b) for a,b in runs if b-a>=3 and c[a:b].mean()>=max(2.,floor+.5) and valid[a:b].mean()>=.8]
        if len(runs)<3: continue
        level=float(np.median([c[a:b].mean() for a,b in runs]))
        runs=[(a,b) for a,b in runs if c[a:b].mean()>=.35*level]        # Rauschstücke zwischen den Blitzen
        if len(runs)<5: continue
        starts=np.array([a for a,_ in runs]);ends=np.array([b for _,b in runs])
        centers=(starts+ends)/2;spacing=np.diff(centers)
        gaps=starts[1:]-ends[:-1]
        # Takt: Abstände sind ganzzahlige Vielfache der Periode – ein Stern über einem Blitz lässt einen ausfallen
        period=float(np.min(spacing)) if len(spacing)<3 else float(np.median(spacing))
        steps=np.maximum(1,np.round(spacing/period))
        deviation=np.abs(spacing/period-steps)
        regularity=float(np.median(deviation))                        # robust: ein von einem Stern geteilter Blitz stört nicht
        missing=int((steps-1).sum())
        lit=int((ends-starts).sum());span=int(ends[-1]-starts[0])
        if (np.median(gaps)<5 or regularity>.12 or np.mean(deviation<=.25)<.75 or missing>len(runs)//2 or lit/span>.7): continue
        spacing=np.full(1,period)
        score=float(np.mean([c[a:b].mean() for a,b in runs])*np.sqrt(lit/4))
        if score<BLINK_SCORE: continue
        return dict(flashes=len(runs),period=period,regularity=round(regularity,3),duty=round(lit/span,3),
                    score=round(score,2),start=int(starts[0]),end=int(ends[-1]),form='Knoten' if floor>1. else 'gestrichelt')
    return None


def local_structure(field, points, normal):
    """Robust local noise around a segment (in global-noise units), the segment band excluded."""
    h,w=field.shape
    x0,y0=np.floor(points.min(axis=0)-20).astype(int).clip(0); x1,y1=np.ceil(points.max(axis=0)+21).astype(int)
    x1,y1=min(w,x1),min(h,y1)
    yy,xx=np.mgrid[y0:y1,x0:x1]
    offset=np.abs((xx-points[0][0])*normal[0]+(yy-points[0][1])*normal[1])
    values=field[y0:y1,x0:x1][offset>5]
    if values.size<50: return np.inf
    return float(1.4826*np.median(np.abs(values-np.median(values))))


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


def _plane(p1,p2,width,height,blink,scale,direction,snr=None,threshold=BLINK_SCORE):
    info={k:blink[k] for k in ('flashes','regularity','duty','score','form')}
    info['period_pixels']=round(blink['period']/scale,1)                   # Analysepixel
    return dict(kind='plane',label=f'Flugzeug? ({info["flashes"]} Blitze in gleichem Takt)',confidence=None,
                box=[float(max(0,min(p1[0],p2[0])-8)/width),float(max(0,min(p1[1],p2[1])-8)/height),float(min(width,max(p1[0],p2[0])+8)/width),float(min(height,max(p1[1],p2[1])+8)/height)],
                segment=[p1.tolist(),p2.tolist()],line_snr=round(max(snr or 0,blink['score']),2),line_threshold=round(threshold,2),
                short_trail=False,blink=info,method='Einzelbild: Linienprofil mit regelmäßig wiederkehrenden Blitzen',_direction=direction)


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
    dashes=stars.copy()        # Maske für die Blinkprüfung: lässt auch kurze helle Striche frei
    # A bright continuous streak also has local maxima. Do not erase it as a chain
    # of stars: preserve elongated connected ridges before the Hough vote.
    count,components,stats,_=cv2.connectedComponentsWithStats((z>7).astype('uint8'),8)
    for component in range(1,count):
        if stats[component,cv2.CC_STAT_AREA]<25: continue
        yy,xx=np.where(components==component)
        coords=np.column_stack([xx,yy]).astype('float32')
        _,_,values=cv2.PCACompute2(coords,mean=None)
        if values[0,0]>12 and values[0,0]>16*max(values[1,0],.1):
            ridge=cv2.dilate((components==component).astype('uint8'),np.ones((5,5),dtype='uint8'))>0
            dashes[ridge]=False    # ab ~12 px: kurze helle Striche blinkender Flugzeuge (Sterne sind rund)
            # Für durchgehende Spuren nur lange Grate freigeben – kurze Sternschweife verwackelter Bilder
            # (IC59 Bild 114) würden sonst zu kurzen Spuren.
            if values[0,0]>100: stars[ridge]=False
    votes=((z>1.35)&~dashes).astype('uint8')*255
    votes[:12]=votes[-12:]=0; votes[:,:12]=votes[:,-12:]=0
    # Low vote threshold so ~100 px meteors get a line; the contrast score decides.
    lines=cv2.HoughLines(votes,1,np.pi/720,max(22,int(min(small.shape)*.042)))
    if lines is None: return []
    h,w=small.shape; candidates=[]
    star_valid=(~stars).astype('uint8'); dash_valid=(~dashes).astype('uint8')
    t=np.arange(-max(h,w)*2,max(h,w)*2,dtype='float32')
    for rho,theta in lines.reshape(-1,2)[:600]:
        normal=np.array([np.cos(theta),np.sin(theta)]); direction=np.array([-normal[1],normal[0]])
        center=normal*rho
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
        raw=np.minimum(contrast,narrow*1.4)
        # Extended galaxies/nebulae are not narrow ridges. Winsorization prevents one
        # bright stellar residual from giving an otherwise empty line a large score.
        contrast=np.clip(raw,-4,4)
        valid=cv2.remap(star_valid,xy[:,0].astype('float32')[None],xy[:,1].astype('float32')[None],cv2.INTER_NEAREST)[0]>0
        blink=blink_pattern(raw,cv2.remap(dash_valid,xy[:,0].astype('float32')[None],xy[:,1].astype('float32')[None],cv2.INTER_NEAREST)[0]>0)
        if blink and (min(abs(direction[0]),abs(direction[1]))<.0175 or      # Auslesezeilen/-spalten
                      local_structure(z,xy[blink['start']:blink['end']],normal)>CALM_LIMIT):
            blink=None
        if blink:                                   # Blitze müssen auch in dieser Aufnahme selbst zu sehen sein
            source_raw=sum(sample(k,source) for k in (-1,0,1))/3-sum(sample(k,source) for k in (-9,-7,7,9))/4
            if blink_pattern(source_raw,cv2.remap(dash_valid,xy[:,0].astype('float32')[None],xy[:,1].astype('float32')[None],cv2.INTER_NEAREST)[0]>0) is None: blink=None
        # Score contiguous 24px bins; a chance chain of stars cannot support an entire line.
        bins=[]
        for start in range(0,len(xy)-23,24):
            keep=valid[start:start+24]
            bins.append(float(np.mean(contrast[start:start+24][keep])) if keep.sum()>=12 else 0.)
        bins=np.asarray(bins)
        best=_best_section(bins)
        if best is None and blink:                  # gestrichelte Spur: als Ganzes kein durchgehender Abschnitt
            p1=xy[blink['start']]/scale; p2=xy[blink['end']-1]/scale
            if np.linalg.norm(p2-p1)<90: continue
            midpoint=(p1+p2)/2
            if any(abs(np.dot(midpoint-np.mean(c['segment'],axis=0),normal))<12 and abs(np.dot(direction,c['_direction']))>.995 for c in candidates): continue
            height,width=image.shape
            candidates.append(_plane(p1,p2,width,height,blink,scale,direction))
            if len(candidates)>=12: break
            continue
        if best is None: continue
        snr,start,end=best
        short=bool(end-start<SHORT_BINS)
        # Partial readout rows/columns (M74 img-0292) mimic short faint streaks. Only short
        # sections within 1° of a sensor axis are dropped; long axis-parallel streaks stay.
        if short and min(abs(direction[0]),abs(direction[1]))<.0175: continue
        # Short sections near bright stars, halos or nebula edges are the typical false alarm.
        if short and max(local_structure(z,xy[start*24:end*24],normal),local_structure(source,xy[start*24:end*24],normal))>CALM_LIMIT: continue
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
        if np.linalg.norm(p2-p1)<90: continue
        # Nearby parallel Hough solutions describe the same measured streak.
        midpoint=(p1+p2)/2
        if any(abs(np.dot(midpoint-np.mean(c['segment'],axis=0),normal))<12 and abs(np.dot(direction,c['_direction']))>.995 for c in candidates): continue
        height,width=image.shape
        if blink and blink['end']>start*24 and blink['start']<end*24:     # durchgehender Abschnitt mit Blitzen
            q1,q2=min(start*24,blink['start']),max(end*24,blink['end'])
            candidates.append(_plane(xy[q1]/scale,xy[q2-1]/scale,width,height,blink,scale,direction,snr,required_score(end-start)))
            if len(candidates)>=12: break
            continue
        candidates.append(dict(kind='trail',label='Einzelbild-Spur (Meteor/Satellit/Flugzeug?)',confidence=None,box=[float(max(0,min(p1[0],p2[0])-8)/width),float(max(0,min(p1[1],p2[1])-8)/height),float(min(width,max(p1[0],p2[0])+8)/width),float(min(height,max(p1[1],p2[1])+8)/height)],segment=[p1.tolist(),p2.tolist()],line_snr=round(snr,2),line_threshold=round(required_score(end-start),2),short_trail=short,method='Einzelbild: Sensorbanding-Korrektur + Hintergrundabzug + Hough + integrierter Linienkontrast',_direction=direction))
        if len(candidates)>=12: break
    merged=[]
    for candidate in sorted(candidates,key=lambda c:(c.get('blink') is not None,c['line_snr']),reverse=True):
        segment=np.asarray(candidate['segment']); midpoint=segment.mean(axis=0)
        duplicate=False
        for kept in merged:
            other=np.asarray(kept['segment']); direction=kept['_direction']; normal=np.array([-direction[1],direction[0]])
            a=segment@direction; b=other@direction
            overlapping=min(a.max(),b.max())>max(a.min(),b.min())
            if abs(np.dot(candidate['_direction'],direction))>.99 and abs(np.dot(midpoint-other.mean(axis=0),normal))<14 and overlapping: duplicate=True; break
            # A short fragment at a slight angle along a stronger streak is the same streak.
            if candidate['short_trail'] and abs(np.dot(midpoint-other.mean(axis=0),normal))<16 and b.min()-8<=midpoint@direction<=b.max()+8: duplicate=True; break
        if not duplicate: merged.append(candidate)
    for candidate in merged: candidate.pop('_direction')
    return merged
