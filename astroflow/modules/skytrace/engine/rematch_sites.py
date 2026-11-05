"""Bestehende Läufe nach einer Standortänderung neu prüfen – ohne Rohbilder, nur Kandidatendaten + lokale Bahnarchive.

  rematch   Ort je Kandidat neu auflösen (Ausnahme → FITS → Ortsliste der Nacht → Standard; Rohbild weg → Ortsliste)
            und die Satelliten wie `refresh_satellites.py` neu abgleichen. Ohne `--force` nur Kandidaten, deren Ort sich
            geändert hat. Kandidaten, Overlay, `videos.json` (`site`) und Bericht werden aktualisiert.
  search    Standortsuche für eine Beobachtung mit < 3 sicheren Treffern: Abgleich mit erweiterter Toleranz (Ort ±
            SEARCH_RADIUS_KM als Vorinformation), Rückrechnung (`sitefit`) aus den eindeutigen Zuordnungen, danach strenger
            Abgleich am Vorschlag und – mit dessen Treffern – eine zweite Rückrechnung. Schreibt nur `standortsuche.json`;
            übernommen wird der Ort erst, wenn der Nutzer bestätigt (Oberfläche).

Netz: keine Archivabfragen (`orbits_cache_only`: Space-Track-Spiegel nur lokal, ETH nur aus dem Minuten-Cache des
ursprünglichen Laufs). SATCAT-Namen: höchstens ein Aufruf mit Netz je Auftrag (danach `satcat_offline`) – Einzelabfragen
je Nummer führten am 01.11.2026 zur CelesTrak-Sperre. Ausgabe: JSON-Zeilen (`progress`, `status`, `result`)."""
import argparse
import importlib
import json
import math
import sys
import time
import types
from pathlib import Path

from json_store import read_json, write_json
from observation_settings import load_settings, resolve_site
from catalog_matching import satellite_check, status
from opensky_archive import ArchiveAccessError
from project_paths import BASE, CODE

SEARCH_RADIUS_KM = 10.0           # Vorinformation der Suche: Ort liegt höchstens so weit daneben
SEARCH_CAP_ARCSEC = 7200          # Quer-Toleranz der Suche höchstens 2° (normal 30′)
UNCHECKABLE = ('needs_archive', 'service_unavailable', 'not_checkable', 'error')


def emit(kind, **row):
    print(json.dumps(dict(type=kind, **row), ensure_ascii=False, allow_nan=False, default=str), flush=True)


def sitefit_module():
    """`astroflow/modules/skytrace/sitefit.py` (relativer Import von orbit3d) ohne das AstroFlow-Paket laden."""
    name = 'skytrace_server'
    if name not in sys.modules:
        pkg = types.ModuleType(name); pkg.__path__ = [str(CODE.parent)]
        sys.modules[name] = pkg
    return importlib.import_module(name + '.sitefit')


def site_key(site):
    if not site or site.get('lat') is None: return None
    return (round(float(site['lat']), 6), round(float(site['lon']), 6), round(float(site.get('alt_m') or 0)), round(float(site.get('accuracy_m') or 0)))


def sat_of(item):
    return next((r for r in item.get('catalogs') or [] if r.get('provider') == 'satellites'), None)


def matched(item):
    s = sat_of(item)
    return bool(s and s.get('status') == 'compatible' and s.get('matches'))


def check(item, site, settings):
    try: return satellite_check(item, site, settings)
    except ArchiveAccessError as error: return status('satellites', error.status, str(error))
    except ValueError as error: return status('satellites', 'not_checkable', str(error))
    finally: settings['satcat_offline'] = True       # Namen: nur der erste Abgleich des Auftrags darf ins Netz


def site_for(item, summary, settings):
    when = item.get('timestamp') if isinstance(item.get('timestamp'), (int, float)) else None
    return resolve_site(item.get('source_file') or '', item.get('session') or summary.get('session'), settings, when=when)


def offline_settings():
    settings = load_settings()
    settings.update(tle_file='', tle_url='', orbits_cache_only=True)
    return settings


def _sessions(root):
    summaries = read_json(root / 'videos.json', []) or []
    for summary in summaries:
        if summary.get('candidates_file') and summary.get('video'):
            yield summaries, summary, root / Path(summary['video']).parent, root / summary['candidates_file']


