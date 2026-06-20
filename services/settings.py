"""Settings: most live in the active dataset's app_meta table (so per-dataset
toggles travel with the file when swapped); the SITE test-mode banner is the
exception — it lives in a global file so it never moves with the data.
Values are stored as strings; helpers coerce the few typed ones we use.
"""
from __future__ import annotations

import json
import re

from sqlalchemy.orm import Session

import config
from models import Meta


# --- per-wheel data-quality rules ------------------------------------------
# A wheel model can report a bad channel (e.g. wrong voltage). Rather than reject
# whole trips, the admin marks (brand, model) + which metrics are unreliable, with
# an optional app_version cutoff (<= cutoff is invalid; blank = the whole model).
# Those metrics are then ignored everywhere they'd feed a leaderboard/record.
# Stored per-dataset in app_meta. Voltage and power are linked (power = V*A).

# admin-facing metric -> the Trip fields it controls
# Newer sustained / directional g columns ride along with the same metric a bad
# channel would poison (a wheel with bad g-force should drop ALL its g boards, etc.).
WHEEL_METRIC_FIELDS = {
    "speed": ["max_speed", "avg_speed", "speed_sust_5s", "speed_sust_10s"],
    "gforce": ["max_gforce", "g_sust_4s", "g_sust_6s", "g_fast_20", "g_fast_30",
               "g_fast_40", "g_lateral", "g_brake", "shake_index"],
    "power": ["max_sustained_w", "power_sust_6s"],
    "current": ["max_sustained_a", "current_sust_6s"],
    "voltage": ["peak_voltage", "max_voltage_sag"],
    "accel": ["fastest_0_40_s", "sustained_accel", "accel_g", "brake_g",
              "t_0_60_s", "t_0_100_s", "accel_g_30", "accel_g_50",
              "brake_g_30", "brake_g_50", "stop_30_s", "stop_50_s"],
    "altitude": ["max_altitude_m", "min_altitude_m", "alt_range_m", "ascent_m", "descent_m"],
    "range": ["est_range_km"],
    "efficiency": ["wh_per_km"],
    "battery": ["min_battery_pct", "battery_used_pct"],
    "pwm": ["max_pwm", "pwm_sust_3s"],
    "temp": ["max_temp", "min_temp"],
    "freespin": ["max_freespin"],
}
WHEEL_METRICS = list(WHEEL_METRIC_FIELDS)
WHEEL_FIELD_METRIC = {f: m for m, fs in WHEEL_METRIC_FIELDS.items() for f in fs}


def _ver_tuple(v) -> tuple:
    return tuple(int(x) for x in re.findall(r"\d+", str(v or "")))


def _app_ver_le(a, cutoff) -> bool:
    """app_version `a` is <= `cutoff` (numeric/semver-ish, e.g. 0.9.9 <= 0.9.10)."""
    try:
        return _ver_tuple(a) <= _ver_tuple(cutoff)
    except Exception:
        return str(a or "") <= str(cutoff or "")


def get_wheel_rules(db: Session) -> list[dict]:
    raw = get_meta(db, "wheel_quality_rules", None)
    try:
        rules = json.loads(raw) if raw else []
    except Exception:
        rules = []
    return rules if isinstance(rules, list) else []


def set_wheel_rules(db: Session, rules) -> None:
    clean = []
    for r in rules or []:
        b, m = (r.get("brand") or "").strip(), (r.get("model") or "").strip()
        mets = [x for x in (r.get("metrics") or []) if x in WHEEL_METRIC_FIELDS]
        if not (b and m and mets):
            continue                              # a rule with no metrics = no rule
        clean.append({"brand": b, "model": m,
                      "max_app_version": (str(r.get("max_app_version") or "").strip() or None),
                      "metrics": mets})
    set_meta(db, "wheel_quality_rules", json.dumps(clean))


def suppressed_fields(brand, model, app_version, rules) -> set:
    """Trip field names to ignore for one trip given the active rules."""
    out = set()
    for r in rules:
        if r.get("brand") == brand and r.get("model") == model:
            cut = r.get("max_app_version")
            if not cut or _app_ver_le(app_version, cut):
                for met in r.get("metrics", []):
                    out.update(WHEEL_METRIC_FIELDS.get(met, []))
    return out


# --- name canonicalization rules (fix mislabeled brand/model, e.g. Veteran -> Leaperkim) ----
# Same shape as the metric rules but the action is "rename" not "ignore": match a (brand/model)
# optionally bounded by app_version <= cutoff, and overwrite brand/model with canonical values.
# Applied at ingest (new uploads) and as a one-time batch over existing wheels.

