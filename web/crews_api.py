"""Crews & Territory: pairing, crew management and the territory payload.

Everything here is either cheap or cached. The territory map is a pre-built gzipped file
served with an ETag, the ranking is one pass over a table with one row per held tile, and crew
writes are small and rare. Nothing in this module walks the trips table on a page view.
"""
from __future__ import annotations

from html import escape
import json
import time
from datetime import timedelta

import re
import secrets

from fastapi import APIRouter, Depends, HTTPException, Request, Response, UploadFile, File
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from database import get_db
from models import (Clan, ClanMember, PairToken, Rider, Trip, WebSession,
                    publishable_handle, utcnow)
from services import crews, pairing, ratelimit, settings, territory
from services import tiles as T

router = APIRouter(prefix="/api/v1", tags=["crews"])
# the pairing deep link lives at the root, because it has to be short enough to be a QR code
# that scans from a laptop screen across a room
pair_router = APIRouter(tags=["crews"])

# Pairing is the one unauthenticated write in the feature, so it is capped tightly. The
# confirm limit is the one that matters: it is what stops a six-character code being guessed.
# The numbers live in the admin rate-limit settings alongside the upload limits.


def _ip(request: Request) -> str:
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    return request.headers.get("x-real-ip") or (request.client.host if request.client else "?")


def _handle(db: Session, store_id: str) -> str:
    """What a roster may publish about a rider.

    Never the store_id: `pair/confirm` treats it as proof of identity, so printing one into
    a leader's panel hands them a session as that rider. The public handle is what every
    other public surface uses.
    """
    r = db.get(Rider, store_id)
    if r is None:
        return ""
    # A handle has one job: to be publishable where the store_id is not. Both minters produce
    # sixteen hex characters, so anything else came from somewhere this code does not control
    # -- an import, a migration, a stray script -- and is re-minted rather than trusted.
    #
    # The first version of this asked only whether the handle contained its own store_id,
    # which is narrower than the invariant the comment claims. A reviewer got past it twice:
    # a handle equal to a DIFFERENT rider's store_id, and a case-variant of its own. Both were
    # published on a browser route and both minted a working session. Checking the shape
    # instead of the substring is the same one line and actually says what is meant.
    if not publishable_handle(r.public_id):
        r.public_id = secrets.token_hex(8)
        db.commit()
    return r.public_id


def _by_handle(db: Session, handle: str) -> str:
    """Back the other way, for a route acting on somebody the panel named."""
    h = (handle or "").strip()
    if not h:
        return ""
    r = db.query(Rider).filter(Rider.public_id == h).first()
    return r.store_id if r is not None else ""


def _living(db: Session, clan_id: str):
    """A crew that still exists. A folded one keeps its row and carries a retirement tag in
    its name, which a public endpoint has no business handing out."""
    c = db.get(Clan, clan_id)
    return c if c is not None and c.disbanded_at is None else None


def _gate(db: Session) -> dict:
    cfg = settings.get_crews(db)
    if not cfg["enabled"]:
        raise HTTPException(404, "crews_disabled")
    return cfg


# How much of a joining rider's own back catalogue comes with them. See _stamp_recent.
JOIN_BACKFILL_DAYS = 14

# The floor on how often the whole-world rebuild runs; main._territory_if_due will not run
# one more than this often. It rides the retention loop, though, so if the admin has set that
# slower then that is the real cadence -- see _rebuild_every.
REBUILD_EVERY_S = 3600


def _me(request: Request, db: Session) -> WebSession | None:
    ws = pairing.session(db, request.cookies.get(pairing.COOKIE), scope="crew")
    return ws


def _require_session(request: Request, db: Session):
    ws = pairing.session(db, request.cookies.get(pairing.COOKIE), scope="crew")
    if ws is None:
        raise HTTPException(401, "not_paired")
    rl = settings.get_rate_limits(db)
    if not ratelimit.hit(f"cw:{ws.session_id}", rl["crew_write_per_session"]):
        raise HTTPException(429, "rate_limited:crew_write")
    return ws


def _err(e: crews.CrewError) -> HTTPException:
    return HTTPException(400, json.dumps({"code": e.code, "detail": e.detail}))


def _perr(status: int, e) -> HTTPException:
    """A pairing failure, in the envelope the client already unpacks.

    The code is what the browser translates; the sentence is what the app shows, and
    `no_rider` -- a rider whose first upload has not landed yet -- is the one that genuinely
    needs explaining rather than naming.
    """
    return HTTPException(status, json.dumps({"code": e.code, "detail": e.detail}))


# --- pairing ------------------------------------------------------------------------------

@router.post("/pair/start")
def pair_start(request: Request, db: Session = Depends(get_db)):
    """Open a pairing and return the QR the app will scan.

    The QR carries the host it was served from, not a hard-coded production URL. That is what
    lets one app build pair against a laptop on the same network for testing and against the
    real server in a rider's pocket, with no second build, no build flavour and no special
    rider id — the code on screen is the configuration. The app decides whether to trust a
    non-production host; see the app's developer-mode switch.
    """
    _gate(db)
    rl = settings.get_rate_limits(db)
    if not ratelimit.hit(f"ps:{_ip(request)}", rl["pair_start_per_ip"]):
        raise HTTPException(429, "rate_limited:pair_start")
    try:
        p = pairing.start(db, purpose="rider")
    except pairing.PairError as e:
        raise _perr(429, e)
    base = str(request.base_url).rstrip("/")
    url = f"{base}/p/{p['code']}"
    return {"token": p["token"], "code": p["code"], "expires_in": p["expires_in"],
            "url": url, "qr": pairing.qr_png_b64(url)}


