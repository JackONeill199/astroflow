"""Erneute Erkennung: vollständige Originalauswahl, Ersatzordner und unveränderter alter Lauf."""
import json
import datetime as dt
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from astroflow.core.config import load_settings
from astroflow.core.store import SettingsStore
from astroflow.server import create_app
from astroflow.modules.skytrace.api import Controller
from astroflow.modules.skytrace.reanalysis import prepare
from astropy.io import fits


@pytest.fixture(autouse=True)
def engine_path(monkeypatch):
    import astroflow.modules.skytrace.api as api
    monkeypatch.syspath_prepend(str(Path(api.__file__).parent/'engine'))


def fixture_run(tmp_path):
    home=tmp_path/'skytrace';run=home/'Ergebnisse'/'alter-lauf';obs=run/'obs';obs.mkdir(parents=True)
    original=tmp_path/'original';original.mkdir()
    for i,name in enumerate(('eins.fits','zwei.fits','drei.fits','neu.fits')):
        header=fits.Header();header['DATE-OBS']=dt.datetime.fromtimestamp(1650000000+i*10,dt.timezone.utc).isoformat()
        fits.PrimaryHDU(header=header).writeto(original/name)
    rows=[dict(index=i,file=str(original/name),timestamp=1650000000+i*10) for i,name in enumerate(('eins.fits','zwei.fits','drei.fits'))]
    (obs/'frames.jsonl').write_text('\n'.join(json.dumps(rows[i]) for i in (0,2)),encoding='utf-8')
    (obs/'ausgeschlossen.jsonl').write_text(json.dumps(rows[1]),encoding='utf-8')
    (run/'videos.json').write_text(json.dumps([dict(session='Beobachtung',video='obs/ereignisse.mp4',frames=2,excluded=1,fps=6)]),encoding='utf-8')
    return home,run,original


def test_reanalyse_starts_new_run_with_exact_originals(tmp_path,monkeypatch):
    monkeypatch.setenv('ASTROFLOW_MODULES','stacking,skytrace')
    home,run,original=fixture_run(tmp_path)
    saved=(run/'videos.json').read_bytes()
    settings=load_settings(tmp_path/'data');SettingsStore(settings.data_dir/'settings.json').update('skytrace',{'home':str(home)})
    client=TestClient(create_app(settings));launches=[]
    def launch(self,options,refresh=False,replay=None):
        launches.append((options,replay))
        return dict(run='neuer-lauf',running=True)
    monkeypatch.setattr(Controller,'launch',launch)
    check=client.post('/api/skytrace/reanalyse',json=dict(run=run.name,check=True))
    assert check.json()==dict(missing=0,total=3,observations=1) and not launches
    result=client.post('/api/skytrace/reanalyse',json=dict(run=run.name))
    assert result.status_code==200 and result.json()['run']=='neuer-lauf'
    options,plan=launches[0]
    assert options['samples']==0 and options['remember'] is False and not options.get('observation')
    assert plan['original_run']==run.name
    assert plan['groups']==[dict(session='Beobachtung',fps=6,source_first_file='eins.fits',files=[str((original/n).resolve()) for n in ('eins.fits','zwei.fits','drei.fits')])]
    assert (run/'videos.json').read_bytes()==saved
    (original/'zwei.fits').unlink()
    assert client.post('/api/skytrace/reanalyse',json=dict(run=run.name,check=True)).json()['missing']==1
    blocked=client.post('/api/skytrace/reanalyse',json=dict(run=run.name))
    assert blocked.status_code==400 and 'Originalaufnahmen fehlen' in blocked.json()['detail']
    assert len(launches)==1                           # fehlende ausgeschlossene Aufnahme darf nicht untergehen
    client.close()


