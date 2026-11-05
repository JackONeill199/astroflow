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
    assert prepare(run,original)['groups'][0]['files'][0]==str((original/'eins.fits').resolve())  # unlesbares Gleichnamiges stört nicht
    fits.setval(original/'eins.fits','DATE-OBS',value='2022-11-24T18:20:00')
    with pytest.raises(ValueError,match='gleichnamigen'):prepare(run,original)


def stellina_night(base,folder,stamp0,count=4):
    """Stellina-Beobachtung: 01-images-initial und 02-images-adjust-focus nummerieren beide ab img-0001r.fits."""
    out={}
    for sub,offset in (('01-images-initial',0),('02-images-adjust-focus',count*10)):
        (base/folder/sub).mkdir(parents=True)
        for i in range(count):
            header=fits.Header();header['DATE-OBS']=dt.datetime.fromtimestamp(stamp0+offset+i*10,dt.timezone.utc).isoformat()
            fits.PrimaryHDU(header=header).writeto(base/folder/sub/f'img-{i+1:04d}r.fits')
            out[stamp0+offset+i*10]=base/folder/sub/f'img-{i+1:04d}r.fits'
    return out


def prefixed_run(tmp_path,stamps):
    """Lauf aus einer automatischen Übergabe: Dateien mit Präfix in einem inzwischen gelöschten Ordner."""
    home=tmp_path/'skytrace';run=home/'Ergebnisse'/'auto-lauf';obs=run/'obs';obs.mkdir(parents=True)
    rows=[dict(index=i,file=str(tmp_path/'auto'/'2022-08-06_observation_X'/f'{i:05d}_img-{(i%4)+1:04d}r.fits'),timestamp=t) for i,t in enumerate(sorted(stamps))]
    (obs/'frames.jsonl').write_text('\n'.join(json.dumps(r) for r in rows[1:]),encoding='utf-8')
    (obs/'ausgeschlossen.jsonl').write_text(json.dumps(rows[0]),encoding='utf-8')
    (run/'videos.json').write_text(json.dumps([dict(session='2022-08-06_observation_X',video='obs/ereignisse.mp4',frames=len(rows)-1,excluded=1,fps=8)]),encoding='utf-8')
    return home,run


def test_stellina_subfolders_with_same_names_are_matched_by_date_obs(tmp_path,monkeypatch):
    monkeypatch.setenv('ASTROFLOW_MODULES','stacking,skytrace')
    stamps=stellina_night(tmp_path/'platte','2022-08-06_22-17-52_observation_X',1659824000)
    home,run=prefixed_run(tmp_path,stamps)
    night=tmp_path/'platte'/'2022-08-06_22-17-52_observation_X'
    plan=prepare(run,night)                                        # ganzer Beobachtungsordner
    assert plan['missing']==0 and plan['total']==8
    assert plan['groups'][0]['files']==[str(stamps[t].resolve()) for t in sorted(stamps)]
    with pytest.raises(ValueError,match='passenden Nacht'):prepare(run,night/'01-images-initial')   # Unterordner allein: img-0001r aus 02 wird nicht untergeschoben
    # Start nutzt dieselbe Zuordnung und dieselben Pfade im Manifest
    settings=load_settings(tmp_path/'data');SettingsStore(settings.data_dir/'settings.json').update('skytrace',{'home':str(home)})
    client=TestClient(create_app(settings));launches=[]
    monkeypatch.setattr(Controller,'launch',lambda self,options,refresh=False,replay=None:launches.append((options,replay)) or dict(run='neu',running=True))
    check=client.post('/api/skytrace/reanalyse',json=dict(run=run.name,source=str(night),check=True))
    assert check.status_code==200 and check.json()==dict(missing=0,total=8,observations=1)
    assert client.post('/api/skytrace/reanalyse',json=dict(run=run.name,source=str(night))).status_code==200
    assert launches[0][1]['groups'][0]['files']==plan['groups'][0]['files']
    client.close()


def test_whole_disk_with_several_nights_picks_by_time_and_reads_few_headers(tmp_path,monkeypatch):
    import astroflow.modules.skytrace.reanalysis as re_
    disk=tmp_path/'platte'
    other=stellina_night(disk,'2022-07-01_21-00-00_observation_X',1656700000)    # andere Nacht, gleiche Namen
    stamps=stellina_night(disk,'2022-08-06_22-17-52_observation_X',1659824000)
    stellina_night(disk,'2022-09-01_21-00-00_observation_Y',1662000000)
    _,run=prefixed_run(tmp_path,stamps)
    reads=[];real=re_.date_obs
    monkeypatch.setattr(re_,'date_obs',lambda p:reads.append(p) or real(p))
    plan=prepare(run,disk)
    assert plan['missing']==0 and plan['groups'][0]['files']==[str(stamps[t].resolve()) for t in sorted(stamps)]
    assert len(reads)<=len(stamps)+4                                # Datum im Ordnernamen + Ordner früherer Treffer: kaum Fehlgriffe
    assert not set(map(str,other.values()))&set(plan['groups'][0]['files'])
    # Nacht nicht vorhanden: klare Meldung statt Namenszuordnung
    import shutil;shutil.rmtree(disk/'2022-08-06_22-17-52_observation_X')
    with pytest.raises(ValueError,match='gleichnamigen.*gehört keine'):prepare(run,disk)


def test_header_reader_is_fast_and_robust(tmp_path):
    from astroflow.modules.skytrace.reanalysis import date_obs
    f=tmp_path/'a.fits';header=fits.Header();header['DATE-OBS']='2022-08-06T22:26:25.5Z'
    fits.PrimaryHDU(header=header).writeto(f)
    assert date_obs(f)==dt.datetime(2022,8,6,22,26,25,500000,dt.timezone.utc).timestamp()
    (tmp_path/'b.fits').write_bytes(b'kein fits');assert date_obs(tmp_path/'b.fits') is None
    assert date_obs(tmp_path/'fehlt.fits') is None


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
