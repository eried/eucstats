"""Settings: most live in the active dataset's app_meta table (so per-dataset
toggles travel with the file when swapped); the SITE test-mode banner is the
exception — it lives in a global file so it never moves with the data.
Values are stored as strings; helpers coerce the few typed ones we use.
"""
from __future__ import annotations

import json

from sqlalchemy.orm import Session

import config
from models import Meta

# Canonical metric/section lists for the admin show/hide UI.
# Keep keys in sync with web/public.py BOARDS (board `k`) and the dock `data-p`.
# Each entry is (key, label, description). Descriptions mirror the public
# site's own copy (BOARDS/GBOARDS `d:` text in web/public.py) so the admin
# tree explains exactly what visitors see.
METRIC_SECTIONS = [
    ("riders", "Riders", "Individual rider leaderboards across every metric"),
    ("countries", "Countries", "Per-country leaderboards and the world map"),
    ("wheels", "Wheels", "Per wheel-model leaderboards"),
    ("brands", "Brands", "Per-brand leaderboards and the factory → rider flow map"),
    ("records", "Records", "All-time single-ride records"),
    ("tech", "App", "App-version adoption and the device / OS breakdown"),
]
METRIC_BOARDS = [
    ("mileage", "Mile Muncher", "Most distance ever ridden"),
    ("daily", "Day Crusher", "Most distance in a single day"),
    ("week", "Week Beast", "Most distance in one week"),
    ("month", "Month Monster", "Most distance in one calendar month"),
    ("speed", "Speedy Gonzales", "Highest speed reached on any ride"),
    ("accel", "Drag Racer", "Fastest launch from a stop to 40 km/h · lower is better"),
    ("gforce", "G-Force Hero", "Strongest g-force spike"),
    ("power", "Watt Beast", "Highest power held for 2 seconds"),
    ("current", "Amp Demon", "Highest current held for 2 seconds"),
    ("voltage", "Volt Lord", "Highest battery voltage observed"),
    ("streak", "Streak Master", "Longest run of consecutive days ridden"),
    ("ascent", "Everest Climber", "Total elevation climbed (Everest = 8849 m)"),
    ("range", "Long Hauler", "Longest estimated full-charge range"),
    ("efficiency", "Eco Rider", "Lowest energy use per km · most efficient"),
    ("hours", "Steel Legs", "Most hours on the wheel"),
    ("cruise", "Sunday Cruiser", "Longest calm ride held under 10 km/h"),
    ("globe", "Globe Trotter", "Most countries ridden in"),
    ("altking", "Altitude King", "Biggest altitude swing in one ride"),
    ("frequent", "Frequent Flyer", "Most rides logged"),
    ("marathon", "Marathoner", "Longest single ride by time"),
    ("pace", "Pace Maker", "Highest average speed on a single ride"),
    ("battery", "Battery Vampire", "Biggest battery drain in one ride"),
    ("night", "Night Rider", "Most rides started at night (22:00–05:00 UTC)"),
    ("weekend", "Weekend Warrior", "Most distance ridden on weekends"),
    ("early", "Early Bird", "Most rides started in the morning (05:00–09:00 UTC)"),
    ("peak", "Peak Bagger", "Biggest elevation gain in a single ride"),
    ("energy", "Power Plant", "Most total energy used across all rides"),
    ("explorer", "Explorer", "Most distinct map areas ridden"),
    ("bigday", "Big Day", "Most rides in a single day"),
    ("commuter", "Commuter", "Most distance ridden on weekdays"),
    ("freespin", "Freespin King", "Biggest freespin / spin-up spike (wheel lifted or a crash)"),
    ("sag", "Sag Lord", "Biggest voltage drop under load — the hardest battery pull"),
    ("rocket", "Rocket", "Hardest sustained acceleration held for 2s or more"),
]
# Inner sub-panels of the "App & OS" section (keys match version_stats output).
METRIC_APP = [
    ("adoption", "Adoption", "Share of riders on the latest app version"),
    ("adopters", "Bleeding Edge", "Riders running the newest app version"),
    ("laggards", "Living in the past", "Riders stuck on the oldest app version"),
    ("appvers", "App versions", "Distribution of riders across app versions"),
    ("osvers", "OS versions", "Distribution of riders across Android / OS versions"),
    ("countries", "Up-to-date countries", "Which countries run the newest app"),
]
# Group leaderboard tabs shown inside Countries / Wheels / Brands (GBOARDS keys).
METRIC_GROUPS = [
    ("dist", "Mile Muncher", "Most distance ridden"),
    ("speed", "Speedy Gonzales", "Fastest ride in the group"),
    ("accel", "Drag Racer", "Fastest 0→40 km/h · lower is better"),
    ("gforce", "G-Force Hero", "Strongest g-force spike"),
    ("power", "Watt Beast", "Highest power held 2s"),
    ("current", "Amp Demon", "Highest current held 2s"),
    ("voltage", "Volt Lord", "Highest battery voltage"),
    ("riders", "Riders", "Active riders"),
    ("trips", "Rides", "Rides logged"),
    ("ascent", "Everest Climber", "Total elevation climbed"),
    ("range", "Long Hauler", "Longest est. range"),
    ("eff", "Eco Rider", "Lowest Wh/km · best"),
]
# All-time single records shown in the Records section (keys match Record.key / RECLABEL).
METRIC_RECORDS = [
    ("mileage_king", "Mileage King", "Most lifetime distance ridden"),
    ("top_speed", "Top Speed", "Fastest speed ever recorded"),
    ("longest_trip", "Longest Trip", "Longest single ride by distance"),
    ("max_gforce", "Max G-Force", "Strongest g-force ever recorded"),
    ("sustained_w", "Sustained Power", "Highest power held for 2 seconds"),
    ("sustained_a", "Sustained Current", "Highest current held for 2 seconds"),
    ("peak_voltage", "Voltage Peak", "Highest battery voltage observed"),
]

