"""One ranking rule, on every screen that shows a rank.

Erwin asked what happens to a crew holding 20 squares as two patches of 10 against a crew
holding 11 in one piece, and expected one clever formula to answer it. There were two. The
board sorted on the biggest patch alone and the champions card sorted on the weighted score,
so his example is precisely where they parted company: the board said the 11 (11 > 10) and the
card said the 20 (15.0 > 13.75). Two screens, two winners, no way for a rider to tell which
one counted.

The rule is now `crews.standing_score` everywhere -- the biggest patch at 1.0 a square and
everything else the crew holds at 0.25. Area was a third term at 0.05 per km² and came out
after being measured: it delivered the spread-beats-tight case it was kept for, and it also
put a crew holding thirteen squares above one holding fourteen on latitude alone. These tests exist because that
is five code paths agreeing by convention: the rebuild that stores `terr_rank`, the board
query, the join list, the client's own sort, and the sentence on your own card that converts a
score gap back into squares. Any one of them drifting puts a crew at a different place on a
different screen, which is the bug this replaced.

The fixture is built so the OLD rule and the NEW rule disagree about it -- asserted, not
assumed. A fixture both rules order the same way would pass whatever the code did.
"""
import pathlib
import re
from datetime import datetime, timedelta

import models
from ingest.downsample import encode_track
from ingest.parser import Sample
from services import crews, settings, territory
from services.aggregator import Aggregator

ROOT = pathlib.Path(__file__).resolve().parent.parent
CREWS_JS = ROOT / "web" / "static" / "crews.js"
CREWS_PY = ROOT / "services" / "crews.py"

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


def _block(x0, y0, w, h):
    return [(x0 + x, y0 + y) for y in range(h) for x in range(w)]


def _crew(db, sid, name):
    db.add(models.Rider(store_id=sid, display_name=sid, platform="google_play", flag="FR"))
    db.commit()
    db.add(models.Trip(trip_uuid=f"seed-{sid}", rider_store_id=sid, distance_km=5.0,
                       validation_status="validated",
                       start_utc=models.utcnow() - timedelta(days=2),
                       end_utc=models.utcnow() - timedelta(days=2)))
    db.commit()
    return crews.create(db, sid, name, "", join_policy="open")


def _ride(db, sid, clan_id, uuid, cells, km=90.0, days_ago=3):
    pts = _in_tiles(cells)
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


def _two_patches_against_one(db):
    """Erwin's case as a fixture: SPREAD holds two patches of 16 in two places, SOLID holds 17
    in one piece. His own numbers were 10+10 against 11; these are the same shape with room
    either side, because the track claims the squares it crosses between visits and a fixture
    pinned to an exact count breaks on a rounding change somewhere else entirely.

    Measured after the rebuild: spread 16 + 32 squares over 82.9 km² scores 28.15, solid 17 +
    17 over 45.1 km² scores 23.51. The old rule picks solid (17 > 16), the new one picks
    spread -- and on the patch-and-squares terms alone, before area, it is still 24.00 to
    21.25, so this does not pass on the area tiebreak.
    """
    _settings(db)
    a = _crew(db, "spread-1", "Two Cities")
    b = _crew(db, "solid-1", "One Block")
    # Two 4x4 patches, 24 squares apart, so nothing bridges them into one component.
    _ride(db, "spread-1", a.clan_id, "sp-1", _block(0, 0, 4, 4))
    _ride(db, "spread-1", a.clan_id, "sp-2", _block(24, 0, 4, 4))
    # 4x4 and one more square welded to its edge, ridden as its own trip so the ride between
    # them does not wander into a third.
    _ride(db, "solid-1", b.clan_id, "so-1", _block(0, 40, 4, 4))
    _ride(db, "solid-1", b.clan_id, "so-2", [(3, 40), (4, 40)])
    territory.rebuild(db, window_days=365, zoom=Z, seed=2)
    return db.get(models.Clan, a.clan_id), db.get(models.Clan, b.clan_id)


