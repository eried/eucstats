"""The daily recap must credit a ride to the day the RIDER had, not the server's day.

The recap bucketed every trip into one timezone (the site's, Europe/Oslo). A rider in
New York is six hours behind, so anything they rode after 18:00 local fell into the next
Oslo day: four rides between 17:17 and 22:06 on a Wednesday evening were reported as
Thursday, and the rider correctly said he had not ridden that day. Worse, the same evening
was split across two recaps - the 17:17 ride landed in one and the 18:27 ride in the next.

Riders here span Shanghai (UTC+8) to Denver (UTC-6), so it misreports in both directions.
Every trip records its own tz, so use it.
"""
from datetime import date, datetime, timedelta

import models
from services import telegram

CFG = {"summary_tz": "Europe/Oslo", "link_url": "https://eucstats.ried.no"}


def _rider(db, sid, name, flag="US"):
    db.add(models.Rider(store_id=sid, display_name=name, flag=flag,
                        platform="google_play", consent_public=True))
    db.commit()


def _trip(db, sid, uuid, start_utc, km, tz):
    db.add(models.Trip(trip_uuid=uuid, rider_store_id=sid, start_utc=start_utc,
                       end_utc=start_utc + timedelta(minutes=30), distance_km=km,
                       tz=tz, validation_status="validated"))
    db.commit()


def _km_in(db, day):
    """Total distance the recap for `day` reports."""
    return telegram.day_distance_km(db, day, CFG)


def test_a_new_york_evening_belongs_to_the_riders_wednesday(db):
    # The real case: 18:27 EDT Wednesday is 22:27 UTC, which is Thursday in Oslo.
    _rider(db, "us1", "MackNificent")
    _trip(db, "us1", "t1", datetime(2026, 8, 19, 22, 27), 13.9, "America/New_York")
    assert _km_in(db, date(2026, 8, 19)) == 13.9
    assert _km_in(db, date(2026, 8, 20)) == 0


def test_one_evening_is_not_split_across_two_recaps(db):
    _rider(db, "us1", "MackNificent")
    # 17:17, 18:27, 18:55, 21:18 and 22:06 local, all Wednesday 19 Aug for him.
    for i, (utc, km) in enumerate([
        (datetime(2026, 8, 19, 21, 17), 8.1),
        (datetime(2026, 8, 19, 22, 27), 13.9),
        (datetime(2026, 8, 19, 22, 55), 23.3),
        (datetime(2026, 8, 20, 1, 18), 0.2),
        (datetime(2026, 8, 20, 2, 6), 22.5),
    ]):
        _trip(db, "us1", f"t{i}", utc, km, "America/New_York")
    assert round(_km_in(db, date(2026, 8, 19)), 1) == 68.0
    assert _km_in(db, date(2026, 8, 20)) == 0


def test_a_rider_in_the_site_timezone_is_unaffected(db):
    _rider(db, "no1", "Erwin", "NO")
    _trip(db, "no1", "t1", datetime(2026, 8, 19, 18, 0), 10.0, "Europe/Oslo")  # 20:00 Oslo
    assert _km_in(db, date(2026, 8, 19)) == 10.0


def test_a_rider_ahead_of_the_site_is_also_bucketed_locally(db):
    """Shanghai is UTC+8: 00:30 local on the 20th is still the 19th in Oslo."""
    _rider(db, "cn1", "roy", "CN")
    _trip(db, "cn1", "t1", datetime(2026, 8, 19, 16, 30), 12.0, "Asia/Shanghai")
    assert _km_in(db, date(2026, 8, 20)) == 12.0
    assert _km_in(db, date(2026, 8, 19)) == 0


def test_a_trip_with_no_timezone_falls_back_to_the_site(db):
    _rider(db, "x1", "Nobody", "SE")
    _trip(db, "x1", "t1", datetime(2026, 8, 19, 10, 0), 5.0, None)
    assert _km_in(db, date(2026, 8, 19)) == 5.0


def test_a_numeric_offset_is_understood(db):
    """Ingest stores the raw tz_offset_min when the app sends no zone name."""
    _rider(db, "x2", "Offset", "US")
    _trip(db, "x2", "t1", datetime(2026, 8, 19, 22, 27), 7.0, "-240")   # UTC-4
    assert _km_in(db, date(2026, 8, 19)) == 7.0


def test_the_top_rider_line_uses_the_same_day(db):
    _rider(db, "us1", "MackNificent")
    _trip(db, "us1", "t1", datetime(2026, 8, 19, 22, 27), 59.9, "America/New_York")
    assert "MackNificent" in (telegram.daily_summary_text(db, date(2026, 8, 19), CFG) or "")
    assert telegram.daily_summary_text(db, date(2026, 8, 20), CFG) is None
