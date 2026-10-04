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
import logging
import math
from datetime import timedelta
from pathlib import Path

import sqlalchemy as sa

import config
from models import Clan, ClanCell, ClanMember, Trip, TripTrack, utcnow, publishable_handle
from services import tiles as T

_log = logging.getLogger(__name__)

WINDOW_DAYS = 90           # the rolling window: territory is what you ride, not what you rode
FRESH_DAYS = 7             # how long newly taken ground still counts as news

# How soon after a ride the map may be redrawn, and the floor between two such redraws. The
# rebuild is 0.5-1.3s on today's data and grows with the trips in the window, so this is the
# debounce that stops a group ride queueing one rebuild per rider.
FRESH_GAP_S = 45

# Set by the upload path when a ride lands that could move a square, drained by the loop in
# main.py. In the process rather than the database deliberately: it is derived state, losing it
# on restart costs one late rebuild, and the hourly pass is still underneath as the floor.
_dirty = [False]


def mark_dirty() -> None:
    """A ride has landed that could change the map.

    Called from the upload path for a ride that validated AND was credited to a crew. A
    flagged ride, or a ride by somebody with no crew, moves no squares and must not trigger
    anything -- the whole point of the mark is that it means something when it is set.
    """
    _dirty[0] = True


def is_dirty() -> bool:
    """Whether a rebuild is owed. Read by the panel so its countdown tells the truth."""
    return _dirty[0]


def claim_dirty() -> bool:
    """Take the mark, if there is one. The caller now owes a rebuild."""
    if not _dirty[0]:
        return False
    _dirty[0] = False
    return True
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
# The week is seven days measured from each ride, not a calendar week. On calendar weeks a
# rider puts the full cap in on one side of the boundary and the full cap in again on the
# other, so the real limit was twice the stated one for anybody who noticed where the
# boundary was. It is not measured from *now* either: that let a score climb on its own as
# trips aged into fresh buckets, and a score may only ever decay. See accumulate().
RIDER_TILE_WEEK_CAP_KM = 6.0
CAP_WINDOW_S = 7 * 86400


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

    # (rider, tile) -> [(when, km counted), ...] newest first, so one odometer cannot hold a
    # tile. See CAP_WINDOW_S for why this is a trailing window rather than a calendar week.
    tracks = _tracks_for(db, [r[0] for r in rows])
    spent: dict[tuple, list] = {}
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
        when = started.timestamp() if started else 0.0
        for tile, d in per_tile.items():
            ridden = d * scale
            if ridden < min_visit_km(tile):
                continue                         # clipped the corner; not a visit
            key = (store_id, tile)
            seen = spent.get(key)
            if seen is None:
                seen = spent[key] = []
            # Everything already counted for this rider and square is newer, because the rows
            # are ordered newest first, so the window is the tail of the list: walk back from
            # the oldest entry until one is more than a week away and stop.
            used = 0.0
            for i in range(len(seen) - 1, -1, -1):
                if seen[i][0] - when >= CAP_WINDOW_S:
                    break
                used += seen[i][1]
            room = RIDER_TILE_WEEK_CAP_KM - used
            if room <= 0:
                continue
            counted = min(ridden, room)
            seen.append((when, counted))
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
                # A ring cannot swallow more than it rode, but dropping the whole gain made
                # the rule punish riding: a loop one block wider went from paying double to
                # paying nothing, and crew.how.3 promises the opposite. Take as much as the
                # ring is worth instead, from the middle outward, so the curve flattens at
                # twice rather than falling off a cliff.
                # Inward from the ring, not outward from the middle of the hole. Taking the
                # centre first left a floating disc with a gap between it and the loop that
                # earned it: two regions instead of one, a second emblem drawn in the middle
                # of nowhere, and the ranked number unmoved because the biggest patch was
                # still just the ring. From 12x12 up the gain never touched the ring at all,
                # and a ring road at this zoom is about twelve squares across.
                edge = {q for q in got
                        if any((q[0] + dx, q[1] + dy) in comp
                               for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)))}
                taken, frontier = set(edge), list(edge)
                while frontier and len(taken) < len(comp):
                    nxt = []
                    for (x, y) in frontier:
                        for q in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                            if q in got and q not in taken and len(taken) < len(comp):
                                taken.add(q)
                                nxt.append(q)
                    frontier = nxt
                got = taken
            gained |= got
        claimed |= gained
        out[clan_id] = pts | gained
    return out


def _name_lookup(acc: dict, kept: dict, clans: dict, zoom: int) -> dict:
    """(x, y) -> neighbourhood, for every square a card could point at.

    Built before the cards, because `targets_for` deduplicates on the place: stamping names
    afterwards left the dedupe keying on None and treating two squares in different
    neighbourhoods as the same row. One batched offline call for the whole world.

    The candidate set is every square anybody holds plus its neighbours, plus everywhere a
    crew has ridden. That is a superset of what any card can offer and still one lookup.
    """
    want = set()
    for pts in kept.values():
        for (x, y) in pts:
            want.add((x, y))
            want.update(((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)))
    for tile in acc:
        pt = T.parse(tile)
        if pt:
            # and everything around it: a first block is anchored on a ridden square and
            # reaches a diagonal from it, which no other set here covers
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    want.add((pt[1] + dx, pt[2] + dy))
    if not want:
        return {}
    want = sorted(want)
    coords = []
    for (x, y) in want:
        b = T.bounds(f"{zoom}/{x}/{y}")
        coords.append(((b[1] + b[3]) / 2, (b[0] + b[2]) / 2) if b else (0.0, 0.0))
    try:
        from ingest.geo import places_for
        names = places_for(coords)
    except Exception:
        # a card without place names is the card we had; a rebuild that dies here is not
        _log.exception("place lookup failed")
        return {}
    return {xy: n for xy, n in zip(want, names) if n}


