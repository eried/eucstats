"""Crews: membership, identity and the rules around joining.

A crew is a group of riders; the ground their riding covers is territory (services/territory.py).
Internally everything is `clan`, because renaming a column later is expensive and renaming a
label is not.
"""
from __future__ import annotations

import hashlib
import random
import re
import uuid
from datetime import timedelta

from models import Clan, ClanMember, Trip, utcnow

# 24 colours chosen to stay apart from each other on a map and to survive the common forms of
# colour blindness — no red/green pair carries meaning on its own, which is why every crew also
# has a pattern and an emblem.
PALETTE = [
    "#e6194b", "#3cb44b", "#ffe119", "#4363d8", "#f58231", "#911eb4",
    "#46f0f0", "#f032e6", "#bcf60c", "#fabebe", "#008080", "#e6beff",
    "#9a6324", "#fffac8", "#800000", "#aaffc3", "#808000", "#ffd8b1",
    "#000075", "#a9a9a9", "#ff7043", "#56c5f0", "#7bd389", "#c2410c",
]
# Patterns cost one sprite each, not one per crew: the fill layer paints the colour and a
# second layer paints the pattern over it. A fifth pattern is one more image, not twenty-four.
PATTERNS = ["solid", "stripes", "dots", "hatch"]

JOIN_POLICIES = ("open", "approval", "invite")
COOLDOWN_DAYS = 7          # after leaving, before joining another crew
IDLE_LEADER_DAYS = 90      # before the longest-serving member may claim leadership
NAME_RE = re.compile(r"^[\w \-'&.]{3,28}$", re.UNICODE)


class CrewError(Exception):
    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code, self.detail = code, detail


def slugify(name: str) -> str:
    s = re.sub(r"[^\w]+", "-", (name or "").strip().lower(), flags=re.UNICODE).strip("-")
    return s or uuid.uuid4().hex[:8]


# --- identity -----------------------------------------------------------------------------

def suggest_identity(db) -> dict:
    """A random one of the LEAST-used (colour, pattern) pairs.

    Nobody is shown a grid of ninety-six swatches to pick from. Counting how many crews hold
    each combination and offering one of the rarest keeps the map spread across the palette
    without any founder having to think about it, and it is one GROUP BY over a table with one
    row per crew.
    """
    taken: dict[tuple[str, str], int] = {}
    for c, p in db.query(Clan.colour, Clan.pattern).filter(Clan.disbanded_at.is_(None)).all():
        taken[(c, p)] = taken.get((c, p), 0) + 1
    combos = [(c, p) for c in PALETTE for p in PATTERNS]
    fewest = min(taken.get(k, 0) for k in combos)
    pool = [k for k in combos if taken.get(k, 0) == fewest]
    colour, pattern = random.choice(pool)
    return {"colour": colour, "pattern": pattern, "free": len(pool), "used": len(taken)}


def identity_taken(db, colour: str, pattern: str, exclude: str | None = None) -> bool:
    q = db.query(Clan).filter(Clan.colour == colour, Clan.pattern == pattern,
                              Clan.disbanded_at.is_(None))
    if exclude:
        q = q.filter(Clan.clan_id != exclude)
    return q.first() is not None


def placeholder_emblem(name: str, colour: str) -> str:
    """An SVG emblem derived from the crew name, for crews that have not uploaded one.

    Generated rather than blank so a crew looks like itself the moment it is founded, and most
    never need to upload anything. The pattern is a hash of the name, mirrored so it reads as
    an emblem rather than noise, with the initials over it.
    """
    h = hashlib.sha256((name or "").encode("utf-8")).digest()
    initials = "".join(w[0] for w in re.split(r"[\s\-_]+", (name or "?").strip()) if w)[:2].upper()
    cells = []
    for row in range(5):
        for col in range(3):                     # mirrored into 5 columns
            if h[row * 3 + col] & 1:
                for c in (col, 4 - col):
                    cells.append(f'<rect x="{c * 20}" y="{row * 20}" width="20" height="20"/>')
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100" width="100" height="100">'
        f'<rect width="100" height="100" fill="{colour}"/>'
        f'<g fill="#000" opacity=".22">{"".join(cells)}</g>'
        '<text x="50" y="50" text-anchor="middle" dominant-baseline="central" '
        'font-family="ui-monospace,Menlo,Consolas,monospace" font-weight="700" font-size="42" '
        f'fill="#fff" stroke="#000" stroke-width="3" paint-order="stroke">{initials}</text></svg>'
    )


