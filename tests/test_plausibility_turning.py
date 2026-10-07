# -*- coding: utf-8 -*-
"""The turning-in-place allowance on distance_mismatch.

Erwin asked whether short or slow rides deserve more slack. Measured over 1,025 trips on the
production box: distance predicts nothing (median disagreement 4% under 3 km, 3% at 3-10 km,
5% above 10 km), while rides under 10 kph breach 12x more often. Speed, though, is the symptom.

The cause is that GPS joins fixes with straight lines, so an arc is recorded as a chord. Pivot
on the spot and the tyre rolls further than the track can see. That has a signature -- most
samples not moving AND the odometer on the high side -- and these pin that BOTH halves are
required, because each alone describes something the allowance must not cover.
"""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ingest.plausibility import _turning_in_place   # noqa: E402


class _S:
    def __init__(self, lat, lon):
        self.lat, self.lon = lat, lon


class _Sum:
    def __init__(self, odo, gps):
        self.distance_km, self.gps_distance_km = odo, gps


def _parked(n=60):
    """Fixes that barely move: a wheel being turned on the spot."""
    return [_S(69.6500000 + i * 0.0000002, 18.9500000) for i in range(n)]


def _moving(n=60):
    """Fixes a few metres apart: a wheel going somewhere."""
    return [_S(69.65 + i * 0.00005, 18.95) for i in range(n)]


def test_a_parked_wheel_with_a_high_odometer_is_turning_in_place():
    assert _turning_in_place(_parked(), _Sum(odo=2.28, gps=1.05), 0.4) is True


def test_a_moving_wheel_is_not_turning_in_place_however_slow():
    """a7181b05 on the real box: 61% off at 8.5 kph, but only 15% of its samples were still."""
    assert _turning_in_place(_moving(), _Sum(odo=9.56, gps=3.72), 0.4) is False


def test_gps_above_the_odometer_is_never_turning_in_place():
    """Chord-cutting makes the odometer read LONG. It cannot make the GPS read long, so this
    shape has some other cause and keeps its flag."""
    assert _turning_in_place(_parked(), _Sum(odo=4.00, gps=8.43), 0.4) is False


def test_too_few_fixes_says_nothing():
    assert _turning_in_place(_parked(4), _Sum(odo=2.0, gps=1.0), 0.4) is False


def test_the_allowance_is_wired_into_the_check():
    src = (ROOT / "ingest" / "plausibility.py").read_text(encoding="utf-8")
    assert "_turning_in_place(samples, summary, turning_still_share)" in src
    assert "tol = max(dist_tolerance, turning_tolerance)" in src, (
        "the allowance must RAISE the tolerance, never lower it")
