"""Read-only leaderboard/map/records queries over the materialized tables.
All public reads hit these precomputed tables — never raw trips."""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import desc, func

from models import CountryStat, DailyDistance, MapCell, Record, Rider, RiderStat, Trip, Wheel


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


def power_leaderboard(db, limit=50):
    return [{**_rider_brief(db, rs.store_id), "sustained_w": round(rs.best_sustained_w or 0, 0)}
            for rs in _board(db, RiderStat.best_sustained_w, limit, positive_only=True)]


def current_leaderboard(db, limit=50):
    return [{**_rider_brief(db, rs.store_id), "sustained_a": round(rs.best_sustained_a or 0, 1)}
            for rs in _board(db, RiderStat.best_sustained_a, limit, positive_only=True)]


def voltage_leaderboard(db, limit=50):
    return [{**_rider_brief(db, rs.store_id), "peak_voltage": round(rs.peak_voltage or 0, 1)}
            for rs in _board(db, RiderStat.peak_voltage, limit, positive_only=True)]


def _period_leaderboard(db, fmt, key, limit):
    """Biggest single ISO-week / calendar-month distance per rider (from daily rows)."""
    p = func.strftime(fmt, DailyDistance.date)
    per = (db.query(DailyDistance.store_id.label("sid"), p.label("p"),
                    func.sum(DailyDistance.km).label("km")).group_by("sid", "p").subquery())
    best = (db.query(per.c.sid.label("sid"), func.max(per.c.km).label("best"))
            .group_by(per.c.sid).subquery())
    rows = (db.query(best.c.sid, best.c.best).join(Rider, Rider.store_id == best.c.sid)
            .filter(Rider.deleted_at.is_(None)).order_by(desc(best.c.best)).limit(limit).all())
    return [{**_rider_brief(db, sid), key: round(b or 0, 2)} for sid, b in rows]


def week_leaderboard(db, limit=50):
    return _period_leaderboard(db, "%Y-%W", "best_week_km", limit)


def month_leaderboard(db, limit=50):
    return _period_leaderboard(db, "%Y-%m", "best_month_km", limit)


def accel_leaderboard(db, limit=50):
    """Fastest launch from a near-stop to 40 km/h (lower is better)."""
    rows = (db.query(RiderStat).join(Rider, Rider.store_id == RiderStat.store_id)
            .filter(Rider.deleted_at.is_(None), RiderStat.fastest_0_40_s.isnot(None),
                    RiderStat.fastest_0_40_s > 0)
            .order_by(RiderStat.fastest_0_40_s.asc()).limit(limit).all())
    return [{**_rider_brief(db, rs.store_id), "accel_s": round(rs.fastest_0_40_s, 2)} for rs in rows]


def ascent_leaderboard(db, limit=50):
    return [{**_rider_brief(db, rs.store_id), "ascent_m": round(rs.total_ascent_m or 0, 0)}
            for rs in _board(db, RiderStat.total_ascent_m, limit, positive_only=True)]


def range_leaderboard(db, limit=50):
    return [{**_rider_brief(db, rs.store_id), "range_km": round(rs.best_range_km or 0, 1)}
            for rs in _board(db, RiderStat.best_range_km, limit, positive_only=True)]


def efficiency_leaderboard(db, limit=50):
    """Lowest Wh/km = most efficient (ascending)."""
    rows = (db.query(RiderStat).join(Rider, Rider.store_id == RiderStat.store_id)
            .filter(Rider.deleted_at.is_(None), RiderStat.best_wh_per_km.isnot(None),
                    RiderStat.best_wh_per_km > 0)
            .order_by(RiderStat.best_wh_per_km.asc()).limit(limit).all())
    return [{**_rider_brief(db, rs.store_id), "wh_per_km": round(rs.best_wh_per_km, 1)} for rs in rows]


def steel_legs(db, limit=50):
    return [{**_rider_brief(db, rs.store_id), "hours": round((rs.total_duration_s or 0) / 3600.0, 1)}
            for rs in _board(db, RiderStat.total_duration_s, limit, positive_only=True)]


