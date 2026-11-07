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
from catalog_matching import satellite_check, status, clock_offset
from archive_errors import ArchiveAccessError
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
            clock_changed = abs(clock_offset(item, settings) - float((sat_of(item) or {}).get('clock_offset_s') or 0)) > 0.05
            if not force and site_key(site) == site_key(item.get('site')) and not clock_changed:
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


# ------------------------------------------------------------- Standorte aller Nächte ---
# Je Nacht alle Beobachtungen gemeinsam (ein Ort, ein Uhrversatz): erst jeden Ort der Ortsliste streng prüfen, dann – wenn kein
# Ort gut passt – mit Spielraum um den besten suchen und aus den eindeutigen Zuordnungen der ganzen Nacht zurückrechnen.
GOOD_RMS_ARCSEC = 60           # passt ein Ort mit eindeutigen Treffern quer im Mittel besser, ist keine Rückrechnung nötig
SAME_SITE_M = 300              # Rückrechnung so nah an einem Listenort → diesen Ort vorschlagen statt eines neuen
MAX_CENTERS = 3                # höchstens so viele Suchmittelpunkte, wenn kein Ort einen Treffer hat
ALT_NEAR_M, ALT_SIGMA = 2000, 5  # Listenorte so nah am Vorschlag (bzw. ≤ 5σ) werden für die Alternative streng nachgeprüft (wie rematch.alternatives)


def _km(a, b):
    return math.hypot((a['lat'] - b['lat']) * 111.195, (a['lon'] - b['lon']) * 111.195 * math.cos(math.radians(b['lat'])))


def site_list(settings):
    """Orte der gemeinsamen Ortsliste (data/settings.json → sites.list)."""
    from observation_settings import shared_sites_file
    path = shared_sites_file(settings)
    data = read_json(path, {}) if path else {}
    return [s for s in ((data or {}).get('sites') or {}).get('list') or [] if s.get('lat') is not None and s.get('lon') is not None]


def _score(sitefit, triples):
    """Eindeutige Treffer (wie sitefit._sure – Starlink-Züge zählen nicht) und quer-RMS ihrer besten Bahnen."""
    sure = [sitefit._sure(s or {}) for _, _, s in triples]
    off = [m.get('mean_offset_arcsec') or m.get('separation_arcsec') or 0 for m in sure if m]
    return dict(sure=len(off), compatible=sum(1 for *_, s in triples if (s or {}).get('status') == 'compatible'),
                rms=round(math.sqrt(sum(o * o for o in off) / len(off)), 1) if off else None)


def _night_fit(sitefit, triples, ref):
    """Rückrechnung aus allen Beobachtungen der Nacht (Spuren ganz im Bild je Ordner bestimmt)."""
    cache = BASE / 'catalog_cache' / 'archives'
    obs, notes = [], []
    for folder in {id(f): f for f, _, _ in triples}.values():
        o, n = sitefit.observations(folder, [(i, s) for f, i, s in triples if f is folder], cache)
        obs += o; notes += n
    res = sitefit.fit(obs, {'lat': float(ref['lat']), 'lon': float(ref['lon']), 'alt_m': float(ref.get('alt_m') or 0)})
    res['notes'] = notes
    return res


def _brief(site):
    return dict(id=site.get('site_id') or site.get('id'), name=site.get('name'), origin=site.get('origin'), accuracy_m=site.get('accuracy_m')) if site else None


def needs_clock(sitefit, entries):
    """Uhrversatz suchen, wenn ab 3 Spuren weniger als die Hälfte eindeutig zugeordnet ist (Suche kostet ~20 Abgleiche je Spur)."""
    sure = sum(1 for _, i in entries if sitefit._sure(sat_of(i) or {}))
    return len(entries) >= sitefit.MIN_TRAILS and sure < 0.5 * len(entries)


def needs_global(tracks, top):
    """Ortssuche ohne Vorwissen nur, wenn kein Ort (Liste, Rückrechnung) mindestens 3 und die Hälfte der Spuren eindeutig zuordnet –
    am richtigen Ort passt die Mehrheit, normale Nächte werden so nicht langsamer."""
    import global_site
    return tracks >= global_site.MIN_TRAILS and top < max(global_site.MIN_TRAILS, 0.5 * tracks)