def get_name_rules(db: Session) -> list[dict]:
    raw = get_meta(db, "wheel_name_rules", None)
    try:
        rules = json.loads(raw) if raw else []
    except Exception:
        rules = []
    return rules if isinstance(rules, list) else []


def set_name_rules(db: Session, rules) -> None:
    clean = []
    for r in rules or []:
        sb, sm = (r.get("set_brand") or "").strip(), (r.get("set_model") or "").strip()
        if not (sb or sm):
            continue                              # a rule must set at least one field
        clean.append({"m_brand": (r.get("m_brand") or "").strip(),
                      "m_model": (r.get("m_model") or "").strip(),
                      "max_app_version": (r.get("max_app_version") or "").strip(),
                      "set_brand": sb, "set_model": sm})
    set_meta(db, "wheel_name_rules", json.dumps(clean))


def canonicalize_name(brand, model, app_version, rules) -> tuple:
    """(brand, model) reported by app_version -> corrected (brand, model). A rule matches when its
    (optional) brand/model equal the reported values and app_version is within its cutoff; matched
    rules overwrite brand/model with their set_* values."""
    nb, nm = brand, model
    for r in rules:
        if r.get("m_brand") and r["m_brand"] != brand:
            continue
        if r.get("m_model") and r["m_model"] != model:
            continue
        cut = r.get("max_app_version")
        if cut and not _app_ver_le(app_version, cut):
            continue
        if r.get("set_brand"):
            nb = r["set_brand"]
        if r.get("set_model"):
            nm = r["set_model"]
    return nb, nm


def name_fix_changes(db: Session, rules=None) -> list[dict]:
    """Existing wheels whose stored brand/model the rules would change. A cutoff is judged by each
    wheel's FIRST report's app_version (the report that set the stored name)."""
    rules = get_name_rules(db) if rules is None else rules
    if not rules:
        return []
    from models import Trip, Wheel
    first_ver: dict = {}
    for wid, ver in db.query(Trip.wheel_id, Trip.app_version).order_by(Trip.created_at).all():
        first_ver.setdefault(wid, ver)
    out = []
    for w in db.query(Wheel).all():
        nb, nm = canonicalize_name(w.brand, w.model, first_ver.get(w.wheel_id), rules)
        if (nb, nm) != (w.brand, w.model):
            out.append({"wheel_id": w.wheel_id, "brand": w.brand, "model": w.model,
                        "new_brand": nb, "new_model": nm})
    return out


def apply_name_fixes(db: Session, rules=None) -> list[dict]:
    """Rewrite existing wheels' brand/model per the rules; returns the changes made. The caller
    snapshots the dataset first (this is a destructive write)."""
    from models import Wheel
    changes = name_fix_changes(db, rules)
    for c in changes:
        w = db.get(Wheel, c["wheel_id"])
        if w:
            w.brand, w.model = c["new_brand"], c["new_model"]
    if changes:
        db.commit()
    return changes


def blocked_trip_uuids(db: Session, rules=None) -> dict:
    """{metric: set(trip_uuid)} for live-Trip queries (group standings, gated boards):
    which trips must have each metric ignored. Empty sets when there are no rules."""
    out = {m: set() for m in WHEEL_METRIC_FIELDS}
    rules = get_wheel_rules(db) if rules is None else rules
    if not rules:
        return out
    from models import Trip, Wheel
    rows = (db.query(Trip.trip_uuid, Trip.app_version, Wheel.brand, Wheel.model)
            .join(Wheel, Wheel.wheel_id == Trip.wheel_id).all())
    for uuid, ver, brand, model in rows:
        for r in rules:
            if r.get("brand") == brand and r.get("model") == model:
                cut = r.get("max_app_version")
                if not cut or _app_ver_le(ver, cut):
                    for met in r.get("metrics", []):
                        out[met].add(uuid)
    return out


def wheel_metric_stats(db: Session, brand: str, model: str, cutoff: str | None = None) -> dict:
    """{metric: {field, min, max, avg}} over one model's trips (each metric's primary field, one
    query). When `cutoff` is given, only trips with app_version <= cutoff are counted — i.e. exactly
    the trips a rule with that cutoff would invalidate. Powers the inline range table on Wheels."""
    from sqlalchemy import func
    from models import Trip, Wheel
    prim = {m: fs[0] for m, fs in WHEEL_METRIC_FIELDS.items()}
    cols = []
    for f in prim.values():
        c = getattr(Trip, f)
        cols += [func.min(c), func.max(c), func.avg(c)]
    q = (db.query(*cols).join(Wheel, Wheel.wheel_id == Trip.wheel_id)
         .filter(Wheel.brand == brand, Wheel.model == model))
    if cutoff:                                              # app_version <= cutoff (semver, in Python)
        good = [v for (v,) in db.query(Trip.app_version)
                .join(Wheel, Wheel.wheel_id == Trip.wheel_id)
                .filter(Wheel.brand == brand, Wheel.model == model, Trip.app_version.isnot(None))
                .distinct() if _app_ver_le(v, cutoff)]
        q = q.filter(Trip.app_version.in_(good))
    r = q.first()
    out = {}
    for i, (m, f) in enumerate(prim.items()):
        out[m] = ({"field": f, "min": r[3 * i], "max": r[3 * i + 1], "avg": r[3 * i + 2]}
                  if r else {"field": f})
    return out


