"""SQLAlchemy models for eucstats (see spec §5)."""
from datetime import datetime, timezone

import re
import secrets

from sqlalchemy import (
    Boolean, Column, Date, DateTime, Float, ForeignKey, Integer, JSON,
    LargeBinary, String, Text, event,
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
    # The id the public API uses. The store_id must NOT appear in public payloads: it is what
    # the app authenticates uploads with, and it was being published in every leaderboard row
    # — which made it a stable cross-site identifier for every rider AND meant anyone could
    # read one off the site and use it. Avatars and row keys only ever needed an opaque
    # handle, so they get one.
    public_id = Column(String, unique=True, index=True)
    created_at = Column(DateTime, default=utcnow)
    deleted_at = Column(DateTime)



# What a handle is: the sixteen hex characters `secrets.token_hex(8)` makes. Anything else
# came from somewhere this code does not control -- an import, a migration, a stray script --
# and must never be published, because `pair/confirm` takes a store_id as proof of identity and
# a handle that happens to be one is a password printed on a public leaderboard.
#
# It lives here, beside the minter, because every reader needs the same answer. Checked at two
# call sites instead, a reviewer simply read a different route: the public mileage board and
# the crew contributors list both published a polluted row verbatim to an anonymous caller.
_HANDLE_RE = re.compile(r"[0-9a-f]{16}")


def publishable_handle(public_id) -> bool:
    return bool(public_id) and bool(_HANDLE_RE.fullmatch(public_id))


@event.listens_for(Rider, "before_insert")
def _give_rider_a_public_id(mapper, connection, target):
    """Every rider gets an opaque public handle at creation, without exception.

    A listener rather than a default on the column, and rather than trusting each call site to
    remember: the public API publishes this in place of the store_id, so a rider who somehow
    arrives without one would either vanish from the leaderboards or tempt someone into
    falling back to the store_id — which is the leak this exists to close.
    """
    if not target.public_id:
        target.public_id = secrets.token_hex(8)


class Wheel(Base):
    __tablename__ = "wheels"
    wheel_id = Column(String, primary_key=True)
    rider_store_id = Column(String, ForeignKey("riders.store_id"))
    brand = Column(String)
    model = Column(String)
    ble_name = Column(String)
    ble_mac = Column(String)                    # kept under its own name, not just as the key
    serial = Column(String)
    firmware = Column(String)
    alt_keys = Column(Text)                     # JSON list: every id this wheel has been known by
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
    moving_s = Column(Float)             # time actually rolling (>2 km/h), not the whole log
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
    descent_m = Column(Float)            # elevation lost (downhill total)
    alt_range_m = Column(Float)
    max_altitude_m = Column(Float)       # absolute per-trip extremes (feed gated min/max boards)
    min_altitude_m = Column(Float)
    max_temp = Column(Float)
    min_temp = Column(Float)
    temp_rise_rate = Column(Float)       # fastest sustained board heat-up (deg/s) while riding
    temp_drop_rate = Column(Float)       # fastest sustained board cool-down (deg/s) while riding
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
    cutout_count = Column(Integer, default=0)   # unloaded spin while travelling: a fall
    spin_count = Column(Integer, default=0)     # free spin: the wheel spun up off the ground
    clan_id = Column(String)                    # the crew this was ridden for, stamped at
                                                # ingest and kept: switching crews must not
                                                # redraw months of map
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
    real_ride_count = Column(Integer, default=0)   # rides >=10min moving & >=1km (anti-cheat)
    best_speed = Column(Float, default=0.0)
    best_gforce = Column(Float, default=0.0)
    best_sustained_w = Column(Float, default=0.0)
    best_sustained_a = Column(Float, default=0.0)
    peak_voltage = Column(Float, default=0.0)
    fastest_0_40_s = Column(Float)
    longest_trip_km = Column(Float, default=0.0)
    total_ascent_m = Column(Float, default=0.0)
    total_duration_s = Column(Float, default=0.0)
    total_moving_s = Column(Float, default=0.0)    # summed real ride time (>2 km/h)
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


# --- crews & territory ------------------------------------------------------------------
# A crew is a group of riders; territory is the ground their riding covers. See
# docs/clans-plan.md. Internally everything is `clan` because renaming a column later is
# expensive and renaming a label is not.

class Clan(Base):
    __tablename__ = "clans"
    clan_id = Column(String, primary_key=True)
    name = Column(String, unique=True)
    slug = Column(String, unique=True)
    description = Column(String)
    colour = Column(String)            # hex, from the fixed palette
    pattern = Column(String)           # solid|stripes|dots|hatch — (colour,pattern) is unique
    logo_png = Column(LargeBinary)     # null -> the generated placeholder is used
    join_policy = Column(String, default="approval")   # open|approval|invite
    invite_code = Column(String)
    created_at = Column(DateTime, default=utcnow)
    created_by = Column(String, ForeignKey("riders.store_id"))
    disbanded_at = Column(DateTime)    # set rather than deleted: trips still point here
    # Territory standings, written by the nightly rebuild. Kept here rather than derived per
    # request: the ranking used to recompute the area of every held tile on every page view,
    # which is a full scan plus trigonometry per row for a number that changes once an hour.
    terr_km2 = Column(Float, default=0.0)        # everything held
    terr_best_km2 = Column(Float, default=0.0)     # the largest single connected region
    terr_best_tiles = Column(Integer, default=0)   # the ranked number: squares, not area
    terr_tiles = Column(Integer, default=0)
    terr_regions = Column(Integer, default=0)
    # Fresh squares inside the ranked patch, not across the whole holding. The board prints
    # this next to `terr_best_tiles`, and the two have to be counted over the same ground or
    # the row says a crew gained more than it holds.
    terr_best_fresh = Column(Integer, default=0)
    # Where this crew stands, and where it stood before the last rebuild. The board wrote the
    # standings every hour and never remembered the previous order, so "up two places" was not
    # computable from anything on disk. Null until a crew has been ranked twice -- a new crew,
    # and every crew on the first rebuild after this shipped, has no previous position, and an
    # arrow drawn from a missing one would be a guess.
    terr_rank = Column(Integer)
    terr_prev_rank = Column(Integer)
    targets_json = Column(Text)                  # ground this crew could take next


class ClanMember(Base):
    __tablename__ = "clan_members"
    clan_id = Column(String, ForeignKey("clans.clan_id"), primary_key=True)
    store_id = Column(String, ForeignKey("riders.store_id"), primary_key=True)
    role = Column(String, default="member")     # leader|officer|member
    status = Column(String, default="active")   # active|pending
    joined_at = Column(DateTime, default=utcnow)
    left_at = Column(DateTime)                  # drives the 7-day cooldown
    last_seen = Column(DateTime)                # drives the idle-leader handover


class ClanCell(Base):
    """Kilometres a crew has ridden in one Mercator tile, over the rolling window.

    Rebuilt wholesale by the nightly job rather than maintained incrementally: the window is
    the query, so nothing has to remember what to expire."""
    __tablename__ = "clan_cells"
    tile = Column(String, primary_key=True)     # "z/x/y"
    clan_id = Column(String, primary_key=True)
    km = Column(Float, default=0.0)
    riders = Column(Integer, default=0)
    first_led = Column(DateTime)                # when this crew first took it


class PairToken(Base):
    """One-use, short-lived token behind the scan-to-sign-in flow."""
    __tablename__ = "pair_tokens"
    token = Column(String, primary_key=True)
    code = Column(String, index=True)           # the typeable 6-character form
    store_id = Column(String)                   # null until the app confirms
    purpose = Column(String, default="rider")   # rider|admin
    created_at = Column(DateTime, default=utcnow)
    used_at = Column(DateTime)


class WebSession(Base):
    """What the browser cookie points at. The browser never holds a store_id."""
    __tablename__ = "web_sessions"
    session_id = Column(String, primary_key=True)
    store_id = Column(String)
    scope = Column(String, default="crew")      # crew|admin
    created_at = Column(DateTime, default=utcnow)
    last_used = Column(DateTime, default=utcnow)
