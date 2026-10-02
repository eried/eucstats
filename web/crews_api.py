"""Crews & Territory: pairing, crew management and the territory payload.

Everything here is either cheap or cached. The territory map is a pre-built gzipped file
served with an ETag, the ranking is one pass over a table with one row per held tile, and crew
writes are small and rare. Nothing in this module walks the trips table on a page view.
"""
from __future__ import annotations

import json
import time

from fastapi import APIRouter, Depends, HTTPException, Request, Response, UploadFile, File
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from database import get_db
from models import Clan, ClanMember, Rider, Trip
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


def _gate(db: Session) -> dict:
    cfg = settings.get_crews(db)
    if not cfg["enabled"]:
        raise HTTPException(404, "crews_disabled")
    return cfg


def _me(request: Request, db: Session) -> ClanMember | None:
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
        raise HTTPException(429, e.detail)
    base = str(request.base_url).rstrip("/")
    url = f"{base}/p/{p['code']}"
    return {"token": p["token"], "code": p["code"], "expires_in": p["expires_in"],
            "url": url, "qr": pairing.qr_png_b64(url)}


@router.get("/pair/poll")
def pair_poll(token: str, response: Response, db: Session = Depends(get_db)):
    """The browser waits here. On success the session lands in an HttpOnly cookie."""
    _gate(db)
    try:
        res = pairing.poll(db, token)
    except pairing.PairError as e:
        raise HTTPException(410, e.code)
    if res.get("session"):
        out = JSONResponse({k: v for k, v in res.items() if k != "session"})
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
        raise HTTPException(410, e.code)


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
        raise HTTPException(410, e.code)


@router.post("/crews/signout")
def signout(request: Request, db: Session = Depends(get_db)):
    sid = request.cookies.get(pairing.COOKIE)
    if sid:
        pairing.revoke(db, sid)
    out = JSONResponse({"ok": True})
    out.delete_cookie(pairing.COOKIE, path="/")
    return out


# --- who am I -----------------------------------------------------------------------------

def _crew_brief(db: Session, clan: Clan) -> dict:
    n = (db.query(ClanMember)
         .filter(ClanMember.clan_id == clan.clan_id, ClanMember.status == "active",
                 ClanMember.left_at.is_(None)).count())
    return {"slug": clan.slug, "name": clan.name, "description": clan.description,
            "colour": clan.colour, "pattern": clan.pattern, "members": n,
            "join_policy": clan.join_policy, "has_logo": clan.logo_png is not None,
            "emblem": f"/api/v1/crews/{clan.slug}/emblem"}


