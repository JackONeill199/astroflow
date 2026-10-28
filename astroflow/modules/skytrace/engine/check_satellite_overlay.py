"""Physical camera projection, off-field trajectories and registration direction."""
import numpy as np
from astropy.wcs import WCS
from satellite_overlay import project_paths,intersects

w=WCS(naxis=2);w.wcs.crpix=[200,200];w.wcs.cdelt=[-.005,.005];w.wcs.crval=[359.9,10];w.wcs.ctype=['RA---TAN','DEC--TAN']
solution=dict(wcs=w,width=400,height=400)
native=np.array([[160,200],[240,200]],dtype=float)
world=w.all_pix2world(native,0).tolist()
outside=w.all_pix2world([[-200,-200],[600,-200]],0).tolist()
catalogs=[dict(provider='satellites',diagnostics=[dict(name='IN FRAME',world_path=world,compatible=False),dict(name='OUTSIDE',world_path=outside)])]
matrix=[[1,0,9],[0,1,-4]]
result=project_paths(solution,(200,200),matrix,catalogs)
assert result['status']=='projected'
inside,off=result['paths']
assert np.allclose(inside['reference_segments'][0],[[80,100],[120,100]])
assert np.allclose(inside['segments'][0],[[89,96],[129,96]])
assert inside['in_frame'] and not off['in_frame']
assert off['segments'][0][0][1]<0,'Off-field paths must not be shifted into the photograph'
assert intersects([[[-10,100],[210,100]]],200,200),'Both endpoints may be outside a crossing path'
assert not intersects([[[-10,-10],[210,-10]]],200,200)
assert not intersects([[[-10,0],[-10,100]]],200,200)
assert project_paths(None,(200,200),matrix,catalogs)['status']=='not_checkable'
assert project_paths(solution,(200,200),None,catalogs)['paths']==[]
print('OK: WCS sky paths project onto actual pixels, RA wrap/binning and reference transform are correct, off-field paths remain outside')
