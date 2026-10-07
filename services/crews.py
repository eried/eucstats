"""Crews: membership, identity and the rules around joining.

A crew is a group of riders; the ground their riding covers is territory (services/territory.py).
Internally everything is `clan`, because renaming a column later is expensive and renaming a
label is not.
"""
from __future__ import annotations

import hashlib
import math
import random
import re
import unicodedata
import uuid
from datetime import timedelta

from models import Clan, ClanMember, ClanCell, Trip, utcnow

# 48 colours chosen to stay apart from each other on a map and to survive the common forms of
# colour blindness — no red/green pair carries meaning on its own, which is why every crew also
# has a pattern and an emblem.
# Sorted by hue with the greys at the end, rather than in the order the 20-colour
# categorical data-viz list ships in -- which put brown, cream, maroon and mint on one row and
# olive, apricot, navy and grey on the next, in the one grid in the product whose whole job is
# to be looked at.
#
# Twenty-four became forty-eight, and four patterns twelve, because 24x4 is ninety-six and
# forty-four crews already held forty of them. A founder was offered a grid where every swatch
# looked available and about two in five were not, and the only way to find out was to submit
# the form. 48x12 is five hundred and seventy-six, which puts the same forty-four crews at
# under eight per cent -- and the grid now says which ones are gone, so the rate stopped
# mattering.
#
# All twenty-four of the original colours are still here, at their original values: crews hold
# these in the database and `colour not in PALETTE` validates against it, so dropping one is a
# migration and adding one is not. The new entries lean on value as much as hue, which is what
# the first twenty-four were short of -- #8d6e63 next to #5d4037 separates on lightness, where
# a twenty-fifth magenta would have separated on nothing. The closest pair in the set is .014
# apart in a rough luma/opponent space, and no pair is closer than that.
PALETTE = [
    "#800000", "#a52a2a", "#fabebe", "#5d4037", "#ff7043", "#ff5722",
    "#8d6e63", "#c2410c", "#f58231", "#ffd8b1", "#9a6324", "#ffab40",
    "#d4a017", "#bfae48", "#ffe119", "#fffac8", "#808000", "#bcf60c",
    "#6b8e23", "#7cb342", "#2e7d32", "#3cb44b", "#7bd389", "#aaffc3",
    "#00a86b", "#26a69a", "#008080", "#46f0f0", "#00bcd4", "#56c5f0",
    "#607d8b", "#0288d1", "#4363d8", "#7986cb", "#5c6bc0", "#000075",
    "#4a148c", "#e6beff", "#911eb4", "#ba68c8", "#f032e6", "#c51162",
    "#b03060", "#ff4081", "#e6194b", "#ff8fa3", "#a9a9a9", "#4a4a4a",
]
# Patterns cost one sprite each, not one per crew: the fill layer paints the colour and a
# second layer paints the pattern over it. A fifth pattern is one more image, not twenty-four,
# which is why going to twelve costs eight canvases and nothing else.
#
# Ordered so that the two that are hardest to tell apart are never adjacent in the grid, and
# so the first four are the four that existed -- every crew in the database holds one of those
# and they keep their position.
PATTERNS = [
    "solid", "stripes", "dots", "hatch",
    "backslash", "vert", "horiz", "grid",
    "bigdots", "rings", "checker", "bricks",
]

JOIN_POLICIES = ("open", "approval", "invite")
COOLDOWN_DAYS = 7          # the default; the admin's figure comes from settings, see _cooldown_days
IDLE_LEADER_DAYS = 90      # before the longest-serving member may claim leadership
NAME_MIN, NAME_MAX = 3, 28
# The five punctuation characters a crew name may use, and nothing else outside letters,
# numbers and marks. Kept beside `name_ok` so the message and the rule cannot drift.
NAME_PUNCT = frozenset(" -'&._")


def name_ok(name: str) -> bool:
    r"""Whether a crew may be called this. The one rule; the client mirrors it exactly.

    This was `^[\w \-'&.]{3,28}$`, and Python's `\w` does not match combining marks while the
    client's `\p{M}` does. Any decomposed string therefore passed in the browser and was
    refused by the server -- and decomposed is what iOS and macOS text input commonly produce.
    A reviewer measured `Zu\u0308rich Crew` (u + U+0308) refused with "3-28 characters." about
    a twelve-character name.

    So it is written by category now, which is what the client's classes mean, and the name is
    normalised first: the two spellings of "Zurich" are one name, not two rows that look
    identical in a list and collide on nothing.
    """
    s = unicodedata.normalize("NFC", name or "").strip()
    if not (NAME_MIN <= len(s) <= NAME_MAX):
        return False
    if not all(ch in NAME_PUNCT or unicodedata.category(ch)[0] in ("L", "N", "M") for ch in s):
        return False
    # And at least one character somebody could read the crew BY. Every character in
    # `NAME_PUNCT` was allowed, so "..." is three permitted characters and passed -- a crew
    # called "..." on a public leaderboard, which `slugify` then reduced to the empty string
    # and replaced with eight random hex digits, giving it a URL nobody could guess or share.
    # A reviewer founded it. Marks are excluded here too: a combining mark has nothing to
    # attach to on its own.
    return any(unicodedata.category(ch)[0] in ("L", "N") for ch in s)


def clean_name(name: str) -> str:
    """The form a name is stored in. Normalising on the way in is what makes uniqueness mean
    what a reader thinks it means."""
    return unicodedata.normalize("NFC", name or "").strip()


# The sentence both refusal paths use. `/edit` had its own, shorter one -- "3-28 characters."
# for a character-set violation -- so a rejected `Ron's Crew #1` was told its length was wrong.
NAME_RULE_TEXT = "3-28 characters, letters, numbers, spaces and - ' & ."
# The other half of `name_ok`, asked separately so a refusal can name the rule it broke.
# "..." is three permitted characters, so answering it with the sentence above tells somebody
# who typed only dots that the dot is allowed -- which it is, and which is not the problem.
NAME_WORD_TEXT = "A name needs at least one letter or number."