@router.get("/pair/poll")
def pair_poll(token: str, response: Response, db: Session = Depends(get_db)):
    """The browser waits here. On success the session lands in an HttpOnly cookie."""
    _gate(db)
    # Read before `poll` is allowed to consume it. Refusing an admin pass afterwards closed
    # the leak but still burned the pairing, so an admin who polled the wrong screen once had
    # to start the whole thing again -- and the second attempt answered "unknown" rather than
    # saying why. `db.get` is the same lookup poll makes and changes nothing.
    peek = db.get(PairToken, token)
    if peek is not None and peek.purpose == "admin":
        raise _perr(410, pairing.PairError(
            "wrong_screen", "That code is for the admin screen, not this one."))
    try:
        res = pairing.poll(db, token)
    except pairing.PairError as e:
        raise _perr(410, e)
    # An admin pass does not belong on the rider's door at all. The branch that mints one
    # returns no `session`, so it fell straight past the strip below and handed the raw
    # store_id to an unauthenticated browser route -- and burned the pairing doing it.
    if res.get("scope") == "admin":
        raise _perr(410, pairing.PairError(
            "wrong_screen", "That code is for the admin screen, not this one."))
    if res.get("session"):
        # `store_id` goes no further than this function for the same reason `session` does
        # not: the browser can replay either one into `pair/confirm`.
        out = JSONResponse({k: v for k, v in res.items()
                            if k not in ("session", "store_id")})
        # HttpOnly so no script on the page can read it; Lax so a link from elsewhere still
        # arrives signed in but a cross-site form post does not act as the rider
        out.set_cookie(pairing.COOKIE, res["session"], httponly=True, samesite="lax",
                       max_age=int(pairing.SESSION_TTL.total_seconds()), path="/")
        return out
    return res


@router.get("/pair/describe")
def pair_describe(code: str, db: Session = Depends(get_db)):
    """What the app shows the rider before asking them to approve it."""
    _gate(db)
    try:
        return pairing.describe(db, code)
    except pairing.PairError as e:
        raise _perr(410, e)


@router.post("/pair/confirm")
def pair_confirm(payload: dict, request: Request, db: Session = Depends(get_db)):
    """The app's side of the handshake: this code belongs to this rider."""
    _gate(db)
    code = (payload.get("code") or "").strip().upper()
    store_id = (payload.get("store_id") or "").strip()
    if not code or not store_id:
        raise HTTPException(400, "code and store_id are required")
    rl = settings.get_rate_limits(db)
    if not ratelimit.hit(f"pc:{_ip(request)}", rl["pair_confirm_per_ip"]):
        raise HTTPException(429, "rate_limited:pair_confirm")
    if not ratelimit.hit(f"pr:{store_id}", rl["pair_confirm_per_rider"]):
        raise HTTPException(429, "rate_limited:pair_confirm")
    try:
        return pairing.confirm(db, code, store_id)
    except pairing.PairError as e:
        raise _perr(410, e)


@router.post("/crews/signout")
def signout(request: Request, db: Session = Depends(get_db)):
    sid = request.cookies.get(pairing.COOKIE)
    if sid:
        pairing.revoke(db, sid)
    out = JSONResponse({"ok": True})
    out.delete_cookie(pairing.COOKIE, path="/")
    return out


# --- who am I -----------------------------------------------------------------------------

def _member_counts(db: Session) -> dict:
    """Active members per crew, in one grouped query.

    _crew_brief used to COUNT per crew, so listing crews cost one query each: the client asks
    for sixty on every panel open, which measured 69 queries and 20 ms, and the endpoint would
    take two hundred.
    """
    import sqlalchemy as sa
    return dict(db.query(ClanMember.clan_id, sa.func.count(ClanMember.store_id))
                .filter(ClanMember.status == "active", ClanMember.left_at.is_(None))
                .group_by(ClanMember.clan_id).all())


def _crew_brief(db: Session, clan: Clan, counts: dict | None = None) -> dict:
    if counts is not None:
        n = counts.get(clan.clan_id, 0)
    else:
        n = (db.query(ClanMember)
             .filter(ClanMember.clan_id == clan.clan_id, ClanMember.status == "active",
                     ClanMember.left_at.is_(None)).count())
    return {"slug": clan.slug, "name": clan.name, "description": clan.description,
            "colour": clan.colour, "pattern": clan.pattern, "members": n,
            "join_policy": clan.join_policy,
            # what it holds, so picking a crew is not a blind name-pick that costs a cooldown
            "km2": clan.terr_best_km2 or 0.0, "tiles": clan.terr_tiles or 0,
            "emblem": f"/api/v1/crews/{clan.slug}/emblem"}


@router.get("/crews/drawn")
def territory_drawn(db: Session = Depends(get_db)):
    """When the map was last drawn, and how often it is.

    Everything a rider sees is baked at rebuild time, so the gap between finishing a ride and
    seeing it is up to a full interval. It is not a bug to hide; it is a number to print.
    """
    cfg = _gate(db)
    at = territory.cached_mtime(cfg["zoom"])
    # one frame, server side: st_mtime is a true epoch second and utcnow() is a naive UTC
    # datetime whose .timestamp() is read as local time, which put the last redraw in the
    # future by the box's own UTC offset
    return {"drawn_s_ago": max(0, int(time.time() - at)) if at else None,
            "every": _rebuild_every(db)}


def _rebuild_every(db) -> int:
    """How long between redraws, honestly.

    The rebuild runs at most hourly AND only when the retention loop comes round, so with
    retention set to daily the true gap is a day. Printing the constant promised forty
    minutes for ever.
    """
    try:
        from services.settings import get_retention
        return max(REBUILD_EVERY_S, int(get_retention(db)["interval_s"]))
    except Exception:
        return REBUILD_EVERY_S