def wheel_catalog(db: Session) -> list[dict]:
    """Every reported brand/model with app-versions + counts and any active rule —
    the admin 'what wheels reported' view."""
    from sqlalchemy import func
    from models import Trip, Wheel
    rules = {(r["brand"], r["model"]): r for r in get_wheel_rules(db)}
    cat: dict = {}
    for brand, model, ver, trips in (
            db.query(Wheel.brand, Wheel.model, Trip.app_version, func.count(Trip.trip_uuid))
            .join(Trip, Trip.wheel_id == Wheel.wheel_id)
            .group_by(Wheel.brand, Wheel.model, Trip.app_version).all()):
        key = (brand or "?", model or "?")
        e = cat.setdefault(key, {"brand": key[0], "model": key[1], "trips": 0,
                                 "riders": 0, "versions": {}, "rule": rules.get(key)})
        e["trips"] += trips
        e["versions"][ver or "?"] = e["versions"].get(ver or "?", 0) + trips
    for brand, model, riders in (
            db.query(Wheel.brand, Wheel.model, func.count(func.distinct(Trip.rider_store_id)))
            .join(Trip, Trip.wheel_id == Wheel.wheel_id)
            .group_by(Wheel.brand, Wheel.model).all()):
        e = cat.get((brand or "?", model or "?"))
        if e:
            e["riders"] = riders
    return sorted(cat.values(), key=lambda x: (-x["trips"], x["brand"], x["model"]))

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
    ("mileage", "Most Distance", "Most distance ever ridden"),
    ("daily", "Best Day", "Most distance in a single day"),
    ("week", "Best Week", "Most distance in one week"),
    ("month", "Best Month", "Most distance in one calendar month"),
    ("speed", "Top Speed", "Highest speed reached on any ride"),
    ("accel", "0→40 km/h", "Fastest launch from a stop to 40 km/h · lower is better"),
    ("gforce", "Top G", "Strongest g-force spike"),
    ("power", "Power 2s", "Highest power held for 2 seconds"),
    ("current", "Current 2s", "Highest current held for 2 seconds"),
    ("voltage", "Peak Voltage", "Highest battery voltage observed"),
    ("streak", "Streak Master", "Longest run of consecutive days ridden"),
    ("ascent", "Total Climb", "Total elevation climbed (Everest = 8849 m)"),
    ("range", "Best Range", "Longest estimated full-charge range"),
    ("efficiency", "Efficiency", "Lowest energy use per km · most efficient"),
    ("hours", "Steel Legs", "Most hours on the wheel"),
    ("cruise", "Calm Ride", "Longest calm ride held under 10 km/h"),
    ("globe", "Globe Trotter", "Most countries ridden in"),
    ("altking", "Altitude King", "Biggest altitude swing in one ride"),
    ("frequent", "Most Rides", "Most rides logged"),
    ("marathon", "Longest Ride", "Longest single ride by time"),
    ("pace", "Avg Speed", "Highest average speed on a single ride"),
    ("battery", "Battery Drain", "Biggest battery drain in one ride"),
    ("night", "Night Rider", "Most rides started at night (22:00–05:00 UTC)"),
    ("weekend", "Weekend Warrior", "Most distance ridden on weekends"),
    ("early", "Early Bird", "Most rides started in the morning (05:00–09:00 UTC)"),
    ("peak", "Biggest Climb", "Biggest elevation gain in a single ride"),
    ("energy", "Most Energy", "Most total energy used across all rides"),
    ("explorer", "Explorer", "Most distinct map areas ridden"),
    ("bigday", "Busiest Day", "Most rides in a single day"),
    ("commuter", "Commuter", "Most distance ridden on weekdays"),
    ("freespin", "Freespin", "Biggest freespin / spin-up spike (wheel lifted or a crash)"),
    ("cutouts", "Cutout Survivor", "Most detected cutout / overlean falls (ride at speed → freespin + impact)"),
    ("sag", "Voltage Sag", "Biggest voltage drop under load — the hardest battery pull"),
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
    ("dist", "Most Distance", "Most distance ridden"),
    ("speed", "Top Speed", "Fastest ride in the group"),
    ("accel", "0→40 km/h", "Fastest 0→40 km/h · lower is better"),
    ("gforce", "Top G", "Strongest g-force spike"),
    ("power", "Power 2s", "Highest power held 2s"),
    ("current", "Current 2s", "Highest current held 2s"),
    ("voltage", "Peak Voltage", "Highest battery voltage"),
    ("riders", "Riders", "Active riders"),
    ("trips", "Rides", "Rides logged"),
    ("ascent", "Total Climb", "Total elevation climbed"),
    ("range", "Best Range", "Longest est. range"),
    ("eff", "Efficiency", "Lowest Wh/km · best"),
    ("climb", "Biggest Climb", "Biggest single climb"),
    ("alt", "Max Altitude", "Highest altitude reached"),
    ("temp", "High Temp", "Hottest the board ran"),
    ("cutout", "Cutouts", "Cutout / overlean falls per 1000 km ridden"),
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
    ("max_altitude", "Highest Altitude", "Highest altitude reached in a ride"),
    ("min_altitude", "Lowest Altitude", "Lowest altitude reached in a ride"),
    ("biggest_climb", "Biggest Climb", "Most elevation gained in one ride"),
    ("biggest_downhill", "Biggest Downhill", "Most elevation lost in one ride"),
    ("most_rides", "Most Rides", "Most real rides (>=10 min moving & >=1 km)"),
]

