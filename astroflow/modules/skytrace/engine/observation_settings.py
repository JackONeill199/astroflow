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

# ---- Gemeinsame Ortsliste von AstroFlow (astroflow/core/sites.py) ----------------------------------------------
# Der Server setzt SITES_FILE (im Prozess) bzw. die Umgebung ASTROFLOW_SITES (Engine-Unterprozess) auf data/settings.json.
SITES_FILE=None
_SITES_MODULE=None

def sites_module():
    """astroflow.core.sites – im Server per Import, im Engine-Interpreter (ohne AstroFlow-Paket) direkt über den Pfad."""
    global _SITES_MODULE
    if _SITES_MODULE is None:
        try:
            from astroflow.core import sites as module
        except Exception:
            import importlib.util
            path=CODE.parents[2]/'core'/'sites.py'
            if not path.is_file(): return None
            spec=importlib.util.spec_from_file_location('astroflow_core_sites',path)
            module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        _SITES_MODULE=module
    return _SITES_MODULE

def shared_sites_file(settings=None):
    import os
    value=(settings or {}).get('_sites_file') or SITES_FILE or os.environ.get('ASTROFLOW_SITES')
    return Path(value) if value else None

ORIGIN_TEXT={'period':'Ortsliste','default':'Ortsliste (Standard-Ort)'}

def _with_accuracy(site,origin,default_accuracy):
    site=dict(site); site['origin']=origin
    site['accuracy_m']=float(site.get('accuracy_m') or default_accuracy)
    return site

def resolve_site(path,session,settings=None,when=None):
    """Reihenfolge: Standort je Beobachtung (SkyTrace-Einstellung) → FITS-Kopf → gemeinsame Ortsliste (Zeitraum der
    Nacht, sonst Standard-Ort) → alter SkyTrace-Standard. `when` = Aufnahmezeit (Unix-Sekunden), sonst DATE-OBS.
    Ergebnis mit `accuracy_m` (Radius in m) und `origin` (override|fits|period|default|skytrace)."""
    settings=settings or load_settings()
    if session in (settings.get('sites') or {}):
        site=validate_site(settings['sites'][session])
        if site: return _with_accuracy(dict(site,source=site.get('source') or 'Standort dieser Beobachtung'),'override',100)
    header=None
    if path and Path(path).suffix.lower() in {'.fits','.fit','.fts'}:
        try: header=fits.getheader(path)
        except OSError: header=None                              # Rohbild schon gelöscht → Ortsliste/Einstellungen
    if header is not None:
        for latkey,lonkey,altkey in [('OBSGEO-B','OBSGEO-L','OBSGEO-H'),('SITELAT','SITELONG','SITEELEV'),('LAT-OBS','LONG-OBS','ALT-OBS')]:
            if latkey in header and lonkey in header:
                try: return _with_accuracy(validate_site(dict(lat=header[latkey],lon=header[lonkey],alt_m=header.get(altkey,0),source='FITS-Metadaten',name='Aufnahmestandort')),'fits',30)
                except (ValueError,TypeError): pass
        if when is None: when=header.get('DATE-OBS')
    shared=shared_sites_file(settings); module=sites_module() if shared else None
    if module:
        try:
            found=module.SiteBook(path=shared).site_for(when=when)
        except Exception:
            found=None
        if found:
            text=ORIGIN_TEXT.get(found['source'],'Ortsliste')+(f" · {found['detail']}" if found['source']=='period' else '')
            return dict(lat=found['lat'],lon=found['lon'],alt_m=found.get('alt_m') or 0,name=found['name'],source=text,
                        site_id=found.get('id'),accuracy_m=float(found['accuracy_m']),origin=found['source'],night=module.night_of(when))
    site=validate_site(settings.get('site'))
    return _with_accuracy(site,'skytrace',100) if site else None