@router.get("/crews/me")
def crews_me(request: Request, db: Session = Depends(get_db)):
    """The state the crew panel renders from: session, membership, and what it may do."""
    cfg = _gate(db)
    ws = _me(request, db)
    if ws is None:
        return {"paired": False, "creation_open": cfg["creation_open"]}
    rider = db.get(Rider, ws.store_id)
    m = crews.membership(db, ws.store_id)
    out = {"paired": True, "store_id": ws.store_id,
           "display_name": rider.display_name if rider else "?",
           "flag": rider.flag if rider else None,
           "can_found": crews.can_found(db, ws.store_id),
           "creation_open": cfg["creation_open"],
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
            if m.role in ("leader", "officer"):
                out["crew"]["invite_code"] = clan.invite_code
                out["pending"] = [
                    {"store_id": p.store_id,
                     "name": (db.get(Rider, p.store_id).display_name
                              if db.get(Rider, p.store_id) else "?")}
                    for p in db.query(ClanMember).filter(
                        ClanMember.clan_id == clan.clan_id,
                        ClanMember.status == "pending",
                        ClanMember.left_at.is_(None)).all()]
        crews.touch(db, ws.store_id)
    return out


# --- crews --------------------------------------------------------------------------------

@router.get("/crews")
def list_crews(db: Session = Depends(get_db), q: str = "", limit: int = 100):
    _gate(db)
    query = db.query(Clan).filter(Clan.disbanded_at.is_(None))
    if q:
        query = query.filter(Clan.name.ilike(f"%{q.strip()}%"))
    return {"crews": [_crew_brief(db, c) for c in query.limit(min(limit, 200)).all()]}


@router.get("/crews/identity")
def crew_identity(db: Session = Depends(get_db)):
    """A suggested colour and pattern: one of the least-used combinations.

    Nobody is shown ninety-six swatches. The least-used pair spreads the palette across the
    map on its own, and a founder who does not care never has to think about it.
    """
    _gate(db)
    return crews.suggest_identity(db)


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
    """Give a brand-new crew the rider's rides from inside the current window.

    Without this a crew founded today holds nothing until its members ride again, and the
    founder's first look at the map is an empty one. Only rides still inside the rolling window
    are stamped, and only rides that carry no crew already — nothing is taken from another
    crew's history.
    """
    from datetime import timedelta
    from models import utcnow
    cfg = settings.get_crews(db)
    since = utcnow() - timedelta(days=cfg["window_days"])
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
def crew_detail(slug: str, request: Request, db: Session = Depends(get_db)):
    _gate(db)
    clan = _clan_by_slug(db, slug)
    out = _crew_brief(db, clan)
    rows = (db.query(ClanMember).filter(ClanMember.clan_id == clan.clan_id,
                                        ClanMember.status == "active",
                                        ClanMember.left_at.is_(None))
            .order_by(ClanMember.joined_at.asc()).all())
    out["roster"] = []
    for m in rows:
        r = db.get(Rider, m.store_id)
        out["roster"].append({"name": r.display_name if r else "?",
                              "flag": r.flag if r else None, "role": m.role,
                              "joined": m.joined_at.isoformat() + "Z" if m.joined_at else None})
    held = territory.ranking(db, limit=500)
    mine = next((h for h in held if h["clan_id"] == clan.clan_id), None)
    out["territory"] = {"km2": mine["km2"] if mine else 0.0,
                        "tiles": mine["tiles"] if mine else 0}
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
    target = (payload.get("store_id") or "").strip()
    accept = bool(payload.get("accept"))
    try:
        crews.decide(db, ws.store_id, clan.clan_id, target, accept)
    except crews.CrewError as e:
        raise _err(e)
    if accept:
        _stamp_recent(db, target, clan.clan_id)
    return {"ok": True}


@router.post("/crews/{slug}/role")
def set_member_role(slug: str, payload: dict, request: Request, db: Session = Depends(get_db)):
    _gate(db)
    ws = _require_session(request, db)
    clan = _clan_by_slug(db, slug)
    try:
        crews.set_role(db, ws.store_id, clan.clan_id,
                       (payload.get("store_id") or "").strip(),
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
        clash = db.query(Clan).filter(Clan.name == name, Clan.clan_id != clan.clan_id,
                                      Clan.disbanded_at.is_(None)).first()
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
        img = Image.open(BytesIO(raw))
        img.load()
        img = img.convert("RGBA")
        side = min(img.size)
        left = (img.size[0] - side) // 2
        top = (img.size[1] - side) // 2
        img = img.crop((left, top, left + side, top + side)).resize((128, 128), Image.LANCZOS)
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
    return Response(body, media_type="application/json",
                    headers={"Content-Encoding": "gzip", "ETag": etag,
                             "Cache-Control": "public, max-age=300",
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
    clan = db.get(Clan, cell.clan_id)
    return {"tile": tile, "area_km2": round(T.area_km2(tile), 2),
            "km": round(cell.km or 0.0, 1), "riders": cell.riders,
            "since": cell.first_led.isoformat() + "Z" if cell.first_led else None,
            "crew": _crew_brief(db, clan) if clan else None}


# --- the deep link the QR actually carries ------------------------------------------------

_PAIR_PAGE = """<!doctype html><html lang=en><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>Pair with EUC Planet</title><style>
:root{color-scheme:dark}
body{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;
 background:#070b16;color:#dce6f7;font:15px/1.6 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
.w{max-width:420px;padding:28px 24px;text-align:center}
.code{font:700 34px/1 ui-monospace,Menlo,Consolas,monospace;letter-spacing:9px;margin:18px 0 6px}
a.btn{display:block;margin:20px 0 8px;padding:13px;background:#2ea8ff;color:#061020;
 font-weight:700;text-decoration:none}
p{color:#8c99bb;font-size:13.5px}b{color:#dce6f7}
</style></head><body><div class=w>
<h1 style="font-size:19px;margin:0">Pair this browser</h1>
<p>Approve it in <b>EUC Planet</b> to use Crews.</p>
<div class=code>__CODE__</div>
<a class=btn href="eucplanet://pair?code=__CODE__&amp;host=__HOST__">Open EUC Planet</a>
<p>If the app did not open, start it yourself, go to <b>Crews &rarr; Scan</b>, and enter the
code above.</p>
<p style="margin-top:22px;font-size:12px">Only approve a code you asked for. Approving one
signs that browser in as you &mdash; for crews only. It can never upload a ride, rename you or
delete anything.</p>
</div></body></html>"""


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
    _gate(db)
    safe = "".join(ch for ch in (code or "").upper() if ch.isalnum())[:12]
    host = str(request.base_url).rstrip("/")
    return HTMLResponse(_PAIR_PAGE.replace("__CODE__", safe).replace("__HOST__", host))
