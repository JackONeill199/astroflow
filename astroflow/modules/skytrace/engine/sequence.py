"""Time ordered original image sequences, local event detection and annotated MP4."""
import datetime as dt
import hashlib
import html
import json
import os
from pathlib import Path
import re

import cv2
import numpy as np
from scipy.ndimage import gaussian_filter, maximum_filter,label

from analyse import PHOTO, ASSETS, BASE, event, load_image
from image_store import write_image
from astro_motion import EnsembleMotion
from line_detection import detect_lines, merge_streak_detections
from frame_quality import measure_quality,assess_quality,calibrate
from motion_plausibility import narrow_field

FITS = {'.fits', '.fit', '.fts'}


class MovieWriter:
    def __init__(self,path,fps,shape):
        import av
        from fractions import Fraction
        self.av=av
        self.container=av.open(str(path),'w',options={'movflags':'+faststart'})
        self.stream=self.container.add_stream('libx264',rate=Fraction(str(fps)))
        self.stream.width=shape[1]; self.stream.height=shape[0]
        self.stream.pix_fmt='yuv420p'
        self.stream.options={'crf':'20','preset':'fast'}
        self.closed=False

    def write(self,image):
        frame=self.av.VideoFrame.from_ndarray(image,format='bgr24')
        for packet in self.stream.encode(frame): self.container.mux(packet)

    def release(self):
        if self.closed: return
        self.closed=True
        try:
            for packet in self.stream.encode(): self.container.mux(packet)
        finally: self.container.close()


def timestamp(path):
    if path.suffix.lower() in FITS:
        from astropy.io import fits
        try:
            header = fits.getheader(path)
            text = str(header.get('DATE-OBS', ''))
            stamp = dt.datetime.fromisoformat(text.replace('Z', '+00:00'))
            if stamp.tzinfo is None: stamp = stamp.replace(tzinfo=dt.timezone.utc)
            return stamp.timestamp(), stamp.astimezone(dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%S UTC')
        except (OSError, ValueError, TypeError): pass
    match = re.search(r'(20\d\d-\d\d-\d\d)[_T-](\d\d)[-:](\d\d)[-:](\d\d)', path.name)
    if match:
        value = dt.datetime.fromisoformat(f'{match[1]}T{match[2]}:{match[3]}:{match[4]}')
        return value.replace(tzinfo=dt.timezone.utc).timestamp(), value.isoformat()+' (Dateiname; Zeitzone unbekannt)'
    return None, 'Aufnahmezeit unbekannt'


def natural(path):
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r'(\d+)', str(path))]


def discover(root):
    """Use initial exposures only, never mix stacks, processed copies or calibrations."""
    roots = [root] if '_observation_' in root.name else sorted([p for p in root.iterdir() if p.is_dir() and '_observation_' in p.name])
    if not roots: roots = [root]
    groups = []
    for folder in roots:
        initial = folder/'01-images-initial'
        search = initial if initial.is_dir() else folder
        paths = [p for p in search.rglob('*') if p.is_file() and p.suffix.lower() in PHOTO and not any(t in str(p.relative_to(search)).lower() for t in ('dark', 'flat', 'bias', 'stack', 'processed', 'result', 'as_p', 'dss'))]
        raw = [p for p in paths if p.suffix.lower() in FITS]
        paths = raw or [p for p in paths if p.suffix.lower() in {'.jpg','.jpeg','.png','.tif','.tiff'}]
        # One acquisition series per directory; duplicate exports must not become extra frames.
        parents = {}
        for p in paths: parents.setdefault(p.parent, []).append(p)
        for parent, images in sorted(parents.items()):
            if len(images) < 2: continue
            if not raw:
                ext = max({p.suffix.lower() for p in images}, key=lambda e: sum(p.suffix.lower()==e for p in images))
                images = [p for p in images if p.suffix.lower()==ext]
            groups.append((folder.name + ((' / '+str(parent.relative_to(folder))) if parent != initial and parent != folder else ''), images))
    return groups