# Names that would let a crew pass itself off as part of the site. A reviewer founded a crew
# called `admin`, which went live at a public `/c/admin` and appeared in the join list beside
# real crews; `RESERVED_SLUGS` did not stop it, because that set exists to keep a crew from
# shadowing an API route and resolves a collision by quietly appending "-crew". A silent
# suffix is the right answer to a routing clash and the wrong one to impersonation: the name
# is the thing being claimed, so the name is what has to be refused, out loud.
#
# Matched on `name_key`, so spacing and case cannot walk around it, and on the slug as well,
# so "A.D.M.I.N" and "a d m i n" are the same claim. Deliberately short: this is a list of
# words that assert authority over the site, not a profanity filter.
RESERVED_NAMES = {
    "admin", "administrator", "moderator", "mod", "staff", "support", "help", "helpdesk",
    "official", "system", "root", "security", "team", "owner", "operator",
    "eucstats", "euc stats", "eucplanet", "euc planet", "anthropic", "claude",
}
RESERVED_NAME_TEXT = "That name is reserved. Pick something that is yours."


def name_reserved(name: str) -> bool:
    """Does this name claim to be the site rather than a crew on it?

    Both forms are checked because both are what a reader sees: the name on the board and the
    slug in the address bar. `slugify` collapses punctuation and spaces to hyphens, so the
    hyphen-free version of the slug catches "A.D.M.I.N" and "a d m i n" without the set having
    to list them.
    """
    key = name_key(name)
    slug = slugify(name)
    return (key in RESERVED_NAMES
            or slug in RESERVED_NAMES
            or slug.replace("-", "") in RESERVED_NAMES)


def name_has_word(name: str) -> bool:
    s = unicodedata.normalize("NFC", name or "").strip()
    return any(unicodedata.category(ch)[0] in ("L", "N") for ch in s)


def name_key(name: str) -> str:
    """What makes two crew names the same name to a reader.

    `clean_name` normalises and strips, and its own comment says that is "what makes
    uniqueness mean what a reader thinks it means" -- but it never folded case, and all four
    uniqueness checks compared with `Clan.name == name`, which is case-sensitive on SQLite.
    So `holmenkollen climb` was founded alongside the existing `Holmenkollen Climb`, and the
    slugger, which lowercases, knew they collided and handed out `holmenkollen-climb-2`. On
    the board and the join list the two rows were told apart only by capitalisation: a free
    impersonation of any crew on the map. A reviewer did it to the number one crew.

    `casefold` rather than `lower`, so this holds outside ASCII too, and whitespace runs
    collapse because "Nordlys  Collective" is not a second crew either. Comparison only --
    the stored name keeps its own capitalisation and spacing.
    """
    return re.sub(r"\s+", " ", clean_name(name)).casefold()


def name_clash(db, name: str, except_id: str | None = None):
    """The crew already using this name, or None.

    Every crew, not only the living ones: `Clan.name` is UNIQUE for the life of the table, so
    checking against live crews alone passed here and raised an IntegrityError nobody caught.
    Disband frees a name by retiring it, so this stays a short list -- short enough that the
    fold happens in Python, where it is correct, rather than in SQLite's ASCII-only `lower()`.
    """
    key = name_key(name)
    if not key:
        return None
    for c in db.query(Clan).all():
        if c.clan_id != except_id and name_key(c.name) == key:
            return c
    return None


class CrewError(Exception):
    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code, self.detail = code, detail


def slugify(name: str) -> str:
    s = re.sub(r"[^\w]+", "-", (name or "").strip().lower(), flags=re.UNICODE).strip("-")
    return s or uuid.uuid4().hex[:8]


RESERVED_SLUGS = {"drawn", "identity", "me", "signout", "ranking", "all", "new", "search",
                  # `POST /crews/notices/seen` -- a crew on this slug would shadow the route
                  # that retires the turned-down / removed / folded notices.
                  "notices"}


def free_slug(db, name: str) -> str:
    """A slug nothing else has ever used.

    `name` and `slug` are both UNIQUE for the life of the table, and only the slug collides
    silently: `Night Riders` and `Night.Riders` are two legal, different names that slugify to
    the same string, so the second founder passed the name check, hit the database and got a
    500 that read `That did not work.` with nothing to act on.
    """
    base = slugify(name)
    # `/api/v1/crews/{slug}` sits under the same prefix as the fixed routes, so a crew
    # called "Drawn" or "Identity" would shadow one and lose its own page.
    if base in RESERVED_SLUGS:
        base = base + "-crew"
    taken = {r[0] for r in db.query(Clan.slug).filter(Clan.slug.like(base + "%")).all()}
    if base not in taken:
        return base
    for n in range(2, 60):
        if f"{base}-{n}" not in taken:
            return f"{base}-{n}"
    return f"{base}-{uuid.uuid4().hex[:6]}"


# --- identity -----------------------------------------------------------------------------

def _hue(hex_colour: str) -> float:
    """Degrees around the wheel, for telling two colours apart at a glance."""
    h = (hex_colour or "").lstrip("#")
    if len(h) != 6:
        return 0.0
    r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
    import colorsys
    return colorsys.rgb_to_hsv(r, g, b)[0] * 360.0


def _rgb(hex_colour: str) -> tuple[float, float, float]:
    h = (hex_colour or "").lstrip("#")
    if len(h) != 6:
        return (0.0, 0.0, 0.0)
    return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))


