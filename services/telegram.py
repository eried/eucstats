"""Telegram integration — announce new riders / first rides and a daily summary.

Config (incl. the bot token) lives in ``data/telegram.json`` (gitignored), NOT in
the dataset DB, so exporting a dataset can never leak the token. Every send is
best-effort: if the integration is disabled, unconfigured, or Telegram is
unreachable, nothing raises into the caller (a rider's registration/ingest is
never blocked or broken by Telegram).

Posts go to a group/supergroup ``chat_id``; an optional ``thread_id`` targets a
forum topic (message_thread_id). New-rider / first-ride posts attach the rider's
avatar via sendPhoto when present, falling back to a plain text sendMessage.
"""
from __future__ import annotations

import html
import json
import logging
from datetime import datetime, timedelta

import httpx

import config
from database import SessionLocal

logger = logging.getLogger("eucstats.telegram")

_CONFIG_FILE = config.DATA_DIR / "telegram.json"
_API = "https://api.telegram.org/bot{token}/{method}"
_TIMEOUT = 10.0

_DEFAULTS = {
    "enabled": False,          # master switch — off = nothing posts
    "token": "",
    "chat_id": "",
    "thread_id": "",           # forum topic id ("" = no topic)
    "link_url": "https://eucstats.ried.no",
    "new_rider": True,
    "first_ride": True,
    "summary_enabled": True,
    "summary_time": "08:00",   # HH:MM in summary_tz
    "summary_tz": "Europe/Oslo",
    "last_summary_date": "",   # ISO date of the last local day already recapped
}


# --- config IO (data/telegram.json) ---------------------------------------

def get_config() -> dict:
    cfg = dict(_DEFAULTS)
    try:
        if _CONFIG_FILE.exists():
            cfg.update(json.loads(_CONFIG_FILE.read_text(encoding="utf-8")))
    except Exception:
        logger.exception("telegram: failed to read config")
    return cfg


def save_config(cfg: dict) -> None:
    merged = dict(_DEFAULTS)
    merged.update(cfg)
    _CONFIG_FILE.write_text(json.dumps(merged, indent=2), encoding="utf-8")


def update_config(**fields) -> dict:
    cfg = get_config()
    cfg.update(fields)
    save_config(cfg)
    return cfg


def is_configured(cfg: dict | None = None) -> bool:
    cfg = cfg or get_config()
    return bool(cfg.get("token") and cfg.get("chat_id"))


# --- low-level send (never raises) ----------------------------------------

def _payload(cfg: dict, **extra) -> dict:
    d = {"chat_id": str(cfg["chat_id"]), "parse_mode": "HTML"}
    thread = str(cfg.get("thread_id") or "").strip()
    if thread:
        d["message_thread_id"] = thread
    d.update(extra)
    return d


def _post(url: str, data: dict, files: dict | None = None) -> tuple[bool, str]:
    try:
        with httpx.Client(timeout=_TIMEOUT) as cli:
            r = cli.post(url, data=data, files=files)
        try:
            body = r.json()
        except Exception:
            body = {}
        if r.status_code == 200 and body.get("ok"):
            return True, "sent"
        detail = body.get("description") or f"HTTP {r.status_code}"
        logger.warning("telegram send failed: %s", detail)
        return False, detail
    except Exception as e:                       # network/timeout/etc. — swallow
        logger.warning("telegram send error: %s", e)
        return False, str(e)


def send_message(text: str, cfg: dict | None = None) -> tuple[bool, str]:
    """sendMessage. Returns (ok, detail)."""
    cfg = cfg or get_config()
    if not is_configured(cfg):
        return False, "not configured (needs token + chat_id)"
    url = _API.format(token=cfg["token"], method="sendMessage")
    return _post(url, _payload(cfg, text=text, disable_web_page_preview="true"))


def send_photo(photo_bytes: bytes, caption: str, cfg: dict | None = None) -> tuple[bool, str]:
    """sendPhoto with raw image bytes + caption."""
    cfg = cfg or get_config()
    if not is_configured(cfg):
        return False, "not configured (needs token + chat_id)"
    url = _API.format(token=cfg["token"], method="sendPhoto")
    return _post(url, _payload(cfg, caption=caption),
                 files={"photo": ("avatar.png", photo_bytes, "image/png")})


# --- text helpers ----------------------------------------------------------

def _esc(s) -> str:
    return html.escape(str(s or ""))


def _flag_emoji(flag) -> str:
    """A 2-letter country code -> regional-indicator emoji; anything else passes through
    (the field may already hold an emoji or be empty)."""
    f = str(flag or "").strip()
    if len(f) == 2 and f.isalpha():
        return "".join(chr(0x1F1E6 + ord(c) - ord("A")) for c in f.upper())
    return f


# --- event notifications (called via FastAPI BackgroundTasks) --------------

def notify_new_rider(store_id: str) -> None:
    cfg = get_config()
    if not (cfg.get("enabled") and cfg.get("new_rider") and is_configured(cfg)):
        return
    db = SessionLocal()
    try:
        from models import Rider
        from services import stats
        r = db.get(Rider, store_id)
        if not r or r.deleted_at is not None or not r.consent_public:
            return                                # never out a private/closed rider
        total = stats.global_summary(db).get("riders", 0)
        plural = "rider" if total == 1 else "riders"
        text = (f"🆕 New rider: <b>{_esc(r.display_name)}</b> {_flag_emoji(r.flag)} — "
                f"now <b>{total}</b> {plural} on EUC Stats!\n{cfg['link_url']}")
        if r.avatar_png and send_photo(r.avatar_png, text, cfg)[0]:
            return
        send_message(text, cfg)
    finally:
        db.close()


