"""Add real full-frame comparison images and saved satellite paths, without queries."""
import argparse
import json
from pathlib import Path
import cv2
from candidate_report import _image,_wcs
from sequence import stretch,report
from satellite_overlay import project_paths
from json_store import read_json,write_json


def refresh(root):
    root=Path(root);summaries=read_json(root/'videos.json',[])
    for summary in summaries:
        if not summary.get('candidates_file'):continue
        folder=root/Path(summary['video']).parent
        solution=_wcs(folder)
        rows={r['index']:r for r in (json.loads(line) for line in (folder/'frames.jsonl').read_text(encoding='utf-8').splitlines())}
        path=root/summary['candidates_file'];items=read_json(path,[])
        for item in items:
            row=rows[item['hit_indices'][0]];data,_=_image(row['file']);gray=stretch(data,summary['display_limits'])
            height,width=gray.shape;matrix=row.get('reference_to_current')
            if row['index']==min(rows):matrix=[[1,0,0],[0,1,0]]
            image=folder/f'vergleich-{row["index"]:06d}.jpg'
            if not cv2.imwrite(str(image),gray):raise OSError('Vergleichsbild konnte nicht gespeichert werden')
            item['context_image']=dict(image=image.relative_to(root).as_posix(),width=width,height=height,frame=row['index']+1,
                timestamp_text=row.get('timestamp_text',''),points=item.get('evidence',{}).get('segment',[]),reference_to_current=matrix)
            item['satellite_overlay']=project_paths(solution,(width,height),matrix,item.get('catalogs',[]))
            write_json(folder/'kandidaten'/item['id']/'kandidat.json',item)
        write_json(path,items)
    report(root,summaries)
    print('Echte Vergleichsbilder und Satellitenprojektion ergänzt:',root.name)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('root',type=Path);refresh(parser.parse_args().root)