def _hue_gap(a: str, b: str) -> float:
    """How far apart two crew colours look, in degrees of hue equivalent.

    Hue on its own says grey, maroon and pastel pink are the same colour -- all three report
    0 -- and says a readable pair 55 degrees apart is too close. Saturation and value carry
    the rest of it, so they are folded in: a pair that differs mostly in lightness scores as
    separable even when their hues agree.
    """
    import colorsys
    ha, sa, va = colorsys.rgb_to_hsv(*_rgb(a))
    hb, sb, vb = colorsys.rgb_to_hsv(*_rgb(b))
    d = abs(ha - hb) * 360.0
    hue = min(d, 360.0 - d)
    # A hue difference only means anything when both colours have some saturation to carry
    # it; between two near-greys it means nothing at all.
    hue *= min(sa, sb)
    # and a big step in saturation or lightness is its own separation, worth about 90 degrees
    return hue + 90.0 * (abs(sa - sb) + abs(va - vb)) / 2.0


def neighbour_colours(db, lat: float | None, lon: float | None,
                      km: float = 60.0) -> set[str]:
    """Colours already on the ground near a point.

    A crew's ground is its cells; the cheapest usable position for each crew is the first
    cell it holds, which is enough to answer "is this crew in my city" at sixty kilometres.
    """
    if lat is None or lon is None:
        return set()
    from services import tiles as T

    def near(cy, cx):
        dy = (cy - lat) * 111.32
        dx = (cx - lon) * 111.32 * math.cos(math.radians(lat))
        return dx * dx + dy * dy <= km * km

    out, seen = set(), set()
    for tile, clan_id in db.query(ClanCell.tile, ClanCell.clan_id).all():
        if clan_id in seen:
            continue
        b = T.bounds(tile)
        if b and near((b[1] + b[3]) / 2, (b[0] + b[2]) / 2):
            seen.add(clan_id)
            c = db.get(Clan, clan_id)
            if c is not None and c.disbanded_at is None:
                out.add(c.colour)

    # Ground is written by the hourly rebuild, so a crew founded in the last hour -- which is
    # exactly when the next crew in that city is being founded, and the whole of a seeding
    # run -- holds no cells and would look like it is nowhere. Where its riders ride is the
    # same answer and is there straight away.
    rows = (db.query(ClanMember.clan_id, Trip.start_lat, Trip.start_lon)
            .join(Trip, Trip.rider_store_id == ClanMember.store_id)
            .filter(ClanMember.left_at.is_(None), ClanMember.status == "active",
                    Trip.validation_status == "validated",
                    Trip.start_lat.isnot(None))
            .all())
    for clan_id, tlat, tlon in rows:
        if clan_id in seen or not near(tlat, tlon):
            continue
        seen.add(clan_id)
        c = db.get(Clan, clan_id)
        if c is not None and c.disbanded_at is None:
            out.add(c.colour)
    return out


def suggest_identity(db, near: tuple[float, float] | None = None) -> dict:
    """A random one of the LEAST-used (colour, pattern) pairs, biased away from the neighbours.

    Nobody is shown a grid of ninety-six swatches to pick from. Counting how many crews hold
    each combination and offering one of the rarest keeps the map spread across the palette
    without any founder having to think about it, and it is one GROUP BY over a table with one
    row per crew. That is a global answer to a local question, though: two crews sharing a
    city border in two shades of magenta are one wash at the opacity the map draws, so when
    the caller knows where the founder rides, anything close in hue to a neighbour is dropped.
    """
    taken: dict[tuple[str, str], int] = {}
    by_colour: dict[str, int] = {}
    for c, p in db.query(Clan.colour, Clan.pattern).filter(Clan.disbanded_at.is_(None)).all():
        taken[(c, p)] = taken.get((c, p), 0) + 1
        by_colour[c] = by_colour.get(c, 0) + 1
    combos = [(c, p) for c in PALETTE for p in PATTERNS]
    # Colour first, pattern second. Counting pairs alone let two crews share a colour while
    # twenty others went unused, and at the fourteen-pixel swatch the board draws, two crews
    # on one colour are the same square whatever pattern is printed on them.
    fewest_c = min(by_colour.get(c, 0) for c in PALETTE)
    combos = [k for k in combos if by_colour.get(k[0], 0) == fewest_c]
    fewest = min(taken.get(k, 0) for k in combos)
    pool = [k for k in combos if taken.get(k, 0) == fewest]
    if near:
        nearby = neighbour_colours(db, near[0], near[1])
        if nearby:
            far = [k for k in pool if all(_hue_gap(k[0], n) >= 60 for n in nearby)]
            # only if it leaves anything: a crowded city must not block a founder entirely
            pool = far or pool
    colour, pattern = random.choice(pool)
    # Which pairs are gone, so the grid can say so. Every swatch used to look available while
    # forty of ninety-six were not, and the only way to find out was to submit the form and
    # read "Another crew already flies those colours" -- about two founders in five.
    # One row per live crew, so this is the same scan the counting above already did.
    return {"colour": colour, "pattern": pattern, "free": len(pool), "used": len(taken),
            "taken": sorted([c, p] for c, p in taken)}


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
    # Only crews you were actually in. A join request that was never accepted and then
    # withdrawn used to start the clock, so pulling a request cost a week in a crew you had
    # never ridden a metre for.
    last = (db.query(ClanMember)
            # 'active' only: 'disbanded' rows are exempt (see disband) and so are 'declined'
            # and 'pending' ones, because a request that was never accepted is not a crew you
            # walked out of.
            .filter(ClanMember.store_id == store_id, ClanMember.status == "active",
                    ClanMember.left_at.isnot(None))
            .order_by(ClanMember.left_at.desc()).first())
    if not last or membership(db, store_id):
        return None
    until = last.left_at + timedelta(days=_cooldown_days(db))
    return until if until > utcnow() else None


