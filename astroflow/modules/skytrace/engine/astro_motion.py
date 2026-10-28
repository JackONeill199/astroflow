"""Ensemble astrometry: measure all stars, subtract field motion, test independent tracks."""
import cv2
import numpy as np
from scipy.ndimage import gaussian_filter, maximum_filter
from scipy.spatial import cKDTree
from scipy.spatial import Delaunay, QhullError
from photutils.detection import DAOStarFinder
import warnings
import itertools
from astropy.utils.exceptions import AstropyWarning


def stars(image):
    image=image.astype('float32')
    high=image-gaussian_filter(image,10)
    sample=high[::2,::2]
    sigma=max(.25,float(1.4826*np.median(np.abs(sample-np.median(sample)))))
    finder=DAOStarFinder(4.5*sigma,fwhm=3.2,exclude_border=True,n_brightest=200,sharpness_range=(.25,.95),roundness_range=(-.8,.8))
    with warnings.catch_warnings():
        warnings.simplefilter('ignore',AstropyWarning)
        table=finder(high)
    if table is None: return np.empty((0,2),dtype='float32')
    return np.column_stack([table['x_centroid'],table['y_centroid']]).astype('float32')


def _fit(reference,current,guess,image_shape):
    predicted=cv2.transform(reference[None],np.asarray(guess,dtype='float64'))[0]
    distance,nearest=cKDTree(current).query(predicted)
    pairs=[]; used=set()
    for i in np.argsort(distance):
        if distance[i]<7 and int(nearest[i]) not in used:
            pairs.append((i,int(nearest[i]))); used.add(int(nearest[i]))
    if len(pairs)<4: return None
    ref=np.asarray([reference[i] for i,j in pairs]); observed=np.asarray([current[j] for i,j in pairs])
    options=[]
    if len(pairs)>=12:
        matrix,inliers=cv2.estimateAffine2D(ref,observed,method=cv2.RANSAC,ransacReprojThreshold=.8,maxIters=3000,confidence=.999,refineIters=20)
        if matrix is not None and int(inliers.sum())>=12: options.append((matrix,inliers,'affine'))
    matrix,inliers=cv2.estimateAffinePartial2D(ref,observed,method=cv2.RANSAC,ransacReprojThreshold=.6,maxIters=3000,confidence=.999,refineIters=20)
    if matrix is not None and int(inliers.sum())>=4:
        selected=inliers.ravel().astype(bool); a,b=ref[selected],observed[selected]
        spread=np.ptp(a,axis=0)/np.array([image_shape[1],image_shape[0]])
        errors=[]
        for omit in range(len(a)):
            keep=np.arange(len(a))!=omit
            test,_=cv2.estimateAffinePartial2D(a[keep],b[keep],method=cv2.LMEDS)
            if test is None: errors.append(np.inf)
            else: errors.append(float(np.linalg.norm((test@np.append(a[omit],1))-b[omit])))
        if spread.min()>.18 and max(errors)<.6: options.append((matrix,inliers,'similarity_crossvalidated'))
    if not options: return None
    matrix,inliers,model=max(options,key=lambda item:int(item[1].sum()))
    errors=np.linalg.norm(observed-cv2.transform(ref[None],matrix)[0],axis=1)
    errors=errors[inliers.ravel().astype(bool)]
    median=float(np.median(errors)); scatter=float(1.4826*np.median(np.abs(errors-median)))
    ratio=float(inliers.mean()); scale=np.linalg.det(matrix[:,:2])
    accepted=median<=.45 and ratio>=.5 and .96<scale<1.04
    return dict(matrix=matrix,matched=len(pairs),inliers=int(inliers.sum()),median_error=median,scatter=scatter,inlier_fraction=ratio,accepted=bool(accepted),model=model)


