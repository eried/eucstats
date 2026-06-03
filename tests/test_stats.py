from datetime import datetime

from fastapi.testclient import TestClient

from repository.riders import RiderRepo
from repository.trips import TripRepo
from services.aggregator import Aggregator
from services import stats
from main import app


def _seed(db):
    RiderRepo(db).upsert("s1", "google_play", "Alpha", "NO")
    RiderRepo(db).upsert("s2", "google_play", "Beta", "SE")
    tr, agg = TripRepo(db), Aggregator(db)

    def t(uuid, store, dist, day, vmax, g):
        return tr.insert_trip(
            trip_uuid=uuid, rider_store_id=store, distance_km=dist,
            start_utc=datetime(2026, 6, day), end_utc=datetime(2026, 6, day, 1),
            max_speed=vmax, max_gforce=g, country="NO", start_lat=69.6, start_lon=18.9,
            validation_status="validated", created_at=datetime(2026, 6, day))

    agg.apply(t("t1", "s1", 20.0, 1, 40.0, 3.0))
    agg.apply(t("t2", "s1", 10.0, 2, 45.0, 2.0))
    agg.apply(t("t3", "s2", 5.0, 1, 30.0, 1.0))


def test_mileage_leaderboard_order(db):
    _seed(db)
    lb = stats.mileage_leaderboard(db)
    assert [e["store_id"] for e in lb][:2] == ["s1", "s2"]
    assert lb[0]["total_km"] == 30.0


def test_speed_and_records(db):
    _seed(db)
    assert stats.speed_leaderboard(db)[0]["best_speed"] == 45.0
    recs = {r["key"]: r for r in stats.records(db)}
    assert recs["top_speed"]["value"] == 45.0
    assert recs["mileage_king"]["rider"]["store_id"] == "s1"


def test_countries_and_summary(db):
    _seed(db)
    cs = stats.countries(db)
    assert cs[0]["country"] == "NO" and cs[0]["riders"] == 2
    s = stats.global_summary(db)
    assert s["riders"] == 2 and s["trips"] == 3 and s["total_km"] == 35.0


def test_leaderboard_endpoints(db):
    _seed(db)
    with TestClient(app) as client:
        r = client.get("/api/v1/leaderboards/mileage")
        assert r.status_code == 200 and r.json()["entries"][0]["store_id"] == "s1"
        assert client.get("/api/v1/leaderboards/nope").status_code == 404
        assert client.get("/api/v1/stats/summary").json()["trips"] == 3
        assert client.get("/api/v1/map/cells?zoom=0.1").status_code == 200
