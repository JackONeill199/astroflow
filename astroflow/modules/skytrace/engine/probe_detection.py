import json
from pathlib import Path
import numpy as np
import cv2
from analyse import load_image
from sequence import register,stretch,annotate
from astro_motion import EnsembleMotion
from line_detection import detect_lines

base=Path(__file__).resolve().parent.parent
source=base/'Ergebnisse/M82-Video-Geprueft'
folder=next(p for p in source.iterdir() if p.is_dir())
rows=[json.loads(s) for s in (folder/'frames.jsonl').read_text(encoding='utf-8').splitlines()]
out=base/'Ergebnisse/Detektor-Ensemble'; out.mkdir(exist_ok=True)
motion=EnsembleMotion(); reference=None; limits=None; previous=None; previous_data=None
with (out/'diagnose.jsonl').open('w',encoding='utf-8') as stream:
    for index,row in enumerate(rows):
        data,_=load_image(Path(row['file']))
        if limits is None: limits=(np.percentile(data,[1,99.9])-np.median(data)).tolist()
        gray=stretch(data,limits)
        if reference is None: reference=gray
        affine,_=register(reference,gray) if index else (np.eye(2,3),0)
        movers=motion.update(gray,index,affine)
        if previous is not None:
            adjacent,_=register(previous,gray)
            lines=detect_lines(data,previous_data,adjacent)
        else:
            following,_=load_image(Path(rows[1]['file'])); other=stretch(following,limits)
            adjacent,_=register(other,gray)
            lines=detect_lines(data,following,adjacent)
        result=dict(index=index,file=row['file'],quality=motion.quality,movers=movers,lines=lines)
        stream.write(json.dumps(result)+'\n'); stream.flush()
        if lines:
            cv2.imwrite(str(out/f'spur-{index+1:04d}.jpg'),annotate(gray,lines,'Linienpruefung',row['timestamp_text'],index,len(rows),True,8))
        print(index+1,len(movers),len(lines),motion.quality,flush=True)
        previous=gray; previous_data=data