@router.get("/crews/me")
def crews_me(request: Request, response: Response, db: Session = Depends(get_db)):
    """The state the crew panel renders from: session, membership, and what it may do.

    Never cached, anywhere. This answer depends on a cookie and carries the rider's handle,
    their crew's invite code, the roster, who is waiting to be let in and who has been turned
    away. `/territory` beside it sets `public, max-age=60` because it is the same for
    everybody; this one is the same for nobody, and a shared cache in front of it would hand
    one rider another crew's panel.
    """
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Vary"] = "Cookie"
    cfg = _gate(db)
    ws = _me(request, db)
    if ws is None:
        return {"paired": False, "creation_open": cfg["creation_open"],
                "test_notice": cfg["test_notice"]}
    rider = db.get(Rider, ws.store_id)
    m = crews.membership(db, ws.store_id)
    # The handle, never `ws.store_id`: `pair/confirm` takes a store_id as proof of identity,
    # so a readable copy in this body is a bearer token that outlives every sign-out. The
    # panel also needs it to tell its own roster row apart from everybody else's.
    out = {"paired": True, "handle": _handle(db, ws.store_id),
           "display_name": rider.display_name if rider else "?",
           "flag": rider.flag if rider else None,
           "can_found": crews.can_found(db, ws.store_id),
           "creation_open": cfg["creation_open"],
           "test_notice": cfg["test_notice"],
           "cooldown_until": None, "crew": None, "role": None, "status": None}
    until = crews.cooldown_until(db, ws.store_id)
    if until:
        out["cooldown_until"] = until.isoformat() + "Z"
    if m:
        clan = db.get(Clan, m.clan_id)
        if clan is not None:
            out["crew"] = _crew_brief(db, clan)
            out["role"] = m.role
            out["status"] = m.status
            # Whether the take-over button exists at all. claim_leadership is the designed way
            # out of a crew whose leader stopped riding, and with nothing on screen saying so
            # it protected nobody.
            if m.role != "leader":
                lead = (db.query(ClanMember)
                        .filter(ClanMember.clan_id == clan.clan_id,
                                ClanMember.role == "leader",
                                ClanMember.left_at.is_(None)).first())
                if lead is None:
                    out["leader_stale"] = True        # nobody is in charge at all
                    out["leader_gone"] = True         # and there was never anyone to go quiet
                else:
                    idle = lead.last_seen or lead.joined_at
                    out["leader_stale"] = bool(
                        idle and (utcnow() - idle) >= timedelta(days=crews.IDLE_LEADER_DAYS))
                # and whether this particular rider is the one who may do it. Without this
                # the button rendered for everybody who could see the flag, including riders
                # still waiting to be let in, for whom it can only ever fail.
                out["can_claim"] = bool(out.get("leader_stale")) and crews.claim_eligible(
                    db, ws.store_id, clan.clan_id)
            if m.role in ("leader", "officer"):
                out["crew"]["invite_code"] = clan.invite_code
                # Every rider this panel is about to name, in one query. Three `db.get` calls
                # per row turned a 41-member crew into 133 statements.
                rows = (db.query(ClanMember)
                        .filter(ClanMember.clan_id == clan.clan_id,
                                ClanMember.left_at.is_(None)
                                | ClanMember.status.in_(("declined", "declined_seen")))
                        .all())
                who = {}
                ids = sorted({x.store_id for x in rows})
                if ids:
                    for r in db.query(Rider).filter(Rider.store_id.in_(ids)).all():
                        who[r.store_id] = r

                def _nm(sid):
                    r = who.get(sid)
                    return r.display_name if r is not None else "?"

                def _hd(sid):
                    # Through `_handle` whenever the row fails its invariant, not just when
                    # the handle is missing. `_handle` guards the reader's own handle; this
                    # publishes everybody else's -- the whole roster, every pending request
                    # and every refusal -- so the containment check matters more here, and
                    # this was the one place it was skipped. A reviewer read a polluted
                    # handle out of a roster, stripped the prefix and minted a session.
                    r = who.get(sid)
                    if r is None:
                        return ""
                    if not publishable_handle(r.public_id):
                        return _handle(db, sid)        # mints a clean one; see _handle
                    return r.public_id

                out["roster"] = [
                    {"store_id": _hd(x.store_id), "role": x.role,
                     "name": _nm(x.store_id)}
                    for x in db.query(ClanMember).filter(
                        ClanMember.clan_id == clan.clan_id,
                        ClanMember.status == "active",
                        ClanMember.left_at.is_(None))
                    .order_by(ClanMember.joined_at.asc()).all()]
                out["pending"] = [
                    {"store_id": _hd(p.store_id), "name": _nm(p.store_id)}
                    for p in db.query(ClanMember).filter(
                        ClanMember.clan_id == clan.clan_id,
                        ClanMember.status == "pending",
                        ClanMember.left_at.is_(None)).all()]
                # Refusals from the last week, so a leader who changed their mind has
                # somewhere to do it. crews.decide(accept=True) reopens the request.
                since = utcnow() - timedelta(days=7)
                declined_rows = (db.query(ClanMember).filter(
                    ClanMember.clan_id == clan.clan_id,
                    ClanMember.status.in_(("declined", "declined_seen")),
                    ClanMember.left_at >= since).all())
                dec_ids = sorted({d.store_id for d in declined_rows})

                # Two queries for the whole list rather than two per row. A leader panel with
                # eighteen refusals cost 35 statements, 27 of them against clan_members: each
                # row asked whether that rider had since joined somewhere, and whether a
                # cooldown was running -- and the second question asks the first again.
                in_crew_ids, left_at = set(), {}
                if dec_ids:
                    for row in (db.query(ClanMember)
                                .filter(ClanMember.store_id.in_(dec_ids),
                                        ClanMember.left_at.is_(None)).all()):
                        in_crew_ids.add(row.store_id)
                    # the most recent walk-out per rider, which is what starts the clock
                    for row in (db.query(ClanMember)
                                .filter(ClanMember.store_id.in_(dec_ids),
                                        ClanMember.status == "active",
                                        ClanMember.left_at.isnot(None))
                                .order_by(ClanMember.left_at.asc()).all()):
                        left_at[row.store_id] = row.left_at

                # `_gate` has already read the settings, and this is the same clamped
                # number it returns. Calling `crews._cooldown_days` was a reach into another
                # module's private helper AND a second trip for a figure already in hand --
                # the settings dict is cached except the kill switch, so it cost one query on
                # every panel load, declines or none.
                cool_days = timedelta(days=cfg["cooldown_days"])
                now = utcnow()
                # The third gate `decide(accept=True)` enforces, and the one this row left
                # out: a full crew refuses with `crew_full` while "Let them in" was still
                # being offered. One query for the whole list, not one per row.
                crew_full = crews.is_full(db, clan.clan_id)

                def _declined(sid):
                    in_crew = sid in in_crew_ids
                    gone = left_at.get(sid)
                    cooling = bool(not in_crew and gone and gone + cool_days > now)
                    return {
                        "store_id": _hd(sid), "name": _nm(sid),
                        # Whether letting them in could work. Without it the button is
                        # offered every day for a week to a rider who has since joined
                        # elsewhere, and fails identically every time.
                        "free": not in_crew and not cooling and not crew_full,
                        # Which of the two, because the row printed "in another crew now"
                        # for a rider who had joined nobody and is simply on a cooldown --
                        # the leader was told they had lost somebody who is back in days.
                        "why": ("crew" if in_crew else "cooldown" if cooling
                                else "full" if crew_full else None)}

                out["declined"] = [_declined(d.store_id) for d in declined_rows]
        crews.touch(db, ws.store_id)
    else:
        # Nobody told a rider their request had been turned down; the panel simply went back
        # to the join list, which is also what cancelling your own request looks like.
        answer = crews.last_answer(db, ws.store_id)
        if answer:
            out["declined_by"] = answer["crew"]
        else:
            # Your leader folded the crew, or the last member walked out from under your
            # request. Both drop you back on the join list with no word, which is also what
            # cancelling your own request looks like.
            folded = crews.last_fold(db, ws.store_id)
            if folded:
                out["folded"] = folded["crew"]
            else:
                gone = crews.last_removal(db, ws.store_id)
                if gone:
                    out["removed_by"] = gone["crew"]
    return out


