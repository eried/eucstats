"""Riders who opted out of public stats (consent_public=False) are hidden from
public boards / records / counts, but consenting riders appear normally."""
import models
from services import stats
from services.aggregator import Aggregator


def _rider_trip(db, sid, consent, km=10.0):
    db.add(models.Rider(store_id=sid, display_name=sid.upper(), platform="google_play",
                        consent_public=consent))
    db.add(models.Trip(trip_uuid="t-" + sid, rider_store_id=sid, validation_status="validated",
                       distance_km=km, max_speed=30.0))
    db.commit()
    Aggregator(db).apply(db.get(models.Trip, "t-" + sid))


def test_consent_false_hidden_from_public(db):
    _rider_trip(db, "yes", True, km=10.0)
    _rider_trip(db, "no", False, km=99.0)        # bigger, but opted out

    ids = [e["store_id"] for e in stats.mileage_leaderboard(db)]
    assert "yes" in ids and "no" not in ids       # opted-out rider absent from the board

    rec_ids = [r["rider"]["store_id"] for r in stats.records(db)]
    assert "no" not in rec_ids                     # and from records (even though km is highest)

    assert stats.global_summary(db)["riders"] == 1  # headline count excludes the opted-out rider


def test_consent_default_true_is_public(db):
    # a rider created without specifying consent defaults to public
    db.add(models.Rider(store_id="d", display_name="D", platform="google_play"))
    db.add(models.Trip(trip_uuid="t-d", rider_store_id="d", validation_status="validated",
                       distance_km=5.0, max_speed=20.0))
    db.commit()
    Aggregator(db).apply(db.get(models.Trip, "t-d"))
    assert "d" in [e["store_id"] for e in stats.mileage_leaderboard(db)]
