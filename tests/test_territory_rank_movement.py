"""Where a crew stood last hour, so the board can say which way it is going.

Every reviewer in round 20 said some version of "nothing shows change", and named the board's
stillness as what holds the fun score down. The rebuild wrote the standings every pass and
never remembered the previous order, so "up two places" was not computable from anything on
disk.

Two things have to hold or the arrow lies. The rank has to be recorded in the same order the
board sorts on -- squares first, area only to break a tie -- or the arrow disagrees with the
row it is drawn on. And the previous rank has to survive a crew losing everything, because a
crew that drops off the board is exactly the case where "it moved" is worth saying.

The browser half is covered here too, by lifting the shipped `moved()` out of crews.js: it must
draw nothing when there is no previous rank, nothing when a crew has not moved, and the right
direction when it has. A cue that silently never renders is the failure this guard exists for --
the first version read its position from an argument `podList` does not pass.
"""
import json
import pathlib
import shutil
import subprocess
import tempfile
from datetime import datetime, timedelta

import pytest

import models
from ingest.downsample import encode_track
from ingest.parser import Sample
from services import crews, settings, territory
from services.aggregator import Aggregator

ROOT = pathlib.Path(__file__).resolve().parent.parent
CREWS_JS = ROOT / "web" / "static" / "crews.js"

LAT, LON = 48.86, 2.35
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
    db.add(models.Rider(store_id=sid, display_name=sid, platform="google_play", flag="FR"))
    db.commit()
    db.add(models.Trip(trip_uuid=f"seed-{sid}", rider_store_id=sid, distance_km=5.0,
                       validation_status="validated",
                       start_utc=models.utcnow() - timedelta(days=2),
                       end_utc=models.utcnow() - timedelta(days=2)))
    db.commit()
    return crews.create(db, sid, name, "", join_policy="open")


def _ride(db, sid, clan_id, uuid, pts, km=90.0, days_ago=3):
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


def test_the_first_rebuild_leaves_no_previous_rank(db):
    """An arrow drawn from a rank that never existed is a guess."""
    _settings(db)
    a = _crew(db, "par-1", "Rive Droite")
    _ride(db, "par-1", a.clan_id, "a1", _square(0, 0, 3))
    _rebuild(db)
    row = db.get(models.Clan, a.clan_id)
    assert row.terr_rank == 1, f"expected to be ranked first, got {row.terr_rank}"
    assert row.terr_prev_rank is None, (
        "a crew ranked for the first time must have no previous position, or the board draws "
        "movement for a crew that has never moved")


def test_overtaking_is_recorded_in_both_directions(db):
    """The whole point: two crews swap, and each one's row knows which way it went."""
    _settings(db)
    a = _crew(db, "par-2", "Montmartre")
    b = _crew(db, "par-3", "Bastille")
    _ride(db, "par-2", a.clan_id, "b1", _square(0, 0, 4))      # the bigger patch
    _ride(db, "par-3", b.clan_id, "b2", _square(10, 10, 3))
    _rebuild(db)
    first = db.get(models.Clan, a.clan_id).terr_rank
    second = db.get(models.Clan, b.clan_id).terr_rank
    assert first < second, "the fixture did not produce the order it needs"

    # B overtakes A by widening its own patch.
    _ride(db, "par-3", b.clan_id, "b3", _square(10, 10, 5))
    _rebuild(db)
    ra, rb = db.get(models.Clan, a.clan_id), db.get(models.Clan, b.clan_id)
    assert rb.terr_rank < ra.terr_rank, (
        f"B holds {rb.terr_best_tiles} and A {ra.terr_best_tiles}; B should now be above A")
    assert rb.terr_prev_rank == second, f"B's previous rank is {rb.terr_prev_rank}, was {second}"
    assert ra.terr_prev_rank == first, f"A's previous rank is {ra.terr_prev_rank}, was {first}"


def test_the_rank_follows_the_order_the_board_sorts_on(db):
    """Recorded in any other order and the arrow contradicts the row it is drawn on."""
    _settings(db)
    for i, sid in enumerate(("par-4", "par-5", "par-6")):
        c = _crew(db, sid, "Crew " + sid)
        _ride(db, sid, c.clan_id, "r" + sid, _square(i * 8, 0, 3 + i))
    _rebuild(db)
    ranked = territory.ranking(db)
    for place, row in enumerate(ranked, start=1):
        stored = db.query(models.Clan).filter(models.Clan.slug == row["slug"]).first()
        assert stored.terr_rank == place, (
            f"{row['name']} is row {place} of the board and stored as rank "
            f"{stored.terr_rank}; the arrow would be drawn against the wrong row")