# --- crews --------------------------------------------------------------------------------

@router.get("/crews")
def list_crews(db: Session = Depends(get_db), q: str = "", limit: int = 60):
    _gate(db)
    query = db.query(Clan).filter(Clan.disbanded_at.is_(None))
    if q:
        query = query.filter(Clan.name.ilike(f"%{q.strip()}%"))
    # busiest first, so a shortened list is the useful end of it
    # The emblem is a LargeBinary on the row and is read only to test it for null, which at a
    # 64 KB cap and a few hundred crews is megabytes pulled out of SQLite and thrown away on
    # every listing. Deferred: it is loaded when something actually asks for the image.
    from sqlalchemy.orm import defer
    rows = (query.options(defer(Clan.logo_png))
            .order_by(Clan.terr_best_tiles.desc().nullslast(),
                      Clan.terr_best_km2.desc().nullslast()).limit(min(limit, 100)).all())
    counts = _member_counts(db)
    # The cap, so a full crew's row can say so rather than letting somebody tap Join and be
    # told afterwards. 0 means no cap.
    return {"crews": [_crew_brief(db, c, counts) for c in rows],
            "max_members": _gate(db)["max_members"]}


@router.get("/crews/identity")
def crew_identity(request: Request, db: Session = Depends(get_db)):
    """A suggested colour and pattern: one of the least-used combinations.

    Nobody is shown ninety-six swatches. The least-used pair spreads the palette across the
    map on its own, and a founder who does not care never has to think about it.
    """
    _gate(db)
    # Where this rider rides, so the suggestion can avoid the colours already on the ground
    # around them rather than only the ones rare worldwide.
    near = None
    ws = _me(request, db)
    if ws is not None:
        t = (db.query(Trip.start_lat, Trip.start_lon)
             .filter(Trip.rider_store_id == ws.store_id,
                     Trip.validation_status == "validated",
                     Trip.start_lat.isnot(None))
             .order_by(Trip.start_utc.desc()).first())
        if t:
            near = (t[0], t[1])
    return crews.suggest_identity(db, near=near)


@router.post("/crews")
def create_crew(payload: dict, request: Request, db: Session = Depends(get_db)):
    cfg = _gate(db)
    if not cfg["creation_open"]:
        raise HTTPException(403, "creation_closed")
    ws = _require_session(request, db)
    try:
        clan = crews.create(db, ws.store_id,
                            name=payload.get("name") or "",
                            description=payload.get("description") or "",
                            colour=payload.get("colour"),
                            pattern=payload.get("pattern"),
                            join_policy=payload.get("join_policy") or "approval")
    except crews.CrewError as e:
        raise _err(e)
    _stamp_recent(db, ws.store_id, clan.clan_id)
    return {"ok": True, "crew": _crew_brief(db, clan)}


