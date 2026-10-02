"""Territory: turning rides into ground held.

The shape of the thing
----------------------
Every validated ride carries the crew its rider was in when it was uploaded (`Trip.clan_id`,
stamped at ingest and never rewritten — switching crews must not redraw months of map). A
ride's distance is spread across the Mercator tiles it passed through, summed per crew over a
rolling window, and the crew with the most kilometres in a tile holds it.

Then two rules shape the result into something that reads as territory rather than confetti:

* **A crew must plant a 2x2 block before it holds anything.** One ride down one street would
  otherwise paint a tile in a crew's colour on the other side of the world, and with 96
  colour-and-pattern combinations in play, scattered single tiles are unreadable. Four tiles
  won together is a deliberate claim.
* **From a seed, territory grows by contact.** Tiles touching a seeded block edge-to-edge join
  it. Tiles that touch nothing are dropped entirely — not greyed out, not shown faintly.

So a crew can hold several regions, each one with its own 2x2 seed somewhere inside it, and a
rider passing through once leaves no mark at all.

Keeping the server out of it
----------------------------
This runs on a schedule, not per request. It writes `ClanCell` rows for the admin and the
ranking, and one compact JSON file per zoom for the map — flat integer triples, not GeoJSON,
because the client can turn a tile index into a polygon with two multiplications and the
server would otherwise ship a few hundred kilobytes of coordinate text to say the same thing.
Fills, region outlines and emblem placement are all derived client-side from that file.

The one piece of geometry done here is the emblem slot, because it is the only part that is
not cheap: the largest square that fits inside a region, chosen nearest the region's middle.
It is stable between rebuilds, so computing it once a night and caching it is free, and it is
what makes a crew's emblem land square in a 3x2 block instead of stretched across it.
"""
from __future__ import annotations

import gzip
import json
import math
from datetime import timedelta
from pathlib import Path

import sqlalchemy as sa

import config
from models import Clan, ClanCell, ClanMember, Trip, TripTrack, utcnow
from services import tiles as T

WINDOW_DAYS = 90           # the rolling window: territory is what you ride, not what you rode
SEED = 2                   # a crew must hold a SEED x SEED block to claim anything
# A visit is a fraction of a crossing, not a fixed distance. As a flat 0.3 km it was 12% of
# a crossing at the equator and 60% at Longyearbyen, which is the same latitude bias the lead
# floor was fixed for, pointing the other way.
MIN_TILE_EDGE = 0.12       # fraction of a tile's edge that counts as having been there

# A lead is measured against the tile's own size rather than a flat distance. A flat 1 km
# bought four times as much ground at the equator as at Oslo, where tiles are a quarter the
# area, and the board was being sorted by latitude as much as by riding.
MIN_LEAD_EDGE = 0.42       # fraction of a tile's edge you must have ridden inside it

# Kilometres fade with age instead of sitting in a 90-day bucket at full value. Without this
# the rule stops rewarding riding the moment you lead: an entrenched tile holds a stock that a
# newcomer has to out-ride in total, which freezes every city core, while on empty ground one
# afternoon wins for three months and the 91st day deletes it all at once. With a half-life,
# today's ride always outweighs one from six weeks ago and ground recedes visibly instead of
# falling off a cliff.
HALF_LIFE_DAYS = 21.0

# What one rider can contribute to one tile in a week. A crew is people, not an odometer:
# without a cap the tile beside somebody's front door is unreachable by any number of other
# riders, and with it a tile is won by how many of you ride there.
#
# The week is a rolling seven days back from now, not the calendar week. On ISO weeks a rider
# could put the full cap in on Sunday and the full cap in again on Monday, so the real limit
# was twice the stated one for anybody who noticed.
RIDER_TILE_WEEK_CAP_KM = 6.0


def _out_path(zoom: int) -> Path:
    return config.DATA_DIR / f"territory-z{zoom}.json.gz"


# --- gathering ----------------------------------------------------------------------------

def _decode(points):
    from ingest.downsample import decode_track
    if not points:
        return []
    try:
        return [(r[1], r[2]) for r in decode_track(points)
                if r[1] is not None and r[2] is not None]
    except Exception:
        return []


def _trip_points(db, trip_uuid: str):
    tt = db.get(TripTrack, trip_uuid)
    return _decode(tt.points if tt else None)


