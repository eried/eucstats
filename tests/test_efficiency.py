"""Wh/km must be a real consumption figure, not an artifact.

Real EUC efficiency is roughly 15-80 Wh/km. The brand/wheel boards were showing 0.1-0.7,
which is physically impossible, for two compounding reasons:

  * `_energy_wh` summed SIGNED power, so regenerative braking cancelled consumption and the
    net integral collapsed toward zero — the harder you brake, the "better" you scored;
  * the boards take min(wh_per_km) with no qualifying gate, so the single most degenerate
    trip (often a few hundred metres) became the model's headline efficiency.

Nulling the metric rather than clamping it keeps a bad reading off the board entirely,
instead of inventing a plausible-looking number.
"""
from datetime import datetime, timedelta, timezone

from ingest.parser import Sample
from ingest.summary import summarize

BASE = datetime(2026, 6, 1, tzinfo=timezone.utc)


def mk(sec, **kw):
    return Sample(t=BASE + timedelta(seconds=sec), **kw)


def ride(n=600, kmh=30.0, volts=100.0, amps=9.0, regen_every=None, regen_mult=2.0):
    """A steady ride. 30 km/h at 100 V x 9 A = 900 W = 30 Wh/km, a normal figure.
    `regen_every` makes every Nth sample a braking burst with current flowing back."""
    out = []
    for i in range(n):
        a = -amps * regen_mult if (regen_every and i % regen_every == 0) else amps
        out.append(mk(i, speed=kmh, odo=100.0 + (kmh / 3600.0) * i, voltage=volts, current=a))
    return out


def test_a_normal_ride_reports_a_normal_figure():
    sm = summarize(ride())
    assert 4.9 <= sm.distance_km <= 5.0
    assert 28.0 <= sm.wh_per_km <= 32.0, f"expected ~30 Wh/km, got {sm.wh_per_km}"


def test_regen_cannot_cancel_consumption():
    """The reported bug: braking hard drove the integral toward zero and 'won' the board."""
    sm = summarize(ride(regen_every=4))
    assert sm.wh_per_km is not None
    assert sm.wh_per_km >= 15.0, f"regen still deflating consumption: {sm.wh_per_km} Wh/km"


def test_a_physically_impossible_figure_is_not_reported():
    """0.1-0.7 Wh/km is not a wheel being efficient, it is a broken power channel."""
    sm = summarize(ride(amps=0.02))          # ~2 W: nothing real draws that while riding
    assert sm.wh_per_km is None


def test_absurdly_high_is_also_rejected():
    sm = summarize(ride(amps=900.0))         # 90 kW
    assert sm.wh_per_km is None


def test_too_short_to_measure():
    """Efficiency over a few hundred metres is noise; the boards must not rank on it."""
    sm = summarize(ride(n=60))               # 0.5 km
    assert sm.distance_km < 1.0
    assert sm.wh_per_km is None


def test_a_qualifying_ride_still_reports():
    sm = summarize(ride(n=400))              # ~3.3 km, comfortably over the gate
    assert sm.wh_per_km is not None
