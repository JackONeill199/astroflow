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


SATCAT='https://celestrak.org/satcat/records.php'
SATCAT_CSV='https://celestrak.org/pub/satcat.csv'
SATCAT_TYPES={'PAY':'Satellit','R/B':'Raketenstufe','DEB':'Trümmerteil','UNK':'unbekanntes Objekt'}


SATCAT_PAUSE_S=0.5           # Abstand zwischen Einzelabfragen
SATCAT_RETRY_S=15*60         # keine HTTP-Antwort (Netz) bzw. unerwarteter Inhalt bei 200: nicht bei jedem Kandidaten erneut
SATCAT_MAX_AGE_S=24*3600     # lokale Kopie höchstens täglich erneuern (SATCAT ändert sich 1–2× am Tag)
SATCAT_MAX_BYTES=200*1024**2
SATCAT_SINGLE=3              # Einzelabfragen nur für Nummern, die jünger sind als die lokale Kopie
SATCAT_PAUSE='satcat-pause.json'
SATCAT_FILE='satcat.csv'
_SATCAT_TABLE={}             # Pfad → (mtime, {Nummer: Eintrag}); einmal je Engine-Prozess einlesen


class SatcatError(Exception):
    """Abruf gescheitert; `http` = Statuscode ≠ 200 (→ anhalten bis zur Freigabe) oder None (→ kurze Pause)."""
    def __init__(self,detail,http=None):
        super().__init__(detail);self.http=http


def _satcat_get(url,params=None,stream=False):
    """GET nach den CelesTrak-Nutzungsregeln: jede Antwort ≠ 200 ist ein Fehler. Weiterleitungen nicht folgen –
    301 heißt laut CelesTrak „falsche/veraltete Adresse“."""
    import requests
    try:response=requests.get(url,params=params,timeout=(10,120) if stream else 8,allow_redirects=False,stream=stream)
    except requests.RequestException as error:raise SatcatError(str(error)) from error
    if response.status_code!=200:
        response.close();raise SatcatError(f'HTTP {response.status_code}',response.status_code)
    return response


def _satcat_entry(row):
    return dict(name=str(row.get('OBJECT_NAME') or '').strip(),type=SATCAT_TYPES.get(row.get('OBJECT_TYPE'),row.get('OBJECT_TYPE')),
                cospar=row.get('OBJECT_ID'),launch=row.get('LAUNCH_DATE'))


def _satcat_table():
    """Lokale Kopie des ganzen SATCAT (CSV, Spalten wie die JSON-Einträge) als {Nummer: Eintrag}; fehlt sie → {}."""
    import csv
    path=CACHE/SATCAT_FILE
    try:mtime=path.stat().st_mtime
    except OSError:return {}
    cached=_SATCAT_TABLE.get(str(path))
    if cached and cached[0]==mtime:return cached[1]
    with open(path,encoding='utf-8',newline='') as handle:
        table={str(int(row['NORAD_CAT_ID'])):_satcat_entry(row) for row in csv.DictReader(handle) if (row.get('NORAD_CAT_ID') or '').strip().isdigit()}
    _SATCAT_TABLE[str(path)]=(mtime,table)
    return table


def _download_satcat(settings):
    """Ganzen Katalog (geschätzt 12–15 MB) laden, prüfen und atomar ersetzen. Gesendet wird nichts außer der Anfrage –
    keine Nummern, kein Standort. Ausdrückliche Ausnahme von „keine weltweiten Vollarchive“ (Nutzerwunsch 01.11.2026)."""
    temporary=CACHE/(SATCAT_FILE+'.part')
    event('status',text='Satellitenkatalog (CelesTrak-SATCAT) wird aktualisiert …')
    response=_satcat_get(SATCAT_CSV,stream=True)
    CACHE.mkdir(parents=True,exist_ok=True);size=0
    try:
        with response,open(temporary,'wb') as handle:
            for chunk in response.iter_content(1<<20):
                check_stop(settings);size+=len(chunk)
                if size>SATCAT_MAX_BYTES:raise SatcatError('Datei unerwartet groß')
                handle.write(chunk)
        with open(temporary,encoding='utf-8',newline='') as handle:
            header=handle.readline()
        if 'NORAD_CAT_ID' not in header or 'OBJECT_NAME' not in header:raise SatcatError('keine SATCAT-CSV')
        replace_file(temporary,CACHE/SATCAT_FILE)
    except (OSError,UnicodeDecodeError) as error:raise SatcatError(str(error)) from error
    finally:temporary.unlink(missing_ok=True)


