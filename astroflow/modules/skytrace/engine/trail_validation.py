"""Shape and simultaneous-event checks; multiple detections alone are never rejected."""
import numpy as np
from scipy.ndimage import gaussian_filter,label


def straightness(image,detection):
    height,width=image.shape
    x1,y1,x2,y2=np.asarray(detection['box'])*[width,height,width,height]
    x1,y1=max(0,int(x1)-4),max(0,int(y1)-4);x2,y2=min(width,int(x2)+5),min(height,int(y2)+5)
    patch=np.asarray(image[y1:y2,x1:x2],dtype='float32')
    result=dict(status='not_checkable',curved=False)
    if patch.size<40 or min(patch.shape)<5:return result
    high=gaussian_filter(patch,.7)-gaussian_filter(patch,8)
    noise=max(.1,float(1.4826*np.median(np.abs(high-np.median(high)))))
    components,count=label(high>max(3*noise,float(high.max())*.025))
    options=[]
    for component in range(1,count+1):
        yy,xx=np.where(components==component)
        if len(xx)<20:continue
        coords=np.column_stack([xx,yy]).astype('float64')
        values,vectors=np.linalg.eigh(np.cov(coords.T))
        if values[1]<20 or values[1]<5*max(values[0],.1):continue
        direction=vectors[:,1];normal=np.array([-direction[1],direction[0]])
        along=coords@direction;across=coords@normal
        lo,hi=np.percentile(along,[2,98]);length=float(hi-lo)
        if length<30:continue
        bins=[];widths=[]
        for start,end in zip(np.linspace(lo,hi,11)[:-1],np.linspace(lo,hi,11)[1:]):
            selected=(along>=start)&(along<=end)
            if selected.sum()<2:continue
            values_across=across[selected]
            bins.append([float(np.mean(along[selected])),float(np.mean(values_across))])
            widths.append(float(np.std(values_across)))
        if len(bins)<7:continue
        bins=np.asarray(bins);fit=np.polyval(np.polyfit(bins[:,0],bins[:,1],1),bins[:,0])
        residual=bins[:,1]-fit
        bend=float(np.max(np.abs(residual)));ridge_width=max(.5,float(np.median(widths)))
        limit=max(2.,.025*length,1.5*ridge_width)
        angle=float(np.degrees(np.arctan2(direction[1],direction[0]))%180)
        options.append(dict(status='measured',curved=bool(bend>limit),bend_pixels=round(bend,3),
            bend_limit_pixels=round(limit,3),length_pixels=round(length,3),ridge_width_pixels=round(ridge_width,3),
            supported_bins=len(bins),angle_degrees=round(angle,3)))
    return max(options,key=lambda o:o['length_pixels']) if options else result


def validate_detections(image,detections,quality):
    """Only a distributed, star-supported burst strengthens the global-motion veto."""
    measured=[]
    for detection in detections:
        item=dict(detection)
        # Integrated straight-line detections and independently tracked movers have
        # their own geometry validation. This checks previously box-only proposals.
        if item.get('line_snr') is None and item.get('track_id') is None:
            item['shape_validation']=straightness(image,item)
        measured.append(item)
    proposed=[d for d in measured if d.get('line_snr') is None and d.get('track_id') is None]
    centers=np.asarray([[(d['box'][0]+d['box'][2])/2,(d['box'][1]+d['box'][3])/2] for d in proposed])
    angles=[d['shape_validation']['angle_degrees'] for d in proposed if d.get('shape_validation',{}).get('status')=='measured']
    coherence=float(abs(np.mean(np.exp(2j*np.radians(angles))))) if len(angles)>=3 else 0.
    distributed=len(centers)>=3 and bool(np.ptp(centers,axis=0).min()>.35)
    star_supported=(quality.get('tail_stars',0)>=8 and quality.get('tail_fraction',0)>=.15 and
                    quality.get('tail_direction_coherence',0)>=.7 and quality.get('tail_quadrants',0)>=3)
    # Elongated star cores in a common direction are independent evidence too (M45 img 36: 42 % elongated,
    # coherence 0.84, but median axis ratio 1.29 just below the shake limit and tails measured incoherently).
    core_supported=quality.get('elongated_fraction',0)>=.3 and quality.get('direction_coherence',0)>=.75
    star_supported=star_supported or core_supported
    burst=bool(distributed and coherence>=.85 and star_supported)
    context=dict(simultaneous_detections=len(detections),distributed_model_proposals=bool(distributed),
                 proposal_direction_coherence=round(coherence,3),star_supported_burst=burst,
                 note='Anzahl allein ist kein Ausschlusskriterium; Sternschweife und Form müssen den Bewegungsverdacht stützen.')
    kept=[];rejected=[]
    for item in measured:
        raw=item.get('line_snr') is None and item.get('track_id') is None
        if raw and (burst or item.get('shape_validation',{}).get('curved')):
            reason='Gleichzeitige verteilte Erkennungen stimmen mit gemeinsamen Sternschweifen überein' if burst else 'Gemessene Spur ist deutlich gekrümmt; keine gerade Einzelbildspur'
            rejected.append(dict(detection=item,reason=reason))
        else:kept.append(item)
    return kept,rejected,context
