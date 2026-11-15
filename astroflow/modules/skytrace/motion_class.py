# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0 OR AGPL-3.0-or-later
# Copyright 2026 JackONeill199 – Doppellizenz (wahlweise), siehe engine/LICENSE und NOTICE.
"""Ehrliche Einstufung bewegter Spuren: Satellit, Flugzeug oder langsamer Beweger (14.11.2026).

Vorher wurde jede über mehrere Aufnahmen verkettete Spur pauschal „Flugzeug/Satellit?“ (kind 'plane'), jede passende
Satellitenbahn – auch eine grob passende – gewann gegen ein klares Blinkmuster, und Flugzeugmerkmale ohne Blinken
(zwei parallele Positionslichter) zählten gar nicht. Hier entstehen aus gespeicherten Messwerten drei ehrliche Fälle:

- ``satellite_or_plane`` „Satellit oder Flugzeug“: bewegt sich über mehrere Aufnahmen, Satellitenprüfung offen.
- ``likely_plane`` „Wahrscheinlich Flugzeug“: Blink-/Positionslichtmerkmale oder eine Satellitenprüfung mit
  vorhandenen Bahndaten findet nichts Passendes.
- ``asteroid`` „Langsamer Beweger (Asteroid möglich)“: nur punktförmig, langsam, über viele Aufnahmen.
``satellite`` heißt: eine Bahn passt eng in Lage, Richtung und Zeit – die Oberfläche nennt dann den Satelliten.
``None``: keine Aussage (z. B. Einzelbild-Spur ohne Merkmale – Meteor, Satellit, Flugzeug bleiben offen).

Reine Standardbibliothek: Der Server importiert das Modul, die Engine lädt es über den Pfad
(`engine/candidate_report.motion_class_module`). Nie eine Identität behaupten – nur Indizien mit Begründung.
"""
import math

VERSION = 1                      # erhöhen, wenn alte Einstufungen falsch wären (Server rechnet bei abweichender Version neu)

# Eng passende Bahn (sonst „grob“): mittlerer Abstand, Zeit außerhalb der Belichtung, Richtung
STRONG_OFFSET_ARCSEC = 120.
STRONG_TIME_S = 2.
STRONG_ALIGN_DEG = 5.
# Zwei parallele Lichter in derselben Aufnahme (Positionslichter an den Flügelspitzen, Befund M2 2026-11-13, Bild 397)
TWIN_ANGLE_DEG = 3.
TWIN_MIN_LENGTH = 40.            # Analysepixel
TWIN_MIN_OFFSET = 3.
TWIN_MAX_OFFSET = 120.
TWIN_OVERLAP = .6
# Asteroiden: auch nahe Erdbahnkreuzer bewegen sich selten schneller als ~1″/s; ein Flugzeug quert das Feld in Sekunden.
ASTEROID_MAX_ARCSEC_S = 2.
# Verkettete Spur ohne passende Bahn: unter ~60″/s (≥ 1 min für 1°) eher ein hoher, im Archiv fehlender Satellit; ein Flugzeug
# wäre dafür sehr weit weg oder flöge auf den Beobachter zu – dann bleibt es „Satellit oder Flugzeug“ (Befund NGC 2244 L2: 23″/s).
PLANE_MIN_ARCSEC_S = 60.

NAMES = {'satellite_or_plane': 'Satellit oder Flugzeug', 'likely_plane': 'Wahrscheinlich Flugzeug',
         'asteroid': 'Langsamer Beweger (Asteroid möglich)', 'satellite': None}
KINDS = {'satellite_or_plane': 'plane', 'likely_plane': 'plane', 'asteroid': 'moving', 'satellite': 'satellite'}


def _num(value):
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (TypeError, ValueError):
        return None


def _segment(c):
    points = (c.get('evidence') or {}).get('segment') or []
    if len(points) < 2:
        return None
    try:
        (x1, y1), (x2, y2) = points[0][:2], points[-1][:2]
        return (float(x1), float(y1)), (float(x2), float(y2))
    except (TypeError, ValueError):
        return None


def _hits(c):
    return set(c.get('hit_indices') or [])


def twin(c, others):
    """Paralleles Nachbarlicht derselben Aufnahme (anderer Kandidat), sonst None."""
    own = _segment(c)
    if not own or len(_hits(c)) != 1 or c.get('linked_track'):
        return None
    (ax, ay), (bx, by) = own
    dx, dy = bx - ax, by - ay
    length = math.hypot(dx, dy)
    if length < TWIN_MIN_LENGTH:
        return None
    ux, uy = dx / length, dy / length
    best = None
    for other in others:
        if other is c or not (_hits(c) & _hits(other)) or other.get('linked_track'):
            continue
        seg = _segment(other)
        if not seg:
            continue
        (cx, cy), (ex, ey) = seg
        odx, ody = ex - cx, ey - cy
        olen = math.hypot(odx, ody)
        if olen < TWIN_MIN_LENGTH:
            continue
        cosine = abs(ux * odx + uy * ody) / olen
        angle = math.degrees(math.acos(min(1., cosine)))
        if angle > TWIN_ANGLE_DEG:
            continue
        mx, my = (cx + ex) / 2, (cy + ey) / 2
        offset = abs((mx - ax) * uy - (my - ay) * ux)            # senkrechter Abstand der Mitte zur eigenen Linie
        if not TWIN_MIN_OFFSET <= offset <= min(TWIN_MAX_OFFSET, .5 * min(length, olen)):
            continue
        along = sorted(((cx - ax) * ux + (cy - ay) * uy, (ex - ax) * ux + (ey - ay) * uy))
        overlap = max(0., min(length, along[1]) - max(0., along[0])) / min(length, olen)
        if overlap < TWIN_OVERLAP:
            continue
        found = dict(partner=other.get('id'), offset_pixels=round(offset, 1), angle_degrees=round(angle, 2), overlap=round(overlap, 2))
        if best is None or offset < best['offset_pixels']:
            best = found
    return best


