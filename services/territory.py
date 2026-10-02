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
from datetime import timedelta
from pathlib import Path

import sqlalchemy as sa

import config
from models import Clan, ClanCell, ClanMember, Trip, TripTrack, utcnow
from services import tiles as T

WINDOW_DAYS = 90           # the rolling window: territory is what you ride, not what you rode
SEED = 2                   # a crew must hold a SEED x SEED block to claim anything
MIN_TILE_KM = 0.3          # below this a tile is a passing GPS wobble, not a visit


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


def accumulate(db, window_days: int = WINDOW_DAYS, zoom: int = T.DEFAULT_ZOOM) -> dict:
    """{tile: {clan_id: [km, {riders}]}} over the window.

    A trip's distance is split evenly across the tiles it crossed. Weighting by the share
    actually ridden in each tile would be more precise and is not worth a second pass over
    every point: a tile is 2-5 km across, so a ride crosses few enough of them that even
    splitting is within the noise of GPS distance itself.
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
        if pts:
            crossed = T.tiles_along(pts, zoom)
        else:                                   # no stored track: the start tile alone
            t = T.tile_of(slat, slon, zoom)
            crossed = [t] if t else []
        crossed = list(dict.fromkeys(crossed))  # unique, order kept
        if not crossed:
            continue
        share = (km or 0.0) / len(crossed)
        if share < MIN_TILE_KM and len(crossed) > 1:
            # a long ride through many tiles still counts; a 200 m hop that clipped the corner
            # of eight tiles does not get to claim all eight
            keep = max(1, int((km or 0.0) / MIN_TILE_KM))
            crossed = crossed[:keep]
            share = (km or 0.0) / len(crossed)
        for tile in crossed:
            per = acc.setdefault(tile, {})
            e = per.setdefault(clan_id, [0.0, set()])
            e[0] += share
            e[1].add(store_id)
    return acc


def winners(acc: dict, previous: dict | None = None) -> dict[str, tuple[str, float, int]]:
    """{tile: (clan_id, km, riders)} — most kilometres takes the tile.

    Ties go to whoever held it first, which is the only answer that does not hand a tile back
    and forth every rebuild. `previous` is the last round's holders, from `ClanCell`.
    """
    prev = previous or {}
    out = {}
    for tile, per in acc.items():
        best = None
        for clan_id, (km, riders) in per.items():
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


def seeded(held: set[tuple[int, int]]) -> set[tuple[int, int]]:
    """The subset of a crew's tiles that is connected to at least one full SEED x SEED block.

    Everything else is dropped. This is the rule that turns a scattering of won tiles into
    territory somebody can point at.
    """
    if not held:
        return set()
    seeds = set()
    for (x, y) in held:
        block = [(x + dx, y + dy) for dx in range(SEED) for dy in range(SEED)]
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

def rebuild(db, window_days: int = WINDOW_DAYS, zoom: int = T.DEFAULT_ZOOM) -> dict:
    """Recompute every crew's territory and write both outputs. Returns a short report."""
    acc = accumulate(db, window_days, zoom)
    prev = {c.tile: c.clan_id for c in db.query(ClanCell).all()}
    first_led = {(c.tile, c.clan_id): c.first_led for c in db.query(ClanCell).all()}
    won = winners(acc, prev)

    by_clan: dict[str, set] = {}
    for tile, (clan_id, _km, _r) in won.items():
        by_clan.setdefault(clan_id, set()).add(_xy(tile))

    clans = {c.clan_id: c for c in db.query(Clan).filter(Clan.disbanded_at.is_(None)).all()}
    member_counts = dict(
        db.query(ClanMember.clan_id, sa.func.count(ClanMember.store_id))
        .filter(ClanMember.status == "active", ClanMember.left_at.is_(None))
        .group_by(ClanMember.clan_id).all())

    kept: dict[str, set] = {}
    for clan_id, pts in by_clan.items():
        if clan_id not in clans:          # disbanded mid-window: its ground is simply free
            continue
        s = seeded(pts)
        if s:
            kept[clan_id] = s

    # --- ClanCell rows: the admin view and the ranking read these
    db.query(ClanCell).delete()
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
            cells_flat.extend((idx, x, y))
        for comp in regions(pts):
            ex, ey, es = emblem_slot(comp)
            regions_out.append({"c": idx, "e": [ex, ey, es], "n": len(comp)})
        payload_crews.append({
            "id": clan_id, "name": c.name, "slug": c.slug, "colour": c.colour,
            "pattern": c.pattern, "tiles": len(pts), "km2": round(km2, 1),
            "members": member_counts.get(clan_id, 0),
            "emblem": f"/api/v1/crews/{c.slug}/emblem",
        })
    db.commit()

    payload = {
        "z": zoom, "generated": now.isoformat() + "Z", "window_days": window_days,
        "seed": SEED,
        # ranked by ground held, which is the measure the crews are competing on
        "crews": sorted(payload_crews, key=lambda c: -c["km2"]),
        "cells": cells_flat,                    # [crewIndex, x, y, crewIndex, x, y, ...]
        "regions": regions_out,
    }
    # `crews` is sorted for display but `cells` indexes the unsorted order, so the indices are
    # remapped rather than leaving the client to join on two different orderings
    remap = {c["id"]: i for i, c in enumerate(payload["crews"])}
    old_to_new = {i: remap[clan_id] for i, clan_id in enumerate(order)}
    payload["cells"] = [old_to_new[v] if k % 3 == 0 else v
                        for k, v in enumerate(cells_flat)]
    for r in payload["regions"]:
        r["c"] = old_to_new[r["c"]]

    body = json.dumps(payload, separators=(",", ":")).encode()
    p = _out_path(zoom)
    tmp = p.with_suffix(".tmp")
    with gzip.open(tmp, "wb", compresslevel=6) as fh:
        fh.write(body)
    tmp.replace(p)                              # atomic: a reader never sees half a file

    return {"crews": len(payload["crews"]), "tiles": len(won), "held": len(cells_flat) // 3,
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
    """Crews by ground held. One pass over `ClanCell`, no per-crew queries."""
    clans = {c.clan_id: c for c in db.query(Clan).filter(Clan.disbanded_at.is_(None)).all()}
    agg: dict[str, list] = {}
    for clan_id, tile, km in db.query(ClanCell.clan_id, ClanCell.tile, ClanCell.km).all():
        e = agg.setdefault(clan_id, [0, 0.0, 0.0])      # tiles, km2, km ridden
        e[0] += 1
        e[1] += T.area_km2(tile)
        e[2] += km or 0.0
    out = []
    for clan_id, (n, km2, km) in agg.items():
        c = clans.get(clan_id)
        if not c:
            continue
        out.append({"clan_id": clan_id, "name": c.name, "slug": c.slug, "colour": c.colour,
                    "pattern": c.pattern, "tiles": n, "km2": round(km2, 1),
                    "km": round(km, 1)})
    out.sort(key=lambda r: -r["km2"])
    return out[:limit]
