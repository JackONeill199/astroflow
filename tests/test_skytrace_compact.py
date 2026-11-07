"""Kurze helle Spuren brauchen Form- und Differenzbeleg, keine pauschal abgesenkte Hough-Schwelle."""
from pathlib import Path
import sys
import numpy as np

if __name__!='__main__':
    import pytest
    pytest.importorskip('cv2')
import cv2
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'astroflow/modules/skytrace/engine'))
from line_detection import detect_lines


def field(seed):
    rng=np.random.default_rng(seed)
    image=rng.normal(1000,15,(360,480)).astype(np.float32)
    for x,y in rng.integers(25,330,(50,2)):
        cv2.circle(image,(int(x),int(y)),1,1800,-1)
    return cv2.GaussianBlur(image,(0,0),.7)


def test_short_bright_trail_needs_temporal_confirmation():
    base=field(22);image=base.copy();layer=np.zeros_like(image)
    cv2.line(layer,(190,200),(205,174),300,2)
    image+=cv2.GaussianBlur(layer,(0,0),.6)
    identity=np.eye(2,3)
    found=detect_lines(image,base,identity)
    compact=[d for d in found if d.get('line_threshold')==12]
    assert len(compact)==1,found
    assert compact[0]['kind']=='trail' and compact[0]['confidence'] is None
    assert np.linalg.norm(np.mean(compact[0]['segment'],axis=0)-[197.5,187])<5
    assert not detect_lines(image,image,identity)           # stationäre kurze Striche / Sensorfehler
    assert not detect_lines(image)                         # ohne Nachbarbild kein neuer kompakter Treffer


def test_noise_stars_and_registration_residuals_stay_empty():
    for seed in range(6):
        base=field(seed)
        matrix=np.array([[1.,0.,1.3],[0.,1.,-.8]])
        current=cv2.warpAffine(base,matrix,(base.shape[1],base.shape[0]),borderMode=cv2.BORDER_REFLECT)
        current+=np.random.default_rng(seed+90).normal(0,2,current.shape).astype(np.float32)
        assert not detect_lines(current,base,matrix),seed


def test_sequence_keeps_measured_neighbour_registration():
    """Gespeicherte Videogeometrie muss zum echten Nachbarbild passen, auch wenn Referenzlösungen springen."""
    import pytest
    pytest.importorskip("av")                      # sequence.py schreibt das Ereignisvideo mit PyAV (Engine-Paket)
    import contextlib
    import io
    import json
    import tempfile
    from types import SimpleNamespace
    from unittest.mock import patch
    from astropy.io import fits
    import sequence
    import observation_settings

    class JumpingReference:
        def __init__(self):self.matrix=np.eye(2,3);self.quality={}
        def update(self,image,index,*args):
            self.matrix=np.array([[1.,0.,20.*index],[0.,1.,0.]])
            self.quality=dict(accepted=True,inliers=30,matched=30,stars=30)
            return []

    class EmptyModel:
        def __init__(self,*args):pass
        def predict(self,*args,**kwargs):return [SimpleNamespace(boxes=[])]

    scratch=Path(__file__).resolve().parents[1]/'.pytest_cache';scratch.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=scratch) as temp:
        root=Path(temp);source=root/'source';source.mkdir();out=root/'output'
        image=field(13)
        for i in range(4):
            header=fits.Header();header['DATE-OBS']=f'2022-05-06T20:00:{i*10:02d}';header['EXPTIME']=10
            fits.writeto(source/f'img-{i}.fits',image,header)
        manifest=root/'eingang.json'
        manifest.write_text(json.dumps(dict(groups=[dict(session='Testbeobachtung',files=[str(source/f'img-{i}.fits') for i in range(3)],fps=6,source_first_file='00001_img-0.fits')])),encoding='utf-8')
        args=SimpleNamespace(source=str(source),output=str(out),fps=8,limit=0,solve=False,input_manifest=str(manifest))
        with patch.dict(sys.modules,{'ultralytics':SimpleNamespace(YOLO=EmptyModel)}),patch.object(sequence,'EnsembleMotion',JumpingReference),patch.object(observation_settings,'load_settings',return_value={'enabled':False,'sites':{}}),contextlib.redirect_stdout(io.StringIO()):
            sequence.run_sequences(args)
        summary=json.loads((out/'videos.json').read_text(encoding='utf-8'))[0]
        rows=[json.loads(s) for s in (out/Path(summary['video']).parent/'frames.jsonl').read_text(encoding='utf-8').splitlines()]
        assert len(rows)==3
        assert summary['session']=='Testbeobachtung' and summary['fps']==6
        assert summary['source_first_file']=='00001_img-0.fits'
        assert all(not row['file'].endswith('img-3.fits') for row in rows)  # neue Dateien im Ordner gehören nicht zum alten Lauf
        for row in rows[1:]:
            np.testing.assert_allclose(row['previous_to_current'],np.eye(2,3),atol=.05)
            assert not row['detections']


if __name__=='__main__':
    test_short_bright_trail_needs_temporal_confirmation()
    test_noise_stars_and_registration_residuals_stay_empty()
    test_sequence_keeps_measured_neighbour_registration()
    print('OK: kurze helle Spur erkannt, stationäre Striche, Sterne, Rauschen und Registrierungsreste verworfen')