def _tracks_for(db, uuids: list[str], stride: int = 400):
    """Tracks for a batch of trips, fetched in chunks instead of one query each.

    The per-trip `db.get` was 96% of the rebuild's queries: 168 of 175 at today's size, and
    16,800 of 16,808 at a hundred times that. The decode itself is irreducible; the round
    trips were not.
    """
    out = {}
    for i in range(0, len(uuids), stride):
        chunk = uuids[i:i + stride]
        for uuid_, points in db.query(TripTrack.trip_uuid, TripTrack.points).filter(
                TripTrack.trip_uuid.in_(chunk)).all():
            out[uuid_] = points
    return out


def _per_tile_km(points, zoom: int) -> dict[str, float]:
    """How far the ride actually went inside each tile.

    Each segment between two fixes is credited to the tile its midpoint falls in. Segments are
    tens of metres and tiles are kilometres across, so the error is one segment at each
    boundary — far below the noise in GPS distance itself.

    This replaces splitting the trip's distance evenly across every tile it touched, which had
    the property that the more ground a ride covered the less each tile was worth. A long ride
    joining two areas — the whole point of a road trip under a "biggest unbroken region" rule
    — scored a few hundred metres per tile and claimed none of them, while a short loop around
    one block scored kilometres. It rewarded exactly the wrong riding.
    """
    out: dict[str, float] = {}
    for (la1, lo1), (la2, lo2) in zip(points, points[1:]):
        mlat = (la1 + la2) / 2.0
        dx = (lo2 - lo1) * 111.32 * math.cos(math.radians(mlat))
        dy = (la2 - la1) * 111.32
        d = math.hypot(dx, dy)
        if d <= 0 or d > 50:            # a 50 km step between fixes is a teleport, not a ride
            continue
        tile = T.tile_of(mlat, (lo1 + lo2) / 2.0, zoom)
        if tile:
            out[tile] = out.get(tile, 0.0) + d
    return out


