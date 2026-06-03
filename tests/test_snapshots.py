from datetime import datetime, date

from repository.riders import RiderRepo
from repository.trips import TripRepo
from services.aggregator import Aggregator
from services.snapshots import generate_weekly, iso_week_key
import models


def test_weekly_champion(db):
    RiderRepo(db).upsert("w1", "google_play", "Wk1", "NO")
    RiderRepo(db).upsert("w2", "google_play", "Wk2", "NO")
    tr, agg = TripRepo(db), Aggregator(db)

    def t(u, s, km, day):
        return tr.insert_trip(trip_uuid=u, rider_store_id=s, distance_km=km,
                              start_utc=datetime(2026, 6, day), country="NO",
                              start_lat=69.6, start_lon=18.9,
                              validation_status="validated", created_at=datetime(2026, 6, day))

    agg.apply(t("w_t1", "w1", 10.0, 1))   # 2026-06-01 (Mon) and 06-02 are same ISO week
    agg.apply(t("w_t2", "w1", 5.0, 2))
    agg.apply(t("w_t3", "w2", 8.0, 1))

    out = generate_weekly(db, ref=date(2026, 6, 1))
    assert out["champion"]["store_id"] == "w1"
    assert out["champion"]["km"] == 15.0
    assert out["top"][1]["store_id"] == "w2"

    key = iso_week_key(date(2026, 6, 1))
    assert db.get(models.LeaderboardSnapshot, ("week", key, "distance")) is not None
