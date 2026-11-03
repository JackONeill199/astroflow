"""Extract real candidate cutouts + temporal context, preserving clean pixels and metadata."""
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import cv2
import numpy as np
from astropy.io import fits
from astropy.wcs import WCS
from analyse import load_image,event
from astro_motion import EnsembleMotion
from observation_settings import load_settings,resolve_site


@lru_cache(maxsize=6)
def _image(path): return load_image(Path(path))


def _write_json(path,value):
    temp=path.with_suffix('.tmp'); temp.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8'); temp.replace(path)


def _wcs(folder):
    path=folder/'platesolve/ergebnis.json'
    if not path.exists(): return None
    info=json.loads(path.read_text(encoding='utf-8'))
    header=fits.Header.fromfile(folder/'platesolve/loesung.wcs')
    header['NAXIS']=2; header['NAXIS1']=info['width']; header['NAXIS2']=info['height']
    info['wcs']=WCS(header,naxis=2)
    return info


def extract_candidates(out,summary,settings=None,make_clean=True):
    from sequence import stretch,register,MovieWriter
    from catalog_matching import compare
    settings=settings or load_settings()
    def check_stop():
        if settings.get('_cancel_extraction') and settings.get('_stop_file') and Path(settings['_stop_file']).exists():
            raise InterruptedError('Aktualisierung angehalten')
    check_stop()
    folder=out/Path(summary['video']).parent
    frames_path=folder/'frames.jsonl'
    if not frames_path.exists(): return []
    rows=[json.loads(line) for line in frames_path.read_text(encoding='utf-8').splitlines() if line.strip()]
    if not rows: return []
    first_data,first_meta=_image(rows[0]['file'])
    limits=summary.get('display_limits') or (np.percentile(first_data,[1,99.9])-float(np.median(first_data))).tolist()
    reference=stretch(first_data,limits)
    height,width=reference.shape
    summary['display_limits']=limits
    clean=folder/'original.mp4'
    if make_clean and not clean.exists():
        event('status',text=summary['session']+': unbeschriftetes Video für Overlay-Umschaltung erzeugen …')
        building=clean.with_name('original-building.mp4')
        movie=MovieWriter(building,summary.get('fps',8),reference.shape)
        complete=False
        try:
            for row in rows:
                check_stop()
                data,_=_image(row['file']); gray=stretch(data,limits)
                movie.write(cv2.cvtColor(gray,cv2.COLOR_GRAY2BGR))
            complete=True
        finally:
            movie.release()
            if not complete: building.unlink(missing_ok=True)
        building.replace(clean)
    if clean.exists(): summary['clean_video']=str(clean.relative_to(out).as_posix())
    from trail_tracks import link_tracks
    from motion_plausibility import narrow_field
    link_tracks(rows)
    groups={}
    for row in rows:
        for number,detection in enumerate(row.get('detections',[])):
            # Teleskop-Bildfeld: Modellbox ohne gemessene Linie ist kein Kandidat (auch für ältere Läufe beim Aktualisieren)
            if narrow_field(row.get('meta')) and detection.get('confidence') is not None and detection.get('line_snr') is None and detection.get('track_id') is None:
                continue
            key=(detection['kind'],detection['track_id']) if detection.get('track_id') is not None else ('hit',row['index'],number)
            groups.setdefault(key,[]).append((row,detection))
    all_rows={row['index']:row for row in rows}
    rejected=folder/'ausgeschlossen.jsonl'
    if rejected.exists():
        for line in rejected.read_text(encoding='utf-8').splitlines():
            row=json.loads(line); row['_excluded']=True; all_rows[row['index']]=row
    solution=_wcs(folder)
    if solution is None and groups and settings.get('enabled',True) and settings.get('auto_solve',True):
        check_stop()
        from solving import solve_image
        event('status',text=summary['session']+': fehlendes Plate Solving für den Katalogabgleich automatisch nachholen …')
        solution=solve_image(Path(rows[0]['file']),first_data,folder,out,stop_file=settings.get('_stop_file'))
        summary['plate_solving']=dict(status='solved' if solution else 'failed',source_file=rows[0]['file'])
        if solution:summary['plate_solving'].update(ra=solution['ra'],dec=solution['dec'])
        check_stop()
    motion=EnsembleMotion()
    reference_linear=cv2.resize(first_data,(width,height),interpolation=cv2.INTER_AREA)
    initial_points=first_meta.get('_stellar_centroids')
    if initial_points is not None: initial_points=(np.asarray(initial_points).reshape(-1,2)*[width/first_data.shape[1],height/first_data.shape[0]]).tolist()
    motion.update(reference_linear,0,np.eye(2,3),initial_points)
    candidates=[]; full_written=set()
    for index,hits in enumerate(groups.values(),1):
        check_stop()
        cid=f'K{index:04d}'; target=folder/'kandidaten'/cid; target.mkdir(parents=True,exist_ok=True)
        anchor,detection=hits[0]
        data,meta=_image(anchor['file']); gray=stretch(data,limits)
        geometry=detection.get('segment') or detection.get('path')
        if not geometry:
            box=detection['box']; geometry=[[(box[0]+box[2])*width/2,(box[1]+box[3])*height/2]]
        pixels=np.asarray(geometry,dtype=float)
        all_boxes=np.asarray([d['box'] for _,d in hits])*[width,height,width,height]
        x1=max(0,int(all_boxes[:,0].min())-40); y1=max(0,int(all_boxes[:,1].min())-40)
        x2=min(width,int(all_boxes[:,2].max())+41); y2=min(height,int(all_boxes[:,3].max())+41)
        from motion_plausibility import exposure_info
        exposure=exposure_info(meta)
        item=dict(id=cid,session=summary['session'],kind=detection['kind'],label=detection['label'],confidence=detection.get('confidence'),method=detection.get('method'),timestamp=anchor.get('timestamp'),timestamp_text=anchor.get('timestamp_text',''),exposure_seconds=exposure['seconds'],time_reference=exposure.get('reference','start'),source_file=anchor['file'],hit_indices=[row['index'] for row,_ in hits],crop=[x1,y1,x2,y2],frames=[],world_path=None,
                  linked_track=detection.get('linked_track'),blink=detection.get('blink'),single_frame_kind=detection.get('single_frame_kind'),model_scope='narrow_field' if narrow_field(meta) else None)
        matrix=None
        if anchor['index']==rows[0]['index']: matrix=np.eye(2,3)
        elif anchor.get('reference_to_current') is not None: matrix=np.asarray(anchor['reference_to_current'],dtype=float)
        else:
            guess,_=register(reference,gray)
            points=meta.get('_stellar_centroids')
            if points is not None: points=(np.asarray(points).reshape(-1,2)*[width/data.shape[1],height/data.shape[0]]).tolist()
            motion.update(cv2.resize(data,(width,height),interpolation=cv2.INTER_AREA),anchor['index'],guess,points)
            if motion.quality.get('accepted'): matrix=motion.matrix
        if solution and matrix is not None:
            reference_pixels=cv2.transform(pixels.astype('float64')[None],cv2.invertAffineTransform(matrix))[0]
            reference_pixels*=np.array([solution['width']/width,solution['height']/height])
            world=solution['wcs'].all_pix2world(reference_pixels,0)
            if np.isfinite(world).all(): item['world_path']=world.tolist()
        context_path=folder/f'vergleich-{anchor["index"]:06d}.jpg'
        if not cv2.imwrite(str(context_path),gray):raise OSError('Vergleichsbild konnte nicht gespeichert werden')
        item['context_image']=dict(image=context_path.relative_to(out).as_posix(),width=width,height=height,frame=anchor['index']+1,
                                   timestamp_text=anchor.get('timestamp_text',''),points=pixels.tolist(),reference_to_current=matrix.tolist() if matrix is not None else None)
        nearby_indices=sorted({i for row,_ in hits for i in range(row['index']-2,row['index']+3) if i in all_rows})
        hits_by_index={row['index']:d for row,d in hits}
        for frame_index in nearby_indices:
            check_stop()
            row=all_rows[frame_index]
            frame_data,_=_image(row['file']); frame_gray=stretch(frame_data,limits)
            full_path=folder/'vollbilder'/f'bild-{frame_index+1:06d}.jpg'
            if frame_index not in full_written:
                from image_store import write_image
                full_path.parent.mkdir(exist_ok=True)
                write_image(full_path,frame_gray)
                full_written.add(frame_index)
            image=frame_gray[y1:y2,x1:x2]
            image_path=target/f'bild-{frame_index+1:06d}.png'
            if not cv2.imwrite(str(image_path),image): raise OSError('Kandidatenausschnitt konnte nicht gespeichert werden')
            overlay=None
            if frame_index in hits_by_index:
                d=hits_by_index[frame_index]
                shape=d.get('segment') or d.get('path')
                if shape: overlay=dict(points=(np.asarray(shape)-[x1,y1]).tolist())
                else:
                    b=np.asarray(d['box'])*[width,height,width,height]
                    overlay=dict(box=[float(b[0]-x1),float(b[1]-y1),float(b[2]-b[0]),float(b[3]-b[1])])
            item['frames'].append(dict(index=frame_index+1,timestamp_text=row.get('timestamp_text','Zeit unbekannt'),timestamp=row.get('timestamp'),image=image_path.relative_to(out).as_posix(),width=x2-x1,height=y2-y1,overlay=overlay,hit=frame_index in hits_by_index,excluded=bool(row.get('_excluded')),original=Path(row['file']).as_uri()))
            item['frames'][-1]['full_image']=dict(image=full_path.relative_to(out).as_posix(),width=width,height=height)
            if not row.get('_excluded') and isinstance(row.get('video_seconds'),(int,float)):
                item['frames'][-1]['video_seconds']=row['video_seconds']
        item['initial_frame']=next((i for i,f in enumerate(item['frames']) if f['hit']),0)
        site=resolve_site(anchor['file'],summary['session'],settings)
        item['site']=site
        from candidate_evidence import enrich
        enrich(item,anchor,detection,all_rows,_image,width,height)
        settings['_catalog_position']=f'Kandidat {index}/{len(groups)} ({cid})'
        event('status',text=f'{summary["session"]}: {cid} extrahiert ({index}/{len(groups)}) · Katalogabgleich prüfen …')
        item['catalogs']=compare(item,site,settings)
        from satellite_overlay import project_paths
        item['satellite_overlay']=project_paths(solution,(width,height),matrix,item['catalogs'])
        _write_json(target/'kandidat.json',item)
        candidates.append(item)
    _write_json(folder/'kandidaten.json',candidates)
    summary['candidates_file']=(folder/'kandidaten.json').relative_to(out).as_posix()
    summary['candidate_count']=len(candidates)
    return candidates


def update_run(out,stop_file=None):
    out=Path(out).resolve()
    summaries=json.loads((out/'videos.json').read_text(encoding='utf-8'))
    settings=load_settings()
    if stop_file:
        settings['_stop_file']=str(stop_file);settings['_cancel_extraction']=True
    stopped=False
    try:
        for summary in summaries: extract_candidates(out,summary,settings)
    except InterruptedError: stopped=True
    from sequence import report
    report(out,summaries)
    event('done',text='Aktualisierung angehalten. Gespeicherte Berichte bleiben erhalten.' if stopped else 'Kandidaten-Zeitreihen und Katalogstatus im Bericht aktualisiert.',report=str(out/'bericht.html'))


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(); parser.add_argument('path',type=Path);parser.add_argument('--stop-file',type=Path)
    args=parser.parse_args();update_run(args.path,args.stop_file)
