"""Ground next to ground you already hold is harder to take off you.

Erwin asked whether a crew should get "a little edge" on squares near its own, because five
riders could take a square off five riders defending it exactly as hard. They could: two crews
both at the weekly cap differ only by who rode most recently, worth about 3.4% a day, and the
incumbent's sole advantage was an exact-tie break that never fires -- real rides carry real
timestamps and do not tie.

So a crew's claim is weighed up by 25% x (neighbours it already holds / 4), read off the
previous rebuild's map. Four tests, because the rule has four ways to be wrong:

  * it must actually decide a contest it would otherwise lose;
  * it must NOT apply where the crew holds nothing around the square, or the frontier stops
    being contestable and the map freezes;
  * it must not be stored, because what a crew is told it holds has to stay the number it
    rode, not the number it rode plus a bonus;
  * it must not lift a claim over the floor, because ground next door is not a substitute for
    having ridden here.
"""
import pytest

from services import territory


Z = 14
BASE_X, BASE_Y = 8600, 4300


def _t(dx, dy):
    return "%d/%d/%d" % (Z, BASE_X + dx, BASE_Y + dy)


HOME = _t(0, 0)


def _acc(defender_km, attacker_km):
    """One contested square, two crews, a rider each."""
    return {HOME: {"def": [defender_km, {"d1"}], "atk": [attacker_km, {"a1"}]}}


def _prev(neighbours_held):
    """The last map: the defender holds the square and `neighbours_held` of the four around
    it. The attacker holds nothing."""
    out = {HOME: "def"}
    for i, (dx, dy) in enumerate(((1, 0), (-1, 0), (0, 1), (0, -1))):
        if i < neighbours_held:
            out[_t(dx, dy)] = "def"
    return out


def _holder(defender_km, attacker_km, neighbours_held):
    won = territory.winners(_acc(defender_km, attacker_km), _prev(neighbours_held))
    return won.get(HOME, (None,))[0]


def test_an_isolated_square_is_decided_on_kilometres_alone(db=None):
    """No neighbours, no edge: the frontier has to stay winnable or the map stops moving."""
    assert _holder(100.0, 101.0, 0) == "atk", (
        "a crew with more kilometres lost an isolated square, so the bonus is being applied "
        "where the crew holds nothing around it")
    assert _holder(100.0, 99.0, 0) == "def"


def test_four_neighbours_hold_a_square_the_defender_would_have_lost():
    """The case Erwin described: equal effort, and the dug-in crew keeps it."""
    # 20% ahead is not enough against +25%
    assert _holder(100.0, 120.0, 4) == "def", (
        "the attacker took a fully surrounded square while only 20% ahead; the defender's "
        "edge is not being applied")
    # 30% ahead is
    assert _holder(100.0, 130.0, 4) == "atk", (
        "the edge is stronger than 25%, so a surrounded square cannot be taken at all")


def test_the_edge_scales_with_how_much_is_held_around_it():
    """Two neighbours is half the bonus of four, so the patch has a soft rim and a hard core."""
    # +12.5% at two neighbours: 115 beats it, 110 does not
    assert _holder(100.0, 110.0, 2) == "def"
    assert _holder(100.0, 115.0, 2) == "atk"
    # the same 115 would NOT be enough against all four
    assert _holder(100.0, 115.0, 4) == "def", (
        "a square with four neighbours held is no harder to take than one with two")


def test_the_stored_kilometres_are_the_ones_actually_ridden():
    """The bonus decides the contest and is not banked. A crew told it holds 125 km when it
    rode 100 would see its own pressure band and its own km2 lie to it."""
    won = territory.winners(_acc(100.0, 10.0), _prev(4))
    clan, km, riders = won[HOME]
    assert clan == "def"
    assert km == pytest.approx(100.0), (
        f"the square stored {km} km against 100 ridden, so the neighbour bonus has been "
        f"written into the crew's own totals")


def test_the_edge_cannot_lift_a_claim_over_the_floor():
    """Riding next door is not riding here. A claim under the tile's floor takes nothing,
    however much of the neighbourhood the crew holds."""
    floor = territory.min_lead_km(HOME)
    # just under the floor, with every neighbour held and nobody contesting it
    acc = {HOME: {"def": [floor * 0.9, {"d1"}]}}
    won = territory.winners(acc, _prev(4))
    # it may still be held through the incumbent fallback -- pinned AT the floor, not above it
    if HOME in won:
        clan, km, _ = won[HOME]
        assert clan == "def"
        assert km == pytest.approx(floor), (
            f"a sub-floor claim was awarded at {km} rather than pinned to the floor "
            f"{floor:.3f}; the neighbour bonus is being counted as riding")


def test_a_crew_gets_nothing_for_a_neighbour_somebody_else_holds():
    """The count is of the crew's OWN neighbours, not of occupied ones."""
    prev = {HOME: "def", _t(1, 0): "atk", _t(-1, 0): "atk",
            _t(0, 1): "atk", _t(0, -1): "atk"}
    won = territory.winners(_acc(100.0, 101.0), prev)
    assert won.get(HOME, (None,))[0] == "atk", (
        "the defender was given an edge for squares its rival holds")