def _triangles(points):
    if len(points)<4: return np.empty((0,2)),[]
    try: indices=np.asarray(list(itertools.combinations(range(len(points)),3))) if len(points)<=12 else Delaunay(points).simplices
    except QhullError: return np.empty((0,2)),[]
    descriptors=[]; canonical=[]
    for tri in indices:
        vertices=points[tri]
        lengths=np.array([np.linalg.norm(vertices[1]-vertices[2]),np.linalg.norm(vertices[0]-vertices[2]),np.linalg.norm(vertices[0]-vertices[1])])
        order=np.argsort(lengths); sides=lengths[order]
        if sides[0]<15 or sides[2]<40 or (sides[0]+sides[1]-sides[2])/sides[2]<.04: continue
        descriptors.append(sides[:2]/sides[2]); canonical.append(vertices[order])
    return np.asarray(descriptors).reshape(-1,2),canonical


def _pattern_guess(reference,current):
    desc_ref,tri_ref=_triangles(reference); desc_cur,tri_cur=_triangles(current)
    if not len(desc_ref) or not len(desc_cur): return None
    distances,indices=cKDTree(desc_cur).query(desc_ref,k=3,distance_upper_bound=.012)
    candidates=sorted((float(distances[i,j]),i,int(indices[i,j])) for i in range(len(desc_ref)) for j in range(3) if np.isfinite(distances[i,j]))[:300]
    tree=cKDTree(current); best=None; support=3
    for _,i,j in candidates:
        matrix=cv2.getAffineTransform(np.asarray(tri_ref[i],dtype='float32'),np.asarray(tri_cur[j],dtype='float32'))
        singular=np.linalg.svd(matrix[:,:2],compute_uv=False)
        if np.linalg.det(matrix[:,:2])<0 or singular.min()<.97 or singular.max()>1.03: continue
        predicted=cv2.transform(reference[None],matrix)[0]
        distance,nearest=tree.query(predicted)
        count=len(set(nearest[distance<1.3].tolist()))
        if count>support: best=matrix; support=count
    return best


