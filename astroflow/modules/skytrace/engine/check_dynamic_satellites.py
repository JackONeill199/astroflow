"""Offline regression: UTC-bounded TLE fetching, complete cache and propagation."""
import datetime as dt
from pathlib import Path
import tempfile
from unittest.mock import patch
from urllib.parse import urlencode
import requests
import archive_sources as sources
import satellite_archive as satellites
import catalog_matching as cm
from opensky_archive import ArchiveAccessError

TLE='ISS\n1 25544U 98067A   24123.50000000  .00010000  00000-0  18000-3 0  9990\n2 25544  51.6400  40.0000 0005000  20.0000  30.0000 15.50000000123456\n'
stamp=dt.datetime(2024,5,2,12,tzinfo=dt.timezone.utc).timestamp()

with tempfile.TemporaryDirectory(dir=cm.BASE) as temp,patch.object(sources,'CACHE',Path(temp)):
    calls=[]
    def fetch(url,params,text,settings,deadline,service):
        calls.append((url,params))
        assert service=='Satellitenarchiv'
        if params:
            assert params['start-datetime']=='20240502T1200'
            assert params['end-datetime']=='20240502T1201'
            assert params['before']==params['after']==3
            return dict(results=[dict(norad_str=TLE)],next=sources.ETH+'?'+urlencode(dict(params,cursor='second')))
        return dict(results=[dict(norad_str=TLE)],next=None)
    with patch.object(cm,'_bounded_jpl',side_effect=fetch):
        models,source,total=satellites.historical_models(stamp,{})
        assert len(models)==1 and total==2 and source==sources.ETH
        assert len(calls)==2 and models[0][1]<.001
        satellites.historical_models(stamp,{})
        assert len(calls)==2,'Complete cache must avoid a repeat HTTP call'
    assert len(list(Path(temp).glob('eth-*.json')))==1
    # A different UTC date gets a different query/cache; no reference to 2022.
    with patch.object(cm,'_bounded_jpl',return_value=dict(results=[],next=None)) as get:
        result=sources.eth_tles(stamp+86400,{})
        assert not result['tles'] and get.call_args.args[1]['start-datetime']=='20240503T1200'
    assert len(list(Path(temp).glob('eth-*.json')))==1,'Empty answers must not persist'
    for following in ('https://other.example/api/satellitedata/',sources.ETH+'?cursor=unbounded'):
        with patch.object(cm,'_bounded_jpl',return_value=dict(results=[dict(norad_str=TLE)],next=following)):
            try:sources.eth_tles(stamp+2*86400,{})
            except ValueError:pass
            else:raise AssertionError('Unbounded pagination accepted')
    assert len(list(Path(temp).glob('eth-*.json')))==1,'Partial results published'
    with patch.object(cm,'_bounded_jpl',side_effect=requests.Timeout):
        try:sources.eth_tles(stamp+2*86400,{})
        except requests.Timeout:pass
        else:raise AssertionError('Timeout masked')
    stop=Path(temp)/'STOP';stop.touch()
    with patch.object(cm,'_bounded_jpl') as get:
        try:sources.eth_tles(stamp,{'_stop_file':str(stop)})
        except InterruptedError:pass
        else:raise AssertionError('Stop ignored for cached data')
        get.assert_not_called()
    with patch.object(cm,'_bounded_jpl') as get:
        try:sources.eth_tles(dt.datetime(2021,1,1,tzinfo=dt.timezone.utc).timestamp(),{})
        except ArchiveAccessError as error:assert error.status=='needs_archive' and '2021-01-01' in str(error)
        else:raise AssertionError('Pre-archive date claimed available')
        get.assert_not_called()

# Local data remains preferred; current observations also follow their UTC.
with patch.object(sources,'orbit_snapshot',return_value={'source':'local','records':[{}]}),patch.object(satellites,'models_for_day',return_value=[(object(),dt.datetime.fromtimestamp(stamp,dt.timezone.utc).isoformat())]),patch.object(sources,'eth_tles') as get:
    assert satellites.historical_models(stamp,{})[1]=='local'
    get.assert_not_called()
now=dt.datetime.now(dt.timezone.utc).timestamp()
with patch.object(satellites,'historical_models',return_value=([],sources.ETH,0)) as get:
    result=cm.satellite_check(dict(timestamp=now,timestamp_text='UTC',exposure_seconds=10),dict(lat=0,lon=0),{'auto_satellites':True})
    assert result['status']=='needs_archive'
    assert get.call_args.args[0]==now+5
print('OK: dynamic UTC queries, pagination, complete cache, empty coverage, stop, timeout, local priority and recent observations')

# A local New Year's query can use the neighbouring year without downloading it.
import polars as pl
with tempfile.TemporaryDirectory(dir=cm.BASE) as temp,patch.object(sources,'CACHE',Path(temp)),patch('requests.get',side_effect=AssertionError('Bulk download attempted')):
    epoch=dt.datetime(2022,12,31,23,tzinfo=dt.timezone.utc)
    pl.DataFrame({'norad_id':[25544],'epoch':[epoch]}).write_parquet(Path(temp)/'tle_2022.parquet')
    result=sources.orbit_snapshot(dt.datetime(2023,1,1,tzinfo=dt.timezone.utc).timestamp(),{})
    assert len(result['records'])==1 and result['day']=='2023-01-01'
print('OK: local UTC year boundary includes an available adjacent year')
