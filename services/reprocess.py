"""Re-summarize trips from their stored raw upload using the CURRENT calibration.

Only trips whose raw blob still exists (within the retention window) can be redone —
older ones had their raw evicted and keep their stored values. This recomputes the
metric values only; a trip's validation status is left untouched.
"""
from __future__ import annotations

import config
from ingest.parser import parse_csv
from ingest.summary import summarize
from models import RawUpload, Trip
from services import settings


def raw_available_count(db) -> int:
    """How many trips still have a raw upload on disk (i.e. can be reprocessed)."""
    return (db.query(Trip.trip_uuid)
            .join(RawUpload, RawUpload.trip_uuid == Trip.trip_uuid).count())


def reprocess_with_calibration(db) -> dict:
    from services.aggregator import rebuild_all
    from services.ingest import _gunzip_capped, _is_gzip

    cal = settings.get_calibration(db)
    thr = settings.get_thresholds(db)
    gps_tol = thr["dist_tolerance"]
    tel_kmh = thr["teleport_kmh"]
    cap = int(config.MAX_DECOMPRESSED_MB * 1024 * 1024)
    rows = (db.query(Trip, RawUpload)
            .join(RawUpload, RawUpload.trip_uuid == Trip.trip_uuid).all())
    done = failed = 0
    for t, ru in rows:
        try:
            raw = ru.blob
            data = _gunzip_capped(raw, cap) if _is_gzip(raw) else raw
            # tz offset only shifts absolute times; metrics use deltas, so 0 is fine
            samples = parse_csv(data.decode("utf-8", "replace"), 0)
            if not samples:
                failed += 1
                continue
            sm = summarize(samples, gps_tolerance=gps_tol, cal=cal, teleport_kmh=tel_kmh)
            # copy recomputed metrics (NOT status / coords / country / wheel / times)
            t.distance_km, t.duration_s = sm.distance_km, sm.duration_s
            t.moving_s = sm.moving_s
            t.max_speed, t.avg_speed = sm.max_speed, sm.avg_speed
            t.max_gforce = sm.max_gforce
            t.wh_per_km = sm.wh_per_km
            t.max_sustained_w, t.max_sustained_a = sm.max_sustained_w, sm.max_sustained_a
            t.peak_voltage = sm.peak_voltage
            t.fastest_0_40_s = sm.fastest_0_40_s
            t.ascent_m, t.alt_range_m = sm.ascent_m, sm.alt_range_m
            t.descent_m, t.cutout_count = sm.descent_m, sm.cutout_count
            t.battery_used_pct, t.est_range_km = sm.battery_used_pct, sm.est_range_km
            t.max_freespin, t.max_voltage_sag = sm.max_freespin, sm.max_voltage_sag
            t.sustained_accel = sm.sustained_accel
            t.g_sust_4s, t.g_sust_6s, t.pwm_sust_3s = sm.g_sust_4s, sm.g_sust_6s, sm.pwm_sust_3s
            t.speed_sust_5s, t.speed_sust_10s = sm.speed_sust_5s, sm.speed_sust_10s
            t.power_sust_6s, t.current_sust_6s = sm.power_sust_6s, sm.current_sust_6s
            t.g_fast_20, t.g_fast_30, t.g_fast_40 = sm.g_fast_20, sm.g_fast_30, sm.g_fast_40
            t.g_lateral, t.g_brake, t.shake_index = sm.g_lateral, sm.g_brake, sm.shake_index
            t.accel_g, t.brake_g = sm.accel_g, sm.brake_g
            t.t_0_60_s, t.t_0_100_s = sm.t_0_60_s, sm.t_0_100_s
            t.accel_g_30, t.accel_g_50 = sm.accel_g_30, sm.accel_g_50
            t.brake_g_30, t.brake_g_50 = sm.brake_g_30, sm.brake_g_50
            t.stop_30_s, t.stop_50_s = sm.stop_30_s, sm.stop_50_s
            mj = dict(t.meta_json) if isinstance(t.meta_json, dict) else {}
            mj.pop("max_gforce_spike", None)
            if sm.max_gforce_spike and sm.max_gforce and sm.max_gforce_spike > sm.max_gforce * 1.3:
                mj["max_gforce_spike"] = round(sm.max_gforce_spike, 3)
            t.meta_json = mj or None
            done += 1
        except Exception:
            failed += 1
    db.commit()
    rebuild_all(db)                       # refresh leaderboards/records from the new values
    return {"reprocessed": done, "failed": failed, "available": len(rows)}