def test_replacement_folder_requires_all_and_unambiguous_names(tmp_path):
    _,run,original=fixture_run(tmp_path)
    rows=[dict(index=i,file=str(tmp_path/'geloescht'/f'00001_{name}'),timestamp=1650000000+i*10) for i,name in zip((0,2),('eins.fits','drei.fits'))]
    (run/'obs/frames.jsonl').write_text('\n'.join(map(json.dumps,rows)),encoding='utf-8')
    plan=prepare(run,original)
    assert plan['missing']==0 and len(plan['groups'][0]['files'])==3  # Präfix entfernt, ausgeschlossene Originaldatei übernommen
    duplicate=original/'unterordner';duplicate.mkdir();(duplicate/'eins.fits').write_bytes(b'anderes original')
    with pytest.raises(ValueError,match='nicht eindeutig'):prepare(run,original)


def test_launch_passes_manifest_without_copying_originals(tmp_path,monkeypatch):
    # Controller direkt isoliert: keinen Engine-Prozess starten und keinen Reader-Thread öffnen.
    import astroflow.modules.skytrace.api as api
    import sys
    engine=Path(api.__file__).parent/'engine';monkeypatch.syspath_prepend(str(engine))
    home,run,original=fixture_run(tmp_path)
    mod=SimpleNamespace(home=home,python=sys.executable,engine_env=lambda:{})
    ctl=Controller(mod);plan=prepare(run);commands=[];saved=(original/'eins.fits').read_bytes()
    monkeypatch.setattr(api.subprocess,'Popen',lambda cmd,**kw:commands.append(cmd) or object())
    monkeypatch.setattr(api.threading,'Thread',lambda **kw:SimpleNamespace(start=lambda:None))
    ctl.launch(dict(source=str(original),samples=0,fps=6,mode=api.MODES[0],remember=False,solve=False),replay=plan)
    cmd=commands[0];manifest=Path(cmd[cmd.index('--input-manifest')+1])
    assert manifest.parent!=run and manifest.name=='eingang.json'
    assert json.loads(manifest.read_text(encoding='utf-8'))==plan
    assert (original/'eins.fits').read_bytes()==saved
    assert not ctl.prefs.exists()


def test_statistics_count_relocated_reanalysis_once(tmp_path):
    from astroflow.modules.skytrace.stats import collect
    home,run,original=fixture_run(tmp_path)
    # Ursprünglicher Auto-Lauf hat Präfixe, Ersatzoriginale nicht: trotzdem dieselbe Beobachtung.
    manifest=run/'obs/frames.jsonl'
    rows=[dict(index=i,file=str(tmp_path/'geloescht'/f'00001_{name}'),timestamp=1650000000+i*10) for i,name in zip((0,2),('eins.fits','drei.fits'))]
    manifest.write_text('\n'.join(map(json.dumps,rows)),encoding='utf-8')
    old=json.loads((run/'videos.json').read_text(encoding='utf-8'));old[0]['state']='Fertig'
    (run/'videos.json').write_text(json.dumps(old),encoding='utf-8')
    plan=prepare(run,original)
    new=home/'Ergebnisse'/'z-neu';(new/'obs').mkdir(parents=True)
    data={**old[0], 'source_first_file':plan['groups'][0]['source_first_file']}
    (new/'videos.json').write_text(json.dumps([data]),encoding='utf-8')
    (new/'obs/frames.jsonl').write_text(json.dumps(dict(file=str(original/'eins.fits'))),encoding='utf-8')
    stats=collect(home/'Ergebnisse')
    assert stats['repeated']==1 and len(stats['sessions'])==1 and stats['sessions'][0]['run']=='z-neu'


def test_same_filename_from_wrong_night_is_rejected(tmp_path):
    _,run,original=fixture_run(tmp_path)
    manifest=run/'obs/frames.jsonl'
    rows=[json.loads(s) for s in manifest.read_text(encoding='utf-8').splitlines()]
    rows[0]['file']=str(tmp_path/'geloescht'/'00001_eins.fits')
    manifest.write_text('\n'.join(map(json.dumps,rows)),encoding='utf-8')
    fits.setval(original/'eins.fits','DATE-OBS',value='2022-11-24T18:20:00')
    with pytest.raises(ValueError,match='passenden Nacht'):prepare(run,original)