# --- gated boards: an anti-gaming qualifying ride length + distance ---
# Each gated metric expands into one board per tier, sharing the display name; the
# tier's minimums live in the description (shown km/mi to match the UI). Computed live
# from the Trip column (max or min) over qualifying trips. Absolute physical extremes
# (altitude, top speed, g) need no gate — a trivial ride can't move their max/min.
GATE_TIERS = [("b", 300, 1.5), ("s", 600, 3.0), ("m", 1800, 15.0), ("l", 3600, 40.0)]   # (suffix, min_seconds, min_km)

# (base, name, desc, trip_col, direction, unit, conv, icon)  -- gated (spikeable/fakeable)
_GATED_SPEC = [
    ("power",    "Power 2s",       "Highest power held for 2 seconds",        "max_sustained_w",  "max", " W",       "",     "power"),
    ("current",  "Current 2s",        "Highest current held for 2 seconds",      "max_sustained_a",  "max", " A",       "",     "current"),
    ("sag",      "Voltage Sag",         "Biggest voltage drop under load",         "max_voltage_sag",  "max", " V",       "",     "sag"),
    ("rocket",   "Rocket",           "Hardest sustained acceleration (2s+)",    "sustained_accel",  "max", " km/h/s",  "",     "rocket"),
    ("battery",  "Battery Drain",  "Biggest battery drain in one ride",       "battery_used_pct", "max", " %",       "",     "battery"),
    ("temphigh", "High Temp",          "Hottest the board ever ran",              "max_temp",         "max", "°",   "temp", "rocket"),
    ("templow",  "Low Temp",        "Coldest ride",                            "min_temp",         "min", "°",   "temp", "streak"),
    ("pwm",      "Peak PWM",          "Closest to maxing the motor (PWM)",       "max_pwm",          "max", " %",       "",     "speed"),
    ("battlow",  "Low Battery", "Lowest battery % reached",                "min_battery_pct",  "min", " %",       "",     "range"),
    # --- newer hidden metrics: longer sustained windows (spikes were too easy to game) ---
    ("g4",       "G-Hold 4s",        "Highest g-force held for 4 seconds",      "g_sust_4s",        "max", "",         "",     "gforce"),
    ("g6",       "G-Hold 6s",        "Highest g-force held for 6 seconds",      "g_sust_6s",        "max", "",         "",     "gforce"),
    ("pwm3",     "PWM 3s",     "Highest PWM held for 3 seconds",          "pwm_sust_3s",      "max", " %",       "",     "speed"),
    ("spd5",     "Speed 5s",      "Highest speed held for 5 seconds",        "speed_sust_5s",    "max", " km/h",    "spd",  "speed"),
    ("spd10",    "Speed 10s",     "Highest speed held for 10 seconds",       "speed_sust_10s",   "max", " km/h",    "spd",  "speed"),
    ("pw6",      "Power 6s",    "Highest power held for 6 seconds",        "power_sust_6s",    "max", " W",       "",     "power"),
    ("cur6",     "Current 6s",     "Highest current held for 6 seconds",      "current_sust_6s",  "max", " A",       "",     "current"),
    # --- g-force while genuinely fast ---
    ("gf20",     "G over 20",  "Strongest g-force sustained above 20 km/h", "g_fast_20",      "max", "",         "",     "gforce"),
    ("gf30",     "G over 30",  "Strongest g-force sustained above 30 km/h", "g_fast_30",      "max", "",         "",     "gforce"),
    ("gf40",     "G over 40",  "Strongest g-force sustained above 40 km/h", "g_fast_40",      "max", "",         "",     "gforce"),
    # --- experimental shake (the fakeable IMU brake/lateral boards were dropped) ---
    ("shake",    "Wobble",   "Biggest speed-wobble / shake index (experimental)", "shake_index", "max", "",   "",     "gforce"),
    # --- speed turned into a longitudinal g-force: launch (green) and braking (red) ---
    ("accg",     "Accel G",     "Hardest acceleration as a g-force (from speed)",    "accel_g", "max", " g",     "",     "accel"),
    ("brkg",     "Braking G",   "Hardest braking as a g-force (from speed)",         "brake_g", "max", " g",     "",     "brake"),
    # --- cheat-proof sprints (lower better), roll-on accel + braking-from-speed g, emergency stops ---
    ("sprint60", "0→60",             "Fastest launch from a stop to 60 km/h (≈37 mph)",   "t_0_60_s",  "min", " s",    "",     "rocket"),
    ("sprint100","0→100",            "Fastest launch from a stop to 100 km/h (≈62 mph)",  "t_0_100_s", "min", " s",    "",     "rocket"),
    ("acc30",    "Accel 30+",       "Hardest acceleration g while already above 30 km/h","accel_g_30","max", " g",     "",     "accel"),
    ("acc50",    "Accel 50+",       "Hardest acceleration g while already above 50 km/h","accel_g_50","max", " g",     "",     "accel"),
    ("brk30",    "Brake 30+",    "Hardest braking g coming down from 30 km/h (≈19 mph)","brake_g_30","max"," g",    "",     "brake"),
    ("brk50",    "Brake 50+",    "Hardest braking g coming down from 50 km/h (≈31 mph)","brake_g_50","max"," g",    "",     "brake"),
    ("stop30",   "Stop 30",     "Fastest stop from 30 km/h (≈19 mph) to a standstill","stop_30_s","min", " s",    "",     "brake"),
    ("stop50",   "Stop 50",     "Fastest stop from 50 km/h (≈31 mph) to a standstill","stop_50_s","min", " s",    "",     "brake"),
]
# (base, name, desc, trip_col, direction, unit, conv, icon)  -- ungated absolute extremes
_UNGATED_NEW = [
    ("althigh",  "Max Altitude",         "Highest altitude ever reached", "max_altitude_m", "max", " m", "alt", "ascent"),
    ("altlow",   "Min Altitude",  "Lowest altitude ever reached",  "min_altitude_m", "min", " m", "alt", "altking"),
]
_GATED_BASES = {s[0] for s in _GATED_SPEC}        # existing ungated boards these replace


