"""Incognito riders on the speed boards.

A public top-speed entry can be evidence against the rider who set it, so above a
jurisdiction's limit the entry is published without an identity. The test that matters is
not what the page draws but what the server sends: a mask applied in CSS leaves the real
name, flag and rider id in the response for anyone who opens devtools.
"""
import models
from services import privacy, settings, stats


def _rider(db, sid, name, flag, speed):
    db.add(models.Rider(store_id=sid, display_name=name, flag=flag,
                        platform="google_play", consent_public=True))
    db.add(models.RiderStat(store_id=sid, best_speed=speed, total_km=100.0, trip_count=5))
    db.commit()


def test_over_the_limit_sends_no_identity(db):
    _rider(db, "dk1", "Kestrel", "DK", 41.0)          # DK limit is 20
    row = [r for r in stats.speed_leaderboard(db) if r["best_speed"] == 41.0][0]
    assert row["anon"] is True
    assert row["store_id"] is None
    assert row["name"] != "Kestrel"
    assert row["flag"] is None                        # the flag names one person on a small board
    assert row["lat"] is None and row["lon"] is None  # a start point is a street address
    assert "Kestrel" not in str(row)


def test_under_the_limit_is_published_normally(db):
    _rider(db, "dk2", "Slowpoke", "DK", 18.0)
    row = [r for r in stats.speed_leaderboard(db) if r["best_speed"] == 18.0][0]
    assert row.get("anon") is not True
    assert row["name"] == "Slowpoke"
    assert row["flag"] == "DK"


def test_a_country_with_no_rule_is_left_alone(db):
    """The site does not guess at a law it does not know."""
    _rider(db, "us1", "Fastlane", "US", 90.0)
    row = [r for r in stats.speed_leaderboard(db) if r["best_speed"] == 90.0][0]
    assert row.get("anon") is not True
    assert row["name"] == "Fastlane"


def test_the_twenty_and_twentyfive_split(db):
    """A flat European 25 would under-protect Denmark, Sweden and Poland."""
    assert settings.speed_privacy_limit(db, "DK") == 20
    assert settings.speed_privacy_limit(db, "SE") == 20
    assert settings.speed_privacy_limit(db, "PL") == 20
    assert settings.speed_privacy_limit(db, "NO") == 20
    assert settings.speed_privacy_limit(db, "FR") == 25
    assert settings.speed_privacy_limit(db, "AT") == 25
    assert settings.speed_privacy_limit(db, "US") is None
    assert settings.speed_privacy_limit(db, "CA") is None


def test_a_rider_at_22_is_incognito_in_denmark_but_not_in_france(db):
    assert privacy.is_incognito(db, "DK", 22.0) is True
    assert privacy.is_incognito(db, "FR", 22.0) is False


def test_the_alias_and_mark_are_stable_but_not_reversible(db):
    a = privacy.token(db, "rider-one")
    b = privacy.token(db, "rider-one")
    c = privacy.token(db, "rider-two")
    assert a == b, "the same rider must keep the same mark across the board"
    assert a != c
    assert "rider-one" not in a
    assert len(a) == 64


def test_the_salt_is_per_install(db):
    """Two installs must not produce the same alias for the same rider id."""
    from services.settings import get_meta
    privacy.token(db, "x")
    assert get_meta(db, "speed_privacy_salt", None), "no salt was stored"


def test_records_hide_the_rider_too(db):
    _rider(db, "dk3", "Bolt", "DK", 55.0)
    db.add(models.Record(key="top_speed", store_id="dk3", value=55.0))
    db.commit()
    rec = [r for r in stats.records(db) if r["key"] == "top_speed"][0]
    assert rec["rider"]["anon"] is True
    assert "Bolt" not in str(rec)


def test_a_non_speed_record_is_untouched(db):
    """Only speed entries are incriminating; distance is not."""
    _rider(db, "dk4", "Rolling", "DK", 60.0)
    db.add(models.Record(key="mileage_king", store_id="dk4", value=900.0))
    db.commit()
    rec = [r for r in stats.records(db) if r["key"] == "mileage_king"][0]
    assert rec["rider"]["name"] == "Rolling"


def test_an_admin_sees_the_identity_and_the_marker(db):
    """The admin eye toggle needs both: the real rider, flagged as incognito."""
    _rider(db, "dk5", "Kestrel", "DK", 41.0)
    row = [r for r in stats.speed_leaderboard(db, reveal=True) if r["best_speed"] == 41.0][0]
    assert row["anon"] is True
    assert row["name"] == "Kestrel"
    assert row["alias"].startswith("Rider #")


