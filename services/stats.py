"""Read-only leaderboard/map/records queries over the materialized tables.
All public reads hit these precomputed tables — never raw trips."""
from __future__ import annotations

from sqlalchemy import desc, func

from models import CountryStat, DailyDistance, MapCell, Record, Rider, RiderStat, Trip


def _rider_loc(db, store_id: str):
    """Representative coordinate for a rider — their latest trip's start point."""
    row = (db.query(Trip.start_lat, Trip.start_lon)
           .filter(Trip.rider_store_id == store_id, Trip.start_lat.isnot(None))
           .order_by(Trip.start_utc.desc()).first())
    return (row[0], row[1]) if row else (None, None)


def _rider_brief(db, store_id: str) -> dict:
    r = db.get(Rider, store_id)
    lat, lon = _rider_loc(db, store_id)
    if r is None:
        return {"store_id": store_id, "name": None, "flag": None,
                "has_avatar": False, "lat": lat, "lon": lon}
    return {"store_id": store_id, "name": r.display_name, "flag": r.flag,
            "has_avatar": r.avatar_png is not None, "lat": lat, "lon": lon}


def _board(db, column, limit, positive_only=False):
    q = (db.query(RiderStat).join(Rider, Rider.store_id == RiderStat.store_id)
         .filter(Rider.deleted_at.is_(None)))
    if positive_only:
        q = q.filter(column > 0)
    rows = q.order_by(desc(column)).limit(limit).all()
    return rows


def mileage_leaderboard(db, limit=50):
    return [{**_rider_brief(db, rs.store_id), "total_km": round(rs.total_km or 0, 2),
             "trips": rs.trip_count} for rs in _board(db, RiderStat.total_km, limit)]


def speed_leaderboard(db, limit=50):
    return [{**_rider_brief(db, rs.store_id), "best_speed": round(rs.best_speed or 0, 1)}
            for rs in _board(db, RiderStat.best_speed, limit, positive_only=True)]


def streak_leaderboard(db, limit=50):
    return [{**_rider_brief(db, rs.store_id), "longest_streak": rs.longest_streak,
             "current_streak": rs.current_streak}
            for rs in _board(db, RiderStat.longest_streak, limit, positive_only=True)]


def gforce_leaderboard(db, limit=50):
    return [{**_rider_brief(db, rs.store_id), "best_gforce": round(rs.best_gforce or 0, 3)}
            for rs in _board(db, RiderStat.best_gforce, limit, positive_only=True)]


def daily_leaderboard(db, limit=50):
    """Highest single-day distance per rider."""
    sub = (db.query(DailyDistance.store_id.label("sid"),
                    func.max(DailyDistance.km).label("best"))
           .group_by(DailyDistance.store_id).subquery())
    rows = (db.query(sub.c.sid, sub.c.best)
            .join(Rider, Rider.store_id == sub.c.sid)
            .filter(Rider.deleted_at.is_(None))
            .order_by(desc(sub.c.best)).limit(limit).all())
    return [{**_rider_brief(db, sid), "best_day_km": round(best or 0, 2)} for sid, best in rows]


BOARDS = {
    "mileage": mileage_leaderboard,
    "speed": speed_leaderboard,
    "daily": daily_leaderboard,
    "streak": streak_leaderboard,
    "gforce": gforce_leaderboard,
}


def countries(db):
    rows = db.query(CountryStat).order_by(desc(CountryStat.total_km)).all()
    return [{"country": c.country, "total_km": round(c.total_km or 0, 2),
             "riders": c.rider_count, "avg_km_per_rider": round(c.avg_km_per_rider or 0, 2)}
            for c in rows]


def records(db):
    out = []
    for rec in db.query(Record).all():
        out.append({"key": rec.key, "value": rec.value,
                    "rider": _rider_brief(db, rec.store_id), "trip_uuid": rec.trip_uuid})
    return out


def map_cells(db, zoom: float):
    out = []
    for c in db.query(MapCell).filter(MapCell.zoom == zoom).all():
        try:
            _, la, lo = c.cell.split(":")
            lat = int(la) * zoom + zoom / 2
            lon = int(lo) * zoom + zoom / 2
        except Exception:
            continue
        out.append({"lat": round(lat, 4), "lon": round(lon, 4),
                    "rider_count": c.rider_count, "total_km": round(c.total_km or 0, 2)})
    return out


def global_summary(db):
    total_km = db.query(func.coalesce(func.sum(RiderStat.total_km), 0.0)).scalar() or 0.0
    return {
        "riders": db.query(func.count(Rider.store_id)).filter(Rider.deleted_at.is_(None)).scalar(),
        "trips": db.query(func.count(Trip.trip_uuid)).filter(Trip.validation_status == "validated").scalar(),
        "total_km": round(total_km, 1),
        "countries": db.query(func.count(CountryStat.country)).scalar(),
    }
