"""Realistic top speed: reached via believable acceleration (~20 km/h/s);
instantaneous spikes are reported separately as freespin, not as max speed."""
from datetime import datetime

from ingest.parser import Sample
from ingest.summary import summarize


def _s(sec, sp, gps):
    return Sample(t=datetime(2026, 6, 4, 10, 0, sec), speed=sp, gps_speed=gps)


def test_gps_noise_spike_rejected():
    # walking the wheel slowly indoors: wheel ~2 km/h, GPS noise spikes to 28
    sm = summarize([_s(0, 2, 3), _s(1, 2, 28), _s(2, 3, 2), _s(3, 2, 3)])
    assert sm.max_speed <= 3


def test_wheel_freespin_rejected():
    # wheel lifted and spinning fast, GPS knows you're not moving
    sm = summarize([_s(0, 50, 1), _s(1, 55, 1), _s(2, 3, 2)])
    assert sm.max_speed <= 3


def test_real_speed_kept():
    sm = summarize([_s(0, 40, 39), _s(1, 42, 41)])
    assert sm.max_speed >= 39


def test_no_gps_falls_back_to_wheel():
    sm = summarize([_s(0, 20, None), _s(1, 22, None)])
    assert sm.max_speed == 22


def test_instant_spike_is_freespin_not_speed():
    # both sensors jump 5 -> 80 in one second (75 km/h/s — impossible) then drop.
    # The realistic max is clamped to what ~20 km/h/s allows (5 + 20 = 25); the
    # 80 km/h reading is recorded as freespin, a separate category.
    sm = summarize([_s(0, 5, 5), _s(1, 80, 80), _s(2, 6, 6)])
    assert sm.max_speed <= 26
    assert sm.max_freespin == 80


def test_believable_hard_launch_counts():
    # 0 -> 48 km/h over 3 s = 16 km/h/s, under the cap: this is a real top speed.
    sm = summarize([_s(0, 0, 0), _s(1, 16, 16), _s(2, 32, 32), _s(3, 48, 48)])
    assert sm.max_speed == 48
    assert sm.max_freespin is None


def test_sustained_high_speed_is_real():
    # ramps up believably and holds 60 km/h — not a spike, so no freespin flag.
    sm = summarize([_s(0, 20, 20), _s(1, 40, 40), _s(2, 58, 58), _s(3, 60, 60), _s(4, 60, 60)])
    assert sm.max_speed >= 58
    assert sm.max_freespin is None