class EnsembleMotion:
    def __init__(self):
        self.reference=None; self.tracks=[]; self.next_id=1
        self.quality={}; self.matrix=None; self.anchor_points=None; self.anchor_matrix=None

    def update(self,image,index,initial=None,source_points=None):
        current=np.asarray(source_points,dtype='float32').reshape(-1,2) if source_points is not None else stars(image)
        h,w=image.shape
        self.quality=dict(stars=len(current),matched=0,accepted=False,reason='Zu wenige gemessene Sterne')
        if self.reference is None:
            if len(current)<4: return []
            self.reference=current.copy(); self.matrix=np.eye(2,3)
            self.anchor_points=current.copy(); self.anchor_matrix=self.matrix.copy()
            self.tracks=[dict(id=i+1,points=[(index,float(x),float(y))],sensor_points=[(float(x),float(y))],last=index) for i,(x,y) in enumerate(current)]
            self.next_id=len(self.tracks)+1
            self.quality.update(matched=len(current),accepted=True,reason='Referenz-Sternfeld')
            return []
        if len(current)<4: return []
        fits=[_fit(self.reference,current,g,image.shape) for g in (initial,self.matrix) if g is not None]
        fits=[f for f in fits if f is not None]
        if not any(f['accepted'] for f in fits):
            guess=_pattern_guess(self.reference,current)
            if guess is not None:
                fit=_fit(self.reference,current,guess,image.shape)
                if fit: fits.append(fit)
        if not any(f['accepted'] for f in fits) and self.anchor_points is not None:
            # Fresh reference stars can replace first-frame stars that faded or left the field.
            anchor=np.vstack([self.anchor_matrix,[0,0,1]])
            guesses=[np.eye(2,3)]
            if initial is not None: guesses.append((np.vstack([initial,[0,0,1]])@np.linalg.inv(anchor))[:2])
            local=[_fit(self.anchor_points,current,g,image.shape) for g in guesses]
            local=[fit for fit in local if fit is not None and fit['accepted']]
            if not local:
                guess=_pattern_guess(self.anchor_points,current)
                fit=_fit(self.anchor_points,current,guess,image.shape) if guess is not None else None
                if fit and fit['accepted']: local.append(fit)
            for fit in local:
                fit['matrix']=(np.vstack([fit['matrix'],[0,0,1]])@anchor)[:2]
                fit['reference']='last_valid_frame'
                fits.append(fit)
        if not fits:
            self.quality['reason']='Keine zuverlässige Sternmuster-Zuordnung'
            return []
        best=max(fits,key=lambda f:(f['accepted'],f['inliers'],-f['median_error']))
        matrix=best.pop('matrix')
        self.quality.update(best,shift_pixels=[round(float(v),3) for v in matrix[:,2]])
        if not best['accepted']:
            self.quality['reason']='Gemeinsame Sternbewegung/Verwacklung nicht zuverlässig kompensiert'
            return []
        self.matrix=matrix
        self.anchor_points=current.copy(); self.anchor_matrix=matrix.copy()
        self.quality.update(accepted=True,reason='Gemeinsame Bewegung des Sternfelds abgezogen')
        inverse=cv2.invertAffineTransform(matrix)
        aligned=cv2.transform(current[None],inverse)[0]
        used=set()
        for source_index,point in enumerate(aligned):
            options=[t for t in self.tracks if t['id'] not in used and 0<index-t['last']<=2 and np.linalg.norm(point-np.asarray(t['points'][-1][1:]))<5]
            track=min(options,key=lambda t:np.linalg.norm(point-np.asarray(t['points'][-1][1:]))) if options else None
            if track is None:
                track=dict(id=self.next_id,points=[],sensor_points=[],last=index); self.next_id+=1; self.tracks.append(track)
            track['points'].append((index,float(point[0]),float(point[1]))); track['last']=index; used.add(track['id'])
            track['sensor_points'].append(tuple(float(v) for v in current[source_index]))
        self.tracks=[t for t in self.tracks if index-t['last']<=2]
        eligible=[t for t in self.tracks if t['last']==index and len(t['points'])>=8]
        if len(eligible)<4: return []
        movements=np.array([np.asarray(t['points'][-1][1:])-np.asarray(t['points'][-8][1:]) for t in eligible])
        common=np.median(movements,axis=0)
        ensemble_error=np.linalg.norm(movements-common,axis=1)
        jitter=max(.15,float(np.median(ensemble_error)+1.4826*np.median(np.abs(ensemble_error-np.median(ensemble_error)))))
        self.quality['ensemble_jitter']=round(jitter,3)
        results=[]
        for track,delta in zip(eligible,movements):
            if np.linalg.norm(delta-common)<max(2.5,8*jitter): continue
            samples=np.asarray(track['points'][-16:])
            if len(samples)<8: continue
            coefficient=np.polyfit(samples[:,0],samples[:,1:],1)
            fitted=samples[:,0,None]*coefficient[0]+coefficient[1]
            rms=float(np.sqrt(np.mean((samples[:,1:]-fitted)**2)))
            motion=float(np.linalg.norm(samples[-1,1:]-samples[0,1:]))
            if rms>max(.45,2*jitter) or motion<max(3,10*jitter): continue
            sensor=np.asarray(track['sensor_points'][-len(samples):])
            if np.linalg.norm(sensor[-1]-sensor[0])<2: continue
            # A detection must be an outlier against the ensemble, not one of many coherent drifts.
            similar=np.linalg.norm(movements-delta,axis=1)<max(.75,3*jitter)
            if similar.sum()>max(2,int(.1*len(eligible))): continue
            track['outlier_votes']=track.get('outlier_votes',0)+1 if track.get('last_outlier')==index-1 else 1
            track['last_outlier']=index
            if track['outlier_votes']<3: continue
            path=cv2.transform(samples[None,:,1:].astype('float32'),matrix)[0].tolist()
            x,y=path[-1]
            results.append(dict(kind='moving',label='Unabhaengiger Beweger (unbestaetigt)',confidence=None,box=[max(0,x-12)/w,max(0,y-12)/h,min(w,x+12)/w,min(h,y+12)/h],track_id=track['id'],path=path,motion_pixels=round(motion,3),fit_rms=round(rms,3),ensemble_jitter=round(jitter,3),reference_stars=len(eligible),method='Sternzentren + affine RANSAC-Sternfeldbewegung + Ensemble-Ausreissertest'))
        return results
