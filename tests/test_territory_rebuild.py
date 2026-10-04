"""`rebuild()` itself, which nothing called.

The suite had territory covered in depth — flood fill, contact, ties, recency, emblem
placement, the shortfall arithmetic — and every one of those tests calls a pure function.
Nothing called `rebuild()`, the thing that actually runs every hour and writes both outputs.
So this went unnoticed:

    KeyError: 'bbd07099bffa48918424c492e1a59bfb'
      services/territory.py  rivals_flat.extend((len(cells_flat) // 5, idx_of[pushing]))

`order`, and therefore `idx_of`, is built from the crews that KEPT ground. `pushing` comes out
of `_pressure()` and is whoever is riding hardest on somebody else's square — which need hold
no ground at all. A crew founded yesterday whose riders have been down the same street as an
established crew is the ordinary case, and it raised KeyError and aborted the whole rebuild:
no standings columns written, no map payload written, and the previous files left in place.

That is why a reviewer found the board listing 13 crews while the map payload had 16, three of
them with real ground, and a chip naming a crew that appeared nowhere else in the panel. It
read like two endpoints disagreeing by design. It was one crash, silently retried every hour,
leaving the two outputs stale in different directions.

So: run the real thing, on the shape that broke it, and check the invariant the crash was
hiding — every crew index written into `rivals` has to be an index into the crew list that
ships beside it.
"""
import gzip
import json
from datetime import datetime, timedelta

import models
from ingest.downsample import encode_track
from ingest.parser import Sample
from services import crews, settings, territory
from services.aggregator import Aggregator

# A corner of the world nothing else in the suite uses, so tiles cannot collide with another
# test's fixtures.
LAT, LON = 59.33, 18.07
Z = 14


def _crew(db, sid, name, policy="open"):
    db.add(models.Rider(store_id=sid, display_name=sid, platform="google_play", flag="SE"))
    db.commit()
    db.add(models.Trip(trip_uuid=f"seed-{sid}", rider_store_id=sid, distance_km=5.0,
                       validation_status="validated",
                       start_utc=models.utcnow() - timedelta(days=2),
                       end_utc=models.utcnow() - timedelta(days=2)))
    db.commit()
    return crews.create(db, sid, name, "", join_policy=policy)


def _ride(db, sid, clan_id, uuid, pts, km=40.0, days_ago=1):
    """A validated ride with a real track, credited to a crew."""
    when = models.utcnow() - timedelta(days=days_ago)
    db.add(models.Trip(trip_uuid=uuid, rider_store_id=sid, clan_id=clan_id,
                       validation_status="validated", distance_km=km,
                       start_lat=pts[0][0], start_lon=pts[0][1],
                       start_utc=when, end_utc=when))
    db.commit()
    samples = [Sample(t=datetime(2026, 6, 1, 10, 0, i % 60), lat=la, lon=lo, speed=20.0)
               for i, (la, lo) in enumerate(pts)]
    db.add(models.TripTrack(trip_uuid=uuid, points=encode_track(samples)))
    db.commit()
    Aggregator(db).apply(db.get(models.Trip, uuid))


# Points placed from the tiler, not from a guessed degree step. My first version used 0.004
# degrees, which at z14 does not reach the next tile: the crew won tiles and KEPT none, so
# `order` was empty, the payload loop never ran, and the test passed with the fix removed
# while asserting `tiles > 0` -- a number that counts tiles won, not tiles held.
_BASE = territory.T.parse(territory.T.tile_of(LAT, LON, Z))


def _tile(dx, dy):
    return "%d/%d/%d" % (Z, _BASE[1] + dx, _BASE[2] + dy)


def _in_tiles(cells, dens=5):
    """Several points well inside each named tile, so each one gets real track length."""
    pts = []
    for (dx, dy) in cells:
        w, s, e, n = territory.T.bounds(_tile(dx, dy))
        for k in range(dens):
            f = 0.2 + 0.6 * k / max(1, dens - 1)
            pts.append((s + (n - s) * f, w + (e - w) * f))
    return pts


def _block(n=3):
    """An n x n run of adjacent tiles — enough to survive the seed rule and be kept."""
    return _in_tiles([(x, y) for y in range(n) for x in range(n)])


def _settings(db):
    settings.set_crews(db, enabled=True, zoom=Z, window_days=90, seed=2, cooldown_days=7,
                       max_members=0, opacity=0.55, creation_open=True)