def _global(sitefit, out, entries, settings, sites, current, global_km, clock, tick, top):
    """Ort ohne Vorwissen (`global_site`) → Vorschlag `global` (bzw. `list`, wenn ein Listenort ≤ 2 km daneben liegt); ersetzt einen
    schwächeren Vorschlag und – falls der neue Ort eine andere Uhr ergibt – auch den Uhrversatz vom bisherigen Ort."""
    import global_site, clock_scan
    from catalog_matching import night_key
    centers = [dict(lat=float(s['lat']), lon=float(s['lon'])) for s in sites]
    if current and current.get('lat') is not None and not any(_km(current, c) < 1 for c in centers):
        centers.append(dict(lat=float(current['lat']), lon=float(current['lon'])))
    res = global_site.search_night(sitefit, entries, settings, centers, region_km=global_km, clock=clock, tick=tick, before_sure=top)
    save_heat(out['night'], res)
    res.pop('_triples', None); res.pop('_clusters', None); res.pop('_heat', None)   # Heatmap nur in der eigenen Datei
    out['global'] = res
    p = res.get('proposal')
    if not p:
        return False
    base = clock_offset(entries[0][1], settings)
    near = min(sites, key=lambda s: _km(s, p), default=None)
    near_km = _km(near, p) if near else None
    out['global'].update(nearest_name=near.get('name') if near else None, nearest_km=round(near_km, 1) if near else None)
    if near and near_km <= max(global_site.NEAR_LIST_KM, 2 * p['accuracy_m'] / 1000):
        out['proposal'] = dict(kind='list', id=near.get('id'), name=near.get('name'), sure=p['sure'], rms=p['rms'], source='global',
                               **{k: p.get(k) for k in ('clock_s', 'clock_sigma_s', 'timed', 'trails')})
    else:
        out['proposal'] = dict(kind='global', lat=round(p['lat'], 6), lon=round(p['lon'], 6), alt_m=0, accuracy_m=round(p['accuracy_m']),
                               sure=p['sure'], rms=p['rms'], trails=p.get('trails'), timed=p.get('timed'), clock_s=round(p['clock_s'], 1),
                               clock_sigma_s=p.get('clock_sigma_s'), ellipse_km=p.get('ellipse_km'), support=p['support'],
                               confidence=res.get('confidence'), region_km=global_km, from_name='Unbekannter Ort',
                               nearest_name=out['global']['nearest_name'], nearest_km=out['global']['nearest_km'],
                               shift_from_current_km=round(_km(p, current), 1) if current else None)
    nights = sorted({night_key(i['timestamp']) for _, i in entries if isinstance(i.get('timestamp'), (int, float))})
    old = (out.get('clock') or {}).get('proposal')
    if abs(p['clock_s']) >= clock_scan.MIN_SHIFT_S:
        out['clock'] = dict(source='global', proposal=dict(offset_s=round(base + p['clock_s'], 1), sigma_s=max(1.0, float(p.get('clock_sigma_s') or 1)),
                                                           support=p['support'], sure=p['sure'], before=top, confidence=res.get('confidence'),
                                                           chance=(res['stages'][-1]['top'] or [{}])[0].get('chance'), nights=nights, base_s=round(base, 1)))
    elif old:
        out['clock'] = dict(source='global', proposal=None, dropped=old,
                            reason=f'Am zurückgerechneten Ort geht die Uhr richtig ({p["clock_s"]:+.0f} s) – der am bisherigen Ort gefundene '
                                   f'Versatz {old["offset_s"]:+.0f} s war eine Folge des falschen Orts.')
    return True


