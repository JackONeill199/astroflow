"""Read-only comparison of the IC59 burst against established real trail examples."""
import json
from pathlib import Path
import os
SAMPLES=os.environ.get('SKYTRACE_SAMPLES','Stellina')  # Stammordner mit echten Stellina-Beobachtungen
from analyse import load_image
from frame_quality import measure_quality

cases=[('IC59 false burst',Path(SAMPLES+r'\2022-09-22_20-18-16_observation_IC59\01-images-initial\img-0114r.fits')),
       ('IC59 before',Path(SAMPLES+r'\2022-09-22_20-18-16_observation_IC59\01-images-initial\img-0113r.fits')),
       ('NGC884 two real lines',Path(SAMPLES+r'\2022-10-27_18-20-29_observation_NGC884_869\01-images-initial\img-0031r.fits')),
       ('M82 real line',Path(SAMPLES+r'\2022-05-06_19-55-42_observation_M82\01-images-initial\img-0019.fits'))]
for name,path in cases:
    if path.exists():
        data,_=load_image(path);print(name,json.dumps(measure_quality(data)),flush=True)
