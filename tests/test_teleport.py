"""The teleport rule must catch spoofing without punishing ordinary GPS noise.

Two ways it used to fire on honest rides:
  * the only test was IMPLIED SPEED, so at 1 Hz any hop over ~42 m read as >150 km/h —
    well inside normal phone GPS error in a city, and one bad fix makes two hops;
  * the cap was a flat count, so a long ride collected more noise events than a short
    one purely by being long.
"""
from datetime import datetime, timedelta, timezone

from ingest.parser import Sample
from ingest.plausibility import check
from ingest.summary import summarize, teleport_segments

BASE = datetime(2026, 6, 1, tzinfo=timezone.utc)
M_PER_DEG_LAT = 111320.0


def mk(sec, **kw):
    return Sample(t=BASE + timedelta(seconds=sec), **kw)


def ride(n, noise_every=None, noise_m=45.0, hz=1.0):
    """A steady 1 Hz ride, optionally with a GPS fix knocked `noise_m` off course
    periodically — the everyday multipath spike near buildings and trees."""
    out = []
    for i in range(n):
        lat = 55.0 + i * 1e-5
        if noise_every and i % noise_every == 0 and i:
            lat += noise_m / M_PER_DEG_LAT
        out.append(mk(i / hz, lat=lat, lon=12.0, speed=20.0, odo=100.0 + i * 0.005, g=0.4))
    return out


def jumps(samples, **kw):
    pts = [(s.t, s.lat, s.lon, s.speed) for s in samples]
    return len(teleport_segments(pts, **kw))


# --- the noise floor: a hop has to actually move you somewhere ---

def test_ordinary_gps_noise_is_not_a_teleport():
    """45 m off course in 1 s implies 162 km/h, but nobody teleported 45 metres."""
    assert jumps(ride(40, noise_every=5)) == 0


def test_a_real_jump_is_still_a_teleport():
    """Half a kilometre in one second is not GPS error."""
    assert jumps(ride(40, noise_every=5, noise_m=500.0)) > 0


def test_noise_floor_is_the_binding_test_at_high_sample_rates():
    """At 5 Hz the speed test alone would flag an 8 m wobble. The distance floor stops it."""
    assert jumps(ride(40, noise_every=5, noise_m=30.0, hz=5.0)) == 0


# --- ride length must not decide the verdict ---

def test_long_clean_ride_is_not_flagged_for_being_long():
    s = ride(4000, noise_every=100)          # ~66 min, a noise spike every 100 s
    assert "teleport" not in check(s, summarize(s))[1]


def test_systematic_teleporting_is_still_flagged():
    """Spoofing corrupts a large fraction of fixes, not a stray few."""
    s = [mk(i, lat=(55.0 if i % 2 == 0 else 55.5), lon=12.0, speed=20.0,
            odo=100.0 + i * 0.01, g=0.4) for i in range(60)]
    assert "teleport" in check(s, summarize(s))[1]


def test_allowance_scales_with_the_number_of_fixes():
    """The same handful of genuine jumps: fatal on a short ride, tolerable on a long one."""
    def with_jumps(n_fixes, n_jumps):
        s = ride(n_fixes)
        for i in range(1, 2 * n_jumps, 2):   # each displaced fix makes an out-and-back pair
            s[i].lat += 800.0 / M_PER_DEG_LAT
        return s
    short = with_jumps(200, 12)
    long_ = with_jumps(4000, 12)
    assert "teleport" in check(short, summarize(short))[1]
    assert "teleport" not in check(long_, summarize(long_))[1]


# --- the existing guards must survive ---

def test_indoor_drift_and_tunnels_still_ignored():
    indoor = [mk(i, lat=(55.0 if i % 2 == 0 else 55.5), lon=12.0, speed=0.0, odo=100.0)
              for i in range(20)]
    assert "teleport" not in check(indoor, summarize(indoor), teleport_max_jumps=1)[1]
    tunnel = [mk(i * 100, lat=(55.0 if i % 2 == 0 else 55.5), lon=12.0, speed=20.0,
                 odo=100.0 + i * 0.4) for i in range(20)]
    assert "teleport" not in check(tunnel, summarize(tunnel), teleport_max_jumps=1)[1]