def test_limits_are_editable_without_a_deploy(db):
    """This is law: it differs, it changes, and it must not be frozen into code."""
    settings.set_speed_privacy(db, {"dk": 30, "ZZ": 10})
    assert settings.speed_privacy_limit(db, "DK") == 30
    assert settings.speed_privacy_limit(db, "ZZ") == 10
    assert settings.speed_privacy_limit(db, "FR") is None      # replaced wholesale


# --- group boards must not re-identify an anonymised rider ------------------------------

def _trip(db, sid, uuid, country, speed, km=10.0, wheel=None):
    db.add(models.Trip(trip_uuid=uuid, rider_store_id=sid, country=country,
                       max_speed=speed, distance_km=km, wheel_id=wheel,
                       validation_status="validated"))
    db.commit()


def test_a_country_does_not_publish_an_over_limit_top_speed(db):
    """The value itself identifies: an anonymous rider at 93.3 and a country whose top speed
    is 93.3 name each other, which undoes the anonymity on the rider board."""
    _rider(db, "ua1", "Fast", "UA", 93.3)
    _trip(db, "ua1", "t-fast", "UA", 93.3)
    _trip(db, "ua1", "t-legal", "UA", 24.0)
    row = [e for e in stats.by_country(db) if e["name"] == "UA"][0]
    assert row["top_speed"] != 93.3, "the over-limit speed is still published"
    assert row["top_speed"] == 24.0, "should fall back to the fastest publishable ride"


def test_a_country_with_no_rule_publishes_normally(db):
    _rider(db, "us9", "Yank", "US", 134.2)
    _trip(db, "us9", "t-us", "US", 134.2)
    row = [e for e in stats.by_country(db) if e["name"] == "US"][0]
    assert row["top_speed"] == 134.2


def test_the_wheel_board_is_masked_too(db):
    """One rider often owns the only wheel of a model, so a model's top speed names them."""
    _rider(db, "dk9", "Dane", "DK", 80.0)
    db.add(models.Wheel(wheel_id="W1", rider_store_id="dk9", brand="Veteran", model="Lynx S"))
    db.commit()
    _trip(db, "dk9", "t-w1", "DK", 80.0, wheel="W1")
    _trip(db, "dk9", "t-w2", "DK", 19.0, wheel="W1")
    row = [e for e in stats.by_wheel(db) if e["name"] == "Lynx S"][0]
    assert row["top_speed"] != 80.0
    assert row["top_speed"] == 19.0


def test_a_country_with_only_over_limit_rides_publishes_nothing(db):
    """Better no value than one that points at a person."""
    _rider(db, "no9", "Norsk", "NO", 60.0)
    _trip(db, "no9", "t-no", "NO", 60.0)
    row = [e for e in stats.by_country(db) if e["name"] == "NO"][0]
    assert row["top_speed"] is None


# --- the country keeps its speed, but not its name -------------------------------------

def test_an_over_limit_country_speed_is_served_detached(db):
    """Asked for: anonymise the COUNTRY, keep the real speed."""
    _rider(db, "ua2", "Fast", "UA", 93.3)
    _trip(db, "ua2", "t-ua", "UA", 93.3)
    anon = stats.anon_country_speeds(db)
    assert len(anon) == 1
    e = anon[0]
    assert e["top_speed"] == 93.3, "the real speed must survive"
    assert e["anon"] is True
    assert e["name"].startswith("Country #")
    assert "UA" not in str(e), "the country leaked into its own anonymised entry"


def test_the_country_row_does_not_also_carry_that_speed(db):
    """Otherwise the response pairs the name and the value straight back together."""
    _rider(db, "ua3", "Fast", "UA", 93.3)
    _trip(db, "ua3", "t-ua", "UA", 93.3)
    row = [e for e in stats.by_country(db) if e["name"] == "UA"][0]
    assert row["top_speed"] is None


def test_a_country_within_its_limit_is_not_detached(db):
    _rider(db, "ua4", "Slow", "UA", 20.0)
    _trip(db, "ua4", "t-slow", "UA", 20.0)
    assert stats.anon_country_speeds(db) == []
    assert [e for e in stats.by_country(db) if e["name"] == "UA"][0]["top_speed"] == 20.0


def test_a_country_with_no_rule_is_never_detached(db):
    _rider(db, "us8", "Yank", "US", 134.2)
    _trip(db, "us8", "t-us", "US", 134.2)
    assert stats.anon_country_speeds(db) == []


def test_two_countries_get_different_marks(db):
    _rider(db, "dk8", "D", "DK", 60.0); _trip(db, "dk8", "t-dk", "DK", 60.0)
    _rider(db, "no8", "N", "NO", 55.0); _trip(db, "no8", "t-no", "NO", 55.0)
    anon = stats.anon_country_speeds(db)
    assert len(anon) == 2
    assert anon[0]["mark"] != anon[1]["mark"]
    assert anon[0]["top_speed"] == 60.0 and anon[1]["top_speed"] == 55.0   # sorted desc
