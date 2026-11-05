"""Evidence-based catalogue comparisons at the observation epoch; unavailable != no match."""
import csv
import datetime as dt
import hashlib
import io
import json
from pathlib import Path
import re
import time
import numpy as np
import requests
from astropy import units as u
from astropy.coordinates import SkyCoord
from skyfield.api import EarthSatellite,load,wgs84

from project_paths import BASE, CODE
CACHE=BASE/'catalog_cache'
TS=load.timescale(builtin=True)
SOURCES={'meteors':'https://globalmeteornetwork.org/data/traj_summary_data/monthly/','asteroids':'https://ssd-api.jpl.nasa.gov/doc/sb_ident.html','fireballs':'https://ssd-api.jpl.nasa.gov/doc/fireball.html','satellites':'https://celestrak.org/NORAD/archives/','aircraft':'https://opensky-network.org/data'}

# Satellitenabgleich: Querabstand zur Bahn (TLE-Fehler quer ~1 km ≈ 6′ bei 600 km), Richtung an der Durchgangsstelle,
# Uhrtoleranz um die Belichtung; Rangfolge Abstand + 20″ je Sekunde außerhalb der Belichtung.
SATELLITE_TOLERANCE_ARCSEC=360
SATELLITE_MAX_ANGLE=15
SATELLITE_CLOCK_SLACK_S=20
# Standortgenauigkeit (Ortsliste `accuracy_m`): 1 km Fehler verschiebt eine LEO-Bahn in 500 km Entfernung um ≈ 7′.
# Die Quer-Toleranz wächst auf 2 × Fehlerwinkel, sobald das mehr als die feste Toleranz ist – höchstens bis zum Deckel.
SATELLITE_SITE_TOLERANCE_CAP_ARCSEC=1800
SATELLITE_SITE_UNCERTAIN_KM=1000            # Bezugsentfernung für den Hinweis „Standort ungenau“
SECONDS_WEIGHT_ARCSEC=20

