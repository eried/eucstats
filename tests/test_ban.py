"""Banning a rider: excluded from public stats, refused at ingest, flagged on profile."""
import models
from services import settings, stats
from services.aggregator import Aggregator, rebuild_all


def _rider_with_trip(db):
    db.add(models.Rider(store_id="bad", display_name="Spammer", platform="google_play"))
    db.add(models.Trip(trip_uuid="t1", rider_store_id="bad", validation_status="validated",
                       distance_km=10.0, max_speed=30.0))
    db.commit()
    Aggregator(db).apply(db.get(models.Trip, "t1"))


def test_ban_roundtrip(db):
    assert settings.is_banned(db, "bad") is False
    settings.ban(db, "bad", "GPS spoofing")
    assert settings.is_banned(db, "bad") is True
    assert settings.ban_reason(db, "bad") == "GPS spoofing"
    settings.unban(db, "bad")
    assert settings.is_banned(db, "bad") is False


def test_ban_default_reason(db):
    settings.ban(db, "x")
    assert settings.ban_reason(db, "x")          # non-empty fallback reason


def test_card_reports_ban(db):
    db.add(models.Rider(store_id="bad", display_name="Spammer", platform="google_play"))
    db.commit()
    assert stats.rider_card(db, "bad")["banned"] is False
    settings.ban(db, "bad", "fraud")
    card = stats.rider_card(db, "bad")
    assert card["banned"] is True and card["ban_reason"] == "fraud"


def test_profile_reports_ban(db):
    from services.identity import IdentityService
    db.add(models.Rider(store_id="bad", display_name="Spammer", platform="google_play"))
    db.commit()
    svc = IdentityService(db)
    assert svc.get_profile("bad")["banned"] is False
    settings.ban(db, "bad", "mock GPS")
    p = svc.get_profile("bad")
    assert p["banned"] is True and p["ban_reason"] == "mock GPS"


def test_rebuild_excludes_banned_rider(db):
    _rider_with_trip(db)
    assert stats.global_summary(db)["total_km"] == 10.0
    settings.ban(db, "bad", "fraud")
    rebuild_all(db)
    s = stats.global_summary(db)
    assert s["total_km"] == 0.0 and s["riders"] == 0 and s["trips"] == 0
    # reversible: unban + rebuild restores them
    settings.unban(db, "bad")
    rebuild_all(db)
    assert stats.global_summary(db)["total_km"] == 10.0