def altitude_king(db, limit=50):
    return [{**_rider_brief(db, rs.store_id), "alt_range": round(rs.best_alt_range_m or 0, 0)}
            for rs in _board(db, RiderStat.best_alt_range_m, limit, positive_only=True)]


def globe_trotter(db, limit=50):
    sub = (db.query(Trip.rider_store_id.label("sid"), func.count(func.distinct(Trip.country)).label("n"))
           .filter(Trip.validation_status == "validated", Trip.country.isnot(None), Trip.country != "")
           .group_by(Trip.rider_store_id).subquery())
    rows = (db.query(sub.c.sid, sub.c.n).join(Rider, Rider.store_id == sub.c.sid)
            .filter(Rider.deleted_at.is_(None)).order_by(sub.c.n.desc()).limit(limit).all())
    return [{**_rider_brief(db, sid), "countries": n} for sid, n in rows]


def sunday_cruiser(db, limit=50):
    """Longest ride held under 10 km/h average — calm & steady."""
    sub = (db.query(Trip.rider_store_id.label("sid"), func.max(Trip.distance_km).label("d"))
           .filter(Trip.validation_status == "validated", Trip.avg_speed > 0,
                   Trip.avg_speed < 10, Trip.distance_km > 2)
           .group_by(Trip.rider_store_id).subquery())
    rows = (db.query(sub.c.sid, sub.c.d).join(Rider, Rider.store_id == sub.c.sid)
            .filter(Rider.deleted_at.is_(None)).order_by(sub.c.d.desc()).limit(limit).all())
    return [{**_rider_brief(db, sid), "slow_km": round(d or 0, 2)} for sid, d in rows]


BOARDS = {
    "mileage": mileage_leaderboard,
    "daily": daily_leaderboard,
    "week": week_leaderboard,
    "month": month_leaderboard,
    "speed": speed_leaderboard,
    "accel": accel_leaderboard,
    "gforce": gforce_leaderboard,
    "power": power_leaderboard,
    "current": current_leaderboard,
    "voltage": voltage_leaderboard,
    "streak": streak_leaderboard,
    "ascent": ascent_leaderboard,
    "range": range_leaderboard,
    "efficiency": efficiency_leaderboard,
    "hours": steel_legs,
    "cruise": sunday_cruiser,
    "globe": globe_trotter,
    "altking": altitude_king,
}


def countries(db):
    rows = db.query(CountryStat).order_by(desc(CountryStat.total_km)).all()
    return [{"country": c.country, "total_km": round(c.total_km or 0, 2),
             "riders": c.rider_count, "avg_km_per_rider": round(c.avg_km_per_rider or 0, 2)}
            for c in rows]


def records(db):
    out = []
    for rec in db.query(Record).all():
        r = db.get(Rider, rec.store_id)
        if r is None or r.deleted_at is not None:   # skip records held by deleted/missing riders
            continue
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


def _grp_aggs():
    """Full metric set for a group (country / brand / wheel) — same dimensions as
    the rider boards so the front-end can offer the same tabs."""
    return (func.coalesce(func.sum(Trip.distance_km), 0.0),
            func.count(func.distinct(Trip.rider_store_id)),
            func.count(Trip.trip_uuid),
            func.max(Trip.max_speed), func.max(Trip.max_gforce),
            func.max(Trip.max_sustained_w), func.max(Trip.max_sustained_a),
            func.max(Trip.peak_voltage), func.min(Trip.fastest_0_40_s),
            func.coalesce(func.sum(Trip.ascent_m), 0.0), func.max(Trip.est_range_km),
            func.min(Trip.wh_per_km))


def _grp_entry(name, km, riders, trips, speed, g, w, a, v, accel, ascent, rng, whkm):
    return {"name": name, "total_km": round(km or 0, 1), "riders": riders, "trips": trips,
            "top_speed": round(speed, 1) if speed else None,
            "max_gforce": round(g, 3) if g else None,
            "sustained_w": round(w, 0) if w else None,
            "sustained_a": round(a, 1) if a else None,
            "peak_voltage": round(v, 1) if v else None,
            "accel_s": round(accel, 2) if accel else None,
            "ascent_m": round(ascent or 0, 0), "range_km": round(rng, 1) if rng else None,
            "wh_per_km": round(whkm, 1) if whkm else None}