def test_a_crew_that_holds_nothing_cannot_abort_the_rebuild(db, monkeypatch):
    """The regression, with its precondition injected rather than built out of geometry.

    `_pressure` only names a rival that is within one ride of holding a block — the "three
    corners" case — so reproducing it from trips means reverse-engineering the tiler until a
    crew sits in exactly that window. My first attempt at that passed with the fix removed,
    which is a test asserting its own fixture rather than the code.

    So the rival is named directly. That is the whole precondition: `_pressure` returns a
    clan_id, `order` is built from the crews that KEPT ground, and the two sets are not the
    same. Patching the one function that produces the name tests the one line that used the
    name, and it fails without the fix.
    """
    _settings(db)
    holder = _crew(db, "holder", "Holder Crew")
    _ride(db, "holder", holder.clan_id, "h-1", _block(3), km=60.0, days_ago=3)

    ghost = "ghost-crew-holding-nothing-at-all"
    real = territory._pressure

    def names_a_landless_crew(acc, tile, holder_id, held_km, blocked=None, seedless=None):
        band, n, _ = real(acc, tile, holder_id, held_km, blocked, seedless)
        # band 1 is "somebody is riding it", which is when the rival marker gets written
        return (1, n, ghost)

    monkeypatch.setattr(territory, "_pressure", names_a_landless_crew)
    rep = territory.rebuild(db, window_days=90, zoom=Z, seed=2)
    payload = json.loads(gzip.decompress(territory.cached(Z)))
    # `tiles` counts tiles WON; a crew can win tiles and keep none, and then `order` is empty
    # and the line this test is about never runs. This is the assertion that proves it ran.
    held = [x for x in payload["crews"] if x.get("best_tiles")]
    assert held, "no crew kept any ground, so the payload loop never ran and this proves nothing"
    assert ghost not in {x["slug"] for x in payload["crews"]},         "a crew that holds nothing must not appear in the payload"

    # And the crash's quieter sibling: an index that is present but wrong points the map's
    # rival marker at whichever crew happens to sit at that position.
    payload = json.loads(gzip.decompress(territory.cached(Z)))
    rivals = payload.get("rivals") or []
    n = len(payload.get("crews") or [])
    bad = [rivals[i + 1] for i in range(0, len(rivals), 2)
           if not (0 <= rivals[i + 1] < n)]
    assert not bad, f"rival markers point at crew indexes that do not exist: {bad} (crews: {n})"


def test_the_standings_columns_and_the_map_payload_say_the_same_thing(db):
    """The invariant the crash was hiding.

    `/crews/ranking/all` reads the stored `terr_*` columns and filters on `terr_tiles > 0`;
    the map, the ride targets and the "takes you past" chips read the cached payload. One
    rebuild writes both, so they can only disagree when a rebuild did not finish — which is
    exactly what happened, and it surfaced as a crew on the map with no row on the board and
    a printed rank that was a number the panel had invented.
    """
    _settings(db)
    a = _crew(db, "aa", "Alpha Crew")
    b = _crew(db, "bb", "Beta Crew")
    c = _crew(db, "cc", "Gamma Crew")        # rides, holds nothing
    _ride(db, "aa", a.clan_id, "a-1", _block(3), km=60.0, days_ago=3)
    _ride(db, "bb", b.clan_id, "b-1", _in_tiles([(x + 8, y + 8) for y in range(3) for x in range(3)]), km=55.0, days_ago=3)
    _ride(db, "cc", c.clan_id, "c-1", _in_tiles([(0, 0), (1, 0)]), km=6.0, days_ago=1)

    territory.rebuild(db, window_days=90, zoom=Z, seed=2)

    ranked = {r["slug"]: r["best_tiles"] for r in territory.ranking(db, 100)}
    payload = json.loads(gzip.decompress(territory.cached(Z)))
    mapped = {x["slug"]: x["best_tiles"] for x in payload["crews"] if x.get("best_tiles")}

    assert ranked == mapped, (
        "the board and the map disagree about who holds what.\n"
        f"  board only: {sorted(set(ranked) - set(mapped))}\n"
        f"  map only:   {sorted(set(mapped) - set(ranked))}\n"
        f"  differing:  { {k: (ranked[k], mapped[k]) for k in set(ranked) & set(mapped) if ranked[k] != mapped[k]} }")


def test_a_rebuild_with_no_crews_at_all_still_writes_its_outputs(db):
    """The empty case, because the first thing a fresh install does is run this."""
    _settings(db)
    rep = territory.rebuild(db, window_days=90, zoom=Z, seed=2)
    assert rep["crews"] == 0 and rep["tiles"] == 0
    payload = json.loads(gzip.decompress(territory.cached(Z)))
    assert payload["crews"] == [] and payload["cells"] == []
