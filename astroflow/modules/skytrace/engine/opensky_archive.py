"""Official OpenSky Trino OAuth connection and narrowly partitioned historical queries."""
import csv
import hashlib
import io
import json
from pathlib import Path
import threading
import time
from urllib.parse import urlsplit
import requests
from analyse import BASE
from json_store import read_json,write_json

STATE=BASE/'catalog_cache/opensky-connection.json'
LOCK=threading.RLock()
WORKER=None
HOST='trino.opensky-network.org'


class ArchiveAccessError(Exception):
    def __init__(self,status,detail):self.status=status;super().__init__(detail)


def connection_state():return read_json(STATE,{'status':'not_connected','detail':'Noch kein OpenSky-Trino-Konto verbunden.'})


def publish(status,detail,user='',**extra):
    STATE.parent.mkdir(exist_ok=True)
    with LOCK:write_json(STATE,dict(status=status,detail=detail,user=user,updated=time.time(),**extra))


def connect(user,interactive=False):
    from trino.dbapi import connect as trino_connect
    from trino.auth import OAuth2Authentication
    user=user.strip().lower()
    if not user:raise ArchiveAccessError('needs_login','OpenSky-Trino-Konto in den Einstellungen verbinden. Die Archivfreigabe muss beim Anbieter vorliegen.')
    def redirect(url):
        parsed=urlsplit(url)
        if parsed.scheme!='https' or not (parsed.hostname or '').endswith('.opensky-network.org'):raise ValueError('Unerwartetes OpenSky-Anmeldeziel.')
        if not interactive:
            publish('needs_login','Die Anmeldung ist abgelaufen. OpenSky in den Einstellungen erneut verbinden.',user)
            raise ArchiveAccessError('needs_login','OpenSky-Anmeldung erforderlich; bitte in den Einstellungen verbinden.')
        publish('needs_login','Anmeldeseite öffnen und bei OpenSky anmelden. Kein Passwort in AstroAnalyse eingeben.',user,auth_url=url)
    return trino_connect(host=HOST,port=443,http_scheme='https',user=user,catalog='minio',schema='osky',timezone='UTC',auth=OAuth2Authentication(redirect_auth_url_handler=redirect),request_timeout=30,max_attempts=2,session_properties={'query_max_run_time':'3m'})


def start_connection(user):
    global WORKER
    user=user.strip().lower()
    if not user or len(user)>150 or '\n' in user or '\r' in user:raise ValueError('OpenSky-Benutzername fehlt oder ist ungültig.')
    with LOCK:
        if WORKER is not None and WORKER.is_alive():return connection_state()
        publish('connecting','Verbindung zum offiziellen OpenSky-Trino-Dienst wird hergestellt …',user)
        def work():
            connection=None
            try:
                connection=connect(user,True);cursor=connection.cursor();cursor.execute('DESCRIBE state_vectors_data4');cursor.fetchall()
                publish('connected','Anmeldung erfolgreich. Die Berechtigung für historische Positionsdaten wird beim Abruf geprüft.',user)
            except Exception as error:
                denied='denied' in str(error).lower() or 'forbidden' in str(error).lower()
                publish('needs_access' if denied else 'error','OpenSky-Archivzugang nicht freigeschaltet.' if denied else 'Anmeldung nicht abgeschlossen. Verbindung erneut starten; OpenSky-Trino-Freigabe prüfen.',user)
            finally:
                if connection:connection.close()
        WORKER=threading.Thread(target=work,daemon=True);WORKER.start()
        return connection_state()


def flight_query(start,duration,site):
    if duration<=0 or duration>600:raise ValueError('Historische Flugabfrage unterstützt Belichtungen bis 10 Minuten.')
    begin=int(start//60*60)-20;end=int(start//60*60)+60+int(duration)+20
    first=begin//3600*3600;last=end//3600*3600
    hours=','.join(str(h) for h in range(first,last+1,3600))
    lat=float(site['lat']);lon=float(site['lon'])
    import math
    # A conservative geographic cap for aircraft up to 60 km altitude;
    # the exact WGS84 horizon predicate below discards invisible positions.
    radius=math.degrees(math.acos(6356000/(6356000+60000)))+.1
    delta=180 if abs(lat)+radius>=90 else math.degrees(math.asin(min(1,math.sin(math.radians(radius))/math.cos(math.radians(lat)))))
    low=lon-delta;high=lon+delta
    if delta>=180:longitude='TRUE'
    elif low < -180:longitude=f'(lon >= {low+360:.8f} OR lon <= {high:.8f})'
    elif high >180:longitude=f'(lon >= {low:.8f} OR lon <= {high-360:.8f})'
    else:longitude=f'lon BETWEEN {low:.8f} AND {high:.8f}'
    from visibility import aircraft_horizon_sql
    horizon=aircraft_horizon_sql(site)
    return f'''SELECT time, icao24, lat, lon, geoaltitude, callsign
FROM state_vectors_data4
WHERE hour IN ({hours}) AND time BETWEEN {begin} AND {end}
AND lat BETWEEN {max(-90,lat-radius):.8f} AND {min(90,lat+radius):.8f} AND {longitude}
AND lat IS NOT NULL AND lon IS NOT NULL AND geoaltitude IS NOT NULL
AND geoaltitude BETWEEN -500 AND 60000 AND {horizon}
AND onground = false AND time-lastposupdate <= 15 AND time-lastcontact <= 15
LIMIT 50001'''


def fetch_flights(start,duration,site,settings):
    user=settings.get('opensky_user','').strip().lower();state=connection_state()
    if not user or state.get('user')!=user or state.get('status')!='connected':
        raise ArchiveAccessError('needs_access' if state.get('status')=='needs_access' else 'needs_login','Historische Flugpositionen benötigen ein freigeschaltetes OpenSky-Trino-Konto. In den Einstellungen über OpenSky anmelden.')
    query=flight_query(start,duration,site)
    key=hashlib.sha256((user+query).encode()).hexdigest();path=BASE/'catalog_cache/archives'/('flights-'+key+'.json')
    saved=read_json(path)
    if saved:return saved['csv'],saved['source']
    from archive_sources import check_stop
    check_stop(settings);connection=None;cursor=None
    try:
        connection=connect(user);cursor=connection.cursor();cursor.execute(query)
        values=[]
        while True:
            check_stop(settings);chunk=cursor.fetchmany(1000)
            if not chunk:break
            values.extend(chunk)
            if len(values)>50000:raise ArchiveAccessError('not_checkable','Flugabfrage enthält zu viele Positionen; unvollständige Abdeckung wird nicht als Ausschluss gewertet.')
        output=io.StringIO();writer=csv.writer(output);writer.writerow(['timestamp','icao24','latitude','longitude','geo_altitude','callsign']);writer.writerows(values)
        result=dict(csv=output.getvalue(),source='https://opensky-network.org/data/trino',records=len(values),query=query)
        path.parent.mkdir(parents=True,exist_ok=True);write_json(path,result)
        return result['csv'],result['source']
    except InterruptedError:raise
    except ArchiveAccessError:raise
    except Exception as error:
        if 'denied' in str(error).lower() or 'forbidden' in str(error).lower():raise ArchiveAccessError('needs_access','OpenSky hat diesen Archivabruf nicht freigegeben. Eine Trino-Datenfreigabe/Lizenz ist erforderlich.') from None
        raise ArchiveAccessError('error','OpenSky-Trino-Abfrage nicht abgeschlossen: '+type(error).__name__) from None
    finally:
        if cursor:
            try:cursor.close()
            except Exception:pass
        if connection:connection.close()
