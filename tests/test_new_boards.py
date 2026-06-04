"""New computed-from-trip leaderboards (frequent / marathon / pace / battery /
night / weekend)."""
from datetime import datetime

import models
from services import stats


def _rider(db, sid, name):
    db.add(models.Rider(store_id=sid, display_name=name, platform="google_play"))
    db.commit()


def _trip(db, sid, uuid, **kw):
    db.add(models.Trip(trip_uuid=uuid, rider_store_id=sid, validation_status="validated", **kw))
    db.commit()


def test_frequent_marathon_pace(db):
    _rider(db, "a", "A")
    _rider(db, "b", "B")
    _trip(db, "a", "a1", duration_s=600, avg_speed=20, distance_km=3)
    _trip(db, "a", "a2", duration_s=7200, avg_speed=10, distance_km=20)   # 2h ride
    _trip(db, "b", "b1", duration_s=1200, avg_speed=35, distance_km=10)   # fastest avg

    freq = stats.frequent_flyer(db)
    assert freq[0]["store_id"] == "a" and freq[0]["trips_total"] == 2

    mar = stats.marathoner(db)
    assert mar[0]["store_id"] == "a" and mar[0]["ride_hours"] == 2.0

    pace = stats.pace_maker(db)
    assert pace[0]["store_id"] == "b" and pace[0]["avg_speed"] == 35.0


def test_battery_weekend_night(db):
    _rider(db, "a", "A")
    _trip(db, "a", "bw1", battery_used_pct=42.0, distance_km=15,
          start_utc=datetime(2026, 6, 6, 23, 0))   # Saturday, 23:00 -> weekend + night
    _trip(db, "a", "bw2", battery_used_pct=10.0, distance_km=99,
          start_utc=datetime(2026, 6, 8, 12, 0))   # Monday noon -> excluded from both

    bat = stats.battery_vampire(db)
    assert bat[0]["batt_pct"] == 42.0

    ww = stats.weekend_warrior(db)
    assert ww and ww[0]["weekend_km"] == 15.0      # Monday's 99 km excluded

    nr = stats.night_rider(db)
    assert nr and nr[0]["night_rides"] == 1        # only the 23:00 ride
