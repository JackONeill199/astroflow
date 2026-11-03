"""Originalaufnahmen eines gespeicherten Laufs für eine vollständige neue Erkennung auflösen."""
import json
import datetime as dt
import re
from collections import defaultdict
from pathlib import Path


def check_replacement(path, row):
    """Gleiche Stellina-Dateinamen kommen in jeder Nacht erneut vor: Zeit statt Namen allein prüfen."""
    expected = row.get('timestamp')
    if path.suffix.lower() not in ('.fits', '.fit', '.fts') or expected is None:
        raise ValueError(f'Die Originalidentität von {path.name} lässt sich nicht sicher prüfen. Bitte den ursprünglichen Ordner wiederherstellen oder eine neue Analyse starten.')
    from astropy.io import fits
    try:
        stamp = dt.datetime.fromisoformat(str(fits.getheader(path)['DATE-OBS']).replace('Z', '+00:00'))
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=dt.timezone.utc)
    except (OSError, ValueError, KeyError) as e:
        raise ValueError(f'Das Aufnahmedatum von {path.name} lässt sich nicht prüfen.') from e
    if abs(stamp.timestamp() - float(expected)) > .001:
        date = dt.datetime.fromtimestamp(float(expected), dt.timezone.utc).strftime('%d.%m.%Y %H:%M:%S UTC')
        raise ValueError(f'{path.name} gehört nicht zur ursprünglichen Aufnahme vom {date}. Bitte den Originalordner der passenden Nacht wählen.')


def prepare(run, source=None):
    from .api import within, _read_json
    from json_store import open_read
    from .stats import _first_file
    summaries = _read_json(run / 'videos.json', [])
    if not summaries:
        raise ValueError('Dieser Lauf hat keine gespeicherte Bildfolge.')
    replacements = defaultdict(list)
    if source:
        source = Path(source).expanduser().resolve()
        if not source.is_dir():
            raise ValueError('Der gewählte Originalordner existiert nicht.')
        for path in source.rglob('*'):
            if path.is_file():
                replacements[path.name.casefold()].append(path)
    groups = []; missing = 0; total = 0
    for summary in summaries:
        folder = within(run, summary['video']).parent
        rows = {}
        for name in ('frames.jsonl', 'ausgeschlossen.jsonl'):
            manifest = folder / name
            if not manifest.exists():
                continue
            with open_read(manifest) as stream:
                for line in stream:
                    if not line.strip():
                        continue
                    row = json.loads(line)
                    rows[int(row['index'])] = row
        expected = int(summary.get('frames', 0)) + int(summary.get('excluded', 0))
        if len(rows) < 2 or len(rows) != expected:
            raise ValueError('Die ursprüngliche Aufnahmeliste fehlt oder ist unvollständig. Bitte eine neue Analyse über den Originalordner starten.')
        files = []
        for _, row in sorted(rows.items()):
            total += 1
            path = Path(row['file'])
            if not path.is_file():
                # Automatische Stacking-Übergaben versehen Dateinamen mit einem fünfstelligen Präfix.
                names = {path.name.casefold(), re.sub(r'^\d{5}_', '', path.name).casefold()}
                matches = {p for name in names for p in replacements[name]}
                if len(matches) > 1:
                    raise ValueError(f'Dateiname nicht eindeutig: {path.name}. Bitte einen engeren Originalordner auswählen.')
                if matches:
                    path = matches.pop()
                    check_replacement(path, row)
                else:
                    missing += 1
                    continue
            files.append(str(path.resolve()))
        fps = float(summary.get('fps') or 8)
        if not 1 <= fps <= 60:
            raise ValueError('Die gespeicherte Bildrate ist ungültig.')
        groups.append(dict(session=summary['session'], files=files, fps=fps,
                           source_first_file=summary.get('source_first_file') or _first_file(folder)))
    return dict(groups=groups, missing=missing, total=total, original_run=run.name)