def _satcat_note(error):
    if error.http:
        return (f'Namen nicht abrufbar: CelesTrak antwortete mit HTTP {error.http}'+(' (Zugriff gesperrt, meist wegen zu vieler Abfragen)' if error.http==403 else '')+
                '. Gemäß den CelesTrak-Nutzungsregeln ist die Namensabfrage angehalten, bis sie unter Einstellungen → SkyTrace '
                'wieder freigegeben wird; bis dahin nur NORAD-Nummern.')
    return f'Namen nicht abrufbar: CelesTrak-Katalog nicht erreichbar oder unerwartete Antwort ({error}). Nächster Versuch in {SATCAT_RETRY_S//60} min.'


def satellite_names(ids,settings,limit=SATCAT_SINGLE):
    """Namen und Objektart je NORAD-Nummer aus dem CelesTrak-SATCAT. Ergebnis `(namen, hinweis)` – Hinweis None oder Grund,
    warum Namen fehlen. Quelle ist eine lokale Kopie des ganzen Katalogs (`satcat.csv`), höchstens täglich und nur bei
    Bedarf erneuert (fehlende Nummer); Nummern, die jünger sind als die Kopie, einzeln (`records.php`, Cache
    `satcat-names.json`, auch „nicht im Katalog“). Anlass 01.11.2026: Einzelabfragen je Nummer führten zur 403-Sperre.
    CelesTrak-Nutzungsregeln: bei **jeder** Antwort außer HTTP 200 sofort aufhören und einem Menschen melden – daher
    `satcat-pause.json` mit `stopped` bis zur Freigabe (`POST /api/skytrace/catalog/satcat/release` löscht die Datei);
    nur ohne HTTP-Antwort bzw. bei unbrauchbarem 200-Inhalt eine kurze Pause."""
    from json_store import read_json
    path=CACHE/'satcat-names.json'
    try:known=read_json(path) if path.exists() else {}
    except (OSError,ValueError):known={}
    try:table=_satcat_table()
    except (OSError,ValueError,KeyError):table={}
    look=lambda:{str(i):table.get(str(i)) or known.get(str(i)) for i in ids if str(i) in table or str(i) in known}
    missing=[str(i) for i in ids if str(i) not in look()]
    # `satcat_offline`: nur Cache (Wartungswerkzeuge, die keine Dienste kontaktieren dürfen).
    if not missing or settings.get('satcat_offline'):return look(),None
    try:pause=read_json(CACHE/SATCAT_PAUSE) if (CACHE/SATCAT_PAUSE).exists() else {}
    except (OSError,ValueError):pause={}
    if pause.get('stopped') or float(pause.get('until') or 0)>time.time():return look(),pause.get('reason')
    added=False
    try:
        try:age=time.time()-(CACHE/SATCAT_FILE).stat().st_mtime
        except OSError:age=float('inf')
        if age>SATCAT_MAX_AGE_S:
            _download_satcat(settings)
            try:table=_satcat_table()
            except (KeyError,ValueError) as error:raise SatcatError(f'CSV nicht lesbar: {error}') from error
            missing=[n for n in missing if n not in table]
        for index,norad in enumerate(missing[:limit]):
            check_stop(settings)
            if index:time.sleep(SATCAT_PAUSE_S)
            response=_satcat_get(SATCAT,{'CATNR':norad,'FORMAT':'json'})
            # Nur eine erkannte „keine Einträge“-Antwort gilt als „nicht im Katalog“ (wird gecacht); unerwartete
            # Antworten (HTML-Fehlerseite, kaputtes JSON, Fehlerobjekt) wie ein Fehler behandeln → später erneut.
            try:rows=response.json()
            except ValueError:
                if 'no satcat records' not in (response.text or '').lower():raise SatcatError('kein SATCAT-Eintrag im JSON-Format')
                rows=[]
            if not isinstance(rows,list) or any(not isinstance(r,dict) for r in rows):raise SatcatError('kein SATCAT-Eintrag im JSON-Format')
            known[norad]=_satcat_entry(rows[0] if rows else {});added=True
        note=None
    except SatcatError as error:
        note=_satcat_note(error)
        entry=dict(stopped=True,http=error.http,since=time.time(),reason=note) if error.http else dict(until=time.time()+SATCAT_RETRY_S,reason=note)
        CACHE.mkdir(parents=True,exist_ok=True);write_json(CACHE/SATCAT_PAUSE,entry)
        event('status',text=note)
    if added:
        CACHE.mkdir(parents=True,exist_ok=True);write_json(path,known)
    return look(),note


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
        # Neu abgleichen (rematch_sites.py): nur, was der ursprüngliche Lauf schon abgerufen hat.
        if settings.get('orbits_cache_only'):raise ArchiveAccessError('needs_archive',f'Bahndaten für {when:%Y-%m-%d %H:%M} UTC liegen nicht im lokalen Cache; beim Neuabgleich werden keine Archive abgefragt.')
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
