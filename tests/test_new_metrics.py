"""Exhaustive tests for the new metrics: Freespin King, Sag Lord, Rocket.

Covers: summary computation + edge cases, aggregator best-of across trips,
leaderboard ordering/exclusions, end-to-end ingest persistence, and the
schema migration's idempotency.
"""
from datetime import datetime

import models
from ingest.parser import Sample
from ingest.summary import summarize, _max_voltage_sag, _max_sustained_accel
from services import stats
from services.aggregator import Aggregator, rebuild_all


def _s(sec, **kw):
    return Sample(t=datetime(2026, 6, 1, 10, 0, sec), **kw)


# ---------- summary: voltage sag ----------

def test_voltage_sag_basic():
    # 84V resting, dips to 75V under a hard pull, recovers -> sag = 9
    sm = summarize([_s(0, speed=10, voltage=84), _s(1, speed=30, voltage=75),
                    _s(2, speed=20, voltage=83)])
    assert sm.max_voltage_sag == 9.0


def test_voltage_sag_ignores_slow_drain():
    # voltage drifts down 100 -> 96 over 60s (battery draining, no load dip);
    # the 5s rolling window means no single dip is large -> tiny/None sag
    samples = [_s(i * 10, speed=20, voltage=100 - i) for i in range(5)]  # 10s apart
    sm = summarize(samples)
    assert sm.max_voltage_sag is None or sm.max_voltage_sag <= 1.0


def test_voltage_sag_none_without_voltage():
    sm = summarize([_s(0, speed=10), _s(1, speed=20)])
    assert sm.max_voltage_sag is None


def test_voltage_sag_helper_direct():
    assert _max_voltage_sag([_s(0, voltage=80), _s(1, voltage=70)]) == 10.0
    assert _max_voltage_sag([_s(0, voltage=80)]) is None       # need >=2


# ---------- summary: sustained acceleration ----------

def test_sustained_accel_basic():
    # 0 -> 40 km/h over 4s = 10 km/h/s sustained
    sm = summarize([_s(0, speed=0), _s(2, speed=20), _s(4, speed=40)])
    assert sm.sustained_accel == 10.0


def test_sustained_accel_ignores_instant_jump():
    # a 1s spike from 5 -> 60 (55 km/h/s) is below the 2s window -> not counted;
    # the surrounding believable rate governs
    sm = summarize([_s(0, speed=5), _s(1, speed=60), _s(2, speed=8), _s(3, speed=12)])
    assert sm.sustained_accel is None or sm.sustained_accel < 20


def test_sustained_accel_helper_direct():
    assert _max_sustained_accel([_s(0, speed=0), _s(3, speed=30)]) == 10.0
    assert _max_sustained_accel([_s(0, speed=10), _s(3, speed=10)]) is None   # no gain


# ---------- summary: freespin (already wired, re-checked here) ----------

def test_freespin_recorded():
    sm = summarize([_s(0, speed=10, gps_speed=10), _s(1, speed=150, gps_speed=8),
                    _s(2, speed=11, gps_speed=11)])
    assert sm.max_freespin == 150.0
    assert sm.max_speed < 40           # realistic speed is not the spike


# ---------- aggregator: best-of across trips ----------

def _trip(uuid, store, **kw):
    return models.Trip(trip_uuid=uuid, rider_store_id=store,
                       validation_status="validated", distance_km=5.0, **kw)


def test_aggregator_tracks_new_bests(db):
    db.add(models.Rider(store_id="r", display_name="R", platform="google_play"))
    db.add(_trip("t1", "r", max_freespin=120.0, max_voltage_sag=8.0, sustained_accel=9.0))
    db.add(_trip("t2", "r", max_freespin=160.0, max_voltage_sag=5.0, sustained_accel=14.0))
    db.commit()
    agg = Aggregator(db)
    agg.apply(db.get(models.Trip, "t1"))
    agg.apply(db.get(models.Trip, "t2"))
    rs = db.get(models.RiderStat, "r")
    assert rs.best_freespin == 160.0          # max across trips
    assert rs.best_voltage_sag == 8.0
    assert rs.best_sustained_accel == 14.0


# ---------- leaderboards: ordering + exclusions ----------

def test_new_leaderboards_order_and_exclude_deleted(db):
    for sid, fs in (("a", 100.0), ("b", 200.0), ("c", 150.0)):
        db.add(models.Rider(store_id=sid, display_name=sid, platform="google_play"))
        db.add(_trip("t_" + sid, sid, max_freespin=fs, max_voltage_sag=fs / 10,
                     sustained_accel=fs / 10))
    # a deleted rider with a huge value must NOT appear
    db.add(models.Rider(store_id="del", display_name="del", platform="google_play",
                        deleted_at=datetime(2026, 6, 1)))
    db.add(_trip("t_del", "del", max_freespin=999.0))
    db.commit()
    rebuild_all(db)

    fk = stats.freespin_leaderboard(db)
    ids = [r["store_id"] for r in fk]
    assert ids[:3] == ["b", "c", "a"]          # descending
    assert "del" not in ids                      # deleted excluded
    assert fk[0]["freespin_kmh"] == 200.0

    assert stats.sag_leaderboard(db)[0]["store_id"] == "b"
    assert stats.rocket_leaderboard(db)[0]["store_id"] == "b"
    assert stats.BOARDS["freespin"] is stats.freespin_leaderboard