# --- membership ---------------------------------------------------------------------------

def membership(db, store_id: str) -> ClanMember | None:
    return (db.query(ClanMember)
            .filter(ClanMember.store_id == store_id, ClanMember.left_at.is_(None))
            .first())


def current_clan_id(db, store_id: str) -> str | None:
    """The crew a trip uploaded right now would be stamped with."""
    m = membership(db, store_id)
    return m.clan_id if (m and m.status == "active") else None


def cooldown_until(db, store_id: str):
    """When this rider may join again, or None if they may join now.

    The cooldown stops crew-hopping to game territory. It is short enough not to feel punitive
    and long enough that it is not worth doing.
    """
    last = (db.query(ClanMember)
            .filter(ClanMember.store_id == store_id, ClanMember.left_at.isnot(None))
            .order_by(ClanMember.left_at.desc()).first())
    if not last or membership(db, store_id):
        return None
    until = last.left_at + timedelta(days=COOLDOWN_DAYS)
    return until if until > utcnow() else None


def can_found(db, store_id: str) -> bool:
    """One validated trip. A low bar, but it proves there is a real wheel behind the crew
    rather than anyone who paired a browser."""
    return db.query(Trip).filter(Trip.rider_store_id == store_id,
                                 Trip.validation_status == "validated").first() is not None


def create(db, store_id: str, name: str, description: str = "", colour: str | None = None,
           pattern: str | None = None, join_policy: str = "approval") -> Clan:
    if not NAME_RE.match((name or "").strip()):
        raise CrewError("bad_name", "3-28 characters, letters, numbers, spaces and - ' & .")
    if membership(db, store_id):
        raise CrewError("already_in_crew", "Leave your crew before founding another.")
    until = cooldown_until(db, store_id)
    if until:
        raise CrewError("cooldown", f"You can found or join a crew after {until:%d %b %H:%M}.")
    if not can_found(db, store_id):
        raise CrewError("no_trips", "Upload one validated ride before founding a crew.")
    if db.query(Clan).filter(Clan.name == name.strip(), Clan.disbanded_at.is_(None)).first():
        raise CrewError("name_taken", "That name is taken.")

    sug = suggest_identity(db)
    colour = colour or sug["colour"]
    pattern = pattern or sug["pattern"]
    if colour not in PALETTE or pattern not in PATTERNS:
        raise CrewError("bad_identity", "Pick a colour and pattern from the palette.")
    if identity_taken(db, colour, pattern):
        raise CrewError("identity_taken", "Another crew already flies those colours.")
    if join_policy not in JOIN_POLICIES:
        join_policy = "approval"

    clan = Clan(clan_id=uuid.uuid4().hex, name=name.strip(), slug=slugify(name),
                description=(description or "").strip()[:280], colour=colour, pattern=pattern,
                join_policy=join_policy, created_by=store_id,
                invite_code=uuid.uuid4().hex[:8].upper())
    db.add(clan)
    db.add(ClanMember(clan_id=clan.clan_id, store_id=store_id, role="leader",
                      status="active", last_seen=utcnow()))
    db.commit()
    return clan


def join(db, store_id: str, clan_id: str, invite_code: str | None = None) -> ClanMember:
    clan = db.get(Clan, clan_id)
    if clan is None or clan.disbanded_at is not None:
        raise CrewError("no_crew", "That crew does not exist.")
    if membership(db, store_id):
        raise CrewError("already_in_crew", "Leave your crew first.")
    until = cooldown_until(db, store_id)
    if until:
        raise CrewError("cooldown", f"You can join a crew after {until:%d %b %H:%M}.")

    if clan.join_policy == "open":
        status = "active"
    elif clan.join_policy == "invite":
        if (invite_code or "").strip().upper() != (clan.invite_code or ""):
            raise CrewError("bad_invite", "That invite code is not right.")
        status = "active"
    else:
        status = "pending"

    m = ClanMember(clan_id=clan_id, store_id=store_id, role="member", status=status,
                   last_seen=utcnow())
    db.add(m)
    db.commit()
    return m


def leave(db, store_id: str) -> None:
    m = membership(db, store_id)
    if m is None:
        raise CrewError("not_in_crew", "You are not in a crew.")
    if m.role == "leader" and _active_members(db, m.clan_id) > 1 and not _officers(db, m.clan_id):
        raise CrewError("promote_first",
                        "Promote an officer before leaving — the crew would have nobody.")
    m.left_at = utcnow()
    db.commit()


