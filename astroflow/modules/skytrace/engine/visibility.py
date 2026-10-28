"""WGS84 geometric horizon; terrain, clouds and brightness are not modelled."""
import math

A=6378137.0
E2=6.69437999014e-3


def ecef(lat,lon,height):
    lat,lon=math.radians(lat),math.radians(lon)
    n=A/math.sqrt(1-E2*math.sin(lat)**2)
    return ((n+height)*math.cos(lat)*math.cos(lon),
            (n+height)*math.cos(lat)*math.sin(lon),
            (n*(1-E2)+height)*math.sin(lat))


def above_horizon(lat,lon,height,site):
    phi,lam=math.radians(site['lat']),math.radians(site['lon'])
    up=(math.cos(phi)*math.cos(lam),math.cos(phi)*math.sin(lam),math.sin(phi))
    observer=ecef(site['lat'],site['lon'],site.get('alt_m',0))
    target=ecef(lat,lon,height)
    return sum((b-a)*u for a,b,u in zip(observer,target,up))>0


def aircraft_horizon_sql(site):
    """Same WGS84 half-space test executed by Trino before positions leave it."""
    phi,lam=math.radians(site['lat']),math.radians(site['lon'])
    up=(math.cos(phi)*math.cos(lam),math.cos(phi)*math.sin(lam),math.sin(phi))
    observer=ecef(site['lat'],site['lon'],site.get('alt_m',0))
    n=f'({A}/sqrt(1-{E2}*power(sin(radians(lat)),2)))'
    x=f'(({n}+geoaltitude)*cos(radians(lat))*cos(radians(lon)))'
    y=f'(({n}+geoaltitude)*cos(radians(lat))*sin(radians(lon)))'
    z=f'(({n}*(1-{E2})+geoaltitude)*sin(radians(lat)))'
    return '('+'+'.join(f'({xyz}-({coordinate:.10f}))*({vertical:.16f})' for xyz,coordinate,vertical in zip((x,y,z),observer,up))+') > 0'