def _cooldown_days(db) -> int:
    """The admin's figure, read now rather than remembered.

    The admin screen used to assign `crews.COOLDOWN_DAYS` on save, which is one process's
    memory of a number: it did not survive a restart, and it did not reach a second worker.
    Meanwhile the browser is handed the configured value and renders `Next crew in 3 days`
    from it, so after any restart the page promised three and the server still enforced seven.
    A rider waited out the countdown, tapped Join, and was told they were still cooling off.
    """
    from services import settings
    try:
        return int(settings.get_crews(db)["cooldown_days"])
    except (KeyError, TypeError, ValueError):
        return COOLDOWN_DAYS


def can_found(db, store_id: str) -> bool:
    """One validated trip. A low bar, but it proves there is a real wheel behind the crew
    rather than anyone who paired a browser."""
    return db.query(Trip).filter(Trip.rider_store_id == store_id,
                                 Trip.validation_status == "validated").first() is not None


def create(db, store_id: str, name: str, description: str = "", colour: str | None = None,
           pattern: str | None = None, join_policy: str = "approval") -> Clan:
    name = clean_name(name)
    if not name_ok(name):
        raise CrewError("bad_name" if name_has_word(name) else "bad_name_word",
                        NAME_RULE_TEXT if name_has_word(name) else NAME_WORD_TEXT)
    if name_reserved(name):
        raise CrewError("name_reserved", RESERVED_NAME_TEXT)
    if membership(db, store_id):
        raise CrewError("already_in_crew", "Leave your crew before founding another.")
    until = cooldown_until(db, store_id)
    if until:
        raise CrewError("cooldown", f"You can found or join a crew after {until:%d %b %H:%M}.")
    if not can_found(db, store_id):
        raise CrewError("no_trips", "Upload one validated ride before founding a crew.")
    # Disbanded crews included: the column is UNIQUE for the life of the table, so a name
    # checked against live crews only passed here and then raised an IntegrityError nobody
    # caught. Disband frees the name by retiring it, so this stays a short list.
    if name_clash(db, name):
        raise CrewError("name_taken", "That name is taken.")

    # Where this founder rides, so a colour is chosen against the crews next door and not
    # only against the world. The API path passes this already; a client posting no colour
    # was falling back to the global answer.
    t = (db.query(Trip.start_lat, Trip.start_lon)
         .filter(Trip.rider_store_id == store_id, Trip.validation_status == "validated",
                 Trip.start_lat.isnot(None))
         .order_by(Trip.start_utc.desc()).first())
    sug = suggest_identity(db, near=(t[0], t[1]) if t else None)
    colour = colour or sug["colour"]
    pattern = pattern or sug["pattern"]
    if colour not in PALETTE or pattern not in PATTERNS:
        raise CrewError("bad_identity", "Pick a colour and pattern from the palette.")
    if identity_taken(db, colour, pattern):
        raise CrewError("identity_taken", "Another crew already flies those colours.")
    if join_policy not in JOIN_POLICIES:
        join_policy = "approval"

    clan = Clan(clan_id=uuid.uuid4().hex, name=name.strip(), slug=free_slug(db, name),
                description=(description or "").strip()[:280], colour=colour, pattern=pattern,
                join_policy=join_policy, created_by=store_id,
                invite_code=uuid.uuid4().hex[:8].upper(),
                invite_key=uuid.uuid4().hex)
    db.add(clan)
    db.add(ClanMember(clan_id=clan.clan_id, store_id=store_id, role="leader",
                      status="active", last_seen=utcnow()))
    db.commit()
    return clan


def invite_key(db, clan) -> str:
    """The crew's permanent invite token, minted once and then never again.

    Crews founded before the column existed have none, so the first read makes one. It is
    deliberately NOT regenerated by `new_invite_code`: that retires the short typed code, and
    the whole point of this one is that a link already on a sticker keeps working.
    """
    if not clan.invite_key:
        clan.invite_key = uuid.uuid4().hex
        db.commit()
    return clan.invite_key


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
        # Either half admits you: the short code that can be retired, or the permanent key
        # that cannot. The key is compared case-sensitively and in full -- it is machine-read
        # out of a link, never typed -- while the code is the one somebody reads aloud.
        given = (invite_code or "").strip()
        ok = (given.upper() == (clan.invite_code or "")
              or (clan.invite_key and given == clan.invite_key))
        if not ok:
            raise CrewError("bad_invite", "That invite code is not right.")
        status = "active"
    else:
        status = "pending"

    # (clan_id, store_id) is the primary key, so a rider who has ever been in this crew
    # already has a row. Reviving it is the whole fix: inserting raised an IntegrityError
    # that reached the browser as "That did not work" and never stopped doing so.
    m = (db.query(ClanMember)
         .filter(ClanMember.clan_id == clan_id, ClanMember.store_id == store_id).first())
    if m is None:
        m = ClanMember(clan_id=clan_id, store_id=store_id)
        db.add(m)
    m.role = "member"          # coming back is not coming back in charge
    m.status = status
    m.left_at = None
    m.joined_at = utcnow()     # seniority counts the membership you are in
    m.last_seen = utcnow()
    db.commit()
    return m