# ------------------------------------------------------------------ Neu abgleichen ---
def rematch(roots, force=False, stop_file=None):
    settings = offline_settings()
    if stop_file: settings['_stop_file'] = str(stop_file)
    plan = []
    for root in roots:
        root = Path(root).resolve()
        for summaries, summary, folder, path in _sessions(root):
            items = read_json(path, []) or []
            plan.append((root, summaries, summary, folder, path, items))
    total = sum(1 for p in plan for item in p[5] if item.get('world_path')) or 0
    done, report = 0, {}
    stopped = False
    for root, summaries, summary, folder, path, items in plan:
        r = report.setdefault(root.name, dict(run=root.name, candidates=0, checked=0, unchanged=0, missing_orbits=0,
                                              before=0, after=0, sites=[], changed=False))
        solution, changed = False, False
        for item in items:
            if not item.get('world_path'): continue
            if stop_file and Path(stop_file).exists(): stopped = True; break
            done += 1
            r['candidates'] += 1
            had = matched(item)
            r['before'] += had
            emit('progress', current=done, total=total, text=f'{summary.get("session")}: {item.get("id")} · Standort und Satelliten prüfen ({done}/{total})')
            try: site = site_for(item, summary, settings)
            except (OSError, ValueError) as error:
                emit('status', text=f'{item.get("id")}: Standort nicht bestimmbar ({error})'); r['after'] += had; continue
            if not force and site_key(site) == site_key(item.get('site')):
                r['unchanged'] += 1; r['after'] += had; continue
            try: sat = check(item, site, settings)
            except InterruptedError:
                # Anhalten mitten im Abgleich: Kandidat bleibt wie er war, bereits fertige werden unten gespeichert
                r['after'] += had; stopped = True; break
            if sat.get('status') in UNCHECKABLE and sat_of(item):
                # Bahndaten fehlen lokal: alten Abgleich (und damit den alten Ort) behalten statt ihn zu verlieren
                r['missing_orbits'] += 1; r['after'] += had; continue
            item['site'] = site
            catalogs = [c for c in item.get('catalogs', []) if c.get('provider') != 'satellites']
            item['catalogs'] = catalogs[:1] + [sat] + catalogs[1:]
            context = item.get('context_image')
            if context and context.get('reference_to_current') is not None:
                if solution is False:
                    from candidate_report import _wcs
                    solution = _wcs(folder)
                from satellite_overlay import project_paths
                item['satellite_overlay'] = project_paths(solution, (context['width'], context['height']), context['reference_to_current'], item['catalogs'])
            target = folder / 'kandidaten' / str(item['id']) / 'kandidat.json'
            if target.parent.exists(): write_json(target, item)
            r['checked'] += 1; r['after'] += matched(item); changed = True
            if site: summary['site'] = {k: site.get(k) for k in ('name', 'site_id', 'source', 'origin', 'accuracy_m')}
        if changed:
            write_json(path, items); r['changed'] = True
        if summary.get('site') and summary['site'].get('name') not in r['sites']: r['sites'].append(summary['site'].get('name'))
        if stopped: break
    for root in {p[0] for p in plan}:
        r = report.get(root.name)
        if r and r['changed']:
            summaries = next(p[1] for p in plan if p[0] == root)
            write_json(root / 'videos.json', summaries)
            try:
                from sequence import report as render
                render(root, summaries)
            except Exception as error:                  # Kandidaten sind gespeichert; nur der HTML-Bericht bleibt alt
                emit('status', text=f'{root.name}: Bericht nicht neu erzeugt ({error.__class__.__name__}: {error})')
    emit('result', runs=list(report.values()), stopped=stopped,
         text=('Angehalten. ' if stopped else '') + f'{sum(r["checked"] for r in report.values())} Spuren neu abgeglichen, '
              f'{sum(r["after"] for r in report.values())} mit Satellitentreffer (vorher {sum(r["before"] for r in report.values())}).')


# ------------------------------------------------------------------ Standortsuche ---
def _fit(sitefit, folder, pairs, site):
    cache = BASE / 'catalog_cache' / 'archives'
    obs, notes = sitefit.observations(folder, pairs, cache)
    res = sitefit.fit(obs, {'lat': float(site['lat']), 'lon': float(site['lon']), 'alt_m': float(site.get('alt_m') or 0)})
    res['notes'] = notes
    return res


