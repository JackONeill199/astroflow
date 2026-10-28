"""Reject intra-exposure telescope shake using the shape of many stars."""
import numpy as np
from scipy.ndimage import gaussian_filter,maximum_filter,label


def extended_star_tails(smooth,noise,centers):
    """Measure faint connected star tails beyond the bright core, across the field."""
    radius=32;yy,xx=np.mgrid[-radius:radius+1,-radius:radius+1];distance=np.hypot(xx,yy)
    measured=0;tails=[];quadrants=set()
    for x,y in centers:
        if x<radius or y<radius or x>=smooth.shape[1]-radius or y>=smooth.shape[0]-radius:continue
        patch=smooth[y-radius:y+radius+1,x-radius:x+radius+1]
        peak=float(patch[radius,radius])
        # A weak maximum along a star's tail must not become a second reference
        # star with the bright core interpreted as a tail in the opposite direction.
        if peak<.25*float(patch.max()):continue
        threshold=max(3*noise,peak*.015)
        components,_=label(patch>threshold);component=components[radius,radius]
        if not component:continue
        measured+=1
        signal=np.where(components==component,np.maximum(patch-threshold,0),0)
        mask=(signal>0)&(distance>6.5)
        if mask.sum()<8:continue
        weights=signal[mask];flux=float(weights.sum())
        extent=float(np.percentile(distance[mask],95))
        dx=float((weights*xx[mask]).sum()/flux);dy=float((weights*yy[mask]).sum()/flux)
        if extent<12 or np.hypot(dx,dy)<6 or flux<.025*float(signal.sum()):continue
        tails.append((float(np.arctan2(dy,dx)),extent))
        quadrants.add((int(x>=smooth.shape[1]/2),int(y>=smooth.shape[0]/2)))
    coherence=float(abs(np.mean(np.exp(1j*np.asarray([t[0] for t in tails]))))) if tails else 0.
    angle=float(np.degrees(np.angle(np.mean(np.exp(1j*np.asarray([t[0] for t in tails])))))) if tails else None
    return dict(tail_stars=len(tails),tail_reference_stars=measured,tail_fraction=round(len(tails)/max(1,measured),3),
                tail_direction_coherence=round(coherence,3),tail_angle_degrees=round(angle,2) if angle is not None else None,
                tail_extent_pixels=round(float(np.median([t[1] for t in tails])),2) if tails else None,tail_quadrants=len(quadrants))