def gated_boards() -> list[dict]:
    """Every gated board variant: one per (metric, tier)."""
    out = []
    for base, name, desc, col, d, unit, conv, icon in _GATED_SPEC:
        for suf, ms, mk in GATE_TIERS:
            out.append({"k": f"{base}_{suf}", "base": base, "name": name, "desc": desc,
                        "col": col, "dir": d, "u": unit, "conv": conv, "ic": icon,
                        "min_s": ms, "min_km": mk})
    return out


def ungated_new_boards() -> list[dict]:
    return [{"k": base, "base": base, "name": name, "desc": desc, "col": col, "dir": d,
             "u": unit, "conv": conv, "ic": icon, "min_s": 0, "min_km": 0}
            for base, name, desc, col, d, unit, conv, icon in _UNGATED_NEW]


def new_board_keys() -> list[str]:
    return [b["k"] for b in gated_boards()] + [b["k"] for b in ungated_new_boards()]


# Rebuild the board catalogue: drop the now-gated ungated originals, append every
# gated tier + the ungated newcomers. Each ships hidden (see DEFAULT_OFF_BOARDS).
def _gate_note(ms, mk):
    return f" · ≥{ms // 60} min & ≥{mk:g} km" if ms else ""


METRIC_BOARDS = [b for b in METRIC_BOARDS if b[0] not in _GATED_BASES]
METRIC_BOARDS += [(b["k"], b["name"], b["desc"] + _gate_note(b["min_s"], b["min_km"]))
                  for b in gated_boards()]
