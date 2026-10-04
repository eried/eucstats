"""The weekly delta the board prints beside the ranked figure, counted over the same ground.

The board ranks crews on their biggest unbroken patch and prints a weekly gain next to it.
Those two numbers were counted over different ground: the figure is the biggest patch, the
delta was every fresh cell the crew held anywhere. A row read "29 squares, +49 squares this
week" -- two quantities, one noun, a plus between them.

My first fix clamped the delta at the ranked figure. That makes the row arithmetically possible
and still untrue, which is worse, because nothing on screen looks wrong any more: a reviewer
measured a crew with 49 fresh cells spread over 3 patches, biggest patch 29, printing
"29 squares, +29 squares this week" -- reading as though that entire block had been won in the
last seven days, when the real gain inside it could be zero.

So `rebuild()` counts the intersection, where the components already exist, and the browser
prints what it is given. The shape that matters is a crew whose fresh ground is NOT in its
ranked patch, because that is the case both the old versions got wrong and the only one where
clamping and counting give different answers.
"""
from datetime import datetime, timedelta

import models
from ingest.downsample import encode_track
from ingest.parser import Sample
from services import crews, settings, territory
from services.aggregator import Aggregator

# Its own corner of the world, away from the other territory fixtures.
LAT, LON = 43.77, 11.25
Z = 14

_BASE = territory.T.parse(territory.T.tile_of(LAT, LON, Z))


def _tile(dx, dy):
    return "%d/%d/%d" % (Z, _BASE[1] + dx, _BASE[2] + dy)


def _in_tiles(cells, dens=5):
    pts = []
    for (dx, dy) in cells:
        w, s, e, n = territory.T.bounds(_tile(dx, dy))
        for k in range(dens):
            f = 0.2 + 0.6 * k / max(1, dens - 1)
            pts.append((s + (n - s) * f, w + (e - w) * f))
    return pts


def _square(x0, y0, n):
    return _in_tiles([(x0 + x, y0 + y) for y in range(n) for x in range(n)])


def _crew(db, sid, name):
    db.add(models.Rider(store_id=sid, display_name=sid, platform="google_play", flag="IT"))
    db.commit()
    db.add(models.Trip(trip_uuid=f"seed-{sid}", rider_store_id=sid, distance_km=5.0,
                       validation_status="validated",
                       start_utc=models.utcnow() - timedelta(days=2),
                       end_utc=models.utcnow() - timedelta(days=2)))
    db.commit()
    return crews.create(db, sid, name, "", join_policy="open")


def _ride(db, sid, clan_id, uuid, pts, km=60.0, days_ago=1):
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


def _settings(db):
    settings.set_crews(db, enabled=True, zoom=Z, window_days=365, seed=2, cooldown_days=7,
                       max_members=0, opacity=0.55, creation_open=True)


def _rebuild(db):
    return territory.rebuild(db, window_days=365, zoom=Z, seed=2)


def _age_held_ground(db, days=60):
    """Backdate the ground already held, so the next rebuild sees it as old.

    Freshness is `first_led`, which the first rebuild stamps from the ride that won the tile
    and falls back to `now` for -- so a fixture that only sets ride dates can come out with
    every tile stamped today and therefore every tile fresh on the next pass. That is what
    this fixture did first, and the test then passed for the wrong reason: 6 of 6 squares in
    the ranked patch counted as gained this week.

    Setting the dates directly makes the precondition the test's own rather than something
    inferred through `accumulate`, `award` and the tiler. The same reasoning as naming the
    rival directly in `test_territory_rebuild.py`.
    """
    when = models.utcnow() - timedelta(days=days)
    for cell in db.query(models.ClanCell).all():
        cell.first_led = when
    db.commit()


def test_fresh_ground_outside_the_ranked_patch_is_not_counted_as_gained(db):
    """The measured case, as a fixture: the new ground is in the SMALLER patch.

    Nothing is fresh on a first rebuild -- there is no previous state for it to differ from --
    so this rides the big patch, rebuilds to establish that state, then rides a detached patch
    and rebuilds again. The second patch is then genuinely new ground, and it is genuinely not
    in the patch the board ranks this crew on.
    """
    _settings(db)
    c = _crew(db, "ital-1", "Arno Riders")

    # Ridden long ago, so it is held and old. 3x3 keeps 9 tiles.
    _ride(db, "ital-1", c.clan_id, "old-block", _square(0, 0, 3), km=90.0, days_ago=60)
    _rebuild(db)
    _age_held_ground(db)

    row = db.get(models.Clan, c.clan_id)
    assert row.terr_best_tiles >= 4, "the big patch was not kept; the fixture proves nothing"
    big = row.terr_best_tiles

    # A separate 2x2 six tiles east -- far enough not to touch, so it is its own component --
    # ridden yesterday.
    _ride(db, "ital-1", c.clan_id, "new-block", _square(6, 0, 2), km=60.0, days_ago=1)
    _rebuild(db)

    row = db.get(models.Clan, c.clan_id)
    assert row.terr_regions >= 2, (
        f"expected two separate patches, got {row.terr_regions}: the new ground joined the old "
        "patch, so there is no 'fresh outside the ranked patch' to measure")
    assert row.terr_best_tiles == big, (
        "the ranked patch changed size, so this no longer isolates the delta")

    # The whole point. The crew gained ground this week, and none of it is in the patch the
    # board ranks it on, so the figure printed beside that patch is zero.
    assert row.terr_best_fresh == 0, (
        f"terr_best_fresh is {row.terr_best_fresh}, but every fresh square this crew holds is "
        f"in its smaller patch. The board prints this next to '{big} squares', so any non-zero "
        "value here says that block was won this week when it was won two months ago. This is "
        "what clamping the whole-holding count at the ranked figure produced.")


