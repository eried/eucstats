"""Gated boards: a min ride-time + distance qualifier filters which trips count,
min/max direction works, and the new boards ship hidden until enabled."""
import models
from services import stats, settings


def _trip(db, sid, uuid, dur_s, dist_km, **cols):
    if db.get(models.Rider, sid) is None:
        db.add(models.Rider(store_id=sid, display_name=sid, platform="google_play"))
    db.add(models.Trip(trip_uuid=uuid, rider_store_id=sid, validation_status="validated",
                       duration_s=dur_s, distance_km=dist_km, **cols))
    db.commit()


def test_gate_filters_by_time_and_distance(db):
    # long ride: temp 55 (qualifies every tier); short ride: temp 99 (only the 5min/1.5km tier)
    _trip(db, "long", "tl", 4000, 50.0, max_temp=55.0)
    _trip(db, "short", "ts", 400, 2.0, max_temp=99.0)
    basic = {r["store_id"]: r["v"] for r in stats.BOARDS["temphigh_b"](db, 10)}   # 5min/1.5km
    epic = {r["store_id"]: r["v"] for r in stats.BOARDS["temphigh_l"](db, 10)}    # 60min/40km
    assert basic.get("short") == 99.0 and basic.get("long") == 55.0   # both clear the basic gate
    assert "short" not in epic and epic.get("long") == 55.0           # short ride excluded from epic


def test_min_direction_orders_ascending(db):
    _trip(db, "a", "ta", 4000, 50.0, min_battery_pct=12.0)
    _trip(db, "b", "tb", 4000, 50.0, min_battery_pct=3.0)
    rows = stats.BOARDS["battlow_l"](db, 10)            # Running on Fumes: lowest battery first
    assert rows[0]["store_id"] == "b" and rows[0]["v"] == 3.0


def test_new_boards_ship_hidden(db):
    h = settings.get_hidden(db)
    assert "temphigh_m" in h["boards"] and "althigh" in h["boards"]   # default-off
    settings.mark_boards_shown(db, ["althigh"])                       # admin enables it once
    db.expire_all()
    assert "althigh" not in settings.get_hidden(db)["boards"]         # no longer forced off