# --- ingest pipeline plausibility rules (admin-toggleable) ---
# (key, label, description, [threshold keys it uses]). Keys match plausibility.check()'s
# flag reasons; the threshold keys reference PIPELINE_THRESHOLDS below. Some rules are
# pure booleans with no tunable parameters.
PIPELINE_RULES = [
    ("mock_location", "Mock-location flag",
     "Flag trips the device reported as using a mock / spoofed GPS provider.", []),
    ("unverified_distance", "Unverified distance (no GPS)",
     "Flag a long trip with no GPS fix at all — odometer-only distance is trivially faked.",
     ["unverified_dist_km"]),
    ("impossible_speed", "Impossible speed",
     "Flag if the realistic (acceleration-corroborated) top speed exceeds the ceiling. "
     "Momentary crash / freespin spikes are recorded as warnings, not cheats.", ["max_kmh"]),
    ("impossible_gforce", "Impossible g-force",
     "Flag if the sustained (2s) g-force exceeds a physical limit. "
     "A fall spikes g for a split second — that's a warning, not a cheat.", ["max_g"]),
    ("teleport", "GPS teleporting",
     "Flag many GPS point-to-point jumps that imply impossible travel speed.",
     ["teleport_kmh", "teleport_max_jumps"]),
    ("distance_mismatch", "Odometer vs GPS mismatch",
     "Flag when odometer distance and GPS-measured distance disagree beyond tolerance.",
     ["dist_tolerance"]),
    ("overlapping_trip", "Overlapping trips",
     "Flag a trip whose time window overlaps another of the rider's trips "
     "(two wheels at once / the same ride uploaded twice).", []),
]
# Tunable thresholds: (key, label, meta_key, config_attr, kind, lo, hi).
PIPELINE_THRESHOLDS = [
    ("max_kmh", "Max wheel speed (km/h)", "thr_max_kmh", "MAX_KMH", "float", 1, 500),
    ("max_g", "Max g-force (g)", "thr_max_g", "MAX_G", "float", 1, 50),
    ("teleport_kmh", "Teleport speed (km/h)", "thr_teleport_kmh", "TELEPORT_KMH", "float", 1, 2000),
    ("teleport_max_jumps", "Teleport jumps allowed", "thr_teleport_jumps", "TELEPORT_MAX_JUMPS", "int", 0, 1000),
    ("dist_tolerance", "Odo/GPS mismatch tolerance (0–1)", "thr_dist_tol", "DIST_TOLERANCE", "float", 0, 1),
    ("unverified_dist_km", "Unverified distance limit (km)", "thr_unverified_km", "UNVERIFIED_DIST_KM", "float", 0, 10000),
]


def pipeline_disabled(db: Session) -> set:
    """Set of plausibility-rule keys the admin has switched OFF."""
    return set(_json_list(db, "pipeline_disabled"))


def set_pipeline_enabled(db: Session, enabled_keys) -> None:
    enabled = set(enabled_keys)
    disabled = [k for k, *_ in PIPELINE_RULES if k not in enabled]
    set_meta(db, "pipeline_disabled", json.dumps(disabled))