def accumulate(db, window_days: int = WINDOW_DAYS, zoom: int = T.DEFAULT_ZOOM) -> dict:
    """({tile: {clan_id: [km, {riders}]}}, {(tile, clan): recency}) over the window.

    Per-tile distance comes from the track, scaled so a trip's tiles sum to the odometer
    distance the ingest pipeline validated: the authority stays with the number that was
    checked against the GPS trace, while the shape comes from where the ride actually went.

    Two things then shape it into a score worth competing over. Distance is weighted by age on
    a HALF_LIFE_DAYS half-life, and each rider's contribution to each tile is capped per week.
    Both exist because the first version stopped rewarding riding the moment you were ahead.
    """
    since = utcnow() - timedelta(days=window_days)
    now = utcnow()
    rows = (db.query(Trip.trip_uuid, Trip.clan_id, Trip.rider_store_id, Trip.distance_km,
                     Trip.start_lat, Trip.start_lon, Trip.start_utc)
            .filter(Trip.validation_status == "validated",
                    Trip.clan_id.isnot(None),
                    Trip.start_utc >= since,
                    Trip.distance_km > 0)
            # Newest first, and explicitly ordered. The weekly cap keeps the kilometres it
            # meets first, and under decay those are worth more, so without an ORDER BY the
            # result depended on whatever order SQLite handed the rows back: the same data
            # could produce 4.9 or 6.0 weighted km for one rider-week and a contested tile
            # could change hands between two rebuilds with nobody riding anything.
            .order_by(Trip.start_utc.desc(), Trip.trip_uuid.desc())
            .all())

    # (rider, tile, week) -> km already counted, so one odometer cannot hold a tile
    tracks = _tracks_for(db, [r[0] for r in rows])
    spent: dict[tuple, float] = {}
    acc: dict[str, dict[str, list]] = {}
    # when each crew last rode each tile, which is how a level contest is decided
    recency: dict[tuple, float] = {}
    for trip_uuid, clan_id, store_id, km, slat, slon, started in rows:
        pts = _decode(tracks.get(trip_uuid))
        per_tile = _per_tile_km(pts, zoom) if len(pts) > 1 else {}
        if not per_tile:
            # No track, no ground. This used to fall back to dumping the whole odometer on
            # the start tile, which meant a ride with a broken GPS log earned a full week's
            # cap at home with no evidence of having gone anywhere, and was indistinguishable
            # from a real ride. Territory is a claim about where you went; a trip that cannot
            # say where it went does not get to make one. It still counts everywhere else.
            continue
        total = sum(per_tile.values())
        if total <= 0:
            continue
        scale = (km or 0.0) / total
        age_days = max(0.0, (now - started).total_seconds() / 86400.0) if started else 0.0
        weight = 0.5 ** (age_days / HALF_LIFE_DAYS)
        # Anchored to a fixed epoch, not to how old the trip is right now. Bucketing by age
        # meant a trip crossed into a new bucket as it aged, releasing kilometres the cap had
        # suppressed: a tile's score could rise 79% between two rebuilds with nobody riding,
        # and a rider acting on "4 km would take it" could find the holder had doubled
        # overnight. A score may only ever decay on its own.
        week = int(started.timestamp() // 604800) if started else 0
        for tile, d in per_tile.items():
            ridden = d * scale
            if ridden < min_visit_km(tile):
                continue                         # clipped the corner; not a visit
            key = (store_id, tile, week)
            room = RIDER_TILE_WEEK_CAP_KM - spent.get(key, 0.0)
            if room <= 0:
                continue
            counted = min(ridden, room)
            spent[key] = spent.get(key, 0.0) + counted
            e = acc.setdefault(tile, {}).setdefault(clan_id, [0.0, set()])
            e[0] += counted * weight
            e[1].add(store_id)
            seen = recency.get((tile, clan_id), 0.0)
            recency[(tile, clan_id)] = max(seen, -age_days)
    return acc, recency


def _tile_edge_km(tile: str) -> float:
    """How far it is across a tile on the ground, at its own latitude."""
    b = T.bounds(tile)
    if b is None:
        return 2.4
    west, south, east, north = b
    return ((east - west) / 360.0 * T.EARTH_C_KM
            * math.cos(math.radians((south + north) / 2.0)))


def min_lead_km(tile: str) -> float:
    """How far you must have ridden inside a tile before a lead counts.

    Scaled to the tile rather than fixed, so the same effort buys the same standing wherever
    you ride. At the default zoom a tile is about 1.2 km across at Oslo and 2.4 km at the
    equator, and a flat kilometre was twice as cheap on the equator in crossings and four
    times as cheap in area.

    No absolute clamp: one existed and it inverted the fix above about 70 degrees north, where
    tiles are small enough that the clamp bit and Longyearbyen ended up paying more per
    crossing than Oslo.
    """
    return _tile_edge_km(tile) * MIN_LEAD_EDGE


def min_visit_km(tile: str) -> float:
    """Below this, a ride only clipped the corner of a tile and did not visit it."""
    return _tile_edge_km(tile) * MIN_TILE_EDGE


def winners(acc: dict, previous: dict | None = None,
            skip: set | None = None,
            last_seen: dict | None = None) -> dict[str, tuple[str, float, int]]:
    """{tile: (clan_id, km, riders)} — most kilometres takes the tile.

    A lead under the tile's own floor takes nothing. Without that floor the cheapest way to hold ground
    was a fabricated 0.3 km "ride", which bought roughly thirty times more area per kilometre
    than actually riding — and the floor is what the spec said all along.

    Ties go to whoever rode it most recently, then to the incumbent. `previous` is the last
    round's holders, from `ClanCell`.

    `skip` is a set of (tile, clan_id) claims that have already been ruled out this rebuild —
    see the re-award loop in rebuild().
    """
    prev = previous or {}
    blocked = skip or set()
    last_seen = last_seen or {}
    out = {}
    for tile, per in acc.items():
        best = None
        floor = min_lead_km(tile)
        for clan_id, (km, riders) in per.items():
            if km < floor or (tile, clan_id) in blocked:
                continue
            # Ties go to whoever rode it most recently, then to the incumbent. Incumbency
            # alone made a level contest permanent: two crews riding a tile equally hard
            # converge on the same number and the holder keeps it forever, so the closest
            # rivalries were the only ones that could never resolve. Recency breaks that in
            # the direction the whole mode is about, which is riding.
            incumbent = prev.get(tile) == clan_id
            key = (round(km, 6), last_seen.get((tile, clan_id), 0.0),
                   1 if incumbent else 0)
            if best is None or key > best[0]:
                best = (key, clan_id, km, len(riders))
        if best:
            out[tile] = (best[1], best[2], best[3])
    return out


# --- shaping ------------------------------------------------------------------------------

def _xy(tile: str) -> tuple[int, int]:
    p = T.parse(tile)
    return (p[1], p[2])


def seeded(held: set[tuple[int, int]], seed: int = SEED) -> set[tuple[int, int]]:
    """The subset of a crew's tiles that is connected to at least one full SEED x SEED block.

    Everything else is dropped. This is the rule that turns a scattering of won tiles into
    territory somebody can point at.
    """
    if not held:
        return set()
    seeds = set()
    for (x, y) in held:
        block = [(x + dx, y + dy) for dx in range(seed) for dy in range(seed)]
        if all(b in held for b in block):
            seeds.update(block)
    if not seeds:
        return set()
    # grow outward from the seeds through edge-adjacent owned tiles
    out, stack = set(seeds), list(seeds)
    while stack:
        x, y = stack.pop()
        for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
            if (nx, ny) in held and (nx, ny) not in out:
                out.add((nx, ny))
                stack.append((nx, ny))
    return out


def fill_enclosed(held: set[tuple[int, int]], taken: set[tuple[int, int]]) -> set:
    """Add tiles this crew has completely surrounded and nobody else holds.

    A tile ringed on all four sides by one crew's ground, with nobody holding it, was being
    left blank, which on the map reads as a rendering fault rather than as a fact. It is also
    the wrong answer: riding all the way around something is a clearer claim to it than riding
    across it once.

    Found by flooding inward from outside the bounding box rather than by checking neighbours,
    so a hole several tiles wide fills as readily as a single one. `taken` is every tile held
    by any other crew, which is never swallowed: you can surround a rival, and they keep what
    they hold.
    """
    if not held:
        return set()
    xs = [p[0] for p in held]
    ys = [p[1] for p in held]
    x0, x1 = min(xs) - 1, max(xs) + 1
    y0, y1 = min(ys) - 1, max(ys) + 1
    outside = set()
    stack = [(x, y0) for x in range(x0, x1 + 1)] + [(x, y1) for x in range(x0, x1 + 1)] \
        + [(x0, y) for y in range(y0, y1 + 1)] + [(x1, y) for y in range(y0, y1 + 1)]
    stack = [p for p in stack if p not in held]
    outside.update(stack)
    while stack:
        x, y = stack.pop()
        for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
            if not (x0 <= nx <= x1 and y0 <= ny <= y1):
                continue
            if (nx, ny) in held or (nx, ny) in outside:
                continue
            outside.add((nx, ny))
            stack.append((nx, ny))
    gained = set()
    for x in range(x0 + 1, x1):
        for y in range(y0 + 1, y1):
            p = (x, y)
            if p in held or p in outside or p in taken:
                continue
            gained.add(p)
    return gained


def regions(held: set[tuple[int, int]]) -> list[set[tuple[int, int]]]:
    """Connected components, so each one can be outlined and given its own emblem."""
    seen, out = set(), []
    for start in held:
        if start in seen:
            continue
        comp, stack = {start}, [start]
        seen.add(start)
        while stack:
            x, y = stack.pop()
            for nb in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                if nb in held and nb not in seen:
                    seen.add(nb)
                    comp.add(nb)
                    stack.append(nb)
        out.append(comp)
    return out


def emblem_slot(comp: set[tuple[int, int]]) -> tuple[int, int, int]:
    """(x, y, size) of the largest square of tiles inside a region, nearest its middle.

    An emblem has to go somewhere square or it comes out stretched, which is the whole reason
    territory is drawn on Mercator tiles rather than the heatmap's degree cells. Where several
    squares of the same size fit — a 3x2 block holds two 2x2 squares — the one closest to the
    region's centre of mass wins, so the emblem reads as centred rather than shoved into a
    corner.
    """
    xs = [p[0] for p in comp]
    ys = [p[1] for p in comp]
    x0, y0 = min(xs), min(ys)
    w, h = max(xs) - x0 + 1, max(ys) - y0 + 1
    cx = sum(xs) / len(xs)
    cy = sum(ys) / len(ys)
    # dp[i][j] = side of the largest square whose bottom-right corner is (i, j)
    dp = [[0] * (h + 1) for _ in range(w + 1)]
    best = (0, None)
    for i in range(1, w + 1):
        for j in range(1, h + 1):
            if (x0 + i - 1, y0 + j - 1) not in comp:
                continue
            s = min(dp[i - 1][j], dp[i][j - 1], dp[i - 1][j - 1]) + 1
            dp[i][j] = s
            tx, ty = x0 + i - s, y0 + j - s           # top-left of this square
            d = (tx + s / 2 - cx) ** 2 + (ty + s / 2 - cy) ** 2
            if s > best[0] or (s == best[0] and best[1] and d < best[1][2]):
                best = (s, (tx, ty, d))
    if best[1] is None:
        return (min(xs), min(ys), 1)
    return (best[1][0], best[1][1], best[0])


# --- the rebuild --------------------------------------------------------------------------

def award(acc: dict, live: set, prev: dict, seed: int = SEED, rounds: int = 8,
          last_seen: dict | None = None) -> tuple[dict[str, set], dict, set]:
    """Decide who draws what, re-awarding tiles their winner cannot actually hold.

    The naive version — pick the km leader per tile, then drop whatever fails to seed — made
    spoiling the cheapest move in the game. A crew that could never draw a tile still took it
    off everyone else: one 10.1 km ride through the middle of a rival's 2x2 block punched a
    hole that unseeded all six of their tiles, and left nothing of its own behind. Riding to
    deny beat riding to hold.

    So a claim that cannot be drawn is withdrawn and the tile falls to the next crew in line,
    repeatedly, until the picture stops changing. A crew with no 2x2 anywhere simply never
    appears on the map, which is the intended rule — it just no longer takes the ground down
    with it.

    `live` is the set of crews that still exist; a disbanded crew's claims are withdrawn the
    same way rather than salting the ground for the rest of the window.
    """
    blocked: set = set()
    for _ in range(rounds):
        won = winners(acc, prev, skip=blocked, last_seen=last_seen)
        by_clan: dict[str, set] = {}
        for tile, (clan_id, _km, _r) in won.items():
            by_clan.setdefault(clan_id, set()).add(_xy(tile))
        z = _zoom_of(won)
        withdrawn = set()
        kept: dict[str, set] = {}
        for clan_id, pts in by_clan.items():
            drawable = seeded(pts, seed) if clan_id in live else set()
            if drawable:
                kept[clan_id] = drawable
            for (x, y) in pts - drawable:
                tile = f"{z}/{x}/{y}"
                # Only hand the tile on where somebody else is actually in line for it.
                # Withdrawing every undrawable claim instead cascades: a crew's claims on
                # tiles nobody else wants get withdrawn too, so the next round it holds even
                # less, and the whole map empties out. A claim nobody can inherit is simply
                # left standing and drawn by nobody, which costs no one anything.
                floor = min_lead_km(tile)
                rivals = [c for c in acc.get(tile, {})
                          if c != clan_id and (tile, c) not in blocked
                          and acc[tile][c][0] >= floor]
                if rivals:
                    withdrawn.add((tile, clan_id))
        if not withdrawn:
            return _close_holes(kept), won, blocked
        blocked |= withdrawn
    return _close_holes(kept), won, blocked


def _close_holes(kept: dict[str, set]) -> dict[str, set]:
    """Give each crew the gaps it has ridden all the way around.

    After seeding, never before: a hole must not be able to satisfy the 2x2 rule that decides
    whether a crew draws anything at all.

    Three things this has to get right, each of which it got wrong first:

    * Per connected region, not per crew. The flood is bounded by the box it runs in, and one
      crew with a rider in Oslo and a rider in Sydney is a box of thirty million cells, which
      is tens of seconds and gigabytes every rebuild. A region is a city.
    * It must not flood through somebody else's ground, or two crews can each claim the same
      enclosed tiles and the map draws them twice.
    * A crew may not gain more than it rode. Without that, a one-tile-wide ring around a city
      is about five times more ground per kilometre than filling the same square solid, and
      since the board ranks on the biggest unbroken region, the ring is not a side exploit but
      the dominant way to play. Capping the gain at the size of the ring itself leaves
      enclosure worth doing and stops it being the only thing worth doing.
    """
    everyone = set()
    for pts in kept.values():
        everyone |= pts
    claimed = set()
    out = {}
    for clan_id in sorted(kept):
        pts = kept[clan_id]
        gained: set = set()
        for comp in regions(pts):
            blocked = (everyone - comp) | claimed
            got = fill_enclosed(comp, blocked)
            if len(got) > len(comp):
                continue                      # a ring cannot swallow more than it rode
            gained |= got
        claimed |= gained
        out[clan_id] = pts | gained
    return out


def _pressure(acc: dict, tile: str, holder: str, held_km: float,
              blocked: set | None = None) -> tuple[int, int]:
    """(band, a number) for a held tile.

    The band is what the map paints and the number is what a rider can act on. Saying "about
    to flip" and stopping is the shape of a warning without the content of one: nobody can
    tell whether that means two more kilometres this week or four more people.

      0 nobody near it        the number is how far clear the holder is, in tenths of a km
      1 somebody is riding it      ... the number is what the rival still needs
      2 about to flip              ... same
      3 fading                     near the floor with nobody else wanting it; the number is
                                   days until it goes, which is what a rider can plan around
      4 ringed                     held because the crew rode all the way around it

    Rivals under the tile's own floor, and rivals whose claim was withdrawn for failing to
    seed, are not counted: a warning that fires on somebody who structurally cannot take the
    tile is a warning people learn to ignore.
    """
    if held_km <= 0:
        # Gained by enclosure: nobody rode it, so none of the questions below apply. This used
        # to fall through to "fading, 0.0 km clear", and to "about to flip" whenever any rival
        # had kilometres there, both false on ground that cannot be lost until the ring breaks.
        return 4, 0

    floor = min_lead_km(tile)
    out = blocked or set()
    rivals = [v[0] for c, v in acc.get(tile, {}).items()
              if c != holder and v[0] >= floor and (tile, c) not in out]

    if not rivals:
        slack = held_km - floor
        if slack > floor * 0.35:
            return 0, max(0, int(round(slack * 10)))
        # Days left, not slack. The slack on a fading tile is tiny by definition, so printing
        # it could only ever say "0.0 km", which is a label rather than something to plan
        # around. At a HALF_LIFE_DAYS half-life this is exact.
        days = HALF_LIFE_DAYS * math.log2(held_km / floor) if held_km > floor else 0.0
        return 3, max(0, int(round(days)))

    best = max(rivals)
    need = max(0.0, held_km - best)          # what the rival still has to find
    ratio = best / held_km
    band = 2 if ratio >= 0.85 else 1 if ratio >= 0.5 else 0
    return band, int(round(need * 10))


def targets_for(acc: dict, kept: dict, clan_id: str, won: dict, zoom: int,
                limit: int = 10) -> list[dict]:
    """The ground this crew could take next, and what taking it would do.

    This is the one question the mode has to answer and did not. A rider could see that a tile
    was contested only by finding it, and the tiles worth riding are exactly the ones somebody
    else holds, which look like any other street from the saddle.

    The list is every square touching the crew's own ground, or that it already has kilometres
    in, that it does not hold, with how far short it is. Three things make a square worth more
    than its distance says:

    * `joins` welds two of the crew's separate patches into one. The board ranks on the biggest
      single patch, so the roadtrip between two towns beats widening either of them.
    * `grows` extends the biggest patch the crew already has, which is the number it is ranked
      on. Extending a smaller patch is real ground but moves nothing on the board.
    * `blocked` means the crew has already ridden enough there and still does not hold it,
      because a square only counts as part of a 2x2. That is the rule people trip over, and
      the fix is a neighbour, not more laps.

    `kept` is the whole board, crew by crew, not just this crew's share: who holds a square is
    not the same question as who leads it on kilometres. A crew with no 2x2 anywhere can lead
    ten tiles and hold none of them, and naming it as the holder is simply wrong.
    """
    held = set(kept.get(clan_id) or ())
    holder_of = {}
    for other, pts in kept.items():
        for xy in pts:
            holder_of[xy] = other
    cand = set()
    for (x, y) in held:                       # everything touching what we hold
        for nb in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
            if nb not in held:
                cand.add(nb)
    for tile, per in acc.items():             # plus anywhere we already rode
        if clan_id not in per:
            continue
        pt = T.parse(tile)
        if pt and (pt[1], pt[2]) not in held:
            cand.add((pt[1], pt[2]))

    # which patch each held tile belongs to, and which patch is the one being ranked
    patches = regions(held)
    patch_of = {}
    for i, comp in enumerate(patches):
        for xy in comp:
            patch_of[xy] = i
    biggest = max(range(len(patches)), key=lambda i: len(patches[i])) if patches else None

    out = []
    for (x, y) in cand:
        tile = f"{zoom}/{x}/{y}"
        if holder_of.get((x, y)) == clan_id:   # no point sending anybody where they already won
            continue
        w = won.get(tile)
        mine = acc.get(tile, {}).get(clan_id, [0.0, set()])[0]
        floor = min_lead_km(tile)
        # rounded to the figure the reader is shown, so a badge can never disagree with the
        # number next to it: 0.04 km short printed as "0.0 km" and then said to need more
        # riding is a row that argues with itself
        need = round(max(0.0, max(floor, w[1] if w else 0.0) - mine), 1)
        touching = {patch_of[nb] for nb in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1))
                    if nb in patch_of}
        out.append({"x": x, "y": y, "need": need,
                    "held_by": holder_of.get((x, y)),
                    "joins": len(touching) > 1,
                    "grows": biggest is not None and biggest in touching,
                    # enough kilometres already in it, and still not ours: it is short a
                    # neighbour, not short a ride
                    "blocked": need <= 0.0 and mine > 0.0})
    # Weld two patches first. Then taking ground off somebody in a way that grows the patch
    # being ranked, because that moves the board twice. Then growing it. Then anything that
    # costs a rival something. Then the rest, nearest first.
    def rank(t):
        if t["joins"]:
            return 0
        if t["grows"] and t["held_by"]:
            return 1
        if t["grows"]:
            return 2
        return 3 if t["held_by"] else 4

    out.sort(key=lambda t: (rank(t), t["need"], t["x"], t["y"]))
    return out[:limit]