def satellite_result(c):
    """('strong'|'weak'|'none'|'open', bester Treffer, Katalogeintrag)."""
    sat = next((r for r in c.get('catalogs') or [] if r.get('provider') == 'satellites'), None)
    if not sat:
        return 'open', None, None
    if sat.get('status') == 'compatible' and sat.get('matches'):
        m = sat['matches'][0]
        offset = _num(m.get('mean_offset_arcsec'))
        offset = offset if offset is not None else _num(m.get('separation_arcsec'))
        late = _num(m.get('time_offset_s')) or 0.
        align = _num(m.get('alignment_degrees'))
        strong = offset is not None and offset <= STRONG_OFFSET_ARCSEC and late <= STRONG_TIME_S and (align is None or align <= STRONG_ALIGN_DEG)
        return ('strong' if strong else 'weak'), m, sat
    if sat.get('status') == 'checked_no_match' and (sat.get('checked_trajectories') or sat.get('archive_records')):
        return 'none', None, sat
    return 'open', None, sat


def _match_text(m):
    offset = _num(m.get('mean_offset_arcsec'))
    offset = offset if offset is not None else _num(m.get('separation_arcsec'))
    parts = [f'Abstand {offset / 60:.1f}′' if offset is not None else 'Abstand unbekannt']
    if _num(m.get('time_offset_s')):
        parts.append(f'{m["time_offset_s"]:.1f} s außerhalb der Belichtung')
    if _num(m.get('alignment_degrees')):
        parts.append(f'Richtung {m["alignment_degrees"]:.0f}° abweichend')
    return (m.get('name') or f'NORAD {m.get("norad_id")}') + ' (' + ', '.join(parts) + ')'


def _pixel_degrees(c):
    p = c.get('plausibility') or {}
    fov, size = p.get('field_of_view_deg'), p.get('analysis_size')
    try:
        return float(fov[0]) / float(size[0])
    except (TypeError, ValueError, IndexError, ZeroDivisionError):
        return None


def track_speed(c):
    """Winkelgeschwindigkeit [″/s] einer verketteten Spur aus `linked_track.rate_pixels_s`; None, wenn unbekannt."""
    rate, scale = _num((c.get('linked_track') or {}).get('rate_pixels_s')), _pixel_degrees(c)
    return rate * scale * 3600 if rate is not None and scale else None


