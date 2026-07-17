"""Trips that arrived without a wheel identity — listing, and attaching them back."""
from datetime import datetime

import models
from repository.riders import RiderRepo
from repository.trips import TripRepo
from services import orphans, stats


def _rider(db, sid, name):
    RiderRepo(db).upsert(sid, "google_play", name, "NO")


def _wheel(db, wid, sid, brand, model):
    db.add(models.Wheel(wheel_id=wid, rider_store_id=sid, brand=brand, model=model))


def _trip(db, uuid, sid, km, wheel_id=None, status="validated"):
    TripRepo(db).insert_trip(trip_uuid=uuid, rider_store_id=sid, wheel_id=wheel_id,
                             distance_km=km, start_utc=datetime(2026, 6, 1),
                             validation_status=status)


def _seed(db):
    # solo: one wheel -> an orphan is unambiguous
    _rider(db, "solo", "Solo Sam")
    _wheel(db, "AA:01", "solo", "InMotion", "InMotion V14")
    # duo: two wheels -> an orphan is ambiguous, must not be guessed
    _rider(db, "duo", "Duo Dee")
    _wheel(db, "BB:01", "duo", "LeaperKim", "NOSFET Apex")
    _wheel(db, "BB:02", "duo", "IPS", None)
    # none: no wheels at all -> nothing to attach to
    _rider(db, "none", "Nowheel Ned")
    db.commit()                       # wheels must exist before trips point at them

    _trip(db, "s-linked", "solo", 10.0, wheel_id="AA:01")
    _trip(db, "s-orphan", "solo", 7.0)
    _trip(db, "d-linked", "duo", 5.0, wheel_id="BB:01")
    _trip(db, "d-orphan", "duo", 9.0)
    _trip(db, "n-orphan", "none", 3.0)
    db.commit()


def test_orphan_trips_lists_only_wheelless(db):
    _seed(db)
    got = {o["trip_uuid"] for o in orphans.orphan_trips(db)}
    assert got == {"s-orphan", "d-orphan", "n-orphan"}


def test_orphan_summary_groups_by_rider_with_candidates(db):
    _seed(db)
    by = {r["store_id"]: r for r in orphans.orphan_summary(db)}
    assert by["solo"]["trips"] == 1 and by["solo"]["km"] == 7.0
    assert by["solo"]["auto"] is True                  # exactly one wheel -> safe to attach
    assert len(by["solo"]["wheels"]) == 1
    assert by["duo"]["auto"] is False                  # two wheels -> needs a human
    assert len(by["duo"]["wheels"]) == 2
    assert by["none"]["auto"] is False and by["none"]["wheels"] == []


def test_attach_auto_only_touches_sole_wheel_riders(db):
    _seed(db)
    done = orphans.attach_auto(db)
    assert done == [("s-orphan", "AA:01")]             # duo/none left alone
    db.expire_all()
    assert db.get(models.Trip, "s-orphan").wheel_id == "AA:01"
    assert db.get(models.Trip, "d-orphan").wheel_id is None
    assert db.get(models.Trip, "n-orphan").wheel_id is None


def test_attach_auto_feeds_the_wheel_board(db):
    _seed(db)
    before = {e["name"]: e for e in stats.by_wheel(db)}
    assert before["V14"]["total_km"] == 10.0          # the orphan's 7 km is missing
    orphans.attach_auto(db)
    db.expire_all()
    after = {e["name"]: e for e in stats.by_wheel(db)}
    assert after["V14"]["total_km"] == 17.0           # ...and now counted
    assert after["V14"]["trips"] == 2


def test_attach_one_assigns_a_named_wheel(db):
    _seed(db)
    assert orphans.attach_one(db, "d-orphan", "BB:01") is True
    db.expire_all()
    assert db.get(models.Trip, "d-orphan").wheel_id == "BB:01"


def test_attach_one_refuses_another_riders_wheel(db):
    _seed(db)
    # duo's orphan must not be attachable to solo's wheel
    assert orphans.attach_one(db, "d-orphan", "AA:01") is False
    db.expire_all()
    assert db.get(models.Trip, "d-orphan").wheel_id is None


def test_attach_one_refuses_a_trip_that_already_has_a_wheel(db):
    _seed(db)
    assert orphans.attach_one(db, "d-linked", "BB:02") is False
    db.expire_all()
    assert db.get(models.Trip, "d-linked").wheel_id == "BB:01"


def test_orphan_count_is_cheap(db):
    _seed(db)
    assert orphans.orphan_count(db) == 3
    orphans.attach_auto(db)
    db.expire_all()
    assert orphans.orphan_count(db) == 2