def _zoom_of(won: dict) -> int:
    for tile in won:
        p = T.parse(tile)
        if p:
            return p[0]
    return T.DEFAULT_ZOOM


def rebuild(db, window_days: int = WINDOW_DAYS, zoom: int = T.DEFAULT_ZOOM,
            seed: int = SEED) -> dict:
    """Recompute every crew's territory and write both outputs. Returns a short report."""
    acc, recency = accumulate(db, window_days, zoom)
    prev = {c.tile: c.clan_id for c in db.query(ClanCell).all()}
    first_led = {(c.tile, c.clan_id): c.first_led for c in db.query(ClanCell).all()}

    clans = {c.clan_id: c for c in db.query(Clan).filter(Clan.disbanded_at.is_(None)).all()}
    member_counts = dict(
        db.query(ClanMember.clan_id, sa.func.count(ClanMember.store_id))
        .filter(ClanMember.status == "active", ClanMember.left_at.is_(None))
        .group_by(ClanMember.clan_id).all())

    kept, won, withdrawn_all = award(acc, set(clans), prev, seed, last_seen=recency)

    # --- ClanCell rows: the admin view and the ranking read these
    db.query(ClanCell).delete()
    for c in clans.values():          # cleared first, so a crew that lost everything shows 0
        c.terr_km2 = c.terr_best_km2 = 0.0
        c.terr_tiles = c.terr_regions = 0
        c.targets_json = None
    now = utcnow()
    order = sorted(kept.keys())
    payload_crews = []
    cells_flat: list[int] = []
    regions_out = []
    for idx, clan_id in enumerate(order):
        c = clans[clan_id]
        km2 = 0.0
        pts = kept[clan_id]
        for (x, y) in sorted(pts):
            tile = f"{zoom}/{x}/{y}"
            # a tile gained by enclosure has no winner entry: nobody rode it, it is held
            # because the crew rode all the way around it
            w = won.get(tile)
            km, riders = (w[1], w[2]) if w else (0.0, 0)
            db.add(ClanCell(tile=tile, clan_id=clan_id, km=round(km, 3), riders=riders,
                            first_led=first_led.get((tile, clan_id)) or now))
            km2 += T.area_km2(tile)
            band, need = _pressure(acc, tile, clan_id, km, withdrawn_all)
            cells_flat.extend((idx, x, y, band, need))
        comps = regions(pts)
        best_km2 = 0.0
        for comp in comps:
            ex, ey, es = emblem_slot(comp)
            regions_out.append({"c": idx, "e": [ex, ey, es], "n": len(comp)})
            # the headline number: one solid block beats the same area in scattered pockets
            best_km2 = max(best_km2, sum(T.area_km2(f"{zoom}/{x}/{y}") for (x, y) in comp))
        c.terr_km2 = round(km2, 1)
        c.terr_best_km2 = round(best_km2, 1)
        c.terr_tiles = len(pts)
        c.terr_regions = len(comps)
        payload_crews.append({
            "id": clan_id, "name": c.name, "slug": c.slug, "colour": c.colour,
            "pattern": c.pattern, "tiles": len(pts), "km2": round(km2, 1),
            "best_km2": round(best_km2, 1), "regions": len(comps),
            "members": member_counts.get(clan_id, 0),
            "emblem": f"/api/v1/crews/{c.slug}/emblem",
        })
    # Where to ride next, for every crew and not only the ones already holding ground: a crew
    # with nothing was the one being told nothing, which is backwards.
    for clan_id, c in clans.items():
        try:
            c.targets_json = json.dumps(targets_for(acc, kept, clan_id, won, zoom))
        except Exception:
            c.targets_json = None
    db.commit()

    payload = {
        "z": zoom, "generated": now.isoformat() + "Z", "window_days": window_days,
        "seed": seed,
        # ranked by the biggest unbroken stretch a crew holds, not the total: one solid
        # block is a harder thing to own than the same area scattered across a country
        "crews": sorted(payload_crews, key=lambda c: (-c["best_km2"], -c["km2"])),
        # [crewIndex, x, y, band, tenths-of-a-km, ...]
        # band: 0 safe, 1 somebody is riding it, 2 about to flip, 3 fading for lack of riding
        # the number: what a rival still needs, or for 0 and 3 how much slack the holder has
        "cells": cells_flat,
        "regions": regions_out,
    }
    # `crews` is sorted for display but `cells` indexes the unsorted order, so the indices are
    # remapped rather than leaving the client to join on two different orderings
    remap = {c["id"]: i for i, c in enumerate(payload["crews"])}
    old_to_new = {i: remap[clan_id] for i, clan_id in enumerate(order)}
    payload["cells"] = [old_to_new[v] if k % 5 == 0 else v
                        for k, v in enumerate(cells_flat)]
    for r in payload["regions"]:
        r["c"] = old_to_new[r["c"]]

    body = json.dumps(payload, separators=(",", ":")).encode()
    p = _out_path(zoom)
    tmp = p.with_suffix(".tmp")
    with gzip.open(tmp, "wb", compresslevel=6) as fh:
        fh.write(body)
    tmp.replace(p)                              # atomic: a reader never sees half a file

    return {"crews": len(payload["crews"]), "tiles": len(won), "held": len(cells_flat) // 5,
            "regions": len(regions_out), "bytes": p.stat().st_size,
            "window_days": window_days, "zoom": zoom}


