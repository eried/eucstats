from datetime import datetime, date

import pytest

from database import SessionLocal, init_db
from repository.riders import RiderRepo
from repository.trips import TripRepo
from repository.aggregates import AggregateRepo
import models


@pytest.fixture
def db():
    init_db()
    s = SessionLocal()
    yield s
    s.close()


def test_rider_upsert_and_get(db):
    repo = RiderRepo(db)
    repo.upsert("g_1", "google_play", "Alice", "NO")
    assert repo.get("g_1").display_name == "Alice"


def test_monthly_change_limit(db):
    repo = RiderRepo(db)
    r = repo.upsert("g_2", "google_play", "Bob", "NO")
    r.last_name_change = datetime(2026, 6, 1)
    db.commit()
    assert repo.can_change(r, "name", datetime(2026, 6, 20)) is False   # same month
    assert repo.can_change(r, "name", datetime(2026, 7, 1)) is True     # next month
    repo.apply_change(r, "name", "Bobby", datetime(2026, 7, 1))
    assert repo.get("g_2").display_name == "Bobby"


def test_trip_insert_and_dedupe(db):
    RiderRepo(db).upsert("g_3", "google_play", "Cara", "NO")
    tr = TripRepo(db)
    tr.insert_trip(trip_uuid="t1", rider_store_id="g_3", distance_km=5.0,
                   validation_status="validated", start_utc=datetime(2026, 6, 1))
    assert tr.exists("t1") is True
    assert tr.exists("missing") is False


def test_raw_eviction_by_age(db):
    RiderRepo(db).upsert("g_4", "google_play", "Dan", "NO")
    tr = TripRepo(db)
    tr.insert_trip(trip_uuid="t2", rider_store_id="g_4",
                   validation_status="validated", start_utc=datetime(2026, 6, 1))
    tr.save_raw("t2", b"x" * 100)
    ru = db.get(models.RawUpload, "t2")
    ru.received_at = datetime(2020, 1, 1)
    db.commit()
    evictable = tr.evictable_by_age(datetime(2026, 6, 3), 30)
    assert any(r.trip_uuid == "t2" for r in evictable)


def test_aggregate_repo_basics(db):
    RiderRepo(db).upsert("g_5", "google_play", "Eve", "NO")
    tr = TripRepo(db)
    tr.insert_trip(trip_uuid="t3", rider_store_id="g_5", distance_km=10.0,
                   country="NO", validation_status="validated", start_utc=datetime(2026, 6, 1))
    agg = AggregateRepo(db)
    rs = agg.get_rider_stat("g_5")
    rs.total_km = 10.0
    agg.add_daily("g_5", date(2026, 6, 1), 10.0)
    agg.bump_map_cell(0.1, "0.1:696:189", "g_5", 10.0, datetime(2026, 6, 1))
    agg.recompute_country("NO")
    agg.set_record_if_better("mileage_king", "g_5", 10.0, "t3", datetime(2026, 6, 1))
    db.commit()
    assert db.get(models.CountryStat, "NO").total_km == 10.0
    assert db.get(models.CountryStat, "NO").rider_count == 1
    assert db.get(models.MapCell, (0.1, "0.1:696:189")).rider_count == 1
    assert db.get(models.Record, "mileage_king").value == 10.0