def search(root, folder_name, radius_km=SEARCH_RADIUS_KM, stop_file=None):
    sitefit = sitefit_module()
    root = Path(root).resolve()
    hit = next(((s, f) for _, s, f, _ in _sessions(root) if f.name == folder_name), None)
    if not hit: raise ValueError('Beobachtung nicht gefunden')
    summary, folder = hit
    items = [i for i in read_json(folder / 'kandidaten.json', []) or [] if i.get('world_path') and i.get('timestamp') is not None]
    settings = offline_settings(); settings['satcat_offline'] = True
    if stop_file: settings['_stop_file'] = str(stop_file)
    out = dict(ok=False, run=root.name, folder=folder_name, session=summary.get('session'), radius_km=radius_km, created=time.time(),
               tracks=len(items), before=sum(matched(i) for i in items))
    site = next((s for s in (site_for(i, summary, settings) for i in items[:1]) if s), None)
    if not items: out['reason'] = 'Diese Beobachtung hat keine Spuren mit Himmelskoordinaten und Zeit.'
    elif not site: out['reason'] = 'Für diese Nacht ist kein Standort bekannt – bitte unter Einstellungen → Standorte einen Ort hinterlegen.'
    if not out.get('reason'):
        out['site'] = {k: site.get(k) for k in ('name', 'site_id', 'source', 'origin', 'accuracy_m')}
        out['night'] = site.get('night') or sitefit_night(summary)
        wide = dict(site, accuracy_m=radius_km * 1000 / 2)            # 2σ-Toleranz = Suchradius
        wsettings = dict(settings, site_tolerance_cap_arcsec=SEARCH_CAP_ARCSEC)
        n = len(items)
        pairs = []
        for k, item in enumerate(items):
            emit('progress', current=k + 1, total=2 * n, text=f'Suche mit ± {radius_km:g} km: {item.get("id")} ({k + 1}/{n})')
            pairs.append((item, check(item, wide, wsettings)))
        out['wide'] = sum(1 for _, s in pairs if s.get('status') == 'compatible')
        out['unchecked'] = sum(1 for _, s in pairs if s.get('status') in UNCHECKABLE)
        first = _fit(sitefit, folder, pairs, site)
        out['search_fit'] = {k: first.get(k) for k in ('ok', 'reason', 'trails', 'shift_km', 'rms_before_arcsec', 'rms_after_arcsec', 'dropped')}
        if not first.get('ok'):
            out['reason'] = (first.get('reason') or 'Rückrechnung nicht möglich.') + (f' Mit ± {radius_km:g} km passten {out["wide"]} von {n} Spuren zu einer Bahn'
                             + (' – nicht eindeutig genug (z. B. Starlink-Züge).' if out['wide'] >= 3 else '.'))
            if out['unchecked']: out['reason'] += f' {out["unchecked"]} Spuren nicht prüfbar (Bahndaten fehlen lokal).'
        else:
            proposal = dict(site, lat=first['lat'], lon=first['lon'], accuracy_m=first['accuracy_m'], origin='search', source='Standortsuche')
            strict = []
            for k, item in enumerate(items):
                emit('progress', current=n + k + 1, total=2 * n, text=f'Strenger Abgleich am Vorschlag: {item.get("id")} ({k + 1}/{n})')
                strict.append((item, check(item, proposal, settings)))
            out['after'] = sum(1 for _, s in strict if s.get('status') == 'compatible')
            final = _fit(sitefit, folder, strict, site)
            res = final if final.get('ok') and final['trails'] >= first['trails'] else first
            out.update({k: v for k, v in res.items() if k not in ('notes',)}, ok=True, notes=res.get('notes', []),
                       stage='strict' if res is final else 'search')
            out['matches'] = [dict(id=i.get('id'), name=(s.get('matches') or [{}])[0].get('name')) for i, s in strict if s.get('status') == 'compatible']
            # Gültig nur, wenn der strenge Abgleich am Vorschlag tatsächlich mehr Treffer bringt als der bisherige Ort
            out['worth'] = bool(res.get('worth') and out['after'] > out['before'])
    write_json(folder / 'standortsuche.json', out)
    emit('result', search=out, text=('Standortsuche: ' + (f'{out["after"]} von {out["tracks"]} Spuren mit dem Vorschlag zugeordnet (vorher {out["before"]}).' if out['ok'] else out.get('reason', ''))))


def sitefit_night(summary):
    import re
    m = re.search(r'(\d{4})-?(\d{2})-?(\d{2})', str(summary.get('session') or ''))
    return '-'.join(m.groups()) if m else None


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest='cmd', required=True)
    a = sub.add_parser('rematch'); a.add_argument('roots', type=Path, nargs='+'); a.add_argument('--force', action='store_true'); a.add_argument('--stop-file', type=Path)
    b = sub.add_parser('search'); b.add_argument('root', type=Path); b.add_argument('folder'); b.add_argument('--radius-km', type=float, default=SEARCH_RADIUS_KM); b.add_argument('--stop-file', type=Path)
    args = parser.parse_args()
    try:
        if args.cmd == 'rematch': rematch(args.roots, args.force, args.stop_file)
        else: search(args.root, args.folder, args.radius_km, args.stop_file)
    except InterruptedError:
        emit('result', stopped=True, text='Angehalten.')
    except (OSError, ValueError) as error:
        emit('error', text=str(error)); sys.exit(1)
