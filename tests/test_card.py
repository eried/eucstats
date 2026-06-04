"""Rider card endpoint data (GET /riders/{store_id}/card)."""
import models
from services import stats
from services.aggregator import Aggregator


def test_card_zeros_when_no_trips(db):
    db.add(models.Rider(store_id="a", display_name="Ada", platform="google_play"))
    db.commit()
    card = stats.rider_card(db, "a")
    assert card["display_name"] == "Ada"
    assert card["stats"]["total_km"] == 0 and card["stats"]["trips"] == 0
    assert card["ranks"]["distance"] is None       # unranked, but still a valid card (not 404)


def test_card_with_trip_and_rank(db):
    db.add(models.Rider(store_id="a", display_name="Ada", platform="google_play"))
    db.add(models.Trip(trip_uuid="t1", rider_store_id="a", validation_status="validated",
                       distance_km=10.0, max_speed=30.0))
    db.commit()
    Aggregator(db).apply(db.get(models.Trip, "t1"))
    card = stats.rider_card(db, "a")
    assert card["stats"]["total_km"] == 10.0
    assert card["stats"]["best_speed_kmh"] == 30.0
    assert card["ranks"]["distance"] == 1


def test_card_missing_rider_is_none(db):
    assert stats.rider_card(db, "nope") is None
