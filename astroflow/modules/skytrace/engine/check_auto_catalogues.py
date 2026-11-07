"""Offline validation for automated historical propagation, meteor paths and archive bounds."""
import datetime as dt
import math
from pathlib import Path
import tempfile
from unittest.mock import patch
import numpy as np
from skyfield.api import EarthSatellite,wgs84
import catalog_matching as cm
from satellite_archive import model_from_record,sky_prefilter
from archive_errors import ArchiveAccessError

sat=EarthSatellite('1 25544U 98067A   24123.50000000  .00010000  00000-0  18000-3 0  9990','2 25544  51.6400  40.0000 0005000  20.0000  30.0000 15.50000000123456','fixture',cm.TS)
factor=2*math.pi/1440
m=sat.model
row=dict(norad_id=m.satnum,epoch=sat.epoch.utc_datetime().isoformat(),bstar=m.bstar,mean_motion_dot=m.ndot*1440/factor,eccentricity=m.ecco,arg_perigee=math.degrees(m.argpo),inclination=math.degrees(m.inclo),mean_anomaly=math.degrees(m.mo),mean_motion=m.no_kozai/factor,raan=math.degrees(m.nodeo))
rebuilt=model_from_record(row,cm.TS)
for delta in (0,600,86400):
    at=sat.epoch+delta/86400
    assert np.linalg.norm(sat.at(at).position.km-rebuilt.at(at).position.km)<.05
mid=sat.epoch+5/86400
sub=wgs84.subpoint(sat.at(mid));site=dict(lat=sub.latitude.degrees,lon=sub.longitude.degrees,alt_m=0)
vector=(sat-cm._observer(site)).at(mid);ra,dec,_=vector.radec()
c=dict(world_path=[[ra.hours*15,dec.degrees]],timestamp=sat.epoch.utc_datetime().timestamp(),timestamp_text='UTC',exposure_seconds=10)
assert len(sky_prefilter([(sat,0)],c['timestamp'],10,site,c))==1
opposite=dict(c,world_path=[[(ra.hours*15+180)%360,-dec.degrees]])
assert not sky_prefilter([(sat,0)],c['timestamp'],10,site,opposite)
print('OK: archived orbital elements reproduce TLE propagation; conservative prefilter preserves a real compatible satellite')

start=dt.datetime(2022,9,23,3,tzinfo=dt.timezone.utc).timestamp();site=dict(lat=49,lon=8,alt_m=100)
event=dict(id='SYNTHETIC',timestamp=start,Duration=1.,LatBeg=49.5,LonBeg=8.1,HtBeg=100,LatEnd=49.4,LonEnd=8.3,HtEnd=80,shower='TEST')
times=cm.TS.from_datetimes([dt.datetime.fromtimestamp(start+s,dt.timezone.utc) for s in (0,1)])
target=wgs84.latlon([49.5,49.4],[8.1,8.3],elevation_m=[100000,80000]);vector=(target-cm._observer(site)).at(times);ra,dec,_=vector.radec()
c=dict(timestamp=start,timestamp_text='UTC',exposure_seconds=10,world_path=np.column_stack([ra.hours*15,dec.degrees]).tolist())
with patch('archive_sources.gmn_events',return_value=([event],1)):
    assert cm.meteor_check(c,site,{})['status']=='compatible'
    assert cm.meteor_check(c,None,{})['status']=='not_checkable'
print('OK: measured meteor trajectory projected topocentrically; missing site prevents archive access')

import archive_sources as sources
with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as temp,patch('requests.get',side_effect=AssertionError('Worldwide download attempted')):
    with patch.object(sources,'CACHE',Path(temp)):
        for call in (lambda:sources.orbit_snapshot(start,{}),lambda:sources.gmn_events(start,start+10,{},site=site),lambda:sources.eth_tles(start,{})):
            try:call()
            except ArchiveAccessError as error:assert error.status=='needs_archive'
            else:raise AssertionError('Missing bulk archive must not trigger a network download')
    with patch.object(cm,'CACHE',Path(temp)):
        try:cm.fireball_check(c,site,{})
        except ArchiveAccessError as error:assert error.status=='needs_archive'
        else:raise AssertionError('Unfiltered fireball download attempted')
print('OK: absent satellite, meteor and fireball caches never cause worldwide downloads')

import json
with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as temp,patch.object(sources,'CACHE',Path(temp)):
    dbpath=Path(temp)/'traj_summary_monthly_202209.sqlite'
    hidden=dict(event,id='HIDDEN',LatBeg=-49,LonBeg=-172,LatEnd=-48.9,LonEnd=-172.1)
    with closing(sqlite3.connect(dbpath)) as db:
        db.execute('CREATE TABLE events(stamp REAL,duration REAL,payload TEXT)')
        db.executemany('INSERT INTO events VALUES(?,?,?)',[(e['timestamp'],e['Duration'],json.dumps(e)) for e in (event,hidden)])
        db.commit()
    records,total=sources.gmn_events(start,start+10,{},site=site)
    assert total==2 and [r['id'] for r in records]==['SYNTHETIC']
print('OK: GMN selects the visible trajectory and excludes the opposite hemisphere')
