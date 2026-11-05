"""Uhrversatz einer Nacht suchen (Kamera-Uhr gegen UTC) – nur Vorschlag, übernommen wird er erst auf Knopfdruck.

Anlass (05.11.2026): In einer Augustwoche 2022 ging die Stellina-Uhr rund 10 min falsch; der Abgleich (Belichtung ± 20 s)
fand dann fast keine Satelliten mehr. Verfahren:
  1. Je Spur den normalen Satellitenabgleich blockweise über ± `SCAN_RANGE_S` verschieben (Blöcke à 2 × `BLOCK_SLACK_S`).
     Jede passende Bahn liefert über ihre Durchgangszeiten an Spuranfang/-ende einen Versatz samt Spielraum: Läuft die Spur
     ganz durchs Bild, steht der Durchgang fest (± 0), sonst darf er innerhalb der Belichtung wandern.
  2. Bei weitem Fenster passen viele Bahnen zufällig (Trümmer, Starlink). Gezählt wird daher nur, wie viele **verschiedene**
     Spuren (mit verschiedenen Satelliten) bei demselben Versatz (± `MATCH_TOL_S`) treffen.
  3. Zufallsniveau: Trefferdichte je Spur fern vom Maximum → erwartete Zahl zufällig zusammenfallender Spuren λ;
     Poisson-Wahrscheinlichkeit für ≥ k, mal Zahl der unabhängigen Versatzschritte im Fenster (Suche an vielen Stellen).
  4. Vorschlag erst ab `MIN_TRAILS` Spuren und Fehlalarm-Wahrscheinlichkeit ≤ `MAX_FALSE`; Feinwert aus den Durchgangszeiten
     (Schnitt der Spielräume) bzw. – mit Spuren ganz im Bild – aus `sitefit.fit` am verschobenen Abgleich.
Keine Netzabfragen (Bahnarchive lokal, `_archive_time` = Kamerazeit, damit der ETH-Minuten-Cache des Laufs passt)."""
import math

from catalog_matching import clock_offset, night_key

SCAN_RANGE_S = 1200           # Suchbereich ± 20 min
BLOCK_SLACK_S = 60            # je Abgleich Belichtung ± 60 s → 20 Abgleiche je Spur
SAMPLES_PER_S = 5             # Bahnpunkte je Sekunde wie im normalen Abgleich (gröber verliert kurze Spuren)
MATCH_TOL_S = 30              # Bahnfehler längs (alte Trümmer-TLEs) + Uhrdrift: echte Nacht 01.08.2022 streute ± 25 s
MIN_TRAILS = 3
MAX_FALSE = 0.01              # Fehlalarm-Wahrscheinlichkeit über das ganze Suchfenster
MIN_SHIFT_S = 20              # kleinere Versätze deckt schon die normale Uhrtoleranz ab
PEAK_GUARD_S = 60             # für das Zufallsniveau Treffer so nah am Maximum nicht mitzählen


def _check(item, site, settings):
    from rematch_sites import check
    return check(item, site, settings)


def trail_hits(item, site, settings, range_s=SCAN_RANGE_S, tick=None):
    """Alle Bahnen, die die Spur bei irgendeinem Versatz im Fenster passieren: [{norad, name, offset, half, cross}]."""
    base = clock_offset(item, settings)                          # schon übernommener Versatz: um ihn herum suchen
    scan = dict(settings, clock_offsets={}, satellite_clock_slack_s=BLOCK_SLACK_S, satellite_samples_per_s=SAMPLES_PER_S,
                satellite_max_matches=1000, satcat_offline=True)
    dur = float(item.get('exposure_seconds') or 0)
    from motion_plausibility import exposure_start
    try:
        scan['_archive_time'] = exposure_start(item) + dur / 2
    except (TypeError, ValueError, KeyError):
        return [], 'not_checkable'
    hits, states = {}, set()
    n = max(1, int(math.ceil(range_s / BLOCK_SLACK_S / 2)))
    for k in range(-n, n):
        shift = base + (2 * k + 1) * BLOCK_SLACK_S
        x = dict(item, timestamp=float(item['timestamp']) + shift)
        if tick: tick(shift)
        res = _check(x, site, scan)
        states.add(res.get('status'))
        for m in res.get('matches') or []:
            p = m.get('passage_seconds')
            if m.get('norad_id') is None or not p or len(p) < 2: continue
            off = shift + (p[0] + p[1]) / 2 - dur / 2
            half = max(0.0, (dur - (p[1] - p[0])) / 2)
            key = (int(m['norad_id']), round(off / 4))
            if key in hits: continue
            hits[key] = dict(norad=int(m['norad_id']), name=m.get('name'), offset=round(off, 2), half=round(half, 2),
                             cross=m.get('mean_offset_arcsec'))
    state = 'ok' if states & {'compatible', 'checked_no_match', 'site_uncertain'} else (sorted(states - {None}) or ['not_checkable'])[0]
    return sorted(hits.values(), key=lambda h: h['offset']), state