def leave(db, store_id: str) -> None:
    m = membership(db, store_id)
    if m is None:
        raise CrewError("not_in_crew", "You are not in a crew.")
    if m.role == "leader" and _active_members(db, m.clan_id) > 1 and not _officers(db, m.clan_id):
        raise CrewError("promote_first",
                        "Make somebody an officer first. Someone has to run the place.")
    # The last one out turns the lights off. A crew left with nobody in it kept its ground,
    # kept its place on the board and kept sitting in the join list reading `0 riders`, and
    # with the default approval policy anybody who joined it waited forever for a leader who
    # did not exist. Only an admin could clear it, and nothing told them it was there.
    last_one = _active_members(db, m.clan_id) <= 1 and m.status == "active"
    m.left_at = utcnow()
    if last_one:
        clan = db.get(Clan, m.clan_id)
        if clan and clan.disbanded_at is None:
            # Pending rows too. Retiring the crew and leaving a request open left that rider
            # looking at a card for a crew that does not exist, waiting on a leader who is
            # gone, with no exit anything pointed at.
            # Everybody else, said explicitly. The session does not autoflush, so the
            # leaver's own row still reads as present here and was being marked `disbanded`
            # alongside the rest -- which is the flag `last_fold` turns into "your crew was
            # folded", shown to the one person who knows perfectly well that they left.
            for other in db.query(ClanMember).filter(
                    ClanMember.clan_id == m.clan_id,
                    ClanMember.store_id != m.store_id,
                    ClanMember.left_at.is_(None)).all():
                other.left_at = utcnow()
                other.status = "disbanded"
            _retire(clan)
    db.commit()


def decide(db, actor: str, clan_id: str, store_id: str, accept: bool) -> None:
    """Leader or officer accepts or declines a pending request."""
    _require_power(db, actor, clan_id)
    m = (db.query(ClanMember)
         .filter(ClanMember.clan_id == clan_id, ClanMember.store_id == store_id,
                 ClanMember.status == "pending", ClanMember.left_at.is_(None)).first())
    if m is None and accept:
        # Reconsidering. A decline was final for both sides: the rider could not re-ask
        # without a fresh request and the leader could not take it back at all.
        m = (db.query(ClanMember)
             .filter(ClanMember.clan_id == clan_id, ClanMember.store_id == store_id,
                     ClanMember.status.in_(("declined", "declined_seen"))).first())
        if m is not None:
            # The same two gates the front door has. Without them a leader's Accept put a
            # rider into a second crew while they were still in a first -- two active rows,
            # both rosters listing them, one Leave silently dropping them into the other --
            # and walked them past a cooldown their own join had just been refused for.
            if membership(db, store_id):
                raise CrewError("already_in_crew", "They are in another crew.")
            if cooldown_until(db, store_id):
                raise CrewError("cooldown", "They are still cooling off from their last crew.")
            m.left_at = None
            m.status = "pending"
    if m is None:
        raise CrewError("no_request", "No pending request from that rider.")
    if accept and is_full(db, clan_id):
        # The cap is enforced when a rider walks in and was not when a leader waved one in,
        # so a crew could sit over the line while its own row said "Full".
        raise CrewError("crew_full", "That crew is full.")
    if accept:
        m.status = "active"
    else:
        # "declined", not a bare left_at: a withdrawn request and a refused one looked
        # identical afterwards, so the rider could not be told which had happened.
        m.status = "declined"
        m.left_at = utcnow()
    db.commit()


def last_fold(db, store_id: str) -> dict | None:
    """A crew that folded under this rider and has not been mentioned to them yet.

    Same channel as last_answer and the same rule: said once, and only while it is news --
    but "said" means SHOWN, which this function cannot know. See `mark_notice_seen`.
    """
    m = (db.query(ClanMember)
         .filter(ClanMember.store_id == store_id, ClanMember.status == "disbanded",
                 ClanMember.left_at.isnot(None),
                 ClanMember.left_at >= utcnow() - timedelta(days=7))
         .order_by(ClanMember.left_at.desc()).first())
    if m is None:
        return None
    clan = db.get(Clan, m.clan_id)
    if clan is None:
        return None
    tag = f" (folded {clan.clan_id[:6]})"
    name = clan.name[:-len(tag)] if clan.name.endswith(tag) else clan.name
    return {"crew": name}


def is_full(db, clan_id: str) -> bool:
    """Whether the cap is reached. Public because the panel has to ask it too:
    a row that offers to let somebody in is making a claim about this."""
    from services import settings
    try:
        cap = int(settings.get_crews(db)["max_members"])
    except (KeyError, TypeError, ValueError):
        return False
    return bool(cap) and _active_members(db, clan_id) >= cap


def last_answer(db, store_id: str) -> dict | None:
    """A decision this rider has not been shown yet, if there is one.

    This used to clear the flag as it read it, and a GET that mutates is only safe when
    exactly one caller ever makes it. The panel makes three per load -- `primeDock`, `show`
    and the territory pass, two of them 3ms apart -- so whichever landed first spent the
    news and the one whose response actually reaches `render()` saw nothing. A reviewer
    pressed "No" on a request, loaded the page as the applicant, polled for three seconds
    and never saw the card; two curl fetches back to back returned the crew name and then
    an empty object. All three notices -- turned down, removed, folded under you -- are the
    only moments in this feature that carry any weight, and all three had been written,
    translated into nineteen languages, and were unreachable.

    So reading is a peek now, and `mark_notice_seen` is what spends it -- called by the
    client once the card is on screen, which is the only place that knows it was shown.
    """
    m = (db.query(ClanMember)
         .filter(ClanMember.store_id == store_id, ClanMember.status == "declined",
                 ClanMember.left_at.isnot(None),
                 # A week. Without a bound, a decline nobody read in January turned up in
                 # March beside a cooldown from a different crew, and one of the two cards
                 # on screen was then false.
                 ClanMember.left_at >= utcnow() - timedelta(days=7))
         .order_by(ClanMember.left_at.desc()).first())
    if m is None:
        return None
    clan = db.get(Clan, m.clan_id)
    # Nothing to say if the crew folded in the meantime.
    return {"crew": clan.name} if clan and clan.disbanded_at is None else None