def test_the_fixture_is_a_case_the_two_rules_disagree_about(db):
    """Guard on the guard. Every test below is worthless if both rules order these the same."""
    spread, solid = _two_patches_against_one(db)
    assert (spread.terr_best_tiles, spread.terr_tiles, spread.terr_regions) == (16, 32, 2), (
        f"the spread crew holds {spread.terr_tiles} squares in {spread.terr_regions} patches, "
        f"the biggest of {spread.terr_best_tiles}; the fixture wants 32 in 2 patches of 16")
    assert (solid.terr_best_tiles, solid.terr_regions) == (17, 1), (
        f"the solid crew holds {solid.terr_tiles} squares in {solid.terr_regions} patches, "
        f"the biggest of {solid.terr_best_tiles}; the fixture wants 17 in one piece")
    # The whole formula now, stated here so a changed weight shows up as a failing fixture
    # rather than as a quietly different board.
    bare = lambda c: c.terr_best_tiles + 0.25 * c.terr_tiles
    assert bare(spread) > bare(solid), (
        f"patch and squares give solid {bare(solid):.2f} against spread "
        f"{bare(spread):.2f}; the fixture no longer shows what it was built to show")
    # the old rule
    assert solid.terr_best_tiles > spread.terr_best_tiles, (
        "biggest-patch-only would not put the solid crew first, so this fixture cannot show "
        "that the rule changed")
    # the new one
    assert crews.standing_score(spread) > crews.standing_score(solid), (
        f"the weighted standing puts the solid crew first too "
        f"({crews.standing_score(solid):.2f} vs {crews.standing_score(spread):.2f}); the "
        f"weights have moved far enough that Erwin's example no longer comes out his way")


def test_the_board_ranks_on_the_weighted_standing(db):
    """`territory.ranking` -- what "Who owns the streets" draws."""
    spread, solid = _two_patches_against_one(db)
    rows = territory.ranking(db)
    order = [r["slug"] for r in rows]
    assert order.index(spread.slug) < order.index(solid.slug), (
        f"the board put the solid crew first: {order}. It is sorting on the biggest patch "
        f"again, not on the standing.")
    assert rows[0]["score"] == round(crews.standing_score(spread), 2), (
        "the row does not carry the score it was ordered by, so the client cannot work out "
        "the gap to the crew above without re-deriving it from a different rule")


def test_the_stored_rank_matches_the_board(db):
    """The rebuild writes `terr_rank`, and the arrows are drawn from it. Recorded in any other
    order than the board's and every arrow contradicts the row it sits on."""
    spread, solid = _two_patches_against_one(db)
    assert (spread.terr_rank, solid.terr_rank) == (1, 2), (
        f"stored ranks are spread={spread.terr_rank} solid={solid.terr_rank}; the board has "
        f"them the other way round")
    board = [r["slug"] for r in territory.ranking(db)]
    stored = [c.slug for c in sorted((spread, solid), key=lambda c: c.terr_rank)]
    assert board == stored, f"board {board} against stored {stored}"


def test_the_join_list_is_in_the_same_order(db):
    """`/api/v1/crews`. These two lists are read within seconds of each other by somebody
    deciding who to join, and they used to be sorted by different rules."""
    spread, solid = _two_patches_against_one(db)
    rows = (db.query(models.Clan).filter(models.Clan.disbanded_at.is_(None))
            .order_by(*crews.standing_order_by()).all())
    order = [c.slug for c in rows if c.terr_tiles]
    assert order.index(spread.slug) < order.index(solid.slug), (
        f"the join list disagrees with the board: {order}")


def test_sql_and_python_orderings_cannot_drift(db):
    """Two implementations of one rule: `standing_order_by` for the queries that LIMIT, and
    `standing_sort_key` for the rebuild, which has the objects in hand already."""
    _two_patches_against_one(db)
    rows = (db.query(models.Clan).filter(models.Clan.terr_tiles > 0)
            .order_by(*crews.standing_order_by()).all())
    by_sql = [c.slug for c in rows]
    by_py = [c.slug for c in sorted(rows, key=crews.standing_sort_key)]
    assert by_sql == by_py, f"SQL says {by_sql}, Python says {by_py}"


