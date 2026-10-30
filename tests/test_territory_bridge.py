"""The square that joins two patches at a corner.

Erwin, with the live map open on two bits of Jonah's turf that meet at a point: "those 2
areas arent connected because the road only touched a part of the square". The grid is not
the problem and hexagons would not have fixed it -- the gap is ground nobody claimed. The
road went through it, and one ride through a square, after the half-life, is worth about a
fifth of what a claim costs.

So the rule hands that square over, and these are its edges. Everything here is about what it
must REFUSE to take: ground nobody rode, ground a rival holds, ground a rival rode harder,
and ground that joins nothing.
"""
from services import territory as TR

Z = 14


def tile(x, y):
    return f"{Z}/{x}/{y}"


def block(x0, y0, n=2):
    return {(x0 + dx, y0 + dy) for dx in range(n) for dy in range(n)}


def rode(acc, xy, clan, part, riders=1):
    """Put `part` of a lead's worth of riding in a square. 1.0 is exactly the lead floor.

    Scaled to the tile rather than written as kilometres, because every floor in this file is
    a fraction of a tile's own edge and a flat number would mean something different at every
    latitude -- which is the bias both floors were rewritten to remove.
    """
    t = tile(*xy)
    acc.setdefault(t, {})[clan] = [TR.min_lead_km(t) * part, {f"r{n}" for n in range(riders)}]
    return acc


# Two 2x2 blocks that meet at one point: (1,1) and (2,2) are diagonal neighbours, so the
# squares at (2,1) and (1,2) are the two candidates.
WEST, EAST = block(0, 0), block(2, 2)
BOTH = WEST | EAST


def test_a_square_the_ride_went_through_joins_two_patches():
    acc = rode({}, (2, 1), "a", 0.5)            # a visit, well under a claim
    out = TR._bridge({"a": set(BOTH)}, acc, Z)
    assert out["a"] == BOTH | {(2, 1)}
    assert len(TR.regions(out["a"])) == 1, "the two patches are one patch now"


def test_a_corner_nobody_rode_stays_two_patches():
    """No evidence of a route, no square. This is the case hexagons would not help either:
    an unclaimed hex between two patches splits them exactly as an unclaimed square does."""
    out = TR._bridge({"a": set(BOTH)}, {}, Z)
    assert out["a"] == BOTH
    assert len(TR.regions(out["a"])) == 2


def test_a_square_only_clipped_is_not_a_visit():
    """Below `min_visit_km` a ride never reaches `acc` at all -- see the `continue` in
    accumulate(). The bridge rule asks `acc` and therefore inherits that floor, which is why
    it needs no threshold of its own. A square missing from `acc` is a square nobody rode."""
    acc = {}
    assert tile(2, 1) not in acc
    assert TR._bridge({"a": set(BOTH)}, acc, Z)["a"] == BOTH


def test_a_rivals_square_is_a_wall():
    acc = rode(rode({}, (2, 1), "a", 0.5), (1, 2), "a", 0.5)
    out = TR._bridge({"a": set(BOTH), "b": {(2, 1), (1, 2)}}, acc, Z)
    assert out["a"] == BOTH, "a crew does not ride through somebody else's ground"
    assert len(TR.regions(out["a"])) == 2


def test_a_rival_in_the_gap_closes_the_whole_corner():
    """One rival square at a corner shuts both of its squares, not just the one it sits on.

    This used to go the other way: a rival on one of the two squares left the other open, and
    a visit to it was enough. Erwin's counting rule is what changed it -- "if square 2 is
    taken by crew B... means crew A could actually take it, so we dont connect them by the
    corner in this case" -- and a corner has to answer the same way to both rules or the
    cheaper one speaks for it. A rival there is proof the gap is rideable, so the way through
    is to ride for a square, not to visit the one beside it.
    """
    acc = rode({}, (1, 2), "a", 0.5)
    out = TR._bridge({"a": set(BOTH), "b": {(2, 1)}}, acc, Z)
    assert out["a"] == BOTH, "the free square at a contested corner is not a bridge any more"
    assert len(TR.regions(out["a"])) == 2
    assert len(TR.regions(out["a"], {(2, 1)})) == 2, "and the counting rule agrees with it"


def test_the_square_goes_to_whoever_rode_it_most():
    """Unowned is not unridden. A square another crew rode harder is theirs to claim when
    they ride a little further, and taking it from under them on a technicality of shape
    would be the spoiling move this file already refuses elsewhere."""
    acc = rode({}, (2, 1), "a", 0.5)
    rode(acc, (2, 1), "b", 0.9)
    assert TR._bridge({"a": set(BOTH)}, acc, Z)["a"] == BOTH
    # and the moment the other crew is behind, it is a bridge again
    rode(acc, (2, 1), "b", 0.2)
    assert TR._bridge({"a": set(BOTH)}, acc, Z)["a"] == BOTH | {(2, 1)}