def set_role(db, actor: str, clan_id: str, store_id: str, role: str) -> None:
    me = _require_power(db, actor, clan_id)
    if role not in ("officer", "member", "leader"):
        raise CrewError("bad_role", "Unknown role.")
    if role == "leader" and me.role != "leader":
        raise CrewError("not_leader", "Only the leader can hand over leadership.")
    # Found while checking something else: nothing stopped a leader setting their own role to
    # member, which leaves the crew with nobody in charge -- the exact state leave() and
    # disband() are written to prevent. Handing over is role="leader" on somebody else.
    if store_id == actor and me.role == "leader" and role != "leader":
        raise CrewError("promote_first", "Hand the crew to somebody else first.")
    # `status == "active"` matters as much as the rest: a rider who has only asked to join
    # matches on clan, store_id and left_at, so a leader could hand the crew to somebody who
    # had not joined it. That rider then holds the title with none of the powers --
    # `_require_power` needs an active row -- the old leader is an officer, `claim` refuses
    # for ninety days because the crew looks led, and nobody can disband. One call, from the
    # leader's own panel, on a handle that panel published.
    m = (db.query(ClanMember)
         .filter(ClanMember.clan_id == clan_id, ClanMember.store_id == store_id,
                 ClanMember.status == "active", ClanMember.left_at.is_(None)).first())
    if m is None:
        raise CrewError("not_member", "Not a member of this crew.")
    # The same guard `remove()` carries, which this did not: without it an officer could set
    # the leader's role to member and walk away from a crew with nobody in charge -- the
    # leaderless shell leave() and disband() are written to prevent, reachable by anybody the
    # leader had handed a badge to. An officer may still stand down themselves.
    if m.role == "leader" and me.role != "leader":
        raise CrewError("forbidden", "Only the leader can do that.")
    if m.role == "officer" and me.role != "leader" and store_id != actor:
        raise CrewError("forbidden", "Only the leader can do that.")
    if role == "leader":
        me.role = "officer"
    m.role = role
    db.commit()


def remove(db, actor: str, clan_id: str, store_id: str) -> None:
    """A leader or officer takes somebody off the roster.

    The only leader power that was missing, and its absence made membership write-once: an
    open crew with a cap could be squatted for ever and the fix was to email an admin.
    """
    me = _require_power(db, actor, clan_id)
    if store_id == actor:
        raise CrewError("not_yourself", "Use Leave crew for that.")
    m = (db.query(ClanMember)
         .filter(ClanMember.clan_id == clan_id, ClanMember.store_id == store_id,
                 ClanMember.left_at.is_(None)).first())
    if m is None:
        raise CrewError("not_member", "Not a member of this crew.")
    if m.role == "leader" or (m.role == "officer" and me.role != "leader"):
        raise CrewError("forbidden", "You cannot remove them.")
    if m.status != "active":
        # They were never on the crew. Turning a waiting rider down is decide(accept=False),
        # which tells them the truth; this would have told them they were taken off a crew
        # they had not joined.
        raise CrewError("not_member", "They have not joined yet. Decline the request instead.")
    # No last_member guard: _require_power already needs an active actor and removing
    # yourself is refused above, so an active target can never be the only one left.
    # "removed", not "active": the cooldown is for people who choose to walk out, and this
    # was not their choice.
    m.status = "removed"
    m.left_at = utcnow()
    db.commit()


def last_removal(db, store_id: str) -> dict | None:
    """Told once, like a decline and like a crew folding under you -- and, like both of
    them, told once means told once it has been SHOWN. See `mark_notice_seen`."""
    m = (db.query(ClanMember)
         .filter(ClanMember.store_id == store_id, ClanMember.status == "removed",
                 ClanMember.left_at.isnot(None),
                 ClanMember.left_at >= utcnow() - timedelta(days=7))
         .order_by(ClanMember.left_at.desc()).first())
    if m is None:
        return None
    clan = db.get(Clan, m.clan_id)
    return {"crew": clan.name} if clan and clan.disbanded_at is None else None


# The three one-shot notices, by the status they are waiting in and the status they move to
# once the rider has actually been shown them.
NOTICE_STATUS = {
    "declined": ("declined", "declined_seen"),
    "removed": ("removed", "removed_seen"),
    "folded": ("disbanded", "disbanded_seen"),
}


def mark_notice_seen(db, store_id: str, kind: str) -> bool:
    """Spend one notice, now that the panel has drawn it.

    Separated from the readers above so that reading is idempotent: any number of concurrent
    GETs report the same news, and the single client that renders it is what retires it.
    Idempotent itself -- a second call finds nothing in the waiting status and says so -- so
    a double render or a retried request costs nothing.
    """
    pair = NOTICE_STATUS.get(kind)
    if not pair:
        return False
    waiting, seen = pair
    m = (db.query(ClanMember)
         .filter(ClanMember.store_id == store_id, ClanMember.status == waiting,
                 ClanMember.left_at.isnot(None),
                 ClanMember.left_at >= utcnow() - timedelta(days=7))
         .order_by(ClanMember.left_at.desc()).first())
    if m is None:
        return False
    m.status = seen
    db.commit()
    return True


def claim_eligible(db, store_id: str, clan_id: str) -> bool:
    """Would claim_leadership succeed for this rider right now?

    The panel used to show the take-over button on `nobody is in charge` alone, to every
    member including ones whose own request has not been approved. For most of them the
    button could only ever fail, and a pending rider in a leaderless crew is in the one trap
    it exists to open: nobody is left who can approve them.
    """
    try:
        _eligible_for(db, clan_id, store_id)
        return True
    except CrewError:
        return False


