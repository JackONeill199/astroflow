"""Compare registration on stretched previews versus linear FITS pixels."""
import json
from pathlib import Path
import os
SAMPLES=os.environ.get('SKYTRACE_SAMPLES','Stellina')  # Stammordner mit echten Stellina-Beobachtungen
import numpy as np
from analyse import load_image
from sequence import stretch,register
from astro_motion import EnsembleMotion

for name in ['2022-05-06_19-55-42_observation_M82','2022-05-06_20-10-46_observation_M66']:
    paths=sorted((Path(SAMPLES)/name/'01-images-initial').glob('*.fits'))
    linear=EnsembleMotion(); reference=None; limits=None; total=0; movers=0
    for i,path in enumerate(paths):
        data,meta=load_image(path)
        if limits is None: limits=(np.percentile(data,[1,99.9])-np.median(data)).tolist()
        gray=stretch(data,limits)
        if reference is None: reference=gray
        guess,_=register(reference,gray) if i else (np.eye(2,3),0)
        results=linear.update(data,i,guess,meta.get('_stellar_centroids'))
        total+=linear.quality['accepted']; movers+=len(results)
        print(name,i+1,linear.quality,flush=True)
    print('TOTAL',name,'FRAMES',len(paths),'ACCEPTED',total,'MOVERS',movers,flush=True)