def _near(h, t, tol=MATCH_TOL_S):
    return abs(h['offset'] - t) <= h['half'] + tol


def _support(trails, t, tol=MATCH_TOL_S):
    """Verschiedene Spuren mit verschiedenen Satelliten, die bei Versatz t treffen (je Spur die nächstgelegene Bahn)."""
    used, out = set(), []
    for tr in trails:
        best = sorted((h for h in tr['hits'] if _near(h, t, tol) and h['norad'] not in used), key=lambda h: abs(h['offset'] - t))
        if best:
            used.add(best[0]['norad'])
            out.append(dict(id=tr['id'], folder=tr.get('folder'), **best[0]))
    return out


def _poisson_tail(k, lam):
    """P(X ≥ k) für X ~ Poisson(λ)."""
    if k <= 0: return 1.0
    term = math.exp(-lam); cdf = term
    for i in range(1, k):
        term *= lam / i; cdf += term
    return max(0.0, 1.0 - cdf)


def evaluate(trails, range_s=SCAN_RANGE_S, step_s=2.0, tol=MATCH_TOL_S):
    """trails: [{id, folder, hits}] → bester Versatz mit Zufallsniveau (ohne Vorschlagsentscheidung)."""
    usable = [t for t in trails if t.get('hits') is not None]
    if not usable:
        return dict(ok=False, reason='Keine prüfbare Spur.')
    best, best_t = [], 0.0
    t = -range_s
    while t <= range_s:
        sup = _support(usable, t, tol)
        if len(sup) > len(best) or (len(sup) == len(best) and sup and abs(t) < abs(best_t)):
            best, best_t = sup, t
        t += step_s
    if not best:
        return dict(ok=False, reason='Bei keinem Versatz passt eine Spur zu einer Satellitenbahn.', trails=len(usable), support=0)
    # Feinwert: Schnitt der Spielräume der beteiligten Spuren, sonst Median der Einzelwerte
    lo = max(h['offset'] - h['half'] for h in best); hi = min(h['offset'] + h['half'] for h in best)
    if lo <= hi:
        offset, spread = (lo + hi) / 2, (hi - lo) / 2
    else:
        # Spielräume schneiden sich nicht (Bahnfehler längs): Median (robust gegen einzelne Zufallstreffer im Fenster),
        # Unsicherheit = Streuung / √n
        vals = sorted(h['offset'] for h in best)
        n = len(vals)
        offset = vals[n // 2] if n % 2 else (vals[n // 2 - 1] + vals[n // 2]) / 2
        spread = math.sqrt(sum((v - offset) ** 2 for v in vals) / max(1, n - 1) / n)
    again = _support(usable, offset, tol)
    best = again if len(again) >= len(best) else best
    # Zufallsniveau aus den Treffern fern vom Maximum
    width = max(1.0, 2 * range_s - 2 * PEAK_GUARD_S)
    lam = 0.0
    for tr in usable:
        far = [h for h in tr['hits'] if abs(h['offset'] - offset) > PEAK_GUARD_S]
        cover = sum(2 * (h['half'] + tol) for h in far)
        lam += 1 - math.exp(-cover / width)
    k = len(best)
    cells = max(1.0, 2 * range_s / (2 * tol + 10))              # unabhängige Versatzschritte (Spielraum ~5 s je Spur)
    false = min(1.0, cells * _poisson_tail(k, lam))
    return dict(ok=True, offset_s=round(offset, 1), spread_s=round(spread, 1), support=k, trails=len(usable),
                chance=round(lam, 2), false_alarm=round(false, 6), confidence=round(1 - false, 4),
                hits=[dict(id=h['id'], folder=h.get('folder'), norad=h['norad'], name=h['name'], offset_s=h['offset'],
                           half_s=h['half'], cross_arcsec=h['cross']) for h in best])


def proposal_ok(ev, before_sure=0):
    """Vorschlag nur bei klarer Häufung, deutlich über dem Zufall, außerhalb der normalen Uhrtoleranz und mit mehr Spuren."""
    if not ev.get('ok'): return False, ev.get('reason')
    if ev['support'] < MIN_TRAILS:
        return False, f'Höchstens {ev["support"]} Spur(en) bei demselben Versatz – zu wenig (nötig {MIN_TRAILS}).'
    if ev['false_alarm'] > MAX_FALSE:
        return False, (f'{ev["support"]} Spuren bei {ev["offset_s"]:+.0f} s, aber zufällig wären etwa {ev["chance"]:.1f} zu erwarten '
                       f'(Fehlalarm-Wahrscheinlichkeit {100 * ev["false_alarm"]:.0f} %).')
    if abs(ev['offset_s']) < MIN_SHIFT_S:
        return False, 'Die Uhr geht richtig (Versatz innerhalb der normalen Uhrtoleranz).'
    if ev['support'] <= before_sure:
        return False, 'Mit dem Versatz passen nicht mehr Spuren als bisher.'
    return True, None


def scan_night(sitefit, entries, site, settings, range_s=SCAN_RANGE_S, tick=None, before_sure=0):
    """entries: [(Ordner, Kandidat)] einer Nacht → Ergebnis mit `proposal` (oder Grund). Kandidaten bleiben unverändert."""
    trails, unchecked = [], 0
    for f, item in entries:
        def t(shift, item=item):
            if tick: tick(f'{item.get("id")}: Uhrversatz {shift:+.0f} s prüfen')
        hits, state = trail_hits(item, site, settings, range_s, t)
        if state != 'ok':
            unchecked += 1; continue
        trails.append(dict(id=item.get('id'), folder=getattr(f, 'name', None), hits=hits, item=item, path=f))
    ev = evaluate(trails, range_s)
    out = dict(range_s=range_s, tracks=len(entries), unchecked=unchecked, **{k: v for k, v in ev.items() if k != 'reason'})
    ok, reason = proposal_ok(ev, before_sure)
    base = clock_offset(entries[0][1], settings) if entries else 0.0
    if ok:
        # Fein: strenger Abgleich mit dem gefundenen Versatz (normale Uhrtoleranz), Rückrechnung für die letzten Sekunden
        nights = sorted({night_key(i['timestamp']) for _, i in entries if isinstance(i.get('timestamp'), (int, float))})
        fine = dict(settings, clock_offsets=dict(settings.get('clock_offsets') or {}, **{n: ev['offset_s'] for n in nights}))
        strict = []
        for f, item in entries:
            if tick: tick(f'{item.get("id")}: Abgleich mit {ev["offset_s"]:+.0f} s')
            strict.append((f, item, _check(item, site, fine)))
        sure = [s for *_, s in strict if sitefit._sure(s or {})]
        out['after_sure'] = len(sure)
        out['after_compatible'] = sum(1 for *_, s in strict if (s or {}).get('status') == 'compatible')
        offset, sigma = ev['offset_s'], max(1.0, ev['spread_s'])
        if len(sure) >= sitefit.MIN_TRAILS:
            from rematch_sites import _night_fit
            fit = _night_fit(sitefit, strict, site)
            if fit.get('ok') and fit.get('timed') and fit.get('clock_sigma_s') is not None and fit['clock_sigma_s'] < 5:
                offset, sigma = ev['offset_s'] + fit['clock_s'], fit['clock_sigma_s']
                out['fit'] = {k: fit.get(k) for k in ('trails', 'timed', 'clock_s', 'clock_sigma_s', 'rms_after_arcsec')}
        if out['after_sure'] <= before_sure:
            ok, reason = False, f'Mit {ev["offset_s"]:+.0f} s passen streng nur {out["after_sure"]} Spuren eindeutig (bisher {before_sure}).'
        else:
            out['proposal'] = dict(offset_s=round(offset, 1), sigma_s=round(sigma, 1), support=ev['support'], sure=out['after_sure'],
                                   before=before_sure, confidence=ev['confidence'], chance=ev['chance'], nights=nights,
                                   base_s=round(base, 1))
            out['_strict'] = strict
    if not ok:
        out['reason'] = reason
    return out
