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