def get_thresholds(db: Session) -> dict:
    out = {}
    for key, _lbl, mkey, cattr, kind, lo, hi in PIPELINE_THRESHOLDS:
        default = getattr(config, cattr)
        raw = get_meta(db, mkey, None)
        val = raw if raw is not None else default
        out[key] = _clamp_int(val, default, lo, hi) if kind == "int" else _clamp_float(val, default, lo, hi)
    return out


def set_thresholds(db: Session, values: dict) -> None:
    for key, _lbl, mkey, cattr, kind, lo, hi in PIPELINE_THRESHOLDS:
        v = values.get(key)
        if v in (None, ""):
            continue
        default = getattr(config, cattr)
        v = _clamp_int(v, default, lo, hi) if kind == "int" else _clamp_float(v, default, lo, hi)
        set_meta(db, mkey, str(v))


# --- data retention (admin-overridable; falls back to env/config defaults) ---

def get_retention(db: Session) -> dict:
    return {
        "days": _clamp_int(get_meta(db, "ret_days", config.RETENTION_DAYS),
                           config.RETENTION_DAYS, 0, 3650),
        "disk_floor_gb": _clamp_float(get_meta(db, "ret_floor_gb", config.DISK_FLOOR_GB),
                                      config.DISK_FLOOR_GB, 0, 100000),
        "interval_s": _clamp_int(get_meta(db, "ret_interval_s", config.RETENTION_INTERVAL_S),
                                 config.RETENTION_INTERVAL_S, 60, 86400),
    }


def set_retention(db: Session, days, disk_floor_gb, interval_s) -> None:
    set_meta(db, "ret_days", str(_clamp_int(days, config.RETENTION_DAYS, 0, 3650)))
    set_meta(db, "ret_floor_gb", str(_clamp_float(disk_floor_gb, config.DISK_FLOOR_GB, 0, 100000)))
    set_meta(db, "ret_interval_s", str(_clamp_int(interval_s, config.RETENTION_INTERVAL_S, 60, 86400)))


# --- page behaviour (consumed by the public frontend as window.__CFG__) ---
MAP_STYLES = ["dark", "light", "voyager", "satellite", "terrain"]
_FALSEY = ("0", "false", "False", "")


def _clamp_int(value, default, lo, hi):
    try:
        return max(lo, min(hi, int(value)))
    except (TypeError, ValueError):
        return default


def _clamp_float(value, default, lo, hi):
    try:
        return max(lo, min(hi, float(value)))
    except (TypeError, ValueError):
        return default


def get_behaviour(db: Session) -> dict:
    style = get_meta(db, "cfg_map_style", "dark") or "dark"
    src = (get_meta(db, "cfg_intro_src", "/static/intro.mp4") or "").strip() or "/static/intro.mp4"
    return {
        "poll_secs": _clamp_int(get_meta(db, "cfg_poll_secs", "30"), 30, 0, 3600),
        "intro_enabled": get_meta(db, "cfg_intro_enabled", "1") not in _FALSEY,
        "intro_src": src,
        "map_style": style if style in MAP_STYLES else "dark",
        "glitch_enabled": get_meta(db, "cfg_glitch_enabled", "1") not in _FALSEY,
        "glitch_secs": _clamp_int(get_meta(db, "cfg_glitch_secs", "4"), 4, 1, 60),
        "glitch_intensity": _clamp_int(get_meta(db, "cfg_glitch_intensity", "2"), 2, 1, 5),
    }


def set_behaviour(db: Session, poll_secs, intro_enabled, intro_src, map_style, glitch_enabled,
                  glitch_secs=4, glitch_intensity=2) -> None:
    set_meta(db, "cfg_poll_secs", str(_clamp_int(poll_secs, 30, 0, 3600)))
    set_meta(db, "cfg_intro_enabled", "1" if intro_enabled else "0")
    set_meta(db, "cfg_intro_src", (intro_src or "").strip() or "/static/intro.mp4")
    set_meta(db, "cfg_map_style", map_style if map_style in MAP_STYLES else "dark")
    set_meta(db, "cfg_glitch_enabled", "1" if glitch_enabled else "0")
    set_meta(db, "cfg_glitch_secs", str(_clamp_int(glitch_secs, 4, 1, 60)))
    set_meta(db, "cfg_glitch_intensity", str(_clamp_int(glitch_intensity, 2, 1, 5)))


def get_meta(db: Session, key: str, default: str | None = None) -> str | None:
    row = db.get(Meta, key)
    return row.value if row else default


