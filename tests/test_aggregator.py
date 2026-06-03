from datetime import datetime

from repository.riders import RiderRepo
from repository.trips import TripRepo
from services.aggregator import Aggregator
import models


def _trip(tr, uuid, store, dist, day, lat=69.0, lon=18.0, country="NO", vmax=30.0, g=2.0):
    return tr.insert_trip(
        trip_uuid=uuid, rider_store_id=store, distance_km=dist,
        start_utc=datetime(2026, 6, day, 12, 0, 0), end_utc=datetime(2026, 6, day, 12, 30, 0),
        max_speed=vmax, max_gforce=g, country=country, start_lat=lat, start_lon=lon,
        validation_status="validated", created_at=datetime(2026, 6, day),
    )


def test_aggregate_two_consecutive_days(db):
    RiderRepo(db).upsert("r1", "google_play", "Ann", "NO")
    tr, agg = TripRepo(db), Aggregator(db)
    agg.apply(_trip(tr, "a1", "r1", 10.0, 1))
    agg.apply(_trip(tr, "a2", "r1", 5.0, 2, vmax=40.0, g=3.0))

    rs = db.get(models.RiderStat, "r1")
    assert rs.total_km == 15.0 and rs.trip_count == 2
    assert rs.best_speed == 40.0 and rs.best_gforce == 3.0
    assert rs.longest_trip_km == 10.0
    assert rs.current_streak == 2 and rs.longest_streak == 2

    cs = db.get(models.CountryStat, "NO")
    assert cs.total_km == 15.0 and cs.rider_count == 1
    assert db.get(models.MapCell, (0.1, "0.1:690:180")).rider_count == 1
    assert db.get(models.MapCell, (0.1, "0.1:690:180")).total_km == 15.0
    assert db.get(models.Record, "top_speed").value == 40.0
    assert db.get(models.Record, "mileage_king").value == 15.0
    assert db.get(models.Record, "longest_trip").value == 10.0


def test_apply_is_idempotent(db):
    RiderRepo(db).upsert("r2", "google_play", "Bo", "NO")
    tr, agg = TripRepo(db), Aggregator(db)
    t = _trip(tr, "b1", "r2", 10.0, 1)
    agg.apply(t)
    agg.apply(t)   # second call is a no-op (already aggregated)
    assert db.get(models.RiderStat, "r2").total_km == 10.0
    assert db.get(models.RiderStat, "r2").trip_count == 1


def test_flagged_trip_not_aggregated(db):
    RiderRepo(db).upsert("r3", "google_play", "Cy", "NO")
    tr, agg = TripRepo(db), Aggregator(db)
    t = tr.insert_trip(trip_uuid="c1", rider_store_id="r3", distance_km=9.0,
                       validation_status="flagged", start_utc=datetime(2026, 6, 1))
    agg.apply(t)
    assert db.get(models.RiderStat, "r3") is None
