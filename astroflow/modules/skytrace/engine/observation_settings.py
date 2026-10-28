"""Local observer configuration; never infer Earth location from a solved star field."""
import json
import copy
import threading
from pathlib import Path
from astropy.io import fits

from project_paths import BASE, CODE
CONFIG=BASE/'catalog_settings.json'
DEFAULTS=dict(enabled=True,auto_solve=True,auto_satellites=True,auto_meteors=True,auto_aircraft=True,opensky_user='',site=None,sites={},tle_file='',tle_url='',flight_file='',flight_url='',geocoder_url='https://photon.komoot.io/api/',max_tle_age_days=3,asteroid_mag_limit=20)
SETTINGS_LOCK=threading.RLock()

def load_settings():
    from json_store import read_json
    saved=read_json(CONFIG,{})
    return dict(copy.deepcopy(DEFAULTS),**saved) if isinstance(saved,dict) else copy.deepcopy(DEFAULTS)

def save_settings(settings):
    from json_store import write_json
    clean={k:v for k,v in settings.items() if 'password' not in k.lower() and 'secret' not in k.lower()}
    with SETTINGS_LOCK: write_json(CONFIG,clean)


def update_settings(data):
    with SETTINGS_LOCK:
        settings=load_settings()
        scope=data.get('session')
        if scope is not None and (not isinstance(scope,str) or len(scope)>500): raise ValueError('Ungültige Beobachtung.')
        if 'site' in data:
            site=validate_site(data['site'])
            if site: site={k:site[k] for k in ('lat','lon','alt_m','name','source') if k in site}
            if scope:
                if site: settings.setdefault('sites',{})[scope]=site
                else: settings.setdefault('sites',{}).pop(scope,None)
            else: settings['site']=site
        for key in ('enabled','auto_solve','auto_satellites','auto_meteors','auto_aircraft'):
            if key in data:
                if not isinstance(data[key],bool): raise ValueError(key+' muss true oder false sein.')
                settings[key]=data[key]
        if 'opensky_user' in data:
            user=data['opensky_user']
            if not isinstance(user,str) or len(user)>150 or any(c in user for c in '\r\n'):raise ValueError('Ungültiger OpenSky-Benutzername.')
            settings['opensky_user']=user.strip().lower()
        for key in ('tle_file','tle_url','flight_file','flight_url'):
            if key in data:
                if not isinstance(data[key],str): raise ValueError('Katalogpfade müssen Text sein.')
                settings[key]=data[key].strip()
        for key in ('tle_url','flight_url'):
            if settings.get(key) and not settings[key].startswith('https://'): raise ValueError('Download-URLs müssen HTTPS verwenden.')
        save_settings(settings)
        return settings

def validate_site(site):
    if not site: return None
    lat,lon,alt=float(site['lat']),float(site['lon']),float(site.get('alt_m',0))
    if not -90<=lat<=90 or not -180<=lon<=180 or not -500<=alt<=10000: raise ValueError('Ungültige Standortkoordinaten oder Höhe')
    return dict(site,lat=lat,lon=lon,alt_m=alt)

def resolve_site(path,session,settings=None):
    settings=settings or load_settings()
    if session in settings.get('sites',{}): return validate_site(settings['sites'][session])
    if Path(path).suffix.lower() in {'.fits','.fit','.fts'}:
        header=fits.getheader(path)
        for latkey,lonkey,altkey in [('OBSGEO-B','OBSGEO-L','OBSGEO-H'),('SITELAT','SITELONG','SITEELEV'),('LAT-OBS','LONG-OBS','ALT-OBS')]:
            if latkey in header and lonkey in header:
                try: return validate_site(dict(lat=header[latkey],lon=header[lonkey],alt_m=header.get(altkey,0),source='FITS-Metadaten',name='Aufnahmestandort'))
                except (ValueError,TypeError): pass
    return validate_site(settings.get('site'))