def night(sitefit, group, entries, settings, radius_km, tick, clock=True, global_km=0.0):
    """Eine Nacht: Vorschlag (bestehender Ort `list`, zurückgerechneter Ort `fit`, Ort ohne Vorwissen `global`) oder ehrlicher Grund.
    Mit `clock` zuerst – bei wenigen Treffern – der Uhrversatz (`clock_scan`); ein gefundener Versatz gilt für die Ortsprüfung danach.
    Mit `global_km` > 0 und ohne gut passenden Ort zuletzt die Suche ohne Vorwissen im Umkreis `global_km` um die Listenorte."""
    from observation_settings import sites_module
    plain = settings                                            # ohne Uhrkorrektur vom bisherigen Ort (die globale Suche hat ihre eigene)
    out = dict(night=group['night'], observations=group['observations'], tracks=len(entries), created=time.time(),
               before=sum(matched(i) for _, i in entries), proposal=None)
    mod = sites_module()
    out['assign'] = sorted({mod.night_of(i['timestamp']) for _, i in entries if mod and isinstance(i.get('timestamp'), (int, float))} - {None}) or [group['night']]
    if not entries:
        return dict(out, status='none', note='Keine Spuren mit Himmelskoordinaten und Zeit in dieser Nacht.')
    folder0, item0 = entries[0]
    current = site_for(item0, {'session': item0.get('session')}, settings)
    out['current'] = _brief(current)
    fits_site = bool(current and current.get('origin') == 'fits')
    # Gespeicherte Abgleiche gelten nur, wenn sie am jetzt aufgelösten Ort gerechnet wurden (Ortsliste kann sich geändert haben)
    stored_triples = [(f, i, sat_of(i) if site_key(i.get('site')) == site_key(current) else None) for f, i in entries]
    stored = _score(sitefit, stored_triples)
    out['current'].update(sure=stored['sure'], rms=stored['rms']) if out['current'] else None
    if clock and current and needs_clock(sitefit, entries):
        import clock_scan
        res = clock_scan.scan_night(sitefit, entries, current, settings, tick=tick, before_sure=stored['sure'])
        strict_c = res.pop('_strict', None)
        out['clock'] = res
        if res.get('proposal'):
            # Ortsprüfung mit korrigierter Uhr (nur in diesem Auftrag; übernommen wird erst auf Knopfdruck)
            settings = dict(settings, clock_offsets=dict(settings.get('clock_offsets') or {},
                                                         **{n: res['proposal']['offset_s'] for n in res['proposal']['nights']}))
            stored_triples = strict_c
            stored = _score(sitefit, stored_triples)
    if fits_site:
        # FITS-Ort hat Vorrang vor der Ortsliste (keine Ortsänderung) – eine falsche Kamera-Uhr lässt sich trotzdem korrigieren
        if (out.get('clock') or {}).get('proposal'):
            return dict(out, status='better', note='Ort aus den FITS-Daten (bleibt); nur die Uhr ist falsch gestellt.')
        return dict(out, status='fits', note='Der Ort steht in den FITS-Daten und hat Vorrang vor der Ortsliste – nichts zu ändern.')

    def strict(site):
        res = []
        for f, i in entries:
            tick(f'{group["night"]}: {i.get("id")} am Ort „{site.get("name") or "Vorschlag"}“ prüfen')
            res.append((f, i, check(i, site, settings)))
        return res

    sites = site_list(settings)
    tested = []
    if stored['sure'] >= sitefit.MIN_TRAILS:
        # Genug eindeutige Treffer am bisherigen Ort: nur verfeinern (wie „Standort der Nacht prüfen“)
        base = dict(current, **stored) if current else None
        candidates = [(base, stored_triples)] if base else []
    else:
        candidates = []
        for s in sites:
            ref = dict(s, accuracy_m=float(s.get('accuracy_m') or 100), site_id=s.get('id'))
            triples = strict(ref)
            sc = _score(sitefit, triples)
            tested.append(dict(id=s.get('id'), name=s.get('name'), **sc))
            candidates.append((dict(ref, **sc), triples))
        if current and current.get('site_id') not in {s.get('id') for s in sites}:      # Ausnahme/alter SkyTrace-Ort
            triples = strict(current); sc = _score(sitefit, triples)
            tested.append(dict(id=None, name=current.get('name'), **sc)); candidates.append((dict(current, **sc), triples))
    out['tested'] = tested
    rank = lambda c: (c[0]['sure'], -(c[0]['rms'] if c[0]['rms'] is not None else 1e9))
    candidates.sort(key=rank, reverse=True)
    best, best_triples = candidates[0] if candidates else (None, [])
    if not best:
        return dict(out, status='none', note='Für diese Nacht ist kein Standort bekannt – bitte unter Einstellungen → Standorte einen Ort anlegen.')
    out['best'] = dict(_brief(best), sure=best['sure'], rms=best['rms'])
    good = best['sure'] >= 1 and best['rms'] is not None and best['rms'] <= GOOD_RMS_ARCSEC
    fit, fit_triples = None, None
    if not good:
        # Mit Spielraum suchen: um den besten Ort, ohne jeden Treffer um den bisherigen und die nächsten Listenorte
        if best['sure'] >= sitefit.MIN_TRAILS:
            fit = _night_fit(sitefit, best_triples, best)
        others = sorted((c for c, _ in candidates if c is not best), key=lambda c: _km(c, current) if current else 0)
        centers = [best] if best['sure'] else ([dict(current, sure=0, rms=None)] if current else [best]) + others
        seen = []
        for center in centers:
            if fit and fit.get('ok') or len(seen) >= MAX_CENTERS: break
            if any(_km(center, s) < radius_km / 2 for s in seen): continue
            seen.append(center)
            wide = dict(center, accuracy_m=radius_km * 1000 / 2)
            wsettings = dict(settings, site_tolerance_cap_arcsec=SEARCH_CAP_ARCSEC)
            wide_triples = []
            for f, i in entries:
                tick(f'{group["night"]}: {i.get("id")} mit ± {radius_km:g} km um „{center.get("name") or "Ort"}“ suchen')
                wide_triples.append((f, i, check(i, wide, wsettings)))
            out['wide'] = max(out.get('wide', 0), sum(1 for *_, s in wide_triples if s.get('status') == 'compatible'))
            fit = _night_fit(sitefit, wide_triples, center)
            out['search_center'] = _brief(center)
        if fit and fit.get('ok'):
            prop = dict(lat=fit['lat'], lon=fit['lon'], alt_m=fit.get('alt_m') or 0, accuracy_m=fit['accuracy_m'], name='Vorschlag')
            fit_triples = strict(prop)
            final = _night_fit(sitefit, fit_triples, best)
            if final.get('ok') and final['trails'] >= fit['trails']:
                fit = final
                prop.update(lat=fit['lat'], lon=fit['lon'], accuracy_m=fit['accuracy_m'])
            sc = _score(sitefit, fit_triples)
            fit = dict(fit, site=prop, **{'after_' + k: v for k, v in sc.items()})
        elif fit:
            out['fit_reason'] = fit.get('reason')
    keys = ('trails', 'timed', 'dropped', 'clock_s', 'clock_sigma_s', 'rms_before_arcsec', 'rms_after_arcsec', 'ellipse_km', 'shift_km')
    better_fit = fit and fit.get('ok') and fit['after_sure'] >= best['sure'] and (
        fit['after_sure'] > best['sure'] or (fit['after_rms'] or 1e9) < 0.7 * (best['rms'] or 1e9))
    if better_fit:
        near = min(sites, key=lambda s: _km(s, fit['site']), default=None)
        if near and _km(near, fit['site']) * 1000 <= max(SAME_SITE_M, 2 * fit['site']['accuracy_m']):
            out['proposal'] = dict(kind='list', id=near.get('id'), name=near.get('name'), sure=fit['after_sure'], rms=fit['after_rms'],
                                   **{k: fit.get(k) for k in ('clock_s', 'clock_sigma_s', 'timed', 'trails')})
        else:
            p = fit['site']
            out['proposal'] = dict(kind='fit', lat=round(p['lat'], 6), lon=round(p['lon'], 6), alt_m=p['alt_m'], accuracy_m=p['accuracy_m'],
                                   sure=fit['after_sure'], rms=fit['after_rms'], **{k: fit.get(k) for k in keys},
                                   from_name=(best.get('name') if best['sure'] else (out.get('search_center') or {}).get('name')),
                                   shift_from_current_km=round(_km(p, current), 3) if current else None)
    elif best.get('site_id') != (current or {}).get('site_id') and best['sure'] > (out['current'] or {}).get('sure', 0):
        out['proposal'] = dict(kind='list', id=best.get('site_id'), name=best.get('name'), sure=best['sure'], rms=best['rms'])
    if out['proposal'] and out['proposal']['kind'] == 'list' and out['proposal']['id'] == (current or {}).get('site_id'):
        out['proposal'] = None                                   # bisheriger Ort ist schon der beste
    top = max(best['sure'], (fit or {}).get('after_sure') or 0, (out['proposal'] or {}).get('sure') or 0)
    found = bool(global_km) and needs_global(len(entries), top) and _global(sitefit, out, entries, plain, sites, current, global_km, clock, tick, top)
    prop = out.get('proposal') or {}
    if prop.get('kind') in ('fit', 'global') and prop.get('lat') is not None:
        # Alternativen (0.38.0): nahe Listenorte, die noch nicht streng geprüft wurden, nachprüfen – die Oberfläche bietet sie neben dem
        # zurückgerechneten Punkt an (Auswahl rematch.alternatives); dazu die Einrastgrenze für den Begründungstext
        import global_site
        acc = float(prop.get('accuracy_m') or 100)
        prop['snap_limit_m'] = round(max(SAME_SITE_M if prop['kind'] == 'fit' else 1000 * global_site.NEAR_LIST_KM, 2 * acc))
        seen = {t.get('id') for t in tested}
        for s in sites:
            if s.get('id') in seen or _km(s, prop) * 1000 > max(ALT_NEAR_M, ALT_SIGMA * acc): continue
            ref = dict(s, accuracy_m=float(s.get('accuracy_m') or 100), site_id=s.get('id'))
            tested.append(dict(id=s.get('id'), name=s.get('name'), alternative=True, **_score(sitefit, strict(ref))))
    if (out.get('clock') or {}).get('proposal'):
        out['status'] = 'better'
        out['note'] = ('Der bisherige Ort passt – mit korrigierter Uhr.' if out['proposal'] is None
                       else 'Ort mit korrigierter Uhr geprüft; Uhr und Ort zusammen übernehmen.')
    elif out['proposal']:
        out['status'] = 'better'
        if out['proposal']['kind'] == 'list' and out['proposal']['sure'] < sitefit.MIN_TRAILS:
            out['note'] = f'Nur {out["proposal"]["sure"]} eindeutige Spur(en) – zu wenig für eine Rückrechnung, nur Auswahl aus der Ortsliste.'
    elif best['sure'] == 0:
        out['status'] = 'none'
        out['note'] = ('Kein Ort passt: keine Spur ließ sich einer Satellitenbahn eindeutig zuordnen'
                       + (f' (auch nicht mit ± {radius_km:g} km Spielraum).' if seen_any(out) else '.'))
    else:
        out['status'] = 'ok'
        out['note'] = 'Der bisherige Ort passt am besten.' + (
            f' Für eine Rückrechnung sind {best["sure"]} eindeutige Spur(en) zu wenig.' if best['sure'] < sitefit.MIN_TRAILS and not good else '')
    if fit and not fit.get('ok') and not out.get('fit_reason'): out['fit_reason'] = fit.get('reason')
    if found:
        p = out['proposal']
        out['status'] = 'better'
        out['note'] = (f'Kein Listenort passte – der Ort wurde ohne Vorwissen aus {p["sure"]} Spuren zurückgerechnet'
                       + (f', {p["nearest_km"]:.0f} km vom nächsten Listenort.' if p.get('kind') == 'global' and p.get('nearest_km') is not None
                          else f' und liegt bei „{p.get("name")}“.' if p.get('kind') == 'list' else '.'))
    return out


