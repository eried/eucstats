"""Period snapshots: weekly champion (most km in the ISO week) from daily rows."""
from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import func

from models import DailyDistance, LeaderboardSnapshot, utcnow
from services.stats import _rider_brief


def iso_week_key(d: date) -> str:
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def week_range(d: date) -> tuple[date, date]:
    monday = d - timedelta(days=d.weekday())
    return monday, monday + timedelta(days=6)


def generate_weekly(db, ref: date | None = None, top: int = 10) -> dict:
    ref = ref or utcnow().date()
    start, end = week_range(ref)
    rows = (db.query(DailyDistance.store_id, func.sum(DailyDistance.km).label("km"))
            .filter(DailyDistance.date >= start, DailyDistance.date <= end)
            .group_by(DailyDistance.store_id)
            .order_by(func.sum(DailyDistance.km).desc()).limit(top).all())
    entries = [{**_rider_brief(db, sid), "km": round(km or 0, 2)} for sid, km in rows]
    payload = {"week": iso_week_key(ref), "start": start.isoformat(),
               "end": end.isoformat(), "champion": entries[0] if entries else None,
               "top": entries}

    key = iso_week_key(ref)
    snap = db.get(LeaderboardSnapshot, ("week", key, "distance"))
    if snap is None:
        snap = LeaderboardSnapshot(period_type="week", period_key=key, board="distance")
        db.add(snap)
    snap.payload = payload
    snap.generated_at = utcnow()
    db.commit()
    return payload
