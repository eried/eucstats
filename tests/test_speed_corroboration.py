"""Top-speed = max of per-sample min(wheel, gps): rejects GPS noise + freespin."""
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