def _stamp_recent(db: Session, store_id: str, clan_id: str) -> None:
    """Give a crew the rider's rides from the last couple of weeks.

    Without this a crew founded today holds nothing until its members ride again, and the
    founder's first look at the map is an empty one. Only rides that carry no crew already are
    stamped, so nothing is ever taken from another crew's history.

    Two weeks, not the whole ninety-day window. At ninety this was the single biggest lever in
    the game and it needed no riding at all: one free agent joining took the smallest crew from
    4 squares to 53, and signing every unaffiliated rider in the window was worth 685 squares
    and took 44 off the leader. Worse, it is one-shot and global, so recruiting a rider does
    not only gain you their backlog, it denies it to everybody else for good. Two weeks is
    "what you have been riding lately", which is what this was for.
    """
    # The window was never what bound this. Measured on the demo world, every un-credited
    # ride in it was already inside a fortnight, so ninety days to fourteen bought nothing at
    # all: one joiner still took a crew from 4 squares to 67, and it is repeatable with a
    # different rider every time. What the mechanic is for is the stated purpose, that a crew
    # founded today is not looking at an empty map. So it is the crew's fortnight, not the
    # rider's. After that a crew grows by riding.
    clan = db.get(Clan, clan_id)
    if clan is not None and clan.created_at and (
            utcnow() - clan.created_at).days > JOIN_BACKFILL_DAYS:
        return
    since = utcnow() - timedelta(days=JOIN_BACKFILL_DAYS)
    (db.query(Trip)
     .filter(Trip.rider_store_id == store_id, Trip.clan_id.is_(None),
             Trip.start_utc >= since, Trip.validation_status == "validated")
     .update({Trip.clan_id: clan_id}, synchronize_session=False))
    db.commit()


def _clan_by_slug(db: Session, slug: str) -> Clan:
    c = db.query(Clan).filter(Clan.slug == slug, Clan.disbanded_at.is_(None)).first()
    if c is None:
        raise HTTPException(404, "no_crew")
    return c


@router.get("/crews/{slug}")
def crew_detail(slug: str, request: Request, response: Response,
                db: Session = Depends(get_db)):
    # Same reason as `/crews/me`: what comes back depends on whether the reader is in this
    # crew, and for a member it includes `targets` -- which the comment further down calls a
    # crew's plan for its own week, and not a thing the site hands out.
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Vary"] = "Cookie"
    _gate(db)
    clan = _clan_by_slug(db, slug)
    out = _crew_brief(db, clan)
    rows = (db.query(ClanMember).filter(ClanMember.clan_id == clan.clan_id,
                                        ClanMember.status == "active",
                                        ClanMember.left_at.is_(None))
            .order_by(ClanMember.joined_at.asc()).all())
    # one query, not one per member: there is no cap on crew size by default, and a 200-rider
    # crew was 215 statements for a page that needs a dozen
    who = {r.store_id: r for r in db.query(Rider).filter(
        Rider.store_id.in_([m.store_id for m in rows]))} if rows else {}
    out["roster"] = []
    for m in rows:
        r = who.get(m.store_id)
        out["roster"].append({"name": r.display_name if r else "?",
                              "flag": r.flag if r else None, "role": m.role,
                              "joined": m.joined_at.isoformat() + "Z" if m.joined_at else None})
    out["territory"] = {"km2": clan.terr_km2 or 0.0, "tiles": clan.terr_tiles or 0,
                        "best_km2": clan.terr_best_km2 or 0.0,
                        "best_tiles": clan.terr_best_tiles or 0,
                        "regions": clan.terr_regions or 0}
    cfg = settings.get_crews(db)
    out["contributors"] = territory.contributors(db, clan.clan_id, cfg["window_days"])
    # Where to ride next: the whole point of the mode is choosing where to go, and until now
    # nothing told anybody where that was. Only your own crew's list, though — not because the
    # underlying numbers are secret, they are not: /api/v1/territory is public and carries
    # every crew's pressure and shortfall, and the map's own tooltip reads them out to
    # anybody. It is that this is a crew's plan for its own week, and other people's plans are
    # not a thing the site hands out.
    out["targets"] = []
    ws = _me(request, db)
    if ws is not None:
        mine = (db.query(ClanMember)
                .filter(ClanMember.store_id == ws.store_id,
                        ClanMember.clan_id == clan.clan_id,
                        ClanMember.status == "active",
                        ClanMember.left_at.is_(None)).first())
        if mine is not None:
            try:
                out["targets"] = json.loads(clan.targets_json) if clan.targets_json else []
            except Exception:
                out["targets"] = []
            # name the crew holding each square. "Cykelslangen holds it" is somewhere to go;
            # "someone holds it" is a fact about the database.
            ids = {t["held_by"] for t in out["targets"] if t.get("held_by")}
            if ids:
                # Living crews only. A folded crew keeps its row and carries a retirement
                # tag in its name, which was being printed at riders as
                # "Spree Shift (folded a1b2c3) has it".
                rows = (db.query(Clan.clan_id, Clan.name)
                        .filter(Clan.clan_id.in_(ids),
                                Clan.disbanded_at.is_(None)).all())
                names = {r[0]: (r[1],) for r in rows}
                for t in out["targets"]:
                    hit = names.get(t.get("held_by"))
                    t["held_name"] = hit[0] if hit else None
                    # `held_tiles` used to ride along here with the holder's ranked number.
                    # The card gets both that and their place from the board it already has,
                    # so this was the same number shipped twice, once per target.
            for t in out["targets"]:
                t["held_by"] = bool(t.get("held_by"))
    return out


@router.post("/crews/{slug}/join")
def join_crew(slug: str, payload: dict, request: Request, db: Session = Depends(get_db)):
    cfg = _gate(db)
    ws = _require_session(request, db)
    clan = _clan_by_slug(db, slug)
    if cfg["max_members"]:
        n = (db.query(ClanMember).filter(ClanMember.clan_id == clan.clan_id,
                                         ClanMember.status == "active",
                                         ClanMember.left_at.is_(None)).count())
        if n >= cfg["max_members"]:
            raise HTTPException(403, "crew_full")
    try:
        m = crews.join(db, ws.store_id, clan.clan_id, payload.get("invite_code"))
    except crews.CrewError as e:
        raise _err(e)
    if m.status == "active":
        _stamp_recent(db, ws.store_id, clan.clan_id)
    return {"ok": True, "status": m.status}


