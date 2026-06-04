"""Apply a validated trip to the materialized tables (idempotent per trip)."""
from __future__ import annotations

import config
from ingest.geo import cells_for
from models import DailyDistance, utcnow
from repository.aggregates import AggregateRepo


class Aggregator:
    def __init__(self, db):
        self.db = db
        self.agg = AggregateRepo(db)

    def apply(self, trip) -> None:
        if trip.validation_status != "validated" or trip.aggregated:
            return
        store = trip.rider_store_id
        dist = trip.distance_km or 0.0
        when = trip.created_at or utcnow()

        rs = self.agg.get_rider_stat(store)
        rs.total_km = (rs.total_km or 0.0) + dist
        rs.trip_count = (rs.trip_count or 0) + 1
        if trip.max_speed is not None:
            rs.best_speed = max(rs.best_speed or 0.0, trip.max_speed)
        if trip.max_gforce is not None:
            rs.best_gforce = max(rs.best_gforce or 0.0, trip.max_gforce)
        if trip.max_sustained_w is not None:
            rs.best_sustained_w = max(rs.best_sustained_w or 0.0, trip.max_sustained_w)
        if trip.max_sustained_a is not None:
            rs.best_sustained_a = max(rs.best_sustained_a or 0.0, trip.max_sustained_a)
        if trip.peak_voltage is not None:
            rs.peak_voltage = max(rs.peak_voltage or 0.0, trip.peak_voltage)
        if trip.fastest_0_40_s is not None and trip.fastest_0_40_s > 0:
            rs.fastest_0_40_s = (trip.fastest_0_40_s if rs.fastest_0_40_s is None
                                 else min(rs.fastest_0_40_s, trip.fastest_0_40_s))
        rs.longest_trip_km = max(rs.longest_trip_km or 0.0, dist)
        if trip.ascent_m:
            rs.total_ascent_m = (rs.total_ascent_m or 0.0) + trip.ascent_m
        if trip.est_range_km:
            rs.best_range_km = max(rs.best_range_km or 0.0, trip.est_range_km)
        if trip.wh_per_km and (trip.distance_km or 0) >= 2:
            rs.best_wh_per_km = (trip.wh_per_km if rs.best_wh_per_km is None
                                 else min(rs.best_wh_per_km, trip.wh_per_km))

        if trip.start_utc:
            self.agg.add_daily(store, trip.start_utc.date(), dist)
            self._recompute_streak(rs, store)

        if trip.country:
            self.agg.recompute_country(trip.country)

        if trip.start_lat is not None and trip.start_lon is not None:
            for z, cell in cells_for(trip.start_lat, trip.start_lon, config.GRID_ZOOMS).items():
                self.agg.bump_map_cell(z, cell, store, dist, when)

        self.agg.set_record_if_better("mileage_king", store, rs.total_km, trip.trip_uuid, when)
        self.agg.set_record_if_better("top_speed", store, trip.max_speed, trip.trip_uuid, when)
        self.agg.set_record_if_better("max_gforce", store, trip.max_gforce, trip.trip_uuid, when)
        self.agg.set_record_if_better("longest_trip", store, dist, trip.trip_uuid, when)
        self.agg.set_record_if_better("sustained_w", store, trip.max_sustained_w, trip.trip_uuid, when)
        self.agg.set_record_if_better("sustained_a", store, trip.max_sustained_a, trip.trip_uuid, when)
        self.agg.set_record_if_better("peak_voltage", store, trip.peak_voltage, trip.trip_uuid, when)

        trip.aggregated = True
        self.db.commit()

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