def test_fresh_ground_inside_the_ranked_patch_is_counted(db):
    """The mirror. A delta of zero for a crew that really did extend its best patch would be
    the same fault facing the other way, and a clamp would have passed this one by accident."""
    _settings(db)
    c = _crew(db, "ital-2", "Ponte Crew")

    _ride(db, "ital-2", c.clan_id, "p-old", _square(0, 0, 3), km=90.0, days_ago=60)
    _rebuild(db)
    _age_held_ground(db)
    before = db.get(models.Clan, c.clan_id).terr_best_tiles

    # Adjacent, so it grows the same component rather than starting a new one.
    _ride(db, "ital-2", c.clan_id, "p-new", _square(0, 3, 2), km=60.0, days_ago=1)
    _rebuild(db)

    row = db.get(models.Clan, c.clan_id)
    assert row.terr_best_tiles > before, (
        "the ranked patch did not grow, so there is no gain inside it to count")
    assert row.terr_best_fresh > 0, (
        "the crew extended the patch the board ranks it on this week and the delta says 0")
    assert row.terr_best_fresh <= row.terr_best_tiles, (
        f"{row.terr_best_fresh} fresh squares inside a {row.terr_best_tiles}-square patch")


def test_the_delta_never_exceeds_the_figure_it_sits_beside(db):
    """The invariant, stated plainly, over every crew the rebuild writes."""
    _settings(db)
    a = _crew(db, "ital-3", "Oltrarno")
    b = _crew(db, "ital-4", "Fiesole Flyers")
    _ride(db, "ital-3", a.clan_id, "a-1", _square(0, 0, 3), km=90.0, days_ago=40)
    _ride(db, "ital-4", b.clan_id, "b-1", _square(10, 10, 3), km=90.0, days_ago=40)
    _rebuild(db)
    _age_held_ground(db)
    _ride(db, "ital-3", a.clan_id, "a-2", _square(6, 0, 2), km=60.0, days_ago=2)
    _ride(db, "ital-4", b.clan_id, "b-2", _square(10, 13, 2), km=60.0, days_ago=2)
    _rebuild(db)

    for row in db.query(models.Clan).all():
        assert (row.terr_best_fresh or 0) <= (row.terr_best_tiles or 0), (
            f"{row.name}: +{row.terr_best_fresh} this week beside {row.terr_best_tiles} "
            "squares -- a crew cannot have gained more of a patch than the patch holds")


def test_the_ranking_carries_the_counted_delta(db):
    """It has to reach the browser, which reads `ranking()` and not the Clan row."""
    _settings(db)
    c = _crew(db, "ital-5", "Cascine")
    _ride(db, "ital-5", c.clan_id, "c-1", _square(0, 0, 3), km=90.0, days_ago=60)
    _rebuild(db)
    _age_held_ground(db)
    _ride(db, "ital-5", c.clan_id, "c-2", _square(6, 0, 2), km=60.0, days_ago=1)
    _rebuild(db)

    rows = territory.ranking(db)
    assert rows, "the ranking is empty"
    mine = next(r for r in rows if r["slug"] == c.slug)
    assert "best_fresh" in mine, (
        "the ranking does not carry best_fresh, so the board falls back to the clamp and the "
        "row goes on contradicting itself")
    assert mine["best_fresh"] == db.get(models.Clan, c.clan_id).terr_best_fresh
    assert mine["best_fresh"] <= mine["best_tiles"]


def test_a_crew_that_loses_everything_reports_no_gain(db):
    """The column is cleared with the others. Left stale, a crew with nothing would keep
    last hour's delta beside a figure of zero."""
    _settings(db)
    c = _crew(db, "ital-6", "Rifredi")
    _ride(db, "ital-6", c.clan_id, "r-1", _square(0, 0, 3), km=90.0, days_ago=2)
    _rebuild(db)
    _rebuild(db)

    # Nothing inside the window any more.
    for trip in db.query(models.Trip).filter(models.Trip.clan_id == c.clan_id).all():
        trip.start_utc = models.utcnow() - timedelta(days=900)
        trip.end_utc = trip.start_utc
    db.commit()
    territory.rebuild(db, window_days=30, zoom=Z, seed=2)

    row = db.get(models.Clan, c.clan_id)
    assert row.terr_best_tiles == 0 and (row.terr_best_fresh or 0) == 0, (
        f"a crew holding nothing reports +{row.terr_best_fresh} this week")