def test_zero_values_excluded_from_boards(db):
    db.add(models.Rider(store_id="z", display_name="z", platform="google_play"))
    db.add(_trip("tz", "z", max_freespin=0.0))   # no freespin -> not on the board
    db.commit()
    rebuild_all(db)
    assert stats.freespin_leaderboard(db) == []


# ---------- end-to-end ingest -> columns -> leaderboard ----------

_E2E_CSV = (
    "Date,Speed,Voltage,Temperature,Battery level,Altitude,Latitude,Longitude,Total mileage,GPS speed,Current,PWM,G-Force,G-Force X,G-Force Y\n"
    "01.06.2026 20:24:31.204,0,84,26,100,68,69.6500,18.9500,1000.0,0,1,0,0.3,0,0\n"
    "01.06.2026 20:24:32.204,10,80,26,100,68,69.6501,18.9501,1000.0,10,8,40,0.3,0,0\n"
    "01.06.2026 20:24:33.204,20,76,26,100,68,69.6502,18.9502,1000.1,20,12,45,0.4,0,0\n"
    "01.06.2026 20:24:34.204,30,78,26,100,68,69.6503,18.9503,1000.1,30,10,45,0.4,0,0\n"
    "01.06.2026 20:24:35.204,40,82,26,100,68,69.6504,18.9504,1000.2,40,6,45,0.3,0,0\n"
    "01.06.2026 20:24:36.204,150,80,26,100,68,69.6505,18.9505,1000.2,5,2,40,0.3,0,0\n"
    "01.06.2026 20:24:37.204,12,84,26,100,68,69.6506,18.9506,1000.2,12,1,10,0.3,0,0\n"
).encode()


def test_ingest_persists_new_metrics_end_to_end(db):
    import gzip
    import json
    from fastapi.testclient import TestClient
    from main import app

    meta = {"store_id": "m_test", "platform": "google_play", "trip_uuid": "m-1",
            "source_app": "eucplanet", "schema_version": "eucplanet-v3-gforce",
            "tz": "Europe/Oslo", "tz_offset_min": 120, "tz_known": True,
            "is_mock_location": False, "wheel": {"serial": "X", "model": "Master"}}
    with TestClient(app) as client:
        client.post("/api/v1/riders", json={"store_id": "m_test", "display_name": "M", "flag": "NO"})
        files = {"trip": ("t.csv.gz", gzip.compress(_E2E_CSV), "application/gzip")}
        r = client.post("/api/v1/trips", data={"meta": json.dumps(meta)}, files=files)
        assert r.status_code == 201, r.text
        assert r.json()["validation_status"] == "validated"   # spike is a warning, not a cheat

    db.expire_all()
    t = db.get(models.Trip, "m-1")
    assert t.max_freespin == 150.0                  # spike captured to the column
    assert t.max_speed is not None and t.max_speed < 60   # realistic, not the spike
    assert t.sustained_accel and t.sustained_accel >= 9    # 0->40 in 4s ~10 km/h/s
    assert t.max_voltage_sag and t.max_voltage_sag >= 7    # 84 -> 76 dip

    fk = stats.freespin_leaderboard(db)
    assert any(e["store_id"] == "m_test" and e["freespin_kmh"] == 150.0 for e in fk)
    assert any(e["store_id"] == "m_test" for e in stats.rocket_leaderboard(db))
    assert any(e["store_id"] == "m_test" for e in stats.sag_leaderboard(db))


# ---------- schema migration idempotency ----------

def test_ensure_schema_idempotent(tmp_path):
    import sqlite3
    from database import ensure_schema, NEW_COLUMNS
    p = tmp_path / "old.sqlite"
    con = sqlite3.connect(str(p))
    con.execute("CREATE TABLE trips (trip_uuid TEXT)")          # pre-migration shape
    con.execute("CREATE TABLE rider_stats (store_id TEXT)")
    con.commit()
    con.close()
    added = ensure_schema(str(p))
    assert "trips.max_freespin" in added and "rider_stats.best_freespin" in added
    assert ensure_schema(str(p)) == []                          # second run is a no-op
    con = sqlite3.connect(str(p))
    cols = {r[1] for r in con.execute("PRAGMA table_info(trips)")}
    con.close()
    for name, _ in NEW_COLUMNS["trips"]:
        assert name in cols
