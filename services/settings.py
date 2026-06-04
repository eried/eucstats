"""Tiny key/value settings stored in the active dataset's app_meta table.

Because app_meta lives *inside* the SQLite file, per-dataset flags such as
``is_test`` travel automatically when the dataset is swapped. Values are stored
as strings; helpers coerce the few typed ones we use.
"""
from __future__ import annotations

import json

from sqlalchemy.orm import Session

import config
from models import Meta

IS_TEST = "is_test"

# Canonical metric/section lists for the admin show/hide UI.
# Keep keys in sync with web/public.py BOARDS (board `k`) and the dock `data-p`.
METRIC_SECTIONS = [
    ("riders", "Riders"), ("countries", "Countries"), ("wheels", "Wheels"),
    ("brands", "Brands"), ("records", "Records"), ("tech", "App"),
]
METRIC_BOARDS = [
    ("mileage", "Mile Muncher"), ("daily", "Day Crusher"), ("week", "Week Beast"),
    ("month", "Month Monster"), ("speed", "Speedy Gonzales"), ("accel", "Drag Racer"),
    ("gforce", "G-Force Hero"), ("power", "Watt Beast"), ("current", "Amp Demon"),
    ("voltage", "Volt Lord"), ("streak", "Streak Master"), ("ascent", "Everest Climber"),
    ("range", "Long Hauler"), ("efficiency", "Eco Rider"), ("hours", "Steel Legs"),
    ("cruise", "Sunday Cruiser"), ("globe", "Globe Trotter"), ("altking", "Altitude King"),
    ("frequent", "Frequent Flyer"), ("marathon", "Marathoner"), ("pace", "Pace Maker"),
    ("battery", "Battery Vampire"), ("night", "Night Rider"), ("weekend", "Weekend Warrior"),
    ("early", "Early Bird"), ("peak", "Peak Bagger"), ("energy", "Power Plant"),
    ("explorer", "Explorer"), ("bigday", "Big Day"), ("commuter", "Commuter"),
]
# Inner sub-panels of the "App & OS" section (keys match version_stats output).
METRIC_APP = [
    ("adopters", "Bleeding Edge (newest app)"),
    ("appvers", "App versions"),
    ("osvers", "OS versions"),
]
# Group leaderboard tabs shown inside Countries / Wheels / Brands (GBOARDS keys).
METRIC_GROUPS = [
    ("dist", "Mile Muncher"), ("speed", "Speedy Gonzales"), ("accel", "Drag Racer"),
    ("gforce", "G-Force Hero"), ("power", "Watt Beast"), ("current", "Amp Demon"),
    ("voltage", "Volt Lord"), ("riders", "Riders"), ("trips", "Rides"),
    ("ascent", "Everest Climber"), ("range", "Long Hauler"), ("eff", "Eco Rider"),
]

# --- page behaviour (consumed by the public frontend as window.__CFG__) ---
MAP_STYLES = ["dark", "light", "voyager", "satellite", "terrain"]
_FALSEY = ("0", "false", "False", "")


def _clamp_int(value, default, lo, hi):
    try:
        return max(lo, min(hi, int(value)))
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


def is_test_dataset(db: Session) -> bool:
    """Whether the active dataset is test data. Defaults to True when unset so
    a dataset of unknown provenance shows the TEST DATA banner (fail safe)."""
    return get_meta(db, IS_TEST, "1") not in ("0", "false", "False", "")


def set_test(db: Session, value: bool) -> None:
    set_meta(db, IS_TEST, "1" if value else "0")


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


def _json_list(db: Session, key: str) -> list:
    try:
        v = json.loads(get_meta(db, key, "[]"))
        return v if isinstance(v, list) else []
    except Exception:
        return []


def get_hidden(db: Session) -> dict:
    """Keys of boards / dock-sections / App-panels / group-tabs to hide publicly."""
    return {"boards": _json_list(db, "hidden_boards"),
            "sections": _json_list(db, "hidden_sections"),
            "app": _json_list(db, "hidden_app"),
            "groups": _json_list(db, "hidden_groups")}


def set_hidden(db: Session, boards, sections, app=(), groups=()) -> None:
    set_meta(db, "hidden_boards", json.dumps(list(boards)))
    set_meta(db, "hidden_sections", json.dumps(list(sections)))
    set_meta(db, "hidden_app", json.dumps(list(app)))
    set_meta(db, "hidden_groups", json.dumps(list(groups)))
