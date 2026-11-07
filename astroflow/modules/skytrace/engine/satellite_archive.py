"""Historical mean-element propagation with vectorized conservative sky preselection."""
import datetime as dt
from functools import lru_cache
import math
import numpy as np
from sgp4.api import Satrec,SatrecArray,WGS72,jday
from skyfield.api import EarthSatellite
from skyfield.sgp4lib import TEME


def model_from_record(row,ts):
    epoch=dt.datetime.fromisoformat(row['epoch'].replace('Z','+00:00'))
    jd,fr=jday(epoch.year,epoch.month,epoch.day,epoch.hour,epoch.minute,epoch.second+epoch.microsecond/1e6)
    model=Satrec();factor=2*math.pi/1440
    model.sgp4init(WGS72,'i',int(row['norad_id']),jd+fr-2433281.5,float(row['bstar']),float(row.get('mean_motion_dot',0))*factor/1440,0.,float(row['eccentricity']),math.radians(row['arg_perigee']),math.radians(row['inclination']),math.radians(row['mean_anomaly']),row['mean_motion']*factor,math.radians(row['raan']))
    return EarthSatellite.from_satrec(model,ts)


@lru_cache(maxsize=2)
def models_for_day(day):
    from archive_sources import CACHE
    from json_store import read_json
    from catalog_matching import TS
    payload=read_json(CACHE/('orbits-'+day.replace('-','')+'.json'))
    return [(model_from_record(row,TS),row['epoch']) for row in payload['records']]


def historical_models(start,settings):
    from archive_sources import orbit_snapshot,eth_tles,check_stop
    from archive_errors import ArchiveAccessError
    from catalog_matching import TS
    maximum=float(settings.get('max_tle_age_days',3));mid=dt.datetime.fromtimestamp(start,dt.timezone.utc)
    models=[];payload=None
    try:payload=orbit_snapshot(start,settings)
    except ArchiveAccessError as error:
        if error.status!='needs_archive':raise
    for sat,epoch in (models_for_day(mid.date().isoformat()) if payload else []):
        check_stop(settings)
        age=abs((mid-dt.datetime.fromisoformat(epoch)).total_seconds()/86400)
        if age<=maximum:models.append((sat,age))
    if models:return models,payload['source'],len(payload['records'])
    fetched=eth_tles(start,settings);nearest={}
    for text in fetched['tles']:
        check_stop(settings)
        lines=[line.rstrip() for line in text.splitlines() if line.strip()]
        sat=EarthSatellite(lines[-2],lines[-1],lines[0].removeprefix('0 ').strip() if len(lines)==3 else lines[-2][2:7].strip(),TS)
        age=abs(float(TS.from_datetime(mid)-sat.epoch))
        if age<=maximum and (sat.model.satnum not in nearest or age<nearest[sat.model.satnum][1]):nearest[sat.model.satnum]=(sat,age)
    return list(nearest.values()),fetched['source'],len(fetched['tles'])


def sky_prefilter(models,start,duration,site,candidate):
    """Midpoint cone enlarged by a conservative 20 km/s displacement bound."""
    from catalog_matching import TS,_observer
    if not models:return []
    instant=dt.datetime.fromtimestamp(start+duration/2,dt.timezone.utc)
    t=TS.from_datetime(instant);jd,fr=jday(instant.year,instant.month,instant.day,instant.hour,instant.minute,instant.second+instant.microsecond/1e6)
    array=SatrecArray([sat.model for sat,_ in models]);errors,positions,velocities=array.sgp4(np.array([jd]),np.array([fr]))
    rotation=TEME.rotation_at(t)
    observer=_observer(site).at(t)
    vectors=positions[:,0,:]@rotation-observer.position.km
    from skyfield.api import wgs84
    up=wgs84.latlon(site['lat'],site['lon'],elevation_m=site.get('alt_m',0)+1000).at(t).position.km-observer.position.km
    up/=np.linalg.norm(up)
    world=np.radians(np.asarray(candidate['world_path']));unit=np.column_stack([np.cos(world[:,1])*np.cos(world[:,0]),np.cos(world[:,1])*np.sin(world[:,0]),np.sin(world[:,1])])
    origin=unit[0];extent=float(np.max(np.arccos(np.clip(unit@origin,-1,1))))
    distance=np.linalg.norm(vectors,axis=1)
    travel=20*duration/2+10
    cone=np.where(distance>travel,np.arcsin(np.minimum(1,travel/np.maximum(distance,1))),np.pi)+extent+np.radians(2)
    angle=np.arccos(np.clip((vectors@origin)/np.maximum(distance,1),-1,1))
    keep=(errors[:,0]==0)&np.isfinite(distance)&(vectors@up>=-travel)&(angle<=cone)
    return [pair for pair,valid in zip(models,keep) if valid]
