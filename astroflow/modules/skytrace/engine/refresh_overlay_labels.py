"""Restore original model hints in existing candidate displays, fully offline."""
import argparse
import json
from pathlib import Path
from json_store import read_json,write_json
from sequence import report


def refresh(root):
    root=Path(root)
    summaries=read_json(root/'videos.json',[])
    for summary in summaries:
        if not summary.get('candidates_file'):continue
        path=root/summary['candidates_file'];folder=root/Path(summary['video']).parent
        items=read_json(path,[])
        rows={r['index']:r for r in (json.loads(line) for line in (folder/'frames.jsonl').read_text(encoding='utf-8').splitlines())}
        for item in items:
            points=item.get('evidence',{}).get('segment')
            detections=[d for index in item.get('hit_indices',[]) for d in rows.get(index,{}).get('detections',[])]
            match=next((d for d in detections if points and (d.get('segment') or d.get('path'))==points),None)
            if match is not None:item['model_hints']=match.get('model_hints',[])
            write_json(folder/'kandidaten'/item['id']/'kandidat.json',item)
        write_json(path,items)
    report(root,summaries)
    print('Modellhinweise und Overlay-Beschriftungen aktualisiert:',root.name)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('root',type=Path);refresh(parser.parse_args().root)
