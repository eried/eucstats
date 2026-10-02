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
MIN_TILE_KM = 0.3          # below this a tile is a passing GPS wobble, not a visit
MIN_LEAD_KM = 1.0          # and below THIS a lead does not take the tile at all


def _out_path(zoom: int) -> Path:
    return config.DATA_DIR / f"territory-z{zoom}.json.gz"


# --- gathering ----------------------------------------------------------------------------

def _trip_points(db, trip_uuid: str):
    from ingest.downsample import decode_track
    tt = db.get(TripTrack, trip_uuid)
    if not tt or not tt.points:
        return []
    try:
        return [(r[1], r[2]) for r in decode_track(tt.points)
                if r[1] is not None and r[2] is not None]
    except Exception:
        return []


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
    """{tile: {clan_id: [km, {riders}]}} over the window.

    Per-tile distance comes from the track, then the whole trip is scaled so its tiles sum to
    the odometer distance the ingest pipeline validated. That keeps the authority with the
    number that was checked against the GPS trace, while the *shape* comes from where the ride
    actually went.
    """
    since = utcnow() - timedelta(days=window_days)
    rows = (db.query(Trip.trip_uuid, Trip.clan_id, Trip.rider_store_id, Trip.distance_km,
                     Trip.start_lat, Trip.start_lon)
            .filter(Trip.validation_status == "validated",
                    Trip.clan_id.isnot(None),
                    Trip.start_utc >= since,
                    Trip.distance_km > 0)
            .all())
    acc: dict[str, dict[str, list]] = {}
    for trip_uuid, clan_id, store_id, km, slat, slon in rows:
        pts = _trip_points(db, trip_uuid)
        per_tile = _per_tile_km(pts, zoom) if len(pts) > 1 else {}
        if not per_tile:                         # no usable track: the start tile alone
            t = T.tile_of(slat, slon, zoom)
            if not t:
                continue
            per_tile = {t: km or 0.0}
        total = sum(per_tile.values())
        if total <= 0:
            continue
        scale = (km or 0.0) / total              # trust the validated odometer for the total
        for tile, d in per_tile.items():
            share = d * scale
            if share <= 0:
                continue
            e = acc.setdefault(tile, {}).setdefault(clan_id, [0.0, set()])
            e[0] += share
            e[1].add(store_id)
    return acc


def winners(acc: dict, previous: dict | None = None,
            skip: set | None = None) -> dict[str, tuple[str, float, int]]:
    """{tile: (clan_id, km, riders)} — most kilometres takes the tile.

    A lead under MIN_LEAD_KM takes nothing. Without that floor the cheapest way to hold ground
    was a fabricated 0.3 km "ride", which bought roughly thirty times more area per kilometre
    than actually riding — and the floor is what the spec said all along.

    Ties go to whoever held it first, which is the only answer that does not hand a tile back
    and forth every rebuild. `previous` is the last round's holders, from `ClanCell`.

    `skip` is a set of (tile, clan_id) claims that have already been ruled out this rebuild —
    see the re-award loop in rebuild().
    """
    prev = previous or {}
    blocked = skip or set()
    out = {}
    for tile, per in acc.items():
        best = None
        for clan_id, (km, riders) in per.items():
            if km < MIN_LEAD_KM or (tile, clan_id) in blocked:
                continue
            incumbent = prev.get(tile) == clan_id
            key = (km, 1 if incumbent else 0)
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

def award(acc: dict, live: set, prev: dict, seed: int = SEED,
          rounds: int = 8) -> tuple[dict[str, set], dict]:
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
        won = winners(acc, prev, skip=blocked)
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
                rivals = [c for c in acc.get(tile, {})
                          if c != clan_id and (tile, c) not in blocked
                          and acc[tile][c][0] >= MIN_LEAD_KM]
                if rivals:
                    withdrawn.add((tile, clan_id))
        if not withdrawn:
            return kept, won
        blocked |= withdrawn
    return kept, won