def _first_block(acc: dict, clan_id: str, won: dict, zoom: int, mine: set,
                 seed: int = SEED) -> set:
    """The cheapest SEED x SEED block a crew with no ground can complete.

    Scored on the total extra distance across its squares, so a block with three squares
    already led beats one on empty ground, and a block over somebody else's territory loses
    to one on nobody's. Anchored on squares the crew already rides, because a block somewhere
    it has never been is a suggestion rather than a plan.
    """
    if not mine:
        return set()

    def cost(x, y):
        tile = f"{zoom}/{x}/{y}"
        w = won.get(tile)
        if w and w[0] == clan_id:
            return 0.0
        km = acc.get(tile, {}).get(clan_id, [0.0, set()])[0]
        return max(0.0, max(min_lead_km(tile), w[1] if w else 0.0) - km)

    def mine_km(x, y):
        return acc.get(f"{zoom}/{x}/{y}", {}).get(clan_id, [0.0, set()])[0]

    best, best_key = None, None
    seen = {}
    for (ax, ay) in sorted(mine):
        # every block this ridden square could be a corner of
        for dx in range(-(seed - 1), 1):
            for dy in range(-(seed - 1), 1):
                key = (ax + dx, ay + dy)
                if key in seen:
                    continue
                sq = [(key[0] + a, key[1] + b) for a in range(seed) for b in range(seed)]
                total = sum(cost(x, y) for x, y in sq)
                seen[key] = total
                # Cheapest, and among equally cheap ones the block this crew already rides
                # most -- six were tied for one crew and four for another, and the winner was
                # whichever the set happened to yield first, so it could move between
                # rebuilds with nothing having changed.
                k = (round(total, 6), -sum(mine_km(x, y) for x, y in sq), key)
                if best_key is None or k < best_key:
                    best, best_key = sq, k
    return set(best or ())


def _pressure(acc: dict, tile: str, holder: str, held_km: float,
              blocked: set | None = None,
              seedless: set | None = None) -> tuple[int, int, str | None]:
    """(band, a number, who is pushing) for a held tile.

    The band is what the map paints and the number is what a rider can act on. Saying "about
    to flip" and stopping is the shape of a warning without the content of one: nobody can
    tell whether that means two more kilometres this week or four more people.

      0 nobody near it        the number is how far clear the holder is, in tenths of a km
      1 somebody is riding it      ... the number is what the rival still needs
      2 about to flip              ... same
      3 fading                     near the floor with nobody else wanting it; the number is
                                   days until it goes, which is what a rider can plan around
      4 ringed                     held because the crew rode all the way around it

    Rivals under the tile's own floor, rivals whose claim here was withdrawn for failing to
    seed, and crews that are nowhere near a block of their own, are not counted: a warning
    that fires on somebody who structurally cannot take the tile is one people learn to
    ignore. `seedless` is the crews that draw nothing AND are more than one square from
    drawing something; a crew with three corners of a block is one ride from all of it and
    counts like anybody else.
    """
    if held_km <= 0:
        # Gained by enclosure: nobody rode it, so none of the questions below apply. This used
        # to fall through to "fading, 0.0 km clear", and to "about to flip" whenever any rival
        # had kilometres there, both false on ground that cannot be lost until the ring breaks.
        return 4, 0, None

    floor = min_lead_km(tile)
    out = blocked or set()
    dead = seedless or set()
    near = [(v[0], c) for c, v in acc.get(tile, {}).items()
            if c != holder and c not in dead and v[0] >= floor and (tile, c) not in out]
    rivals = [v for v, _c in near]
    # who is pushing hardest, for the card that is about to lose the square
    pushing = max(near)[1] if near else None

    if not rivals:
        slack = held_km - floor
        if slack > floor * 0.35:
            return 0, max(0, int(round(slack * 10))), None
        # Days left, not slack. The slack on a fading tile is tiny by definition, so printing
        # it could only ever say "0.0 km", which is a label rather than something to plan
        # around. At a HALF_LIFE_DAYS half-life this is exact.
        days = HALF_LIFE_DAYS * math.log2(held_km / floor) if held_km > floor else 0.0
        return 3, max(0, int(round(days))), None

    best = max(rivals)
    need = max(0.0, held_km - best)          # what the rival still has to find
    ratio = best / held_km
    band = 2 if ratio >= 0.85 else 1 if ratio >= 0.5 else 0
    return band, int(round(need * 10)), pushing


_COMPASS = ("n", "ne", "e", "se", "s", "sw", "w", "nw")
# How far out of your own ground the list may point, in squares. At 4 this was five kilometres
# at Copenhagen and three and a half at Tromso, which cannot cross a city: measured, it found a
# rival for two crews out of twelve. Fourteen is about seventeen kilometres at Oslo, which is a
# ride somebody might actually make to go and take something.
REACH = 14