def test_a_score_gap_converts_to_squares_a_rider_can_go_and_get():
    """"4 squares off 7th" has to mean four squares. The gap is in score; a square welded to
    your biggest patch is worth 1.25 of it (1.0 for the patch, 0.25 for holding it)."""
    assert crews.SCORE_PER_CONNECTED_SQUARE == 1.25
    assert crews.squares_to_close(0) == 0, "level is level, not one square behind"
    assert crews.squares_to_close(-3) == 0, "ahead is not behind"
    # Ceiling, never rounding: a third of a square's worth behind still takes a square.
    assert crews.squares_to_close(0.05) == 1
    assert crews.squares_to_close(1.25) == 1
    assert crews.squares_to_close(1.26) == 2
    assert crews.squares_to_close(5.0) == 4


def test_the_client_derives_every_rank_from_one_comparator():
    """Five copies of the old comparator lived in crews.js, each deriving a rank of its own.
    That is how your own card came to read "4th" about a row the board had drawn fifth."""
    js = CREWS_JS.read_text(encoding="utf-8")
    assert "function byStanding(" in js, "the shared comparator is gone"
    strays = [m.start() for m in
              re.finditer(r"\(b\.best_tiles \|\| 0\) - \(a\.best_tiles \|\| 0\)", js)]
    assert len(strays) == 1, (
        f"{len(strays)} places sort on the biggest patch. There should be exactly one -- the "
        f"tiebreak inside byStanding() -- and anything else is a second ranking rule.")
    inside = js.index("function byStanding(")
    end = js.index("function ranked(", inside)
    assert inside < strays[0] < end, (
        "the biggest-patch comparison is not the one inside byStanding(), so some other "
        "function is ranking crews its own way")


def test_the_two_halves_agree_on_what_a_square_is_worth():
    """The client converts a score gap into squares with a constant of its own, because the
    panel has no access to the server's. They have to be the same number."""
    js = CREWS_JS.read_text(encoding="utf-8")
    py = CREWS_PY.read_text(encoding="utf-8")
    m = re.search(r"var SCORE_PER_SQUARE = ([\d.]+);", js)
    assert m, "crews.js no longer declares SCORE_PER_SQUARE"
    assert float(m.group(1)) == crews.SCORE_PER_CONNECTED_SQUARE, (
        f"crews.js says a square is worth {m.group(1)} and services/crews.py says "
        f"{crews.SCORE_PER_CONNECTED_SQUARE}; the card's 'N squares off 7th' is wrong by the "
        f"ratio between them")
    assert "SCORE_PER_CONNECTED_SQUARE = SCORE_W_PATCH + SCORE_W_SQUARES" in py, (
        "the server's constant is no longer derived from the weights, so changing a weight "
        "would leave it stale")


def test_area_is_not_a_term_in_the_standing(db):
    """km² informs and does not rank.

    It was a third term at 0.05 per km², and on the live board it put Harbour Bridge Bombers
    (13 squares, 54 km²) above Harbour Loop (14 squares, 27 km²) -- a crew ranked over one
    that had ridden MORE ground, on nothing but the latitude of the ground. A reviewer derived
    the weights unprompted and called the board rigged, and that pair is what they meant.

    Two crews identical in squares and shape and wildly different in area must now score the
    same, and tie-break on area rather than rank on it.
    """
    _settings(db)
    a = _crew(db, "north", "Far North")
    b = _crew(db, "south", "Far South")
    for row, km2 in ((db.get(models.Clan, a.clan_id), 20.0),
                     (db.get(models.Clan, b.clan_id), 200.0)):
        row.terr_best_tiles = row.terr_tiles = 12
        row.terr_km2 = row.terr_best_km2 = km2
    db.commit()
    north, south = db.get(models.Clan, a.clan_id), db.get(models.Clan, b.clan_id)
    assert crews.standing_score(north) == crews.standing_score(south), (
        f"same squares, ten times the area, different scores "
        f"({crews.standing_score(north)} vs {crews.standing_score(south)}) -- area is back in "
        f"the standing")
    assert crews.standing_score(north) == 12 + 0.25 * 12
