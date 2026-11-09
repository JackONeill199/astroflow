# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0 OR AGPL-3.0-or-later
# Copyright 2026 JackONeill199 – Doppellizenz (wahlweise), siehe engine/LICENSE und NOTICE.
"""Local angular-motion screening; scenario limits are not universal class limits."""
import math
import numpy as np


def positive(value):
    try:
        value=float(value)
        return value if math.isfinite(value) and value>0 else None
    except (ValueError,TypeError,OverflowError): return None


NARROW_FIELD_DEG=5.


def field_of_view(meta):
    """Full field of view [deg] (width, height) from FITS FOCAL [mm], PIXSZ [µm], NAXIS1/2; None if unknown."""
    focal=positive(meta.get('FOCAL'));pixel=positive(meta.get('PIXSZ'))
    native_width=positive(meta.get('NAXIS1'));native_height=positive(meta.get('NAXIS2'))
    if not (focal and pixel and native_width and native_height): return None
    return [math.degrees(2*math.atan(n*pixel/1000/(2*focal))) for n in (native_width,native_height)]


def narrow_field(meta):
    """Telescope field: the OpenSkyCam model was trained on wide all-sky cameras and is only a hint here."""
    fov=field_of_view(meta or {})
    return bool(fov and max(fov)<NARROW_FIELD_DEG)


def exposure_info(meta):
    """EXPTIME is seconds; Stellina EXPOSURE is milliseconds. Never invent 10 s."""
    seconds=positive(meta.get('EXPTIME'))
    stellina=positive(meta.get('EXPOSURE')) if 'stellina' in str(meta.get('INSTRUME','')).lower() else None
    milliseconds=stellina/1000 if stellina else None
    normalized=positive(meta.get('exposure_seconds'))
    values=[v for v in (seconds,milliseconds,normalized) if v is not None]
    if not values: return dict(seconds=None,source=None,status='missing')
    if max(values)-min(values)>max(values)*.01:
        return dict(seconds=None,source=None,status='conflicting')
    # Stellina schreibt in DATE-OBS das Ende der Belichtung (gemessen an 80 Satellitenspuren 2022: Bahn kreuzt
    # die Spur 0–20 s vor DATE-OBS, langsame Satelliten füllen genau [DATE-OBS − Belichtung, DATE-OBS]).
    return dict(seconds=values[0],source='EXPTIME [s]' if seconds else 'Stellina EXPOSURE [ms]' if milliseconds else 'exposure_seconds [s]',status='known',
                reference='end' if milliseconds else 'start')


def exposure_start(candidate):
    """Belichtungsbeginn aus Zeitstempel und Zeitbezug; ältere Kandidaten ohne `time_reference` über die Belichtungsquelle."""
    reference=candidate.get('time_reference') or ('end' if (candidate.get('plausibility') or {}).get('exposure_source')=='Stellina EXPOSURE [ms]' else 'start')
    return float(candidate['timestamp'])-(float(candidate['exposure_seconds']) if reference=='end' else 0.)


def assess_motion(detection,meta,shape,world_path=None):
    """Screen measured segments before catalogues; pixels alone are not sky angles.

    No lower transverse speed is imposed: motion toward the observer can have
    arbitrarily small sky motion. Full exposure is only an upper visibility time.
    """
    height,width=shape[:2]
    exposure=exposure_info(meta)
    result=dict(version=1,status='not_checkable',exposure_seconds=exposure['seconds'],exposure_source=exposure['source'],exposure_status=exposure['status'],
                analysis_size=[width,height],original_size=[meta.get('NAXIS1'),meta.get('NAXIS2')],
                angular_length_deg=None,field_of_view_deg=None,angular_source=None,minimum_angular_speed_deg_s=None,
                touches_border=False,scenarios=[],excluded_classes=[],
                limitations='Belichtung ist nicht Leuchtdauer. Entfernung, Bewegungsrichtung, reale Geschwindigkeit und Objektgröße sind unbekannt. Vergleichsszenarien sind keine allgemeinen Klassengrenzen; kein automatischer Klassenausschluss.')
    points=np.asarray(detection.get('segment') or detection.get('path') or [],dtype=float)
    if points.ndim!=2 or points.shape[1:]!=(2,) or len(points)<2 or not np.isfinite(points).all(): return result
    if np.any(points<0) or np.any(points[:,0]>width) or np.any(points[:,1]>height):
        result['status']='invalid_geometry';return result
    result['touches_border']=bool(np.any(points[:,0]<width*.02) or np.any(points[:,0]>width*.98) or np.any(points[:,1]<height*.02) or np.any(points[:,1]>height*.98))
    focal=positive(meta.get('FOCAL'));pixel=positive(meta.get('PIXSZ'))
    native_width=positive(meta.get('NAXIS1'));native_height=positive(meta.get('NAXIS2'))
    if focal and pixel and native_width and native_height:
        sensor_width=native_width*pixel/1000;sensor_height=native_height*pixel/1000
        result['field_of_view_deg']=[math.degrees(2*math.atan(sensor_width/(2*focal))),math.degrees(2*math.atan(sensor_height/(2*focal)))]
        # Rays handle Bayer binning, preview resize, off-axis position and unequal axes.
        rays=np.column_stack([(points[:,0]/width-.5)*sensor_width,(points[:,1]/height-.5)*sensor_height,np.full(len(points),focal)])
        rays/=np.linalg.norm(rays,axis=1)[:,None]
        angular=np.degrees(np.arctan2(np.linalg.norm(np.cross(rays[:-1],rays[1:]),axis=1),np.sum(rays[:-1]*rays[1:],axis=1))).sum()
        result.update(angular_length_deg=float(angular),angular_source='FITS: FOCAL [mm], PIXSZ [µm], NAXIS1/2; Binning/Verkleinerung berücksichtigt')
    if world_path is not None and len(world_path)>1:
        from astropy.coordinates import SkyCoord
        from astropy import units as u
        world=np.asarray(world_path,dtype=float)
        if np.isfinite(world).all():
            coords=SkyCoord(world[:,0]*u.deg,world[:,1]*u.deg)
            result.update(angular_length_deg=float(coords[:-1].separation(coords[1:]).deg.sum()),angular_source='Plate Solving / WCS der gemessenen Spur')
    angular=result['angular_length_deg'];seconds=exposure['seconds']
    if angular is None or angular<=0 or seconds is None: return result
    omega=math.radians(angular)/seconds
    result.update(status='assessed',minimum_angular_speed_deg_s=math.degrees(omega))
    # Deliberately named comparison scenarios, NOT hard aircraft/meteor bounds.
    # Each uses transverse motion at a stated slant distance, not an inferred altitude.
    profiles=[('aircraft','Flugzeug: Vergleich bei 10 km Entfernung',10.,.25),
              ('satellite','Satellit: Vergleich bei 500 km Entfernung',500.,7.8),
              ('meteor','Meteor: Vergleich bei 100 km Entfernung',100.,30.)]
    for kind,label,distance,speed in profiles:
        angular_speed=speed/distance
        duration=math.radians(angular)/angular_speed
        result['scenarios'].append(dict(kind=kind,label=label,distance_km=distance,transverse_speed_km_s=speed,
            required_visibility_seconds=duration,compatible_with_exposure=duration<=seconds,
            minimum_transverse_speed_km_s=omega*distance,
            verdict='Mit Belichtung vereinbar; keine Identifikation' if duration<=seconds else 'Dieses Entfernungs-/Geschwindigkeitsszenario ist zu langsam; Klasse bleibt offen'))
    return result