METRIC_BOARDS += [(b["k"], b["name"], b["desc"]) for b in ungated_new_boards()]
DEFAULT_OFF_BOARDS = set(new_board_keys()) | {"cutouts"}   # ship every new board hidden until enabled

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
    ("impossible_ascent", "Impossible ascent",
     "Flag a ride that gains more than ~300 m of climb per km ridden — GPS/altitude "
     "noise or fabricated elevation, not a real climb.", []),
    ("teleport", "GPS teleporting",
     "Flag many GPS point-to-point jumps that imply impossible travel speed.",
     ["teleport_kmh", "teleport_max_jumps"]),
    ("distance_mismatch", "Odometer vs GPS mismatch",
     "Flag when odometer distance and GPS-measured distance disagree beyond tolerance.",
     ["dist_tolerance", "mismatch_min_km"]),
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
    ("mismatch_min_km", "Min distance before mismatch is judged (km)", "thr_mismatch_min", "MISMATCH_MIN_KM", "float", 0, 1000),
    ("unverified_dist_km", "Unverified distance limit (km)", "thr_unverified_km", "UNVERIFIED_DIST_KM", "float", 0, 10000),
]


def pipeline_disabled(db: Session) -> set:
    """Set of plausibility-rule keys the admin has switched OFF."""
    return set(_json_list(db, "pipeline_disabled"))


def set_pipeline_enabled(db: Session, enabled_keys) -> None:
    enabled = set(enabled_keys)
    disabled = [k for k, *_ in PIPELINE_RULES if k not in enabled]
    set_meta(db, "pipeline_disabled", json.dumps(disabled))


# Telemetry calibration used by ingest/summary (physics limits). Same tuple shape.
# Keys match ingest.summary.CALIBRATION_DEFAULTS.
CALIBRATION = [
    ("max_accel", "Max believable acceleration (km/h per s)", "cal_max_accel", "MAX_ACCEL_KMH_S", "float", 1, 100),
    ("sustain_secs", "Sustained-metric window (s)", "cal_sustain_secs", "SUSTAIN_SECS", "float", 0.5, 30),
    ("freespin_margin", "Freespin margin over realistic (km/h)", "cal_freespin_margin", "FREESPIN_MARGIN_KMH", "float", 0, 200),
    ("accel_target_kmh", "Launch metric target (km/h)", "cal_accel_target", "ACCEL_TARGET_KMH", "float", 5, 200),
    ("accel_min_s", "Fastest believable launch (s)", "cal_accel_min", "ACCEL_MIN_S", "float", 0.1, 30),
    ("accel_max_s", "Longest counted launch (s)", "cal_accel_max", "ACCEL_MAX_S", "float", 1, 120),
    ("sustain_accel_lo_s", "Sustained-accel min window (s)", "cal_saccel_lo", "SUSTAIN_ACCEL_LO_S", "float", 0.5, 30),
    ("sustain_accel_hi_s", "Sustained-accel max window (s)", "cal_saccel_hi", "SUSTAIN_ACCEL_HI_S", "float", 1, 60),
    ("sag_window_s", "Voltage-sag look-back (s)", "cal_sag_window", "SAG_WINDOW_S", "float", 1, 60),
    ("ascent_hysteresis_m", "Ascent noise filter (m)", "cal_ascent_hyst", "ASCENT_HYSTERESIS_M", "float", 0, 100),
    ("odo_max_step_km", "Max odometer jump per reading (km)", "cal_odo_step", "ODO_MAX_STEP_KM", "float", 0.1, 1000),
    ("range_min_battery_pct", "Min battery drop to estimate range (%)", "cal_range_minbatt", "RANGE_MIN_BATTERY_PCT", "float", 1, 100),
]


def get_calibration(db: Session) -> dict:
    out = {}
    for key, _lbl, mkey, cattr, kind, lo, hi in CALIBRATION:
        default = getattr(config, cattr)
        raw = get_meta(db, mkey, None)
        val = raw if raw is not None else default
        out[key] = _clamp_int(val, default, lo, hi) if kind == "int" else _clamp_float(val, default, lo, hi)
    return out


def set_calibration(db: Session, values: dict) -> None:
    for key, _lbl, mkey, cattr, kind, lo, hi in CALIBRATION:
        v = values.get(key)
        if v in (None, ""):
            continue
        default = getattr(config, cattr)
        v = _clamp_int(v, default, lo, hi) if kind == "int" else _clamp_float(v, default, lo, hi)
        set_meta(db, mkey, str(v))


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


# --- rate limits (per hour; 0 disables). Same tuple shape as thresholds. ---
RATE_LIMITS = [
    ("rider_create_per_ip", "New riders / hour / IP", "rl_rider_ip", "RATE_RIDER_CREATE_PER_IP", "int", 0, 100000),
    ("trip_per_rider", "Trip uploads / hour / rider", "rl_trip_rider", "RATE_TRIP_PER_RIDER", "int", 0, 100000),
    ("trip_per_ip", "Trip uploads / hour / IP", "rl_trip_ip", "RATE_TRIP_PER_IP", "int", 0, 100000),
]