def _bearing(dx: int, dy: int) -> str:
    """Which way out of town, to the nearest eighth.

    Four rows reading "0.1 km, nobody holds it" are one row printed four times. The same four
    with a direction on them are four places, and choosing between them is the decision the
    mode is supposed to be about. Tile y grows southward, hence the minus.
    """
    if dx == 0 and dy == 0:
        return ""
    return _COMPASS[round(math.atan2(dx, -dy) / (math.pi / 4)) % 8]


def bucket(holder_of: dict, size: int = REACH) -> dict:
    """Every held square filed by a coarse grid cell, built once for the whole board.

    Flood-filling REACH rings out of each crew's own ground was fine at four steps and is not
    at fourteen: the frontier grows with the square of the radius and it was being redone per
    crew. Filing the board once and looking in the nine cells around each of a crew's own
    squares answers the same question against a number that does not move.
    """
    out: dict = {}
    for (x, y), cid in holder_of.items():
        out.setdefault((x // size, y // size), []).append(((x, y), cid))
    return out


def _rivals_near(held: set, buckets: dict, clan_id: str, reach: int = REACH) -> set:
    """Squares another crew holds, within `reach` of anything this crew holds."""
    out = set()
    seen_cells = set()
    for (x, y) in held:
        cx, cy = x // reach, y // reach
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                cell = (cx + dx, cy + dy)
                if cell in seen_cells:
                    continue
                seen_cells.add(cell)
                for xy, cid in buckets.get(cell, ()):
                    if cid != clan_id:
                        out.add(xy)
    if not out:
        return out
    # the cells are coarse, so trim to the ones genuinely within reach
    return {q for q in out
            if any(max(abs(q[0] - hx), abs(q[1] - hy)) <= reach for (hx, hy) in held)}


def _one_square_short(pts: set, seed: int = SEED) -> bool:
    """Is this crew a single square away from a block of its own?

    Cheap: for every square they lead, look at the blocks it sits in and count how many corners
    they already have. One missing is one ride.
    """
    if not pts:
        return False
    for (x, y) in pts:
        for dx in range(-(seed - 1), 1):
            for dy in range(-(seed - 1), 1):
                block = [(x + dx + a, y + dy + b) for a in range(seed) for b in range(seed)]
                if sum(1 for b in block if b in pts) >= len(block) - 1:
                    return True
    return False


def targets_for(acc: dict, kept: dict, clan_id: str, won: dict, zoom: int,
                limit: int = 8, seed: int = SEED, holder_of: dict | None = None,
                mine: set | None = None, leads: set | None = None, names: dict | None = None,
                patches: list | None = None, buckets: dict | None = None) -> list[dict]:
    """The ground this crew could take next, and what taking it would do.

    This is the one question the mode has to answer and did not. A rider could see that a tile
    was contested only by finding it, and the tiles worth riding are exactly the ones somebody
    else holds, which look like any other street from the saddle.

    Four things can put a square on the list:

    * it touches ground the crew holds
    * the crew already has kilometres in it and does not hold it
    * somebody else holds it and it is within REACH squares of the crew's own ground. This is
      the one that makes the list worth opening: without it the furthest the card could ever
      point was one square down a street you are already on.

    There was a fourth: the squares along the road between the crew's two biggest patches, on
    the theory that a welding square could not otherwise reach the list. It could. `joins`
    needs one square orthogonally touching two patches at once, which only happens across a
    one-tile gap, and a square in a one-tile gap is a neighbour of held ground already. The
    road produced nothing at any distance and cost up to 35ms per crew, so it is gone. A
    genuine roadtrip is several rides long and is not a thing one row can ask for.

    And four things can make a square worth more than its distance:

    * `first` completes a 2x2 for a crew that holds nothing. Until you have a block you are not
      on the map however far you ride, and the crew in that state was the only one the list had
      nothing to say to.
    * `links` is a square on the road between two of the crew's patches, carrying how many
      more squares it takes to close the gap. The board ranks on the biggest single patch, so
      welding two together beats widening either end by any amount, and it is the only move in
      the game worth planning a whole afternoon around. It used to be `joins`, which required
      a square touching two patches at once: that can only happen across a gap of exactly one,
      where the square is an ordinary neighbour anyway, so it fired on nothing, ever.
    * `grows` extends the patch the crew is ranked on. It sorts the list and stays off the
      screen: it was true of ten rows out of ten for eleven of twelve crews.
    * `kills` means the holder is standing on it: take it and their seed breaks, and whatever
      was only connected through it goes with it. It is the biggest single move on the board
      and the list used to print it as an ordinary row.
    * `blocked` means the crew has ridden enough there and still does not hold it, because a
      square only counts as part of a 2x2. Those go last. Their shortfall is zero, so by
      distance alone they sorted straight to the top, and a list called "Where to ride next"
      opened with two rows where riding does nothing at all.

    `kept` is the whole board, crew by crew: who holds a square is not the same question as who
    leads it on kilometres, and a crew with no 2x2 anywhere can lead ten squares and hold none.
    """
    held = set(kept.get(clan_id) or ())
    # The caller normally hands these in, built once for the whole board: on their own they
    # are three passes over every tile there is, and done per crew they were the entire cost
    # of this function. Computed here when absent so the function still stands alone.
    if holder_of is None:
        holder_of = {xy: other for other, pts in kept.items() for xy in pts}
    if buckets is None:
        buckets = bucket(holder_of)
    if mine is None:
        mine = {(p[1], p[2]) for p in
                (T.parse(t) for t, per in acc.items() if clan_id in per) if p}
    if leads is None and not held:
        # squares the crew wins on kilometres, which is not the same as squares it draws.
        # Only a crew holding nothing ever reads this.
        leads = {(p[1], p[2]) for p in
                 (T.parse(t) for t, w in won.items() if w[0] == clan_id) if p}
    leads = leads or set()

    cand = set()
    for (x, y) in held:                       # everything touching what we hold
        for nb in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
            if nb not in held:
                cand.add(nb)
    # Anywhere we already ride -- but only near ground the crew actually holds. `_rivals_near`
    # is bounded to REACH for exactly this reason and this line was not bounded at all, so an
    # Oslo crew whose riders had been to Paris was offered a square in Aubervilliers, 1,335 km
    # away, in the same "how much more you have to ride" column as a square two streets over.
    # The cheapest-looking row on the card sent somebody to another country.
    if held:
        near = set()
        for (x, y) in held:
            near.add((x // REACH, y // REACH))
        cand |= {q for q in (mine - held)
                 if any((q[0] // REACH + dx, q[1] // REACH + dy) in near
                        for dx in (-1, 0, 1) for dy in (-1, 0, 1))}
    else:
        cand |= mine - held

    # A crew holding nothing needs one block, not eight singles: see _first_block.
    block = _first_block(acc, clan_id, won, zoom, mine, seed) if not held else set()
    cand |= block

    patches = list(patches if patches is not None else regions(held))
    patches.sort(key=len, reverse=True)
    # The road between the two biggest patches, when they are close enough that closing it is
    # a ride rather than a holiday. Four squares is about five kilometres at Oslo.
    link_road, link_len = set(), 0
    if len(patches) > 1:
        a = min(patches[0], key=lambda q: min((q[0] - r[0]) ** 2 + (q[1] - r[1]) ** 2
                                              for r in patches[1]))
        b = min(patches[1], key=lambda q: (q[0] - a[0]) ** 2 + (q[1] - a[1]) ** 2)
        gap = max(abs(a[0] - b[0]), abs(a[1] - b[1])) - 1
        if 0 < gap <= 4:
            x0, y0, x1, y1 = a[0], a[1], b[0], b[1]
            steps = max(abs(x1 - x0), abs(y1 - y0)) or 1
            for i in range(steps + 1):
                q = (round(x0 + (x1 - x0) * i / steps), round(y0 + (y1 - y0) * i / steps))
                if q not in held:
                    link_road.add(q)
            link_len = len(link_road)
    if patches and buckets is not None:
        # rivals within riding distance. Not the whole board: a list that points at another
        # city is as useless as one that points at the next street.
        cand |= _rivals_near(held, buckets, clan_id)
    cand -= held

    patch_of = {}
    for i, comp in enumerate(patches):
        for xy in comp:
            patch_of[xy] = i
    biggest = 0 if patches else None
    home = held or mine
    cx = sum(x for x, _ in home) / len(home) if home else 0.0
    cy = sum(y for _, y in home) / len(home) if home else 0.0

    out = []
    for (x, y) in cand:
        if holder_of.get((x, y)) == clan_id:   # never send anybody where they already won
            continue
        tile = f"{zoom}/{x}/{y}"
        w = won.get(tile)
        km = acc.get(tile, {}).get(clan_id, [0.0, set()])[0]
        need = max(0.0, max(min_lead_km(tile), w[1] if w else 0.0) - km)
        if need > 0:
            # A trip that puts less than the visit floor into a square is thrown away whole, so
            # printing "0.1 km" where the floor is 0.15 asks for a ride that cannot count. Up,
            # not nearest: round() put 0.147 back to 0.1 and undid this on the line below.
            need = math.ceil(max(need, min_visit_km(tile)) * 10) / 10
        touching = {patch_of[nb] for nb in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1))
                    if nb in patch_of}
        # The four squares of the one block this crew should go and take. The old test asked
        # whether this square completed a block the crew already led three corners of, which
        # across sixteen crews fired on nothing, ever.
        first = (x, y) in block
        out.append({"x": x, "y": y, "need": need,
                    # the neighbourhood, so the dedupe below can tell two rides apart and the
                    # card can say where rather than only which way
                    "at": (names or {}).get((x, y)),
                    "held_by": holder_of.get((x, y)),
                    "dir": _bearing(round(x - cx), round(y - cy)),
                    "first": first,
                    "joins": len(touching) > 1 or (x, y) in link_road,
                    "links": link_len if (x, y) in link_road else 0,
                    "grows": biggest is not None and biggest in touching,
                    "blocked": need <= 0.0 and km > 0.0})

    def rank(t):
        # While a crew has no ground, only the block it is being sent to can be kept: seeded()
        # drops everything else and award withdraws the claim outright. A cheap square ten
        # squares away is not a cheaper option, it is a wasted evening.
        if block and not t["first"]:
            return 10
        if t["blocked"]:
            return 9                          # nothing you ride today changes it
        if t["first"]:
            return 0                          # one square from being on the map at all
        if t["joins"]:
            return 1
        if t["grows"] and t["held_by"]:
            return 2
        # Taking ground off somebody beats widening an empty edge, even when the empty edge is
        # closer. Ranked the other way round, eight blank neighbours filled the list and the
        # one square in the world that another crew actually holds fell off the bottom.
        if t["held_by"]:
            return 3
        return 4 if t["grows"] else 5

    out.sort(key=lambda t: (rank(t), t["need"], t["x"], t["y"]))
    if block:
        # Four squares, one move, nothing else on the card: anything past the block is ground
        # this crew cannot hold yet, and it is cheaper, so it was sorting to the top.
        out = [t for t in out if t["first"]] or out

    # Would taking it break the holder's seed? Only asked of the rows that made the cut, so a
    # crew pays for `limit` flood fills, not one per candidate.
    for t in out[:limit * 2]:
        holder = t["held_by"]
        t["kills"] = False
        if not holder:
            continue
        theirs = kept.get(holder) or set()
        if len(theirs) < 2:
            continue
        left = seeded(theirs - {(t["x"], t["y"])}, seed)
        # More than one dangling tile. At "> 1" this fired on four crews out of five to mean
        # "they lose two squares out of a hundred and twenty", in the hottest colour in the
        # card, which teaches a rider to ignore it before they ever meet a real one.
        # A quarter of a hundred-and-twenty-square crew is thirty, and no single square has
        # ever cost anybody thirty: measured across every held square of every crew, the best
        # one is five. Scaling the bar with the victim's size moved this from firing four
        # times too often to never firing at all. Three squares is a real hole whoever you
        # are, and the row carries the number so it argues for itself.
        lost = len(theirs) - len(left)
        # Measured over every held square of every crew in the demo world: 520 squares, of
        # which 59 (11.3%) cost their holder two or more and 31 (6.0%) cost three or more.
        # At 3, with the one-per-victim rule below, this fired once in the whole world, which
        # is a feature nobody meets. Rebuild the same world with the commands in
        # scripts/seed_demo.py's docstring if you want to re-measure before moving it again.
        #
        # The gate below is the other half, and it used to live only in a comment: an earlier
        # version of this one claimed the card refused to print the badge unless the board
        # moved. The card does that for `drops` and never did it for `kills`, so one row in
        # six announced BREAKS THEIR BLOCK beside a standing line advertising the victim as
        # 1st with 91 squares -- and 91 was also what they had afterwards. Breaking a
        # detached outpost takes squares off a crew without touching the number anybody is
        # ranked on, and the badge is about the ranking.
        t["kills"] = lost >= 2
        # What the board would actually show them afterwards. `lost` counts every square they
        # drop anywhere, and the board ranks the biggest single patch, so a square taken off
        # a detached outpost cost them four tiles and nothing at all on the ladder -- the row
        # said "drops to 12" while their ranked number stayed 16.
        before = max((len(c) for c in regions(seeded(theirs, seed))), default=0)
        after = max((len(c) for c in regions(left)), default=0)
        t["lost"] = lost
        t["ranked_now"] = after
        t["ranked_was"] = before
        # Said plainly here rather than trusted to the card: if the board does not move, this
        # is not the move the badge says it is.
        if before <= after:
            t["kills"] = False
    # a square that takes a crew off the map outranks everything except being on the map
    # yourself, which is the same move from the other side
    # `blocked` last within its group. A square with nothing left to ride has `need` 0, and
    # `need` sorted third, so it went to the top of a card headed "Where to ride next" -- on a
    # brand-new crew's first-block card that put the one square they cannot ride above the two
    # they must, and the place name (printed on the first row) landed on it. The card told a
    # new crew where to go by naming the square they had already been to.
    out.sort(key=lambda t: (0 if t.get("first") else 1 if t.get("kills") else 2,
                            1 if t.get("blocked") else 0,
                            rank(t), t["need"], t["x"], t["y"]))
    # One per victim. Four rows that are the same decision about the same crew are one row and
    # three wasted lines, and the cheapest of them is the one to ride.
    struck = set()
    for t in out:
        if not t.get("kills"):
            continue
        if t["held_by"] in struck:
            t["kills"] = False
        else:
            struck.add(t["held_by"])
    out.sort(key=lambda t: (0 if t.get("first") else 1 if t.get("kills") else 2,
                            1 if t.get("blocked") else 0,
                            rank(t), t["need"], t["x"], t["y"]))

    # Carried on every row rather than beside the list, because the payload is a flat array of
    # rows and the card reads them one at a time.
    # Everything a row prints is effort, bearing and who holds it, so two rows agreeing on all
    # three are the same row printed twice. A quarter of every card was a duplicate, and seven
    # crews in fifteen showed five distinct rows out of eight while eleven to a hundred
    # candidates went unmentioned. Keep the first of each face, then fill the freed slots from
    # what is left, because a near-duplicate still beats a short card.
    # Mirrors the six rungs the client prints (crews.js effort()). The two used to disagree:
    # this bucketed on absolute kilometres at four steps while the card bucketed on six rungs
    # relative to the square's own floor, so the list could hide a row that would have read
    # differently and keep two that read the same.
    def face(t):
        f = min_lead_km(f"{zoom}/{t['x']}/{t['y']}") or 0.5
        r = t["need"] / f
        step = (1 if r <= 0.4 else 2 if r <= 1 else 3 if r <= 2.5
                else 4 if r <= 5 else 5 if r <= 10 else 6)
        # Not the bearing. A compass letter is the one thing on a row that differs without
        # anything differing: eight squares around one crew's ground read as eight rows and
        # one decision, which is exactly what this key exists to stop. The place is in, because
        # two squares in different neighbourhoods are two different rides however alike the
        # numbers look.
        return (step, t["held_by"], t.get("at"))

    order = {id(t): i for i, t in enumerate(out)}
    # How many rows may share a face. Two on the card a rider actually sees, more when a
    # caller asks for a long list and wants the tail.
    quota = max(2, limit // 4)
    taken_face: dict = {}
    picked = []
    # A block is one move and only works whole; halving it tells a new crew to ride half a
    # block, which puts them on no map at all. The roadtrip is rare enough to be exempt too.
    for t in out:
        if t.get("first") or t.get("links") or t.get("joins"):
            taken_face[face(t)] = taken_face.get(face(t), 0) + 1
            picked.append(t)
    for t in out:                       # best first, a few of each face
        if len(picked) >= limit:
            break
        if t.get("first") or t.get("links") or t.get("joins"):
            continue
        k = face(t)
        if taken_face.get(k, 0) >= quota:
            continue
        taken_face[k] = taken_face.get(k, 0) + 1
        picked.append(t)
    # back into rank order: preferring a distinct row must not promote a worse one above a
    # better one, only decide which of two equally good ones gets the slot
    picked.sort(key=lambda t: order[id(t)])
    out = picked

    # What the crew's own ranked number becomes if they take each square, from the same
    # `seeded` the board itself uses so a row cannot claim a number the board would not show.
    # Computed HERE, on the rows that actually ship: done on the pre-dedupe ranking it missed
    # every row the dedupe promoted from further down, and four rows went out with no answer
    # at all underneath a headline asserting what each of them does.
    own_now = max((len(c) for c in regions(seeded(held, seed))), default=0)
    for t in out[:limit]:
        t["own_now"] = own_now
        t["grown"] = max(
            (len(c) for c in regions(seeded(held | {(t["x"], t["y"])}, seed))), default=0)

    # `grows` has done its job in the sort. It was true of ten rows in ten for almost every
    # crew, nothing on the client reads it, and it is bytes in the payload and in the row.
    for t in out[:limit]:
        del t["grows"]
        t.setdefault("kills", False)
        t.setdefault("lost", 0)
        t.setdefault("links", 0)
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
    prev, first_led = {}, {}
    won_at = {}              # when the newest ride behind a tile actually happened
    for cell in db.query(ClanCell).all():      # one scan for both dicts, not two
        prev[cell.tile] = cell.clan_id
        first_led[(cell.tile, cell.clan_id)] = cell.first_led

    clans = {c.clan_id: c for c in db.query(Clan).filter(Clan.disbanded_at.is_(None)).all()}
    member_counts = dict(
        db.query(ClanMember.clan_id, sa.func.count(ClanMember.store_id))
        .filter(ClanMember.status == "active", ClanMember.left_at.is_(None))
        .group_by(ClanMember.clan_id).all())

    kept, won, withdrawn_all = award(acc, set(clans), prev, seed, last_seen=recency)
    # recency holds minus-days-ago per (tile, crew); turn the winner's into a date
    for (rtile, rcid), age in recency.items():
        rw = won.get(rtile)
        if rw and rw[0] == rcid:
            won_at[rtile] = utcnow() - timedelta(days=max(0.0, -age))

    # Crews that draw nothing anywhere AND are not one square from drawing something. The
    # first half alone was too generous: a crew holding three corners of a block is one ride
    # from existing, and everything it leads flips the moment it lands, so leaving it out of
    # the bands meant the holder got no warning at all until it was over.
    seedless = set()
    for cid in set(clans) - set(kept):
        leads = {(p[1], p[2]) for p in
                 (T.parse(t) for t, w in won.items() if w[0] == cid) if p}
        if not _one_square_short(leads, seed):
            seedless.add(cid)
    patches_by_clan = {cid: regions(pts) for cid, pts in kept.items()}

    # --- where to ride next, worked out BEFORE the write transaction opens.
    #
    # Two things were wrong with doing it further down. It sat between the ClanCell delete and
    # the commit, so SQLite's single writer was held for the whole of it: 36 seconds once an
    # hour at a hundred times today's data, during which every join, leave, upload and emblem
    # write waits, and so does the session touch that serving a crew page performs. And the
    # three dictionaries below were rebuilt inside the per-crew loop, which is the same answer
    # recomputed once per crew: 50M, 81M and 53M iterations at that size, 21 seconds of the
    # 36. Built once out here it is 1.2 seconds and nothing holds the lock.
    holder_of = {xy: cid for cid, pts in kept.items() for xy in pts}
    buckets = bucket(holder_of)
    mine_by_clan: dict[str, set] = {}
    for tile, per in acc.items():
        pt = T.parse(tile)
        if pt is None:
            continue
        for cid in per:
            mine_by_clan.setdefault(cid, set()).add((pt[1], pt[2]))
    # Every crew that draws nothing, not only the ones too far from a block to be a threat.
    # `seedless` means "cannot take anything" and is narrower than "holds nothing"; handing the
    # narrower set here meant the crews one ride from existing got an empty leads set instead
    # of None, and `first` could never fire for the only crews it is for.
    nothing_held = set(clans) - set(kept)
    leads_by_clan: dict[str, set] = {}
    for tile, w in won.items():
        if w[0] in nothing_held:
            pt = T.parse(tile)
            if pt:
                leads_by_clan.setdefault(w[0], set()).add((pt[1], pt[2]))
    # Names first, because the dedupe inside targets_for keys on them: stamped afterwards,
    # `at` was always None by the time the quota read it and two squares in different
    # neighbourhoods counted as the same row.
    names_for = _name_lookup(acc, kept, clans, zoom)
    targets_json = {}
    for clan_id in clans:
        try:
            targets_json[clan_id] = json.dumps(targets_for(
                acc, kept, clan_id, won, zoom, seed=seed,
                holder_of=holder_of, mine=mine_by_clan.get(clan_id, set()),
                leads=leads_by_clan.get(clan_id),
                patches=patches_by_clan.get(clan_id, []), buckets=buckets,
                names=names_for))
        except Exception:
            # A silent failure here empties every crew's list and then tells crews that hold
            # ground that they hold none, which is the opposite of the truth.
            _log.exception("targets_for failed for %s", clan_id)
            targets_json[clan_id] = None

    # --- ClanCell rows: the admin view and the ranking read these
    db.query(ClanCell).delete()
    for c in clans.values():          # cleared first, so a crew that lost everything shows 0
        c.terr_km2 = c.terr_best_km2 = 0.0
        c.terr_tiles = c.terr_regions = c.terr_best_tiles = c.terr_best_fresh = 0
        c.targets_json = targets_json.get(c.clan_id)
    now = utcnow()
    order = sorted(kept.keys())
    payload_crews = []
    cells_flat: list[int] = []
    # [cell ordinal, crew index, ...] for the squares somebody else is riding
    rivals_flat: list[int] = []
    losable: list[tuple[int, int, int]] = []   # (cell ordinal, x, y) for bands 1, 2 and 3
    idx_of = {cid: i for i, cid in enumerate(order)}
    regions_out = []
    for idx, clan_id in enumerate(order):
        c = clans[clan_id]
        km2 = 0.0
        pts = kept[clan_id]
        # The squares this crew took inside the window, kept so the components pass below can
        # count the ones that fell inside the patch the board ranks on. The loop is already
        # making this decision per tile for the band; this only writes it down.
        fresh_pts: set[tuple[int, int]] = set()
        for (x, y) in sorted(pts):
            tile = f"{zoom}/{x}/{y}"
            # a tile gained by enclosure has no winner entry: nobody rode it, it is held
            # because the crew rode all the way around it
            w = won.get(tile)
            km, riders = (w[1], w[2]) if w else (0.0, 0)
            db.add(ClanCell(tile=tile, clan_id=clan_id, km=round(km, 3), riders=riders,
                            # dated from the ride that won it when the row is new, so a
                            # fresh install does not claim the whole map was taken this week
                            # and "held for" does not read "a day or two" everywhere on it
                            first_led=(first_led.get((tile, clan_id))
                                       or won_at.get(tile) or now)))
            km2 += T.area_km2(tile)
            band, need, pushing = _pressure(acc, tile, clan_id, km, withdrawn_all, seedless)
            # Before the fresh bump below: a square that is contested AND taken this week
            # comes out as band 6 or 7, matched neither 1 nor 2, and lost its rival's name on
            # precisely the squares that just changed hands.
            # `idx_of` only knows the crews in this payload, and `order` is built from the
            # crews that KEPT ground. A crew pushing on somebody's square need hold nothing
            # of its own -- a brand-new crew whose riders have been down the same street is
            # the ordinary case -- so this was `idx_of[pushing]` and raised KeyError, which
            # aborted the whole rebuild: no standings written, no payload written, and the
            # previous two files left in place to go stale in different directions. That is
            # what a reviewer saw as the board listing 13 crews while the map had 16.
            # The marker points at a crew index, so with no index there is no marker to draw;
            # the square still reports its band and its shortfall.
            if pushing is not None and band in (1, 2) and pushing in idx_of:
                rivals_flat.extend((len(cells_flat) // 5, idx_of[pushing]))
            # Fresh ground rides along in the band rather than as a sixth integer per cell:
            # the payload is five ints a tile and a whole extra column to carry one bit would
            # be 20% more bytes on the one response every visitor downloads.
            # Dated from the ride that won the square, not from this rebuild. Stamping `now`
            # meant a crew that recruited one rider's back catalogue showed "49 new this
            # week", and the first rebuild against an empty table lit the entire map up.
            got = first_led.get((tile, clan_id)) or won_at.get(tile)
            # nothing is news on the very first rebuild: there is no previous state for it to
            # be different from, and marking all of it new says the opposite of what it means
            # Before the bump, like the rival two lines up. Ground taken this week comes out
            # as band 6, 7 or 8, the client folds it back with `band % 5`, and these are the
            # rows that sort to the top of the losing card.
            if band in (1, 2, 3):
                losable.append((len(cells_flat) // 5, x, y))
            if prev and got and (now - got).days < FRESH_DAYS:
                band += 5
                fresh_pts.add((x, y))
            cells_flat.extend((idx, x, y, band, need))
        comps = patches_by_clan[clan_id]          # worked out once, above
        best_km2 = 0.0
        best_tiles = 0
        # The patch itself, not only its size: the board prints a weekly delta beside the
        # ranked figure, and the two have to be counted over the same squares. Clamping the
        # crew's whole fresh count at this figure instead made the row arithmetically possible
        # and still untrue -- 49 fresh cells across 3 patches printed as "+29" next to a
        # 29-square patch, which reads as the whole block having been won this week.
        best_comp: set | None = None
        for comp in comps:
            ex, ey, es = emblem_slot(comp)
            regions_out.append({"c": idx, "e": [ex, ey, es], "n": len(comp)})
            # One solid block beats the same ground in scattered pockets, counted in squares.
            # A square is the same amount of riding at every latitude and its area is not, so
            # ranking on area hands a rider at the equator four times the credit.
            if len(comp) > best_tiles:
                best_tiles = len(comp)
                best_comp = comp
            best_km2 = max(best_km2, sum(T.area_km2(f"{zoom}/{x}/{y}") for (x, y) in comp))
        c.terr_km2 = round(km2, 1)
        c.terr_best_km2 = round(best_km2, 1)
        c.terr_best_tiles = best_tiles
        c.terr_tiles = len(pts)
        c.terr_regions = len(comps)
        c.terr_best_fresh = len(fresh_pts & best_comp) if best_comp else 0
        payload_crews.append({
            "id": clan_id, "name": c.name, "slug": c.slug, "colour": c.colour,
            "pattern": c.pattern, "tiles": len(pts), "km2": round(km2, 1),
            "best_km2": round(best_km2, 1), "best_tiles": best_tiles,
            "best_fresh": c.terr_best_fresh,
            "regions": len(comps),
            "members": member_counts.get(clan_id, 0),
            "emblem": f"/api/v1/crews/{c.slug}/emblem",
        })
    db.commit()

    # One lookup for the whole world's losable ground, shared through an index because a
    # neighbourhood names several squares and the payload is the one file every visitor
    # downloads.
    places_flat: list[int] = []
    placenames: list[str] = []
    if losable:
        try:
            from ingest.geo import places_for
            coords = []
            for _ord, x, y in losable:
                b = T.bounds(f"{zoom}/{x}/{y}")
                coords.append(((b[1] + b[3]) / 2, (b[0] + b[2]) / 2) if b else (0.0, 0.0))
            at = {}
            for (cell, _x, _y), name in zip(losable, places_for(coords)):
                if not name:
                    continue
                if name not in at:
                    at[name] = len(placenames)
                    placenames.append(name)
                places_flat.extend((cell, at[name]))
        except Exception:
            # a card without place names is the card we had; a rebuild that dies here is not
            _log.exception("place lookup failed for losable ground")
            places_flat, placenames = [], []

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
        # [cell ordinal, crew index] pairs: who is taking the square, where there is a who
        "rivals": rivals_flat,
        # [cell ordinal, name index] pairs for the squares a crew can lose, so the defending
        # card can say where as well as who. Sparse and shared, because the same neighbourhood
        # names several squares.
        "places": places_flat,
        "placenames": placenames,
        "regions": regions_out,
    }
    # `crews` is sorted for display but `cells` indexes the unsorted order, so the indices are
    # remapped rather than leaving the client to join on two different orderings
    remap = {c["id"]: i for i, c in enumerate(payload["crews"])}
    old_to_new = {i: remap[clan_id] for i, clan_id in enumerate(order)}
    payload["cells"] = [old_to_new[v] if k % 5 == 0 else v
                        for k, v in enumerate(cells_flat)]
    payload["rivals"] = [old_to_new[v] if k % 2 else v
                         for k, v in enumerate(rivals_flat)]
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
            # squares first, area only to break a tie: see the note in rebuild()
            .order_by(Clan.terr_best_tiles.desc(), Clan.terr_best_km2.desc())
            .limit(limit).all())
    return [{"clan_id": c.clan_id, "name": c.name, "slug": c.slug, "colour": c.colour,
             "pattern": c.pattern, "tiles": c.terr_tiles or 0,
             "km2": c.terr_km2 or 0.0, "best_km2": c.terr_best_km2 or 0.0,
             "best_tiles": c.terr_best_tiles or 0,
             # Counted inside the patch `best_tiles` measures. The board puts them side by
             # side, so a delta counted over anything wider is a row that contradicts itself.
             "best_fresh": c.terr_best_fresh or 0,
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
    # Only riders who are still in the crew. Without this a leader who walked out kept their
    # star on the contributors list for the whole ninety-day window, so the card showed a crew
    # with a leader while `/crews/me` beside it said there was none -- and `ROLEIC.past`, the
    # mark written for exactly this rider, was unreachable code.
    roles = {m.store_id: m.role for m in
             db.query(ClanMember).filter(ClanMember.clan_id == clan_id,
                                         ClanMember.store_id.in_(ids),
                                         ClanMember.left_at.is_(None)).all()}
    out = []
    for store_id, km, n in rows:
        r = riders.get(store_id)
        # Same rule as everywhere else: a contributors list is public.
        out.append({"id": r.public_id if (r and publishable_handle(r.public_id)) else None,
                    "name": r.display_name if r else "?",
                    "flag": r.flag if r else None,
                    "has_avatar": bool(r and r.avatar_png),
                    "role": roles.get(store_id, "past"),
                    "km": round(km or 0.0, 1), "rides": n})
    return out