def seen_any(out):
    return out.get('search_center') is not None


HEAT_DIR = 'standortsuche-karten'           # je Nacht eine kleine Datei (nicht in standortsuche-naechte.json, die gepollt wird)


def heat_file(night):
    import re
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', str(night or '')): raise ValueError('Ungültige Nacht')
    return BASE / HEAT_DIR / f'{night}.json'


def save_heat(night, res, computed=False):
    """Heatmap der Rückprojektion (`global_site.heat_map` + feine Karte der besten Häufung) und Kurzfassung des Ergebnisses speichern."""
    heat = res.get('_heat')
    if not heat or not night: return None
    p = res.get('proposal') or {}
    data = dict(night=night, created=time.time(), computed=computed, **heat,
                result=dict(tracks=res.get('tracks'), reason=res.get('reason'), stages=res.get('stages'), seconds=res.get('seconds'),
                            proposal={k: p.get(k) for k in ('lat', 'lon', 'accuracy_m', 'ellipse_km', 'sure', 'support', 'rms', 'clock_s')} if p else None,
                            clusters=[{k: c.get(k) for k in ('lat', 'lon', 'support', 'clock', 'false_alarm')} for c in (res.get('_clusters') or [])[:5]]))
    path = heat_file(night)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json(path, data)
    return path