def _pressure(acc: dict, tile: str, holder: str, held_km: float) -> int:
    """How hard the nearest rival is pushing on this tile, 0 (safe) to 2 (slipping).

    Territory was binary — held or not — which hides the only thing that makes it a game: that
    somebody is riding your ground right now. A tile at 95% of your kilometres looks identical
    to one nobody has touched in three months, right up until the night it flips.

    Three buckets rather than a continuous value, because the client draws one shape per crew
    per bucket. A finer scale would mean a shape per tile.
    """
    rivals = [v[0] for c, v in acc.get(tile, {}).items() if c != holder]
    if not rivals or held_km <= 0:
        return 0
    ratio = max(rivals) / held_km
    if ratio >= 0.85:
        return 2          # about to go
    if ratio >= 0.5:
        return 1          # being pushed
    return 0


def _zoom_of(won: dict) -> int:
    for tile in won:
        p = T.parse(tile)
        if p:
            return p[0]
    return T.DEFAULT_ZOOM


def rebuild(db, window_days: int = WINDOW_DAYS, zoom: int = T.DEFAULT_ZOOM,
            seed: int = SEED) -> dict:
    """Recompute every crew's territory and write both outputs. Returns a short report."""
    acc = accumulate(db, window_days, zoom)
    prev = {c.tile: c.clan_id for c in db.query(ClanCell).all()}
    first_led = {(c.tile, c.clan_id): c.first_led for c in db.query(ClanCell).all()}

    clans = {c.clan_id: c for c in db.query(Clan).filter(Clan.disbanded_at.is_(None)).all()}
    member_counts = dict(
        db.query(ClanMember.clan_id, sa.func.count(ClanMember.store_id))
        .filter(ClanMember.status == "active", ClanMember.left_at.is_(None))
        .group_by(ClanMember.clan_id).all())

    kept, won = award(acc, set(clans), prev, seed)

    # --- ClanCell rows: the admin view and the ranking read these
    db.query(ClanCell).delete()
    for c in clans.values():          # cleared first, so a crew that lost everything shows 0
        c.terr_km2 = c.terr_best_km2 = 0.0
        c.terr_tiles = c.terr_regions = 0
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
            km, riders = won[tile][1], won[tile][2]
            db.add(ClanCell(tile=tile, clan_id=clan_id, km=round(km, 3), riders=riders,
                            first_led=first_led.get((tile, clan_id)) or now))
            km2 += T.area_km2(tile)
            cells_flat.extend((idx, x, y, _pressure(acc, tile, clan_id, km)))
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
    db.commit()

    payload = {
        "z": zoom, "generated": now.isoformat() + "Z", "window_days": window_days,
        "seed": seed,
        # ranked by the biggest unbroken stretch a crew holds, not the total: one solid
        # block is a harder thing to own than the same area scattered across a country
        "crews": sorted(payload_crews, key=lambda c: (-c["best_km2"], -c["km2"])),
        # [crewIndex, x, y, pressure, ...] — pressure is 0 safe / 1 pushed / 2 slipping
        "cells": cells_flat,
        "regions": regions_out,
    }
    # `crews` is sorted for display but `cells` indexes the unsorted order, so the indices are
    # remapped rather than leaving the client to join on two different orderings
    remap = {c["id"]: i for i, c in enumerate(payload["crews"])}
    old_to_new = {i: remap[clan_id] for i, clan_id in enumerate(order)}
    payload["cells"] = [old_to_new[v] if k % 4 == 0 else v
                        for k, v in enumerate(cells_flat)]
    for r in payload["regions"]:
        r["c"] = old_to_new[r["c"]]

    body = json.dumps(payload, separators=(",", ":")).encode()
    p = _out_path(zoom)
    tmp = p.with_suffix(".tmp")
    with gzip.open(tmp, "wb", compresslevel=6) as fh:
        fh.write(body)
    tmp.replace(p)                              # atomic: a reader never sees half a file

    return {"crews": len(payload["crews"]), "tiles": len(won), "held": len(cells_flat) // 4,
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