def cached(zoom: int = T.DEFAULT_ZOOM) -> bytes | None:
    """The gzipped payload as stored, to be handed straight to the client."""
    p = _out_path(zoom)
    return p.read_bytes() if p.exists() else None


def cached_mtime(zoom: int = T.DEFAULT_ZOOM) -> float:
    p = _out_path(zoom)
    return p.stat().st_mtime if p.exists() else 0.0


def ranking(db, limit: int = 50) -> list[dict]:
    """Crews by their biggest unbroken stretch of ground.

    Reads the standings the rebuild wrote. The previous version recomputed `area_km2()` — a
    parse plus sinh, atan and cos — for every held tile on every request, which is a full
    table scan and a few hundred trig calls to answer a question whose answer changes once an
    hour.
    """
    rows = (db.query(Clan)
            .filter(Clan.disbanded_at.is_(None), Clan.terr_tiles > 0)
            .order_by(Clan.terr_best_km2.desc(), Clan.terr_km2.desc())
            .limit(limit).all())
    return [{"clan_id": c.clan_id, "name": c.name, "slug": c.slug, "colour": c.colour,
             "pattern": c.pattern, "tiles": c.terr_tiles or 0,
             "km2": c.terr_km2 or 0.0, "best_km2": c.terr_best_km2 or 0.0,
             "regions": c.terr_regions or 0,
             "emblem": f"/api/v1/crews/{c.slug}/emblem"}
            for c in rows]