def test_a_crew_that_loses_everything_keeps_where_it_came_from(db):
    """Dropping off the board is the case where movement is most worth saying, so the previous
    position must not be cleared with the rest of the standings."""
    _settings(db)
    a = _crew(db, "par-7", "Canal Saint-Martin")
    _ride(db, "par-7", a.clan_id, "c1", _square(0, 0, 3), days_ago=3)
    _rebuild(db)
    was = db.get(models.Clan, a.clan_id).terr_rank
    assert was == 1

    for trip in db.query(models.Trip).filter(models.Trip.clan_id == a.clan_id).all():
        trip.start_utc = models.utcnow() - timedelta(days=900)
        trip.end_utc = trip.start_utc
    db.commit()
    territory.rebuild(db, window_days=30, zoom=Z, seed=2)

    row = db.get(models.Clan, a.clan_id)
    assert row.terr_best_tiles == 0, "the fixture did not actually drop the crew off the board"
    assert row.terr_rank is None, "a crew holding nothing is off the board, not last on it"
    assert row.terr_prev_rank == was, (
        f"it came from {was} and the row says {row.terr_prev_rank}; the one case where "
        "movement matters most has forgotten where it moved from")


def test_the_ranking_carries_the_previous_rank(db):
    """It has to reach the browser, which reads `ranking()` rather than the Clan row."""
    _settings(db)
    a = _crew(db, "par-8", "Belleville")
    _ride(db, "par-8", a.clan_id, "d1", _square(0, 0, 3))
    _rebuild(db)
    _rebuild(db)
    row = next(r for r in territory.ranking(db) if r["slug"] == a.slug)
    assert "prev_rank" in row, (
        "the ranking does not carry prev_rank, so the board can never draw movement")
    assert row["prev_rank"] == db.get(models.Clan, a.clan_id).terr_prev_rank


# --- the browser half ---------------------------------------------------------------------

START = "  function moved(e, i) {"
END = "  function rankingHTML(rows) {"

HARNESS = """
%(region)s
const cases = [
  { name: "no previous rank",      e: { prev_rank: null }, i: 0 },
  { name: "unchanged",             e: { prev_rank: 3 },    i: 2 },
  { name: "up two",                e: { prev_rank: 5 },    i: 2 },
  { name: "down one",              e: { prev_rank: 2 },    i: 2 },
  { name: "back onto the board",   e: { prev_rank: 9 },    i: 0 },
  { name: "no index",              e: { prev_rank: 4 },    i: undefined },
  { name: "null row",              e: null,                i: 0 },
];
console.log(JSON.stringify(cases.map(c => ({ name: c.name, html: moved(c.e, c.i) }))));
"""

pytestmark_node = pytest.mark.skipif(shutil.which("node") is None,
                                     reason="needs node to run the shipped js")


def _moved():
    src = CREWS_JS.read_text(encoding="utf-8")
    i = src.index(START)
    j = src.index(END, i)
    region = src[i:j]
    assert "prev_rank" in region, "the lifted region is not the movement helper"
    assert "aria-hidden" in region, (
        "the cue is not aria-hidden; it duplicates a rank that is already announced")
    d = pathlib.Path(tempfile.mkdtemp(prefix="moved-"))
    try:
        f = d / "t.mjs"
        f.write_text(HARNESS % {"region": region}, encoding="utf-8")
        r = subprocess.run(["node", str(f)], capture_output=True, text=True, encoding="utf-8")
        assert r.returncode == 0, (r.stdout or "") + (r.stderr or "")
        return {row["name"]: row["html"] for row in json.loads(r.stdout)}
    finally:
        shutil.rmtree(d, ignore_errors=True)


@pytestmark_node
def test_nothing_is_drawn_without_something_true_to_say():
    out = _moved()
    assert out["no previous rank"] == "", "a crew ranked once must get no arrow"
    assert out["unchanged"] == "", (
        "an arrow on a crew that has not moved is the badge-on-every-row mistake again")
    assert out["null row"] == "", "a missing row must not throw or draw"


@pytestmark_node
def test_the_direction_and_the_distance_are_both_right():
    out = _moved()
    up = out["up two"]
    # Rank 5 -> 3 is two places up: the glyph carries the direction, the digit the distance.
    assert "▲" in up, f"up two has no up arrow: {up!r}"
    assert "▲2" in up, f"up two should read two places, got {up!r}"
    assert "crewmoved up" in up and "down" not in up, up
    down = out["down one"]
    assert "▼1" in down, f"down one rendered {down!r}"
    assert "crewmoved down" in down, down


@pytestmark_node
def test_the_cue_does_not_speak_over_the_rank():
    """It is a second reading of a number already announced beside it."""
    out = _moved()
    assert 'aria-hidden="true"' in out["up two"]


@pytestmark_node
def test_a_missing_index_draws_nothing_rather_than_guessing():
    """The first version read the position from an argument `podList` does not pass, so it
    rendered nothing on every row forever. If that ever comes back, this says so."""
    out = _moved()
    assert out["no index"] == "", (
        "with no position the helper invented one; it must draw nothing instead")