def _eligible_for(db, clan_id: str, store_id: str):
    leader = (db.query(ClanMember)
              .filter(ClanMember.clan_id == clan_id, ClanMember.role == "leader",
                      ClanMember.left_at.is_(None)).first())
    if leader is not None:
        idle_since = leader.last_seen or leader.joined_at
        if idle_since and (utcnow() - idle_since) < timedelta(days=IDLE_LEADER_DAYS):
            raise CrewError("leader_active", "The leader is still active.")
    q = (db.query(ClanMember)
         .filter(ClanMember.clan_id == clan_id, ClanMember.status == "active",
                 ClanMember.left_at.is_(None)))
    if leader is not None:
        q = q.filter(ClanMember.store_id != leader.store_id)
    eligible = q.order_by(ClanMember.joined_at.asc()).first()
    if eligible is None or eligible.store_id != store_id:
        raise CrewError("not_eligible", "The longest-serving active member takes over.")
    return leader, eligible


def claim_leadership(db, store_id: str, clan_id: str) -> None:
    """The backstop for a crew whose leader has gone quiet.

    Officers cover the ordinary case; this covers the crew where everyone with power has
    stopped riding. The longest-serving active member may take over once the leader has been
    idle for IDLE_LEADER_DAYS, with no admin involvement.
    """
    leader = (db.query(ClanMember)
              .filter(ClanMember.clan_id == clan_id, ClanMember.role == "leader",
                      ClanMember.left_at.is_(None)).first())
    # A leader may walk out the moment there is an officer, which leaves the crew with no
    # leader row at all. That used to be permanent: the officer could not disband (not the
    # leader), could not be promoted (only a leader may promote), and this raised "no leader
    # to replace" at the one person trying to replace them. A crew with nobody in charge is
    # exactly the case this function exists for, so there is no waiting period.
    if leader is not None:
        idle_since = leader.last_seen or leader.joined_at
        if idle_since and (utcnow() - idle_since) < timedelta(days=IDLE_LEADER_DAYS):
            raise CrewError("leader_active", "The leader is still active.")
    # the leader is excluded explicitly: they founded the crew, so they are always its
    # longest-serving member, and ordering by join date without this clause re-elects the
    # very person who stopped riding
    q = (db.query(ClanMember)
         .filter(ClanMember.clan_id == clan_id, ClanMember.status == "active",
                 ClanMember.left_at.is_(None)))
    if leader is not None:
        q = q.filter(ClanMember.store_id != leader.store_id)
    eligible = q.order_by(ClanMember.joined_at.asc()).first()
    if eligible is None or eligible.store_id != store_id:
        raise CrewError("not_eligible", "The longest-serving active member takes over.")
    if leader is not None:          # there may be nobody to stand down
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
    for mm in db.query(ClanMember).filter(ClanMember.clan_id == clan_id,
                                          ClanMember.left_at.is_(None)).all():
        mm.left_at = utcnow()
        # Nobody here walked out. The cooldown exists to stop crew-hopping, and having your
        # crew folded underneath you is not hopping: the members took no action at all, and
        # were being benched a week and shown "You just walked out of one".
        # The one who pressed the button is marked as having seen it, because they have: they
        # read a confirm dialog describing exactly this. Marking it by role instead meant an
        # admin-folded crew's leader -- who pressed nothing -- was told nothing either.
        mm.status = "disbanded_seen" if mm.store_id == actor else "disbanded"
    # The admin's disband clears these and the leader's did not, so the same action left two
    # different maps standing for up to a rebuild interval.
    db.query(ClanCell).filter(ClanCell.clan_id == clan_id).delete()
    _retire(clan)
    db.commit()


def _retire(clan) -> None:
    """Fold a crew and give its name back.

    `name` and `slug` are UNIQUE for the life of the table, so a disbanded crew went on
    holding both forever and re-founding under the same name was a 500. The colours are
    freed the moment a crew folds; the name works the same way now, by moving the dead
    crew's out of the way rather than by keeping a graveyard of reserved words.
    """
    clan.disbanded_at = utcnow()
    tag = clan.clan_id[:6]
    if not clan.name.endswith(")"):
        clan.name = f"{clan.name} (folded {tag})"[:60]
    clan.slug = f"{clan.slug}-x{tag}"[:80]


def unretire(db, clan) -> str | None:
    """Undo _retire. Returns an error sentence, or None when it worked.

    Restore has to be a real undo: a crew brought back under its retirement tag, with the
    members still marked gone, is the leaderless shell the whole of leave() exists to stop.
    """
    tag = f" (folded {clan.clan_id[:6]})"
    name = clan.name[:-len(tag)] if clan.name.endswith(tag) else clan.name
    if name_clash(db, name, except_id=clan.clan_id):
        return f"{name} has been taken since. Rename that crew first."
    suffix = f"-x{clan.clan_id[:6]}"
    slug = clan.slug[:-len(suffix)] if clan.slug.endswith(suffix) else clan.slug
    if db.query(Clan).filter(Clan.slug == slug, Clan.clan_id != clan.clan_id).first():
        slug = free_slug(db, name)
    # Who is actually coming back. Counted before anything is written, because a crew with
    # nobody in it must not be un-retired at all: it returns to the public join list with
    # `members: 0` and no leader, and under the default approval policy anybody who joins
    # waits for an approver who does not exist. That is the shell `leave()` abolishes,
    # rebuilt by the admin screen and reported as a success.
    coming = [m for m in db.query(ClanMember).filter(
        ClanMember.clan_id == clan.clan_id,
        ClanMember.status.in_(("disbanded", "disbanded_seen"))).all()
        if not membership(db, m.store_id)]
    if not coming:
        return (f"Every rider who was in {name} has joined another crew since. "
                f"Restoring it would put an empty crew back in the join list.")
    clan.name, clan.slug, clan.disbanded_at = name, slug, None
    back = 0
    # `disbanded_seen` too: last_fold rewrites the mark the moment the rider reads their
    # notification, so anybody who had looked at their own fold card was being dropped from
    # the restored crew under a flash saying "restored with its riders".
    for m in db.query(ClanMember).filter(
            ClanMember.clan_id == clan.clan_id,
            ClanMember.status.in_(("disbanded", "disbanded_seen"))).all():
        # Not somebody who has joined somewhere else since. Reviving them regardless put a
        # rider in two crews at once, with two Leaves and a week's cooldown as the only way
        # out of a state they had no part in creating.
        if membership(db, m.store_id):
            continue
        m.status = "active"
        m.left_at = None
        back += 1
    # The session is built with `autoflush=False`, so without this both queries below run
    # against the pre-loop rows: the restored members are invisible, `first` comes back None,
    # and the whole promotion block quietly does nothing. It only ever looked right because
    # the case anybody tested restored the original leader, who needs no promoting.
    db.flush()
    if back and not db.query(ClanMember).filter(
            ClanMember.clan_id == clan.clan_id, ClanMember.role == "leader",
            ClanMember.left_at.is_(None)).first():
        # somebody has to be in charge, or this is the shell again in a different shape
        first = (db.query(ClanMember)
                 .filter(ClanMember.clan_id == clan.clan_id, ClanMember.status == "active",
                         ClanMember.left_at.is_(None))
                 .order_by(ClanMember.joined_at.asc()).first())
        if first:
            first.role = "leader"
    return None


