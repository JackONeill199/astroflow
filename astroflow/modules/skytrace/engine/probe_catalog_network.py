from pathlib import Path
import sys,json
sys.path.insert(0,str(Path(__file__).parent))
from catalog_matching import retrieve
p={'sb-kind':'a','mpc-code':'568','obs-time':'2021-02-09_00:00:00','mag-required':'true','two-pass':'true','suppress-first-pass':'true','req-elem':'false','vmag-lim':20,'fov-ra-lim':'10-10-00,10-20-00','fov-dec-lim':'10-00-00,10-30-00'}
d=retrieve('https://ssd-api.jpl.nasa.gov/sb_ident.api',p)
print(json.dumps(d)[:8500])
f=retrieve('https://photon.komoot.io/api/',{'q':'Berlin','limit':1,'lang':'de'})
print('Photon:',len(f.get('features',[])),'Ergebnis')
from candidate_report import update_run
update_run(Path(__file__).resolve().parent.parent/'Ergebnisse/M82-Zeitreihe-Katalogbericht')