def decide(db, actor: str, clan_id: str, store_id: str, accept: bool) -> None:
    """Leader or officer accepts or declines a pending request."""
    _require_power(db, actor, clan_id)
    m = (db.query(ClanMember)
         .filter(ClanMember.clan_id == clan_id, ClanMember.store_id == store_id,
                 ClanMember.status == "pending", ClanMember.left_at.is_(None)).first())
    if m is None:
        raise CrewError("no_request", "No pending request from that rider.")
    if accept:
        m.status = "active"
    else:
        m.left_at = utcnow()
    db.commit()


def set_role(db, actor: str, clan_id: str, store_id: str, role: str) -> None:
    me = _require_power(db, actor, clan_id)
    if role not in ("officer", "member", "leader"):
        raise CrewError("bad_role", "Unknown role.")
    if role == "leader" and me.role != "leader":
        raise CrewError("not_leader", "Only the leader can hand over leadership.")
    m = (db.query(ClanMember)
         .filter(ClanMember.clan_id == clan_id, ClanMember.store_id == store_id,
                 ClanMember.left_at.is_(None)).first())
    if m is None:
        raise CrewError("not_member", "Not a member of this crew.")
    if role == "leader":
        me.role = "officer"
    m.role = role
    db.commit()


def claim_leadership(db, store_id: str, clan_id: str) -> None:
    """The backstop for a crew whose leader has gone quiet.

    Officers cover the ordinary case; this covers the crew where everyone with power has
    stopped riding. The longest-serving active member may take over once the leader has been
    idle for IDLE_LEADER_DAYS, with no admin involvement.
    """
    leader = (db.query(ClanMember)
              .filter(ClanMember.clan_id == clan_id, ClanMember.role == "leader",
                      ClanMember.left_at.is_(None)).first())
    if leader is None:
        raise CrewError("no_leader", "This crew has no leader to replace.")
    idle_since = leader.last_seen or leader.joined_at
    if idle_since and (utcnow() - idle_since) < timedelta(days=IDLE_LEADER_DAYS):
        raise CrewError("leader_active", "The leader is still active.")
    # the leader is excluded explicitly: they founded the crew, so they are always its
    # longest-serving member, and ordering by join date without this clause re-elects the
    # very person who stopped riding
    eligible = (db.query(ClanMember)
                .filter(ClanMember.clan_id == clan_id, ClanMember.status == "active",
                        ClanMember.left_at.is_(None),
                        ClanMember.store_id != leader.store_id)
                .order_by(ClanMember.joined_at.asc()).first())
    if eligible is None or eligible.store_id != store_id:
        raise CrewError("not_eligible", "The longest-serving active member takes over.")
    leader.role = "member"
    eligible.role = "leader"
    db.commit()


def disband(db, actor: str, clan_id: str) -> None:
    """Mark rather than delete: trips still point here, and their territory fades over the
    window rather than vanishing in one frame. The colour and pattern are freed at once."""
    m = _require_power(db, actor, clan_id)
    if m.role != "leader":
        raise CrewError("not_leader", "Only the leader can disband a crew.")
    clan = db.get(Clan, clan_id)
    clan.disbanded_at = utcnow()
    for mm in db.query(ClanMember).filter(ClanMember.clan_id == clan_id,
                                          ClanMember.left_at.is_(None)).all():
        mm.left_at = utcnow()
    db.commit()


def touch(db, store_id: str) -> None:
    """Mark a member as seen — what the idle-leader handover measures against."""
    m = membership(db, store_id)
    if m is not None:
        m.last_seen = utcnow()
        db.commit()


def _active_members(db, clan_id: str) -> int:
    return (db.query(ClanMember)
            .filter(ClanMember.clan_id == clan_id, ClanMember.status == "active",
                    ClanMember.left_at.is_(None)).count())


def _officers(db, clan_id: str) -> int:
    return (db.query(ClanMember)
            .filter(ClanMember.clan_id == clan_id, ClanMember.role == "officer",
                    ClanMember.left_at.is_(None)).count())


def _require_power(db, store_id: str, clan_id: str) -> ClanMember:
    m = (db.query(ClanMember)
         .filter(ClanMember.clan_id == clan_id, ClanMember.store_id == store_id,
                 ClanMember.status == "active", ClanMember.left_at.is_(None)).first())
    if m is None or m.role not in ("leader", "officer"):
        raise CrewError("forbidden", "Only a leader or officer can do that.")
    return m
