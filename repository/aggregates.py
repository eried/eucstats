"""Materialized-table maintenance (rider/country/daily/map/records)."""
from datetime import date, datetime

from sqlalchemy import func

from models import (
    RiderStat, CountryStat, DailyDistance, MapCell, MapCellRider, Record, Trip,
)


class AggregateRepo:
    def __init__(self, db):
        self.db = db

    # --- rider ---
    def get_rider_stat(self, store_id) -> RiderStat:
        rs = self.db.get(RiderStat, store_id)
        if rs is None:
            rs = RiderStat(store_id=store_id, total_km=0.0, trip_count=0,
                           best_speed=0.0, best_gforce=0.0, longest_trip_km=0.0,
                           current_streak=0, longest_streak=0)
            self.db.add(rs)
        return rs

    # --- daily ---
    def add_daily(self, store_id, d: date, km: float) -> DailyDistance:
        row = self.db.get(DailyDistance, (store_id, d))
        if row is None:
            row = DailyDistance(store_id=store_id, date=d, km=0.0)
            self.db.add(row)
        row.km = (row.km or 0.0) + km
        return row

    # --- map cells (distinct riders via association) ---
    def bump_map_cell(self, zoom: float, cell: str, store_id: str, km: float,
                      when: datetime) -> MapCell:
        mc = self.db.get(MapCell, (zoom, cell))
        if mc is None:
            mc = MapCell(zoom=zoom, cell=cell, rider_count=0, total_km=0.0)
            self.db.add(mc)
        mc.total_km = (mc.total_km or 0.0) + km
        mc.last_activity = when
        if self.db.get(MapCellRider, (zoom, cell, store_id)) is None:
            self.db.add(MapCellRider(zoom=zoom, cell=cell, store_id=store_id))
            mc.rider_count = (mc.rider_count or 0) + 1
        return mc

    # --- country (recomputed from validated trips; country count is small) ---
    def recompute_country(self, country: str) -> CountryStat | None:
        if not country:
            return None
        cs = self.db.get(CountryStat, country)
        if cs is None:
            cs = CountryStat(country=country)
            self.db.add(cs)
        total_km = self.db.query(func.coalesce(func.sum(Trip.distance_km), 0.0)).filter(
            Trip.country == country, Trip.validation_status == "validated").scalar() or 0.0
        rider_count = self.db.query(func.count(func.distinct(Trip.rider_store_id))).filter(
            Trip.country == country, Trip.validation_status == "validated").scalar() or 0
        cs.total_km = total_km
        cs.rider_count = rider_count
        cs.avg_km_per_rider = (total_km / rider_count) if rider_count else 0.0
        return cs

    # --- records ---
    def set_record_if_better(self, key: str, store_id: str, value, trip_uuid: str,
                             when: datetime, lower_better: bool = False) -> Record | None:
        if value is None:
            return None
        rec = self.db.get(Record, key)
        cur = rec.value if (rec is not None and rec.value is not None) else None
        if cur is not None and (value >= cur if lower_better else value <= cur):
            return rec                     # existing record stands
        if rec is None:
            rec = Record(key=key)
            self.db.add(rec)
        rec.store_id = store_id
        rec.value = value
        rec.trip_uuid = trip_uuid
        rec.achieved_at = when
        return rec
