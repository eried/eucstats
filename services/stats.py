"""Read-only leaderboard/map/records queries over the materialized tables.
All public reads hit these precomputed tables — never raw trips."""
from __future__ import annotations

import re
from datetime import datetime, timedelta

from sqlalchemy import case, desc, func

from models import CountryStat, DailyDistance, MapCell, Record, Rider, RiderStat, Trip, Wheel, utcnow


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
         .filter(Rider.consent_public.isnot(False)))
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
            .filter(Rider.consent_public.isnot(False))
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


def freespin_leaderboard(db, limit=50):
    return [{**_rider_brief(db, rs.store_id), "freespin_kmh": round(rs.best_freespin or 0, 1)}
            for rs in _board(db, RiderStat.best_freespin, limit, positive_only=True)]


def sag_leaderboard(db, limit=50):
    return [{**_rider_brief(db, rs.store_id), "voltage_sag": round(rs.best_voltage_sag or 0, 2)}
            for rs in _board(db, RiderStat.best_voltage_sag, limit, positive_only=True)]


def rocket_leaderboard(db, limit=50):
    return [{**_rider_brief(db, rs.store_id), "sustained_accel": round(rs.best_sustained_accel or 0, 2)}
            for rs in _board(db, RiderStat.best_sustained_accel, limit, positive_only=True)]


def cutout_leaderboard(db, limit=50):
    """Most detected cutout/overlean falls per rider (raw count)."""
    sub = (db.query(Trip.rider_store_id.label("sid"),
                    func.coalesce(func.sum(Trip.cutout_count), 0).label("v"))
           .filter(Trip.validation_status == "validated")
           .group_by(Trip.rider_store_id).having(func.sum(Trip.cutout_count) > 0).subquery())
    rows = (db.query(sub.c.sid, sub.c.v).join(Rider, Rider.store_id == sub.c.sid)
            .filter(Rider.consent_public.isnot(False)).order_by(desc(sub.c.v)).limit(limit).all())
    return [{**_rider_brief(db, sid), "cutouts": int(v or 0)} for sid, v in rows]


def _period_leaderboard(db, fmt, key, limit):
    """Biggest single ISO-week / calendar-month distance per rider (from daily rows)."""
    p = func.strftime(fmt, DailyDistance.date)
    per = (db.query(DailyDistance.store_id.label("sid"), p.label("p"),
                    func.sum(DailyDistance.km).label("km")).group_by("sid", "p").subquery())
    best = (db.query(per.c.sid.label("sid"), func.max(per.c.km).label("best"))
            .group_by(per.c.sid).subquery())
    rows = (db.query(best.c.sid, best.c.best).join(Rider, Rider.store_id == best.c.sid)
            .filter(Rider.consent_public.isnot(False)).order_by(desc(best.c.best)).limit(limit).all())
    return [{**_rider_brief(db, sid), key: round(b or 0, 2)} for sid, b in rows]


def week_leaderboard(db, limit=50):
    return _period_leaderboard(db, "%Y-%W", "best_week_km", limit)


def month_leaderboard(db, limit=50):
    return _period_leaderboard(db, "%Y-%m", "best_month_km", limit)


