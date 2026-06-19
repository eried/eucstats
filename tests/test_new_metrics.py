"""Exhaustive tests for the new metrics: Freespin King, Sag Lord, Rocket.

Covers: summary computation + edge cases, aggregator best-of across trips,
leaderboard ordering/exclusions, end-to-end ingest persistence, and the
schema migration's idempotency.
"""
from datetime import datetime

import models
from ingest.parser import Sample
from ingest.summary import (summarize, _max_voltage_sag, _max_sustained_accel, _max_shake,
                            _speed_g, _speed_g_band, _fastest_stop)
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

def test_new_leaderboards_order_and_banned_excluded(db):
    from services import settings
    for sid, fs in (("a", 100.0), ("b", 200.0), ("c", 150.0)):
        db.add(models.Rider(store_id=sid, display_name=sid, platform="google_play"))
        db.add(_trip("t_" + sid, sid, max_freespin=fs, max_voltage_sag=fs / 10,
                     sustained_accel=fs / 10))
    # a banned rider with a huge value must NOT appear (deleted riders still would)
    db.add(models.Rider(store_id="ban", display_name="ban", platform="google_play"))
    db.add(_trip("t_ban", "ban", max_freespin=999.0))
    db.commit()
    settings.ban(db, "ban", "fraud")
    rebuild_all(db)

    fk = stats.freespin_leaderboard(db)
    ids = [r["store_id"] for r in fk]
    assert ids[:3] == ["b", "c", "a"]          # descending
    assert "ban" not in ids                      # banned excluded
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
        client.post("/api/v1/riders", json={"store_id": "m_test", "display_name": "Metric", "flag": "NO"})
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


# ---------- newer hidden metrics: sustained windows / high-speed / directional / shake ----------

def test_sustained_windows_hold_a_steady_value():
    # hold everything constant for 12s -> every sustained-window metric == that value
    samples = [_s(i, speed=30, gps_speed=30, voltage=80, current=10, pwm=50, g=1.5)
               for i in range(12)]
    sm = summarize(samples)
    assert round(sm.speed_sust_5s, 1) == 30.0 and round(sm.speed_sust_10s, 1) == 30.0
    assert round(sm.g_sust_4s, 2) == 1.5 and round(sm.g_sust_6s, 2) == 1.5
    assert round(sm.pwm_sust_3s, 0) == 50 and round(sm.current_sust_6s, 0) == 10
    assert round(sm.power_sust_6s, 0) == 800        # 80 V * 10 A


def test_longer_window_dilutes_a_spike_more():
    # one 1s g-spike of 10 between long calm stretches: the wider window averages it down further
    samples = ([_s(i, g=1.0) for i in range(5)] + [_s(5, g=10.0)]
               + [_s(i, g=1.0) for i in range(6, 12)])
    sm = summarize(samples)
    assert sm.g_sust_6s < sm.g_sust_4s


def test_high_speed_g_only_counts_while_fast():
    # a hard 2.0 g while crawling at 5 km/h is ignored; only the 0.5 g at 35 km/h counts
    slow = [_s(i, speed=5, gps_speed=5, g=2.0) for i in range(4)]
    fast = [_s(i, speed=35, gps_speed=35, g=0.5) for i in range(4, 8)]
    sm = summarize(slow + fast)
    assert round(sm.g_fast_20, 2) == 0.5 and round(sm.g_fast_30, 2) == 0.5
    assert sm.g_fast_40 is None                     # never rode above 40 km/h


def test_directional_g_from_axes():
    sm = summarize([_s(i, speed=20, gps_speed=20, gx=0.7, gy=-0.6, g=1.0) for i in range(6)])
    assert round(sm.g_lateral, 2) == 0.7            # |gx| (cornering)
    assert round(sm.g_brake, 2) == 0.6              # |gy| (braking, abs of signed)


def test_shake_index_detects_oscillation_not_steady_corner():
    steady = summarize([_s(i, gx=0.8, g=0.8) for i in range(6)])          # constant lateral g
    shaky = summarize([_s(i, gx=(0.8 if i % 2 == 0 else -0.8), g=0.8)     # rapid side-to-side
                       for i in range(6)])
    assert shaky.shake_index is not None and shaky.shake_index > 0.5
    assert (steady.shake_index or 0.0) < shaky.shake_index
    assert _max_shake([_s(0, gx=1.0)]) is None      # need >=3 points


def test_speed_g_accel_and_brake():
    # accelerate 0->40 km/h at +10 km/h/s (≈0.283 g), then brake 40->0 at -20 km/h/s (≈0.566 g)
    samples = [_s(0, speed=0, gps_speed=0), _s(1, speed=10, gps_speed=10),
               _s(2, speed=20, gps_speed=20), _s(3, speed=30, gps_speed=30),
               _s(4, speed=40, gps_speed=40),
               _s(5, speed=20, gps_speed=20), _s(6, speed=0, gps_speed=0)]
    sm = summarize(samples)
    assert abs(sm.accel_g - 0.283) < 0.02
    assert abs(sm.brake_g - 0.566) < 0.02
    assert sm.brake_g > sm.accel_g                 # the stop was harder than the launch