def _plan(groups):
    results = BASE / 'Ergebnisse'
    plan = []
    for g in groups:
        entries = []
        for o in g['observations']:
            folder = (results / o['run'] / o['folder']).resolve()
            if folder.parent.parent != results.resolve(): continue
            entries += [(folder, i) for i in read_json(folder / 'kandidaten.json', []) or [] if i.get('world_path') and i.get('timestamp') is not None]
        plan.append((g, entries))
    return plan


def heatmap(groups_file, night_key_, global_km=1500.0, stop_file=None, clock=True):
    """„Heatmap berechnen“ für eine Nacht: Suche ohne Vorwissen auch dann, wenn ein Listenort passt – nur für die Karte, ändert keinen Vorschlag."""
    import global_site
    sitefit = sitefit_module()
    settings = offline_settings(); settings['satcat_offline'] = True
    if stop_file: settings['_stop_file'] = str(stop_file)
    plan = [(g, e) for g, e in _plan(read_json(groups_file, []) or []) if g['night'] == night_key_]
    if not plan: raise ValueError('Nacht nicht gefunden')
    g, entries = plan[0]
    sites = site_list(settings)
    centers = [dict(lat=float(s['lat']), lon=float(s['lon'])) for s in sites]
    current = site_for(entries[0][1], {'session': entries[0][1].get('session')}, settings) if entries else None
    if current and current.get('lat') is not None and not any(_km(current, c) < 1 for c in centers):
        centers.append(dict(lat=float(current['lat']), lon=float(current['lon'])))
    state = dict(done=0)

    def tick(text):
        state['done'] += 1
        emit('progress', current=min(99, state['done']), total=100, text=f'{night_key_}: {text}')
    res = global_site.search_night(sitefit, entries, settings, centers, region_km=global_km, clock=clock, tick=tick)
    path = save_heat(night_key_, res, computed=True)
    p = res.get('proposal')
    emit('result', night=night_key_, saved=bool(path), text=(f'Heatmap für {night_key_} berechnet' + (f' – Häufung aus {p["support"]} Spuren.' if p else '.'))
         if path else f'{night_key_}: keine Heatmap ({res.get("reason") or "keine Stimmen"}).')


