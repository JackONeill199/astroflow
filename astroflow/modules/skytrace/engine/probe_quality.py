from pathlib import Path
import os
SAMPLES=os.environ.get('SKYTRACE_SAMPLES','Stellina')  # Stammordner mit echten Stellina-Beobachtungen
from analyse import load_image
from frame_quality import measure_quality,baseline_from_metrics,assess_quality

root=Path(SAMPLES,'2022-08-07_20-32-32_observation_NGC6823/01-images-initial')
results=[]
for i in (1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,20,30,50,100):
    data,_=load_image(root/f'img-{i:04d}r.fits')
    metrics=measure_quality(data); results.append((i,metrics))
baseline=baseline_from_metrics([m for _,m in results])
print('BASELINE',baseline)
for i,m in results: print(i,assess_quality(m,baseline))