def by_brand(db, limit=50):
    rows = (db.query(Wheel.brand, *_grp_aggs())
            .join(Trip, Trip.wheel_id == Wheel.wheel_id)
            .filter(Trip.validation_status == "validated", Wheel.brand.isnot(None), Wheel.brand != "")
            .group_by(Wheel.brand).order_by(func.sum(Trip.distance_km).desc()).limit(limit).all())
    return [_grp_entry(g, *rest) for g, *rest in rows]


def by_wheel(db, limit=50):
    rows = (db.query(Wheel.brand, Wheel.model, *_grp_aggs())
            .join(Trip, Trip.wheel_id == Wheel.wheel_id)
            .filter(Trip.validation_status == "validated", Wheel.model.isnot(None), Wheel.model != "")
            .group_by(Wheel.brand, Wheel.model).order_by(func.sum(Trip.distance_km).desc()).limit(limit).all())
    return [_grp_entry(((b or "") + " " + (m or "")).strip(), *rest) for b, m, *rest in rows]


def by_country(db, limit=50):
    rows = (db.query(Trip.country, *_grp_aggs())
            .join(Rider, Rider.store_id == Trip.rider_store_id)
            .filter(Trip.validation_status == "validated", Rider.deleted_at.is_(None),
                    Trip.country.isnot(None), Trip.country != "")
            .group_by(Trip.country).order_by(func.sum(Trip.distance_km).desc()).limit(limit).all())
    out = []
    for country, *rest in rows:
        e = _grp_entry(country, *rest)
        e["country"] = country
        e["avg"] = round(e["total_km"] / e["riders"], 1) if e["riders"] else 0
        out.append(e)
    return out


def champions(db):
    """Champion of the day/week/month by the EUC Planet Score: distance rewarded,
    boosted by top speed and time in the saddle. Computed in Python so it works
    whether start_utc is stored tz-aware or naive."""
    now = datetime.utcnow()
    windows = {"day": now - timedelta(days=1), "week": now - timedelta(days=7),
               "month": now - timedelta(days=30)}
    rows = (db.query(Trip.rider_store_id, Trip.distance_km, Trip.duration_s,
                     Trip.max_speed, Trip.start_utc)
            .join(Rider, Rider.store_id == Trip.rider_store_id)
            .filter(Trip.validation_status == "validated", Rider.deleted_at.is_(None),
                    Trip.start_utc.isnot(None)).all())
    agg = {k: {} for k in windows}   # period -> sid -> [dist, dur_s, vmax]
    for sid, dist, dur, vmax, t in rows:
        tt = t.replace(tzinfo=None) if getattr(t, "tzinfo", None) else t
        for k, since in windows.items():
            if tt >= since:
                a = agg[k].setdefault(sid, [0.0, 0.0, 0.0])
                a[0] += dist or 0.0
                a[1] += dur or 0.0
                a[2] = max(a[2], vmax or 0.0)

    def top(period):
        best = None
        for sid, (dist, dur, vmax) in agg[period].items():
            hours = dur / 3600.0
            score = dist * (1 + vmax / 100.0) * (1 + hours / 10.0)
            if best is None or score > best[1]:
                best = (sid, score, dist, hours, vmax)
        if best is None:
            return None
        sid, score, dist, hours, vmax = best
        return {**_rider_brief(db, sid), "score": round(score, 1), "km": round(dist, 2),
                "hours": round(hours, 2), "top_speed": round(vmax, 1)}

    return {"day": top("day"), "week": top("week"), "month": top("month"),
            "formula": "EUC Planet Score = km × (1 + top km/h ÷ 100) × (1 + hours ÷ 10)"}


def global_summary(db):
    total_km = db.query(func.coalesce(func.sum(RiderStat.total_km), 0.0)).scalar() or 0.0
    return {
        "riders": db.query(func.count(Rider.store_id)).filter(Rider.deleted_at.is_(None)).scalar(),
        "trips": db.query(func.count(Trip.trip_uuid)).filter(Trip.validation_status == "validated").scalar(),
        "total_km": round(total_km, 1),
        "countries": db.query(func.count(CountryStat.country)).scalar(),
    }