def touch(db, store_id: str) -> None:
    """Mark a member as seen, which is what the idle-leader handover measures against.

    Hourly at most. The rule it feeds is "has this leader been quiet for ninety days", so a
    timestamp to the hour is ample and it saves a write and a commit on every panel load.
    """
    m = membership(db, store_id)
    if m is None:
        return
    now = utcnow()
    if m.last_seen is None or (now - m.last_seen) > timedelta(hours=1):
        m.last_seen = now
        db.commit()


def _active_members(db, clan_id: str) -> int:
    return (db.query(ClanMember)
            .filter(ClanMember.clan_id == clan_id, ClanMember.status == "active",
                    ClanMember.left_at.is_(None)).count())


def _officers(db, clan_id: str) -> int:
    # Active ones. This counts towards the guard that lets a leader leave, and a rider who has
    # only asked to join can hold the role without being able to use it -- so a crew could be
    # left in the hands of somebody with no powers at all.
    return (db.query(ClanMember)
            .filter(ClanMember.clan_id == clan_id, ClanMember.role == "officer",
                    ClanMember.status == "active",
                    ClanMember.left_at.is_(None)).count())


def _require_power(db, store_id: str, clan_id: str) -> ClanMember:
    m = (db.query(ClanMember)
         .filter(ClanMember.clan_id == clan_id, ClanMember.store_id == store_id,
                 ClanMember.status == "active", ClanMember.left_at.is_(None)).first())
    if m is None or m.role not in ("leader", "officer"):
        raise CrewError("forbidden", "Only a leader or officer can do that.")
    return m


def champions(db, window_days: int | None = None) -> dict:
    """Crew champion of the day / week / month, for the card beside the rider one.

    What this measures, said plainly because it is a choice and not an obvious one: kilometres
    ridden FOR a crew inside each window. Territory itself has no history -- `terr_best_tiles`
    and friends are current state, rewritten wholesale by each rebuild -- so "the crew that led
    on squares last Tuesday" is not a question this database can answer. Distance ridden for
    the crew is the thing that produces squares, it is stored per trip with a timestamp, and it
    is exactly the shape the rider champions already use, so the two cards mean comparable
    things.

    The crew's standing (`score`) rides along for the card to show: that IS the weighted
    patch/squares/area figure, and it is current state by definition.
    """
    from datetime import timedelta
    import sqlalchemy as sa
    from models import Trip

    now = utcnow()
    windows = {"day": now - timedelta(days=1),
               "week": now - timedelta(days=7),
               "month": now - timedelta(days=30)}
    out: dict = {}
    for key, since in windows.items():
        row = (db.query(Trip.clan_id, sa.func.sum(Trip.distance_km).label("km"))
               .filter(Trip.clan_id.isnot(None),
                       Trip.validation_status == "validated",
                       Trip.start_utc.isnot(None), Trip.start_utc >= since)
               .group_by(Trip.clan_id)
               .order_by(sa.desc("km")).first())
        if row is None or not row[1]:
            out[key] = None
            continue
        clan = db.get(Clan, row[0])
        if clan is None or clan.disbanded_at is not None:
            out[key] = None
            continue
        out[key] = {"slug": clan.slug, "name": clan.name,
                    "colour": clan.colour, "pattern": clan.pattern,
                    "km": round(float(row[1]), 1),
                    "tiles": clan.terr_best_tiles or 0,
                    "km2": round(clan.terr_best_km2 or 0.0, 1),
                    "score": round(standing_score(clan), 1)}
    return out


# Biggest patch is king, and the rest of what a crew holds is worth something.
#
# Erwin's call, against three options: a crew spread over two cities and a crew near the
# equator, where one square covers four times the ground, both get a real route up the board
# without overturning it. Measured on the demo board before it was adopted: five crews of
# fifteen move and none by more than two places -- the two-city crews (52 squares in a patch of
# 30, 48 in a patch of 28) climb past the one-city crew holding the same patch and nothing
# else, and the equator crew climbs on area alone.
#
# The board itself still RANKS on squares -- also his call, because one number has to reconcile
# across the board, the join list, the map, the crew card and the public page. This is the
# champions card's own figure.
SCORE_W_PATCH, SCORE_W_SQUARES, SCORE_W_KM2 = 1.0, 0.25, 0.05


def standing_score(clan) -> float:
    return (SCORE_W_PATCH * (clan.terr_best_tiles or 0)
            + SCORE_W_SQUARES * (clan.terr_tiles or 0)
            + SCORE_W_KM2 * (clan.terr_km2 or 0.0))