def test_where_both_squares_qualify_only_the_better_ridden_one_is_taken():
    acc = rode(rode({}, (2, 1), "a", 0.3), (1, 2), "a", 0.7)
    out = TR._bridge({"a": set(BOTH)}, acc, Z)
    assert out["a"] == BOTH | {(1, 2)}, "one corner hands over one square, the ridden one"


def test_the_same_data_draws_the_same_map():
    """Two squares ridden exactly alike still has to settle somewhere, and settle there every
    time: a map that changes between two rebuilds of identical data is a bug a rider would
    see as their square flickering."""
    acc = rode(rode({}, (2, 1), "a", 0.5), (1, 2), "a", 0.5)
    first = TR._bridge({"a": set(BOTH)}, acc, Z)["a"]
    assert len(first) == len(BOTH) + 1
    for _ in range(5):
        assert TR._bridge({"a": set(BOTH)}, acc, Z)["a"] == first


def test_riding_beside_your_ground_is_not_a_bridge():
    """The rule gives you a square because it JOINS something, not because you rode there.
    Without this it would be a second, cheaper way to take ground: half a claim and a square
    anywhere along your frontier."""
    acc = rode({}, (2, 0), "a", 0.5)            # touching the west block, joining nothing
    assert TR._bridge({"a": block(0, 0)}, acc, Z)["a"] == block(0, 0)


def test_a_square_between_two_crews_patches_is_not_a_bridge():
    """Your corner, your bridge. Two crews whose ground meets at a point are two crews."""
    acc = rode({}, (2, 1), "a", 0.5)
    out = TR._bridge({"a": set(WEST), "b": set(EAST)}, acc, Z)
    assert out["a"] == WEST and out["b"] == EAST


def test_two_crews_cannot_both_take_the_same_square():
    """One square, one owner. Both crews rode it exactly alike, so the pass is ordered by
    crew id and the same one takes it every rebuild -- the alternative is a square that
    changes colour depending on how a dictionary was built."""
    # One square with four corners of its own: `a` reaches it from the south-west pair,
    # `b` from the north-east one, and both rode it exactly as far.
    acc = rode({}, (5, 5), "a", 0.5)
    rode(acc, (5, 5), "b", 0.5)
    kept = {"b": {(5, 6), (6, 5)}, "a": {(5, 4), (4, 5)}}
    out = TR._bridge(kept, acc, Z)
    assert sum(1 for pts in out.values() if (5, 5) in pts) == 1
    assert (5, 5) in out["a"], "sorted by crew id, so a and not b, and so again next hour"


def test_a_notch_in_your_own_patch_is_not_a_bridge():
    """The square has to join two DIFFERENT patches. An L of your own ground has a diagonal
    pair inside it like any other shape, and if that counted, the rule would quietly become
    "fill in every notch you have ridden past" -- ground for half the price of a claim."""
    ell = {(0, 0), (1, 0), (1, 1)}               # (0,0) and (1,1) are diagonal, one patch
    acc = rode({}, (0, 1), "a", 0.5)
    assert TR._bridge({"a": set(ell)}, acc, Z)["a"] == ell


# Hill Country Hum as it stands on the local world, moved to the origin. Nine squares and
# six, meeting at three separate points, with the riding that is really in the four squares
# around those points. The whole rule is visible in one shape.
#
#   . . #        the 9 is (2,0) down through (2,3) and out to (3,4)
#   # # #        the 6 is (0,3) up to (0,5) and across to (2,5)
#   . # #        (0,2) and (2,4) are ridden and unowned
#   # . # #      (1,3) is ridden less, and is the other candidate at the first corner
#   # # . #
#   # # #
HILL_BIG = {(2, 0), (2, 1), (1, 1), (0, 1), (1, 2), (2, 2), (2, 3), (3, 3), (3, 4)}
HILL_SMALL = {(0, 3), (0, 4), (0, 5), (1, 4), (1, 5), (2, 5)}


def hill_acc():
    acc = {}
    rode(acc, (0, 2), "a", 0.46)                 # 0.194 of an edge, as measured
    rode(acc, (2, 4), "a", 0.45)                 # 0.189
    rode(acc, (1, 3), "a", 0.32)                 # 0.134, the loser at the first corner
    return acc


def test_the_shape_this_rule_was_written_for():
    """Three corners, two squares, and the third candidate arriving by the older rule.

    At the first corner both squares qualify and the better-ridden one takes it. The other,
    (1, 3), is then surrounded by the result, so `_close_holes` hands it over anyway -- which
    is why the bridge runs first and why this ends at 18 squares rather than 17.
    """
    held = HILL_BIG | HILL_SMALL
    assert sorted(len(p) for p in TR.regions(held)) == [6, 9]

    joined = TR._bridge({"a": held}, hill_acc(), Z)["a"]
    assert joined == held | {(0, 2), (2, 4)}, "both corners, and the ridden square at each"
    assert (1, 3) not in joined, "the lesser-ridden square at a corner already bridged"
    assert len(TR.regions(joined)) == 1

    closed = TR._close_holes({"a": joined})["a"]
    assert closed == joined | {(1, 3)}
    assert len(closed) == 18


