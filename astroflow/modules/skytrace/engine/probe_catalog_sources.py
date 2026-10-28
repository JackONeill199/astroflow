from pathlib import Path
import requests,json
out=Path('AstroAnalyse/catalog_cache');out.mkdir(exist_ok=True)
for name,url in [('satdb-home','https://satdb.ethz.ch/'),('hf-info','https://huggingface.co/api/datasets/juliensimon/space-track-tle-history'),('trino-auth','https://raw.githubusercontent.com/trinodb/trino-python-client/master/trino/auth.py')]:
 try:
  r=requests.get(url,timeout=30);r.raise_for_status();(out/(name+'.txt')).write_text(r.text,encoding='utf-8')
  if name=='hf-info':
   d=r.json();print(name,json.dumps({'files':[s['rfilename'] for s in d.get('siblings',[])],'features':d.get('cardData',{}).get('dataset_info',{})})[:4500])
  elif name=='satdb-home':
   from html.parser import HTMLParser
   import re
   print(name, re.findall(r'(?:href|src)=[\"\']([^\"\']+)',r.text)[-30:])
  else:print(name,'downloaded')
 except Exception as e:print(name,type(e).__name__,str(e)[:100])
url='https://globalmeteornetwork.org/data/traj_summary_data/monthly/traj_summary_monthly_202209.txt'
r=requests.get(url,timeout=30,stream=True);r.raise_for_status()
lines=[]
for line in r.iter_lines(decode_unicode=True):
 lines.append(line)
 if len(lines)>=6:break
r.close();print('GMN header',lines)
