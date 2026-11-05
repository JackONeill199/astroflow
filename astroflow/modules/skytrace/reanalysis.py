"""Originalaufnahmen eines gespeicherten Laufs für eine vollständige neue Erkennung auflösen."""
import json
import datetime as dt
import os
import re
from collections import defaultdict
from pathlib import Path

FITS_SUFFIXES = ('.fits', '.fit', '.fts')
HEADER_BLOCKS = 12            # DATE-OBS steht bei Stellina im ersten Block; mehr als 12 × 2880 Byte werden nie gelesen
MAX_ENTRIES = 500_000         # Obergrenze beim Durchsuchen eines Ersatzordners (ganze Stellina-Platte ≈ 30 000 Dateien)
TOLERANCE_S = .001


def date_obs(path):
    """DATE-OBS als UTC-Zeitstempel aus den ersten Header-Blöcken lesen (ohne astropy); None, wenn nicht lesbar."""
    try:
        with open(path, 'rb') as stream:
            for number in range(HEADER_BLOCKS):
                block = stream.read(2880)
                if len(block) < 2880 or (number == 0 and not block.startswith(b'SIMPLE')):
                    return None
                for at in range(0, 2880, 80):
                    card = block[at:at + 80]
                    if card[:8] == b'DATE-OBS':
                        found = re.match(rb"=\s*'([^']*)'", card[8:])
                        if not found:
                            return None
                        text = found.group(1).decode('ascii', 'replace').strip().replace('Z', '+00:00')
                        stamp = dt.datetime.fromisoformat(text)
                        if stamp.tzinfo is None:
                            stamp = stamp.replace(tzinfo=dt.timezone.utc)
                        return stamp.timestamp()
                    if card[:3] == b'END' and not card[3:8].strip():
                        return None
    except (OSError, ValueError):
        return None
    return None


def _date_text(expected):
    return dt.datetime.fromtimestamp(float(expected), dt.timezone.utc).strftime('%d.%m.%Y %H:%M:%S UTC')


def _stamp(path, stamps):
    if stamps is None:
        return date_obs(path)
    if path not in stamps:
        stamps[path] = date_obs(path)
    return stamps[path]


def check_replacement(path, row, stamps=None):
    """Gleiche Stellina-Dateinamen kommen in jeder Nacht erneut vor: Zeit statt Namen allein prüfen."""
    expected = row.get('timestamp')
    if path.suffix.lower() not in FITS_SUFFIXES or expected is None:
        raise ValueError(f'Die Originalidentität von {path.name} lässt sich nicht sicher prüfen. Bitte den ursprünglichen Ordner wiederherstellen oder eine neue Analyse starten.')
    stamp = _stamp(path, stamps)
    if stamp is None:
        raise ValueError(f'Das Aufnahmedatum von {path.name} lässt sich nicht prüfen.')
    if abs(stamp - float(expected)) > TOLERANCE_S:
        raise ValueError(f'{path.name} gehört nicht zur ursprünglichen Aufnahme vom {_date_text(expected)}. Bitte den Originalordner der passenden Nacht wählen.')


def _day_texts(expected):
    """Datumstexte (JJJJ-MM-TT), die ein passender Nacht-/Beobachtungsordner enthalten kann – nur für die Reihenfolge."""
    base = dt.datetime.fromtimestamp(float(expected), dt.timezone.utc)
    return {(base + dt.timedelta(hours=h)).strftime('%Y-%m-%d') for h in (-36, -24, -12, 0, 12)}


def pick(candidates, row, hints, stamps):
    """Unter gleichnamigen Dateien die mit passender DATE-OBS wählen. Die Reihenfolge dient nur der Leistung
    (Ordner früherer Treffer, Pfade mit dem Nachtdatum, Rest) – entschieden wird allein über die Aufnahmezeit."""
    expected = row.get('timestamp')
    name = Path(row['file']).name
    if len(candidates) == 1:
        check_replacement(candidates[0], row, stamps)
        return candidates[0]
    if expected is None:
        raise ValueError(f'Dateiname nicht eindeutig: {name}. Bitte einen engeren Originalordner auswählen.')
    days = _day_texts(expected)

    def rank(path):
        return (0 if path.parent in hints else 1, 0 if any(day in str(path) for day in days) else 1)
    readable = False
    for path in sorted(candidates, key=rank):
        stamp = _stamp(path, stamps) if path.suffix.lower() in FITS_SUFFIXES else None
        if stamp is None:
            continue
        readable = True
        if abs(stamp - float(expected)) <= TOLERANCE_S:
            return path
    if not readable:
        raise ValueError(f'Das Aufnahmedatum der {len(candidates)} gleichnamigen Dateien {name} lässt sich nicht prüfen. Bitte einen engeren Originalordner auswählen.')
    raise ValueError(f'Unter {len(candidates)} gleichnamigen Dateien {name} gehört keine zur ursprünglichen Aufnahme vom {_date_text(expected)}. Bitte den Originalordner der passenden Nacht wählen.')


def _index(source, wanted):
    """Gesuchte Dateien des Ersatzordners (rekursiv): Name → Pfade. Begrenzt auf MAX_ENTRIES Einträge."""
    found = defaultdict(list); seen = 0
    for folder, _, names in os.walk(source):
        for name in names:
            seen += 1
            if seen > MAX_ENTRIES:
                raise ValueError(f'Der gewählte Ordner enthält mehr als {MAX_ENTRIES // 1000} 000 Dateien. Bitte den Ordner der Beobachtung oder Nacht wählen.')
            key = name.casefold()
            if key in wanted:
                found[key].append(Path(folder) / name)
    return found


def _names(path):
    # Automatische Stacking-Übergaben versehen Dateinamen mit einem fünfstelligen Präfix.
    return {path.name.casefold(), re.sub(r'^\d{5}_', '', path.name).casefold()}


def prepare(run, source=None):
    from .api import within, _read_json
    from json_store import open_read
    from .stats import _first_file
    summaries = _read_json(run / 'videos.json', [])
    if not summaries:
        raise ValueError('Dieser Lauf hat keine gespeicherte Bildfolge.')
    loaded = []
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
        loaded.append((summary, folder, [row for _, row in sorted(rows.items())]))
    replacements = {}; stamps = {}; hints = set()
    if source:
        source = Path(source).expanduser().resolve()
        if not source.is_dir():
            raise ValueError('Der gewählte Originalordner existiert nicht.')
        wanted = {n for _, _, rows in loaded for row in rows if not Path(row['file']).is_file() for n in _names(Path(row['file']))}
        replacements = _index(source, wanted)
    groups = []; missing = 0; total = 0
    for summary, folder, rows in loaded:
        files = []
        for row in rows:
            total += 1
            path = Path(row['file'])
            if not path.is_file():
                matches = sorted({p for n in _names(path) for p in replacements.get(n, ())})
                if matches:
                    path = pick(matches, row, hints, stamps)
                    hints.add(path.parent)
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