def test_a_bridge_can_expose_a_corner_that_was_not_there_before():
    """Why the pass repeats. A third patch can touch the new square at a point, and that
    corner did not exist while the square was unowned, so one pass would leave it."""
    acc = rode(rode({}, (2, 1), "a", 0.5), (3, 1), "a", 0.5)
    far = {(3, 0), (4, 0), (3, -1), (4, -1)}     # touches nothing until (2,1) is taken
    assert len(TR.regions(BOTH | far)) == 3
    out = TR._bridge({"a": BOTH | far}, acc, Z)
    assert {(2, 1), (3, 1)} <= out["a"]
    assert len(TR.regions(out["a"])) == 1, "all three patches, in two passes"


def test_a_bridge_that_closes_a_ring_hands_over_the_hole_too():
    """Order of the two rules, and the reason the bridge runs first. A loop with a blank
    square in the middle of it is the rendering fault `_close_holes` exists to stop, and a
    bridge can be the square that completes the loop."""
    ring = {(1, 0), (2, 0), (0, 1), (0, 2), (1, 3), (2, 3), (3, 1), (3, 2),
            (1, 1), (2, 2)}                      # open at (2,1), which pinches to a corner
    acc = rode({}, (2, 1), "a", 0.5)
    joined = TR._bridge({"a": ring}, acc, Z)["a"]
    assert (2, 1) in joined
    closed = TR._close_holes({"a": joined})["a"]
    assert (1, 2) in closed, "the square the completed loop now encloses"


def test_the_whole_pipeline_turns_two_patches_into_one():
    """Through award(), where it actually runs: the lead contest, seeding, the bridge and the
    hole rule in the order a rebuild uses them."""
    acc = {}
    for xy in BOTH:
        rode(acc, xy, "a", 1.4)                  # comfortably a claim
    rode(acc, (2, 1), "a", 0.5)                  # and a visit in the square between
    kept, _won, _blocked = TR.award(acc, {"a"}, {})
    assert kept["a"] == BOTH | {(2, 1)}
    assert len(TR.regions(kept["a"])) == 1


def test_award_runs_the_bridge_before_the_hole_rule():
    """The order lives in award(), not in whoever calls it, so it is pinned there too. Run
    the other way round, the hole is still a hole when `_close_holes` looks at it and the
    crew ends on 17 squares with a blank one in the middle of its ground."""
    acc = {}
    for xy in HILL_BIG | HILL_SMALL:
        rode(acc, xy, "a", 1.4)
    for xy, part in (((0, 2), 0.46), ((2, 4), 0.45), ((1, 3), 0.32)):
        rode(acc, xy, "a", part)
    kept, _won, _blocked = TR.award(acc, {"a"}, {})
    assert len(kept["a"]) == 18
    assert (1, 3) in kept["a"], "the square the bridges enclosed"
    assert len(TR.regions(kept["a"])) == 1


def test_a_crew_that_draws_nothing_gains_nothing():
    """The bridge runs after seeding and cannot rescue a crew from it. Two single squares on
    a diagonal are not a 2x2 and never become one."""
    acc = {}
    for xy in ((0, 0), (1, 1)):
        rode(acc, xy, "a", 1.4)
    rode(acc, (1, 0), "a", 0.5)
    kept, _won, _blocked = TR.award(acc, {"a"}, {})
    assert kept.get("a", set()) == set()


def test_a_bridge_square_reports_the_riding_behind_it():
    """A square gained by enclosure has no kilometres because nobody rode it. A bridge does,
    and the cell write has to say so: the popup and the pressure band both read that number
    back to the rider, and 0 there would describe a square nobody has been to."""
    acc = {}
    for xy in BOTH:
        rode(acc, xy, "a", 1.4)
    rode(acc, (2, 1), "a", 0.5)
    kept, won, _ = TR.award(acc, {"a"}, {})
    assert tile(2, 1) not in won, "it was never led, which is the whole point"
    e = acc[tile(2, 1)]["a"]
    assert e[0] > 0 and len(e[1]) == 1
def test_the_map_quotes_what_a_rival_actually_needs_on_a_bridge_square():
    """A bridge is the cheapest ground on the map, and the map has to say so correctly.

    It is held with less in it than a claim costs, which no other square is: everything else
    reaching this band was pinned AT the floor by `winners`. Quoting what the holder rode
    would advertise a square as a quarter of a kilometre when it takes half of one, on the
    one number a rider is meant to act on.
    """
    t = tile(2, 1)
    floor = TR.min_lead_km(t)
    held = floor * 0.5                           # a visit, which is what a bridge is held on
    band, need, pushing = TR._pressure({t: {"a": [held, {"r0"}]}}, t, "a", held)
    assert band == 3 and pushing is None
    assert need == round(floor * 10), f"quoted {need / 10:.2f} km for a square needing {floor:.2f}"

    # and a square pinned at the floor, which is every other square in this band, is unmoved
    band2, need2, _ = TR._pressure({t: {"a": [floor, {"r0"}]}}, t, "a", floor)
    assert (band2, need2) == (3, round(floor * 10))