def iso(timestamp): return dt.datetime.fromtimestamp(timestamp,dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')

def _bounded_jpl(url,params,text,settings,deadline,service='JPL-Asteroiden'):
    """Bound catalogue HTTP (JPL by default), including DNS/TLS/body and stop."""
    import subprocess
    import sys
    from analyse import event
    from archive_sources import check_stop
    proc=subprocess.Popen([sys.executable,str(CODE/'catalog_http.py')],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,encoding='utf-8',creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    data=json.dumps(dict(url=url,params=params,text=text));began=time.monotonic();notice=began
    try:
        while True:
            check_stop(settings)
            remaining=deadline-time.monotonic()
            if remaining<=0:raise requests.Timeout(service+'-Gesamtzeitlimit erreicht')
            try:
                output,_=proc.communicate(input=data,timeout=min(.25,remaining))
                break
            except subprocess.TimeoutExpired:data=None
            now=time.monotonic()
            if now-notice>=5:
                limit='höchstens 45 s einschließlich Warteschlange' if service=='JPL-Asteroiden' else 'Gesamtzeitlimit 180 s einschließlich Warteschlange'
                event('status',text=f'{service} · {settings.get("_catalog_position","Katalogabgleich")} · warte auf Antwort: {int(now-began)} s · {limit} · Anhalten möglich')
                notice=now
        if proc.returncode:raise requests.RequestException(service+'-Abfrageprozess fehlgeschlagen')
        result=json.loads(output)
        if not result.get('ok'):
            if result.get('kind')=='timeout':raise requests.Timeout(service+'-Antwortzeit überschritten')
            if result.get('kind')=='format':raise ValueError('Ungültige '+service+'-Antwort')
            raise requests.RequestException(service+'-HTTP-Abfrage fehlgeschlagen')
        return result['payload']
    finally:
        if proc.poll() is None:proc.kill()
        proc.communicate()


def retrieve(url,params=None,text=False,cached_only=False,settings=None):
    from filelock import FileLock,Timeout as LockTimeout
    from json_store import read_json,write_json,open_read,replace_file
    import tempfile
    CACHE.mkdir(exist_ok=True)
    key=hashlib.sha256(json.dumps([url,params],sort_keys=True).encode()).hexdigest()
    path=CACHE/(key+('.txt' if text else '.json'))
    def cached():
        if not path.exists():return None
        if not text:return read_json(path)
        with open_read(path) as stream:return stream.read().decode('utf-8')
    saved=cached()
    if saved is not None:return saved
    if cached_only:
        from opensky_archive import ArchiveAccessError
        raise ArchiveAccessError('needs_archive','Diese Quelle unterstützt keinen Standortfilter. Nur vorhandene lokale Daten werden genutzt; kein weltweiter Abruf.')
    jpl=url.startswith('https://ssd-api.jpl.nasa.gov/')
    lock=CACHE/('jpl-service.lock' if jpl else key+'.lock')
    guard=FileLock(str(lock),timeout=240)
    settings=settings or {};deadline=time.monotonic()+45
    if jpl:
        from archive_sources import check_stop
        from analyse import event
        notice=time.monotonic()
        while True:
            check_stop(settings)
            if time.monotonic()>=deadline:raise requests.Timeout('JPL-Warteschlange hat das Zeitlimit überschritten')
            try:guard.acquire(timeout=.25);break
            except LockTimeout:
                if time.monotonic()-notice>=5:
                    event('status',text='JPL-Asteroiden · '+settings.get('_catalog_position','Katalogabgleich')+' · warte auf andere JPL-Abfrage · Gesamtzeitlimit 45 s · Anhalten möglich')
                    notice=time.monotonic()
    else:guard.acquire()
    try:
        saved=cached()
        if saved is not None:return saved
        if jpl:payload=_bounded_jpl(url,params,text,settings,deadline)
        else:
            response=requests.get(url,params=params,headers={'User-Agent':'Stellina-AstroAnalyse/1.0 (local astronomy analysis)'},timeout=(10,50))
            response.raise_for_status()
            payload=response.text if text else response.json()
        if text:
            with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',dir=CACHE,suffix='.tmp',delete=False) as output:
                temporary=Path(output.name);output.write(payload)
            try:replace_file(temporary,path)
            finally:temporary.unlink(missing_ok=True)
        else:write_json(path,payload)
        return payload
    finally:guard.release()


def status(provider,state,detail,matches=None,**extra):
    result=dict(provider=provider,status=state,detail=detail,source=SOURCES[provider],matches=matches or [])
    result.update(extra)
    return result

def _times(candidate):
    stamp=candidate.get('timestamp')
    if stamp is None or 'unbekannt' in candidate.get('timestamp_text',''): raise ValueError('Keine gesicherte UTC-Aufnahmezeit')
    from motion_plausibility import positive,exposure_start
    seconds=positive(candidate.get('exposure_seconds'))
    if seconds is None: raise ValueError('Keine gesicherte Belichtungszeit')
    return exposure_start(candidate),seconds

def _world_polyline(candidate):
    return np.asarray(candidate['world_path'],dtype=float)

def angular_match(candidate,ra,dec,tolerance_arcsec,max_angle=30,times=None):
    """Tangent-plane separation and coverage of the observed trail by a predicted path.
    Mit `times` (je Bahnpunkt) zusätzlich `passage` = Zeitpunkte, zu denen die Bahn Anfang und Ende der Spur passiert."""
    observed=_world_polyline(candidate)
    origin=SkyCoord(float(observed[0,0])*u.deg,float(observed[0,1])*u.deg)
    obs=SkyCoord(observed[:,0]*u.deg,observed[:,1]*u.deg)
    pred=SkyCoord(np.asarray(ra)*u.deg,np.asarray(dec)*u.deg)
    nearby=origin.separation(pred).deg<10
    if not np.any(nearby): return dict(separation_arcsec=None,coverage=0.,compatible=False,alignment_degrees=None,reasons=['Vorhergesagte Spur liegt mehr als 10° vom Spuranfang entfernt'])
    pred=pred[nearby]
    if times is not None: times=np.asarray(times,dtype=float)[nearby]
    ox,oy=origin.spherical_offsets_to(obs); px,py=origin.spherical_offsets_to(pred)
    a=np.column_stack([ox.arcsec,oy.arcsec]); b=np.column_stack([px.arcsec,py.arcsec])
    def distances(points,line):
        if len(line)==1: return np.linalg.norm(points-line[0],axis=1)
        result=np.full(len(points),np.inf)
        for start,end in zip(line[:-1],line[1:]):
            v=end-start; t=np.clip(((points-start)@v)/max(v@v,1e-12),0,1)
            result=np.minimum(result,np.linalg.norm(points-(start+t[:,None]*v),axis=1))
        return result
    if len(a)==2: a=np.linspace(a[0],a[1],21)
    nearest=float(distances(b,a).min())
    offsets=distances(a,b)
    coverage=float(np.mean(offsets<=tolerance_arcsec))
    aligned=True;angle=None
    if len(a)>1 and len(b)>1 and np.linalg.norm(a[-1]-a[0])>2*tolerance_arcsec:
        v1=a[-1]-a[0]
        if times is not None and len(b)>2:
            # Richtung der Bahn dort, wo sie die Spur passiert (die Bahn über das ganze Zeitfenster ist gekrümmt).
            k=int(np.argmin(np.linalg.norm(b-a[len(a)//2],axis=1)));k=min(max(k,1),len(b)-2);v2=b[k+1]-b[k-1]
        else: v2=b[-1]-b[0]
        cosine=abs(v1@v2)/max(np.linalg.norm(v1)*np.linalg.norm(v2),1e-12)
        aligned=bool(cosine>np.cos(np.radians(max_angle)));angle=round(float(np.degrees(np.arccos(np.clip(cosine,0,1)))),2)
    reasons=[]
    if nearest>tolerance_arcsec:reasons.append('Winkelabstand überschreitet die Toleranz')
    if coverage<.5:reasons.append('Weniger als 50 % der gemessenen Spur werden abgedeckt')
    if not aligned:reasons.append(f'Spurausrichtung weicht um mehr als {max_angle:g}° ab')
    result=dict(separation_arcsec=round(nearest,2),mean_offset_arcsec=round(float(np.mean(np.minimum(offsets,1e6))),2),coverage=round(coverage,3),compatible=bool(nearest<=tolerance_arcsec and coverage>=.5 and aligned),alignment_degrees=angle,reasons=reasons)
    if times is not None and len(b)>1:
        def passage(point):
            # Zeitpunkt des nächstgelegenen Bahnpunkts, linear zwischen den Stützstellen.
            v=b[1:]-b[:-1];t=np.clip(((point-b[:-1])*v).sum(1)/np.maximum((v*v).sum(1),1e-12),0,1)
            k=int(np.argmin(np.linalg.norm(point-(b[:-1]+t[:,None]*v),axis=1)))
            return float(times[k]+t[k]*(times[k+1]-times[k]))
        result['passage']=sorted([passage(a[0]),passage(a[-1])])
    return result

def asteroid_check(candidate,site,settings):
    if not site: return status('asteroids','not_checkable','Beobachtungsstandort fehlt; keine topozentrische Ephemeride abgefragt.')
    if not candidate.get('world_path'): return status('asteroids','not_checkable','Keine belastbare WCS-Zuordnung für diesen Treffer.')
    start,duration=_times(candidate); mid=start+duration/2
    world=np.asarray(candidate['world_path']); center=SkyCoord(world[:,0]*u.deg,world[:,1]*u.deg)
    ra=float(center[0].ra.deg); dec=float(np.mean(world[:,1]))
    ra_width=max(.03,float(np.max(np.abs((world[:,0]-ra+180)%360-180)))+.02)
    dec_width=max(.03,float(np.max(np.abs(world[:,1]-dec)))+.02)
    ra_text=SkyCoord(ra*u.deg,dec*u.deg).ra.to_string(unit=u.hourangle,sep='-',precision=3,pad=True)
    dec_text=SkyCoord(ra*u.deg,dec*u.deg).dec.to_string(unit=u.deg,sep='-',precision=2,pad=True,alwayssign=True).replace('-','M',1) if dec<0 else SkyCoord(ra*u.deg,dec*u.deg).dec.to_string(unit=u.deg,sep='-',precision=2,pad=True)
    params={'lat':site['lat'],'lon':site['lon'],'alt':site.get('alt_m',0)/1000,'obs-time':iso(mid).replace('T','_'),'fov-ra-center':ra_text,'fov-dec-center':dec_text,'fov-ra-hwidth':ra_width,'fov-dec-hwidth':dec_width,'two-pass':'true','suppress-first-pass':'true','mag-required':'true','vmag-lim':settings.get('asteroid_mag_limit',20),'sb-kind':'a'}
    data=retrieve('https://ssd-api.jpl.nasa.gov/sb_ident.api',params,settings=settings)
    if data.get('signature',{}).get('version')!='1.1': raise ValueError('Unbekannte JPL-SB-Ident-Antwortversion')
    if 'error' in data: raise ValueError(str(data['error']))
    fields=data.get('fields_second',[])
    matches=[]
    for values in data.get('data_second_pass',[]):
        row=dict(zip(fields,values))
        declination=str(row['Astrometric Dec (dd mm\'ss")']).replace("'",' ').replace('"',' ')
        coord=SkyCoord(str(row['Astrometric RA (hh:mm:ss)']),declination,unit=(u.hourangle,u.deg))
        geometry=angular_match(candidate,[coord.ra.deg],[coord.dec.deg],30)
        if geometry['compatible']:
            matches.append(dict(name=row['Object name'],ra_deg=float(coord.ra.deg),dec_deg=float(coord.dec.deg),magnitude=row.get('Visual magnitude (V)'),epoch=iso(mid),evidence='Positionsvereinbarkeit innerhalb 30 Bogensekunden; noch keine bestätigte Identität',**geometry))
    return status('asteroids','compatible' if matches else 'checked_no_match','Topozentrische JPL-Ephemeriden am Belichtungsmittelpunkt, Grenzgröße '+str(settings.get('asteroid_mag_limit',20))+'. Positionsabgleich allein bestätigt keinen Asteroiden.',matches,objects_in_field=int(data.get('n_second_pass',0)),query=params)

def _observer(site): return wgs84.latlon(site['lat'],site['lon'],elevation_m=site.get('alt_m',0))

def fireball_check(candidate,site,settings):
    if not site:return status('fireballs','not_checkable','Standort fehlt; ohne Standort wird kein Feuerkugelarchiv geladen.')
    start,duration=_times(candidate)
    data=retrieve('https://ssd-api.jpl.nasa.gov/fireball.api',{'date-min':iso(start-60),'date-max':iso(start+duration+60),'req-loc':'true','req-alt':'true'},cached_only=True)
    if data.get('signature',{}).get('version')!='1.2': raise ValueError('Unbekannte CNEOS-Antwortversion')
    matches=[]; hints=[]
    for values in data.get('data',[]):
        row=dict(zip(data['fields'],values))
        stamp=dt.datetime.fromisoformat(row['date']).replace(tzinfo=dt.timezone.utc).timestamp()
        item=dict(name='Gemeldete Feuerkugel '+row['date'],epoch=row['date'],evidence='Zeitlicher Hinweis; keine Identifizierung eines Meteoritenfalls')
        from visibility import above_horizon
        lat=float(row['lat'])*(-1 if row['lat-dir']=='S' else 1);lon=float(row['lon'])*(-1 if row['lon-dir']=='W' else 1)
        if not above_horizon(lat,lon,float(row['alt'])*1000,site):continue
        hints.append(item)
        if site and candidate.get('world_path'):
            lat=float(row['lat'])*(-1 if row['lat-dir']=='S' else 1); lon=float(row['lon'])*(-1 if row['lon-dir']=='W' else 1)
            at=TS.from_datetime(dt.datetime.fromtimestamp(stamp,dt.timezone.utc))
            target=wgs84.latlon(lat,lon,elevation_m=float(row['alt'])*1000)
            vector=(target-_observer(site)).at(at)
            altitude,_,_=vector.altaz()
            if altitude.degrees<=0: continue
            ra,dec,_=vector.radec(); geometry=angular_match(candidate,[ra.hours*15],[dec.degrees],7200)
            if geometry['compatible'] and start-5<=stamp<=start+duration+5:
                matches.append(dict(item,**geometry,evidence='Zeit und grobe Himmelsposition vereinbar; CNEOS-Orts-/Zeitunsicherheit beachten'))
    return status('fireballs','compatible' if matches else ('temporal_hint' if hints else 'checked_no_match'),'CNEOS enthält gemeldete helle Feuerkugeln, keinen vollständigen Katalog gewöhnlicher Meteore oder Meteoritenfälle.',matches,temporal_hints=hints)

def _file_or_url(settings,prefix):
    filename=settings.get(prefix+'_file','')
    if filename:
        return Path(filename).read_text(encoding='utf-8-sig'),str(Path(filename).name)
    url=settings.get(prefix+'_url','')
    if url:
        if not url.startswith('https://'): raise ValueError('Katalog-Download benötigt eine HTTPS-Adresse')
        return retrieve(url,text=True),url.split('?')[0]
    return None,None

def site_tolerance(accuracy_m,distance_km,cap=None):
    """Quer-Toleranz in Bogensekunden für eine Bahn in `distance_km` bei Standortfehler `accuracy_m` (Radius, ~1σ).
    Bei genauem Standort (2 × Fehlerwinkel ≤ 360″) bleibt es bei der festen Toleranz. `cap` nur für die Standortsuche."""
    if not accuracy_m or not distance_km or distance_km<=0: return float(SATELLITE_TOLERANCE_ARCSEC)
    shift=2*float(accuracy_m)/(float(distance_km)*1000)*206264.806
    return float(min(cap or SATELLITE_SITE_TOLERANCE_CAP_ARCSEC,max(SATELLITE_TOLERANCE_ARCSEC,shift)))

def site_uncertain(accuracy_m):
    """Ist der Standort so ungenau, dass selbst eine Bahn in 1000 km die feste Toleranz sprengt?"""
    return bool(accuracy_m) and site_tolerance(accuracy_m,SATELLITE_SITE_UNCERTAIN_KM)>SATELLITE_TOLERANCE_ARCSEC

def satellite_check(candidate,site,settings):
    if not site: return status('satellites','not_checkable','Standort fehlt. Plate Solving allein reicht für erdnahe Satelliten nicht.')
    start,duration=_times(candidate)
    text,source=_file_or_url(settings,'tle')
    satellites={};stale=0;archive_total=0
    if not text and settings.get('auto_satellites',False):
        from satellite_archive import historical_models
        models,source,archive_total=historical_models(start+duration/2,settings)
        satellites={sat.model.satnum:(sat,age) for sat,age in models}
    if not text and not satellites: return status('satellites','needs_archive',f'Keine zeitnahen Bahndaten für {iso(start)} UTC verfügbar. Fehlende Archivabdeckung schließt Satelliten nicht aus.',archive=source,archive_records=archive_total)
    rows=[line.rstrip() for line in (text or '').splitlines() if line.strip()]
    for index,line in enumerate(rows[:-1]):
        if not line.startswith('1 ') or not rows[index+1].startswith('2 '): continue
        name=rows[index-1].removeprefix('0 ').strip() if index and not rows[index-1].startswith(('1 ','2 ')) else line[2:7].strip()
        sat=EarthSatellite(line,rows[index+1],name,TS)
        age=abs(float(TS.from_datetime(dt.datetime.fromtimestamp(start+duration/2,dt.timezone.utc))-sat.epoch))
        if age>float(settings.get('max_tle_age_days',3)): stale+=1; continue
        key=sat.model.satnum
        if key not in satellites or age<satellites[key][1]: satellites[key]=(sat,age)
    if not satellites: return status('satellites','not_checkable','Keine Bahnelemente nahe genug an der Aufnahmezeit (max. '+str(settings.get('max_tle_age_days',3))+' Tage).',archive=source,stale_records=stale)
    # Zeitfenster = Belichtung ± Uhrtoleranz: Kameras ohne GPS (Stellina stellt die Uhr übers Handy) und Bahnelemente
    # mit Fehlern entlang der Bahn verschieben den Durchgang um Sekunden – bei LEO-Satelliten (~0,5°/s) sind das Grad.
    # Vorher wurde nur exakt die Belichtung propagiert, die Bahn lag dann neben der Spur (quer < 6′, längs 1–6°).
    slack=float(settings.get('satellite_clock_slack_s',SATELLITE_CLOCK_SLACK_S))
    first,last=start-slack,start+duration+slack
    stamps=np.linspace(first,last,max(21,int((last-first)*5)+1))
    times=TS.from_datetimes([dt.datetime.fromtimestamp(float(s),dt.timezone.utc) for s in stamps])
    observer=_observer(site); matches=[];diagnostics=[]
    accuracy=float(site.get('accuracy_m') or 0)
    cap=settings.get('site_tolerance_cap_arcsec')          # Standortsuche (rematch_sites.py): weiter als im normalen Abgleich
    from satellite_archive import sky_prefilter
    possible=sky_prefilter(list(satellites.values()),first,last-first,site,candidate)
    for sat,age in possible:
        from archive_sources import check_stop
        check_stop(settings)
        vector=(sat-observer).at(times); altitude,_,_=vector.altaz()
        if np.max(altitude.degrees)<=0: continue
        ra,dec,_=vector.radec()
        valid=np.isfinite(ra.hours)&np.isfinite(dec.degrees)&(altitude.degrees>0)
        if not valid.any(): continue
        distance_km=float(np.median(vector.distance().km[valid]))
        tolerance=site_tolerance(accuracy,distance_km,cap)
        geometry=angular_match(candidate,ra.hours[valid]*15,dec.degrees[valid],tolerance,max_angle=SATELLITE_MAX_ANGLE,times=stamps[valid])
        geometry['distance_km']=round(distance_km,1)
        if geometry['compatible'] and (geometry.get('separation_arcsec') or 0)>SATELLITE_TOLERANCE_ARCSEC:
            geometry['site_widened']=True               # nur wegen der Standortunsicherheit noch vereinbar
        passage=geometry.pop('passage',None)
        if passage:
            # Sekunden außerhalb der nominellen Belichtung (0 = Durchgang während der Belichtung).
            geometry['passage_seconds']=[round(p-start,2) for p in passage]
            geometry['time_offset_s']=round(max(start-passage[1],passage[0]-(start+duration),0.),2)
            if geometry['compatible'] and passage[1]-passage[0]>duration*1.2+1:
                geometry['compatible']=False;geometry['reasons'].append('Satellit bräuchte für die Spur länger als die Belichtung')
        diagnostics.append(dict(name=sat.name or 'NORAD '+str(sat.model.satnum),norad_id=int(sat.model.satnum),tle_age_days=round(age,3),world_path=np.column_stack([ra.hours[valid]*15,dec.degrees[valid]]).round(8).tolist(),tolerance_arcsec=round(tolerance,1),**geometry))
        if geometry['compatible']:
            # Bahnelemente mitspeichern: 3D-Ansicht (orbit3d.py) rechnet die Bahn später ohne Archiv nach.
            from sgp4.exporter import export_tle
            evidence='Historische SGP4-Bahn passiert die Spur in Lage, Richtung und Zeit; Sichtbarkeit nicht bestätigt'
            if geometry.get('site_widened'): evidence+=f'; nur mit der Standortunsicherheit (± {accuracy/1000:.1f} km) vereinbar'
            matches.append(dict(name=sat.name,norad_id=int(sat.model.satnum),tle_age_days=round(age,3),tle=list(export_tle(sat.model)),tolerance_arcsec=round(tolerance,1),evidence=evidence,**geometry))
    rank=lambda r:float('inf') if r.get('separation_arcsec') is None else r.get('mean_offset_arcsec',r['separation_arcsec'])+SECONDS_WEIGHT_ARCSEC*r.get('time_offset_s',0)
    diagnostics.sort(key=lambda r:(not r['compatible'],rank(r)));matches.sort(key=rank)
    # Namen aus der lokalen SATCAT-Kopie; Treffer zuerst (nur für sie reichen die wenigen Einzelabfragen bei neuen Nummern).
    names_note=_name_satellites(matches+diagnostics[:8],settings)
    detail=f'Bahndaten zur Aufnahmezeit {iso(start)} UTC (Belichtung ±{slack:g} s Uhrtoleranz): {len(possible)} mögliche Spuren nach Standort-, Zeit- und Bildfeldfilter genau geprüft. Archivabdeckung kann unvollständig sein (militärische Satelliten fehlen); begrenzte Bahngenauigkeit. Ein geometrischer Treffer ist kein Beweis.'
    if names_note: detail+=' '+names_note
    state='compatible' if matches else 'checked_no_match'
    site_note=None
    if accuracy and site_tolerance(accuracy,SATELLITE_SITE_UNCERTAIN_KM)>SATELLITE_TOLERANCE_ARCSEC:
        site_note=f'Standort nur auf ± {accuracy/1000:.1f} km bekannt ({site.get("name") or "Standort"}): Toleranz quer je Bahn auf bis zu {SATELLITE_SITE_TOLERANCE_CAP_ARCSEC/60:.0f}′ erweitert.'
        detail+=' '+site_note
        if not matches and site_uncertain(accuracy):
            state='site_uncertain'; detail='Standort ungenau – Zuordnung unsicher. '+detail
    return status('satellites',state,detail,matches[:20],archive=source,source=source or SOURCES['satellites'],stale_records=stale,archive_records=archive_total,checked_trajectories=len(diagnostics),diagnostics=diagnostics[:8],tolerance_arcsec=SATELLITE_TOLERANCE_ARCSEC,clock_slack_s=slack,exposure_start=start,names_note=names_note,site_accuracy_m=accuracy or None,site_note=site_note)


def _name_satellites(rows,settings):
    """Archivbahnen tragen nur NORAD-Nummern; Namen und Objektart aus dem SATCAT nachtragen (Fehler → Nummer bleibt).
    Ergebnis: None oder Hinweis, warum Namen fehlen (wird im Katalogtext angezeigt)."""
    ids=list(dict.fromkeys(r['norad_id'] for r in rows if not r.get('name') or str(r['name']).startswith('NORAD ') or str(r['name'])==str(r['norad_id'])))
    if not ids: return None
    try:
        from archive_sources import satellite_names
        names,note=satellite_names(ids,settings)
    except Exception as error: return f'Namen nicht abrufbar: {error.__class__.__name__}: {error}'
    for r in rows:
        info=names.get(str(r['norad_id']))
        if info and info.get('name'): r['name']=info['name'];r['object_type']=info.get('type')
    return note if any(str(i) not in names for i in ids) else None

def _stamp(value):
    try: return float(value)
    except ValueError:
        parsed=dt.datetime.fromisoformat(value.replace('Z','+00:00'))
        if parsed.tzinfo is None: parsed=parsed.replace(tzinfo=dt.timezone.utc)
        return parsed.timestamp()

def aircraft_check(candidate,site,settings):
    if not site: return status('aircraft','not_checkable','Beobachtungsstandort fehlt.')
    start,duration=_times(candidate)
    text,source=_file_or_url(settings,'flight')
    if not text and settings.get('auto_aircraft',False):
        from opensky_archive import fetch_flights
        text,source=fetch_flights(start,duration,site,settings)
    if not text: return status('aircraft','needs_archive',f'Für {iso(start)} UTC ist ein historischer ADS-B-Positionsdatenexport nötig. CSV-Datei oder HTTPS-Download-URL hinterlegen; Live-Flugdaten eignen sich dafür nicht.')
    tracks={}
    for row in csv.DictReader(io.StringIO(text)):
        if not {'timestamp','latitude','longitude','geo_altitude','icao24'}.issubset(row): raise ValueError('ADS-B CSV benötigt timestamp,latitude,longitude,geo_altitude,icao24; optional callsign. Höhe geometrisch in Metern.')
        stamp=_stamp(row['timestamp'])
        if start-20<=stamp<=start+duration+20:
            tracks.setdefault(row['icao24'],[]).append((stamp,float(row['latitude']),float(row['longitude']),float(row['geo_altitude']),row.get('callsign','').strip()))
    observer=_observer(site); matches=[]; checked=0
    for icao,rows in tracks.items():
        rows.sort()
        stamps=np.asarray([r[0] for r in rows])
        if len(rows)<2 or stamps[0]>start or stamps[-1]<start+duration or np.diff(stamps).max()>15: continue
        values=np.asarray([r[1:4] for r in rows]); samples=np.linspace(start,start+duration,21)
        lat=np.interp(samples,stamps,values[:,0]); lon=np.degrees(np.interp(samples,stamps,np.unwrap(np.radians(values[:,1])))); alt=np.interp(samples,stamps,values[:,2])
        times=TS.from_datetimes([dt.datetime.fromtimestamp(float(s),dt.timezone.utc) for s in samples])
        target=wgs84.latlon(lat,lon,elevation_m=alt)
        vector=(target-observer).at(times); altitude,_,_=vector.altaz()
        if np.max(altitude.degrees)<=0: continue
        ra,dec,_=vector.radec(); checked+=1
        geometry=angular_match(candidate,ra.hours*15,dec.degrees,360)
        if geometry['compatible']: matches.append(dict(name=rows[0][4] or icao,icao24=icao,track=[[r[0],round(r[1],5),round(r[2],5),round(r[3],1)] for r in rows],evidence='Archivierte geometrische ADS-B-Positionen: Zeit und Spur vereinbar; keine eindeutige Identifikation',**geometry))
    return status('aircraft','compatible' if matches else ('checked_no_match' if checked else 'not_checkable'),f'{checked} ausreichend dichte historische Flugspuren im Belichtungsfenster geprüft. Fehlende Abdeckung schließt ein Flugzeug nicht aus.',matches,archive=source,source=source if source and source.startswith('https://') else SOURCES['aircraft'])


def meteor_check(candidate,site,settings):
    from archive_sources import gmn_events,GMN
    start,duration=_times(candidate)
    if not site:return status('meteors','not_checkable','Standort fehlt; ohne Standort wird kein Meteorarchiv geladen.')
    records,total=gmn_events(start-2,start+duration+2,settings,site=site)
    matches=[];hints=[]
    for row in records:
        hint=dict(name='GMN '+row['id'],epoch=iso(row['timestamp']),shower=row['shower'],evidence='Vermessene Meteorflugbahn im Belichtungszeitfenster')
        hints.append(hint)
        if not site or not candidate.get('world_path'):continue
        samples=np.linspace(0,1,31);times=TS.from_datetimes([dt.datetime.fromtimestamp(row['timestamp']+float(s)*row['Duration'],dt.timezone.utc) for s in samples])
        # Interpolate geocentric Cartesian endpoints, avoiding longitude-wrap errors.
        begin=wgs84.latlon(row['LatBeg'],row['LonBeg'],elevation_m=row['HtBeg']*1000)
        end=wgs84.latlon(row['LatEnd'],row['LonEnd'],elevation_m=row['HtEnd']*1000)
        a=(begin-_observer(site)).at(times);b=(end-_observer(site)).at(times)
        vectors=a.position.km*(1-samples)+b.position.km*samples
        # Filter below-horizon trajectories using the local vertical.
        observer=_observer(site).at(times)
        high=wgs84.latlon(site['lat'],site['lon'],elevation_m=site.get('alt_m',0)+1000).at(times)
        up=high.position.km-observer.position.km
        visible=np.sum(vectors*up,axis=0)>0
        if not visible.any():continue
        vectors=vectors[:,visible];ra=np.degrees(np.arctan2(vectors[1],vectors[0]))%360;dec=np.degrees(np.arctan2(vectors[2],np.hypot(vectors[0],vectors[1])))
        geometry=angular_match(candidate,ra,dec,180)
        trajectory=dict(begin=[row['LatBeg'],row['LonBeg'],row['HtBeg']],end=[row['LatEnd'],row['LonEnd'],row['HtEnd']],start=row['timestamp'],duration=row['Duration'])
        if geometry['compatible']:matches.append(dict(hint,**geometry,trajectory=trajectory,evidence='GMN-Flugbahn: Zeit und topozentrische Spur innerhalb 3 Bogenminuten vereinbar; Identität nicht bestätigt'))
    only_time=bool(hints) and (not site or not candidate.get('world_path'))
    return status('meteors','compatible' if matches else ('temporal_hint' if only_time else 'checked_no_match'),f'{len(records)} GMN-Flugbahnen im Zeitfenster über dem geometrischen Standorthorizont aus lokalem Bestand ausgewählt. Kein weltweiter Archivdownload. GMN ist kein vollständiger Meteorkatalog.',matches,source=GMN,temporal_hints=hints if only_time else [])

def compare(candidate,site,settings):
    results=[]
    providers=[('asteroids',asteroid_check),('satellites',satellite_check),('aircraft',aircraft_check)]
    if settings.get('auto_meteors',False):providers.append(('meteors',meteor_check))
    providers.append(('fireballs',fireball_check))
    from opensky_archive import ArchiveAccessError
    for provider,function in providers:
        request_key=provider+':'+hashlib.sha256(json.dumps([candidate.get('timestamp'),candidate.get('world_path')],sort_keys=True).encode()).hexdigest()
        if settings.get('_stop_file') and Path(settings['_stop_file']).exists():
            results.append(status(provider,'cancelled','Abgleich durch Benutzer gestoppt.')); continue
        if not settings.get('enabled',True): results.append(status(provider,'disabled','Katalogabgleich ist ausgeschaltet.')); continue
        from motion_plausibility import positive
        if positive(candidate.get('exposure_seconds')) is None:
            results.append(status(provider,'not_checkable','Keine eindeutige positive Belichtungszeit aus den Headern; keine erfundene Zeitspanne für den Abgleich.'));continue
        if provider in settings.get('_unavailable',{}):
            results.append(status(provider,'service_unavailable','Der Dienst hat in diesem Lauf nicht rechtzeitig geantwortet. Weitere gleichartige Abfragen werden bis zur nächsten Aktualisierung ausgelassen.'));continue
        if request_key in settings.get('_timed_out_requests',{}):
            results.append(status(provider,'service_unavailable','Diese identische Katalogabfrage hat das Zeitlimit überschritten. Andere Bildfelder werden weiter geprüft.'));continue
        try:
            from analyse import event
            label='JPL-Asteroiden' if provider=='asteroids' else provider
            event('status',text=f'Katalogprüfung: {label} · {settings.get("_catalog_position",candidate.get("id",""))} …')
            if provider not in ('fireballs','meteors') and not candidate.get('world_path'):
                results.append(status(provider,'not_checkable','Belastbare Bild-Himmelskoordinaten fehlen.')); continue
            results.append(function(candidate,site,settings))
        except ArchiveAccessError as error:results.append(status(provider,error.status,str(error)))
        except InterruptedError:results.append(status(provider,'cancelled','Abgleich angehalten.'))
        except requests.Timeout:
            settings.setdefault('_timed_out_requests',{})[request_key]=True
            results.append(status(provider,'service_unavailable','Diese Katalogabfrage hat das Zeitlimit überschritten. Andere Bildfelder werden weiter geprüft; kein Ausschluss dieses Objekttyps.'))
        except requests.ConnectionError:
            settings.setdefault('_unavailable',{})[provider]=True
            results.append(status(provider,'service_unavailable','Der Katalogdienst ist vorübergehend nicht erreichbar oder hat das Zeitlimit überschritten. Kein Ausschluss dieses Objekttyps.'))
        except (ValueError,KeyError,OSError,requests.RequestException) as error:
            # Do not echo request URLs, user credentials or raw provider responses.
            results.append(status(provider,'error','Abgleich nicht abgeschlossen: '+type(error).__name__))
        except Exception as error:
            results.append(status(provider,'error','Katalogantwort konnte nicht verarbeitet werden: '+type(error).__name__))
    return results
