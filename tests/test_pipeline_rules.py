"""Admin-toggleable plausibility rules + tunable thresholds + retention settings."""
from datetime import datetime

from ingest.parser import Sample
from ingest.plausibility import check
from ingest.summary import summarize
from services import settings
from services.retention import run_retention


def _s(sec, **kw):
    return Sample(t=datetime(2026, 6, 1, 10, 0, sec), **kw)


def _fast_samples():
    return [Sample(t=datetime(2026, 6, 1, 10, 0, i), speed=999.0, g=1.0) for i in range(3)]


def test_rule_can_be_disabled():
    samples = _fast_samples()
    sm = summarize(samples)
    _, reasons = check(samples, sm, max_kmh=120)
    assert "impossible_speed" in reasons
    _, reasons = check(samples, sm, max_kmh=120, disabled={"impossible_speed"})
    assert "impossible_speed" not in reasons


def test_crash_speed_spike_is_warning_not_cheat():
    # ~20 km/h ride with one instant 160 km/h spike (a fall): realistic speed stays
    # low so it is NOT flagged as a cheat; the spike is captured as freespin (warning).
    s = [Sample(t=datetime(2026, 6, 1, 10, 0, i), speed=sp, g=1.0)
         for i, sp in enumerate([20.0, 160.0, 20.0, 21.0])]
    sm = summarize(s)
    _, reasons = check(s, sm, max_kmh=120)
    assert "impossible_speed" not in reasons
    assert sm.max_freespin == 160.0


def test_sustained_overspeed_is_cheat():
    s = [Sample(t=datetime(2026, 6, 1, 10, 0, i), speed=200.0, g=1.0) for i in range(4)]
    sm = summarize(s)
    _, reasons = check(s, sm, max_kmh=120)
    assert "impossible_speed" in reasons


def test_gforce_sustained_vs_spike():
    # 0.3g cruising with a single 8g crash spike: the metric (2s sustained) stays
    # low, the spike is kept separately, and it is not flagged as impossible g-force.
    gs = [0.3, 0.3, 8.0, 0.3, 0.3]
    s = [Sample(t=datetime(2026, 6, 1, 10, 0, i), speed=20.0, g=g) for i, g in enumerate(gs)]
    sm = summarize(s)
    assert sm.max_gforce < sm.max_gforce_spike
    assert sm.max_gforce_spike == 8.0
    _, reasons = check(s, sm, max_g=12)
    assert "impossible_gforce" not in reasons


def test_pipeline_enabled_roundtrip(db):
    assert settings.pipeline_disabled(db) == set()
    enabled = [k for k, *_ in settings.PIPELINE_RULES if k not in ("teleport", "mock_location")]
    settings.set_pipeline_enabled(db, enabled)
    assert settings.pipeline_disabled(db) == {"teleport", "mock_location"}


def test_thresholds_roundtrip_and_clamp(db):
    assert "max_kmh" in settings.get_thresholds(db)            # config default present
    settings.set_thresholds(db, {"max_kmh": "80"})
    assert settings.get_thresholds(db)["max_kmh"] == 80.0
    settings.set_thresholds(db, {"max_kmh": "99999"})          # clamps to hi=500
    assert settings.get_thresholds(db)["max_kmh"] == 500.0


def test_calibration_roundtrip_and_clamp(db):
    c = settings.get_calibration(db)
    # all knobs present with their defaults
    for k in ("max_accel", "sustain_secs", "freespin_margin", "accel_target_kmh",
              "sag_window_s", "ascent_hysteresis_m", "odo_max_step_km", "range_min_battery_pct"):
        assert k in c
    assert c["max_accel"] == 20.0 and c["accel_target_kmh"] == 40.0 and c["ascent_hysteresis_m"] == 3.0
    settings.set_calibration(db, {"max_accel": "10", "accel_target_kmh": "50"})
    c = settings.get_calibration(db)
    assert c["max_accel"] == 10.0 and c["accel_target_kmh"] == 50.0
    settings.set_calibration(db, {"max_accel": "999"})           # clamp to hi=100
    assert settings.get_calibration(db)["max_accel"] == 100.0


def test_ascent_hysteresis_param_changes_metric():
    # altitude oscillating ±2 m (noise): counted with a 0.5 m filter, ignored with 3 m
    alts = [100, 102, 100, 102, 100]
    s = [_s(i, speed=10, alt=a) for i, a in enumerate(alts)]
    assert summarize(s, cal={"ascent_hysteresis_m": 0.5}).ascent_m > 0
    assert (summarize(s, cal={"ascent_hysteresis_m": 3.0}).ascent_m or 0) == 0


def test_launch_target_param_changes_metric():
    # ramp 0..50 over 5s (GPS-corroborated, as a real launch must be);
    # "0->40" finds a launch, "0->100" does not
    s = [_s(i, speed=10 * i, gps_speed=10 * i) for i in range(6)]
    assert summarize(s, cal={"accel_target_kmh": 40}).fastest_0_40_s is not None
    assert summarize(s, cal={"accel_target_kmh": 100}).fastest_0_40_s is None


def test_max_accel_param_changes_realistic_speed():
    # 0 -> 50 km/h in 1s. With a 20 km/h/s cap the realistic max is ~20; with a
    # 60 cap the same ramp is believable, so the realistic max reaches 50.
    s = [_s(0, speed=0, gps_speed=0), _s(1, speed=50, gps_speed=50)]
    assert summarize(s, cal={"max_accel": 20}).max_speed <= 21
    assert summarize(s, cal={"max_accel": 60}).max_speed >= 49


def test_retention_settings_roundtrip(db):
    settings.set_retention(db, days=7, disk_floor_gb=2.5, interval_s=120)
    r = settings.get_retention(db)
    assert r["days"] == 7 and r["disk_floor_gb"] == 2.5 and r["interval_s"] == 120


def test_run_retention_uses_admin_days(db):
    settings.set_retention(db, days=7, disk_floor_gb=0, interval_s=3600)
    n = run_retention(db, data_dir=".")     # no raw uploads -> nothing to evict, just must not crash
    assert isinstance(n, int)


def test_system_stats_shape():
    from services.sysinfo import system_stats
    s = system_stats(".")
    assert "disk" in s and "mem" in s and "cpu" in s and "app" in s
    assert s["cpu"]["count"] >= 1


def test_app_footprint():
    from services.sysinfo import app_footprint
    fp = app_footprint(".")
    assert fp is not None and fp["bytes"] > 0
    assert isinstance(fp["breakdown"], list) and fp["breakdown"]