def stretch(data, limits, max_width=1536):
    lo, hi = limits
    centered=data-float(np.median(data))
    normalized = np.clip((centered-lo)/max(hi-lo, 1), 0, 1)
    gray = (np.arcsinh(normalized*8)/np.arcsinh(8)*255).astype('uint8')
    scale = min(1, max_width/gray.shape[1])
    width, height = max(2,int(gray.shape[1]*scale)//2*2), max(2,int(gray.shape[0]*scale)//2*2)
    return cv2.resize(gray, (width,height), interpolation=cv2.INTER_AREA)


def register(previous, current):
    points = cv2.goodFeaturesToTrack(previous, 400, .035, 12, blockSize=5)
    if points is None or len(points)<12: return None, 0
    moved, status, error = cv2.calcOpticalFlowPyrLK(previous, current, points, None, winSize=(31,31), maxLevel=3)
    if moved is None: return None, 0
    mask = (status.ravel()==1) & (error.ravel()<30)
    if mask.sum()<12: return None, 0
    matrix, inliers = cv2.estimateAffinePartial2D(points[mask], moved[mask], method=cv2.RANSAC, ransacReprojThreshold=1.5)
    if matrix is None or inliers.sum()<12: return None, 0
    scale = np.hypot(matrix[0,0],matrix[0,1])
    if not .98<scale<1.02: return None, 0
    return matrix, int(inliers.sum())


def streaks(gray, previous, transform):
    if previous is None or transform is None: return []
    h,w = gray.shape
    aligned = cv2.warpAffine(previous, transform, (w,h), borderMode=cv2.BORDER_REFLECT)
    diff = gaussian_filter(gray.astype('float32')-aligned.astype('float32'), .8)
    noise = max(1., float(1.4826*np.median(np.abs(diff-np.median(diff)))))
    mask = ((diff>max(14,6*noise))*255).astype('uint8')
    # Never interpret the invalid warp border as a trail.
    valid = cv2.warpAffine(np.ones_like(gray),transform,(w,h),flags=cv2.INTER_NEAREST)
    valid = cv2.erode(valid,np.ones((21,21),dtype='uint8'))
    mask[valid==0]=0
    lines = cv2.HoughLinesP(mask,1,np.pi/180,threshold=30,minLineLength=35,maxLineGap=12)
    found=[]
    if lines is not None:
        for x1,y1,x2,y2 in lines.reshape(-1,4):
            box=[max(0,min(x1,x2)-8)/w,max(0,min(y1,y2)-8)/h,min(w,max(x1,x2)+8)/w,min(h,max(y1,y2)+8)/h]
            if any(overlap(box, row['box'])>.2 for row in found): continue
            found.append(dict(label='Spurkandidat: Meteor/Satellit/Flugzeug',confidence=None,box=box,segment=[[int(x1),int(y1)],[int(x2),int(y2)]],method='Registrierte Bilddifferenz + Hough',kind='trail'))
            if len(found)>=12: break
    return found


def overlap(a,b):
    intersection=max(0,min(a[2],b[2])-max(a[0],b[0]))*max(0,min(a[3],b[3])-max(a[1],b[1]))
    union=(a[2]-a[0])*(a[3]-a[1])+(b[2]-b[0])*(b[3]-b[1])-intersection
    return intersection/max(union,1e-9)


def transient_shape(gray, comparison, matrix, box):
    """Reject fixed stars/galaxies and pointlike YOLO false positives on telescope images."""
    if comparison is None or matrix is None: return False
    h,w=gray.shape
    x1,y1,x2,y2=[int(v*s) for v,s in zip(box,[w,h,w,h])]
    if max(x2-x1,y2-y1)<35: return False
    x1,y1,x2,y2=max(1,x1-4),max(1,y1-4),min(w-1,x2+4),min(h-1,y2+4)
    if x2<=x1 or y2<=y1: return False
    source=gray[y1:y2,x1:x2].astype('float32')
    ridge=gaussian_filter(source,.7)-gaussian_filter(source,8)
    sigma=max(1.,float(1.4826*np.median(np.abs(ridge-np.median(ridge)))))
    components,count=label(ridge>max(4*sigma,float(ridge.max())*.15))
    elongated=False
    for component in range(1,count+1):
        cy,cx=np.where(components==component)
        if len(cx)<12: continue
        eig=np.linalg.eigvalsh(np.cov(np.column_stack([cx,cy]).T))
        if eig[1]>20 and eig[1]/max(eig[0],.1)>5:
            elongated=True; break
    if not elongated: return False
    aligned=cv2.warpAffine(comparison,matrix,(w,h),borderMode=cv2.BORDER_REFLECT)
    diff=gaussian_filter(gray[y1:y2,x1:x2].astype('float32')-aligned[y1:y2,x1:x2].astype('float32'),.8)
    noise=max(1.,float(1.4826*np.median(np.abs(diff-np.median(diff)))))
    ys,xs=np.where(diff>max(12,5*noise))
    if len(xs)<12: return False
    eig=np.linalg.eigvalsh(np.cov(np.column_stack([xs,ys]).T))
    return bool(eig[1]>20 and eig[1]/max(eig[0],.1)>5)


class PersistentTrails:
    """Observed geometry stays visible; never extrapolate an object's future motion."""
    def __init__(self):
        self.records=[]

    def update(self,gray,detections,affine,index):
        if index>0 and affine is None:
            for record in self.records: record['key']=None
        if affine is not None:
            for record in self.records:
                record['points']=cv2.transform(np.asarray([record['points']],dtype='float32'),affine)[0].tolist()
        h,w=gray.shape
        for detection in detections:
            points=detection.get('path') or detection.get('segment')
            if not points:
                x1,y1,x2,y2=[int(v*s) for v,s in zip(detection['box'],[w,h,w,h])]
                x1,y1,x2,y2=max(0,x1),max(0,y1),min(w,x2),min(h,y2)
                patch=gray[y1:y2,x1:x2].astype('float32')
                # Draw only the measured source direction, not an invented box diagonal.
                if patch.size:
                    yy,xx=np.where(patch>np.percentile(patch,92))
                    coords=np.column_stack([xx+x1,yy+y1]).astype('float32')
                    if len(coords)>=4:
                        mean,vectors,values=cv2.PCACompute2(coords,mean=None)
                        if values[0,0]>4*max(values[1,0],.1):
                            projection=(coords-mean)@vectors[0]
                            points=[(mean[0]+vectors[0]*projection.min()).tolist(),(mean[0]+vectors[0]*projection.max()).tolist()]
                if points is None: points=[[(x1+x2)/2,(y1+y2)/2]]
            key=(detection['kind'],detection.get('track_id')) if detection.get('track_id') is not None else None
            record=next((r for r in self.records if key is not None and r['key']==key),None)
            if record is None:
                record=dict(id=len(self.records)+1,key=key,kind=detection['kind'],first_frame=index,points=points)
                self.records.append(record)
            else:
                record['points']=record['points']+points[-1:]
            detection['trail_id']=record['id']

    def render(self,gray):
        frame=cv2.cvtColor(gray,cv2.COLOR_GRAY2BGR)
        ink=np.zeros_like(frame)
        colors={'meteor':(70,215,255),'plane':(255,180,70),'moving':(210,90,240),'trail':(70,190,245)}
        names={'meteor':'Meteor?','plane':'Flugzeug/Satellit?','moving':'Beweger?','trail':'Spur?'}
        for record in self.records:
            color=colors.get(record['kind'],(100,240,220))
            points=np.rint(record['points']).clip(-100000,100000).astype('int32')
            if len(points)>1: cv2.polylines(ink,[points],False,color,2,cv2.LINE_AA)
            else: cv2.circle(ink,tuple(points[0]),6,color,2,cv2.LINE_AA)
        glow=cv2.GaussianBlur(ink,(0,0),4)
        frame=cv2.add(frame,cv2.add(ink,glow))
        for record in self.records:
            x,y=map(int,record['points'][-1])
            if not (0<=x<gray.shape[1] and 70<=y<gray.shape[0]-30): continue
            label=f"#{record['id']} {names.get(record['kind'],'Kandidat?')} [Bild {record['first_frame']+1}]"
            position=(max(3,min(x+9,gray.shape[1]-285)),max(85,y-10))
            cv2.putText(frame,label,position,cv2.FONT_HERSHEY_SIMPLEX,.4,(0,0,0),3,cv2.LINE_AA)
            cv2.putText(frame,label,position,cv2.FONT_HERSHEY_SIMPLEX,.4,colors.get(record['kind'],(100,240,220)),1,cv2.LINE_AA)
        return frame


def annotate(gray, detections, title, stamp, index, count, registration, fps, catalog=None, trails=None):
    frame=trails.render(gray) if trails is not None else cv2.cvtColor(gray,cv2.COLOR_GRAY2BGR)
    h,w=gray.shape
    cv2.rectangle(frame,(0,0),(w,68),(12,20,32),-1)
    cv2.putText(frame,title[:95],(12,24),cv2.FONT_HERSHEY_SIMPLEX,.55,(210,235,240),1,cv2.LINE_AA)
    cv2.putText(frame,f'{index+1}/{count} | {stamp[:66]} | Zeitraffer {fps:g} Bilder/s',(12,50),cv2.FONT_HERSHEY_SIMPLEX,.45,(190,210,220),1,cv2.LINE_AA)
    if catalog:
        for obj in catalog:
            x,y=int(obj['x']),int(obj['y'])
            cv2.circle(frame,(x,y),15,(230,190,70),1,cv2.LINE_AA)
            cv2.putText(frame,obj['name'][:38],(min(w-150,x+18),max(85,y-8)),cv2.FONT_HERSHEY_SIMPLEX,.48,(230,190,70),1,cv2.LINE_AA)
    for row in detections:
        color=(70,215,255) if row['kind']=='meteor' else (255,180,70) if row['kind']=='plane' else (210,90,240) if row['kind']=='moving' else (70,190,245)
        x1,y1,x2,y2=[int(v*s) for v,s in zip(row['box'],[w,h,w,h])]
        if row.get('segment'):
            start,end=np.rint(row['segment']).astype('int32')
            cv2.line(frame,tuple(start),tuple(end),color,2,cv2.LINE_AA)
            anchor=min(row['segment'],key=lambda point:point[1])
            x1,y1=map(int,anchor)
        else:
            cv2.rectangle(frame,(x1,y1),(x2,y2),color,2)
        label=row['label']+(f" {row['confidence']:.0%}" if row.get('confidence') is not None else '')
        cv2.putText(frame,label[:80],(max(2,min(x1,w-400)),max(85,y1-7)),cv2.FONT_HERSHEY_SIMPLEX,.47,color,1,cv2.LINE_AA)
        if row.get('path'):
            cv2.polylines(frame,[np.asarray(row['path'],dtype='int32')],False,color,2)
    foot=f'{len(detections)} Kandidaten | Sternregistrierung: '+('OK' if registration else 'nicht verfuegbar')+' | Sichtpruefung erforderlich'
    if trails is not None: foot+=f' | {len(trails.records)} bleibende Spuren (Historie)'
    cv2.rectangle(frame,(0,h-30),(w,h),(12,20,32),-1)
    cv2.putText(frame,foot,(10,h-10),cv2.FONT_HERSHEY_SIMPLEX,.44,(190,220,230),1,cv2.LINE_AA)
    return frame


def report(out, summaries):
    from report_view import render
    from json_store import write_json
    render(out,summaries)
    write_json(out/'videos.json',summaries)


def run_sequences(args):
    root,out=Path(args.source).resolve(),Path(args.output).resolve()
    if not root.is_dir(): raise ValueError('Quellordner fehlt')
    if root==out or root in out.parents: raise ValueError('Ergebnisse müssen außerhalb der Quelldaten liegen')
    out.mkdir(parents=True,exist_ok=True)
    replay=None
    if getattr(args,'input_manifest',None):
        replay=json.loads(Path(args.input_manifest).read_text(encoding='utf-8'))
        groups=[(g['session'],[Path(p) for p in g['files']]) for g in replay['groups']]
        if any(len(paths)<2 or any(not p.is_file() for p in paths) for _,paths in groups):
            raise ValueError('Originalaufnahmen fehlen. Die erneute Analyse wurde nicht gestartet.')
    else:
        groups=discover(root)
    event('status',text=f'{len(groups)} Bildfolgen gefunden. Zeitstempel werden gelesen; '+(f'maximal {args.limit} Bilder je Beobachtung.' if args.limit else 'es werden ALLE Bilder verwendet.'))
    if not groups:
        event('done',text='Keine Bildfolge mit mindestens zwei Bildern gefunden.'); return
    os.environ['YOLO_CONFIG_DIR']=str(BASE/'runtime')
    from ultralytics import YOLO
    model=YOLO(str(ASSETS/'models/night-detector/night-yolo26s-1920.pt'))
    summaries=[]
    report(out,summaries)
    for group_index,(name,paths) in enumerate(groups):
        if replay: args.fps=float(replay['groups'][group_index]['fps'])
        if (out/'STOP').exists(): break
        event('status',text=f'{name}: {len(paths)} Aufnahmezeiten lesen …')
        timed=[]
        for p in paths:
            if (out/'STOP').exists(): break
            stamp,text=timestamp(p); timed.append((p,stamp,text))
        if (out/'STOP').exists(): break
        timed.sort(key=lambda item:(item[1] is None,item[1] if item[1] is not None else 0,natural(item[0])))
        if args.limit: timed=timed[:args.limit]
        safe=re.sub(r'[^a-zA-Z0-9_.-]+','_',name)[:95]+'-'+hashlib.sha1(name.encode()).hexdigest()[:7]
        folder=out/safe; folder.mkdir(exist_ok=True)
        frames_file=(folder/'frames.jsonl').open('w',encoding='utf-8')
        events_file=(folder/'ereignisse.jsonl').open('w',encoding='utf-8')
        rejected_file=(folder/'ausgeschlossen.jsonl').open('w',encoding='utf-8')
        event('status',text=f'{name}: Schärfereferenz aus bis zu 17 Aufnahmen bestimmen …')
        quality_baseline=calibrate([row[0] for row in timed],lambda:(out/'STOP').exists())
        writer=None; previous=None; previous_line_data=None; previous_field_matrix=None; reference=None; transform_total=np.eye(3); ensemble=EnsembleMotion(); limits=None; hits=written=0
        clean_writer=None
        summary=dict(session=name,video=f'{safe}/ereignisse.mp4',events=f'{safe}/ereignisse.jsonl',frames=0,hits=0,excluded=0,source_frames=len(timed),fps=args.fps,quality_reference=quality_baseline,state='In Bearbeitung')
        if replay: summary['source_first_file']=replay['groups'][group_index].get('source_first_file')
        summaries.append(summary)
        summary['source_folder']=str(next((p for p in paths[0].parents if p.name==name),root))
        summary['limited']=len(timed)<len(paths)
        catalog=[]; solved=None; trails=PersistentTrails()
        try:
            for index,(path,stamp,stamp_text) in enumerate(timed):
                if (out/'STOP').exists(): break
                event('status',text=f'{name} · {path.name} · Erkennung läuft',current=index+1,total=len(timed))
                try: data,meta=load_image(path)
                except Exception as error:
                    event('error',text=f'{path}: {error}'); continue
                quality=assess_quality(measure_quality(data),quality_baseline)
                if quality['excluded']:
                    summary['excluded']+=1
                    rejected=dict(file=str(path),index=index,timestamp=stamp,timestamp_text=stamp_text,quality=quality)
                    rejected_file.write(json.dumps(rejected,ensure_ascii=False)+'\n'); rejected_file.flush()
                    rejected_limits=(np.percentile(data,[1,99.9])-float(np.median(data))).tolist()
                    rejected_gray=stretch(data,rejected_limits,max_width=920)
                    rejected_preview=cv2.cvtColor(rejected_gray,cv2.COLOR_GRAY2BGR)
                    cv2.rectangle(rejected_preview,(0,0),(rejected_preview.shape[1],50),(20,20,110),-1)
                    cv2.putText(rejected_preview,f'AUSGESCHLOSSEN: {path.name}',(10,22),cv2.FONT_HERSHEY_SIMPLEX,.55,(240,240,255),1,cv2.LINE_AA)
                    cv2.putText(rejected_preview,'Verwackelt / unscharf - nicht im Video und nicht als Ereignis gewertet',(10,42),cv2.FONT_HERSHEY_SIMPLEX,.4,(240,240,255),1,cv2.LINE_AA)
                    preview_file=folder/f'ausgeschlossen-{index:06d}.jpg'
                    cv2.imwrite(str(preview_file),rejected_preview)
                    clean_preview=folder/'live-clean.jpg';write_image(clean_preview,stretch(data,rejected_limits))
                    event('rejected',session=name,file=str(path),index=index,current=index+1,total=len(timed),preview_absolute=str(preview_file),preview_clean_absolute=str(clean_preview),quality=quality,timestamp_text=stamp_text,text=f'{name} · {path.name} ausgeschlossen: {quality["reason"]} · bisher {summary["excluded"]} ausgeschlossen')
                    continue
                if limits is None: limits=(np.percentile(data,[1,99.9])-float(np.median(data))).tolist()
                gray=stretch(data,limits)
                if reference is None:
                    reference=gray.copy()
                    writer=MovieWriter(folder/'ereignisse.mp4',args.fps,gray.shape)
                    clean_writer=MovieWriter(folder/'original.mp4',args.fps,gray.shape)
                    summary['clean_video']=str((folder/'original.mp4').relative_to(out).as_posix())
                    summary['display_limits']=limits
                    if getattr(args,'solve',False):
                        from solving import solve_image, objects_for_frame
                        solved=solve_image(path,data,folder,out)
                        if solved: catalog=objects_for_frame(solved,gray.shape)
                if gray.shape!=reference.shape:
                    event('error',text=f'{path.name}: Abweichende Bildgröße, übersprungen.'); continue
                affine,inliers=register(previous,gray) if previous is not None else (None,0)
                registration=affine is not None
                reference_affine,reference_inliers=register(reference,gray) if index>0 else (np.array([[1.,0.,0.],[0.,1.,0.]]),0)
                line_data=cv2.resize(data,(gray.shape[1],gray.shape[0]),interpolation=cv2.INTER_AREA)
                centroids=meta.get('_stellar_centroids')
                if centroids is not None:
                    centroids=(np.asarray(centroids)*np.array([gray.shape[1]/data.shape[1],gray.shape[0]/data.shape[0]])).tolist()
                movers=ensemble.update(line_data,index,reference_affine,centroids)
                field_ok=ensemble.quality.get('accepted',False)
                if field_ok:
                    reference_affine=ensemble.matrix
                    # Direkte Zuordnung zum tatsächlichen Nachbarbild hat Vorrang. Zwei unabhängig bestimmte
                    # Referenzlösungen können trotz akzeptiertem Sternfit gegeneinander springen.
                    if affine is None and previous_field_matrix is not None:
                        affine=(np.vstack([ensemble.matrix,[0,0,1]])@np.linalg.inv(previous_field_matrix))[:2]
                        registration=True
                reference_valid=reference_affine is not None
                if reference_valid:
                    # Direct reference registration avoids accumulated drift masquerading as asteroids.
                    transform_total=np.linalg.inv(np.vstack([reference_affine,[0,0,1]]))
                # Keep a fixed star reference; unreliable frames cannot seed independent-mover claims.
                predicted=model.predict(cv2.cvtColor(gray,cv2.COLOR_GRAY2BGR),imgsz=1920,conf=.3,device='cpu',verbose=False)[0]
                detections=[]
                line_data=cv2.resize(data,(gray.shape[1],gray.shape[0]),interpolation=cv2.INTER_AREA)
                comparison,compare_matrix,line_comparison=previous,affine,previous_line_data
                if previous is None:
                    for future_path,_,_ in timed[index+1:]:
                        if (out/'STOP').exists(): break
                        try:
                            following_data,_=load_image(future_path)
                            if assess_quality(measure_quality(following_data),quality_baseline)['excluded']: continue
                            following=stretch(following_data,limits)
                            compare_matrix,_=register(following,gray); comparison=following
                            line_comparison=cv2.resize(following_data,(gray.shape[1],gray.shape[0]),interpolation=cv2.INTER_AREA)
                            break
                        except (OSError,ValueError): continue
                for box in predicted.boxes:
                    cls=int(box.cls.item())
                    coords=box.xyxyn.tolist()[0]
                    if transient_shape(gray,comparison,compare_matrix,coords):
                        candidate=dict(label='Meteorkandidat' if cls==1 else 'Flugzeugkandidat (Satellit moeglich)',kind='meteor' if cls==1 else 'plane',confidence=round(float(box.conf.item()),4),box=coords,method='OpenSkyCam YOLO26s + registrierte transiente Spur')
                        if not any(overlap(coords,d['box'])>.35 for d in detections): detections.append(candidate)
                detections=merge_streak_detections(detections,detect_lines(line_data,line_comparison,compare_matrix),gray.shape)
                for mover in movers:
                    if solved and catalog:
                        x=(mover['box'][0]+mover['box'][2])/2*gray.shape[1]
                        y=(mover['box'][1]+mover['box'][3])/2*gray.shape[0]
                        rx,ry,_=transform_total@np.array([x,y,1])
                        sky=solved['wcs'].pixel_to_world(rx*solved['width']/gray.shape[1],ry*solved['height']/gray.shape[0])
                        mover['ra_deg']=float(sky.ra.deg); mover['dec_deg']=float(sky.dec.deg)
                detections.extend(movers)
                from trail_validation import validate_detections
                detections,rejected_shapes,frame_validation=validate_detections(line_data,detections,quality)
                model_proposals=[]
                if narrow_field(meta):
                    # OpenSkyCam was trained on wide all-sky cameras. In telescope fields it flags star trails of
                    # shaken frames and bright stars (M45 2022-09-22: 22 proposals, none real). Its class stays a hint
                    # on measured streaks; a box without a measured line is kept for review only, not as a candidate.
                    kept=[]
                    for detection in detections:
                        detection['model_scope']='narrow_field'
                        if detection.get('confidence') is not None and detection.get('line_snr') is None and detection.get('track_id') is None:
                            model_proposals.append(dict(kind=detection['kind'],confidence=detection['confidence'],box=detection['box']))
                        else: kept.append(detection)
                    detections=kept
                if rejected_shapes:
                    summary['shape_rejected']=summary.get('shape_rejected',0)+len(rejected_shapes)
                    with (folder/'formartefakte.jsonl').open('a',encoding='utf-8') as rejected_shapes_file:
                        for rejected_shape in rejected_shapes:
                            rejected_shapes_file.write(json.dumps(dict(file=str(path),index=index,frame_validation=frame_validation,**rejected_shape),ensure_ascii=False)+'\n')
                from motion_plausibility import assess_motion
                for detection in detections:
                    points=detection.get('segment') or detection.get('path')
                    world=None
                    if points and len(points)>1 and solved and reference_valid:
                        pixels=cv2.transform(np.asarray(points,dtype='float64')[None],cv2.invertAffineTransform(reference_affine))[0]
                        pixels*=np.array([solved['width']/gray.shape[1],solved['height']/gray.shape[0]])
                        world=solved['wcs'].all_pix2world(pixels,0)
                    detection['plausibility']=assess_motion(detection,meta,gray.shape,world)
                current_catalog=catalog
                if catalog and index>0:
                    inverse=np.linalg.inv(transform_total)
                    current_catalog=[dict(obj,x=float((inverse@np.array([obj['x'],obj['y'],1]))[0]),y=float((inverse@np.array([obj['x'],obj['y'],1]))[1])) for obj in catalog]
                trails.update(gray,detections,affine,index)
                drawn=annotate(gray,detections,name,stamp_text,index,len(timed),registration,args.fps,current_catalog,trails)
                writer.write(drawn); written+=1
                clean_writer.write(cv2.cvtColor(gray,cv2.COLOR_GRAY2BGR))
                row=dict(file=str(path),index=index,video_frame=written-1,video_seconds=round((written-1)/args.fps,4),timestamp=stamp, timestamp_text=stamp_text,meta=meta,quality=quality,registration_inliers=inliers,star_field=ensemble.quality,detections=detections,persistent_trails=len(trails.records),previous_to_current=affine.tolist() if affine is not None else None,reference_to_current=reference_affine.tolist() if reference_valid else None)
                row['frame_validation']=frame_validation
                if model_proposals: row['model_proposals']=model_proposals
                frames_file.write(json.dumps(row,ensure_ascii=False)+'\n'); frames_file.flush()
                preview_file=folder/'live.jpg'
                write_image(preview_file,drawn)
                clean_preview=folder/'live-clean.jpg';write_image(clean_preview,gray)
                if detections:
                    hits+=1; events_file.write(json.dumps(row,ensure_ascii=False)+'\n'); events_file.flush()
                    preview_file=folder/f'treffer-{index:06d}.jpg'; cv2.imwrite(str(preview_file),drawn)
                event('sequence',session=name,file=str(path),preview_absolute=str(preview_file),preview_clean_absolute=str(clean_preview),quality=quality,star_field=ensemble.quality,timestamp_text=stamp_text,text=f'{name} · {index+1}/{len(timed)} · {len(detections)} Kandidaten · {hits} Trefferbilder · {ensemble.quality.get("inliers",ensemble.quality.get("matched",0))} Referenzsterne · Sternfeld '+('kompensiert' if field_ok else 'unsicher: keine Bewegerfreigabe'),detections=detections,current=index+1,total=len(timed),video=str(folder/'ereignisse.mp4'),frame=index,seconds=row['video_seconds'])
                previous=gray
                previous_line_data=line_data
                previous_field_matrix=np.vstack([ensemble.matrix,[0,0,1]]) if field_ok else None
                summary.update(frames=written,hits=hits)
            summary['state']='Gestoppt (Teilvideo)' if (out/'STOP').exists() else 'Fertig'
        finally:
            if writer: writer.release()
            if clean_writer: clean_writer.release()
            frames_file.close(); events_file.close()
            rejected_file.close()
            summary['persistent_trails']=len(trails.records)
            (folder/'spuren.json').write_text(json.dumps(trails.records,ensure_ascii=False,indent=2),encoding='utf-8')
            if written: cv2.imwrite(str(folder/'spuren-gesamt.jpg'),drawn)
            report(out,summaries)
        if written:
            try:
                from candidate_report import extract_candidates
                from observation_settings import load_settings
                settings=load_settings();settings['_stop_file']=str(out/'STOP')
                extract_candidates(out,summary,settings,make_clean=False)
                report(out,summaries)
            except Exception as error:
                event('error',text='Kandidatenbericht konnte nicht vollständig erstellt werden: '+str(error))
        event('artifact',text=f'Video gespeichert: {folder / "ereignisse.mp4"}',video=str(folder/'ereignisse.mp4'),session=name)
    event('done',text='Gestoppt · Teilvideos und Ergebnisse gespeichert.' if (out/'STOP').exists() else 'Ereignisvideos fertig.',report=str(out/'bericht.html'))