def accel_leaderboard(db, limit=50):
    """Fastest launch from a near-stop to 40 km/h (lower is better)."""
    rows = (db.query(RiderStat).join(Rider, Rider.store_id == RiderStat.store_id)
            .filter(Rider.consent_public.isnot(False), RiderStat.fastest_0_40_s.isnot(None),
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
            .filter(Rider.consent_public.isnot(False), RiderStat.best_wh_per_km.isnot(None),
                    RiderStat.best_wh_per_km > 0)
            .order_by(RiderStat.best_wh_per_km.asc()).limit(limit).all())
    return [{**_rider_brief(db, rs.store_id), "wh_per_km": round(rs.best_wh_per_km, 1)} for rs in rows]


def steel_legs(db, limit=50):
    # "hours on the wheel" = real moving time (>2 km/h), not the whole logging session
    return [{**_rider_brief(db, rs.store_id), "hours": round((rs.total_moving_s or 0) / 3600.0, 1)}
            for rs in _board(db, RiderStat.total_moving_s, limit, positive_only=True)]


def altitude_king(db, limit=50):
    return [{**_rider_brief(db, rs.store_id), "alt_range": round(rs.best_alt_range_m or 0, 0)}
            for rs in _board(db, RiderStat.best_alt_range_m, limit, positive_only=True)]


def globe_trotter(db, limit=50):
    sub = (db.query(Trip.rider_store_id.label("sid"), func.count(func.distinct(Trip.country)).label("n"))
           .filter(Trip.validation_status == "validated", Trip.country.isnot(None), Trip.country != "")
           .group_by(Trip.rider_store_id).subquery())
    rows = (db.query(sub.c.sid, sub.c.n).join(Rider, Rider.store_id == sub.c.sid)
            .filter(Rider.consent_public.isnot(False)).order_by(sub.c.n.desc()).limit(limit).all())
    return [{**_rider_brief(db, sid), "countries": n} for sid, n in rows]


def sunday_cruiser(db, limit=50):
    """Longest ride held under 10 km/h average — calm & steady."""
    sub = (db.query(Trip.rider_store_id.label("sid"), func.max(Trip.distance_km).label("d"))
           .filter(Trip.validation_status == "validated", Trip.avg_speed > 0,
                   Trip.avg_speed < 10, Trip.distance_km > 2)
           .group_by(Trip.rider_store_id).subquery())
    rows = (db.query(sub.c.sid, sub.c.d).join(Rider, Rider.store_id == sub.c.sid)
            .filter(Rider.consent_public.isnot(False)).order_by(sub.c.d.desc()).limit(limit).all())
    return [{**_rider_brief(db, sid), "slow_km": round(d or 0, 2)} for sid, d in rows]


# --- extra fun boards, computed straight from trips (no extra materialized columns) ---

def _trip_max_board(db, col, key, limit, transform, flt=None):
    sub = (db.query(Trip.rider_store_id.label("sid"), func.max(col).label("v"))
           .filter(Trip.validation_status == "validated", col.isnot(None)))
    if flt is not None:
        sub = sub.filter(flt)
    sub = sub.group_by(Trip.rider_store_id).subquery()
    rows = (db.query(sub.c.sid, sub.c.v).join(Rider, Rider.store_id == sub.c.sid)
            .filter(Rider.consent_public.isnot(False)).order_by(desc(sub.c.v)).limit(limit).all())
    return [{**_rider_brief(db, sid), key: transform(v)} for sid, v in rows]


def _trip_count_board(db, key, limit, flt=None):
    sub = (db.query(Trip.rider_store_id.label("sid"), func.count(Trip.trip_uuid).label("v"))
           .filter(Trip.validation_status == "validated"))
    if flt is not None:
        sub = sub.filter(flt)
    sub = sub.group_by(Trip.rider_store_id).subquery()
    rows = (db.query(sub.c.sid, sub.c.v).join(Rider, Rider.store_id == sub.c.sid)
            .filter(Rider.consent_public.isnot(False)).order_by(desc(sub.c.v)).limit(limit).all())
    return [{**_rider_brief(db, sid), key: int(n or 0)} for sid, n in rows]


def frequent_flyer(db, limit=50):
    return _trip_count_board(db, "trips_total", limit)


def night_rider(db, limit=50):
    hh = func.strftime("%H", Trip.start_utc)
    return _trip_count_board(db, "night_rides", limit,
                             flt=(Trip.start_utc.isnot(None)) & hh.in_(["22", "23", "00", "01", "02", "03", "04"]))


def marathoner(db, limit=50):
    # longest ride by MOVING time (>2 km/h); old trips w/o moving_s fall back to wall-clock
    col = func.coalesce(Trip.moving_s, Trip.duration_s)
    return _trip_max_board(db, col, "ride_hours", limit,
                           transform=lambda v: round((v or 0) / 3600.0, 2), flt=Trip.duration_s > 0)


def pace_maker(db, limit=50):
    return _trip_max_board(db, Trip.avg_speed, "avg_speed", limit,
                           transform=lambda v: round(v or 0, 1), flt=Trip.avg_speed > 0)


def battery_vampire(db, limit=50):
    return _trip_max_board(db, Trip.battery_used_pct, "batt_pct", limit,
                           transform=lambda v: round(v or 0, 1), flt=Trip.battery_used_pct > 0)


def weekend_warrior(db, limit=50):
    dow = func.strftime("%w", Trip.start_utc)  # 0=Sun .. 6=Sat
    sub = (db.query(Trip.rider_store_id.label("sid"), func.sum(Trip.distance_km).label("v"))
           .filter(Trip.validation_status == "validated", Trip.start_utc.isnot(None), dow.in_(["0", "6"]))
           .group_by(Trip.rider_store_id).subquery())
    rows = (db.query(sub.c.sid, sub.c.v).join(Rider, Rider.store_id == sub.c.sid)
            .filter(Rider.consent_public.isnot(False)).order_by(desc(sub.c.v)).limit(limit).all())
    return [{**_rider_brief(db, sid), "weekend_km": round(v or 0, 2)} for sid, v in rows]


def early_bird(db, limit=50):
    hh = func.strftime("%H", Trip.start_utc)
    return _trip_count_board(db, "morning_rides", limit,
                             flt=(Trip.start_utc.isnot(None)) & hh.in_(["05", "06", "07", "08"]))


def peak_bagger(db, limit=50):
    return _trip_max_board(db, Trip.ascent_m, "peak_ascent", limit,
                           transform=lambda v: round(v or 0, 0), flt=Trip.ascent_m > 0)


def power_plant(db, limit=50):
    energy = func.sum(Trip.wh_per_km * Trip.distance_km)
    sub = (db.query(Trip.rider_store_id.label("sid"), energy.label("v"))
           .filter(Trip.validation_status == "validated", Trip.wh_per_km.isnot(None), Trip.distance_km > 0)
           .group_by(Trip.rider_store_id).subquery())
    rows = (db.query(sub.c.sid, sub.c.v).join(Rider, Rider.store_id == sub.c.sid)
            .filter(Rider.consent_public.isnot(False)).order_by(desc(sub.c.v)).limit(limit).all())
    return [{**_rider_brief(db, sid), "energy_kwh": round((v or 0) / 1000.0, 1)} for sid, v in rows]


def explorer(db, limit=50):
    sub = (db.query(Trip.rider_store_id.label("sid"), func.count(func.distinct(Trip.start_cell)).label("v"))
           .filter(Trip.validation_status == "validated", Trip.start_cell.isnot(None))
           .group_by(Trip.rider_store_id).subquery())
    rows = (db.query(sub.c.sid, sub.c.v).join(Rider, Rider.store_id == sub.c.sid)
            .filter(Rider.consent_public.isnot(False)).order_by(desc(sub.c.v)).limit(limit).all())
    return [{**_rider_brief(db, sid), "areas": int(v or 0)} for sid, v in rows]


def big_day(db, limit=50):
    day = func.date(Trip.start_utc)
    per = (db.query(Trip.rider_store_id.label("sid"), day.label("d"), func.count(Trip.trip_uuid).label("c"))
           .filter(Trip.validation_status == "validated", Trip.start_utc.isnot(None))
           .group_by(Trip.rider_store_id, day).subquery())
    best = (db.query(per.c.sid.label("sid"), func.max(per.c.c).label("v")).group_by(per.c.sid).subquery())
    rows = (db.query(best.c.sid, best.c.v).join(Rider, Rider.store_id == best.c.sid)
            .filter(Rider.consent_public.isnot(False)).order_by(desc(best.c.v)).limit(limit).all())
    return [{**_rider_brief(db, sid), "rides_in_day": int(v or 0)} for sid, v in rows]


def commuter(db, limit=50):
    dow = func.strftime("%w", Trip.start_utc)  # weekdays Mon-Fri = 1..5
    sub = (db.query(Trip.rider_store_id.label("sid"), func.sum(Trip.distance_km).label("v"))
           .filter(Trip.validation_status == "validated", Trip.start_utc.isnot(None),
                   dow.in_(["1", "2", "3", "4", "5"]))
           .group_by(Trip.rider_store_id).subquery())
    rows = (db.query(sub.c.sid, sub.c.v).join(Rider, Rider.store_id == sub.c.sid)
            .filter(Rider.consent_public.isnot(False)).order_by(desc(sub.c.v)).limit(limit).all())
    return [{**_rider_brief(db, sid), "weekday_km": round(v or 0, 2)} for sid, v in rows]


def gated_leaderboard(db, col, direction, min_s, min_km, limit=50):
    """Live max/min of a per-trip column over QUALIFYING trips (ride >= min_s seconds
    AND >= min_km km). Anti-gaming gate for spikeable / fakeable metrics. Excludes
    banned / self-deleted / opted-out riders."""
    from services.aggregator import _excluded_ids
    import services.settings as settings
    colattr = getattr(Trip, col)
    # null this column for trips whose metric is flagged invalid for their wheel model
    blk = settings.blocked_trip_uuids(db).get(settings.WHEEL_FIELD_METRIC.get(col), set())
    masked = case((Trip.trip_uuid.in_(blk), None), else_=colattr) if blk else colattr
    agg = func.min(masked) if direction == "min" else func.max(masked)
    q = (db.query(Trip.rider_store_id.label("sid"), agg.label("v"))
         .join(Rider, Rider.store_id == Trip.rider_store_id)
         .filter(Trip.validation_status == "validated",
                 Rider.consent_public.isnot(False), colattr.isnot(None)))
    if min_s:
        q = q.filter(Trip.duration_s >= min_s)
    if min_km:
        q = q.filter(Trip.distance_km >= min_km)
    excl = _excluded_ids(db)
    if excl:
        q = q.filter(~Trip.rider_store_id.in_(excl))
    sub = q.group_by(Trip.rider_store_id).subquery()
    order = sub.c.v.asc() if direction == "min" else desc(sub.c.v)
    rows = (db.query(sub.c.sid, sub.c.v).filter(sub.c.v.isnot(None))
            .order_by(order).limit(limit).all())
    return [{**_rider_brief(db, sid), "v": round(v, 2)} for sid, v in rows]


BOARDS = {
    "mileage": mileage_leaderboard,
    "daily": daily_leaderboard,
    "week": week_leaderboard,
    "month": month_leaderboard,
    "speed": speed_leaderboard,
    "accel": accel_leaderboard,
    "gforce": gforce_leaderboard,
    "voltage": voltage_leaderboard,
    "streak": streak_leaderboard,
    "ascent": ascent_leaderboard,
    "range": range_leaderboard,
    "efficiency": efficiency_leaderboard,
    "hours": steel_legs,
    "cruise": sunday_cruiser,
    "globe": globe_trotter,
    "altking": altitude_king,
    "frequent": frequent_flyer,
    "marathon": marathoner,
    "pace": pace_maker,
    "night": night_rider,
    "weekend": weekend_warrior,
    "early": early_bird,
    "peak": peak_bagger,
    "energy": power_plant,
    "explorer": explorer,
    "bigday": big_day,
    "commuter": commuter,
    "freespin": freespin_leaderboard,
    "cutouts": cutout_leaderboard,
}


def _register_gated_boards():
    import services.settings as _s
    for b in _s.gated_boards() + _s.ungated_new_boards():
        col, d, ms, mk = b["col"], b["dir"], b["min_s"], b["min_km"]
        BOARDS[b["k"]] = (lambda db, limit=50, col=col, d=d, ms=ms, mk=mk:
                          gated_leaderboard(db, col, d, ms, mk, limit))


_register_gated_boards()


def countries(db):
    rows = db.query(CountryStat).order_by(desc(CountryStat.total_km)).all()
    return [{"country": c.country, "total_km": round(c.total_km or 0, 2),
             "riders": c.rider_count, "avg_km_per_rider": round(c.avg_km_per_rider or 0, 2)}
            for c in rows]


def records(db):
    out = []
    for rec in db.query(Record).all():
        r = db.get(Rider, rec.store_id)
        if r is None or r.consent_public is False:  # skip purged + opted-out riders
            continue
        out.append({"key": rec.key, "value": rec.value,
                    "rider": _rider_brief(db, rec.store_id), "trip_uuid": rec.trip_uuid})
    return out


def map_cells(db, zoom: float):
    import services.settings as settings
    floor = settings.get_heatmap(db)["floor"]      # privacy: hide cells with < floor distinct riders
    out = []
    for c in db.query(MapCell).filter(MapCell.zoom == zoom,
                                      MapCell.rider_count >= floor).all():
        try:
            _, la, lo = c.cell.split(":")
            lat = int(la) * zoom + zoom / 2
            lon = int(lo) * zoom + zoom / 2
        except Exception:
            continue
        out.append({"lat": round(lat, 4), "lon": round(lon, 4),
                    "rider_count": c.rider_count, "total_km": round(c.total_km or 0, 2)})
    return out


def _mask(col, metric, blocked):
    """Null `col` for trips whose `metric` is invalid per the wheel data-quality rules."""
    ids = (blocked or {}).get(metric)
    return case((Trip.trip_uuid.in_(ids), None), else_=col) if ids else col


def _grp_aggs(blocked=None):
    """Full metric set for a group (country / brand / wheel) — same dimensions as the rider
    boards. `blocked` (from settings.blocked_trip_uuids) nulls metrics flagged invalid for a
    wheel model so a bad channel doesn't poison the group standings."""
    return (func.coalesce(func.sum(Trip.distance_km), 0.0),
            func.count(func.distinct(Trip.rider_store_id)),
            func.count(Trip.trip_uuid),
            func.max(_mask(Trip.max_speed, "speed", blocked)),
            func.max(_mask(Trip.max_gforce, "gforce", blocked)),
            func.max(_mask(Trip.max_sustained_w, "power", blocked)),
            func.max(_mask(Trip.max_sustained_a, "current", blocked)),
            func.max(_mask(Trip.peak_voltage, "voltage", blocked)),
            func.min(_mask(Trip.fastest_0_40_s, "accel", blocked)),
            func.coalesce(func.sum(_mask(Trip.ascent_m, "altitude", blocked)), 0.0),
            func.max(_mask(Trip.est_range_km, "range", blocked)),
            func.min(_mask(Trip.wh_per_km, "efficiency", blocked)),
            func.max(_mask(Trip.ascent_m, "altitude", blocked)),       # biggest single climb
            func.max(_mask(Trip.max_altitude_m, "altitude", blocked)),  # highest point reached
            func.max(_mask(Trip.max_temp, "temp", blocked)),            # hottest the board ran
            func.coalesce(func.sum(Trip.cutout_count), 0))              # total cutout/overlean falls


def _grp_entry(name, km, riders, trips, speed, g, w, a, v, accel, ascent, rng, whkm,
               climb, alt, temp, cutouts):
    km = km or 0
    return {"name": name, "total_km": round(km, 1), "riders": riders, "trips": trips,
            "top_speed": round(speed, 1) if speed else None,
            "max_gforce": round(g, 3) if g else None,
            "sustained_w": round(w, 0) if w else None,
            "sustained_a": round(a, 1) if a else None,
            "peak_voltage": round(v, 1) if v else None,
            "accel_s": round(accel, 2) if accel else None,
            "ascent_m": round(ascent or 0, 0), "range_km": round(rng, 1) if rng else None,
            "wh_per_km": round(whkm, 1) if whkm else None,
            "climb_m": round(climb, 0) if climb else None,
            "max_alt": round(alt, 0) if alt is not None else None,
            "max_temp": round(temp, 1) if temp else None,
            "cutouts": int(cutouts or 0),
            "cutout_rate": round((cutouts or 0) / km * 1000, 2) if km >= 1 else None}


def by_brand(db, limit=50):
    import services.settings as settings
    blk = settings.blocked_trip_uuids(db)
    rows = (db.query(Wheel.brand, *_grp_aggs(blk))
            .join(Trip, Trip.wheel_id == Wheel.wheel_id)
            .filter(Trip.validation_status == "validated", Wheel.brand.isnot(None), Wheel.brand != "")
            .group_by(Wheel.brand).order_by(func.sum(Trip.distance_km).desc()).limit(limit).all())
    return [_grp_entry(g, *rest) for g, *rest in rows]


def _wheel_name(brand, model):
    """Model name without a redundant leading brand ('Inmotion Inmotion V14' -> 'V14')."""
    b, m = (brand or "").strip(), (model or "").strip()
    if b and m.lower().startswith(b.lower()):
        m = m[len(b):].strip(" -·") or m
    return m or b


def by_wheel(db, limit=50):
    import services.settings as settings
    blk = settings.blocked_trip_uuids(db)
    rows = (db.query(Wheel.brand, Wheel.model, *_grp_aggs(blk))
            .join(Trip, Trip.wheel_id == Wheel.wheel_id)
            .filter(Trip.validation_status == "validated", Wheel.model.isnot(None), Wheel.model != "")
            .group_by(Wheel.brand, Wheel.model).order_by(func.sum(Trip.distance_km).desc()).limit(limit).all())
    out = []
    for b, m, *rest in rows:
        e = _grp_entry(_wheel_name(b, m), *rest)
        e["brand"] = b
        out.append(e)
    return out


def by_country(db, limit=50):
    import services.settings as settings
    blk = settings.blocked_trip_uuids(db)
    rows = (db.query(Trip.country, *_grp_aggs(blk))
            .join(Rider, Rider.store_id == Trip.rider_store_id)
            .filter(Trip.validation_status == "validated", Rider.consent_public.isnot(False),
                    Trip.country.isnot(None), Trip.country != "")
            .group_by(Trip.country).order_by(func.sum(Trip.distance_km).desc()).limit(limit).all())
    coords = {c: (la, lo) for c, la, lo in
              db.query(Trip.country, func.avg(Trip.start_lat), func.avg(Trip.start_lon))
              .filter(Trip.validation_status == "validated", Trip.start_lat.isnot(None),
                      Trip.country.isnot(None), Trip.country != "")
              .group_by(Trip.country).all()}
    out = []
    for country, *rest in rows:
        e = _grp_entry(country, *rest)
        e["country"] = country
        e["avg"] = round(e["total_km"] / e["riders"], 1) if e["riders"] else 0
        la, lo = coords.get(country, (None, None))
        e["lat"] = round(la, 3) if la is not None else None    # where this country's riders ride
        e["lon"] = round(lo, 3) if lo is not None else None
        out.append(e)
    return out


def _score_formula(cfg) -> str:
    """Human-readable EUC Planet Score formula for the given config."""
    de = cfg["dist_exp"]
    base = "km" if abs(de - 1.0) < 1e-9 else f"km^{de:g}"
    parts = [base]
    if cfg["speed_on"]:
        parts.append(f"(1 + top km/h ÷ {cfg['speed_div']:g})")
    if cfg["hours_on"]:
        parts.append(f"(1 + hours ÷ {cfg['hours_div']:g})")
    return "EUC Planet Score = " + " × ".join(parts)


def champions(db, cfg=None):
    """Champion of the day/week/month by the EUC Planet Score. Distance is the base
    (raised to dist_exp), boosted by top speed and real ride time per the admin's
    tunable config. Pass `cfg` to evaluate a candidate formula without saving it.
    Computed in Python so it works whether start_utc is tz-aware or naive."""
    import services.settings as settings
    if cfg is None:
        cfg = settings.get_score_config(db)
    now = utcnow()
    windows = {"day": now - timedelta(days=1), "week": now - timedelta(days=7),
               "month": now - timedelta(days=30)}
    rows = (db.query(Trip.rider_store_id, Trip.distance_km, Trip.moving_s, Trip.duration_s,
                     Trip.max_speed, Trip.start_utc)
            .join(Rider, Rider.store_id == Trip.rider_store_id)
            .filter(Trip.validation_status == "validated", Rider.consent_public.isnot(False),
                    Trip.start_utc.isnot(None)).all())
    agg = {k: {} for k in windows}   # period -> sid -> [dist, moving_s, vmax]
    for sid, dist, mov, dur, vmax, t in rows:
        secs = mov if mov is not None else (dur or 0.0)   # real ride time (fallback wall-clock)
        tt = t.replace(tzinfo=None) if getattr(t, "tzinfo", None) else t
        for k, since in windows.items():
            if tt >= since:
                a = agg[k].setdefault(sid, [0.0, 0.0, 0.0])
                a[0] += dist or 0.0
                a[1] += secs
                a[2] = max(a[2], vmax or 0.0)

    de, sd, hd = cfg["dist_exp"], cfg["speed_div"], cfg["hours_div"]
    son, hon = cfg["speed_on"], cfg["hours_on"]

    def score_of(dist, hours, vmax):
        s = (dist ** de) if dist > 0 else 0.0
        if son:
            s *= (1 + vmax / sd)
        if hon:
            s *= (1 + hours / hd)
        return s

    def top(period):
        best = None
        for sid, (dist, secs, vmax) in agg[period].items():
            hours = secs / 3600.0
            s = score_of(dist, hours, vmax)
            if best is None or s > best[1]:
                best = (sid, s, dist, hours, vmax)
        if best is None:
            return None
        sid, s, dist, hours, vmax = best
        return {**_rider_brief(db, sid), "score": round(s, 1), "km": round(dist, 2),
                "hours": round(hours, 2), "top_speed": round(vmax, 1)}

    return {"day": top("day"), "week": top("week"), "month": top("month"),
            "formula": _score_formula(cfg)}


ANDROID = {36: "Android 16", 35: "Android 15", 34: "Android 14", 33: "Android 13",
           32: "Android 12L", 31: "Android 12", 30: "Android 11", 29: "Android 10",
           28: "Android 9", 27: "Android 8.1", 26: "Android 8", 25: "Android 7.1"}


def _vkey(v):
    """Sortable semver-ish key from a version string ('0.9.13' -> (0,9,13))."""
    return tuple(int(x) for x in re.findall(r"\d+", str(v or ""))) or (0,)


def version_stats(db):
    """App / OS adoption from app_version + free-form os_version (the app sends no
    device/SDK object). Representative per rider = their latest validated trip."""
    rows = (db.query(Trip.rider_store_id, Trip.app_version, Trip.meta_json)
            .join(Rider, Rider.store_id == Trip.rider_store_id)
            .filter(Trip.validation_status == "validated", Rider.consent_public.isnot(False))
            .order_by(Trip.start_utc.asc()).all())
    rep = {}
    for sid, appv, mj in rows:
        osv = mj.get("os_version") if isinstance(mj, dict) else None
        rep[sid] = {"app": appv, "os": osv}     # ordered asc -> latest trip wins
    appc, osc = {}, {}
    for d in rep.values():
        if d["app"]:
            appc[d["app"]] = appc.get(d["app"], 0) + 1
        if d["os"]:
            osc[d["os"]] = osc.get(d["os"], 0) + 1
    with_app = [(s, d["app"]) for s, d in rep.items() if d["app"]]
    newest = max((v for _, v in with_app), key=_vkey, default=None)
    on_latest = sum(1 for _, v in with_app if v == newest)
    latest_pct = round(100 * on_latest / len(with_app)) if with_app else 0
    adopters = [{**_rider_brief(db, s), "ver": v}
                for s, v in sorted(with_app, key=lambda x: _vkey(x[1]), reverse=True)[:8]]
    laggards = [{**_rider_brief(db, s), "ver": v}
                for s, v in sorted(with_app, key=lambda x: _vkey(x[1]))[:8]]
    cv = {}   # country flag -> newest app version + rider count
    for sid, d in rep.items():
        r = db.get(Rider, sid)
        f = r.flag if r else None
        if not f:
            continue
        e = cv.setdefault(f, {"riders": 0, "ver": None})
        e["riders"] += 1
        if d["app"] and (e["ver"] is None or _vkey(d["app"]) > _vkey(e["ver"])):
            e["ver"] = d["app"]
    countries = sorted([{"country": f, "riders": c["riders"], "version": c["ver"]}
                        for f, c in cv.items() if c["ver"]],
                       key=lambda x: _vkey(x["version"]), reverse=True)
    return {
        "latest": newest, "latest_pct": latest_pct,
        "adopters": adopters, "laggards": laggards,
        "appvers": sorted([{"version": v, "riders": n} for v, n in appc.items()], key=lambda x: -x["riders"]),
        "osvers": sorted([{"version": v, "riders": n} for v, n in osc.items()], key=lambda x: -x["riders"]),
        "countries": countries,
    }


def _rider_rank(db, column, value):
    """1-based rank of `value` among non-deleted riders by `column` (None if unranked)."""
    if not value or value <= 0:
        return None
    better = (db.query(func.count())
              .select_from(RiderStat).join(Rider, Rider.store_id == RiderStat.store_id)
              .filter(Rider.consent_public.isnot(False), column > value).scalar())
    return int(better or 0) + 1


def rider_card(db, store_id):
    """Personal stats card for a rider. Returns zeros (not 404) for a registered
    rider with no validated trips yet, so the app never shows 'couldn't load'."""
    import services.settings as settings
    r = db.get(Rider, store_id)
    if r is None or r.deleted_at is not None:   # purged or self-closed accounts: no card
        return None
    rs = db.get(RiderStat, store_id)
    countries = (db.query(func.count(func.distinct(Trip.country)))
                 .filter(Trip.rider_store_id == store_id, Trip.validation_status == "validated",
                         Trip.country.isnot(None), Trip.country != "").scalar()) or 0

    def z(attr):
        return (getattr(rs, attr) or 0) if rs else 0

    return {
        "store_id": store_id,
        "display_name": r.display_name,
        "flag": r.flag,
        "has_avatar": r.avatar_png is not None,
        "banned": settings.is_banned(db, store_id),       # app shows a suspension notice on profile load
        "ban_reason": settings.ban_reason(db, store_id),  # human-readable; null when not banned
        "stats": {
            "total_km": round(z("total_km"), 2),
            "trips": int(z("trip_count")),
            "best_speed_kmh": round(z("best_speed"), 1),
            "best_gforce": round(z("best_gforce"), 3),
            "longest_trip_km": round(z("longest_trip_km"), 2),
            "total_ascent_m": round(z("total_ascent_m")),
            "hours": round(z("total_duration_s") / 3600.0, 1),
            "current_streak": int(z("current_streak")),
            "longest_streak": int(z("longest_streak")),
            "countries": int(countries),
            "last_ride": rs.last_ride_date.isoformat() if (rs and rs.last_ride_date) else None,
        },
        "ranks": {
            "distance": _rider_rank(db, RiderStat.total_km, rs.total_km if rs else None),
            "speed": _rider_rank(db, RiderStat.best_speed, rs.best_speed if rs else None),
        },
    }


FACTORIES = {   # approximate EUC maker HQs (editable; admin manager is a follow-up)
    "Begode": (113.75, 23.02, "Dongguan, China"),
    "Gotway": (113.75, 23.02, "Dongguan, China"),          # former name of Begode
    "ExtremeBull": (113.12, 23.02, "Guangzhou, China"),
    "Veteran": (114.06, 22.54, "Shenzhen, China"),
    "LeaperKim": (114.06, 22.54, "Shenzhen, China"),       # maker of Veteran
    "InMotion": (114.00, 22.62, "Shenzhen, China"),
    "KingSong": (114.12, 22.50, "Shenzhen, China"),
    "Ninebot": (116.40, 39.90, "Beijing, China"),          # Segway-Ninebot
    "Segway": (116.40, 39.90, "Beijing, China"),
    "IPS": (114.06, 22.55, "Shenzhen, China"),
    "Rockwheel": (114.10, 22.58, "Shenzhen, China"),
    "GotwayMSuper": (113.75, 23.02, "Dongguan, China"),
    "Solowheel": (-122.40, 45.59, "Camas, WA, USA"),       # Inventist (original Solowheel)
    "Inventist": (-122.40, 45.59, "Camas, WA, USA"),
    "Airwheel": (120.62, 31.30, "Wuxi, China"),
    "Ninebot-Segway": (116.40, 39.90, "Beijing, China"),
}


def _factory(brand):
    """Case/spacing-insensitive factory lookup (wheel brand strings vary)."""
    if not brand:
        return None
    key = re.sub(r"[^a-z0-9]", "", str(brand).lower())
    if not key:
        return None
    for name, loc in FACTORIES.items():
        if re.sub(r"[^a-z0-9]", "", name.lower()) == key:
            return loc
    return None


def brand_flow(db, brand):
    """Factory HQ + the country hotspots where a brand's wheels are ridden (for the
    animated 'flow of wheels' map effect)."""
    fac = _factory(brand)
    rows = (db.query(Trip.country, func.avg(Trip.start_lat), func.avg(Trip.start_lon),
                     func.count(Trip.trip_uuid))
            .join(Wheel, Wheel.wheel_id == Trip.wheel_id)
            .filter(Trip.validation_status == "validated", Wheel.brand == brand,
                    Trip.start_lat.isnot(None), Trip.country.isnot(None))
            .group_by(Trip.country).order_by(func.count(Trip.trip_uuid).desc()).limit(40).all())
    points = [{"lon": round(lon, 4), "lat": round(lat, 4), "country": ct, "trips": n}
              for ct, lat, lon, n in rows if lat is not None and lon is not None]
    return {"brand": brand,
            "factory": ({"lon": fac[0], "lat": fac[1], "name": fac[2]} if fac else None),
            "points": points}


def global_summary(db):
    from services.aggregator import _excluded_ids
    excluded = _excluded_ids(db)          # banned + self-deleted, kept out of public counts
    pub = Rider.consent_public.isnot(False)
    # total_km: only consent-public riders' totals — excludes deleted (consent cleared on
    # close) and opted-out riders; banned riders have no RiderStat row after a rebuild.
    total_km = (db.query(func.coalesce(func.sum(RiderStat.total_km), 0.0))
                .join(Rider, Rider.store_id == RiderStat.store_id).filter(pub).scalar()) or 0.0
    riders_q = db.query(func.count(Rider.store_id)).filter(pub)
    trips_q = db.query(func.count(Trip.trip_uuid)).filter(Trip.validation_status == "validated")
    if excluded:
        riders_q = riders_q.filter(~Rider.store_id.in_(excluded))
        trips_q = trips_q.filter(~Trip.rider_store_id.in_(excluded))
    return {
        "riders": riders_q.scalar(),
        "trips": trips_q.scalar(),
        "total_km": round(total_km, 1),
        "countries": db.query(func.count(CountryStat.country)).scalar(),
    }