def contributors(db, clan_id: str, window_days: int = WINDOW_DAYS, limit: int = 20) -> list:
    """Who actually rode for this crew, most kilometres first.

    Deliberately scoped to the same rolling window as territory, so the list explains the
    ground on the map rather than all-time loyalty. Riders are named by their public handle;
    the store_id never leaves the server.
    """
    from datetime import timedelta
    from models import Rider
    since = utcnow() - timedelta(days=window_days)
    rows = (db.query(Trip.rider_store_id, sa.func.sum(Trip.distance_km),
                     sa.func.count(Trip.trip_uuid))
            .filter(Trip.clan_id == clan_id, Trip.validation_status == "validated",
                    Trip.start_utc >= since)
            .group_by(Trip.rider_store_id)
            .order_by(sa.func.sum(Trip.distance_km).desc())
            .limit(limit).all())
    if not rows:
        return []
    ids = [r[0] for r in rows]
    riders = {r.store_id: r for r in db.query(Rider).filter(Rider.store_id.in_(ids)).all()}
    roles = {m.store_id: m.role for m in
             db.query(ClanMember).filter(ClanMember.clan_id == clan_id,
                                         ClanMember.store_id.in_(ids)).all()}
    out = []
    for store_id, km, n in rows:
        r = riders.get(store_id)
        out.append({"id": r.public_id if r else None,
                    "name": r.display_name if r else "—",
                    "flag": r.flag if r else None,
                    "has_avatar": bool(r and r.avatar_png),
                    "role": roles.get(store_id, "past"),
                    "km": round(km or 0.0, 1), "rides": n})
    return out