@router.post("/crews/leave")
def leave_crew(request: Request, db: Session = Depends(get_db)):
    _gate(db)
    ws = _require_session(request, db)
    try:
        crews.leave(db, ws.store_id)
    except crews.CrewError as e:
        raise _err(e)
    # Past rides keep their crew: territory is a record of what was ridden, and rewriting it
    # on every departure would let a rider erase a crew's map by walking out of it.
    return {"ok": True}


@router.post("/crews/{slug}/decide")
def decide_member(slug: str, payload: dict, request: Request, db: Session = Depends(get_db)):
    _gate(db)
    ws = _require_session(request, db)
    clan = _clan_by_slug(db, slug)
    target = _by_handle(db, payload.get("store_id"))
    accept = bool(payload.get("accept"))
    try:
        crews.decide(db, ws.store_id, clan.clan_id, target, accept)
    except crews.CrewError as e:
        raise _err(e)
    if accept:
        _stamp_recent(db, target, clan.clan_id)
    return {"ok": True}


@router.post("/crews/{slug}/remove")
def remove_member(slug: str, payload: dict, request: Request, db: Session = Depends(get_db)):
    """Take somebody off the roster. Leader or officer only; see crews.remove."""
    _gate(db)
    ws = _require_session(request, db)
    clan = _clan_by_slug(db, slug)
    try:
        crews.remove(db, ws.store_id, clan.clan_id,
                     _by_handle(db, payload.get("store_id")))
    except crews.CrewError as e:
        raise _err(e)
    return {"ok": True}


@router.post("/crews/{slug}/role")
def set_member_role(slug: str, payload: dict, request: Request, db: Session = Depends(get_db)):
    _gate(db)
    ws = _require_session(request, db)
    clan = _clan_by_slug(db, slug)
    try:
        crews.set_role(db, ws.store_id, clan.clan_id,
                       _by_handle(db, payload.get("store_id")),
                       (payload.get("role") or "").strip())
    except crews.CrewError as e:
        raise _err(e)
    return {"ok": True}


@router.post("/crews/{slug}/claim")
def claim_leadership(slug: str, request: Request, db: Session = Depends(get_db)):
    """Take over a crew whose leader has gone quiet. No admin involved."""
    _gate(db)
    ws = _require_session(request, db)
    clan = _clan_by_slug(db, slug)
    try:
        crews.claim_leadership(db, ws.store_id, clan.clan_id)
    except crews.CrewError as e:
        raise _err(e)
    return {"ok": True}


@router.post("/crews/{slug}/edit")
def edit_crew(slug: str, payload: dict, request: Request, db: Session = Depends(get_db)):
    _gate(db)
    ws = _require_session(request, db)
    clan = _clan_by_slug(db, slug)
    m = (db.query(ClanMember)
         .filter(ClanMember.clan_id == clan.clan_id, ClanMember.store_id == ws.store_id,
                 ClanMember.left_at.is_(None)).first())
    if m is None or m.role not in ("leader", "officer"):
        raise HTTPException(403, "forbidden")
    if "name" in payload:
        name = (payload["name"] or "").strip()
        if not crews.NAME_RE.match(name):
            raise HTTPException(400, json.dumps({"code": "bad_name",
                                                 "detail": "3-28 characters."}))
        # Every crew, not only the living ones: the column is UNIQUE for the life of the
        # table, so clashing against live crews alone passed here and raised an
        # IntegrityError nobody caught. Same reasoning as crews.create.
        clash = db.query(Clan).filter(Clan.name == name,
                                      Clan.clan_id != clan.clan_id).first()
        if clash:
            raise HTTPException(400, json.dumps({"code": "name_taken",
                                                 "detail": "That name is taken."}))
        clan.name = name
        # the slug is NOT regenerated: links to a crew should not rot because it renamed
    if "description" in payload:
        clan.description = (payload["description"] or "").strip()[:280]
    if "join_policy" in payload and payload["join_policy"] in crews.JOIN_POLICIES:
        clan.join_policy = payload["join_policy"]
    for field in ("colour", "pattern"):
        if field in payload:
            colour = payload.get("colour", clan.colour)
            pattern = payload.get("pattern", clan.pattern)
            if colour not in crews.PALETTE or pattern not in crews.PATTERNS:
                raise HTTPException(400, json.dumps({"code": "bad_identity",
                                                     "detail": "Not in the palette."}))
            if crews.identity_taken(db, colour, pattern, exclude=clan.clan_id):
                raise HTTPException(400, json.dumps({"code": "identity_taken",
                                                     "detail": "Another crew flies those."}))
            clan.colour, clan.pattern = colour, pattern
            break
    db.commit()
    return {"ok": True, "crew": _crew_brief(db, clan)}


@router.post("/crews/{slug}/disband")
def disband_crew(slug: str, request: Request, db: Session = Depends(get_db)):
    _gate(db)
    ws = _require_session(request, db)
    clan = _clan_by_slug(db, slug)
    try:
        crews.disband(db, ws.store_id, clan.clan_id)
    except crews.CrewError as e:
        raise _err(e)
    return {"ok": True}


# --- emblems ------------------------------------------------------------------------------

MAX_EMBLEM_BYTES = 64 * 1024


@router.get("/crews/{slug}/emblem")
def crew_emblem(slug: str, db: Session = Depends(get_db)):
    """The uploaded emblem, or the generated one. Always something, never a broken image."""
    _gate(db)        # the one crew route that answered with the mode switched off
    clan = db.query(Clan).filter(Clan.slug == slug).first()
    if clan is None:
        raise HTTPException(404, "no_crew")
    if clan.logo_png:
        return Response(clan.logo_png, media_type="image/png",
                        headers={"Cache-Control": "public, max-age=3600"})
    svg = crews.placeholder_emblem(clan.name, clan.colour)
    return Response(svg, media_type="image/svg+xml",
                    headers={"Cache-Control": "public, max-age=3600"})