def mover_speed(c):
    """Winkelgeschwindigkeit [″/s] eines langsamen Punktbewegers aus Pfad und Zeit; None, wenn unbekannt.

    Zeit aus den Aufnahmezeiten der Pfadpunkte (`evidence.segment_times`), sonst aus ihren Bildnummern (`segment_frames`) mal
    üblichem Bildabstand – ausgeschlossene oder fehlende Bilder zwischen zwei Punkten zählen so mit (Review PR #130). Ältere
    Läufe ohne diese Angaben: Punkte als aufeinanderfolgende Bilder im üblichen Abstand (Näherung)."""
    ev = c.get('evidence') or {}
    points = ev.get('segment') or []
    scale = _pixel_degrees(c)
    if len(points) < 2 or not scale:
        return None
    length = sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(points, points[1:]))
    times = ev.get('segment_times') or []
    if len(times) == len(points) and all(_num(t) is not None for t in times) and float(times[-1]) > float(times[0]):
        return length * scale * 3600 / (float(times[-1]) - float(times[0]))
    stamps = sorted(f['timestamp'] for f in c.get('frames') or [] if _num(f.get('timestamp')) is not None and not f.get('excluded'))
    steps = sorted(b - a for a, b in zip(stamps, stamps[1:]) if b > a)
    if not steps:
        return None
    step = steps[len(steps) // 2]
    nums = ev.get('segment_frames') or []
    if len(nums) == len(points) and all(_num(n) is not None for n in nums) and float(nums[-1]) > float(nums[0]):
        return length * scale * 3600 / (step * (float(nums[-1]) - float(nums[0])))
    return length * scale * 3600 / (step * (len(points) - 1))


def assess(c, others=()):
    """Einstufung eines Kandidaten (dict mit verdict, name, kind, reasons, features). `others`: Kandidaten derselben Beobachtung."""
    kind = c.get('kind')
    sat_state, match, _ = satellite_result(c)
    features, plane, reasons = {}, [], []
    blink = c.get('blink') if isinstance(c.get('blink'), dict) else None
    if blink:
        plane.append(f'{blink.get("flashes")} Blitze im gleichen Takt (Blink- bzw. Positionslichter)')
        features['blink'] = True
    pair = twin(c, others)
    if pair:
        features['twin'] = pair
        plane.append(f'zweites paralleles Licht {pair["partner"]} im Abstand von {pair["offset_pixels"]:.0f} Analysepixeln in derselben Aufnahme '
                     '(Positionslichter an den Flügelspitzen; ein Satellit zieht eine einzelne Spur)')
    track = c.get('linked_track') or {}
    multi = (track.get('count') or 0) > 1
    if multi:
        features['frames'] = track.get('count')
    verdict = None
    if kind == 'moving':
        # Geschwindigkeit nur als Kontext: „Asteroid möglich“ nur bei gemessener langsamer Bewegung; schneller oder unbekannt
        # bleibt neutral „Bewegungskandidat“ (keine Klasse aus Beispielgeschwindigkeiten, skytrace/AGENTS.md).
        speed = mover_speed(c)
        features['speed_arcsec_s'] = round(speed, 3) if speed is not None else None
        if sat_state == 'strong':
            verdict = 'satellite'
        elif speed is not None and speed <= ASTEROID_MAX_ARCSEC_S:
            verdict = 'asteroid'
            reasons.append(f'Punktförmig, über viele Aufnahmen gleichmäßig gegen die Sterne bewegt (etwa {speed:.2f}″/s) – '
                           'vereinbar mit einem Asteroiden; ein Flugzeug quert das Bildfeld in Sekunden.')
    elif sat_state == 'strong' and not pair:
        verdict = 'satellite'
        if blink:
            reasons.append('Bahn passt eng; das Blinken kann von einem taumelnden Satelliten stammen.')
    elif plane:
        verdict = 'likely_plane'
        reasons.append('Flugzeugmerkmal: ' + '; '.join(plane) + '.')
        if sat_state in ('strong', 'weak'):
            reasons.append('Eine Satellitenbahn passt nur ' + ('in der Lage' if sat_state == 'strong' else 'grob') + ': ' + _match_text(match)
                           + ' – bei diesem Merkmal wenig wahrscheinlich.')
        elif sat_state == 'none':
            reasons.append('Satellitenprüfung mit vorhandenen Bahndaten fand keine passende Bahn.')
    elif multi or kind == 'plane':
        if sat_state == 'strong':
            verdict = 'satellite'
        elif sat_state == 'none':
            speed = track_speed(c) if multi else None
            features['speed_arcsec_s'] = round(speed, 1) if speed is not None else None
            note = 'Satellitenprüfung mit vorhandenen Bahndaten fand keine passende Bahn (Archive sind unvollständig, z. B. fehlen militärische Satelliten)'
            if speed is not None and speed < PLANE_MIN_ARCSEC_S:
                verdict = 'satellite_or_plane'
                reasons.append(note + f'; mit etwa {speed:.0f}″/s aber langsam – eher ein hoher Satellit, ein Flugzeug wäre sehr weit entfernt oder flöge auf den Beobachter zu.')
            elif speed is None:
                verdict = 'satellite_or_plane'
                reasons.append(note + '; Winkelgeschwindigkeit nicht messbar (Bildfeld unbekannt) – ein langsamer hoher Satellit bleibt möglich.')
            else:
                verdict = 'likely_plane'
                reasons.append(note + '.')
        else:
            verdict = 'satellite_or_plane'
            reasons.append('Satellitenprüfung ' + ('ergab nur eine grob passende Bahn: ' + _match_text(match) if sat_state == 'weak'
                                                     else 'nicht möglich (keine Bahndaten, Standort oder Belichtung)') + '.')
        if multi:
            reasons.insert(0, f'Gleichmäßig bewegt über {track.get("count")} Aufnahmen ({_num(track.get("span_seconds")) or 0:.0f} s) – kein Meteor.')
    if verdict is None:
        return dict(version=VERSION, verdict=None, name=None, kind=None, reasons=[], features=features)
    return dict(version=VERSION, verdict=verdict, name=NAMES[verdict], kind=KINDS[verdict], reasons=reasons, features=features)


def annotate(candidates):
    """Alle Kandidaten einer Beobachtung (Liste) einstufen; setzt `motion_class` und gibt die Liste zurück."""
    items = [c for c in candidates or [] if isinstance(c, dict)]
    for c in items:
        c['motion_class'] = assess(c, items)
    return candidates


def current(candidates):
    """Wie `annotate`, aber nur, wenn eine Einstufung fehlt oder aus einer älteren Version stammt (Server, ältere Läufe)."""
    items = [c for c in candidates or [] if isinstance(c, dict)]
    if any((c.get('motion_class') or {}).get('version') != VERSION for c in items):
        annotate(items)
    return candidates
