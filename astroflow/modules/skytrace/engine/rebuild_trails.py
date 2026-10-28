"""Re-render saved detections with permanent luminous trails, without re-running inference."""
import argparse
import json
from pathlib import Path
import shutil
import numpy as np
from astropy.io import fits
from astropy.wcs import WCS
from analyse import load_image, event
from sequence import PersistentTrails, MovieWriter, annotate, stretch, register, report
from line_detection import detect_lines, merge_streak_detections
from solving import objects_for_frame


def rebuild(source,out,filter_readout=False,redetect_lines=False,validate_motion=False):
    import cv2
    summaries=json.loads((source/'videos.json').read_text(encoding='utf-8'))
    out.mkdir(parents=True,exist_ok=False)
    for summary in summaries:
        old=source/Path(summary['video']).parent
        folder=out/Path(summary['video']).parent
        folder.mkdir(parents=True)
        for name in ('original.mp4','live-clean.jpg'):
            if validate_motion:continue
            if (old/name).exists():
                import os
                try: os.link(old/name,folder/name)
                except OSError: shutil.copy2(old/name,folder/name)
        if (old/'ausgeschlossen.jsonl').exists(): shutil.copy2(old/'ausgeschlossen.jsonl',folder/'ausgeschlossen.jsonl')
        for rejected_preview in old.glob('ausgeschlossen-*.jpg'): shutil.copy2(rejected_preview,folder/rejected_preview.name)
        rows=[json.loads(line) for line in (old/'frames.jsonl').read_text(encoding='utf-8').splitlines()]
        if not rows: continue
        import av
        with av.open(str(source/summary['video'])) as video: fps=float(video.streams.video[0].average_rate)
        trails=PersistentTrails(); previous=None; previous_linear=None; reference=None; limits=summary.get('display_limits'); writer=None; solved=None; catalog=[];hits=0;removed=[]
        clean_writer=None;written=0;previous_reference=None;motion_frames=0;motion_detections=0
        if (old/'platesolve').exists():
            shutil.copytree(old/'platesolve',folder/'platesolve')
            info=json.loads((old/'platesolve/ergebnis.json').read_text(encoding='utf-8'))
            header=fits.Header.fromfile(old/'platesolve/loesung.wcs')
            header['NAXIS']=2; header['NAXIS1']=info['width']; header['NAXIS2']=info['height']
            solved=dict(info,wcs=WCS(header,naxis=2))
        try:
            with (folder/'frames.jsonl').open('w',encoding='utf-8') as frames, (folder/'ereignisse.jsonl').open('w',encoding='utf-8') as events:
                for number,row in enumerate(rows):
                    data,_=load_image(Path(row['file']))
                    if limits is None: limits=(np.percentile(data,[1,99.9])-float(np.median(data))).tolist()
                    gray=stretch(data,limits)
                    if validate_motion:
                        from frame_quality import measure_quality,assess_quality
                        quality=assess_quality(measure_quality(data),summary.get('quality_reference'))
                        row['quality']=quality
                        if quality['excluded']:
                            motion_frames+=1;motion_detections+=len(row['detections'])
                            rejected=dict(index=row['index'],file=row['file'],reason=quality['reason'],quality=quality,detections=row['detections'])
                            removed.append(rejected)
                            with (folder/'ausgeschlossen.jsonl').open('a',encoding='utf-8') as rejected_file:
                                rejected_file.write(json.dumps(dict(row,detections=[],rejected_detections=row['detections']),ensure_ascii=False)+'\n')
                            cv2.imwrite(str(folder/f'bewegung-ausgeschlossen-{row["index"]:06d}.jpg'),gray)
                            continue
                    if reference is None:
                        reference=gray.copy(); writer=MovieWriter(folder/'ereignisse.mp4',fps,gray.shape)
                        if validate_motion:clean_writer=MovieWriter(folder/'original.mp4',fps,gray.shape)
                        if solved: catalog=objects_for_frame(solved,gray.shape)
                    if row.get('previous_to_current') is not None:
                        affine=np.asarray(row['previous_to_current'],dtype='float64')
                    else:
                        affine,_=register(previous,gray) if previous is not None else (None,0)
                    current_reference=np.asarray(row['reference_to_current']) if row.get('reference_to_current') is not None else None
                    if validate_motion and previous is not None:
                        if previous_reference is not None and current_reference is not None:
                            affine=(np.vstack([current_reference,[0,0,1]])@np.linalg.inv(np.vstack([previous_reference,[0,0,1]])))[:2]
                        else:affine,_=register(previous,gray)
                    import cv2
                    linear=cv2.resize(data,(gray.shape[1],gray.shape[0]),interpolation=cv2.INTER_AREA)
                    if validate_motion:
                        from trail_validation import validate_detections
                        row['detections'],rejected_shapes,row['frame_validation']=validate_detections(linear,row['detections'],row['quality'])
                        for rejected in rejected_shapes:removed.append(dict(index=row['index'],file=row['file'],**rejected))
                        motion_detections+=len(rejected_shapes)
                    if redetect_lines:
                        fresh=detect_lines(linear,previous_linear,affine)
                        models=[{k:v for k,v in d.items() if k!='trail_id'} for d in row['detections'] if d.get('line_snr') is None]
                        row['detections']=merge_streak_detections(models,fresh,gray.shape)
                    if filter_readout and any(d.get('line_snr') is not None for d in row['detections']):
                        fresh=detect_lines(linear,previous_linear,affine)
                        kept=[]
                        for detection in row['detections']:
                            if detection.get('line_snr') is None:
                                kept.append(detection);continue
                            segment=np.asarray(detection['segment']);direction=segment[1]-segment[0];direction/=np.linalg.norm(direction)
                            normal=np.array([-direction[1],direction[0]])
                            matches=[]
                            for candidate in fresh:
                                points=np.asarray(candidate['segment']);delta=points[1]-points[0];delta/=np.linalg.norm(delta)
                                a=points@direction;b=segment@direction
                                if abs(delta@direction)>.99 and abs((points.mean(axis=0)-segment.mean(axis=0))@normal)<12 and min(a.max(),b.max())>max(a.min(),b.min()):matches.append(candidate)
                            if matches:
                                match=max(matches,key=lambda c:c['line_snr'])
                                detection.update(segment=match['segment'],box=match['box'],line_snr=match['line_snr'],readout_validated=True)
                                kept.append(detection)
                            else:removed.append(dict(index=row['index'],file=row['file'],reason='Spur nach Sensorbanding-Korrektur nicht bestätigt',detection=detection))
                        row['detections']=kept
                    ref_affine=np.asarray(row['reference_to_current']) if row.get('reference_to_current') is not None else (register(reference,gray)[0] if number else np.eye(2,3))
                    current_catalog=[]
                    if ref_affine is not None:
                        for obj in catalog:
                            x,y=ref_affine@np.array([obj['x'],obj['y'],1])
                            current_catalog.append(dict(obj,x=float(x),y=float(y)))
                    trails.update(gray,row['detections'],affine,row['index'])
                    image=annotate(gray,row['detections'],summary['session'],row['timestamp_text'],row['index'],summary.get('source_frames',len(rows)),affine is not None,fps,current_catalog,trails)
                    writer.write(image)
                    written+=1
                    if clean_writer:
                        clean_writer.write(cv2.cvtColor(gray,cv2.COLOR_GRAY2BGR))
                        cv2.imwrite(str(folder/'live-clean.jpg'),gray)
                    row['video_frame']=written-1;row['video_seconds']=round((written-1)/fps,4)
                    import cv2
                    cv2.imwrite(str(folder/'live.jpg'),image)
                    if row['detections']: cv2.imwrite(str(folder/f"treffer-{row['index']:06d}.jpg"),image)
                    row['persistent_trails']=len(trails.records)
                    row['previous_to_current']=affine.tolist() if affine is not None else None
                    line=json.dumps(row,ensure_ascii=False)+'\n'
                    frames.write(line)
                    if row['detections']: events.write(line)
                    hits+=int(bool(row['detections']))
                    previous=gray;previous_linear=linear;previous_reference=current_reference
                    if number%16==0: event('status',text=f'Dauerspuren rendern: {number+1}/{len(rows)}')
                if written:cv2.imwrite(str(folder/'spuren-gesamt.jpg'),image)
                (folder/'spuren.json').write_text(json.dumps(trails.records,ensure_ascii=False,indent=2),encoding='utf-8')
        finally:
            if writer: writer.release()
            if clean_writer:clean_writer.release()
        summary['persistent_trails']=len(trails.records)
        summary['hits']=hits;summary['fps']=fps;summary['display_limits']=limits
        summary['frames']=written
        if validate_motion:
            summary['excluded']=summary.get('excluded',0)+motion_frames
            summary['validation']=dict(source_run=source.name,removed_frames=motion_frames,removed_detections=motion_detections,
                audit=(folder/'bewegungsartefakte.json').relative_to(out).as_posix(),review='bewegungspruefung.html')
            summary.pop('candidates_file',None);summary['candidate_count']=0
            (folder/'bewegungsartefakte.json').write_text(json.dumps(removed,ensure_ascii=False,indent=2),encoding='utf-8')
            from motion_review import render as render_review
            render_review(out,folder,summary,removed)
            event('status',text=f'Bewegungsprüfung: {motion_frames} verwackelte Bilder und {motion_detections} Erkennungen ausgeschlossen; {len(trails.records)} Kandidaten bleiben.')
        if filter_readout or redetect_lines:
            summary.pop('candidates_file',None);summary['candidate_count']=0
            summary['readout_rejected']=len(removed)
            (folder/'sensorartefakte.json').write_text(json.dumps(removed,ensure_ascii=False,indent=2),encoding='utf-8')
            event('status',text=f'{len(removed)} unbestätigte Sensor-/Rauschspuren entfernt; {len(trails.records)} übrige Kandidaten bleiben unbestätigt.')
    report(out,summaries)
    event('done',text=f'Video mit Dauerspuren gespeichert: {out}')


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('source',type=Path); parser.add_argument('output',type=Path)
    parser.add_argument('--filter-readout',action='store_true')
    parser.add_argument('--redetect-lines',action='store_true',help='Re-measure streaks and merge model fragments into their measured lines')
    parser.add_argument('--validate-motion',action='store_true',help='Recheck star tails, curved proposals and star-supported simultaneous detections')
    args=parser.parse_args(); rebuild(args.source.resolve(),args.output.resolve(),args.filter_readout,args.redetect_lines,args.validate_motion)