def nights(groups_file, out_file, radius_km=SEARCH_RADIUS_KM, stop_file=None, clock=True, global_km=0.0):
    """Standorte aller Nächte (Gruppen vom Server: [{night, observations: [{run, folder, session}]}]). Schreibt nach jeder Nacht."""
    sitefit = sitefit_module()
    groups = read_json(groups_file, []) or []
    settings = offline_settings(); settings['satcat_offline'] = True
    if stop_file: settings['_stop_file'] = str(stop_file)
    plan = _plan(groups)
    n_sites = max(1, len(site_list(settings)))
    import clock_scan
    blocks = 2 * math.ceil(clock_scan.SCAN_RANGE_S / clock_scan.BLOCK_SLACK_S / 2) + 1
    # Ortssuche ohne Vorwissen: Schritte grob geschätzt (Rückprojektion, feine Prüfung, strenger Abgleich)
    total = sum(len(e) * (n_sites + 3 + (blocks if clock and needs_clock(sitefit, e) else 0) + (3 if global_km else 0)) + (60 if global_km and len(e) >= 3 else 0)
                for _, e in plan) or 1
    state = dict(done=0)
    report = dict(created=time.time(), radius_km=radius_km, global_km=global_km, state='running', nights=[])

    def tick(text):
        state['done'] = min(total - 1, state['done'] + 1)
        emit('progress', current=state['done'], total=total, text=text)

    stopped = False
    for g, entries in plan:
        if stop_file and Path(stop_file).exists(): stopped = True; break
        try: report['nights'].append(night(sitefit, g, entries, settings, radius_km, tick, clock, global_km))
        except InterruptedError: stopped = True; break
        except (OSError, ValueError) as error:
            report['nights'].append(dict(night=g['night'], observations=g['observations'], status='error', note=str(error), proposal=None))
        write_json(out_file, report)                           # fertige Nächte bleiben beim Anhalten erhalten
    report['state'] = 'stopped' if stopped else 'done'
    write_json(out_file, report)
    better = sum(1 for r in report['nights'] if r.get('status') == 'better')
    clocks = sum(1 for r in report['nights'] if (r.get('clock') or {}).get('proposal'))
    unknown = sum(1 for r in report['nights'] if (r.get('proposal') or {}).get('kind') == 'global')
    emit('result', stopped=stopped, nights=len(report['nights']), better=better, clocks=clocks, unknown=unknown,
         text=('Angehalten. ' if stopped else '') + f'{len(report["nights"])} Nächte geprüft, {better} mit Vorschlag'
              + ''.join(f' · {t}' for t in ([f'{unknown} unbekannte Orte zurückgerechnet'] if unknown else []) + ([f'{clocks} mit Uhrversatz'] if clocks else [])) + '.')


