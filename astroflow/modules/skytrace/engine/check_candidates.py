"""Offline regression checks for geometry, missing archives, cutouts and UTC context."""
import datetime as dt
import json
from pathlib import Path
import tempfile
from unittest.mock import patch
import cv2
import numpy as np
from astropy.io import fits
from astropy.wcs import WCS
import catalog_matching as cm
from observation_settings import resolve_site,validate_site
from candidate_report import extract_candidates
from sequence import report,stretch

site={'lat':0.,'lon':0.,'alt_m':0.}
stamp=dt.datetime(2022,5,6,20,tzinfo=dt.timezone.utc).timestamp()
c={'timestamp':stamp,'timestamp_text':'UTC','exposure_seconds':10,'world_path':[[150.,10.]]}
assert cm.asteroid_check(c,None,{})['status']=='not_checkable'
assert cm.satellite_check(c,site,{})['status']=='needs_archive'
assert cm.aircraft_check(c,site,{})['status']=='needs_archive'
assert cm.angular_match(c,[150.],[10.],30)['compatible']
assert not cm.angular_match(c,[151.],[10.],30)['compatible']
long=dict(c,world_path=[[150.,10.],[151.,10.]])
assert not cm.angular_match(long,[150.5],[10.],30)['compatible'],'Crossing a trail at one point is not a trajectory match'
assert cm.angular_match(long,[150.,150.5,151.],[10.,10.,10.],30)['compatible']
payload={'signature':{'version':'1.1'},'fields_second':['Object name','Astrometric RA (hh:mm:ss)','Astrometric Dec (dd mm\'ss")','Visual magnitude (V)'],'data_second_pass':[['test asteroid','10:00:00','+10 00\'00.0"','15.2']],'n_second_pass':1}
with patch.object(cm,'retrieve',return_value=payload) as get:
    result=cm.asteroid_check(c,site,{})
    assert result['status']=='compatible',result
    p=get.call_args.args[1]
    assert p['obs-time']=='2022-05-06_20:00:05' and p['alt']==0 and p['fov-ra-center']=='10-00-00.000',p
with patch.object(cm,'retrieve',side_effect=ValueError('bad response')):
    assert cm.compare(c,site,{'enabled':True})[0]['status']=='error'

with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as temp:
    root=Path(temp);out=root/'out';folder=out/'sequence';folder.mkdir(parents=True)
    source=root/'source';source.mkdir()
    header=fits.Header();header['SITELAT']=40.;header['SITELONG']=10.;header['SITEELEV']=123.
    yy,xx=np.mgrid[:120,:160];base=100+xx*.15+yy*.2
    rows=[]
    for i in range(5):
        raw=base.copy();raw[45,40+i]=500
        path=source/f'img-{i}.fits';fits.writeto(path,raw,header)
        d=[dict(kind='trail',label='Testspur',confidence=None,box=[.2,.3,.5,.6],segment=[[40,45],[65,55]])] if i==2 else []
        rows.append(dict(index=i,file=str(path.resolve()),timestamp=stamp+i*11,timestamp_text=f'UTC {i}',detections=d,reference_to_current=np.eye(2,3).tolist()))
    assert resolve_site(path,'session',{'site':site,'sites':{}})['alt_m']==123
    assert resolve_site(path,'session',{'sites':{'session':site}})['lat']==0
    accepted=[r for r in rows if r['index']!=1]
    (folder/'frames.jsonl').write_text('\n'.join(json.dumps(r) for r in accepted),encoding='utf-8')
    (folder/'ausgeschlossen.jsonl').write_text(json.dumps(rows[1]),encoding='utf-8')
    w=WCS(naxis=2);w.wcs.crpix=[80.,60.];w.wcs.cdelt=[-.001,.001];w.wcs.crval=[150.,10.];w.wcs.ctype=['RA---TAN','DEC--TAN']
    solve=folder/'platesolve';solve.mkdir();w.to_header().tofile(solve/'loesung.wcs')
    (solve/'ergebnis.json').write_text(json.dumps({'width':160,'height':120}),encoding='utf-8')
    summary=dict(session='session',video='sequence/ereignisse.mp4',frames=4,hits=1,fps=8)
    candidates=extract_candidates(out,summary,{'enabled':False,'sites':{}})
    assert len(candidates)==1
    item=candidates[0];assert item['world_path'] and len(item['frames'])==5,item
    assert [f['index'] for f in item['frames']]==[1,2,3,4,5]
    assert item['frames'][1]['excluded'] and not item['frames'][1]['hit']
    assert [f['hit'] for f in item['frames']]==[False,False,True,False,False]
    assert all(r['status']=='disabled' for r in item['catalogs'])
    from analyse import load_image
    raw,_=load_image(source/'img-2.fits');gray=stretch(raw,summary['display_limits'])
    x1,y1,x2,y2=item['crop']
    decoded=cv2.imread(str(out/item['frames'][2]['image']),cv2.IMREAD_GRAYSCALE)
    assert np.array_equal(decoded,gray[y1:y2,x1:x2]),'Overlay must never be burned into candidate pixels'
    import av
    with av.open(str(folder/'original.mp4')) as movie: assert sum(1 for f in movie.decode(video=0))==4
    report(out,[summary]);page=(out/'bericht.html').read_text(encoding='utf-8')
    assert 'id="globalOverlay" type="checkbox" checked' in page and 'createElementNS' in page
    (solve/'ergebnis.json').unlink();(solve/'loesung.wcs').unlink()
    with patch('solving.solve_image',return_value=dict(wcs=w,width=160,height=120,ra=150.,dec=10.)) as solver,patch.object(cm,'compare',return_value=[]):
        restored=extract_candidates(out,summary,{'enabled':True,'auto_solve':True,'sites':{}},make_clean=False)
        solver.assert_called_once()
        assert summary['plate_solving']['status']=='solved' and restored[0]['world_path']
    # A current TLE must never be substituted for historical data.
    tle=root/'stale.tle';tle.write_text('ISS\n1 25544U 98067A   24123.50000000  .00010000  00000-0  18000-3 0  9990\n2 25544  51.6400  40.0000 0005000  20.0000  30.0000 15.50000000123456\n')
    assert cm.satellite_check(c,site,{'tle_file':str(tle)})['status']=='not_checkable'
    # Generate an independent known flight geometry; exact timestamps must bracket the exposure.
    csv=root/'flights.csv'
    csv.write_text('timestamp,latitude,longitude,geo_altitude,icao24,callsign\n'+f'{stamp},0.05,0.1,10000,test,SYNTHETIC\n{stamp+10},0.06,0.12,10000,test,SYNTHETIC\n')
    times=cm.TS.from_datetimes([dt.datetime.fromtimestamp(t,dt.timezone.utc) for t in [stamp,stamp+10]])
    vector=(cm.wgs84.latlon([.05,.06],[.1,.12],elevation_m=[10000,10000])-cm._observer(site)).at(times)
    ra,dec,_=vector.radec();flight=dict(c,world_path=np.column_stack([ra.hours*15,dec.degrees]).tolist())
    assert cm.aircraft_check(flight,site,{'flight_file':str(csv)})['status']=='compatible'
    csv.write_text('timestamp,latitude,longitude,geo_altitude,icao24\n'+f'{stamp+3},0.05,0.1,10000,test\n')
    assert cm.aircraft_check(flight,site,{'flight_file':str(csv)})['status']=='not_checkable'
print('OK: UTC, world geometry, archives, provider errors, clean cutouts, excluded context, raw MP4, FITS site and historical ADS-B')