@router.post("/crews/{slug}/emblem")
async def upload_emblem(slug: str, request: Request, file: UploadFile = File(...),
                        db: Session = Depends(get_db)):
    """Accept a small square image and re-encode it.

    Re-encoded rather than stored as sent: an uploaded file is not an image until something
    has decoded it, and a crew emblem is served to every visitor looking at the map. Decoding
    and re-writing it strips metadata, normalises the size, and means anything that is not
    really an image fails here instead of in a thousand browsers.
    """
    _gate(db)
    ws = _require_session(request, db)
    clan = _clan_by_slug(db, slug)
    m = (db.query(ClanMember)
         .filter(ClanMember.clan_id == clan.clan_id, ClanMember.store_id == ws.store_id,
                 ClanMember.left_at.is_(None)).first())
    if m is None or m.role not in ("leader", "officer"):
        raise HTTPException(403, "forbidden")
    raw = await file.read()
    if len(raw) > 2 * 1024 * 1024:
        raise HTTPException(413, "too_large")
    try:
        from io import BytesIO
        from PIL import Image
        # Pillow will happily decode a 370 KB file into a 90-megapixel surface — about a
        # gigabyte of RAM once converted and resampled, which OOM-kills the process. The size
        # is known straight after open(), before any of that work happens, so the guard costs
        # nothing and goes first.
        Image.MAX_IMAGE_PIXELS = 40_000_000
        img = Image.open(BytesIO(raw))
        if img.size[0] * img.size[1] > 30_000_000:
            raise ValueError("too many pixels")
        img.load()
        if max(img.size) > 4096:          # before any resampling work is done on it
            raise ValueError("too many pixels")
        img = img.convert("RGBA")
        # Fitted inside the square, not cropped to fill it. Cropping a wide logo to a square
        # cuts a third of it off, and a crew emblem is artwork somebody chose — a 600x380
        # badge should arrive intact with space either side, not with its ends removed.
        img.thumbnail((128, 128), Image.LANCZOS)
        square = Image.new("RGBA", (128, 128), (0, 0, 0, 0))
        square.paste(img, ((128 - img.size[0]) // 2, (128 - img.size[1]) // 2))
        img = square
        buf = BytesIO()
        img.save(buf, format="PNG", optimize=True)
        out = buf.getvalue()
    except Exception:
        raise HTTPException(422, "not_an_image")
    if len(out) > MAX_EMBLEM_BYTES:
        raise HTTPException(413, "too_large_after_encode")
    clan.logo_png = out
    db.commit()
    return {"ok": True, "bytes": len(out)}


@router.delete("/crews/{slug}/emblem")
def clear_emblem(slug: str, request: Request, db: Session = Depends(get_db)):
    _gate(db)
    ws = _require_session(request, db)
    clan = _clan_by_slug(db, slug)
    m = (db.query(ClanMember)
         .filter(ClanMember.clan_id == clan.clan_id, ClanMember.store_id == ws.store_id,
                 ClanMember.left_at.is_(None)).first())
    if m is None or m.role not in ("leader", "officer"):
        raise HTTPException(403, "forbidden")
    clan.logo_png = None
    db.commit()
    return {"ok": True}


# --- territory ----------------------------------------------------------------------------

@router.get("/territory")
def territory_payload(request: Request, db: Session = Depends(get_db)):
    """The pre-built map payload, straight off disk.

    Already gzipped on disk, so this hands over bytes without compressing anything, and an
    ETag off the file's own timestamp means a client that has looked recently gets a 304. The
    map is the most-requested thing in the feature and it costs a stat() and a read().
    """
    cfg = _gate(db)
    zoom = cfg["zoom"]
    body = territory.cached(zoom)
    if body is None:
        return JSONResponse({"z": zoom, "crews": [], "cells": [], "regions": [],
                             "generated": None, "pending": True})
    etag = f'W/"terr-{zoom}-{int(territory.cached_mtime(zoom))}"'
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304)
    # The file is gzipped on disk and normally handed over as it is, but a client that
    # explicitly refused compression was being given it anyway.
    accept = request.headers.get("accept-encoding", "")
    if "gzip" not in accept.lower():
        import gzip as _gz
        return Response(_gz.decompress(body), media_type="application/json",
                        headers={"ETag": etag,
                                 "Cache-Control": "public, max-age=60, must-revalidate",
                                 "Vary": "Accept-Encoding"})
    return Response(body, media_type="application/json",
                    headers={"Content-Encoding": "gzip", "ETag": etag,
                             # A minute, and revalidate after it. At five minutes a rebuild
                             # took that long to reach anybody, and a browser holding the old
                             # map while the server has a new one is indistinguishable from a
                             # rendering bug: I spent a while chasing two tiles that were
                             # already fixed. The revalidation is an ETag round trip against
                             # an endpoint that costs 1.6 ms.
                             "Cache-Control": "public, max-age=60, must-revalidate",
                             "Vary": "Accept-Encoding"})


@router.get("/crews/ranking/all")
def crew_ranking(db: Session = Depends(get_db), limit: int = 50):
    _gate(db)
    return {"crews": territory.ranking(db, limit)}


@router.get("/territory/at")
def territory_at(lat: float, lon: float, db: Session = Depends(get_db)):
    """Who holds the tile at a coordinate — what the map's click handler asks."""
    cfg = _gate(db)
    from models import ClanCell
    tile = T.tile_of(lat, lon, cfg["zoom"])
    if tile is None:
        raise HTTPException(400, "bad_coordinate")
    cell = db.query(ClanCell).filter(ClanCell.tile == tile).first()
    if cell is None:
        return {"tile": tile, "crew": None, "area_km2": round(T.area_km2(tile), 2)}
    clan = _living(db, cell.clan_id)
    return {"tile": tile, "area_km2": round(T.area_km2(tile), 2),
            "km": round(cell.km or 0.0, 1), "riders": cell.riders,
            "since": cell.first_led.isoformat() + "Z" if cell.first_led else None,
            "crew": _crew_brief(db, clan) if clan else None}


# --- the deep link the QR actually carries ------------------------------------------------

_PAIR_SHELL = """<!doctype html><html lang=__LANG__><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>__TITLE__</title>
<link rel=icon type="image/png" href="/static/favicon.png">
<link rel=preconnect href="https://fonts.googleapis.com"><link rel=preconnect
 href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Chakra+Petch:wght@400;600;700\
&family=Orbitron:wght@700;800&display=swap" rel=stylesheet><style>
:root{color-scheme:dark;--ink:#eef1fb;--mut:#9aa6c8;--line:#33457a;--pink:#ff8ad8}
*{box-sizing:border-box;margin:0}
body{min-height:100vh;display:flex;align-items:center;justify-content:center;padding:18px;
 background:#070a16;color:var(--ink);
 font:15px/1.6 "Chakra Petch",ui-sans-serif,system-ui,Segoe UI,Roboto,sans-serif}
.w{width:100%;max-width:420px;padding:26px 22px 22px;
 background:linear-gradient(158deg,rgba(26,40,78,.86),rgba(8,12,26,.87));
 border:1px solid var(--line);border-top:2px solid var(--pink);border-radius:12px;
 box-shadow:0 30px 90px rgba(0,0,0,.65),inset 0 0 70px -52px var(--pink)}
h1{font:800 19px/1.25 Orbitron,ui-sans-serif,sans-serif;letter-spacing:.5px}
.code{font:800 32px/1 Orbitron,ui-monospace,monospace;letter-spacing:9px;
 margin:18px 0 6px;color:var(--pink);text-align:center}
a.btn{display:block;margin:18px 0 10px;padding:13px;background:var(--pink);color:#140a11;
 font-weight:700;text-decoration:none;border-radius:8px;text-align:center}
a.back{display:inline-block;margin-top:4px;color:var(--pink);font-size:13.5px}
p{color:var(--mut);font-size:13.5px;margin-top:10px}b{color:var(--ink)}
.safe{margin-top:20px;font-size:12px}
</style></head><body><div class=w>__BODY__</div></body></html>"""

_PAIR_LIVE = """<h1>__H__</h1>
<p>__P__</p>
<div class=code>__CODE__</div>
<a class=btn href="eucplanet://pair?code=__CODE__&amp;host=__HOST__">__OPEN__</a>
<p>__NOAPP__</p>
<p class=safe>__SAFE__</p>
<p><a class=back href="__HOST__/">__SITE__</a></p>"""

# A code lives three minutes and works once, so a scan of a photographed QR is likelier to be
# stale than live. This page used to render byte-identical either way -- the heading, the code,
# the button, the safety notice -- and hand the app a code that could not work.
_PAIR_DEAD = """<h1>__DEADH__</h1>
<p>__DEADP__</p>
<a class=btn href="__HOST__/">__SITE__</a>"""


@pair_router.get("/p/{code}")
def pair_landing(code: str, request: Request, db: Session = Depends(get_db)):
    """Where a scanned pairing QR lands.

    The QR carries this URL including the host it was served from, which is the whole reason
    one app build can pair against a laptop for testing and against production in a rider's
    pocket. The app reads the host out of the link rather than having it compiled in, so there
    is no debug flavour, no second APK and no special rider id — but it only ADOPTS a
    non-production host when the rider has switched developer mode on, so a QR code taped to a
    wall cannot redirect somebody's app at a stranger's server.

    A human who scans it with a plain camera app gets this page instead of a dead link.
    """
    from fastapi.responses import HTMLResponse
    from web import i18n
    _gate(db)
    safe = "".join(ch for ch in (code or "").upper() if ch.isalnum())[:12]
    host = str(request.base_url).rstrip("/")
    # The rider scanned this with a camera app, so there is no stored preference to read and
    # no script to run -- only the header. `i18n.pick` is the same negotiation the public page
    # uses. Three of these strings already existed: the button is the sign-in card's own, the
    # section name is the dock's, and the site name is the page title's.
    loc = i18n.pick(request.headers.get("accept-language", ""))

    def t(key, **vars):
        """The translation escaped, then trusted HTML substituted INTO it.

        The other order -- substitute, then escape the lot -- is what shipped in round sixteen,
        and it served `Say yes in &lt;b&gt;EUC Planet&lt;/b&gt;` in all nineteen languages. A
        `{name}` placeholder survives `escape()` untouched, so this order costs nothing.
        """
        text = i18n.TRANSLATIONS.get(loc, {}).get(key) or i18n.EN.get(key, key)
        out = escape(text)
        for name, value in vars.items():
            out = out.replace("{" + name + "}", value)
        return out

    # `describe()` is a read and consumes nothing, so asking costs the page nothing either.
    live = True
    try:
        pairing.describe(db, safe)
    except pairing.PairError:
        live = False

    app_name = "<b>EUC Planet</b>"
    crews_name = "<b>" + t("dock.crews") + "</b>"
    body = (_PAIR_LIVE if live else _PAIR_DEAD)
    page = (_PAIR_SHELL.replace("__BODY__", body)
            .replace("__LANG__", escape(loc))
            .replace("__TITLE__", t("pair.h") if live else t("pair.dead.h"))
            .replace("__H__", t("pair.h"))
            .replace("__P__", t("pair.p", app=app_name))
            .replace("__OPEN__", t("crew.signin.open"))
            .replace("__NOAPP__", t("pair.noapp", crews=crews_name))
            .replace("__SAFE__", t("pair.safe"))
            .replace("__DEADH__", t("pair.dead.h"))
            .replace("__DEADP__", t("pair.dead.p", crews=crews_name))
            .replace("__SITE__", t("pair.site"))
            .replace("__CODE__", safe).replace("__HOST__", escape(host)))
    return HTMLResponse(page)
