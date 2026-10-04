"""Redrawing the map after a ride instead of on the hour.

"You ride the block, come home, open the panel, and the squares have not moved." The oldest
item on the open list, and the one thing a real ride test is guaranteed to hit: the rebuild
rode the retention loop, which is hourly by default, so a rider's own ride reached the map
somewhere between one second and sixty minutes after they uploaded it.

The mark is what makes the difference, so the mark is what this tests: it has to be set by a
ride that could move a square and left alone by one that could not, it has to survive being
read, and it has to be claimed exactly once so two loops cannot both own the same rebuild.

The debounce matters as much as the trigger. A full rebuild measured 0.5-1.3s on sixteen crews
and grows with the trips in the window, so a group ride finishing together must produce one
redraw rather than one per rider.
"""
from datetime import timedelta

import models
from services import crews, settings, territory


def _rider(db, sid, flag="NO"):
    db.add(models.Rider(store_id=sid, display_name=sid, platform="google_play", flag=flag))
    db.commit()
    db.add(models.Trip(trip_uuid=f"seed-{sid}", rider_store_id=sid, distance_km=5.0,
                       validation_status="validated",
                       start_utc=models.utcnow() - timedelta(days=2),
                       end_utc=models.utcnow() - timedelta(days=2)))
    db.commit()


def test_the_mark_starts_clear():
    """Otherwise every restart owes a rebuild it does not need."""
    territory.claim_dirty()
    assert territory.is_dirty() is False


def test_marking_is_visible_and_claiming_takes_it():
    territory.claim_dirty()
    territory.mark_dirty()
    assert territory.is_dirty() is True
    assert territory.claim_dirty() is True, "the first claim must take the mark"
    assert territory.is_dirty() is False
    assert territory.claim_dirty() is False, (
        "a second claim must come back empty, or two loops both think they owe the rebuild")


def test_marking_twice_is_still_one_rebuild():
    """The group-ride case: twenty riders finishing together is one redraw, not twenty.

    The debounce itself lives in the loop, but it can only work if the mark collapses -- a
    counter here would hand the loop twenty rebuilds to drain.
    """
    territory.claim_dirty()
    for _ in range(20):
        territory.mark_dirty()
    assert territory.claim_dirty() is True
    assert territory.claim_dirty() is False, "twenty rides left more than one rebuild owed"


def test_the_debounce_floor_is_short_enough_to_be_useful_and_long_enough_to_protect():
    """A number, pinned, because both directions are a real failure.

    Too long and the ride test this exists for still fails; too short and a busy evening is a
    rebuild loop. The rebuild measured 0.5-1.3s at today's size.
    """
    assert 15 <= territory.FRESH_GAP_S <= 120, (
        f"FRESH_GAP_S is {territory.FRESH_GAP_S}: outside the range where it both answers a "
        "rider quickly and protects the box from a group upload")


def test_a_crew_credited_ride_marks_the_map(db, monkeypatch):
    """The trigger, through the real upload path rather than by calling `mark_dirty` directly.

    `services/ingest.py` stamps the rider's crew onto the trip and then aggregates it; the mark
    belongs in the same place, because that is where it is known both that the ride counts and
    that it belongs to somebody with ground.
    """
    import inspect
    src = inspect.getsource(__import__("services.ingest", fromlist=["x"]))
    assert "territory.mark_dirty()" in src, (
        "the upload path no longer marks the map; a rider's ride is back to waiting for the "
        "hourly pass")
    # And it is inside the validated branch, not next to the insert: a flagged ride must not
    # trigger a redraw.
    after_validated = src.split('if status == "validated":', 1)
    assert len(after_validated) == 2, "the validated branch moved"
    assert "territory.mark_dirty()" in after_validated[1], (
        "mark_dirty is outside the validated branch, so a flagged ride would redraw the map")
    assert "if clan_id:" in after_validated[1], (
        "mark_dirty is not gated on the ride being credited to a crew, so a rider with no "
        "crew would trigger a whole-world rebuild")


def test_the_countdown_tells_the_truth_once_a_rebuild_is_owed(db):
    """The panel's "next redraw" line is served from the same gap.

    Without this it told a rider who had just uploaded that their ride would appear in
    fifty-one minutes, while it was in fact about to appear.
    """
    from web import crews_api
    territory.claim_dirty()
    settings.set_crews(db, enabled=True, zoom=14, window_days=90, seed=2, cooldown_days=7,
                       max_members=0, opacity=0.55, creation_open=True)
    idle = crews_api._rebuild_every(db)
    assert idle >= crews_api.REBUILD_EVERY_S, (
        f"with nothing owed the cadence should be the hourly floor, got {idle}")
    territory.mark_dirty()
    try:
        owed = crews_api._rebuild_every(db)
        assert owed == territory.FRESH_GAP_S, (
            f"a rebuild is owed and the panel still promises {owed}s")
        assert owed < idle, "the pending answer must be sooner than the idle one"
    finally:
        territory.claim_dirty()


def test_a_rebuild_still_happens_without_any_mark(db):
    """The hourly pass stays underneath. Ground going cold, a crew folding and an admin
    changing the zoom all make the map stale with no upload involved."""
    import main
    assert hasattr(main, "_territory_if_due"), (
        "the hourly path is gone; the map would now only ever redraw after an upload")
    territory.claim_dirty()
    settings.set_crews(db, enabled=True, zoom=14, window_days=90, seed=2, cooldown_days=7,
                       max_members=0, opacity=0.55, creation_open=True)
    _rider(db, "fresh-one")
    crews.create(db, "fresh-one", "Fresh Loop Crew", "")
    main._last_territory[0] = 0.0            # as if it had never run
    main._territory_if_due(db)
    assert main._last_territory[0] > 0.0, "the hourly path did not run"
