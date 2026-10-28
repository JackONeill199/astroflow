from pathlib import Path
from collections import Counter
import json,sys,requests
from project_paths import BASE
from observation_settings import load_settings
from catalog_matching import asteroid_check,status
from json_store import write_json
from sequence import report
root=BASE/'Ergebnisse/IC405-Sensorrauschen-korrigiert'
summaries=json.loads((root/'videos.json').read_text(encoding='utf-8'));summary=summaries[0];folder=root/Path(summary['video']).parent
items=json.loads((folder/'kandidaten.json').read_text(encoding='utf-8'));settings=load_settings()
for item in items:
 if item['id'] not in ['K0005','K0006','K0007','K0008','K0009']:continue
 try:result=asteroid_check(item,item['site'],settings)
 except requests.Timeout:result=status('asteroids','service_unavailable','JPL-Nachrechnung für dieses Bildfeld hat das Zeitlimit überschritten; kein Ausschluss.')
 except Exception as error:result=status('asteroids','error','JPL-Abfrage: '+type(error).__name__)
 item['catalogs']=[result if r['provider']=='asteroids' else r for r in item['catalogs']]
 write_json(folder/'kandidaten'/item['id']/'kandidat.json',item)
 print(item['id'],result['status'],flush=True)
write_json(folder/'kandidaten.json',items);report(root,summaries)
print('JPL-Ergebnisse aktualisiert.',flush=True)
