"""SQLAlchemy models for eucstats (see spec §5)."""
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean, Column, Date, DateTime, Float, ForeignKey, Integer, JSON,
    LargeBinary, String,
)

from database import Base


def utcnow() -> datetime:
    """Naive UTC timestamp (SQLite stores naive; we keep everything UTC)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


# --- Settings / per-dataset flags ---

class Meta(Base):
    """Key/value settings. Lives inside each dataset file, so per-dataset flags
    (e.g. is_test) and future UI toggles survive a dataset swap."""
    __tablename__ = "app_meta"
    key = Column(String, primary_key=True)
    value = Column(String)


# --- Source-of-truth tables ---

class Rider(Base):
    __tablename__ = "riders"
    store_id = Column(String, primary_key=True)
    platform = Column(String, nullable=False, default="google_play")
    display_name = Column(String, nullable=False)
    flag = Column(String)                      # ISO-3166-1 alpha-2
    avatar_png = Column(LargeBinary)           # 64x64 PNG
    last_name_change = Column(DateTime)
    last_flag_change = Column(DateTime)
    last_avatar_change = Column(DateTime)
    consent_public = Column(Boolean, default=True)
    created_at = Column(DateTime, default=utcnow)
    deleted_at = Column(DateTime)


class Wheel(Base):
    __tablename__ = "wheels"
    wheel_id = Column(String, primary_key=True)
    rider_store_id = Column(String, ForeignKey("riders.store_id"))
    brand = Column(String)
    model = Column(String)
    ble_name = Column(String)
    firmware = Column(String)
    first_seen = Column(DateTime, default=utcnow)
    last_seen = Column(DateTime, default=utcnow)


class Trip(Base):
    __tablename__ = "trips"
    trip_uuid = Column(String, primary_key=True)
    rider_store_id = Column(String, ForeignKey("riders.store_id"), index=True)
    wheel_id = Column(String, ForeignKey("wheels.wheel_id"), nullable=True)
    start_utc = Column(DateTime, index=True)
    end_utc = Column(DateTime)
    tz = Column(String)
    tz_known = Column(Boolean, default=True)
    distance_km = Column(Float)
    duration_s = Column(Float)
    max_speed = Column(Float)
    avg_speed = Column(Float)
    max_gforce = Column(Float)
    wh_per_km = Column(Float)
    max_sustained_w = Column(Float)
    max_sustained_a = Column(Float)
    peak_voltage = Column(Float)
    fastest_0_40_s = Column(Float)
    max_freespin = Column(Float)         # biggest instant speed spike (freespin / crash)
    max_voltage_sag = Column(Float)      # biggest voltage drop under load
    sustained_accel = Column(Float)      # highest acceleration held >=2s (km/h per s)
    ascent_m = Column(Float)
    alt_range_m = Column(Float)
    max_altitude_m = Column(Float)       # absolute per-trip extremes (feed gated min/max boards)
    min_altitude_m = Column(Float)
    max_temp = Column(Float)
    min_temp = Column(Float)
    max_pwm = Column(Float)
    min_battery_pct = Column(Float)
    # newer (hidden) gated metrics: longer sustained windows, high-speed / directional g, shake
    g_sust_4s = Column(Float)            # g-force held >=4s / >=6s (steadier than the 2s board)
    g_sust_6s = Column(Float)
    pwm_sust_3s = Column(Float)          # PWM held >=3s
    speed_sust_5s = Column(Float)        # speed held >=5s / >=10s
    speed_sust_10s = Column(Float)
    power_sust_6s = Column(Float)        # power / current held >=6s
    current_sust_6s = Column(Float)
    g_fast_20 = Column(Float)            # sustained g while above 20 / 30 / 40 km/h
    g_fast_30 = Column(Float)
    g_fast_40 = Column(Float)
    g_lateral = Column(Float)            # sustained sideways (cornering) g
    g_brake = Column(Float)              # sustained fore-aft (braking) g
    shake_index = Column(Float)          # experimental wobble index (lateral-g std-dev)
    accel_g = Column(Float)              # longitudinal g from speed change: launch (accel)
    brake_g = Column(Float)              # longitudinal g from speed change: braking
    t_0_60_s = Column(Float)             # cheat-proof sprint times (corroborated speed)
    t_0_100_s = Column(Float)
    accel_g_30 = Column(Float)           # roll-on accel g above 30 / 50 km/h
    accel_g_50 = Column(Float)
    brake_g_30 = Column(Float)           # braking g from 30 / 50 km/h
    brake_g_50 = Column(Float)
    stop_30_s = Column(Float)            # fastest stop from 30 / 50 km/h (lower better)
    stop_50_s = Column(Float)
    battery_used_pct = Column(Float)
    est_range_km = Column(Float)
    country = Column(String, index=True)
    start_cell = Column(String)
    start_lat = Column(Float)
    start_lon = Column(Float)
    validation_status = Column(String, default="validated", index=True)  # validated|flagged|rejected
    flag_reasons = Column(JSON)
    schema_version = Column(String)
    source_app = Column(String)
    is_mock_location = Column(Boolean, default=False)
    sample_count = Column(Integer)
    app_version = Column(String)
    app_build = Column(Integer)
    os_name = Column(String)             # android | ios
    sdk_int = Column(Integer)            # android API level
    device_brand = Column(String)
    device_model = Column(String)
    meta_json = Column(JSON)             # device/gps/sample-rate extras
    aggregated = Column(Boolean, default=False)
    created_at = Column(DateTime, default=utcnow)


class TripTrack(Base):
    __tablename__ = "trip_tracks"
    trip_uuid = Column(String, ForeignKey("trips.trip_uuid"), primary_key=True)
    points = Column(LargeBinary)               # gzip-compressed JSON


class RawUpload(Base):
    __tablename__ = "raw_uploads"
    trip_uuid = Column(String, ForeignKey("trips.trip_uuid"), primary_key=True)
    blob = Column(LargeBinary)
    bytes = Column(Integer)
    received_at = Column(DateTime, default=utcnow, index=True)


# --- Materialized / precomputed tables (public reads hit only these) ---

class RiderStat(Base):
    __tablename__ = "rider_stats"
    store_id = Column(String, ForeignKey("riders.store_id"), primary_key=True)
    total_km = Column(Float, default=0.0)
    trip_count = Column(Integer, default=0)
    best_speed = Column(Float, default=0.0)
    best_gforce = Column(Float, default=0.0)
    best_sustained_w = Column(Float, default=0.0)
    best_sustained_a = Column(Float, default=0.0)
    peak_voltage = Column(Float, default=0.0)
    fastest_0_40_s = Column(Float)
    longest_trip_km = Column(Float, default=0.0)
    total_ascent_m = Column(Float, default=0.0)
    total_duration_s = Column(Float, default=0.0)
    best_range_km = Column(Float, default=0.0)
    best_wh_per_km = Column(Float)
    best_alt_range_m = Column(Float, default=0.0)
    best_freespin = Column(Float, default=0.0)
    best_voltage_sag = Column(Float, default=0.0)
    best_sustained_accel = Column(Float, default=0.0)
    current_streak = Column(Integer, default=0)
    longest_streak = Column(Integer, default=0)
    last_ride_date = Column(Date)


class CountryStat(Base):
    __tablename__ = "country_stats"
    country = Column(String, primary_key=True)
    total_km = Column(Float, default=0.0)
    rider_count = Column(Integer, default=0)
    avg_km_per_rider = Column(Float, default=0.0)


class DailyDistance(Base):
    __tablename__ = "daily_distance"
    store_id = Column(String, primary_key=True)
    date = Column(Date, primary_key=True)
    km = Column(Float, default=0.0)


class MapCell(Base):
    __tablename__ = "map_cells"
    zoom = Column(Float, primary_key=True)
    cell = Column(String, primary_key=True)
    rider_count = Column(Integer, default=0)
    total_km = Column(Float, default=0.0)
    last_activity = Column(DateTime)


class MapCellRider(Base):
    """Association for distinct-rider counting per cell."""
    __tablename__ = "map_cell_riders"
    zoom = Column(Float, primary_key=True)
    cell = Column(String, primary_key=True)
    store_id = Column(String, primary_key=True)


class Record(Base):
    __tablename__ = "records"
    key = Column(String, primary_key=True)     # mileage_king|top_speed|max_gforce|longest_trip
    store_id = Column(String)
    value = Column(Float)
    trip_uuid = Column(String)
    achieved_at = Column(DateTime)


class LeaderboardSnapshot(Base):
    __tablename__ = "leaderboard_snapshots"
    period_type = Column(String, primary_key=True)   # 'week'
    period_key = Column(String, primary_key=True)    # '2026-W22'
    board = Column(String, primary_key=True)         # 'distance'
    payload = Column(JSON)
    generated_at = Column(DateTime, default=utcnow)
