"""Telegram integration — announce new riders / first rides and a daily summary.

Config (incl. the bot token) lives in ``data/telegram.json`` (gitignored), NOT in
the dataset DB, so exporting a dataset can never leak the token. Every send is
best-effort: if the integration is disabled, unconfigured, or Telegram is
unreachable, nothing raises into the caller (a rider's registration/ingest is
never blocked or broken by Telegram).

Posts go to a group/supergroup ``chat_id``; an optional ``thread_id`` targets a
forum topic (message_thread_id). All posts are plain text (sendMessage) — rider
avatars are intentionally not attached (the stored 64px images look bad upscaled
to Telegram's photo width).
"""
from __future__ import annotations

import html
import json
import logging
import re
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
    # "first place takeover" — announce when #1 changes, per category
    "tk_rider": True,          # visible rider leaderboards
    "tk_country": True,        # top country by distance
    "tk_wheel": True,          # top wheel by distance
    "tk_brand": True,          # top brand by distance
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


def _dist(km) -> str:
    """Distance shown both ways for the metric/imperial audience, e.g. '24.2 km (15.0 mi)'."""
    km = round(km or 0, 1)
    return f"{km} km ({round(km * 0.621371, 1)} mi)"


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
        text = (f"🛞 <b>{_esc(r.display_name)}</b> {_flag_emoji(r.flag)} just logged their "
                f"first ride — <b>{_dist(trip.distance_km)}</b>!\n{cfg['link_url']}")
        send_message(text, cfg)
    finally:
        db.close()


# --- leaderboard records (new #1) ------------------------------------------

def _board_label(board: str) -> tuple[str, str]:
    """(trophy name, plain description) for a board key, e.g. 'mileage' -> ('Mile Muncher',
    'Most distance ever ridden'). Strips a gated-tier suffix (_b/_s/_m/_l)."""
    base = re.sub(r"_(b|s|m|l)$", "", board)
    try:
        from web import i18n
        return (i18n.EN.get(f"b.{base}.n", base.replace("_", " ").title()),
                i18n.EN.get(f"b.{base}.d", ""))
    except Exception:
        return (base.replace("_", " ").title(), "")


def _group_takeover_text(kind: str, top: dict, old_label: str, cfg: dict) -> str:
    link, name = cfg["link_url"], _esc(top.get("name"))
    if kind == "country":
        return (f"🏆 New top country! {_flag_emoji(top.get('country') or top.get('name'))} "
                f"<b>{name}</b> just overtook {_flag_emoji(old_label)} <b>{_esc(old_label)}</b> "
                f"for #1 by distance.\n{link}")
    if kind == "wheel":
        bpart = f" ({_esc(top.get('brand'))})" if top.get("brand") else ""
        return (f"🏆 New top wheel! 🛞 <b>{name}</b>{bpart} just overtook "
                f"<b>{_esc(old_label)}</b> for #1 by distance.\n{link}")
    return (f"🏆 New top brand! 🛞 <b>{name}</b> just overtook "
            f"<b>{_esc(old_label)}</b> for #1 by distance.\n{link}")


