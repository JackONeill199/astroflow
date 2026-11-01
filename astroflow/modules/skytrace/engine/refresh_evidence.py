"""Augment existing reports from original pixels and local satellite archives only."""
import argparse
import json
from pathlib import Path
import numpy as np
from candidate_report import _image
from candidate_evidence import enrich
from catalog_matching import satellite_check,status
from opensky_archive import ArchiveAccessError
from observation_settings import load_settings
from sequence import report,stretch
from json_store import write_json


def refresh(root):
    root=Path(root).resolve();summaries=json.loads((root/'videos.json').read_text(encoding='utf-8'));settings=load_settings()
    # This tool must never follow a user URL or contact a catalogue service.
    settings.update(tle_file='',tle_url='',satcat_offline=True)
    for summary in summaries:
        if not summary.get('candidates_file'):continue
        folder=root/Path(summary['video']).parent;path=root/summary['candidates_file']
        items=json.loads(path.read_text(encoding='utf-8'))
        rows={r['index']:r for r in (json.loads(line) for line in (folder/'frames.jsonl').read_text(encoding='utf-8').splitlines())}
        rejected=folder/'ausgeschlossen.jsonl'
        if rejected.exists():
            for line in rejected.read_text(encoding='utf-8').splitlines():
                row=json.loads(line);rows[row['index']]=row
        first,_=_image(rows[min(rows)]['file']);height,width=stretch(first,summary['display_limits']).shape
        for item in items:
            anchor=rows[item['hit_indices'][0]];detection=None
            target=next((f for f in item['frames'] if f['index']==anchor['index']+1),None)
            measured=target.get('overlay',{}).get('points') if target else None
            for candidate in anchor.get('detections',[]):
                points=candidate.get('segment') or candidate.get('path')
                if measured is not None and points is not None:
                    expected=np.asarray(measured)+item['crop'][:2]
                    if np.shape(expected)==np.shape(points) and np.allclose(expected,points):detection=candidate;break
            if detection is None:
                options=[d for d in anchor.get('detections',[]) if d['kind']==item['kind']]
                if len(options)==1:detection=options[0]
            if detection is None:raise ValueError('Erkennung nicht eindeutig zugeordnet: '+item['id'])
            enrich(item,anchor,detection,rows,_image,width,height)
            try:sat=satellite_check(item,item.get('site'),settings)
            except ArchiveAccessError as error:sat=status('satellites',error.status,str(error))
            item['catalogs']=[sat if r['provider']=='satellites' else r for r in item['catalogs']]
            write_json(folder/'kandidaten'/item['id']/'kandidat.json',item)
            print(item['id'],json.dumps({k:item['evidence'].get(k) for k in ('detector_score','length_pixels','length_arcmin','minimum_angular_speed_arcsec_s')},ensure_ascii=True),flush=True)
        write_json(path,items)
    report(root,summaries)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('root',type=Path);refresh(parser.parse_args().root)
