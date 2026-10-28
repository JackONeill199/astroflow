"""Project saved topocentric satellite diagnostics onto the actual camera field."""
import numpy as np


def intersects(segments,width,height):
    """Liang–Barsky clipping, including crossings with both endpoints outside."""
    for points in segments:
        for a,b in zip(points,points[1:]):
            dx,dy=b[0]-a[0],b[1]-a[1];low,high=0.,1.
            for p,q in ((-dx,a[0]),(dx,width-a[0]),(-dy,a[1]),(dy,height-a[1])):
                if p==0:
                    if q<0:break
                elif p<0:low=max(low,q/p)
                else:high=min(high,q/p)
                if low>high:break
            else:return True
    return False


def transform_segments(segments,matrix):
    return [[[float(matrix[0][0]*x+matrix[0][1]*y+matrix[0][2]),float(matrix[1][0]*x+matrix[1][1]*y+matrix[1][2])] for x,y in points] for points in segments]


def project_paths(solution,size,matrix,catalogs):
    diagnostics=next((r.get('diagnostics',[]) for r in catalogs if r.get('provider')=='satellites'),[])
    result=dict(status='not_checkable',paths=[],width=size[0],height=size[1],reason='Keine gespeicherten Satellitenbahnen vorhanden.')
    if not diagnostics:return result
    if not solution or matrix is None:
        result['reason']='WCS oder zuverlässige Sternregistrierung fehlt; keine verlässliche Bildprojektion.';return result
    result.update(status='projected',reason='Gestrichelte Bahnen sind Prognosen für die Belichtung des Trefferbildes, keine erkannten Objekte.')
    for diagnostic in diagnostics:
        world=diagnostic.get('world_path') or []
        if len(world)<2:continue
        try:
            pixels=np.asarray(solution['wcs'].all_world2pix(np.asarray(world,dtype=float),0),dtype=float)
            pixels*=np.array([size[0]/solution['width'],size[1]/solution['height']])
        except (ValueError,TypeError):continue
        segments=[];part=[]
        for point in pixels:
            if np.isfinite(point).all():part.append(point.tolist())
            else:
                if len(part)>1:segments.append(part)
                part=[]
        if len(part)>1:segments.append(part)
        anchor=transform_segments(segments,matrix)
        result['paths'].append(dict(name=diagnostic.get('name') or 'NORAD '+str(diagnostic.get('norad_id','?')),
            norad_id=diagnostic.get('norad_id'),reference_segments=segments,segments=anchor,in_frame=intersects(anchor,*size),
            compatible=bool(diagnostic.get('compatible')),separation_arcsec=diagnostic.get('separation_arcsec')))
    return result