def check_records() -> None:
    """Announce a 'first place takeover' — when #1 changes — per category, each behind its own
    settings toggle:
      • tk_rider:  VISIBLE individual-rider leaderboards (stats.BOARDS minus hidden)
      • tk_country / tk_wheel / tk_brand:  the top country / wheel / brand by total distance
    Only a DIFFERENT holder taking #1 fires (no self-improvement). The per-dataset snapshot lives
    in app_meta; anything first seen (just un-hidden, or just enabled) is recorded silently — no
    launch/reveal spam, no re-fire. Best-effort; called after a validated trip.
    """
    cfg = get_config()
    if not (cfg.get("enabled") and is_configured(cfg)):
        return
    if not any(cfg.get(k) for k in ("tk_rider", "tk_country", "tk_wheel", "tk_brand")):
        return
    db = SessionLocal()
    try:
        from services import stats, settings
        from models import Rider
        try:
            prev = json.loads(settings.get_meta(db, "tg_record_holders", "") or "{}")
        except Exception:
            prev = {}
        snap, rider_hits, group_hits = {}, [], []

        # individual rider leaderboards — visible boards only
        if cfg.get("tk_rider"):
            hidden = set(settings.get_hidden(db).get("boards", []))
            for board, fn in stats.BOARDS.items():
                if board in hidden:              # not visible -> don't track (re-show = silent init)
                    continue
                try:
                    entries = fn(db, 1)
                except Exception:
                    continue
                if not entries or not entries[0].get("store_id"):
                    continue
                top = entries[0]
                snap[board] = top["store_id"]
                old = prev.get(board)
                if old is not None and old != top["store_id"]:
                    rider_hits.append((board, top, old))

        # group standings — top country / wheel / brand by distance
        gfns = {"country": stats.by_country, "wheel": stats.by_wheel, "brand": stats.by_brand}
        for kind, toggle in (("country", "tk_country"), ("wheel", "tk_wheel"), ("brand", "tk_brand")):
            if not cfg.get(toggle):
                continue
            try:
                entries = gfns[kind](db, 1)
            except Exception:
                continue
            if not entries or not entries[0].get("name"):
                continue
            top = entries[0]
            key = "g:" + kind
            snap[key] = top["name"]
            old = prev.get(key)
            if old is not None and old != top["name"]:
                group_hits.append((kind, top, old))

        settings.set_meta(db, "tg_record_holders", json.dumps(snap))
        db.commit()

        # group a rider's simultaneous new #1s into ONE message — a single big ride can sweep
        # several boards, and we don't want a burst of one post per board for the same rider.
        by_rider: dict = {}
        for board, top, old_sid in rider_hits:
            slot = by_rider.setdefault(top["store_id"], {"top": top, "hits": []})
            slot["hits"].append((board, old_sid))
        for info in by_rider.values():
            top, hits = info["top"], info["hits"]
            who = f"<b>{_esc(top.get('name'))}</b> {_flag_emoji(top.get('flag'))}"
            if len(hits) == 1:
                board, old_sid = hits[0]
                name, desc = _board_label(board)
                oldr = db.get(Rider, old_sid)
                beat = f", beating <b>{_esc(oldr.display_name)}</b>" if oldr and oldr.display_name else ""
                descpart = f" ({_esc(desc)})" if desc else ""
                text = (f"🏆 New record! {who} is the new <b>{_esc(name)}</b>{descpart}{beat}."
                        f"\n{cfg['link_url']}")
            else:
                titles = ", ".join(f"<b>{_esc(_board_label(b)[0])}</b>" for b, _ in hits)
                text = (f"🏆 {who} just swept {len(hits)} #1 spots: {titles}."
                        f"\n{cfg['link_url']}")
            send_message(text, cfg)

        for kind, top, old_label in group_hits:
            send_message(_group_takeover_text(kind, top, old_label, cfg), cfg)
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
                        f"{_flag_emoji(tr.flag)} — {_dist(d)}")
            break

    g = stats.global_summary(db)
    tot_r, tot_t = g.get("riders", 0), g.get("trips", 0)
    tot_c = g.get("countries", 0)
    # full weekday + day + full month, e.g. "Sunday 12 July" (no zero-padding on the day)
    head = (f"📊 <b>EUC Stats — {recap_date.strftime('%A')} {recap_date.day} "
            f"{recap_date.strftime('%B')}</b>")
    yday = (f"\nYesterday: <b>{new_riders}</b> new {'rider' if new_riders == 1 else 'riders'} · "
            f"<b>{trips_n}</b> {'ride' if trips_n == 1 else 'rides'} · <b>{_dist(km)}</b>")
    alltime = (f"\nAll-time: {tot_r} {'rider' if tot_r == 1 else 'riders'} · "
               f"{tot_t} {'ride' if tot_t == 1 else 'rides'} · {_dist(g.get('total_km', 0))} · "
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
