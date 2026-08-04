"""Estimated range must come from real battery drain, not sensor wiggle.

The live boards showed ranges of 0.7, 0.8 and 1.9 km on wheels that genuinely do 40-100.
Same shape of bug as Wh/km:

  * `_battery_used` summed EVERY downward tick of the battery reading. Battery % sags under
    load and recovers when you ease off, so a ride of ordinary sag accumulated a huge phantom
    "drain" — and est_range = distance x 100 / drain, so inflating drain crushes the range;
  * est_range had no minimum distance and no plausibility bound, so a few hundred metres with
    a noisy reading produced a confident sub-kilometre "range".

Elevation already solves the identical problem with a hysteresis (see `_ascent_m`).
"""
from datetime import datetime, timedelta, timezone

from ingest.parser import Sample
from ingest.summary import summarize

BASE = datetime(2026, 6, 1, tzinfo=timezone.utc)


def mk(sec, **kw):
    return Sample(t=BASE + timedelta(seconds=sec), **kw)


def ride(n=2400, kmh=30.0, drain_pct=20.0, sag=0.0, sag_every=10):
    """A ride draining `drain_pct` linearly. `sag` adds a transient dip that recovers —
    the wheel pulling current, not charge actually leaving the pack."""
    out = []
    for i in range(n):
        b = 100.0 - drain_pct * (i / max(n - 1, 1))
        if sag and i % sag_every == 0:
            b -= sag
        out.append(mk(i, speed=kmh, odo=100.0 + (kmh / 3600.0) * i,
                      voltage=100.0, current=9.0, battery=b))
    return out


def test_clean_drain_gives_a_sensible_range():
    """20 km on 20% of the pack extrapolates to ~100 km."""
    sm = summarize(ride())
    assert 90.0 <= sm.est_range_km <= 110.0, f"got {sm.est_range_km} km"
    assert 18.0 <= sm.battery_used_pct <= 22.0, f"drain {sm.battery_used_pct}%"


def test_sag_does_not_masquerade_as_drain():
    """The reported bug: transient dips summed into a huge phantom drain, crushing range."""
    sm = summarize(ride(sag=4.0))
    assert sm.battery_used_pct <= 30.0, f"sag still inflating drain: {sm.battery_used_pct}%"
    assert sm.est_range_km >= 60.0, f"range still crushed by sag: {sm.est_range_km} km"


def test_short_ride_reports_no_range():
    """A few hundred metres cannot support a full-charge extrapolation."""
    sm = summarize(ride(n=60, drain_pct=15.0))
    assert sm.distance_km < 1.0
    assert sm.est_range_km is None


def test_impossible_range_is_not_reported():
    """A barely-there drain over a long ride extrapolates past any real EUC — a reading error."""
    sm = summarize(ride(n=6000, drain_pct=10.02))     # ~50 km on ~10% -> ~500 km
    assert sm.est_range_km is None


def test_genuine_recovery_still_counts_as_drain_later():
    """Hysteresis must not swallow real consumption that resumes after a plateau."""
    sm = summarize(ride(drain_pct=40.0))
    assert 35.0 <= sm.battery_used_pct <= 45.0, f"real drain lost: {sm.battery_used_pct}%"
