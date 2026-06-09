"""Apply a validated trip to the materialized tables (idempotent per trip)."""
from __future__ import annotations

from models import DailyDistance, utcnow
from repository.aggregates import AggregateRepo


class Aggregator:
    def __init__(self, db):
        self.db = db
        self.agg = AggregateRepo(db)
        self._rules = None          # per-wheel data-quality rules (lazy, cached per instance)

    def _suppressed(self, trip) -> set:
        """Trip fields to ignore for this trip per the wheel data-quality rules
        (e.g. a model that reports bad voltage). Empty when no rule applies."""
        if self._rules is None:
            import services.settings as settings
            self._rules = settings.get_wheel_rules(self.db)
        if not self._rules:
            return set()
        from models import Wheel
        import services.settings as settings
        w = self.db.get(Wheel, trip.wheel_id) if trip.wheel_id else None
        if not w:
            return set()
        return settings.suppressed_fields(w.brand, w.model, trip.app_version, self._rules)

    def apply(self, trip) -> None:
        if trip.validation_status != "validated" or trip.aggregated:
            return
        store = trip.rider_store_id
        dist = trip.distance_km or 0.0
        when = trip.created_at or utcnow()

        sup = self._suppressed(trip)
        def mv(field):                 # masked value: None if this metric is invalid for this wheel
            return None if field in sup else getattr(trip, field)

        rs = self.agg.get_rider_stat(store)
        rs.total_km = (rs.total_km or 0.0) + dist
        rs.trip_count = (rs.trip_count or 0) + 1
        if mv("max_speed") is not None:
            rs.best_speed = max(rs.best_speed or 0.0, mv("max_speed"))
        if mv("max_gforce") is not None:
            rs.best_gforce = max(rs.best_gforce or 0.0, mv("max_gforce"))
        if mv("max_sustained_w") is not None:
            rs.best_sustained_w = max(rs.best_sustained_w or 0.0, mv("max_sustained_w"))
        if mv("max_sustained_a") is not None:
            rs.best_sustained_a = max(rs.best_sustained_a or 0.0, mv("max_sustained_a"))
        if mv("peak_voltage") is not None:
            rs.peak_voltage = max(rs.peak_voltage or 0.0, mv("peak_voltage"))
        if mv("fastest_0_40_s") is not None and mv("fastest_0_40_s") > 0:
            rs.fastest_0_40_s = (mv("fastest_0_40_s") if rs.fastest_0_40_s is None
                                 else min(rs.fastest_0_40_s, mv("fastest_0_40_s")))
        rs.longest_trip_km = max(rs.longest_trip_km or 0.0, dist)
        if mv("ascent_m"):
            rs.total_ascent_m = (rs.total_ascent_m or 0.0) + mv("ascent_m")
        rs.total_duration_s = (rs.total_duration_s or 0.0) + (trip.duration_s or 0.0)
        if mv("alt_range_m"):
            rs.best_alt_range_m = max(rs.best_alt_range_m or 0.0, mv("alt_range_m"))
        if mv("est_range_km"):
            rs.best_range_km = max(rs.best_range_km or 0.0, mv("est_range_km"))
        if mv("wh_per_km") and (trip.distance_km or 0) >= 2:
            rs.best_wh_per_km = (mv("wh_per_km") if rs.best_wh_per_km is None
                                 else min(rs.best_wh_per_km, mv("wh_per_km")))
        if mv("max_freespin"):
            rs.best_freespin = max(rs.best_freespin or 0.0, mv("max_freespin"))
        if mv("max_voltage_sag"):
            rs.best_voltage_sag = max(rs.best_voltage_sag or 0.0, mv("max_voltage_sag"))
        if mv("sustained_accel"):
            rs.best_sustained_accel = max(rs.best_sustained_accel or 0.0, mv("sustained_accel"))

        if trip.start_utc:
            self.agg.add_daily(store, trip.start_utc.date(), dist)
            self._recompute_streak(rs, store)

        if trip.country:
            self.agg.recompute_country(trip.country)

        if trip.start_lat is not None and trip.start_lon is not None:
            self._bump_cells(trip, store, dist, when)

        self.agg.set_record_if_better("mileage_king", store, rs.total_km, trip.trip_uuid, when)
        self.agg.set_record_if_better("top_speed", store, mv("max_speed"), trip.trip_uuid, when)
        self.agg.set_record_if_better("max_gforce", store, mv("max_gforce"), trip.trip_uuid, when)
        self.agg.set_record_if_better("longest_trip", store, dist, trip.trip_uuid, when)
        self.agg.set_record_if_better("sustained_w", store, mv("max_sustained_w"), trip.trip_uuid, when)
        self.agg.set_record_if_better("sustained_a", store, mv("max_sustained_a"), trip.trip_uuid, when)
        self.agg.set_record_if_better("peak_voltage", store, mv("peak_voltage"), trip.trip_uuid, when)

        trip.aggregated = True
        self.db.commit()

    def _route_points(self, trip):
        """(lat, lon) points along the trip from its stored track; [] if none."""
        from ingest.downsample import decode_track
        from models import TripTrack
        tt = self.db.get(TripTrack, trip.trip_uuid)
        if not tt or not tt.points:
            return []
        try:
            return [(r[1], r[2]) for r in decode_track(tt.points)
                    if r[1] is not None and r[2] is not None]
        except Exception:
            return []

    def _bump_cells(self, trip, store, dist, when) -> None:
        """Mark the grid cells this trip occupies. In 'route' mode the whole GPS path
        is spread across cells (distance shared evenly); in 'start' mode only the start
        cell. Distinct riders per cell drive the public heatmap weight."""
        import services.settings as settings
        from ingest.geo import cell_id
        zooms = settings.heatmap_zooms(self.db)
        mode = settings.get_heatmap(self.db)["route_mode"]
        pts = self._route_points(trip) if mode == "route" else []
        if not pts:
            pts = [(trip.start_lat, trip.start_lon)]
        for z in zooms:
            cells = {cell_id(la, lo, z) for la, lo in pts}
            cells.discard(None)
            if not cells:
                continue
            share = dist / len(cells)          # split distance so per-cell totals stay sane
            for cid in cells:
                self.agg.bump_map_cell(z, cid, store, share, when)

    def _recompute_streak(self, rs, store) -> None:
        """Recompute streaks from the rider's daily rows — robust to out-of-order
        backfill (unlike an incremental counter)."""
        self.db.flush()  # make the just-added daily row visible (autoflush is off)
        dates = sorted(
            d for (d,) in self.db.query(DailyDistance.date)
            .filter(DailyDistance.store_id == store).all()
        )
        if not dates:
            rs.current_streak = 0
            rs.longest_streak = 0
            rs.last_ride_date = None
            return
        longest = cur = 1
        for i in range(1, len(dates)):
            cur = cur + 1 if (dates[i] - dates[i - 1]).days == 1 else 1
            longest = max(longest, cur)
        cur_run = 1
        for i in range(len(dates) - 1, 0, -1):
            if (dates[i] - dates[i - 1]).days == 1:
                cur_run += 1
            else:
                break
        rs.current_streak = cur_run
        rs.longest_streak = longest
        rs.last_ride_date = dates[-1]


