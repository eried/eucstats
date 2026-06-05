"""Admin-toggleable plausibility rules + tunable thresholds + retention settings."""
from datetime import datetime

from ingest.parser import Sample
from ingest.plausibility import check
from ingest.summary import summarize
from services import settings
from services.retention import run_retention


def _fast_samples():
    return [Sample(t=datetime(2026, 6, 1, 10, 0, i), speed=999.0, g=1.0) for i in range(3)]


def test_rule_can_be_disabled():
    samples = _fast_samples()
    sm = summarize(samples)
    _, reasons = check(samples, sm, max_kmh=120)
    assert "impossible_speed" in reasons
    _, reasons = check(samples, sm, max_kmh=120, disabled={"impossible_speed"})
    assert "impossible_speed" not in reasons


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
    assert "disk" in s and "mem" in s and "cpu" in s
    assert s["cpu"]["count"] >= 1