def set_meta(db: Session, key: str, value: str) -> None:
    row = db.get(Meta, key)
    if row:
        row.value = str(value)
    else:
        db.add(Meta(key=key, value=str(value)))
    db.commit()


# --- site test mode (GLOBAL, not per-dataset) -----------------------------
# Stored outside any dataset (config.SITE_STATE_FILE) so switching datasets never
# changes it: test mode is a property of the SITE, not of the data it holds.

def _site_state() -> dict:
    try:
        return json.loads(config.SITE_STATE_FILE.read_text())
    except Exception:
        return {}


def _save_site_state(s: dict) -> None:
    config.SITE_STATE_FILE.write_text(json.dumps(s, indent=2))


def get_test_mode() -> dict:
    """{enabled, text}. Defaults to enabled (fail-safe: show the banner until the
    admin explicitly turns it off) with the text 'TEST DATA'."""
    s = _site_state()
    return {"enabled": s.get("test_mode", True),
            "text": (s.get("test_banner") or "").strip() or "TEST DATA"}


def set_test_mode(enabled, text) -> None:
    s = _site_state()
    s["test_mode"] = bool(enabled)
    s["test_banner"] = (text or "").strip() or "TEST DATA"
    _save_site_state(s)


def is_test_mode() -> bool:
    return get_test_mode()["enabled"]


def ingest_allow(db: Session) -> dict:
    """Ingest allowlist: {enabled, ids}. Admin (app_meta) overrides the env
    default (config.INGEST_ALLOW). When disabled, any registered rider can upload."""
    en = get_meta(db, "ingest_allow_enabled")
    raw = get_meta(db, "ingest_allow_ids")
    if en is None and raw is None:                      # never set in admin -> env default
        return {"enabled": bool(config.INGEST_ALLOW), "ids": list(config.INGEST_ALLOW)}
    ids = _json_list(db, "ingest_allow_ids")
    enabled = (en not in _FALSEY) if en is not None else bool(ids)
    return {"enabled": enabled, "ids": ids}


def set_ingest_allow(db: Session, enabled, ids) -> None:
    set_meta(db, "ingest_allow_enabled", "1" if enabled else "0")
    set_meta(db, "ingest_allow_ids", json.dumps([s.strip() for s in ids if s.strip()]))


# --- banned riders (store_id -> reason) ---------------------------------
# Kept in app_meta (not a DB column) so it travels inside the dataset file and
# never breaks frozen snapshots. A banned rider is rejected at ingest and
# excluded from public stats; their profile reports the ban so the app can show it.

def banned(db: Session) -> dict:
    """Map of {store_id: reason} for all banned riders."""
    try:
        v = json.loads(get_meta(db, "banned_ids", "{}") or "{}")
        return v if isinstance(v, dict) else {}
    except Exception:
        return {}


def is_banned(db: Session, store_id: str) -> bool:
    return bool(store_id) and store_id in banned(db)


def ban_reason(db: Session, store_id: str) -> str | None:
    return banned(db).get(store_id)


def ban(db: Session, store_id: str, reason: str = "") -> None:
    store_id = (store_id or "").strip()
    if not store_id:
        return
    b = banned(db)
    b[store_id] = (reason or "").strip() or "Violation of fair-use / anti-fraud policy"
    set_meta(db, "banned_ids", json.dumps(b))


def unban(db: Session, store_id: str) -> None:
    b = banned(db)
    if b.pop((store_id or "").strip(), None) is not None:
        set_meta(db, "banned_ids", json.dumps(b))


def _json_list(db: Session, key: str) -> list:
    try:
        v = json.loads(get_meta(db, key, "[]"))
        return v if isinstance(v, list) else []
    except Exception:
        return []


def get_hidden(db: Session) -> dict:
    """Keys of boards / dock-sections / App-panels / group-tabs / records to hide publicly."""
    return {"boards": _json_list(db, "hidden_boards"),
            "sections": _json_list(db, "hidden_sections"),
            "app": _json_list(db, "hidden_app"),
            "groups": _json_list(db, "hidden_groups"),
            "records": _json_list(db, "hidden_records")}


def set_hidden(db: Session, boards, sections, app=(), groups=(), records=()) -> None:
    set_meta(db, "hidden_boards", json.dumps(list(boards)))
    set_meta(db, "hidden_sections", json.dumps(list(sections)))
    set_meta(db, "hidden_app", json.dumps(list(app)))
    set_meta(db, "hidden_groups", json.dumps(list(groups)))
    set_meta(db, "hidden_records", json.dumps(list(records)))
