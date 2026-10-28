"""Cached public meteor/orbit archives, with complete coverage and provenance metadata."""
import datetime as dt
import json
from pathlib import Path
import sqlite3
import math
import time
import hashlib
from urllib.parse import urlsplit,parse_qs
from contextlib import closing
from filelock import FileLock
from json_store import write_json,replace_file
from analyse import BASE,event

CACHE=BASE/'catalog_cache/archives'
GMN='https://globalmeteornetwork.org/data/traj_summary_data/monthly/'
ETH='https://satdb.ethz.ch/api/satellitedata/'
HF='https://huggingface.co/datasets/juliensimon/space-track-tle-history'


def check_stop(settings):
    if settings.get('_stop_file') and Path(settings['_stop_file']).exists():raise InterruptedError('Archivabruf angehalten')


def download(url,path,settings,max_bytes=1200*1024**2):
    """Reuse existing bulk files only; worldwide network fallback is forbidden."""
    check_stop(settings)
    if path.exists():return path
    from opensky_archive import ArchiveAccessError
    raise ArchiveAccessError('needs_archive','Diese öffentliche Quelle bietet nur weltweite Sammelarchive ohne Standortfilter. Automatischer Vollarchiv-Download ist gesperrt; passenden lokalen Bestand oder eine eigene Archivquelle verwenden.')


def eth_tles(stamp,settings):
    """Fetch a minute at the observation epoch plus bounded bracketing TLEs.

    Explicitly permitted time-only exception for satellites; never fetch a bulk
    archive. Publish only after every page succeeded, and cache by UTC minute.
    ETH started collecting on 2023-03-10; absence is not negative evidence.
    """
    from opensky_archive import ArchiveAccessError
    from catalog_matching import _bounded_jpl
    from filelock import Timeout as LockTimeout
    from json_store import read_json
    import requests
    check_stop(settings)
    when=dt.datetime.fromtimestamp(stamp,dt.timezone.utc).replace(second=0,microsecond=0)
    age=float(settings.get('max_tle_age_days',3))
    if not math.isfinite(age) or not 0<age<=3:raise ValueError('Automatischer Satellitenabruf benötigt ein Bahnalter über 0 bis höchstens 3 Tage.')
    if when<dt.datetime(2023,3,10,tzinfo=dt.timezone.utc):
        raise ArchiveAccessError('needs_archive',f'Für {when:%Y-%m-%d} fehlt ein lokales Satellitenarchiv. Das automatische ETH-Archiv beginnt am 10.03.2023; kein Jahresdownload als Ersatz.')
    params={'start-datetime':when.strftime('%Y%m%dT%H%M'),'end-datetime':(when+dt.timedelta(minutes=1)).strftime('%Y%m%dT%H%M'),
            'before':math.ceil(age),'after':math.ceil(age),'without-frequency-data':'True'}
    key=hashlib.sha256(json.dumps(params,sort_keys=True).encode()).hexdigest()
    CACHE.mkdir(parents=True,exist_ok=True)
    target=CACHE/('eth-'+key+'.json')
    deadline=time.monotonic()+180
    guard=FileLock(str(target)+'.lock')
    while True:
        check_stop(settings)
        if time.monotonic()>=deadline:raise requests.Timeout('Satellitenarchiv: Gesamtzeitlimit einschließlich Warteschlange erreicht')
        try:guard.acquire(timeout=.25);break
        except LockTimeout:pass
    try:
        saved=read_json(target,None)
        if saved is not None:return saved
        url=ETH;query=params;seen=set();rows=[]
        for page in range(120):
            check_stop(settings)
            event('status',text=f'Satellitenarchiv · {when:%Y-%m-%d %H:%M} UTC · Seite {page+1} · Anhalten möglich')
            payload=_bounded_jpl(url,query,False,settings,deadline,service='Satellitenarchiv')
            if not isinstance(payload,dict) or not isinstance(payload.get('results'),list) or 'next' not in payload:
                raise ValueError('Ungültige Satellitenarchiv-Antwort')
            for item in payload['results']:
                value=item.get('norad_str') if isinstance(item,dict) else None
                if not isinstance(value,str) or len(value)>4096:raise ValueError('Ungültiger TLE-Datensatz')
                lines=[line for line in value.splitlines() if line.strip()]
                if len(lines) not in (2,3) or not lines[-2].startswith('1 ') or not lines[-1].startswith('2 '):
                    raise ValueError('Ungültiges TLE-Format im Satellitenarchiv')
                rows.append(value)
            following=payload['next']
            if following is None:break
            if not isinstance(following,str):raise ValueError('Ungültige Archiv-Folgeseite')
            parsed=urlsplit(following);values=parse_qs(parsed.query)
            if (parsed.scheme!='https' or parsed.netloc!='satdb.ethz.ch' or parsed.path!='/api/satellitedata/'
                    or parsed.fragment or set(values)-set(params)-{'cursor','page'}
                    or any(values.get(k)!=[str(v)] for k,v in params.items()) or following in seen):
                raise ValueError('Archiv-Folgeseite verlässt den begrenzten Zeitfilter')
            seen.add(following);url=following;query=None
        else:raise ArchiveAccessError('service_unavailable','Satellitenarchiv überschreitet das Seitenlimit; Abfrage unvollständig, keine Auswertung.')
        result=dict(source=ETH,credit='ETH Zürich Satellite Database / CelesTrak',utc=when.isoformat(),params=params,tles=rows)
        # Empty results may reflect delayed archive ingestion: do not persist them.
        if rows:write_json(target,result)
        return result
    finally:guard.release()