def sitefit_night(summary):
    import re
    m = re.search(r'(\d{4})-?(\d{2})-?(\d{2})', str(summary.get('session') or ''))
    return '-'.join(m.groups()) if m else None


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest='cmd', required=True)
    a = sub.add_parser('rematch'); a.add_argument('roots', type=Path, nargs='+'); a.add_argument('--force', action='store_true'); a.add_argument('--stop-file', type=Path)
    b = sub.add_parser('search'); b.add_argument('root', type=Path); b.add_argument('folder'); b.add_argument('--radius-km', type=float, default=SEARCH_RADIUS_KM); b.add_argument('--stop-file', type=Path)
    c = sub.add_parser('nights'); c.add_argument('groups', type=Path); c.add_argument('out', type=Path); c.add_argument('--radius-km', type=float, default=SEARCH_RADIUS_KM); c.add_argument('--stop-file', type=Path)
    c.add_argument('--no-clock', dest='clock', action='store_false')
    c.add_argument('--global-km', type=float, default=0.0)          # Ortssuche ohne Vorwissen (0 = aus)
    h = sub.add_parser('heatmap'); h.add_argument('groups', type=Path); h.add_argument('night'); h.add_argument('--global-km', type=float, default=1500.0)
    h.add_argument('--stop-file', type=Path); h.add_argument('--no-clock', dest='clock', action='store_false')
    args = parser.parse_args()
    try:
        if args.cmd == 'rematch': rematch(args.roots, args.force, args.stop_file)
        elif args.cmd == 'nights': nights(args.groups, args.out, args.radius_km, args.stop_file, args.clock, args.global_km)
        elif args.cmd == 'heatmap': heatmap(args.groups, args.night, args.global_km, args.stop_file, args.clock)
        else: search(args.root, args.folder, args.radius_km, args.stop_file)
    except InterruptedError:
        emit('result', stopped=True, text='Angehalten.')
    except (OSError, ValueError) as error:
        emit('error', text=str(error)); sys.exit(1)