def _banned_ids(db):
    import services.settings as settings
    return set(settings.banned(db).keys())


def _deleted_ids(db):
    from models import Rider
    return {sid for (sid,) in db.query(Rider.store_id)
            .filter(Rider.deleted_at.isnot(None)).all()}


def _excluded_ids(db):
    """Riders kept out of every public stat: banned (admin) + self-deleted (closed)."""
    return _banned_ids(db) | _deleted_ids(db)


def reconcile_unaggregated(db, limit=5000):
    """Apply any validated trips that were never aggregated (e.g. a crash between
    the trip insert and aggregation). Idempotent via the `aggregated` flag.
    Banned riders' trips are left unaggregated (excluded from public stats)."""
    from models import Trip
    q = (db.query(Trip)
         .filter(Trip.validation_status == "validated", Trip.aggregated.is_(False)))
    excluded = _excluded_ids(db)
    if excluded:
        q = q.filter(~Trip.rider_store_id.in_(excluded))
    trips = q.order_by(Trip.created_at).limit(limit).all()
    agg = Aggregator(db)
    for t in trips:
        agg.apply(t)
    return len(trips)


def rebuild_all(db):
    """Clear every materialized table and replay all validated trips from scratch.
    Use after rejecting an already-counted trip, banning a rider, or to repair drift.
    Banned riders' trips stay in the DB (reversible) but produce no public stats."""
    from models import CountryStat, DailyDistance, MapCell, MapCellRider, Record, RiderStat, Trip
    # MapCellRider must be cleared too: bump_map_cell only increments MapCell.rider_count
    # when the (cell, rider) row is new, so leaving these behind makes every rebuilt cell
    # come back with rider_count=0 — silently emptying the whole heatmap.
    for model in (RiderStat, CountryStat, DailyDistance, MapCell, MapCellRider, Record):
        db.query(model).delete()
    db.query(Trip).update({Trip.aggregated: False})
    db.commit()
    agg = Aggregator(db)
    q = db.query(Trip).filter(Trip.validation_status == "validated")
    excluded = _excluded_ids(db)
    if excluded:
        q = q.filter(~Trip.rider_store_id.in_(excluded))
    trips = q.order_by(Trip.created_at).all()
    for t in trips:
        agg.apply(t)
    return len(trips)