def test_speed_g_helper_edges():
    assert _speed_g([_s(0, speed=10)]) == (None, None)        # need >=2 speeds
    a, b = _speed_g([_s(0, speed=0, gps_speed=0), _s(2, speed=40, gps_speed=40)])  # 20 km/h/s over 2s
    assert a is not None and abs(a - 0.566) < 0.02 and b is None   # pure acceleration, no braking


def test_moving_seconds_excludes_stops():
    # stopped, then rolling at 20 km/h for 5 s, then stopped again (1 s sampling)
    samples = ([_s(0, speed=0, gps_speed=0)]
               + [_s(i, speed=20, gps_speed=20) for i in range(1, 6)]
               + [_s(i, speed=0, gps_speed=0) for i in range(6, 9)])
    sm = summarize(samples)
    assert sm.moving_s == 5.0          # only the rolling seconds count, not the stopped ones
    assert sm.avg_speed == 20.0        # average over moving samples only (the 0s are excluded)


def test_speed_band_and_stop_helpers():
    # roll-on: accelerate while already fast (40->60 over 2s = 10 km/h/s ≈ 0.283 g, starts >=30 and >=50)
    a30, b30 = _speed_g_band([_s(0, speed=40, gps_speed=40), _s(2, speed=60, gps_speed=60)], 30.0)
    assert a30 is not None and abs(a30 - 0.283) < 0.03 and b30 is None
    a50, b50 = _speed_g_band([_s(0, speed=40, gps_speed=40), _s(2, speed=60, gps_speed=60)], 50.0)
    assert a50 is None                                      # window started at 40, below the 50 band
    # emergency stop: 30 -> 0 over 2s
    samples = [_s(0, speed=30, gps_speed=30), _s(1, speed=15, gps_speed=15), _s(2, speed=0, gps_speed=0)]
    assert _fastest_stop(samples, 30.0) == 2.0
    assert _fastest_stop(samples, 50.0) is None            # never reached 50


def test_sprints_use_corroborated_speed():
    # GPS says fast early (spoof attempt) but wheel speed is the real, slower curve -> uses the min
    samples = [_s(0, speed=0, gps_speed=0), _s(1, speed=20, gps_speed=99),
               _s(2, speed=40, gps_speed=99), _s(3, speed=60, gps_speed=99)]
    sm = summarize(samples)
    assert sm.fastest_0_40_s == 2.0 and sm.t_0_60_s == 3.0   # corroborated (min) speed, not the GPS spoof


def test_sprint_thresholds_interpolated_so_40_and_60_differ():
    # coarse launch 0 -> 10 -> 70 km/h: it blows through both 40 and 60 between the last two
    # readings. Without interpolation both snap to the same sample (the reported bug); with it
    # they differ. Crossing 40 at ~50% of the 2s gap (=3.0s), 60 at ~83% (~3.67s).
    samples = [_s(0, speed=0, gps_speed=0), _s(2, speed=10, gps_speed=10), _s(4, speed=70, gps_speed=70)]
    sm = summarize(samples)
    assert sm.fastest_0_40_s is not None and sm.t_0_60_s is not None
    assert sm.t_0_60_s > sm.fastest_0_40_s
    assert abs(sm.fastest_0_40_s - 3.0) < 0.05 and abs(sm.t_0_60_s - 3.667) < 0.05


def test_new_gated_boards_registered_and_default_off():
    from services import settings
    for base in ("g4", "g6", "pwm3", "spd5", "spd10", "pw6", "cur6",
                 "gf20", "gf30", "gf40", "shake", "accg", "brkg",
                 "sprint60", "sprint100", "acc30", "acc50", "brk30", "brk50", "stop30", "stop50"):
        for suf, *_ in settings.GATE_TIERS:
            k = f"{base}_{suf}"
            assert k in stats.BOARDS                       # leaderboard callable wired
            assert k in settings.DEFAULT_OFF_BOARDS        # ships hidden until enabled
    for gone in ("gbrk_b", "glat_b"):                      # the fakeable IMU boards were dropped
        assert gone not in stats.BOARDS
        assert f"b.{base}.n" in __import__("web.i18n", fromlist=["EN"]).EN


def test_new_gated_leaderboard_returns_qualifying_max(db):
    from services.aggregator import rebuild_all
    db.add(models.Rider(store_id="g", display_name="G", platform="google_play"))
    T = lambda u, **kw: models.Trip(trip_uuid=u, rider_store_id="g",
                                    validation_status="validated", **kw)
    # both rides clear the briefest tier 'b' gate (>=300 s & >=1.5 km); board takes the per-rider max
    db.add(T("g1", duration_s=600, distance_km=5.0, brake_g=0.5, speed_sust_5s=30.0))
    db.add(T("g2", duration_s=600, distance_km=5.0, brake_g=0.9, speed_sust_5s=42.0))
    # a too-short ride must NOT qualify even with a huge value
    db.add(T("g3", duration_s=10, distance_km=0.2, brake_g=9.9))
    db.commit()
    rebuild_all(db)
    brk = stats.BOARDS["brkg_b"](db, limit=10)
    assert brk and brk[0]["store_id"] == "g" and brk[0]["v"] == 0.9   # max, short ride excluded
    assert stats.BOARDS["spd5_b"](db, limit=10)[0]["v"] == 42.0
