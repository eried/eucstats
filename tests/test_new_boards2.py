"""Second batch of computed boards: early / peak / energy / explorer / bigday / commuter."""
from datetime import datetime

import models
from services import stats


def _rider(db, sid):
    db.add(models.Rider(store_id=sid, display_name=sid, platform="google_play"))
    db.commit()


def _trip(db, sid, uuid, **kw):
    db.add(models.Trip(trip_uuid=uuid, rider_store_id=sid, validation_status="validated", **kw))
    db.commit()


def test_early_peak_energy(db):
    _rider(db, "a")
    _rider(db, "b")
    _trip(db, "a", "a1", start_utc=datetime(2026, 6, 4, 6, 0), ascent_m=120, wh_per_km=20, distance_km=10)
    _trip(db, "a", "a2", start_utc=datetime(2026, 6, 4, 7, 0), ascent_m=300, wh_per_km=25, distance_km=8)
    _trip(db, "b", "b1", start_utc=datetime(2026, 6, 4, 15, 0), ascent_m=50, wh_per_km=10, distance_km=5)

    eb = stats.early_bird(db)
    assert eb[0]["store_id"] == "a" and eb[0]["morning_rides"] == 2

    pk = stats.peak_bagger(db)
    assert pk[0]["store_id"] == "a" and pk[0]["peak_ascent"] == 300

    en = stats.power_plant(db)
    assert en[0]["store_id"] == "a" and en[0]["energy_kwh"] == 0.4   # (200+200) Wh


def test_explorer_bigday_commuter(db):
    _rider(db, "a")
    _trip(db, "a", "e1", start_utc=datetime(2026, 6, 8, 9, 0), start_cell="x", distance_km=5)   # Mon
    _trip(db, "a", "e2", start_utc=datetime(2026, 6, 8, 12, 0), start_cell="x", distance_km=5)
    _trip(db, "a", "e3", start_utc=datetime(2026, 6, 8, 18, 0), start_cell="y", distance_km=5)

    assert stats.explorer(db)[0]["areas"] == 2            # cells x, y
    assert stats.big_day(db)[0]["rides_in_day"] == 3      # all on 2026-06-08
    assert stats.commuter(db)[0]["weekday_km"] == 15.0    # Monday is a weekday
