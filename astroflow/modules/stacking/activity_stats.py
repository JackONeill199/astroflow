"""Beobachtungsaktivität aus FITS-Metadaten, unabhängig von Ergebnisversionen und Rohdateien."""
from __future__ import annotations

import datetime as dt
import json
import math

from ...core import sites


def summarize(rows: list[dict], year: int | None = None) -> dict:
    records, years, missing = [], set(), 0
    for row in rows:
        if row.get("kind") != "light":
            continue
        try:
            info = json.loads(row.get("info") or "{}")
            stamp = dt.datetime.fromisoformat(info.get("date_obs", "").strip().replace("Z", "+00:00"))
            stamp = stamp.replace(tzinfo=dt.timezone.utc) if stamp.tzinfo is None else stamp.astimezone(dt.timezone.utc)
        except (ValueError, TypeError, AttributeError):
            missing += 1
            continue
        # Eine Beobachtungsnacht läuft von Mittag bis Mittag Ortszeit (core/sites.night_of, wie beim Import).
        site = info.get("site")                       # Ort im FITS-Kopf wie im Scanner (frames.night)
        lon = site[1] if isinstance(site, (list, tuple)) and len(site) >= 2 else None
        night = dt.date.fromisoformat(sites.night_of(stamp, lon=lon))
        years.add(night.year)
        if year is not None and night.year != year:
            continue
        try:
            exposure = float(info.get("exptime") or 0)
            exposure = exposure if math.isfinite(exposure) and exposure > 0 else 0
        except (ValueError, TypeError):
            exposure = 0
        records.append((stamp, night, exposure, row.get("project_id")))

    def bucket(key):
        return {"key": key, "frames": 0, "exposure": 0.0}

    months, nights = {}, {}
    weekdays = [bucket(i) for i in range(7)]
    hours = [bucket(i) for i in range(24)]
    for stamp, night, exposure, pid in records:
        month = night.strftime("%Y-%m")
        n = night.isoformat()
        for b in (months.setdefault(month, bucket(month)), nights.setdefault(n, bucket(n)), weekdays[night.weekday()], hours[stamp.hour]):
            b["frames"] += 1
            b["exposure"] += exposure
    if year is not None:
        for m in range(1, 13):
            key = f"{year:04d}-{m:02d}"
            months.setdefault(key, bucket(key))
    ranked = lambda values: sorted(values, key=lambda b: (-b["frames"], -b["exposure"], b["key"]))
    return {"year": year, "years": sorted(years, reverse=True), "timezone": "UTC", "missing_dates": missing,
            "frames": len(records), "exposure": sum(r[2] for r in records), "nights": len(nights),
            "objects": len({r[3] for r in records if r[3] is not None}),
            "first": min(nights, default=None), "last": max(nights, default=None),
            "months": sorted(months.values(), key=lambda b: b["key"]), "weekdays": weekdays, "hours": hours,
            "top_nights": ranked(nights.values())[:10], "top_month": next(iter(ranked(months.values())), None)}