def get_rate_limits(db: Session) -> dict:
    out = {}
    for key, _lbl, mkey, cattr, _kind, lo, hi in RATE_LIMITS:
        default = getattr(config, cattr)
        raw = get_meta(db, mkey, None)
        out[key] = _clamp_int(raw if raw is not None else default, default, lo, hi)
    return out


def set_rate_limits(db: Session, values: dict) -> None:
    for key, _lbl, mkey, cattr, _kind, lo, hi in RATE_LIMITS:
        v = values.get(key)
        if v in (None, ""):
            continue
        default = getattr(config, cattr)
        set_meta(db, mkey, str(_clamp_int(v, default, lo, hi)))


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


# --- heatmap (per-dataset; cell_size/route_mode are DESTRUCTIVE -> need a Rebuild) ---
HEATMAP_COARSE_ZOOMS = [2.0, 0.5]          # always-present coarse tiers for zoomed-out views


def get_heatmap(db: Session) -> dict:
    mode = (get_meta(db, "hm_route_mode", "route") or "route").strip().lower()
    return {
        "cell_size": _clamp_float(get_meta(db, "hm_cell_size", 0.025), 0.025, 0.005, 5.0),
        "route_mode": mode if mode in ("route", "start") else "route",
        "floor": _clamp_int(get_meta(db, "hm_floor", 2), 2, 1, 1000),
        "radius": _clamp_int(get_meta(db, "hm_radius", 60), 60, 4, 400),
        "zoom_growth": _clamp_float(get_meta(db, "hm_zoom_growth", 1.0), 1.0, 0.2, 2.0),
        "intensity": _clamp_float(get_meta(db, "hm_intensity", 1.0), 1.0, 0.1, 10.0),
        "glow_floor": _clamp_float(get_meta(db, "hm_glow_floor", 0.45), 0.45, 0.0, 1.0),
        "opacity": _clamp_float(get_meta(db, "hm_opacity", 0.62), 0.62, 0.0, 1.0),
    }


def set_heatmap(db: Session, cell_size, route_mode, floor, radius, intensity, opacity,
                zoom_growth=1.0, glow_floor=0.45) -> None:
    set_meta(db, "hm_cell_size", str(_clamp_float(cell_size, 0.025, 0.005, 5.0)))
    set_meta(db, "hm_route_mode", route_mode if route_mode in ("route", "start") else "route")
    set_meta(db, "hm_floor", str(_clamp_int(floor, 2, 1, 1000)))
    set_meta(db, "hm_radius", str(_clamp_int(radius, 60, 4, 400)))
    set_meta(db, "hm_zoom_growth", str(_clamp_float(zoom_growth, 1.0, 0.2, 2.0)))
    set_meta(db, "hm_intensity", str(_clamp_float(intensity, 1.0, 0.1, 10.0)))
    set_meta(db, "hm_glow_floor", str(_clamp_float(glow_floor, 0.45, 0.0, 1.0)))
    set_meta(db, "hm_opacity", str(_clamp_float(opacity, 0.62, 0.0, 1.0)))


def heatmap_zooms(db: Session) -> list[float]:
    """Grid tiers to bake: coarse tiers for zoomed-out + the admin's finest cell size."""
    return HEATMAP_COARSE_ZOOMS + [get_heatmap(db)["cell_size"]]


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


def sandbox_enabled() -> bool:
    """Global (not per-dataset) flag: when on, reserved sandbox-* store_ids return
    deterministic test responses on /riders and /trips. Off by default."""
    return bool(_site_state().get("sandbox", False))


def set_sandbox(enabled) -> None:
    s = _site_state()
    s["sandbox"] = bool(enabled)
    _save_site_state(s)


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


_GROUP_KINDS = ("countries", "wheels", "brands")


def get_hidden(db: Session) -> dict:
    """Keys of metrics to hide publicly. There is no standalone 'section' flag any more:
    a dock section is hidden only when every metric under it is hidden. Group tabs are
    independent per section (Countries / Wheels / Brands each keep their own list)."""
    legacy = _json_list(db, "hidden_groups")        # pre-split shared list -> migrate per kind

    def grp(kind):
        raw = get_meta(db, "hidden_groups_" + kind, None)
        if raw is None:
            return list(legacy)
        try:
            return list(json.loads(raw))
        except Exception:
            return []

    # new boards ship hidden until an admin explicitly enables them at least once
    stored = set(_json_list(db, "hidden_boards"))
    shown_ever = set(_json_list(db, "shown_boards"))
    boards = sorted(stored | {k for k in DEFAULT_OFF_BOARDS if k not in shown_ever})
    return {"boards": boards,
            "app": _json_list(db, "hidden_app"),
            "records": _json_list(db, "hidden_records"),
            "groups": {k: grp(k) for k in _GROUP_KINDS}}