def orbit_snapshot(stamp,settings):
    """Select nearest historical mean elements from the public Space-Track mirror."""
    import polars as pl
    when=dt.datetime.fromtimestamp(stamp,dt.timezone.utc)
    day=when.replace(hour=12,minute=0,second=0,microsecond=0)
    target=CACHE/('orbits-'+when.strftime('%Y%m%d')+'.json')
    CACHE.mkdir(parents=True,exist_ok=True)
    with FileLock(str(target)+'.lock',timeout=600):
        if target.exists():return json.loads(target.read_text(encoding='utf-8'))
        # Include already available neighbouring years at the year boundary.
        years=range((day-dt.timedelta(days=3)).year,(day+dt.timedelta(days=3)).year+1)
        paths=[CACHE/f'tle_{year}.parquet' for year in years if (CACHE/f'tle_{year}.parquet').exists()]
        if not paths:download(HF+f'/resolve/main/data/tle_{when.year}.parquet',CACHE/f'tle_{when.year}.parquet',settings)
        check_stop(settings)
        event('status',text=f'Satellitenarchiv {when.year}: zeitnahe Bahnelemente für {when.date()} auswählen …')
        table=(pl.scan_parquet(paths).filter((pl.col('epoch')>=day-dt.timedelta(days=3))&(pl.col('epoch')<=day+dt.timedelta(days=3)))
               .with_columns((pl.col('epoch')-day).dt.total_seconds().abs().alias('_age'))
               .sort('_age').unique(subset='norad_id',keep='first').drop('_age').collect(engine='streaming'))
        records=table.to_dicts()
        for item in records:item['epoch']=item['epoch'].isoformat()
        result=dict(source=HF,credit='US Space Surveillance Network / Space-Track.org; Mirror Julien Simon, CC-BY-4.0',day=when.date().isoformat(),records=records)
        write_json(target,result)
        event('status',text=f'{len(records)} historische Satellitenbahnen für {when.date()} bereit.')
        return result


def gmn_events(start,end,settings,site=None):
    """Import official GMN monthly trajectories once, then query a UTC time index."""
    from opensky_archive import ArchiveAccessError
    from visibility import above_horizon
    if not site:raise ArchiveAccessError('not_checkable','Standort fehlt; ohne Standort wird kein Meteorarchiv geladen.')
    first=dt.datetime.fromtimestamp(start,dt.timezone.utc).date().replace(day=1)
    last=dt.datetime.fromtimestamp(end,dt.timezone.utc).date().replace(day=1)
    months=[first]
    if last!=first:months.append(last)
    events=[];total=0
    for month in months:
        stem='traj_summary_monthly_'+month.strftime('%Y%m')
        text=CACHE/(stem+'.txt');dbpath=CACHE/(stem+'.sqlite')
        CACHE.mkdir(parents=True,exist_ok=True)
        with FileLock(str(dbpath)+'.lock',timeout=600):
            if not dbpath.exists():
                download(GMN+stem+'.txt',text,settings,max_bytes=400*1024**2)
                temporary=dbpath.with_suffix('.building.sqlite');temporary.unlink(missing_ok=True)
                connection=sqlite3.connect(temporary)
                try:
                    connection.execute('CREATE TABLE events(stamp REAL,duration REAL,payload TEXT)')
                    fields=None;batch=[];count=0
                    with text.open(encoding='utf-8-sig',errors='strict') as stream:
                        for line in stream:
                            check_stop(settings)
                            if line.startswith('#'):
                                if 'Unique trajectory' in line and 'LatBeg' in line:fields=[v.strip() for v in line[1:].split(';')]
                                continue
                            if not line.strip():continue
                            if fields is None:raise ValueError('Unbekanntes GMN-Archivformat.')
                            values=[v.strip() for v in line.split(';')]
                            try:
                                stamp=(float(values[1])-2440587.5)*86400
                                item={key:float(values[fields.index(key)] ) for key in ('LatBeg','LonBeg','HtBeg','LatEnd','LonEnd','HtEnd','Duration')}
                                item.update(id=values[0],timestamp=stamp,shower=values[4])
                            except (ValueError,IndexError) as error:raise ValueError('GMN-Datensatz konnte nicht vollständig gelesen werden.') from error
                            batch.append((stamp,item['Duration'],json.dumps(item)));count+=1
                            if len(batch)>=1000:connection.executemany('INSERT INTO events VALUES(?,?,?)',batch);batch=[]
                    connection.executemany('INSERT INTO events VALUES(?,?,?)',batch)
                    if not count:raise ValueError('GMN-Archiv enthält keine Flugbahnen.')
                    connection.execute('CREATE INDEX event_time ON events(stamp)');connection.commit()
                finally:connection.close()
                replace_file(temporary,dbpath)
                event('status',text=f'GMN {month:%Y-%m}: {count} vermessene Meteorflugbahnen indiziert.')
        with closing(sqlite3.connect(dbpath)) as connection:
            total+=connection.execute('SELECT COUNT(*) FROM events').fetchone()[0]
            for row in connection.execute('SELECT payload FROM events WHERE stamp BETWEEN ? AND ? AND stamp+duration>=?',(start-60,end,start)):
                item=json.loads(row[0])
                # The trajectory is a straight Cartesian segment. The horizon
                # dot product is linear, so at least one endpoint must be up.
                if any(above_horizon(item['Lat'+part],item['Lon'+part],item['Ht'+part]*1000,site) for part in ('Beg','End')):events.append(item)
    return events,total


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--year',type=int);args=parser.parse_args()
    if args.year:download(HF+f'/resolve/main/data/tle_{args.year}.parquet',CACHE/f'tle_{args.year}.parquet',{})