def notify_first_ride(store_id: str) -> None:
    cfg = get_config()
    if not (cfg.get("enabled") and cfg.get("first_ride") and is_configured(cfg)):
        return
    db = SessionLocal()
    try:
        from models import Rider, Trip
        r = db.get(Rider, store_id)
        if not r or r.deleted_at is not None or not r.consent_public:
            return
        q = db.query(Trip).filter(Trip.rider_store_id == store_id,
                                  Trip.validation_status == "validated")
        if q.count() != 1:                        # only the rider's very first counted ride
            return
        trip = q.first()
        km = round(trip.distance_km or 0, 1)
        text = (f"🛞 <b>{_esc(r.display_name)}</b> {_flag_emoji(r.flag)} just logged their "
                f"first ride — <b>{km} km</b>!\n{cfg['link_url']}")
        if r.avatar_png and send_photo(r.avatar_png, text, cfg)[0]:
            return
        send_message(text, cfg)
    finally:
        db.close()


# --- daily summary ---------------------------------------------------------

def _zone(name: str):
    from zoneinfo import ZoneInfo
    return ZoneInfo(name)


def _local_day_range_utc(tz_name: str, the_date) -> tuple[datetime, datetime]:
    """[start, end) of a local calendar day, as naive UTC datetimes (DB columns are naive UTC)."""
    tz, utc = _zone(tz_name), _zone("UTC")
    start_local = datetime(the_date.year, the_date.month, the_date.day, tzinfo=tz)
    end_local = start_local + timedelta(days=1)
    return (start_local.astimezone(utc).replace(tzinfo=None),
            end_local.astimezone(utc).replace(tzinfo=None))


def daily_summary_text(db, recap_date, cfg: dict) -> str | None:
    """Recap of one local day. Returns None for a silent day (no new riders and no rides)."""
    from sqlalchemy import func
    from models import Rider, Trip
    from services import stats
    start, end = _local_day_range_utc(cfg.get("summary_tz") or "Europe/Oslo", recap_date)

    new_riders = db.query(func.count(Rider.store_id)).filter(
        Rider.created_at >= start, Rider.created_at < end,
        Rider.deleted_at.is_(None), Rider.consent_public.is_(True)).scalar() or 0
    day_trips = db.query(Trip).filter(
        Trip.start_utc >= start, Trip.start_utc < end,
        Trip.validation_status == "validated")
    trips_n = day_trips.count()
    km = round(db.query(func.coalesce(func.sum(Trip.distance_km), 0.0)).filter(
        Trip.start_utc >= start, Trip.start_utc < end,
        Trip.validation_status == "validated").scalar() or 0, 1)
    if new_riders == 0 and trips_n == 0:
        return None

    top_line = ""
    rows = (db.query(Trip.rider_store_id, func.sum(Trip.distance_km).label("d"))
              .filter(Trip.start_utc >= start, Trip.start_utc < end,
                      Trip.validation_status == "validated")
              .group_by(Trip.rider_store_id)
              .order_by(func.sum(Trip.distance_km).desc()).all())
    for sid, d in rows:                           # first public, non-deleted rider wins
        tr = db.get(Rider, sid)
        if tr and tr.deleted_at is None and tr.consent_public:
            top_line = (f"\n🏆 Top rider: <b>{_esc(tr.display_name)}</b> "
                        f"{_flag_emoji(tr.flag)} — {round(d or 0, 1)} km")
            break

    g = stats.global_summary(db)
    tot_r, tot_t = g.get("riders", 0), g.get("trips", 0)
    tot_c = g.get("countries", 0)
    head = f"📊 <b>EUC Stats — {recap_date.strftime('%a %d %b')}</b>"
    yday = (f"\nYesterday: <b>{new_riders}</b> new {'rider' if new_riders == 1 else 'riders'} · "
            f"<b>{trips_n}</b> {'ride' if trips_n == 1 else 'rides'} · <b>{km} km</b>")
    alltime = (f"\nAll-time: {tot_r} {'rider' if tot_r == 1 else 'riders'} · "
               f"{tot_t} {'ride' if tot_t == 1 else 'rides'} · {round(g.get('total_km', 0), 1)} km · "
               f"{tot_c} {'country' if tot_c == 1 else 'countries'}")
    return head + yday + top_line + alltime + f"\n👉 {cfg['link_url']}"


def run_daily_if_due() -> None:
    """Called periodically by the app loop. Posts yesterday's recap once per day, at/after
    summary_time in summary_tz. Restart-safe (persists last_summary_date) and DST-safe."""
    cfg = get_config()
    if not (cfg.get("enabled") and cfg.get("summary_enabled") and is_configured(cfg)):
        return
    try:
        now_local = datetime.now(_zone(cfg.get("summary_tz") or "Europe/Oslo"))
    except Exception:                             # bad/missing tz database — skip quietly
        logger.warning("telegram: unknown timezone %r", cfg.get("summary_tz"))
        return
    try:
        hh, mm = (int(x) for x in str(cfg.get("summary_time") or "08:00").split(":", 1))
    except Exception:
        hh, mm = 8, 0
    if (now_local.hour, now_local.minute) < (hh, mm):
        return                                    # not time yet today
    recap_date = now_local.date() - timedelta(days=1)
    if cfg.get("last_summary_date") == recap_date.isoformat():
        return                                    # already handled today

    db = SessionLocal()
    try:
        text = daily_summary_text(db, recap_date, cfg)
    finally:
        db.close()
    if text:                                      # None => silent day; still mark done
        send_message(text, cfg)
    update_config(last_summary_date=recap_date.isoformat())