def mark_boards_shown(db: Session, keys) -> None:
    """Remember boards the admin has explicitly enabled, so default-off newcomers
    stay off only until first ticked."""
    cur = set(_json_list(db, "shown_boards")) | set(keys)
    set_meta(db, "shown_boards", json.dumps(sorted(cur)))


def set_hidden(db: Session, boards=(), app=(), records=(), groups=None) -> None:
    groups = groups or {}
    set_meta(db, "hidden_boards", json.dumps(list(boards)))
    set_meta(db, "hidden_app", json.dumps(list(app)))
    set_meta(db, "hidden_records", json.dumps(list(records)))
    for kind in _GROUP_KINDS:
        set_meta(db, "hidden_groups_" + kind, json.dumps(list(groups.get(kind, []))))


# --- admin-defined display order for metrics (drag-to-reorder) ---------------
# Per public-panel ordering of metric keys. Unlisted/new keys keep their natural
# order, appended after the explicitly-ordered ones. boards->Riders panel,
# g*->the three group panels, records->Records, app->App & OS.
_ORDER_SECTIONS = ("boards", "gcountries", "gwheels", "gbrands", "records", "app")


def get_metric_order(db: Session) -> dict:
    try:
        d = json.loads(get_meta(db, "metric_order", "{}") or "{}")
    except Exception:
        d = {}
    return {s: (d[s] if isinstance(d.get(s), list) else []) for s in _ORDER_SECTIONS}


def set_metric_order(db: Session, orders: dict) -> None:
    clean = {s: [str(x) for x in (orders or {}).get(s, []) if x]
             for s in _ORDER_SECTIONS if isinstance((orders or {}).get(s), list)}
    set_meta(db, "metric_order", json.dumps(clean))


def order_items(items, order):
    """Reorder a list of (key, ...) tuples by `order` (a list of keys). Stable: keys not in
    `order` (e.g. newly added metrics) keep their original relative order, after the ordered ones."""
    if not order:
        return list(items)
    pos = {k: i for i, k in enumerate(order)}
    return sorted(items, key=lambda it: pos.get(it[0], len(order) + 1))


# --- EUC Planet Score formula (the champions ranking) ------------------------
# score = distance_km ** dist_exp  ×  (1 + top_kmh / speed_div)  ×  (1 + hours / hours_div)
# Lower divisor = bigger boost (spicier); dist_exp > 1 rewards big distance non-linearly.
# Defaults reproduce the original formula. Per-dataset (app_meta).

def get_score_config(db: Session) -> dict:
    return {
        "dist_exp": _clamp_float(get_meta(db, "score_dist_exp", 1.0), 1.0, 0.1, 3.0),
        "speed_on": get_meta(db, "score_speed_on", "1") not in _FALSEY,
        "speed_div": _clamp_float(get_meta(db, "score_speed_div", 100.0), 100.0, 1.0, 100000.0),
        "hours_on": get_meta(db, "score_hours_on", "1") not in _FALSEY,
        "hours_div": _clamp_float(get_meta(db, "score_hours_div", 10.0), 10.0, 0.1, 100000.0),
    }


def set_score_config(db: Session, dist_exp, speed_on, speed_div, hours_on, hours_div) -> None:
    set_meta(db, "score_dist_exp", str(_clamp_float(dist_exp, 1.0, 0.1, 3.0)))
    set_meta(db, "score_speed_on", "1" if speed_on else "0")
    set_meta(db, "score_speed_div", str(_clamp_float(speed_div, 100.0, 1.0, 100000.0)))
    set_meta(db, "score_hours_on", "1" if hours_on else "0")
    set_meta(db, "score_hours_div", str(_clamp_float(hours_div, 10.0, 0.1, 100000.0)))


def sections_fully_hidden(db: Session) -> dict:
    """Per dock section: True when every metric under it is hidden (so the section
    itself should disappear from the public site, or dim for an admin previewing)."""
    h = get_hidden(db)
    allk = lambda items: {k for k, *_ in items}
    g = h["groups"]
    return {
        "riders": allk(METRIC_BOARDS).issubset(set(h["boards"])),
        "countries": allk(METRIC_GROUPS).issubset(set(g["countries"])),
        "wheels": allk(METRIC_GROUPS).issubset(set(g["wheels"])),
        "brands": allk(METRIC_GROUPS).issubset(set(g["brands"])),
        "records": allk(METRIC_RECORDS).issubset(set(h["records"])),
        "tech": allk(METRIC_APP).issubset(set(h["app"])),
    }