def measure_quality(data):
    high=data.astype('float32')-gaussian_filter(data.astype('float32'),12)
    smooth=gaussian_filter(high,.7)
    noise=max(.1,float(1.4826*np.median(np.abs(smooth[::2,::2]-np.median(smooth[::2,::2])))))
    mask=(smooth==maximum_filter(smooth,11))&(smooth>8*noise)
    mask[:14]=mask[-14:]=False; mask[:,:14]=mask[:,-14:]=False
    ys,xs=np.where(mask)
    order=np.argsort(smooth[ys,xs])[::-1]
    measurements=[]; used=[]
    yy,xx=np.mgrid[-12:13,-12:13]
    for idx in order:
        x,y=int(xs[idx]),int(ys[idx])
        if any((x-u)**2+(y-v)**2<24**2 for u,v in used): continue
        patch=smooth[y-12:y+13,x-12:x+13]
        threshold=max(2.5*noise,float(patch.max())*.12)
        signal=np.maximum(patch-threshold,0)
        components,_=label(signal>0)
        component=components[12,12]
        if not component: continue
        primary_flux=float(signal[components==component].sum())
        secondary_angle=None; secondary_flux=0
        for other in np.unique(components):
            if other in (0,component): continue
            weights=np.where(components==other,signal,0)
            mass=float(weights.sum())
            if mass<.18*primary_flux or np.count_nonzero(weights)<3: continue
            dx,dy=float((weights*xx).sum()/mass),float((weights*yy).sum()/mass)
            if 2.5<np.hypot(dx,dy)<10 and mass>secondary_flux:
                secondary_angle=float(np.arctan2(dy,dx)); secondary_flux=mass
        signal[components!=component]=0
        flux=signal.sum()
        if flux<=0 or np.count_nonzero(signal)<5: continue
        mx,my=float((signal*xx).sum()/flux),float((signal*yy).sum()/flux)
        covariance=np.array([[(signal*(xx-mx)**2).sum(),(signal*(xx-mx)*(yy-my)).sum()],[(signal*(xx-mx)*(yy-my)).sum(),(signal*(yy-my)**2).sum()]])/flux
        values,vectors=np.linalg.eigh(covariance)
        if values[0]<.4 or values[1]>100: continue
        # Compact detector defects and extended nebulae are not focus references.
        ratio=float(np.sqrt(values[1]/values[0])); width=float(2.355*np.sqrt(values.mean()))
        direction=vectors[:,1]; angle=float(np.arctan2(direction[1],direction[0]))
        measurements.append((ratio,width,angle,secondary_angle)); used.append((x,y))
        if len(measurements)>=80: break
    if len(measurements)<6:
        return dict(stars=len(measurements),measurable=False,reason='Zu wenige helle Sternprofile für eine sichere Schärfemessung')
    metrics=np.asarray([row[:3] for row in measurements]); elongated=metrics[:,0]>1.55
    angles=metrics[elongated,2]
    coherence=float(abs(np.mean(np.exp(2j*angles)))) if len(angles) else 0.
    doubles=[row[3] for row in measurements if row[3] is not None]
    double_coherence=float(abs(np.mean(np.exp(2j*np.asarray(doubles))))) if doubles else 0.
    return dict(stars=len(measurements),measurable=True,axis_ratio=round(float(np.median(metrics[:,0])),3),elongated_fraction=round(float(elongated.mean()),3),direction_coherence=round(coherence,3),fwhm=round(float(np.median(metrics[:,1])),3),double_fraction=round(len(doubles)/len(measurements),3),double_coherence=round(double_coherence,3),**extended_star_tails(smooth,noise,used))


def baseline_from_metrics(metrics):
    valid=[m for m in metrics if m.get('measurable')]
    if not valid: return None
    # Use the sharpest quarter; a majority of shaken frames must not define 'normal'.
    widths=sorted(m['fwhm'] for m in valid)
    return dict(fwhm=float(np.median(widths[:max(1,len(widths)//4)])))


def assess_quality(metrics,baseline=None):
    result=dict(metrics,excluded=False,reason='Sternprofile ausreichend scharf')
    if not metrics.get('measurable'):
        result['reason']=metrics['reason']; return result
    common_shake=metrics['elongated_fraction']>=.3 and metrics['axis_ratio']>=1.45 and metrics['direction_coherence']>=.65
    doubled=metrics.get('double_fraction',0)>=.35 and metrics.get('double_coherence',0)>=.7
    severely_distorted=metrics['elongated_fraction']>=.7 and metrics['axis_ratio']>=1.9
    broadened=baseline is not None and metrics['fwhm']>max(baseline['fwhm']*1.6,baseline['fwhm']+1.5)
    field_tails=(metrics.get('tail_reference_stars',0)>=20 and metrics.get('tail_stars',0)>=10 and
                 metrics.get('tail_fraction',0)>=.2 and metrics.get('tail_direction_coherence',0)>=.75 and metrics.get('tail_quadrants',0)>=3)
    if field_tails:
        result.update(excluded=True,reason='Verwackelt: gleichgerichtete schwache Sternschweife über das gesamte Bildfeld',field_motion_evidence='extended_star_tails')
    elif common_shake or severely_distorted or doubled:
        result.update(excluded=True,reason='Verwackelt: viele Sterne sind gleichgerichtet länglich oder doppelt')
    elif broadened:
        result.update(excluded=True,reason='Unscharf: Sternprofile deutlich breiter als in den scharfen Referenzaufnahmen')
    if baseline: result['reference_fwhm']=round(baseline['fwhm'],3)
    return result


def calibrate(paths,stopped=lambda:False):
    from analyse import load_image
    indices=np.unique(np.linspace(0,len(paths)-1,min(17,len(paths)),dtype=int))
    metrics=[]
    for index in indices:
        if stopped(): break
        try:
            data,_=load_image(paths[index]); metrics.append(measure_quality(data))
        except (OSError,ValueError): continue
    return baseline_from_metrics(metrics)
