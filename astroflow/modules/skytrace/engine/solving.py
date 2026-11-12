"""Offline ASTAP WCS solutions and local HNSKY catalogue annotations."""
import csv
import json
import math
from pathlib import Path
import re
import subprocess
import time
import numpy as np
from astropy.io import fits
from astropy.wcs import WCS
from analyse import BASE, event

import os
# ASTAP-Objektkatalog: Windows-Installation, sonst der Ordner des Einrichtungsassistenten (~/astap) bzw. /opt/astap
CATALOG=next((c for c in (Path('C:/Program Files/astap/deep_sky.csv'),Path.home()/'astap'/'deep_sky.csv',Path('/opt/astap/deep_sky.csv')) if c.is_file()),
             Path('C:/Program Files/astap/deep_sky.csv'))
TOOLS=BASE/'tools/astap'
CLI='astap_cli.exe' if os.name=='nt' else 'astap_cli'


def catalog_entries():
    if not CATALOG.is_file(): return []
    rows=[]
    with CATALOG.open(encoding='utf-8-sig',errors='replace') as stream:
        next(stream); next(stream)
        for fields in csv.reader(stream):
            try: rows.append(dict(ra=float(fields[0])/2400,dec=float(fields[1])/3600,name=fields[2].replace('_',' ')))
            except (ValueError,IndexError): pass
    return rows


def hint(path):
    name=str(path).upper()
    match=re.search(r'_OBSERVATION_((?:NGC|IC|M)\s*\d+)',name)
    if not match: return None
    target=match[1].replace(' ','')
    for row in catalog_entries():
        if target in row['name'].upper().split('/'): return row
    return None


def solve_image(path,data,folder,out,stop_file=None):
    """Solve a generated FITS copy. No -update and no writes alongside source files."""
    solver=TOOLS/CLI
    if not solver.exists(): solver=Path('C:/Program Files/astap/astap_cli.exe') if os.name=='nt' else Path.home()/'astap'/CLI
    database=TOOLS/'catalog'
    if not list(database.rglob('*.1476')):
        event('error',text='Plate Solving: lokaler Sternkatalog fehlt. Ereigniserkennung läuft weiter.'); return None
    database=next(database.rglob('*.1476')).parent
    target=folder/'platesolve'; target.mkdir(exist_ok=True)
    image_path=target/'referenz.fits'
    header=fits.Header()
    # The sample Stellina sensor has 2.4um pixels and 400mm focal length.
    if path.suffix.lower() in {'.fits','.fit','.fts'}:
        original=fits.getheader(path)
        height=original.get('NAXIS2',data.shape[0])
        focal=float(original.get('FOCAL',0)); pixel=float(original.get('PIXSZ',0))
        fov=math.degrees(height*pixel/1000/focal) if focal>0 and pixel>0 else 0
    else: fov=0
    fits.writeto(image_path,data.astype('float32'),header,overwrite=True)
    command=[str(solver),'-f',str(image_path),'-o',str(target/'loesung'),'-d',str(database),'-fov',str(fov),'-r','180','-z','0','-wcs','-log']
    center=hint(path)
    if center: command+=['-ra',str(center['ra']/15),'-spd',str(center['dec']+90),'-r','10']
    event('status',text='Lokales Plate Solving: Sternmuster werden mit ASTAP abgeglichen …')
    try:
        with (target/'solver-console.log').open('w') as log:
            proc=subprocess.Popen(command,stdout=log,stderr=subprocess.STDOUT,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            start=time.monotonic()
            while proc.poll() is None:
                if (Path(stop_file) if stop_file else out/'STOP').exists() or time.monotonic()-start>120:
                    proc.terminate(); proc.wait(); event('error',text='Plate Solving gestoppt oder Zeitlimit erreicht.'); return None
                time.sleep(.2)
        wcs_path=target/'loesung.wcs'
        if proc.returncode!=0 or not wcs_path.exists():
            event('error',text=f'ASTAP fand keine Lösung (Code {proc.returncode}); Details im platesolve-Ordner.'); return None
        header=fits.Header.fromfile(wcs_path)
        header['NAXIS']=2
        header['NAXIS1']=data.shape[1]
        header['NAXIS2']=data.shape[0]
        wcs=WCS(header,naxis=2)
        if not wcs.has_celestial: raise ValueError('Keine gültige Himmelskoordinatenlösung')
        center=wcs.pixel_to_world(data.shape[1]/2,data.shape[0]/2)
        solved=dict(wcs=wcs,width=data.shape[1],height=data.shape[0],ra=float(center.ra.deg),dec=float(center.dec.deg))
        (target/'ergebnis.json').write_text(json.dumps({k:v for k,v in solved.items() if k!='wcs'},indent=2),encoding='utf-8')
        event('status',text=f'Plate Solving erfolgreich: RA {solved["ra"]:.5f}°, Dec {solved["dec"]:.5f}°')
        return solved
    except (OSError,ValueError) as error:
        event('error',text=f'Plate Solving: {error}'); return None


def objects_for_frame(solved,shape):
    entries=catalog_entries()
    ra=np.array([r['ra'] for r in entries]); dec=np.array([r['dec'] for r in entries])
    # Restrict the catalogue before projecting, avoiding invalid far-hemisphere pixels.
    delta=np.abs((ra-solved['ra']+180)%360-180)
    mask=(np.abs(dec-solved['dec'])<6)&(delta*np.cos(np.radians(solved['dec']))<6)
    indices=np.where(mask)[0]
    x,y=solved['wcs'].all_world2pix(ra[mask],dec[mask],0)
    height,width=shape
    selected=[]
    for idx,px,py in zip(indices,x,y):
        if np.isfinite(px) and np.isfinite(py) and 0<=px<solved['width'] and 0<=py<solved['height']:
            selected.append(dict(name=entries[idx]['name'],ra=entries[idx]['ra'],dec=entries[idx]['dec'],x=float(px*width/solved['width']),y=float(py*height/solved['height'])))
    return selected[:80]
