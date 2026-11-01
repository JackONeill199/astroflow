"""Satellitenabgleich bestehender Läufe neu rechnen (Belichtungsende bei Stellina, Uhrtoleranz, Namen).

Nur Kandidatendaten, lokale Bahnarchive (bzw. ETH-Abruf ab 03/2023 wie im Lauf) und der Namenskatalog –
keine Originalbilder nötig, keine JPL-/Flugabfragen. Aufruf: refresh_satellites.py <Ergebnisordner> [...]"""
import argparse
from pathlib import Path
from candidate_report import _wcs
from catalog_matching import satellite_check,status
from opensky_archive import ArchiveAccessError
from observation_settings import load_settings
from satellite_overlay import project_paths
from sequence import report
from json_store import read_json,write_json


def refresh(root):
    root=Path(root).resolve();summaries=read_json(root/'videos.json',[]);settings=load_settings()
    settings.update(tle_file='',tle_url='')
    found=0
    for summary in summaries:
        if not summary.get('candidates_file'):continue
        folder=root/Path(summary['video']).parent;path=root/summary['candidates_file']
        items=read_json(path,[]);solution=_wcs(folder)
        for item in items:
            if not item.get('world_path') or not item.get('site'):continue
            try:sat=satellite_check(item,item['site'],settings)
            except ArchiveAccessError as error:sat=status('satellites',error.status,str(error))
            except ValueError as error:sat=status('satellites','not_checkable',str(error))
            catalogs=[r for r in item.get('catalogs',[]) if r.get('provider')!='satellites']
            item['catalogs']=catalogs[:1]+[sat]+catalogs[1:]
            context=item.get('context_image')
            if context and context.get('reference_to_current') is not None:
                item['satellite_overlay']=project_paths(solution,(context['width'],context['height']),context['reference_to_current'],item['catalogs'])
            target=folder/'kandidaten'/item['id']/'kandidat.json'
            if target.parent.exists():write_json(target,item)
            best=sat['matches'][0] if sat.get('matches') else None
            found+=bool(best)
            print(item['id'],sat['status'],(best['name'] if best else ''),flush=True)
        write_json(path,items)
    report(root,summaries)
    print(f'{root.name}: {found} Spuren einer Satellitenbahn zugeordnet.')


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('roots',type=Path,nargs='+')
    for root in parser.parse_args().roots:refresh(root)
